from __future__ import annotations

import json
import os
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contracts import CompiledShot, ContractError
from .jsonio import atomic_write_json, sha256_file, sha256_json

DEFAULT_REGION = "us-central1"
_GCS_URI = re.compile(r"^gs://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/[^?#]+$")
_REDACTED_ERROR_LIMIT = 1_000
_DIAGNOSTIC_BODY_LIMIT = 64_000
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|token|secret|password|credential|cookie|api[-_]?key)", re.IGNORECASE
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SAFE_RESPONSE_HEADERS = {
    "content-type",
    "date",
    "server",
    "x-cloud-trace-context",
    "x-goog-request-id",
    "x-request-id",
}


@dataclass(frozen=True)
class ProviderRequestContext:
    phase: str
    job_id: str | None = None
    shot_id: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True)
class ProviderDiagnostic:
    diagnostic_id: str
    path: Path
    http_status: int | None
    provider_status: str | None
    provider_error_code: str | None
    provider_message: str | None


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        http_status: int | None = None,
        provider_status: str | None = None,
        provider_error_code: str | None = None,
        diagnostic_id: str | None = None,
        remedy: str | None = None,
        retry_classification: str | None = None,
        retry_after_seconds: float | None = None,
        request_not_sent: bool = False,
    ) -> None:
        super().__init__(message[:_REDACTED_ERROR_LIMIT])
        self.code = code
        self.retryable = retryable
        self.http_status = http_status
        self.provider_status = provider_status
        self.provider_error_code = provider_error_code
        self.diagnostic_id = diagnostic_id
        self.remedy = remedy or provider_remedy(code)
        self.retry_classification = retry_classification or ("safe-read" if retryable else "operator-action")
        self.retry_after_seconds = retry_after_seconds
        self.request_not_sent = request_not_sent


def provider_remedy(code: str) -> str:
    return {
        "provider_authentication_required": "Run tools/reauth-gcp.ps1 -Mode Reauth, then resume this recorded run.",
        "provider_access_denied": "Verify the bound identity's Vertex and storage permissions. A persistent 403 requires targeted IAM correction, not repeated login.",
        "api_disabled": "Have the project administrator enable the Vertex AI API for the bound project; then resume.",
        "provider_billing_disabled": "Ask the billing administrator to restore billing on the bound project; then resume.",
        "provider_quota_exhausted": "Review the project quota; resume safe reads later. Do not raise quotas automatically.",
        "provider_rate_limited": "Wait for the bounded safe-read retry or resume the retained operation later.",
        "provider_model_unavailable": "Verify this exact model and region are available to the bound project before compiling a new plan.",
        "provider_request_failed": "Inspect the private diagnostic and correct the model request before authorizing a new plan.",
        "provider_network_error": "Restore network access, then resume polling or transfer of this recorded attempt.",
    }.get(code, "Inspect this attempt's retained evidence and resume its earliest unverified stage.")


def safe_read_delay(failures: int, *, base_seconds: float = 1.0, retry_after: float | None = None) -> float:
    ceiling = min(60.0, base_seconds * 2 ** max(0, failures - 1))
    return min(60.0, max(retry_after or 0.0, random.uniform(ceiling / 2, ceiling)))


def _retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers else None
    if value is None:
        return None
    try:
        return min(60.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        try:
            return min(60.0, max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None


@dataclass(frozen=True)
class DoctorResult:
    ok: bool
    network_contacted: bool
    checks: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "networkContacted": self.network_contacted,
            "checks": list(self.checks),
        }


def active_access_token(*, project_id: str | None = None) -> str:
    from .gcp_auth import GcpAuthError, access_token
    try:
        return access_token(project_id=project_id)
    except GcpAuthError as exc:
        raise ProviderError(str(exc), code=exc.code, retryable=exc.retryable,
                            remedy=exc.remedy, request_not_sent=True) from exc


def doctor(*, project_id: str, bucket: str, region: str = DEFAULT_REGION) -> DoctorResult:
    from .gcp_auth import check_access
    value = check_access(mode="cloud", project_id=project_id, bucket=bucket, region=region)
    return DoctorResult(ok=bool(value["ok"]), network_contacted=bool(value["networkContacted"]),
                        checks=tuple(value["checks"]))


def build_request_payload(shot: CompiledShot) -> dict[str, Any]:
    if shot.model_id not in {"veo-3.1-generate-001", "veo-3.1-fast-generate-001"}:
        raise ContractError("unsupported model; explicit capability qualification is required")
    if shot.resolution not in {"720p", "1080p", "4k"}:
        raise ContractError("unsupported provider resolution")
    if shot.continuation_mode not in {"prompt-anchors", "first-frame-reference", "accepted-previous-shot-end-frame", "local-composition"}:
        raise ContractError("unsupported image mode; only the qualified first-frame image payload is supported")
    if not shot.storage_uri:
        raise ContractError(f"{shot.shot_id}: a GCS storageUri is required for resumable generation")
    if shot.continuation_mode == "local-composition":
        raise ContractError("local-composition shots cannot be sent to the provider")
    if shot.duration_seconds not in {4, 6, 8} or shot.sample_count != 1:
        raise ContractError("this verified video lane requires 4/6/8 seconds and one sample per attempt")
    if shot.aspect_ratio not in {"16:9", "9:16"} or shot.generate_audio:
        raise ContractError("this lane requires supported aspect ratio and visual-only generation")
    if shot.resolution == "4k" and shot.model_id in {
        "veo-3.1-generate-001",
        "veo-3.1-fast-generate-001",
    }:
        raise ContractError(
            f"{shot.shot_id}: {shot.model_id} supports 720p/1080p, not 4k; compile a fresh 1080p plan"
        )
    instance: dict[str, Any] = {"prompt": shot.prompt}
    if shot.first_frame_reference is not None:
        if (
            not _GCS_URI.fullmatch(shot.first_frame_reference.gcs_uri)
            or shot.first_frame_reference.mime_type not in {"image/jpeg", "image/png"}
            or not re.fullmatch(r"[0-9a-f]{64}", shot.first_frame_reference.sha256)
        ):
            raise ContractError(f"{shot.shot_id}: first-frame reference contract is invalid")
        instance["image"] = {
            "gcsUri": shot.first_frame_reference.gcs_uri,
            "mimeType": shot.first_frame_reference.mime_type,
        }
    return {
        "instances": [instance],
        "parameters": {
            "storageUri": shot.storage_uri,
            "sampleCount": shot.sample_count,
            "durationSeconds": shot.duration_seconds,
            "seed": shot.seed,
            "aspectRatio": shot.aspect_ratio,
            "resolution": shot.resolution,
            "personGeneration": shot.person_generation,
            "negativePrompt": shot.negative_prompt,
            "enhancePrompt": shot.enhance_prompt,
            "generateAudio": shot.generate_audio,
            "compressionQuality": shot.compression_quality,
        },
    }


def _redact_text(value: str) -> str:
    return _BEARER_TOKEN.sub("Bearer [REDACTED]", value)[:_DIAGNOSTIC_BODY_LIMIT]


def _redact_value(value: Any, *, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _provider_error_fields(body: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(body, dict):
        return None, None, None
    error = body.get("error")
    if not isinstance(error, dict):
        return None, None, None
    status = error.get("status")
    code = error.get("code")
    message = error.get("message")
    return (
        str(status)[:160] if status is not None else None,
        str(code)[:160] if code is not None else None,
        _redact_text(str(message))[:_REDACTED_ERROR_LIMIT] if message is not None else None,
    )


def _request_summary(payload: dict[str, Any]) -> dict[str, Any]:
    instances = payload.get("instances")
    first = (
        instances[0] if isinstance(instances, list) and instances and isinstance(instances[0], dict) else {}
    )
    parameters = payload.get("parameters")
    parameter_object = parameters if isinstance(parameters, dict) else {}
    prompt = first.get("prompt") if isinstance(first.get("prompt"), str) else ""
    storage_uri = parameter_object.get("storageUri")
    return {
        "topLevelKeys": sorted(payload),
        "instanceKeys": sorted(first),
        "parameterKeys": sorted(parameter_object),
        "promptSha256": sha256_json(prompt),
        "storageUriSha256": sha256_json(storage_uri) if isinstance(storage_uri, str) else None,
    }


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _safe_headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    return {
        str(key).lower(): _redact_text(str(value))
        for key, value in headers.items()
        if str(key).lower() in _SAFE_RESPONSE_HEADERS
    }


def save_operation_failure_diagnostic(
    diagnostics_root: Path,
    *,
    context: ProviderRequestContext,
    response: dict[str, Any],
) -> ProviderDiagnostic:
    diagnostic_id = f"veo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:10]}"
    redacted = _redact_value(response)
    error_value = redacted.get("error") if isinstance(redacted, dict) else None
    provider_status, provider_error_code, provider_message = _provider_error_fields(
        {"error": error_value} if isinstance(error_value, dict) else {}
    )
    path = diagnostics_root.resolve() / f"{diagnostic_id}.json"
    atomic_write_json(
        path,
        {
            "schemaVersion": "1.0.0",
            "diagnosticId": diagnostic_id,
            "capturedAt": datetime.now(UTC).isoformat(),
            "phase": context.phase,
            "jobId": context.job_id,
            "shotId": context.shot_id,
            "attemptId": context.attempt_id,
            "request": None,
            "response": {
                "httpStatus": 200,
                "providerStatus": provider_status,
                "providerErrorCode": provider_error_code,
                "providerMessage": provider_message,
                "headers": {},
                "bodyFormat": "json",
                "body": redacted,
            },
            "transportError": None,
        },
    )
    return ProviderDiagnostic(
        diagnostic_id=diagnostic_id,
        path=path,
        http_status=200,
        provider_status=provider_status,
        provider_error_code=provider_error_code,
        provider_message=provider_message,
    )


class VeoRestClient:
    def __init__(
        self,
        *,
        project_id: str,
        region: str = DEFAULT_REGION,
        token_provider: Callable[[], str] | None = None,
        timeout_seconds: int = 120,
        diagnostics_root: Path | None = None,
    ) -> None:
        if not project_id.strip():
            raise ValueError("project_id must not be empty")
        if region != DEFAULT_REGION:
            raise ValueError(f"This starter supports {DEFAULT_REGION}; got {region!r}")
        self.project_id = project_id
        self.region = region
        self.token_provider = token_provider or (lambda: active_access_token(project_id=self.project_id))
        self.timeout_seconds = timeout_seconds
        self.diagnostics_root = diagnostics_root.resolve() if diagnostics_root is not None else None

    def _model_url(self, model_id: str, method: str) -> str:
        return (
            f"https://{self.region}-aiplatform.googleapis.com/v1/projects/"
            f"{self.project_id}/locations/{self.region}/publishers/google/"
            f"models/{model_id}:{method}"
        )

    def _write_diagnostic(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        context: ProviderRequestContext,
        http_status: int | None,
        response_headers: Any,
        raw_body: str,
        transport_error: str | None = None,
    ) -> ProviderDiagnostic | None:
        if self.diagnostics_root is None:
            return None
        diagnostic_id = f"veo-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:10]}"
        parsed_body: Any
        body_format: str
        try:
            parsed_body = json.loads(raw_body) if raw_body else None
            body_format = "json"
        except json.JSONDecodeError:
            parsed_body = _redact_text(raw_body)
            body_format = "text"
        redacted_body = _redact_value(parsed_body)
        provider_status, provider_error_code, provider_message = _provider_error_fields(redacted_body)
        document = {
            "schemaVersion": "1.0.0",
            "diagnosticId": diagnostic_id,
            "capturedAt": datetime.now(UTC).isoformat(),
            "phase": context.phase,
            "jobId": context.job_id,
            "shotId": context.shot_id,
            "attemptId": context.attempt_id,
            "request": {
                "method": "POST",
                "url": _safe_url(url),
                "summary": _request_summary(payload),
                "authorization": "[REDACTED]",
            },
            "response": {
                "httpStatus": http_status,
                "providerStatus": provider_status,
                "providerErrorCode": provider_error_code,
                "providerMessage": provider_message,
                "headers": _safe_headers(response_headers),
                "bodyFormat": body_format,
                "body": redacted_body,
            },
            "transportError": _redact_text(transport_error) if transport_error else None,
        }
        path = self.diagnostics_root / f"{diagnostic_id}.json"
        atomic_write_json(path, document)
        return ProviderDiagnostic(
            diagnostic_id=diagnostic_id,
            path=path,
            http_status=http_status,
            provider_status=provider_status,
            provider_error_code=provider_error_code,
            provider_message=provider_message,
        )

    def _post(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        context: ProviderRequestContext,
    ) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token_provider()}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_bytes = response.read(2_000_001)
                if len(raw_bytes) > 2_000_000:
                    raise ProviderError("Provider response exceeded its safe size bound", code="provider_response_invalid")
                raw = raw_bytes.decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read(_DIAGNOSTIC_BODY_LIMIT).decode("utf-8", errors="replace")
            diagnostic = self._write_diagnostic(
                url=url,
                payload=payload,
                context=context,
                http_status=exc.code,
                response_headers=exc.headers,
                raw_body=body,
            )
            lowered = body.lower()
            if exc.code == 401:
                code = "provider_authentication_required"
            elif exc.code == 403 and "billing" in lowered:
                code = "provider_billing_disabled"
            elif exc.code == 403 and any(value in lowered for value in ("serviceusage", "service_disabled", "api has not been used")):
                code = "api_disabled"
            elif exc.code == 403:
                code = "provider_access_denied"
            elif "quota" in lowered or "resource_exhausted" in lowered:
                code = "provider_quota_exhausted"
            elif exc.code == 429:
                code = "provider_rate_limited"
            elif exc.code == 404:
                code = "provider_model_unavailable"
            elif exc.code >= 500:
                code = "provider_transient"
            else:
                code = "provider_request_failed"
            provider_status = diagnostic.provider_status if diagnostic else None
            provider_error_code = diagnostic.provider_error_code if diagnostic else None
            provider_label = provider_status or provider_error_code
            # Raw provider messages can contain prompts, resource names or URIs.
            # They remain only in the private diagnostic, never public errors.
            detail = f" {provider_label}." if provider_label else ""
            diagnostic_label = f" Diagnostic ID {diagnostic.diagnostic_id}." if diagnostic is not None else ""
            raise ProviderError(
                f"Google Veo returned HTTP {exc.code} ({code}).{detail}{diagnostic_label}",
                code=code,
                retryable=exc.code == 429 or exc.code >= 500,
                http_status=exc.code,
                provider_status=provider_status,
                provider_error_code=provider_error_code,
                diagnostic_id=diagnostic.diagnostic_id if diagnostic else None,
                retry_after_seconds=_retry_after(exc.headers),
            ) from exc
        except urllib.error.URLError as exc:
            diagnostic = self._write_diagnostic(
                url=url,
                payload=payload,
                context=context,
                http_status=None,
                response_headers=None,
                raw_body="",
                transport_error=str(exc.reason),
            )
            raise ProviderError(
                "Google API network request failed."
                + (f" Diagnostic ID {diagnostic.diagnostic_id}." if diagnostic else ""),
                code="provider_network_error",
                retryable=True,
                diagnostic_id=diagnostic.diagnostic_id if diagnostic else None,
            ) from exc
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("Google API returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError("Google API response root was not an object")
        return value

    def submit(
        self,
        shot: CompiledShot,
        *,
        context: ProviderRequestContext | None = None,
    ) -> dict[str, Any]:
        response = self._post(
            self._model_url(shot.model_id, "predictLongRunning"),
            build_request_payload(shot),
            context=context or ProviderRequestContext(phase="submit", shot_id=shot.shot_id),
        )
        if not isinstance(response.get("name"), str):
            raise ProviderError("Generation response did not contain a long-running operation name")
        return response

    def fetch(
        self,
        *,
        model_id: str,
        operation_name: str,
        context: ProviderRequestContext | None = None,
    ) -> dict[str, Any]:
        return self._post(
            self._model_url(model_id, "fetchPredictOperation"),
            {"operationName": operation_name},
            context=context or ProviderRequestContext(phase="poll"),
        )


def validate_gcs_uri(uri: str, *, required_prefix: str | None = None) -> str:
    if not _GCS_URI.fullmatch(uri) or ".." in uri.split("/", 3)[-1].split("/"):
        raise ProviderError("Provider returned an invalid GCS output URI.", code="provider_response_invalid")
    if required_prefix is not None and not uri.startswith(required_prefix):
        raise ProviderError(
            "Provider output URI did not match the authorized storage prefix.",
            code="provider_response_invalid",
        )
    return uri


def response_output_uris(response: dict[str, Any], *, required_prefix: str | None = None) -> tuple[str, ...]:
    operation_response = response.get("response")
    if not isinstance(operation_response, dict):
        return ()
    videos = operation_response.get("videos")
    if not isinstance(videos, list):
        return ()
    values: list[str] = []
    for item in videos:
        if isinstance(item, dict) and isinstance(item.get("gcsUri"), str):
            values.append(validate_gcs_uri(item["gcsUri"], required_prefix=required_prefix))
    return tuple(values)


def copy_gcs_uri(uri: str, destination: Path, *, project_id: str | None = None) -> None:
    validate_gcs_uri(uri)
    if destination.exists():
        raise ProviderError(
            "A local file already exists at the requested clip destination.",
            code="local_clip_conflict",
        )
    from .gcp_auth import GcpAuthError, run_bound_gcloud
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}-{uuid4().hex}.transfer")
    try:
        metadata_result = run_bound_gcloud(["storage", "objects", "describe", uri, "--format=json"],
                                          project_id=project_id, timeout=60)
        metadata = json.loads(metadata_result.stdout)
        generation = str(metadata.get("generation", ""))
        size = int(metadata.get("size", 0))
        if metadata_result.returncode != 0 or not generation.isdecimal() or size <= 0:
            raise ProviderError("GCS object identity could not be verified", code="provider_artifact_invalid")
        result = run_bound_gcloud(["storage", "cp", f"{uri}#{generation}", str(partial)], project_id=project_id, timeout=600)
    except GcpAuthError as exc:
        raise ProviderError(str(exc), code=exc.code, retryable=exc.retryable, remedy=exc.remedy) from exc
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProviderError("GCS object metadata was invalid", code="provider_artifact_invalid") from exc
    if result.returncode != 0 or not partial.is_file() or partial.stat().st_size != size:
        raise ProviderError(
            "The generated clip could not be downloaded from GCS.",
            code="provider_download_failed",
            retryable=True,
        )
    os.replace(partial, destination)
    atomic_write_json(destination.with_name(destination.name + ".gcs-object.json"), {
        "uri": uri, "generation": generation, "size": size, "sha256": sha256_file(destination),
        "crc32c": metadata.get("crc32c"), "md5Hash": metadata.get("md5Hash"),
    })


def upload_reference_image(
    source: Path,
    destination_uri: str,
    *,
    expected_sha256: str,
    project_id: str | None = None,
) -> None:
    validate_gcs_uri(destination_uri)
    if not source.is_file() or sha256_file(source) != expected_sha256:
        raise ProviderError(
            "The approved local reference image is missing or changed; compile a fresh exact plan.",
            code="reference_asset_changed",
        )
    from .gcp_auth import GcpAuthError, run_bound_gcloud
    try:
        result = run_bound_gcloud(
            [
                "storage",
                "cp",
                "--content-type",
                "image/png" if source.suffix.lower() == ".png" else "image/jpeg",
                str(source),
                destination_uri,
            ],
            timeout=600,
            project_id=project_id,
        )
    except GcpAuthError as exc:
        code = "provider_reference_reauthentication_required" if exc.code == "auth_required" else "provider_reference_upload_failed"
        raise ProviderError(str(exc), code=code, retryable=exc.retryable, remedy=exc.remedy) from exc
    if result.returncode != 0:
        detail = (result.stderr or "").lower()
        if "reauthentication failed" in detail or "reauth is required" in detail:
            raise ProviderError(
                "gcloud sign-in requires interactive reauthentication. Run gcloud auth login in your own terminal, then explicitly resume the pre-submission upload failure.",
                code="provider_reference_reauthentication_required",
                retryable=True,
            )
        raise ProviderError(
            "The approved reference image could not be uploaded to GCS.",
            code="provider_reference_upload_failed",
            retryable=True,
        )

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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
    ) -> None:
        super().__init__(message[:_REDACTED_ERROR_LIMIT])
        self.code = code
        self.retryable = retryable
        self.http_status = http_status
        self.provider_status = provider_status
        self.provider_error_code = provider_error_code
        self.diagnostic_id = diagnostic_id


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


def _run(arguments: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def active_access_token() -> str:
    executable = shutil.which("gcloud")
    if not executable:
        raise ProviderError("gcloud CLI is not installed or not on PATH")
    result = _run([executable, "auth", "print-access-token"], timeout=30)
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise ProviderError(
            "gcloud could not provide an access token; run gcloud auth login.",
            code="provider_access_denied",
        )
    return token


def doctor(*, project_id: str, bucket: str, region: str = DEFAULT_REGION) -> DoctorResult:
    checks: list[dict[str, Any]] = []
    executable = shutil.which("gcloud")
    checks.append(
        {
            "id": "gcloud-installed",
            "ok": bool(executable),
            "detail": executable or "not found on PATH",
            "safeDetail": "gcloud CLI is available."
            if executable
            else "gcloud CLI is not available on PATH.",
        }
    )
    if not executable:
        return DoctorResult(ok=False, network_contacted=False, checks=tuple(checks))

    account = _run(
        [
            executable,
            "auth",
            "list",
            "--filter=status:ACTIVE",
            "--format=value(account)",
        ],
        timeout=30,
    )
    checks.append(
        {
            "id": "active-account",
            "ok": account.returncode == 0 and bool(account.stdout.strip()),
            "detail": account.stdout.strip() or account.stderr.strip(),
            "safeDetail": "An active gcloud account is available."
            if account.returncode == 0 and account.stdout.strip()
            else "No active gcloud account is available.",
        }
    )

    token = _run([executable, "auth", "print-access-token"], timeout=30)
    checks.append(
        {
            "id": "access-token",
            "ok": token.returncode == 0 and bool(token.stdout.strip()),
            "detail": "available" if token.stdout.strip() else token.stderr.strip(),
            "safeDetail": "Application credentials are available."
            if token.returncode == 0 and token.stdout.strip()
            else "Application credentials are unavailable.",
        }
    )

    project = _run(
        [executable, "projects", "describe", project_id, "--format=value(projectId)"],
        timeout=45,
    )
    checks.append(
        {
            "id": "project-access",
            "ok": project.returncode == 0 and project.stdout.strip() == project_id,
            "detail": project.stdout.strip() or project.stderr.strip(),
            "safeDetail": "The configured project is accessible."
            if project.returncode == 0 and project.stdout.strip() == project_id
            else "The configured project is not accessible.",
        }
    )

    api = _run(
        [
            executable,
            "services",
            "list",
            "--enabled",
            f"--project={project_id}",
            "--filter=config.name:aiplatform.googleapis.com",
            "--format=value(config.name)",
        ],
        timeout=45,
    )
    checks.append(
        {
            "id": "aiplatform-api-enabled",
            "ok": "aiplatform.googleapis.com" in api.stdout,
            "detail": api.stdout.strip() or api.stderr.strip(),
            "safeDetail": "Vertex AI API is enabled."
            if "aiplatform.googleapis.com" in api.stdout
            else "Vertex AI API is not confirmed enabled.",
        }
    )

    normalized_bucket = bucket.removeprefix("gs://").strip("/")
    bucket_result = _run(
        [
            executable,
            "storage",
            "buckets",
            "describe",
            f"gs://{normalized_bucket}",
            "--format=value(name)",
        ],
        timeout=45,
    )
    checks.append(
        {
            "id": "gcs-bucket-access",
            "ok": bucket_result.returncode == 0,
            "detail": bucket_result.stdout.strip() or bucket_result.stderr.strip(),
            "safeDetail": "The configured GCS bucket is accessible."
            if bucket_result.returncode == 0
            else "The configured GCS bucket is not accessible.",
        }
    )

    checks.append(
        {
            "id": "region",
            "ok": region == DEFAULT_REGION,
            "detail": region,
            "safeDetail": f"Provider region is {region}.",
        }
    )
    return DoctorResult(
        ok=all(item["ok"] for item in checks),
        network_contacted=True,
        checks=tuple(checks),
    )


def build_request_payload(shot: CompiledShot) -> dict[str, Any]:
    if not shot.storage_uri:
        raise ContractError(f"{shot.shot_id}: a GCS storageUri is required for resumable generation")
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
        token_provider: Callable[[], str] = active_access_token,
        timeout_seconds: int = 120,
        diagnostics_root: Path | None = None,
    ) -> None:
        if not project_id.strip():
            raise ValueError("project_id must not be empty")
        if region != DEFAULT_REGION:
            raise ValueError(f"This starter supports {DEFAULT_REGION}; got {region!r}")
        self.project_id = project_id
        self.region = region
        self.token_provider = token_provider
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
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            diagnostic = self._write_diagnostic(
                url=url,
                payload=payload,
                context=context,
                http_status=exc.code,
                response_headers=exc.headers,
                raw_body=body,
            )
            lowered = body.lower()
            if exc.code in {401, 403} and "serviceusage" in lowered:
                code = "api_disabled"
            elif exc.code in {401, 403}:
                code = "provider_access_denied"
            elif exc.code == 429 or "quota" in lowered or "resource_exhausted" in lowered:
                code = "provider_quota_exhausted"
            elif exc.code >= 500:
                code = "provider_transient"
            else:
                code = "provider_request_failed"
            provider_status = diagnostic.provider_status if diagnostic else None
            provider_error_code = diagnostic.provider_error_code if diagnostic else None
            provider_message = diagnostic.provider_message if diagnostic else None
            provider_label = provider_status or provider_error_code
            detail = f" {provider_label}: {provider_message}" if provider_label and provider_message else ""
            diagnostic_label = f" Diagnostic ID {diagnostic.diagnostic_id}." if diagnostic is not None else ""
            raise ProviderError(
                f"Google Veo returned HTTP {exc.code} ({code}).{detail}{diagnostic_label}",
                code=code,
                retryable=exc.code == 429 or exc.code >= 500,
                http_status=exc.code,
                provider_status=provider_status,
                provider_error_code=provider_error_code,
                diagnostic_id=diagnostic.diagnostic_id if diagnostic else None,
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


def copy_gcs_uri(uri: str, destination: Path) -> None:
    validate_gcs_uri(uri)
    if destination.exists():
        raise ProviderError(
            "A local file already exists at the requested clip destination.",
            code="local_clip_conflict",
        )
    executable = shutil.which("gcloud")
    if not executable:
        raise ProviderError("gcloud CLI is not installed or not on PATH")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _run([executable, "storage", "cp", uri, str(destination)], timeout=600)
    if result.returncode != 0:
        raise ProviderError(
            "The generated clip could not be downloaded from GCS.",
            code="provider_download_failed",
            retryable=True,
        )


def upload_reference_image(
    source: Path,
    destination_uri: str,
    *,
    expected_sha256: str,
) -> None:
    validate_gcs_uri(destination_uri)
    if not source.is_file() or sha256_file(source) != expected_sha256:
        raise ProviderError(
            "The approved local reference image is missing or changed; compile a fresh exact plan.",
            code="reference_asset_changed",
        )
    executable = shutil.which("gcloud")
    if not executable:
        raise ProviderError("gcloud CLI is not installed or not on PATH")
    result = _run(
        [
            executable,
            "storage",
            "cp",
            "--content-type",
            "image/png" if source.suffix.lower() == ".png" else "image/jpeg",
            str(source),
            destination_uri,
        ],
        timeout=600,
    )
    if result.returncode != 0:
        raise ProviderError(
            "The approved reference image could not be uploaded to GCS.",
            code="provider_reference_upload_failed",
            retryable=True,
        )

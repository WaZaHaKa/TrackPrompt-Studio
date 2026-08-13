from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import CompiledShot, ContractError

DEFAULT_REGION = "us-central1"
_GCS_URI = re.compile(r"^gs://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/[^?#]+$")
_REDACTED_ERROR_LIMIT = 1_000


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str = "provider_error", retryable: bool = False) -> None:
        super().__init__(message[:_REDACTED_ERROR_LIMIT])
        self.code = code
        self.retryable = retryable


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
    return {
        "instances": [{"prompt": shot.prompt}],
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
            "task": "textToVideo",
        },
    }


class VeoRestClient:
    def __init__(
        self,
        *,
        project_id: str,
        region: str = DEFAULT_REGION,
        token_provider: Callable[[], str] = active_access_token,
        timeout_seconds: int = 120,
    ) -> None:
        if not project_id.strip():
            raise ValueError("project_id must not be empty")
        if region != DEFAULT_REGION:
            raise ValueError(f"This starter supports {DEFAULT_REGION}; got {region!r}")
        self.project_id = project_id
        self.region = region
        self.token_provider = token_provider
        self.timeout_seconds = timeout_seconds

    def _model_url(self, model_id: str, method: str) -> str:
        return (
            f"https://{self.region}-aiplatform.googleapis.com/v1/projects/"
            f"{self.project_id}/locations/{self.region}/publishers/google/"
            f"models/{model_id}:{method}"
        )

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
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
            raise ProviderError(
                f"Google API returned HTTP {exc.code} ({code}).",
                code=code,
                retryable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                "Google API network request failed.",
                code="provider_network_error",
                retryable=True,
            ) from exc
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("Google API returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError("Google API response root was not an object")
        return value

    def submit(self, shot: CompiledShot) -> dict[str, Any]:
        response = self._post(
            self._model_url(shot.model_id, "predictLongRunning"),
            build_request_payload(shot),
        )
        if not isinstance(response.get("name"), str):
            raise ProviderError("Generation response did not contain a long-running operation name")
        return response

    def fetch(self, *, model_id: str, operation_name: str) -> dict[str, Any]:
        return self._post(
            self._model_url(model_id, "fetchPredictOperation"),
            {"operationName": operation_name},
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

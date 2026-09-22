"""Explicit-token GCS and one-shot Vertex transport; no ambient ADC lookup."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..jsonio import canonical_json_bytes, sha256_file


class CloudError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False, status: int | None = None,
                 evidence: dict[str, Any] | None = None, context: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.code, self.retryable, self.status = code, retryable, status
        self.evidence = evidence
        self.context = context or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "retryable": self.retryable, "httpStatus": self.status,
                "context": self.context}


def storage_context(method: str, url: str, object_name: str | None = None) -> dict[str, str]:
    """Fixed categories and hashed identity only; never retain credentials or session URLs."""
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    if parsed.hostname != "storage.googleapis.com":
        return {"operation": "provider_poll", "method": method}
    name = object_name
    if "/o/" in parsed.path:
        name = urllib.parse.unquote(parsed.path.split("/o/", 1)[1])
    if "upload_id" in query:
        category = "resumable_session_put"
    elif query.get("uploadType") == ["resumable"]:
        category = "resumable_upload_initiation"
    elif query.get("uploadType") == ["multipart"]:
        category = "multipart_json_insert"
    elif method == "PATCH":
        category = "object_metadata_patch"
    elif method == "DELETE":
        category = "object_delete"
    elif query.get("alt") == ["media"]:
        category = "object_media_get"
    elif "prefix" in query:
        category, name = "object_list", query["prefix"][0]
    else:
        category = "object_metadata_get" if name is not None else "bucket_metadata_get"
    result = {"operation": category, "method": method}
    if name is not None:
        result["objectHash"] = hashlib.sha256(name.encode()).hexdigest()
    generation = query.get("generation", query.get("ifGenerationMatch", []))
    if generation:
        result["generation"] = generation[0] if re.fullmatch(r"[0-9]{1,30}", generation[0]) else "invalid"
    return result


class StateConflict(CloudError):
    def __init__(self) -> None:
        super().__init__("cloud_state_changed")


class SubmissionUnknown(CloudError):
    def __init__(self) -> None:
        super().__init__("provider_submission_outcome_unknown")


class BeforeSubmission(CloudError):
    """No provider HTTP request was made."""


class SubmissionRejected(CloudError):
    """A definite HTTP rejection, never a transport or malformed-ack outcome."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def _open(request: urllib.request.Request, *, timeout: int) -> Any:
    # Never forward an access token to a redirect target. GCS upload sessions
    # are followed explicitly only after their HTTPS host has been checked.
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _read_json_response(response: Any) -> dict[str, Any]:
    raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise CloudError("cloud_response_too_large")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CloudError("cloud_response_invalid") from exc
    if not isinstance(value, dict):
        raise CloudError("cloud_response_invalid")
    return value


def _redacted_error(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): ("[REDACTED]" if re.search(r"authorization|token|secret|password|credential", str(key), re.I)
                           else _redacted_error(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted_error(item) for item in value[:100]]
    if isinstance(value, str):
        return re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value[:16000])
    return value


class AttachedIdentity:
    def __init__(self, project: str, service_account: str) -> None:
        if os.name == "nt" or not os.environ.get("BATCH_JOB_UID") or os.environ.get("BATCH_TASK_INDEX") != "0":
            raise CloudError("attached_batch_runtime_required")
        def metadata(resource: str) -> str:
            request = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/" + resource,
                                             headers={"Metadata-Flavor": "Google"})
            with _open(request, timeout=10) as response:
                if response.headers.get("Metadata-Flavor") != "Google":
                    raise CloudError("metadata_identity_unverified")
                return str(response.read(4096).decode().strip())
        if (metadata("project/project-id") != project
                or metadata("instance/service-accounts/default/email") != service_account):
            raise CloudError("attached_runtime_identity_mismatch")
        from google.auth.compute_engine import Credentials
        from google.auth.transport.requests import Request
        self.credentials = Credentials(service_account_email=service_account,
                                       scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.request = Request()

    def token(self) -> str:
        if not self.credentials.valid:
            try:
                self.credentials.refresh(self.request)
            except Exception as exc:
                raise CloudError("runtime_identity_refresh_unavailable", retryable=True) from exc
        value = self.credentials.token
        if not isinstance(value, str) or not value:
            raise CloudError("attached_runtime_token_unavailable", retryable=True)
        return value

    def invalidate(self) -> None:
        self.credentials.token = None


def _refresh_attached_token(provider: Callable[[], str]) -> None:
    invalidate = getattr(getattr(provider, "__self__", None), "invalidate", None)
    if callable(invalidate):
        invalidate()


class GcsClient:
    def __init__(self, bucket: str, token_provider: Callable[[], str]) -> None:
        self.bucket, self.token_provider = bucket, token_provider

    def _name(self, name: str) -> str:
        if name.startswith("gs://"):
            prefix = f"gs://{self.bucket}/"
            if not name.startswith(prefix):
                raise CloudError("storage_bucket_mismatch")
            name = name[len(prefix):]
        if not name or ".." in name.split("/") or any(ord(c) < 32 for c in name):
            raise CloudError("storage_object_invalid")
        return name

    def _url(self, name: str) -> str:
        return f"https://storage.googleapis.com/storage/v1/b/{urllib.parse.quote(self.bucket, safe='')}/o/{urllib.parse.quote(self._name(name), safe='')}"

    def _json(self, method: str, url: str, data: bytes | None = None,
              headers: dict[str, str] | None = None, *, object_name: str | None = None) -> dict[str, Any]:
        context = storage_context(method, url, object_name)
        for number in range(5):
            try:
                request = urllib.request.Request(url, data=data, method=method,
                    headers={"Authorization": f"Bearer {self.token_provider()}", "Content-Type": "application/json", **(headers or {})})
                with _open(request, timeout=120) as response:
                    if getattr(response, "status", None) == 204:
                        return {}
                    return _read_json_response(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 412:
                    conflict = StateConflict()
                    conflict.context = context
                    raise conflict from exc
                if exc.code == 401 and number < 4:
                    _refresh_attached_token(self.token_provider)
                    continue
                if exc.code not in {408, 429, 500, 502, 503, 504} or number == 4:
                    raise CloudError(f"storage_http_{exc.code}", retryable=exc.code in {408, 429, 500, 502, 503, 504}, status=exc.code, context=context) from exc
            except CloudError as exc:
                if not exc.context:
                    exc.context = context
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if number == 4:
                    raise CloudError("storage_transport_unavailable", retryable=True, context=context) from exc
            time.sleep(min(16, 2 ** number))
        raise CloudError("storage_transport_unavailable", retryable=True)

    def bucket_metadata(self) -> dict[str, Any]:
        return self._json("GET", f"https://storage.googleapis.com/storage/v1/b/{urllib.parse.quote(self.bucket, safe='')}")

    def metadata(self, name: str, generation: str | None = None) -> dict[str, Any] | None:
        try:
            return self._json("GET", self._url(name) + ("?generation=" + str(generation) if generation else ""))
        except CloudError as exc:
            if exc.status == 404:
                return None
            raise

    def get_json(self, name: str, generation: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        # Replacing a live object in a non-versioned bucket can remove the
        # generation between metadata GET and media GET. Only latest reads
        # follow a proven replacement; explicit immutable generations stay strict.
        for _ in range(5):
            meta = self.metadata(name, generation)
            if meta is None:
                raise CloudError("storage_object_missing", status=404,
                                 context=storage_context("GET", self._url(name)))
            url = self._url(name) + "?alt=media&generation=" + str(meta["generation"])
            try:
                return self._json("GET", url), meta
            except CloudError as exc:
                if exc.status != 404 or generation is not None:
                    raise
                current = self.metadata(name)
                if current is None or str(current["generation"]) == str(meta["generation"]):
                    raise
        raise CloudError("storage_snapshot_changed", retryable=True, context=storage_context("GET", url))

    @staticmethod
    def receipt(meta: dict[str, Any], sha256: str | None = None) -> dict[str, Any]:
        return {"object": meta["name"], "generation": str(meta["generation"]),
                "sha256": sha256 or meta.get("metadata", {}).get("sha256"),
                "size": int(meta["size"]), "mimeType": meta.get("contentType", "application/octet-stream")}

    def put_json(self, name: str, value: dict[str, Any], if_generation_match: int | str) -> dict[str, Any]:
        data = canonical_json_bytes(value)
        digest = hashlib.sha256(data).hexdigest()
        boundary = "trackprompt-" + uuid4().hex
        meta = {"name": self._name(name), "contentType": "application/json", "metadata": {"sha256": digest}}
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                + canonical_json_bytes(meta) + f"\r\n--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode()
                + data + f"\r\n--{boundary}--\r\n".encode())
        url = f"https://storage.googleapis.com/upload/storage/v1/b/{urllib.parse.quote(self.bucket, safe='')}/o?uploadType=multipart&ifGenerationMatch={if_generation_match}"
        try:
            return self._json("POST", url, body, {"Content-Type": f"multipart/related; boundary={boundary}"}, object_name=self._name(name))
        except StateConflict:
            current = self.metadata(name)
            if current and current.get("metadata", {}).get("sha256") == digest:
                return current  # Previous identical conditional write was accepted.
            raise

    def upload_file(self, name: str, source: Path, *, if_generation_match: int | str = 0,
                    content_type: str = "application/octet-stream") -> dict[str, Any]:
        digest = sha256_file(source)
        size = source.stat().st_size
        md5 = hashlib.md5(usedforsecurity=False)
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                md5.update(chunk)
        encoded_md5 = base64.b64encode(md5.digest()).decode()
        prior = self.metadata(name)
        if prior and prior.get("metadata", {}).get("sha256") == digest and int(prior["size"]) == size:
            return self.receipt(prior, digest)
        if prior and str(if_generation_match) == "0":
            raise StateConflict()
        url = (f"https://storage.googleapis.com/upload/storage/v1/b/{urllib.parse.quote(self.bucket, safe='')}/o"
               f"?uploadType=resumable&ifGenerationMatch={if_generation_match}")
        metadata = {"name": self._name(name), "contentType": content_type,
                    "metadata": {"sha256": digest}, "md5Hash": encoded_md5}
        for number in range(5):
            context = storage_context("POST", url, self._name(name))
            try:
                request = urllib.request.Request(url, method="POST", data=canonical_json_bytes(metadata), headers={
                    "Authorization": f"Bearer {self.token_provider()}", "Content-Type": "application/json",
                    "X-Upload-Content-Type": content_type, "X-Upload-Content-Length": str(size)})
                with _open(request, timeout=120) as response:
                    session = response.headers["Location"]
                parsed = urllib.parse.urlparse(session)
                if parsed.scheme != "https" or parsed.hostname not in {"storage.googleapis.com", "www.googleapis.com"}:
                    raise CloudError("storage_upload_session_invalid")
                with source.open("rb") as stream:
                    context = {"operation": "resumable_session_put", "method": "PUT",
                               "objectHash": hashlib.sha256(self._name(name).encode()).hexdigest()}
                    request = urllib.request.Request(session, method="PUT", data=stream, headers={
                        "Authorization": f"Bearer {self.token_provider()}", "Content-Type": content_type,
                        "Content-Length": str(size)})
                    with _open(request, timeout=600) as response:
                        result = _read_json_response(response)
                if result.get("md5Hash") != encoded_md5 or int(result.get("size", -1)) != size:
                    raise CloudError("storage_uploaded_integrity_mismatch")
                return self.receipt(result, digest)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                current = self.metadata(name)
                if current and current.get("metadata", {}).get("sha256") == digest and current.get("md5Hash") == encoded_md5:
                    return self.receipt(current, digest)
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
                    _refresh_attached_token(self.token_provider)
                if isinstance(exc, urllib.error.HTTPError) and exc.code not in {401, 408, 429, 500, 502, 503, 504}:
                    raise CloudError(f"storage_http_{exc.code}", status=exc.code, context=context) from exc
                if number == 4:
                    raise CloudError("storage_transfer_failed", retryable=True, context=context) from exc
                time.sleep(min(16, 2 ** number))
        raise CloudError("storage_transfer_failed", retryable=True)

    def download(self, name: str, destination: Path, *, generation: str,
                 expected_sha256: str | None = None) -> dict[str, Any]:
        meta = self.metadata(name, generation)
        if meta is None:
            raise CloudError("storage_generation_missing")
        expected = expected_sha256 or meta.get("metadata", {}).get("sha256")
        if destination.is_file() and expected and sha256_file(destination) == expected:
            return self.receipt(meta, expected)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial-" + uuid4().hex)
        for number in range(5):
            try:
                request = urllib.request.Request(self._url(name) + "?alt=media&generation=" + str(generation),
                    headers={"Authorization": f"Bearer {self.token_provider()}"})
                digest = hashlib.sha256()
                with _open(request, timeout=600) as response, temporary.open("wb") as stream:
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        stream.write(chunk)
                        digest.update(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                actual = digest.hexdigest()
                if temporary.stat().st_size != int(meta["size"]) or (expected and actual != expected):
                    raise CloudError("storage_download_integrity_mismatch", retryable=True)
                os.replace(temporary, destination)
                return self.receipt(meta, actual)
            except (urllib.error.URLError, TimeoutError, OSError, CloudError) as exc:
                temporary.unlink(missing_ok=True)
                context = storage_context("GET", self._url(name) + "?alt=media&generation=" + str(generation))
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
                    _refresh_attached_token(self.token_provider)
                if isinstance(exc, urllib.error.HTTPError) and exc.code not in {401, 408, 429, 500, 502, 503, 504}:
                    raise CloudError(f"storage_http_{exc.code}", status=exc.code, context=context) from exc
                if isinstance(exc, CloudError) and not exc.retryable:
                    raise
                if number == 4:
                    raise CloudError("storage_transfer_failed", retryable=True, context=context) from exc
                time.sleep(min(16, 2 ** number))
        raise CloudError("storage_transfer_failed", retryable=True)

    def _disposable_object(self, name: str, required_prefix: str) -> str:
        prefix = self._name(required_prefix)
        if (not re.fullmatch(r"hayale-final/[a-z0-9-]+/smoke-object-io/[a-z0-9-]+/[a-f0-9]{32}/", prefix)
                or not self._name(name).startswith(prefix) or self._name(name) == prefix):
            raise CloudError("disposable_smoke_prefix_required")
        return self._name(name)

    def update_metadata(self, name: str, metadata: dict[str, str], *, generation: str,
                        required_prefix: str) -> dict[str, Any]:
        name = self._disposable_object(name, required_prefix)
        current = self.metadata(name, generation)
        if current is None:
            raise CloudError("storage_generation_missing")
        query = urllib.parse.urlencode({"generation": generation, "ifGenerationMatch": generation,
                                        "ifMetagenerationMatch": current["metageneration"]})
        return self._json("PATCH", self._url(name) + "?" + query,
                          canonical_json_bytes({"metadata": {**current.get("metadata", {}), **metadata}}))

    def delete_object(self, name: str, *, generation: str, required_prefix: str) -> None:
        name = self._disposable_object(name, required_prefix)
        query = urllib.parse.urlencode({"generation": generation, "ifGenerationMatch": generation})
        try:
            self._json("DELETE", self._url(name) + "?" + query)
        except CloudError as exc:
            if exc.status != 404:
                raise

    def list_objects(self, prefix: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = ""
        while True:
            query = urllib.parse.urlencode({"prefix": self._name(prefix), "maxResults": 1000, **({"pageToken": token} if token else {})})
            page = self._json("GET", f"https://storage.googleapis.com/storage/v1/b/{urllib.parse.quote(self.bucket, safe='')}/o?{query}")
            items.extend(page.get("items", []))
            if len(items) > 10000:
                raise CloudError("storage_prefix_evidence_limit")
            token = page.get("nextPageToken", "")
            if not token:
                return items


class VertexClient:
    def __init__(self, project: str, region: str, model: str, token_provider: Callable[[], str]) -> None:
        self.project, self.region, self.model, self.token_provider = project, region, model, token_provider
        self.model_name = f"projects/{project}/locations/{region}/publishers/google/models/{model}"

    def valid_operation(self, value: Any) -> bool:
        return isinstance(value, str) and value.startswith(self.model_name + "/operations/") and len(value) > len(self.model_name) + 12

    def refresh_auth(self) -> None:
        _refresh_attached_token(self.token_provider)

    def submit_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            token = self.token_provider()
        except Exception as exc:
            raise BeforeSubmission("runtime_authentication_unavailable", retryable=True) from exc
        request = urllib.request.Request(f"https://{self.region}-aiplatform.googleapis.com/v1/{self.model_name}:predictLongRunning",
            method="POST", data=canonical_json_bytes(payload), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with _open(request, timeout=180) as response:
                value = _read_json_response(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 401, 403, 404, 422, 429}:
                try:
                    evidence = _redacted_error(_read_json_response(exc))
                except CloudError:
                    evidence = {"httpStatus": exc.code, "bodyUnreadable": True}
                raise SubmissionRejected(f"provider_request_http_{exc.code}", status=exc.code,
                    retryable=exc.code in {401, 429}, evidence=evidence) from exc
            raise SubmissionUnknown() from exc
        except (urllib.error.URLError, TimeoutError, OSError, CloudError) as exc:
            raise SubmissionUnknown() from exc
        if not self.valid_operation(value.get("name")):
            raise SubmissionUnknown()
        return value

    def poll(self, operation_name: str) -> dict[str, Any]:
        if not self.valid_operation(operation_name):
            raise CloudError("provider_operation_identity_invalid")
        transport = GcsClient("unused", self.token_provider)
        value = transport._json("POST", f"https://{self.region}-aiplatform.googleapis.com/v1/{self.model_name}:fetchPredictOperation",
                                canonical_json_bytes({"operationName": operation_name}))
        if value.get("name") != operation_name:
            raise CloudError("provider_operation_identity_mismatch")
        return value

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


class ComfyUIProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ComfyUIDeviceSnapshot:
    name: str
    type: str
    vram_total_bytes: int | None
    vram_free_bytes: int | None


@dataclass(frozen=True, slots=True)
class ComfyUIHealthSnapshot:
    version: str | None
    devices: tuple[ComfyUIDeviceSnapshot, ...]
    node_count: int
    object_info: dict[str, Any]
    model_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComfyUIProgressEvent:
    event: str
    prompt_id: str
    node_id: str | None = None
    value: int | None = None
    maximum: int | None = None
    completed: bool = False
    error: bool = False


def _safe_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0:
        return int(value)
    return None


def _safe_endpoint(value: str, *, allow_non_loopback: bool) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ComfyUIProviderError("provider_endpoint_invalid", "The ComfyUI endpoint is invalid.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ComfyUIProviderError("provider_endpoint_invalid", "The ComfyUI endpoint is invalid.")
    loopback = parsed.hostname.casefold() in {"localhost", "127.0.0.1", "::1"}
    if not loopback and not allow_non_loopback:
        raise ComfyUIProviderError(
            "provider_endpoint_not_local",
            "ComfyUI must use a loopback endpoint unless non-loopback access is explicitly enabled.",
        )
    normalized_path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _bounded_json(payload: bytes, *, label: str) -> dict[str, Any]:
    if len(payload) > 20_000_000:
        raise ComfyUIProviderError("provider_response_too_large", f"The ComfyUI {label} response is too large.")
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ComfyUIProviderError(
            "provider_response_invalid", f"The ComfyUI {label} response is invalid."
        ) from exc
    if not isinstance(value, dict):
        raise ComfyUIProviderError("provider_response_invalid", f"The ComfyUI {label} response is invalid.")
    return value


def _enum_strings(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, list):
        if value and isinstance(value[0], list):
            result.extend(str(item) for item in value[0] if isinstance(item, str))
        for child in value[1:]:
            result.extend(_enum_strings(child))
    elif isinstance(value, dict):
        for child in value.values():
            result.extend(_enum_strings(child))
    return result


def discover_model_names(object_info: dict[str, Any]) -> tuple[str, ...]:
    names: set[str] = set()
    for class_name, value in object_info.items():
        if not isinstance(value, dict):
            continue
        lowered = str(class_name).casefold()
        if not any(token in lowered for token in ("loader", "checkpoint", "unet", "vae", "clip")):
            continue
        raw_inputs = value.get("input")
        for candidate in _enum_strings(raw_inputs):
            if not candidate or len(candidate) > 300 or "\x00" in candidate:
                continue
            # API-facing readiness needs identities, not installation paths.
            names.add(Path(candidate.replace("\\", "/")).name)
    return tuple(sorted(names, key=str.casefold))


class ComfyUIClient:
    """Bounded client for the local ComfyUI HTTP and WebSocket APIs."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        *,
        allow_non_loopback: bool = False,
        connect_timeout_seconds: float = 5,
        request_timeout_seconds: float = 30,
    ) -> None:
        self.base_url = _safe_endpoint(base_url, allow_non_loopback=allow_non_loopback)
        self.connect_timeout_seconds = connect_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        timeout_seconds: float | None = None,
        maximum_bytes: int = 20_000_000,
    ) -> bytes:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self._url(path), data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds or self.request_timeout_seconds,
            ) as response:
                payload = response.read(maximum_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise ComfyUIProviderError(
                "provider_http_error",
                "ComfyUI rejected the local request.",
                retryable=exc.code in {408, 409, 425, 429, 500, 502, 503, 504},
                status_code=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ComfyUIProviderError(
                "provider_unavailable",
                "The configured local ComfyUI service is unavailable.",
                retryable=True,
            ) from exc
        if len(payload) > maximum_bytes:
            raise ComfyUIProviderError("provider_response_too_large", "The ComfyUI response is too large.")
        return bytes(payload)

    def _request_json(
        self,
        method: str,
        path: str,
        value: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
        label: str = "API",
    ) -> dict[str, Any]:
        body = None
        content_type = None
        if value is not None:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
            if len(body) > 20_000_000:
                raise ComfyUIProviderError("provider_request_too_large", "The ComfyUI request is too large.")
            content_type = "application/json"
        payload = self._request_bytes(
            method,
            path,
            body=body,
            content_type=content_type,
            timeout_seconds=timeout_seconds,
        )
        return _bounded_json(payload, label=label)

    def health(self) -> ComfyUIHealthSnapshot:
        stats = self._request_json("GET", "/system_stats", label="system status")
        object_info = self._request_json("GET", "/object_info", label="node capability")
        raw_system = stats.get("system")
        system = raw_system if isinstance(raw_system, dict) else {}
        version_raw = system.get("comfyui_version") or system.get("version")
        version = str(version_raw)[:120] if version_raw is not None else None
        devices: list[ComfyUIDeviceSnapshot] = []
        raw_devices = stats.get("devices")
        if isinstance(raw_devices, list):
            for raw in raw_devices[:16]:
                if not isinstance(raw, dict):
                    continue
                name = " ".join(str(raw.get("name") or "Local compute device").split())[:160]
                kind = " ".join(str(raw.get("type") or "unknown").split())[:80]
                devices.append(
                    ComfyUIDeviceSnapshot(
                        name=name,
                        type=kind,
                        vram_total_bytes=_safe_int(raw.get("vram_total")),
                        vram_free_bytes=_safe_int(raw.get("vram_free")),
                    )
                )
        return ComfyUIHealthSnapshot(
            version=version,
            devices=tuple(devices),
            node_count=len(object_info),
            object_info=object_info,
            model_names=discover_model_names(object_info),
        )

    def upload_image(self, path: Path, *, upload_name: str, overwrite: bool = False) -> str:
        if not path.is_file() or path.stat().st_size <= 0:
            raise ComfyUIProviderError("provider_input_missing", "The local keyframe is unavailable.")
        if len(upload_name) > 120 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", upload_name):
            raise ComfyUIProviderError("provider_upload_name_invalid", "The upload identity is invalid.")
        maximum = 50_000_000
        if path.stat().st_size > maximum:
            raise ComfyUIProviderError("provider_input_too_large", "The local keyframe is too large.")
        boundary = f"----TrackPrompt{secrets.token_hex(16)}"
        media_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{upload_name}"\r\n'
            f"Content-Type: {media_type}\r\n\r\n"
        ).encode("ascii")
        suffix = (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
            f"{'true' if overwrite else 'false'}\r\n--{boundary}--\r\n"
        ).encode("ascii")
        payload = prefix + path.read_bytes() + suffix
        response = self._request_bytes(
            "POST",
            "/upload/image",
            body=payload,
            content_type=f"multipart/form-data; boundary={boundary}",
            maximum_bytes=1_000_000,
        )
        value = _bounded_json(response, label="image upload")
        returned = value.get("name")
        if not isinstance(returned, str) or not returned:
            raise ComfyUIProviderError("provider_upload_invalid", "ComfyUI did not confirm the image upload.")
        return returned

    def queue_prompt(self, workflow: dict[str, Any], *, client_id: str) -> str:
        response = self._request_json(
            "POST",
            "/prompt",
            {"prompt": workflow, "client_id": client_id},
            timeout_seconds=60,
            label="prompt submission",
        )
        prompt_id = response.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyUIProviderError(
                "provider_submission_invalid", "ComfyUI did not return a prompt identity."
            )
        return prompt_id

    def history(self, prompt_id: str) -> dict[str, Any]:
        safe = urllib.parse.quote(prompt_id, safe="")
        return self._request_json("GET", f"/history/{safe}", label="history")

    def cancel(self, prompt_id: str) -> None:
        # Interrupt the active workflow, then remove this exact queued prompt if
        # it has not started. Both operations are idempotent in ComfyUI.
        try:
            self._request_bytes(
                "POST",
                "/interrupt",
                body=b"{}",
                content_type="application/json",
                maximum_bytes=1_000_000,
            )
        except ComfyUIProviderError as exc:
            if not exc.retryable:
                raise
        self._request_bytes(
            "POST",
            "/queue",
            body=json.dumps({"delete": [prompt_id]}, separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
            maximum_bytes=1_000_000,
        )

    async def progress_events(
        self,
        *,
        prompt_id: str,
        client_id: str,
        idle_timeout_seconds: float = 120,
        total_timeout_seconds: float = 3_600,
    ) -> AsyncIterator[ComfyUIProgressEvent]:
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:  # pragma: no cover - uvicorn[standard] supplies this in production
            raise ComfyUIProviderError(
                "provider_websocket_unavailable",
                "The local WebSocket client dependency is unavailable.",
            ) from exc
        parsed = urllib.parse.urlsplit(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = urllib.parse.urlencode({"clientId": client_id})
        ws_url = urllib.parse.urlunsplit((scheme, parsed.netloc, f"{parsed.path}/ws", query, ""))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + total_timeout_seconds
        try:
            async with connect(
                ws_url,
                open_timeout=self.connect_timeout_seconds,
                max_size=2_000_000,
                ping_interval=20,
                ping_timeout=20,
            ) as socket:
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise ComfyUIProviderError(
                            "provider_total_timeout", "ComfyUI generation exceeded its bounded deadline."
                        )
                    raw = await asyncio.wait_for(socket.recv(), min(idle_timeout_seconds, remaining))
                    if isinstance(raw, bytes):
                        continue
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
                        continue
                    event_type = str(message.get("type") or "unknown")
                    data = cast(dict[str, Any], message["data"])
                    event_prompt_id = data.get("prompt_id")
                    if event_prompt_id not in {None, prompt_id}:
                        continue
                    value = _safe_int(data.get("value"))
                    maximum = _safe_int(data.get("max"))
                    node = data.get("node")
                    node_id = str(node) if node is not None else None
                    completed = event_type == "executing" and node is None
                    error = event_type in {"execution_error", "execution_interrupted"}
                    yield ComfyUIProgressEvent(
                        event=event_type,
                        prompt_id=prompt_id,
                        node_id=node_id,
                        value=value,
                        maximum=maximum,
                        completed=completed,
                        error=error,
                    )
                    if completed or error:
                        return
        except TimeoutError as exc:
            raise ComfyUIProviderError(
                "provider_idle_timeout",
                "ComfyUI stopped reporting progress before the bounded idle deadline.",
                retryable=True,
            ) from exc
        except ComfyUIProviderError:
            raise
        except OSError as exc:
            raise ComfyUIProviderError(
                "provider_websocket_failed",
                "The local ComfyUI progress connection failed.",
                retryable=True,
            ) from exc

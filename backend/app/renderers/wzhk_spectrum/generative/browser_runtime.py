from __future__ import annotations

import ctypes
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Protocol, cast
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4

LOOPBACK_HOST = "127.0.0.1"
ENTRYPOINT_ROUTE = "/index.html"
CONFIG_ROUTE = "/runtime-config.json"
CONFIG_ALIAS_ROUTE = "/config/runtime-config.json"
LOGO_ROUTE = "/assets/logo"
AUDIO_ROUTE = "/assets/audio"
MAX_EVENT_BYTES = 64 * 1024
MAX_CONTROL_SECONDS = 24 * 60 * 60
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_RESERVED_ROUTES = frozenset(
    {ENTRYPOINT_ROUTE, CONFIG_ROUTE, CONFIG_ALIAS_ROUTE, LOGO_ROUTE, AUDIO_ROUTE}
)
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_RUNTIME_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SOFTWARE_RENDERER_MARKERS = (
    "swiftshader",
    "llvmpipe",
    "software rasterizer",
    "microsoft basic render",
)


class BrowserRuntimeError(RuntimeError):
    """Safe browser-runtime failure."""


class BrowserUnavailableError(BrowserRuntimeError):
    """No supported local browser could be started."""


class BrowserRuntimeProtocolError(BrowserRuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class BrowserFamily(StrEnum):
    EDGE = "edge"
    CHROME = "chrome"


class BrowserCapabilityState(StrEnum):
    READY = "READY"
    WEBGL2_UNAVAILABLE = "WEBGL2_UNAVAILABLE"
    GPU_RENDERER_UNAVAILABLE = "GPU_RENDERER_UNAVAILABLE"
    SHADER_COMPILE_FAILED = "SHADER_COMPILE_FAILED"
    PERFORMANCE_INSUFFICIENT = "PERFORMANCE_INSUFFICIENT"
    BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"


class BrowserRuntimePhase(StrEnum):
    CREATED = "CREATED"
    SERVER_READY = "SERVER_READY"
    BROWSER_STARTING = "BROWSER_STARTING"
    BROWSER_RUNNING = "BROWSER_RUNNING"
    RUNTIME_READY = "RUNTIME_READY"
    STARTED = "STARTED"
    ENDED = "ENDED"
    ERROR = "ERROR"
    CLOSED = "CLOSED"


class BrowserRuntimeEventState(StrEnum):
    READY = "ready"
    STARTED = "started"
    TELEMETRY = "telemetry"
    ENDED = "ended"
    ERROR = "error"


class BrowserControlCommand(StrEnum):
    RUN = "run"
    PAUSE = "pause"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class BrowserInstallation:
    family: BrowserFamily
    executable: Path


@dataclass(frozen=True, slots=True)
class RuntimeResource:
    route: str
    path: Path
    content_type: str
    allow_ranges: bool = False


@dataclass(frozen=True, slots=True)
class BrowserRuntimeResources:
    entrypoint: Path
    config: Path
    logo: Path
    audio: Path
    runtime_files: Mapping[str, Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_files", MappingProxyType(dict(self.runtime_files)))

    def build_allowlist(self, job_root: Path) -> Mapping[str, RuntimeResource]:
        resources: dict[str, RuntimeResource] = {
            ENTRYPOINT_ROUTE: _resource(job_root, ENTRYPOINT_ROUTE, self.entrypoint),
            CONFIG_ROUTE: _resource(job_root, CONFIG_ROUTE, self.config),
            CONFIG_ALIAS_ROUTE: _resource(job_root, CONFIG_ALIAS_ROUTE, self.config),
            LOGO_ROUTE: _resource(job_root, LOGO_ROUTE, self.logo),
            AUDIO_ROUTE: _resource(job_root, AUDIO_ROUTE, self.audio, allow_ranges=True),
        }
        for route, path in sorted(self.runtime_files.items()):
            validated_route = _validate_route(route)
            if validated_route in _RESERVED_ROUTES or validated_route in resources:
                raise BrowserRuntimeError("The browser runtime resource route is duplicated or reserved.")
            resources[validated_route] = _resource(job_root, validated_route, path)
        return MappingProxyType(resources)


@dataclass(frozen=True, slots=True)
class BrowserCapabilityReport:
    state: BrowserCapabilityState
    webgl2: bool | None
    gpu_renderer: str | None
    shader_compiled: bool | None
    performance_measured: bool
    performance_sufficient: bool | None
    renderer_fps: float | None
    average_frame_time_ms: float | None
    point_count: int | None
    detail: str

    @classmethod
    def browser_unavailable(cls, detail: str) -> BrowserCapabilityReport:
        return cls(
            state=BrowserCapabilityState.BROWSER_UNAVAILABLE,
            webgl2=None,
            gpu_renderer=None,
            shader_compiled=None,
            performance_measured=False,
            performance_sufficient=None,
            renderer_fps=None,
            average_frame_time_ms=None,
            point_count=None,
            detail=detail[:500],
        )

    def to_json(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "webgl2": self.webgl2,
            "gpuRenderer": self.gpu_renderer,
            "shaderCompiled": self.shader_compiled,
            "performanceMeasured": self.performance_measured,
            "performanceSufficient": self.performance_sufficient,
            "rendererFps": self.renderer_fps,
            "averageFrameTimeMs": self.average_frame_time_ms,
            "pointCount": self.point_count,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class BrowserRuntimeTelemetry:
    sample_count: int
    timeline_seconds: float | None
    section: str | None
    source_shape: str | None
    target_shape: str | None
    morph: float | None
    renderer_fps: float
    average_frame_time_ms: float
    p95_frame_time_ms: float | None
    average_render_time_ms: float | None
    maximum_render_time_ms: float | None
    rendered_frames: int | None
    dropped_frames: int | None
    target_fps: float | None
    point_count: int
    canvas_width: int | None
    canvas_height: int | None
    gpu_renderer: str | None
    measured_at_monotonic_seconds: float

    def to_json(self) -> dict[str, object]:
        return {
            "sampleCount": self.sample_count,
            "timelineSeconds": self.timeline_seconds,
            "section": self.section,
            "sourceShape": self.source_shape,
            "targetShape": self.target_shape,
            "morph": self.morph,
            "rendererFps": self.renderer_fps,
            "averageFrameTimeMs": self.average_frame_time_ms,
            "p95FrameTimeMs": self.p95_frame_time_ms,
            "averageRenderTimeMs": self.average_render_time_ms,
            "maximumRenderTimeMs": self.maximum_render_time_ms,
            "renderedFrames": self.rendered_frames,
            "droppedFrames": self.dropped_frames,
            "targetFps": self.target_fps,
            "pointCount": self.point_count,
            "canvasWidth": self.canvas_width,
            "canvasHeight": self.canvas_height,
            "gpuRenderer": self.gpu_renderer,
            "measuredAtMonotonicSeconds": self.measured_at_monotonic_seconds,
        }


@dataclass(frozen=True, slots=True)
class BrowserControlSnapshot:
    revision: int
    command: BrowserControlCommand
    timeline_seconds: float
    audio_response_enabled: bool

    def to_json(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "command": self.command.value,
            "timelineSeconds": self.timeline_seconds,
            "audioResponseEnabled": self.audio_response_enabled,
        }


@dataclass(frozen=True, slots=True)
class BrowserRuntimeStatus:
    session_id: str
    job_id: str
    phase: BrowserRuntimePhase
    server_origin: str | None
    browser_family: BrowserFamily | None
    browser_pid: int | None
    last_event: BrowserRuntimeEventState | None
    capability: BrowserCapabilityReport | None
    last_runtime_milliseconds: int | None
    error_code: str | None
    error_message: str | None
    warning_code: str | None
    warning_message: str | None


class BrowserProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


BrowserProcessLauncher = Callable[[Sequence[str], Path], BrowserProcess]
BrowserDiscovery = Callable[[], BrowserInstallation | None]


class BinaryWriter(Protocol):
    def write(self, data: bytes) -> int: ...


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _validate_route(route: str) -> str:
    if (
        not route.startswith("/")
        or route.startswith("//")
        or "\\" in route
        or "?" in route
        or "#" in route
        or "%" in route
        or any(part in {"", ".", ".."} for part in route.split("/")[1:])
    ):
        raise BrowserRuntimeError("The browser runtime resource route is invalid.")
    return route


def _owned_file(job_root: Path, candidate: Path) -> Path:
    root = job_root.resolve(strict=True)
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise BrowserRuntimeError("A browser runtime resource escaped the job workspace.") from exc
    if not resolved.is_file():
        raise BrowserRuntimeError("A browser runtime resource is missing.")
    current = root
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            raise BrowserRuntimeError("Linked browser runtime resources are not accepted.")
    return resolved


def _content_type(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".glsl": "text/plain; charset=utf-8",
        ".vert": "text/plain; charset=utf-8",
        ".frag": "text/plain; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".aiff": "audio/aiff",
        ".aif": "audio/aiff",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
    }.get(path.suffix.casefold(), "application/octet-stream")


def _resource(
    job_root: Path,
    route: str,
    path: Path,
    *,
    allow_ranges: bool = False,
) -> RuntimeResource:
    validated_route = _validate_route(route)
    resolved = _owned_file(job_root, path)
    return RuntimeResource(validated_route, resolved, _content_type(resolved), allow_ranges)


def _browser_family(path: Path) -> BrowserFamily | None:
    name = path.name.casefold()
    if "msedge" in name or name in {"edge", "edge.exe"}:
        return BrowserFamily.EDGE
    if "chrome" in name:
        return BrowserFamily.CHROME
    return None


def discover_browser(
    *,
    explicit_path: Path | None = None,
    preferred: Sequence[BrowserFamily] = (BrowserFamily.EDGE, BrowserFamily.CHROME),
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> BrowserInstallation | None:
    """Discover Edge or Chrome in a stable family/path order."""
    if explicit_path is not None:
        resolved = explicit_path.expanduser().resolve(strict=False)
        family = _browser_family(resolved)
        if family is None:
            raise BrowserRuntimeError("The explicit browser is not Microsoft Edge or Google Chrome.")
        return BrowserInstallation(family, resolved) if resolved.is_file() else None

    values = dict(os.environ if environ is None else environ)
    roots = {
        "program_files": values.get("PROGRAMFILES", ""),
        "program_files_x86": values.get("PROGRAMFILES(X86)", ""),
        "local_app_data": values.get("LOCALAPPDATA", ""),
    }
    standard: dict[BrowserFamily, list[Path]] = {
        BrowserFamily.EDGE: [
            Path(roots[key]) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            for key in ("program_files_x86", "program_files", "local_app_data")
            if roots[key]
        ],
        BrowserFamily.CHROME: [
            Path(roots[key]) / "Google" / "Chrome" / "Application" / "chrome.exe"
            for key in ("program_files", "program_files_x86", "local_app_data")
            if roots[key]
        ],
    }
    command_names = {
        BrowserFamily.EDGE: ("msedge.exe", "msedge"),
        BrowserFamily.CHROME: ("chrome.exe", "chrome", "google-chrome", "google-chrome-stable"),
    }
    seen: set[Path] = set()
    for family in preferred:
        for candidate in standard.get(family, []):
            resolved = candidate.resolve(strict=False)
            if resolved not in seen and resolved.is_file():
                return BrowserInstallation(family, resolved)
            seen.add(resolved)
        for command_name in command_names.get(family, ()):
            found = which(command_name)
            if not found:
                continue
            resolved = Path(found).resolve(strict=False)
            if resolved not in seen and resolved.is_file():
                return BrowserInstallation(family, resolved)
            seen.add(resolved)
    return None


def _default_process_launcher(args: Sequence[str], working_directory: Path) -> BrowserProcess:
    return subprocess.Popen(
        list(args),
        cwd=str(working_directory),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        start_new_session=os.name != "nt",
    )


def _configure_borderless_browser_window(
    process_id: int,
    width: int,
    height: int,
    *,
    timeout_seconds: float = 10.0,
) -> None:
    if os.name != "nt":
        return
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    deadline = time.monotonic() + timeout_seconds
    target: int | None = None
    candidates: list[tuple[int, int]] = []
    while time.monotonic() < deadline and target is None:
        candidates.clear()

        def collect(window: int, _parameter: int) -> bool:
            if not user32.IsWindowVisible(window):
                return True
            owner_process = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(owner_process))
            if int(owner_process.value) != process_id:
                return True
            bounds = wintypes.RECT()
            if user32.GetWindowRect(window, ctypes.byref(bounds)):
                area = max(0, int(bounds.right - bounds.left)) * max(
                    0, int(bounds.bottom - bounds.top)
                )
                if area > 0:
                    candidates.append((area, int(window)))
            return True

        user32.EnumWindows(callback_type(collect), 0)
        if candidates:
            target = max(candidates)[1]
            break
        time.sleep(0.05)
    if target is None:
        raise BrowserRuntimeError("The owned browser window could not be positioned safely.")
    time.sleep(0.25)

    gwl_style = -16
    ws_caption = 0x00C00000
    ws_thickframe = 0x00040000
    ws_minimizebox = 0x00020000
    ws_maximizebox = 0x00010000
    ws_sysmenu = 0x00080000
    ws_popup = 0x80000000
    previous_dpi_context = None
    set_thread_dpi = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if set_thread_dpi is not None:
        set_thread_dpi.argtypes = [ctypes.c_void_p]
        set_thread_dpi.restype = ctypes.c_void_p
        previous_dpi_context = set_thread_dpi(ctypes.c_void_p(-4))
    try:
        style = int(user32.GetWindowLongW(target, gwl_style))
        style &= ~(ws_caption | ws_thickframe | ws_minimizebox | ws_maximizebox | ws_sysmenu)
        style |= ws_popup
        user32.SetWindowLongW(target, gwl_style, style)
        swp_framechanged = 0x0020
        swp_showwindow = 0x0040
        if not user32.SetWindowPos(
            target,
            0,
            0,
            0,
            width,
            height,
            swp_framechanged | swp_showwindow,
        ):
            raise BrowserRuntimeError(
                "The owned browser window could not enter borderless capture mode."
            )
    finally:
        if set_thread_dpi is not None and previous_dpi_context:
            set_thread_dpi(previous_dpi_context)


def _ensure_owned_directory(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve(strict=True)
    try:
        relative = candidate.absolute().relative_to(resolved_root)
    except ValueError as exc:
        raise BrowserRuntimeError("The browser runtime directory escaped the job workspace.") from exc
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.exists():
            if _is_link(current) or not current.is_dir():
                raise BrowserRuntimeError("The browser runtime directory is linked or occupied.")
            continue
        current.mkdir()
        with suppress(OSError):
            current.chmod(0o700)
    try:
        current.resolve(strict=True).relative_to(resolved_root)
    except ValueError as exc:
        raise BrowserRuntimeError("The browser runtime directory escaped the job workspace.") from exc
    return current


def _finite_number(
    value: object,
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrowserRuntimeProtocolError(HTTPStatus.UNPROCESSABLE_ENTITY, f"{name} must be numeric.")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise BrowserRuntimeProtocolError(HTTPStatus.UNPROCESSABLE_ENTITY, f"{name} is out of bounds.")
    return number


def _bounded_int(value: object, name: str, *, minimum: int, maximum: int) -> int:
    number = _finite_number(value, name, minimum=minimum, maximum=maximum)
    if not number.is_integer():
        raise BrowserRuntimeProtocolError(HTTPStatus.UNPROCESSABLE_ENTITY, f"{name} must be an integer.")
    return int(number)


class _RuntimeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    runtime_session: BrowserRuntimeSession

    def handle_error(self, request: Any, client_address: Any) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class _RuntimeRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def _session(self) -> BrowserRuntimeSession:
        return cast(_RuntimeHTTPServer, self.server).runtime_session

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def do_OPTIONS(self) -> None:
        if not self._authorized():
            self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._security_headers()
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        self._get(head_only=True)

    def do_GET(self) -> None:
        self._get(head_only=False)

    def do_POST(self) -> None:
        split = urlsplit(self.path)
        if split.path != "/api/event":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._authorized():
            self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return
        if self.headers.get_content_type().casefold() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "json_required"})
            return
        if self.headers.get("Transfer-Encoding"):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_body"})
            return
        raw_length = self.headers.get("Content-Length")
        try:
            content_length = int(raw_length or "")
        except ValueError:
            content_length = -1
        if content_length <= 0:
            self._json(HTTPStatus.LENGTH_REQUIRED, {"error": "content_length_required"})
            return
        if content_length > MAX_EVENT_BYTES:
            self.close_connection = True
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "event_too_large"})
            return
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(
                body.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except (UnicodeError, ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "invalid_event"})
            return
        try:
            state = self._session.accept_event(cast(dict[str, object], payload))
        except BrowserRuntimeProtocolError as exc:
            self._json(exc.status_code, {"error": "invalid_event", "detail": str(exc)})
            return
        self._json(HTTPStatus.ACCEPTED, {"accepted": True, "state": state.value})

    def _get(self, *, head_only: bool) -> None:
        split = urlsplit(self.path)
        if not self._authorized():
            self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"}, head_only=head_only)
            return
        if split.path == "/api/control":
            compatibility = self._session.control_api_payload()
            self._json(
                HTTPStatus.OK,
                {
                    "schemaVersion": "1.0.0",
                    "sessionId": self._session.session_id,
                    "jobId": self._session.job_id,
                    **compatibility,
                    "control": self._session.control_snapshot().to_json(),
                },
                head_only=head_only,
            )
            return
        resource = self._session.resource(split.path)
        if resource is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"}, head_only=head_only)
            return
        self._file(resource, head_only=head_only)

    def _authorized(self) -> bool:
        self._authenticated = False
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
        except ValueError:
            return False
        if self.headers.get("Host", "") != self._session.expected_host_header:
            return False
        candidates: list[str] = []
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        candidates.extend(query.get("token", []))
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            candidates.append(authorization[7:])
        header_token = self.headers.get("X-TrackPrompt-Runtime-Token")
        if header_token:
            candidates.append(header_token)
        cookie = SimpleCookie()
        with suppress(Exception):
            cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get(self._session.cookie_name)
        if morsel is not None:
            candidates.append(morsel.value)
        self._authenticated = any(
            hmac.compare_digest(candidate, self._session.token) for candidate in candidates
        )
        return self._authenticated

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        if getattr(self, "_authenticated", False):
            self.send_header(
                "Set-Cookie",
                f"{self._session.cookie_name}={self._session.token}; HttpOnly; SameSite=Strict; Path=/",
            )

    def _json(self, status: int, payload: Mapping[str, object], *, head_only: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    def _file(self, resource: RuntimeResource, *, head_only: bool) -> None:
        try:
            size = resource.path.stat().st_size
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"}, head_only=head_only)
            return
        start = 0
        end = max(0, size - 1)
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header and resource.allow_ranges:
            parsed = _parse_range(range_header, size)
            if parsed is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self._security_headers()
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = parsed
            status = HTTPStatus.PARTIAL_CONTENT
        length = max(0, end - start + 1) if size else 0
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", resource.content_type)
        self.send_header("Accept-Ranges", "bytes" if resource.allow_ranges else "none")
        self.send_header("Content-Length", str(length))
        if status is HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only or length == 0:
            return
        try:
            with resource.path.open("rb") as stream:
                stream.seek(start)
                _copy_bounded(stream, self.wfile, length)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    if size <= 0 or not value.startswith("bytes=") or "," in value:
        return None
    start_text, separator, end_text = value[6:].partition("-")
    if separator != "-":
        return None
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                return None
            return max(0, size - suffix), size - 1
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


def _copy_bounded(source: BinaryIO, destination: BinaryWriter, length: int) -> None:
    remaining = length
    while remaining > 0:
        chunk = source.read(min(64 * 1024, remaining))
        if not chunk:
            return
        destination.write(chunk)
        remaining -= len(chunk)


class BrowserRuntimeSession:
    """Job-owned, authenticated loopback runtime for a trusted WebGL bundle."""

    def __init__(
        self,
        job_root: Path,
        resources: BrowserRuntimeResources,
        *,
        browser: BrowserInstallation | None = None,
        browser_discovery: BrowserDiscovery = discover_browser,
        process_launcher: BrowserProcessLauncher = _default_process_launcher,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        width: int = 1920,
        height: int = 1080,
        minimum_renderer_fps: float = 55.0,
        maximum_average_frame_time_ms: float = 20.0,
        fullscreen: bool = False,
        renderer_id: str = "wzhk-generative-geometry",
        runtime_version: str = "1.0.0",
    ) -> None:
        self.job_root = job_root.resolve(strict=True)
        if not self.job_root.is_dir() or _is_link(self.job_root):
            raise BrowserRuntimeError("The browser runtime job root is invalid.")
        if _UUID.fullmatch(self.job_root.name) is None:
            raise BrowserRuntimeError("The browser runtime requires a canonical workspace job UUID.")
        if width < 320 or width > 7680 or height < 240 or height > 4320:
            raise BrowserRuntimeError("The browser runtime window size is out of bounds.")
        if not math.isfinite(minimum_renderer_fps) or minimum_renderer_fps <= 0:
            raise BrowserRuntimeError("The minimum renderer FPS is invalid.")
        if not math.isfinite(maximum_average_frame_time_ms) or maximum_average_frame_time_ms <= 0:
            raise BrowserRuntimeError("The maximum frame time is invalid.")
        token = token_factory()
        if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
            raise BrowserRuntimeError("The browser runtime token is invalid.")
        if _IDENTIFIER.fullmatch(renderer_id) is None:
            raise BrowserRuntimeError("The browser runtime renderer identity is invalid.")
        if _RUNTIME_VERSION.fullmatch(runtime_version) is None:
            raise BrowserRuntimeError("The browser runtime version is invalid.")

        self.session_id = uuid4().hex
        self.token = token
        self.renderer_id = renderer_id
        self.runtime_version = runtime_version
        self.job_id = self.job_root.name
        self.cookie_name = f"trackprompt_runtime_{self.session_id[:12]}"
        self.width = width
        self.height = height
        self.minimum_renderer_fps = minimum_renderer_fps
        self.maximum_average_frame_time_ms = maximum_average_frame_time_ms
        self.fullscreen = fullscreen
        self._resources = resources.build_allowlist(self.job_root)
        self._browser = browser
        self._browser_discovery = browser_discovery
        self._process_launcher = process_launcher
        self._session_root = self.job_root / "runtime" / "browser" / self.session_id
        self._profile_root = self._session_root / "profile"
        self._server: _RuntimeHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._process: BrowserProcess | None = None
        self._watchdog: threading.Thread | None = None
        self._closing = False
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._phase = BrowserRuntimePhase.CREATED
        self._last_event: BrowserRuntimeEventState | None = None
        self._capability: BrowserCapabilityReport | None = None
        self._telemetry: BrowserRuntimeTelemetry | None = None
        self._telemetry_samples = 0
        self._performance_samples: list[BrowserRuntimeTelemetry] = []
        self._last_runtime_milliseconds: int | None = None
        self._error_code: str | None = None
        self._error_message: str | None = None
        self._warning_code: str | None = None
        self._warning_message: str | None = None
        self._control = BrowserControlSnapshot(0, BrowserControlCommand.PAUSE, 0.0, True)

    @property
    def server_origin(self) -> str | None:
        server = self._server
        return f"http://{LOOPBACK_HOST}:{server.server_port}" if server is not None else None

    @property
    def expected_host_header(self) -> str:
        server = self._server
        if server is None:
            raise BrowserRuntimeError("The browser runtime server is not active.")
        return f"{LOOPBACK_HOST}:{server.server_port}"

    @property
    def entrypoint_url(self) -> str:
        origin = self.server_origin
        if origin is None:
            raise BrowserRuntimeError("The browser runtime server is not active.")
        return (
            f"{origin}{ENTRYPOINT_ROUTE}?token={quote(self.token, safe='')}"
            f"&sessionJobId={quote(self.job_id, safe='')}"
        )

    @property
    def profile_root(self) -> Path:
        return self._profile_root

    def resource(self, route: str) -> RuntimeResource | None:
        resource = self._resources.get(route)
        if resource is None:
            return None
        try:
            resolved = resource.path.resolve(strict=True)
            resolved.relative_to(self.job_root)
        except (OSError, ValueError):
            return None
        if resolved != resource.path or _is_link(resource.path) or not resolved.is_file():
            return None
        return resource

    def start_server(self) -> str:
        with self._condition:
            if self._phase is BrowserRuntimePhase.CLOSED:
                raise BrowserRuntimeError("The browser runtime session is closed.")
            if self._server is not None:
                return self.server_origin or ""
            _ensure_owned_directory(self.job_root, self._session_root)
            server = _RuntimeHTTPServer((LOOPBACK_HOST, 0), _RuntimeRequestHandler)
            if server.server_address[0] != LOOPBACK_HOST:
                server.server_close()
                raise BrowserRuntimeError("The browser runtime refused a non-loopback bind.")
            server.runtime_session = self
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.05},
                daemon=True,
                name=f"wzhk-geometry-http-{self.session_id[:8]}",
            )
            self._server = server
            self._server_thread = thread
            self._phase = BrowserRuntimePhase.SERVER_READY
            thread.start()
            self._condition.notify_all()
            return self.server_origin or ""

    def browser_command(self, installation: BrowserInstallation | None = None) -> tuple[str, ...]:
        selected = installation or self._browser
        if selected is None or not selected.executable.is_file():
            raise BrowserUnavailableError("Microsoft Edge or Google Chrome is unavailable.")
        if self._server is None:
            raise BrowserRuntimeError("Start the loopback server before building the browser command.")
        _ensure_owned_directory(self._session_root, self._profile_root)
        self._write_profile_marker()
        disabled_features = ",".join(
            (
                "AutofillServerCommunication",
                "MediaRouter",
                "OptimizationHints",
                "Translate",
            )
        )
        return (
            str(selected.executable),
            # Edge can relaunch itself under a Windows compatibility layer,
            # leaving the watched launcher PID exited while an orphan app
            # window survives. Keep this job-owned Edge process as the owner;
            # do not alter global compatibility settings or other browsers.
            *(("--edge-skip-compat-layer-relaunch",)
              if os.name == "nt" and selected.family is BrowserFamily.EDGE
              else ()),
            f"--app={self.entrypoint_url}",
            f"--user-data-dir={self._profile_root}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-sync",
            "--disable-extensions",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--disable-client-side-phishing-detection",
            "--disable-session-crashed-bubble",
            "--metrics-recording-only",
            "--no-proxy-server",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
            f"--disable-features={disabled_features}",
            "--autoplay-policy=no-user-gesture-required",
            *(("--kiosk",) if self.fullscreen else ()),
            "--window-position=0,0",
            f"--window-size={self.width},{self.height}",
            "--new-window",
        )

    def start_browser(self) -> BrowserRuntimeStatus:
        with self._condition:
            if self._phase is BrowserRuntimePhase.CLOSED:
                raise BrowserRuntimeError("The browser runtime session is closed.")
            if self._process is not None and self._process.poll() is None:
                return self.status_snapshot()
        self.start_server()
        selected = self._browser or self._browser_discovery()
        if selected is None or not selected.executable.is_file():
            report = BrowserCapabilityReport.browser_unavailable(
                "Microsoft Edge or Google Chrome was not found; no browser was launched."
            )
            with self._condition:
                self._capability = report
                self._phase = BrowserRuntimePhase.ERROR
                self._error_code = BrowserCapabilityState.BROWSER_UNAVAILABLE.value
                self._error_message = report.detail
                self._condition.notify_all()
            raise BrowserUnavailableError(report.detail)
        self._browser = selected
        command = self.browser_command(selected)
        with self._condition:
            self._phase = BrowserRuntimePhase.BROWSER_STARTING
            self._condition.notify_all()
        try:
            process = self._process_launcher(command, self._session_root)
        except OSError as exc:
            report = BrowserCapabilityReport.browser_unavailable(
                "The supported browser could not be started."
            )
            with self._condition:
                self._capability = report
                self._phase = BrowserRuntimePhase.ERROR
                self._error_code = BrowserCapabilityState.BROWSER_UNAVAILABLE.value
                self._error_message = report.detail
                self._condition.notify_all()
            raise BrowserUnavailableError(report.detail) from exc
        with self._condition:
            self._process = process
            self._phase = BrowserRuntimePhase.BROWSER_RUNNING
            self._watchdog = threading.Thread(
                target=self._watch_process,
                args=(process,),
                daemon=True,
                name=f"wzhk-geometry-browser-{self.session_id[:8]}",
            )
            self._watchdog.start()
            self._condition.notify_all()
            return self.status_snapshot()

    def probe_capability(self, timeout_seconds: float = 15.0) -> BrowserCapabilityReport:
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise BrowserRuntimeError("The browser capability timeout is invalid.")
        try:
            self.start_browser()
        except BrowserUnavailableError:
            capability = self.capability_snapshot()
            return capability or BrowserCapabilityReport.browser_unavailable("The browser is unavailable.")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            probe_started = False
            while time.monotonic() < deadline:
                capability = self._capability
                if capability is not None:
                    if capability.state not in {
                        BrowserCapabilityState.READY,
                        BrowserCapabilityState.PERFORMANCE_INSUFFICIENT,
                    }:
                        return capability
                    if capability.performance_measured and self._telemetry_samples >= 3:
                        return capability
                    if not probe_started:
                        self.update_control(
                            BrowserControlCommand.RUN,
                            timeline_seconds=0.0,
                            audio_response_enabled=False,
                        )
                        probe_started = True
                self._condition.wait(max(0.0, deadline - time.monotonic()))
        return BrowserCapabilityReport.browser_unavailable(
            "The browser runtime did not complete an authenticated measured-performance probe; "
            "WebGL performance remains unconfirmed and READY was not reported."
        )

    def accept_event(self, payload: dict[str, object]) -> BrowserRuntimeEventState:
        payload = self._unwrap_event(payload)
        allowed = {"state", "capability", "telemetry", "error"}
        if set(payload) - allowed:
            raise BrowserRuntimeProtocolError(HTTPStatus.UNPROCESSABLE_ENTITY, "The event has unknown fields.")
        try:
            event_state = BrowserRuntimeEventState(str(payload.get("state", "")))
        except ValueError as exc:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The runtime event state is invalid."
            ) from exc

        with self._condition:
            if self._phase in {
                BrowserRuntimePhase.CLOSED,
                BrowserRuntimePhase.ENDED,
                BrowserRuntimePhase.ERROR,
            }:
                raise BrowserRuntimeProtocolError(HTTPStatus.CONFLICT, "The runtime session is terminal.")
            if event_state is BrowserRuntimeEventState.READY:
                if set(payload) != {"state", "capability"}:
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "A ready event requires only capability evidence."
                    )
                capability_payload = payload.get("capability")
                if not isinstance(capability_payload, dict):
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "Capability evidence is required."
                    )
                if self._phase not in {
                    BrowserRuntimePhase.SERVER_READY,
                    BrowserRuntimePhase.BROWSER_RUNNING,
                }:
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.CONFLICT,
                        "A ready event is accepted only from an active loopback runtime.",
                    )
                capability = self._parse_capability(cast(dict[str, object], capability_payload))
                self._capability = capability
                if capability.state is BrowserCapabilityState.READY:
                    self._phase = BrowserRuntimePhase.RUNTIME_READY
                    self._error_code = None
                    self._error_message = None
                else:
                    self._phase = BrowserRuntimePhase.ERROR
                    self._error_code = capability.state.value
                    self._error_message = capability.detail
            elif event_state is BrowserRuntimeEventState.STARTED:
                if set(payload) != {"state"}:
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "A started event cannot carry extra data."
                    )
                if self._phase is not BrowserRuntimePhase.RUNTIME_READY:
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.CONFLICT, "The runtime cannot start before a READY capability report."
                    )
                self._phase = BrowserRuntimePhase.STARTED
            elif event_state is BrowserRuntimeEventState.TELEMETRY:
                if set(payload) != {"state", "telemetry"}:
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "A telemetry event requires only telemetry data."
                    )
                if self._phase is not BrowserRuntimePhase.STARTED:
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.CONFLICT, "Telemetry is accepted only while the runtime is started."
                    )
                telemetry_payload = payload.get("telemetry")
                if not isinstance(telemetry_payload, dict):
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "Telemetry data is required."
                    )
                telemetry = self._parse_telemetry(cast(dict[str, object], telemetry_payload))
                self._telemetry = telemetry
                self._performance_samples.append(telemetry)
                self._performance_samples = self._performance_samples[-5:]
                if self._capability is not None:
                    self._capability = self._with_measured_performance(self._capability, telemetry)
            elif event_state is BrowserRuntimeEventState.ENDED:
                if set(payload) != {"state"}:
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "An ended event cannot carry extra data."
                    )
                if self._phase not in {
                    BrowserRuntimePhase.RUNTIME_READY,
                    BrowserRuntimePhase.STARTED,
                }:
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.CONFLICT, "The runtime cannot end before it is ready."
                    )
                self._phase = BrowserRuntimePhase.ENDED
            else:
                if set(payload) not in ({"state", "error"}, {"state", "error", "capability"}):
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "An error event has invalid fields."
                    )
                error_payload = payload.get("error")
                if not isinstance(error_payload, dict):
                    raise BrowserRuntimeProtocolError(
                        HTTPStatus.UNPROCESSABLE_ENTITY, "Structured runtime error data is required."
                    )
                self._record_error(cast(dict[str, object], error_payload), payload.get("capability"))
            self._last_event = event_state
            self._condition.notify_all()
            return event_state

    def update_control(
        self,
        command: BrowserControlCommand,
        *,
        timeline_seconds: float | None = None,
        audio_response_enabled: bool | None = None,
    ) -> BrowserControlSnapshot:
        if not isinstance(command, BrowserControlCommand):
            raise BrowserRuntimeError("The browser runtime control command is invalid.")
        with self._condition:
            current = self._control
            resolved_timeline = current.timeline_seconds
            if timeline_seconds is not None:
                if not math.isfinite(timeline_seconds) or not 0 <= timeline_seconds <= MAX_CONTROL_SECONDS:
                    raise BrowserRuntimeError("The browser runtime timeline position is invalid.")
                resolved_timeline = timeline_seconds
            resolved_audio = (
                current.audio_response_enabled
                if audio_response_enabled is None
                else audio_response_enabled
            )
            self._control = BrowserControlSnapshot(
                revision=current.revision + 1,
                command=command,
                timeline_seconds=resolved_timeline,
                audio_response_enabled=resolved_audio,
            )
            self._condition.notify_all()
            return self._control

    def request_stop(self) -> BrowserControlSnapshot:
        return self.update_control(BrowserControlCommand.STOP)

    def control_snapshot(self) -> BrowserControlSnapshot:
        with self._lock:
            return self._control

    def control_api_payload(self) -> dict[str, object]:
        with self._lock:
            control = self._control
            if self._phase is BrowserRuntimePhase.ENDED or control.command is BrowserControlCommand.STOP:
                state = "ended"
            elif control.revision == 0:
                state = "idle"
            elif control.command is BrowserControlCommand.RUN:
                state = "playing"
            else:
                state = "paused"
            return {
                "state": state,
                "currentSeconds": control.timeline_seconds,
                "revision": control.revision,
            }

    def telemetry_snapshot(self) -> BrowserRuntimeTelemetry | None:
        with self._lock:
            return self._telemetry

    def capability_snapshot(self) -> BrowserCapabilityReport | None:
        with self._lock:
            return self._capability

    def status_snapshot(self) -> BrowserRuntimeStatus:
        with self._lock:
            process = self._process
            return BrowserRuntimeStatus(
                session_id=self.session_id,
                job_id=self.job_id,
                phase=self._phase,
                server_origin=self.server_origin,
                browser_family=self._browser.family if self._browser is not None else None,
                browser_pid=(process.pid if process is not None and process.poll() is None else None),
                last_event=self._last_event,
                capability=self._capability,
                last_runtime_milliseconds=self._last_runtime_milliseconds,
                error_code=self._error_code,
                error_message=self._error_message,
                warning_code=self._warning_code,
                warning_message=self._warning_message,
            )

    def wait_for_phase(
        self,
        phases: BrowserRuntimePhase | set[BrowserRuntimePhase],
        timeout_seconds: float,
    ) -> BrowserRuntimeStatus:
        targets = {phases} if isinstance(phases, BrowserRuntimePhase) else set(phases)
        if not targets or timeout_seconds < 0 or not math.isfinite(timeout_seconds):
            raise BrowserRuntimeError("The browser runtime wait request is invalid.")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while self._phase not in targets and time.monotonic() < deadline:
                self._condition.wait(max(0.0, deadline - time.monotonic()))
            return self.status_snapshot()

    def stop_browser(self, grace_seconds: float = 2.0) -> None:
        if grace_seconds < 0 or not math.isfinite(grace_seconds):
            raise BrowserRuntimeError("The browser shutdown grace period is invalid.")
        with self._condition:
            process = self._process
            if process is None:
                self._cleanup_profile()
                return
            if self._control.command is not BrowserControlCommand.STOP:
                self.request_stop()
            deadline = time.monotonic() + grace_seconds
            while (
                process.poll() is None
                and self._phase not in {BrowserRuntimePhase.ENDED, BrowserRuntimePhase.ERROR}
                and time.monotonic() < deadline
            ):
                self._condition.wait(max(0.0, deadline - time.monotonic()))
            self._closing = True
        _terminate_owned_process(process)
        watchdog = self._watchdog
        if watchdog is not None:
            watchdog.join(timeout=2)
        with self._condition:
            self._process = None
            self._watchdog = None
            self._condition.notify_all()
        self._cleanup_profile()

    def stop_server(self) -> None:
        with self._condition:
            server = self._server
            thread = self._server_thread
            self._server = None
            self._server_thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)

    def close(self) -> None:
        with self._condition:
            if self._phase is BrowserRuntimePhase.CLOSED:
                return
            self._closing = True
        self.stop_browser()
        self.stop_server()
        with self._condition:
            self._phase = BrowserRuntimePhase.CLOSED
            self._condition.notify_all()
        with suppress(OSError):
            self._session_root.rmdir()

    def __enter__(self) -> BrowserRuntimeSession:
        self.start_server()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _parse_capability(self, payload: dict[str, object]) -> BrowserCapabilityReport:
        allowed = {
            "state",
            "webgl2",
            "gpuRenderer",
            "shaderCompiled",
            "performanceMeasured",
            "rendererFps",
            "averageFrameTimeMs",
            "pointCount",
            "detail",
        }
        if set(payload) - allowed:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "Capability evidence has unknown fields."
            )
        try:
            state = BrowserCapabilityState(str(payload.get("state", "")))
        except ValueError as exc:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The capability state is invalid."
            ) from exc
        webgl2 = payload.get("webgl2")
        shader_compiled = payload.get("shaderCompiled")
        performance_measured = payload.get("performanceMeasured")
        if not isinstance(webgl2, bool) or not isinstance(shader_compiled, bool):
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "WebGL and shader evidence must be boolean."
            )
        if not isinstance(performance_measured, bool):
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "Performance evidence must identify whether it was measured."
            )
        renderer_raw = payload.get("gpuRenderer")
        gpu_renderer = None
        if renderer_raw is not None:
            if not isinstance(renderer_raw, str) or not 1 <= len(renderer_raw) <= 240:
                raise BrowserRuntimeProtocolError(
                    HTTPStatus.UNPROCESSABLE_ENTITY, "The GPU renderer description is invalid."
                )
            gpu_renderer = renderer_raw
        detail_raw = payload.get("detail", "")
        if not isinstance(detail_raw, str) or len(detail_raw) > 500:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The capability detail is invalid."
            )
        point_count = None
        if payload.get("pointCount") is not None:
            point_count = _bounded_int(payload["pointCount"], "pointCount", minimum=1, maximum=1_000_000)
        renderer_fps = None
        average_frame_time_ms = None
        performance_sufficient = None
        if performance_measured:
            renderer_fps = _finite_number(
                payload.get("rendererFps"), "rendererFps", minimum=0, maximum=1000
            )
            average_frame_time_ms = _finite_number(
                payload.get("averageFrameTimeMs"),
                "averageFrameTimeMs",
                minimum=0,
                maximum=10_000,
            )
            performance_sufficient = (
                renderer_fps >= self.minimum_renderer_fps
                and average_frame_time_ms <= self.maximum_average_frame_time_ms
            )
        elif payload.get("rendererFps") is not None or payload.get("averageFrameTimeMs") is not None:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "Unmeasured performance cannot include renderer FPS or frame time.",
            )

        renderer_is_software = gpu_renderer is not None and any(
            marker in gpu_renderer.casefold() for marker in _SOFTWARE_RENDERER_MARKERS
        )
        if state is BrowserCapabilityState.READY:
            if not webgl2:
                state = BrowserCapabilityState.WEBGL2_UNAVAILABLE
            elif not gpu_renderer or renderer_is_software:
                state = BrowserCapabilityState.GPU_RENDERER_UNAVAILABLE
            elif not shader_compiled:
                state = BrowserCapabilityState.SHADER_COMPILE_FAILED
            elif performance_sufficient is False:
                state = BrowserCapabilityState.PERFORMANCE_INSUFFICIENT
        if state is BrowserCapabilityState.WEBGL2_UNAVAILABLE and webgl2:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "WEBGL2_UNAVAILABLE conflicts with the supplied evidence."
            )
        if state is BrowserCapabilityState.SHADER_COMPILE_FAILED and shader_compiled:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "SHADER_COMPILE_FAILED conflicts with the supplied evidence."
            )
        if state is BrowserCapabilityState.PERFORMANCE_INSUFFICIENT and performance_sufficient is not False:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "PERFORMANCE_INSUFFICIENT requires measured insufficient performance.",
            )
        if state is BrowserCapabilityState.BROWSER_UNAVAILABLE:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "BROWSER_UNAVAILABLE is host-generated and cannot be asserted by page script.",
            )
        if state is BrowserCapabilityState.READY and (not webgl2 or not shader_compiled or not gpu_renderer):
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "READY capability evidence is incomplete."
            )
        return BrowserCapabilityReport(
            state=state,
            webgl2=webgl2,
            gpu_renderer=gpu_renderer,
            shader_compiled=shader_compiled,
            performance_measured=performance_measured,
            performance_sufficient=performance_sufficient,
            renderer_fps=renderer_fps,
            average_frame_time_ms=average_frame_time_ms,
            point_count=point_count,
            detail=(detail_raw or "Capability evidence received from the authenticated local runtime."),
        )

    def _with_measured_performance(
        self,
        capability: BrowserCapabilityReport,
        telemetry: BrowserRuntimeTelemetry,
    ) -> BrowserCapabilityReport:
        if capability.state not in {
            BrowserCapabilityState.READY,
            BrowserCapabilityState.PERFORMANCE_INSUFFICIENT,
        }:
            return capability
        gpu_renderer = telemetry.gpu_renderer or capability.gpu_renderer
        samples = self._performance_samples or [telemetry]
        renderer_fps = float(statistics.median(item.renderer_fps for item in samples))
        average_frame_time_ms = float(
            statistics.median(item.average_frame_time_ms for item in samples)
        )
        renderer_is_software = gpu_renderer is not None and any(
            marker in gpu_renderer.casefold() for marker in _SOFTWARE_RENDERER_MARKERS
        )
        canvas_scale_x = (
            telemetry.canvas_width / self.width
            if telemetry.canvas_width is not None
            else 0.0
        )
        canvas_scale_y = (
            telemetry.canvas_height / self.height
            if telemetry.canvas_height is not None
            else 0.0
        )
        canvas_matches = (
            (
                1.0 <= canvas_scale_x <= 2.5
                and 1.0 <= canvas_scale_y <= 2.5
                and math.isclose(canvas_scale_x, canvas_scale_y, abs_tol=0.01)
            )
            if self.fullscreen
            else (
                telemetry.canvas_width is not None
                and telemetry.canvas_height is not None
                and telemetry.canvas_width >= 1280
                and telemetry.canvas_height >= 720
            )
        )
        sufficient = (
            renderer_fps >= self.minimum_renderer_fps
            and average_frame_time_ms <= self.maximum_average_frame_time_ms
            and canvas_matches
        )
        if not gpu_renderer or renderer_is_software:
            state = BrowserCapabilityState.GPU_RENDERER_UNAVAILABLE
            sufficient = False
        else:
            state = (
                BrowserCapabilityState.READY
                if sufficient
                else BrowserCapabilityState.PERFORMANCE_INSUFFICIENT
            )
        return BrowserCapabilityReport(
            state=state,
            webgl2=capability.webgl2,
            gpu_renderer=gpu_renderer,
            shader_compiled=capability.shader_compiled,
            performance_measured=True,
            performance_sufficient=sufficient,
            renderer_fps=renderer_fps,
            average_frame_time_ms=average_frame_time_ms,
            point_count=telemetry.point_count,
            detail=(
                f"Median of {len(samples)} measured renderer samples satisfied the configured capture threshold."
                if state is BrowserCapabilityState.READY
                else f"Median of {len(samples)} renderer samples, GPU identity, or canvas bounds did not satisfy the configured capture threshold."
            ),
        )

    def _unwrap_event(self, event: dict[str, object]) -> dict[str, object]:
        wrapper_fields = {
            "type",
            "rendererId",
            "jobId",
            "runtimeVersion",
            "runtimeMilliseconds",
            "payload",
        }
        if not wrapper_fields.intersection(event):
            return event
        if set(event) != wrapper_fields:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The runtime event envelope is invalid."
            )
        if event.get("rendererId") != self.renderer_id:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.CONFLICT, "The runtime event renderer identity does not match this session."
            )
        if event.get("jobId") != self.job_id:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.CONFLICT, "The runtime event job identity does not match this session."
            )
        if event.get("runtimeVersion") != self.runtime_version:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.CONFLICT, "The runtime event version does not match this session."
            )
        runtime_milliseconds = _bounded_int(
            event.get("runtimeMilliseconds"),
            "runtimeMilliseconds",
            minimum=0,
            maximum=30 * 24 * 60 * 60 * 1000,
        )
        with self._lock:
            self._last_runtime_milliseconds = runtime_milliseconds
        try:
            state = BrowserRuntimeEventState(str(event.get("type", "")))
        except ValueError as exc:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The runtime event type is invalid."
            ) from exc
        wrapped_payload = event.get("payload")
        if not isinstance(wrapped_payload, dict) or not all(
            isinstance(key, str) for key in wrapped_payload
        ):
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The runtime event payload must be an object."
            )
        body = cast(dict[str, object], wrapped_payload)
        if state is BrowserRuntimeEventState.READY:
            capability = (
                self._normalize_runtime_ready_payload(body)
                if "capabilities" in body
                else body.get("capability", body)
            )
            return {"state": state.value, "capability": capability}
        if state is BrowserRuntimeEventState.TELEMETRY:
            telemetry = body.get("telemetry", body)
            return {"state": state.value, "telemetry": telemetry}
        if state is BrowserRuntimeEventState.ERROR:
            error = body.get("error", body)
            normalized: dict[str, object] = {"state": state.value, "error": error}
            if "capability" in body:
                normalized["capability"] = body["capability"]
            return normalized
        if state is BrowserRuntimeEventState.STARTED:
            if set(body) != {"timelineSeconds", "controlRevision"}:
                raise BrowserRuntimeProtocolError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "The started runtime payload is invalid.",
                )
            _finite_number(
                body["timelineSeconds"],
                "timelineSeconds",
                minimum=0,
                maximum=MAX_CONTROL_SECONDS,
            )
            revision = body["controlRevision"]
            if not (
                isinstance(revision, (str, int))
                and not isinstance(revision, bool)
                and 1 <= len(str(revision)) <= 120
            ):
                raise BrowserRuntimeProtocolError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "The started control revision is invalid.",
                )
            return {"state": state.value}
        if state is BrowserRuntimeEventState.ENDED:
            if set(body) != {"timelineSeconds", "reason"}:
                raise BrowserRuntimeProtocolError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "The ended runtime payload is invalid.",
                )
            _finite_number(
                body["timelineSeconds"],
                "timelineSeconds",
                minimum=0,
                maximum=MAX_CONTROL_SECONDS,
            )
            reason = body["reason"]
            if not isinstance(reason, str) or _IDENTIFIER.fullmatch(reason) is None:
                raise BrowserRuntimeProtocolError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "The ended runtime reason is invalid.",
                )
            return {"state": state.value}
        if body:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                f"The {state.value} runtime event payload must be empty.",
            )
        return {"state": state.value}

    def _normalize_runtime_ready_payload(self, payload: dict[str, object]) -> dict[str, object]:
        required = {
            "mode",
            "designJobId",
            "sessionIdentitySource",
            "pointCount",
            "pointDomain",
            "targetFps",
            "trustedShapes",
            "capabilities",
        }
        if set(payload) != required:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "The ready runtime payload is missing fields or contains unknown fields.",
            )
        if payload["mode"] not in {"preview", "production"}:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The ready runtime mode is invalid."
            )
        design_job_id = payload["designJobId"]
        if not isinstance(design_job_id, str) or _UUID.fullmatch(design_job_id) is None:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The deterministic design job identity is invalid."
            )
        if payload["sessionIdentitySource"] not in {
            "sessionJobId-query",
            "control-api",
            "standalone-config-fallback",
        }:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The session identity source is invalid."
            )
        point_count = _bounded_int(
            payload["pointCount"], "pointCount", minimum=128, maximum=65_536
        )
        target_fps = _bounded_int(payload["targetFps"], "targetFps", minimum=24, maximum=120)
        point_domain = payload["pointDomain"]
        if not isinstance(point_domain, dict) or set(point_domain) != {"width", "height"}:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The ready point domain is invalid."
            )
        domain = cast(dict[str, object], point_domain)
        domain_width = _bounded_int(domain["width"], "pointDomain.width", minimum=1, maximum=1024)
        domain_height = _bounded_int(
            domain["height"], "pointDomain.height", minimum=1, maximum=1024
        )
        if domain_width * domain_height < point_count:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The ready point domain cannot address every point."
            )
        trusted_shapes = payload["trustedShapes"]
        if not isinstance(trusted_shapes, list) or not 1 <= len(trusted_shapes) <= 64:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The trusted shape list is invalid."
            )
        if any(
            not isinstance(shape, str) or _IDENTIFIER.fullmatch(shape) is None
            for shape in trusted_shapes
        ) or len(set(cast(list[str], trusted_shapes))) != len(trusted_shapes):
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The trusted shape list is invalid or duplicated."
            )
        capabilities = payload["capabilities"]
        capability_fields = {
            "webglVersion",
            "shadingLanguageVersion",
            "vendor",
            "renderer",
            "maxPointSize",
            "canvasWidth",
            "canvasHeight",
        }
        if not isinstance(capabilities, dict) or set(capabilities) != capability_fields:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The WebGL capability payload is invalid."
            )
        report = cast(dict[str, object], capabilities)
        strings: dict[str, str] = {}
        for name, maximum in (
            ("webglVersion", 160),
            ("shadingLanguageVersion", 160),
            ("vendor", 160),
            ("renderer", 240),
        ):
            value = report[name]
            if not isinstance(value, str) or not 1 <= len(value) <= maximum:
                raise BrowserRuntimeProtocolError(
                    HTTPStatus.UNPROCESSABLE_ENTITY, f"The {name} capability is invalid."
                )
            strings[name] = value
        _finite_number(report["maxPointSize"], "maxPointSize", minimum=1, maximum=1_000_000)
        canvas_width = _bounded_int(
            report["canvasWidth"], "canvasWidth", minimum=1, maximum=7680
        )
        canvas_height = _bounded_int(
            report["canvasHeight"], "canvasHeight", minimum=1, maximum=4320
        )
        webgl2 = "webgl 2" in strings["webglVersion"].casefold()
        return {
            "state": (
                BrowserCapabilityState.READY.value
                if webgl2
                else BrowserCapabilityState.WEBGL2_UNAVAILABLE.value
            ),
            "webgl2": webgl2,
            "gpuRenderer": strings["renderer"],
            # The trusted runtime emits ready only after built-in shader compilation/linking
            # and an initial draw have succeeded.
            "shaderCompiled": True,
            "performanceMeasured": False,
            "pointCount": point_count,
            "detail": (
                f"WebGL runtime initialized at {canvas_width}x{canvas_height} with target "
                f"{target_fps} FPS; measured performance is pending."
            ),
        }

    def _parse_telemetry(self, payload: dict[str, object]) -> BrowserRuntimeTelemetry:
        allowed = {
            "timelineSeconds",
            "section",
            "sourceShape",
            "targetShape",
            "morph",
            "rendererFps",
            "averageFrameIntervalMs",
            "averageFrameTimeMs",
            "p95FrameTimeMs",
            "averageRenderTimeMs",
            "maximumRenderTimeMs",
            "renderedFrames",
            "droppedRendererUpdates",
            "droppedRendererFrames",
            "droppedFrames",
            "targetFps",
            "pointCount",
            "canvasWidth",
            "canvasHeight",
            "gpuRenderer",
        }
        required = {"rendererFps", "pointCount"}
        frame_interval_keys = {
            key for key in ("averageFrameIntervalMs", "averageFrameTimeMs") if key in payload
        }
        if set(payload) - allowed or not required.issubset(payload) or len(frame_interval_keys) != 1:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "Telemetry fields are missing or unknown."
            )
        self._telemetry_samples += 1

        def optional_number(name: str, maximum: float = 10_000) -> float | None:
            value = payload.get(name)
            return (
                None
                if value is None
                else _finite_number(value, name, minimum=0, maximum=maximum)
            )

        def optional_label(name: str) -> str | None:
            value = payload.get(name)
            if value is None:
                return None
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise BrowserRuntimeProtocolError(
                    HTTPStatus.UNPROCESSABLE_ENTITY, f"The telemetry {name} value is invalid."
                )
            return value

        p95 = None
        if payload.get("p95FrameTimeMs") is not None:
            p95 = _finite_number(
                payload["p95FrameTimeMs"], "p95FrameTimeMs", minimum=0, maximum=10_000
            )
        dropped_keys = [
            key
            for key in ("droppedRendererUpdates", "droppedRendererFrames", "droppedFrames")
            if payload.get(key) is not None
        ]
        if len(dropped_keys) > 1:
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "Telemetry supplied conflicting dropped-frame fields."
            )
        dropped = None
        if dropped_keys:
            dropped_key = dropped_keys[0]
            dropped = _bounded_int(
                payload[dropped_key], dropped_key, minimum=0, maximum=1_000_000_000
            )
        rendered_frames = None
        if payload.get("renderedFrames") is not None:
            rendered_frames = _bounded_int(
                payload["renderedFrames"],
                "renderedFrames",
                minimum=0,
                maximum=1_000_000_000_000,
            )
        canvas_width = None
        canvas_height = None
        if ("canvasWidth" in payload) != ("canvasHeight" in payload):
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "Telemetry canvas dimensions must be supplied together."
            )
        if payload.get("canvasWidth") is not None and payload.get("canvasHeight") is not None:
            canvas_width = _bounded_int(
                payload["canvasWidth"], "canvasWidth", minimum=1, maximum=7680
            )
            canvas_height = _bounded_int(
                payload["canvasHeight"], "canvasHeight", minimum=1, maximum=4320
            )
        gpu_renderer = payload.get("gpuRenderer")
        if gpu_renderer is not None and (
            not isinstance(gpu_renderer, str) or not 1 <= len(gpu_renderer) <= 240
        ):
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The telemetry GPU renderer is invalid."
            )
        frame_interval_key = next(iter(frame_interval_keys))
        return BrowserRuntimeTelemetry(
            sample_count=self._telemetry_samples,
            timeline_seconds=optional_number("timelineSeconds", MAX_CONTROL_SECONDS),
            section=optional_label("section"),
            source_shape=optional_label("sourceShape"),
            target_shape=optional_label("targetShape"),
            morph=optional_number("morph", 1),
            renderer_fps=_finite_number(
                payload["rendererFps"], "rendererFps", minimum=0, maximum=1000
            ),
            average_frame_time_ms=_finite_number(
                payload[frame_interval_key],
                frame_interval_key,
                minimum=0,
                maximum=10_000,
            ),
            p95_frame_time_ms=p95,
            average_render_time_ms=optional_number("averageRenderTimeMs"),
            maximum_render_time_ms=optional_number("maximumRenderTimeMs"),
            rendered_frames=rendered_frames,
            dropped_frames=dropped,
            target_fps=optional_number("targetFps", 240),
            point_count=_bounded_int(
                payload["pointCount"], "pointCount", minimum=1, maximum=1_000_000
            ),
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            gpu_renderer=gpu_renderer,
            measured_at_monotonic_seconds=time.monotonic(),
        )

    def _record_error(self, payload: dict[str, object], capability_payload: object) -> None:
        allowed = {"code", "message", "detail", "recoverable"}
        if set(payload) - allowed or not {"code", "message"}.issubset(payload):
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The runtime error fields are invalid."
            )
        code = payload.get("code")
        message = payload.get("message")
        if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
            raise BrowserRuntimeProtocolError(HTTPStatus.UNPROCESSABLE_ENTITY, "The error code is invalid.")
        if not isinstance(message, str) or not 1 <= len(message) <= 500:
            raise BrowserRuntimeProtocolError(HTTPStatus.UNPROCESSABLE_ENTITY, "The error message is invalid.")
        detail = payload.get("detail")
        if detail is not None and (not isinstance(detail, str) or len(detail) > 1600):
            raise BrowserRuntimeProtocolError(HTTPStatus.UNPROCESSABLE_ENTITY, "The error detail is invalid.")
        recoverable = payload.get("recoverable", False)
        if not isinstance(recoverable, bool):
            raise BrowserRuntimeProtocolError(
                HTTPStatus.UNPROCESSABLE_ENTITY, "The recoverable error flag is invalid."
            )
        if capability_payload is not None:
            if not isinstance(capability_payload, dict):
                raise BrowserRuntimeProtocolError(
                    HTTPStatus.UNPROCESSABLE_ENTITY, "The error capability evidence is invalid."
                )
            self._capability = self._parse_capability(cast(dict[str, object], capability_payload))
        if recoverable:
            self._warning_code = code
            self._warning_message = message
            return
        mapped_state = {
            "WEBGL2_UNAVAILABLE": BrowserCapabilityState.WEBGL2_UNAVAILABLE,
            "GPU_RENDERER_UNAVAILABLE": BrowserCapabilityState.GPU_RENDERER_UNAVAILABLE,
            "GPU_RENDER_ERROR": BrowserCapabilityState.GPU_RENDERER_UNAVAILABLE,
            "GPU_CONTEXT_LOST": BrowserCapabilityState.GPU_RENDERER_UNAVAILABLE,
            "SHADER_COMPILE_FAILED": BrowserCapabilityState.SHADER_COMPILE_FAILED,
            "SHADER_LINK_FAILED": BrowserCapabilityState.SHADER_COMPILE_FAILED,
        }.get(code)
        if mapped_state is not None:
            self._capability = BrowserCapabilityReport(
                state=mapped_state,
                webgl2=(False if mapped_state is BrowserCapabilityState.WEBGL2_UNAVAILABLE else True),
                gpu_renderer=(self._capability.gpu_renderer if self._capability is not None else None),
                shader_compiled=(False if mapped_state is BrowserCapabilityState.SHADER_COMPILE_FAILED else None),
                performance_measured=False,
                performance_sufficient=None,
                renderer_fps=None,
                average_frame_time_ms=None,
                point_count=(self._capability.point_count if self._capability is not None else None),
                detail=message,
            )
        else:
            self._capability = BrowserCapabilityReport.browser_unavailable(
                f"The browser runtime failed before a usable measured capability result: {message}"
            )
        self._phase = BrowserRuntimePhase.ERROR
        self._error_code = code
        self._error_message = message

    def _watch_process(self, process: BrowserProcess) -> None:
        return_code = process.wait()
        with self._condition:
            if process is self._process and not self._closing and self._phase not in {
                BrowserRuntimePhase.ENDED,
                BrowserRuntimePhase.ERROR,
                BrowserRuntimePhase.CLOSED,
            }:
                self._phase = BrowserRuntimePhase.ERROR
                self._error_code = "BROWSER_EXITED"
                self._error_message = f"The owned browser exited before the runtime ended (code {return_code})."
                self._condition.notify_all()

    def _write_profile_marker(self) -> None:
        marker = self._profile_root / ".trackprompt-browser-profile.json"
        if marker.exists():
            try:
                existing = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BrowserRuntimeError("The browser profile marker is invalid.") from exc
            if existing.get("sessionId") != self.session_id:
                raise BrowserRuntimeError("The browser profile is owned by another session.")
            return
        marker.write_text(
            json.dumps(
                {"schemaVersion": "1.0.0", "sessionId": self.session_id},
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def _cleanup_profile(self) -> None:
        profile = self._profile_root
        if not profile.exists():
            return
        try:
            resolved_session = self._session_root.resolve(strict=True)
            resolved_profile = profile.resolve(strict=True)
            resolved_profile.relative_to(resolved_session)
            if _is_link(profile):
                return
            marker = json.loads(
                (profile / ".trackprompt-browser-profile.json").read_text(encoding="utf-8")
            )
            if marker.get("sessionId") != self.session_id:
                return
        except (OSError, ValueError, json.JSONDecodeError):
            return

        def clear_read_only_and_retry(function: Callable[[str], object], path: str, _error: object) -> None:
            os.chmod(path, 0o700)
            function(path)

        with suppress(OSError):
            shutil.rmtree(profile, onerror=clear_read_only_and_retry)


def _terminate_owned_process(process: BrowserProcess) -> None:
    if process.poll() is not None:
        return
    with suppress(OSError):
        process.terminate()
    try:
        process.wait(timeout=2)
        return
    except (OSError, subprocess.TimeoutExpired, TimeoutError):
        pass
    if os.name == "nt":
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=5,
                check=False,
            )
    else:
        get_process_group = getattr(os, "getpgid", None)
        kill_process_group = getattr(os, "killpg", None)
        kill_signal = int(getattr(signal, "SIGKILL", 9))
        with suppress(OSError):
            if callable(get_process_group) and callable(kill_process_group):
                kill_process_group(get_process_group(process.pid), kill_signal)
    if process.poll() is None:
        with suppress(OSError):
            process.kill()
    with suppress(OSError, subprocess.TimeoutExpired, TimeoutError):
        process.wait(timeout=2)

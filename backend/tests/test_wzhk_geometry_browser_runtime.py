from __future__ import annotations

import http.client
import json
import os
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from app.renderers.wzhk_spectrum.generative.browser_runtime import (
    AUDIO_ROUTE,
    CONFIG_ALIAS_ROUTE,
    CONFIG_ROUTE,
    ENTRYPOINT_ROUTE,
    LOGO_ROUTE,
    MAX_EVENT_BYTES,
    BrowserCapabilityReport,
    BrowserCapabilityState,
    BrowserControlCommand,
    BrowserFamily,
    BrowserInstallation,
    BrowserRuntimeError,
    BrowserRuntimeEventState,
    BrowserRuntimePhase,
    BrowserRuntimeResources,
    BrowserRuntimeSession,
    discover_browser,
)

TOKEN = "test-runtime-token-0123456789-abcdef"


def _workspace(tmp_path: Path) -> tuple[Path, BrowserRuntimeResources]:
    job_root = tmp_path / "8c45eb06-a9d6-4d31-b8ef-cd184eb71362"
    runtime_root = job_root / "generative"
    runtime_root.mkdir(parents=True)
    entrypoint = runtime_root / "index.html"
    config = runtime_root / "runtime-config.json"
    logo = runtime_root / "logo.png"
    audio = runtime_root / "master.wav"
    module = runtime_root / "runtime.js"
    shader = runtime_root / "points.vert"
    entrypoint.write_text("<!doctype html><title>WZHK Geometry Runtime</title>", encoding="utf-8")
    config.write_text('{"seed":84291}', encoding="utf-8")
    logo.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    audio.write_bytes(b"0123456789abcdef")
    module.write_text("export const runtime = true;", encoding="utf-8")
    shader.write_text("#version 300 es\nvoid main(){}", encoding="utf-8")
    return job_root, BrowserRuntimeResources(
        entrypoint=entrypoint,
        config=config,
        logo=logo,
        audio=audio,
        runtime_files={
            "/runtime/runtime.js": module,
            "/runtime/points.vert": shader,
        },
    )


def _session(
    tmp_path: Path,
    **kwargs: object,
) -> BrowserRuntimeSession:
    job_root, resources = _workspace(tmp_path)
    return BrowserRuntimeSession(
        job_root,
        resources,
        token_factory=lambda: TOKEN,
        **kwargs,
    )


def _request(
    session: BrowserRuntimeSession,
    method: str,
    path: str,
    *,
    payload: Mapping[str, object] | None = None,
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, Mapping[str, str], bytes]:
    origin = session.start_server()
    port = int(origin.rsplit(":", 1)[1])
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    response_body = response.read()
    response_headers = {key: value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, response_headers, response_body


def _envelope(
    session: BrowserRuntimeSession,
    event_type: BrowserRuntimeEventState,
    payload: Mapping[str, object],
    *,
    job_id: str | None = None,
) -> dict[str, object]:
    return {
        "type": event_type.value,
        "rendererId": session.renderer_id,
        "jobId": job_id or session.job_id,
        "runtimeVersion": session.runtime_version,
        "runtimeMilliseconds": 1234,
        "payload": dict(payload),
    }


def _ready_payload(
    *,
    renderer: str = "ANGLE (NVIDIA GeForce RTX 3060)",
) -> dict[str, object]:
    return {
        "mode": "production",
        "designJobId": "bbf153c1-a38e-54a4-9a7f-960328ce858f",
        "sessionIdentitySource": "sessionJobId-query",
        "pointCount": 4096,
        "pointDomain": {"width": 64, "height": 64},
        "targetFps": 60,
        "trustedShapes": ["torus", "wave-surface", "spherical-lattice"],
        "capabilities": {
            "webglVersion": "WebGL 2.0 (OpenGL ES 3.0 Chromium)",
            "shadingLanguageVersion": "WebGL GLSL ES 3.00",
            "vendor": "Google Inc. (NVIDIA)",
            "renderer": renderer,
            "maxPointSize": 1024,
            "canvasWidth": 1920,
            "canvasHeight": 1080,
        },
    }


def test_loopback_server_serves_only_authenticated_explicit_resources_and_ranges(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    try:
        status, headers, _body = _request(session, "GET", ENTRYPOINT_ROUTE)
        assert status == 403
        assert "Set-Cookie" not in headers

        status, headers, body = _request(session, "GET", f"{ENTRYPOINT_ROUTE}?token={TOKEN}")
        assert status == 200
        assert b"WZHK Geometry Runtime" in body
        assert "HttpOnly" in headers["Set-Cookie"]
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'self'" in headers["Content-Security-Policy"]

        authenticated = {"X-TrackPrompt-Runtime-Token": TOKEN}
        for route, expected in (
            (CONFIG_ROUTE, b"84291"),
            (CONFIG_ALIAS_ROUTE, b"84291"),
            (LOGO_ROUTE, b"PNG"),
            ("/runtime/runtime.js", b"runtime"),
            ("/runtime/points.vert", b"#version"),
        ):
            status, _headers, body = _request(
                session,
                "GET",
                route,
                headers=authenticated,
            )
            assert status == 200
            assert expected in body

        status, headers, body = _request(
            session,
            "GET",
            AUDIO_ROUTE,
            headers={**authenticated, "Range": "bytes=4-7"},
        )
        assert status == 206
        assert headers["Content-Range"] == "bytes 4-7/16"
        assert body == b"4567"

        for route in ("/generative/secret.txt", "/runtime/%2e%2e/secret.txt", "/runtime/"):
            status, _headers, _body = _request(session, "GET", route, headers=authenticated)
            assert status == 404
    finally:
        session.close()


def test_resource_allowlist_rejects_files_outside_the_job(tmp_path: Path) -> None:
    job_root, resources = _workspace(tmp_path)
    outside = tmp_path / "outside.js"
    outside.write_text("private", encoding="utf-8")
    unsafe = BrowserRuntimeResources(
        entrypoint=resources.entrypoint,
        config=resources.config,
        logo=resources.logo,
        audio=resources.audio,
        runtime_files={"/runtime/outside.js": outside},
    )
    with pytest.raises(BrowserRuntimeError, match="escaped"):
        BrowserRuntimeSession(job_root, unsafe, token_factory=lambda: TOKEN)


def test_event_envelope_control_and_runtime_telemetry_are_typed_and_separate(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    authenticated = {"X-TrackPrompt-Runtime-Token": TOKEN}
    try:
        status, _headers, body = _request(
            session,
            "GET",
            "/api/control",
            headers=authenticated,
        )
        assert status == 200
        initial = json.loads(body)
        assert initial | {"state": "idle", "currentSeconds": 0.0, "revision": 0} == initial
        assert initial["jobId"] == session.job_id
        assert "telemetry" not in initial

        session.update_control(
            BrowserControlCommand.RUN,
            timeline_seconds=12.5,
            audio_response_enabled=False,
        )
        status, _headers, body = _request(
            session,
            "GET",
            "/api/control",
            headers=authenticated,
        )
        control = json.loads(body)
        assert control["state"] == "playing"
        assert control["currentSeconds"] == 12.5
        assert control["revision"] == 1
        assert control["control"]["audioResponseEnabled"] is False

        ready = _envelope(
            session,
            BrowserRuntimeEventState.READY,
            _ready_payload(),
        )
        status, _headers, body = _request(
            session,
            "POST",
            "/api/event",
            payload=ready,
            headers=authenticated,
        )
        assert status == 202
        assert json.loads(body)["state"] == "ready"
        assert session.status_snapshot().phase is BrowserRuntimePhase.RUNTIME_READY
        assert session.capability_snapshot() is not None
        assert session.capability_snapshot().performance_measured is False  # type: ignore[union-attr]

        started = _envelope(
            session,
            BrowserRuntimeEventState.STARTED,
            {"timelineSeconds": 0.0, "controlRevision": "1"},
        )
        status, _headers, _body = _request(
            session,
            "POST",
            "/api/event",
            payload=started,
            headers=authenticated,
        )
        assert status == 202

        recoverable_error = _envelope(
            session,
            BrowserRuntimeEventState.ERROR,
            {
                "code": "WEB_AUDIO_UNAVAILABLE",
                "message": "Web Audio response is unavailable.",
                "recoverable": True,
            },
        )
        status, _headers, _body = _request(
            session,
            "POST",
            "/api/event",
            payload=recoverable_error,
            headers=authenticated,
        )
        assert status == 202
        assert session.status_snapshot().phase is BrowserRuntimePhase.STARTED
        assert session.status_snapshot().warning_code == "WEB_AUDIO_UNAVAILABLE"

        telemetry = _envelope(
            session,
            BrowserRuntimeEventState.TELEMETRY,
            {
                "rendererFps": 59.7,
                "averageFrameIntervalMs": 16.4,
                "averageRenderTimeMs": 4.2,
                "maximumRenderTimeMs": 8.7,
                "renderedFrames": 600,
                "droppedRendererFrames": 2,
                "pointCount": 4096,
                "timelineSeconds": 1.0,
                "section": "intro",
                "sourceShape": "torus",
                "targetShape": "wave-surface",
                "morph": 0.25,
                "targetFps": 60,
                "canvasWidth": 1920,
                "canvasHeight": 1080,
                "gpuRenderer": "ANGLE (NVIDIA GeForce RTX 3060)",
            },
        )
        status, _headers, _body = _request(
            session,
            "POST",
            "/api/event",
            payload=telemetry,
            headers=authenticated,
        )
        assert status == 202
        sample = session.telemetry_snapshot()
        assert sample is not None
        assert sample.renderer_fps == 59.7
        assert sample.dropped_frames == 2

        status, _headers, body = _request(
            session,
            "GET",
            "/api/control",
            headers=authenticated,
        )
        assert status == 200
        assert "telemetry" not in json.loads(body)

        ended = _envelope(
            session,
            BrowserRuntimeEventState.ENDED,
            {"timelineSeconds": 1.0, "reason": "control-ended"},
        )
        status, _headers, _body = _request(
            session,
            "POST",
            "/api/event",
            payload=ended,
            headers=authenticated,
        )
        assert status == 202
        waited = session.wait_for_phase(BrowserRuntimePhase.ENDED, 0.1)
        assert waited.phase is BrowserRuntimePhase.ENDED
        assert session.control_api_payload()["state"] == "ended"
    finally:
        session.close()


def test_event_endpoint_rejects_wrong_identity_order_nonfinite_and_oversized_json(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    authenticated = {"X-TrackPrompt-Runtime-Token": TOKEN}
    try:
        wrong_job = _envelope(
            session,
            BrowserRuntimeEventState.READY,
            _ready_payload(),
            job_id="other-job",
        )
        status, _headers, _body = _request(
            session,
            "POST",
            "/api/event",
            payload=wrong_job,
            headers=authenticated,
        )
        assert status == 409

        premature = _envelope(
            session,
            BrowserRuntimeEventState.TELEMETRY,
            {
                "rendererFps": 60,
                "averageFrameIntervalMs": 16,
                "renderedFrames": 1,
                "pointCount": 4096,
            },
        )
        status, _headers, _body = _request(
            session,
            "POST",
            "/api/event",
            payload=premature,
            headers=authenticated,
        )
        assert status == 409

        status, _headers, _body = _request(
            session,
            "POST",
            "/api/event",
            body=b"{}",
            headers={**authenticated, "Content-Type": "text/plain"},
        )
        assert status == 415

        status, _headers, _body = _request(
            session,
            "POST",
            "/api/event",
            body=b'{"state":"telemetry","telemetry":{"rendererFps":NaN}}',
            headers={**authenticated, "Content-Type": "application/json"},
        )
        assert status == 400

        status, _headers, _body = _request(
            session,
            "POST",
            "/api/event",
            body=b" " * (MAX_EVENT_BYTES + 1),
            headers={**authenticated, "Content-Type": "application/json"},
        )
        assert status == 413
    finally:
        session.close()


@pytest.mark.parametrize(
    ("renderer", "measured", "fps", "frame_time", "expected"),
    [
        (
            "Google SwiftShader software rasterizer",
            False,
            None,
            None,
            BrowserCapabilityState.GPU_RENDERER_UNAVAILABLE,
        ),
        (
            "ANGLE (NVIDIA GeForce RTX 3060)",
            True,
            21.0,
            47.0,
            BrowserCapabilityState.PERFORMANCE_INSUFFICIENT,
        ),
    ],
)
def test_capability_probe_classifies_software_gpu_and_only_measured_performance(
    tmp_path: Path,
    renderer: str,
    measured: bool,
    fps: float | None,
    frame_time: float | None,
    expected: BrowserCapabilityState,
) -> None:
    session = _session(tmp_path)
    try:
        session.start_server()
        session.accept_event(
            _envelope(
                session,
                BrowserRuntimeEventState.READY,
                _ready_payload(renderer=renderer),
            )
        )
        if measured:
            assert fps is not None
            assert frame_time is not None
            session.accept_event(
                _envelope(
                    session,
                    BrowserRuntimeEventState.STARTED,
                    {"timelineSeconds": 0.0, "controlRevision": "1"},
                )
            )
            session.accept_event(
                _envelope(
                    session,
                    BrowserRuntimeEventState.TELEMETRY,
                    {
                        "rendererFps": fps,
                        "averageFrameIntervalMs": frame_time,
                        "averageRenderTimeMs": 3.0,
                        "maximumRenderTimeMs": 6.0,
                        "droppedRendererUpdates": 12,
                        "targetFps": 60,
                        "pointCount": 4096,
                        "canvasWidth": 1920,
                        "canvasHeight": 1080,
                    },
                )
            )
        report = session.capability_snapshot()
        assert report is not None
        assert report.state is expected
        assert report.performance_measured is measured
        if not measured:
            assert report.renderer_fps is None
            assert report.performance_sufficient is None
    finally:
        session.close()


def test_browser_discovery_uses_deterministic_family_and_path_order(tmp_path: Path) -> None:
    program_files = tmp_path / "Program Files"
    program_files_x86 = tmp_path / "Program Files (x86)"
    edge = program_files_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    chrome = program_files / "Google" / "Chrome" / "Application" / "chrome.exe"
    edge.parent.mkdir(parents=True)
    chrome.parent.mkdir(parents=True)
    edge.write_bytes(b"edge")
    chrome.write_bytes(b"chrome")
    environment = {
        "PROGRAMFILES": str(program_files),
        "PROGRAMFILES(X86)": str(program_files_x86),
        "LOCALAPPDATA": str(tmp_path / "Local"),
    }
    discovered = discover_browser(environ=environment, which=lambda _name: None)
    assert discovered == BrowserInstallation(BrowserFamily.EDGE, edge.resolve())
    chrome_first = discover_browser(
        preferred=(BrowserFamily.CHROME, BrowserFamily.EDGE),
        environ=environment,
        which=lambda _name: None,
    )
    assert chrome_first == BrowserInstallation(BrowserFamily.CHROME, chrome.resolve())

    unsupported = tmp_path / "browser.exe"
    unsupported.write_bytes(b"unknown")
    with pytest.raises(BrowserRuntimeError, match="not Microsoft Edge or Google Chrome"):
        discover_browser(explicit_path=unsupported)


class _FakeBrowserProcess:
    def __init__(self) -> None:
        self.pid = 43210
        self.terminated = False
        self.killed = False
        self._exited = threading.Event()

    def poll(self) -> int | None:
        return 0 if self._exited.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self._exited.wait(timeout):
            raise subprocess.TimeoutExpired("fake-browser", timeout)
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self._exited.set()

    def kill(self) -> None:
        self.killed = True
        self._exited.set()


def test_session_capability_probe_waits_for_measured_sufficient_telemetry(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"synthetic chrome")
    process = _FakeBrowserProcess()
    reports: list[BrowserCapabilityReport] = []

    def launch(_args: Sequence[str], _working_directory: Path) -> _FakeBrowserProcess:
        return process

    session = _session(
        tmp_path / "probe",
        browser=BrowserInstallation(BrowserFamily.CHROME, executable),
        process_launcher=launch,
    )
    probe = threading.Thread(target=lambda: reports.append(session.probe_capability(2.0)))
    try:
        probe.start()
        assert session.wait_for_phase(BrowserRuntimePhase.BROWSER_RUNNING, 1.0).phase is (
            BrowserRuntimePhase.BROWSER_RUNNING
        )
        session.accept_event(
            _envelope(session, BrowserRuntimeEventState.READY, _ready_payload())
        )
        deadline = time.monotonic() + 1
        while session.control_snapshot().command is not BrowserControlCommand.RUN:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        session.accept_event(
            _envelope(
                session,
                BrowserRuntimeEventState.STARTED,
                {"timelineSeconds": 0.0, "controlRevision": "1"},
            )
        )
        for renderer_fps in (59.8, 58.9, 60.0):
            session.accept_event(
                _envelope(
                    session,
                    BrowserRuntimeEventState.TELEMETRY,
                    {
                        "rendererFps": renderer_fps,
                        "averageFrameIntervalMs": 1000 / renderer_fps,
                        "averageRenderTimeMs": 4.5,
                        "maximumRenderTimeMs": 8.0,
                        "droppedRendererUpdates": 0,
                        "targetFps": 60,
                        "pointCount": 4096,
                        "canvasWidth": 1920,
                        "canvasHeight": 1080,
                    },
                )
            )
        probe.join(timeout=1)
        assert not probe.is_alive()
        assert len(reports) == 1
        assert reports[0].state is BrowserCapabilityState.READY
        assert reports[0].performance_measured is True
        assert reports[0].performance_sufficient is True
        session.stop_browser(grace_seconds=0)
    finally:
        if probe.is_alive():
            probe.join(timeout=2)
        session.close()


@pytest.mark.parametrize(
    ("family", "filename"),
    [(BrowserFamily.EDGE, "msedge.exe"), (BrowserFamily.CHROME, "chrome.exe")],
)
def test_browser_process_uses_argument_array_unique_profile_and_owned_cleanup(
    tmp_path: Path,
    family: BrowserFamily,
    filename: str,
) -> None:
    executable = tmp_path / filename
    executable.write_bytes(b"synthetic browser")
    process = _FakeBrowserProcess()
    launched: list[tuple[tuple[str, ...], Path]] = []

    def launch(args: Sequence[str], working_directory: Path) -> _FakeBrowserProcess:
        launched.append((tuple(args), working_directory))
        return process

    session = _session(
        tmp_path / "workspace",
        browser=BrowserInstallation(family, executable),
        process_launcher=launch,
    )
    try:
        status = session.start_browser()
        assert status.phase is BrowserRuntimePhase.BROWSER_RUNNING
        assert status.browser_pid == process.pid
        assert len(launched) == 1
        command, working_directory = launched[0]
        assert command[0] == str(executable)
        assert any(argument.startswith("--app=http://127.0.0.1:") for argument in command)
        assert any(f"sessionJobId={session.job_id}" in argument for argument in command)
        assert f"--user-data-dir={session.profile_root}" in command
        assert "--disable-background-networking" in command
        assert ("--edge-skip-compat-layer-relaunch" in command) is (
            os.name == "nt" and family is BrowserFamily.EDGE
        )
        assert "--window-size=1920,1080" in command
        assert working_directory == session.profile_root.parent
        assert session.profile_root.is_dir()
        session.stop_browser(grace_seconds=0)
        assert process.terminated
        assert not session.profile_root.exists()
    finally:
        session.close()


def test_probe_reports_browser_unavailable_without_claiming_webgl_or_performance(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path, browser_discovery=lambda: None)
    try:
        report = session.probe_capability(timeout_seconds=0.1)
        assert report.state is BrowserCapabilityState.BROWSER_UNAVAILABLE
        assert report.webgl2 is None
        assert report.performance_measured is False
        assert report.renderer_fps is None
        assert session.status_snapshot().phase is BrowserRuntimePhase.ERROR
    finally:
        session.close()

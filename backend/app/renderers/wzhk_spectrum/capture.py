from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from ...config import Settings
from ...privacy import secure_private_directory, secure_private_file
from ...subprocess_utils import ProcessTimedOut, ProcessWasCancelled, run_process_bounded
from ..schemas import SpectrumWorkspaceJob
from .generative.browser_runtime import (
    BrowserCapabilityReport,
    BrowserControlCommand,
    BrowserFamily,
    BrowserRuntimeError,
    BrowserRuntimePhase,
    BrowserRuntimeResources,
    BrowserRuntimeSession,
    discover_browser,
)
from .preflight import (
    SpectrumInspection,
    SpectrumPaths,
    ensure_within,
    inspect_wzhk_spectrum,
    sha256_file,
)
from .production import (
    CAPTURE_FILENAME,
    CapturePreflightResult,
    CaptureProviderCapabilities,
    CaptureSynchronization,
    GeometryCapabilityEvidence,
    GeometryRuntimeTelemetry,
    SpectrumArtifact,
    SpectrumArtifactType,
    SpectrumMasterTiming,
    SpectrumProductionAvailability,
    SpectrumProductionError,
    SpectrumProductionState,
    SpectrumValidationReport,
    build_capture_command,
    build_chroma_composite_capture_command,
    build_composite_capture_command,
    build_monitor_capture_command,
    build_mux_command,
    build_playback_command,
    build_review_frame_command,
    probe_media_file,
    review_frame_timestamps,
    select_output_filename,
    validate_final_media,
    validate_production_transition,
)
from .workspace import SpectrumWorkspaceError, load_workspace_job


class SpectrumProductionCancelled(SpectrumProductionError):
    pass


StateCallback = Callable[[SpectrumProductionState, dict[str, Any] | None], None]


@dataclass(frozen=True, slots=True)
class SpectrumProductionResult:
    synchronization: CaptureSynchronization
    artifacts: list[SpectrumArtifact]
    validation: SpectrumValidationReport
    provider: str
    encoder: str
    captured_frames: int | None
    dropped_frames: int | None
    capture_duration_seconds: float
    geometry_capability: GeometryCapabilityEvidence | None = None
    geometry_telemetry: GeometryRuntimeTelemetry | None = None


class SpectrumProductionExecutor(Protocol):
    def run(
        self,
        job_root: Path,
        manifest: dict[str, Any],
        cancel_event: threading.Event,
        transition: StateCallback,
    ) -> SpectrumProductionResult: ...


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(_json_bytes(value))
    secure_private_file(temporary)
    os.replace(temporary, path)
    secure_private_file(path)


def _atomic_clock(path: Path, seconds: float) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(f"{seconds:.6f}\n", encoding="ascii", newline="\n")
    os.replace(temporary, path)


def _read_manifest(job_root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((job_root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpectrumWorkspaceError("The Spectrum production manifest is invalid.") from exc
    if not isinstance(payload, dict):
        raise SpectrumWorkspaceError("The Spectrum production manifest is invalid.")
    return payload


def _artifact(
    job_root: Path,
    path: Path,
    artifact_type: SpectrumArtifactType,
    state: SpectrumProductionState,
    provenance: str,
    *,
    timestamp_seconds: float | None = None,
) -> SpectrumArtifact:
    try:
        relative_path = path.resolve(strict=True).relative_to(job_root.resolve()).as_posix()
        size_bytes = path.stat().st_size
    except (OSError, ValueError) as exc:
        raise SpectrumProductionError("A production artifact escaped or became unavailable.") from exc
    if size_bytes <= 0:
        raise SpectrumProductionError("A production artifact is empty.")
    return SpectrumArtifact(
        artifact_type=artifact_type,
        relative_path=relative_path,
        sha256=sha256_file(path),
        size_bytes=size_bytes,
        created_state=state,
        provenance=provenance,
        timestamp_seconds=timestamp_seconds,
    )


def _stop_owned_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    with suppress(OSError):
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def _wait_owned_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    cancel_event: threading.Event,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None:
        if cancel_event.is_set():
            _stop_owned_process(process)
            raise SpectrumProductionCancelled("Spectrum production was cancelled by the operator.")
        if time.monotonic() >= deadline:
            _stop_owned_process(process)
            raise SpectrumProductionError("The owned production process timed out.")
        time.sleep(0.05)
    return int(process.returncode)


def _rainmeter_skin_path() -> Path | None:
    app_data = os.environ.get("APPDATA")
    if not app_data:
        return None
    ini_path = Path(app_data) / "Rainmeter" / "Rainmeter.ini"
    try:
        text = ini_path.read_text(encoding="utf-16")
    except UnicodeError:
        try:
            text = ini_path.read_text(encoding="utf-8-sig")
        except OSError:
            return None
    except OSError:
        return None
    match = re.search(r"(?mi)^SkinPath=(.+?)\s*$", text)
    if match is None:
        return None
    candidate = Path(match.group(1).strip()).expanduser().resolve(strict=False)
    return candidate if candidate.is_dir() else None


@dataclass(frozen=True, slots=True)
class CaptureWindowTarget:
    handle: int
    title: str
    width: int
    height: int


def _find_window_target(fragment: str) -> CaptureWindowTarget | None:
    if os.name != "nt":
        return None
    targets: list[CaptureWindowTarget] = []
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(window: int, _parameter: int) -> bool:
        length = int(user32.GetWindowTextLengthW(window))
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window, buffer, length + 1)
        title = buffer.value
        if title and fragment.casefold() in title.casefold():
            bounds = wintypes.RECT()
            if user32.GetWindowRect(window, ctypes.byref(bounds)):
                targets.append(
                    CaptureWindowTarget(
                        handle=int(window),
                        title=title,
                        width=max(0, int(bounds.right - bounds.left)),
                        height=max(0, int(bounds.bottom - bounds.top)),
                    )
                )
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return targets[0] if targets else None


def _wait_for_window(
    fragment: str,
    cancel_event: threading.Event,
    *,
    width: int = 1920,
    height: int = 1080,
) -> CaptureWindowTarget:
    deadline = time.monotonic() + 20
    stable_handle: int | None = None
    stable_observations = 0
    while time.monotonic() < deadline:
        if cancel_event.is_set():
            raise SpectrumProductionCancelled("Spectrum production was cancelled by the operator.")
        target = _find_window_target(fragment)
        if target is not None and target.width == width and target.height == height:
            if stable_handle == target.handle:
                stable_observations += 1
            else:
                stable_handle = target.handle
                stable_observations = 1
            if stable_observations >= 3:
                return target
        else:
            stable_handle = None
            stable_observations = 0
        time.sleep(0.1)
    raise SpectrumProductionError(
        "The job-specific Rainmeter presentation did not reach its required 1920x1080 capture size."
    )


def _geometry_runtime_session(
    job_root: Path,
    manifest: dict[str, Any],
    *,
    fullscreen: bool = False,
) -> BrowserRuntimeSession:
    geometry_root = ensure_within(job_root, job_root / "geometry")
    config_path = ensure_within(
        job_root,
        geometry_root / "config" / "runtime-config.json",
    )
    logo_path = ensure_within(job_root, job_root / str(manifest["resolvedLogo"]))
    audio_path = ensure_within(job_root, job_root / str(manifest["resolvedMasterAudio"]))
    runtime_files = {
        "/runtime.css": geometry_root / "runtime.css",
        "/runtime.js": geometry_root / "runtime.js",
        "/shaders/neopixel.vert.glsl": geometry_root / "shaders" / "neopixel.vert.glsl",
        "/shaders/neopixel.frag.glsl": geometry_root / "shaders" / "neopixel.frag.glsl",
    }
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        minimum_fps = float(config["minimumSustainedFps"])
        maximum_frame_time = float(config["maximumAverageFrameTimeMs"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SpectrumProductionError("The generated geometry runtime configuration is invalid.") from exc
    resources = BrowserRuntimeResources(
        entrypoint=geometry_root / "index.html",
        config=config_path,
        logo=logo_path,
        audio=audio_path,
        runtime_files=runtime_files,
    )
    return BrowserRuntimeSession(
        job_root,
        resources,
        browser=discover_browser(
            preferred=(
                (BrowserFamily.EDGE, BrowserFamily.CHROME)
                if fullscreen
                else (BrowserFamily.CHROME, BrowserFamily.EDGE)
            ),
        ),
        minimum_renderer_fps=minimum_fps,
        maximum_average_frame_time_ms=maximum_frame_time,
        fullscreen=fullscreen,
    )


def _geometry_capability_evidence(
    session: BrowserRuntimeSession,
    report: BrowserCapabilityReport | None = None,
) -> GeometryCapabilityEvidence:
    resolved_report = report or session.capability_snapshot()
    if resolved_report is None:
        raise SpectrumProductionError("The geometry runtime supplied no capability evidence.")
    return GeometryCapabilityEvidence.model_validate(resolved_report.to_json())


def _arrange_monitor_composition(
    background: CaptureWindowTarget,
    foreground: CaptureWindowTarget,
) -> None:
    if os.name != "nt":
        raise SpectrumProductionError("Monitor composition requires Windows.")
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    hwnd_topmost = wintypes.HWND(-1)
    swp_nosize = 0x0001
    swp_nomove = 0x0002
    swp_showwindow = 0x0040
    flags = swp_nosize | swp_nomove | swp_showwindow
    if not user32.SetWindowPos(background.handle, hwnd_topmost, 0, 0, 0, 0, flags):
        raise SpectrumProductionError("The geometry background z-order could not be secured.")
    if not user32.SetWindowPos(foreground.handle, hwnd_topmost, 0, 0, 0, 0, flags):
        raise SpectrumProductionError("The Rainmeter foreground z-order could not be secured.")


class RainmeterJobDeployment:
    def __init__(self, rainmeter_path: Path, job_root: Path, workspace_hash: str) -> None:
        self.rainmeter_path = rainmeter_path
        self.job_root = job_root
        self.job_id = job_root.name
        self.workspace_hash = workspace_hash
        self.root_name = f"TrackPrompt-WZHK-{self.job_id}"
        self.config_name = f"{self.root_name}\\WZHK Presentation"
        self.target_root: Path | None = None

    def deploy(self) -> Path:
        skins_root = _rainmeter_skin_path()
        if skins_root is None:
            raise SpectrumProductionError("Rainmeter's configured Skins directory could not be resolved.")
        target = (skins_root / self.root_name).resolve(strict=False)
        try:
            target.relative_to(skins_root.resolve())
        except ValueError as exc:
            raise SpectrumProductionError("The Rainmeter deployment path escaped its configured root.") from exc
        marker = target / ".trackprompt-job.json"
        if target.exists():
            try:
                existing = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SpectrumProductionError(
                    "The job-specific Rainmeter deployment name is occupied by unrelated content."
                ) from exc
            if existing.get("jobId") != self.job_id or existing.get("workspaceHash") != self.workspace_hash:
                raise SpectrumProductionError(
                    "The existing Rainmeter deployment does not match this production workspace."
                )
            self.target_root = target
            return target
        shutil.copytree(self.job_root / "skin", target, copy_function=shutil.copyfile)
        _atomic_json(
            marker,
            {
                "schemaVersion": "1.0.0",
                "jobId": self.job_id,
                "workspaceHash": self.workspace_hash,
                "source": "private-trackprompt-spectrum-workspace",
            },
        )
        self.target_root = target
        return target

    def activate(self) -> None:
        for args in (
            [str(self.rainmeter_path), "!RefreshApp"],
            [str(self.rainmeter_path), "!ActivateConfig", self.config_name, "Scattered.ini"],
        ):
            result = run_process_bounded(
                args,
                timeout_seconds=15,
                capture_stdout=False,
                stderr_limit=16_000,
            )
            if result.returncode != 0:
                raise SpectrumProductionError("Rainmeter could not activate the job presentation.")

    def deactivate(self) -> None:
        with suppress(OSError, ProcessTimedOut):
            run_process_bounded(
                [str(self.rainmeter_path), "!DeactivateConfig", self.config_name],
                timeout_seconds=10,
                capture_stdout=False,
                stderr_limit=16_000,
            )

    def cleanup(self) -> None:
        target = self.target_root
        if target is None or not target.exists():
            return
        skins_root = _rainmeter_skin_path()
        if skins_root is None:
            return
        try:
            target.resolve().relative_to(skins_root.resolve())
            marker = json.loads((target / ".trackprompt-job.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if marker.get("jobId") == self.job_id and marker.get("workspaceHash") == self.workspace_hash:
            def clear_read_only_and_retry(function: Any, path: str, _error: Any) -> None:
                os.chmod(path, stat.S_IWRITE)
                function(path)

            shutil.rmtree(target, onerror=clear_read_only_and_retry)


class FfmpegGraphicsCaptureProvider:
    def __init__(
        self,
        capabilities: CaptureProviderCapabilities,
        ffmpeg_path: str,
        output_path: Path,
        log_path: Path,
    ) -> None:
        self._capabilities = capabilities
        self.ffmpeg_path = ffmpeg_path
        self.output_path = output_path
        self.log_path = log_path
        self._command: list[str] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._log_stream: Any = None
        self._progress_thread: threading.Thread | None = None
        self._progress_lock = threading.Lock()
        self._media_time_seconds = 0.0

    def capabilities(self) -> CaptureProviderCapabilities:
        return self._capabilities

    def preflight(self) -> CaptureProviderCapabilities:
        return self._capabilities

    def prepare(
        self,
        job_root: Path,
        window_handle: int,
        foreground_window_handle: int | None = None,
    ) -> list[str]:
        if not self._capabilities.available or self._capabilities.encoder is None:
            raise SpectrumProductionError("The FFmpeg window capture provider is unavailable.")
        ensure_within(job_root, self.output_path)
        ensure_within(job_root, self.log_path)
        if foreground_window_handle is None:
            self._command = build_capture_command(
                ffmpeg_path=self.ffmpeg_path,
                window_handle=window_handle,
                output_path=self.output_path,
                encoder=self._capabilities.encoder,
            )
        else:
            if not self._capabilities.supports_alpha_composition:
                raise SpectrumProductionError(
                    "The FFmpeg provider did not qualify premultiplied-alpha composition."
                )
            self._command = build_composite_capture_command(
                ffmpeg_path=self.ffmpeg_path,
                background_window_handle=window_handle,
                foreground_window_handle=foreground_window_handle,
                output_path=self.output_path,
                encoder=self._capabilities.encoder,
            )
        return list(self._command)

    def prepare_monitor(self, job_root: Path, monitor_index: int = 0) -> list[str]:
        if not self._capabilities.available or self._capabilities.encoder is None:
            raise SpectrumProductionError("The FFmpeg monitor capture provider is unavailable.")
        if not self._capabilities.supports_monitor_capture:
            raise SpectrumProductionError("FFmpeg monitor capture did not pass capability inspection.")
        ensure_within(job_root, self.output_path)
        ensure_within(job_root, self.log_path)
        self._command = build_monitor_capture_command(
            ffmpeg_path=self.ffmpeg_path,
            monitor_index=monitor_index,
            output_path=self.output_path,
            encoder=self._capabilities.encoder,
        )
        return list(self._command)

    def prepare_chroma_composite(
        self,
        job_root: Path,
        background_window_handle: int,
        foreground_window_handle: int,
    ) -> list[str]:
        if not self._capabilities.available or self._capabilities.encoder is None:
            raise SpectrumProductionError("The FFmpeg chroma compositor is unavailable.")
        if not self._capabilities.supports_chroma_composition:
            raise SpectrumProductionError("FFmpeg chroma composition did not pass inspection.")
        ensure_within(job_root, self.output_path)
        ensure_within(job_root, self.log_path)
        self._command = build_chroma_composite_capture_command(
            ffmpeg_path=self.ffmpeg_path,
            background_window_handle=background_window_handle,
            foreground_window_handle=foreground_window_handle,
            output_path=self.output_path,
            encoder=self._capabilities.encoder,
        )
        return list(self._command)

    def start(self) -> None:
        if self._command is None:
            raise SpectrumProductionError("The capture provider was not prepared.")
        self._log_stream = self.log_path.open("wb")
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._log_stream,
                shell=False,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            self._log_stream.close()
            self._log_stream = None
            raise SpectrumProductionError("The FFmpeg capture process could not start.") from exc
        self._progress_thread = threading.Thread(
            target=self._read_progress,
            daemon=True,
            name="wzhk-spectrum-capture-progress",
        )
        self._progress_thread.start()

    def wait_until_recording(self, cancel_event: threading.Event) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if cancel_event.is_set():
                self.cancel()
                raise SpectrumProductionCancelled("Spectrum production was cancelled by the operator.")
            if self._process is None or self._process.poll() is not None:
                raise SpectrumProductionError("The FFmpeg capture process exited before recording began.")
            if (
                self.output_path.is_file()
                and self.output_path.stat().st_size > 0
                and self.media_time_seconds() >= 0.05
            ):
                return
            time.sleep(0.05)
        self.cancel()
        raise SpectrumProductionError("The FFmpeg capture process did not begin recording in time.")

    def stop(self) -> Path:
        process = self._process
        if process is None:
            raise SpectrumProductionError("The capture provider is not running.")
        if process.poll() is None and process.stdin is not None:
            with suppress(OSError):
                process.stdin.write(b"q\n")
                process.stdin.flush()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            _stop_owned_process(process)
        if process.stdin is not None:
            process.stdin.close()
        self._close_progress()
        self._close_log()
        if process.returncode not in {0, 255}:
            raise SpectrumProductionError("The FFmpeg capture process failed.")
        if not self.output_path.is_file() or self.output_path.stat().st_size <= 0:
            raise SpectrumProductionError("The FFmpeg capture process produced no artifact.")
        secure_private_file(self.output_path)
        return self.output_path

    def cancel(self) -> None:
        _stop_owned_process(self._process)
        self._close_progress()
        self._close_log()

    def artifact(self) -> Path | None:
        return self.output_path if self.output_path.is_file() else None

    def media_time_seconds(self) -> float:
        with self._progress_lock:
            return self._media_time_seconds

    def _read_progress(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw_line in iter(process.stdout.readline, b""):
            key, separator, raw_value = raw_line.decode("ascii", errors="ignore").strip().partition("=")
            if separator != "=" or key != "out_time_us":
                continue
            try:
                seconds = max(0.0, int(raw_value) / 1_000_000)
            except ValueError:
                continue
            with self._progress_lock:
                self._media_time_seconds = seconds

    def _close_progress(self) -> None:
        if self._progress_thread is not None:
            self._progress_thread.join(timeout=2)
            self._progress_thread = None
        process = self._process
        if process is not None and process.stdout is not None:
            process.stdout.close()

    def _close_log(self) -> None:
        if self._log_stream is not None:
            self._log_stream.close()
            self._log_stream = None


class LocalSpectrumProductionExecutor:
    def __init__(
        self,
        settings: Settings,
        paths: SpectrumPaths,
        inspection: SpectrumInspection | None,
    ) -> None:
        self.settings = settings
        self.paths = paths
        self.inspection = inspection

    def run(
        self,
        job_root: Path,
        manifest: dict[str, Any],
        cancel_event: threading.Event,
        transition: StateCallback,
    ) -> SpectrumProductionResult:
        outcome = inspect_wzhk_spectrum(self.settings, self.paths, self.inspection)
        if (
            outcome.descriptor.capture_availability
            is not SpectrumProductionAvailability.READY_FOR_CAPTURE
            or outcome.rainmeter_path is None
            or outcome.ffplay_path is None
            or outcome.master_timing is None
            or outcome.master_audio_path is None
            or outcome.capture_provider.encoder is None
        ):
            raise SpectrumProductionError("Spectrum capture dependencies are no longer ready.")
        timing = outcome.master_timing
        master_path = ensure_within(job_root, job_root / str(manifest["resolvedMasterAudio"])).resolve(strict=True)
        if sha256_file(master_path) != sha256_file(outcome.master_audio_path):
            raise SpectrumProductionError("The production master no longer matches the approved source asset.")

        capture_root = ensure_within(job_root, job_root / "capture")
        output_root = ensure_within(job_root, job_root / "output")
        logs_root = ensure_within(job_root, job_root / "logs")
        review_root = ensure_within(job_root, output_root / "review-frames")
        for directory in (capture_root, output_root, logs_root, review_root):
            directory.mkdir(parents=True, exist_ok=True)
            secure_private_directory(directory)

        capture_path = capture_root / CAPTURE_FILENAME
        capture_log = logs_root / "capture-provider.log"
        capture_manifest_path = capture_root / "capture-manifest.json"
        deployment = RainmeterJobDeployment(
            outcome.rainmeter_path,
            job_root,
            str(manifest["generatedWorkspaceHash"]),
        )
        provider = FfmpegGraphicsCaptureProvider(
            outcome.capture_provider,
            self.settings.ffmpeg_path,
            capture_path,
            capture_log,
        )
        playback: subprocess.Popen[bytes] | None = None
        clock_stop = threading.Event()
        clock_thread: threading.Thread | None = None
        synchronization: CaptureSynchronization | None = None
        artifacts: list[SpectrumArtifact] = []
        geometry_enabled = manifest.get("backgroundMode") == "generative-geometry"
        geometry_runtime: BrowserRuntimeSession | None = None
        geometry_capability: GeometryCapabilityEvidence | None = None
        geometry_telemetry: GeometryRuntimeTelemetry | None = None
        geometry_report_path = output_root / "geometry-runtime-report.json"
        try:
            reusable = self._reusable_capture(capture_path, capture_manifest_path, timing)
            if reusable is not None:
                synchronization = reusable
                if geometry_enabled and geometry_report_path.is_file():
                    try:
                        prior_geometry = json.loads(
                            geometry_report_path.read_text(encoding="utf-8")
                        )
                        geometry_capability = GeometryCapabilityEvidence.model_validate(
                            prior_geometry["capability"]
                        )
                        geometry_telemetry = GeometryRuntimeTelemetry.model_validate(
                            prior_geometry["telemetry"]
                        )
                    except (OSError, KeyError, ValueError, json.JSONDecodeError):
                        geometry_capability = None
                        geometry_telemetry = None
                transition(SpectrumProductionState.CAPTURE_COMPLETE, None)
            else:
                self._quarantine_if_present(capture_path)
                if geometry_enabled:
                    deployed_root = job_root / "skin"
                    geometry_runtime = _geometry_runtime_session(
                        job_root,
                        manifest,
                        fullscreen=True,
                    )
                    geometry_runtime.start_browser()
                    runtime_status = geometry_runtime.wait_for_phase(
                        {
                            BrowserRuntimePhase.RUNTIME_READY,
                            BrowserRuntimePhase.ERROR,
                        },
                        timeout_seconds=20,
                    )
                    geometry_capability = _geometry_capability_evidence(geometry_runtime)
                    if (
                        runtime_status.phase is BrowserRuntimePhase.ERROR
                        or geometry_capability.state != "READY"
                        or not geometry_capability.webgl2
                        or not geometry_capability.shader_compiled
                    ):
                        raise SpectrumProductionError(
                            "The Generative Geometry runtime failed its production capability check."
                        )
                    geometry_runtime.update_control(
                        BrowserControlCommand.PAUSE,
                        timeline_seconds=0,
                    )
                    background_target = _wait_for_window(
                        f"TrackPrompt-WZHK-Geometry-{job_root.name}",
                        cancel_event,
                    )
                    provider.prepare(job_root, background_target.handle)
                else:
                    deployed_root = deployment.deploy()
                    deployment.activate()
                    foreground_target = _wait_for_window(
                        f"TrackPrompt-WZHK-{job_root.name}",
                        cancel_event,
                    )
                    provider.prepare(job_root, foreground_target.handle)
                transition(SpectrumProductionState.CAPTURING, None)
                capture_started = time.monotonic()
                provider.start()
                provider.wait_until_recording(cancel_event)

                clock_path = deployed_root / "@Resources" / "runtime" / "production-clock.txt"
                playback = subprocess.Popen(
                    build_playback_command(str(outcome.ffplay_path), master_path),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
                    start_new_session=os.name != "nt",
                )
                master_zero = time.monotonic()
                capture_media_at_master_zero = provider.media_time_seconds()
                if geometry_runtime is not None:
                    geometry_runtime.update_control(
                        BrowserControlCommand.RUN,
                        timeline_seconds=0,
                        audio_response_enabled=True,
                    )
                clock_thread = threading.Thread(
                    target=self._publish_clock,
                    args=(
                        clock_path,
                        master_zero,
                        timing.master_duration_seconds,
                        clock_stop,
                        geometry_runtime,
                    ),
                    daemon=True,
                )
                clock_thread.start()
                return_code = _wait_owned_process(
                    playback,
                    timeout_seconds=timing.master_duration_seconds + 20,
                    cancel_event=cancel_event,
                )
                if return_code != 0:
                    raise SpectrumProductionError("The owned master playback process failed.")
                if geometry_runtime is not None:
                    with suppress(BrowserRuntimeError):
                        geometry_runtime.update_control(
                            BrowserControlCommand.STOP,
                            timeline_seconds=timing.master_duration_seconds,
                        )
                post_roll_deadline = time.monotonic() + 0.25
                while time.monotonic() < post_roll_deadline:
                    if cancel_event.is_set():
                        raise SpectrumProductionCancelled("Spectrum production was cancelled by the operator.")
                    time.sleep(0.02)
                capture_path = provider.stop()
                capture_stopped = time.monotonic()
                clock_stop.set()
                if clock_thread is not None:
                    clock_thread.join(timeout=2)
                if geometry_runtime is not None:
                    runtime_telemetry = geometry_runtime.telemetry_snapshot()
                    if runtime_telemetry is None:
                        raise SpectrumProductionError(
                            "The geometry runtime produced no renderer performance telemetry."
                        )
                    geometry_capability = _geometry_capability_evidence(
                        geometry_runtime,
                    )
                    telemetry_payload = runtime_telemetry.to_json()
                    if telemetry_payload.get("gpuRenderer") is None:
                        telemetry_payload["gpuRenderer"] = geometry_capability.gpu_renderer
                    geometry_telemetry = GeometryRuntimeTelemetry.model_validate(
                        telemetry_payload
                    )
                    if geometry_capability.state != "READY":
                        raise SpectrumProductionError(
                            "The geometry renderer did not sustain its rolling production cadence."
                        )
                capture_probe = probe_media_file(
                    self.settings.ffprobe_path,
                    capture_path,
                    count_frames=False,
                    timeout_seconds=30,
                )
                capture_video = capture_probe.video()
                if capture_video is None or capture_probe.duration_seconds < timing.master_duration_seconds - 0.25:
                    raise SpectrumProductionError("The capture intermediate is truncated or lacks video.")
                synchronization = CaptureSynchronization(
                    method="owned-playback-process-ffmpeg-progress-clock",
                    capture_started_monotonic_seconds=capture_started,
                    master_zero_monotonic_seconds=master_zero,
                    capture_stopped_monotonic_seconds=capture_stopped,
                    measured_start_offset_seconds=capture_media_at_master_zero,
                    measured_end_offset_seconds=(
                        capture_probe.duration_seconds
                        - capture_media_at_master_zero
                        - timing.master_duration_seconds
                    ),
                    correction_applied_seconds=capture_media_at_master_zero,
                    precision="host-monotonic-process-boundary",
                )
                if geometry_enabled:
                    if geometry_capability is None or geometry_telemetry is None:
                        raise SpectrumProductionError(
                            "Generative Geometry capture evidence is incomplete."
                        )
                    _atomic_json(
                        geometry_report_path,
                        {
                            "schemaVersion": "1.0.0",
                            "rendererId": "wzhk-generative-geometry",
                            "capability": geometry_capability.model_dump(
                                mode="json",
                                by_alias=True,
                            ),
                            "telemetry": geometry_telemetry.model_dump(
                                mode="json",
                                by_alias=True,
                            ),
                            "captureFps": 60,
                            "captureCfrIsRendererEvidence": False,
                            "composition": "single-browser-webgl2-compositor",
                        },
                    )
                capture_manifest = {
                    "schemaVersion": "1.0.0",
                    "provider": outcome.capture_provider.model_dump(mode="json", by_alias=True),
                    "timing": timing.model_dump(mode="json", by_alias=True),
                    "synchronization": synchronization.model_dump(mode="json", by_alias=True),
                    "capture": {
                        "relativePath": capture_path.relative_to(job_root).as_posix(),
                        "sha256": sha256_file(capture_path),
                        "sizeBytes": capture_path.stat().st_size,
                        "durationSeconds": capture_probe.duration_seconds,
                        "video": capture_video.model_dump(mode="json", by_alias=True),
                    },
                    "scratchAudioUsedForFinal": False,
                    "droppedFrames": None,
                    "backgroundMode": manifest.get("backgroundMode", "static-structured"),
                    "composition": (
                        "single-browser-webgl2-compositor"
                        if geometry_enabled
                        else "ffmpeg-single-rainmeter-hwnd"
                    ),
                    "geometryCapability": (
                        geometry_capability.model_dump(mode="json", by_alias=True)
                        if geometry_capability is not None
                        else None
                    ),
                    "geometryTelemetry": (
                        geometry_telemetry.model_dump(mode="json", by_alias=True)
                        if geometry_telemetry is not None
                        else None
                    ),
                }
                _atomic_json(capture_manifest_path, capture_manifest)
                transition(SpectrumProductionState.CAPTURE_COMPLETE, None)

            if synchronization is None:
                raise SpectrumProductionError("Capture synchronization evidence is unavailable.")
            artifacts.extend(
                [
                    _artifact(
                        job_root,
                        capture_path,
                        SpectrumArtifactType.CAPTURE_INTERMEDIATE,
                        SpectrumProductionState.CAPTURE_COMPLETE,
                        "FFmpeg Windows Graphics Capture video-only stream; scratch/system audio excluded",
                    ),
                    _artifact(
                        job_root,
                        capture_manifest_path,
                        SpectrumArtifactType.CAPTURE_MANIFEST,
                        SpectrumProductionState.CAPTURE_COMPLETE,
                        "TrackPrompt capture timing and provider evidence",
                    ),
                ]
            )
            if capture_log.is_file() and capture_log.stat().st_size > 0:
                artifacts.append(
                    _artifact(
                        job_root,
                        capture_log,
                        SpectrumArtifactType.CAPTURE_LOG,
                        SpectrumProductionState.CAPTURE_COMPLETE,
                        "Bounded job-owned FFmpeg capture log",
                    )
                )
            if geometry_enabled and geometry_report_path.is_file():
                artifacts.append(
                    _artifact(
                        job_root,
                        geometry_report_path,
                        SpectrumArtifactType.GEOMETRY_RUNTIME_REPORT,
                        SpectrumProductionState.CAPTURE_COMPLETE,
                        "Measured WebGL2 capability and renderer cadence; separate from CFR capture",
                    )
                )

            transition(SpectrumProductionState.MUXING, {"synchronization": synchronization.model_dump(mode="json", by_alias=True)})
            final_path = output_root / select_output_filename(
                str(manifest.get("backgroundMode", "static-structured")),
                manifest.get("compositionRevision"),
            )
            self._quarantine_if_present(final_path)
            inflight_final = output_root / f".inflight-{uuid4().hex}.mp4"
            mux_command = build_mux_command(
                ffmpeg_path=self.settings.ffmpeg_path,
                capture_path=capture_path,
                master_path=master_path,
                output_path=inflight_final,
                start_offset_seconds=synchronization.correction_applied_seconds,
                master_duration_seconds=timing.master_duration_seconds,
                encoder=outcome.capture_provider.encoder,
            )
            self._run_ffmpeg(
                mux_command,
                timeout_seconds=max(300, timing.master_duration_seconds * 4),
                cancel_event=cancel_event,
            )
            if not inflight_final.is_file() or inflight_final.stat().st_size <= 100_000:
                raise SpectrumProductionError("FFmpeg produced no usable final video.")
            transition(SpectrumProductionState.VALIDATING, None)
            final_probe = probe_media_file(self.settings.ffprobe_path, inflight_final)
            validation = validate_final_media(final_probe, timing)
            if not validation.valid:
                raise SpectrumProductionError("The final Spectrum video failed deterministic validation.")
            os.replace(inflight_final, final_path)
            secure_private_file(final_path)

            mux_manifest_path = output_root / "mux-manifest.json"
            _atomic_json(
                mux_manifest_path,
                {
                    "schemaVersion": "1.0.0",
                    "videoSourceSha256": sha256_file(capture_path),
                    "audioSourceSha256": sha256_file(master_path),
                    "audioPolicy": "original-approved-master-only",
                    "audioProcessing": ["AAC 320k container encoding"],
                    "startTrimSeconds": synchronization.correction_applied_seconds,
                    "masterDurationSeconds": timing.master_duration_seconds,
                    "encoder": outcome.capture_provider.encoder,
                    "finalRelativePath": final_path.relative_to(job_root).as_posix(),
                    "finalSha256": sha256_file(final_path),
                },
            )
            validation_path = output_root / "validation-report.json"
            _atomic_json(validation_path, validation.model_dump(mode="json", by_alias=True))
            artifacts.extend(
                [
                    _artifact(
                        job_root,
                        mux_manifest_path,
                        SpectrumArtifactType.MUX_MANIFEST,
                        SpectrumProductionState.VALIDATING,
                        "Original approved master muxed over the captured visual stream",
                    ),
                    _artifact(
                        job_root,
                        final_path,
                        SpectrumArtifactType.FINAL_VIDEO,
                        SpectrumProductionState.VALIDATING,
                        "Validated WZHK Spectrum final MP4",
                    ),
                    _artifact(
                        job_root,
                        validation_path,
                        SpectrumArtifactType.VALIDATION_REPORT,
                        SpectrumProductionState.VALIDATING,
                        "ffprobe structural, duration, stream, frame-rate, and frame-count checks",
                    ),
                ]
            )
            for label, timestamp in review_frame_timestamps(timing.master_duration_seconds):
                frame_path = review_root / f"{label}.png"
                if not frame_path.exists():
                    self._run_ffmpeg(
                        build_review_frame_command(
                            ffmpeg_path=self.settings.ffmpeg_path,
                            final_video_path=final_path,
                            timestamp_seconds=timestamp,
                            output_path=frame_path,
                        ),
                        timeout_seconds=60,
                        cancel_event=cancel_event,
                    )
                artifacts.append(
                    _artifact(
                        job_root,
                        frame_path,
                        SpectrumArtifactType.REVIEW_FRAME,
                        SpectrumProductionState.VALIDATING,
                        "Deterministic FFmpeg review-frame extraction",
                        timestamp_seconds=timestamp,
                    )
                )
            capture_probe = probe_media_file(self.settings.ffprobe_path, capture_path)
            capture_video = capture_probe.video()
            return SpectrumProductionResult(
                synchronization=synchronization,
                artifacts=artifacts,
                validation=validation,
                provider=outcome.capture_provider.provider_id,
                encoder=outcome.capture_provider.encoder,
                captured_frames=(capture_video.frame_count if capture_video else None),
                dropped_frames=None,
                capture_duration_seconds=capture_probe.duration_seconds,
                geometry_capability=geometry_capability,
                geometry_telemetry=geometry_telemetry,
            )
        finally:
            clock_stop.set()
            if clock_thread is not None:
                clock_thread.join(timeout=2)
            _stop_owned_process(playback)
            provider.cancel()
            if geometry_runtime is not None:
                geometry_runtime.close()
            deployment.deactivate()
            deployment.cleanup()

    def _publish_clock(
        self,
        path: Path,
        start_monotonic: float,
        master_duration_seconds: float,
        stop_event: threading.Event,
        geometry_runtime: BrowserRuntimeSession | None = None,
    ) -> None:
        while not stop_event.is_set():
            elapsed = min(master_duration_seconds, max(0.0, time.monotonic() - start_monotonic))
            with suppress(OSError):
                _atomic_clock(path, elapsed)
            if geometry_runtime is not None:
                with suppress(BrowserRuntimeError):
                    geometry_runtime.update_control(
                        BrowserControlCommand.RUN,
                        timeline_seconds=elapsed,
                        audio_response_enabled=True,
                    )
            stop_event.wait(0.016)
        with suppress(OSError):
            _atomic_clock(path, master_duration_seconds)
        if geometry_runtime is not None:
            with suppress(BrowserRuntimeError):
                geometry_runtime.update_control(
                    BrowserControlCommand.STOP,
                    timeline_seconds=master_duration_seconds,
                    audio_response_enabled=True,
                )

    def _reusable_capture(
        self,
        capture_path: Path,
        manifest_path: Path,
        timing: SpectrumMasterTiming,
    ) -> CaptureSynchronization | None:
        if not capture_path.is_file() or not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected_hash = manifest["capture"]["sha256"]
            synchronization = CaptureSynchronization.model_validate(manifest["synchronization"])
            probe = probe_media_file(
                self.settings.ffprobe_path,
                capture_path,
                count_frames=False,
                timeout_seconds=30,
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError, SpectrumProductionError, ValueError):
            return None
        if (
            expected_hash != sha256_file(capture_path)
            or probe.video() is None
            or probe.duration_seconds < timing.master_duration_seconds - 0.25
        ):
            return None
        return synchronization

    def _quarantine_if_present(self, path: Path) -> None:
        if not path.exists():
            return
        quarantine = path.with_name(f"{path.stem}.superseded-{uuid4().hex}{path.suffix}")
        os.replace(path, quarantine)

    def _run_ffmpeg(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> None:
        try:
            result = run_process_bounded(
                args,
                timeout_seconds=timeout_seconds,
                cancel_requested=cancel_event.is_set,
                capture_stdout=False,
                stderr_limit=128_000,
            )
        except ProcessWasCancelled as exc:
            raise SpectrumProductionCancelled("Spectrum production was cancelled by the operator.") from exc
        except (FileNotFoundError, ProcessTimedOut) as exc:
            raise SpectrumProductionError("FFmpeg could not complete the production stage.") from exc
        if result.returncode != 0 or result.stderr_exceeded:
            raise SpectrumProductionError("FFmpeg rejected the production stage.")


class SpectrumProductionManager:
    def __init__(
        self,
        settings: Settings,
        paths: SpectrumPaths,
        inspection: SpectrumInspection | None = None,
        executor: SpectrumProductionExecutor | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths
        self.inspection = inspection
        self.executor = executor or LocalSpectrumProductionExecutor(settings, paths, inspection)
        self._lock = threading.RLock()
        self._active: dict[str, tuple[threading.Thread, threading.Event]] = {}

    def preflight(self, job_id: str) -> SpectrumWorkspaceJob:
        with self._lock:
            job_root = self._job_root(job_id)
            manifest = _read_manifest(job_root)
            if manifest.get("mode") != "production" or manifest.get("previewSection") is not None:
                result = self._invalid_workspace_preflight()
                manifest.update(
                    state=SpectrumProductionState.FAILED.value,
                    productionAvailability=result.availability.value,
                    capturePreflight=result.model_dump(mode="json", by_alias=True),
                    errorMessage="Preview workspaces cannot enter production capture.",
                )
                self._persist(job_root, manifest)
                return load_workspace_job(self.paths, job_id)
        # Browser/GPU probing runs outside the manager lock. Chromium callbacks
        # arrive on the runtime server thread and must never contend with job
        # registry synchronization.
        result = self._evaluate_preflight(job_root, manifest)
        ready = result.ready
        geometry_capability = result.geometry_capability
        with self._lock:
            manifest = _read_manifest(job_root)
            manifest.update(
                state=(
                    SpectrumProductionState.CAPTURE_READY.value
                    if ready
                    else SpectrumProductionState.CAPTURE_PREFLIGHT.value
                ),
                productionAvailability=result.availability.value,
                capturePreflight=result.model_dump(mode="json", by_alias=True),
                captureProvider=result.provider.provider_id,
                encoder=result.provider.encoder,
                geometryCapability=(
                    geometry_capability.model_dump(mode="json", by_alias=True)
                    if geometry_capability is not None
                    else None
                ),
                errorMessage=None if ready else result.operator_notice,
            )
            self._persist(job_root, manifest)
            return load_workspace_job(self.paths, job_id)

    def _evaluate_preflight(
        self,
        job_root: Path,
        manifest: dict[str, Any],
    ) -> CapturePreflightResult:
        outcome = inspect_wzhk_spectrum(self.settings, self.paths, self.inspection)
        availability = (
            outcome.descriptor.capture_availability
            or SpectrumProductionAvailability.INVALID_WORKSPACE
        )
        ready = availability is SpectrumProductionAvailability.READY_FOR_CAPTURE
        geometry_requested = manifest.get("backgroundMode") == "generative-geometry"
        geometry_capability: GeometryCapabilityEvidence | None = None
        geometry_warning: str | None = None
        if geometry_requested and ready:
            if self.inspection is not None and self.inspection.geometry_capability is not None:
                geometry_capability = self.inspection.geometry_capability
            else:
                for _attempt in range(2):
                    runtime: BrowserRuntimeSession | None = None
                    try:
                        runtime = _geometry_runtime_session(job_root, manifest)
                        geometry_report = runtime.probe_capability(timeout_seconds=30)
                        geometry_capability = _geometry_capability_evidence(
                            runtime,
                            geometry_report,
                        )
                    except (BrowserRuntimeError, OSError, SpectrumProductionError) as exc:
                        geometry_capability = GeometryCapabilityEvidence(
                            state="BROWSER_UNAVAILABLE",
                            webgl2=None,
                            gpu_renderer=None,
                            shader_compiled=None,
                            performance_measured=False,
                            performance_sufficient=None,
                            renderer_fps=None,
                            average_frame_time_ms=None,
                            point_count=None,
                            detail=(
                                f"The local geometry capability probe failed safely: {exc}"
                            )[:500],
                        )
                    finally:
                        if runtime is not None:
                            runtime.close()
                    if geometry_capability.state != "BROWSER_UNAVAILABLE":
                        break
            if geometry_capability is None:
                geometry_capability = GeometryCapabilityEvidence(
                    state="BROWSER_UNAVAILABLE",
                    webgl2=None,
                    gpu_renderer=None,
                    shader_compiled=None,
                    performance_measured=False,
                    performance_sufficient=None,
                    renderer_fps=None,
                    average_frame_time_ms=None,
                    point_count=None,
                    detail="The geometry capability probe produced no evidence.",
                )
            geometry_ready = (
                geometry_capability.state == "READY"
                and outcome.capture_provider.supports_window_capture
            )
            if not geometry_ready:
                ready = False
                availability = SpectrumProductionAvailability.INVALID_WORKSPACE
                geometry_warning = (
                    "Generative Geometry did not qualify for capture; prepare a "
                    "static-structured fallback workspace or resolve the reported capability."
                )
        operator_notice = (
            "Starting production will launch one owned loopback-only WebGL2 browser compositor "
            "for geometry and WZHK identity, play the approved master, and capture "
            "that exact window with FFmpeg/GPU capture."
            if geometry_requested
            else "Starting production will load a job-specific Rainmeter skin, play the approved "
            "master through the Windows output, and use FFmpeg/GPU capture. Desktop interaction "
            "can affect window capture."
        )
        result_warnings = list(outcome.descriptor.warnings)
        if geometry_warning is not None:
            result_warnings.append(geometry_warning)
        return CapturePreflightResult(
            availability=availability,
            ready=ready,
            provider=outcome.capture_provider,
            timing=outcome.master_timing,
            rainmeter_path_resolved=outcome.rainmeter_path is not None,
            ffmpeg_path_resolved=outcome.descriptor.capture_availability
            not in {SpectrumProductionAvailability.MISSING_FFMPEG},
            ffprobe_path_resolved=outcome.master_timing is not None,
            playback_path_resolved=outcome.ffplay_path is not None,
            workspace_valid=True,
            master_valid=outcome.master_timing is not None,
            geometry_capability=geometry_capability,
            static_fallback_available=True,
            alpha_composition_ready=False,
            monitor_composition_ready=False,
            chroma_composition_ready=False,
            single_browser_composition_ready=(
                outcome.capture_provider.supports_window_capture
                if geometry_requested
                else False
            ),
            operator_notice=operator_notice,
            warnings=result_warnings,
        )

    def start(self, job_id: str) -> SpectrumWorkspaceJob:
        with self._lock:
            job_root = self._job_root(job_id)
            manifest = _read_manifest(job_root)
            if manifest.get("mode") != "production" or manifest.get("previewSection") is not None:
                raise SpectrumWorkspaceError("Preview overrides cannot enter Spectrum production.")
            if manifest.get("state") != SpectrumProductionState.CAPTURE_READY.value:
                raise SpectrumWorkspaceError("Run a successful Spectrum capture preflight first.")
            if job_id in self._active and self._active[job_id][0].is_alive():
                raise SpectrumWorkspaceError("Spectrum production is already active for this job.")
            cancel_event = threading.Event()
            thread = threading.Thread(
                target=self._run_job,
                args=(job_id, cancel_event),
                daemon=True,
                name=f"wzhk-spectrum-{job_id[:8]}",
            )
            self._active[job_id] = (thread, cancel_event)
            thread.start()
            return load_workspace_job(self.paths, job_id)

    def cancel(self, job_id: str, reason: str) -> SpectrumWorkspaceJob:
        with self._lock:
            self._job_root(job_id)
            active = self._active.get(job_id)
            if active is None or not active[0].is_alive():
                raise SpectrumWorkspaceError("No active Spectrum production process was found for this job.")
            active[1].set()
            job_root = self._job_root(job_id)
            manifest = _read_manifest(job_root)
            manifest["cancellationRequested"] = True
            manifest["cancellationReason"] = reason
            self._persist(job_root, manifest)
            return load_workspace_job(self.paths, job_id)

    def _run_job(self, job_id: str, cancel_event: threading.Event) -> None:
        job_root = self._job_root(job_id)
        try:
            manifest = _read_manifest(job_root)

            def transition(state: SpectrumProductionState, values: dict[str, Any] | None) -> None:
                with self._lock:
                    current_manifest = _read_manifest(job_root)
                    current_state = SpectrumProductionState(str(current_manifest["state"]))
                    validate_production_transition(current_state, state)
                    current_manifest["state"] = state.value
                    if values:
                        current_manifest.update(values)
                    current_manifest["updatedAt"] = datetime.now(UTC).isoformat()
                    self._persist(job_root, current_manifest)

            result = self.executor.run(job_root, manifest, cancel_event, transition)
            with self._lock:
                current = _read_manifest(job_root)
                validate_production_transition(SpectrumProductionState(str(current["state"])), SpectrumProductionState.COMPLETE)
                current.update(
                    state=SpectrumProductionState.COMPLETE.value,
                    synchronization=result.synchronization.model_dump(mode="json", by_alias=True),
                    artifacts=[artifact.model_dump(mode="json", by_alias=True) for artifact in result.artifacts],
                    validationReport=result.validation.model_dump(mode="json", by_alias=True),
                    captureProvider=result.provider,
                    encoder=result.encoder,
                    capturedFrames=result.captured_frames,
                    droppedFrames=result.dropped_frames,
                    captureDurationSeconds=result.capture_duration_seconds,
                    geometryCapability=(
                        result.geometry_capability.model_dump(mode="json", by_alias=True)
                        if result.geometry_capability is not None
                        else current.get("geometryCapability")
                    ),
                    geometryTelemetry=(
                        result.geometry_telemetry.model_dump(mode="json", by_alias=True)
                        if result.geometry_telemetry is not None
                        else current.get("geometryTelemetry")
                    ),
                    errorMessage=None,
                    updatedAt=datetime.now(UTC).isoformat(),
                )
                self._persist(job_root, current)
        except SpectrumProductionCancelled as exc:
            self._finish_failure(job_root, SpectrumProductionState.CANCELLED, str(exc))
        except Exception as exc:
            safe_message = str(exc) if isinstance(exc, SpectrumProductionError) else "Spectrum production failed unexpectedly."
            self._finish_failure(job_root, SpectrumProductionState.FAILED, safe_message)
        finally:
            with self._lock:
                self._active.pop(job_id, None)

    def _finish_failure(
        self,
        job_root: Path,
        state: SpectrumProductionState,
        message: str,
    ) -> None:
        with self._lock:
            manifest = _read_manifest(job_root)
            manifest["state"] = state.value
            manifest["errorMessage"] = message[:500]
            manifest["updatedAt"] = datetime.now(UTC).isoformat()
            self._persist(job_root, manifest)

    def _persist(self, job_root: Path, manifest: dict[str, Any]) -> None:
        _atomic_json(job_root / "manifest.json", manifest)

    def _job_root(self, job_id: str) -> Path:
        job = load_workspace_job(self.paths, job_id)
        return ensure_within(self.paths.jobs_root, self.paths.data_root / job.workspace_relative_path)

    def _invalid_workspace_preflight(self) -> CapturePreflightResult:
        return CapturePreflightResult(
            availability=SpectrumProductionAvailability.INVALID_WORKSPACE,
            ready=False,
            provider=CaptureProviderCapabilities(
                available=False,
                supports_window_capture=False,
                supports_constant_frame_rate=False,
                detail="Preview workspaces cannot be used for production capture.",
            ),
            rainmeter_path_resolved=False,
            ffmpeg_path_resolved=False,
            ffprobe_path_resolved=False,
            playback_path_resolved=False,
            workspace_valid=False,
            master_valid=False,
            operator_notice="Prepare a production workspace with no fixed section override.",
        )

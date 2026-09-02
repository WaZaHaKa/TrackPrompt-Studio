from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ....config import Settings
from ....privacy import secure_private_directory
from ....subprocess_utils import run_process_bounded
from ...schemas import SpectrumWorkspaceJob
from ..capture import (
    FfmpegGraphicsCaptureProvider,
    _atomic_json,
    _geometry_capability_evidence,
    _geometry_runtime_session,
    _wait_for_window,
)
from ..preflight import (
    SpectrumPaths,
    ensure_within,
    inspect_wzhk_spectrum,
    sha256_file,
)
from ..production import (
    SpectrumProductionAvailability,
    SpectrumProductionError,
    build_review_frame_command,
    probe_media_file,
)
from ..workspace import load_workspace_job
from .browser_runtime import BrowserControlCommand, BrowserRuntimePhase


def _manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpectrumProductionError("The qualification workspace manifest is invalid.") from exc
    if not isinstance(value, dict):
        raise SpectrumProductionError("The qualification workspace manifest is invalid.")
    return value


def _job_root(paths: SpectrumPaths, job: SpectrumWorkspaceJob) -> Path:
    return ensure_within(paths.jobs_root, paths.data_root / job.workspace_relative_path)


def qualify_geometry_composition(
    settings: Settings,
    paths: SpectrumPaths,
    job_id: str,
    *,
    duration_seconds: float = 4.0,
    timeline_seconds: float = 120.0,
) -> dict[str, Any]:
    try:
        parsed = UUID(job_id)
    except ValueError as exc:
        raise SpectrumProductionError("The qualification job identity is invalid.") from exc
    if parsed.version != 4 or str(parsed) != job_id:
        raise SpectrumProductionError("The qualification job identity is invalid.")
    if not 2 <= duration_seconds <= 15:
        raise SpectrumProductionError("Composition qualification must run for 2 to 15 seconds.")
    if not 0 <= timeline_seconds <= 196.62:
        raise SpectrumProductionError("The qualification timeline position is invalid.")

    job = load_workspace_job(paths, job_id)
    job_root = _job_root(paths, job)
    manifest = _manifest(job_root / "manifest.json")
    if manifest.get("backgroundMode") != "generative-geometry":
        raise SpectrumProductionError("Composition qualification requires a generative workspace.")
    outcome = inspect_wzhk_spectrum(settings, paths)
    if (
        outcome.descriptor.capture_availability
        is not SpectrumProductionAvailability.READY_FOR_CAPTURE
        or outcome.capture_provider.encoder is None
        or not outcome.capture_provider.supports_window_capture
    ):
        raise SpectrumProductionError("Composition qualification dependencies are unavailable.")

    qualification_root = ensure_within(
        job_root,
        job_root / "output" / "qualification" / uuid4().hex,
    )
    qualification_root.mkdir(parents=True, exist_ok=False)
    secure_private_directory(qualification_root)
    capture_path = qualification_root / "browser-composition.mkv"
    log_path = qualification_root / "capture.log"
    frame_path = qualification_root / "frame.png"
    report_path = qualification_root / "qualification.json"
    provider = FfmpegGraphicsCaptureProvider(
        outcome.capture_provider,
        settings.ffmpeg_path,
        capture_path,
        log_path,
    )
    runtime = _geometry_runtime_session(job_root, manifest, fullscreen=True)
    cancel = threading.Event()
    try:
        runtime.start_browser()
        runtime_status = runtime.wait_for_phase(
            {BrowserRuntimePhase.RUNTIME_READY, BrowserRuntimePhase.ERROR},
            timeout_seconds=20,
        )
        capability = _geometry_capability_evidence(runtime)
        if (
            runtime_status.phase is BrowserRuntimePhase.ERROR
            or capability.state != "READY"
            or not capability.webgl2
            or not capability.shader_compiled
        ):
            raise SpectrumProductionError("The geometry runtime did not qualify.")
        runtime.update_control(
            BrowserControlCommand.PAUSE,
            timeline_seconds=timeline_seconds,
            audio_response_enabled=False,
        )
        background = _wait_for_window(
            f"TrackPrompt-WZHK-Geometry-{job_id}",
            cancel,
        )
        provider.prepare(job_root, background.handle)
        provider.start()
        provider.wait_until_recording(cancel)
        runtime.update_control(
            BrowserControlCommand.RUN,
            timeline_seconds=timeline_seconds,
            audio_response_enabled=False,
        )
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            time.sleep(0.02)
        runtime.update_control(
            BrowserControlCommand.PAUSE,
            timeline_seconds=min(196.62, timeline_seconds + duration_seconds),
            audio_response_enabled=False,
        )
        provider.stop()
        probe = probe_media_file(
            settings.ffprobe_path,
            capture_path,
            count_frames=True,
            timeout_seconds=60,
        )
        video = probe.video()
        if video is None or video.width != 1920 or video.height != 1080:
            raise SpectrumProductionError("The browser-composition qualification bounds are invalid.")
        frame_result = run_process_bounded(
            build_review_frame_command(
                ffmpeg_path=settings.ffmpeg_path,
                final_video_path=capture_path,
                timestamp_seconds=max(0.5, probe.duration_seconds - 1.0),
                output_path=frame_path,
            ),
            timeout_seconds=60,
            capture_stdout=False,
            stderr_limit=32_000,
        )
        if frame_result.returncode != 0 or not frame_path.is_file():
            raise SpectrumProductionError("The qualification review frame could not be extracted.")
        telemetry_value = runtime.telemetry_snapshot()
        telemetry = telemetry_value.to_json() if telemetry_value is not None else None
        capability = _geometry_capability_evidence(runtime)
        report: dict[str, Any] = {
            "schemaVersion": "1.0.0",
            "jobId": job_id,
            "composition": "single-browser-webgl2-compositor",
            "durationSeconds": probe.duration_seconds,
            "timelineSeconds": timeline_seconds,
            "capability": capability.model_dump(mode="json", by_alias=True),
            "telemetry": telemetry,
            "capture": {
                "relativePath": capture_path.relative_to(job_root).as_posix(),
                "sha256": sha256_file(capture_path),
                "sizeBytes": capture_path.stat().st_size,
                "video": video.model_dump(mode="json", by_alias=True),
            },
            "reviewFrame": {
                "relativePath": frame_path.relative_to(job_root).as_posix(),
                "sha256": sha256_file(frame_path),
                "sizeBytes": frame_path.stat().st_size,
            },
            "qualified": telemetry is not None and capability.state == "READY",
        }
        _atomic_json(report_path, report)
        return report
    finally:
        provider.cancel()
        runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Qualify the single owned WebGL2 composition window.",
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--duration-seconds", type=float, default=4.0)
    parser.add_argument("--timeline-seconds", type=float, default=120.0)
    arguments = parser.parse_args()
    settings = Settings.from_env()
    repository_root = Path(__file__).resolve().parents[5]
    report = qualify_geometry_composition(
        settings,
        SpectrumPaths(repository_root, settings.data_dir),
        arguments.job_id,
        duration_seconds=arguments.duration_seconds,
        timeline_seconds=arguments.timeline_seconds,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

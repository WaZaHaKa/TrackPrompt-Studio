from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.renderers.schemas import SpectrumWorkspacePrepareRequest
from app.renderers.wzhk_spectrum.capture import (
    SpectrumProductionCancelled,
    SpectrumProductionManager,
    SpectrumProductionResult,
)
from app.renderers.wzhk_spectrum.design import (
    load_design_preset,
    resolve_state_at_milliseconds,
)
from app.renderers.wzhk_spectrum.preflight import SpectrumPaths
from app.renderers.wzhk_spectrum.production import (
    FINAL_OUTPUT_FILENAME,
    GENERATIVE_OUTPUT_FILENAME,
    GEOMETRY_FIRST_OUTPUT_FILENAME,
    CaptureProviderCapabilities,
    CaptureSynchronization,
    MediaProbeSummary,
    MediaStreamProbe,
    SpectrumMasterTiming,
    SpectrumProductionError,
    SpectrumProductionState,
    SpectrumValidationCheck,
    SpectrumValidationReport,
    build_capture_command,
    build_chroma_composite_capture_command,
    build_composite_capture_command,
    build_monitor_capture_command,
    build_mux_command,
    build_playback_command,
    build_review_frame_command,
    resolve_master_timing,
    review_frame_timestamps,
    select_output_filename,
    validate_final_media,
    validate_production_transition,
)
from app.renderers.wzhk_spectrum.workspace import prepare_workspace

from .helpers import settings_for
from .test_wzhk_spectrum import CANONICAL_DESIGN_PATH, _build_fixture, _inspect


def _valid_probe(duration: float = 196.62) -> MediaProbeSummary:
    return MediaProbeSummary(
        duration_seconds=duration,
        size_bytes=25_000_000,
        streams=[
            MediaStreamProbe(
                codec_type="video",
                codec_name="h264",
                width=1920,
                height=1080,
                frame_rate=60,
                duration_seconds=duration,
                frame_count=11_797,
            ),
            MediaStreamProbe(
                codec_type="audio",
                codec_name="aac",
                duration_seconds=duration,
            ),
        ],
    )


def _synchronization(duration: float = 196.62) -> CaptureSynchronization:
    return CaptureSynchronization(
        method="owned-playback-process-ffmpeg-progress-clock",
        capture_started_monotonic_seconds=100.0,
        master_zero_monotonic_seconds=100.125,
        capture_stopped_monotonic_seconds=100.125 + duration + 0.2,
        measured_start_offset_seconds=0.125,
        measured_end_offset_seconds=0.2,
        correction_applied_seconds=0.125,
        precision="host-monotonic-process-boundary",
    )


def _validation(timing: SpectrumMasterTiming) -> SpectrumValidationReport:
    return SpectrumValidationReport(
        valid=True,
        final_video=_valid_probe(timing.master_duration_seconds),
        timing=timing,
        checks=[
            SpectrumValidationCheck(
                id="synthetic-valid",
                passed=True,
                measured="valid",
                expected="valid",
            )
        ],
    )


class SuccessfulExecutor:
    def run(
        self,
        _job_root: Path,
        manifest: dict[str, Any],
        _cancel_event: threading.Event,
        transition: Any,
    ) -> SpectrumProductionResult:
        timing = SpectrumMasterTiming.model_validate(manifest["masterTiming"])
        for state in (
            SpectrumProductionState.CAPTURING,
            SpectrumProductionState.CAPTURE_COMPLETE,
            SpectrumProductionState.MUXING,
            SpectrumProductionState.VALIDATING,
        ):
            transition(state, None)
        return SpectrumProductionResult(
            synchronization=_synchronization(timing.master_duration_seconds),
            artifacts=[],
            validation=_validation(timing),
            provider="ffmpeg-gfxcapture",
            encoder="h264_nvenc",
            captured_frames=11_810,
            dropped_frames=None,
            capture_duration_seconds=timing.master_duration_seconds + 0.2,
        )


class BlockingExecutor:
    def run(
        self,
        _job_root: Path,
        _manifest: dict[str, Any],
        cancel_event: threading.Event,
        transition: Any,
    ) -> SpectrumProductionResult:
        transition(SpectrumProductionState.CAPTURING, None)
        if not cancel_event.wait(3):
            raise SpectrumProductionError("Synthetic cancellation did not arrive.")
        raise SpectrumProductionCancelled("Spectrum production was cancelled by the operator.")


class RetryExecutor(SuccessfulExecutor):
    def __init__(self) -> None:
        self.capture_calls = 0
        self.attempts = 0

    def run(
        self,
        job_root: Path,
        manifest: dict[str, Any],
        cancel_event: threading.Event,
        transition: Any,
    ) -> SpectrumProductionResult:
        self.attempts += 1
        if self.attempts == 1:
            self.capture_calls += 1
            transition(SpectrumProductionState.CAPTURING, None)
            transition(SpectrumProductionState.CAPTURE_COMPLETE, None)
            transition(SpectrumProductionState.MUXING, None)
            raise SpectrumProductionError("Synthetic mux failure.")
        transition(SpectrumProductionState.CAPTURE_COMPLETE, None)
        transition(SpectrumProductionState.MUXING, None)
        transition(SpectrumProductionState.VALIDATING, None)
        timing = SpectrumMasterTiming.model_validate(manifest["masterTiming"])
        return SpectrumProductionResult(
            synchronization=_synchronization(timing.master_duration_seconds),
            artifacts=[],
            validation=_validation(timing),
            provider="ffmpeg-gfxcapture",
            encoder="h264_nvenc",
            captured_frames=11_810,
            dropped_frames=None,
            capture_duration_seconds=timing.master_duration_seconds + 0.2,
        )


def _wait_for_state(
    manager: SpectrumProductionManager,
    job_id: str,
    expected: SpectrumProductionState,
) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        manifest_path = manager.paths.jobs_root / job_id / "manifest.json"
        if manifest_path.is_file() and f'"state": "{expected.value}"' in manifest_path.read_text(encoding="utf-8"):
            return
        time.sleep(0.02)
    raise AssertionError(f"job did not reach {expected.value}")


def _production_manager_fixture(
    tmp_path: Path,
    executor: Any,
) -> tuple[SpectrumProductionManager, str]:
    repository_root, data_root, inspection = _build_fixture(tmp_path)
    settings = settings_for(data_root)
    paths = SpectrumPaths(repository_root, data_root)
    outcome = _inspect(repository_root, data_root, inspection)
    job = prepare_workspace(
        paths,
        outcome,
        SpectrumWorkspacePrepareRequest(mode="production"),
    )
    manager = SpectrumProductionManager(
        settings,
        paths,
        inspection,
        executor=executor,
    )
    ready = manager.preflight(job.job_id)
    assert ready.state is SpectrumProductionState.CAPTURE_READY
    return manager, job.job_id


def test_dual_duration_model_and_post_grid_tail_boundaries() -> None:
    timing = resolve_master_timing(196.620)
    assert timing.grid_duration_seconds == 192
    assert timing.master_duration_seconds == 196.620
    assert timing.tail_duration_seconds == pytest.approx(4.620)
    assert timing.final_fade_start_seconds == pytest.approx(192.620)
    assert resolve_master_timing(192).tail_duration_seconds == 0
    with pytest.raises(SpectrumProductionError):
        resolve_master_timing(191.999)

    preset = load_design_preset(CANONICAL_DESIGN_PATH)
    assert resolve_state_at_milliseconds(preset, 191_999, 196.620).section_id == "outro"
    assert resolve_state_at_milliseconds(preset, 192_000, 196.620).section_id == "post-grid-tail"
    assert resolve_state_at_milliseconds(preset, 196_619, 196.620).section_id == "post-grid-tail"
    assert resolve_state_at_milliseconds(preset, 196_620, 196.620).section_id == "end"


def test_production_rejects_preview_overrides_and_invalid_state_transitions() -> None:
    with pytest.raises(ValidationError):
        SpectrumWorkspacePrepareRequest(mode="production", preview_section="main")
    validate_production_transition(
        SpectrumProductionState.CAPTURE_COMPLETE,
        SpectrumProductionState.MUXING,
    )
    with pytest.raises(SpectrumProductionError):
        validate_production_transition(
            SpectrumProductionState.COMPLETE,
            SpectrumProductionState.CAPTURING,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"mode": "shape", "shapeA": {"shapeId": "torus"}},
        {"mode": "morph", "shapeA": {"shapeId": "torus"}, "shapeB": {"shapeId": "trefoil-knot"}, "morphProgress": 0.5},
        {"mode": "section", "section": "post-grid-tail"},
        {"mode": "lab", "shapeA": {"shapeId": "superformula"}},
    ],
)
def test_production_rejects_every_geometry_developer_preview_override(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="production workspaces cannot use preview overrides"):
        SpectrumWorkspacePrepareRequest.model_validate({"mode": "production", "generativePreview": override})


def test_output_filename_keeps_legacy_geometry_recovery_separate_from_milestone_3_7() -> None:
    revision = "scattered-geometry-first-3.7"
    assert select_output_filename("static-structured") == FINAL_OUTPUT_FILENAME
    assert select_output_filename("static-structured", revision) == FINAL_OUTPUT_FILENAME
    assert select_output_filename("generative-geometry") == GENERATIVE_OUTPUT_FILENAME
    assert select_output_filename("generative-geometry", None) == GENERATIVE_OUTPUT_FILENAME
    assert select_output_filename("generative-geometry", revision) == GEOMETRY_FIRST_OUTPUT_FILENAME
    assert GENERATIVE_OUTPUT_FILENAME == "dj-wazahaka-scattered-wzhk-generative-geometry-milestone-3-6.mp4"
    assert GEOMETRY_FIRST_OUTPUT_FILENAME != GENERATIVE_OUTPUT_FILENAME
    assert "milestone-3-7" in GEOMETRY_FIRST_OUTPUT_FILENAME


def test_capture_mux_playback_and_review_commands_are_bounded() -> None:
    capture = build_capture_command(
        ffmpeg_path="ffmpeg",
        window_handle=4242,
        output_path=Path("capture.mkv"),
        encoder="h264_nvenc",
    )
    assert capture[0] == "ffmpeg"
    assert any("gfxcapture=hwnd=4242" in argument for argument in capture)
    assert any("capture_cursor=0" in argument for argument in capture)
    assert any("hwdownload,format=bgra,format=yuv420p" in argument for argument in capture)
    assert capture[capture.index("-progress") + 1] == "pipe:1"
    assert "h264_nvenc" in capture
    assert "-an" in capture
    assert "-fps_mode" in capture and "cfr" in capture
    composite = build_composite_capture_command(
        ffmpeg_path="ffmpeg",
        background_window_handle=5151,
        foreground_window_handle=6262,
        output_path=Path("geometry-capture.mkv"),
        encoder="h264_nvenc",
    )
    composite_filter = composite[composite.index("-filter_complex") + 1]
    assert "gfxcapture=hwnd=5151" in composite_filter
    assert "gfxcapture=hwnd=6262" in composite_filter
    assert "premultiplied=1" in composite_filter
    assert "overlay=x=0:y=0:alpha=premultiplied" in composite_filter
    chroma = build_chroma_composite_capture_command(
        ffmpeg_path="ffmpeg",
        background_window_handle=7171,
        foreground_window_handle=8181,
        output_path=Path("chroma-capture.mkv"),
        encoder="h264_nvenc",
    )
    chroma_filter = chroma[chroma.index("-filter_complex") + 1]
    assert "gfxcapture=hwnd=7171" in chroma_filter
    assert "gfxcapture=hwnd=8181" in chroma_filter
    assert "colorkey=color=black" in chroma_filter
    assert "overlay=x=0:y=0:alpha=straight" in chroma_filter
    monitor = build_monitor_capture_command(
        ffmpeg_path="ffmpeg",
        monitor_index=0,
        output_path=Path("monitor-capture.mkv"),
        encoder="h264_nvenc",
    )
    monitor_filter = monitor[monitor.index("-filter_complex") + 1]
    assert "gfxcapture=monitor_idx=0" in monitor_filter
    assert "capture_cursor=0" in monitor_filter
    assert build_playback_command("ffplay", Path("master.wav"))[-1] == "master.wav"

    mux = build_mux_command(
        ffmpeg_path="ffmpeg",
        capture_path=Path("capture.mkv"),
        master_path=Path("master.wav"),
        output_path=Path("final.mp4"),
        start_offset_seconds=0.125,
        master_duration_seconds=196.620,
        encoder="h264_nvenc",
    )
    assert mux[mux.index("-map") + 1] == "0:v:0"
    assert "1:a:0" in mux
    assert "196.620000" in mux
    assert "-shortest" not in mux
    with pytest.raises(SpectrumProductionError):
        build_mux_command(
            ffmpeg_path="ffmpeg",
            capture_path=Path("capture.mkv"),
            master_path=Path("master.wav"),
            output_path=Path("final.mp4"),
            start_offset_seconds=-1,
            master_duration_seconds=196.620,
            encoder="libx264",
        )

    timestamps = review_frame_timestamps(196.620)
    assert [timestamp for _label, timestamp in timestamps] == [
        10,
        63,
        65,
        120,
        175,
        177,
        191,
        193,
        196.12,
    ]


def test_review_frame_labels_cover_exact_approved_master_and_post_grid_tail() -> None:
    master_duration = 196.61979591836734
    frames = review_frame_timestamps(master_duration)
    assert [label for label, _timestamp in frames] == [
        "intro-0010",
        "intro-end-0103",
        "main-0105",
        "main-mid-0200",
        "main-end-0255",
        "outro-0257",
        "grid-end-0311",
        "post-grid-tail-0313",
        "near-eof",
    ]
    assert [timestamp for _label, timestamp in frames[:-1]] == [10, 63, 65, 120, 175, 177, 191, 193]
    assert frames[-1][1] == pytest.approx(master_duration - 0.5, abs=1e-9)
    assert all(0 <= timestamp < master_duration for _label, timestamp in frames)
    final_path = Path("output") / GEOMETRY_FIRST_OUTPUT_FILENAME
    for label, timestamp in frames:
        command = build_review_frame_command(
            ffmpeg_path="ffmpeg",
            final_video_path=final_path,
            timestamp_seconds=timestamp,
            output_path=Path("output") / "review-frames" / f"{label}.png",
        )
        assert command[command.index("-i") + 1] == str(final_path)
        assert command[command.index("-ss") + 1] == f"{timestamp:.3f}"
        assert command[command.index("-frames:v") + 1] == "1"
        assert command[command.index("-vf") + 1] == "scale=960:540:flags=lanczos"


def test_capture_command_rejects_an_invalid_window_handle() -> None:
    with pytest.raises(SpectrumProductionError):
        build_capture_command(
            ffmpeg_path="ffmpeg",
            window_handle=0,
            output_path=Path("capture.mkv"),
            encoder="libx264",
        )
    with pytest.raises(SpectrumProductionError):
        build_composite_capture_command(
            ffmpeg_path="ffmpeg",
            background_window_handle=4242,
            foreground_window_handle=4242,
            output_path=Path("capture.mkv"),
            encoder="libx264",
        )
    with pytest.raises(SpectrumProductionError):
        build_monitor_capture_command(
            ffmpeg_path="ffmpeg",
            monitor_index=-1,
            output_path=Path("capture.mkv"),
            encoder="libx264",
        )
    with pytest.raises(SpectrumProductionError):
        build_chroma_composite_capture_command(
            ffmpeg_path="ffmpeg",
            background_window_handle=5151,
            foreground_window_handle=5151,
            output_path=Path("capture.mkv"),
            encoder="libx264",
        )
    review = build_review_frame_command(
        ffmpeg_path="ffmpeg",
        final_video_path=Path("final.mp4"),
        timestamp_seconds=193,
        output_path=Path("tail.png"),
    )
    assert review[-1] == "tail.png"


def test_final_probe_validation_accepts_exact_media_and_rejects_truncation() -> None:
    timing = resolve_master_timing(196.620)
    accepted = validate_final_media(_valid_probe(), timing)
    assert accepted.valid is True
    truncated = _valid_probe(192.0)
    rejected = validate_final_media(truncated, timing)
    assert rejected.valid is False
    assert next(check for check in rejected.checks if check.id == "master-duration").passed is False


def test_capture_capability_shapes_cover_available_and_missing() -> None:
    available = CaptureProviderCapabilities(
        available=True,
        supports_window_capture=True,
        supports_constant_frame_rate=True,
        encoder="h264_nvenc",
        hardware_acceleration_verified=True,
        detail="Available.",
    )
    missing = CaptureProviderCapabilities(
        available=False,
        supports_window_capture=False,
        supports_constant_frame_rate=False,
        detail="Missing.",
    )
    assert available.hardware_acceleration_verified is True
    assert missing.encoder is None


def test_production_manager_completes_and_cancels_with_mocked_executor(tmp_path: Path) -> None:
    manager, job_id = _production_manager_fixture(tmp_path / "complete", SuccessfulExecutor())
    manager.start(job_id)
    _wait_for_state(manager, job_id, SpectrumProductionState.COMPLETE)
    complete = manager.paths.jobs_root / job_id / "manifest.json"
    assert '"droppedFrames": null' in complete.read_text(encoding="utf-8")

    cancelled_manager, cancelled_id = _production_manager_fixture(
        tmp_path / "cancel",
        BlockingExecutor(),
    )
    cancelled_manager.start(cancelled_id)
    _wait_for_state(cancelled_manager, cancelled_id, SpectrumProductionState.CAPTURING)
    cancelled_manager.cancel(cancelled_id, "Operator stopped the synthetic test.")
    _wait_for_state(cancelled_manager, cancelled_id, SpectrumProductionState.CANCELLED)


def test_retry_reuses_valid_capture_after_mux_failure(tmp_path: Path) -> None:
    executor = RetryExecutor()
    manager, job_id = _production_manager_fixture(tmp_path, executor)
    manager.start(job_id)
    _wait_for_state(manager, job_id, SpectrumProductionState.FAILED)
    manager.preflight(job_id)
    manager.start(job_id)
    _wait_for_state(manager, job_id, SpectrumProductionState.COMPLETE)
    assert executor.attempts == 2
    assert executor.capture_calls == 1

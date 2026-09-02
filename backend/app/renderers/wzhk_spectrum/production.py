from __future__ import annotations

import json
import math
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from ...subprocess_utils import ProcessTimedOut, run_process_bounded

GRID_DURATION_SECONDS = 192.0
MAX_POST_GRID_TAIL_SECONDS = 60.0
FINAL_DURATION_TOLERANCE_SECONDS = 0.150
FINAL_FPS_TOLERANCE = 0.001
FINAL_OUTPUT_FILENAME = "dj-wazahaka-scattered-wzhk-spectrum-visualizer.mp4"
GENERATIVE_OUTPUT_FILENAME = (
    "dj-wazahaka-scattered-wzhk-generative-geometry-milestone-3-6.mp4"
)
GEOMETRY_FIRST_OUTPUT_FILENAME = (
    "dj-wazahaka-scattered-wzhk-generative-geometry-milestone-3-7.mp4"
)
CAPTURE_FILENAME = "scattered-visual-capture.mkv"


def select_output_filename(background_mode: str, composition_revision: str | None = None) -> str:
    """Name new compositions without relabeling historical 3.6/recovery output."""

    if background_mode != "generative-geometry":
        return FINAL_OUTPUT_FILENAME
    if composition_revision == "scattered-geometry-first-3.7":
        return GEOMETRY_FIRST_OUTPUT_FILENAME
    return GENERATIVE_OUTPUT_FILENAME


class SpectrumProductionError(RuntimeError):
    """Safe production-domain failure."""


class SpectrumProductionState(StrEnum):
    WORKSPACE_READY = "WORKSPACE_READY"
    PREVIEW_READY = "PREVIEW_READY"
    CAPTURE_PREFLIGHT = "CAPTURE_PREFLIGHT"
    CAPTURE_READY = "CAPTURE_READY"
    CAPTURING = "CAPTURING"
    CAPTURE_COMPLETE = "CAPTURE_COMPLETE"
    MUXING = "MUXING"
    VALIDATING = "VALIDATING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SpectrumProductionAvailability(StrEnum):
    READY_FOR_PREVIEW = "READY_FOR_PREVIEW"
    READY_FOR_CAPTURE = "READY_FOR_CAPTURE"
    MISSING_RAINMETER = "MISSING_RAINMETER"
    MISSING_FFMPEG = "MISSING_FFMPEG"
    MISSING_CAPTURE_PROVIDER = "MISSING_CAPTURE_PROVIDER"
    INVALID_WORKSPACE = "INVALID_WORKSPACE"
    MISSING_MASTER = "MISSING_MASTER"
    INVALID_MASTER_DURATION = "INVALID_MASTER_DURATION"


class SpectrumArtifactType(StrEnum):
    WORKSPACE_MANIFEST = "workspace-manifest"
    GENERATED_SKIN = "generated-skin"
    CAPTURE_MANIFEST = "capture-manifest"
    CAPTURE_INTERMEDIATE = "capture-intermediate"
    CAPTURE_LOG = "capture-log"
    MUX_MANIFEST = "mux-manifest"
    FINAL_VIDEO = "final-video"
    VALIDATION_REPORT = "validation-report"
    REVIEW_FRAME = "review-frame"
    GEOMETRY_RUNTIME_REPORT = "geometry-runtime-report"
    VISUAL_SANITY_REPORT = "visual-sanity-report"
    BRANDING_STABILIZATION_MANIFEST = "branding-stabilization-manifest"
    AUDIO_COPY_EVIDENCE = "audio-copy-evidence"
    COMPARISON_MANIFEST = "comparison-manifest"
    COMPARISON_FRAME = "comparison-frame"


class ProductionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        allow_inf_nan=False,
        use_enum_values=False,
    )


class SpectrumMasterTiming(ProductionModel):
    grid_duration_seconds: float = Field(ge=GRID_DURATION_SECONDS, le=GRID_DURATION_SECONDS)
    master_duration_seconds: float = Field(ge=GRID_DURATION_SECONDS)
    tail_duration_seconds: float = Field(ge=0, le=MAX_POST_GRID_TAIL_SECONDS)
    configured_final_fade_seconds: float = Field(gt=0, le=16)
    final_fade_start_seconds: float = Field(ge=GRID_DURATION_SECONDS)

    @model_validator(mode="after")
    def validate_derived_timing(self) -> SpectrumMasterTiming:
        expected_tail = self.master_duration_seconds - self.grid_duration_seconds
        if not math.isclose(self.tail_duration_seconds, expected_tail, abs_tol=0.001):
            raise ValueError("tail duration must equal master duration minus grid duration")
        expected_fade = max(
            self.grid_duration_seconds,
            self.master_duration_seconds - self.configured_final_fade_seconds,
        )
        if not math.isclose(self.final_fade_start_seconds, expected_fade, abs_tol=0.001):
            raise ValueError("final fade must be relative to the resolved master EOF")
        return self


class SpectrumArtifact(ProductionModel):
    artifact_type: SpectrumArtifactType
    relative_path: str = Field(
        pattern=r"^(?:manifest\.json|skin/.+|capture/.+|logs/.+|output/.+)$",
        max_length=400,
    )
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    created_state: SpectrumProductionState
    provenance: str = Field(min_length=1, max_length=240)
    timestamp_seconds: float | None = Field(default=None, ge=0)


class CaptureProviderCapabilities(ProductionModel):
    provider_id: Literal["ffmpeg-gfxcapture"] = "ffmpeg-gfxcapture"
    display_name: Literal["FFmpeg Windows Graphics Capture"] = "FFmpeg Windows Graphics Capture"
    available: bool
    supports_window_capture: bool
    supports_constant_frame_rate: bool
    supports_alpha_composition: bool = False
    supports_monitor_capture: bool = False
    supports_chroma_composition: bool = False
    crash_resilient_container: Literal["matroska"] = "matroska"
    encoder: Literal["h264_nvenc", "libx264"] | None = None
    hardware_acceleration_verified: bool = False
    detail: str = Field(min_length=1, max_length=500)


class GeometryCapabilityEvidence(ProductionModel):
    state: Literal[
        "READY",
        "WEBGL2_UNAVAILABLE",
        "GPU_RENDERER_UNAVAILABLE",
        "SHADER_COMPILE_FAILED",
        "PERFORMANCE_INSUFFICIENT",
        "BROWSER_UNAVAILABLE",
    ]
    webgl2: bool | None = None
    gpu_renderer: str | None = Field(default=None, max_length=240)
    shader_compiled: bool | None = None
    performance_measured: bool
    performance_sufficient: bool | None = None
    renderer_fps: float | None = Field(default=None, ge=0, le=1_000)
    average_frame_time_ms: float | None = Field(default=None, ge=0, le=10_000)
    point_count: int | None = Field(default=None, ge=1, le=1_000_000)
    detail: str = Field(min_length=1, max_length=500)


class GeometryRuntimeTelemetry(ProductionModel):
    sample_count: int = Field(ge=1)
    timeline_seconds: float | None = Field(default=None, ge=0)
    section: str | None = Field(default=None, max_length=40)
    source_shape: str | None = Field(default=None, max_length=40)
    target_shape: str | None = Field(default=None, max_length=40)
    morph: float | None = Field(default=None, ge=0, le=1)
    renderer_fps: float = Field(ge=0, le=1_000)
    average_frame_time_ms: float = Field(ge=0, le=10_000)
    p95_frame_time_ms: float | None = Field(default=None, ge=0, le=10_000)
    average_render_time_ms: float | None = Field(default=None, ge=0, le=10_000)
    maximum_render_time_ms: float | None = Field(default=None, ge=0, le=10_000)
    rendered_frames: int | None = Field(default=None, ge=0)
    dropped_frames: int | None = Field(default=None, ge=0)
    target_fps: float | None = Field(default=None, ge=1, le=1_000)
    point_count: int = Field(ge=1, le=1_000_000)
    canvas_width: int | None = Field(default=None, ge=1, le=7_680)
    canvas_height: int | None = Field(default=None, ge=1, le=4_320)
    gpu_renderer: str | None = Field(default=None, max_length=240)
    measured_at_monotonic_seconds: float = Field(ge=0)


class CapturePreflightResult(ProductionModel):
    availability: SpectrumProductionAvailability
    ready: bool
    provider: CaptureProviderCapabilities
    timing: SpectrumMasterTiming | None = None
    rainmeter_path_resolved: bool
    ffmpeg_path_resolved: bool
    ffprobe_path_resolved: bool
    playback_path_resolved: bool
    workspace_valid: bool
    master_valid: bool
    geometry_capability: GeometryCapabilityEvidence | None = None
    static_fallback_available: bool = True
    alpha_composition_ready: bool = False
    monitor_composition_ready: bool = False
    chroma_composition_ready: bool = False
    single_browser_composition_ready: bool = False
    operator_notice: str = Field(min_length=1, max_length=1000)
    warnings: list[str] = Field(default_factory=list)


class CaptureSynchronization(ProductionModel):
    method: Literal[
        "owned-playback-process-ffmpeg-progress-clock",
        "owned-browser-audio-ffmpeg-progress-clock",
    ]
    capture_started_monotonic_seconds: float = Field(ge=0)
    master_zero_monotonic_seconds: float = Field(ge=0)
    capture_stopped_monotonic_seconds: float = Field(ge=0)
    measured_start_offset_seconds: float = Field(ge=0)
    measured_end_offset_seconds: float
    correction_applied_seconds: float = Field(ge=0)
    precision: Literal["host-monotonic-process-boundary"]


class MediaStreamProbe(ProductionModel):
    codec_type: Literal["video", "audio"]
    codec_name: str = Field(min_length=1, max_length=80)
    width: int | None = Field(default=None, gt=0, le=7680)
    height: int | None = Field(default=None, gt=0, le=4320)
    frame_rate: float | None = Field(default=None, gt=0, le=240)
    duration_seconds: float | None = Field(default=None, gt=0)
    frame_count: int | None = Field(default=None, gt=0)


class MediaProbeSummary(ProductionModel):
    duration_seconds: float = Field(gt=0)
    size_bytes: int = Field(gt=0)
    streams: list[MediaStreamProbe] = Field(min_length=1)

    def video(self) -> MediaStreamProbe | None:
        return next((stream for stream in self.streams if stream.codec_type == "video"), None)

    def audio(self) -> MediaStreamProbe | None:
        return next((stream for stream in self.streams if stream.codec_type == "audio"), None)


class SpectrumValidationCheck(ProductionModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,79}$")
    passed: bool
    measured: str = Field(min_length=1, max_length=200)
    expected: str = Field(min_length=1, max_length=200)


class SpectrumValidationReport(ProductionModel):
    valid: bool
    final_video: MediaProbeSummary
    timing: SpectrumMasterTiming
    checks: list[SpectrumValidationCheck] = Field(min_length=1)


class CaptureProvider(Protocol):
    def capabilities(self) -> CaptureProviderCapabilities: ...

    def preflight(self) -> CaptureProviderCapabilities: ...

    def prepare(
        self,
        job_root: Path,
        window_handle: int,
        foreground_window_handle: int | None = None,
    ) -> list[str]: ...

    def start(self) -> None: ...

    def stop(self) -> Path: ...

    def cancel(self) -> None: ...

    def artifact(self) -> Path | None: ...


VALID_PRODUCTION_TRANSITIONS: dict[SpectrumProductionState, set[SpectrumProductionState]] = {
    SpectrumProductionState.WORKSPACE_READY: {
        SpectrumProductionState.PREVIEW_READY,
        SpectrumProductionState.CAPTURE_PREFLIGHT,
        SpectrumProductionState.FAILED,
        SpectrumProductionState.CANCELLED,
    },
    SpectrumProductionState.PREVIEW_READY: {
        SpectrumProductionState.CAPTURE_PREFLIGHT,
        SpectrumProductionState.CANCELLED,
    },
    SpectrumProductionState.CAPTURE_PREFLIGHT: {
        SpectrumProductionState.CAPTURE_READY,
        SpectrumProductionState.FAILED,
        SpectrumProductionState.CANCELLED,
    },
    SpectrumProductionState.CAPTURE_READY: {
        SpectrumProductionState.CAPTURING,
        SpectrumProductionState.CAPTURE_COMPLETE,
        SpectrumProductionState.CANCELLED,
    },
    SpectrumProductionState.CAPTURING: {
        SpectrumProductionState.CAPTURE_COMPLETE,
        SpectrumProductionState.FAILED,
        SpectrumProductionState.CANCELLED,
    },
    SpectrumProductionState.CAPTURE_COMPLETE: {
        SpectrumProductionState.MUXING,
        SpectrumProductionState.FAILED,
        SpectrumProductionState.CANCELLED,
    },
    SpectrumProductionState.MUXING: {
        SpectrumProductionState.VALIDATING,
        SpectrumProductionState.FAILED,
        SpectrumProductionState.CANCELLED,
    },
    SpectrumProductionState.VALIDATING: {
        SpectrumProductionState.COMPLETE,
        SpectrumProductionState.FAILED,
        SpectrumProductionState.CANCELLED,
    },
    SpectrumProductionState.FAILED: {
        SpectrumProductionState.CAPTURE_PREFLIGHT,
        SpectrumProductionState.CAPTURE_READY,
        SpectrumProductionState.MUXING,
        SpectrumProductionState.VALIDATING,
    },
    SpectrumProductionState.CANCELLED: {
        SpectrumProductionState.CAPTURE_PREFLIGHT,
        SpectrumProductionState.CAPTURE_READY,
        SpectrumProductionState.MUXING,
    },
    SpectrumProductionState.COMPLETE: set(),
}


def validate_production_transition(
    current: SpectrumProductionState,
    target: SpectrumProductionState,
) -> None:
    if target not in VALID_PRODUCTION_TRANSITIONS[current]:
        raise SpectrumProductionError(
            f"The Spectrum production transition from {current.value} to {target.value} is invalid."
        )


def resolve_master_timing(
    master_duration_seconds: float,
    *,
    grid_duration_seconds: float = GRID_DURATION_SECONDS,
    final_fade_seconds: float = 4.0,
) -> SpectrumMasterTiming:
    if not math.isfinite(master_duration_seconds) or master_duration_seconds <= 0:
        raise SpectrumProductionError("The approved master duration is invalid.")
    if master_duration_seconds < grid_duration_seconds:
        raise SpectrumProductionError("The approved master is shorter than the 192-second musical grid.")
    tail = master_duration_seconds - grid_duration_seconds
    if tail > MAX_POST_GRID_TAIL_SECONDS:
        raise SpectrumProductionError("The approved master tail exceeds the configured production safety limit.")
    return SpectrumMasterTiming(
        grid_duration_seconds=grid_duration_seconds,
        master_duration_seconds=master_duration_seconds,
        tail_duration_seconds=tail,
        configured_final_fade_seconds=final_fade_seconds,
        final_fade_start_seconds=max(
            grid_duration_seconds,
            master_duration_seconds - final_fade_seconds,
        ),
    )


def _parse_rate(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw or raw in {"0/0", "N/A"}:
        return None
    try:
        if "/" in raw:
            numerator, denominator = raw.split("/", 1)
            rate = float(numerator) / float(denominator)
        else:
            rate = float(raw)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_ffprobe_payload(payload: dict[str, Any], *, size_bytes: int) -> MediaProbeSummary:
    format_data = payload.get("format")
    streams_data = payload.get("streams")
    if not isinstance(format_data, dict) or not isinstance(streams_data, list):
        raise SpectrumProductionError("ffprobe returned an incomplete media description.")
    duration = _positive_float(format_data.get("duration"))
    if duration is None:
        raise SpectrumProductionError("ffprobe did not report a valid media duration.")
    streams: list[MediaStreamProbe] = []
    for raw_stream in streams_data:
        if not isinstance(raw_stream, dict):
            continue
        codec_type = raw_stream.get("codec_type")
        codec_name = str(raw_stream.get("codec_name") or "").strip()
        if codec_type not in {"audio", "video"} or not codec_name:
            continue
        streams.append(
            MediaStreamProbe(
                codec_type=codec_type,
                codec_name=codec_name,
                width=_positive_int(raw_stream.get("width")) if codec_type == "video" else None,
                height=_positive_int(raw_stream.get("height")) if codec_type == "video" else None,
                frame_rate=_parse_rate(raw_stream.get("avg_frame_rate")) if codec_type == "video" else None,
                duration_seconds=_positive_float(raw_stream.get("duration")),
                frame_count=(
                    _positive_int(raw_stream.get("nb_read_frames"))
                    or _positive_int(raw_stream.get("nb_frames"))
                ),
            )
        )
    if not streams:
        raise SpectrumProductionError("ffprobe did not report a usable media stream.")
    return MediaProbeSummary(
        duration_seconds=duration,
        size_bytes=size_bytes,
        streams=streams,
    )


def probe_media_file(
    ffprobe_path: str,
    path: Path,
    *,
    count_frames: bool = True,
    timeout_seconds: float = 120,
) -> MediaProbeSummary:
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise SpectrumProductionError("The media artifact is unavailable.") from exc
    if size_bytes <= 0:
        raise SpectrumProductionError("The media artifact is empty.")
    args = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=codec_type,codec_name,width,height,avg_frame_rate,duration,nb_frames,nb_read_frames",
    ]
    if count_frames:
        args.append("-count_frames")
    args.extend([
        "-of",
        "json",
        str(path),
    ])
    try:
        result = run_process_bounded(
            args,
            timeout_seconds=timeout_seconds,
            stdout_limit=1_000_000,
            stderr_limit=32_000,
        )
    except (FileNotFoundError, ProcessTimedOut) as exc:
        raise SpectrumProductionError("ffprobe could not inspect the media artifact.") from exc
    if result.returncode != 0 or result.stdout_exceeded or result.stderr_exceeded:
        raise SpectrumProductionError("ffprobe rejected the media artifact.")
    try:
        payload = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpectrumProductionError("ffprobe returned unreadable media data.") from exc
    if not isinstance(payload, dict):
        raise SpectrumProductionError("ffprobe returned an invalid media description.")
    return parse_ffprobe_payload(payload, size_bytes=size_bytes)


def inspect_ffmpeg_capture_capabilities(
    ffmpeg_path: str,
) -> CaptureProviderCapabilities:
    try:
        filters = run_process_bounded(
            [ffmpeg_path, "-hide_banner", "-filters"],
            timeout_seconds=15,
            stdout_limit=256_000,
            stderr_limit=256_000,
        )
        encoders = run_process_bounded(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            timeout_seconds=15,
            stdout_limit=512_000,
            stderr_limit=512_000,
        )
        gfxcapture_help = run_process_bounded(
            [ffmpeg_path, "-hide_banner", "-h", "filter=gfxcapture"],
            timeout_seconds=15,
            stdout_limit=256_000,
            stderr_limit=256_000,
        )
        overlay_help = run_process_bounded(
            [ffmpeg_path, "-hide_banner", "-h", "filter=overlay"],
            timeout_seconds=15,
            stdout_limit=256_000,
            stderr_limit=256_000,
        )
    except (FileNotFoundError, ProcessTimedOut):
        return CaptureProviderCapabilities(
            available=False,
            supports_window_capture=False,
            supports_constant_frame_rate=False,
            detail="FFmpeg capture capability could not be inspected.",
        )
    filter_text = (filters.stdout + filters.stderr).decode("utf-8", errors="ignore")
    encoder_text = (encoders.stdout + encoders.stderr).decode("utf-8", errors="ignore")
    gfxcapture_help_text = (gfxcapture_help.stdout + gfxcapture_help.stderr).decode(
        "utf-8", errors="ignore"
    )
    overlay_help_text = (overlay_help.stdout + overlay_help.stderr).decode(
        "utf-8", errors="ignore"
    )
    gfxcapture = filters.returncode == 0 and "gfxcapture" in filter_text
    alpha_composition = (
        gfxcapture
        and gfxcapture_help.returncode == 0
        and "premultiplied" in gfxcapture_help_text
        and "output_fmt" in gfxcapture_help_text
        and overlay_help.returncode == 0
        and "premultiplied" in overlay_help_text
    )
    monitor_capture = gfxcapture and "monitor_idx" in gfxcapture_help_text
    chroma_composition = gfxcapture and "colorkey" in filter_text and "overlay" in filter_text
    nvenc = encoders.returncode == 0 and "h264_nvenc" in encoder_text
    x264 = encoders.returncode == 0 and "libx264" in encoder_text
    encoder: Literal["h264_nvenc", "libx264"] | None = (
        "h264_nvenc" if nvenc else "libx264" if x264 else None
    )
    available = gfxcapture and encoder is not None
    return CaptureProviderCapabilities(
        available=available,
        supports_window_capture=gfxcapture,
        supports_constant_frame_rate=available,
        supports_alpha_composition=alpha_composition,
        supports_monitor_capture=monitor_capture,
        supports_chroma_composition=chroma_composition,
        encoder=encoder,
        hardware_acceleration_verified=nvenc,
        detail=(
            f"FFmpeg Graphics Capture is available with {encoder}; monitor capture is "
            f"{'available' if monitor_capture else 'unavailable'}, black-key composition is "
            f"{'available' if chroma_composition else 'unavailable'}, and "
            f"premultiplied-alpha composition is "
            f"{'available' if alpha_composition else 'unavailable'}."
            if available
            else "FFmpeg lacks the Graphics Capture window filter or a supported H.264 encoder."
        ),
    )


def build_capture_command(
    *,
    ffmpeg_path: str,
    window_handle: int,
    output_path: Path,
    encoder: Literal["h264_nvenc", "libx264"],
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
) -> list[str]:
    if window_handle <= 0:
        raise SpectrumProductionError("The Rainmeter capture window handle is invalid.")
    encoder_args = (
        ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "14", "-b:v", "0"]
        if encoder == "h264_nvenc"
        else ["-c:v", "libx264", "-preset", "fast", "-crf", "14"]
    )
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
        "-stats_period",
        "0.05",
        "-progress",
        "pipe:1",
        "-filter_complex",
        (
            f"gfxcapture=hwnd={window_handle}:capture_cursor=0:capture_border=0:"
            f"display_border=0:max_framerate={fps}:width={width}:height={height}:"
            "resize_mode=scale,hwdownload,format=bgra,format=yuv420p"
        ),
        "-an",
        *encoder_args,
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-fps_mode",
        "cfr",
        "-g",
        str(fps * 2),
        "-f",
        "matroska",
        str(output_path),
    ]


def build_composite_capture_command(
    *,
    ffmpeg_path: str,
    background_window_handle: int,
    foreground_window_handle: int,
    output_path: Path,
    encoder: Literal["h264_nvenc", "libx264"],
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
) -> list[str]:
    """Capture an opaque browser and premultiplied-alpha Rainmeter foreground.

    The existing single-window command remains the compatibility path.  This
    command is used only after a job-specific WebGL/Rainmeter composition has
    passed capability preflight.
    """
    if background_window_handle <= 0 or foreground_window_handle <= 0:
        raise SpectrumProductionError("The generative capture window handles are invalid.")
    if background_window_handle == foreground_window_handle:
        raise SpectrumProductionError("The generative capture windows must be distinct.")
    encoder_args = (
        [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "14",
            "-b:v",
            "0",
        ]
        if encoder == "h264_nvenc"
        else ["-c:v", "libx264", "-preset", "fast", "-crf", "14"]
    )
    background = (
        f"gfxcapture=hwnd={background_window_handle}:capture_cursor=0:"
        f"capture_border=0:display_border=0:max_framerate={fps}:"
        f"width={width}:height={height}:resize_mode=scale:output_fmt=bgra,"
        "hwdownload,format=bgra[geometry]"
    )
    foreground = (
        f"gfxcapture=hwnd={foreground_window_handle}:capture_cursor=0:"
        f"capture_border=0:display_border=0:max_framerate={fps}:"
        f"width={width}:height={height}:resize_mode=scale:output_fmt=bgra:"
        "premultiplied=1,hwdownload,format=bgra[foreground]"
    )
    composition = (
        "[geometry][foreground]overlay=x=0:y=0:alpha=premultiplied:"
        "format=auto,format=yuv420p"
    )
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
        "-stats_period",
        "0.05",
        "-progress",
        "pipe:1",
        "-filter_complex",
        f"{background};{foreground};{composition}",
        "-an",
        *encoder_args,
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-fps_mode",
        "cfr",
        "-g",
        str(fps * 2),
        "-f",
        "matroska",
        str(output_path),
    ]


def build_monitor_capture_command(
    *,
    ffmpeg_path: str,
    monitor_index: int,
    output_path: Path,
    encoder: Literal["h264_nvenc", "libx264"],
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
) -> list[str]:
    if monitor_index < 0 or monitor_index > 32:
        raise SpectrumProductionError("The geometry capture monitor index is invalid.")
    encoder_args = (
        [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "14",
            "-b:v",
            "0",
        ]
        if encoder == "h264_nvenc"
        else ["-c:v", "libx264", "-preset", "fast", "-crf", "14"]
    )
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
        "-stats_period",
        "0.05",
        "-progress",
        "pipe:1",
        "-filter_complex",
        (
            f"gfxcapture=monitor_idx={monitor_index}:capture_cursor=0:"
            f"capture_border=0:display_border=0:max_framerate={fps}:"
            f"width={width}:height={height}:resize_mode=scale,"
            "hwdownload,format=bgra,format=yuv420p"
        ),
        "-an",
        *encoder_args,
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-fps_mode",
        "cfr",
        "-g",
        str(fps * 2),
        "-f",
        "matroska",
        str(output_path),
    ]


def build_chroma_composite_capture_command(
    *,
    ffmpeg_path: str,
    background_window_handle: int,
    foreground_window_handle: int,
    output_path: Path,
    encoder: Literal["h264_nvenc", "libx264"],
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
) -> list[str]:
    if background_window_handle <= 0 or foreground_window_handle <= 0:
        raise SpectrumProductionError("The chroma-composition window handles are invalid.")
    if background_window_handle == foreground_window_handle:
        raise SpectrumProductionError("The chroma-composition windows must be distinct.")
    encoder_args = (
        [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "14",
            "-b:v",
            "0",
        ]
        if encoder == "h264_nvenc"
        else ["-c:v", "libx264", "-preset", "fast", "-crf", "14"]
    )
    background = (
        f"gfxcapture=hwnd={background_window_handle}:capture_cursor=0:"
        f"capture_border=0:display_border=0:max_framerate={fps}:"
        f"width={width}:height={height}:resize_mode=scale:output_fmt=bgra,"
        "hwdownload,format=bgra[geometry]"
    )
    foreground = (
        f"gfxcapture=hwnd={foreground_window_handle}:capture_cursor=0:"
        f"capture_border=0:display_border=0:max_framerate={fps}:"
        f"width={width}:height={height}:resize_mode=scale:output_fmt=bgra,"
        "hwdownload,format=bgra,colorkey=color=black:similarity=0.055:blend=0.015[foreground]"
    )
    composition = (
        "[geometry][foreground]overlay=x=0:y=0:alpha=straight:"
        "format=auto,format=yuv420p"
    )
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
        "-stats_period",
        "0.05",
        "-progress",
        "pipe:1",
        "-filter_complex",
        f"{background};{foreground};{composition}",
        "-an",
        *encoder_args,
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-fps_mode",
        "cfr",
        "-g",
        str(fps * 2),
        "-f",
        "matroska",
        str(output_path),
    ]


def build_playback_command(ffplay_path: str, master_path: Path) -> list[str]:
    return [
        ffplay_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nodisp",
        "-autoexit",
        "-volume",
        "100",
        str(master_path),
    ]


def build_mux_command(
    *,
    ffmpeg_path: str,
    capture_path: Path,
    master_path: Path,
    output_path: Path,
    start_offset_seconds: float,
    master_duration_seconds: float,
    encoder: Literal["h264_nvenc", "libx264"],
    fps: int = 60,
) -> list[str]:
    if start_offset_seconds < 0 or not math.isfinite(start_offset_seconds):
        raise SpectrumProductionError("The capture start offset is invalid.")
    encoder_args = (
        ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "18", "-b:v", "0"]
        if encoder == "h264_nvenc"
        else ["-c:v", "libx264", "-preset", "slow", "-crf", "18"]
    )
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-ss",
        f"{start_offset_seconds:.6f}",
        "-i",
        str(capture_path),
        "-i",
        str(master_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-map_metadata",
        "-1",
        "-t",
        f"{master_duration_seconds:.6f}",
        *encoder_args,
        "-vf",
        f"fps={fps}",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-movflags",
        "+faststart",
        "-fps_mode",
        "cfr",
        str(output_path),
    ]


def review_frame_timestamps(master_duration_seconds: float) -> list[tuple[str, float]]:
    near_eof = max(GRID_DURATION_SECONDS, master_duration_seconds - 0.5)
    candidates = [
        ("intro-0010", 10.0),
        ("intro-end-0103", 63.0),
        ("main-0105", 65.0),
        ("main-mid-0200", 120.0),
        ("main-end-0255", 175.0),
        ("outro-0257", 177.0),
        ("grid-end-0311", 191.0),
        ("post-grid-tail-0313", 193.0),
        ("near-eof", near_eof),
    ]
    return [(label, timestamp) for label, timestamp in candidates if timestamp < master_duration_seconds]


def build_review_frame_command(
    *,
    ffmpeg_path: str,
    final_video_path: Path,
    timestamp_seconds: float,
    output_path: Path,
) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-ss",
        f"{timestamp_seconds:.3f}",
        "-i",
        str(final_video_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=960:540:flags=lanczos",
        "-compression_level",
        "6",
        str(output_path),
    ]


def validate_final_media(
    probe: MediaProbeSummary,
    timing: SpectrumMasterTiming,
    *,
    expected_width: int = 1920,
    expected_height: int = 1080,
    expected_fps: int = 60,
) -> SpectrumValidationReport:
    video = probe.video()
    audio = probe.audio()
    minimum_frames = math.floor(timing.master_duration_seconds * expected_fps) - 2
    checks = [
        SpectrumValidationCheck(
            id="video-stream",
            passed=video is not None,
            measured=video.codec_name if video else "missing",
            expected="one readable video stream",
        ),
        SpectrumValidationCheck(
            id="audio-stream",
            passed=audio is not None,
            measured=audio.codec_name if audio else "missing",
            expected="one readable audio stream",
        ),
        SpectrumValidationCheck(
            id="resolution",
            passed=video is not None and video.width == expected_width and video.height == expected_height,
            measured=f"{video.width}x{video.height}" if video else "missing",
            expected=f"{expected_width}x{expected_height}",
        ),
        SpectrumValidationCheck(
            id="constant-frame-rate",
            passed=(
                video is not None
                and video.frame_rate is not None
                and abs(video.frame_rate - expected_fps) <= FINAL_FPS_TOLERANCE
            ),
            measured=f"{video.frame_rate:.6f}" if video and video.frame_rate else "unknown",
            expected=f"{expected_fps:.6f}",
        ),
        SpectrumValidationCheck(
            id="master-duration",
            passed=abs(probe.duration_seconds - timing.master_duration_seconds) <= FINAL_DURATION_TOLERANCE_SECONDS,
            measured=f"{probe.duration_seconds:.6f}s",
            expected=f"{timing.master_duration_seconds:.6f}s +/- {FINAL_DURATION_TOLERANCE_SECONDS:.3f}s",
        ),
        SpectrumValidationCheck(
            id="frame-count",
            passed=video is not None and video.frame_count is not None and video.frame_count >= minimum_frames,
            measured=str(video.frame_count) if video and video.frame_count else "unknown",
            expected=f">={minimum_frames}",
        ),
        SpectrumValidationCheck(
            id="non-empty-content",
            passed=probe.size_bytes >= 100_000,
            measured=f"{probe.size_bytes} bytes",
            expected=">=100000 bytes",
        ),
    ]
    return SpectrumValidationReport(
        valid=all(check.passed for check in checks),
        final_video=probe,
        timing=timing,
        checks=checks,
    )

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import FrameRange


class MediaPlanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VideoOnlyEncodePlan:
    arguments: tuple[str, ...]
    frame_count: int
    audio_included: bool
    output: Path


@dataclass(frozen=True, slots=True)
class LocalMuxPlan:
    arguments: tuple[str, ...]
    audio_location: str
    shortest_allowed: bool
    output: Path


def require_complete_sequence(frame_range: FrameRange, frames: Iterable[int]) -> None:
    actual = set(frames)
    expected = set(range(frame_range.start, frame_range.end + 1))
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise MediaPlanError(
            f"frame sequence is incomplete (missing={missing[:10]}, unexpected={unexpected[:10]})"
        )


def plan_cloud_video_only_encode(
    *,
    ffmpeg: str,
    frame_pattern: str,
    frame_range: FrameRange,
    verified_frames: Iterable[int],
    fps: int,
    output: Path,
    codec: str = "libx264",
    pixel_format: str = "yuv420p",
    crf: int = 16,
) -> VideoOnlyEncodePlan:
    require_complete_sequence(frame_range, verified_frames)
    if fps < 1 or not 0 <= crf <= 51:
        raise MediaPlanError("fps or CRF is outside the supported range")
    if "%06d" not in frame_pattern:
        raise MediaPlanError("cloud encode requires the canonical six-digit frame pattern")
    arguments = (
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-framerate",
        str(fps),
        "-start_number",
        str(frame_range.start),
        "-i",
        frame_pattern,
        "-frames:v",
        str(frame_range.count),
        "-an",
        "-c:v",
        codec,
        "-pix_fmt",
        pixel_format,
        "-crf",
        str(crf),
        str(output),
    )
    return VideoOnlyEncodePlan(arguments, frame_range.count, False, output)


def plan_local_audio_mux(
    *,
    ffmpeg: str,
    video_only_input: Path,
    private_audio_input: Path,
    output: Path,
    audio_codec: str = "aac",
    audio_bitrate: str = "320k",
) -> LocalMuxPlan:
    if video_only_input == output or private_audio_input == output:
        raise MediaPlanError("mux output must be new and distinct from both inputs")
    arguments = (
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-i",
        str(video_only_input),
        "-i",
        str(private_audio_input),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        audio_codec,
        "-b:a",
        audio_bitrate,
        str(output),
    )
    if "-shortest" in arguments:
        raise MediaPlanError("local mux must preserve the full frame-sequence clock")
    return LocalMuxPlan(arguments, "LOCAL_ONLY", False, output)

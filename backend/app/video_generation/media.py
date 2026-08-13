from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ContractError
from .jsonio import sha256_file


@dataclass(frozen=True)
class MediaProbe:
    path: str
    sha256: str
    duration_seconds: float
    width: int
    height: int
    fps: float
    codec: str
    pixel_format: str | None
    has_audio: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "durationSeconds": round(self.duration_seconds, 4),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 4),
            "codec": self.codec,
            "pixelFormat": self.pixel_format,
            "hasAudio": self.has_audio,
        }


def _ratio(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        if float(denominator) == 0:
            return 0.0
        return float(numerator) / float(denominator)
    return float(value)


def probe(path: Path, *, ffprobe: str | None = None) -> MediaProbe:
    executable = ffprobe or os.getenv("TRACKPROMPT_MC_FFPROBE_PATH") or shutil.which("ffprobe")
    if not executable:
        raise ContractError("ffprobe is not installed or not on PATH")
    if not path.is_file():
        raise ContractError(f"media file does not exist: {path}")
    result = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise ContractError(f"ffprobe failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    if not isinstance(video, dict):
        raise ContractError(f"no video stream found: {path}")
    duration = video.get("duration") or payload.get("format", {}).get("duration")
    if duration is None:
        raise ContractError(f"no duration found: {path}")
    return MediaProbe(
        path=str(path),
        sha256=sha256_file(path),
        duration_seconds=float(duration),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=_ratio(str(video.get("avg_frame_rate", "0/1"))),
        codec=str(video.get("codec_name", "unknown")),
        pixel_format=video.get("pix_fmt"),
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
    )


def expected_dimensions(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    long_edge = {"720p": 1280, "1080p": 1920, "4k": 3840}[resolution]
    short_edge = {"720p": 720, "1080p": 1080, "4k": 2160}[resolution]
    return (long_edge, short_edge) if aspect_ratio == "16:9" else (short_edge, long_edge)


def verify_generated_clip(
    path: Path,
    *,
    resolution: str,
    aspect_ratio: str,
    expected_duration_seconds: int,
    ffprobe: str | None = None,
) -> MediaProbe:
    result = probe(path, ffprobe=ffprobe)
    expected_width, expected_height = expected_dimensions(resolution, aspect_ratio)
    if (result.width, result.height) != (expected_width, expected_height):
        raise ContractError(
            f"unexpected dimensions for {path.name}: "
            f"{result.width}x{result.height}, expected "
            f"{expected_width}x{expected_height}"
        )
    if abs(result.fps - 24.0) > 0.1:
        raise ContractError(f"unexpected FPS for {path.name}: {result.fps}")
    if abs(result.duration_seconds - expected_duration_seconds) > 0.5:
        raise ContractError(f"unexpected duration for {path.name}: {result.duration_seconds:.3f}s")
    if result.has_audio:
        raise ContractError(f"{path.name} contains audio; this pipeline expects video-only clips")
    return result

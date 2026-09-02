from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, cast

from .config import Settings
from .privacy import secure_private_directory, secure_private_file
from .schemas import FileInfo
from .subprocess_utils import ProcessTimedOut, ProcessWasCancelled, run_process_bounded

SUPPORTED_CONTAINERS = {
    "aac",
    "flac",
    "ipod",
    "m4a",
    "matroska",
    "mov",
    "mp3",
    "mp4",
    "mpeg",
    "ogg",
    "oga",
    "wav",
    "wave",
}
SUPPORTED_CODECS = {
    "aac",
    "alac",
    "flac",
    "mp3",
    "opus",
    "pcm_f32le",
    "pcm_s16le",
    "pcm_s24le",
    "pcm_s32le",
    "vorbis",
}


@dataclass(frozen=True, slots=True)
class MediaProbe:
    file: FileInfo
    source_path: Path


class MediaValidationError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status_code = status_code


class MediaProcessError(RuntimeError):
    pass


class MediaCancelled(RuntimeError):
    pass


def sanitize_display_name(name: str) -> str:
    # Browsers normally send a basename, but normalize both slash styles and strip controls.
    basename = re.split(r"[/\\]+", name)[-1]
    basename = "".join(
        character
        for character in basename
        if character.isprintable()
        and not ("\u202a" <= character <= "\u202e")
        and not ("\u2066" <= character <= "\u2069")
    )
    basename = re.sub(r"\s+", " ", basename).strip(" .")
    if not basename:
        return "audio-file"
    stem = PurePath(basename).stem[:90].strip(" .") or "audio-file"
    suffix = PurePath(basename).suffix.lower()[:12]
    return f"{stem}{suffix}"[:110]


def _parse_positive_number(raw: Any, field: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise MediaValidationError("invalid_media", f"The audio has an invalid {field} value.") from exc
    if not math.isfinite(value) or value <= 0:
        raise MediaValidationError("invalid_media", f"The audio has an invalid {field} value.")
    return value


def _run_probe(
    path: Path,
    settings: Settings,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    args = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration,bit_rate:stream=index,codec_type,codec_name,sample_rate,channels,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = run_process_bounded(
            args,
            timeout_seconds=min(settings.subprocess_timeout_seconds, 30),
            cancel_requested=cancel_requested,
            stdout_limit=1_000_000,
            stderr_limit=32_000,
        )
    except FileNotFoundError as exc:
        raise MediaValidationError(
            "ffprobe_unavailable",
            "ffprobe is unavailable. Configure FFPROBE_PATH and try again.",
            status_code=503,
        ) from exc
    except ProcessWasCancelled as exc:
        raise MediaCancelled("Analysis was cancelled.") from exc
    except ProcessTimedOut as exc:
        raise MediaValidationError("media_probe_timeout", "Media validation timed out.") from exc
    if result.returncode != 0:
        raise MediaValidationError("invalid_media", "The upload is not valid, readable audio media.")
    if result.stdout_exceeded or result.stderr_exceeded:
        raise MediaValidationError("invalid_media", "The media description is unexpectedly large.")
    try:
        parsed = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except json.JSONDecodeError as exc:
        raise MediaValidationError("invalid_media", "The upload has an unreadable media description.") from exc
    if not isinstance(parsed, dict):
        raise MediaValidationError("invalid_media", "The upload has an unreadable media description.")
    return parsed


def probe_media(
    path: Path,
    display_name: str,
    settings: Settings,
    cancel_requested: Callable[[], bool] | None = None,
) -> MediaProbe:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise MediaValidationError("upload_unavailable", "The uploaded file cannot be read.") from exc
    if size <= 0:
        raise MediaValidationError("empty_upload", "The uploaded file is empty.")
    if size > settings.max_upload_bytes:
        raise MediaValidationError(
            "upload_too_large",
            f"The upload exceeds the configured {settings.max_upload_mb} MB limit.",
            status_code=413,
        )

    data = _run_probe(path, settings, cancel_requested)
    streams = data.get("streams")
    if not isinstance(streams, list):
        raise MediaValidationError("no_audio_stream", "No audio stream was found in the upload.")
    audio_streams: list[dict[str, Any]] = [
        cast(dict[str, Any], stream)
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]
    if not audio_streams:
        raise MediaValidationError("no_audio_stream", "No audio stream was found in the upload.")
    stream = audio_streams[0]
    codec = str(stream.get("codec_name") or "").lower()
    raw_format = data.get("format")
    format_data: dict[str, Any] = cast(dict[str, Any], raw_format) if isinstance(raw_format, dict) else {}
    format_name = str(format_data.get("format_name") or "").lower()
    containers = {entry.strip() for entry in format_name.split(",") if entry.strip()}
    if codec not in SUPPORTED_CODECS or not (containers & SUPPORTED_CONTAINERS):
        raise MediaValidationError(
            "unsupported_media",
            "Supported audio formats are WAV, FLAC, MP3, M4A/AAC, and OGG.",
        )

    duration_raw = format_data.get("duration") or stream.get("duration")
    duration = _parse_positive_number(duration_raw, "duration")
    if duration > settings.max_duration_seconds:
        raise MediaValidationError(
            "duration_too_long",
            f"The track exceeds the configured {settings.max_duration_seconds}-second duration limit.",
            status_code=413,
        )
    sample_rate = int(_parse_positive_number(stream.get("sample_rate"), "sample rate"))
    channels = int(_parse_positive_number(stream.get("channels"), "channel count"))
    if channels > 32:
        raise MediaValidationError("unsupported_channels", "The audio has an unsupported channel layout.")
    bit_rate_value: int | None = None
    raw_bit_rate = format_data.get("bit_rate")
    if raw_bit_rate not in (None, "N/A"):
        try:
            parsed_rate = int(str(raw_bit_rate))
            bit_rate_value = parsed_rate if parsed_rate > 0 else None
        except (TypeError, ValueError):
            bit_rate_value = None

    file_info = FileInfo(
        display_name=sanitize_display_name(display_name),
        duration_seconds=round(duration, 3),
        sample_rate=sample_rate,
        channels=channels,
        codec=codec,
        container=sorted(containers & SUPPORTED_CONTAINERS)[0],
        bit_rate=bit_rate_value,
        size_bytes=size,
        # Metadata is intentionally not requested from ffprobe. That keeps it out of logs,
        # classification, prompts, and the persisted analysis by construction.
        private_metadata={},
    )
    return MediaProbe(file=file_info, source_path=path)


def decode_for_analysis(
    probe: MediaProbe,
    output_path: Path,
    settings: Settings,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    channels = 1 if probe.file.channels == 1 else 2
    args = [
        settings.ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(probe.source_path),
        "-map_metadata",
        "-1",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        str(channels),
        "-ar",
        str(settings.decoded_sample_rate),
        "-c:a",
        "pcm_f32le",
        str(output_path),
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    secure_private_directory(output_path.parent)
    try:
        result = run_process_bounded(
            args,
            timeout_seconds=settings.subprocess_timeout_seconds,
            cancel_requested=cancel_requested,
            capture_stdout=False,
            stderr_limit=32_000,
        )
    except FileNotFoundError as exc:
        raise MediaProcessError("FFmpeg is unavailable. Configure FFMPEG_PATH and try again.") from exc

    except ProcessWasCancelled as exc:
        output_path.unlink(missing_ok=True)
        raise MediaCancelled("Analysis was cancelled.") from exc
    except ProcessTimedOut as exc:
        output_path.unlink(missing_ok=True)
        raise MediaProcessError("Audio decoding timed out.") from exc
    if result.returncode != 0 or result.stderr_exceeded:
        output_path.unlink(missing_ok=True)
        raise MediaProcessError("FFmpeg could not decode the audio safely.")
    if not output_path.exists() or output_path.stat().st_size <= 44:
        output_path.unlink(missing_ok=True)
        raise MediaProcessError("FFmpeg produced no usable analysis audio.")
    secure_private_file(output_path)
    return output_path

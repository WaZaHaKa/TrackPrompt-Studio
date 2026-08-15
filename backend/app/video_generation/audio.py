from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.media import SUPPORTED_CODECS, SUPPORTED_CONTAINERS, sanitize_display_name

from .jsonio import sha256_file


class AudioBindingError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class AudioEvidence:
    path: Path
    sha256: str
    duration_seconds: float
    container: str
    codec: str
    sample_rate_hz: int
    channels: int
    size_bytes: int

    @property
    def is_finishing_ready(self) -> bool:
        return (
            self.container == "wav"
            and self.codec in {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"}
            and self.sample_rate_hz == 48_000
            and self.channels == 2
        )


@dataclass(frozen=True, slots=True)
class StagedAudio:
    source: AudioEvidence
    artifact: AudioEvidence
    finishing: AudioEvidence
    display_name: str


def _executable(value: str | Path | None, name: str) -> str:
    executable = str(value) if value else shutil.which(name)
    if not executable:
        raise AudioBindingError(
            f"{name}_unavailable",
            f"{name} is required to verify the local audio master.",
            status_code=503,
        )
    return executable


def _positive_float(value: object, field: str) -> float:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise AudioBindingError("audio_media_invalid", f"The audio has an invalid {field}.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AudioBindingError("audio_media_invalid", f"The audio has an invalid {field}.") from exc
    if result <= 0:
        raise AudioBindingError("audio_duration_zero", "The selected audio has zero duration.")
    return result


def probe_audio(path: Path, *, ffprobe: str | Path | None = None) -> AudioEvidence:
    try:
        resolved = path.expanduser().resolve(strict=True)
        size_bytes = resolved.stat().st_size
    except OSError as exc:
        raise AudioBindingError("audio_file_missing", "The selected audio file is unavailable.") from exc
    if not resolved.is_file() or size_bytes <= 0:
        raise AudioBindingError("audio_file_empty", "The selected audio file is empty or unreadable.")
    executable = _executable(ffprobe, "ffprobe")
    try:
        result = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration,size:stream=index,codec_type,codec_name,sample_rate,channels,duration",
                "-of",
                "json",
                "--",
                str(resolved),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioBindingError(
            "audio_probe_failed",
            "The selected audio could not be inspected with ffprobe.",
        ) from exc
    if result.returncode != 0 or len(result.stdout) > 1_000_000:
        raise AudioBindingError(
            "audio_media_invalid",
            "The selected file is not valid, readable audio media.",
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AudioBindingError(
            "audio_probe_invalid",
            "ffprobe returned an unreadable audio description.",
        ) from exc
    if not isinstance(payload, dict):
        raise AudioBindingError("audio_probe_invalid", "ffprobe returned an invalid audio description.")
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise AudioBindingError("audio_stream_missing", "No audio stream was found in the selected file.")
    audio = next(
        (
            item
            for item in streams
            if isinstance(item, dict) and item.get("codec_type") == "audio"
        ),
        None,
    )
    if not isinstance(audio, dict):
        raise AudioBindingError("audio_stream_missing", "No audio stream was found in the selected file.")
    raw_format = payload.get("format")
    media_format = raw_format if isinstance(raw_format, dict) else {}
    formats = {
        item.strip().lower()
        for item in str(media_format.get("format_name") or "").split(",")
        if item.strip()
    }
    codec = str(audio.get("codec_name") or "").lower()
    supported_formats = formats & SUPPORTED_CONTAINERS
    if codec not in SUPPORTED_CODECS or not supported_formats:
        raise AudioBindingError(
            "audio_format_unsupported",
            "Supported audio formats are WAV, FLAC, MP3, M4A/AAC, and OGG.",
        )
    duration = _positive_float(media_format.get("duration") or audio.get("duration"), "duration")
    sample_rate = round(_positive_float(audio.get("sample_rate"), "sample rate"))
    channels = round(_positive_float(audio.get("channels"), "channel count"))
    if channels > 32:
        raise AudioBindingError(
            "audio_channels_unsupported",
            "The selected audio has an unsupported channel layout.",
        )
    return AudioEvidence(
        path=resolved,
        sha256=sha256_file(resolved),
        duration_seconds=duration,
        container=sorted(supported_formats)[0],
        codec=codec,
        sample_rate_hz=sample_rate,
        channels=channels,
        size_bytes=size_bytes,
    )


def _copy_immutable(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256_file(destination) == expected_sha256:
        return
    temporary = destination.with_name(f".{destination.name}.partial-{uuid4().hex}")
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        if sha256_file(temporary) != expected_sha256:
            raise AudioBindingError(
                "audio_copy_hash_mismatch",
                "The private audio copy did not match the selected original.",
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _derive_finishing_wav(
    source: AudioEvidence,
    destination: Path,
    *,
    ffmpeg: str | Path | None,
    ffprobe: str | Path | None,
) -> AudioEvidence:
    executable = _executable(ffmpeg, "ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.partial-{uuid4().hex}.wav")
    try:
        result = subprocess.run(
            [
                executable,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source.path),
                "-map",
                "0:a:0",
                "-map_metadata",
                "-1",
                "-vn",
                "-sn",
                "-dn",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s24le",
                str(temporary),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3_600,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        if result.returncode != 0 or not temporary.is_file():
            raise AudioBindingError(
                "audio_derivation_failed",
                "FFmpeg could not create the non-destructive 48 kHz finishing WAV.",
            )
        derived = probe_audio(temporary, ffprobe=ffprobe)
        if (
            not derived.is_finishing_ready
            or abs(derived.duration_seconds - source.duration_seconds) > 0.02
        ):
            raise AudioBindingError(
                "audio_derivation_invalid",
                "The derived finishing WAV did not preserve the original audio clock.",
            )
        os.replace(temporary, destination)
        return probe_audio(destination, ffprobe=ffprobe)
    finally:
        temporary.unlink(missing_ok=True)


def stage_audio_master(
    source_path: Path,
    *,
    artifact_root: Path,
    display_name: str | None = None,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
) -> StagedAudio:
    source = probe_audio(source_path, ffprobe=ffprobe)
    safe_name = sanitize_display_name(display_name or source.path.name)
    suffix_by_container = {
        "aac": ".aac",
        "flac": ".flac",
        "ipod": ".m4a",
        "m4a": ".m4a",
        "matroska": ".mka",
        "mov": ".m4a",
        "mp3": ".mp3",
        "mp4": ".m4a",
        "mpeg": ".mp3",
        "oga": ".ogg",
        "ogg": ".ogg",
        "wav": ".wav",
        "wave": ".wav",
    }
    suffix = Path(safe_name).suffix.lower() or suffix_by_container.get(source.container, ".audio")
    artifact_id = f"audio-{source.sha256[:20]}"
    original = artifact_root / "original" / f"{artifact_id}{suffix}"
    _copy_immutable(source.path, original, source.sha256)
    artifact = probe_audio(original, ffprobe=ffprobe)
    if artifact.sha256 != source.sha256:
        raise AudioBindingError(
            "audio_copy_hash_mismatch",
            "The private audio copy did not match the selected original.",
        )
    finishing = (
        artifact
        if artifact.is_finishing_ready
        else _derive_finishing_wav(
            artifact,
            artifact_root / "derived" / f"{artifact_id}-48k-stereo.wav",
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
    )
    return StagedAudio(
        source=source,
        artifact=artifact,
        finishing=finishing,
        display_name=safe_name,
    )

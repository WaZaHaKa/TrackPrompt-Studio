from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import zlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile
from typing import Any

from tools.analyze_cinematic_v2_r13_lookdev import luminance_metrics
from tools.analyze_cinematic_v2_r131_media import contrast_metrics

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_FRAME_NAME = re.compile(r"^(?P<prefix>.*?)(?P<frame>[0-9]{6})\.png$", re.IGNORECASE)
_MAX_PIXELS = 4_000_000
_PROCESS_STDERR_LIMIT = 256_000
_PROBE_STDOUT_LIMIT = 2_000_000
_NEAR_BLACK_LIMIT = 0.85
_CLIPPED_HIGHLIGHT_LIMIT = 0.01
_MIN_CONTRAST_SPAN = 0.08
_MIN_SPARSE_CONTRAST_SPAN = 0.12


class MediaQaError(ValueError):
    """A deterministic, reader-safe technical-QA failure."""


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class PngArtifact:
    frame: int
    file: str
    width: int | None
    height: int | None
    bit_depth: int | None
    color_type: int | None
    size_bytes: int
    sha256: str | None
    integrity_pass: bool
    error: str | None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_bounded(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int = _PROCESS_STDERR_LIMIT,
) -> ProcessResult:
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise MediaQaError("Subprocess commands must be non-empty argument arrays.")
    if timeout_seconds <= 0 or stdout_limit < 0 or stderr_limit < 0:
        raise MediaQaError("Subprocess bounds must be non-negative and include a positive timeout.")
    with TemporaryFile() as stdout_handle, TemporaryFile() as stderr_handle:
        try:
            completed = subprocess.run(
                list(command),
                check=False,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise MediaQaError(
                f"Subprocess exceeded its {timeout_seconds:g}-second timeout."
            ) from exc
        except OSError as exc:
            raise MediaQaError("Subprocess could not be started.") from exc
        stdout_size = stdout_handle.tell()
        stderr_size = stderr_handle.tell()
        if stdout_size > stdout_limit or stderr_size > stderr_limit:
            raise MediaQaError("Subprocess output exceeded the configured safety limit.")
        stdout_handle.seek(0)
        stderr_handle.seek(0)
        return ProcessResult(
            returncode=completed.returncode,
            stdout=stdout_handle.read(stdout_limit + 1),
            stderr=stderr_handle.read(stderr_limit + 1),
        )


def _resolve_file(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MediaQaError(f"{label} does not exist.") from exc
    if not resolved.is_file():
        raise MediaQaError(f"{label} must be a file.")
    return resolved


def _resolve_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MediaQaError(f"{label} does not exist.") from exc
    if not resolved.is_dir():
        raise MediaQaError(f"{label} must be a directory.")
    return resolved


def _read_exact(handle: Any, length: int, label: str) -> bytes:
    value = handle.read(length)
    if not isinstance(value, bytes) or len(value) != length:
        raise MediaQaError(f"PNG is truncated while reading {label}.")
    return value


def _validate_png(path: Path, frame: int) -> PngArtifact:
    size = path.stat().st_size
    digest = hashlib.sha256()
    width: int | None = None
    height: int | None = None
    bit_depth: int | None = None
    color_type: int | None = None
    saw_idat = False
    saw_iend = False
    error: str | None = None
    try:
        if size <= 0:
            raise MediaQaError("PNG is empty.")
        with path.open("rb") as handle:
            signature = _read_exact(handle, len(_PNG_SIGNATURE), "signature")
            digest.update(signature)
            if signature != _PNG_SIGNATURE:
                raise MediaQaError("PNG signature is invalid.")
            chunk_index = 0
            while not saw_iend:
                header = _read_exact(handle, 8, "chunk header")
                digest.update(header)
                length, chunk_type = struct.unpack(">I4s", header)
                if length > 512 * 1024 * 1024:
                    raise MediaQaError("PNG chunk length is unreasonable.")
                chunk_data = _read_exact(handle, length, "chunk data")
                stored_crc = _read_exact(handle, 4, "chunk CRC")
                digest.update(chunk_data)
                digest.update(stored_crc)
                expected_crc = zlib.crc32(chunk_data, zlib.crc32(chunk_type)) & 0xFFFFFFFF
                if struct.unpack(">I", stored_crc)[0] != expected_crc:
                    raise MediaQaError("PNG chunk CRC is invalid.")
                if chunk_index == 0 and chunk_type != b"IHDR":
                    raise MediaQaError("PNG IHDR must be the first chunk.")
                if chunk_type == b"IHDR":
                    if width is not None or length != 13:
                        raise MediaQaError("PNG IHDR is duplicated or malformed.")
                    (
                        width,
                        height,
                        bit_depth,
                        color_type,
                        compression,
                        filtering,
                        interlace,
                    ) = struct.unpack(">IIBBBBB", chunk_data)
                    valid_depths = {
                        0: {1, 2, 4, 8, 16},
                        2: {8, 16},
                        3: {1, 2, 4, 8},
                        4: {8, 16},
                        6: {8, 16},
                    }
                    if (
                        width <= 0
                        or height <= 0
                        or color_type not in valid_depths
                        or bit_depth not in valid_depths[color_type]
                        or compression != 0
                        or filtering != 0
                        or interlace not in {0, 1}
                    ):
                        raise MediaQaError("PNG IHDR values are invalid.")
                elif chunk_type == b"IDAT":
                    if width is None or saw_iend:
                        raise MediaQaError("PNG IDAT appears outside the image data section.")
                    saw_idat = True
                elif chunk_type == b"IEND":
                    if length != 0:
                        raise MediaQaError("PNG IEND is malformed.")
                    saw_iend = True
                chunk_index += 1
            if handle.read(1):
                raise MediaQaError("PNG contains trailing bytes after IEND.")
        if width is None or height is None or bit_depth is None or color_type is None:
            raise MediaQaError("PNG is missing a valid IHDR.")
        if not saw_idat:
            raise MediaQaError("PNG contains no image data.")
    except (MediaQaError, OSError, struct.error) as exc:
        error = str(exc)
    return PngArtifact(
        frame=frame,
        file=path.name,
        width=width,
        height=height,
        bit_depth=bit_depth,
        color_type=color_type,
        size_bytes=size,
        sha256=None if error is not None else digest.hexdigest(),
        integrity_pass=error is None,
        error=error,
    )


def _parse_rate(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            numerator, separator, denominator = value.partition("/")
            result = float(numerator) / (float(denominator) if separator else 1.0)
        elif isinstance(value, int | float):
            result = float(value)
        else:
            return None
    except (ValueError, ZeroDivisionError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _optional_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _probe_media(ffprobe: Path, media: Path) -> dict[str, Any]:
    result = _run_bounded(
        [
            str(ffprobe),
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            (
                "format=duration,size,format_name:"
                "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,"
                "nb_frames,nb_read_frames,sample_rate,channels,pix_fmt,duration"
            ),
            "-of",
            "json",
            str(media),
        ],
        timeout_seconds=180,
        stdout_limit=_PROBE_STDOUT_LIMIT,
    )
    if result.returncode != 0:
        raise MediaQaError("ffprobe rejected the encoded media.")
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaQaError("ffprobe returned invalid JSON.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise MediaQaError("ffprobe returned an invalid stream payload.")
    streams = payload["streams"]
    video = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    audio = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        None,
    )
    format_info = payload.get("format")
    if not isinstance(format_info, dict):
        format_info = {}
    frame_count = None
    if video is not None:
        frame_count = _optional_int(video.get("nb_read_frames"))
        if frame_count is None:
            frame_count = _optional_int(video.get("nb_frames"))
    return {
        "videoPresent": video is not None,
        "audioPresent": audio is not None,
        "videoCodec": None if video is None else video.get("codec_name"),
        "pixelFormat": None if video is None else video.get("pix_fmt"),
        "width": None if video is None else _optional_int(video.get("width")),
        "height": None if video is None else _optional_int(video.get("height")),
        "fps": (
            None
            if video is None
            else _parse_rate(video.get("avg_frame_rate"))
            or _parse_rate(video.get("r_frame_rate"))
        ),
        "frameCount": frame_count,
        "videoDurationSeconds": (
            None if video is None else _optional_float(video.get("duration"))
        ),
        "audioCodec": None if audio is None else audio.get("codec_name"),
        "audioSampleRate": (
            None if audio is None else _optional_int(audio.get("sample_rate"))
        ),
        "audioChannels": None if audio is None else _optional_int(audio.get("channels")),
        "audioDurationSeconds": (
            None if audio is None else _optional_float(audio.get("duration"))
        ),
        "formatDurationSeconds": _optional_float(format_info.get("duration")),
        "formatName": format_info.get("format_name"),
    }


def _decode_rgb(ffmpeg: Path, image: Path, width: int, height: int) -> bytes:
    expected_bytes = width * height * 3
    result = _run_bounded(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-i",
            str(image),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:{height}:flags=lanczos+accurate_rnd",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        timeout_seconds=60,
        stdout_limit=expected_bytes,
    )
    if result.returncode != 0:
        raise MediaQaError(f"ffmpeg could not decode {image.name}.")
    if len(result.stdout) != expected_bytes:
        raise MediaQaError(
            f"Decoded RGB byte count for {image.name} does not match {width}x{height}."
        )
    return result.stdout


def _phone_dimensions(width: int, height: int) -> tuple[int, int]:
    if width >= height:
        phone_width = min(width, 320)
        phone_height = max(1, round(phone_width * height / width))
    else:
        phone_height = min(height, 320)
        phone_width = max(1, round(phone_height * width / height))
    return phone_width, phone_height


def _diagnostics(rgb: bytes, width: int, height: int) -> dict[str, Any]:
    luminances = sorted(
        (
            0.2126 * rgb[index]
            + 0.7152 * rgb[index + 1]
            + 0.0722 * rgb[index + 2]
        )
        / 255.0
        for index in range(0, len(rgb), 3)
    )
    p005 = luminances[min(len(luminances) - 1, int(len(luminances) * 0.005))]
    p995 = luminances[min(len(luminances) - 1, int(len(luminances) * 0.995))]
    contrast = contrast_metrics(rgb)
    contrast.update(
        {
            "p005": p005,
            "p995": p995,
            "p995MinusP005": p995 - p005,
        }
    )
    return {
        "width": width,
        "height": height,
        "luminance": luminance_metrics(rgb, width, height),
        "contrast": contrast,
    }


def _diagnostic_findings(native: dict[str, Any], phone: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    near_black = max(
        float(native["luminance"]["nearBlackFraction"]),
        float(phone["luminance"]["nearBlackFraction"]),
    )
    clipping = max(
        float(native["luminance"]["clippedHighlightFraction"]),
        float(phone["luminance"]["clippedHighlightFraction"]),
    )
    broad_contrast = min(
        float(native["contrast"]["p90MinusP10"]),
        float(phone["contrast"]["p90MinusP10"]),
    )
    sparse_contrast = min(
        float(native["contrast"]["p995MinusP005"]),
        float(phone["contrast"]["p995MinusP005"]),
    )
    if near_black > _NEAR_BLACK_LIMIT:
        findings.append("near-black-fraction-exceeds-review-limit")
    if clipping > _CLIPPED_HIGHLIGHT_LIMIT:
        findings.append("clipped-highlight-fraction-exceeds-review-limit")
    if (
        broad_contrast < _MIN_CONTRAST_SPAN
        and sparse_contrast < _MIN_SPARSE_CONTRAST_SPAN
    ):
        findings.append("luminance-contrast-span-is-weak")
    return findings


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    destination = path.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, destination)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _check(identifier: str, passed: bool, detail: str) -> dict[str, object]:
    return {"id": identifier, "pass": passed, "detail": detail}


def _validate_contract(
    *,
    width: int,
    height: int,
    fps: float,
    frame_start: int,
    frame_end: int,
    review_frames: Sequence[int],
) -> tuple[int, ...]:
    if width <= 0 or height <= 0 or width * height > _MAX_PIXELS:
        raise MediaQaError(
            f"Expected dimensions must contain between 1 and {_MAX_PIXELS} pixels."
        )
    if not math.isfinite(fps) or fps <= 0 or fps > 240:
        raise MediaQaError("Expected FPS must be finite and between 0 and 240.")
    if frame_start < 0 or frame_end < frame_start:
        raise MediaQaError("Expected frame range is invalid.")
    selected = tuple(review_frames)
    if not selected or len(set(selected)) != len(selected):
        raise MediaQaError("Review frames must be an explicit, non-empty list without duplicates.")
    if tuple(sorted(selected)) != selected:
        raise MediaQaError("Review frames must be listed in ascending order.")
    if any(frame < frame_start or frame > frame_end for frame in selected):
        raise MediaQaError("Every review frame must fall inside the expected frame range.")
    return selected


def analyze_andromeda_v2_media(
    *,
    frame_directory: Path,
    encoded_media: Path,
    ffmpeg: Path,
    ffprobe: Path,
    output: Path,
    expected_width: int,
    expected_height: int,
    expected_fps: float,
    frame_start: int,
    frame_end: int,
    review_frames: Sequence[int],
) -> dict[str, Any]:
    selected = _validate_contract(
        width=expected_width,
        height=expected_height,
        fps=expected_fps,
        frame_start=frame_start,
        frame_end=frame_end,
        review_frames=review_frames,
    )
    frames_root = _resolve_directory(frame_directory, "Frame directory")
    media = _resolve_file(encoded_media, "Encoded media")
    ffmpeg_path = _resolve_file(ffmpeg, "ffmpeg executable")
    ffprobe_path = _resolve_file(ffprobe, "ffprobe executable")
    expected_count = frame_end - frame_start + 1
    expected_duration = expected_count / expected_fps
    duration_tolerance = (1.0 / expected_fps) + 0.005
    checks: list[dict[str, object]] = []

    numbered: dict[int, Path] = {}
    prefixes: set[str] = set()
    malformed_names: list[str] = []
    duplicates: list[int] = []
    for candidate in sorted(frames_root.glob("*.png"), key=lambda item: item.name.casefold()):
        match = _FRAME_NAME.fullmatch(candidate.name)
        if match is None:
            malformed_names.append(candidate.name)
            continue
        frame = int(match.group("frame"))
        if frame in numbered:
            duplicates.append(frame)
        else:
            numbered[frame] = candidate
        prefixes.add(match.group("prefix"))
    expected_numbers = set(range(frame_start, frame_end + 1))
    observed_numbers = set(numbered)
    missing = sorted(expected_numbers - observed_numbers)
    unexpected = sorted(observed_numbers - expected_numbers)
    naming_pass = not malformed_names and not duplicates and len(prefixes) == 1
    contiguous_pass = not missing and not unexpected and len(numbered) == expected_count
    checks.append(
        _check(
            "png-six-digit-uniform-naming",
            naming_pass,
            (
                f"prefixes={sorted(prefixes)!r}; malformed={malformed_names!r}; "
                f"duplicateFrames={sorted(set(duplicates))!r}"
            ),
        )
    )
    checks.append(
        _check(
            "png-exact-contiguous-frame-range",
            contiguous_pass,
            f"expected={expected_count}; observed={len(numbered)}; missing={missing!r}; unexpected={unexpected!r}",
        )
    )

    artifacts: list[PngArtifact] = []
    for frame in sorted(expected_numbers & observed_numbers):
        candidate = numbered[frame]
        try:
            candidate.resolve(strict=True).relative_to(frames_root)
        except (OSError, ValueError):
            artifacts.append(
                PngArtifact(
                    frame=frame,
                    file=candidate.name,
                    width=None,
                    height=None,
                    bit_depth=None,
                    color_type=None,
                    size_bytes=0,
                    sha256=None,
                    integrity_pass=False,
                    error="PNG resolves outside the frame directory.",
                )
            )
            continue
        artifact = _validate_png(candidate, frame)
        if (
            artifact.integrity_pass
            and (artifact.width != expected_width or artifact.height != expected_height)
        ):
            artifact = PngArtifact(
                **{
                    **asdict(artifact),
                    "integrity_pass": False,
                    "error": (
                        f"PNG dimensions are {artifact.width}x{artifact.height}; "
                        f"expected {expected_width}x{expected_height}."
                    ),
                }
            )
        artifacts.append(artifact)
    integrity_pass = (
        contiguous_pass
        and len(artifacts) == expected_count
        and all(artifact.integrity_pass for artifact in artifacts)
    )
    checks.append(
        _check(
            "png-integrity-and-dimensions",
            integrity_pass,
            f"validated={sum(item.integrity_pass for item in artifacts)}/{expected_count}",
        )
    )
    sequence_digest = hashlib.sha256()
    total_frame_bytes = 0
    for artifact in artifacts:
        total_frame_bytes += artifact.size_bytes
        sequence_digest.update(
            (
                f"{artifact.frame:06d}\0{artifact.file}\0{artifact.sha256 or '-'}\0"
                f"{artifact.size_bytes}\0{artifact.error or '-'}\n"
            ).encode()
        )

    probe: dict[str, Any] | None = None
    probe_error: str | None = None
    try:
        probe = _probe_media(ffprobe_path, media)
    except MediaQaError as exc:
        probe_error = str(exc)
    media_checks: list[tuple[str, bool, str]] = []
    if probe is None:
        media_checks.append(("encoded-media-probe", False, probe_error or "ffprobe failed"))
    else:
        video_duration = probe["videoDurationSeconds"] or probe["formatDurationSeconds"]
        audio_duration = probe["audioDurationSeconds"] or probe["formatDurationSeconds"]
        media_checks.extend(
            [
                (
                    "encoded-media-stream-presence",
                    probe["videoPresent"] is True and probe["audioPresent"] is True,
                    f"video={probe['videoPresent']}; audio={probe['audioPresent']}",
                ),
                (
                    "encoded-media-video-contract",
                    (
                        probe["videoCodec"] == "h264"
                        and probe["pixelFormat"] == "yuv420p"
                        and probe["width"] == expected_width
                        and probe["height"] == expected_height
                        and probe["fps"] is not None
                        and abs(float(probe["fps"]) - expected_fps) <= 1e-6
                    ),
                    (
                        f"codec={probe['videoCodec']}; pixelFormat={probe['pixelFormat']}; "
                        f"dimensions={probe['width']}x{probe['height']}; fps={probe['fps']}"
                    ),
                ),
                (
                    "encoded-media-audio-contract",
                    (
                        probe["audioCodec"] == "aac"
                        and probe["audioSampleRate"] == 44_100
                        and probe["audioChannels"] == 2
                    ),
                    (
                        f"codec={probe['audioCodec']}; sampleRate={probe['audioSampleRate']}; "
                        f"channels={probe['audioChannels']}"
                    ),
                ),
                (
                    "encoded-media-frame-count",
                    probe["frameCount"] == expected_count,
                    f"expected={expected_count}; observed={probe['frameCount']}",
                ),
                (
                    "encoded-media-duration",
                    (
                        video_duration is not None
                        and abs(float(video_duration) - expected_duration) <= duration_tolerance
                        and audio_duration is not None
                        and abs(float(audio_duration) - expected_duration) <= duration_tolerance
                        and abs(float(audio_duration) - float(video_duration))
                        <= duration_tolerance
                    ),
                    (
                        f"expected={expected_duration:.9f}; video={video_duration}; "
                        f"audio={audio_duration}; tolerance={duration_tolerance:.9f}"
                    ),
                ),
            ]
        )
    checks.extend(_check(*item) for item in media_checks)

    artifact_by_frame = {artifact.frame: artifact for artifact in artifacts}
    phone_width, phone_height = _phone_dimensions(expected_width, expected_height)
    review_results: list[dict[str, Any]] = []
    for frame in selected:
        review_artifact = artifact_by_frame.get(frame)
        if review_artifact is None or not review_artifact.integrity_pass:
            review_results.append(
                {
                    "frame": frame,
                    "file": None if review_artifact is None else review_artifact.file,
                    "findings": ["review-frame-is-missing-or-invalid"],
                }
            )
            continue
        source = numbered[frame]
        try:
            native_rgb = _decode_rgb(ffmpeg_path, source, expected_width, expected_height)
            phone_rgb = _decode_rgb(ffmpeg_path, source, phone_width, phone_height)
            native = _diagnostics(native_rgb, expected_width, expected_height)
            phone = _diagnostics(phone_rgb, phone_width, phone_height)
            findings = _diagnostic_findings(native, phone)
            review_results.append(
                {
                    "frame": frame,
                    "file": review_artifact.file,
                    "sha256": review_artifact.sha256,
                    "sizeBytes": review_artifact.size_bytes,
                    "native": native,
                    "phone": phone,
                    "findings": findings,
                }
            )
        except MediaQaError as exc:
            review_results.append(
                {
                    "frame": frame,
                    "file": review_artifact.file,
                    "sha256": review_artifact.sha256,
                    "sizeBytes": review_artifact.size_bytes,
                    "findings": ["review-frame-decode-failed"],
                    "error": str(exc),
                }
            )
    review_pass = len(review_results) == len(selected) and all(
        not item["findings"] for item in review_results
    )
    checks.append(
        _check(
            "selected-review-frame-diagnostics",
            review_pass,
            f"selected={list(selected)!r}; findings={sum(bool(item['findings']) for item in review_results)}",
        )
    )

    technical_pass = all(item["pass"] is True for item in checks)
    payload: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-andromeda-v2-media-technical-qa",
        "contract": {
            "width": expected_width,
            "height": expected_height,
            "fps": expected_fps,
            "frameStart": frame_start,
            "frameEnd": frame_end,
            "frameCount": expected_count,
            "durationSeconds": expected_duration,
            "durationToleranceSeconds": duration_tolerance,
            "reviewFrames": list(selected),
            "videoCodec": "h264",
            "pixelFormat": "yuv420p",
            "audioCodec": "aac",
            "audioSampleRate": 44_100,
            "audioChannels": 2,
        },
        "thresholds": {
            "nearBlackFractionMaximum": _NEAR_BLACK_LIMIT,
            "clippedHighlightFractionMaximum": _CLIPPED_HIGHLIGHT_LIMIT,
            "p90MinusP10Minimum": _MIN_CONTRAST_SPAN,
            "p995MinusP005SparseSubjectMinimum": _MIN_SPARSE_CONTRAST_SPAN,
        },
        "toolchain": {
            "ffmpeg": {
                "file": ffmpeg_path.name,
                "sha256": sha256_file(ffmpeg_path),
            },
            "ffprobe": {
                "file": ffprobe_path.name,
                "sha256": sha256_file(ffprobe_path),
            },
        },
        "frameSequence": {
            "directory": frames_root.name,
            "filenamePrefix": next(iter(prefixes)) if len(prefixes) == 1 else None,
            "digitWidth": 6,
            "expectedCount": expected_count,
            "observedCount": len(numbered),
            "totalBytes": total_frame_bytes,
            "sequenceSha256": sequence_digest.hexdigest(),
            "missingFrames": missing,
            "unexpectedFrames": unexpected,
            "frames": [asdict(artifact) for artifact in artifacts],
        },
        "encodedMedia": {
            "file": media.name,
            "sizeBytes": media.stat().st_size,
            "sha256": sha256_file(media),
            "probe": probe,
            "probeError": probe_error,
        },
        "reviewFrames": review_results,
        "checks": checks,
        "technicalPass": technical_pass,
        "humanArtisticApproval": False,
        "humanReviewRequired": True,
    }
    _atomic_json(output, payload)
    return payload


def _parse_review_frames(value: str) -> tuple[int, ...]:
    try:
        frames = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Review frames must be a comma-separated list of integers."
        ) from exc
    if not frames:
        raise argparse.ArgumentTypeError("Review frames must not be empty.")
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Perform deterministic technical QA on an Andromeda V2 PNG sequence "
            "and its encoded animatic or bounded composition proof."
        )
    )
    parser.add_argument("--frame-directory", type=Path, required=True)
    parser.add_argument("--encoded-media", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-width", type=int, required=True)
    parser.add_argument("--expected-height", type=int, required=True)
    parser.add_argument("--expected-fps", type=float, required=True)
    parser.add_argument("--frame-start", type=int, required=True)
    parser.add_argument("--frame-end", type=int, required=True)
    parser.add_argument(
        "--review-frames",
        type=_parse_review_frames,
        required=True,
        help="Explicit ascending comma-separated frame numbers selected for native and phone QA.",
    )
    args = parser.parse_args()
    payload = analyze_andromeda_v2_media(
        frame_directory=args.frame_directory,
        encoded_media=args.encoded_media,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        output=args.output,
        expected_width=args.expected_width,
        expected_height=args.expected_height,
        expected_fps=args.expected_fps,
        frame_start=args.frame_start,
        frame_end=args.frame_end,
        review_frames=args.review_frames,
    )
    print(
        json.dumps(
            {
                "ok": payload["technicalPass"],
                "technicalPass": payload["technicalPass"],
                "humanArtisticApproval": False,
                "output": str(args.output),
            },
            separators=(",", ":"),
        )
    )
    return 0 if payload["technicalPass"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())

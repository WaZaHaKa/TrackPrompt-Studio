from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SUPPORTED_SCHEMA_VERSIONS = {"1.1.0"}
ALLOWED_FPS = {24, 25, 30, 50, 60}
MAX_CUE_BYTES = 25_000_000
MAX_CURVE_POINTS = 5000
_PRIVATE_KEYS = {
    "displayname",
    "filename",
    "privateMetadata".casefold(),
    "waveformPeaks".casefold(),
    "lyrics",
    "transcript",
    "promptpackage",
    "sourceaudiopath",
    "uploadpath",
    "stempath",
    "modelcachepath",
}
_WINDOWS_ABSOLUTE = re.compile(r"(?i)\b[a-z]:[\\/]")


class VisualizerValidationError(ValueError):
    pass


def _finite_number(value: object) -> bool:
    return isinstance(value, int | float) and math.isfinite(float(value))


def _walk_public(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _PRIVATE_KEYS:
                raise VisualizerValidationError("Cue sheet contains a private field.")
            _walk_public(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _walk_public(nested)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise VisualizerValidationError("Cue sheet contains a non-finite value.")
    if isinstance(value, str):
        normalized = value.replace("\\", "/").casefold()
        if _WINDOWS_ABSOLUTE.search(value) or normalized.startswith(("/data/", "/home/", "/users/")):
            raise VisualizerValidationError("Cue sheet contains a private filesystem path.")


def validate_input_file(path: str | Path, *, label: str, suffixes: set[str] | None = None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise VisualizerValidationError(f"{label} path must be absolute.")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise VisualizerValidationError(f"{label} must be a non-empty file.")
    if suffixes and resolved.suffix.casefold() not in suffixes:
        raise VisualizerValidationError(f"{label} has an unsupported extension.")
    return resolved


def validate_output_file(path: str | Path, *, suffix: str, allow_existing: bool = False) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise VisualizerValidationError("Output path must be absolute.")
    if candidate.suffix.casefold() != suffix.casefold():
        raise VisualizerValidationError(f"Output path must use the {suffix} extension.")
    parent = candidate.parent.resolve(strict=True)
    if parent == Path(parent.anchor):
        raise VisualizerValidationError("Output may not be written directly to a filesystem root.")
    resolved = parent / candidate.name
    if resolved.exists() and not allow_existing:
        raise VisualizerValidationError("Output already exists; choose a new name or make an explicit backup.")
    return resolved


def validate_output_directory(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise VisualizerValidationError("Output directory path must be absolute.")
    parent = candidate.parent.resolve(strict=True)
    if parent == Path(parent.anchor) and candidate == parent:
        raise VisualizerValidationError("A filesystem root cannot be used as the output directory.")
    candidate.mkdir(parents=False, exist_ok=True)
    return candidate.resolve(strict=True)


def validate_cue_sheet(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise VisualizerValidationError("Cue sheet must be a JSON object.")
    _walk_public(data)
    if data.get("schemaVersion") not in SUPPORTED_SCHEMA_VERSIONS:
        raise VisualizerValidationError("Unsupported visual cue-sheet schema version.")
    timeline = data.get("timeline")
    if not isinstance(timeline, dict):
        raise VisualizerValidationError("Cue sheet timeline is missing.")
    fps = timeline.get("fps")
    frame_start = timeline.get("frameStart")
    frame_end = timeline.get("frameEnd")
    duration = timeline.get("durationSeconds")
    if fps not in ALLOWED_FPS:
        raise VisualizerValidationError("Cue sheet FPS is unsupported.")
    if not all(_finite_number(value) for value in (frame_start, frame_end, duration)):
        raise VisualizerValidationError("Cue sheet timeline is invalid.")
    if int(frame_start) < 1 or int(frame_end) < int(frame_start) or float(duration) <= 0:
        raise VisualizerValidationError("Cue sheet frame range is invalid.")
    for event_name in ("beats", "onsets"):
        events = data.get(event_name)
        if not isinstance(events, list):
            raise VisualizerValidationError(f"Cue sheet {event_name} must be a list.")
        event_times: list[float] = []
        for event in events:
            if not isinstance(event, dict) or not _finite_number(event.get("timeSeconds")):
                raise VisualizerValidationError(f"Cue sheet {event_name} contains an invalid event.")
            frame = event.get("frame")
            if not _finite_number(frame) or not int(frame_start) <= int(frame) <= int(frame_end):
                raise VisualizerValidationError(f"Cue sheet {event_name} frame is outside the timeline.")
            event_times.append(float(event["timeSeconds"]))
        if event_times != sorted(event_times):
            raise VisualizerValidationError(f"Cue sheet {event_name} must be ordered.")
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        raise VisualizerValidationError("Cue sheet must contain sections.")
    previous_end = 0.0
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            raise VisualizerValidationError("Cue sheet contains an invalid section.")
        start = section.get("startSeconds")
        end = section.get("endSeconds")
        if not _finite_number(start) or not _finite_number(end) or float(end) <= float(start):
            raise VisualizerValidationError("Cue sheet contains an invalid section range.")
        if index and float(start) < previous_end - 1e-6:
            raise VisualizerValidationError("Cue sheet sections overlap.")
        previous_end = float(end)
    curves = data.get("curves")
    if not isinstance(curves, dict) or "masterEnergy" not in curves:
        raise VisualizerValidationError("masterEnergy is required for the Blender preset.")
    for name, curve in curves.items():
        if not isinstance(name, str) or not isinstance(curve, dict):
            raise VisualizerValidationError("Cue sheet contains an invalid curve.")
        points = curve.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= MAX_CURVE_POINTS:
            raise VisualizerValidationError("Cue curve point count is invalid.")
        frames: list[int] = []
        for point in points:
            if (
                not isinstance(point, list)
                or len(point) != 2
                or not _finite_number(point[0])
                or not _finite_number(point[1])
            ):
                raise VisualizerValidationError("Cue curve contains an invalid point.")
            frame = int(point[0])
            value = float(point[1])
            if not int(frame_start) <= frame <= int(frame_end) or not 0.0 <= value <= 1.0:
                raise VisualizerValidationError("Cue curve point is outside its allowed range.")
            frames.append(frame)
        if frames != sorted(set(frames)):
            raise VisualizerValidationError("Cue curve frames must be strictly ordered.")
    return data

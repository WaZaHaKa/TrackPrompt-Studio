from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .validation import VisualizerValidationError

MAX_SHOT_PLAN_BYTES = 2_000_000
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ABSOLUTE_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|/(?:users|home|data|mnt)/)")
_FORBIDDEN_KEYS = {
    "filename", "displayname", "sourcepath", "audiopath", "lyrics", "transcript",
    "prompt", "credential", "modelpath", "physicalpath", "outputdirectory",
}


def _reject_nonfinite(token: str) -> None:
    raise VisualizerValidationError(f"Non-finite JSON number {token!r} is not allowed in a shot plan.")


def _privacy(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).replace("_", "").casefold() in _FORBIDDEN_KEYS:
                raise VisualizerValidationError("Shot plan contains a private field.")
            _privacy(item)
    elif isinstance(value, list):
        for item in value:
            _privacy(item)
    elif isinstance(value, str) and _ABSOLUTE_PATH.search(value):
        raise VisualizerValidationError("Shot plan contains a physical path.")


def load_shot_plan(path: str | Path) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise VisualizerValidationError("Shot plan path must be absolute.")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise VisualizerValidationError("Shot plan file does not exist.") from exc
    if not resolved.is_file() or resolved.suffix.casefold() != ".json":
        raise VisualizerValidationError("Shot plan must be a JSON file.")
    if not 0 < resolved.stat().st_size <= MAX_SHOT_PLAN_BYTES:
        raise VisualizerValidationError("Shot plan file size is invalid.")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VisualizerValidationError("Shot plan is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise VisualizerValidationError("Shot plan must contain a JSON object.")
    validate_shot_plan(payload)
    return payload


def validate_shot_plan(payload: dict[str, Any]) -> None:
    allowed = {
        "schemaVersion", "storyPlanSchemaVersion", "preset", "seed", "frameStart",
        "frameEnd", "fps", "inputDigest", "shots",
    }
    if set(payload) - allowed:
        raise VisualizerValidationError("Shot plan contains unknown top-level fields.")
    if payload.get("schemaVersion") != "1.0.0" or payload.get("storyPlanSchemaVersion") != "1.0.0":
        raise VisualizerValidationError("Unsupported shot plan schema version.")
    if payload.get("preset") != "space-journey-story":
        raise VisualizerValidationError("Shot plan preset must be space-journey-story.")
    frame_start = payload.get("frameStart")
    frame_end = payload.get("frameEnd")
    fps = payload.get("fps")
    if (
        isinstance(frame_start, bool) or not isinstance(frame_start, int) or frame_start < 1
        or isinstance(frame_end, bool) or not isinstance(frame_end, int) or frame_end < frame_start
        or isinstance(fps, bool) or not isinstance(fps, int | float) or not math.isfinite(float(fps)) or fps <= 0
    ):
        raise VisualizerValidationError("Shot plan timeline is invalid.")
    shots = payload.get("shots")
    if not isinstance(shots, list) or not 7 <= len(shots) <= 64:
        raise VisualizerValidationError("Shot plan must contain seven through 64 shots.")
    previous_end = frame_start - 1
    seen: set[str] = set()
    for shot in shots:
        if not isinstance(shot, dict):
            raise VisualizerValidationError("Shot plan contains an invalid shot.")
        identifier = shot.get("id")
        start = shot.get("frameStart")
        end = shot.get("frameEnd")
        act_id = shot.get("actId")
        if (
            not isinstance(identifier, str) or _IDENTIFIER.fullmatch(identifier) is None
            or identifier in seen
            or not isinstance(act_id, str) or _IDENTIFIER.fullmatch(act_id) is None
            or isinstance(start, bool) or not isinstance(start, int)
            or isinstance(end, bool) or not isinstance(end, int)
            or start != previous_end + 1 or end < start
            or shot.get("durationFrames") != end - start + 1
        ):
            raise VisualizerValidationError("Shot plan contains invalid IDs or non-contiguous bounds.")
        transition = shot.get("transition")
        declared = shot.get("intentionalDiscontinuity")
        if bool(declared) != (transition == "cut"):
            raise VisualizerValidationError("Only a declared cut may contain an intentional discontinuity.")
        reviews = shot.get("reviewFrames")
        if not isinstance(reviews, list) or not reviews or any(
            isinstance(frame, bool) or not isinstance(frame, int) or not start <= frame <= end
            for frame in reviews
        ):
            raise VisualizerValidationError("Shot review frames are invalid.")
        for layer in shot.get("reactiveLayers", []):
            if not isinstance(layer, dict):
                raise VisualizerValidationError("Reactive layer is invalid.")
            strength = layer.get("strength")
            if (
                isinstance(strength, bool) or not isinstance(strength, int | float)
                or not math.isfinite(float(strength)) or not 0 <= float(strength) <= 0.25
            ):
                raise VisualizerValidationError("Reactive strength must remain bounded to 0..0.25.")
        seen.add(identifier)
        previous_end = end
    if previous_end != frame_end:
        raise VisualizerValidationError("Shot plan must cover the complete timeline.")
    _privacy(payload)


def active_shot(shot_plan: dict[str, Any], frame: int) -> dict[str, Any] | None:
    for shot in shot_plan.get("shots", []):
        if int(shot["frameStart"]) <= frame <= int(shot["frameEnd"]):
            return shot
    return None

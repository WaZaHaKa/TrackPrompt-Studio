from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .models import LocalVideoTimelineScene
from .package import LocalVideoPackageError, LocalVideoProjectPackage


@dataclass(frozen=True, slots=True)
class BoundaryCandidate:
    seconds: float
    kind: str


_PRIORITY = {
    "phrase": 0,
    "section": 1,
    "downbeat": 2,
    "beat": 3,
    "onset": 4,
}


def _numbers(value: object) -> list[float]:
    if isinstance(value, dict):
        return _numbers(value.get("value"))
    if not isinstance(value, list):
        return []
    result: list[float] = []
    for item in value:
        if isinstance(item, (int, float)) and not isinstance(item, bool) and item >= 0:
            result.append(float(item))
    return result


def analysis_boundary_candidates(analysis: dict[str, Any]) -> tuple[BoundaryCandidate, ...]:
    """Extract only measured TrackPrompt boundaries; never synthesize confidence or downbeats."""
    result: list[BoundaryCandidate] = []
    structure = analysis.get("structure")
    if isinstance(structure, dict):
        sections = structure.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                for key in ("startSeconds", "endSeconds"):
                    value = section.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                        result.append(BoundaryCandidate(float(value), "section"))
        for value in _numbers(structure.get("importantTransitions")):
            result.append(BoundaryCandidate(value, "phrase"))
    rhythm = analysis.get("rhythm")
    if isinstance(rhythm, dict):
        for value in _numbers(rhythm.get("beatTimestamps")):
            result.append(BoundaryCandidate(value, "beat"))
        for value in _numbers(rhythm.get("downbeatTimestamps")):
            result.append(BoundaryCandidate(value, "downbeat"))
        for value in _numbers(rhythm.get("onsetTimestamps")):
            result.append(BoundaryCandidate(value, "onset"))
    unique: dict[tuple[float, str], BoundaryCandidate] = {}
    for candidate in result:
        unique[(round(candidate.seconds, 6), candidate.kind)] = BoundaryCandidate(
            round(candidate.seconds, 6), candidate.kind
        )
    return tuple(sorted(unique.values(), key=lambda item: (item.seconds, _PRIORITY.get(item.kind, 99))))


def _valid_candidate(
    candidate: BoundaryCandidate,
    *,
    target: float,
    window: float,
    lower: float,
    upper: float,
) -> bool:
    return lower <= candidate.seconds <= upper and abs(candidate.seconds - target) <= window


def resolve_timeline(
    package: LocalVideoProjectPackage,
    *,
    actual_duration_seconds: float,
    candidates: Iterable[BoundaryCandidate] = (),
) -> tuple[LocalVideoTimelineScene, ...]:
    if actual_duration_seconds <= 0:
        raise LocalVideoPackageError("audio_duration_invalid", "The decoded audio duration is invalid.")
    sync = package.sync_policy
    try:
        minimum = float(sync["minimumSceneDurationSeconds"])
        maximum = float(sync["maximumSceneDurationSeconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LocalVideoPackageError("sync_policy_invalid", "The package sync policy is invalid.") from exc
    if minimum <= 0 or maximum < minimum:
        raise LocalVideoPackageError("sync_policy_invalid", "The scene duration limits are invalid.")
    snap_window = 1.25
    count = len(package.shots)
    if actual_duration_seconds < count * minimum or actual_duration_seconds > count * maximum:
        raise LocalVideoPackageError(
            "audio_duration_outside_scene_contract",
            "The audio duration cannot satisfy the declared scene duration limits.",
        )
    provisional_duration = package.provisional_duration_seconds
    scale = actual_duration_seconds / provisional_duration
    source_candidates = tuple(
        item for item in candidates if 0 < item.seconds < actual_duration_seconds
    )
    boundaries = [0.0]
    sources = ["audio-start"]
    for index, shot in enumerate(package.shots[:-1], start=1):
        raw_end = shot.get("provisionalEndSeconds")
        if not isinstance(raw_end, (int, float)) or isinstance(raw_end, bool):
            raise LocalVideoPackageError("package_shot_timing_invalid", "A shot timing is invalid.")
        scaled = float(raw_end) * scale
        remaining = count - index
        previous = boundaries[-1]
        lower = max(previous + minimum, actual_duration_seconds - remaining * maximum)
        upper = min(previous + maximum, actual_duration_seconds - remaining * minimum)
        if lower > upper:
            raise LocalVideoPackageError(
                "timeline_constraints_unsatisfied",
                "The snapped timeline cannot preserve the scene duration contract.",
            )
        eligible = [
            item
            for item in source_candidates
            if _valid_candidate(item, target=scaled, window=snap_window, lower=lower, upper=upper)
        ]
        if eligible:
            selected = min(
                eligible,
                key=lambda item: (
                    abs(item.seconds - scaled),
                    _PRIORITY.get(item.kind, 99),
                    item.seconds,
                ),
            )
            boundary = selected.seconds
            source = selected.kind
        else:
            boundary = min(max(scaled, lower), upper)
            source = "scaled-provisional" if lower <= scaled <= upper else "duration-constraint"
        boundaries.append(round(boundary, 6))
        sources.append(source)
    boundaries.append(round(actual_duration_seconds, 6))
    sources.append("audio-end")

    scenes: list[LocalVideoTimelineScene] = []
    for index, shot in enumerate(package.shots):
        start = boundaries[index]
        end = boundaries[index + 1]
        duration = end - start
        if duration < minimum - 0.000001 or duration > maximum + 0.000001:
            raise LocalVideoPackageError(
                "timeline_constraints_unsatisfied",
                "The resolved timeline violates a scene duration limit.",
            )
        scenes.append(
            LocalVideoTimelineScene(
                shot_id=str(shot["shotId"]),
                order=index + 1,
                start_seconds=start,
                end_seconds=end,
                duration_seconds=round(duration, 6),
                boundary_source=sources[index + 1],
            )
        )
    if scenes[-1].end_seconds != round(actual_duration_seconds, 6):
        raise LocalVideoPackageError("timeline_clock_mismatch", "The resolved timeline lost the audio clock.")
    return tuple(scenes)

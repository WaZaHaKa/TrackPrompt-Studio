from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_story_template() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "templates" / "trip_to_andromeda_story_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("templateId") != "trip-to-andromeda-story-v1":
        raise ValueError("cinematic story template is invalid")
    acts = payload.get("acts")
    if not isinstance(acts, list) or len(acts) != 7:
        raise ValueError("cinematic story template must contain seven acts")
    return payload


def weighted_ranges(
    frame_start: int,
    frame_end: int,
    weights: list[float],
) -> list[tuple[int, int]]:
    if frame_start < 1 or frame_end < frame_start or not weights or any(weight <= 0 for weight in weights):
        raise ValueError("cinematic range inputs are invalid")
    frame_count = frame_end - frame_start + 1
    if frame_count < len(weights):
        raise ValueError("cinematic timeline is too short for the story template")
    total = sum(weights)
    boundaries = [frame_start]
    accumulated = 0.0
    for index, weight in enumerate(weights[:-1], start=1):
        accumulated += weight
        target = frame_start + round(frame_count * accumulated / total)
        minimum = boundaries[-1] + 1
        maximum = frame_end - (len(weights) - index) + 1
        boundaries.append(max(minimum, min(maximum, target)))
    boundaries.append(frame_end + 1)
    return [(boundaries[index], boundaries[index + 1] - 1) for index in range(len(weights))]

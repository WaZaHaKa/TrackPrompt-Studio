from __future__ import annotations

from typing import Any


def _number(value: object, fallback: float = 0.0) -> float:
    return float(value) if isinstance(value, int | float) else fallback


def _midpoint(section: dict[str, Any]) -> int:
    return round((_number(section.get("startFrame"), 1) + _number(section.get("endFrame"), 1)) / 2)


def build_preview_plan(cues: dict[str, Any]) -> dict[str, Any]:
    timeline = cues["timeline"]
    frame_start = int(timeline["frameStart"])
    frame_end = int(timeline["frameEnd"])
    fps = int(timeline["fps"])
    sections = [section for section in cues.get("sections", []) if isinstance(section, dict)]
    transitions = [event for event in cues.get("transitions", []) if isinstance(event, dict)]
    candidates: list[int] = [frame_start]
    energy_sections = [section for section in sections if isinstance(section.get("energy"), int | float)]
    if energy_sections:
        candidates.append(_midpoint(max(energy_sections, key=lambda item: _number(item.get("energy")))))
        candidates.append(_midpoint(min(energy_sections, key=lambda item: _number(item.get("energy")))))
    if transitions:
        major = max(transitions, key=lambda item: abs(_number(item.get("energyDelta"))))
        candidates.append(int(_number(major.get("frame"), frame_start)))
        clip_center = int(_number(major.get("frame"), frame_start))
    else:
        clip_center = round((frame_start + frame_end) / 2)
    vocal_sections = [
        section
        for section in sections
        if str(section.get("vocalActivity", "")).casefold() in {"present", "prominent", "active"}
        or str(section.get("stemActivity", {}).get("vocals", "")).casefold() in {"present", "prominent"}
    ]
    if vocal_sections:
        candidates.append(_midpoint(vocal_sections[0]))
    late_sections = [
        section
        for section in sections
        if _number(section.get("startFrame")) >= frame_start + (frame_end - frame_start) * 0.70
    ]
    if late_sections:
        candidates.append(_midpoint(late_sections[0]))
    candidates.append(frame_end)
    stills = sorted({min(frame_end, max(frame_start, int(frame))) for frame in candidates})
    if len(stills) > 7:
        stills = stills[:6] + [frame_end]
    desired_clip_frames = max(1, min(frame_end - frame_start + 1, fps * 10))
    clip_start = max(frame_start, clip_center - desired_clip_frames // 2)
    clip_end = min(frame_end, clip_start + desired_clip_frames - 1)
    clip_start = max(frame_start, clip_end - desired_clip_frames + 1)
    return {
        "stillFrames": stills,
        "clip": {"startFrame": clip_start, "endFrame": clip_end},
    }

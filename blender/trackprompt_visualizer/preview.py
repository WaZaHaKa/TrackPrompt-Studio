from __future__ import annotations

from typing import Any


def _number(value: object, fallback: float = 0.0) -> float:
    return float(value) if isinstance(value, int | float) else fallback


def _midpoint(section: dict[str, Any]) -> int:
    return round((_number(section.get("startFrame"), 1) + _number(section.get("endFrame"), 1)) / 2)


def _abstract_preview_plan(cues: dict[str, Any]) -> dict[str, Any]:
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


def _section_at_frame(sections: list[dict[str, Any]], frame: int) -> str | None:
    for section in sections:
        if int(_number(section.get("startFrame"), frame)) <= frame <= int(
            _number(section.get("endFrame"), frame)
        ):
            value = section.get("id")
            return str(value) if value is not None else None
    return None


def _space_journey_preview_plan(cues: dict[str, Any]) -> dict[str, Any]:
    timeline = cues["timeline"]
    frame_start = int(timeline["frameStart"])
    frame_end = int(timeline["frameEnd"])
    fps = int(timeline["fps"])
    span = max(1, frame_end - frame_start)
    sections = [section for section in cues.get("sections", []) if isinstance(section, dict)]
    transitions = [event for event in cues.get("transitions", []) if isinstance(event, dict)]

    def at_fraction(fraction: float) -> int:
        return min(frame_end, max(frame_start, frame_start + round(span * fraction)))

    first_half = [section for section in sections if _midpoint(section) <= at_fraction(0.50)]
    groove_section = max(first_half, key=lambda item: _number(item.get("energy"), 0.5)) if first_half else None
    groove_frame = _midpoint(groove_section) if groove_section is not None else at_fraction(0.34)

    breakdown_candidates = [
        section
        for index, section in enumerate(sections)
        if index not in {0, len(sections) - 1}
        and at_fraction(0.35) <= _midpoint(section) <= at_fraction(0.78)
        and _midpoint(section) > groove_frame
    ]
    breakdown_section = (
        min(breakdown_candidates, key=lambda item: _number(item.get("energy"), 0.5))
        if breakdown_candidates
        else None
    )
    breakdown_frame = _midpoint(breakdown_section) if breakdown_section is not None else at_fraction(0.56)

    peak_candidates = [
        section
        for section in sections
        if breakdown_frame < _midpoint(section) <= at_fraction(0.90)
    ]
    peak_section = max(peak_candidates, key=lambda item: _number(item.get("energy"), 0.5)) if peak_candidates else None
    peak_frame = _midpoint(peak_section) if peak_section is not None else at_fraction(0.78)

    role_targets = [
        ("opening", frame_start),
        ("early-development", at_fraction(0.16)),
        ("main-groove", groove_frame),
        ("breakdown", breakdown_frame),
        ("peak", peak_frame),
        ("outro", frame_end),
    ]
    minimum_gap = max(1, span // 40)
    bounded_frames: list[int] = []
    for index, (_role, frame) in enumerate(role_targets):
        lower = frame_start if index == 0 else bounded_frames[-1] + minimum_gap
        remaining = len(role_targets) - index - 1
        upper = frame_end - remaining * minimum_gap
        bounded_frames.append(min(upper, max(lower, int(frame))))
    bounded_frames[-1] = frame_end
    roles = [
        {
            "role": role,
            "frame": frame,
            "sectionId": _section_at_frame(sections, frame),
        }
        for (role, _target), frame in zip(role_targets, bounded_frames, strict=True)
    ]

    interior_start = at_fraction(0.15)
    interior_end = at_fraction(0.85)
    rising = [
        event
        for event in transitions
        if interior_start <= int(_number(event.get("frame"), frame_start)) <= interior_end
        and _number(event.get("energyDelta")) > 0.0
    ]
    if rising:
        representative = max(rising, key=lambda item: _number(item.get("energyDelta")))
        clip_center = int(_number(representative.get("frame"), at_fraction(0.58)))
        clip_role = "representative-rising-transition"
    elif peak_section is not None:
        clip_center = peak_frame
        clip_role = "representative-peak"
    else:
        clip_center = at_fraction(0.58)
        clip_role = "representative-interior"
    desired_clip_frames = max(1, min(frame_end - frame_start + 1, fps * 10))
    clip_start = max(frame_start, clip_center - desired_clip_frames // 2)
    clip_end = min(frame_end, clip_start + desired_clip_frames - 1)
    clip_start = max(frame_start, clip_end - desired_clip_frames + 1)
    return {
        "stillFrames": bounded_frames,
        "stillRoles": roles,
        "clip": {
            "startFrame": clip_start,
            "endFrame": clip_end,
            "role": clip_role,
            "centerFrame": clip_center,
        },
    }


def _story_preview_plan(cues: dict[str, Any], shot_plan: dict[str, Any]) -> dict[str, Any]:
    shots = [shot for shot in shot_plan.get("shots", []) if isinstance(shot, dict)]
    first_four = [
        shot
        for act_id in ("signal", "awakening", "departure", "gates")
        for shot in shots
        if shot.get("actId") == act_id
    ]
    if len(first_four) < 4:
        raise ValueError("Story preview requires Signal through Gates shots.")

    def review_frame(shot: dict[str, Any], index: int) -> int:
        frames = [int(frame) for frame in shot.get("reviewFrames", []) if isinstance(frame, int)]
        if frames:
            return frames[min(index, len(frames) - 1)]
        return int(shot["frameStart"])

    signal, awakening, departure, gates = first_four[:4]
    role_targets = (
        ("signal", signal, review_frame(signal, 1)),
        ("awakening", awakening, review_frame(awakening, 1)),
        ("departure-commit", departure, review_frame(departure, 0)),
        ("departure-passage", departure, review_frame(departure, 1)),
        ("first-gate-approach", gates, review_frame(gates, 0)),
        ("first-gate", gates, review_frame(gates, 1)),
    )
    roles = [
        {
            "role": role,
            "frame": frame,
            "actId": shot["actId"],
            "shotId": shot["id"],
        }
        for role, shot, frame in role_targets
    ]
    frame_start = int(signal["frameStart"])
    frame_end = int(gates["frameEnd"])
    fps = int(cues["timeline"]["fps"])
    maximum_frames = fps * 10
    clip_end = min(frame_end, frame_start + maximum_frames - 1)
    return {
        "stillFrames": [item["frame"] for item in roles],
        "stillRoles": roles,
        "clip": {
            "startFrame": frame_start,
            "endFrame": clip_end,
            "role": "signal-through-first-gate",
            "centerFrame": (frame_start + clip_end) // 2,
        },
    }


def build_preview_plan(
    cues: dict[str, Any],
    preset: str = "abstract-geometry",
    shot_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if preset == "abstract-geometry":
        return _abstract_preview_plan(cues)
    if preset == "space-journey":
        return _space_journey_preview_plan(cues)
    if preset == "space-journey-story" and shot_plan is not None:
        return _story_preview_plan(cues, shot_plan)
    if preset == "space-journey-story":
        raise ValueError("Space Journey Story preview requires a shot plan.")
    raise ValueError("Unsupported visualizer preset.")

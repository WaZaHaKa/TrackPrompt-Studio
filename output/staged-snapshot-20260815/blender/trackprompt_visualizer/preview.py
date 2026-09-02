from __future__ import annotations

import math
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
        ("departure-passage", departure, review_frame(departure, -1)),
        ("first-gate-approach", gates, review_frame(gates, 1)),
        ("first-gate", gates, review_frame(gates, -1)),
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
    shot_by_id = {str(shot["id"]): shot for shot in first_four}
    frame_start = int(signal["frameStart"])
    frame_end = int(gates["frameEnd"])
    fps = int(cues["timeline"]["fps"])
    maximum_frames = fps * 10
    clip_end = min(frame_end, frame_start + maximum_frames - 1)
    review_segments: list[dict[str, Any]] = []
    segment_frames = max(1, fps)
    for item in roles:
        shot = shot_by_id[str(item["shotId"])]
        shot_start = int(shot["frameStart"])
        shot_end = int(shot["frameEnd"])
        desired = min(segment_frames, shot_end - shot_start + 1)
        selected = int(item["frame"])
        segment_start = max(shot_start, selected - desired // 2)
        segment_end = min(shot_end, segment_start + desired - 1)
        segment_start = max(shot_start, segment_end - desired + 1)
        review_segments.append(
            {
                **item,
                "startFrame": segment_start,
                "endFrame": segment_end,
                "durationFrames": segment_end - segment_start + 1,
            }
        )
    return {
        "stillFrames": [item["frame"] for item in roles],
        "stillRoles": roles,
        "reviewSegments": review_segments,
        "clip": {
            "startFrame": frame_start,
            "endFrame": clip_end,
            "role": "signal-through-first-gate",
            "centerFrame": (frame_start + clip_end) // 2,
            "reviewEditStrategy": "six-authored-motion-excerpts",
            "sourceEndFrame": frame_end,
            "maximumOutputFrames": maximum_frames,
        },
    }


def _r12_story_preview_plan(cues: dict[str, Any], shot_plan: dict[str, Any]) -> dict[str, Any]:
    """Build the exact continuous R12 vertical-slice review contract.

    R12 still uses the versioned StoryPlan/ShotPlan schemas.  The distinguishing
    signal is deliberately carried by its safe shot identifiers so the R11
    six-excerpt plan remains byte-for-byte compatible.
    """

    shots = [shot for shot in shot_plan.get("shots", []) if isinstance(shot, dict)]
    role_ids = (
        ("awakening-question", "r12-shot-02-awakening-question"),
        ("chamber-release", "r12-shot-03-awakening-release"),
        ("departure-rear-follow", "r12-shot-04-departure-rear-follow"),
        ("departure-side-track", "r12-shot-05-departure-side-track"),
        ("departure-foreground-occlusion", "r12-shot-06-departure-occluded"),
        ("gate-low-approach", "r12-shot-07-gate-approach"),
        ("gate-threshold-crossing", "r12-shot-08-gate-crossing"),
        ("gate-sealed-consequence", "r12-shot-09-gate-seal"),
    )
    by_id = {str(shot.get("id")): shot for shot in shots}
    missing = [shot_id for _role, shot_id in role_ids if shot_id not in by_id]
    if missing:
        raise ValueError("R12 preview requires the complete bounded shot grammar.")

    roles: list[dict[str, Any]] = []
    for role, shot_id in role_ids:
        shot = by_id[shot_id]
        review_frames = [
            int(frame)
            for frame in shot.get("reviewFrames", [])
            if isinstance(frame, int) and not isinstance(frame, bool)
        ]
        frame = review_frames[len(review_frames) // 2] if review_frames else (
            int(shot["frameStart"]) + int(shot["frameEnd"])
        ) // 2
        roles.append(
            {
                "role": role,
                "frame": frame,
                "actId": shot["actId"],
                "shotId": shot_id,
            }
        )

    start_frame = int(by_id[role_ids[0][1]]["frameStart"])
    end_frame = int(by_id[role_ids[-1][1]]["frameEnd"])
    fps = float(cues["timeline"]["fps"])
    frame_count = end_frame - start_frame + 1
    duration = frame_count / fps
    if not 15.0 <= duration <= 20.0 or not 450 <= frame_count <= 600:
        raise ValueError("R12 continuous preview must remain within its 15-20 second bound.")
    return {
        "revisionId": "andromeda-r12-continuous-slice",
        "stillFrames": [item["frame"] for item in roles],
        "stillRoles": roles,
        "continuousRange": {
            "startFrame": start_frame,
            "endFrame": end_frame,
            "frameCount": frame_count,
            "durationSeconds": duration,
            "sourceShotIds": [shot_id for _role, shot_id in role_ids],
        },
        "formats": {
            "landscape": {
                "width": 1920,
                "height": 1080,
                "phoneWidth": 320,
                "phoneHeight": 180,
                "compositionProfile": "r12-landscape-authored",
            },
            "vertical": {
                "width": 1080,
                "height": 1920,
                "phoneWidth": 180,
                "phoneHeight": 320,
                "compositionProfile": "r12-vertical-authored",
            },
        },
        "clip": {
            "startFrame": start_frame,
            "endFrame": end_frame,
            "role": "awakening-through-gate-seal",
            "centerFrame": (start_frame + end_frame) // 2,
            "reviewEditStrategy": "continuous-authored-motion-range",
            "sourceEndFrame": end_frame,
            "maximumOutputFrames": 600,
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
    if (
        preset == "space-journey-story"
        and shot_plan is not None
        and any(
            isinstance(shot, dict) and str(shot.get("id", "")).startswith("r12-shot-")
            for shot in shot_plan.get("shots", [])
        )
    ):
        return _r12_story_preview_plan(cues, shot_plan)
    if preset == "space-journey-story" and shot_plan is not None:
        return _story_preview_plan(cues, shot_plan)
    if preset == "space-journey-story":
        raise ValueError("Space Journey Story preview requires a shot plan.")
    raise ValueError("Unsupported visualizer preset.")


def build_review_edit_spec(
    preview_plan: dict[str, Any],
    *,
    timeline_frame_start: int,
    timeline_frame_end: int,
    fps: float,
) -> dict[str, Any]:
    """Validate and flatten a non-contiguous authored-motion review edit."""

    if not math.isfinite(fps) or fps <= 0.0:
        raise ValueError("Review-edit FPS must be finite and positive.")
    segments = preview_plan.get("reviewSegments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("Review edit requires declared source segments.")
    maximum_frames = int(preview_plan.get("clip", {}).get("maximumOutputFrames", round(fps * 10.0)))
    if maximum_frames < 1:
        raise ValueError("Review edit maximum frame count is invalid.")
    validated: list[dict[str, Any]] = []
    source_frames: list[int] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError("Review edit contains an invalid segment.")
        start = segment.get("startFrame")
        end = segment.get("endFrame")
        duration = segment.get("durationFrames")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or isinstance(duration, bool)
            or not isinstance(duration, int)
            or not timeline_frame_start <= start <= end <= timeline_frame_end
            or duration != end - start + 1
        ):
            raise ValueError("Review edit segment is outside the source timeline.")
        source_frames.extend(range(start, end + 1))
        validated.append(
            {
                "index": index,
                "startFrame": start,
                "endFrame": end,
                "durationFrames": duration,
                **{
                    key: segment[key]
                    for key in ("role", "actId", "shotId", "frame")
                    if key in segment
                },
            }
        )
    if len(source_frames) > maximum_frames:
        raise ValueError("Review edit exceeds its bounded frame count.")
    source_labels = "".join(f"[review_source_{index}]" for index in range(len(validated)))
    filters = [f"[1:a]asplit={len(validated)}{source_labels}"]
    for segment in validated:
        index = int(segment["index"])
        start_seconds = (int(segment["startFrame"]) - timeline_frame_start) / fps
        duration_seconds = int(segment["durationFrames"]) / fps
        filters.append(
            f"[review_source_{index}]atrim=start={start_seconds:.9f}:duration={duration_seconds:.9f},"
            f"asetpts=PTS-STARTPTS[review_audio_{index}]"
        )
    audio_inputs = "".join(f"[review_audio_{index}]" for index in range(len(validated)))
    filters.append(f"{audio_inputs}concat=n={len(validated)}:v=0:a=1[review_audio]")
    return {
        "strategy": "six-authored-motion-excerpts",
        "segments": validated,
        "sourceFrames": source_frames,
        "outputFrameCount": len(source_frames),
        "durationSeconds": len(source_frames) / fps,
        "audioFilter": ";".join(filters),
    }


def build_continuous_review_spec(
    preview_plan: dict[str, Any],
    *,
    timeline_frame_start: int,
    timeline_frame_end: int,
    fps: float,
) -> dict[str, Any]:
    """Validate the one-range R12 render and its exactly aligned audio trim."""

    if not math.isfinite(fps) or fps <= 0.0:
        raise ValueError("Continuous review FPS must be finite and positive.")
    continuous = preview_plan.get("continuousRange")
    clip = preview_plan.get("clip")
    if not isinstance(continuous, dict) or not isinstance(clip, dict):
        raise ValueError("Continuous review requires an exact declared range.")
    start = continuous.get("startFrame")
    end = continuous.get("endFrame")
    count = continuous.get("frameCount")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or not timeline_frame_start <= start <= end <= timeline_frame_end
        or count != end - start + 1
        or clip.get("startFrame") != start
        or clip.get("endFrame") != end
        or clip.get("reviewEditStrategy") != "continuous-authored-motion-range"
    ):
        raise ValueError("Continuous review range is inconsistent with its clip contract.")
    duration = count / fps
    if not 15.0 <= duration <= 20.0 or count > int(clip.get("maximumOutputFrames", 0)):
        raise ValueError("Continuous review exceeds its bounded duration.")
    start_seconds = (start - timeline_frame_start) / fps
    return {
        "strategy": "continuous-authored-motion-range",
        "startFrame": start,
        "endFrame": end,
        "sourceFrames": list(range(start, end + 1)),
        "outputFrameCount": count,
        "durationSeconds": duration,
        "audioStartSeconds": start_seconds,
        "audioFilter": (
            f"[1:a]atrim=start={start_seconds:.9f}:duration={duration:.9f},"
            "asetpts=PTS-STARTPTS[review_audio]"
        ),
    }

from __future__ import annotations

import math
import sys
from bisect import bisect_left
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.visualizer.frames import event_frame, frame_end, section_frames  # noqa: E402
from app.visualizer.schemas import TrackPromptVisualCueSheet  # noqa: E402
from app.visualizer.validation import validate_public_cue_sheet  # noqa: E402

R12_SOURCE_START_SECONDS = 224.0
R12_SOURCE_END_SECONDS = 266.0
R12_SLICE_DURATION_SECONDS = 42.0
R12_SLICE_FPS = 30
R12_SLICE_FRAME_START = 1
R12_SLICE_FRAME_END = 1260


def _curve_value(points: list[tuple[int, float]], frame: int) -> float:
    frames = [point[0] for point in points]
    position = bisect_left(frames, frame)
    if position < len(points) and points[position][0] == frame:
        return float(points[position][1])
    if position <= 0:
        return float(points[0][1])
    if position >= len(points):
        return float(points[-1][1])
    left_frame, left_value = points[position - 1]
    right_frame, right_value = points[position]
    fraction = (frame - left_frame) / (right_frame - left_frame)
    return float(left_value + fraction * (right_value - left_value))


def _slice_curve(
    curve: dict[str, Any],
    *,
    source_frame_start: int,
    source_frame_end: int,
) -> dict[str, Any]:
    source_points = [(int(frame), float(value)) for frame, value in curve["points"]]
    selected = [
        (frame, value)
        for frame, value in source_points
        if source_frame_start <= frame <= source_frame_end
    ]
    by_frame = {frame: value for frame, value in selected}
    by_frame[source_frame_start] = _curve_value(source_points, source_frame_start)
    by_frame[source_frame_end] = _curve_value(source_points, source_frame_end)
    rebased = [
        [frame - source_frame_start + 1, max(0.0, min(1.0, value))]
        for frame, value in sorted(by_frame.items())
    ]
    if len(rebased) < 2:
        raise ValueError("R12 cue derivation produced an incomplete curve.")
    result = dict(curve)
    result["points"] = rebased
    result["originalPointCount"] = max(2, len(selected))
    result["exportedPointCount"] = len(rebased)
    simplification = dict(result["simplification"])
    simplification["maximumPointCount"] = max(
        int(simplification["maximumPointCount"]),
        len(rebased),
    )
    result["simplification"] = simplification
    return result


def derive_r12_cue_slice(
    source: TrackPromptVisualCueSheet,
) -> TrackPromptVisualCueSheet:
    """Derive the frozen 224-266 second public cue window on a local 1-1260 timeline."""

    validate_public_cue_sheet(source)
    timeline = source.timeline
    if (
        timeline.fps != R12_SLICE_FPS
        or timeline.frame_start != 1
        or timeline.duration_seconds + 1e-9 < R12_SOURCE_END_SECONDS
        or timeline.frame_end < event_frame(
            R12_SOURCE_END_SECONDS,
            timeline.duration_seconds,
            timeline.fps,
        )
    ):
        raise ValueError("R12 cue derivation requires the complete 30 FPS source analysis.")
    if not math.isclose(
        R12_SOURCE_END_SECONDS - R12_SOURCE_START_SECONDS,
        R12_SLICE_DURATION_SECONDS,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise RuntimeError("The frozen R12 cue-slice contract is inconsistent.")

    source_frame_start = event_frame(
        R12_SOURCE_START_SECONDS,
        timeline.duration_seconds,
        timeline.fps,
    )
    source_frame_end_exclusive = event_frame(
        R12_SOURCE_END_SECONDS,
        timeline.duration_seconds,
        timeline.fps,
    )
    source_frame_end = source_frame_end_exclusive - 1
    if source_frame_end - source_frame_start + 1 != R12_SLICE_FRAME_END:
        raise ValueError("R12 source frame mapping does not contain exactly 1,260 frames.")

    payload = source.model_dump(mode="json", by_alias=True)
    payload["timeline"] = {
        "durationSeconds": R12_SLICE_DURATION_SECONDS,
        "fps": R12_SLICE_FPS,
        "frameStart": R12_SLICE_FRAME_START,
        "frameEnd": frame_end(R12_SLICE_DURATION_SECONDS, R12_SLICE_FPS),
        "framePolicy": timeline.frame_policy,
    }

    for event_key in ("beats", "onsets"):
        selected_events: list[dict[str, Any]] = []
        for event in payload[event_key]:
            source_time = float(event["timeSeconds"])
            if R12_SOURCE_START_SECONDS <= source_time < R12_SOURCE_END_SECONDS:
                local_time = source_time - R12_SOURCE_START_SECONDS
                selected_events.append(
                    {
                        **event,
                        "index": len(selected_events),
                        "timeSeconds": local_time,
                        "frame": event_frame(
                            local_time,
                            R12_SLICE_DURATION_SECONDS,
                            R12_SLICE_FPS,
                        ),
                    }
                )
        payload[event_key] = selected_events

    source_sections = payload["sections"]
    clipped_sections: list[dict[str, Any]] = []
    for section in source_sections:
        clipped_start = max(float(section["startSeconds"]), R12_SOURCE_START_SECONDS)
        clipped_end = min(float(section["endSeconds"]), R12_SOURCE_END_SECONDS)
        if clipped_end <= clipped_start:
            continue
        local_start = clipped_start - R12_SOURCE_START_SECONDS
        local_end = clipped_end - R12_SOURCE_START_SECONDS
        clipped_sections.append(
            {
                **section,
                "startSeconds": local_start,
                "endSeconds": local_end,
            }
        )
    if not clipped_sections:
        raise ValueError("R12 cue derivation found no source sections.")
    for index, section in enumerate(clipped_sections):
        start, end = section_frames(
            float(section["startSeconds"]),
            float(section["endSeconds"]),
            R12_SLICE_DURATION_SECONDS,
            R12_SLICE_FPS,
            final=index == len(clipped_sections) - 1,
        )
        section["startFrame"] = start
        section["endFrame"] = end
    payload["sections"] = clipped_sections

    retained_section_ids = {str(section["id"]) for section in clipped_sections}
    transitions: list[dict[str, Any]] = []
    for transition in payload["transitions"]:
        source_time = float(transition["timeSeconds"])
        if (
            R12_SOURCE_START_SECONDS <= source_time < R12_SOURCE_END_SECONDS
            and str(transition["fromSectionId"]) in retained_section_ids
            and str(transition["toSectionId"]) in retained_section_ids
        ):
            local_time = source_time - R12_SOURCE_START_SECONDS
            transitions.append(
                {
                    **transition,
                    "timeSeconds": local_time,
                    "frame": event_frame(
                        local_time,
                        R12_SLICE_DURATION_SECONDS,
                        R12_SLICE_FPS,
                    ),
                }
            )
    payload["transitions"] = transitions
    payload["curves"] = {
        name: _slice_curve(
            curve,
            source_frame_start=source_frame_start,
            source_frame_end=source_frame_end,
        )
        for name, curve in payload["curves"].items()
    }
    warning = "r12-public-cue-slice-224000-266000ms"
    payload["warnings"] = [
        *[str(item) for item in payload.get("warnings", []) if item != warning],
        warning,
    ]
    derived = TrackPromptVisualCueSheet.model_validate(payload)
    validate_public_cue_sheet(derived)
    if derived.timeline.frame_end != R12_SLICE_FRAME_END:
        raise RuntimeError("R12 derived cue timeline drifted from the frozen contract.")
    return derived


def r12_cue_slice_contract(
    source: TrackPromptVisualCueSheet,
    derived: TrackPromptVisualCueSheet,
) -> dict[str, Any]:
    """Return the privacy-safe deterministic mapping; content hashes are added by the proof builder."""

    expected = derive_r12_cue_slice(source)
    if expected != derived:
        raise ValueError("Derived R12 cue sheet does not match the frozen slice contract.")
    source_frame_start = event_frame(
        R12_SOURCE_START_SECONDS,
        source.timeline.duration_seconds,
        source.timeline.fps,
    )
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r12-cue-slice",
        "analysisJobId": str(source.source.job_id),
        "sourceStartSeconds": R12_SOURCE_START_SECONDS,
        "sourceEndSeconds": R12_SOURCE_END_SECONDS,
        "sourceFrameStart": source_frame_start,
        "sourceFrameEnd": source_frame_start + R12_SLICE_FRAME_END - 1,
        "localFrameStart": R12_SLICE_FRAME_START,
        "localFrameEnd": R12_SLICE_FRAME_END,
        "durationSeconds": R12_SLICE_DURATION_SECONDS,
        "fps": R12_SLICE_FPS,
        "mapping": "source-frame-minus-source-start-plus-one",
        "boundaryPolicy": "linear-curve-interpolation-inclusive-local-range",
    }

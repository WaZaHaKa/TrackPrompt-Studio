from __future__ import annotations

import hashlib
import json

from ..visualizer.presets import SpaceJourneyStoryResolvedVisualizerConfig
from ..visualizer.schemas import CurveName, TrackPromptVisualCueSheet
from .planner import load_story_template, weighted_ranges
from .schemas import (
    CameraDirective,
    CameraRig,
    CompositionDirective,
    EnvironmentDirective,
    LightingDirective,
    MotionDirective,
    MotionProfileName,
    NarrativeEnvironment,
    ProtagonistState,
    ReactiveLayer,
    Shot,
    ShotPlan,
    StoryAct,
    StoryBeat,
    StoryPlan,
    TransitionType,
)
from .validation import validate_plan_pair

_LENS_BY_RIG: dict[CameraRig, float] = {
    CameraRig.ESTABLISHING_REVEAL: 32.0,
    CameraRig.SLOW_ORBIT: 50.0,
    CameraRig.SUBJECT_FOLLOW: 45.0,
    CameraRig.GATE_APPROACH: 28.0,
    CameraRig.THRESHOLD_PUSH: 35.0,
    CameraRig.RUPTURE_FALL: 24.0,
    CameraRig.TRANSFORMATION_CLOSEUP: 72.0,
    CameraRig.SCALE_PULLBACK: 28.0,
    CameraRig.ARRIVAL_REVEAL: 30.0,
}


def _canonical_digest(cues: TrackPromptVisualCueSheet, config: SpaceJourneyStoryResolvedVisualizerConfig) -> str:
    payload = {
        "cue": cues.model_dump(mode="json", by_alias=True),
        "config": config.model_dump(mode="json", by_alias=True),
        "template": load_story_template(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _curve_average(cues: TrackPromptVisualCueSheet, start: int, end: int) -> float:
    curve = cues.curves.get(CurveName.MASTER_ENERGY)
    if curve is None:
        return 0.5
    values = [value for frame, value in curve.points if start <= frame <= end]
    if not values:
        nearest = min(curve.points, key=lambda point: abs(point[0] - (start + end) // 2))
        values = [float(nearest[1])]
    return max(0.0, min(1.0, sum(values) / len(values)))


def _review_frames(start: int, end: int) -> list[int]:
    middle = start + (end - start) // 2
    return sorted({start, middle, end})


def compile_cinematic_plan(
    cues: TrackPromptVisualCueSheet,
    config: SpaceJourneyStoryResolvedVisualizerConfig,
) -> tuple[StoryPlan, ShotPlan]:
    template = load_story_template()
    raw_acts = template["acts"]
    ranges = weighted_ranges(
        cues.timeline.frame_start,
        cues.timeline.frame_end,
        [float(item["weight"]) for item in raw_acts],
    )
    digest = _canonical_digest(cues, config)
    acts: list[StoryAct] = []
    shots: list[Shot] = []
    palette = config.parameters.palette.value
    for index, (item, frame_range) in enumerate(zip(raw_acts, ranges, strict=True), start=1):
        start, end = frame_range
        act_id = str(item["id"])
        state = ProtagonistState(str(item["state"]))
        purpose = str(item["purpose"])
        midpoint = start + (end - start) // 2
        acts.append(
            StoryAct(
                id=act_id,
                name=str(item["name"]),
                frame_start=start,
                frame_end=end,
                narrative_purpose=purpose,
                protagonist_state=state,
                beats=[
                    StoryBeat(
                        id=f"{act_id}-turn",
                        frame=midpoint,
                        purpose=purpose,
                        protagonist_state=state,
                    )
                ],
            )
        )
        rig = CameraRig(str(item["cameraRig"]))
        motion = MotionProfileName(str(item["motion"]))
        energy = _curve_average(cues, start, end)
        strength = min(0.25, 0.05 + energy * 0.12)
        is_cut = act_id in {"rupture"}
        shots.append(
            Shot(
                id=f"shot-{index:02d}-{act_id}",
                name=f"{item['name']} passage",
                act_id=act_id,
                frame_start=start,
                frame_end=end,
                duration_frames=end - start + 1,
                story_purpose=purpose,
                protagonist_state=state,
                environment=EnvironmentDirective(
                    environment=NarrativeEnvironment(str(item["environment"])),
                    secondary_action=str(item["secondaryAction"]),
                ),
                camera=CameraDirective(
                    rig=rig,
                    lens_mm=_LENS_BY_RIG[rig],
                    framing="Asymmetric cinematic frame with persistent subject readability.",
                    movement_profile=motion,
                ),
                composition=CompositionDirective(
                    dominant_shape=str(item["dominantShape"]),
                    foreground=str(item["foreground"]),
                    midground_subject=str(item["midgroundSubject"]),
                    background_landmark=str(item["backgroundLandmark"]),
                    atmosphere=str(item["atmosphere"]),
                    focal_hierarchy=[str(value) for value in item["focalHierarchy"]],
                ),
                lighting=LightingDirective(
                    palette=str(item.get("lightingPalette", palette)),
                    key_direction=str(item["keyDirection"]),
                    intensity=min(1.0, 0.35 + energy * 0.45),
                ),
                motion=MotionDirective(
                    profile=motion,
                    ease_in_frames=min(90, max(12, (end - start + 1) // 8)),
                    ease_out_frames=min(90, max(12, (end - start + 1) // 8)),
                    maximum_velocity=8.0 if motion == MotionProfileName.CONTROLLED_CHASE else 3.0,
                    maximum_acceleration=4.0 if motion == MotionProfileName.IMPACT_RECOIL else 1.5,
                    maximum_angular_velocity=0.8 if motion == MotionProfileName.TRANSFORMATION_ORBIT else 0.35,
                ),
                reactive_layers=[
                    ReactiveLayer(
                        signal="master_energy_smoothed",
                        target="protagonist emission and local atmosphere",
                        strength=strength,
                    ),
                    ReactiveLayer(
                        signal="transient_event",
                        target="secondary optical glints only",
                        strength=min(0.12, strength),
                        continuous=False,
                    ),
                ],
                transition=TransitionType.CUT if is_cut else TransitionType.CONTINUOUS,
                intentional_discontinuity=is_cut,
                review_frames=_review_frames(start, end),
            )
        )
    story = StoryPlan(
        seed=config.seed,
        frame_start=cues.timeline.frame_start,
        frame_end=cues.timeline.frame_end,
        fps=float(cues.timeline.fps),
        input_digest=digest,
        acts=acts,
    )
    shot_plan = ShotPlan(
        seed=config.seed,
        frame_start=cues.timeline.frame_start,
        frame_end=cues.timeline.frame_end,
        fps=float(cues.timeline.fps),
        input_digest=digest,
        shots=shots,
    )
    validate_plan_pair(story, shot_plan)
    return story, shot_plan

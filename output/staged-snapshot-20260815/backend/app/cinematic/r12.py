from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..visualizer.presets import SpaceJourneyStoryResolvedVisualizerConfig
from ..visualizer.schemas import TrackPromptVisualCueSheet
from .compiler import compile_cinematic_plan
from .schemas import (
    CameraRig,
    MotionProfileName,
    Shot,
    ShotPlan,
    StoryPlan,
    TransitionType,
)
from .validation import validate_plan_pair

R12_REVISION_ID = "andromeda-r12-continuous-slice"
R12_CONTINUOUS_FRAME_START = 127
R12_CONTINUOUS_FRAME_END = 655
R12_CONTINUOUS_FRAME_COUNT = 529


@dataclass(frozen=True, slots=True)
class R12ShotContract:
    identifier: str
    act_id: str
    frame_start: int
    frame_end: int
    name: str
    rig: CameraRig
    lens_mm: float
    motion: MotionProfileName
    framing: str


R12_SHOT_CONTRACT: tuple[R12ShotContract, ...] = (
    R12ShotContract(
        "r12-shot-01-signal",
        "signal",
        1,
        126,
        "Signal contract",
        CameraRig.ESTABLISHING_REVEAL,
        32.0,
        MotionProfileName.CINEMATIC_DRIFT,
        "Distant dormant-vessel establishing frame retained as the pre-roll contract.",
    ),
    R12ShotContract(
        "r12-shot-02-awakening-question",
        "awakening",
        127,
        171,
        "Awakening question",
        CameraRig.TRANSFORMATION_CLOSEUP,
        72.0,
        MotionProfileName.WEIGHTLESS_FLOAT,
        "Extreme close-up asks what is moving inside the dormant vessel.",
    ),
    R12ShotContract(
        "r12-shot-03-awakening-release",
        "awakening",
        172,
        277,
        "Awakening chamber release",
        CameraRig.ESTABLISHING_REVEAL,
        32.0,
        MotionProfileName.CINEMATIC_DRIFT,
        "Wide reveal shows the chamber opening around an off-centre vessel.",
    ),
    R12ShotContract(
        "r12-shot-04-departure-rear-follow",
        "departure",
        278,
        334,
        "Departure rear follow",
        CameraRig.SUBJECT_FOLLOW,
        45.0,
        MotionProfileName.SLOW_ACCELERATION,
        "Rear follow establishes directional travel and the energy wake.",
    ),
    R12ShotContract(
        "r12-shot-05-departure-side-track",
        "departure",
        335,
        394,
        "Departure side track",
        CameraRig.SLOW_ORBIT,
        50.0,
        MotionProfileName.SLOW_ACCELERATION,
        "Side tracking makes acceleration and passing scale markers legible.",
    ),
    R12ShotContract(
        "r12-shot-06-departure-occluded",
        "departure",
        395,
        454,
        "Departure foreground-obstructed track",
        CameraRig.SUBJECT_FOLLOW,
        40.0,
        MotionProfileName.CONTROLLED_CHASE,
        "Close foreground machinery supplies parallax without hiding the vessel.",
    ),
    R12ShotContract(
        "r12-shot-07-gate-approach",
        "gates",
        455,
        531,
        "Low-angle gate approach",
        CameraRig.GATE_APPROACH,
        28.0,
        MotionProfileName.SLOW_ACCELERATION,
        "Low-angle approach establishes the dimensional threshold and anticipation.",
    ),
    R12ShotContract(
        "r12-shot-08-gate-crossing",
        "gates",
        532,
        588,
        "Point-of-view gate crossing",
        CameraRig.THRESHOLD_PUSH,
        35.0,
        MotionProfileName.CONTROLLED_CHASE,
        "Threshold push proves compression, crossing, and immediate reaction.",
    ),
    R12ShotContract(
        "r12-shot-09-gate-seal",
        "gates",
        589,
        655,
        "Gate consequence pullback",
        CameraRig.SCALE_PULLBACK,
        28.0,
        MotionProfileName.CINEMATIC_DRIFT,
        "Smooth pullback reveals the route sealing behind the departing vessel.",
    ),
    R12ShotContract(
        "r12-shot-10-rupture-contract",
        "rupture",
        656,
        832,
        "Rupture future contract",
        CameraRig.RUPTURE_FALL,
        24.0,
        MotionProfileName.IMPACT_RECOIL,
        "Unrevised future-act contract; outside the bounded R12 render.",
    ),
    R12ShotContract(
        "r12-shot-11-transformation-contract",
        "transformation",
        833,
        1058,
        "Transformation future contract",
        CameraRig.TRANSFORMATION_CLOSEUP,
        72.0,
        MotionProfileName.TRANSFORMATION_ORBIT,
        "Unrevised future-act contract; outside the bounded R12 render.",
    ),
    R12ShotContract(
        "r12-shot-12-arrival-contract",
        "arrival",
        1059,
        1260,
        "Arrival future contract",
        CameraRig.ARRIVAL_REVEAL,
        30.0,
        MotionProfileName.CINEMATIC_DRIFT,
        "Unrevised future-act contract; outside the bounded R12 render.",
    ),
)

_EXPECTED_ACT_RANGES = {
    "signal": (1, 126),
    "awakening": (127, 277),
    "departure": (278, 454),
    "gates": (455, 655),
    "rupture": (656, 832),
    "transformation": (833, 1058),
    "arrival": (1059, 1260),
}


def _review_frames(start: int, end: int) -> list[int]:
    return sorted({start, start + (end - start) // 2, end})


def _motion_limits(profile: MotionProfileName) -> tuple[float, float, float]:
    return {
        MotionProfileName.CINEMATIC_DRIFT: (3.0, 1.2, 0.30),
        MotionProfileName.SLOW_ACCELERATION: (4.0, 1.5, 0.35),
        MotionProfileName.CONTROLLED_CHASE: (8.0, 2.5, 0.55),
        MotionProfileName.WEIGHTLESS_FLOAT: (2.0, 0.8, 0.25),
        MotionProfileName.IMPACT_RECOIL: (5.0, 4.0, 0.75),
        MotionProfileName.TRANSFORMATION_ORBIT: (4.0, 1.6, 0.80),
        MotionProfileName.MICRO_AUDIO_RESPONSE: (0.25, 0.5, 0.08),
    }[profile]


def _refined_shot(base: Shot, contract: R12ShotContract) -> Shot:
    duration = contract.frame_end - contract.frame_start + 1
    velocity, acceleration, angular_velocity = _motion_limits(contract.motion)
    payload: dict[str, Any] = base.model_dump(mode="python")
    payload.update(
        {
            "id": contract.identifier,
            "name": contract.name,
            "frame_start": contract.frame_start,
            "frame_end": contract.frame_end,
            "duration_frames": duration,
            "story_purpose": contract.framing,
            "review_frames": _review_frames(contract.frame_start, contract.frame_end),
        }
    )
    camera = base.camera.model_dump(mode="python")
    camera.update(
        {
            "rig": contract.rig,
            "lens_mm": contract.lens_mm,
            "framing": contract.framing,
            "movement_profile": contract.motion,
        }
    )
    payload["camera"] = camera
    motion = base.motion.model_dump(mode="python")
    motion.update(
        {
            "profile": contract.motion,
            "interpolation": "BEZIER",
            "ease_in_frames": min(30, max(8, duration // 5)),
            "ease_out_frames": min(30, max(8, duration // 5)),
            "maximum_velocity": velocity,
            "maximum_acceleration": acceleration,
            "maximum_angular_velocity": angular_velocity,
        }
    )
    payload["motion"] = motion
    transition = base.transition if contract.act_id == "rupture" else TransitionType.CONTINUOUS
    payload["transition"] = transition
    payload["intentional_discontinuity"] = transition == TransitionType.CUT
    return Shot.model_validate(payload)


def refine_r12_shot_plan(story: StoryPlan, canonical_shots: ShotPlan) -> ShotPlan:
    """Refine the canonical 42-second plan without changing either input."""

    validate_plan_pair(story, canonical_shots)
    if (
        story.frame_start != 1
        or story.frame_end != 1260
        or canonical_shots.frame_start != 1
        or canonical_shots.frame_end != 1260
        or story.fps != 30.0
        or canonical_shots.fps != 30.0
    ):
        raise ValueError("R12 refinement requires the deterministic 42-second 30 FPS plan.")
    actual_act_ranges = {
        act.id: (act.frame_start, act.frame_end)
        for act in story.acts
    }
    if actual_act_ranges != _EXPECTED_ACT_RANGES:
        raise ValueError("R12 canonical act ranges do not match the frozen refinement contract.")
    base_by_act = {shot.act_id: shot for shot in canonical_shots.shots}
    if set(base_by_act) != set(_EXPECTED_ACT_RANGES):
        raise ValueError("R12 refinement requires one canonical source shot per story act.")
    refined = ShotPlan(
        seed=canonical_shots.seed,
        frame_start=canonical_shots.frame_start,
        frame_end=canonical_shots.frame_end,
        fps=canonical_shots.fps,
        input_digest=canonical_shots.input_digest,
        shots=[
            _refined_shot(base_by_act[contract.act_id], contract)
            for contract in R12_SHOT_CONTRACT
        ],
    )
    validate_plan_pair(story, refined)
    return refined


def compile_r12_cinematic_plan(
    cues: TrackPromptVisualCueSheet,
    config: SpaceJourneyStoryResolvedVisualizerConfig,
) -> tuple[StoryPlan, ShotPlan]:
    """Compile the canonical story, then apply the separately identified R12 shot grammar."""

    story, canonical_shots = compile_cinematic_plan(cues, config)
    return story, refine_r12_shot_plan(story, canonical_shots)

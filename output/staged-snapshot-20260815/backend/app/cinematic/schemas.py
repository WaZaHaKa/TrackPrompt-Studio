from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from ..schemas import APIModel
from ..visualizer.presets import SpaceJourneyStoryVisualizerConfigRequest
from ..visualizer.schemas import CuePreferences

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ProtagonistState(StrEnum):
    DORMANT = "dormant"
    SIGNALLED = "signalled"
    AWAKENED = "awakened"
    TRAVELLING = "travelling"
    DAMAGED = "damaged"
    TRANSFORMING = "transforming"
    TRANSFORMED = "transformed"
    ARRIVED = "arrived"


class NarrativeEnvironment(StrEnum):
    DEAD_MOON = "dead_moon"
    SIGNAL_RUINS = "signal_ruins"
    LAUNCH_STRUCTURE = "launch_structure"
    GATE_CORRIDOR = "gate_corridor"
    BROKEN_VOID = "broken_void"
    TRANSFORMATION_MEGASTRUCTURE = "transformation_megastructure"
    ANDROMEDA_ARRIVAL = "andromeda_arrival"


class CameraRig(StrEnum):
    ESTABLISHING_REVEAL = "establishing_reveal"
    SLOW_ORBIT = "slow_orbit"
    SUBJECT_FOLLOW = "subject_follow"
    GATE_APPROACH = "gate_approach"
    THRESHOLD_PUSH = "threshold_push"
    RUPTURE_FALL = "rupture_fall"
    TRANSFORMATION_CLOSEUP = "transformation_closeup"
    SCALE_PULLBACK = "scale_pullback"
    ARRIVAL_REVEAL = "arrival_reveal"


class MotionProfileName(StrEnum):
    CINEMATIC_DRIFT = "cinematic_drift"
    SLOW_ACCELERATION = "slow_acceleration"
    CONTROLLED_CHASE = "controlled_chase"
    WEIGHTLESS_FLOAT = "weightless_float"
    IMPACT_RECOIL = "impact_recoil"
    TRANSFORMATION_ORBIT = "transformation_orbit"
    MICRO_AUDIO_RESPONSE = "micro_audio_response"


class TransitionType(StrEnum):
    CONTINUOUS = "continuous"
    DISSOLVE = "dissolve"
    CUT = "cut"
    THRESHOLD = "threshold"


class ReviewAssessment(StrEnum):
    CLEAR = "clear"
    ACCEPTABLE = "acceptable"
    NEEDS_REVISION = "needs-revision"
    UNKNOWN = "unknown"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"


class StoryBeat(APIModel):
    id: str
    frame: int = Field(ge=1)
    purpose: str = Field(min_length=1, max_length=240)
    protagonist_state: ProtagonistState

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("story beat ID is invalid")
        return value


class StoryAct(APIModel):
    id: str
    name: str = Field(min_length=1, max_length=64)
    frame_start: int = Field(ge=1)
    frame_end: int = Field(ge=1)
    narrative_purpose: str = Field(min_length=1, max_length=300)
    protagonist_state: ProtagonistState
    beats: list[StoryBeat] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def valid_act(self) -> Self:
        if _IDENTIFIER.fullmatch(self.id) is None or self.frame_end < self.frame_start:
            raise ValueError("story act bounds or ID are invalid")
        if any(not self.frame_start <= beat.frame <= self.frame_end for beat in self.beats):
            raise ValueError("story beat is outside its act")
        return self


class StoryPlan(APIModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    template_id: Literal["trip-to-andromeda-story-v1"] = "trip-to-andromeda-story-v1"
    preset: Literal["space-journey-story"] = "space-journey-story"
    seed: int = Field(ge=0, le=2_147_483_647)
    frame_start: int = Field(ge=1)
    frame_end: int = Field(ge=1)
    fps: float = Field(gt=0.0, le=120.0)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    acts: list[StoryAct] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def contiguous_acts(self) -> Self:
        if not math.isfinite(self.fps) or self.frame_end < self.frame_start:
            raise ValueError("story plan timeline is invalid")
        if self.acts[0].frame_start != self.frame_start or self.acts[-1].frame_end != self.frame_end:
            raise ValueError("story acts must cover the timeline")
        for previous, current in zip(self.acts, self.acts[1:], strict=False):
            if current.frame_start != previous.frame_end + 1:
                raise ValueError("story acts must be contiguous")
        if [act.name for act in self.acts] != [
            "Signal", "Awakening", "Departure", "Gates", "Rupture", "Transformation", "Arrival"
        ]:
            raise ValueError("story act order is fixed by the template")
        return self


class CameraDirective(APIModel):
    rig: CameraRig
    lens_mm: float = Field(ge=12.0, le=200.0)
    framing: str = Field(min_length=1, max_length=100)
    movement_profile: MotionProfileName


class EnvironmentDirective(APIModel):
    environment: NarrativeEnvironment
    secondary_action: str = Field(min_length=1, max_length=180)


class LightingDirective(APIModel):
    palette: str = Field(min_length=1, max_length=40)
    key_direction: str = Field(min_length=1, max_length=80)
    intensity: float = Field(ge=0.0, le=1.0)


class CompositionDirective(APIModel):
    dominant_shape: str = Field(min_length=1, max_length=100)
    foreground: str = Field(min_length=1, max_length=120)
    midground_subject: str = Field(min_length=1, max_length=120)
    background_landmark: str = Field(min_length=1, max_length=120)
    atmosphere: str = Field(min_length=1, max_length=120)
    focal_hierarchy: list[str] = Field(min_length=2, max_length=5)


class MotionDirective(APIModel):
    profile: MotionProfileName
    interpolation: Literal["BEZIER", "LINEAR", "CONSTANT"] = "BEZIER"
    ease_in_frames: int = Field(ge=0, le=300)
    ease_out_frames: int = Field(ge=0, le=300)
    maximum_velocity: float = Field(gt=0.0, le=100.0)
    maximum_acceleration: float = Field(gt=0.0, le=100.0)
    maximum_angular_velocity: float = Field(gt=0.0, le=10.0)


class ReactiveLayer(APIModel):
    signal: Literal[
        "master_energy_smoothed", "drum_energy_smoothed", "bass_energy_smoothed",
        "vocal_energy_smoothed", "brightness_smoothed", "transient_event"
    ]
    target: str = Field(min_length=1, max_length=100)
    strength: float = Field(ge=0.0, le=0.25)
    continuous: bool = True


class Shot(APIModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    act_id: str
    frame_start: int = Field(ge=1)
    frame_end: int = Field(ge=1)
    duration_frames: int = Field(ge=1)
    story_purpose: str = Field(min_length=1, max_length=300)
    protagonist_state: ProtagonistState
    environment: EnvironmentDirective
    camera: CameraDirective
    composition: CompositionDirective
    lighting: LightingDirective
    motion: MotionDirective
    reactive_layers: list[ReactiveLayer] = Field(default_factory=list, max_length=8)
    transition: TransitionType
    intentional_discontinuity: bool = False
    review_frames: list[int] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def valid_shot(self) -> Self:
        if _IDENTIFIER.fullmatch(self.id) is None or _IDENTIFIER.fullmatch(self.act_id) is None:
            raise ValueError("shot or act ID is invalid")
        if self.frame_end < self.frame_start or self.duration_frames != self.frame_end - self.frame_start + 1:
            raise ValueError("shot duration does not match its frame range")
        if any(not self.frame_start <= frame <= self.frame_end for frame in self.review_frames):
            raise ValueError("review frame is outside its shot")
        if self.intentional_discontinuity != (self.transition == TransitionType.CUT):
            raise ValueError("only declared cuts may be intentional discontinuities")
        return self


class ShotPlan(APIModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    story_plan_schema_version: Literal["1.0.0"] = "1.0.0"
    preset: Literal["space-journey-story"] = "space-journey-story"
    seed: int = Field(ge=0, le=2_147_483_647)
    frame_start: int = Field(ge=1)
    frame_end: int = Field(ge=1)
    fps: float = Field(gt=0.0, le=120.0)
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    shots: list[Shot] = Field(min_length=7, max_length=64)

    @model_validator(mode="after")
    def valid_shots(self) -> Self:
        if not math.isfinite(self.fps):
            raise ValueError("shot plan FPS must be finite")
        if self.shots[0].frame_start != self.frame_start or self.shots[-1].frame_end != self.frame_end:
            raise ValueError("shots must cover the timeline")
        if len({shot.id for shot in self.shots}) != len(self.shots):
            raise ValueError("shot IDs must be unique")
        for previous, current in zip(self.shots, self.shots[1:], strict=False):
            if current.frame_start != previous.frame_end + 1:
                raise ValueError("shots must be contiguous and non-overlapping")
        return self


class RevisionMetadata(APIModel):
    revision: int = Field(ge=1, le=10_000)
    reviewer: Literal["human", "codex-assisted"]
    note: str = Field(default="", max_length=500)


class ArtDirectionReview(APIModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    shot_id: str
    review_frame: int = Field(ge=1)
    focal_readability: ReviewAssessment
    depth: ReviewAssessment
    silhouette: ReviewAssessment
    color_hierarchy: ReviewAssessment
    visual_density: ReviewAssessment
    story_clarity: ReviewAssessment
    mobile_readability: ReviewAssessment
    findings: list[str] = Field(default_factory=list, max_length=20)
    decision: ReviewDecision
    revision_metadata: RevisionMetadata

    @field_validator("shot_id")
    @classmethod
    def safe_shot_id(cls, value: str) -> str:
        if _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("review shot ID is invalid")
        return value


class ArtDirectionReviewCollection(APIModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    reviews: list[ArtDirectionReview] = Field(default_factory=list, max_length=256)


class CinematicCompileRequest(APIModel):
    cue_preferences: CuePreferences = Field(default_factory=CuePreferences)
    visualizer_config: SpaceJourneyStoryVisualizerConfigRequest = Field(
        default_factory=lambda: SpaceJourneyStoryVisualizerConfigRequest(
            preset="space-journey-story"
        )
    )


class CinematicPlanBundle(APIModel):
    story_plan: StoryPlan
    shot_plan: ShotPlan

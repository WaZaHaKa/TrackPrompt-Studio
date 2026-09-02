from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from ..schemas import (
    VISUAL_CUE_SHEET_SCHEMA_VERSION,
    VISUAL_FEATURE_ARTIFACT_SCHEMA_VERSION,
    APIModel,
    Confidence,
)

VISUAL_FEATURE_SCHEMA_VERSION = VISUAL_FEATURE_ARTIFACT_SCHEMA_VERSION
VISUAL_CUE_SCHEMA_VERSION = VISUAL_CUE_SHEET_SCHEMA_VERSION
ALLOWED_FPS = {24, 25, 30, 50, 60}


class CurveName(StrEnum):
    MASTER_ENERGY = "masterEnergy"
    DRUM_ENERGY = "drumEnergy"
    BASS_ENERGY = "bassEnergy"
    VOCAL_ENERGY = "vocalEnergy"
    OTHER_ENERGY = "otherEnergy"
    LOW_BAND_ENERGY = "lowBandEnergy"
    MID_BAND_ENERGY = "midBandEnergy"
    HIGH_BAND_ENERGY = "highBandEnergy"
    BRIGHTNESS = "brightness"
    TRANSIENT_ACTIVITY = "transientActivity"
    STEREO_WIDTH = "stereoWidth"


class CurveDetail(StrEnum):
    COMPACT = "compact"
    BALANCED = "balanced"
    DETAILED = "detailed"


class NormalizationMetadata(APIModel):
    method: str = "robust-percentile"
    lower_percentile: float = Field(default=5.0, ge=0.0, le=100.0)
    upper_percentile: float = Field(default=95.0, ge=0.0, le=100.0)
    normalization_group: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def ordered_percentiles(self) -> Self:
        if self.upper_percentile <= self.lower_percentile:
            raise ValueError("normalization percentiles must be ordered")
        return self


class SmoothingMetadata(APIModel):
    method: str = "asymmetric-exponential"
    attack_seconds: float = Field(ge=0.0, le=10.0)
    release_seconds: float = Field(ge=0.0, le=10.0)
    source_sample_rate_hz: float = Field(gt=0.0, le=1000.0)
    output_sample_rate_hz: float = Field(gt=0.0, le=1000.0)


class PrivateVisualCurve(APIModel):
    values: list[float] = Field(min_length=2, max_length=25_001)
    normalization: NormalizationMetadata
    smoothing: SmoothingMetadata

    @field_validator("values")
    @classmethod
    def bounded_values(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in values):
            raise ValueError("visual feature values must be finite and bounded to 0..1")
        return values


class VisualFeatureArtifact(APIModel):
    schema_version: str = VISUAL_FEATURE_SCHEMA_VERSION
    job_id: str
    duration_seconds: float = Field(gt=0.0, le=7200.0)
    sample_rate_hz: float = Field(default=20.0, gt=0.0, le=100.0)
    curves: dict[CurveName, PrivateVisualCurve]
    effective_mode: str
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def required_and_bounded(self) -> Self:
        if self.schema_version != VISUAL_FEATURE_SCHEMA_VERSION:
            raise ValueError("unsupported visual feature schema version")
        if CurveName.MASTER_ENERGY not in self.curves:
            raise ValueError("masterEnergy is required")
        expected_maximum = math.ceil(self.duration_seconds * self.sample_rate_hz) + 1
        if any(len(curve.values) > expected_maximum + 1 for curve in self.curves.values()):
            raise ValueError("visual feature curve exceeds its bounded cadence")
        return self


class CuePreferences(APIModel):
    fps: Literal[24, 25, 30, 50, 60] = 30
    include_beats: bool = True
    include_onsets: bool = True
    include_stem_evidence: bool = True
    include_curves: bool = True
    curve_detail: CurveDetail = CurveDetail.BALANCED

    @field_validator("fps")
    @classmethod
    def allowed_fps(cls, value: int) -> int:
        if value not in ALLOWED_FPS:
            raise ValueError("fps must be one of 24, 25, 30, 50, or 60")
        return value


class CueSource(APIModel):
    analysis_schema_version: str
    analysis_version: str
    job_id: str
    requested_mode: str
    effective_mode: str


class CueTimeline(APIModel):
    duration_seconds: float = Field(gt=0.0, le=7200.0)
    fps: int
    frame_start: int = Field(ge=1)
    frame_end: int = Field(ge=1)
    frame_policy: str = "nearest-half-up-clamped"

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.fps not in ALLOWED_FPS or self.frame_end < self.frame_start:
            raise ValueError("invalid cue timeline")
        return self


class CueMeasuredValue(APIModel):
    value: float | None = None
    confidence: Confidence = Confidence.UNKNOWN

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("measured cue value must be finite")
        return value


class CueMeterValue(APIModel):
    value: str | None = Field(default=None, max_length=40)
    confidence: Confidence = Confidence.UNKNOWN


class MusicalGrid(APIModel):
    bpm: CueMeasuredValue
    seconds_per_beat: float | None = Field(default=None, gt=0.0)
    meter: CueMeterValue
    downbeats_available: bool = False


class CueEvent(APIModel):
    index: int = Field(ge=0)
    time_seconds: float = Field(ge=0.0)
    frame: int = Field(ge=1)
    confidence: Confidence
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    source_path: str


class CueSection(APIModel):
    id: str
    neutral_label: str
    inferred_label: str | None = None
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    start_frame: int = Field(ge=1)
    end_frame: int = Field(ge=1)
    energy: float | None = None
    loudness: float | None = None
    confidence: Confidence
    boundary_confidence: Confidence
    repetition_group: str | None = None
    vocal_activity: str | None = None
    instruments: list[str] = Field(default_factory=list, max_length=20)
    stem_activity: dict[str, str] = Field(default_factory=dict)
    stem_relative_rms: dict[str, float] = Field(default_factory=dict)
    source_path: str

    @model_validator(mode="after")
    def ordered_section(self) -> Self:
        if self.end_seconds <= self.start_seconds or self.end_frame < self.start_frame:
            raise ValueError("cue section bounds must be ordered")
        for value in (self.energy, self.loudness, *self.stem_relative_rms.values()):
            if value is not None and not math.isfinite(value):
                raise ValueError("cue section values must be finite")
        return self


class CueTransition(APIModel):
    id: str
    time_seconds: float = Field(ge=0.0)
    frame: int = Field(ge=1)
    from_section_id: str
    to_section_id: str
    energy_before: float | None = None
    energy_after: float | None = None
    energy_delta: float | None = None
    direction: str = Field(pattern=r"^(rising|falling|stable|unknown)$")
    confidence: Confidence
    source_paths: list[str] = Field(min_length=2, max_length=2)


class SimplificationMetadata(APIModel):
    method: str = "rdp-vertical-error"
    tolerance: float = Field(ge=0.0, le=1.0)
    maximum_error: float = Field(ge=0.0, le=1.0)
    maximum_point_count: int = Field(ge=2, le=5000)


class CueCurve(APIModel):
    point_format: tuple[str, str] = ("frame", "value")
    points: list[tuple[int, float]] = Field(min_length=2, max_length=5000)
    interpolation: str = "linear"
    source_sample_rate_hz: float = Field(gt=0.0, le=1000.0)
    original_point_count: int = Field(ge=2)
    exported_point_count: int = Field(ge=2, le=5000)
    simplification: SimplificationMetadata
    normalization: NormalizationMetadata
    smoothing: SmoothingMetadata

    @model_validator(mode="after")
    def valid_points(self) -> Self:
        frames = [point[0] for point in self.points]
        if frames != sorted(set(frames)):
            raise ValueError("cue curve frames must be strictly ordered")
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for _, value in self.points):
            raise ValueError("cue curve values must be finite and bounded")
        if self.exported_point_count != len(self.points):
            raise ValueError("exported point count must match points")
        return self


class TrackPromptVisualCueSheet(APIModel):
    schema_version: str = VISUAL_CUE_SCHEMA_VERSION
    source: CueSource
    timeline: CueTimeline
    musical_grid: MusicalGrid
    beats: list[CueEvent] = Field(default_factory=list, max_length=100_000)
    onsets: list[CueEvent] = Field(default_factory=list, max_length=100_000)
    sections: list[CueSection] = Field(min_length=1, max_length=256)
    transitions: list[CueTransition] = Field(default_factory=list, max_length=255)
    curves: dict[CurveName, CueCurve]
    warnings: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.schema_version != VISUAL_CUE_SCHEMA_VERSION:
            raise ValueError("unsupported visual cue schema version")
        for events in (self.beats, self.onsets):
            if [event.time_seconds for event in events] != sorted(event.time_seconds for event in events):
                raise ValueError("cue events must be ordered")
            if any(not self.timeline.frame_start <= event.frame <= self.timeline.frame_end for event in events):
                raise ValueError("cue event frame is outside the timeline")
        if self.sections[0].start_frame != self.timeline.frame_start:
            raise ValueError("sections must begin at frameStart")
        for previous, current in zip(self.sections, self.sections[1:], strict=False):
            if current.start_seconds < previous.end_seconds - 1e-6:
                raise ValueError("cue sections may not overlap")
        if self.sections[-1].end_frame != self.timeline.frame_end:
            raise ValueError("final section must end at frameEnd")
        if any(
            frame < self.timeline.frame_start or frame > self.timeline.frame_end
            for curve in self.curves.values()
            for frame, _value in curve.points
        ):
            raise ValueError("cue curve frame is outside the timeline")
        return self

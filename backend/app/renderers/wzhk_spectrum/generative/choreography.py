from __future__ import annotations

import math
from typing import Self

from pydantic import Field, model_validator

from .contracts import (
    EasingId,
    GenerativeModel,
    GeometrySectionId,
    MusicalTime,
    MusicalTimeUnit,
    ShapeSpec,
)
from .shapes import easing_progress


def seconds_per_beat(bpm: float) -> float:
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError("BPM must be finite and positive")
    return 60 / bpm


def beats_to_seconds(beats: float, bpm: float) -> float:
    if not math.isfinite(beats) or beats < 0:
        raise ValueError("beats must be finite and nonnegative")
    return beats * seconds_per_beat(bpm)


def seconds_per_bar(bpm: float, beats_per_bar: int) -> float:
    if beats_per_bar <= 0:
        raise ValueError("beats per bar must be positive")
    return beats_to_seconds(float(beats_per_bar), bpm)


def bars_to_seconds(bars: float, bpm: float, beats_per_bar: int) -> float:
    if not math.isfinite(bars) or bars < 0:
        raise ValueError("bars must be finite and nonnegative")
    return bars * seconds_per_bar(bpm, beats_per_bar)


def musical_time_to_seconds(value: MusicalTime, bpm: float, beats_per_bar: int) -> float:
    if value.unit is MusicalTimeUnit.SECONDS:
        return value.value
    if value.unit is MusicalTimeUnit.BEATS:
        return beats_to_seconds(value.value, bpm)
    return bars_to_seconds(value.value, bpm, beats_per_bar)


class ChoreographySection(GenerativeModel):
    section_id: GeometrySectionId
    start_seconds: float = Field(ge=0, le=86_400)
    end_seconds: float = Field(gt=0, le=86_400)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("choreography sections must have positive duration")
        return self


class GeometryTransition(GenerativeModel):
    transition_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,79}$")
    section: GeometrySectionId
    start: MusicalTime
    duration: MusicalTime
    shape_a: ShapeSpec
    shape_b: ShapeSpec
    easing: EasingId = EasingId.SMOOTHERSTEP

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        if self.duration.value <= 0:
            raise ValueError("geometry transition duration must be positive")
        if self.shape_a == self.shape_b:
            raise ValueError("geometry transitions require distinct endpoints")
        return self


class GeometryChoreography(GenerativeModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^1\.0\.0$")
    bpm: float = Field(gt=0, le=400)
    beats_per_bar: int = Field(gt=0, le=32)
    grid_duration_seconds: float = Field(gt=0, le=86_400)
    master_duration_seconds: float = Field(gt=0, le=86_400)
    sections: list[ChoreographySection] = Field(min_length=4, max_length=4)
    transitions: list[GeometryTransition] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_chain(self) -> Self:
        if self.master_duration_seconds < self.grid_duration_seconds:
            raise ValueError("master duration cannot be shorter than the musical grid")
        expected_ids = list(GeometrySectionId)
        if [section.section_id for section in self.sections] != expected_ids:
            raise ValueError("sections must be intro, main, outro, then post-grid-tail")
        if self.sections[0].start_seconds != 0:
            raise ValueError("the first choreography section must begin at zero")
        for previous, current in zip(self.sections[:-1], self.sections[1:], strict=True):
            if previous.end_seconds != current.start_seconds:
                raise ValueError("choreography sections must be contiguous")
        tail = self.sections[-1]
        if tail.start_seconds != self.grid_duration_seconds:
            raise ValueError("the post-grid tail must begin at the musical-grid boundary")
        if tail.end_seconds != self.master_duration_seconds:
            raise ValueError("the post-grid tail must end at master EOF")

        previous_end = -1.0
        previous_target: ShapeSpec | None = None
        for transition in self.transitions:
            start = musical_time_to_seconds(transition.start, self.bpm, self.beats_per_bar)
            duration = musical_time_to_seconds(transition.duration, self.bpm, self.beats_per_bar)
            end = start + duration
            if start < previous_end:
                raise ValueError("geometry transitions must be ordered and non-overlapping")
            if end > self.master_duration_seconds:
                raise ValueError("geometry transitions cannot extend beyond master EOF")
            if self.section_at(start).section_id is not transition.section:
                raise ValueError("transition section must match its start position")
            if previous_target is not None and transition.shape_a != previous_target:
                raise ValueError("geometry transitions must form a continuous shape chain")
            previous_end = end
            previous_target = transition.shape_b
        return self

    def section_at(self, seconds: float) -> ChoreographySection:
        for section in self.sections:
            if section.start_seconds <= seconds < section.end_seconds:
                return section
        if seconds == self.master_duration_seconds:
            return self.sections[-1]
        raise ValueError("timeline position is outside the choreography")


class ResolvedGeometryState(GenerativeModel):
    seconds: float = Field(ge=0, le=86_400)
    section: GeometrySectionId
    transition_id: str | None = None
    shape_a: ShapeSpec
    shape_b: ShapeSpec
    raw_progress: float = Field(ge=0, le=1)
    eased_progress: float = Field(ge=0, le=1)
    transition_active: bool


def scattered_sections(master_duration_seconds: float) -> list[ChoreographySection]:
    if not math.isfinite(master_duration_seconds) or master_duration_seconds <= 192:
        raise ValueError("Scattered master duration must extend beyond the 192-second grid")
    return [
        ChoreographySection(section_id=GeometrySectionId.INTRO, start_seconds=0, end_seconds=64),
        ChoreographySection(section_id=GeometrySectionId.MAIN, start_seconds=64, end_seconds=176),
        ChoreographySection(section_id=GeometrySectionId.OUTRO, start_seconds=176, end_seconds=192),
        ChoreographySection(
            section_id=GeometrySectionId.POST_GRID_TAIL,
            start_seconds=192,
            end_seconds=master_duration_seconds,
        ),
    ]


def resolve_choreography(choreography: GeometryChoreography, seconds: float) -> ResolvedGeometryState:
    if not math.isfinite(seconds) or not 0 <= seconds <= choreography.master_duration_seconds:
        raise ValueError("timeline position must be finite and inside the master")
    section = choreography.section_at(seconds).section_id
    held_shape = choreography.transitions[0].shape_a
    for transition in choreography.transitions:
        start = musical_time_to_seconds(
            transition.start,
            choreography.bpm,
            choreography.beats_per_bar,
        )
        duration = musical_time_to_seconds(
            transition.duration,
            choreography.bpm,
            choreography.beats_per_bar,
        )
        end = start + duration
        if seconds < start:
            return ResolvedGeometryState(
                seconds=seconds,
                section=section,
                shape_a=held_shape,
                shape_b=held_shape,
                raw_progress=1,
                eased_progress=1,
                transition_active=False,
            )
        if seconds <= end:
            raw_progress = min(1.0, max(0.0, (seconds - start) / duration))
            return ResolvedGeometryState(
                seconds=seconds,
                section=section,
                transition_id=transition.transition_id,
                shape_a=transition.shape_a,
                shape_b=transition.shape_b,
                raw_progress=raw_progress,
                eased_progress=easing_progress(raw_progress, transition.easing),
                transition_active=True,
            )
        held_shape = transition.shape_b
    return ResolvedGeometryState(
        seconds=seconds,
        section=section,
        shape_a=held_shape,
        shape_b=held_shape,
        raw_progress=1,
        eased_progress=1,
        transition_active=False,
    )

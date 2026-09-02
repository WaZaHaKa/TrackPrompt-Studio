from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

COMPOSITION_MASTER_DURATION_SECONDS = 196.619796
NormalizedCoordinate = Annotated[float, Field(ge=0, le=1, strict=True)]
LocalizedRadius = Annotated[float, Field(ge=0.02, le=0.25, strict=True)]


class CompositionModel(BaseModel):
    """Finite, typed, local-only composition data with no executable content."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class GeometryProductionElements(CompositionModel):
    logo_visible: Literal[True]
    artist_visible: Literal[True]
    title_visible: Literal[True]
    spectrum_bars_visible: Literal[False]
    spectral_ribbon_visible: Literal[False]
    technical_metadata_visible: Literal[False]
    section_labels_visible: Literal[False]

    @field_validator(
        "logo_visible",
        "artist_visible",
        "title_visible",
        "spectrum_bars_visible",
        "spectral_ribbon_visible",
        "technical_metadata_visible",
        "section_labels_visible",
        mode="before",
    )
    @classmethod
    def require_actual_boolean(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("production visibility flags must be explicit booleans")
        return value


class ReadabilityZone(CompositionModel):
    id: Literal["logo", "identity"]
    center: tuple[NormalizedCoordinate, NormalizedCoordinate]
    radius: tuple[LocalizedRadius, LocalizedRadius]
    strength: float = Field(ge=0, le=0.75, strict=True)


class GeometryReadability(CompositionModel):
    mode: Literal["soft-ellipses"]
    minimum_brightness: float = Field(ge=0.25, le=1, strict=True)
    halo_suppression: float = Field(ge=0, le=1, strict=True)
    zones: list[ReadabilityZone] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_localized_identity_zones(self) -> Self:
        if {zone.id for zone in self.zones} != {"logo", "identity"}:
            raise ValueError("the readability mask must contain one logo and one identity ellipse")
        return self


class GeometryFraming(CompositionModel):
    center: tuple[NormalizedCoordinate, NormalizedCoordinate]
    shape_scale: float = Field(ge=0.25, le=2, strict=True)
    depth_strength: float = Field(ge=0, le=1, strict=True)


class CompositionEnvelopePoint(CompositionModel):
    time_seconds: float = Field(
        ge=0,
        le=COMPOSITION_MASTER_DURATION_SECONDS,
        strict=True,
    )
    density: float = Field(ge=0, le=1, strict=True)
    brightness: float = Field(ge=0, le=1, strict=True)
    scale: float = Field(ge=0.25, le=2, strict=True)
    deformation: float = Field(ge=0, le=2, strict=True)


class GeometryComposition(CompositionModel):
    schema_version: Literal["1.0.0"]
    revision: Literal["scattered-geometry-first-3.7"]
    geometry_coverage: Literal["full-frame"]
    production: GeometryProductionElements
    readability: GeometryReadability
    framing: GeometryFraming
    envelope: list[CompositionEnvelopePoint] = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def validate_intentional_media_envelope(self) -> Self:
        if self.envelope[0].time_seconds != 0:
            raise ValueError("the composition envelope must start at zero")
        if not math.isclose(
            self.envelope[-1].time_seconds,
            COMPOSITION_MASTER_DURATION_SECONDS,
            rel_tol=0,
            abs_tol=0.000001,
        ):
            raise ValueError("the composition envelope must include the intentional master EOF")
        if any(
            earlier.time_seconds >= later.time_seconds
            for earlier, later in zip(self.envelope, self.envelope[1:], strict=False)
        ):
            raise ValueError("composition envelope times must increase strictly")
        if self.envelope[-1].density != 0 or self.envelope[-1].brightness != 0:
            raise ValueError("the composition envelope must resolve geometry density and brightness at EOF")
        return self


@dataclass(frozen=True, slots=True)
class ReadabilityAttenuation:
    """Separate point-core and halo multipliers, both strictly positive."""

    intensity: float
    halo: float


def readability_at(
    composition: GeometryComposition,
    x: float,
    y: float,
) -> ReadabilityAttenuation:
    """Measure soft mask attenuation at normalized, top-left-origin canvas coordinates.

    Each ellipse uses ``exp(-2 * distance_squared)``. Multiplicative retained
    intensity avoids a hard boundary and preserves geometry beneath the identity.
    The bounded two-zone strengths keep halo attenuation positive even at maximal
    suppression; minimum brightness supplies an additional point-core floor.
    """

    if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("readability coordinates must be finite normalized canvas coordinates")
    retained = 1.0
    for zone in composition.readability.zones:
        dx = (x - zone.center[0]) / zone.radius[0]
        dy = (y - zone.center[1]) / zone.radius[1]
        weight = math.exp(-2.0 * (dx * dx + dy * dy))
        retained *= 1.0 - zone.strength * weight
    coverage = 1.0 - retained
    intensity = max(composition.readability.minimum_brightness, retained)
    halo = intensity * (1.0 - composition.readability.halo_suppression * coverage)
    return ReadabilityAttenuation(intensity=intensity, halo=halo)


def _smootherstep(progress: float) -> float:
    return min(1.0, max(0.0, progress**3 * (progress * (progress * 6.0 - 15.0) + 10.0)))


def resolve_composition_envelope(
    composition: GeometryComposition,
    seconds: float,
) -> CompositionEnvelopePoint:
    """Resolve a deterministic smootherstep envelope, clamped to the media span."""

    if not math.isfinite(seconds):
        raise ValueError("composition envelope time must be finite")
    points = composition.envelope
    resolved_seconds = min(points[-1].time_seconds, max(0.0, seconds))
    if resolved_seconds == points[0].time_seconds:
        return points[0].model_copy()
    for start, end in zip(points, points[1:], strict=False):
        if resolved_seconds > end.time_seconds:
            continue
        if resolved_seconds == end.time_seconds:
            return end.model_copy()
        progress = _smootherstep(
            (resolved_seconds - start.time_seconds) / (end.time_seconds - start.time_seconds)
        )
        return CompositionEnvelopePoint(
            time_seconds=resolved_seconds,
            density=start.density + (end.density - start.density) * progress,
            brightness=start.brightness + (end.brightness - start.brightness) * progress,
            scale=start.scale + (end.scale - start.scale) * progress,
            deformation=start.deformation + (end.deformation - start.deformation) * progress,
        )
    return points[-1].model_copy()

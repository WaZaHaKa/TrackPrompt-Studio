from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class GenerativeModel(BaseModel):
    """Strict base model for deterministic generative design data."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        allow_inf_nan=False,
        use_enum_values=False,
    )


class ShapeId(StrEnum):
    SPARSE_FIELD = "sparse-field"
    LISSAJOUS = "lissajous"
    MATRIX_FIELD = "matrix-field"
    WAVE_SURFACE = "wave-surface"
    TORUS = "torus"
    TWISTED_TORUS = "twisted-torus"
    TREFOIL_KNOT = "trefoil-knot"
    SUPERFORMULA = "superformula"
    SPHERICAL_LATTICE = "spherical-lattice"
    DISPERSED_FIELD = "dispersed-field"


class EasingId(StrEnum):
    SMOOTHSTEP = "smoothstep"
    SMOOTHERSTEP = "smootherstep"
    CUBIC = "cubic"
    SINUSOIDAL = "sinusoidal"


class GeometrySectionId(StrEnum):
    INTRO = "intro"
    MAIN = "main"
    OUTRO = "outro"
    POST_GRID_TAIL = "post-grid-tail"


class MusicalTimeUnit(StrEnum):
    SECONDS = "seconds"
    BEATS = "beats"
    BARS = "bars"


class GeometryPreviewMode(StrEnum):
    SHAPE = "shape"
    MORPH = "morph"
    SECTION = "section"
    LAB = "lab"


class PreviewAudioMode(StrEnum):
    DISABLED = "disabled"
    SIMULATED = "simulated"


class IndexedDomainSpec(GenerativeModel):
    point_count: int = Field(ge=16, le=65_536)
    columns: int | None = Field(default=None, ge=2, le=4_096)

    @model_validator(mode="after")
    def validate_columns(self) -> Self:
        if self.columns is not None and self.columns > self.point_count:
            raise ValueError("domain columns cannot exceed the point count")
        return self


class ShapeSpec(GenerativeModel):
    """Trusted shape identity plus bounded built-in parameters.

    The shared parameter surface keeps choreography serialization simple. A shape
    ignores parameters that are not meaningful to its built-in sampler.
    """

    shape_id: ShapeId
    seed: int = Field(default=0, ge=0, le=2_147_483_647)
    scale: float = Field(default=1.0, gt=0, le=4)
    phase: float = Field(default=0.0, ge=-25.132_741_228_7, le=25.132_741_228_7)
    amplitude: float = Field(default=0.35, ge=0, le=2)
    frequency: float = Field(default=3.0, gt=0, le=32)
    twist: float = Field(default=2.0, ge=-16, le=16)
    spread: float = Field(default=1.0, ge=0, le=8)
    tube_radius: float = Field(default=0.06, ge=0, le=0.5)
    superformula_m: int = Field(default=6, ge=1, le=32)
    superformula_n1: float = Field(default=1.0, gt=0, le=32)
    superformula_n2: float = Field(default=1.0, gt=0, le=32)
    superformula_n3: float = Field(default=1.0, gt=0, le=32)


class MusicalTime(GenerativeModel):
    value: float = Field(ge=0, le=86_400)
    unit: MusicalTimeUnit = MusicalTimeUnit.SECONDS


class GeometryPreviewOverride(GenerativeModel):
    """Typed, preview-only override for focused inspection and shape-lab use."""

    mode: GeometryPreviewMode
    shape_a: ShapeSpec | None = None
    shape_b: ShapeSpec | None = None
    morph_progress: float | None = Field(default=None, ge=0, le=1)
    section: GeometrySectionId | None = None
    point_count: int | None = Field(default=None, ge=16, le=16_384)
    rotation_degrees: float = Field(default=0, ge=-360, le=360)
    scale: float = Field(default=1, gt=0, le=4)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    audio_mode: PreviewAudioMode = PreviewAudioMode.DISABLED

    @model_validator(mode="after")
    def validate_mode_payload(self) -> Self:
        if self.mode is GeometryPreviewMode.SHAPE:
            if self.shape_a is None:
                raise ValueError("shape preview requires shapeA")
            if self.shape_b is not None or self.morph_progress is not None or self.section is not None:
                raise ValueError("shape preview accepts only shapeA")
        elif self.mode is GeometryPreviewMode.MORPH:
            if self.shape_a is None or self.shape_b is None or self.morph_progress is None:
                raise ValueError("morph preview requires shapeA, shapeB, and morphProgress")
            if self.section is not None:
                raise ValueError("morph preview cannot fix a choreography section")
        elif self.mode is GeometryPreviewMode.SECTION:
            if self.section is None:
                raise ValueError("section preview requires a section")
            if self.shape_a is not None or self.shape_b is not None or self.morph_progress is not None:
                raise ValueError("section preview cannot replace canonical shapes")
        else:
            if self.shape_a is None:
                raise ValueError("lab preview requires shapeA")
            if self.morph_progress is not None and self.shape_b is None:
                raise ValueError("lab morphProgress requires shapeB")
        return self


class GeometryPreviewRequest(GenerativeModel):
    """Wrapper that makes preview overrides impossible on a production request."""

    mode: Literal["preview"] = "preview"
    override: GeometryPreviewOverride

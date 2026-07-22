from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal, TypeAlias

from pydantic import Field, TypeAdapter, field_validator

from ..schemas import (
    BLENDER_VISUALIZER_CONFIG_SCHEMA_VERSION,
    BLENDER_VISUALIZER_PRESETS,
    APIModel,
    VisualizerPreset,
)

VISUALIZER_CONFIG_SCHEMA_VERSION: Literal["1.0.0"] = (
    BLENDER_VISUALIZER_CONFIG_SCHEMA_VERSION
)
DEFAULT_VISUALIZER_SEED = 84291
MAX_VISUALIZER_SEED = 2_147_483_647


class SpaceJourneyPalette(StrEnum):
    ANDROMEDA = "andromeda"
    DEEP_SPACE = "deep-space"
    CYAN_VIOLET = "cyan-violet"
    VIOLET_MAGENTA = "violet-magenta"
    MONOCHROME_BLUE = "monochrome-blue"
    DARK_AMBER = "dark-amber"


class AbstractGeometryParameters(APIModel):
    """The legacy preset has no public tuning parameters in this contract."""


class SpaceJourneyParameters(APIModel):
    camera_distance: float = Field(default=18.0, ge=8.0, le=40.0, strict=True)
    camera_orbit_speed: float = Field(default=0.15, ge=0.0, le=0.5, strict=True)
    ring_thickness: float = Field(default=0.06, ge=0.02, le=0.20, strict=True)
    ring_occlusion: float = Field(default=0.20, ge=0.0, le=1.0, strict=True)
    palette: SpaceJourneyPalette = SpaceJourneyPalette.ANDROMEDA
    glow_strength: float = Field(default=1.8, ge=0.0, le=4.0, strict=True)
    shard_density: float = Field(default=0.35, ge=0.0, le=1.0, strict=True)
    fog_depth: float = Field(default=0.50, ge=0.0, le=1.0, strict=True)
    bass_response: float = Field(default=1.2, ge=0.0, le=2.0, strict=True)
    drum_response: float = Field(default=0.9, ge=0.0, le=2.0, strict=True)
    vocal_response: float = Field(default=0.65, ge=0.0, le=2.0, strict=True)

    @field_validator(
        "camera_distance",
        "camera_orbit_speed",
        "ring_thickness",
        "ring_occlusion",
        "glow_strength",
        "shard_density",
        "fog_depth",
        "bass_response",
        "drum_response",
        "vocal_response",
    )
    @classmethod
    def finite_parameter(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("visualizer parameters must be finite")
        return value


class AbstractVisualizerConfigRequest(APIModel):
    schema_version: Literal["1.0.0"] = VISUALIZER_CONFIG_SCHEMA_VERSION
    preset: Literal["abstract-geometry"] = "abstract-geometry"
    parameters: AbstractGeometryParameters = Field(
        default_factory=AbstractGeometryParameters
    )
    seed: int = Field(
        default=DEFAULT_VISUALIZER_SEED,
        ge=0,
        le=MAX_VISUALIZER_SEED,
        strict=True,
    )


class SpaceJourneyVisualizerConfigRequest(APIModel):
    schema_version: Literal["1.0.0"] = VISUALIZER_CONFIG_SCHEMA_VERSION
    preset: Literal["space-journey"]
    parameters: SpaceJourneyParameters = Field(default_factory=SpaceJourneyParameters)
    seed: int = Field(
        default=DEFAULT_VISUALIZER_SEED,
        ge=0,
        le=MAX_VISUALIZER_SEED,
        strict=True,
    )


class SpaceJourneyStoryVisualizerConfigRequest(APIModel):
    schema_version: Literal["1.0.0"] = VISUALIZER_CONFIG_SCHEMA_VERSION
    preset: Literal["space-journey-story"]
    parameters: SpaceJourneyParameters = Field(default_factory=SpaceJourneyParameters)
    seed: int = Field(
        default=DEFAULT_VISUALIZER_SEED,
        ge=0,
        le=MAX_VISUALIZER_SEED,
        strict=True,
    )


VisualizerConfigRequest: TypeAlias = (
    AbstractVisualizerConfigRequest
    | SpaceJourneyVisualizerConfigRequest
    | SpaceJourneyStoryVisualizerConfigRequest
)
_VISUALIZER_CONFIG_REQUEST_ADAPTER: TypeAdapter[VisualizerConfigRequest] = TypeAdapter(
    VisualizerConfigRequest
)


class AbstractResolvedVisualizerConfig(APIModel):
    schema_version: Literal["1.0.0"] = VISUALIZER_CONFIG_SCHEMA_VERSION
    preset: Literal["abstract-geometry"] = "abstract-geometry"
    parameters: AbstractGeometryParameters
    seed: int = Field(ge=0, le=MAX_VISUALIZER_SEED)
    defaulted_parameters: list[str] = Field(default_factory=list, max_length=32)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class SpaceJourneyResolvedVisualizerConfig(APIModel):
    schema_version: Literal["1.0.0"] = VISUALIZER_CONFIG_SCHEMA_VERSION
    preset: Literal["space-journey"] = "space-journey"
    parameters: SpaceJourneyParameters
    seed: int = Field(ge=0, le=MAX_VISUALIZER_SEED)
    defaulted_parameters: list[str] = Field(default_factory=list, max_length=32)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class SpaceJourneyStoryResolvedVisualizerConfig(APIModel):
    schema_version: Literal["1.0.0"] = VISUALIZER_CONFIG_SCHEMA_VERSION
    preset: Literal["space-journey-story"] = "space-journey-story"
    parameters: SpaceJourneyParameters
    seed: int = Field(ge=0, le=MAX_VISUALIZER_SEED)
    defaulted_parameters: list[str] = Field(default_factory=list, max_length=32)
    warnings: list[str] = Field(
        default_factory=lambda: ["preview_only_requires_v2_calibration_and_authorization"],
        max_length=20,
    )


ResolvedVisualizerConfig: TypeAlias = (
    AbstractResolvedVisualizerConfig
    | SpaceJourneyResolvedVisualizerConfig
    | SpaceJourneyStoryResolvedVisualizerConfig
)


def validate_visualizer_config_request(payload: object) -> VisualizerConfigRequest:
    """Validate non-HTTP callers through the same concrete request union."""

    return _VISUALIZER_CONFIG_REQUEST_ADAPTER.validate_python(payload)


def _defaulted_parameter_aliases(
    parameters: AbstractGeometryParameters | SpaceJourneyParameters,
) -> list[str]:
    return sorted(
        str(field_info.serialization_alias or field_info.alias or field_name)
        for field_name, field_info in type(parameters).model_fields.items()
        if field_name not in parameters.model_fields_set
    )


def resolve_visualizer_config(
    request: VisualizerConfigRequest,
) -> ResolvedVisualizerConfig:
    defaulted = _defaulted_parameter_aliases(request.parameters)
    if isinstance(request, AbstractVisualizerConfigRequest):
        return AbstractResolvedVisualizerConfig(
            parameters=request.parameters,
            seed=request.seed,
            defaulted_parameters=defaulted,
            warnings=[],
        )
    if isinstance(request, SpaceJourneyVisualizerConfigRequest):
        return SpaceJourneyResolvedVisualizerConfig(
            parameters=request.parameters,
            seed=request.seed,
            defaulted_parameters=defaulted,
            warnings=[],
        )
    return SpaceJourneyStoryResolvedVisualizerConfig(
        parameters=request.parameters,
        seed=request.seed,
        defaulted_parameters=defaulted,
    )


def supported_visualizer_presets() -> tuple[VisualizerPreset, ...]:
    return BLENDER_VISUALIZER_PRESETS

from __future__ import annotations

import importlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from .validation import VisualizerValidationError

CONFIG_SCHEMA_VERSION = "1.0.0"
DEFAULT_PRESET = "abstract-geometry"
DEFAULT_SEED = 84291
MAX_CONFIG_BYTES = 1_000_000
MAX_CONFIG_WARNINGS = 32
MAX_CONFIG_WARNING_LENGTH = 240
_PRIVATE_WARNING_PATH = re.compile(r"(?i)(?:^|\s)(?:[a-z]:[\\/]|/(?:users|home|data)/)")

PresetIdentifier: TypeAlias = Literal[
    "abstract-geometry", "space-journey", "space-journey-story"
]
ParameterValue: TypeAlias = float | str

SPACE_JOURNEY_PALETTES = (
    "andromeda",
    "deep-space",
    "cyan-violet",
    "violet-magenta",
    "monochrome-blue",
    "dark-amber",
)

SPACE_JOURNEY_DEFAULTS: dict[str, ParameterValue] = {
    "cameraDistance": 18.0,
    "cameraOrbitSpeed": 0.15,
    "ringThickness": 0.06,
    "ringOcclusion": 0.20,
    "palette": "andromeda",
    "glowStrength": 1.8,
    "shardDensity": 0.35,
    "fogDepth": 0.50,
    "bassResponse": 1.2,
    "drumResponse": 0.9,
    "vocalResponse": 0.65,
}

_SPACE_JOURNEY_BOUNDS: dict[str, tuple[float, float]] = {
    "cameraDistance": (8.0, 40.0),
    "cameraOrbitSpeed": (0.0, 0.5),
    "ringThickness": (0.02, 0.20),
    "ringOcclusion": (0.0, 1.0),
    "glowStrength": (0.0, 4.0),
    "shardDensity": (0.0, 1.0),
    "fogDepth": (0.0, 1.0),
    "bassResponse": (0.0, 2.0),
    "drumResponse": (0.0, 2.0),
    "vocalResponse": (0.0, 2.0),
}

COMMON_COLLECTIONS = (
    "TP_WORLD",
    "TP_CAMERAS",
    "TP_LIGHTS",
    "TP_PRIMARY_GEOMETRY",
    "TP_RINGS",
    "TP_SHARDS",
    "TP_VOCAL_ELEMENTS",
    "TP_BACKGROUND",
    "TP_DEBUG",
)

SPACE_JOURNEY_COLLECTIONS = (
    "TP_DESTINATION",
    "TP_SPACE_ENVIRONMENT",
    "TP_STARFIELD",
    "TP_NEBULA",
    "TP_TRAVEL_PATHS",
)

SPACE_JOURNEY_STORY_COLLECTIONS = (
    "TP_STORY",
    "TP_PROTAGONIST",
    "TP_NARRATIVE_ENVIRONMENTS",
    "TP_CAMERA_RIGS",
)


@dataclass(frozen=True)
class PresetDefinition:
    identifier: PresetIdentifier
    label: str
    module: str
    builder_name: str
    parameter_names: tuple[str, ...]
    required_collections: tuple[str, ...]
    preview_clip_name: str

    def load_builder(self) -> Callable[..., dict[str, Any]]:
        module = importlib.import_module(self.module, package=__package__)
        builder = getattr(module, self.builder_name, None)
        if not callable(builder):
            raise RuntimeError(f"Visualizer preset builder {self.identifier!r} is unavailable.")
        return cast(Callable[..., dict[str, Any]], builder)


PRESET_REGISTRY: dict[PresetIdentifier, PresetDefinition] = {
    "abstract-geometry": PresetDefinition(
        identifier="abstract-geometry",
        label="Abstract Geometry",
        module=".preset_abstract_geometry",
        builder_name="build_abstract_geometry",
        parameter_names=(),
        required_collections=COMMON_COLLECTIONS,
        preview_clip_name="trackprompt-preview.mp4",
    ),
    "space-journey": PresetDefinition(
        identifier="space-journey",
        label="Space Journey",
        module=".preset_space_journey",
        builder_name="build_space_journey",
        parameter_names=tuple(SPACE_JOURNEY_DEFAULTS),
        required_collections=COMMON_COLLECTIONS + SPACE_JOURNEY_COLLECTIONS,
        preview_clip_name="space-journey-preview.mp4",
    ),
    "space-journey-story": PresetDefinition(
        identifier="space-journey-story",
        label="Space Journey Story V2",
        module=".preset_space_journey_story",
        builder_name="build_space_journey_story",
        parameter_names=tuple(SPACE_JOURNEY_DEFAULTS),
        required_collections=(
            COMMON_COLLECTIONS
            + SPACE_JOURNEY_COLLECTIONS
            + SPACE_JOURNEY_STORY_COLLECTIONS
        ),
        preview_clip_name="space-journey-story-preview.mp4",
    ),
}

SUPPORTED_PRESETS: tuple[PresetIdentifier, ...] = tuple(PRESET_REGISTRY)


@dataclass(frozen=True)
class ResolvedVisualizerConfig:
    preset: PresetIdentifier
    parameters: dict[str, ParameterValue]
    seed: int
    defaulted_parameters: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "preset": self.preset,
            "parameters": dict(self.parameters),
            "seed": self.seed,
            "defaultedParameters": list(self.defaulted_parameters),
            "warnings": list(self.warnings),
        }


def get_preset_definition(value: object) -> PresetDefinition:
    if not isinstance(value, str) or value not in PRESET_REGISTRY:
        supported = ", ".join(SUPPORTED_PRESETS)
        raise VisualizerValidationError(f"Unsupported visualizer preset. Supported presets: {supported}.")
    return PRESET_REGISTRY[cast(PresetIdentifier, value)]


def _reject_nonfinite(token: str) -> None:
    raise VisualizerValidationError(f"Non-finite JSON number {token!r} is not allowed in visualizer config.")


def _load_config(path: str | Path) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise VisualizerValidationError("Visualizer config path must be absolute.")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise VisualizerValidationError("Visualizer config file does not exist.") from exc
    if not resolved.is_file() or resolved.suffix.casefold() != ".json":
        raise VisualizerValidationError("Visualizer config must be a JSON file.")
    if resolved.stat().st_size <= 0 or resolved.stat().st_size > MAX_CONFIG_BYTES:
        raise VisualizerValidationError("Visualizer config file size is invalid.")
    try:
        parsed = json.loads(resolved.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VisualizerValidationError("Visualizer config is not valid UTF-8 JSON.") from exc
    if not isinstance(parsed, dict):
        raise VisualizerValidationError("Visualizer config must be a JSON object.")
    allowed = {"schemaVersion", "preset", "parameters", "seed", "defaultedParameters", "warnings"}
    unknown = sorted(str(key) for key in parsed if key not in allowed)
    if unknown:
        raise VisualizerValidationError(f"Visualizer config contains unknown fields: {', '.join(unknown)}.")
    if parsed.get("schemaVersion") != CONFIG_SCHEMA_VERSION:
        raise VisualizerValidationError("Unsupported visualizer config schema version.")
    if "preset" not in parsed or "parameters" not in parsed:
        raise VisualizerValidationError("Visualizer config must contain preset and parameters.")
    if not isinstance(parsed["parameters"], dict):
        raise VisualizerValidationError("Visualizer config parameters must be an object.")
    defaulted = parsed.get("defaultedParameters", [])
    if not isinstance(defaulted, list) or any(not isinstance(item, str) for item in defaulted):
        raise VisualizerValidationError("Visualizer config defaultedParameters must be a string array.")
    warnings = parsed.get("warnings", [])
    if not isinstance(warnings, list) or len(warnings) > MAX_CONFIG_WARNINGS:
        raise VisualizerValidationError("Visualizer config warnings must be a bounded string array.")
    for warning in warnings:
        if (
            not isinstance(warning, str)
            or not warning
            or warning != warning.strip()
            or len(warning) > MAX_CONFIG_WARNING_LENGTH
            or any(ord(character) < 32 for character in warning)
            or _PRIVATE_WARNING_PATH.search(warning)
        ):
            raise VisualizerValidationError("Visualizer config contains an unsafe warning value.")
    return parsed


def _seed(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VisualizerValidationError("Seed must be an integer.")
    if not 0 <= value <= 2_147_483_647:
        raise VisualizerValidationError("Seed must be between 0 and 2147483647.")
    return value


def _space_parameters(
    supplied: Mapping[str, object],
    declared_defaulted: set[str] | None,
) -> tuple[dict[str, ParameterValue], tuple[str, ...]]:
    unknown = sorted(str(key) for key in supplied if key not in SPACE_JOURNEY_DEFAULTS)
    if unknown:
        raise VisualizerValidationError(f"Unknown Space Journey parameters: {', '.join(unknown)}.")
    if declared_defaulted is not None:
        invalid_defaulted = sorted(declared_defaulted.difference(SPACE_JOURNEY_DEFAULTS))
        if invalid_defaulted:
            raise VisualizerValidationError(
                f"Unknown defaulted Space Journey parameters: {', '.join(invalid_defaulted)}."
            )
    resolved: dict[str, ParameterValue] = {}
    defaulted: set[str] = set()
    for name, default in SPACE_JOURNEY_DEFAULTS.items():
        if name not in supplied:
            resolved[name] = default
            defaulted.add(name)
            continue
        value = supplied[name]
        if name == "palette":
            if not isinstance(value, str) or value not in SPACE_JOURNEY_PALETTES:
                allowed = ", ".join(SPACE_JOURNEY_PALETTES)
                raise VisualizerValidationError(f"palette must be one of: {allowed}.")
            resolved[name] = value
            continue
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
            raise VisualizerValidationError(f"{name} must be a finite number.")
        lower, upper = _SPACE_JOURNEY_BOUNDS[name]
        numeric = float(value)
        if not lower <= numeric <= upper:
            raise VisualizerValidationError(f"{name} must be between {lower:g} and {upper:g}.")
        resolved[name] = numeric
    if declared_defaulted is not None:
        for name in declared_defaulted:
            if resolved[name] != SPACE_JOURNEY_DEFAULTS[name]:
                raise VisualizerValidationError(f"Defaulted parameter {name} does not contain its default value.")
        defaulted.update(declared_defaulted)
    return resolved, tuple(sorted(defaulted))


def resolve_visualizer_config(
    preset: str = DEFAULT_PRESET,
    seed: int = DEFAULT_SEED,
    *,
    config_path: str | Path | None = None,
    parameters: Mapping[str, object] | None = None,
) -> tuple[PresetDefinition, ResolvedVisualizerConfig]:
    parsed = _load_config(config_path) if config_path is not None else None
    requested_preset = preset
    requested_seed: object = seed
    supplied: dict[str, object] = {}
    declared_defaulted: set[str] | None = None
    config_warnings: tuple[str, ...] = ()
    if parsed is not None:
        config_preset = parsed["preset"]
        get_preset_definition(config_preset)
        if preset != DEFAULT_PRESET and preset != config_preset:
            raise VisualizerValidationError("Visualizer config preset does not match the requested preset.")
        requested_preset = cast(str, config_preset)
        if "seed" in parsed:
            config_seed = _seed(parsed["seed"])
            if seed != DEFAULT_SEED and seed != config_seed:
                raise VisualizerValidationError("Visualizer config seed does not match the requested seed.")
            requested_seed = config_seed
        supplied.update(cast(dict[str, object], parsed["parameters"]))
        if "defaultedParameters" in parsed:
            declared_defaulted = set(cast(list[str], parsed["defaultedParameters"]))
        config_warnings = tuple(cast(list[str], parsed.get("warnings", [])))
    definition = get_preset_definition(requested_preset)
    resolved_seed = _seed(requested_seed)
    if parameters is not None:
        if not isinstance(parameters, Mapping):
            raise VisualizerValidationError("Visualizer parameters must be an object.")
        supplied.update(parameters)
        if declared_defaulted is not None:
            declared_defaulted.difference_update(str(key) for key in parameters)
    if definition.identifier == "abstract-geometry":
        if supplied:
            unknown = ", ".join(sorted(str(key) for key in supplied))
            raise VisualizerValidationError(f"Abstract Geometry does not accept parameters: {unknown}.")
        resolved_parameters: dict[str, ParameterValue] = {}
        defaulted_parameters: tuple[str, ...] = ()
    else:
        resolved_parameters, defaulted_parameters = _space_parameters(supplied, declared_defaulted)
    config = ResolvedVisualizerConfig(
        preset=definition.identifier,
        parameters=resolved_parameters,
        seed=resolved_seed,
        defaulted_parameters=defaulted_parameters,
        warnings=config_warnings,
    )
    return definition, config

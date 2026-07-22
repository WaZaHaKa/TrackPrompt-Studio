from __future__ import annotations

import json
from pathlib import Path

import pytest

from trackprompt_visualizer.preset_registry import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_PRESET,
    DEFAULT_SEED,
    SPACE_JOURNEY_DEFAULTS,
    SPACE_JOURNEY_PALETTES,
    SUPPORTED_PRESETS,
    get_preset_definition,
    resolve_visualizer_config,
)
from trackprompt_visualizer.validation import VisualizerValidationError


def _write_config(tmp_path: Path, **changes: object) -> Path:
    payload: dict[str, object] = {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "preset": "space-journey",
        "parameters": {},
        "seed": DEFAULT_SEED,
        "defaultedParameters": [],
        "warnings": [],
    }
    payload.update(changes)
    path = tmp_path / "visualizer-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path.resolve()


def test_registry_preserves_abstract_default_and_enumerates_space_journey() -> None:
    definition, config = resolve_visualizer_config()
    assert DEFAULT_PRESET == "abstract-geometry"
    assert SUPPORTED_PRESETS == (
        "abstract-geometry",
        "space-journey",
        "space-journey-story",
    )
    assert definition.identifier == "abstract-geometry"
    assert definition.preview_clip_name == "trackprompt-preview.mp4"
    assert config.to_public_dict() == {
        "schemaVersion": "1.0.0",
        "preset": "abstract-geometry",
        "parameters": {},
        "seed": 84291,
        "defaultedParameters": [],
        "warnings": [],
    }
    assert get_preset_definition("space-journey").preview_clip_name == "space-journey-preview.mp4"
    assert (
        get_preset_definition("space-journey-story").preview_clip_name
        == "space-journey-story-preview.mp4"
    )


def test_story_preset_reuses_v1_parameters_but_has_distinct_identity() -> None:
    v1_definition, v1 = resolve_visualizer_config("space-journey")
    v2_definition, v2 = resolve_visualizer_config("space-journey-story")
    assert v1.parameters == v2.parameters == SPACE_JOURNEY_DEFAULTS
    assert v1_definition.identifier == "space-journey"
    assert v2_definition.identifier == "space-journey-story"
    assert v1.to_public_dict()["preset"] != v2.to_public_dict()["preset"]
    assert "TP_STORY" not in v1_definition.required_collections
    assert "TP_STORY" in v2_definition.required_collections


def test_space_defaults_and_bounds_are_resolved_with_public_names() -> None:
    definition, config = resolve_visualizer_config("space-journey")
    assert definition.identifier == "space-journey"
    assert config.parameters == SPACE_JOURNEY_DEFAULTS
    assert config.defaulted_parameters == tuple(sorted(SPACE_JOURNEY_DEFAULTS))
    assert config.parameters["palette"] == "andromeda"
    assert set(SPACE_JOURNEY_PALETTES) == {
        "andromeda",
        "deep-space",
        "cyan-violet",
        "violet-magenta",
        "monochrome-blue",
        "dark-amber",
    }


def test_config_file_and_typed_revision_overlay_preserve_safe_warnings(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        parameters={**SPACE_JOURNEY_DEFAULTS, "cameraDistance": 22.0},
        defaultedParameters=[name for name in SPACE_JOURNEY_DEFAULTS if name != "cameraDistance"],
        warnings=["preview_quality_fog_approximation"],
    )
    definition, config = resolve_visualizer_config(
        "space-journey",
        DEFAULT_SEED,
        config_path=path,
        parameters={"cameraDistance": 24.0, "palette": "cyan-violet"},
    )
    assert definition.identifier == "space-journey"
    assert config.parameters["cameraDistance"] == 24.0
    assert config.parameters["palette"] == "cyan-violet"
    assert "cameraDistance" not in config.defaulted_parameters
    assert "palette" not in config.defaulted_parameters
    assert config.warnings == ("preview_quality_fog_approximation",)


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({"unknown": 1}, "Unknown"),
        ({"cameraDistance": float("nan")}, "finite"),
        ({"cameraDistance": float("inf")}, "finite"),
        ({"cameraDistance": True}, "finite"),
        ({"cameraDistance": 7.99}, "between"),
        ({"cameraOrbitSpeed": 0.5001}, "between"),
        ({"ringThickness": -0.1}, "between"),
        ({"palette": "rainbow"}, "palette"),
        ({"glowStrength": 4.01}, "between"),
        ({"shardDensity": -0.01}, "between"),
        ({"fogDepth": 1.01}, "between"),
        ({"bassResponse": 2.01}, "between"),
        ({"drumResponse": -0.01}, "between"),
        ({"vocalResponse": "high"}, "finite"),
    ],
)
def test_invalid_space_parameters_are_rejected(parameters: dict[str, object], message: str) -> None:
    with pytest.raises(VisualizerValidationError, match=message):
        resolve_visualizer_config("space-journey", parameters=parameters)


def test_config_rejects_unknown_top_level_fields_private_warnings_and_mismatch(tmp_path: Path) -> None:
    unknown = _write_config(tmp_path, unexpected=True)
    with pytest.raises(VisualizerValidationError, match="unknown fields"):
        resolve_visualizer_config("space-journey", config_path=unknown)

    private_warning = _write_config(tmp_path, warnings=[r"read C:\private\track.wav"])
    with pytest.raises(VisualizerValidationError, match="unsafe warning"):
        resolve_visualizer_config("space-journey", config_path=private_warning)

    mismatched = _write_config(tmp_path, preset="abstract-geometry")
    with pytest.raises(VisualizerValidationError, match="does not match"):
        resolve_visualizer_config("space-journey", config_path=mismatched)


def test_nonfinite_json_and_abstract_parameters_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nonfinite.json"
    path.write_text(
        '{"schemaVersion":"1.0.0","preset":"space-journey","parameters":{"fogDepth":NaN}}',
        encoding="utf-8",
    )
    with pytest.raises(VisualizerValidationError, match="Non-finite"):
        resolve_visualizer_config("space-journey", config_path=path.resolve())
    with pytest.raises(VisualizerValidationError, match="does not accept"):
        resolve_visualizer_config("abstract-geometry", parameters={"glowStrength": 1.0})

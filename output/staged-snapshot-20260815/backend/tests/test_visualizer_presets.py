from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from app.schemas import VisualizerPreset
from app.visualizer.presets import (
    DEFAULT_VISUALIZER_SEED,
    AbstractVisualizerConfigRequest,
    SpaceJourneyParameters,
    SpaceJourneyVisualizerConfigRequest,
    resolve_visualizer_config,
    supported_visualizer_presets,
    validate_visualizer_config_request,
)


def test_registry_and_default_config_preserve_abstract_geometry() -> None:
    assert supported_visualizer_presets() == (
        VisualizerPreset.ABSTRACT_GEOMETRY,
        VisualizerPreset.SPACE_JOURNEY,
        VisualizerPreset.SPACE_JOURNEY_STORY,
    )
    resolved = resolve_visualizer_config(AbstractVisualizerConfigRequest())
    assert resolved.model_dump(mode="json", by_alias=True) == {
        "schemaVersion": "1.0.0",
        "preset": "abstract-geometry",
        "parameters": {},
        "seed": DEFAULT_VISUALIZER_SEED,
        "defaultedParameters": [],
        "warnings": [],
    }


def test_space_journey_defaults_are_complete_and_deterministic() -> None:
    request = SpaceJourneyVisualizerConfigRequest(preset="space-journey")
    first = resolve_visualizer_config(request)
    second = resolve_visualizer_config(request)
    expected = SpaceJourneyParameters().model_dump(mode="json", by_alias=True)
    assert first == second
    assert first.parameters.model_dump(mode="json", by_alias=True) == expected
    assert first.defaulted_parameters == sorted(expected)
    assert first.seed == DEFAULT_VISUALIZER_SEED


def test_space_journey_partial_parameters_report_only_applied_defaults() -> None:
    resolved = resolve_visualizer_config(
        SpaceJourneyVisualizerConfigRequest.model_validate(
            {
                "preset": "space-journey",
                "parameters": {
                    "cameraDistance": 24,
                    "palette": "dark-amber",
                    "drumResponse": 1.4,
                },
                "seed": 1234,
            }
        )
    )
    assert resolved.parameters.camera_distance == 24.0
    assert resolved.parameters.palette.value == "dark-amber"
    assert resolved.parameters.drum_response == 1.4
    assert "cameraDistance" not in resolved.defaulted_parameters
    assert "palette" not in resolved.defaulted_parameters
    assert "drumResponse" not in resolved.defaulted_parameters
    assert "fogDepth" in resolved.defaulted_parameters
    assert resolved.seed == 1234


@pytest.mark.parametrize(
    "payload",
    [
        {"preset": "unsupported"},
        {"preset": "abstract-geometry", "parameters": {"glowStrength": 1.0}},
        {"preset": "space-journey", "parameters": {"unknownParameter": 1.0}},
        {"preset": "space-journey", "parameters": {"cameraDistance": 7.99}},
        {"preset": "space-journey", "parameters": {"cameraOrbitSpeed": 0.51}},
        {"preset": "space-journey", "parameters": {"ringThickness": -0.1}},
        {"preset": "space-journey", "parameters": {"palette": "rainbow"}},
        {"preset": "space-journey", "parameters": {"shardDensity": "dense"}},
        {"preset": "space-journey", "parameters": {"fogDepth": math.nan}},
        {"preset": "space-journey", "parameters": {"glowStrength": math.inf}},
        {"seed": -1},
        {"seed": 2_147_483_648},
        {"seed": "84291"},
        {"schemaVersion": "2.0.0"},
        {"extraTopLevel": True},
    ],
)
def test_invalid_visualizer_requests_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        validate_visualizer_config_request(payload)


def test_resolved_config_serialization_contains_no_private_render_inputs() -> None:
    serialized = json.dumps(
        resolve_visualizer_config(
            SpaceJourneyVisualizerConfigRequest(preset="space-journey")
        ).model_dump(mode="json", by_alias=True),
        allow_nan=False,
    )
    for forbidden in (
        "sourceAudioPath",
        "displayName",
        "lyrics",
        "transcript",
        "promptPackage",
    ):
        assert forbidden not in serialized

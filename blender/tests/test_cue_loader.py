from __future__ import annotations

import json
from pathlib import Path

import pytest

from trackprompt_visualizer.cue_loader import load_cue_sheet
from trackprompt_visualizer.curve_importer import resolve_curve_sources
from trackprompt_visualizer.validation import (
    VisualizerValidationError,
    validate_output_file,
)


def cue_fixture() -> dict[str, object]:
    curve = {
        "pointFormat": ["frame", "value"],
        "points": [[1, 0.2], [151, 0.8], [300, 0.3]],
        "interpolation": "linear",
        "sourceSampleRateHz": 20,
        "originalPointCount": 201,
        "exportedPointCount": 3,
        "simplification": {"method": "rdp", "tolerance": 0.008, "maximumError": 0.004},
        "normalization": {"method": "robust-percentile", "normalizationGroup": "master"},
    }
    return {
        "schemaVersion": "1.1.0",
        "source": {
            "analysisSchemaVersion": "1.4.0",
            "analysisVersion": "0.5.0",
            "jobId": "11111111-1111-4111-8111-111111111111",
            "requestedMode": "fast",
            "effectiveMode": "fast",
        },
        "timeline": {
            "durationSeconds": 10.0,
            "fps": 30,
            "frameStart": 1,
            "frameEnd": 300,
            "framePolicy": "nearest-half-up-clamped",
        },
        "musicalGrid": {"bpm": {"value": 120, "confidence": "medium"}},
        "beats": [{"index": 0, "timeSeconds": 0.0, "frame": 1, "confidence": "medium", "strength": None, "sourcePath": "rhythm.beatTimestamps"}],
        "onsets": [],
        "sections": [{"id": "a", "startSeconds": 0.0, "endSeconds": 10.0, "startFrame": 1, "endFrame": 300, "energy": 0.5}],
        "transitions": [],
        "curves": {
            "masterEnergy": curve,
            "lowBandEnergy": curve,
            "midBandEnergy": curve,
            "highBandEnergy": curve,
            "brightness": curve,
            "transientActivity": curve,
        },
        "warnings": [],
    }


def test_loads_valid_minimized_cue_sheet(tmp_path: Path) -> None:
    path = tmp_path / "visual-cues.json"
    path.write_text(json.dumps(cue_fixture()), encoding="utf-8")
    loaded = load_cue_sheet(path.resolve())
    assert loaded["schemaVersion"] == "1.1.0"
    assert loaded["curves"]["masterEnergy"]["points"][0] == [1, 0.2]


def test_rejects_private_paths_nonfinite_and_missing_master(tmp_path: Path) -> None:
    private = cue_fixture()
    private["sourceAudioPath"] = "C:\\private\\track.wav"
    path = tmp_path / "private.json"
    path.write_text(json.dumps(private), encoding="utf-8")
    with pytest.raises(VisualizerValidationError):
        load_cue_sheet(path.resolve())

    missing = cue_fixture()
    missing["curves"] = {}
    path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(VisualizerValidationError, match="masterEnergy"):
        load_cue_sheet(path.resolve())

    nonfinite = json.dumps(cue_fixture()).replace("0.2", "NaN", 1)
    path.write_text(nonfinite, encoding="utf-8")
    with pytest.raises(VisualizerValidationError, match="Non-finite"):
        load_cue_sheet(path.resolve())


def test_fallbacks_are_declared_without_claiming_stems() -> None:
    resolved, fallbacks = resolve_curve_sources(cue_fixture())
    assert resolved["drum_energy"] == "transientActivity"
    assert resolved["bass_energy"] == "lowBandEnergy"
    assert resolved["vocal_energy"] is None
    assert {item["used"] for item in fallbacks} >= {"transientActivity", "lowBandEnergy", "constantZero"}


def test_output_path_refuses_implicit_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "scene.blend"
    assert validate_output_file(output.resolve(), suffix=".blend") == output.resolve()
    output.write_bytes(b"existing")
    with pytest.raises(VisualizerValidationError, match="already exists"):
        validate_output_file(output.resolve(), suffix=".blend")

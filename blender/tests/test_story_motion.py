from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from trackprompt_visualizer.motion import MOTION_PROFILES, smooth_control
from trackprompt_visualizer.shot_plan import active_shot, load_shot_plan, validate_shot_plan
from trackprompt_visualizer.validation import VisualizerValidationError


def _shot(identifier: str, act_id: str, start: int, end: int, *, cut: bool = False) -> dict[str, object]:
    return {
        "id": identifier,
        "name": identifier,
        "actId": act_id,
        "frameStart": start,
        "frameEnd": end,
        "durationFrames": end - start + 1,
        "storyPurpose": "safe local story purpose",
        "protagonistState": "travelling",
        "environment": {"environment": "gate_corridor", "secondaryAction": "bounded"},
        "camera": {"rig": "gate_approach", "lensMm": 35, "framing": "wide", "movementProfile": "controlled_chase"},
        "composition": {
            "dominantShape": "orb", "foreground": "arc", "midgroundSubject": "orb",
            "backgroundLandmark": "gate", "atmosphere": "nebula", "focalHierarchy": ["orb", "gate"],
        },
        "lighting": {"palette": "andromeda", "keyDirection": "left", "intensity": 0.5},
        "motion": {
            "profile": "controlled_chase", "interpolation": "BEZIER", "easeInFrames": 12,
            "easeOutFrames": 12, "maximumVelocity": 8, "maximumAcceleration": 2,
            "maximumAngularVelocity": 0.5,
        },
        "reactiveLayers": [
            {"signal": "master_energy_smoothed", "target": "emission", "strength": 0.15, "continuous": True}
        ],
        "transition": "cut" if cut else "continuous",
        "intentionalDiscontinuity": cut,
        "reviewFrames": [start, start + (end - start) // 2, end],
    }


def _plan() -> dict[str, object]:
    shots = []
    start = 1
    for index, act in enumerate(("signal", "awakening", "departure", "gates", "rupture", "transformation", "arrival"), start=1):
        end = start + 9
        shots.append(_shot(f"shot-{index:02d}-{act}", act, start, end, cut=act == "rupture"))
        start = end + 1
    return {
        "schemaVersion": "1.0.0",
        "storyPlanSchemaVersion": "1.0.0",
        "preset": "space-journey-story",
        "seed": 84291,
        "frameStart": 1,
        "frameEnd": 70,
        "fps": 30,
        "inputDigest": "a" * 64,
        "shots": shots,
    }


def test_shot_plan_validation_mapping_and_privacy(tmp_path: Path) -> None:
    payload = _plan()
    validate_shot_plan(payload)
    assert active_shot(payload, 35)["actId"] == "gates"
    path = tmp_path / "shot-plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_shot_plan(path.resolve()) == payload
    payload["sourcePath"] = r"C:\\private\\track.wav"
    with pytest.raises(VisualizerValidationError):
        validate_shot_plan(payload)


def test_shot_plan_rejects_overlap_unbounded_reactivity_and_undeclared_cut() -> None:
    payload = _plan()
    payload["shots"][1]["frameStart"] = 9
    with pytest.raises(VisualizerValidationError, match="contiguous"):
        validate_shot_plan(payload)
    payload = _plan()
    payload["shots"][0]["reactiveLayers"][0]["strength"] = 0.251
    with pytest.raises(VisualizerValidationError, match="bounded"):
        validate_shot_plan(payload)
    payload = _plan()
    payload["shots"][0]["intentionalDiscontinuity"] = True
    with pytest.raises(VisualizerValidationError, match="declared cut"):
        validate_shot_plan(payload)


def test_smoothing_pipeline_is_finite_bounded_and_reduces_sharpness() -> None:
    raw = [0.0] * 20 + [1.0, 0.0] * 10 + [0.8] * 20
    result = smooth_control(raw, sample_rate_hz=20.0)
    assert len(result) == len(raw)
    assert all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in result)
    raw_jump = max(abs(right - left) for left, right in zip(raw, raw[1:], strict=False))
    smooth_jump = max(abs(right - left) for left, right in zip(result, result[1:], strict=False))
    assert smooth_jump < raw_jump
    assert set(MOTION_PROFILES) == {
        "cinematic_drift", "slow_acceleration", "controlled_chase", "weightless_float",
        "impact_recoil", "transformation_orbit", "micro_audio_response",
    }

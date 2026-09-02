from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from trackprompt_visualizer.camera_rigs import planned_camera_keyframes
from trackprompt_visualizer.art_direction import micro_camera_reaction_is_bounded
from trackprompt_visualizer.motion import MOTION_PROFILES, smooth_control
from trackprompt_visualizer.narrative_environments import reviewed_stage_contract, story_action_frames
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


def test_reviewed_stage_art_contract_is_unique_and_depth_complete() -> None:
    stages = reviewed_stage_contract()
    assert [stage.environment for stage in stages] == [
        "dead_moon", "signal_ruins", "launch_structure", "gate_corridor"
    ]
    assert len({stage.landmark for stage in stages}) == 4
    assert len({stage.dominant_shape for stage in stages}) == 4
    assert len({stage.lighting_identity for stage in stages}) == 4
    assert len({stage.secondary_action for stage in stages}) == 4
    assert all(stage.layers == ("foreground", "midground", "background") for stage in stages)
    assert "beacon" in stages[0].dominant_shape
    assert "opening" in stages[1].dominant_shape
    assert "converging" in stages[2].dominant_shape
    assert "threshold" in stages[3].dominant_shape


def test_story_action_frames_support_one_frame_and_sparse_review_contracts() -> None:
    assert story_action_frames({"frameStart": 7, "frameEnd": 7, "reviewFrames": [7]}) == (7, 7, 7)
    assert story_action_frames({"frameStart": 10, "frameEnd": 20, "reviewFrames": [10, 20]}) == (10, 20, 20)


def test_micro_camera_reaction_requires_the_exact_bounded_audio_contract() -> None:
    bus = object()
    target = SimpleNamespace(id=bus, data_path='["master_energy"]')
    variable = SimpleNamespace(name="v", type="SINGLE_PROP", targets=[target])
    driver = SimpleNamespace(type="SCRIPTED", expression="(v - 0.5) * 0.04", variables=[variable])
    fcurve = SimpleNamespace(data_path="location", array_index=2, driver=driver)
    micro = SimpleNamespace(animation_data=SimpleNamespace(drivers=[fcurve]))
    assert micro_camera_reaction_is_bounded(micro, bus) is True
    driver.expression = "v * 100"
    assert micro_camera_reaction_is_bounded(micro, bus) is False


def test_story_camera_plan_authors_smooth_progression_and_preserves_declared_cut() -> None:
    payload = _plan()
    rigs = (
        ("establishing_reveal", "cinematic_drift", 32),
        ("slow_orbit", "weightless_float", 50),
        ("subject_follow", "slow_acceleration", 45),
        ("gate_approach", "controlled_chase", 28),
        ("rupture_fall", "impact_recoil", 24),
        ("transformation_closeup", "transformation_orbit", 72),
        ("arrival_reveal", "cinematic_drift", 30),
    )
    for shot, (rig, profile, lens) in zip(payload["shots"], rigs, strict=True):
        shot["camera"]["rig"] = rig
        shot["camera"]["lensMm"] = lens
        shot["motion"]["profile"] = profile
    keys = planned_camera_keyframes(payload)
    assert len(keys) == 7
    assert all(len(state["frames"]) == 3 for state in keys)
    assert all(state["frames"] == tuple(sorted(state["frames"])) for state in keys)
    for current, following in zip(keys[:3], keys[1:4], strict=True):
        assert current["locations"][-1] == following["locations"][0]
        assert current["targetLocations"][-1] == following["targetLocations"][0]
    departure_distance = math.sqrt(sum(value * value for value in keys[2]["locations"][0]))
    gate_distance = math.sqrt(sum(value * value for value in keys[3]["locations"][0]))
    assert gate_distance < departure_distance - 3.0
    assert keys[3]["crossesDeclaredCut"] is True
    assert keys[3]["locations"][-1] == keys[3]["locations"][0]
    assert keys[3]["locations"][-1] != keys[4]["locations"][0]
    assert keys[3]["targetLocations"][-1] != keys[4]["targetLocations"][0]
    for current, following in zip(keys[4:-1], keys[5:], strict=True):
        assert current["targetLocations"][-1] == following["targetLocations"][0]

from __future__ import annotations

import json

import pytest

from trackprompt_visualizer.story_revision_r12 import (
    R12_PROOF_FRAME_END,
    R12_PROOF_FRAME_START,
    R12_REQUIRED_SHOTS,
    build_r12_schedule,
    is_r12_shot_plan,
    planned_r12_camera_keyframes,
    planned_r12_protagonist_keyframes,
    r12_art_contract,
    r12_layout_state,
)


def _shot_plan() -> dict[str, object]:
    shots: list[dict[str, object]] = [
        {
            "id": "r12-shot-01-signal",
            "actId": "signal",
            "frameStart": 1,
            "frameEnd": 126,
        }
    ]
    shots.extend(
        {
            "id": identifier,
            "actId": act_id,
            "frameStart": start,
            "frameEnd": end,
        }
        for identifier, act_id, start, end in R12_REQUIRED_SHOTS
    )
    shots.extend(
        (
            {
                "id": "r12-shot-10-rupture-contract",
                "actId": "rupture",
                "frameStart": 656,
                "frameEnd": 656,
            },
            {
                "id": "r12-shot-11-transformation-contract",
                "actId": "transformation",
                "frameStart": 657,
                "frameEnd": 657,
            },
            {
                "id": "r12-shot-12-arrival-contract",
                "actId": "arrival",
                "frameStart": 658,
                "frameEnd": 658,
            },
        )
    )
    return {
        "schemaVersion": "1.0.0",
        "storyPlanSchemaVersion": "1.0.0",
        "inputDigest": "a" * 64,
        "seed": 84291,
        "fps": 30.0,
        "shots": shots,
    }


def test_r12_detection_and_schedule_are_exact_deterministic_and_continuous() -> None:
    plan = _shot_plan()
    assert is_r12_shot_plan(plan) is True
    assert is_r12_shot_plan({"shots": [{"id": "shot-02-awakening"}]}) is False

    first = build_r12_schedule(plan)
    second = build_r12_schedule(plan)
    assert first == second
    assert first["continuousRange"] == {
        "frameStart": 127,
        "frameEnd": 655,
        "durationFrames": 529,
        "durationSeconds": 529 / 30.0,
        "fps": 30.0,
    }
    phases = first["phases"]
    assert [phase["shotId"] for phase in phases] == [item[0] for item in R12_REQUIRED_SHOTS]
    assert phases[0]["frameStart"] == R12_PROOF_FRAME_START
    assert phases[-1]["frameEnd"] == R12_PROOF_FRAME_END
    assert all(
        right["frameStart"] == left["frameEnd"] + 1
        for left, right in zip(phases, phases[1:], strict=False)
    )
    assert [phase["environment"] for phase in phases] == [
        "signal_ruins",
        "signal_ruins",
        "launch_structure",
        "launch_structure",
        "launch_structure",
        "gate_corridor",
        "gate_corridor",
        "gate_corridor",
    ]
    assert len(first["canonicalSha256"]) == 64
    assert "C:\\" not in json.dumps(first)


@pytest.mark.parametrize("failure", ["missing", "range", "act"])
def test_r12_schedule_rejects_identity_or_range_drift(failure: str) -> None:
    plan = _shot_plan()
    shots = plan["shots"]
    assert isinstance(shots, list)
    target = next(shot for shot in shots if shot["id"] == "r12-shot-08-gate-crossing")
    if failure == "missing":
        shots.remove(target)
    elif failure == "range":
        target["frameEnd"] = 587
    else:
        target["actId"] = "departure"
    with pytest.raises(ValueError, match="R12 shot"):
        build_r12_schedule(plan)


def test_r12_layouts_are_embedded_responsive_contracts_not_crops() -> None:
    landscape = r12_layout_state("landscape")
    vertical = r12_layout_state("vertical")
    assert (landscape["width"], landscape["height"]) == (1920, 1080)
    assert (vertical["width"], vertical["height"]) == (1080, 1920)
    assert (landscape["phoneWidth"], landscape["phoneHeight"]) == (320, 180)
    assert (vertical["phoneWidth"], vertical["phoneHeight"]) == (180, 320)
    assert landscape["sensorFit"] == "HORIZONTAL"
    assert vertical["sensorFit"] == "VERTICAL"
    assert landscape["camera"] != vertical["camera"]
    assert landscape["cameraRoot"] != vertical["cameraRoot"]
    assert landscape["responsiveAnchor"] != vertical["responsiveAnchor"]
    assert landscape["stageTransforms"] != vertical["stageTransforms"]
    assert landscape["canonicalSha256"] != vertical["canonicalSha256"]
    assert vertical == r12_layout_state("vertical")
    with pytest.raises(ValueError, match="landscape or vertical"):
        r12_layout_state("square")


def test_r12_protagonist_plan_has_agency_wake_compression_and_reaction() -> None:
    landscape = planned_r12_protagonist_keyframes("landscape")
    vertical = planned_r12_protagonist_keyframes("vertical")
    assert [state["frame"] for state in landscape] == sorted(
        state["frame"] for state in landscape
    )
    assert landscape[0]["frame"] == R12_PROOF_FRAME_START
    assert landscape[-1]["frame"] == R12_PROOF_FRAME_END
    assert landscape != vertical
    assert [state["location"][1] for state in landscape] == sorted(
        state["location"][1] for state in landscape
    )
    assert len({state["rotation"] for state in landscape}) == len(landscape)
    assert max(state["wakeScale"][1] for state in landscape) >= 2.0

    by_role = {state["role"]: state for state in landscape}
    anticipation = by_role["threshold-anticipation"]
    compression = by_role["threshold-compression"]
    reaction = by_role["crossing-reaction"]
    assert anticipation["wakeScale"][1] < by_role["side-track"]["wakeScale"][1]
    assert compression["scale"][1] / compression["scale"][0] <= 0.65
    assert compression["deformation"] < 0.0
    assert reaction["scale"][1] / reaction["scale"][0] >= 1.35
    assert reaction["deformation"] > 0.10
    assert reaction["rotation"] != compression["rotation"]
    assert abs(vertical[6]["location"][0]) < abs(landscape[6]["location"][0])
    assert vertical[6]["location"][2] > landscape[6]["location"][2]


def test_r12_camera_plans_have_varied_authored_grammar_for_both_layouts() -> None:
    landscape = planned_r12_camera_keyframes("landscape")
    vertical = planned_r12_camera_keyframes("vertical")
    expected_roles = [
        "extreme-close-up",
        "close-orientation",
        "wide-chamber-reveal",
        "rear-follow-entry",
        "rear-follow",
        "side-track",
        "foreground-obstructed",
        "low-gate-approach",
        "threshold-pov",
        "crossing-reaction",
        "post-crossing-reaction",
        "scale-pullback",
    ]
    assert [state["role"] for state in landscape] == expected_roles
    assert [state["role"] for state in vertical] == expected_roles
    assert landscape[0]["frame"] == R12_PROOF_FRAME_START
    assert landscape[-1]["frame"] == R12_PROOF_FRAME_END
    assert [state["frame"] for state in landscape] == sorted(
        state["frame"] for state in landscape
    )
    assert min(state["lensMm"] for state in landscape) <= 22.0
    assert max(state["lensMm"] for state in landscape) >= 70.0
    landscape_by_role = {state["role"]: state for state in landscape}
    protagonist_by_role = {
        state["role"]: state for state in planned_r12_protagonist_keyframes("landscape")
    }
    side_track = landscape_by_role["side-track"]
    foreground_obstructed = landscape_by_role["foreground-obstructed"]
    crossing_reaction = landscape_by_role["crossing-reaction"]
    side_track_hero_offset = (
        side_track["location"][0] - protagonist_by_role["side-track"]["location"][0]
    )
    foreground_hero_offset = (
        foreground_obstructed["location"][0]
        - protagonist_by_role["occluded-track"]["location"][0]
    )
    crossing_hero_offset = (
        crossing_reaction["location"][0]
        - protagonist_by_role["crossing-reaction"]["location"][0]
    )
    side_track_target_offset = side_track["location"][0] - side_track["target"][0]
    foreground_target_offset = (
        foreground_obstructed["location"][0]
        - foreground_obstructed["target"][0]
    )
    crossing_target_offset = (
        crossing_reaction["location"][0] - crossing_reaction["target"][0]
    )
    assert side_track_hero_offset >= 10.0
    assert side_track_target_offset >= 10.0
    assert foreground_hero_offset >= 8.0
    assert foreground_target_offset >= 8.0
    assert side_track_hero_offset * crossing_hero_offset < 0.0
    assert side_track_target_offset * crossing_target_offset < 0.0
    assert landscape != vertical
    assert all(state["location"] != portrait["location"] for state, portrait in zip(landscape, vertical, strict=True))
    assert all(state["target"] != portrait["target"] for state, portrait in zip(landscape, vertical, strict=True))


def test_r12_art_contract_is_bounded_and_does_not_build_future_acts() -> None:
    contract = r12_art_contract()
    assert contract["environments"] == [
        "signal_ruins",
        "launch_structure",
        "gate_corridor",
    ]
    assert not {
        "broken_void",
        "transformation_megastructure",
        "andromeda_arrival",
    }.intersection(contract["environments"])
    assert contract["worldSpaceTravelAxis"] == "+Y"
    assert contract["maximumEmissiveStrength"] <= 2.0
    assert contract["maximumLuminousRenderableFraction"] <= 0.25
    assert contract["maximumVolumeDensity"] <= 0.025
    assert contract["maximumVolumeCountPerStage"] == 1
    assert contract["renderSamples"] <= 32
    assert contract["humanApprovalRequired"] is True
    assert len(contract["canonicalSha256"]) == 64

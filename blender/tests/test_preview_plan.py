from __future__ import annotations

import json
from pathlib import Path

from render_preview import _ffprobe_path, _parse_frame_rate, _resolve_executable, _stream_facts
from trackprompt_visualizer.preset_abstract_geometry import deterministic_seed_plan
from trackprompt_visualizer.preset_registry import SPACE_JOURNEY_DEFAULTS
from trackprompt_visualizer.preset_space_journey import (
    build_space_journey_direction_plan,
    deterministic_space_seed_plan,
)
from trackprompt_visualizer.preview import build_preview_plan, build_review_edit_spec


def test_preview_plan_uses_actual_sections_transitions_and_vocals() -> None:
    cues = {
        "timeline": {"frameStart": 1, "frameEnd": 13029, "fps": 30},
        "sections": [
            {"id": "intro", "startFrame": 1, "endFrame": 2000, "energy": 0.2},
            {"id": "high", "startFrame": 2001, "endFrame": 6864, "energy": 0.95},
            {"id": "vocal", "startFrame": 6865, "endFrame": 9500, "energy": 0.6, "vocalActivity": "prominent"},
            {"id": "outro", "startFrame": 11000, "endFrame": 13029, "energy": 0.1},
        ],
        "transitions": [
            {"frame": 2001, "energyDelta": 0.75},
            {"frame": 6865, "energyDelta": -0.35},
        ],
    }
    first = build_preview_plan(cues)
    second = build_preview_plan(cues)
    assert first == second
    assert {1, 2001, 13029}.issubset(first["stillFrames"])
    assert any(6865 <= frame <= 9500 for frame in first["stillFrames"])
    assert first["clip"]["endFrame"] - first["clip"]["startFrame"] + 1 == 300
    assert first["clip"]["startFrame"] <= 2001 <= first["clip"]["endFrame"]


def test_seed_plan_is_repeatable_and_changes_with_seed() -> None:
    assert deterministic_seed_plan(84291) == deterministic_seed_plan(84291)
    assert deterministic_seed_plan(84291) != deterministic_seed_plan(84292)


def test_space_preview_plan_has_six_distributed_roles_and_an_interior_clip() -> None:
    cues = {
        "timeline": {"frameStart": 1, "frameEnd": 13029, "fps": 30},
        "sections": [
            {"id": "intro", "startFrame": 1, "endFrame": 2000, "energy": 0.2},
            {"id": "groove", "startFrame": 2001, "endFrame": 6864, "energy": 0.95},
            {"id": "break", "startFrame": 6865, "endFrame": 9500, "energy": 0.6},
            {"id": "outro", "startFrame": 11000, "endFrame": 13029, "energy": 0.1},
        ],
        "transitions": [
            {"frame": 2001, "energyDelta": 0.75},
            {"frame": 6865, "energyDelta": -0.35},
        ],
    }
    plan = build_preview_plan(cues, "space-journey")
    assert len(plan["stillFrames"]) == 6
    assert plan["stillFrames"] == sorted(set(plan["stillFrames"]))
    assert [item["role"] for item in plan["stillRoles"]] == [
        "opening",
        "early-development",
        "main-groove",
        "breakdown",
        "peak",
        "outro",
    ]
    assert plan["stillFrames"][0] == 1
    assert plan["stillFrames"][-1] == 13029
    assert plan["clip"]["endFrame"] - plan["clip"]["startFrame"] + 1 == 300
    assert plan["clip"]["role"] == "representative-rising-transition"
    assert plan["clip"]["endFrame"] < 13029


def test_story_preview_is_bounded_to_signal_through_first_gate() -> None:
    cues = {"timeline": {"frameStart": 1, "frameEnd": 300, "fps": 30}}
    shots = {
        "shots": [
            {"id": "shot-signal", "actId": "signal", "frameStart": 1, "frameEnd": 30, "reviewFrames": [1, 15, 30]},
            {"id": "shot-awakening", "actId": "awakening", "frameStart": 31, "frameEnd": 66, "reviewFrames": [31, 48, 66]},
            {"id": "shot-departure", "actId": "departure", "frameStart": 67, "frameEnd": 108, "reviewFrames": [67, 87, 108]},
            {"id": "shot-gates", "actId": "gates", "frameStart": 109, "frameEnd": 156, "reviewFrames": [109, 132, 156]},
        ]
    }
    plan = build_preview_plan(cues, "space-journey-story", shots)
    assert plan["stillFrames"] == [15, 48, 67, 108, 132, 156]
    assert [item["role"] for item in plan["stillRoles"]] == [
        "signal", "awakening", "departure-commit", "departure-passage", "first-gate-approach", "first-gate"
    ]
    assert plan["clip"] == {
        "startFrame": 1,
        "endFrame": 156,
        "role": "signal-through-first-gate",
        "centerFrame": 78,
        "reviewEditStrategy": "six-authored-motion-excerpts",
        "sourceEndFrame": 156,
        "maximumOutputFrames": 300,
    }
    assert [segment["role"] for segment in plan["reviewSegments"]] == [
        "signal", "awakening", "departure-commit", "departure-passage",
        "first-gate-approach", "first-gate",
    ]
    assert all(segment["durationFrames"] == 30 for segment in plan["reviewSegments"])
    assert all(
        next(
            shot for shot in shots["shots"] if shot["id"] == segment["shotId"]
        )["frameStart"]
        <= segment["startFrame"]
        <= segment["endFrame"]
        <= next(
            shot for shot in shots["shots"] if shot["id"] == segment["shotId"]
        )["frameEnd"]
        for segment in plan["reviewSegments"]
    )
    edit = build_review_edit_spec(
        plan,
        timeline_frame_start=1,
        timeline_frame_end=300,
        fps=30,
    )
    assert edit["strategy"] == "six-authored-motion-excerpts"
    assert edit["outputFrameCount"] == 180
    assert edit["durationSeconds"] == 6.0
    assert edit["sourceFrames"] == [
        frame
        for segment in plan["reviewSegments"]
        for frame in range(segment["startFrame"], segment["endFrame"] + 1)
    ]
    assert str(edit["audioFilter"]).count("atrim=") == 6
    assert "concat=n=6:v=0:a=1[review_audio]" in str(edit["audioFilter"])


def test_space_direction_and_seed_plans_are_deterministic_and_bounded() -> None:
    cues = {
        "timeline": {"frameStart": 1, "frameEnd": 13029, "fps": 30},
        "sections": [
            {"id": "a", "startFrame": 1, "endFrame": 6864, "energy": 0.85},
            {"id": "b", "startFrame": 6865, "endFrame": 13029, "energy": 0.5},
        ],
    }
    first = build_space_journey_direction_plan(cues, SPACE_JOURNEY_DEFAULTS)
    second = build_space_journey_direction_plan(cues, SPACE_JOURNEY_DEFAULTS)
    assert first == second
    assert first[0]["frame"] == 1
    assert first[-1]["frame"] == 13029
    assert [int(state["frame"]) for state in first] == sorted(
        {int(state["frame"]) for state in first}
    )
    assert first[-1]["cameraOrbitRadians"] <= 0.15 * 2 * 3.141593
    distances = [float(state["cameraDistance"]) for state in first]
    assert all(24.0 < distance < 33.0 for distance in distances)
    assert min(distances) < distances[0] - 2.0
    assert distances[-1] > min(distances)
    roles = {str(state["narrativeRole"]): state for state in first}
    assert {
        "opening",
        "early-development",
        "main-groove",
        "breakdown",
        "peak",
        "outro",
    }.issubset(roles)
    assert float(roles["opening"]["destinationReveal"]) < float(
        roles["main-groove"]["destinationReveal"]
    )
    assert float(roles["breakdown"]["cameraDistance"]) > float(
        roles["main-groove"]["cameraDistance"]
    )
    assert float(roles["peak"]["heroAwakening"]) == 1.0
    assert float(roles["outro"]["destinationReveal"]) < float(
        roles["peak"]["destinationReveal"]
    )
    assert all(abs(float(state["targetOffsetX"])) <= 1.20 for state in first)
    assert all(abs(float(state["targetOffsetZ"])) <= 0.28 for state in first)
    assert all(abs(float(state["cameraShiftX"])) <= 0.030 for state in first)
    assert all(abs(float(state["cameraShiftY"])) <= 0.012 for state in first)
    assert all(0.55 <= float(state["orbitReveal"]) <= 1.04 for state in first)
    assert float(roles["opening"]["orbitReveal"]) < float(
        roles["main-groove"]["orbitReveal"]
    )
    assert float(roles["breakdown"]["orbitReveal"]) < float(
        roles["peak"]["orbitReveal"]
    )
    assert all(0.12 <= float(state["heroAwakening"]) <= 1.0 for state in first)
    assert all(abs(float(state["orbitTiltX"])) <= 0.62 for state in first)
    assert all(abs(float(state["orbitTiltY"])) <= 0.30 for state in first)
    assert all(abs(float(state["orbitOffsetX"])) <= 2.60 for state in first)
    assert all(abs(float(state["orbitOffsetZ"])) <= 1.00 for state in first)
    assert all(abs(float(state["travelOffsetY"])) <= 3.40 for state in first)
    assert all(abs(float(state["foregroundOffsetX"])) <= 0.62 for state in first)
    assert all(abs(float(state["foregroundOffsetY"])) <= 0.82 for state in first)
    assert all(abs(float(state["foregroundOffsetZ"])) <= 0.12 for state in first)
    assert all(0.82 <= float(state["lightingScale"]) <= 1.28 for state in first)
    seed_plan = deterministic_space_seed_plan(84291, SPACE_JOURNEY_DEFAULTS)
    assert seed_plan == deterministic_space_seed_plan(84291, SPACE_JOURNEY_DEFAULTS)
    assert seed_plan != deterministic_space_seed_plan(84292, SPACE_JOURNEY_DEFAULTS)
    assert seed_plan["shardCount"] <= 72
    assert sum(seed_plan["starLayerCounts"]) <= 330
    assert len(seed_plan["starLayerCounts"]) == 4
    assert seed_plan["companionRingCount"] == 9
    assert 48 <= seed_plan["heroSurfaceDetailCount"] <= 100
    assert 88 <= seed_plan["orbitalDustCount"] <= 192


def test_space_direction_rising_transition_builds_depth_and_destination_consequence() -> None:
    cues = {
        "timeline": {"frameStart": 1, "frameEnd": 13029, "fps": 30},
        "sections": [
            {"id": "intro", "startFrame": 1, "endFrame": 2000, "energy": 0.2},
            {"id": "groove", "startFrame": 2001, "endFrame": 5000, "energy": 0.8},
            {"id": "breakdown", "startFrame": 5001, "endFrame": 7000, "energy": 0.3},
            {"id": "rebuild", "startFrame": 7001, "endFrame": 8500, "energy": 0.7},
            {"id": "peak", "startFrame": 8501, "endFrame": 10500, "energy": 0.95},
            {"id": "outro", "startFrame": 10501, "endFrame": 13029, "energy": 0.2},
        ],
        "transitions": [{"frame": 7080, "energyDelta": 0.4}],
    }
    plan = build_space_journey_direction_plan(cues, SPACE_JOURNEY_DEFAULTS)
    roles = {str(state["narrativeRole"]): state for state in plan}
    rebuild = roles["rebuild"]
    threshold = roles["threshold-hold"]
    release = roles["rebuild-release"]

    assert int(rebuild["frame"]) == 7080
    assert int(threshold["frame"]) == 7155
    assert int(release["frame"]) == 7230
    assert abs(float(threshold["cameraDistance"]) - float(rebuild["cameraDistance"])) < 0.25
    assert float(threshold["destinationReveal"]) - float(rebuild["destinationReveal"]) <= 0.02
    assert float(release["cameraDistance"]) < float(threshold["cameraDistance"]) - 2.0
    assert float(release["destinationReveal"]) > float(rebuild["destinationReveal"])
    assert float(release["heroAwakening"]) > float(rebuild["heroAwakening"])
    assert float(release["orbitReveal"]) > float(rebuild["orbitReveal"])
    assert float(release["packetScale"]) > float(rebuild["packetScale"])
    assert float(release["travelOffsetY"]) < float(rebuild["travelOffsetY"])
    assert abs(float(release["nebulaOffsetZ"])) < abs(float(rebuild["nebulaOffsetZ"]))
    assert float(rebuild["foregroundOffsetX"]) > 0.0
    assert float(threshold["foregroundOffsetX"]) > 0.0
    assert float(release["foregroundOffsetX"]) < 0.0
    assert float(release["foregroundOffsetY"]) < float(threshold["foregroundOffsetY"])
    assert float(release["lightingScale"]) < float(threshold["lightingScale"])


def test_preview_clip_uses_complete_short_source() -> None:
    plan = build_preview_plan(
        {
            "timeline": {"frameStart": 1, "frameEnd": 90, "fps": 30},
            "sections": [{"id": "only", "startFrame": 1, "endFrame": 90, "energy": 0.0}],
            "transitions": [],
        }
    )
    assert plan["clip"] == {"startFrame": 1, "endFrame": 90}


def test_explicit_executable_paths_are_absolute_existing_files(tmp_path: Path) -> None:
    executable = tmp_path / "ffmpeg.exe"
    executable.write_bytes(b"fixture")
    assert _resolve_executable(str(executable.resolve()), "ffmpeg") == executable.resolve()
    assert _resolve_executable("relative-ffmpeg.exe", "ffmpeg") is None
    assert _resolve_executable(str(tmp_path / "missing.exe"), "ffmpeg") is None


def test_ffprobe_can_be_resolved_beside_explicit_ffmpeg(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"fixture")
    ffprobe.write_bytes(json.dumps({"fixture": True}).encode())
    assert _ffprobe_path(None, str(ffmpeg.resolve())) == ffprobe.resolve()


def test_ffprobe_stream_facts_use_measured_codec_dimensions_and_rate() -> None:
    facts = _stream_facts(
        [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "0/0",
                "r_frame_rate": "30000/1001",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ]
    )
    assert facts == {
        "videoPresent": True,
        "audioPresent": True,
        "hasVideo": True,
        "hasAudio": True,
        "videoCodec": "h264",
        "audioCodec": "aac",
        "width": 1280,
        "height": 720,
        "frameRate": 30000 / 1001,
        "fps": 30000 / 1001,
    }
    assert _parse_frame_rate("30/1") == 30.0
    assert _parse_frame_rate("0/0") is None
    assert _parse_frame_rate("not-a-rate") is None

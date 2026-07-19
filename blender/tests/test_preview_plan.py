from __future__ import annotations

import json
from pathlib import Path

from render_preview import _ffprobe_path, _resolve_executable
from trackprompt_visualizer.preset_abstract_geometry import deterministic_seed_plan
from trackprompt_visualizer.preview import build_preview_plan


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

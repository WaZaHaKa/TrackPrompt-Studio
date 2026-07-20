from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from timeline_health_scan import _compositor_health, _iter_animation_owners, build_sample_plan


def test_sample_plan_covers_periodic_boundaries_preview_and_peaks() -> None:
    cue_sheet = {
        "sections": [
            {"id": "section-1", "startFrame": 1, "endFrame": 60},
            {"id": "section-2", "startFrame": 61, "endFrame": 120},
        ],
        "transitions": [{"id": "transition-1", "frame": 61}],
        "curves": {
            "masterEnergy": {
                "points": [[1, 0.1], [45, 1.0], [90, 0.9], [120, 0.0]],
            }
        },
    }
    manifest = {
        "preview": {
            "stillFrames": [1, 75, 120],
            "clip": {"startFrame": 50, "centerFrame": 60, "endFrame": 70},
        }
    }
    plan = build_sample_plan(
        cue_sheet,
        manifest,
        frame_start=1,
        frame_end=120,
        fps=30,
        interval=30,
        high_energy_peak_count=2,
    )
    by_frame = {item["frame"]: set(item["reasons"]) for item in plan}
    assert {1, 31, 61, 91, 120}.issubset(by_frame)
    assert "section-end:section-1" in by_frame[60]
    assert "section-start:section-2" in by_frame[61]
    assert "transition:transition-1" in by_frame[60]
    assert "transition:transition-1" in by_frame[61]
    assert "transition:transition-1" in by_frame[62]
    assert "representative-still" in by_frame[75]
    assert "preview-start" in by_frame[49]
    assert "preview-end" in by_frame[71]
    assert "high-energy-peak" in by_frame[45]


def test_blender_52_compositor_node_group_is_supported_without_legacy_scene_node_tree() -> None:
    compositor = SimpleNamespace(nodes=[])
    scene = SimpleNamespace(
        name="Scene",
        world=None,
        compositing_node_group=compositor,
    )
    bpy = SimpleNamespace(data=SimpleNamespace(objects=[], materials=[]))
    assert _compositor_health(scene) == []
    assert ("compositor:Scene", compositor) in _iter_animation_owners(bpy, scene)


def test_timeline_scanner_has_no_render_or_save_operation() -> None:
    source = (Path(__file__).resolve().parents[1] / "timeline_health_scan.py").read_text(encoding="utf-8")
    assert "bpy.ops.render" not in source
    assert "bpy.ops.wm.save" not in source

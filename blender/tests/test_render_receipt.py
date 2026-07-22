from __future__ import annotations

import json
from pathlib import Path

from trackprompt_visualizer.mcp_entrypoints import _build_story_render_receipt
from trackprompt_visualizer.preview import build_preview_plan, build_review_edit_spec


def _shot(identifier: str, act_id: str, start: int) -> dict[str, object]:
    end = start + 9
    return {
        "id": identifier,
        "name": identifier,
        "actId": act_id,
        "frameStart": start,
        "frameEnd": end,
        "durationFrames": 10,
        "storyPurpose": "safe local story purpose",
        "protagonistState": "travelling",
        "environment": {"environment": "gate_corridor", "secondaryAction": "bounded"},
        "camera": {
            "rig": "gate_approach",
            "lensMm": 35,
            "framing": "wide",
            "movementProfile": "controlled_chase",
        },
        "composition": {
            "dominantShape": "orb",
            "foreground": "arc",
            "midgroundSubject": "orb",
            "backgroundLandmark": "gate",
            "atmosphere": "nebula",
            "focalHierarchy": ["orb", "gate"],
        },
        "lighting": {"palette": "andromeda", "keyDirection": "left", "intensity": 0.5},
        "motion": {
            "profile": "controlled_chase",
            "interpolation": "BEZIER",
            "easeInFrames": 2,
            "easeOutFrames": 2,
            "maximumVelocity": 8,
            "maximumAcceleration": 2,
            "maximumAngularVelocity": 0.5,
        },
        "reactiveLayers": [],
        "transition": "continuous",
        "intentionalDiscontinuity": False,
        "reviewFrames": [start, start + 4, end],
    }


def _plan() -> dict[str, object]:
    acts = ("signal", "awakening", "departure", "gates", "rupture", "transformation", "arrival")
    return {
        "schemaVersion": "1.0.0",
        "storyPlanSchemaVersion": "1.0.0",
        "preset": "space-journey-story",
        "seed": 84291,
        "frameStart": 1,
        "frameEnd": 70,
        "fps": 30,
        "inputDigest": "a" * 64,
        "shots": [
            _shot(f"shot-{index:02d}-{act}", act, index * 10 - 9)
            for index, act in enumerate(acts, start=1)
        ],
    }


def test_story_render_receipt_hash_binds_scene_plan_edit_clip_and_stills(tmp_path: Path) -> None:
    shot_plan = _plan()
    preview_plan = build_preview_plan(
        {"timeline": {"frameStart": 1, "frameEnd": 70, "fps": 30}},
        "space-journey-story",
        shot_plan,
    )
    review_edit = build_review_edit_spec(
        preview_plan,
        timeline_frame_start=1,
        timeline_frame_end=70,
        fps=30,
    )
    scene = tmp_path / "story.blend"
    clip = tmp_path / "preview.mp4"
    scene.write_bytes(b"scene")
    clip.write_bytes(b"clip")
    for role in preview_plan["stillRoles"]:
        (tmp_path / f"frame_{int(role['frame']):06d}.png").write_bytes(
            f"still-{role['frame']}".encode()
        )

    receipt = _build_story_render_receipt(
        scene_file=scene,
        shot_plan=shot_plan,
        preview_plan=preview_plan,
        review_edit=review_edit,
        rendered_frame_sequence={"count": review_edit["outputFrameCount"], "sha256": "b" * 64},
        clip=clip,
    )

    assert receipt["kind"] == "trackprompt-blender-mcp-preview-render-receipt"
    assert receipt["scene"]["file"] == "story.blend"
    assert receipt["shotPlan"]["inputDigest"] == "a" * 64
    assert receipt["reviewEdit"]["segments"] == review_edit["segments"]
    assert receipt["renderedFrames"]["count"] == review_edit["outputFrameCount"]
    assert len(receipt["renderedFrames"]["representativeFrames"]) == 6
    assert receipt["clip"]["file"] == "preview.mp4"
    assert str(tmp_path) not in json.dumps(receipt)

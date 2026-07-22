from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for source_root in (REPOSITORY_ROOT, REPOSITORY_ROOT / "backend", REPOSITORY_ROOT / "blender"):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from app.cinematic.schemas import ShotPlan  # noqa: E402
from tools.verify_cinematic_v2_proof import _validate_mcp_render_receipt  # noqa: E402
from trackprompt_visualizer.mcp_entrypoints import _build_story_render_receipt  # noqa: E402
from trackprompt_visualizer.preview import build_preview_plan, build_review_edit_spec  # noqa: E402


def _shot(identifier: str, act_id: str, start: int) -> dict[str, object]:
    end = start + 9
    return {
        "id": identifier,
        "name": identifier,
        "actId": act_id,
        "frameStart": start,
        "frameEnd": end,
        "durationFrames": 10,
        "storyPurpose": "A safe local narrative beat.",
        "protagonistState": "travelling",
        "environment": {"environment": "gate_corridor", "secondaryAction": "bounded action"},
        "camera": {
            "rig": "gate_approach",
            "lensMm": 35,
            "framing": "wide",
            "movementProfile": "controlled_chase",
        },
        "composition": {
            "dominantShape": "threshold",
            "foreground": "occluder",
            "midgroundSubject": "orb",
            "backgroundLandmark": "gate",
            "atmosphere": "haze",
            "focalHierarchy": ["protagonist", "gate"],
        },
        "lighting": {"palette": "green", "keyDirection": "behind", "intensity": 0.5},
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


def _shot_plan_payload() -> dict[str, object]:
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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> dict[str, object]:
    payload = _shot_plan_payload()
    shots = ShotPlan.model_validate(payload)
    preview_plan = build_preview_plan(
        {"timeline": {"frameStart": 1, "frameEnd": 70, "fps": 30}},
        "space-journey-story",
        payload,
    )
    review_edit = build_review_edit_spec(
        preview_plan,
        timeline_frame_start=1,
        timeline_frame_end=70,
        fps=30,
    )
    scene = tmp_path / "story.blend"
    clip = tmp_path / "signal-to-first-gate-preview.mp4"
    scene.write_bytes(b"scene")
    clip.write_bytes(b"clip")
    stills: list[dict[str, object]] = []
    for role in preview_plan["stillRoles"]:
        frame = int(role["frame"])
        still = tmp_path / f"frame_{frame:06d}.png"
        still.write_bytes(f"still-{frame}".encode())
        stills.append(
            {
                "frame": frame,
                "file": still.name,
                "sha256": _digest(still),
                "sizeBytes": still.stat().st_size,
                **{key: role[key] for key in ("role", "actId", "shotId")},
            }
        )
    receipt = _build_story_render_receipt(
        scene_file=scene,
        shot_plan=payload,
        preview_plan=preview_plan,
        review_edit=review_edit,
        rendered_frame_sequence={"count": review_edit["outputFrameCount"], "sha256": "b" * 64},
        clip=clip,
    )
    (tmp_path / "mcp-render-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return {
        "scene": scene,
        "clip": clip,
        "payload": payload,
        "shots": shots,
        "review_edit": review_edit,
        "stills": stills,
    }


def _verify(tmp_path: Path, fixture: dict[str, object]) -> None:
    _validate_mcp_render_receipt(
        preview=tmp_path,
        scene_path=fixture["scene"],
        shot_plan_payload=fixture["payload"],
        shots=fixture["shots"],
        review_edit=fixture["review_edit"],
        stills=fixture["stills"],
        clip=fixture["clip"],
    )


def test_mcp_render_receipt_verifies_all_bound_artifacts(tmp_path: Path) -> None:
    _verify(tmp_path, _fixture(tmp_path))


@pytest.mark.parametrize(
    ("tamper", "message"),
    (("scene", "scene"), ("plan", "shotPlan"), ("edit", "reviewEdit"), ("clip", "clip")),
)
def test_mcp_render_receipt_rejects_bound_artifact_tampering(
    tmp_path: Path,
    tamper: str,
    message: str,
) -> None:
    fixture = _fixture(tmp_path)
    if tamper == "scene":
        fixture["scene"].write_bytes(b"changed-scene")
    elif tamper == "plan":
        fixture["payload"]["seed"] = 1
    elif tamper == "edit":
        fixture["review_edit"]["segments"][0]["role"] = "changed"
    else:
        fixture["clip"].write_bytes(b"changed-clip")
    with pytest.raises(RuntimeError, match=message):
        _verify(tmp_path, fixture)


def test_mcp_render_receipt_rejects_representative_frame_tampering(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["stills"][0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="rendered-frame evidence"):
        _verify(tmp_path, fixture)

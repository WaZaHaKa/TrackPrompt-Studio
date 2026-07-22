from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .camera_rigs import build_story_camera_rig
from .narrative_environments import build_narrative_environments
from .preset_space_journey import build_space_journey
from .protagonist import animate_protagonist
from .shot_plan import validate_shot_plan

STORY_COLLECTIONS = (
    "TP_STORY",
    "TP_PROTAGONIST",
    "TP_NARRATIVE_ENVIRONMENTS",
    "TP_CAMERA_RIGS",
)


def _collection(name: str) -> Any:
    import bpy  # type: ignore[import-not-found]

    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def build_space_journey_story(
    cues: dict[str, Any],
    bus: Any,
    seed: int,
    parameters: Mapping[str, object] | None = None,
    shot_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    if shot_plan is None:
        raise ValueError("space-journey-story requires a validated shot plan")
    validate_shot_plan(shot_plan)
    baseline = build_space_journey(cues, bus, seed, parameters)
    collections = {name: _collection(name) for name in STORY_COLLECTIONS}
    hero = bpy.data.objects.get("TP_SPACE_CORE_SHELL")
    camera = bpy.data.objects.get("TP_CAMERA")
    target = bpy.data.objects.get("TP_CAMERA_TARGET")
    if hero is None or camera is None or target is None:
        raise RuntimeError("Space Journey V1 components required by V2 are unavailable.")
    protagonist = animate_protagonist(hero, shot_plan)
    environments = build_narrative_environments(
        shot_plan,
        collections["TP_NARRATIVE_ENVIRONMENTS"],
    )
    camera_rig = build_story_camera_rig(
        camera,
        target,
        bus,
        shot_plan,
        collections["TP_CAMERA_RIGS"],
    )
    scene = bpy.context.scene
    for marker in list(scene.timeline_markers):
        scene.timeline_markers.remove(marker)
    seen_acts: set[str] = set()
    for shot in shot_plan["shots"]:
        act_id = str(shot["actId"])
        if act_id not in seen_acts:
            scene.timeline_markers.new(
                f"ACT|{act_id}|{act_id.replace('-', ' ').title()}",
                frame=int(shot["frameStart"]),
            )
            seen_acts.add(act_id)
        scene.timeline_markers.new(
            f"SHOT|{shot['id']}|{shot['name']}",
            frame=int(shot["frameStart"]),
        )
    scene["trackprompt_shot_plan"] = json.dumps(
        shot_plan,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    scene["trackprompt_story_schema"] = shot_plan["storyPlanSchemaVersion"]
    scene["trackprompt_shot_schema"] = shot_plan["schemaVersion"]
    scene["trackprompt_preview_only"] = True
    scene["trackprompt_requires_v2_calibration"] = True
    return {
        **baseline,
        "storyVersion": "2.0.0-preview",
        "shotPlanSchemaVersion": shot_plan["schemaVersion"],
        "shotCount": len(shot_plan["shots"]),
        "actCount": len(seen_acts),
        "timelineMarkerCount": len(scene.timeline_markers),
        "protagonist": protagonist,
        "environments": environments,
        "storyCameraRig": camera_rig,
        "collections": sorted(set(baseline["collections"]) | set(STORY_COLLECTIONS)),
        "warnings": list(baseline.get("warnings", []))
        + ["preview_only_requires_v2_calibration_and_authorization"],
    }

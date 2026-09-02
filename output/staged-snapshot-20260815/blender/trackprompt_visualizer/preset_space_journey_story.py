from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .camera_rigs import build_story_camera_rig
from .curve_importer import iter_action_fcurves
from .narrative_environments import build_narrative_environments
from .preset_space_journey import build_space_journey
from .protagonist import animate_protagonist
from .shot_plan import validate_shot_plan
from .story_revision_r12 import build_r12_story_slice, is_r12_shot_plan

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


def _remove_stale_v2_orphans() -> None:
    """Keep repeated MCP builds deterministic without touching linked V1 objects."""

    import bpy  # type: ignore[import-not-found]

    prefixes = ("TP_ENV_", "TP_STORY_CAMERA_")
    baseline_rig_names = {
        "TP_SPACE_DEBRIS_RIG",
        "TP_SPACE_ORBITAL_DUST_RIG",
        "TP_SPACE_REVELATION_RIG",
        "TP_SPACE_TRAVEL_MACRO",
        "TP_SPACE_TRAVEL_RIG",
        "TP_SPACE_VOCAL_RIG",
    }
    for obj in list(bpy.data.objects):
        base_name = obj.name.rsplit(".", 1)[0]
        if len(obj.users_collection) == 0 and (
            obj.name.startswith(prefixes) or base_name in baseline_rig_names
        ):
            bpy.data.objects.remove(obj, do_unlink=True)


def _author_v2_baseline_visibility(shot_plan: dict[str, Any]) -> dict[str, Any]:
    """Restrain V1 travel ornament only inside the additive V2 scene."""

    import bpy  # type: ignore[import-not-found]

    categories = {
        "orbits": tuple(
            obj for obj in bpy.data.objects
            if obj.name.startswith("TP_SPACE_ORBIT_")
        ),
        "travel": tuple(
            obj for obj in bpy.data.objects
            if obj.name.startswith("TP_SPACE_TRAVEL")
        ),
        "debris": tuple(
            obj for obj in bpy.data.objects
            if obj.name.startswith("TP_SPACE_DEBRIS")
        ),
        "dust": tuple(
            obj for obj in bpy.data.objects
            if obj.name.startswith("TP_SPACE_ORBITAL_DUST")
        ),
        "wisps": tuple(
            obj for obj in bpy.data.objects
            if obj.name.startswith("TP_SPACE_VOCAL")
        ),
        "revelation": tuple(
            obj for obj in bpy.data.objects
            if obj.name.startswith("TP_SPACE_REVELATION")
        ),
    }
    for shot in shot_plan["shots"]:
        act_id = str(shot["actId"])
        frame = int(shot["frameStart"])
        reviewed = act_id in {"signal", "awakening", "departure", "gates"}
        visibility = {
            "orbits": not reviewed,
            "travel": act_id in {"departure", "gates"} or not reviewed,
            "debris": act_id in {"departure", "gates"} or not reviewed,
            "dust": act_id in {"departure", "gates"} or not reviewed,
            "wisps": not reviewed,
            "revelation": not reviewed,
        }
        for category, objects in categories.items():
            for obj in objects:
                obj.hide_viewport = not visibility[category]
                obj.hide_render = not visibility[category]
                obj.keyframe_insert("hide_viewport", frame=frame)
                obj.keyframe_insert("hide_render", frame=frame)
    for objects in categories.values():
        for obj in objects:
            animation = getattr(obj, "animation_data", None)
            action = getattr(animation, "action", None) if animation is not None else None
            if action is None:
                continue
            for fcurve in iter_action_fcurves(action):
                if fcurve.data_path in {"hide_viewport", "hide_render"}:
                    for point in fcurve.keyframe_points:
                        point.interpolation = "CONSTANT"
            obj["trackprompt_story_visibility_authored"] = True
    return {
        "policy": "v2-stage-landmarks-prioritized",
        "categoryObjectCounts": {
            name: len(objects) for name, objects in categories.items()
        },
    }


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
    _remove_stale_v2_orphans()
    baseline = build_space_journey(cues, bus, seed, parameters)
    collections = {name: _collection(name) for name in STORY_COLLECTIONS}
    hero = bpy.data.objects.get("TP_SPACE_CORE_SHELL")
    camera = bpy.data.objects.get("TP_CAMERA")
    target = bpy.data.objects.get("TP_CAMERA_TARGET")
    if hero is None or camera is None or target is None:
        raise RuntimeError("Space Journey V1 components required by V2 are unavailable.")
    r12 = is_r12_shot_plan(shot_plan)
    if r12:
        destination_macro = bpy.data.objects.get("TP_DESTINATION_MACRO")
        if destination_macro is None:
            raise RuntimeError("Space Journey V1 protagonist root required by R12 is unavailable.")
        revision = build_r12_story_slice(
            shot_plan,
            collections["TP_NARRATIVE_ENVIRONMENTS"],
            collections["TP_PROTAGONIST"],
            collections["TP_CAMERA_RIGS"],
            bus,
            camera,
            target,
            destination_macro,
            hero,
        )
        protagonist = revision["protagonist"]
        baseline_visibility = revision["baselineVisibility"]
        environments = revision["environments"]
        camera_rig = revision["storyCameraRig"]
    else:
        protagonist = animate_protagonist(hero, shot_plan)
        baseline_visibility = _author_v2_baseline_visibility(shot_plan)
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
        "storyVersion": "2.0.0-preview-r12" if r12 else "2.0.0-preview",
        "shotPlanSchemaVersion": shot_plan["schemaVersion"],
        "shotCount": len(shot_plan["shots"]),
        "actCount": len(seen_acts),
        "timelineMarkerCount": len(scene.timeline_markers),
        "protagonist": protagonist,
        "baselineVisibility": baseline_visibility,
        "environments": environments,
        "storyCameraRig": camera_rig,
        "collections": sorted(set(baseline["collections"]) | set(STORY_COLLECTIONS)),
        "warnings": list(baseline.get("warnings", []))
        + ["preview_only_requires_v2_calibration_and_authorization"]
        + (["r12_bounded_art_slice_human_approval_pending"] if r12 else []),
    }

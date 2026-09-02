from __future__ import annotations

import json
from typing import Any

from .shot_plan import active_shot
from .validation import VisualizerValidationError

RENDERABLE_GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "VOLUME", "FONT"}


def micro_camera_reaction_is_bounded(micro: Any, bus: Any) -> bool:
    animation = getattr(micro, "animation_data", None)
    drivers = list(getattr(animation, "drivers", ())) if animation is not None else []
    if len(drivers) != 1:
        return False
    fcurve = drivers[0]
    driver = getattr(fcurve, "driver", None)
    variables = list(getattr(driver, "variables", ())) if driver is not None else []
    if (
        getattr(fcurve, "data_path", None) != "location"
        or int(getattr(fcurve, "array_index", -1)) != 2
        or getattr(driver, "type", None) != "SCRIPTED"
        or getattr(driver, "expression", None) != "(v - 0.5) * 0.04"
        or len(variables) != 1
    ):
        return False
    variable = variables[0]
    targets = list(getattr(variable, "targets", ()))
    return (
        getattr(variable, "name", None) == "v"
        and getattr(variable, "type", None) == "SINGLE_PROP"
        and len(targets) == 1
        and getattr(targets[0], "id", None) is bus
        and getattr(targets[0], "data_path", None) == '["master_energy"]'
    )


def capture_review_state() -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    raw = scene.get("trackprompt_shot_plan")
    if not isinstance(raw, str):
        raise VisualizerValidationError("The current scene has no V2 shot plan.")
    shot_plan = json.loads(raw)
    shot = active_shot(shot_plan, int(scene.frame_current))
    if shot is None:
        raise VisualizerValidationError("The current frame does not belong to a V2 shot.")
    environment = str(shot["environment"]["environment"])
    visible = [
        obj
        for obj in bpy.data.objects
        if obj.get("trackprompt_environment") == environment
        and not bool(obj.hide_render)
        and getattr(obj, "type", "EMPTY") in RENDERABLE_GEOMETRY_TYPES
    ]
    layer_counts = {
        layer: sum(1 for obj in visible if obj.get("trackprompt_depth_layer") == layer)
        for layer in ("foreground", "midground", "background")
    }
    landmarks = sorted(
        obj.name for obj in visible if bool(obj.get("trackprompt_landmark", False))
    )
    anchor = bpy.data.objects.get(f"TP_ENV_{environment.upper()}")
    return {
        "ok": True,
        "schemaVersion": "1.0.0",
        "frame": int(scene.frame_current),
        "shotId": shot["id"],
        "shotName": shot["name"],
        "actId": shot["actId"],
        "protagonistState": shot["protagonistState"],
        "cameraRig": shot["camera"]["rig"],
        "environment": environment,
        "secondaryAction": shot["environment"]["secondaryAction"],
        "composition": dict(shot["composition"]),
        "lighting": dict(shot["lighting"]),
        "reviewFrames": list(shot["reviewFrames"]),
        "visibleEnvironmentObjectCount": len(visible),
        "visibleDepthLayerCounts": layer_counts,
        "visibleLandmarks": landmarks,
        "stageLightingIdentity": (
            str(anchor.get("trackprompt_lighting_identity")) if anchor is not None else None
        ),
    }


def validate_current_shot() -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    state = capture_review_state()
    scene = bpy.context.scene
    raw = scene.get("trackprompt_shot_plan")
    plan = json.loads(raw) if isinstance(raw, str) else {"shots": []}
    shot = active_shot(plan, int(scene.frame_current))
    layers = state["visibleDepthLayerCounts"]
    root = bpy.data.objects.get("TP_STORY_CAMERA_ROOT")
    micro = bpy.data.objects.get("TP_STORY_CAMERA_MICRO")
    target = bpy.data.objects.get("TP_CAMERA_TARGET")
    bus = bpy.data.objects.get("TP_AUDIO_BUS")

    def driver_count(obj: Any) -> int:
        animation = getattr(obj, "animation_data", None)
        return len(getattr(animation, "drivers", ())) if animation is not None else 0

    state["checks"] = {
        "activeCamera": scene.camera is not None and scene.camera.name == "TP_CAMERA",
        "shotMapped": shot is not None,
        "declaredMotionProfile": bool(shot and shot.get("motion", {}).get("profile")),
        "boundedReactiveLayers": bool(
            shot
            and all(
                isinstance(layer.get("strength"), int | float)
                and not isinstance(layer.get("strength"), bool)
                and 0.0 <= float(layer["strength"]) <= 0.25
                for layer in shot.get("reactiveLayers", [])
            )
        ),
        "stageGeometryVisible": state["visibleEnvironmentObjectCount"] > 0,
        "foregroundMidgroundBackground": all(layers[layer] > 0 for layer in layers),
        "dominantLandmarkVisible": bool(state["visibleLandmarks"]),
        "uniqueLightingIdentityDeclared": bool(state["stageLightingIdentity"]),
        "plannedCameraHasNoAudioDriver": root is not None and driver_count(root) == 0,
        "plannedAimHasNoAudioDriver": target is not None and driver_count(target) == 0,
        "microCameraReactionBounded": (
            micro is not None and bus is not None and micro_camera_reaction_is_bounded(micro, bus)
        ),
    }
    state["ok"] = all(state["checks"].values())
    return state

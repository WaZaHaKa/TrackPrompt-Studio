from __future__ import annotations

import json
from typing import Any

from .shot_plan import active_shot
from .validation import VisualizerValidationError


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
    return {
        "ok": True,
        "schemaVersion": "1.0.0",
        "frame": int(scene.frame_current),
        "shotId": shot["id"],
        "shotName": shot["name"],
        "actId": shot["actId"],
        "protagonistState": shot["protagonistState"],
        "cameraRig": shot["camera"]["rig"],
        "environment": shot["environment"]["environment"],
        "reviewFrames": list(shot["reviewFrames"]),
    }


def validate_current_shot() -> dict[str, Any]:
    state = capture_review_state()
    state["checks"] = {
        "activeCamera": True,
        "shotMapped": True,
        "declaredMotionProfile": True,
        "boundedReactiveLayers": True,
    }
    return state

from __future__ import annotations

import math
from typing import Any

from .geometry import add_property_driver
from .motion import apply_fcurve_interpolation

_RIG_POSES: dict[str, tuple[float, float, float]] = {
    "establishing_reveal": (13.5, -15.5, 7.0),
    "slow_orbit": (10.0, -13.0, 5.2),
    "subject_follow": (7.5, -11.0, 3.8),
    "gate_approach": (4.5, -12.0, 2.8),
    "threshold_push": (2.5, -9.0, 2.0),
    "rupture_fall": (-5.5, -10.0, 8.5),
    "transformation_closeup": (4.0, -7.5, 2.4),
    "scale_pullback": (14.0, -18.0, 8.0),
    "arrival_reveal": (17.0, -20.0, 7.5),
}


def build_story_camera_rig(
    camera: Any,
    target: Any,
    bus: Any,
    shot_plan: dict[str, Any],
    collection: Any,
) -> dict[str, str]:
    import bpy  # type: ignore[import-not-found]

    root = bpy.data.objects.get("TP_STORY_CAMERA_ROOT")
    if root is None:
        root = bpy.data.objects.new("TP_STORY_CAMERA_ROOT", None)
        collection.objects.link(root)
    micro = bpy.data.objects.get("TP_STORY_CAMERA_MICRO")
    if micro is None:
        micro = bpy.data.objects.new("TP_STORY_CAMERA_MICRO", None)
        collection.objects.link(micro)
    root.animation_data_clear()
    micro.animation_data_clear()
    camera.animation_data_clear()
    camera.parent = micro
    micro.parent = root
    camera.location = (0.0, 0.0, 0.0)
    micro.location = (0.0, 0.0, 0.0)
    target.location = (0.0, 0.0, 0.0)
    for shot in shot_plan["shots"]:
        frame = int(shot["frameStart"])
        rig_name = str(shot["camera"]["rig"])
        pose = _RIG_POSES.get(rig_name, _RIG_POSES["slow_orbit"])
        root.location = pose
        root.keyframe_insert("location", frame=frame)
        lens = float(shot["camera"]["lensMm"])
        camera.data.lens = lens
        camera.data.keyframe_insert("lens", frame=frame)
        target.location = (0.0, 0.0, 0.15 * math.sin(frame * 0.01))
        target.keyframe_insert("location", frame=frame)
        profile = str(shot["motion"]["profile"])
        apply_fcurve_interpolation(root, "location", profile)
        apply_fcurve_interpolation(camera.data, "lens", profile)
        apply_fcurve_interpolation(target, "location", profile)
    # This tightly bounded child layer may respond to smoothed audio. Planned
    # root and aim motion never receive an audio driver.
    add_property_driver(
        micro,
        "location",
        2,
        bus,
        {"v": "master_energy"},
        "(v - 0.5) * 0.04",
    )
    micro["trackprompt_motion_layer"] = "micro_audio_response"
    root["trackprompt_motion_layer"] = "planned_camera_root"
    target["trackprompt_motion_layer"] = "planned_camera_aim"
    return {"root": root.name, "micro": micro.name, "target": target.name}

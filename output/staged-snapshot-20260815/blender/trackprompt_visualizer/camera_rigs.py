from __future__ import annotations

import math
from typing import Any

from .geometry import add_property_driver
from .motion import apply_fcurve_interpolation

_RIG_POSES: dict[str, tuple[float, float, float]] = {
    "establishing_reveal": (13.5, -15.5, 7.0),
    "slow_orbit": (10.0, -13.0, 5.2),
    "subject_follow": (7.5, -11.0, 3.8),
    "gate_approach": (3.0, -9.2, 2.2),
    "threshold_push": (1.8, -7.0, 1.6),
    "rupture_fall": (-5.5, -10.0, 8.5),
    "transformation_closeup": (4.0, -7.5, 2.4),
    "scale_pullback": (14.0, -18.0, 8.0),
    "arrival_reveal": (17.0, -20.0, 7.5),
}

_RIG_MID_OFFSETS: dict[str, tuple[float, float, float]] = {
    "establishing_reveal": (-1.0, 1.4, -0.4),
    "slow_orbit": (-0.65, 0.35, 0.30),
    "subject_follow": (-0.45, 1.10, -0.15),
    "gate_approach": (-0.35, 1.35, -0.10),
    "threshold_push": (-0.20, 0.80, -0.05),
    "rupture_fall": (-0.80, -0.20, 1.20),
    "transformation_closeup": (0.45, 0.55, 0.25),
    "scale_pullback": (0.50, -0.50, 0.60),
    "arrival_reveal": (0.65, -0.40, 0.45),
}

_TARGET_OFFSETS: dict[str, tuple[float, float, float]] = {
    "establishing_reveal": (-0.20, 0.0, -0.10),
    "slow_orbit": (0.15, 0.0, 0.05),
    "subject_follow": (0.0, 0.0, 0.12),
    "gate_approach": (0.0, 0.0, 0.05),
    "threshold_push": (0.0, 0.0, 0.0),
    "rupture_fall": (-0.30, 0.0, -0.35),
    "transformation_closeup": (0.0, 0.0, 0.10),
    "scale_pullback": (0.10, 0.0, 0.15),
    "arrival_reveal": (0.25, 0.0, 0.10),
}


def _add(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(left[index] + right[index] for index in range(3))  # type: ignore[return-value]


def _midpoint(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple((left[index] + right[index]) * 0.5 for index in range(3))  # type: ignore[return-value]


def planned_camera_keyframes(shot_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Build deterministic macro-camera keys without importing Blender."""

    shots = list(shot_plan["shots"])
    result: list[dict[str, Any]] = []
    for index, shot in enumerate(shots):
        rig_name = str(shot["camera"]["rig"])
        start_pose = _RIG_POSES.get(rig_name, _RIG_POSES["slow_orbit"])
        next_shot = shots[index + 1] if index + 1 < len(shots) else None
        crosses_declared_cut = bool(next_shot and next_shot.get("transition") == "cut")
        if next_shot is not None and not crosses_declared_cut:
            next_rig = str(next_shot["camera"]["rig"])
            end_pose = _RIG_POSES.get(next_rig, start_pose)
            end_lens = float(next_shot["camera"]["lensMm"])
        else:
            end_pose = start_pose
            end_lens = float(shot["camera"]["lensMm"])
        midpoint_pose = _add(
            _midpoint(start_pose, end_pose),
            _RIG_MID_OFFSETS.get(rig_name, (0.0, 0.0, 0.0)),
        )
        start_frame = int(shot["frameStart"])
        end_frame = int(shot["frameEnd"])
        middle_frame = start_frame + (end_frame - start_frame) // 2
        start_target = _TARGET_OFFSETS.get(rig_name, (0.0, 0.0, 0.0))
        if next_shot is not None and not crosses_declared_cut:
            next_rig = str(next_shot["camera"]["rig"])
            end_target = _TARGET_OFFSETS.get(next_rig, start_target)
        else:
            end_target = start_target
        midpoint_target = _midpoint(start_target, end_target)
        result.append(
            {
                "shotId": str(shot["id"]),
                "profile": str(shot["motion"]["profile"]),
                "frames": (start_frame, middle_frame, end_frame),
                "locations": (start_pose, midpoint_pose, end_pose),
                "targetLocations": (start_target, midpoint_target, end_target),
                "lenses": (
                    float(shot["camera"]["lensMm"]),
                    (float(shot["camera"]["lensMm"]) + end_lens) * 0.5,
                    end_lens,
                ),
                "crossesDeclaredCut": crosses_declared_cut,
            }
        )
    return result


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
    camera.data.animation_data_clear()
    target.animation_data_clear()
    camera.parent = micro
    micro.parent = root
    camera.location = (0.0, 0.0, 0.0)
    micro.location = (0.0, 0.0, 0.0)
    target.location = (0.0, 0.0, 0.0)
    camera_plan = planned_camera_keyframes(shot_plan)
    for state in camera_plan:
        for frame, location, target_location, lens in zip(
            state["frames"],
            state["locations"],
            state["targetLocations"],
            state["lenses"],
            strict=True,
        ):
            root.location = location
            root.keyframe_insert("location", frame=frame)
            camera.data.lens = lens
            camera.data.keyframe_insert("lens", frame=frame)
            target.location = (
                float(target_location[0]),
                float(target_location[1]),
                float(target_location[2]) + 0.02 * math.sin(frame * 0.01),
            )
            target.keyframe_insert("location", frame=frame)
        profile = str(state["profile"])
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
    return {
        "root": root.name,
        "micro": micro.name,
        "target": target.name,
        "plannedKeyCount": sum(len(state["frames"]) for state in camera_plan),
        "authoredPath": True,
    }

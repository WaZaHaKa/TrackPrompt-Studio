from __future__ import annotations

import json
import math
from typing import Any

from .curve_importer import iter_action_fcurves


def _magnitude(values: tuple[float, ...]) -> float:
    return math.sqrt(sum(value * value for value in values))


def motion_metrics(
    samples: list[dict[str, Any]],
    *,
    fps: float,
    camera_jump_threshold: float = 40.0,
    protagonist_jump_threshold: float = 30.0,
    acceleration_threshold: float = 120.0,
    camera_angular_velocity_threshold: float = 1.1,
    lens_jump_threshold: float = 3.0,
) -> dict[str, Any]:
    """Reduce dense rendered-range samples into explicit motion diagnostics."""

    if not math.isfinite(fps) or fps <= 0.0 or len(samples) < 2:
        raise ValueError("Motion reporting requires ordered samples and positive FPS.")
    ordered = sorted(samples, key=lambda item: int(item["frame"]))
    if any(
        int(right["frame"]) != int(left["frame"]) + 1
        for left, right in zip(ordered, ordered[1:], strict=False)
    ):
        raise ValueError("Motion report samples must cover every consecutive frame.")

    camera_velocities: list[tuple[int, tuple[float, float, float], float]] = []
    protagonist_velocities: list[tuple[int, tuple[float, float, float], float]] = []
    angular_velocities: list[tuple[int, float]] = []
    lens_deltas: list[tuple[int, float]] = []
    camera_changes: list[int] = []
    for left, right in zip(ordered, ordered[1:], strict=False):
        frame = int(right["frame"])
        camera_delta = tuple(
            (float(right_value) - float(left_value)) * fps
            for left_value, right_value in zip(
                left["cameraLocation"], right["cameraLocation"], strict=True
            )
        )
        protagonist_delta = tuple(
            (float(right_value) - float(left_value)) * fps
            for left_value, right_value in zip(
                left["protagonistLocation"], right["protagonistLocation"], strict=True
            )
        )
        camera_velocities.append((frame, camera_delta, _magnitude(camera_delta)))
        protagonist_velocities.append(
            (frame, protagonist_delta, _magnitude(protagonist_delta))
        )
        angular_velocities.append((frame, float(right["cameraAngularDelta"]) * fps))
        lens_deltas.append((frame, abs(float(right["lensMm"]) - float(left["lensMm"]))))
        if right["cameraName"] != left["cameraName"]:
            camera_changes.append(frame)

    accelerations: list[tuple[int, float]] = []
    protagonist_accelerations: list[tuple[int, float]] = []
    for left, right in zip(camera_velocities, camera_velocities[1:], strict=False):
        accelerations.append(
            (
                right[0],
                _magnitude(
                    tuple(
                        (right_value - left_value) * fps
                        for left_value, right_value in zip(left[1], right[1], strict=True)
                    )
                ),
            )
        )
    for left, right in zip(
        protagonist_velocities, protagonist_velocities[1:], strict=False
    ):
        protagonist_accelerations.append(
            (
                right[0],
                _magnitude(
                    tuple(
                        (right_value - left_value) * fps
                        for left_value, right_value in zip(left[1], right[1], strict=True)
                    )
                ),
            )
        )

    camera_jumps = [
        {"frame": frame, "velocity": value}
        for frame, _vector, value in camera_velocities
        if value > camera_jump_threshold
    ]
    protagonist_jumps = [
        {"frame": frame, "velocity": value}
        for frame, _vector, value in protagonist_velocities
        if value > protagonist_jump_threshold
    ]
    acceleration_discontinuities = [
        {"frame": frame, "acceleration": value, "owner": "camera"}
        for frame, value in accelerations
        if value > acceleration_threshold
    ] + [
        {"frame": frame, "acceleration": value, "owner": "protagonist"}
        for frame, value in protagonist_accelerations
        if value > acceleration_threshold
    ]
    lens_jumps = [
        {"frame": frame, "deltaMm": delta}
        for frame, delta in lens_deltas
        if delta > lens_jump_threshold
    ]
    angular_velocity_outliers = [
        {"frame": frame, "radiansPerSecond": value}
        for frame, value in angular_velocities
        if value > camera_angular_velocity_threshold
    ]
    return {
        "sampleCount": len(ordered),
        "frameStart": int(ordered[0]["frame"]),
        "frameEnd": int(ordered[-1]["frame"]),
        "cameraVelocity": {
            "maximumUnitsPerSecond": max(item[2] for item in camera_velocities),
            "meanUnitsPerSecond": sum(item[2] for item in camera_velocities)
            / len(camera_velocities),
        },
        "cameraAngularVelocity": {
            "maximumRadiansPerSecond": max(value for _frame, value in angular_velocities),
            "meanRadiansPerSecond": sum(value for _frame, value in angular_velocities)
            / len(angular_velocities),
        },
        "protagonistVelocity": {
            "maximumUnitsPerSecond": max(item[2] for item in protagonist_velocities),
            "meanUnitsPerSecond": sum(item[2] for item in protagonist_velocities)
            / len(protagonist_velocities),
        },
        "cameraAcceleration": {
            "maximumUnitsPerSecondSquared": max(value for _frame, value in accelerations)
            if accelerations
            else 0.0,
        },
        "protagonistAcceleration": {
            "maximumUnitsPerSecondSquared": max(
                value for _frame, value in protagonist_accelerations
            )
            if protagonist_accelerations
            else 0.0,
        },
        "lens": {
            "minimumMm": min(float(item["lensMm"]) for item in ordered),
            "maximumMm": max(float(item["lensMm"]) for item in ordered),
            "maximumOneFrameDeltaMm": max(delta for _frame, delta in lens_deltas),
        },
        "oneFrameJumps": {
            "camera": camera_jumps,
            "protagonist": protagonist_jumps,
        },
        "accelerationDiscontinuities": acceleration_discontinuities,
        "angularVelocityOutliers": angular_velocity_outliers,
        "lensJumps": lens_jumps,
        "cameraChanges": camera_changes,
        "thresholds": {
            "cameraVelocity": camera_jump_threshold,
            "protagonistVelocity": protagonist_jump_threshold,
            "acceleration": acceleration_threshold,
            "cameraAngularVelocityRadiansPerSecond": camera_angular_velocity_threshold,
            "lensDeltaMm": lens_jump_threshold,
        },
    }


def _raw_audio_driver_findings(owners: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    major: list[dict[str, Any]] = []
    bounded_micro: list[dict[str, Any]] = []
    for owner in owners:
        animation = getattr(owner, "animation_data", None)
        for fcurve in list(getattr(animation, "drivers", [])) if animation is not None else []:
            driver = getattr(fcurve, "driver", None)
            targets_audio = False
            for variable in list(getattr(driver, "variables", [])):
                for target in list(getattr(variable, "targets", [])):
                    target_id = getattr(target, "id", None)
                    if getattr(target_id, "name", "") == "TP_AUDIO_BUS":
                        targets_audio = True
            if not targets_audio:
                continue
            finding = {
                "owner": str(getattr(owner, "name", "unknown")),
                "dataPath": str(getattr(fcurve, "data_path", "")),
                "arrayIndex": int(getattr(fcurve, "array_index", -1)),
                "expression": str(getattr(driver, "expression", "")),
            }
            if (
                bool(getattr(owner, "get", lambda *_args: False)("trackprompt_micro_audio_layer", False))
                and finding["dataPath"] == "location"
                and finding["arrayIndex"] == 2
                and finding["expression"] == "(v - 0.5) * 0.04"
            ):
                bounded_micro.append(finding)
            else:
                major.append(finding)
    return major, bounded_micro


def build_r12_motion_report(layout: str) -> dict[str, Any]:
    """Densely evaluate the exact R12 range in Blender without rendering media."""

    import bpy  # type: ignore[import-not-found]

    from .story_revision_r12 import apply_r12_layout

    scene = bpy.context.scene
    raw_preview = scene.get("trackprompt_preview_plan")
    if not isinstance(raw_preview, str):
        raise ValueError("The R12 scene has no preview contract.")
    preview = json.loads(raw_preview)
    continuous = preview.get("continuousRange")
    if not isinstance(continuous, dict):
        raise ValueError("The current scene is not an R12 continuous preview.")
    layout_state = apply_r12_layout(layout)
    camera = scene.camera
    protagonist_name = scene.get("trackprompt_r12_protagonist_action")
    protagonist = (
        bpy.data.objects.get(protagonist_name)
        if isinstance(protagonist_name, str)
        else bpy.data.objects.get("TP_R12_PROTAGONIST_ACTION")
    )
    if camera is None or protagonist is None:
        raise ValueError("R12 camera or protagonist action controller is missing.")
    start, end = int(continuous["startFrame"]), int(continuous["endFrame"])
    fps = scene.render.fps / scene.render.fps_base
    original_frame = scene.frame_current
    samples: list[dict[str, Any]] = []
    previous_camera_quaternion = None
    try:
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            matrix = camera.matrix_world.copy()
            quaternion = matrix.to_quaternion()
            angular_delta = (
                0.0
                if previous_camera_quaternion is None
                else previous_camera_quaternion.rotation_difference(quaternion).angle
            )
            previous_camera_quaternion = quaternion.copy()
            samples.append(
                {
                    "frame": frame,
                    "cameraName": camera.name,
                    "cameraLocation": tuple(float(value) for value in matrix.translation),
                    "cameraAngularDelta": float(angular_delta),
                    "protagonistLocation": tuple(
                        float(value) for value in protagonist.matrix_world.translation
                    ),
                    "lensMm": float(camera.data.lens),
                }
            )
    finally:
        scene.frame_set(original_frame)

    metrics = motion_metrics(samples, fps=fps)
    relevant_objects: list[Any] = [camera, protagonist]
    current = camera.parent
    while current is not None:
        relevant_objects.append(current)
        current = current.parent
    target_name = scene.get(f"trackprompt_r12_camera_target_{layout}")
    if isinstance(target_name, str) and bpy.data.objects.get(target_name) is not None:
        relevant_objects.append(bpy.data.objects[target_name])
    relevant_owners = [*relevant_objects, camera.data]
    raw_audio_macro, bounded_micro = _raw_audio_driver_findings(relevant_owners)

    overshoot: list[dict[str, Any]] = []
    from timeline_health_scan import detect_scalar_overshoot

    for owner in relevant_owners:
        animation = getattr(owner, "animation_data", None)
        action = getattr(animation, "action", None) if animation is not None else None
        if action is None:
            continue
        for fcurve in iter_action_fcurves(action):
            points = [
                (float(point.co[0]), float(point.co[1]))
                for point in fcurve.keyframe_points
                if start <= float(point.co[0]) <= end
            ]
            for finding in detect_scalar_overshoot(points, fcurve.evaluate):
                overshoot.append(
                    {
                        **finding,
                        "owner": str(getattr(owner, "name", "camera-data")),
                        "dataPath": str(fcurve.data_path),
                    }
                )

    raw_shot_plan = scene.get("trackprompt_shot_plan")
    shot_plan = json.loads(raw_shot_plan) if isinstance(raw_shot_plan, str) else {}
    declared_cuts = [
        int(shot["frameStart"])
        for shot in shot_plan.get("shots", [])
        if isinstance(shot, dict)
        and start <= int(shot.get("frameStart", 0)) <= end
        and shot.get("transition") == "cut"
    ]
    undeclared_cuts = list(metrics["cameraChanges"])
    technical_findings = [
        *metrics["oneFrameJumps"]["camera"],
        *metrics["oneFrameJumps"]["protagonist"],
        *metrics["accelerationDiscontinuities"],
        *metrics["angularVelocityOutliers"],
        *metrics["lensJumps"],
        *overshoot,
        *raw_audio_macro,
        *({"frame": frame, "code": "undeclared-camera-change"} for frame in undeclared_cuts),
    ]
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-r12-rendered-range-motion-report",
        "revisionId": "andromeda-r12-continuous-slice",
        "layout": layout,
        "responsiveState": layout_state,
        "frameStart": start,
        "frameEnd": end,
        "fps": fps,
        "durationSeconds": (end - start + 1) / fps,
        "metrics": metrics,
        "declaredCuts": declared_cuts,
        "undeclaredCuts": undeclared_cuts,
        "fCurveOvershoot": overshoot,
        "rawAudioMajorCameraLinks": raw_audio_macro,
        "boundedMicroAudioLinks": bounded_micro,
        "technicalFindings": technical_findings,
        "technicalPass": len(technical_findings) == 0 and len(declared_cuts) == 0,
        "artisticApproval": False,
    }

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Iterable as IterableABC
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, TypeGuard
from uuid import uuid4

BLENDER_ROOT = Path(__file__).resolve().parent
if str(BLENDER_ROOT) not in sys.path:
    sys.path.insert(0, str(BLENDER_ROOT))

from trackprompt_visualizer.curve_importer import CONTROL_CURVES, iter_action_fcurves  # noqa: E402

SCHEMA_VERSION = "1.0.0"
TARGETED_CONSTRAINT_TYPES = {
    "COPY_LOCATION",
    "COPY_ROTATION",
    "COPY_SCALE",
    "DAMPED_TRACK",
    "LOCKED_TRACK",
    "SHRINKWRAP",
    "TRACK_TO",
}
BACKGROUND_COLLECTION_PREFIXES = (
    "TP_BACKGROUND",
    "TP_SPACE_ENVIRONMENT",
    "TP_STARFIELD",
    "TP_NEBULA",
)


def analyze_motion_samples(
    samples: list[dict[str, Any]],
    *,
    fps: float,
    declared_cut_frames: set[int] | None = None,
    maximum_velocity: float = 8.0,
    maximum_acceleration: float = 6.0,
    maximum_angular_velocity: float = 1.0,
) -> list[dict[str, Any]]:
    """Detect sharp transform changes without depending on Blender types."""

    cuts = declared_cut_frames or set()
    ordered = sorted(samples, key=lambda item: int(item["frame"]))
    issues: list[dict[str, Any]] = []
    velocities: list[tuple[int, float]] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        left_frame = int(previous["frame"])
        right_frame = int(current["frame"])
        frame_delta = right_frame - left_frame
        if frame_delta <= 0:
            continue
        seconds = frame_delta / fps
        left_location = tuple(float(value) for value in previous["location"])
        right_location = tuple(float(value) for value in current["location"])
        distance = math.sqrt(sum((right - left) ** 2 for left, right in zip(left_location, right_location, strict=True)))
        velocity = distance / seconds
        velocities.append((right_frame, velocity))
        left_rotation = tuple(float(value) for value in previous["rotation"])
        right_rotation = tuple(float(value) for value in current["rotation"])
        angular_distance = math.sqrt(
            sum((right - left) ** 2 for left, right in zip(left_rotation, right_rotation, strict=True))
        )
        angular_velocity = angular_distance / seconds
        intentional = right_frame in cuts
        if frame_delta == 1 and velocity > maximum_velocity and not intentional:
            issues.append(
                {"code": "undeclared-one-frame-transform-jump", "frame": right_frame, "velocity": velocity}
            )
        if velocity > maximum_velocity and not intentional:
            issues.append({"code": "position-velocity-outlier", "frame": right_frame, "velocity": velocity})
        if angular_velocity > maximum_angular_velocity and not intentional:
            issues.append(
                {"code": "angular-velocity-outlier", "frame": right_frame, "angularVelocity": angular_velocity}
            )
    for previous, current in zip(velocities, velocities[1:], strict=False):
        frame_delta = current[0] - previous[0]
        # A declared cut changes both the velocity ending on the cut frame and
        # the acceleration comparison that immediately follows it. Neither is
        # evidence of an unintended camera discontinuity.
        if frame_delta <= 0 or current[0] in cuts or previous[0] in cuts:
            continue
        acceleration = abs(current[1] - previous[1]) / (frame_delta / fps)
        if acceleration > maximum_acceleration:
            issues.append(
                {"code": "acceleration-discontinuity", "frame": current[0], "acceleration": acceleration}
            )
    return issues


def detect_scalar_overshoot(
    keyframes: list[tuple[float, float]],
    evaluate: Any,
    *,
    tolerance: float = 1e-4,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for left, right in zip(keyframes, keyframes[1:], strict=False):
        midpoint = (left[0] + right[0]) / 2.0
        value = float(evaluate(midpoint))
        lower, upper = sorted((left[1], right[1]))
        if value < lower - tolerance or value > upper + tolerance:
            issues.append({"code": "unexpected-fcurve-overshoot", "frame": midpoint, "value": value})
    return issues


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate a frozen TrackPrompt Blender scene across its timeline without rendering."
    )
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-markdown", required=True)
    parser.add_argument("--cue-sheet", required=True)
    parser.add_argument("--scene-manifest")
    parser.add_argument("--expected-frame-start", type=int, default=1)
    parser.add_argument("--expected-frame-end", type=int, default=13029)
    parser.add_argument("--expected-fps", type=float, default=30.0)
    parser.add_argument("--sample-interval-frames", type=int, default=30)
    parser.add_argument("--high-energy-peak-count", type=int, default=8)
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(arguments)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{label} must be an existing absolute file path.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object.")
    return payload


def _atomic_write(path: Path, text: str) -> None:
    if not path.is_absolute():
        raise ValueError("Report paths must be absolute.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _finite_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _numeric_values(value: object) -> Iterable[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        yield float(value)
        return
    if isinstance(value, (str, bytes, bool)) or value is None:
        return
    if not isinstance(value, IterableABC):
        return
    for item in value:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            yield float(item)


def _all_finite(value: object) -> bool:
    values = list(_numeric_values(value))
    return bool(values) and all(math.isfinite(item) for item in values)


def _add_sample(
    reasons: dict[int, set[str]], frame: object, reason: str, frame_start: int, frame_end: int
) -> None:
    if isinstance(frame, bool) or not isinstance(frame, (int, float)) or not math.isfinite(float(frame)):
        return
    bounded = min(frame_end, max(frame_start, int(round(float(frame)))))
    reasons[bounded].add(reason)


def _high_energy_frames(
    cue_sheet: dict[str, Any], *, frame_start: int, frame_end: int, fps: float, count: int
) -> list[int]:
    curves = cue_sheet.get("curves")
    curve = curves.get("masterEnergy") if isinstance(curves, dict) else None
    points = curve.get("points") if isinstance(curve, dict) else None
    candidates: list[tuple[float, int]] = []
    if isinstance(points, list):
        for point in points:
            if not isinstance(point, list) or len(point) < 2:
                continue
            frame, value = point[0], point[1]
            if _finite_number(frame) and _finite_number(value):
                frame_number = int(round(float(frame)))
                if frame_start <= frame_number <= frame_end:
                    candidates.append((float(value), frame_number))
    minimum_gap = max(1, int(round(fps * 8.0)))
    selected: list[int] = []
    for _value, frame in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if all(abs(frame - existing) >= minimum_gap for existing in selected):
            selected.append(frame)
            if len(selected) >= count:
                break
    return sorted(selected)


def build_sample_plan(
    cue_sheet: dict[str, Any],
    scene_manifest: dict[str, Any],
    *,
    frame_start: int,
    frame_end: int,
    fps: float,
    interval: int,
    high_energy_peak_count: int,
) -> list[dict[str, Any]]:
    if frame_start < 1 or frame_end < frame_start:
        raise ValueError("The expected frame range is invalid.")
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Expected FPS must be finite and positive.")
    if interval < 1:
        raise ValueError("Sample interval must be at least one frame.")
    if high_energy_peak_count < 0 or high_energy_peak_count > 100:
        raise ValueError("High-energy peak count must be between 0 and 100.")

    reasons: dict[int, set[str]] = defaultdict(set)
    for frame in range(frame_start, frame_end + 1, interval):
        _add_sample(reasons, frame, "periodic", frame_start, frame_end)
    _add_sample(reasons, frame_start, "first-frame", frame_start, frame_end)
    _add_sample(reasons, frame_end, "final-frame", frame_start, frame_end)

    sections = cue_sheet.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            identifier = str(section.get("id", "unknown"))
            _add_sample(reasons, section.get("startFrame"), f"section-start:{identifier}", frame_start, frame_end)
            _add_sample(reasons, section.get("endFrame"), f"section-end:{identifier}", frame_start, frame_end)

    transitions = cue_sheet.get("transitions")
    if isinstance(transitions, list):
        for transition in transitions:
            if not isinstance(transition, dict):
                continue
            identifier = str(transition.get("id", "unknown"))
            values = [
                transition.get("frame"),
                transition.get("startFrame"),
                transition.get("centerFrame"),
                transition.get("endFrame"),
            ]
            for value in values:
                if _finite_number(value):
                    center = int(round(float(value)))
                    for offset in (-1, 0, 1):
                        _add_sample(
                            reasons,
                            center + offset,
                            f"transition:{identifier}",
                            frame_start,
                            frame_end,
                        )

    preview = scene_manifest.get("preview")
    if not isinstance(preview, dict):
        preview = {}
    stills = preview.get("stillFrames")
    if isinstance(stills, list):
        for frame in stills:
            _add_sample(reasons, frame, "representative-still", frame_start, frame_end)
    clip = preview.get("clip")
    if isinstance(clip, dict):
        for label, key in (
            ("preview-start", "startFrame"),
            ("preview-center", "centerFrame"),
            ("preview-end", "endFrame"),
        ):
            value = clip.get(key)
            if _finite_number(value):
                center = int(round(float(value)))
                for offset in (-1, 0, 1):
                    _add_sample(reasons, center + offset, label, frame_start, frame_end)

    for peak in _high_energy_frames(
        cue_sheet,
        frame_start=frame_start,
        frame_end=frame_end,
        fps=fps,
        count=high_energy_peak_count,
    ):
        for offset in (-1, 0, 1):
            _add_sample(reasons, peak + offset, "high-energy-peak", frame_start, frame_end)

    return [
        {"frame": frame, "reasons": sorted(frame_reasons)}
        for frame, frame_reasons in sorted(reasons.items())
    ]


def _iter_animation_owners(bpy: Any, scene: Any) -> list[tuple[str, Any]]:
    owners: list[tuple[str, Any]] = [(f"scene:{scene.name}", scene)]
    for obj in bpy.data.objects:
        owners.append((f"object:{obj.name}", obj))
        if obj.data is not None:
            owners.append((f"object-data:{obj.name}", obj.data))
    for material in bpy.data.materials:
        owners.append((f"material:{material.name}", material))
        if material.node_tree is not None:
            owners.append((f"material-nodes:{material.name}", material.node_tree))
    if scene.world is not None:
        owners.append((f"world:{scene.world.name}", scene.world))
        if scene.world.node_tree is not None:
            owners.append((f"world-nodes:{scene.world.name}", scene.world.node_tree))
    compositor_tree = getattr(scene, "node_tree", None)
    if compositor_tree is None:
        compositor_tree = getattr(scene, "compositing_node_group", None)
    if compositor_tree is not None:
        owners.append((f"compositor:{scene.name}", compositor_tree))
    return owners


def _fcurve_health(bpy: Any, scene: Any, frame: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for owner_name, owner in _iter_animation_owners(bpy, scene):
        animation = getattr(owner, "animation_data", None)
        if animation is None:
            continue
        fcurves = list(getattr(animation, "drivers", []))
        action = getattr(animation, "action", None)
        if action is not None:
            fcurves.extend(iter_action_fcurves(action))
        for fcurve in fcurves:
            data_path = str(getattr(fcurve, "data_path", "unknown"))
            if hasattr(fcurve, "is_valid") and not bool(fcurve.is_valid):
                issues.append(
                    {
                        "code": "invalid-driver",
                        "owner": owner_name,
                        "dataPath": data_path,
                    }
                )
                continue
            try:
                value = fcurve.evaluate(frame)
            except Exception as exc:  # Blender RNA errors vary by data block.
                issues.append(
                    {
                        "code": "fcurve-evaluation-error",
                        "owner": owner_name,
                        "dataPath": data_path,
                        "errorType": type(exc).__name__,
                    }
                )
                continue
            if not _finite_number(value):
                issues.append(
                    {
                        "code": "non-finite-fcurve",
                        "owner": owner_name,
                        "dataPath": data_path,
                    }
                )
    return issues


def _material_health(bpy: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for material in bpy.data.materials:
        if not _all_finite(material.diffuse_color):
            issues.append({"code": "non-finite-material-value", "material": material.name, "field": "diffuse_color"})
        tree = material.node_tree
        if tree is None:
            continue
        for node in tree.nodes:
            for socket in list(node.inputs) + list(node.outputs):
                if not hasattr(socket, "default_value"):
                    continue
                values = list(_numeric_values(socket.default_value))
                if values and not all(math.isfinite(value) for value in values):
                    issues.append(
                        {
                            "code": "non-finite-material-value",
                            "material": material.name,
                            "node": node.name,
                            "socket": socket.name,
                        }
                    )
    return issues


def _object_health(bpy: Any, depsgraph: Any) -> tuple[list[dict[str, Any]], int]:
    issues: list[dict[str, Any]] = []
    visible_renderables = 0
    for obj in bpy.data.objects:
        try:
            evaluated = obj.evaluated_get(depsgraph)
            matrix_values = [float(value) for row in evaluated.matrix_world for value in row]
        except Exception as exc:
            issues.append({"code": "object-evaluation-error", "object": obj.name, "errorType": type(exc).__name__})
            continue
        if not matrix_values or not all(math.isfinite(value) for value in matrix_values):
            issues.append({"code": "non-finite-object-transform", "object": obj.name})
        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "VOLUME", "FONT"} and not obj.hide_render:
            visible_renderables += 1
        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "VOLUME", "FONT", "LIGHT", "CAMERA"} and obj.data is None:
            issues.append({"code": "broken-object-data-reference", "object": obj.name, "objectType": obj.type})
        for slot_index, slot in enumerate(obj.material_slots):
            if slot.material is None:
                issues.append({"code": "broken-material-reference", "object": obj.name, "slot": slot_index})
        for constraint in obj.constraints:
            if constraint.type in TARGETED_CONSTRAINT_TYPES and constraint.influence > 0 and getattr(constraint, "target", None) is None:
                issues.append({"code": "broken-constraint-target", "object": obj.name, "constraint": constraint.name})
        data = obj.data
        if obj.type == "CURVE" and data is not None:
            for field in ("bevel_depth", "extrude"):
                value = getattr(data, field, 0.0)
                if not _finite_number(value) or float(value) < 0:
                    issues.append({"code": "invalid-geometry-thickness", "object": obj.name, "field": field})
        if obj.type == "LIGHT" and data is not None:
            energy = getattr(data, "energy", None)
            if not _finite_number(energy) or float(energy) < 0:
                issues.append({"code": "invalid-light-energy", "object": obj.name})
    return issues, visible_renderables


def _camera_health(scene: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    camera = scene.camera
    if camera is None:
        return [{"code": "missing-active-camera"}]
    matrix_values = [float(value) for row in camera.matrix_world for value in row]
    if not matrix_values or not all(math.isfinite(value) for value in matrix_values):
        issues.append({"code": "non-finite-camera-transform", "camera": camera.name})
    data = camera.data
    clip_start = getattr(data, "clip_start", None)
    clip_end = getattr(data, "clip_end", None)
    if (
        not _finite_number(clip_start)
        or not _finite_number(clip_end)
        or float(clip_start) <= 0
        or float(clip_end) <= float(clip_start)
    ):
        issues.append({"code": "invalid-camera-clipping", "camera": camera.name})
    lens = getattr(data, "lens", None)
    if not _finite_number(lens) or not 12.0 <= float(lens) <= 200.0:
        issues.append({"code": "invalid-camera-lens", "camera": camera.name, "lens": lens})
    target_name = scene.get("trackprompt_camera_target")
    if not isinstance(target_name, str) or not target_name or scene.objects.get(target_name) is None:
        issues.append({"code": "missing-camera-target", "target": target_name})
    return issues


def _story_motion_health(bpy: Any, scene: Any, fps: float) -> list[dict[str, Any]]:
    raw = scene.get("trackprompt_shot_plan")
    if not isinstance(raw, str):
        return []
    try:
        shot_plan = json.loads(raw)
    except json.JSONDecodeError:
        return [{"code": "invalid-scene-shot-plan"}]
    shots = shot_plan.get("shots") if isinstance(shot_plan, dict) else None
    if not isinstance(shots, list):
        return [{"code": "invalid-scene-shot-plan"}]
    root = bpy.data.objects.get("TP_STORY_CAMERA_ROOT")
    target = bpy.data.objects.get("TP_CAMERA_TARGET")
    camera = scene.camera
    if root is None or target is None or camera is None:
        return [{"code": "missing-story-camera-layer"}]
    issues: list[dict[str, Any]] = []
    for obj in (root, target, camera):
        animation = getattr(obj, "animation_data", None)
        for driver in list(getattr(animation, "drivers", [])) if animation is not None else []:
            if str(getattr(driver, "data_path", "")) not in {"location", "rotation_euler", "rotation_quaternion"}:
                continue
            variables = getattr(getattr(driver, "driver", None), "variables", [])
            if any(
                "TP_AUDIO_BUS" in str(getattr(getattr(variable, "targets", [None])[0], "id", ""))
                for variable in variables
            ):
                issues.append(
                    {"code": "raw-audio-controls-major-camera-transform", "object": obj.name}
                )
    cut_frames = {
        int(shot["frameStart"])
        for shot in shots
        if isinstance(shot, dict) and shot.get("transition") == "cut"
    }
    frames: set[int] = set()
    maximum_velocity = 8.0
    maximum_acceleration = 6.0
    maximum_angular_velocity = 1.0
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        for boundary in (shot.get("frameStart"), shot.get("frameEnd")):
            if isinstance(boundary, int):
                frames.update(
                    frame
                    for frame in (boundary - 1, boundary, boundary + 1)
                    if scene.frame_start <= frame <= scene.frame_end
                )
        motion = shot.get("motion")
        if isinstance(motion, dict):
            maximum_velocity = max(maximum_velocity, float(motion.get("maximumVelocity", 0.0)))
            maximum_acceleration = max(maximum_acceleration, float(motion.get("maximumAcceleration", 0.0)))
            maximum_angular_velocity = max(
                maximum_angular_velocity,
                float(motion.get("maximumAngularVelocity", 0.0)),
            )
    original = scene.frame_current
    samples: list[dict[str, Any]] = []
    try:
        for frame in sorted(frames):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            matrix = camera.matrix_world
            rotation = matrix.to_euler()
            samples.append(
                {
                    "frame": frame,
                    "location": tuple(float(value) for value in matrix.translation),
                    "rotation": tuple(float(value) for value in rotation),
                }
            )
    finally:
        scene.frame_set(original)
    issues.extend(
        analyze_motion_samples(
            samples,
            fps=fps,
            declared_cut_frames=cut_frames,
            maximum_velocity=maximum_velocity,
            maximum_acceleration=maximum_acceleration,
            maximum_angular_velocity=maximum_angular_velocity,
        )
    )
    for owner_name, owner in ((root.name, root), (target.name, target), (camera.name, camera)):
        animation = getattr(owner, "animation_data", None)
        action = getattr(animation, "action", None) if animation is not None else None
        if action is None:
            continue
        for fcurve in iter_action_fcurves(action):
            points = [(float(point.co[0]), float(point.co[1])) for point in fcurve.keyframe_points]
            for issue in detect_scalar_overshoot(points, fcurve.evaluate):
                issues.append({**issue, "object": owner_name, "dataPath": str(fcurve.data_path)})
    return _deduplicate_issues(issues)


def _foreground_block_health(scene: Any, depsgraph: Any) -> list[dict[str, Any]]:
    camera = scene.camera
    if camera is None or camera.data is None:
        return []
    try:
        corners = list(camera.data.view_frame(scene=scene))
        if len(corners) != 4:
            return []
        center = corners[0].copy()
        center.zero()
        for corner in corners:
            center += corner
        center /= len(corners)
        rotation = camera.matrix_world.to_quaternion()
        origin = camera.matrix_world.translation
        hits: list[Any] = []
        for local_direction in [*corners, center]:
            direction = rotation @ local_direction
            if direction.length_squared <= 0:
                return []
            result = scene.ray_cast(
                depsgraph,
                origin,
                direction.normalized(),
                distance=float(camera.data.clip_end),
            )
            if not result[0] or result[4] is None:
                return []
            hits.append(result[4])
    except Exception as exc:
        return [{"code": "foreground-block-check-error", "errorType": type(exc).__name__}]
    first = hits[0]
    if any(item != first for item in hits[1:]):
        return []
    collection_names = {collection.name for collection in getattr(first, "users_collection", [])}
    if any(name.startswith(BACKGROUND_COLLECTION_PREFIXES) for name in collection_names):
        return []
    return [{"code": "foreground-object-fully-blocks-camera", "object": first.name}]


def _compositor_health(scene: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    tree = getattr(scene, "node_tree", None)
    if tree is None:
        tree = getattr(scene, "compositing_node_group", None)
    if tree is None and not bool(getattr(scene, "use_nodes", False)):
        return issues
    if tree is None:
        return [{"code": "missing-compositor-node-tree"}]
    for node in tree.nodes:
        if node.bl_idname == "CompositorNodeImage" and getattr(node, "image", None) is None:
            issues.append({"code": "missing-compositor-input", "node": node.name, "inputType": "image"})
        elif node.bl_idname == "CompositorNodeMovieClip" and getattr(node, "clip", None) is None:
            issues.append({"code": "missing-compositor-input", "node": node.name, "inputType": "movie-clip"})
        elif node.bl_idname == "CompositorNodeMask" and getattr(node, "mask", None) is None:
            issues.append({"code": "missing-compositor-input", "node": node.name, "inputType": "mask"})
        elif node.type == "GROUP" and getattr(node, "node_tree", None) is None:
            issues.append({"code": "missing-compositor-node-group", "node": node.name})
    return issues


def _audio_bus_health(bpy: Any) -> list[dict[str, Any]]:
    bus = bpy.data.objects.get("TP_AUDIO_BUS")
    if bus is None:
        return [{"code": "missing-audio-bus"}]
    issues: list[dict[str, Any]] = []
    expected_paths = {f'["{control}"]' for control in CONTROL_CURVES}
    present_paths: set[str] = set()
    animation = bus.animation_data
    action = animation.action if animation is not None else None
    if action is not None:
        present_paths = {str(fcurve.data_path) for fcurve in iter_action_fcurves(action)}
    missing_paths = sorted(expected_paths - present_paths)
    extra_paths = sorted(present_paths - expected_paths)
    for control in CONTROL_CURVES:
        value = bus.get(control)
        if not _finite_number(value):
            issues.append({"code": "missing-or-invalid-audio-bus-property", "property": control})
    if missing_paths or extra_paths or len(present_paths) != len(expected_paths):
        issues.append(
            {
                "code": "audio-bus-fcurve-contract-mismatch",
                "expectedCount": len(expected_paths),
                "actualCount": len(present_paths),
                "missingDataPaths": missing_paths,
                "unexpectedDataPaths": extra_paths,
            }
        )
    return issues


def _dependency_audit(bpy: Any) -> dict[str, Any]:
    dependencies: list[dict[str, Any]] = []

    def record(kind: str, raw_path: object, packed: bool) -> None:
        if not isinstance(raw_path, str) or not raw_path:
            return
        resolved_text = bpy.path.abspath(raw_path)
        resolved = Path(resolved_text)
        dependencies.append(
            {
                "kind": kind,
                "reference": f"{kind}-{len(dependencies) + 1}",
                "sourcePathKind": "relative" if raw_path.startswith("//") else "absolute",
                "suffix": resolved.suffix.casefold(),
                "packed": packed,
                "exists": packed or resolved.is_file(),
            }
        )

    for image in bpy.data.images:
        if image.source == "FILE":
            record("image", image.filepath, bool(image.packed_file))
    for sound in bpy.data.sounds:
        record("sound", sound.filepath, bool(getattr(sound, "packed_file", None)))
    for font in bpy.data.fonts:
        if font.name != "Bfont":
            record("font", font.filepath, bool(getattr(font, "packed_file", None)))
    for library in bpy.data.libraries:
        record("library", library.filepath, False)

    missing = [item for item in dependencies if item["exists"] is not True]
    return {
        "dependencyCount": len(dependencies),
        "missingCount": len(missing),
        "linkedLibraryCount": len(bpy.data.libraries),
        "dependencies": dependencies,
        "missing": missing,
    }


def _deduplicate_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for issue in issues:
        identity = json.dumps(issue, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        if identity not in seen:
            seen.add(identity)
            result.append(issue)
    return result


def scan_scene(
    bpy: Any,
    cue_sheet: dict[str, Any],
    scene_manifest: dict[str, Any],
    *,
    expected_frame_start: int,
    expected_frame_end: int,
    expected_fps: float,
    sample_interval_frames: int,
    high_energy_peak_count: int,
) -> dict[str, Any]:
    scene = bpy.context.scene
    actual_fps = scene.render.fps / scene.render.fps_base
    sample_plan = build_sample_plan(
        cue_sheet,
        scene_manifest,
        frame_start=expected_frame_start,
        frame_end=expected_frame_end,
        fps=expected_fps,
        interval=sample_interval_frames,
        high_energy_peak_count=high_energy_peak_count,
    )
    global_issues: list[dict[str, Any]] = []
    if scene.frame_start != expected_frame_start or scene.frame_end != expected_frame_end:
        global_issues.append(
            {
                "code": "frame-range-mismatch",
                "expected": [expected_frame_start, expected_frame_end],
                "actual": [scene.frame_start, scene.frame_end],
            }
        )
    if not math.isclose(actual_fps, expected_fps, rel_tol=0.0, abs_tol=1e-9):
        global_issues.append({"code": "fps-mismatch", "expected": expected_fps, "actual": actual_fps})
    timeline = cue_sheet.get("timeline")
    if not isinstance(timeline, dict):
        global_issues.append({"code": "cue-timeline-missing"})
    else:
        if timeline.get("frameStart") != expected_frame_start or timeline.get("frameEnd") != expected_frame_end:
            global_issues.append({"code": "cue-frame-range-mismatch"})
        cue_fps = timeline.get("fps")
        if not _finite_number(cue_fps) or not math.isclose(float(cue_fps), expected_fps, rel_tol=0.0, abs_tol=1e-9):
            global_issues.append({"code": "cue-fps-mismatch", "actual": cue_fps})

    dependency_audit = _dependency_audit(bpy)
    if dependency_audit["missingCount"]:
        global_issues.append(
            {"code": "missing-external-dependencies", "count": dependency_audit["missingCount"]}
        )
    global_issues.extend(_compositor_health(scene))
    global_issues.extend(_story_motion_health(bpy, scene, expected_fps))

    original_frame = scene.frame_current
    samples: list[dict[str, Any]] = []
    all_sample_issues: list[dict[str, Any]] = []
    try:
        for planned in sample_plan:
            frame = int(planned["frame"])
            issues: list[dict[str, Any]] = []
            try:
                scene.frame_set(frame)
                bpy.context.view_layer.update()
                depsgraph = bpy.context.evaluated_depsgraph_get()
                depsgraph.update()
            except Exception as exc:
                issues.append({"code": "dependency-graph-evaluation-error", "errorType": type(exc).__name__})
                samples.append({**planned, "ok": False, "visibleRenderableCount": 0, "issues": issues})
                all_sample_issues.extend({**issue, "frame": frame} for issue in issues)
                continue
            issues.extend(_camera_health(scene))
            issues.extend(_foreground_block_health(scene, depsgraph))
            object_issues, visible_count = _object_health(bpy, depsgraph)
            issues.extend(object_issues)
            issues.extend(_material_health(bpy))
            issues.extend(_fcurve_health(bpy, scene, frame))
            issues.extend(_audio_bus_health(bpy))
            if visible_count <= 0:
                issues.append({"code": "unintended-empty-frame"})
            issues = _deduplicate_issues(issues)
            samples.append(
                {
                    **planned,
                    "ok": not issues,
                    "visibleRenderableCount": visible_count,
                    "issues": issues,
                }
            )
            all_sample_issues.extend({**issue, "frame": frame} for issue in issues)
    finally:
        scene.frame_set(original_frame)

    issue_counts = Counter(str(item.get("code", "unknown")) for item in global_issues + all_sample_issues)
    ok = not global_issues and not all_sample_issues
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "ok": ok,
        "verdict": "READY" if ok else "NOT READY",
        "scanType": "dependency-graph-only-no-render",
        "scene": {
            "fileName": Path(bpy.data.filepath).name if bpy.data.filepath else None,
            "blenderVersion": bpy.app.version_string,
            "frameStart": scene.frame_start,
            "frameEnd": scene.frame_end,
            "fps": actual_fps,
            "renderEngine": scene.render.engine,
            "activeCamera": scene.camera.name if scene.camera else None,
            "preset": scene.get("trackprompt_preset", "unknown"),
            "seed": scene.get("trackprompt_seed", None),
        },
        "contract": {
            "expectedFrameStart": expected_frame_start,
            "expectedFrameEnd": expected_frame_end,
            "expectedFps": expected_fps,
            "sampleIntervalFrames": sample_interval_frames,
        },
        "sampleCount": len(samples),
        "passingSampleCount": sum(1 for sample in samples if sample["ok"]),
        "failingSampleCount": sum(1 for sample in samples if not sample["ok"]),
        "issueCounts": dict(sorted(issue_counts.items())),
        "globalIssues": global_issues,
        "dependencyAudit": dependency_audit,
        "samples": samples,
    }


def _markdown(report: dict[str, Any]) -> str:
    scene = report["scene"]
    lines = [
        "# Timeline health scan",
        "",
        f"**Verdict: {report['verdict']}**",
        "",
        "This is a dependency-graph evaluation only. It did not render frames or save the Blender scene.",
        "",
        "## Contract",
        "",
        f"- Scene: `{scene['fileName']}`",
        f"- Blender: `{scene['blenderVersion']}`",
        f"- Frame range: `{scene['frameStart']}..{scene['frameEnd']}`",
        f"- FPS: `{scene['fps']}`",
        f"- Sample interval: `{report['contract']['sampleIntervalFrames']}` frames",
        f"- Samples: `{report['sampleCount']}` (`{report['passingSampleCount']}` pass, `{report['failingSampleCount']}` fail)",
        "",
        "## Dependency audit",
        "",
        f"- External dependencies: `{report['dependencyAudit']['dependencyCount']}`",
        f"- Missing dependencies: `{report['dependencyAudit']['missingCount']}`",
        f"- Linked libraries: `{report['dependencyAudit']['linkedLibraryCount']}`",
        "",
        "## Issues",
        "",
    ]
    issue_counts = report["issueCounts"]
    if issue_counts:
        lines.extend(["| Code | Count |", "| --- | ---: |"])
        lines.extend(f"| `{code}` | {count} |" for code, count in issue_counts.items())
    else:
        lines.append("No blocking issues were detected at the sampled frames.")
    failing = [sample for sample in report["samples"] if not sample["ok"]]
    if failing:
        lines.extend(["", "## Failing samples", "", "| Frame | Reasons | Issue codes |", "| ---: | --- | --- |"])
        for sample in failing[:100]:
            codes = sorted({str(issue.get("code", "unknown")) for issue in sample["issues"]})
            lines.append(
                f"| {sample['frame']} | {', '.join(sample['reasons'])} | {', '.join(codes)} |"
            )
        if len(failing) > 100:
            lines.append(f"\nOnly the first 100 of {len(failing)} failing samples are shown; see JSON for all details.")
    lines.extend(["", "## Result", "", f"`{report['verdict']}`", ""])
    return "\n".join(lines)


def main() -> int:
    import bpy  # type: ignore[import-not-found]

    args = _arguments()
    try:
        output_json = Path(args.output_json).expanduser()
        output_markdown = Path(args.output_markdown).expanduser()
        cue_sheet = _load_json(Path(args.cue_sheet).expanduser(), "Cue sheet")
        scene_manifest = (
            _load_json(Path(args.scene_manifest).expanduser(), "Scene manifest")
            if args.scene_manifest
            else {}
        )
        report = scan_scene(
            bpy,
            cue_sheet,
            scene_manifest,
            expected_frame_start=args.expected_frame_start,
            expected_frame_end=args.expected_frame_end,
            expected_fps=args.expected_fps,
            sample_interval_frames=args.sample_interval_frames,
            high_energy_peak_count=args.high_energy_peak_count,
        )
        _atomic_write(output_json, json.dumps(report, indent=2, ensure_ascii=True) + "\n")
        _atomic_write(output_markdown, _markdown(report))
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "timeline-scan-input-error", "message": str(exc)}}))
        return 2
    except Exception as exc:  # Keep Blender from treating a script traceback as a successful scan.
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "timeline-scan-unhandled-error",
                        "errorType": type(exc).__name__,
                        "message": str(exc)[:500],
                    },
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )
        return 3
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "verdict": report["verdict"],
                "sampleCount": report["sampleCount"],
                "failingSampleCount": report["failingSampleCount"],
                "outputJson": str(output_json),
                "outputMarkdown": str(output_markdown),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

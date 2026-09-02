from __future__ import annotations

import json
from typing import Any

from .curve_importer import CONTROL_CURVES, iter_action_fcurves

REQUIRED_COLLECTIONS = {
    "TP_WORLD",
    "TP_CAMERAS",
    "TP_LIGHTS",
    "TP_PRIMARY_GEOMETRY",
    "TP_RINGS",
    "TP_SHARDS",
    "TP_VOCAL_ELEMENTS",
    "TP_BACKGROUND",
    "TP_DEBUG",
}


def _animation_count(owner: Any) -> int:
    animation = getattr(owner, "animation_data", None)
    if animation is None:
        return 0
    count = len(getattr(animation, "drivers", []))
    action = getattr(animation, "action", None)
    if action is not None:
        count += len(iter_action_fcurves(action))
    return count


def _audio_strips(scene: Any) -> list[Any]:
    editor = getattr(scene, "sequence_editor", None)
    if editor is None:
        return []
    strips = getattr(editor, "strips", None)
    if strips is None:
        strips = getattr(editor, "sequences", None)
    if strips is None:
        strips = []
    return [strip for strip in strips if getattr(strip, "type", "") == "SOUND"]


def audio_strip_present(scene: Any) -> bool:
    return any(strip.name == "TP_AUDIO" for strip in _audio_strips(scene))


def _json_property(scene: Any, name: str, fallback: Any) -> Any:
    raw = scene.get(name)
    if not isinstance(raw, str):
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def scene_summary() -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    bus = bpy.data.objects.get("TP_AUDIO_BUS")
    bus_fcurve_count = _animation_count(bus) if bus is not None else 0
    audio_strips = _audio_strips(scene)
    fcurve_count = sum(_animation_count(obj) + _animation_count(obj.data) for obj in bpy.data.objects)
    fcurve_count += sum(
        _animation_count(material) + _animation_count(material.node_tree)
        for material in bpy.data.materials
        if material.node_tree is not None
    )
    if scene.world is not None:
        fcurve_count += _animation_count(scene.world)
        if scene.world.node_tree is not None:
            fcurve_count += _animation_count(scene.world.node_tree)
    preview = _json_property(scene, "trackprompt_preview_plan", {})
    fallbacks = _json_property(scene, "trackprompt_curve_fallbacks", [])
    resolved_config = _json_property(scene, "trackprompt_resolved_config", {})
    preset_summary = _json_property(scene, "trackprompt_preset_summary", {})
    preset_warnings = _json_property(scene, "trackprompt_preset_warnings", [])
    preset_requirements = _json_property(scene, "trackprompt_preset_required_collections", [])
    collections = sorted(collection.name for collection in bpy.data.collections if collection.name.startswith("TP_"))
    collection_counts = {
        collection.name: len(collection.objects)
        for collection in bpy.data.collections
        if collection.name.startswith("TP_")
    }
    fps = scene.render.fps / scene.render.fps_base
    return {
        "ok": True,
        "blenderVersion": bpy.app.version_string,
        "frameStart": scene.frame_start,
        "frameEnd": scene.frame_end,
        "fps": fps,
        "durationSeconds": (scene.frame_end - scene.frame_start + 1) / fps,
        "activeCamera": scene.camera.name if scene.camera else None,
        "collections": collections,
        "collectionObjectCounts": dict(sorted(collection_counts.items())),
        "requiredCollectionsPresent": REQUIRED_COLLECTIONS.issubset(collections),
        "requiredPresetCollections": preset_requirements,
        "requiredPresetCollectionsPresent": set(preset_requirements).issubset(collections),
        "objectCount": len(bpy.data.objects),
        "materialCount": len(bpy.data.materials),
        "fCurveCount": fcurve_count,
        "audioBusPresent": bus is not None,
        "audioBusFCurveCount": bus_fcurve_count,
        "controlProperties": sorted(name for name in CONTROL_CURVES if bus is not None and name in bus),
        "cueSheetSchemaVersion": scene.get("trackprompt_cue_schema", "unknown"),
        "preset": scene.get("trackprompt_preset", "unknown"),
        "seed": scene.get("trackprompt_seed", 0),
        "resolvedConfiguration": resolved_config,
        "presetSummary": preset_summary,
        "warnings": preset_warnings,
        "cameraTarget": scene.get("trackprompt_camera_target", None),
        "audioStripPresent": audio_strip_present(scene),
        "audioStripCount": len(audio_strips),
        "audioStripNames": sorted(strip.name for strip in audio_strips),
        "missingCurveFallbacks": fallbacks,
        "outputFile": bpy.data.filepath or None,
        "previewFrames": preview.get("stillFrames", []),
        "previewRoles": preview.get("stillRoles", []),
        "previewClip": preview.get("clip", {}),
        "renderEngine": scene.render.engine,
    }

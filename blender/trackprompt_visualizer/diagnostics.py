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
    preview = json.loads(scene.get("trackprompt_preview_plan", "{}"))
    fallbacks = json.loads(scene.get("trackprompt_curve_fallbacks", "[]"))
    collections = sorted(collection.name for collection in bpy.data.collections if collection.name.startswith("TP_"))
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
        "requiredCollectionsPresent": REQUIRED_COLLECTIONS.issubset(collections),
        "objectCount": len(bpy.data.objects),
        "materialCount": len(bpy.data.materials),
        "fCurveCount": fcurve_count,
        "audioBusPresent": bus is not None,
        "audioBusFCurveCount": bus_fcurve_count,
        "controlProperties": sorted(name for name in CONTROL_CURVES if bus is not None and name in bus),
        "cueSheetSchemaVersion": scene.get("trackprompt_cue_schema", "unknown"),
        "preset": scene.get("trackprompt_preset", "unknown"),
        "seed": scene.get("trackprompt_seed", 0),
        "audioStripPresent": audio_strip_present(scene),
        "audioStripCount": len(audio_strips),
        "audioStripNames": sorted(strip.name for strip in audio_strips),
        "missingCurveFallbacks": fallbacks,
        "outputFile": bpy.data.filepath or None,
        "previewFrames": preview.get("stillFrames", []),
        "renderEngine": scene.render.engine,
    }

from __future__ import annotations

import json
from typing import Any, Callable

from .cue_loader import load_cue_sheet
from .curve_importer import CONTROL_CURVES, create_audio_bus
from .diagnostics import audio_strip_present, scene_summary as collect_scene_summary
from .geometry import clear_scene, move_to_collection
from .preset_abstract_geometry import build_abstract_geometry
from .preview import build_preview_plan
from .timeline import attach_audio, configure_timeline
from .validation import (
    VisualizerValidationError,
    validate_input_file,
    validate_output_directory,
    validate_output_file,
)

AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg"}


def _safe_entrypoint(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except VisualizerValidationError as exc:
        return {"ok": False, "error": {"code": "validation_failed", "message": str(exc)}}
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "blender_operation_failed",
                "message": "The Blender operation failed safely.",
                "errorType": type(exc).__name__,
            },
        }


def _configure_preview_scene() -> None:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.use_file_extension = True


def _build_scene(
    cue_path: str,
    audio_path: str,
    output_blend: str,
    preset: str,
    seed: int,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    if preset != "abstract-geometry":
        raise VisualizerValidationError("Only the abstract-geometry preset is available in this MVP.")
    if not 0 <= seed <= 2_147_483_647:
        raise VisualizerValidationError("Seed must be between 0 and 2147483647.")
    cues = load_cue_sheet(cue_path)
    audio = validate_input_file(audio_path, label="Audio", suffixes=AUDIO_SUFFIXES)
    output = validate_output_file(output_blend, suffix=".blend")
    # Validation is complete before this explicit destructive build boundary.
    clear_scene()
    configure_timeline(cues)
    attach_audio(audio, int(cues["timeline"]["frameStart"]))
    bus, fallbacks = create_audio_bus(cues)
    preset_summary = build_abstract_geometry(cues, bus, seed)
    move_to_collection(bus, bpy.data.collections["TP_DEBUG"])
    preview_plan = build_preview_plan(cues)
    bpy.context.scene.render.resolution_x = 1280
    bpy.context.scene.render.resolution_y = 720
    _configure_preview_scene()
    scene = bpy.context.scene
    scene["trackprompt_cue_schema"] = cues["schemaVersion"]
    scene["trackprompt_preset"] = preset
    scene["trackprompt_seed"] = seed
    scene["trackprompt_curve_fallbacks"] = json.dumps(fallbacks, separators=(",", ":"))
    scene["trackprompt_preview_plan"] = json.dumps(preview_plan, separators=(",", ":"))
    scene["trackprompt_preset_summary"] = json.dumps(preset_summary, separators=(",", ":"))
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    summary = collect_scene_summary()
    expected_controls = sorted(CONTROL_CURVES)
    checks = {
        "frameRange": (
            int(summary["frameStart"]) == int(cues["timeline"]["frameStart"])
            and int(summary["frameEnd"]) == int(cues["timeline"]["frameEnd"])
        ),
        "fps": abs(float(summary["fps"]) - float(cues["timeline"]["fps"])) < 1e-9,
        "activeCamera": summary["activeCamera"] == "TP_CAMERA",
        "collections": summary["requiredCollectionsPresent"] is True,
        "audioBus": summary["audioBusPresent"] is True,
        "audioBusControls": summary["controlProperties"] == expected_controls,
        "audioBusFCurves": int(summary["audioBusFCurveCount"]) == len(expected_controls),
        "sceneFCurves": int(summary["fCurveCount"]) > int(summary["audioBusFCurveCount"]),
        "audioStrip": summary["audioStripPresent"] is True,
        "outputFile": summary["outputFile"] == str(output),
    }
    contract_ok = all(checks.values())
    manifest = {
        "ok": contract_ok,
        "schemaVersion": "1.0.0",
        "preset": preset,
        "seed": seed,
        "cueSheetSchemaVersion": cues["schemaVersion"],
        "missingCurveFallbacks": fallbacks,
        "preview": preview_plan,
        "checks": checks,
        "scene": summary,
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        **summary,
        "ok": contract_ok,
        "checks": checks,
        "manifest": str(manifest_path),
        "presetSummary": preset_summary,
    }


def build_scene(
    cue_path: str,
    audio_path: str,
    output_blend: str,
    preset: str = "abstract-geometry",
    seed: int = 84291,
) -> dict[str, Any]:
    """Validate, build, save, and summarize one caller-approved scene."""
    return _safe_entrypoint(lambda: _build_scene(cue_path, audio_path, output_blend, preset, seed))


def scene_summary() -> dict[str, Any]:
    return _safe_entrypoint(collect_scene_summary)


def _preview_plan() -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    raw = bpy.context.scene.get("trackprompt_preview_plan")
    if not isinstance(raw, str):
        raise VisualizerValidationError("The scene has no TrackPrompt preview plan.")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise VisualizerValidationError("The scene preview plan is invalid.")
    return parsed


def _render_preview_stills(output_directory: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    output = validate_output_directory(output_directory)
    scene = bpy.context.scene
    plan = _preview_plan()
    _configure_preview_scene()
    planned_frames = [int(frame) for frame in plan.get("stillFrames", [])]
    rendered: list[dict[str, Any]] = []
    original_frame = scene.frame_current
    try:
        for frame in planned_frames:
            scene.frame_set(frame)
            path = output / f"frame_{frame:06d}.png"
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            if not path.is_file() or path.stat().st_size <= 0:
                raise RuntimeError("Blender did not write a planned still frame.")
            rendered.append({"frame": frame, "path": str(path), "sizeBytes": path.stat().st_size})
    finally:
        scene.frame_set(original_frame)
    return {
        "ok": [item["frame"] for item in rendered] == planned_frames,
        "plannedFrames": planned_frames,
        "renderedFrames": [item["frame"] for item in rendered],
        "stillFrames": [item["path"] for item in rendered],
        "stills": rendered,
    }


def render_preview_stills(output_directory: str) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _render_preview_stills(output_directory))


def _render_preview_clip(output_path: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    output = validate_output_file(output_path, suffix=".mp4")
    scene = bpy.context.scene
    plan = _preview_plan()["clip"]
    audio_requested = audio_strip_present(scene)
    original_start, original_end = scene.frame_start, scene.frame_end
    _configure_preview_scene()
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.render.ffmpeg.audio_codec = "AAC"
    scene.render.filepath = str(output)
    scene.frame_start = int(plan["startFrame"])
    scene.frame_end = int(plan["endFrame"])
    try:
        bpy.ops.render.render(animation=True)
    finally:
        scene.frame_start, scene.frame_end = original_start, original_end
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("Blender did not write the preview clip.")
    return {
        "ok": True,
        "clip": str(output),
        "startFrame": int(plan["startFrame"]),
        "endFrame": int(plan["endFrame"]),
        "plannedDurationSeconds": (int(plan["endFrame"]) - int(plan["startFrame"]) + 1)
        / (scene.render.fps / scene.render.fps_base),
        "encoder": "blender-ffmpeg",
        "audioRequested": audio_requested,
        "audioMuxStatus": "requested-unverified" if audio_requested else "not-requested-no-audio-strip",
        "verification": {"ok": False, "status": "not-probed-by-mcp-entrypoint"},
    }


def render_preview_clip(output_path: str) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _render_preview_clip(output_path))


def _save_scene(path: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    output = validate_output_file(path, suffix=".blend")
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    return {"ok": True, "outputFile": str(output)}


def save_scene(path: str) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _save_scene(path))

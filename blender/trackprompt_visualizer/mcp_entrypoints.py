from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Callable

from .cue_loader import load_cue_sheet
from .curve_importer import CONTROL_CURVES, create_audio_bus
from .diagnostics import audio_strip_present, scene_summary as collect_scene_summary
from .geometry import clear_scene, move_to_collection
from .preset_registry import DEFAULT_PRESET, DEFAULT_SEED, resolve_visualizer_config
from .preview import build_preview_plan
from .shot_plan import active_shot, load_shot_plan, validate_shot_plan
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
    config_path: str | None,
    parameters: Mapping[str, object] | None,
    shot_plan_path: str | None,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    definition, resolved_config = resolve_visualizer_config(
        preset,
        seed,
        config_path=config_path,
        parameters=parameters,
    )
    builder = definition.load_builder()
    cues = load_cue_sheet(cue_path)
    shot_plan = load_shot_plan(shot_plan_path) if shot_plan_path is not None else None
    if definition.identifier == "space-journey-story" and shot_plan is None:
        raise VisualizerValidationError("space-journey-story requires a validated shot-plan path.")
    if definition.identifier != "space-journey-story" and shot_plan is not None:
        raise VisualizerValidationError("A shot plan may only be used with space-journey-story.")
    audio = validate_input_file(audio_path, label="Audio", suffixes=AUDIO_SUFFIXES)
    output = validate_output_file(output_blend, suffix=".blend")
    # Validation is complete before this explicit destructive build boundary.
    clear_scene()
    configure_timeline(cues)
    attach_audio(audio, int(cues["timeline"]["frameStart"]))
    bus, fallbacks = create_audio_bus(cues)
    preset_summary = (
        builder(cues, bus, resolved_config.seed, resolved_config.parameters, shot_plan)
        if definition.identifier == "space-journey-story"
        else builder(cues, bus, resolved_config.seed, resolved_config.parameters)
    )
    move_to_collection(bus, bpy.data.collections["TP_DEBUG"])
    preview_plan = build_preview_plan(cues, definition.identifier, shot_plan)
    bpy.context.scene.render.resolution_x = 1280
    bpy.context.scene.render.resolution_y = 720
    _configure_preview_scene()
    scene = bpy.context.scene
    scene["trackprompt_cue_schema"] = cues["schemaVersion"]
    scene["trackprompt_preset"] = definition.identifier
    scene["trackprompt_seed"] = resolved_config.seed
    scene["trackprompt_curve_fallbacks"] = json.dumps(fallbacks, separators=(",", ":"))
    scene["trackprompt_preview_plan"] = json.dumps(preview_plan, separators=(",", ":"))
    scene["trackprompt_preset_summary"] = json.dumps(preset_summary, separators=(",", ":"))
    scene["trackprompt_resolved_config"] = json.dumps(
        resolved_config.to_public_dict(),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    preset_warnings = list(resolved_config.warnings) + [
        str(item) for item in preset_summary.get("warnings", []) if isinstance(item, str)
    ]
    scene["trackprompt_preset_warnings"] = json.dumps(preset_warnings, separators=(",", ":"))
    scene["trackprompt_preset_required_collections"] = json.dumps(
        definition.required_collections,
        separators=(",", ":"),
    )
    if not isinstance(scene.get("trackprompt_camera_target"), str):
        target = bpy.data.objects.get(str(preset_summary.get("cameraTarget", "TP_CAMERA_TARGET")))
        if target is not None:
            scene["trackprompt_camera_target"] = target.name
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
        "presetCollections": summary["requiredPresetCollectionsPresent"] is True,
        "audioBus": summary["audioBusPresent"] is True,
        "audioBusControls": summary["controlProperties"] == expected_controls,
        "audioBusFCurves": int(summary["audioBusFCurveCount"]) == len(expected_controls),
        "sceneFCurves": int(summary["fCurveCount"]) > int(summary["audioBusFCurveCount"]),
        "audioStrip": summary["audioStripPresent"] is True,
        "cameraTarget": (
            isinstance(summary["cameraTarget"], str)
            and bool(summary["cameraTarget"])
            and bpy.data.objects.get(summary["cameraTarget"]) is not None
        ),
        "resolvedConfiguration": summary["resolvedConfiguration"] == resolved_config.to_public_dict(),
        "outputFile": summary["outputFile"] == str(output),
    }
    contract_ok = all(checks.values())
    manifest = {
        "ok": contract_ok,
        "schemaVersion": "1.0.0",
        "preset": definition.identifier,
        "seed": resolved_config.seed,
        "cueSheetSchemaVersion": cues["schemaVersion"],
        "visualizerConfig": resolved_config.to_public_dict(),
        "warnings": preset_warnings,
        "missingCurveFallbacks": fallbacks,
        "preview": preview_plan,
        "presetSummary": preset_summary,
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
    preset: str = DEFAULT_PRESET,
    seed: int = DEFAULT_SEED,
    config_path: str | None = None,
    parameters: Mapping[str, object] | None = None,
    shot_plan_path: str | None = None,
) -> dict[str, Any]:
    """Validate, build, save, and summarize one caller-approved scene."""
    return _safe_entrypoint(
        lambda: _build_scene(
            cue_path,
            audio_path,
            output_blend,
            preset,
            seed,
            config_path,
            parameters,
            shot_plan_path,
        )
    )


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
    role_by_frame = {
        int(item["frame"]): item
        for item in plan.get("stillRoles", [])
        if isinstance(item, dict) and isinstance(item.get("frame"), int | float)
    }
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
            role = role_by_frame.get(frame, {})
            context = {
                key: role[key]
                for key in ("role", "sectionId", "actId", "shotId")
                if isinstance(role.get(key), str) and role.get(key)
            }
            rendered.append(
                {
                    "frame": frame,
                    "path": str(path),
                    "sizeBytes": path.stat().st_size,
                    **context,
                }
            )
    finally:
        scene.frame_set(original_frame)
    return {
        "ok": [item["frame"] for item in rendered] == planned_frames,
        "plannedFrames": planned_frames,
        "renderedFrames": [item["frame"] for item in rendered],
        "stillFrames": [item["path"] for item in rendered],
        "stillRoles": plan.get("stillRoles", []),
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
        "role": plan.get("role"),
        "centerFrame": plan.get("centerFrame"),
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


def _load_story_revision(shot_plan_path: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    plan = load_shot_plan(shot_plan_path)
    scene = bpy.context.scene
    scene["trackprompt_shot_plan"] = json.dumps(
        plan,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    scene["trackprompt_shot_schema"] = plan["schemaVersion"]
    return {
        "ok": True,
        "schemaVersion": plan["schemaVersion"],
        "shotCount": len(plan["shots"]),
        "frameStart": plan["frameStart"],
        "frameEnd": plan["frameEnd"],
    }


def load_story_revision(shot_plan_path: str) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _load_story_revision(shot_plan_path))


def build_story_scene(
    cue_path: str,
    audio_path: str,
    shot_plan_path: str,
    output_blend: str,
    seed: int = DEFAULT_SEED,
    config_path: str | None = None,
    parameters: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    return build_scene(
        cue_path,
        audio_path,
        output_blend,
        preset="space-journey-story",
        seed=seed,
        config_path=config_path,
        parameters=parameters,
        shot_plan_path=shot_plan_path,
    )


def _set_review_shot(shot_id: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    raw = bpy.context.scene.get("trackprompt_shot_plan")
    if not isinstance(raw, str):
        raise VisualizerValidationError("The scene has no V2 shot plan.")
    plan = json.loads(raw)
    validate_shot_plan(plan)
    shot = next((item for item in plan["shots"] if item["id"] == shot_id), None)
    if shot is None:
        raise VisualizerValidationError("The requested review shot does not exist.")
    frame = int(shot["reviewFrames"][len(shot["reviewFrames"]) // 2])
    bpy.context.scene.frame_set(frame)
    return {"ok": True, "shotId": shot_id, "frame": frame, "actId": shot["actId"]}


def set_review_shot(shot_id: str) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _set_review_shot(shot_id))


def _set_review_frame(frame: int) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    if isinstance(frame, bool) or not isinstance(frame, int) or not scene.frame_start <= frame <= scene.frame_end:
        raise VisualizerValidationError("Review frame is outside the scene timeline.")
    raw = scene.get("trackprompt_shot_plan")
    if not isinstance(raw, str):
        raise VisualizerValidationError("The scene has no V2 shot plan.")
    plan = json.loads(raw)
    shot = active_shot(plan, frame)
    if shot is None:
        raise VisualizerValidationError("Review frame does not map to a shot.")
    scene.frame_set(frame)
    return {"ok": True, "frame": frame, "shotId": shot["id"], "actId": shot["actId"]}


def set_review_frame(frame: int) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _set_review_frame(frame))


def build_shot(shot_id: str) -> dict[str, Any]:
    """Select a source-defined shot after a deterministic full-scene build."""
    return set_review_shot(shot_id)


def apply_shot_revision(shot_plan_path: str, shot_id: str) -> dict[str, Any]:
    loaded = load_story_revision(shot_plan_path)
    if loaded.get("ok") is not True:
        return loaded
    return set_review_shot(shot_id)


def validate_current_shot() -> dict[str, Any]:
    from .art_direction import validate_current_shot as validate

    return _safe_entrypoint(validate)


def capture_review_state() -> dict[str, Any]:
    from .art_direction import capture_review_state as capture

    return _safe_entrypoint(capture)


def save_revision_snapshot(path: str) -> dict[str, Any]:
    return save_scene(path)

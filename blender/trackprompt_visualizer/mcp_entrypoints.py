from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .cue_loader import load_cue_sheet
from .curve_importer import CONTROL_CURVES, create_audio_bus
from .diagnostics import audio_strip_present, scene_summary as collect_scene_summary
from .geometry import clear_scene, move_to_collection
from .preset_registry import DEFAULT_PRESET, DEFAULT_SEED, resolve_visualizer_config
from .preview import build_continuous_review_spec, build_preview_plan, build_review_edit_spec
from .shot_plan import active_shot, load_shot_plan, validate_shot_plan
from .timeline import attach_audio, configure_timeline
from .validation import (
    VisualizerValidationError,
    validate_input_file,
    validate_output_directory,
    validate_output_file,
)

AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg"}
MCP_RENDER_RECEIPT_NAME = "mcp-render-receipt.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_payload_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    data = json.dumps(
        payload,
        indent=2,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
    ) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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


def _apply_preview_layout(plan: dict[str, Any], layout: str | None) -> dict[str, Any] | None:
    """Select one authored R12 composition without affecting legacy previews."""

    if "continuousRange" not in plan:
        if layout is not None:
            raise VisualizerValidationError("A responsive layout may only be selected for R12.")
        return None
    formats = plan.get("formats")
    if not isinstance(formats, dict) or layout not in formats:
        raise VisualizerValidationError("R12 rendering requires landscape or vertical layout.")
    contract = formats[layout]
    if not isinstance(contract, dict):
        raise VisualizerValidationError("The R12 responsive format contract is invalid.")
    width, height = contract.get("width"), contract.get("height")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
    ):
        raise VisualizerValidationError("The R12 responsive dimensions are invalid.")
    from .story_revision_r12 import apply_r12_layout

    state = apply_r12_layout(layout)
    if not isinstance(state, dict):
        raise RuntimeError("The R12 authored layout did not return a safe state contract.")
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    return {
        "id": layout,
        "width": width,
        "height": height,
        "phoneWidth": int(contract["phoneWidth"]),
        "phoneHeight": int(contract["phoneHeight"]),
        "compositionProfile": str(contract["compositionProfile"]),
        "authoredState": state,
        "authoredStateSha256": _canonical_payload_sha256(state),
    }


def _render_preview_stills(
    output_directory: str,
    layout: str | None = None,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    output = validate_output_directory(output_directory)
    scene = bpy.context.scene
    plan = _preview_plan()
    layout_contract = _apply_preview_layout(plan, layout)
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
        **({"format": layout_contract} if layout_contract is not None else {}),
    }


def render_preview_stills(
    output_directory: str,
    layout: str | None = None,
) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _render_preview_stills(output_directory, layout))


def _scene_audio_path() -> Path | None:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    editor = getattr(scene, "sequence_editor", None)
    strips = getattr(editor, "strips", None) if editor is not None else None
    if strips is None and editor is not None:
        strips = getattr(editor, "sequences", None)
    for strip in strips or []:
        sound = getattr(strip, "sound", None)
        filepath = getattr(sound, "filepath", None)
        if getattr(strip, "type", "") == "SOUND" and isinstance(filepath, str):
            candidate = Path(bpy.path.abspath(filepath)).resolve()
            if candidate.is_file():
                return candidate
    return None


def _build_story_render_receipt(
    *,
    scene_file: Path,
    shot_plan: dict[str, Any],
    preview_plan: dict[str, Any],
    review_edit: dict[str, Any],
    rendered_frame_sequence: dict[str, Any],
    clip: Path,
) -> dict[str, Any]:
    """Build the privacy-safe receipt for the canonical six-excerpt MCP render."""

    validate_shot_plan(shot_plan)
    roles = preview_plan.get("stillRoles")
    segments = review_edit.get("segments")
    source_frames = review_edit.get("sourceFrames")
    if (
        not scene_file.is_file()
        or scene_file.suffix.casefold() != ".blend"
        or not clip.is_file()
        or not isinstance(roles, list)
        or len(roles) != 6
        or not isinstance(segments, list)
        or len(segments) != 6
        or not isinstance(source_frames, list)
        or len(source_frames) != int(review_edit.get("outputFrameCount", -1))
    ):
        raise RuntimeError("The story render receipt inputs are incomplete.")
    representative_frames: list[dict[str, Any]] = []
    for role in roles:
        if not isinstance(role, dict) or isinstance(role.get("frame"), bool):
            raise RuntimeError("The story render receipt has an invalid representative role.")
        frame = int(role["frame"])
        still = clip.parent / f"frame_{frame:06d}.png"
        if not still.is_file() or still.stat().st_size <= 0:
            raise RuntimeError("Render the six canonical stills before the story preview clip.")
        representative_frames.append(
            {
                "frame": frame,
                "file": still.name,
                "sha256": _sha256_file(still),
                "sizeBytes": still.stat().st_size,
                **{
                    key: role[key]
                    for key in ("role", "actId", "shotId")
                    if isinstance(role.get(key), str) and role.get(key)
                },
            }
        )
    sequence_count = rendered_frame_sequence.get("count")
    sequence_sha256 = rendered_frame_sequence.get("sha256")
    if (
        sequence_count != len(source_frames)
        or not isinstance(sequence_sha256, str)
        or len(sequence_sha256) != 64
    ):
        raise RuntimeError("The ordered rendered-frame digest is incomplete.")
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-blender-mcp-preview-render-receipt",
        "preset": "space-journey-story",
        "previewOnly": True,
        "scene": {
            "file": scene_file.name,
            "sha256": _sha256_file(scene_file),
            "sizeBytes": scene_file.stat().st_size,
        },
        "shotPlan": {
            "schemaVersion": shot_plan["schemaVersion"],
            "canonicalSha256": _canonical_payload_sha256(shot_plan),
            "inputDigest": shot_plan["inputDigest"],
            "seed": shot_plan["seed"],
            "shotCount": len(shot_plan["shots"]),
        },
        "reviewEdit": {
            "strategy": review_edit["strategy"],
            "segments": segments,
            "outputFrameCount": len(source_frames),
            "durationSeconds": review_edit["durationSeconds"],
            "orderedSourceFramesSha256": _canonical_payload_sha256(source_frames),
        },
        "renderedFrames": {
            "count": sequence_count,
            "orderedPngSha256": sequence_sha256,
            "hashScope": "ordered-output-index-source-frame-png-sha256-v1",
            "representativeFrames": representative_frames,
        },
        "clip": {
            "file": clip.name,
            "sha256": _sha256_file(clip),
            "sizeBytes": clip.stat().st_size,
        },
        "encoding": {
            "strategy": "external-ffmpeg-argument-array",
            "videoCodec": "libx264",
            "videoPreset": "fast",
            "constantRateFactor": 23,
            "pixelFormat": "yuv420p",
            "audioCodec": "aac",
            "audioBitrate": "160k",
            "audioEdit": "source-segment-atrim-concat",
            "fastStart": True,
        },
    }


def _render_review_edit(
    output: Path,
    ffmpeg: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    edit = build_review_edit_spec(
        plan,
        timeline_frame_start=scene.frame_start,
        timeline_frame_end=scene.frame_end,
        fps=fps,
    )
    audio = _scene_audio_path()
    if audio is None:
        raise VisualizerValidationError("Story review edit requires the attached local audio strip.")
    temporary = output.parent / f".{output.stem}.review-{uuid4().hex}"
    temporary.mkdir(exist_ok=False)
    original_frame = scene.frame_current
    original_start, original_end = scene.frame_start, scene.frame_end
    original_filepath = scene.render.filepath
    original_format = scene.render.image_settings.file_format
    output_index = 1
    rendered_sequence_digest = hashlib.sha256()
    try:
        _configure_preview_scene()
        for segment in edit["segments"]:
            prefix = temporary / f"segment-{int(segment['index']):02d}-"
            scene.render.filepath = str(prefix)
            scene.frame_start = int(segment["startFrame"])
            scene.frame_end = int(segment["endFrame"])
            bpy.ops.render.render(animation=True)
            rendered = sorted(temporary.glob(f"{prefix.name}*.png"))
            if len(rendered) != int(segment["durationFrames"]):
                raise RuntimeError("Blender did not write a complete review-edit segment.")
            source_frames = range(int(segment["startFrame"]), int(segment["endFrame"]) + 1)
            for source_frame, source in zip(source_frames, rendered, strict=True):
                destination = temporary / f"frame_{output_index:06d}.png"
                os.replace(source, destination)
                frame_digest = _sha256_file(destination)
                rendered_sequence_digest.update(
                    (
                        json.dumps(
                            {
                                "outputFrame": output_index,
                                "sourceFrame": source_frame,
                                "pngSha256": frame_digest,
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                output_index += 1
        if output_index - 1 != int(edit["outputFrameCount"]):
            raise RuntimeError("Review-edit frame publication is incomplete.")
        encoded = temporary / output.name
        command = [
            str(ffmpeg),
            "-y",
            "-nostdin",
            "-loglevel",
            "error",
            "-framerate",
            f"{fps:.8g}",
            "-start_number",
            "1",
            "-i",
            str(temporary / "frame_%06d.png"),
            "-i",
            str(audio),
            "-filter_complex",
            str(edit["audioFilter"]),
            "-map",
            "0:v:0",
            "-map",
            "[review_audio]",
            "-frames:v",
            str(edit["outputFrameCount"]),
            "-t",
            f"{float(edit['durationSeconds']):.9f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(encoded),
        ]
        completed = subprocess.run(
            command,
            shell=False,
            check=False,
            capture_output=True,
            timeout=1800,
        )
        if (
            completed.returncode != 0
            or len(completed.stdout) > 1_000_000
            or len(completed.stderr) > 1_000_000
            or not encoded.is_file()
            or encoded.stat().st_size <= 0
        ):
            raise RuntimeError("External FFmpeg did not encode the bounded review edit.")
        os.replace(encoded, output)
    finally:
        scene.frame_start, scene.frame_end = original_start, original_end
        scene.frame_set(original_frame)
        scene.render.filepath = original_filepath
        try:
            scene.render.image_settings.file_format = original_format
        except TypeError:
            scene.render.image_settings.file_format = "PNG"
        shutil.rmtree(temporary, ignore_errors=True)
    return {
        "ok": True,
        "clip": str(output),
        "strategy": edit["strategy"],
        "sourceSegments": edit["segments"],
        "outputFrameCount": edit["outputFrameCount"],
        "plannedDurationSeconds": edit["durationSeconds"],
        "encoder": "external-ffmpeg-argument-array",
        "audioRequested": True,
        "audioMuxStatus": "requested-unverified",
        "verification": {"ok": False, "status": "not-probed-by-mcp-entrypoint"},
        "renderedFrameSequence": {
            "count": int(edit["outputFrameCount"]),
            "sha256": rendered_sequence_digest.hexdigest(),
        },
    }


def _build_continuous_story_render_receipt(
    *,
    scene_file: Path,
    shot_plan: dict[str, Any],
    preview_plan: dict[str, Any],
    continuous_edit: dict[str, Any],
    rendered_frame_sequence: dict[str, Any],
    clip: Path,
    layout: dict[str, Any],
) -> dict[str, Any]:
    """Hash-bind one exact R12 range and one authored responsive composition."""

    validate_shot_plan(shot_plan)
    roles = preview_plan.get("stillRoles")
    source_frames = continuous_edit.get("sourceFrames")
    if (
        not scene_file.is_file()
        or scene_file.suffix.casefold() != ".blend"
        or not clip.is_file()
        or not isinstance(roles, list)
        or len(roles) != 8
        or not isinstance(source_frames, list)
        or len(source_frames) != int(continuous_edit.get("outputFrameCount", -1))
    ):
        raise RuntimeError("The R12 continuous render receipt inputs are incomplete.")
    representative_frames: list[dict[str, Any]] = []
    for role in roles:
        if not isinstance(role, dict) or isinstance(role.get("frame"), bool):
            raise RuntimeError("The R12 representative role is invalid.")
        frame = int(role["frame"])
        still = clip.parent / f"frame_{frame:06d}.png"
        if not still.is_file() or still.stat().st_size <= 0:
            raise RuntimeError("Render the eight R12 stills before the continuous clip.")
        representative_frames.append(
            {
                "frame": frame,
                "file": still.name,
                "sha256": _sha256_file(still),
                "sizeBytes": still.stat().st_size,
                **{
                    key: role[key]
                    for key in ("role", "actId", "shotId")
                    if isinstance(role.get(key), str) and role.get(key)
                },
            }
        )
    sequence_count = rendered_frame_sequence.get("count")
    sequence_sha256 = rendered_frame_sequence.get("sha256")
    if (
        sequence_count != len(source_frames)
        or not isinstance(sequence_sha256, str)
        or len(sequence_sha256) != 64
    ):
        raise RuntimeError("The R12 ordered rendered-frame digest is incomplete.")
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-blender-mcp-continuous-preview-render-receipt",
        "revisionId": "andromeda-r12-continuous-slice",
        "preset": "space-journey-story",
        "previewOnly": True,
        "productionAuthorized": False,
        "scene": {
            "file": scene_file.name,
            "sha256": _sha256_file(scene_file),
            "sizeBytes": scene_file.stat().st_size,
        },
        "shotPlan": {
            "schemaVersion": shot_plan["schemaVersion"],
            "canonicalSha256": _canonical_payload_sha256(shot_plan),
            "inputDigest": shot_plan["inputDigest"],
            "seed": shot_plan["seed"],
            "shotCount": len(shot_plan["shots"]),
        },
        "continuousRange": {
            "strategy": continuous_edit["strategy"],
            "startFrame": continuous_edit["startFrame"],
            "endFrame": continuous_edit["endFrame"],
            "outputFrameCount": len(source_frames),
            "durationSeconds": continuous_edit["durationSeconds"],
            "orderedSourceFramesSha256": _canonical_payload_sha256(source_frames),
        },
        "format": layout,
        "renderedFrames": {
            "count": sequence_count,
            "orderedPngSha256": sequence_sha256,
            "hashScope": "ordered-output-index-source-frame-png-sha256-v1",
            "representativeFrames": representative_frames,
        },
        "clip": {
            "file": clip.name,
            "sha256": _sha256_file(clip),
            "sizeBytes": clip.stat().st_size,
        },
        "encoding": {
            "strategy": "external-ffmpeg-argument-array",
            "videoCodec": "libx264",
            "videoPreset": "fast",
            "constantRateFactor": 23,
            "pixelFormat": "yuv420p",
            "audioCodec": "aac",
            "audioBitrate": "160k",
            "audioEdit": "single-contiguous-source-atrim",
            "fastStart": True,
        },
    }


def _render_continuous_story_clip(
    output: Path,
    ffmpeg: Path,
    plan: dict[str, Any],
    layout: dict[str, Any],
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    edit = build_continuous_review_spec(
        plan,
        timeline_frame_start=scene.frame_start,
        timeline_frame_end=scene.frame_end,
        fps=fps,
    )
    audio = _scene_audio_path()
    if audio is None:
        raise VisualizerValidationError("R12 continuous review requires the attached local audio strip.")
    temporary = output.parent / f".{output.stem}.continuous-{uuid4().hex}"
    temporary.mkdir(exist_ok=False)
    original_frame = scene.frame_current
    original_start, original_end = scene.frame_start, scene.frame_end
    original_filepath = scene.render.filepath
    original_format = scene.render.image_settings.file_format
    rendered_sequence_digest = hashlib.sha256()
    try:
        _configure_preview_scene()
        scene.render.filepath = str(temporary / "source_")
        scene.frame_start = int(edit["startFrame"])
        scene.frame_end = int(edit["endFrame"])
        bpy.ops.render.render(animation=True)
        rendered = sorted(temporary.glob("source_*.png"))
        if len(rendered) != int(edit["outputFrameCount"]):
            raise RuntimeError("Blender did not write the complete R12 continuous range.")
        for output_index, (source_frame, source) in enumerate(
            zip(edit["sourceFrames"], rendered, strict=True),
            start=1,
        ):
            destination = temporary / f"frame_{output_index:06d}.png"
            os.replace(source, destination)
            rendered_sequence_digest.update(
                (
                    json.dumps(
                        {
                            "outputFrame": output_index,
                            "sourceFrame": source_frame,
                            "pngSha256": _sha256_file(destination),
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
            )
        encoded = temporary / output.name
        command = [
            str(ffmpeg),
            "-y",
            "-nostdin",
            "-loglevel",
            "error",
            "-framerate",
            f"{fps:.8g}",
            "-start_number",
            "1",
            "-i",
            str(temporary / "frame_%06d.png"),
            "-i",
            str(audio),
            "-filter_complex",
            str(edit["audioFilter"]),
            "-map",
            "0:v:0",
            "-map",
            "[review_audio]",
            "-frames:v",
            str(edit["outputFrameCount"]),
            "-t",
            f"{float(edit['durationSeconds']):.9f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(encoded),
        ]
        completed = subprocess.run(
            command,
            shell=False,
            check=False,
            capture_output=True,
            timeout=3600,
        )
        if (
            completed.returncode != 0
            or len(completed.stdout) > 1_000_000
            or len(completed.stderr) > 1_000_000
            or not encoded.is_file()
            or encoded.stat().st_size <= 0
        ):
            raise RuntimeError("External FFmpeg did not encode the R12 continuous review.")
        os.replace(encoded, output)
    finally:
        scene.frame_start, scene.frame_end = original_start, original_end
        scene.frame_set(original_frame)
        scene.render.filepath = original_filepath
        try:
            scene.render.image_settings.file_format = original_format
        except TypeError:
            scene.render.image_settings.file_format = "PNG"
        shutil.rmtree(temporary, ignore_errors=True)
    return {
        "ok": True,
        "clip": str(output),
        "strategy": edit["strategy"],
        "startFrame": edit["startFrame"],
        "endFrame": edit["endFrame"],
        "outputFrameCount": edit["outputFrameCount"],
        "plannedDurationSeconds": edit["durationSeconds"],
        "format": layout,
        "encoder": "external-ffmpeg-argument-array",
        "audioRequested": True,
        "audioMuxStatus": "requested-unverified",
        "verification": {"ok": False, "status": "not-probed-by-mcp-entrypoint"},
        "renderedFrameSequence": {
            "count": int(edit["outputFrameCount"]),
            "sha256": rendered_sequence_digest.hexdigest(),
        },
    }


def _render_preview_clip(
    output_path: str,
    ffmpeg_path: str | None = None,
    layout: str | None = None,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    output = validate_output_file(output_path, suffix=".mp4")
    scene = bpy.context.scene
    preview_plan = _preview_plan()
    layout_contract = _apply_preview_layout(preview_plan, layout)
    if preview_plan.get("continuousRange"):
        if ffmpeg_path is None or layout_contract is None:
            raise VisualizerValidationError(
                "R12 continuous review requires explicit FFmpeg and responsive layout."
            )
        ffmpeg = validate_input_file(ffmpeg_path, label="FFmpeg")
        result = _render_continuous_story_clip(output, ffmpeg, preview_plan, layout_contract)
        raw_shot_plan = scene.get("trackprompt_shot_plan")
        if not isinstance(raw_shot_plan, str):
            raise VisualizerValidationError("The R12 scene has no identity-bound shot plan.")
        shot_plan = json.loads(raw_shot_plan)
        if not isinstance(shot_plan, dict):
            raise VisualizerValidationError("The R12 scene shot plan is invalid.")
        continuous_edit = build_continuous_review_spec(
            preview_plan,
            timeline_frame_start=scene.frame_start,
            timeline_frame_end=scene.frame_end,
            fps=scene.render.fps / scene.render.fps_base,
        )
        sequence = result.get("renderedFrameSequence")
        if not isinstance(sequence, dict):
            raise RuntimeError("The R12 render has no ordered frame digest.")
        receipt = _build_continuous_story_render_receipt(
            scene_file=Path(bpy.data.filepath).resolve(),
            shot_plan=shot_plan,
            preview_plan=preview_plan,
            continuous_edit=continuous_edit,
            rendered_frame_sequence=sequence,
            clip=output,
            layout=layout_contract,
        )
        receipt_path = output.parent / MCP_RENDER_RECEIPT_NAME
        _atomic_json(receipt_path, receipt)
        result["renderReceiptFile"] = receipt_path.name
        result["renderReceiptSha256"] = _sha256_file(receipt_path)
        return result
    if preview_plan.get("reviewSegments"):
        if ffmpeg_path is None:
            raise VisualizerValidationError("Story review edit requires an explicit local FFmpeg path.")
        ffmpeg = validate_input_file(ffmpeg_path, label="FFmpeg")
        result = _render_review_edit(output, ffmpeg, preview_plan)
        raw_shot_plan = scene.get("trackprompt_shot_plan")
        if not isinstance(raw_shot_plan, str):
            raise VisualizerValidationError("The story scene has no identity-bound shot plan.")
        shot_plan = json.loads(raw_shot_plan)
        if not isinstance(shot_plan, dict):
            raise VisualizerValidationError("The story scene shot plan is invalid.")
        scene_file = Path(bpy.data.filepath).resolve()
        review_edit = build_review_edit_spec(
            preview_plan,
            timeline_frame_start=scene.frame_start,
            timeline_frame_end=scene.frame_end,
            fps=scene.render.fps / scene.render.fps_base,
        )
        sequence = result.get("renderedFrameSequence")
        if not isinstance(sequence, dict):
            raise RuntimeError("The canonical story render has no ordered frame digest.")
        receipt = _build_story_render_receipt(
            scene_file=scene_file,
            shot_plan=shot_plan,
            preview_plan=preview_plan,
            review_edit=review_edit,
            rendered_frame_sequence=sequence,
            clip=output,
        )
        receipt_path = output.parent / MCP_RENDER_RECEIPT_NAME
        _atomic_json(receipt_path, receipt)
        result["renderReceiptFile"] = receipt_path.name
        result["renderReceiptSha256"] = _sha256_file(receipt_path)
        return result
    plan = preview_plan["clip"]
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


def render_preview_clip(
    output_path: str,
    ffmpeg_path: str | None = None,
    layout: str | None = None,
) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _render_preview_clip(output_path, ffmpeg_path, layout))


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


def _capture_review_state(
    output_path: str | None = None,
    layout: str | None = None,
) -> dict[str, Any]:
    from .art_direction import capture_review_state as capture

    state = capture()
    if output_path is None and layout is None:
        return state
    if output_path is None or layout is None:
        raise VisualizerValidationError(
            "R12 motion capture requires both output path and responsive layout."
        )
    from .render_reports import build_r12_motion_report

    output = validate_output_file(output_path, suffix=".json")
    report = build_r12_motion_report(layout)
    _atomic_json(output, report)
    return {
        "ok": True,
        "reviewState": state,
        "motionReport": str(output),
        "motionReportSha256": _sha256_file(output),
        "layout": layout,
        "technicalPass": report["technicalPass"],
    }


def capture_review_state(
    output_path: str | None = None,
    layout: str | None = None,
) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _capture_review_state(output_path, layout))


def save_revision_snapshot(path: str) -> dict[str, Any]:
    return save_scene(path)


def _build_r13_lookdev_scene(output_path: str) -> dict[str, Any]:
    output = validate_output_file(output_path, suffix=".blend")
    from .lookdev_r13 import build_r13_lookdev_scene

    return build_r13_lookdev_scene(str(output))


def build_r13_lookdev_scene(output_path: str) -> dict[str, Any]:
    """Author the bounded R13 look-development scene from the active verified R12 scene."""

    return _safe_entrypoint(lambda: _build_r13_lookdev_scene(output_path))


def _set_r13_lookdev_variant(variant_id: str) -> dict[str, Any]:
    from .lookdev_r13 import apply_r13_variant

    return apply_r13_variant(variant_id)


def set_r13_lookdev_variant(variant_id: str) -> dict[str, Any]:
    return _safe_entrypoint(lambda: _set_r13_lookdev_variant(variant_id))


def validate_r13_lookdev_scene() -> dict[str, Any]:
    from .lookdev_r13 import validate_r13_scene

    return _safe_entrypoint(validate_r13_scene)


def _render_r13_lookdev_variants(
    output_directory: str,
    snapshot_directory: str,
) -> dict[str, Any]:
    output = validate_output_directory(output_directory)
    snapshots = validate_output_directory(snapshot_directory)
    from .lookdev_r13 import render_r13_lookdev_variants

    return render_r13_lookdev_variants(str(output), str(snapshots))


def render_r13_lookdev_variants(
    output_directory: str,
    snapshot_directory: str,
) -> dict[str, Any]:
    """Render the source-defined R13 variant set and ignored revision snapshots."""

    return _safe_entrypoint(
        lambda: _render_r13_lookdev_variants(output_directory, snapshot_directory)
    )

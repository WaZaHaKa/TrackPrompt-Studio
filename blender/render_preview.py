from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

BLENDER_ROOT = Path(__file__).resolve().parent
if str(BLENDER_ROOT) not in sys.path:
    sys.path.insert(0, str(BLENDER_ROOT))

from trackprompt_visualizer.diagnostics import scene_summary  # noqa: E402
from trackprompt_visualizer.mcp_entrypoints import (  # noqa: E402
    render_preview_clip,
    render_preview_stills,
)
from trackprompt_visualizer.preset_registry import get_preset_definition  # noqa: E402
from trackprompt_visualizer.validation import VisualizerValidationError  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render bounded TrackPrompt preview artifacts.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--skip-clip", action="store_true")
    parser.add_argument("--ffmpeg", help="Optional absolute FFmpeg path for builds without Blender movie encoding")
    parser.add_argument("--ffprobe", help="Optional absolute ffprobe path used to verify movie streams and duration")
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(arguments)


def _audio_path(scene: object) -> Path | None:
    import bpy  # type: ignore[import-not-found]

    editor = getattr(scene, "sequence_editor", None)
    if editor is None:
        return None
    strips = getattr(editor, "strips", None)
    if strips is None:
        strips = getattr(editor, "sequences", None)
    for strip in strips or []:
        sound = getattr(strip, "sound", None)
        filepath = getattr(sound, "filepath", None)
        if getattr(strip, "type", "") == "SOUND" and isinstance(filepath, str):
            candidate = Path(bpy.path.abspath(filepath)).resolve()
            if candidate.is_file():
                return candidate
    return None


def _resolve_executable(argument: str | None, name: str) -> Path | None:
    if argument:
        candidate = Path(argument).expanduser()
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None
        return resolved if resolved.is_file() else None
    discovered = shutil.which(name)
    if not discovered:
        return None
    try:
        resolved = Path(discovered).resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def _ffprobe_path(ffprobe_argument: str | None, ffmpeg_argument: str | None) -> Path | None:
    if ffprobe_argument:
        return _resolve_executable(ffprobe_argument, "ffprobe")
    if ffmpeg_argument:
        ffmpeg = _resolve_executable(ffmpeg_argument, "ffmpeg")
        if ffmpeg is not None:
            executable_name = "ffprobe.exe" if ffmpeg.suffix.casefold() == ".exe" else "ffprobe"
            sibling = ffmpeg.with_name(executable_name)
            try:
                resolved = sibling.resolve(strict=True)
            except OSError:
                resolved = None
            if resolved is not None and resolved.is_file():
                return resolved
    return _resolve_executable(None, "ffprobe")


def _parse_frame_rate(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        numerator_text, separator, denominator_text = value.partition("/")
        try:
            numerator = float(numerator_text)
            denominator = float(denominator_text) if separator else 1.0
        except ValueError:
            return None
        if denominator == 0.0:
            return None
        result = numerator / denominator
    else:
        return None
    return result if math.isfinite(result) and result > 0.0 else None


def _stream_facts(streams: object) -> dict[str, object]:
    stream_items = [item for item in streams if isinstance(item, dict)] if isinstance(streams, list) else []
    video = next((item for item in stream_items if item.get("codec_type") == "video"), None)
    audio = next((item for item in stream_items if item.get("codec_type") == "audio"), None)
    frame_rate = None
    if video is not None:
        frame_rate = _parse_frame_rate(video.get("avg_frame_rate"))
        if frame_rate is None:
            frame_rate = _parse_frame_rate(video.get("r_frame_rate"))
    width = video.get("width") if video is not None else None
    height = video.get("height") if video is not None else None
    return {
        "videoPresent": video is not None,
        "audioPresent": audio is not None,
        "hasVideo": video is not None,
        "hasAudio": audio is not None,
        "videoCodec": video.get("codec_name") if video is not None else None,
        "audioCodec": audio.get("codec_name") if audio is not None else None,
        "width": int(width) if isinstance(width, (int, float)) and not isinstance(width, bool) else None,
        "height": int(height) if isinstance(height, (int, float)) and not isinstance(height, bool) else None,
        "frameRate": frame_rate,
        "fps": frame_rate,
    }


def _probe_clip(
    output: Path,
    *,
    start_frame: int,
    end_frame: int,
    fps: float,
    audio_requested: bool,
    ffprobe_argument: str | None,
    ffmpeg_argument: str | None,
) -> dict[str, object]:
    planned_duration = (end_frame - start_frame + 1) / fps
    ffprobe = _ffprobe_path(ffprobe_argument, ffmpeg_argument)
    if ffprobe is None:
        return {
            "ok": False,
            "status": "ffprobe-unavailable",
            "plannedDurationSeconds": planned_duration,
            "audioRequested": audio_requested,
        }
    command = [
        str(ffprobe),
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        str(output),
    ]
    try:
        completed = subprocess.run(
            command,
            shell=False,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "ok": False,
            "status": "ffprobe-failed",
            "plannedDurationSeconds": planned_duration,
            "audioRequested": audio_requested,
        }
    if completed.returncode != 0 or len(completed.stdout) > 1_000_000:
        return {
            "ok": False,
            "status": "ffprobe-failed",
            "plannedDurationSeconds": planned_duration,
            "audioRequested": audio_requested,
        }
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
        streams = payload["streams"]
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return {
            "ok": False,
            "status": "ffprobe-invalid-output",
            "plannedDurationSeconds": planned_duration,
            "audioRequested": audio_requested,
        }
    stream_facts = _stream_facts(streams)
    video_present = stream_facts["videoPresent"] is True
    audio_present = stream_facts["audioPresent"] is True
    tolerance = max(0.15, 2.0 / fps)
    duration_matches = math.isfinite(duration) and abs(duration - planned_duration) <= tolerance
    audio_matches = audio_present == audio_requested
    metadata_present = (
        isinstance(stream_facts["videoCodec"], str)
        and isinstance(stream_facts["width"], int)
        and isinstance(stream_facts["height"], int)
        and isinstance(stream_facts["frameRate"], float)
        and (not audio_requested or isinstance(stream_facts["audioCodec"], str))
    )
    ok = video_present and duration_matches and audio_matches and metadata_present
    return {
        "ok": ok,
        "status": "verified" if ok else "verification-failed",
        "probe": "ffprobe-argument-array",
        "plannedDurationSeconds": planned_duration,
        "durationSeconds": duration,
        "durationToleranceSeconds": tolerance,
        "durationMatches": duration_matches,
        "audioRequested": audio_requested,
        "audioMatchesRequest": audio_matches,
        **stream_facts,
    }


def _external_ffmpeg_clip(output: Path, ffmpeg_argument: str | None) -> dict[str, object]:
    import bpy  # type: ignore[import-not-found]

    ffmpeg = _resolve_executable(ffmpeg_argument, "ffmpeg")
    if ffmpeg is None:
        return {
            "ok": False,
            "error": {"code": "movie_encoder_unavailable", "message": "Blender movie encoding and external FFmpeg are unavailable."},
        }
    scene = bpy.context.scene
    raw_plan = scene.get("trackprompt_preview_plan")
    if not isinstance(raw_plan, str):
        return {"ok": False, "error": {"code": "preview_plan_missing"}}
    clip = json.loads(raw_plan)["clip"]
    start = int(clip["startFrame"])
    end = int(clip["endFrame"])
    fps = scene.render.fps / scene.render.fps_base
    audio = _audio_path(scene)
    original_start, original_end = scene.frame_start, scene.frame_end
    frame_prefix = f".trackprompt-clip-{uuid4().hex[:8]}-"
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(output.parent / frame_prefix)
    scene.frame_start, scene.frame_end = start, end
    try:
        bpy.ops.render.render(animation=True)
    finally:
        scene.frame_start, scene.frame_end = original_start, original_end
    frames = sorted(output.parent.glob(f"{frame_prefix}*.png"))
    if len(frames) != end - start + 1:
        return {
            "ok": False,
            "error": {"code": "frame_sequence_incomplete"},
            "framesDirectory": str(output.parent),
        }
    command = [
        str(ffmpeg),
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-framerate",
        f"{fps:.8g}",
        "-start_number",
        str(start),
        "-i",
        str(output.parent / f"{frame_prefix}%04d.png"),
    ]
    audio_requested = audio is not None
    if audio is not None:
        command.extend(
            [
                "-ss",
                f"{max(0.0, (start - original_start) / fps):.6f}",
                "-i",
                str(audio),
            ]
        )
    command.extend(
        [
            "-t",
            f"{(end - start + 1) / fps:.6f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if audio is not None:
        command.extend(["-c:a", "aac", "-b:a", "160k", "-shortest"])
    command.append(str(output))
    try:
        completed = subprocess.run(
            command,
            shell=False,
            check=False,
            capture_output=True,
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "ok": False,
            "error": {"code": "external_encoding_failed"},
            "framesDirectory": str(output.parent),
        }
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
        return {
            "ok": False,
            "error": {"code": "external_encoding_failed"},
            "framesDirectory": str(output.parent),
        }
    for frame in frames:
        frame.unlink(missing_ok=True)
    return {
        "ok": True,
        "clip": str(output),
        "startFrame": start,
        "endFrame": end,
        "role": clip.get("role"),
        "centerFrame": clip.get("centerFrame"),
        "encoder": "external-ffmpeg-argument-array",
        "audioRequested": audio_requested,
        "audioMuxStatus": "pending-verification",
    }


def _privacy_safe_runner_result(value: object) -> object:
    """Keep the V2 runner record useful without persisting private absolute paths."""

    if isinstance(value, dict):
        return {str(key): _privacy_safe_runner_result(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_privacy_safe_runner_result(item) for item in value]
    if isinstance(value, str) and Path(value).is_absolute():
        return Path(value).name
    return value


def main() -> int:
    import bpy  # type: ignore[import-not-found]

    args = _arguments()
    if not 320 <= args.width <= 1920 or not 180 <= args.height <= 1080:
        print(json.dumps({"ok": False, "error": {"code": "invalid_resolution"}}))
        return 1
    output = Path(args.output).expanduser()
    if not output.is_absolute():
        print(json.dumps({"ok": False, "error": {"code": "output_must_be_absolute"}}))
        return 1
    output.mkdir(parents=False, exist_ok=True)
    bpy.context.scene.render.resolution_x = args.width
    bpy.context.scene.render.resolution_y = args.height
    try:
        preset_definition = get_preset_definition(bpy.context.scene.get("trackprompt_preset", "abstract-geometry"))
    except VisualizerValidationError as exc:
        print(json.dumps({"ok": False, "error": {"code": "invalid_scene_preset", "message": str(exc)}}))
        return 1
    clip_path = output / preset_definition.preview_clip_name
    stills = render_preview_stills(str(output))
    clip: dict[str, object] = {"ok": True, "skipped": True, "reason": "explicit-skip-clip"}
    if not args.skip_clip and stills.get("ok") is True:
        ffmpeg = _resolve_executable(args.ffmpeg, "ffmpeg")
        clip = render_preview_clip(
            str(clip_path),
            ffmpeg_path=str(ffmpeg) if ffmpeg is not None else None,
        )
        if clip.get("ok") is not True and preset_definition.identifier != "space-journey-story":
            clip = _external_ffmpeg_clip(clip_path, args.ffmpeg)
        if clip.get("ok") is True:
            output_frame_count_value = clip.get("outputFrameCount")
            output_frame_count = (
                int(output_frame_count_value)
                if isinstance(output_frame_count_value, int | float)
                else int(clip["endFrame"]) - int(clip["startFrame"]) + 1
            )
            verification = _probe_clip(
                clip_path,
                start_frame=1,
                end_frame=output_frame_count,
                fps=bpy.context.scene.render.fps / bpy.context.scene.render.fps_base,
                audio_requested=bool(clip["audioRequested"]),
                ffprobe_argument=args.ffprobe,
                ffmpeg_argument=args.ffmpeg,
            )
            clip["verification"] = verification
            clip["plannedDurationSeconds"] = verification["plannedDurationSeconds"]
            if "durationSeconds" in verification:
                clip["durationSeconds"] = verification["durationSeconds"]
            if verification.get("ok") is True:
                clip["audioMuxStatus"] = (
                    "verified-muxed" if verification["audioRequested"] else "verified-video-only"
                )
            else:
                clip["audioMuxStatus"] = "unverified"
                clip["ok"] = False
    scene = scene_summary()
    planned_frames = scene.get("previewFrames", [])
    rendered_frames = stills.get("renderedFrames", [])
    verification = clip.get("verification", {}) if isinstance(clip, dict) else {}
    checks = {
        "sceneFrameRange": int(scene["frameStart"]) <= int(scene["frameEnd"]),
        "collections": scene["requiredCollectionsPresent"] is True,
        "audioBus": scene["audioBusPresent"] is True,
        "audioBusFCurves": int(scene["audioBusFCurveCount"]) == len(scene["controlProperties"]),
        "sceneFCurves": int(scene["fCurveCount"]) > int(scene["audioBusFCurveCount"]),
        "audioStrip": scene["audioStripPresent"] is True,
        "stills": stills.get("ok") is True and rendered_frames == planned_frames,
    }
    if not args.skip_clip:
        checks.update(
            {
                "movie": (
                    Path(str(clip.get("clip", ""))).is_file()
                    and verification.get("videoPresent") is True
                ),
                "movieDuration": verification.get("durationMatches") is True,
                "audioMux": verification.get("audioMatchesRequest") is True,
            }
        )
    result = {
        "ok": all(checks.values()),
        "schemaVersion": "1.0.0",
        "preset": preset_definition.identifier,
        "visualizerConfig": scene.get("resolvedConfiguration", {}),
        "warnings": scene.get("warnings", []),
        "previewRoles": scene.get("previewRoles", []),
        "previewClipName": preset_definition.preview_clip_name,
        "clipRequested": not args.skip_clip,
        "render": {
            "width": args.width,
            "height": args.height,
            "fps": bpy.context.scene.render.fps / bpy.context.scene.render.fps_base,
        },
        "checks": checks,
        "stills": stills,
        "clip": clip,
        "scene": scene,
    }
    is_story_v2 = preset_definition.identifier == "space-journey-story"
    manifest = output / ("preview-runner-manifest.json" if is_story_v2 else "preview-manifest.json")
    stored_result = _privacy_safe_runner_result(result) if is_story_v2 else result
    manifest.write_text(json.dumps(stored_result, indent=2), encoding="utf-8")
    result["manifest"] = str(manifest)
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.final_render_tooling import ToolingError, sha256_file  # noqa: E402
from blender.trackprompt_visualizer.curve_importer import iter_action_fcurves  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sanitize a baked TrackPrompt Blender scene for remote rendering.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(values)


def _remove_audio_strips(scene: Any) -> list[str]:
    removed: list[str] = []
    editor = getattr(scene, "sequence_editor", None)
    if editor is None:
        return removed
    strips = getattr(editor, "sequences_all", None) or getattr(editor, "strips_all", None) or []
    for strip in list(strips):
        if str(getattr(strip, "type", "")).upper() != "SOUND":
            continue
        removed.append(str(getattr(strip, "name", "sound")))
        owner = getattr(editor, "sequences", None) or getattr(editor, "strips", None)
        if owner is not None:
            owner.remove(strip)
    return removed


def _strip_private_properties(owner: Any) -> list[str]:
    preserved = {"trackprompt_preset", "trackprompt_schema_version"}
    private_terms = ("audio", "wav", "source", "path", "lyric", "transcript", "prompt", "model", "cue")
    removed: list[str] = []
    for key in list(owner.keys()):
        lowered = str(key).casefold()
        value = owner.get(key)
        if key in preserved:
            continue
        if any(term in lowered for term in private_terms) or (
            isinstance(value, str) and ("\\" in value or ":/" in value or "oneDrive".casefold() in value.casefold())
        ):
            removed.append(str(key))
            del owner[key]
    return removed


def main() -> int:
    import bpy  # type: ignore[import-not-found]

    try:
        args = _arguments()
        source = Path(bpy.data.filepath).resolve(strict=True)
        expected = args.expected_source_sha256.upper()
        if sha256_file(source) != expected:
            raise ToolingError("scene-hash-mismatch", "Loaded source scene is not the approved frozen candidate.")
        output = Path(args.output).resolve()
        report = Path(args.report).resolve()
        if output.exists():
            raise ToolingError("overwrite-refused", "Sanitized scene destination already exists.")
        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        audio_bus = bpy.data.objects.get("TP_AUDIO_BUS")
        action = getattr(getattr(audio_bus, "animation_data", None), "action", None)
        curve_count = len(iter_action_fcurves(action)) if action is not None else 0
        if audio_bus is None or action is None or curve_count < 10:
            raise ToolingError(
                "audio-not-fully-baked",
                "Remote export requires TP_AUDIO_BUS animation to be fully baked into at least ten curves.",
            )
        removed_strips: list[str] = []
        removed_properties: dict[str, list[str]] = {}
        for scene in bpy.data.scenes:
            removed_strips.extend(_remove_audio_strips(scene))
            keys = _strip_private_properties(scene)
            if keys:
                removed_properties[f"scene:{scene.name}"] = keys
        for collection in (bpy.data.objects, bpy.data.materials, bpy.data.worlds):
            for block in collection:
                keys = _strip_private_properties(block)
                if keys:
                    removed_properties[f"{type(block).__name__}:{block.name}"] = keys
        sound_names = [sound.name for sound in bpy.data.sounds]
        for sound in list(bpy.data.sounds):
            bpy.data.sounds.remove(sound, do_unlink=True)
        try:
            bpy.ops.outliner.orphans_purge(do_recursive=True)
        except (AttributeError, RuntimeError):
            pass
        try:
            bpy.ops.file.pack_all()
            bpy.ops.file.make_paths_relative()
        except RuntimeError as exc:
            raise ToolingError(
                "visual-assets-not-portable",
                "Blender could not pack and relativize every visual asset for the remote package.",
            ) from exc
        bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False, copy=True)
        sanitized_hash = sha256_file(output)
        payload = {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-remote-scene-sanitization",
            "completedAt": datetime.now(UTC).isoformat(),
            "sourceSceneSha256": expected,
            "sanitizedSceneSha256": sanitized_hash,
            "audioBusCurveCount": curve_count,
            "audioFullyBaked": True,
            "removedAudioStripCount": len(removed_strips),
            "removedSoundCount": len(sound_names),
            "removedPrivatePropertyCount": sum(len(keys) for keys in removed_properties.values()),
            "privateAudioIncluded": False,
            "requiresHeadlessVisualComparison": True,
        }
        report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "scene": str(output), "sceneSha256": sanitized_hash, "report": str(report)}))
        return 0
    except (OSError, ToolingError) as exc:
        code = exc.code if isinstance(exc, ToolingError) else "remote-sanitization-filesystem-error"
        print(json.dumps({"ok": False, "error": {"code": code, "message": str(exc)[:500]}}))
        return 2
    except Exception as exc:  # pragma: no cover - Blender integration guard
        print(json.dumps({"ok": False, "error": {"code": "remote-sanitization-unhandled-error", "type": type(exc).__name__, "message": str(exc)[:500]}}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

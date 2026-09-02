from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from blender.render_final_chunk import _apply_render_profile  # noqa: E402
from tools.final_render_tooling import ToolingError, _validate_exr, _validate_png, load_render_profile, sha256_file  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one bounded TrackPrompt calibration range.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--start", required=True, type=int)
    parser.add_argument("--end", required=True, type=int)
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(values)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12]}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    import bpy  # type: ignore[import-not-found]

    try:
        args = _arguments()
        profile_path = Path(args.profile).resolve(strict=True)
        output = Path(args.output).resolve()
        report_path = Path(args.report).resolve()
        calibration_root = (REPOSITORY_ROOT / "test-output" / "render-calibration").resolve()
        output.relative_to(calibration_root)
        report_path.relative_to(calibration_root)
        if output.exists() and any(output.iterdir()):
            raise ToolingError("calibration-output-not-empty", "Calibration output must be new and empty.")
        output.mkdir(parents=True, exist_ok=True)
        profile = load_render_profile(profile_path)
        if bpy.app.version_string != profile.blender_version:
            raise ToolingError("blender-version-mismatch", "Running Blender version differs from the candidate profile.")
        scene_path = Path(bpy.data.filepath).resolve(strict=True)
        scene_hash = sha256_file(scene_path)
        if scene_hash != profile.approved_scene_sha256:
            raise ToolingError("scene-hash-mismatch", "Loaded scene differs from the profile's frozen scene.")
        if args.start < profile.frame_start or args.end > profile.frame_end or args.end < args.start:
            raise ToolingError("invalid-frame-range", "Calibration range is outside the saved profile contract.")
        if args.end - args.start + 1 > 120:
            raise ToolingError("calibration-range-too-large", "A bounded calibration invocation may render at most 120 frames.")

        scene = bpy.context.scene
        _apply_render_profile(scene, profile, output, args.start, args.end)
        starts: dict[int, float] = {}
        rendered: dict[int, float] = {}
        timings: list[dict[str, Any]] = []

        def on_pre(current_scene: Any, *_: Any) -> None:
            starts[int(current_scene.frame_current)] = time.perf_counter()

        def on_post(current_scene: Any, *_: Any) -> None:
            frame = int(current_scene.frame_current)
            started = starts.get(frame, time.perf_counter())
            rendered[frame] = time.perf_counter()
            timings.append({"frame": frame, "renderSeconds": rendered[frame] - started})

        def on_write(current_scene: Any, *_: Any) -> None:
            frame = int(current_scene.frame_current)
            write_finished = time.perf_counter()
            item = next((entry for entry in reversed(timings) if entry["frame"] == frame), None)
            if item is not None:
                item["outputWriteSeconds"] = max(0.0, write_finished - rendered.get(frame, write_finished))
                item["totalSeconds"] = max(0.0, write_finished - starts.get(frame, write_finished))

        bpy.app.handlers.render_pre.append(on_pre)
        bpy.app.handlers.render_post.append(on_post)
        bpy.app.handlers.render_write.append(on_write)
        wall_started = time.perf_counter()
        try:
            bpy.ops.render.render(animation=True)
        finally:
            for handlers, callback in (
                (bpy.app.handlers.render_pre, on_pre),
                (bpy.app.handlers.render_post, on_post),
                (bpy.app.handlers.render_write, on_write),
            ):
                if callback in handlers:
                    handlers.remove(callback)
        wall_seconds = time.perf_counter() - wall_started
        expected = [output / profile.image.filename(frame) for frame in range(args.start, args.end + 1)]
        missing = [path.name for path in expected if not path.is_file() or path.stat().st_size <= 0]
        if missing:
            raise ToolingError("calibration-render-incomplete", f"Blender omitted {len(missing)} expected frame(s).")
        validation_started = time.perf_counter()
        validator = _validate_png if profile.image.format == "PNG" else _validate_exr
        for path in expected:
            width, height, bit_depth, _, _ = validator(path)
            if (width, height, bit_depth) != (profile.width, profile.height, profile.image.bit_depth):
                raise ToolingError("calibration-frame-contract-mismatch", "A calibration frame differs from the exact profile contract.")
        validation_seconds = time.perf_counter() - validation_started
        publication = output.parent / "published"
        if publication.exists():
            raise ToolingError("calibration-publication-not-empty", "Calibration publication directory already exists.")
        publication.mkdir(parents=False, exist_ok=False)
        publication_started = time.perf_counter()
        for source in expected:
            os.link(source, publication / source.name)
        publication_seconds = time.perf_counter() - publication_started
        by_frame = {int(item["frame"]): item for item in timings}
        for frame, path in zip(range(args.start, args.end + 1), expected, strict=True):
            item = by_frame.setdefault(frame, {"frame": frame})
            item["sizeBytes"] = path.stat().st_size
            item["sha256"] = sha256_file(path)
            item.setdefault("renderSeconds", 0.0)
            item.setdefault("outputWriteSeconds", 0.0)
            item.setdefault("totalSeconds", item["renderSeconds"] + item["outputWriteSeconds"])
        ordered_timings = [by_frame[frame] for frame in range(args.start, args.end + 1)]
        totals = [float(item["totalSeconds"]) for item in ordered_timings]
        warm = totals[1:] if len(totals) > 1 else totals
        sizes = [int(item["sizeBytes"]) for item in ordered_timings]
        report = {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-render-calibration-range",
            "completedAt": datetime.now(UTC).isoformat(),
            "scene": {"path": str(scene_path), "sha256": scene_hash},
            "profile": {"path": str(profile_path), "sha256": sha256_file(profile_path), "profileId": profile.profile_id},
            "blenderVersion": bpy.app.version_string,
            "frameRange": {"start": args.start, "end": args.end, "count": args.end - args.start + 1},
            "wallSeconds": wall_seconds,
            "dependencyGraphSeconds": None,
            "compositorSeconds": None,
            "frameValidationSeconds": validation_seconds,
            "chunkPublicationSeconds": publication_seconds,
            "coldStartFrameSeconds": totals[0],
            "warmMeanSeconds": statistics.fmean(warm),
            "warmMedianSeconds": statistics.median(warm),
            "warmP90Seconds": _percentile(warm, 0.90),
            "worstFrameSeconds": max(warm),
            "framesPerHour": 3600.0 / statistics.median(warm),
            "frameSize": {
                "meanBytes": statistics.fmean(sizes),
                "medianBytes": statistics.median(sizes),
                "p90Bytes": _percentile([float(value) for value in sizes], 0.90),
            },
            "timings": ordered_timings,
            "visualReviewDirectory": str(publication),
            "visualGate": {"status": "PENDING HUMAN REVIEW", "reviewer": None, "notes": []},
        }
        _atomic_json(report_path, report)
        print(json.dumps({"ok": True, "report": str(report_path), "warmMedianSeconds": report["warmMedianSeconds"]}))
        return 0
    except (OSError, ValueError, ToolingError) as exc:
        code = exc.code if isinstance(exc, ToolingError) else "calibration-filesystem-error"
        print(json.dumps({"ok": False, "error": {"code": code, "message": str(exc)[:500]}}))
        return 2
    except Exception as exc:  # pragma: no cover - Blender integration guard
        print(json.dumps({"ok": False, "error": {"code": "calibration-unhandled-error", "type": type(exc).__name__, "message": str(exc)[:500]}}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

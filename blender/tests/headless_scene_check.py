from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BLENDER_ROOT = Path(__file__).resolve().parents[1]
if str(BLENDER_ROOT) not in sys.path:
    sys.path.insert(0, str(BLENDER_ROOT))

from trackprompt_visualizer.diagnostics import scene_summary  # noqa: E402


def main() -> int:
    import bpy  # type: ignore[import-not-found]

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    args = parser.parse_args(arguments)
    output = Path(args.output).resolve()
    summary = scene_summary()
    checks = {
        "camera": summary["activeCamera"] == "TP_CAMERA",
        "collections": summary["requiredCollectionsPresent"] is True,
        "audioBus": summary["audioBusPresent"] is True,
        "audioStrip": summary["audioStripPresent"] is True,
        "audioBusFCurves": int(summary["audioBusFCurveCount"]) == len(summary["controlProperties"]),
        "sceneFCurves": int(summary["fCurveCount"]) > int(summary["audioBusFCurveCount"]),
        "objectBound": 10 <= int(summary["objectCount"]) <= 250,
        "renderEngine": str(summary["renderEngine"]).startswith("BLENDER_EEVEE"),
    }
    if not all(checks.values()):
        print(json.dumps({"ok": False, "checks": checks, "scene": summary}, separators=(",", ":")))
        return 1
    bpy.context.scene.frame_set(int(summary["previewFrames"][0]))
    bpy.context.scene.render.resolution_x = 320
    bpy.context.scene.render.resolution_y = 180
    bpy.context.scene.render.resolution_percentage = 100
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.filepath = str(output)
    bpy.ops.render.render(write_still=True)
    checks["sampleRender"] = output.is_file() and output.stat().st_size > 0
    print(json.dumps({"ok": all(checks.values()), "checks": checks, "scene": summary}, separators=(",", ":")))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

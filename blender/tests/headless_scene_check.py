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
    parser.add_argument("--expected-preset", default="abstract-geometry", choices=("abstract-geometry", "space-journey"))
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    args = parser.parse_args(arguments)
    output = Path(args.output).resolve()
    summary = scene_summary()
    checks = {
        "camera": summary["activeCamera"] == "TP_CAMERA",
        "cameraTarget": (
            isinstance(summary.get("cameraTarget"), str)
            and bpy.data.objects.get(summary["cameraTarget"]) is not None
        ),
        "preset": summary["preset"] == args.expected_preset,
        "resolvedConfiguration": (
            summary.get("resolvedConfiguration", {}).get("schemaVersion") == "1.0.0"
            and summary.get("resolvedConfiguration", {}).get("preset") == args.expected_preset
        ),
        "collections": summary["requiredCollectionsPresent"] is True,
        "presetCollections": summary["requiredPresetCollectionsPresent"] is True,
        "audioBus": summary["audioBusPresent"] is True,
        "audioStrip": summary["audioStripPresent"] is True,
        "audioBusFCurves": int(summary["audioBusFCurveCount"]) == len(summary["controlProperties"]),
        "sceneFCurves": int(summary["fCurveCount"]) > int(summary["audioBusFCurveCount"]),
        "objectBound": 10 <= int(summary["objectCount"]) <= 250,
        "renderEngine": str(summary["renderEngine"]).startswith("BLENDER_EEVEE"),
    }
    if args.expected_preset == "space-journey":
        preset_summary = summary.get("presetSummary", {})
        travel_macro = bpy.data.objects.get("TP_SPACE_TRAVEL_MACRO")
        travel_audio_rig = bpy.data.objects.get("TP_SPACE_TRAVEL_RIG")
        travel_audio_drivers = (
            list(travel_audio_rig.animation_data.drivers)
            if travel_audio_rig is not None and travel_audio_rig.animation_data is not None
            else []
        )
        checks.update(
            {
                "spaceCollections": {
                    "TP_DESTINATION",
                    "TP_SPACE_ENVIRONMENT",
                    "TP_STARFIELD",
                    "TP_NEBULA",
                    "TP_TRAVEL_PATHS",
                }.issubset(summary["collections"]),
                "spaceHero": len(preset_summary.get("heroObjects", [])) >= 10,
                "spaceRings": int(preset_summary.get("ringCount", 0)) == 6,
                "spaceCompanionRings": int(preset_summary.get("companionRingCount", 0)) == 9,
                "spaceHeroDetails": int(preset_summary.get("heroSurfaceDetailCount", 0)) >= 48,
                "spaceEnvironmentDepth": (
                    int(preset_summary.get("starLayerCount", 0)) == 4
                    and int(preset_summary.get("nebulaLayerCount", 0)) == 4
                    and int(preset_summary.get("orbitalDustCount", 0)) >= 88
                ),
                "spaceCameraOrbitBound": (
                    abs(float(preset_summary.get("directionPlan", [{}])[-1].get("cameraOrbitRadians", 99.0)))
                    <= 3.141593
                ),
                "spaceTravelRigComposition": (
                    travel_macro is not None
                    and travel_audio_rig is not None
                    and travel_audio_rig.parent == travel_macro
                    and {driver.data_path for driver in travel_audio_drivers} == {"scale"}
                ),
                "spaceSceneBound": int(summary["objectCount"]) <= 250,
            }
        )
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

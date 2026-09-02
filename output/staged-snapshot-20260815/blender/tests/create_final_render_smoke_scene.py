from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a tiny synthetic final-render tooling smoke scene.")
    parser.add_argument("--output", required=True)
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(arguments)


def main() -> int:
    import bpy  # type: ignore[import-not-found]

    args = _arguments()
    output = Path(args.output).expanduser()
    if not output.is_absolute() or output.suffix.casefold() != ".blend":
        print(json.dumps({"ok": False, "error": "Output must be an absolute .blend path."}))
        return 2
    output.parent.mkdir(parents=True, exist_ok=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = "TrackPromptFinalToolingSmoke"
    scene.frame_start = 1
    scene.frame_end = 3
    scene.render.fps = 30
    scene.render.fps_base = 1.0
    scene.render.engine = "BLENDER_EEVEE"
    scene["trackprompt_preset"] = "space-journey"
    scene.display_settings.display_device = "sRGB"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.sequencer_colorspace_settings.name = "sRGB"

    bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 0.0))
    cube = bpy.context.object
    cube.name = "TP_SYNTHETIC_CUBE"
    cube.rotation_euler.z = 0.0
    cube.keyframe_insert("rotation_euler", frame=1)
    cube.rotation_euler.z = 0.5
    cube.keyframe_insert("rotation_euler", frame=3)

    material = bpy.data.materials.new("TP_SYNTHETIC_MATERIAL")
    material.diffuse_color = (0.05, 0.25, 0.8, 1.0)
    cube.data.materials.append(material)

    bpy.ops.object.light_add(type="AREA", location=(2.0, -3.0, 4.0))
    light = bpy.context.object
    light.name = "TP_SYNTHETIC_LIGHT"
    light.data.energy = 800.0
    light.data.shape = "DISK"
    light.data.size = 5.0

    bpy.ops.object.camera_add(location=(0.0, -6.0, 1.5))
    camera = bpy.context.object
    camera.name = "TP_SYNTHETIC_CAMERA"
    camera.rotation_euler = (1.32645, 0.0, 0.0)
    scene.camera = camera

    scene.render.resolution_x = 16
    scene.render.resolution_y = 16
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.use_compositing = False
    scene.render.use_motion_blur = False
    scene.render.dither_intensity = 1.0
    scene.eevee.taa_render_samples = 1
    scene.eevee.shadow_pool_size = "128"
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    print(json.dumps({"ok": output.is_file(), "output": str(output)}, separators=(",", ":")))
    return 0 if output.is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())

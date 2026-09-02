from __future__ import annotations

import math
from typing import Any

from .geometry import add_property_driver


def create_camera(collection: Any, bus: Any, cues: dict[str, Any], seed: int) -> Any:
    import bpy  # type: ignore[import-not-found]

    target = bpy.data.objects.new("TP_CAMERA_TARGET", None)
    collection.objects.link(target)
    camera_data = bpy.data.cameras.new("TP_CAMERA_DATA")
    camera = bpy.data.objects.new("TP_CAMERA", camera_data)
    collection.objects.link(camera)
    bpy.context.scene.camera = camera
    camera_data.lens = 48.0
    camera_data.sensor_width = 36.0
    phase = (seed % 360) * math.pi / 180.0
    add_property_driver(
        camera,
        "location",
        0,
        bus,
        {"v": "master_energy"},
        f"(10.6 - v * 0.75) * cos(frame * 0.0035 + {phase:.8f})",
    )
    add_property_driver(
        camera,
        "location",
        1,
        bus,
        {"v": "master_energy"},
        f"(10.6 - v * 0.75) * sin(frame * 0.0035 + {phase:.8f})",
    )
    add_property_driver(camera, "location", 2, bus, {"v": "master_energy"}, "3.5 + v * 0.8")
    constraint = camera.constraints.new("TRACK_TO")
    constraint.name = "TP_BOUNDED_TRACK"
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"
    frame_start = int(cues["timeline"]["frameStart"])
    for section in cues.get("sections", []):
        energy = section.get("energy")
        bounded = min(1.0, max(0.0, float(energy))) if isinstance(energy, int | float) else 0.5
        camera_data.lens = 52.0 - bounded * 8.0
        camera_data.keyframe_insert("lens", frame=int(section["startFrame"]))
    for transition in cues.get("transitions", []):
        frame = int(transition["frame"])
        for offset, lens in ((-4, camera_data.lens), (0, max(38.0, camera_data.lens - 2.0)), (8, camera_data.lens)):
            camera_data.lens = lens
            camera_data.keyframe_insert("lens", frame=max(frame_start, frame + offset))
    return camera

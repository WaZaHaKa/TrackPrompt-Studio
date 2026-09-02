from __future__ import annotations

from typing import Any

from .geometry import add_property_driver


def create_lighting(collection: Any, bus: Any) -> list[Any]:
    import bpy  # type: ignore[import-not-found]

    lights: list[Any] = []
    for index, (location, color, energy) in enumerate(
        (
            ((4.5, -3.5, 6.0), (0.35, 1.0, 0.7), 850.0),
            ((-5.0, 2.5, 3.0), (0.5, 0.35, 1.0), 650.0),
            ((0.0, 0.0, -2.0), (1.0, 0.25, 0.42), 400.0),
        ),
        start=1,
    ):
        data = bpy.data.lights.new(f"TP_LIGHT_DATA_{index:02d}", type="AREA" if index < 3 else "POINT")
        data.color = color
        data.energy = energy
        if hasattr(data, "shape"):
            data.shape = "DISK"
            data.size = 4.0
        obj = bpy.data.objects.new(f"TP_LIGHT_{index:02d}", data)
        obj.location = location
        collection.objects.link(obj)
        add_property_driver(
            data,
            "energy",
            -1,
            bus,
            {"v": "master_energy" if index < 3 else "low_band"},
            f"{energy * 0.55:.4f} + v * {energy * 0.9:.4f}",
        )
        lights.append(obj)
    return lights

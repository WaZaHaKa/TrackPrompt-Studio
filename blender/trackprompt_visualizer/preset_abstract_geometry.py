from __future__ import annotations

from typing import Any

from .cameras import create_camera
from .geometry import create_collections, create_geometry
from .lighting import create_lighting
from .materials import animate_section_palette, configure_world


def deterministic_seed_plan(seed: int) -> dict[str, Any]:
    import random

    rng = random.Random(seed)
    return {
        "paletteOffset": seed % 4,
        "shardSignature": [round(rng.random(), 8) for _index in range(8)],
        "cameraPhaseDegrees": seed % 360,
    }


def build_abstract_geometry(cues: dict[str, Any], bus: Any, seed: int) -> dict[str, Any]:
    collections = create_collections()
    geometry = create_geometry(collections, bus, cues, seed)
    camera = create_camera(collections["TP_CAMERAS"], bus, cues, seed)
    lights = create_lighting(collections["TP_LIGHTS"], bus)
    configure_world(bus, seed)
    animate_section_palette(geometry["materials"], cues, seed)
    return {
        "collections": list(collections),
        "camera": camera.name,
        "lightCount": len(lights),
        "ringCount": len(geometry["rings"]),
        "shardCount": len(geometry["shards"]),
        "core": geometry["core"].name,
        "vocalElement": geometry["vocal"].name,
        "seedPlan": deterministic_seed_plan(seed),
    }

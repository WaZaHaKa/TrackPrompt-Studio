from __future__ import annotations

import math
import random
from typing import Any

from .materials import create_material, drive_emission

COLLECTION_NAMES = (
    "TP_WORLD",
    "TP_CAMERAS",
    "TP_LIGHTS",
    "TP_PRIMARY_GEOMETRY",
    "TP_RINGS",
    "TP_SHARDS",
    "TP_VOCAL_ELEMENTS",
    "TP_BACKGROUND",
    "TP_DEBUG",
)


def clear_scene() -> None:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.cameras, bpy.data.lights, bpy.data.materials, bpy.data.textures):
        for block in list(datablocks):
            datablocks.remove(block)


def create_collections() -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    result: dict[str, Any] = {}
    for name in COLLECTION_NAMES:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
        result[name] = collection
    return result


def move_to_collection(obj: Any, collection: Any) -> None:
    for existing in list(obj.users_collection):
        existing.objects.unlink(obj)
    collection.objects.link(obj)


def add_property_driver(
    target: Any,
    data_path: str,
    index: int,
    bus: Any,
    variables: dict[str, str],
    expression: str,
) -> Any:
    fcurve = target.driver_add(data_path, index) if index >= 0 else target.driver_add(data_path)
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    for name, property_name in variables.items():
        variable = driver.variables.new()
        variable.name = name
        variable.type = "SINGLE_PROP"
        variable.targets[0].id = bus
        variable.targets[0].data_path = f'["{property_name}"]'
    driver.expression = expression
    return fcurve


def _smooth_active() -> None:
    import bpy  # type: ignore[import-not-found]

    try:
        bpy.ops.object.shade_smooth()
    except RuntimeError:
        pass


def create_geometry(
    collections: dict[str, Any],
    bus: Any,
    cues: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    rng = random.Random(seed)
    core_material = create_material(
        "TP_MAT_CORE",
        (0.12, 0.75, 0.52, 1.0),
        metallic=0.72,
        roughness=0.24,
        emission_color=(0.2, 0.95, 0.68, 1.0),
        emission_strength=0.35,
    )
    ring_material = create_material(
        "TP_MAT_RINGS",
        (0.58, 0.31, 0.95, 1.0),
        metallic=0.45,
        roughness=0.2,
        emission_color=(0.7, 0.45, 1.0, 1.0),
        emission_strength=1.0,
    )
    shard_material = create_material(
        "TP_MAT_SHARDS",
        (0.18, 0.62, 0.9, 1.0),
        metallic=0.8,
        roughness=0.3,
        emission_color=(0.3, 0.72, 1.0, 1.0),
        emission_strength=0.25,
    )
    vocal_material = create_material(
        "TP_MAT_VOCAL",
        (0.95, 0.28, 0.48, 0.85),
        metallic=0.25,
        roughness=0.18,
        emission_color=(1.0, 0.22, 0.5, 1.0),
        emission_strength=0.0,
    )
    background_material = create_material(
        "TP_MAT_BACKGROUND",
        (0.025, 0.045, 0.06, 1.0),
        metallic=0.0,
        roughness=0.72,
    )
    drive_emission(core_material, bus, "brightness", "0.12 + v * 1.5")
    drive_emission(ring_material, bus, "drum_energy", "0.25 + v * 5.0")
    drive_emission(shard_material, bus, "high_band", "0.08 + v * 1.6")
    drive_emission(vocal_material, bus, "vocal_energy", "v * 5.0")

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.7, location=(0, 0, 0))
    core = bpy.context.object
    core.name = "TP_CORE"
    move_to_collection(core, collections["TP_PRIMARY_GEOMETRY"])
    core.data.materials.append(core_material)
    _smooth_active()
    texture = bpy.data.textures.new("TP_CORE_NOISE", type="CLOUDS")
    texture.noise_scale = 0.42
    displacement = core.modifiers.new("TP_AUDIO_DISPLACEMENT", "DISPLACE")
    displacement.texture = texture
    displacement.strength = 0.12
    add_property_driver(displacement, "strength", -1, bus, {"v": "master_energy"}, "0.08 + v * 0.42")
    for axis in range(3):
        add_property_driver(core, "scale", axis, bus, {"v": "bass_energy"}, "1.0 + v * 0.48")

    ring_field = bpy.data.objects.new("TP_RING_FIELD", None)
    collections["TP_RINGS"].objects.link(ring_field)
    rings: list[Any] = []
    for index in range(4):
        bpy.ops.mesh.primitive_torus_add(
            major_radius=2.65 + index * 0.58,
            minor_radius=0.045 + index * 0.012,
            major_segments=96,
            minor_segments=12,
            location=(0, 0, 0),
            rotation=(rng.uniform(-0.55, 0.55), rng.uniform(-0.55, 0.55), rng.uniform(0, math.tau)),
        )
        ring = bpy.context.object
        ring.name = f"TP_RING_{index + 1:02d}"
        move_to_collection(ring, collections["TP_RINGS"])
        ring.parent = ring_field
        ring.data.materials.append(ring_material)
        _smooth_active()
        rings.append(ring)
    add_property_driver(
        ring_field,
        "rotation_euler",
        2,
        bus,
        {"m": "master_energy"},
        "frame * 0.0025 + m * 0.18",
    )
    for axis in range(3):
        add_property_driver(
            ring_field,
            "scale",
            axis,
            bus,
            {"d": "drum_energy", "t": "transient_activity"},
            "1.0 + d * 0.08 + t * 0.10",
        )

    shard_field = bpy.data.objects.new("TP_SHARD_FIELD", None)
    collections["TP_SHARDS"].objects.link(shard_field)
    bpy.ops.mesh.primitive_cone_add(vertices=5, radius1=0.11, radius2=0.02, depth=0.85)
    template = bpy.context.object
    template.name = "TP_SHARD_001"
    move_to_collection(template, collections["TP_SHARDS"])
    template.data.materials.append(shard_material)
    shards = [template]
    for index in range(42):
        shard = template if index == 0 else template.copy()
        if index:
            shard.data = template.data
            collections["TP_SHARDS"].objects.link(shard)
            shard.name = f"TP_SHARD_{index + 1:03d}"
            shards.append(shard)
        radius = rng.uniform(4.5, 8.2)
        theta = rng.uniform(0, math.tau)
        phi = rng.uniform(-0.62, 0.62)
        shard.location = (
            radius * math.cos(theta) * math.cos(phi),
            radius * math.sin(theta) * math.cos(phi),
            radius * math.sin(phi),
        )
        shard.rotation_euler = (rng.random() * math.tau, rng.random() * math.tau, rng.random() * math.tau)
        uniform = rng.uniform(0.45, 1.25)
        shard.scale = (uniform, uniform, uniform)
        shard.parent = shard_field
    add_property_driver(
        shard_field,
        "rotation_euler",
        2,
        bus,
        {"m": "master_energy"},
        "-frame * 0.0012 - m * 0.12",
    )
    for axis in range(3):
        add_property_driver(
            shard_field,
            "scale",
            axis,
            bus,
            {"h": "high_band", "t": "transient_activity"},
            "0.88 + h * 0.22 + t * 0.08",
        )

    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=2.18)
    vocal = bpy.context.object
    vocal.name = "TP_VOCAL_ORBIT"
    move_to_collection(vocal, collections["TP_VOCAL_ELEMENTS"])
    vocal.data.materials.append(vocal_material)
    wire = vocal.modifiers.new("TP_VOCAL_WIREFRAME", "WIREFRAME")
    wire.thickness = 0.018
    for axis in range(3):
        add_property_driver(vocal, "scale", axis, bus, {"v": "vocal_energy"}, "0.84 + v * 0.24")

    bpy.ops.mesh.primitive_plane_add(size=44, location=(0, 0, -5.8))
    floor = bpy.context.object
    floor.name = "TP_BACKGROUND_FLOOR"
    move_to_collection(floor, collections["TP_BACKGROUND"])
    floor.data.materials.append(background_material)
    bevel = floor.modifiers.new("TP_BACKGROUND_BEVEL", "BEVEL")
    bevel.width = 0.2
    bevel.segments = 2

    for transition in cues.get("transitions", []):
        frame = int(transition["frame"])
        for offset, scale in ((-4, 1.0), (0, 1.08), (8, 1.0)):
            ring_field.scale = (scale, scale, scale)
            ring_field.keyframe_insert("scale", frame=max(int(cues["timeline"]["frameStart"]), frame + offset))

    return {
        "core": core,
        "ringField": ring_field,
        "rings": rings,
        "shardField": shard_field,
        "shards": shards,
        "vocal": vocal,
        "materials": [core_material, ring_material, shard_material, vocal_material],
    }

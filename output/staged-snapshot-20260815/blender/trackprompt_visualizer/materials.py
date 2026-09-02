from __future__ import annotations

import random
from typing import Any

PALETTES = [
    ((0.055, 0.16, 0.12, 1.0), (0.18, 0.95, 0.62, 1.0), (0.72, 0.57, 1.0, 1.0)),
    ((0.04, 0.08, 0.18, 1.0), (0.20, 0.62, 1.0, 1.0), (1.0, 0.43, 0.62, 1.0)),
    ((0.16, 0.055, 0.09, 1.0), (1.0, 0.42, 0.30, 1.0), (0.98, 0.78, 0.34, 1.0)),
    ((0.08, 0.055, 0.16, 1.0), (0.66, 0.34, 1.0, 1.0), (0.24, 0.92, 0.92, 1.0)),
]


def _input(node: Any, *names: str) -> Any | None:
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    return None


def create_material(
    name: str,
    base_color: tuple[float, float, float, float],
    *,
    metallic: float,
    roughness: float,
    emission_color: tuple[float, float, float, float] | None = None,
    emission_strength: float = 0.0,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = base_color
    nodes = material.node_tree.nodes
    principled = nodes.get("Principled BSDF")
    if principled is None:
        raise RuntimeError("Blender Principled BSDF node is unavailable.")
    base = _input(principled, "Base Color")
    metal = _input(principled, "Metallic")
    rough = _input(principled, "Roughness")
    emission = _input(principled, "Emission Color", "Emission")
    strength = _input(principled, "Emission Strength")
    if base is not None:
        base.default_value = base_color
    if metal is not None:
        metal.default_value = metallic
    if rough is not None:
        rough.default_value = roughness
    if emission is not None:
        emission.default_value = emission_color or base_color
    if strength is not None:
        strength.default_value = emission_strength
    material["tp_principled_node"] = principled.name
    return material


def add_socket_driver(socket: Any, bus: Any, property_name: str, expression: str) -> None:
    fcurve = socket.driver_add("default_value")
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    variable = driver.variables.new()
    variable.name = "v"
    variable.type = "SINGLE_PROP"
    variable.targets[0].id = bus
    variable.targets[0].data_path = f'["{property_name}"]'
    driver.expression = expression


def drive_emission(material: Any, bus: Any, property_name: str, expression: str) -> None:
    principled = material.node_tree.nodes.get(material.get("tp_principled_node", "Principled BSDF"))
    if principled is None:
        return
    strength = _input(principled, "Emission Strength")
    if strength is not None:
        add_socket_driver(strength, bus, property_name, expression)


def animate_section_palette(materials: list[Any], cues: dict[str, Any], seed: int) -> None:
    sections = cues.get("sections", [])
    if not sections:
        return
    rng = random.Random(seed)
    offset = rng.randrange(len(PALETTES))
    for index, section in enumerate(sections):
        palette = PALETTES[(offset + index) % len(PALETTES)]
        frame = int(section["startFrame"])
        for material_index, material in enumerate(materials):
            principled = material.node_tree.nodes.get(material.get("tp_principled_node", "Principled BSDF"))
            if principled is None:
                continue
            color = palette[(material_index + 1) % len(palette)]
            for socket in (
                _input(principled, "Base Color"),
                _input(principled, "Emission Color", "Emission"),
            ):
                if socket is not None:
                    socket.default_value = color
                    socket.keyframe_insert("default_value", frame=frame)


def configure_world(bus: Any, seed: int) -> Any:
    import bpy  # type: ignore[import-not-found]

    world = bpy.data.worlds.new("TP_WORLD_MATERIAL")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        palette = PALETTES[seed % len(PALETTES)]
        background.inputs["Color"].default_value = palette[0]
        background.inputs["Strength"].default_value = 0.035
        add_socket_driver(background.inputs["Strength"], bus, "master_energy", "0.025 + v * 0.075")
    return world

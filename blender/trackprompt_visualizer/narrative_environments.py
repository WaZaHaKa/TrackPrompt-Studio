from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .curve_importer import iter_action_fcurves
from .materials import create_material
from .motion import apply_fcurve_interpolation

ENVIRONMENTS = (
    "dead_moon",
    "signal_ruins",
    "launch_structure",
    "gate_corridor",
    "broken_void",
    "transformation_megastructure",
    "andromeda_arrival",
)

DEPTH_LAYERS = ("foreground", "midground", "background")
REVIEWED_ENVIRONMENTS = ENVIRONMENTS[:4]

Color = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class StageVisualSpec:
    environment: str
    landmark: str
    dominant_shape: str
    lighting_identity: str
    secondary_action: str
    layers: tuple[str, ...] = DEPTH_LAYERS


STAGE_VISUAL_SPECS: dict[str, StageVisualSpec] = {
    "dead_moon": StageVisualSpec(
        environment="dead_moon",
        landmark="TP_ENV_SIGNAL_DEAD_MOON",
        dominant_shape="dead-moon crescent cut by a needle beacon",
        lighting_identity="cold-slate-beacon-amber",
        secondary_action="distant-beacon-sweep",
    ),
    "signal_ruins": StageVisualSpec(
        environment="signal_ruins",
        landmark="TP_ENV_AWAKENING_IRIS",
        dominant_shape="opening iris chamber enclosing the orb",
        lighting_identity="enclosed-teal-reactivation-amber",
        secondary_action="authored-chamber-opening",
    ),
    "launch_structure": StageVisualSpec(
        environment="launch_structure",
        landmark="TP_ENV_DEPARTURE_CORRIDOR",
        dominant_shape="repeating launch ribs converging on a vanishing point",
        lighting_identity="cobalt-depth-hot-white-guides",
        secondary_action="sequential-guide-packets",
    ),
    "gate_corridor": StageVisualSpec(
        environment="gate_corridor",
        landmark="TP_ENV_GATE_DIAMOND",
        dominant_shape="split monolith diamond threshold larger than frame",
        lighting_identity="indigo-emerald-threshold",
        secondary_action="threshold-shockwave-and-route-seal",
    ),
    "broken_void": StageVisualSpec(
        environment="broken_void",
        landmark="TP_ENV_RUPTURE_FRACTURE",
        dominant_shape="diagonal void fracture",
        lighting_identity="desaturated-void-fracture-red",
        secondary_action="fracture-shear",
    ),
    "transformation_megastructure": StageVisualSpec(
        environment="transformation_megastructure",
        landmark="TP_ENV_TRANSFORMATION_CRADLE",
        dominant_shape="radial reconstruction cradle",
        lighting_identity="violet-reconstruction-white-core",
        secondary_action="reconstruction-arm-alignment",
    ),
    "andromeda_arrival": StageVisualSpec(
        environment="andromeda_arrival",
        landmark="TP_ENV_ARRIVAL_HORIZON",
        dominant_shape="vast Andromeda horizon arc",
        lighting_identity="blue-white-arrival-soft-violet",
        secondary_action="galactic-horizon-unfolding",
    ),
}


def reviewed_stage_contract() -> tuple[StageVisualSpec, ...]:
    """Return the immutable phone-review art contract without importing Blender."""

    return tuple(STAGE_VISUAL_SPECS[name] for name in REVIEWED_ENVIRONMENTS)


def _tag(
    obj: Any,
    environment: str,
    layer: str,
    role: str,
    *,
    landmark: bool = False,
) -> Any:
    obj["trackprompt_environment"] = environment
    obj["trackprompt_depth_layer"] = layer
    obj["trackprompt_narrative_role"] = role
    obj["trackprompt_landmark"] = bool(landmark)
    return obj


def _material(
    name: str,
    base: Color,
    *,
    emission: Color | None = None,
    strength: float = 0.0,
    metallic: float = 0.35,
    roughness: float = 0.42,
) -> Any:
    return create_material(
        name,
        base,
        metallic=metallic,
        roughness=roughness,
        emission_color=emission,
        emission_strength=strength,
    )


def story_action_frames(shot: dict[str, Any]) -> tuple[int, int, int]:
    """Return bounded start/action/end frames even for a one-frame act."""

    start = int(shot["frameStart"])
    end = int(shot["frameEnd"])
    review_frames = [
        int(frame)
        for frame in shot.get("reviewFrames", [])
        if isinstance(frame, int) and not isinstance(frame, bool) and start <= frame <= end
    ]
    middle = review_frames[len(review_frames) // 2] if review_frames else start + (end - start) // 2
    return start, middle, end


def _view_basis() -> tuple[Any, Any, Any]:
    from mathutils import Vector  # type: ignore[import-not-found]

    camera = Vector((10.0, -13.0, 5.0))
    forward = (-camera).normalized()
    right = forward.cross(Vector((0.0, 0.0, 1.0))).normalized()
    up = right.cross(forward).normalized()
    return forward, right, up


def _view_point(x: float, z: float, depth: float) -> Any:
    forward, right, up = _view_basis()
    return right * x + up * z + forward * depth


def _face_camera(obj: Any) -> None:
    forward, _right, _up = _view_basis()
    obj.rotation_euler = (-forward).to_track_quat("Z", "Y").to_euler()


def _link(obj: Any, collection: Any, anchor: Any, material: Any | None = None) -> Any:
    for existing in list(obj.users_collection):
        existing.objects.unlink(obj)
    collection.objects.link(obj)
    obj.parent = anchor
    if material is not None and hasattr(obj.data, "materials"):
        obj.data.materials.append(material)
    return obj


def _cube(
    name: str,
    collection: Any,
    anchor: Any,
    environment: str,
    layer: str,
    role: str,
    *,
    x: float,
    z: float,
    depth: float,
    width: float,
    height: float,
    thickness: float,
    material: Any,
    landmark: bool = False,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=_view_point(x, z, depth))
    obj = bpy.context.object
    obj.name = name
    _face_camera(obj)
    obj.scale = (width, height, thickness)
    _link(obj, collection, anchor, material)
    return _tag(obj, environment, layer, role, landmark=landmark)


def _sphere(
    name: str,
    collection: Any,
    anchor: Any,
    environment: str,
    layer: str,
    role: str,
    *,
    x: float,
    z: float,
    depth: float,
    radius: float,
    material: Any,
    landmark: bool = False,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_ico_sphere_add(
        subdivisions=3,
        radius=radius,
        location=_view_point(x, z, depth),
    )
    obj = bpy.context.object
    obj.name = name
    _link(obj, collection, anchor, material)
    return _tag(obj, environment, layer, role, landmark=landmark)


def _torus(
    name: str,
    collection: Any,
    anchor: Any,
    environment: str,
    layer: str,
    role: str,
    *,
    x: float,
    z: float,
    depth: float,
    radius: float,
    thickness: float,
    material: Any,
    landmark: bool = False,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_torus_add(
        major_radius=radius,
        minor_radius=thickness,
        major_segments=64,
        minor_segments=10,
        location=_view_point(x, z, depth),
    )
    obj = bpy.context.object
    obj.name = name
    _face_camera(obj)
    _link(obj, collection, anchor, material)
    return _tag(obj, environment, layer, role, landmark=landmark)


def _beam(
    name: str,
    collection: Any,
    anchor: Any,
    environment: str,
    layer: str,
    role: str,
    *,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    radius: float,
    material: Any,
    landmark: bool = False,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    start_point = _view_point(*start)
    end_point = _view_point(*end)
    direction = end_point - start_point
    midpoint = (start_point + end_point) * 0.5
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=16,
        radius=radius,
        depth=direction.length,
        location=midpoint,
    )
    obj = bpy.context.object
    obj.name = name
    obj.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    _link(obj, collection, anchor, material)
    return _tag(obj, environment, layer, role, landmark=landmark)


def _light(
    name: str,
    collection: Any,
    anchor: Any,
    environment: str,
    *,
    x: float,
    z: float,
    depth: float,
    color: tuple[float, float, float],
    energy: float,
    size: float,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    data = bpy.data.lights.new(name=f"{name}_DATA", type="AREA")
    data.color = color
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.parent = anchor
    obj.location = _view_point(x, z, depth)
    obj.rotation_euler = (-obj.location).to_track_quat("-Z", "Y").to_euler()
    return _tag(obj, environment, "background", "stage-key-light")


def _animate(
    obj: Any,
    data_path: str,
    values: tuple[tuple[int, Any], ...],
    profile: str,
) -> None:
    for frame, value in values:
        setattr(obj, data_path, value)
        obj.keyframe_insert(data_path, frame=frame)
    apply_fcurve_interpolation(obj, data_path, profile)


def _set_visibility(objects: list[Any], visible: bool, frame: int) -> None:
    for obj in objects:
        obj.hide_viewport = not visible
        obj.hide_render = not visible
        obj.keyframe_insert("hide_viewport", frame=frame)
        obj.keyframe_insert("hide_render", frame=frame)


def _constant_visibility(objects: list[Any]) -> None:
    for obj in objects:
        animation = getattr(obj, "animation_data", None)
        action = getattr(animation, "action", None) if animation is not None else None
        if action is None:
            continue
        for fcurve in iter_action_fcurves(action):
            if fcurve.data_path in {"hide_viewport", "hide_render"}:
                for point in fcurve.keyframe_points:
                    point.interpolation = "CONSTANT"


def _shot(shot_plan: dict[str, Any], act_id: str) -> dict[str, Any]:
    return next(shot for shot in shot_plan["shots"] if shot["actId"] == act_id)


def _build_signal(
    shot_plan: dict[str, Any],
    collection: Any,
    anchor: Any,
) -> list[Any]:
    environment = "dead_moon"
    shot = _shot(shot_plan, "signal")
    dark = _material("TP_ENV_MAT_SIGNAL_MOON", (0.022, 0.032, 0.050, 1.0), metallic=0.10, roughness=0.92)
    ruin = _material("TP_ENV_MAT_SIGNAL_RUIN", (0.010, 0.015, 0.024, 1.0), metallic=0.42, roughness=0.65)
    beacon = _material(
        "TP_ENV_MAT_SIGNAL_BEACON",
        (0.32, 0.12, 0.025, 1.0),
        emission=(1.0, 0.32, 0.045, 1.0),
        strength=8.0,
        metallic=0.15,
        roughness=0.22,
    )
    objects: list[Any] = []
    moon = _sphere(
        "TP_ENV_SIGNAL_DEAD_MOON",
        collection,
        anchor,
        environment,
        "background",
        "dominant-dead-moon",
        x=-5.3,
        z=-2.2,
        depth=10.0,
        radius=7.8,
        material=dark,
        landmark=True,
    )
    moon.scale = (1.0, 0.72, 1.0)
    objects.append(moon)
    objects.append(
        _cube(
            "TP_ENV_SIGNAL_REGOLITH_OCCLUDER",
            collection,
            anchor,
            environment,
            "foreground",
            "regolith-occlusion",
            x=-5.5,
            z=-4.4,
            depth=-3.2,
            width=11.0,
            height=3.8,
            thickness=2.0,
            material=ruin,
        )
    )
    for index, (x, z, height) in enumerate(((-7.2, -0.8, 4.5), (-4.8, 0.2, 3.2), (5.8, -1.4, 3.8))):
        objects.append(
            _cube(
                f"TP_ENV_SIGNAL_RUIN_{index + 1:02d}",
                collection,
                anchor,
                environment,
                "foreground" if index < 2 else "midground",
                "dormant-ruin-silhouette",
                x=x,
                z=z,
                depth=-1.8 if index < 2 else 4.0,
                width=0.65,
                height=height,
                thickness=0.8,
                material=ruin,
            )
        )
    spire = _beam(
        "TP_ENV_SIGNAL_BEACON_SPIRE",
        collection,
        anchor,
        environment,
        "background",
        "distant-beacon",
        start=(5.8, -2.1, 8.2),
        end=(5.8, 2.7, 8.2),
        radius=0.16,
        material=beacon,
        landmark=True,
    )
    beam = _beam(
        "TP_ENV_SIGNAL_BEACON_BEAM",
        collection,
        anchor,
        environment,
        "background",
        "beacon-sweep",
        start=(5.8, 2.2, 8.2),
        end=(5.8, 7.8, 10.4),
        radius=0.08,
        material=beacon,
    )
    halo = _torus(
        "TP_ENV_SIGNAL_BEACON_HALO",
        collection,
        anchor,
        environment,
        "background",
        "beacon-signal-ring",
        x=5.8,
        z=2.3,
        depth=8.0,
        radius=0.75,
        thickness=0.06,
        material=beacon,
    )
    objects.extend((spire, beam, halo))
    start, middle, end = story_action_frames(shot)
    _animate(beam, "scale", ((start, (0.35, 0.35, 0.35)), (middle, (1.0, 1.0, 1.0)), (end, (0.72, 0.72, 0.72))), "cinematic_drift")
    _animate(halo, "scale", ((start, (0.5, 0.5, 0.5)), (middle, (1.0, 1.0, 1.0)), (end, (1.18, 1.18, 1.18))), "cinematic_drift")
    objects.append(
        _light(
            "TP_ENV_SIGNAL_KEY",
            collection,
            anchor,
            environment,
            x=5.6,
            z=4.2,
            depth=3.0,
            color=(1.0, 0.24, 0.035),
            energy=520.0,
            size=2.0,
        )
    )
    return objects


def _build_awakening(
    shot_plan: dict[str, Any],
    collection: Any,
    anchor: Any,
) -> list[Any]:
    environment = "signal_ruins"
    shot = _shot(shot_plan, "awakening")
    chamber = _material("TP_ENV_MAT_AWAKENING_CHAMBER", (0.018, 0.080, 0.088, 1.0), metallic=0.72, roughness=0.30)
    edge = _material(
        "TP_ENV_MAT_AWAKENING_EDGE",
        (0.06, 0.32, 0.34, 1.0),
        emission=(0.08, 0.92, 0.82, 1.0),
        strength=2.4,
        metallic=0.48,
        roughness=0.22,
    )
    amber = _material(
        "TP_ENV_MAT_AWAKENING_AMBER",
        (0.34, 0.12, 0.015, 1.0),
        emission=(1.0, 0.42, 0.055, 1.0),
        strength=5.5,
        metallic=0.20,
        roughness=0.25,
    )
    objects: list[Any] = []
    objects.append(
        _cube(
            "TP_ENV_AWAKENING_BACKPLATE",
            collection,
            anchor,
            environment,
            "background",
            "enclosed-chamber-shadow",
            x=0.0,
            z=0.0,
            depth=7.5,
            width=12.0,
            height=9.5,
            thickness=0.8,
            material=chamber,
        )
    )
    start, middle, end = story_action_frames(shot)
    for index in range(10):
        angle = math.tau * index / 10.0
        inner_radius = 2.55
        outer_radius = 4.65
        x, z = math.cos(angle) * inner_radius, math.sin(angle) * inner_radius
        petal = _beam(
            f"TP_ENV_AWAKENING_PETAL_{index + 1:02d}",
            collection,
            anchor,
            environment,
            "foreground" if index in {1, 2, 6, 7} else "midground",
            "opening-chamber-petal",
            start=(x * 0.56, z * 0.56, -0.8 if index in {1, 2, 6, 7} else 2.0),
            end=(x * 1.22, z * 1.22, -0.8 if index in {1, 2, 6, 7} else 2.0),
            radius=0.28,
            material=chamber if index % 2 else edge,
            landmark=index == 0,
        )
        closed = petal.location.copy()
        outward = _view_basis()[1] * (math.cos(angle) * (outer_radius - inner_radius))
        outward += _view_basis()[2] * (math.sin(angle) * (outer_radius - inner_radius))
        _animate(
            petal,
            "location",
            ((start, closed), (middle, closed + outward * 0.68), (end, closed + outward)),
            "weightless_float",
        )
        objects.append(petal)
    iris = _torus(
        "TP_ENV_AWAKENING_IRIS",
        collection,
        anchor,
        environment,
        "background",
        "dominant-opening-iris",
        x=0.0,
        z=0.0,
        depth=3.0,
        radius=4.25,
        thickness=0.22,
        material=edge,
        landmark=True,
    )
    objects.append(iris)
    for index, radius in enumerate((2.8, 3.5, 4.2)):
        band = _torus(
            f"TP_ENV_AWAKENING_REACTIVATION_{index + 1:02d}",
            collection,
            anchor,
            environment,
            "background",
            "reactivation-band",
            x=0.0,
            z=0.0,
            depth=3.3 + index * 0.15,
            radius=radius,
            thickness=0.07,
            material=amber,
        )
        _animate(
            band,
            "scale",
            (
                (start, (0.38, 0.38, 0.38)),
                (middle, (0.78 + index * 0.08,) * 3),
                (end, (1.0 + index * 0.06,) * 3),
            ),
            "weightless_float",
        )
        objects.append(band)
    objects.append(
        _light(
            "TP_ENV_AWAKENING_KEY",
            collection,
            anchor,
            environment,
            x=-1.0,
            z=-4.0,
            depth=-0.5,
            color=(1.0, 0.31, 0.045),
            energy=780.0,
            size=4.0,
        )
    )
    return objects


def _build_departure(
    shot_plan: dict[str, Any],
    collection: Any,
    anchor: Any,
) -> list[Any]:
    environment = "launch_structure"
    shot = _shot(shot_plan, "departure")
    structure = _material("TP_ENV_MAT_DEPARTURE_STRUCTURE", (0.012, 0.026, 0.12, 1.0), metallic=0.82, roughness=0.28)
    guide = _material(
        "TP_ENV_MAT_DEPARTURE_GUIDE",
        (0.08, 0.34, 0.78, 1.0),
        emission=(0.34, 0.78, 1.0, 1.0),
        strength=4.2,
        metallic=0.50,
        roughness=0.18,
    )
    hot = _material(
        "TP_ENV_MAT_DEPARTURE_HOT",
        (0.6, 0.72, 0.86, 1.0),
        emission=(0.84, 0.95, 1.0, 1.0),
        strength=7.0,
        metallic=0.10,
        roughness=0.18,
    )
    objects: list[Any] = []
    depths = (-4.5, -0.5, 3.5, 7.5, 11.5)
    for index, depth in enumerate(depths):
        layer = "foreground" if index == 0 else "midground" if index < 3 else "background"
        width = 6.7
        for suffix, start, end in (
            ("LEFT", (-width, -3.8, depth), (-width, 4.6, depth)),
            ("RIGHT", (width, -3.8, depth), (width, 4.6, depth)),
            ("TOP", (-width, 4.6, depth), (width, 4.6, depth)),
        ):
            objects.append(
                _beam(
                    f"TP_ENV_DEPARTURE_RIB_{index + 1:02d}_{suffix}",
                    collection,
                    anchor,
                    environment,
                    layer,
                    "monumental-corridor-rib",
                    start=start,
                    end=end,
                    radius=0.24 if index == 0 else 0.17,
                    material=structure if index % 2 else guide,
                    landmark=index == 2 and suffix == "TOP",
                )
            )
    for index, x in enumerate((-3.2, -1.2, 1.2, 3.2)):
        objects.append(
            _beam(
                f"TP_ENV_DEPARTURE_RAIL_{index + 1:02d}",
                collection,
                anchor,
                environment,
                "foreground" if abs(x) > 2 else "midground",
                "converging-guide-rail",
                start=(x, -3.6, -5.0),
                end=(x * 0.35, -1.1, 14.0),
                radius=0.08,
                material=guide,
                landmark=index == 0,
            )
        )
    start, middle, end = story_action_frames(shot)
    for index, (x, z) in enumerate(((-2.4, -2.8), (0.0, 3.4), (2.4, -2.8))):
        packet = _sphere(
            f"TP_ENV_DEPARTURE_PACKET_{index + 1:02d}",
            collection,
            anchor,
            environment,
            "midground",
            "sequential-guide-packet",
            x=x,
            z=z,
            depth=-2.5 + index * 2.0,
            radius=0.18,
            material=hot,
        )
        first = _view_point(x, z, -2.5 + index * 2.0)
        second = _view_point(x * 0.65, z * 0.65, 5.0 + index * 1.5)
        third = _view_point(x * 0.25, z * 0.25, 13.0)
        _animate(packet, "location", ((start, first), (middle, second), (end, third)), "slow_acceleration")
        objects.append(packet)
    corridor_anchor = _tag(
        anchor,
        environment,
        "midground",
        "dominant-monumental-corridor",
        landmark=True,
    )
    corridor_anchor["trackprompt_landmark_id"] = "TP_ENV_DEPARTURE_CORRIDOR"
    objects.append(
        _light(
            "TP_ENV_DEPARTURE_KEY",
            collection,
            anchor,
            environment,
            x=0.0,
            z=2.0,
            depth=10.0,
            color=(0.18, 0.46, 1.0),
            energy=920.0,
            size=5.0,
        )
    )
    return objects


def _build_gate(
    shot_plan: dict[str, Any],
    collection: Any,
    anchor: Any,
) -> list[Any]:
    environment = "gate_corridor"
    shot = _shot(shot_plan, "gates")
    monolith = _material("TP_ENV_MAT_GATE_MONOLITH", (0.006, 0.008, 0.018, 1.0), metallic=0.72, roughness=0.34)
    edge = _material(
        "TP_ENV_MAT_GATE_EDGE",
        (0.02, 0.30, 0.20, 1.0),
        emission=(0.12, 1.0, 0.58, 1.0),
        strength=7.0,
        metallic=0.25,
        roughness=0.17,
    )
    membrane = _material(
        "TP_ENV_MAT_GATE_MEMBRANE",
        (0.025, 0.11, 0.18, 1.0),
        emission=(0.20, 0.92, 0.70, 1.0),
        strength=2.0,
        metallic=0.05,
        roughness=0.48,
    )
    objects: list[Any] = []
    for side, x in (("LEFT", -7.2), ("RIGHT", 7.2)):
        objects.append(
            _cube(
                f"TP_ENV_GATE_FOREGROUND_{side}",
                collection,
                anchor,
                environment,
                "foreground",
                "asymmetric-gate-occluder",
                x=x,
                z=0.0 if side == "LEFT" else 1.0,
                depth=-3.5,
                width=4.0,
                height=13.0,
                thickness=2.2,
                material=monolith,
            )
        )
    left = _cube(
        "TP_ENV_GATE_MONOLITH_LEFT",
        collection,
        anchor,
        environment,
        "midground",
        "split-monolith",
        x=-5.7,
        z=0.0,
        depth=3.0,
        width=2.8,
        height=11.5,
        thickness=1.6,
        material=monolith,
        landmark=True,
    )
    right = _cube(
        "TP_ENV_GATE_MONOLITH_RIGHT",
        collection,
        anchor,
        environment,
        "midground",
        "split-monolith",
        x=5.7,
        z=0.0,
        depth=3.0,
        width=2.8,
        height=11.5,
        thickness=1.6,
        material=monolith,
        landmark=True,
    )
    objects.extend((left, right))
    diamond_points = ((0.0, 5.6, 3.5), (5.0, 0.0, 3.5), (0.0, -5.6, 3.5), (-5.0, 0.0, 3.5))
    for index, (start_point, end_point) in enumerate(zip(diamond_points, diamond_points[1:] + diamond_points[:1], strict=True)):
        objects.append(
            _beam(
                f"TP_ENV_GATE_DIAMOND_{index + 1:02d}",
                collection,
                anchor,
                environment,
                "background",
                "dominant-diamond-threshold",
                start=start_point,
                end=end_point,
                radius=0.22,
                material=edge,
                landmark=True,
            )
        )
    portal = _sphere(
        "TP_ENV_GATE_MEMBRANE",
        collection,
        anchor,
        environment,
        "background",
        "threshold-membrane",
        x=0.0,
        z=0.0,
        depth=6.5,
        radius=4.2,
        material=membrane,
    )
    forward, _right_axis, _up_axis = _view_basis()
    portal.scale = (1.0, 0.12, 1.0)
    portal.rotation_euler = forward.to_track_quat("Y", "Z").to_euler()
    objects.append(portal)
    shockwave = _torus(
        "TP_ENV_GATE_SHOCKWAVE",
        collection,
        anchor,
        environment,
        "midground",
        "environmental-reaction",
        x=0.0,
        z=0.0,
        depth=1.0,
        radius=4.4,
        thickness=0.12,
        material=edge,
    )
    objects.append(shockwave)
    start, middle, end = story_action_frames(shot)
    left_open, right_open = left.location.copy(), right.location.copy()
    right_axis = _view_basis()[1]
    _animate(left, "location", ((start, left_open), (middle, left_open), (end, left_open + right_axis * 1.35)), "controlled_chase")
    _animate(right, "location", ((start, right_open), (middle, right_open), (end, right_open - right_axis * 1.35)), "controlled_chase")
    _animate(
        shockwave,
        "scale",
        ((start, (0.12, 0.12, 0.12)), (middle, (0.42, 0.42, 0.42)), (end, (1.45, 1.45, 1.45))),
        "controlled_chase",
    )
    _animate(
        portal,
        "scale",
        ((start, (0.78, 0.10, 0.78)), (middle, (1.0, 0.12, 1.0)), (end, (0.52, 0.08, 0.52))),
        "controlled_chase",
    )
    objects.append(
        _light(
            "TP_ENV_GATE_KEY",
            collection,
            anchor,
            environment,
            x=0.0,
            z=0.0,
            depth=5.5,
            color=(0.08, 1.0, 0.46),
            energy=1250.0,
            size=6.0,
        )
    )
    return objects


def _build_future_landmark(
    environment: str,
    collection: Any,
    anchor: Any,
    *,
    index: int,
) -> list[Any]:
    spec = STAGE_VISUAL_SPECS[environment]
    colors = (
        ((0.20, 0.012, 0.018, 1.0), (1.0, 0.08, 0.04, 1.0)),
        ((0.12, 0.018, 0.24, 1.0), (0.74, 0.24, 1.0, 1.0)),
        ((0.025, 0.12, 0.20, 1.0), (0.48, 0.82, 1.0, 1.0)),
    )
    base, emission = colors[index]
    material = _material(
        f"TP_ENV_MAT_FUTURE_{index + 1:02d}",
        base,
        emission=emission,
        strength=3.0,
        metallic=0.45,
        roughness=0.30,
    )
    objects = [
        _torus(
            spec.landmark,
            collection,
            anchor,
            environment,
            "background",
            spec.secondary_action,
            x=0.0,
            z=0.0,
            depth=5.0,
            radius=4.5 + index,
            thickness=0.18,
            material=material,
            landmark=True,
        ),
        _cube(
            f"{spec.landmark}_FOREGROUND",
            collection,
            anchor,
            environment,
            "foreground",
            "future-stage-occluder",
            x=-6.0 + index * 6.0,
            z=-2.0,
            depth=-2.0,
            width=3.0,
            height=8.0,
            thickness=1.4,
            material=material,
        ),
        _sphere(
            f"{spec.landmark}_MIDGROUND",
            collection,
            anchor,
            environment,
            "midground",
            "future-stage-secondary-action",
            x=3.0 - index * 2.0,
            z=1.5,
            depth=1.5,
            radius=0.45,
            material=material,
        ),
    ]
    return objects


def build_narrative_environments(
    shot_plan: dict[str, Any],
    collection: Any,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    anchors: dict[str, Any] = {}
    objects_by_environment: dict[str, list[Any]] = {}
    for index, name in enumerate(ENVIRONMENTS):
        anchor = bpy.data.objects.new(f"TP_ENV_{name.upper()}", None)
        collection.objects.link(anchor)
        anchor["trackprompt_environment"] = name
        anchor["trackprompt_environment_index"] = index
        spec = STAGE_VISUAL_SPECS[name]
        anchor["trackprompt_dominant_shape"] = spec.dominant_shape
        anchor["trackprompt_lighting_identity"] = spec.lighting_identity
        anchor["trackprompt_secondary_action"] = spec.secondary_action
        anchors[name] = anchor

    objects_by_environment["dead_moon"] = _build_signal(shot_plan, collection, anchors["dead_moon"])
    objects_by_environment["signal_ruins"] = _build_awakening(shot_plan, collection, anchors["signal_ruins"])
    objects_by_environment["launch_structure"] = _build_departure(shot_plan, collection, anchors["launch_structure"])
    objects_by_environment["gate_corridor"] = _build_gate(shot_plan, collection, anchors["gate_corridor"])
    for index, name in enumerate(ENVIRONMENTS[4:]):
        objects_by_environment[name] = _build_future_landmark(
            name,
            collection,
            anchors[name],
            index=index,
        )

    for name, anchor in anchors.items():
        objects_by_environment[name].append(anchor)
    for shot in shot_plan["shots"]:
        active = str(shot["environment"]["environment"])
        frame = int(shot["frameStart"])
        for name, objects in objects_by_environment.items():
            _set_visibility(objects, name == active, frame)
    for objects in objects_by_environment.values():
        _constant_visibility(objects)

    stages: list[dict[str, Any]] = []
    for name in ENVIRONMENTS:
        spec = STAGE_VISUAL_SPECS[name]
        renderables = [obj for obj in objects_by_environment[name] if getattr(obj, "type", "EMPTY") != "EMPTY"]
        layer_counts = {
            layer: sum(1 for obj in renderables if obj.get("trackprompt_depth_layer") == layer)
            for layer in DEPTH_LAYERS
        }
        stages.append(
            {
                "environment": name,
                "landmark": spec.landmark,
                "dominantShape": spec.dominant_shape,
                "lightingIdentity": spec.lighting_identity,
                "secondaryAction": spec.secondary_action,
                "objectCount": len(renderables),
                "layerCounts": layer_counts,
            }
        )
    return {
        "environmentCount": len(anchors),
        "anchors": [anchor.name for anchor in anchors.values()],
        "renderableObjectCount": sum(stage["objectCount"] for stage in stages),
        "stages": stages,
    }

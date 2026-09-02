from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .curve_importer import iter_action_fcurves
from .geometry import add_property_driver
from .materials import create_material
from .motion import apply_fcurve_interpolation

R12_REVISION_ID = "andromeda-r12-continuous-slice"
R12_PROOF_FRAME_START = 127
R12_PROOF_FRAME_END = 655
R12_REQUIRED_SHOTS: tuple[tuple[str, str, int, int], ...] = (
    ("r12-shot-02-awakening-question", "awakening", 127, 171),
    ("r12-shot-03-awakening-release", "awakening", 172, 277),
    ("r12-shot-04-departure-rear-follow", "departure", 278, 334),
    ("r12-shot-05-departure-side-track", "departure", 335, 394),
    ("r12-shot-06-departure-occluded", "departure", 395, 454),
    ("r12-shot-07-gate-approach", "gates", 455, 531),
    ("r12-shot-08-gate-crossing", "gates", 532, 588),
    ("r12-shot-09-gate-seal", "gates", 589, 655),
)
R12_ENVIRONMENTS: tuple[str, ...] = (
    "signal_ruins",
    "launch_structure",
    "gate_corridor",
)
R12_LAYOUTS: tuple[str, ...] = ("landscape", "vertical")

_STAGE_BY_ACT = {
    "awakening": "signal_ruins",
    "departure": "launch_structure",
    "gates": "gate_corridor",
}
_STAGE_ANCHORS = {
    "signal_ruins": "TP_ENV_SIGNAL_RUINS",
    "launch_structure": "TP_ENV_LAUNCH_STRUCTURE",
    "gate_corridor": "TP_ENV_GATE_CORRIDOR",
}
_LIGHTING_IDENTITIES = {
    "signal_ruins": "weathered-teal-chamber-amber-slit",
    "launch_structure": "dark-metal-corridor-cobalt-insets",
    "gate_corridor": "black-stone-layered-emerald-threshold",
}
_DOMINANT_SHAPES = {
    "signal_ruins": "weathered chamber shell opening around the vessel",
    "launch_structure": "asymmetric monumental corridor receding in world space",
    "gate_corridor": "thick split monolith threshold with layered membrane depth",
}
_SECONDARY_ACTIONS = {
    "signal_ruins": "hinged chamber slabs release and expose the travel axis",
    "launch_structure": "restrained guide packets chase the accelerating vessel",
    "gate_corridor": "the membrane compresses, a shockwave releases, and the route seals",
}


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_r12_shot_plan(shot_plan: Mapping[str, Any]) -> bool:
    shots = shot_plan.get("shots")
    return isinstance(shots, list) and any(
        isinstance(shot, Mapping)
        and isinstance(shot.get("id"), str)
        and str(shot["id"]).startswith("r12-")
        for shot in shots
    )


def build_r12_schedule(shot_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and expose the deterministic R12 proof range without Blender."""

    shots = shot_plan.get("shots")
    if not isinstance(shots, list):
        raise ValueError("R12 requires a shot plan with shots.")
    by_id = {
        str(shot["id"]): shot
        for shot in shots
        if isinstance(shot, Mapping) and isinstance(shot.get("id"), str)
    }
    phases: list[dict[str, Any]] = []
    for identifier, act_id, expected_start, expected_end in R12_REQUIRED_SHOTS:
        shot = by_id.get(identifier)
        if shot is None:
            raise ValueError(f"R12 shot plan is missing {identifier}.")
        start = shot.get("frameStart")
        end = shot.get("frameEnd")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start != expected_start
            or end != expected_end
            or str(shot.get("actId")) != act_id
        ):
            raise ValueError(f"R12 shot {identifier} does not match its frozen range or act.")
        phases.append(
            {
                "shotId": identifier,
                "role": identifier.removeprefix("r12-shot-"),
                "actId": act_id,
                "environment": _STAGE_BY_ACT[act_id],
                "frameStart": start,
                "frameEnd": end,
                "durationFrames": end - start + 1,
            }
        )
    for previous, current in zip(phases, phases[1:], strict=False):
        if int(current["frameStart"]) != int(previous["frameEnd"]) + 1:
            raise ValueError("R12 proof phases must form one continuous range.")
    fps_value = shot_plan.get("fps", 30.0)
    if isinstance(fps_value, bool) or not isinstance(fps_value, int | float):
        raise ValueError("R12 shot-plan FPS is invalid.")
    fps = float(fps_value)
    if not math.isfinite(fps) or fps <= 0.0:
        raise ValueError("R12 shot-plan FPS is invalid.")
    schedule = {
        "schemaVersion": "1.0.0",
        "revisionId": R12_REVISION_ID,
        "previewOnly": True,
        "sourceShotPlan": {
            "schemaVersion": shot_plan.get("schemaVersion"),
            "inputDigest": shot_plan.get("inputDigest"),
            "seed": shot_plan.get("seed"),
        },
        "continuousRange": {
            "frameStart": R12_PROOF_FRAME_START,
            "frameEnd": R12_PROOF_FRAME_END,
            "durationFrames": R12_PROOF_FRAME_END - R12_PROOF_FRAME_START + 1,
            "durationSeconds": (R12_PROOF_FRAME_END - R12_PROOF_FRAME_START + 1) / fps,
            "fps": fps,
        },
        "phases": phases,
        "layouts": [r12_layout_state(name) for name in R12_LAYOUTS],
    }
    schedule["canonicalSha256"] = _canonical_sha256(schedule)
    return schedule


def r12_layout_state(layout: str) -> dict[str, Any]:
    if layout not in R12_LAYOUTS:
        raise ValueError("R12 layout must be landscape or vertical.")
    landscape = layout == "landscape"
    stage_transforms = {
        "signal_ruins": {
            "location": (0.0, 0.0, 0.0 if landscape else 0.7),
            "scale": (1.0, 1.0, 1.0) if landscape else (0.78, 1.0, 1.12),
        },
        "launch_structure": {
            "location": (0.0, 0.0, 0.0 if landscape else 0.35),
            "scale": (1.0, 1.0, 1.0) if landscape else (0.70, 1.0, 1.10),
        },
        "gate_corridor": {
            "location": (0.0, 0.0, 0.0 if landscape else 0.8),
            "scale": (1.0, 1.0, 1.0) if landscape else (0.72, 1.0, 1.15),
        },
    }
    state: dict[str, Any] = {
        "layout": layout,
        "width": 1920 if landscape else 1080,
        "height": 1080 if landscape else 1920,
        "phoneWidth": 320 if landscape else 180,
        "phoneHeight": 180 if landscape else 320,
        "sensorFit": "HORIZONTAL" if landscape else "VERTICAL",
        "camera": "TP_CAMERA" if landscape else "TP_R12_CAMERA_VERTICAL",
        "cameraRoot": (
            "TP_STORY_CAMERA_ROOT" if landscape else "TP_R12_CAMERA_ROOT_VERTICAL"
        ),
        "cameraMicro": (
            "TP_STORY_CAMERA_MICRO" if landscape else "TP_R12_CAMERA_MICRO_VERTICAL"
        ),
        "cameraTarget": (
            "TP_CAMERA_TARGET" if landscape else "TP_R12_CAMERA_TARGET_VERTICAL"
        ),
        "responsiveAnchor": f"TP_R12_LAYOUT_{layout.upper()}",
        "stageTransforms": stage_transforms,
    }
    state["canonicalSha256"] = _canonical_sha256(state)
    return state


def planned_r12_protagonist_keyframes(layout: str) -> list[dict[str, Any]]:
    if layout not in R12_LAYOUTS:
        raise ValueError("R12 layout must be landscape or vertical.")
    vertical = layout == "vertical"
    lateral = 0.45 if vertical else 1.0
    lift = 0.55 if vertical else 0.0
    raw = (
        (127, (-0.62, -4.8, 0.24), (0.08, 0.04, 0.28), (1.06, 1.00, 1.06), (0.12, 0.10, 0.12), 0.025, "question"),
        (149, (-0.48, -4.65, 0.34), (0.10, 0.08, 0.48), (1.08, 0.97, 1.08), (0.15, 0.16, 0.15), 0.040, "orientation"),
        (171, (-0.32, -4.55, 0.42), (0.06, 0.12, 0.62), (1.03, 0.94, 1.03), (0.16, 0.12, 0.16), 0.055, "anticipation"),
        (220, (-0.18, -2.10, 0.38), (0.02, 0.10, 0.92), (1.00, 1.00, 1.00), (0.20, 0.42, 0.20), 0.070, "release"),
        (277, (0.00, 3.00, 0.30), (0.00, 0.08, 1.22), (0.98, 1.04, 0.98), (0.24, 0.80, 0.24), 0.060, "departure"),
        (334, (-0.65, 12.0, 0.55), (0.02, 0.16, 1.36), (0.96, 1.08, 0.96), (0.30, 1.35, 0.30), 0.045, "rear-follow"),
        (394, (1.75, 22.8, 0.82), (0.10, 0.28, 1.54), (0.94, 1.10, 0.94), (0.34, 1.75, 0.34), 0.055, "side-track"),
        (454, (0.55, 33.0, 0.42), (-0.04, 0.12, 1.42), (0.96, 1.06, 0.96), (0.30, 1.48, 0.30), 0.045, "occluded-track"),
        (500, (0.24, 38.6, 0.34), (0.00, 0.06, 1.50), (1.00, 1.00, 1.00), (0.25, 1.02, 0.25), 0.060, "gate-deceleration"),
        (531, (0.12, 40.2, 0.26), (0.00, 0.03, 1.56), (1.04, 0.92, 1.04), (0.20, 0.58, 0.20), 0.085, "threshold-anticipation"),
        (560, (0.02, 44.1, 0.18), (0.04, -0.05, 1.64), (1.14, 0.58, 1.14), (0.15, 0.34, 0.15), -0.110, "threshold-compression"),
        (588, (0.55, 50.2, 0.62), (0.26, 0.38, 2.02), (0.88, 1.24, 0.88), (0.32, 2.10, 0.32), 0.135, "crossing-reaction"),
        (620, (1.25, 54.1, 1.08), (0.20, 0.24, 2.34), (0.96, 1.08, 0.96), (0.27, 1.42, 0.27), 0.070, "seal-reaction"),
        (655, (1.55, 56.2, 1.18), (0.08, 0.12, 2.48), (1.00, 1.00, 1.00), (0.20, 0.72, 0.20), 0.035, "settle"),
    )
    return [
        {
            "frame": frame,
            "location": (location[0] * lateral, location[1], location[2] + lift),
            "rotation": rotation,
            "scale": scale,
            "wakeScale": wake,
            "deformation": deformation,
            "role": role,
        }
        for frame, location, rotation, scale, wake, deformation, role in raw
    ]


def planned_r12_camera_keyframes(layout: str) -> list[dict[str, Any]]:
    if layout not in R12_LAYOUTS:
        raise ValueError("R12 layout must be landscape or vertical.")
    landscape = layout == "landscape"
    if landscape:
        raw = (
            (127, (-2.3, -15.0, 1.6), (-0.55, -4.65, 0.28), 70.0, "extreme-close-up"),
            (171, (-1.7, -14.5, 1.8), (-0.35, -4.50, 0.40), 58.0, "close-orientation"),
            (220, (9.8, -20.5, 7.8), (-0.10, -2.6, 0.35), 28.0, "wide-chamber-reveal"),
            (277, (0.8, -11.5, 3.2), (0.0, 3.4, 0.35), 30.0, "rear-follow-entry"),
            (334, (-1.2, -3.5, 3.0), (-0.5, 13.4, 0.55), 32.0, "rear-follow"),
            (394, (16.0, 17.0, 5.0), (1.45, 23.2, 0.80), 42.0, "side-track"),
            (454, (12.0, 22.0, 2.5), (0.45, 33.2, 0.42), 36.0, "foreground-obstructed"),
            (531, (5.0, 24.0, -2.0), (0.05, 43.7, 2.4), 24.0, "low-gate-approach"),
            (560, (0.4, 34.0, 1.6), (0.05, 48.5, 1.55), 22.0, "threshold-pov"),
            (588, (-2.0, 46.0, 3.0), (0.52, 52.0, 0.65), 28.0, "crossing-reaction"),
            (620, (0.0, 38.0, 5.0), (1.0, 54.0, 1.0), 34.0, "post-crossing-reaction"),
            (655, (2.0, 32.0, 8.0), (1.5, 56.0, 1.2), 40.0, "scale-pullback"),
        )
    else:
        raw = (
            (127, (-1.2, -20.0, 3.2), (-0.22, -4.65, 0.95), 60.0, "extreme-close-up"),
            (171, (-0.8, -19.0, 3.1), (-0.15, -4.50, 1.05), 52.0, "close-orientation"),
            (220, (5.5, -21.5, 9.0), (-0.05, -2.5, 1.15), 34.0, "wide-chamber-reveal"),
            (277, (0.5, -12.5, 4.5), (0.0, 3.5, 1.0), 34.0, "rear-follow-entry"),
            (334, (-0.5, -3.5, 4.6), (-0.22, 13.2, 1.2), 36.0, "rear-follow"),
            (394, (11.5, 16.5, 6.0), (0.60, 23.0, 1.35), 44.0, "side-track"),
            (454, (9.0, 21.5, 4.2), (0.25, 33.0, 1.10), 40.0, "foreground-obstructed"),
            (531, (4.0, 23.0, -1.0), (0.0, 43.8, 3.3), 28.0, "low-gate-approach"),
            (560, (0.2, 33.0, 2.4), (0.0, 48.0, 2.35), 26.0, "threshold-pov"),
            (588, (-1.0, 46.0, 4.0), (0.22, 52.0, 1.45), 32.0, "crossing-reaction"),
            (620, (0.0, 37.0, 7.0), (0.6, 54.0, 1.5), 36.0, "post-crossing-reaction"),
            (655, (0.0, 30.0, 10.0), (0.7, 56.0, 1.75), 42.0, "scale-pullback"),
        )
    return [
        {"frame": frame, "location": location, "target": target, "lensMm": lens, "role": role}
        for frame, location, target, lens, role in raw
    ]


def r12_art_contract() -> dict[str, Any]:
    contract = {
        "revisionId": R12_REVISION_ID,
        "environments": list(R12_ENVIRONMENTS),
        "worldSpaceTravelAxis": "+Y",
        "maximumEmissiveStrength": 2.0,
        "maximumLuminousRenderableFraction": 0.25,
        "maximumVolumeDensity": 0.025,
        "maximumVolumeCountPerStage": 1,
        "maximumNewRenderables": 150,
        "maximumNewTriangles": 250_000,
        "renderSamples": 32,
        "viewExposure": -0.65,
        "humanApprovalRequired": True,
    }
    contract["canonicalSha256"] = _canonical_sha256(contract)
    return contract


def _tag(
    obj: Any,
    environment: str,
    layer: str,
    role: str,
    *,
    landmark: bool = False,
    physical: bool = True,
    emissive: bool = False,
    volume: bool = False,
) -> Any:
    obj["trackprompt_revision"] = "r12"
    obj["trackprompt_environment"] = environment
    obj["trackprompt_depth_layer"] = layer
    obj["trackprompt_narrative_role"] = role
    obj["trackprompt_landmark"] = bool(landmark)
    obj["trackprompt_space"] = "world"
    obj["trackprompt_physical_surface"] = bool(physical)
    obj["trackprompt_emissive_accent"] = bool(emissive)
    obj["trackprompt_volume"] = bool(volume)
    return obj


def _principled(material: Any) -> Any:
    return material.node_tree.nodes.get(material.get("tp_principled_node", "Principled BSDF"))


def _physical_material(
    name: str,
    base: tuple[float, float, float, float],
    *,
    metallic: float,
    roughness: float,
    noise_scale: float,
    bump_strength: float,
) -> Any:
    material = create_material(
        name,
        base,
        metallic=metallic,
        roughness=roughness,
        emission_strength=0.0,
    )
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = _principled(material)
    if principled is None:
        raise RuntimeError("R12 physical material has no Principled node.")
    texcoord = nodes.new("ShaderNodeTexCoord")
    texcoord.name = f"{name}_COORDINATES"
    noise = nodes.new("ShaderNodeTexNoise")
    noise.name = f"{name}_WEATHERING"
    noise.inputs["Scale"].default_value = noise_scale
    noise.inputs["Detail"].default_value = 3.0
    noise.inputs["Roughness"].default_value = 0.68
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.name = f"{name}_ROUGHNESS_VARIATION"
    lower = max(0.0, roughness - 0.16)
    upper = min(1.0, roughness + 0.16)
    ramp.color_ramp.elements[0].color = (lower, lower, lower, 1.0)
    ramp.color_ramp.elements[1].color = (upper, upper, upper, 1.0)
    bump = nodes.new("ShaderNodeBump")
    bump.name = f"{name}_SURFACE_RELIEF"
    bump.inputs["Strength"].default_value = bump_strength
    bump.inputs["Distance"].default_value = 0.12
    links.new(texcoord.outputs["Generated"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    rough_socket = principled.inputs.get("Roughness")
    normal_socket = principled.inputs.get("Normal")
    if rough_socket is not None:
        links.new(ramp.outputs["Color"], rough_socket)
    if normal_socket is not None:
        links.new(bump.outputs["Normal"], normal_socket)
    material["trackprompt_revision"] = "r12"
    material["trackprompt_surface_family"] = "weathered-physical"
    material["trackprompt_emissive_strength"] = 0.0
    material["trackprompt_roughness_nominal"] = roughness
    material["trackprompt_bump_strength"] = bump_strength
    return material


def _accent_material(
    name: str,
    base: tuple[float, float, float, float],
    emission: tuple[float, float, float, float],
    strength: float,
    *,
    alpha: float = 1.0,
) -> Any:
    if not 0.0 <= strength <= 2.0:
        raise ValueError("R12 emissive accents must remain within 0..2.")
    material = create_material(
        name,
        base,
        metallic=0.22,
        roughness=0.48,
        emission_color=emission,
        emission_strength=strength,
    )
    principled = _principled(material)
    if principled is not None:
        alpha_socket = principled.inputs.get("Alpha")
        if alpha_socket is not None:
            alpha_socket.default_value = alpha
    if alpha < 1.0 and hasattr(material, "surface_render_method"):
        try:
            material.surface_render_method = "DITHERED"
        except (TypeError, ValueError):
            pass
    material.diffuse_color = (*base[:3], alpha)
    material["trackprompt_revision"] = "r12"
    material["trackprompt_surface_family"] = "restrained-emissive-accent"
    material["trackprompt_emissive_strength"] = strength
    return material


def _volume_material(
    name: str,
    color: tuple[float, float, float, float],
    density: float,
    anisotropy: float,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    if not 0.0 < density <= 0.025 or not 0.0 <= anisotropy <= 0.35:
        raise ValueError("R12 volume settings exceed the bounded preview contract.")
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    volume = nodes.new("ShaderNodeVolumePrincipled")
    volume.inputs["Color"].default_value = color
    volume.inputs["Density"].default_value = density
    volume.inputs["Anisotropy"].default_value = anisotropy
    links.new(volume.outputs["Volume"], output.inputs["Volume"])
    material["trackprompt_revision"] = "r12"
    material["trackprompt_surface_family"] = "bounded-atmospheric-volume"
    material["trackprompt_volume_density"] = density
    return material


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
    location: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    material: Any,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    bevel: float = 0.14,
    landmark: bool = False,
    emissive: bool = False,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.scale = tuple(value * 0.5 for value in dimensions)
    _link(obj, collection, anchor, material)
    if bevel > 0.0:
        modifier = obj.modifiers.new(f"{name}_BEVEL", "BEVEL")
        modifier.width = min(bevel, min(dimensions) * 0.10)
        modifier.segments = 2
    return _tag(obj, environment, layer, role, landmark=landmark, emissive=emissive)


def _torus(
    name: str,
    collection: Any,
    anchor: Any,
    environment: str,
    layer: str,
    role: str,
    *,
    location: tuple[float, float, float],
    radius: float,
    thickness: float,
    material: Any,
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    landmark: bool = False,
    emissive: bool = False,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_torus_add(
        major_radius=radius,
        minor_radius=thickness,
        major_segments=48,
        minor_segments=8,
        location=location,
        rotation=(math.pi / 2.0, 0.0, 0.0),
    )
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    _link(obj, collection, anchor, material)
    return _tag(obj, environment, layer, role, landmark=landmark, emissive=emissive)


def _sphere(
    name: str,
    collection: Any,
    anchor: Any,
    environment: str,
    layer: str,
    role: str,
    *,
    location: tuple[float, float, float],
    radius: float,
    material: Any,
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    landmark: bool = False,
    emissive: bool = False,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=radius, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    _link(obj, collection, anchor, material)
    return _tag(obj, environment, layer, role, landmark=landmark, emissive=emissive)


def _volume_box(
    name: str,
    collection: Any,
    anchor: Any,
    environment: str,
    *,
    location: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    material: Any,
) -> Any:
    obj = _cube(
        name,
        collection,
        anchor,
        environment,
        "background",
        "bounded-atmospheric-depth",
        location=location,
        dimensions=dimensions,
        material=material,
        bevel=0.0,
    )
    obj["trackprompt_physical_surface"] = False
    obj["trackprompt_volume"] = True
    return obj


def _point_light(
    name: str,
    collection: Any,
    anchor: Any,
    environment: str,
    *,
    location: tuple[float, float, float],
    color: tuple[float, float, float],
    energy: float,
    radius: float,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    data = bpy.data.lights.new(f"{name}_DATA", type="POINT")
    data.color = color
    data.energy = energy
    data.shadow_soft_size = radius
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.parent = anchor
    obj.location = location
    return _tag(obj, environment, "midground", "bounded-local-light", physical=False)


def _animate(
    obj: Any,
    data_path: str,
    values: Sequence[tuple[int, Any]],
    profile: str,
) -> None:
    for frame, value in values:
        setattr(obj, data_path, value)
        obj.keyframe_insert(data_path, frame=frame)
    apply_fcurve_interpolation(obj, data_path, profile)


def _constant_visibility(objects: Sequence[Any]) -> None:
    for obj in objects:
        animation = getattr(obj, "animation_data", None)
        action = getattr(animation, "action", None) if animation is not None else None
        if action is None:
            continue
        for fcurve in iter_action_fcurves(action):
            if fcurve.data_path in {"hide_viewport", "hide_render"}:
                for point in fcurve.keyframe_points:
                    point.interpolation = "CONSTANT"


def _build_awakening(collection: Any, anchor: Any, materials: Mapping[str, Any]) -> list[Any]:
    environment = "signal_ruins"
    stone = materials["stone"]
    metal = materials["metal"]
    amber = materials["amber"]
    volume = materials["awakening_volume"]
    objects: list[Any] = []
    objects.extend(
        (
            _cube(
                "TP_R12_AWAKENING_BACK_WALL",
                collection,
                anchor,
                environment,
                "background",
                "weathered-chamber-back-wall",
                location=(0.0, 0.8, 0.8),
                dimensions=(12.5, 1.5, 11.0),
                material=stone,
            ),
            _cube(
                "TP_R12_AWAKENING_NEAR_LEFT",
                collection,
                anchor,
                environment,
                "foreground",
                "near-chamber-architecture",
                location=(-5.5, -7.2, 1.0),
                dimensions=(3.2, 3.0, 11.0),
                rotation=(0.0, 0.18, -0.10),
                material=stone,
            ),
            _cube(
                "TP_R12_AWAKENING_NEAR_TOP",
                collection,
                anchor,
                environment,
                "foreground",
                "near-chamber-architecture",
                location=(1.0, -6.6, 5.7),
                dimensions=(11.5, 2.4, 2.1),
                rotation=(0.0, 0.0, 0.08),
                material=metal,
            ),
            _torus(
                "TP_R12_AWAKENING_APERTURE",
                collection,
                anchor,
                environment,
                "background",
                "restrained-reactivation-aperture",
                location=(0.0, -0.2, 0.4),
                radius=4.15,
                thickness=0.16,
                material=amber,
                scale=(1.0, 1.0, 1.12),
                landmark=True,
                emissive=True,
            ),
        )
    )
    for index in range(10):
        angle = math.tau * index / 10.0
        radial = 3.05
        opened = 5.15
        closed_location = (math.cos(angle) * radial, -2.0, math.sin(angle) * radial + 0.4)
        open_location = (math.cos(angle) * opened, -1.15, math.sin(angle) * opened + 0.4)
        panel = _cube(
            f"TP_R12_AWAKENING_HINGED_SLAB_{index + 1:02d}",
            collection,
            anchor,
            environment,
            "foreground" if index in {1, 2, 7, 8} else "midground",
            "hinged-weathered-chamber-slab",
            location=closed_location,
            dimensions=(1.15, 2.2, 3.7),
            rotation=(0.0, angle, 0.0),
            material=metal if index % 2 else stone,
            landmark=index == 0,
        )
        _animate(
            panel,
            "location",
            ((127, closed_location), (171, closed_location), (220, open_location), (277, open_location)),
            "weightless_float",
        )
        _animate(
            panel,
            "rotation_euler",
            (
                (127, (0.0, angle, 0.0)),
                (171, (0.0, angle, 0.0)),
                (220, (0.22 * math.sin(angle), angle + 0.28, 0.12 * math.cos(angle))),
                (277, (0.35 * math.sin(angle), angle + 0.52, 0.20 * math.cos(angle))),
            ),
            "weightless_float",
        )
        objects.append(panel)
    objects.append(
        _volume_box(
            "TP_R12_AWAKENING_VOLUME",
            collection,
            anchor,
            environment,
            location=(0.0, -2.8, 0.7),
            dimensions=(12.0, 13.0, 10.0),
            material=volume,
        )
    )
    objects.append(
        _point_light(
            "TP_R12_AWAKENING_AMBER_LIGHT",
            collection,
            anchor,
            environment,
            location=(-1.2, -2.3, -2.2),
            color=(1.0, 0.30, 0.045),
            energy=720.0,
            radius=3.5,
        )
    )
    objects.append(
        _point_light(
            "TP_R12_AWAKENING_TEAL_FILL",
            collection,
            anchor,
            environment,
            location=(4.2, -3.0, 3.8),
            color=(0.06, 0.34, 0.38),
            energy=520.0,
            radius=4.5,
        )
    )
    objects.append(
        _point_light(
            "TP_R12_AWAKENING_CHAMBER_FILL",
            collection,
            anchor,
            environment,
            location=(0.8, -1.6, 4.2),
            color=(0.18, 0.30, 0.34),
            energy=820.0,
            radius=5.0,
        )
    )
    return objects


def _build_departure(collection: Any, anchor: Any, materials: Mapping[str, Any]) -> list[Any]:
    environment = "launch_structure"
    metal = materials["metal"]
    stone = materials["stone"]
    cobalt = materials["cobalt"]
    volume = materials["departure_volume"]
    objects: list[Any] = []
    objects.append(
        _cube(
            "TP_R12_DEPARTURE_FLOOR",
            collection,
            anchor,
            environment,
            "midground",
            "weathered-travel-deck",
            location=(0.0, 20.0, -2.35),
            dimensions=(12.0, 36.0, 0.9),
            material=stone,
            landmark=True,
        )
    )
    for index, y in enumerate((4.5, 10.0, 16.0, 22.5, 29.0, 35.0)):
        layer = "foreground" if index < 2 else "midground" if index < 4 else "background"
        lateral = 5.2 + (0.45 if index % 2 else -0.15)
        height = 6.7 + index * 0.18
        for side, x in (("LEFT", -lateral), ("RIGHT", lateral)):
            objects.append(
                _cube(
                    f"TP_R12_DEPARTURE_PYLON_{index + 1:02d}_{side}",
                    collection,
                    anchor,
                    environment,
                    layer,
                    "monumental-weathered-pylon",
                    location=(x, y, 1.0 + (0.35 if side == "RIGHT" else 0.0)),
                    dimensions=(1.55 + index * 0.05, 1.9, height),
                    rotation=(0.02 * index, 0.0, 0.04 * (-1 if side == "LEFT" else 1)),
                    material=metal if index % 2 else stone,
                    landmark=index == 3 and side == "LEFT",
                )
            )
        objects.append(
            _cube(
                f"TP_R12_DEPARTURE_CEILING_{index + 1:02d}",
                collection,
                anchor,
                environment,
                layer,
                "asymmetric-corridor-crossbeam",
                location=(0.45 * (-1 if index % 2 else 1), y, 5.35 + index * 0.10),
                dimensions=(11.8, 1.75, 1.15),
                rotation=(0.0, 0.025 * (-1 if index % 2 else 1), 0.0),
                material=stone,
            )
        )
    for index, x in enumerate((-3.35, 3.05)):
        objects.append(
            _cube(
                f"TP_R12_DEPARTURE_INSET_GUIDE_{index + 1:02d}",
                collection,
                anchor,
                environment,
                "midground",
                "restrained-inset-guide",
                location=(x, 20.0, -1.82),
                dimensions=(0.22, 32.0, 0.08),
                material=cobalt,
                bevel=0.02,
                emissive=True,
            )
        )
    for index, x in enumerate((-2.8, 0.2, 2.5)):
        packet = _sphere(
            f"TP_R12_DEPARTURE_GUIDE_PACKET_{index + 1:02d}",
            collection,
            anchor,
            environment,
            "midground",
            "authored-guide-packet",
            location=(x, 7.0 + index * 2.0, -1.55),
            radius=0.13,
            material=cobalt,
            emissive=True,
        )
        _animate(
            packet,
            "location",
            (
                (278, (x, 7.0 + index * 2.0, -1.55)),
                (334, (x * 0.75, 16.0 + index * 2.5, -1.50)),
                (394, (x * 0.35, 27.0 + index * 2.0, -1.42)),
                (454, (x * 0.10, 36.0, -1.35)),
            ),
            "slow_acceleration",
        )
        objects.append(packet)
    objects.append(
        _volume_box(
            "TP_R12_DEPARTURE_VOLUME",
            collection,
            anchor,
            environment,
            location=(0.0, 20.0, 1.4),
            dimensions=(11.5, 36.0, 9.5),
            material=volume,
        )
    )
    objects.append(
        _point_light(
            "TP_R12_DEPARTURE_DEPTH_LIGHT",
            collection,
            anchor,
            environment,
            location=(0.0, 34.0, 2.8),
            color=(0.12, 0.32, 0.88),
            energy=1050.0,
            radius=5.0,
        )
    )
    objects.append(
        _point_light(
            "TP_R12_DEPARTURE_NEAR_FILL",
            collection,
            anchor,
            environment,
            location=(-2.5, 18.0, 3.8),
            color=(0.18, 0.24, 0.52),
            energy=860.0,
            radius=5.5,
        )
    )
    objects.append(
        _point_light(
            "TP_R12_DEPARTURE_ENTRY_FILL",
            collection,
            anchor,
            environment,
            location=(3.0, 6.5, 3.2),
            color=(0.12, 0.26, 0.62),
            energy=620.0,
            radius=4.8,
        )
    )
    return objects


def _build_gate(collection: Any, anchor: Any, materials: Mapping[str, Any]) -> list[Any]:
    environment = "gate_corridor"
    stone = materials["gate_stone"]
    metal = materials["metal"]
    emerald = materials["emerald"]
    membrane_materials = materials["membranes"]
    volume = materials["gate_volume"]
    objects: list[Any] = []
    objects.extend(
        (
            _cube(
                "TP_R12_GATE_APPROACH_DECK",
                collection,
                anchor,
                environment,
                "foreground",
                "weathered-gate-approach-deck",
                location=(0.0, 40.0, -3.55),
                dimensions=(13.0, 9.0, 0.85),
                material=stone,
            ),
            _cube(
                "TP_R12_GATE_NEAR_OCCLUDER_LEFT",
                collection,
                anchor,
                environment,
                "foreground",
                "near-gate-parallax-occluder",
                location=(-7.6, 37.2, 1.2),
                dimensions=(3.4, 3.2, 12.5),
                rotation=(0.02, 0.10, -0.08),
                material=stone,
            ),
            _cube(
                "TP_R12_GATE_NEAR_OCCLUDER_RIGHT",
                collection,
                anchor,
                environment,
                "foreground",
                "near-gate-parallax-occluder",
                location=(7.3, 38.4, 2.0),
                dimensions=(2.8, 2.7, 10.5),
                rotation=(-0.03, -0.08, 0.10),
                material=metal,
            ),
        )
    )
    for index, y in enumerate((42.0, 44.3, 46.4)):
        lateral = 6.0 - index * 0.55
        depth = 2.2 - index * 0.25
        for side, x in (("LEFT", -lateral), ("RIGHT", lateral)):
            objects.append(
                _cube(
                    f"TP_R12_GATE_FRAME_{index + 1:02d}_{side}",
                    collection,
                    anchor,
                    environment,
                    "foreground" if index == 0 else "midground",
                    "thick-dark-gate-monolith",
                    location=(x, y, 1.2 + index * 0.25),
                    dimensions=(3.25 - index * 0.35, depth, 13.5 - index * 1.1),
                    rotation=(0.0, 0.08 * (-1 if side == "LEFT" else 1), 0.0),
                    material=stone if index != 1 else metal,
                    landmark=index == 1,
                )
            )
        objects.extend(
            (
                _cube(
                    f"TP_R12_GATE_FRAME_{index + 1:02d}_TOP",
                    collection,
                    anchor,
                    environment,
                    "midground",
                    "thick-gate-lintel",
                    location=(0.0, y, 6.1 - index * 0.15),
                    dimensions=(10.8 - index * 0.8, depth, 1.55),
                    material=stone,
                ),
                _cube(
                    f"TP_R12_GATE_FRAME_{index + 1:02d}_BASE",
                    collection,
                    anchor,
                    environment,
                    "midground",
                    "thick-gate-foundation",
                    location=(0.0, y, -3.65 + index * 0.10),
                    dimensions=(10.8 - index * 0.8, depth, 1.25),
                    material=stone,
                ),
            )
        )
    for index, (y, material, scale) in enumerate(
        zip((43.0, 44.2, 45.4), membrane_materials, ((3.5, 0.16, 4.45), (3.25, 0.14, 4.20), (3.0, 0.12, 3.95)), strict=True)
    ):
        membrane = _sphere(
            f"TP_R12_GATE_MEMBRANE_LAYER_{index + 1:02d}",
            collection,
            anchor,
            environment,
            "background",
            "layered-threshold-membrane",
            location=(0.0, y, 1.0),
            radius=1.0,
            material=material,
            scale=scale,
            landmark=index == 1,
            emissive=True,
        )
        _animate(
            membrane,
            "scale",
            (
                (455, scale),
                (531, scale),
                (560, (scale[0] * 0.76, scale[1] * 1.8, scale[2] * 1.08)),
                (588, (scale[0] * 1.05, scale[1], scale[2] * 0.96)),
                (620, (0.16, scale[1] * 0.7, scale[2] * 0.88)),
                (655, (0.08, scale[1] * 0.6, scale[2] * 0.82)),
            ),
            "controlled_chase",
        )
        objects.append(membrane)
    beyond = _sphere(
        "TP_R12_GATE_SPACE_BEYOND",
        collection,
        anchor,
        environment,
        "background",
        "readable-altered-space-beyond",
        location=(0.0, 56.5, 2.5),
        radius=4.8,
        material=materials["beyond"],
        scale=(1.55, 0.35, 0.82),
        landmark=True,
        emissive=True,
    )
    _animate(
        beyond,
        "scale",
        (
            (455, (1.55, 0.35, 0.82)),
            (588, (1.55, 0.35, 0.82)),
            (606, (0.45, 0.20, 0.36)),
            (620, (0.12, 0.10, 0.16)),
            (655, (0.04, 0.05, 0.08)),
        ),
        "controlled_chase",
    )
    objects.append(beyond)
    locks: list[Any] = []
    for side, x in (("LEFT", -4.1), ("RIGHT", 4.1)):
        lock = _cube(
            f"TP_R12_GATE_LOCK_{side}",
            collection,
            anchor,
            environment,
            "midground",
            "mechanical-route-lock",
            location=(x, 41.0, 0.8),
            dimensions=(1.25, 3.4, 8.8),
            rotation=(0.0, 0.0, 0.12 * (-1 if side == "LEFT" else 1)),
            material=metal,
        )
        closed_x = -2.0 if side == "LEFT" else 2.0
        _animate(
            lock,
            "location",
            ((455, (x, 41.0, 0.8)), (588, (x, 41.0, 0.8)), (620, (closed_x, 41.0, 0.8)), (655, (closed_x, 41.0, 0.8))),
            "controlled_chase",
        )
        locks.append(lock)
    objects.extend(locks)
    shockwave = _torus(
        "TP_R12_GATE_CROSSING_SHOCKWAVE",
        collection,
        anchor,
        environment,
        "midground",
        "crossing-shockwave",
        location=(0.0, 44.0, 1.0),
        radius=4.0,
        thickness=0.10,
        material=emerald,
        scale=(0.05, 0.05, 0.05),
        emissive=True,
    )
    _animate(
        shockwave,
        "scale",
        (
            (455, (0.05, 0.05, 0.05)),
            (531, (0.05, 0.05, 0.05)),
            (560, (0.28, 0.28, 0.28)),
            (588, (1.40, 1.40, 1.40)),
            (606, (2.00, 2.00, 2.00)),
            (620, (0.40, 0.40, 0.40)),
            (655, (0.05, 0.05, 0.05)),
        ),
        "controlled_chase",
    )
    objects.append(shockwave)
    seam = _cube(
        "TP_R12_GATE_SEALED_SEAM",
        collection,
        anchor,
        environment,
        "background",
        "persistent-sealed-route-consequence",
        location=(0.0, 40.8, 0.9),
        dimensions=(0.18, 0.32, 8.2),
        material=emerald,
        bevel=0.04,
        emissive=True,
        landmark=True,
    )
    _animate(
        seam,
        "scale",
        ((455, (0.02, 0.02, 0.02)), (588, (0.02, 0.02, 0.02)), (620, (1.0, 1.0, 1.0)), (655, (1.0, 1.0, 1.0))),
        "controlled_chase",
    )
    objects.append(seam)
    objects.append(
        _volume_box(
            "TP_R12_GATE_VOLUME",
            collection,
            anchor,
            environment,
            location=(0.0, 45.0, 1.2),
            dimensions=(13.5, 12.0, 13.0),
            material=volume,
        )
    )
    objects.append(
        _point_light(
            "TP_R12_GATE_CONTROLLED_KEY",
            collection,
            anchor,
            environment,
            location=(0.0, 47.0, 2.0),
            color=(0.08, 0.75, 0.40),
            energy=1120.0,
            radius=4.5,
        )
    )
    objects.append(
        _point_light(
            "TP_R12_GATE_APPROACH_FILL",
            collection,
            anchor,
            environment,
            location=(-3.8, 38.0, 3.6),
            color=(0.10, 0.25, 0.58),
            energy=720.0,
            radius=5.2,
        )
    )
    return objects


def _create_materials() -> dict[str, Any]:
    return {
        "stone": _physical_material(
            "TP_R12_MAT_WEATHERED_STONE",
            (0.060, 0.078, 0.082, 1.0),
            metallic=0.16,
            roughness=0.78,
            noise_scale=3.2,
            bump_strength=0.22,
        ),
        "metal": _physical_material(
            "TP_R12_MAT_DARK_METAL",
            (0.050, 0.078, 0.090, 1.0),
            metallic=0.68,
            roughness=0.56,
            noise_scale=7.0,
            bump_strength=0.14,
        ),
        "gate_stone": _physical_material(
            "TP_R12_MAT_GATE_BLACK_STONE",
            (0.020, 0.028, 0.042, 1.0),
            metallic=0.24,
            roughness=0.72,
            noise_scale=2.4,
            bump_strength=0.25,
        ),
        "amber": _accent_material(
            "TP_R12_MAT_AMBER_INSET",
            (0.16, 0.055, 0.008, 1.0),
            (1.0, 0.28, 0.025, 1.0),
            0.78,
        ),
        "cobalt": _accent_material(
            "TP_R12_MAT_COBALT_INSET",
            (0.012, 0.055, 0.16, 1.0),
            (0.12, 0.42, 1.0, 1.0),
            0.82,
        ),
        "emerald": _accent_material(
            "TP_R12_MAT_EMERALD_SEAM",
            (0.008, 0.10, 0.055, 1.0),
            (0.08, 0.72, 0.36, 1.0),
            1.05,
        ),
        "membranes": (
            _accent_material(
                "TP_R12_MAT_MEMBRANE_OUTER",
                (0.008, 0.035, 0.050, 1.0),
                (0.04, 0.34, 0.26, 1.0),
                0.45,
                alpha=0.10,
            ),
            _accent_material(
                "TP_R12_MAT_MEMBRANE_MIDDLE",
                (0.010, 0.045, 0.062, 1.0),
                (0.08, 0.52, 0.34, 1.0),
                0.70,
                alpha=0.075,
            ),
            _accent_material(
                "TP_R12_MAT_MEMBRANE_INNER",
                (0.008, 0.030, 0.044, 1.0),
                (0.06, 0.42, 0.30, 1.0),
                0.55,
                alpha=0.055,
            ),
        ),
        "beyond": _accent_material(
            "TP_R12_MAT_SPACE_BEYOND",
            (0.010, 0.025, 0.075, 1.0),
            (0.08, 0.20, 0.52, 1.0),
            0.38,
        ),
        "wake": _accent_material(
            "TP_R12_MAT_VESSEL_WAKE",
            (0.006, 0.025, 0.042, 1.0),
            (0.06, 0.44, 0.56, 1.0),
            0.62,
            alpha=0.16,
        ),
        "awakening_volume": _volume_material(
            "TP_R12_MAT_AWAKENING_VOLUME", (0.02, 0.12, 0.12, 1.0), 0.012, 0.20
        ),
        "departure_volume": _volume_material(
            "TP_R12_MAT_DEPARTURE_VOLUME", (0.008, 0.025, 0.12, 1.0), 0.009, 0.28
        ),
        "gate_volume": _volume_material(
            "TP_R12_MAT_GATE_VOLUME", (0.008, 0.055, 0.040, 1.0), 0.014, 0.32
        ),
    }


def _new_empty(name: str, collection: Any, parent: Any | None = None) -> Any:
    import bpy  # type: ignore[import-not-found]

    obj = bpy.data.objects.new(name, None)
    collection.objects.link(obj)
    obj.parent = parent
    return obj


def _author_visibility(
    shot_plan: Mapping[str, Any],
    objects_by_environment: Mapping[str, Sequence[Any]],
) -> None:
    shots = shot_plan.get("shots")
    if not isinstance(shots, list):
        raise ValueError("R12 visibility authoring requires shot-plan shots.")
    for shot in shots:
        if not isinstance(shot, Mapping):
            continue
        identifier = str(shot.get("id", ""))
        frame = shot.get("frameStart")
        if isinstance(frame, bool) or not isinstance(frame, int):
            continue
        active_environment = next(
            (
                environment
                for shot_id, act_id, _start, _end in R12_REQUIRED_SHOTS
                if shot_id == identifier
                for environment in (_STAGE_BY_ACT[act_id],)
            ),
            None,
        )
        for environment, objects in objects_by_environment.items():
            visible = environment == active_environment
            for obj in objects:
                obj.hide_viewport = not visible
                obj.hide_render = not visible
                obj.keyframe_insert("hide_viewport", frame=frame)
                obj.keyframe_insert("hide_render", frame=frame)
    for objects in objects_by_environment.values():
        _constant_visibility(objects)


def _hide_v1_ornament() -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    prefixes = (
        "TP_SPACE_ORBIT_",
        "TP_SPACE_TRAVEL",
        "TP_SPACE_DEBRIS",
        "TP_SPACE_ORBITAL_DUST",
        "TP_SPACE_VOCAL",
        "TP_SPACE_REVELATION",
        "TP_SPACE_NEBULA",
        "TP_SPACE_CORE_FILAMENT",
    )
    exact = {
        "TP_SPACE_CORE_LATTICE",
        "TP_SPACE_CORE_ATMOSPHERE",
    }
    hidden: list[str] = []
    for obj in bpy.data.objects:
        if obj.name in exact or obj.name.startswith(prefixes):
            obj.hide_viewport = True
            obj.hide_render = True
            obj["trackprompt_r12_suppressed_ornament"] = True
            hidden.append(obj.name)
    return {
        "policy": "r12-physical-sets-and-authored-wake-only",
        "suppressedObjectCount": len(hidden),
    }


def _author_protagonist_plan(root: Any, wake: Any, layout: str) -> int:
    root.animation_data_clear()
    wake.animation_data_clear()
    plan = planned_r12_protagonist_keyframes(layout)
    for state in plan:
        frame = int(state["frame"])
        root.location = state["location"]
        root.rotation_euler = state["rotation"]
        root.scale = state["scale"]
        root.keyframe_insert("location", frame=frame)
        root.keyframe_insert("rotation_euler", frame=frame)
        root.keyframe_insert("scale", frame=frame)
        wake.scale = state["wakeScale"]
        wake.keyframe_insert("scale", frame=frame)
    for owner, paths in ((root, ("location", "rotation_euler", "scale")), (wake, ("scale",))):
        for path in paths:
            apply_fcurve_interpolation(owner, path, "controlled_chase")
    return len(plan)


def _build_protagonist(
    collection: Any,
    destination_macro: Any,
    shell: Any,
    wake_material: Any,
    shot_plan: Mapping[str, Any],
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    action = _new_empty("TP_STORY_PROTAGONIST_ACTION", collection)
    action["trackprompt_revision"] = "r12"
    action["trackprompt_protagonist"] = True
    action["trackprompt_motion_layer"] = "authored_protagonist_root"
    action["trackprompt_major_motion"] = "authored-no-audio-driver"
    destination_macro.parent = action
    wake = _new_empty("TP_R12_PROTAGONIST_WAKE", collection, action)
    wake["trackprompt_revision"] = "r12"
    wake["trackprompt_protagonist_wake"] = True
    wake["trackprompt_motion_layer"] = "authored-protagonist-wake"
    for index, (radius, length, alpha_scale) in enumerate(
        ((0.78, 7.5, 1.0), (0.52, 5.6, 0.75), (0.30, 3.8, 0.52)),
        start=1,
    ):
        bpy.ops.mesh.primitive_cone_add(
            vertices=24,
            radius1=0.10,
            radius2=radius,
            depth=length,
            location=(0.0, -length * 0.54, 0.0),
            rotation=(math.pi / 2.0, 0.0, 0.0),
        )
        trail = bpy.context.object
        trail.name = f"TP_R12_PROTAGONIST_WAKE_LAYER_{index:02d}"
        for existing in list(trail.users_collection):
            existing.objects.unlink(trail)
        collection.objects.link(trail)
        trail.parent = wake
        trail.data.materials.append(wake_material)
        trail["trackprompt_revision"] = "r12"
        trail["trackprompt_protagonist_wake"] = True
        trail["trackprompt_wake_alpha_scale"] = alpha_scale
    displacement = shell.modifiers.get("TP_R12_AUTHORED_SHELL_RESPONSE")
    if displacement is None:
        texture = bpy.data.textures.new("TP_R12_AUTHORED_SHELL_TEXTURE", type="CLOUDS")
        texture.noise_scale = 0.22
        texture.noise_depth = 2
        displacement = shell.modifiers.new("TP_R12_AUTHORED_SHELL_RESPONSE", "DISPLACE")
        displacement.texture = texture
    shell["trackprompt_revision"] = "r12"
    shell["trackprompt_authored_shell_response"] = displacement.name
    for state in planned_r12_protagonist_keyframes("landscape"):
        displacement.strength = float(state["deformation"])
        shell.keyframe_insert(
            data_path=f'modifiers["{displacement.name}"].strength',
            frame=int(state["frame"]),
        )
    apply_fcurve_interpolation(
        shell,
        f'modifiers["{displacement.name}"].strength',
        "controlled_chase",
    )
    state_indices = {"awakened": 2, "travelling": 3}
    for shot in shot_plan.get("shots", []):
        if not isinstance(shot, Mapping) or not str(shot.get("id", "")).startswith("r12-"):
            continue
        state = str(shot.get("protagonistState", "travelling"))
        shell["protagonist_state_index"] = state_indices.get(state, 3)
        shell.keyframe_insert(
            data_path='["protagonist_state_index"]',
            frame=int(shot["frameStart"]),
        )
    key_count = _author_protagonist_plan(action, wake, "landscape")
    return {
        "object": shell.name,
        "actionRoot": action.name,
        "wakeRoot": wake.name,
        "wakeLayerCount": 3,
        "authoredKeyCount": key_count,
        "shellResponseModifier": displacement.name,
        "orientationAuthored": True,
        "accelerationAuthored": True,
        "anticipationAuthored": True,
        "thresholdCompressionAuthored": True,
        "postCrossingReactionAuthored": True,
    }


def _camera_rig(
    layout: str,
    camera: Any,
    target: Any,
    bus: Any,
    collection: Any,
) -> dict[str, Any]:
    root_name = "TP_STORY_CAMERA_ROOT" if layout == "landscape" else "TP_R12_CAMERA_ROOT_VERTICAL"
    micro_name = "TP_STORY_CAMERA_MICRO" if layout == "landscape" else "TP_R12_CAMERA_MICRO_VERTICAL"
    root = _new_empty(root_name, collection)
    micro = _new_empty(micro_name, collection, root)
    root["trackprompt_revision"] = "r12"
    root["trackprompt_layout"] = layout
    root["trackprompt_motion_layer"] = "planned_camera_root"
    root["trackprompt_major_motion"] = "authored-no-audio-driver"
    micro["trackprompt_revision"] = "r12"
    micro["trackprompt_layout"] = layout
    micro["trackprompt_motion_layer"] = "micro_audio_response"
    micro["trackprompt_micro_audio_layer"] = True
    camera.parent = micro
    camera.location = (0.0, 0.0, 0.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera.animation_data_clear()
    camera.data.animation_data_clear()
    target.animation_data_clear()
    for constraint in list(camera.constraints):
        camera.constraints.remove(constraint)
    constraint = camera.constraints.new("TRACK_TO")
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"
    camera.data.sensor_fit = r12_layout_state(layout)["sensorFit"]
    camera.data.clip_start = 0.05
    camera.data.clip_end = 500.0
    plan = planned_r12_camera_keyframes(layout)
    for state in plan:
        frame = int(state["frame"])
        root.location = state["location"]
        root.keyframe_insert("location", frame=frame)
        target.location = state["target"]
        target.keyframe_insert("location", frame=frame)
        camera.data.lens = float(state["lensMm"])
        camera.data.keyframe_insert("lens", frame=frame)
    apply_fcurve_interpolation(root, "location", "controlled_chase")
    apply_fcurve_interpolation(target, "location", "controlled_chase")
    apply_fcurve_interpolation(camera.data, "lens", "controlled_chase")
    add_property_driver(
        micro,
        "location",
        2,
        bus,
        {"v": "master_energy"},
        "(v - 0.5) * 0.04",
    )
    return {
        "layout": layout,
        "root": root.name,
        "micro": micro.name,
        "target": target.name,
        "camera": camera.name,
        "plannedKeyCount": len(plan),
        "authoredPath": True,
        "rawAudioMajorMotion": False,
    }


def _build_cameras(camera: Any, target: Any, bus: Any, collection: Any) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    vertical_data = camera.data.copy()
    vertical_data.name = "TP_R12_CAMERA_DATA_VERTICAL"
    vertical_camera = bpy.data.objects.new("TP_R12_CAMERA_VERTICAL", vertical_data)
    collection.objects.link(vertical_camera)
    vertical_target = _new_empty("TP_R12_CAMERA_TARGET_VERTICAL", collection)
    landscape = _camera_rig("landscape", camera, target, bus, collection)
    vertical = _camera_rig("vertical", vertical_camera, vertical_target, bus, collection)
    bpy.context.scene.camera = camera
    return {
        "root": landscape["root"],
        "micro": landscape["micro"],
        "target": landscape["target"],
        "plannedKeyCount": landscape["plannedKeyCount"] + vertical["plannedKeyCount"],
        "authoredPath": True,
        "layouts": {"landscape": landscape, "vertical": vertical},
    }


def _configure_exposure() -> None:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    scene.view_settings.exposure = 0.15
    scene.view_settings.gamma = 1.0
    scene["trackprompt_r12_render_samples"] = 32
    scene["trackprompt_r12_max_volume_density"] = 0.025
    scene["trackprompt_r12_exposure_controlled"] = True
    tree = getattr(scene, "node_tree", None)
    if tree is None:
        tree = getattr(scene, "compositing_node_group", None)
    glare = tree.nodes.get("TP_CONTROLLED_GLOW") if tree is not None else None
    if glare is not None:
        threshold = glare.inputs.get("Threshold") if hasattr(glare, "inputs") else None
        strength = glare.inputs.get("Strength") if hasattr(glare, "inputs") else None
        if threshold is not None:
            threshold.default_value = 1.6
        elif hasattr(glare, "threshold"):
            glare.threshold = 1.6
        if strength is not None:
            strength.default_value = 0.28
        elif hasattr(glare, "mix"):
            glare.mix = -0.72


def apply_r12_layout(layout: str) -> dict[str, Any]:
    """Select one embedded authored camera and responsively recompose the R12 set."""

    import bpy  # type: ignore[import-not-found]

    state = r12_layout_state(layout)
    scene = bpy.context.scene
    raw_schedule = scene.get("trackprompt_r12_schedule")
    if not isinstance(raw_schedule, str):
        raise RuntimeError("The active scene has no identity-bound R12 schedule.")
    schedule = json.loads(raw_schedule)
    if schedule.get("revisionId") != R12_REVISION_ID:
        raise RuntimeError("The active scene R12 schedule is invalid.")
    camera = bpy.data.objects.get(str(state["camera"]))
    action = bpy.data.objects.get("TP_STORY_PROTAGONIST_ACTION")
    wake = bpy.data.objects.get("TP_R12_PROTAGONIST_WAKE")
    responsive_anchor = bpy.data.objects.get(str(state["responsiveAnchor"]))
    if camera is None or action is None or wake is None or responsive_anchor is None:
        raise RuntimeError("The active scene is missing an embedded R12 layout component.")
    stage_transforms = state["stageTransforms"]
    for environment, anchor_name in _STAGE_ANCHORS.items():
        anchor = bpy.data.objects.get(anchor_name)
        if anchor is None:
            raise RuntimeError("The active scene is missing an R12 stage anchor.")
        transform = stage_transforms[environment]
        anchor.parent = responsive_anchor
        anchor.location = transform["location"]
        anchor.scale = transform["scale"]
        anchor["trackprompt_r12_active_layout"] = layout
    _author_protagonist_plan(action, wake, layout)
    scene.camera = camera
    scene.render.resolution_x = int(state["width"])
    scene.render.resolution_y = int(state["height"])
    scene.render.resolution_percentage = 100
    scene["trackprompt_r12_layout"] = layout
    scene["trackprompt_r12_layout_state_sha256"] = state["canonicalSha256"]
    return {
        "ok": True,
        "revisionId": R12_REVISION_ID,
        "layout": layout,
        "width": state["width"],
        "height": state["height"],
        "phoneWidth": state["phoneWidth"],
        "phoneHeight": state["phoneHeight"],
        "camera": state["camera"],
        "cameraRoot": state["cameraRoot"],
        "cameraMicro": state["cameraMicro"],
        "cameraTarget": state["cameraTarget"],
        "responsiveAnchor": state["responsiveAnchor"],
        "continuousRange": dict(schedule["continuousRange"]),
        "layoutStateSha256": state["canonicalSha256"],
    }


def build_r12_story_slice(
    shot_plan: Mapping[str, Any],
    narrative_collection: Any,
    protagonist_collection: Any,
    camera_collection: Any,
    bus: Any,
    camera: Any,
    target: Any,
    destination_macro: Any,
    shell: Any,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    schedule = build_r12_schedule(shot_plan)
    materials = _create_materials()
    layout_anchors: dict[str, Any] = {}
    for layout in R12_LAYOUTS:
        anchor = _new_empty(f"TP_R12_LAYOUT_{layout.upper()}", narrative_collection)
        anchor["trackprompt_revision"] = "r12"
        anchor["trackprompt_responsive_layout_anchor"] = layout
        layout_anchors[layout] = anchor
    anchors: dict[str, Any] = {}
    for environment in R12_ENVIRONMENTS:
        anchor = _new_empty(
            _STAGE_ANCHORS[environment], narrative_collection, layout_anchors["landscape"]
        )
        anchor["trackprompt_revision"] = "r12"
        anchor["trackprompt_environment"] = environment
        anchor["trackprompt_dominant_shape"] = _DOMINANT_SHAPES[environment]
        anchor["trackprompt_lighting_identity"] = _LIGHTING_IDENTITIES[environment]
        anchor["trackprompt_secondary_action"] = _SECONDARY_ACTIONS[environment]
        anchor["trackprompt_responsive"] = True
        anchors[environment] = anchor
    objects_by_environment = {
        "signal_ruins": _build_awakening(
            narrative_collection, anchors["signal_ruins"], materials
        ),
        "launch_structure": _build_departure(
            narrative_collection, anchors["launch_structure"], materials
        ),
        "gate_corridor": _build_gate(
            narrative_collection, anchors["gate_corridor"], materials
        ),
    }
    _author_visibility(shot_plan, objects_by_environment)
    protagonist = _build_protagonist(
        protagonist_collection,
        destination_macro,
        shell,
        materials["wake"],
        shot_plan,
    )
    cameras = _build_cameras(camera, target, bus, camera_collection)
    _configure_exposure()
    scene = bpy.context.scene
    scene["trackprompt_r12_revision"] = R12_REVISION_ID
    scene["trackprompt_r12_schedule"] = json.dumps(
        schedule,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    scene["trackprompt_r12_art_contract"] = json.dumps(
        r12_art_contract(),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    scene["trackprompt_r12_protagonist_action"] = protagonist["actionRoot"]
    scene["trackprompt_r12_camera_landscape"] = cameras["layouts"]["landscape"]["camera"]
    scene["trackprompt_r12_camera_vertical"] = cameras["layouts"]["vertical"]["camera"]
    scene["trackprompt_r12_camera_target_landscape"] = cameras["layouts"]["landscape"]["target"]
    scene["trackprompt_r12_camera_target_vertical"] = cameras["layouts"]["vertical"]["target"]
    scene["trackprompt_r12_human_approval_status"] = "pending"
    baseline_visibility = _hide_v1_ornament()
    layout_result = apply_r12_layout("landscape")
    stages: list[dict[str, Any]] = []
    renderable_count = 0
    for environment in R12_ENVIRONMENTS:
        renderables = [
            obj
            for obj in objects_by_environment[environment]
            if getattr(obj, "type", "EMPTY") not in {"EMPTY", "LIGHT"}
        ]
        renderable_count += len(renderables)
        stages.append(
            {
                "environment": environment,
                "landmark": _STAGE_ANCHORS[environment],
                "dominantShape": _DOMINANT_SHAPES[environment],
                "lightingIdentity": _LIGHTING_IDENTITIES[environment],
                "secondaryAction": _SECONDARY_ACTIONS[environment],
                "objectCount": len(renderables),
                "layerCounts": {
                    layer: sum(
                        1
                        for obj in renderables
                        if obj.get("trackprompt_depth_layer") == layer
                    )
                    for layer in ("foreground", "midground", "background")
                },
                "physicalSurfaceCount": sum(
                    bool(obj.get("trackprompt_physical_surface")) for obj in renderables
                ),
                "emissiveAccentCount": sum(
                    bool(obj.get("trackprompt_emissive_accent")) for obj in renderables
                ),
                "volumeCount": sum(bool(obj.get("trackprompt_volume")) for obj in renderables),
            }
        )
    return {
        "revisionId": R12_REVISION_ID,
        "schedule": schedule,
        "layout": layout_result,
        "protagonist": protagonist,
        "baselineVisibility": baseline_visibility,
        "environments": {
            "environmentCount": len(R12_ENVIRONMENTS),
            "anchors": [anchors[name].name for name in R12_ENVIRONMENTS],
            "responsiveLayoutAnchors": [anchor.name for anchor in layout_anchors.values()],
            "renderableObjectCount": renderable_count,
            "futureLandmarksBuilt": False,
            "stages": stages,
        },
        "storyCameraRig": cameras,
        "artContract": r12_art_contract(),
    }

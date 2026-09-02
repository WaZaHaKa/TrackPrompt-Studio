from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .materials import create_material


R13_REVISION_ID = "andromeda-r13-lookdev-lock"
R13_ROOT_COLLECTION = "TP_R13_LOOKDEV"
R13_CAMERA_NAME = "TP_R13_CAMERA"
R13_RENDER_WIDTH = 1080
R13_RENDER_HEIGHT = 1920
R13_PHONE_WIDTH = 180
R13_PHONE_HEIGHT = 320

R13_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "id": "protagonist-a-directional-shell",
        "kind": "protagonist",
        "label": "Directional shell",
        "visibleCollections": ["TP_R13_HERO_A"],
        "cameraRigId": "hero-controlled-comparison",
        "lightingRigId": "hero-controlled-comparison",
        "cameraLocation": (9.0, -8.0, 5.2),
        "cameraTarget": (0.0, 0.75, 2.35),
        "lensMm": 55.0,
        "subjectMaskGroups": ["subject"],
        "gateMaskGroups": [],
        "heroVariant": "A",
        "heroState": "oriented-cruise",
        "intentionalBlackout": False,
    },
    {
        "id": "protagonist-b-ancient-engine",
        "kind": "protagonist",
        "label": "Ancient engine",
        "visibleCollections": ["TP_R13_HERO_B"],
        "cameraRigId": "hero-controlled-comparison",
        "lightingRigId": "hero-controlled-comparison",
        "cameraLocation": (9.0, -8.0, 5.2),
        "cameraTarget": (0.0, 0.75, 2.35),
        "lensMm": 55.0,
        "subjectMaskGroups": ["subject"],
        "gateMaskGroups": [],
        "heroVariant": "B",
        "heroState": "oriented-cruise",
        "intentionalBlackout": False,
    },
    {
        "id": "protagonist-c-living-prism",
        "kind": "protagonist",
        "label": "Living prism",
        "visibleCollections": ["TP_R13_HERO_C"],
        "cameraRigId": "hero-controlled-comparison",
        "lightingRigId": "hero-controlled-comparison",
        "cameraLocation": (9.0, -8.0, 5.2),
        "cameraTarget": (0.0, 0.75, 2.35),
        "lensMm": 55.0,
        "subjectMaskGroups": ["subject"],
        "gateMaskGroups": [],
        "heroVariant": "C",
        "heroState": "oriented-cruise",
        "intentionalBlackout": False,
    },
    {
        "id": "architecture-chamber-module",
        "kind": "architecture",
        "label": "Chamber module",
        "visibleCollections": ["TP_R13_CHAMBER"],
        "cameraRigId": "chamber-module-test",
        "lightingRigId": "ancient-machine-amber",
        "cameraLocation": (7.8, -10.5, 6.2),
        "cameraTarget": (0.0, 2.2, 3.2),
        "lensMm": 48.0,
        "subjectMaskGroups": ["architecture"],
        "gateMaskGroups": [],
        "heroVariant": None,
        "heroState": None,
        "intentionalBlackout": False,
    },
    {
        "id": "architecture-gate-monolith",
        "kind": "architecture",
        "label": "Gate monolith",
        "visibleCollections": ["TP_R13_GATE"],
        "cameraRigId": "gate-monolith-test",
        "lightingRigId": "ancient-machine-emerald",
        "cameraLocation": (10.0, 14.5, 7.0),
        "cameraTarget": (0.0, -4.0, 3.4),
        "lensMm": 52.0,
        "subjectMaskGroups": ["gate"],
        "gateMaskGroups": ["gate"],
        "heroVariant": None,
        "heroState": None,
        "gateState": "open",
        "intentionalBlackout": False,
    },
    {
        "id": "gate-approach-hero",
        "kind": "gate",
        "label": "Gate approach",
        "visibleCollections": ["TP_R13_HERO_B", "TP_R13_GATE"],
        "cameraRigId": "gate-approach-rear-three-quarter",
        "lightingRigId": "ancient-machine-emerald",
        "cameraLocation": (8.0, 11.0, 5.8),
        "cameraTarget": (0.0, -3.0, 2.6),
        "lensMm": 48.0,
        "subjectMaskGroups": ["subject"],
        "gateMaskGroups": ["gate"],
        "heroVariant": "B",
        "heroState": "approach",
        "gateState": "open",
        "intentionalBlackout": False,
    },
    {
        "id": "gate-compression-hero",
        "kind": "gate",
        "label": "Gate compression",
        "visibleCollections": ["TP_R13_HERO_B", "TP_R13_GATE"],
        "cameraRigId": "gate-compression-side",
        "lightingRigId": "ancient-machine-emerald",
        "cameraLocation": (7.0, -0.2, 4.8),
        "cameraTarget": (0.0, -4.0, 2.6),
        "lensMm": 52.0,
        "subjectMaskGroups": ["subject"],
        "gateMaskGroups": ["gate"],
        "heroVariant": "B",
        "heroState": "gate-pressure",
        "gateState": "compression",
        "intentionalBlackout": False,
    },
    {
        "id": "gate-post-crossing-hero",
        "kind": "gate",
        "label": "Post-crossing recovery",
        "visibleCollections": ["TP_R13_HERO_B", "TP_R13_GATE"],
        "cameraRigId": "gate-post-crossing-front",
        "lightingRigId": "ancient-machine-emerald",
        "cameraLocation": (8.0, -15.0, 5.5),
        "cameraTarget": (-0.5, -5.2, 2.65),
        "lensMm": 56.0,
        "subjectMaskGroups": ["subject"],
        "gateMaskGroups": ["gate"],
        "heroVariant": "B",
        "heroState": "recovery",
        "gateState": "sealed",
        "intentionalBlackout": False,
    },
)


def _canonical_sha256(payload: object) -> str:
    data = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def r13_lookdev_contract() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "revisionId": R13_REVISION_ID,
        "sourceRevision": "andromeda-r12-continuous-slice",
        "previewOnly": True,
        "fullSequenceRender": False,
        "renderFormat": {
            "width": R13_RENDER_WIDTH,
            "height": R13_RENDER_HEIGHT,
            "phoneWidth": R13_PHONE_WIDTH,
            "phoneHeight": R13_PHONE_HEIGHT,
            "responsiveComposition": "native-vertical-not-crop",
        },
        "protagonistHierarchy": ["core", "shell", "atmosphere", "restrained-lattice"],
        "protagonistRequirements": {
            "frontBackReadable": True,
            "integratedAperture": True,
            "leadingEdgeIndicator": True,
            "trailingEnergyWake": True,
            "boundedAccelerationDeformation": True,
            "boundedGatePressureDeformation": True,
            "preservesV2OrbIdentity": True,
        },
        "constructionSystem": {
            "sharedBevelWidth": 0.18,
            "proportionModule": 0.6,
            "structuralConnections": True,
            "recessesAndPanelSeams": True,
            "supportElements": True,
            "weatheredCrystalVariation": True,
            "limitedEmissiveAccents": True,
            "functionalMovingParts": ["gate-locks", "seal-iris", "membrane-compression"],
        },
        "gateSystem": {
            "darkOuterMonoliths": True,
            "structuralThickness": True,
            "nestedMembraneDepth": True,
            "controlledTransmission": True,
            "readableBeyond": True,
            "sealingMechanism": True,
        },
        "diagnosticThresholds": {
            "nearBlackLuminance": 0.05,
            "ordinaryFrameReviewFraction": 0.60,
            "clippedHighlightLuminance": 0.98,
        },
        "variants": [dict(item) for item in R13_VARIANTS],
        "selection": {
            "protagonistDesign": None,
            "architecturalMaterialLanguage": None,
            "gateConstruction": None,
            "exposureLightingTreatment": None,
            "status": "pending-human-operator-selection",
            "humanArtistApproval": "pending",
            "artistApproved": False,
        },
        "motionTest": {
            "allowedOnlyAfterSelection": True,
            "status": "blocked-pending-look-selection",
            "durationSeconds": {"minimum": 3.0, "maximum": 5.0},
        },
    }


def validate_r13_contract(contract: Mapping[str, Any]) -> None:
    variants = contract.get("variants")
    if not isinstance(variants, list) or len(variants) != 8:
        raise ValueError("R13 requires exactly eight bounded look-development states.")
    ids = [item.get("id") for item in variants if isinstance(item, Mapping)]
    if len(ids) != 8 or len(set(ids)) != 8:
        raise ValueError("R13 variant identifiers must be unique.")
    protagonist = [
        item for item in variants if isinstance(item, Mapping) and item.get("kind") == "protagonist"
    ]
    if len(protagonist) != 3:
        raise ValueError("R13 requires three controlled protagonist variants.")
    if len({item.get("cameraRigId") for item in protagonist}) != 1 or len(
        {item.get("lightingRigId") for item in protagonist}
    ) != 1:
        raise ValueError("R13 protagonist variants must share camera and lighting.")
    gate_states = {
        item.get("gateState")
        for item in variants
        if isinstance(item, Mapping) and item.get("kind") == "gate"
    }
    if gate_states != {"open", "compression", "sealed"}:
        raise ValueError("R13 gate review must include approach, compression, and sealing.")
    selection = contract.get("selection")
    if not isinstance(selection, Mapping) or selection.get("artistApproved") is not False:
        raise ValueError("R13 cannot auto-approve the selected look.")


def _remove_r13_data() -> None:
    import bpy  # type: ignore[import-not-found]

    for obj in list(bpy.data.objects):
        if obj.name.startswith("TP_R13_"):
            bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        if collection.name.startswith("TP_R13_"):
            bpy.data.collections.remove(collection)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.cameras, bpy.data.lights):
        for datablock in list(datablocks):
            if datablock.name.startswith("TP_R13_") and datablock.users == 0:
                datablocks.remove(datablock)
    for material in list(bpy.data.materials):
        if material.name.startswith("TP_R13_") and material.users == 0:
            bpy.data.materials.remove(material)
    for world in list(bpy.data.worlds):
        if world.name.startswith("TP_R13_") and world.users == 0:
            bpy.data.worlds.remove(world)


def _collection(name: str, parent: Any) -> Any:
    import bpy  # type: ignore[import-not-found]

    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


def _link(obj: Any, collection: Any, material: Any | None = None) -> Any:
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    if material is not None and hasattr(obj.data, "materials"):
        obj.data.materials.append(material)
    return obj


def _tag(obj: Any, *, semantic: str, component: str, variant: str | None = None) -> Any:
    obj["trackprompt_revision"] = "r13"
    obj["trackprompt_r13_semantic"] = semantic
    obj["trackprompt_r13_component"] = component
    obj["trackprompt_r13_mask_group"] = semantic
    if variant is not None:
        obj["trackprompt_r13_variant"] = variant
    return obj


def _bevel(obj: Any, width: float = 0.18, segments: int = 3) -> None:
    modifier = obj.modifiers.new(f"{obj.name}_BEVEL", "BEVEL")
    modifier.width = width
    modifier.segments = segments


def _cube(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    material: Any,
    semantic: str,
    component: str,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    bevel: float = 0.18,
    variant: str | None = None,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.scale = tuple(value * 0.5 for value in dimensions)
    _link(obj, collection, material)
    if bevel > 0.0:
        _bevel(obj, min(bevel, min(dimensions) * 0.12))
    return _tag(obj, semantic=semantic, component=component, variant=variant)


def _sphere(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    scale: tuple[float, float, float],
    material: Any,
    semantic: str,
    component: str,
    variant: str | None = None,
    subdivisions: int = 3,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions, radius=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    _link(obj, collection, material)
    return _tag(obj, semantic=semantic, component=component, variant=variant)


def _torus(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    major_radius: float,
    minor_radius: float,
    material: Any,
    semantic: str,
    component: str,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    variant: str | None = None,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_torus_add(
        major_radius=major_radius,
        minor_radius=minor_radius,
        major_segments=48,
        minor_segments=8,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    _link(obj, collection, material)
    return _tag(obj, semantic=semantic, component=component, variant=variant)


def _cylinder(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    radius: float,
    depth: float,
    material: Any,
    semantic: str,
    component: str,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    variant: str | None = None,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=48,
        radius=radius,
        depth=depth,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    _link(obj, collection, material)
    _bevel(obj, min(0.12, depth * 0.15), 2)
    return _tag(obj, semantic=semantic, component=component, variant=variant)


def _cone(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    radius1: float,
    radius2: float,
    depth: float,
    material: Any,
    semantic: str,
    component: str,
    rotation: tuple[float, float, float] = (math.pi / 2.0, 0.0, 0.0),
    variant: str | None = None,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_cone_add(
        vertices=32,
        radius1=radius1,
        radius2=radius2,
        depth=depth,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    _link(obj, collection, material)
    return _tag(obj, semantic=semantic, component=component, variant=variant)


def _empty(name: str, collection: Any, location: tuple[float, float, float]) -> Any:
    import bpy  # type: ignore[import-not-found]

    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = "PLAIN_AXES"
    obj.location = location
    collection.objects.link(obj)
    obj["trackprompt_revision"] = "r13"
    return obj


def _parent(objects: Sequence[Any], root: Any) -> None:
    for obj in objects:
        obj.parent = root
        obj.matrix_parent_inverse.identity()


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
        raise RuntimeError("R13 physical material has no Principled BSDF.")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = noise_scale
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.72
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = bump_strength
    bump.inputs["Distance"].default_value = 0.1
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    normal = principled.inputs.get("Normal")
    if normal is not None:
        links.new(bump.outputs["Normal"], normal)
    material["trackprompt_revision"] = "r13"
    material["trackprompt_surface_family"] = "weathered-ancient-machine"
    return material


def _accent_material(
    name: str,
    base: tuple[float, float, float, float],
    emission: tuple[float, float, float, float],
    strength: float,
    *,
    alpha: float = 1.0,
    transmission: float = 0.0,
) -> Any:
    if not 0.0 <= strength <= 2.0:
        raise ValueError("R13 emissive accents must remain restrained.")
    material = create_material(
        name,
        (*base[:3], alpha),
        metallic=0.12,
        roughness=0.38,
        emission_color=emission,
        emission_strength=strength,
    )
    principled = _principled(material)
    if principled is not None:
        alpha_socket = principled.inputs.get("Alpha")
        if alpha_socket is not None:
            alpha_socket.default_value = alpha
        transmission_socket = principled.inputs.get("Transmission Weight") or principled.inputs.get(
            "Transmission"
        )
        if transmission_socket is not None:
            transmission_socket.default_value = transmission
    material.diffuse_color = (*base[:3], alpha)
    if alpha < 1.0 and hasattr(material, "surface_render_method"):
        try:
            material.surface_render_method = "DITHERED"
        except (TypeError, ValueError):
            pass
    material["trackprompt_revision"] = "r13"
    material["trackprompt_surface_family"] = "controlled-accent"
    material["trackprompt_emission_strength"] = strength
    return material


def _materials() -> dict[str, Any]:
    return {
        "shell": _physical_material(
            "TP_R13_HERO_SHELL",
            (0.055, 0.085, 0.12, 1.0),
            metallic=0.56,
            roughness=0.34,
            noise_scale=6.0,
            bump_strength=0.12,
        ),
        "shell_b": _physical_material(
            "TP_R13_HERO_SHELL_B",
            (0.105, 0.105, 0.115, 1.0),
            metallic=0.68,
            roughness=0.29,
            noise_scale=8.0,
            bump_strength=0.1,
        ),
        "shell_c": _physical_material(
            "TP_R13_HERO_SHELL_C",
            (0.045, 0.11, 0.145, 1.0),
            metallic=0.36,
            roughness=0.3,
            noise_scale=4.0,
            bump_strength=0.08,
        ),
        "core": _accent_material(
            "TP_R13_HERO_CORE",
            (0.05, 0.18, 0.26, 1.0),
            (0.18, 0.72, 1.0, 1.0),
            1.45,
        ),
        "atmosphere": _accent_material(
            "TP_R13_HERO_ATMOSPHERE",
            (0.05, 0.18, 0.23, 1.0),
            (0.1, 0.44, 0.62, 1.0),
            0.18,
            alpha=0.12,
            transmission=0.35,
        ),
        "lattice": _accent_material(
            "TP_R13_HERO_LATTICE",
            (0.08, 0.18, 0.22, 1.0),
            (0.12, 0.48, 0.64, 1.0),
            0.24,
        ),
        "aperture": _accent_material(
            "TP_R13_HERO_APERTURE",
            (0.18, 0.06, 0.24, 1.0),
            (0.62, 0.22, 0.92, 1.0),
            1.1,
        ),
        "leading": _accent_material(
            "TP_R13_HERO_LEADING",
            (0.05, 0.22, 0.25, 1.0),
            (0.14, 0.9, 0.96, 1.0),
            0.72,
        ),
        "wake": _accent_material(
            "TP_R13_HERO_WAKE",
            (0.05, 0.2, 0.24, 1.0),
            (0.08, 0.62, 0.8, 1.0),
            0.38,
            alpha=0.2,
            transmission=0.2,
        ),
        "stone": _physical_material(
            "TP_R13_ANCIENT_STONE",
            (0.075, 0.082, 0.09, 1.0),
            metallic=0.18,
            roughness=0.7,
            noise_scale=3.5,
            bump_strength=0.32,
        ),
        "metal": _physical_material(
            "TP_R13_ANCIENT_METAL",
            (0.09, 0.105, 0.11, 1.0),
            metallic=0.74,
            roughness=0.46,
            noise_scale=9.0,
            bump_strength=0.18,
        ),
        "recess": _physical_material(
            "TP_R13_PANEL_RECESS",
            (0.018, 0.025, 0.03, 1.0),
            metallic=0.4,
            roughness=0.62,
            noise_scale=12.0,
            bump_strength=0.1,
        ),
        "crystal": _accent_material(
            "TP_R13_CONSTRUCTION_CRYSTAL",
            (0.025, 0.14, 0.14, 1.0),
            (0.08, 0.62, 0.5, 1.0),
            0.56,
        ),
        "amber": _accent_material(
            "TP_R13_CHAMBER_AMBER",
            (0.21, 0.09, 0.02, 1.0),
            (0.95, 0.38, 0.06, 1.0),
            0.75,
        ),
        "membrane_outer": _accent_material(
            "TP_R13_GATE_MEMBRANE_OUTER",
            (0.018, 0.16, 0.13, 1.0),
            (0.05, 0.5, 0.35, 1.0),
            0.1,
            alpha=0.055,
            transmission=0.86,
        ),
        "membrane_inner": _accent_material(
            "TP_R13_GATE_MEMBRANE_INNER",
            (0.04, 0.2, 0.24, 1.0),
            (0.08, 0.62, 0.58, 1.0),
            0.14,
            alpha=0.085,
            transmission=0.92,
        ),
        "beyond": _accent_material(
            "TP_R13_GATE_BEYOND",
            (0.018, 0.055, 0.1, 1.0),
            (0.04, 0.18, 0.32, 1.0),
            0.28,
        ),
    }


def _build_hero_variant(
    collection: Any,
    variant: str,
    materials: Mapping[str, Any],
) -> Any:
    root = _empty(f"TP_R13_HERO_{variant}_ROOT", collection, (0.0, 0.0, 2.4))
    shell_material = {
        "A": materials["shell"],
        "B": materials["shell_b"],
        "C": materials["shell_c"],
    }[variant]
    shell_scale = {"A": (1.38, 1.62, 1.08), "B": (1.48, 1.72, 1.0), "C": (1.25, 1.78, 1.25)}[
        variant
    ]
    parts: list[Any] = []
    parts.append(
        _sphere(
            f"TP_R13_HERO_{variant}_SHELL",
            collection,
            location=(0.0, 0.0, 0.0),
            scale=shell_scale,
            material=shell_material,
            semantic="subject",
            component="shell",
            variant=variant,
            subdivisions=2 if variant == "C" else 3,
        )
    )
    parts.append(
        _sphere(
            f"TP_R13_HERO_{variant}_CORE",
            collection,
            location=(0.0, -0.1, 0.0),
            scale=(0.58, 0.78, 0.58),
            material=materials["core"],
            semantic="subject",
            component="core",
            variant=variant,
            subdivisions=3,
        )
    )
    parts.append(
        _sphere(
            f"TP_R13_HERO_{variant}_ATMOSPHERE",
            collection,
            location=(0.0, 0.0, 0.0),
            scale=tuple(value * 1.14 for value in shell_scale),
            material=materials["atmosphere"],
            semantic="subject",
            component="atmosphere",
            variant=variant,
            subdivisions=3,
        )
    )
    for index, (rotation, scale) in enumerate(
        (
            ((math.pi / 2.0, 0.0, 0.0), (1.0, 1.0, 0.82)),
            ((0.0, math.pi / 2.0, 0.0), (0.86, 1.0, 1.0)),
            ((0.0, 0.0, math.pi / 4.0), (1.0, 0.84, 1.0)),
        )
    ):
        if variant == "C" and index == 2:
            continue
        parts.append(
            _torus(
                f"TP_R13_HERO_{variant}_LATTICE_{index:02d}",
                collection,
                location=(0.0, 0.0, 0.0),
                major_radius=1.17 + index * 0.06,
                minor_radius=0.018,
                material=materials["lattice"],
                semantic="subject",
                component="restrained-lattice",
                rotation=rotation,
                scale=scale,
                variant=variant,
            )
        )
    aperture_depth = 0.46 if variant == "B" else 0.36
    parts.append(
        _cylinder(
            f"TP_R13_HERO_{variant}_APERTURE_CORE",
            collection,
            location=(0.0, -1.36, 0.0),
            radius=0.53,
            depth=aperture_depth,
            material=materials["aperture"],
            semantic="subject",
            component="integrated-front-aperture",
            rotation=(math.pi / 2.0, 0.0, 0.0),
            variant=variant,
        )
    )
    parts.append(
        _torus(
            f"TP_R13_HERO_{variant}_APERTURE_COLLAR",
            collection,
            location=(0.0, -1.56, 0.0),
            major_radius=0.62,
            minor_radius=0.1,
            material=shell_material,
            semantic="subject",
            component="integrated-aperture-collar",
            rotation=(math.pi / 2.0, 0.0, 0.0),
            variant=variant,
        )
    )
    parts.append(
        _cube(
            f"TP_R13_HERO_{variant}_LEADING_EDGE",
            collection,
            location=(0.0, -1.52, 0.88),
            dimensions=(0.32 if variant != "C" else 0.52, 0.3, 0.22),
            material=materials["leading"],
            semantic="subject",
            component="leading-edge-indicator",
            rotation=(0.18, 0.0, 0.0),
            bevel=0.08,
            variant=variant,
        )
    )
    fin_offsets = {"A": (-0.55, 0.35), "B": (-0.68, 0.68), "C": (-0.42, 0.82)}[variant]
    for index, x_value in enumerate(fin_offsets):
        parts.append(
            _cube(
                f"TP_R13_HERO_{variant}_DIRECTIONAL_FIN_{index:02d}",
                collection,
                location=(x_value, 0.35, 0.75 - index * 0.3),
                dimensions=(0.18, 1.05 + 0.2 * index, 0.52),
                material=shell_material,
                semantic="subject",
                component="directional-fin",
                rotation=(0.0, 0.16 * (index * 2 - 1), 0.1 * (index * 2 - 1)),
                bevel=0.07,
                variant=variant,
            )
        )
    if variant == "A":
        parts.append(
            _torus(
                "TP_R13_HERO_A_REAR_STABILIZER",
                collection,
                location=(0.0, 0.78, 0.0),
                major_radius=1.12,
                minor_radius=0.095,
                material=shell_material,
                semantic="subject",
                component="rear-stabilizer",
                rotation=(math.pi / 2.0, 0.0, 0.0),
                scale=(1.0, 1.0, 0.82),
                variant=variant,
            )
        )
    elif variant == "B":
        for band_index, y_value in enumerate((-0.48, 0.46)):
            parts.append(
                _torus(
                    f"TP_R13_HERO_B_ARMOR_BAND_{band_index:02d}",
                    collection,
                    location=(0.0, y_value, 0.0),
                    major_radius=1.28,
                    minor_radius=0.11,
                    material=shell_material,
                    semantic="subject",
                    component="integrated-engine-band",
                    rotation=(math.pi / 2.0, 0.0, 0.0),
                    scale=(1.0, 1.0, 0.72),
                    variant=variant,
                )
            )
        for side in (-1.0, 1.0):
            parts.append(
                _cube(
                    f"TP_R13_HERO_B_ENGINE_POD_{'L' if side < 0 else 'R'}",
                    collection,
                    location=(side * 1.22, 0.2, -0.12),
                    dimensions=(0.34, 0.92, 0.7),
                    material=shell_material,
                    semantic="subject",
                    component="integrated-engine-pod",
                    rotation=(0.0, side * 0.18, side * 0.12),
                    bevel=0.09,
                    variant=variant,
                )
            )
    else:
        for vane_index, (x_value, z_value, angle) in enumerate(
            ((-1.05, 0.58, -0.42), (0.92, 0.82, 0.34), (-0.72, -0.82, -0.28))
        ):
            parts.append(
                _cube(
                    f"TP_R13_HERO_C_PRISM_VANE_{vane_index:02d}",
                    collection,
                    location=(x_value, 0.35, z_value),
                    dimensions=(0.24, 1.15, 0.62),
                    material=materials["leading"],
                    semantic="subject",
                    component="asymmetric-prismatic-vane",
                    rotation=(angle, angle * 0.5, -angle),
                    bevel=0.04,
                    variant=variant,
                )
            )
    wake_count = {"A": 3, "B": 4, "C": 3}[variant]
    for index in range(wake_count):
        parts.append(
            _cone(
                f"TP_R13_HERO_{variant}_WAKE_{index:02d}",
                collection,
                location=(0.0, 1.65 + index * 0.62, -0.05 + index * 0.05),
                radius1=max(0.12, 0.5 - index * 0.1),
                radius2=max(0.04, 0.22 - index * 0.045),
                depth=1.15,
                material=materials["wake"],
                semantic="subject",
                component="trailing-energy-wake",
                rotation=(math.pi / 2.0, 0.0, 0.0),
                variant=variant,
            )
        )
    _parent(parts, root)
    root["trackprompt_r13_front_axis"] = "local-negative-y"
    root["trackprompt_r13_back_axis"] = "local-positive-y"
    root["trackprompt_r13_deformation"] = "bounded-scale-response"
    return root


def _build_chamber(collection: Any, materials: Mapping[str, Any]) -> None:
    y_value = 2.4
    for side in (-1.0, 1.0):
        x_value = side * 3.2
        _cube(
            f"TP_R13_CHAMBER_PYLON_{'L' if side < 0 else 'R'}",
            collection,
            location=(x_value, y_value, 3.5),
            dimensions=(1.35, 2.2, 7.0),
            material=materials["stone"],
            semantic="architecture",
            component="load-bearing-pylon",
        )
        _cube(
            f"TP_R13_CHAMBER_RECESS_{'L' if side < 0 else 'R'}",
            collection,
            location=(x_value - side * 0.69, y_value - 0.18, 3.5),
            dimensions=(0.16, 1.55, 4.8),
            material=materials["recess"],
            semantic="architecture",
            component="functional-recess",
            bevel=0.04,
        )
        for level in (1.35, 3.5, 5.65):
            _cube(
                f"TP_R13_CHAMBER_SEAM_{'L' if side < 0 else 'R'}_{level}",
                collection,
                location=(x_value - side * 0.72, y_value - 0.45, level),
                dimensions=(0.12, 0.28, 0.12),
                material=materials["amber"],
                semantic="architecture",
                component="limited-emissive-seam",
                bevel=0.03,
            )
        _cube(
            f"TP_R13_CHAMBER_SUPPORT_{'L' if side < 0 else 'R'}",
            collection,
            location=(side * 4.1, y_value + 0.2, 1.2),
            dimensions=(1.4, 3.0, 1.1),
            material=materials["metal"],
            semantic="architecture",
            component="structural-buttress",
            rotation=(0.0, side * 0.34, 0.0),
        )
    _cube(
        "TP_R13_CHAMBER_LINTEL",
        collection,
        location=(0.0, y_value, 7.05),
        dimensions=(7.8, 2.3, 1.25),
        material=materials["stone"],
        semantic="architecture",
        component="connected-lintel",
    )
    _cube(
        "TP_R13_CHAMBER_BACKPLATE",
        collection,
        location=(0.0, y_value + 1.0, 3.4),
        dimensions=(5.0, 0.45, 5.8),
        material=materials["metal"],
        semantic="architecture",
        component="reactivation-door",
    )
    for index, z_value in enumerate((1.5, 2.5, 3.5, 4.5, 5.5)):
        _cube(
            f"TP_R13_CHAMBER_PANEL_SEAM_{index:02d}",
            collection,
            location=(0.0, y_value + 0.74, z_value),
            dimensions=(4.2, 0.08, 0.07),
            material=materials["recess"],
            semantic="architecture",
            component="panel-seam",
            bevel=0.02,
        )
    for x_value in (-1.6, 0.0, 1.6):
        _sphere(
            f"TP_R13_CHAMBER_CRYSTAL_{x_value}",
            collection,
            location=(x_value, y_value - 0.35, 6.35),
            scale=(0.24, 0.18, 0.52),
            material=materials["crystal"],
            semantic="architecture",
            component="crystalline-reactivation-node",
            subdivisions=1,
        )


def _build_gate(collection: Any, materials: Mapping[str, Any]) -> None:
    gate_y = -4.0
    for side in (-1.0, 1.0):
        x_value = side * 3.65
        _cube(
            f"TP_R13_GATE_MONOLITH_{'L' if side < 0 else 'R'}",
            collection,
            location=(x_value, gate_y, 3.7),
            dimensions=(2.25, 2.5, 8.2),
            material=materials["stone"],
            semantic="gate",
            component="thick-outer-monolith",
        )
        _cube(
            f"TP_R13_GATE_INNER_RECESS_{'L' if side < 0 else 'R'}",
            collection,
            location=(x_value - side * 1.12, gate_y - 0.25, 3.7),
            dimensions=(0.18, 1.45, 5.9),
            material=materials["recess"],
            semantic="gate",
            component="inner-recess",
            bevel=0.04,
        )
        _cube(
            f"TP_R13_GATE_BUTTRESS_{'L' if side < 0 else 'R'}",
            collection,
            location=(side * 5.0, gate_y + 0.45, 1.3),
            dimensions=(2.0, 3.4, 1.25),
            material=materials["metal"],
            semantic="gate",
            component="load-transfer-buttress",
            rotation=(0.0, side * 0.38, 0.0),
        )
        for level in (1.5, 3.0, 4.5, 6.0):
            _cube(
                f"TP_R13_GATE_SEAM_{'L' if side < 0 else 'R'}_{level}",
                collection,
                location=(x_value - side * 1.18, gate_y - 0.6, level),
                dimensions=(0.13, 0.2, 0.08),
                material=materials["crystal"],
                semantic="gate",
                component="limited-emissive-seam",
                bevel=0.02,
            )
    _cube(
        "TP_R13_GATE_LINTEL",
        collection,
        location=(0.0, gate_y, 8.0),
        dimensions=(9.4, 2.5, 1.4),
        material=materials["stone"],
        semantic="gate",
        component="connected-lintel",
    )
    _cube(
        "TP_R13_GATE_BEYOND",
        collection,
        location=(0.0, gate_y - 1.35, 3.75),
        dimensions=(5.3, 0.12, 6.3),
        material=materials["beyond"],
        semantic="gate",
        component="readable-space-beyond",
        bevel=0.0,
    )
    for index, (y_offset, radius, material_key) in enumerate(
        ((0.35, 2.72, "membrane_outer"), (0.0, 2.42, "membrane_inner"), (-0.35, 2.1, "membrane_outer"))
    ):
        membrane = _cylinder(
            f"TP_R13_GATE_MEMBRANE_{index:02d}",
            collection,
            location=(0.0, gate_y + y_offset, 3.75),
            radius=radius,
            depth=0.08,
            material=materials[material_key],
            semantic="gate",
            component="nested-membrane-layer",
            rotation=(math.pi / 2.0, 0.0, 0.0),
        )
        membrane["trackprompt_r13_membrane_index"] = index
        _torus(
            f"TP_R13_GATE_MEMBRANE_FRAME_{index:02d}",
            collection,
            location=(0.0, gate_y + y_offset, 3.75),
            major_radius=radius,
            minor_radius=0.07 + index * 0.015,
            material=materials["crystal" if index == 1 else "metal"],
            semantic="gate",
            component="nested-membrane-frame",
            rotation=(math.pi / 2.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
        )
    for index, (x_value, z_value, scale) in enumerate(
        (
            (-1.6, 2.2, 0.07),
            (1.25, 2.75, 0.09),
            (-0.55, 4.5, 0.06),
            (1.75, 5.35, 0.08),
            (0.35, 6.1, 0.05),
        )
    ):
        _sphere(
            f"TP_R13_GATE_BEYOND_MARKER_{index:02d}",
            collection,
            location=(x_value, gate_y - 1.5, z_value),
            scale=(scale, scale * 0.7, scale),
            material=materials["leading"],
            semantic="gate",
            component="beyond-depth-marker",
            subdivisions=1,
        )
    for side in (-1.0, 1.0):
        lock = _cube(
            f"TP_R13_GATE_LOCK_{'L' if side < 0 else 'R'}",
            collection,
            location=(side * 2.15, gate_y - 0.72, 3.75),
            dimensions=(1.05, 0.62, 1.55),
            material=materials["metal"],
            semantic="gate",
            component="functional-gate-lock",
            rotation=(0.0, 0.0, side * 0.12),
        )
        lock["trackprompt_r13_lock_side"] = side
    seal = _cube(
        "TP_R13_GATE_SEAL",
        collection,
        location=(0.0, gate_y - 0.86, 3.75),
        dimensions=(0.18, 0.3, 5.65),
        material=materials["crystal"],
        semantic="gate",
        component="closed-seal-seam",
        bevel=0.04,
    )
    seal["trackprompt_r13_seal"] = True


def _light(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    energy: float,
    color: tuple[float, float, float],
    size: float,
    light_type: str = "AREA",
) -> Any:
    import bpy  # type: ignore[import-not-found]

    data = bpy.data.lights.new(name, type=light_type)
    data.energy = energy
    data.color = color
    if hasattr(data, "shape"):
        data.shape = "DISK"
    if hasattr(data, "size"):
        data.size = size
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    collection.objects.link(obj)
    obj["trackprompt_revision"] = "r13"
    return obj


def _aim(obj: Any, target: tuple[float, float, float]) -> None:
    from mathutils import Vector  # type: ignore[import-not-found]

    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _configure_world() -> Any:
    import bpy  # type: ignore[import-not-found]

    world = bpy.data.worlds.new("TP_R13_WORLD")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.012, 0.022, 0.032, 1.0)
        background.inputs["Strength"].default_value = 0.18
    bpy.context.scene.world = world
    return world


def _configure_render() -> None:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = R13_RENDER_WIDTH
    scene.render.resolution_y = R13_RENDER_HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.render.use_file_extension = True
    scene.render.fps = 30
    scene.render.fps_base = 1.0
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.25
    scene.view_settings.gamma = 1.0


def _hide_source_scene(root: Any) -> list[str]:
    import bpy  # type: ignore[import-not-found]

    hidden: list[str] = []
    for collection in bpy.context.scene.collection.children:
        if collection == root:
            continue
        if not collection.hide_render:
            collection.hide_render = True
            hidden.append(collection.name)
    for obj in bpy.context.scene.collection.objects:
        if obj.name.startswith("TP_R13_"):
            continue
        obj.hide_render = True
        obj.hide_viewport = True
        hidden.append(f"object:{obj.name}")
    return sorted(hidden)


def build_r13_lookdev_scene(output_path: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    contract = r13_lookdev_contract()
    validate_r13_contract(contract)
    scene = bpy.context.scene
    source_revision = scene.get("trackprompt_r12_revision") or scene.get(
        "trackprompt_story_revision"
    )
    if source_revision != "andromeda-r12-continuous-slice":
        raise ValueError("R13 look development must be derived from the verified R12 scene.")
    _remove_r13_data()
    root = bpy.data.collections.new(R13_ROOT_COLLECTION)
    scene.collection.children.link(root)
    controls = _collection("TP_R13_CONTROLS", root)
    environment = _collection("TP_R13_ENVIRONMENT", root)
    lights = _collection("TP_R13_LIGHTS", root)
    hero_a = _collection("TP_R13_HERO_A", root)
    hero_b = _collection("TP_R13_HERO_B", root)
    hero_c = _collection("TP_R13_HERO_C", root)
    chamber = _collection("TP_R13_CHAMBER", root)
    gate = _collection("TP_R13_GATE", root)
    materials = _materials()

    _cube(
        "TP_R13_ENV_FLOOR",
        environment,
        location=(0.0, -0.5, -0.25),
        dimensions=(20.0, 28.0, 0.4),
        material=materials["stone"],
        semantic="background",
        component="ground-plane",
        bevel=0.08,
    )
    _cube(
        "TP_R13_ENV_BACKDROP",
        environment,
        location=(0.0, 8.5, 5.0),
        dimensions=(18.0, 0.4, 12.0),
        material=materials["recess"],
        semantic="background",
        component="depth-backdrop",
        bevel=0.06,
    )
    for side in (-1.0, 1.0):
        _cube(
            f"TP_R13_ENV_SIDE_{'L' if side < 0 else 'R'}",
            environment,
            location=(side * 8.4, 0.0, 4.2),
            dimensions=(0.45, 18.0, 8.8),
            material=materials["stone"],
            semantic="background",
            component="depth-wing",
            bevel=0.08,
        )

    hero_roots = {
        "A": _build_hero_variant(hero_a, "A", materials),
        "B": _build_hero_variant(hero_b, "B", materials),
        "C": _build_hero_variant(hero_c, "C", materials),
    }
    _build_chamber(chamber, materials)
    _build_gate(gate, materials)

    camera_data = bpy.data.cameras.new(R13_CAMERA_NAME)
    camera = bpy.data.objects.new(R13_CAMERA_NAME, camera_data)
    controls.objects.link(camera)
    camera.data.sensor_width = 36.0
    camera.data.dof.use_dof = False
    camera["trackprompt_revision"] = "r13"
    scene.camera = camera

    key = _light(
        "TP_R13_LIGHT_KEY",
        lights,
        location=(-5.5, -6.0, 8.5),
        energy=1200.0,
        color=(0.44, 0.68, 1.0),
        size=5.5,
    )
    rim = _light(
        "TP_R13_LIGHT_RIM",
        lights,
        location=(5.8, 2.0, 7.2),
        energy=1500.0,
        color=(0.18, 0.85, 0.74),
        size=4.0,
    )
    fill = _light(
        "TP_R13_LIGHT_FILL",
        lights,
        location=(0.0, -2.5, 1.5),
        energy=850.0,
        color=(0.42, 0.32, 0.58),
        size=6.5,
    )
    amber = _light(
        "TP_R13_LIGHT_CHAMBER",
        lights,
        location=(0.0, 0.8, 6.5),
        energy=900.0,
        color=(1.0, 0.34, 0.08),
        size=3.2,
    )
    gate_light = _light(
        "TP_R13_LIGHT_GATE",
        lights,
        location=(0.0, -2.6, 4.0),
        energy=720.0,
        color=(0.08, 0.82, 0.52),
        size=4.5,
    )
    for light, target in (
        (key, (0.0, 0.0, 2.5)),
        (rim, (0.0, 0.0, 2.5)),
        (fill, (0.0, 0.0, 2.2)),
        (amber, (0.0, 2.4, 3.5)),
        (gate_light, (0.0, -4.0, 3.6)),
    ):
        _aim(light, target)

    _configure_world()
    _configure_render()
    hidden = _hide_source_scene(root)
    scene["trackprompt_r13_lookdev_contract"] = json.dumps(
        contract,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    scene["trackprompt_r13_contract_sha256"] = _canonical_sha256(contract)
    scene["trackprompt_r13_revision"] = R13_REVISION_ID
    scene["trackprompt_r13_source_revision"] = str(source_revision)
    scene["trackprompt_r13_hidden_source_collections"] = json.dumps(hidden)
    scene["trackprompt_r13_authoritative_source"] = "lookdev_r13.py"
    scene["trackprompt_r13_manual_edits_authoritative"] = False
    scene["trackprompt_r13_selected_look"] = "pending-human-operator-selection"
    for variant, root_obj in hero_roots.items():
        root_obj["trackprompt_r13_base_location"] = tuple(root_obj.location)
        root_obj["trackprompt_r13_base_scale"] = tuple(root_obj.scale)
        root_obj["trackprompt_r13_variant_id"] = variant
    apply_r13_variant("protagonist-b-ancient-engine")
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    validation = validate_r13_scene()
    return {
        "ok": validation["ok"],
        "revisionId": R13_REVISION_ID,
        "outputFile": str(output),
        "contractSha256": scene["trackprompt_r13_contract_sha256"],
        "hiddenSourceCollections": hidden,
        "selectionStatus": "pending-human-operator-selection",
        "validation": validation,
    }


def _variant(identifier: str) -> dict[str, Any]:
    match = next((item for item in R13_VARIANTS if item["id"] == identifier), None)
    if match is None:
        raise ValueError("Unknown R13 look-development variant.")
    return dict(match)


def _collection_visibility(visible: set[str]) -> None:
    import bpy  # type: ignore[import-not-found]

    for name in (
        "TP_R13_HERO_A",
        "TP_R13_HERO_B",
        "TP_R13_HERO_C",
        "TP_R13_CHAMBER",
        "TP_R13_GATE",
    ):
        collection = bpy.data.collections.get(name)
        if collection is not None:
            collection.hide_render = name not in visible
            collection.hide_viewport = name not in visible


def _apply_hero_state(variant: Mapping[str, Any]) -> None:
    import bpy  # type: ignore[import-not-found]

    roots = {key: bpy.data.objects.get(f"TP_R13_HERO_{key}_ROOT") for key in ("A", "B", "C")}
    for root in roots.values():
        if root is not None:
            root.location = (0.0, 0.0, 2.4)
            root.scale = (1.0, 1.0, 1.0)
            root.rotation_euler = (0.0, 0.0, 0.0)
            root["trackprompt_r13_deformation_state"] = "neutral"
    hero_variant = variant.get("heroVariant")
    root = roots.get(str(hero_variant)) if hero_variant is not None else None
    state = variant.get("heroState")
    if root is None:
        return
    if state == "oriented-cruise":
        root.rotation_euler = (0.04, -0.12, 0.18)
        root["trackprompt_r13_deformation_state"] = "orientation-readable"
    elif state == "approach":
        root.location = (0.15, 0.2, 2.55)
        root.rotation_euler = (0.02, -0.08, 0.05)
        root.scale = (1.0, 1.04, 0.98)
        root["trackprompt_r13_deformation_state"] = "bounded-acceleration-elongation"
    elif state == "gate-pressure":
        root.location = (0.0, -3.72, 2.55)
        root.rotation_euler = (0.0, -0.05, -0.06)
        root.scale = (1.14, 0.64, 1.16)
        root["trackprompt_r13_deformation_state"] = "bounded-gate-compression"
    elif state == "recovery":
        root.location = (-1.35, -6.1, 2.7)
        root.rotation_euler = (-0.03, 0.08, 0.1)
        root.scale = (1.04, 0.94, 1.02)
        root["trackprompt_r13_deformation_state"] = "readable-post-crossing-recovery"


def _apply_gate_state(state: str | None) -> None:
    import bpy  # type: ignore[import-not-found]

    for obj in bpy.data.objects:
        if obj.get("trackprompt_r13_component") == "readable-space-beyond":
            obj.hide_render = state == "sealed"
            obj.hide_viewport = state == "sealed"
        membrane_index = obj.get("trackprompt_r13_membrane_index")
        if isinstance(membrane_index, int):
            obj.scale = (1.0, 1.0, 1.0)
            if state == "compression":
                factor = (0.88, 0.78, 0.68)[membrane_index]
                obj.scale = (factor, factor, 1.0)
            elif state == "sealed":
                factor = (0.76, 0.58, 0.38)[membrane_index]
                obj.scale = (factor, factor, 1.0)
        side = obj.get("trackprompt_r13_lock_side")
        if isinstance(side, float):
            x_position = 2.15
            if state == "compression":
                x_position = 1.25
            elif state == "sealed":
                x_position = 0.48
            obj.location.x = side * x_position
        if obj.get("trackprompt_r13_seal") is True:
            obj.hide_render = state != "sealed"
            obj.hide_viewport = state != "sealed"


def apply_r13_variant(identifier: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    if scene.get("trackprompt_r13_revision") != R13_REVISION_ID:
        raise ValueError("The active scene is not an R13 look-development scene.")
    variant = _variant(identifier)
    _collection_visibility(set(variant["visibleCollections"]))
    _apply_hero_state(variant)
    _apply_gate_state(variant.get("gateState"))
    camera = bpy.data.objects.get(R13_CAMERA_NAME)
    if camera is None or camera.type != "CAMERA":
        raise ValueError("R13 look-development camera is missing.")
    camera.location = tuple(variant["cameraLocation"])
    _aim(camera, tuple(variant["cameraTarget"]))
    camera.data.lens = float(variant["lensMm"])
    scene.camera = camera
    scene["trackprompt_r13_active_variant"] = identifier
    scene["trackprompt_r13_active_variant_sha256"] = _canonical_sha256(variant)
    return {
        "ok": True,
        "revisionId": R13_REVISION_ID,
        "variant": variant,
        "variantSha256": scene["trackprompt_r13_active_variant_sha256"],
    }


def validate_r13_scene() -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    raw = scene.get("trackprompt_r13_lookdev_contract")
    if not isinstance(raw, str):
        return {"ok": False, "findings": ["missing-source-contract"]}
    contract = json.loads(raw)
    findings: list[str] = []
    try:
        validate_r13_contract(contract)
    except ValueError as exc:
        findings.append(str(exc))
    r13_objects = [obj for obj in bpy.data.objects if obj.name.startswith("TP_R13_")]
    subject_objects = [obj for obj in r13_objects if obj.get("trackprompt_r13_mask_group") == "subject"]
    gate_objects = [obj for obj in r13_objects if obj.get("trackprompt_r13_mask_group") == "gate"]
    architecture_objects = [
        obj for obj in r13_objects if obj.get("trackprompt_r13_mask_group") == "architecture"
    ]
    materials = [material for material in bpy.data.materials if material.name.startswith("TP_R13_")]
    if len(subject_objects) < 24:
        findings.append("protagonist-hierarchy-is-incomplete")
    if len(gate_objects) < 20:
        findings.append("gate-construction-is-incomplete")
    if len(architecture_objects) < 16:
        findings.append("chamber-construction-is-incomplete")
    if not any(obj.get("trackprompt_r13_component") == "integrated-front-aperture" for obj in subject_objects):
        findings.append("integrated-aperture-is-missing")
    if not any(obj.get("trackprompt_r13_component") == "trailing-energy-wake" for obj in subject_objects):
        findings.append("trailing-wake-is-missing")
    if scene.render.resolution_x != R13_RENDER_WIDTH or scene.render.resolution_y != R13_RENDER_HEIGHT:
        findings.append("native-vertical-resolution-is-not-active")
    if scene.get("trackprompt_r13_selected_look") != "pending-human-operator-selection":
        findings.append("look-selection-was-not-human-gated")
    return {
        "ok": not findings,
        "revisionId": R13_REVISION_ID,
        "activeVariant": scene.get("trackprompt_r13_active_variant"),
        "objectCount": len(r13_objects),
        "subjectObjectCount": len(subject_objects),
        "gateObjectCount": len(gate_objects),
        "architectureObjectCount": len(architecture_objects),
        "materialCount": len(materials),
        "variantCount": len(R13_VARIANTS),
        "renderResolution": [scene.render.resolution_x, scene.render.resolution_y],
        "selectionStatus": scene.get("trackprompt_r13_selected_look"),
        "findings": findings,
    }


def _image_downscale(source: Path, destination: Path) -> None:
    import bpy  # type: ignore[import-not-found]

    image = bpy.data.images.load(str(source), check_existing=False)
    try:
        image.scale(R13_PHONE_WIDTH, R13_PHONE_HEIGHT)
        image.filepath_raw = str(destination)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def _mask_material() -> Any:
    import bpy  # type: ignore[import-not-found]

    material = bpy.data.materials.get("TP_R13_MASK_WHITE")
    if material is None:
        material = bpy.data.materials.new("TP_R13_MASK_WHITE")
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        emission.inputs["Strength"].default_value = 1.0
        links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def _render_mask(path: Path, groups: set[str]) -> None:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    mask_material = _mask_material()
    original_resolution = (scene.render.resolution_x, scene.render.resolution_y)
    original_world = scene.world
    original_filepath = scene.render.filepath
    object_states: list[tuple[Any, bool, list[Any | None]]] = []
    mask_world = bpy.data.worlds.get("TP_R13_MASK_WORLD")
    if mask_world is None:
        mask_world = bpy.data.worlds.new("TP_R13_MASK_WORLD")
        mask_world.use_nodes = True
        background = mask_world.node_tree.nodes.get("Background")
        if background is not None:
            background.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
            background.inputs["Strength"].default_value = 0.0
    try:
        for obj in scene.objects:
            if obj.type not in {"MESH", "CURVE"}:
                continue
            materials = [slot.material for slot in obj.material_slots]
            object_states.append((obj, obj.hide_render, materials))
            keep = obj.get("trackprompt_r13_mask_group") in groups and not obj.hide_render
            obj.hide_render = not keep
            if keep:
                for slot in obj.material_slots:
                    slot.material = mask_material
        scene.world = mask_world
        scene.render.resolution_x = R13_PHONE_WIDTH
        scene.render.resolution_y = R13_PHONE_HEIGHT
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
    finally:
        for obj, hidden, materials in object_states:
            obj.hide_render = hidden
            for slot, material in zip(obj.material_slots, materials, strict=False):
                slot.material = material
        scene.world = original_world
        scene.render.resolution_x, scene.render.resolution_y = original_resolution
        scene.render.filepath = original_filepath


def _file_reference(root: Path, path: Path) -> dict[str, object]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "file": path.relative_to(root).as_posix(),
        "sha256": digest,
        "sizeBytes": path.stat().st_size,
    }


def render_r13_lookdev_variants(
    output_directory: str,
    snapshot_directory: str,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    if scene.get("trackprompt_r13_revision") != R13_REVISION_ID:
        raise ValueError("Build the R13 look-development scene before rendering variants.")
    output = Path(output_directory).expanduser().resolve()
    snapshots = Path(snapshot_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshots.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for variant in R13_VARIANTS:
        state = apply_r13_variant(str(variant["id"]))
        validation = validate_r13_scene()
        if not validation["ok"]:
            raise ValueError("R13 scene validation failed before a variant render.")
        variant_dir = output / str(variant["id"])
        phone_dir = variant_dir / "phone"
        mask_dir = variant_dir / "masks"
        variant_dir.mkdir(parents=True, exist_ok=True)
        phone_dir.mkdir(parents=True, exist_ok=True)
        mask_dir.mkdir(parents=True, exist_ok=True)
        beauty = variant_dir / "beauty-1080x1920.png"
        phone = phone_dir / "beauty-180x320.png"
        subject_mask = mask_dir / "subject-180x320.png"
        gate_mask = mask_dir / "gate-180x320.png"
        scene.render.resolution_x = R13_RENDER_WIDTH
        scene.render.resolution_y = R13_RENDER_HEIGHT
        scene.render.filepath = str(beauty)
        bpy.ops.render.render(write_still=True)
        if not beauty.is_file() or beauty.stat().st_size <= 0:
            raise RuntimeError("Blender did not write the R13 look-development still.")
        _image_downscale(beauty, phone)
        _render_mask(subject_mask, set(variant["subjectMaskGroups"]))
        gate_groups = set(variant["gateMaskGroups"])
        if gate_groups:
            _render_mask(gate_mask, gate_groups)
        snapshot = snapshots / f"{variant['id']}.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(snapshot), check_existing=False)
        record = {
            "id": variant["id"],
            "kind": variant["kind"],
            "label": variant["label"],
            "variantSha256": state["variantSha256"],
            "cameraRigId": variant["cameraRigId"],
            "lightingRigId": variant["lightingRigId"],
            "intentionalBlackout": variant["intentionalBlackout"],
            "beauty": _file_reference(output, beauty),
            "phone": _file_reference(output, phone),
            "subjectMask": _file_reference(output, subject_mask),
            "gateMask": _file_reference(output, gate_mask) if gate_groups else None,
            "snapshot": {
                "file": snapshot.relative_to(snapshots.parent).as_posix(),
                "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                "sizeBytes": snapshot.stat().st_size,
            },
            "validation": validation,
        }
        records.append(record)
    manifest = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r13-lookdev-render-manifest",
        "revisionId": R13_REVISION_ID,
        "contractSha256": scene["trackprompt_r13_contract_sha256"],
        "nativeVertical": {"width": R13_RENDER_WIDTH, "height": R13_RENDER_HEIGHT},
        "phoneReview": {"width": R13_PHONE_WIDTH, "height": R13_PHONE_HEIGHT, "crop": False},
        "variants": records,
        "selection": r13_lookdev_contract()["selection"],
        "motionTest": r13_lookdev_contract()["motionTest"],
        "generatedMediaCommitted": False,
    }
    manifest_path = output / "r13-lookdev-render-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "ok": len(records) == len(R13_VARIANTS),
        "revisionId": R13_REVISION_ID,
        "outputDirectory": str(output),
        "snapshotDirectory": str(snapshots),
        "manifest": str(manifest_path),
        "variantCount": len(records),
        "variants": records,
        "selectionStatus": "pending-human-operator-selection",
        "motionTestStatus": "blocked-pending-look-selection",
    }

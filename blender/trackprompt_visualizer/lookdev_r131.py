from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


R131_REVISION_ID = "andromeda-r13.1-selected-refinement"
R131_ROOT_COLLECTION = "TP_R131_REFINEMENT"
R131_CAMERA_NAME = "TP_R131_CAMERA"
R131_HERO_NAME = "TP_R131_HERO_ROOT"
R131_GATE_CONTROLLER_NAME = "TP_R131_GATE_CONTROLLER"
R131_FRAME_START = 1
R131_FRAME_END = 120
R131_FPS = 30
R131_WIDTH = 1080
R131_HEIGHT = 1920
R131_PHONE_WIDTH = 180
R131_PHONE_HEIGHT = 320

R131_SELECTION: dict[str, object] = {
    "protagonistDesign": "protagonist-b-ancient-engine",
    "architecturalMaterialLanguage": "weathered-stone-metal-crystal-v1",
    "gateConstruction": "nested-ring-monolith-v1",
    "exposureLightingTreatment": "restrained-teal-cyan-amber-v1",
    "status": "selected-for-refinement",
    "artistApproved": False,
    "humanArtistApproval": "pending",
}

R131_EVENT_FRAMES: dict[str, int] = {
    "approachEstablished": 24,
    "cameraLagReadable": 42,
    "foregroundParallax": 55,
    "anticipationResponse": 62,
    "gateAligned": 72,
    "localizedCompressionPeak": 80,
    "crossingComplete": 92,
    "formRecovered": 106,
    "sealingBegins": 118,
}

R131_REVIEW_STATES: tuple[dict[str, object], ...] = (
    {
        "id": "selected-protagonist-orientation",
        "frame": 12,
        "criteria": ["protagonist-orientation", "silhouette-clarity", "material-noise"],
    },
    {
        "id": "independent-movement-camera-lag",
        "frame": 42,
        "criteria": ["independent-movement", "camera-lag"],
    },
    {
        "id": "foreground-parallax",
        "frame": 55,
        "criteria": ["parallax", "exposure"],
    },
    {
        "id": "selected-gate-depth",
        "frame": 64,
        "criteria": ["gate-depth", "story-clarity"],
    },
    {
        "id": "crossing-anticipation",
        "frame": 70,
        "criteria": ["protagonist-orientation", "story-clarity"],
    },
    {
        "id": "localized-compression",
        "frame": 80,
        "criteria": ["compression-readability", "exposure"],
    },
    {
        "id": "post-crossing-recovery",
        "frame": 112,
        "criteria": ["post-crossing-recovery", "silhouette-clarity"],
    },
    {
        "id": "gate-sealing",
        "frame": 118,
        "criteria": ["gate-sealing", "story-clarity"],
    },
)


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def r131_contract() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "revisionId": R131_REVISION_ID,
        "sourceRevision": "andromeda-r13-lookdev-lock",
        "previewOnly": True,
        "selection": dict(R131_SELECTION),
        "frameRange": {
            "start": R131_FRAME_START,
            "end": R131_FRAME_END,
            "fps": R131_FPS,
            "durationSeconds": 4.0,
        },
        "render": {
            "renderer": "BLENDER_EEVEE",
            "width": R131_WIDTH,
            "height": R131_HEIGHT,
            "finalTemporalSamples": 64,
            "comparisonTemporalSamples": 8,
            "volumetricSamples": 32,
            "denoising": "temporal-antialiasing-no-compositor-denoise",
            "transparencyMode": "DITHERED",
            "motionBlur": False,
            "transparentLayerMaximum": 2,
        },
        "protagonist": {
            "base": "protagonist-b-ancient-engine",
            "silhouetteReference": "protagonist-a-directional-shell",
            "majorArmorBands": 1,
            "transparentAtmosphereLayers": 1,
            "integratedFrontAperture": True,
            "asymmetricOrientationCues": True,
            "restrainedRearWake": True,
            "wireCage": False,
            "boundedCompression": True,
        },
        "architecture": {
            "materialLanguage": "weathered-stone-metal-crystal-v1",
            "connectedSupports": True,
            "railsAndConduits": True,
            "repeatedStructuralRhythm": True,
            "functionalCrystalRouting": True,
            "floatingPanelField": False,
        },
        "gate": {
            "construction": "nested-ring-monolith-v1",
            "outerMonolith": True,
            "movingLockRings": True,
            "localizedMembrane": True,
            "destinationDepth": True,
            "closingMechanism": True,
            "uniformCyanWash": False,
        },
        "motion": {
            "cameraCuts": 0,
            "rawAudioMacroMotion": False,
            "independentProtagonistPath": True,
            "controlledCameraLag": True,
            "foregroundParallax": True,
            "events": dict(R131_EVENT_FRAMES),
        },
        "reviewStates": [dict(item) for item in R131_REVIEW_STATES],
        "humanArtistApproval": "pending",
        "artistApproved": False,
        "calibrationReadiness": "blocked",
        "productionAuthorization": False,
    }


def validate_r131_contract(contract: Mapping[str, object]) -> None:
    selection = contract.get("selection")
    if selection != R131_SELECTION:
        raise ValueError("R13.1 must preserve the exact provisional refinement selection.")
    frame_range = contract.get("frameRange")
    if not isinstance(frame_range, Mapping) or frame_range.get("durationSeconds") != 4.0:
        raise ValueError("R13.1 motion proof must be exactly four seconds.")
    protagonist = contract.get("protagonist")
    if not isinstance(protagonist, Mapping):
        raise ValueError("R13.1 protagonist contract is missing.")
    if protagonist.get("majorArmorBands") != 1 or protagonist.get("wireCage") is not False:
        raise ValueError("R13.1 must use one restrained armor band and no wire cage.")
    render = contract.get("render")
    if not isinstance(render, Mapping) or int(render.get("finalTemporalSamples", 0)) < 64:
        raise ValueError("R13.1 final media requires at least 64 temporal samples.")
    if contract.get("artistApproved") is not False or contract.get("humanArtistApproval") != "pending":
        raise ValueError("R13.1 cannot record artistic approval automatically.")
    if contract.get("calibrationReadiness") != "blocked":
        raise ValueError("R13.1 calibration must remain blocked.")
    if contract.get("productionAuthorization") is not False:
        raise ValueError("R13.1 cannot authorize production.")


def _remove_existing_r131() -> None:
    import bpy  # type: ignore[import-not-found]

    for obj in list(bpy.data.objects):
        if obj.name.startswith("TP_R131_"):
            bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        if collection.name.startswith("TP_R131_"):
            bpy.data.collections.remove(collection)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.cameras, bpy.data.lights):
        for datablock in list(datablocks):
            if datablock.name.startswith("TP_R131_") and datablock.users == 0:
                datablocks.remove(datablock)
    for material in list(bpy.data.materials):
        if material.name.startswith("TP_R131_") and material.users == 0:
            bpy.data.materials.remove(material)


def _collection(name: str, parent: Any) -> Any:
    import bpy  # type: ignore[import-not-found]

    result = bpy.data.collections.new(name)
    parent.children.link(result)
    return result


def _mark(obj: Any, semantic: str, component: str) -> Any:
    obj["trackprompt_revision"] = "r13.1"
    obj["trackprompt_r131_semantic"] = semantic
    obj["trackprompt_r131_component"] = component
    obj["trackprompt_r131_mask_group"] = semantic
    return obj


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
    bevel: float = 0.16,
) -> Any:
    from .lookdev_r13 import _cube as create

    return _mark(
        create(
            name,
            collection,
            location=location,
            dimensions=dimensions,
            material=material,
            semantic=semantic,
            component=component,
            rotation=rotation,
            bevel=bevel,
        ),
        semantic,
        component,
    )


def _sphere(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    scale: tuple[float, float, float],
    material: Any,
    semantic: str,
    component: str,
    subdivisions: int = 3,
) -> Any:
    from .lookdev_r13 import _sphere as create

    return _mark(
        create(
            name,
            collection,
            location=location,
            scale=scale,
            material=material,
            semantic=semantic,
            component=component,
            subdivisions=subdivisions,
        ),
        semantic,
        component,
    )


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
) -> Any:
    from .lookdev_r13 import _torus as create

    return _mark(
        create(
            name,
            collection,
            location=location,
            major_radius=major_radius,
            minor_radius=minor_radius,
            material=material,
            semantic=semantic,
            component=component,
            rotation=rotation,
            scale=scale,
        ),
        semantic,
        component,
    )


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
) -> Any:
    from .lookdev_r13 import _cylinder as create

    return _mark(
        create(
            name,
            collection,
            location=location,
            radius=radius,
            depth=depth,
            material=material,
            semantic=semantic,
            component=component,
            rotation=rotation,
        ),
        semantic,
        component,
    )


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
    rotation: tuple[float, float, float],
) -> Any:
    from .lookdev_r13 import _cone as create

    return _mark(
        create(
            name,
            collection,
            location=location,
            radius1=radius1,
            radius2=radius2,
            depth=depth,
            material=material,
            semantic=semantic,
            component=component,
            rotation=rotation,
        ),
        semantic,
        component,
    )


def _empty(name: str, collection: Any, location: tuple[float, float, float]) -> Any:
    from .lookdev_r13 import _empty as create

    return _mark(create(name, collection, location), "control", "animation-control")


def _beam_between(
    name: str,
    collection: Any,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    width: float,
    material: Any,
    semantic: str,
    component: str,
) -> Any:
    from mathutils import Vector  # type: ignore[import-not-found]

    beginning = Vector(start)
    ending = Vector(end)
    direction = ending - beginning
    midpoint = (beginning + ending) * 0.5
    rotation = direction.to_track_quat("Z", "Y").to_euler()
    return _cube(
        name,
        collection,
        location=tuple(midpoint),
        dimensions=(width, width, direction.length),
        material=material,
        semantic=semantic,
        component=component,
        rotation=tuple(rotation),
        bevel=width * 0.35,
    )


def _materials() -> dict[str, Any]:
    from .lookdev_r13 import _accent_material, _physical_material

    return {
        "shell": _physical_material(
            "TP_R131_HERO_DARK_SHELL",
            (0.025, 0.04, 0.055, 1.0),
            metallic=0.66,
            roughness=0.31,
            noise_scale=7.0,
            bump_strength=0.055,
        ),
        "armor": _physical_material(
            "TP_R131_HERO_LIMITED_ARMOR",
            (0.09, 0.095, 0.11, 1.0),
            metallic=0.76,
            roughness=0.27,
            noise_scale=9.0,
            bump_strength=0.045,
        ),
        "core": _accent_material(
            "TP_R131_HERO_LUMINOUS_CORE",
            (0.025, 0.18, 0.22, 1.0),
            (0.08, 0.72, 0.95, 1.0),
            1.25,
        ),
        "atmosphere": _accent_material(
            "TP_R131_HERO_ATMOSPHERE",
            (0.035, 0.16, 0.2, 1.0),
            (0.07, 0.42, 0.55, 1.0),
            0.12,
            alpha=0.075,
            transmission=0.26,
        ),
        "aperture": _accent_material(
            "TP_R131_HERO_APERTURE",
            (0.18, 0.035, 0.24, 1.0),
            (0.76, 0.16, 0.98, 1.0),
            1.32,
        ),
        "orientation": _accent_material(
            "TP_R131_HERO_ORIENTATION",
            (0.035, 0.18, 0.2, 1.0),
            (0.08, 0.86, 0.92, 1.0),
            0.72,
        ),
        "wake": _accent_material(
            "TP_R131_HERO_WAKE",
            (0.025, 0.12, 0.17, 1.0),
            (0.04, 0.5, 0.7, 1.0),
            0.25,
            alpha=0.12,
            transmission=0.12,
        ),
        "stone": _physical_material(
            "TP_R131_WEATHERED_STONE",
            (0.045, 0.052, 0.06, 1.0),
            metallic=0.16,
            roughness=0.69,
            noise_scale=3.8,
            bump_strength=0.2,
        ),
        "metal": _physical_material(
            "TP_R131_ANCIENT_METAL",
            (0.06, 0.075, 0.085, 1.0),
            metallic=0.74,
            roughness=0.42,
            noise_scale=10.0,
            bump_strength=0.1,
        ),
        "recess": _physical_material(
            "TP_R131_LOCK_RECESS",
            (0.009, 0.014, 0.019, 1.0),
            metallic=0.45,
            roughness=0.6,
            noise_scale=12.0,
            bump_strength=0.05,
        ),
        "amber": _accent_material(
            "TP_R131_AMBER_ROUTING",
            (0.18, 0.065, 0.01, 1.0),
            (0.9, 0.28, 0.025, 1.0),
            0.64,
        ),
        "crystal": _accent_material(
            "TP_R131_FUNCTIONAL_CRYSTAL",
            (0.018, 0.12, 0.13, 1.0),
            (0.05, 0.55, 0.48, 1.0),
            0.46,
        ),
        "membrane": _accent_material(
            "TP_R131_LOCAL_MEMBRANE",
            (0.012, 0.11, 0.12, 1.0),
            (0.035, 0.32, 0.3, 1.0),
            0.06,
            alpha=0.035,
            transmission=0.62,
        ),
        "destination": _accent_material(
            "TP_R131_DESTINATION_DEPTH",
            (0.014, 0.035, 0.075, 1.0),
            (0.025, 0.16, 0.38, 1.0),
            0.34,
        ),
    }


def _build_hero(collection: Any, materials: Mapping[str, Any]) -> Any:
    from .lookdev_r13 import _parent

    root = _empty(R131_HERO_NAME, collection, (0.0, 3.8, 3.45))
    root["trackprompt_r131_front_axis"] = "local-negative-y"
    root["trackprompt_r131_base_design"] = "protagonist-b-ancient-engine"
    root["trackprompt_r131_silhouette_reference"] = "protagonist-a-directional-shell"
    parts: list[Any] = []
    parts.append(
        _sphere(
            "TP_R131_HERO_SHELL",
            collection,
            location=(0.0, 0.0, 0.0),
            scale=(1.35, 1.62, 1.03),
            material=materials["shell"],
            semantic="subject",
            component="dark-structural-shell",
        )
    )
    parts.append(
        _sphere(
            "TP_R131_HERO_CORE",
            collection,
            location=(0.0, -0.22, 0.0),
            scale=(0.57, 0.74, 0.54),
            material=materials["core"],
            semantic="subject",
            component="luminous-internal-core",
        )
    )
    parts.append(
        _sphere(
            "TP_R131_HERO_ATMOSPHERE",
            collection,
            location=(0.0, 0.0, 0.0),
            scale=(1.48, 1.76, 1.14),
            material=materials["atmosphere"],
            semantic="subject",
            component="single-translucent-atmosphere",
        )
    )
    parts.append(
        _torus(
            "TP_R131_HERO_ARMOR_BAND",
            collection,
            location=(0.0, 0.28, 0.0),
            major_radius=1.18,
            minor_radius=0.055,
            material=materials["armor"],
            semantic="subject",
            component="single-restrained-armor-band",
            rotation=(math.pi / 2.0, 0.0, 0.0),
            scale=(1.0, 1.0, 0.76),
        )
    )
    aperture = _cylinder(
        "TP_R131_HERO_APERTURE",
        collection,
        location=(0.0, -1.48, 0.0),
        radius=0.56,
        depth=0.34,
        material=materials["aperture"],
        semantic="subject",
        component="unmistakable-integrated-front-aperture",
        rotation=(math.pi / 2.0, 0.0, 0.0),
    )
    parts.append(aperture)
    parts.append(
        _torus(
            "TP_R131_HERO_APERTURE_COLLAR",
            collection,
            location=(0.0, -1.61, 0.0),
            major_radius=0.64,
            minor_radius=0.075,
            material=materials["armor"],
            semantic="subject",
            component="aperture-structural-collar",
            rotation=(math.pi / 2.0, 0.0, 0.0),
        )
    )
    parts.append(
        _cube(
            "TP_R131_HERO_LEADING_MARKER",
            collection,
            location=(-0.34, -1.54, 0.82),
            dimensions=(0.32, 0.24, 0.22),
            material=materials["orientation"],
            semantic="subject",
            component="asymmetric-leading-edge-marker",
            rotation=(0.18, -0.08, -0.12),
            bevel=0.07,
        )
    )
    for side, z_value in ((-1.0, 0.56), (1.0, -0.32)):
        parts.append(
            _cube(
                f"TP_R131_HERO_DIRECTIONAL_FIN_{'L' if side < 0 else 'R'}",
                collection,
                location=(side * 0.72, 0.38, z_value),
                dimensions=(0.16, 1.02, 0.46),
                material=materials["armor"],
                semantic="subject",
                component="asymmetric-directional-fin",
                rotation=(0.0, side * 0.14, side * 0.12),
                bevel=0.055,
            )
        )
    for side in (-1.0, 1.0):
        parts.append(
            _cube(
                f"TP_R131_HERO_REAR_POD_{'L' if side < 0 else 'R'}",
                collection,
                location=(side * 0.94, 0.72, -0.08),
                dimensions=(0.24, 0.68, 0.42),
                material=materials["armor"],
                semantic="subject",
                component="limited-rear-propulsion-pod",
                rotation=(0.0, side * 0.12, side * 0.08),
                bevel=0.065,
            )
        )
    for index in range(3):
        parts.append(
            _cone(
                f"TP_R131_HERO_WAKE_{index:02d}",
                collection,
                location=(0.0, 1.62 + index * 0.56, -0.04 + index * 0.04),
                radius1=0.38 - index * 0.09,
                radius2=0.15 - index * 0.035,
                depth=0.95,
                material=materials["wake"],
                semantic="subject",
                component="restrained-rear-energy-wake",
                rotation=(math.pi / 2.0, 0.0, 0.0),
            )
        )
    _parent(parts, root)
    root["trackprompt_r131_deformation_amount"] = 0.0
    aperture["trackprompt_r131_aperture_response"] = 0.0
    return root


def _build_architecture(collection: Any, materials: Mapping[str, Any]) -> dict[str, Any]:
    foreground: Any | None = None
    for bay, y_value in enumerate((4.4, 1.4, -1.5)):
        for side in (-1.0, 1.0):
            x_value = side * 4.35
            if bay == 0 and side > 0:
                x_value = 5.8
            pylon = _cube(
                f"TP_R131_ARCH_PYLON_{bay:02d}_{'L' if side < 0 else 'R'}",
                collection,
                location=(x_value, y_value, 3.55),
                dimensions=(1.2, 1.3, 7.1),
                material=materials["stone"],
                semantic="architecture",
                component="repeated-load-bearing-pylon",
            )
            if bay == 0 and side > 0:
                pylon.name = "TP_R131_ARCH_FOREGROUND_PYLON_R"
                pylon["trackprompt_r131_component"] = "foreground-parallax-occluder"
                foreground = pylon
            _cube(
                f"TP_R131_ARCH_PYLON_RECESS_{bay:02d}_{'L' if side < 0 else 'R'}",
                collection,
                location=(x_value - side * 0.63, y_value - 0.06, 3.55),
                dimensions=(0.16, 0.72, 4.7),
                material=materials["recess"],
                semantic="architecture",
                component="locking-surface-recess",
                bevel=0.03,
            )
        _cube(
            f"TP_R131_ARCH_OVERHEAD_RAIL_{bay:02d}",
            collection,
            location=(0.0, y_value, 7.05),
            dimensions=(12.0 if bay == 0 else 9.25, 1.15, 0.72),
            material=materials["metal"],
            semantic="architecture",
            component="connected-overhead-load-rail",
        )
        for side in (-1.0, 1.0):
            hinge = _cylinder(
                f"TP_R131_ARCH_HINGE_{bay:02d}_{'L' if side < 0 else 'R'}",
                collection,
                location=(side * 3.75, y_value - 0.52, 5.75),
                radius=0.24,
                depth=0.42,
                material=materials["metal"],
                semantic="architecture",
                component="visible-mechanical-hinge",
                rotation=(math.pi / 2.0, 0.0, 0.0),
            )
            hinge["trackprompt_r131_bay"] = bay
    for side in (-1.0, 1.0):
        _beam_between(
            f"TP_R131_ARCH_LONGITUDINAL_RAIL_{'L' if side < 0 else 'R'}",
            collection,
            (side * 3.72, 5.4, 1.3),
            (side * 3.72, -3.1, 1.3),
            0.22,
            materials["metal"],
            "architecture",
            "continuous-longitudinal-rail",
        )
        _beam_between(
            f"TP_R131_ARCH_CONDUIT_{'L' if side < 0 else 'R'}",
            collection,
            (side * 3.58, 4.5, 5.8),
            (side * 3.1, -3.25, 4.3),
            0.09,
            materials["amber"],
            "architecture",
            "functional-energy-conduit",
        )
    for index, (x_value, y_value, z_value) in enumerate(
        ((-3.55, 3.9, 5.8), (3.55, 1.0, 5.1), (-3.5, -1.9, 4.45))
    ):
        crystal = _sphere(
            f"TP_R131_ARCH_ROUTING_CRYSTAL_{index:02d}",
            collection,
            location=(x_value, y_value, z_value),
            scale=(0.18, 0.13, 0.42),
            material=materials["crystal"],
            semantic="architecture",
            component="functional-energy-routing-crystal",
            subdivisions=1,
        )
        crystal["trackprompt_r131_function"] = "sense-route-gate-alignment-energy"
    if foreground is None:
        raise RuntimeError("R13.1 foreground parallax object was not built.")
    return {"foreground": foreground}


def _build_gate(collection: Any, materials: Mapping[str, Any]) -> dict[str, Any]:
    gate_y = -4.0
    controller = _empty(R131_GATE_CONTROLLER_NAME, collection, (0.0, gate_y, 3.65))
    controller["trackprompt_r131_gate_alignment_progress"] = 0.0
    controller["trackprompt_r131_membrane_compression"] = 0.0
    controller["trackprompt_r131_seal_progress"] = 0.0
    for side in (-1.0, 1.0):
        _cube(
            f"TP_R131_GATE_MONOLITH_{'L' if side < 0 else 'R'}",
            collection,
            location=(side * 3.75, gate_y, 3.7),
            dimensions=(2.3, 3.0, 8.4),
            material=materials["stone"],
            semantic="gate",
            component="dark-thick-outer-monolith",
        )
        _cube(
            f"TP_R131_GATE_INNER_THICKNESS_{'L' if side < 0 else 'R'}",
            collection,
            location=(side * 2.58, gate_y - 0.15, 3.7),
            dimensions=(0.26, 2.2, 6.2),
            material=materials["metal"],
            semantic="gate",
            component="visible-ring-structural-thickness",
            bevel=0.05,
        )
        _cube(
            f"TP_R131_GATE_BUTTRESS_{'L' if side < 0 else 'R'}",
            collection,
            location=(side * 5.05, gate_y + 0.55, 1.25),
            dimensions=(2.0, 3.6, 1.35),
            material=materials["metal"],
            semantic="gate",
            component="load-transfer-buttress",
            rotation=(0.0, side * 0.36, 0.0),
        )
    _cube(
        "TP_R131_GATE_LINTEL",
        collection,
        location=(0.0, gate_y, 8.05),
        dimensions=(9.6, 3.0, 1.45),
        material=materials["stone"],
        semantic="gate",
        component="connected-monolith-lintel",
    )
    rings: list[Any] = []
    for index, (y_offset, radius, minor) in enumerate(
        ((0.42, 2.78, 0.18), (0.0, 2.38, 0.12), (-0.42, 2.04, 0.085))
    ):
        ring = _torus(
            f"TP_R131_GATE_LOCK_RING_{index:02d}",
            collection,
            location=(0.0, gate_y + y_offset, 3.7),
            major_radius=radius,
            minor_radius=minor,
            material=materials["metal" if index != 1 else "crystal"],
            semantic="gate",
            component="moving-lock-ring",
            rotation=(math.pi / 2.0, 0.0, index * 0.22),
        )
        ring["trackprompt_r131_ring_index"] = index
        rings.append(ring)
    membrane = _cylinder(
        "TP_R131_GATE_LOCAL_MEMBRANE",
        collection,
        location=(0.0, gate_y - 0.12, 3.7),
        radius=1.78,
        depth=0.045,
        material=materials["membrane"],
        semantic="gate",
        component="localized-inner-membrane",
        rotation=(math.pi / 2.0, 0.0, 0.0),
    )
    destination: list[Any] = []
    for index, y_value in enumerate((-6.4, -8.7, -11.0)):
        destination.append(
            _torus(
                f"TP_R131_DESTINATION_DEPTH_RING_{index:02d}",
                collection,
                location=(0.0, y_value, 3.7),
                major_radius=1.72 - index * 0.24,
                minor_radius=0.035,
                material=materials["destination"],
                semantic="destination",
                component="destination-depth-ring",
                rotation=(math.pi / 2.0, 0.0, index * 0.28),
            )
        )
    beacon = _sphere(
        "TP_R131_DESTINATION_BEACON",
        collection,
        location=(-0.5, -12.8, 4.15),
        scale=(0.3, 0.18, 0.3),
        material=materials["destination"],
        semantic="destination",
        component="destination-depth-beacon",
        subdivisions=2,
    )
    destination.append(beacon)
    for index, (x_value, z_value) in enumerate(
        ((-1.2, 2.65), (0.8, 3.1), (1.4, 4.8), (-0.35, 5.25), (0.25, 2.1))
    ):
        destination.append(
            _sphere(
                f"TP_R131_DESTINATION_MARKER_{index:02d}",
                collection,
                location=(x_value, -7.0 - index * 0.7, z_value),
                scale=(0.055, 0.035, 0.055),
                material=materials["orientation"],
                semantic="destination",
                component="destination-parallax-marker",
                subdivisions=1,
            )
        )
    locks: list[Any] = []
    for side in (-1.0, 1.0):
        side_lock = _cube(
            f"TP_R131_GATE_SIDE_LOCK_{'L' if side < 0 else 'R'}",
            collection,
            location=(side * 2.15, gate_y - 0.72, 3.7),
            dimensions=(1.0, 0.72, 1.5),
            material=materials["metal"],
            semantic="gate",
            component="moving-seal-lock",
            rotation=(0.0, 0.0, side * 0.1),
        )
        side_lock["trackprompt_r131_lock_axis"] = "x"
        side_lock["trackprompt_r131_lock_side"] = side
        locks.append(side_lock)
    for side in (-1.0, 1.0):
        vertical_lock = _cube(
            f"TP_R131_GATE_VERTICAL_LOCK_{'B' if side < 0 else 'T'}",
            collection,
            location=(0.0, gate_y - 0.78, 3.7 + side * 2.15),
            dimensions=(1.35, 0.68, 0.92),
            material=materials["metal"],
            semantic="gate",
            component="moving-seal-lock",
        )
        vertical_lock["trackprompt_r131_lock_axis"] = "z"
        vertical_lock["trackprompt_r131_lock_side"] = side
        locks.append(vertical_lock)
    _cube(
        "TP_R131_GATE_SEAL_SEAM",
        collection,
        location=(0.0, gate_y - 0.84, 3.7),
        dimensions=(0.12, 0.18, 5.0),
        material=materials["amber"],
        semantic="gate",
        component="closing-seal-seam",
        bevel=0.025,
    )
    return {
        "controller": controller,
        "rings": rings,
        "membrane": membrane,
        "locks": locks,
        "destination": destination,
        "beacon": beacon,
    }


def _light(
    name: str,
    collection: Any,
    location: tuple[float, float, float],
    energy: float,
    color: tuple[float, float, float],
    size: float,
    target: tuple[float, float, float],
) -> Any:
    from .lookdev_r13 import _aim, _light as create

    result = create(
        name,
        collection,
        location=location,
        energy=energy,
        color=color,
        size=size,
    )
    _mark(result, "lighting", "authored-light")
    _aim(result, target)
    return result


def _keyframe_transform(
    obj: Any,
    frame: int,
    *,
    location: Sequence[float] | None = None,
    scale: Sequence[float] | None = None,
    rotation: Sequence[float] | None = None,
) -> None:
    if location is not None:
        obj.location = tuple(location)
        obj.keyframe_insert(data_path="location", frame=frame)
    if scale is not None:
        obj.scale = tuple(scale)
        obj.keyframe_insert(data_path="scale", frame=frame)
    if rotation is not None:
        obj.rotation_euler = tuple(rotation)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)


def _keyframe_property(obj: Any, name: str, frame: int, value: float) -> None:
    obj[name] = value
    obj.keyframe_insert(data_path=f'["{name}"]', frame=frame)


def _smooth_animation(objects: Sequence[Any]) -> None:
    from .curve_importer import iter_action_fcurves

    for obj in objects:
        animation = getattr(obj, "animation_data", None)
        action = getattr(animation, "action", None) if animation is not None else None
        if action is None:
            continue
        for curve in iter_action_fcurves(action):
            for point in curve.keyframe_points:
                point.interpolation = "BEZIER"
                point.handle_left_type = "AUTO_CLAMPED"
                point.handle_right_type = "AUTO_CLAMPED"


def _animate(hero: Any, aperture: Any, camera: Any, target: Any, gate: Mapping[str, Any]) -> None:
    hero_keys = (
        (1, (0.2, 3.8, 3.45), (1.0, 1.0, 1.0), (0.02, -0.08, 0.42)),
        (24, (0.05, 2.15, 3.5), (1.0, 1.02, 0.99), (0.01, -0.06, 0.38)),
        (42, (-0.08, 0.95, 3.55), (1.0, 1.03, 0.99), (0.0, -0.04, 0.34)),
        (55, (-0.1, 0.15, 3.58), (1.0, 0.98, 1.01), (0.0, -0.02, 0.3)),
        (62, (-0.08, -0.25, 3.6), (1.02, 0.94, 1.02), (0.0, 0.0, 0.27)),
        (72, (-0.03, -1.35, 3.62), (1.05, 0.88, 1.04), (0.0, 0.02, 0.24)),
        (80, (0.02, -3.7, 3.67), (1.14, 0.68, 1.1), (0.0, 0.03, 0.2)),
        (92, (-0.12, -5.05, 3.72), (1.06, 0.86, 1.05), (-0.01, 0.04, 0.16)),
        (106, (-0.32, -6.65, 3.78), (1.0, 1.0, 1.0), (-0.015, 0.06, 0.12)),
        (120, (-0.5, -8.05, 3.82), (1.0, 1.01, 1.0), (-0.02, 0.07, 0.1)),
    )
    for frame, location, scale, rotation in hero_keys:
        _keyframe_transform(hero, frame, location=location, scale=scale, rotation=rotation)
    for frame, value in ((1, 0.0), (55, 0.0), (62, 0.25), (80, 1.0), (106, 0.0), (120, 0.0)):
        _keyframe_property(hero, "trackprompt_r131_deformation_amount", frame, value)
    for frame, scale, response in (
        (1, (1.0, 1.0, 1.0), 0.0),
        (55, (1.0, 1.0, 1.0), 0.0),
        (62, (1.18, 1.18, 1.0), 0.8),
        (80, (0.92, 0.92, 1.0), 0.45),
        (106, (1.0, 1.0, 1.0), 0.0),
    ):
        _keyframe_transform(aperture, frame, scale=scale)
        _keyframe_property(aperture, "trackprompt_r131_aperture_response", frame, response)
    camera_keys = (
        (1, (7.9, 12.2, 6.5)),
        (24, (7.45, 10.75, 6.25)),
        (42, (6.85, 8.75, 5.95)),
        (55, (6.25, 7.1, 5.68)),
        (72, (5.55, 5.25, 5.4)),
        (80, (5.05, 3.95, 5.22)),
        (92, (6.5, 1.25, 5.0)),
        (100, (8.0, -2.8, 5.2)),
        (104, (9.0, -5.3, 5.3)),
        (108, (10.0, -7.8, 5.38)),
        (112, (10.0, -10.0, 5.4)),
        (120, (9.0, -13.0, 5.35)),
    )
    for frame, location in camera_keys:
        _keyframe_transform(camera, frame, location=location)
    target_keys = (
        (1, (0.0, 2.55, 3.5)),
        (24, (0.0, 1.25, 3.52)),
        (42, (-0.04, 0.35, 3.55)),
        (55, (-0.06, -0.35, 3.58)),
        (72, (-0.04, -1.5, 3.62)),
        (80, (0.0, -2.75, 3.67)),
        (92, (-0.08, -4.55, 3.72)),
        (100, (-0.12, -5.35, 3.75)),
        (104, (-0.18, -5.8, 3.77)),
        (108, (-0.24, -6.15, 3.78)),
        (112, (-0.24, -6.2, 3.78)),
        (120, (-0.2, -6.0, 3.75)),
    )
    for frame, location in target_keys:
        _keyframe_transform(target, frame, location=location)
    for index, ring in enumerate(gate["rings"]):
        start_rotation = (math.pi / 2.0, 0.0, index * 0.22)
        aligned_rotation = (math.pi / 2.0, (index - 1) * 0.18, 0.0)
        sealed_rotation = (math.pi / 2.0, (index - 1) * 0.34, (-1) ** index * 0.28)
        _keyframe_transform(ring, 1, rotation=start_rotation)
        _keyframe_transform(ring, 55, rotation=start_rotation)
        _keyframe_transform(ring, 72, rotation=aligned_rotation)
        _keyframe_transform(ring, 92, rotation=aligned_rotation)
        _keyframe_transform(ring, 120, rotation=sealed_rotation, scale=(0.88, 0.88, 1.0))
    membrane = gate["membrane"]
    for frame, scale in (
        (1, (0.18, 0.18, 1.0)),
        (55, (0.18, 0.18, 1.0)),
        (72, (1.0, 1.0, 1.0)),
        (80, (0.9, 0.74, 1.0)),
        (92, (1.0, 1.0, 1.0)),
        (120, (0.72, 0.72, 1.0)),
    ):
        _keyframe_transform(membrane, frame, scale=scale)
    for lock in gate["locks"]:
        axis = lock["trackprompt_r131_lock_axis"]
        side = float(lock["trackprompt_r131_lock_side"])
        start = tuple(lock.location)
        _keyframe_transform(lock, 1, location=start)
        _keyframe_transform(lock, 92, location=start)
        if axis == "x":
            end = (side * 0.72, start[1], start[2])
        else:
            end = (start[0], start[1], 3.7 + side * 0.72)
        _keyframe_transform(lock, 120, location=end)
    controller = gate["controller"]
    for frame, value in ((1, 0.0), (55, 0.0), (72, 1.0), (120, 1.0)):
        _keyframe_property(controller, "trackprompt_r131_gate_alignment_progress", frame, value)
    for frame, value in ((1, 0.0), (72, 0.0), (80, 1.0), (92, 0.0), (120, 0.0)):
        _keyframe_property(controller, "trackprompt_r131_membrane_compression", frame, value)
    for frame, value in ((1, 0.0), (92, 0.0), (106, 0.12), (118, 0.4), (120, 0.48)):
        _keyframe_property(controller, "trackprompt_r131_seal_progress", frame, value)
    _smooth_animation(
        [hero, aperture, camera, target, controller, membrane, *gate["rings"], *gate["locks"]]
    )


def _configure_render() -> dict[str, object]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = R131_WIDTH
    scene.render.resolution_y = R131_HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = False
    scene.render.use_file_extension = True
    scene.render.fps = R131_FPS
    scene.render.fps_base = 1.0
    scene.render.use_motion_blur = False
    scene.frame_start = R131_FRAME_START
    scene.frame_end = R131_FRAME_END
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.18
    scene.view_settings.gamma = 1.0
    scene.eevee.taa_render_samples = 64
    scene.eevee.taa_samples = 64
    scene.eevee.use_taa_reprojection = True
    scene.eevee.volumetric_samples = 32
    return dict(r131_contract()["render"])


def _configure_world() -> None:
    import bpy  # type: ignore[import-not-found]

    world = bpy.data.worlds.get("TP_R131_WORLD") or bpy.data.worlds.new("TP_R131_WORLD")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.004, 0.009, 0.017, 1.0)
        background.inputs["Strength"].default_value = 0.12
    bpy.context.scene.world = world


def _hide_r13_source(root: Any) -> list[str]:
    import bpy  # type: ignore[import-not-found]

    hidden: list[str] = []
    for collection in bpy.context.scene.collection.children:
        if collection == root:
            continue
        if collection.name.startswith("TP_R13_") or collection.name == "TP_R13_LOOKDEV":
            collection.hide_render = True
            collection.hide_viewport = True
            hidden.append(collection.name)
    for obj in bpy.context.scene.collection.objects:
        if not obj.name.startswith("TP_R131_"):
            obj.hide_render = True
            obj.hide_viewport = True
    return sorted(hidden)


def build_r131_refinement_scene(output_path: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    contract = r131_contract()
    validate_r131_contract(contract)
    scene = bpy.context.scene
    if scene.get("trackprompt_r13_revision") != "andromeda-r13-lookdev-lock":
        raise ValueError("R13.1 requires the verified R13 look-development scene as source.")
    source_file = Path(bpy.data.filepath).resolve(strict=True)
    source_hash = _sha256_file(source_file)
    _remove_existing_r131()
    root = _collection(R131_ROOT_COLLECTION, scene.collection)
    hero_collection = _collection("TP_R131_HERO", root)
    architecture_collection = _collection("TP_R131_ARCHITECTURE", root)
    gate_collection = _collection("TP_R131_GATE", root)
    controls_collection = _collection("TP_R131_CONTROLS", root)
    lights_collection = _collection("TP_R131_LIGHTS", root)
    materials = _materials()
    hero = _build_hero(hero_collection, materials)
    architecture = _build_architecture(architecture_collection, materials)
    gate = _build_gate(gate_collection, materials)
    camera_data = bpy.data.cameras.new(R131_CAMERA_NAME)
    camera = bpy.data.objects.new(R131_CAMERA_NAME, camera_data)
    controls_collection.objects.link(camera)
    _mark(camera, "camera", "authored-lag-camera")
    camera.data.lens = 40.0
    camera.data.sensor_width = 36.0
    camera.data.dof.use_dof = False
    target = _empty("TP_R131_CAMERA_TARGET", controls_collection, (0.0, 2.5, 3.5))
    constraint = camera.constraints.new("TRACK_TO")
    constraint.name = "TP_R131_AUTHORED_TRACK"
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"
    scene.camera = camera
    _light(
        "TP_R131_LIGHT_KEY",
        lights_collection,
        (-5.5, 4.0, 9.5),
        1450.0,
        (0.28, 0.58, 0.78),
        5.5,
        (0.0, -1.0, 3.5),
    )
    _light(
        "TP_R131_LIGHT_RIM",
        lights_collection,
        (5.2, -2.5, 7.5),
        1650.0,
        (0.08, 0.78, 0.72),
        4.5,
        (0.0, -3.5, 3.7),
    )
    _light(
        "TP_R131_LIGHT_AMBER",
        lights_collection,
        (-3.8, 1.2, 5.8),
        850.0,
        (1.0, 0.22, 0.035),
        3.0,
        (0.0, -1.0, 3.4),
    )
    _light(
        "TP_R131_LIGHT_DESTINATION",
        lights_collection,
        (0.0, -9.0, 4.2),
        620.0,
        (0.04, 0.26, 0.65),
        3.8,
        (0.0, -4.0, 3.7),
    )
    _animate(hero, bpy.data.objects["TP_R131_HERO_APERTURE"], camera, target, gate)
    render_settings = _configure_render()
    _configure_world()
    hidden = _hide_r13_source(root)
    scene["trackprompt_r131_revision"] = R131_REVISION_ID
    scene["trackprompt_r131_contract"] = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    scene["trackprompt_r131_contract_sha256"] = _canonical_sha256(contract)
    scene["trackprompt_r131_selection"] = json.dumps(R131_SELECTION, sort_keys=True, separators=(",", ":"))
    scene["trackprompt_r131_source_file_sha256"] = source_hash
    scene["trackprompt_r131_source_revision"] = "andromeda-r13-lookdev-lock"
    scene["trackprompt_r131_raw_audio_macro_motion"] = False
    scene["trackprompt_r131_artist_approved"] = False
    scene["trackprompt_r131_human_artist_approval"] = "pending"
    scene["trackprompt_r131_calibration_readiness"] = "blocked"
    scene["trackprompt_r131_production_authorization"] = False
    scene["trackprompt_r131_foreground_object"] = architecture["foreground"].name
    scene["trackprompt_r131_destination_object"] = gate["beacon"].name
    scene.frame_set(R131_FRAME_START)
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    validation = validate_r131_scene()
    return {
        "ok": validation["ok"],
        "revisionId": R131_REVISION_ID,
        "outputFile": str(output),
        "sourceFileSha256": source_hash,
        "contractSha256": scene["trackprompt_r131_contract_sha256"],
        "selection": dict(R131_SELECTION),
        "renderSettings": render_settings,
        "hiddenSourceCollections": hidden,
        "validation": validation,
    }


def _major_audio_driver_findings(owners: Sequence[Any]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for owner in owners:
        animation = getattr(owner, "animation_data", None)
        for curve in list(getattr(animation, "drivers", [])) if animation is not None else []:
            driver = getattr(curve, "driver", None)
            for variable in list(getattr(driver, "variables", [])):
                for target in list(getattr(variable, "targets", [])):
                    if getattr(getattr(target, "id", None), "name", "") == "TP_AUDIO_BUS":
                        findings.append(
                            {
                                "owner": owner.name,
                                "dataPath": curve.data_path,
                                "arrayIndex": curve.array_index,
                            }
                        )
    return findings


def validate_r131_scene() -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    findings: list[str] = []
    raw_contract = scene.get("trackprompt_r131_contract")
    if not isinstance(raw_contract, str):
        return {"ok": False, "findings": ["missing-r13.1-contract"]}
    try:
        validate_r131_contract(json.loads(raw_contract))
    except (ValueError, json.JSONDecodeError) as exc:
        findings.append(str(exc))
    objects = [obj for obj in bpy.data.objects if obj.name.startswith("TP_R131_")]
    hero = [obj for obj in objects if obj.get("trackprompt_r131_semantic") == "subject"]
    architecture = [obj for obj in objects if obj.get("trackprompt_r131_semantic") == "architecture"]
    gate = [obj for obj in objects if obj.get("trackprompt_r131_semantic") == "gate"]
    destination = [obj for obj in objects if obj.get("trackprompt_r131_semantic") == "destination"]
    components = [obj.get("trackprompt_r131_component") for obj in objects]
    if components.count("single-restrained-armor-band") != 1:
        findings.append("protagonist-must-have-exactly-one-major-armor-band")
    if components.count("single-translucent-atmosphere") != 1:
        findings.append("protagonist-transparent-atmosphere-layer-count-drifted")
    for required in (
        "unmistakable-integrated-front-aperture",
        "restrained-rear-energy-wake",
        "functional-energy-conduit",
        "functional-energy-routing-crystal",
        "dark-thick-outer-monolith",
        "moving-lock-ring",
        "localized-inner-membrane",
        "moving-seal-lock",
    ):
        if required not in components:
            findings.append(f"missing-{required}")
    if len(destination) < 4:
        findings.append("destination-depth-is-not-constructed")
    if len(hero) < 12 or len(architecture) < 20 or len(gate) < 14:
        findings.append("r13.1-construction-count-is-incomplete")
    camera = bpy.data.objects.get(R131_CAMERA_NAME)
    hero_root = bpy.data.objects.get(R131_HERO_NAME)
    controller = bpy.data.objects.get(R131_GATE_CONTROLLER_NAME)
    if camera is None or hero_root is None or controller is None:
        findings.append("r13.1-motion-controls-are-missing")
    else:
        if _major_audio_driver_findings([camera, camera.data, hero_root, controller]):
            findings.append("raw-audio-controls-major-travel")
    if (
        scene.render.resolution_x != R131_WIDTH
        or scene.render.resolution_y != R131_HEIGHT
        or scene.frame_start != R131_FRAME_START
        or scene.frame_end != R131_FRAME_END
    ):
        findings.append("r13.1-render-range-or-resolution-drifted")
    if scene.get("trackprompt_r131_artist_approved") is not False:
        findings.append("r13.1-was-automatically-artist-approved")
    if scene.get("trackprompt_r131_calibration_readiness") != "blocked":
        findings.append("r13.1-calibration-was-unblocked")
    return {
        "ok": not findings,
        "revisionId": R131_REVISION_ID,
        "objectCount": len(objects),
        "heroObjectCount": len(hero),
        "architectureObjectCount": len(architecture),
        "gateObjectCount": len(gate),
        "destinationObjectCount": len(destination),
        "majorArmorBandCount": components.count("single-restrained-armor-band"),
        "transparentAtmosphereLayerCount": components.count("single-translucent-atmosphere"),
        "localizedMembraneCount": components.count("localized-inner-membrane"),
        "frameRange": [scene.frame_start, scene.frame_end],
        "renderResolution": [scene.render.resolution_x, scene.render.resolution_y],
        "selection": json.loads(scene["trackprompt_r131_selection"]),
        "findings": findings,
    }


def _file_reference(root: Path, path: Path) -> dict[str, object]:
    return {
        "file": path.relative_to(root).as_posix(),
        "sha256": _sha256_file(path),
        "sizeBytes": path.stat().st_size,
    }


def _downscale(source: Path, destination: Path) -> None:
    import bpy  # type: ignore[import-not-found]

    image = bpy.data.images.load(str(source), check_existing=False)
    try:
        image.scale(R131_PHONE_WIDTH, R131_PHONE_HEIGHT)
        image.filepath_raw = str(destination)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def _render_frame(path: Path, frame: int) -> None:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    scene.frame_set(frame)
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Blender did not render R13.1 frame {frame}.")


def _encode_preview(ffmpeg: Path, frames: Path, output: Path) -> None:
    temporary = output.with_suffix(".partial.mp4")
    command = [
        str(ffmpeg),
        "-y",
        "-framerate",
        str(R131_FPS),
        "-start_number",
        str(R131_FRAME_START),
        "-i",
        str(frames / "frame_%06d.png"),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-frames:v",
        str(R131_FRAME_END - R131_FRAME_START + 1),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=900,
    )
    if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
        raise RuntimeError("FFmpeg did not encode the bounded R13.1 preview.")
    os.replace(temporary, output)


def render_r131_refinement_proof(output_directory: str, ffmpeg_path: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    validation = validate_r131_scene()
    if not validation["ok"]:
        raise ValueError("R13.1 scene validation failed before rendering.")
    root = Path(output_directory).expanduser().resolve()
    frames = root / "frames"
    stills = root / "stills"
    quality = root / "quality-comparison"
    frames.mkdir(parents=True, exist_ok=True)
    stills.mkdir(parents=True, exist_ok=True)
    quality.mkdir(parents=True, exist_ok=True)
    ffmpeg = Path(ffmpeg_path).expanduser().resolve(strict=True)
    original_samples = int(scene.eevee.taa_render_samples)
    scene.eevee.taa_render_samples = 8
    before = quality / "before-taa8-frame-000064.png"
    _render_frame(before, 64)
    scene.eevee.taa_render_samples = 64
    try:
        for frame in range(R131_FRAME_START, R131_FRAME_END + 1):
            _render_frame(frames / f"frame_{frame:06d}.png", frame)
    finally:
        scene.eevee.taa_render_samples = original_samples
    after = quality / "after-taa64-frame-000064.png"
    shutil.copy2(frames / "frame_000064.png", after)
    records: list[dict[str, object]] = []
    for state in R131_REVIEW_STATES:
        identifier = str(state["id"])
        frame = int(state["frame"])
        state_root = stills / identifier
        state_root.mkdir(parents=True, exist_ok=True)
        native = state_root / "native-1080x1920.png"
        phone = state_root / "phone-180x320.png"
        shutil.copy2(frames / f"frame_{frame:06d}.png", native)
        _downscale(native, phone)
        records.append(
            {
                "id": identifier,
                "frame": frame,
                "criteria": list(state["criteria"]),
                "native": _file_reference(root, native),
                "phone": _file_reference(root, phone),
            }
        )
    before_phone = quality / "before-taa8-phone-180x320.png"
    after_phone = quality / "after-taa64-phone-180x320.png"
    _downscale(before, before_phone)
    _downscale(after, after_phone)
    preview = root / "r13.1-motion-preview.mp4"
    _encode_preview(ffmpeg, frames, preview)
    scene_path = Path(bpy.data.filepath).resolve(strict=True)
    manifest = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r13.1-render-manifest",
        "revisionId": R131_REVISION_ID,
        "contractSha256": scene["trackprompt_r131_contract_sha256"],
        "selection": dict(R131_SELECTION),
        "scene": _file_reference(root.parent, scene_path),
        "frameRange": {
            "start": R131_FRAME_START,
            "end": R131_FRAME_END,
            "count": R131_FRAME_END - R131_FRAME_START + 1,
            "fps": R131_FPS,
            "durationSeconds": 4.0,
        },
        "renderQuality": dict(r131_contract()["render"]),
        "qualityComparison": {
            "before": _file_reference(root, before),
            "after": _file_reference(root, after),
            "beforePhone": _file_reference(root, before_phone),
            "afterPhone": _file_reference(root, after_phone),
        },
        "reviewStates": records,
        "motionPreview": _file_reference(root, preview),
        "events": dict(R131_EVENT_FRAMES),
        "rawAudioMacroMotion": False,
        "humanArtistApproval": "pending",
        "artistApproved": False,
        "calibrationReadiness": "blocked",
        "productionAuthorization": False,
        "generatedMediaCommitted": False,
    }
    manifest_path = root / "r13.1-render-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    scene.frame_set(R131_FRAME_START)
    return {
        "ok": True,
        "revisionId": R131_REVISION_ID,
        "outputDirectory": str(root),
        "preview": str(preview),
        "manifest": str(manifest_path),
        "frameCount": R131_FRAME_END - R131_FRAME_START + 1,
        "durationSeconds": 4.0,
        "reviewStateCount": len(records),
        "selection": dict(R131_SELECTION),
        "artistApproved": False,
    }


def build_r131_motion_report() -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]
    from bpy_extras.object_utils import world_to_camera_view  # type: ignore[import-not-found]

    from .render_reports import _raw_audio_driver_findings, motion_metrics

    scene = bpy.context.scene
    validation = validate_r131_scene()
    if not validation["ok"]:
        raise ValueError("R13.1 scene validation failed before motion capture.")
    camera = bpy.data.objects[R131_CAMERA_NAME]
    hero = bpy.data.objects[R131_HERO_NAME]
    controller = bpy.data.objects[R131_GATE_CONTROLLER_NAME]
    foreground = bpy.data.objects[str(scene["trackprompt_r131_foreground_object"])]
    destination = bpy.data.objects[str(scene["trackprompt_r131_destination_object"])]
    original_frame = scene.frame_current
    samples: list[dict[str, Any]] = []
    previous_quaternion = None
    try:
        for frame in range(R131_FRAME_START, R131_FRAME_END + 1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            camera_matrix = camera.matrix_world.copy()
            quaternion = camera_matrix.to_quaternion()
            angular_delta = (
                0.0
                if previous_quaternion is None
                else previous_quaternion.rotation_difference(quaternion).angle
            )
            previous_quaternion = quaternion.copy()
            hero_location = hero.matrix_world.translation.copy()
            camera_location = camera_matrix.translation.copy()
            foreground_ndc = world_to_camera_view(scene, camera, foreground.matrix_world.translation)
            destination_ndc = world_to_camera_view(scene, camera, destination.matrix_world.translation)
            samples.append(
                {
                    "frame": frame,
                    "cameraName": camera.name,
                    "cameraLocation": tuple(float(value) for value in camera_location),
                    "cameraAngularDelta": float(angular_delta),
                    "protagonistLocation": tuple(float(value) for value in hero_location),
                    "lensMm": float(camera.data.lens),
                    "cameraLagDistance": float((camera_location - hero_location).length),
                    "foregroundNdc": [
                        float(foreground_ndc.x),
                        float(foreground_ndc.y),
                        float(foreground_ndc.z),
                    ],
                    "destinationNdc": [
                        float(destination_ndc.x),
                        float(destination_ndc.y),
                        float(destination_ndc.z),
                    ],
                    "deformationAmount": float(hero["trackprompt_r131_deformation_amount"]),
                    "gateAlignmentProgress": float(
                        controller["trackprompt_r131_gate_alignment_progress"]
                    ),
                    "membraneCompression": float(
                        controller["trackprompt_r131_membrane_compression"]
                    ),
                    "sealProgress": float(controller["trackprompt_r131_seal_progress"]),
                }
            )
    finally:
        scene.frame_set(original_frame)
    metrics = motion_metrics(
        samples,
        fps=float(R131_FPS),
        camera_jump_threshold=24.0,
        protagonist_jump_threshold=24.0,
        acceleration_threshold=320.0,
        camera_angular_velocity_threshold=2.0,
        lens_jump_threshold=1.0,
    )
    lag_values = [float(item["cameraLagDistance"]) for item in samples]
    def bounded_screen_velocities(key: str) -> list[float]:
        velocities: list[float] = []
        for left, right in zip(samples, samples[1:], strict=False):
            left_ndc = left[key]
            right_ndc = right[key]
            visible = (
                float(left_ndc[2]) > 0.1
                and float(right_ndc[2]) > 0.1
                and all(-1.5 <= float(value) <= 2.5 for value in (*left_ndc[:2], *right_ndc[:2]))
            )
            if visible:
                velocities.append(
                    math.dist(left_ndc[:2], right_ndc[:2]) * R131_FPS
                )
        if not velocities:
            raise ValueError(f"No bounded visible {key} parallax samples were captured.")
        return velocities

    foreground_velocities = bounded_screen_velocities("foregroundNdc")
    destination_velocities = bounded_screen_velocities("destinationNdc")
    foreground_sorted = sorted(foreground_velocities)
    destination_sorted = sorted(destination_velocities)
    foreground_p95 = foreground_sorted[min(len(foreground_sorted) - 1, int(len(foreground_sorted) * 0.95))]
    destination_p95 = destination_sorted[min(len(destination_sorted) - 1, int(len(destination_sorted) * 0.95))]
    deformation_values = [float(item["deformationAmount"]) for item in samples]
    gate_alignment = [float(item["gateAlignmentProgress"]) for item in samples]
    membrane = [float(item["membraneCompression"]) for item in samples]
    seal = [float(item["sealProgress"]) for item in samples]
    raw_audio_macro, bounded_micro = _raw_audio_driver_findings(
        [camera, camera.data, hero, controller]
    )
    undeclared_cuts = list(metrics["cameraChanges"])
    technical_findings: list[dict[str, object]] = [
        *metrics["oneFrameJumps"]["camera"],
        *metrics["oneFrameJumps"]["protagonist"],
        *metrics["accelerationDiscontinuities"],
        *metrics["angularVelocityOutliers"],
        *metrics["lensJumps"],
        *raw_audio_macro,
        *(
            {"frame": frame, "code": "undeclared-camera-cut"}
            for frame in undeclared_cuts
        ),
    ]
    if max(deformation_values) < 0.95 or deformation_values[-1] > 0.05:
        technical_findings.append({"code": "compression-recovery-range-is-incomplete"})
    if max(gate_alignment) < 0.95 or max(membrane) < 0.95 or seal[-1] < 0.4:
        technical_findings.append({"code": "gate-mechanism-progress-is-incomplete"})
    if foreground_p95 <= destination_p95 * 1.1:
        technical_findings.append({"code": "foreground-parallax-is-not-separated"})
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-r13.1-motion-diagnostics",
        "revisionId": R131_REVISION_ID,
        "frameStart": R131_FRAME_START,
        "frameEnd": R131_FRAME_END,
        "frameCount": len(samples),
        "fps": R131_FPS,
        "durationSeconds": 4.0,
        "metrics": metrics,
        "cameraToProtagonistLag": {
            "minimumDistance": min(lag_values),
            "maximumDistance": max(lag_values),
            "meanDistance": sum(lag_values) / len(lag_values),
            "range": max(lag_values) - min(lag_values),
        },
        "foregroundParallax": {
            "boundedForegroundSampleCount": len(foreground_velocities),
            "boundedDestinationSampleCount": len(destination_velocities),
            "foregroundP95NdcVelocity": foreground_p95,
            "destinationP95NdcVelocity": destination_p95,
            "p95VelocityRatio": foreground_p95 / max(destination_p95, 1e-9),
        },
        "deformationRange": {
            "minimum": min(deformation_values),
            "maximum": max(deformation_values),
            "final": deformation_values[-1],
        },
        "gateMechanismProgress": {
            "alignmentMaximum": max(gate_alignment),
            "membraneCompressionMaximum": max(membrane),
            "sealFinal": seal[-1],
        },
        "declaredCuts": [],
        "undeclaredCuts": undeclared_cuts,
        "rawAudioMajorMotionLinks": raw_audio_macro,
        "boundedMicroAudioLinks": bounded_micro,
        "technicalFindings": technical_findings,
        "technicalPass": not technical_findings,
        "samples": samples,
        "artistApproved": False,
    }

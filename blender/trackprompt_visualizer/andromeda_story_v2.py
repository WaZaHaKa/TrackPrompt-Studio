from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

if __package__:
    from .composition_profiles import (
        HORIZONTAL_VARIANT_ID,
        OUTPUT_VARIANT_IDS,
        VERTICAL_VARIANT_ID,
        all_authored_composition_profiles,
        authored_composition_profile,
        resolve_shot_compositions,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from trackprompt_visualizer.composition_profiles import (  # type: ignore[no-redef]
        HORIZONTAL_VARIANT_ID,
        OUTPUT_VARIANT_IDS,
        VERTICAL_VARIANT_ID,
        all_authored_composition_profiles,
        authored_composition_profile,
        resolve_shot_compositions,
    )

ANDROMEDA_V2_PROJECT_ID = "trip-to-andromeda-v2"
ANDROMEDA_V2_FRAME_START = 1
ANDROMEDA_V2_FRAME_END = 13029
ANDROMEDA_V2_FPS = 30
ANDROMEDA_V2_SHOT_COUNT = 35
ANDROMEDA_V2_LOOK_PROFILE_ID = "andromeda-r13.1-final-look-v1"
ANDROMEDA_V2_SOURCE_AUDIO_SHA256 = (
    "6adf4f3e75f1f775226571ace56883b6e72ad11775bde6c94adc1b95112e5cd5"
)
ANIMATIC_FAST_MODE = "animatic-fast"
MASTER_MODE = "master"
RENDER_MODES = (MASTER_MODE, ANIMATIC_FAST_MODE)

_AUDIO_FEATURE_TO_CUE_CURVE = {
    "smoothed-spectral-centroid": "brightness",
    "smoothed-rms-energy": "masterEnergy",
    "smoothed-onset-density": "transientActivity",
    "smoothed-bass-energy": "bassEnergy",
}
_MAXIMUM_LIGHTING_INFLUENCE_FRACTION = 0.12
_ACT_ORDER = (
    "signal",
    "awakening",
    "departure",
    "gates",
    "rupture",
    "transformation",
    "arrival",
)
_ACT_ANCHORS: dict[str, tuple[float, float, float]] = {
    "signal": (0.0, 0.0, 0.0),
    "awakening": (0.0, 42.0, 1.0),
    "departure": (0.0, 86.0, 1.0),
    "gates": (0.0, 132.0, 1.5),
    "rupture": (0.0, 178.0, -5.0),
    "transformation": (0.0, 224.0, 2.0),
    "arrival": (0.0, 274.0, 7.0),
}
_ACT_PALETTES: dict[str, tuple[float, float, float, float]] = {
    "signal": (0.06, 0.13, 0.15, 1.0),
    "awakening": (0.08, 0.22, 0.22, 1.0),
    "departure": (0.04, 0.11, 0.22, 1.0),
    "gates": (0.04, 0.15, 0.13, 1.0),
    "rupture": (0.17, 0.055, 0.045, 1.0),
    "transformation": (0.18, 0.08, 0.22, 1.0),
    "arrival": (0.12, 0.18, 0.30, 1.0),
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validated_curve_points(
    curve_name: str,
    curve: object,
) -> tuple[tuple[int, float], ...]:
    if not isinstance(curve, dict):
        raise ValueError(f"visual cue curve {curve_name} must be an object")
    if curve.get("pointFormat") != ["frame", "value"]:
        raise ValueError(
            f"visual cue curve {curve_name} must use [frame, value] points"
        )
    if curve.get("interpolation") != "linear":
        raise ValueError(f"visual cue curve {curve_name} must use linear interpolation")
    if not isinstance(curve.get("smoothing"), dict):
        raise ValueError(f"visual cue curve {curve_name} must record smoothing metadata")
    raw_points = curve.get("points")
    if not isinstance(raw_points, list) or not 2 <= len(raw_points) <= 5_000:
        raise ValueError(
            f"visual cue curve {curve_name} requires 2 to 5,000 points"
        )
    points: list[tuple[int, float]] = []
    for raw_point in raw_points:
        if (
            not isinstance(raw_point, list)
            or len(raw_point) != 2
            or not _finite_number(raw_point[0])
            or not _finite_number(raw_point[1])
            or float(raw_point[0]) != int(raw_point[0])
        ):
            raise ValueError(f"visual cue curve {curve_name} contains an invalid point")
        frame = int(raw_point[0])
        value = float(raw_point[1])
        if (
            not ANDROMEDA_V2_FRAME_START <= frame <= ANDROMEDA_V2_FRAME_END
            or not 0.0 <= value <= 1.0
        ):
            raise ValueError(
                f"visual cue curve {curve_name} contains an out-of-range point"
            )
        points.append((frame, value))
    frames = [point[0] for point in points]
    if frames != sorted(set(frames)):
        raise ValueError(f"visual cue curve {curve_name} frames must be strictly ordered")
    return tuple(points)


def load_and_validate_visual_cues(
    visual_cues_path: str | Path,
    expected_sha256: str,
) -> dict[str, Any]:
    """Load the private cue sheet only when its exact StoryPlan binding matches."""

    path = Path(visual_cues_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("visual cues must be a readable JSON file")
    if not 0 < path.stat().st_size <= 25_000_000:
        raise ValueError("visual cues exceed the 25 MB safety limit")
    if (
        len(expected_sha256) != 64
        or expected_sha256 != expected_sha256.lower()
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("StoryPlan source-cue SHA-256 binding is invalid")
    actual_sha256 = _file_sha256(path)
    if actual_sha256 != expected_sha256:
        raise ValueError("visual cues do not match the StoryPlan source-cue SHA-256")
    payload = _read_json(path)
    if payload.get("schemaVersion") != "1.1.0":
        raise ValueError("visual cues must use schema version 1.1.0")
    timeline = payload.get("timeline")
    if (
        not isinstance(timeline, dict)
        or timeline.get("fps") != ANDROMEDA_V2_FPS
        or timeline.get("frameStart") != ANDROMEDA_V2_FRAME_START
        or timeline.get("frameEnd") != ANDROMEDA_V2_FRAME_END
    ):
        raise ValueError("visual cues must match the Andromeda master timeline")
    curves = payload.get("curves")
    if not isinstance(curves, dict):
        raise ValueError("visual cues must contain curves")
    for curve_name in _AUDIO_FEATURE_TO_CUE_CURVE.values():
        _validated_curve_points(curve_name, curves.get(curve_name))
    return payload


def _curve_value_at(
    points: tuple[tuple[int, float], ...],
    frame: int,
) -> float:
    if frame <= points[0][0]:
        return points[0][1]
    if frame >= points[-1][0]:
        return points[-1][1]
    low = 0
    high = len(points) - 1
    while low + 1 < high:
        midpoint = (low + high) // 2
        if points[midpoint][0] <= frame:
            low = midpoint
        else:
            high = midpoint
    left_frame, left_value = points[low]
    right_frame, right_value = points[high]
    progress = (frame - left_frame) / (right_frame - left_frame)
    return left_value + (right_value - left_value) * progress


def _curve_frames_for_shot(
    points: tuple[tuple[int, float], ...],
    frame_start: int,
    frame_end: int,
) -> set[int]:
    return {
        frame_start,
        frame_end,
        *(
            frame
            for frame, _value in points
            if frame_start < frame < frame_end
        ),
    }


def load_andromeda_v2_source_contracts(repository_root: str | Path) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    story_path = (
        root
        / "backend"
        / "app"
        / "cinematic"
        / "templates"
        / "trip_to_andromeda_story_v2.json"
    )
    shot_path = (
        root
        / "backend"
        / "app"
        / "cinematic"
        / "templates"
        / "trip_to_andromeda_shots_v2.json"
    )
    production_root = root / "production" / "andromeda-v2"
    look_path = production_root / "final-look-profile.json"
    variants_path = production_root / "output-variants.json"
    acceptance_path = production_root / "creative-acceptance.json"
    story = _read_json(story_path)
    shots = _read_json(shot_path)
    look = _read_json(look_path)
    variants = _read_json(variants_path)
    acceptance = _read_json(acceptance_path)

    if (
        story.get("projectId") != ANDROMEDA_V2_PROJECT_ID
        or shots.get("projectId") != ANDROMEDA_V2_PROJECT_ID
        or look.get("profileId") != ANDROMEDA_V2_LOOK_PROFILE_ID
    ):
        raise ValueError("Andromeda V2 source identities do not agree")
    if story.get("lookProfileSha256") != _file_sha256(look_path):
        raise ValueError("StoryPlan look-profile binding is invalid")
    if shots.get("lookProfileSha256") != _file_sha256(look_path):
        raise ValueError("ShotPlan look-profile binding is invalid")
    if shots.get("storyPlanSha256") != _file_sha256(story_path):
        raise ValueError("ShotPlan StoryPlan binding is invalid")
    if story.get("sourceAudioSha256") != ANDROMEDA_V2_SOURCE_AUDIO_SHA256:
        raise ValueError("StoryPlan source-audio binding is invalid")
    source_cue_sha256 = story.get("sourceCueSha256")
    if (
        not isinstance(source_cue_sha256, str)
        or len(source_cue_sha256) != 64
        or source_cue_sha256 != source_cue_sha256.lower()
        or any(
            character not in "0123456789abcdef"
            for character in source_cue_sha256
        )
    ):
        raise ValueError("StoryPlan source-cue binding is invalid")
    if acceptance.get("doesNotAuthorizeProduction") is not True:
        raise ValueError("creative acceptance may not authorize production")
    return {
        "repositoryRoot": root,
        "storyPath": story_path,
        "shotPath": shot_path,
        "lookPath": look_path,
        "variantsPath": variants_path,
        "acceptancePath": acceptance_path,
        "story": story,
        "shots": shots,
        "lookProfile": look,
        "outputVariants": variants,
        "acceptance": acceptance,
    }


def build_andromeda_v2_scene_spec(repository_root: str | Path) -> dict[str, Any]:
    contracts = load_andromeda_v2_source_contracts(repository_root)
    story = contracts["story"]
    shot_plan = contracts["shots"]
    acts = story.get("acts")
    shots = shot_plan.get("shots")
    if not isinstance(acts, list) or [act.get("id") for act in acts] != list(_ACT_ORDER):
        raise ValueError("Andromeda V2 requires its fixed seven-act order")
    if not isinstance(shots, list) or len(shots) != ANDROMEDA_V2_SHOT_COUNT:
        raise ValueError("Andromeda V2 requires exactly 35 shots")
    if (
        shots[0].get("frameStart") != ANDROMEDA_V2_FRAME_START
        or shots[-1].get("frameEnd") != ANDROMEDA_V2_FRAME_END
    ):
        raise ValueError("Andromeda V2 shots do not cover the master timeline")

    complexity_by_shot: dict[str, str] = {}
    resolved_compositions: dict[str, dict[str, dict[str, Any]]] = {}
    previous_end = 0
    shots_by_act = {act_id: 0 for act_id in _ACT_ORDER}
    for expected_sequence, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise ValueError("Andromeda V2 shot entries must be objects")
        start = shot.get("frameStart")
        end = shot.get("frameEnd")
        if (
            shot.get("sequence") != expected_sequence
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start != previous_end + 1
            or end < start
            or shot.get("durationFrames") != end - start + 1
        ):
            raise ValueError("Andromeda V2 shot timeline is not deterministic and contiguous")
        shot_id = shot.get("id")
        act_id = shot.get("actId")
        complexity = shot.get("complexityClass")
        if (
            not isinstance(shot_id, str)
            or act_id not in shots_by_act
            or complexity not in {"light", "standard", "heavy", "extreme"}
        ):
            raise ValueError("Andromeda V2 shot identity, act, or complexity is invalid")
        if shot.get("lookProfileSha256") != shot_plan.get("lookProfileSha256"):
            raise ValueError("every Andromeda V2 shot must bind the locked look profile")
        shots_by_act[str(act_id)] += 1
        complexity_by_shot[shot_id] = str(complexity)
        resolved_compositions[shot_id] = resolve_shot_compositions(shot)
        previous_end = end
    if any(count != 5 for count in shots_by_act.values()):
        raise ValueError("each Andromeda V2 act must contain exactly five shots")

    source_payload = {
        "storyPlanSha256": _file_sha256(contracts["storyPath"]),
        "shotPlanSha256": _file_sha256(contracts["shotPath"]),
        "lookProfileSha256": _file_sha256(contracts["lookPath"]),
        "outputVariantsSha256": _file_sha256(contracts["variantsPath"]),
        "creativeAcceptanceSha256": _file_sha256(contracts["acceptancePath"]),
    }
    spec: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "builderId": "andromeda-v2-master-scene-builder-v1",
        "projectId": ANDROMEDA_V2_PROJECT_ID,
        "frameStart": ANDROMEDA_V2_FRAME_START,
        "frameEnd": ANDROMEDA_V2_FRAME_END,
        "fps": ANDROMEDA_V2_FPS,
        "actOrder": list(_ACT_ORDER),
        "actAnchors": {key: list(value) for key, value in _ACT_ANCHORS.items()},
        "shotCount": len(shots),
        "complexityByShot": complexity_by_shot,
        "outputVariantIds": list(OUTPUT_VARIANT_IDS),
        "verticalEnabledByDefault": False,
        "compositionProfiles": all_authored_composition_profiles(),
        "resolvedShotCompositions": resolved_compositions,
        "sourceBindings": source_payload,
        "productionAuthorized": False,
        "renderStarted": False,
    }
    spec["canonicalSha256"] = _canonical_sha256(spec)
    return spec


def _clear_scene() -> None:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for blocks in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.materials,
    ):
        for block in list(blocks):
            if block.users == 0:
                blocks.remove(block)


def _collection(name: str, parent: Any) -> Any:
    import bpy  # type: ignore[import-not-found]

    result = bpy.data.collections.new(name)
    parent.children.link(result)
    return result


def _move_to_collection(obj: Any, collection: Any) -> Any:
    for existing in list(obj.users_collection):
        existing.objects.unlink(obj)
    collection.objects.link(obj)
    return obj


def _material(
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
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        for socket_name, value in (
            ("Base Color", base_color),
            ("Metallic", metallic),
            ("Roughness", roughness),
        ):
            socket = principled.inputs.get(socket_name)
            if socket is not None:
                socket.default_value = value
        emission = principled.inputs.get("Emission Color") or principled.inputs.get("Emission")
        strength = principled.inputs.get("Emission Strength")
        if emission is not None:
            emission.default_value = emission_color or base_color
        if strength is not None:
            strength.default_value = emission_strength
    material["trackprompt_look_profile"] = ANDROMEDA_V2_LOOK_PROFILE_ID
    return material


def _mark(obj: Any, *, act_id: str, semantic: str) -> Any:
    obj["trackprompt_project_id"] = ANDROMEDA_V2_PROJECT_ID
    obj["trackprompt_act_id"] = act_id
    obj["trackprompt_semantic"] = semantic
    obj["trackprompt_look_profile"] = ANDROMEDA_V2_LOOK_PROFILE_ID
    return obj


def _cube(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    material: Any,
    act_id: str,
    semantic: str,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _move_to_collection(obj, collection)
    obj.data.materials.append(material)
    return _mark(obj, act_id=act_id, semantic=semantic)


def _sphere(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    radius: float,
    material: Any,
    act_id: str,
    semantic: str,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=radius, location=location)
    obj = bpy.context.object
    obj.name = name
    _move_to_collection(obj, collection)
    obj.data.materials.append(material)
    return _mark(obj, act_id=act_id, semantic=semantic)


def _torus(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    major_radius: float,
    minor_radius: float,
    material: Any,
    act_id: str,
    semantic: str,
    rotation: tuple[float, float, float] = (math.pi / 2.0, 0.0, 0.0),
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_torus_add(
        major_radius=major_radius,
        minor_radius=minor_radius,
        major_segments=40,
        minor_segments=8,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    _move_to_collection(obj, collection)
    obj.data.materials.append(material)
    return _mark(obj, act_id=act_id, semantic=semantic)


def _build_environment(
    act_id: str,
    collection: Any,
    materials: Mapping[str, Any],
) -> list[Any]:
    anchor = _ACT_ANCHORS[act_id]
    ax, ay, az = anchor
    stone = materials["stone"]
    crystal = materials["crystal"]
    dark = materials["dark"]
    accent = materials[act_id]
    objects: list[Any] = []

    if act_id == "signal":
        objects.append(
            _sphere(
                "TP_ANDROMEDA_V2_SIGNAL_DEAD_MOON",
                collection,
                location=(ax + 10.0, ay + 9.0, az - 7.0),
                radius=8.0,
                material=dark,
                act_id=act_id,
                semantic="dead-moon-rim",
            )
        )
        for index in range(4):
            objects.append(
                _cube(
                    f"TP_ANDROMEDA_V2_SIGNAL_OBSERVATORY_{index:02d}",
                    collection,
                    location=(ax - 6.0 + index * 4.0, ay, az - 1.5 + index * 0.5),
                    dimensions=(1.2, 9.0, 3.0 + index),
                    material=stone,
                    act_id=act_id,
                    semantic="connected-observatory-support",
                    rotation=(0.0, 0.12 * index, -0.08 * index),
                )
            )
        objects.append(
            _cube(
                "TP_ANDROMEDA_V2_SIGNAL_NEEDLE_BEACON",
                collection,
                location=(ax + 8.0, ay + 4.0, az + 9.0),
                dimensions=(0.18, 0.18, 14.0),
                material=crystal,
                act_id=act_id,
                semantic="needle-beacon",
                rotation=(0.0, 0.28, 0.0),
            )
        )
    elif act_id == "awakening":
        for index in range(5):
            objects.append(
                _torus(
                    f"TP_ANDROMEDA_V2_AWAKENING_IRIS_{index:02d}",
                    collection,
                    location=(ax, ay + index * 0.75, az),
                    major_radius=4.0 + index * 0.7,
                    minor_radius=0.18,
                    material=stone if index % 2 == 0 else accent,
                    act_id=act_id,
                    semantic="reactivation-iris-chamber",
                )
            )
        for side in (-1.0, 1.0):
            objects.append(
                _cube(
                    f"TP_ANDROMEDA_V2_AWAKENING_ROUTE_{'L' if side < 0 else 'R'}",
                    collection,
                    location=(ax + side * 4.5, ay + 4.0, az),
                    dimensions=(0.3, 10.0, 0.3),
                    material=crystal,
                    act_id=act_id,
                    semantic="functional-crystal-routing",
                )
            )
    elif act_id == "departure":
        for index in range(8):
            y = ay - 14.0 + index * 4.0
            for side in (-1.0, 1.0):
                objects.append(
                    _cube(
                        f"TP_ANDROMEDA_V2_DEPARTURE_RIB_{index:02d}_{'L' if side < 0 else 'R'}",
                        collection,
                        location=(ax + side * 14.0, y, az + 2.0),
                        dimensions=(0.25, 0.4, 4.5),
                        material=stone,
                        act_id=act_id,
                        semantic="connected-launch-rib",
                        rotation=(0.0, side * 0.12, 0.0),
                    )
                )
        for side in (-1.0, 1.0):
            objects.append(
                _cube(
                    f"TP_ANDROMEDA_V2_DEPARTURE_RAIL_{'L' if side < 0 else 'R'}",
                    collection,
                    location=(ax + side * 2.8, ay, az - 2.0),
                    dimensions=(0.18, 34.0, 0.18),
                    material=crystal,
                    act_id=act_id,
                    semantic="cobalt-guide-rail",
                )
            )
    elif act_id == "gates":
        for side in (-1.0, 1.0):
            objects.append(
                _cube(
                    f"TP_ANDROMEDA_V2_GATE_MONOLITH_{'L' if side < 0 else 'R'}",
                    collection,
                    location=(ax + side * 9.0, ay + 2.0, az + 2.0),
                    dimensions=(2.5, 4.0, 16.0),
                    material=stone,
                    act_id=act_id,
                    semantic="outer-stone-monolith",
                    rotation=(0.0, side * 0.10, side * 0.06),
                )
            )
        for index in range(4):
            objects.append(
                _torus(
                    f"TP_ANDROMEDA_V2_GATE_LOCK_RING_{index:02d}",
                    collection,
                    location=(ax, ay + index * 0.6, az + 2.0),
                    major_radius=5.0 - index * 0.65,
                    minor_radius=0.24,
                    material=accent if index % 2 == 0 else stone,
                    act_id=act_id,
                    semantic="moving-lock-ring",
                )
            )
        objects.append(
            _sphere(
                "TP_ANDROMEDA_V2_GATE_LOCALIZED_MEMBRANE",
                collection,
                location=(ax, ay + 2.0, az + 2.0),
                radius=2.0,
                material=crystal,
                act_id=act_id,
                semantic="localized-threshold-membrane",
            )
        )
    elif act_id == "rupture":
        for index in range(9):
            side = -1.0 if index % 2 == 0 else 1.0
            objects.append(
                _cube(
                    f"TP_ANDROMEDA_V2_RUPTURE_SLAB_{index:02d}",
                    collection,
                    location=(
                        ax + side * (6.0 + index),
                        ay - 15.0 + index * 4.0,
                        az + index * 0.8,
                    ),
                    dimensions=(1.5, 1.6, 0.35),
                    material=stone if index % 3 else accent,
                    act_id=act_id,
                    semantic="directional-fracture-canyon-slab",
                    rotation=(0.14 * index, side * 0.08 * index, side * 0.12),
                )
            )
        objects.append(
            _cube(
                "TP_ANDROMEDA_V2_RUPTURE_IMPACT_RIB",
                collection,
                location=(ax + 5.0, ay + 4.0, az + 2.0),
                dimensions=(0.6, 8.0, 0.7),
                material=accent,
                act_id=act_id,
                semantic="released-route-impact-rib",
                rotation=(0.2, 0.5, -0.4),
            )
        )
    elif act_id == "transformation":
        for index in range(8):
            angle = math.tau * index / 8.0
            objects.append(
                _cube(
                    f"TP_ANDROMEDA_V2_TRANSFORMATION_ARM_{index:02d}",
                    collection,
                    location=(
                        ax + math.cos(angle) * 6.0,
                        ay,
                        az + math.sin(angle) * 6.0,
                    ),
                    dimensions=(0.75, 10.0, 0.75),
                    material=stone if index % 2 else accent,
                    act_id=act_id,
                    semantic="connected-reconstruction-arm",
                    rotation=(0.0, -angle, angle),
                )
            )
        for radius in (2.2, 3.4, 4.8):
            objects.append(
                _torus(
                    f"TP_ANDROMEDA_V2_TRANSFORMATION_CRADLE_{int(radius * 10):02d}",
                    collection,
                    location=(ax, ay + 2.0, az),
                    major_radius=radius,
                    minor_radius=0.16,
                    material=crystal,
                    act_id=act_id,
                    semantic="reconstruction-cradle",
                    rotation=(math.pi / 2.0, radius * 0.11, 0.0),
                )
            )
    elif act_id == "arrival":
        for index in range(7):
            objects.append(
                _torus(
                    f"TP_ANDROMEDA_V2_ARRIVAL_MARKER_{index:02d}",
                    collection,
                    location=(ax + (index - 3) * 3.2, ay + index * 4.0, az + index * 1.3),
                    major_radius=1.8 + index * 0.35,
                    minor_radius=0.10,
                    material=stone if index % 2 else crystal,
                    act_id=act_id,
                    semantic="destination-scale-marker",
                    rotation=(math.pi / 2.0, 0.1 * index, 0.0),
                )
            )
        objects.append(
            _torus(
                "TP_ANDROMEDA_V2_ARRIVAL_HORIZON",
                collection,
                location=(ax, ay + 35.0, az + 14.0),
                major_radius=24.0,
                minor_radius=1.2,
                material=accent,
                act_id=act_id,
                semantic="andromeda-horizon-arc",
                rotation=(math.pi / 2.0, 0.32, 0.0),
            )
        )
    return objects


def _build_protagonist(collection: Any, materials: Mapping[str, Any]) -> Any:
    import bpy  # type: ignore[import-not-found]

    root = bpy.data.objects.new("TP_ANDROMEDA_V2_PROTAGONIST_ROOT", None)
    collection.objects.link(root)
    _mark(root, act_id="story", semantic="protagonist-b-ancient-engine")
    body = _sphere(
        "TP_ANDROMEDA_V2_PROTAGONIST_BODY",
        collection,
        location=(0.0, 0.0, 0.0),
        radius=1.45,
        material=materials["hero"],
        act_id="story",
        semantic="ancient-engine-body",
    )
    body.scale = (1.0, 1.22, 0.90)
    body.parent = root
    band = _torus(
        "TP_ANDROMEDA_V2_PROTAGONIST_ARMOR_BAND",
        collection,
        location=(0.0, 0.0, 0.0),
        major_radius=1.52,
        minor_radius=0.16,
        material=materials["stone"],
        act_id="story",
        semantic="single-major-armor-band",
        rotation=(0.0, math.pi / 2.0, 0.0),
    )
    band.parent = root
    aperture = _sphere(
        "TP_ANDROMEDA_V2_PROTAGONIST_APERTURE",
        collection,
        location=(-0.58, -1.52, 0.46),
        radius=0.54,
        material=materials["amber"],
        act_id="story",
        semantic="integrated-front-aperture",
    )
    aperture.scale = (1.0, 0.28, 0.72)
    aperture.parent = root
    cue = _cube(
        "TP_ANDROMEDA_V2_PROTAGONIST_ORIENTATION_CUE",
        collection,
        location=(0.75, -0.25, 0.42),
        dimensions=(0.30, 1.20, 0.35),
        material=materials["crystal"],
        act_id="story",
        semantic="asymmetric-orientation-cue",
        rotation=(0.15, 0.35, 0.20),
    )
    cue.parent = root
    wake = _torus(
        "TP_ANDROMEDA_V2_PROTAGONIST_REAR_WAKE",
        collection,
        location=(0.0, 1.8, 0.0),
        major_radius=1.05,
        minor_radius=0.07,
        material=materials["crystal"],
        act_id="story",
        semantic="restrained-rear-wake",
    )
    wake.parent = root
    root["trackprompt_major_armor_bands"] = 1
    root["trackprompt_wire_cage"] = False
    root["trackprompt_transparent_atmosphere_layers"] = 1
    return root


def _new_camera(name: str, collection: Any) -> Any:
    import bpy  # type: ignore[import-not-found]

    data = bpy.data.cameras.new(name)
    camera = bpy.data.objects.new(name, data)
    collection.objects.link(camera)
    camera["trackprompt_project_id"] = ANDROMEDA_V2_PROJECT_ID
    return camera


def _build_lights(collection: Any) -> list[Any]:
    import bpy  # type: ignore[import-not-found]

    lights: list[Any] = []
    for index, act_id in enumerate(_ACT_ORDER):
        anchor = _ACT_ANCHORS[act_id]
        color = _ACT_PALETTES[act_id]
        data = bpy.data.lights.new(f"TP_ANDROMEDA_V2_LIGHT_{act_id.upper()}", type="AREA")
        data.energy = 1_200.0 if act_id not in {"rupture", "arrival"} else 1_500.0
        data.shape = "DISK"
        data.size = 8.0
        data.color = tuple(min(1.0, channel * 3.2 + 0.12) for channel in color[:3])
        light = bpy.data.objects.new(data.name, data)
        collection.objects.link(light)
        light.location = (anchor[0] - 7.0, anchor[1] - 7.0, anchor[2] + 10.0)
        _point_camera(light, anchor)
        _mark(light, act_id=act_id, semantic="motivated-act-key-light")
        lights.append(light)

        fill_data = bpy.data.lights.new(
            f"TP_ANDROMEDA_V2_LIGHT_{act_id.upper()}_FILL",
            type="POINT",
        )
        fill_data.energy = 420.0 if act_id != "rupture" else 650.0
        fill_data.shadow_soft_size = 5.0
        fill_data.color = (0.38, 0.58, 0.62) if index % 2 == 0 else (0.62, 0.38, 0.22)
        fill = bpy.data.objects.new(fill_data.name, fill_data)
        collection.objects.link(fill)
        fill.location = (anchor[0] + 5.0, anchor[1] - 3.0, anchor[2] + 3.0)
        _mark(fill, act_id=act_id, semantic="bounded-act-fill-light")
        lights.append(fill)
    return lights


def _point_camera(camera: Any, target: tuple[float, float, float]) -> None:
    from mathutils import Vector  # type: ignore[import-not-found]

    direction = Vector(target) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _protagonist_transform(
    *,
    frame: int,
    location: tuple[float, float, float],
    sequence: int,
    protagonist_state: str,
    midpoint: bool,
) -> dict[str, Any]:
    rotation = [
        0.08 * math.sin(sequence),
        0.12 * math.cos(sequence * 0.5),
        0.17 * sequence + (0.08 if midpoint else 0.0),
    ]
    if protagonist_state == "damaged":
        rotation[1] += 0.48
        scale = (1.0, 0.88, 1.06)
    elif protagonist_state == "transforming":
        scale = (1.04, 1.08, 1.04)
    else:
        scale = (1.0, 1.0, 1.0)
    return {
        "frame": frame,
        "location": location,
        "rotationEuler": tuple(rotation),
        "scale": scale,
    }


def _camera_transform(
    *,
    frame: int,
    hero_location: tuple[float, float, float],
    anchor: tuple[float, float, float],
    composition: Mapping[str, Any],
    base_composition: Mapping[str, Any],
    lateral: float,
) -> dict[str, Any]:
    camera_offset = composition["cameraOffset"]
    target_offset = composition["targetOffset"]
    occupancy = float(composition["subjectOccupancyFraction"])
    base_occupancy = float(composition["subjectScale"])
    lens_scale = float(composition["lensMm"]) / float(base_composition["lensMm"])
    distance_scale = max(0.72, min(2.20, base_occupancy / occupancy * lens_scale))
    return {
        "frame": frame,
        "location": (
            anchor[0] + float(camera_offset[0]) * distance_scale + lateral,
            hero_location[1] + float(camera_offset[1]) * distance_scale,
            anchor[2] + float(camera_offset[2]) * distance_scale,
        ),
        "target": (
            hero_location[0] + float(target_offset[0]),
            hero_location[1] + float(target_offset[1]),
            hero_location[2] + float(target_offset[2]),
        ),
        "lensMm": float(composition["lensMm"]),
    }


def _story_animation_plan(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve authored motion while making every non-cut boundary continuous."""

    plan: list[dict[str, Any]] = []
    previous_protagonist_end: dict[str, Any] | None = None
    previous_camera_ends: dict[str, dict[str, Any]] = {}
    for shot in shots:
        act_id = str(shot["actId"])
        sequence = int(shot["sequence"])
        index_in_act = (sequence - 1) % 5
        frame_start = int(shot["frameStart"])
        frame_end = int(shot["frameEnd"])
        frame_mid = (frame_start + frame_end) // 2
        intentional_cut = shot.get("intentionalCut")
        if not isinstance(intentional_cut, bool):
            raise ValueError("every shot must declare intentionalCut")
        anchor = _ACT_ANCHORS[act_id]
        start_location = (
            anchor[0] + (index_in_act - 2) * 1.15,
            anchor[1] - 8.0 + index_in_act * 3.8,
            anchor[2] + math.sin(sequence * 0.7) * 1.1,
        )
        if previous_protagonist_end is not None and not intentional_cut:
            start_location = tuple(previous_protagonist_end["location"])
        end_location = (
            (
                anchor[0] + (index_in_act - 2) * 1.15
                + math.sin(sequence * 0.43) * 1.6
            ),
            anchor[1] - 8.0 + index_in_act * 3.8 + 4.8,
            (
                anchor[2]
                + math.sin(sequence * 0.7) * 1.1
                + math.cos(sequence * 0.37) * 0.9
            ),
        )
        mid_location = (
            (start_location[0] + end_location[0]) / 2.0
            + math.sin(sequence * 0.61) * 0.65,
            (start_location[1] + end_location[1]) / 2.0,
            (start_location[2] + end_location[2]) / 2.0
            + math.cos(sequence * 0.47) * 0.48,
        )
        protagonist_states = [
            _protagonist_transform(
                frame=frame_start,
                location=start_location,
                sequence=sequence,
                protagonist_state=str(shot["protagonistState"]),
                midpoint=False,
            ),
            _protagonist_transform(
                frame=frame_mid,
                location=mid_location,
                sequence=sequence,
                protagonist_state=str(shot["protagonistState"]),
                midpoint=True,
            ),
            _protagonist_transform(
                frame=frame_end,
                location=end_location,
                sequence=sequence,
                protagonist_state=str(shot["protagonistState"]),
                midpoint=False,
            ),
        ]
        if previous_protagonist_end is not None and not intentional_cut:
            protagonist_states[0] = {
                **previous_protagonist_end,
                "frame": frame_start,
            }

        shot_compositions = resolve_shot_compositions(shot)
        camera_states: dict[str, list[dict[str, Any]]] = {}
        for variant_id in OUTPUT_VARIANT_IDS:
            composition = shot_compositions[variant_id]
            base_composition = authored_composition_profile(variant_id, act_id)
            states = [
                _camera_transform(
                    frame=frame,
                    hero_location=hero_state["location"],
                    anchor=anchor,
                    composition=composition,
                    base_composition=base_composition,
                    lateral=lateral,
                )
                for frame, hero_state, lateral in (
                    (frame_start, protagonist_states[0], -0.35),
                    (
                        frame_mid,
                        protagonist_states[1],
                        0.12 * math.sin(sequence),
                    ),
                    (frame_end, protagonist_states[2], 0.35),
                )
            ]
            previous_end = previous_camera_ends.get(variant_id)
            if previous_end is not None and not intentional_cut:
                states[0] = {**previous_end, "frame": frame_start}
            camera_states[variant_id] = states
            previous_camera_ends[variant_id] = states[-1]

        shot_plan = {
            "shotId": str(shot["id"]),
            "intentionalCut": intentional_cut,
            "protagonist": protagonist_states,
            "cameras": camera_states,
        }
        plan.append(shot_plan)
        previous_protagonist_end = protagonist_states[-1]
    return plan


def _animate_story(
    protagonist: Any,
    cameras: Mapping[str, Any],
    shots: list[dict[str, Any]],
) -> None:
    for shot_plan in _story_animation_plan(shots):
        for state in shot_plan["protagonist"]:
            frame = int(state["frame"])
            protagonist.location = state["location"]
            protagonist.rotation_euler = state["rotationEuler"]
            protagonist.scale = state["scale"]
            protagonist.keyframe_insert("location", frame=frame)
            protagonist.keyframe_insert("rotation_euler", frame=frame)
            protagonist.keyframe_insert("scale", frame=frame)

        for variant_id, camera in cameras.items():
            for state in shot_plan["cameras"][variant_id]:
                frame = int(state["frame"])
                camera.location = state["location"]
                camera.data.lens = float(state["lensMm"])
                _point_camera(camera, state["target"])
                camera.keyframe_insert("location", frame=frame)
                camera.keyframe_insert("rotation_euler", frame=frame)
                camera.data.keyframe_insert("lens", frame=frame)


def _act_range(shots: list[dict[str, Any]], act_id: str) -> tuple[int, int]:
    matching = [shot for shot in shots if shot["actId"] == act_id]
    if not matching:
        raise ValueError(f"missing shots for act {act_id}")
    return int(matching[0]["frameStart"]), int(matching[-1]["frameEnd"])


def _animate_environment_actions(
    environment_objects: Mapping[str, list[Any]],
    shots: list[dict[str, Any]],
) -> None:
    for act_id, objects in environment_objects.items():
        frame_start, frame_end = _act_range(shots, act_id)
        frame_mid = (frame_start + frame_end) // 2
        for index, obj in enumerate(objects):
            base_location = tuple(obj.location)
            base_rotation = tuple(obj.rotation_euler)
            base_scale = tuple(obj.scale)
            semantic = str(obj.get("trackprompt_semantic", ""))

            if semantic == "needle-beacon":
                for frame, scale_z in (
                    (frame_start, 0.35),
                    (frame_mid, 1.15),
                    (frame_end, 0.75),
                ):
                    obj.scale = (base_scale[0], base_scale[1], scale_z)
                    obj.keyframe_insert("scale", frame=frame)
            elif semantic == "reactivation-iris-chamber":
                for frame, turn in (
                    (frame_start, -0.28),
                    (frame_mid, 0.22),
                    (frame_end, 0.58),
                ):
                    obj.rotation_euler = (
                        base_rotation[0],
                        base_rotation[1],
                        base_rotation[2] + turn * (index + 1),
                    )
                    obj.keyframe_insert("rotation_euler", frame=frame)
            elif semantic == "connected-launch-rib":
                for frame, lean in (
                    (frame_start, 0.0),
                    (frame_mid, 0.08 if index % 2 else -0.08),
                    (frame_end, 0.0),
                ):
                    obj.rotation_euler = (
                        base_rotation[0],
                        base_rotation[1] + lean,
                        base_rotation[2],
                    )
                    obj.keyframe_insert("rotation_euler", frame=frame)
            elif semantic == "moving-lock-ring":
                for frame, compression, turn in (
                    (frame_start, 1.12, -0.32),
                    (frame_mid, 0.84, 0.18),
                    (frame_end, 0.58, 0.72),
                ):
                    obj.scale = (compression, compression, compression)
                    obj.rotation_euler = (
                        base_rotation[0],
                        base_rotation[1] + turn * (index + 1),
                        base_rotation[2],
                    )
                    obj.keyframe_insert("scale", frame=frame)
                    obj.keyframe_insert("rotation_euler", frame=frame)
            elif semantic == "localized-threshold-membrane":
                for frame, scale in (
                    (frame_start, 0.28),
                    (frame_mid, 1.18),
                    (frame_end, 0.36),
                ):
                    obj.scale = (scale, 0.18, scale)
                    obj.keyframe_insert("scale", frame=frame)
            elif semantic in {
                "directional-fracture-canyon-slab",
                "released-route-impact-rib",
            }:
                direction = -1.0 if index % 2 == 0 else 1.0
                for frame, offset, turn in (
                    (frame_start, 0.0, 0.0),
                    (frame_mid, 1.2, 0.18),
                    (frame_end, 3.0, 0.48),
                ):
                    obj.location = (
                        base_location[0] + direction * offset,
                        base_location[1] + offset * 0.45,
                        base_location[2] + offset * 0.32,
                    )
                    obj.rotation_euler = (
                        base_rotation[0] + turn,
                        base_rotation[1] + direction * turn,
                        base_rotation[2],
                    )
                    obj.keyframe_insert("location", frame=frame)
                    obj.keyframe_insert("rotation_euler", frame=frame)
            elif semantic in {
                "connected-reconstruction-arm",
                "reconstruction-cradle",
            }:
                delay = min(120, index * 12)
                for frame, scale, turn in (
                    (frame_start + delay, 0.18, -0.25),
                    (frame_mid + delay // 2, 0.82, 0.12),
                    (frame_end, 1.0, 0.38),
                ):
                    obj.scale = (scale, scale, scale)
                    obj.rotation_euler = (
                        base_rotation[0],
                        base_rotation[1] + turn,
                        base_rotation[2] - turn * 0.5,
                    )
                    obj.keyframe_insert("scale", frame=frame)
                    obj.keyframe_insert("rotation_euler", frame=frame)
            elif semantic in {
                "destination-scale-marker",
                "andromeda-horizon-arc",
            }:
                for frame, scale, turn in (
                    (frame_start, 0.72, -0.08),
                    (frame_mid, 1.04, 0.04),
                    (frame_end, 1.0, 0.18),
                ):
                    obj.scale = (scale, scale, scale)
                    obj.rotation_euler = (
                        base_rotation[0],
                        base_rotation[1] + turn,
                        base_rotation[2],
                    )
                    obj.keyframe_insert("scale", frame=frame)
                    obj.keyframe_insert("rotation_euler", frame=frame)

def _bounded_lighting_plan(
    shots: list[dict[str, Any]],
    visual_cues: Mapping[str, Any],
) -> dict[str, Any]:
    curves = visual_cues.get("curves")
    if not isinstance(curves, dict):
        raise ValueError("visual cues must contain curves")
    points_by_curve = {
        curve_name: _validated_curve_points(curve_name, curves.get(curve_name))
        for curve_name in _AUDIO_FEATURE_TO_CUE_CURVE.values()
    }
    shot_plans: dict[str, dict[str, Any]] = {}
    total_keyframes = 0
    maximum_applied_influence = 0.0
    for shot in shots:
        shot_id = str(shot["id"])
        frame_start = int(shot["frameStart"])
        frame_end = int(shot["frameEnd"])
        layers = shot.get("audioReactiveLayers")
        if not isinstance(layers, list) or not layers:
            raise ValueError(f"shot {shot_id} must declare audio-reactive layers")
        resolved_layers: list[dict[str, Any]] = []
        frame_set = {frame_start, frame_end}
        for layer in layers:
            if not isinstance(layer, dict):
                raise ValueError(f"shot {shot_id} contains an invalid audio-reactive layer")
            source_feature = layer.get("sourceFeature")
            curve_name = _AUDIO_FEATURE_TO_CUE_CURVE.get(str(source_feature))
            declared_influence = layer.get("maximumInfluenceFraction")
            if (
                curve_name is None
                or not _finite_number(declared_influence)
                or not 0.0 <= float(declared_influence) <= 1.0
                or layer.get("controlsMajorCameraOrProtagonistTravel") is not False
            ):
                raise ValueError(f"shot {shot_id} has an unsafe audio-reactive layer")
            applied_influence = min(
                float(declared_influence),
                _MAXIMUM_LIGHTING_INFLUENCE_FRACTION,
            )
            maximum_applied_influence = max(
                maximum_applied_influence,
                applied_influence,
            )
            curve_points = points_by_curve[curve_name]
            frame_set.update(
                _curve_frames_for_shot(curve_points, frame_start, frame_end)
            )
            resolved_layers.append(
                {
                    "sourceFeature": source_feature,
                    "curve": curve_name,
                    "declaredMaximumInfluenceFraction": float(declared_influence),
                    "appliedMaximumInfluenceFraction": applied_influence,
                    "points": curve_points,
                }
            )
        keyframes: list[dict[str, Any]] = []
        for frame in sorted(frame_set):
            contributions = [
                (
                    _curve_value_at(layer["points"], frame) * 2.0 - 1.0
                )
                * layer["appliedMaximumInfluenceFraction"]
                for layer in resolved_layers
            ]
            factor = 1.0 + sum(contributions) / len(contributions)
            factor = max(
                1.0 - _MAXIMUM_LIGHTING_INFLUENCE_FRACTION,
                min(1.0 + _MAXIMUM_LIGHTING_INFLUENCE_FRACTION, factor),
            )
            keyframes.append({"frame": frame, "energyFactor": factor})
        total_keyframes += len(keyframes)
        shot_plans[shot_id] = {
            "actId": str(shot["actId"]),
            "layers": [
                {
                    key: value
                    for key, value in layer.items()
                    if key != "points"
                }
                for layer in resolved_layers
            ],
            "keyframes": keyframes,
        }
    return {
        "sourceFeatureToCurve": dict(_AUDIO_FEATURE_TO_CUE_CURVE),
        "maximumInfluenceFraction": _MAXIMUM_LIGHTING_INFLUENCE_FRACTION,
        "maximumAppliedInfluenceFraction": maximum_applied_influence,
        "controlsMajorCameraOrProtagonistTravel": False,
        "shotPlans": shot_plans,
        "curvePointKeyframeCount": total_keyframes,
    }


def _animate_bounded_lighting(
    lights: list[Any],
    shots: list[dict[str, Any]],
    visual_cues: Mapping[str, Any] | None,
    source_cue_sha256: str | None,
) -> dict[str, Any]:
    if visual_cues is None:
        for light in lights:
            light["trackprompt_audio_reactivity"] = "disabled-no-visual-cues"
        return {
            "supplied": False,
            "applied": False,
            "sha256": None,
            "reason": "private visual cues were not supplied",
            "sourceFeatureToCurve": dict(_AUDIO_FEATURE_TO_CUE_CURVE),
            "maximumInfluenceFraction": _MAXIMUM_LIGHTING_INFLUENCE_FRACTION,
            "controlsMajorCameraOrProtagonistTravel": False,
            "lightEnergyKeyframeCount": 0,
        }
    if source_cue_sha256 is None:
        raise ValueError("validated visual cues require a source-cue SHA-256")
    plan = _bounded_lighting_plan(shots, visual_cues)
    applied_keyframes = 0
    for light in lights:
        act_id = str(light.get("trackprompt_act_id", ""))
        base_energy = float(light.data.energy)
        for shot in shots:
            shot_plan = plan["shotPlans"][str(shot["id"])]
            if shot_plan["actId"] != act_id:
                continue
            for keyframe in shot_plan["keyframes"]:
                light.data.energy = base_energy * float(keyframe["energyFactor"])
                light.data.keyframe_insert(
                    "energy",
                    frame=int(keyframe["frame"]),
                )
                applied_keyframes += 1
        light["trackprompt_audio_reactivity"] = "validated-smoothed-cue-curves"
        light["trackprompt_source_cue_sha256"] = source_cue_sha256
    return {
        "supplied": True,
        "applied": True,
        "sha256": source_cue_sha256,
        "schemaVersion": visual_cues.get("schemaVersion"),
        "sourceFeatureToCurve": plan["sourceFeatureToCurve"],
        "maximumInfluenceFraction": plan["maximumInfluenceFraction"],
        "maximumAppliedInfluenceFraction": plan[
            "maximumAppliedInfluenceFraction"
        ],
        "controlsMajorCameraOrProtagonistTravel": False,
        "curvePointKeyframeCount": plan["curvePointKeyframeCount"],
        "lightEnergyKeyframeCount": applied_keyframes,
    }


def _action_fcurves(action: Any) -> list[Any]:
    """Return legacy and Blender 5.2 layered-action F-curves."""

    fcurves = list(getattr(action, "fcurves", ()))
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for channelbag in getattr(strip, "channelbags", ()):
                fcurves.extend(getattr(channelbag, "fcurves", ()))
    return fcurves


def _action_identity(action: Any) -> int:
    as_pointer = getattr(action, "as_pointer", None)
    if callable(as_pointer):
        return int(as_pointer())
    return id(action)


def _normalize_animation_interpolation(objects: list[Any]) -> int:
    """Set every object/data keyframe to LINEAR and return the point count."""

    normalized = 0
    visited_actions: set[int] = set()
    for obj in objects:
        for owner in (obj, getattr(obj, "data", None)):
            if owner is None:
                continue
            animation_data = getattr(owner, "animation_data", None)
            action = getattr(animation_data, "action", None)
            if action is None:
                continue
            action_identity = _action_identity(action)
            if action_identity in visited_actions:
                continue
            visited_actions.add(action_identity)
            for fcurve in _action_fcurves(action):
                for keyframe in getattr(fcurve, "keyframe_points", ()):
                    keyframe.interpolation = "LINEAR"
                    normalized += 1
    return normalized


def _intentional_cut_frames(shots: list[dict[str, Any]]) -> list[int]:
    return sorted(
        {
            int(shot["frameStart"])
            for shot in shots
            if shot.get("intentionalCut") is True
        }
    )


def _apply_and_read_setting(
    candidates: list[tuple[Any, str, str]],
    requested: int | bool | None,
) -> dict[str, Any]:
    if requested is None:
        return {
            "requested": None,
            "applied": None,
            "source": None,
            "status": "not-requested",
        }
    for owner, attribute, source in candidates:
        if owner is None or not hasattr(owner, attribute):
            continue
        try:
            setattr(owner, attribute, requested)
        except (AttributeError, TypeError, ValueError):
            try:
                applied = getattr(owner, attribute)
            except (AttributeError, TypeError, ValueError):
                continue
            if applied == requested:
                return {
                    "requested": requested,
                    "applied": applied,
                    "source": source,
                    "status": "introspected-default",
                }
            continue
        try:
            applied = getattr(owner, attribute)
        except (AttributeError, TypeError, ValueError):
            continue
        return {
            "requested": requested,
            "applied": applied,
            "source": source,
            "status": "applied" if applied == requested else "mismatch",
        }
    return {
        "requested": requested,
        "applied": None,
        "source": None,
        "status": "unavailable",
    }


def _setting_satisfied(receipt: Mapping[str, Any]) -> bool:
    return receipt.get("status") in {"applied", "introspected-default"}


def configure_render_mode(scene: Any, mode: str) -> dict[str, Any]:
    if mode not in RENDER_MODES:
        raise ValueError("render mode must be master or animatic-fast")
    engine_requested = "BLENDER_EEVEE_NEXT"
    try:
        scene.render.engine = engine_requested
    except (TypeError, ValueError):
        engine_requested = "BLENDER_EEVEE"
        scene.render.engine = engine_requested
    engine_applied = str(scene.render.engine)
    if mode == ANIMATIC_FAST_MODE:
        scene.render.resolution_percentage = 25
        direct_movie_output = True
        try:
            scene.render.image_settings.file_format = "FFMPEG"
        except (TypeError, ValueError):
            scene.render.image_settings.file_format = "PNG"
            scene.render.image_settings.color_mode = "RGB"
            direct_movie_output = False
        scene.render.image_settings.color_depth = "8"
        scene.render.ffmpeg.format = "MPEG4"
        scene.render.ffmpeg.codec = "H264"
        scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
        scene.render.ffmpeg.audio_codec = "AAC"
        scene.render.ffmpeg.audio_bitrate = 192
        scene.render.ffmpeg.audio_mixrate = 44100
        scene.render.ffmpeg.audio_channels = "STEREO"
        temporal_samples = 2
    else:
        direct_movie_output = False
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGB"
        scene.render.image_settings.color_depth = "16"
        temporal_samples = 64
    eevee = getattr(scene, "eevee", None)
    temporal_state = _apply_and_read_setting(
        [
            (eevee, "taa_render_samples", "scene.eevee.taa_render_samples"),
            (eevee, "taa_samples", "scene.eevee.taa_samples"),
            (
                scene.render,
                "taa_render_samples",
                "scene.render.taa_render_samples",
            ),
        ],
        temporal_samples,
    )
    volumetric_state = _apply_and_read_setting(
        [
            (eevee, "volumetric_samples", "scene.eevee.volumetric_samples"),
            (
                eevee,
                "volumetric_sample_count",
                "scene.eevee.volumetric_sample_count",
            ),
            (
                scene.render,
                "volumetric_samples",
                "scene.render.volumetric_samples",
            ),
        ],
        32 if mode == MASTER_MODE else None,
    )
    reprojection_state = _apply_and_read_setting(
        [
            (
                eevee,
                "use_taa_reprojection",
                "scene.eevee.use_taa_reprojection",
            ),
            (eevee, "use_reprojection", "scene.eevee.use_reprojection"),
            (
                scene.render,
                "use_reprojection",
                "scene.render.use_reprojection",
            ),
        ],
        True if mode == MASTER_MODE else None,
    )
    motion_blur_state = _apply_and_read_setting(
        [
            (
                scene.render,
                "use_motion_blur",
                "scene.render.use_motion_blur",
            ),
            (eevee, "use_motion_blur", "scene.eevee.use_motion_blur"),
        ],
        False,
    )
    locked_settings = {
        "temporalSamples": temporal_state,
        "volumetricSamples": volumetric_state,
        "reprojection": reprojection_state,
        "motionBlur": motion_blur_state,
    }
    required_setting_names = (
        (
            "temporalSamples",
            "volumetricSamples",
            "reprojection",
            "motionBlur",
        )
        if mode == MASTER_MODE
        else ("temporalSamples", "motionBlur")
    )
    locked_settings_satisfied = all(
        _setting_satisfied(locked_settings[name])
        for name in required_setting_names
    )
    if mode == MASTER_MODE and not locked_settings_satisfied:
        unavailable = [
            name
            for name in required_setting_names
            if not _setting_satisfied(locked_settings[name])
        ]
        raise RuntimeError(
            "master render settings cannot be locked and read back: "
            + ", ".join(unavailable)
        )
    scene["trackprompt_render_mode"] = mode
    scene["trackprompt_requested_temporal_samples"] = temporal_samples
    scene["trackprompt_temporal_samples"] = (
        int(temporal_state["applied"])
        if isinstance(temporal_state["applied"], int)
        else 0
    )
    scene["trackprompt_requested_volumetric_samples"] = (
        32 if mode == MASTER_MODE else 0
    )
    scene["trackprompt_volumetric_samples"] = (
        int(volumetric_state["applied"])
        if isinstance(volumetric_state["applied"], int)
        else 0
    )
    scene["trackprompt_reprojection_enabled"] = (
        reprojection_state["applied"]
        if isinstance(reprojection_state["applied"], bool)
        else False
    )
    scene["trackprompt_motion_blur_enabled"] = (
        motion_blur_state["applied"]
        if isinstance(motion_blur_state["applied"], bool)
        else True
    )
    scene["trackprompt_locked_render_settings_satisfied"] = (
        locked_settings_satisfied
    )
    scene["trackprompt_animatic_audio_enabled"] = mode == ANIMATIC_FAST_MODE
    scene["trackprompt_animatic_audio_codec"] = "AAC" if mode == ANIMATIC_FAST_MODE else ""
    scene["trackprompt_animatic_audio_sample_rate"] = (
        44100 if mode == ANIMATIC_FAST_MODE else 0
    )
    scene["trackprompt_animatic_audio_channels"] = (
        "stereo" if mode == ANIMATIC_FAST_MODE else ""
    )
    scene["trackprompt_render_started"] = False
    return {
        "mode": mode,
        "engine": {
            "requested": engine_requested,
            "applied": engine_applied,
            "status": (
                "applied"
                if engine_applied == engine_requested
                else "mismatch"
            ),
        },
        "resolutionPercentage": int(scene.render.resolution_percentage),
        "requestedTemporalSamples": temporal_samples,
        "temporalSamples": temporal_state["applied"],
        "requestedVolumetricSamples": 32 if mode == MASTER_MODE else None,
        "volumetricSamples": volumetric_state["applied"],
        "reprojection": reprojection_state["applied"],
        "motionBlur": motion_blur_state["applied"],
        "lockedSettings": locked_settings,
        "lockedSettingsSatisfied": locked_settings_satisfied,
        "audioCodec": "AAC" if mode == ANIMATIC_FAST_MODE else None,
        "audioSampleRate": 44100 if mode == ANIMATIC_FAST_MODE else None,
        "audioChannels": "stereo" if mode == ANIMATIC_FAST_MODE else None,
        "directMovieOutput": direct_movie_output,
        "outputMode": (
            "ffmpeg-mp4"
            if direct_movie_output
            else "png-sequence-with-aac-encode-contract"
            if mode == ANIMATIC_FAST_MODE
            else "png-master-sequence"
        ),
        "renderStarted": False,
    }


def select_output_composition(scene: Any, composition_id: str) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    if composition_id in OUTPUT_VARIANT_IDS:
        variant_id = composition_id
    elif composition_id.startswith("andromeda-v2-horizontal-"):
        variant_id = HORIZONTAL_VARIANT_ID
    elif composition_id.startswith("andromeda-v2-vertical-"):
        variant_id = VERTICAL_VARIANT_ID
    else:
        raise ValueError("unknown Andromeda V2 composition ID")
    base = authored_composition_profile(variant_id, "signal")
    camera = bpy.data.objects.get(str(base["cameraName"]))
    if camera is None or getattr(camera, "type", None) != "CAMERA":
        raise ValueError("the selected Andromeda V2 camera is missing")
    scene.camera = camera
    scene.render.resolution_x = int(base["width"])
    scene.render.resolution_y = int(base["height"])
    scene.render.resolution_percentage = int(scene.render.resolution_percentage)
    scene["trackprompt_output_variant_id"] = variant_id
    scene["trackprompt_composition_id"] = composition_id
    scene["trackprompt_composition_crop_policy"] = "native-authored-never-crop"
    scene["trackprompt_vertical_enabled"] = variant_id == VERTICAL_VARIANT_ID
    return {
        "outputVariantId": variant_id,
        "compositionId": composition_id,
        "camera": camera.name,
        "width": int(base["width"]),
        "height": int(base["height"]),
        "cropPolicy": "native-authored-never-crop",
    }


def attach_bound_source_audio(scene: Any, audio_path: str | Path) -> dict[str, Any]:
    path = Path(audio_path).resolve()
    if not path.is_file() or _file_sha256(path) != ANDROMEDA_V2_SOURCE_AUDIO_SHA256:
        raise ValueError("audio must match the private Andromeda source-audio binding")
    editor = scene.sequence_editor_create()
    strips = getattr(editor, "strips", None)
    if strips is None:
        strips = getattr(editor, "sequences", None)
    if strips is None or not hasattr(strips, "new_sound"):
        raise RuntimeError("the Blender sequence editor cannot create a sound strip")
    sound = strips.new_sound(
        "TP_ANDROMEDA_V2_SOURCE_AUDIO",
        str(path),
        channel=1,
        frame_start=ANDROMEDA_V2_FRAME_START,
    )
    sound["trackprompt_private_local_artifact"] = True
    sound["trackprompt_source_audio_sha256"] = ANDROMEDA_V2_SOURCE_AUDIO_SHA256
    scene["trackprompt_source_audio_attached"] = True
    scene["trackprompt_source_audio_sha256"] = ANDROMEDA_V2_SOURCE_AUDIO_SHA256
    return {
        "attached": True,
        "sha256": ANDROMEDA_V2_SOURCE_AUDIO_SHA256,
        "frameStart": ANDROMEDA_V2_FRAME_START,
    }


def _embed_contracts(
    scene: Any,
    contracts: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> None:
    import bpy  # type: ignore[import-not-found]

    compact = lambda payload: json.dumps(  # noqa: E731
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    story_json = compact(contracts["story"])
    shots_json = compact(contracts["shots"])
    complexity_json = compact(spec["complexityByShot"])
    composition_json = compact(spec["compositionProfiles"])
    scene["trackprompt_project_id"] = ANDROMEDA_V2_PROJECT_ID
    scene["trackprompt_preset"] = "andromeda-story-v2"
    scene["trackprompt_story_schema_version"] = contracts["story"]["schemaVersion"]
    scene["trackprompt_shot_schema_version"] = contracts["shots"]["schemaVersion"]
    scene["trackprompt_story_plan_json"] = story_json
    scene["trackprompt_shot_plan_json"] = shots_json
    scene["trackprompt_complexity_classes_json"] = complexity_json
    scene["trackprompt_composition_profiles_json"] = composition_json
    scene["trackprompt_scene_spec_sha256"] = spec["canonicalSha256"]
    scene["trackprompt_look_profile_id"] = ANDROMEDA_V2_LOOK_PROFILE_ID
    scene["trackprompt_look_profile_sha256"] = contracts["shots"]["lookProfileSha256"]
    scene["trackprompt_source_audio_sha256"] = ANDROMEDA_V2_SOURCE_AUDIO_SHA256
    scene["trackprompt_source_audio_attached"] = False
    scene["trackprompt_production_authorized"] = False
    scene["trackprompt_final_render_started"] = False
    scene["trackprompt_vertical_enabled_by_default"] = False
    scene["trackprompt_intentional_cut_frames_json"] = compact(
        _intentional_cut_frames(contracts["shots"]["shots"])
    )
    for name, content in (
        ("TP_ANDROMEDA_V2_STORY_PLAN_JSON", story_json),
        ("TP_ANDROMEDA_V2_SHOT_PLAN_JSON", shots_json),
        ("TP_ANDROMEDA_V2_COMPLEXITY_CLASSES_JSON", complexity_json),
        ("TP_ANDROMEDA_V2_COMPOSITION_PROFILES_JSON", composition_json),
    ):
        text = bpy.data.texts.new(name)
        text.write(content)


def build_and_save_andromeda_v2_master(
    repository_root: str | Path,
    output_blend: str | Path,
    *,
    composition_id: str = HORIZONTAL_VARIANT_ID,
    render_mode: str = MASTER_MODE,
    audio_path: str | Path | None = None,
    visual_cues_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build and save the complete 35-shot master scene without starting a render."""

    import bpy  # type: ignore[import-not-found]

    contracts = load_andromeda_v2_source_contracts(repository_root)
    spec = build_andromeda_v2_scene_spec(repository_root)
    source_cue_sha256 = str(contracts["story"]["sourceCueSha256"])
    visual_cues = (
        load_and_validate_visual_cues(
            visual_cues_path,
            source_cue_sha256,
        )
        if visual_cues_path is not None
        else None
    )
    output = Path(output_blend).resolve()
    if output.suffix.lower() != ".blend":
        raise ValueError("Andromeda V2 master output must use the .blend extension")
    output.parent.mkdir(parents=True, exist_ok=True)

    _clear_scene()
    scene = bpy.context.scene
    scene.name = "TP_ANDROMEDA_V2_MASTER"
    scene.frame_start = ANDROMEDA_V2_FRAME_START
    scene.frame_end = ANDROMEDA_V2_FRAME_END
    scene.render.fps = ANDROMEDA_V2_FPS
    scene.render.fps_base = 1.0

    root_collection = _collection("TP_ANDROMEDA_V2", scene.collection)
    environment_root = _collection("TP_ANDROMEDA_V2_ENVIRONMENTS", root_collection)
    protagonist_collection = _collection("TP_ANDROMEDA_V2_PROTAGONIST", root_collection)
    camera_collection = _collection("TP_ANDROMEDA_V2_CAMERAS", root_collection)
    light_collection = _collection("TP_ANDROMEDA_V2_LIGHTS", root_collection)

    materials: dict[str, Any] = {
        "stone": _material(
            "TP_ANDROMEDA_V2_MAT_WEATHERED_STONE",
            (0.08, 0.10, 0.11, 1.0),
            metallic=0.62,
            roughness=0.47,
        ),
        "dark": _material(
            "TP_ANDROMEDA_V2_MAT_DARK_VOID",
            (0.008, 0.012, 0.016, 1.0),
            metallic=0.05,
            roughness=0.82,
        ),
        "hero": _material(
            "TP_ANDROMEDA_V2_MAT_ANCIENT_ENGINE",
            (0.14, 0.18, 0.19, 1.0),
            metallic=0.78,
            roughness=0.27,
        ),
        "crystal": _material(
            "TP_ANDROMEDA_V2_MAT_CRYSTAL",
            (0.05, 0.26, 0.29, 1.0),
            metallic=0.20,
            roughness=0.18,
            emission_color=(0.08, 0.68, 0.72, 1.0),
            emission_strength=1.5,
        ),
        "amber": _material(
            "TP_ANDROMEDA_V2_MAT_AMBER",
            (0.44, 0.20, 0.05, 1.0),
            metallic=0.24,
            roughness=0.24,
            emission_color=(1.0, 0.42, 0.08, 1.0),
            emission_strength=2.0,
        ),
    }
    for act_id, color in _ACT_PALETTES.items():
        materials[act_id] = _material(
            f"TP_ANDROMEDA_V2_MAT_{act_id.upper()}",
            color,
            metallic=0.38,
            roughness=0.34,
            emission_color=color,
            emission_strength=0.55,
        )

    environment_objects: dict[str, list[Any]] = {}
    for act_id in _ACT_ORDER:
        act_collection = _collection(f"TP_ANDROMEDA_V2_ACT_{act_id.upper()}", environment_root)
        environment_objects[act_id] = _build_environment(
            act_id,
            act_collection,
            materials,
        )

    protagonist = _build_protagonist(protagonist_collection, materials)
    lights = _build_lights(light_collection)
    cameras = {
        HORIZONTAL_VARIANT_ID: _new_camera(
            "TP_ANDROMEDA_V2_CAMERA_HORIZONTAL",
            camera_collection,
        ),
        VERTICAL_VARIANT_ID: _new_camera(
            "TP_ANDROMEDA_V2_CAMERA_VERTICAL",
            camera_collection,
        ),
    }
    shot_entries = contracts["shots"]["shots"]
    _animate_story(protagonist, cameras, shot_entries)
    _animate_environment_actions(environment_objects, shot_entries)
    visual_cue_state = _animate_bounded_lighting(
        lights,
        shot_entries,
        visual_cues,
        source_cue_sha256 if visual_cues is not None else None,
    )
    animated_objects = [
        protagonist,
        *cameras.values(),
        *lights,
        *(
            obj
            for objects in environment_objects.values()
            for obj in objects
        ),
    ]
    linear_keyframe_count = _normalize_animation_interpolation(animated_objects)

    for shot in shot_entries:
        scene.timeline_markers.new(str(shot["id"]), frame=int(shot["frameStart"]))

    world = bpy.data.worlds.new("TP_ANDROMEDA_V2_WORLD")
    world.use_nodes = True
    scene.world = world
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.03, 0.065, 0.09, 1.0)
        background.inputs["Strength"].default_value = 0.18
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.65

    _embed_contracts(scene, contracts, spec)
    scene["trackprompt_visual_cues_supplied"] = visual_cue_state["supplied"]
    scene["trackprompt_visual_cues_applied"] = visual_cue_state["applied"]
    scene["trackprompt_visual_cues_sha256"] = (
        visual_cue_state["sha256"] or ""
    )
    scene["trackprompt_visual_cue_bindings_json"] = json.dumps(
        visual_cue_state["sourceFeatureToCurve"],
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    scene["trackprompt_fcurve_interpolation"] = "LINEAR"
    scene["trackprompt_linear_keyframe_count"] = linear_keyframe_count
    render_state = configure_render_mode(scene, render_mode)
    composition_state = select_output_composition(scene, composition_id)
    audio_state = (
        attach_bound_source_audio(scene, audio_path)
        if audio_path is not None
        else {
            "attached": False,
            "sha256": ANDROMEDA_V2_SOURCE_AUDIO_SHA256,
            "reason": "private source audio was not supplied",
        }
    )
    scene.frame_set(ANDROMEDA_V2_FRAME_START)
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)

    summary: dict[str, Any] = {
        "ok": True,
        "schemaVersion": "1.0.0",
        "builderId": "andromeda-v2-master-scene-builder-v1",
        "projectId": ANDROMEDA_V2_PROJECT_ID,
        "outputBlend": str(output),
        "frameStart": ANDROMEDA_V2_FRAME_START,
        "frameEnd": ANDROMEDA_V2_FRAME_END,
        "fps": ANDROMEDA_V2_FPS,
        "actCount": len(_ACT_ORDER),
        "shotCount": len(shot_entries),
        "environmentObjectCounts": {
            act_id: len(objects) for act_id, objects in environment_objects.items()
        },
        "composition": composition_state,
        "renderMode": render_state,
        "audio": audio_state,
        "visualCues": visual_cue_state,
        "intentionalCutFrames": _intentional_cut_frames(shot_entries),
        "animationInterpolation": {
            "mode": "LINEAR",
            "keyframePointCount": linear_keyframe_count,
            "bezierOvershootEnabled": False,
        },
        "sceneSpecSha256": spec["canonicalSha256"],
        "productionAuthorized": False,
        "renderStarted": False,
    }
    summary["canonicalSha256"] = _canonical_sha256(summary)
    manifest_path = output.with_suffix(".build.json")
    manifest_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=True, allow_nan=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Andromeda V2 master scene.")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--composition",
        default=HORIZONTAL_VARIANT_ID,
        help="Output variant ID or authored composition profile ID.",
    )
    parser.add_argument("--render-mode", choices=RENDER_MODES, default=MASTER_MODE)
    parser.add_argument("--audio", default=None)
    parser.add_argument("--visual-cues", default=None)
    return parser.parse_args(argv)


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    args = _arguments(argv)
    summary = build_and_save_andromeda_v2_master(
        args.repository_root,
        args.output,
        composition_id=args.composition,
        render_mode=args.render_mode,
        audio_path=args.audio,
        visual_cues_path=args.visual_cues,
    )
    print(json.dumps(summary, ensure_ascii=True, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

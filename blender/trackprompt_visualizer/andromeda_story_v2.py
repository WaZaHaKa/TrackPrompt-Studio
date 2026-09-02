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
ANDROMEDA_V2_BUILDER_ID = "andromeda-v2-master-scene-builder-v2"
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
_STORY_ACTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "fracture-impact",
        ("physical impact", "visibly damages", "fracture impact", "damaged engine"),
    ),
    (
        "component-release",
        ("remove only", "removed components", "armor unmake", "damaged armor"),
    ),
    (
        "aperture-rebirth",
        ("aperture rebirth", "reignite", "aperture stabilizes", "front aperture"),
    ),
    (
        "route-repair",
        ("bridge the damaged", "completed route", "crystal routing", "repaired route"),
    ),
    (
        "transformed-release",
        ("rebuilt engine", "transformed release", "release the rebuilt", "under its own power"),
    ),
    (
        "arrival-settle",
        ("at rest", "held orientation", "decelerates", "quiet arrival", "chosen rest"),
    ),
    (
        "cradle-capture",
        ("catch the damaged", "arrest motion", "cradle capture", "connected functional arms"),
    ),
    (
        "localized-crossing",
        ("cross the membrane", "localized compression", "threshold membrane", "crossing"),
    ),
    (
        "mechanical-alignment",
        ("align", "alignment", "locking", "seal", "mechanically settle"),
    ),
    (
        "directional-sweep",
        ("sweep", "searches", "directional event", "chosen direction"),
    ),
    (
        "mechanical-opening",
        ("opens", "open the", "release", "withdraw", "parting"),
    ),
    (
        "signal-pulse",
        ("signal", "pulse", "ignite", "ignition", "answers the aperture"),
    ),
)
_RING_GEOMETRY_TOKENS = (
    "aperture",
    "arc",
    "horizon",
    "iris",
    "membrane",
    "orbit",
    "ring",
)
_CRYSTAL_GEOMETRY_TOKENS = (
    "beacon",
    "core",
    "crystal",
    "dust",
    "moon",
    "signal",
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _story_geometry_kind(label: str) -> str:
    normalized = label.casefold()
    if any(token in normalized for token in _RING_GEOMETRY_TOKENS):
        return "ring"
    if any(token in normalized for token in _CRYSTAL_GEOMETRY_TOKENS):
        return "crystal"
    return "connected-structure"


def _story_action_family(story_purpose: str, secondary_action: str) -> str:
    # The authored primary purpose owns the shot's action family. Secondary
    # action text is a fallback only, so a transition hint cannot override the
    # principal event (for example route repair leading into aperture rebirth).
    for narrative in (story_purpose.casefold(), secondary_action.casefold()):
        for family, tokens in _STORY_ACTION_RULES:
            if any(token in narrative for token in tokens):
                return family
    return "authored-travel"


def _controller_story_states(
    *,
    frame_start: int,
    frame_mid: int,
    frame_end: int,
    family: str,
    digest: bytes,
) -> list[dict[str, Any]]:
    amplitude = 0.28 + (digest[0] / 255.0) * 0.42
    direction = -1.0 if digest[1] % 2 else 1.0
    turn = (0.12 + (digest[2] / 255.0) * 0.30) * direction
    lift = 0.10 + (digest[3] / 255.0) * 0.34
    scale_triplets: dict[str, tuple[float, float, float]] = {
        "signal-pulse": (0.84, 1.14, 1.0),
        "directional-sweep": (0.92, 1.05, 1.0),
        "mechanical-opening": (0.76, 1.02, 1.13),
        "mechanical-alignment": (1.12, 0.88, 1.0),
        "localized-crossing": (1.0, 0.72, 1.04),
        "fracture-impact": (1.0, 1.18, 0.86),
        "cradle-capture": (1.08, 0.82, 0.94),
        "component-release": (1.0, 0.72, 0.38),
        "route-repair": (0.52, 0.82, 1.0),
        "aperture-rebirth": (0.62, 1.22, 1.04),
        "transformed-release": (0.78, 1.08, 1.0),
        "arrival-settle": (1.06, 1.0, 0.96),
        "authored-travel": (0.94, 1.04, 1.0),
    }
    start_scale, mid_scale, end_scale = scale_triplets[family]
    end_travel = (
        amplitude * 0.35
        if family
        in {
            "mechanical-opening",
            "transformed-release",
            "authored-travel",
        }
        else 0.0
    )
    return [
        {
            "frame": frame_start,
            "locationOffset": (0.0, 0.0, 0.0),
            "rotationOffset": (0.0, -turn * 0.18, -turn * 0.32),
            "scaleFactor": start_scale,
        },
        {
            "frame": frame_mid,
            "locationOffset": (
                direction * amplitude,
                amplitude * 0.22,
                lift,
            ),
            "rotationOffset": (turn * 0.62, -turn * 0.38, turn),
            "scaleFactor": mid_scale,
        },
        {
            "frame": frame_end,
            "locationOffset": (
                direction * end_travel,
                end_travel * 0.65,
                lift * 0.25,
            ),
            "rotationOffset": (turn * 0.12, turn * 0.10, turn * 0.18),
            "scaleFactor": end_scale,
        },
    ]


def build_shot_story_action_plan(
    shots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compile every authored narrative field into deterministic scene actions."""

    shot_actions: list[dict[str, Any]] = []
    for shot in shots:
        shot_id = shot.get("id")
        act_id = shot.get("actId")
        story_purpose = shot.get("storyPurpose")
        secondary_action = shot.get("secondaryNarrativeAction")
        dominant_shape = shot.get("dominantShape")
        required_landmarks = shot.get("requiredLandmarks")
        frame_start = shot.get("frameStart")
        frame_end = shot.get("frameEnd")
        if (
            not isinstance(shot_id, str)
            or not shot_id
            or act_id not in _ACT_ORDER
            or not isinstance(story_purpose, str)
            or not story_purpose.strip()
            or not isinstance(secondary_action, str)
            or not secondary_action.strip()
            or not isinstance(dominant_shape, str)
            or not dominant_shape.strip()
            or not isinstance(required_landmarks, list)
            or not required_landmarks
            or any(
                not isinstance(landmark, str) or not landmark.strip()
                for landmark in required_landmarks
            )
            or not isinstance(frame_start, int)
            or not isinstance(frame_end, int)
            or frame_end < frame_start
        ):
            raise ValueError(
                "every shot requires executable story purpose, secondary action, "
                "dominant shape, landmarks, and frame bounds"
            )
        signature_payload = {
            "shotId": shot_id,
            "actId": act_id,
            "storyPurpose": story_purpose,
            "secondaryNarrativeAction": secondary_action,
            "dominantShape": dominant_shape,
            "requiredLandmarks": required_landmarks,
        }
        action_signature = _canonical_sha256(signature_payload)
        digest = bytes.fromhex(action_signature)
        frame_mid = (frame_start + frame_end) // 2
        action_family = _story_action_family(story_purpose, secondary_action)
        landmarks = [
            {
                "id": landmark,
                "geometryKind": _story_geometry_kind(landmark),
                "pulsePhase": round(
                    int.from_bytes(
                        hashlib.sha256(
                            f"{action_signature}:{landmark}".encode("utf-8")
                        ).digest()[:2],
                        byteorder="big",
                    )
                    / 65535.0,
                    6,
                ),
            }
            for landmark in required_landmarks
        ]
        shot_actions.append(
            {
                "shotId": shot_id,
                "sequence": int(shot["sequence"]),
                "actId": act_id,
                "frameStart": frame_start,
                "frameMid": frame_mid,
                "frameEnd": frame_end,
                "storyPurpose": story_purpose,
                "storyPurposeSha256": hashlib.sha256(
                    story_purpose.encode("utf-8")
                ).hexdigest(),
                "secondaryNarrativeAction": secondary_action,
                "secondaryNarrativeActionSha256": hashlib.sha256(
                    secondary_action.encode("utf-8")
                ).hexdigest(),
                "dominantShape": {
                    "id": dominant_shape,
                    "geometryKind": _story_geometry_kind(dominant_shape),
                },
                "requiredLandmarks": landmarks,
                "actionFamily": action_family,
                "actionSignatureSha256": action_signature,
                "motionSeed": int.from_bytes(digest[:4], byteorder="big"),
                "controllerStates": _controller_story_states(
                    frame_start=frame_start,
                    frame_mid=frame_mid,
                    frame_end=frame_end,
                    family=action_family,
                    digest=digest,
                ),
            }
        )
    plan: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "projectId": ANDROMEDA_V2_PROJECT_ID,
        "shotActions": shot_actions,
    }
    plan["canonicalSha256"] = _canonical_sha256(plan)
    return plan


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

    story_action_plan = build_shot_story_action_plan(shots)
    source_payload = {
        "storyPlanSha256": _file_sha256(contracts["storyPath"]),
        "shotPlanSha256": _file_sha256(contracts["shotPath"]),
        "lookProfileSha256": _file_sha256(contracts["lookPath"]),
        "outputVariantsSha256": _file_sha256(contracts["variantsPath"]),
        "creativeAcceptanceSha256": _file_sha256(contracts["acceptancePath"]),
    }
    spec: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "builderId": ANDROMEDA_V2_BUILDER_ID,
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
        "storyActionCount": len(story_action_plan["shotActions"]),
        "storyActionPlanSha256": story_action_plan["canonicalSha256"],
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
    alpha: float = 1.0,
    transmission: float = 0.0,
    noise_scale: float = 7.0,
    bump_strength: float = 0.08,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = (*base_color[:3], alpha)
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    node_tree = material.node_tree
    principled = node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        for socket_name, value in (
            ("Base Color", base_color),
            ("Metallic", metallic),
            ("Roughness", roughness),
            ("Alpha", alpha),
        ):
            socket = principled.inputs.get(socket_name)
            if socket is not None:
                socket.default_value = value
        emission = principled.inputs.get("Emission Color") or principled.inputs.get("Emission")
        strength = principled.inputs.get("Emission Strength")
        transmission_socket = principled.inputs.get(
            "Transmission Weight"
        ) or principled.inputs.get("Transmission")
        if emission is not None:
            emission.default_value = emission_color or base_color
        if strength is not None:
            strength.default_value = emission_strength
        if transmission_socket is not None:
            transmission_socket.default_value = transmission

        texture_coordinate = node_tree.nodes.new("ShaderNodeTexCoord")
        texture_coordinate.name = f"{name}_COORDINATES"
        noise = node_tree.nodes.new("ShaderNodeTexNoise")
        noise.name = f"{name}_WEATHERING"
        noise.inputs["Scale"].default_value = noise_scale
        noise.inputs["Detail"].default_value = 5.0
        noise.inputs["Roughness"].default_value = 0.72
        color_ramp = node_tree.nodes.new("ShaderNodeValToRGB")
        color_ramp.name = f"{name}_WEATHERED_COLOR"
        color_ramp.color_ramp.elements[0].position = 0.24
        color_ramp.color_ramp.elements[0].color = (
            max(0.0, base_color[0] * 0.38),
            max(0.0, base_color[1] * 0.42),
            max(0.0, base_color[2] * 0.46),
            alpha,
        )
        color_ramp.color_ramp.elements[1].position = 0.78
        color_ramp.color_ramp.elements[1].color = (
            min(1.0, base_color[0] * 1.42 + 0.015),
            min(1.0, base_color[1] * 1.34 + 0.015),
            min(1.0, base_color[2] * 1.28 + 0.015),
            alpha,
        )
        bump = node_tree.nodes.new("ShaderNodeBump")
        bump.name = f"{name}_MICRO_RELIEF"
        bump.inputs["Strength"].default_value = bump_strength
        bump.inputs["Distance"].default_value = 0.12
        node_tree.links.new(texture_coordinate.outputs["Generated"], noise.inputs["Vector"])
        node_tree.links.new(noise.outputs["Fac"], color_ramp.inputs["Fac"])
        node_tree.links.new(color_ramp.outputs["Color"], principled.inputs["Base Color"])
        node_tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        normal = principled.inputs.get("Normal")
        if normal is not None:
            node_tree.links.new(bump.outputs["Normal"], normal)
    material["trackprompt_look_profile"] = ANDROMEDA_V2_LOOK_PROFILE_ID
    material["trackprompt_material_language"] = "weathered-stone-metal-crystal-v1"
    material["trackprompt_noise_scale"] = noise_scale
    material["trackprompt_bump_strength"] = bump_strength
    material["trackprompt_transparency_mode"] = "DITHERED"
    return material


def _mark(obj: Any, *, act_id: str, semantic: str) -> Any:
    obj["trackprompt_project_id"] = ANDROMEDA_V2_PROJECT_ID
    obj["trackprompt_act_id"] = act_id
    obj["trackprompt_semantic"] = semantic
    obj["trackprompt_look_profile"] = ANDROMEDA_V2_LOOK_PROFILE_ID
    return obj


def _empty(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    act_id: str,
    semantic: str,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    obj = bpy.data.objects.new(name, None)
    collection.objects.link(obj)
    obj.location = location
    return _mark(obj, act_id=act_id, semantic=semantic)


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
    bevel: float = 0.06,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _move_to_collection(obj, collection)
    obj.data.materials.append(material)
    if bevel > 0.0:
        modifier = obj.modifiers.new(name="TP_R131_EDGE_WEAR", type="BEVEL")
        modifier.width = min(bevel, min(dimensions) * 0.24)
        modifier.segments = 3
        modifier.limit_method = "ANGLE"
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
    subdivisions: int = 3,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_ico_sphere_add(
        subdivisions=subdivisions,
        radius=radius,
        location=location,
    )
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
        major_segments=64,
        minor_segments=12,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    _move_to_collection(obj, collection)
    obj.data.materials.append(material)
    return _mark(obj, act_id=act_id, semantic=semantic)


def _cylinder(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    radius: float,
    depth: float,
    material: Any,
    act_id: str,
    semantic: str,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
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
    _move_to_collection(obj, collection)
    obj.data.materials.append(material)
    modifier = obj.modifiers.new(name="TP_R131_EDGE_WEAR", type="BEVEL")
    modifier.width = min(0.055, radius * 0.12, depth * 0.12)
    modifier.segments = 3
    return _mark(obj, act_id=act_id, semantic=semantic)


def _cone(
    name: str,
    collection: Any,
    *,
    location: tuple[float, float, float],
    radius1: float,
    radius2: float,
    depth: float,
    material: Any,
    act_id: str,
    semantic: str,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Any:
    import bpy  # type: ignore[import-not-found]

    bpy.ops.mesh.primitive_cone_add(
        vertices=40,
        radius1=radius1,
        radius2=radius2,
        depth=depth,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    _move_to_collection(obj, collection)
    obj.data.materials.append(material)
    return _mark(obj, act_id=act_id, semantic=semantic)


def _beam_between(
    name: str,
    collection: Any,
    *,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    thickness: float,
    material: Any,
    act_id: str,
    semantic: str,
) -> Any:
    from mathutils import Vector  # type: ignore[import-not-found]

    start_vector = Vector(start)
    end_vector = Vector(end)
    direction = end_vector - start_vector
    length = max(0.001, float(direction.length))
    midpoint = tuple((start_vector + end_vector) * 0.5)
    beam = _cube(
        name,
        collection,
        location=midpoint,
        dimensions=(thickness, thickness, length),
        material=material,
        act_id=act_id,
        semantic=semantic,
        bevel=min(0.04, thickness * 0.28),
    )
    beam.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    return beam


def _build_shot_story_architecture(
    collection: Any,
    materials: Mapping[str, Any],
    story_action_plan: Mapping[str, Any],
) -> dict[str, list[Any]]:
    """Build connected, shot-addressable structures from the authored plan."""

    objects_by_shot: dict[str, list[Any]] = {}
    for action in story_action_plan["shotActions"]:
        shot_id = str(action["shotId"])
        sequence = int(action["sequence"])
        act_id = str(action["actId"])
        index_in_act = (sequence - 1) % 5
        anchor = _ACT_ANCHORS[act_id]
        root = _empty(
            f"TP_ANDROMEDA_V2_SHOT_{sequence:02d}_STORY",
            collection,
            location=(
                anchor[0] + (index_in_act - 2) * 1.55 + 5.8,
                anchor[1] - 2.4 + index_in_act * 3.8,
                anchor[2] + 2.8 + math.sin(sequence * 0.71) * 0.75,
            ),
            act_id=act_id,
            semantic="shot-story-controller",
        )
        root["trackprompt_shot_id"] = shot_id
        root["trackprompt_story_purpose"] = str(action["storyPurpose"])
        root["trackprompt_story_purpose_sha256"] = str(
            action["storyPurposeSha256"]
        )
        root["trackprompt_secondary_narrative_action"] = str(
            action["secondaryNarrativeAction"]
        )
        root["trackprompt_secondary_narrative_action_sha256"] = str(
            action["secondaryNarrativeActionSha256"]
        )
        root["trackprompt_dominant_shape"] = str(action["dominantShape"]["id"])
        root["trackprompt_required_landmarks_json"] = json.dumps(
            [landmark["id"] for landmark in action["requiredLandmarks"]],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        root["trackprompt_story_action_family"] = str(action["actionFamily"])
        root["trackprompt_story_action_signature_sha256"] = str(
            action["actionSignatureSha256"]
        )
        objects = [root]

        dominant = action["dominantShape"]
        dominant_kind = str(dominant["geometryKind"])
        if dominant_kind == "ring":
            dominant_object = _torus(
                f"TP_ANDROMEDA_V2_SHOT_{sequence:02d}_DOMINANT",
                collection,
                location=(0.0, 2.8, 2.1),
                major_radius=2.05,
                minor_radius=0.16,
                material=materials[act_id],
                act_id=act_id,
                semantic=f"dominant-shape:{dominant['id']}",
                rotation=(math.pi / 2.0, 0.12 * math.sin(sequence), 0.0),
            )
        elif dominant_kind == "crystal":
            dominant_object = _sphere(
                f"TP_ANDROMEDA_V2_SHOT_{sequence:02d}_DOMINANT",
                collection,
                location=(0.0, 2.8, 2.1),
                radius=1.15,
                material=materials["crystal"],
                act_id=act_id,
                semantic=f"dominant-shape:{dominant['id']}",
                subdivisions=2,
            )
            dominant_object.scale = (0.72, 1.38, 1.12)
        else:
            dominant_object = _cube(
                f"TP_ANDROMEDA_V2_SHOT_{sequence:02d}_DOMINANT",
                collection,
                location=(0.0, 2.8, 2.1),
                dimensions=(1.35, 5.6, 0.72),
                material=materials["stone"],
                act_id=act_id,
                semantic=f"dominant-shape:{dominant['id']}",
                rotation=(0.08, 0.14 * math.sin(sequence), 0.18),
                bevel=0.11,
            )
        dominant_object.parent = root
        dominant_object["trackprompt_shot_id"] = shot_id
        dominant_object["trackprompt_story_role"] = "dominant-shape"
        dominant_object["trackprompt_authored_label"] = str(dominant["id"])
        objects.append(dominant_object)

        landmark_positions = (
            (-2.75, 0.65, 1.15),
            (2.65, 3.75, 2.95),
            (0.0, 5.2, 0.75),
        )
        for landmark_index, landmark in enumerate(action["requiredLandmarks"]):
            location = landmark_positions[landmark_index % len(landmark_positions)]
            landmark_id = str(landmark["id"])
            geometry_kind = str(landmark["geometryKind"])
            if geometry_kind == "ring":
                landmark_object = _torus(
                    f"TP_ANDROMEDA_V2_SHOT_{sequence:02d}_LANDMARK_{landmark_index:02d}",
                    collection,
                    location=location,
                    major_radius=0.92 + landmark_index * 0.12,
                    minor_radius=0.075,
                    material=materials["amber" if landmark_index else "crystal"],
                    act_id=act_id,
                    semantic=landmark_id,
                    rotation=(math.pi / 2.0, landmark_index * 0.18, 0.0),
                )
            elif geometry_kind == "crystal":
                landmark_object = _sphere(
                    f"TP_ANDROMEDA_V2_SHOT_{sequence:02d}_LANDMARK_{landmark_index:02d}",
                    collection,
                    location=location,
                    radius=0.48 + landmark_index * 0.08,
                    material=materials["crystal" if landmark_index == 0 else "amber"],
                    act_id=act_id,
                    semantic=landmark_id,
                    subdivisions=2,
                )
                landmark_object.scale = (0.48, 0.82, 1.42)
            else:
                landmark_object = _cube(
                    f"TP_ANDROMEDA_V2_SHOT_{sequence:02d}_LANDMARK_{landmark_index:02d}",
                    collection,
                    location=location,
                    dimensions=(0.42, 1.45, 2.2),
                    material=materials["metal"],
                    act_id=act_id,
                    semantic=landmark_id,
                    rotation=(
                        0.08 * landmark_index,
                        -0.14 + landmark_index * 0.22,
                        0.12 * (-1.0 if landmark_index else 1.0),
                    ),
                )
            landmark_object.parent = root
            landmark_object["trackprompt_shot_id"] = shot_id
            landmark_object["trackprompt_story_role"] = "required-landmark"
            landmark_object["trackprompt_required_landmark"] = landmark_id
            landmark_object["trackprompt_landmark_pulse_phase"] = float(
                landmark["pulsePhase"]
            )
            objects.append(landmark_object)
            conduit = _beam_between(
                f"TP_ANDROMEDA_V2_SHOT_{sequence:02d}_CONDUIT_{landmark_index:02d}",
                collection,
                start=(0.0, 2.8, 2.1),
                end=location,
                thickness=0.10,
                material=materials["crystal" if landmark_index == 0 else "amber"],
                act_id=act_id,
                semantic="functional-shot-landmark-conduit",
            )
            conduit.parent = root
            conduit["trackprompt_shot_id"] = shot_id
            conduit["trackprompt_story_role"] = "connected-landmark-routing"
            conduit["trackprompt_required_landmark"] = landmark_id
            objects.append(conduit)
        objects_by_shot[shot_id] = objects
    return objects_by_shot


def _animate_shot_story_actions(
    story_objects: Mapping[str, list[Any]],
    story_action_plan: Mapping[str, Any],
) -> int:
    applied_keyframes = 0
    for action in story_action_plan["shotActions"]:
        shot_id = str(action["shotId"])
        objects = story_objects.get(shot_id)
        if not objects:
            raise ValueError(f"shot story objects are missing for {shot_id}")
        root = objects[0]
        base_location = tuple(root.location)
        base_rotation = tuple(root.rotation_euler)
        base_scale = tuple(root.scale)
        for state in action["controllerStates"]:
            frame = int(state["frame"])
            location_offset = state["locationOffset"]
            rotation_offset = state["rotationOffset"]
            scale_factor = float(state["scaleFactor"])
            root.location = tuple(
                base_location[index] + float(location_offset[index])
                for index in range(3)
            )
            root.rotation_euler = tuple(
                base_rotation[index] + float(rotation_offset[index])
                for index in range(3)
            )
            root.scale = tuple(value * scale_factor for value in base_scale)
            root.keyframe_insert("location", frame=frame)
            root.keyframe_insert("rotation_euler", frame=frame)
            root.keyframe_insert("scale", frame=frame)
            applied_keyframes += 9

        for object_index, obj in enumerate(objects[1:], start=1):
            base_component_scale = tuple(obj.scale)
            phase = float(
                obj.get(
                    "trackprompt_landmark_pulse_phase",
                    (object_index * 0.173) % 1.0,
                )
            )
            for state_index, state in enumerate(action["controllerStates"]):
                frame = int(state["frame"])
                pulse = (
                    0.96 + phase * 0.04
                    if state_index == 0
                    else 1.04 + phase * 0.10
                    if state_index == 1
                    else 1.0
                )
                obj.scale = tuple(value * pulse for value in base_component_scale)
                obj.keyframe_insert("scale", frame=frame)
                applied_keyframes += 3
            obj["trackprompt_story_action_signature_sha256"] = str(
                action["actionSignatureSha256"]
            )
    return applied_keyframes


def _animate_shot_story_visibility(
    story_objects: Mapping[str, list[Any]],
    story_action_plan: Mapping[str, Any],
    *,
    scene_frame_start: int = ANDROMEDA_V2_FRAME_START,
    scene_frame_end: int = ANDROMEDA_V2_FRAME_END,
) -> int:
    """Keep only the authored current-shot architecture visible.

    Each shot owns a complete dominant-shape/landmark/conduit assembly. Those
    assemblies intentionally share an act volume, so leaving every assembly
    visible creates an unreadable pile-up of all five shots in that act.
    Boolean visibility keys are stepped (see
    ``_normalize_animation_interpolation``) to make the hand-off exact.
    """

    applied_keyframes = 0
    for action in story_action_plan["shotActions"]:
        shot_id = str(action["shotId"])
        frame_start = int(action["frameStart"])
        frame_end = int(action["frameEnd"])
        if (
            frame_start < scene_frame_start
            or frame_end > scene_frame_end
            or frame_end < frame_start
        ):
            raise ValueError(f"shot visibility range is invalid for {shot_id}")
        objects = story_objects.get(shot_id)
        if not objects:
            raise ValueError(f"shot story objects are missing for {shot_id}")

        states: dict[int, bool] = {}
        if frame_start > scene_frame_start:
            states[scene_frame_start] = True
            states[frame_start - 1] = True
        states[frame_start] = False
        states[frame_end] = False
        if frame_end < scene_frame_end:
            states[frame_end + 1] = True

        for obj in objects:
            for frame, hidden in sorted(states.items()):
                obj.hide_render = hidden
                obj.hide_viewport = hidden
                obj.keyframe_insert("hide_render", frame=frame)
                obj.keyframe_insert("hide_viewport", frame=frame)
                applied_keyframes += 2
            obj["trackprompt_visibility_frame_start"] = frame_start
            obj["trackprompt_visibility_frame_end"] = frame_end
            obj["trackprompt_visibility_policy"] = "current-shot-only"
    return applied_keyframes


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
                material=materials["membrane"],
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
        destination = (ax + 6.0, ay + 24.0, az + 5.0)
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
                location=destination,
                major_radius=18.0,
                minor_radius=0.82,
                material=accent,
                act_id=act_id,
                semantic="andromeda-horizon-arc",
                rotation=(math.pi / 2.0, 0.32, 0.0),
            )
        )
        objects.append(
            _sphere(
                "TP_ANDROMEDA_V2_ARRIVAL_ANDROMEDA_CORE",
                collection,
                location=destination,
                radius=3.2,
                material=materials["amber"],
                act_id=act_id,
                semantic="andromeda-destination-luminous-core",
                subdivisions=3,
            )
        )
        for index, (radius, x_offset, z_offset) in enumerate(
            (
                (5.0, -1.1, 0.5),
                (8.0, 0.8, -0.4),
                (11.0, -0.4, 0.7),
                (14.0, 1.2, -0.8),
            )
        ):
            objects.append(
                _torus(
                    f"TP_ANDROMEDA_V2_ARRIVAL_GALAXY_ARM_{index:02d}",
                    collection,
                    location=(
                        destination[0] + x_offset,
                        destination[1] + index * 0.18,
                        destination[2] + z_offset,
                    ),
                    major_radius=radius,
                    minor_radius=0.18 + index * 0.04,
                    material=crystal if index % 2 == 0 else accent,
                    act_id=act_id,
                    semantic="andromeda-destination-spiral-arm",
                    rotation=(
                        math.pi / 2.0,
                        0.24 + index * 0.035,
                        -0.18 + index * 0.13,
                    ),
                )
            )
        for index in range(12):
            angle = index * 2.399963229728653
            radius = 5.0 + index * 1.05
            objects.append(
                _sphere(
                    f"TP_ANDROMEDA_V2_ARRIVAL_STAR_MARKER_{index:02d}",
                    collection,
                    location=(
                        destination[0] + math.cos(angle) * radius,
                        destination[1] - 0.8 + (index % 3) * 0.7,
                        destination[2] + math.sin(angle) * radius * 0.46,
                    ),
                    radius=0.16 + (index % 4) * 0.045,
                    material=materials["amber" if index % 5 == 0 else "crystal"],
                    act_id=act_id,
                    semantic="andromeda-destination-star-depth-marker",
                    subdivisions=1,
                )
            )
        objects.append(
            _cube(
                "TP_ANDROMEDA_V2_ARRIVAL_BEACON_ECHO",
                collection,
                location=(
                    destination[0] + 7.2,
                    destination[1] - 1.0,
                    destination[2] + 8.5,
                ),
                dimensions=(0.18, 0.18, 8.0),
                material=crystal,
                act_id=act_id,
                semantic="arrival-opening-beacon-echo",
                rotation=(0.0, -0.24, 0.0),
            )
        )
    return objects


def _tag_protagonist_component(obj: Any, component_id: str) -> Any:
    obj["trackprompt_protagonist_component_id"] = component_id
    obj["trackprompt_component_story_state"] = "authored"
    obj["trackprompt_look_profile"] = ANDROMEDA_V2_LOOK_PROFILE_ID
    return obj


def _build_protagonist(collection: Any, materials: Mapping[str, Any]) -> Any:
    root = _empty(
        "TP_ANDROMEDA_V2_PROTAGONIST_ROOT",
        collection,
        location=(0.0, 0.0, 0.0),
        act_id="story",
        semantic="protagonist-b-ancient-engine",
    )
    body = _tag_protagonist_component(
        _sphere(
            "TP_ANDROMEDA_V2_PROTAGONIST_BODY",
            collection,
            location=(0.0, 0.0, 0.0),
            radius=1.45,
            material=materials["hero"],
            act_id="story",
            semantic="ancient-engine-dark-structural-shell",
        ),
        "structural-shell",
    )
    body.scale = (1.0, 1.22, 0.90)
    body.parent = root
    core = _tag_protagonist_component(
        _sphere(
            "TP_ANDROMEDA_V2_PROTAGONIST_CORE",
            collection,
            location=(0.0, -0.18, 0.0),
            radius=0.70,
            material=materials["crystal"],
            act_id="story",
            semantic="luminous-internal-core",
        ),
        "internal-core",
    )
    core.scale = (0.78, 0.96, 0.74)
    core.parent = root
    atmosphere = _tag_protagonist_component(
        _sphere(
            "TP_ANDROMEDA_V2_PROTAGONIST_ATMOSPHERE",
            collection,
            location=(0.0, 0.0, 0.0),
            radius=1.58,
            material=materials["atmosphere"],
            act_id="story",
            semantic="single-translucent-atmosphere",
        ),
        "atmosphere",
    )
    atmosphere.scale = (1.02, 1.24, 0.92)
    atmosphere.parent = root
    band = _tag_protagonist_component(
        _torus(
            "TP_ANDROMEDA_V2_PROTAGONIST_ARMOR_BAND",
            collection,
            location=(0.0, 0.24, 0.0),
            major_radius=1.50,
            minor_radius=0.13,
            material=materials["metal"],
            act_id="story",
            semantic="single-major-armor-band",
            rotation=(math.pi / 2.0, 0.0, 0.0),
        ),
        "armor-band",
    )
    band.scale = (1.0, 1.0, 0.80)
    band.parent = root
    aperture = _tag_protagonist_component(
        _cylinder(
            "TP_ANDROMEDA_V2_PROTAGONIST_APERTURE",
            collection,
            location=(0.0, -1.50, 0.0),
            radius=0.55,
            depth=0.32,
            material=materials["amber"],
            act_id="story",
            semantic="integrated-front-aperture",
            rotation=(math.pi / 2.0, 0.0, 0.0),
        ),
        "front-aperture",
    )
    aperture.parent = root
    aperture_collar = _tag_protagonist_component(
        _torus(
            "TP_ANDROMEDA_V2_PROTAGONIST_APERTURE_COLLAR",
            collection,
            location=(0.0, -1.62, 0.0),
            major_radius=0.64,
            minor_radius=0.075,
            material=materials["metal"],
            act_id="story",
            semantic="aperture-structural-collar",
            rotation=(math.pi / 2.0, 0.0, 0.0),
        ),
        "aperture-collar",
    )
    aperture_collar.parent = root
    cue = _tag_protagonist_component(
        _cube(
            "TP_ANDROMEDA_V2_PROTAGONIST_ORIENTATION_CUE",
            collection,
            location=(-0.48, -1.42, 0.74),
            dimensions=(0.30, 0.34, 0.24),
            material=materials["crystal"],
            act_id="story",
            semantic="asymmetric-leading-edge-orientation-cue",
            rotation=(0.15, -0.08, -0.18),
            bevel=0.065,
        ),
        "orientation-cue",
    )
    cue.parent = root
    damaged_plate = _tag_protagonist_component(
        _cube(
            "TP_ANDROMEDA_V2_PROTAGONIST_DAMAGE_PLATE",
            collection,
            location=(0.92, 0.12, 0.34),
            dimensions=(0.30, 1.18, 0.72),
            material=materials["metal"],
            act_id="story",
            semantic="indexed-damage-side-armor-component",
            rotation=(0.0, 0.16, 0.12),
            bevel=0.075,
        ),
        "damaged-armor-plate",
    )
    damaged_plate.parent = root
    damaged_route = _tag_protagonist_component(
        _cube(
            "TP_ANDROMEDA_V2_PROTAGONIST_DAMAGED_ROUTE",
            collection,
            location=(0.79, -0.42, -0.12),
            dimensions=(0.12, 1.32, 0.14),
            material=materials["crystal"],
            act_id="story",
            semantic="damage-side-functional-crystal-route",
            rotation=(0.12, 0.22, -0.18),
            bevel=0.025,
        ),
        "damaged-crystal-route",
    )
    damaged_route.parent = root
    repaired_bridge = _tag_protagonist_component(
        _cube(
            "TP_ANDROMEDA_V2_PROTAGONIST_REPAIRED_BRIDGE",
            collection,
            location=(0.70, -0.46, -0.06),
            dimensions=(0.16, 1.48, 0.18),
            material=materials["transformed"],
            act_id="story",
            semantic="bridging-functional-crystal-route",
            rotation=(-0.10, 0.28, -0.24),
            bevel=0.03,
        ),
        "repaired-crystal-bridge",
    )
    repaired_bridge.parent = root
    for side in (-1.0, 1.0):
        fin = _tag_protagonist_component(
            _cube(
                f"TP_ANDROMEDA_V2_PROTAGONIST_TRANSFORMED_FIN_{'L' if side < 0 else 'R'}",
                collection,
                location=(side * 0.78, 0.38, 0.42 if side < 0 else -0.34),
                dimensions=(0.16, 1.18, 0.48),
                material=materials["transformed"],
                act_id="story",
                semantic="permanent-transformed-directional-fin",
                rotation=(0.0, side * 0.16, side * 0.14),
                bevel=0.055,
            ),
            f"transformed-fin-{'left' if side < 0 else 'right'}",
        )
        fin.parent = root
    for side in (-1.0, 1.0):
        pod = _tag_protagonist_component(
            _cube(
                f"TP_ANDROMEDA_V2_PROTAGONIST_REAR_POD_{'L' if side < 0 else 'R'}",
                collection,
                location=(side * 0.92, 0.76, -0.08),
                dimensions=(0.24, 0.72, 0.40),
                material=materials["metal"],
                act_id="story",
                semantic="limited-rear-propulsion-pod",
                rotation=(0.0, side * 0.12, side * 0.08),
                bevel=0.06,
            ),
            f"rear-pod-{'left' if side < 0 else 'right'}",
        )
        pod.parent = root
    wake = _tag_protagonist_component(
        _cone(
            "TP_ANDROMEDA_V2_PROTAGONIST_REAR_WAKE",
            collection,
            location=(0.0, 1.92, -0.04),
            radius1=0.44,
            radius2=0.12,
            depth=1.35,
            material=materials["atmosphere"],
            act_id="story",
            semantic="restrained-rear-energy-wake",
            rotation=(math.pi / 2.0, 0.0, 0.0),
        ),
        "rear-wake",
    )
    wake.parent = root
    component_ids = sorted(
        str(child["trackprompt_protagonist_component_id"])
        for child in root.children
        if child.get("trackprompt_protagonist_component_id")
    )
    root["trackprompt_major_armor_bands"] = 1
    root["trackprompt_wire_cage"] = False
    root["trackprompt_transparent_atmosphere_layers"] = 1
    root["trackprompt_component_ids_json"] = json.dumps(
        component_ids,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    root["trackprompt_damage_is_component_level"] = True
    root["trackprompt_transformation_persists_through_arrival"] = True
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

        rim_data = bpy.data.lights.new(
            f"TP_ANDROMEDA_V2_LIGHT_{act_id.upper()}_RIM",
            type="AREA",
        )
        rim_data.energy = 980.0 if act_id not in {"rupture", "transformation"} else 1_280.0
        rim_data.shape = "DISK"
        rim_data.size = 5.0
        rim_data.color = (
            (0.06, 0.72, 0.68)
            if act_id in {"signal", "awakening", "gates", "arrival"}
            else (1.0, 0.24, 0.035)
        )
        rim = bpy.data.objects.new(rim_data.name, rim_data)
        collection.objects.link(rim)
        rim.location = (anchor[0] + 8.5, anchor[1] + 3.0, anchor[2] + 7.0)
        _point_camera(rim, (anchor[0], anchor[1], anchor[2] + 1.5))
        _mark(rim, act_id=act_id, semantic="authored-r13.1-rim-light")
        lights.append(rim)
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
        rotation[0] -= 0.10
        scale = (1.04, 1.08, 1.04)
    elif protagonist_state in {"transformed", "arrived"}:
        rotation[0] -= 0.16
        rotation[1] += 0.08
        scale = (1.08, 1.16, 0.98)
    else:
        scale = (1.0, 1.0, 1.0)
    return {
        "frame": frame,
        "location": location,
        "rotationEuler": tuple(rotation),
        "scale": scale,
    }


def _component_story_state(
    frame: int,
    stage: str,
    *,
    scale: tuple[float, float, float],
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict[str, Any]:
    return {
        "frame": frame,
        "stage": stage,
        "scaleMultiplier": scale,
        "locationOffset": location,
        "rotationOffset": rotation,
    }


def build_protagonist_component_story_plan(
    shots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build persistent component damage and transformation states."""

    shots_by_sequence = {
        int(shot["sequence"]): shot
        for shot in shots
        if isinstance(shot, dict) and isinstance(shot.get("sequence"), int)
    }
    required_sequences = {23, 27, 28, 29, 30, 31, 35}
    if not required_sequences.issubset(shots_by_sequence):
        raise ValueError(
            "the protagonist component plan requires Rupture, Transformation, "
            "and Arrival milestone shots"
        )

    def frames(sequence: int) -> tuple[int, int, int]:
        shot = shots_by_sequence[sequence]
        frame_start = int(shot["frameStart"])
        frame_end = int(shot["frameEnd"])
        return frame_start, (frame_start + frame_end) // 2, frame_end

    impact_start, impact_mid, impact_end = frames(23)
    unmake_start, unmake_mid, unmake_end = frames(27)
    route_start, route_mid, route_end = frames(28)
    rebirth_start, rebirth_mid, rebirth_end = frames(29)
    release_start, release_mid, release_end = frames(30)
    arrival_start = int(shots_by_sequence[31]["frameStart"])
    final_frame = int(shots_by_sequence[35]["frameEnd"])
    base_frame = ANDROMEDA_V2_FRAME_START

    components: dict[str, list[dict[str, Any]]] = {
        "structural-shell": [
            _component_story_state(
                base_frame,
                "intact",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_end,
                "impact-scarred",
                scale=(0.98, 0.94, 1.02),
                rotation=(0.05, 0.12, -0.08),
            ),
            _component_story_state(
                release_end,
                "transformed-permanent-shell",
                scale=(1.04, 1.08, 1.02),
                rotation=(-0.04, 0.04, 0.08),
            ),
            _component_story_state(
                final_frame,
                "arrived-transformed-shell",
                scale=(1.04, 1.08, 1.02),
                rotation=(-0.04, 0.04, 0.08),
            ),
        ],
        "internal-core": [
            _component_story_state(
                base_frame,
                "restrained-core",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_end,
                "impact-dimmed-core",
                scale=(0.72, 0.72, 0.72),
            ),
            _component_story_state(
                route_end,
                "repaired-routing-core",
                scale=(0.96, 0.96, 0.96),
            ),
            _component_story_state(
                rebirth_end,
                "reborn-core",
                scale=(1.12, 1.12, 1.12),
            ),
            _component_story_state(
                final_frame,
                "arrived-reborn-core",
                scale=(1.12, 1.12, 1.12),
            ),
        ],
        "atmosphere": [
            _component_story_state(
                base_frame,
                "restrained-atmosphere",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_end,
                "ruptured-atmosphere",
                scale=(0.72, 0.64, 0.78),
                location=(0.08, 0.06, -0.04),
            ),
            _component_story_state(
                rebirth_end,
                "rebuilt-atmosphere",
                scale=(1.08, 1.12, 1.06),
            ),
            _component_story_state(
                release_end,
                "transformed-permanent-atmosphere",
                scale=(1.16, 1.20, 1.12),
            ),
            _component_story_state(
                final_frame,
                "arrived-transformed-atmosphere",
                scale=(1.16, 1.20, 1.12),
            ),
        ],
        "armor-band": [
            _component_story_state(
                base_frame,
                "intact-single-band",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_mid,
                "impact-buckled-band",
                scale=(0.92, 0.84, 1.06),
                rotation=(0.18, 0.05, 0.16),
            ),
            _component_story_state(
                impact_end,
                "damaged-single-band",
                scale=(0.88, 0.80, 1.04),
                rotation=(0.20, 0.08, 0.20),
            ),
            _component_story_state(
                unmake_mid,
                "indexed-for-repair",
                scale=(0.74, 0.68, 0.92),
                rotation=(0.26, 0.12, 0.24),
            ),
            _component_story_state(
                rebirth_end,
                "single-reformed-band",
                scale=(1.02, 1.02, 0.92),
                rotation=(-0.04, 0.02, -0.08),
            ),
            _component_story_state(
                release_end,
                "transformed-single-band",
                scale=(1.04, 1.04, 0.94),
                rotation=(-0.04, 0.02, -0.08),
            ),
            _component_story_state(
                final_frame,
                "arrived-single-reformed-band",
                scale=(1.04, 1.04, 0.94),
                rotation=(-0.04, 0.02, -0.08),
            ),
        ],
        "damaged-armor-plate": [
            _component_story_state(
                base_frame,
                "integrated-armor-plate",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_start,
                "impact-contact",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_mid,
                "armor-torn",
                scale=(0.92, 0.92, 0.92),
                location=(0.44, -0.16, 0.28),
                rotation=(0.58, 0.22, 0.42),
            ),
            _component_story_state(
                impact_end,
                "armor-damaged-persistent",
                scale=(0.74, 0.74, 0.74),
                location=(0.88, 0.22, 0.52),
                rotation=(0.84, 0.42, 0.66),
            ),
            _component_story_state(
                unmake_start,
                "armor-indexed-for-removal",
                scale=(0.74, 0.74, 0.74),
                location=(0.88, 0.22, 0.52),
                rotation=(0.84, 0.42, 0.66),
            ),
            _component_story_state(
                unmake_mid,
                "armor-mechanically-withdrawn",
                scale=(0.40, 0.40, 0.40),
                location=(1.64, 0.78, 1.05),
                rotation=(1.18, 0.72, 1.08),
            ),
            _component_story_state(
                unmake_end,
                "damaged-armor-removed",
                scale=(0.001, 0.001, 0.001),
                location=(2.42, 1.24, 1.46),
                rotation=(1.42, 0.92, 1.34),
            ),
            _component_story_state(
                final_frame,
                "removed-through-arrival",
                scale=(0.001, 0.001, 0.001),
                location=(2.42, 1.24, 1.46),
                rotation=(1.42, 0.92, 1.34),
            ),
        ],
        "damaged-crystal-route": [
            _component_story_state(
                base_frame,
                "functional-route",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_mid,
                "route-fracturing",
                scale=(0.62, 0.52, 0.74),
                rotation=(0.18, 0.10, -0.22),
            ),
            _component_story_state(
                impact_end,
                "interrupted-route",
                scale=(0.18, 0.28, 0.24),
                location=(0.14, 0.12, -0.10),
                rotation=(0.32, 0.18, -0.38),
            ),
            _component_story_state(
                route_start,
                "route-repair-start",
                scale=(0.18, 0.28, 0.24),
                location=(0.14, 0.12, -0.10),
                rotation=(0.32, 0.18, -0.38),
            ),
            _component_story_state(
                route_end,
                "interrupted-route-retired",
                scale=(0.001, 0.001, 0.001),
            ),
            _component_story_state(
                final_frame,
                "retired-through-arrival",
                scale=(0.001, 0.001, 0.001),
            ),
        ],
        "repaired-crystal-bridge": [
            _component_story_state(
                base_frame,
                "repair-bridge-absent",
                scale=(0.001, 0.001, 0.001),
            ),
            _component_story_state(
                route_start,
                "repair-bridge-seeded",
                scale=(0.001, 0.001, 0.001),
            ),
            _component_story_state(
                route_mid,
                "repair-bridge-growing",
                scale=(0.58, 0.58, 0.58),
            ),
            _component_story_state(
                route_end,
                "repair-bridge-complete",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                release_end,
                "transformed-functional-route",
                scale=(1.12, 1.12, 1.12),
            ),
            _component_story_state(
                final_frame,
                "arrival-functional-route",
                scale=(1.12, 1.12, 1.12),
            ),
        ],
        "front-aperture": [
            _component_story_state(
                base_frame,
                "restrained-aperture",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_end,
                "damaged-aperture",
                scale=(0.68, 0.68, 0.68),
                rotation=(0.08, -0.12, 0.10),
            ),
            _component_story_state(
                rebirth_start,
                "aperture-rebirth-start",
                scale=(0.68, 0.68, 0.68),
            ),
            _component_story_state(
                rebirth_mid,
                "aperture-rebirth-pulse",
                scale=(1.26, 1.26, 1.26),
            ),
            _component_story_state(
                rebirth_end,
                "repaired-aperture",
                scale=(1.06, 1.06, 1.06),
            ),
            _component_story_state(
                final_frame,
                "protected-arrival-aperture",
                scale=(1.06, 1.06, 1.06),
            ),
        ],
        "aperture-collar": [
            _component_story_state(
                base_frame,
                "intact-collar",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_end,
                "damaged-collar",
                scale=(0.84, 0.82, 0.90),
                rotation=(0.10, -0.08, 0.12),
            ),
            _component_story_state(
                rebirth_end,
                "reformed-collar",
                scale=(1.08, 1.08, 1.0),
            ),
            _component_story_state(
                final_frame,
                "arrival-reformed-collar",
                scale=(1.08, 1.08, 1.0),
            ),
        ],
        "orientation-cue": [
            _component_story_state(
                base_frame,
                "clear-orientation",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_end,
                "damaged-orientation-cue",
                scale=(0.72, 0.72, 0.72),
                rotation=(0.24, 0.18, 0.38),
            ),
            _component_story_state(
                release_end,
                "transformed-orientation-cue",
                scale=(1.18, 1.18, 1.18),
                rotation=(-0.06, -0.10, -0.16),
            ),
            _component_story_state(
                final_frame,
                "held-arrival-orientation",
                scale=(1.18, 1.18, 1.18),
                rotation=(-0.06, -0.10, -0.16),
            ),
        ],
        "rear-wake": [
            _component_story_state(
                base_frame,
                "restrained-wake",
                scale=(1.0, 1.0, 1.0),
            ),
            _component_story_state(
                impact_end,
                "damaged-wake",
                scale=(0.24, 0.38, 0.24),
                rotation=(0.20, 0.12, -0.18),
            ),
            _component_story_state(
                release_start,
                "release-wake-start",
                scale=(0.38, 0.52, 0.38),
            ),
            _component_story_state(
                release_mid,
                "transformed-wake-surge",
                scale=(1.34, 1.52, 1.34),
            ),
            _component_story_state(
                release_end,
                "transformed-authored-wake",
                scale=(1.12, 1.24, 1.12),
            ),
            _component_story_state(
                arrival_start,
                "arrival-deceleration-wake",
                scale=(0.82, 0.94, 0.82),
            ),
            _component_story_state(
                final_frame,
                "quiet-arrival-wake",
                scale=(0.46, 0.58, 0.46),
            ),
        ],
    }
    transformed_fin_states = [
        _component_story_state(
            base_frame,
            "transformed-fin-absent",
            scale=(0.001, 0.001, 0.001),
        ),
        _component_story_state(
            rebirth_start,
            "transformed-fin-seeded",
            scale=(0.001, 0.001, 0.001),
        ),
        _component_story_state(
            rebirth_end,
            "transformed-fin-forming",
            scale=(0.36, 0.36, 0.36),
        ),
        _component_story_state(
            release_mid,
            "transformed-fin-opening",
            scale=(0.84, 0.84, 0.84),
        ),
        _component_story_state(
            release_end,
            "transformed-fin-permanent",
            scale=(1.0, 1.0, 1.0),
        ),
        _component_story_state(
            arrival_start,
            "arrival-transformed-fin",
            scale=(1.0, 1.0, 1.0),
        ),
        _component_story_state(
            final_frame,
            "arrived-transformed-fin",
            scale=(1.0, 1.0, 1.0),
        ),
    ]
    components["transformed-fin-left"] = [
        dict(state) for state in transformed_fin_states
    ]
    components["transformed-fin-right"] = [
        dict(state) for state in transformed_fin_states
    ]
    plan: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "projectId": ANDROMEDA_V2_PROJECT_ID,
        "damageBeginsFrame": impact_start,
        "damagePersistsUntilFrame": unmake_end,
        "transformationCompletesFrame": release_end,
        "transformationPersistsThroughFrame": final_frame,
        "components": components,
    }
    plan["canonicalSha256"] = _canonical_sha256(plan)
    return plan


def _animate_protagonist_components(
    protagonist: Any,
    component_plan: Mapping[str, Any],
) -> int:
    components = {
        str(child.get("trackprompt_protagonist_component_id")): child
        for child in protagonist.children
        if child.get("trackprompt_protagonist_component_id")
    }
    applied_keyframes = 0
    for component_id, states in component_plan["components"].items():
        component = components.get(str(component_id))
        if component is None:
            raise ValueError(f"protagonist component {component_id} is missing")
        base_location = tuple(component.location)
        base_rotation = tuple(component.rotation_euler)
        base_scale = tuple(component.scale)
        for state in states:
            frame = int(state["frame"])
            location_offset = state["locationOffset"]
            rotation_offset = state["rotationOffset"]
            scale_multiplier = state["scaleMultiplier"]
            component.location = tuple(
                base_location[index] + float(location_offset[index])
                for index in range(3)
            )
            component.rotation_euler = tuple(
                base_rotation[index] + float(rotation_offset[index])
                for index in range(3)
            )
            component.scale = tuple(
                base_scale[index] * float(scale_multiplier[index])
                for index in range(3)
            )
            component.keyframe_insert("location", frame=frame)
            component.keyframe_insert("rotation_euler", frame=frame)
            component.keyframe_insert("scale", frame=frame)
            applied_keyframes += 9
        component["trackprompt_component_story_plan_sha256"] = str(
            component_plan["canonicalSha256"]
        )
        component["trackprompt_final_component_story_stage"] = str(
            states[-1]["stage"]
        )
    protagonist["trackprompt_component_story_plan_sha256"] = str(
        component_plan["canonicalSha256"]
    )
    protagonist["trackprompt_damage_begins_frame"] = int(
        component_plan["damageBeginsFrame"]
    )
    protagonist["trackprompt_transformation_completes_frame"] = int(
        component_plan["transformationCompletesFrame"]
    )
    protagonist["trackprompt_transformation_persists_through_frame"] = int(
        component_plan["transformationPersistsThroughFrame"]
    )
    return applied_keyframes


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
    act_id = str(composition.get("actId", ""))
    narrative_target_bias = (
        (2.5, 8.0, 2.8)
        if act_id == "arrival"
        else (0.0, 0.0, 0.0)
    )
    return {
        "frame": frame,
        "location": (
            anchor[0] + float(camera_offset[0]) * distance_scale + lateral,
            hero_location[1] + float(camera_offset[1]) * distance_scale,
            anchor[2] + float(camera_offset[2]) * distance_scale,
        ),
        "target": (
            hero_location[0]
            + float(target_offset[0])
            + narrative_target_bias[0],
            hero_location[1]
            + float(target_offset[1])
            + narrative_target_bias[1],
            hero_location[2]
            + float(target_offset[2])
            + narrative_target_bias[2],
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
            elif semantic == "connected-reconstruction-arm":
                delay = min(120, index * 12)
                reveal_frame = min(frame_end - 1, frame_mid + 180 + delay // 2)
                for frame, scale, turn in (
                    (frame_start + delay, 0.18, -0.25),
                    (frame_mid + delay // 2, 0.82, 0.12),
                    (reveal_frame, 0.32, 0.30),
                    (frame_end, 0.18, 0.38),
                ):
                    obj.scale = (scale, scale, scale)
                    obj.rotation_euler = (
                        base_rotation[0],
                        base_rotation[1] + turn,
                        base_rotation[2] - turn * 0.5,
                    )
                    obj.keyframe_insert("scale", frame=frame)
                    obj.keyframe_insert("rotation_euler", frame=frame)
            elif semantic == "reconstruction-cradle":
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
    """Normalize motion to LINEAR and boolean visibility to stepped CONSTANT."""

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
                interpolation = (
                    "CONSTANT"
                    if getattr(fcurve, "data_path", "") in {
                        "hide_render",
                        "hide_viewport",
                    }
                    else "LINEAR"
                )
                for keyframe in getattr(fcurve, "keyframe_points", ()):
                    keyframe.interpolation = interpolation
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
    story_architecture_collection = _collection(
        "TP_ANDROMEDA_V2_SHOT_STORY_ARCHITECTURE",
        environment_root,
    )
    protagonist_collection = _collection("TP_ANDROMEDA_V2_PROTAGONIST", root_collection)
    camera_collection = _collection("TP_ANDROMEDA_V2_CAMERAS", root_collection)
    light_collection = _collection("TP_ANDROMEDA_V2_LIGHTS", root_collection)

    materials: dict[str, Any] = {
        "stone": _material(
            "TP_ANDROMEDA_V2_MAT_WEATHERED_STONE",
            (0.045, 0.052, 0.060, 1.0),
            metallic=0.16,
            roughness=0.69,
            noise_scale=3.8,
            bump_strength=0.20,
        ),
        "metal": _material(
            "TP_ANDROMEDA_V2_MAT_ANCIENT_METAL",
            (0.060, 0.075, 0.085, 1.0),
            metallic=0.74,
            roughness=0.42,
            noise_scale=10.0,
            bump_strength=0.10,
        ),
        "recess": _material(
            "TP_ANDROMEDA_V2_MAT_LOCK_RECESS",
            (0.009, 0.014, 0.019, 1.0),
            metallic=0.45,
            roughness=0.60,
            noise_scale=12.0,
            bump_strength=0.05,
        ),
        "dark": _material(
            "TP_ANDROMEDA_V2_MAT_DARK_VOID",
            (0.008, 0.012, 0.016, 1.0),
            metallic=0.05,
            roughness=0.82,
        ),
        "hero": _material(
            "TP_ANDROMEDA_V2_MAT_ANCIENT_ENGINE",
            (0.025, 0.040, 0.055, 1.0),
            metallic=0.66,
            roughness=0.31,
            noise_scale=7.0,
            bump_strength=0.055,
        ),
        "crystal": _material(
            "TP_ANDROMEDA_V2_MAT_CRYSTAL",
            (0.018, 0.120, 0.130, 1.0),
            metallic=0.20,
            roughness=0.18,
            emission_color=(0.050, 0.550, 0.480, 1.0),
            emission_strength=0.46,
            noise_scale=8.0,
            bump_strength=0.035,
        ),
        "membrane": _material(
            "TP_ANDROMEDA_V2_MAT_LOCALIZED_MEMBRANE",
            (0.025, 0.210, 0.230, 1.0),
            metallic=0.06,
            roughness=0.15,
            emission_color=(0.050, 0.520, 0.500, 1.0),
            emission_strength=0.08,
            alpha=0.035,
            transmission=0.70,
            noise_scale=5.5,
            bump_strength=0.018,
        ),
        "amber": _material(
            "TP_ANDROMEDA_V2_MAT_AMBER",
            (0.180, 0.065, 0.010, 1.0),
            metallic=0.24,
            roughness=0.24,
            emission_color=(0.900, 0.280, 0.025, 1.0),
            emission_strength=0.64,
            noise_scale=9.0,
            bump_strength=0.025,
        ),
        "atmosphere": _material(
            "TP_ANDROMEDA_V2_MAT_ATMOSPHERE",
            (0.035, 0.160, 0.200, 1.0),
            metallic=0.05,
            roughness=0.24,
            emission_color=(0.070, 0.420, 0.550, 1.0),
            emission_strength=0.12,
            alpha=0.075,
            transmission=0.26,
            noise_scale=5.0,
            bump_strength=0.02,
        ),
        "transformed": _material(
            "TP_ANDROMEDA_V2_MAT_TRANSFORMED_ROUTE",
            (0.025, 0.180, 0.220, 1.0),
            metallic=0.32,
            roughness=0.20,
            emission_color=(0.080, 0.720, 0.950, 1.0),
            emission_strength=1.10,
            noise_scale=8.5,
            bump_strength=0.035,
        ),
    }
    for act_id, color in _ACT_PALETTES.items():
        materials[act_id] = _material(
            f"TP_ANDROMEDA_V2_MAT_{act_id.upper()}",
            color,
            metallic=0.38,
            roughness=0.34,
            emission_color=color,
            emission_strength=0.42,
            noise_scale=6.0,
            bump_strength=0.055,
        )

    shot_entries = contracts["shots"]["shots"]
    story_action_plan = build_shot_story_action_plan(shot_entries)
    protagonist_component_plan = build_protagonist_component_story_plan(
        shot_entries
    )
    environment_objects: dict[str, list[Any]] = {}
    for act_id in _ACT_ORDER:
        act_collection = _collection(f"TP_ANDROMEDA_V2_ACT_{act_id.upper()}", environment_root)
        environment_objects[act_id] = _build_environment(
            act_id,
            act_collection,
            materials,
        )
    story_objects = _build_shot_story_architecture(
        story_architecture_collection,
        materials,
        story_action_plan,
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
    _animate_story(protagonist, cameras, shot_entries)
    _animate_environment_actions(environment_objects, shot_entries)
    story_action_keyframe_count = _animate_shot_story_actions(
        story_objects,
        story_action_plan,
    )
    story_visibility_keyframe_count = _animate_shot_story_visibility(
        story_objects,
        story_action_plan,
    )
    protagonist_component_keyframe_count = _animate_protagonist_components(
        protagonist,
        protagonist_component_plan,
    )
    visual_cue_state = _animate_bounded_lighting(
        lights,
        shot_entries,
        visual_cues,
        source_cue_sha256 if visual_cues is not None else None,
    )
    animated_objects = [
        protagonist,
        *protagonist.children,
        *cameras.values(),
        *lights,
        *(
            obj
            for objects in environment_objects.values()
            for obj in objects
        ),
        *(
            obj
            for objects in story_objects.values()
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
        background.inputs["Color"].default_value = (0.004, 0.009, 0.017, 1.0)
        background.inputs["Strength"].default_value = 0.12
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.65

    _embed_contracts(scene, contracts, spec)
    for name, payload in (
        ("TP_ANDROMEDA_V2_STORY_ACTION_PLAN_JSON", story_action_plan),
        (
            "TP_ANDROMEDA_V2_PROTAGONIST_COMPONENT_PLAN_JSON",
            protagonist_component_plan,
        ),
    ):
        text = bpy.data.texts.new(name)
        text.write(
            json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    scene["trackprompt_story_action_plan_sha256"] = story_action_plan[
        "canonicalSha256"
    ]
    scene["trackprompt_story_action_count"] = len(
        story_action_plan["shotActions"]
    )
    scene["trackprompt_story_action_keyframe_count"] = (
        story_action_keyframe_count
    )
    scene["trackprompt_story_visibility_keyframe_count"] = (
        story_visibility_keyframe_count
    )
    scene["trackprompt_protagonist_component_plan_sha256"] = (
        protagonist_component_plan["canonicalSha256"]
    )
    scene["trackprompt_protagonist_component_keyframe_count"] = (
        protagonist_component_keyframe_count
    )
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
    builder_source_sha256 = _file_sha256(Path(__file__).resolve())
    scene["trackprompt_builder_source_sha256"] = builder_source_sha256
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
        "builderId": ANDROMEDA_V2_BUILDER_ID,
        "builderSourceSha256": builder_source_sha256,
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
        "shotStoryArchitecture": {
            "shotCount": len(story_objects),
            "objectCount": sum(len(objects) for objects in story_objects.values()),
            "actionPlanSha256": story_action_plan["canonicalSha256"],
            "keyframePointCount": story_action_keyframe_count,
            "visibilityPolicy": "current-shot-only",
            "visibilityKeyframePointCount": story_visibility_keyframe_count,
        },
        "protagonistComponentStory": {
            "componentCount": len(protagonist_component_plan["components"]),
            "planSha256": protagonist_component_plan["canonicalSha256"],
            "keyframePointCount": protagonist_component_keyframe_count,
            "damageBeginsFrame": protagonist_component_plan[
                "damageBeginsFrame"
            ],
            "transformationCompletesFrame": protagonist_component_plan[
                "transformationCompletesFrame"
            ],
            "transformationPersistsThroughFrame": protagonist_component_plan[
                "transformationPersistsThroughFrame"
            ],
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

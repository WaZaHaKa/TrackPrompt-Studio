from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

HORIZONTAL_VARIANT_ID = "horizontal-16x9-1080p"
VERTICAL_VARIANT_ID = "vertical-9x16-1080p"
OUTPUT_VARIANT_IDS = (HORIZONTAL_VARIANT_ID, VERTICAL_VARIANT_ID)

_ACTS = (
    "signal",
    "awakening",
    "departure",
    "gates",
    "rupture",
    "transformation",
    "arrival",
)
_VARIANT_BASES: dict[str, dict[str, Any]] = {
    HORIZONTAL_VARIANT_ID: {
        "width": 1920,
        "height": 1080,
        "compositionMode": "authored",
        "deliverableRole": "primary-master",
        "sensorFit": "HORIZONTAL",
        "cameraName": "TP_ANDROMEDA_V2_CAMERA_HORIZONTAL",
        "safeZone": {"xMin": 0.08, "xMax": 0.92, "yMin": 0.10, "yMax": 0.90},
        "subjectAnchor": (0.40, 0.50),
        "landmarkAnchor": (0.73, 0.48),
        "subjectScale": 0.18,
        "cameraOffset": (-13.0, -18.0, 7.0),
        "targetOffset": (0.0, 0.0, 1.0),
        "lensMm": 42.0,
        "maximumSubjectOcclusion": 0.18,
        "maximumLandmarkOcclusion": 0.30,
    },
    VERTICAL_VARIANT_ID: {
        "width": 1080,
        "height": 1920,
        "compositionMode": "authored",
        "deliverableRole": "optional-social",
        "sensorFit": "VERTICAL",
        "cameraName": "TP_ANDROMEDA_V2_CAMERA_VERTICAL",
        "safeZone": {"xMin": 0.12, "xMax": 0.88, "yMin": 0.08, "yMax": 0.92},
        "subjectAnchor": (0.50, 0.38),
        "landmarkAnchor": (0.50, 0.70),
        "subjectScale": 0.13,
        "cameraOffset": (-8.0, -25.0, 10.0),
        "targetOffset": (0.0, 0.0, 1.8),
        "lensMm": 50.0,
        "maximumSubjectOcclusion": 0.12,
        "maximumLandmarkOcclusion": 0.22,
    },
}
_ACT_ADJUSTMENTS: dict[str, dict[str, dict[str, Any]]] = {
    "signal": {
        HORIZONTAL_VARIANT_ID: {
            "subjectAnchor": (0.34, 0.56),
            "landmarkAnchor": (0.76, 0.38),
            "lensMm": 46.0,
        },
        VERTICAL_VARIANT_ID: {
            "subjectAnchor": (0.50, 0.64),
            "landmarkAnchor": (0.50, 0.24),
            "lensMm": 55.0,
        },
    },
    "awakening": {
        HORIZONTAL_VARIANT_ID: {
            "subjectAnchor": (0.43, 0.50),
            "landmarkAnchor": (0.70, 0.50),
            "lensMm": 52.0,
        },
        VERTICAL_VARIANT_ID: {
            "subjectAnchor": (0.50, 0.43),
            "landmarkAnchor": (0.50, 0.70),
            "lensMm": 58.0,
        },
    },
    "departure": {
        HORIZONTAL_VARIANT_ID: {
            "subjectAnchor": (0.42, 0.52),
            "landmarkAnchor": (0.76, 0.46),
            "lensMm": 36.0,
        },
        VERTICAL_VARIANT_ID: {
            "subjectAnchor": (0.50, 0.57),
            "landmarkAnchor": (0.50, 0.27),
            "lensMm": 44.0,
        },
    },
    "gates": {
        HORIZONTAL_VARIANT_ID: {
            "subjectAnchor": (0.39, 0.54),
            "landmarkAnchor": (0.69, 0.47),
            "lensMm": 30.0,
        },
        VERTICAL_VARIANT_ID: {
            "subjectAnchor": (0.50, 0.62),
            "landmarkAnchor": (0.50, 0.30),
            "lensMm": 38.0,
        },
    },
    "rupture": {
        HORIZONTAL_VARIANT_ID: {
            "subjectAnchor": (0.43, 0.44),
            "landmarkAnchor": (0.72, 0.62),
            "lensMm": 40.0,
        },
        VERTICAL_VARIANT_ID: {
            "subjectAnchor": (0.46, 0.37),
            "landmarkAnchor": (0.54, 0.73),
            "lensMm": 48.0,
        },
    },
    "transformation": {
        HORIZONTAL_VARIANT_ID: {
            "subjectAnchor": (0.50, 0.50),
            "landmarkAnchor": (0.74, 0.50),
            "lensMm": 55.0,
        },
        VERTICAL_VARIANT_ID: {
            "subjectAnchor": (0.50, 0.47),
            "landmarkAnchor": (0.50, 0.75),
            "lensMm": 62.0,
        },
    },
    "arrival": {
        HORIZONTAL_VARIANT_ID: {
            "subjectAnchor": (0.33, 0.58),
            "landmarkAnchor": (0.72, 0.38),
            "lensMm": 38.0,
        },
        VERTICAL_VARIANT_ID: {
            "subjectAnchor": (0.50, 0.70),
            "landmarkAnchor": (0.50, 0.26),
            "lensMm": 46.0,
        },
    },
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


def composition_profile_id(variant_id: str, act_id: str) -> str:
    if variant_id not in OUTPUT_VARIANT_IDS:
        raise ValueError("unknown Andromeda V2 output variant")
    if act_id not in _ACTS:
        raise ValueError("unknown Andromeda V2 story act")
    orientation = "horizontal" if variant_id == HORIZONTAL_VARIANT_ID else "vertical"
    return f"andromeda-v2-{orientation}-{act_id}-v1"


def authored_composition_profile(variant_id: str, act_id: str) -> dict[str, Any]:
    profile = dict(_VARIANT_BASES.get(variant_id, {}))
    if not profile:
        raise ValueError("unknown Andromeda V2 output variant")
    adjustments = _ACT_ADJUSTMENTS.get(act_id)
    if adjustments is None:
        raise ValueError("unknown Andromeda V2 story act")
    profile.update(adjustments[variant_id])
    profile.update(
        {
            "schemaVersion": "1.0.0",
            "profileId": composition_profile_id(variant_id, act_id),
            "outputVariantId": variant_id,
            "actId": act_id,
            "cropPolicy": "native-authored-never-crop",
            "occlusion": {
                "policyId": "andromeda-subject-landmark-protection-v1",
                "maximumSubjectFraction": profile.pop("maximumSubjectOcclusion"),
                "maximumPrimaryLandmarkFraction": profile.pop("maximumLandmarkOcclusion"),
                "protectedRegions": [
                    "front-aperture",
                    "asymmetric-orientation-cues",
                    "primary-story-landmark",
                ],
            },
        }
    )
    profile["canonicalSha256"] = _canonical_sha256(profile)
    return profile


def resolve_shot_compositions(shot: Mapping[str, object]) -> dict[str, dict[str, Any]]:
    act_id = shot.get("actId")
    bindings = shot.get("compositionProfileIds")
    overrides = shot.get("compositionOverrides")
    landmarks = shot.get("requiredLandmarks")
    if (
        not isinstance(act_id, str)
        or not isinstance(bindings, Mapping)
        or not isinstance(overrides, Mapping)
    ):
        raise ValueError("shot composition bindings are missing")
    if (
        not isinstance(landmarks, list)
        or not landmarks
        or any(not isinstance(item, str) or not item for item in landmarks)
    ):
        raise ValueError("shot required landmarks are missing")
    if overrides.get("horizontal") == overrides.get("vertical"):
        raise ValueError("shot formats require independent composition overrides")

    result: dict[str, dict[str, Any]] = {}
    for variant_id, binding_key in (
        (HORIZONTAL_VARIANT_ID, "horizontal"),
        (VERTICAL_VARIANT_ID, "vertical"),
    ):
        expected_id = composition_profile_id(variant_id, act_id)
        if bindings.get(binding_key) != expected_id:
            raise ValueError(f"shot does not bind the expected {binding_key} composition")
        override = overrides.get(binding_key)
        if not isinstance(override, Mapping):
            raise ValueError(f"shot does not define the {binding_key} composition override")
        if override.get("compositionProfileId") != expected_id:
            raise ValueError(f"shot {binding_key} override does not bind the expected profile")
        if (
            override.get("independentlyAuthored") is not True
            or override.get("derivedByCrop") is not False
            or override.get("sharedEventTiming") is not True
        ):
            raise ValueError(f"shot {binding_key} override violates authored-format policy")
        resolved = authored_composition_profile(variant_id, act_id)
        resolved.update(
            {
                "cameraRigId": override.get("cameraRigId"),
                "lensMm": override.get("lensMm"),
                "framingIntent": override.get("framingIntent"),
                "subjectOccupancyFraction": override.get("subjectOccupancyFraction"),
                "foregroundPlacement": override.get("foregroundPlacement"),
                "safeZone": override.get("safeZone"),
                "maximumForegroundOcclusionFraction": override.get(
                    "maximumForegroundOcclusionFraction"
                ),
                "minimumLandmarkVisibilityFraction": override.get(
                    "minimumLandmarkVisibilityFraction"
                ),
                "titleSafeSpace": override.get("titleSafeSpace"),
                "screenDirection": override.get("screenDirection"),
                "independentlyAuthored": True,
                "derivedByCrop": False,
                "sharedEventTiming": True,
            }
        )
        resolved["shotId"] = shot.get("id")
        resolved["requiredLandmarks"] = list(landmarks)
        resolved["shotOverride"] = dict(override)
        resolved["canonicalSha256"] = _canonical_sha256(resolved)
        result[variant_id] = resolved
    return result


def all_authored_composition_profiles() -> list[dict[str, Any]]:
    return [
        authored_composition_profile(variant_id, act_id)
        for variant_id in OUTPUT_VARIANT_IDS
        for act_id in _ACTS
    ]

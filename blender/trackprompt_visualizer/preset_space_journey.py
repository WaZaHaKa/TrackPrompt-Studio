from __future__ import annotations

import math
import random
from collections.abc import Mapping
from typing import Any

from .curve_importer import iter_action_fcurves
from .geometry import add_property_driver, create_collections, move_to_collection
from .materials import add_socket_driver, create_material, drive_emission
from .preset_registry import SPACE_JOURNEY_COLLECTIONS, SPACE_JOURNEY_DEFAULTS

Color = tuple[float, float, float, float]

PALETTES: dict[str, dict[str, Color]] = {
    "andromeda": {
        "background": (0.002, 0.004, 0.018, 1.0),
        "core": (0.12, 0.52, 1.0, 1.0),
        "highlight": (0.72, 0.94, 1.0, 1.0),
        "accent": (0.12, 0.92, 1.0, 1.0),
        "secondary": (0.44, 0.16, 0.96, 1.0),
        "fog": (0.08, 0.025, 0.24, 1.0),
    },
    "deep-space": {
        "background": (0.001, 0.003, 0.012, 1.0),
        "core": (0.08, 0.28, 0.72, 1.0),
        "highlight": (0.64, 0.78, 1.0, 1.0),
        "accent": (0.08, 0.52, 0.92, 1.0),
        "secondary": (0.20, 0.12, 0.48, 1.0),
        "fog": (0.02, 0.04, 0.14, 1.0),
    },
    "cyan-violet": {
        "background": (0.002, 0.008, 0.016, 1.0),
        "core": (0.06, 0.74, 0.92, 1.0),
        "highlight": (0.76, 1.0, 1.0, 1.0),
        "accent": (0.14, 0.96, 0.92, 1.0),
        "secondary": (0.52, 0.18, 1.0, 1.0),
        "fog": (0.06, 0.03, 0.20, 1.0),
    },
    "violet-magenta": {
        "background": (0.008, 0.002, 0.018, 1.0),
        "core": (0.48, 0.14, 0.92, 1.0),
        "highlight": (0.98, 0.66, 1.0, 1.0),
        "accent": (0.88, 0.16, 0.74, 1.0),
        "secondary": (0.26, 0.20, 0.92, 1.0),
        "fog": (0.20, 0.02, 0.18, 1.0),
    },
    "monochrome-blue": {
        "background": (0.001, 0.005, 0.018, 1.0),
        "core": (0.12, 0.38, 0.92, 1.0),
        "highlight": (0.74, 0.88, 1.0, 1.0),
        "accent": (0.20, 0.58, 1.0, 1.0),
        "secondary": (0.06, 0.20, 0.56, 1.0),
        "fog": (0.02, 0.07, 0.20, 1.0),
    },
    "dark-amber": {
        "background": (0.010, 0.004, 0.001, 1.0),
        "core": (0.92, 0.32, 0.06, 1.0),
        "highlight": (1.0, 0.82, 0.42, 1.0),
        "accent": (1.0, 0.50, 0.10, 1.0),
        "secondary": (0.52, 0.12, 0.04, 1.0),
        "fog": (0.18, 0.055, 0.008, 1.0),
    },
}


def _number(value: object, fallback: float = 0.0) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else fallback


def _bounded(value: object, fallback: float = 0.0) -> float:
    return min(1.0, max(0.0, _number(value, fallback)))


def _scaled_color(color: Color, scale: float) -> Color:
    return (color[0] * scale, color[1] * scale, color[2] * scale, color[3])


def _parameters(parameters: Mapping[str, object]) -> dict[str, float | str]:
    return {
        name: parameters.get(name, default)  # type: ignore[dict-item]
        for name, default in SPACE_JOURNEY_DEFAULTS.items()
    }


def deterministic_space_seed_plan(seed: int, parameters: Mapping[str, object]) -> dict[str, Any]:
    values = _parameters(parameters)
    rng = random.Random(seed)
    shard_density = float(values["shardDensity"])
    # Four combined depth layers add parallax and density without creating one
    # Blender object per star.  The upper bound is intentionally modest enough
    # for Eevee preview renders while keeping the distant field from feeling
    # empty in closer destination shots.
    star_counts = [52 + round(shard_density * amount) for amount in (46, 60, 76, 92)]
    return {
        "rootSeed": seed,
        "heroSeed": rng.randrange(0, 2_147_483_647),
        "ringSeed": rng.randrange(0, 2_147_483_647),
        "starSeeds": [rng.randrange(0, 2_147_483_647) for _index in range(4)],
        "debrisSeed": rng.randrange(0, 2_147_483_647),
        "nebulaSeed": rng.randrange(0, 2_147_483_647),
        "ringCount": 6,
        "companionRingCount": 9,
        "starLayerCounts": star_counts,
        "shardCount": 20 + round(shard_density * 72),
        "travelStreakCount": 14 + round(shard_density * 46),
        "heroSurfaceDetailCount": 48 + round(shard_density * 52),
        "orbitalDustCount": 88 + round(shard_density * 104),
    }


def build_space_journey_direction_plan(
    cues: Mapping[str, Any],
    parameters: Mapping[str, object],
) -> list[dict[str, Any]]:
    values = _parameters(parameters)
    timeline = cues["timeline"]
    frame_start = int(timeline["frameStart"])
    frame_end = int(timeline["frameEnd"])
    fps = max(1, int(_number(timeline.get("fps"), 30.0)))
    span = max(1, frame_end - frame_start)
    sections = [section for section in cues.get("sections", []) if isinstance(section, Mapping)]

    def at_fraction(fraction: float) -> int:
        return min(frame_end, max(frame_start, frame_start + round(span * fraction)))

    def midpoint(section: Mapping[str, Any]) -> int:
        start = int(_number(section.get("startFrame"), frame_start))
        end = int(_number(section.get("endFrame"), start))
        return min(frame_end, max(frame_start, (start + end) // 2))

    def section_at(frame: int) -> tuple[str, float]:
        for index, section in enumerate(sections):
            start = int(_number(section.get("startFrame"), frame_start))
            end = int(_number(section.get("endFrame"), frame_end))
            if start <= frame <= end:
                return (
                    str(section.get("id", f"section-{index + 1}")),
                    _bounded(section.get("energy"), 0.5),
                )
        if sections:
            nearest = min(
                enumerate(sections),
                key=lambda item: abs(int(_number(item[1].get("startFrame"), frame_start)) - frame),
            )
            return (
                str(nearest[1].get("id", f"section-{nearest[0] + 1}")),
                _bounded(nearest[1].get("energy"), 0.5),
            )
        return ("track", 0.5)

    # Cue-section keys remain authoritative, but six deterministic cinematic
    # anchors give long sections an internal dramatic grammar.  The supplied
    # Andromeda cue sheet has one section spanning more than half the track;
    # relying only on section starts made opening, development, and groove
    # nearly identical shots.  These anchors are bounded, deterministic, and
    # purely visual: no analysis or preview-selection contract is changed.
    source_states: dict[int, tuple[str, float, str]] = {}
    for index, section in enumerate(sections):
        frame = min(frame_end, max(frame_start, int(_number(section.get("startFrame"), frame_start))))
        source_states[frame] = (
            str(section.get("id", f"section-{index + 1}")),
            _bounded(section.get("energy"), 0.5),
            "section",
        )
    if not source_states:
        source_states[frame_start] = ("track-start", 0.5, "section")

    transitions = [event for event in cues.get("transitions", []) if isinstance(event, Mapping)]
    for event in transitions:
        frame = min(frame_end, max(frame_start, int(_number(event.get("frame"), frame_start))))
        section_id, energy = section_at(frame)
        delta = _number(event.get("energyDelta"))
        role = "rebuild" if delta >= 0.08 else "breath" if delta <= -0.12 else "section"
        source_states[frame] = (section_id, energy, role)

    first_half = [section for section in sections if midpoint(section) <= at_fraction(0.50)]
    groove_section = (
        max(first_half, key=lambda item: _number(item.get("energy"), 0.5))
        if first_half
        else None
    )
    groove_frame = midpoint(groove_section) if groove_section is not None else at_fraction(0.34)
    breakdown_candidates = [
        section
        for index, section in enumerate(sections)
        if index not in {0, len(sections) - 1}
        and at_fraction(0.35) <= midpoint(section) <= at_fraction(0.78)
        and midpoint(section) > groove_frame
    ]
    breakdown_section = (
        min(breakdown_candidates, key=lambda item: _number(item.get("energy"), 0.5))
        if breakdown_candidates
        else None
    )
    breakdown_frame = midpoint(breakdown_section) if breakdown_section is not None else at_fraction(0.56)
    peak_candidates = [
        section
        for section in sections
        if breakdown_frame < midpoint(section) <= at_fraction(0.90)
    ]
    peak_section = (
        max(peak_candidates, key=lambda item: _number(item.get("energy"), 0.5))
        if peak_candidates
        else None
    )
    peak_frame = midpoint(peak_section) if peak_section is not None else at_fraction(0.78)

    cinematic_anchors = (
        (frame_start, "opening"),
        (at_fraction(0.16), "early-development"),
        (groove_frame, "main-groove"),
        (breakdown_frame, "breakdown"),
        (peak_frame, "peak"),
        (frame_end, "outro"),
    )
    for frame, role in cinematic_anchors:
        section_id, energy = section_at(frame)
        source_states[frame] = (section_id, energy, role)

    rising = [
        event
        for event in transitions
        if at_fraction(0.15) <= int(_number(event.get("frame"), frame_start)) <= at_fraction(0.85)
        and _number(event.get("energyDelta")) > 0.0
    ]
    if rising:
        representative_rise = max(rising, key=lambda item: _number(item.get("energyDelta")))
        rise_frame = min(
            frame_end,
            max(frame_start, int(_number(representative_rise.get("frame"), frame_start))),
        )
        section_id, energy = section_at(rise_frame)
        source_states[rise_frame] = (section_id, energy, "rebuild")
        release_frame = min(frame_end, rise_frame + fps * 5)
        if release_frame < peak_frame:
            threshold_frame = min(
                release_frame - 1,
                rise_frame + max(1, round(fps * 2.5)),
            )
            if rise_frame < threshold_frame < release_frame:
                section_id, energy = section_at(threshold_frame)
                source_states[threshold_frame] = (
                    section_id,
                    energy,
                    "threshold-hold",
                )
            section_id, energy = section_at(release_frame)
            source_states[release_frame] = (section_id, energy, "rebuild-release")

    late_sections = [
        section
        for section in sections
        if peak_frame < midpoint(section) <= at_fraction(0.90)
    ]
    if late_sections:
        late_section = max(late_sections, key=lambda item: _number(item.get("energy"), 0.5))
        late_frame = midpoint(late_section)
        section_id, energy = section_at(late_frame)
        source_states[late_frame] = (section_id, energy, "late-crest")

    result: list[dict[str, Any]] = []
    base_distance = float(values["cameraDistance"])
    orbit_speed = float(values["cameraOrbitSpeed"])
    fog_depth = float(values["fogDepth"])
    profiles: dict[str, dict[str, float]] = {
        "opening": {
            "distanceFactor": 1.80,
            "cameraHeight": 2.90,
            "cameraLens": 60.0,
            "targetOffsetX": 1.15,
            "targetOffsetZ": -0.20,
            "cameraShiftX": 0.025,
            "cameraShiftY": 0.008,
            "destinationReveal": 0.30,
            "heroAwakening": 0.12,
            "orbitReveal": 0.56,
            "orbitTiltX": -0.62,
            "orbitTiltY": 0.28,
            "orbitHeading": -0.32,
            "orbitOffsetX": 2.60,
            "orbitOffsetZ": 1.00,
            "traceTiltX": 0.35,
            "traceTiltY": -0.18,
            "packetScale": 0.42,
            "fogScale": 1.18,
            "nebulaOffsetX": 4.50,
            "nebulaOffsetZ": 1.60,
            "nebulaRoll": -0.18,
            "travelScale": 0.66,
            "travelOffsetX": -1.10,
            "travelOffsetY": 2.40,
            "travelOffsetZ": 0.62,
            "lightingRoll": -0.28,
            "lightingScale": 1.22,
        },
        "early-development": {
            "distanceFactor": 1.69,
            "cameraHeight": 3.25,
            "cameraLens": 58.0,
            "targetOffsetX": 0.65,
            "targetOffsetZ": 0.08,
            "cameraShiftX": 0.014,
            "cameraShiftY": 0.004,
            "destinationReveal": 0.50,
            "heroAwakening": 0.31,
            "orbitReveal": 0.68,
            "orbitTiltX": -0.48,
            "orbitTiltY": 0.22,
            "orbitHeading": -0.16,
            "orbitOffsetX": 1.55,
            "orbitOffsetZ": 0.58,
            "traceTiltX": 0.24,
            "traceTiltY": -0.13,
            "packetScale": 0.62,
            "fogScale": 1.12,
            "nebulaOffsetX": 2.80,
            "nebulaOffsetZ": 1.10,
            "nebulaRoll": -0.10,
            "travelScale": 0.82,
            "travelOffsetX": -0.62,
            "travelOffsetY": 1.40,
            "travelOffsetZ": 0.38,
            "lightingRoll": -0.18,
            "lightingScale": 1.14,
        },
        "main-groove": {
            "distanceFactor": 1.56,
            "cameraHeight": 3.55,
            "cameraLens": 54.0,
            "targetOffsetX": -0.45,
            "targetOffsetZ": 0.12,
            "cameraShiftX": -0.012,
            "cameraShiftY": 0.002,
            "destinationReveal": 0.71,
            "heroAwakening": 0.62,
            "orbitReveal": 0.90,
            "orbitTiltX": -0.34,
            "orbitTiltY": 0.14,
            "orbitHeading": 0.08,
            "orbitOffsetX": 0.42,
            "orbitOffsetZ": -0.10,
            "traceTiltX": -0.16,
            "traceTiltY": 0.10,
            "packetScale": 0.88,
            "fogScale": 1.02,
            "nebulaOffsetX": 0.90,
            "nebulaOffsetZ": 0.50,
            "nebulaRoll": 0.02,
            "travelScale": 1.00,
            "travelOffsetX": 0.20,
            "travelOffsetY": 0.40,
            "travelOffsetZ": 0.05,
            "lightingRoll": -0.04,
            "lightingScale": 1.02,
        },
        "breath": {
            "distanceFactor": 1.68,
            "cameraHeight": 4.10,
            "cameraLens": 59.0,
            "targetOffsetX": -0.78,
            "targetOffsetZ": 0.24,
            "cameraShiftX": -0.020,
            "cameraShiftY": -0.004,
            "destinationReveal": 0.62,
            "heroAwakening": 0.28,
            "orbitReveal": 0.62,
            "orbitTiltX": -0.56,
            "orbitTiltY": 0.26,
            "orbitHeading": -0.35,
            "orbitOffsetX": -1.60,
            "orbitOffsetZ": 0.50,
            "traceTiltX": 0.25,
            "traceTiltY": -0.16,
            "packetScale": 0.48,
            "fogScale": 1.24,
            "nebulaOffsetX": -3.20,
            "nebulaOffsetZ": 1.75,
            "nebulaRoll": -0.22,
            "travelScale": 0.72,
            "travelOffsetX": 1.10,
            "travelOffsetY": 2.60,
            "travelOffsetZ": -0.34,
            "lightingRoll": -0.34,
            "lightingScale": 1.22,
        },
        "breakdown": {
            "distanceFactor": 1.77,
            "cameraHeight": 4.28,
            "cameraLens": 61.0,
            "targetOffsetX": -0.95,
            "targetOffsetZ": 0.28,
            "cameraShiftX": -0.025,
            "cameraShiftY": -0.006,
            "destinationReveal": 0.58,
            "heroAwakening": 0.20,
            "orbitReveal": 0.58,
            "orbitTiltX": -0.62,
            "orbitTiltY": 0.30,
            "orbitHeading": -0.46,
            "orbitOffsetX": -2.20,
            "orbitOffsetZ": 0.68,
            "traceTiltX": 0.30,
            "traceTiltY": -0.18,
            "packetScale": 0.38,
            "fogScale": 1.30,
            "nebulaOffsetX": -3.80,
            "nebulaOffsetZ": 2.00,
            "nebulaRoll": -0.28,
            "travelScale": 0.66,
            "travelOffsetX": 1.45,
            "travelOffsetY": 3.40,
            "travelOffsetZ": -0.42,
            "lightingRoll": -0.40,
            "lightingScale": 1.28,
        },
        "rebuild": {
            "distanceFactor": 1.65,
            "cameraHeight": 3.82,
            "cameraLens": 57.0,
            "targetOffsetX": -0.58,
            "targetOffsetZ": 0.18,
            "cameraShiftX": -0.014,
            "cameraShiftY": -0.002,
            "destinationReveal": 0.76,
            "heroAwakening": 0.56,
            "orbitReveal": 0.76,
            "orbitTiltX": -0.28,
            "orbitTiltY": 0.12,
            "orbitHeading": -0.18,
            "orbitOffsetX": -1.00,
            "orbitOffsetZ": 0.32,
            "traceTiltX": 0.13,
            "traceTiltY": -0.08,
            "packetScale": 0.72,
            "fogScale": 1.12,
            "nebulaOffsetX": -1.00,
            "nebulaOffsetZ": 0.62,
            "nebulaRoll": -0.10,
            "travelScale": 1.06,
            "travelOffsetX": 0.75,
            "travelOffsetY": 0.80,
            "travelOffsetZ": -0.22,
            "lightingRoll": -0.16,
            "lightingScale": 1.12,
            "foregroundOffsetX": 0.62,
            "foregroundOffsetY": 0.00,
            "foregroundOffsetZ": -0.08,
        },
        "threshold-hold": {
            "distanceFactor": 1.64,
            "cameraHeight": 3.80,
            "cameraLens": 57.0,
            "targetOffsetX": -0.55,
            "targetOffsetZ": 0.17,
            "cameraShiftX": -0.013,
            "cameraShiftY": -0.002,
            "destinationReveal": 0.77,
            "heroAwakening": 0.58,
            "orbitReveal": 0.77,
            "orbitTiltX": -0.27,
            "orbitTiltY": 0.11,
            "orbitHeading": -0.16,
            "orbitOffsetX": -0.94,
            "orbitOffsetZ": 0.30,
            "traceTiltX": 0.12,
            "traceTiltY": -0.07,
            "packetScale": 0.74,
            "fogScale": 1.14,
            "nebulaOffsetX": -0.90,
            "nebulaOffsetZ": 0.60,
            "nebulaRoll": -0.09,
            "travelScale": 1.04,
            "travelOffsetX": 0.70,
            "travelOffsetY": 0.70,
            "travelOffsetZ": -0.20,
            "lightingRoll": -0.15,
            "lightingScale": 1.16,
            "foregroundOffsetX": 0.58,
            "foregroundOffsetY": -0.10,
            "foregroundOffsetZ": -0.08,
        },
        "rebuild-release": {
            "distanceFactor": 1.47,
            "cameraHeight": 3.45,
            "cameraLens": 53.0,
            "targetOffsetX": -0.20,
            "targetOffsetZ": 0.10,
            "cameraShiftX": -0.005,
            "cameraShiftY": 0.002,
            "destinationReveal": 0.97,
            "heroAwakening": 0.96,
            "orbitReveal": 1.00,
            "orbitTiltX": -0.08,
            "orbitTiltY": 0.03,
            "orbitHeading": 0.06,
            "orbitOffsetX": -0.05,
            "orbitOffsetZ": 0.02,
            "traceTiltX": 0.02,
            "traceTiltY": -0.01,
            "packetScale": 1.18,
            "fogScale": 1.10,
            "nebulaOffsetX": 0.00,
            "nebulaOffsetZ": 0.16,
            "nebulaRoll": 0.04,
            "travelScale": 1.34,
            "travelOffsetX": -0.20,
            "travelOffsetY": -2.60,
            "travelOffsetZ": 0.08,
            "lightingRoll": 0.02,
            "lightingScale": 0.82,
            "foregroundOffsetX": -0.56,
            "foregroundOffsetY": -0.82,
            "foregroundOffsetZ": 0.12,
        },
        "peak": {
            "distanceFactor": 1.40,
            "cameraHeight": 3.25,
            "cameraLens": 48.5,
            "targetOffsetX": 0.18,
            "targetOffsetZ": 0.05,
            "cameraShiftX": 0.006,
            "cameraShiftY": 0.002,
            "destinationReveal": 1.04,
            "heroAwakening": 1.00,
            "orbitReveal": 1.04,
            "orbitTiltX": -0.04,
            "orbitTiltY": -0.02,
            "orbitHeading": 0.20,
            "orbitOffsetX": 0.00,
            "orbitOffsetZ": 0.00,
            "traceTiltX": 0.00,
            "traceTiltY": 0.00,
            "packetScale": 1.28,
            "fogScale": 1.16,
            "nebulaOffsetX": 0.25,
            "nebulaOffsetZ": -0.05,
            "nebulaRoll": 0.08,
            "travelScale": 1.28,
            "travelOffsetX": 0.00,
            "travelOffsetY": -1.40,
            "travelOffsetZ": 0.00,
            "lightingRoll": 0.12,
            "lightingScale": 0.86,
        },
        "late-crest": {
            "distanceFactor": 1.48,
            "cameraHeight": 3.38,
            "cameraLens": 51.5,
            "targetOffsetX": 0.30,
            "targetOffsetZ": -0.04,
            "cameraShiftX": -0.010,
            "cameraShiftY": 0.002,
            "destinationReveal": 0.90,
            "heroAwakening": 0.82,
            "orbitReveal": 0.94,
            "orbitTiltX": -0.16,
            "orbitTiltY": -0.08,
            "orbitHeading": 0.36,
            "orbitOffsetX": 0.36,
            "orbitOffsetZ": -0.12,
            "traceTiltX": -0.08,
            "traceTiltY": 0.05,
            "packetScale": 1.00,
            "fogScale": 1.04,
            "nebulaOffsetX": 1.20,
            "nebulaOffsetZ": 0.46,
            "nebulaRoll": 0.14,
            "travelScale": 1.08,
            "travelOffsetX": -0.28,
            "travelOffsetY": -0.60,
            "travelOffsetZ": 0.10,
            "lightingRoll": 0.24,
            "lightingScale": 0.94,
        },
        "outro": {
            "distanceFactor": 1.81,
            "cameraHeight": 3.72,
            "cameraLens": 61.0,
            "targetOffsetX": -1.20,
            "targetOffsetZ": -0.22,
            "cameraShiftX": -0.030,
            "cameraShiftY": 0.006,
            "destinationReveal": 0.43,
            "heroAwakening": 0.24,
            "orbitReveal": 0.55,
            "orbitTiltX": -0.56,
            "orbitTiltY": -0.24,
            "orbitHeading": 0.62,
            "orbitOffsetX": -2.60,
            "orbitOffsetZ": -0.72,
            "traceTiltX": -0.24,
            "traceTiltY": 0.16,
            "packetScale": 0.45,
            "fogScale": 1.22,
            "nebulaOffsetX": -5.00,
            "nebulaOffsetZ": 1.20,
            "nebulaRoll": 0.30,
            "travelScale": 0.58,
            "travelOffsetX": 1.20,
            "travelOffsetY": 2.80,
            "travelOffsetZ": 0.44,
            "lightingRoll": 0.38,
            "lightingScale": 1.20,
        },
    }

    for frame in sorted(source_states):
        section_id, energy, narrative_role = source_states[frame]
        progress = (frame - frame_start) / span
        reveal = min(0.98, 0.42 + math.sin(progress * math.pi) * 0.44 + energy * 0.07)
        approach = 1.70 - math.sin(progress * math.pi) * 0.23 - energy * 0.055
        if progress > 0.82:
            approach += (progress - 0.82) * 0.42
        profile = profiles.get(narrative_role)
        if profile is None:
            profile = {
                "distanceFactor": approach + (0.5 - energy) * 0.026,
                "cameraHeight": 3.45 + math.sin(progress * math.pi) * 0.94 + (0.5 - energy) * 0.28,
                "cameraLens": 57.0 - reveal * 8.0 + (0.5 - energy) * 1.4,
                "targetOffsetX": math.sin(progress * math.pi * 1.35) * 0.60,
                "targetOffsetZ": math.sin(progress * math.tau - 0.55) * 0.24,
                "cameraShiftX": math.sin(progress * math.pi * 1.70 - 0.40) * 0.026,
                "cameraShiftY": math.cos(progress * math.pi * 1.25 + 0.30) * 0.012,
                "destinationReveal": reveal,
                "heroAwakening": min(0.88, 0.24 + math.sin(progress * math.pi) * 0.48 + energy * 0.10),
                "orbitReveal": 0.62 + reveal * 0.34,
                "orbitTiltX": -0.30 + math.sin(progress * math.pi) * 0.13,
                "orbitTiltY": math.cos(progress * math.pi * 1.30) * 0.10,
                "orbitHeading": (progress - 0.5) * 0.42,
                "orbitOffsetX": math.cos(progress * math.pi * 1.35) * 0.45,
                "orbitOffsetZ": math.sin(progress * math.tau) * 0.14,
                "traceTiltX": math.cos(progress * math.pi * 1.6) * 0.10,
                "traceTiltY": math.sin(progress * math.pi * 1.2) * 0.07,
                "packetScale": 0.72 + energy * 0.24,
                "fogScale": 0.88 + fog_depth * 0.36 + (0.5 - energy) * 0.10,
                "nebulaOffsetX": math.cos(progress * math.pi * 1.25) * 1.20,
                "nebulaOffsetZ": 0.55 + math.sin(progress * math.pi) * 0.35,
                "nebulaRoll": (progress - 0.5) * 0.22,
                "travelScale": 0.82 + progress * 0.25 + energy * 0.10,
                "travelOffsetX": -math.cos(progress * math.pi * 1.25) * 0.42,
                "travelOffsetY": math.cos(progress * math.pi) * 1.20,
                "travelOffsetZ": -math.sin(progress * math.tau) * 0.16,
                "lightingRoll": (progress - 0.5) * 0.28,
                "lightingScale": 1.08 - energy * 0.12,
            }
        camera_orbit = (progress + math.sin(progress * math.pi) * 0.08) * orbit_speed * math.tau
        result.append(
            {
                "frame": frame,
                "sectionId": section_id,
                "narrativeRole": narrative_role,
                "progress": round(progress, 8),
                "energy": energy,
                "cameraDistance": base_distance * profile["distanceFactor"],
                "cameraHeight": profile["cameraHeight"],
                "cameraOrbitRadians": camera_orbit,
                "cameraLens": profile["cameraLens"],
                "targetOffsetX": profile["targetOffsetX"],
                "targetOffsetZ": profile["targetOffsetZ"],
                "cameraShiftX": profile["cameraShiftX"],
                "cameraShiftY": profile["cameraShiftY"],
                "destinationReveal": profile["destinationReveal"],
                "heroAwakening": profile["heroAwakening"],
                "ringAlignment": profile["orbitHeading"],
                "orbitReveal": profile["orbitReveal"],
                "orbitTiltX": profile["orbitTiltX"],
                "orbitTiltY": profile["orbitTiltY"],
                "orbitHeading": profile["orbitHeading"],
                "orbitOffsetX": profile["orbitOffsetX"],
                "orbitOffsetZ": profile["orbitOffsetZ"],
                "traceTiltX": profile["traceTiltX"],
                "traceTiltY": profile["traceTiltY"],
                "packetScale": profile["packetScale"],
                "fogScale": profile["fogScale"],
                "nebulaOffsetX": profile["nebulaOffsetX"],
                "nebulaOffsetZ": profile["nebulaOffsetZ"],
                "nebulaRoll": profile["nebulaRoll"],
                "travelScale": profile["travelScale"],
                "travelOffsetX": profile["travelOffsetX"],
                "travelOffsetY": profile["travelOffsetY"],
                "travelOffsetZ": profile["travelOffsetZ"],
                "lightingRoll": profile["lightingRoll"],
                "lightingScale": profile["lightingScale"],
                "foregroundOffsetX": profile.get("foregroundOffsetX", 0.0),
                "foregroundOffsetY": profile.get("foregroundOffsetY", 0.0),
                "foregroundOffsetZ": profile.get("foregroundOffsetZ", 0.0),
            }
        )
    return result


def _empty(name: str, collection: Any) -> Any:
    import bpy  # type: ignore[import-not-found]

    obj = bpy.data.objects.new(name, None)
    collection.objects.link(obj)
    return obj


def _smooth_active() -> None:
    import bpy  # type: ignore[import-not-found]

    try:
        bpy.ops.object.shade_smooth()
    except RuntimeError:
        pass


def _set_action_easing(owner: Any) -> None:
    animation = getattr(owner, "animation_data", None)
    action = getattr(animation, "action", None) if animation is not None else None
    if action is None:
        return
    for fcurve in iter_action_fcurves(action):
        for point in fcurve.keyframe_points:
            point.interpolation = "BEZIER"
            if hasattr(point, "handle_left_type"):
                point.handle_left_type = "AUTO_CLAMPED"
                point.handle_right_type = "AUTO_CLAMPED"


def _create_arc(
    name: str,
    collection: Any,
    material: Any,
    *,
    radius: float,
    thickness: float,
    start: float,
    span: float,
    tilt: tuple[float, float, float],
    depth: float,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    curve = bpy.data.curves.new(f"{name}_CURVE", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 2
    curve.bevel_depth = thickness
    curve.bevel_resolution = 3
    curve.resolution_u = 2
    spline = curve.splines.new("POLY")
    point_count = 72
    spline.points.add(point_count - 1)
    for index in range(point_count):
        angle = start + span * index / (point_count - 1)
        spline.points[index].co = (radius * math.cos(angle), radius * math.sin(angle), 0.0, 1.0)
    obj = bpy.data.objects.new(name, curve)
    collection.objects.link(obj)
    obj.rotation_euler = tilt
    obj.location.y = depth
    curve.materials.append(material)
    return obj


def _create_segmented_arc(
    name: str,
    collection: Any,
    material: Any,
    *,
    radius: float,
    thickness: float,
    start: float,
    span: float,
    tilt: tuple[float, float, float],
    depth: float,
    segment_count: int,
    duty_cycle: float,
    phase: float = 0.0,
) -> Any:
    """Create one lightweight curve object containing several polished arc dashes."""
    import bpy  # type: ignore[import-not-found]

    curve = bpy.data.curves.new(f"{name}_CURVE", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 2
    curve.bevel_depth = thickness
    curve.bevel_resolution = 3
    points_per_segment = 7
    segment_span = span / max(1, segment_count)
    visible_span = segment_span * min(0.92, max(0.32, duty_cycle))
    for segment_index in range(segment_count):
        segment_start = start + phase + segment_index * segment_span
        spline = curve.splines.new("POLY")
        spline.points.add(points_per_segment - 1)
        for point_index in range(points_per_segment):
            angle = segment_start + visible_span * point_index / (points_per_segment - 1)
            spline.points[point_index].co = (
                radius * math.cos(angle),
                radius * math.sin(angle),
                0.0,
                1.0,
            )
    obj = bpy.data.objects.new(name, curve)
    collection.objects.link(obj)
    obj.rotation_euler = tilt
    obj.location.y = depth
    curve.materials.append(material)
    return obj


def _create_patterned_arc(
    name: str,
    collection: Any,
    material: Any,
    *,
    radius: float,
    thickness: float,
    start: float,
    span: float,
    tilt: tuple[float, float, float],
    depth: float,
    pattern: tuple[tuple[float, float], ...],
) -> Any:
    """Create an irregular broken rail with deliberate negative-space rhythm."""
    import bpy  # type: ignore[import-not-found]

    curve = bpy.data.curves.new(f"{name}_CURVE", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 2
    curve.bevel_depth = thickness
    curve.bevel_resolution = 3
    for normalized_start, normalized_span in pattern:
        visible_span = span * normalized_span
        point_count = max(8, round(visible_span * radius * 4.0))
        spline = curve.splines.new("POLY")
        spline.points.add(point_count - 1)
        segment_start = start + span * normalized_start
        for point_index in range(point_count):
            angle = segment_start + visible_span * point_index / (point_count - 1)
            spline.points[point_index].co = (
                radius * math.cos(angle),
                radius * math.sin(angle),
                0.0,
                1.0,
            )
    obj = bpy.data.objects.new(name, curve)
    collection.objects.link(obj)
    obj.rotation_euler = tilt
    obj.location.y = depth
    curve.materials.append(material)
    return obj


def _material_input(node: Any, *names: str) -> Any | None:
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    return None


def _set_principled_finish(material: Any, *, coat: float, coat_roughness: float) -> Any:
    principled = material.node_tree.nodes.get(material.get("tp_principled_node", "Principled BSDF"))
    if principled is None:
        return None
    coat_input = _material_input(principled, "Coat Weight", "Clearcoat")
    coat_roughness_input = _material_input(principled, "Coat Roughness", "Clearcoat Roughness")
    specular_input = _material_input(principled, "Specular IOR Level", "Specular")
    if coat_input is not None:
        coat_input.default_value = coat
    if coat_roughness_input is not None:
        coat_roughness_input.default_value = coat_roughness
    if specular_input is not None:
        specular_input.default_value = 0.48
    return principled


def _create_core_surface_material(
    name: str,
    palette: Mapping[str, Color],
    bus: Any,
    glow: float,
) -> Any:
    """Build the faceted, layered outer destination surface."""
    material = create_material(
        name,
        (
            palette["background"][0] * 2.4 + palette["core"][0] * 0.055,
            palette["background"][1] * 2.4 + palette["core"][1] * 0.055,
            palette["background"][2] * 2.4 + palette["core"][2] * 0.055,
            1.0,
        ),
        metallic=0.76,
        roughness=0.29,
        emission_color=palette["accent"],
        emission_strength=0.10 + glow * 0.055,
    )
    principled = _set_principled_finish(material, coat=0.46, coat_roughness=0.11)
    if principled is None:
        return material
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    coordinates = nodes.new("ShaderNodeTexCoord")
    macro_noise = nodes.new("ShaderNodeTexNoise")
    macro_noise.name = "TP_CORE_CONTINENTAL_NOISE"
    macro_noise.inputs["Scale"].default_value = 2.8
    macro_noise.inputs["Detail"].default_value = 6.0
    macro_noise.inputs["Roughness"].default_value = 0.61
    macro_noise.inputs["Distortion"].default_value = 0.14
    micro_noise = nodes.new("ShaderNodeTexNoise")
    micro_noise.name = "TP_CORE_MICRO_NOISE"
    micro_noise.inputs["Scale"].default_value = 27.0
    micro_noise.inputs["Detail"].default_value = 4.0
    micro_noise.inputs["Roughness"].default_value = 0.54
    surface_ramp = nodes.new("ShaderNodeValToRGB")
    surface_ramp.name = "TP_CORE_SURFACE_GRADIENT"
    surface_ramp.color_ramp.elements[0].position = 0.26
    surface_ramp.color_ramp.elements[0].color = (
        palette["background"][0] * 1.6,
        palette["background"][1] * 1.8,
        palette["background"][2] * 2.1,
        1.0,
    )
    middle = surface_ramp.color_ramp.elements.new(0.55)
    middle.color = (
        palette["core"][0] * 0.12,
        palette["core"][1] * 0.15,
        palette["core"][2] * 0.18,
        1.0,
    )
    surface_ramp.color_ramp.elements[1].position = 0.82
    surface_ramp.color_ramp.elements[1].color = (
        palette["secondary"][0] * 0.22 + palette["core"][0] * 0.12,
        palette["secondary"][1] * 0.22 + palette["core"][1] * 0.12,
        palette["secondary"][2] * 0.22 + palette["core"][2] * 0.12,
        1.0,
    )
    roughness_ramp = nodes.new("ShaderNodeValToRGB")
    roughness_ramp.color_ramp.elements[0].color = (0.10, 0.10, 0.10, 1.0)
    roughness_ramp.color_ramp.elements[1].color = (0.40, 0.40, 0.40, 1.0)
    layer_weight = nodes.new("ShaderNodeLayerWeight")
    rim_ramp = nodes.new("ShaderNodeValToRGB")
    rim_ramp.name = "TP_CORE_FRESNEL_RIM"
    rim_ramp.color_ramp.elements[0].position = 0.12
    rim_ramp.color_ramp.elements[0].color = palette["highlight"]
    rim_ramp.color_ramp.elements[1].position = 0.48
    rim_ramp.color_ramp.elements[1].color = (
        palette["background"][0] * 2.0,
        palette["background"][1] * 3.0,
        palette["background"][2] * 4.0,
        1.0,
    )
    fissure = nodes.new("ShaderNodeTexVoronoi")
    fissure.name = "TP_CORE_CELLULAR_FISSURES"
    fissure.feature = "DISTANCE_TO_EDGE"
    fissure.distance = "EUCLIDEAN"
    fissure.inputs["Scale"].default_value = 5.8
    fissure_ramp = nodes.new("ShaderNodeValToRGB")
    fissure_ramp.name = "TP_CORE_FISSURE_EMISSION"
    fissure_ramp.color_ramp.elements[0].position = 0.018
    fissure_ramp.color_ramp.elements[0].color = palette["highlight"]
    fissure_ramp.color_ramp.elements[1].position = 0.082
    fissure_ramp.color_ramp.elements[1].color = (0.0, 0.002, 0.008, 1.0)
    aperture_ramp = nodes.new("ShaderNodeValToRGB")
    aperture_ramp.name = "TP_CORE_FISSURE_APERTURES"
    aperture_ramp.color_ramp.elements[0].position = 0.016
    aperture_ramp.color_ramp.elements[0].color = (0.16, 0.16, 0.16, 1.0)
    aperture_ramp.color_ramp.elements[1].position = 0.078
    aperture_ramp.color_ramp.elements[1].color = (0.62, 0.62, 0.62, 1.0)
    emission_layers = nodes.new("ShaderNodeMixRGB")
    emission_layers.name = "TP_CORE_RIM_PLUS_FISSURES"
    emission_layers.blend_type = "ADD"
    emission_layers.inputs[0].default_value = 1.0
    emission_layers.use_clamp = True
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.27
    bump.inputs["Distance"].default_value = 0.052
    links.new(coordinates.outputs["Generated"], macro_noise.inputs["Vector"])
    links.new(coordinates.outputs["Generated"], micro_noise.inputs["Vector"])
    links.new(coordinates.outputs["Generated"], fissure.inputs["Vector"])
    links.new(macro_noise.outputs["Fac"], surface_ramp.inputs["Fac"])
    links.new(surface_ramp.outputs["Color"], principled.inputs["Base Color"])
    links.new(micro_noise.outputs["Fac"], roughness_ramp.inputs["Fac"])
    roughness = _material_input(principled, "Roughness")
    if roughness is not None:
        links.new(roughness_ramp.outputs["Color"], roughness)
    links.new(micro_noise.outputs["Fac"], bump.inputs["Height"])
    normal = _material_input(principled, "Normal")
    if normal is not None:
        links.new(bump.outputs["Normal"], normal)
    links.new(layer_weight.outputs["Facing"], rim_ramp.inputs["Fac"])
    links.new(fissure.outputs["Distance"], fissure_ramp.inputs["Fac"])
    links.new(fissure.outputs["Distance"], aperture_ramp.inputs["Fac"])
    links.new(fissure_ramp.outputs["Color"], emission_layers.inputs[1])
    links.new(rim_ramp.outputs["Color"], emission_layers.inputs[2])
    emission = _material_input(principled, "Emission Color", "Emission")
    if emission is not None:
        links.new(emission_layers.outputs["Color"], emission)
    alpha = _material_input(principled, "Alpha")
    if alpha is not None:
        links.new(aperture_ramp.outputs["Color"], alpha)
    drive_emission(
        material,
        bus,
        "master_energy",
        f"{0.010 + glow * 0.008:.6f} + v * {0.075 + glow * 0.014:.6f}",
    )
    return material


def _create_energy_core_material(
    name: str,
    palette: Mapping[str, Color],
    bus: Any,
    glow: float,
    bass_response: float,
) -> Any:
    material = create_material(
        name,
        palette["secondary"],
        metallic=0.24,
        roughness=0.18,
        emission_color=palette["accent"],
        emission_strength=0.32 + glow * 0.14,
    )
    principled = _set_principled_finish(material, coat=0.30, coat_roughness=0.10)
    if principled is None:
        return material
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    coordinates = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.name = "TP_CORE_PLASMA_NOISE"
    noise.inputs["Scale"].default_value = 2.7
    noise.inputs["Detail"].default_value = 8.0
    noise.inputs["Roughness"].default_value = 0.72
    noise.inputs["Distortion"].default_value = 0.42
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.name = "TP_CORE_PLASMA_GRADIENT"
    ramp.color_ramp.elements[0].position = 0.20
    ramp.color_ramp.elements[0].color = (
        palette["background"][0] * 2.0,
        palette["background"][1] * 2.0,
        palette["background"][2] * 2.5,
        1.0,
    )
    middle = ramp.color_ramp.elements.new(0.48)
    middle.color = (
        palette["secondary"][0] * 0.34,
        palette["secondary"][1] * 0.34,
        palette["secondary"][2] * 0.34,
        1.0,
    )
    ramp.color_ramp.elements[1].position = 0.76
    ramp.color_ramp.elements[1].color = (
        palette["core"][0] * 0.45,
        palette["core"][1] * 0.45,
        palette["core"][2] * 0.45,
        1.0,
    )
    energy_mask = nodes.new("ShaderNodeValToRGB")
    energy_mask.name = "TP_CORE_LOCAL_ENERGY_MASK"
    energy_mask.color_ramp.elements[0].position = 0.48
    energy_mask.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    energy_mask.color_ramp.elements[1].position = 0.69
    energy_mask.color_ramp.elements[1].color = palette["highlight"]
    emission_mix = nodes.new("ShaderNodeMixRGB")
    emission_mix.name = "TP_CORE_LOCAL_ENERGY_COLOR"
    emission_mix.blend_type = "MULTIPLY"
    emission_mix.inputs[0].default_value = 1.0
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.18
    bump.inputs["Distance"].default_value = 0.035
    links.new(coordinates.outputs["Generated"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(noise.outputs["Fac"], energy_mask.inputs["Fac"])
    links.new(ramp.outputs["Color"], principled.inputs["Base Color"])
    emission = _material_input(principled, "Emission Color", "Emission")
    if emission is not None:
        links.new(ramp.outputs["Color"], emission_mix.inputs[1])
        links.new(energy_mask.outputs["Color"], emission_mix.inputs[2])
        links.new(emission_mix.outputs["Color"], emission)
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    normal = _material_input(principled, "Normal")
    if normal is not None:
        links.new(bump.outputs["Normal"], normal)
    drive_emission(
        material,
        bus,
        "bass_energy",
        f"{0.050 + glow * 0.020:.6f} + v * {0.20 * bass_response:.6f}",
    )
    return material


def _create_atmosphere_material(name: str, palette: Mapping[str, Color], bus: Any, glow: float) -> Any:
    import bpy  # type: ignore[import-not-found]

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = (*palette["accent"][:3], 0.12)
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = palette["accent"]
    emission.inputs["Strength"].default_value = 0.24 + glow * 0.13
    layer_weight = nodes.new("ShaderNodeLayerWeight")
    rim = nodes.new("ShaderNodeValToRGB")
    rim.name = "TP_ATMOSPHERE_FRESNEL"
    rim.color_ramp.elements[0].position = 0.08
    rim.color_ramp.elements[0].color = (1.0, 1.0, 1.0, 1.0)
    rim.color_ramp.elements[1].position = 0.58
    rim.color_ramp.elements[1].color = (0.0, 0.0, 0.0, 1.0)
    mix = nodes.new("ShaderNodeMixShader")
    links.new(layer_weight.outputs["Facing"], rim.inputs["Fac"])
    links.new(rim.outputs["Color"], mix.inputs[0])
    links.new(transparent.outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], output.inputs["Surface"])
    add_socket_driver(
        emission.inputs["Strength"],
        bus,
        "master_energy",
        f"{0.08 + glow * 0.035:.6f} + v * {0.26 + glow * 0.070:.6f}",
    )
    if hasattr(material, "surface_render_method"):
        try:
            material.surface_render_method = "DITHERED"
        except (TypeError, ValueError):
            pass
    elif hasattr(material, "blend_method"):
        material.blend_method = "BLEND"
    if hasattr(material, "use_transparency_overlap"):
        material.use_transparency_overlap = False
    return material


def _add_fresnel_microfinish(
    material: Any,
    *,
    edge_color: Color,
    body_color: Color,
    texture_scale: float,
) -> None:
    """Layer fine roughness and a camera-facing color rolloff onto a material."""
    principled = _set_principled_finish(material, coat=0.36, coat_roughness=0.12)
    if principled is None:
        return
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    coordinates = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = texture_scale
    noise.inputs["Detail"].default_value = 3.0
    noise.inputs["Roughness"].default_value = 0.52
    roughness_ramp = nodes.new("ShaderNodeValToRGB")
    roughness_ramp.color_ramp.elements[0].color = (0.10, 0.10, 0.10, 1.0)
    roughness_ramp.color_ramp.elements[1].color = (0.34, 0.34, 0.34, 1.0)
    layer_weight = nodes.new("ShaderNodeLayerWeight")
    rim = nodes.new("ShaderNodeValToRGB")
    rim.color_ramp.elements[0].position = 0.18
    rim.color_ramp.elements[0].color = edge_color
    rim.color_ramp.elements[1].position = 0.80
    rim.color_ramp.elements[1].color = body_color
    links.new(coordinates.outputs["Generated"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], roughness_ramp.inputs["Fac"])
    roughness = _material_input(principled, "Roughness")
    if roughness is not None:
        links.new(roughness_ramp.outputs["Color"], roughness)
    links.new(layer_weight.outputs["Facing"], rim.inputs["Fac"])
    emission = _material_input(principled, "Emission Color", "Emission")
    if emission is not None:
        links.new(rim.outputs["Color"], emission)


def _add_radial_panel(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
    normal: tuple[float, float, float],
    radius: float,
    width: float,
    height: float,
) -> None:
    """Append a tiny raised diamond panel oriented to a spherical normal."""
    nx, ny, nz = normal
    if abs(nz) < 0.88:
        ux, uy, uz = -ny, nx, 0.0
    else:
        ux, uy, uz = 0.0, -nz, ny
    u_length = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
    ux, uy, uz = ux / u_length, uy / u_length, uz / u_length
    vx = ny * uz - nz * uy
    vy = nz * ux - nx * uz
    vz = nx * uy - ny * ux
    cx, cy, cz = nx * radius, ny * radius, nz * radius
    offset = len(vertices)
    vertices.extend(
        (
            (cx + ux * width, cy + uy * width, cz + uz * width),
            (cx + vx * width * 0.64, cy + vy * width * 0.64, cz + vz * width * 0.64),
            (cx - ux * width, cy - uy * width, cz - uz * width),
            (cx - vx * width * 0.64, cy - vy * width * 0.64, cz - vz * width * 0.64),
            (cx + nx * height, cy + ny * height, cz + nz * height),
        )
    )
    faces.extend(
        (
            (offset, offset + 1, offset + 4),
            (offset + 1, offset + 2, offset + 4),
            (offset + 2, offset + 3, offset + 4),
            (offset + 3, offset, offset + 4),
        )
    )


def _tetrahedron(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
    center: tuple[float, float, float],
    size: float,
) -> None:
    offset = len(vertices)
    x, y, z = center
    vertices.extend(
        (
            (x + size, y, z - size * 0.45),
            (x - size, y - size * 0.55, z - size * 0.45),
            (x, y + size * 0.55, z - size * 0.45),
            (x, y, z + size),
        )
    )
    faces.extend(
        (
            (offset, offset + 1, offset + 2),
            (offset, offset + 3, offset + 1),
            (offset + 1, offset + 3, offset + 2),
            (offset + 2, offset + 3, offset),
        )
    )


def _billboard_lozenge(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, ...]],
    center: tuple[float, float, float],
    half_width: float,
    half_height: float,
    angle: float = 0.0,
) -> None:
    """Append one camera-facing pinprick or sliver without triangle silhouettes."""
    x, y, z = center
    cosine = math.cos(angle)
    sine = math.sin(angle)
    ux, uz = cosine * half_width, sine * half_width
    vx, vz = -sine * half_height, cosine * half_height
    offset = len(vertices)
    vertices.extend(
        (
            (x - ux, y, z - uz),
            (x + vx, y, z + vz),
            (x + ux, y, z + uz),
            (x - vx, y, z - vz),
        )
    )
    faces.append((offset, offset + 1, offset + 2, offset + 3))


def _orbit_packet(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, ...]],
    *,
    radius: float,
    angle: float,
    depth: float,
    length: float,
    width: float,
) -> None:
    """Append a tangential light packet that can travel as one combined mesh."""
    x = radius * math.cos(angle)
    z = radius * math.sin(angle)
    tangent_x, tangent_z = -math.sin(angle), math.cos(angle)
    radial_x, radial_z = math.cos(angle), math.sin(angle)
    offset = len(vertices)
    vertices.extend(
        (
            (x - tangent_x * length - radial_x * width, depth, z - tangent_z * length - radial_z * width),
            (x + tangent_x * length - radial_x * width, depth, z + tangent_z * length - radial_z * width),
            (x + tangent_x * length + radial_x * width, depth, z + tangent_z * length + radial_z * width),
            (x - tangent_x * length + radial_x * width, depth, z - tangent_z * length + radial_z * width),
        )
    )
    faces.append((offset, offset + 1, offset + 2, offset + 3))


def _combined_mesh(name: str, collection: Any, vertices: list[tuple[float, float, float]], faces: list[tuple[int, ...]], material: Any) -> Any:
    import bpy  # type: ignore[import-not-found]

    mesh = bpy.data.meshes.new(f"{name}_MESH")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    mesh.materials.append(material)
    return obj


def _nebula_material(
    name: str,
    color: Color,
    bus: Any,
    fog_depth: float,
    seed: int,
    strength_scale: float = 1.0,
) -> Any:
    import bpy  # type: ignore[import-not-found]

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = (*color[:3], 0.16)
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = (0.07 + fog_depth * 0.20) * strength_scale
    texture_coordinate = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.name = "TP_NEBULA_LAYER_OFFSET"
    mapping.inputs["Location"].default_value = (
        ((seed % 17) - 8) * 0.19,
        (((seed // 17) % 19) - 9) * 0.17,
        ((seed % 11) - 5) * 0.11,
    )
    noise = nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "3D"
    noise.inputs["Scale"].default_value = 0.58 + (seed % 7) * 0.045
    noise.inputs["Detail"].default_value = 3.2
    noise.inputs["Roughness"].default_value = 0.57
    fine_noise = nodes.new("ShaderNodeTexNoise")
    fine_noise.noise_dimensions = "3D"
    fine_noise.inputs["Scale"].default_value = 2.6 + (seed % 5) * 0.19
    fine_noise.inputs["Detail"].default_value = 2.0
    fine_noise.inputs["Roughness"].default_value = 0.48
    cloud_mix = nodes.new("ShaderNodeMixRGB")
    cloud_mix.name = "TP_NEBULA_MACRO_MICRO_MIX"
    cloud_mix.blend_type = "MULTIPLY"
    cloud_mix.inputs[0].default_value = 0.74
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.30 + fog_depth * 0.06
    ramp.color_ramp.elements[1].position = 0.65 + fog_depth * 0.06
    radial_distance = nodes.new("ShaderNodeVectorMath")
    radial_distance.operation = "DISTANCE"
    radial_distance.inputs[1].default_value = (0.5, 0.5, 0.0)
    vignette = nodes.new("ShaderNodeValToRGB")
    vignette.name = "TP_NEBULA_SOFT_VIGNETTE"
    vignette.color_ramp.elements[0].position = 0.12
    vignette.color_ramp.elements[0].color = (1.0, 1.0, 1.0, 1.0)
    vignette.color_ramp.elements[1].position = 0.72
    vignette.color_ramp.elements[1].color = (0.0, 0.0, 0.0, 1.0)
    vignetted_cloud = nodes.new("ShaderNodeMixRGB")
    vignetted_cloud.name = "TP_NEBULA_VIGNETTED_CLOUD"
    vignetted_cloud.blend_type = "MULTIPLY"
    vignetted_cloud.inputs[0].default_value = 1.0
    mix = nodes.new("ShaderNodeMixShader")
    links.new(texture_coordinate.outputs["Generated"], mapping.inputs["Vector"])
    links.new(texture_coordinate.outputs["Generated"], radial_distance.inputs[0])
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    links.new(mapping.outputs["Vector"], fine_noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], cloud_mix.inputs[1])
    links.new(fine_noise.outputs["Fac"], cloud_mix.inputs[2])
    links.new(cloud_mix.outputs["Color"], ramp.inputs["Fac"])
    links.new(radial_distance.outputs["Value"], vignette.inputs["Fac"])
    links.new(ramp.outputs["Color"], vignetted_cloud.inputs[1])
    links.new(vignette.outputs["Color"], vignetted_cloud.inputs[2])
    links.new(vignetted_cloud.outputs["Color"], mix.inputs[0])
    links.new(transparent.outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], output.inputs["Surface"])
    fcurve = emission.inputs["Strength"].driver_add("default_value")
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    variable = driver.variables.new()
    variable.name = "v"
    variable.type = "SINGLE_PROP"
    variable.targets[0].id = bus
    variable.targets[0].data_path = '["brightness"]'
    base = (0.09 + fog_depth * 0.20) * strength_scale
    response = (0.17 + fog_depth * 0.17) * strength_scale
    driver.expression = f"{base:.6f} + v * {response:.6f}"
    if hasattr(material, "surface_render_method"):
        try:
            material.surface_render_method = "DITHERED"
        except (TypeError, ValueError):
            pass
    elif hasattr(material, "blend_method"):
        material.blend_method = "BLEND"
    return material


def _configure_world(bus: Any, palette: Mapping[str, Color]) -> Any:
    import bpy  # type: ignore[import-not-found]

    world = bpy.data.worlds.new("TP_SPACE_WORLD_MATERIAL")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = palette["background"]
        background.inputs["Strength"].default_value = 0.008
        fcurve = background.inputs["Strength"].driver_add("default_value")
        driver = fcurve.driver
        driver.type = "SCRIPTED"
        variable = driver.variables.new()
        variable.name = "v"
        variable.type = "SINGLE_PROP"
        variable.targets[0].id = bus
        variable.targets[0].data_path = '["master_energy"]'
        driver.expression = "0.006 + v * 0.016"
    return world


def _configure_glow(glow_strength: float, warnings: list[str]) -> None:
    import bpy  # type: ignore[import-not-found]

    scene = bpy.context.scene
    try:
        scene.use_nodes = True
        tree = getattr(scene, "node_tree", None)
        modern_group = tree is None and hasattr(scene, "compositing_node_group")
        if modern_group:
            tree = bpy.data.node_groups.new("TP_SPACE_COMPOSITOR", "CompositorNodeTree")
            tree.interface.new_socket(
                name="Image",
                in_out="OUTPUT",
                socket_type="NodeSocketColor",
            )
            scene.compositing_node_group = tree
        if tree is None:
            raise RuntimeError("compositor node tree unavailable")
        nodes = tree.nodes
        links = tree.links
        nodes.clear()
        render_layers = nodes.new("CompositorNodeRLayers")
        glare = nodes.new("CompositorNodeGlare")
        glare.name = "TP_CONTROLLED_GLOW"
        threshold = max(0.60, 1.35 - glow_strength * 0.18)
        if modern_group:
            # Blender 5.2 exposes compositor settings as node inputs instead
            # of RNA properties.  Strength is intentionally bounded so the
            # glow supports material detail rather than washing it out.
            glare.inputs["Type"].default_value = "Fog Glow"
            glare.inputs["Quality"].default_value = "High"
            glare.inputs["Threshold"].default_value = threshold
            glare.inputs["Strength"].default_value = min(
                0.52,
                0.22 + glow_strength * 0.11,
            )
            glare.inputs["Saturation"].default_value = 0.92
            glare.inputs["Size"].default_value = min(
                0.82,
                0.56 + glow_strength * 0.08,
            )
            glare.inputs["Iterations"].default_value = 3
        else:
            glare.glare_type = "FOG_GLOW"
            glare.quality = "HIGH"
            glare.threshold = threshold
            glare.size = 6
            glare.mix = min(-0.30, -0.78 + glow_strength * 0.16)
        links.new(render_layers.outputs["Image"], glare.inputs["Image"])
        if modern_group:
            group_output = nodes.new("NodeGroupOutput")
            links.new(glare.outputs["Image"], group_output.inputs["Image"])
        else:
            composite = nodes.new("CompositorNodeComposite")
            links.new(glare.outputs["Image"], composite.inputs["Image"])
    except (AttributeError, RuntimeError, TypeError, ValueError):
        warnings.append("controlled_compositor_glow_unavailable")


def _create_hero(collections: Mapping[str, Any], bus: Any, palette: Mapping[str, Color], values: Mapping[str, float | str], seed_plan: Mapping[str, Any]) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    glow = float(values["glowStrength"])
    bass_response = float(values["bassResponse"])
    hero_rng = random.Random(int(seed_plan["heroSeed"]))
    destination_macro = _empty("TP_DESTINATION_MACRO", collections["TP_DESTINATION"])
    core_micro = _empty("TP_DESTINATION_MICRO", collections["TP_DESTINATION"])
    core_micro.parent = destination_macro
    shell_material = _create_core_surface_material(
        "TP_SPACE_MAT_CORE_SHELL", palette, bus, glow
    )
    energy_material = _create_energy_core_material(
        "TP_SPACE_MAT_CORE_ENERGY",
        palette,
        bus,
        glow,
        bass_response,
    )
    atmosphere_material = _create_atmosphere_material(
        "TP_SPACE_MAT_CORE_ATMOSPHERE", palette, bus, glow
    )
    wire_material = create_material(
        "TP_SPACE_MAT_CORE_WIRE",
        palette["secondary"],
        metallic=0.48,
        roughness=0.15,
        emission_color=palette["accent"],
        emission_strength=0.16 + glow * 0.055,
    )
    _set_principled_finish(wire_material, coat=0.38, coat_roughness=0.12)
    drive_emission(wire_material, bus, "mid_band", f"{0.055 + glow * 0.024:.6f} + v * 0.26")
    mantle_material = create_material(
        "TP_SPACE_MAT_CORE_MANTLE",
        palette["core"],
        metallic=0.28,
        roughness=0.15,
        emission_color=palette["highlight"],
        emission_strength=0.15 + glow * 0.065,
    )
    _set_principled_finish(mantle_material, coat=0.30, coat_roughness=0.10)
    drive_emission(mantle_material, bus, "low_band", f"{0.06 + glow * 0.026:.6f} + v * {0.30 * bass_response:.6f}")
    detail_material = create_material(
        "TP_SPACE_MAT_CORE_SURFACE_DETAIL",
        palette["highlight"],
        metallic=0.74,
        roughness=0.18,
        emission_color=palette["accent"],
        emission_strength=0.10 + glow * 0.045,
    )
    _set_principled_finish(detail_material, coat=0.42, coat_roughness=0.10)
    drive_emission(detail_material, bus, "transient_activity", f"{0.045 + glow * 0.020:.6f} + v * 0.30")
    filament_material = create_material(
        "TP_SPACE_MAT_CORE_FILAMENTS",
        palette["accent"],
        metallic=0.24,
        roughness=0.12,
        emission_color=palette["highlight"],
        emission_strength=0.20 + glow * 0.075,
    )
    _set_principled_finish(filament_material, coat=0.30, coat_roughness=0.08)
    drive_emission(filament_material, bus, "high_band", f"{0.10 + glow * 0.035:.6f} + v * 0.52")
    aperture_material = bpy.data.materials.new("TP_SPACE_MAT_CORE_APERTURE")
    aperture_material.use_nodes = True
    aperture_material.diffuse_color = _scaled_color(palette["fog"], 0.22)
    aperture_nodes = aperture_material.node_tree.nodes
    aperture_links = aperture_material.node_tree.links
    aperture_nodes.clear()
    aperture_output = aperture_nodes.new("ShaderNodeOutputMaterial")
    aperture_emission = aperture_nodes.new("ShaderNodeEmission")
    aperture_emission.inputs["Strength"].default_value = 1.05 + glow * 0.05
    aperture_coordinates = aperture_nodes.new("ShaderNodeTexCoord")
    aperture_plane = aperture_nodes.new("ShaderNodeVectorMath")
    aperture_plane.operation = "MULTIPLY"
    aperture_plane.inputs[1].default_value = (1.0, 0.0, 1.0)
    aperture_distance = aperture_nodes.new("ShaderNodeVectorMath")
    aperture_distance.operation = "DISTANCE"
    aperture_distance.inputs[1].default_value = (0.5, 0.0, 0.5)
    aperture_radial = aperture_nodes.new("ShaderNodeValToRGB")
    aperture_radial.name = "TP_PORTAL_RADIAL_DEPTH"
    aperture_radial.color_ramp.elements[0].position = 0.08
    aperture_radial.color_ramp.elements[0].color = _scaled_color(palette["fog"], 0.075)
    aperture_radial.color_ramp.elements[1].position = 0.58
    aperture_radial.color_ramp.elements[1].color = _scaled_color(palette["secondary"], 0.22)
    aperture_noise = aperture_nodes.new("ShaderNodeTexNoise")
    aperture_noise.noise_dimensions = "3D"
    aperture_noise.inputs["Scale"].default_value = 4.2
    aperture_noise.inputs["Detail"].default_value = 3.0
    aperture_noise.inputs["Roughness"].default_value = 0.58
    aperture_noise_color = aperture_nodes.new("ShaderNodeValToRGB")
    aperture_noise_color.name = "TP_PORTAL_INNER_CLOUDS"
    aperture_noise_color.color_ramp.elements[0].position = 0.28
    aperture_noise_color.color_ramp.elements[0].color = _scaled_color(palette["background"], 0.50)
    aperture_noise_color.color_ramp.elements[1].position = 0.78
    aperture_noise_color.color_ramp.elements[1].color = _scaled_color(palette["accent"], 0.12)
    aperture_color = aperture_nodes.new("ShaderNodeMixRGB")
    aperture_color.name = "TP_PORTAL_ASYMMETRIC_DEPTH"
    aperture_color.blend_type = "ADD"
    aperture_color.inputs[0].default_value = 0.42
    aperture_color.use_clamp = True
    aperture_links.new(aperture_coordinates.outputs["Generated"], aperture_plane.inputs[0])
    aperture_links.new(aperture_plane.outputs["Vector"], aperture_distance.inputs[0])
    aperture_links.new(aperture_distance.outputs["Value"], aperture_radial.inputs["Fac"])
    aperture_links.new(aperture_coordinates.outputs["Generated"], aperture_noise.inputs["Vector"])
    aperture_links.new(aperture_noise.outputs["Fac"], aperture_noise_color.inputs["Fac"])
    aperture_links.new(aperture_radial.outputs["Color"], aperture_color.inputs[1])
    aperture_links.new(aperture_noise_color.outputs["Color"], aperture_color.inputs[2])
    aperture_links.new(aperture_color.outputs["Color"], aperture_emission.inputs["Color"])
    aperture_links.new(aperture_emission.outputs[0], aperture_output.inputs["Surface"])
    iris_material = create_material(
        "TP_SPACE_MAT_CORE_IRIS",
        palette["secondary"],
        metallic=0.34,
        roughness=0.14,
        emission_color=palette["secondary"],
        emission_strength=0.22 + glow * 0.075,
    )
    _set_principled_finish(iris_material, coat=0.42, coat_roughness=0.06)
    drive_emission(
        iris_material,
        bus,
        "bass_energy",
        f"{0.10 + glow * 0.040:.6f} + v * {0.30 * bass_response:.6f}",
    )
    revelation_material = create_material(
        "TP_SPACE_MAT_REVELATION_HALO",
        _scaled_color(palette["secondary"], 0.16),
        metallic=0.42,
        roughness=0.16,
        emission_color=palette["secondary"],
        emission_strength=0.12 + glow * 0.035,
    )
    _set_principled_finish(revelation_material, coat=0.34, coat_roughness=0.08)
    drive_emission(
        revelation_material,
        bus,
        "other_energy",
        f"{0.055 + glow * 0.020:.6f} + v * 0.34",
    )

    # The visible shell has enough true subdivision for close shots, but most
    # perceived detail comes from two displacement scales and the shader graph.
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.92, location=(0.0, 0.0, 0.0))
    shell = bpy.context.object
    shell.name = "TP_SPACE_CORE_SHELL"
    move_to_collection(shell, collections["TP_PRIMARY_GEOMETRY"])
    shell.data.materials.append(shell_material)
    shell.parent = core_micro
    _smooth_active()
    shell_principled = shell_material.node_tree.nodes.get(shell_material.get("tp_principled_node", "Principled BSDF"))
    shell_alpha = _material_input(shell_principled, "Alpha") if shell_principled is not None else None
    if shell_alpha is not None:
        if not shell_alpha.is_linked:
            shell_alpha.default_value = 0.96
        if hasattr(shell_material, "surface_render_method"):
            try:
                shell_material.surface_render_method = "DITHERED"
            except (TypeError, ValueError):
                pass
    macro_texture = bpy.data.textures.new("TP_SPACE_CORE_MACRO_NOISE", type="CLOUDS")
    macro_texture.noise_scale = 0.30 + (int(seed_plan["heroSeed"]) % 9) * 0.008
    macro_displacement = shell.modifiers.new("TP_SPACE_CORE_MACRO_DISPLACEMENT", "DISPLACE")
    macro_displacement.texture = macro_texture
    macro_displacement.strength = 0.10
    add_property_driver(
        macro_displacement,
        "strength",
        -1,
        bus,
        {"b": "bass_energy", "l": "low_band"},
        f"0.050 + b * {0.065 * bass_response:.6f} + l * 0.025",
    )
    micro_texture = bpy.data.textures.new("TP_SPACE_CORE_FINE_NOISE", type="CLOUDS")
    micro_texture.noise_scale = 0.075
    micro_texture.noise_depth = 2
    micro_displacement = shell.modifiers.new("TP_SPACE_CORE_FINE_DISPLACEMENT", "DISPLACE")
    micro_displacement.texture = micro_texture
    micro_displacement.strength = 0.012

    bpy.ops.mesh.primitive_uv_sphere_add(segments=80, ring_count=48, radius=1.40, location=(0.0, 0.02, 0.0))
    inner = bpy.context.object
    inner.name = "TP_SPACE_CORE_ENERGY"
    move_to_collection(inner, collections["TP_PRIMARY_GEOMETRY"])
    inner.data.materials.append(energy_material)
    inner.parent = core_micro
    _smooth_active()

    # A triangular inner mantle catches energy through the semi-transparent
    # shell and creates a layered engineered-destination silhouette.
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.68, location=(0.0, 0.0, 0.0))
    mantle = bpy.context.object
    mantle.name = "TP_SPACE_CORE_MANTLE"
    move_to_collection(mantle, collections["TP_PRIMARY_GEOMETRY"])
    mantle.data.materials.append(mantle_material)
    mantle.parent = core_micro
    mantle_wireframe = mantle.modifiers.new("TP_SPACE_CORE_MANTLE_WIREFRAME", "WIREFRAME")
    mantle_wireframe.thickness = 0.028

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=2.08, location=(0.0, 0.0, 0.0))
    wire = bpy.context.object
    wire.name = "TP_SPACE_CORE_LATTICE"
    move_to_collection(wire, collections["TP_PRIMARY_GEOMETRY"])
    wire.data.materials.append(wire_material)
    wire.parent = core_micro
    wireframe = wire.modifiers.new("TP_SPACE_CORE_WIREFRAME", "WIREFRAME")
    wireframe.thickness = 0.019

    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=2.23, location=(0.0, 0.0, 0.0))
    atmosphere = bpy.context.object
    atmosphere.name = "TP_SPACE_CORE_ATMOSPHERE"
    move_to_collection(atmosphere, collections["TP_PRIMARY_GEOMETRY"])
    atmosphere.data.materials.append(atmosphere_material)
    atmosphere.parent = core_micro
    _smooth_active()

    # One opaque, asymmetric horizon lives behind the destination.  It opens
    # toward camera with the narrative awakening and gives the arrival a
    # singular silhouette without another transparent shell or broad bloom.
    revelation_rig = _empty(
        "TP_SPACE_REVELATION_RIG",
        collections["TP_PRIMARY_GEOMETRY"],
    )
    revelation_rig.parent = destination_macro
    revelation_halo = _create_patterned_arc(
        "TP_SPACE_REVELATION_HALO",
        collections["TP_PRIMARY_GEOMETRY"],
        revelation_material,
        radius=2.72,
        thickness=0.028,
        start=-0.72,
        span=math.tau,
        tilt=(math.pi / 2.0, 0.0, 0.0),
        depth=0.48,
        pattern=((0.02, 0.09), (0.20, 0.05), (0.43, 0.34), (0.88, 0.045)),
    )
    revelation_halo.parent = revelation_rig

    # An asymmetric, camera-following portal gives the destination dimensional
    # depth at thumbnail scale.  Its off-axis shard and broken crescent avoid a
    # centered pupil/eye read; this remains physical geometry, not a HUD.
    iris_rig = _empty("TP_SPACE_CORE_IRIS_RIG", collections["TP_PRIMARY_GEOMETRY"])
    # Keep the optical face in destination space.  Parenting it to core_micro
    # would compound the core's independent audio rotation with the keyed
    # camera orbit and turn the front-facing aperture into a side lobe.
    iris_rig.parent = destination_macro
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=48,
        ring_count=24,
        radius=1.0,
        location=(0.0, -2.30, 0.0),
    )
    aperture = bpy.context.object
    aperture.name = "TP_SPACE_CORE_APERTURE"
    aperture.scale = (0.68, 0.045, 0.94)
    aperture.location.x = -0.12
    aperture.location.z = 0.04
    aperture.rotation_euler[1] = -0.22
    move_to_collection(aperture, collections["TP_PRIMARY_GEOMETRY"])
    aperture.data.materials.append(aperture_material)
    aperture.parent = iris_rig
    _smooth_active()
    bpy.ops.mesh.primitive_torus_add(
        major_segments=64,
        minor_segments=12,
        major_radius=0.88,
        minor_radius=0.026,
        location=(0.0, -2.36, 0.0),
        rotation=(math.pi / 2.0, 0.0, 0.0),
    )
    iris_ring = bpy.context.object
    iris_ring.name = "TP_SPACE_CORE_IRIS_RING"
    iris_ring.location.x = -0.12
    iris_ring.location.z = 0.04
    iris_ring.scale = (0.78, 1.0, 1.08)
    move_to_collection(iris_ring, collections["TP_PRIMARY_GEOMETRY"])
    iris_ring.data.materials.append(iris_material)
    iris_ring.parent = iris_rig
    _smooth_active()
    iris_seed = None
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=32,
        ring_count=16,
        radius=0.105,
        location=(0.30, -2.37, -0.16),
    )
    iris_seed = bpy.context.object
    iris_seed.name = "TP_SPACE_CORE_IRIS_SEED"
    iris_seed.rotation_euler[1] = -0.38
    move_to_collection(iris_seed, collections["TP_PRIMARY_GEOMETRY"])
    iris_seed.data.materials.append(filament_material)
    iris_seed.parent = iris_rig
    _smooth_active()
    iris_crescent = _create_patterned_arc(
        "TP_SPACE_CORE_IRIS_CRESCENT",
        collections["TP_PRIMARY_GEOMETRY"],
        iris_material,
        radius=1.00,
        thickness=0.052,
        start=-0.58,
        span=math.tau,
        tilt=(math.pi / 2.0, 0.0, 0.0),
        depth=-2.36,
        pattern=((0.00, 0.28), (0.57, 0.10), (0.82, 0.06)),
    )
    iris_crescent.location.x = -0.18
    iris_crescent.location.z = 0.07
    iris_crescent.parent = iris_rig

    detail_vertices: list[tuple[float, float, float]] = []
    detail_faces: list[tuple[int, int, int]] = []
    detail_count = int(seed_plan["heroSurfaceDetailCount"])
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    for index in range(detail_count):
        y = 1.0 - 2.0 * (index + 0.5) / detail_count
        radial = math.sqrt(max(0.0, 1.0 - y * y))
        angle = index * golden_angle + hero_rng.uniform(-0.06, 0.06)
        normal = (radial * math.cos(angle), y, radial * math.sin(angle))
        featured = index % 4 == 0
        _add_radial_panel(
            detail_vertices,
            detail_faces,
            normal,
            radius=1.955 + hero_rng.uniform(-0.010, 0.016),
            width=hero_rng.uniform(0.075, 0.13) if featured else hero_rng.uniform(0.018, 0.038),
            height=hero_rng.uniform(0.055, 0.12) if featured else hero_rng.uniform(0.010, 0.028),
        )
    surface_details = _combined_mesh(
        "TP_SPACE_CORE_SURFACE_DETAILS",
        collections["TP_PRIMARY_GEOMETRY"],
        detail_vertices,
        detail_faces,
        detail_material,
    )
    surface_details.parent = core_micro

    filaments: list[Any] = []
    for index in range(3):
        filament = _create_arc(
            f"TP_SPACE_CORE_FILAMENT_{index + 1:02d}",
            collections["TP_PRIMARY_GEOMETRY"],
            filament_material,
            radius=1.48 + index * 0.13,
            thickness=0.019 + index * 0.004,
            start=hero_rng.uniform(-0.6, 1.1),
            span=1.80 + index * 0.34 + hero_rng.uniform(0.08, 0.28),
            tilt=(
                math.pi / 2.0 + hero_rng.uniform(-0.34, 0.34),
                hero_rng.uniform(-0.32, 0.32),
                hero_rng.uniform(-0.42, 0.42),
            ),
            depth=hero_rng.uniform(-0.04, 0.10),
        )
        filament.parent = core_micro
        filaments.append(filament)
    for index in range(2):
        filament = _create_patterned_arc(
            f"TP_SPACE_CORE_FILAMENT_{index + 4:02d}",
            collections["TP_PRIMARY_GEOMETRY"],
            filament_material,
            radius=2.16 + index * 0.09,
            thickness=0.019 + index * 0.004,
            start=hero_rng.uniform(-0.45, 0.65),
            span=math.tau,
            tilt=(
                math.pi / 2.0 + hero_rng.uniform(-0.22, 0.22),
                hero_rng.uniform(-0.18, 0.18),
                hero_rng.uniform(-0.26, 0.26),
            ),
            depth=hero_rng.uniform(0.02, 0.14),
            pattern=((0.00, 0.15), (0.28, 0.08), (0.55, 0.18), (0.86, 0.06)),
        )
        filament.parent = core_micro
        filaments.append(filament)

    for axis in range(3):
        add_property_driver(
            core_micro,
            "scale",
            axis,
            bus,
            {"b": "bass_energy", "l": "low_band"},
            f"1.0 + b * {0.022 * bass_response:.6f} + l * 0.009",
        )
    add_property_driver(
        core_micro,
        "rotation_euler",
        2,
        bus,
        {"m": "mid_band"},
        "frame * 0.00011 + m * 0.025",
    )
    add_property_driver(
        wire,
        "rotation_euler",
        1,
        bus,
        {"h": "high_band"},
        "-frame * 0.00017 + h * 0.020",
    )
    add_property_driver(
        mantle,
        "rotation_euler",
        0,
        bus,
        {"l": "low_band"},
        "frame * 0.00013 - l * 0.018",
    )
    return {
        "macro": destination_macro,
        "micro": core_micro,
        "irisRig": iris_rig,
        "revelationRig": revelation_rig,
        "revelationHalo": revelation_halo,
        "irisSeed": iris_seed,
        "energyCore": inner,
        "mantle": mantle,
        "lattice": wire,
        "atmosphere": atmosphere,
        "surfaceDetails": surface_details,
        "objects": [
            shell,
            inner,
            mantle,
            wire,
            atmosphere,
            revelation_halo,
            aperture,
            iris_ring,
            iris_seed,
            iris_crescent,
            surface_details,
            *filaments,
        ],
        "materials": [
            shell_material,
            energy_material,
            mantle_material,
            wire_material,
            atmosphere_material,
            detail_material,
            filament_material,
            aperture_material,
            iris_material,
            revelation_material,
        ],
        "surfaceDetailCount": detail_count,
        "filamentCount": len(filaments),
    }


def _create_orbits(collections: Mapping[str, Any], bus: Any, palette: Mapping[str, Color], values: Mapping[str, float | str], seed_plan: Mapping[str, Any]) -> dict[str, Any]:
    ring_rng = random.Random(int(seed_plan["ringSeed"]))
    thickness = float(values["ringThickness"])
    occlusion = float(values["ringOcclusion"])
    drum_response = float(values["drumResponse"])
    glow = float(values["glowStrength"])
    primary_material = create_material(
        "TP_SPACE_MAT_ORBITS_PRIMARY",
        _scaled_color(palette["accent"], 0.18),
        metallic=0.82,
        roughness=0.22,
        emission_color=palette["accent"],
        emission_strength=0.08 + glow * 0.035,
    )
    secondary_material = create_material(
        "TP_SPACE_MAT_ORBITS_SECONDARY",
        _scaled_color(palette["secondary"], 0.16),
        metallic=0.78,
        roughness=0.27,
        emission_color=palette["secondary"],
        emission_strength=0.035 + glow * 0.018,
    )
    trim_material = create_material(
        "TP_SPACE_MAT_ORBITS_TRIM",
        palette["core"],
        metallic=0.46,
        roughness=0.14,
        emission_color=palette["highlight"],
        emission_strength=0.30 + glow * 0.11,
    )
    _add_fresnel_microfinish(
        primary_material,
        edge_color=palette["highlight"],
        body_color=palette["accent"],
        texture_scale=13.0,
    )
    _add_fresnel_microfinish(
        secondary_material,
        edge_color=palette["accent"],
        body_color=palette["secondary"],
        texture_scale=17.0,
    )
    _add_fresnel_microfinish(
        trim_material,
        edge_color=palette["highlight"],
        body_color=palette["core"],
        texture_scale=23.0,
    )
    drive_emission(primary_material, bus, "drum_energy", f"{0.035 + glow * 0.018:.6f} + v * {0.22 * drum_response:.6f}")
    drive_emission(secondary_material, bus, "mid_band", f"{0.015 + glow * 0.008:.6f} + v * 0.12")
    drive_emission(trim_material, bus, "transient_activity", f"{0.12 + glow * 0.045:.6f} + v * {0.56 * drum_response:.6f}")
    macro = _empty("TP_ORBIT_MACRO", collections["TP_RINGS"])
    micro = _empty("TP_ORBIT_MICRO", collections["TP_RINGS"])
    micro.parent = macro
    trace_rig = _empty("TP_ORBIT_TRACE_RIG", collections["TP_RINGS"])
    trace_rig.parent = micro
    packet_rig = _empty("TP_ORBIT_PACKET_RIG", collections["TP_RINGS"])
    packet_rig.parent = micro
    foreground_count = min(2, max(0, round(occlusion * 4.0)))
    rings: list[Any] = []
    ring_specs = (
        # One restrained foreground bracket, then a signature broken rail,
        # continuous ellipse, hairline trace, outer broken rail, and far frame.
        (3.12, 1.20 + occlusion * 0.55, 0.88, -0.30 if foreground_count else 0.16, 0.72, trim_material, ((0.00, 0.48), (0.72, 0.16))),
        (3.58, 5.45, -0.24, 0.14, 0.84, primary_material, ((0.00, 0.23), (0.36, 0.09), (0.58, 0.18), (0.89, 0.05))),
        (4.34, 5.02, 0.34, 0.30, 0.34, secondary_material, None),
        (4.94, 4.46, -0.62, 0.43, 0.28, secondary_material, ((0.00, 0.38), (0.68, 0.12))),
        (5.72, 5.78, 0.08, 0.58, 0.58, primary_material, ((0.00, 0.14), (0.25, 0.28), (0.70, 0.10))),
        (6.55, 4.84, -0.44, 0.78, 0.32, secondary_material, None),
    )
    for index, (radius, span, start, depth, width_scale, material, pattern) in enumerate(ring_specs):
        tilt = (
            math.pi / 2.0 + (-0.12 + index * 0.045) + ring_rng.uniform(-0.035, 0.035),
            (-0.14 + index * 0.055) + ring_rng.uniform(-0.025, 0.025),
            (-0.24 + index * 0.09) + ring_rng.uniform(-0.035, 0.035),
        )
        if index == 2:
            tilt = (math.pi / 2.0 + 0.40, -0.28, 0.14)
        elif index == 5:
            tilt = (math.pi / 2.0 - 0.30, 0.22, 0.26)
        create = _create_arc if pattern is None else _create_patterned_arc
        arguments: dict[str, Any] = {
            "radius": radius,
            "thickness": thickness * width_scale,
            "start": start + ring_rng.uniform(-0.08, 0.08),
            "span": span,
            "tilt": tilt,
            "depth": depth,
        }
        if pattern is not None:
            arguments["pattern"] = pattern
        ring = create(
            f"TP_SPACE_ORBIT_{index + 1:02d}",
            collections["TP_RINGS"],
            material,
            **arguments,
        )
        ring.parent = trace_rig if index in (2, 3, 5) else micro
        rings.append(ring)

    # Nine contract-preserving companions now form sparse hairline traces, not
    # nine additional barcode rings.  Three use irregular breaks; the others
    # are short, quiet arcs with deliberately offset inclinations.
    companions: list[Any] = []
    companion_count = int(seed_plan["companionRingCount"])
    for index in range(companion_count):
        radius = 2.86 + index * 0.43 + ring_rng.uniform(-0.045, 0.045)
        patterned = index in (1, 5, 8)
        create = _create_patterned_arc if patterned else _create_arc
        companion_arguments: dict[str, Any] = {
            "radius": radius,
            "thickness": thickness * (0.22 + (index % 3) * 0.055),
            "start": -0.72 + index * 0.37 + ring_rng.uniform(-0.10, 0.10),
            "span": 1.28 + (index % 4) * 0.34,
            "tilt": (
                math.pi / 2.0 + math.sin(index * 1.7) * 0.19,
                math.cos(index * 1.3) * 0.16,
                -0.32 + index * 0.075,
            ),
            "depth": 0.10 + index * 0.085,
        }
        if patterned:
            companion_arguments["pattern"] = ((0.00, 0.31), (0.52, 0.14), (0.82, 0.07))
        companion = create(
            f"TP_SPACE_ORBIT_COMPANION_{index + 1:02d}",
            collections["TP_RINGS"],
            trim_material if index == 5 else secondary_material,
            **companion_arguments,
        )
        companion.parent = trace_rig
        companions.append(companion)

    beacon_vertices: list[tuple[float, float, float]] = []
    beacon_faces: list[tuple[int, ...]] = []
    beacon_count = 7
    for index in range(beacon_count):
        _orbit_packet(
            beacon_vertices,
            beacon_faces,
            radius=3.50 + (index % 3) * 1.10,
            angle=0.28 + index * 0.82 + ring_rng.uniform(-0.05, 0.05),
            depth=-0.02 + index * 0.035,
            length=0.075 + (index % 2) * 0.035,
            width=0.014,
        )
    beacons = _combined_mesh(
        "TP_SPACE_ORBIT_BEACONS",
        collections["TP_RINGS"],
        beacon_vertices,
        beacon_faces,
        trim_material,
    )
    beacons.parent = packet_rig

    add_property_driver(
        micro,
        "rotation_euler",
        2,
        bus,
        {"d": "drum_energy", "m": "mid_band"},
        f"frame * 0.000035 + d * {0.012 * drum_response:.6f} + m * 0.006",
    )
    add_property_driver(
        trace_rig,
        "rotation_euler",
        2,
        bus,
        {"m": "mid_band", "o": "other_energy"},
        "-frame * 0.000022 + m * 0.006 - o * 0.004",
    )
    add_property_driver(
        packet_rig,
        "rotation_euler",
        2,
        bus,
        {"t": "transient_activity", "d": "drum_energy"},
        "frame * 0.00110 + t * 0.055 + d * 0.018",
    )
    for axis in range(3):
        add_property_driver(
            micro,
            "scale",
            axis,
            bus,
            {"d": "drum_energy", "t": "transient_activity"},
            f"1.0 + d * {0.009 * drum_response:.6f} + t * {0.004 * drum_response:.6f}",
        )
    return {
        "macro": macro,
        "micro": micro,
        "traceRig": trace_rig,
        "packetRig": packet_rig,
        "rings": rings,
        "companions": companions,
        "beacons": beacons,
        "materials": [primary_material, secondary_material, trim_material],
        "foregroundArcCount": foreground_count,
        "orbitDetailCount": len(companions) + 1,
    }


def _create_starfield(collections: Mapping[str, Any], bus: Any, palette: Mapping[str, Color], values: Mapping[str, float | str], seed_plan: Mapping[str, Any]) -> dict[str, Any]:
    glow = float(values["glowStrength"])
    cool_material = create_material(
        "TP_SPACE_MAT_STARS_COOL",
        palette["highlight"],
        metallic=0.0,
        roughness=0.26,
        emission_color=palette["highlight"],
        emission_strength=0.22 + glow * 0.07,
    )
    violet_material = create_material(
        "TP_SPACE_MAT_STARS_VIOLET",
        palette["secondary"],
        metallic=0.0,
        roughness=0.30,
        emission_color=palette["secondary"],
        emission_strength=0.11 + glow * 0.045,
    )
    glint_material = create_material(
        "TP_SPACE_MAT_STAR_GLINTS",
        palette["accent"],
        metallic=0.0,
        roughness=0.16,
        emission_color=palette["highlight"],
        emission_strength=0.52 + glow * 0.13,
    )
    drive_emission(cool_material, bus, "high_band", f"{0.12 + glow * 0.040:.6f} + v * 0.42")
    drive_emission(violet_material, bus, "other_energy", f"{0.055 + glow * 0.022:.6f} + v * 0.24")
    drive_emission(glint_material, bus, "transient_activity", f"{0.34 + glow * 0.09:.6f} + v * 0.82")
    rig = _empty("TP_STARFIELD_RIG", collections["TP_STARFIELD"])
    layers: list[Any] = []
    for layer_index, (count, layer_seed) in enumerate(zip(seed_plan["starLayerCounts"], seed_plan["starSeeds"], strict=True)):
        rng = random.Random(int(layer_seed))
        vertices: list[tuple[float, float, float]] = []
        faces: list[tuple[int, ...]] = []
        for _index in range(int(count)):
            x = rng.uniform(-17.0 - layer_index * 5.0, 17.0 + layer_index * 5.0)
            y = rng.uniform(1.5 + layer_index * 6.0, 7.0 + layer_index * 10.0)
            z = rng.uniform(-9.5 - layer_index * 2.7, 9.5 + layer_index * 2.7)
            if abs(x) < 2.4 and abs(z) < 2.4:
                x += 3.2 if x >= 0 else -3.2
            size = rng.uniform(0.012, 0.036) * (1.0 + layer_index * 0.12)
            _billboard_lozenge(
                vertices,
                faces,
                (x, y, z),
                size,
                size * rng.uniform(0.72, 1.22),
                rng.uniform(-0.35, 0.35),
            )
        layer = _combined_mesh(
            f"TP_STAR_LAYER_{layer_index + 1:02d}",
            collections["TP_STARFIELD"],
            vertices,
            faces,
            cool_material if layer_index % 2 == 0 else violet_material,
        )
        layer.parent = rig
        add_property_driver(
            layer,
            "rotation_euler",
            2,
            bus,
            {"h": "high_band"},
            f"frame * {0.000010 + layer_index * 0.000006:.8f} + h * {0.004 + layer_index * 0.002:.6f}",
        )
        add_property_driver(
            layer,
            "location",
            0,
            bus,
            {"h": "high_band"},
            f"sin(frame * {0.00022 + layer_index * 0.00005:.8f}) * {0.045 + layer_index * 0.025:.6f} + h * 0.012",
        )
        layers.append(layer)

    # A handful of camera-facing cross glints establishes a premium optical
    # scale cue while the dense tetrahedron layers carry the actual star field.
    glint_rng = random.Random(int(seed_plan["starSeeds"][-1]) ^ 0xA511E9B3)
    glint_vertices: list[tuple[float, float, float]] = []
    glint_faces: list[tuple[int, ...]] = []
    for _index in range(7):
        x = glint_rng.uniform(-15.5, 15.5)
        z = glint_rng.uniform(-8.5, 8.5)
        if abs(x) < 3.0 and abs(z) < 2.7:
            x += 4.1 if x >= 0 else -4.1
        y = glint_rng.uniform(8.0, 32.0)
        width = glint_rng.uniform(0.012, 0.025)
        length = glint_rng.uniform(0.11, 0.28)
        offset = len(glint_vertices)
        glint_vertices.extend(
            (
                (x - length, y, z),
                (x, y, z + width),
                (x + length, y, z),
                (x, y, z - width),
                (x - width, y, z),
                (x, y, z + length),
                (x + width, y, z),
                (x, y, z - length),
            )
        )
        glint_faces.extend(
            (
                (offset, offset + 1, offset + 2),
                (offset, offset + 2, offset + 3),
                (offset + 4, offset + 5, offset + 6),
                (offset + 4, offset + 6, offset + 7),
            )
        )
    glints = _combined_mesh(
        "TP_SPACE_STAR_GLINTS",
        collections["TP_STARFIELD"],
        glint_vertices,
        glint_faces,
        glint_material,
    )
    glints.parent = rig
    return {
        "rig": rig,
        "layers": layers,
        "glints": glints,
        "materials": [cool_material, violet_material, glint_material],
    }


def _create_debris(collections: Mapping[str, Any], bus: Any, palette: Mapping[str, Color], values: Mapping[str, float | str], seed_plan: Mapping[str, Any]) -> dict[str, Any]:
    rng = random.Random(int(seed_plan["debrisSeed"]))
    material = create_material(
        "TP_SPACE_MAT_DEBRIS",
        _scaled_color(palette["secondary"], 0.24),
        metallic=0.82,
        roughness=0.28,
        emission_color=palette["accent"],
        emission_strength=0.025,
    )
    _add_fresnel_microfinish(
        material,
        edge_color=palette["highlight"],
        body_color=palette["secondary"],
        texture_scale=9.0,
    )
    drive_emission(material, bus, "transient_activity", "0.010 + v * 0.11")
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    for _index in range(int(seed_plan["shardCount"])):
        radius = rng.uniform(4.7, 12.8)
        theta = rng.uniform(0.0, math.tau)
        x = radius * math.cos(theta)
        z = radius * math.sin(theta) * rng.uniform(0.48, 0.92)
        if abs(x) < 2.7 and abs(z) < 2.7:
            x += 3.0 if x >= 0 else -3.0
        width = rng.uniform(0.014, 0.042)
        _billboard_lozenge(
            vertices,
            faces,
            (x, rng.uniform(0.9, 9.5), z),
            width,
            width * rng.uniform(2.4, 4.8),
            theta + math.pi / 2.0 + rng.uniform(-0.22, 0.22),
        )
    debris = _combined_mesh("TP_SPACE_DEBRIS", collections["TP_SHARDS"], vertices, faces, material)
    rig = _empty("TP_SPACE_DEBRIS_RIG", collections["TP_SHARDS"])
    debris.parent = rig
    add_property_driver(
        rig,
        "rotation_euler",
        2,
        bus,
        {"o": "other_energy", "m": "mid_band"},
        "-frame * 0.000035 - o * 0.018 + m * 0.012",
    )
    for axis in range(3):
        add_property_driver(
            rig,
            "scale",
            axis,
            bus,
            {"h": "high_band", "t": "transient_activity"},
            "0.96 + h * 0.035 + t * 0.018",
        )
    return {"rig": rig, "object": debris, "material": material}


def _create_orbital_dust(
    collections: Mapping[str, Any],
    bus: Any,
    palette: Mapping[str, Color],
    values: Mapping[str, float | str],
    seed_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one combined near-field dust mesh for depth around the hero."""
    rng = random.Random(int(seed_plan["debrisSeed"]) ^ 0x91E10DA5)
    glow = float(values["glowStrength"])
    material = create_material(
        "TP_SPACE_MAT_ORBITAL_DUST",
        palette["accent"],
        metallic=0.18,
        roughness=0.28,
        emission_color=palette["highlight"],
        emission_strength=0.055 + glow * 0.026,
    )
    drive_emission(material, bus, "high_band", f"{0.035 + glow * 0.018:.6f} + v * 0.24")
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    dust_count = int(seed_plan["orbitalDustCount"])
    for index in range(dust_count):
        radius = rng.uniform(2.55, 7.2)
        angle = index * 2.399963 + rng.uniform(-0.16, 0.16)
        x = radius * math.cos(angle)
        z = radius * math.sin(angle) * rng.uniform(0.68, 1.0)
        if abs(x) < 2.28 and abs(z) < 2.28:
            continue
        size = rng.uniform(0.006, 0.016)
        _billboard_lozenge(
            vertices,
            faces,
            (x, rng.uniform(-0.18, 2.4), z),
            size,
            size * rng.uniform(1.0, 1.8),
            angle + math.pi / 2.0,
        )
    dust = _combined_mesh(
        "TP_SPACE_ORBITAL_DUST",
        collections["TP_SHARDS"],
        vertices,
        faces,
        material,
    )
    rig = _empty("TP_SPACE_ORBITAL_DUST_RIG", collections["TP_SHARDS"])
    dust.parent = rig
    add_property_driver(
        rig,
        "rotation_euler",
        2,
        bus,
        {"h": "high_band", "o": "other_energy"},
        "frame * 0.000065 + h * 0.018 - o * 0.010",
    )
    return {"rig": rig, "object": dust, "material": material, "count": dust_count}


def _create_travel_paths(collections: Mapping[str, Any], bus: Any, palette: Mapping[str, Color], values: Mapping[str, float | str], seed_plan: Mapping[str, Any]) -> dict[str, Any]:
    rng = random.Random(int(seed_plan["debrisSeed"]) ^ 0x5F3759DF)
    near_material = create_material(
        "TP_SPACE_MAT_TRAVEL_NEAR",
        palette["accent"],
        metallic=0.12,
        roughness=0.18,
        emission_color=palette["highlight"],
        emission_strength=0.09,
    )
    far_material = create_material(
        "TP_SPACE_MAT_TRAVEL_FAR",
        palette["secondary"],
        metallic=0.08,
        roughness=0.24,
        emission_color=palette["secondary"],
        emission_strength=0.04,
    )
    drive_emission(near_material, bus, "transient_activity", "0.085 + v * 0.62")
    drive_emission(far_material, bus, "master_energy", "0.028 + v * 0.24")
    layer_vertices: list[list[tuple[float, float, float]]] = [[], []]
    layer_faces: list[list[tuple[int, int, int]]] = [[], []]
    for index in range(int(seed_plan["travelStreakCount"])):
        layer_index = index % 2
        vertices = layer_vertices[layer_index]
        faces = layer_faces[layer_index]
        angle = rng.uniform(0.0, math.tau)
        radius = rng.uniform(5.0, 14.0)
        length = rng.uniform(0.34, 0.92) if layer_index == 0 else rng.uniform(0.12, 0.42)
        width = rng.uniform(0.012, 0.028) if layer_index == 0 else rng.uniform(0.004, 0.011)
        x = radius * math.cos(angle)
        z = radius * math.sin(angle) * 0.66
        direction_x = -math.cos(angle) * length
        direction_z = -math.sin(angle) * length * 0.66
        perpendicular_x = -math.sin(angle) * width
        perpendicular_z = math.cos(angle) * width
        depth = rng.uniform(0.2, 5.5) if layer_index == 0 else rng.uniform(6.0, 15.0)
        offset = len(vertices)
        vertices.extend(
            (
                (x + perpendicular_x, depth, z + perpendicular_z),
                (x - perpendicular_x, depth, z - perpendicular_z),
                (x + direction_x + perpendicular_x * 0.18, depth, z + direction_z + perpendicular_z * 0.18),
                (x + direction_x - perpendicular_x * 0.18, depth, z + direction_z - perpendicular_z * 0.18),
            )
        )
        faces.extend(((offset, offset + 1, offset + 2), (offset + 1, offset + 3, offset + 2)))
    near_streaks = _combined_mesh(
        "TP_SPACE_TRAVEL_STREAKS_NEAR",
        collections["TP_TRAVEL_PATHS"],
        layer_vertices[0],
        layer_faces[0],
        near_material,
    )
    far_streaks = _combined_mesh(
        "TP_SPACE_TRAVEL_STREAKS_FAR",
        collections["TP_TRAVEL_PATHS"],
        layer_vertices[1],
        layer_faces[1],
        far_material,
    )
    # Narrative travel staging belongs on a parent so it composes with, rather
    # than replaces, the existing audio-driven X/Z scale on TP_SPACE_TRAVEL_RIG.
    macro = _empty("TP_SPACE_TRAVEL_MACRO", collections["TP_TRAVEL_PATHS"])
    rig = _empty("TP_SPACE_TRAVEL_RIG", collections["TP_TRAVEL_PATHS"])
    rig.parent = macro
    near_streaks.parent = rig
    far_streaks.parent = rig
    for axis in (0, 2):
        add_property_driver(
            rig,
            "scale",
            axis,
            bus,
            {"m": "master_energy", "t": "transient_activity"},
            "0.88 + m * 0.11 + t * 0.045",
        )
    return {
        "macro": macro,
        "rig": rig,
        "object": near_streaks,
        "objects": [near_streaks, far_streaks],
        "materials": [near_material, far_material],
    }


def _create_vocal_wisps(collections: Mapping[str, Any], bus: Any, palette: Mapping[str, Color], values: Mapping[str, float | str]) -> dict[str, Any]:
    material = create_material(
        "TP_SPACE_MAT_VOCAL_WISPS",
        palette["secondary"],
        metallic=0.10,
        roughness=0.22,
        emission_color=palette["secondary"],
        emission_strength=0.08,
    )
    vocal_response = float(values["vocalResponse"])
    drive_emission(material, bus, "vocal_energy", f"0.012 + v * {0.34 * vocal_response:.6f}")
    rig = _empty("TP_SPACE_VOCAL_RIG", collections["TP_VOCAL_ELEMENTS"])
    wisps: list[Any] = []
    for index in range(3):
        wisp = _create_segmented_arc(
            f"TP_SPACE_VOCAL_WISP_{index + 1:02d}",
            collections["TP_VOCAL_ELEMENTS"],
            material,
            radius=3.0 + index * 0.48,
            thickness=0.011 + index * 0.002,
            start=0.55 + index * 1.25,
            span=1.25 + index * 0.18,
            tilt=(
                math.pi / 2.0 - 0.16 + index * 0.15,
                -0.24 + index * 0.20,
                0.12 + index * 0.24,
            ),
            depth=0.72 + index * 0.22,
            segment_count=6 + index,
            duty_cycle=0.48 + index * 0.05,
            phase=0.025 * index,
        )
        wisp.parent = rig
        wisps.append(wisp)
    for axis in range(3):
        add_property_driver(
            rig,
            "scale",
            axis,
            bus,
            {"v": "vocal_energy"},
            f"0.96 + v * {0.065 * vocal_response:.6f}",
        )
    add_property_driver(
        rig,
        "rotation_euler",
        1,
        bus,
        {"v": "vocal_energy"},
        f"frame * 0.000045 + v * {0.025 * vocal_response:.6f}",
    )
    return {"rig": rig, "wisps": wisps, "material": material}


def _create_nebula(collections: Mapping[str, Any], bus: Any, palette: Mapping[str, Color], values: Mapping[str, float | str], seed_plan: Mapping[str, Any]) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    fog_depth = float(values["fogDepth"])
    rig = _empty("TP_NEBULA_RIG", collections["TP_NEBULA"])
    planes: list[Any] = []
    materials: list[Any] = []
    nebula_seed = int(seed_plan["nebulaSeed"])
    for index in range(4):
        color = palette["fog"] if index % 2 == 0 else palette["secondary"]
        # Violet layers carry the poetic directional veil while the darker
        # alternating layers preserve depth and black-level restraint.
        strength_scale = 1.00 if index % 2 == 0 else 1.42
        material = _nebula_material(
            f"TP_SPACE_MAT_NEBULA_{index + 1:02d}",
            color,
            bus,
            fog_depth,
            nebula_seed + index * 17,
            strength_scale,
        )
        bpy.ops.mesh.primitive_plane_add(
            size=2.0,
            location=(-6.0 + index * 4.0, 7.0 + index * 9.0, -2.6 + index * 1.5),
            rotation=(math.pi / 2.0, 0.0, 0.075 * (index - 1.5)),
        )
        plane = bpy.context.object
        plane.name = f"TP_SPACE_NEBULA_LAYER_{index + 1:02d}"
        move_to_collection(plane, collections["TP_NEBULA"])
        # Overscan each one-polygon fog layer well beyond a 16:9 frustum.  The
        # rig follows the camera orbit below, keeping every rectangular edge out
        # of frame without adding volumetric render cost.
        plane.scale = (34.0 + index * 9.0, 22.0 + index * 5.5, 1.0)
        plane.data.materials.append(material)
        plane.parent = rig
        planes.append(plane)
        materials.append(material)
    background_anchor = _empty("TP_SPACE_BACKGROUND_ANCHOR", collections["TP_BACKGROUND"])
    background_anchor.parent = rig
    return {"rig": rig, "planes": planes, "materials": materials}


def _create_lighting(
    collections: Mapping[str, Any],
    bus: Any,
    palette: Mapping[str, Color],
    values: Mapping[str, float | str],
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]
    from mathutils import Vector  # type: ignore[import-not-found]

    glow = float(values["glowStrength"])
    rig = _empty("TP_SPACE_LIGHTING_RIG", collections["TP_LIGHTS"])
    lights: list[Any] = []
    specifications = (
        # The core light sits behind the shell: the aperture stays dark while
        # fissures and atmosphere receive a convincing internal edge glow.
        ("TP_SPACE_LIGHT_CORE", "POINT", (0.0, 1.35, -0.15), palette["accent"][:3], 230.0 + glow * 55.0, "bass_energy"),
        ("TP_SPACE_LIGHT_RIM", "AREA", (5.7, -4.8, 5.8), palette["highlight"][:3], 610.0 + glow * 80.0, "master_energy"),
        ("TP_SPACE_LIGHT_FILL", "AREA", (-5.6, 2.8, 2.2), palette["secondary"][:3], 470.0 + glow * 70.0, "other_energy"),
        ("TP_SPACE_LIGHT_UNDERSIDE", "AREA", (-3.2, -1.8, -5.2), palette["core"][:3], 250.0 + glow * 42.0, "low_band"),
    )
    for index, (name, light_type, location, color, energy, control) in enumerate(specifications):
        data = bpy.data.lights.new(f"{name}_DATA", type=light_type)
        data.color = color
        data.energy = energy
        data.use_shadow = True
        if light_type == "POINT" and hasattr(data, "shadow_soft_size"):
            data.shadow_soft_size = 1.15
        if light_type == "AREA":
            data.shape = "DISK"
            data.size = 4.8 + index * 1.2
        obj = bpy.data.objects.new(name, data)
        obj.location = location
        collections["TP_LIGHTS"].objects.link(obj)
        obj.parent = rig
        if light_type == "AREA":
            obj.rotation_euler = (Vector((0.0, 0.0, 0.0)) - obj.location).to_track_quat("-Z", "Y").to_euler()
        add_property_driver(
            data,
            "energy",
            -1,
            bus,
            {"v": control},
            f"{energy * 0.62:.6f} + v * {energy * 0.34:.6f}",
        )
        lights.append(obj)
    return {"rig": rig, "lights": lights}


def _create_camera(collections: Mapping[str, Any], direction_plan: list[dict[str, Any]]) -> tuple[Any, Any, Any]:
    import bpy  # type: ignore[import-not-found]

    target = _empty("TP_CAMERA_TARGET", collections["TP_CAMERAS"])
    rig = _empty("TP_SPACE_CAMERA_RIG", collections["TP_CAMERAS"])
    camera_data = bpy.data.cameras.new("TP_CAMERA_DATA")
    camera = bpy.data.objects.new("TP_CAMERA", camera_data)
    collections["TP_CAMERAS"].objects.link(camera)
    camera.parent = rig
    camera_data.sensor_width = 36.0
    camera_data.dof.use_dof = False
    bpy.context.scene.camera = camera
    constraint = camera.constraints.new("TRACK_TO")
    constraint.name = "TP_STABLE_DESTINATION_TRACK"
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"
    for state in direction_plan:
        frame = int(state["frame"])
        target.location = (
            float(state["targetOffsetX"]),
            0.0,
            float(state["targetOffsetZ"]),
        )
        target.keyframe_insert("location", frame=frame)
        rig.rotation_euler[2] = float(state["cameraOrbitRadians"])
        rig.keyframe_insert("rotation_euler", index=2, frame=frame)
        camera.location = (0.0, -float(state["cameraDistance"]), float(state["cameraHeight"]))
        camera.keyframe_insert("location", frame=frame)
        camera_data.lens = float(state["cameraLens"])
        camera_data.keyframe_insert("lens", frame=frame)
        camera_data.shift_x = float(state["cameraShiftX"])
        camera_data.shift_y = float(state["cameraShiftY"])
        camera_data.keyframe_insert("shift_x", frame=frame)
        camera_data.keyframe_insert("shift_y", frame=frame)
    _set_action_easing(target)
    _set_action_easing(rig)
    _set_action_easing(camera)
    _set_action_easing(camera_data)
    return camera, target, rig


def _animate_macro_rigs(
    direction_plan: list[dict[str, Any]],
    hero: Mapping[str, Any],
    orbits: Mapping[str, Any],
    nebula: Mapping[str, Any],
    travel: Mapping[str, Any],
    lighting: Mapping[str, Any],
) -> None:
    destination_macro = hero["macro"]
    iris_rig = hero["irisRig"]
    revelation_rig = hero["revelationRig"]
    iris_seed = hero["irisSeed"]
    energy_core = hero["energyCore"]
    mantle = hero["mantle"]
    lattice = hero["lattice"]
    atmosphere = hero["atmosphere"]
    surface_details = hero["surfaceDetails"]
    orbit_macro = orbits["macro"]
    trace_rig = orbits["traceRig"]
    packet_rig = orbits["packetRig"]
    foreground_arc = (
        orbits["rings"][0]
        if int(orbits["foregroundArcCount"]) > 0 and orbits["rings"]
        else None
    )
    foreground_base_location = (
        tuple(float(value) for value in foreground_arc.location)
        if foreground_arc is not None
        else (0.0, 0.0, 0.0)
    )
    nebula_rig = nebula["rig"]
    travel_rig = travel["macro"]
    lighting_rig = lighting["rig"]
    for state in direction_plan:
        frame = int(state["frame"])
        reveal = float(state["destinationReveal"])
        destination_macro.scale = (reveal, reveal, reveal)
        destination_macro.keyframe_insert("scale", frame=frame)
        awakening = float(state["heroAwakening"])
        camera_orbit = float(state["cameraOrbitRadians"])
        iris_scale = 0.82 + awakening * 0.20
        iris_pitch = -math.atan2(
            float(state["cameraHeight"]),
            float(state["cameraDistance"]),
        )
        iris_rig.scale = (iris_scale, iris_scale, iris_scale)
        iris_rig.rotation_euler = (iris_pitch, 0.0, camera_orbit)
        iris_rig.keyframe_insert("scale", frame=frame)
        iris_rig.keyframe_insert("rotation_euler", frame=frame)
        revelation_scale = 0.68 + awakening * 0.48
        revelation_edge_tilt = (1.0 - awakening) * 0.52
        revelation_rig.scale = (
            revelation_scale,
            revelation_scale,
            revelation_scale,
        )
        revelation_rig.rotation_euler = (
            iris_pitch + revelation_edge_tilt,
            float(state["orbitTiltY"]) * 0.34,
            camera_orbit + float(state["orbitHeading"]) * 0.55,
        )
        revelation_rig.keyframe_insert("scale", frame=frame)
        revelation_rig.keyframe_insert("rotation_euler", frame=frame)
        seed_scale = 0.28 + awakening * 1.00
        iris_seed.scale = (seed_scale * 1.35, seed_scale * 0.62, seed_scale * 0.34)
        iris_seed.keyframe_insert("scale", frame=frame)
        # All luminous sub-shells stay behind the aperture's verified depth
        # clearance.  Their awakening comes from reveal within the premium
        # silhouette, never by crossing the dark optical face.
        energy_scale = 0.92 + awakening * 0.08
        energy_core.scale = (energy_scale, energy_scale, energy_scale)
        energy_core.keyframe_insert("scale", frame=frame)
        mantle_scale = 0.95 + awakening * 0.05
        mantle.scale = (mantle_scale, mantle_scale, mantle_scale)
        mantle.keyframe_insert("scale", frame=frame)
        lattice_scale = 0.97 + awakening * 0.03
        lattice.scale = (lattice_scale, lattice_scale, lattice_scale)
        lattice.keyframe_insert("scale", frame=frame)
        atmosphere_scale = 0.82 + awakening * 0.18
        atmosphere.scale = (atmosphere_scale, atmosphere_scale, atmosphere_scale)
        atmosphere.keyframe_insert("scale", frame=frame)
        detail_scale = 0.97 + awakening * 0.03
        surface_details.scale = (detail_scale, detail_scale, detail_scale)
        surface_details.keyframe_insert("scale", frame=frame)
        orbit_reveal = float(state["orbitReveal"])
        orbit_macro.scale = (orbit_reveal, orbit_reveal, orbit_reveal)
        orbit_macro.location = (
            float(state["orbitOffsetX"]),
            0.0,
            float(state["orbitOffsetZ"]),
        )
        orbit_macro.rotation_euler = (
            float(state["orbitTiltX"]),
            float(state["orbitTiltY"]),
            camera_orbit * 0.16 + float(state["orbitHeading"]),
        )
        orbit_macro.keyframe_insert("scale", frame=frame)
        orbit_macro.keyframe_insert("location", frame=frame)
        orbit_macro.keyframe_insert("rotation_euler", frame=frame)
        if foreground_arc is not None:
            foreground_arc.location = (
                foreground_base_location[0] + float(state["foregroundOffsetX"]),
                foreground_base_location[1] + float(state["foregroundOffsetY"]),
                foreground_base_location[2] + float(state["foregroundOffsetZ"]),
            )
            foreground_arc.keyframe_insert("location", frame=frame)
        trace_rig.rotation_euler[0] = float(state["traceTiltX"])
        trace_rig.rotation_euler[1] = float(state["traceTiltY"])
        trace_rig.keyframe_insert("rotation_euler", index=0, frame=frame)
        trace_rig.keyframe_insert("rotation_euler", index=1, frame=frame)
        packet_scale = float(state["packetScale"])
        packet_rig.scale = (packet_scale, packet_scale, packet_scale)
        packet_rig.keyframe_insert("scale", frame=frame)
        fog_scale = float(state["fogScale"])
        nebula_rig.scale = (fog_scale, fog_scale, fog_scale)
        nebula_rig.location = (
            float(state["nebulaOffsetX"]),
            0.0,
            float(state["nebulaOffsetZ"]),
        )
        nebula_rig.rotation_euler[2] = camera_orbit * 0.08 + float(state["nebulaRoll"])
        nebula_rig.keyframe_insert("scale", frame=frame)
        nebula_rig.keyframe_insert("location", frame=frame)
        nebula_rig.keyframe_insert("rotation_euler", index=2, frame=frame)
        travel_scale = float(state["travelScale"])
        travel_rig.scale = (travel_scale, travel_scale, travel_scale)
        travel_rig.location = (
            float(state["travelOffsetX"]),
            float(state["travelOffsetY"]),
            float(state["travelOffsetZ"]),
        )
        travel_rig.keyframe_insert("scale", frame=frame)
        travel_rig.keyframe_insert("location", frame=frame)
        lighting_scale = float(state["lightingScale"])
        lighting_rig.scale = (lighting_scale, lighting_scale, lighting_scale)
        lighting_rig.rotation_euler[2] = camera_orbit * 0.35 + float(state["lightingRoll"])
        lighting_rig.keyframe_insert("scale", frame=frame)
        lighting_rig.keyframe_insert("rotation_euler", index=2, frame=frame)
    for owner in (
        destination_macro,
        iris_rig,
        revelation_rig,
        iris_seed,
        energy_core,
        mantle,
        lattice,
        atmosphere,
        surface_details,
        orbit_macro,
        trace_rig,
        packet_rig,
        nebula_rig,
        travel_rig,
        lighting_rig,
    ):
        _set_action_easing(owner)
    if foreground_arc is not None:
        _set_action_easing(foreground_arc)


def build_space_journey(
    cues: dict[str, Any],
    bus: Any,
    seed: int,
    parameters: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    import bpy  # type: ignore[import-not-found]

    values = _parameters(parameters or {})
    palette_name = str(values["palette"])
    palette = PALETTES[palette_name]
    seed_plan = deterministic_space_seed_plan(seed, values)
    direction_plan = build_space_journey_direction_plan(cues, values)
    collections = create_collections(SPACE_JOURNEY_COLLECTIONS)
    warnings: list[str] = []
    _empty("TP_SPACE_ENVIRONMENT_RIG", collections["TP_SPACE_ENVIRONMENT"])
    _empty("TP_WORLD_ANCHOR", collections["TP_WORLD"])
    hero = _create_hero(collections, bus, palette, values, seed_plan)
    orbits = _create_orbits(collections, bus, palette, values, seed_plan)
    stars = _create_starfield(collections, bus, palette, values, seed_plan)
    debris = _create_debris(collections, bus, palette, values, seed_plan)
    orbital_dust = _create_orbital_dust(collections, bus, palette, values, seed_plan)
    travel = _create_travel_paths(collections, bus, palette, values, seed_plan)
    vocals = _create_vocal_wisps(collections, bus, palette, values)
    nebula = _create_nebula(collections, bus, palette, values, seed_plan)
    lighting = _create_lighting(collections, bus, palette, values)
    camera, target, camera_rig = _create_camera(collections, direction_plan)
    _configure_world(bus, palette)
    _configure_glow(float(values["glowStrength"]), warnings)
    _animate_macro_rigs(
        direction_plan,
        hero,
        orbits,
        nebula,
        travel,
        lighting,
    )
    scene = bpy.context.scene
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except (TypeError, ValueError):
        warnings.append("agx_medium_high_contrast_unavailable")
    scene.render.image_settings.color_mode = "RGBA"
    scene["trackprompt_camera_target"] = target.name
    return {
        "collections": list(collections),
        "camera": camera.name,
        "cameraTarget": target.name,
        "cameraRig": camera_rig.name,
        "lightCount": len(lighting["lights"]),
        "ringCount": len(orbits["rings"]),
        "companionRingCount": len(orbits["companions"]),
        "orbitDetailCount": int(orbits["orbitDetailCount"]),
        "foregroundArcCount": orbits["foregroundArcCount"],
        "shardCount": int(seed_plan["shardCount"]),
        "starCount": sum(int(value) for value in seed_plan["starLayerCounts"]),
        "starLayerCount": len(stars["layers"]),
        "travelStreakCount": int(seed_plan["travelStreakCount"]),
        "orbitalDustCount": int(orbital_dust["count"]),
        "nebulaLayerCount": len(nebula["planes"]),
        "vocalWispCount": len(vocals["wisps"]),
        "heroObjects": [obj.name for obj in hero["objects"]],
        "heroSurfaceDetailCount": int(hero["surfaceDetailCount"]),
        "heroFilamentCount": int(hero["filamentCount"]),
        "macroStateCount": len(direction_plan),
        "directionPlan": direction_plan,
        "palette": palette_name,
        "seedPlan": seed_plan,
        "warnings": warnings,
        "combinedDebrisObject": debris["object"].name,
    }

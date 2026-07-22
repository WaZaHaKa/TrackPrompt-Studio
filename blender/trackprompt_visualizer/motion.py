from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from .curve_importer import iter_action_fcurves


@dataclass(frozen=True, slots=True)
class MotionProfile:
    interpolation: Literal["BEZIER", "LINEAR", "CONSTANT"]
    ease_in_frames: int
    ease_out_frames: int
    maximum_velocity: float
    maximum_acceleration: float
    maximum_angular_velocity: float


MOTION_PROFILES: dict[str, MotionProfile] = {
    "cinematic_drift": MotionProfile("BEZIER", 60, 60, 3.0, 1.2, 0.30),
    "slow_acceleration": MotionProfile("BEZIER", 45, 75, 4.0, 1.5, 0.35),
    "controlled_chase": MotionProfile("BEZIER", 30, 45, 8.0, 2.5, 0.55),
    "weightless_float": MotionProfile("BEZIER", 75, 75, 2.0, 0.8, 0.25),
    "impact_recoil": MotionProfile("BEZIER", 6, 48, 5.0, 4.0, 0.75),
    "transformation_orbit": MotionProfile("BEZIER", 45, 60, 4.0, 1.6, 0.80),
    "micro_audio_response": MotionProfile("BEZIER", 2, 12, 0.25, 0.5, 0.08),
}


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    blend = position - lower
    return ordered[lower] * (1.0 - blend) + ordered[upper] * blend


def smooth_control(
    values: list[float],
    *,
    sample_rate_hz: float,
    lower_percentile: float = 0.05,
    upper_percentile: float = 0.95,
    deadband: float = 0.04,
    attack_seconds: float = 0.08,
    release_seconds: float = 0.35,
    low_pass_seconds: float = 0.12,
    response_exponent: float = 1.15,
) -> list[float]:
    if len(values) < 2 or not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("motion input must contain finite samples at a positive cadence")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("motion input samples must be finite")
    lower = _percentile(values, lower_percentile)
    upper = _percentile(values, upper_percentile)
    span = max(1e-9, upper - lower)
    normalized = [max(0.0, min(1.0, (value - lower) / span)) for value in values]
    deadbanded = [0.0 if value <= deadband else (value - deadband) / (1.0 - deadband) for value in normalized]

    def coefficient(seconds: float) -> float:
        return 1.0 if seconds <= 0 else 1.0 - math.exp(-1.0 / (sample_rate_hz * seconds))

    attack = coefficient(attack_seconds)
    release = coefficient(release_seconds)
    envelope = [deadbanded[0]]
    for value in deadbanded[1:]:
        previous = envelope[-1]
        alpha = attack if value > previous else release
        envelope.append(previous + alpha * (value - previous))
    low_pass = coefficient(low_pass_seconds)
    smoothed = [envelope[0]]
    for value in envelope[1:]:
        smoothed.append(smoothed[-1] + low_pass * (value - smoothed[-1]))
    return [max(0.0, min(1.0, value**response_exponent)) for value in smoothed]


def apply_fcurve_interpolation(owner: Any, data_path: str, profile_name: str) -> None:
    profile = MOTION_PROFILES[profile_name]
    animation = getattr(owner, "animation_data", None)
    action = getattr(animation, "action", None) if animation is not None else None
    if action is None:
        return
    for fcurve in iter_action_fcurves(action):
        if fcurve.data_path != data_path:
            continue
        for point in fcurve.keyframe_points:
            point.interpolation = profile.interpolation
            if profile.interpolation == "BEZIER":
                point.handle_left_type = "AUTO_CLAMPED"
                point.handle_right_type = "AUTO_CLAMPED"

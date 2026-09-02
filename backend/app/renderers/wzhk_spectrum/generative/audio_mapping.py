from __future__ import annotations

import math

from pydantic import Field

from .contracts import GenerativeModel


class AudioInputs(GenerativeModel):
    low: float = Field(ge=0, le=1)
    mid: float = Field(ge=0, le=1)
    high: float = Field(ge=0, le=1)
    transient: float = Field(ge=0, le=1)
    energy: float = Field(ge=0, le=1)


class AudioMappingConfig(GenerativeModel):
    low_scale_gain: float = Field(default=0.4, ge=0, le=1)
    mid_displacement_gain: float = Field(default=0.65, ge=0, le=2)
    high_brightness_gain: float = Field(default=0.8, ge=0, le=2)
    high_sparkle_threshold: float = Field(default=0.6, ge=0, le=1)
    transient_impulse_gain: float = Field(default=0.9, ge=0, le=2)
    energy_motion_gain: float = Field(default=0.75, ge=0, le=2)
    energy_complexity_gain: float = Field(default=0.6, ge=0, le=1)


class MappedAudioResponse(GenerativeModel):
    global_scale: float = Field(ge=0.5, le=1.5)
    local_displacement: float = Field(ge=0, le=1)
    pixel_brightness: float = Field(ge=0, le=1)
    sparkle: float = Field(ge=0, le=1)
    propagation_impulse: float = Field(ge=0, le=1)
    movement_intensity: float = Field(ge=0, le=1)
    complexity: float = Field(ge=0, le=1)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def map_audio(inputs: AudioInputs, config: AudioMappingConfig) -> MappedAudioResponse:
    sparkle_denominator = max(1e-9, 1 - config.high_sparkle_threshold)
    return MappedAudioResponse(
        global_scale=_clamp(
            1 + (inputs.low - 0.5) * config.low_scale_gain,
            0.5,
            1.5,
        ),
        local_displacement=_clamp(inputs.mid * config.mid_displacement_gain),
        pixel_brightness=_clamp(0.2 + inputs.high * config.high_brightness_gain),
        sparkle=_clamp(
            (inputs.high - config.high_sparkle_threshold) / sparkle_denominator
        ),
        propagation_impulse=_clamp(inputs.transient * config.transient_impulse_gain),
        movement_intensity=_clamp(inputs.energy * config.energy_motion_gain),
        complexity=_clamp(inputs.energy * config.energy_complexity_gain),
    )


def propagation_wave(
    *,
    topology_distance: float,
    age_seconds: float,
    impulse: float,
    speed: float = 1.0,
    decay: float = 1.5,
    width: float = 0.15,
) -> float:
    values = (topology_distance, age_seconds, impulse, speed, decay, width)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("propagation inputs must be finite")
    if topology_distance < 0 or age_seconds < 0 or speed <= 0 or decay < 0 or width <= 0:
        raise ValueError("propagation distance and age must be nonnegative; speed and width positive")
    if not 0 <= impulse <= 1:
        raise ValueError("propagation impulse must be between zero and one")
    wavefront = age_seconds * speed
    distance_from_front = (topology_distance - wavefront) / width
    response = impulse * math.exp(-(distance_from_front**2) * 2) * math.exp(-decay * age_seconds)
    return _clamp(response)

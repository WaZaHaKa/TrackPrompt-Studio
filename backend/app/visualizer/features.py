from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from ..analysis.core import AudioData, load_audio
from .curves import asymmetric_smooth, robust_normalize, shared_robust_normalize
from .schemas import (
    CurveName,
    NormalizationMetadata,
    PrivateVisualCurve,
    SmoothingMetadata,
    VisualFeatureArtifact,
)

VISUAL_SAMPLE_RATE_HZ = 20.0
ENERGY_ATTACK_SECONDS = 0.08
ENERGY_RELEASE_SECONDS = 0.35
BRIGHTNESS_ATTACK_SECONDS = 0.15
BRIGHTNESS_RELEASE_SECONDS = 0.30
TRANSIENT_ATTACK_SECONDS = 0.025
TRANSIENT_RELEASE_SECONDS = 0.10
VOCAL_ATTACK_SECONDS = 0.10
VOCAL_RELEASE_SECONDS = 0.45
WINDOW_CHUNK_FRAME_COUNT = 256


def _analysis_windows(audio: AudioData, sample_rate_hz: float) -> np.ndarray:
    hop = max(1, int(round(audio.sample_rate / sample_rate_hz)))
    window = max(hop, int(round(audio.sample_rate * 0.10)))
    frame_count = math.ceil(audio.duration * sample_rate_hz) + 1
    final_length = (frame_count - 1) * hop + window
    # Keep the decoded dtype here. FFT operations promote each bounded chunk as
    # needed, while retaining a full-duration float64 copy would add hundreds of
    # MiB for long 44.1/48 kHz Deep stems.
    mono = np.asarray(audio.mono)
    padded = np.pad(mono, (0, max(0, final_length - mono.size)))
    return np.lib.stride_tricks.sliding_window_view(padded, window)[::hop][:frame_count]


def _window_rms(windows: np.ndarray) -> np.ndarray:
    """Calculate float64 RMS without materializing the full squared matrix."""
    result = np.empty(windows.shape[0], dtype=np.float64)
    width = windows.shape[1]
    for start in range(0, windows.shape[0], WINDOW_CHUNK_FRAME_COUNT):
        stop = min(windows.shape[0], start + WINDOW_CHUNK_FRAME_COUNT)
        chunk = windows[start:stop]
        squared_sum = np.einsum(
            "ij,ij->i",
            chunk,
            chunk,
            dtype=np.float64,
            optimize=False,
        )
        result[start:stop] = np.sqrt(np.maximum(squared_sum / width, 0.0))
    return result


def _full_mix_raw(audio: AudioData, sample_rate_hz: float) -> dict[CurveName, np.ndarray]:
    windows = _analysis_windows(audio, sample_rate_hz)
    count = windows.shape[0]
    master = _window_rms(windows)
    low = np.zeros(count, dtype=np.float64)
    mid = np.zeros(count, dtype=np.float64)
    high = np.zeros(count, dtype=np.float64)
    brightness = np.zeros(count, dtype=np.float64)
    transient = np.zeros(count, dtype=np.float64)
    frequencies = np.fft.rfftfreq(windows.shape[1], 1.0 / audio.sample_rate)
    hann = np.hanning(windows.shape[1])
    previous: np.ndarray | None = None
    for start in range(0, count, WINDOW_CHUNK_FRAME_COUNT):
        stop = min(count, start + WINDOW_CHUNK_FRAME_COUNT)
        magnitude = np.abs(np.fft.rfft(windows[start:stop] * hann, axis=1))
        power = magnitude * magnitude
        low[start:stop] = np.sqrt(np.mean(power[:, (frequencies >= 20.0) & (frequencies < 150.0)], axis=1))
        mid[start:stop] = np.sqrt(np.mean(power[:, (frequencies >= 150.0) & (frequencies < 4000.0)], axis=1))
        high_mask = frequencies >= 4000.0
        high[start:stop] = (
            np.sqrt(np.mean(power[:, high_mask], axis=1))
            if np.any(high_mask)
            else np.zeros(stop - start)
        )
        magnitude_sum = np.maximum(np.sum(magnitude, axis=1), 1e-12)
        brightness[start:stop] = (magnitude @ frequencies) / magnitude_sum
        for local_index, spectrum in enumerate(magnitude):
            global_index = start + local_index
            if previous is not None:
                scale = max(float(np.sum(spectrum)), float(np.sum(previous)), 1e-12)
                transient[global_index] = float(np.sum(np.maximum(0.0, spectrum - previous)) / scale)
            previous = spectrum
    result = {
        CurveName.MASTER_ENERGY: master,
        CurveName.LOW_BAND_ENERGY: low,
        CurveName.MID_BAND_ENERGY: mid,
        CurveName.HIGH_BAND_ENERGY: high,
        CurveName.BRIGHTNESS: brightness,
        CurveName.TRANSIENT_ACTIVITY: transient,
    }
    return result


def _curve(
    values: np.ndarray,
    *,
    group: str,
    sample_rate_hz: float,
    attack_seconds: float,
    release_seconds: float,
) -> PrivateVisualCurve:
    smoothed = asymmetric_smooth(
        values,
        sample_rate_hz=sample_rate_hz,
        attack_seconds=attack_seconds,
        release_seconds=release_seconds,
    )
    return PrivateVisualCurve(
        values=[round(float(value), 6) for value in smoothed],
        normalization=NormalizationMetadata(normalization_group=group),
        smoothing=SmoothingMetadata(
            attack_seconds=attack_seconds,
            release_seconds=release_seconds,
            source_sample_rate_hz=sample_rate_hz,
            output_sample_rate_hz=sample_rate_hz,
        ),
    )


def extract_full_mix_features(
    audio: AudioData,
    job_id: str,
    *,
    sample_rate_hz: float = VISUAL_SAMPLE_RATE_HZ,
) -> VisualFeatureArtifact:
    raw = _full_mix_raw(audio, sample_rate_hz)
    curves: dict[CurveName, PrivateVisualCurve] = {}
    for name, values in raw.items():
        normalized = robust_normalize(values)
        attack, release = (
            (TRANSIENT_ATTACK_SECONDS, TRANSIENT_RELEASE_SECONDS)
            if name == CurveName.TRANSIENT_ACTIVITY
            else (BRIGHTNESS_ATTACK_SECONDS, BRIGHTNESS_RELEASE_SECONDS)
            if name in {CurveName.BRIGHTNESS, CurveName.STEREO_WIDTH}
            else (ENERGY_ATTACK_SECONDS, ENERGY_RELEASE_SECONDS)
        )
        curves[name] = _curve(
            normalized,
            group=name.value,
            sample_rate_hz=sample_rate_hz,
            attack_seconds=attack,
            release_seconds=release,
        )
    return VisualFeatureArtifact(
        job_id=job_id,
        duration_seconds=audio.duration,
        sample_rate_hz=sample_rate_hz,
        curves=curves,
        effective_mode="fast",
    )


def _stem_rms(audio: AudioData, sample_rate_hz: float) -> np.ndarray:
    windows = _analysis_windows(audio, sample_rate_hz)
    return _window_rms(windows)


def extract_stem_features(
    artifact: VisualFeatureArtifact,
    stems: Mapping[str, object],
) -> VisualFeatureArtifact:
    stem_names = {
        "drums": CurveName.DRUM_ENERGY,
        "bass": CurveName.BASS_ENERGY,
        "vocals": CurveName.VOCAL_ENERGY,
        "other": CurveName.OTHER_ENERGY,
    }
    raw: dict[str, np.ndarray] = {}
    for source_name in stem_names:
        path = stems.get(source_name)
        if path is None:
            continue
        raw[source_name] = _stem_rms(load_audio(str(path)), artifact.sample_rate_hz)
    if len(raw) != len(stem_names):
        raise ValueError("all four private coarse stems are required for Deep visual curves")
    normalized = shared_robust_normalize(raw)
    curves = dict(artifact.curves)
    for source_name, curve_name in stem_names.items():
        curves[curve_name] = _curve(
            normalized[source_name],
            group="stems",
            sample_rate_hz=artifact.sample_rate_hz,
            attack_seconds=(VOCAL_ATTACK_SECONDS if source_name == "vocals" else ENERGY_ATTACK_SECONDS),
            release_seconds=(VOCAL_RELEASE_SECONDS if source_name == "vocals" else ENERGY_RELEASE_SECONDS),
        )
    return artifact.model_copy(update={"curves": curves, "effective_mode": "deep"})

from __future__ import annotations

import math

import numpy as np
import pytest

from app.analysis.core import AudioData
from app.visualizer import features as visual_features
from app.visualizer.curves import (
    asymmetric_smooth,
    robust_normalize,
    shared_robust_normalize,
    simplify_points,
)
from app.visualizer.features import extract_full_mix_features
from app.visualizer.schemas import CurveName


def _audio(seconds: float = 3.0, sample_rate: int = 16_000) -> AudioData:
    time = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    signal = (
        0.30 * np.sin(2 * np.pi * 70 * time)
        + 0.16 * np.sin(2 * np.pi * 740 * time)
        + 0.08 * np.sin(2 * np.pi * 6_000 * time)
    )
    signal *= np.linspace(0.2, 1.0, signal.size)
    stereo = np.column_stack((signal, signal)).astype(np.float32)
    return AudioData(
        samples=stereo,
        mono=signal.astype(np.float32),
        sample_rate=sample_rate,
        duration=seconds,
        decoded_min=float(signal.min()),
        decoded_max=float(signal.max()),
        normalization_violation=False,
    )


def test_full_mix_visual_features_are_finite_bounded_and_complete() -> None:
    artifact = extract_full_mix_features(_audio(), "11111111-1111-4111-8111-111111111111")
    required = {
        CurveName.MASTER_ENERGY,
        CurveName.LOW_BAND_ENERGY,
        CurveName.MID_BAND_ENERGY,
        CurveName.HIGH_BAND_ENERGY,
        CurveName.BRIGHTNESS,
        CurveName.TRANSIENT_ACTIVITY,
    }
    assert required.issubset(artifact.curves)
    assert len(artifact.curves[CurveName.MASTER_ENERGY].values) == math.ceil(3.0 * 20) + 1
    assert all(
        math.isfinite(value) and 0.0 <= value <= 1.0
        for curve in artifact.curves.values()
        for value in curve.values
    )


def test_chunked_rms_matches_float64_reference() -> None:
    rng = np.random.default_rng(84291)
    windows = rng.normal(0.0, 0.2, (517, 1600)).astype(np.float32)
    expected = np.sqrt(
        np.maximum(
            np.mean(windows.astype(np.float64) ** 2, axis=1),
            0.0,
        )
    )

    actual = visual_features._window_rms(windows)

    assert np.allclose(actual, expected, rtol=1e-12, atol=1e-12)


def test_maximum_duration_rms_shape_is_processed_in_bounded_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame_count = math.ceil(1200.0 * 20.0) + 1
    window_size = 4800  # 100 ms at the supported 48 kHz upper-bound case.
    source = np.ones(1, dtype=np.float32)
    windows = np.lib.stride_tricks.as_strided(
        source,
        shape=(frame_count, window_size),
        strides=(0, 0),
        writeable=False,
    )
    observed_rows: list[int] = []

    def fake_einsum(
        _subscripts: str,
        left: np.ndarray,
        _right: np.ndarray,
        **_kwargs: object,
    ) -> np.ndarray:
        observed_rows.append(left.shape[0])
        return np.zeros(left.shape[0], dtype=np.float64)

    monkeypatch.setattr(visual_features.np, "einsum", fake_einsum)

    result = visual_features._window_rms(windows)

    assert result.shape == (frame_count,)
    assert len(observed_rows) == math.ceil(
        frame_count / visual_features.WINDOW_CHUNK_FRAME_COUNT
    )
    assert max(observed_rows) <= visual_features.WINDOW_CHUNK_FRAME_COUNT


def test_robust_normalization_handles_silence_constant_and_outlier() -> None:
    assert np.array_equal(robust_normalize(np.zeros(20)), np.zeros(20))
    assert np.array_equal(robust_normalize(np.ones(20)), np.zeros(20))
    normalized = robust_normalize(np.asarray([0.0] * 50 + [1.0] * 50 + [1000.0]))
    assert normalized[-2] == 1.0
    assert normalized[-1] == 1.0


def test_shared_stem_normalization_preserves_relative_prominence() -> None:
    normalized = shared_robust_normalize(
        {
            "drums": np.linspace(0.2, 0.8, 100),
            "vocals": np.linspace(0.001, 0.004, 100),
        }
    )
    assert float(np.mean(normalized["drums"])) > 0.4
    assert float(np.max(normalized["vocals"])) < 0.02


def test_smoothing_uses_fast_attack_and_slower_release() -> None:
    smoothed = asymmetric_smooth(
        [0.0, 1.0, 0.0, 0.0, 0.0],
        sample_rate_hz=20.0,
        attack_seconds=0.025,
        release_seconds=0.25,
    )
    assert smoothed[1] > 0.8
    assert smoothed[2] > smoothed[4] > 0.0


def test_simplification_preserves_landmarks_extrema_and_bound() -> None:
    points = [
        (frame, min(1.0, max(0.0, 0.5 + 0.3 * math.sin(frame / 11))))
        for frame in range(1, 1001)
    ]
    points[499] = (500, 1.0)
    simplified, error, effective_tolerance = simplify_points(
        points,
        tolerance=0.008,
        maximum_point_count=180,
        forced_frames={1, 250, 500, 750, 1000},
        important_peak_frames={500},
    )
    frames = {frame for frame, _value in simplified}
    assert {1, 250, 500, 750, 1000}.issubset(frames)
    assert len(simplified) <= 180
    assert error <= effective_tolerance + 1e-9
    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]

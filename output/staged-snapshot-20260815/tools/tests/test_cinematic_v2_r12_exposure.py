from __future__ import annotations

import pytest

from tools.analyze_cinematic_v2_r12_exposure import frame_exposure_metrics


def test_exposure_metrics_detects_large_clipped_white_region() -> None:
    width, height = 10, 10
    rgb = bytearray([8, 12, 18] * (width * height))
    for y in range(4):
        for x in range(5):
            offset = (y * width + x) * 3
            rgb[offset : offset + 3] = b"\xff\xff\xff"
    metrics = frame_exposure_metrics(bytes(rgb), width, height)
    assert metrics["nearWhiteFraction"] == pytest.approx(0.2)
    assert metrics["clippedWhiteFraction"] == pytest.approx(0.2)
    assert metrics["largestNearWhiteComponentFraction"] == pytest.approx(0.2)
    assert metrics["p99Luminance"] == 1.0


def test_exposure_metrics_separates_disconnected_highlights() -> None:
    width, height = 10, 10
    rgb = bytearray([32, 40, 48] * (width * height))
    for pixel in (0, 9, 90, 99):
        offset = pixel * 3
        rgb[offset : offset + 3] = bytes((250, 250, 250))
    metrics = frame_exposure_metrics(bytes(rgb), width, height)
    assert metrics["nearWhiteFraction"] == pytest.approx(0.04)
    assert metrics["clippedWhiteFraction"] == 0.0
    assert metrics["largestNearWhiteComponentFraction"] == pytest.approx(0.01)


def test_exposure_metrics_rejects_mismatched_frame_bytes() -> None:
    with pytest.raises(ValueError, match="byte count"):
        frame_exposure_metrics(b"\x00" * 5, 2, 2)

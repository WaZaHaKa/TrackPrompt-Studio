from __future__ import annotations

import pytest

from tools.analyze_cinematic_v2_r131_media import (
    contrast_metrics,
    neighbor_luminance_delta,
)


def test_r131_contrast_metrics_report_bounded_dynamic_range() -> None:
    rgb = bytes([0, 0, 0] * 5 + [128, 128, 128] * 5 + [255, 255, 255] * 10)
    metrics = contrast_metrics(rgb)
    assert metrics["p10"] == 0.0
    assert metrics["p90"] == pytest.approx(1.0)
    assert metrics["p90MinusP10"] == pytest.approx(1.0)


def test_r131_neighbor_delta_distinguishes_flat_and_alternating_rows() -> None:
    flat = bytes([64, 64, 64] * 4)
    alternating = bytes([0, 0, 0, 255, 255, 255, 0, 0, 0, 255, 255, 255])
    assert neighbor_luminance_delta(flat, 4, 1) == 0.0
    assert neighbor_luminance_delta(alternating, 4, 1) == pytest.approx(1.0)


def test_r131_neighbor_delta_rejects_wrong_dimensions() -> None:
    with pytest.raises(ValueError, match="byte count"):
        neighbor_luminance_delta(bytes([0, 0, 0]), 2, 2)

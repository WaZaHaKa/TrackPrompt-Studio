from __future__ import annotations

import pytest

from tools.analyze_cinematic_v2_r13_lookdev import (
    luminance_metrics,
    masked_separation_metrics,
)


def test_luminance_metrics_flag_near_black_without_manufacturing_scores() -> None:
    width, height = 10, 10
    rgb = bytearray([4, 4, 4] * 65 + [64, 72, 80] * 34 + [255, 255, 255])
    metrics = luminance_metrics(bytes(rgb), width, height)
    assert metrics["nearBlackFraction"] == pytest.approx(0.65)
    assert metrics["clippedHighlightFraction"] == pytest.approx(0.01)
    assert 0.0 < metrics["meanLuminance"] < 1.0


def test_masked_separation_reports_occupancy_and_signed_difference() -> None:
    width, height = 4, 2
    beauty = bytes([200, 200, 200] * 2 + [20, 20, 20] * 6)
    mask = bytes([255, 255, 255] * 2 + [0, 0, 0] * 6)
    metrics = masked_separation_metrics(beauty, mask, width, height)
    assert metrics["occupancyFraction"] == pytest.approx(0.25)
    assert metrics["subjectMeanLuminance"] == pytest.approx(200 / 255)
    assert metrics["backgroundMeanLuminance"] == pytest.approx(20 / 255)
    assert metrics["signedLuminanceSeparation"] > 0.0
    assert metrics["absoluteLuminanceSeparation"] == pytest.approx(180 / 255)


def test_masked_separation_rejects_empty_subject_mask() -> None:
    beauty = bytes([40, 40, 40] * 4)
    mask = bytes([0, 0, 0] * 4)
    with pytest.raises(ValueError, match="both subject and background"):
        masked_separation_metrics(beauty, mask, 2, 2)

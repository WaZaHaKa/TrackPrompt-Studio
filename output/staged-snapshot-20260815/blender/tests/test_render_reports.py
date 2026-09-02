from __future__ import annotations

import pytest

from trackprompt_visualizer.render_reports import motion_metrics


def _sample(frame: int, *, camera_x: float, hero_x: float, lens: float = 35.0) -> dict[str, object]:
    return {
        "frame": frame,
        "cameraName": "TP_R12_CAMERA",
        "cameraLocation": (camera_x, 0.0, 0.0),
        "cameraAngularDelta": 0.01 if frame > 1 else 0.0,
        "protagonistLocation": (hero_x, 0.0, 0.0),
        "lensMm": lens,
    }


def test_motion_metrics_reports_dense_smooth_motion_without_false_jumps() -> None:
    samples = [
        _sample(frame, camera_x=(frame - 1) * 0.01, hero_x=(frame - 1) * 0.015)
        for frame in range(1, 31)
    ]
    report = motion_metrics(samples, fps=30.0)
    assert report["sampleCount"] == 30
    assert report["frameStart"] == 1
    assert report["frameEnd"] == 30
    assert report["oneFrameJumps"] == {"camera": [], "protagonist": []}
    assert report["accelerationDiscontinuities"] == []
    assert report["angularVelocityOutliers"] == []
    assert report["lensJumps"] == []
    assert report["cameraChanges"] == []
    assert report["cameraVelocity"]["maximumUnitsPerSecond"] == pytest.approx(0.3)
    assert report["protagonistVelocity"]["maximumUnitsPerSecond"] == pytest.approx(0.45)


def test_motion_metrics_detects_jump_acceleration_lens_and_camera_change() -> None:
    samples = [
        _sample(1, camera_x=0.0, hero_x=0.0, lens=35.0),
        _sample(2, camera_x=0.01, hero_x=0.01, lens=35.0),
        _sample(3, camera_x=5.0, hero_x=4.0, lens=50.0),
    ]
    samples[-1]["cameraName"] = "TP_R12_CAMERA_OTHER"
    samples[-1]["cameraAngularDelta"] = 0.1
    report = motion_metrics(samples, fps=30.0)
    assert report["oneFrameJumps"]["camera"][0]["frame"] == 3
    assert report["oneFrameJumps"]["protagonist"][0]["frame"] == 3
    assert report["accelerationDiscontinuities"]
    assert report["angularVelocityOutliers"] == [
        {"frame": 3, "radiansPerSecond": pytest.approx(3.0)}
    ]
    assert report["lensJumps"] == [{"frame": 3, "deltaMm": 15.0}]
    assert report["cameraChanges"] == [3]


def test_motion_metrics_rejects_sparse_or_invalid_sampling() -> None:
    with pytest.raises(ValueError, match="consecutive"):
        motion_metrics(
            [_sample(1, camera_x=0.0, hero_x=0.0), _sample(3, camera_x=0.1, hero_x=0.1)],
            fps=30.0,
        )
    with pytest.raises(ValueError, match="positive FPS"):
        motion_metrics([_sample(1, camera_x=0.0, hero_x=0.0)], fps=30.0)

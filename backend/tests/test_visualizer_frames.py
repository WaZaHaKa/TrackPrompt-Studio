from __future__ import annotations

import pytest

from app.visualizer.frames import event_frame, frame_end, section_frames


def test_real_case_time_to_frame_convention() -> None:
    assert frame_end(434.286, 30) == 13_029
    assert event_frame(228.8, 434.286, 30) == 6_865


@pytest.mark.parametrize("fps", [24, 25, 30, 50, 60])
def test_frame_conversion_is_half_up_and_clamped(fps: int) -> None:
    duration = 3.2
    assert event_frame(-1.0, duration, fps) == 1
    assert event_frame(100.0, duration, fps) == frame_end(duration, fps)
    assert event_frame(0.5 / fps, duration, fps) == 2
    start, end = section_frames(0.0, 1.0, duration, fps)
    assert start == 1
    assert end == event_frame(1.0, duration, fps) - 1
    assert section_frames(1.0, duration, duration, fps, final=True)[1] == frame_end(duration, fps)


def test_invalid_frame_inputs_fail_safely() -> None:
    with pytest.raises(ValueError):
        frame_end(float("nan"), 30)
    with pytest.raises(ValueError):
        frame_end(1.0, 29)

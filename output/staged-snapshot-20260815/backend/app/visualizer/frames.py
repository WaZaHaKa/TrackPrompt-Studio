from __future__ import annotations

import math

from .schemas import ALLOWED_FPS


def frame_end(duration_seconds: float, fps: int, frame_start: int = 1) -> int:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0.0:
        raise ValueError("durationSeconds must be finite and positive")
    if fps not in ALLOWED_FPS:
        raise ValueError("unsupported frame rate")
    return frame_start + math.ceil(duration_seconds * fps) - 1


def event_frame(
    time_seconds: float,
    duration_seconds: float,
    fps: int,
    frame_start: int = 1,
) -> int:
    if not math.isfinite(time_seconds):
        raise ValueError("event time must be finite")
    last = frame_end(duration_seconds, fps, frame_start)
    # floor(x + .5) is intentional nearest-half-up behavior, not bankers rounding.
    calculated = frame_start + math.floor(max(0.0, time_seconds) * fps + 0.5)
    return min(last, max(frame_start, calculated))


def section_frames(
    start_seconds: float,
    end_seconds: float,
    duration_seconds: float,
    fps: int,
    *,
    final: bool = False,
    frame_start: int = 1,
) -> tuple[int, int]:
    start = event_frame(start_seconds, duration_seconds, fps, frame_start)
    end = (
        frame_end(duration_seconds, fps, frame_start)
        if final
        else max(start, event_frame(end_seconds, duration_seconds, fps, frame_start) - 1)
    )
    return start, end

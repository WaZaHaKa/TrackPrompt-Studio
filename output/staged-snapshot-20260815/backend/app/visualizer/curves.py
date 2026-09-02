from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def robust_normalize(
    values: Sequence[float] | FloatArray,
    *,
    lower_percentile: float = 5.0,
    upper_percentile: float = 95.0,
) -> FloatArray:
    array = np.asarray(values, dtype=np.float64)
    array = np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0)
    if array.size == 0:
        return array
    lower = float(np.percentile(array, lower_percentile))
    upper = float(np.percentile(array, upper_percentile))
    if not math.isfinite(lower) or not math.isfinite(upper) or upper - lower <= 1e-12:
        return np.zeros_like(array)
    return np.clip((array - lower) / (upper - lower), 0.0, 1.0)


def shared_robust_normalize(
    groups: Mapping[str, Sequence[float] | FloatArray],
    *,
    lower_percentile: float = 5.0,
    upper_percentile: float = 95.0,
) -> dict[str, FloatArray]:
    arrays = {
        name: np.nan_to_num(np.asarray(values, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
        for name, values in groups.items()
    }
    populated = [array for array in arrays.values() if array.size]
    if not populated:
        return {name: np.zeros_like(array) for name, array in arrays.items()}
    combined = np.concatenate(populated)
    lower = float(np.percentile(combined, lower_percentile))
    upper = float(np.percentile(combined, upper_percentile))
    if not math.isfinite(lower) or not math.isfinite(upper) or upper - lower <= 1e-12:
        return {name: np.zeros_like(array) for name, array in arrays.items()}
    return {
        name: np.clip((array - lower) / (upper - lower), 0.0, 1.0)
        for name, array in arrays.items()
    }


def asymmetric_smooth(
    values: Sequence[float] | FloatArray,
    *,
    sample_rate_hz: float,
    attack_seconds: float,
    release_seconds: float,
) -> FloatArray:
    array = np.clip(
        np.nan_to_num(np.asarray(values, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0),
        0.0,
        1.0,
    )
    if array.size < 2:
        return array
    delta = 1.0 / sample_rate_hz

    def coefficient(duration: float) -> float:
        return 1.0 if duration <= 0.0 else 1.0 - math.exp(-delta / duration)

    attack = coefficient(attack_seconds)
    release = coefficient(release_seconds)
    output = np.empty_like(array)
    output[0] = array[0]
    for index in range(1, array.size):
        alpha = attack if array[index] >= output[index - 1] else release
        output[index] = output[index - 1] + alpha * (array[index] - output[index - 1])
    return np.clip(output, 0.0, 1.0)


def _vertical_error(
    point: tuple[int, float],
    start: tuple[int, float],
    end: tuple[int, float],
) -> float:
    if end[0] == start[0]:
        return abs(point[1] - start[1])
    fraction = (point[0] - start[0]) / (end[0] - start[0])
    expected = start[1] + fraction * (end[1] - start[1])
    return abs(point[1] - expected)


def _rdp_segment_indices(
    points: Sequence[tuple[int, float]],
    start: int,
    end: int,
    tolerance: float,
) -> set[int]:
    retained = {start, end}
    stack = [(start, end)]
    while stack:
        left, right = stack.pop()
        if right - left <= 1:
            continue
        maximum = -1.0
        selected = -1
        for index in range(left + 1, right):
            error = _vertical_error(points[index], points[left], points[right])
            if error > maximum:
                maximum = error
                selected = index
        if selected >= 0 and maximum > tolerance:
            retained.add(selected)
            stack.extend(((left, selected), (selected, right)))
    return retained


def _extrema_rank(points: Sequence[tuple[int, float]]) -> list[tuple[float, int]]:
    ranked: list[tuple[float, int]] = []
    for index in range(1, len(points) - 1):
        previous = points[index - 1][1]
        current = points[index][1]
        following = points[index + 1][1]
        if (current - previous) * (following - current) <= 0.0 and (
            current != previous or current != following
        ):
            prominence = min(abs(current - previous), abs(current - following))
            ranked.append((prominence, index))
    return sorted(ranked, reverse=True)


def reconstruction_error(
    original: Sequence[tuple[int, float]],
    simplified: Sequence[tuple[int, float]],
) -> float:
    if len(simplified) < 2:
        return 1.0
    maximum = 0.0
    segment = 0
    for point in original:
        while segment + 1 < len(simplified) - 1 and point[0] > simplified[segment + 1][0]:
            segment += 1
        maximum = max(
            maximum,
            _vertical_error(point, simplified[segment], simplified[segment + 1]),
        )
    return min(1.0, float(maximum))


def simplify_points(
    points: Sequence[tuple[int, float]],
    *,
    tolerance: float,
    maximum_point_count: int,
    forced_frames: Iterable[int] = (),
    important_peak_frames: Iterable[int] = (),
    extrema_limit: int = 192,
) -> tuple[list[tuple[int, float]], float, float]:
    """Simplify by bounded vertical interpolation error while keeping landmarks."""
    collapsed: list[tuple[int, float]] = []
    for frame, value in sorted(points):
        bounded = min(1.0, max(0.0, float(value)))
        if collapsed and collapsed[-1][0] == frame:
            collapsed[-1] = (frame, bounded)
        else:
            collapsed.append((frame, bounded))
    if len(collapsed) < 2:
        raise ValueError("a cue curve requires at least two distinct frames")
    frame_to_index = {frame: index for index, (frame, _value) in enumerate(collapsed)}
    required = {0, len(collapsed) - 1}
    required.update(frame_to_index[frame] for frame in forced_frames if frame in frame_to_index)
    required.update(frame_to_index[frame] for frame in important_peak_frames if frame in frame_to_index)
    available_extrema = max(0, min(extrema_limit, maximum_point_count - len(required)))
    required.update(index for _prominence, index in _extrema_rank(collapsed)[:available_extrema])

    def run(current_tolerance: float) -> list[tuple[int, float]]:
        indices: set[int] = set(required)
        landmarks = sorted(required)
        for left, right in zip(landmarks, landmarks[1:], strict=False):
            indices.update(_rdp_segment_indices(collapsed, left, right, current_tolerance))
        return [collapsed[index] for index in sorted(indices)]

    effective_tolerance = max(0.0, tolerance)
    simplified = run(effective_tolerance)
    if len(simplified) > maximum_point_count:
        low = effective_tolerance
        high = 1.0
        for _attempt in range(24):
            candidate_tolerance = (low + high) / 2.0
            candidate = run(candidate_tolerance)
            if len(candidate) > maximum_point_count:
                low = candidate_tolerance
            else:
                high = candidate_tolerance
                simplified = candidate
        effective_tolerance = high
    if len(simplified) > maximum_point_count:
        raise ValueError("mandatory curve landmarks exceed the point-count limit")
    return simplified, reconstruction_error(collapsed, simplified), effective_tolerance

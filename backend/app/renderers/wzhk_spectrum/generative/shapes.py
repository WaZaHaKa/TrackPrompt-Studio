from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .contracts import EasingId, IndexedDomainSpec, ShapeId, ShapeSpec


@dataclass(frozen=True, slots=True)
class UVPoint:
    index: int
    u: float
    v: float


@dataclass(frozen=True, slots=True)
class Point3:
    x: float
    y: float
    z: float

    def finite(self) -> bool:
        return math.isfinite(self.x) and math.isfinite(self.y) and math.isfinite(self.z)


TRUSTED_SHAPE_IDS: tuple[ShapeId, ...] = tuple(ShapeId)


def indexed_uv_domain(spec: IndexedDomainSpec) -> tuple[UVPoint, ...]:
    """Create stable point correspondence shared by every built-in shape."""

    columns = spec.columns or math.ceil(math.sqrt(spec.point_count))
    rows = math.ceil(spec.point_count / columns)
    return tuple(
        UVPoint(
            index=index,
            u=(index % columns) / (columns - 1),
            v=(index // columns) / max(1, rows - 1),
        )
        for index in range(spec.point_count)
    )


def _seed_unit(seed: int, index: int, channel: int) -> float:
    value = (seed ^ ((index + 1) * 0x9E3779B1) ^ ((channel + 1) * 0x85EBCA77)) & 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x7FEB352D) & 0xFFFFFFFF
    value ^= value >> 15
    value = (value * 0x846CA68B) & 0xFFFFFFFF
    value ^= value >> 16
    return value / 0xFFFFFFFF


def _scaled(point: Point3, scale: float) -> Point3:
    return Point3(point.x * scale, point.y * scale, point.z * scale)


def _sparse_field(point: UVPoint, spec: ShapeSpec) -> Point3:
    return _scaled(
        Point3(
            (_seed_unit(spec.seed, point.index, 0) * 2 - 1) * spec.spread,
            (_seed_unit(spec.seed, point.index, 1) * 2 - 1) * spec.spread,
            (_seed_unit(spec.seed, point.index, 2) * 2 - 1) * spec.spread,
        ),
        spec.scale,
    )


def _lissajous(point: UVPoint, spec: ShapeSpec) -> Point3:
    angle = math.tau * point.u + spec.phase
    ribbon = (point.v - 0.5) * spec.tube_radius * 2
    return _scaled(
        Point3(
            0.82 * math.sin(3 * angle) + ribbon * math.cos(angle),
            0.82 * math.sin(4 * angle + math.pi / 2) + ribbon * math.sin(angle),
            spec.amplitude * math.sin(5 * angle) + ribbon,
        ),
        spec.scale,
    )


def _matrix_field(point: UVPoint, spec: ShapeSpec) -> Point3:
    x = point.u * 2 - 1
    y = point.v * 2 - 1
    z = (
        spec.amplitude
        * math.sin(math.tau * spec.frequency * point.u + spec.phase)
        * math.cos(math.tau * spec.frequency * point.v + spec.phase)
        * 0.35
    )
    return _scaled(Point3(x, y, z), spec.scale)


def _wave_surface(point: UVPoint, spec: ShapeSpec) -> Point3:
    x = point.u * 2 - 1
    z = point.v * 2 - 1
    y = spec.amplitude * 0.5 * (
        math.sin(math.tau * spec.frequency * point.u + spec.phase)
        + math.cos(math.tau * spec.frequency * point.v - spec.phase)
    )
    return _scaled(Point3(x, y, z), spec.scale)


def _torus_point(point: UVPoint, spec: ShapeSpec, *, twisted: bool) -> Point3:
    theta = math.tau * point.u + spec.phase
    phi = math.tau * point.v + (spec.twist * theta if twisted else 0)
    major_radius = 0.68
    minor_radius = 0.28
    radius = major_radius + minor_radius * math.cos(phi)
    return _scaled(
        Point3(radius * math.cos(theta), radius * math.sin(theta), minor_radius * math.sin(phi)),
        spec.scale,
    )


def _torus(point: UVPoint, spec: ShapeSpec) -> Point3:
    return _torus_point(point, spec, twisted=False)


def _twisted_torus(point: UVPoint, spec: ShapeSpec) -> Point3:
    return _torus_point(point, spec, twisted=True)


def _trefoil_knot(point: UVPoint, spec: ShapeSpec) -> Point3:
    angle = math.tau * point.u + spec.phase
    tube_angle = math.tau * point.v
    center_x = (math.sin(angle) + 2 * math.sin(2 * angle)) / 3
    center_y = (math.cos(angle) - 2 * math.cos(2 * angle)) / 3
    center_z = -math.sin(3 * angle) / 3
    radial = spec.tube_radius * math.cos(tube_angle)
    return _scaled(
        Point3(
            center_x + radial * math.cos(angle),
            center_y + radial * math.sin(angle),
            center_z + spec.tube_radius * math.sin(tube_angle),
        ),
        spec.scale,
    )


def _superformula_radius(angle: float, spec: ShapeSpec) -> float:
    m_angle = spec.superformula_m * angle / 4
    first = abs(math.cos(m_angle)) ** spec.superformula_n2
    second = abs(math.sin(m_angle)) ** spec.superformula_n3
    denominator = max(1e-12, first + second)
    return math.pow(denominator, -1.0 / spec.superformula_n1)


def _superformula(point: UVPoint, spec: ShapeSpec) -> Point3:
    longitude = math.tau * point.u - math.pi + spec.phase
    latitude = math.pi * (point.v - 0.5)
    radial = min(2.5, _superformula_radius(longitude, spec))
    latitude_radius = min(2.5, _superformula_radius(latitude, spec))
    cos_latitude = math.cos(latitude)
    return _scaled(
        Point3(
            radial * math.cos(longitude) * latitude_radius * cos_latitude * 0.55,
            radial * math.sin(longitude) * latitude_radius * cos_latitude * 0.55,
            latitude_radius * math.sin(latitude) * 0.55,
        ),
        spec.scale,
    )


def _spherical_lattice(point: UVPoint, spec: ShapeSpec) -> Point3:
    longitude = math.tau * point.u + spec.phase
    latitude = math.pi * (point.v - 0.5)
    radius = 0.82 + spec.amplitude * 0.08 * math.sin(spec.frequency * longitude)
    return _scaled(
        Point3(
            radius * math.cos(latitude) * math.cos(longitude),
            radius * math.sin(latitude),
            radius * math.cos(latitude) * math.sin(longitude),
        ),
        spec.scale,
    )


def _dispersed_field(point: UVPoint, spec: ShapeSpec) -> Point3:
    longitude = math.tau * point.u + spec.phase
    latitude = math.pi * (point.v - 0.5)
    radius = 0.35 + spec.spread * _seed_unit(spec.seed, point.index, 0)
    jitter = spec.spread * 0.18
    return _scaled(
        Point3(
            radius * math.cos(latitude) * math.cos(longitude)
            + (_seed_unit(spec.seed, point.index, 1) * 2 - 1) * jitter,
            radius * math.sin(latitude)
            + (_seed_unit(spec.seed, point.index, 2) * 2 - 1) * jitter,
            radius * math.cos(latitude) * math.sin(longitude)
            + (_seed_unit(spec.seed, point.index, 3) * 2 - 1) * jitter,
        ),
        spec.scale,
    )


_Sampler = Callable[[UVPoint, ShapeSpec], Point3]

_SAMPLERS: dict[ShapeId, _Sampler] = {
    ShapeId.SPARSE_FIELD: _sparse_field,
    ShapeId.LISSAJOUS: _lissajous,
    ShapeId.MATRIX_FIELD: _matrix_field,
    ShapeId.WAVE_SURFACE: _wave_surface,
    ShapeId.TORUS: _torus,
    ShapeId.TWISTED_TORUS: _twisted_torus,
    ShapeId.TREFOIL_KNOT: _trefoil_knot,
    ShapeId.SUPERFORMULA: _superformula,
    ShapeId.SPHERICAL_LATTICE: _spherical_lattice,
    ShapeId.DISPERSED_FIELD: _dispersed_field,
}


def sample_shape(spec: ShapeSpec, domain: Sequence[UVPoint]) -> tuple[Point3, ...]:
    sampler = _SAMPLERS[spec.shape_id]
    sampled = tuple(sampler(point, spec) for point in domain)
    if not all(point.finite() for point in sampled):
        raise ValueError("built-in shape sampling produced a non-finite coordinate")
    return sampled


def easing_progress(progress: float, easing: EasingId) -> float:
    if not math.isfinite(progress) or not 0 <= progress <= 1:
        raise ValueError("morph progress must be finite and between zero and one")
    if progress in {0.0, 1.0}:
        return progress
    if easing is EasingId.SMOOTHSTEP:
        return progress * progress * (3 - 2 * progress)
    if easing is EasingId.SMOOTHERSTEP:
        return progress**3 * (progress * (progress * 6 - 15) + 10)
    if easing is EasingId.CUBIC:
        return 4 * progress**3 if progress < 0.5 else 1 - ((-2 * progress + 2) ** 3) / 2
    return (1 - math.cos(math.pi * progress)) / 2


def morph_points(
    start: Sequence[Point3],
    end: Sequence[Point3],
    progress: float,
    easing: EasingId = EasingId.SMOOTHERSTEP,
) -> tuple[Point3, ...]:
    if len(start) != len(end):
        raise ValueError("morph endpoints must use the same indexed point domain")
    eased = easing_progress(progress, easing)
    if eased == 0:
        return tuple(start)
    if eased == 1:
        return tuple(end)
    result = tuple(
        Point3(
            first.x + (second.x - first.x) * eased,
            first.y + (second.y - first.y) * eased,
            first.z + (second.z - first.z) * eased,
        )
        for first, second in zip(start, end, strict=True)
    )
    if not all(point.finite() for point in result):
        raise ValueError("morphing produced a non-finite coordinate")
    return result


def morph_shapes(
    start: ShapeSpec,
    end: ShapeSpec,
    domain: Sequence[UVPoint],
    progress: float,
    easing: EasingId = EasingId.SMOOTHERSTEP,
) -> tuple[Point3, ...]:
    return morph_points(
        sample_shape(start, domain),
        sample_shape(end, domain),
        progress,
        easing,
    )

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.renderers.wzhk_spectrum.generative import (
    AudioInputs,
    AudioMappingConfig,
    ChoreographySection,
    EasingId,
    GeometryChoreography,
    GeometryPreviewOverride,
    GeometryPreviewRequest,
    GeometrySectionId,
    GeometryTransition,
    IndexedDomainSpec,
    MusicalTime,
    MusicalTimeUnit,
    ShapeId,
    ShapeSpec,
    bars_to_seconds,
    beats_to_seconds,
    easing_progress,
    indexed_uv_domain,
    map_audio,
    morph_points,
    morph_shapes,
    propagation_wave,
    resolve_choreography,
    sample_shape,
    scattered_sections,
    seconds_per_bar,
    seconds_per_beat,
)

MASTER_DURATION = 196.619796


def _shape(shape_id: ShapeId, *, seed: int = 84291) -> ShapeSpec:
    return ShapeSpec(shape_id=shape_id, seed=seed)


def _transition(
    transition_id: str,
    section: GeometrySectionId,
    start_value: float,
    start_unit: MusicalTimeUnit,
    duration_value: float,
    duration_unit: MusicalTimeUnit,
    shape_a: ShapeSpec,
    shape_b: ShapeSpec,
) -> GeometryTransition:
    return GeometryTransition(
        transition_id=transition_id,
        section=section,
        start=MusicalTime(value=start_value, unit=start_unit),
        duration=MusicalTime(value=duration_value, unit=duration_unit),
        shape_a=shape_a,
        shape_b=shape_b,
        easing=EasingId.SMOOTHERSTEP,
    )


def _choreography() -> GeometryChoreography:
    sparse = _shape(ShapeId.SPARSE_FIELD)
    lissajous = _shape(ShapeId.LISSAJOUS)
    torus = _shape(ShapeId.TORUS)
    sphere = _shape(ShapeId.SPHERICAL_LATTICE)
    dispersed = _shape(ShapeId.DISPERSED_FIELD)
    return GeometryChoreography(
        bpm=120,
        beats_per_bar=4,
        grid_duration_seconds=192,
        master_duration_seconds=MASTER_DURATION,
        sections=scattered_sections(MASTER_DURATION),
        transitions=[
            _transition(
                "intro-formation",
                GeometrySectionId.INTRO,
                0,
                MusicalTimeUnit.BARS,
                4,
                MusicalTimeUnit.BARS,
                sparse,
                lissajous,
            ),
            _transition(
                "main-torus",
                GeometrySectionId.MAIN,
                32,
                MusicalTimeUnit.BARS,
                4,
                MusicalTimeUnit.BARS,
                lissajous,
                torus,
            ),
            _transition(
                "outro-simplify",
                GeometrySectionId.OUTRO,
                88,
                MusicalTimeUnit.BARS,
                4,
                MusicalTimeUnit.BARS,
                torus,
                sphere,
            ),
            _transition(
                "tail-dispersion",
                GeometrySectionId.POST_GRID_TAIL,
                192,
                MusicalTimeUnit.SECONDS,
                MASTER_DURATION - 192,
                MusicalTimeUnit.SECONDS,
                sphere,
                dispersed,
            ),
        ],
    )


def test_indexed_uv_domain_is_stable_and_shared_by_every_shape() -> None:
    spec = IndexedDomainSpec(point_count=97, columns=11)
    first = indexed_uv_domain(spec)
    second = indexed_uv_domain(spec)
    assert first == second
    assert len(first) == 97
    assert [point.index for point in first] == list(range(97))
    assert first[0].u == 0
    assert first[0].v == 0
    assert all(0 <= point.u <= 1 and 0 <= point.v <= 1 for point in first)


def test_all_trusted_shape_families_are_deterministic_and_finite() -> None:
    domain = indexed_uv_domain(IndexedDomainSpec(point_count=128))
    assert {shape.value for shape in ShapeId} == {
        "sparse-field",
        "lissajous",
        "matrix-field",
        "wave-surface",
        "torus",
        "twisted-torus",
        "trefoil-knot",
        "superformula",
        "spherical-lattice",
        "dispersed-field",
    }
    for shape_id in ShapeId:
        spec = _shape(shape_id)
        first = sample_shape(spec, domain)
        second = sample_shape(spec, domain)
        assert first == second
        assert len(first) == len(domain)
        assert all(point.finite() for point in first)


@pytest.mark.parametrize("shape_id", [ShapeId.SPARSE_FIELD, ShapeId.DISPERSED_FIELD])
def test_seed_controls_deterministic_field_variation(shape_id: ShapeId) -> None:
    domain = indexed_uv_domain(IndexedDomainSpec(point_count=64))
    first = sample_shape(_shape(shape_id, seed=11), domain)
    repeated = sample_shape(_shape(shape_id, seed=11), domain)
    changed = sample_shape(_shape(shape_id, seed=12), domain)
    assert first == repeated
    assert first != changed


@pytest.mark.parametrize("easing", list(EasingId))
def test_morph_endpoints_are_exact_and_intermediate_points_are_bounded(
    easing: EasingId,
) -> None:
    domain = indexed_uv_domain(IndexedDomainSpec(point_count=96))
    start = sample_shape(_shape(ShapeId.TORUS), domain)
    end = sample_shape(_shape(ShapeId.TREFOIL_KNOT), domain)
    assert morph_points(start, end, 0, easing) == start
    assert morph_points(start, end, 1, easing) == end
    assert easing_progress(0, easing) == 0
    assert easing_progress(1, easing) == 1
    middle = morph_points(start, end, 0.5, easing)
    assert all(point.finite() for point in middle)
    for first, current, last in zip(start, middle, end, strict=True):
        for low, value, high in (
            (first.x, current.x, last.x),
            (first.y, current.y, last.y),
            (first.z, current.z, last.z),
        ):
            assert min(low, high) <= value <= max(low, high)


def test_morph_shapes_uses_the_common_domain_and_rejects_bad_progress() -> None:
    domain = indexed_uv_domain(IndexedDomainSpec(point_count=64))
    first = _shape(ShapeId.MATRIX_FIELD)
    second = _shape(ShapeId.WAVE_SURFACE)
    assert morph_shapes(first, second, domain, 0) == sample_shape(first, domain)
    assert morph_shapes(first, second, domain, 1) == sample_shape(second, domain)
    with pytest.raises(ValueError):
        morph_shapes(first, second, domain, math.nan)


def test_musical_time_helpers_match_scattered_tempo() -> None:
    assert seconds_per_beat(120) == pytest.approx(0.5)
    assert seconds_per_bar(120, 4) == pytest.approx(2.0)
    assert beats_to_seconds(128, 120) == pytest.approx(64.0)
    assert bars_to_seconds(32, 120, 4) == pytest.approx(64.0)


def test_choreography_resolves_chained_sections_morphs_and_media_tail() -> None:
    choreography = _choreography()
    intro_start = resolve_choreography(choreography, 0)
    assert intro_start.section is GeometrySectionId.INTRO
    assert intro_start.raw_progress == 0
    assert intro_start.shape_a.shape_id is ShapeId.SPARSE_FIELD

    intro_hold = resolve_choreography(choreography, 20)
    assert intro_hold.transition_active is False
    assert intro_hold.shape_a.shape_id is ShapeId.LISSAJOUS
    assert intro_hold.shape_a == intro_hold.shape_b

    main = resolve_choreography(choreography, 64)
    assert main.section is GeometrySectionId.MAIN
    assert main.transition_id == "main-torus"
    assert main.raw_progress == 0

    outro = resolve_choreography(choreography, 176)
    assert outro.section is GeometrySectionId.OUTRO
    assert outro.transition_id == "outro-simplify"

    tail = resolve_choreography(choreography, 192)
    assert tail.section is GeometrySectionId.POST_GRID_TAIL
    assert tail.transition_id == "tail-dispersion"
    assert tail.raw_progress == 0

    eof = resolve_choreography(choreography, MASTER_DURATION)
    assert eof.section is GeometrySectionId.POST_GRID_TAIL
    assert eof.raw_progress == 1
    assert eof.shape_b.shape_id is ShapeId.DISPERSED_FIELD


def test_choreography_rejects_broken_chains_and_wrong_tail_boundary() -> None:
    choreography = _choreography()
    payload = choreography.model_dump(mode="json", by_alias=True)
    payload["transitions"][1]["shapeA"]["shapeId"] = ShapeId.SUPERFORMULA.value
    with pytest.raises(ValidationError):
        GeometryChoreography.model_validate(payload)

    sections = scattered_sections(MASTER_DURATION)
    wrong_tail = [section.model_copy() for section in sections]
    wrong_tail[-1] = ChoreographySection(
        section_id=GeometrySectionId.POST_GRID_TAIL,
        start_seconds=191,
        end_seconds=MASTER_DURATION,
    )
    with pytest.raises(ValidationError):
        GeometryChoreography(
            bpm=120,
            beats_per_bar=4,
            grid_duration_seconds=192,
            master_duration_seconds=MASTER_DURATION,
            sections=wrong_tail,
            transitions=choreography.transitions,
        )


def test_audio_mapping_is_typed_bounded_and_finite() -> None:
    response = map_audio(
        AudioInputs(low=1, mid=1, high=1, transient=1, energy=1),
        AudioMappingConfig(),
    )
    assert 0.5 <= response.global_scale <= 1.5
    assert 0 <= response.local_displacement <= 1
    assert 0 <= response.pixel_brightness <= 1
    assert 0 <= response.sparkle <= 1
    assert 0 <= response.propagation_impulse <= 1
    assert 0 <= response.movement_intensity <= 1
    assert 0 <= response.complexity <= 1
    assert 0 <= propagation_wave(topology_distance=0.5, age_seconds=0.5, impulse=1) <= 1
    with pytest.raises(ValidationError):
        AudioInputs(low=math.nan, mid=0, high=0, transient=0, energy=0)
    with pytest.raises(ValueError):
        propagation_wave(topology_distance=math.inf, age_seconds=0, impulse=1)


def test_preview_override_modes_are_typed_and_preview_only() -> None:
    shape = GeometryPreviewOverride(mode="shape", shape_a=_shape(ShapeId.TORUS))
    morph = GeometryPreviewOverride(
        mode="morph",
        shape_a=_shape(ShapeId.TORUS),
        shape_b=_shape(ShapeId.TREFOIL_KNOT),
        morph_progress=0.5,
    )
    section = GeometryPreviewOverride(mode="section", section="post-grid-tail")
    lab = GeometryPreviewOverride(
        mode="lab",
        shape_a=_shape(ShapeId.SUPERFORMULA),
        shape_b=_shape(ShapeId.SPHERICAL_LATTICE),
        morph_progress=0.25,
        point_count=2048,
        audio_mode="simulated",
    )
    for override in (shape, morph, section, lab):
        assert GeometryPreviewRequest(override=override).mode == "preview"

    with pytest.raises(ValidationError):
        GeometryPreviewOverride(mode="morph", shape_a=_shape(ShapeId.TORUS))
    with pytest.raises(ValidationError):
        GeometryPreviewRequest.model_validate(
            {"mode": "production", "override": shape.model_dump(mode="json", by_alias=True)}
        )
    with pytest.raises(ValidationError):
        ShapeSpec.model_validate({"shapeId": "torus", "rawShader": "unsafe"})

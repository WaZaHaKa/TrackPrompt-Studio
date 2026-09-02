from __future__ import annotations

import copy
import math
from typing import Any

import pytest
from pydantic import ValidationError

from app.renderers.wzhk_spectrum.generative.composition import (
    COMPOSITION_MASTER_DURATION_SECONDS,
    GeometryComposition,
    readability_at,
    resolve_composition_envelope,
)


def _composition_payload() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "revision": "scattered-geometry-first-3.7",
        "geometryCoverage": "full-frame",
        "production": {
            "logoVisible": True,
            "artistVisible": True,
            "titleVisible": True,
            "spectrumBarsVisible": False,
            "spectralRibbonVisible": False,
            "technicalMetadataVisible": False,
            "sectionLabelsVisible": False,
        },
        "readability": {
            "mode": "soft-ellipses",
            "minimumBrightness": 0.42,
            "haloSuppression": 0.72,
            "zones": [
                {"id": "logo", "center": [0.1073, 0.1722], "radius": [0.092, 0.145], "strength": 0.46},
                {"id": "identity", "center": [0.162, 0.402], "radius": [0.177, 0.095], "strength": 0.62},
            ],
        },
        "framing": {"center": [0.55, 0.54], "shapeScale": 1.0, "depthStrength": 0.75},
        "envelope": [
            {"timeSeconds": 0, "density": 0.40, "brightness": 0.55, "scale": 0.78, "deformation": 0.50},
            {"timeSeconds": 64, "density": 0.84, "brightness": 0.88, "scale": 1.0, "deformation": 1.0},
            {"timeSeconds": 120, "density": 1.0, "brightness": 1.0, "scale": 1.1, "deformation": 1.2},
            {"timeSeconds": 176, "density": 0.80, "brightness": 0.84, "scale": 1.0, "deformation": 0.9},
            {"timeSeconds": 192, "density": 0.28, "brightness": 0.42, "scale": 1.05, "deformation": 0.4},
            {
                "timeSeconds": COMPOSITION_MASTER_DURATION_SECONDS,
                "density": 0,
                "brightness": 0,
                "scale": 1.12,
                "deformation": 0,
            },
        ],
    }


def _replace(payload: dict[str, Any], path: tuple[str | int, ...], value: object) -> None:
    target: Any = payload
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value


def test_composition_retains_identity_and_forbids_legacy_production_elements() -> None:
    composition = GeometryComposition.model_validate(_composition_payload())
    assert composition.geometry_coverage == "full-frame"
    assert composition.production.logo_visible is True
    assert composition.production.artist_visible is True
    assert composition.production.title_visible is True
    assert composition.production.spectrum_bars_visible is False
    assert composition.production.spectral_ribbon_visible is False
    assert composition.production.technical_metadata_visible is False
    assert composition.production.section_labels_visible is False
    assert {zone.id for zone in composition.readability.zones} == {"logo", "identity"}


@pytest.mark.parametrize(
    "flag",
    [
        "logoVisible",
        "artistVisible",
        "titleVisible",
        "spectrumBarsVisible",
        "spectralRibbonVisible",
        "technicalMetadataVisible",
        "sectionLabelsVisible",
    ],
)
def test_production_visibility_flags_cannot_be_overridden(flag: str) -> None:
    payload = _composition_payload()
    payload["production"][flag] = not payload["production"][flag]
    with pytest.raises(ValidationError):
        GeometryComposition.model_validate(payload)


@pytest.mark.parametrize("value", [0, 1, "false", "true", None])
def test_production_flags_require_actual_booleans(value: object) -> None:
    payload = _composition_payload()
    payload["production"]["spectrumBarsVisible"] = value
    with pytest.raises(ValidationError):
        GeometryComposition.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schemaVersion",), "2.0.0"),
        (("revision",), "scattered-unversioned"),
        (("geometryCoverage",), "right-panel"),
        (("readability", "mode"), "rectangle"),
        (("readability", "minimumBrightness"), 0),
        (("readability", "minimumBrightness"), 0.249),
        (("readability", "minimumBrightness"), 1.001),
        (("readability", "haloSuppression"), -0.001),
        (("readability", "haloSuppression"), 1.001),
        (("readability", "zones", 0, "center"), [-0.001, 0.2]),
        (("readability", "zones", 0, "center"), [0.1, 1.001]),
        (("readability", "zones", 0, "radius"), [0, 0.1]),
        (("readability", "zones", 0, "radius"), [0.019, 0.1]),
        (("readability", "zones", 0, "radius"), [0.1, 0.251]),
        (("readability", "zones", 0, "strength"), -0.001),
        (("readability", "zones", 0, "strength"), 0.751),
        (("readability", "zones", 1, "id"), "logo"),
        (("framing", "center"), [1.001, 0.5]),
        (("framing", "shapeScale"), 0.249),
        (("framing", "shapeScale"), 2.001),
        (("framing", "depthStrength"), 1.001),
        (("envelope", 0, "timeSeconds"), 0.01),
        (("envelope", 1, "timeSeconds"), 0),
        (("envelope", 1, "timeSeconds"), 121),
        (("envelope", -1, "timeSeconds"), 192),
        (("envelope", -1, "timeSeconds"), 197),
        (("envelope", -1, "density"), 0.01),
        (("envelope", -1, "brightness"), 0.01),
        (("envelope", 1, "density"), -0.001),
        (("envelope", 1, "density"), 1.001),
        (("envelope", 1, "brightness"), 1.001),
        (("envelope", 1, "scale"), 0.249),
        (("envelope", 1, "scale"), 2.001),
        (("envelope", 1, "deformation"), -0.001),
        (("envelope", 1, "deformation"), 2.001),
        (("envelope", 1, "brightness"), True),
        (("readability", "minimumBrightness"), "0.42"),
    ],
)
def test_composition_rejects_invalid_contract_values(
    path: tuple[str | int, ...],
    value: object,
) -> None:
    payload = _composition_payload()
    _replace(payload, path, value)
    with pytest.raises(ValidationError):
        GeometryComposition.model_validate(payload)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize(
    "path",
    [
        ("readability", "minimumBrightness"),
        ("readability", "haloSuppression"),
        ("readability", "zones", 0, "center", 0),
        ("readability", "zones", 0, "radius", 1),
        ("readability", "zones", 0, "strength"),
        ("framing", "center", 1),
        ("framing", "shapeScale"),
        ("framing", "depthStrength"),
        ("envelope", 1, "timeSeconds"),
        ("envelope", 1, "density"),
        ("envelope", 1, "brightness"),
        ("envelope", 1, "scale"),
        ("envelope", 1, "deformation"),
    ],
)
def test_composition_rejects_nonfinite_numbers(
    path: tuple[str | int, ...],
    value: float,
) -> None:
    payload = _composition_payload()
    _replace(payload, path, value)
    with pytest.raises(ValidationError):
        GeometryComposition.model_validate(payload)


@pytest.mark.parametrize(
    "path",
    [(), ("production",), ("readability",), ("readability", "zones", 0), ("framing",), ("envelope", 1)],
)
def test_composition_rejects_unknown_fields_at_each_level(path: tuple[str | int, ...]) -> None:
    payload = _composition_payload()
    _replace(payload, (*path, "untrusted"), "not executable")
    with pytest.raises(ValidationError):
        GeometryComposition.model_validate(payload)


def test_composition_requires_bounded_zone_and_envelope_counts() -> None:
    for key, count in (("zones", 0), ("zones", 1), ("zones", 3), ("envelope", 1), ("envelope", 65)):
        payload = _composition_payload()
        if key == "zones":
            payload["readability"]["zones"] = [copy.deepcopy(payload["readability"]["zones"][0])] * count
        else:
            payload["envelope"] = [copy.deepcopy(payload["envelope"][0])] * count
        with pytest.raises(ValidationError):
            GeometryComposition.model_validate(payload)


def test_readability_is_localized_and_never_blacks_out_geometry() -> None:
    composition = GeometryComposition.model_validate(_composition_payload())
    mask = composition.readability
    for zone in mask.zones:
        protected = readability_at(composition, *zone.center)
        assert mask.minimum_brightness <= protected.intensity < 0.6
        assert 0 < protected.halo < protected.intensity
    assert readability_at(composition, 0.02, 0.88).intensity > 0.999
    assert readability_at(composition, 0.72, 0.40).intensity > 0.999
    assert readability_at(composition, 0.98, 0.98).halo > 0.999
    for column in range(21):
        for row in range(21):
            attenuation = readability_at(composition, column / 20, row / 20)
            assert mask.minimum_brightness <= attenuation.intensity <= 1
            assert 0 < attenuation.halo <= attenuation.intensity


def test_maximal_overlapping_masks_preserve_brightness_and_positive_halo() -> None:
    payload = _composition_payload()
    payload["readability"]["minimumBrightness"] = 0.25
    payload["readability"]["haloSuppression"] = 1.0
    for zone in payload["readability"]["zones"]:
        zone.update(center=[0.2, 0.2], radius=[0.25, 0.25], strength=0.75)
    composition = GeometryComposition.model_validate(payload)
    attenuation = readability_at(composition, 0.2, 0.2)
    assert attenuation.intensity == pytest.approx(0.25)
    assert attenuation.halo == pytest.approx(0.015625)


def test_readability_has_continuous_falloff_without_a_vertical_cut() -> None:
    payload = _composition_payload()
    for zone in payload["readability"]["zones"]:
        zone.update(center=[0.2, 0.2], radius=[0.1, 0.1], strength=0.25)
    composition = GeometryComposition.model_validate(payload)
    assert readability_at(composition, 0.2, 0.2).intensity == pytest.approx(0.75**2)
    left = readability_at(composition, 0.15, 0.2)
    right = readability_at(composition, 0.25, 0.2)
    assert left.intensity == pytest.approx(right.intensity, abs=1e-12)
    assert left.halo == pytest.approx(right.halo, abs=1e-12)
    samples = [readability_at(composition, x, 0.2).intensity for x in (0.2, 0.25, 0.3, 0.4, 0.8)]
    assert samples == sorted(samples)
    before = readability_at(composition, 0.3 - 1e-8, 0.2)
    after = readability_at(composition, 0.3 + 1e-8, 0.2)
    assert abs(before.intensity - after.intensity) < 1e-6
    assert abs(before.halo - after.halo) < 1e-6


@pytest.mark.parametrize("x,y", [(math.nan, 0.2), (0.1, math.inf), (-0.1, 0.5), (0.5, 1.1)])
def test_readability_rejects_invalid_canvas_coordinates(x: float, y: float) -> None:
    composition = GeometryComposition.model_validate(_composition_payload())
    with pytest.raises(ValueError, match="finite normalized"):
        readability_at(composition, x, y)


def test_envelope_is_exact_at_anchors_and_clamps_to_the_media_span() -> None:
    composition = GeometryComposition.model_validate(_composition_payload())
    for point in composition.envelope:
        resolved = resolve_composition_envelope(composition, point.time_seconds)
        assert resolved == point
        assert resolved is not point
    assert resolve_composition_envelope(composition, -1) == composition.envelope[0]
    assert resolve_composition_envelope(composition, 300) == composition.envelope[-1]
    assert resolve_composition_envelope(composition, COMPOSITION_MASTER_DURATION_SECONDS).brightness == 0


def test_envelope_uses_smootherstep_for_bounded_continuous_section_motion() -> None:
    composition = GeometryComposition.model_validate(_composition_payload())
    quarter = resolve_composition_envelope(composition, 16)
    progress = 0.103515625
    assert quarter.density == pytest.approx(0.40 + (0.84 - 0.40) * progress)
    assert quarter.brightness == pytest.approx(0.55 + (0.88 - 0.55) * progress)
    assert quarter.scale == pytest.approx(0.78 + (1.0 - 0.78) * progress)
    assert quarter.deformation == pytest.approx(0.50 + (1.0 - 0.50) * progress)
    for earlier, later in zip(composition.envelope, composition.envelope[1:], strict=False):
        for step in range(11):
            seconds = earlier.time_seconds + (later.time_seconds - earlier.time_seconds) * step / 10
            state = resolve_composition_envelope(composition, seconds)
            for name in ("density", "brightness", "scale", "deformation"):
                low, high = sorted((getattr(earlier, name), getattr(later, name)))
                assert low - 1e-12 <= getattr(state, name) <= high + 1e-12
    for boundary in (64.0, 176.0, 192.0):
        before = resolve_composition_envelope(composition, boundary - 1e-5)
        after = resolve_composition_envelope(composition, boundary + 1e-5)
        for name in ("density", "brightness", "scale", "deformation"):
            assert abs(getattr(before, name) - getattr(after, name)) < 1e-8


def test_sections_peak_in_main_and_geometry_resolves_through_the_intentional_tail() -> None:
    composition = GeometryComposition.model_validate(_composition_payload())
    intro = resolve_composition_envelope(composition, 10)
    main = resolve_composition_envelope(composition, 120)
    outro = resolve_composition_envelope(composition, 177)
    tail = resolve_composition_envelope(composition, 193)
    near_eof = resolve_composition_envelope(composition, COMPOSITION_MASTER_DURATION_SECONDS - 0.05)
    eof = resolve_composition_envelope(composition, COMPOSITION_MASTER_DURATION_SECONDS)
    assert main.density > intro.density
    assert main.brightness > intro.brightness
    assert main.deformation > intro.deformation
    assert main.density > outro.density > tail.density > near_eof.density > eof.density
    assert main.brightness > outro.brightness > tail.brightness > near_eof.brightness > eof.brightness
    assert eof.density == eof.brightness == 0
    assert eof.time_seconds == COMPOSITION_MASTER_DURATION_SECONDS


@pytest.mark.parametrize("seconds", [math.nan, math.inf, -math.inf])
def test_envelope_rejects_nonfinite_query_time(seconds: float) -> None:
    composition = GeometryComposition.model_validate(_composition_payload())
    with pytest.raises(ValueError, match="must be finite"):
        resolve_composition_envelope(composition, seconds)


def test_composition_helpers_are_deterministic_and_do_not_mutate_input() -> None:
    payload = _composition_payload()
    original_payload = copy.deepcopy(payload)
    first = GeometryComposition.model_validate(payload)
    first_bytes = first.model_dump_json(by_alias=True)
    second = GeometryComposition.model_validate_json(first_bytes)
    assert second.model_dump_json(by_alias=True) == first_bytes
    for x, y in ((0.1, 0.2), (0.16, 0.4), (0.75, 0.6)):
        assert readability_at(first, x, y) == readability_at(second, x, y)
    for seconds in (0, 10, 63, 65, 120, 175, 177, 191, 193, 196.119796, 196.619796):
        assert resolve_composition_envelope(first, seconds) == resolve_composition_envelope(second, seconds)
    assert first.model_dump_json(by_alias=True) == first_bytes
    assert payload == original_payload

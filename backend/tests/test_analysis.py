from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import app.analysis.pipeline as pipeline_module
from app.analysis.confidence import classify_key_confidence
from app.analysis.core import (
    _merge_chords,
    analyze_harmony,
    analyze_production,
    analyze_rhythm,
    analyze_structure,
    estimated_peak_analysis_bytes,
    feature,
    load_audio,
    signal_quality,
    spectral_features,
    with_failure_isolation,
)
from app.analysis.sanity import validate_analysis_result
from app.schemas import Confidence


def _loaded(fixture_dir: Path, name: str):
    audio = load_audio(str(fixture_dir / name))
    return audio, spectral_features(audio)


def test_very_short_audio_has_safe_spectral_path(tmp_path: Path) -> None:
    path = tmp_path / "tiny.wav"
    sf.write(path, np.asarray([0.1, -0.1, 0.05], dtype=np.float64), 22_050)
    audio = load_audio(str(path))
    spectral = spectral_features(audio)
    assert spectral.magnitude.shape[0] > 0
    assert np.isfinite(spectral.magnitude).all()


def test_silence_reports_insufficient_signal(fixture_dir: Path) -> None:
    audio = load_audio(str(fixture_dir / "silence.wav"))
    quality = signal_quality(audio)
    assert quality.sufficient_signal.value is False
    assert "Insufficient" in (quality.sufficient_signal.warning or "")


def test_120_bpm_is_primary_and_octaves_are_retained(fixture_dir: Path) -> None:
    audio, spectral = _loaded(fixture_dir, "120bpm_click.wav")
    rhythm = analyze_rhythm(audio, spectral)
    assert rhythm.bpm.value is not None
    assert abs(rhythm.bpm.value - 120) < 3
    alternatives = [float(item["bpm"]) for item in rhythm.bpm.alternatives]
    assert any(abs(value - 60) < 3 for value in alternatives)
    assert any(abs(value - 240) < 5 for value in alternatives)


def test_meter_accent_cycles_distinguish_three_and_four(fixture_dir: Path) -> None:
    three_audio, three_spectral = _loaded(fixture_dir, "three_four.wav")
    four_audio, four_spectral = _loaded(fixture_dir, "90bpm_accented_4_4.wav")
    assert analyze_rhythm(three_audio, three_spectral).meter.value == "3/4 (approximate)"
    assert analyze_rhythm(four_audio, four_spectral).meter.value == "4/4 (approximate)"


def test_a_minor_ranks_as_primary_key(fixture_dir: Path) -> None:
    audio, spectral = _loaded(fixture_dir, "a_minor_progression.wav")
    harmony = analyze_harmony(audio, spectral)
    assert harmony.key.value == "A"
    assert harmony.mode.value == "minor"


def test_chords_are_merged_without_adjacent_duplicates(fixture_dir: Path) -> None:
    audio, spectral = _loaded(fixture_dir, "c_major_progression.wav")
    chords = analyze_harmony(audio, spectral).chords.value or []
    assert chords
    assert all(left.chord != right.chord for left, right in zip(chords, chords[1:], strict=False))
    assert all(chord.end_seconds > chord.start_seconds for chord in chords)


def test_merge_chords_keeps_unknown_instead_of_forcing_label() -> None:
    merged = _merge_chords(
        [(None, 0.1, Confidence.UNKNOWN), (None, 0.1, Confidence.UNKNOWN)],
        np.asarray([0.0, 1.0]),
        2.0,
    )
    assert len(merged) == 1
    assert merged[0].chord is None


def test_arrangement_has_boundaries_and_repeated_groups(fixture_dir: Path) -> None:
    audio, spectral = _loaded(fixture_dir, "arrangement_intro_a_b_a_outro.wav")
    structure = analyze_structure(audio, spectral)
    assert len(structure.sections) >= 5
    groups = [section.repetition_group for section in structure.sections]
    assert any(group is not None and groups.count(group) >= 2 for group in groups)
    assert all(section.density is not None for section in structure.sections)
    assert all(section.harmony_summary for section in structure.sections)


@pytest.mark.parametrize("name", ["120bpm_click.wav", "133bpm_click.wav"])
def test_click_track_stays_one_neutral_section(fixture_dir: Path, name: str) -> None:
    audio, spectral = _loaded(fixture_dir, name)
    sections = analyze_structure(audio, spectral).sections
    assert len(sections) == 1
    assert all(section.repetition_group is None for section in sections)


def test_loudness_and_stereo_width_are_measured(fixture_dir: Path) -> None:
    wide_audio, wide_spectral = _loaded(fixture_dir, "stereo_wide.wav")
    production = analyze_production(wide_audio, wide_spectral)
    assert production.integrated_loudness_lufs.value is not None
    assert production.stereo_width.value is not None
    assert production.stereo_width.value > 0.2


def test_density_discriminates_tone_from_broadband_mixture(tmp_path: Path, fixture_dir: Path) -> None:
    tone_audio, tone_spectral = _loaded(fixture_dir, "mono.wav")
    tone_density = analyze_production(tone_audio, tone_spectral).mix_density.value
    rng = np.random.default_rng(7)
    noise_path = tmp_path / "dense.wav"
    sf.write(noise_path, rng.normal(0, 0.1, 22_050 * 3), 22_050)
    noise_audio = load_audio(str(noise_path))
    noise_density = analyze_production(noise_audio, spectral_features(noise_audio)).mix_density.value
    assert tone_density == "sparse"
    assert noise_density in {"moderate", "dense"}


def test_analyzer_failure_is_isolated() -> None:
    warnings: list[str] = []

    def broken() -> str:
        raise RuntimeError("sensitive details")

    result = with_failure_isolation("Example", broken, lambda warning: warning, warnings)
    assert result == "Example analysis was unavailable; other results are still usable."
    assert warnings == [result]


def test_confidence_has_no_manufactured_score() -> None:
    result = feature("approximate", Confidence.LOW, "heuristic only")
    assert result.score is None


def test_max_duration_memory_estimate_is_bounded_for_safe_defaults() -> None:
    assert estimated_peak_analysis_bytes(1200, 16_000, 2) < 700 * 1024 * 1024


def test_progress_replace_retries_transient_windows_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    progress = tmp_path / "progress.json"
    actual_replace = pipeline_module.os.replace
    calls = 0

    def transient_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("temporary sharing violation")
        actual_replace(source, destination)

    monkeypatch.setattr(pipeline_module.os, "replace", transient_replace)
    monkeypatch.setattr(pipeline_module.time, "sleep", lambda _seconds: None)
    pipeline_module._write_progress(progress, "analyzing_rhythm", "Working", 38)
    assert calls == 2
    assert '"progress":38' in progress.read_text(encoding="utf-8")


def test_worker_progress_map_is_monotonic_before_parent_finalization() -> None:
    values = list(pipeline_module.WORKER_STAGE_PROGRESS.values())
    assert values == sorted(values)
    assert values[-1] < 92


def test_corrupt_deep_stem_falls_back_without_losing_fast_analysis(
    click_analysis,
    fixture_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .helpers import settings_for

    corrupt = tmp_path / "corrupt-stem.wav"
    corrupt.write_bytes(b"not a wav")
    monkeypatch.setattr(pipeline_module, "demucs_ready", lambda _settings: True)
    monkeypatch.setattr(
        pipeline_module,
        "run_demucs",
        lambda *_args, **_kwargs: {
            name: corrupt for name in ("vocals", "drums", "bass", "other")
        },
    )
    serialized = pipeline_module.analyze_audio(
        str(fixture_dir / "120bpm_click.wav"),
        click_analysis.file.model_dump(mode="json", by_alias=True),
        click_analysis.job_id,
        "deep",
        str(tmp_path / "progress.json"),
        str(tmp_path / "cancel.flag"),
        settings_for(tmp_path / "data"),
    )
    result = click_analysis.__class__.model_validate_json(serialized)
    assert result.effective_mode == "fast"
    assert result.rhythm.bpm.value is not None
    assert any("Deep adapter failed safely" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("name", "leading_min", "leading_max"),
    [
        ("silence_then_music.wav", 4.8, 5.1),
        ("quiet_intro_loud_music.wav", 0.0, 0.2),
        ("gradual_fade_in.wav", 0.0, 1.0),
        ("mastered_electronic.wav", 0.0, 0.2),
        ("sparse_ambient.wav", 0.0, 0.5),
        ("isolated_clicks_then_music.wav", 5.7, 6.2),
        ("stereo_one_channel.wav", 0.0, 0.2),
    ],
)
def test_edge_activity_regressions(
    fixture_dir: Path,
    name: str,
    leading_min: float,
    leading_max: float,
) -> None:
    quality = signal_quality(load_audio(str(fixture_dir / name)))
    assert quality.activity_threshold_dbfs is not None
    assert -70.0 <= float(quality.activity_threshold_dbfs.value) <= -50.0
    assert leading_min <= float(quality.leading_silence_seconds.value) <= leading_max
    assert float(quality.leading_silence_seconds.value) + float(
        quality.trailing_silence_seconds.value
    ) <= load_audio(str(fixture_dir / name)).duration + 0.05


def test_fade_out_is_not_reported_as_a_long_silent_tail(fixture_dir: Path) -> None:
    quality = signal_quality(load_audio(str(fixture_dir / "fade_out.wav")))
    assert float(quality.trailing_silence_seconds.value) < 0.5


def test_sample_peak_invariants(tmp_path: Path, fixture_dir: Path) -> None:
    sample_rate = 16_000
    time = np.arange(sample_rate * 2) / sample_rate
    minus_six = np.sin(2 * np.pi * 440 * time) * (10 ** (-6 / 20))
    minus_six_path = tmp_path / "minus-six.wav"
    full_scale_path = tmp_path / "full-scale.wav"
    stereo_path = tmp_path / "stereo.wav"
    sf.write(minus_six_path, minus_six, sample_rate, subtype="PCM_16")
    sf.write(full_scale_path, np.sin(2 * np.pi * 440 * time), sample_rate, subtype="PCM_16")
    sf.write(stereo_path, np.column_stack((minus_six, minus_six)), sample_rate, subtype="PCM_16")
    minus_six_audio = load_audio(str(minus_six_path))
    minus_six_peak = analyze_production(
        minus_six_audio,
        spectral_features(minus_six_audio),
    ).peak_dbfs.value
    full_scale_audio = load_audio(str(full_scale_path))
    full_scale_peak = analyze_production(
        full_scale_audio,
        spectral_features(full_scale_audio),
    ).peak_dbfs.value
    stereo_audio = load_audio(str(stereo_path))
    stereo_peak = analyze_production(stereo_audio, spectral_features(stereo_audio)).peak_dbfs.value
    assert minus_six_peak == pytest.approx(-6.0, abs=0.15)
    assert full_scale_peak == pytest.approx(0.0, abs=0.05)
    assert stereo_peak == pytest.approx(minus_six_peak, abs=0.02)
    assert signal_quality(load_audio(str(fixture_dir / "clipped.wav"))).clipping.value is True


def test_out_of_range_float_decode_is_diagnosed_not_clamped_silently(tmp_path: Path) -> None:
    path = tmp_path / "out-of-range.wav"
    sf.write(path, np.asarray([0.0, 1.2, -1.1, 0.0]), 16_000, subtype="FLOAT")
    audio = load_audio(str(path))
    production = analyze_production(audio, spectral_features(audio))
    assert audio.normalization_violation is True
    assert production.peak_dbfs.value is None
    assert "withheld" in (production.peak_dbfs.warning or "")
    assert signal_quality(audio).clipping.value is True


@pytest.mark.parametrize("name", ["120bpm_click.wav", "133bpm_click.wav", "dense_hihat.wav"])
def test_beat_grid_matches_selected_bpm_and_onsets_are_separate(
    fixture_dir: Path,
    name: str,
) -> None:
    audio, spectral = _loaded(fixture_dir, name)
    rhythm = analyze_rhythm(audio, spectral)
    assert rhythm.bpm.value is not None
    beats = np.asarray(rhythm.beat_timestamps.value)
    onsets = np.asarray(rhythm.onset_timestamps.value if rhythm.onset_timestamps else [])
    assert beats.size >= 4
    assert np.median(np.diff(beats)) == pytest.approx(60.0 / float(rhythm.bpm.value), rel=0.03)
    assert rhythm.beat_grid_alignment is not None
    assert rhythm.beat_grid_alignment.value is not None
    if name == "dense_hihat.wav":
        assert onsets.size > beats.size


def test_difficult_rhythm_confidence_and_meter_remain_conservative(fixture_dir: Path) -> None:
    noise_audio, noise_spectral = _loaded(fixture_dir, "atonal_noise.wav")
    noise = analyze_rhythm(noise_audio, noise_spectral)
    assert noise.bpm.confidence == Confidence.LOW
    change_audio, change_spectral = _loaded(fixture_dir, "tempo_change.wav")
    assert analyze_rhythm(change_audio, change_spectral).bpm.confidence != Confidence.HIGH
    six_audio, six_spectral = _loaded(fixture_dir, "six_eight.wav")
    assert analyze_rhythm(six_audio, six_spectral).meter.value == "unknown"


def test_near_tied_key_policy_is_ambiguous() -> None:
    decision = classify_key_confidence(
        best_fit=0.456,
        runner_up_margin=0.001,
        temporal_consistency=0.9,
        tonal_concentration=0.2,
        usable_seconds=200.0,
    )
    assert decision.confidence == Confidence.LOW
    assert decision.ambiguous is True


def test_continuous_loop_does_not_fragment_into_short_sections(fixture_dir: Path) -> None:
    audio, spectral = _loaded(fixture_dir, "continuous_loop.wav")
    structure = analyze_structure(audio, spectral)
    assert len(structure.sections) <= 2


def test_successful_deep_analysis_populates_sections_and_cleans_stems(
    click_analysis,
    fixture_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .helpers import settings_for

    fixture_source = fixture_dir / "arrangement_intro_a_b_a_outro.wav"
    decoded_path = tmp_path / "decoded.wav"
    decoded_samples, decoded_rate = sf.read(fixture_source, always_2d=False)
    sf.write(decoded_path, decoded_samples, decoded_rate, subtype="FLOAT")

    def fake_demucs(
        _source: Path,
        output: Path,
        _settings,
        **_kwargs,
    ) -> dict[str, Path]:
        output.mkdir(parents=True, exist_ok=True)
        source, sample_rate = sf.read(decoded_path, always_2d=False)
        midpoint = source.shape[0] // 2
        stems = {
            "vocals": np.concatenate((source[:midpoint] * 0.35, np.zeros(source.shape[0] - midpoint))),
            "drums": source * 0.7,
            "bass": source * 0.2,
            "other": np.zeros_like(source),
        }
        paths: dict[str, Path] = {}
        for name, values in stems.items():
            path = output / f"{name}.wav"
            sf.write(path, values, sample_rate, subtype="FLOAT")
            paths[name] = path
        return paths

    monkeypatch.setattr(pipeline_module, "demucs_ready", lambda _settings: True)
    monkeypatch.setattr(pipeline_module, "run_demucs", fake_demucs)
    serialized = pipeline_module.analyze_audio(
        str(decoded_path),
        click_analysis.file.model_copy(
            update={"duration_seconds": 20.0}
        ).model_dump(mode="json", by_alias=True),
        click_analysis.job_id,
        "deep",
        str(tmp_path / "progress.json"),
        str(tmp_path / "cancel.flag"),
        settings_for(tmp_path / "data"),
    )
    result = click_analysis.__class__.model_validate_json(serialized)
    assert result.effective_mode == "deep"
    assert result.deep_diagnostics is not None
    assert result.deep_diagnostics.adapter_id == "demucs-four-stem"
    assert all(section.deep_evidence is not None for section in result.structure.sections)
    activity = [section.vocal_activity for section in result.structure.sections]
    assert any(value in {"present", "prominent"} for value in activity)
    assert "inactive" in activity
    assert all("no enabled vocal separator" not in (value or "") for value in activity)
    assert not (tmp_path / "stems").exists()


def test_sanity_layer_omits_contradictory_peak_and_beat_grid(click_analysis) -> None:
    analysis = click_analysis.model_copy(deep=True)
    analysis.production.peak_dbfs.value = 1.58
    analysis.rhythm.bpm.value = 120.0
    analysis.rhythm.beat_timestamps.value = [0.0, 0.2, 0.4, 0.6]
    analysis.structure.sections[0].energy = float("nan")
    validated = validate_analysis_result(analysis)
    assert validated.production.peak_dbfs.value is None
    assert validated.rhythm.beat_timestamps.value == []
    assert any("normalized_peak_not_positive" in warning for warning in validated.warnings)
    assert any("beat_grid_matches_bpm" in warning for warning in validated.warnings)
    assert "NaN" not in validated.model_dump_json()

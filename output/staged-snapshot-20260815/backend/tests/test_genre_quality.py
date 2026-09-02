from __future__ import annotations

import runpy
from pathlib import Path

import numpy as np
import pytest

from app.analysis.core import load_audio
from app.schemas import Confidence, Section, SectionStemEvidence
from app.tagging.music import (
    WINDOW_WEIGHTING_METHOD,
    TransformersClapMusicTagger,
    _aggregate_rows,
    _confidence,
    _family_ids_for_subgenre_stage,
    _measured_support_adjustments,
    _profile_windows,
    _rank_items,
    _select_windows,
    _subgenre_support_adjustments,
    _usable_audio_bounds,
)
from app.taxonomies import load_music_style_taxonomy

from .helpers import settings_for


def test_taxonomy_uses_natural_language_description_ensembles() -> None:
    taxonomy = load_music_style_taxonomy()
    assert taxonomy.taxonomy_version == "2.0.0"
    assert [item.id for item in taxonomy.broad_genres] == [
        "electronic-dance",
        "electronic-non-dance",
        "hip-hop",
        "r-and-b-soul",
        "pop",
        "rock-metal",
        "jazz-blues",
        "acoustic-folk",
        "orchestral-classical",
        "cinematic",
        "reggae-dub",
        "experimental",
    ]
    entries = [*taxonomy.broad_genres, *taxonomy.subgenres]
    assert all(len(item.clap_descriptions) >= 2 for item in entries)
    assert all(
        len(description.split()) >= 6
        for item in entries
        for description in item.clap_descriptions
    )
    assert all(
        "weak" in item.tempo_prior.note.casefold()
        for item in entries
        if item.tempo_prior is not None
    )


def test_deterministic_genre_fixtures_cover_requested_families_without_recorded_audio() -> None:
    generator_path = Path(__file__).resolve().parents[2] / "tools" / "generate_test_audio.py"
    namespace = runpy.run_path(str(generator_path))
    generate_signals = namespace["genre_regression_signals"]
    first = generate_signals(8.0)
    second = generate_signals(8.0)
    assert set(first) == {
        "genre_techno_four_floor.wav",
        "genre_minimal_techno.wav",
        "genre_dub_techno.wav",
        "genre_progressive_house.wav",
        "genre_breakbeat.wav",
        "genre_hip_hop.wav",
        "genre_r_and_b.wav",
        "genre_rock.wav",
        "genre_ambient_electronic.wav",
    }
    assert all(np.array_equal(first[name], second[name]) for name in first)
    assert np.mean(np.abs(np.diff(first["genre_techno_four_floor.wav"]))) > (
        np.mean(np.abs(np.diff(first["genre_ambient_electronic.wav"]))) * 2.0
    )
    assert not np.allclose(first["genre_hip_hop.wav"], first["genre_r_and_b.wav"])


def test_electronic_dance_subgenres_are_family_gated_and_descriptive_tags_are_separate() -> None:
    taxonomy = load_music_style_taxonomy()
    dance_ids = {
        item.id for item in taxonomy.subgenres if item.parent == "electronic-dance"
    }
    assert {
        "techno",
        "minimal-techno",
        "deep-techno",
        "dub-techno",
        "melodic-techno",
        "progressive-house",
        "deep-house",
        "electro-house",
        "trance",
        "breakbeat",
        "drum-and-bass",
        "ambient-techno",
        "industrial-techno",
        "vocal-techno",
    } <= dance_ids
    assert {item.id for item in taxonomy.descriptive_tags} >= {
        "club-driven",
        "hypnotic",
        "percussive",
        "vocal-led",
        "synthetic",
        "progressive-arrangement",
    }
    assert dance_ids.isdisjoint({item.id for item in taxonomy.descriptive_tags})


def test_description_ensemble_uses_robust_median(monkeypatch: pytest.MonkeyPatch) -> None:
    taxonomy = load_music_style_taxonomy()
    entry = next(item for item in taxonomy.broad_genres if item.id == "electronic-dance")
    adapter = object.__new__(TransformersClapMusicTagger)

    def fake_similarities(_samples, labels):
        values = [0.1, 0.3, 0.9]
        return {label: values[index] for index, (label, _description) in enumerate(labels)}

    monkeypatch.setattr(adapter, "_similarities", fake_similarities)
    scores = adapter._entry_similarities(np.zeros(32), [entry])
    assert scores[entry.id] == pytest.approx(0.3)


def test_subgenre_stage_only_opens_close_broad_families() -> None:
    taxonomy = load_music_style_taxonomy()
    scores = {item.id: 0.02 for item in taxonomy.broad_genres}
    scores.update(
        {
            "electronic-dance": 0.31,
            "electronic-non-dance": 0.286,
            "r-and-b-soul": 0.22,
            "experimental": 0.18,
        }
    )
    ranked = _rank_items(taxonomy.broad_genres, scores)
    parent_ids = _family_ids_for_subgenre_stage(ranked, scores, {})
    assert parent_ids == ("electronic-dance", "electronic-non-dance")
    evaluated = {item.parent for item in taxonomy.subgenres if item.parent in parent_ids}
    assert evaluated == set(parent_ids)
    assert "r-and-b-soul" not in evaluated


def test_staged_classifier_keeps_genres_and_descriptive_tags_separate(
    click_analysis,
    fixture_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = click_analysis.model_copy(deep=True)
    analysis.rhythm.tempo_stability.value = "stable"
    analysis.rhythm.rhythmic_regularity.value = "steady"
    analysis.rhythm.percussiveness.value = "pronounced"
    analysis.rhythm.beat_grid_alignment.value = 0.7
    adapter = TransformersClapMusicTagger(settings_for(tmp_path / "data"))

    def fake_entry_similarities(_samples, entries):
        ids = {entry.id for entry in entries}
        if "club-driven" in ids:
            preferred = {
                "club-driven": 0.42,
                "synthetic": 0.39,
                "percussive": 0.37,
                "repetitive": 0.35,
                "hypnotic": 0.34,
                "vocal-led": 0.12,
            }
        elif "electronic-dance" in ids:
            preferred = {
                "experimental": 0.31,
                "r-and-b-soul": 0.305,
                "electronic-dance": 0.3,
                "electronic-non-dance": 0.24,
            }
        else:
            preferred = {
                "techno": 0.29,
                "progressive-house": 0.282,
                "vocal-techno": 0.21,
                "contemporary-r-and-b": 0.2,
            }
        return {entry.id: preferred.get(entry.id, 0.08) for entry in entries}

    monkeypatch.setattr(adapter, "_entry_similarities", fake_entry_similarities)
    result = adapter.analyze_windows(
        fixture_dir / "120bpm_click.wav",
        analysis,
    )
    assert result.broad_candidates[0].id == "electronic-dance"
    assert result.broad_candidates[0].similarity == pytest.approx(0.3)
    assert result.subgenre_candidates[0].id == "techno"
    assert result.descriptive_tags[0].id == "club-driven"
    assert result.descriptive_tags[0].id not in {
        item.id for item in result.subgenre_candidates
    }
    assert result.broad_candidates[0].id != "experimental"
    assert result.window_evidence
    assert all(0.0 <= window.weight <= 1.0 for window in result.window_evidence)
    assert all(0.0 <= window.representativeness <= 1.0 for window in result.window_evidence)
    assert all(isinstance(window.percussion_dominant, bool) for window in result.window_evidence)
    assert any(window.section_ids for window in result.window_evidence)
    assert "cosine similarity" in result.method
    assert all("probabilit" not in item for item in result.window_evidence[0].top_labels)


def test_vocal_outro_is_retained_but_cannot_override_central_groove(
    click_analysis,
    fixture_dir: Path,
) -> None:
    analysis = click_analysis.model_copy(deep=True)
    analysis.structure.sections = [
        Section(
            id="section-1",
            neutral_label="A",
            start_seconds=0.0,
            end_seconds=4.0,
            confidence=Confidence.MEDIUM,
            energy=0.55,
            deep_evidence=SectionStemEvidence(
                relative_rms={"drums": 0.58, "bass": 0.42, "vocals": 0.08},
                activity={"drums": "prominent", "bass": "present", "vocals": "present"},
                method="synthetic section evidence",
                confidence=Confidence.MEDIUM,
            ),
        ),
        Section(
            id="section-2",
            neutral_label="B",
            start_seconds=4.0,
            end_seconds=8.0,
            confidence=Confidence.MEDIUM,
            repetition_group="R1",
            energy=0.8,
            deep_evidence=SectionStemEvidence(
                relative_rms={"drums": 0.64, "bass": 0.5, "vocals": 0.1},
                activity={"drums": "prominent", "bass": "prominent", "vocals": "present"},
                method="synthetic section evidence",
                confidence=Confidence.MEDIUM,
            ),
        ),
        Section(
            id="section-3",
            neutral_label="outro",
            start_seconds=8.0,
            end_seconds=12.0,
            confidence=Confidence.MEDIUM,
            repetition_group="R1",
            energy=0.4,
            deep_evidence=SectionStemEvidence(
                relative_rms={"drums": 0.04, "bass": 0.05, "vocals": 0.68},
                activity={"drums": "inactive", "bass": "present", "vocals": "prominent"},
                method="synthetic section evidence",
                confidence=Confidence.MEDIUM,
            ),
        ),
    ]
    windows = _select_windows(analysis, window_seconds=3.0)
    profiles = _profile_windows(load_audio(str(fixture_dir / "120bpm_click.wav")), windows)
    outro = next(profile for profile in profiles if profile.window.kind == "outro")
    middle = next(profile for profile in profiles if profile.window.kind == "middle")
    assert outro.window.vocal_dominant
    assert middle.window.percussion_dominant
    assert outro.weight < middle.weight * 0.35

    rows = [
        (
            {"electronic-dance": 0.05, "r-and-b-soul": 0.62}
            if profile.window.kind == "outro"
            else {"electronic-dance": 0.36, "r-and-b-soul": 0.14}
        )
        for profile in profiles
    ]
    aggregated = _aggregate_rows(rows, [profile.weight for profile in profiles])
    assert aggregated["electronic-dance"] > aggregated["r-and-b-soul"]


def test_club_evidence_supports_electronic_dance_and_controls_experimental(
    click_analysis,
) -> None:
    analysis = click_analysis.model_copy(deep=True)
    analysis.rhythm.tempo_stability.value = "stable"
    analysis.rhythm.rhythmic_regularity.value = "steady"
    analysis.rhythm.percussiveness.value = "pronounced"
    analysis.rhythm.beat_grid_alignment.value = 0.72
    analysis.rhythm.bpm.value = 133.0
    tag_scores = {
        "club-driven": 0.38,
        "synthetic": 0.36,
        "percussive": 0.34,
        "repetitive": 0.31,
        "hypnotic": 0.3,
        "dense": 0.2,
        "vocal-led": 0.1,
    }
    adjustments, reasons = _measured_support_adjustments(analysis, tag_scores)
    raw = {
        "electronic-dance": 0.205,
        "r-and-b-soul": 0.212,
        "experimental": 0.216,
    }
    assert raw["experimental"] > raw["electronic-dance"]
    assert (
        raw["electronic-dance"] + adjustments["electronic-dance"]
        > raw["experimental"] + adjustments["experimental"]
    )
    assert raw["electronic-dance"] + adjustments["electronic-dance"] > raw["r-and-b-soul"]
    assert any("tempo supplied only weak support" in reason for reason in reasons)


def test_tempo_alone_does_not_force_r_and_b_into_techno(click_analysis) -> None:
    analysis = click_analysis.model_copy(deep=True)
    analysis.rhythm.bpm.value = 133.0
    analysis.rhythm.tempo_stability.value = "stable"
    analysis.rhythm.percussiveness.value = "moderate"
    tag_scores = {
        "vocal-led": 0.4,
        "melodic": 0.34,
        "organic": 0.31,
        "spacious": 0.29,
        "sparse": 0.27,
        "dark": 0.2,
        "club-driven": 0.1,
        "percussive": 0.09,
    }
    adjustments, _reasons = _measured_support_adjustments(analysis, tag_scores)
    raw = {"r-and-b-soul": 0.24, "electronic-dance": 0.235}
    assert raw["r-and-b-soul"] + adjustments["r-and-b-soul"] > (
        raw["electronic-dance"] + adjustments.get("electronic-dance", 0.0)
    )


def test_techno_family_ranking_uses_clap_first_and_weak_support_second(
    click_analysis,
) -> None:
    taxonomy = load_music_style_taxonomy()
    analysis = click_analysis.model_copy(deep=True)
    analysis.rhythm.bpm.value = 132.0
    analysis.rhythm.tempo_stability.value = "stable"
    analysis.rhythm.rhythmic_regularity.value = "steady"
    analysis.rhythm.percussiveness.value = "pronounced"
    tags = {
        "club-driven": 0.4,
        "percussive": 0.37,
        "repetitive": 0.35,
        "hypnotic": 0.34,
        "synthetic": 0.33,
        "dark": 0.2,
    }
    items = [item for item in taxonomy.subgenres if item.parent == "electronic-dance"]
    raw = {item.id: 0.1 for item in items}
    raw.update({"techno": 0.225, "progressive-house": 0.219, "vocal-techno": 0.17})
    adjustments = _subgenre_support_adjustments(analysis, items, tags)
    ranked = _rank_items(items, raw, adjustments)
    assert ranked[0].id == "techno"
    assert adjustments["techno"] <= 0.012


def test_close_candidates_remain_low_confidence_and_ambiguous() -> None:
    confidence = _confidence(
        broad_margin=0.006,
        subgenre_margin=0.004,
        agreement=0.48,
        duration=45.0,
        hierarchy_consistency=1.0,
        alternate_stability=0.6,
        supporting_evidence_compatible=True,
    )
    assert confidence == Confidence.LOW


def test_genre_confidence_uses_non_silent_duration(click_analysis) -> None:
    analysis = click_analysis.model_copy(deep=True)
    analysis.file.duration_seconds = 60.0
    analysis.signal_quality.leading_silence_seconds.value = 28.0
    analysis.signal_quality.trailing_silence_seconds.value = 28.0
    usable_start, usable_end = _usable_audio_bounds(analysis)

    confidence = _confidence(
        broad_margin=0.2,
        subgenre_margin=0.2,
        agreement=1.0,
        duration=usable_end - usable_start,
        hierarchy_consistency=1.0,
        alternate_stability=1.0,
        supporting_evidence_compatible=True,
    )

    assert usable_end - usable_start == pytest.approx(4.0)
    assert confidence == Confidence.LOW


def test_weighting_method_is_explicit_and_similarity_is_not_probability() -> None:
    lowered = WINDOW_WEIGHTING_METHOD.casefold()
    assert "vocal-dominant" in lowered
    assert "outro" in lowered
    result = _aggregate_rows(
        [{"techno": 0.2, "r-and-b-soul": 0.1}, {"techno": 0.3, "r-and-b-soul": 0.2}],
        [0.8, 0.2],
    )
    assert result["techno"] == pytest.approx(0.214)
    assert -1.0 <= result["techno"] <= 1.0

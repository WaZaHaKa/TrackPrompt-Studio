from __future__ import annotations

import runpy
from pathlib import Path

import numpy as np
import soundfile as sf

from app.analysis.core import feature, load_audio
from app.analysis.layered_views import (
    analyze_vocal_delivery_view,
    create_temporary_accompaniment_view,
)
from app.prompting.composer import compose_prompt
from app.prompting.engine import generate_prompt_package
from app.prompting.local_writer import FakePromptWriterAdapter
from app.schemas import (
    Confidence,
    GenreAnalysis,
    GenreCandidate,
    GenreInterpretationMode,
    GenreWindowEvidence,
    LyricsSegment,
    PrivateLyricsTranscript,
    PromptEngineMode,
    PromptPreferences,
    Section,
    SectionStemEvidence,
)
from app.tagging.layers import build_layered_genre_analysis

from .helpers import settings_for


def _genre(
    broad: list[tuple[str, str]],
    subgenres: list[tuple[str, str]],
    windows: list[GenreWindowEvidence],
    *,
    ambiguity: str | None = None,
) -> GenreAnalysis:
    return GenreAnalysis(
        broad_candidates=[
            GenreCandidate(
                id=candidate_id,
                label=label,
                canonical_label=label,
                similarity=0.34 - index * 0.01,
                confidence=Confidence.MEDIUM,
            )
            for index, (candidate_id, label) in enumerate(broad)
        ],
        subgenre_candidates=[
            GenreCandidate(
                id=candidate_id,
                label=label,
                canonical_label=label,
                parent=broad[0][0],
                similarity=0.3 - index * 0.01,
                confidence=Confidence.MEDIUM,
            )
            for index, (candidate_id, label) in enumerate(subgenres)
        ],
        window_evidence=windows,
        confidence=Confidence.LOW if ambiguity else Confidence.MEDIUM,
        ambiguity=ambiguity,
        method="synthetic audio-text ranking, not probability",
        model_id="synthetic-layer-test",
        taxonomy_version="2.0.0",
        selected_device="cpu",
    )


def _window(
    identifier: str,
    start: float,
    end: float,
    labels: list[str],
    section_id: str,
    *,
    vocal_dominant: bool = False,
) -> GenreWindowEvidence:
    return GenreWindowEvidence(
        id=identifier,
        kind="middle",
        start_seconds=start,
        end_seconds=end,
        top_labels=labels,
        similarities={label: 0.3 for label in labels},
        section_ids=[section_id],
        vocal_dominant=vocal_dominant,
    )


def _layered_analysis(click_analysis):
    analysis = click_analysis.model_copy(deep=True)
    analysis.structure.sections = [
        Section(
            id="section-1",
            neutral_label="A",
            inferred_label="intro",
            start_seconds=0.0,
            end_seconds=4.0,
            confidence=Confidence.MEDIUM,
            energy=0.4,
            deep_evidence=SectionStemEvidence(
                relative_rms={"drums": 0.6, "bass": 0.5, "other": 0.3, "vocals": 0.02},
                activity={"drums": "prominent", "bass": "prominent", "other": "present", "vocals": "inactive"},
                method="synthetic private stems",
                confidence=Confidence.MEDIUM,
            ),
        ),
        Section(
            id="section-2",
            neutral_label="B",
            start_seconds=4.0,
            end_seconds=8.0,
            confidence=Confidence.MEDIUM,
            energy=0.9,
            deep_evidence=SectionStemEvidence(
                relative_rms={"drums": 0.64, "bass": 0.52, "other": 0.28, "vocals": 0.24},
                activity={"drums": "prominent", "bass": "prominent", "other": "present", "vocals": "present"},
                method="synthetic private stems",
                confidence=Confidence.MEDIUM,
            ),
        ),
        Section(
            id="section-3",
            neutral_label="C",
            inferred_label="outro",
            start_seconds=8.0,
            end_seconds=12.0,
            confidence=Confidence.MEDIUM,
            energy=0.35,
            deep_evidence=SectionStemEvidence(
                relative_rms={"drums": 0.03, "bass": 0.02, "other": 0.04, "vocals": 0.72},
                activity={"drums": "inactive", "bass": "inactive", "other": "inactive", "vocals": "prominent"},
                method="synthetic private stems",
                confidence=Confidence.MEDIUM,
            ),
        ),
    ]
    analysis.vocals.delivery = feature(
        ["spoken-rhythmic"],
        Confidence.MEDIUM,
        "synthetic private vocal-stem acoustics without transcript",
    )
    analysis.vocals.phrasing = feature(
        ["short rhythmic phrases", "hook-like repetition"],
        Confidence.MEDIUM,
        "synthetic private vocal-stem acoustics without transcript",
    )
    full = _genre(
        [("hip-hop", "hip-hop"), ("electronic-dance", "electronic dance")],
        [("rap", "rap")],
        [
            _window("full-a", 0.0, 4.0, ["electronic dance"], "section-1"),
            _window("full-b", 4.0, 8.0, ["hip-hop", "electronic dance"], "section-2", vocal_dominant=True),
            _window("full-c", 8.0, 12.0, ["hip-hop", "pop"], "section-3", vocal_dominant=True),
        ],
        ambiguity="The full mix contains close electronic and hip-hop evidence.",
    )
    production = _genre(
        [("electronic-dance", "electronic dance"), ("hip-hop", "hip-hop")],
        [("techno", "techno"), ("progressive-house", "progressive house"), ("breakbeat", "breakbeat")],
        [
            _window("prod-a", 0.0, 4.0, ["electronic dance"], "section-1"),
            _window("prod-b", 4.0, 8.0, ["electronic dance"], "section-2"),
            _window("prod-c", 8.0, 12.0, ["electronic dance"], "section-3"),
        ],
        ambiguity="Techno and progressive house remain close production alternatives.",
    )
    return analysis, build_layered_genre_analysis(full, analysis, production)


def test_six_hybrid_fixtures_are_deterministic_and_legally_safe() -> None:
    generator = Path(__file__).resolve().parents[2] / "tools" / "generate_test_audio.py"
    namespace = runpy.run_path(str(generator))
    first = namespace["hybrid_genre_regression_signals"](8.0)
    second = namespace["hybrid_genre_regression_signals"](8.0)
    assert set(first) == {
        "hybrid_techno_instrumental.wav",
        "hybrid_techno_spoken_rhythmic_vocals.wav",
        "hybrid_progressive_house_pop_vocals.wav",
        "hybrid_hip_hop_rap_vocals.wav",
        "hybrid_electronic_vocal_only_outro.wav",
        "hybrid_section_genre_change.wav",
    }
    assert all(np.array_equal(first[name], second[name]) for name in first)
    assert not np.array_equal(
        first["hybrid_techno_instrumental.wav"],
        first["hybrid_techno_spoken_rhythmic_vocals.wav"],
    )


def test_private_accompaniment_view_excludes_vocals_and_vocal_delivery_needs_no_transcript(
    tmp_path: Path,
) -> None:
    sample_rate = 22_050
    time = np.arange(sample_rate * 4, dtype=np.float64) / sample_rate
    stems: dict[str, Path] = {}
    signals = {
        "drums": 0.08 * np.sin(2 * np.pi * 60 * time),
        "bass": 0.06 * np.sin(2 * np.pi * 110 * time),
        "other": 0.04 * np.sin(2 * np.pi * 330 * time),
        "vocals": np.where((time * 4).astype(int) % 2 == 0, 0.09 * np.random.default_rng(7).normal(size=time.size), 0.0),
    }
    for name, signal in signals.items():
        path = tmp_path / f"{name}.wav"
        sf.write(path, signal, sample_rate, subtype="FLOAT")
        stems[name] = path
    destination = tmp_path / ".genre-accompaniment-view.wav"
    create_temporary_accompaniment_view(stems, destination)
    accompaniment = load_audio(str(destination)).mono
    expected = signals["drums"] + signals["bass"] + signals["other"]
    assert np.max(np.abs(accompaniment - expected)) < 1e-5
    assert np.max(np.abs(accompaniment - (expected + signals["vocals"]))) > 0.02
    delivery, _phrasing = analyze_vocal_delivery_view(stems["vocals"])
    assert "spoken-rhythmic" in (delivery.value or [])
    assert "transcript" in delivery.method


def test_production_genre_is_not_overwritten_by_hip_hop_vocals_and_sections_remain_visible(
    click_analysis,
) -> None:
    analysis, layered = _layered_analysis(click_analysis)
    assert layered.primary_production_genre is not None
    assert layered.primary_production_genre.value == "electronic dance"
    assert layered.vocal_delivery_style is not None
    assert "spoken-rhythmic" in layered.vocal_delivery_style.value
    assert layered.vocal_genre_influences is not None
    assert "hip-hop" in layered.vocal_genre_influences.value
    assert layered.overall_genre_blend is not None
    assert "techno / progressive house production" in layered.overall_genre_blend.value
    assert "hip-hop" in layered.overall_genre_blend.value
    assert any(layer.supporting_section_ids == ["section-3"] for layer in layered.section_genre_evidence)
    assert layered.user_edited is False
    assert layered.user_accepted is False
    assert all(not candidate.user_edited for candidate in layered.broad_candidates + layered.subgenre_candidates)
    analysis.genre_analysis = layered


def test_layered_detected_prompt_tracks_structured_evidence_without_raw_transcript(
    click_analysis,
    tmp_path: Path,
) -> None:
    analysis, layered = _layered_analysis(click_analysis)
    analysis.genre_analysis = layered
    analysis.rhythm.bpm.value = 133.3
    analysis.rhythm.bpm.confidence = Confidence.MEDIUM
    analysis.structure.energy_arc.value = "tapering"
    transcript = PrivateLyricsTranscript(
        job_id=analysis.job_id,
        model_id="synthetic-private",
        selected_device="cpu",
        segments=[
            LyricsSegment(
                id="private-1",
                start_seconds=4.0,
                end_seconds=6.0,
                text="private regression transcript words",
                confidence=Confidence.MEDIUM,
            )
        ],
    )
    preferences = PromptPreferences(
        prompt_engine_mode=PromptEngineMode.CREATIVE,
        genre_interpretation_mode=GenreInterpretationMode.DETECTED_LAYERED,
        variation_seed=17,
    )
    package = generate_prompt_package(
        analysis,
        preferences,
        settings_for(tmp_path / "prompt"),
        transcript=transcript,
        adapter=FakePromptWriterAdapter(),
    )
    assert not package.deterministic_fallback_used
    assert package.arrangement_blueprint
    assert package.rationale
    facts = {fact.path: fact for fact in package.facts_used}
    assert facts["rhythm.bpm"].value == 133.3
    assert facts["genreAnalysis.overallGenreBlend"].role == "detected-ambiguous"
    assert any(fact.role == "detected-component-influence" for fact in package.facts_used)
    assert all(candidate.facts_used for candidate in package.candidates)
    assert "private regression transcript words" not in package.primary_prompt
    assert "techno / progressive house production" in package.primary_prompt
    assert "hip-hop" in package.primary_prompt


def test_genre_prompt_warnings_distinguish_unavailable_unaccepted_disabled_and_ambiguous(
    click_analysis,
) -> None:
    unavailable = compose_prompt(click_analysis, PromptPreferences())
    assert any("adapter is unavailable" in warning for warning in unavailable.warnings)
    failed_analysis = click_analysis.model_copy(deep=True)
    failed_analysis.warnings.append("The local genre adapter failed safely; no genre result was fabricated.")
    failed = compose_prompt(failed_analysis, PromptPreferences())
    assert any("analysis is unavailable" in warning for warning in failed.warnings)

    analysis, layered = _layered_analysis(click_analysis)
    analysis.genre_analysis = layered
    unaccepted = compose_prompt(analysis, PromptPreferences())
    assert any("none were accepted" in warning for warning in unaccepted.warnings)

    analysis.genre_analysis.disabled_for_prompt = True
    disabled = compose_prompt(
        analysis,
        PromptPreferences(genre_interpretation_mode=GenreInterpretationMode.DETECTED_LAYERED),
    )
    assert any("available but disabled" in warning for warning in disabled.warnings)

    analysis.genre_analysis.disabled_for_prompt = False
    ambiguous = compose_prompt(
        analysis,
        PromptPreferences(genre_interpretation_mode=GenreInterpretationMode.DETECTED_LAYERED),
    )
    assert any("available but ambiguous" in warning for warning in ambiguous.warnings)
    assert "techno / progressive house production" in ambiguous.primary_prompt
    assert "hip-hop" in ambiguous.primary_prompt

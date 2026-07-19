from __future__ import annotations

import importlib.util
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeAlias

import numpy as np
from scipy.signal import resample_poly

from ..analysis.core import AudioData, load_audio
from ..config import Settings
from ..model_cache import verify_model_manifest
from ..schemas import (
    AnalysisResult,
    Confidence,
    GenreAnalysis,
    GenreCandidate,
    GenreWindowEvidence,
    ModelAdapterCapability,
    Section,
)
from ..taxonomies.music_styles import (
    DescriptiveLabel,
    MusicStyleTaxonomy,
    StyleLabel,
    SubgenreLabel,
    load_music_style_taxonomy,
)

WINDOW_WEIGHTING_METHOD = (
    "silence-trimmed central, high-energy, median-energy, repeated-groove, percussion, "
    "intro, and outro views; robust weighted aggregation favors track-centroid and level "
    "representativeness, sustained central/repeated grooves, and percussion evidence while "
    "downweighting context edges and vocal-dominant windows"
)

TaxonomyEntry: TypeAlias = StyleLabel | SubgenreLabel | DescriptiveLabel


class MusicTaggerAdapter(Protocol):
    adapter_id: str

    def capability(self) -> ModelAdapterCapability: ...
    def model_metadata(self) -> dict[str, str]: ...
    def analyze_global(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis: ...
    def analyze_windows(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis: ...
    def selected_device(self) -> str: ...
    def cleanup(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AudioWindow:
    id: str
    kind: str
    start: float
    end: float
    section_ids: tuple[str, ...] = ()
    base_weight: float = 1.0
    vocal_dominant: bool = False
    percussion_dominant: bool = False


@dataclass(frozen=True, slots=True)
class WindowProfile:
    window: AudioWindow
    weight: float
    representativeness: float
    activity: float
    centroid_similarity: float


def _candidate(
    item: StyleLabel | SubgenreLabel,
    similarity: float,
    confidence: Confidence,
) -> GenreCandidate:
    return GenreCandidate(
        id=item.id,
        label=item.prompt_safe_label,
        canonical_label=item.prompt_safe_label,
        parent=getattr(item, "parent", None),
        similarity=round(float(similarity), 5),
        confidence=confidence,
    )


def _overlap(start: float, end: float, section: Section) -> float:
    return max(0.0, min(end, section.end_seconds) - max(start, section.start_seconds))


def _section_context(
    analysis: AnalysisResult,
    start: float,
    end: float,
) -> tuple[tuple[str, ...], bool, bool]:
    overlaps = [
        (section, _overlap(start, end, section))
        for section in analysis.structure.sections
        if _overlap(start, end, section) > 0.0
    ]
    section_ids = tuple(section.id for section, _duration in overlaps)
    if not overlaps:
        return section_ids, False, False

    stem_totals: defaultdict[str, float] = defaultdict(float)
    covered = sum(duration for _section, duration in overlaps)
    for section, duration in overlaps:
        if section.deep_evidence is None:
            continue
        for stem, ratio in section.deep_evidence.relative_rms.items():
            stem_totals[stem] += max(0.0, float(ratio)) * duration
    if covered > 0.0:
        stem_totals = defaultdict(
            float,
            {stem: total / covered for stem, total in stem_totals.items()},
        )
    vocals = stem_totals["vocals"]
    drums = stem_totals["drums"]
    bass = stem_totals["bass"]
    vocal_dominant = (
        vocals >= 0.25
        and vocals >= drums * 1.35
        and vocals >= bass * 1.2
    )
    percussion_dominant = drums >= 0.2 and drums >= vocals * 1.1
    return section_ids, vocal_dominant, percussion_dominant


def _base_weight(kind: str) -> float:
    return {
        "middle": 1.25,
        "repeated-groove": 1.2,
        "percussion-dominant": 1.15,
        "high-energy": 1.1,
        "median-energy": 1.0,
        "intro": 0.65,
        "outro": 0.5,
        "whole-track": 1.0,
    }.get(kind, 1.0)


def _percussion_margin(section: Section) -> float:
    evidence = section.deep_evidence
    if evidence is None:
        return -1.0
    return evidence.relative_rms.get("drums", 0.0) - evidence.relative_rms.get(
        "vocals",
        0.0,
    )


def _make_window(
    analysis: AnalysisResult,
    kind: str,
    raw_start: float,
    usable_start: float,
    usable_end: float,
    window_seconds: float,
) -> AudioWindow | None:
    start = min(max(usable_start, usable_end - window_seconds), max(usable_start, raw_start))
    end = min(usable_end, start + window_seconds)
    if end - start < 1.0:
        return None
    section_ids, vocal_dominant, percussion_dominant = _section_context(
        analysis,
        start,
        end,
    )
    weight = _base_weight(kind)
    if vocal_dominant:
        weight *= 0.28 if kind == "outro" else 0.42
    if percussion_dominant:
        weight *= 1.12
    return AudioWindow(
        id="",
        kind=kind,
        start=start,
        end=end,
        section_ids=section_ids,
        base_weight=weight,
        vocal_dominant=vocal_dominant,
        percussion_dominant=percussion_dominant,
    )


def _usable_audio_bounds(analysis: AnalysisResult) -> tuple[float, float]:
    duration = analysis.file.duration_seconds
    leading = float(analysis.signal_quality.leading_silence_seconds.value or 0.0)
    trailing = float(analysis.signal_quality.trailing_silence_seconds.value or 0.0)
    usable_start = min(duration, max(0.0, leading))
    usable_end = max(usable_start, duration - max(0.0, trailing))
    return usable_start, usable_end


def _select_windows(
    analysis: AnalysisResult,
    window_seconds: float = 10.0,
) -> list[AudioWindow]:
    """Select bounded track views without claiming that every view is representative."""

    usable_start, usable_end = _usable_audio_bounds(analysis)
    usable_duration = usable_end - usable_start
    if usable_duration <= 0.0:
        return []
    if usable_duration <= window_seconds:
        section_ids, vocal_dominant, percussion_dominant = _section_context(
            analysis,
            usable_start,
            usable_end,
        )
        return [
            AudioWindow(
                "window-1",
                "whole-track",
                usable_start,
                usable_end,
                section_ids,
                _base_weight("whole-track"),
                vocal_dominant,
                percussion_dominant,
            )
        ]

    starts: list[tuple[str, float]] = [
        ("middle", usable_start + usable_duration / 2 - window_seconds / 2),
    ]
    energetic = [
        section
        for section in analysis.structure.sections
        if section.energy is not None and _overlap(usable_start, usable_end, section) > 0.0
    ]
    if energetic:
        high = max(energetic, key=lambda item: float(item.energy or 0.0))
        ordered = sorted(energetic, key=lambda item: float(item.energy or 0.0))
        median = ordered[len(ordered) // 2]
        starts.extend(
            [
                (
                    "high-energy",
                    (high.start_seconds + high.end_seconds - window_seconds) / 2,
                ),
                (
                    "median-energy",
                    (median.start_seconds + median.end_seconds - window_seconds) / 2,
                ),
            ]
        )

    repeated: defaultdict[str, list[Section]] = defaultdict(list)
    for section in analysis.structure.sections:
        if section.repetition_group:
            repeated[section.repetition_group].append(section)
    eligible_groups = [sections for sections in repeated.values() if len(sections) >= 2]
    if eligible_groups:
        main_group = max(
            eligible_groups,
            key=lambda group: sum(item.end_seconds - item.start_seconds for item in group),
        )
        representative = max(
            main_group,
            key=lambda item: (
                float(item.energy or 0.0),
                item.end_seconds - item.start_seconds,
            ),
        )
        starts.append(
            (
                "repeated-groove",
                (
                    representative.start_seconds
                    + representative.end_seconds
                    - window_seconds
                )
                / 2,
            )
        )

    percussive_sections = [
        section
        for section in analysis.structure.sections
        if section.deep_evidence is not None
        and section.deep_evidence.relative_rms.get("drums", 0.0) >= 0.2
    ]
    if percussive_sections:
        percussion = max(
            percussive_sections,
            key=_percussion_margin,
        )
        starts.append(
            (
                "percussion-dominant",
                (percussion.start_seconds + percussion.end_seconds - window_seconds) / 2,
            )
        )

    # Intro and outro remain visible context, but their lower base weights prevent
    # either edge from overriding the sustained central/repeated material.
    starts.extend(
        [
            ("intro", usable_start),
            ("outro", usable_end - window_seconds),
        ]
    )

    windows: list[AudioWindow] = []
    for kind, raw_start in starts:
        candidate = _make_window(
            analysis,
            kind,
            raw_start,
            usable_start,
            usable_end,
            window_seconds,
        )
        if candidate is None:
            continue
        candidate_duration = candidate.end - candidate.start
        duplicate = any(
            max(
                0.0,
                min(candidate.end, previous.end) - max(candidate.start, previous.start),
            )
            / max(1e-6, min(candidate_duration, previous.end - previous.start))
            >= 0.8
            for previous in windows
        )
        if duplicate:
            continue
        windows.append(
            AudioWindow(
                id=f"window-{len(windows) + 1}",
                kind=candidate.kind,
                start=candidate.start,
                end=candidate.end,
                section_ids=candidate.section_ids,
                base_weight=candidate.base_weight,
                vocal_dominant=candidate.vocal_dominant,
                percussion_dominant=candidate.percussion_dominant,
            )
        )
    return windows[:7]


def _spectral_centroid(samples: np.ndarray, sample_rate: int) -> float:
    if samples.size < 8 or not np.any(samples):
        return 0.0
    stride = max(1, math.ceil(samples.size / 32_768))
    bounded = samples[::stride].astype(np.float64)
    bounded -= float(np.mean(bounded))
    spectrum = np.abs(np.fft.rfft(bounded * np.hanning(bounded.size)))
    total = float(np.sum(spectrum))
    if total <= 1e-12:
        return 0.0
    frequencies = np.fft.rfftfreq(bounded.size, d=stride / sample_rate)
    return float(np.sum(frequencies * spectrum) / total)


def _profile_windows(audio: AudioData, windows: Sequence[AudioWindow]) -> list[WindowProfile]:
    if not windows:
        return []
    global_rms = max(float(np.sqrt(np.mean(np.square(audio.mono)))), 1e-8)
    global_centroid = _spectral_centroid(audio.mono, audio.sample_rate)
    activity_threshold = max(global_rms * 0.12, 1e-6)
    unnormalized: list[tuple[AudioWindow, float, float, float, float]] = []
    for window in windows:
        start = max(0, int(round(window.start * audio.sample_rate)))
        end = min(audio.mono.size, max(start + 1, int(round(window.end * audio.sample_rate))))
        samples = audio.mono[start:end]
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        activity = float(np.mean(np.abs(samples) >= activity_threshold)) if samples.size else 0.0
        centroid = _spectral_centroid(samples, audio.sample_rate)
        centroid_similarity = math.exp(
            -abs(math.log((centroid + 40.0) / (global_centroid + 40.0)))
        )
        level_similarity = math.exp(-abs(math.log((rms + 1e-8) / global_rms)))
        representativeness = float(
            np.clip(0.55 * centroid_similarity + 0.3 * level_similarity + 0.15 * activity, 0.0, 1.0)
        )
        duration_factor = min(1.0, samples.size / max(1.0, 8.0 * audio.sample_rate))
        raw_weight = (
            window.base_weight
            * (0.45 + 0.55 * representativeness)
            * (0.75 + 0.25 * duration_factor)
        )
        unnormalized.append(
            (window, raw_weight, representativeness, activity, centroid_similarity)
        )
    total = sum(item[1] for item in unnormalized)
    if total <= 0.0:
        total = float(len(unnormalized))
        unnormalized = [
            (window, 1.0, representative, activity, centroid)
            for window, _weight, representative, activity, centroid in unnormalized
        ]
    return [
        WindowProfile(
            window=window,
            weight=raw_weight / total,
            representativeness=representativeness,
            activity=activity,
            centroid_similarity=centroid_similarity,
        )
        for window, raw_weight, representativeness, activity, centroid_similarity in unnormalized
    ]


def _weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    order = np.argsort(np.asarray(values, dtype=np.float64))
    ordered_values = np.asarray(values, dtype=np.float64)[order]
    ordered_weights = np.asarray(weights, dtype=np.float64)[order]
    cumulative = np.cumsum(ordered_weights)
    index = int(np.searchsorted(cumulative, cumulative[-1] * 0.5, side="left"))
    return float(ordered_values[min(index, len(ordered_values) - 1)])


def _aggregate_rows(
    rows: Sequence[dict[str, float]],
    weights: Sequence[float],
) -> dict[str, float]:
    if not rows or len(rows) != len(weights):
        raise ValueError("genre aggregation requires one positive weight per score row")
    weight_array = np.asarray(weights, dtype=np.float64)
    if np.any(weight_array < 0.0) or float(np.sum(weight_array)) <= 0.0:
        raise ValueError("genre window weights must be non-negative with a positive sum")
    weight_array /= float(np.sum(weight_array))
    labels = tuple(rows[0])
    if any(set(row) != set(labels) for row in rows):
        raise ValueError("genre score rows must contain the same labels")
    aggregated: dict[str, float] = {}
    for label in labels:
        values = [row[label] for row in rows]
        weighted_mean = float(np.dot(np.asarray(values), weight_array))
        robust_center = _weighted_median(values, weight_array.tolist())
        aggregated[label] = 0.7 * weighted_mean + 0.3 * robust_center
    return aggregated


def _ranking_score(
    item_id: str,
    similarities: dict[str, float],
    adjustments: dict[str, float] | None = None,
) -> float:
    return similarities[item_id] + (adjustments or {}).get(item_id, 0.0)


def _rank_items(
    items: Sequence[TaxonomyEntry],
    similarities: dict[str, float],
    adjustments: dict[str, float] | None = None,
) -> list[TaxonomyEntry]:
    return sorted(
        items,
        key=lambda item: (_ranking_score(item.id, similarities, adjustments), item.id),
        reverse=True,
    )


def _top_tag_ids(tag_scores: dict[str, float], limit: int = 6) -> set[str]:
    return {
        item[0]
        for item in sorted(tag_scores.items(), key=lambda entry: entry[1], reverse=True)[:limit]
    }


def _feature_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item) for item in value).casefold()
    return str(value).casefold()


def _measured_support_adjustments(
    analysis: AnalysisResult,
    tag_scores: dict[str, float],
) -> tuple[dict[str, float], list[str]]:
    """Return bounded ranking nudges; CLAP remains the primary semantic evidence."""

    adjustments: defaultdict[str, float] = defaultdict(float)
    reasons: list[str] = []
    regularity = _feature_text(analysis.rhythm.rhythmic_regularity.value)
    stability = _feature_text(analysis.rhythm.tempo_stability.value)
    percussiveness = _feature_text(analysis.rhythm.percussiveness.value)
    groove = _feature_text(analysis.rhythm.groove_descriptors.value)
    grid = float(analysis.rhythm.beat_grid_alignment.value or 0.0) if analysis.rhythm.beat_grid_alignment else 0.0
    bpm = float(analysis.rhythm.bpm.value or 0.0)
    top_tags = _top_tag_ids(tag_scores)
    steady = any(token in f"{regularity} {stability} {groove}" for token in ("stable", "steady", "driving"))
    percussive = "pronounced" in percussiveness or "percussive" in top_tags
    club_semantics = "club-driven" in top_tags
    club_evidence = steady and percussive and (grid >= 0.35 or club_semantics)
    instruments = analysis.instrumentation.candidates.value or []
    instrument_names = " ".join(candidate.name for candidate in instruments).casefold()
    section_drums = any(
        section.deep_evidence is not None
        and section.deep_evidence.relative_rms.get("drums", 0.0) >= 0.2
        for section in analysis.structure.sections
    )
    section_bass = any(
        section.deep_evidence is not None
        and section.deep_evidence.relative_rms.get("bass", 0.0) >= 0.2
        for section in analysis.structure.sections
    )
    drums_and_bass = (
        ("drum" in instrument_names or section_drums)
        and ("bass" in instrument_names or section_bass)
    )
    if club_evidence:
        adjustments["electronic-dance"] += 0.014
        adjustments["experimental"] -= 0.016
        reasons.append("steady pulse and percussion supported a dance family and restrained the experimental catch-all")
        if 95.0 <= bpm <= 180.0:
            adjustments["electronic-dance"] += 0.002
            reasons.append("tempo supplied only weak support after the independent club-pulse evidence")
        if drums_and_bass:
            adjustments["electronic-dance"] += 0.004
            reasons.append("section-aligned drums and bass supplied bounded supporting evidence")
    if club_semantics and "synthetic" in top_tags:
        adjustments["electronic-dance"] += 0.006
        reasons.append("CLAP descriptive tags jointly supported club-driven synthetic production")
    if {"atmospheric", "sparse"}.issubset(top_tags) and not club_evidence:
        adjustments["electronic-non-dance"] += 0.009
        adjustments["cinematic"] += 0.003
        reasons.append("atmospheric sparse evidence without a stable club pulse supported non-dance electronic texture")
    if "vocal-led" in top_tags and not club_evidence:
        adjustments["r-and-b-soul"] += 0.005
        adjustments["pop"] += 0.003
        reasons.append("vocal-led evidence supplied a small song-family nudge")
    if "organic" in top_tags and "synthetic" not in top_tags:
        adjustments["acoustic-folk"] += 0.004
        adjustments["rock-metal"] += 0.003
    return {
        item_id: float(np.clip(value, -0.025, 0.025))
        for item_id, value in adjustments.items()
    }, reasons


def _subgenre_support_adjustments(
    analysis: AnalysisResult,
    items: Sequence[SubgenreLabel],
    tag_scores: dict[str, float],
) -> dict[str, float]:
    adjustments: defaultdict[str, float] = defaultdict(float)
    top_tags = _top_tag_ids(tag_scores)
    regularity = _feature_text(analysis.rhythm.rhythmic_regularity.value)
    stability = _feature_text(analysis.rhythm.tempo_stability.value)
    percussiveness = _feature_text(analysis.rhythm.percussiveness.value)
    bpm = float(analysis.rhythm.bpm.value or 0.0)
    club_evidence = (
        "club-driven" in top_tags
        and ("stable" in stability or "steady" in regularity)
        and ("pronounced" in percussiveness or "percussive" in top_tags)
    )
    for item in items:
        if club_evidence and "techno-family" in item.compatibility_groups:
            adjustments[item.id] += 0.005
        if "progressive-arrangement" in top_tags and item.id in {
            "progressive-house",
            "melodic-techno",
        }:
            adjustments[item.id] += 0.006
        if {"atmospheric", "spacious"} & top_tags and item.id in {
            "ambient-techno",
            "deep-techno",
            "dub-techno",
            "ambient-electronic",
        }:
            adjustments[item.id] += 0.004
        if "vocal-led" in top_tags and item.id == "vocal-techno":
            adjustments[item.id] += 0.004
        if "sparse" in top_tags and item.id == "minimal-techno":
            adjustments[item.id] += 0.005
        if "dense" in top_tags and item.id == "industrial-techno":
            adjustments[item.id] += 0.003
        # Tempo never creates a family or subgenre. Once rhythmic and semantic
        # club evidence exists, an in-range prior can only break a very close tie.
        if (
            club_evidence
            and item.tempo_prior is not None
            and item.tempo_prior.minimum_bpm <= bpm <= item.tempo_prior.maximum_bpm
        ):
            adjustments[item.id] += 0.002
    return {
        item_id: float(np.clip(value, -0.012, 0.012))
        for item_id, value in adjustments.items()
    }


def _family_ids_for_subgenre_stage(
    broad_ranked: Sequence[StyleLabel],
    broad_scores: dict[str, float],
    adjustments: dict[str, float],
) -> tuple[str, ...]:
    if not broad_ranked:
        return ()
    selected = [broad_ranked[0].id]
    top_score = _ranking_score(broad_ranked[0].id, broad_scores, adjustments)
    for item in broad_ranked[1:3]:
        if top_score - _ranking_score(item.id, broad_scores, adjustments) <= 0.035:
            selected.append(item.id)
    return tuple(selected)


def _agreement(
    rows: Sequence[dict[str, float]],
    profiles: Sequence[WindowProfile],
    winner: str,
) -> float:
    total = sum(profile.weight for profile in profiles)
    if total <= 0.0:
        return 0.0
    agreement_weight = sum(
        (
            profile.weight
            for row, profile in zip(rows, profiles, strict=True)
            if max(row, key=lambda item_id: row[item_id]) == winner
        ),
        0.0,
    )
    return agreement_weight / total


def _alternate_window_stability(
    rows: Sequence[dict[str, float]],
    profiles: Sequence[WindowProfile],
    winner: str,
) -> float:
    if len(rows) < 3:
        return 1.0 if rows else 0.0
    stable = 0
    trials = 0
    for omitted in range(len(rows)):
        reduced_rows = [row for index, row in enumerate(rows) if index != omitted]
        reduced_weights = [
            profile.weight for index, profile in enumerate(profiles) if index != omitted
        ]
        ranking = _aggregate_rows(reduced_rows, reduced_weights)
        stable += int(max(ranking, key=lambda item_id: ranking[item_id]) == winner)
        trials += 1
    return stable / max(1, trials)


def _confidence(
    *,
    broad_margin: float,
    subgenre_margin: float,
    agreement: float,
    duration: float,
    hierarchy_consistency: float,
    alternate_stability: float,
    supporting_evidence_compatible: bool,
) -> Confidence:
    if duration < 6.0 or broad_margin < 0.007 or agreement < 0.3:
        return Confidence.LOW
    high = (
        duration >= 20.0
        and broad_margin >= 0.024
        and subgenre_margin >= 0.016
        and agreement >= 0.55
        and hierarchy_consistency >= 0.85
        and alternate_stability >= 0.75
        and supporting_evidence_compatible
    )
    if high:
        return Confidence.HIGH
    medium = (
        duration >= 10.0
        and broad_margin >= 0.011
        and subgenre_margin >= 0.007
        and agreement >= 0.4
        and hierarchy_consistency >= 0.6
        and alternate_stability >= 0.5
    )
    return Confidence.MEDIUM if medium else Confidence.LOW


class FakeMusicTaggerAdapter:
    adapter_id = "fake-music-tagger"

    def capability(self) -> ModelAdapterCapability:
        return ModelAdapterCapability(
            id=self.adapter_id,
            name="Fake deterministic music tagger",
            installed=True,
            model_ready=True,
            available=True,
            enabled=True,
            reason="Deterministic test adapter is ready.",
            model_id="fake-clap-v2",
            selected_device="cpu",
            effective_device="cpu",
            taxonomy_version="2.0.0",
            license="Test-only",
        )

    def model_metadata(self) -> dict[str, str]:
        return {"modelId": "fake-clap-v2", "taxonomyVersion": "2.0.0"}

    def analyze_global(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis:
        return self.analyze_windows(decoded_path, analysis)

    def analyze_windows(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis:
        windows = _select_windows(analysis)
        total_weight = sum((window.base_weight for window in windows), 0.0) or 1.0
        evidence = [
            GenreWindowEvidence(
                id=window.id,
                kind=window.kind,
                start_seconds=window.start,
                end_seconds=window.end,
                top_labels=["electronic dance", "electronic non-dance"],
                similarities={"electronic dance": 0.42, "electronic non-dance": 0.35},
                weight=window.base_weight / total_weight,
                representativeness=1.0,
                vocal_dominant=window.vocal_dominant,
                percussion_dominant=window.percussion_dominant,
                section_ids=list(window.section_ids),
            )
            for window in windows
        ]
        return GenreAnalysis(
            broad_candidates=[
                GenreCandidate(
                    id="electronic-dance",
                    label="electronic dance",
                    canonical_label="electronic dance",
                    similarity=0.42,
                    confidence=Confidence.HIGH,
                ),
                GenreCandidate(
                    id="electronic-non-dance",
                    label="non-dance electronic",
                    canonical_label="non-dance electronic",
                    similarity=0.35,
                    confidence=Confidence.MEDIUM,
                ),
            ],
            subgenre_candidates=[
                GenreCandidate(
                    id="melodic-techno",
                    label="melodic techno",
                    canonical_label="melodic techno",
                    parent="electronic-dance",
                    similarity=0.31,
                    confidence=Confidence.MEDIUM,
                ),
                GenreCandidate(
                    id="progressive-house",
                    label="progressive house",
                    canonical_label="progressive house",
                    parent="electronic-dance",
                    similarity=0.29,
                    confidence=Confidence.MEDIUM,
                ),
            ],
            blend_candidates=["melodic techno / progressive house blend"],
            descriptive_tags=[
                GenreCandidate(
                    id="synthetic",
                    label="synthetic",
                    canonical_label="synthetic",
                    similarity=0.38,
                    confidence=Confidence.MEDIUM,
                )
            ],
            window_evidence=evidence,
            confidence=Confidence.MEDIUM,
            ambiguity="Melodic techno and progressive house remain close alternatives.",
            method="deterministic fake hierarchical audio-text similarity",
            model_id="fake-clap-v2",
            taxonomy_version="2.0.0",
            selected_device="cpu",
            agreement_across_windows=1.0,
        )

    def selected_device(self) -> str:
        return "cpu"

    def cleanup(self) -> None:
        return


class TransformersClapMusicTagger:
    adapter_id = "transformers-clap"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.taxonomy = load_music_style_taxonomy()
        self._model: Any | None = None
        self._processor: Any | None = None
        self._text_feature_cache: dict[tuple[str, ...], Any] = {}
        self._device = self._resolve_device()

    def _resolve_device(self) -> str:
        if self.settings.genre_device == "cpu":
            return "cpu"
        if importlib.util.find_spec("torch") is None:
            return "unavailable"
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else (
                "cpu" if self.settings.genre_device == "auto" else "unavailable"
            )
        except (ImportError, RuntimeError, OSError):
            return "unavailable"

    def capability(self) -> ModelAdapterCapability:
        installed = (
            importlib.util.find_spec("transformers") is not None
            and importlib.util.find_spec("torch") is not None
        )
        verified, manifest_reason = verify_model_manifest(
            self.settings.genre_model_dir,
            self.settings.genre_model_id,
            self.settings.genre_model_revision,
        )
        ready = (
            self.settings.enable_genre_tagger
            and installed
            and verified
            and self._device != "unavailable"
        )
        if not self.settings.enable_genre_tagger:
            reason = (
                "Disabled until ENABLE_GENRE_TAGGER=true and an explicitly installed "
                "model is verified."
            )
        elif not installed:
            reason = "Transformers and CUDA-capable PyTorch are not installed."
        elif not verified:
            reason = manifest_reason
        elif self._device == "unavailable":
            reason = "The requested genre device is unavailable."
        else:
            reason = "The offline CLAP model and complete manifest are ready."
        return ModelAdapterCapability(
            id=self.adapter_id,
            name="Transformers CLAP music tagger",
            installed=installed,
            model_ready=verified,
            available=ready,
            enabled=self.settings.enable_genre_tagger,
            reason=reason,
            model_id=self.settings.genre_model_id,
            model_revision=self.settings.genre_model_revision,
            selected_device=self.settings.genre_device,
            effective_device=self._device if ready else "unavailable",
            disk_impact_mb=650,
            taxonomy_version=self.taxonomy.taxonomy_version,
            fallback_reason=None if ready else reason,
            features=[
                "broad-family hierarchy",
                "family-gated subgenres",
                "separate descriptive tags",
                "representative-window weighting",
            ],
            license="Apache-2.0 model card and Transformers code",
        )

    def model_metadata(self) -> dict[str, str]:
        return {
            "modelId": self.settings.genre_model_id,
            "revision": self.settings.genre_model_revision,
            "taxonomyVersion": self.taxonomy.taxonomy_version,
        }

    def selected_device(self) -> str:
        return self._device

    def _load(self) -> tuple[Any, Any, Any]:
        if not self.capability().available:
            raise RuntimeError("The local genre adapter is unavailable.")
        import torch
        from transformers import ClapModel, ClapProcessor

        if self._model is None or self._processor is None:
            self._processor = ClapProcessor.from_pretrained(
                str(self.settings.genre_model_dir),
                local_files_only=True,
            )
            self._model = ClapModel.from_pretrained(
                str(self.settings.genre_model_dir),
                local_files_only=True,
            ).to(self._device)
            self._model.eval()
        return self._model, self._processor, torch

    def _similarities(
        self,
        samples: np.ndarray,
        labels: Sequence[tuple[str, str]],
    ) -> dict[str, float]:
        model, processor, torch = self._load()
        audio = resample_poly(samples.astype(np.float32), 3, 1).astype(np.float32)
        descriptions = [description for _label, description in labels]
        description_key = tuple(descriptions)
        with torch.inference_mode():
            audio_inputs = processor(audios=audio, sampling_rate=48_000, return_tensors="pt")
            audio_inputs = {key: value.to(self._device) for key, value in audio_inputs.items()}
            audio_features = model.get_audio_features(**audio_inputs)
            audio_features = audio_features / audio_features.norm(dim=-1, keepdim=True)
            text_features = self._text_feature_cache.get(description_key)
            if text_features is None:
                text_inputs = processor(text=descriptions, return_tensors="pt", padding=True)
                text_inputs = {
                    key: value.to(self._device) for key, value in text_inputs.items()
                }
                text_features = model.get_text_features(**text_inputs)
                text_features = text_features / text_features.norm(dim=-1, keepdim=True)
                self._text_feature_cache[description_key] = text_features.detach()
            scores = (audio_features @ text_features.T).squeeze(0).detach().cpu().tolist()
        return {
            label: float(score)
            for (label, _description), score in zip(labels, scores, strict=True)
        }

    def _entry_similarities(
        self,
        samples: np.ndarray,
        entries: Sequence[TaxonomyEntry],
    ) -> dict[str, float]:
        prompts = [
            (f"{entry.id}::{index}", description)
            for entry in entries
            for index, description in enumerate(entry.clap_descriptions)
        ]
        prompt_scores = self._similarities(samples, prompts)
        return {
            entry.id: float(
                np.median(
                    [
                        prompt_scores[f"{entry.id}::{index}"]
                        for index in range(len(entry.clap_descriptions))
                    ]
                )
            )
            for entry in entries
        }

    def _aggregate(
        self,
        audio: AudioData,
        profiles: Sequence[WindowProfile],
        entries: Sequence[TaxonomyEntry],
    ) -> tuple[dict[str, float], list[dict[str, float]]]:
        if not entries:
            return {}, []
        rows: list[dict[str, float]] = []
        for profile in profiles:
            start = int(profile.window.start * audio.sample_rate)
            end = max(start + 1, int(profile.window.end * audio.sample_rate))
            rows.append(self._entry_similarities(audio.mono[start:end], entries))
        return _aggregate_rows(rows, [profile.weight for profile in profiles]), rows

    def analyze_global(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis:
        return self.analyze_windows(decoded_path, analysis)

    def analyze_windows(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis:
        taxonomy: MusicStyleTaxonomy = self.taxonomy
        audio = load_audio(str(decoded_path))
        windows = _select_windows(analysis)
        if not windows:
            raise RuntimeError("No usable non-silent window was available for genre tagging.")
        profiles = _profile_windows(audio, windows)

        broad_scores, broad_rows = self._aggregate(
            audio,
            profiles,
            taxonomy.broad_genres,
        )
        tag_scores, _tag_rows = self._aggregate(
            audio,
            profiles,
            taxonomy.descriptive_tags,
        )
        broad_adjustments, support_reasons = _measured_support_adjustments(
            analysis,
            tag_scores,
        )
        broad_ranked = [
            item
            for item in _rank_items(
                taxonomy.broad_genres,
                broad_scores,
                broad_adjustments,
            )
            if isinstance(item, StyleLabel)
        ]
        parent_ids = set(
            _family_ids_for_subgenre_stage(
                broad_ranked,
                broad_scores,
                broad_adjustments,
            )
        )
        sub_items = [item for item in taxonomy.subgenres if item.parent in parent_ids]
        sub_scores, _sub_rows = self._aggregate(audio, profiles, sub_items)
        sub_adjustments = _subgenre_support_adjustments(analysis, sub_items, tag_scores)
        sub_ranked = [
            item
            for item in _rank_items(sub_items, sub_scores, sub_adjustments)
            if isinstance(item, SubgenreLabel)
        ]
        tag_ranked = [
            item
            for item in _rank_items(taxonomy.descriptive_tags, tag_scores)
            if isinstance(item, DescriptiveLabel)
        ]

        broad_top = broad_ranked[0]
        broad_second = broad_ranked[1] if len(broad_ranked) > 1 else None
        broad_top_rank = _ranking_score(broad_top.id, broad_scores, broad_adjustments)
        broad_second_rank = (
            _ranking_score(broad_second.id, broad_scores, broad_adjustments)
            if broad_second is not None
            else -1.0
        )
        broad_margin = broad_top_rank - broad_second_rank
        sub_top = sub_ranked[0] if sub_ranked else None
        sub_second = sub_ranked[1] if len(sub_ranked) > 1 else None
        sub_top_rank = (
            _ranking_score(sub_top.id, sub_scores, sub_adjustments)
            if sub_top is not None
            else 0.0
        )
        sub_second_rank = (
            _ranking_score(sub_second.id, sub_scores, sub_adjustments)
            if sub_second is not None
            else sub_top_rank - 1.0
        )
        subgenre_margin = sub_top_rank - sub_second_rank
        agreement = _agreement(broad_rows, profiles, broad_top.id)
        alternate_stability = _alternate_window_stability(
            broad_rows,
            profiles,
            broad_top.id,
        )
        hierarchy_consistency = (
            1.0
            if not sub_items or (sub_top is not None and sub_top.parent == broad_top.id)
            else 0.65
            if sub_top is not None and sub_top.parent in parent_ids
            else 0.0
        )
        confidence = _confidence(
            broad_margin=broad_margin,
            subgenre_margin=subgenre_margin,
            agreement=agreement,
            duration=_usable_audio_bounds(analysis)[1] - _usable_audio_bounds(analysis)[0],
            hierarchy_consistency=hierarchy_consistency,
            alternate_stability=alternate_stability,
            supporting_evidence_compatible=broad_adjustments.get(broad_top.id, 0.0) >= 0.0,
        )

        evidence = [
            GenreWindowEvidence(
                id=profile.window.id,
                kind=profile.window.kind,
                start_seconds=round(profile.window.start, 3),
                end_seconds=round(profile.window.end, 3),
                top_labels=[
                    item.prompt_safe_label
                    for item in sorted(
                        taxonomy.broad_genres,
                        key=lambda entry: row[entry.id],
                        reverse=True,
                    )[:3]
                ],
                similarities={
                    item.prompt_safe_label: round(row[item.id], 5)
                    for item in taxonomy.broad_genres
                },
                weight=profile.weight,
                representativeness=profile.representativeness,
                vocal_dominant=profile.window.vocal_dominant,
                percussion_dominant=profile.window.percussion_dominant,
                section_ids=list(profile.window.section_ids),
            )
            for profile, row in zip(profiles, broad_rows, strict=True)
        ]
        broad_candidates = [
            _candidate(item, broad_scores[item.id], confidence)
            for item in broad_ranked[:5]
        ]
        sub_candidates = [
            _candidate(item, sub_scores[item.id], confidence)
            for item in sub_ranked[:6]
        ]
        tags = [
            GenreCandidate(
                id=item.id,
                label=item.label,
                canonical_label=item.label,
                similarity=round(tag_scores[item.id], 5),
                confidence=confidence,
            )
            for item in tag_ranked[:7]
        ]

        compatible = {frozenset(pair) for pair in taxonomy.compatible_blends}
        blends: list[str] = []
        if (
            sub_top is not None
            and sub_second is not None
            and frozenset((sub_top.id, sub_second.id)) in compatible
            and subgenre_margin < 0.035
        ):
            blends.append(
                f"{sub_top.prompt_safe_label} / {sub_second.prompt_safe_label} blend"
            )

        ambiguity: str | None = None
        if broad_second is not None and broad_margin < 0.018:
            ambiguity = (
                f"{broad_top.prompt_safe_label} and {broad_second.prompt_safe_label} "
                "remain close broad-family alternatives."
            )
        elif sub_top is not None and sub_second is not None and subgenre_margin < 0.016:
            ambiguity = (
                f"Within the selected family, {sub_top.prompt_safe_label} and "
                f"{sub_second.prompt_safe_label} remain close alternatives."
            )
        elif confidence == Confidence.LOW:
            ambiguity = (
                "Window agreement, candidate separation, or usable duration was too weak "
                "for a precise genre claim."
            )

        warnings = [
            "Genre values are cosine-similarity rankings, not calibrated probabilities.",
            f"Representative-window aggregation: {WINDOW_WEIGHTING_METHOD}.",
            (
                f"The top broad family was stable in {alternate_stability:.0%} of "
                "leave-one-window-out comparisons."
            ),
        ]
        if support_reasons:
            warnings.append(
                "Bounded measured-evidence support: " + "; ".join(support_reasons) + "."
            )
        return GenreAnalysis(
            broad_candidates=broad_candidates,
            subgenre_candidates=sub_candidates,
            blend_candidates=blends,
            descriptive_tags=tags,
            window_evidence=evidence,
            confidence=confidence,
            ambiguity=ambiguity,
            method=(
                "three-stage hierarchical CLAP cosine similarity: natural-language "
                "broad-family description ensembles, family-gated subgenres, then separate "
                "descriptive tags; raw similarities are retained while bounded measured "
                "evidence can only adjust ranking"
            ),
            model_id=self.settings.genre_model_id,
            taxonomy_version=taxonomy.taxonomy_version,
            selected_device=self._device,
            agreement_across_windows=round(agreement, 4),
            warnings=warnings,
        )

    def cleanup(self) -> None:
        self._model = None
        self._processor = None
        self._text_feature_cache.clear()
        if importlib.util.find_spec("torch") is not None:
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except (ImportError, RuntimeError):
                pass


def create_music_tagger(settings: Settings) -> MusicTaggerAdapter:
    return TransformersClapMusicTagger(settings)

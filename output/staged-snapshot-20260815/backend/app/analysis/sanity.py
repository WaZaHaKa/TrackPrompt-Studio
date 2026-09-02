from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
from pydantic import BaseModel

from ..schemas import AnalysisResult, Confidence, EvidenceKind, FeatureValue

LOGGER = logging.getLogger(__name__)


def _record(result: AnalysisResult, invariant: str, message: str) -> None:
    safe = f"Analysis consistency check ({invariant}) adjusted an affected result: {message}"
    if safe not in result.warnings:
        result.warnings.append(safe)
    # Analyzer and invariant names are metadata-safe. Do not log values or paths.
    LOGGER.warning("analysis_invariant analyzer=%s invariant=%s", "sanity", invariant)


def _sanitize_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, (float, np.floating)):
        return (float(value), False) if math.isfinite(float(value)) else (None, True)
    if isinstance(value, list):
        changed = False
        cleaned_list: list[Any] = []
        for item in value:
            next_item, item_changed = _sanitize_value(item)
            changed = changed or item_changed
            if next_item is not None:
                cleaned_list.append(next_item)
        return cleaned_list, changed
    if isinstance(value, dict):
        changed = False
        cleaned_dict: dict[Any, Any] = {}
        for key, item in value.items():
            next_item, item_changed = _sanitize_value(item)
            changed = changed or item_changed
            if next_item is not None:
                cleaned_dict[key] = next_item
        return cleaned_dict, changed
    return value, False


def _sanitize_features(model: BaseModel) -> bool:
    changed = False
    for name in model.__class__.model_fields:
        value = getattr(model, name)
        if isinstance(value, FeatureValue):
            cleaned, value_changed = _sanitize_value(value.value)
            if value_changed:
                value.value = cleaned
                value.confidence = Confidence.UNKNOWN
                value.evidence_kind = EvidenceKind.UNAVAILABLE
                value.warning = "A non-finite analyzer value was omitted by consistency validation."
                changed = True
            if value.score is not None and not math.isfinite(value.score):
                value.score = None
                changed = True
        elif isinstance(value, BaseModel):
            changed = _sanitize_features(value) or changed
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, BaseModel):
                    changed = _sanitize_features(item) or changed
            cleaned_list, list_changed = _sanitize_value(value)
            if list_changed:
                setattr(model, name, cleaned_list)
                changed = True
        elif isinstance(value, dict):
            cleaned_dict, dict_changed = _sanitize_value(value)
            if dict_changed:
                setattr(model, name, cleaned_dict)
                changed = True
        elif isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
            setattr(model, name, None)
            changed = True
    return changed


def _genre_edit_evidence(result: AnalysisResult) -> bool:
    genre = result.genre_analysis
    if genre is None:
        return False
    candidates = [
        *genre.broad_candidates,
        *genre.subgenre_candidates,
        *genre.descriptive_tags,
    ]
    return genre.disabled_for_prompt or any(
        candidate.user_edited
        or candidate.accepted
        or candidate.rejected
        or candidate.locked
        or candidate.custom
        for candidate in candidates
    )


def _project_authoritative_genre(result: AnalysisResult) -> None:
    """Derive compatibility style fields from the authoritative genre object."""
    genre = result.genre_analysis
    if genre is None:
        return
    candidate_groups = [
        genre.broad_candidates,
        genre.subgenre_candidates,
        genre.descriptive_tags,
    ]
    candidates = [candidate for group in candidate_groups for candidate in group]
    for candidate in candidates:
        if candidate.accepted and candidate.rejected:
            candidate.accepted = False
            _record(result, "genre_candidate_review_state", "a rejected genre candidate was unaccepted")
    expected_edited = _genre_edit_evidence(result)
    if genre.user_edited != expected_edited:
        genre.user_edited = expected_edited
        _record(result, "genre_edit_state", "the aggregate genre edit marker was reconciled")
    genre.user_accepted = any(candidate.accepted for candidate in candidates)
    accepted_labels = {
        candidate.label
        for candidate in candidates
        if candidate.accepted and not candidate.rejected
    }
    custom_labels = {candidate.label for candidate in candidates if candidate.custom}
    for layer in (
        genre.primary_production_genre,
        genre.secondary_production_genres,
        genre.vocal_delivery_style,
        genre.vocal_genre_influences,
        genre.overall_genre_blend,
        *genre.section_genre_evidence,
    ):
        if layer is None:
            continue
        values = [layer.value] if isinstance(layer.value, str) else layer.value
        layer.accepted = any(value in accepted_labels for value in values)
        layer.source = "user_entered" if any(value in custom_labels for value in values) else "detected"
        layer.enabled_for_prompt = not genre.disabled_for_prompt
    if genre.overall_genre_blend is not None and genre.primary_production_genre is not None:
        genre.overall_genre_blend.accepted = genre.primary_production_genre.accepted

    usable_broad = [candidate for candidate in genre.broad_candidates if not candidate.rejected]
    top_broad = usable_broad[:3]
    style = result.style_and_mood
    style.broad_style = FeatureValue[list[str]](
        value=[candidate.label for candidate in top_broad],
        confidence=genre.confidence if top_broad else Confidence.UNKNOWN,
        score=top_broad[0].similarity if top_broad else None,
        method=(
            "compatibility projection from authoritative genreAnalysis; "
            "ranked audio-text similarity, not probability"
        ),
        alternatives=[candidate.label for candidate in usable_broad[1:5]],
        warning=genre.ambiguity,
        evidence_kind=(
            EvidenceKind.AMBIGUOUS
            if genre.ambiguity
            else EvidenceKind.STRONG_ESTIMATE
            if top_broad
            else EvidenceKind.UNAVAILABLE
        ),
        user_edited=genre.user_edited,
        user_accepted=genre.user_accepted,
    )
    style.genre_blend = FeatureValue[list[str]](
        value=list(genre.blend_candidates),
        confidence=genre.confidence if genre.blend_candidates else Confidence.UNKNOWN,
        method="compatibility projection from authoritative genreAnalysis blend candidates",
        warning=genre.ambiguity,
        evidence_kind=(
            EvidenceKind.AMBIGUOUS
            if genre.ambiguity
            else EvidenceKind.STRONG_ESTIMATE
            if genre.blend_candidates
            else EvidenceKind.UNAVAILABLE
        ),
        user_edited=genre.user_edited,
        user_accepted=genre.user_accepted,
    )


def validate_analysis_result(
    result: AnalysisResult,
    *,
    private_lyrics_artifact_available: bool | None = None,
) -> AnalysisResult:
    """Downgrade contradictory fields before API serialization."""
    duration = max(0.0, float(result.file.duration_seconds))
    leading = result.signal_quality.leading_silence_seconds
    trailing = result.signal_quality.trailing_silence_seconds
    if isinstance(leading.value, (int, float)) and isinstance(trailing.value, (int, float)):
        if float(leading.value) + float(trailing.value) > duration + 0.05:
            trailing.value = round(max(0.0, duration - float(leading.value)), 3)
            trailing.confidence = Confidence.LOW
            trailing.warning = "Adjusted because edge-silence measurements exceeded track duration."
            _record(result, "edge_silence_within_duration", "trailing silence was downgraded")

    valid_sections = []
    last_end = 0.0
    for section in result.structure.sections:
        bounds = (section.start_seconds, section.end_seconds)
        if not all(math.isfinite(value) for value in bounds):
            _record(result, "finite_section_bounds", "a section with invalid bounds was omitted")
            continue
        if section.start_seconds < last_end - 0.001 or not (
            0.0 <= section.start_seconds < section.end_seconds <= duration + 0.001
        ):
            _record(result, "ordered_section_bounds", "an overlapping or out-of-range section was omitted")
            continue
        section.end_seconds = min(section.end_seconds, duration)
        valid_sections.append(section)
        last_end = section.end_seconds
    if not valid_sections and duration > 0:
        result.structure.sections = []
        _record(result, "usable_sections", "no safe section bounds remained")
    else:
        result.structure.sections = valid_sections

    beats = result.rhythm.beat_timestamps
    if isinstance(beats.value, list):
        cleaned_beats = sorted(
            {
                round(float(value), 6)
                for value in beats.value
                if isinstance(value, (int, float))
                and math.isfinite(float(value))
                and 0.0 <= float(value) <= duration
            }
        )
        if cleaned_beats != beats.value:
            beats.value = cleaned_beats
            _record(result, "finite_bounded_beats", "invalid beat timestamps were omitted")
        bpm = result.rhythm.bpm.value
        if isinstance(bpm, (int, float)) and len(cleaned_beats) >= 3:
            measured = float(np.median(np.diff(cleaned_beats)))
            expected = 60.0 / float(bpm)
            if abs(measured - expected) / max(expected, 1e-9) > 0.12:
                beats.value = []
                beats.confidence = Confidence.UNKNOWN
                beats.evidence_kind = EvidenceKind.UNAVAILABLE
                beats.warning = "Beat grid was omitted because its interval contradicted the selected BPM."
                _record(result, "beat_grid_matches_bpm", "the contradictory beat grid was omitted")

    peak = result.production.peak_dbfs
    if isinstance(peak.value, (int, float)) and float(peak.value) > 0.05:
        peak.value = None
        peak.confidence = Confidence.UNKNOWN
        peak.evidence_kind = EvidenceKind.UNAVAILABLE
        peak.warning = "A physically inconsistent normalized sample peak was withheld."
        _record(result, "normalized_peak_not_positive", "sample peak was withheld")

    if result.effective_mode == "deep":
        for section in result.structure.sections:
            if section.deep_evidence is None or "no enabled vocal separator" in (
                section.vocal_activity or ""
            ).casefold():
                section.vocal_activity = "unavailable (Deep section evidence missing)"
                _record(
                    result,
                    "deep_sections_match_adapter",
                    "missing section-level Deep evidence was marked unavailable",
                )

    _project_authoritative_genre(result)

    if result.lyrics_summary is not None:
        summary = result.lyrics_summary
        valid_section_ids = {section.id for section in result.structure.sections}
        active_ids = list(
            dict.fromkeys(
                section_id
                for section_id in summary.active_section_ids
                if section_id in valid_section_ids
            )
        )
        if active_ids != summary.active_section_ids:
            summary.active_section_ids = active_ids
            _record(result, "lyrics_active_sections_exist", "invalid transcript section references were omitted")
        if summary.segment_count == 0 and summary.transcript_available:
            summary.transcript_available = False
            summary.active_section_ids = []
            _record(result, "lyrics_segment_count_matches_availability", "empty transcript availability was corrected")
        if private_lyrics_artifact_available is False and (
            summary.status == "completed" or summary.transcript_available
        ):
            summary.status = "artifact_missing"
            summary.transcript_available = False
            summary.segment_count = 0
            summary.active_section_ids = []
            summary.language = None
            summary.language_confidence = Confidence.UNKNOWN
            summary.vocal_word_density = None
            summary.non_lexical_vocalization_tendency = None
            summary.abstract_themes = []
            summary.theme_confidence = Confidence.UNKNOWN
            summary.themes_user_approved = False
            artifact_warning = (
                "The private transcript artifact is unavailable; transcript-derived lyrics "
                "evidence was disabled."
            )
            if artifact_warning not in summary.warnings:
                summary.warnings.append(artifact_warning)
            _record(
                result,
                "lyrics_completed_requires_private_artifact",
                "lyrics evidence was marked unavailable because its private artifact was missing",
            )

    key = result.harmony.key
    alternative_fits = [
        float(item["templateFit"])
        for item in key.alternatives
        if isinstance(item, dict) and isinstance(item.get("templateFit"), (int, float))
    ]
    if key.score is not None and alternative_fits:
        margin = key.score - max(alternative_fits)
        if margin < 0.025 and key.confidence in {Confidence.MEDIUM, Confidence.HIGH}:
            for tonal_feature in (result.harmony.key, result.harmony.mode):
                tonal_feature.confidence = Confidence.LOW
                tonal_feature.evidence_kind = EvidenceKind.AMBIGUOUS
                tonal_feature.warning = "Near-tied tonal candidates were downgraded to ambiguous."
            _record(result, "key_margin_matches_confidence", "near-tied key confidence was downgraded")

    if _sanitize_features(result):
        _record(result, "finite_serialized_numbers", "non-finite analyzer output was omitted")
    return result

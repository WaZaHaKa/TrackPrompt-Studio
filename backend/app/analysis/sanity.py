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


def validate_analysis_result(result: AnalysisResult) -> AnalysisResult:
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

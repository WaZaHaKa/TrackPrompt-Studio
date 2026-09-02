from __future__ import annotations

import math
import re
from typing import Any

from pydantic import ValidationError

from .schemas import AnalysisPatch, AnalysisResult, FeatureUpdate


class PatchError(ValueError):
    pass


FORBIDDEN_ROOTS = {
    "schemaVersion",
    "analysisVersion",
    "jobId",
    "capabilities",
    "requestedMode",
    "effectiveMode",
    "file",
    "waveformPeaks",
    "warnings",
    "analyzerVersions",
    "disabledFeaturePaths",
    "createdAt",
}
SECTION_EDIT_FIELDS = {
    "neutralLabel",
    "inferredLabel",
    "startSeconds",
    "endSeconds",
    "repetitionGroup",
    "energy",
    "density",
    "vocalActivity",
    "harmonySummary",
    "transitionIn",
    "transitionOut",
}
SECTION_LABEL_PATTERN = re.compile(
    r"(?:intro|verse|pre-?chorus|chorus|refrain|bridge|breakdown|build|drop|interlude|outro|"
    r"instrumental|transition|section(?: [a-z0-9]+)?|part [a-z0-9]+)",
    re.IGNORECASE,
)


def _feature_at(payload: dict[str, Any], path: str) -> dict[str, Any]:
    parts = path.split(".")
    if not parts or any(not part or not part.replace("_", "").isalnum() for part in parts):
        raise PatchError("Feature path is invalid.")
    if parts[0] in FORBIDDEN_ROOTS:
        raise PatchError("This field cannot be edited.")
    current: Any = payload
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise PatchError("Feature path was not found.")
        current = current[part]
    if not isinstance(current, dict) or not {"value", "confidence", "method", "userEdited"}.issubset(current):
        raise PatchError("Only analysis feature values can be edited or disabled.")
    return current


def _section_field_at(payload: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
    parts = path.split(".")
    if len(parts) != 4 or parts[:2] != ["structure", "sections"]:
        raise PatchError("Section edit path is invalid.")
    if not parts[2].isdigit() or parts[3] not in SECTION_EDIT_FIELDS:
        raise PatchError("This section field cannot be edited.")
    sections = payload.get("structure", {}).get("sections")
    index = int(parts[2])
    if not isinstance(sections, list) or index >= len(sections):
        raise PatchError("Section edit path was not found.")
    section = sections[index]
    if not isinstance(section, dict) or parts[3] not in section:
        raise PatchError("Section edit path was not found.")
    return section, parts[3]


def _validate_section_value(field: str, value: Any) -> None:
    if field in {"startSeconds", "endSeconds", "energy", "density"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PatchError("Section timing and measure edits must be finite numbers.")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise PatchError("Section timing and measure edits must be finite non-negative numbers.")
        if field in {"energy", "density"} and numeric > 1:
            raise PatchError("Section energy and density must be between zero and one.")
        return
    if value is None and field != "neutralLabel":
        return
    if not isinstance(value, str) or not value or len(value) > 160 or not value.isprintable():
        raise PatchError("Section text edits must be short printable text.")
    if field in {"neutralLabel", "inferredLabel"} and SECTION_LABEL_PATTERN.fullmatch(value) is None:
        raise PatchError("Section labels must use a neutral arrangement label.")
    if field == "repetitionGroup" and re.fullmatch(r"[A-Za-z0-9_-]{1,20}", value) is None:
        raise PatchError("Section repetition groups must use a short neutral identifier.")


def _apply_section_update(
    current_payload: dict[str, Any],
    detected_payload: dict[str, Any],
    update: FeatureUpdate,
    disabled: set[str],
) -> None:
    current, field = _section_field_at(current_payload, update.path)
    detected, _ = _section_field_at(detected_payload, update.path)
    if update.accepted_for_prompt is not None:
        raise PatchError("Section fields do not use feature acceptance.")
    if update.restore_detected and "value" in update.model_fields_set:
        raise PatchError("A restore update cannot also supply a new value.")
    if update.restore_detected:
        current[field] = detected[field]
    elif "value" in update.model_fields_set:
        _validate_section_value(field, update.value)
        current[field] = update.value
    if update.disabled_for_prompt is True:
        disabled.add(update.path)
    elif update.disabled_for_prompt is False:
        disabled.discard(update.path)


def _validate_section_bounds(analysis: AnalysisResult) -> None:
    previous_end = 0.0
    duration = analysis.file.duration_seconds
    for section in analysis.structure.sections:
        if section.start_seconds < previous_end - 0.001:
            raise PatchError("Edited sections cannot overlap or move out of order.")
        if section.end_seconds <= section.start_seconds:
            raise PatchError("Each edited section must end after it starts.")
        if section.end_seconds > duration + 0.25:
            raise PatchError("Edited section bounds cannot exceed the track duration.")
        previous_end = section.end_seconds


def _apply_update(
    current_payload: dict[str, Any],
    detected_payload: dict[str, Any],
    update: FeatureUpdate,
    disabled: set[str],
) -> None:
    if update.path.startswith("structure.sections."):
        _apply_section_update(current_payload, detected_payload, update, disabled)
        return
    current = _feature_at(current_payload, update.path)
    detected = _feature_at(detected_payload, update.path)
    if update.restore_detected and "value" in update.model_fields_set:
        raise PatchError("A restore update cannot also supply a new value.")
    if update.restore_detected:
        current.clear()
        current.update(detected)
        current["userEdited"] = False
        current["userAccepted"] = False
    elif "value" in update.model_fields_set:
        current["value"] = update.value
        current["userEdited"] = True
        current["userAccepted"] = False
    if update.accepted_for_prompt is not None:
        current["userAccepted"] = update.accepted_for_prompt
    if update.disabled_for_prompt is True:
        disabled.add(update.path)
    elif update.disabled_for_prompt is False:
        disabled.discard(update.path)


def apply_analysis_patch(
    analysis: AnalysisResult,
    detected: AnalysisResult,
    patch: AnalysisPatch,
) -> AnalysisResult:
    if analysis.job_id != detected.job_id:
        raise PatchError("Detected and edited analyses do not match.")
    current_payload = analysis.model_dump(mode="json", by_alias=True)
    detected_payload = detected.model_dump(mode="json", by_alias=True)
    disabled = set(analysis.disabled_feature_paths)
    if patch.disabled_feature_paths is not None:
        disabled = set()
        for path in patch.disabled_feature_paths:
            if path.startswith("structure.sections."):
                _section_field_at(current_payload, path)
            else:
                _feature_at(current_payload, path)
            disabled.add(path)
    for update in patch.updates:
        _apply_update(current_payload, detected_payload, update, disabled)
    if patch.user_overrides:
        for path, value in patch.user_overrides.items():
            synthetic = FeatureUpdate(path=path, value=value)
            _apply_update(current_payload, detected_payload, synthetic, disabled)
    current_payload["disabledFeaturePaths"] = sorted(disabled)
    try:
        result = AnalysisResult.model_validate(current_payload)
        _validate_section_bounds(result)
        return result
    except ValidationError as exc:
        raise PatchError("The edited value is not valid for this feature.") from exc

from __future__ import annotations

from collections import defaultdict

from ..schemas import (
    AnalysisResult,
    Confidence,
    GenreAnalysis,
    GenreCandidate,
    GenreLayerEvidence,
    GenreWindowEvidence,
)

VOCAL_FAMILY_LABELS = {
    "hip-hop": "hip-hop",
    "hip hop": "hip-hop",
    "pop": "pop",
    "r&b / soul": "R&B",
    "r-and-b / soul": "R&B",
    "r-and-b-soul": "R&B",
}


def _active(candidates: list[GenreCandidate]) -> list[GenreCandidate]:
    return [candidate for candidate in candidates if not candidate.rejected]


def _window_ids(windows: list[GenreWindowEvidence], view: str) -> list[str]:
    return [window.id for window in windows if window.analysis_view == view]


def _accepted_for_label(candidates: list[GenreCandidate], label: str) -> bool:
    return any(candidate.label == label and candidate.accepted and not candidate.rejected for candidate in candidates)


def _layer(
    value: str | list[str],
    *,
    confidence: Confidence,
    method: str,
    windows: list[str],
    sections: list[str],
    alternatives: list[str] | None = None,
    ambiguity: str | None = None,
    accepted: bool = False,
    enabled: bool = True,
) -> GenreLayerEvidence:
    return GenreLayerEvidence(
        value=value,
        confidence=confidence,
        method=method,
        supporting_window_ids=list(dict.fromkeys(windows)),
        supporting_section_ids=list(dict.fromkeys(sections)),
        alternatives=list(dict.fromkeys(alternatives or [])),
        ambiguity=ambiguity,
        source="detected",
        accepted=accepted,
        enabled_for_prompt=enabled,
    )


def _copy_windows(
    analysis: GenreAnalysis,
    view: str,
) -> list[GenreWindowEvidence]:
    return [
        window.model_copy(
            update={
                "id": f"{view}:{window.id}",
                "analysis_view": view,
            }
        )
        for window in analysis.window_evidence
    ]


def build_layered_genre_analysis(
    full_mix: GenreAnalysis,
    analysis: AnalysisResult,
    production_view: GenreAnalysis | None = None,
) -> GenreAnalysis:
    """Keep production, vocal, section, and listener-facing genre evidence distinct."""

    production = production_view or full_mix
    full_windows = _copy_windows(full_mix, "full_mix")
    production_windows = (
        _copy_windows(production, "instrumental_accompaniment")
        if production_view is not None
        else []
    )
    windows = [*full_windows, *production_windows]
    production_window_ids = _window_ids(
        windows,
        "instrumental_accompaniment" if production_view is not None else "full_mix",
    )
    production_sections = [
        section_id
        for window in windows
        if window.id in production_window_ids
        for section_id in window.section_ids
    ]
    broad = _active(production.broad_candidates)
    subgenres = _active(production.subgenre_candidates)
    primary_candidate = broad[0] if broad else None
    secondary_labels = [candidate.label for candidate in subgenres[:3]]
    enabled = not full_mix.disabled_for_prompt

    primary = (
        _layer(
            primary_candidate.label,
            confidence=production.confidence,
            method=(
                "private accompaniment-view genre ranking"
                if production_view is not None
                else "full-mix representative-window genre ranking with vocal-dominant windows downweighted"
            ),
            windows=production_window_ids,
            sections=production_sections,
            alternatives=[candidate.label for candidate in broad[1:4]],
            ambiguity=production.ambiguity,
            accepted=primary_candidate.accepted,
            enabled=enabled,
        )
        if primary_candidate is not None
        else None
    )
    secondary = _layer(
        secondary_labels,
        confidence=production.confidence,
        method="family-gated production-subgenre ranking on the accompaniment view",
        windows=production_window_ids,
        sections=production_sections,
        alternatives=[candidate.label for candidate in subgenres[3:6]],
        ambiguity=production.ambiguity,
        accepted=any(candidate.accepted for candidate in subgenres[:3]),
        enabled=enabled,
    )

    delivery_values = [str(value) for value in (analysis.vocals.delivery.value or [])]
    phrasing_values = [str(value) for value in (analysis.vocals.phrasing.value or [])]
    vocal_windows = [window for window in full_windows if window.vocal_dominant]
    vocal_section_ids = [section_id for window in vocal_windows for section_id in window.section_ids]
    vocal_delivery = _layer(
        list(dict.fromkeys([*delivery_values, *phrasing_values])),
        confidence=analysis.vocals.delivery.confidence,
        method=analysis.vocals.delivery.method,
        windows=[window.id for window in vocal_windows],
        sections=vocal_section_ids,
        ambiguity=analysis.vocals.delivery.warning,
        enabled=enabled,
    )

    vocal_influences: list[str] = []
    if any(value in {"spoken-rhythmic", "rapped"} for value in delivery_values):
        vocal_influences.append("hip-hop")
    for window in vocal_windows:
        for label in window.top_labels:
            normalized = label.casefold().strip()
            mapped = VOCAL_FAMILY_LABELS.get(normalized)
            if mapped is not None:
                vocal_influences.append(mapped)
    vocal_influences = list(dict.fromkeys(vocal_influences))
    vocal_genre = _layer(
        vocal_influences,
        confidence=(
            Confidence.MEDIUM
            if vocal_influences and analysis.vocals.delivery.confidence == Confidence.MEDIUM
            else Confidence.LOW
            if vocal_influences
            else Confidence.UNKNOWN
        ),
        method=(
            "non-identifying vocal-delivery acoustics plus vocal-dominant full-mix window evidence"
        ),
        windows=[window.id for window in vocal_windows],
        sections=vocal_section_ids,
        alternatives=[],
        ambiguity=(
            None
            if vocal_influences
            else "No defensible vocal genre influence crossed the component-evidence threshold."
        ),
        enabled=enabled,
    )

    by_section: defaultdict[str, list[GenreWindowEvidence]] = defaultdict(list)
    for window in windows:
        for section_id in window.section_ids:
            by_section[section_id].append(window)
    section_layers: list[GenreLayerEvidence] = []
    legacy_section_evidence: dict[str, list[str]] = {}
    for section in analysis.structure.sections:
        related = by_section.get(section.id, [])
        production_labels = [
            label
            for window in related
            if window.analysis_view in {"instrumental_accompaniment", "full_mix"}
            and (production_view is None or window.analysis_view == "instrumental_accompaniment")
            for label in window.top_labels[:2]
        ]
        values = [f"{label} production" for label in dict.fromkeys(production_labels)]
        vocal_active = section.deep_evidence is not None and section.deep_evidence.activity.get("vocals") in {
            "present",
            "prominent",
        }
        if vocal_active:
            values.extend(f"{value} vocal delivery" for value in delivery_values[:2])
            values.extend(f"{value} vocal influence" for value in vocal_influences[:2])
        values = list(dict.fromkeys(values))
        if not values:
            continue
        legacy_section_evidence[section.id] = values
        section_layers.append(
            _layer(
                values,
                confidence=Confidence.MEDIUM if production_labels else Confidence.LOW,
                method="section overlap with separated production and vocal evidence views",
                windows=[window.id for window in related],
                sections=[section.id],
                alternatives=[],
                ambiguity=None if production_labels else "Only component-level vocal evidence was available.",
                enabled=enabled,
            )
        )

    production_identity = secondary_labels[0] if secondary_labels else (primary_candidate.label if primary_candidate else "")
    if len(secondary_labels) >= 2:
        production_identity = f"{secondary_labels[0]} / {secondary_labels[1]}"
    overall_text = (
        f"{production_identity} production with {' and '.join(vocal_influences)} vocal influence"
        if production_identity and vocal_influences
        else f"{production_identity} production"
        if production_identity
        else "genre blend unavailable"
    )
    overall = _layer(
        overall_text,
        confidence=production.confidence,
        method="layer-aware synthesis of production-view, full-mix, and vocal-delivery evidence",
        windows=[window.id for window in windows],
        sections=[section.id for section in analysis.structure.sections],
        alternatives=[*production.blend_candidates, *full_mix.blend_candidates],
        ambiguity=production.ambiguity or full_mix.ambiguity,
        accepted=bool(primary and primary.accepted),
        enabled=enabled,
    )
    blends = list(dict.fromkeys([overall_text, *production.blend_candidates, *full_mix.blend_candidates]))[:4]
    return full_mix.model_copy(
        update={
            "broad_candidates": production.broad_candidates,
            "subgenre_candidates": production.subgenre_candidates,
            "descriptive_tags": production.descriptive_tags,
            "blend_candidates": blends,
            "window_evidence": windows,
            "section_evidence": legacy_section_evidence,
            "primary_production_genre": primary,
            "secondary_production_genres": secondary,
            "vocal_delivery_style": vocal_delivery,
            "vocal_genre_influences": vocal_genre,
            "section_genre_evidence": section_layers,
            "overall_genre_blend": overall,
            "confidence": production.confidence,
            "ambiguity": production.ambiguity or full_mix.ambiguity,
            "method": (
                f"{production.method}; layer synthesis keeps accompaniment production, "
                "full-mix presentation, and non-identifying vocal delivery separate"
            ),
            "warnings": list(
                dict.fromkeys(
                    [
                        *full_mix.warnings,
                        *production.warnings,
                        "Vocal delivery is acoustic component evidence and is not a transcript or identity claim.",
                    ]
                )
            ),
        }
    )

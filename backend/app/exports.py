from __future__ import annotations

import json

from .schemas import AnalysisResult, GenreCandidate, PromptPackage


def analysis_json_export(analysis: AnalysisResult, prompt: PromptPackage | None) -> bytes:
    payload = {
        "schemaVersion": analysis.schema_version,
        "analysisVersion": analysis.analysis_version,
        "analysis": analysis.model_dump(mode="json", by_alias=True),
        "promptPackage": prompt.model_dump(mode="json", by_alias=True) if prompt is not None else None,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _safe(value: object) -> str:
    text = str(value)
    text = "".join(character for character in text if character.isprintable())
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`").replace("<", "&lt;")


def _genre_candidate_state(candidate: GenreCandidate, prompt_disabled: bool) -> tuple[str, str]:
    states = ["User-entered" if candidate.custom else "Detected"]
    if candidate.accepted:
        states.append("Accepted")
    if candidate.rejected:
        states.append("Rejected")
    if candidate.locked:
        states.append("Locked")
    if candidate.user_edited:
        states.append("User-edited")
    prompt_state = (
        "Disabled"
        if prompt_disabled
        else "Eligible"
        if candidate.accepted and not candidate.rejected
        else "Not eligible"
    )
    return ", ".join(states), prompt_state


def _genre_layer_value(value: object | None) -> str:
    if value is None:
        return "not available"
    layer_value = getattr(value, "value", None)
    if isinstance(layer_value, list):
        return ", ".join(str(item) for item in layer_value) or "none detected"
    return str(layer_value) if layer_value is not None else "not available"


def _append_genre_analysis(lines: list[str], analysis: AnalysisResult) -> None:
    genre = analysis.genre_analysis
    if genre is None:
        return
    broad_projection = analysis.style_and_mood.broad_style.value or []
    blend_projection = analysis.style_and_mood.genre_blend.value or []
    lines.extend(
        [
            "",
            "## Authoritative genre analysis",
            "",
            f"- Confidence: `{_safe(genre.confidence)}`",
            f"- Ambiguity: {_safe(genre.ambiguity or 'none reported')}",
            f"- Taxonomy/model: `{_safe(genre.taxonomy_version)}` / `{_safe(genre.model_id)}`",
            f"- Device: `{_safe(genre.selected_device)}`",
            f"- Prompt inclusion: {'disabled' if genre.disabled_for_prompt else 'enabled; active prompt mode decides accepted versus detected-layered use'}",
            f"- User edited/accepted: {'yes' if genre.user_edited else 'no'} / {'yes' if genre.user_accepted else 'no'}",
            f"- Primary production genre: {_safe(_genre_layer_value(genre.primary_production_genre))}",
            f"- Secondary production genres: {_safe(_genre_layer_value(genre.secondary_production_genres))}",
            f"- Vocal delivery style: {_safe(_genre_layer_value(genre.vocal_delivery_style))}",
            f"- Vocal genre influences: {_safe(_genre_layer_value(genre.vocal_genre_influences))}",
            f"- Overall genre blend: {_safe(_genre_layer_value(genre.overall_genre_blend))}",
            f"- `styleAndMood.broadStyle` projection: {_safe(', '.join(broad_projection) or 'none')}",
            f"- `styleAndMood.genreBlend` projection: {_safe(', '.join(blend_projection) or 'none')}",
            f"- Method: {_safe(genre.method)}",
            "",
            "| Level | Label | Parent | Similarity | Confidence | Review state | Prompt state |",
            "| --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    candidate_groups = (
        ("Broad family", genre.broad_candidates),
        ("Subgenre", genre.subgenre_candidates),
        ("Descriptive tag", genre.descriptive_tags),
    )
    for level, candidates in candidate_groups:
        for candidate in candidates:
            review_state, prompt_state = _genre_candidate_state(candidate, genre.disabled_for_prompt)
            lines.append(
                f"| {level} | {_safe(candidate.label)} | {_safe(candidate.parent or '')} | "
                f"{candidate.similarity:.5f} | {_safe(candidate.confidence)} | {_safe(review_state)} | "
                f"{_safe(prompt_state)} |"
            )
    if genre.blend_candidates:
        lines.extend(["", f"- Detected ambiguous blends: {_safe(', '.join(genre.blend_candidates))}"])
    if genre.section_genre_evidence:
        lines.extend(["", "### Section-level genre influences", ""])
        for layer in genre.section_genre_evidence:
            lines.append(
                f"- {_safe(', '.join(layer.supporting_section_ids) or 'unmapped section')}: "
                f"{_safe(_genre_layer_value(layer))} ({_safe(layer.confidence)}; {_safe(layer.method)})"
            )
    if genre.window_evidence:
        lines.extend(
            [
                "",
                "### Representative windows",
                "",
                "| View | Kind | Start | End | Weight | Representativeness | Evidence | Sections |",
                "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for window in genre.window_evidence:
            dominance = [
                label
                for enabled, label in (
                    (window.vocal_dominant, "vocal-dominant"),
                    (window.percussion_dominant, "percussion-dominant"),
                )
                if enabled
            ]
            evidence = ", ".join([*window.top_labels, *dominance]) or "none"
            lines.append(
                f"| {_safe(window.analysis_view)} | {_safe(window.kind)} | {window.start_seconds:.2f} | {window.end_seconds:.2f} | "
                f"{window.weight:.3f} | {window.representativeness:.3f} | {_safe(evidence)} | "
                f"{_safe(', '.join(window.section_ids) or 'none')} |"
            )


def analysis_markdown_export(analysis: AnalysisResult, prompt: PromptPackage | None) -> bytes:
    lines = [
        "# TrackPrompt Studio analysis",
        "",
        f"- Schema version: `{_safe(analysis.schema_version)}`",
        f"- Analysis version: `{_safe(analysis.analysis_version)}`",
        f"- Job ID: `{_safe(analysis.job_id)}`",
        f"- File: {_safe(analysis.file.display_name)}",
        f"- Duration: {analysis.file.duration_seconds:.2f} seconds",
        f"- Requested/effective mode: {_safe(analysis.requested_mode)} / {_safe(analysis.effective_mode)}",
        "",
        "## Overview",
        "",
        "| Feature | Value | Confidence | Method |",
        "| --- | --- | --- | --- |",
        f"| BPM | {_safe(analysis.rhythm.bpm.value)} | {_safe(analysis.rhythm.bpm.confidence)} | {_safe(analysis.rhythm.bpm.method)} |",
        f"| Key | {_safe(analysis.harmony.key.value)} {_safe(analysis.harmony.mode.value)} | {_safe(analysis.harmony.key.confidence)} | {_safe(analysis.harmony.key.method)} |",
        f"| Meter | {_safe(analysis.rhythm.meter.value)} | {_safe(analysis.rhythm.meter.confidence)} | {_safe(analysis.rhythm.meter.method)} |",
        f"| Energy | {_safe(analysis.style_and_mood.energy.value)} | {_safe(analysis.style_and_mood.energy.confidence)} | {_safe(analysis.style_and_mood.energy.method)} |",
        f"| Loudness | {_safe(analysis.production.integrated_loudness_lufs.value)} LUFS | {_safe(analysis.production.integrated_loudness_lufs.confidence)} | {_safe(analysis.production.integrated_loudness_lufs.method)} |",
    ]
    _append_genre_analysis(lines, analysis)
    lines.extend(
        [
            "",
            "## Sections",
            "",
            "| Label | Start | End | Energy | Repetition | Harmony |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for section in analysis.structure.sections:
        lines.append(
            f"| {_safe(section.inferred_label or section.neutral_label)} | {section.start_seconds:.2f} | "
            f"{section.end_seconds:.2f} | {_safe(section.energy)} | {_safe(section.repetition_group or '')} | "
            f"{_safe(section.harmony_summary or 'unknown')} |"
        )
    if analysis.harmony.chords.value:
        lines.extend(["", "## Approximate chord analysis", "", "Chord labels are estimates and are not copied into the generated prompt.", ""])
        for chord in analysis.harmony.chords.value:
            lines.append(
                f"- {chord.start_seconds:.2f}-{chord.end_seconds:.2f}s: "
                f"{_safe(chord.chord or 'unknown')} ({_safe(chord.confidence)})"
            )
    if analysis.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {_safe(warning)}" for warning in analysis.warnings)
    if prompt is not None:
        lines.extend(
            [
                "",
                "## Generated prompt",
                "",
                f"- Engine mode: `{_safe(prompt.engine_mode)}`",
                f"- Selected candidate: `{_safe(prompt.selected_candidate_id or 'none')}`",
                f"- Candidate count: {len(prompt.candidates)}",
                f"- Deterministic fallback used: {'yes' if prompt.deterministic_fallback_used else 'no'}",
                "",
                _safe(prompt.primary_prompt),
                "",
                "### Exclusions",
                "",
            ]
        )
        lines.extend(f"- {_safe(exclusion)}" for exclusion in prompt.exclusions)
        if prompt.candidates:
            lines.extend(["", "### Persisted candidate set", ""])
            for candidate in prompt.candidates:
                selected = " (selected)" if candidate.id == prompt.selected_candidate_id else ""
                lines.extend(
                    [
                        f"#### {_safe(candidate.short_title)}{selected}",
                        "",
                        f"- Candidate ID: `{_safe(candidate.id)}`",
                        f"- Engine: `{_safe(candidate.engine_mode)}`",
                        "",
                        _safe(candidate.prompt),
                        "",
                    ]
                )
        if prompt.arrangement_blueprint:
            lines.extend(["", "### Arrangement blueprint", ""])
            lines.extend(f"- {_safe(item)}" for item in prompt.arrangement_blueprint)
        if prompt.facts_used:
            lines.extend(["", "### Prompt evidence used", ""])
            lines.extend(
                f"- `{_safe(fact.path)}` = {_safe(fact.value)} (`{_safe(fact.role)}`)"
                for fact in prompt.facts_used
            )
        if prompt.facts_omitted:
            lines.extend(["", "### Prompt evidence omitted", ""])
            lines.extend(
                f"- `{_safe(item.path)}`: {_safe(item.reason)}"
                for item in prompt.facts_omitted
            )
    lines.extend(["", "---", "Audio-analysis results are estimates.", ""])
    return "\n".join(lines).encode("utf-8")

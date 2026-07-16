from __future__ import annotations

import json

from .schemas import AnalysisResult, PromptPackage


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
        "",
        "## Sections",
        "",
        "| Label | Start | End | Energy | Repetition | Harmony |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
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
                _safe(prompt.primary_prompt),
                "",
                "### Exclusions",
                "",
            ]
        )
        lines.extend(f"- {_safe(exclusion)}" for exclusion in prompt.exclusions)
    lines.extend(["", "---", "Audio-analysis results are estimates.", ""])
    return "\n".join(lines).encode("utf-8")

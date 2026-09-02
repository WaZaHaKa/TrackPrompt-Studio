from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.adapters import deep_adapters  # noqa: E402
from app.analysis.pipeline import analyze_audio  # noqa: E402
from app.config import Settings  # noqa: E402
from app.media import decode_for_analysis, probe_media  # noqa: E402
from app.prompting import compose_prompt  # noqa: E402
from app.schemas import AnalysisResult, PromptPreferences  # noqa: E402


def _feature(group: Any, name: str) -> Any:
    value = getattr(group, name, None)
    return getattr(value, "value", None)


def _report(result: AnalysisResult, prompt: Any, readiness: list[Any]) -> dict[str, Any]:
    beats = _feature(result.rhythm, "beat_timestamps") or []
    bpm = _feature(result.rhythm, "bpm")
    beat_interval = None
    if isinstance(bpm, (int, float)) and bpm > 0:
        beat_interval = round(60.0 / float(bpm), 4)
    key_score = result.harmony.key.score
    alternative_fits = [
        float(item["templateFit"])
        for item in result.harmony.key.alternatives
        if isinstance(item, dict) and isinstance(item.get("templateFit"), (int, float))
    ]
    key_margin = (
        round(float(key_score) - max(alternative_fits), 4)
        if key_score is not None and alternative_fits
        else None
    )
    return {
        "media": {
            "durationSeconds": result.file.duration_seconds,
            "sampleRate": result.file.sample_rate,
            "channels": result.file.channels,
            "codec": result.file.codec,
            "container": result.file.container,
        },
        "signal": {
            "decodedSampleRange": _feature(result.signal_quality, "decoded_sample_range"),
            "effectiveLevelDbfs": _feature(result.signal_quality, "effective_level_dbfs"),
            "samplePeakDbfs": _feature(result.production, "peak_dbfs"),
            "clipping": _feature(result.signal_quality, "clipping"),
            "activityThresholdDbfs": _feature(result.signal_quality, "activity_threshold_dbfs"),
            "leadingSilenceSeconds": _feature(result.signal_quality, "leading_silence_seconds"),
            "trailingSilenceSeconds": _feature(result.signal_quality, "trailing_silence_seconds"),
        },
        "rhythm": {
            "bpm": bpm,
            "tempoCandidates": result.rhythm.bpm.alternatives,
            "beatGridIntervalSeconds": beat_interval,
            "beatGridAlignment": _feature(result.rhythm, "beat_grid_alignment"),
            "beatCount": len(beats),
            "onsetCount": len(_feature(result.rhythm, "onset_timestamps") or []),
        },
        "harmony": {
            "key": _feature(result.harmony, "key"),
            "mode": _feature(result.harmony, "mode"),
            "confidence": result.harmony.key.confidence,
            "candidates": result.harmony.key.alternatives,
            "runnerUpMargin": key_margin,
        },
        "sections": [
            {
                "label": section.inferred_label or section.neutral_label,
                "startSeconds": section.start_seconds,
                "endSeconds": section.end_seconds,
                "boundaryConfidence": section.boundary_confidence,
                "vocalActivity": section.vocal_activity,
                "stemRelativeRms": (
                    section.deep_evidence.relative_rms if section.deep_evidence else None
                ),
            }
            for section in result.structure.sections
        ],
        "deep": {
            "readiness": [adapter.model_dump(mode="json", by_alias=True) for adapter in readiness],
            "requestedMode": result.requested_mode,
            "effectiveMode": result.effective_mode,
            "diagnostics": (
                result.deep_diagnostics.model_dump(mode="json", by_alias=True)
                if result.deep_diagnostics
                else None
            ),
        },
        "invariantWarnings": [
            warning for warning in result.warnings if "consistency check" in warning
        ],
        "warnings": result.warnings,
        "prompt": prompt.primary_prompt,
        "omittedPromptFacts": [
            item.model_dump(mode="json", by_alias=True) for item in prompt.facts_omitted
        ],
    }


def _render_text(report: dict[str, Any]) -> str:
    lines = ["TrackPrompt Studio local analysis diagnostic"]
    for heading in ("media", "signal", "rhythm", "harmony", "deep"):
        lines.append(f"\n{heading.upper()}")
        value = report[heading]
        if isinstance(value, dict):
            for key, item in value.items():
                lines.append(f"- {key}: {json.dumps(item, ensure_ascii=False, default=str)}")
    lines.append("\nSECTIONS")
    for section in report["sections"]:
        lines.append(f"- {json.dumps(section, ensure_ascii=False, default=str)}")
    lines.append("\nWARNINGS")
    for warning in report["warnings"] or ["none"]:
        lines.append(f"- {warning}")
    lines.append("\nGENERATED PROMPT")
    lines.append(str(report["prompt"]))
    lines.append("\nOMITTED PROMPT FACTS")
    for item in report["omittedPromptFacts"] or ["none"]:
        lines.append(f"- {json.dumps(item, ensure_ascii=False, default=str)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a private local TrackPrompt analysis diagnostic.",
    )
    parser.add_argument("audio_file", type=Path)
    parser.add_argument("--mode", choices=("fast", "deep"), default="fast")
    parser.add_argument("--json", action="store_true", dest="json_output")
    arguments = parser.parse_args()
    source = arguments.audio_file.resolve()
    if not source.is_file():
        parser.error("audio_file must be a readable local file")
    settings = Settings.from_env()
    readiness = deep_adapters(settings)
    with tempfile.TemporaryDirectory(prefix="trackprompt-diagnostic-") as temporary:
        workspace = Path(temporary)
        probe = probe_media(source, "diagnostic-audio", settings)
        decoded = decode_for_analysis(probe, workspace / "decoded.wav", settings)
        serialized = analyze_audio(
            str(decoded),
            probe.file.model_dump(mode="json", by_alias=True),
            "00000000-0000-4000-8000-000000000000",
            arguments.mode,
            str(workspace / "progress.json"),
            str(workspace / "cancel.flag"),
            settings,
        )
        result = AnalysisResult.model_validate_json(serialized)
        prompt = compose_prompt(result, PromptPreferences())
        report = _report(result, prompt, readiness)
        if arguments.json_output:
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        else:
            print(_render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

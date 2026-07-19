from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from ..config import Settings
from ..lyrics.quality import assess_segment_quality, quality_decision_counts
from ..lyrics.transcriber import FasterWhisperLyricsAdapter
from ..prompting.local_writer import theme_evidence_gate
from ..schemas import (
    Confidence,
    LyricsSegment,
    LyricsSegmentQualityDecision,
    PrivateLyricsTranscript,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    arguments = parser.parse_args()
    adapter = FasterWhisperLyricsAdapter(Settings.from_env())
    capability = adapter.capability()
    payload: dict[str, object] = capability.model_dump(mode="json", by_alias=True)
    clear_quality = assess_segment_quality(
        text="synthetic courage returns",
        start_seconds=0.0,
        end_seconds=2.0,
        avg_log_probability=-0.25,
        no_speech_probability=0.05,
        compression_ratio=1.0,
        language_probability=0.95,
    )
    hallucination_quality = assess_segment_quality(
        text="thanks for watching",
        start_seconds=2.0,
        end_seconds=4.0,
        avg_log_probability=-0.8,
        no_speech_probability=0.5,
        compression_ratio=1.2,
        language_probability=0.95,
    )
    sparse_transcript = PrivateLyricsTranscript(
        job_id="00000000-0000-4000-8000-000000000000",
        language="en",
        segments=[
            LyricsSegment(
                id="diagnostic-segment",
                start_seconds=0.0,
                end_seconds=2.0,
                text="synthetic courage returns",
                confidence=Confidence.HIGH,
                quality_decision=LyricsSegmentQualityDecision.ACCEPTED,
            )
        ],
        model_id="diagnostic-only",
        selected_device="cpu",
    )
    _theme_segments, theme_gate_warning = theme_evidence_gate(sparse_transcript)
    payload["segmentQualityFieldsAvailable"] = all(
        field in LyricsSegment.model_fields
        for field in (
            "quality_decision",
            "avg_log_probability",
            "no_speech_score",
            "compression_ratio",
            "repeated_token_ratio",
            "active_section_ids",
        )
    )
    payload["clearSegmentAccepted"] = (
        clear_quality.decision == LyricsSegmentQualityDecision.ACCEPTED
    )
    payload["hallucinationFilterBehavior"] = (
        hallucination_quality.decision
        == LyricsSegmentQualityDecision.REJECTED_AS_LIKELY_HALLUCINATION
    )
    payload["themeGateRejectsSparseEvidence"] = theme_gate_warning is not None
    payload["transcriptPrivacyBoundary"] = (
        "Raw text is stored only in a job-scoped private artifact; diagnostics and standard exports use aggregates."
    )
    payload["rawTranscriptExcludedFromOutput"] = True
    if arguments.smoke and capability.available:
        with tempfile.TemporaryDirectory(prefix="trackprompt-lyrics-smoke-") as directory:
            path = Path(directory) / "silence.wav"
            sf.write(path, np.zeros(16_000, dtype=np.float32), 16_000, subtype="PCM_16")
            transcript, summary = adapter.transcribe(
                path,
                "00000000-0000-4000-8000-000000000000",
            )
            payload["modelLoaded"] = adapter._model is not None
            payload["tinyInference"] = summary.status in {"completed", "no_reliable_words"}
            payload["privateSegmentDecisionCounts"] = quality_decision_counts(transcript)
            payload["ordinaryUsableSegmentCount"] = summary.segment_count
    adapter.cleanup()
    print(json.dumps(payload, indent=2))
    return 0 if capability.available and (not arguments.smoke or payload.get("tinyInference") is True) else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import importlib.util
import math
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from ..config import Settings
from ..model_cache import verify_model_manifest
from ..schemas import (
    Confidence,
    LyricsAnalysisSummary,
    LyricsSegment,
    LyricsSegmentQualityDecision,
    ModelAdapterCapability,
    PrivateLyricsTranscript,
)
from .quality import (
    assess_segment_quality,
    normalize_lyrics_text,
    quality_decision_counts,
    usable_transcript_segments,
)


class LyricsAdapter(Protocol):
    adapter_id: str

    def capability(self) -> ModelAdapterCapability: ...
    def model_metadata(self) -> dict[str, str]: ...
    def transcribe(
        self,
        vocal_stem: Path,
        job_id: str,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[PrivateLyricsTranscript, LyricsAnalysisSummary]: ...
    def selected_device(self) -> str: ...
    def cleanup(self) -> None: ...


def _finite_metric(value: object, *, minimum: float, maximum: float) -> float | None:
    try:
        metric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(metric) or not minimum <= metric <= maximum:
        return None
    return metric


class FakeLyricsAdapter:
    adapter_id = "fake-lyrics-adapter"

    def capability(self) -> ModelAdapterCapability:
        return ModelAdapterCapability(
            id=self.adapter_id,
            name="Fake deterministic lyrics adapter",
            installed=True,
            model_ready=True,
            available=True,
            enabled=True,
            reason="Deterministic test adapter is ready.",
            model_id="fake-whisper-v1",
            selected_device="cpu",
            effective_device="cpu",
            languages_supported="test language",
            privacy_behavior="Raw transcript remains in a separate private artifact.",
            license="Test-only",
        )

    def model_metadata(self) -> dict[str, str]:
        return {"modelId": "fake-whisper-v1"}

    def transcribe(
        self,
        vocal_stem: Path,
        job_id: str,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[PrivateLyricsTranscript, LyricsAnalysisSummary]:
        if cancel_requested is not None and cancel_requested():
            raise RuntimeError("Lyrics transcription was cancelled.")
        created = datetime.now(UTC)
        segments = [
            LyricsSegment(
                id="segment-1",
                start_seconds=0.5,
                end_seconds=2.0,
                text="synthetic test phrase",
                confidence=Confidence.MEDIUM,
                quality_decision=LyricsSegmentQualityDecision.ACCEPTED,
                avg_log_probability=-0.3,
                no_speech_score=0.05,
                compression_ratio=1.0,
                repeated_token_ratio=0.0,
            )
        ]
        transcript = PrivateLyricsTranscript(
            job_id=job_id,
            language="en",
            segments=segments,
            model_id="fake-whisper-v1",
            selected_device="cpu",
            warnings=["This is a deterministic test transcript."],
            created_at=created,
        )
        summary = LyricsAnalysisSummary(
            enabled=True,
            status="completed",
            adapter_id=self.adapter_id,
            model_id="fake-whisper-v1",
            selected_device="cpu",
            language="en",
            language_confidence=Confidence.MEDIUM,
            transcript_available=True,
            segment_count=1,
            vocal_word_density="sparse",
            warnings=["Sung-word recognition is approximate."],
            created_at=created,
        )
        return transcript, summary

    def selected_device(self) -> str:
        return "cpu"

    def cleanup(self) -> None:
        return


class FasterWhisperLyricsAdapter:
    adapter_id = "faster-whisper"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: Any | None = None
        self._device = self._resolve_device()

    def _resolve_device(self) -> str:
        if self.settings.lyrics_device == "cpu":
            return "cpu"
        if importlib.util.find_spec("ctranslate2") is None:
            return "unavailable"
        try:
            import ctranslate2

            if int(ctranslate2.get_cuda_device_count()) > 0:
                return "cuda"
        except (ImportError, RuntimeError, OSError):
            pass
        if self.settings.lyrics_device == "auto" and self.settings.lyrics_cpu_fallback:
            return "cpu"
        return "unavailable"

    def capability(self) -> ModelAdapterCapability:
        installed = importlib.util.find_spec("faster_whisper") is not None and importlib.util.find_spec("ctranslate2") is not None
        verified, manifest_reason = verify_model_manifest(
            self.settings.lyrics_model_dir,
            self.settings.lyrics_model_name,
            self.settings.lyrics_model_revision,
        )
        ready = self.settings.enable_lyrics_adapter and installed and verified and self._device != "unavailable"
        if not self.settings.enable_lyrics_adapter:
            reason = "Disabled until ENABLE_LYRICS_ADAPTER=true and an explicitly installed model is verified."
        elif not installed:
            reason = "faster-whisper and CTranslate2 are not installed."
        elif not verified:
            reason = manifest_reason
        elif self._device == "unavailable":
            reason = "CTranslate2 cannot use the requested device; CPU fallback was not silently enabled."
        else:
            reason = "The offline faster-whisper model and complete manifest are ready."
        return ModelAdapterCapability(
            id=self.adapter_id,
            name="faster-whisper lyrics adapter",
            installed=installed,
            model_ready=verified,
            available=ready,
            enabled=self.settings.enable_lyrics_adapter,
            reason=reason,
            model_id=self.settings.lyrics_model_name,
            model_revision=self.settings.lyrics_model_revision,
            selected_device=self.settings.lyrics_device,
            effective_device=self._device if ready else "unavailable",
            disk_impact_mb=520,
            languages_supported="Whisper multilingual language set",
            privacy_behavior="Consumes only the private vocal stem; raw transcript uses a separate private artifact and is excluded from standard exports.",
            fallback_reason=None if ready else reason,
            features=["language identification", "timestamped approximate transcript", "quality filtering"],
            license="MIT model card, faster-whisper, and CTranslate2",
        )

    def model_metadata(self) -> dict[str, str]:
        return {"modelId": self.settings.lyrics_model_name, "revision": self.settings.lyrics_model_revision}

    def selected_device(self) -> str:
        return self._device

    def _load(self) -> Any:
        if not self.capability().available:
            raise RuntimeError("The local lyrics adapter is unavailable.")
        from faster_whisper import WhisperModel

        if self._model is None:
            compute_type = self.settings.lyrics_compute_type
            if self._device == "cpu" and compute_type in {"float16", "int8_float16"}:
                compute_type = "int8"
            self._model = WhisperModel(
                str(self.settings.lyrics_model_dir),
                device=self._device,
                compute_type=compute_type,
                local_files_only=True,
            )
        return self._model

    def transcribe(
        self,
        vocal_stem: Path,
        job_id: str,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[PrivateLyricsTranscript, LyricsAnalysisSummary]:
        model = self._load()
        raw_segments, info = model.transcribe(
            str(vocal_stem),
            beam_size=3,
            best_of=3,
            temperature=(0.0, 0.2),
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 750, "speech_pad_ms": 300},
            word_timestamps=True,
            hallucination_silence_threshold=1.5,
            compression_ratio_threshold=2.2,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.6,
            repetition_penalty=1.1,
            no_repeat_ngram_size=3,
        )
        buffered_segments: list[Any] = []
        for raw in raw_segments:
            if cancel_requested is not None and cancel_requested():
                raise RuntimeError("Lyrics transcription was cancelled.")
            buffered_segments.append(raw)
        occurrence_counts = Counter(
            normalized
            for raw in buffered_segments
            if (normalized := normalize_lyrics_text(str(raw.text).strip()[:1000]))
        )
        evaluated: list[LyricsSegment] = []
        previous_normalized = ""
        adjacent_repetition_count = 0
        language_probability = _finite_metric(
            getattr(info, "language_probability", None),
            minimum=0.0,
            maximum=1.0,
        )
        for raw in buffered_segments:
            if cancel_requested is not None and cancel_requested():
                raise RuntimeError("Lyrics transcription was cancelled.")
            text = str(raw.text).strip()[:1000]
            normalized = normalize_lyrics_text(text)
            if normalized and normalized == previous_normalized:
                adjacent_repetition_count += 1
            else:
                adjacent_repetition_count = 1
            previous_normalized = normalized
            raw_start = _finite_metric(getattr(raw, "start", None), minimum=-86_400.0, maximum=86_400.0)
            raw_end = _finite_metric(getattr(raw, "end", None), minimum=-86_400.0, maximum=86_400.0)
            stored_start = max(0.0, round(raw_start or 0.0, 3))
            stored_end = max(round(raw_end or stored_start, 3), stored_start + 0.001)
            no_speech = _finite_metric(
                getattr(raw, "no_speech_prob", None),
                minimum=0.0,
                maximum=1.0,
            )
            avg_log_probability = _finite_metric(
                getattr(raw, "avg_logprob", None),
                minimum=-100.0,
                maximum=10.0,
            )
            compression_ratio = _finite_metric(
                getattr(raw, "compression_ratio", None),
                minimum=0.0,
                maximum=100.0,
            )
            quality = assess_segment_quality(
                text=text,
                start_seconds=raw_start if raw_start is not None else math.nan,
                end_seconds=raw_end if raw_end is not None else math.nan,
                avg_log_probability=avg_log_probability,
                no_speech_probability=no_speech,
                compression_ratio=compression_ratio,
                language_probability=language_probability,
                adjacent_repetition_count=adjacent_repetition_count,
                total_occurrences=occurrence_counts.get(normalized, 1),
            )
            evaluated.append(
                LyricsSegment(
                    id=str(uuid4()),
                    start_seconds=stored_start,
                    end_seconds=stored_end,
                    text=text,
                    confidence=quality.confidence,
                    quality_decision=quality.decision,
                    avg_log_probability=avg_log_probability,
                    no_speech_score=no_speech,
                    compression_ratio=compression_ratio,
                    repeated_token_ratio=quality.repeated_token_ratio,
                    quality_flags=list(quality.flags),
                )
            )
        created = datetime.now(UTC)
        warnings = [
            "This is an approximate transcript: singing, reverb, layering, vocal chops, and dense accompaniment can cause substantial errors."
        ]
        transcript = PrivateLyricsTranscript(
            job_id=job_id,
            language=str(info.language) if getattr(info, "language", None) else None,
            segments=evaluated,
            model_id=self.settings.lyrics_model_name,
            selected_device=self._device,
            warnings=warnings,
            created_at=created,
        )
        decision_counts = quality_decision_counts(transcript)
        rejected_count = decision_counts[LyricsSegmentQualityDecision.REJECTED_AS_LIKELY_HALLUCINATION.value]
        non_lexical_count = decision_counts[LyricsSegmentQualityDecision.NON_LEXICAL.value]
        uncertain_count = decision_counts[LyricsSegmentQualityDecision.UNCERTAIN.value]
        if rejected_count:
            warnings.append(
                f"{rejected_count} likely hallucinated segment(s) remain private and were excluded from ordinary analysis and themes."
            )
        if any("repeated" in flag for segment in evaluated for flag in segment.quality_flags):
            warnings.append("Repeated hallucination-like transcript evidence was excluded or marked uncertain.")
        if non_lexical_count:
            warnings.append(
                f"{non_lexical_count} non-lexical vocal segment(s) remain private and were excluded from text evidence."
            )
        if uncertain_count:
            warnings.append(
                f"{uncertain_count} uncertain segment(s) remain private and are not eligible for abstract-theme generation."
            )
        transcript.warnings = warnings
        usable = usable_transcript_segments(transcript)
        total_span = max((segment.end_seconds for segment in usable), default=0.0)
        word_count = sum(len(normalize_lyrics_text(segment.text).split()) for segment in usable)
        words_per_minute = word_count * 60 / max(total_span, 1.0)
        density = "dense" if words_per_minute >= 110 else "moderate" if words_per_minute >= 45 else "sparse"
        language_score = language_probability or 0.0
        language_confidence = Confidence.HIGH if language_score >= 0.8 else Confidence.MEDIUM if language_score >= 0.5 else Confidence.LOW
        summary = LyricsAnalysisSummary(
            enabled=True,
            status="completed" if usable else "no_reliable_words",
            adapter_id=self.adapter_id,
            model_id=self.settings.lyrics_model_name,
            selected_device=self._device,
            language=transcript.language,
            language_confidence=language_confidence,
            transcript_available=bool(usable),
            segment_count=len(usable),
            vocal_word_density=density if usable else "unknown",
            non_lexical_vocalization_tendency="possible" if non_lexical_count else "unknown",
            warnings=warnings,
            created_at=created,
        )
        return transcript, summary

    def cleanup(self) -> None:
        self._model = None
        if importlib.util.find_spec("ctranslate2") is not None:
            try:
                import ctranslate2

                ctranslate2.set_random_seed(0)
            except (ImportError, RuntimeError):
                pass


def create_lyrics_adapter(settings: Settings) -> LyricsAdapter:
    return FasterWhisperLyricsAdapter(settings)

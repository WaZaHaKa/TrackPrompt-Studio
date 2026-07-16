from __future__ import annotations

import importlib.util
import re
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
    ModelAdapterCapability,
    PrivateLyricsTranscript,
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


def _normalized_phrase(text: str) -> str:
    return " ".join(re.findall(r"[\w']+", text.casefold()))


def _quality(avg_log_prob: float | None, no_speech: float | None) -> Confidence:
    if no_speech is not None and no_speech >= 0.5:
        return Confidence.LOW
    if avg_log_prob is None:
        return Confidence.UNKNOWN
    if avg_log_prob >= -0.45:
        return Confidence.HIGH
    if avg_log_prob >= -0.9:
        return Confidence.MEDIUM
    return Confidence.LOW


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
                no_speech_score=0.05,
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
            condition_on_previous_text=False,
            vad_filter=True,
            word_timestamps=False,
            hallucination_silence_threshold=1.5,
            compression_ratio_threshold=2.2,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.6,
        )
        accepted: list[LyricsSegment] = []
        seen: dict[str, int] = {}
        filtered_low_quality = 0
        filtered_repetitions = 0
        for raw in raw_segments:
            if cancel_requested is not None and cancel_requested():
                raise RuntimeError("Lyrics transcription was cancelled.")
            text = str(raw.text).strip()[:1000]
            normalized = _normalized_phrase(text)
            no_speech = float(raw.no_speech_prob) if raw.no_speech_prob is not None else None
            avg_log_prob = float(raw.avg_logprob) if raw.avg_logprob is not None else None
            flags: list[str] = []
            if not normalized or len(normalized) < 2 or (no_speech is not None and no_speech >= 0.65) or (avg_log_prob is not None and avg_log_prob < -1.2):
                filtered_low_quality += 1
                continue
            seen[normalized] = seen.get(normalized, 0) + 1
            if seen[normalized] > 2:
                filtered_repetitions += 1
                continue
            confidence = _quality(avg_log_prob, no_speech)
            if confidence == Confidence.LOW:
                flags.append("uncertain_phrase")
            accepted.append(
                LyricsSegment(
                    id=str(uuid4()),
                    start_seconds=max(0.0, round(float(raw.start), 3)),
                    end_seconds=max(round(float(raw.end), 3), round(float(raw.start), 3) + 0.001),
                    text=text,
                    confidence=confidence,
                    no_speech_score=no_speech,
                    quality_flags=flags,
                )
            )
        created = datetime.now(UTC)
        warnings = [
            "This is an approximate transcript: singing, reverb, layering, vocal chops, and dense accompaniment can cause substantial errors."
        ]
        if filtered_low_quality:
            warnings.append(f"{filtered_low_quality} low-quality or no-speech segment(s) were withheld.")
        if filtered_repetitions:
            warnings.append(f"{filtered_repetitions} repeated hallucination-like segment(s) were withheld.")
        transcript = PrivateLyricsTranscript(
            job_id=job_id,
            language=str(info.language) if getattr(info, "language", None) else None,
            segments=accepted,
            model_id=self.settings.lyrics_model_name,
            selected_device=self._device,
            warnings=warnings,
            created_at=created,
        )
        total_span = max((segment.end_seconds for segment in accepted), default=0.0)
        word_count = sum(len(_normalized_phrase(segment.text).split()) for segment in accepted)
        words_per_minute = word_count * 60 / max(total_span, 1.0)
        density = "dense" if words_per_minute >= 110 else "moderate" if words_per_minute >= 45 else "sparse"
        language_probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        language_confidence = Confidence.HIGH if language_probability >= 0.8 else Confidence.MEDIUM if language_probability >= 0.5 else Confidence.LOW
        summary = LyricsAnalysisSummary(
            enabled=True,
            status="completed" if accepted else "no_reliable_words",
            adapter_id=self.adapter_id,
            model_id=self.settings.lyrics_model_name,
            selected_device=self._device,
            language=transcript.language,
            language_confidence=language_confidence,
            transcript_available=bool(accepted),
            segment_count=len(accepted),
            vocal_word_density=density if accepted else "unknown",
            non_lexical_vocalization_tendency="possible" if filtered_low_quality and not accepted else "unknown",
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

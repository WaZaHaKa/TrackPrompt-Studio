from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from scipy.signal import resample_poly

from ..analysis.core import load_audio
from ..config import Settings
from ..model_cache import verify_model_manifest
from ..schemas import (
    AnalysisResult,
    Confidence,
    GenreAnalysis,
    GenreCandidate,
    GenreWindowEvidence,
    ModelAdapterCapability,
)
from ..taxonomies import MusicStyleTaxonomy, load_music_style_taxonomy


class MusicTaggerAdapter(Protocol):
    adapter_id: str

    def capability(self) -> ModelAdapterCapability: ...
    def model_metadata(self) -> dict[str, str]: ...
    def analyze_global(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis: ...
    def analyze_windows(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis: ...
    def selected_device(self) -> str: ...
    def cleanup(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AudioWindow:
    id: str
    kind: str
    start: float
    end: float


def _candidate(
    item: Any,
    similarity: float,
    confidence: Confidence,
) -> GenreCandidate:
    return GenreCandidate(
        id=str(item.id),
        label=str(item.prompt_safe_label),
        canonical_label=str(item.prompt_safe_label),
        parent=getattr(item, "parent", None),
        similarity=round(float(similarity), 5),
        confidence=confidence,
    )


def _confidence(top: float, margin: float, agreement: float, duration: float) -> Confidence:
    if duration < 6 or top < 0.05 or margin < 0.008:
        return Confidence.LOW
    if top >= 0.18 and margin >= 0.025 and agreement >= 0.55:
        return Confidence.HIGH
    if top >= 0.09 and margin >= 0.012 and agreement >= 0.35:
        return Confidence.MEDIUM
    return Confidence.LOW


def _select_windows(analysis: AnalysisResult, window_seconds: float = 10.0) -> list[AudioWindow]:
    duration = analysis.file.duration_seconds
    leading = float(analysis.signal_quality.leading_silence_seconds.value or 0.0)
    trailing = float(analysis.signal_quality.trailing_silence_seconds.value or 0.0)
    usable_start = min(duration, max(0.0, leading))
    usable_end = max(usable_start, duration - max(0.0, trailing))
    usable_duration = usable_end - usable_start
    if usable_duration <= window_seconds:
        return [AudioWindow("window-1", "whole-track", usable_start, usable_end)]
    starts: list[tuple[str, float]] = [
        ("intro", usable_start),
        ("middle", usable_start + usable_duration / 2 - window_seconds / 2),
        ("outro", usable_end - window_seconds),
    ]
    energetic = [section for section in analysis.structure.sections if section.energy is not None]
    if energetic:
        high = max(energetic, key=lambda item: float(item.energy or 0.0))
        low = min(energetic, key=lambda item: float(item.energy or 0.0))
        starts.extend(
            [
                ("high-energy", (high.start_seconds + high.end_seconds - window_seconds) / 2),
                ("low-energy", (low.start_seconds + low.end_seconds - window_seconds) / 2),
            ]
        )
    windows: list[AudioWindow] = []
    for kind, raw_start in starts:
        start = min(usable_end - window_seconds, max(usable_start, raw_start))
        end = min(usable_end, start + window_seconds)
        if end - start < 1.0 or any(abs(start - previous.start) < 1.0 for previous in windows):
            continue
        windows.append(AudioWindow(f"window-{len(windows) + 1}", kind, start, end))
    return windows[:5]


class FakeMusicTaggerAdapter:
    adapter_id = "fake-music-tagger"

    def capability(self) -> ModelAdapterCapability:
        return ModelAdapterCapability(
            id=self.adapter_id,
            name="Fake deterministic music tagger",
            installed=True,
            model_ready=True,
            available=True,
            enabled=True,
            reason="Deterministic test adapter is ready.",
            model_id="fake-clap-v1",
            selected_device="cpu",
            effective_device="cpu",
            taxonomy_version="1.0.0",
            license="Test-only",
        )

    def model_metadata(self) -> dict[str, str]:
        return {"modelId": "fake-clap-v1", "taxonomyVersion": "1.0.0"}

    def analyze_global(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis:
        return self.analyze_windows(decoded_path, analysis)

    def analyze_windows(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis:
        windows = _select_windows(analysis)
        evidence = [
            GenreWindowEvidence(
                id=window.id,
                kind=window.kind,
                start_seconds=window.start,
                end_seconds=window.end,
                top_labels=["electronic", "dance"],
                similarities={"electronic": 0.42, "dance": 0.35},
            )
            for window in windows
        ]
        return GenreAnalysis(
            broad_candidates=[
                GenreCandidate(id="electronic", label="electronic", canonical_label="electronic", similarity=0.42, confidence=Confidence.HIGH),
                GenreCandidate(id="dance", label="dance", canonical_label="dance", similarity=0.35, confidence=Confidence.MEDIUM),
            ],
            subgenre_candidates=[
                GenreCandidate(id="melodic-techno", label="melodic techno", canonical_label="melodic techno", parent="electronic", similarity=0.31, confidence=Confidence.MEDIUM),
                GenreCandidate(id="progressive-house", label="progressive house", canonical_label="progressive house", parent="dance", similarity=0.29, confidence=Confidence.MEDIUM),
            ],
            blend_candidates=["melodic techno with progressive-house influence"],
            descriptive_tags=[
                GenreCandidate(id="synthetic", label="synthetic", canonical_label="synthetic", similarity=0.38, confidence=Confidence.MEDIUM)
            ],
            window_evidence=evidence,
            confidence=Confidence.MEDIUM,
            ambiguity="Electronic and dance evidence overlap across the analyzed windows.",
            method="deterministic fake hierarchical audio-text similarity",
            model_id="fake-clap-v1",
            taxonomy_version="1.0.0",
            selected_device="cpu",
            agreement_across_windows=1.0,
        )

    def selected_device(self) -> str:
        return "cpu"

    def cleanup(self) -> None:
        return


class TransformersClapMusicTagger:
    adapter_id = "transformers-clap"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.taxonomy = load_music_style_taxonomy()
        self._model: Any | None = None
        self._processor: Any | None = None
        self._device = self._resolve_device()

    def _resolve_device(self) -> str:
        if self.settings.genre_device == "cpu":
            return "cpu"
        if importlib.util.find_spec("torch") is None:
            return "unavailable"
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else ("cpu" if self.settings.genre_device == "auto" else "unavailable")
        except (ImportError, RuntimeError, OSError):
            return "unavailable"

    def capability(self) -> ModelAdapterCapability:
        installed = importlib.util.find_spec("transformers") is not None and importlib.util.find_spec("torch") is not None
        verified, manifest_reason = verify_model_manifest(
            self.settings.genre_model_dir,
            self.settings.genre_model_id,
            self.settings.genre_model_revision,
        )
        ready = self.settings.enable_genre_tagger and installed and verified and self._device != "unavailable"
        if not self.settings.enable_genre_tagger:
            reason = "Disabled until ENABLE_GENRE_TAGGER=true and an explicitly installed model is verified."
        elif not installed:
            reason = "Transformers and CUDA-capable PyTorch are not installed."
        elif not verified:
            reason = manifest_reason
        elif self._device == "unavailable":
            reason = "The requested genre device is unavailable."
        else:
            reason = "The offline CLAP model and complete manifest are ready."
        return ModelAdapterCapability(
            id=self.adapter_id,
            name="Transformers CLAP music tagger",
            installed=installed,
            model_ready=verified,
            available=ready,
            enabled=self.settings.enable_genre_tagger,
            reason=reason,
            model_id=self.settings.genre_model_id,
            model_revision=self.settings.genre_model_revision,
            selected_device=self.settings.genre_device,
            effective_device=self._device if ready else "unavailable",
            disk_impact_mb=650,
            taxonomy_version=self.taxonomy.taxonomy_version,
            fallback_reason=None if ready else reason,
            features=["hierarchical genres", "subgenres", "descriptive tags", "window evidence"],
            license="Apache-2.0 model card and Transformers code",
        )

    def model_metadata(self) -> dict[str, str]:
        return {
            "modelId": self.settings.genre_model_id,
            "revision": self.settings.genre_model_revision,
            "taxonomyVersion": self.taxonomy.taxonomy_version,
        }

    def selected_device(self) -> str:
        return self._device

    def _load(self) -> tuple[Any, Any, Any]:
        if not self.capability().available:
            raise RuntimeError("The local genre adapter is unavailable.")
        import torch
        from transformers import ClapModel, ClapProcessor

        if self._model is None or self._processor is None:
            self._processor = ClapProcessor.from_pretrained(
                str(self.settings.genre_model_dir), local_files_only=True
            )
            self._model = ClapModel.from_pretrained(
                str(self.settings.genre_model_dir), local_files_only=True
            ).to(self._device)
            self._model.eval()
        return self._model, self._processor, torch

    def _similarities(self, samples: np.ndarray, labels: Sequence[tuple[str, str]]) -> dict[str, float]:
        model, processor, torch = self._load()
        audio = resample_poly(samples.astype(np.float32), 3, 1).astype(np.float32)
        descriptions = [description for _label, description in labels]
        with torch.inference_mode():
            audio_inputs = processor(audios=audio, sampling_rate=48_000, return_tensors="pt")
            text_inputs = processor(text=descriptions, return_tensors="pt", padding=True)
            audio_inputs = {key: value.to(self._device) for key, value in audio_inputs.items()}
            text_inputs = {key: value.to(self._device) for key, value in text_inputs.items()}
            audio_features = model.get_audio_features(**audio_inputs)
            text_features = model.get_text_features(**text_inputs)
            audio_features = audio_features / audio_features.norm(dim=-1, keepdim=True)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
            scores = (audio_features @ text_features.T).squeeze(0).detach().cpu().tolist()
        return {label: float(score) for (label, _description), score in zip(labels, scores, strict=True)}

    def _aggregate(
        self,
        audio: Any,
        windows: list[AudioWindow],
        labels: Sequence[tuple[str, str]],
    ) -> tuple[dict[str, float], list[dict[str, float]]]:
        rows: list[dict[str, float]] = []
        for window in windows:
            start = int(window.start * audio.sample_rate)
            end = max(start + 1, int(window.end * audio.sample_rate))
            rows.append(self._similarities(audio.mono[start:end], labels))
        aggregated = {
            label: float(np.median([row[label] for row in rows]))
            for label, _description in labels
        }
        return aggregated, rows

    def analyze_global(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis:
        return self.analyze_windows(decoded_path, analysis)

    def analyze_windows(self, decoded_path: Path, analysis: AnalysisResult) -> GenreAnalysis:
        taxonomy: MusicStyleTaxonomy = self.taxonomy
        audio = load_audio(str(decoded_path))
        windows = _select_windows(analysis)
        broad_labels = [(item.id, item.description) for item in taxonomy.broad_genres]
        broad_scores, broad_rows = self._aggregate(audio, windows, broad_labels)
        broad_ranked = sorted(taxonomy.broad_genres, key=lambda item: broad_scores[item.id], reverse=True)
        parent_ids = {item.id for item in broad_ranked[:3]}
        sub_items = [item for item in taxonomy.subgenres if item.parent in parent_ids]
        sub_scores, _sub_rows = self._aggregate(
            audio,
            windows,
            [(item.id, item.description) for item in sub_items],
        )
        sub_ranked = sorted(sub_items, key=lambda item: sub_scores[item.id], reverse=True)
        tag_scores, _tag_rows = self._aggregate(
            audio,
            windows,
            [(item.id, item.description) for item in taxonomy.descriptive_tags],
        )
        tag_ranked = sorted(taxonomy.descriptive_tags, key=lambda item: tag_scores[item.id], reverse=True)
        top_labels = [max(row, key=lambda label: row[label]) for row in broad_rows]
        agreement = max(top_labels.count(label) for label in set(top_labels)) / len(top_labels)
        top = broad_scores[broad_ranked[0].id]
        second = broad_scores[broad_ranked[1].id] if len(broad_ranked) > 1 else -1.0
        confidence = _confidence(top, top - second, agreement, audio.duration)
        evidence = [
            GenreWindowEvidence(
                id=window.id,
                kind=window.kind,
                start_seconds=round(window.start, 3),
                end_seconds=round(window.end, 3),
                top_labels=[item.prompt_safe_label for item in sorted(taxonomy.broad_genres, key=lambda entry: row[entry.id], reverse=True)[:3]],
                similarities={item.prompt_safe_label: round(row[item.id], 5) for item in taxonomy.broad_genres},
            )
            for window, row in zip(windows, broad_rows, strict=True)
        ]
        broad_candidates = [_candidate(item, broad_scores[item.id], confidence) for item in broad_ranked[:5]]
        sub_candidates = [_candidate(item, sub_scores[item.id], confidence) for item in sub_ranked[:6]]
        tags = [
            GenreCandidate(
                id=item.id,
                label=item.label,
                canonical_label=item.label,
                similarity=round(tag_scores[item.id], 5),
                confidence=confidence,
            )
            for item in tag_ranked[:5]
        ]
        available_labels = {item.label for item in broad_candidates + sub_candidates}
        blends = [
            f"{left} with {right} influence"
            for left, right in taxonomy.compatible_blends
            if left in available_labels and right in available_labels
        ][:2]
        ambiguity = None
        if confidence in {Confidence.LOW, Confidence.UNKNOWN} or top - second < 0.02:
            ambiguity = "Top audio-text similarities are close; treat the ranking as ambiguous rather than a probability result."
        return GenreAnalysis(
            broad_candidates=broad_candidates,
            subgenre_candidates=sub_candidates,
            blend_candidates=blends,
            descriptive_tags=tags,
            window_evidence=evidence,
            confidence=confidence,
            ambiguity=ambiguity,
            method="hierarchical CLAP cosine similarity over bounded silence-trimmed windows; similarities are not calibrated probabilities",
            model_id=self.settings.genre_model_id,
            taxonomy_version=taxonomy.taxonomy_version,
            selected_device=self._device,
            agreement_across_windows=round(agreement, 4),
            warnings=["Genre labels are similarity-ranked estimates and can be ambiguous for blends or unfamiliar styles."],
        )

    def cleanup(self) -> None:
        self._model = None
        self._processor = None
        if importlib.util.find_spec("torch") is not None:
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except (ImportError, RuntimeError):
                pass


def create_music_tagger(settings: Settings) -> MusicTaggerAdapter:
    return TransformersClapMusicTagger(settings)

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from ..privacy import secure_private_file
from ..schemas import Confidence, EvidenceKind, FeatureValue
from .core import AudioData, feature, load_audio


def create_temporary_accompaniment_view(
    stems: dict[str, Path],
    destination: Path,
) -> Path:
    """Mix only accompaniment stems into a private, short-lived analysis view."""

    inputs = [load_audio(str(stems[name])) for name in ("drums", "bass", "other")]
    sample_rates = {item.sample_rate for item in inputs}
    if len(sample_rates) != 1:
        raise ValueError("Private accompaniment stems must share a sample rate.")
    frame_count = max(item.samples.shape[0] for item in inputs)
    channel_count = max(item.samples.shape[1] for item in inputs)
    mixed = np.zeros((frame_count, channel_count), dtype=np.float32)
    for item in inputs:
        samples = item.samples
        if samples.shape[1] == 1 and channel_count == 2:
            samples = np.repeat(samples, 2, axis=1)
        mixed[: samples.shape[0], : samples.shape[1]] += samples
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.98:
        mixed *= 0.98 / peak
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination, mixed, inputs[0].sample_rate, subtype="FLOAT")
    secure_private_file(destination)
    return destination


def _frame_rms(audio: AudioData, frame_seconds: float = 0.04, hop_seconds: float = 0.02) -> np.ndarray:
    frame = max(64, int(audio.sample_rate * frame_seconds))
    hop = max(32, int(audio.sample_rate * hop_seconds))
    if audio.mono.size < frame:
        padded = np.pad(audio.mono, (0, frame - audio.mono.size))
        return np.asarray([float(np.sqrt(np.mean(np.square(padded))))])
    return np.asarray(
        [
            float(np.sqrt(np.mean(np.square(audio.mono[start : start + frame]))))
            for start in range(0, audio.mono.size - frame + 1, hop)
        ],
        dtype=np.float64,
    )


def _active_run_durations(active: np.ndarray, hop_seconds: float = 0.02) -> list[float]:
    padded = np.pad(active.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return [float((end - start) * hop_seconds) for start, end in zip(starts, ends, strict=True)]


def _spectral_tonality(audio: AudioData, active: np.ndarray) -> float:
    frame = max(256, int(audio.sample_rate * 0.04))
    hop = max(128, int(audio.sample_rate * 0.02))
    scores: list[float] = []
    for index, enabled in enumerate(active):
        if not enabled:
            continue
        start = index * hop
        samples = audio.mono[start : start + frame]
        if samples.size < frame:
            continue
        power = np.square(np.abs(np.fft.rfft(samples * np.hanning(frame))))
        total = float(np.sum(power))
        if total > 1e-12:
            scores.append(float(np.max(power) / total))
    return float(np.median(scores)) if scores else 0.0


def _envelope_repetition(envelope: np.ndarray) -> float:
    centered = envelope - float(np.mean(envelope))
    denominator = float(np.dot(centered, centered))
    if denominator <= 1e-12:
        return 0.0
    correlations: list[float] = []
    for lag in range(25, min(200, centered.size // 2)):
        correlations.append(float(np.dot(centered[:-lag], centered[lag:]) / denominator))
    return max(correlations, default=0.0)


def analyze_vocal_delivery_view(
    vocal_stem: Path,
) -> tuple[FeatureValue[list[str]], FeatureValue[list[str]]]:
    """Classify non-identifying vocal delivery from acoustics, without transcription."""

    audio = load_audio(str(vocal_stem))
    envelope = _frame_rms(audio)
    peak = float(np.max(envelope)) if envelope.size else 0.0
    floor = float(np.percentile(envelope, 20)) if envelope.size else 0.0
    threshold = max(floor * 3.0, peak * 0.12, 1e-5)
    active = envelope >= threshold
    active_fraction = float(np.mean(active)) if active.size else 0.0
    runs = _active_run_durations(active)
    median_run = float(np.median(runs)) if runs else 0.0
    onset_rate = len(runs) / max(audio.duration, 1e-6)
    tonality = _spectral_tonality(audio, active)
    repetition = _envelope_repetition(envelope)

    delivery: list[str] = []
    phrasing: list[str] = []
    if active_fraction < 0.04:
        return (
            feature(
                [],
                Confidence.UNKNOWN,
                "private vocal-stem acoustic activity was insufficient",
                warning="No delivery label was inferred from the private vocal stem.",
                evidence_kind=EvidenceKind.AMBIGUOUS,
            ),
            feature(
                [],
                Confidence.UNKNOWN,
                "private vocal-stem acoustic activity was insufficient",
                evidence_kind=EvidenceKind.AMBIGUOUS,
            ),
        )
    if active_fraction < 0.22 and median_run < 0.35:
        delivery.append("sparse vocal chops")
    if onset_rate >= 1.7 and tonality < 0.16:
        delivery.append("spoken-rhythmic")
        phrasing.append("short rhythmic phrases")
    elif tonality >= 0.16:
        delivery.extend(["sung", "melodic vocal"])
    else:
        delivery.append("spoken-rhythmic")
    if median_run >= 0.65:
        phrasing.append("sustained phrases")
    if repetition >= 0.42:
        phrasing.append("hook-like repetition")
    if not phrasing:
        phrasing.append("sectional phrases")

    confidence = Confidence.MEDIUM if active_fraction >= 0.12 else Confidence.LOW
    method = (
        "private vocal-stem activity, phrase-run, onset-rate, spectral-tonality, "
        "and envelope-repetition heuristics; no transcript or identity inference"
    )
    return (
        feature(
            list(dict.fromkeys(delivery)),
            confidence,
            method,
            score=round(active_fraction, 3),
            evidence_kind=EvidenceKind.STRONG_ESTIMATE,
        ),
        feature(
            list(dict.fromkeys(phrasing)),
            confidence,
            method,
            score=round(repetition, 3),
            evidence_kind=EvidenceKind.STRONG_ESTIMATE,
        ),
    )

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy import signal

from ..schemas import (
    ChordSegment,
    Confidence,
    EvidenceKind,
    FeatureValue,
    HarmonyAnalysis,
    InstrumentationAnalysis,
    InstrumentCandidate,
    MelodyAnalysis,
    ProductionAnalysis,
    RhythmAnalysis,
    Section,
    SignalQuality,
    StructureAnalysis,
    StyleAndMoodAnalysis,
    TimbreAnalysis,
    VocalsAnalysis,
)
from .confidence import classify_key_confidence, classify_tempo_confidence

FloatArray = NDArray[np.floating[Any]]
T = TypeVar("T")
EPSILON = float(np.finfo(np.float32).eps)


def feature(
    value: T | None,
    confidence: Confidence,
    method: str,
    *,
    score: float | None = None,
    alternatives: list[Any] | None = None,
    warning: str | None = None,
    evidence_kind: EvidenceKind | None = None,
) -> FeatureValue[T]:
    if evidence_kind is None:
        normalized_method = method.casefold()
        if value is None or confidence == Confidence.UNKNOWN:
            evidence_kind = EvidenceKind.UNAVAILABLE
        elif "proxy" in normalized_method:
            evidence_kind = EvidenceKind.PROXY
        elif confidence == Confidence.HIGH and any(
            token in normalized_method
            for token in ("sample", "rms", "mean", "pearson", "measured", "direct")
        ):
            evidence_kind = EvidenceKind.DIRECT_MEASUREMENT
        elif confidence == Confidence.HIGH:
            evidence_kind = EvidenceKind.STRONG_ESTIMATE
        else:
            evidence_kind = EvidenceKind.HEURISTIC
    return FeatureValue[T](
        value=value,
        confidence=confidence,
        score=score,
        method=method,
        alternatives=alternatives or [],
        warning=warning,
        evidence_kind=evidence_kind,
    )


def unavailable(method: str, warning: str) -> FeatureValue[Any]:
    return feature(None, Confidence.UNKNOWN, method, warning=warning)


@dataclass(slots=True)
class AudioData:
    samples: FloatArray
    mono: FloatArray
    sample_rate: int
    duration: float
    decoded_min: float
    decoded_max: float
    normalization_violation: bool


@dataclass(slots=True)
class SpectralData:
    frequencies: FloatArray
    times: FloatArray
    magnitude: FloatArray
    hop_seconds: float


def load_audio(path: str) -> AudioData:
    raw, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if raw.size == 0 or sample_rate <= 0:
        raise ValueError("Decoded audio is empty.")
    raw = np.asarray(raw, dtype=np.float32)
    nonfinite = ~np.isfinite(raw)
    if np.any(nonfinite):
        raw[nonfinite] = 0.0
    if raw.shape[1] > 2:
        raw = raw[:, :2]
    decoded_min = float(np.min(raw))
    decoded_max = float(np.max(raw))
    normalization_violation = max(abs(decoded_min), abs(decoded_max)) > 1.0001
    mono = np.mean(raw, axis=1)
    return AudioData(
        samples=raw,
        mono=mono,
        sample_rate=int(sample_rate),
        duration=float(raw.shape[0] / sample_rate),
        decoded_min=decoded_min,
        decoded_max=decoded_max,
        normalization_violation=normalization_violation,
    )


def spectral_features(audio: AudioData) -> SpectralData:
    # Pad pathological sub-frame signals so SciPy never silently shrinks nperseg
    # below noverlap. The original duration remains unchanged in AudioData.
    working = audio.mono
    if working.size < 64:
        working = np.pad(working, (0, 64 - working.size))
    nperseg = min(2048, max(32, int(2 ** math.floor(math.log2(working.size)))))
    noverlap = min(nperseg - 1, int(nperseg * 0.5))
    frequencies, times, transformed = signal.stft(
        working,
        fs=audio.sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
        boundary=None,
        padded=False,
    )
    magnitude = np.abs(transformed).astype(np.float32)
    hop_seconds = (nperseg - noverlap) / audio.sample_rate
    return SpectralData(
        frequencies=np.asarray(frequencies, dtype=np.float32),
        times=np.asarray(times, dtype=np.float32),
        magnitude=magnitude,
        hop_seconds=float(hop_seconds),
    )


def estimated_peak_analysis_bytes(
    duration_seconds: float,
    sample_rate: int = 16_000,
    channels: int = 2,
) -> int:
    """Conservative float32/complex64 peak estimate for capacity guard tests."""
    sample_count = max(1, int(duration_seconds * sample_rate))
    audio_bytes = sample_count * max(1, min(channels, 2)) * 4
    mono_bytes = sample_count * 4
    stft_frames = math.ceil(sample_count / 1024)
    magnitude_bytes = stft_frames * 1025 * 4
    # During STFT construction both complex64 coefficients and float32
    # magnitudes coexist briefly: about three magnitude matrices total.
    estimate = audio_bytes + mono_bytes + magnitude_bytes * 3
    return int(estimate * 1.3)


def dbfs(amplitude: float) -> float:
    return 20.0 * math.log10(max(abs(amplitude), 1e-12))


def _frame_rms(samples: FloatArray, frame: int, hop: int) -> FloatArray:
    if samples.size < frame:
        return np.asarray([math.sqrt(max(float(np.mean(samples * samples)), 0.0))], dtype=np.float64)
    values = [
        math.sqrt(max(float(np.mean(samples[start : start + frame] ** 2)), 0.0))
        for start in range(0, samples.size - frame + 1, hop)
    ]
    return np.asarray(values, dtype=np.float64)


def _activity_frame_rms(audio: AudioData, frame: int, hop: int) -> FloatArray:
    """Return the loudest channel RMS for each frame.

    Using the loudest channel avoids treating anti-phase stereo or material
    panned primarily to one side as silence. It is an activity measurement, not
    a loudness downmix.
    """
    channels = [
        _frame_rms(np.asarray(audio.samples[:, index]), frame, hop)
        for index in range(audio.samples.shape[1])
    ]
    length = min(channel.size for channel in channels)
    return np.max(np.vstack([channel[:length] for channel in channels]), axis=0)


def _first_sustained_activity(
    rms_db: FloatArray,
    threshold_db: float,
    hop_seconds: float,
    *,
    reverse: bool = False,
) -> int | None:
    """Locate sustained edge activity with a lower hysteresis hold threshold."""
    values = rms_db[::-1] if reverse else rms_db
    start_threshold = threshold_db
    hold_threshold = threshold_db - 6.0
    minimum_frames = max(3, int(math.ceil(0.20 / max(hop_seconds, 1e-6))))
    sparse_window_frames = max(minimum_frames, int(math.ceil(1.25 / max(hop_seconds, 1e-6))))
    for start in np.flatnonzero(values > start_threshold):
        end = min(values.size, int(start) + minimum_frames)
        if end - int(start) < minimum_frames:
            continue
        window = values[int(start) : end]
        # One click cannot establish an active edge. Most of the following
        # frames must remain above the hysteresis threshold.
        sparse_end = min(values.size, int(start) + sparse_window_frames)
        sparse_indices = np.flatnonzero(values[int(start) : sparse_end] > start_threshold)
        repeated_sparse_activity = (
            sparse_indices.size >= 3
            and int(sparse_indices[-1] - sparse_indices[0]) * hop_seconds >= 0.25
        )
        if float(np.mean(window > hold_threshold)) >= 0.75 or repeated_sparse_activity:
            original = values.size - 1 - int(start) if reverse else int(start)
            return original
    return None


def signal_quality(audio: AudioData) -> SignalQuality:
    frame = max(128, int(audio.sample_rate * 0.05))
    hop = max(64, frame // 2)
    rms = _activity_frame_rms(audio, frame, hop)
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    noise_floor = float(np.percentile(rms_db, 10))
    # The adaptive estimate separates a stable noise floor from activity, but
    # hard bounds keep a mastered track from raising its own silence threshold
    # and keep digital silence from requiring an infinitesimal comparison.
    threshold = min(-50.0, max(-70.0, noise_floor + 8.0))
    hop_seconds = hop / audio.sample_rate
    first_active = _first_sustained_activity(rms_db, threshold, hop_seconds)
    last_active = _first_sustained_activity(
        rms_db,
        threshold,
        hop_seconds,
        reverse=True,
    )
    if first_active is not None and last_active is not None and last_active >= first_active:
        leading = float(first_active * hop_seconds)
        trailing = float(
            max(0.0, audio.duration - ((last_active * hop + frame) / audio.sample_rate))
        )
    else:
        leading = audio.duration
        # For fully inactive material, assign the duration to one edge so the
        # direct measurements remain physically consistent (their sum cannot
        # exceed the track duration).
        trailing = 0.0
    effective_rms = math.sqrt(max(float(np.mean(audio.mono * audio.mono)), 0.0))
    effective_db = dbfs(effective_rms)
    clipped_fraction = float(np.mean(np.abs(audio.samples) >= 0.999))
    dc = float(np.mean(audio.mono))
    active_fraction = float(np.mean(rms_db > threshold))
    sufficient = (
        first_active is not None
        and effective_db > -70.0
        and active_fraction > 0.01
        and float(np.std(audio.samples)) > 1e-5
    )
    if audio.samples.shape[1] >= 2:
        left = audio.samples[:, 0]
        right = audio.samples[:, 1]
        if np.std(left) < 1e-9 or np.std(right) < 1e-9:
            correlation = 1.0
        else:
            correlation = float(np.corrcoef(left, right)[0, 1])
            if not math.isfinite(correlation):
                correlation = 0.0
    else:
        correlation = 1.0
    return SignalQuality(
        leading_silence_seconds=feature(
            round(leading, 3),
            Confidence.HIGH,
            "contiguous edge activity from 50 ms per-channel RMS frames",
            evidence_kind=EvidenceKind.DIRECT_MEASUREMENT,
        ),
        trailing_silence_seconds=feature(
            round(trailing, 3),
            Confidence.HIGH,
            "contiguous edge activity from 50 ms per-channel RMS frames",
            evidence_kind=EvidenceKind.DIRECT_MEASUREMENT,
        ),
        clipping=feature(
            clipped_fraction > 0.0005 or audio.normalization_violation,
            Confidence.HIGH,
            "sample occupancy at or above -0.009 dBFS plus decoded-range validation",
            score=round(clipped_fraction, 6),
            warning=(
                "Decoded samples exceeded normalized full scale."
                if audio.normalization_violation
                else None
            ),
        ),
        dc_offset=feature(round(dc, 6), Confidence.HIGH, "mean normalized sample amplitude"),
        noise_floor_dbfs=feature(round(noise_floor, 2), Confidence.MEDIUM, "10th percentile short-window RMS proxy"),
        effective_level_dbfs=feature(round(effective_db, 2), Confidence.HIGH, "whole-track RMS"),
        phase_correlation=feature(
            round(correlation, 3),
            Confidence.HIGH if audio.samples.shape[1] >= 2 else Confidence.UNKNOWN,
            "Pearson correlation between decoded stereo channels",
            warning="Mono source; stereo phase correlation is not applicable." if audio.samples.shape[1] == 1 else None,
        ),
        sufficient_signal=feature(
            sufficient,
            Confidence.HIGH,
            "RMS, active-frame fraction, and variance thresholds",
            warning=None if sufficient else "Insufficient non-silent signal for reliable musical analysis.",
        ),
        activity_threshold_dbfs=feature(
            round(threshold, 2),
            Confidence.HIGH,
            "bounded adaptive activity threshold (-70 to -50 dBFS)",
            evidence_kind=EvidenceKind.DIRECT_MEASUREMENT,
        ),
        decoded_sample_range=feature(
            [round(audio.decoded_min, 6), round(audio.decoded_max, 6)],
            Confidence.HIGH if not audio.normalization_violation else Confidence.LOW,
            "minimum and maximum decoded normalized sample values",
            warning=(
                "Decoded samples exceeded the expected normalized range; peak reporting is withheld."
                if audio.normalization_violation
                else None
            ),
            evidence_kind=(
                EvidenceKind.AMBIGUOUS
                if audio.normalization_violation
                else EvidenceKind.DIRECT_MEASUREMENT
            ),
        ),
    )


def waveform_peaks(audio: AudioData, points: int = 1200) -> list[float]:
    count = min(points, max(1, audio.mono.size))
    edges = np.linspace(0, audio.mono.size, count + 1, dtype=np.int64)
    peaks = [float(np.max(np.abs(audio.mono[edges[index] : edges[index + 1]]))) for index in range(count)]
    maximum = max(peaks, default=1.0)
    scale = maximum if maximum > 1e-12 else 1.0
    return [round(value / scale, 4) for value in peaks]


def _onset_envelope(spectral: SpectralData) -> FloatArray:
    if spectral.magnitude.shape[1] < 2:
        return np.zeros(1, dtype=np.float64)
    flux = np.maximum(0.0, np.diff(spectral.magnitude, axis=1)).sum(axis=0)
    flux = np.concatenate(([0.0], flux))
    if flux.size >= 3:
        flux = np.convolve(flux, np.asarray([0.25, 0.5, 0.25], dtype=np.float32), mode="same")
    flux -= np.min(flux)
    maximum = float(np.max(flux))
    return np.asarray(flux / maximum if maximum > EPSILON else flux, dtype=np.float64)


def _tempo_candidates(onsets: FloatArray, frame_rate: float) -> list[tuple[float, float]]:
    centered = onsets - float(np.mean(onsets))
    if centered.size < 8 or np.max(np.abs(centered)) < 1e-8:
        return []
    correlation = signal.correlate(centered, centered, mode="full", method="fft")[centered.size - 1 :]
    correlation /= np.maximum(1, np.arange(centered.size, 0, -1))
    min_lag = max(1, int(frame_rate * 60.0 / 220.0))
    max_lag = min(correlation.size - 1, int(frame_rate * 60.0 / 40.0))
    if max_lag <= min_lag:
        return []
    region = correlation[min_lag : max_lag + 1]
    peaks, _ = signal.find_peaks(region, distance=max(1, int(frame_rate * 60 / 220 / 2)))
    if peaks.size == 0:
        peaks = np.asarray([int(np.argmax(region))])
    candidates: list[tuple[float, float]] = []
    denominator = max(float(correlation[0]), EPSILON)
    for peak in peaks:
        lag = int(peak + min_lag)
        refined_lag = float(lag)
        if 1 <= lag < correlation.size - 1:
            left = float(correlation[lag - 1])
            center = float(correlation[lag])
            right = float(correlation[lag + 1])
            denominator_curve = left - 2.0 * center + right
            if abs(denominator_curve) > EPSILON:
                offset = 0.5 * (left - right) / denominator_curve
                if abs(offset) <= 1.0:
                    refined_lag += offset
        bpm = 60.0 * frame_rate / refined_lag
        strength = max(0.0, float(correlation[lag] / denominator))
        # Prefer the conventional display range while retaining all octave candidates.
        display_bias = 1.08 if 75.0 <= bpm <= 165.0 else 1.0
        candidates.append((bpm, strength * display_bias))
    return sorted(candidates, key=lambda item: item[1], reverse=True)


def _fit_beat_grid(
    onset_times: FloatArray,
    bpm: float,
    duration: float,
) -> tuple[FloatArray, float, float]:
    """Fit a constant musical-pulse grid to onset evidence.

    The returned timestamps are beats at the selected BPM, never the complete
    onset list. Alignment combines grid coverage and robust timing error.
    Temporal consistency compares coverage across coarse track windows.
    """
    if onset_times.size == 0 or bpm <= 0 or not math.isfinite(bpm):
        return np.asarray([], dtype=np.float64), 0.0, 0.0
    interval = 60.0 / bpm
    phases = np.unique(np.mod(onset_times[: min(400, onset_times.size)], interval))
    if phases.size > 80:
        phases = phases[np.linspace(0, phases.size - 1, 80, dtype=np.int64)]

    def grid_for(phase: float) -> FloatArray:
        first = phase - math.ceil(phase / interval) * interval
        return np.arange(first, duration + interval, interval, dtype=np.float64)

    def score_grid(grid: FloatArray) -> float:
        inside = grid[(grid >= 0.0) & (grid <= duration)]
        if inside.size == 0:
            return 0.0
        indices = np.searchsorted(onset_times, inside)
        right = onset_times[np.minimum(indices, onset_times.size - 1)]
        left = onset_times[np.maximum(indices - 1, 0)]
        distances = np.minimum(np.abs(right - inside), np.abs(left - inside))
        tolerance = max(0.035, interval * 0.18)
        coverage = float(np.mean(distances <= tolerance))
        timing = float(np.mean(np.exp(-np.square(distances / max(tolerance, EPSILON)))))
        return 0.65 * coverage + 0.35 * timing

    best_phase = 0.0
    best_score = -1.0
    for phase in phases:
        candidate_score = score_grid(grid_for(float(phase)))
        if candidate_score > best_score:
            best_phase = float(phase)
            best_score = candidate_score
    grid = grid_for(best_phase)
    grid = grid[(grid >= 0.0) & (grid <= duration)]
    window_scores: list[float] = []
    for start, end in zip(
        np.linspace(0.0, duration, 5)[:-1],
        np.linspace(0.0, duration, 5)[1:],
        strict=False,
    ):
        local_grid = grid[(grid >= start) & (grid < end)]
        local_onsets = onset_times[(onset_times >= start) & (onset_times < end)]
        if local_grid.size < 2 or local_onsets.size == 0:
            continue
        indices = np.searchsorted(local_onsets, local_grid)
        right = local_onsets[np.minimum(indices, local_onsets.size - 1)]
        left = local_onsets[np.maximum(indices - 1, 0)]
        distances = np.minimum(np.abs(right - local_grid), np.abs(left - local_grid))
        window_scores.append(float(np.mean(distances <= max(0.035, interval * 0.18))))
    consistency = 0.0
    if window_scores:
        consistency = float(max(0.0, np.mean(window_scores) - np.std(window_scores)))
    return grid, max(0.0, best_score), consistency


def analyze_rhythm(audio: AudioData, spectral: SpectralData) -> RhythmAnalysis:
    onsets = _onset_envelope(spectral)
    frame_rate = 1.0 / spectral.hop_seconds
    candidates = _tempo_candidates(onsets, frame_rate)
    prominence = max(0.05, float(np.std(onsets)))
    min_onset_distance = max(1, int(frame_rate * 60 / 300))
    onset_indices, _ = signal.find_peaks(
        onsets,
        prominence=prominence * 0.4,
        distance=min_onset_distance,
    )
    onset_times = (
        spectral.times[np.minimum(onset_indices, spectral.times.size - 1)]
        if spectral.times.size
        else np.asarray([], dtype=np.float64)
    )
    rms_frame = max(32, int(audio.sample_rate * 0.02))
    rms_hop = max(16, int(audio.sample_rate * 0.01))
    accent_envelope = _frame_rms(audio.mono, rms_frame, rms_hop)
    transient_indices, _ = signal.find_peaks(
        accent_envelope,
        prominence=max(float(np.max(accent_envelope)) * 0.04, 1e-8),
        distance=max(1, int(0.25 * audio.sample_rate / rms_hop)),
    )
    transient_times = transient_indices * rms_hop / audio.sample_rate
    transient_tempo: float | None = None
    transient_cv = 1.0
    if transient_times.size >= 4:
        intervals = np.diff(transient_times)
        median_interval = float(np.median(intervals))
        transient_cv = float(np.median(np.abs(intervals - median_interval)) / max(median_interval, EPSILON))
        if 0.25 <= median_interval <= 1.5:
            transient_tempo = 60.0 / median_interval
            while transient_tempo < 75.0:
                transient_tempo *= 2.0
            while transient_tempo > 165.0:
                transient_tempo /= 2.0
    if candidates:
        bpm, strength = candidates[0]
        octave_promoted = False
        if bpm < 75.0 and 75.0 <= bpm * 2.0 <= 165.0:
            bpm *= 2.0
            octave_promoted = True
        elif bpm > 165.0 and 75.0 <= bpm / 2.0 <= 165.0:
            bpm /= 2.0
            octave_promoted = True
        estimator_agreement = (
            transient_tempo is not None
            and transient_cv < 0.2
            and abs(transient_tempo - bpm) / max(bpm, EPSILON) < 0.08
        )
        if estimator_agreement and transient_tempo is not None:
            bpm = transient_tempo
        octave_values = [bpm / 2.0, bpm * 2.0]
        retained: list[dict[str, float]] = []
        for value in octave_values:
            if 35.0 <= value <= 260.0:
                retained.append({"bpm": round(value, 1), "relationship": 0.5 if value < bpm else 2.0})
        for value, candidate_strength in candidates[1:4]:
            if all(abs(value - existing["bpm"]) > 3.0 for existing in retained):
                retained.append({"bpm": round(value, 1), "autocorrelationStrength": round(candidate_strength, 3)})
        beat_times, grid_alignment, temporal_consistency = _fit_beat_grid(
            onset_times,
            bpm,
            audio.duration,
        )
        tempo_confidence = classify_tempo_confidence(
            autocorrelation_strength=strength,
            estimator_agreement=estimator_agreement,
            grid_alignment=grid_alignment,
            temporal_consistency=temporal_consistency,
            duration_seconds=audio.duration,
            octave_normalized_without_agreement=octave_promoted and not estimator_agreement,
        )
        bpm_feature = feature(
            round(bpm, 1),
            tempo_confidence,
            "spectral-flux autocorrelation reconciled with short-RMS transient intervals",
            score=round(min(strength, 1.0), 3),
            alternatives=retained,
            warning=(
                "The strongest periodicity was octave-normalized into the conventional tempo range; half/double-time alternatives are retained."
                if octave_promoted
                else "Tempo can be perceived at half or double this pulse."
                if retained
                else None
            ),
        )
    elif transient_tempo is not None and transient_cv < 0.15:
        beat_times, grid_alignment, temporal_consistency = _fit_beat_grid(
            onset_times,
            transient_tempo,
            audio.duration,
        )
        bpm_feature = feature(
            round(transient_tempo, 1),
            Confidence.MEDIUM,
            "robust short-RMS transient intervals with octave normalization",
            score=round(transient_cv, 3),
            alternatives=[
                {"bpm": round(transient_tempo / 2.0, 1), "relationship": 0.5},
                {"bpm": round(transient_tempo * 2.0, 1), "relationship": 2.0},
            ],
            warning="Tempo can be perceived at half or double this pulse.",
        )
    else:
        beat_times = np.asarray([], dtype=np.float64)
        grid_alignment = 0.0
        temporal_consistency = 0.0
        bpm_feature = feature(
            None,
            Confidence.UNKNOWN,
            "spectral-flux autocorrelation",
            warning="No stable periodic onset pattern was found.",
        )

    if beat_times.size >= 3:
        variability = 1.0 - temporal_consistency
        stability = (
            "stable"
            if temporal_consistency >= 0.72
            else "moderately variable"
            if temporal_consistency >= 0.45
            else "variable"
        )
        regularity = "regular" if grid_alignment >= 0.7 else "mixed" if grid_alignment >= 0.42 else "irregular"
    else:
        variability = 1.0
        stability = "unknown"
        regularity = "unknown"
    onset_density_value = float(onset_indices.size / max(audio.duration, EPSILON))
    meter_value = "unknown"
    meter_confidence = Confidence.UNKNOWN
    meter_score: float | None = None
    meter_warning = "No stable accent cycle distinguished 3/4 from 4/4."
    if bpm_feature.value is not None:
        # Short time-domain RMS peaks preserve click/accent strength more reliably
        # than the normalized spectral-flux curve. Compare periodicity in that
        # accent sequence at lags of three and four beats.
        beat_seconds = 60.0 / float(bpm_feature.value)
        accent_indices, _ = signal.find_peaks(
            accent_envelope,
            prominence=max(float(np.max(accent_envelope)) * 0.04, 1e-8),
            distance=max(1, int(beat_seconds * 0.55 * audio.sample_rate / rms_hop)),
        )
        accent = accent_envelope[accent_indices]
        if accent.size >= 8 and float(np.std(accent)) > max(float(np.mean(accent)) * 0.04, 1e-8):
            cycle_scores: dict[int, float] = {}
            for cycle in (3, 4):
                if accent.size > cycle and np.std(accent[:-cycle]) > EPSILON and np.std(accent[cycle:]) > EPSILON:
                    correlation = float(np.corrcoef(accent[:-cycle], accent[cycle:])[0, 1])
                    cycle_scores[cycle] = correlation if math.isfinite(correlation) else 0.0
                else:
                    cycle_scores[cycle] = 0.0
            winner = max(cycle_scores, key=lambda cycle: cycle_scores[cycle])
            loser = 4 if winner == 3 else 3
            if cycle_scores[winner] > 0.18 and cycle_scores[winner] - cycle_scores[loser] > 0.08:
                pulse_rate = float(bpm_feature.value) / 60.0
                subdivision_ratio = onset_density_value / max(pulse_rate, EPSILON)
                if winner == 3 and 1.65 <= subdivision_ratio <= 2.35:
                    meter_warning = (
                        "The accent cycle is ambiguous between simple triple and compound 6/8; meter is withheld."
                    )
                else:
                    meter_value = f"{winner}/4 (approximate)"
                    meter_confidence = Confidence.LOW
                    meter_score = round(cycle_scores[winner], 3)
                    meter_warning = (
                        "Meter is inferred from a repeating RMS accent cycle and remains approximate."
                    )
    descriptors: list[str] = []
    if bpm_feature.value is not None:
        if float(bpm_feature.value) >= 135:
            descriptors.append("driving")
        elif float(bpm_feature.value) <= 75:
            descriptors.append("laid-back")
        else:
            descriptors.append("steady")
    descriptors.append("busy" if onset_density_value > 4.0 else "sparse" if onset_density_value < 1.0 else "moderate")
    percussive = "pronounced" if float(np.mean(onsets > 0.4)) > 0.08 else "moderate" if np.max(onsets) > 0.2 else "soft"
    return RhythmAnalysis(
        bpm=bpm_feature,
        tempo_stability=feature(
            stability,
            Confidence.MEDIUM if beat_times.size >= 3 else Confidence.UNKNOWN,
            "beat-grid coverage consistency across track windows",
            score=round(variability, 3) if beat_times.size >= 3 else None,
        ),
        beat_timestamps=feature(
            [round(float(value), 3) for value in beat_times[:2000]],
            bpm_feature.confidence if beat_times.size else Confidence.UNKNOWN,
            "constant pulse grid phase-aligned to spectral-flux onsets at the selected BPM",
            warning=None if beat_times.size else "No defensible beat grid was available.",
            evidence_kind=EvidenceKind.STRONG_ESTIMATE if beat_times.size else EvidenceKind.UNAVAILABLE,
        ),
        downbeat_likelihood=feature("unknown", Confidence.UNKNOWN, "no trained downbeat model", warning="Downbeats are not asserted by the Fast analyzer."),
        meter=feature(meter_value, meter_confidence, "lag-3 versus lag-4 onset-accent cycle comparison", score=meter_score, warning=meter_warning),
        onset_density=feature(round(onset_density_value, 3), Confidence.MEDIUM, "spectral-flux peaks per second"),
        rhythmic_regularity=feature(
            regularity,
            Confidence.MEDIUM if beat_times.size >= 3 else Confidence.UNKNOWN,
            "onset agreement with the inferred musical-pulse grid",
        ),
        swing_tendency=feature("not established", Confidence.LOW, "subdivision timing proxy", warning="Swing requires clearer subdivisions than were available."),
        syncopation_tendency=feature("not established", Confidence.LOW, "onset-to-grid proxy", warning="Syncopation is approximate in Fast mode."),
        percussiveness=feature(percussive, Confidence.MEDIUM, "normalized spectral-flux activity"),
        groove_descriptors=feature(descriptors, Confidence.MEDIUM, "tempo, onset-density, and regularity rules"),
        onset_timestamps=feature(
            [round(float(value), 3) for value in onset_times[:4000]],
            Confidence.MEDIUM if onset_times.size else Confidence.UNKNOWN,
            "spectral-flux peak picking (transient/onset evidence, not beats)",
        ),
        beat_grid_alignment=feature(
            round(grid_alignment, 3) if beat_times.size else None,
            Confidence.MEDIUM if beat_times.size else Confidence.UNKNOWN,
            "grid coverage and robust onset timing fit on a 0-to-1 evidence scale",
            score=round(grid_alignment, 3) if beat_times.size else None,
            warning="This is an evidence score, not a probability.",
            evidence_kind=EvidenceKind.STRONG_ESTIMATE if grid_alignment >= 0.62 else EvidenceKind.HEURISTIC,
        ),
    )


PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MAJOR_PROFILE = np.asarray([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.asarray([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def chromagram(spectral: SpectralData) -> FloatArray:
    chroma = np.zeros((12, spectral.magnitude.shape[1]), dtype=np.float64)
    valid = (spectral.frequencies >= 55.0) & (spectral.frequencies <= 5000.0)
    frequencies = spectral.frequencies[valid]
    if frequencies.size == 0:
        return chroma
    midi = np.rint(69.0 + 12.0 * np.log2(frequencies / 440.0)).astype(np.int64)
    pitch_classes = np.mod(midi, 12)
    weighted = spectral.magnitude[valid] / np.sqrt(np.maximum(frequencies[:, None], 55.0))
    for pitch_class in range(12):
        mask = pitch_classes == pitch_class
        if np.any(mask):
            chroma[pitch_class] = np.sum(weighted[mask], axis=0)
    column_sums = np.sum(chroma, axis=0, keepdims=True)
    return chroma / np.maximum(column_sums, EPSILON)


def _correlation(a: FloatArray, b: FloatArray) -> float:
    if np.std(a) < EPSILON or np.std(b) < EPSILON:
        return 0.0
    value = float(np.corrcoef(a, b)[0, 1])
    return value if math.isfinite(value) else 0.0


def _merge_chords(labels: list[tuple[str | None, float, Confidence]], times: FloatArray, duration: float) -> list[ChordSegment]:
    if not labels:
        return []
    merged: list[ChordSegment] = []
    current_label, current_score, current_confidence = labels[0]
    start_index = 0
    for index in range(1, len(labels) + 1):
        label = labels[index][0] if index < len(labels) else "__end__"
        if label == current_label:
            continue
        start = float(times[start_index]) if times.size > start_index else 0.0
        end = float(times[index]) if times.size > index else duration
        if end - start >= 0.15:
            merged.append(
                ChordSegment(
                    chord=current_label,
                    start_seconds=round(max(0.0, start), 3),
                    end_seconds=round(min(duration, max(start, end)), 3),
                    confidence=current_confidence,
                )
            )
        if index < len(labels):
            current_label, current_score, current_confidence = labels[index]
            start_index = index
    return merged


def analyze_harmony(audio: AudioData, spectral: SpectralData) -> HarmonyAnalysis:
    chroma = chromagram(spectral)
    summary = np.mean(chroma, axis=1) if chroma.shape[1] else np.zeros(12)
    ranked: list[tuple[float, str, str]] = []
    for root in range(12):
        ranked.append((_correlation(summary, np.roll(MAJOR_PROFILE, root)), PITCH_NAMES[root], "major"))
        ranked.append((_correlation(summary, np.roll(MINOR_PROFILE, root)), PITCH_NAMES[root], "minor"))
    ranked.sort(reverse=True, key=lambda item: item[0])
    best_score, best_root, best_mode = ranked[0]
    second_score, second_root, second_mode = ranked[1]
    margin = best_score - second_score
    tonal_concentration = float(np.max(summary) - np.median(summary)) if summary.size else 0.0
    usable_frames = np.flatnonzero(np.sum(chroma, axis=0) > 0.5)
    usable_seconds = float(usable_frames.size * spectral.hop_seconds)
    window_winners: list[tuple[str, str]] = []
    window_frames = max(1, int(round(4.0 / spectral.hop_seconds)))
    for start in range(0, chroma.shape[1], window_frames):
        local = np.mean(chroma[:, start : start + window_frames], axis=1)
        if float(np.sum(local)) < 0.5:
            continue
        local_ranked: list[tuple[float, str, str]] = []
        for root in range(12):
            local_ranked.append(
                (_correlation(local, np.roll(MAJOR_PROFILE, root)), PITCH_NAMES[root], "major")
            )
            local_ranked.append(
                (_correlation(local, np.roll(MINOR_PROFILE, root)), PITCH_NAMES[root], "minor")
            )
        local_ranked.sort(reverse=True, key=lambda item: item[0])
        window_winners.append((local_ranked[0][1], local_ranked[0][2]))
    temporal_consistency = (
        float(np.mean([winner == (best_root, best_mode) for winner in window_winners]))
        if window_winners
        else 0.0
    )
    decision = classify_key_confidence(
        best_fit=best_score,
        runner_up_margin=margin,
        temporal_consistency=temporal_consistency,
        tonal_concentration=tonal_concentration,
        usable_seconds=usable_seconds,
    )
    tonal_confidence = decision.confidence
    tonal_label = decision.label
    alternatives = [
        {"key": root, "mode": mode, "templateFit": round(score, 3)} for score, root, mode in ranked[1:4]
    ]
    ambiguity_warning = decision.reason
    if decision.ambiguous and margin < 0.025:
        ambiguity_warning = (
            f"Ambiguous between {best_root} {best_mode} and {second_root} {second_mode}; "
            f"the uncalibrated template-fit margin is {margin:.3f}."
        )

    # Chord estimates use pitch-class templates over roughly one-second blocks.
    block_frames = max(1, int(round(1.0 / spectral.hop_seconds)))
    labels: list[tuple[str | None, float, Confidence]] = []
    chord_times: list[float] = []
    for start in range(0, chroma.shape[1], block_frames):
        block = np.mean(chroma[:, start : start + block_frames], axis=1)
        block_norm = float(np.linalg.norm(block))
        best_label: str | None = None
        best_fit = 0.0
        for root in range(12):
            for suffix, intervals in (("", (0, 4, 7)), ("m", (0, 3, 7))):
                template = np.zeros(12)
                template[list((root + np.asarray(intervals)) % 12)] = 1.0
                fit = float(np.dot(block, template) / max(block_norm * np.linalg.norm(template), EPSILON))
                if fit > best_fit:
                    best_fit = fit
                    best_label = f"{PITCH_NAMES[root]}{suffix}"
        if best_fit < 0.5 or block_norm < 0.02:
            best_label = None
            confidence = Confidence.UNKNOWN
        elif best_fit >= 0.72:
            confidence = Confidence.MEDIUM
        else:
            confidence = Confidence.LOW
        labels.append((best_label, best_fit, confidence))
        chord_times.append(float(spectral.times[start]) if spectral.times.size > start else start * spectral.hop_seconds)

    # Three-block majority smoothing removes isolated flips without forcing unknown blocks.
    if len(labels) >= 3:
        smoothed = labels.copy()
        for index in range(1, len(labels) - 1):
            if labels[index - 1][0] == labels[index + 1][0] and labels[index][0] != labels[index - 1][0]:
                smoothed[index] = labels[index - 1]
        labels = smoothed
    chords = _merge_chords(labels, np.asarray(chord_times), audio.duration)
    vocabulary = sorted({segment.chord for segment in chords if segment.chord})
    harmonic_rate = len(chords) / max(audio.duration, EPSILON)
    harmonic_rhythm = "active" if harmonic_rate > 0.5 else "moderate" if harmonic_rate > 0.2 else "slow-moving"
    major_count = sum(1 for chord in vocabulary if chord and not chord.endswith("m"))
    minor_count = sum(1 for chord in vocabulary if chord and chord.endswith("m"))
    balance = "minor-leaning" if minor_count > major_count else "major-leaning" if major_count > minor_count else "balanced or ambiguous"
    character = ["tonally stable" if not decision.ambiguous and best_score > 0.7 else "tonally fluid"]
    if not decision.ambiguous:
        if best_mode == "minor":
            character.append("minor-key color")
        else:
            character.append("major-key color")
    return HarmonyAnalysis(
        key=feature(
            best_root,
            tonal_confidence,
            "Krumhansl-Schmuckler pitch-class template correlation with temporal consistency",
            score=round(best_score, 3),
            alternatives=alternatives,
            warning=ambiguity_warning,
            evidence_kind=EvidenceKind.AMBIGUOUS if decision.ambiguous else None,
        ),
        mode=feature(
            best_mode,
            tonal_confidence,
            "joint major/minor pitch-class template fit",
            score=round(best_score, 3),
            alternatives=alternatives,
            warning=ambiguity_warning,
            evidence_kind=EvidenceKind.AMBIGUOUS if decision.ambiguous else None,
        ),
        tonal_confidence=feature(
            tonal_label,
            tonal_confidence,
            "absolute fit, runner-up margin, tonal concentration, usable duration, and window consistency",
            score=round(margin, 3),
            alternatives=[
                {"temporalConsistency": round(temporal_consistency, 3)},
                {"tonalConcentration": round(tonal_concentration, 3)},
            ],
            warning=ambiguity_warning,
            evidence_kind=EvidenceKind.AMBIGUOUS if decision.ambiguous else None,
        ),
        chords=feature(chords, Confidence.LOW if chords else Confidence.UNKNOWN, "smoothed major/minor triad chroma templates", warning="Chord labels are approximate and are not copied into generated prompts."),
        chord_vocabulary=feature(vocabulary, Confidence.LOW if vocabulary else Confidence.UNKNOWN, "unique smoothed chord labels"),
        harmonic_rhythm=feature(harmonic_rhythm, Confidence.LOW, "merged chord-change rate"),
        major_minor_balance=feature(balance, Confidence.LOW, "estimated chord-vocabulary balance"),
        stability=feature(character[0], tonal_confidence, "global and windowed tonal-template fit"),
        character=feature(character, tonal_confidence, "key, mode, and stability descriptors"),
    )


def _novelty_boundaries(audio: AudioData, spectral: SpectralData, max_sections: int = 10) -> list[float]:
    if audio.duration < 4 or spectral.magnitude.shape[1] < 8:
        return [0.0, audio.duration]
    # Phase-robust stationarity guard: sparse periodic signals can generate
    # novelty peaks simply because adjacent short windows contain a different
    # integer number of impulses. Compare longer quarter-track summaries first
    # and keep genuinely stable material as one neutral section.
    stationary_slices = np.array_split(
        np.arange(spectral.magnitude.shape[1]),
        min(4, spectral.magnitude.shape[1]),
    )
    stationary_summaries = [
        np.mean(spectral.magnitude[:, indices], axis=1)
        for indices in stationary_slices
        if indices.size
    ]
    stationary_levels = [float(np.linalg.norm(summary)) for summary in stationary_summaries]
    stationary_similarities = [
        float(
            np.dot(left, right)
            / max(float(np.linalg.norm(left) * np.linalg.norm(right)), EPSILON)
        )
        for left, right in zip(stationary_summaries, stationary_summaries[1:], strict=False)
    ]
    if stationary_levels and stationary_similarities:
        level_spread = (max(stationary_levels) - min(stationary_levels)) / max(
            max(stationary_levels),
            EPSILON,
        )
        if level_spread < 0.12 and min(stationary_similarities) >= 0.985:
            return [0.0, audio.duration]
    frame_energy = np.linalg.norm(spectral.magnitude, axis=0) / math.sqrt(
        max(1, spectral.magnitude.shape[0])
    )
    centroid = np.sum(
        spectral.frequencies[:, None] * spectral.magnitude,
        axis=0,
    ) / np.maximum(np.sum(spectral.magnitude, axis=0), EPSILON)
    section_chroma = chromagram(spectral)
    band_rows: list[FloatArray] = []
    for indices in np.array_split(np.arange(spectral.magnitude.shape[0]), 8):
        if indices.size:
            band_rows.append(np.log1p(np.mean(spectral.magnitude[indices], axis=0)))
    features = np.vstack(
        (
            np.log1p(frame_energy),
            np.log1p(centroid),
            section_chroma,
            *band_rows,
        )
    )
    features = (features - np.mean(features, axis=1, keepdims=True)) / np.maximum(
        np.std(features, axis=1, keepdims=True),
        EPSILON,
    )
    width = max(1, int(1.5 / spectral.hop_seconds))
    kernel = np.ones(width, dtype=np.float64) / width
    smoothed = np.vstack([np.convolve(row, kernel, mode="same") for row in features])
    comparison = max(1, int(0.35 / spectral.hop_seconds))
    novelty = np.zeros(smoothed.shape[1], dtype=np.float64)
    if smoothed.shape[1] > comparison:
        novelty[comparison:] = np.linalg.norm(
            smoothed[:, comparison:] - smoothed[:, :-comparison],
            axis=0,
        ) / math.sqrt(smoothed.shape[0])
    novelty = np.convolve(novelty, np.ones(3) / 3.0, mode="same")
    distance = max(1, int(2.5 / spectral.hop_seconds))
    prominence = max(
        float(np.std(novelty)) * 0.55,
        float(np.max(novelty)) * 0.07,
        1e-7,
    )
    peaks, properties = signal.find_peaks(novelty, distance=distance, prominence=prominence)
    if peaks.size:
        prominence_values = properties.get("prominences", np.zeros_like(peaks, dtype=float))
        selected = sorted(
            zip(peaks, prominence_values, strict=False),
            key=lambda item: float(item[1]),
            reverse=True,
        )[: max_sections - 1]
        selected.sort(key=lambda item: int(item[0]))
        median_prominence = float(np.median(prominence_values))
        boundaries = []
        last = 0.0
        for peak, peak_prominence in selected:
            value = float(spectral.times[min(int(peak), spectral.times.size - 1)])
            context = max(2, int(2.0 / spectral.hop_seconds))
            peak_index = int(peak)
            before = np.mean(
                spectral.magnitude[:, max(0, peak_index - context) : peak_index],
                axis=1,
            )
            after = np.mean(
                spectral.magnitude[:, peak_index : min(spectral.magnitude.shape[1], peak_index + context)],
                axis=1,
            )
            recurrence_similarity = float(
                np.dot(before, after)
                / max(float(np.linalg.norm(before) * np.linalg.norm(after)), EPSILON)
            )
            before_energy = float(np.linalg.norm(before))
            after_energy = float(np.linalg.norm(after))
            energy_change = abs(after_energy - before_energy) / max(
                before_energy,
                after_energy,
                EPSILON,
            )
            # Sparse periodic material is sensitive to small phase offsets in
            # the comparison windows. A high (but not near-identical) spectral
            # cosine plus stable energy is therefore stronger evidence of a
            # recurring loop than of a section boundary.
            if recurrence_similarity >= 0.95 and energy_change < 0.15:
                continue
            strong = float(peak_prominence) >= max(median_prominence * 0.5, prominence)
            minimum_duration = 3.0 if strong else 8.0
            if value - last >= minimum_duration and audio.duration - value >= minimum_duration:
                boundaries.append(value)
                last = value
    else:
        boundaries = []
    return [0.0, *[value for value in boundaries if 1.0 < value < audio.duration - 1.0], audio.duration]


def analyze_structure(audio: AudioData, spectral: SpectralData) -> StructureAnalysis:
    boundaries = _novelty_boundaries(audio, spectral)
    sections: list[Section] = []
    energies: list[float] = []
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    section_chroma = chromagram(spectral)
    global_flux = _onset_envelope(spectral)
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:], strict=False)):
        start_sample = min(audio.mono.size, int(start * audio.sample_rate))
        end_sample = min(audio.mono.size, max(start_sample + 1, int(end * audio.sample_rate)))
        samples = audio.mono[start_sample:end_sample]
        rms = math.sqrt(float(np.mean(samples * samples)) + EPSILON)
        energy = min(1.0, max(0.0, (dbfs(rms) + 60.0) / 60.0))
        energies.append(energy)
        frame_mask = (spectral.times >= start) & (spectral.times < end)
        if np.any(frame_mask):
            local_magnitude = spectral.magnitude[:, frame_mask]
            local_peaks = np.maximum(np.max(local_magnitude, axis=0, keepdims=True), EPSILON)
            density_value = float(np.mean(local_magnitude > local_peaks * 0.08))
            local_chroma = np.mean(section_chroma[:, frame_mask], axis=1)
            chroma_total = float(np.sum(local_chroma))
            pitch_class = int(np.argmax(local_chroma)) if chroma_total > EPSILON else 0
            emphasis = float(local_chroma[pitch_class] / max(chroma_total, EPSILON))
            harmony_summary = (
                f"{PITCH_NAMES[pitch_class]} pitch-class emphasis (not a chord)"
                if emphasis >= 0.2
                else "ambiguous tonal center"
            )
            local_flux = global_flux[frame_mask]
            coarse_instruments = ["tonal material"] if emphasis >= 0.16 else []
            if local_flux.size and float(np.percentile(local_flux, 90)) > 0.35:
                coarse_instruments.append("percussive elements")
        else:
            density_value = 0.0
            harmony_summary = "unknown"
            coarse_instruments = []
        inferred: str | None = None
        if index == 0 and start < 0.5 and end <= max(15.0, audio.duration * 0.2):
            inferred = "intro"
        elif index == len(boundaries) - 2 and end >= audio.duration - 0.5 and energy < max(energies):
            inferred = "outro"
        sections.append(
            Section(
                id=f"section-{index + 1}",
                neutral_label=labels[index % len(labels)],
                inferred_label=inferred,
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
                confidence=Confidence.MEDIUM if len(boundaries) > 2 else Confidence.LOW,
                repetition_group=None,
                energy=round(energy, 3),
                loudness=round(dbfs(rms), 2),
                density=round(density_value, 3),
                instruments=coarse_instruments,
                vocal_activity="unknown (no enabled vocal separator)",
                harmony_summary=harmony_summary,
                transition_in="timbral/energy novelty" if index else None,
                transition_out="timbral/energy novelty" if index < len(boundaries) - 2 else None,
                boundary_confidence=(
                    Confidence.MEDIUM if index > 0 and len(boundaries) > 2 else Confidence.LOW
                ),
            )
        )
    # Assign conservative neutral repetition groups by comparing normalized
    # section spectra. This never turns them into semantic verse/chorus labels.
    summaries: list[FloatArray] = []
    repeatable_flags: list[bool] = []
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        frame_mask = (spectral.times >= start) & (spectral.times < end)
        if not np.any(frame_mask):
            summaries.append(np.zeros(16, dtype=np.float64))
            continue
        # Pitch-class distribution distinguishes repeated harmonic material more
        # usefully than equal-width linear FFT bands (which collapse most music
        # into the first low-frequency band at this sample rate).
        summary = np.mean(section_chroma[:, frame_mask], axis=1)
        norm = float(np.linalg.norm(summary))
        summaries.append(summary / norm if norm > EPSILON else summary)
        start_sample = min(audio.mono.size, int(start * audio.sample_rate))
        end_sample = min(audio.mono.size, max(start_sample + 1, int(end * audio.sample_rate)))
        section_samples = np.abs(audio.mono[start_sample:end_sample])
        local_peak = float(np.max(section_samples)) if section_samples.size else 0.0
        active_fraction = float(np.mean(section_samples > local_peak * 0.05)) if local_peak > EPSILON else 0.0
        repeatable_flags.append(active_fraction >= 0.25)
    next_group = 1
    for index in range(len(sections)):
        for previous in range(index):
            duration = sections[index].end_seconds - sections[index].start_seconds
            previous_duration = sections[previous].end_seconds - sections[previous].start_seconds
            duration_ratio = min(duration, previous_duration) / max(duration, previous_duration, EPSILON)
            similarity = float(np.dot(summaries[index], summaries[previous]))
            if (
                repeatable_flags[index]
                and repeatable_flags[previous]
                and duration_ratio >= 0.72
                and similarity >= 0.98
                and np.linalg.norm(summaries[index]) > EPSILON
            ):
                if sections[previous].repetition_group is None:
                    sections[previous].repetition_group = f"R{next_group}"
                    next_group += 1
                sections[index].repetition_group = sections[previous].repetition_group
                break
    if len(energies) >= 2:
        difference = energies[-1] - energies[0]
        if max(energies) - min(energies) < 0.12:
            arc = "mostly consistent"
        elif difference > 0.15:
            arc = "building"
        elif difference < -0.15:
            arc = "tapering"
        else:
            arc = "contrasting"
    else:
        arc = "insufficient duration for a reliable arc"
    transitions = [round(value, 3) for value in boundaries[1:-1]]
    return StructureAnalysis(
        sections=sections,
        energy_arc=feature(arc, Confidence.MEDIUM if len(sections) > 1 else Confidence.LOW, "section RMS trajectory"),
        important_transitions=feature(transitions, Confidence.MEDIUM if transitions else Confidence.LOW, "smoothed energy and spectral-centroid novelty peaks"),
        repetition_summary=feature(
            "neutral repeated section groups assigned" if any(section.repetition_group for section in sections) else "no conservative repetition match",
            Confidence.LOW,
            "duration-constrained normalized section-spectrum cosine similarity",
            warning="Repetition groups are neutral; Fast mode does not force verse/chorus labels.",
        ),
    )


def analyze_timbre(spectral: SpectralData, audio: AudioData) -> TimbreAnalysis:
    magnitude_sum = np.maximum(np.sum(spectral.magnitude, axis=0), EPSILON)
    centroids = np.sum(spectral.frequencies[:, None] * spectral.magnitude, axis=0) / magnitude_sum
    bandwidths = np.sqrt(
        np.sum(((spectral.frequencies[:, None] - centroids[None, :]) ** 2) * spectral.magnitude, axis=0)
        / magnitude_sum
    )
    indices = np.zeros(spectral.magnitude.shape[1], dtype=np.int64)
    for frame_index in range(spectral.magnitude.shape[1]):
        frame_power = spectral.magnitude[:, frame_index] ** 2
        cumulative = np.cumsum(frame_power)
        target = float(cumulative[-1]) * 0.85
        indices[frame_index] = int(np.searchsorted(cumulative, target))
    rolloff = spectral.frequencies[indices]
    geometric = np.exp(2.0 * np.mean(np.log(np.maximum(spectral.magnitude, EPSILON)), axis=0))
    arithmetic = np.square(np.linalg.norm(spectral.magnitude, axis=0)) / max(
        1, spectral.magnitude.shape[0]
    )
    flatness = float(np.mean(geometric / np.maximum(arithmetic, EPSILON)))
    zcr = float(np.mean(np.abs(np.diff(np.signbit(audio.mono)))))
    centroid = float(np.median(centroids))
    bandwidth = float(np.median(bandwidths))
    rolloff_value = float(np.median(rolloff))
    descriptors: list[str] = []
    if centroid > 3500:
        descriptors.extend(["bright", "airy"])
    elif centroid < 1400:
        descriptors.extend(["warm", "dark"])
    else:
        descriptors.append("balanced")
    if flatness > 0.3:
        descriptors.append("noisy or textured")
    texture = ["percussive" if zcr > 0.1 else "sustained", "dense" if bandwidth > 2500 else "focused"]

    # A compact DCT of log-band energy is reported as an MFCC-like summary. It is a
    # deterministic descriptor, not a classifier input or calibrated score.
    bands = np.array_split(2.0 * np.mean(np.log1p(spectral.magnitude), axis=1), 20)
    band_means = np.asarray([float(np.mean(band)) if band.size else 0.0 for band in bands])
    mfcc_like = np.real(np.fft.rfft(band_means))[:8]
    flux = _onset_envelope(spectral)
    transient = "sharp" if float(np.percentile(flux, 95)) > 0.65 else "rounded"
    return TimbreAnalysis(
        spectral_centroid_hz=feature(round(centroid, 1), Confidence.HIGH, "magnitude-weighted spectral centroid median"),
        spectral_bandwidth_hz=feature(round(bandwidth, 1), Confidence.HIGH, "magnitude-weighted spectral spread median"),
        spectral_rolloff_hz=feature(round(rolloff_value, 1), Confidence.HIGH, "85% spectral-energy rolloff median"),
        spectral_flatness=feature(round(flatness, 4), Confidence.HIGH, "geometric-to-arithmetic spectral power ratio"),
        mfcc_summary=feature([round(float(value), 4) for value in mfcc_like], Confidence.MEDIUM, "DCT-like log spectral-band summary"),
        zero_crossing_rate=feature(round(zcr, 5), Confidence.HIGH, "sample sign-change rate"),
        harmonic_percussive_balance=feature("mixed", Confidence.LOW, "spectral flux and sustained-energy proxy", warning="Fast mode uses a proxy rather than stem separation."),
        transient_sharpness=feature(transient, Confidence.MEDIUM, "spectral-flux upper percentile"),
        descriptors=feature(descriptors, Confidence.MEDIUM, "documented centroid and flatness thresholds"),
        texture=feature(texture, Confidence.MEDIUM, "zero-crossing and bandwidth thresholds"),
    )


def _integrated_loudness(samples: FloatArray, sample_rate: int) -> tuple[float, str]:
    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(sample_rate)
        value = float(meter.integrated_loudness(samples))
        if math.isfinite(value):
            return value, "ITU-R BS.1770 integrated loudness via pyloudnorm"
    except (ImportError, ValueError, OverflowError):
        pass
    rms = math.sqrt(float(np.mean(samples * samples)) + EPSILON)
    return dbfs(rms) - 0.7, "ungated RMS-derived loudness proxy (pyloudnorm unavailable or inapplicable)"


def analyze_production(audio: AudioData, spectral: SpectralData) -> ProductionAnalysis:
    peak = float(np.max(np.abs(audio.samples)))
    rms = math.sqrt(max(float(np.mean(audio.samples * audio.samples)), 0.0))
    peak_db = dbfs(peak)
    reported_peak_db = None if audio.normalization_violation else min(0.0, peak_db)
    crest = None if reported_peak_db is None else reported_peak_db - dbfs(rms)
    loudness, loudness_method = _integrated_loudness(audio.samples, audio.sample_rate)
    frame_rms = _frame_rms(audio.mono, max(128, int(audio.sample_rate * 0.4)), max(64, int(audio.sample_rate * 0.1)))
    frame_db = 20.0 * np.log10(np.maximum(frame_rms, 1e-12))
    dynamic_range = float(np.percentile(frame_db, 95) - np.percentile(frame_db, 10))
    loudness_range = float(np.percentile(frame_db, 95) - np.percentile(frame_db, 20))
    compression = (
        None
        if crest is None
        else "strong"
        if crest < 7
        else "moderate"
        if crest < 13
        else "light"
    )
    if audio.samples.shape[1] >= 2:
        stereo_mid = (audio.samples[:, 0] + audio.samples[:, 1]) * 0.5
        side = (audio.samples[:, 0] - audio.samples[:, 1]) * 0.5
        width = math.sqrt(float(np.mean(side * side)) + EPSILON) / max(
            math.sqrt(float(np.mean(stereo_mid * stereo_mid)) + EPSILON), EPSILON
        )
        correlation = float(np.corrcoef(audio.samples[:, 0], audio.samples[:, 1])[0, 1]) if np.std(audio.samples[:, 0]) > EPSILON and np.std(audio.samples[:, 1]) > EPSILON else 1.0
        mono_compatibility = "phase-risk warning" if correlation < -0.1 else "check in mono" if correlation < 0.2 else "good"
        width_confidence = Confidence.HIGH
    else:
        width = 0.0
        mono_compatibility = "mono source"
        width_confidence = Confidence.UNKNOWN
    nyquist = audio.sample_rate / 2
    edges = np.asarray([0, 120, 500, 2000, 6000, nyquist], dtype=float)
    average_power = np.square(np.linalg.norm(spectral.magnitude, axis=1)) / max(
        1, spectral.magnitude.shape[1]
    )
    balance: list[float] = []
    total = max(float(np.sum(average_power)), EPSILON)
    for start, end in zip(edges, edges[1:], strict=False):
        mask = (spectral.frequencies >= start) & (spectral.frequencies < end)
        balance.append(float(np.sum(average_power[mask]) / total))
    low = "weighty" if sum(balance[:2]) > 0.55 else "lean" if sum(balance[:2]) < 0.25 else "balanced"
    midrange = "forward" if sum(balance[1:3]) > 0.55 else "balanced"
    high = "bright" if sum(balance[3:]) > 0.28 else "soft" if sum(balance[3:]) < 0.1 else "balanced"
    flux = _onset_envelope(spectral)
    transient = "emphasized" if float(np.percentile(flux, 90)) > 0.5 else "rounded"
    frame_peaks = np.maximum(np.max(spectral.magnitude, axis=0, keepdims=True), EPSILON)
    relative_occupancy = float(np.mean(spectral.magnitude > frame_peaks * 0.08))
    density = "dense" if relative_occupancy > 0.18 else "sparse" if relative_occupancy < 0.055 else "moderate"
    spaciousness = "wide/spacious proxy" if width > 0.45 else "focused/dry proxy"
    pumping = "not established"
    character = (
        [high]
        if crest is None
        else ["polished" if crest < 13 and peak_db > -12 else "dynamic/raw", high]
    )
    normalization_warning = (
        "Decoded samples exceeded the expected normalized range; sample peak, crest factor, and "
        "compression tendency were withheld instead of reporting an invalid positive dBFS peak."
        if audio.normalization_violation
        else None
    )
    return ProductionAnalysis(
        integrated_loudness_lufs=feature(round(loudness, 2), Confidence.HIGH if loudness_method.startswith("ITU") else Confidence.LOW, loudness_method, warning=None if loudness_method.startswith("ITU") else "This is a loudness proxy, not gated LUFS."),
        loudness_range_lu=feature(round(loudness_range, 2), Confidence.MEDIUM, "short-window RMS 95th-to-20th percentile proxy", warning="Approximate loudness range; not EBU LRA."),
        peak_dbfs=feature(
            round(reported_peak_db, 2) if reported_peak_db is not None else None,
            Confidence.HIGH if reported_peak_db is not None else Confidence.UNKNOWN,
            "decoded normalized sample peak",
            warning=normalization_warning,
            evidence_kind=(
                EvidenceKind.DIRECT_MEASUREMENT
                if reported_peak_db is not None
                else EvidenceKind.UNAVAILABLE
            ),
        ),
        true_peak_dbfs=feature(None, Confidence.UNKNOWN, "not measured", warning="True peak requires oversampled metering; sample peak is reported instead."),
        crest_factor_db=feature(
            round(crest, 2) if crest is not None else None,
            Confidence.HIGH if crest is not None else Confidence.UNKNOWN,
            "sample peak minus whole-track RMS",
            warning=normalization_warning,
        ),
        macro_dynamic_range_db=feature(round(dynamic_range, 2), Confidence.MEDIUM, "short-window RMS percentile range"),
        compression_tendency=feature(
            compression,
            Confidence.MEDIUM if compression is not None else Confidence.UNKNOWN,
            "crest-factor heuristic",
            warning=normalization_warning,
        ),
        stereo_width=feature(round(width, 3), width_confidence, "mid/side RMS ratio", warning="Mono source." if audio.samples.shape[1] == 1 else None),
        mono_compatibility=feature(mono_compatibility, Confidence.HIGH if audio.samples.shape[1] >= 2 else Confidence.UNKNOWN, "inter-channel correlation threshold"),
        frequency_balance=feature([round(value, 4) for value in balance], Confidence.HIGH, "relative FFT power in 0-120, 120-500, 500-2000, 2000-6000, and 6000-Nyquist Hz bands"),
        low_end_weight=feature(low, Confidence.MEDIUM, "low-band energy ratio thresholds"),
        midrange_focus=feature(midrange, Confidence.MEDIUM, "mid-band energy ratio thresholds"),
        high_frequency_brightness=feature(high, Confidence.MEDIUM, "high-band energy ratio thresholds"),
        spaciousness_proxy=feature(spaciousness, Confidence.LOW, "mid/side energy proxy", warning="This proxy cannot distinguish stereo arrangement from reverb."),
        sidechain_pumping_proxy=feature(pumping, Confidence.UNKNOWN, "no reliable envelope periodicity assertion", warning="Sidechain pumping is not asserted without stronger evidence."),
        transient_emphasis=feature(transient, Confidence.MEDIUM, "spectral-flux percentile"),
        mix_density=feature(density, Confidence.LOW, "mean spectral-bin occupancy above 8% of each frame peak", score=round(relative_occupancy, 4)),
        production_character=feature(
            character,
            Confidence.MEDIUM if crest is not None else Confidence.LOW,
            "crest factor, peak level, and frequency-balance rules",
            warning=normalization_warning,
        ),
    )


def analyze_melody() -> MelodyAnalysis:
    warning = "Predominant melody is omitted for polyphonic Fast-mode analysis."
    return MelodyAnalysis(
        pitch_contour_available=feature(False, Confidence.HIGH, "Fast-mode policy", warning=warning),
        melodic_range=unavailable("no predominant-melody model", warning),
        register_value=unavailable("no predominant-melody model", warning),
        phrase_length=unavailable("no predominant-melody model", warning),
        movement=unavailable("no predominant-melody model", warning),
        repetition=unavailable("no predominant-melody model", warning),
        density=unavailable("no predominant-melody model", warning),
        ornamentation=unavailable("no predominant-melody model", warning),
        call_and_response=unavailable("no predominant-melody model", warning),
        hook_prominence=unavailable("no predominant-melody model", warning),
    )


def analyze_instrumentation(timbre: TimbreAnalysis, rhythm: RhythmAnalysis) -> InstrumentationAnalysis:
    candidates: list[InstrumentCandidate] = []
    if rhythm.percussiveness.value in {"pronounced", "moderate"}:
        candidates.append(InstrumentCandidate(name="percussive elements", prominence="present", confidence=Confidence.MEDIUM))
    descriptors = set(timbre.descriptors.value or [])
    if "warm" in descriptors or "dark" in descriptors:
        candidates.append(InstrumentCandidate(name="low-frequency tonal material", prominence="audible", confidence=Confidence.LOW))
    return InstrumentationAnalysis(
        candidates=feature(candidates, Confidence.LOW if candidates else Confidence.UNKNOWN, "coarse spectral and onset rules", warning="Fast mode reports coarse categories, not specific instruments."),
        coarse_categories_only=True,
    )


def analyze_vocals() -> VocalsAnalysis:
    warning = "Vocal presence is not inferred reliably without an enabled separator or tagger."
    return VocalsAnalysis(
        presence=feature("unknown", Confidence.UNKNOWN, "no enabled vocal adapter", warning=warning),
        register_value=unavailable("no enabled vocal adapter", warning),
        delivery=unavailable("no enabled vocal adapter", warning),
        phrasing=unavailable("no enabled vocal adapter", warning),
        density=unavailable("no enabled vocal adapter", warning),
        layering=unavailable("no enabled vocal adapter", warning),
        processing=unavailable("no enabled vocal adapter", warning),
        mix_placement=unavailable("no enabled vocal adapter", warning),
    )


def analyze_style(timbre: TimbreAnalysis, rhythm: RhythmAnalysis, production: ProductionAnalysis) -> StyleAndMoodAnalysis:
    descriptors = list(timbre.descriptors.value or [])
    groove = list(rhythm.groove_descriptors.value or [])
    energy = "high" if rhythm.bpm.value and float(rhythm.bpm.value) >= 135 else "low" if rhythm.bpm.value and float(rhythm.bpm.value) <= 75 else "medium"
    mood: list[str] = []
    if "bright" in descriptors:
        mood.append("open")
    if "dark" in descriptors:
        mood.append("brooding")
    if not mood:
        mood.append("balanced")
    organic = "textured" if "noisy or textured" in descriptors else "mixed organic/synthetic character"
    return StyleAndMoodAnalysis(
        broad_style=feature(["production descriptors available; genre unclassified"], Confidence.UNKNOWN, "Fast-mode policy", warning="No genre model is installed, so a genre is not fabricated."),
        genre_blend=feature([], Confidence.UNKNOWN, "no enabled musical tagger"),
        production_era_resemblance=feature("not estimated", Confidence.UNKNOWN, "no enabled era tagger", warning="This field would describe resemblance, never recording date."),
        mood=feature(mood, Confidence.LOW, "timbre descriptor rules"),
        energy=feature(energy, Confidence.MEDIUM, "tempo and onset activity rules"),
        valence=feature("not established", Confidence.UNKNOWN, "no calibrated valence model"),
        intensity=feature("high" if "driving" in groove else "moderate", Confidence.LOW, "tempo and groove proxy"),
        danceability_tendency=feature("moderate" if rhythm.tempo_stability.value == "stable" else "uncertain", Confidence.LOW, "tempo stability proxy"),
        cinematic_quality=feature("not established", Confidence.UNKNOWN, "no cinematic tagger"),
        organic_synthetic=feature(organic, Confidence.LOW, "spectral texture proxy"),
        commercial_experimental=feature("not established", Confidence.UNKNOWN, "no defensible Fast-mode estimator"),
    )


def with_failure_isolation(
    name: str,
    analyzer: Callable[[], T],
    fallback: Callable[[str], T],
    warnings: list[str],
) -> T:
    try:
        return analyzer()
    except Exception:  # Deliberately isolates analyzer failure; message is kept internal/safe.
        warning = f"{name} analysis was unavailable; other results are still usable."
        warnings.append(warning)
        return fallback(warning)

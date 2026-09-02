from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import numpy as np

from ..config import Settings
from ..schemas import Confidence
from ..subprocess_utils import ProcessTimedOut, ProcessWasCancelled, run_process_bounded
from .schemas import (
    ReviewState,
    SegmentationResponse,
    SegmentEvidence,
    SegmentResponse,
    TransitionType,
)

SCAN_SAMPLE_RATE = 8_000
SCAN_CHANNELS = 2
ProgressCallback = Callable[[str, int], None]


class LongformScanCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Observation:
    time_seconds: float
    rms: float
    low_ratio: float
    mid_ratio: float
    high_ratio: float
    centroid: float
    flatness: float
    chroma: tuple[float, ...]
    onset_density: float
    stereo_width: float


@dataclass(frozen=True, slots=True)
class Candidate:
    time_seconds: float
    score: float
    transition_type: TransitionType
    transition_start: float
    transition_end: float
    evidence: SegmentEvidence


def _bounded_pcm_chunk(
    source: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
    settings: Settings,
    cancel_requested: Callable[[], bool] | None = None,
) -> bytes:
    expected = math.ceil(duration_seconds * SCAN_SAMPLE_RATE * SCAN_CHANNELS * 4)
    args = [
        settings.ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_seconds:.6f}",
        "-t",
        f"{duration_seconds:.6f}",
        "-i",
        str(source),
        "-map_metadata",
        "-1",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        str(SCAN_CHANNELS),
        "-ar",
        str(SCAN_SAMPLE_RATE),
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        result = run_process_bounded(
            args,
            timeout_seconds=min(
                settings.subprocess_timeout_seconds,
                max(30, math.ceil(duration_seconds * 2)),
            ),
            stdout_limit=expected + 64 * 1024,
            stderr_limit=32_000,
            cancel_requested=cancel_requested,
        )
    except ProcessWasCancelled as exc:
        raise LongformScanCancelled("The long-form source scan was cancelled.") from exc
    except ProcessTimedOut as exc:
        raise RuntimeError("A bounded long-form scan chunk timed out.") from exc
    if result.returncode != 0 or result.stdout_exceeded or result.stderr_exceeded:
        raise RuntimeError("FFmpeg could not decode a bounded long-form scan chunk.")
    return result.stdout


def _chroma_summary(power: np.ndarray, frequencies: np.ndarray) -> tuple[float, ...]:
    chroma = np.zeros(12, dtype=np.float64)
    valid = frequencies >= 40.0
    if not np.any(valid):
        return tuple(0.0 for _ in range(12))
    midi = 69.0 + 12.0 * np.log2(np.maximum(frequencies[valid], 1.0) / 440.0)
    classes = np.mod(np.rint(midi).astype(np.int64), 12)
    np.add.at(chroma, classes, power[valid])
    total = float(np.sum(chroma))
    if total > 0:
        chroma /= total
    return tuple(float(value) for value in chroma)


def _observe_window(samples: np.ndarray, time_seconds: float) -> Observation:
    if samples.ndim != 2 or samples.shape[1] != 2:
        raise ValueError("Long-form scan windows must be stereo-shaped")
    mono = np.mean(samples, axis=1, dtype=np.float64)
    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64) + 1e-12))
    windowed = mono * np.hanning(mono.size)
    spectrum = np.abs(np.fft.rfft(windowed))
    power = np.square(spectrum)
    frequencies = np.fft.rfftfreq(mono.size, 1.0 / SCAN_SAMPLE_RATE)
    total = float(np.sum(power)) + 1e-12

    def band(low: float, high: float) -> float:
        mask = (frequencies >= low) & (frequencies < high)
        return float(np.sum(power[mask]) / total)

    centroid = float(np.sum(frequencies * power) / total / (SCAN_SAMPLE_RATE / 2))
    positive = power[power > 1e-18]
    flatness = (
        float(np.exp(np.mean(np.log(positive))) / (np.mean(positive) + 1e-12))
        if positive.size
        else 0.0
    )
    envelope_size = max(1, SCAN_SAMPLE_RATE // 100)
    usable = mono[: mono.size - (mono.size % envelope_size)]
    envelope = (
        np.mean(np.abs(usable.reshape(-1, envelope_size)), axis=1)
        if usable.size
        else np.zeros(1)
    )
    differences = np.diff(envelope, prepend=envelope[0])
    threshold = float(np.median(np.abs(differences)) * 4 + 1e-6)
    onset_density = min(1.0, float(np.count_nonzero(differences > threshold) / max(1, envelope.size) * 10))
    left = samples[:, 0].astype(np.float64)
    right = samples[:, 1].astype(np.float64)
    mid_energy = float(np.mean(np.square((left + right) * 0.5))) + 1e-12
    side_energy = float(np.mean(np.square((left - right) * 0.5)))
    width = min(2.0, math.sqrt(side_energy / mid_energy)) / 2.0
    return Observation(
        time_seconds=time_seconds,
        rms=rms,
        low_ratio=band(20, 250),
        mid_ratio=band(250, 2_000),
        high_ratio=band(2_000, 4_000),
        centroid=max(0.0, min(1.0, centroid)),
        flatness=max(0.0, min(1.0, flatness)),
        chroma=_chroma_summary(power, frequencies),
        onset_density=onset_density,
        stereo_width=width,
    )


def scan_streaming_features(
    source: Path,
    duration_seconds: float,
    settings: Settings,
    *,
    cadence_seconds: float | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[Observation], int]:
    cadence = cadence_seconds or float(settings.longform_scan_cadence_seconds)
    if cadence <= 0:
        raise ValueError("Scan cadence must be positive")
    observations: list[Observation] = []
    chunk_seconds = float(settings.longform_scan_chunk_seconds)
    peak_buffer = 0
    started = time.monotonic()
    offset = 0.0
    while offset < duration_seconds - 1e-6:
        if cancel_requested is not None and cancel_requested():
            raise LongformScanCancelled("The long-form source scan was cancelled.")
        if time.monotonic() - started > settings.longform_scan_timeout_seconds:
            raise RuntimeError("The long-form streaming scan exceeded its configured timeout.")
        current_duration = min(chunk_seconds, duration_seconds - offset)
        raw = _bounded_pcm_chunk(
            source,
            start_seconds=offset,
            duration_seconds=current_duration,
            settings=settings,
            cancel_requested=cancel_requested,
        )
        peak_buffer = max(peak_buffer, len(raw))
        values = np.frombuffer(raw, dtype="<f4")
        usable_values = values[: values.size - (values.size % SCAN_CHANNELS)]
        frames = usable_values.reshape(-1, SCAN_CHANNELS)
        window_frames = max(1, round(cadence * SCAN_SAMPLE_RATE))
        for frame_start in range(0, frames.shape[0], window_frames):
            window = frames[frame_start : frame_start + window_frames]
            if window.shape[0] < window_frames // 2:
                continue
            center = offset + (frame_start + window.shape[0] / 2) / SCAN_SAMPLE_RATE
            observations.append(_observe_window(window, center))
        offset += current_duration
        if progress_callback is not None:
            progress_callback(
                "Coarse set scan",
                min(65, round(offset / max(duration_seconds, 0.001) * 65)),
            )
    return observations, peak_buffer


def _mean_vector(observations: list[Observation]) -> np.ndarray:
    if not observations:
        return np.zeros(20, dtype=np.float64)
    vectors = [
        [
            item.rms,
            item.low_ratio,
            item.mid_ratio,
            item.high_ratio,
            item.centroid,
            item.flatness,
            item.onset_density,
            item.stereo_width,
            *item.chroma,
        ]
        for item in observations
    ]
    return np.mean(np.asarray(vectors, dtype=np.float64), axis=0)


def _bounded_distance(left: np.ndarray, right: np.ndarray, indexes: slice | list[int]) -> float:
    delta = left[indexes] - right[indexes]
    return min(1.0, float(np.linalg.norm(delta) / max(0.25, math.sqrt(delta.size) * 0.35)))


def detect_candidates(observations: list[Observation], cadence_seconds: float) -> list[Candidate]:
    if len(observations) < 12:
        return []
    radius = max(3, round(8 / cadence_seconds))
    scored: list[tuple[int, float, SegmentEvidence]] = []
    for index in range(radius, len(observations) - radius):
        before_items = observations[index - radius : index]
        after_items = observations[index + 1 : index + radius + 1]
        before = _mean_vector(before_items)
        after = _mean_vector(after_items)
        current = observations[index]
        neighborhood_rms = max(float(before[0]), float(after[0]), 1e-5)
        energy_dip = max(0.0, min(1.0, 1.0 - current.rms / neighborhood_rms))
        timbral = _bounded_distance(before, after, [1, 2, 3, 4, 5])
        harmonic = _bounded_distance(before, after, slice(8, 20))
        stereo = min(1.0, abs(float(before[7]) - float(after[7])) * 2.5)
        onset = min(1.0, abs(float(before[6]) - float(after[6])) * 2.0)
        near_after = _mean_vector(observations[index + 1 : index + 1 + max(2, radius // 2)])
        far_after = _mean_vector(after_items[-max(2, radius // 2) :])
        persistent = 1.0 - min(1.0, float(np.linalg.norm(near_after[1:] - far_after[1:]) / 1.5))
        evidence = SegmentEvidence(
            energy_dip=energy_dip,
            timbral_change=timbral,
            harmonic_change=harmonic,
            stereo_change=stereo,
            onset_change=onset,
            persistent_change=persistent,
        )
        independent = sum(value >= 0.28 for value in (energy_dip, timbral, harmonic, stereo, onset))
        score = (
            energy_dip * 0.28
            + timbral * 0.25
            + harmonic * 0.18
            + stereo * 0.08
            + onset * 0.08
            + persistent * 0.13
        )
        if independent >= 2 or energy_dip >= 0.75 or (timbral >= 0.6 and persistent >= 0.55):
            scored.append((index, score, evidence))

    candidates: list[Candidate] = []
    suppression = max(4, round(12 / cadence_seconds))
    for index, score, evidence in sorted(scored, key=lambda item: (-item[1], item[0])):
        if score < 0.34:
            continue
        if any(abs(observations[index].time_seconds - item.time_seconds) < suppression * cadence_seconds for item in candidates):
            continue
        anchor = observations[index].time_seconds
        if evidence.energy_dip >= 0.78:
            transition_type = TransitionType.SILENCE_GAP
            width = 2.0
        elif evidence.timbral_change >= 0.72 and evidence.persistent_change >= 0.55:
            transition_type = TransitionType.HARD_CUT
            width = 1.0
        elif evidence.energy_dip >= 0.35 and evidence.timbral_change >= 0.35:
            transition_type = TransitionType.FADE
            width = 6.0
        elif evidence.timbral_change >= 0.42 and evidence.energy_dip < 0.25:
            transition_type = TransitionType.CROSSFADE
            width = 12.0
        elif score >= 0.5:
            transition_type = TransitionType.GRADUAL_TRANSITION
            width = 10.0
        else:
            transition_type = TransitionType.UNCERTAIN
            width = 5.0
        candidates.append(
            Candidate(
                time_seconds=anchor,
                score=min(1.0, score),
                transition_type=transition_type,
                transition_start=max(0.0, anchor - width / 2),
                transition_end=anchor + width / 2,
                evidence=evidence,
            )
        )
    return sorted(candidates, key=lambda item: item.time_seconds)


def refine_candidates(
    source: Path,
    candidates: list[Candidate],
    duration_seconds: float,
    settings: Settings,
    *,
    window_seconds: float = 32.0,
    cadence_seconds: float = 0.25,
    cancel_requested: Callable[[], bool] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[Candidate], int]:
    """Refine selected coarse candidates with bounded local decodes.

    Each candidate is rescanned independently. The decoded buffer is bounded
    by ``window_seconds`` and is discarded before the next candidate, so the
    operation never retains source-resolution audio for the full recording.
    """
    refined: list[Candidate] = []
    peak_buffer = 0
    for index, candidate in enumerate(candidates):
        if cancel_requested is not None and cancel_requested():
            raise LongformScanCancelled("The long-form source scan was cancelled.")
        start = max(0.0, candidate.time_seconds - window_seconds / 2)
        end = min(duration_seconds, candidate.time_seconds + window_seconds / 2)
        raw = _bounded_pcm_chunk(
            source,
            start_seconds=start,
            duration_seconds=end - start,
            settings=settings,
            cancel_requested=cancel_requested,
        )
        peak_buffer = max(peak_buffer, len(raw))
        values = np.frombuffer(raw, dtype="<f4")
        usable = values[: values.size - (values.size % SCAN_CHANNELS)]
        frames = usable.reshape(-1, SCAN_CHANNELS)
        window_frames = max(1, round(cadence_seconds * SCAN_SAMPLE_RATE))
        observations: list[Observation] = []
        for frame_start in range(0, frames.shape[0], window_frames):
            window = frames[frame_start : frame_start + window_frames]
            if window.shape[0] < window_frames // 2:
                continue
            center = start + (frame_start + window.shape[0] / 2) / SCAN_SAMPLE_RATE
            observations.append(_observe_window(window, center))
        local = detect_candidates(observations, cadence_seconds)
        if local:
            best = max(
                local,
                key=lambda item: (
                    item.score - min(1.0, abs(item.time_seconds - candidate.time_seconds) / 8.0) * 0.2,
                    -abs(item.time_seconds - candidate.time_seconds),
                ),
            )
            refined_candidate = Candidate(
                time_seconds=max(0.0, min(duration_seconds, best.time_seconds)),
                score=max(candidate.score * 0.75, best.score),
                transition_type=best.transition_type,
                transition_start=max(0.0, best.transition_start),
                transition_end=min(duration_seconds, best.transition_end),
                evidence=best.evidence,
            )
        else:
            refined_candidate = Candidate(
                time_seconds=candidate.time_seconds,
                score=candidate.score,
                transition_type=candidate.transition_type,
                transition_start=max(0.0, candidate.transition_start),
                transition_end=min(duration_seconds, candidate.transition_end),
                evidence=candidate.evidence,
            )
        refined.append(refined_candidate)
        if progress_callback is not None:
            progress_callback(
                "Refining boundaries",
                76 + round((index + 1) / max(1, len(candidates)) * 20),
            )
    return sorted(refined, key=lambda item: item.time_seconds), peak_buffer


def select_boundaries(
    candidates: list[Candidate],
    duration_seconds: float,
    *,
    minimum_expected_seconds: float,
    maximum_expected_seconds: float,
) -> list[Candidate]:
    """Select candidates globally with soft length penalties.

    The optimizer may retain short interludes or long tracks when evidence is
    strong; neither expected length is a hard cutoff.
    """
    if not candidates:
        return []
    points = [0.0, *[item.time_seconds for item in candidates], duration_seconds]
    scores = [0.0, *[item.score for item in candidates], 0.0]
    best = [-math.inf] * len(points)
    parent = [-1] * len(points)
    best[0] = 0.0
    for end in range(1, len(points)):
        for start in range(end):
            length = points[end] - points[start]
            if length <= 0:
                continue
            short_penalty = max(0.0, (minimum_expected_seconds - length) / minimum_expected_seconds)
            long_penalty = max(0.0, (length - maximum_expected_seconds) / maximum_expected_seconds)
            edge_value = scores[end] if end < len(points) - 1 else 0.0
            value = best[start] + edge_value * 1.7 - short_penalty * 0.8 - long_penalty * 0.35
            if value > best[end]:
                best[end] = value
                parent[end] = start
    selected_indexes: list[int] = []
    cursor = len(points) - 1
    while parent[cursor] > 0:
        cursor = parent[cursor]
        selected_indexes.append(cursor - 1)
    return [candidates[index] for index in reversed(selected_indexes)]


def build_segments(
    asset_id: str,
    duration_seconds: float,
    boundaries: list[Candidate],
) -> list[SegmentResponse]:
    anchors = [0.0, *[item.time_seconds for item in boundaries], duration_seconds]
    segments: list[SegmentResponse] = []
    for index, (start, end) in enumerate(zip(anchors, anchors[1:], strict=False)):
        incoming = boundaries[index - 1] if index > 0 else None
        outgoing = boundaries[index] if index < len(boundaries) else None
        stable_start = incoming.transition_end if incoming else start
        stable_end = outgoing.transition_start if outgoing else end
        if stable_end <= stable_start:
            center = (start + end) / 2
            stable_start = center
            stable_end = center
        adjacent_scores = [item.score for item in (incoming, outgoing) if item is not None]
        score = min(adjacent_scores) if adjacent_scores else 0.0
        confidence = Confidence.HIGH if score >= 0.7 else Confidence.MEDIUM if score >= 0.48 else Confidence.LOW
        transition = incoming.transition_type if incoming else (
            outgoing.transition_type if outgoing else TransitionType.UNCERTAIN
        )
        evidence = incoming.evidence if incoming else (outgoing.evidence if outgoing else SegmentEvidence())
        segment_id = str(uuid5(NAMESPACE_URL, f"trackprompt:{asset_id}:{start:.3f}:{end:.3f}"))
        segments.append(
            SegmentResponse(
                id=segment_id,
                source_asset_id=asset_id,
                sequence_index=index,
                label=f"Track {index + 1}",
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
                stable_core_start_seconds=round(stable_start, 3),
                stable_core_end_seconds=round(stable_end, 3),
                transition_in_start_seconds=round(incoming.transition_start, 3) if incoming else None,
                transition_in_end_seconds=round(incoming.transition_end, 3) if incoming else None,
                transition_out_start_seconds=round(outgoing.transition_start, 3) if outgoing else None,
                transition_out_end_seconds=round(outgoing.transition_end, 3) if outgoing else None,
                confidence=confidence if boundaries else Confidence.UNKNOWN,
                confidence_score=round(score, 4) if adjacent_scores else None,
                transition_type=transition,
                review_state=ReviewState.DETECTED if boundaries else ReviewState.UNRESOLVED,
                accepted=False,
                evidence=evidence,
                revision=1,
            )
        )
    return segments


def segment_longform_source(
    asset_id: str,
    source: Path,
    duration_seconds: float,
    settings: Settings,
    *,
    minimum_expected_seconds: int | None = None,
    maximum_expected_seconds: int | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SegmentationResponse:
    if duration_seconds > settings.max_longform_duration_seconds:
        raise ValueError("Source exceeds the configured long-form duration limit")
    started = time.monotonic()
    cadence = float(settings.longform_scan_cadence_seconds)
    observations, peak_buffer = scan_streaming_features(
        source,
        duration_seconds,
        settings,
        cancel_requested=cancel_requested,
        progress_callback=progress_callback,
    )
    candidates = detect_candidates(observations, cadence)
    if progress_callback is not None:
        progress_callback("Detecting transition candidates", 70)
    coarse_selected = select_boundaries(
        candidates,
        duration_seconds,
        minimum_expected_seconds=float(
            minimum_expected_seconds or settings.minimum_expected_track_seconds
        ),
        maximum_expected_seconds=float(
            maximum_expected_seconds or settings.maximum_expected_track_seconds
        ),
    )
    if progress_callback is not None:
        progress_callback("Selecting virtual boundaries", 76)
    selected, refinement_peak = refine_candidates(
        source,
        coarse_selected,
        duration_seconds,
        settings,
        cancel_requested=cancel_requested,
        progress_callback=progress_callback,
    )
    peak_buffer = max(peak_buffer, refinement_peak)
    warnings: list[str] = []
    state = "awaiting_review"
    if not selected:
        warnings.append(
            "No defensible multi-signal track boundaries were found; one unresolved long-form item was retained for review."
        )
        state = "unresolved"
    if any(item.transition_type == TransitionType.CROSSFADE for item in selected):
        warnings.append(
            "Crossfade regions contain overlapping tracks. Stable cores exclude the mixed transition where possible; no source separation is claimed."
        )
    segments = build_segments(asset_id, duration_seconds, selected)
    if progress_callback is not None:
        progress_callback("Creating virtual segments", 99)
    return SegmentationResponse(
        asset_id=asset_id,
        state=state,
        duration_seconds=duration_seconds,
        observation_count=len(observations),
        candidate_count=len(candidates),
        segments=segments,
        warnings=warnings,
        cadence_seconds=cadence,
        peak_buffer_bytes=peak_buffer,
        elapsed_seconds=round(time.monotonic() - started, 3),
    )

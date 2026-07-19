from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass

from ..schemas import AnalysisResult, Confidence, FeatureValue, Section
from .curves import simplify_points
from .frames import event_frame, frame_end, section_frames
from .schemas import (
    CueCurve,
    CueEvent,
    CueMeasuredValue,
    CueMeterValue,
    CuePreferences,
    CueSection,
    CueSource,
    CueTimeline,
    CueTransition,
    CurveDetail,
    CurveName,
    MusicalGrid,
    SimplificationMetadata,
    TrackPromptVisualCueSheet,
    VisualFeatureArtifact,
)
from .validation import validate_public_cue_sheet


class VisualCueCompilationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True, slots=True)
class DetailPlan:
    tolerance: float
    maximum_points: int


DETAIL_PLANS = {
    CurveDetail.COMPACT: DetailPlan(tolerance=0.020, maximum_points=600),
    CurveDetail.BALANCED: DetailPlan(tolerance=0.008, maximum_points=1600),
    CurveDetail.DETAILED: DetailPlan(tolerance=0.0035, maximum_points=3500),
}


def _finite_float(value: object) -> float | None:
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return float(value)
    return None


def _feature_times(feature: FeatureValue[list[float]] | None) -> list[float]:
    if feature is None or not isinstance(feature.value, list):
        return []
    return sorted(
        {
            float(value)
            for value in feature.value
            if isinstance(value, int | float) and math.isfinite(float(value)) and float(value) >= 0.0
        }
    )


def _events(
    feature: FeatureValue[list[float]] | None,
    *,
    duration: float,
    fps: int,
    source_path: str,
) -> list[CueEvent]:
    if feature is None:
        return []
    return [
        CueEvent(
            index=index,
            time_seconds=min(duration, time_seconds),
            frame=event_frame(time_seconds, duration, fps),
            confidence=feature.confidence,
            strength=None,
            source_path=source_path,
        )
        for index, time_seconds in enumerate(_feature_times(feature))
        if time_seconds <= duration + 1e-6
    ]


def _validate_sections(sections: list[Section], duration: float) -> None:
    if not sections:
        raise VisualCueCompilationError("invalid_section_ranges", "The analysis contains no visualizable sections.")
    if abs(sections[0].start_seconds) > 0.05:
        raise VisualCueCompilationError("invalid_section_ranges", "The first section does not begin at the track start.")
    for index, section in enumerate(sections):
        if (
            not math.isfinite(section.start_seconds)
            or not math.isfinite(section.end_seconds)
            or section.start_seconds < 0.0
            or section.end_seconds <= section.start_seconds
            or section.end_seconds > duration + 0.1
        ):
            raise VisualCueCompilationError("invalid_section_ranges", "The analysis contains an invalid section range.")
        if index and section.start_seconds < sections[index - 1].end_seconds - 1e-6:
            raise VisualCueCompilationError("invalid_section_ranges", "The analysis contains overlapping sections.")


def _cue_sections(
    analysis: AnalysisResult,
    preferences: CuePreferences,
) -> list[CueSection]:
    duration = analysis.file.duration_seconds
    _validate_sections(analysis.structure.sections, duration)
    result: list[CueSection] = []
    for index, section in enumerate(analysis.structure.sections):
        start, end = section_frames(
            section.start_seconds,
            section.end_seconds,
            duration,
            preferences.fps,
            final=index == len(analysis.structure.sections) - 1,
        )
        deep = section.deep_evidence if preferences.include_stem_evidence else None
        result.append(
            CueSection(
                id=section.id,
                neutral_label=section.neutral_label,
                inferred_label=section.inferred_label,
                start_seconds=section.start_seconds,
                end_seconds=section.end_seconds,
                start_frame=start,
                end_frame=end,
                energy=_finite_float(section.energy),
                loudness=_finite_float(section.loudness),
                confidence=section.confidence,
                boundary_confidence=section.boundary_confidence or section.confidence,
                repetition_group=section.repetition_group,
                vocal_activity=section.vocal_activity,
                instruments=list(section.instruments),
                stem_activity=dict(deep.activity) if deep else {},
                stem_relative_rms={
                    name: float(value)
                    for name, value in (deep.relative_rms.items() if deep else [])
                    if math.isfinite(float(value))
                },
                source_path=f"structure.sections[{index}]",
            )
        )
    return result


def _transitions(sections: list[CueSection]) -> list[CueTransition]:
    result: list[CueTransition] = []
    for index, (previous, current) in enumerate(zip(sections, sections[1:], strict=False), start=1):
        delta = (
            current.energy - previous.energy
            if current.energy is not None and previous.energy is not None
            else None
        )
        direction = (
            "unknown"
            if delta is None
            else "rising"
            if delta > 0.03
            else "falling"
            if delta < -0.03
            else "stable"
        )
        confidence = (
            Confidence.LOW
            if Confidence.LOW in {previous.boundary_confidence, current.boundary_confidence}
            else Confidence.UNKNOWN
            if Confidence.UNKNOWN in {previous.boundary_confidence, current.boundary_confidence}
            else Confidence.MEDIUM
        )
        result.append(
            CueTransition(
                id=f"transition-{index}",
                time_seconds=current.start_seconds,
                frame=current.start_frame,
                from_section_id=previous.id,
                to_section_id=current.id,
                energy_before=previous.energy,
                energy_after=current.energy,
                energy_delta=round(delta, 6) if delta is not None else None,
                direction=direction,
                confidence=confidence,
                source_paths=[previous.source_path, current.source_path],
            )
        )
    return result


def _insert_landmark(
    points: list[tuple[int, float]],
    frame: int,
) -> None:
    frames = [point[0] for point in points]
    position = bisect_left(frames, frame)
    if position < len(points) and points[position][0] == frame:
        return
    if position <= 0:
        value = points[0][1]
    elif position >= len(points):
        value = points[-1][1]
    else:
        left = points[position - 1]
        right = points[position]
        fraction = (frame - left[0]) / max(1, right[0] - left[0])
        value = left[1] + fraction * (right[1] - left[1])
    points.insert(position, (frame, min(1.0, max(0.0, value))))


def _important_peaks(points: list[tuple[int, float]], *, maximum: int = 96) -> list[int]:
    candidates = [
        (value, frame)
        for index, (frame, value) in enumerate(points[1:-1], start=1)
        if value >= 0.75 and value >= points[index - 1][1] and value >= points[index + 1][1]
    ]
    return [frame for _value, frame in sorted(candidates, reverse=True)[:maximum]]


def _cue_curves(
    artifact: VisualFeatureArtifact,
    preferences: CuePreferences,
    landmark_frames: set[int],
) -> dict[CurveName, CueCurve]:
    plan = DETAIL_PLANS[preferences.curve_detail]
    result: dict[CurveName, CueCurve] = {}
    for name in sorted(artifact.curves, key=lambda item: item.value):
        curve = artifact.curves[name]
        dense = [
            (
                event_frame(index / artifact.sample_rate_hz, artifact.duration_seconds, preferences.fps),
                value,
            )
            for index, value in enumerate(curve.values)
        ]
        collapsed: list[tuple[int, float]] = []
        for frame, value in dense:
            if collapsed and collapsed[-1][0] == frame:
                collapsed[-1] = (frame, value)
            else:
                collapsed.append((frame, value))
        for landmark in sorted(landmark_frames):
            _insert_landmark(collapsed, landmark)
        peaks = _important_peaks(collapsed) if name == CurveName.TRANSIENT_ACTIVITY else []
        simplified, maximum_error, effective_tolerance = simplify_points(
            collapsed,
            tolerance=plan.tolerance,
            maximum_point_count=plan.maximum_points,
            forced_frames=landmark_frames,
            important_peak_frames=peaks,
        )
        result[name] = CueCurve(
            points=[(frame, round(value, 6)) for frame, value in simplified],
            source_sample_rate_hz=artifact.sample_rate_hz,
            original_point_count=len(curve.values),
            exported_point_count=len(simplified),
            simplification=SimplificationMetadata(
                tolerance=round(effective_tolerance, 8),
                maximum_error=round(maximum_error, 8),
                maximum_point_count=plan.maximum_points,
            ),
            normalization=curve.normalization,
            smoothing=curve.smoothing,
        )
    return result


def _meter(analysis: AnalysisResult) -> CueMeterValue:
    raw = analysis.rhythm.meter.value
    value = raw if isinstance(raw, str) and raw.strip().casefold() not in {"", "unknown"} else None
    return CueMeterValue(value=value, confidence=analysis.rhythm.meter.confidence if value else Confidence.UNKNOWN)


def compile_visual_cues(
    analysis: AnalysisResult,
    artifact: VisualFeatureArtifact | None,
    preferences: CuePreferences,
) -> TrackPromptVisualCueSheet:
    duration = analysis.file.duration_seconds
    if not math.isfinite(duration) or duration <= 0.0:
        raise VisualCueCompilationError("invalid_timeline", "The analysis duration is not visualizable.")
    if frame_end(duration, preferences.fps) <= 1:
        raise VisualCueCompilationError("invalid_timeline", "The track is too short for a two-point visual curve.")
    if preferences.include_curves:
        if artifact is None:
            raise VisualCueCompilationError(
                "visual_features_unavailable",
                "Continuous visual curves are unavailable for this older analysis; reanalysis is required.",
            )
        if artifact.job_id != analysis.job_id or abs(artifact.duration_seconds - duration) > 0.1:
            raise VisualCueCompilationError("invalid_curves", "Stored visual curves do not match this analysis.")
    sections = _cue_sections(analysis, preferences)
    transitions = _transitions(sections)
    landmarks = {
        frame
        for section in sections
        for frame in (section.start_frame, section.end_frame)
    } | {transition.frame for transition in transitions}
    curves = _cue_curves(artifact, preferences, landmarks) if preferences.include_curves and artifact else {}
    bpm = _finite_float(analysis.rhythm.bpm.value)
    warnings = list(artifact.warnings if artifact else [])
    stem_names = {
        CurveName.DRUM_ENERGY,
        CurveName.BASS_ENERGY,
        CurveName.VOCAL_ENERGY,
        CurveName.OTHER_ENERGY,
    }
    if preferences.include_curves and not stem_names.issubset(curves):
        warnings.append(
            "Private stem curves are unavailable; Blender will use declared full-mix fallbacks. Reanalyze in successful Deep mode for stem curves."
        )
    cue = TrackPromptVisualCueSheet(
        source=CueSource(
            analysis_schema_version=analysis.schema_version,
            analysis_version=analysis.analysis_version,
            job_id=analysis.job_id,
            requested_mode=analysis.requested_mode,
            effective_mode=analysis.effective_mode,
        ),
        timeline=CueTimeline(
            duration_seconds=duration,
            fps=preferences.fps,
            frame_start=1,
            frame_end=frame_end(duration, preferences.fps),
        ),
        musical_grid=MusicalGrid(
            bpm=CueMeasuredValue(value=bpm, confidence=analysis.rhythm.bpm.confidence if bpm else Confidence.UNKNOWN),
            seconds_per_beat=round(60.0 / bpm, 8) if bpm and bpm > 0.0 else None,
            meter=_meter(analysis),
            downbeats_available=False,
        ),
        beats=(
            _events(
                analysis.rhythm.beat_timestamps,
                duration=duration,
                fps=preferences.fps,
                source_path="rhythm.beatTimestamps",
            )
            if preferences.include_beats
            else []
        ),
        onsets=(
            _events(
                analysis.rhythm.onset_timestamps,
                duration=duration,
                fps=preferences.fps,
                source_path="rhythm.onsetTimestamps",
            )
            if preferences.include_onsets
            else []
        ),
        sections=sections,
        transitions=transitions,
        curves=curves,
        warnings=list(dict.fromkeys(warnings)),
    )
    return validate_public_cue_sheet(cue)

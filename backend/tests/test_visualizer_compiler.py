from __future__ import annotations

import json
import math

from app.visualizer.compiler import compile_visual_cues
from app.visualizer.schemas import (
    CuePreferences,
    CurveName,
    NormalizationMetadata,
    PrivateVisualCurve,
    SmoothingMetadata,
    VisualFeatureArtifact,
)


def _curve(values: list[float], group: str) -> PrivateVisualCurve:
    return PrivateVisualCurve(
        values=values,
        normalization=NormalizationMetadata(normalization_group=group),
        smoothing=SmoothingMetadata(
            attack_seconds=0.08,
            release_seconds=0.35,
            source_sample_rate_hz=20,
            output_sample_rate_hz=20,
        ),
    )


def test_compiler_is_deterministic_private_and_landmark_preserving(click_analysis) -> None:
    count = math.ceil(click_analysis.file.duration_seconds * 20) + 1
    values = [0.5 + 0.4 * math.sin(index / 9) for index in range(count)]
    artifact = VisualFeatureArtifact(
        job_id=click_analysis.job_id,
        duration_seconds=click_analysis.file.duration_seconds,
        curves={
            CurveName.MASTER_ENERGY: _curve(values, "master"),
            CurveName.LOW_BAND_ENERGY: _curve(values, "low"),
            CurveName.MID_BAND_ENERGY: _curve(values, "mid"),
            CurveName.HIGH_BAND_ENERGY: _curve(values, "high"),
            CurveName.BRIGHTNESS: _curve(values, "brightness"),
            CurveName.TRANSIENT_ACTIVITY: _curve(values, "transient"),
        },
        effective_mode="fast",
    )
    preferences = CuePreferences()
    first = compile_visual_cues(click_analysis, artifact, preferences)
    second = compile_visual_cues(click_analysis, artifact, preferences)
    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
    assert first.schema_version == "1.1.0"
    assert first.timeline.frame_end == 1 + math.ceil(click_analysis.file.duration_seconds * 30) - 1
    assert CurveName.MASTER_ENERGY in first.curves
    landmarks = {
        frame
        for section in first.sections
        for frame in (section.start_frame, section.end_frame)
    }
    assert landmarks.issubset({frame for frame, _value in first.curves[CurveName.MASTER_ENERGY].points})
    serialized = json.dumps(first.model_dump(mode="json", by_alias=True))
    assert "secret-source-name" not in serialized
    assert "privateMetadata" not in serialized
    assert "waveformPeaks" not in serialized
    assert any("stem curves are unavailable" in warning for warning in first.warnings)


def test_compiler_can_export_events_without_curves_for_legacy_analysis(click_analysis) -> None:
    cue = compile_visual_cues(
        click_analysis,
        None,
        CuePreferences(include_curves=False),
    )
    assert cue.curves == {}
    assert cue.beats
    assert cue.sections

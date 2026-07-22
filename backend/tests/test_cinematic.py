from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.cinematic.compiler import compile_cinematic_plan
from app.cinematic.planner import weighted_ranges
from app.cinematic.schemas import ArtDirectionReview, ProtagonistState
from app.cinematic.validation import validate_cinematic_privacy
from app.mission_control.config import MissionControlConfig
from app.mission_control.service import MissionControlService
from app.visualizer.compiler import compile_visual_cues
from app.visualizer.presets import (
    SpaceJourneyStoryVisualizerConfigRequest,
    resolve_visualizer_config,
)
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


def _cues(click_analysis):
    count = math.ceil(click_analysis.file.duration_seconds * 20) + 1
    values = [0.5 + 0.35 * math.sin(index / 8) for index in range(count)]
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
    return compile_visual_cues(click_analysis, artifact, CuePreferences())


def test_story_and_shot_compilation_is_deterministic_contiguous_and_private(click_analysis) -> None:
    cues = _cues(click_analysis)
    resolved = resolve_visualizer_config(
        SpaceJourneyStoryVisualizerConfigRequest(preset="space-journey-story")
    )
    first = compile_cinematic_plan(cues, resolved)
    second = compile_cinematic_plan(cues, resolved)
    assert first == second
    story, shots = first
    assert [act.name for act in story.acts] == [
        "Signal", "Awakening", "Departure", "Gates", "Rupture", "Transformation", "Arrival"
    ]
    assert story.acts[0].protagonist_state == ProtagonistState.SIGNALLED
    assert shots.shots[0].frame_start == cues.timeline.frame_start
    assert shots.shots[-1].frame_end == cues.timeline.frame_end
    assert all(
        current.frame_start == previous.frame_end + 1
        for previous, current in zip(shots.shots, shots.shots[1:], strict=False)
    )
    assert max(layer.strength for shot in shots.shots for layer in shot.reactive_layers) <= 0.25
    serialized = json.dumps(
        {"story": story.model_dump(mode="json", by_alias=True), "shots": shots.model_dump(mode="json", by_alias=True)},
        allow_nan=False,
    )
    for forbidden in ("sourcePath", "displayName", "lyrics", "transcript", "modelPath"):
        assert forbidden not in serialized


def test_weighted_ranges_are_stable_and_cover_every_frame() -> None:
    ranges = weighted_ranges(1, 701, [0.1, 0.12, 0.14, 0.16, 0.14, 0.18, 0.16])
    assert ranges[0][0] == 1
    assert ranges[-1][1] == 701
    assert sum(end - start + 1 for start, end in ranges) == 701
    assert all(current[0] == previous[1] + 1 for previous, current in zip(ranges, ranges[1:], strict=False))


@pytest.mark.parametrize(
    "payload",
    [
        {"sourcePath": "safe-looking"},
        {"nested": {"lyrics": "text"}},
        {"note": r"C:\\private\\source.wav"},
        {"note": "/home/private/source.wav"},
    ],
)
def test_privacy_validation_rejects_forbidden_fields_and_paths(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="privacy"):
        validate_cinematic_privacy(payload)


def test_director_workspace_reads_plans_and_atomically_upserts_review(
    click_analysis,
    tmp_path: Path,
) -> None:
    cues = _cues(click_analysis)
    resolved = resolve_visualizer_config(
        SpaceJourneyStoryVisualizerConfigRequest(preset="space-journey-story")
    )
    story, shots = compile_cinematic_plan(cues, resolved)
    jobs_root = tmp_path / "jobs"
    job_root = jobs_root / click_analysis.job_id
    job_root.mkdir(parents=True)
    (job_root / "story-plan.json").write_text(
        story.model_dump_json(by_alias=True), encoding="utf-8"
    )
    (job_root / "shot-plan.json").write_text(
        shots.model_dump_json(by_alias=True), encoding="utf-8"
    )
    config = MissionControlConfig(
        repository_root=tmp_path,
        state_root=tmp_path / "mission-control",
        profile_root=tmp_path / "profiles",
        calibration_root=tmp_path / "calibration",
        default_output_root=tmp_path / "output",
        native_dialog_enabled=False,
    )
    service = MissionControlService(config)
    try:
        workspace = service.director_workspace()
        assert workspace is not None
        shot = workspace.shot_plan.shots[0]
        review = ArtDirectionReview(
            shot_id=shot.id,
            review_frame=shot.review_frames[0],
            focal_readability="clear",
            depth="acceptable",
            silhouette="clear",
            color_hierarchy="acceptable",
            visual_density="acceptable",
            story_clarity="clear",
            mobile_readability="acceptable",
            findings=["Protagonist reads clearly at the representative frame."],
            decision="approve",
            revision_metadata={"revision": 1, "reviewer": "human", "note": "Local review."},
        )
        updated = service.put_director_review(click_analysis.job_id, shot.id, review)
        assert updated.reviews.reviews == [review]
        stored = json.loads((job_root / "art-direction-reviews.json").read_text(encoding="utf-8"))
        assert stored["reviews"][0]["shotId"] == shot.id
    finally:
        service.close()

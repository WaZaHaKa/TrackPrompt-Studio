from __future__ import annotations

from app.cinematic.compiler import compile_cinematic_plan
from app.cinematic.r12 import (
    R12_CONTINUOUS_FRAME_COUNT,
    R12_CONTINUOUS_FRAME_END,
    R12_CONTINUOUS_FRAME_START,
    R12_SHOT_CONTRACT,
    compile_r12_cinematic_plan,
)
from app.visualizer.presets import (
    SpaceJourneyStoryVisualizerConfigRequest,
    resolve_visualizer_config,
)
from app.visualizer.schemas import TrackPromptVisualCueSheet


def _cue_sheet() -> TrackPromptVisualCueSheet:
    return TrackPromptVisualCueSheet.model_validate(
        {
            "schemaVersion": "1.1.0",
            "source": {
                "analysisSchemaVersion": "1.4.0",
                "analysisVersion": "test",
                "jobId": "84291842-9184-4291-8429-184291842918",
                "requestedMode": "deep",
                "effectiveMode": "deep",
            },
            "timeline": {
                "durationSeconds": 42.0,
                "fps": 30,
                "frameStart": 1,
                "frameEnd": 1260,
                "framePolicy": "nearest-half-up-clamped",
            },
            "musicalGrid": {
                "bpm": {"value": 120.0, "confidence": "high"},
                "secondsPerBeat": 0.5,
                "meter": {"value": "4/4", "confidence": "high"},
                "downbeatsAvailable": True,
            },
            "beats": [],
            "onsets": [],
            "sections": [
                {
                    "id": "section-1",
                    "neutralLabel": "A",
                    "startSeconds": 0.0,
                    "endSeconds": 42.0,
                    "startFrame": 1,
                    "endFrame": 1260,
                    "energy": 0.65,
                    "confidence": "high",
                    "boundaryConfidence": "high",
                    "sourcePath": "structure.sections[0]",
                }
            ],
            "transitions": [],
            "curves": {
                "masterEnergy": {
                    "pointFormat": ["frame", "value"],
                    "points": [[1, 0.2], [630, 0.8], [1260, 0.4]],
                    "interpolation": "linear",
                    "sourceSampleRateHz": 20,
                    "originalPointCount": 3,
                    "exportedPointCount": 3,
                    "simplification": {
                        "method": "test",
                        "tolerance": 0.0,
                        "maximumError": 0.0,
                        "maximumPointCount": 32,
                    },
                    "normalization": {"normalizationGroup": "test"},
                    "smoothing": {
                        "attackSeconds": 0.08,
                        "releaseSeconds": 0.35,
                        "sourceSampleRateHz": 20,
                        "outputSampleRateHz": 20,
                    },
                }
            },
            "warnings": ["r12-public-cue-slice-224000-266000ms"],
        }
    )


def test_r12_refines_the_canonical_plan_without_mutating_it() -> None:
    cues = _cue_sheet()
    resolved = resolve_visualizer_config(
        SpaceJourneyStoryVisualizerConfigRequest(preset="space-journey-story")
    )
    canonical_story, canonical_shots = compile_cinematic_plan(cues, resolved)
    canonical_dump = canonical_shots.model_dump(mode="json", by_alias=True)

    story, refined = compile_r12_cinematic_plan(cues, resolved)

    assert story == canonical_story
    assert canonical_shots.model_dump(mode="json", by_alias=True) == canonical_dump
    assert refined.input_digest == story.input_digest == canonical_shots.input_digest
    assert [
        (shot.id, shot.frame_start, shot.frame_end)
        for shot in refined.shots
    ] == [
        (contract.identifier, contract.frame_start, contract.frame_end)
        for contract in R12_SHOT_CONTRACT
    ]
    assert all(
        following.frame_start == current.frame_end + 1
        for current, following in zip(refined.shots, refined.shots[1:], strict=False)
    )
    assert refined.shots[9].transition.value == "cut"
    assert all(shot.transition.value == "continuous" for shot in refined.shots[:9])


def test_r12_continuous_range_is_exact_and_cut_free() -> None:
    cues = _cue_sheet()
    resolved = resolve_visualizer_config(
        SpaceJourneyStoryVisualizerConfigRequest(preset="space-journey-story")
    )
    _story, shots = compile_r12_cinematic_plan(cues, resolved)
    bounded = [
        shot
        for shot in shots.shots
        if shot.frame_start >= R12_CONTINUOUS_FRAME_START
        and shot.frame_end <= R12_CONTINUOUS_FRAME_END
    ]
    assert [shot.act_id for shot in bounded] == [
        "awakening",
        "awakening",
        "departure",
        "departure",
        "departure",
        "gates",
        "gates",
        "gates",
    ]
    assert bounded[0].frame_start == R12_CONTINUOUS_FRAME_START
    assert bounded[-1].frame_end == R12_CONTINUOUS_FRAME_END
    assert sum(shot.duration_frames for shot in bounded) == R12_CONTINUOUS_FRAME_COUNT
    assert R12_CONTINUOUS_FRAME_COUNT / shots.fps == 529 / 30
    assert all(not shot.intentional_discontinuity for shot in bounded)

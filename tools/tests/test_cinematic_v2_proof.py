from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for source_root in (REPOSITORY_ROOT, REPOSITORY_ROOT / "backend"):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from app.cinematic.compiler import compile_cinematic_plan  # noqa: E402
from app.cinematic.schemas import ArtDirectionReviewCollection, ShotPlan, StoryPlan  # noqa: E402
from app.visualizer.presets import (  # noqa: E402
    SpaceJourneyStoryResolvedVisualizerConfig,
    SpaceJourneyStoryVisualizerConfigRequest,
    resolve_visualizer_config,
)
from app.visualizer.schemas import TrackPromptVisualCueSheet  # noqa: E402
from tools.generate_cinematic_v2_proof import (  # noqa: E402
    _register_director_artifacts,
    _synthetic_cues,
)
from tools.verify_cinematic_v2_proof import (  # noqa: E402
    _review_summary,
    _validate_stored_manifest,
    _verify_compiled_plan_identity,
)


def _shot(identifier: str, act_id: str, start: int) -> dict[str, object]:
    end = start + 9
    return {
        "id": identifier,
        "name": identifier,
        "actId": act_id,
        "frameStart": start,
        "frameEnd": end,
        "durationFrames": 10,
        "storyPurpose": "A safe local narrative beat.",
        "protagonistState": "travelling",
        "environment": {"environment": "gate_corridor", "secondaryAction": "bounded action"},
        "camera": {"rig": "gate_approach", "lensMm": 35, "framing": "wide", "movementProfile": "controlled_chase"},
        "composition": {
            "dominantShape": "threshold", "foreground": "occluder", "midgroundSubject": "orb",
            "backgroundLandmark": "gate", "atmosphere": "haze", "focalHierarchy": ["protagonist", "gate"],
        },
        "lighting": {"palette": "green", "keyDirection": "behind", "intensity": 0.5},
        "motion": {
            "profile": "controlled_chase", "interpolation": "BEZIER", "easeInFrames": 2,
            "easeOutFrames": 2, "maximumVelocity": 8, "maximumAcceleration": 2,
            "maximumAngularVelocity": 0.5,
        },
        "reactiveLayers": [],
        "transition": "continuous",
        "intentionalDiscontinuity": False,
        "reviewFrames": [start, start + 4, end],
    }


def _shots() -> ShotPlan:
    acts = ("signal", "awakening", "departure", "gates", "rupture", "transformation", "arrival")
    return ShotPlan.model_validate(
        {
            "seed": 84291,
            "frameStart": 1,
            "frameEnd": 70,
            "fps": 30,
            "inputDigest": "a" * 64,
            "shots": [_shot(f"shot-{index:02d}-{act}", act, index * 10 - 9) for index, act in enumerate(acts, start=1)],
        }
    )


def _reviews(*, mobile: str = "clear", decision: str = "approve") -> ArtDirectionReviewCollection:
    shots = _shots().shots[:4]
    return ArtDirectionReviewCollection.model_validate(
        {
            "reviews": [
                {
                    "shotId": shot.id,
                    "reviewFrame": shot.review_frames[1],
                    "focalReadability": "clear",
                    "depth": "acceptable",
                    "silhouette": "clear",
                    "colorHierarchy": "clear",
                    "visualDensity": "acceptable",
                    "storyClarity": "clear",
                    "mobileReadability": mobile,
                    "findings": [f"{shot.act_id} has a distinct landmark and readable mobile silhouette."],
                    "decision": decision,
                    "revisionMetadata": {"revision": 5, "reviewer": "codex-assisted", "note": "Phone-size proof reviewed."},
                }
                for shot in shots
            ]
        }
    )


def _compiled_plan_fixture() -> tuple[
    TrackPromptVisualCueSheet,
    StoryPlan,
    ShotPlan,
    dict[str, object],
]:
    cues = _synthetic_cues()
    resolved = resolve_visualizer_config(
        SpaceJourneyStoryVisualizerConfigRequest(
            preset="space-journey-story",
            seed=84291,
        )
    )
    assert isinstance(resolved, SpaceJourneyStoryResolvedVisualizerConfig)
    story, shots = compile_cinematic_plan(cues, resolved)
    visualizer_config = resolved.model_dump(mode="json", by_alias=True)
    # Match the Blender manifest split: public config warnings remain empty while
    # preset-level warnings are stored at the scene-manifest top level.
    visualizer_config["warnings"] = []
    scene_manifest: dict[str, object] = {
        "seed": resolved.seed,
        "visualizerConfig": visualizer_config,
        "warnings": resolved.warnings,
    }
    return cues, story, shots, scene_manifest


def test_plan_binding_recompiles_exact_story_and_shot_plans() -> None:
    cues, story, shots, scene_manifest = _compiled_plan_fixture()
    _verify_compiled_plan_identity(cues, story, shots, scene_manifest)


@pytest.mark.parametrize("tamper", ["cue", "input-digest", "shot", "configuration"])
def test_plan_binding_rejects_identity_consistent_plan_tampering(tamper: str) -> None:
    cues, story, shots, scene_manifest = _compiled_plan_fixture()
    if tamper == "cue":
        cue_payload = cues.model_dump(mode="json", by_alias=True)
        cue_payload["curves"]["masterEnergy"]["points"][1][1] = 0.6123
        cues = TrackPromptVisualCueSheet.model_validate(cue_payload)
    elif tamper == "input-digest":
        story_payload = story.model_dump(mode="json", by_alias=True)
        shot_payload = shots.model_dump(mode="json", by_alias=True)
        story_payload["inputDigest"] = "f" * 64
        shot_payload["inputDigest"] = "f" * 64
        story = StoryPlan.model_validate(story_payload)
        shots = ShotPlan.model_validate(shot_payload)
    elif tamper == "shot":
        shot_payload = shots.model_dump(mode="json", by_alias=True)
        shot_payload["shots"][0]["composition"]["dominantShape"] = "tampered landmark"
        shots = ShotPlan.model_validate(shot_payload)
    else:
        scene_payload = copy.deepcopy(scene_manifest)
        config = scene_payload["visualizerConfig"]
        assert isinstance(config, dict)
        parameters = config["parameters"]
        assert isinstance(parameters, dict)
        parameters["glowStrength"] = 2.1
        scene_manifest = scene_payload

    with pytest.raises(RuntimeError, match="deterministic compilation"):
        _verify_compiled_plan_identity(cues, story, shots, scene_manifest)


def test_review_summary_accepts_only_complete_clear_approval_without_mutating_review() -> None:
    reviews = _reviews()
    before = copy.deepcopy(reviews.model_dump(mode="json"))
    summary = _review_summary(reviews, _shots())
    assert summary == {
        "decision": "approve",
        "artisticGatesPassed": True,
        "artistApproved": False,
        "reviewCount": 4,
        "reviewers": ["codex-assisted"],
    }
    assert reviews.model_dump(mode="json") == before


def test_review_summary_rejects_approval_without_clear_mobile_gate() -> None:
    with pytest.raises(RuntimeError, match="clear mobile readability"):
        _review_summary(_reviews(mobile="acceptable"), _shots())


def test_review_summary_preserves_a_truthful_revision_decision() -> None:
    summary = _review_summary(_reviews(decision="revise"), _shots())
    assert summary["decision"] == "revise"
    assert summary["artisticGatesPassed"] is False


def test_stored_manifest_validation_binds_plan_and_scene_manifest_identities(tmp_path: Path) -> None:
    preview = tmp_path / "preview-signal-to-gate"
    preview.mkdir()

    def write_json(path: Path, payload: dict[str, object]) -> None:
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    expected: dict[str, object] = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-preview-manifest",
        "preset": "space-journey-story",
        "previewOnly": True,
        "analysisJobId": "00000000-0000-4000-8000-000000000001",
        "boundedStory": ["Signal", "Awakening", "Departure", "First Gate"],
        "reviewEdit": {"strategy": "six-authored-motion-excerpts"},
        "stills": [{"file": "frame.png", "sha256": "a" * 64}],
        "phoneStills": [{"file": "phone/frame.png", "sha256": "b" * 64}],
        "clip": {"file": "preview.mp4", "sha256": "c" * 64},
        "renderReceipt": {"file": "mcp-render-receipt.json", "sha256": "e" * 64},
        "review": {"decision": "approve", "artistApproved": False},
        "planIdentity": {"seed": 84291, "storyPlanSha256": "d" * 64},
        "production": {"fullTimelineRendered": False, "authorizationUsed": False},
    }
    preview_manifest_path = preview / "preview-manifest.json"
    write_json(preview_manifest_path, expected)
    for name, content in (
        ("visual-cues.json", b"visual-cues"),
        ("story-plan.json", b"story-plan"),
        ("shot-plan.json", b"shot-plan"),
        ("art-direction-reviews.json", b"reviews"),
        ("preview-signal-to-gate/mcp-render-receipt.json", b"receipt"),
        ("story.blend", b"blend"),
        ("story.manifest.json", b"scene-manifest"),
    ):
        (tmp_path / name).write_bytes(content)
    scene = (tmp_path / "story.blend").resolve()
    scene_manifest = (tmp_path / "story.manifest.json").resolve()
    build = {
        "preset": "space-journey-story",
        "previewOnly": True,
        "fullTimelineRenderAuthorized": False,
        "analysisInputs": {
            "jobId": expected["analysisJobId"],
            "cueSha256": digest(tmp_path / "visual-cues.json"),
        },
        "storyPlan": {"sha256": digest(tmp_path / "story-plan.json")},
        "shotPlan": {"sha256": digest(tmp_path / "shot-plan.json")},
        "reviewArtifact": {"sha256": digest(tmp_path / "art-direction-reviews.json")},
        "previewArtifact": {
            "path": "preview-signal-to-gate/preview-manifest.json",
            "sha256": digest(preview_manifest_path),
        },
        "renderReceiptArtifact": {
            "path": "preview-signal-to-gate/mcp-render-receipt.json",
            "sha256": digest(preview / "mcp-render-receipt.json"),
        },
        "sceneArtifact": {
            "file": scene.name,
            "sha256": digest(scene),
            "manifestFile": scene_manifest.name,
            "manifestSha256": digest(scene_manifest),
            "previewOnly": True,
            "v2CalibrationRequired": True,
        },
    }
    build_path = tmp_path / "build-manifest.json"
    write_json(build_path, build)
    _validate_stored_manifest(
        tmp_path.resolve(),
        preview.resolve(),
        expected,
        scene_path=scene,
        scene_manifest_path=scene_manifest,
    )

    stale_preview = copy.deepcopy(expected)
    stale_preview["planIdentity"] = {"seed": 1}
    write_json(preview_manifest_path, stale_preview)
    with pytest.raises(RuntimeError, match="planIdentity"):
        _validate_stored_manifest(
            tmp_path.resolve(),
            preview.resolve(),
            expected,
            scene_path=scene,
            scene_manifest_path=scene_manifest,
        )

    write_json(preview_manifest_path, expected)
    build["previewArtifact"]["sha256"] = digest(preview_manifest_path)
    build["sceneArtifact"]["manifestSha256"] = "0" * 64
    write_json(build_path, build)
    with pytest.raises(RuntimeError, match="scene identity"):
        _validate_stored_manifest(
            tmp_path.resolve(),
            preview.resolve(),
            expected,
            scene_path=scene,
            scene_manifest_path=scene_manifest,
        )


def test_director_registration_preserves_existing_reviews_and_rejects_plan_conflicts(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    story = {"schemaVersion": "1.0.0", "acts": []}
    shots = _shots().model_dump(mode="json", by_alias=True)
    reviews = _reviews(decision="revise").model_dump(mode="json", by_alias=True)
    job_id = "00000000-0000-4000-8000-000000000001"
    first = _register_director_artifacts(jobs_root, job_id, story, shots, reviews)
    assert first["reviewsPreserved"] is False

    review_path = first["jobRoot"] / "art-direction-reviews.json"
    persisted = json.loads(review_path.read_text(encoding="utf-8"))
    persisted["reviews"][0]["revisionMetadata"]["revision"] = 5
    review_path.write_text(json.dumps(persisted), encoding="utf-8")
    second = _register_director_artifacts(jobs_root, job_id, story, shots, reviews)
    assert second["reviewsPreserved"] is True
    assert json.loads(review_path.read_text(encoding="utf-8")) == persisted

    with pytest.raises(RuntimeError, match="different identity-bound cinematic plan"):
        _register_director_artifacts(
            jobs_root,
            job_id,
            {**story, "acts": [{"id": "different"}]},
            shots,
            reviews,
        )


def test_director_registration_never_overwrites_a_concurrently_created_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    story = {"schemaVersion": "1.0.0", "acts": []}
    shots = _shots().model_dump(mode="json", by_alias=True)
    initial_reviews = _reviews(decision="revise").model_dump(mode="json", by_alias=True)
    concurrent_reviews = copy.deepcopy(initial_reviews)
    concurrent_reviews["reviews"][0]["revisionMetadata"]["revision"] = 9
    concurrent_reviews["reviews"][0]["findings"] = ["Director review won the publication race."]
    attempted_destinations: list[str] = []
    original_link = os.link

    def create_concurrent_review_then_link(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: object,
        **kwargs: object,
    ) -> None:
        destination_path = Path(destination)
        attempted_destinations.append(destination_path.name)
        if destination_path.name == "art-direction-reviews.json" and not destination_path.exists():
            destination_path.write_text(json.dumps(concurrent_reviews), encoding="utf-8")
        original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", create_concurrent_review_then_link)
    result = _register_director_artifacts(
        jobs_root,
        "00000000-0000-4000-8000-000000000001",
        story,
        shots,
        initial_reviews,
    )

    review_path = result["jobRoot"] / "art-direction-reviews.json"
    assert result["reviewsPreserved"] is True
    assert attempted_destinations[0] == "art-direction-reviews.json"
    assert json.loads(review_path.read_text(encoding="utf-8")) == concurrent_reviews
    assert (result["jobRoot"] / "story-plan.json").exists()
    assert (result["jobRoot"] / "shot-plan.json").exists()

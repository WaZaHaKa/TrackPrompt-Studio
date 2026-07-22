from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.cinematic.compiler import compile_cinematic_plan  # noqa: E402
from app.cinematic.schemas import ArtDirectionReview, ArtDirectionReviewCollection  # noqa: E402
from app.cinematic.validation import validate_cinematic_privacy, validate_plan_pair  # noqa: E402
from app.visualizer.presets import (  # noqa: E402
    SpaceJourneyStoryResolvedVisualizerConfig,
    SpaceJourneyStoryVisualizerConfigRequest,
    resolve_visualizer_config,
)
from app.visualizer.schemas import TrackPromptVisualCueSheet  # noqa: E402
from app.visualizer.validation import validate_public_cue_sheet  # noqa: E402


def _synthetic_cues() -> TrackPromptVisualCueSheet:
    frames = [1, 50, 100, 150, 200, 250, 300]
    values = [0.16, 0.28, 0.43, 0.68, 0.88, 0.56, 0.34]
    curve = {
        "pointFormat": ["frame", "value"],
        "points": list(zip(frames, values, strict=True)),
        "interpolation": "linear",
        "sourceSampleRateHz": 20,
        "originalPointCount": 201,
        "exportedPointCount": len(frames),
        "simplification": {
            "method": "bounded-synthetic",
            "tolerance": 0,
            "maximumError": 0,
            "maximumPointCount": 64,
        },
        "normalization": {"normalizationGroup": "synthetic"},
        "smoothing": {
            "attackSeconds": 0.08,
            "releaseSeconds": 0.35,
            "sourceSampleRateHz": 20,
            "outputSampleRateHz": 20,
        },
    }
    payload = {
        "schemaVersion": "1.1.0",
        "source": {
            "analysisSchemaVersion": "1.4.0",
            "analysisVersion": "synthetic-proof-1",
            "jobId": "84291842-9184-4291-8429-184291842918",
            "requestedMode": "fast",
            "effectiveMode": "fast",
        },
        "timeline": {
            "durationSeconds": 10.0,
            "fps": 30,
            "frameStart": 1,
            "frameEnd": 300,
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
                "id": f"section-{index + 1}",
                "neutralLabel": label,
                "startSeconds": index * 2.5,
                "endSeconds": (index + 1) * 2.5,
                "startFrame": index * 75 + 1,
                "endFrame": (index + 1) * 75,
                "energy": 0.25 + index * 0.16,
                "confidence": "high",
                "boundaryConfidence": "high",
                "sourcePath": "synthetic.sections",
            }
            for index, label in enumerate(("A", "B", "C", "D"))
        ],
        "transitions": [
            {
                "id": f"transition-{index}",
                "timeSeconds": index * 2.5,
                "frame": (index - 1) * 75 + 76,
                "fromSectionId": f"section-{index}",
                "toSectionId": f"section-{index + 1}",
                "energyBefore": 0.25 + (index - 1) * 0.16,
                "energyAfter": 0.25 + index * 0.16,
                "energyDelta": 0.16,
                "direction": "rising",
                "confidence": "high",
                "sourcePaths": ["synthetic.sections", "synthetic.curves"],
            }
            for index in range(1, 4)
        ],
        "curves": {
            name: curve
            for name in (
                "masterEnergy",
                "drumEnergy",
                "bassEnergy",
                "vocalEnergy",
                "lowBandEnergy",
                "midBandEnergy",
                "highBandEnergy",
                "brightness",
                "transientActivity",
            )
        },
        "warnings": ["synthetic-bounded-proof"],
    }
    return TrackPromptVisualCueSheet.model_validate(payload)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any], *, validate_privacy: bool = True) -> None:
    if validate_privacy:
        validate_cinematic_privacy(payload)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_exclusive(
    path: Path,
    payload: dict[str, Any],
    *,
    validate_privacy: bool = True,
) -> bool:
    """Publish a complete JSON file without replacing a concurrently created target."""
    if validate_privacy:
        validate_cinematic_privacy(payload)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _register_director_artifacts(
    jobs_root: Path,
    job_id: str,
    story_payload: dict[str, Any],
    shot_payload: dict[str, Any],
    review_payload: dict[str, Any],
) -> dict[str, Any]:
    resolved_jobs_root = jobs_root.resolve(strict=True)
    director_job_root = (resolved_jobs_root / str(UUID(job_id))).resolve()
    if director_job_root.parent != resolved_jobs_root:
        raise RuntimeError("Director job root escaped the configured jobs directory.")
    director_job_root.mkdir(exist_ok=True)

    plan_artifacts = [
        (director_job_root / name, payload)
        for name, payload in (("story-plan.json", story_payload), ("shot-plan.json", shot_payload))
    ]
    def validate_plan(path: Path, payload: dict[str, Any]) -> None:
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
        if existing != payload:
            raise RuntimeError("Director already contains a different identity-bound cinematic plan.")

    for path, payload in plan_artifacts:
        if path.exists():
            validate_plan(path, payload)

    review_path = director_job_root / "art-direction-reviews.json"
    expected_shots = {
        str(shot["id"])
        for shot in shot_payload.get("shots", [])
        if isinstance(shot, dict) and shot.get("actId") in {"signal", "awakening", "departure", "gates"}
    }

    def validate_reviews() -> None:
        existing_reviews = ArtDirectionReviewCollection.model_validate_json(
            review_path.read_text(encoding="utf-8-sig")
        )
        validate_cinematic_privacy(existing_reviews.model_dump(mode="json", by_alias=True))
        if {review.shot_id for review in existing_reviews.reviews} != expected_shots:
            raise RuntimeError("Director reviews do not match the identity-bound bounded shots.")

    # Publish the safe initial review before exposing either half of the plan pair.
    # The hard-link is an atomic create: if Director wins the race, its review is
    # preserved and validated instead of being replaced by the generator.
    reviews_preserved = not _write_json_exclusive(review_path, review_payload)
    validate_reviews()

    for path, payload in plan_artifacts:
        if not _write_json_exclusive(path, payload):
            validate_plan(path, payload)
    return {
        "jobRoot": director_job_root,
        "reviewsPreserved": reviews_preserved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate bounded Cinematic Visualizer V2 proof artifacts.")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument(
        "--cue-sheet",
        type=Path,
        help="Use an existing privacy-minimized TrackPrompt cue sheet instead of synthetic proof cues.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--director-jobs-root",
        type=Path,
        help="Optionally register the validated plan pair in Mission Control's UUID job root.",
    )
    parser.add_argument("--seed", type=int, default=84291)
    args = parser.parse_args()
    audio_path = args.audio.resolve(strict=True)
    output = args.output.resolve()
    cue_source = args.cue_sheet.resolve(strict=True) if args.cue_sheet is not None else None
    cues = (
        TrackPromptVisualCueSheet.model_validate_json(cue_source.read_text(encoding="utf-8-sig"))
        if cue_source is not None
        else _synthetic_cues()
    )
    validate_public_cue_sheet(cues)
    resolved = resolve_visualizer_config(
        SpaceJourneyStoryVisualizerConfigRequest(
            preset="space-journey-story",
            seed=args.seed,
        )
    )
    if not isinstance(resolved, SpaceJourneyStoryResolvedVisualizerConfig):
        raise RuntimeError("V2 visualizer configuration did not resolve to the story preset.")
    story, shots = compile_cinematic_plan(cues, resolved)
    validate_plan_pair(story, shots)
    output.mkdir(parents=True, exist_ok=False)
    cue_path = output / "visual-cues.json"
    _write_json(cue_path, cues.model_dump(mode="json", by_alias=True), validate_privacy=False)
    first_four = {"signal", "awakening", "departure", "gates"}
    reviews = ArtDirectionReviewCollection(
        reviews=[
            ArtDirectionReview(
                shot_id=shot.id,
                review_frame=shot.review_frames[len(shot.review_frames) // 2],
                focal_readability="unknown",
                depth="unknown",
                silhouette="unknown",
                color_hierarchy="unknown",
                visual_density="unknown",
                story_clarity="unknown",
                mobile_readability="unknown",
                findings=["Awaiting representative still review."],
                decision="revise",
                revision_metadata={
                    "revision": 1,
                    "reviewer": "codex-assisted",
                    "note": (
                        "Real-analysis proof initialized for bounded local review."
                        if cue_source is not None
                        else "Synthetic proof initialized without claiming artist approval."
                    ),
                },
            )
            for shot in shots.shots
            if shot.act_id in first_four
        ]
    )
    story_path = output / "story-plan.json"
    shot_path = output / "shot-plan.json"
    review_path = output / "art-direction-reviews.json"
    _write_json(story_path, story.model_dump(mode="json", by_alias=True))
    _write_json(shot_path, shots.model_dump(mode="json", by_alias=True))
    _write_json(review_path, reviews.model_dump(mode="json", by_alias=True))
    source_inputs = (
        {
            "analysisInputs": {
                "jobId": cues.source.job_id,
                "analysisSchemaVersion": cues.source.analysis_schema_version,
                "analysisVersion": cues.source.analysis_version,
                "requestedMode": cues.source.requested_mode,
                "effectiveMode": cues.source.effective_mode,
                "sourceCueSha256": _sha256(cue_source),
                "cueSha256": _sha256(cue_path),
                "cueSizeBytes": cue_path.stat().st_size,
                "audioSha256": _sha256(audio_path),
                "audioSizeBytes": audio_path.stat().st_size,
                "durationSeconds": cues.timeline.duration_seconds,
                "frameStart": cues.timeline.frame_start,
                "frameEnd": cues.timeline.frame_end,
                "fps": cues.timeline.fps,
            }
        }
        if cue_source is not None
        else {
            "syntheticInputs": {
                "cueSha256": _sha256(cue_path),
                "audioSha256": _sha256(audio_path),
                "audioSizeBytes": audio_path.stat().st_size,
            }
        }
    )
    manifest = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-bounded-proof",
        "createdAt": datetime.now(UTC).isoformat(),
        "preset": "space-journey-story",
        "previewOnly": True,
        "fullTimelineRenderAuthorized": False,
        **source_inputs,
        "storyPlan": {"sha256": _sha256(story_path), "actCount": len(story.acts)},
        "shotPlan": {"sha256": _sha256(shot_path), "shotCount": len(shots.shots)},
        "reviewArtifact": {"sha256": _sha256(review_path), "reviewCount": len(reviews.reviews)},
        "boundedActs": ["Signal", "Awakening", "Departure", "Gates"],
    }
    manifest_path = output / "build-manifest.json"
    _write_json(manifest_path, manifest)
    director_job_root: Path | None = None
    director_reviews_preserved = False
    if args.director_jobs_root is not None:
        registration = _register_director_artifacts(
            args.director_jobs_root,
            str(cues.source.job_id),
            story.model_dump(mode="json", by_alias=True),
            shots.model_dump(mode="json", by_alias=True),
            reviews.model_dump(mode="json", by_alias=True),
        )
        director_job_root = registration["jobRoot"]
        director_reviews_preserved = bool(registration["reviewsPreserved"])
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "storyPlan": str(story_path),
                "shotPlan": str(shot_path),
                "reviews": str(review_path),
                "manifest": str(manifest_path),
                "directorRegistered": director_job_root is not None,
                "directorReviewsPreserved": director_reviews_preserved,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

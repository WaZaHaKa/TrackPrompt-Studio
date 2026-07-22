from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
for source_root in (REPOSITORY_ROOT, BACKEND_ROOT):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from app.cinematic.r12 import (  # noqa: E402
    R12_CONTINUOUS_FRAME_COUNT,
    R12_CONTINUOUS_FRAME_END,
    R12_CONTINUOUS_FRAME_START,
    R12_REVISION_ID,
    R12_SHOT_CONTRACT,
    compile_r12_cinematic_plan,
)
from app.cinematic.schemas import ShotPlan, StoryPlan  # noqa: E402
from app.cinematic.validation import validate_cinematic_privacy, validate_plan_pair  # noqa: E402
from app.visualizer.presets import (  # noqa: E402
    SpaceJourneyStoryResolvedVisualizerConfig,
    SpaceJourneyStoryVisualizerConfigRequest,
    resolve_visualizer_config,
)
from app.visualizer.schemas import TrackPromptVisualCueSheet  # noqa: E402
from app.visualizer.validation import validate_public_cue_sheet  # noqa: E402
from tools.cinematic_v2_slice import (  # noqa: E402
    R12_SLICE_DURATION_SECONDS,
    R12_SOURCE_END_SECONDS,
    R12_SOURCE_START_SECONDS,
    derive_r12_cue_slice,
    r12_cue_slice_contract,
)
from tools.generate_cinematic_v2_r12_proof import (  # noqa: E402
    R12_REVIEW_CRITERIA,
    build_r11_human_override,
    build_r12_review_packet,
    collect_r11_evidence_identity,
    sha256_file,
)
from tools.finalize_cinematic_v2_r12_media import (  # noqa: E402
    build_r12_director_review,
)

_REQUIRED_MEDIA_ARTIFACTS = {
    "profiles": ["landscape", "vertical"],
    "receiptCount": 2,
    "clipCount": 2,
    "fullResolutionStillCount": 16,
    "phoneStillCount": 16,
    "motionReportCount": 2,
    "exposureReportCount": 2,
}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return payload


def _artifact(root: Path, reference: object, label: str) -> Path:
    if not isinstance(reference, dict):
        raise ValueError(f"R12 manifest has no {label} reference.")
    filename = reference.get("file")
    expected_hash = reference.get("sha256")
    if not isinstance(filename, str) or not isinstance(expected_hash, str):
        raise ValueError(f"R12 {label} reference is incomplete.")
    path = (root / filename).resolve(strict=True)
    if path.parent != root or not path.is_file() or sha256_file(path) != expected_hash:
        raise ValueError(f"R12 {label} artifact hash is stale or unsafe.")
    return path


def _media_artifact(root: Path, reference: object, label: str) -> Path:
    if not isinstance(reference, dict):
        raise ValueError(f"R12 completed media proof has no {label} reference.")
    filename = reference.get("file")
    expected_hash = reference.get("sha256")
    if not isinstance(filename, str) or not isinstance(expected_hash, str):
        raise ValueError(f"R12 completed media {label} reference is incomplete.")
    path = (root / filename).resolve(strict=True)
    if (
        path == root
        or root not in path.parents
        or not path.is_file()
        or sha256_file(path) != expected_hash
    ):
        raise ValueError(f"R12 completed media {label} hash is stale or unsafe.")
    return path


def _validate_media_extension(root: Path, payload: object) -> dict[str, Any]:
    """Validate pending state now and generic hashes when the MCP receipt contract lands."""

    if not isinstance(payload, dict):
        raise ValueError("R12 media proof contract is missing.")
    status = payload.get("status")
    if status == "pending":
        if payload.get("requiredArtifacts") != _REQUIRED_MEDIA_ARTIFACTS or "profiles" in payload:
            raise ValueError("R12 pending media proof contract has drifted.")
        return {"status": "pending", "artifactCount": 0, "profiles": None}
    if status != "complete":
        raise ValueError("R12 media proof status must be pending or complete.")
    if payload.get("requiredArtifacts") != _REQUIRED_MEDIA_ARTIFACTS:
        raise ValueError("Completed R12 media proof requirement counts have drifted.")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != {"landscape", "vertical"}:
        raise ValueError("Completed R12 media proof requires landscape and vertical profiles.")
    expected_dimensions = {
        "landscape": ((1920, 1080), (320, 180)),
        "vertical": ((1080, 1920), (180, 320)),
    }
    referenced_files: set[Path] = set()
    artifact_count = 0
    for profile_id, ((width, height), (phone_width, phone_height)) in expected_dimensions.items():
        profile = profiles[profile_id]
        if (
            not isinstance(profile, dict)
            or profile.get("width") != width
            or profile.get("height") != height
        ):
            raise ValueError(f"R12 {profile_id} media profile dimensions are invalid.")
        for field in ("receipt", "clip", "motionReport", "exposureReport"):
            path = _media_artifact(root, profile.get(field), f"{profile_id} {field}")
            if path in referenced_files:
                raise ValueError("R12 media proof reuses an artifact for multiple evidence roles.")
            referenced_files.add(path)
            artifact_count += 1
        for field, expected_size, expected_count in (
            ("fullResolutionStills", (width, height), 8),
            ("phoneStills", (phone_width, phone_height), 8),
        ):
            stills = profile.get(field)
            if not isinstance(stills, list) or len(stills) != expected_count:
                raise ValueError(f"R12 {profile_id} {field} must contain eight stills.")
            frames: set[int] = set()
            for index, still in enumerate(stills):
                if (
                    not isinstance(still, dict)
                    or isinstance(still.get("frame"), bool)
                    or not isinstance(still.get("frame"), int)
                    or not isinstance(still.get("role"), str)
                    or (still.get("width"), still.get("height")) != expected_size
                ):
                    raise ValueError(f"R12 {profile_id} {field} contains an invalid still contract.")
                frame = int(still["frame"])
                if frame in frames:
                    raise ValueError(f"R12 {profile_id} {field} contains duplicate frames.")
                frames.add(frame)
                path = _media_artifact(
                    root,
                    still,
                    f"{profile_id} {field} item {index + 1}",
                )
                if path in referenced_files:
                    raise ValueError("R12 media proof reuses an artifact for multiple evidence roles.")
                referenced_files.add(path)
                artifact_count += 1
    if artifact_count != 40:
        raise RuntimeError("R12 completed media proof artifact count is inconsistent.")
    return {
        "status": "complete",
        "artifactCount": artifact_count,
        "profiles": profiles,
    }


def verify_r12_proof(
    *,
    root: Path,
    source_cue_path: Path,
    source_audio_path: Path,
    slice_audio_path: Path,
    r11_root: Path,
) -> dict[str, Any]:
    proof_root = root.resolve(strict=True)
    cue_source_path = source_cue_path.resolve(strict=True)
    audio_path = source_audio_path.resolve(strict=True)
    audio_slice_path = slice_audio_path.resolve(strict=True)
    if (
        not proof_root.is_dir()
        or not cue_source_path.is_file()
        or not audio_path.is_file()
        or not audio_slice_path.is_file()
    ):
        raise ValueError("R12 proof and source inputs must exist.")

    build = _read_json(proof_root / "build-manifest.json")
    validate_cinematic_privacy(build)
    if (
        build.get("kind") != "trackprompt-cinematic-v2-r12-bounded-proof"
        or build.get("revisionId") != R12_REVISION_ID
        or build.get("preset") != "space-journey-story"
        or build.get("previewOnly") is not True
        or build.get("fullTimelineRenderAuthorized") is not False
        or build.get("v2CalibrationPerformed") is not False
        or build.get("cloudProvisioned") is not False
    ):
        raise ValueError("R12 build manifest violates its preview-only safety boundary.")

    source = TrackPromptVisualCueSheet.model_validate_json(
        cue_source_path.read_text(encoding="utf-8-sig")
    )
    validate_public_cue_sheet(source)
    expected_derived = derive_r12_cue_slice(source)
    derived_path = proof_root / "visual-cues.json"
    derived = TrackPromptVisualCueSheet.model_validate_json(
        derived_path.read_text(encoding="utf-8-sig")
    )
    validate_public_cue_sheet(derived)
    if derived != expected_derived:
        raise ValueError("R12 stored cue sheet does not match deterministic source derivation.")

    analysis_inputs = build.get("analysisInputs")
    if not isinstance(analysis_inputs, dict) or analysis_inputs != {
        "jobId": str(source.source.job_id),
        "analysisSchemaVersion": source.source.analysis_schema_version,
        "analysisVersion": source.source.analysis_version,
        "sourceCueSha256": sha256_file(cue_source_path),
        "derivedCueSha256": sha256_file(derived_path),
        "sourceAudioSha256": sha256_file(audio_path),
        "sourceAudioSizeBytes": audio_path.stat().st_size,
    }:
        raise ValueError("R12 analysis input identities do not match the supplied sources.")

    slice_path = _artifact(proof_root, build.get("cueSliceArtifact"), "cue slice")
    expected_slice = r12_cue_slice_contract(source, derived)
    expected_slice.update(
        {
            "sourceCueSha256": sha256_file(cue_source_path),
            "derivedCueSha256": sha256_file(derived_path),
            "sourceAudioSha256": sha256_file(audio_path),
            "sourceAudioSizeBytes": audio_path.stat().st_size,
            "sliceAudioArtifactName": "r12-source-window-audio",
            "sliceAudioSha256": sha256_file(audio_slice_path),
            "sliceAudioSizeBytes": audio_slice_path.stat().st_size,
        }
    )
    if _read_json(slice_path) != expected_slice:
        raise ValueError("R12 cue-slice artifact does not match deterministic derivation.")
    audio_slice = build.get("audioSlice")
    if not isinstance(audio_slice, dict) or audio_slice != {
        "artifactName": "r12-source-window-audio",
        "sha256": sha256_file(audio_slice_path),
        "sizeBytes": audio_slice_path.stat().st_size,
        "sourceStartSeconds": R12_SOURCE_START_SECONDS,
        "sourceEndSeconds": R12_SOURCE_END_SECONDS,
        "durationSeconds": R12_SLICE_DURATION_SECONDS,
        "ignoredLocalArtifact": True,
        "committed": False,
    }:
        raise ValueError("R12 ignored audio-slice identity is stale or missing.")
    if sha256_file(audio_slice_path) == sha256_file(audio_path):
        raise ValueError("R12 audio slice is not distinct from the full source audio.")

    story_path = _artifact(proof_root, build.get("storyPlan"), "story plan")
    shot_path = _artifact(proof_root, build.get("shotPlan"), "shot plan")
    story = StoryPlan.model_validate_json(story_path.read_text(encoding="utf-8-sig"))
    shots = ShotPlan.model_validate_json(shot_path.read_text(encoding="utf-8-sig"))
    validate_plan_pair(story, shots)
    resolved = resolve_visualizer_config(
        SpaceJourneyStoryVisualizerConfigRequest(
            preset="space-journey-story",
            seed=shots.seed,
        )
    )
    if not isinstance(resolved, SpaceJourneyStoryResolvedVisualizerConfig):
        raise RuntimeError("R12 verification configuration resolved incorrectly.")
    expected_story, expected_shots = compile_r12_cinematic_plan(derived, resolved)
    if story != expected_story or shots != expected_shots:
        raise ValueError("R12 StoryPlan or refined ShotPlan does not recompile exactly.")
    expected_ranges = [
        (contract.identifier, contract.frame_start, contract.frame_end)
        for contract in R12_SHOT_CONTRACT
    ]
    actual_ranges = [(shot.id, shot.frame_start, shot.frame_end) for shot in shots.shots]
    if actual_ranges != expected_ranges:
        raise ValueError("R12 refined shot ranges do not match the frozen contract.")

    override_path = _artifact(
        proof_root,
        build.get("r11HumanOverrideArtifact"),
        "R11 human override",
    )
    expected_override = build_r11_human_override(collect_r11_evidence_identity(r11_root))
    if _read_json(override_path) != expected_override:
        raise ValueError("R11 human override is not bound to the preserved R11 evidence.")
    review_path = _artifact(
        proof_root,
        build.get("r12ReviewPacketArtifact"),
        "R12 artistic review packet",
    )
    review_packet = _read_json(review_path)
    if review_packet != build_r12_review_packet():
        raise ValueError("R12 artistic review packet has drifted from its pending-human contract.")
    if (
        len(review_packet["codexAssisted"]["criteria"]) != len(R12_REVIEW_CRITERIA)
        or review_packet["humanReview"]["status"] != "pending-human-review"
        or review_packet["humanReview"]["approved"] is not False
        or review_packet["artistApproved"] is not False
    ):
        raise ValueError("R12 review packet improperly claims artistic approval.")

    continuous = build.get("continuousProof")
    duration = R12_CONTINUOUS_FRAME_COUNT / shots.fps
    slice_audio_start = (R12_CONTINUOUS_FRAME_START - derived.timeline.frame_start) / shots.fps
    source_audio_start = R12_SOURCE_START_SECONDS + slice_audio_start
    if (
        not isinstance(continuous, dict)
        or continuous.get("frameStart") != R12_CONTINUOUS_FRAME_START
        or continuous.get("frameEnd") != R12_CONTINUOUS_FRAME_END
        or continuous.get("frameCount") != R12_CONTINUOUS_FRAME_COUNT
        or not math.isclose(float(continuous.get("durationSeconds", 0.0)), duration)
        or not math.isclose(
            float(continuous.get("sliceAudioStartSeconds", -1.0)),
            slice_audio_start,
        )
        or not math.isclose(
            float(continuous.get("sliceAudioEndSeconds", -1.0)),
            slice_audio_start + duration,
        )
        or not math.isclose(
            float(continuous.get("sourceAudioStartSeconds", -1.0)),
            source_audio_start,
        )
        or not math.isclose(
            float(continuous.get("sourceAudioEndSeconds", -1.0)),
            source_audio_start + duration,
        )
        or continuous.get("expectedRenderProfiles")
        != [
            {"id": "landscape", "width": 1920, "height": 1080},
            {"id": "vertical", "width": 1080, "height": 1920},
        ]
    ):
        raise ValueError("R12 continuous proof range or responsive profile contract has drifted.")
    media = _validate_media_extension(proof_root, build.get("mediaProof"))
    director_review_reference = build.get("r12DirectorReviewArtifact")
    if media["status"] == "pending":
        if director_review_reference is not None:
            raise ValueError("Pending R12 media must not claim a completed Director review.")
    else:
        if (
            not isinstance(director_review_reference, dict)
            or director_review_reference.get("file") != "r12-director-review.json"
            or director_review_reference.get("codexAssistedDecision") != "revise"
            or director_review_reference.get("humanApprovalStatus")
            != "pending-human-review"
            or director_review_reference.get("artistApproved") is not False
        ):
            raise ValueError("Completed R12 media has no truthful Director review reference.")
        director_review_path = _artifact(
            proof_root,
            director_review_reference,
            "Director review",
        )
        profiles = media.get("profiles")
        if not isinstance(profiles, dict) or _read_json(
            director_review_path
        ) != build_r12_director_review(profiles):
            raise ValueError("R12 Director review has drifted from the completed media evidence.")
    return {
        "ok": True,
        "revisionId": R12_REVISION_ID,
        "frameStart": R12_CONTINUOUS_FRAME_START,
        "frameEnd": R12_CONTINUOUS_FRAME_END,
        "frameCount": R12_CONTINUOUS_FRAME_COUNT,
        "durationSeconds": duration,
        "codexAssistedDecision": "revise",
        "humanApprovalStatus": "pending-human-review",
        "artistApproved": False,
        "mediaStatus": media["status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the deterministic Cinematic Visualizer V2 R12 proof contract."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cue-sheet", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--audio-slice", type=Path, required=True)
    parser.add_argument("--r11-root", type=Path, required=True)
    args = parser.parse_args()
    result = verify_r12_proof(
        root=args.root,
        source_cue_path=args.cue_sheet,
        source_audio_path=args.audio,
        slice_audio_path=args.audio_slice,
        r11_root=args.r11_root,
    )
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

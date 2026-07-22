from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    compile_r12_cinematic_plan,
)
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

R12_REVIEW_CRITERIA: tuple[str, ...] = (
    "cinematic-appeal",
    "physical-believability",
    "protagonist-agency",
    "shot-scale-variation",
    "depth-and-parallax",
    "exposure-control",
    "material-richness",
    "motion-smoothness",
    "story-clarity",
    "vertical-mobile-readability",
    "landscape-readability",
    "stimulation-without-clutter",
)

_RECOMMENDATIONS: dict[str, str] = {
    "cinematic-appeal": "Prefer motivated reveals, quiet anticipation, and one legible release over procedural spectacle.",
    "physical-believability": "Review structure thickness, contact shadows, volumetric depth, and persistent mechanical consequence.",
    "protagonist-agency": "Confirm orientation, acceleration, anticipation, compression, reaction, and wake read as deliberate actions.",
    "shot-scale-variation": "Confirm close, wide, follow, track, threshold, and pullback framings are visibly distinct.",
    "depth-and-parallax": "Require close foreground passage, a separated midground vessel, and stable distant landmarks.",
    "exposure-control": "Reject clipped featureless gate regions and preserve vessel silhouette against the membrane.",
    "material-richness": "Look for dark rough structural surfaces with restrained emissive accents and visible variation.",
    "motion-smoothness": "Use the exact-range report to reject jumps, lens discontinuities, overshoot, and raw-audio macro motion.",
    "story-clarity": "The chamber must open, travel must progress, crossing must occur, and the route must remain sealed.",
    "vertical-mobile-readability": "Review the native vertical composition at phone size; do not accept a landscape crop.",
    "landscape-readability": "Review the native landscape composition at phone size with one persistent focal point.",
    "stimulation-without-clutter": "Require meaningful development every two to four seconds without random flashing or particle noise.",
}

_R11_OBSERVATIONS: tuple[str, ...] = (
    "The rendered result reads as a procedural neon visualizer or HUD.",
    "The protagonist remains similarly centred, front-facing, and sized across shots.",
    "Rendered camera angle, distance, scale, and framing do not vary enough.",
    "Departure relies on repetitive wireframe geometry.",
    "The Gate is overexposed and reads as flat white graphics rather than a physical threshold.",
    "The six-excerpt edit does not prove continuous smooth motion.",
    "Landscape phone stills do not prove native vertical readability.",
    "Repeated glowing outlines displace meaningful detail, action, and consequence.",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return payload


def _resolved_artifact(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve(strict=True)
    if candidate == root or root not in candidate.parents or not candidate.is_file():
        raise ValueError("R11 evidence reference escaped its proof directory.")
    return candidate


def collect_r11_evidence_identity(r11_root: Path) -> dict[str, Any]:
    root = r11_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("R11 proof root must be a directory.")
    build_path = _resolved_artifact(root, "build-manifest.json")
    review_path = _resolved_artifact(root, "art-direction-reviews.json")
    build = _read_json(build_path)
    if (
        build.get("preset") != "space-journey-story"
        or build.get("previewOnly") is not True
        or build.get("fullTimelineRenderAuthorized") is not False
    ):
        raise ValueError("R11 evidence does not preserve the V2 preview-only boundary.")

    def referenced(field: str, path_key: str) -> tuple[Path, dict[str, Any]]:
        reference = build.get(field)
        if not isinstance(reference, dict) or not isinstance(reference.get(path_key), str):
            raise ValueError(f"R11 build manifest has no {field} reference.")
        artifact = _resolved_artifact(root, str(reference[path_key]))
        if reference.get("sha256") != sha256_file(artifact):
            raise ValueError(f"R11 {field} hash does not match its preserved artifact.")
        return artifact, reference

    preview_path, _preview_reference = referenced("previewArtifact", "path")
    receipt_path, _receipt_reference = referenced("renderReceiptArtifact", "path")
    scene_reference = build.get("sceneArtifact")
    if not isinstance(scene_reference, dict) or not isinstance(scene_reference.get("file"), str):
        raise ValueError("R11 build manifest has no scene artifact reference.")
    scene_path = _resolved_artifact(root, str(scene_reference["file"]))
    if scene_reference.get("sha256") != sha256_file(scene_path):
        raise ValueError("R11 scene hash does not match its preserved artifact.")
    review_reference = build.get("reviewArtifact")
    if not isinstance(review_reference, dict) or review_reference.get("sha256") != sha256_file(review_path):
        raise ValueError("R11 review hash does not match its preserved artifact.")
    return {
        "buildManifestSha256": sha256_file(build_path),
        "previewManifestSha256": sha256_file(preview_path),
        "renderReceiptSha256": sha256_file(receipt_path),
        "artDirectionReviewsSha256": sha256_file(review_path),
        "sceneSha256": sha256_file(scene_path),
        "priorRecordedDecision": str(review_reference.get("decision", "unknown")),
    }


def build_r11_human_override(evidence_identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r11-human-artistic-override",
        "revisionId": "andromeda-r11",
        "evidenceIdentity": dict(evidence_identity),
        "reviewer": "human",
        "source": "operator-provided-artistic-review",
        "decision": "revise",
        "artistApproved": False,
        "structuralEvidencePreserved": True,
        "mutatesR11Evidence": False,
        "observations": list(_R11_OBSERVATIONS),
    }


def build_r12_review_packet() -> dict[str, Any]:
    recommendations = [
        {
            "criterion": criterion,
            "status": "pending-render-review",
            "recommendation": _RECOMMENDATIONS[criterion],
        }
        for criterion in R12_REVIEW_CRITERIA
    ]
    human_criteria = [
        {
            "criterion": criterion,
            "assessment": "unknown",
            "findings": [],
        }
        for criterion in R12_REVIEW_CRITERIA
    ]
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r12-artistic-review-packet",
        "revisionId": R12_REVISION_ID,
        "previewOnly": True,
        "codexAssisted": {
            "decision": "revise",
            "approvalGranted": False,
            "status": "recommendations-prepared-awaiting-render-review",
            "criteria": recommendations,
        },
        "humanReview": {
            "status": "pending-human-review",
            "decision": None,
            "approved": False,
            "reviewer": None,
            "criteria": human_criteria,
        },
        "artistApproved": False,
        "automaticApprovalAllowed": False,
    }


def _write_json(path: Path, payload: dict[str, Any], *, cinematic_privacy: bool = True) -> None:
    if cinematic_privacy:
        validate_cinematic_privacy(payload)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    serialized = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def generate_r12_proof(
    *,
    source_cue_path: Path,
    source_audio_path: Path,
    slice_audio_path: Path,
    r11_root: Path,
    output: Path,
    seed: int = 84291,
) -> dict[str, Path]:
    cue_path = source_cue_path.resolve(strict=True)
    audio_path = source_audio_path.resolve(strict=True)
    audio_slice_path = slice_audio_path.resolve(strict=True)
    if not cue_path.is_file() or not audio_path.is_file() or not audio_slice_path.is_file():
        raise ValueError("R12 source cue, source audio, and sliced audio inputs must be files.")
    source_audio_sha256 = sha256_file(audio_path)
    slice_audio_sha256 = sha256_file(audio_slice_path)
    if source_audio_sha256 == slice_audio_sha256:
        raise ValueError("R12 sliced audio must have a distinct content identity from the full source.")
    source = TrackPromptVisualCueSheet.model_validate_json(
        cue_path.read_text(encoding="utf-8-sig")
    )
    validate_public_cue_sheet(source)
    derived = derive_r12_cue_slice(source)
    resolved = resolve_visualizer_config(
        SpaceJourneyStoryVisualizerConfigRequest(
            preset="space-journey-story",
            seed=seed,
        )
    )
    if not isinstance(resolved, SpaceJourneyStoryResolvedVisualizerConfig):
        raise RuntimeError("R12 configuration did not resolve to space-journey-story.")
    story, shots = compile_r12_cinematic_plan(derived, resolved)
    validate_plan_pair(story, shots)

    destination = output.resolve()
    destination.mkdir(parents=False, exist_ok=False)
    derived_cue_path = destination / "visual-cues.json"
    story_path = destination / "story-plan.json"
    shot_path = destination / "shot-plan.json"
    slice_contract_path = destination / "cue-slice-contract.json"
    override_path = destination / "r11-human-artistic-override.json"
    review_path = destination / "r12-artistic-review-packet.json"
    build_path = destination / "build-manifest.json"

    _write_json(
        derived_cue_path,
        derived.model_dump(mode="json", by_alias=True),
        cinematic_privacy=False,
    )
    _write_json(story_path, story.model_dump(mode="json", by_alias=True))
    _write_json(shot_path, shots.model_dump(mode="json", by_alias=True))
    slice_contract = r12_cue_slice_contract(source, derived)
    slice_contract.update(
        {
            "sourceCueSha256": sha256_file(cue_path),
            "derivedCueSha256": sha256_file(derived_cue_path),
            "sourceAudioSha256": source_audio_sha256,
            "sourceAudioSizeBytes": audio_path.stat().st_size,
            "sliceAudioArtifactName": "r12-source-window-audio",
            "sliceAudioSha256": slice_audio_sha256,
            "sliceAudioSizeBytes": audio_slice_path.stat().st_size,
        }
    )
    _write_json(slice_contract_path, slice_contract)
    override = build_r11_human_override(collect_r11_evidence_identity(r11_root))
    review_packet = build_r12_review_packet()
    _write_json(override_path, override)
    _write_json(review_path, review_packet)

    continuous_duration = R12_CONTINUOUS_FRAME_COUNT / shots.fps
    slice_audio_start = (
        R12_CONTINUOUS_FRAME_START - derived.timeline.frame_start
    ) / shots.fps
    source_audio_start = R12_SOURCE_START_SECONDS + slice_audio_start
    build = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r12-bounded-proof",
        "revisionId": R12_REVISION_ID,
        "preset": "space-journey-story",
        "previewOnly": True,
        "fullTimelineRenderAuthorized": False,
        "v2CalibrationPerformed": False,
        "cloudProvisioned": False,
        "analysisInputs": {
            "jobId": str(source.source.job_id),
            "analysisSchemaVersion": source.source.analysis_schema_version,
            "analysisVersion": source.source.analysis_version,
            "sourceCueSha256": sha256_file(cue_path),
            "derivedCueSha256": sha256_file(derived_cue_path),
            "sourceAudioSha256": source_audio_sha256,
            "sourceAudioSizeBytes": audio_path.stat().st_size,
        },
        "audioSlice": {
            "artifactName": "r12-source-window-audio",
            "sha256": slice_audio_sha256,
            "sizeBytes": audio_slice_path.stat().st_size,
            "sourceStartSeconds": R12_SOURCE_START_SECONDS,
            "sourceEndSeconds": R12_SOURCE_END_SECONDS,
            "durationSeconds": R12_SLICE_DURATION_SECONDS,
            "ignoredLocalArtifact": True,
            "committed": False,
        },
        "cueSliceArtifact": {
            "file": slice_contract_path.name,
            "sha256": sha256_file(slice_contract_path),
        },
        "storyPlan": {
            "file": story_path.name,
            "sha256": sha256_file(story_path),
            "actCount": len(story.acts),
        },
        "shotPlan": {
            "file": shot_path.name,
            "sha256": sha256_file(shot_path),
            "shotCount": len(shots.shots),
            "inputDigest": shots.input_digest,
        },
        "continuousProof": {
            "frameStart": R12_CONTINUOUS_FRAME_START,
            "frameEnd": R12_CONTINUOUS_FRAME_END,
            "frameCount": R12_CONTINUOUS_FRAME_COUNT,
            "fps": shots.fps,
            "durationSeconds": continuous_duration,
            "sliceAudioStartSeconds": slice_audio_start,
            "sliceAudioEndSeconds": slice_audio_start + continuous_duration,
            "sourceAudioStartSeconds": source_audio_start,
            "sourceAudioEndSeconds": source_audio_start + continuous_duration,
            "boundedStory": [
                "Awakening",
                "Departure",
                "Gate approach",
                "Gate crossing",
                "Gate sealing",
            ],
            "expectedRenderProfiles": [
                {"id": "landscape", "width": 1920, "height": 1080},
                {"id": "vertical", "width": 1080, "height": 1920},
            ],
        },
        "r11HumanOverrideArtifact": {
            "file": override_path.name,
            "sha256": sha256_file(override_path),
            "decision": "revise",
        },
        "r12ReviewPacketArtifact": {
            "file": review_path.name,
            "sha256": sha256_file(review_path),
            "codexAssistedDecision": "revise",
            "humanApprovalStatus": "pending-human-review",
            "artistApproved": False,
        },
        "mediaProof": {
            "status": "pending",
            "requiredArtifacts": {
                "profiles": ["landscape", "vertical"],
                "receiptCount": 2,
                "clipCount": 2,
                "fullResolutionStillCount": 16,
                "phoneStillCount": 16,
                "motionReportCount": 2,
                "exposureReportCount": 2,
            },
        },
    }
    _write_json(build_path, build)
    return {
        "root": destination,
        "cue": derived_cue_path,
        "story": story_path,
        "shots": shot_path,
        "sliceContract": slice_contract_path,
        "r11Override": override_path,
        "reviewPacket": review_path,
        "manifest": build_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the deterministic, preview-only Cinematic Visualizer V2 R12 proof contract."
    )
    parser.add_argument("--cue-sheet", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--audio-slice", type=Path, required=True)
    parser.add_argument("--r11-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=84291)
    args = parser.parse_args()
    result = generate_r12_proof(
        source_cue_path=args.cue_sheet,
        source_audio_path=args.audio,
        slice_audio_path=args.audio_slice,
        r11_root=args.r11_root,
        output=args.output,
        seed=args.seed,
    )
    print(
        json.dumps(
            {"ok": True, **{key: str(path) for key, path in result.items()}},
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

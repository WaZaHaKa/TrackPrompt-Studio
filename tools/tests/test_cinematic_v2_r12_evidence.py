from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.reconcile_cinematic_v2_r12_evidence import (
    R12_REVIEW_FRAMES,
    R12_REVIEW_ROLES,
    _mapping_contract,
    build_vertical_proof_manifest,
    sha256_file,
    write_immutable_manifest,
)


def _write(path: Path, data: bytes) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "file": path.as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sizeBytes": len(data),
    }


def _fixture(root: Path) -> tuple[Path, str]:
    def artifact(relative: str, data: bytes) -> dict[str, object]:
        reference = _write(root / relative, data)
        reference["file"] = relative
        return reference

    clip = artifact("vertical/r12-continuous-preview.mp4", b"r12-vertical-clip")
    story = artifact("story-plan.json", b"story")
    shot = artifact("shot-plan.json", b"shot")
    cue = artifact(
        "cue-slice-contract.json",
        json.dumps({"sourceFrameStart": 6721, "sourceFrameEnd": 7980}).encode(),
    )
    scene = artifact("trackprompt-space-journey-story-r12.blend", b"scene")
    artifact("trackprompt-space-journey-story-r12.manifest.json", b"scene-manifest")
    motion = artifact(
        "vertical/rendered-motion-report.json",
        json.dumps({"technicalPass": True}).encode(),
    )
    exposure = artifact(
        "vertical/exposure-clipping-report.json",
        json.dumps({"technicalPass": True}).encode(),
    )
    review = artifact(
        "r12-director-review.json",
        json.dumps(
            {
                "artistApproved": False,
                "codexAssisted": {"decision": "revise"},
                "humanReview": {"status": "pending-human-review"},
            }
        ).encode(),
    )
    stills: list[dict[str, object]] = []
    receipt_stills: list[dict[str, object]] = []
    for frame, role in zip(R12_REVIEW_FRAMES, R12_REVIEW_ROLES, strict=True):
        still = artifact(f"vertical/frame_{frame:06d}.png", f"still-{frame}".encode())
        stills.append({**still, "frame": frame, "role": role})
        receipt_stills.append(
            {
                "file": f"frame_{frame:06d}.png",
                "frame": frame,
                "role": role,
                "sha256": still["sha256"],
            }
        )
    receipt = artifact(
        "vertical/mcp-render-receipt.json",
        json.dumps(
            {
                "kind": "trackprompt-blender-mcp-continuous-preview-render-receipt",
                "revisionId": "andromeda-r12-continuous-slice",
                "format": {"id": "vertical"},
                "continuousRange": {
                    "startFrame": 127,
                    "endFrame": 655,
                    "outputFrameCount": 529,
                    "orderedSourceFramesSha256": "source-order",
                    "strategy": "continuous-authored-motion-range",
                },
                "renderedFrames": {
                    "representativeFrames": receipt_stills,
                    "orderedPngSha256": "render-order",
                },
                "scene": scene,
            }
        ).encode(),
    )
    build = {
        "kind": "trackprompt-cinematic-v2-r12-bounded-proof",
        "revisionId": "andromeda-r12-continuous-slice",
        "preset": "space-journey-story",
        "previewOnly": True,
        "fullTimelineRenderAuthorized": False,
        "v2CalibrationPerformed": False,
        "storyPlan": story,
        "shotPlan": {**shot, "inputDigest": "digest", "shotCount": 12},
        "cueSliceArtifact": cue,
        "r12DirectorReviewArtifact": review,
        "mediaProof": {
            "status": "complete",
            "profiles": {
                "vertical": {
                    "clip": clip,
                    "receipt": receipt,
                    "motionReport": motion,
                    "exposureReport": exposure,
                    "fullResolutionStills": stills,
                }
            },
        },
    }
    (root / "build-manifest.json").write_text(json.dumps(build), encoding="utf-8")
    ffprobe = root / "ffprobe.exe"
    ffprobe.write_bytes(b"probe")
    return ffprobe, str(clip["sha256"])


def _probe(_: Path, __: Path) -> dict[str, object]:
    return {
        "width": 1080,
        "height": 1920,
        "fps": "30/1",
        "frameCount": 529,
        "durationSeconds": 17.633333,
        "videoCodec": "h264",
        "pixelFormat": "yuv420p",
        "audioCodec": "aac",
        "audioSampleRate": 44100,
        "audioChannels": 2,
    }


def test_mapping_contract_is_exact_and_contiguous() -> None:
    mapping = _mapping_contract()
    assert mapping["mappingCount"] == 529
    assert mapping["first"] == {
        "outputFrame": 1,
        "r12LocalFrame": 127,
        "sourceTimelineFrame": 6847,
    }
    assert mapping["last"] == {
        "outputFrame": 529,
        "r12LocalFrame": 655,
        "sourceTimelineFrame": 7375,
    }
    assert len(str(mapping["orderedMappingSha256"])) == 64


def test_reconciler_hashes_local_media_and_binds_all_vertical_evidence(tmp_path: Path) -> None:
    ffprobe, expected_hash = _fixture(tmp_path)
    manifest = build_vertical_proof_manifest(
        tmp_path,
        ffprobe,
        expected_preview_sha256=expected_hash,
        probe_media=_probe,
    )
    assert manifest["output"]["continuousPreview"]["sha256"] == expected_hash
    assert manifest["output"]["frameCount"] == 529
    assert len(manifest["output"]["representativeStills"]) == 8
    assert manifest["status"] == {
        "structural": "pass",
        "continuousMotionProof": "pass",
        "verticalProof": "pass",
        "codexAssistedArtisticRecommendation": "revise",
        "humanArtistApproval": "pending",
        "calibrationReadiness": "blocked-pending-human-artistic-approval",
        "productionAuthorization": False,
    }


def test_reconciler_rejects_wrong_intended_preview_hash(tmp_path: Path) -> None:
    ffprobe, _ = _fixture(tmp_path)
    with pytest.raises(ValueError, match="calculated local R12 vertical preview hash"):
        build_vertical_proof_manifest(
            tmp_path,
            ffprobe,
            expected_preview_sha256="0" * 64,
            probe_media=_probe,
        )


def test_immutable_manifest_refuses_different_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    first_hash = write_immutable_manifest(path, {"value": 1})
    assert first_hash == sha256_file(path)
    assert write_immutable_manifest(path, {"value": 1}) == first_hash
    with pytest.raises(ValueError, match="immutable"):
        write_immutable_manifest(path, {"value": 2})

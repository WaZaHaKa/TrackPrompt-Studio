from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for source_root in (REPOSITORY_ROOT, REPOSITORY_ROOT / "backend"):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from app.visualizer.schemas import TrackPromptVisualCueSheet  # noqa: E402
from tools import finalize_cinematic_v2_r12_media as r12_media_finalizer  # noqa: E402
from tools.cinematic_v2_slice import derive_r12_cue_slice, r12_cue_slice_contract  # noqa: E402
from tools.generate_cinematic_v2_r12_proof import (  # noqa: E402
    R12_REVIEW_CRITERIA,
    generate_r12_proof,
    sha256_file,
)
from tools.verify_cinematic_v2_r12_proof import verify_r12_proof  # noqa: E402


def _source_cues() -> TrackPromptVisualCueSheet:
    return TrackPromptVisualCueSheet.model_validate(
        {
            "schemaVersion": "1.1.0",
            "source": {
                "analysisSchemaVersion": "1.4.0",
                "analysisVersion": "0.5.0",
                "jobId": "5ec62fcc-8230-4581-9d2c-60f1484b0879",
                "requestedMode": "deep",
                "effectiveMode": "deep",
            },
            "timeline": {
                "durationSeconds": 300.0,
                "fps": 30,
                "frameStart": 1,
                "frameEnd": 9000,
                "framePolicy": "nearest-half-up-clamped",
            },
            "musicalGrid": {
                "bpm": {"value": 120.0, "confidence": "high"},
                "secondsPerBeat": 0.5,
                "meter": {"value": "4/4", "confidence": "high"},
                "downbeatsAvailable": True,
            },
            "beats": [
                {
                    "index": index,
                    "timeSeconds": seconds,
                    "frame": frame,
                    "confidence": "high",
                    "strength": 0.5,
                    "sourcePath": "rhythm.beats",
                }
                for index, (seconds, frame) in enumerate(
                    ((223.0, 6691), (224.0, 6721), (228.8, 6865), (265.9, 7978), (266.0, 7981))
                )
            ],
            "onsets": [],
            "sections": [
                {
                    "id": "section-1",
                    "neutralLabel": "A",
                    "startSeconds": 0.0,
                    "endSeconds": 228.8,
                    "startFrame": 1,
                    "endFrame": 6864,
                    "energy": 0.85,
                    "confidence": "high",
                    "boundaryConfidence": "high",
                    "sourcePath": "structure.sections[0]",
                },
                {
                    "id": "section-2",
                    "neutralLabel": "B",
                    "startSeconds": 228.8,
                    "endSeconds": 235.968,
                    "startFrame": 6865,
                    "endFrame": 7079,
                    "energy": 0.656,
                    "confidence": "high",
                    "boundaryConfidence": "high",
                    "sourcePath": "structure.sections[1]",
                },
                {
                    "id": "section-3",
                    "neutralLabel": "C",
                    "startSeconds": 235.968,
                    "endSeconds": 300.0,
                    "startFrame": 7080,
                    "endFrame": 9000,
                    "energy": 0.839,
                    "confidence": "high",
                    "boundaryConfidence": "high",
                    "sourcePath": "structure.sections[2]",
                },
            ],
            "transitions": [
                {
                    "id": "transition-1",
                    "timeSeconds": 228.8,
                    "frame": 6865,
                    "fromSectionId": "section-1",
                    "toSectionId": "section-2",
                    "energyBefore": 0.85,
                    "energyAfter": 0.656,
                    "energyDelta": -0.194,
                    "direction": "falling",
                    "confidence": "high",
                    "sourcePaths": ["structure.sections[0]", "structure.sections[1]"],
                },
                {
                    "id": "transition-2",
                    "timeSeconds": 235.968,
                    "frame": 7080,
                    "fromSectionId": "section-2",
                    "toSectionId": "section-3",
                    "energyBefore": 0.656,
                    "energyAfter": 0.839,
                    "energyDelta": 0.183,
                    "direction": "rising",
                    "confidence": "high",
                    "sourcePaths": ["structure.sections[1]", "structure.sections[2]"],
                },
            ],
            "curves": {
                "masterEnergy": {
                    "pointFormat": ["frame", "value"],
                    "points": [
                        [1, 0.1],
                        [6601, 0.2],
                        [6801, 0.4],
                        [7001, 0.3],
                        [7801, 0.8],
                        [8201, 0.6],
                        [9000, 0.5],
                    ],
                    "interpolation": "linear",
                    "sourceSampleRateHz": 20,
                    "originalPointCount": 7,
                    "exportedPointCount": 7,
                    "simplification": {
                        "method": "test",
                        "tolerance": 0.0,
                        "maximumError": 0.0,
                        "maximumPointCount": 64,
                    },
                    "normalization": {"normalizationGroup": "master"},
                    "smoothing": {
                        "attackSeconds": 0.08,
                        "releaseSeconds": 0.35,
                        "sourceSampleRateHz": 20,
                        "outputSampleRateHz": 20,
                    },
                }
            },
            "warnings": [],
        }
    )


def _write_r11_fixture(root: Path) -> None:
    preview = root / "preview-signal-to-gate"
    preview.mkdir(parents=True)
    scene = root / "trackprompt-space-journey-story-r11.blend"
    reviews = root / "art-direction-reviews.json"
    preview_manifest = preview / "preview-manifest.json"
    receipt = preview / "mcp-render-receipt.json"
    scene.write_bytes(b"r11-scene")
    reviews.write_text(json.dumps({"reviews": [], "decision": "approve"}), encoding="utf-8")
    preview_manifest.write_text(json.dumps({"revision": "R11"}), encoding="utf-8")
    receipt.write_text(json.dumps({"receipt": "R11"}), encoding="utf-8")
    build = {
        "preset": "space-journey-story",
        "previewOnly": True,
        "fullTimelineRenderAuthorized": False,
        "previewArtifact": {
            "path": "preview-signal-to-gate/preview-manifest.json",
            "sha256": sha256_file(preview_manifest),
        },
        "renderReceiptArtifact": {
            "path": "preview-signal-to-gate/mcp-render-receipt.json",
            "sha256": sha256_file(receipt),
        },
        "reviewArtifact": {
            "sha256": sha256_file(reviews),
            "decision": "approve",
        },
        "sceneArtifact": {
            "file": scene.name,
            "sha256": sha256_file(scene),
        },
    }
    (root / "build-manifest.json").write_text(json.dumps(build), encoding="utf-8")


def _write_complete_media_contract(
    root: Path,
    manifest: dict[str, object],
    *,
    include_director_review: bool = True,
) -> None:
    review_frames = [149, 224, 306, 364, 424, 493, 560, 622]
    roles = [
        "awakening-question",
        "chamber-release",
        "departure-rear-follow",
        "departure-side-track",
        "departure-foreground-occlusion",
        "gate-low-approach",
        "gate-threshold-crossing",
        "gate-sealed-consequence",
    ]

    def reference(relative: str, content: bytes) -> dict[str, object]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"file": relative.replace("\\", "/"), "sha256": sha256_file(path)}

    profiles: dict[str, object] = {}
    dimensions = {
        "landscape": ((1920, 1080), (320, 180)),
        "vertical": ((1080, 1920), (180, 320)),
    }
    for profile_id, ((width, height), (phone_width, phone_height)) in dimensions.items():
        directory = f"preview-r12/{profile_id}"
        full_stills: list[dict[str, object]] = []
        phone_stills: list[dict[str, object]] = []
        for frame, role in zip(review_frames, roles, strict=True):
            full = reference(
                f"{directory}/stills/frame_{frame:06d}.png",
                f"{profile_id}-full-{frame}".encode(),
            )
            phone = reference(
                f"{directory}/phone/frame_{frame:06d}.png",
                f"{profile_id}-phone-{frame}".encode(),
            )
            full_stills.append(
                {**full, "frame": frame, "role": role, "width": width, "height": height}
            )
            phone_stills.append(
                {
                    **phone,
                    "frame": frame,
                    "role": role,
                    "width": phone_width,
                    "height": phone_height,
                }
            )
        profiles[profile_id] = {
            "width": width,
            "height": height,
            "phoneWidth": phone_width,
            "phoneHeight": phone_height,
            "receipt": reference(
                f"{directory}/mcp-render-receipt.json",
                f"{profile_id}-receipt".encode(),
            ),
            "clip": reference(
                f"{directory}/awakening-to-gate-r12.mp4",
                f"{profile_id}-clip".encode(),
            ),
            "fullResolutionStills": full_stills,
            "phoneStills": phone_stills,
            "motionReport": reference(
                f"{directory}/rendered-motion-report.json",
                f"{profile_id}-motion".encode(),
            ),
            "exposureReport": reference(
                f"{directory}/exposure-clipping-report.json",
                f"{profile_id}-exposure".encode(),
            ),
            "technicalMotionPass": True,
            "technicalExposurePass": True,
        }
    pending = manifest["mediaProof"]
    assert isinstance(pending, dict)
    manifest["mediaProof"] = {
        "status": "complete",
        "requiredArtifacts": pending["requiredArtifacts"],
        "profiles": profiles,
    }
    if include_director_review:
        director_review_path = root / "r12-director-review.json"
        director_review_path.write_text(
            json.dumps(r12_media_finalizer.build_r12_director_review(profiles)),
            encoding="utf-8",
        )
        manifest["r12DirectorReviewArtifact"] = {
            "file": director_review_path.name,
            "sha256": sha256_file(director_review_path),
            "codexAssistedDecision": "revise",
            "humanApprovalStatus": "pending-human-review",
            "artistApproved": False,
        }
    (root / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_r12_slice_rebases_sections_events_transitions_and_curve_boundaries() -> None:
    source = _source_cues()
    original = source.model_copy(deep=True)
    sliced = derive_r12_cue_slice(source)

    assert source == original
    assert sliced.timeline.duration_seconds == 42.0
    assert (sliced.timeline.frame_start, sliced.timeline.frame_end) == (1, 1260)
    assert [(section.start_frame, section.end_frame) for section in sliced.sections] == [
        (1, 144),
        (145, 359),
        (360, 1260),
    ]
    assert [transition.frame for transition in sliced.transitions] == [145, 360]
    assert [event.index for event in sliced.beats] == [0, 1, 2]
    assert [event.frame for event in sliced.beats] == [1, 145, 1258]
    points = sliced.curves["masterEnergy"].points
    assert points[0][0] == 1
    assert points[-1][0] == 1260
    assert points[0][1] == pytest.approx(0.32)
    assert points[-1][1] == pytest.approx(0.7105)
    contract = r12_cue_slice_contract(source, sliced)
    assert contract["sourceFrameStart"] == 6721
    assert contract["sourceFrameEnd"] == 7980


def test_r12_generator_and_verifier_bind_plans_override_and_pending_review(tmp_path: Path) -> None:
    source = _source_cues()
    source_path = tmp_path / "source-cues.json"
    source_path.write_text(source.model_dump_json(by_alias=True), encoding="utf-8")
    audio_path = tmp_path / "private-source.wav"
    audio_path.write_bytes(b"bounded-private-audio-identity")
    audio_slice_path = tmp_path / "private-r12-slice.wav"
    audio_slice_path.write_bytes(b"distinct-42-second-slice-identity")
    r11_root = tmp_path / "r11"
    r11_root.mkdir()
    _write_r11_fixture(r11_root)
    proof_root = tmp_path / "r12"

    generated = generate_r12_proof(
        source_cue_path=source_path,
        source_audio_path=audio_path,
        slice_audio_path=audio_slice_path,
        r11_root=r11_root,
        output=proof_root,
    )
    result = verify_r12_proof(
        root=proof_root,
        source_cue_path=source_path,
        source_audio_path=audio_path,
        slice_audio_path=audio_slice_path,
        r11_root=r11_root,
    )

    assert generated["root"] == proof_root.resolve()
    assert result == {
        "ok": True,
        "revisionId": "andromeda-r12-continuous-slice",
        "frameStart": 127,
        "frameEnd": 655,
        "frameCount": 529,
        "durationSeconds": 529 / 30,
        "codexAssistedDecision": "revise",
        "humanApprovalStatus": "pending-human-review",
        "artistApproved": False,
        "mediaStatus": "pending",
    }
    override = json.loads(generated["r11Override"].read_text(encoding="utf-8"))
    review = json.loads(generated["reviewPacket"].read_text(encoding="utf-8"))
    manifest = json.loads(generated["manifest"].read_text(encoding="utf-8"))
    assert override["decision"] == "revise"
    assert override["mutatesR11Evidence"] is False
    assert len(override["observations"]) == 8
    assert [item["criterion"] for item in review["codexAssisted"]["criteria"]] == list(
        R12_REVIEW_CRITERIA
    )
    assert review["humanReview"]["status"] == "pending-human-review"
    assert review["artistApproved"] is False
    assert manifest["mediaProof"]["status"] == "pending"
    assert manifest["audioSlice"] == {
        "artifactName": "r12-source-window-audio",
        "sha256": sha256_file(audio_slice_path),
        "sizeBytes": audio_slice_path.stat().st_size,
        "sourceStartSeconds": 224.0,
        "sourceEndSeconds": 266.0,
        "durationSeconds": 42.0,
        "ignoredLocalArtifact": True,
        "committed": False,
    }
    serialized_public_proof = json.dumps(
        {
            "manifest": manifest,
            "override": override,
            "review": review,
        }
    )
    assert str(tmp_path) not in serialized_public_proof
    assert audio_path.name not in serialized_public_proof
    assert audio_slice_path.name not in serialized_public_proof

    _write_complete_media_contract(proof_root, manifest)
    completed = verify_r12_proof(
        root=proof_root,
        source_cue_path=source_path,
        source_audio_path=audio_path,
        slice_audio_path=audio_slice_path,
        r11_root=r11_root,
    )
    assert completed["mediaStatus"] == "complete"
    completed_manifest = json.loads(generated["manifest"].read_text(encoding="utf-8"))
    director_reference = completed_manifest["r12DirectorReviewArtifact"]
    assert director_reference["codexAssistedDecision"] == "revise"
    assert director_reference["humanApprovalStatus"] == "pending-human-review"
    assert director_reference["artistApproved"] is False
    director_review = json.loads(
        (proof_root / director_reference["file"]).read_text(encoding="utf-8")
    )
    assert director_review["artistApproved"] is False
    assert director_review["humanReview"]["decision"] is None
    assert set(director_review["layoutEvidence"]) == {"landscape", "vertical"}
    assert director_review["phoneSizeReviewContract"]["phoneStillCount"] == 16
    assert [item["stage"] for item in director_review["stageDifferentiation"]] == [
        "Awakening",
        "Departure",
        "First Gate",
    ]
    assert director_review["knownArtisticLimitations"] == list(
        r12_media_finalizer.R12_KNOWN_ARTISTIC_LIMITATIONS
    )


def test_r12_completed_media_requires_and_verifies_director_review(tmp_path: Path) -> None:
    source = _source_cues()
    source_path = tmp_path / "source-cues.json"
    source_path.write_text(source.model_dump_json(by_alias=True), encoding="utf-8")
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"source-audio")
    audio_slice_path = tmp_path / "slice.wav"
    audio_slice_path.write_bytes(b"distinct-slice-audio")
    r11_root = tmp_path / "r11"
    r11_root.mkdir()
    _write_r11_fixture(r11_root)
    proof_root = tmp_path / "r12"
    generated = generate_r12_proof(
        source_cue_path=source_path,
        source_audio_path=audio_path,
        slice_audio_path=audio_slice_path,
        r11_root=r11_root,
        output=proof_root,
    )
    manifest = json.loads(generated["manifest"].read_text(encoding="utf-8"))
    _write_complete_media_contract(
        proof_root,
        manifest,
        include_director_review=False,
    )
    with pytest.raises(ValueError, match="Director review"):
        verify_r12_proof(
            root=proof_root,
            source_cue_path=source_path,
            source_audio_path=audio_path,
            slice_audio_path=audio_slice_path,
            r11_root=r11_root,
        )

    profiles = manifest["mediaProof"]["profiles"]
    assert isinstance(profiles, dict)
    review_path = proof_root / "r12-director-review.json"
    review = r12_media_finalizer.build_r12_director_review(profiles)
    review["artistApproved"] = True
    review_path.write_text(json.dumps(review), encoding="utf-8")
    manifest["r12DirectorReviewArtifact"] = {
        "file": review_path.name,
        "sha256": sha256_file(review_path),
        "codexAssistedDecision": "revise",
        "humanApprovalStatus": "pending-human-review",
        "artistApproved": False,
    }
    generated["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Director review"):
        verify_r12_proof(
            root=proof_root,
            source_cue_path=source_path,
            source_audio_path=audio_path,
            slice_audio_path=audio_slice_path,
            r11_root=r11_root,
        )


def test_r12_media_finalizer_writes_hash_bound_director_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof_root = tmp_path / "r12"
    proof_root.mkdir()
    manifest: dict[str, object] = {
        "revisionId": "andromeda-r12-continuous-slice",
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
    _write_complete_media_contract(
        proof_root,
        manifest,
        include_director_review=False,
    )
    profiles = manifest["mediaProof"]["profiles"]
    assert isinstance(profiles, dict)
    manifest["mediaProof"] = {
        "status": "pending",
        "requiredArtifacts": manifest["mediaProof"]["requiredArtifacts"],
    }
    (proof_root / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ffprobe = tmp_path / "ffprobe.exe"
    ffprobe.write_bytes(b"test executable identity")
    monkeypatch.setattr(
        r12_media_finalizer,
        "_profile_contract",
        lambda _root, profile_id, _ffprobe: profiles[profile_id],
    )

    r12_media_finalizer.finalize(proof_root, ffprobe)

    finalized = json.loads((proof_root / "build-manifest.json").read_text(encoding="utf-8"))
    reference = finalized["r12DirectorReviewArtifact"]
    review_path = proof_root / reference["file"]
    assert reference["sha256"] == sha256_file(review_path)
    assert reference["artistApproved"] is False
    assert json.loads(review_path.read_text(encoding="utf-8")) == (
        r12_media_finalizer.build_r12_director_review(profiles)
    )


def test_r12_verifier_rejects_review_packet_tampering(tmp_path: Path) -> None:
    source = _source_cues()
    source_path = tmp_path / "source-cues.json"
    source_path.write_text(source.model_dump_json(by_alias=True), encoding="utf-8")
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"audio")
    audio_slice_path = tmp_path / "slice.wav"
    audio_slice_path.write_bytes(b"slice-audio")
    r11_root = tmp_path / "r11"
    r11_root.mkdir()
    _write_r11_fixture(r11_root)
    proof_root = tmp_path / "r12"
    generated = generate_r12_proof(
        source_cue_path=source_path,
        source_audio_path=audio_path,
        slice_audio_path=audio_slice_path,
        r11_root=r11_root,
        output=proof_root,
    )
    packet = json.loads(generated["reviewPacket"].read_text(encoding="utf-8"))
    packet["artistApproved"] = True
    generated["reviewPacket"].write_text(json.dumps(packet), encoding="utf-8")

    with pytest.raises(ValueError, match="review packet"):
        verify_r12_proof(
            root=proof_root,
            source_cue_path=source_path,
            source_audio_path=audio_path,
            slice_audio_path=audio_slice_path,
            r11_root=r11_root,
        )

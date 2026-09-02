from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


R131_REVISION_ID = "andromeda-r13.1-selected-refinement"
EXPECTED_PRESERVED_HASHES = {
    "r11": "25b1a67a4bef82d399f39b0cc53e21cd83b7f45477e67e4ad3a4902e92c750c3",
    "r12": "80da1b97ce91f240e6bdb1ef638d6279db78690a5cc861f55751209de978e316",
    "r13": "ba0f13da116d6d13994c75bc58720153aae13c56147bc0373a6f8d1688063658",
}
EXPECTED_REVIEW_IDS = (
    "selected-protagonist-orientation",
    "independent-movement-camera-lag",
    "foreground-parallax",
    "selected-gate-depth",
    "crossing-anticipation",
    "localized-compression",
    "post-crossing-recovery",
    "gate-sealing",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path.name}.")
    return payload


def _reference(root: Path, path: Path) -> dict[str, object]:
    return {
        "file": path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix(),
        "sha256": sha256_file(path.resolve(strict=True)),
        "sizeBytes": path.resolve(strict=True).stat().st_size,
    }


def _verify_reference(root: Path, reference: object, label: str) -> Path:
    if not isinstance(reference, dict):
        raise ValueError(f"Missing {label} reference.")
    relative = reference.get("file")
    expected = reference.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected, str):
        raise ValueError(f"Invalid {label} reference.")
    path = (root / relative).resolve(strict=True)
    try:
        path.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError(f"{label} escapes its evidence root.") from exc
    if sha256_file(path) != expected:
        raise ValueError(f"{label} hash does not match the local artifact.")
    return path


def build_r131_review(
    render_manifest: Mapping[str, Any],
    motion: Mapping[str, Any],
    media: Mapping[str, Any],
) -> dict[str, Any]:
    states = render_manifest.get("reviewStates")
    if not isinstance(states, list):
        raise ValueError("R13.1 review states are missing.")
    identifiers = [item.get("id") for item in states if isinstance(item, Mapping)]
    if identifiers != list(EXPECTED_REVIEW_IDS):
        raise ValueError("R13.1 review-state identity or ordering drifted.")
    if motion.get("technicalPass") is not True:
        raise ValueError("R13.1 motion diagnostics did not pass.")
    media_summary = media.get("summary")
    if not isinstance(media_summary, Mapping) or media_summary.get("technicalPass") is not True:
        raise ValueError("R13.1 media diagnostics did not pass.")
    selection = render_manifest.get("selection")
    if not isinstance(selection, Mapping) or selection.get("status") != "selected-for-refinement":
        raise ValueError("R13.1 provisional selection is missing.")
    if selection.get("artistApproved") is not False or selection.get("humanArtistApproval") != "pending":
        raise ValueError("R13.1 selection cannot be artist-approved by automation.")
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r13.1-artistic-review",
        "revisionId": R131_REVISION_ID,
        "scope": {
            "singleMotionProofRendered": True,
            "futureActsBuilt": False,
            "calibrationPerformed": False,
            "cloudProvisioned": False,
            "productionAuthorized": False,
        },
        "recordedProvisionalSelection": dict(selection),
        "visualInspection": {
            "nativeVerticalReviewed": True,
            "phoneSizeReviewed": True,
            "motionPreviewReviewed": True,
            "reviewStateCount": 8,
            "qualityComparisonReviewed": True,
        },
        "findings": {
            "protagonist": [
                "The integrated purple aperture, asymmetric upper marker, narrow single armor band, and rear wake establish front/back orientation at phone size.",
                "One dark structural shell, one translucent atmosphere layer, a luminous core, and limited accents replace the busier R13-B band hierarchy without returning to a white cage.",
                "The procedural faceting remains visually synthetic and the nearly spherical body can still lose direction when the aperture turns fully away.",
            ],
            "architecture": [
                "Repeated pylons, overhead rails, hinges, longitudinal rails, conduits, recesses, and routed crystals read as one connected machine instead of floating padded rectangles.",
                "The construction kit is still a sparse procedural blockout; several conduits cross the portrait frame more prominently than their mechanical function warrants.",
            ],
            "gate": [
                "Dark monolith thickness, three moving lock rings, one localized membrane, destination depth rings, and four seal locks are visually separable.",
                "Compression preserves the protagonist silhouette and avoids a full-frame cyan wash; the destination remains subtle and the approach frame is still protagonist-dominant.",
            ],
            "motion": [
                "The protagonist follows an independent authored path, anticipates, compresses locally, crosses, recovers, and remains visible while the camera overtakes and the gate begins sealing.",
                "The camera uses one continuous authored move with fixed lens, measurable lag and foreground parallax, no transform jumps, no cuts, and no raw-audio macro-motion.",
                "The motivated look-back arc peaks near the declared two-radian-per-second ceiling and remains the most aggressive motion in the proof.",
            ],
            "quality": [
                "Final media uses 64 Eevee temporal samples versus the eight-sample comparison, with one atmosphere layer, one local membrane, DITHERED transparency, and no compositor denoise.",
                "Phone neighbor-luminance variation decreases after the quality adjustment, all reviewed frames avoid clipped highlights, and no frame breaches the cosmic darkness plus contrast gates.",
            ],
        },
        "codexAssistedRecommendation": {
            "decision": "REVISE",
            "rationale": "R13.1 passes its bounded technical and story-motion gates, but the destination, architectural finish, and late camera arc need human-led refinement before artistic approval.",
            "approvalGranted": False,
        },
        "motionProof": {
            "status": "complete",
            "frameStart": 1,
            "frameEnd": 120,
            "fps": 30,
            "durationSeconds": 4.0,
        },
        "humanReview": {
            "status": "pending",
            "reviewer": None,
            "artistApproved": False,
        },
        "calibrationReadiness": "blocked",
        "productionAuthorization": False,
        "remainingWeaknesses": [
            "The destination beyond the gate is readable as layered depth but remains abstract and under-detailed.",
            "Architecture is mechanically connected but still carries a procedural blockout finish.",
            "The protagonist body is clearer but remains close to a sphere when its aperture is occluded.",
            "The post-crossing look-back arc is smooth but comparatively fast at its peak.",
            "No human artist has approved the provisional design or motion proof.",
        ],
    }


def _write_immutable(path: Path, payload: Mapping[str, Any]) -> str:
    data = json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != data:
            raise ValueError(f"Immutable R13.1 evidence {path.name} already differs.")
        return sha256_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return sha256_file(path)


def finalize_r131_proof(
    proof_root: Path,
    r11_manifest: Path,
    r12_manifest: Path,
    r13_manifest: Path,
) -> dict[str, Any]:
    root = proof_root.resolve(strict=True)
    media_root = root / "media-lock-final"
    scene_path = root / "trackprompt-space-journey-story-r13.1-authoritative.blend"
    render_path = media_root / "r13.1-render-manifest.json"
    motion_path = root / "r13.1-motion-diagnostics-lock.json"
    media_path = root / "r13.1-media-diagnostics.json"
    render = _read_json(render_path)
    motion = _read_json(motion_path)
    media = _read_json(media_path)
    if render.get("revisionId") != R131_REVISION_ID:
        raise ValueError("R13.1 render manifest revision is invalid.")
    if motion.get("revisionId") != R131_REVISION_ID or motion.get("technicalPass") is not True:
        raise ValueError("R13.1 motion report is invalid or failed.")
    if media.get("revisionId") != R131_REVISION_ID:
        raise ValueError("R13.1 media diagnostics revision is invalid.")
    scene_reference = render.get("scene")
    if not isinstance(scene_reference, dict) or scene_reference.get("file") != scene_path.name:
        raise ValueError("R13.1 render manifest is not bound to the authoritative scene.")
    if sha256_file(scene_path) != scene_reference.get("sha256"):
        raise ValueError("R13.1 scene hash does not match the render manifest.")
    preview = _verify_reference(media_root, render.get("motionPreview"), "motion preview")
    quality = render.get("qualityComparison")
    if not isinstance(quality, dict):
        raise ValueError("R13.1 quality comparison is missing.")
    for key in ("before", "after", "beforePhone", "afterPhone"):
        _verify_reference(media_root, quality.get(key), f"quality comparison {key}")
    states = render.get("reviewStates")
    if not isinstance(states, list) or len(states) != 8:
        raise ValueError("R13.1 review-state evidence is incomplete.")
    for state in states:
        if not isinstance(state, dict):
            raise ValueError("R13.1 review-state record is invalid.")
        _verify_reference(media_root, state.get("native"), "native review still")
        _verify_reference(media_root, state.get("phone"), "phone review still")
    preserved_paths = {
        "r11": r11_manifest.resolve(strict=True),
        "r12": r12_manifest.resolve(strict=True),
        "r13": r13_manifest.resolve(strict=True),
    }
    preserved: dict[str, dict[str, object]] = {}
    for label, path in preserved_paths.items():
        digest = sha256_file(path)
        if digest != EXPECTED_PRESERVED_HASHES[label]:
            raise ValueError(f"Preserved {label.upper()} proof hash changed.")
        preserved[label] = {
            "file": path.as_posix(),
            "sha256": digest,
            "unchanged": True,
        }
    review = build_r131_review(render, motion, media)
    review_path = root / "r13.1-artistic-review.json"
    review_hash = _write_immutable(review_path, review)
    protagonist_state = next(
        item for item in states if item.get("id") == "selected-protagonist-orientation"
    )
    gate_state = next(item for item in states if item.get("id") == "selected-gate-depth")
    proof = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r13.1-immutable-proof",
        "revisionId": R131_REVISION_ID,
        "previewOnly": True,
        "recordedProvisionalSelection": render["selection"],
        "preservedEvidence": preserved,
        "scene": _reference(root, scene_path),
        "renderManifest": _reference(root, render_path),
        "motionDiagnostics": _reference(root, motion_path),
        "mediaDiagnostics": _reference(root, media_path),
        "artisticReview": _reference(root, review_path),
        "motionPreview": _reference(root, preview),
        "selectedProtagonistStill": _reference(
            root,
            _verify_reference(
                media_root, protagonist_state.get("native"), "selected protagonist still"
            ),
        ),
        "selectedGateStill": _reference(
            root,
            _verify_reference(media_root, gate_state.get("native"), "selected gate still"),
        ),
        "frameRange": render["frameRange"],
        "renderQuality": render["renderQuality"],
        "status": {
            "structural": "pass",
            "motionDiagnostics": "pass",
            "mediaDiagnostics": "pass",
            "codexAssistedRecommendation": "REVISE",
            "humanArtistApproval": "pending",
            "artistApproved": False,
            "calibrationReadiness": "blocked",
            "productionAuthorization": False,
        },
        "generatedMediaCommitted": False,
    }
    proof_path = root / "r13.1-proof-manifest.json"
    proof_hash = _write_immutable(proof_path, proof)
    return {
        "ok": True,
        "proofManifest": str(proof_path),
        "proofManifestSha256": proof_hash,
        "review": str(review_path),
        "reviewSha256": review_hash,
        "status": proof["status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize immutable bounded R13.1 proof evidence.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--r11-manifest", type=Path, required=True)
    parser.add_argument("--r12-manifest", type=Path, required=True)
    parser.add_argument("--r13-manifest", type=Path, required=True)
    args = parser.parse_args()
    result = finalize_r131_proof(
        args.root,
        args.r11_manifest,
        args.r12_manifest,
        args.r13_manifest,
    )
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping


R13_REVISION_ID = "andromeda-r13-lookdev-lock"
R13_VARIANT_IDS: tuple[str, ...] = (
    "protagonist-a-directional-shell",
    "protagonist-b-ancient-engine",
    "protagonist-c-living-prism",
    "architecture-chamber-module",
    "architecture-gate-monolith",
    "gate-approach-hero",
    "gate-compression-hero",
    "gate-post-crossing-hero",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path.name}.")
    return payload


def _reference(root: Path, path: Path) -> dict[str, object]:
    return {
        "file": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "sizeBytes": path.stat().st_size,
    }


def _verified_reference(root: Path, reference: object, label: str) -> Path:
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


def build_r13_review(
    render_manifest: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    variants = render_manifest.get("variants")
    measurements = diagnostics.get("variants")
    if not isinstance(variants, list) or not isinstance(measurements, list):
        raise ValueError("R13 render and diagnostic variants are missing.")
    render_ids = [item.get("id") for item in variants if isinstance(item, Mapping)]
    diagnostic_ids = [item.get("id") for item in measurements if isinstance(item, Mapping)]
    if render_ids != list(R13_VARIANT_IDS) or diagnostic_ids != list(R13_VARIANT_IDS):
        raise ValueError("R13 variant identity or ordering has drifted.")
    summary = diagnostics.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError("R13 diagnostic summary is missing.")
    if any(int(summary.get(key, -1)) != 0 for key in (
        "ordinaryNearBlackReviewCount",
        "subjectSeparationReviewCount",
        "gateSeparationReviewCount",
    )):
        raise ValueError("R13 final lock still has unresolved diagnostic review flags.")
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r13-lookdev-review",
        "revisionId": R13_REVISION_ID,
        "scope": {
            "fullSequenceRendered": False,
            "futureActsBuilt": False,
            "calibrationPerformed": False,
            "cloudProvisioned": False,
            "productionAuthorized": False,
        },
        "visualInspection": {
            "nativeVerticalReviewed": True,
            "phoneSizeReviewed": True,
            "variantCount": 8,
            "allOrdinaryFramesDeclared": True,
            "intentionalBlackoutFrames": [],
        },
        "findings": {
            "protagonist-a-directional-shell": [
                "The integrated purple front aperture, small leading marker, and rear wake read as one oriented body.",
                "The restrained rings preserve V2 identity, but the silhouette is the least mechanically specific option.",
            ],
            "protagonist-b-ancient-engine": [
                "Integrated armor bands and side engine pods give the strongest ancient-machine hierarchy and front/back read.",
                "The bands remain visually active and should be simplified if the operator selects this direction.",
            ],
            "protagonist-c-living-prism": [
                "The faceted shell and asymmetric crystalline vanes produce the cleanest low-lattice silhouette.",
                "Its aperture-to-body transition is quieter and may need a stronger mechanical recess in a later pass.",
            ],
            "architecture-chamber-module": [
                "Shared bevels, panel seams, connected lintel, buttresses, and crystal nodes establish a coherent construction kit.",
                "The isolated module still reads as a look-development specimen rather than a complete enclosing chamber.",
            ],
            "architecture-gate-monolith": [
                "Thick monoliths, nested structural rings, recesses, buttresses, and readable space beyond replace the R12 transparent-polyhedron noise.",
                "The membrane remains deliberately graphic and needs material refinement after a gate direction is selected.",
            ],
            "gate-action": [
                "Approach preserves wake direction and gate depth; compression visibly deforms the whole vessel; post-crossing exposes the central seal and paired locks.",
                "The compression still is intentionally intense and should be motion-tested only after the look selections are human-confirmed.",
            ],
            "lighting-exposure": [
                "All eight native and phone frames remain below the 60 percent near-black review threshold with no measured clipped highlights.",
                "The neutral studio floor/backdrop is useful for comparison but is not final narrative environment dressing.",
            ],
        },
        "codexAssistedRecommendation": {
            "protagonistDesign": "protagonist-b-ancient-engine",
            "architecturalMaterialLanguage": "weathered-stone-metal-crystal-construction-system",
            "gateConstruction": "thick-monolith-nested-ring-membrane-lock-seal",
            "exposureLightingTreatment": "restrained-teal-fill-cyan-rim-amber-chamber",
            "decision": "ready-for-human-selection-with-revisions-noted",
            "approvalGranted": False,
        },
        "selectedLook": {
            "protagonistDesign": None,
            "architecturalMaterialLanguage": None,
            "gateConstruction": None,
            "exposureLightingTreatment": None,
            "status": "pending-human-operator-selection",
        },
        "humanReview": {
            "status": "pending",
            "reviewer": None,
            "artistApproved": False,
        },
        "motionTest": {
            "status": "blocked-pending-look-selection",
            "rendered": False,
            "requiredDurationSeconds": {"minimum": 3.0, "maximum": 5.0},
        },
        "remainingWeaknesses": [
            "The comparison stage is intentionally neutral and is not a complete cinematic environment.",
            "The recommended protagonist armor bands are still busier than the desired final hierarchy.",
            "The gate membrane remains a stylized translucent disk-and-ring system rather than a final refractive volume.",
            "The chamber module needs surrounding enclosure and moving-part animation after selection.",
            "No 3-5 second motion test exists because the four look selections are still pending human choice.",
        ],
    }


def _write_immutable(path: Path, payload: Mapping[str, Any]) -> str:
    data = json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != data:
            raise ValueError(f"Immutable R13 evidence {path.name} already differs.")
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


def finalize_r13_lookdev(root: Path) -> dict[str, Any]:
    proof_root = root.resolve(strict=True)
    variants_root = proof_root / "variants-lock"
    render_path = variants_root / "r13-lookdev-render-manifest.json"
    diagnostics_path = variants_root / "r13-lookdev-diagnostics.json"
    scene_path = proof_root / "trackprompt-space-journey-story-r13-lookdev-lock.blend"
    render_manifest = _read_json(render_path)
    diagnostics = _read_json(diagnostics_path)
    if (
        render_manifest.get("kind")
        != "trackprompt-cinematic-v2-r13-lookdev-render-manifest"
        or render_manifest.get("revisionId") != R13_REVISION_ID
        or diagnostics.get("kind") != "trackprompt-cinematic-v2-r13-lookdev-diagnostics"
        or diagnostics.get("revisionId") != R13_REVISION_ID
    ):
        raise ValueError("R13 lock artifacts do not match the requested revision.")
    diagnostics_render = diagnostics.get("renderManifest")
    if (
        not isinstance(diagnostics_render, dict)
        or diagnostics_render.get("sha256") != sha256_file(render_path)
    ):
        raise ValueError("R13 diagnostics are not bound to the final render manifest.")
    variants = render_manifest.get("variants")
    if not isinstance(variants, list) or len(variants) != 8:
        raise ValueError("R13 final render manifest does not contain eight variants.")
    for variant in variants:
        if not isinstance(variant, dict):
            raise ValueError("R13 variant record is invalid.")
        _verified_reference(variants_root, variant.get("beauty"), "native beauty")
        _verified_reference(variants_root, variant.get("phone"), "phone beauty")
        _verified_reference(variants_root, variant.get("subjectMask"), "subject mask")
        if isinstance(variant.get("gateMask"), dict):
            _verified_reference(variants_root, variant.get("gateMask"), "gate mask")
        snapshot = variant.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("R13 variant snapshot reference is missing.")
        _verified_reference(proof_root, snapshot, "revision snapshot")
        validation = variant.get("validation")
        if not isinstance(validation, dict) or validation.get("ok") is not True:
            raise ValueError("R13 variant was not scene-validated.")

    review = build_r13_review(render_manifest, diagnostics)
    review_path = proof_root / "r13-lookdev-review.json"
    review_hash = _write_immutable(review_path, review)
    proof = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r13-lookdev-proof",
        "revisionId": R13_REVISION_ID,
        "previewOnly": True,
        "sourceRevision": "andromeda-r12-continuous-slice",
        "scene": _reference(proof_root, scene_path.resolve(strict=True)),
        "renderManifest": _reference(proof_root, render_path),
        "diagnostics": _reference(proof_root, diagnostics_path),
        "review": _reference(proof_root, review_path),
        "variantCount": 8,
        "nativeVertical": {"width": 1080, "height": 1920},
        "phoneReview": {"width": 180, "height": 320, "crop": False},
        "status": {
            "structural": "pass",
            "lookDevelopment": "ready-for-human-selection",
            "selectedLook": "pending-human-operator-selection",
            "humanArtistApproval": "pending",
            "artistApproved": False,
            "motionTest": "blocked-pending-look-selection",
            "calibrationReadiness": "blocked",
            "productionAuthorization": False,
        },
        "generatedMediaCommitted": False,
    }
    proof_path = proof_root / "r13-lookdev-proof-manifest.json"
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
    parser = argparse.ArgumentParser(
        description="Finalize the ignored R13 look-development lock evidence."
    )
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(finalize_r13_lookdev(args.root), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

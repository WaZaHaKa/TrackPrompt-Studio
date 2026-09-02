from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4


PROFILES = {
    "landscape": ((1920, 1080), (320, 180)),
    "vertical": ((1080, 1920), (180, 320)),
}

R12_REVIEW_ROLES: tuple[str, ...] = (
    "awakening-question",
    "chamber-release",
    "departure-rear-follow",
    "departure-side-track",
    "departure-foreground-occlusion",
    "gate-low-approach",
    "gate-threshold-crossing",
    "gate-sealed-consequence",
)

R12_KNOWN_ARTISTIC_LIMITATIONS: tuple[str, ...] = (
    "The environment still uses a procedural, low-poly visual vocabulary.",
    "The green threshold membrane and the space beyond remain simple and visually flat.",
    "The Awakening chamber aperture still reads as graphic and emissive.",
    "The Departure corridor remains dark enough to suppress some structural detail.",
    "The protagonist remains the inherited orb hero rather than a fully redesigned living energy vessel.",
)


def _sha256_file(path: Path) -> str:
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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(
                payload,
                stream,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _review_reference(reference: object, label: str) -> dict[str, object]:
    if not isinstance(reference, dict):
        raise ValueError(f"R12 Director review has no {label} evidence reference.")
    filename = reference.get("file")
    digest = reference.get("sha256")
    if not isinstance(filename, str) or not isinstance(digest, str):
        raise ValueError(f"R12 Director review {label} evidence reference is incomplete.")
    return {"file": filename, "sha256": digest}


def build_r12_director_review(profiles: Mapping[str, object]) -> dict[str, Any]:
    """Build the deterministic proof-local review without claiming human approval."""

    if set(profiles) != set(PROFILES):
        raise ValueError("R12 Director review requires landscape and vertical evidence.")
    layout_evidence: dict[str, object] = {}
    for profile_id, ((width, height), (phone_width, phone_height)) in PROFILES.items():
        profile = profiles[profile_id]
        if not isinstance(profile, dict):
            raise ValueError(f"R12 Director review {profile_id} evidence is invalid.")
        full_stills = profile.get("fullResolutionStills")
        phone_stills = profile.get("phoneStills")
        if not isinstance(full_stills, list) or not isinstance(phone_stills, list):
            raise ValueError(f"R12 Director review {profile_id} still evidence is missing.")
        full_roles = [item.get("role") for item in full_stills if isinstance(item, dict)]
        phone_roles = [item.get("role") for item in phone_stills if isinstance(item, dict)]
        if full_roles != list(R12_REVIEW_ROLES) or phone_roles != list(R12_REVIEW_ROLES):
            raise ValueError(f"R12 Director review {profile_id} stage grammar has drifted.")
        phone_references = [
            _review_reference(item, f"{profile_id} phone still")
            for item in phone_stills
        ]
        layout_evidence[profile_id] = {
            "renderResolution": {"width": width, "height": height},
            "phoneReviewResolution": {
                "width": phone_width,
                "height": phone_height,
            },
            "responsiveComposition": True,
            "representativeRoles": list(R12_REVIEW_ROLES),
            "receipt": _review_reference(profile.get("receipt"), f"{profile_id} receipt"),
            "clip": _review_reference(profile.get("clip"), f"{profile_id} clip"),
            "motionReport": _review_reference(
                profile.get("motionReport"),
                f"{profile_id} motion report",
            ),
            "exposureReport": _review_reference(
                profile.get("exposureReport"),
                f"{profile_id} exposure report",
            ),
            "phoneStills": phone_references,
            "technicalMotionPass": profile.get("technicalMotionPass") is True,
            "technicalExposurePass": profile.get("technicalExposurePass") is True,
        }
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r12-director-review",
        "revisionId": "andromeda-r12-continuous-slice",
        "proofLocal": True,
        "liveDirectorWorkspaceMutated": False,
        "previewOnly": True,
        "continuousRange": {
            "frameStart": 127,
            "frameEnd": 655,
            "frameCount": 529,
            "fps": 30,
            "durationSeconds": 529 / 30,
        },
        "layoutEvidence": layout_evidence,
        "phoneSizeReviewContract": {
            "status": "codex-assisted-review-recorded-human-review-pending",
            "fullResolutionStillCount": 16,
            "phoneStillCount": 16,
            "sameRepresentativeRolesInBothLayouts": True,
            "nativeVerticalRequired": True,
            "landscapeCenterCropAcceptedForVertical": False,
        },
        "stageDifferentiation": [
            {
                "stage": "Awakening",
                "roles": ["awakening-question", "awakening-release"],
                "finding": (
                    "The question and chamber-release beats are separately represented, "
                    "but the aperture remains graphic and emissive."
                ),
            },
            {
                "stage": "Departure",
                "roles": [
                    "departure-rear-follow",
                    "departure-side-track",
                    "departure-occluded",
                ],
                "finding": (
                    "Rear-follow, side-track, and foreground-occluded roles provide distinct "
                    "travel grammar, while the corridor remains dark and procedurally low-poly."
                ),
            },
            {
                "stage": "First Gate",
                "roles": ["gate-approach", "gate-crossing", "gate-seal"],
                "finding": (
                    "Approach, crossing, and sealing are separately represented as action and "
                    "consequence, but the green membrane and space beyond remain simple and flat."
                ),
            },
        ],
        "codexAssisted": {
            "reviewer": "codex-assisted",
            "decision": "revise",
            "approvalGranted": False,
            "findings": [
                "The proof binds one continuous 529-frame range in native landscape and native vertical layouts.",
                "Each layout binds eight matching full-resolution stills and eight phone-size derivatives for the complete review grammar.",
                "Awakening, three Departure camera grammars, Gate approach, crossing, and sealing are represented as distinct stages.",
                "Motion and exposure reports are bound per layout as technical evidence; they do not constitute artistic approval.",
                "Further revision is required because material depth, threshold dimensionality, chamber realism, corridor readability, and protagonist identity remain limited.",
            ],
        },
        "humanReview": {
            "status": "pending-human-review",
            "decision": None,
            "approved": False,
            "reviewer": None,
        },
        "knownArtisticLimitations": list(R12_KNOWN_ARTISTIC_LIMITATIONS),
        "artistApproved": False,
        "automaticApprovalAllowed": False,
    }


def _reference(root: Path, path: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise ValueError("R12 media artifact escaped the proof root.")
    return {
        "file": resolved.relative_to(root).as_posix(),
        "sha256": _sha256_file(resolved),
        "sizeBytes": resolved.stat().st_size,
    }


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"{path.name} is not a valid PNG still.")
    return struct.unpack(">II", header[16:24])


def _probe_clip(ffprobe: Path, clip: Path) -> dict[str, object]:
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,nb_read_frames",
            "-of",
            "json",
            str(clip),
        ],
        shell=False,
        check=False,
        capture_output=True,
        timeout=120,
    )
    if completed.returncode != 0 or len(completed.stdout) > 1_000_000:
        raise RuntimeError("FFprobe could not verify the R12 bounded preview.")
    payload = json.loads(completed.stdout.decode("utf-8"))
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ValueError("FFprobe returned no R12 media streams.")
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not isinstance(video, dict) or not isinstance(audio, dict):
        raise ValueError("R12 preview must contain video and audio streams.")
    return {
        "videoCodec": video.get("codec_name"),
        "audioCodec": audio.get("codec_name"),
        "pixelFormat": video.get("pix_fmt"),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": video.get("avg_frame_rate"),
        "frameCount": int(video.get("nb_read_frames", 0)),
        "durationSeconds": float(payload.get("format", {}).get("duration", 0.0)),
    }


def _profile_contract(root: Path, profile_id: str, ffprobe: Path) -> dict[str, object]:
    (width, height), (phone_width, phone_height) = PROFILES[profile_id]
    directory = root / profile_id
    receipt_path = directory / "mcp-render-receipt.json"
    clip_path = directory / "r12-continuous-preview.mp4"
    motion_path = directory / "rendered-motion-report.json"
    exposure_path = directory / "exposure-clipping-report.json"
    receipt = _read_json(receipt_path)
    motion = _read_json(motion_path)
    exposure = _read_json(exposure_path)
    if (
        receipt.get("kind") != "trackprompt-blender-mcp-continuous-preview-render-receipt"
        or receipt.get("format", {}).get("id") != profile_id
        or (receipt.get("format", {}).get("width"), receipt.get("format", {}).get("height"))
        != (width, height)
        or receipt.get("continuousRange", {}).get("startFrame") != 127
        or receipt.get("continuousRange", {}).get("endFrame") != 655
        or receipt.get("continuousRange", {}).get("outputFrameCount") != 529
    ):
        raise ValueError(f"The {profile_id} MCP receipt does not match the R12 contract.")
    probe = _probe_clip(ffprobe, clip_path)
    if (
        probe["videoCodec"] != "h264"
        or probe["audioCodec"] != "aac"
        or probe["pixelFormat"] != "yuv420p"
        or (probe["width"], probe["height"]) != (width, height)
        or probe["fps"] != "30/1"
        or probe["frameCount"] != 529
        or abs(float(probe["durationSeconds"]) - 529 / 30) > 0.08
    ):
        raise ValueError(f"The {profile_id} R12 clip failed codec, range, or duration verification.")
    if (
        motion.get("layout") != profile_id
        or motion.get("frameStart") != 127
        or motion.get("frameEnd") != 655
        or motion.get("metrics", {}).get("sampleCount") != 529
        or exposure.get("layout") != profile_id
        or exposure.get("clip", {}).get("decodedFrameCount") != 529
    ):
        raise ValueError(f"The {profile_id} technical reports do not cover the exact render.")

    roles = receipt.get("renderedFrames", {}).get("representativeFrames")
    if (
        not isinstance(roles, list)
        or len(roles) != 8
        or [item.get("role") for item in roles if isinstance(item, dict)]
        != list(R12_REVIEW_ROLES)
    ):
        raise ValueError(f"The {profile_id} receipt has no eight-frame review grammar.")
    full_stills: list[dict[str, object]] = []
    phone_stills: list[dict[str, object]] = []
    for role in roles:
        if not isinstance(role, dict) or not isinstance(role.get("frame"), int):
            raise ValueError("R12 receipt representative frame is invalid.")
        frame = int(role["frame"])
        role_name = str(role.get("role", ""))
        full_path = directory / f"frame_{frame:06d}.png"
        phone_path = directory / "phone" / full_path.name
        if _png_dimensions(full_path) != (width, height):
            raise ValueError(f"The {profile_id} full-resolution still dimensions are invalid.")
        if _png_dimensions(phone_path) != (phone_width, phone_height):
            raise ValueError(f"The {profile_id} phone still dimensions are invalid.")
        full_stills.append(
            {
                **_reference(root, full_path),
                "frame": frame,
                "role": role_name,
                "width": width,
                "height": height,
            }
        )
        phone_stills.append(
            {
                **_reference(root, phone_path),
                "frame": frame,
                "role": role_name,
                "width": phone_width,
                "height": phone_height,
            }
        )
    return {
        "width": width,
        "height": height,
        "phoneWidth": phone_width,
        "phoneHeight": phone_height,
        "receipt": _reference(root, receipt_path),
        "clip": {**_reference(root, clip_path), "probe": probe},
        "motionReport": _reference(root, motion_path),
        "exposureReport": _reference(root, exposure_path),
        "fullResolutionStills": full_stills,
        "phoneStills": phone_stills,
        "technicalMotionPass": motion.get("technicalPass") is True,
        "technicalExposurePass": exposure.get("technicalPass") is True,
    }


def finalize(root: Path, ffprobe: Path) -> dict[str, object]:
    proof_root = root.resolve(strict=True)
    probe = ffprobe.resolve(strict=True)
    if not proof_root.is_dir() or not probe.is_file():
        raise ValueError("R12 proof root or FFprobe executable is invalid.")
    manifest_path = proof_root / "build-manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("revisionId") != "andromeda-r12-continuous-slice":
        raise ValueError("The target is not an R12 proof.")
    profiles = {
        profile_id: _profile_contract(proof_root, profile_id, probe)
        for profile_id in PROFILES
    }
    director_review_path = proof_root / "r12-director-review.json"
    _atomic_json(director_review_path, build_r12_director_review(profiles))
    manifest["mediaProof"] = {
        "status": "complete",
        "requiredArtifacts": manifest["mediaProof"]["requiredArtifacts"],
        "profiles": profiles,
    }
    manifest["r12DirectorReviewArtifact"] = {
        **_reference(proof_root, director_review_path),
        "codexAssistedDecision": "revise",
        "humanApprovalStatus": "pending-human-review",
        "artistApproved": False,
    }
    _atomic_json(manifest_path, manifest)
    return {
        "ok": True,
        "manifest": manifest_path.name,
        "profiles": {
            profile_id: {
                "clip": profiles[profile_id]["clip"]["file"],
                "motionPass": profiles[profile_id]["technicalMotionPass"],
                "exposurePass": profiles[profile_id]["technicalExposurePass"],
            }
            for profile_id in PROFILES
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize hash-bound R12 bounded media evidence.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--ffprobe", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(finalize(args.root, args.ffprobe), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

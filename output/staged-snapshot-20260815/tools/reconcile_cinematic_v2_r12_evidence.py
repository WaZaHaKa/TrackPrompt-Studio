from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Mapping


R12_REVISION_ID = "andromeda-r12-continuous-slice"
EXPECTED_VERTICAL_PREVIEW_SHA256 = (
    "c53996521114496f69da011154dc4e2ded00695fd5044fba1bfebfa583ee2efc"
)
R12_LOCAL_FRAME_START = 127
R12_LOCAL_FRAME_END = 655
R12_OUTPUT_FRAME_COUNT = 529
R12_SOURCE_WINDOW_FRAME_START = 6721
R12_SOURCE_WINDOW_FRAME_END = 7980
R12_SOURCE_FRAME_START = 6847
R12_SOURCE_FRAME_END = 7375
R12_REVIEW_FRAMES: tuple[int, ...] = (149, 224, 306, 364, 424, 493, 560, 622)
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path.name}.")
    return data


def _reference(root: Path, path: Path) -> dict[str, object]:
    relative = path.relative_to(root).as_posix()
    return {
        "file": relative,
        "sha256": sha256_file(path),
        "sizeBytes": path.stat().st_size,
    }


def _verified_reference(
    root: Path,
    reference: object,
    label: str,
) -> tuple[Path, dict[str, object]]:
    if not isinstance(reference, dict):
        raise ValueError(f"Missing {label} reference.")
    relative = reference.get("file")
    expected_hash = reference.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected_hash, str):
        raise ValueError(f"Invalid {label} reference.")
    path = (root / relative).resolve(strict=True)
    try:
        path.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError(f"{label} escapes the proof root.") from exc
    actual = _reference(root, path)
    if actual["sha256"] != expected_hash:
        raise ValueError(f"{label} hash does not match the local artifact.")
    return path, actual


def _probe_media(ffprobe: Path, clip: Path) -> dict[str, object]:
    command = [
        str(ffprobe),
        "-v",
        "error",
        "-show_entries",
        (
            "stream=codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,"
            "nb_frames,sample_rate,channels:format=duration"
        ),
        "-of",
        "json",
        str(clip),
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
    )
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("ffprobe output exceeded the proof limit.")
    payload = json.loads(result.stdout)
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ValueError("ffprobe returned no streams.")
    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )
    if not isinstance(video, dict) or not isinstance(audio, dict):
        raise ValueError("R12 vertical proof requires video and audio streams.")
    return {
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": str(video.get("avg_frame_rate", "")),
        "frameCount": int(video.get("nb_frames", 0)),
        "durationSeconds": float(payload.get("format", {}).get("duration", 0.0)),
        "videoCodec": str(video.get("codec_name", "")),
        "pixelFormat": str(video.get("pix_fmt", "")),
        "audioCodec": str(audio.get("codec_name", "")),
        "audioSampleRate": int(audio.get("sample_rate", 0)),
        "audioChannels": int(audio.get("channels", 0)),
    }


def _mapping_contract() -> dict[str, object]:
    mappings = [
        {
            "outputFrame": output_frame,
            "r12LocalFrame": R12_LOCAL_FRAME_START + output_frame - 1,
            "sourceTimelineFrame": R12_SOURCE_FRAME_START + output_frame - 1,
        }
        for output_frame in range(1, R12_OUTPUT_FRAME_COUNT + 1)
    ]
    mapping_digest = hashlib.sha256(
        json.dumps(mappings, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "kind": "affine-inclusive-one-to-one",
        "ordering": "ascending-contiguous-no-omissions",
        "formula": {
            "r12LocalFrame": "outputFrame + 126",
            "sourceTimelineFrame": "outputFrame + 6846",
        },
        "first": mappings[0],
        "last": mappings[-1],
        "mappingCount": len(mappings),
        "orderedMappingSha256": mapping_digest,
    }


def build_vertical_proof_manifest(
    proof_root: Path,
    ffprobe: Path,
    *,
    expected_preview_sha256: str = EXPECTED_VERTICAL_PREVIEW_SHA256,
    probe_media: Callable[[Path, Path], dict[str, object]] = _probe_media,
) -> dict[str, object]:
    root = proof_root.resolve(strict=True)
    build_path = root / "build-manifest.json"
    build = _read_json(build_path)
    if (
        build.get("kind") != "trackprompt-cinematic-v2-r12-bounded-proof"
        or build.get("revisionId") != R12_REVISION_ID
        or build.get("preset") != "space-journey-story"
        or build.get("previewOnly") is not True
        or build.get("fullTimelineRenderAuthorized") is not False
        or build.get("v2CalibrationPerformed") is not False
    ):
        raise ValueError("The local build manifest is not the bounded R12 proof.")

    media = build.get("mediaProof")
    if not isinstance(media, dict) or media.get("status") != "complete":
        raise ValueError("R12 media proof is not complete.")
    profiles = media.get("profiles")
    vertical = profiles.get("vertical") if isinstance(profiles, dict) else None
    if not isinstance(vertical, dict):
        raise ValueError("R12 vertical proof profile is missing.")

    clip_path, clip_reference = _verified_reference(root, vertical.get("clip"), "vertical clip")
    if clip_reference["sha256"] != expected_preview_sha256:
        raise ValueError(
            "The calculated local R12 vertical preview hash does not match the intended media."
        )
    receipt_path, receipt_reference = _verified_reference(
        root,
        vertical.get("receipt"),
        "vertical MCP receipt",
    )
    motion_path, motion_reference = _verified_reference(
        root,
        vertical.get("motionReport"),
        "vertical motion report",
    )
    exposure_path, exposure_reference = _verified_reference(
        root,
        vertical.get("exposureReport"),
        "vertical exposure report",
    )
    review_path, review_reference = _verified_reference(
        root,
        build.get("r12DirectorReviewArtifact"),
        "R12 Director review",
    )
    story_path, story_reference = _verified_reference(
        root,
        build.get("storyPlan"),
        "StoryPlan",
    )
    shot_path, shot_reference = _verified_reference(
        root,
        build.get("shotPlan"),
        "ShotPlan",
    )
    cue_slice_path, cue_slice_reference = _verified_reference(
        root,
        build.get("cueSliceArtifact"),
        "cue-slice contract",
    )

    receipt = _read_json(receipt_path)
    motion = _read_json(motion_path)
    exposure = _read_json(exposure_path)
    review = _read_json(review_path)
    cue_slice = _read_json(cue_slice_path)
    if (
        receipt.get("kind")
        != "trackprompt-blender-mcp-continuous-preview-render-receipt"
        or receipt.get("revisionId") != R12_REVISION_ID
        or receipt.get("format", {}).get("id") != "vertical"
    ):
        raise ValueError("The R12 vertical MCP receipt has drifted.")
    if motion.get("technicalPass") is not True or exposure.get("technicalPass") is not True:
        raise ValueError("R12 vertical motion or exposure evidence does not pass.")
    if (
        review.get("codexAssisted", {}).get("decision") != "revise"
        or review.get("humanReview", {}).get("status") != "pending-human-review"
        or review.get("artistApproved") is not False
    ):
        raise ValueError("R12 artistic or human approval status is not truthful.")
    if (
        cue_slice.get("sourceFrameStart") != R12_SOURCE_WINDOW_FRAME_START
        or cue_slice.get("sourceFrameEnd") != R12_SOURCE_WINDOW_FRAME_END
    ):
        raise ValueError("R12 source analysis window has drifted.")

    continuous = receipt.get("continuousRange")
    if (
        not isinstance(continuous, dict)
        or continuous.get("startFrame") != R12_LOCAL_FRAME_START
        or continuous.get("endFrame") != R12_LOCAL_FRAME_END
        or continuous.get("outputFrameCount") != R12_OUTPUT_FRAME_COUNT
    ):
        raise ValueError("R12 continuous frame ordering has drifted.")
    probe = probe_media(ffprobe.resolve(strict=True), clip_path)
    expected_probe = {
        "width": 1080,
        "height": 1920,
        "fps": "30/1",
        "frameCount": R12_OUTPUT_FRAME_COUNT,
        "videoCodec": "h264",
        "pixelFormat": "yuv420p",
        "audioCodec": "aac",
    }
    for key, value in expected_probe.items():
        if probe.get(key) != value:
            raise ValueError(f"R12 vertical media {key} does not match the proof contract.")
    if abs(float(probe.get("durationSeconds", 0.0)) - R12_OUTPUT_FRAME_COUNT / 30) > 0.001:
        raise ValueError("R12 vertical media duration does not match 529 frames at 30 fps.")

    receipt_stills = receipt.get("renderedFrames", {}).get("representativeFrames")
    profile_stills = vertical.get("fullResolutionStills")
    if not isinstance(receipt_stills, list) or not isinstance(profile_stills, list):
        raise ValueError("R12 representative still evidence is missing.")
    if len(receipt_stills) != 8 or len(profile_stills) != 8:
        raise ValueError("R12 vertical proof requires eight representative stills.")
    stills: list[dict[str, object]] = []
    for index, (expected_frame, expected_role) in enumerate(
        zip(R12_REVIEW_FRAMES, R12_REVIEW_ROLES, strict=True)
    ):
        receipt_still = receipt_stills[index]
        profile_still = profile_stills[index]
        if not isinstance(receipt_still, dict) or not isinstance(profile_still, dict):
            raise ValueError("R12 representative still reference is invalid.")
        if (
            receipt_still.get("frame") != expected_frame
            or receipt_still.get("role") != expected_role
            or profile_still.get("frame") != expected_frame
            or profile_still.get("role") != expected_role
        ):
            raise ValueError("R12 representative still identity or order has drifted.")
        still_path, still_reference = _verified_reference(
            root,
            profile_still,
            f"vertical still {expected_frame}",
        )
        if (
            receipt_still.get("file") != still_path.name
            or receipt_still.get("sha256") != still_reference["sha256"]
        ):
            raise ValueError("R12 receipt and proof manifest disagree on a still hash.")
        stills.append(
            {
                **still_reference,
                "identity": f"{expected_frame}:{expected_role}",
                "r12LocalFrame": expected_frame,
                "sourceTimelineFrame": R12_SOURCE_WINDOW_FRAME_START + expected_frame - 1,
                "outputFrame": expected_frame - R12_LOCAL_FRAME_START + 1,
                "role": expected_role,
            }
        )

    scene = receipt.get("scene")
    scene_path, scene_reference = _verified_reference(root, scene, "R12 Blender scene")
    scene_manifest_path = root / "trackprompt-space-journey-story-r12.manifest.json"
    scene_manifest_reference = _reference(root, scene_manifest_path.resolve(strict=True))

    output_order = receipt.get("renderedFrames")
    if not isinstance(output_order, dict):
        raise ValueError("R12 ordered render evidence is missing.")
    return {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-r12-continuous-vertical-proof",
        "immutableEvidence": True,
        "revision": {
            "id": R12_REVISION_ID,
            "preset": "space-journey-story",
            "previewOnly": True,
            "scene": scene_reference,
            "sceneManifest": scene_manifest_reference,
            "sceneFile": scene_path.name,
        },
        "plans": {
            "storyPlan": story_reference,
            "shotPlan": {
                **shot_reference,
                "schemaVersion": build.get("shotPlan", {}).get("schemaVersion", "1.0.0"),
                "inputDigest": build.get("shotPlan", {}).get("inputDigest"),
                "shotCount": build.get("shotPlan", {}).get("shotCount"),
            },
            "cueSlice": cue_slice_reference,
            "storyPlanFile": story_path.name,
            "shotPlanFile": shot_path.name,
        },
        "sourceTimeline": {
            "analysisWindow": {
                "frameStart": R12_SOURCE_WINDOW_FRAME_START,
                "frameEnd": R12_SOURCE_WINDOW_FRAME_END,
                "startSeconds": 224.0,
                "endSeconds": 266.0,
            },
            "reviewedRange": {
                "sourceFrameStart": R12_SOURCE_FRAME_START,
                "sourceFrameEnd": R12_SOURCE_FRAME_END,
                "sourceStartSeconds": 228.2,
                "sourceEndSeconds": 245.83333333333334,
                "r12LocalFrameStart": R12_LOCAL_FRAME_START,
                "r12LocalFrameEnd": R12_LOCAL_FRAME_END,
            },
            "sourceToPreviewMapping": _mapping_contract(),
        },
        "output": {
            "profile": "vertical",
            **probe,
            "frameOrdering": {
                "orderedSourceFramesSha256": continuous.get("orderedSourceFramesSha256"),
                "orderedRenderedPngSha256": output_order.get("orderedPngSha256"),
                "strategy": continuous.get("strategy"),
            },
            "continuousPreview": clip_reference,
            "representativeStills": stills,
        },
        "reports": {
            "mcpRenderReceipt": receipt_reference,
            "renderedMotion": motion_reference,
            "exposure": exposure_reference,
            "directorReview": review_reference,
        },
        "status": {
            "structural": "pass",
            "continuousMotionProof": "pass",
            "verticalProof": "pass",
            "codexAssistedArtisticRecommendation": "revise",
            "humanArtistApproval": "pending",
            "calibrationReadiness": "blocked-pending-human-artistic-approval",
            "productionAuthorization": False,
        },
        "evidenceRoot": {
            "buildManifest": _reference(root, build_path),
            "r11EvidenceMutated": False,
        },
    }


def write_immutable_manifest(path: Path, manifest: Mapping[str, object]) -> str:
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise ValueError(
                "The immutable R12 vertical proof manifest already exists with different content."
            )
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
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return sha256_file(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile and verify the immutable R12 continuous vertical proof."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="Defaults to r12-continuous-vertical-proof-manifest.json under --root.",
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest = build_vertical_proof_manifest(args.root, args.ffprobe)
    output = args.output or args.root / "r12-continuous-vertical-proof-manifest.json"
    if args.verify_only:
        existing = _read_json(output.resolve(strict=True))
        if existing != manifest:
            raise ValueError("The immutable R12 vertical proof manifest failed verification.")
        digest = sha256_file(output)
    else:
        digest = write_immutable_manifest(output, manifest)
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(output),
                "sha256": digest,
                "previewSha256": manifest["output"]["continuousPreview"]["sha256"],
                "frameCount": manifest["output"]["frameCount"],
                "status": manifest["status"],
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

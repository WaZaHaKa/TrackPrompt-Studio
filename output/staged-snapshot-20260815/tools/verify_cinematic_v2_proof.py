from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
BLENDER_ROOT = REPOSITORY_ROOT / "blender"
for source_root in (BACKEND_ROOT, BLENDER_ROOT):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from app.cinematic.schemas import (  # noqa: E402
    ArtDirectionReviewCollection,
    ShotPlan,
    StoryPlan,
)
from app.cinematic.compiler import compile_cinematic_plan  # noqa: E402
from app.cinematic.planner import load_story_template  # noqa: E402
from app.cinematic.validation import validate_cinematic_privacy, validate_plan_pair  # noqa: E402
from app.visualizer.presets import SpaceJourneyStoryResolvedVisualizerConfig  # noqa: E402
from app.visualizer.schemas import TrackPromptVisualCueSheet  # noqa: E402
from trackprompt_visualizer.preview import build_preview_plan, build_review_edit_spec  # noqa: E402

STILL_DIMENSIONS = (640, 360)
PHONE_DIMENSIONS = (320, 180)
MCP_RENDER_RECEIPT_NAME = "mcp-render-receipt.json"
REVIEWED_ACTS = ("signal", "awakening", "departure", "gates")
ASSESSMENT_FIELDS = (
    "focal_readability",
    "depth",
    "silhouette",
    "color_hierarchy",
    "visual_density",
    "story_clarity",
    "mobile_readability",
)
REQUIRED_SCENE_CHECKS = (
    "frameRange",
    "fps",
    "activeCamera",
    "collections",
    "presetCollections",
    "audioBus",
    "audioBusControls",
    "audioBusFCurves",
    "sceneFCurves",
    "audioStrip",
    "cameraTarget",
    "resolvedConfiguration",
    "outputFile",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"{path.name} is not a structurally valid PNG")
    return struct.unpack(">II", header[16:24])


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
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


def _ffprobe(path: Path, executable: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            str(executable),
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size:stream=codec_name,codec_type,width,height,pix_fmt,"
                "sample_rate,channels,avg_frame_rate,nb_frames"
            ),
            "-of",
            "json",
            str(path),
        ],
        shell=False,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0 or len(result.stdout) > 1_000_000:
        raise RuntimeError("ffprobe did not verify the bounded preview")
    payload = json.loads(result.stdout.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("ffprobe returned an invalid payload")
    return payload


def _frame_rate(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        rate = float(value)
    elif isinstance(value, str):
        numerator, separator, denominator = value.partition("/")
        try:
            rate = float(numerator) / (float(denominator) if separator else 1.0)
        except (ValueError, ZeroDivisionError):
            return None
    else:
        return None
    return rate if rate > 0.0 else None


def _review_summary(
    reviews: ArtDirectionReviewCollection,
    shots: ShotPlan,
) -> dict[str, Any]:
    reviewed_shots = [shot for shot in shots.shots if shot.act_id in REVIEWED_ACTS]
    review_by_shot = {review.shot_id: review for review in reviews.reviews}
    if len(review_by_shot) != len(reviews.reviews):
        raise RuntimeError("Director review contains duplicate shot decisions.")
    if set(review_by_shot) != {shot.id for shot in reviewed_shots}:
        raise RuntimeError("Director review must cover Signal through First Gate exactly once.")
    approved = True
    reviewers: set[str] = set()
    for shot in reviewed_shots:
        review = review_by_shot[shot.id]
        reviewers.add(str(review.revision_metadata.reviewer))
        if review.review_frame not in shot.review_frames:
            raise RuntimeError("Director review frame is not declared by its ShotPlan.")
        if not review.findings:
            raise RuntimeError("Every Director review requires specific findings.")
        values = {
            field: str(getattr(review, field))
            for field in ASSESSMENT_FIELDS
        }
        if review.decision == "approve":
            if any(value not in {"clear", "acceptable"} for value in values.values()):
                raise RuntimeError("An approved review contains a failing or unknown assessment.")
            if values["mobile_readability"] != "clear" or values["story_clarity"] != "clear":
                raise RuntimeError("Approval requires clear mobile readability and story clarity.")
        else:
            approved = False
    return {
        "decision": "approve" if approved else "revise",
        "artisticGatesPassed": approved,
        "artistApproved": approved and reviewers == {"human"},
        "reviewCount": len(reviews.reviews),
        "reviewers": sorted(reviewers),
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path.name} must contain a JSON object.")
    return payload


def _validate_build_input_identity(
    root: Path,
    build: dict[str, Any],
    analysis_job_id: str,
) -> None:
    cue_sha256 = _sha256(root / "visual-cues.json")
    analysis_inputs = build.get("analysisInputs")
    synthetic_inputs = build.get("syntheticInputs")
    if isinstance(analysis_inputs, dict):
        if (
            analysis_inputs.get("jobId") != analysis_job_id
            or analysis_inputs.get("cueSha256") != cue_sha256
        ):
            raise RuntimeError("Build manifest real-analysis identity is stale or missing.")
    elif isinstance(synthetic_inputs, dict):
        if synthetic_inputs.get("cueSha256") != cue_sha256:
            raise RuntimeError("Build manifest synthetic cue identity is stale or missing.")
    else:
        raise RuntimeError("Build manifest has no bounded input identity.")


def _resolved_plan_configuration(
    scene_manifest: dict[str, Any],
) -> SpaceJourneyStoryResolvedVisualizerConfig:
    stored = scene_manifest.get("visualizerConfig")
    warnings = scene_manifest.get("warnings")
    if not isinstance(stored, dict) or not isinstance(warnings, list):
        raise RuntimeError("The Blender scene has no stored plan-compilation configuration.")
    payload = dict(stored)
    # Blender records preset-level warnings beside its public visualizer config.
    # The backend compiler includes those warnings in its canonical input digest,
    # so restore the exact resolved configuration before recompiling the plan.
    payload["warnings"] = warnings
    try:
        resolved = SpaceJourneyStoryResolvedVisualizerConfig.model_validate(payload)
    except ValueError as exc:
        raise RuntimeError("The stored plan-compilation configuration is invalid.") from exc
    if scene_manifest.get("seed") != resolved.seed:
        raise RuntimeError("The Blender scene and stored plan configuration use different seeds.")
    return resolved


def _verify_compiled_plan_identity(
    cues: TrackPromptVisualCueSheet,
    story: StoryPlan,
    shots: ShotPlan,
    scene_manifest: dict[str, Any],
) -> None:
    """Prove both stored plans are the deterministic output of their bound inputs."""
    validate_plan_pair(story, shots)
    resolved = _resolved_plan_configuration(scene_manifest)
    template = load_story_template()
    if story.template_id != template.get("templateId"):
        raise RuntimeError("StoryPlan does not identify the loaded cinematic template.")
    expected_story, expected_shots = compile_cinematic_plan(cues, resolved)
    if (
        story.input_digest != expected_story.input_digest
        or shots.input_digest != expected_shots.input_digest
        or story.model_dump(mode="json", by_alias=True)
        != expected_story.model_dump(mode="json", by_alias=True)
        or shots.model_dump(mode="json", by_alias=True)
        != expected_shots.model_dump(mode="json", by_alias=True)
    ):
        raise RuntimeError(
            "Stored StoryPlan/ShotPlan do not match deterministic compilation from "
            "the bound cues, configuration, seed, and template."
        )


def _hashed_image(path: Path, expected: tuple[int, int]) -> dict[str, Any]:
    width, height = _png_dimensions(path)
    if (width, height) != expected or path.stat().st_size <= 0:
        raise RuntimeError("A representative image failed its bounded image contract.")
    return {
        "file": path.name,
        "width": width,
        "height": height,
        "sizeBytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _canonical_payload_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _validate_mcp_render_receipt(
    *,
    preview: Path,
    scene_path: Path,
    shot_plan_payload: dict[str, Any],
    shots: ShotPlan,
    review_edit: dict[str, Any],
    stills: list[dict[str, Any]],
    clip: Path,
) -> Path:
    """Recompute every durable identity in the canonical Blender MCP receipt."""

    receipt_path = preview / MCP_RENDER_RECEIPT_NAME
    receipt = _read_json(receipt_path)
    validate_cinematic_privacy(receipt)
    expected_representative_frames = [
        {
            key: still[key]
            for key in ("frame", "file", "sha256", "sizeBytes", "role", "actId", "shotId")
            if key in still
        }
        for still in stills
    ]
    expected = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-blender-mcp-preview-render-receipt",
        "preset": "space-journey-story",
        "previewOnly": True,
        "scene": {
            "file": scene_path.name,
            "sha256": _sha256(scene_path),
            "sizeBytes": scene_path.stat().st_size,
        },
        "shotPlan": {
            "schemaVersion": shot_plan_payload.get("schemaVersion"),
            "canonicalSha256": _canonical_payload_sha256(shot_plan_payload),
            "inputDigest": shots.input_digest,
            "seed": shots.seed,
            "shotCount": len(shots.shots),
        },
        "reviewEdit": {
            "strategy": review_edit["strategy"],
            "segments": review_edit["segments"],
            "outputFrameCount": review_edit["outputFrameCount"],
            "durationSeconds": review_edit["durationSeconds"],
            "orderedSourceFramesSha256": _canonical_payload_sha256(review_edit["sourceFrames"]),
        },
        "clip": {
            "file": clip.name,
            "sha256": _sha256(clip),
            "sizeBytes": clip.stat().st_size,
        },
        "encoding": {
            "strategy": "external-ffmpeg-argument-array",
            "videoCodec": "libx264",
            "videoPreset": "fast",
            "constantRateFactor": 23,
            "pixelFormat": "yuv420p",
            "audioCodec": "aac",
            "audioBitrate": "160k",
            "audioEdit": "source-segment-atrim-concat",
            "fastStart": True,
        },
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise RuntimeError(f"MCP render receipt {field} does not match verified evidence.")
    rendered = receipt.get("renderedFrames")
    if (
        not isinstance(rendered, dict)
        or rendered.get("count") != int(review_edit["outputFrameCount"])
        or rendered.get("hashScope") != "ordered-output-index-source-frame-png-sha256-v1"
        or not _is_sha256(rendered.get("orderedPngSha256"))
        or rendered.get("representativeFrames") != expected_representative_frames
    ):
        raise RuntimeError("MCP render receipt rendered-frame evidence is stale or incomplete.")
    return receipt_path


def _validate_stored_manifest(
    root: Path,
    preview: Path,
    expected: dict[str, Any],
    *,
    scene_path: Path | None,
    scene_manifest_path: Path | None,
) -> None:
    preview_manifest = _read_json(preview / "preview-manifest.json")
    for key in (
        "schemaVersion",
        "kind",
        "preset",
        "previewOnly",
        "analysisJobId",
        "boundedStory",
        "reviewEdit",
        "stills",
        "phoneStills",
        "clip",
        "renderReceipt",
        "review",
        "planIdentity",
        "production",
    ):
        if preview_manifest.get(key) != expected.get(key):
            raise RuntimeError(f"Stored preview manifest field {key} does not match verified evidence.")
    build = _read_json(root / "build-manifest.json")
    if (
        build.get("preset") != "space-journey-story"
        or build.get("previewOnly") is not True
        or build.get("fullTimelineRenderAuthorized") is not False
    ):
        raise RuntimeError("Build manifest does not preserve the V2 preview-only authorization boundary.")
    _validate_build_input_identity(root, build, str(expected.get("analysisJobId")))
    references = {
        "storyPlan": root / "story-plan.json",
        "shotPlan": root / "shot-plan.json",
        "reviewArtifact": root / "art-direction-reviews.json",
        "previewArtifact": preview / "preview-manifest.json",
        "renderReceiptArtifact": preview / MCP_RENDER_RECEIPT_NAME,
    }
    scene_reference = build.get("sceneArtifact")
    if not isinstance(scene_reference, dict):
        raise RuntimeError("Build manifest has no final scene identity.")
    scene_file = scene_reference.get("file")
    scene_manifest_file = scene_reference.get("manifestFile")
    if not isinstance(scene_file, str) or not isinstance(scene_manifest_file, str):
        raise RuntimeError("Build manifest scene paths are invalid.")
    stored_scene_path = (root / scene_file).resolve(strict=True)
    stored_scene_manifest_path = (root / scene_manifest_file).resolve(strict=True)
    if (
        stored_scene_path.parent != root
        or stored_scene_manifest_path.parent != root
        or stored_scene_path.suffix.casefold() != ".blend"
        or stored_scene_manifest_path != stored_scene_path.with_suffix(".manifest.json")
        or (scene_path is not None and stored_scene_path != scene_path)
        or (scene_manifest_path is not None and stored_scene_manifest_path != scene_manifest_path)
        or scene_reference.get("previewOnly") is not True
        or scene_reference.get("v2CalibrationRequired") is not True
        or scene_reference.get("manifestSha256") != _sha256(stored_scene_manifest_path)
    ):
        raise RuntimeError("Build manifest final scene identity is stale or unsafe.")
    references["sceneArtifact"] = stored_scene_path
    preview_reference = build.get("previewArtifact")
    if not isinstance(preview_reference, dict) or preview_reference.get("path") != (
        "preview-signal-to-gate/preview-manifest.json"
    ):
        raise RuntimeError("Build manifest preview artifact path is stale or unsafe.")
    receipt_reference = build.get("renderReceiptArtifact")
    if not isinstance(receipt_reference, dict) or receipt_reference.get("path") != (
        f"preview-signal-to-gate/{MCP_RENDER_RECEIPT_NAME}"
    ):
        raise RuntimeError("Build manifest MCP render receipt path is stale or unsafe.")
    for field, path in references.items():
        item = build.get(field)
        if not isinstance(item, dict) or item.get("sha256") != _sha256(path):
            raise RuntimeError(f"Build manifest {field} hash is stale or missing.")


def _resolve_scene_artifacts(
    root: Path,
    requested_scene: Path | None,
) -> tuple[Path, Path]:
    if requested_scene is not None:
        scene_path = requested_scene
        scene_manifest_path = scene_path.with_suffix(".manifest.json")
    else:
        build = _read_json(root / "build-manifest.json")
        reference = build.get("sceneArtifact")
        if not isinstance(reference, dict):
            raise RuntimeError("Verification requires a stored or explicitly supplied scene identity.")
        scene_file = reference.get("file")
        manifest_file = reference.get("manifestFile")
        if not isinstance(scene_file, str) or not isinstance(manifest_file, str):
            raise RuntimeError("The stored scene identity is invalid.")
        scene_path = (root / scene_file).resolve(strict=True)
        scene_manifest_path = (root / manifest_file).resolve(strict=True)
    if (
        scene_path.parent != root
        or scene_path.suffix.casefold() != ".blend"
        or scene_manifest_path.parent != root
        or scene_manifest_path != scene_path.with_suffix(".manifest.json")
        or not scene_manifest_path.is_file()
    ):
        raise RuntimeError("The proof scene and its contract manifest are invalid.")
    return scene_path, scene_manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the bounded Cinematic Visualizer V2 proof.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument(
        "--scene",
        type=Path,
        help="Final bounded V2 Blender revision to bind into the proof manifest.",
    )
    parser.add_argument(
        "--write-manifests",
        action="store_true",
        help="After all proof inputs and reviews pass, atomically emit the final proof manifests.",
    )
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    ffprobe = args.ffprobe.resolve(strict=True)
    preview = root / "preview-signal-to-gate"

    if args.write_manifests and args.scene is None:
        raise RuntimeError("--write-manifests requires --scene for identity binding.")
    requested_scene = args.scene.resolve(strict=True) if args.scene is not None else None
    scene_path, scene_manifest_path = _resolve_scene_artifacts(root, requested_scene)
    scene_manifest = _read_json(scene_manifest_path)
    checks = scene_manifest.get("checks")
    preset_summary = scene_manifest.get("presetSummary")
    environments = preset_summary.get("environments", {}) if isinstance(preset_summary, dict) else {}
    stages = environments.get("stages") if isinstance(environments, dict) else None
    reviewed_stages = [
        stage for stage in stages or []
        if isinstance(stage, dict) and stage.get("environment") in {
            "dead_moon", "signal_ruins", "launch_structure", "gate_corridor"
        }
    ]
    if (
        scene_manifest.get("ok") is not True
        or scene_manifest.get("preset") != "space-journey-story"
        or not isinstance(checks, dict)
        or any(checks.get(name) is not True for name in REQUIRED_SCENE_CHECKS)
        or len(reviewed_stages) != 4
        or len({str(stage.get("dominantShape")) for stage in reviewed_stages}) != 4
        or len({str(stage.get("lightingIdentity")) for stage in reviewed_stages}) != 4
        or any(
            not isinstance(stage.get("layerCounts"), dict)
            or any(
                int(stage["layerCounts"].get(layer, 0)) < 1
                for layer in ("foreground", "midground", "background")
            )
            for stage in reviewed_stages
        )
    ):
        raise RuntimeError("The Blender scene manifest failed the V2 stage-art contract.")

    cues = TrackPromptVisualCueSheet.model_validate_json(
        (root / "visual-cues.json").read_text(encoding="utf-8-sig")
    )
    story = StoryPlan.model_validate_json((root / "story-plan.json").read_text(encoding="utf-8-sig"))
    shots = ShotPlan.model_validate_json((root / "shot-plan.json").read_text(encoding="utf-8-sig"))
    build = _read_json(root / "build-manifest.json")
    _validate_build_input_identity(root, build, str(cues.source.job_id))
    _verify_compiled_plan_identity(cues, story, shots, scene_manifest)
    stages_by_environment = {str(stage.get("environment")): stage for stage in reviewed_stages}
    reviewed_shots = [shot for shot in shots.shots if shot.act_id in REVIEWED_ACTS]
    if set(stages_by_environment) != {str(shot.environment.environment) for shot in reviewed_shots}:
        raise RuntimeError("The Blender scene stages do not match the bound ShotPlan environments.")
    for shot in reviewed_shots:
        stage = stages_by_environment[str(shot.environment.environment)]
        if (
            stage.get("dominantShape") != shot.composition.dominant_shape
            or stage.get("lightingIdentity") != shot.lighting.palette
        ):
            raise RuntimeError("The Blender scene art direction drifted from the bound ShotPlan.")
    reviews = ArtDirectionReviewCollection.model_validate_json(
        (root / "art-direction-reviews.json").read_text(encoding="utf-8-sig")
    )
    review_summary = _review_summary(reviews, shots)
    plan = build_preview_plan(
        cues.model_dump(mode="json", by_alias=True),
        "space-journey-story",
        shots.model_dump(mode="json", by_alias=True),
    )
    roles = plan.get("stillRoles")
    segments = plan.get("reviewSegments")
    if not isinstance(roles, list) or len(roles) != 6 or not isinstance(segments, list) or len(segments) != 6:
        raise RuntimeError("The story proof must declare exactly six still roles and motion excerpts.")
    review_edit = build_review_edit_spec(
        plan,
        timeline_frame_start=shots.frame_start,
        timeline_frame_end=shots.frame_end,
        fps=shots.fps,
    )

    stills: list[dict[str, Any]] = []
    phone_stills: list[dict[str, Any]] = []
    for role in roles:
        frame = int(role["frame"])
        still = _hashed_image(preview / f"frame_{frame:06d}.png", STILL_DIMENSIONS)
        phone = _hashed_image(preview / "phone" / f"frame_{frame:06d}.png", PHONE_DIMENSIONS)
        phone["file"] = f"phone/{phone['file']}"
        context = {
            key: role[key]
            for key in ("role", "actId", "shotId")
            if isinstance(role.get(key), str)
        }
        stills.append({"frame": frame, **context, **still})
        phone_stills.append({"frame": frame, **context, **phone})

    clip = preview / "signal-to-first-gate-preview.mp4"
    probe = _ffprobe(clip, ffprobe)
    streams = probe.get("streams")
    format_info = probe.get("format")
    if not isinstance(streams, list) or not isinstance(format_info, dict):
        raise RuntimeError("The bounded preview has no verified streams.")
    video = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), None)
    duration = float(format_info.get("duration", 0.0))
    frame_rate = _frame_rate(video.get("avg_frame_rate")) if isinstance(video, dict) else None
    try:
        frame_count = int(video.get("nb_frames", 0)) if isinstance(video, dict) else 0
    except (TypeError, ValueError):
        frame_count = 0
    expected_duration = float(review_edit["durationSeconds"])
    duration_tolerance = max(0.05, 1.0 / shots.fps)
    if (
        not isinstance(video, dict)
        or not isinstance(audio, dict)
        or video.get("codec_name") != "h264"
        or video.get("pix_fmt") != "yuv420p"
        or (video.get("width"), video.get("height")) != STILL_DIMENSIONS
        or frame_rate is None
        or abs(frame_rate - shots.fps) > 1e-6
        or frame_count != int(review_edit["outputFrameCount"])
        or audio.get("codec_name") != "aac"
        or not 0.0 < expected_duration <= 10.0
        or abs(duration - expected_duration) > duration_tolerance
    ):
        raise RuntimeError("The bounded preview failed its six-excerpt H.264/AAC media contract.")
    render_receipt_path = _validate_mcp_render_receipt(
        preview=preview,
        scene_path=scene_path,
        shot_plan_payload=_read_json(root / "shot-plan.json"),
        shots=shots,
        review_edit=review_edit,
        stills=stills,
        clip=clip,
    )

    preview_manifest = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-cinematic-v2-preview-manifest",
        "verifiedAt": datetime.now(UTC).isoformat(),
        "preset": "space-journey-story",
        "previewOnly": True,
        "analysisJobId": cues.source.job_id,
        "boundedStory": ["Signal", "Awakening", "Departure", "First Gate"],
        "reviewEdit": {
            "strategy": "six-authored-motion-excerpts",
            "segments": review_edit["segments"],
            "sourceTimelineFrameStart": shots.frame_start,
            "sourceTimelineFrameEnd": shots.frame_end,
        },
        "stills": stills,
        "phoneStills": phone_stills,
        "clip": {
            "file": clip.name,
            "sha256": _sha256(clip),
            "sizeBytes": clip.stat().st_size,
            "durationSeconds": duration,
            "frameCount": frame_count,
            "fps": frame_rate,
            "videoCodec": "h264",
            "audioCodec": "aac",
            "width": STILL_DIMENSIONS[0],
            "height": STILL_DIMENSIONS[1],
            "pixelFormat": "yuv420p",
        },
        "renderReceipt": {
            "file": render_receipt_path.name,
            "sha256": _sha256(render_receipt_path),
        },
        "review": {
            "file": "art-direction-reviews.json",
            "sha256": _sha256(root / "art-direction-reviews.json"),
            **review_summary,
        },
        "planIdentity": {
            "storyPlanSha256": _sha256(root / "story-plan.json"),
            "shotPlanSha256": _sha256(root / "shot-plan.json"),
            "inputDigest": shots.input_digest,
            "seed": shots.seed,
        },
        "production": {
            "fullTimelineRendered": False,
            "authorizationUsed": False,
            "calibratedForV2": False,
            "cloudProvisioned": False,
        },
    }
    if args.write_manifests:
        manifest_path = preview / "preview-manifest.json"
        _atomic_json(manifest_path, preview_manifest)
        build_path = root / "build-manifest.json"
        build = _read_json(build_path)
        if build.get("preset") != "space-journey-story" or build.get("previewOnly") is not True:
            raise RuntimeError("Build manifest does not identify a V2 preview-only proof.")
        build.update(
            {
                "finalizedAt": datetime.now(UTC).isoformat(),
                "storyPlan": {
                    "sha256": _sha256(root / "story-plan.json"),
                    "actCount": len(story.acts),
                },
                "shotPlan": {
                    "sha256": _sha256(root / "shot-plan.json"),
                    "shotCount": len(shots.shots),
                },
                "reviewArtifact": {
                    "sha256": _sha256(root / "art-direction-reviews.json"),
                    "reviewCount": len(reviews.reviews),
                    "decision": review_summary["decision"],
                    "artisticGatesPassed": review_summary["artisticGatesPassed"],
                },
                "previewArtifact": {
                    "path": "preview-signal-to-gate/preview-manifest.json",
                    "sha256": _sha256(manifest_path),
                },
                "renderReceiptArtifact": {
                    "path": f"preview-signal-to-gate/{render_receipt_path.name}",
                    "sha256": _sha256(render_receipt_path),
                },
                "sceneArtifact": {
                    "file": scene_path.name,
                    "sha256": _sha256(scene_path),
                    "manifestFile": scene_manifest_path.name,
                    "manifestSha256": _sha256(scene_manifest_path),
                    "previewOnly": True,
                    "v2CalibrationRequired": True,
                },
            }
        )
        _atomic_json(build_path, build)
    else:
        _validate_stored_manifest(
            root,
            preview,
            preview_manifest,
            scene_path=scene_path,
            scene_manifest_path=scene_manifest_path,
        )
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(preview / "preview-manifest.json"),
                "durationSeconds": duration,
                "decision": review_summary["decision"],
                "artisticGatesPassed": review_summary["artisticGatesPassed"],
                "reviewMutation": False,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

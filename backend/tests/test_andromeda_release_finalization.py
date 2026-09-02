from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import app.cinematic.release_finalization as release_module
from app.cinematic.production_contracts import (
    SOURCE_AUDIO_SHA256,
    SOURCE_CUE_SHA256,
    file_sha256,
    load_and_validate_final_release,
)
from app.cinematic.release_finalization import (
    _RELEASE_OWNED_GIT_PATHS,
    HORIZONTAL_VARIANT_ID,
    VERTICAL_VARIANT_ID,
    FinalReleaseReport,
    HumanVisualQaApproval,
    LiveDashboardProof,
    ProfilePreparationRequest,
    ReleaseFinalizationError,
    ReleaseFinalizationRequest,
    SceneBuildReceipt,
    VerificationReport,
    VersionedRenderProfile,
    finalize_horizontal_release,
    prepare_versioned_profiles,
)

SOURCE_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_TAG = "r14-reviewed-test"
WORKER_ID = "local-rtx3060-eevee-r14"
GPU_MODEL = "NVIDIA GeForce RTX 3060"
FREE_BYTES = 200_000_000_000
PEAK_BYTES = 100_000_000_000
PROJECTED_OUTPUT_BYTES = 50_000_000_000


@dataclass(frozen=True)
class FinalizationFixture:
    repository_root: Path
    request: ReleaseFinalizationRequest
    role_paths: dict[str, Path]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{json.dumps(payload, ensure_ascii=True, indent=2)}\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _artifact(repository_root: Path, role: str, path: Path) -> dict[str, object]:
    return {
        "role": role,
        "path": path.relative_to(repository_root).as_posix(),
        "sha256": file_sha256(path),
    }


def _evidence_file(repository_root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(repository_root).as_posix(),
        "sha256": file_sha256(path),
        "sizeBytes": path.stat().st_size,
    }


def _write_png(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


def _image_evidence(
    repository_root: Path,
    path: Path,
    width: int,
    height: int,
) -> dict[str, object]:
    return {
        **_evidence_file(repository_root, path),
        "format": "PNG",
        "width": width,
        "height": height,
    }


def _write_mp4(path: Path, *, placeholder: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if placeholder:
        path.write_bytes(b"placeholder text posing as media\n")
        return
    path.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        b"\x00\x00\x00\x08free"
    )


def _media_evidence(
    repository_root: Path,
    path: Path,
    *,
    width: int,
    height: int,
    duration_seconds: float,
) -> dict[str, object]:
    return {
        **_evidence_file(repository_root, path),
        "container": "mp4",
        "width": width,
        "height": height,
        "fps": 30,
        "durationSeconds": duration_seconds,
        "videoCodec": "h264",
        "pixelFormat": "yuv420p",
        "audioCodec": "aac",
        "audioSampleRate": 44100,
        "audioChannels": 2,
    }


def _build_receipt(
    *,
    scene_path: Path,
    variant_id: str,
    builder_sha256: str,
) -> dict[str, object]:
    horizontal = variant_id == HORIZONTAL_VARIANT_ID
    return {
        "schemaVersion": "1.0.0",
        "projectId": "trip-to-andromeda-v2",
        "builderId": "andromeda-v2-master-scene-builder-v2",
        "builderSourceSha256": builder_sha256,
        "canonicalSha256": "4" * 64,
        "outputBlend": str(scene_path.resolve()),
        "composition": {
            "camera": (
                "TP_ANDROMEDA_V2_CAMERA_HORIZONTAL"
                if horizontal
                else "TP_ANDROMEDA_V2_CAMERA_VERTICAL"
            ),
            "compositionId": variant_id,
            "cropPolicy": "native-authored-never-crop",
            "height": 1080 if horizontal else 1920,
            "outputVariantId": variant_id,
            "width": 1920 if horizontal else 1080,
        },
        "audio": {
            "attached": True,
            "frameStart": 1,
            "sha256": SOURCE_AUDIO_SHA256,
        },
        "visualCues": {
            "applied": True,
            "controlsMajorCameraOrProtagonistTravel": False,
            "sha256": SOURCE_CUE_SHA256,
            "supplied": True,
        },
        "renderMode": {
            "lockedSettingsSatisfied": True,
            "mode": "master",
            "motionBlur": False,
            "outputMode": "png-master-sequence",
            "renderStarted": False,
            "resolutionPercentage": 100,
            "temporalSamples": 64,
            "volumetricSamples": 32,
        },
        "frameStart": 1,
        "frameEnd": 13029,
        "fps": 30,
        "actCount": 7,
        "shotCount": 35,
        "sceneSpecSha256": "5" * 64,
        "productionAuthorized": False,
        "renderStarted": False,
    }


def _git(repository_root: Path, *args: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), *args],
        check=True,
        capture_output=True,
        text=not binary,
    )
    return completed.stdout


def _copy_tracked_inputs(repository_root: Path) -> dict[str, Path]:
    source_destinations = {
        "output-variants": (
            "production/andromeda-v2/output-variants.json",
            "production/andromeda-v2/output-variants.json",
        ),
        "story-plan": (
            "backend/app/cinematic/templates/trip_to_andromeda_story_v2.json",
            "foundation/trip_to_andromeda_story_v2.json",
        ),
        "shot-plan": (
            "backend/app/cinematic/templates/trip_to_andromeda_shots_v2.json",
            "foundation/trip_to_andromeda_shots_v2.json",
        ),
        "owner-creative-acceptance": (
            "production/andromeda-v2/creative-acceptance.json",
            "production/andromeda-v2/creative-acceptance.json",
        ),
        "final-look-profile": (
            "production/andromeda-v2/final-look-profile.json",
            "production/andromeda-v2/final-look-profile.json",
        ),
        "encoding-profiles": (
            "production/andromeda-v2/encoding-profiles.json",
            "production/andromeda-v2/encoding-profiles.json",
        ),
        "horizontal-base": (
            "render-profiles/trip-to-andromeda/"
            "andromeda-v2-horizontal-1080p-final.json",
            "foundation/andromeda-v2-horizontal-1080p-final.json",
        ),
        "vertical-base": (
            "render-profiles/trip-to-andromeda/"
            "andromeda-v2-vertical-1080x1920-final-optional.json",
            "foundation/andromeda-v2-vertical-1080x1920-final-optional.json",
        ),
        "builder-source": (
            "blender/trackprompt_visualizer/andromeda_story_v2.py",
            "blender/trackprompt_visualizer/andromeda_story_v2.py",
        ),
        "release-hold": (
            "production/andromeda-v2/release-hold.json",
            "production/andromeda-v2/release-hold.json",
        ),
    }
    result: dict[str, Path] = {}
    for role, (source_relative, destination_relative) in source_destinations.items():
        destination = repository_root / destination_relative
        _copy(SOURCE_REPOSITORY_ROOT / source_relative, destination)
        result[role] = destination
    _write_text(
        repository_root / ".gitignore",
        "/evidence/\n/generated/\n/release-bundle*/\n",
    )
    return result


def _initialize_git(repository_root: Path) -> tuple[str, str, int]:
    _git(repository_root, "init", "--quiet")
    _git(repository_root, "config", "user.email", "test@example.invalid")
    _git(repository_root, "config", "user.name", "TrackPrompt Test")
    _git(repository_root, "checkout", "-b", "feat/andromeda-story-v2", "--quiet")
    _git(repository_root, "add", ".")
    _git(repository_root, "commit", "--quiet", "-m", "tracked release inputs")
    head = str(_git(repository_root, "rev-parse", "HEAD")).strip()
    tree = _git(
        repository_root,
        "ls-tree",
        "-r",
        "--full-tree",
        head,
        binary=True,
    )
    assert isinstance(tree, bytes)
    return (
        head,
        hashlib.sha256(tree).hexdigest(),
        sum(1 for line in tree.splitlines() if line),
    )


def _prepare_profiles(
    repository_root: Path,
    tracked: dict[str, Path],
) -> dict[str, Path]:
    builder_sha256 = file_sha256(tracked["builder-source"])
    evidence_root = repository_root / "evidence"
    horizontal_scene = evidence_root / "final-horizontal.blend"
    vertical_scene = evidence_root / "final-vertical.blend"
    horizontal_scene.parent.mkdir(parents=True, exist_ok=True)
    horizontal_scene.write_bytes(b"fresh horizontal scene package\n")
    vertical_scene.write_bytes(b"fresh independently authored vertical scene package\n")
    horizontal_receipt = evidence_root / "final-horizontal.build.json"
    vertical_receipt = evidence_root / "final-vertical.build.json"
    _write_json(
        horizontal_receipt,
        _build_receipt(
            scene_path=horizontal_scene,
            variant_id=HORIZONTAL_VARIANT_ID,
            builder_sha256=builder_sha256,
        ),
    )
    _write_json(
        vertical_receipt,
        _build_receipt(
            scene_path=vertical_scene,
            variant_id=VERTICAL_VARIANT_ID,
            builder_sha256=builder_sha256,
        ),
    )
    request = ProfilePreparationRequest.model_validate(
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-profile-preparation-request",
            "releaseTag": RELEASE_TAG,
            "builderSource": _artifact(
                repository_root,
                "builder-source",
                tracked["builder-source"],
            ),
            "horizontal": {
                "baseProfile": _artifact(
                    repository_root,
                    "horizontal-base-render-profile",
                    tracked["horizontal-base"],
                ),
                "scene": _artifact(
                    repository_root,
                    "horizontal-scene",
                    horizontal_scene,
                ),
                "buildReceipt": _artifact(
                    repository_root,
                    "horizontal-scene-build-receipt",
                    horizontal_receipt,
                ),
            },
            "vertical": {
                "baseProfile": _artifact(
                    repository_root,
                    "vertical-base-render-profile",
                    tracked["vertical-base"],
                ),
                "scene": _artifact(
                    repository_root,
                    "vertical-scene",
                    vertical_scene,
                ),
                "buildReceipt": _artifact(
                    repository_root,
                    "vertical-scene-build-receipt",
                    vertical_receipt,
                ),
            },
        }
    )
    result = prepare_versioned_profiles(
        repository_root,
        request,
        Path("generated/profiles"),
    )
    profiles = result["profiles"]
    assert isinstance(profiles, list)
    profile_paths = {
        str(profile["outputVariantId"]): repository_root / str(profile["path"])
        for profile in profiles
    }
    return {
        "final-scene": horizontal_scene,
        "vertical-master-scene": vertical_scene,
        "horizontal-scene-build-receipt": horizontal_receipt,
        "vertical-scene-build-receipt": vertical_receipt,
        "horizontal-render-profile": profile_paths[HORIZONTAL_VARIANT_ID],
        "vertical-render-profile": profile_paths[VERTICAL_VARIANT_ID],
    }


def _write_transition_evidence(
    repository_root: Path,
    evidence_root: Path,
) -> tuple[dict[str, Path], list[dict[str, object]]]:
    definitions = (
        ("gates-to-rupture", 6750, 6809),
        ("rupture-to-transformation", 8570, 8629),
        ("transformation-to-arrival", 10915, 10974),
    )
    role_paths: dict[str, Path] = {}
    samples: list[dict[str, object]] = []
    for transition_id, frame_start, frame_end in definitions:
        role = f"{transition_id}-media"
        media_path = evidence_root / f"{transition_id}.mp4"
        _write_mp4(media_path)
        media = _media_evidence(
            repository_root,
            media_path,
            width=1920,
            height=1080,
            duration_seconds=2.0,
        )
        qa_path = evidence_root / f"{transition_id}.media-qa.json"
        _write_json(
            qa_path,
            {
                "schemaVersion": "1.0.0",
                "kind": "trackprompt-andromeda-v2-transition-media-qa",
                "projectId": "trip-to-andromeda-v2",
                "mediaSha256": file_sha256(media_path),
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "frameStart": frame_start,
                "frameEnd": frame_end,
                "frameCount": frame_end - frame_start + 1,
                "ffprobePassed": True,
                "frameSequenceIntegrityPassed": True,
                "audioVideoSyncPassed": True,
                "technicalPass": True,
            },
        )
        role_paths[role] = media_path
        samples.append(
            {
                "id": transition_id,
                "frameStart": frame_start,
                "frameEnd": frame_end,
                "media": media,
                "mediaQa": _evidence_file(repository_root, qa_path),
                "continuousSample": True,
                "ffprobePassed": True,
            }
        )
    return role_paths, samples


def _write_calibration_evidence(
    repository_root: Path,
    evidence_root: Path,
    *,
    paths: dict[str, Path],
    hardware_sha256: str,
    implementation_commit_sha: str,
    source_tree_sha256: str,
    recorded_at: datetime,
    tamper_projection: bool,
) -> Path:
    frames = (652, 1950, 3779, 6150, 7688, 8834, 11987, 5000, 7500, 10000)
    acts = (
        "signal",
        "awakening",
        "departure",
        "gates",
        "rupture",
        "transformation",
        "arrival",
        "gates",
        "rupture",
        "transformation",
    )
    effects = (
        ["simple-dark"],
        ["dense-architecture"],
        ["transparency-heavy"],
        ["gate-compression"],
        ["rupture-debris-peak"],
        ["transformation-peak"],
        ["arrival-depth-volume-peak"],
        ["expensive-shot-boundary"],
        ["expensive-shot-boundary"],
        ["expensive-shot-boundary"],
    )
    phases = (
        "not-applicable",
        "not-applicable",
        "not-applicable",
        "not-applicable",
        "not-applicable",
        "not-applicable",
        "not-applicable",
        "start",
        "middle",
        "end",
    )
    durations = (0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9, 1.0)
    samples: list[dict[str, object]] = []
    sample_base = recorded_at - timedelta(minutes=30)
    for index, frame in enumerate(frames):
        image_path = evidence_root / "calibration" / f"frame-{frame:06d}.png"
        log_path = evidence_root / "calibration" / f"frame-{frame:06d}.log"
        _write_png(image_path, 1920, 1080)
        _write_text(log_path, f"frame={frame} render_seconds={durations[index]}\n")
        started_at = sample_base + timedelta(seconds=index * 3)
        completed_at = started_at + timedelta(seconds=durations[index])
        samples.append(
            {
                "frame": frame,
                "actId": acts[index],
                "effectClasses": effects[index],
                "expensiveShotPhase": phases[index],
                "renderSeconds": durations[index],
                "forecastWeight": 0.1,
                "startedAt": started_at.isoformat(),
                "completedAt": completed_at.isoformat(),
                "warmRenderer": True,
                "image": _image_evidence(
                    repository_root,
                    image_path,
                    1920,
                    1080,
                ),
                "rendererLog": _evidence_file(repository_root, log_path),
            }
        )
    render_p50 = 0.65 * 13029
    render_p90 = 0.9 * 13029
    stage_values = (
        ("scene-package-preparation", 20.0, 30.0, "measured-local-run", 1),
        ("cache-bake", 40.0, 60.0, "measured-local-run", 1),
        (
            "image-sequence-render",
            render_p50,
            render_p90,
            "derived-from-frame-measurements",
            len(samples),
        ),
        ("frame-validation", 120.0, 180.0, "measured-throughput", 1),
        ("encoding", 180.0, 240.0, "measured-throughput", 1),
        ("final-qa", 120.0, 180.0, "measured-local-run", 1),
        ("publication", 30.0, 60.0, "measured-local-run", 1),
        ("contingency", 300.0, 600.0, "fixed-operational-reserve", 1),
    )
    stage_forecasts: list[dict[str, object]] = []
    for stage, p50, p90, method, measurement_count in stage_values:
        stage_path = evidence_root / "calibration" / f"stage-{stage}.log"
        _write_text(stage_path, f"{stage} p50={p50} p90={p90}\n")
        stage_forecasts.append(
            {
                "stage": stage,
                "p50Seconds": p50,
                "p90Seconds": p90,
                "method": method,
                "evidence": _evidence_file(repository_root, stage_path),
                "measurementCount": measurement_count,
            }
        )
    weighted = math.fsum(duration * 0.1 for duration in durations) * 13029
    if tamper_projection:
        weighted += 100.0
    calibration_path = evidence_root / "final-resolution-calibration-evidence.json"
    _write_json(
        calibration_path,
        {
            "schemaVersion": "1.0.0",
            "kind": (
                "trackprompt-andromeda-v2-final-resolution-calibration-evidence"
            ),
            "projectId": "trip-to-andromeda-v2",
            "outputVariantId": HORIZONTAL_VARIANT_ID,
            "sceneSha256": file_sha256(paths["final-scene"]),
            "renderProfileSha256": file_sha256(
                paths["horizontal-render-profile"]
            ),
            "builderSourceSha256": file_sha256(paths["builder-source"]),
            "workerRequirementId": WORKER_ID,
            "hardwareReportSha256": hardware_sha256,
            "implementationCommitSha": implementation_commit_sha,
            "sourceTreeSha256": source_tree_sha256,
            "frameStart": 1,
            "frameEnd": 13029,
            "frameCount": 13029,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "rendererWarmupComplete": True,
            "samples": samples,
            "weightedProjectedRenderSeconds": weighted,
            "projectedOutputBytes": PROJECTED_OUTPUT_BYTES,
            "stageForecasts": stage_forecasts,
            "finalResolutionVerified": True,
            "representativeCoverageVerified": True,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )
    return calibration_path


def _write_release_evidence(
    repository_root: Path,
    *,
    paths: dict[str, Path],
    implementation_commit_sha: str,
    source_tree_sha256: str,
    source_tree_entry_count: int,
    recorded_at: datetime,
    placeholder_animatic: bool,
    tamper_calibration: bool,
) -> dict[str, Path]:
    evidence_root = repository_root / "evidence"
    scene_sha256 = file_sha256(paths["final-scene"])
    profile_sha256 = file_sha256(paths["horizontal-render-profile"])
    vertical_scene_sha256 = file_sha256(paths["vertical-master-scene"])
    vertical_profile_sha256 = file_sha256(paths["vertical-render-profile"])
    builder_sha256 = file_sha256(paths["builder-source"])

    throughput_path = evidence_root / "hardware" / "target-volume-throughput.log"
    _write_text(throughput_path, "bytes=104857600 seconds=1.0 bytes_per_second=104857600\n")
    hardware_path = evidence_root / "hardware-and-storage-report.json"
    _write_json(
        hardware_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-hardware-and-storage-report",
            "projectId": "trip-to-andromeda-v2",
            "recordedAt": (recorded_at - timedelta(minutes=10)).isoformat(),
            "sceneSha256": scene_sha256,
            "renderProfileSha256": profile_sha256,
            "workerRequirementId": WORKER_ID,
            "operatingSystem": {
                "family": "Windows",
                "version": "11",
                "build": "test-build",
            },
            "cpu": {"model": "Synthetic 12 Core CPU", "logicalCores": 12},
            "ramBytes": 32 * 1024**3,
            "gpus": [
                {
                    "model": GPU_MODEL,
                    "vramBytes": 12 * 1024**3,
                    "driverVersion": "test-driver",
                }
            ],
            "blender": {
                "version": "5.2.0 LTS",
                "build": "test-build",
                "executableSha256": "6" * 64,
            },
            "ffmpeg": {
                "version": "8.1.1",
                "build": "test-build",
                "executableSha256": "7" * 64,
            },
            "ffprobe": {
                "version": "8.1.1",
                "build": "test-build",
                "executableSha256": "8" * 64,
            },
            "storage": {
                "targetVolume": "C:",
                "freeBytes": FREE_BYTES,
                "projectedPeakDiskBytes": PEAK_BYTES,
                "safetyMultiplier": 1.25,
                "measuredWriteBytesPerSecond": 104_857_600.0,
                "throughputEvidence": _evidence_file(
                    repository_root,
                    throughput_path,
                ),
                "headroomSatisfied": True,
            },
            "acPowerConnected": True,
            "sleepRiskAcknowledged": True,
            "systemConfigurationChangedByTool": False,
            "vramStableDuringBoundedEvidence": True,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )

    calibration_path = _write_calibration_evidence(
        repository_root,
        evidence_root,
        paths=paths,
        hardware_sha256=file_sha256(hardware_path),
        implementation_commit_sha=implementation_commit_sha,
        source_tree_sha256=source_tree_sha256,
        recorded_at=recorded_at,
        tamper_projection=tamper_calibration,
    )

    animatic_path = evidence_root / "full-audio-animatic.mp4"
    _write_mp4(animatic_path, placeholder=placeholder_animatic)
    animatic_media = _media_evidence(
        repository_root,
        animatic_path,
        width=480,
        height=270,
        duration_seconds=13029 / 30,
    )
    animatic_qa_path = evidence_root / "animatic-media-qa-report.json"
    _write_json(
        animatic_qa_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-animatic-media-qa-report",
            "projectId": "trip-to-andromeda-v2",
            "outputVariantId": HORIZONTAL_VARIANT_ID,
            "resolutionClass": "LOW",
            "sceneSha256": scene_sha256,
            "renderProfileSha256": profile_sha256,
            "encodedMedia": animatic_media,
            "sourceAudioSha256": SOURCE_AUDIO_SHA256,
            "frameStart": 1,
            "frameEnd": 13029,
            "videoFrameCount": 13029,
            "audioVideoSyncErrorSeconds": 0.01,
            "ffprobePassed": True,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )
    animatic_receipt_path = evidence_root / "animatic-receipt.json"
    _write_json(
        animatic_receipt_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-animatic-receipt",
            "projectId": "trip-to-andromeda-v2",
            "artifact": _evidence_file(repository_root, animatic_path),
            "audioSourceSha256": SOURCE_AUDIO_SHA256,
            "sceneSha256": scene_sha256,
            "renderProfileSha256": profile_sha256,
            "storyPlanSha256": file_sha256(paths["story-plan"]),
            "shotPlanSha256": file_sha256(paths["shot-plan"]),
            "fullSongClockPreserved": True,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )

    final_scene_receipt_path = evidence_root / "final-scene-receipt.json"
    _write_json(
        final_scene_receipt_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-final-scene-receipt",
            "projectId": "trip-to-andromeda-v2",
            "localArtifact": _evidence_file(
                repository_root,
                paths["final-scene"],
            ),
            "buildReceipt": _evidence_file(
                repository_root,
                paths["horizontal-scene-build-receipt"],
            ),
            "builderSourceSha256": builder_sha256,
            "renderProfileSha256": profile_sha256,
            "frameStart": 1,
            "frameEnd": 13029,
            "fps": 30,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )

    reviewed_frames = [652, 1950, 3779, 6150, 7688, 8834, 11987]
    act_coverage = [
        "signal",
        "awakening",
        "departure",
        "gates",
        "rupture",
        "transformation",
        "arrival",
    ]
    motion_path = evidence_root / "motion-health-report.json"
    _write_json(
        motion_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-motion-health-report",
            "projectId": "trip-to-andromeda-v2",
            "sceneSha256": scene_sha256,
            "renderProfileSha256": profile_sha256,
            "reviewedFrames": reviewed_frames,
            "actCoverage": act_coverage,
            "declaredCutsVerified": True,
            "cameraTransformJumpsPassed": True,
            "protagonistTransformJumpsPassed": True,
            "lensJumpsPassed": True,
            "fcurveOvershootPassed": True,
            "rawAudioMajorMotionLinksVerified": True,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )
    exposure_path = evidence_root / "exposure-mobile-readability-report.json"
    _write_json(
        exposure_path,
        {
            "schemaVersion": "1.0.0",
            "kind": (
                "trackprompt-andromeda-v2-exposure-mobile-readability-report"
            ),
            "projectId": "trip-to-andromeda-v2",
            "sceneSha256": scene_sha256,
            "renderProfileSha256": profile_sha256,
            "reviewedFrames": reviewed_frames,
            "actCoverage": act_coverage,
            "nativeResolutionReviewPassed": True,
            "phoneSizeReviewPassed": True,
            "protagonistBackgroundSeparationPassed": True,
            "nearBlackFailureCount": 0,
            "clippedHighlightFailureCount": 0,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )

    transition_paths, transition_samples = _write_transition_evidence(
        repository_root,
        evidence_root,
    )
    transition_report_path = evidence_root / "final-quality-transition-report.json"
    _write_json(
        transition_report_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-final-quality-transition-report",
            "projectId": "trip-to-andromeda-v2",
            "sceneSha256": scene_sha256,
            "renderProfileSha256": profile_sha256,
            "samples": transition_samples,
            "finalResolution": True,
            "sourceAudioSha256": SOURCE_AUDIO_SHA256,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )

    vertical_media_path = evidence_root / "vertical-bounded-proof.mp4"
    _write_mp4(vertical_media_path)
    vertical_media = _media_evidence(
        repository_root,
        vertical_media_path,
        width=1080,
        height=1920,
        duration_seconds=2.0,
    )
    vertical_qa_path = evidence_root / "vertical-bounded-proof-media-qa.json"
    _write_json(
        vertical_qa_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-vertical-bounded-proof-media-qa",
            "projectId": "trip-to-andromeda-v2",
            "sceneSha256": vertical_scene_sha256,
            "renderProfileSha256": vertical_profile_sha256,
            "media": vertical_media,
            "independentlyAuthored": True,
            "horizontalCropUsed": False,
            "safeZonePassed": True,
            "subjectOccupancyPassed": True,
            "mobileReadabilityPassed": True,
            "ffprobePassed": True,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )
    vertical_proof_path = evidence_root / "vertical-composition-proof.json"
    _write_json(
        vertical_proof_path,
        {
            "schemaVersion": "2.0.0",
            "kind": "trackprompt-authored-vertical-composition-proof",
            "projectId": "trip-to-andromeda-v2",
            "outputVariantId": VERTICAL_VARIANT_ID,
            "enabledForProduction": False,
            "renderProfileSha256": vertical_profile_sha256,
            "masterScene": {
                "path": paths["vertical-master-scene"]
                .relative_to(repository_root)
                .as_posix(),
                "sha256": vertical_scene_sha256,
                "renderStarted": False,
            },
            "boundedProof": {
                "path": vertical_media_path.relative_to(repository_root).as_posix(),
                "sha256": file_sha256(vertical_media_path),
                "mediaQaSha256": file_sha256(vertical_qa_path),
            },
            "independentAuthorship": {
                "compositionProfileId": "andromeda-v2-vertical-master-v1",
                "cameraName": "TP_ANDROMEDA_V2_CAMERA_VERTICAL",
                "cropPolicy": "native-authored-never-crop",
                "horizontalAndVerticalFramingDiffer": True,
                "proofIsNotHorizontalCrop": True,
            },
            "productionEligibility": {
                "separateFinalResolutionCalibrationComplete": False,
                "aggregateDualMatrixSlaCalculated": False,
                "exactOperatorAuthorizationPresent": False,
                "productionStartAllowed": False,
            },
            "technicalPass": True,
            "humanArtisticApproval": False,
            "productionRenderStarted": False,
        },
    )

    dashboard_frame_path = evidence_root / "dashboard-latest-frame.png"
    _write_png(dashboard_frame_path, 1920, 1080)
    dashboard_path = evidence_root / "live-dashboard-proof.json"
    _write_json(
        dashboard_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-live-dashboard-proof",
            "projectId": "trip-to-andromeda-v2",
            "sceneSha256": scene_sha256,
            "renderProfileSha256": profile_sha256,
            "sourceRevisionGitCommitSha": implementation_commit_sha,
            "sourceRevisionSourceTreeSha256": source_tree_sha256,
            "completedFrame": {
                "outputVariantId": HORIZONTAL_VARIANT_ID,
                "frame": 652,
                "completedAt": (recorded_at - timedelta(minutes=8)).isoformat(),
                "publicationStartedAt": (
                    recorded_at
                    - timedelta(minutes=8)
                    + timedelta(seconds=0.1)
                ).isoformat(),
                "publishedAt": (
                    recorded_at
                    - timedelta(minutes=8)
                    + timedelta(seconds=0.75)
                ).isoformat(),
                "publicationLatencySeconds": 0.75,
                "image": _image_evidence(
                    repository_root,
                    dashboard_frame_path,
                    1920,
                    1080,
                ),
            },
            "eta": {
                "updatedAt": (recorded_at - timedelta(minutes=7)).isoformat(),
                "completedFrameCount": 1,
                "renderP50SecondsRemaining": 8400.0,
                "renderP90SecondsRemaining": 11600.0,
                "aggregateP50SecondsRemaining": 9200.0,
                "aggregateP90SecondsRemaining": 12800.0,
                "persistedAfterRestart": True,
            },
            "checks": {
                "latestFrameEndpointPassed": True,
                "etaEndpointPassed": True,
                "persistentEventChannelPassed": True,
                "jobRestorationPassed": True,
                "stopAfterChunkPassed": True,
                "retryFailedChunkPassed": True,
                "selectedVariantStreamPassed": True,
            },
            "fullProductionRenderStarted": False,
            "technicalPass": True,
        },
    )

    deterministic_path = evidence_root / "deterministic-effects-and-disk-report.json"
    _write_json(
        deterministic_path,
        {
            "schemaVersion": "1.0.0",
            "kind": (
                "trackprompt-andromeda-v2-deterministic-effects-and-disk-report"
            ),
            "projectId": "trip-to-andromeda-v2",
            "sceneSha256": scene_sha256,
            "renderProfileSha256": profile_sha256,
            "deterministicEffectsVerified": True,
            "allRequiredBakesComplete": True,
            "unbakedEffectCount": 0,
            "deterministicSeed": 84291,
            "storageFreeBytes": FREE_BYTES,
            "storageProjectedPeakDiskBytes": PEAK_BYTES,
            "storageSafetyMultiplier": 1.25,
            "storageHeadroomSatisfied": True,
            "vramStable": True,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )
    dependency_path = evidence_root / "dependency-health-report.json"
    _write_json(
        dependency_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-dependency-health-report",
            "projectId": "trip-to-andromeda-v2",
            "sceneSha256": scene_sha256,
            "renderProfileSha256": profile_sha256,
            "missingDependencyCount": 0,
            "externalDependencyCount": 0,
            "sourceAudioAttached": True,
            "visualCuesApplied": True,
            "blenderLoadPassed": True,
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )

    worker_path = evidence_root / "worker-requirements.json"
    _write_json(
        worker_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-worker-requirements",
            "projectId": "trip-to-andromeda-v2",
            "requirements": [
                {
                    "id": WORKER_ID,
                    "deviceClass": "gpu",
                    "renderer": "BLENDER_EEVEE",
                    "blenderVersion": "5.2.0 LTS",
                    "minimumVramMib": 8192,
                    "maximumWorkersPerDevice": 1,
                    "chunkSizeFrames": 300,
                    "deterministicSeed": 84291,
                    "requiredCapabilities": [
                        "png-16bit-rgb",
                        "atomic-chunk-publication",
                        "validated-missing-frame-resume",
                        "variant-aware-telemetry",
                    ],
                }
            ],
            "localMatch": {
                "matches": True,
                "detectedGpuModel": GPU_MODEL,
                "detectedVramBytes": 12 * 1024**3,
                "blenderVersion": "5.2.0 LTS",
            },
        },
    )

    source_revision_path = evidence_root / "source-revision-report.json"
    _write_json(
        source_revision_path,
        {
            "schemaVersion": "3.0.0",
            "kind": "trackprompt-source-revision-report",
            "projectId": "trip-to-andromeda-v2",
            "branch": "feat/andromeda-story-v2",
            "startingCommitSha": implementation_commit_sha,
            "implementationCommitSha": implementation_commit_sha,
            "commitList": [implementation_commit_sha],
            "sourceTreeSha256": source_tree_sha256,
            "sourceTreeHashMethod": (
                "sha256 of raw git ls-tree -r --full-tree bytes for "
                "implementationCommitSha"
            ),
            "sourceTreeEntryCount": source_tree_entry_count,
            "releaseOwnedPaths": list(_RELEASE_OWNED_GIT_PATHS),
            "releaseOwnedPathsClean": True,
            "remotePushStatus": "not-configured",
            "remoteTrackingRef": None,
            "remoteCommitSha": None,
            "boundSourceHashes": {
                "builder": builder_sha256,
                "ownerCreativeAcceptance": file_sha256(
                    paths["owner-creative-acceptance"]
                ),
                "encodingProfiles": file_sha256(paths["encoding-profiles"]),
                "lookProfile": file_sha256(paths["final-look-profile"]),
                "storyPlan": file_sha256(paths["story-plan"]),
                "shotPlan": file_sha256(paths["shot-plan"]),
                "outputVariantContract": file_sha256(paths["output-variants"]),
                "horizontalRenderProfile": profile_sha256,
                "verticalRenderProfile": vertical_profile_sha256,
            },
            "productionRenderStarted": False,
        },
    )
    verification_path = evidence_root / "verification-report.json"
    verification_commands = (
        ("backend-pytest", "cd backend && python -m pytest", "backend Python"),
        (
            "backend-ruff",
            "cd backend && python -m ruff check .",
            "backend Python",
        ),
        (
            "backend-mypy",
            "cd backend && python -m mypy app",
            "backend Python",
        ),
        (
            "blender-tooling-tests",
            "python -m pytest blender/tests backend/tests/test_andromeda_story_v2.py",
            "backend Python",
        ),
        (
            "mission-control-generic-fixture-tests",
            "python -m pytest backend/tests/test_mission_control.py",
            "backend Python",
        ),
        (
            "frontend-unit-tests",
            "cd frontend && npm test -- --run",
            "Node.js",
        ),
        ("frontend-lint", "cd frontend && npm run lint", "Node.js"),
        (
            "frontend-typecheck",
            "cd frontend && npm run typecheck",
            "Node.js",
        ),
        ("frontend-build", "cd frontend && npm run build", "Node.js"),
        ("frontend-e2e", "cd frontend && npm run test:e2e", "Node.js"),
        (
            "dependency-import-diagnostics",
            "cd backend && python -m app.diagnostics.imports",
            "backend Python",
        ),
        (
            "powershell-parser-harness-launcher",
            "powershell -File tools/test-andromeda-production-paths.ps1",
            "PowerShell 5.1",
        ),
        ("compose-base-config", "docker compose config", "Docker Compose"),
        (
            "compose-gpu-config",
            "docker compose -f compose.yaml -f compose.full-gpu.yaml config",
            "Docker Compose",
        ),
        (
            "proof-regeneration",
            "python -m pytest backend/tests/test_andromeda_release_finalization.py",
            "backend Python",
        ),
        ("git-diff-check", "git diff --check", "Git"),
    )
    _write_json(
        verification_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-verification-report",
            "projectId": "trip-to-andromeda-v2",
            "implementationCommitSha": implementation_commit_sha,
            "sourceTreeSha256": source_tree_sha256,
            "checks": [
                {
                    "checkId": check_id,
                    "command": command,
                    "status": "passed",
                    "runtime": runtime,
                    "evidence": f"synthetic strict receipt for {check_id}",
                    "skipReason": None,
                }
                for check_id, command, runtime in verification_commands
            ],
            "failureCount": 0,
            "gitDiffCheckPassed": True,
            "knownLimitations": [
                "Synthetic fixture media validates the fail-closed container contract."
            ],
            "technicalPass": True,
            "productionRenderStarted": False,
        },
    )

    human_qa_path = evidence_root / "human-visual-qa-approval.json"
    _write_json(
        human_qa_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-human-visual-qa-approval",
            "approvalId": "andromeda-v2-human-visual-qa-r14",
            "projectId": "trip-to-andromeda-v2",
            "reviewedByRole": "project-owner-operator",
            "reviewedAt": (recorded_at - timedelta(minutes=2)).isoformat(),
            "decision": "approved-for-final-release",
            "referenceRevision": "andromeda-r13.1-selected-refinement",
            "sceneSha256": scene_sha256,
            "horizontalRenderProfileSha256": profile_sha256,
            "fullAudioAnimaticSha256": file_sha256(animatic_path),
            "animaticMediaQaReportSha256": file_sha256(animatic_qa_path),
            "finalSceneReceiptSha256": file_sha256(final_scene_receipt_path),
            "motionHealthReportSha256": file_sha256(motion_path),
            "exposureMobileReadabilityReportSha256": file_sha256(exposure_path),
            "finalQualityTransitionReportSha256": file_sha256(
                transition_report_path
            ),
            "verticalCompositionProofSha256": file_sha256(vertical_proof_path),
            "verticalRenderProfileSha256": vertical_profile_sha256,
            "verticalBoundedProofMediaSha256": file_sha256(vertical_media_path),
            "allSevenActsAtR131ProjectLevel": True,
            "phoneSizeReadabilityApproved": True,
            "finalQualityTransitionsApproved": True,
            "knownBlockingFindings": [],
            "humanArtisticApproval": True,
            "codexHumanArtisticApproval": False,
            "productionRenderStarted": False,
        },
    )

    hold_payload = json.loads(paths["release-hold"].read_text(encoding="utf-8"))
    closure_path = evidence_root / "human-review-closure.json"
    _write_json(
        closure_path,
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-human-review-closure",
            "closureId": "andromeda-v2-human-review-closure-r14",
            "projectId": "trip-to-andromeda-v2",
            "reviewedByRole": "project-owner-operator",
            "reviewedAt": (recorded_at - timedelta(minutes=1)).isoformat(),
            "decision": "held-findings-resolved-for-new-exact-release",
            "releaseHoldId": hold_payload["holdId"],
            "releaseHoldSha256": file_sha256(paths["release-hold"]),
            "heldReleaseIdentitySha256": hold_payload["releaseIdentitySha256"],
            "humanVisualQaApprovalSha256": file_sha256(human_qa_path),
            "correctedSceneSha256": scene_sha256,
            "correctedRenderProfileSha256": profile_sha256,
            "calibrationEvidenceSha256": file_sha256(calibration_path),
            "finalQualityTransitionReportSha256": file_sha256(
                transition_report_path
            ),
            "verticalCompositionProofSha256": file_sha256(vertical_proof_path),
            "remainingBlockingFindings": [],
            "humanReviewPerformed": True,
            "codexHumanApproval": False,
            "operatorStartAuthorized": False,
            "productionRenderStarted": False,
        },
    )

    return {
        "final-resolution-calibration-evidence": calibration_path,
        "hardware-and-storage-report": hardware_path,
        "full-audio-animatic": animatic_path,
        "animatic-media-qa-report": animatic_qa_path,
        "animatic-receipt": animatic_receipt_path,
        "final-scene-receipt": final_scene_receipt_path,
        "motion-health-report": motion_path,
        "exposure-mobile-readability-report": exposure_path,
        "final-quality-transition-report": transition_report_path,
        "vertical-bounded-proof-media": vertical_media_path,
        "vertical-bounded-proof-media-qa": vertical_qa_path,
        "vertical-composition-proof": vertical_proof_path,
        "live-dashboard-proof": dashboard_path,
        "deterministic-effects-and-disk-report": deterministic_path,
        "dependency-health-report": dependency_path,
        "worker-requirements": worker_path,
        "source-revision-report": source_revision_path,
        "verification-report": verification_path,
        "human-visual-qa-approval": human_qa_path,
        "human-review-closure": closure_path,
        **transition_paths,
    }


def _finalization_fixture(
    tmp_path: Path,
    *,
    placeholder_animatic: bool = False,
    tamper_calibration: bool = False,
) -> FinalizationFixture:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    tracked = _copy_tracked_inputs(repository_root)
    implementation_commit, source_tree, tree_count = _initialize_git(repository_root)
    profile_paths = _prepare_profiles(repository_root, tracked)
    paths = {
        **profile_paths,
        "output-variants": tracked["output-variants"],
        "story-plan": tracked["story-plan"],
        "shot-plan": tracked["shot-plan"],
        "owner-creative-acceptance": tracked["owner-creative-acceptance"],
        "final-look-profile": tracked["final-look-profile"],
        "encoding-profiles": tracked["encoding-profiles"],
        "builder-source": tracked["builder-source"],
        "release-hold": tracked["release-hold"],
    }
    recorded_at = datetime.now(UTC)
    evidence = _write_release_evidence(
        repository_root,
        paths=paths,
        implementation_commit_sha=implementation_commit,
        source_tree_sha256=source_tree,
        source_tree_entry_count=tree_count,
        recorded_at=recorded_at,
        placeholder_animatic=placeholder_animatic,
        tamper_calibration=tamper_calibration,
    )
    role_paths = {**paths, **evidence}
    role_paths.pop("release-hold")
    hold_payload = json.loads(tracked["release-hold"].read_text(encoding="utf-8"))
    request = ReleaseFinalizationRequest.model_validate(
        {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-andromeda-v2-release-finalization-request",
            "releaseTag": RELEASE_TAG,
            "recordedAt": recorded_at.isoformat(),
            "branch": "feat/andromeda-story-v2",
            "startingCommitSha": implementation_commit,
            "implementationCommitSha": implementation_commit,
            "commitList": [implementation_commit],
            "sourceTreeSha256": source_tree,
            "sourceTreeEntryCount": tree_count,
            "supersedesReleaseIdentitySha256": hold_payload[
                "releaseIdentitySha256"
            ],
            "horizontalOutputPattern": (
                "final-output/andromeda-v2-r14-horizontal/"
                "frames/frame_######.png"
            ),
            "sourceBindings": [
                {
                    "role": "source-audio",
                    "sha256": SOURCE_AUDIO_SHA256,
                    "sizeBytes": 76_608_080,
                    "privateLocalArtifact": True,
                    "committed": False,
                },
                {
                    "role": "source-cue",
                    "sha256": SOURCE_CUE_SHA256,
                    "sizeBytes": 1_276_886,
                    "privateLocalArtifact": True,
                    "committed": False,
                },
            ],
            "artifacts": [
                _artifact(repository_root, role, path)
                for role, path in sorted(role_paths.items())
            ],
        }
    )
    return FinalizationFixture(repository_root, request, role_paths)


def _request_with_updates(
    request: ReleaseFinalizationRequest,
    **updates: object,
) -> ReleaseFinalizationRequest:
    payload = request.model_dump(mode="json", by_alias=True)
    payload.update(updates)
    return ReleaseFinalizationRequest.model_validate(payload)


def test_scene_build_receipt_requires_builder_source_hash(tmp_path: Path) -> None:
    scene = tmp_path / "scene.blend"
    scene.write_bytes(b"scene")
    payload = _build_receipt(
        scene_path=scene,
        variant_id=HORIZONTAL_VARIANT_ID,
        builder_sha256="a" * 64,
    )
    payload.pop("builderSourceSha256")

    with pytest.raises(ValidationError, match="builderSourceSha256"):
        SceneBuildReceipt.model_validate(payload)


def test_finalize_creates_atomic_hash_bound_operator_gated_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _finalization_fixture(tmp_path)
    process_calls: list[list[str]] = []
    original_run = release_module.subprocess.run

    def observed_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = args[0]
        assert isinstance(command, list)
        process_calls.append(command)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(release_module.subprocess, "run", observed_run)
    result = finalize_horizontal_release(
        fixture.repository_root,
        fixture.request,
        Path("release-bundle"),
    )

    assert result.ok is True
    assert result.enabled_variant_ids == [HORIZONTAL_VARIANT_ID]
    assert result.operator_start_gate == "not-authorized"
    assert result.production_start_allowed is False
    assert result.final_render_started is False
    assert result.external_processes_started is False
    assert process_calls
    assert all(command[0] == "git" for command in process_calls)

    output_root = fixture.repository_root / "release-bundle"
    output_paths = {
        "calibration": output_root / "v2-calibration.json",
        "package_manifest": output_root / "package-manifest-v2.json",
        "technical_authorization": (
            output_root / "technical-authorization-v2.json"
        ),
        "release_report": output_root / "evidence" / "release-report.json",
    }
    for field, path in output_paths.items():
        assert path.is_file()
        assert getattr(result.sha256, field) == file_sha256(path)
    loaded = load_and_validate_final_release(
        output_paths["calibration"],
        output_paths["package_manifest"],
        output_paths["technical_authorization"],
        repository_root=fixture.repository_root,
    )
    assert loaded.calibration.variant_calibrations[0].sample_frames == [
        652,
        1950,
        3779,
        6150,
        7688,
        8834,
        11987,
        5000,
        7500,
        10000,
    ]
    assert loaded.calibration.variant_calibrations[0].p50_seconds_per_frame == 0.65
    assert loaded.calibration.variant_calibrations[0].p90_seconds_per_frame == 0.9
    report = FinalReleaseReport.model_validate_json(
        output_paths["release_report"].read_text(encoding="utf-8")
    )
    assert report.git.remote_push_status == "not-configured"
    assert report.git.release_owned_paths_clean is True
    assert report.story.act_count == 7
    assert report.story.shot_count == 35
    assert report.full_animatic.encoded_media.width == 480
    assert report.full_animatic.encoded_media.height == 270
    assert report.full_animatic.resolution_class == "LOW"
    assert report.full_animatic.video_frame_count == 13029
    assert len(report.calibration.representative_measurements) == 10
    variant_forecast = report.enabled_variant_forecasts[0]
    assert variant_forecast.output_variant_id == HORIZONTAL_VARIANT_ID
    assert variant_forecast.render.p50_seconds == 0.65 * 13029
    assert variant_forecast.render.p90_seconds == 0.9 * 13029
    assert variant_forecast.encoding.p50_seconds == 180.0
    assert variant_forecast.encoding.p90_seconds == 240.0
    assert variant_forecast.qa.p50_seconds == 120.0
    assert variant_forecast.qa.p90_seconds == 180.0
    assert variant_forecast.total.p50_seconds == 9278.85
    assert variant_forecast.total.p90_seconds == 13076.1
    assert (
        report.aggregate_forecast.model_dump()
        == {
            "enabled_variant_ids": [HORIZONTAL_VARIANT_ID],
            "render": variant_forecast.render.model_dump(),
            "encoding": variant_forecast.encoding.model_dump(),
            "qa": variant_forecast.qa.model_dump(),
            "total": variant_forecast.total.model_dump(),
        }
    )
    tampered_report = report.model_dump(mode="json", by_alias=True)
    tampered_report["aggregateForecast"]["total"]["p90Seconds"] += 1.0
    with pytest.raises(
        ValidationError,
        match="aggregate forecasts",
    ):
        FinalReleaseReport.model_validate(tampered_report)
    assert "-RenderProfilePath" in report.commands.horizontal_start_or_resume
    assert (
        "-VerticalRenderProfilePath"
        in report.commands.horizontal_plus_vertical_reference
    )
    for required_argument in (
        "-OutputDirectory",
        "-VerticalOutputDirectory",
        "-OperatorAuthorizationPath",
        "-SourceAudioPath",
        "-SourceCuePath",
        "-AuthorizationToken",
        "-VerticalAuthorizationToken",
    ):
        assert required_argument in report.commands.horizontal_plus_vertical_reference
    assert (
        report.enabled_output_matrix_id
        in report.commands.operator_authorization
    )
    assert (
        f"{report.enabled_output_matrix_id}.operator-start-authorization.json"
        in report.commands.operator_authorization
    )
    assert report.operator_start_gate == "not-authorized"


def test_finalize_rejects_placeholder_media_before_writing(tmp_path: Path) -> None:
    fixture = _finalization_fixture(tmp_path, placeholder_animatic=True)

    with pytest.raises(ReleaseFinalizationError, match="not an MP4 container"):
        finalize_horizontal_release(
            fixture.repository_root,
            fixture.request,
            Path("release-bundle"),
        )

    assert not (fixture.repository_root / "release-bundle").exists()


def test_dashboard_publication_latency_is_measured_and_capped(
    tmp_path: Path,
) -> None:
    fixture = _finalization_fixture(tmp_path)
    dashboard_path = fixture.role_paths["live-dashboard-proof"]
    payload = json.loads(dashboard_path.read_text(encoding="utf-8"))
    completed = datetime.fromisoformat(payload["completedFrame"]["completedAt"])

    threshold_payload = json.loads(json.dumps(payload))
    threshold_payload["completedFrame"]["publicationStartedAt"] = (
        completed + timedelta(seconds=0.1)
    ).isoformat()
    threshold_payload["completedFrame"]["publishedAt"] = (
        completed + timedelta(seconds=2.0)
    ).isoformat()
    threshold_payload["completedFrame"]["publicationLatencySeconds"] = 2.0
    threshold = LiveDashboardProof.model_validate(threshold_payload)
    assert threshold.completed_frame.publication_latency_seconds == 2.0

    too_slow = json.loads(json.dumps(payload))
    too_slow["completedFrame"]["publishedAt"] = (
        completed + timedelta(seconds=2.001)
    ).isoformat()
    too_slow["completedFrame"]["publicationLatencySeconds"] = 2.001
    with pytest.raises(ValidationError, match="publicationLatencySeconds"):
        LiveDashboardProof.model_validate(too_slow)

    tampered = json.loads(json.dumps(payload))
    tampered["completedFrame"]["publicationLatencySeconds"] = 0.5
    with pytest.raises(
        ValidationError,
        match="must match the completion-to-publication timestamps",
    ):
        LiveDashboardProof.model_validate(tampered)


def test_verification_report_requires_exact_matrix_and_honest_skips(
    tmp_path: Path,
) -> None:
    fixture = _finalization_fixture(tmp_path)
    verification_path = fixture.role_paths["verification-report"]
    payload = json.loads(verification_path.read_text(encoding="utf-8"))
    report = VerificationReport.model_validate(payload)
    assert len(report.checks) == 16
    assert report.checks[-1].check_id == "git-diff-check"
    assert report.checks[-1].status == "passed"

    missing = json.loads(json.dumps(payload))
    missing["checks"].pop(3)
    with pytest.raises(ValidationError, match="exactly cover"):
        VerificationReport.model_validate(missing)

    duplicate = json.loads(json.dumps(payload))
    duplicate["checks"][1]["checkId"] = duplicate["checks"][0]["checkId"]
    with pytest.raises(ValidationError, match="exactly cover"):
        VerificationReport.model_validate(duplicate)

    skip_reason = "Browser runtime was unavailable for the E2E check."
    unsurfaced_skip = json.loads(json.dumps(payload))
    unsurfaced_skip["checks"][9]["status"] = "skipped"
    unsurfaced_skip["checks"][9]["skipReason"] = skip_reason
    with pytest.raises(ValidationError, match="appear verbatim"):
        VerificationReport.model_validate(unsurfaced_skip)

    empty_skip = json.loads(json.dumps(payload))
    empty_skip["checks"][9]["status"] = "skipped"
    empty_skip["checks"][9]["skipReason"] = ""
    with pytest.raises(ValidationError, match="skipReason"):
        VerificationReport.model_validate(empty_skip)

    surfaced_skip = json.loads(json.dumps(unsurfaced_skip))
    surfaced_skip["knownLimitations"].append(skip_reason)
    skipped_report = VerificationReport.model_validate(surfaced_skip)
    assert skipped_report.checks[9].status == "skipped"

    skipped_git_diff = json.loads(json.dumps(surfaced_skip))
    skipped_git_diff["checks"][-1]["status"] = "skipped"
    skipped_git_diff["checks"][-1]["skipReason"] = "Git was unavailable."
    skipped_git_diff["knownLimitations"].append("Git was unavailable.")
    with pytest.raises(ValidationError, match="git diff --check must have passed"):
        VerificationReport.model_validate(skipped_git_diff)


def test_finalize_recomputes_and_rejects_tampered_calibration(
    tmp_path: Path,
) -> None:
    fixture = _finalization_fixture(tmp_path, tamper_calibration=True)

    with pytest.raises(
        ReleaseFinalizationError,
        match="weighted render projection",
    ):
        finalize_horizontal_release(
            fixture.repository_root,
            fixture.request,
            Path("release-bundle"),
        )

    assert not (fixture.repository_root / "release-bundle").exists()


def test_finalize_rejects_echoed_git_tree_values(tmp_path: Path) -> None:
    fixture = _finalization_fixture(tmp_path)
    request = _request_with_updates(
        fixture.request,
        sourceTreeSha256="e" * 64,
    )

    with pytest.raises(
        ReleaseFinalizationError,
        match="recomputed directly from Git HEAD",
    ):
        finalize_horizontal_release(
            fixture.repository_root,
            request,
            Path("release-bundle"),
        )


def test_finalize_requires_exact_tracked_release_hold_identity(
    tmp_path: Path,
) -> None:
    fixture = _finalization_fixture(tmp_path)
    request = _request_with_updates(
        fixture.request,
        supersedesReleaseIdentitySha256="f" * 64,
    )

    with pytest.raises(
        ReleaseFinalizationError,
        match="exact tracked release hold",
    ):
        finalize_horizontal_release(
            fixture.repository_root,
            request,
            Path("release-bundle"),
        )


def test_finalize_rejects_release_owned_dirty_state(tmp_path: Path) -> None:
    fixture = _finalization_fixture(tmp_path)
    dirty_path = (
        fixture.repository_root
        / "backend"
        / "app"
        / "cinematic"
        / "uncommitted-release-source.py"
    )
    _write_text(dirty_path, "DIRTY = True\n")

    with pytest.raises(
        ReleaseFinalizationError,
        match="release-owned source paths",
    ):
        finalize_horizontal_release(
            fixture.repository_root,
            fixture.request,
            Path("release-bundle"),
        )


def test_json_role_cannot_bypass_validation_with_non_json_suffix(
    tmp_path: Path,
) -> None:
    fixture = _finalization_fixture(tmp_path)
    old_path = fixture.role_paths["verification-report"]
    new_path = old_path.with_suffix(".txt")
    old_path.replace(new_path)
    payload = fixture.request.model_dump(mode="json", by_alias=True)
    artifacts = payload["artifacts"]
    assert isinstance(artifacts, list)
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        if artifact["role"] == "verification-report":
            artifact["path"] = new_path.relative_to(
                fixture.repository_root
            ).as_posix()
            break
    request = ReleaseFinalizationRequest.model_validate(payload)

    with pytest.raises(ReleaseFinalizationError, match="strict JSON"):
        finalize_horizontal_release(
            fixture.repository_root,
            request,
            Path("release-bundle"),
        )


def test_bundle_write_failure_leaves_no_partial_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _finalization_fixture(tmp_path)
    original_write = release_module._write_bytes
    write_count = 0

    def fail_second_write(
        path: Path,
        payload: bytes,
        *,
        overwrite: bool,
    ) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("synthetic staging failure")
        original_write(path, payload, overwrite=overwrite)

    monkeypatch.setattr(release_module, "_write_bytes", fail_second_write)

    with pytest.raises(
        ReleaseFinalizationError,
        match="synthetic staging failure",
    ):
        finalize_horizontal_release(
            fixture.repository_root,
            fixture.request,
            Path("release-bundle"),
        )

    assert not (fixture.repository_root / "release-bundle").exists()
    assert not list(fixture.repository_root.glob(".release-bundle.*.staging"))


def test_finalize_never_overwrites_existing_bundle(tmp_path: Path) -> None:
    fixture = _finalization_fixture(tmp_path)
    output_root = fixture.repository_root / "release-bundle"
    sentinel = output_root / "preserve.txt"
    _write_text(sentinel, "existing release must survive\n")

    with pytest.raises(ReleaseFinalizationError, match="never overwrites"):
        finalize_horizontal_release(
            fixture.repository_root,
            fixture.request,
            Path("release-bundle"),
            overwrite=True,
        )

    assert sentinel.read_text(encoding="utf-8") == "existing release must survive\n"
    assert list(output_root.iterdir()) == [sentinel]


def test_human_visual_qa_cannot_hide_blocking_findings() -> None:
    with pytest.raises(ValidationError, match="knownBlockingFindings"):
        HumanVisualQaApproval.model_validate(
            {
                "schemaVersion": "1.0.0",
                "kind": "trackprompt-andromeda-v2-human-visual-qa-approval",
                "approvalId": "invalid-blocking-approval",
                "projectId": "trip-to-andromeda-v2",
                "reviewedByRole": "human-artist",
                "reviewedAt": datetime.now(UTC).isoformat(),
                "decision": "approved-for-final-release",
                "referenceRevision": "andromeda-r13.1-selected-refinement",
                "sceneSha256": "1" * 64,
                "horizontalRenderProfileSha256": "2" * 64,
                "fullAudioAnimaticSha256": "3" * 64,
                "animaticMediaQaReportSha256": "4" * 64,
                "finalSceneReceiptSha256": "5" * 64,
                "motionHealthReportSha256": "6" * 64,
                "exposureMobileReadabilityReportSha256": "7" * 64,
                "finalQualityTransitionReportSha256": "8" * 64,
                "verticalCompositionProofSha256": "9" * 64,
                "verticalRenderProfileSha256": "a" * 64,
                "verticalBoundedProofMediaSha256": "b" * 64,
                "allSevenActsAtR131ProjectLevel": True,
                "phoneSizeReadabilityApproved": True,
                "finalQualityTransitionsApproved": True,
                "knownBlockingFindings": ["arrival still ambiguous"],
                "humanArtisticApproval": True,
                "codexHumanArtisticApproval": False,
                "productionRenderStarted": False,
            }
        )


def test_generated_profiles_bind_exact_builder_and_scenes(tmp_path: Path) -> None:
    fixture = _finalization_fixture(tmp_path)
    builder_sha256 = file_sha256(fixture.role_paths["builder-source"])
    horizontal = VersionedRenderProfile.model_validate_json(
        fixture.role_paths["horizontal-render-profile"].read_text(encoding="utf-8")
    )
    vertical = VersionedRenderProfile.model_validate_json(
        fixture.role_paths["vertical-render-profile"].read_text(encoding="utf-8")
    )

    assert horizontal.source_identities.builder_source_sha256 == builder_sha256
    assert vertical.source_identities.builder_source_sha256 == builder_sha256
    assert horizontal.approved_scene_sha256 == file_sha256(
        fixture.role_paths["final-scene"]
    )
    assert vertical.approved_scene_sha256 == file_sha256(
        fixture.role_paths["vertical-master-scene"]
    )
    assert horizontal.production_start_allowed is False
    assert vertical.output_variant.enabled is False

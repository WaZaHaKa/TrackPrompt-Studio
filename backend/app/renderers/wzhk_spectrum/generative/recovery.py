from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ....config import Settings
from ..capture import _artifact, _atomic_json
from ..preflight import SpectrumPaths, ensure_within, sha256_file
from ..production import (
    GENERATIVE_OUTPUT_FILENAME,
    SpectrumArtifactType,
    SpectrumMasterTiming,
    SpectrumProductionError,
    SpectrumProductionState,
    probe_media_file,
    validate_final_media,
)
from ..workspace import load_workspace_job

SOURCE_OUTPUT = f"output/{GENERATIVE_OUTPUT_FILENAME}"
REVIEW_TIMESTAMPS = {
    "intro-0010.png": 10.0,
    "intro-end-0103.png": 63.0,
    "main-0105.png": 65.0,
    "main-mid-0200.png": 120.0,
    "main-end-0255.png": 175.0,
    "outro-0257.png": 177.0,
    "grid-end-0311.png": 191.0,
    "post-grid-tail-0313.png": 193.0,
    "near-eof.png": 196.119796,
}
COMPARISON_LABELS = {
    "0010.png": 10.0,
    "0103.png": 63.0,
    "0105.png": 65.0,
    "0200.png": 120.0,
    "0255.png": 175.0,
    "0257.png": 177.0,
    "0311.png": 191.0,
    "0313.png": 193.0,
    "near-eof.png": 196.119796,
}


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpectrumProductionError("A stabilization recovery record is invalid.") from exc
    if not isinstance(value, dict):
        raise SpectrumProductionError("A stabilization recovery record is invalid.")
    return value


def _root(paths: SpectrumPaths, job_id: str) -> Path:
    job = load_workspace_job(paths, job_id)
    return ensure_within(paths.jobs_root, paths.data_root / job.workspace_relative_path)


def finalize_branding_stabilization(
    settings: Settings,
    paths: SpectrumPaths,
    *,
    source_job_id: str,
    target_job_id: str,
) -> dict[str, Any]:
    source_root = _root(paths, source_job_id)
    target_root = _root(paths, target_job_id)
    source_manifest = _read_object(source_root / "manifest.json")
    target_manifest = _read_object(target_root / "manifest.json")
    if source_manifest.get("state") != SpectrumProductionState.COMPLETE.value:
        raise SpectrumProductionError("Branding stabilization requires a complete source job.")
    if not bool(source_manifest.get("validationReport", {}).get("valid")):
        raise SpectrumProductionError("The branding-stabilization source was not validated.")
    if target_manifest.get("backgroundMode") != "generative-geometry":
        raise SpectrumProductionError("The recovery target is not a generative workspace.")

    source_final = ensure_within(source_root, source_root / SOURCE_OUTPUT)
    target_final = ensure_within(target_root, target_root / SOURCE_OUTPUT)
    validation_path = ensure_within(target_root, target_root / "output" / "validation-report.json")
    sanity_path = ensure_within(target_root, target_root / "output" / "visual-sanity-report.json")
    recovery_path = ensure_within(
        target_root,
        target_root / "output" / "branding-stabilization-manifest.json",
    )
    comparison_root = ensure_within(target_root, target_root / "output" / "comparison")
    comparison_manifest_path = ensure_within(
        target_root,
        comparison_root / "manifest.json",
    )
    audio_evidence_path = ensure_within(
        target_root,
        target_root / "output" / "audio-copy-evidence.json",
    )
    if not source_final.is_file() or not target_final.is_file():
        raise SpectrumProductionError("A branding-stabilization media artifact is missing.")
    sanity = _read_object(sanity_path)
    if sanity.get("valid") is not True:
        raise SpectrumProductionError("The corrected branding artifact failed visual sanity checks.")

    timing = SpectrumMasterTiming.model_validate(source_manifest["masterTiming"])
    probe = probe_media_file(settings.ffprobe_path, target_final, count_frames=False)
    validation = validate_final_media(probe, timing)
    if not validation.valid:
        raise SpectrumProductionError("The corrected branding artifact failed media validation.")
    _atomic_json(validation_path, validation.model_dump(mode="json", by_alias=True))

    source_validation_path = source_root / "output" / "validation-report.json"
    logo_path = paths.logo_directory / "WZHK.png"
    failed_capture = target_root / "capture" / "scattered-visual-capture.mkv"
    source_aac = target_root / "output" / "audio-copy-evidence" / "source.aac"
    target_aac = target_root / "output" / "audio-copy-evidence" / "target.aac"
    if not source_aac.is_file() or not target_aac.is_file():
        raise SpectrumProductionError("Elementary-stream audio copy evidence is missing.")
    source_audio_hash = sha256_file(source_aac)
    target_audio_hash = sha256_file(target_aac)
    if source_audio_hash != target_audio_hash:
        raise SpectrumProductionError("The stabilized artifact changed the validated AAC stream.")
    audio_evidence = {
        "schemaVersion": "1.0.0",
        "sourceElementaryStreamSha256": source_audio_hash,
        "targetElementaryStreamSha256": target_audio_hash,
        "identical": True,
        "method": "FFmpeg ADTS extraction with stream copy",
    }
    _atomic_json(audio_evidence_path, audio_evidence)

    recovery_record: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "operation": "deterministic-browser-branding-stabilization",
        "source": {
            "jobId": source_job_id,
            "relativePath": source_final.relative_to(source_root).as_posix(),
            "sha256": sha256_file(source_final),
            "validationSha256": (
                sha256_file(source_validation_path)
                if source_validation_path.is_file()
                else None
            ),
            "designHash": source_manifest.get("designHash"),
        },
        "target": {
            "jobId": target_job_id,
            "relativePath": target_final.relative_to(target_root).as_posix(),
            "sha256": sha256_file(target_final),
            "sizeBytes": target_final.stat().st_size,
            "designHash": target_manifest.get("designHash"),
            "workspaceHash": target_manifest.get("generatedWorkspaceHash"),
        },
        "branding": {
            "logoSha256": sha256_file(logo_path),
            "logoBounds": {"x": 96, "y": 76, "width": 220, "height": 220},
            "textBounds": {"x": 70, "y": 350, "width": 560, "height": 215},
            "fontFiles": ["segoeuib.ttf", "segoeuil.ttf", "segoeui.ttf"],
            "backgroundColor": "#070A12",
        },
        "audioPolicy": {
            "operation": "copy validated source AAC without re-encoding",
            "elementaryStreamSha256": source_audio_hash,
            "evidence": audio_evidence_path.relative_to(target_root).as_posix(),
        },
        "failedTargetCapturePreserved": (
            {
                "relativePath": failed_capture.relative_to(target_root).as_posix(),
                "sha256": sha256_file(failed_capture),
                "sizeBytes": failed_capture.stat().st_size,
            }
            if failed_capture.is_file()
            else None
        ),
        "visualSanitySha256": sha256_file(sanity_path),
    }
    _atomic_json(recovery_path, recovery_record)

    comparisons: list[dict[str, Any]] = []
    for filename, timestamp in COMPARISON_LABELS.items():
        baseline = comparison_root / "milestone-3.5" / filename
        current = comparison_root / "milestone-3.6" / filename
        combined = comparison_root / "side-by-side" / filename
        if not baseline.is_file() or not current.is_file() or not combined.is_file():
            raise SpectrumProductionError("The Milestone 3.5/3.6 comparison set is incomplete.")
        comparisons.append(
            {
                "timestampSeconds": timestamp,
                "milestone35": {
                    "relativePath": baseline.relative_to(target_root).as_posix(),
                    "sha256": sha256_file(baseline),
                },
                "milestone36": {
                    "relativePath": current.relative_to(target_root).as_posix(),
                    "sha256": sha256_file(current),
                },
                "sideBySide": {
                    "relativePath": combined.relative_to(target_root).as_posix(),
                    "sha256": sha256_file(combined),
                },
            }
        )
    comparison_manifest = {
        "schemaVersion": "1.0.0",
        "left": {"milestone": "3.5", "jobId": "212e5178-3f81-401a-9171-d6dfcf0302b2"},
        "right": {"milestone": "3.6", "jobId": target_job_id},
        "frames": comparisons,
    }
    _atomic_json(comparison_manifest_path, comparison_manifest)

    artifacts = [
        _artifact(
            target_root,
            recovery_path,
            SpectrumArtifactType.BRANDING_STABILIZATION_MANIFEST,
            SpectrumProductionState.VALIDATING,
            "Validated source-video branding stabilization with immutable lineage",
        ),
        _artifact(
            target_root,
            target_final,
            SpectrumArtifactType.FINAL_VIDEO,
            SpectrumProductionState.VALIDATING,
            "Validated WZHK Generative Geometry Milestone 3.6 MP4",
        ),
        _artifact(
            target_root,
            validation_path,
            SpectrumArtifactType.VALIDATION_REPORT,
            SpectrumProductionState.VALIDATING,
            "ffprobe structural, duration, stream, frame-rate, and frame-count checks",
        ),
        _artifact(
            target_root,
            sanity_path,
            SpectrumArtifactType.VISUAL_SANITY_REPORT,
            SpectrumProductionState.VALIDATING,
            "Non-brittle frame statistics for geometry, foreground, spectrum, and tail decay",
        ),
        _artifact(
            target_root,
            audio_evidence_path,
            SpectrumArtifactType.AUDIO_COPY_EVIDENCE,
            SpectrumProductionState.VALIDATING,
            "Identical source and stabilized AAC elementary-stream hashes",
        ),
        _artifact(
            target_root,
            comparison_manifest_path,
            SpectrumArtifactType.COMPARISON_MANIFEST,
            SpectrumProductionState.VALIDATING,
            "Matched Milestone 3.5 and 3.6 review-frame lineage",
        ),
    ]
    for filename, timestamp in REVIEW_TIMESTAMPS.items():
        frame = target_root / "output" / "review-frames" / filename
        artifacts.append(
            _artifact(
                target_root,
                frame,
                SpectrumArtifactType.REVIEW_FRAME,
                SpectrumProductionState.VALIDATING,
                "Deterministic corrected review-frame extraction",
                timestamp_seconds=timestamp,
            )
        )
    for filename, timestamp in COMPARISON_LABELS.items():
        artifacts.append(
            _artifact(
                target_root,
                comparison_root / "side-by-side" / filename,
                SpectrumArtifactType.COMPARISON_FRAME,
                SpectrumProductionState.VALIDATING,
                "Side-by-side Milestone 3.5 left and Milestone 3.6 right",
                timestamp_seconds=timestamp,
            )
        )

    target_manifest.update(
        state=SpectrumProductionState.COMPLETE.value,
        artifacts=[item.model_dump(mode="json", by_alias=True) for item in artifacts],
        synchronization=source_manifest.get("synchronization"),
        validationReport=validation.model_dump(mode="json", by_alias=True),
        captureProvider="ffmpeg-gfxcapture+branding-stabilization",
        encoder="h264_nvenc",
        capturedFrames=source_manifest.get("capturedFrames"),
        droppedFrames=source_manifest.get("droppedFrames"),
        captureDurationSeconds=source_manifest.get("captureDurationSeconds"),
        geometryCapability=source_manifest.get("geometryCapability"),
        geometryTelemetry=source_manifest.get("geometryTelemetry"),
        recovery={
            "kind": "branding-stabilization",
            "sourceJobId": source_job_id,
            "manifest": recovery_path.relative_to(target_root).as_posix(),
        },
        errorMessage=None,
        updatedAt=datetime.now(UTC).isoformat(),
    )
    _atomic_json(target_root / "manifest.json", target_manifest)
    return target_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize a validated WZHK branding recovery.")
    parser.add_argument("--source-job-id", required=True)
    parser.add_argument("--target-job-id", required=True)
    arguments = parser.parse_args()
    settings = Settings.from_env()
    repository_root = Path(__file__).resolve().parents[5]
    manifest = finalize_branding_stabilization(
        settings,
        SpectrumPaths(repository_root, settings.data_dir),
        source_job_id=arguments.source_job_id,
        target_job_id=arguments.target_job_id,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

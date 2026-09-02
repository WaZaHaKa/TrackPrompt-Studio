from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.mission_control.render_contracts import (
    ArtifactState,
    ArtifactStore,
    CompositionProfile,
    EncodeTask,
    FrameArtifact,
    LocalFilesystemArtifactStore,
    MediaRenderJob,
    OutputVariant,
    OutputVariantMatrixIdentity,
    OutputVariantProgress,
    PackageIdentity,
    ProjectRef,
    QaCheck,
    QaResult,
    QaStatus,
    ShotRenderTask,
    StageProgress,
    TaskState,
    TenantRef,
)

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _variant(
    variant_id: str,
    *,
    enabled: bool,
    required: bool,
    width: int,
    height: int,
    root: str | None = None,
) -> OutputVariant:
    variant_root = root or f"jobs/job-1/variants/{variant_id}"
    return OutputVariant(
        id=variant_id,
        enabled=enabled,
        required=required,
        width=width,
        height=height,
        fps=30,
        deliverable_role="primary" if required else "alternate",
        render_profile_id=f"profile-{variant_id}",
        render_profile_sha256=_digest(f"profile-{variant_id}"),
        composition_profile=CompositionProfile(
            id=f"composition-{variant_id}",
            revision="revision-1",
            scene_sha256=_digest(f"scene-{variant_id}"),
            camera_sha256=_digest(f"camera-{variant_id}"),
            composition_sha256=_digest(f"composition-{variant_id}"),
        ),
        output_variant_sha256=_digest(f"variant-{variant_id}"),
        frames_root=f"{variant_root}/frames",
        preview_root=f"{variant_root}/previews",
        encode_root=f"{variant_root}/encodes",
        qa_root=f"{variant_root}/qa",
        progress=OutputVariantProgress(
            output_variant_id=variant_id,
            total_frames=120 if enabled else 0,
            current_frame=18 if enabled else None,
            latest_rendered_frame=17 if enabled else None,
            latest_safe_frame=16 if enabled else None,
            in_flight_frames=(18,) if enabled else (),
            active_worker_ids=("worker-1",) if enabled else (),
            retry_count=1 if enabled else 0,
            preview_url=(
                f"/api/jobs/job-1/variants/{variant_id}/preview"
                if enabled
                else None
            ),
            full_frame_url=(
                f"/api/jobs/job-1/variants/{variant_id}/frames/17"
                if enabled
                else None
            ),
            latest_frame_artifact=(
                f"{variant_root}/checkpoints/chunk-1/frame_000017.png"
                if enabled
                else None
            ),
            latest_frame_artifact_frame=17 if enabled else None,
            latest_frame_written_at=NOW if enabled else None,
            updated_at=NOW,
        ),
    )


def _job(*variants: OutputVariant) -> MediaRenderJob:
    package_hash = _digest("package")
    enabled = tuple(variant for variant in variants if variant.enabled)
    return MediaRenderJob(
        id="job-1",
        project=ProjectRef(
            tenant=TenantRef(namespace="tenant-1", deployment_id="local-1"),
            project_id="project-1",
            revision="revision-1",
        ),
        package=PackageIdentity(
            package_id="package-1",
            package_sha256=package_hash,
            source_revision="commit-1",
            source_hashes={"story": _digest("story"), "media": _digest("media")},
            tool_versions={"renderer": "1.2.3"},
        ),
        output_matrix=OutputVariantMatrixIdentity(
            matrix_id="matrix-1",
            matrix_sha256=_digest("matrix"),
            package_sha256=package_hash,
            enabled_variant_ids=tuple(variant.id for variant in enabled),
            variant_sha256_by_id={
                variant.id: variant.output_variant_sha256 for variant in enabled
            },
        ),
        output_variants=variants,
        created_at=NOW,
        updated_at=NOW,
    )


def test_dual_variant_contract_is_versioned_isolated_and_round_trips() -> None:
    landscape = _variant(
        "landscape",
        enabled=True,
        required=True,
        width=1920,
        height=1080,
    )
    portrait = _variant(
        "portrait",
        enabled=True,
        required=False,
        width=1080,
        height=1920,
    )

    job = _job(landscape, portrait)
    payload = job.model_dump_json(by_alias=True)
    restored = MediaRenderJob.model_validate_json(payload)

    assert restored == job
    assert '"schemaVersion":"2.0.0"' in payload
    assert '"outputVariants"' in payload
    assert restored.output_matrix.enabled_variant_ids == ("landscape", "portrait")
    assert landscape.frames_root != portrait.frames_root
    assert landscape.composition_profile.camera_sha256 != portrait.composition_profile.camera_sha256
    assert restored.output_variants[0].progress.latest_rendered_frame == 17
    assert restored.output_variants[0].progress.latest_safe_frame == 16
    assert restored.output_variants[0].progress.in_flight_frames == (18,)
    assert restored.output_variants[0].progress.latest_frame_artifact_frame == 17
    assert '"fullFrameUrl":' in payload


def test_required_variant_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError, match="required output variants must be enabled"):
        _variant(
            "required-output",
            enabled=False,
            required=True,
            width=1280,
            height=720,
        )


def test_job_rejects_matrix_drift_and_cross_variant_root_collisions() -> None:
    landscape = _variant(
        "landscape",
        enabled=True,
        required=True,
        width=1920,
        height=1080,
    )
    portrait = _variant(
        "portrait",
        enabled=True,
        required=False,
        width=1080,
        height=1920,
    )
    package_hash = _digest("package")
    mismatched_matrix = OutputVariantMatrixIdentity(
        matrix_id="matrix-1",
        matrix_sha256=_digest("matrix"),
        package_sha256=package_hash,
        enabled_variant_ids=("landscape",),
        variant_sha256_by_id={"landscape": landscape.output_variant_sha256},
    )
    with pytest.raises(ValidationError, match="exactly match enabled variants"):
        MediaRenderJob(
            id="job-1",
            project=ProjectRef(
                tenant=TenantRef(namespace="tenant-1"),
                project_id="project-1",
            ),
            package=PackageIdentity(
                package_id="package-1",
                package_sha256=package_hash,
                source_revision="commit-1",
            ),
            output_matrix=mismatched_matrix,
            output_variants=(landscape, portrait),
            created_at=NOW,
            updated_at=NOW,
        )

    colliding = _variant(
        "portrait",
        enabled=True,
        required=False,
        width=1080,
        height=1920,
        root="jobs/job-1/variants/landscape",
    )
    with pytest.raises(ValidationError, match="isolated artifact roots"):
        _job(landscape, colliding)


def test_frame_artifacts_are_immutable_and_paths_are_portable() -> None:
    frame = FrameArtifact(
        id="frame-1",
        job_id="job-1",
        output_variant_id="landscape",
        frame_number=1,
        artifact_key="jobs/job-1/variants/landscape/frames/000001.png",
        sha256=_digest("frame"),
        byte_size=1024,
        width=1920,
        height=1080,
        state=ArtifactState.RENDERED,
        worker_id="worker-1",
        chunk_id="chunk-1",
        shot_id="shot-1",
        created_at=NOW,
    )

    with pytest.raises(ValidationError, match="frozen"):
        frame.state = ArtifactState.SAFE
    with pytest.raises(ValidationError, match="portable POSIX-style"):
        FrameArtifact(
            **{
                **frame.model_dump(),
                "id": "frame-2",
                "artifact_key": r"C:\private\frame.png",
            }
        )


def test_encode_and_qa_records_are_variant_scoped() -> None:
    encode = EncodeTask(
        id="encode-1",
        job_id="job-1",
        output_variant_id="landscape",
        deliverable_role="primary",
        state=TaskState.COMPLETE,
        manifest_sha256=_digest("encode-manifest"),
        input_frames_root="jobs/job-1/variants/landscape/frames",
        output_artifact_key="jobs/job-1/variants/landscape/encodes/final.mp4",
        completed_units=120,
        total_units=120,
        updated_at=NOW,
    )
    qa = QaResult(
        id="qa-1",
        job_id="job-1",
        output_variant_id="landscape",
        status=QaStatus.PASS,
        checks=(QaCheck(id="dimensions", status=QaStatus.PASS, summary="Dimensions match."),),
        report_artifact_key="jobs/job-1/variants/landscape/qa/report.json",
        report_sha256=_digest("qa-report"),
        completed_at=NOW,
    )

    with pytest.raises(ValidationError, match="must match their output variant"):
        OutputVariantProgress(
            output_variant_id="portrait",
            stages=(
                StageProgress(stage="encoding", state="complete", updated_at=NOW),
            ),
            total_frames=120,
            rendered_frames=120,
            validated_frames=120,
            encode_tasks=(encode,),
            qa_results=(qa,),
            updated_at=NOW,
        )


def test_local_artifact_store_is_root_scoped_atomic_and_non_overwriting(
    tmp_path: Path,
) -> None:
    store: ArtifactStore = LocalFilesystemArtifactStore(tmp_path / "artifacts")
    stored = store.put_bytes("jobs/job-1/frames/000001.png", b"synthetic-frame")

    assert stored.sha256 == _digest("synthetic-frame")
    assert store.exists(stored.artifact_key)
    assert store.read_bytes(stored.artifact_key) == b"synthetic-frame"
    with pytest.raises(FileExistsError, match="artifact already exists"):
        store.put_bytes(stored.artifact_key, b"replacement")
    with pytest.raises(ValueError, match="normalized relative paths"):
        store.exists("../outside")


def test_synthetic_fixture_exercises_horizontal_only_and_dual_variant_tasks() -> None:
    fixture_path = (
        Path(__file__).parent / "fixtures" / "synthetic_output_variant_matrix.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert fixture["kind"] == "synthetic-output-variant-matrix"
    assert [scenario["frameRange"] for scenario in fixture["scenarios"]] == [
        {"start": 17, "end": 28},
        {"start": 101, "end": 112},
    ]
    enabled_counts: list[int] = []
    for scenario in fixture["scenarios"]:
        variants = []
        for raw_variant in scenario["outputVariants"]:
            variant_id = raw_variant["id"]
            root = raw_variant["artifactRoot"]
            variants.append(
                OutputVariant(
                    id=variant_id,
                    enabled=raw_variant["enabled"],
                    required=raw_variant["required"],
                    width=raw_variant["width"],
                    height=raw_variant["height"],
                    fps=raw_variant["fps"],
                    composition_mode=raw_variant["compositionMode"],
                    deliverable_role=raw_variant["deliverableRole"],
                    render_profile_id=f"profile-{variant_id}",
                    render_profile_sha256=_digest(
                        f"{scenario['id']}-profile-{variant_id}"
                    ),
                    composition_profile=CompositionProfile(
                        id=f"composition-{variant_id}",
                        revision="fixture-1",
                        scene_sha256=_digest(
                            f"{scenario['id']}-scene-{variant_id}"
                        ),
                        camera_sha256=_digest(
                            f"{scenario['id']}-camera-{variant_id}"
                        ),
                        composition_sha256=_digest(
                            f"{scenario['id']}-composition-{variant_id}"
                        ),
                    ),
                    output_variant_sha256=_digest(
                        f"{scenario['id']}-variant-{variant_id}"
                    ),
                    frames_root=f"{root}/frames",
                    preview_root=f"{root}/previews",
                    encode_root=f"{root}/encodes",
                    qa_root=f"{root}/qa",
                    progress=OutputVariantProgress(
                        output_variant_id=variant_id,
                        total_frames=12 if raw_variant["enabled"] else 0,
                        updated_at=NOW,
                    ),
                )
            )
        job = _job(*variants)
        enabled_variants = tuple(variant for variant in variants if variant.enabled)
        tasks = tuple(
            ShotRenderTask(
                id=f"task-{scenario['id']}-{variant.id}",
                job_id=job.id,
                output_variant_id=variant.id,
                shot_id="synthetic-shot",
                chunk_id="synthetic-chunk",
                frame_start=scenario["frameRange"]["start"],
                frame_end=scenario["frameRange"]["end"],
                width=variant.width,
                height=variant.height,
                fps=variant.fps,
                complexity_class="synthetic",
                package_sha256=job.package.package_sha256,
                matrix_sha256=job.output_matrix.matrix_sha256,
                output_variant_sha256=variant.output_variant_sha256,
                scene_sha256=variant.composition_profile.scene_sha256,
                render_profile_sha256=variant.render_profile_sha256,
                composition_sha256=variant.composition_profile.composition_sha256,
                task_sha256=_digest(f"{scenario['id']}-task-{variant.id}"),
                output_root=variant.frames_root,
            )
            for variant in enabled_variants
        )
        enabled_counts.append(len(tasks))
        assert all(task.frame_count == 12 for task in tasks)
        assert {(task.width, task.height) for task in tasks} == {
            (variant.width, variant.height) for variant in enabled_variants
        }
        assert {task.output_variant_id for task in tasks} == {
            variant.id for variant in enabled_variants
        }

    assert enabled_counts == [1, 2]

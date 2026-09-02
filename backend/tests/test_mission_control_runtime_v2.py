from __future__ import annotations

import asyncio
import json
import struct
import sys
import zlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import app.mission_control.service as service_module
from app.mission_control.config import MissionControlConfig
from app.mission_control.discovery import sha256_file
from app.mission_control.errors import MissionControlError
from app.mission_control.local_worker import (
    LocalSubprocessRenderWorker,
    LocalWorkerRunStatus,
    TrackPromptTaskCommandBuilder,
)
from app.mission_control.models import (
    FakeRenderOptions,
    JobRecord,
    JobState,
    ProfileSummary,
    RendererKind,
    RenderIdentity,
    Resolution,
    RetryFailedRenderRequest,
    StartFakeRenderRequest,
    StructuredError,
)
from app.mission_control.render_contracts import (
    CompositionProfile,
    OutputVariant,
    OutputVariantProgress,
    RenderStage,
    TaskState,
    WorkerCapabilities,
    WorkerKind,
)
from app.mission_control.renderers import RendererTelemetryEvent
from app.mission_control.scheduler import LeaseRejectedError
from app.mission_control.service import MissionControlService

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png(width: int, height: int) -> bytes:
    scanlines = b"".join(
        b"\x00" + b"\x10\x20\x30" * width for _ in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(scanlines))
        + _chunk(b"IEND", b"")
    )


def _digest(character: str) -> str:
    return character * 64


def test_checked_in_final_singular_profile_activates_horizontal_v2_contract() -> None:
    profile_path = (
        REPOSITORY_ROOT
        / "render-profiles"
        / "trip-to-andromeda"
        / "andromeda-v2-horizontal-1080p-final.json"
    )
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = ProfileSummary(
        id=payload["profileId"],
        project_id=payload["project"],
        scene_id=payload["preset"],
        display_name=payload["displayName"],
        path=str(profile_path),
        saved_file_sha256=sha256_file(profile_path),
        scene_sha256=payload["approvedSceneSha256"].upper(),
        resolution=Resolution(
            width=payload["resolution"]["width"],
            height=payload["resolution"]["height"],
            label="1920x1080",
        ),
        fps=float(payload["fps"]),
        frame_start=payload["frameStart"],
        frame_end=payload["frameEnd"],
        total_frames=payload["frameEnd"] - payload["frameStart"] + 1,
        frames_per_chunk=payload["chunking"]["framesPerChunk"],
        quality_role="release",
        calibrated=True,
        authorization_status="technical-ready",
        authorized=False,
    )
    service = object.__new__(MissionControlService)

    definitions = service._output_variant_definitions(profile, payload)

    assert len(definitions) == 1
    assert definitions[0].id == "horizontal-16x9-1080p"
    assert definitions[0].enabled is True
    assert definitions[0].required is True
    assert (definitions[0].width, definitions[0].height) == (1920, 1080)
    assert (
        definitions[0].composition_profile.id
        == "andromeda-v2-horizontal-master-v1"
    )


def _runtime_variant(
    variant_id: str,
    *,
    enabled: bool,
    required: bool,
    frames_root: str,
) -> OutputVariant:
    now = datetime.now(UTC)
    return OutputVariant(
        id=variant_id,
        enabled=enabled,
        required=required,
        width=16,
        height=16 if variant_id == "wide" else 32,
        fps=30,
        deliverable_role="primary" if required else "alternate",
        render_profile_id=f"profile-{variant_id}",
        render_profile_sha256=_digest("a"),
        composition_profile=CompositionProfile(
            id=f"composition-{variant_id}",
            revision="1",
            scene_sha256=_digest("b"),
            camera_sha256=_digest("c"),
            composition_sha256=_digest("d"),
        ),
        output_variant_sha256=_digest("e" if enabled else "f"),
        frames_root=frames_root,
        preview_root=f"variant-artifacts/{variant_id}/previews",
        encode_root=f"variant-artifacts/{variant_id}/encodes",
        qa_root=f"variant-artifacts/{variant_id}/qa",
        progress=OutputVariantProgress(
            output_variant_id=variant_id,
            total_frames=10 if enabled else 0,
            latest_rendered_frame=7 if enabled else None,
            updated_at=now,
        ),
    )


def _artifact_job(output: Path) -> JobRecord:
    now = datetime.now(UTC)
    return JobRecord(
        id="variant-artifact-job",
        renderer=RendererKind.FAKE,
        state=JobState.RUNNING,
        identity=RenderIdentity(
            project_id="project",
            scene_id="scene",
            scene_sha256="A" * 64,
            profile_id="profile",
            profile_sha256="B" * 64,
            output_directory=str(output),
            output_variant_id="wide",
            output_width=16,
            output_height=16,
            composition_profile_id="composition-wide",
        ),
        created_at=now,
        updated_at=now,
        frame_start=1,
        frame_end=10,
        total_frame_count=10,
        chunks_total=1,
        latest_preview_frame=7,
        output_variants=(
            _runtime_variant(
                "wide",
                enabled=True,
                required=True,
                frames_root="variants/wide/frames",
            ),
            _runtime_variant(
                "portrait",
                enabled=False,
                required=False,
                frames_root="variants/portrait/frames",
            ),
        ),
        active_variant_id="wide",
    )


def test_explicit_variant_artifact_requests_fail_closed_without_legacy_fallback(
    tmp_path: Path,
) -> None:
    legacy_frames = tmp_path / "frames"
    legacy_frames.mkdir(parents=True)
    for frame in (7, 8, 9):
        (legacy_frames / f"frame_{frame:06d}.png").write_bytes(_png(16, 16))
    wide_frames = tmp_path / "variants" / "wide" / "frames"
    wide_frames.mkdir(parents=True)
    (wide_frames / "frame_000007.png").write_bytes(_png(16, 16))
    portrait_frames = tmp_path / "variants" / "portrait" / "frames"
    portrait_frames.mkdir(parents=True)
    (portrait_frames / "frame_000007.png").write_bytes(_png(16, 32))
    (portrait_frames / "frame_000009.png").write_bytes(_png(16, 32))

    job = _artifact_job(tmp_path)
    service = object.__new__(MissionControlService)
    service._preview_cache = {}
    service.get_job = lambda _job_id: job  # type: ignore[method-assign]

    assert service.full_frame_path(job.id, frame=7) == (
        wide_frames / "frame_000007.png"
    ).resolve()
    assert service.preview_path(job.id, frame=7) == (
        wide_frames / "frame_000007.png"
    ).resolve()
    assert service.full_frame_path(
        job.id,
        frame=7,
        output_variant_id="wide",
    ) == (wide_frames / "frame_000007.png").resolve()
    assert service.preview_path(
        job.id,
        frame=7,
        output_variant_id="wide",
    ) == (wide_frames / "frame_000007.png").resolve()

    for variant_id, frame in (
        ("unknown", 7),
        ("portrait", 7),
        ("wide", 8),
        ("wide", 9),
    ):
        assert service.full_frame_path(
            job.id,
            frame=frame,
            output_variant_id=variant_id,
        ) is None
        assert service.preview_path(
            job.id,
            frame=frame,
            output_variant_id=variant_id,
        ) is None


def _frame_written_event(
    *,
    artifact_relative_path: str,
    width: int = 16,
    height: int = 16,
) -> RendererTelemetryEvent:
    now = datetime.now(UTC)
    return RendererTelemetryEvent(
        schema_version="2.0.0",
        event_type="frame_written",
        sequence=1,
        job_id="variant-artifact-job",
        worker_id="worker-a",
        chunk_id="chunk-1",
        chunk_start=1,
        chunk_end=10,
        frame=7,
        elapsed_seconds=0.25,
        renderer_status="awaiting_chunk_validation",
        act_id="act-a",
        act_name="Synthetic act",
        shot_id="shot-a",
        shot_name="Synthetic shot",
        complexity_class="light",
        project_id="project",
        scene_sha256="A" * 64,
        profile_sha256="a" * 64,
        output_variant_id="wide",
        width=width,
        height=height,
        composition_profile_id="composition-wide",
        artifact_relative_path=artifact_relative_path,
        emitted_at=now,
    )


def test_frame_written_is_validated_inside_its_exact_variant_root_before_preview(
    tmp_path: Path,
) -> None:
    wide_frames = tmp_path / "variants" / "wide" / "frames"
    wide_frames.mkdir(parents=True)
    valid = wide_frames / "frame_000007.png"
    valid.write_bytes(_png(16, 16))
    wrong_root = tmp_path / "variants" / "portrait" / "frames"
    wrong_root.mkdir(parents=True)
    (wrong_root / "frame_000007.png").write_bytes(_png(16, 16))

    job = _artifact_job(tmp_path)
    service = object.__new__(MissionControlService)
    service._preview_cache = {}

    accepted = service.validate_and_prepare_frame_event(
        job,
        _frame_written_event(
            artifact_relative_path="variants/wide/frames/frame_000007.png"
        ),
    )
    assert accepted is True
    assert (
        service._preview_cache[f"{job.id}:wide:7"][1] == valid.resolve()
    )
    assert service._preview_cache[f"{job.id}:wide:latest"][1] == valid.resolve()

    assert service.validate_and_prepare_frame_event(
        job,
        _frame_written_event(
            artifact_relative_path="variants/portrait/frames/frame_000007.png"
        ),
    ) is False
    valid.write_bytes(_png(15, 16))
    assert service.validate_and_prepare_frame_event(
        job,
        _frame_written_event(
            artifact_relative_path="variants/wide/frames/frame_000007.png"
        ),
    ) is False


def _profile_payload(
    scene_path: Path,
    *,
    portrait_enabled: bool,
    include_v2: bool = True,
    singular_v2: bool = False,
) -> dict[str, object]:
    scene_hash = sha256_file(scene_path)
    payload: dict[str, object] = {
        "schemaVersion": "1.1.0",
        "kind": "trackprompt-render-profile",
        "project": "synthetic-project",
        "preset": "synthetic-scene",
        "profileId": "SYNTHETIC-V2-PROFILE",
        "displayName": "Synthetic V2 Profile",
        "approvedScenePath": str(scene_path.resolve()),
        "approvedSceneSha256": scene_hash,
        "authorization": {
            "project": "SYNTHETIC-PROJECT",
            "preset": "SYNTHETIC-SCENE",
            "profile": "SYNTHETIC-V2-PROFILE",
            "status": "pending-operator-approval",
        },
        "timeline": {"frameStart": 1, "frameEnd": 10, "fps": 30},
        "resolution": {"width": 16, "height": 16, "label": "synthetic"},
        "chunking": {"framesPerChunk": 5},
        "production": {
            "resumeEnabled": True,
            "verifyExistingFrames": True,
            "atomicChunkCommit": True,
            "overwriteValidFrames": False,
        },
    }
    if include_v2:
        variants = [
            {
                "id": "wide",
                "enabledByDefault": True,
                "required": True,
                "width": 16,
                "height": 16,
                "fps": 30,
                "compositionMode": "authored",
                "deliverableRole": "primary-master",
                "compositionProfileId": "wide-authored-v1",
                "cameraName": "WIDE_CAMERA",
            },
            {
                "id": "portrait",
                "enabledByDefault": False,
                "required": False,
                "width": 16,
                "height": 32,
                "fps": 30,
                "compositionMode": "authored",
                "deliverableRole": "optional-deliverable",
                "compositionProfileId": "portrait-authored-v1",
                "cameraName": "PORTRAIT_CAMERA",
            },
        ]
        if singular_v2:
            payload["outputVariant"] = {
                "id": "wide",
                "compositionMode": "authored",
                "compositionProfileId": "wide-authored-v1",
                "cameraName": "WIDE_CAMERA",
            }
        else:
            payload["outputVariants"] = variants
            if portrait_enabled:
                payload["enabledOutputVariantIds"] = ["wide", "portrait"]
        payload["shotPlan"] = {
            "shots": [
                {
                    "id": "shot-light",
                    "frameStart": 1,
                    "frameEnd": 4,
                    "complexityClass": "light",
                },
                {
                    "id": "shot-heavy",
                    "frameStart": 5,
                    "frameEnd": 10,
                    "complexityClass": "heavy",
                },
            ]
        }
    return payload


def _runtime_service(
    tmp_path: Path,
    *,
    portrait_enabled: bool,
    include_v2: bool = True,
    singular_v2: bool = False,
) -> MissionControlService:
    profile_root = tmp_path / "profiles"
    profile_root.mkdir(parents=True)
    calibration_root = tmp_path / "calibrations"
    calibration_root.mkdir()
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    scene_path = tmp_path / "synthetic.blend"
    scene_path.write_bytes(b"BLENDER-v300 synthetic scene")
    profile_path = profile_root / "synthetic.json"
    profile_path.write_text(
        json.dumps(
            _profile_payload(
                scene_path,
                portrait_enabled=portrait_enabled,
                include_v2=include_v2,
                singular_v2=singular_v2,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return MissionControlService(
        MissionControlConfig(
            repository_root=Path(__file__).resolve().parents[2],
            state_root=tmp_path / "state",
            profile_root=profile_root,
            calibration_root=calibration_root,
            default_output_root=output_root,
            allow_fake_renderer=True,
            native_dialog_enabled=False,
        )
    )


async def _start_without_renderer_task(
    service: MissionControlService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> JobRecord:
    _profile_path, profile_payload, _profile_hash = service.discovery.profile_source(
        "SYNTHETIC-V2-PROFILE"
    )
    selected_variants = profile_payload.get("enabledOutputVariantIds")
    if not isinstance(selected_variants, list):
        declared = profile_payload.get("outputVariants")
        if isinstance(declared, list):
            selected_variants = [
                value["id"]
                for value in declared
                if isinstance(value, dict)
                and isinstance(value.get("id"), str)
                and (
                    value.get("required") is True
                    or value.get("enabledByDefault") is True
                )
            ]
        else:
            singular = profile_payload.get("outputVariant")
            selected_variants = (
                [singular["id"]]
                if isinstance(singular, dict) and isinstance(singular.get("id"), str)
                else None
            )
    service.authorize_profile(
        "SYNTHETIC-V2-PROFILE",
        "synthetic-scene",
        enabled_output_variant_ids=selected_variants,
        settings_and_hashes_reviewed=True,
        production_render_authorized=True,
    )
    monkeypatch.setattr(
        service.fake_renderer,
        "start",
        lambda *_args, **_kwargs: None,
    )
    output = tmp_path / "selected-output"
    output.mkdir()
    return await service.start_render(
        StartFakeRenderRequest(
            project_id="synthetic-project",
            scene_id="synthetic-scene",
            profile_id="SYNTHETIC-V2-PROFILE",
            output_directory=str(output),
            renderer=RendererKind.FAKE,
            fake=FakeRenderOptions(total_frames=10, frames_per_chunk=5),
        ),
        fake_options=FakeRenderOptions(total_frames=10, frames_per_chunk=5),
    )


@pytest.mark.asyncio
async def test_failed_retry_counts_only_the_active_output_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _runtime_service(tmp_path, portrait_enabled=True)
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        failed = await service.fail_job(
            job.id,
            StructuredError(
                code="synthetic_worker_failure",
                title="Synthetic worker failed",
                summary="The active synthetic worker failed.",
                likely_cause="A deterministic test failure was requested.",
                recommended_action="Retry the exact failed job.",
                retryable=True,
                timestamp=datetime.now(UTC),
                job_id=job.id,
            ),
        )
        assert failed.failure_count == 1
        assert failed.output_variants[0].progress.failure_count == 1
        assert failed.output_variants[1].progress.failure_count == 0

        retried = await service.retry_failed(
            job.id,
            RetryFailedRenderRequest(
                scene_sha256=job.identity.scene_sha256,
                profile_sha256=job.identity.profile_sha256,
                operator_confirmed=True,
            ),
        )

        assert retried.retry_count == 1
        assert retried.failure_count == 1
        assert retried.output_variants[0].progress.retry_count == 1
        assert retried.output_variants[0].progress.failure_count == 1
        assert retried.output_variants[1].progress.retry_count == 0
        assert retried.output_variants[1].progress.failure_count == 0
    finally:
        service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("portrait_enabled", [False, True])
async def test_start_materializes_and_persists_exact_v2_output_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    portrait_enabled: bool,
) -> None:
    monkeypatch.setattr(service_module, "_physical_memory_bytes", lambda: 8 * 1024**3)
    service = _runtime_service(
        tmp_path,
        portrait_enabled=portrait_enabled,
    )
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        expected_enabled = (
            ("wide", "portrait") if portrait_enabled else ("wide",)
        )

        assert job.identity.output_variant_id == "wide"
        assert job.active_variant_id == "wide"
        assert tuple(
            variant.id for variant in job.output_variants if variant.enabled
        ) == expected_enabled
        assert job.output_variants[0].progress.total_frames == 10
        assert job.output_variants[1].progress.total_frames == (
            10 if portrait_enabled else 0
        )
        if portrait_enabled:
            assert job.output_variants[1].progress.stages
        else:
            assert job.output_variants[1].progress.stages == ()
        assert {stage.stage for stage in job.stages} == set(RenderStage)
        assert job.workers[0].worker_id == "local-render-worker"
        assert job.aggregate_eta is not None
        assert job.aggregate_eta.enabled_variant_ids == expected_enabled
        assert job.aggregate_eta.p50_remaining_seconds is None
        assert job.aggregate_eta.p90_remaining_seconds is None

        canonical = service.store.get_media_render_job(job.id)
        assert canonical is not None
        assert canonical.output_matrix.enabled_variant_ids == expected_enabled
        assert canonical.output_matrix.variant_sha256_by_id == {
            variant.id: variant.output_variant_sha256
            for variant in canonical.output_variants
            if variant.enabled
        }
    finally:
        service.close()


@pytest.mark.asyncio
async def test_frame_updates_use_remaining_shot_complexity_and_persist_canonical_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "_physical_memory_bytes", lambda: 8 * 1024**3)
    service = _runtime_service(tmp_path, portrait_enabled=False)
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        first = await service.update_job(
            job.id,
            renderer_event_type="frame_written",
            rendered_frame_count=1,
            latest_rendered_frame=1,
            current_seconds_per_frame=2.0,
            current_complexity_class="light",
            worker_id="local-render-worker",
        )

        assert first.aggregate_eta is not None
        estimates = first.aggregate_eta.variant_forecasts[0].estimates
        assert {
            estimate.complexity_class: estimate.remaining_units
            for estimate in estimates
        } == {"heavy": 6, "light": 3}
        assert first.stages[-1].eta_p50_seconds is None

        second = await service.update_job(
            job.id,
            renderer_event_type="frame_written",
            rendered_frame_count=5,
            latest_rendered_frame=5,
            current_seconds_per_frame=4.0,
            current_complexity_class="heavy",
            worker_id="local-render-worker",
        )
        assert second.aggregate_eta is not None
        assert second.aggregate_eta.p50_remaining_seconds == pytest.approx(20)
        assert second.aggregate_eta.p90_remaining_seconds == pytest.approx(20)
        assert second.stages[-1].eta_p50_seconds == pytest.approx(20)

        canonical = service.store.get_media_render_job(job.id)
        assert canonical is not None
        assert canonical.updated_at == second.updated_at
        assert canonical.output_variants[0].progress.rendered_frames == 5
        assert canonical.output_variants[0].progress.latest_rendered_frame == 5
    finally:
        service.close()


def _scheduler_cpu_worker(
    worker_id: str,
    *,
    max_width: int = 4096,
    max_height: int = 4096,
) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_id=worker_id,
        kinds=(WorkerKind.LOCAL_CPU,),
        logical_cpu_count=8,
        memory_bytes=8 * 1024**3,
        max_concurrent_tasks=1,
        max_width=max_width,
        max_height=max_height,
        supported_artifact_formats=("png",),
    )


@pytest.mark.asyncio
async def test_scheduler_integration_horizontal_only_has_no_phantom_variant_and_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _runtime_service(tmp_path, portrait_enabled=False)
    restarted: MissionControlService | None = None
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        tasks = service.scheduled_render_tasks(job.id)
        assert len(tasks) == 3
        assert {task.task.output_variant_id for task in tasks} == {"wide"}
        assert sum(task.task.frame_count for task in tasks) == 10
        assert all(task.task.shot_id in {"shot-light", "shot-heavy"} for task in tasks)

        persisted = service.get_job(job.id)
        assert persisted.chunks_total == 3
        assert persisted.output_variants[1].enabled is False
        assert persisted.output_variants[1].progress.total_frames == 0
        assert persisted.output_variants[1].progress.stages == ()

        base = datetime.now(UTC)
        service.register_render_worker(
            _scheduler_cpu_worker("too-small", max_width=8),
            now=base,
            heartbeat_timeout=timedelta(minutes=1),
        )
        assert (
            await service.claim_render_task(
                "too-small",
                job_id=job.id,
                now=base,
            )
            is None
        )
        service.register_render_worker(
            _scheduler_cpu_worker("capable-worker"),
            now=base,
            heartbeat_timeout=timedelta(minutes=1),
        )
        grant = await service.claim_render_task(
            "capable-worker",
            job_id=job.id,
            now=base,
            lease_duration=timedelta(minutes=1),
        )
        assert grant is not None
        assert grant.task.output_variant_id == "wide"
        config = service.config
        service.close()

        restarted = MissionControlService(config)
        restored_tasks = restarted.scheduled_render_tasks(job.id)
        restored = next(task for task in restored_tasks if task.task.id == grant.task.id)
        assert restored.state is TaskState.LEASED
        assert restored.leased_worker_id == "capable-worker"
        restored_job = restarted.get_job(job.id)
        assert restored_job.state is JobState.RUNNING
        assert restored_job.output_variants[1].progress.stages == ()
        assert (
            restarted.store.get_media_render_job(job.id)
            .output_matrix.enabled_variant_ids
            == ("wide",)
        )
    finally:
        if restarted is not None:
            restarted.close()
        elif not service._closed:
            service.close()


@pytest.mark.asyncio
async def test_scheduler_integration_heartbeat_worker_loss_and_retry_are_persistent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _runtime_service(tmp_path, portrait_enabled=False)
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        base = datetime.now(UTC)
        service.register_render_worker(
            _scheduler_cpu_worker("worker-a"),
            now=base,
            heartbeat_timeout=timedelta(seconds=5),
        )
        first = await service.claim_render_task(
            "worker-a",
            job_id=job.id,
            now=base,
            lease_duration=timedelta(seconds=30),
        )
        assert first is not None
        await service.start_scheduled_render_task(
            first.lease.id,
            "worker-a",
            first.lease_token.get_secret_value(),
            now=base + timedelta(seconds=1),
        )
        renewed = await service.heartbeat_scheduled_render_task(
            first.lease.id,
            "worker-a",
            first.lease_token.get_secret_value(),
            now=base + timedelta(seconds=4),
            lease_duration=timedelta(seconds=10),
            worker_timeout=timedelta(seconds=10),
        )
        assert renewed.expires_at == base + timedelta(seconds=14)
        assert (
            await service.reap_render_scheduler(
                now=base + timedelta(seconds=6)
            )
        ).lost_worker_ids == ()

        sequence_before_loss = service.store.latest_event_sequence()
        lost = await service.reap_render_scheduler(
            now=base + timedelta(seconds=15)
        )
        assert lost.lost_worker_ids == ("worker-a",)
        assert lost.requeued_task_ids == (first.task.id,)
        requeued = service.scheduler.get_task(first.task.id)
        assert requeued is not None
        assert requeued.state is TaskState.PENDING
        assert requeued.attempt == 2
        after_loss = service.get_job(job.id)
        assert after_loss.state is JobState.RESUMABLE
        assert after_loss.output_variants[0].progress.retry_count == 1
        assert service.store.events_after(sequence_before_loss, job_id=job.id)[
            -1
        ].renderer_event_type == "scheduler_worker_lost"

        service.register_render_worker(
            _scheduler_cpu_worker("worker-b"),
            now=base + timedelta(seconds=15),
            heartbeat_timeout=timedelta(minutes=1),
        )
        second = await service.claim_render_task(
            "worker-b",
            job_id=job.id,
            now=base + timedelta(seconds=15),
        )
        assert second is not None
        assert second.task.id == first.task.id
        assert second.task.attempt == 2
        with pytest.raises(LeaseRejectedError, match="no longer active"):
            await service.complete_scheduled_render_task(
                first.lease.id,
                "worker-a",
                first.lease_token.get_secret_value(),
                now=base + timedelta(seconds=16),
            )
        completed = await service.complete_scheduled_render_task(
            second.lease.id,
            "worker-b",
            second.lease_token.get_secret_value(),
            now=base + timedelta(seconds=16),
        )
        assert completed.state is TaskState.COMPLETE
    finally:
        service.close()


@pytest.mark.asyncio
async def test_scheduler_integration_dual_variant_tracks_exact_aggregate_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _runtime_service(tmp_path, portrait_enabled=True)
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        tasks = service.scheduled_render_tasks(job.id)
        assert len(tasks) == 6
        assert {
            variant_id: sum(
                task.task.frame_count
                for task in tasks
                if task.task.output_variant_id == variant_id
            )
            for variant_id in ("wide", "portrait")
        } == {"wide": 10, "portrait": 10}
        assert service.get_job(job.id).chunks_total == 6

        base = datetime.now(UTC)
        service.register_render_worker(
            _scheduler_cpu_worker("matrix-worker"),
            now=base,
            heartbeat_timeout=timedelta(minutes=5),
        )
        observed_variants: set[str] = set()
        for offset in range(6):
            timestamp = base + timedelta(seconds=offset)
            grant = await service.claim_render_task(
                "matrix-worker",
                job_id=job.id,
                now=timestamp,
            )
            assert grant is not None
            observed_variants.add(grant.task.output_variant_id)
            await service.complete_scheduled_render_task(
                grant.lease.id,
                "matrix-worker",
                grant.lease_token.get_secret_value(),
                now=timestamp + timedelta(milliseconds=100),
            )

        assert observed_variants == {"wide", "portrait"}
        completed_job = service.get_job(job.id)
        assert completed_job.state is JobState.VERIFYING
        assert completed_job.chunks_completed == 6
        assert completed_job.aggregate_eta is not None
        assert completed_job.aggregate_eta.enabled_variant_ids == (
            "wide",
            "portrait",
        )
        for variant in completed_job.output_variants:
            rendering = next(
                stage
                for stage in variant.progress.stages
                if stage.stage is RenderStage.RENDERING
            )
            assert rendering.state.value == "complete"
            assert rendering.completed_units == 10
            assert rendering.total_units == 10
            # Scheduler completion never fabricates safe-frame publication.
            assert variant.progress.rendered_frames == 0
            assert variant.progress.validated_frames == 0
    finally:
        service.close()


@pytest.mark.asyncio
async def test_variant_events_update_independent_progress_eta_and_event_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "_physical_memory_bytes", lambda: 8 * 1024**3)
    service = _runtime_service(tmp_path, portrait_enabled=True)
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        wide = await service.update_job(
            job.id,
            output_variant_id="wide",
            renderer_event_type="frame_written",
            rendered_frame_count=1,
            latest_rendered_frame=1,
            current_seconds_per_frame=2.0,
            current_complexity_class="light",
            worker_id="local-render-worker",
        )
        portrait = await service.update_job(
            job.id,
            output_variant_id="portrait",
            renderer_event_type="frame_written",
            rendered_frame_count=5,
            latest_rendered_frame=5,
            current_seconds_per_frame=4.0,
            current_complexity_class="heavy",
            worker_id="local-render-worker",
        )

        assert wide.active_variant_id == "wide"
        assert portrait.active_variant_id == "portrait"
        progress = {
            variant.id: variant.progress for variant in portrait.output_variants
        }
        assert progress["wide"].rendered_frames == 1
        assert progress["portrait"].rendered_frames == 5
        assert portrait.aggregate_eta is not None
        forecasts = {
            forecast.output_variant_id: forecast
            for forecast in portrait.aggregate_eta.variant_forecasts
        }
        assert {
            estimate.complexity_class: estimate.remaining_units
            for estimate in forecasts["wide"].estimates
        } == {"heavy": 6, "light": 3}
        assert {
            estimate.complexity_class: estimate.remaining_units
            for estimate in forecasts["portrait"].estimates
        } == {"heavy": 5}

        event = service.events_after(0, job_id=job.id)[-1]
        assert event.output_variant_id == "portrait"
        assert event.output_width == 16
        assert event.output_height == 32
        assert event.composition_profile_id == "portrait-authored-v1"
        canonical = service.store.get_media_render_job(job.id)
        assert canonical is not None
        assert canonical.output_variants[0].progress.rendered_frames == 1
        assert canonical.output_variants[1].progress.rendered_frames == 5

        with pytest.raises(MissionControlError) as error:
            await service.update_job(
                job.id,
                output_variant_id="unknown",
                renderer_event_type="frame_written",
                rendered_frame_count=1,
                latest_rendered_frame=1,
                current_seconds_per_frame=1.0,
            )
        assert error.value.error.code == "render_event_variant_mismatch"
    finally:
        service.close()


@pytest.mark.asyncio
async def test_singular_output_variant_profile_materializes_one_entry_v2_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "_physical_memory_bytes", lambda: 8 * 1024**3)
    service = _runtime_service(
        tmp_path,
        portrait_enabled=False,
        singular_v2=True,
    )
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)

        assert job.identity.output_variant_id == "wide"
        assert job.identity.output_width == 16
        assert job.identity.output_height == 16
        assert job.active_variant_id == "wide"
        assert len(job.output_variants) == 1
        assert job.output_variants[0].enabled is True
        assert job.output_variants[0].required is True
        assert job.output_variants[0].composition_profile.id == "wide-authored-v1"
        canonical = service.store.get_media_render_job(job.id)
        assert canonical is not None
        assert canonical.output_matrix.enabled_variant_ids == ("wide",)
    finally:
        service.close()


@pytest.mark.asyncio
async def test_legacy_profile_keeps_legacy_job_without_phantom_v2_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _runtime_service(
        tmp_path,
        portrait_enabled=False,
        include_v2=False,
    )
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        assert job.identity.output_variant_id == "primary"
        assert job.output_variants == ()
        assert job.active_variant_id is None
        assert job.aggregate_eta is None
        assert service.store.get_media_render_job(job.id) is None
        updated = await service.update_job(
            job.id,
            renderer_event_type="frame_written",
            rendered_frame_count=1,
            latest_rendered_frame=1,
            current_seconds_per_frame=2.0,
            current_complexity_class="legacy",
            worker_id="local-render-worker",
        )
        render_stage = next(
            stage
            for stage in updated.stages
            if stage.stage is RenderStage.RENDERING
        )
        assert render_stage.eta_p50_seconds == pytest.approx(18)
        assert updated.aggregate_eta is None
    finally:
        service.close()


class _SyntheticProcessStream:
    def __init__(self, chunks: list[bytes], *, delay_seconds: float = 0) -> None:
        self._chunks = chunks
        self._delay_seconds = delay_seconds

    async def read(self, _count: int = -1) -> bytes:
        if self._delay_seconds:
            delay = self._delay_seconds
            self._delay_seconds = 0
            await asyncio.sleep(delay)
        return self._chunks.pop(0) if self._chunks else b""


class _SyntheticLocalProcess:
    def __init__(
        self,
        exit_code: int,
        *,
        duration_seconds: float = 0,
        output: bytes = b"",
    ) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.stdout = _SyntheticProcessStream(
            [output] if output else [],
            delay_seconds=min(duration_seconds / 2, 0.02),
        )
        self._exit_code = exit_code
        self._duration_seconds = duration_seconds
        self.killed = False

    async def wait(self) -> int:
        if self.returncode is None:
            await asyncio.sleep(self._duration_seconds)
            self.returncode = -9 if self.killed else self._exit_code
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _task_command_builder(tmp_path: Path) -> TrackPromptTaskCommandBuilder:
    package = tmp_path / "synthetic-package"
    package.mkdir()
    blender = tmp_path / "synthetic-blender.exe"
    blender.write_bytes(b"synthetic executable placeholder")
    worker_script = tmp_path / "synthetic-worker.py"
    worker_script.write_text("# synthetic worker placeholder\n", encoding="utf-8")
    return TrackPromptTaskCommandBuilder(
        package_directory=package,
        blender_executable=blender,
        artifact_root=tmp_path / "artifacts",
        worker_script=worker_script,
        python_executable=Path(sys.executable),
        inner_timeout_seconds=60,
        max_log_bytes=1024,
    )


@pytest.mark.asyncio
async def test_local_subprocess_adapter_uses_exec_identity_and_persistent_heartbeats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _runtime_service(tmp_path, portrait_enabled=False)
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(
        *arguments: str,
        **keyword_arguments: object,
    ) -> _SyntheticLocalProcess:
        captured["arguments"] = arguments
        captured["keywords"] = keyword_arguments
        return _SyntheticLocalProcess(
            0,
            duration_seconds=0.06,
            output=b"bounded synthetic output",
        )

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        sequence = service.store.latest_event_sequence()
        adapter = LocalSubprocessRenderWorker(
            service,
            _scheduler_cpu_worker("subprocess-worker"),
            _task_command_builder(tmp_path),
            heartbeat_interval_seconds=0.01,
            lease_duration=timedelta(seconds=0.1),
            worker_timeout=timedelta(seconds=0.1),
            task_timeout_seconds=1,
            max_captured_output_bytes=8,
        )

        result = await adapter.run_once(job_id=job.id)

        assert result is not None
        assert result.status is LocalWorkerRunStatus.COMPLETED
        assert result.exit_code == 0
        assert result.captured_output_bytes == 8
        assert result.output_truncated is True
        scheduled = service.scheduler.get_task(result.task_id)
        assert scheduled is not None
        assert scheduled.state is TaskState.COMPLETE

        arguments = captured["arguments"]
        keywords = captured["keywords"]
        assert isinstance(arguments, tuple)
        assert isinstance(keywords, dict)
        assert arguments[0] == str(Path(sys.executable).resolve())
        assert "--start" in arguments
        assert "--end" in arguments
        assert "--output-directory" in arguments
        assert "shell" not in keywords
        environment = keywords["env"]
        assert isinstance(environment, dict)
        assert environment["TRACKPROMPT_RENDER_TASK_SHA256"] == result.task_sha256
        assert environment["TRACKPROMPT_WORKER_LEASE_ID"] == result.lease_id
        assert all("TOKEN" not in key for key in environment)

        events = service.store.events_after(sequence, job_id=job.id)
        event_types = [event.renderer_event_type for event in events]
        assert "scheduler_task_started" in event_types
        assert "scheduler_heartbeat" in event_types
        assert event_types[-1] == "scheduler_task_completed"
    finally:
        service.close()


@pytest.mark.asyncio
async def test_local_subprocess_adapter_nonzero_exit_requeues_same_task_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _runtime_service(tmp_path, portrait_enabled=False)
    commands: list[tuple[str, ...]] = []
    exit_codes = [7, 0]

    async def process_factory(
        arguments: Sequence[str],
        _environment: Mapping[str, str],
        _working_directory: Path,
    ) -> _SyntheticLocalProcess:
        commands.append(tuple(arguments))
        return _SyntheticLocalProcess(exit_codes.pop(0))

    try:
        job = await _start_without_renderer_task(service, tmp_path, monkeypatch)
        adapter = LocalSubprocessRenderWorker(
            service,
            _scheduler_cpu_worker("retrying-subprocess-worker"),
            _task_command_builder(tmp_path),
            process_factory=process_factory,
            heartbeat_interval_seconds=0.01,
            lease_duration=timedelta(seconds=0.1),
            worker_timeout=timedelta(seconds=0.1),
            task_timeout_seconds=1,
        )

        first = await adapter.run_once(job_id=job.id)
        second = await adapter.run_once(job_id=job.id)

        assert first is not None
        assert first.status is LocalWorkerRunStatus.REQUEUED
        assert first.exit_code == 7
        assert first.attempt == 2
        assert second is not None
        assert second.status is LocalWorkerRunStatus.COMPLETED
        assert second.task_id == first.task_id
        assert second.task_sha256 == first.task_sha256
        assert second.attempt == 2
        first_output = commands[0][commands[0].index("--output-directory") + 1]
        second_output = commands[1][commands[1].index("--output-directory") + 1]
        assert "attempt-001" in first_output
        assert "attempt-002" in second_output
        assert first_output != second_output
    finally:
        service.close()

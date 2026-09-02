from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.mission_control.render_contracts import (
    GpuCapability,
    ShotRenderTask,
    TaskState,
    WorkerCapabilities,
    WorkerKind,
)
from app.mission_control.scheduler import (
    LeaseRejectedError,
    PersistentRenderScheduler,
    SchedulerConflictError,
    SchedulerReapResult,
    SchedulerWorkerStatus,
    TaskIdentityError,
    TaskResourceRequirements,
    deterministic_task_sha256,
    seal_task_identity,
)

NOW = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
GIB = 1024**3


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fixture() -> dict[str, object]:
    path = Path(__file__).parent / "fixtures" / "synthetic_render_scheduler.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _task(
    variant: dict[str, object],
    *,
    chunk: int = 1,
    worker_kind: WorkerKind | None = None,
    minimum_gpu_memory_bytes: int | None = None,
) -> ShotRenderTask:
    variant_id = str(variant["id"])
    draft = ShotRenderTask(
        id="unsealed-task",
        job_id="synthetic-gallery-job",
        output_variant_id=variant_id,
        shot_id="product-reveal",
        chunk_id=f"chunk-{chunk:02d}",
        frame_start=int(variant["frameStart"]) + ((chunk - 1) * 4),
        frame_end=min(
            int(variant["frameEnd"]),
            int(variant["frameStart"]) + (chunk * 4) - 1,
        ),
        width=int(variant["width"]),
        height=int(variant["height"]),
        fps=24,
        complexity_class="synthetic-reflective-product",
        package_sha256=str(_fixture()["packageSha256"]),
        matrix_sha256=str(_fixture()["matrixSha256"]),
        output_variant_sha256=_digest(f"variant-{variant_id}"),
        scene_sha256=_digest("synthetic-gallery-scene"),
        render_profile_sha256=_digest(f"profile-{variant_id}"),
        composition_sha256=str(variant["compositionSha256"]),
        task_sha256="0" * 64,
        output_root=(
            f"jobs/synthetic-gallery-job/variants/{variant_id}/chunks/chunk-{chunk:02d}"
        ),
        required_worker_kind=worker_kind,
        minimum_gpu_memory_bytes=minimum_gpu_memory_bytes,
    )
    return seal_task_identity(draft)


def _cpu_worker(
    worker_id: str,
    *,
    memory_bytes: int = 16 * GIB,
    concurrency: int = 2,
) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_id=worker_id,
        kinds=(WorkerKind.LOCAL_CPU,),
        logical_cpu_count=8,
        memory_bytes=memory_bytes,
        max_concurrent_tasks=concurrency,
        max_width=4096,
        max_height=4096,
        supported_artifact_formats=("png",),
    )


def _gpu_worker(
    worker_id: str,
    device_id: str,
    *,
    gpu_memory_bytes: int,
    concurrency: int = 3,
) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_id=worker_id,
        kinds=(WorkerKind.LOCAL_GPU,),
        logical_cpu_count=8,
        memory_bytes=32 * GIB,
        gpus=(
            GpuCapability(
                device_id=device_id,
                name="Synthetic GPU",
                memory_bytes=gpu_memory_bytes,
                render_engines=("synthetic-cycles",),
            ),
        ),
        max_concurrent_tasks=concurrency,
        max_width=4096,
        max_height=4096,
        supported_artifact_formats=("png",),
    )


def _token(grant_token: SecretStr) -> str:
    return grant_token.get_secret_value()


def test_task_identity_is_deterministic_and_submission_is_idempotent(
    tmp_path: Path,
) -> None:
    variant = _fixture()["variants"][0]
    assert isinstance(variant, dict)
    task = _task(variant)
    retry_copy = task.model_copy(update={"attempt": 99})

    assert deterministic_task_sha256(retry_copy) == task.task_sha256
    assert seal_task_identity(retry_copy) == task

    scheduler = PersistentRenderScheduler(tmp_path / "mission-control.sqlite3")
    try:
        first = scheduler.submit_task(task, now=NOW)
        duplicate = scheduler.submit_task(task, now=NOW + timedelta(seconds=1))
        assert first == duplicate
        assert len(scheduler.list_tasks()) == 1

        unsealed = task.model_copy(update={"id": "caller-chosen-id"})
        with pytest.raises(TaskIdentityError, match="seal_task_identity"):
            scheduler.submit_task(unsealed, now=NOW)
    finally:
        scheduler.close()


def test_gpu_matching_enforces_memory_capability_and_one_worker_per_device(
    tmp_path: Path,
) -> None:
    fixture = _fixture()
    wide = fixture["variants"][0]
    square = fixture["variants"][1]
    assert isinstance(wide, dict)
    assert isinstance(square, dict)
    scheduler = PersistentRenderScheduler(tmp_path / "mission-control.sqlite3")
    try:
        scheduler.submit_task(
            _task(
                wide,
                worker_kind=WorkerKind.LOCAL_GPU,
                minimum_gpu_memory_bytes=8 * GIB,
            ),
            requirements=TaskResourceRequirements(
                memory_bytes=4 * GIB,
                gpu_memory_bytes=8 * GIB,
                required_artifact_format="png",
            ),
            now=NOW,
        )
        scheduler.submit_task(
            _task(
                square,
                worker_kind=WorkerKind.LOCAL_GPU,
                minimum_gpu_memory_bytes=8 * GIB,
            ),
            requirements=TaskResourceRequirements(
                memory_bytes=4 * GIB,
                gpu_memory_bytes=8 * GIB,
                required_artifact_format="png",
            ),
            now=NOW,
        )
        scheduler.register_worker(
            _gpu_worker("gpu-small", "synthetic-device-small", gpu_memory_bytes=6 * GIB),
            now=NOW,
        )
        assert scheduler.claim_next_task("gpu-small", now=NOW) is None

        scheduler.register_worker(
            _gpu_worker("gpu-large", "synthetic-device-large", gpu_memory_bytes=12 * GIB),
            now=NOW,
        )
        with pytest.raises(SchedulerConflictError, match="already belongs"):
            scheduler.register_worker(
                _gpu_worker(
                    "duplicate-device-worker",
                    "synthetic-device-large",
                    gpu_memory_bytes=12 * GIB,
                ),
                now=NOW,
            )

        first = scheduler.claim_next_task("gpu-large", now=NOW)
        assert first is not None
        assert first.task.output_variant_id in {"gallery-wide", "square-social"}
        # A GPU worker is one process per device even if it advertises concurrency 3.
        assert scheduler.claim_next_task("gpu-large", now=NOW) is None
    finally:
        scheduler.close()


def test_cpu_concurrency_reserves_memory_and_tracks_changing_worker_count(
    tmp_path: Path,
) -> None:
    variants = _fixture()["variants"]
    assert isinstance(variants, list)
    scheduler = PersistentRenderScheduler(tmp_path / "mission-control.sqlite3")
    try:
        for variant in variants:
            assert isinstance(variant, dict)
            scheduler.submit_task(
                _task(variant),
                requirements=TaskResourceRequirements(
                    memory_bytes=6 * GIB,
                    required_artifact_format="png",
                ),
                now=NOW,
            )
        scheduler.register_worker(
            _cpu_worker("cpu-a", memory_bytes=10 * GIB, concurrency=3),
            now=NOW,
        )
        assert scheduler.active_worker_count(now=NOW) == 1
        first = scheduler.claim_next_task("cpu-a", now=NOW)
        assert first is not None
        # Memory reservation, not the nominal concurrency, is the limiting factor.
        assert scheduler.claim_next_task("cpu-a", now=NOW) is None

        scheduler.register_worker(
            _cpu_worker("cpu-b", memory_bytes=16 * GIB, concurrency=2),
            now=NOW,
        )
        assert scheduler.active_worker_count(now=NOW) == 2
        second = scheduler.claim_next_task("cpu-b", now=NOW)
        assert second is not None
        assert second.task.output_variant_id == "square-social"

        scheduler.retire_worker("cpu-a", now=NOW + timedelta(seconds=1))
        assert scheduler.active_worker_count(now=NOW + timedelta(seconds=1)) == 1
        reassigned = scheduler.claim_next_task(
            "cpu-b",
            now=NOW + timedelta(seconds=1),
        )
        assert reassigned is not None
        assert reassigned.task.id == first.task.id
        assert reassigned.task.attempt == 2
        assert {
            task.task.output_variant_id for task in scheduler.list_tasks()
        } == {"gallery-wide", "square-social", "portrait-poster"}
    finally:
        scheduler.close()


def test_worker_loss_reassigns_retry_safely_and_survives_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mission-control.sqlite3"
    variant = _fixture()["variants"][0]
    assert isinstance(variant, dict)
    task = _task(variant)

    first_scheduler = PersistentRenderScheduler(
        database,
        default_worker_timeout=timedelta(seconds=10),
        default_lease_duration=timedelta(minutes=5),
    )
    first_scheduler.submit_task(task, now=NOW)
    first_scheduler.register_worker(_cpu_worker("cpu-a"), now=NOW)
    first_grant = first_scheduler.claim_next_task("cpu-a", now=NOW)
    assert first_grant is not None
    first_scheduler.start_task(
        first_grant.lease.id,
        "cpu-a",
        _token(first_grant.lease_token),
        now=NOW + timedelta(seconds=1),
    )
    first_scheduler.close()

    second_scheduler = PersistentRenderScheduler(
        database,
        default_worker_timeout=timedelta(seconds=10),
        default_lease_duration=timedelta(minutes=5),
    )
    try:
        loss_time = NOW + timedelta(seconds=11)
        reaped = second_scheduler.reap_expired(now=loss_time)
        assert reaped.lost_worker_ids == ("cpu-a",)
        assert reaped.expired_lease_ids == (first_grant.lease.id,)
        assert reaped.requeued_task_ids == (task.id,)
        assert second_scheduler.list_workers(now=loss_time)[0].status is (
            SchedulerWorkerStatus.LOST
        )

        second_scheduler.register_worker(_cpu_worker("cpu-b"), now=loss_time)
        second_grant = second_scheduler.claim_next_task("cpu-b", now=loss_time)
        assert second_grant is not None
        assert second_grant.task.id == task.id
        assert second_grant.task.task_sha256 == task.task_sha256
        assert second_grant.task.attempt == 2

        with pytest.raises(LeaseRejectedError, match="no longer active"):
            second_scheduler.complete_task(
                first_grant.lease.id,
                "cpu-a",
                _token(first_grant.lease_token),
                now=loss_time,
            )

        completed = second_scheduler.complete_task(
            second_grant.lease.id,
            "cpu-b",
            _token(second_grant.lease_token),
            now=loss_time + timedelta(seconds=1),
        )
        assert completed.state is TaskState.COMPLETE
        # Completion is idempotent for the same authenticated lease.
        assert (
            second_scheduler.complete_task(
                second_grant.lease.id,
                "cpu-b",
                _token(second_grant.lease_token),
                now=loss_time + timedelta(seconds=2),
            )
            == completed
        )
    finally:
        second_scheduler.close()

    final_scheduler = PersistentRenderScheduler(database)
    try:
        restored = final_scheduler.get_task(task.id)
        assert restored is not None
        assert restored.state is TaskState.COMPLETE
        assert restored.attempt == 2
        assert restored.completed_lease_id is not None
    finally:
        final_scheduler.close()


def test_lease_heartbeat_extends_both_lease_and_worker_liveness(
    tmp_path: Path,
) -> None:
    variant = _fixture()["variants"][0]
    assert isinstance(variant, dict)
    scheduler = PersistentRenderScheduler(
        tmp_path / "mission-control.sqlite3",
        default_worker_timeout=timedelta(seconds=5),
        default_lease_duration=timedelta(seconds=5),
    )
    try:
        scheduler.submit_task(_task(variant), now=NOW)
        scheduler.register_worker(_cpu_worker("cpu-a"), now=NOW)
        grant = scheduler.claim_next_task("cpu-a", now=NOW)
        assert grant is not None
        heartbeat_at = NOW + timedelta(seconds=4)
        renewed = scheduler.heartbeat_lease(
            grant.lease.id,
            "cpu-a",
            _token(grant.lease_token),
            now=heartbeat_at,
            lease_duration=timedelta(seconds=10),
            worker_timeout=timedelta(seconds=10),
        )
        assert renewed.expires_at == heartbeat_at + timedelta(seconds=10)
        assert scheduler.reap_expired(
            now=NOW + timedelta(seconds=6)
        ) == SchedulerReapResult()
        scheduled = scheduler.get_task(grant.task.id)
        assert scheduled is not None
        assert scheduled.state is TaskState.LEASED
    finally:
        scheduler.close()

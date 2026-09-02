from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Self, cast

from pydantic import Field, SecretStr, model_validator

from .render_contracts import (
    Identifier,
    ImmutableRenderContractModel,
    ShotRenderTask,
    TaskState,
    WorkerCapabilities,
    WorkerKind,
    WorkerLease,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler timestamps must include a timezone")
    return value.astimezone(UTC)


def _positive_duration(value: timedelta, label: str) -> timedelta:
    if value <= timedelta(0):
        raise ValueError(f"{label} must be positive")
    return value


def deterministic_task_sha256(task: ShotRenderTask) -> str:
    """Hash the immutable work identity while excluding scheduler-owned attempts."""
    payload = task.model_dump(
        mode="json",
        by_alias=True,
        exclude={"id", "task_sha256", "attempt"},
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seal_task_identity(task: ShotRenderTask) -> ShotRenderTask:
    """Return a task whose ID and hash are derived only from immutable work fields."""
    digest = deterministic_task_sha256(task)
    return task.model_copy(
        update={
            "id": f"render-task-{digest[:40]}",
            "task_sha256": digest,
            "attempt": 1,
        }
    )


class SchedulerError(RuntimeError):
    """Base class for safe scheduler failures."""


class TaskIdentityError(SchedulerError):
    """Raised when a task does not carry its deterministic identity."""


class SchedulerConflictError(SchedulerError):
    """Raised when a persistent identity collides with different data."""


class WorkerUnavailableError(SchedulerError):
    """Raised when a worker is unknown, stale, retired, or over capacity."""


class LeaseRejectedError(SchedulerError):
    """Raised when a lease is stale, expired, or cannot be authenticated."""


class SchedulerWorkerStatus(StrEnum):
    ACTIVE = "active"
    LOST = "lost"
    RETIRED = "retired"


class TaskResourceRequirements(ImmutableRenderContractModel):
    """Placement-only reservations; these do not alter rendered output identity."""

    memory_bytes: int = Field(default=0, ge=0)
    gpu_memory_bytes: int = Field(default=0, ge=0)
    required_artifact_format: Identifier | None = None


class SchedulerWorker(ImmutableRenderContractModel):
    capabilities: WorkerCapabilities
    status: SchedulerWorkerStatus
    registered_at: datetime
    last_heartbeat_at: datetime
    heartbeat_expires_at: datetime
    active_lease_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_heartbeat_window(self) -> Self:
        if self.heartbeat_expires_at <= self.last_heartbeat_at:
            raise ValueError("worker heartbeat expiry must follow its last heartbeat")
        if self.last_heartbeat_at < self.registered_at:
            raise ValueError("worker heartbeat cannot precede registration")
        return self


class ScheduledRenderTask(ImmutableRenderContractModel):
    task: ShotRenderTask
    requirements: TaskResourceRequirements
    state: TaskState
    attempt: int = Field(ge=1)
    active_lease_id: Identifier | None = None
    leased_worker_id: Identifier | None = None
    completed_lease_id: Identifier | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_lease_state(self) -> Self:
        if self.state in {TaskState.LEASED, TaskState.RUNNING}:
            if self.active_lease_id is None or self.leased_worker_id is None:
                raise ValueError("leased tasks require an active lease and worker")
        elif self.active_lease_id is not None or self.leased_worker_id is not None:
            raise ValueError("only leased or running tasks may retain an active lease")
        if self.state is TaskState.COMPLETE and self.completed_lease_id is None:
            raise ValueError("completed tasks require their completing lease identity")
        return self


class LeaseGrant(ImmutableRenderContractModel):
    task: ShotRenderTask
    lease: WorkerLease
    lease_token: SecretStr


class SchedulerReapResult(ImmutableRenderContractModel):
    lost_worker_ids: tuple[Identifier, ...] = ()
    expired_lease_ids: tuple[Identifier, ...] = ()
    requeued_task_ids: tuple[Identifier, ...] = ()


class PersistentRenderScheduler:
    """Content-neutral, restart-safe render task placement in Mission Control SQLite."""

    def __init__(
        self,
        database_path: Path,
        *,
        default_lease_duration: timedelta = timedelta(seconds=60),
        default_worker_timeout: timedelta = timedelta(seconds=30),
    ) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self.default_lease_duration = _positive_duration(
            default_lease_duration,
            "default lease duration",
        )
        self.default_worker_timeout = _positive_duration(
            default_worker_timeout,
            "default worker timeout",
        )
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            database_path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS render_scheduler_workers (
                    worker_id TEXT PRIMARY KEY,
                    capabilities_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    last_heartbeat_at TEXT NOT NULL,
                    heartbeat_expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS render_scheduler_worker_devices (
                    device_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL UNIQUE,
                    FOREIGN KEY(worker_id)
                        REFERENCES render_scheduler_workers(worker_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS render_scheduler_tasks (
                    task_sha256 TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    task_json TEXT NOT NULL,
                    requirements_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    active_lease_id TEXT,
                    leased_worker_id TEXT,
                    completed_lease_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS render_scheduler_tasks_pending
                ON render_scheduler_tasks(state, created_at, task_sha256);

                CREATE TABLE IF NOT EXISTS render_scheduler_leases (
                    lease_id TEXT PRIMARY KEY,
                    task_sha256 TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    output_variant_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    device_id TEXT,
                    attempt INTEGER NOT NULL,
                    token_sha256 TEXT NOT NULL,
                    granted_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_heartbeat_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    ended_at TEXT,
                    end_reason TEXT,
                    FOREIGN KEY(task_sha256)
                        REFERENCES render_scheduler_tasks(task_sha256),
                    FOREIGN KEY(worker_id)
                        REFERENCES render_scheduler_workers(worker_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS render_scheduler_active_task_lease
                ON render_scheduler_leases(task_sha256)
                WHERE state = 'active';

                CREATE INDEX IF NOT EXISTS render_scheduler_worker_leases
                ON render_scheduler_leases(worker_id, state);
                """
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def healthcheck(self) -> bool:
        with self._lock:
            row = self._connection.execute("SELECT 1 AS ok").fetchone()
        return row is not None and int(row["ok"]) == 1

    def submit_task(
        self,
        task: ShotRenderTask,
        *,
        requirements: TaskResourceRequirements | None = None,
        now: datetime | None = None,
    ) -> ScheduledRenderTask:
        sealed = seal_task_identity(task)
        if task.id != sealed.id or task.task_sha256 != sealed.task_sha256:
            raise TaskIdentityError(
                "render task ID and hash must be derived with seal_task_identity"
            )
        if task.attempt != 1:
            raise TaskIdentityError("new render tasks must start at attempt 1")
        placement = requirements or TaskResourceRequirements()
        if (
            task.minimum_gpu_memory_bytes is not None
            and placement.gpu_memory_bytes < task.minimum_gpu_memory_bytes
        ):
            placement = placement.model_copy(
                update={"gpu_memory_bytes": task.minimum_gpu_memory_bytes}
            )
        timestamp = _as_utc(now or _utc_now())
        with self._transaction():
            existing = self._connection.execute(
                """
                SELECT * FROM render_scheduler_tasks
                WHERE task_sha256 = ? OR task_id = ?
                """,
                (task.task_sha256, task.id),
            ).fetchone()
            if existing is not None:
                stored = self._task_from_row(existing)
                if (
                    stored.task.task_sha256 != task.task_sha256
                    or stored.task.id != task.id
                    or stored.task.model_copy(update={"attempt": 1}) != task
                    or stored.requirements != placement
                ):
                    raise SchedulerConflictError(
                        "render task identity already exists with different data"
                    )
                return stored
            self._connection.execute(
                """
                INSERT INTO render_scheduler_tasks(
                    task_sha256, task_id, task_json, requirements_json, state,
                    attempt, active_lease_id, leased_worker_id,
                    completed_lease_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    task.task_sha256,
                    task.id,
                    task.model_dump_json(by_alias=False),
                    placement.model_dump_json(by_alias=False),
                    TaskState.PENDING.value,
                    1,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                ),
            )
            row = self._task_row(task.task_sha256)
            return self._task_from_row(row)

    def register_worker(
        self,
        capabilities: WorkerCapabilities,
        *,
        now: datetime | None = None,
        heartbeat_timeout: timedelta | None = None,
    ) -> SchedulerWorker:
        timestamp = _as_utc(now or _utc_now())
        timeout = _positive_duration(
            heartbeat_timeout or self.default_worker_timeout,
            "worker heartbeat timeout",
        )
        gpu_worker = any(
            kind in {WorkerKind.LOCAL_GPU, WorkerKind.REMOTE_GPU}
            for kind in capabilities.kinds
        )
        if gpu_worker and len(capabilities.gpus) != 1:
            raise SchedulerConflictError(
                "GPU workers must register exactly one device per worker"
            )
        with self._transaction():
            self._reap_expired_locked(timestamp)
            active_leases = self._active_lease_count_locked(capabilities.worker_id)
            existing = self._connection.execute(
                "SELECT * FROM render_scheduler_workers WHERE worker_id = ?",
                (capabilities.worker_id,),
            ).fetchone()
            if existing is not None and active_leases:
                stored_capabilities = WorkerCapabilities.model_validate_json(
                    str(existing["capabilities_json"])
                )
                if stored_capabilities != capabilities:
                    raise SchedulerConflictError(
                        "worker capabilities cannot change while it holds leases"
                    )
            for gpu in capabilities.gpus if gpu_worker else ():
                owner = self._connection.execute(
                    """
                    SELECT worker_id FROM render_scheduler_worker_devices
                    WHERE device_id = ?
                    """,
                    (gpu.device_id,),
                ).fetchone()
                if owner is not None and str(owner["worker_id"]) != capabilities.worker_id:
                    raise SchedulerConflictError(
                        f"device {gpu.device_id} already belongs to an active worker"
                    )
            if existing is None:
                registered_at = timestamp
                self._connection.execute(
                    """
                    INSERT INTO render_scheduler_workers(
                        worker_id, capabilities_json, status, registered_at,
                        last_heartbeat_at, heartbeat_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        capabilities.worker_id,
                        capabilities.model_dump_json(by_alias=False),
                        SchedulerWorkerStatus.ACTIVE.value,
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        (timestamp + timeout).isoformat(),
                    ),
                )
            else:
                registered_at = datetime.fromisoformat(str(existing["registered_at"]))
                self._connection.execute(
                    """
                    UPDATE render_scheduler_workers
                    SET capabilities_json = ?, status = ?,
                        last_heartbeat_at = ?, heartbeat_expires_at = ?
                    WHERE worker_id = ?
                    """,
                    (
                        capabilities.model_dump_json(by_alias=False),
                        SchedulerWorkerStatus.ACTIVE.value,
                        timestamp.isoformat(),
                        (timestamp + timeout).isoformat(),
                        capabilities.worker_id,
                    ),
                )
            self._connection.execute(
                "DELETE FROM render_scheduler_worker_devices WHERE worker_id = ?",
                (capabilities.worker_id,),
            )
            for gpu in capabilities.gpus if gpu_worker else ():
                self._connection.execute(
                    """
                    INSERT INTO render_scheduler_worker_devices(device_id, worker_id)
                    VALUES (?, ?)
                    """,
                    (gpu.device_id, capabilities.worker_id),
                )
            return SchedulerWorker(
                capabilities=capabilities,
                status=SchedulerWorkerStatus.ACTIVE,
                registered_at=registered_at,
                last_heartbeat_at=timestamp,
                heartbeat_expires_at=timestamp + timeout,
                active_lease_count=active_leases,
            )

    def heartbeat_worker(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        heartbeat_timeout: timedelta | None = None,
    ) -> SchedulerWorker:
        timestamp = _as_utc(now or _utc_now())
        timeout = _positive_duration(
            heartbeat_timeout or self.default_worker_timeout,
            "worker heartbeat timeout",
        )
        with self._transaction():
            self._reap_expired_locked(timestamp)
            row = self._worker_row(worker_id)
            if SchedulerWorkerStatus(str(row["status"])) is not SchedulerWorkerStatus.ACTIVE:
                raise WorkerUnavailableError("worker is not active; register it again")
            self._connection.execute(
                """
                UPDATE render_scheduler_workers
                SET last_heartbeat_at = ?, heartbeat_expires_at = ?
                WHERE worker_id = ?
                """,
                (
                    timestamp.isoformat(),
                    (timestamp + timeout).isoformat(),
                    worker_id,
                ),
            )
            updated = self._worker_row(worker_id)
            return self._worker_from_row(updated)

    def retire_worker(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> SchedulerWorker:
        timestamp = _as_utc(now or _utc_now())
        with self._transaction():
            row = self._worker_row(worker_id)
            self._requeue_worker_leases_locked(
                worker_id,
                timestamp,
                lease_state="worker-retired",
                reason="worker-retired",
            )
            self._connection.execute(
                """
                UPDATE render_scheduler_workers
                SET status = ?, last_heartbeat_at = ?, heartbeat_expires_at = ?
                WHERE worker_id = ?
                """,
                (
                    SchedulerWorkerStatus.RETIRED.value,
                    timestamp.isoformat(),
                    (timestamp + self.default_worker_timeout).isoformat(),
                    worker_id,
                ),
            )
            self._connection.execute(
                "DELETE FROM render_scheduler_worker_devices WHERE worker_id = ?",
                (worker_id,),
            )
            return SchedulerWorker(
                capabilities=WorkerCapabilities.model_validate_json(
                    str(row["capabilities_json"])
                ),
                status=SchedulerWorkerStatus.RETIRED,
                registered_at=datetime.fromisoformat(str(row["registered_at"])),
                last_heartbeat_at=timestamp,
                heartbeat_expires_at=timestamp + self.default_worker_timeout,
                active_lease_count=0,
            )

    def claim_next_task(
        self,
        worker_id: str,
        *,
        job_id: str | None = None,
        now: datetime | None = None,
        lease_duration: timedelta | None = None,
    ) -> LeaseGrant | None:
        timestamp = _as_utc(now or _utc_now())
        duration = _positive_duration(
            lease_duration or self.default_lease_duration,
            "lease duration",
        )
        with self._transaction():
            self._reap_expired_locked(timestamp)
            worker_row = self._worker_row(worker_id)
            if SchedulerWorkerStatus(str(worker_row["status"])) is not SchedulerWorkerStatus.ACTIVE:
                raise WorkerUnavailableError("worker is not active")
            if datetime.fromisoformat(str(worker_row["heartbeat_expires_at"])) <= timestamp:
                raise WorkerUnavailableError("worker heartbeat has expired")
            capabilities = WorkerCapabilities.model_validate_json(
                str(worker_row["capabilities_json"])
            )
            active_lease_count = self._active_lease_count_locked(worker_id)
            effective_concurrency = (
                1
                if any(
                    kind in {WorkerKind.LOCAL_GPU, WorkerKind.REMOTE_GPU}
                    for kind in capabilities.kinds
                )
                else capabilities.max_concurrent_tasks
            )
            if active_lease_count >= effective_concurrency:
                return None
            reserved_memory, reserved_gpu_memory = self._reserved_memory_locked(worker_id)
            if job_id is None:
                rows = self._connection.execute(
                    """
                    SELECT * FROM render_scheduler_tasks
                    WHERE state = ?
                    ORDER BY created_at, task_sha256
                    """,
                    (TaskState.PENDING.value,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM render_scheduler_tasks
                    WHERE state = ? AND json_extract(task_json, '$.job_id') = ?
                    ORDER BY created_at, task_sha256
                    """,
                    (TaskState.PENDING.value, job_id),
                ).fetchall()
            selected: ScheduledRenderTask | None = None
            for row in rows:
                candidate = self._task_from_row(row)
                if self._worker_can_run(
                    capabilities,
                    candidate,
                    reserved_memory=reserved_memory,
                    reserved_gpu_memory=reserved_gpu_memory,
                ):
                    selected = candidate
                    break
            if selected is None:
                return None
            token = secrets.token_urlsafe(32)
            token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
            lease_id = f"lease-{uuid.uuid4().hex}"
            expires_at = timestamp + duration
            device_id = (
                capabilities.gpus[0].device_id
                if any(
                    kind in {WorkerKind.LOCAL_GPU, WorkerKind.REMOTE_GPU}
                    for kind in capabilities.kinds
                )
                else None
            )
            self._connection.execute(
                """
                INSERT INTO render_scheduler_leases(
                    lease_id, task_sha256, task_id, job_id, output_variant_id,
                    worker_id, device_id, attempt, token_sha256, granted_at,
                    expires_at, last_heartbeat_at, state, ended_at, end_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL)
                """,
                (
                    lease_id,
                    selected.task.task_sha256,
                    selected.task.id,
                    selected.task.job_id,
                    selected.task.output_variant_id,
                    worker_id,
                    device_id,
                    selected.attempt,
                    token_sha256,
                    timestamp.isoformat(),
                    expires_at.isoformat(),
                    timestamp.isoformat(),
                ),
            )
            self._connection.execute(
                """
                UPDATE render_scheduler_tasks
                SET state = ?, active_lease_id = ?, leased_worker_id = ?,
                    updated_at = ?
                WHERE task_sha256 = ? AND state = ?
                """,
                (
                    TaskState.LEASED.value,
                    lease_id,
                    worker_id,
                    timestamp.isoformat(),
                    selected.task.task_sha256,
                    TaskState.PENDING.value,
                ),
            )
            assigned_task = selected.task.model_copy(update={"attempt": selected.attempt})
            lease = WorkerLease(
                id=lease_id,
                task_id=assigned_task.id,
                job_id=assigned_task.job_id,
                output_variant_id=assigned_task.output_variant_id,
                worker_id=worker_id,
                attempt=selected.attempt,
                lease_token_sha256=token_sha256,
                granted_at=timestamp,
                expires_at=expires_at,
                last_heartbeat_at=timestamp,
            )
            return LeaseGrant(
                task=assigned_task,
                lease=lease,
                lease_token=SecretStr(token),
            )

    def start_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: SecretStr | str,
        *,
        now: datetime | None = None,
    ) -> ScheduledRenderTask:
        timestamp = _as_utc(now or _utc_now())
        with self._transaction():
            self._reap_expired_locked(timestamp)
            lease_row = self._authenticated_active_lease(
                lease_id,
                worker_id,
                lease_token,
            )
            self._connection.execute(
                """
                UPDATE render_scheduler_tasks
                SET state = ?, updated_at = ?
                WHERE task_sha256 = ? AND active_lease_id = ?
                """,
                (
                    TaskState.RUNNING.value,
                    timestamp.isoformat(),
                    str(lease_row["task_sha256"]),
                    lease_id,
                ),
            )
            return self._task_from_row(
                self._task_row(str(lease_row["task_sha256"]))
            )

    def heartbeat_lease(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: SecretStr | str,
        *,
        now: datetime | None = None,
        lease_duration: timedelta | None = None,
        worker_timeout: timedelta | None = None,
    ) -> WorkerLease:
        timestamp = _as_utc(now or _utc_now())
        duration = _positive_duration(
            lease_duration or self.default_lease_duration,
            "lease duration",
        )
        timeout = _positive_duration(
            worker_timeout or self.default_worker_timeout,
            "worker heartbeat timeout",
        )
        with self._transaction():
            self._reap_expired_locked(timestamp)
            lease_row = self._authenticated_active_lease(
                lease_id,
                worker_id,
                lease_token,
            )
            expires_at = timestamp + duration
            self._connection.execute(
                """
                UPDATE render_scheduler_leases
                SET last_heartbeat_at = ?, expires_at = ?
                WHERE lease_id = ?
                """,
                (timestamp.isoformat(), expires_at.isoformat(), lease_id),
            )
            self._connection.execute(
                """
                UPDATE render_scheduler_workers
                SET last_heartbeat_at = ?, heartbeat_expires_at = ?
                WHERE worker_id = ? AND status = ?
                """,
                (
                    timestamp.isoformat(),
                    (timestamp + timeout).isoformat(),
                    worker_id,
                    SchedulerWorkerStatus.ACTIVE.value,
                ),
            )
            return WorkerLease(
                id=lease_id,
                task_id=str(lease_row["task_id"]),
                job_id=str(lease_row["job_id"]),
                output_variant_id=str(lease_row["output_variant_id"]),
                worker_id=worker_id,
                attempt=int(lease_row["attempt"]),
                lease_token_sha256=str(lease_row["token_sha256"]),
                granted_at=datetime.fromisoformat(str(lease_row["granted_at"])),
                expires_at=expires_at,
                last_heartbeat_at=timestamp,
            )

    def complete_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: SecretStr | str,
        *,
        now: datetime | None = None,
    ) -> ScheduledRenderTask:
        timestamp = _as_utc(now or _utc_now())
        with self._transaction():
            self._reap_expired_locked(timestamp)
            lease_row = self._authenticated_lease(
                lease_id,
                worker_id,
                lease_token,
            )
            task_sha256 = str(lease_row["task_sha256"])
            if str(lease_row["state"]) == "completed":
                task = self._task_from_row(self._task_row(task_sha256))
                if (
                    task.state is TaskState.COMPLETE
                    and task.completed_lease_id == lease_id
                ):
                    return task
                raise LeaseRejectedError("completed lease does not own task completion")
            if str(lease_row["state"]) != "active":
                raise LeaseRejectedError("lease is no longer active")
            task_row = self._task_row(task_sha256)
            if str(task_row["active_lease_id"]) != lease_id:
                raise LeaseRejectedError("lease no longer owns the task")
            self._connection.execute(
                """
                UPDATE render_scheduler_leases
                SET state = 'completed', ended_at = ?, end_reason = 'completed'
                WHERE lease_id = ?
                """,
                (timestamp.isoformat(), lease_id),
            )
            self._connection.execute(
                """
                UPDATE render_scheduler_tasks
                SET state = ?, active_lease_id = NULL, leased_worker_id = NULL,
                    completed_lease_id = ?, updated_at = ?
                WHERE task_sha256 = ? AND active_lease_id = ?
                """,
                (
                    TaskState.COMPLETE.value,
                    lease_id,
                    timestamp.isoformat(),
                    task_sha256,
                    lease_id,
                ),
            )
            return self._task_from_row(self._task_row(task_sha256))

    def fail_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: SecretStr | str,
        *,
        retry: bool = True,
        reason: Identifier = "worker-reported-failure",
        now: datetime | None = None,
    ) -> ScheduledRenderTask:
        timestamp = _as_utc(now or _utc_now())
        with self._transaction():
            self._reap_expired_locked(timestamp)
            lease_row = self._authenticated_active_lease(
                lease_id,
                worker_id,
                lease_token,
            )
            task_sha256 = str(lease_row["task_sha256"])
            task_row = self._task_row(task_sha256)
            next_attempt = int(task_row["attempt"]) + (1 if retry else 0)
            next_state = TaskState.PENDING if retry else TaskState.FAILED
            self._connection.execute(
                """
                UPDATE render_scheduler_leases
                SET state = 'failed', ended_at = ?, end_reason = ?
                WHERE lease_id = ?
                """,
                (timestamp.isoformat(), reason, lease_id),
            )
            self._connection.execute(
                """
                UPDATE render_scheduler_tasks
                SET state = ?, attempt = ?, active_lease_id = NULL,
                    leased_worker_id = NULL, updated_at = ?
                WHERE task_sha256 = ? AND active_lease_id = ?
                """,
                (
                    next_state.value,
                    next_attempt,
                    timestamp.isoformat(),
                    task_sha256,
                    lease_id,
                ),
            )
            return self._task_from_row(self._task_row(task_sha256))

    def reap_expired(self, *, now: datetime | None = None) -> SchedulerReapResult:
        timestamp = _as_utc(now or _utc_now())
        with self._transaction():
            return self._reap_expired_locked(timestamp)

    def get_task(self, task_id: str) -> ScheduledRenderTask | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM render_scheduler_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return None if row is None else self._task_from_row(row)

    def list_tasks(
        self,
        *,
        job_id: str | None = None,
    ) -> tuple[ScheduledRenderTask, ...]:
        with self._lock:
            if job_id is None:
                rows = self._connection.execute(
                    """
                    SELECT * FROM render_scheduler_tasks
                    ORDER BY created_at, task_sha256
                    """
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM render_scheduler_tasks
                    WHERE json_extract(task_json, '$.job_id') = ?
                    ORDER BY created_at, task_sha256
                    """,
                    (job_id,),
                ).fetchall()
            return tuple(self._task_from_row(row) for row in rows)

    def list_workers(
        self,
        *,
        include_inactive: bool = True,
        now: datetime | None = None,
    ) -> tuple[SchedulerWorker, ...]:
        self.reap_expired(now=now)
        with self._lock:
            if include_inactive:
                rows = self._connection.execute(
                    """
                    SELECT * FROM render_scheduler_workers
                    ORDER BY worker_id
                    """
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM render_scheduler_workers
                    WHERE status = ? ORDER BY worker_id
                    """,
                    (SchedulerWorkerStatus.ACTIVE.value,),
                ).fetchall()
            return tuple(self._worker_from_row(row) for row in rows)

    def active_worker_count(
        self,
        *,
        required_kind: WorkerKind | None = None,
        now: datetime | None = None,
    ) -> int:
        workers = self.list_workers(include_inactive=False, now=now)
        if required_kind is None:
            return len(workers)
        return sum(
            1 for worker in workers if required_kind in worker.capabilities.kinds
        )

    def _worker_can_run(
        self,
        capabilities: WorkerCapabilities,
        scheduled: ScheduledRenderTask,
        *,
        reserved_memory: int,
        reserved_gpu_memory: int,
    ) -> bool:
        task = scheduled.task
        requirements = scheduled.requirements
        if (
            task.required_worker_kind is not None
            and task.required_worker_kind not in capabilities.kinds
        ):
            return False
        if capabilities.max_width is not None and task.width > capabilities.max_width:
            return False
        if capabilities.max_height is not None and task.height > capabilities.max_height:
            return False
        if (
            requirements.required_artifact_format is not None
            and requirements.required_artifact_format
            not in capabilities.supported_artifact_formats
        ):
            return False
        if reserved_memory + requirements.memory_bytes > capabilities.memory_bytes:
            return False
        required_gpu_memory = max(
            requirements.gpu_memory_bytes,
            task.minimum_gpu_memory_bytes or 0,
        )
        gpu_required = (
            required_gpu_memory > 0
            or task.required_worker_kind
            in {WorkerKind.LOCAL_GPU, WorkerKind.REMOTE_GPU}
        )
        if gpu_required:
            if len(capabilities.gpus) != 1:
                return False
            if (
                reserved_gpu_memory + required_gpu_memory
                > capabilities.gpus[0].memory_bytes
            ):
                return False
        return True

    def _reap_expired_locked(self, now: datetime) -> SchedulerReapResult:
        worker_rows = self._connection.execute(
            """
            SELECT worker_id, heartbeat_expires_at
            FROM render_scheduler_workers
            WHERE status = ?
            """,
            (SchedulerWorkerStatus.ACTIVE.value,),
        ).fetchall()
        lost_workers = sorted(
            str(row["worker_id"])
            for row in worker_rows
            if datetime.fromisoformat(str(row["heartbeat_expires_at"])) <= now
        )
        for worker_id in lost_workers:
            self._connection.execute(
                """
                UPDATE render_scheduler_workers SET status = ?
                WHERE worker_id = ?
                """,
                (SchedulerWorkerStatus.LOST.value, worker_id),
            )
            self._connection.execute(
                "DELETE FROM render_scheduler_worker_devices WHERE worker_id = ?",
                (worker_id,),
            )

        active_leases = self._connection.execute(
            """
            SELECT * FROM render_scheduler_leases
            WHERE state = 'active'
            ORDER BY lease_id
            """
        ).fetchall()
        expired_leases: list[str] = []
        requeued_tasks: list[str] = []
        lost_worker_set = set(lost_workers)
        for lease in active_leases:
            worker_lost = str(lease["worker_id"]) in lost_worker_set
            lease_expired = datetime.fromisoformat(str(lease["expires_at"])) <= now
            if not worker_lost and not lease_expired:
                continue
            lease_id = str(lease["lease_id"])
            task_sha256 = str(lease["task_sha256"])
            task_row = self._task_row(task_sha256)
            state = "worker-lost" if worker_lost else "expired"
            self._connection.execute(
                """
                UPDATE render_scheduler_leases
                SET state = ?, ended_at = ?, end_reason = ?
                WHERE lease_id = ? AND state = 'active'
                """,
                (state, now.isoformat(), state, lease_id),
            )
            expired_leases.append(lease_id)
            if (
                str(task_row["active_lease_id"]) == lease_id
                and TaskState(str(task_row["state"]))
                in {TaskState.LEASED, TaskState.RUNNING}
            ):
                self._connection.execute(
                    """
                    UPDATE render_scheduler_tasks
                    SET state = ?, attempt = ?, active_lease_id = NULL,
                        leased_worker_id = NULL, updated_at = ?
                    WHERE task_sha256 = ? AND active_lease_id = ?
                    """,
                    (
                        TaskState.PENDING.value,
                        int(task_row["attempt"]) + 1,
                        now.isoformat(),
                        task_sha256,
                        lease_id,
                    ),
                )
                requeued_tasks.append(str(task_row["task_id"]))
        return SchedulerReapResult(
            lost_worker_ids=tuple(lost_workers),
            expired_lease_ids=tuple(sorted(expired_leases)),
            requeued_task_ids=tuple(sorted(requeued_tasks)),
        )

    def _requeue_worker_leases_locked(
        self,
        worker_id: str,
        now: datetime,
        *,
        lease_state: str,
        reason: str,
    ) -> None:
        leases = self._connection.execute(
            """
            SELECT * FROM render_scheduler_leases
            WHERE worker_id = ? AND state = 'active'
            """,
            (worker_id,),
        ).fetchall()
        for lease in leases:
            lease_id = str(lease["lease_id"])
            task_sha256 = str(lease["task_sha256"])
            task_row = self._task_row(task_sha256)
            self._connection.execute(
                """
                UPDATE render_scheduler_leases
                SET state = ?, ended_at = ?, end_reason = ?
                WHERE lease_id = ?
                """,
                (lease_state, now.isoformat(), reason, lease_id),
            )
            if str(task_row["active_lease_id"]) == lease_id:
                self._connection.execute(
                    """
                    UPDATE render_scheduler_tasks
                    SET state = ?, attempt = ?, active_lease_id = NULL,
                        leased_worker_id = NULL, updated_at = ?
                    WHERE task_sha256 = ?
                    """,
                    (
                        TaskState.PENDING.value,
                        int(task_row["attempt"]) + 1,
                        now.isoformat(),
                        task_sha256,
                    ),
                )

    def _authenticated_active_lease(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: SecretStr | str,
    ) -> sqlite3.Row:
        row = self._authenticated_lease(lease_id, worker_id, lease_token)
        if str(row["state"]) != "active":
            raise LeaseRejectedError("lease is no longer active")
        return row

    def _authenticated_lease(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: SecretStr | str,
    ) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM render_scheduler_leases WHERE lease_id = ?",
            (lease_id,),
        ).fetchone()
        if row is None:
            raise LeaseRejectedError("lease does not exist")
        token = (
            lease_token.get_secret_value()
            if isinstance(lease_token, SecretStr)
            else lease_token
        )
        supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if str(row["worker_id"]) != worker_id or not hmac.compare_digest(
            str(row["token_sha256"]),
            supplied_hash,
        ):
            raise LeaseRejectedError("lease authentication failed")
        return cast(sqlite3.Row, row)

    def _worker_row(self, worker_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM render_scheduler_workers WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()
        if row is None:
            raise WorkerUnavailableError("worker is not registered")
        return cast(sqlite3.Row, row)

    def _task_row(self, task_sha256: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM render_scheduler_tasks WHERE task_sha256 = ?",
            (task_sha256,),
        ).fetchone()
        if row is None:
            raise SchedulerConflictError("scheduled task does not exist")
        return cast(sqlite3.Row, row)

    def _task_from_row(self, row: sqlite3.Row) -> ScheduledRenderTask:
        attempt = int(row["attempt"])
        task = ShotRenderTask.model_validate_json(str(row["task_json"])).model_copy(
            update={"attempt": attempt}
        )
        return ScheduledRenderTask(
            task=task,
            requirements=TaskResourceRequirements.model_validate_json(
                str(row["requirements_json"])
            ),
            state=TaskState(str(row["state"])),
            attempt=attempt,
            active_lease_id=(
                None
                if row["active_lease_id"] is None
                else str(row["active_lease_id"])
            ),
            leased_worker_id=(
                None
                if row["leased_worker_id"] is None
                else str(row["leased_worker_id"])
            ),
            completed_lease_id=(
                None
                if row["completed_lease_id"] is None
                else str(row["completed_lease_id"])
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _worker_from_row(self, row: sqlite3.Row) -> SchedulerWorker:
        worker_id = str(row["worker_id"])
        return SchedulerWorker(
            capabilities=WorkerCapabilities.model_validate_json(
                str(row["capabilities_json"])
            ),
            status=SchedulerWorkerStatus(str(row["status"])),
            registered_at=datetime.fromisoformat(str(row["registered_at"])),
            last_heartbeat_at=datetime.fromisoformat(
                str(row["last_heartbeat_at"])
            ),
            heartbeat_expires_at=datetime.fromisoformat(
                str(row["heartbeat_expires_at"])
            ),
            active_lease_count=self._active_lease_count_locked(worker_id),
        )

    def _active_lease_count_locked(self, worker_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT COUNT(*) AS count FROM render_scheduler_leases
            WHERE worker_id = ? AND state = 'active'
            """,
            (worker_id,),
        ).fetchone()
        return 0 if row is None else int(row["count"])

    def _reserved_memory_locked(self, worker_id: str) -> tuple[int, int]:
        rows = self._connection.execute(
            """
            SELECT task.requirements_json
            FROM render_scheduler_leases AS lease
            JOIN render_scheduler_tasks AS task
              ON task.task_sha256 = lease.task_sha256
            WHERE lease.worker_id = ? AND lease.state = 'active'
            """,
            (worker_id,),
        ).fetchall()
        memory_bytes = 0
        gpu_memory_bytes = 0
        for row in rows:
            requirements = TaskResourceRequirements.model_validate_json(
                str(row["requirements_json"])
            )
            memory_bytes += requirements.memory_bytes
            gpu_memory_bytes += requirements.gpu_memory_bytes
        return memory_bytes, gpu_memory_bytes

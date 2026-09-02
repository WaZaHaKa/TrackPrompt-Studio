from __future__ import annotations

import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from .manifests import canonical_json_bytes, safe_identifier
from .models import (
    ChunkLease,
    ChunkOutput,
    ChunkState,
    FrameRange,
    IdentityBundle,
    WorkerKind,
    require_sha256,
)

_ACTIVE_STATES = (
    ChunkState.LEASED,
    ChunkState.RENDERING,
    ChunkState.UPLOADING,
    ChunkState.VALIDATING,
)
_TRANSITIONS: dict[ChunkState, frozenset[ChunkState]] = {
    ChunkState.LEASED: frozenset(
        {ChunkState.RENDERING, ChunkState.RETRYABLE, ChunkState.FAILED}
    ),
    ChunkState.RENDERING: frozenset(
        {ChunkState.UPLOADING, ChunkState.RETRYABLE, ChunkState.FAILED}
    ),
    ChunkState.UPLOADING: frozenset(
        {ChunkState.VALIDATING, ChunkState.RETRYABLE, ChunkState.FAILED}
    ),
    ChunkState.VALIDATING: frozenset(
        {
            ChunkState.COMPLETE,
            ChunkState.RETRYABLE,
            ChunkState.FAILED,
            ChunkState.QUARANTINED,
        }
    ),
}


class SchedulerError(RuntimeError):
    pass


class LeaseLostError(SchedulerError):
    pass


@dataclass(frozen=True, slots=True)
class CompletionResult:
    state: ChunkState
    published_frames: tuple[int, ...]
    identical_duplicates: tuple[int, ...]
    quarantined_conflicts: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class JobStatus:
    job_id: str
    cancelled: bool
    state_counts: dict[ChunkState, int]
    published_frames: int
    unresolved_conflicts: int
    complete: bool


def _epoch(value: datetime) -> float:
    if value.tzinfo is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
    return value.timestamp()


def _from_epoch(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


class SqliteScheduler:
    """Durable, transactional chunk leasing and publication coordination."""

    def __init__(self, database: Path | str) -> None:
        self.database = Path(database).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SqliteScheduler:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                scene_sha256 TEXT NOT NULL,
                profile_sha256 TEXT NOT NULL,
                package_sha256 TEXT NOT NULL,
                manifest_sha256 TEXT,
                frame_start INTEGER NOT NULL,
                frame_end INTEGER NOT NULL,
                prefer_local INTEGER NOT NULL,
                cancelled INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                chunk_id TEXT NOT NULL,
                start_frame INTEGER NOT NULL,
                end_frame INTEGER NOT NULL,
                state TEXT NOT NULL,
                assigned_worker TEXT,
                worker_kind TEXT,
                lease_token TEXT,
                lease_expires_at REAL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL,
                output_manifest_json TEXT,
                timing_json TEXT,
                cost TEXT NOT NULL DEFAULT '0',
                last_error TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (job_id, chunk_id),
                UNIQUE (job_id, start_frame, end_frame)
            );
            CREATE INDEX IF NOT EXISTS chunks_claim_order
                ON chunks(job_id, state, start_frame);
            CREATE TABLE IF NOT EXISTS heartbeats (
                job_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                lease_token TEXT NOT NULL,
                state TEXT NOT NULL,
                seen_at REAL NOT NULL,
                metadata_json TEXT NOT NULL,
                PRIMARY KEY (job_id, worker_id),
                FOREIGN KEY (job_id, chunk_id)
                    REFERENCES chunks(job_id, chunk_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS frame_candidates (
                job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                frame INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                object_key TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(job_id, frame, sha256, object_key)
            );
            CREATE TABLE IF NOT EXISTS frame_publications (
                job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                frame INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                object_key TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(job_id, frame)
            );
            CREATE TABLE IF NOT EXISTS publication_conflicts (
                conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                frame INTEGER NOT NULL,
                existing_sha256 TEXT NOT NULL,
                incoming_sha256 TEXT NOT NULL,
                preferred_sha256 TEXT NOT NULL,
                preferred_source_kind TEXT NOT NULL,
                resolution TEXT NOT NULL,
                created_at REAL NOT NULL,
                resolved_at REAL
            );
            """
        )
        job_columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "manifest_sha256" not in job_columns:
            self._connection.execute("ALTER TABLE jobs ADD COLUMN manifest_sha256 TEXT")

    def create_job(
        self,
        job_id: str,
        identities: IdentityBundle,
        frame_range: FrameRange,
        *,
        frames_per_chunk: int,
        max_attempts: int = 3,
        prefer_local: bool = True,
        manifest_sha256: str | None = None,
        now: datetime | None = None,
    ) -> int:
        safe_identifier(job_id, "job_id")
        if frames_per_chunk < 1 or max_attempts < 1:
            raise ValueError("chunk size and max attempts must be positive")
        if manifest_sha256 is not None:
            manifest_sha256 = require_sha256(manifest_sha256, "manifest_sha256")
        timestamp = _epoch(now or datetime.now(UTC))
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, scene_sha256, profile_sha256, package_sha256,
                    manifest_sha256, frame_start, frame_end, prefer_local, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    identities.scene_sha256,
                    identities.profile_sha256,
                    identities.package_sha256,
                    manifest_sha256,
                    frame_range.start,
                    frame_range.end,
                    int(prefer_local),
                    timestamp,
                ),
            )
            count = 0
            for start in range(frame_range.start, frame_range.end + 1, frames_per_chunk):
                end = min(frame_range.end, start + frames_per_chunk - 1)
                chunk_id = f"chunk-{start:06d}-{end:06d}"
                connection.execute(
                    """
                    INSERT INTO chunks(
                        job_id, chunk_id, start_frame, end_frame, state,
                        max_attempts, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        chunk_id,
                        start,
                        end,
                        ChunkState.PENDING.value,
                        max_attempts,
                        timestamp,
                    ),
                )
                count += 1
        return count

    def _expire_leases(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        timestamp: float,
    ) -> int:
        placeholders = ",".join("?" for _ in _ACTIVE_STATES)
        rows = connection.execute(
            f"""
            SELECT chunk_id, attempt_count, max_attempts
            FROM chunks
            WHERE job_id = ? AND state IN ({placeholders})
              AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
            """,
            (job_id, *(state.value for state in _ACTIVE_STATES), timestamp),
        ).fetchall()
        for row in rows:
            next_state = (
                ChunkState.RETRYABLE
                if int(row["attempt_count"]) < int(row["max_attempts"])
                else ChunkState.FAILED
            )
            connection.execute(
                """
                UPDATE chunks
                SET state = ?, assigned_worker = NULL, worker_kind = NULL,
                    lease_token = NULL, lease_expires_at = NULL,
                    last_error = 'lease-expired', updated_at = ?
                WHERE job_id = ? AND chunk_id = ?
                """,
                (next_state.value, timestamp, job_id, row["chunk_id"]),
            )
        return len(rows)

    def recover_expired_leases(self, job_id: str, *, now: datetime | None = None) -> int:
        timestamp = _epoch(now or datetime.now(UTC))
        with self._transaction() as connection:
            return self._expire_leases(connection, job_id, timestamp)

    def claim_next(
        self,
        job_id: str,
        worker_id: str,
        worker_kind: WorkerKind,
        *,
        lease_seconds: int = 900,
        now: datetime | None = None,
    ) -> ChunkLease | None:
        safe_identifier(worker_id, "worker_id")
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least five")
        claimed_at = now or datetime.now(UTC)
        timestamp = _epoch(claimed_at)
        expiry = claimed_at + timedelta(seconds=lease_seconds)
        token = secrets.token_urlsafe(32)
        with self._transaction() as connection:
            job = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise SchedulerError("unknown job")
            self._expire_leases(connection, job_id, timestamp)
            if bool(job["cancelled"]):
                return None
            row = connection.execute(
                """
                SELECT * FROM chunks
                WHERE job_id = ? AND state IN (?, ?)
                ORDER BY start_frame, chunk_id
                LIMIT 1
                """,
                (job_id, ChunkState.PENDING.value, ChunkState.RETRYABLE.value),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE chunks
                SET state = ?, assigned_worker = ?, worker_kind = ?,
                    lease_token = ?, lease_expires_at = ?,
                    attempt_count = attempt_count + 1, updated_at = ?,
                    last_error = NULL
                WHERE job_id = ? AND chunk_id = ? AND state IN (?, ?)
                """,
                (
                    ChunkState.LEASED.value,
                    worker_id,
                    worker_kind.value,
                    token,
                    _epoch(expiry),
                    timestamp,
                    job_id,
                    row["chunk_id"],
                    ChunkState.PENDING.value,
                    ChunkState.RETRYABLE.value,
                ),
            )
            if updated.rowcount != 1:
                raise SchedulerError("chunk claim lost an atomic update race")
            attempt_count = int(row["attempt_count"]) + 1
        return ChunkLease(
            job_id=job_id,
            chunk_id=str(row["chunk_id"]),
            frame_range=FrameRange(int(row["start_frame"]), int(row["end_frame"])),
            identities=IdentityBundle(
                str(job["scene_sha256"]),
                str(job["profile_sha256"]),
                str(job["package_sha256"]),
            ),
            worker_id=worker_id,
            worker_kind=worker_kind,
            lease_token=token,
            lease_expires_at=expiry,
            attempt_count=attempt_count,
            manifest_sha256=(
                str(job["manifest_sha256"])
                if job["manifest_sha256"] is not None
                else None
            ),
        )

    def _leased_chunk(
        self,
        connection: sqlite3.Connection,
        lease: ChunkLease,
        timestamp: float,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM chunks WHERE job_id = ? AND chunk_id = ?",
            (lease.job_id, lease.chunk_id),
        ).fetchone()
        if (
            row is None
            or row["lease_token"] != lease.lease_token
            or row["assigned_worker"] != lease.worker_id
            or row["worker_kind"] != lease.worker_kind.value
            or row["lease_expires_at"] is None
            or float(row["lease_expires_at"]) <= timestamp
        ):
            raise LeaseLostError("lease is missing, expired, or owned by another worker")
        return cast(sqlite3.Row, row)

    def renew_lease(
        self,
        lease: ChunkLease,
        *,
        lease_seconds: int = 900,
        now: datetime | None = None,
    ) -> datetime:
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least five")
        renewed_at = now or datetime.now(UTC)
        timestamp = _epoch(renewed_at)
        expiry = renewed_at + timedelta(seconds=lease_seconds)
        with self._transaction() as connection:
            row = self._leased_chunk(connection, lease, timestamp)
            if ChunkState(str(row["state"])) not in _ACTIVE_STATES:
                raise LeaseLostError("terminal chunks cannot renew a lease")
            connection.execute(
                """
                UPDATE chunks SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND chunk_id = ? AND lease_token = ?
                """,
                (_epoch(expiry), timestamp, lease.job_id, lease.chunk_id, lease.lease_token),
            )
        return expiry

    def heartbeat(
        self,
        lease: ChunkLease,
        state: ChunkState,
        *,
        metadata: dict[str, Any] | None = None,
        lease_seconds: int = 900,
        now: datetime | None = None,
    ) -> datetime:
        seen_at = now or datetime.now(UTC)
        timestamp = _epoch(seen_at)
        expiry = seen_at + timedelta(seconds=lease_seconds)
        with self._transaction() as connection:
            row = self._leased_chunk(connection, lease, timestamp)
            if ChunkState(str(row["state"])) != state:
                raise LeaseLostError("heartbeat state differs from scheduler state")
            connection.execute(
                "UPDATE chunks SET lease_expires_at = ?, updated_at = ? WHERE job_id = ? AND chunk_id = ?",
                (_epoch(expiry), timestamp, lease.job_id, lease.chunk_id),
            )
            connection.execute(
                """
                INSERT INTO heartbeats(
                    job_id, worker_id, chunk_id, lease_token, state,
                    seen_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, worker_id) DO UPDATE SET
                    chunk_id = excluded.chunk_id,
                    lease_token = excluded.lease_token,
                    state = excluded.state,
                    seen_at = excluded.seen_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    lease.job_id,
                    lease.worker_id,
                    lease.chunk_id,
                    lease.lease_token,
                    state.value,
                    timestamp,
                    canonical_json_bytes(metadata or {}).decode("utf-8"),
                ),
            )
        return expiry

    def transition(
        self,
        lease: ChunkLease,
        target: ChunkState,
        *,
        error: str | None = None,
        now: datetime | None = None,
    ) -> None:
        timestamp = _epoch(now or datetime.now(UTC))
        with self._transaction() as connection:
            row = self._leased_chunk(connection, lease, timestamp)
            current = ChunkState(str(row["state"]))
            if target not in _TRANSITIONS.get(current, frozenset()):
                raise SchedulerError(f"invalid chunk transition {current.value} -> {target.value}")
            if target in {ChunkState.COMPLETE, ChunkState.QUARANTINED}:
                raise SchedulerError("use complete_chunk for publication transitions")
            if (
                target == ChunkState.RETRYABLE
                and int(row["attempt_count"]) >= int(row["max_attempts"])
            ):
                target = ChunkState.FAILED
            clear_lease = target in {ChunkState.RETRYABLE, ChunkState.FAILED}
            connection.execute(
                """
                UPDATE chunks
                SET state = ?, last_error = ?, updated_at = ?,
                    assigned_worker = CASE WHEN ? THEN NULL ELSE assigned_worker END,
                    worker_kind = CASE WHEN ? THEN NULL ELSE worker_kind END,
                    lease_token = CASE WHEN ? THEN NULL ELSE lease_token END,
                    lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END
                WHERE job_id = ? AND chunk_id = ?
                """,
                (
                    target.value,
                    error[:500] if error else None,
                    timestamp,
                    int(clear_lease),
                    int(clear_lease),
                    int(clear_lease),
                    int(clear_lease),
                    lease.job_id,
                    lease.chunk_id,
                ),
            )

    def complete_chunk(
        self,
        lease: ChunkLease,
        output: ChunkOutput,
        *,
        now: datetime | None = None,
    ) -> CompletionResult:
        if output.job_id != lease.job_id or output.chunk_id != lease.chunk_id:
            raise SchedulerError("chunk output identity does not match its lease")
        if output.identities != lease.identities:
            raise SchedulerError("chunk output scene/profile/package identity drifted")
        if output.worker_id != lease.worker_id or output.worker_kind != lease.worker_kind:
            raise SchedulerError("chunk output worker identity does not match its lease")
        expected = set(range(lease.frame_range.start, lease.frame_range.end + 1))
        actual = {item.frame for item in output.frames}
        if actual != expected:
            raise SchedulerError("chunk output frame set is incomplete or outside its lease")
        timestamp = _epoch(now or datetime.now(UTC))
        published: list[int] = []
        identical: list[int] = []
        conflicts: list[int] = []
        with self._transaction() as connection:
            row = self._leased_chunk(connection, lease, timestamp)
            if ChunkState(str(row["state"])) != ChunkState.VALIDATING:
                raise SchedulerError("chunk must be VALIDATING before publication")
            job = connection.execute(
                "SELECT prefer_local FROM jobs WHERE job_id = ?",
                (lease.job_id,),
            ).fetchone()
            if job is None:
                raise SchedulerError("job disappeared during publication")
            prefer_local = bool(job["prefer_local"])
            for artifact in sorted(output.frames, key=lambda item: item.frame):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO frame_candidates(
                        job_id, frame, sha256, source_kind, worker_id, chunk_id,
                        object_key, size_bytes, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CANDIDATE', ?)
                    """,
                    (
                        lease.job_id,
                        artifact.frame,
                        artifact.sha256,
                        lease.worker_kind.value,
                        lease.worker_id,
                        lease.chunk_id,
                        artifact.object_key,
                        artifact.size_bytes,
                        timestamp,
                    ),
                )
                existing = connection.execute(
                    "SELECT * FROM frame_publications WHERE job_id = ? AND frame = ?",
                    (lease.job_id, artifact.frame),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO frame_publications(
                            job_id, frame, sha256, source_kind, worker_id,
                            chunk_id, object_key, status, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACCEPTED', ?)
                        """,
                        (
                            lease.job_id,
                            artifact.frame,
                            artifact.sha256,
                            lease.worker_kind.value,
                            lease.worker_id,
                            lease.chunk_id,
                            artifact.object_key,
                            timestamp,
                        ),
                    )
                    published.append(artifact.frame)
                    continue
                if str(existing["sha256"]) == artifact.sha256:
                    connection.execute(
                        """
                        UPDATE frame_candidates SET status = 'DUPLICATE_IDENTICAL'
                        WHERE job_id = ? AND frame = ? AND sha256 = ? AND object_key = ?
                        """,
                        (lease.job_id, artifact.frame, artifact.sha256, artifact.object_key),
                    )
                    identical.append(artifact.frame)
                    continue
                existing_kind = WorkerKind(str(existing["source_kind"]))
                prefer_incoming = (
                    prefer_local
                    and lease.worker_kind == WorkerKind.LOCAL
                    and existing_kind != WorkerKind.LOCAL
                )
                preferred_sha = artifact.sha256 if prefer_incoming else str(existing["sha256"])
                preferred_kind = lease.worker_kind if prefer_incoming else existing_kind
                if prefer_incoming:
                    connection.execute(
                        """
                        UPDATE frame_publications
                        SET sha256 = ?, source_kind = ?, worker_id = ?, chunk_id = ?,
                            object_key = ?, status = 'QUARANTINED_CONFLICT', updated_at = ?
                        WHERE job_id = ? AND frame = ?
                        """,
                        (
                            artifact.sha256,
                            lease.worker_kind.value,
                            lease.worker_id,
                            lease.chunk_id,
                            artifact.object_key,
                            timestamp,
                            lease.job_id,
                            artifact.frame,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE frame_publications
                        SET status = 'QUARANTINED_CONFLICT', updated_at = ?
                        WHERE job_id = ? AND frame = ?
                        """,
                        (timestamp, lease.job_id, artifact.frame),
                    )
                connection.execute(
                    """
                    UPDATE frame_candidates SET status = 'QUARANTINED'
                    WHERE job_id = ? AND frame = ?
                    """,
                    (lease.job_id, artifact.frame),
                )
                connection.execute(
                    """
                    INSERT INTO publication_conflicts(
                        job_id, frame, existing_sha256, incoming_sha256,
                        preferred_sha256, preferred_source_kind, resolution,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING_OPERATOR', ?)
                    """,
                    (
                        lease.job_id,
                        artifact.frame,
                        existing["sha256"],
                        artifact.sha256,
                        preferred_sha,
                        preferred_kind.value,
                        timestamp,
                    ),
                )
                conflicts.append(artifact.frame)
            state = ChunkState.QUARANTINED if conflicts else ChunkState.COMPLETE
            output_payload = {
                "jobId": output.job_id,
                "chunkId": output.chunk_id,
                "workerId": output.worker_id,
                "workerKind": output.worker_kind.value,
                "sceneSha256": output.identities.scene_sha256,
                "profileSha256": output.identities.profile_sha256,
                "packageSha256": output.identities.package_sha256,
                "frames": [
                    {
                        "frame": item.frame,
                        "objectKey": item.object_key,
                        "sha256": item.sha256,
                        "sizeBytes": item.size_bytes,
                    }
                    for item in output.frames
                ],
            }
            connection.execute(
                """
                UPDATE chunks
                SET state = ?, output_manifest_json = ?, timing_json = ?,
                    cost = ?, assigned_worker = NULL, worker_kind = NULL,
                    lease_token = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND chunk_id = ?
                """,
                (
                    state.value,
                    canonical_json_bytes(output_payload).decode("utf-8"),
                    canonical_json_bytes(
                        {"wallSeconds": str(output.wall_seconds), **dict(output.metadata)}
                    ).decode("utf-8"),
                    str(output.cost),
                    timestamp,
                    lease.job_id,
                    lease.chunk_id,
                ),
            )
        return CompletionResult(
            state=state,
            published_frames=tuple(published),
            identical_duplicates=tuple(identical),
            quarantined_conflicts=tuple(conflicts),
        )

    def cancel_job(self, job_id: str) -> None:
        with self._transaction() as connection:
            updated = connection.execute(
                "UPDATE jobs SET cancelled = 1 WHERE job_id = ?",
                (job_id,),
            )
            if updated.rowcount != 1:
                raise SchedulerError("unknown job")

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT cancelled FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise SchedulerError("unknown job")
        return bool(row["cancelled"])

    def status(self, job_id: str) -> JobStatus:
        with self._lock:
            job = self._connection.execute(
                "SELECT cancelled, frame_start, frame_end FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise SchedulerError("unknown job")
            rows = self._connection.execute(
                "SELECT state, COUNT(*) AS amount FROM chunks WHERE job_id = ? GROUP BY state",
                (job_id,),
            ).fetchall()
            published = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM frame_publications WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0]
            )
            conflicts = int(
                self._connection.execute(
                    """
                    SELECT COUNT(*) FROM publication_conflicts
                    WHERE job_id = ? AND resolution = 'PENDING_OPERATOR'
                    """,
                    (job_id,),
                ).fetchone()[0]
            )
        counts = {state: 0 for state in ChunkState}
        for row in rows:
            counts[ChunkState(str(row["state"]))] = int(row["amount"])
        expected_frames = int(job["frame_end"]) - int(job["frame_start"]) + 1
        complete = (
            not bool(job["cancelled"])
            and conflicts == 0
            and published == expected_frames
            and sum(counts.values()) == counts[ChunkState.COMPLETE]
        )
        return JobStatus(job_id, bool(job["cancelled"]), counts, published, conflicts, complete)

    def chunk_state(self, job_id: str, chunk_id: str) -> ChunkState:
        with self._lock:
            row = self._connection.execute(
                "SELECT state FROM chunks WHERE job_id = ? AND chunk_id = ?",
                (job_id, chunk_id),
            ).fetchone()
        if row is None:
            raise SchedulerError("unknown chunk")
        return ChunkState(str(row["state"]))

    def publication(self, job_id: str, frame: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM frame_publications WHERE job_id = ? AND frame = ?",
                (job_id, frame),
            ).fetchone()
        return dict(row) if row is not None else None

    def unresolved_conflicts(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM publication_conflicts
                WHERE job_id = ? AND resolution = 'PENDING_OPERATOR'
                ORDER BY frame, conflict_id
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def register_external_publication(
        self,
        job_id: str,
        *,
        frame: int,
        sha256: str,
        source_kind: WorkerKind,
        worker_id: str,
        chunk_id: str,
        object_key: str,
        size_bytes: int,
        now: datetime | None = None,
    ) -> str:
        """Register a validated local/imported frame before normal chunk completion.

        This is the bridge for hybrid local/cloud operation. Differing valid
        frames are never silently overwritten; both candidates are quarantined
        and the local candidate is the provisional preference.
        """

        from .manifests import safe_relative_key
        from .models import require_sha256

        digest = require_sha256(sha256, "external frame sha256")
        safe_identifier(worker_id, "worker_id")
        safe_identifier(chunk_id, "chunk_id")
        safe_relative_key(object_key)
        if size_bytes <= 0:
            raise ValueError("external frame size must be positive")
        timestamp = _epoch(now or datetime.now(UTC))
        with self._transaction() as connection:
            job = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None or not int(job["frame_start"]) <= frame <= int(job["frame_end"]):
                raise SchedulerError("external frame is outside an existing job")
            connection.execute(
                """
                INSERT OR IGNORE INTO frame_candidates(
                    job_id, frame, sha256, source_kind, worker_id, chunk_id,
                    object_key, size_bytes, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CANDIDATE', ?)
                """,
                (
                    job_id,
                    frame,
                    digest,
                    source_kind.value,
                    worker_id,
                    chunk_id,
                    object_key,
                    size_bytes,
                    timestamp,
                ),
            )
            existing = connection.execute(
                "SELECT * FROM frame_publications WHERE job_id = ? AND frame = ?",
                (job_id, frame),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO frame_publications(
                        job_id, frame, sha256, source_kind, worker_id,
                        chunk_id, object_key, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACCEPTED', ?)
                    """,
                    (
                        job_id,
                        frame,
                        digest,
                        source_kind.value,
                        worker_id,
                        chunk_id,
                        object_key,
                        timestamp,
                    ),
                )
                return "ACCEPTED"
            if str(existing["sha256"]) == digest:
                return "DUPLICATE_IDENTICAL"
            existing_kind = WorkerKind(str(existing["source_kind"]))
            prefer_incoming = (
                bool(job["prefer_local"])
                and source_kind == WorkerKind.LOCAL
                and existing_kind != WorkerKind.LOCAL
            )
            preferred_sha = digest if prefer_incoming else str(existing["sha256"])
            preferred_kind = source_kind if prefer_incoming else existing_kind
            if prefer_incoming:
                connection.execute(
                    """
                    UPDATE frame_publications
                    SET sha256 = ?, source_kind = ?, worker_id = ?, chunk_id = ?,
                        object_key = ?, status = 'QUARANTINED_CONFLICT', updated_at = ?
                    WHERE job_id = ? AND frame = ?
                    """,
                    (
                        digest,
                        source_kind.value,
                        worker_id,
                        chunk_id,
                        object_key,
                        timestamp,
                        job_id,
                        frame,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE frame_publications
                    SET status = 'QUARANTINED_CONFLICT', updated_at = ?
                    WHERE job_id = ? AND frame = ?
                    """,
                    (timestamp, job_id, frame),
                )
            connection.execute(
                "UPDATE frame_candidates SET status = 'QUARANTINED' WHERE job_id = ? AND frame = ?",
                (job_id, frame),
            )
            connection.execute(
                """
                INSERT INTO publication_conflicts(
                    job_id, frame, existing_sha256, incoming_sha256,
                    preferred_sha256, preferred_source_kind, resolution,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING_OPERATOR', ?)
                """,
                (
                    job_id,
                    frame,
                    existing["sha256"],
                    digest,
                    preferred_sha,
                    preferred_kind.value,
                    timestamp,
                ),
            )
            return "QUARANTINED_CONFLICT"

    def resolve_conflict(
        self,
        job_id: str,
        frame: int,
        chosen_sha256: str,
        *,
        now: datetime | None = None,
    ) -> None:
        from .models import require_sha256

        chosen = require_sha256(chosen_sha256, "chosen frame sha256")
        timestamp = _epoch(now or datetime.now(UTC))
        with self._transaction() as connection:
            candidate = connection.execute(
                """
                SELECT * FROM frame_candidates
                WHERE job_id = ? AND frame = ? AND sha256 = ?
                ORDER BY CASE source_kind WHEN 'LOCAL' THEN 0 ELSE 1 END, created_at
                LIMIT 1
                """,
                (job_id, frame, chosen),
            ).fetchone()
            if candidate is None:
                raise SchedulerError("chosen conflict candidate does not exist")
            connection.execute(
                """
                UPDATE frame_publications
                SET sha256 = ?, source_kind = ?, worker_id = ?, chunk_id = ?,
                    object_key = ?, status = 'ACCEPTED', updated_at = ?
                WHERE job_id = ? AND frame = ?
                """,
                (
                    candidate["sha256"],
                    candidate["source_kind"],
                    candidate["worker_id"],
                    candidate["chunk_id"],
                    candidate["object_key"],
                    timestamp,
                    job_id,
                    frame,
                ),
            )
            updated = connection.execute(
                """
                UPDATE publication_conflicts
                SET resolution = ?, resolved_at = ?
                WHERE job_id = ? AND frame = ? AND resolution = 'PENDING_OPERATOR'
                """,
                (f"OPERATOR_SELECTED:{chosen}", timestamp, job_id, frame),
            )
            if updated.rowcount < 1:
                raise SchedulerError("frame has no unresolved publication conflict")
            connection.execute(
                """
                UPDATE chunks
                SET state = ?, updated_at = ?
                WHERE job_id = ? AND state = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM publication_conflicts AS conflicts
                    WHERE conflicts.job_id = chunks.job_id
                      AND conflicts.resolution = 'PENDING_OPERATOR'
                      AND conflicts.frame BETWEEN chunks.start_frame AND chunks.end_frame
                  )
                """,
                (
                    ChunkState.COMPLETE.value,
                    timestamp,
                    job_id,
                    ChunkState.QUARANTINED.value,
                ),
            )

    def total_recorded_cost(self, job_id: str) -> Decimal:
        with self._lock:
            rows = self._connection.execute(
                "SELECT cost FROM chunks WHERE job_id = ?",
                (job_id,),
            ).fetchall()
        return sum((Decimal(str(row["cost"])) for row in rows), Decimal("0"))

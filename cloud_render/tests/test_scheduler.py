from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Thread

import pytest

from cloud_render.models import (
    ChunkLease,
    ChunkOutput,
    ChunkState,
    FrameArtifact,
    FrameRange,
    IdentityBundle,
    WorkerKind,
)
from cloud_render.scheduler import LeaseLostError, SchedulerError, SqliteScheduler


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _scheduler(path: Path) -> SqliteScheduler:
    return SqliteScheduler(path / "scheduler.sqlite3")


def _job(
    scheduler: SqliteScheduler,
    identities: IdentityBundle,
    *,
    end: int = 6,
    chunk: int = 2,
    max_attempts: int = 3,
) -> None:
    scheduler.create_job(
        "job-1",
        identities,
        FrameRange(1, end),
        frames_per_chunk=chunk,
        max_attempts=max_attempts,
        now=NOW,
    )


def _advance_to_validating(scheduler: SqliteScheduler, lease: ChunkLease) -> None:
    scheduler.transition(lease, ChunkState.RENDERING, now=NOW + timedelta(seconds=1))
    scheduler.transition(lease, ChunkState.UPLOADING, now=NOW + timedelta(seconds=2))
    scheduler.transition(lease, ChunkState.VALIDATING, now=NOW + timedelta(seconds=3))


def _output(lease: ChunkLease, *, suffix: str = "A") -> ChunkOutput:
    frames = tuple(
        FrameArtifact(
            frame,
            f"returns/{lease.chunk_id}/frame_{frame:06d}.png",
            suffix * 64,
            100 + frame,
        )
        for frame in range(lease.frame_range.start, lease.frame_range.end + 1)
    )
    return ChunkOutput(
        lease.job_id,
        lease.chunk_id,
        lease.identities,
        lease.worker_id,
        lease.worker_kind,
        frames,
        Decimal("2.5"),
        Decimal("0.01"),
    )


def test_job_chunks_are_complete_nonoverlapping_and_pending(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    with _scheduler(tmp_path) as scheduler:
        assert scheduler.create_job(
            "job-1",
            identities,
            FrameRange(1, 13_029),
            frames_per_chunk=150,
            now=NOW,
        ) == 87
        status = scheduler.status("job-1")
        assert status.state_counts[ChunkState.PENDING] == 87
        claimed: list[int] = []
        while lease := scheduler.claim_next(
            "job-1",
            f"worker-{len(claimed):03d}",
            WorkerKind.CLOUD,
            now=NOW,
        ):
            claimed.extend(range(lease.frame_range.start, lease.frame_range.end + 1))
        assert claimed == list(range(1, 13_030))


def test_concurrent_claims_are_atomic_and_distinct(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    first = _scheduler(tmp_path)
    _job(first, identities, end=4, chunk=2)
    second = SqliteScheduler(first.database)
    barrier = Barrier(2)
    leases: list[ChunkLease | None] = []

    def claim(scheduler: SqliteScheduler, worker: str) -> None:
        barrier.wait()
        leases.append(scheduler.claim_next("job-1", worker, WorkerKind.CLOUD, now=NOW))

    threads = [Thread(target=claim, args=(first, "worker-a")), Thread(target=claim, args=(second, "worker-b"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    first.close()
    second.close()
    assert len(leases) == 2
    assert None not in leases
    assert len({lease.chunk_id for lease in leases if lease is not None}) == 2


def test_lease_expiry_retries_then_fails_at_attempt_limit(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    with _scheduler(tmp_path) as scheduler:
        _job(scheduler, identities, end=1, chunk=1, max_attempts=1)
        lease = scheduler.claim_next(
            "job-1",
            "worker-a",
            WorkerKind.CLOUD,
            lease_seconds=10,
            now=NOW,
        )
        assert lease is not None
        assert scheduler.recover_expired_leases("job-1", now=NOW + timedelta(seconds=11)) == 1
        assert scheduler.chunk_state("job-1", lease.chunk_id) == ChunkState.FAILED
        with pytest.raises(LeaseLostError):
            scheduler.renew_lease(lease, now=NOW + timedelta(seconds=11))


def test_explicit_retry_failure_respects_attempt_limit(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    with _scheduler(tmp_path) as scheduler:
        _job(scheduler, identities, end=1, chunk=1, max_attempts=1)
        lease = scheduler.claim_next("job-1", "worker-a", WorkerKind.CLOUD, now=NOW)
        assert lease is not None
        scheduler.transition(
            lease,
            ChunkState.RETRYABLE,
            error="render failed",
            now=NOW + timedelta(seconds=1),
        )
        assert scheduler.chunk_state("job-1", lease.chunk_id) == ChunkState.FAILED


def test_heartbeat_renews_active_lease(tmp_path: Path, identities: IdentityBundle) -> None:
    with _scheduler(tmp_path) as scheduler:
        _job(scheduler, identities, end=1, chunk=1)
        lease = scheduler.claim_next("job-1", "worker-a", WorkerKind.CLOUD, now=NOW)
        assert lease is not None
        scheduler.transition(lease, ChunkState.RENDERING, now=NOW + timedelta(seconds=1))
        expiry = scheduler.heartbeat(
            lease,
            ChunkState.RENDERING,
            metadata={"lastFrame": 1},
            lease_seconds=100,
            now=NOW + timedelta(seconds=2),
        )
        assert expiry == NOW + timedelta(seconds=102)


def test_invalid_transition_and_wrong_token_are_rejected(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    with _scheduler(tmp_path) as scheduler:
        _job(scheduler, identities, end=1, chunk=1)
        lease = scheduler.claim_next("job-1", "worker-a", WorkerKind.CLOUD, now=NOW)
        assert lease is not None
        with pytest.raises(SchedulerError, match="invalid"):
            scheduler.transition(lease, ChunkState.UPLOADING, now=NOW + timedelta(seconds=1))
        stolen = ChunkLease(
            lease.job_id,
            lease.chunk_id,
            lease.frame_range,
            lease.identities,
            lease.worker_id,
            lease.worker_kind,
            "stolen-token",
            lease.lease_expires_at,
            lease.attempt_count,
        )
        with pytest.raises(LeaseLostError):
            scheduler.renew_lease(stolen, now=NOW + timedelta(seconds=1))


def test_validated_chunk_publishes_once_and_completes_job(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    with _scheduler(tmp_path) as scheduler:
        _job(scheduler, identities, end=2, chunk=2)
        lease = scheduler.claim_next("job-1", "worker-a", WorkerKind.CLOUD, now=NOW)
        assert lease is not None
        _advance_to_validating(scheduler, lease)
        result = scheduler.complete_chunk(lease, _output(lease), now=NOW + timedelta(seconds=4))
        assert result.state == ChunkState.COMPLETE
        assert result.published_frames == (1, 2)
        assert scheduler.status("job-1").complete is True
        assert scheduler.total_recorded_cost("job-1") == Decimal("0.01")


def test_hybrid_different_frame_is_quarantined_with_local_preference(
    tmp_path: Path,
    identities: IdentityBundle,
) -> None:
    with _scheduler(tmp_path) as scheduler:
        _job(scheduler, identities, end=1, chunk=1)
        lease = scheduler.claim_next("job-1", "cloud-a", WorkerKind.CLOUD, now=NOW)
        assert lease is not None
        _advance_to_validating(scheduler, lease)
        assert scheduler.register_external_publication(
            "job-1",
            frame=1,
            sha256="D" * 64,
            source_kind=WorkerKind.LOCAL,
            worker_id="local-a",
            chunk_id="local-000001",
            object_key="local/frame_000001.png",
            size_bytes=100,
            now=NOW + timedelta(seconds=3),
        ) == "ACCEPTED"
        result = scheduler.complete_chunk(
            lease,
            _output(lease, suffix="C"),
            now=NOW + timedelta(seconds=4),
        )
        assert result.state == ChunkState.QUARANTINED
        assert result.quarantined_conflicts == (1,)
        publication = scheduler.publication("job-1", 1)
        assert publication is not None
        assert publication["sha256"] == "D" * 64
        assert publication["status"] == "QUARANTINED_CONFLICT"
        scheduler.resolve_conflict("job-1", 1, "D" * 64, now=NOW + timedelta(seconds=5))
        assert scheduler.chunk_state("job-1", lease.chunk_id) == ChunkState.COMPLETE
        assert scheduler.status("job-1").complete is True


def test_cancelled_job_issues_no_new_lease(tmp_path: Path, identities: IdentityBundle) -> None:
    with _scheduler(tmp_path) as scheduler:
        _job(scheduler, identities)
        scheduler.cancel_job("job-1")
        assert scheduler.is_cancelled("job-1") is True
        assert scheduler.claim_next("job-1", "worker-a", WorkerKind.CLOUD, now=NOW) is None

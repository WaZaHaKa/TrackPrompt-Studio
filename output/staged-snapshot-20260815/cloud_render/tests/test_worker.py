from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cloud_render.models import ChunkState, FrameRange, IdentityBundle, WorkerKind
from cloud_render.scheduler import SqliteScheduler
from cloud_render.storage import FilesystemStorage
from cloud_render.worker import MockRenderRuntime, WorkerConfig, WorkerService
from cloud_render.worker.core import RenderedFrame, WorkerError, WorkerOutcome


NOW = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class AdvancingRuntime(MockRenderRuntime):
    def __init__(self, identities: IdentityBundle, clock: FakeClock) -> None:
        super().__init__(identities)
        self.clock = clock

    def render(
        self,
        lease: Any,
        output_directory: Path,
        progress: Any,
        cancelled: Any,
    ) -> list[RenderedFrame]:
        result: list[RenderedFrame] = []
        for frame in range(lease.frame_range.start, lease.frame_range.end + 1):
            self.clock.sleep(50)
            path = output_directory / f"frame_{frame:06d}.png"
            from cloud_render.worker.mock import _write_png

            _write_png(path, 16, 16, 8, frame)
            progress(frame)
            result.append(RenderedFrame(frame, path, 16, 16, 8, "PNG"))
        return result


class CorruptRuntime(MockRenderRuntime):
    def render(self, *args: Any, **kwargs: Any) -> list[RenderedFrame]:
        frames = super().render(*args, **kwargs)
        frames[0].path.write_bytes(b"not-a-png")
        return frames


def _scheduler(tmp_path: Path, identities: IdentityBundle, *, end: int = 2) -> SqliteScheduler:
    scheduler = SqliteScheduler(tmp_path / "scheduler.sqlite3")
    scheduler.create_job(
        "job-1",
        identities,
        FrameRange(1, end),
        frames_per_chunk=end,
        now=NOW,
    )
    return scheduler


def test_mock_worker_verifies_uploads_and_completes_chunk(
    tmp_path: Path,
    identities: IdentityBundle,
    package_manifest: object,
) -> None:
    scheduler = _scheduler(tmp_path, identities)
    storage = FilesystemStorage(tmp_path / "objects")
    worker = WorkerService(
        WorkerConfig("job-1", "worker-a"),
        package_manifest(frameRange={"start": 1, "end": 2}),
        scheduler,
        storage,
        MockRenderRuntime(identities),
        clock=lambda: NOW,
    )
    result = worker.run_once()
    assert result.outcome == WorkerOutcome.COMPLETED_CHUNK
    assert result.frame_count == 2
    assert scheduler.status("job-1").complete is True
    assert len(storage.list("jobs/job-1/attempts")) == 3
    scheduler.close()


def test_worker_rejects_unknown_gpu_or_software_rendering(
    tmp_path: Path,
    identities: IdentityBundle,
    package_manifest: object,
) -> None:
    scheduler = _scheduler(tmp_path, identities)
    with pytest.raises(WorkerError, match="GPU"):
        WorkerService(
            WorkerConfig("job-1", "worker-a"),
            package_manifest(frameRange={"start": 1, "end": 2}),
            scheduler,
            FilesystemStorage(tmp_path / "objects"),
            MockRenderRuntime(identities, gpu_visible=False),
        )
    scheduler.close()


def test_worker_rejects_runtime_identity_or_blender_drift(
    tmp_path: Path,
    identities: IdentityBundle,
    package_manifest: object,
) -> None:
    scheduler = _scheduler(tmp_path, identities)
    with pytest.raises(WorkerError, match="Blender"):
        WorkerService(
            WorkerConfig("job-1", "worker-a"),
            package_manifest(frameRange={"start": 1, "end": 2}),
            scheduler,
            FilesystemStorage(tmp_path / "objects"),
            MockRenderRuntime(identities, blender_version="5.1.0"),
        )
    wrong = IdentityBundle("D" * 64, "E" * 64, "F" * 64)
    with pytest.raises(WorkerError, match="hashes"):
        WorkerService(
            WorkerConfig("job-1", "worker-b"),
            package_manifest(frameRange={"start": 1, "end": 2}),
            scheduler,
            FilesystemStorage(tmp_path / "objects-2"),
            MockRenderRuntime(wrong),
        )
    scheduler.close()


def test_worker_rejects_corrupt_png_before_upload(
    tmp_path: Path,
    identities: IdentityBundle,
    package_manifest: object,
) -> None:
    scheduler = _scheduler(tmp_path, identities)
    worker = WorkerService(
        WorkerConfig("job-1", "worker-a"),
        package_manifest(frameRange={"start": 1, "end": 2}),
        scheduler,
        FilesystemStorage(tmp_path / "objects"),
        CorruptRuntime(identities),
        clock=lambda: NOW,
    )
    result = worker.run_once()
    assert result.outcome == WorkerOutcome.FAILED
    assert "PNG" in result.reason
    assert storage_is_empty(tmp_path / "objects")
    scheduler.close()


def storage_is_empty(root: Path) -> bool:
    return not root.exists() or not any(path.is_file() for path in root.rglob("*"))


def test_worker_heartbeat_renews_lease_during_long_chunk(
    tmp_path: Path,
    identities: IdentityBundle,
    package_manifest: object,
) -> None:
    clock = FakeClock()
    scheduler = _scheduler(tmp_path, identities)
    worker = WorkerService(
        WorkerConfig(
            "job-1",
            "worker-a",
            WorkerKind.CLOUD,
            lease_seconds=60,
            heartbeat_seconds=10,
        ),
        package_manifest(frameRange={"start": 1, "end": 2}),
        scheduler,
        FilesystemStorage(tmp_path / "objects"),
        AdvancingRuntime(identities, clock),
        clock=clock,
    )
    assert worker.run_once().outcome == WorkerOutcome.COMPLETED_CHUNK
    scheduler.close()


def test_worker_cancellation_claims_no_work(
    tmp_path: Path,
    identities: IdentityBundle,
    package_manifest: object,
) -> None:
    scheduler = _scheduler(tmp_path, identities)
    scheduler.cancel_job("job-1")
    worker = WorkerService(
        WorkerConfig("job-1", "worker-a"),
        package_manifest(frameRange={"start": 1, "end": 2}),
        scheduler,
        FilesystemStorage(tmp_path / "objects"),
        MockRenderRuntime(identities),
    )
    assert worker.run_once().outcome == WorkerOutcome.CANCELLED
    scheduler.close()


def test_worker_stops_after_bounded_no_work_timeout(
    tmp_path: Path,
    identities: IdentityBundle,
    package_manifest: object,
) -> None:
    clock = FakeClock()
    scheduler = _scheduler(tmp_path, identities, end=1)
    lease = scheduler.claim_next("job-1", "finished", WorkerKind.CLOUD, now=NOW)
    assert lease is not None
    scheduler.transition(lease, ChunkState.FAILED, now=NOW)
    runtime = MockRenderRuntime(identities)
    worker = WorkerService(
        WorkerConfig(
            "job-1",
            "worker-a",
            no_work_timeout_seconds=10,
            poll_seconds=5,
        ),
        package_manifest(frameRange={"start": 1, "end": 1}),
        scheduler,
        FilesystemStorage(tmp_path / "objects"),
        runtime,
        clock=clock,
        sleep=clock.sleep,
    )
    results = worker.run_until_idle()
    assert all(item.outcome == WorkerOutcome.NO_WORK for item in results)
    assert runtime.shutdown_reasons == ["no-work-timeout"]
    scheduler.close()

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from ..frame_validation import FrameValidationError, validate_image
from ..manifests import (
    CHUNK_OUTPUT_KIND,
    SCHEMA_VERSION,
    seal_manifest,
    validate_package_manifest,
)
from ..models import (
    ChunkLease,
    ChunkOutput,
    ChunkState,
    FrameArtifact,
    IdentityBundle,
    WorkerKind,
)
from ..scheduler import LeaseLostError, SchedulerError
from ..storage import ObjectStorage
from ..storage.base import sha256_path


class WorkerError(RuntimeError):
    pass


class WorkerCancelled(WorkerError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    blender_version: str
    gpu_name: str
    gpu_visible: bool
    software_rendering: bool
    identities: IdentityBundle


@dataclass(frozen=True, slots=True)
class RenderedFrame:
    frame: int
    path: Path
    width: int
    height: int
    bit_depth: int
    image_format: str


class RenderRuntime(Protocol):
    def inspect(self, package_manifest: dict[str, Any]) -> RuntimeInfo: ...

    def render(
        self,
        lease: ChunkLease,
        output_directory: Path,
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
    ) -> Iterable[RenderedFrame]: ...

    def shutdown(self, reason: str) -> None: ...


class LeaseCoordinator(Protocol):
    def claim_next(
        self,
        job_id: str,
        worker_id: str,
        worker_kind: WorkerKind,
        *,
        lease_seconds: int = 900,
        now: datetime | None = None,
    ) -> ChunkLease | None: ...

    def heartbeat(
        self,
        lease: ChunkLease,
        state: ChunkState,
        *,
        metadata: dict[str, Any] | None = None,
        lease_seconds: int = 900,
        now: datetime | None = None,
    ) -> datetime: ...

    def transition(
        self,
        lease: ChunkLease,
        target: ChunkState,
        *,
        error: str | None = None,
        now: datetime | None = None,
    ) -> None: ...

    def complete_chunk(
        self,
        lease: ChunkLease,
        output: ChunkOutput,
        *,
        now: datetime | None = None,
    ) -> Any: ...

    def is_cancelled(self, job_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    job_id: str
    worker_id: str
    worker_kind: WorkerKind = WorkerKind.CLOUD
    lease_seconds: int = 900
    heartbeat_seconds: int = 60
    poll_seconds: float = 5.0
    no_work_timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.job_id or not self.worker_id:
            raise ValueError("worker job_id and worker_id are required")
        if self.lease_seconds < 5 or not 1 <= self.heartbeat_seconds < self.lease_seconds:
            raise ValueError("heartbeat interval must be positive and shorter than the lease")
        if self.poll_seconds < 0 or self.no_work_timeout_seconds < 0:
            raise ValueError("poll and idle timeouts cannot be negative")


class WorkerOutcome(StrEnum):
    COMPLETED_CHUNK = "COMPLETED_CHUNK"
    NO_WORK = "NO_WORK"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class WorkerResult:
    outcome: WorkerOutcome
    chunk_id: str | None = None
    frame_count: int = 0
    reason: str = ""


class WorkerService:
    """Provider-neutral pull worker. It never encodes or accesses source audio."""

    def __init__(
        self,
        config: WorkerConfig,
        package_manifest: dict[str, Any],
        coordinator: LeaseCoordinator,
        storage: ObjectStorage,
        runtime: RenderRuntime,
        *,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self.package_manifest = package_manifest
        self.identities, self.frame_range = validate_package_manifest(package_manifest)
        self.coordinator = coordinator
        self.storage = storage
        self.runtime = runtime
        self.clock = clock or (lambda: datetime.now(UTC))
        if sleep is None:
            import time

            sleep = time.sleep
        self.sleep = sleep
        self._verify_runtime(runtime.inspect(package_manifest))

    def _verify_runtime(self, info: RuntimeInfo) -> None:
        required_version = str(self.package_manifest.get("blenderVersion", ""))
        if info.blender_version != required_version:
            raise WorkerError("Blender version differs from the package contract")
        if not info.gpu_visible or info.software_rendering:
            raise WorkerError("headless EEVEE GPU validation failed or software rendering was detected")
        if info.identities != self.identities:
            raise WorkerError("runtime scene/profile/package hashes differ from the package contract")

    def _cancelled(self) -> bool:
        return self.coordinator.is_cancelled(self.config.job_id)

    def _frame_contract(self) -> tuple[int, int, int, str, str]:
        resolution = self.package_manifest.get("resolution")
        image = self.package_manifest.get("image")
        if not isinstance(resolution, dict) or not isinstance(image, dict):
            raise WorkerError("package lacks resolved resolution or image settings")
        return (
            int(resolution.get("width", 0)),
            int(resolution.get("height", 0)),
            int(image.get("bitDepth", 0)),
            str(image.get("format", "")).upper(),
            str(image.get("extension", "")).lower().lstrip("."),
        )

    def run_once(self) -> WorkerResult:
        if self._cancelled():
            return WorkerResult(WorkerOutcome.CANCELLED, reason="job-cancelled")
        now = self.clock()
        lease = self.coordinator.claim_next(
            self.config.job_id,
            self.config.worker_id,
            self.config.worker_kind,
            lease_seconds=self.config.lease_seconds,
            now=now,
        )
        if lease is None:
            return WorkerResult(WorkerOutcome.NO_WORK, reason="no-claimable-chunk")
        started = self.clock()
        last_heartbeat = started

        def heartbeat(state: ChunkState, frame: int | None = None) -> None:
            nonlocal last_heartbeat
            current = self.clock()
            if current - last_heartbeat >= timedelta(seconds=self.config.heartbeat_seconds):
                self.coordinator.heartbeat(
                    lease,
                    state,
                    metadata={"lastFrame": frame} if frame is not None else {},
                    lease_seconds=self.config.lease_seconds,
                    now=current,
                )
                last_heartbeat = current

        def progress(frame: int) -> None:
            if self._cancelled():
                raise WorkerCancelled("job cancelled while rendering")
            heartbeat(ChunkState.RENDERING, frame)

        try:
            if lease.identities != self.identities:
                raise WorkerError("scheduler lease identity differs from the package")
            supplied_manifest_sha = str(
                self.package_manifest.get("manifestSha256", "")
            )
            if (
                lease.manifest_sha256 is not None
                and supplied_manifest_sha != lease.manifest_sha256
            ):
                raise WorkerError("scheduler lease is bound to another cloud manifest")
            self.coordinator.transition(lease, ChunkState.RENDERING, now=started)
            with tempfile.TemporaryDirectory(prefix="trackprompt-cloud-worker-") as temporary:
                output_directory = Path(temporary)
                rendered = list(
                    self.runtime.render(lease, output_directory, progress, self._cancelled)
                )
                expected_frames = set(range(lease.frame_range.start, lease.frame_range.end + 1))
                if {item.frame for item in rendered} != expected_frames:
                    raise WorkerError("runtime did not return exactly the leased frame set")
                width, height, bit_depth, image_format, extension = self._frame_contract()
                self.coordinator.transition(lease, ChunkState.UPLOADING, now=self.clock())
                artifacts: list[FrameArtifact] = []
                attempt_id = hashlib.sha256(lease.lease_token.encode("utf-8")).hexdigest()[:16]
                for item in sorted(rendered, key=lambda candidate: candidate.frame):
                    if self._cancelled():
                        raise WorkerCancelled("job cancelled while uploading")
                    if (
                        item.width,
                        item.height,
                        item.bit_depth,
                        item.image_format.upper(),
                    ) != (width, height, bit_depth, image_format):
                        raise WorkerError(f"frame {item.frame} violates the resolved image contract")
                    if item.path.suffix.lower() != f".{extension}" or not item.path.is_file():
                        raise WorkerError(f"frame {item.frame} has a noncanonical extension or is absent")
                    expected_name = f"frame_{item.frame:06d}.{extension}"
                    if item.path.name != expected_name or item.path.stat().st_size <= 0:
                        raise WorkerError(f"frame {item.frame} has a noncanonical or empty output file")
                    header = validate_image(item.path, image_format)
                    if (header.width, header.height, header.bit_depth, header.image_format) != (
                        width,
                        height,
                        bit_depth,
                        image_format,
                    ):
                        raise WorkerError(
                            f"frame {item.frame} header violates the resolved image contract"
                        )
                    key = (
                        f"jobs/{lease.job_id}/attempts/{attempt_id}/{lease.chunk_id}/"
                        f"frames/{expected_name}"
                    )
                    metadata = self.storage.put_file(key, item.path, if_absent=True)
                    if metadata.sha256 != sha256_path(item.path):
                        raise WorkerError("uploaded frame hash differs from the local validated frame")
                    artifacts.append(
                        FrameArtifact(item.frame, key, metadata.sha256, metadata.size_bytes)
                    )
                    heartbeat(ChunkState.UPLOADING, item.frame)
                self.coordinator.transition(lease, ChunkState.VALIDATING, now=self.clock())
                wall_seconds = Decimal(str((self.clock() - started).total_seconds()))
                manifest = seal_manifest(
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "kind": CHUNK_OUTPUT_KIND,
                        "jobId": lease.job_id,
                        "chunkId": lease.chunk_id,
                        "sceneSha256": lease.identities.scene_sha256,
                        "profileSha256": lease.identities.profile_sha256,
                        "packageSha256": lease.identities.package_sha256,
                        "workerId": lease.worker_id,
                        "workerKind": lease.worker_kind.value,
                        "privateAudioUsed": False,
                        "encodingPerformed": False,
                        "frames": [
                            {
                                "frame": item.frame,
                                "objectKey": item.object_key,
                                "sha256": item.sha256,
                                "sizeBytes": item.size_bytes,
                            }
                            for item in artifacts
                        ],
                        "wallSeconds": str(wall_seconds),
                    }
                )
                manifest_key = (
                    f"jobs/{lease.job_id}/attempts/{attempt_id}/{lease.chunk_id}/"
                    "chunk-output-manifest.json"
                )
                uploaded_manifest = self.storage.put_bytes(
                    manifest_key,
                    json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
                    if_absent=True,
                )
                output = ChunkOutput(
                    job_id=lease.job_id,
                    chunk_id=lease.chunk_id,
                    identities=lease.identities,
                    worker_id=lease.worker_id,
                    worker_kind=lease.worker_kind,
                    frames=tuple(artifacts),
                    wall_seconds=wall_seconds,
                    metadata={
                        "manifestObjectKey": manifest_key,
                        "manifestObjectSha256": uploaded_manifest.sha256,
                    },
                )
                self.coordinator.complete_chunk(lease, output, now=self.clock())
                return WorkerResult(
                    WorkerOutcome.COMPLETED_CHUNK,
                    lease.chunk_id,
                    len(artifacts),
                    "validated-upload-complete",
                )
        except WorkerCancelled as exc:
            try:
                self.coordinator.transition(
                    lease,
                    ChunkState.RETRYABLE,
                    error=str(exc),
                    now=self.clock(),
                )
            except LeaseLostError:
                pass
            return WorkerResult(WorkerOutcome.CANCELLED, lease.chunk_id, reason=str(exc))
        except (LeaseLostError, WorkerError, FrameValidationError, OSError, ValueError) as exc:
            try:
                self.coordinator.transition(
                    lease,
                    ChunkState.RETRYABLE,
                    error=str(exc),
                    now=self.clock(),
                )
            except (LeaseLostError, SchedulerError):
                pass
            return WorkerResult(WorkerOutcome.FAILED, lease.chunk_id, reason=str(exc))

    def run_until_idle(self) -> list[WorkerResult]:
        results: list[WorkerResult] = []
        idle_since = self.clock()
        while True:
            result = self.run_once()
            results.append(result)
            if result.outcome == WorkerOutcome.CANCELLED:
                self.runtime.shutdown("cancelled")
                return results
            if result.outcome == WorkerOutcome.NO_WORK:
                if (self.clock() - idle_since).total_seconds() >= self.config.no_work_timeout_seconds:
                    self.runtime.shutdown("no-work-timeout")
                    return results
                self.sleep(self.config.poll_seconds)
                continue
            idle_since = self.clock()

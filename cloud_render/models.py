from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping

_SHA256 = re.compile(r"^[A-Fa-f0-9]{64}$")


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_sha256(value: str, label: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a complete SHA-256 value")
    return value.upper()


class ChunkState(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    RENDERING = "RENDERING"
    UPLOADING = "UPLOADING"
    VALIDATING = "VALIDATING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    RETRYABLE = "RETRYABLE"
    QUARANTINED = "QUARANTINED"


class WorkerKind(StrEnum):
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"


@dataclass(frozen=True, slots=True)
class IdentityBundle:
    scene_sha256: str
    profile_sha256: str
    package_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_sha256", require_sha256(self.scene_sha256, "scene_sha256"))
        object.__setattr__(self, "profile_sha256", require_sha256(self.profile_sha256, "profile_sha256"))
        object.__setattr__(self, "package_sha256", require_sha256(self.package_sha256, "package_sha256"))


@dataclass(frozen=True, slots=True)
class FrameRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1 or self.end < self.start:
            raise ValueError("frame range must be positive and ordered")

    @property
    def count(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True, slots=True)
class GpuOffer:
    provider: str
    offer_id: str
    gpu_name: str
    region: str
    hourly_price: Decimal
    vram_gib: Decimal | None = None
    available: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.offer_id.strip() or not self.gpu_name.strip():
            raise ValueError("provider, offer_id, and gpu_name are required")
        if self.hourly_price < 0:
            raise ValueError("hourly_price cannot be negative")
        if self.vram_gib is not None and self.vram_gib <= 0:
            raise ValueError("vram_gib must be positive when supplied")


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    offer: GpuOffer
    seconds_per_frame: Decimal
    p90_seconds_per_frame: Decimal
    validated_frames: int
    boot_seconds: Decimal = Decimal("0")
    upload_seconds: Decimal = Decimal("0")
    visual_passed: bool = False
    technical_passed: bool = False
    software_rendering: bool = False
    stable: bool = True
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.seconds_per_frame <= 0 or self.p90_seconds_per_frame <= 0:
            raise ValueError("benchmark frame timings must be positive")
        if self.validated_frames < 1:
            raise ValueError("validated_frames must be positive")
        if self.boot_seconds < 0 or self.upload_seconds < 0:
            raise ValueError("benchmark overhead cannot be negative")

    @property
    def eligible(self) -> bool:
        return (
            self.offer.available
            and self.visual_passed
            and self.technical_passed
            and not self.software_rendering
            and self.stable
        )


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    max_hourly_price_per_worker: Decimal
    max_worker_count: int
    total_budget_ceiling: Decimal
    deadline_utc: datetime | None = None
    warning_fraction: Decimal = Decimal("0.80")

    def __post_init__(self) -> None:
        if self.max_hourly_price_per_worker <= 0:
            raise ValueError("max_hourly_price_per_worker must be positive")
        if self.max_worker_count < 1:
            raise ValueError("max_worker_count must be positive")
        if self.total_budget_ceiling <= 0:
            raise ValueError("total_budget_ceiling must be positive")
        if not Decimal("0") < self.warning_fraction < Decimal("1"):
            raise ValueError("warning_fraction must be between zero and one")
        if self.deadline_utc is not None and self.deadline_utc.tzinfo is None:
            raise ValueError("deadline_utc must be timezone-aware")


@dataclass(frozen=True, slots=True)
class FrameArtifact:
    frame: int
    object_key: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if self.frame < 1:
            raise ValueError("frame must be positive")
        if not self.object_key or self.object_key.startswith(("/", "\\")) or ".." in self.object_key.split("/"):
            raise ValueError("object_key must be a safe relative key")
        object.__setattr__(self, "sha256", require_sha256(self.sha256, "frame sha256"))
        if self.size_bytes <= 0:
            raise ValueError("size_bytes must be positive")


@dataclass(frozen=True, slots=True)
class ChunkLease:
    job_id: str
    chunk_id: str
    frame_range: FrameRange
    identities: IdentityBundle
    worker_id: str
    worker_kind: WorkerKind
    lease_token: str
    lease_expires_at: datetime
    attempt_count: int
    manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id or not self.chunk_id or not self.worker_id or not self.lease_token:
            raise ValueError("lease identifiers are required")
        if self.lease_expires_at.tzinfo is None:
            raise ValueError("lease expiry must be timezone-aware")
        if self.attempt_count < 1:
            raise ValueError("attempt_count must be positive")
        if self.manifest_sha256 is not None:
            object.__setattr__(
                self,
                "manifest_sha256",
                require_sha256(self.manifest_sha256, "manifest_sha256"),
            )


@dataclass(frozen=True, slots=True)
class ChunkOutput:
    job_id: str
    chunk_id: str
    identities: IdentityBundle
    worker_id: str
    worker_kind: WorkerKind
    frames: tuple[FrameArtifact, ...]
    wall_seconds: Decimal
    cost: Decimal = Decimal("0")
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.frames:
            raise ValueError("chunk output must contain frames")
        if self.wall_seconds < 0 or self.cost < 0:
            raise ValueError("timing and cost cannot be negative")
        frame_numbers = [item.frame for item in self.frames]
        if len(frame_numbers) != len(set(frame_numbers)):
            raise ValueError("chunk output contains duplicate frames")

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Protocol, Self, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

RENDER_CONTRACT_SCHEMA_VERSION: Literal["2.0.0"] = "2.0.0"

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
ArtifactKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]


def _validate_artifact_key(value: str) -> str:
    """Validate a content-store key without accepting host filesystem paths."""
    if "\x00" in value or "\\" in value:
        raise ValueError("artifact keys must be portable POSIX-style relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact keys must be normalized relative paths")
    if path.parts and ":" in path.parts[0]:
        raise ValueError("artifact keys must not contain a drive or URI scheme")
    return path.as_posix()


def _artifact_roots_are_isolated(roots: tuple[str, ...]) -> bool:
    paths = tuple(PurePosixPath(root) for root in roots)
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                return False
    return True


class RenderContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    schema_version: Literal["2.0.0"] = RENDER_CONTRACT_SCHEMA_VERSION


class ImmutableRenderContractModel(RenderContractModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class RenderStage(StrEnum):
    INPUT_VERIFICATION = "input-verification"
    STORY_COMPILATION = "story-compilation"
    SCENE_BUILD = "scene-build"
    ASSET_PREPARATION = "asset-preparation"
    CACHE_BAKE = "cache-bake"
    CALIBRATION = "calibration"
    RENDERING = "rendering"
    FRAME_VALIDATION = "frame-validation"
    ENCODING = "encoding"
    FINAL_QA = "final-qa"
    PUBLICATION = "publication"


class ProgressState(StrEnum):
    PENDING = "pending"
    CALIBRATING = "calibrating"
    INDETERMINATE = "indeterminate"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactState(StrEnum):
    RENDERED = "rendered"
    VALIDATED = "validated"
    SAFE = "safe"
    REJECTED = "rejected"


class TaskState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QaStatus(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class WorkerKind(StrEnum):
    LOCAL_CPU = "local-cpu"
    LOCAL_GPU = "local-gpu"
    REMOTE_CPU = "remote-cpu"
    REMOTE_GPU = "remote-gpu"


class RenderEventType(StrEnum):
    FRAME_STARTED = "frame_started"
    FRAME_WRITTEN = "frame_written"
    FRAME_VALIDATED = "frame_validated"
    CHUNK_PUBLISHED = "chunk_published"
    RENDER_STATS = "render_stats"
    RENDER_FAILED = "render_failed"
    RENDER_CANCELLED = "render_cancelled"


class TenantRef(ImmutableRenderContractModel):
    namespace: Identifier
    deployment_id: Identifier | None = None


class ProjectRef(ImmutableRenderContractModel):
    tenant: TenantRef
    project_id: Identifier
    revision: str | None = Field(default=None, min_length=1, max_length=200)


class PackageIdentity(ImmutableRenderContractModel):
    package_id: Identifier
    package_sha256: Sha256Digest
    source_revision: str = Field(min_length=1, max_length=200)
    source_hashes: dict[Identifier, Sha256Digest] = Field(default_factory=dict)
    tool_versions: dict[Identifier, str] = Field(default_factory=dict)

    @field_validator("source_hashes", "tool_versions")
    @classmethod
    def validate_identity_keys(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            return value
        if len(value) != len(set(value)):
            raise ValueError("identity keys must be unique")
        return dict(sorted(value.items()))


class OutputVariantMatrixIdentity(ImmutableRenderContractModel):
    matrix_id: Identifier
    matrix_sha256: Sha256Digest
    package_sha256: Sha256Digest
    enabled_variant_ids: tuple[Identifier, ...]
    variant_sha256_by_id: dict[Identifier, Sha256Digest]

    @model_validator(mode="after")
    def validate_enabled_variant_identity(self) -> Self:
        if not self.enabled_variant_ids:
            raise ValueError("an output matrix must enable at least one variant")
        if len(self.enabled_variant_ids) != len(set(self.enabled_variant_ids)):
            raise ValueError("enabled variant IDs must be unique")
        if set(self.variant_sha256_by_id) != set(self.enabled_variant_ids):
            raise ValueError("variant hashes must exactly match enabled variant IDs")
        return self


class CompositionProfile(ImmutableRenderContractModel):
    id: Identifier
    revision: str = Field(min_length=1, max_length=200)
    composition_mode: Literal["authored"] = "authored"
    scene_sha256: Sha256Digest
    camera_sha256: Sha256Digest
    composition_sha256: Sha256Digest
    override_sha256: Sha256Digest | None = None


class StageProgress(RenderContractModel):
    stage: RenderStage
    state: ProgressState = ProgressState.PENDING
    completed_units: float | None = Field(default=None, ge=0)
    total_units: float | None = Field(default=None, ge=0)
    unit: str = Field(default="items", min_length=1, max_length=40)
    throughput_per_second: float | None = Field(default=None, gt=0)
    elapsed_seconds: float = Field(default=0, ge=0)
    eta_p50_seconds: float | None = Field(default=None, ge=0)
    eta_p90_seconds: float | None = Field(default=None, ge=0)
    started_at: datetime | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        if self.total_units is not None and self.completed_units is None:
            raise ValueError("completed units are required when total units are known")
        if (
            self.completed_units is not None
            and self.total_units is not None
            and self.completed_units > self.total_units
        ):
            raise ValueError("completed units cannot exceed total units")
        if (
            self.state is ProgressState.COMPLETE
            and self.total_units is not None
            and self.completed_units != self.total_units
        ):
            raise ValueError("a complete stage must report all known units complete")
        if (
            self.eta_p50_seconds is not None
            and self.eta_p90_seconds is not None
            and self.eta_p90_seconds < self.eta_p50_seconds
        ):
            raise ValueError("P90 ETA cannot be lower than P50 ETA")
        if self.started_at is not None and self.updated_at < self.started_at:
            raise ValueError("updated_at cannot precede started_at")
        return self

    @property
    def completion_fraction(self) -> float | None:
        if self.completed_units is None or self.total_units is None:
            return None
        if self.total_units == 0:
            return 1.0 if self.state is ProgressState.COMPLETE else None
        return self.completed_units / self.total_units


class FrameArtifact(ImmutableRenderContractModel):
    id: Identifier
    job_id: Identifier
    output_variant_id: Identifier
    frame_number: int = Field(ge=0)
    artifact_key: ArtifactKey
    sha256: Sha256Digest
    byte_size: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    state: ArtifactState
    worker_id: Identifier
    chunk_id: Identifier
    shot_id: Identifier
    created_at: datetime

    @field_validator("artifact_key")
    @classmethod
    def validate_artifact_key(cls, value: str) -> str:
        return _validate_artifact_key(value)


class PreviewArtifact(ImmutableRenderContractModel):
    id: Identifier
    job_id: Identifier
    output_variant_id: Identifier
    frame_number: int = Field(ge=0)
    source_frame_artifact_id: Identifier
    source_frame_sha256: Sha256Digest
    artifact_key: ArtifactKey
    sha256: Sha256Digest
    revision: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    created_at: datetime

    @field_validator("artifact_key")
    @classmethod
    def validate_artifact_key(cls, value: str) -> str:
        return _validate_artifact_key(value)


class StoredArtifact(ImmutableRenderContractModel):
    artifact_key: ArtifactKey
    sha256: Sha256Digest
    byte_size: int = Field(ge=0)

    @field_validator("artifact_key")
    @classmethod
    def validate_artifact_key(cls, value: str) -> str:
        return _validate_artifact_key(value)


@runtime_checkable
class ArtifactStore(Protocol):
    def put_bytes(
        self,
        artifact_key: str,
        data: bytes,
        *,
        overwrite: bool = False,
    ) -> StoredArtifact: ...

    def read_bytes(self, artifact_key: str) -> bytes: ...

    def exists(self, artifact_key: str) -> bool: ...

    def delete(self, artifact_key: str) -> bool: ...


class LocalFilesystemArtifactStore:
    """Atomic local artifact adapter constrained to one configured root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def put_bytes(
        self,
        artifact_key: str,
        data: bytes,
        *,
        overwrite: bool = False,
    ) -> StoredArtifact:
        normalized_key = _validate_artifact_key(artifact_key)
        target = self._path_for(normalized_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError(f"artifact already exists: {normalized_key}")
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".partial",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists() and not overwrite:
                raise FileExistsError(f"artifact already exists: {normalized_key}")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredArtifact(
            artifact_key=normalized_key,
            sha256=hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
        )

    def read_bytes(self, artifact_key: str) -> bytes:
        return self._path_for(_validate_artifact_key(artifact_key)).read_bytes()

    def exists(self, artifact_key: str) -> bool:
        return self._path_for(_validate_artifact_key(artifact_key)).is_file()

    def delete(self, artifact_key: str) -> bool:
        target = self._path_for(_validate_artifact_key(artifact_key))
        if not target.is_file():
            return False
        target.unlink()
        return True

    def _path_for(self, artifact_key: str) -> Path:
        relative_path = PurePosixPath(artifact_key)
        candidate = self._root.joinpath(*relative_path.parts).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError("artifact key escapes the configured store root")
        return candidate


class GpuCapability(ImmutableRenderContractModel):
    device_id: Identifier
    name: str = Field(min_length=1, max_length=200)
    memory_bytes: int = Field(gt=0)
    render_engines: tuple[Identifier, ...] = ()


class WorkerCapabilities(ImmutableRenderContractModel):
    worker_id: Identifier
    kinds: tuple[WorkerKind, ...]
    logical_cpu_count: int = Field(ge=1)
    memory_bytes: int = Field(gt=0)
    gpus: tuple[GpuCapability, ...] = ()
    max_concurrent_tasks: int = Field(default=1, ge=1)
    max_width: int | None = Field(default=None, gt=0)
    max_height: int | None = Field(default=None, gt=0)
    supported_artifact_formats: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        if not self.kinds:
            raise ValueError("a worker must advertise at least one worker kind")
        if len(self.kinds) != len(set(self.kinds)):
            raise ValueError("worker kinds must be unique")
        gpu_kind = WorkerKind.LOCAL_GPU in self.kinds or WorkerKind.REMOTE_GPU in self.kinds
        if gpu_kind and not self.gpus:
            raise ValueError("GPU workers must advertise at least one GPU")
        return self


class ShotRenderTask(ImmutableRenderContractModel):
    id: Identifier
    job_id: Identifier
    output_variant_id: Identifier
    shot_id: Identifier
    chunk_id: Identifier
    frame_start: int = Field(ge=0)
    frame_end: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0, le=240)
    complexity_class: Identifier
    package_sha256: Sha256Digest
    matrix_sha256: Sha256Digest
    output_variant_sha256: Sha256Digest
    scene_sha256: Sha256Digest
    render_profile_sha256: Sha256Digest
    composition_sha256: Sha256Digest
    task_sha256: Sha256Digest
    output_root: ArtifactKey
    required_worker_kind: WorkerKind | None = None
    minimum_gpu_memory_bytes: int | None = Field(default=None, gt=0)
    attempt: int = Field(default=1, ge=1)

    @field_validator("output_root")
    @classmethod
    def validate_output_root(cls, value: str) -> str:
        return _validate_artifact_key(value)

    @model_validator(mode="after")
    def validate_frame_range(self) -> Self:
        if self.frame_end < self.frame_start:
            raise ValueError("frame_end cannot precede frame_start")
        return self

    @property
    def frame_count(self) -> int:
        return self.frame_end - self.frame_start + 1


class WorkerLease(ImmutableRenderContractModel):
    id: Identifier
    task_id: Identifier
    job_id: Identifier
    output_variant_id: Identifier
    worker_id: Identifier
    attempt: int = Field(ge=1)
    lease_token_sha256: Sha256Digest
    granted_at: datetime
    expires_at: datetime
    last_heartbeat_at: datetime | None = None

    @model_validator(mode="after")
    def validate_lease_window(self) -> Self:
        if self.expires_at <= self.granted_at:
            raise ValueError("lease expiry must follow grant time")
        if self.last_heartbeat_at is not None and not (
            self.granted_at <= self.last_heartbeat_at <= self.expires_at
        ):
            raise ValueError("heartbeat must fall inside the lease window")
        return self


class EncodeTask(RenderContractModel):
    id: Identifier
    job_id: Identifier
    output_variant_id: Identifier
    deliverable_role: Identifier
    state: TaskState = TaskState.PENDING
    manifest_sha256: Sha256Digest
    input_frames_root: ArtifactKey
    output_artifact_key: ArtifactKey
    completed_units: int = Field(default=0, ge=0)
    total_units: int = Field(ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)
    eta_p50_seconds: float | None = Field(default=None, ge=0)
    eta_p90_seconds: float | None = Field(default=None, ge=0)
    updated_at: datetime

    @field_validator("input_frames_root", "output_artifact_key")
    @classmethod
    def validate_artifact_keys(cls, value: str) -> str:
        return _validate_artifact_key(value)

    @model_validator(mode="after")
    def validate_encode_progress(self) -> Self:
        if self.completed_units > self.total_units:
            raise ValueError("encoded units cannot exceed total units")
        if self.state is TaskState.COMPLETE and self.completed_units != self.total_units:
            raise ValueError("a complete encode task must report all units complete")
        if (
            self.eta_p50_seconds is not None
            and self.eta_p90_seconds is not None
            and self.eta_p90_seconds < self.eta_p50_seconds
        ):
            raise ValueError("P90 ETA cannot be lower than P50 ETA")
        return self


class QaCheck(ImmutableRenderContractModel):
    id: Identifier
    status: QaStatus
    summary: str = Field(min_length=1, max_length=500)


class QaResult(ImmutableRenderContractModel):
    id: Identifier
    job_id: Identifier
    output_variant_id: Identifier
    status: QaStatus
    checks: tuple[QaCheck, ...]
    report_artifact_key: ArtifactKey
    report_sha256: Sha256Digest
    completed_at: datetime

    @field_validator("report_artifact_key")
    @classmethod
    def validate_report_artifact_key(cls, value: str) -> str:
        return _validate_artifact_key(value)

    @model_validator(mode="after")
    def validate_checks(self) -> Self:
        if not self.checks:
            raise ValueError("QA results must contain at least one check")
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("QA check IDs must be unique")
        if self.status is QaStatus.PASS and any(check.status is not QaStatus.PASS for check in self.checks):
            raise ValueError("a passing QA result cannot contain warnings or failures")
        return self


class OutputVariantArtifact(ImmutableRenderContractModel):
    id: Identifier
    job_id: Identifier
    output_variant_id: Identifier
    deliverable_role: Identifier
    artifact_key: ArtifactKey
    sha256: Sha256Digest
    byte_size: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0, le=240)
    created_at: datetime

    @field_validator("artifact_key")
    @classmethod
    def validate_artifact_key(cls, value: str) -> str:
        return _validate_artifact_key(value)


class OutputVariantProgress(RenderContractModel):
    output_variant_id: Identifier
    stages: tuple[StageProgress, ...] = ()
    total_frames: int = Field(default=0, ge=0)
    rendered_frames: int = Field(default=0, ge=0)
    validated_frames: int = Field(default=0, ge=0)
    current_frame: int | None = Field(default=None, ge=0)
    latest_rendered_frame: int | None = Field(default=None, ge=0)
    latest_safe_frame: int | None = Field(default=None, ge=0)
    in_flight_frames: tuple[int, ...] = ()
    active_worker_ids: tuple[Identifier, ...] = ()
    retry_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    preview_url: str | None = Field(default=None, min_length=1, max_length=2048)
    full_frame_url: str | None = Field(default=None, min_length=1, max_length=2048)
    latest_frame_artifact: ArtifactKey | None = None
    latest_frame_artifact_frame: int | None = Field(default=None, ge=0)
    latest_frame_written_at: datetime | None = None
    latest_preview: PreviewArtifact | None = None
    encode_tasks: tuple[EncodeTask, ...] = ()
    qa_results: tuple[QaResult, ...] = ()
    artifacts: tuple[OutputVariantArtifact, ...] = ()
    updated_at: datetime

    @field_validator("latest_frame_artifact")
    @classmethod
    def validate_latest_frame_artifact(cls, value: str | None) -> str | None:
        return _validate_artifact_key(value) if value is not None else None

    @model_validator(mode="after")
    def validate_variant_progress(self) -> Self:
        if self.validated_frames > self.rendered_frames or self.rendered_frames > self.total_frames:
            raise ValueError("frame counts must satisfy validated <= rendered <= total")
        if len({stage.stage for stage in self.stages}) != len(self.stages):
            raise ValueError("a variant can contain only one progress record per stage")
        if len(self.active_worker_ids) != len(set(self.active_worker_ids)):
            raise ValueError("active worker IDs must be unique")
        if len(self.in_flight_frames) != len(set(self.in_flight_frames)):
            raise ValueError("in-flight frame numbers must be unique")
        if (
            self.latest_safe_frame is not None
            and self.latest_rendered_frame is not None
            and self.latest_safe_frame > self.latest_rendered_frame
        ):
            raise ValueError("latest safe frame cannot exceed latest rendered frame")
        if (
            any(
                task.output_variant_id != self.output_variant_id
                for task in self.encode_tasks
            )
            or any(
                result.output_variant_id != self.output_variant_id
                for result in self.qa_results
            )
            or any(
                artifact.output_variant_id != self.output_variant_id
                for artifact in self.artifacts
            )
        ):
            raise ValueError("encode, QA, and artifact records must match their output variant")
        if (
            self.latest_preview is not None
            and self.latest_preview.output_variant_id != self.output_variant_id
        ):
            raise ValueError("latest preview must match its output variant")
        artifact_fields = (
            self.latest_frame_artifact,
            self.latest_frame_artifact_frame,
            self.latest_frame_written_at,
        )
        if any(value is not None for value in artifact_fields) and any(
            value is None for value in artifact_fields
        ):
            raise ValueError("latest frame artifact identity must be complete")
        if (
            self.latest_frame_artifact_frame is not None
            and self.latest_frame_artifact_frame != self.latest_rendered_frame
        ):
            raise ValueError("latest frame artifact must match the latest rendered frame")
        if (
            self.latest_frame_written_at is not None
            and self.latest_frame_written_at > self.updated_at
        ):
            raise ValueError("latest frame write cannot be newer than progress state")
        return self


class OutputVariant(RenderContractModel):
    id: Identifier
    enabled: bool
    required: bool
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0, le=240)
    composition_mode: Literal["authored"] = "authored"
    deliverable_role: Identifier
    render_profile_id: Identifier
    render_profile_sha256: Sha256Digest
    composition_profile: CompositionProfile
    output_variant_sha256: Sha256Digest
    frames_root: ArtifactKey
    preview_root: ArtifactKey
    encode_root: ArtifactKey
    qa_root: ArtifactKey
    progress: OutputVariantProgress

    @field_validator("frames_root", "preview_root", "encode_root", "qa_root")
    @classmethod
    def validate_artifact_roots(cls, value: str) -> str:
        return _validate_artifact_key(value)

    @model_validator(mode="after")
    def validate_variant(self) -> Self:
        if self.required and not self.enabled:
            raise ValueError("required output variants must be enabled")
        roots = (self.frames_root, self.preview_root, self.encode_root, self.qa_root)
        if not _artifact_roots_are_isolated(roots):
            raise ValueError("each output variant artifact root must be isolated")
        if self.progress.output_variant_id != self.id:
            raise ValueError("variant progress must match the output variant ID")
        if not self.enabled and (
            self.progress.stages
            or self.progress.total_frames
            or self.progress.rendered_frames
            or self.progress.validated_frames
            or self.progress.current_frame is not None
            or self.progress.latest_rendered_frame is not None
            or self.progress.latest_safe_frame is not None
            or self.progress.in_flight_frames
            or self.progress.active_worker_ids
            or self.progress.retry_count
            or self.progress.failure_count
            or self.progress.preview_url is not None
            or self.progress.full_frame_url is not None
            or self.progress.latest_frame_artifact is not None
            or self.progress.latest_frame_artifact_frame is not None
            or self.progress.latest_frame_written_at is not None
            or self.progress.latest_preview is not None
            or self.progress.encode_tasks
            or self.progress.qa_results
            or self.progress.artifacts
        ):
            raise ValueError("disabled output variants cannot carry active progress or workload")
        if (
            self.progress.latest_preview is not None
            and (
                self.progress.latest_preview.width > self.width
                or self.progress.latest_preview.height > self.height
            )
        ):
            raise ValueError("preview dimensions cannot exceed output dimensions")
        return self


class RenderEvent(ImmutableRenderContractModel):
    id: Identifier
    type: RenderEventType
    tenant_namespace: Identifier | None = None
    project_id: Identifier
    job_id: Identifier
    output_variant_id: Identifier
    worker_id: Identifier
    chunk_id: Identifier
    shot_id: Identifier
    frame_number: int | None = Field(default=None, ge=0)
    frame_start: int = Field(ge=0)
    frame_end: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    scene_sha256: Sha256Digest
    render_profile_sha256: Sha256Digest
    composition_sha256: Sha256Digest
    output_variant_sha256: Sha256Digest
    elapsed_seconds: float | None = Field(default=None, ge=0)
    artifact_key: ArtifactKey | None = None
    occurred_at: datetime

    @field_validator("artifact_key")
    @classmethod
    def validate_optional_artifact_key(cls, value: str | None) -> str | None:
        return _validate_artifact_key(value) if value is not None else None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.frame_end < self.frame_start:
            raise ValueError("frame_end cannot precede frame_start")
        if self.frame_number is not None and not self.frame_start <= self.frame_number <= self.frame_end:
            raise ValueError("event frame must fall inside the declared frame range")
        if self.type is RenderEventType.FRAME_WRITTEN and self.artifact_key is None:
            raise ValueError("frame_written events require an artifact key")
        return self


class MediaRenderJob(RenderContractModel):
    id: Identifier
    project: ProjectRef
    package: PackageIdentity
    output_matrix: OutputVariantMatrixIdentity
    output_variants: tuple[OutputVariant, ...]
    created_at: datetime
    updated_at: datetime
    authorization_sha256: Sha256Digest | None = None

    @model_validator(mode="after")
    def validate_job_identity_and_isolation(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        variant_ids = tuple(variant.id for variant in self.output_variants)
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError("output variant IDs must be unique")
        enabled = tuple(variant for variant in self.output_variants if variant.enabled)
        enabled_ids = tuple(variant.id for variant in enabled)
        if not enabled_ids:
            raise ValueError("a media render job must enable at least one output variant")
        if self.output_matrix.package_sha256 != self.package.package_sha256:
            raise ValueError("output matrix package identity does not match the job package")
        if enabled_ids != self.output_matrix.enabled_variant_ids:
            raise ValueError("output matrix must exactly match enabled variants in declaration order")
        enabled_hashes = {variant.id: variant.output_variant_sha256 for variant in enabled}
        if enabled_hashes != self.output_matrix.variant_sha256_by_id:
            raise ValueError("output matrix variant hashes do not match enabled variants")
        enabled_roots = [
            root
            for variant in enabled
            for root in (variant.frames_root, variant.preview_root, variant.encode_root, variant.qa_root)
        ]
        if not _artifact_roots_are_isolated(tuple(enabled_roots)):
            raise ValueError("enabled output variants must have isolated artifact roots")
        for variant in self.output_variants:
            if (
                any(task.job_id != self.id for task in variant.progress.encode_tasks)
                or any(
                    result.job_id != self.id for result in variant.progress.qa_results
                )
                or any(
                    artifact.job_id != self.id
                    for artifact in variant.progress.artifacts
                )
            ):
                raise ValueError("variant progress records must match the media render job")
            if (
                variant.progress.latest_preview is not None
                and variant.progress.latest_preview.job_id != self.id
            ):
                raise ValueError("variant preview must match the media render job")
        return self

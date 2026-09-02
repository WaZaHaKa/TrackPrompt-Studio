from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ..schemas import AnalysisMode, APIModel, Confidence
from . import (
    AUDIT_SCHEMA_VERSION,
    CATALOGUE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    SEGMENT_SCHEMA_VERSION,
)


class RetentionPolicy(StrEnum):
    TEMPORARY = "temporary"
    ARCHIVE = "archive"
    CUSTOM = "custom"


class BatchState(StrEnum):
    DRAFT = "draft"
    UPLOADING = "uploading"
    AWAITING_REVIEW = "awaiting_review"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class UploadState(StrEnum):
    CREATED = "created"
    UPLOADING = "uploading"
    PAUSED = "paused"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ReviewState(StrEnum):
    DETECTED = "detected"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    USER_EDITED = "user_edited"
    IMPORTED = "imported"
    UNRESOLVED = "unresolved"


class TransitionType(StrEnum):
    SILENCE_GAP = "silence_gap"
    HARD_CUT = "hard_cut"
    FADE = "fade"
    CROSSFADE = "crossfade"
    GRADUAL_TRANSITION = "gradual_transition"
    UNCERTAIN = "uncertain"


class QueueState(StrEnum):
    STORED = "stored"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SegmentationJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ClientCreate(APIModel):
    display_name: str = Field(min_length=1, max_length=160)
    private_notes: str = Field(default="", max_length=8_000)
    tags: list[str] = Field(default_factory=list, max_length=64)


class ClientPatch(APIModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    private_notes: str | None = Field(default=None, max_length=8_000)
    tags: list[str] | None = Field(default=None, max_length=64)
    archived: bool | None = None


class ClientResponse(APIModel):
    id: str
    display_name: str
    private_notes: str
    tags: list[str]
    archived: bool
    project_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectCreate(APIModel):
    client_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=12_000)
    status: str = Field(default="active", min_length=1, max_length=48)
    retention_policy: RetentionPolicy = RetentionPolicy.ARCHIVE
    retention_until: datetime | None = None
    tags: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def custom_requires_date(self) -> ProjectCreate:
        if self.retention_policy == RetentionPolicy.CUSTOM and self.retention_until is None:
            raise ValueError("custom retention requires retentionUntil")
        return self


class ProjectPatch(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=12_000)
    status: str | None = Field(default=None, min_length=1, max_length=48)
    retention_policy: RetentionPolicy | None = None
    retention_until: datetime | None = None
    tags: list[str] | None = Field(default=None, max_length=64)
    archived: bool | None = None


class ProjectResponse(APIModel):
    id: str
    client_id: str
    name: str
    description: str
    status: str
    retention_policy: RetentionPolicy
    retention_until: datetime | None = None
    tags: list[str]
    archived_at: datetime | None = None
    storage_bytes: int = 0
    batch_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectDeletionResponse(APIModel):
    event_id: str
    project_id: str
    deleted_at: datetime
    counts: dict[str, int]
    tombstone_hash: str


class BatchCreate(APIModel):
    name: str = Field(min_length=1, max_length=200)
    sequence: int = Field(default=0, ge=0)
    default_analysis_mode: AnalysisMode = AnalysisMode.FAST
    enable_genre_analysis: bool = False
    enable_lyrical_analysis: bool = False
    lyrics_consent_confirmed: bool = False


class BatchPatch(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sequence: int | None = Field(default=None, ge=0)
    state: BatchState | None = None


class BatchResponse(APIModel):
    id: str
    project_id: str
    name: str
    sequence: int
    default_analysis_mode: AnalysisMode
    enable_genre_analysis: bool
    enable_lyrical_analysis: bool
    lyrics_consent_confirmed: bool
    state: BatchState
    item_total: int
    completed_items: int
    failed_items: int
    duration_seconds: float
    progress: int
    created_at: datetime
    completed_at: datetime | None = None


class UploadSessionCreate(APIModel):
    batch_id: str
    display_name: str = Field(min_length=1, max_length=240)
    total_bytes: int = Field(gt=0)
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=160)
    original_order: int = Field(default=0, ge=0)
    permission_confirmed: bool = False

    @field_validator("expected_sha256")
    @classmethod
    def valid_hash(cls, value: str | None) -> str | None:
        if value is not None and any(character not in "0123456789abcdefABCDEF" for character in value):
            raise ValueError("expectedSha256 must be hexadecimal")
        return value.lower() if value is not None else None


class UploadSessionResponse(APIModel):
    id: str
    batch_id: str
    display_name: str
    total_bytes: int
    received_bytes: int
    chunk_size_bytes: int
    expected_sha256: str | None = None
    state: UploadState
    asset_id: str | None = None
    duplicate_asset_id: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class UploadChunkResponse(APIModel):
    upload_id: str
    received_bytes: int
    total_bytes: int
    state: UploadState
    chunk_sha256: str


class SourceAssetResponse(APIModel):
    id: str
    project_id: str
    batch_id: str
    display_name: str
    content_sha256: str
    byte_size: int
    duration_seconds: float
    codec: str
    container: str
    sample_rate: int
    channels: int
    original_order: int
    upload_state: UploadState
    storage_state: str
    archival_state: RetentionPolicy
    segmentation_state: str
    created_at: datetime


class SegmentEvidence(APIModel):
    energy_dip: float = Field(default=0, ge=0, le=1)
    timbral_change: float = Field(default=0, ge=0, le=1)
    harmonic_change: float = Field(default=0, ge=0, le=1)
    stereo_change: float = Field(default=0, ge=0, le=1)
    onset_change: float = Field(default=0, ge=0, le=1)
    persistent_change: float = Field(default=0, ge=0, le=1)


class SegmentResponse(APIModel):
    id: str
    source_asset_id: str
    sequence_index: int
    label: str
    start_seconds: float
    end_seconds: float
    stable_core_start_seconds: float
    stable_core_end_seconds: float
    transition_in_start_seconds: float | None = None
    transition_in_end_seconds: float | None = None
    transition_out_start_seconds: float | None = None
    transition_out_end_seconds: float | None = None
    confidence: Confidence
    confidence_score: float | None = None
    transition_type: TransitionType
    review_state: ReviewState
    accepted: bool
    child_analysis_job_id: str | None = None
    evidence: SegmentEvidence = Field(default_factory=SegmentEvidence)
    revision: int


class SegmentBoundaryInput(APIModel):
    label: str = Field(default="", max_length=200)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    stable_core_start_seconds: float | None = Field(default=None, ge=0)
    stable_core_end_seconds: float | None = Field(default=None, gt=0)
    transition_type: TransitionType = TransitionType.UNCERTAIN
    confidence: Confidence = Confidence.UNKNOWN
    accepted: bool = False

    @model_validator(mode="after")
    def ordered(self) -> SegmentBoundaryInput:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("segment end must be after start")
        return self


class SegmentReplaceRequest(APIModel):
    segments: list[SegmentBoundaryInput] = Field(min_length=1, max_length=10_000)
    reason: str = Field(default="Boundary review", min_length=1, max_length=500)


class SegmentEditRequest(APIModel):
    operation: Literal["add", "move", "delete", "merge", "split", "rename", "accept", "reject", "restore"]
    segment_id: str | None = None
    adjacent_segment_id: str | None = None
    at_seconds: float | None = Field(default=None, ge=0)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)
    label: str | None = Field(default=None, max_length=200)
    reason: str = Field(default="Boundary edit", min_length=1, max_length=500)


class SegmentationRequest(APIModel):
    minimum_expected_track_seconds: int | None = Field(default=None, ge=10, le=1_800)
    maximum_expected_track_seconds: int | None = Field(default=None, ge=60, le=7_200)


class SegmentationResponse(APIModel):
    asset_id: str
    schema_version: str = SEGMENT_SCHEMA_VERSION
    state: str
    duration_seconds: float
    observation_count: int
    candidate_count: int
    segments: list[SegmentResponse]
    warnings: list[str] = Field(default_factory=list)
    cadence_seconds: float
    peak_buffer_bytes: int
    elapsed_seconds: float


class SegmentationJobResponse(APIModel):
    id: str
    asset_id: str
    state: SegmentationJobState
    stage: str
    progress: int = Field(ge=0, le=100)
    observation_count: int = 0
    candidate_count: int = 0
    refined_boundary_count: int = 0
    peak_buffer_bytes: int = 0
    elapsed_seconds: float = 0
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class ManualBoundaryImport(APIModel):
    format: Literal["cue", "csv", "json", "m3u"]
    content: str = Field(min_length=1, max_length=1_000_000)


class QueueItemResponse(APIModel):
    id: str
    batch_id: str
    segment_id: str
    state: QueueState
    attempt: int
    analysis_mode: AnalysisMode
    job_id: str | None = None
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ArtifactResponse(APIModel):
    id: str
    owner_type: str
    owner_id: str
    artifact_type: str
    schema_version: str
    media_type: str
    byte_size: int
    sha256: str
    created_at: datetime
    producer_versions: dict[str, str]
    current: bool
    supersedes_id: str | None = None


class RevisionResponse(APIModel):
    id: str
    entity_type: str
    entity_id: str
    parent_revision_id: str | None = None
    revision_number: int
    reason: str
    artifact_sha256: str
    schema_version: str
    audit_event_id: str
    created_at: datetime


class AuditEventResponse(APIModel):
    event_id: str
    timestamp: datetime
    sequence: int
    project_id: str
    batch_id: str | None = None
    entity_type: str
    entity_id: str
    event_type: str
    actor_type: str
    request_id: str | None = None
    correlation_id: str
    schema_version: str = AUDIT_SCHEMA_VERSION
    payload: dict[str, Any]
    previous_event_hash: str
    event_hash: str


class AuditVerificationResponse(APIModel):
    project_id: str
    valid: bool
    event_count: int
    first_sequence: int | None = None
    last_sequence: int | None = None
    failure_sequence: int | None = None


class Page(APIModel):
    items: list[Any]
    total: int
    offset: int
    limit: int


class TrackComparison(APIModel):
    segment_id: str
    order: int
    label: str
    source_start_seconds: float
    source_end_seconds: float
    duration_seconds: float
    boundary_confidence: Confidence
    transition_type: TransitionType
    stable_core_seconds: float
    measurements: dict[str, float | str | None]
    deviations: dict[str, float | None]
    outliers: list[str]
    warnings: list[str]


class MasteringReport(APIModel):
    schema_version: str = REPORT_SCHEMA_VERSION
    batch_id: str
    generated_at: datetime
    tracks: list[TrackComparison]
    medians: dict[str, float]
    minima: dict[str, float]
    maxima: dict[str, float]
    withheld_counts: dict[str, int]
    observations: list[str]
    estimator_limitations: list[str]


class CatalogueCapability(APIModel):
    available: bool = True
    catalogue_schema_version: str = CATALOGUE_SCHEMA_VERSION
    audit_schema_version: str = AUDIT_SCHEMA_VERSION
    segment_schema_version: str = SEGMENT_SCHEMA_VERSION
    report_schema_version: str = REPORT_SCHEMA_VERSION
    auto_segmentation_available: bool = True
    supported_retention_policies: list[RetentionPolicy] = Field(
        default_factory=lambda: list(RetentionPolicy)
    )
    free_storage_bytes: int
    archive_storage_used_bytes: int
    archive_quota_bytes: int

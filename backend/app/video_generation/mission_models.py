from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

VIDEO_MISSION_SCHEMA_VERSION = "1.0.0"


class VideoMissionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        use_enum_values=False,
    )


class VideoJobState(StrEnum):
    PLANNED = "planned"
    AUTHORIZED = "authorized"
    SMOKE_SUBMITTED = "smoke_submitted"
    GENERATING = "generating"
    PARTIAL = "partial"
    REVIEW_READY = "review_ready"
    TIMELINE_READY = "timeline_ready"
    EXPORTED = "exported"
    ASSEMBLING = "assembling"
    COMPLETE = "complete"
    BLOCKED_BUDGET = "blocked_budget"
    BLOCKED_PROVIDER_ACCESS = "blocked_provider_access"
    BLOCKED_PROVIDER_QUOTA = "blocked_provider_quota"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VideoShotState(StrEnum):
    PLANNED = "planned"
    RESERVED = "reserved"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FILTERED = "filtered"
    FAILED = "failed"
    DOWNLOADED = "downloaded"
    VERIFIED = "verified"


class VideoReviewState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class VideoError(VideoMissionModel):
    code: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=1_000)
    retryable: bool = False
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_status: str | None = Field(default=None, max_length=160)
    provider_error_code: str | None = Field(default=None, max_length=160)
    diagnostic_id: str | None = Field(default=None, max_length=160)


class VideoShotAttempt(VideoMissionModel):
    id: str = Field(min_length=1, max_length=160)
    attempt: int = Field(ge=1)
    idempotency_key: str = Field(min_length=64, max_length=64)
    state: VideoShotState
    reserved_cost_usd: float = Field(ge=0)
    operation_name: str | None = Field(default=None, max_length=2_000)
    output_uri: str | None = Field(default=None, max_length=4_000)
    local_clip_path: str | None = None
    clip_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    probe_evidence: dict[str, Any] | None = None
    error: VideoError | None = None
    created_at: datetime
    updated_at: datetime


class VideoShotRecord(VideoMissionModel):
    shot_id: str = Field(min_length=1, max_length=160)
    chapter_id: str = Field(min_length=1, max_length=160)
    order: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    prompt: str = Field(min_length=1, max_length=20_000)
    negative_prompt: str = Field(min_length=1, max_length=20_000)
    seed: int = Field(ge=0, le=4_294_967_295)
    required: bool = True
    estimated_cost_usd: float = Field(ge=0)
    review_state: VideoReviewState = VideoReviewState.PENDING
    review_note: str | None = Field(default=None, max_length=2_000)
    accepted_attempt_id: str | None = None
    retry_requested: bool = False
    attempts: tuple[VideoShotAttempt, ...] = ()

    @property
    def latest_attempt(self) -> VideoShotAttempt | None:
        return self.attempts[-1] if self.attempts else None


class VideoJobRecord(VideoMissionModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    id: str = Field(min_length=1, max_length=160)
    analysis_job_id: str
    project_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    state: VideoJobState
    plan_digest: str = Field(min_length=64, max_length=64)
    plan: dict[str, Any]
    gcp_project_id: str = Field(min_length=1, max_length=300)
    gcs_bucket: str = Field(min_length=1, max_length=300)
    region: Literal["us-central1"] = "us-central1"
    audio_path: str | None = None
    authorization: dict[str, Any] | None = None
    reference_assets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    shots: tuple[VideoShotRecord, ...]
    reserved_cost_usd: float = Field(default=0, ge=0)
    timeline_path: str | None = None
    export_root: str | None = None
    preview_path: str | None = None
    error: VideoError | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def shot(self, shot_id: str) -> VideoShotRecord | None:
        return next((item for item in self.shots if item.shot_id == shot_id), None)


class VideoPlanCreateRequest(VideoMissionModel):
    analysis_job_id: str = Field(min_length=36, max_length=36)
    project_id: str = Field(default="the-glitch-is-me", min_length=1, max_length=160)
    profile_id: Literal["fast-1080p", "quality-1080p", "quality-4k"] = "fast-1080p"
    gcp_project_id: str = Field(min_length=1, max_length=300)
    gcs_bucket: str = Field(min_length=3, max_length=300)
    audio_path: str | None = Field(default=None, max_length=32_000)
    master_seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    seed_locked: bool = True
    reference_image_path: str | None = Field(default=None, max_length=32_000)

    @field_validator("gcp_project_id", "gcs_bucket")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("value contains invalid control characters")
        return normalized


class VideoAuthorizationRequest(VideoMissionModel):
    confirmation: str = Field(min_length=1, max_length=500)


class VideoReviewRequest(VideoMissionModel):
    decision: VideoReviewState
    note: str | None = Field(default=None, max_length=2_000)

    @field_validator("decision")
    @classmethod
    def decision_must_be_final(cls, value: VideoReviewState) -> VideoReviewState:
        if value is VideoReviewState.PENDING:
            raise ValueError("review decision must be accepted or rejected")
        return value


class VideoRetryRequest(VideoMissionModel):
    mode: Literal["same_setup", "new_variation"] = "same_setup"


class VideoChainReferenceRequest(VideoMissionModel):
    source_shot_id: str = Field(min_length=1, max_length=160)


class VideoDoctorRequest(VideoMissionModel):
    gcp_project_id: str = Field(min_length=1, max_length=300)
    gcs_bucket: str = Field(min_length=3, max_length=300)
    region: Literal["us-central1"] = "us-central1"


class VideoAnalysisSource(VideoMissionModel):
    analysis_job_id: str
    display_name: str
    story_plan_available: bool
    shot_plan_available: bool
    retained_audio_available: bool


class VideoProfileSummary(VideoMissionModel):
    id: str
    display_name: str
    model_id: str
    resolution: str
    duration_seconds: int
    fps: int
    sample_count: int
    default: bool
    optional: bool
    base_estimated_usd: float
    conservative_estimated_usd: float
    max_spend_usd: float
    available: bool = True
    availability_note: str | None = None


class VideoContentPackage(VideoMissionModel):
    project_id: str
    title: str
    shot_count: int
    profiles: tuple[VideoProfileSummary, ...]


class VideoCatalog(VideoMissionModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    analyses: tuple[VideoAnalysisSource, ...]
    packages: tuple[VideoContentPackage, ...]
    pricing_snapshot_date: str
    provider_network_contacted: Literal[False] = False


class VideoShotView(VideoMissionModel):
    shot_id: str
    chapter_id: str
    order: int
    title: str
    prompt: str
    negative_prompt: str
    seed: int
    state: VideoShotState
    review_state: VideoReviewState
    review_note: str | None
    attempt_count: int
    reserved_cost_usd: float
    error: VideoError | None
    clip_url: str | None
    variation_index: int = Field(ge=0)
    continuity_group_ids: tuple[str, ...]
    previous_shot_id: str | None
    continuation_mode: str
    reference_asset_id: str | None


class VideoArtifactSummary(VideoMissionModel):
    timeline_ready: bool
    davinci_package_ready: bool
    preview_ready: bool
    fcpxml_url: str | None = None
    fcp7_xml_url: str | None = None
    edl_url: str | None = None
    edit_sheet_url: str | None = None
    markers_url: str | None = None
    preview_url: str | None = None


class VideoJobView(VideoMissionModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: str
    analysis_job_id: str
    project_id: str
    title: str
    state: VideoJobState
    plan_digest: str
    profile: dict[str, Any]
    cost: dict[str, Any]
    source_artifacts: dict[str, str]
    authorization_phrase: str
    authorization_expires_at: str | None
    audio_master_bound: bool
    shots: tuple[VideoShotView, ...]
    progress_percent: float = Field(ge=0, le=100)
    verified_shot_count: int = Field(ge=0)
    total_shot_count: int = Field(ge=0)
    reserved_cost_usd: float = Field(ge=0)
    remaining_authorized_usd: float = Field(ge=0)
    request_preview_url: str
    consistency_notice: str
    continuity: dict[str, Any]
    artifacts: VideoArtifactSummary
    error: VideoError | None
    created_at: datetime
    updated_at: datetime


class VideoRequestPreview(VideoMissionModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: str
    plan_digest: str
    requests: tuple[dict[str, Any], ...]


class VideoGenerationEvent(VideoMissionModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    event_kind: Literal["video_generation"] = "video_generation"
    sequence: int = Field(ge=0)
    timestamp: datetime
    job_id: str
    project_id: str
    state: VideoJobState
    progress_percent: float = Field(ge=0, le=100)
    verified_shot_count: int = Field(ge=0)
    total_shot_count: int = Field(ge=0)
    reserved_cost_usd: float = Field(ge=0)
    error: VideoError | None = None


class VideoDoctorCheck(VideoMissionModel):
    id: str
    status: Literal["pass", "fail", "unknown"]
    code: str
    detail: str


class VideoDoctorView(VideoMissionModel):
    ok: bool
    network_contacted: bool
    generation_submitted: Literal[False] = False
    checks: tuple[VideoDoctorCheck, ...]


def utc_now() -> datetime:
    return datetime.now(UTC)

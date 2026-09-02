from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class LocalVideoModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        use_enum_values=False,
    )


class LocalVideoStage(StrEnum):
    INGEST = "ingest"
    ANALYSIS = "analysis"
    REFERENCES = "references"
    KEYFRAMES = "keyframes"
    VIDEO = "video"
    POST = "post"
    EDIT = "edit"
    QC = "qc"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class LocalVideoShotState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LocalVideoProviderState(StrEnum):
    COMFYUI_MISSING = "comfyui_missing"
    COMFYUI_STARTING = "comfyui_starting"
    GGUF_NODE_MISSING = "gguf_node_missing"
    FLUX_WORKFLOW_UNAVAILABLE = "flux_workflow_unavailable"
    WAN_WORKFLOW_UNAVAILABLE = "wan_workflow_unavailable"
    MODELS_MISSING = "models_missing"
    QUALIFICATION_NOT_RUN = "qualification_not_run"
    POST_PROCESSING_MISSING = "post_processing_missing"
    FULLY_PRODUCTION_READY = "fully_production_ready"


class LocalVideoQualificationState(StrEnum):
    NOT_RUN = "qualification_not_run"
    Q5_QUALIFIED = "q5_qualified"
    Q4_QUALIFIED = "q4_qualified"
    FALLBACK_TIER_QUALIFIED = "fallback_tier_qualified"


class LocalVideoError(LocalVideoModel):
    code: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    action: str | None = Field(default=None, max_length=500)
    retryable: bool = False


class ComfyUIDevice(LocalVideoModel):
    name: str = Field(min_length=1, max_length=160)
    type: str = Field(min_length=1, max_length=80)
    vram_total_bytes: int | None = Field(default=None, ge=0)
    vram_free_bytes: int | None = Field(default=None, ge=0)


class ComfyUIReadiness(LocalVideoModel):
    provider_id: Literal["local-comfyui"] = "local-comfyui"
    configured: bool
    reachable: bool
    local_endpoint: str
    comfyui_version: str | None = None
    node_count: int = Field(default=0, ge=0)
    devices: list[ComfyUIDevice] = Field(default_factory=list)
    missing_node_roles: list[str] = Field(default_factory=list)
    discovered_model_names: list[str] = Field(default_factory=list)
    setup_required: bool = True
    local_api_contacted: bool = False
    provider_state: LocalVideoProviderState = LocalVideoProviderState.COMFYUI_MISSING
    qualification_state: LocalVideoQualificationState = LocalVideoQualificationState.NOT_RUN
    gguf_node_available: bool = False
    flux_workflow_available: bool = False
    wan_workflow_available: bool = False
    models_available: bool = False
    qualification_completed: bool = False
    selected_tier: str | None = None
    post_processing_ready: bool = False
    production_ready: bool = False
    status_message: str = Field(default="Local ComfyUI is not ready", max_length=300)
    error: LocalVideoError | None = None


class QualificationCandidate(LocalVideoModel):
    tier: Literal["A14B-Q5_K_M", "A14B-Q4_K_M", "TI2V-5B"]
    state: Literal["pending", "running", "passed", "failed", "skipped"]
    reason: str | None = Field(default=None, max_length=300)
    peak_vram_bytes: int | None = Field(default=None, ge=0)
    peak_system_memory_bytes: int | None = Field(default=None, ge=0)
    elapsed_seconds: float | None = Field(default=None, ge=0)


class QualificationView(LocalVideoModel):
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_tier: str | None = None
    cached: bool = False
    completed_at: datetime | None = None
    candidates: list[QualificationCandidate] = Field(default_factory=list)
    error: LocalVideoError | None = None


class LocalVideoTimelineScene(LocalVideoModel):
    shot_id: str = Field(pattern=r"^shot-[0-9]{3}$")
    order: int = Field(ge=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    boundary_source: str = Field(min_length=1, max_length=80)


class LocalVideoShot(LocalVideoModel):
    shot_id: str = Field(pattern=r"^shot-[0-9]{3}$")
    order: int = Field(ge=1)
    state: LocalVideoShotState = LocalVideoShotState.PLANNED
    stage: LocalVideoStage = LocalVideoStage.VIDEO
    progress: float | None = Field(default=None, ge=0, le=1)
    attempt: int = Field(default=0, ge=0, le=2)
    alternate: bool = False
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error: LocalVideoError | None = None


class LocalVideoProjectSummary(LocalVideoModel):
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    title: str = Field(min_length=1, max_length=200)
    status: str = Field(min_length=1, max_length=80)
    current_revision_id: str | None = None
    updated_at: datetime | None = None
    audio_hash_prefix: str | None = Field(default=None, min_length=12, max_length=12)
    duration_seconds: float | None = Field(default=None, gt=0)
    selected_tier: str | None = None


class LocalVideoProjectView(LocalVideoModel):
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    title: str = Field(min_length=1, max_length=200)
    revision_id: str | None = None
    package_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    audio_hash_prefix: str | None = Field(default=None, min_length=12, max_length=12)
    audio_duration_seconds: float | None = Field(default=None, gt=0)
    stage: LocalVideoStage = LocalVideoStage.INGEST
    status_message: str = Field(default="Project package discovered", max_length=500)
    completed_units: int = Field(default=0, ge=0)
    total_units: int = Field(default=16, ge=1)
    elapsed_seconds: float = Field(default=0, ge=0)
    eta_seconds: float | None = Field(default=None, ge=0)
    current_shot_id: str | None = Field(default=None, pattern=r"^shot-[0-9]{3}$")
    provider: ComfyUIReadiness | None = None
    qualification: QualificationView | None = None
    timeline: list[LocalVideoTimelineScene] = Field(default_factory=list)
    shots: list[LocalVideoShot] = Field(default_factory=list)
    analysis_archived: bool = False
    can_start: bool = False
    can_resume: bool = False
    can_cancel: bool = False
    final_qc_passed: bool = False
    output_available: bool = False
    error: LocalVideoError | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LocalVideoPrepareRequest(LocalVideoModel):
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    analysis_id: str | None = None


class LocalVideoStartRequest(LocalVideoModel):
    workflow_id: str = Field(min_length=1, max_length=120)
    qualification_cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocalVideoWorkflowRequest(LocalVideoModel):
    workflow_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    capability: Literal["wan22-i2v", "keyframe-flux", "keyframe-sdxl"]
    workflow: dict[str, Any]
    source_url: str | None = Field(default=None, max_length=500)
    source_revision: str | None = Field(default=None, max_length=160)


class LocalVideoWorkflowView(LocalVideoModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    workflow_id: str
    capability: str
    workflow_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_roles: dict[str, list[str]] = Field(default_factory=dict)
    missing_roles: list[str] = Field(default_factory=list)
    source_url: str | None = None
    source_revision: str | None = None
    installed_at: datetime


class LocalVideoQualificationRequest(LocalVideoModel):
    workflow_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")


class LocalVideoRetryRequest(LocalVideoModel):
    alternate: bool = False


class LocalVideoDeletePreview(LocalVideoModel):
    project_id: str
    revision_id: str | None = None
    artifact_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    includes_retained_audio: bool
    confirmation_phrase: str


class LocalVideoDeleteRequest(LocalVideoModel):
    revision_id: str | None = None
    confirmation: str = Field(min_length=1, max_length=200)

    @field_validator("confirmation")
    @classmethod
    def trim_confirmation(cls, value: str) -> str:
        return value.strip()

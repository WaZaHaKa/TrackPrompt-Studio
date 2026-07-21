from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

MISSION_CONTROL_SCHEMA_VERSION = "1.0.0"


class MissionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class JobState(StrEnum):
    IDLE = "IDLE"
    VALIDATING = "VALIDATING"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOP_REQUESTED = "STOP_REQUESTED"
    FINISHING_CURRENT_CHUNK = "FINISHING_CURRENT_CHUNK"
    PAUSED_SAFELY = "PAUSED_SAFELY"
    RESUMABLE = "RESUMABLE"
    ENCODING = "ENCODING"
    VERIFYING = "VERIFYING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobPhase(StrEnum):
    SCENE_LOAD = "SCENE_LOAD"
    RENDER_FRAME = "RENDER_FRAME"
    WRITE_FRAME = "WRITE_FRAME"
    VALIDATE_FRAME = "VALIDATE_FRAME"
    VALIDATE_CHUNK = "VALIDATE_CHUNK"
    PUBLISH_CHUNK = "PUBLISH_CHUNK"
    WAITING_FOR_STORAGE = "WAITING_FOR_STORAGE"
    ENCODE_MASTER = "ENCODE_MASTER"
    ENCODE_DELIVERY = "ENCODE_DELIVERY"
    MUX_AUDIO = "MUX_AUDIO"
    FINAL_VERIFY = "FINAL_VERIFY"


class RendererKind(StrEnum):
    PRODUCTION = "production"
    FAKE = "fake"


class CheckStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class SafeStopStatus(StrEnum):
    NONE = "none"
    REQUESTED = "requested"
    FINISHING_CURRENT_CHUNK = "finishing_current_chunk"
    PAUSED = "paused"


class EtaConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class OutputClassification(StrEnum):
    NEW_OUTPUT = "new_output"
    EMPTY_DIRECTORY = "empty_directory"
    COMPATIBLE_RESUMABLE = "compatible_resumable"
    INCOMPATIBLE_RENDER = "incompatible_render"
    CONTAINS_UNRELATED_FILES = "contains_unrelated_files"
    CONTAINS_HIDDEN_SYSTEM_ENTRIES = "contains_hidden_system_entries"
    PARENT_SUITABLE = "parent_suitable"
    NOT_A_DIRECTORY = "not_a_directory"
    UNSAFE_PATH = "unsafe_path"


class StructuredError(MissionModel):
    code: str
    title: str
    summary: str
    likely_cause: str | None = None
    recommended_action: str
    retryable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)
    technical_details: str | None = None
    related_path: str | None = None
    timestamp: datetime
    job_id: str | None = None


class ErrorEnvelope(MissionModel):
    error: StructuredError


class RuntimeIdentity(MissionModel):
    instance_id: str
    pid: int
    host: str
    port: int
    started_at: datetime


class ComponentStatus(MissionModel):
    id: str
    label: str
    status: CheckStatus
    detail: str
    path: str | None = None


class SystemHealth(MissionModel):
    status: Literal["ok", "degraded"]
    schema_version: str = MISSION_CONTROL_SCHEMA_VERSION
    local_only: bool = True
    instance: RuntimeIdentity | None = None


class SystemStatus(MissionModel):
    status: Literal["ready", "needs_attention"]
    local_only: bool = True
    current_job_id: str | None = None
    recommended_profile_id: str | None = None
    components: list[ComponentStatus]


class SystemPaths(MissionModel):
    repository_root: str
    profile_root: str
    calibration_root: str
    state_root: str
    default_output_root: str
    blender_path: str | None = None
    ffmpeg_path: str | None = None
    powershell_path: str | None = None


class NativePickerRequest(MissionModel):
    initial_directory: str | None = None
    title: str = Field(default="Choose a folder", min_length=1, max_length=120)


class NativePickerResponse(MissionModel):
    cancelled: bool
    path: str | None = None


class OpenPathRequest(MissionModel):
    job_id: str = Field(min_length=1, max_length=128)
    path: str | None = None


class OpenPathResult(MissionModel):
    opened: bool
    path: str


class MissionSettings(MissionModel):
    theme: Literal["system", "light", "dark"] = "system"
    preferred_drive: str | None = None
    default_output_root: str
    performance_mode_enabled: bool = False
    performance_mode_available: bool = False
    performance_mode_detail: str
    fake_renderer_available: bool = False


class MissionSettingsPatch(MissionModel):
    theme: Literal["system", "light", "dark"] | None = None
    preferred_drive: str | None = None
    default_output_root: str | None = None


class PerformanceStatus(MissionModel):
    available: bool
    active: bool
    restore_required: bool
    on_ac_power: bool | None = None
    power_line_status: str | None = None
    previous_power_plan_guid: str | None = None
    current_power_plan_guid: str | None = None
    selected_power_plan_guid: str | None = None
    sleep_inhibited: bool = False
    blender_process_id: int | None = None
    blender_priority: str | None = None
    gpu_temperature_c: float | None = None
    gpu_utilization_percent: float | None = None
    vram_used_mib: float | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "vramUsedMib",
            "vramUsedMiB",
            "vram_used_mib",
        ),
        serialization_alias="vramUsedMib",
    )
    restored_at: datetime | None = None
    detail: str


class PerformanceEnableRequest(MissionModel):
    operator_confirmed: bool = False
    use_high_performance_power_plan: bool = True
    job_id: str | None = Field(default=None, min_length=1, max_length=128)


class PerformanceRestoreRequest(MissionModel):
    operator_confirmed: bool = False


class ProjectSummary(MissionModel):
    id: str
    display_name: str
    scene_ids: list[str]
    profile_ids: list[str]
    recommended_profile_id: str | None = None


class SceneSummary(MissionModel):
    id: str
    project_id: str
    display_name: str
    preset: str
    path: str
    sha256: str
    expected_sha256: str
    verified: bool
    manifest_path: str | None = None
    manifest_sha256: str | None = None
    frame_start: int
    frame_end: int
    fps: float
    preview_path: str | None = None


class Resolution(MissionModel):
    width: int
    height: int
    label: str


class ProfileSummary(MissionModel):
    id: str
    project_id: str
    scene_id: str
    display_name: str
    path: str
    saved_file_sha256: str
    embedded_profile_sha256: str | None = None
    scene_sha256: str
    resolution: Resolution
    fps: float
    frame_start: int
    frame_end: int
    total_frames: int
    frames_per_chunk: int
    expected_hours: float | None = None
    conservative_hours: float | None = None
    planned_frame_sequence_gib: float | None = None
    minimum_launch_free_gib: float | None = None
    quality_role: str
    quality_verdict: str | None = None
    calibrated: bool
    calibration_id: str | None = None
    authorization_status: str
    authorized: bool
    authorization_issues: list[str] = Field(default_factory=list)
    recommended: bool = False
    last_used_at: datetime | None = None


class ProfileValidation(MissionModel):
    profile_id: str
    valid: bool
    saved_file_sha256: str
    scene_sha256: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    authorized: bool
    authorization_issues: list[str] = Field(default_factory=list)


class AuthorizationRequest(MissionModel):
    scene_id: str
    settings_and_hashes_reviewed: bool = False
    production_render_authorized: bool = False


class AuthorizationResult(MissionModel):
    authorized: bool
    profile_id: str
    scene_id: str
    profile_sha256: str
    scene_sha256: str
    authorization_token: str
    token_sha256: str
    record_path: str
    authorized_at: datetime


class OutputEntry(MissionModel):
    name: str
    type: Literal["file", "directory", "other"]
    hidden: bool = False
    system: bool = False
    reparse_point: bool = False


class OutputInspectRequest(MissionModel):
    path: str
    profile_id: str | None = None
    scene_id: str | None = None


class RenderIdentity(MissionModel):
    project_id: str
    scene_id: str
    scene_sha256: str
    profile_id: str
    profile_sha256: str
    output_directory: str


class OutputInspection(MissionModel):
    path: str
    exists: bool
    usable: bool
    classification: OutputClassification
    entries: list[OutputEntry] = Field(default_factory=list)
    conflicting_entries: list[str] = Field(default_factory=list)
    existing_identity: RenderIdentity | None = None
    expected_identity: RenderIdentity | None = None
    issues: list[str] = Field(default_factory=list)
    free_bytes: int | None = None
    create_child_available: bool = False


class OutputCreateChildRequest(MissionModel):
    parent_directory: str
    project_id: str
    profile_id: str
    base_name: str | None = None


class OutputCreateChildResult(MissionModel):
    path: str
    created: bool = True
    inspection: OutputInspection


class PreflightRequest(MissionModel):
    project_id: str
    scene_id: str
    profile_id: str
    output_directory: str = Field(
        validation_alias=AliasChoices(
            "outputDirectory",
            "output_directory",
            "outputPath",
            "output_path",
        ),
        serialization_alias="outputDirectory",
    )
    renderer: RendererKind = RendererKind.PRODUCTION


class PreflightCheck(MissionModel):
    id: str
    label: str
    status: CheckStatus
    summary: str
    detail: str | None = None


class PreflightResult(MissionModel):
    ready: bool
    authorization_required: bool
    identity: RenderIdentity
    checks: list[PreflightCheck]
    expected_hours: float | None = None
    required_free_bytes: int | None = None
    available_bytes: int | None = None
    raw_engine_result: dict[str, Any] | None = None


class DryRunResult(MissionModel):
    ok: bool
    identity: RenderIdentity
    plan: dict[str, Any] = Field(default_factory=dict)
    log_lines: list[str] = Field(default_factory=list)


class StartRenderRequest(PreflightRequest):
    renderer: Literal[RendererKind.PRODUCTION] = RendererKind.PRODUCTION


class FakeRenderOptions(MissionModel):
    total_frames: int | None = Field(default=None, ge=1, le=100_000)
    frames_per_chunk: int | None = Field(default=None, ge=1, le=10_000)
    step_delay_seconds: float = Field(default=0.01, ge=0.0, le=5.0)
    fail_at_frame: int | None = Field(default=None, ge=1)
    storage_warning_at_frame: int | None = Field(default=None, ge=1)
    long_frame_at: int | None = Field(default=None, ge=1)


class StartFakeRenderRequest(PreflightRequest):
    renderer: Literal[RendererKind.FAKE] = RendererKind.FAKE
    fake: FakeRenderOptions = Field(default_factory=FakeRenderOptions)


class JobRecord(MissionModel):
    id: str
    renderer: RendererKind
    state: JobState
    phase: JobPhase | None = None
    identity: RenderIdentity
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    process_id: int | None = None
    orphaned: bool = False
    renderer_active: bool | None = None
    watcher_active: bool | None = None
    current_frame_started_at: datetime | None = None
    last_output_at: datetime | None = None
    frame_start: int
    frame_end: int
    current_frame: int | None = None
    rendered_frame_count: int = 0
    inflight_frame_count: int = 0
    validated_frame_count: int = 0
    published_frame_count: int = 0
    total_frame_count: int
    chunk_start: int | None = None
    chunk_end: int | None = None
    current_chunk_progress: float | None = None
    chunks_completed: int = 0
    chunks_total: int
    current_seconds_per_frame: float | None = None
    rolling_median_seconds: float | None = None
    rolling_mean_seconds: float | None = None
    p90_seconds: float | None = None
    estimated_completion_time: datetime | None = None
    eta_confidence: EtaConfidence = EtaConfidence.UNKNOWN
    current_storage_bytes: int | None = None
    projected_storage_bytes: int | None = None
    free_storage_bytes: int | None = None
    gpu_utilization_percent: float | None = None
    vram_used_mib: int | None = None
    gpu_temperature_c: float | None = None
    cpu_utilization_percent: float | None = None
    ram_used_mib: int | None = None
    latest_frame_preview: str | None = None
    latest_preview_frame: int | None = None
    latest_log_line: str | None = None
    warning: str | None = None
    error: StructuredError | None = None
    safe_stop_status: SafeStopStatus = SafeStopStatus.NONE


class RenderEvent(MissionModel):
    schema_version: str = MISSION_CONTROL_SCHEMA_VERSION
    sequence: int
    timestamp: datetime
    job_id: str
    project_id: str
    state: JobState
    phase: JobPhase | None = None
    scene_id: str
    scene_sha256: str
    profile_id: str
    profile_sha256: str
    renderer_active: bool | None = None
    watcher_active: bool | None = None
    current_frame_started_at: datetime | None = None
    last_output_at: datetime | None = None
    frame_start: int
    frame_end: int
    current_frame: int | None = None
    rendered_frame_count: int = 0
    inflight_frame_count: int = 0
    validated_frame_count: int = 0
    published_frame_count: int = 0
    total_frame_count: int
    chunk_start: int | None = None
    chunk_end: int | None = None
    current_chunk_progress: float | None = None
    chunks_completed: int = 0
    chunks_total: int
    current_seconds_per_frame: float | None = None
    rolling_median_seconds: float | None = None
    rolling_mean_seconds: float | None = None
    p90_seconds: float | None = None
    estimated_completion_time: datetime | None = None
    eta_confidence: EtaConfidence = EtaConfidence.UNKNOWN
    current_storage_bytes: int | None = None
    projected_storage_bytes: int | None = None
    free_storage_bytes: int | None = None
    gpu_utilization_percent: float | None = None
    vram_used_mib: int | None = None
    gpu_temperature_c: float | None = None
    cpu_utilization_percent: float | None = None
    ram_used_mib: int | None = None
    latest_frame_preview: str | None = None
    latest_preview_frame: int | None = None
    latest_log_line: str | None = None
    warning: str | None = None
    error: StructuredError | None = None
    safe_stop_status: SafeStopStatus = SafeStopStatus.NONE


class LogEntry(MissionModel):
    sequence: int
    timestamp: datetime
    job_id: str
    level: Literal["debug", "info", "warning", "error"]
    message: str


class LogPage(MissionModel):
    items: list[LogEntry]
    next_sequence: int | None = None


class StopRequest(MissionModel):
    confirmed: bool = True


class CancelStopRequest(MissionModel):
    operator_confirmed: bool = False


class ResumeRequest(MissionModel):
    scene_sha256: str
    profile_sha256: str

    @field_validator("scene_sha256", "profile_sha256")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        upper = value.upper()
        if len(upper) != 64 or any(character not in "0123456789ABCDEF" for character in upper):
            raise ValueError("must be a 64-character SHA-256 value")
        return upper


class CalibrationCandidate(MissionModel):
    id: str
    resolution: str
    width: int
    height: int
    samples: int
    status: str
    expected_hours: float | None = None
    conservative_hours: float | None = None
    projected_storage_bytes: int | None = None
    quality_result: str | None = None
    quality_notes: str | None = None


class CalibrationSummary(MissionModel):
    id: str
    status: str
    created_at: datetime | None = None
    completed_at: datetime | None = None
    path: str
    scene_sha256: str | None = None
    machine_id: str | None = None
    machine_fingerprint: str | None = None
    cpu_model: str | None = None
    gpu_model: str | None = None
    vram_mib: int | None = None
    ram_bytes: int | None = None
    recommended_candidate: CalibrationCandidate | None = None
    finalists: list[CalibrationCandidate] = Field(default_factory=list)


class CalibrationPlanRequest(MissionModel):
    scene_id: str
    goal: str = Field(default="RECOMMENDED BALANCED", min_length=1, max_length=120)


class CalibrationPlanResult(MissionModel):
    id: str
    status: Literal["planned"] = "planned"
    path: str
    execution_available: bool
    detail: str


class CalibrationCandidateRunRequest(MissionModel):
    candidate_id: str
    confirmed_bounded_run: bool = False


class CalibrationReviewRequest(MissionModel):
    candidate_id: str
    verdict: Literal["pass", "pass_with_caveat", "fail"]
    notes: str = Field(min_length=1, max_length=2_000)


class CapabilityActionResult(MissionModel):
    available: bool
    accepted: bool = False
    detail: str


class CloudReadiness(MissionModel):
    provider: str = "NVIDIA Brev"
    status: Literal["setup_required", "offline_ready"]
    offline_preparation_available: bool
    package_validation_available: bool
    brev_cli_installed: bool
    brev_cli_inspected: bool = False
    live_provisioning_verified: bool = False
    live_fleet_verified: bool = False
    automatic_teardown_verified: bool = False
    cloud_encode_download_verified: bool = False
    provisioning_enabled: bool = False
    detail: str
    setup_checklist: list[str]


class CloudPackageRequest(MissionModel):
    profile_id: str
    scene_id: str
    output_directory: str


class CloudValidateRequest(MissionModel):
    manifest_path: str


class EncodeReadiness(MissionModel):
    job_id: str
    ready: bool
    frame_sequence_complete: bool
    published_frames: int
    total_frames: int
    ffmpeg_available: bool
    detail: str

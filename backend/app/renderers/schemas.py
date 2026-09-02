from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from .wzhk_spectrum.design import SpectrumPreviewSection, SpectrumVisualOverrides
from .wzhk_spectrum.generative.contracts import GeometryPreviewOverride
from .wzhk_spectrum.production import (
    CapturePreflightResult,
    CaptureSynchronization,
    GeometryCapabilityEvidence,
    GeometryRuntimeTelemetry,
    SpectrumArtifact,
    SpectrumMasterTiming,
    SpectrumProductionAvailability,
    SpectrumProductionState,
    SpectrumValidationReport,
)


class RendererModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        use_enum_values=False,
    )


class RendererAvailabilityState(StrEnum):
    READY = "READY"
    READY_FOR_PREVIEW = "READY_FOR_PREVIEW"
    READY_FOR_CAPTURE = "READY_FOR_CAPTURE"
    MISSING_RAINMETER = "MISSING_RAINMETER"
    MISSING_ASSETS = "MISSING_ASSETS"
    MISSING_FFMPEG = "MISSING_FFMPEG"
    MISSING_CAPTURE_PROVIDER = "MISSING_CAPTURE_PROVIDER"
    MISSING_MASTER = "MISSING_MASTER"
    INVALID_MASTER_DURATION = "INVALID_MASTER_DURATION"
    INVALID_WORKSPACE = "INVALID_WORKSPACE"
    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"
    INVALID_VENDOR_SNAPSHOT = "INVALID_VENDOR_SNAPSHOT"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    INVALID_DESIGN_PRESET = "INVALID_DESIGN_PRESET"
    ASSET_DURATION_MISMATCH = "ASSET_DURATION_MISMATCH"
    WORKSPACE_UNAVAILABLE = "WORKSPACE_UNAVAILABLE"


class RendererRequirement(RendererModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,79}$")
    label: str = Field(min_length=1, max_length=120)
    available: bool
    required_for_preparation: bool = True
    detail: str = Field(min_length=1, max_length=500)


class RendererContractSummary(RendererModel):
    artist: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    bpm: float = Field(gt=0, le=400)
    meter: str = Field(pattern=r"^[1-9][0-9]?/[1-9][0-9]?$", max_length=8)
    total_bars: int = Field(gt=0)
    expected_duration_seconds: float = Field(gt=0)
    grid_duration_seconds: float = Field(gt=0)
    master_duration_seconds: float | None = Field(default=None, gt=0)
    tail_duration_seconds: float | None = Field(default=None, ge=0)
    width: int = Field(gt=0, le=7680)
    height: int = Field(gt=0, le=4320)
    fps: int = Field(gt=0, le=240)


class SpectrumTimelineSectionSummary(RendererModel):
    id: Literal["intro", "main", "outro", "post-grid-tail"]
    label: str = Field(min_length=1, max_length=80)
    start_seconds: float = Field(ge=0, le=192)
    end_seconds: float | None = Field(default=None, gt=0)
    spectrum_color: str = Field(pattern=r"^#[0-9A-F]{6}$")


class GenerativeGeometrySummary(RendererModel):
    enabled: bool
    subsystem_id: Literal["wzhk-generative-geometry"]
    render_mode: Literal["neopixel-points"]
    seed: int = Field(ge=0, le=2_147_483_647)
    point_count: int = Field(ge=1_024, le=8_192)
    performance_profile: Literal["preview", "production", "high"]
    shape_families: list[str] = Field(min_length=6, max_length=32)


class SpectrumDesignPresetSummary(RendererModel):
    preset_id: Literal["scattered"]
    display_name: str = Field(min_length=1, max_length=120)
    preview_timing_source: Literal["external-media-player-position"]
    production_timing_source: Literal["trackprompt-production-clock"]
    preview_timing_accuracy: Literal["preview-level"]
    production_timing_accuracy: Literal["host-monotonic-process-boundary"]
    progress_visible: bool
    background_mode: Literal["static-structured", "generative-geometry"]
    generative_geometry: GenerativeGeometrySummary
    sections: list[SpectrumTimelineSectionSummary] = Field(min_length=4, max_length=4)


class RendererDescriptor(RendererModel):
    renderer_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,79}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    platform: Literal["cross-platform", "windows"]
    capabilities: list[str] = Field(default_factory=list)
    availability: RendererAvailabilityState
    available: bool
    preparation_available: bool
    preview_availability: SpectrumProductionAvailability | None = None
    capture_availability: SpectrumProductionAvailability | None = None
    preview_available: bool = False
    capture_available: bool = False
    warnings: list[str] = Field(default_factory=list)
    requirements: list[RendererRequirement] = Field(default_factory=list)
    contract_summary: RendererContractSummary | None = None
    design_preset: SpectrumDesignPresetSummary | None = None
    geometry_capability: GeometryCapabilityEvidence | None = None


class RendererRegistryResponse(RendererModel):
    renderers: list[RendererDescriptor]


class SpectrumWorkspacePrepareRequest(RendererModel):
    contract_id: Literal["scattered"] = "scattered"
    preset_id: Literal["scattered"] = "scattered"
    mode: Literal["preview", "production"] = "preview"
    background_mode: Literal["static-structured", "generative-geometry"] | None = None
    preview_section: SpectrumPreviewSection = None
    visual_overrides: SpectrumVisualOverrides = Field(default_factory=SpectrumVisualOverrides)
    generative_preview: GeometryPreviewOverride | None = None

    @model_validator(mode="after")
    def prevent_preview_override_in_production(self) -> SpectrumWorkspacePrepareRequest:
        if self.mode == "production" and (
            self.preview_section is not None or self.generative_preview is not None
        ):
            raise ValueError("production workspaces cannot use preview overrides")
        return self


class SpectrumWorkspaceState(StrEnum):
    PREPARED = "PREPARED"


class SpectrumWorkspaceJob(RendererModel):
    schema_version: Literal["1.0.0", "2.0.0", "3.0.0", "4.0.0"] = "4.0.0"
    job_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    renderer_id: Literal["wzhk-spectrum"] = "wzhk-spectrum"
    state: SpectrumProductionState | Literal[SpectrumWorkspaceState.PREPARED] = SpectrumProductionState.WORKSPACE_READY
    workspace_relative_path: str = Field(pattern=r"^wzhk-spectrum/jobs/[0-9a-f-]{36}$")
    contract_valid: bool
    branding_applied: bool
    vendor_unchanged: bool
    generated_workspace_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    vendor_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    vendor_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    logo_resolved: bool
    master_audio_resolved: bool
    warnings: list[str] = Field(default_factory=list)
    contract_summary: RendererContractSummary
    mode: Literal["preview", "production"] = "preview"
    background_mode: Literal["static-structured", "generative-geometry"] = "static-structured"
    preset_id: Literal["scattered"] | None = None
    preset_name: str | None = Field(default=None, min_length=1, max_length=120)
    composition_revision: Literal["scattered-geometry-first-3.7"] | None = None
    preview_section: SpectrumPreviewSection = None
    generative_preview_override: GeometryPreviewOverride | None = None
    design_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    timing_source: Literal["external-media-player-position", "trackprompt-production-clock"] | None = None
    timing_accuracy: Literal["preview-level", "host-monotonic-process-boundary"] | None = None
    timeline_controller_version: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    visual_qa_required: bool = True
    master_timing: SpectrumMasterTiming | None = None
    production_availability: SpectrumProductionAvailability | None = None
    capture_preflight: CapturePreflightResult | None = None
    artifacts: list[SpectrumArtifact] = Field(default_factory=list)
    synchronization: CaptureSynchronization | None = None
    validation_report: SpectrumValidationReport | None = None
    capture_provider: str | None = Field(default=None, max_length=80)
    encoder: str | None = Field(default=None, max_length=80)
    captured_frames: int | None = Field(default=None, ge=0)
    dropped_frames: int | None = Field(default=None, ge=0)
    capture_duration_seconds: float | None = Field(default=None, ge=0)
    error_message: str | None = Field(default=None, max_length=500)
    geometry_capability: GeometryCapabilityEvidence | None = None
    geometry_telemetry: GeometryRuntimeTelemetry | None = None


class SpectrumCapturePreflightRequest(RendererModel):
    refresh: bool = True


class SpectrumProductionStartRequest(RendererModel):
    operator_confirmed: Literal[True]
    confirmation_phrase: Literal["START WZHK SCATTERED CAPTURE"]


class SpectrumProductionCancelRequest(RendererModel):
    reason: str = Field(min_length=3, max_length=240)

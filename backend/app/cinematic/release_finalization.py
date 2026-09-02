from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeVar

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from ..schemas import APIModel
from .operator_authorization import AndromedaV2ReleaseHold
from .production_contracts import (
    ANDROMEDA_V2_FRAME_END,
    ANDROMEDA_V2_FRAME_START,
    ANDROMEDA_V2_SEED,
    ENCODING_PROFILES_SHA256,
    OWNER_CREATIVE_ACCEPTANCE_SHA256,
    SOURCE_AUDIO_SHA256,
    SOURCE_CUE_SHA256,
    AndromedaV2FinalCalibration,
    AndromedaV2FinalPackageManifest,
    AndromedaV2TechnicalAuthorization,
    EnabledFinalOutputMatrix,
    EnabledFinalOutputVariant,
    ExplicitOperatorStartGate,
    FinalLookProfile,
    FinalReleaseIdentity,
    FinalReleaseObjectiveGate,
    FinalReleaseObjectiveGateId,
    FinalReleaseWorkerRequirement,
    FinalStageForecast,
    FinalVariantCalibration,
    OutputVariantSet,
    OwnerAttestedCreativeAcceptance,
    PackageArtifact,
    PrivateSourceBinding,
    ShotPlanV2,
    StoryPlanV2,
    file_sha256,
    final_release_identity_sha256,
    load_and_validate_final_release,
    validate_final_frame_output_pattern,
)

HORIZONTAL_VARIANT_ID = "horizontal-16x9-1080p"
VERTICAL_VARIANT_ID = "vertical-9x16-1080p"
_HEX_64 = r"^[0-9a-f]{64}$"
_COMMIT_SHA = r"^[0-9a-f]{40}$"
_RELEASE_TAG = r"^[a-z0-9][a-z0-9-]{0,47}$"
_ACT_IDS = (
    "signal",
    "awakening",
    "departure",
    "gates",
    "rupture",
    "transformation",
    "arrival",
)
_CALIBRATION_EFFECT_CLASSES = (
    "simple-dark",
    "dense-architecture",
    "transparency-heavy",
    "gate-compression",
    "rupture-debris-peak",
    "transformation-peak",
    "arrival-depth-volume-peak",
    "expensive-shot-boundary",
)
_CALIBRATION_STAGES = (
    "scene-package-preparation",
    "cache-bake",
    "image-sequence-render",
    "frame-validation",
    "encoding",
    "final-qa",
    "publication",
    "contingency",
)
_VERIFICATION_CHECK_IDS = (
    "backend-pytest",
    "backend-ruff",
    "backend-mypy",
    "blender-tooling-tests",
    "mission-control-generic-fixture-tests",
    "frontend-unit-tests",
    "frontend-lint",
    "frontend-typecheck",
    "frontend-build",
    "frontend-e2e",
    "dependency-import-diagnostics",
    "powershell-parser-harness-launcher",
    "compose-base-config",
    "compose-gpu-config",
    "proof-regeneration",
    "git-diff-check",
)
_RELEASE_OWNED_GIT_PATHS = (
    "backend/app/cinematic",
    "backend/app/mission_control",
    "backend/tests/test_andromeda_release_finalization.py",
    "backend/tests/test_andromeda_operator_authorization.py",
    "blender/trackprompt_visualizer",
    "blender/tests/test_andromeda_story_v2.py",
    "docs/andromeda-v2-production-runbook.md",
    "docs/andromeda-v2-release-finalization.md",
    "frontend/src/mission-control",
    "production/andromeda-v2",
    "tools/andromeda_operator_authorization.py",
    "tools/andromeda_release_finalization.py",
)
_RELEASE_HOLD_PATH = PurePosixPath("production/andromeda-v2/release-hold.json")

_GENERATED_ARTIFACT_ROLES = frozenset(
    {
        "final-calibration-v2",
        "technical-authorization-v2",
        "release-report",
    }
)
_REQUIRED_INPUT_ARTIFACT_ROLES = frozenset(
    {
        "final-scene",
        "output-variants",
        "story-plan",
        "shot-plan",
        "owner-creative-acceptance",
        "final-look-profile",
        "horizontal-render-profile",
        "encoding-profiles",
        "final-resolution-calibration-evidence",
        "deterministic-effects-and-disk-report",
        "live-dashboard-proof",
        "full-audio-animatic",
        "animatic-media-qa-report",
        "animatic-receipt",
        "dependency-health-report",
        "source-revision-report",
        "verification-report",
        "worker-requirements",
        "human-visual-qa-approval",
        "human-review-closure",
        "final-scene-receipt",
        "horizontal-scene-build-receipt",
        "motion-health-report",
        "exposure-mobile-readability-report",
        "final-quality-transition-report",
        "gates-to-rupture-media",
        "rupture-to-transformation-media",
        "transformation-to-arrival-media",
        "vertical-composition-proof",
        "vertical-master-scene",
        "vertical-render-profile",
        "vertical-scene-build-receipt",
        "vertical-bounded-proof-media",
        "vertical-bounded-proof-media-qa",
        "hardware-and-storage-report",
        "builder-source",
    }
)
_TECHNICAL_PASS_ROLES = frozenset(
    {
        "deterministic-effects-and-disk-report",
        "live-dashboard-proof",
        "animatic-media-qa-report",
        "animatic-receipt",
        "dependency-health-report",
        "motion-health-report",
        "exposure-mobile-readability-report",
        "final-quality-transition-report",
        "vertical-composition-proof",
        "vertical-bounded-proof-media-qa",
        "hardware-and-storage-report",
        "final-resolution-calibration-evidence",
        "verification-report",
    }
)
_NON_JSON_INPUT_ARTIFACT_ROLES = frozenset(
    {
        "final-scene",
        "full-audio-animatic",
        "gates-to-rupture-media",
        "rupture-to-transformation-media",
        "transformation-to-arrival-media",
        "vertical-master-scene",
        "vertical-bounded-proof-media",
        "builder-source",
    }
)

_OBJECTIVE_GATE_EVIDENCE: dict[FinalReleaseObjectiveGateId, tuple[str, ...]] = {
    FinalReleaseObjectiveGateId.DETERMINISTIC_EFFECTS_AND_DISK: (
        "deterministic-effects-and-disk-report",
    ),
    FinalReleaseObjectiveGateId.LIVE_DASHBOARD: ("live-dashboard-proof",),
    FinalReleaseObjectiveGateId.ANIMATIC_AND_MEDIA_QA: (
        "full-audio-animatic",
        "animatic-media-qa-report",
    ),
    FinalReleaseObjectiveGateId.CALIBRATION_AND_ENABLED_MATRIX_SLA: (
        "final-calibration-v2",
    ),
    FinalReleaseObjectiveGateId.DEPENDENCY_HEALTH: ("dependency-health-report",),
    FinalReleaseObjectiveGateId.ENABLED_OUTPUT_MATRIX_IDENTITY: (
        "output-variants",
    ),
    FinalReleaseObjectiveGateId.SCENE_PROFILE_SOURCE_IDENTITY: (
        "final-scene",
        "story-plan",
        "shot-plan",
        "owner-creative-acceptance",
        "final-look-profile",
        "horizontal-render-profile",
        "encoding-profiles",
        "source-revision-report",
    ),
    FinalReleaseObjectiveGateId.WORKER_REQUIREMENTS: ("worker-requirements",),
}

_OBJECTIVE_GATE_SUMMARIES: dict[FinalReleaseObjectiveGateId, str] = {
    FinalReleaseObjectiveGateId.DETERMINISTIC_EFFECTS_AND_DISK: (
        "Exact deterministic-effects, bake, VRAM, and disk-headroom evidence passed."
    ),
    FinalReleaseObjectiveGateId.LIVE_DASHBOARD: (
        "The local dashboard evidence passed actual-frame, persistence, stop, retry, and ETA checks."
    ),
    FinalReleaseObjectiveGateId.ANIMATIC_AND_MEDIA_QA: (
        "The exact full-audio horizontal animatic and its media QA passed."
    ),
    FinalReleaseObjectiveGateId.CALIBRATION_AND_ENABLED_MATRIX_SLA: (
        "The exact horizontal-only calibration is complete and its aggregate P90 is within 24 hours."
    ),
    FinalReleaseObjectiveGateId.DEPENDENCY_HEALTH: (
        "The exact final scene and local dependency-health evidence passed."
    ),
    FinalReleaseObjectiveGateId.ENABLED_OUTPUT_MATRIX_IDENTITY: (
        "Only the required horizontal variant is enabled; authored vertical remains disabled."
    ),
    FinalReleaseObjectiveGateId.SCENE_PROFILE_SOURCE_IDENTITY: (
        "Scene, profile, story, source, look, acceptance, and revision hashes agree."
    ),
    FinalReleaseObjectiveGateId.WORKER_REQUIREMENTS: (
        "The exact enabled variant resolves a matching deterministic worker requirement."
    ),
}
_MODEL = TypeVar("_MODEL", bound=BaseModel)


class ReleaseFinalizationError(ValueError):
    """A fail-closed Andromeda release-finalization failure."""


class FlexibleAPIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )


class ExplicitArtifact(APIModel):
    role: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    path: str
    sha256: str = Field(pattern=_HEX_64)

    @field_validator("path")
    @classmethod
    def safe_repository_path(cls, value: str) -> str:
        candidate = PurePosixPath(value)
        if (
            not value
            or value != value.strip()
            or candidate.is_absolute()
            or candidate == PurePosixPath(".")
            or candidate.as_posix() != value
            or ".." in candidate.parts
            or "\\" in value
            or (candidate.parts and ":" in candidate.parts[0])
        ):
            raise ValueError("artifact paths must be normalized repository-relative POSIX paths")
        return value


class PrivateSourceInput(APIModel):
    role: Literal["source-audio", "source-cue"]
    sha256: str = Field(pattern=_HEX_64)
    size_bytes: int = Field(ge=1)
    private_local_artifact: Literal[True]
    committed: Literal[False]


class ProfileVariantPreparation(APIModel):
    base_profile: ExplicitArtifact
    scene: ExplicitArtifact
    build_receipt: ExplicitArtifact


class ProfilePreparationRequest(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-profile-preparation-request"]
    release_tag: str = Field(pattern=_RELEASE_TAG)
    builder_source: ExplicitArtifact
    horizontal: ProfileVariantPreparation
    vertical: ProfileVariantPreparation

    @model_validator(mode="after")
    def exact_roles(self) -> ProfilePreparationRequest:
        expected = {
            "builder_source": "builder-source",
            "horizontal.base_profile": "horizontal-base-render-profile",
            "horizontal.scene": "horizontal-scene",
            "horizontal.build_receipt": "horizontal-scene-build-receipt",
            "vertical.base_profile": "vertical-base-render-profile",
            "vertical.scene": "vertical-scene",
            "vertical.build_receipt": "vertical-scene-build-receipt",
        }
        actual = {
            "builder_source": self.builder_source.role,
            "horizontal.base_profile": self.horizontal.base_profile.role,
            "horizontal.scene": self.horizontal.scene.role,
            "horizontal.build_receipt": self.horizontal.build_receipt.role,
            "vertical.base_profile": self.vertical.base_profile.role,
            "vertical.scene": self.vertical.scene.role,
            "vertical.build_receipt": self.vertical.build_receipt.role,
        }
        if actual != expected:
            raise ValueError(f"profile-preparation artifact roles are invalid: {actual}")
        return self


class SceneBuildComposition(FlexibleAPIModel):
    camera: str
    composition_id: Literal[
        "horizontal-16x9-1080p",
        "vertical-9x16-1080p",
    ]
    crop_policy: Literal["native-authored-never-crop"]
    height: int
    output_variant_id: Literal[
        "horizontal-16x9-1080p",
        "vertical-9x16-1080p",
    ]
    width: int


class SceneBuildAudio(FlexibleAPIModel):
    attached: Literal[True]
    frame_start: Literal[1]
    sha256: str = Field(pattern=_HEX_64)


class SceneBuildVisualCues(FlexibleAPIModel):
    applied: Literal[True]
    controls_major_camera_or_protagonist_travel: Literal[False]
    sha256: str = Field(pattern=_HEX_64)
    supplied: Literal[True]


class SceneBuildRenderMode(FlexibleAPIModel):
    locked_settings_satisfied: Literal[True]
    mode: Literal["master"]
    motion_blur: Literal[False]
    output_mode: Literal["png-master-sequence"]
    render_started: Literal[False]
    resolution_percentage: Literal[100]
    temporal_samples: Literal[64]
    volumetric_samples: Literal[32]


class SceneBuildReceipt(FlexibleAPIModel):
    schema_version: Literal["1.0.0"]
    project_id: Literal["trip-to-andromeda-v2"]
    builder_id: str = Field(pattern=r"^andromeda-v2-master-scene-builder-v[0-9]+$")
    builder_source_sha256: str = Field(pattern=_HEX_64)
    output_blend: str = Field(min_length=1)
    composition: SceneBuildComposition
    audio: SceneBuildAudio
    visual_cues: SceneBuildVisualCues
    render_mode: SceneBuildRenderMode
    frame_start: Literal[1]
    frame_end: Literal[13029]
    fps: Literal[30]
    act_count: Literal[7]
    shot_count: Literal[35]
    scene_spec_sha256: str = Field(pattern=_HEX_64)
    production_authorized: Literal[False]
    render_started: Literal[False]

    @model_validator(mode="after")
    def exact_private_sources(self) -> SceneBuildReceipt:
        if self.audio.sha256 != SOURCE_AUDIO_SHA256:
            raise ValueError("scene build receipt source-audio hash is invalid")
        if self.visual_cues.sha256 != SOURCE_CUE_SHA256:
            raise ValueError("scene build receipt source-cue hash is invalid")
        return self


class ProfileResolution(FlexibleAPIModel):
    width: int
    height: int
    percentage: Literal[100]
    pixel_aspect_x: Literal[1]
    pixel_aspect_y: Literal[1]


class ProfileImageColorManagement(FlexibleAPIModel):
    display_transform_baked: Literal[True]
    encoded_color_space: Literal["sRGB"]


class ProfileImageSequence(FlexibleAPIModel):
    format: Literal["PNG"]
    extension: Literal["png"]
    bit_depth: Literal[16]
    color_mode: Literal["RGB"]
    filename_pattern: Literal["frame_%06d.png"]
    color_management: ProfileImageColorManagement


class ProfileSourceIdentities(FlexibleAPIModel):
    builder_source_sha256: str = Field(pattern=_HEX_64)
    scene_build_receipt_sha256: str = Field(pattern=_HEX_64)
    scene_spec_sha256: str = Field(pattern=_HEX_64)
    source_cue_sha256: str = Field(pattern=_HEX_64)


class ProfileOutputVariant(FlexibleAPIModel):
    id: Literal["horizontal-16x9-1080p", "vertical-9x16-1080p"]
    enabled: bool
    required: bool
    width: int
    height: int
    fps: Literal[30]
    composition_mode: Literal["authored"]
    composition_profile_id: str = Field(
        pattern=r"^andromeda-v2-(horizontal|vertical)-[a-z0-9-]+$"
    )
    camera_name: str = Field(pattern=r"^TP_ANDROMEDA_V2_CAMERA_[A-Z0-9_]+$")
    crop_policy: Literal["native-authored-never-crop"]
    render_profile_id: str = Field(min_length=1)


class ProfileRenderSettings(FlexibleAPIModel):
    engine: Literal["BLENDER_EEVEE"]
    samples: Literal[64]
    motion_blur: Literal[False]
    use_compositing: Literal[False]
    film_transparent: Literal[False]
    volumetric_samples: Literal[32]


class ProfileAudio(FlexibleAPIModel):
    sha256: str = Field(pattern=_HEX_64)
    sample_rate: Literal[44100]
    channels: Literal[2]
    creative_processing: Literal["none"]


class ProfileProduction(FlexibleAPIModel):
    resume_enabled: Literal[True]
    resume_policy: Literal["validated-missing-frames-only"]
    verify_existing_frames: Literal[True]
    overwrite_valid_frames: Literal[False]
    overwrite_invalid_frames: Literal[False]
    atomic_chunk_commit: Literal[True]
    atomic_publication: Literal[True]
    stop_on_validation_failure: Literal[True]
    maximum_frames_per_chunk: int = Field(ge=1, le=300)


class ProfileWorkerRequirement(FlexibleAPIModel):
    device_class: Literal["gpu"]
    renderer: Literal["BLENDER_EEVEE"]
    blender_version: Literal["5.2"]
    minimum_vram_mib: int = Field(
        ge=8192,
        validation_alias=AliasChoices("minimumVramMiB", "minimumVramMib"),
        serialization_alias="minimumVramMiB",
    )
    maximum_workers_per_device: Literal[1]
    deterministic_seed: Literal[84291]


class ProfileAuthorization(FlexibleAPIModel):
    status: Literal[
        "pending-operator-approval",
        "disabled-pending-separate-calibration-and-operator-approval",
    ]
    production_start_allowed: Literal[False]
    final_render_started: Literal[False]
    explicit_operator_authorization_required: Literal[True]


class ReleaseFinalizationProfileMetadata(FlexibleAPIModel):
    release_tag: str = Field(pattern=_RELEASE_TAG)
    base_profile_sha256: str = Field(pattern=_HEX_64)
    scene_path: str
    scene_sha256: str = Field(pattern=_HEX_64)
    scene_build_receipt_path: str
    scene_build_receipt_sha256: str = Field(pattern=_HEX_64)
    production_render_started: Literal[False]
    operator_start_authorization_created: Literal[False]


class VersionedRenderProfile(FlexibleAPIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-render-profile"]
    project: Literal["trip-to-andromeda-v2"]
    preset: Literal["andromeda-story-v2"]
    profile_id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=200)
    blender_version: Literal["5.2.0 LTS"]
    frame_start: Literal[1]
    frame_end: Literal[13029]
    fps: Literal[30]
    resolution: ProfileResolution
    image_sequence: ProfileImageSequence
    approved_scene_sha256: str = Field(pattern=_HEX_64)
    source_identities: ProfileSourceIdentities
    output_variant: ProfileOutputVariant
    render: ProfileRenderSettings
    audio: ProfileAudio
    production: ProfileProduction
    worker_requirement: ProfileWorkerRequirement
    authorization: ProfileAuthorization
    release_finalization: ReleaseFinalizationProfileMetadata
    production_start_allowed: Literal[False]
    final_render_started: Literal[False]

    @model_validator(mode="after")
    def exact_variant_contract(self) -> VersionedRenderProfile:
        variant = self.output_variant
        expected = (
            (1920, 1080, True, True, "pending-operator-approval")
            if variant.id == HORIZONTAL_VARIANT_ID
            else (
                1080,
                1920,
                False,
                False,
                "disabled-pending-separate-calibration-and-operator-approval",
            )
        )
        actual = (
            self.resolution.width,
            self.resolution.height,
            variant.enabled,
            variant.required,
            self.authorization.status,
        )
        if actual != expected:
            raise ValueError(f"render profile geometry/gate does not match {variant.id}")
        if (variant.width, variant.height) != (
            self.resolution.width,
            self.resolution.height,
        ):
            raise ValueError("render-profile variant and resolution geometry disagree")
        if self.audio.sha256 != SOURCE_AUDIO_SHA256:
            raise ValueError("render profile source-audio hash is invalid")
        if self.source_identities.source_cue_sha256 != SOURCE_CUE_SHA256:
            raise ValueError("render profile source-cue hash is invalid")
        if self.approved_scene_sha256 != self.release_finalization.scene_sha256:
            raise ValueError("render profile scene binding is internally inconsistent")
        if (
            self.source_identities.scene_build_receipt_sha256
            != self.release_finalization.scene_build_receipt_sha256
        ):
            raise ValueError("render profile build-receipt binding is inconsistent")
        return self


def _timezone_bound(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value


def _safe_evidence_path(value: str) -> str:
    candidate = PurePosixPath(value)
    if (
        not value
        or value != value.strip()
        or candidate.is_absolute()
        or candidate == PurePosixPath(".")
        or candidate.as_posix() != value
        or ".." in candidate.parts
        or "\\" in value
        or (candidate.parts and ":" in candidate.parts[0])
    ):
        raise ValueError("evidence paths must be normalized repository-relative POSIX paths")
    return value


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("a percentile requires at least one measurement")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


class EvidenceFile(APIModel):
    path: str
    sha256: str = Field(pattern=_HEX_64)
    size_bytes: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return _safe_evidence_path(value)


class EvidenceImage(EvidenceFile):
    format: Literal["PNG"]
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class EvidenceMedia(EvidenceFile):
    container: Literal["mp4"]
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    fps: Literal[30]
    duration_seconds: float = Field(gt=0.0)
    video_codec: Literal["h264"]
    pixel_format: Literal["yuv420p"]
    audio_codec: Literal["aac"]
    audio_sample_rate: Literal[44100]
    audio_channels: Literal[2]


class HumanVisualQaApproval(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-human-visual-qa-approval"]
    approval_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    project_id: Literal["trip-to-andromeda-v2"]
    reviewed_by_role: Literal["project-owner-operator", "human-artist"]
    reviewed_at: datetime
    decision: Literal["approved-for-final-release"]
    reference_revision: Literal["andromeda-r13.1-selected-refinement"]
    scene_sha256: str = Field(pattern=_HEX_64)
    horizontal_render_profile_sha256: str = Field(pattern=_HEX_64)
    full_audio_animatic_sha256: str = Field(pattern=_HEX_64)
    animatic_media_qa_report_sha256: str = Field(pattern=_HEX_64)
    final_scene_receipt_sha256: str = Field(pattern=_HEX_64)
    motion_health_report_sha256: str = Field(pattern=_HEX_64)
    exposure_mobile_readability_report_sha256: str = Field(pattern=_HEX_64)
    final_quality_transition_report_sha256: str = Field(pattern=_HEX_64)
    vertical_composition_proof_sha256: str = Field(pattern=_HEX_64)
    vertical_render_profile_sha256: str = Field(pattern=_HEX_64)
    vertical_bounded_proof_media_sha256: str = Field(pattern=_HEX_64)
    all_seven_acts_at_r131_project_level: Literal[True]
    phone_size_readability_approved: Literal[True]
    final_quality_transitions_approved: Literal[True]
    known_blocking_findings: list[str] = Field(max_length=0)
    human_artistic_approval: Literal[True]
    codex_human_artistic_approval: Literal[False]
    production_render_started: Literal[False]

    @field_validator("reviewed_at")
    @classmethod
    def timezone_bound_review(cls, value: datetime) -> datetime:
        return _timezone_bound(value, label="human visual-QA approval timestamp")


class CalibrationFrameMeasurement(APIModel):
    frame: int = Field(ge=ANDROMEDA_V2_FRAME_START, le=ANDROMEDA_V2_FRAME_END)
    act_id: Literal[
        "signal",
        "awakening",
        "departure",
        "gates",
        "rupture",
        "transformation",
        "arrival",
    ]
    effect_classes: list[
        Literal[
            "simple-dark",
            "dense-architecture",
            "transparency-heavy",
            "gate-compression",
            "rupture-debris-peak",
            "transformation-peak",
            "arrival-depth-volume-peak",
            "expensive-shot-boundary",
        ]
    ] = Field(min_length=1)
    expensive_shot_phase: Literal["start", "middle", "end", "not-applicable"]
    render_seconds: float = Field(gt=0.0)
    forecast_weight: float = Field(gt=0.0, le=1.0)
    started_at: datetime
    completed_at: datetime
    warm_renderer: Literal[True]
    image: EvidenceImage
    renderer_log: EvidenceFile

    @model_validator(mode="after")
    def exact_measurement(self) -> CalibrationFrameMeasurement:
        _timezone_bound(self.started_at, label="calibration sample startedAt")
        _timezone_bound(self.completed_at, label="calibration sample completedAt")
        elapsed = (self.completed_at - self.started_at).total_seconds()
        tolerance = max(0.25, self.render_seconds * 0.05)
        if elapsed <= 0 or not math.isclose(
            elapsed,
            self.render_seconds,
            rel_tol=0.0,
            abs_tol=tolerance,
        ):
            raise ValueError(
                "calibration renderSeconds must match timezone-bound wall-clock timestamps"
            )
        if len(set(self.effect_classes)) != len(self.effect_classes):
            raise ValueError("calibration effect classes must be unique per sample")
        boundary = "expensive-shot-boundary" in self.effect_classes
        if boundary != (self.expensive_shot_phase != "not-applicable"):
            raise ValueError(
                "expensive-shot-boundary samples must declare start, middle, or end"
            )
        if (
            self.image.format != "PNG"
            or self.image.width != 1920
            or self.image.height != 1080
        ):
            raise ValueError("calibration samples must be final-resolution 1920x1080 PNGs")
        return self


class CalibrationStageForecast(APIModel):
    stage: Literal[
        "scene-package-preparation",
        "cache-bake",
        "image-sequence-render",
        "frame-validation",
        "encoding",
        "final-qa",
        "publication",
        "contingency",
    ]
    p50_seconds: float = Field(ge=0.0)
    p90_seconds: float = Field(ge=0.0)
    method: Literal[
        "measured-local-run",
        "derived-from-frame-measurements",
        "measured-throughput",
        "fixed-operational-reserve",
    ]
    evidence: EvidenceFile
    measurement_count: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered_forecast(self) -> CalibrationStageForecast:
        if self.p50_seconds > self.p90_seconds:
            raise ValueError("stage P50 may not exceed P90")
        if self.stage == "contingency" and self.p90_seconds <= 0:
            raise ValueError("calibration must reserve a positive P90 contingency")
        if (
            self.stage == "image-sequence-render"
            and self.method != "derived-from-frame-measurements"
        ):
            raise ValueError("render forecast must be derived from measured final frames")
        return self


class FinalResolutionCalibrationEvidence(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-final-resolution-calibration-evidence"]
    project_id: Literal["trip-to-andromeda-v2"]
    output_variant_id: Literal["horizontal-16x9-1080p"]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    builder_source_sha256: str = Field(pattern=_HEX_64)
    worker_requirement_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    hardware_report_sha256: str = Field(pattern=_HEX_64)
    implementation_commit_sha: str = Field(pattern=_COMMIT_SHA)
    source_tree_sha256: str = Field(pattern=_HEX_64)
    frame_start: Literal[1]
    frame_end: Literal[13029]
    frame_count: Literal[13029]
    width: Literal[1920]
    height: Literal[1080]
    fps: Literal[30]
    renderer_warmup_complete: Literal[True]
    samples: list[CalibrationFrameMeasurement] = Field(min_length=10)
    weighted_projected_render_seconds: float = Field(gt=0.0)
    projected_output_bytes: int = Field(ge=1)
    stage_forecasts: list[CalibrationStageForecast] = Field(
        min_length=8,
        max_length=8,
    )
    final_resolution_verified: Literal[True]
    representative_coverage_verified: Literal[True]
    technical_pass: Literal[True]
    production_render_started: Literal[False]

    @model_validator(mode="after")
    def measured_complete_calibration(
        self,
    ) -> FinalResolutionCalibrationEvidence:
        frames = [sample.frame for sample in self.samples]
        if len(set(frames)) != len(frames):
            raise ValueError("calibration sample frames must be unique")
        if {sample.act_id for sample in self.samples} != set(_ACT_IDS):
            raise ValueError("calibration must include every Andromeda act")
        effect_classes = {
            effect_class
            for sample in self.samples
            for effect_class in sample.effect_classes
        }
        if effect_classes != set(_CALIBRATION_EFFECT_CLASSES):
            raise ValueError(
                "calibration must include every required expensive effect class"
            )
        phases = {
            sample.expensive_shot_phase
            for sample in self.samples
            if sample.expensive_shot_phase != "not-applicable"
        }
        if phases != {"start", "middle", "end"}:
            raise ValueError(
                "calibration must measure start, middle, and end of expensive shots"
            )
        weight = math.fsum(sample.forecast_weight for sample in self.samples)
        if not math.isclose(weight, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("calibration forecast weights must sum to exactly 1.0")
        durations = [sample.render_seconds for sample in self.samples]
        weighted_seconds = math.fsum(
            sample.render_seconds * sample.forecast_weight
            for sample in self.samples
        )
        expected_weighted_projection = weighted_seconds * self.frame_count
        if not math.isclose(
            self.weighted_projected_render_seconds,
            expected_weighted_projection,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise ValueError(
                "weighted render projection must be recomputed for exactly 13,029 frames"
            )
        if [forecast.stage for forecast in self.stage_forecasts] != list(
            _CALIBRATION_STAGES
        ):
            raise ValueError(
                "calibration stages must include preparation, cache/bake, render, "
                "validation, encode, QA, publication, and contingency in order"
            )
        render_forecast = self.stage_forecasts[2]
        expected_p50 = _nearest_rank_percentile(durations, 0.50) * self.frame_count
        expected_p90 = _nearest_rank_percentile(durations, 0.90) * self.frame_count
        if not math.isclose(
            render_forecast.p50_seconds,
            expected_p50,
            rel_tol=0.0,
            abs_tol=1e-6,
        ) or not math.isclose(
            render_forecast.p90_seconds,
            expected_p90,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise ValueError(
                "image-sequence forecast must equal measured P50/P90 times 13,029"
            )
        aggregate_p90 = math.fsum(
            forecast.p90_seconds for forecast in self.stage_forecasts
        )
        if aggregate_p90 > 86_400:
            raise ValueError("horizontal-only aggregate P90 exceeds the 24-hour SLA")
        return self


class HardwareOperatingSystem(APIModel):
    family: Literal["Windows"]
    version: str = Field(min_length=1, max_length=120)
    build: str = Field(min_length=1, max_length=120)


class HardwareCpu(APIModel):
    model: str = Field(min_length=1, max_length=240)
    logical_cores: int = Field(ge=1)


class HardwareGpu(APIModel):
    model: str = Field(min_length=1, max_length=240)
    vram_bytes: int = Field(ge=1)
    driver_version: str = Field(min_length=1, max_length=120)


class ToolFingerprint(APIModel):
    version: str = Field(min_length=1, max_length=240)
    build: str = Field(min_length=1, max_length=240)
    executable_sha256: str = Field(pattern=_HEX_64)


class StorageFingerprint(APIModel):
    target_volume: str = Field(min_length=1, max_length=240)
    free_bytes: int = Field(ge=1)
    projected_peak_disk_bytes: int = Field(ge=1)
    safety_multiplier: float = Field(ge=1.25)
    measured_write_bytes_per_second: float = Field(gt=0.0)
    throughput_evidence: EvidenceFile
    headroom_satisfied: Literal[True]

    @model_validator(mode="after")
    def actual_headroom(self) -> StorageFingerprint:
        if self.free_bytes < self.projected_peak_disk_bytes * self.safety_multiplier:
            raise ValueError("hardware report lacks 25 percent disk safety headroom")
        return self


class HardwareAndStorageReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-hardware-and-storage-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    recorded_at: datetime
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    worker_requirement_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    operating_system: HardwareOperatingSystem
    cpu: HardwareCpu
    ram_bytes: int = Field(ge=1)
    gpus: list[HardwareGpu] = Field(min_length=1)
    blender: ToolFingerprint
    ffmpeg: ToolFingerprint
    ffprobe: ToolFingerprint
    storage: StorageFingerprint
    ac_power_connected: Literal[True]
    sleep_risk_acknowledged: Literal[True]
    system_configuration_changed_by_tool: Literal[False]
    vram_stable_during_bounded_evidence: Literal[True]
    technical_pass: Literal[True]
    production_render_started: Literal[False]

    @field_validator("recorded_at")
    @classmethod
    def timezone_bound_record(cls, value: datetime) -> datetime:
        return _timezone_bound(value, label="hardware report recordedAt")


class CompletedDashboardFrame(APIModel):
    output_variant_id: Literal["horizontal-16x9-1080p"]
    frame: int = Field(ge=1, le=13029)
    completed_at: datetime
    publication_started_at: datetime
    published_at: datetime
    publication_latency_seconds: float = Field(ge=0.0, le=2.0)
    image: EvidenceImage

    @field_validator("completed_at", "publication_started_at", "published_at")
    @classmethod
    def timezone_bound_frame_event(cls, value: datetime) -> datetime:
        return _timezone_bound(value, label="dashboard frame event timestamp")

    @model_validator(mode="after")
    def measured_publication_latency(self) -> CompletedDashboardFrame:
        if not (
            self.completed_at
            <= self.publication_started_at
            <= self.published_at
        ):
            raise ValueError(
                "dashboard publication timestamps must follow frame completion"
            )
        measured = (self.published_at - self.completed_at).total_seconds()
        if not math.isclose(
            self.publication_latency_seconds,
            measured,
            rel_tol=0.0,
            abs_tol=0.001,
        ):
            raise ValueError(
                "dashboard publicationLatencySeconds must match the "
                "completion-to-publication timestamps"
            )
        return self


class DashboardEtaEvidence(APIModel):
    updated_at: datetime
    completed_frame_count: int = Field(ge=1)
    render_p50_seconds_remaining: float = Field(ge=0.0)
    render_p90_seconds_remaining: float = Field(ge=0.0)
    aggregate_p50_seconds_remaining: float = Field(ge=0.0)
    aggregate_p90_seconds_remaining: float = Field(ge=0.0)
    persisted_after_restart: Literal[True]

    @model_validator(mode="after")
    def valid_eta(self) -> DashboardEtaEvidence:
        _timezone_bound(self.updated_at, label="dashboard ETA updatedAt")
        if (
            self.render_p50_seconds_remaining > self.render_p90_seconds_remaining
            or self.aggregate_p50_seconds_remaining
            > self.aggregate_p90_seconds_remaining
        ):
            raise ValueError("dashboard ETA P50 may not exceed P90")
        return self


class DashboardChecks(APIModel):
    latest_frame_endpoint_passed: Literal[True]
    eta_endpoint_passed: Literal[True]
    persistent_event_channel_passed: Literal[True]
    job_restoration_passed: Literal[True]
    stop_after_chunk_passed: Literal[True]
    retry_failed_chunk_passed: Literal[True]
    selected_variant_stream_passed: Literal[True]


class LiveDashboardProof(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-live-dashboard-proof"]
    project_id: Literal["trip-to-andromeda-v2"]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    source_revision_git_commit_sha: str = Field(pattern=_COMMIT_SHA)
    source_revision_source_tree_sha256: str = Field(pattern=_HEX_64)
    completed_frame: CompletedDashboardFrame
    eta: DashboardEtaEvidence
    checks: DashboardChecks
    full_production_render_started: Literal[False]
    technical_pass: Literal[True]


class DeterministicEffectsAndDiskReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-deterministic-effects-and-disk-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    deterministic_effects_verified: Literal[True]
    all_required_bakes_complete: Literal[True]
    unbaked_effect_count: Literal[0]
    deterministic_seed: Literal[84291]
    storage_free_bytes: int = Field(ge=1)
    storage_projected_peak_disk_bytes: int = Field(ge=1)
    storage_safety_multiplier: float = Field(ge=1.25)
    storage_headroom_satisfied: Literal[True]
    vram_stable: Literal[True]
    technical_pass: Literal[True]
    production_render_started: Literal[False]


class DependencyHealthReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-dependency-health-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    missing_dependency_count: Literal[0]
    external_dependency_count: int = Field(ge=0)
    source_audio_attached: Literal[True]
    visual_cues_applied: Literal[True]
    blender_load_passed: Literal[True]
    technical_pass: Literal[True]
    production_render_started: Literal[False]


class MotionHealthReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-motion-health-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    reviewed_frames: list[int] = Field(min_length=7)
    act_coverage: list[
        Literal[
            "signal",
            "awakening",
            "departure",
            "gates",
            "rupture",
            "transformation",
            "arrival",
        ]
    ] = Field(min_length=7, max_length=7)
    declared_cuts_verified: Literal[True]
    camera_transform_jumps_passed: Literal[True]
    protagonist_transform_jumps_passed: Literal[True]
    lens_jumps_passed: Literal[True]
    fcurve_overshoot_passed: Literal[True]
    raw_audio_major_motion_links_verified: Literal[True]
    technical_pass: Literal[True]
    production_render_started: Literal[False]

    @model_validator(mode="after")
    def exact_act_coverage(self) -> MotionHealthReport:
        if set(self.act_coverage) != set(_ACT_IDS):
            raise ValueError("motion health must cover every act")
        if len(set(self.reviewed_frames)) != len(self.reviewed_frames):
            raise ValueError("motion-health review frames must be unique")
        return self


class ExposureMobileReadabilityReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-exposure-mobile-readability-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    reviewed_frames: list[int] = Field(min_length=7)
    act_coverage: list[
        Literal[
            "signal",
            "awakening",
            "departure",
            "gates",
            "rupture",
            "transformation",
            "arrival",
        ]
    ] = Field(min_length=7, max_length=7)
    native_resolution_review_passed: Literal[True]
    phone_size_review_passed: Literal[True]
    protagonist_background_separation_passed: Literal[True]
    near_black_failure_count: Literal[0]
    clipped_highlight_failure_count: Literal[0]
    technical_pass: Literal[True]
    production_render_started: Literal[False]

    @model_validator(mode="after")
    def exact_act_coverage(self) -> ExposureMobileReadabilityReport:
        if set(self.act_coverage) != set(_ACT_IDS):
            raise ValueError("exposure/mobile readability must cover every act")
        return self


class AnimaticMediaQaReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-animatic-media-qa-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    output_variant_id: Literal["horizontal-16x9-1080p"]
    resolution_class: Literal["LOW"]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    encoded_media: EvidenceMedia
    source_audio_sha256: str = Field(pattern=_HEX_64)
    frame_start: Literal[1]
    frame_end: Literal[13029]
    video_frame_count: Literal[13029]
    audio_video_sync_error_seconds: float = Field(ge=0.0, le=0.05)
    ffprobe_passed: Literal[True]
    technical_pass: Literal[True]
    production_render_started: Literal[False]

    @model_validator(mode="after")
    def exact_low_resolution_geometry(self) -> AnimaticMediaQaReport:
        if self.encoded_media.width != 480 or self.encoded_media.height != 270:
            raise ValueError(
                "full-song LOW animatic media must be exactly 480x270"
            )
        return self


class AnimaticReceipt(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-animatic-receipt"]
    project_id: Literal["trip-to-andromeda-v2"]
    artifact: EvidenceFile
    audio_source_sha256: str = Field(pattern=_HEX_64)
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    story_plan_sha256: str = Field(pattern=_HEX_64)
    shot_plan_sha256: str = Field(pattern=_HEX_64)
    full_song_clock_preserved: Literal[True]
    technical_pass: Literal[True]
    production_render_started: Literal[False]


class FinalSceneReceipt(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-final-scene-receipt"]
    project_id: Literal["trip-to-andromeda-v2"]
    local_artifact: EvidenceFile
    build_receipt: EvidenceFile
    builder_source_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    frame_start: Literal[1]
    frame_end: Literal[13029]
    fps: Literal[30]
    technical_pass: Literal[True]
    production_render_started: Literal[False]


class TransitionSample(APIModel):
    id: Literal[
        "gates-to-rupture",
        "rupture-to-transformation",
        "transformation-to-arrival",
    ]
    frame_start: int = Field(ge=1, le=13029)
    frame_end: int = Field(ge=1, le=13029)
    media: EvidenceMedia
    media_qa: EvidenceFile
    continuous_sample: Literal[True]
    ffprobe_passed: Literal[True]

    @model_validator(mode="after")
    def continuous_range(self) -> TransitionSample:
        if self.frame_end < self.frame_start or self.frame_end - self.frame_start + 1 < 30:
            raise ValueError("transition samples must contain at least 30 continuous frames")
        return self


class TransitionMediaQaReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-transition-media-qa"]
    project_id: Literal["trip-to-andromeda-v2"]
    media_sha256: str = Field(pattern=_HEX_64)
    width: Literal[1920]
    height: Literal[1080]
    fps: Literal[30]
    frame_start: int = Field(ge=1, le=13029)
    frame_end: int = Field(ge=1, le=13029)
    frame_count: int = Field(ge=30)
    ffprobe_passed: Literal[True]
    frame_sequence_integrity_passed: Literal[True]
    audio_video_sync_passed: Literal[True]
    technical_pass: Literal[True]

    @model_validator(mode="after")
    def exact_range(self) -> TransitionMediaQaReport:
        if self.frame_end - self.frame_start + 1 != self.frame_count:
            raise ValueError("transition media-QA frame count must match its range")
        return self


class FinalQualityTransitionReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-final-quality-transition-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    samples: list[TransitionSample] = Field(min_length=3, max_length=3)
    final_resolution: Literal[True]
    source_audio_sha256: str = Field(pattern=_HEX_64)
    technical_pass: Literal[True]
    production_render_started: Literal[False]

    @model_validator(mode="after")
    def exact_transitions(self) -> FinalQualityTransitionReport:
        if [sample.id for sample in self.samples] != [
            "gates-to-rupture",
            "rupture-to-transformation",
            "transformation-to-arrival",
        ]:
            raise ValueError("transition report must contain the three transitions in order")
        return self


class VerticalBoundedProofMediaQa(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-vertical-bounded-proof-media-qa"]
    project_id: Literal["trip-to-andromeda-v2"]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    media: EvidenceMedia
    independently_authored: Literal[True]
    horizontal_crop_used: Literal[False]
    safe_zone_passed: Literal[True]
    subject_occupancy_passed: Literal[True]
    mobile_readability_passed: Literal[True]
    ffprobe_passed: Literal[True]
    technical_pass: Literal[True]
    production_render_started: Literal[False]

    @model_validator(mode="after")
    def exact_vertical_geometry(self) -> VerticalBoundedProofMediaQa:
        if self.media.width != 1080 or self.media.height != 1920:
            raise ValueError("vertical proof media must be 1080x1920")
        return self


class VerticalMasterSceneIdentity(APIModel):
    path: str
    sha256: str = Field(pattern=_HEX_64)
    render_started: Literal[False]

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return _safe_evidence_path(value)


class VerticalBoundedProofIdentity(APIModel):
    path: str
    sha256: str = Field(pattern=_HEX_64)
    media_qa_sha256: str = Field(pattern=_HEX_64)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return _safe_evidence_path(value)


class VerticalIndependentAuthorship(APIModel):
    composition_profile_id: str = Field(
        pattern=r"^andromeda-v2-vertical-[a-z0-9-]+$"
    )
    camera_name: str = Field(pattern=r"^TP_ANDROMEDA_V2_CAMERA_[A-Z0-9_]+$")
    crop_policy: Literal["native-authored-never-crop"]
    horizontal_and_vertical_framing_differ: Literal[True]
    proof_is_not_horizontal_crop: Literal[True]


class VerticalProductionEligibility(APIModel):
    separate_final_resolution_calibration_complete: Literal[False]
    aggregate_dual_matrix_sla_calculated: Literal[False]
    exact_operator_authorization_present: Literal[False]
    production_start_allowed: Literal[False]


class VerticalCompositionProof(APIModel):
    schema_version: Literal["2.0.0"]
    kind: Literal["trackprompt-authored-vertical-composition-proof"]
    project_id: Literal["trip-to-andromeda-v2"]
    output_variant_id: Literal["vertical-9x16-1080p"]
    enabled_for_production: Literal[False]
    render_profile_sha256: str = Field(pattern=_HEX_64)
    master_scene: VerticalMasterSceneIdentity
    bounded_proof: VerticalBoundedProofIdentity
    independent_authorship: VerticalIndependentAuthorship
    production_eligibility: VerticalProductionEligibility
    technical_pass: Literal[True]
    human_artistic_approval: Literal[False]
    production_render_started: Literal[False]


class SourceRevisionReport(APIModel):
    schema_version: Literal["3.0.0"]
    kind: Literal["trackprompt-source-revision-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    branch: str = Field(min_length=1, max_length=240)
    starting_commit_sha: str = Field(pattern=_COMMIT_SHA)
    implementation_commit_sha: str = Field(pattern=_COMMIT_SHA)
    commit_list: list[str] = Field(min_length=1)
    source_tree_sha256: str = Field(pattern=_HEX_64)
    source_tree_hash_method: Literal[
        "sha256 of raw git ls-tree -r --full-tree bytes for implementationCommitSha"
    ]
    source_tree_entry_count: int = Field(ge=1)
    release_owned_paths: list[str] = Field(min_length=1)
    release_owned_paths_clean: Literal[True]
    remote_push_status: Literal["pushed", "not-pushed", "not-configured"]
    remote_tracking_ref: str | None
    remote_commit_sha: str | None = Field(default=None, pattern=_COMMIT_SHA)
    bound_source_hashes: dict[str, str]
    production_render_started: Literal[False]

    @field_validator("release_owned_paths")
    @classmethod
    def safe_release_paths(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("releaseOwnedPaths must be unique")
        return [_safe_evidence_path(value) for value in values]

    @field_validator("bound_source_hashes")
    @classmethod
    def exact_hash_values(cls, values: dict[str, str]) -> dict[str, str]:
        if any(
            len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in values.values()
        ):
            raise ValueError("source revision bound source hashes must be SHA-256")
        return values


class WorkerLocalMatch(APIModel):
    matches: Literal[True]
    detected_gpu_model: str = Field(min_length=1, max_length=240)
    detected_vram_bytes: int = Field(ge=1)
    blender_version: Literal["5.2.0 LTS"]


class WorkerRequirementsReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-worker-requirements"]
    project_id: Literal["trip-to-andromeda-v2"]
    requirements: list[FinalReleaseWorkerRequirement] = Field(
        min_length=1,
        max_length=1,
    )
    local_match: WorkerLocalMatch


class VerificationCheck(APIModel):
    check_id: Literal[
        "backend-pytest",
        "backend-ruff",
        "backend-mypy",
        "blender-tooling-tests",
        "mission-control-generic-fixture-tests",
        "frontend-unit-tests",
        "frontend-lint",
        "frontend-typecheck",
        "frontend-build",
        "frontend-e2e",
        "dependency-import-diagnostics",
        "powershell-parser-harness-launcher",
        "compose-base-config",
        "compose-gpu-config",
        "proof-regeneration",
        "git-diff-check",
    ]
    command: str = Field(min_length=1, max_length=1000)
    status: Literal["passed", "skipped"]
    runtime: str = Field(min_length=1, max_length=240)
    evidence: str = Field(min_length=1, max_length=1000)
    skip_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def honest_status(self) -> VerificationCheck:
        if (self.status == "skipped") != (self.skip_reason is not None):
            raise ValueError("only skipped checks may declare a skip reason")
        return self


class VerificationReport(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-verification-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    implementation_commit_sha: str = Field(pattern=_COMMIT_SHA)
    source_tree_sha256: str = Field(pattern=_HEX_64)
    checks: list[VerificationCheck] = Field(min_length=1)
    failure_count: Literal[0]
    git_diff_check_passed: Literal[True]
    known_limitations: list[str]
    technical_pass: Literal[True]
    production_render_started: Literal[False]

    @field_validator("known_limitations")
    @classmethod
    def unique_nonempty_limitations(cls, values: list[str]) -> list[str]:
        if (
            any(not value.strip() for value in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError("knownLimitations must be nonempty and unique")
        return values

    @model_validator(mode="after")
    def exact_verification_matrix(self) -> VerificationReport:
        check_ids = tuple(check.check_id for check in self.checks)
        if check_ids != _VERIFICATION_CHECK_IDS:
            raise ValueError(
                "verification checks must exactly cover the ordered "
                "section 14 and AGENTS matrix"
            )
        for check in self.checks:
            if check.status != "skipped":
                continue
            if check.skip_reason is None:
                raise ValueError("skipped verification checks require a reason")
            if check.skip_reason not in self.known_limitations:
                raise ValueError(
                    f"skip reason for {check.check_id} must appear verbatim "
                    "in knownLimitations"
                )
        git_diff = self.checks[-1]
        if git_diff.check_id != "git-diff-check" or git_diff.status != "passed":
            raise ValueError("git diff --check must have passed")
        return self


class HumanReviewClosure(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-human-review-closure"]
    closure_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    project_id: Literal["trip-to-andromeda-v2"]
    reviewed_by_role: Literal["project-owner-operator", "human-artist"]
    reviewed_at: datetime
    decision: Literal["held-findings-resolved-for-new-exact-release"]
    release_hold_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    release_hold_sha256: str = Field(pattern=_HEX_64)
    held_release_identity_sha256: str = Field(pattern=_HEX_64)
    human_visual_qa_approval_sha256: str = Field(pattern=_HEX_64)
    corrected_scene_sha256: str = Field(pattern=_HEX_64)
    corrected_render_profile_sha256: str = Field(pattern=_HEX_64)
    calibration_evidence_sha256: str = Field(pattern=_HEX_64)
    final_quality_transition_report_sha256: str = Field(pattern=_HEX_64)
    vertical_composition_proof_sha256: str = Field(pattern=_HEX_64)
    remaining_blocking_findings: list[str] = Field(max_length=0)
    human_review_performed: Literal[True]
    codex_human_approval: Literal[False]
    operator_start_authorized: Literal[False]
    production_render_started: Literal[False]

    @field_validator("reviewed_at")
    @classmethod
    def timezone_bound_review(cls, value: datetime) -> datetime:
        return _timezone_bound(value, label="human review closure reviewedAt")


class FinalizationCalibrationInput(APIModel):
    """Legacy type retained only so stale internal code fails explicitly.

    Release requests no longer expose this caller-controlled scalar contract.
    """

    sample_frames: list[int]
    p50_seconds_per_frame: float
    p90_seconds_per_frame: float
    weighted_seconds_per_frame: float
    projected_render_seconds_p50: float
    projected_render_seconds_p90: float
    projected_output_bytes: int
    stage_forecasts: list[FinalStageForecast]
    deterministic_effects_verified: bool
    all_required_bakes_complete: bool
    dependency_health_passed: bool
    vram_stable: bool
    disk_free_bytes: int
    projected_peak_disk_bytes: int
    disk_safety_multiplier: float


class ReleaseFinalizationRequest(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-andromeda-v2-release-finalization-request"]
    release_tag: str = Field(pattern=_RELEASE_TAG)
    recorded_at: datetime
    branch: str = Field(min_length=1, max_length=240)
    starting_commit_sha: str = Field(pattern=_COMMIT_SHA)
    implementation_commit_sha: str = Field(pattern=_COMMIT_SHA)
    commit_list: list[str] = Field(min_length=1)
    source_tree_sha256: str = Field(pattern=_HEX_64)
    source_tree_entry_count: int = Field(ge=1)
    supersedes_release_identity_sha256: str = Field(pattern=_HEX_64)
    horizontal_output_pattern: str
    source_bindings: list[PrivateSourceInput] = Field(min_length=2, max_length=2)
    artifacts: list[ExplicitArtifact] = Field(min_length=1)

    @property
    def calibration(self) -> FinalizationCalibrationInput:
        raise ReleaseFinalizationError(
            "caller-controlled calibration scalars are no longer supported"
        )

    @field_validator("recorded_at")
    @classmethod
    def timezone_bound_record(cls, value: datetime) -> datetime:
        _timezone_bound(value, label="release recordedAt")
        if value.astimezone(UTC) > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("release recordedAt may not be in the future")
        return value

    @field_validator("commit_list")
    @classmethod
    def valid_commit_list(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("release commit list must be unique")
        for value in values:
            if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError("release commit list values must be lowercase Git SHA-1 IDs")
        return values

    @field_validator("horizontal_output_pattern")
    @classmethod
    def safe_output_pattern(cls, value: str) -> str:
        return validate_final_frame_output_pattern(value)

    @model_validator(mode="after")
    def complete_inputs(self) -> ReleaseFinalizationRequest:
        if self.implementation_commit_sha not in self.commit_list:
            raise ValueError("implementation commit must appear in commitList")
        roles = [artifact.role for artifact in self.artifacts]
        paths = [artifact.path for artifact in self.artifacts]
        if len(set(roles)) != len(roles) or len(set(paths)) != len(paths):
            raise ValueError("release input artifact roles and paths must be unique")
        missing = _REQUIRED_INPUT_ARTIFACT_ROLES.difference(roles)
        if missing:
            raise ValueError(f"release request is missing artifact roles: {sorted(missing)}")
        unexpected = set(roles).difference(_REQUIRED_INPUT_ARTIFACT_ROLES)
        if unexpected:
            raise ValueError(
                f"release request contains unexpected artifact roles: {sorted(unexpected)}"
            )
        generated = _GENERATED_ARTIFACT_ROLES.intersection(roles)
        if generated:
            raise ValueError(
                f"release request may not supply generated artifact roles: {sorted(generated)}"
            )
        sources = {binding.role: binding for binding in self.source_bindings}
        if set(sources) != {"source-audio", "source-cue"}:
            raise ValueError("release request must bind source audio and source cue")
        if sources["source-audio"].sha256 != SOURCE_AUDIO_SHA256:
            raise ValueError("release request source-audio hash is invalid")
        if sources["source-cue"].sha256 != SOURCE_CUE_SHA256:
            raise ValueError("release request source-cue hash is invalid")
        return self


class FinalizationPaths(APIModel):
    calibration: str
    package_manifest: str
    technical_authorization: str
    release_report: str


class FinalizationHashes(APIModel):
    calibration: str = Field(pattern=_HEX_64)
    package_manifest: str = Field(pattern=_HEX_64)
    technical_authorization: str = Field(pattern=_HEX_64)
    release_report: str = Field(pattern=_HEX_64)


class ReleaseFinalizationResult(APIModel):
    ok: Literal[True]
    release_identity_sha256: str = Field(pattern=_HEX_64)
    matrix_id: str
    enabled_variant_ids: list[Literal["horizontal-16x9-1080p"]]
    files: FinalizationPaths
    sha256: FinalizationHashes
    technical_ready: Literal[True]
    operator_start_gate: Literal["not-authorized"]
    production_start_allowed: Literal[False]
    final_render_started: Literal[False]
    external_processes_started: Literal[False]


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReleaseFinalizationError(f"could not read JSON artifact {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseFinalizationError(f"artifact {path.name} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ReleaseFinalizationError(f"JSON artifact {path.name} must be an object")
    return payload


def _repository_root(repository_root: Path) -> Path:
    try:
        root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise ReleaseFinalizationError("repository root does not exist") from exc
    if not root.is_dir():
        raise ReleaseFinalizationError("repository root must be a directory")
    return root


def _run_git(
    root: Path,
    arguments: list[str],
    *,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            shell=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseFinalizationError("Git revision verification could not run") from exc
    if completed.returncode != 0 and not allow_failure:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseFinalizationError(
            f"Git revision verification failed: {message[:400]}"
        )
    return completed


@dataclass(frozen=True)
class _VerifiedGitRevision:
    branch: str
    implementation_commit_sha: str
    commit_list: tuple[str, ...]
    source_tree_sha256: str
    source_tree_entry_count: int
    remote_push_status: Literal["pushed", "not-pushed", "not-configured"]
    remote_tracking_ref: str | None
    remote_commit_sha: str | None


def _verify_git_revision(
    root: Path,
    request: ReleaseFinalizationRequest,
) -> _VerifiedGitRevision:
    inside = _run_git(
        root,
        ["rev-parse", "--is-inside-work-tree"],
    ).stdout.strip()
    if inside != b"true":
        raise ReleaseFinalizationError("repository root is not a Git working tree")
    branch = (
        _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
        .stdout.decode("utf-8", errors="strict")
        .strip()
    )
    implementation_commit_sha = (
        _run_git(root, ["rev-parse", "HEAD^{commit}"])
        .stdout.decode("ascii", errors="strict")
        .strip()
    )
    if branch != request.branch:
        raise ReleaseFinalizationError(
            f"release branch mismatch: request={request.branch}, Git={branch}"
        )
    if implementation_commit_sha != request.implementation_commit_sha:
        raise ReleaseFinalizationError(
            "implementationCommitSha must equal the checked-out Git HEAD"
        )
    ancestor = _run_git(
        root,
        [
            "merge-base",
            "--is-ancestor",
            request.starting_commit_sha,
            request.implementation_commit_sha,
        ],
        allow_failure=True,
    )
    if ancestor.returncode != 0:
        raise ReleaseFinalizationError(
            "startingCommitSha must exist and be an ancestor of implementationCommitSha"
        )
    descendants = (
        _run_git(
            root,
            [
                "rev-list",
                "--reverse",
                f"{request.starting_commit_sha}..{request.implementation_commit_sha}",
            ],
        )
        .stdout.decode("ascii", errors="strict")
        .splitlines()
    )
    commit_list = (request.starting_commit_sha, *descendants)
    if tuple(request.commit_list) != commit_list:
        raise ReleaseFinalizationError(
            "commitList must exactly equal the Git ancestry from starting commit to HEAD"
        )
    tree_bytes = _run_git(
        root,
        [
            "ls-tree",
            "-r",
            "--full-tree",
            request.implementation_commit_sha,
        ],
    ).stdout
    source_tree_sha256 = hashlib.sha256(tree_bytes).hexdigest()
    source_tree_entry_count = sum(
        1 for line in tree_bytes.splitlines() if line
    )
    if (
        request.source_tree_sha256 != source_tree_sha256
        or request.source_tree_entry_count != source_tree_entry_count
    ):
        raise ReleaseFinalizationError(
            "source tree hash/count must be recomputed directly from Git HEAD"
        )
    dirty = _run_git(
        root,
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            *_RELEASE_OWNED_GIT_PATHS,
        ],
    ).stdout
    if dirty:
        raise ReleaseFinalizationError(
            "release-owned source paths contain uncommitted or untracked changes"
        )

    upstream = _run_git(
        root,
        [
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ],
        allow_failure=True,
    )
    remote_tracking_ref: str | None = None
    remote_commit_sha: str | None = None
    remote_push_status: Literal["pushed", "not-pushed", "not-configured"]
    if upstream.returncode != 0:
        remote_push_status = "not-configured"
    else:
        remote_tracking_ref = upstream.stdout.decode("utf-8", errors="strict").strip()
        remote_commit_sha = (
            _run_git(root, ["rev-parse", f"{remote_tracking_ref}^{{commit}}"])
            .stdout.decode("ascii", errors="strict")
            .strip()
        )
        remote_push_status = (
            "pushed"
            if remote_commit_sha == implementation_commit_sha
            else "not-pushed"
        )
    return _VerifiedGitRevision(
        branch=branch,
        implementation_commit_sha=implementation_commit_sha,
        commit_list=commit_list,
        source_tree_sha256=source_tree_sha256,
        source_tree_entry_count=source_tree_entry_count,
        remote_push_status=remote_push_status,
        remote_tracking_ref=remote_tracking_ref,
        remote_commit_sha=remote_commit_sha,
    )


def _load_release_hold(root: Path) -> tuple[AndromedaV2ReleaseHold, str]:
    hold_path = root.joinpath(*_RELEASE_HOLD_PATH.parts)
    try:
        payload = hold_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseFinalizationError("tracked Andromeda release hold is unavailable") from exc
    try:
        hold = AndromedaV2ReleaseHold.model_validate_json(payload)
    except ValueError as exc:
        raise ReleaseFinalizationError(
            f"tracked Andromeda release hold is invalid: {exc}"
        ) from exc
    return hold, file_sha256(hold_path)


def _resolve_repository_file(root: Path, artifact: ExplicitArtifact) -> Path:
    candidate = root.joinpath(*PurePosixPath(artifact.path).parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ReleaseFinalizationError(
            f"artifact {artifact.role} must resolve to a file inside the repository"
        ) from exc
    if not resolved.is_file():
        raise ReleaseFinalizationError(f"artifact {artifact.role} is not a file")
    actual_sha256 = file_sha256(resolved)
    if actual_sha256 != artifact.sha256:
        raise ReleaseFinalizationError(
            f"artifact hash mismatch for role {artifact.role}: "
            f"expected {artifact.sha256}, found {actual_sha256}"
        )
    return resolved


def _resolve_evidence_file(
    root: Path,
    evidence: EvidenceFile,
    *,
    label: str,
) -> Path:
    artifact = ExplicitArtifact(
        role="evidence-file",
        path=evidence.path,
        sha256=evidence.sha256,
    )
    path = _resolve_repository_file(root, artifact)
    if path.stat().st_size != evidence.size_bytes:
        raise ReleaseFinalizationError(f"{label} size does not match its evidence receipt")
    return path


def _png_dimensions(path: Path) -> tuple[int, int]:
    try:
        with path.open("rb") as stream:
            header = stream.read(24)
    except OSError as exc:
        raise ReleaseFinalizationError("PNG evidence could not be read") from exc
    if (
        len(header) < 24
        or header[:8] != b"\x89PNG\r\n\x1a\n"
        or header[12:16] != b"IHDR"
    ):
        raise ReleaseFinalizationError("image evidence is not a PNG")
    return struct.unpack(">II", header[16:24])


def _validate_image_evidence(
    root: Path,
    image: EvidenceImage,
    *,
    label: str,
) -> Path:
    path = _resolve_evidence_file(root, image, label=label)
    if _png_dimensions(path) != (image.width, image.height):
        raise ReleaseFinalizationError(f"{label} PNG dimensions do not match its receipt")
    return path


def _validate_media_evidence(
    root: Path,
    media: EvidenceMedia,
    *,
    label: str,
) -> Path:
    path = _resolve_evidence_file(root, media, label=label)
    try:
        with path.open("rb") as stream:
            header = stream.read(16)
    except OSError as exc:
        raise ReleaseFinalizationError(f"{label} could not be read") from exc
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise ReleaseFinalizationError(f"{label} is not an MP4 container")
    return path


def _new_staging_directory(output_root: Path, *, overwrite: bool) -> Path:
    if overwrite:
        raise ReleaseFinalizationError(
            "release finalization never overwrites an existing bundle; use a new directory"
        )
    if output_root.exists():
        raise ReleaseFinalizationError(
            f"release output directory already exists: {output_root}"
        )
    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = output_root.with_name(
        f".{output_root.name}.{uuid.uuid4().hex}.staging"
    )
    staging_root.mkdir()
    return staging_root


def _publish_staged_directory(staging_root: Path, output_root: Path) -> None:
    try:
        os.replace(staging_root, output_root)
    except OSError as exc:
        raise ReleaseFinalizationError(
            "could not atomically publish the complete release directory"
        ) from exc


def _resolve_output_directory(root: Path, output_directory: Path) -> Path:
    candidate = output_directory if output_directory.is_absolute() else root / output_directory
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ReleaseFinalizationError(
            "output directory must resolve inside the repository"
        ) from exc
    if resolved == root:
        raise ReleaseFinalizationError("output directory must not be the repository root")
    return resolved


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _serialized_json(payload: object) -> bytes:
    value: object
    if isinstance(payload, BaseModel):
        value = payload.model_dump(mode="json", by_alias=True)
    else:
        value = payload
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _bytes_sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _write_bytes(path: Path, payload: bytes, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ReleaseFinalizationError(
                f"refusing to overwrite existing output: {path}"
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return

    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _preflight_outputs(paths: list[Path], *, overwrite: bool) -> None:
    if len(set(paths)) != len(paths):
        raise ReleaseFinalizationError("generated output paths must be unique")
    if overwrite:
        return
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise ReleaseFinalizationError(
            f"refusing to overwrite existing output files: {existing}"
        )


def _load_request(path: Path, model: type[APIModel]) -> APIModel:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReleaseFinalizationError("request file could not be read") from exc
    except ValueError as exc:
        raise ReleaseFinalizationError(f"request file is invalid: {exc}") from exc


def load_profile_preparation_request(path: Path) -> ProfilePreparationRequest:
    request = _load_request(path, ProfilePreparationRequest)
    if not isinstance(request, ProfilePreparationRequest):
        raise AssertionError("profile request model dispatch failed")
    return request


def load_release_finalization_request(path: Path) -> ReleaseFinalizationRequest:
    request = _load_request(path, ReleaseFinalizationRequest)
    if not isinstance(request, ReleaseFinalizationRequest):
        raise AssertionError("release request model dispatch failed")
    return request


def _profile_base_payload(
    root: Path,
    variant: ProfileVariantPreparation,
    *,
    expected_variant_id: str,
) -> tuple[dict[str, Any], Path, Path, SceneBuildReceipt]:
    base_profile_path = _resolve_repository_file(root, variant.base_profile)
    scene_path = _resolve_repository_file(root, variant.scene)
    receipt_path = _resolve_repository_file(root, variant.build_receipt)
    base_payload = _read_json_object(base_profile_path)
    if (
        base_payload.get("schemaVersion") != "1.0.0"
        or base_payload.get("kind") != "trackprompt-render-profile"
        or base_payload.get("project") != "trip-to-andromeda-v2"
        or base_payload.get("preset") != "andromeda-story-v2"
    ):
        raise ReleaseFinalizationError(
            f"{variant.base_profile.role} is not an Andromeda V2 render profile"
        )
    output_variant = base_payload.get("outputVariant")
    if not isinstance(output_variant, dict) or output_variant.get("id") != expected_variant_id:
        raise ReleaseFinalizationError(
            f"{variant.base_profile.role} has the wrong output variant"
        )
    try:
        receipt = SceneBuildReceipt.model_validate(_read_json_object(receipt_path))
    except ValueError as exc:
        raise ReleaseFinalizationError(
            f"{variant.build_receipt.role} is invalid: {exc}"
        ) from exc
    if receipt.composition.output_variant_id != expected_variant_id:
        raise ReleaseFinalizationError(
            f"{variant.build_receipt.role} has the wrong output variant"
        )
    try:
        receipt_scene = Path(receipt.output_blend).resolve(strict=True)
    except OSError as exc:
        raise ReleaseFinalizationError(
            f"{variant.build_receipt.role} outputBlend is unavailable"
        ) from exc
    if receipt_scene != scene_path:
        raise ReleaseFinalizationError(
            f"{variant.build_receipt.role} does not name the exact supplied scene"
        )
    return base_payload, scene_path, receipt_path, receipt


def _versioned_profile_payload(
    base_payload: dict[str, Any],
    *,
    root: Path,
    release_tag: str,
    variant_id: str,
    scene_artifact: ExplicitArtifact,
    scene_path: Path,
    receipt_artifact: ExplicitArtifact,
    receipt_path: Path,
    receipt: SceneBuildReceipt,
    builder_sha256: str,
    base_profile_sha256: str,
) -> dict[str, Any]:
    payload = copy.deepcopy(base_payload)
    horizontal = variant_id == HORIZONTAL_VARIANT_ID
    label = "HORIZONTAL-1080P" if horizontal else "VERTICAL-1080X1920"
    profile_id = f"ANDROMEDA-V2-{label}-FINAL-{release_tag.upper()}"
    payload["profileId"] = profile_id
    payload["displayName"] = (
        f"Andromeda V2 {'Horizontal 1080p' if horizontal else 'Vertical 1080x1920'} "
        f"Final ({release_tag})"
    )
    payload["approvedSceneSha256"] = scene_artifact.sha256
    payload["sourceIdentities"] = {
        "builderSourceSha256": builder_sha256,
        "sceneBuildReceiptSha256": receipt_artifact.sha256,
        "sceneSpecSha256": receipt.scene_spec_sha256,
        "sourceCueSha256": receipt.visual_cues.sha256,
    }
    output_variant = payload.get("outputVariant")
    if not isinstance(output_variant, dict):
        output_variant = {}
    output_variant.update(
        {
            "id": variant_id,
            "enabled": horizontal,
            "required": horizontal,
            "width": receipt.composition.width,
            "height": receipt.composition.height,
            "fps": 30,
            "deliverableRole": "primary-master" if horizontal else "optional-social",
            "renderProfileId": profile_id,
            "compositionMode": "authored",
            "compositionProfileId": (
                "andromeda-v2-horizontal-master-v1"
                if horizontal
                else "andromeda-v2-vertical-master-v1"
            ),
            "cameraName": receipt.composition.camera,
            "cropPolicy": "native-authored-never-crop",
        }
    )
    payload["outputVariant"] = output_variant
    status = (
        "pending-operator-approval"
        if horizontal
        else "disabled-pending-separate-calibration-and-operator-approval"
    )
    payload["authorization"] = {
        "status": status,
        "project": "TRIP-TO-ANDROMEDA-V2",
        "preset": "ANDROMEDA-STORY-V2",
        "profile": profile_id,
        "reason": (
            "Technical readiness and a separate exact operator-start authorization are required."
            if horizontal
            else (
                "Vertical is authored but disabled; separate calibration, aggregate SLA, "
                "package identity, and operator authorization are required before enablement."
            )
        ),
        "productionStartAllowed": False,
        "finalRenderStarted": False,
        "explicitOperatorAuthorizationRequired": True,
    }
    payload["releaseFinalization"] = {
        "releaseTag": release_tag,
        "baseProfileSha256": base_profile_sha256,
        "scenePath": _relative_posix(root, scene_path),
        "sceneSha256": scene_artifact.sha256,
        "sceneBuildReceiptPath": _relative_posix(root, receipt_path),
        "sceneBuildReceiptSha256": receipt_artifact.sha256,
        "productionRenderStarted": False,
        "operatorStartAuthorizationCreated": False,
    }
    payload["productionStartAllowed"] = False
    payload["finalRenderStarted"] = False
    try:
        model = VersionedRenderProfile.model_validate(payload)
    except ValueError as exc:
        raise ReleaseFinalizationError(
            f"generated {variant_id} render profile is invalid: {exc}"
        ) from exc
    return model.model_dump(mode="json", by_alias=True)


def prepare_versioned_profiles(
    repository_root: Path,
    request: ProfilePreparationRequest,
    output_directory: Path,
    *,
    overwrite: bool = False,
) -> dict[str, object]:
    root = _repository_root(repository_root)
    output_root = _resolve_output_directory(root, output_directory)
    builder_path = _resolve_repository_file(root, request.builder_source)
    builder_sha256 = file_sha256(builder_path)
    if builder_sha256 != request.builder_source.sha256:
        raise ReleaseFinalizationError("builder source hash changed during preparation")

    horizontal_base, horizontal_scene, horizontal_receipt_path, horizontal_receipt = (
        _profile_base_payload(
            root,
            request.horizontal,
            expected_variant_id=HORIZONTAL_VARIANT_ID,
        )
    )
    vertical_base, vertical_scene, vertical_receipt_path, vertical_receipt = (
        _profile_base_payload(
            root,
            request.vertical,
            expected_variant_id=VERTICAL_VARIANT_ID,
        )
    )
    if (
        horizontal_receipt.builder_source_sha256 != builder_sha256
        or vertical_receipt.builder_source_sha256 != builder_sha256
    ):
        raise ReleaseFinalizationError(
            "scene build receipts do not bind the exact supplied builder source"
        )
    expected_geometry = {
        HORIZONTAL_VARIANT_ID: (
            horizontal_receipt.composition.width,
            horizontal_receipt.composition.height,
        ),
        VERTICAL_VARIANT_ID: (
            vertical_receipt.composition.width,
            vertical_receipt.composition.height,
        ),
    }
    if expected_geometry != {
        HORIZONTAL_VARIANT_ID: (1920, 1080),
        VERTICAL_VARIANT_ID: (1080, 1920),
    }:
        raise ReleaseFinalizationError(
            f"scene build receipts have invalid final geometry: {expected_geometry}"
        )

    horizontal_payload = _versioned_profile_payload(
        horizontal_base,
        root=root,
        release_tag=request.release_tag,
        variant_id=HORIZONTAL_VARIANT_ID,
        scene_artifact=request.horizontal.scene,
        scene_path=horizontal_scene,
        receipt_artifact=request.horizontal.build_receipt,
        receipt_path=horizontal_receipt_path,
        receipt=horizontal_receipt,
        builder_sha256=builder_sha256,
        base_profile_sha256=request.horizontal.base_profile.sha256,
    )
    vertical_payload = _versioned_profile_payload(
        vertical_base,
        root=root,
        release_tag=request.release_tag,
        variant_id=VERTICAL_VARIANT_ID,
        scene_artifact=request.vertical.scene,
        scene_path=vertical_scene,
        receipt_artifact=request.vertical.build_receipt,
        receipt_path=vertical_receipt_path,
        receipt=vertical_receipt,
        builder_sha256=builder_sha256,
        base_profile_sha256=request.vertical.base_profile.sha256,
    )
    horizontal_bytes = _serialized_json(horizontal_payload)
    vertical_bytes = _serialized_json(vertical_payload)
    horizontal_path = (
        output_root
        / f"andromeda-v2-horizontal-1080p-final-{request.release_tag}.json"
    )
    vertical_path = (
        output_root
        / f"andromeda-v2-vertical-1080x1920-final-{request.release_tag}.json"
    )
    targets = [horizontal_path, vertical_path]
    if len(set(targets)) != len(targets):
        raise ReleaseFinalizationError("generated output paths must be unique")
    staging_root = _new_staging_directory(output_root, overwrite=overwrite)
    try:
        _write_bytes(
            staging_root / horizontal_path.name,
            horizontal_bytes,
            overwrite=False,
        )
        _write_bytes(
            staging_root / vertical_path.name,
            vertical_bytes,
            overwrite=False,
        )
        _publish_staged_directory(staging_root, output_root)
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
    return {
        "ok": True,
        "releaseTag": request.release_tag,
        "profiles": [
            {
                "outputVariantId": HORIZONTAL_VARIANT_ID,
                "path": _relative_posix(root, horizontal_path),
                "sha256": _bytes_sha256(horizontal_bytes),
                "enabledInHorizontalOnlyMatrix": True,
                "productionStartAllowed": False,
            },
            {
                "outputVariantId": VERTICAL_VARIANT_ID,
                "path": _relative_posix(root, vertical_path),
                "sha256": _bytes_sha256(vertical_bytes),
                "enabledInHorizontalOnlyMatrix": False,
                "separateCalibrationRequiredBeforeEnablement": True,
                "productionStartAllowed": False,
            },
        ],
        "productionRenderStarted": False,
        "externalProcessesStarted": False,
    }


def _model_from_artifact(
    paths: Mapping[str, Path],
    role: str,
    model_type: type[_MODEL],
) -> _MODEL:
    try:
        return model_type.model_validate(_read_json_object(paths[role]))
    except ValueError as exc:
        raise ReleaseFinalizationError(f"artifact model is invalid for role {role}: {exc}") from exc


def _model_from_evidence_file(
    root: Path,
    evidence: EvidenceFile,
    label: str,
    model_type: type[_MODEL],
) -> _MODEL:
    path = _resolve_evidence_file(root, evidence, label=label)
    try:
        return model_type.model_validate(_read_json_object(path))
    except ValueError as exc:
        raise ReleaseFinalizationError(f"{label} is invalid: {exc}") from exc


def _nested_value(payload: Mapping[str, Any], *path: str) -> object:
    value: object = payload
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            raise ReleaseFinalizationError(
                f"JSON evidence is missing required binding {'.'.join(path)}"
            )
        value = value[part]
    return value


def _require_json_binding(
    paths: Mapping[str, Path],
    role: str,
    expected: object,
    *binding_path: str,
) -> dict[str, Any]:
    payload = _read_json_object(paths[role])
    actual = _nested_value(payload, *binding_path)
    if actual != expected:
        raise ReleaseFinalizationError(
            f"artifact {role} binding {'.'.join(binding_path)} does not match "
            "the fresh release"
        )
    return payload


def _validate_json_evidence(
    artifacts: Mapping[str, ExplicitArtifact],
    paths: Mapping[str, Path],
) -> None:
    for role, path in paths.items():
        if role in _NON_JSON_INPUT_ARTIFACT_ROLES:
            continue
        if path.suffix.casefold() != ".json":
            raise ReleaseFinalizationError(
                f"artifact role {role} must be a strict JSON evidence document"
            )
        payload = _read_json_object(path)
        project_id = payload.get("projectId")
        if project_id is not None and project_id != "trip-to-andromeda-v2":
            raise ReleaseFinalizationError(f"artifact {role} has the wrong project ID")
        if payload.get("productionRenderStarted") is True:
            raise ReleaseFinalizationError(
                f"artifact {role} claims the full production render started"
            )
        if payload.get("finalRenderStarted") is True:
            raise ReleaseFinalizationError(
                f"artifact {role} claims the final render started"
            )
        if role in _TECHNICAL_PASS_ROLES and payload.get("technicalPass") is not True:
            raise ReleaseFinalizationError(
                f"artifact {role} does not declare technicalPass=true"
            )
        if file_sha256(path) != artifacts[role].sha256:
            raise ReleaseFinalizationError(
                f"artifact {role} changed during JSON model validation"
            )


def _load_worker_requirement(paths: Mapping[str, Path]) -> FinalReleaseWorkerRequirement:
    payload = _read_json_object(paths["worker-requirements"])
    requirements = payload.get("requirements")
    if not isinstance(requirements, list) or len(requirements) != 1:
        raise ReleaseFinalizationError(
            "worker-requirements must contain exactly one horizontal worker requirement"
        )
    try:
        return FinalReleaseWorkerRequirement.model_validate(requirements[0])
    except ValueError as exc:
        raise ReleaseFinalizationError(
            f"worker-requirements model is invalid: {exc}"
        ) from exc


def _legacy_validate_finalization_inputs(
    request: ReleaseFinalizationRequest,
    artifacts: Mapping[str, ExplicitArtifact],
    paths: Mapping[str, Path],
) -> tuple[
    OutputVariantSet,
    StoryPlanV2,
    ShotPlanV2,
    VersionedRenderProfile,
    VersionedRenderProfile,
    HumanVisualQaApproval,
    SceneBuildReceipt,
    SceneBuildReceipt,
    FinalReleaseWorkerRequirement,
]:
    _validate_json_evidence(artifacts, paths)
    output_variants = _model_from_artifact(paths, "output-variants", OutputVariantSet)
    story_plan = _model_from_artifact(paths, "story-plan", StoryPlanV2)
    shot_plan = _model_from_artifact(paths, "shot-plan", ShotPlanV2)
    _model_from_artifact(
        paths,
        "owner-creative-acceptance",
        OwnerAttestedCreativeAcceptance,
    )
    _model_from_artifact(paths, "final-look-profile", FinalLookProfile)
    horizontal_profile = _model_from_artifact(
        paths,
        "horizontal-render-profile",
        VersionedRenderProfile,
    )
    vertical_profile = _model_from_artifact(
        paths,
        "vertical-render-profile",
        VersionedRenderProfile,
    )
    visual_qa = _model_from_artifact(
        paths,
        "human-visual-qa-approval",
        HumanVisualQaApproval,
    )
    horizontal_receipt = _model_from_artifact(
        paths,
        "horizontal-scene-build-receipt",
        SceneBuildReceipt,
    )
    vertical_receipt = _model_from_artifact(
        paths,
        "vertical-scene-build-receipt",
        SceneBuildReceipt,
    )
    worker = _load_worker_requirement(paths)
    typed = (
        output_variants,
        story_plan,
        shot_plan,
        horizontal_profile,
        vertical_profile,
        visual_qa,
        horizontal_receipt,
        vertical_receipt,
        worker,
    )
    if not (
        isinstance(output_variants, OutputVariantSet)
        and isinstance(story_plan, StoryPlanV2)
        and isinstance(shot_plan, ShotPlanV2)
        and isinstance(horizontal_profile, VersionedRenderProfile)
        and isinstance(vertical_profile, VersionedRenderProfile)
        and isinstance(visual_qa, HumanVisualQaApproval)
        and isinstance(horizontal_receipt, SceneBuildReceipt)
        and isinstance(vertical_receipt, SceneBuildReceipt)
    ):
        raise AssertionError("release finalization model dispatch failed")

    if horizontal_profile.output_variant.id != HORIZONTAL_VARIANT_ID:
        raise ReleaseFinalizationError("horizontal render profile has the wrong variant")
    if vertical_profile.output_variant.id != VERTICAL_VARIANT_ID:
        raise ReleaseFinalizationError("vertical render profile has the wrong variant")
    if vertical_profile.output_variant.enabled:
        raise ReleaseFinalizationError(
            "vertical render profile must remain disabled in a horizontal-only release"
        )
    if (
        horizontal_profile.release_finalization.release_tag
        != request.release_tag
        or vertical_profile.release_finalization.release_tag
        != request.release_tag
    ):
        raise ReleaseFinalizationError(
            "render profiles do not bind the requested release tag"
        )
    if artifacts["final-scene"].sha256 == artifacts["vertical-master-scene"].sha256:
        raise ReleaseFinalizationError(
            "horizontal and vertical scenes must be independently authored artifacts"
        )
    if horizontal_profile.approved_scene_sha256 != artifacts["final-scene"].sha256:
        raise ReleaseFinalizationError("horizontal profile does not bind the final scene")
    if vertical_profile.approved_scene_sha256 != artifacts["vertical-master-scene"].sha256:
        raise ReleaseFinalizationError("vertical profile does not bind the vertical scene")
    if (
        horizontal_profile.source_identities.scene_build_receipt_sha256
        != artifacts["horizontal-scene-build-receipt"].sha256
    ):
        raise ReleaseFinalizationError(
            "horizontal profile does not bind its exact scene build receipt"
        )
    if (
        vertical_profile.source_identities.scene_build_receipt_sha256
        != artifacts["vertical-scene-build-receipt"].sha256
    ):
        raise ReleaseFinalizationError(
            "vertical profile does not bind its exact scene build receipt"
        )
    if (
        horizontal_profile.source_identities.builder_source_sha256
        != artifacts["builder-source"].sha256
        or vertical_profile.source_identities.builder_source_sha256
        != artifacts["builder-source"].sha256
    ):
        raise ReleaseFinalizationError("render profiles do not bind the exact builder source")
    if horizontal_receipt.composition.output_variant_id != HORIZONTAL_VARIANT_ID:
        raise ReleaseFinalizationError("horizontal build receipt has the wrong variant")
    if vertical_receipt.composition.output_variant_id != VERTICAL_VARIANT_ID:
        raise ReleaseFinalizationError("vertical build receipt has the wrong variant")
    receipt_profile_bindings = (
        (horizontal_receipt, horizontal_profile),
        (vertical_receipt, vertical_profile),
    )
    for receipt, profile in receipt_profile_bindings:
        if (
            profile.source_identities.scene_spec_sha256
            != receipt.scene_spec_sha256
            or profile.source_identities.source_cue_sha256
            != receipt.visual_cues.sha256
        ):
            raise ReleaseFinalizationError(
                f"render profile {profile.output_variant.id} has stale build-receipt bindings"
            )

    variants_by_id = {variant.id: variant for variant in output_variants.variants}
    horizontal_contract = variants_by_id[HORIZONTAL_VARIANT_ID]
    vertical_contract = variants_by_id[VERTICAL_VARIANT_ID]
    profile_contracts = (
        (horizontal_profile, horizontal_contract),
        (vertical_profile, vertical_contract),
    )
    for profile, contract in profile_contracts:
        if (
            profile.output_variant.composition_profile_id
            != contract.composition_profile_id
            or profile.output_variant.camera_name != contract.camera_name
            or profile.output_variant.crop_policy != contract.crop_policy
        ):
            raise ReleaseFinalizationError(
                f"render profile {profile.output_variant.id} disagrees with output-variants"
            )

    visual_bindings = {
        "scene": (
            visual_qa.scene_sha256,
            artifacts["final-scene"].sha256,
        ),
        "horizontal profile": (
            visual_qa.horizontal_render_profile_sha256,
            artifacts["horizontal-render-profile"].sha256,
        ),
        "full animatic": (
            visual_qa.full_audio_animatic_sha256,
            artifacts["full-audio-animatic"].sha256,
        ),
        "animatic media QA": (
            visual_qa.animatic_media_qa_report_sha256,
            artifacts["animatic-media-qa-report"].sha256,
        ),
        "final scene receipt": (
            visual_qa.final_scene_receipt_sha256,
            artifacts["final-scene-receipt"].sha256,
        ),
        "motion-health report": (
            visual_qa.motion_health_report_sha256,
            artifacts["motion-health-report"].sha256,
        ),
        "exposure/mobile-readability report": (
            visual_qa.exposure_mobile_readability_report_sha256,
            artifacts["exposure-mobile-readability-report"].sha256,
        ),
        "final-quality transition report": (
            visual_qa.final_quality_transition_report_sha256,
            artifacts["final-quality-transition-report"].sha256,
        ),
        "vertical composition proof": (
            visual_qa.vertical_composition_proof_sha256,
            artifacts["vertical-composition-proof"].sha256,
        ),
        "vertical render profile": (
            visual_qa.vertical_render_profile_sha256,
            artifacts["vertical-render-profile"].sha256,
        ),
        "vertical bounded-proof media": (
            visual_qa.vertical_bounded_proof_media_sha256,
            artifacts["vertical-bounded-proof-media"].sha256,
        ),
    }
    for label, (actual, expected) in visual_bindings.items():
        if actual != expected:
            raise ReleaseFinalizationError(
                f"human visual-QA approval has a stale {label} binding"
            )
    if visual_qa.reviewed_at > request.recorded_at:
        raise ReleaseFinalizationError(
            "human visual-QA approval cannot be recorded after release finalization"
        )

    deterministic_report = _require_json_binding(
        paths,
        "deterministic-effects-and-disk-report",
        artifacts["final-scene"].sha256,
        "sceneSha256",
    )
    _require_json_binding(
        paths,
        "deterministic-effects-and-disk-report",
        artifacts["horizontal-render-profile"].sha256,
        "renderProfileSha256",
    )
    deterministic_bindings = {
        ("determinism", "deterministicEffectsVerified"): True,
        ("determinism", "allRequiredBakesComplete"): True,
        ("storage", "freeBytes"): request.calibration.disk_free_bytes,
        ("storage", "projectedPeakDiskBytes"): (
            request.calibration.projected_peak_disk_bytes
        ),
        ("storage", "safetyMultiplier"): (
            request.calibration.disk_safety_multiplier
        ),
        ("storage", "headroomSatisfied"): True,
        ("vram", "stableDuringBoundedEvidence"): True,
    }
    for binding_path, binding_expected in deterministic_bindings.items():
        if _nested_value(deterministic_report, *binding_path) != binding_expected:
            raise ReleaseFinalizationError(
                "deterministic-effects-and-disk-report does not match the "
                f"calibration input at {'.'.join(binding_path)}"
            )
    _require_json_binding(
        paths,
        "dependency-health-report",
        artifacts["final-scene"].sha256,
        "sceneSha256",
    )
    _require_json_binding(
        paths,
        "final-quality-transition-report",
        artifacts["final-scene"].sha256,
        "sceneSha256",
    )
    _require_json_binding(
        paths,
        "final-quality-transition-report",
        artifacts["horizontal-render-profile"].sha256,
        "renderProfileSha256",
    )
    transition_report = _read_json_object(
        paths["final-quality-transition-report"]
    )
    samples = transition_report.get("samples")
    if not isinstance(samples, list):
        raise ReleaseFinalizationError(
            "final-quality-transition-report samples must be a list"
        )
    sample_hashes = {
        sample.get("id"): sample.get("mediaSha256")
        for sample in samples
        if isinstance(sample, Mapping)
    }
    transition_bindings = {
        "gates-to-rupture": artifacts["gates-to-rupture-media"].sha256,
        "rupture-to-transformation": artifacts[
            "rupture-to-transformation-media"
        ].sha256,
        "transformation-to-arrival": artifacts[
            "transformation-to-arrival-media"
        ].sha256,
    }
    if sample_hashes != transition_bindings:
        raise ReleaseFinalizationError(
            "final-quality-transition-report does not bind the three exact transition media"
        )
    animatic_qa = _require_json_binding(
        paths,
        "animatic-media-qa-report",
        artifacts["full-audio-animatic"].sha256,
        "encodedMedia",
        "sha256",
    )
    if animatic_qa.get("technicalPass") is not True:
        raise ReleaseFinalizationError("animatic media QA is not a technical pass")
    _require_json_binding(
        paths,
        "animatic-receipt",
        artifacts["full-audio-animatic"].sha256,
        "artifact",
        "sha256",
    )
    _require_json_binding(
        paths,
        "final-scene-receipt",
        artifacts["final-scene"].sha256,
        "localArtifact",
        "sha256",
    )
    _require_json_binding(
        paths,
        "final-scene-receipt",
        artifacts["horizontal-scene-build-receipt"].sha256,
        "buildReceipt",
        "sha256",
    )
    _require_json_binding(
        paths,
        "motion-health-report",
        artifacts["final-scene"].sha256,
        "sceneSha256",
    )
    _require_json_binding(
        paths,
        "animatic-receipt",
        SOURCE_AUDIO_SHA256,
        "audio",
        "sourceSha256",
    )
    _require_json_binding(
        paths,
        "vertical-composition-proof",
        False,
        "enabledForProduction",
    )
    _require_json_binding(
        paths,
        "vertical-composition-proof",
        artifacts["vertical-master-scene"].sha256,
        "masterScene",
        "sha256",
    )
    _require_json_binding(
        paths,
        "vertical-composition-proof",
        artifacts["vertical-render-profile"].sha256,
        "renderProfileSha256",
    )
    _require_json_binding(
        paths,
        "vertical-composition-proof",
        artifacts["vertical-bounded-proof-media"].sha256,
        "boundedProof",
        "sha256",
    )
    _require_json_binding(
        paths,
        "vertical-composition-proof",
        artifacts["vertical-bounded-proof-media-qa"].sha256,
        "boundedProof",
        "mediaQaSha256",
    )
    vertical_proof_payload = _read_json_object(paths["vertical-composition-proof"])
    vertical_proof_bindings = {
        ("independentAuthorship", "compositionProfileId"): (
            vertical_profile.output_variant.composition_profile_id
        ),
        ("independentAuthorship", "cameraName"): (
            vertical_profile.output_variant.camera_name
        ),
        ("independentAuthorship", "proofIsNotHorizontalCrop"): True,
        ("independentAuthorship", "horizontalAndVerticalFramingDiffer"): True,
    }
    for proof_path, proof_expected in vertical_proof_bindings.items():
        if _nested_value(vertical_proof_payload, *proof_path) != proof_expected:
            raise ReleaseFinalizationError(
                "vertical-composition-proof does not match the disabled authored "
                f"vertical profile at {'.'.join(proof_path)}"
            )
    _require_json_binding(
        paths,
        "vertical-composition-proof",
        False,
        "productionEligibility",
        "productionStartAllowed",
    )
    source_revision = _read_json_object(paths["source-revision-report"])
    source_bindings: dict[tuple[str, ...], object] = {
        ("branch",): request.branch,
        ("startingCommitSha",): request.starting_commit_sha,
        ("implementationCommitSha",): request.implementation_commit_sha,
        ("sourceTreeSha256",): request.source_tree_sha256,
        ("sourceTreeEntryCount",): request.source_tree_entry_count,
        ("boundSourceHashes", "builder"): artifacts["builder-source"].sha256,
        ("boundSourceHashes", "ownerCreativeAcceptance"): artifacts[
            "owner-creative-acceptance"
        ].sha256,
        ("boundSourceHashes", "encodingProfiles"): artifacts[
            "encoding-profiles"
        ].sha256,
        ("boundSourceHashes", "lookProfile"): artifacts[
            "final-look-profile"
        ].sha256,
        ("boundSourceHashes", "storyPlan"): artifacts["story-plan"].sha256,
        ("boundSourceHashes", "shotPlan"): artifacts["shot-plan"].sha256,
        ("boundSourceHashes", "outputVariantContract"): artifacts[
            "output-variants"
        ].sha256,
        ("boundSourceHashes", "horizontalRenderProfile"): artifacts[
            "horizontal-render-profile"
        ].sha256,
        ("boundSourceHashes", "verticalRenderProfile"): artifacts[
            "vertical-render-profile"
        ].sha256,
    }
    for source_binding_path, source_binding_expected in source_bindings.items():
        if (
            _nested_value(source_revision, *source_binding_path)
            != source_binding_expected
        ):
            raise ReleaseFinalizationError(
                "source-revision-report binding "
                f"{'.'.join(source_binding_path)} is stale"
            )
    live_dashboard = _read_json_object(paths["live-dashboard-proof"])
    if _nested_value(live_dashboard, "runtime", "fullProductionRenderStarted") is not False:
        raise ReleaseFinalizationError(
            "live-dashboard proof must not claim a full production render"
        )
    if (
        _nested_value(live_dashboard, "sourceRevision", "gitCommitSha")
        != request.implementation_commit_sha
        or _nested_value(
            live_dashboard,
            "sourceRevision",
            "sourceTreeSha256",
        )
        != request.source_tree_sha256
    ):
        raise ReleaseFinalizationError(
            "live-dashboard proof does not bind the implementation revision"
        )
    worker_payload = _read_json_object(paths["worker-requirements"])
    if _nested_value(worker_payload, "localMatch", "matches") is not True:
        raise ReleaseFinalizationError(
            "worker-requirements does not record a passing local worker match"
        )
    return typed


@dataclass(frozen=True)
class _DerivedCalibration:
    sample_frames: tuple[int, ...]
    p50_seconds_per_frame: float
    p90_seconds_per_frame: float
    weighted_seconds_per_frame: float
    projected_render_seconds_p50: float
    projected_render_seconds_p90: float
    projected_render_seconds_weighted: float
    canonical_stage_forecasts: tuple[FinalStageForecast, ...]
    detailed_stage_forecasts: tuple[CalibrationStageForecast, ...]
    aggregate_p50_seconds: float
    aggregate_p90_seconds: float


def _derive_calibration(
    evidence: FinalResolutionCalibrationEvidence,
) -> _DerivedCalibration:
    durations = [sample.render_seconds for sample in evidence.samples]
    p50 = _nearest_rank_percentile(durations, 0.50)
    p90 = _nearest_rank_percentile(durations, 0.90)
    weighted = math.fsum(
        sample.render_seconds * sample.forecast_weight for sample in evidence.samples
    )
    detailed = {forecast.stage: forecast for forecast in evidence.stage_forecasts}
    render_components = (
        detailed["scene-package-preparation"],
        detailed["cache-bake"],
        detailed["image-sequence-render"],
        detailed["contingency"],
    )
    canonical = (
        FinalStageForecast(
            stage="rendering",
            p50_seconds=math.fsum(item.p50_seconds for item in render_components),
            p90_seconds=math.fsum(item.p90_seconds for item in render_components),
        ),
        FinalStageForecast(
            stage="frame-validation",
            p50_seconds=detailed["frame-validation"].p50_seconds,
            p90_seconds=detailed["frame-validation"].p90_seconds,
        ),
        FinalStageForecast(
            stage="encoding",
            p50_seconds=detailed["encoding"].p50_seconds,
            p90_seconds=detailed["encoding"].p90_seconds,
        ),
        FinalStageForecast(
            stage="media-qa",
            p50_seconds=detailed["final-qa"].p50_seconds,
            p90_seconds=detailed["final-qa"].p90_seconds,
        ),
        FinalStageForecast(
            stage="publication",
            p50_seconds=detailed["publication"].p50_seconds,
            p90_seconds=detailed["publication"].p90_seconds,
        ),
    )
    return _DerivedCalibration(
        sample_frames=tuple(sample.frame for sample in evidence.samples),
        p50_seconds_per_frame=p50,
        p90_seconds_per_frame=p90,
        weighted_seconds_per_frame=weighted,
        projected_render_seconds_p50=p50 * ANDROMEDA_V2_FRAME_END,
        projected_render_seconds_p90=p90 * ANDROMEDA_V2_FRAME_END,
        projected_render_seconds_weighted=weighted * ANDROMEDA_V2_FRAME_END,
        canonical_stage_forecasts=canonical,
        detailed_stage_forecasts=tuple(evidence.stage_forecasts),
        aggregate_p50_seconds=math.fsum(
            forecast.p50_seconds for forecast in evidence.stage_forecasts
        ),
        aggregate_p90_seconds=math.fsum(
            forecast.p90_seconds for forecast in evidence.stage_forecasts
        ),
    )


@dataclass(frozen=True)
class _ValidatedFinalizationInputs:
    output_variants: OutputVariantSet
    story_plan: StoryPlanV2
    shot_plan: ShotPlanV2
    horizontal_profile: VersionedRenderProfile
    vertical_profile: VersionedRenderProfile
    visual_qa: HumanVisualQaApproval
    review_closure: HumanReviewClosure
    horizontal_receipt: SceneBuildReceipt
    vertical_receipt: SceneBuildReceipt
    worker: FinalReleaseWorkerRequirement
    calibration_evidence: FinalResolutionCalibrationEvidence
    derived_calibration: _DerivedCalibration
    hardware: HardwareAndStorageReport
    deterministic: DeterministicEffectsAndDiskReport
    dependency: DependencyHealthReport
    dashboard: LiveDashboardProof
    animatic_qa: AnimaticMediaQaReport
    animatic_receipt: AnimaticReceipt
    final_scene_receipt: FinalSceneReceipt
    motion: MotionHealthReport
    exposure: ExposureMobileReadabilityReport
    transitions: FinalQualityTransitionReport
    vertical_media_qa: VerticalBoundedProofMediaQa
    vertical_proof: VerticalCompositionProof
    source_revision: SourceRevisionReport
    verification: VerificationReport
    git_revision: _VerifiedGitRevision
    release_hold: AndromedaV2ReleaseHold
    release_hold_sha256: str


def _validate_finalization_inputs(
    root: Path,
    request: ReleaseFinalizationRequest,
    artifacts: Mapping[str, ExplicitArtifact],
    paths: Mapping[str, Path],
) -> _ValidatedFinalizationInputs:
    _validate_json_evidence(artifacts, paths)
    git_revision = _verify_git_revision(root, request)
    release_hold, release_hold_sha256 = _load_release_hold(root)
    if (
        request.supersedes_release_identity_sha256
        != release_hold.release_identity_sha256
    ):
        raise ReleaseFinalizationError(
            "supersedesReleaseIdentitySha256 must match the exact tracked release hold"
        )

    output_variants = _model_from_artifact(paths, "output-variants", OutputVariantSet)
    story_plan = _model_from_artifact(paths, "story-plan", StoryPlanV2)
    shot_plan = _model_from_artifact(paths, "shot-plan", ShotPlanV2)
    _model_from_artifact(
        paths,
        "owner-creative-acceptance",
        OwnerAttestedCreativeAcceptance,
    )
    _model_from_artifact(paths, "final-look-profile", FinalLookProfile)
    horizontal_profile = _model_from_artifact(
        paths,
        "horizontal-render-profile",
        VersionedRenderProfile,
    )
    vertical_profile = _model_from_artifact(
        paths,
        "vertical-render-profile",
        VersionedRenderProfile,
    )
    visual_qa = _model_from_artifact(
        paths,
        "human-visual-qa-approval",
        HumanVisualQaApproval,
    )
    review_closure = _model_from_artifact(
        paths,
        "human-review-closure",
        HumanReviewClosure,
    )
    horizontal_receipt = _model_from_artifact(
        paths,
        "horizontal-scene-build-receipt",
        SceneBuildReceipt,
    )
    vertical_receipt = _model_from_artifact(
        paths,
        "vertical-scene-build-receipt",
        SceneBuildReceipt,
    )
    worker_report = _model_from_artifact(
        paths,
        "worker-requirements",
        WorkerRequirementsReport,
    )
    worker = worker_report.requirements[0]
    calibration_evidence = _model_from_artifact(
        paths,
        "final-resolution-calibration-evidence",
        FinalResolutionCalibrationEvidence,
    )
    hardware = _model_from_artifact(
        paths,
        "hardware-and-storage-report",
        HardwareAndStorageReport,
    )
    deterministic = _model_from_artifact(
        paths,
        "deterministic-effects-and-disk-report",
        DeterministicEffectsAndDiskReport,
    )
    dependency = _model_from_artifact(
        paths,
        "dependency-health-report",
        DependencyHealthReport,
    )
    dashboard = _model_from_artifact(
        paths,
        "live-dashboard-proof",
        LiveDashboardProof,
    )
    animatic_qa = _model_from_artifact(
        paths,
        "animatic-media-qa-report",
        AnimaticMediaQaReport,
    )
    animatic_receipt = _model_from_artifact(
        paths,
        "animatic-receipt",
        AnimaticReceipt,
    )
    final_scene_receipt = _model_from_artifact(
        paths,
        "final-scene-receipt",
        FinalSceneReceipt,
    )
    motion = _model_from_artifact(
        paths,
        "motion-health-report",
        MotionHealthReport,
    )
    exposure = _model_from_artifact(
        paths,
        "exposure-mobile-readability-report",
        ExposureMobileReadabilityReport,
    )
    transitions = _model_from_artifact(
        paths,
        "final-quality-transition-report",
        FinalQualityTransitionReport,
    )
    vertical_media_qa = _model_from_artifact(
        paths,
        "vertical-bounded-proof-media-qa",
        VerticalBoundedProofMediaQa,
    )
    vertical_proof = _model_from_artifact(
        paths,
        "vertical-composition-proof",
        VerticalCompositionProof,
    )
    source_revision = _model_from_artifact(
        paths,
        "source-revision-report",
        SourceRevisionReport,
    )
    verification = _model_from_artifact(
        paths,
        "verification-report",
        VerificationReport,
    )

    if horizontal_profile.output_variant.id != HORIZONTAL_VARIANT_ID:
        raise ReleaseFinalizationError("horizontal render profile has the wrong variant")
    if vertical_profile.output_variant.id != VERTICAL_VARIANT_ID:
        raise ReleaseFinalizationError("vertical render profile has the wrong variant")
    if vertical_profile.output_variant.enabled:
        raise ReleaseFinalizationError(
            "vertical render profile must remain disabled in a horizontal-only release"
        )
    if (
        horizontal_profile.release_finalization.release_tag != request.release_tag
        or vertical_profile.release_finalization.release_tag != request.release_tag
    ):
        raise ReleaseFinalizationError(
            "render profiles do not bind the requested release tag"
        )
    if artifacts["final-scene"].sha256 == artifacts["vertical-master-scene"].sha256:
        raise ReleaseFinalizationError(
            "horizontal and vertical scenes must be independently authored artifacts"
        )
    if horizontal_profile.approved_scene_sha256 != artifacts["final-scene"].sha256:
        raise ReleaseFinalizationError("horizontal profile does not bind the final scene")
    if (
        vertical_profile.approved_scene_sha256
        != artifacts["vertical-master-scene"].sha256
    ):
        raise ReleaseFinalizationError("vertical profile does not bind the vertical scene")
    builder_sha256 = artifacts["builder-source"].sha256
    receipt_bindings = (
        (
            horizontal_receipt,
            horizontal_profile,
            artifacts["horizontal-scene-build-receipt"].sha256,
            HORIZONTAL_VARIANT_ID,
        ),
        (
            vertical_receipt,
            vertical_profile,
            artifacts["vertical-scene-build-receipt"].sha256,
            VERTICAL_VARIANT_ID,
        ),
    )
    for receipt, profile, receipt_sha256, variant_id in receipt_bindings:
        if receipt.composition.output_variant_id != variant_id:
            raise ReleaseFinalizationError(
                f"{variant_id} build receipt has the wrong output variant"
            )
        if receipt.builder_source_sha256 != builder_sha256:
            raise ReleaseFinalizationError(
                f"{variant_id} build receipt has a stale builder-source binding"
            )
        if (
            profile.source_identities.builder_source_sha256 != builder_sha256
            or profile.source_identities.scene_build_receipt_sha256
            != receipt_sha256
            or profile.source_identities.scene_spec_sha256
            != receipt.scene_spec_sha256
            or profile.source_identities.source_cue_sha256
            != receipt.visual_cues.sha256
        ):
            raise ReleaseFinalizationError(
                f"{variant_id} render profile has stale scene/build/source bindings"
            )
    variants_by_id = {variant.id: variant for variant in output_variants.variants}
    for profile, variant_id in (
        (horizontal_profile, HORIZONTAL_VARIANT_ID),
        (vertical_profile, VERTICAL_VARIANT_ID),
    ):
        contract = variants_by_id[variant_id]
        if (
            profile.output_variant.composition_profile_id
            != contract.composition_profile_id
            or profile.output_variant.camera_name != contract.camera_name
            or profile.output_variant.crop_policy != contract.crop_policy
        ):
            raise ReleaseFinalizationError(
                f"render profile {variant_id} disagrees with output-variants"
            )

    visual_bindings = {
        "scene": (visual_qa.scene_sha256, artifacts["final-scene"].sha256),
        "horizontal profile": (
            visual_qa.horizontal_render_profile_sha256,
            artifacts["horizontal-render-profile"].sha256,
        ),
        "full animatic": (
            visual_qa.full_audio_animatic_sha256,
            artifacts["full-audio-animatic"].sha256,
        ),
        "animatic media QA": (
            visual_qa.animatic_media_qa_report_sha256,
            artifacts["animatic-media-qa-report"].sha256,
        ),
        "final scene receipt": (
            visual_qa.final_scene_receipt_sha256,
            artifacts["final-scene-receipt"].sha256,
        ),
        "motion-health report": (
            visual_qa.motion_health_report_sha256,
            artifacts["motion-health-report"].sha256,
        ),
        "exposure/mobile-readability report": (
            visual_qa.exposure_mobile_readability_report_sha256,
            artifacts["exposure-mobile-readability-report"].sha256,
        ),
        "final-quality transition report": (
            visual_qa.final_quality_transition_report_sha256,
            artifacts["final-quality-transition-report"].sha256,
        ),
        "vertical composition proof": (
            visual_qa.vertical_composition_proof_sha256,
            artifacts["vertical-composition-proof"].sha256,
        ),
        "vertical render profile": (
            visual_qa.vertical_render_profile_sha256,
            artifacts["vertical-render-profile"].sha256,
        ),
        "vertical bounded-proof media": (
            visual_qa.vertical_bounded_proof_media_sha256,
            artifacts["vertical-bounded-proof-media"].sha256,
        ),
    }
    for label, (actual, expected) in visual_bindings.items():
        if actual != expected:
            raise ReleaseFinalizationError(
                f"human visual-QA approval has a stale {label} binding"
            )
    now = datetime.now(UTC)
    if (
        visual_qa.reviewed_at.astimezone(UTC) > now + timedelta(minutes=5)
        or visual_qa.reviewed_at > request.recorded_at
    ):
        raise ReleaseFinalizationError(
            "human visual-QA approval timestamp is after the verified release time"
        )

    closure_bindings = {
        "release hold ID": (
            review_closure.release_hold_id,
            release_hold.hold_id,
        ),
        "release hold hash": (
            review_closure.release_hold_sha256,
            release_hold_sha256,
        ),
        "held release identity": (
            review_closure.held_release_identity_sha256,
            release_hold.release_identity_sha256,
        ),
        "human visual-QA approval": (
            review_closure.human_visual_qa_approval_sha256,
            artifacts["human-visual-qa-approval"].sha256,
        ),
        "corrected scene": (
            review_closure.corrected_scene_sha256,
            artifacts["final-scene"].sha256,
        ),
        "corrected render profile": (
            review_closure.corrected_render_profile_sha256,
            artifacts["horizontal-render-profile"].sha256,
        ),
        "calibration evidence": (
            review_closure.calibration_evidence_sha256,
            artifacts["final-resolution-calibration-evidence"].sha256,
        ),
        "transition report": (
            review_closure.final_quality_transition_report_sha256,
            artifacts["final-quality-transition-report"].sha256,
        ),
        "vertical proof": (
            review_closure.vertical_composition_proof_sha256,
            artifacts["vertical-composition-proof"].sha256,
        ),
    }
    for label, (actual, expected) in closure_bindings.items():
        if actual != expected:
            raise ReleaseFinalizationError(
                f"human review closure has a stale {label} binding"
            )
    if (
        review_closure.reviewed_at < visual_qa.reviewed_at
        or review_closure.reviewed_at > request.recorded_at
        or review_closure.reviewed_at.astimezone(UTC) > now + timedelta(minutes=5)
    ):
        raise ReleaseFinalizationError(
            "human review closure must follow visual QA and precede finalization"
        )

    calibration_bindings = {
        "scene": (
            calibration_evidence.scene_sha256,
            artifacts["final-scene"].sha256,
        ),
        "profile": (
            calibration_evidence.render_profile_sha256,
            artifacts["horizontal-render-profile"].sha256,
        ),
        "builder": (
            calibration_evidence.builder_source_sha256,
            builder_sha256,
        ),
        "worker": (
            calibration_evidence.worker_requirement_id,
            worker.id,
        ),
        "hardware report": (
            calibration_evidence.hardware_report_sha256,
            artifacts["hardware-and-storage-report"].sha256,
        ),
        "implementation commit": (
            calibration_evidence.implementation_commit_sha,
            git_revision.implementation_commit_sha,
        ),
        "source tree": (
            calibration_evidence.source_tree_sha256,
            git_revision.source_tree_sha256,
        ),
    }
    for label, (actual, expected) in calibration_bindings.items():
        if actual != expected:
            raise ReleaseFinalizationError(
                f"calibration evidence has a stale {label} binding"
            )
    image_paths: set[str] = set()
    log_paths: set[str] = set()
    for sample in calibration_evidence.samples:
        if sample.image.path in image_paths or sample.renderer_log.path in log_paths:
            raise ReleaseFinalizationError(
                "calibration samples must bind unique frame and renderer-log evidence"
            )
        image_paths.add(sample.image.path)
        log_paths.add(sample.renderer_log.path)
        _validate_image_evidence(
            root,
            sample.image,
            label=f"calibration frame {sample.frame}",
        )
        _resolve_evidence_file(
            root,
            sample.renderer_log,
            label=f"calibration renderer log {sample.frame}",
        )
    stage_evidence_paths = {
        forecast.evidence.path for forecast in calibration_evidence.stage_forecasts
    }
    if len(stage_evidence_paths) != len(calibration_evidence.stage_forecasts):
        raise ReleaseFinalizationError(
            "every calibration stage must bind its own evidence basis"
        )
    for forecast in calibration_evidence.stage_forecasts:
        _resolve_evidence_file(
            root,
            forecast.evidence,
            label=f"{forecast.stage} forecast evidence",
        )
    derived_calibration = _derive_calibration(calibration_evidence)

    if (
        hardware.scene_sha256 != artifacts["final-scene"].sha256
        or hardware.render_profile_sha256
        != artifacts["horizontal-render-profile"].sha256
        or hardware.worker_requirement_id != worker.id
    ):
        raise ReleaseFinalizationError(
            "hardware report does not bind the exact scene/profile/worker"
        )
    if hardware.blender.version != "5.2.0 LTS":
        raise ReleaseFinalizationError(
            "hardware report does not record the locked Blender 5.2.0 LTS version"
        )
    if max(gpu.vram_bytes for gpu in hardware.gpus) < worker.minimum_vram_mib * 1024 * 1024:
        raise ReleaseFinalizationError(
            "hardware report does not satisfy the worker VRAM requirement"
        )
    _resolve_evidence_file(
        root,
        hardware.storage.throughput_evidence,
        label="target-disk throughput evidence",
    )
    if hardware.recorded_at > request.recorded_at:
        raise ReleaseFinalizationError(
            "hardware report cannot be recorded after release finalization"
        )

    if (
        deterministic.scene_sha256 != artifacts["final-scene"].sha256
        or deterministic.render_profile_sha256
        != artifacts["horizontal-render-profile"].sha256
    ):
        raise ReleaseFinalizationError(
            "deterministic-effects report has stale scene/profile bindings"
        )
    storage_pairs = (
        (
            deterministic.storage_free_bytes,
            hardware.storage.free_bytes,
        ),
        (
            deterministic.storage_projected_peak_disk_bytes,
            hardware.storage.projected_peak_disk_bytes,
        ),
        (
            deterministic.storage_safety_multiplier,
            hardware.storage.safety_multiplier,
        ),
    )
    if any(
        not math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-6)
        for actual, expected in storage_pairs
    ):
        raise ReleaseFinalizationError(
            "calibration, deterministic, and hardware storage evidence disagree"
        )
    if (
        hardware.storage.projected_peak_disk_bytes
        < calibration_evidence.projected_output_bytes
    ):
        raise ReleaseFinalizationError(
            "projected peak disk usage may not be below projected final output"
        )
    if (
        dependency.scene_sha256 != artifacts["final-scene"].sha256
        or dependency.render_profile_sha256
        != artifacts["horizontal-render-profile"].sha256
    ):
        raise ReleaseFinalizationError(
            "dependency-health report has stale scene/profile bindings"
        )

    if (
        dashboard.scene_sha256 != artifacts["final-scene"].sha256
        or dashboard.render_profile_sha256
        != artifacts["horizontal-render-profile"].sha256
        or dashboard.source_revision_git_commit_sha
        != git_revision.implementation_commit_sha
        or dashboard.source_revision_source_tree_sha256
        != git_revision.source_tree_sha256
    ):
        raise ReleaseFinalizationError(
            "live-dashboard proof has stale scene/profile/revision bindings"
        )
    if (
        dashboard.completed_frame.image.width != 1920
        or dashboard.completed_frame.image.height != 1080
    ):
        raise ReleaseFinalizationError(
            "live-dashboard proof does not show a valid final-resolution completed frame"
        )
    _validate_image_evidence(
        root,
        dashboard.completed_frame.image,
        label="live-dashboard completed frame",
    )
    if (
        dashboard.completed_frame.completed_at > request.recorded_at
        or dashboard.completed_frame.publication_started_at
        > request.recorded_at
        or dashboard.completed_frame.published_at > request.recorded_at
        or dashboard.eta.updated_at > request.recorded_at
    ):
        raise ReleaseFinalizationError(
            "live-dashboard proof cannot be recorded after release finalization"
        )

    animatic_artifact = artifacts["full-audio-animatic"]
    if (
        animatic_qa.scene_sha256 != artifacts["final-scene"].sha256
        or animatic_qa.render_profile_sha256
        != artifacts["horizontal-render-profile"].sha256
        or animatic_qa.source_audio_sha256 != SOURCE_AUDIO_SHA256
        or animatic_qa.encoded_media.path != animatic_artifact.path
        or animatic_qa.encoded_media.sha256 != animatic_artifact.sha256
        or animatic_qa.encoded_media.width != 480
        or animatic_qa.encoded_media.height != 270
    ):
        raise ReleaseFinalizationError(
            "animatic media-QA report has stale media/scene/profile/audio bindings"
        )
    expected_duration = ANDROMEDA_V2_FRAME_END / 30
    if not math.isclose(
        animatic_qa.encoded_media.duration_seconds,
        expected_duration,
        rel_tol=0.0,
        abs_tol=0.05,
    ):
        raise ReleaseFinalizationError(
            "full animatic duration does not preserve the exact 13,029-frame song clock"
        )
    _validate_media_evidence(
        root,
        animatic_qa.encoded_media,
        label="full-audio animatic",
    )
    if (
        animatic_receipt.artifact.path != animatic_artifact.path
        or animatic_receipt.artifact.sha256 != animatic_artifact.sha256
        or animatic_receipt.audio_source_sha256 != SOURCE_AUDIO_SHA256
        or animatic_receipt.scene_sha256 != artifacts["final-scene"].sha256
        or animatic_receipt.render_profile_sha256
        != artifacts["horizontal-render-profile"].sha256
        or animatic_receipt.story_plan_sha256 != artifacts["story-plan"].sha256
        or animatic_receipt.shot_plan_sha256 != artifacts["shot-plan"].sha256
    ):
        raise ReleaseFinalizationError("animatic receipt has stale exact bindings")
    _resolve_evidence_file(
        root,
        animatic_receipt.artifact,
        label="animatic receipt media",
    )

    if (
        final_scene_receipt.local_artifact.path != artifacts["final-scene"].path
        or final_scene_receipt.local_artifact.sha256
        != artifacts["final-scene"].sha256
        or final_scene_receipt.build_receipt.path
        != artifacts["horizontal-scene-build-receipt"].path
        or final_scene_receipt.build_receipt.sha256
        != artifacts["horizontal-scene-build-receipt"].sha256
        or final_scene_receipt.builder_source_sha256 != builder_sha256
        or final_scene_receipt.render_profile_sha256
        != artifacts["horizontal-render-profile"].sha256
    ):
        raise ReleaseFinalizationError("final-scene receipt has stale exact bindings")
    _resolve_evidence_file(
        root,
        final_scene_receipt.local_artifact,
        label="final scene receipt scene",
    )
    _resolve_evidence_file(
        root,
        final_scene_receipt.build_receipt,
        label="final scene receipt build receipt",
    )

    for label, report in (
        ("motion-health", motion),
        ("exposure/mobile-readability", exposure),
    ):
        if (
            report.scene_sha256 != artifacts["final-scene"].sha256
            or report.render_profile_sha256
            != artifacts["horizontal-render-profile"].sha256
        ):
            raise ReleaseFinalizationError(
                f"{label} report has stale scene/profile bindings"
            )

    if (
        transitions.scene_sha256 != artifacts["final-scene"].sha256
        or transitions.render_profile_sha256
        != artifacts["horizontal-render-profile"].sha256
        or transitions.source_audio_sha256 != SOURCE_AUDIO_SHA256
    ):
        raise ReleaseFinalizationError(
            "final-quality transition report has stale scene/profile/audio bindings"
        )
    transition_roles = {
        "gates-to-rupture": "gates-to-rupture-media",
        "rupture-to-transformation": "rupture-to-transformation-media",
        "transformation-to-arrival": "transformation-to-arrival-media",
    }
    for transition_sample in transitions.samples:
        role = transition_roles[transition_sample.id]
        artifact = artifacts[role]
        if (
            transition_sample.media.path != artifact.path
            or transition_sample.media.sha256 != artifact.sha256
            or transition_sample.media.width != 1920
            or transition_sample.media.height != 1080
        ):
            raise ReleaseFinalizationError(
                f"transition sample {transition_sample.id} has stale media bindings"
            )
        _validate_media_evidence(
            root,
            transition_sample.media,
            label=f"{transition_sample.id} transition media",
        )
        media_qa = _model_from_evidence_file(
            root,
            transition_sample.media_qa,
            f"{transition_sample.id} transition media QA",
            TransitionMediaQaReport,
        )
        if (
            media_qa.media_sha256 != transition_sample.media.sha256
            or media_qa.frame_start != transition_sample.frame_start
            or media_qa.frame_end != transition_sample.frame_end
        ):
            raise ReleaseFinalizationError(
                f"transition sample {transition_sample.id} has stale media-QA bindings"
            )

    vertical_media_artifact = artifacts["vertical-bounded-proof-media"]
    if (
        vertical_media_qa.scene_sha256
        != artifacts["vertical-master-scene"].sha256
        or vertical_media_qa.render_profile_sha256
        != artifacts["vertical-render-profile"].sha256
        or vertical_media_qa.media.path != vertical_media_artifact.path
        or vertical_media_qa.media.sha256 != vertical_media_artifact.sha256
    ):
        raise ReleaseFinalizationError(
            "vertical bounded-proof media QA has stale exact bindings"
        )
    _validate_media_evidence(
        root,
        vertical_media_qa.media,
        label="vertical bounded-proof media",
    )
    if (
        vertical_proof.render_profile_sha256
        != artifacts["vertical-render-profile"].sha256
        or vertical_proof.master_scene.path
        != artifacts["vertical-master-scene"].path
        or vertical_proof.master_scene.sha256
        != artifacts["vertical-master-scene"].sha256
        or vertical_proof.bounded_proof.path != vertical_media_artifact.path
        or vertical_proof.bounded_proof.sha256 != vertical_media_artifact.sha256
        or vertical_proof.bounded_proof.media_qa_sha256
        != artifacts["vertical-bounded-proof-media-qa"].sha256
        or vertical_proof.independent_authorship.composition_profile_id
        != vertical_profile.output_variant.composition_profile_id
        or vertical_proof.independent_authorship.camera_name
        != vertical_profile.output_variant.camera_name
    ):
        raise ReleaseFinalizationError(
            "vertical composition proof has stale scene/profile/media/authorship bindings"
        )

    source_revision_bindings: dict[str, object] = {
        "branch": source_revision.branch,
        "starting commit": source_revision.starting_commit_sha,
        "implementation commit": source_revision.implementation_commit_sha,
        "commit list": tuple(source_revision.commit_list),
        "source tree": source_revision.source_tree_sha256,
        "source tree entry count": source_revision.source_tree_entry_count,
        "release-owned paths": tuple(source_revision.release_owned_paths),
        "remote push status": source_revision.remote_push_status,
        "remote tracking ref": source_revision.remote_tracking_ref,
        "remote commit": source_revision.remote_commit_sha,
    }
    expected_source_revision: dict[str, object] = {
        "branch": git_revision.branch,
        "starting commit": request.starting_commit_sha,
        "implementation commit": git_revision.implementation_commit_sha,
        "commit list": git_revision.commit_list,
        "source tree": git_revision.source_tree_sha256,
        "source tree entry count": git_revision.source_tree_entry_count,
        "release-owned paths": _RELEASE_OWNED_GIT_PATHS,
        "remote push status": git_revision.remote_push_status,
        "remote tracking ref": git_revision.remote_tracking_ref,
        "remote commit": git_revision.remote_commit_sha,
    }
    for label, actual_revision_value in source_revision_bindings.items():
        if actual_revision_value != expected_source_revision[label]:
            raise ReleaseFinalizationError(
                f"source-revision report does not match Git-derived {label}"
            )
    expected_bound_source_hashes = {
        "builder": artifacts["builder-source"].sha256,
        "ownerCreativeAcceptance": artifacts["owner-creative-acceptance"].sha256,
        "encodingProfiles": artifacts["encoding-profiles"].sha256,
        "lookProfile": artifacts["final-look-profile"].sha256,
        "storyPlan": artifacts["story-plan"].sha256,
        "shotPlan": artifacts["shot-plan"].sha256,
        "outputVariantContract": artifacts["output-variants"].sha256,
        "horizontalRenderProfile": artifacts["horizontal-render-profile"].sha256,
        "verticalRenderProfile": artifacts["vertical-render-profile"].sha256,
    }
    if source_revision.bound_source_hashes != expected_bound_source_hashes:
        raise ReleaseFinalizationError(
            "source-revision report has stale bound source hashes"
        )
    if (
        verification.implementation_commit_sha
        != git_revision.implementation_commit_sha
        or verification.source_tree_sha256 != git_revision.source_tree_sha256
    ):
        raise ReleaseFinalizationError(
            "verification report has stale implementation revision bindings"
        )
    if (
        worker_report.local_match.detected_vram_bytes
        < worker.minimum_vram_mib * 1024 * 1024
        or worker_report.local_match.detected_gpu_model
        not in {gpu.model for gpu in hardware.gpus}
    ):
        raise ReleaseFinalizationError(
            "worker local-match evidence disagrees with the hardware report"
        )

    return _ValidatedFinalizationInputs(
        output_variants=output_variants,
        story_plan=story_plan,
        shot_plan=shot_plan,
        horizontal_profile=horizontal_profile,
        vertical_profile=vertical_profile,
        visual_qa=visual_qa,
        review_closure=review_closure,
        horizontal_receipt=horizontal_receipt,
        vertical_receipt=vertical_receipt,
        worker=worker,
        calibration_evidence=calibration_evidence,
        derived_calibration=derived_calibration,
        hardware=hardware,
        deterministic=deterministic,
        dependency=dependency,
        dashboard=dashboard,
        animatic_qa=animatic_qa,
        animatic_receipt=animatic_receipt,
        final_scene_receipt=final_scene_receipt,
        motion=motion,
        exposure=exposure,
        transitions=transitions,
        vertical_media_qa=vertical_media_qa,
        vertical_proof=vertical_proof,
        source_revision=source_revision,
        verification=verification,
        git_revision=git_revision,
        release_hold=release_hold,
        release_hold_sha256=release_hold_sha256,
    )


def _package_artifact(
    root: Path,
    role: str,
    path: Path,
    sha256: str,
) -> PackageArtifact:
    return PackageArtifact(
        role=role,
        path=_relative_posix(root, path),
        sha256=sha256,
        immutable=True,
    )


def _legacy_build_release_report(
    request: ReleaseFinalizationRequest,
    *,
    root: Path,
    artifacts: Mapping[str, ExplicitArtifact],
    identity: FinalReleaseIdentity,
    calibration_path: Path,
    calibration_sha256: str,
    technical_path: Path,
    technical_sha256: str,
    package_path: Path,
) -> dict[str, object]:
    return {
        "schemaVersion": "3.0.0",
        "kind": "trackprompt-andromeda-v2-release-report",
        "projectId": "trip-to-andromeda-v2",
        "releaseTag": request.release_tag,
        "recordedAt": request.recorded_at.isoformat(),
        "branch": request.branch,
        "startingCommitSha": request.starting_commit_sha,
        "implementationCommitSha": request.implementation_commit_sha,
        "commitList": request.commit_list,
        "sourceTreeSha256": request.source_tree_sha256,
        "sourceTreeEntryCount": request.source_tree_entry_count,
        "releaseIdentitySha256": final_release_identity_sha256(identity),
        "supersedesReleaseIdentitySha256": (
            request.supersedes_release_identity_sha256
        ),
        "enabledOutputMatrix": {
            "matrixId": identity.output_matrix.matrix_id,
            "enabledVariantIds": [HORIZONTAL_VARIANT_ID],
            "optionalDisabledVariantIds": [VERTICAL_VARIANT_ID],
        },
        "horizontal": {
            "scenePath": artifacts["final-scene"].path,
            "sceneSha256": artifacts["final-scene"].sha256,
            "profilePath": artifacts["horizontal-render-profile"].path,
            "profileSha256": artifacts["horizontal-render-profile"].sha256,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "outputPattern": request.horizontal_output_pattern,
        },
        "vertical": {
            "enabled": False,
            "scenePath": artifacts["vertical-master-scene"].path,
            "sceneSha256": artifacts["vertical-master-scene"].sha256,
            "profilePath": artifacts["vertical-render-profile"].path,
            "profileSha256": artifacts["vertical-render-profile"].sha256,
            "boundedProofPath": artifacts["vertical-composition-proof"].path,
            "boundedProofSha256": artifacts["vertical-composition-proof"].sha256,
            "separateFinalResolutionCalibrationRequiredBeforeEnablement": True,
        },
        "fullAnimatic": {
            "path": artifacts["full-audio-animatic"].path,
            "sha256": artifacts["full-audio-animatic"].sha256,
            "mediaQaPath": artifacts["animatic-media-qa-report"].path,
            "mediaQaSha256": artifacts["animatic-media-qa-report"].sha256,
            "technicalPass": True,
        },
        "humanVisualQa": {
            "path": artifacts["human-visual-qa-approval"].path,
            "sha256": artifacts["human-visual-qa-approval"].sha256,
            "decision": "approved-for-final-release",
            "allSevenActsAtR131ProjectLevel": True,
            "codexHumanArtisticApproval": False,
        },
        "calibration": {
            "path": _relative_posix(root, calibration_path),
            "sha256": calibration_sha256,
            "aggregateSecondsP50": math.fsum(
                item.p50_seconds
                for item in request.calibration.stage_forecasts
            ),
            "aggregateSecondsP90": math.fsum(
                item.p90_seconds
                for item in request.calibration.stage_forecasts
            ),
            "slaSeconds": 86_400,
            "slaSatisfied": True,
        },
        "technicalAuthorization": {
            "path": _relative_posix(root, technical_path),
            "sha256": technical_sha256,
            "technicalReady": True,
            "operatorStartGate": "not-authorized",
            "productionStartAllowed": False,
            "finalRenderStarted": False,
        },
        "packageManifest": {
            "path": _relative_posix(root, package_path),
            "hashRecordedExternallyToAvoidSelfReference": True,
        },
        "fullProductionRenderInvoked": False,
        "externalRenderOrEncodeProcessStarted": False,
        "codexHumanArtisticApproval": False,
        "productionRenderStarted": False,
    }


class ReportArtifact(APIModel):
    role: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    path: str
    sha256: str = Field(pattern=_HEX_64)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return _safe_evidence_path(value)


class ReportGitStatus(APIModel):
    branch: str
    starting_commit_sha: str = Field(pattern=_COMMIT_SHA)
    implementation_commit_sha: str = Field(pattern=_COMMIT_SHA)
    commit_list: list[str]
    source_tree_sha256: str = Field(pattern=_HEX_64)
    source_tree_entry_count: int = Field(ge=1)
    release_owned_paths_clean: Literal[True]
    remote_push_status: Literal["pushed", "not-pushed", "not-configured"]
    remote_tracking_ref: str | None
    remote_commit_sha: str | None = Field(default=None, pattern=_COMMIT_SHA)


class ReportStoryPlan(APIModel):
    story_plan: ReportArtifact
    shot_plan: ReportArtifact
    act_count: Literal[7]
    shot_count: Literal[35]
    sub_shot_count: int = Field(ge=0)


class ReportOutputVariant(APIModel):
    id: Literal["horizontal-16x9-1080p", "vertical-9x16-1080p"]
    enabled: bool
    scene: ReportArtifact
    render_profile: ReportArtifact
    width: int
    height: int
    fps: Literal[30]
    frame_start: Literal[1]
    frame_end: Literal[13029]
    output_pattern: str | None
    exact_settings: VersionedRenderProfile


class ReportCalibration(APIModel):
    evidence: ReportArtifact
    generated_calibration: ReportArtifact
    representative_measurements: list[CalibrationFrameMeasurement]
    p50_seconds_per_frame: float = Field(gt=0.0)
    p90_seconds_per_frame: float = Field(gt=0.0)
    weighted_seconds_per_frame: float = Field(gt=0.0)
    projected_render_seconds_p50: float = Field(gt=0.0)
    projected_render_seconds_p90: float = Field(gt=0.0)
    projected_render_seconds_weighted: float = Field(gt=0.0)
    detailed_stage_forecasts: list[CalibrationStageForecast]
    aggregate_p50_seconds: float = Field(gt=0.0)
    aggregate_p90_seconds: float = Field(gt=0.0)
    sla_limit_seconds: Literal[86400]
    sla_satisfied: Literal[True]


class ReportForecastPercentiles(APIModel):
    p50_seconds: float = Field(ge=0.0)
    p90_seconds: float = Field(ge=0.0)

    @model_validator(mode="after")
    def ordered_percentiles(self) -> ReportForecastPercentiles:
        if self.p50_seconds > self.p90_seconds:
            raise ValueError("forecast P50 may not exceed P90")
        return self


class ReportEnabledVariantForecast(APIModel):
    output_variant_id: Literal["horizontal-16x9-1080p"]
    frame_count: Literal[13029]
    render: ReportForecastPercentiles
    encoding: ReportForecastPercentiles
    qa: ReportForecastPercentiles
    total: ReportForecastPercentiles


class ReportAggregateForecast(APIModel):
    enabled_variant_ids: list[Literal["horizontal-16x9-1080p"]] = Field(
        min_length=1,
        max_length=1,
    )
    render: ReportForecastPercentiles
    encoding: ReportForecastPercentiles
    qa: ReportForecastPercentiles
    total: ReportForecastPercentiles


class ReportGeneratedDocuments(APIModel):
    calibration: ReportArtifact
    technical_authorization: ReportArtifact
    package_manifest_path: str
    package_manifest_sha256_delivery: Literal[
        "ReleaseFinalizationResult.sha256.packageManifest"
    ]
    release_report_path: str
    release_report_sha256_delivery: Literal[
        "ReleaseFinalizationResult.sha256.releaseReport"
    ]

    @field_validator("package_manifest_path", "release_report_path")
    @classmethod
    def safe_paths(cls, value: str) -> str:
        return _safe_evidence_path(value)


class ReportCommands(APIModel):
    operator_authorization: str
    horizontal_start_or_resume: str
    horizontal_resume: str
    horizontal_plus_vertical_reference: str
    dashboard_launch: Literal[".\\WZHK-Media-Launcher.cmd"]
    production_start_authorized: Literal[False]


class FinalReleaseReport(APIModel):
    schema_version: Literal["4.1.0"]
    kind: Literal["trackprompt-andromeda-v2-release-report"]
    project_id: Literal["trip-to-andromeda-v2"]
    release_tag: str = Field(pattern=_RELEASE_TAG)
    recorded_at: datetime
    release_identity_sha256: str = Field(pattern=_HEX_64)
    superseded_release_identity_sha256: str = Field(pattern=_HEX_64)
    release_hold: ReportArtifact
    human_review_closure: ReportArtifact
    git: ReportGitStatus
    owner_creative_acceptance: ReportArtifact
    story: ReportStoryPlan
    final_look_profile: ReportArtifact
    final_scene_package_artifacts: list[ReportArtifact] = Field(min_length=1)
    full_animatic: AnimaticMediaQaReport
    hardware_fingerprint: HardwareAndStorageReport
    enabled_output_matrix_id: str
    enabled_variant_ids: list[Literal["horizontal-16x9-1080p"]]
    optional_disabled_variant_ids: list[Literal["vertical-9x16-1080p"]]
    output_variants: list[ReportOutputVariant] = Field(min_length=2, max_length=2)
    enabled_variant_forecasts: list[ReportEnabledVariantForecast] = Field(
        min_length=1,
        max_length=1,
    )
    aggregate_forecast: ReportAggregateForecast
    calibration: ReportCalibration
    disk_free_bytes: int = Field(ge=1)
    projected_peak_disk_bytes: int = Field(ge=1)
    disk_safety_multiplier: float = Field(ge=1.25)
    disk_headroom_satisfied: Literal[True]
    minimum_worker_vram_mib: int = Field(ge=1)
    detected_gpu_vram_bytes: int = Field(ge=1)
    vram_stable: Literal[True]
    live_frame_and_eta_dashboard_proof: LiveDashboardProof
    generated_documents: ReportGeneratedDocuments
    commands: ReportCommands
    verification: VerificationReport
    remaining_known_limitations: list[str]
    full_production_render_started: Literal[False]
    external_render_or_encode_process_started: Literal[False]
    codex_human_artistic_approval: Literal[False]
    operator_start_gate: Literal["not-authorized"]
    production_start_allowed: Literal[False]

    @model_validator(mode="after")
    def exact_section_16_forecasts(self) -> FinalReleaseReport:
        forecast_ids = [
            forecast.output_variant_id
            for forecast in self.enabled_variant_forecasts
        ]
        if forecast_ids != self.enabled_variant_ids:
            raise ValueError(
                "per-variant forecasts must exactly match enabledVariantIds"
            )
        if self.aggregate_forecast.enabled_variant_ids != self.enabled_variant_ids:
            raise ValueError(
                "aggregate forecast must name the exact enabled variants"
            )
        stages = {
            forecast.stage: forecast
            for forecast in self.calibration.detailed_stage_forecasts
        }
        try:
            encoding = stages["encoding"]
            qa = stages["final-qa"]
        except KeyError as exc:
            raise ValueError(
                "section 16 forecasts require encoding and final-QA stages"
            ) from exc
        variant = self.enabled_variant_forecasts[0]
        expected_values = (
            (
                variant.render.p50_seconds,
                self.calibration.projected_render_seconds_p50,
            ),
            (
                variant.render.p90_seconds,
                self.calibration.projected_render_seconds_p90,
            ),
            (variant.encoding.p50_seconds, encoding.p50_seconds),
            (variant.encoding.p90_seconds, encoding.p90_seconds),
            (variant.qa.p50_seconds, qa.p50_seconds),
            (variant.qa.p90_seconds, qa.p90_seconds),
            (
                variant.total.p50_seconds,
                self.calibration.aggregate_p50_seconds,
            ),
            (
                variant.total.p90_seconds,
                self.calibration.aggregate_p90_seconds,
            ),
        )
        if any(
            not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-6)
            for actual, expected in expected_values
        ):
            raise ValueError(
                "per-variant forecasts must match the measured calibration"
            )
        aggregate_pairs = (
            (self.aggregate_forecast.render, variant.render),
            (self.aggregate_forecast.encoding, variant.encoding),
            (self.aggregate_forecast.qa, variant.qa),
            (self.aggregate_forecast.total, variant.total),
        )
        if any(
            not math.isclose(
                aggregate.p50_seconds,
                per_variant.p50_seconds,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
            or not math.isclose(
                aggregate.p90_seconds,
                per_variant.p90_seconds,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
            for aggregate, per_variant in aggregate_pairs
        ):
            raise ValueError(
                "horizontal-only aggregate forecasts must equal the enabled "
                "variant forecasts"
            )
        return self


def _artifact_report(artifact: ExplicitArtifact) -> ReportArtifact:
    return ReportArtifact(
        role=artifact.role,
        path=artifact.path,
        sha256=artifact.sha256,
    )


def _build_release_report(
    request: ReleaseFinalizationRequest,
    *,
    root: Path,
    artifacts: Mapping[str, ExplicitArtifact],
    identity: FinalReleaseIdentity,
    validated: _ValidatedFinalizationInputs,
    calibration_path: Path,
    calibration_sha256: str,
    technical_path: Path,
    technical_sha256: str,
    package_path: Path,
    report_path: Path,
) -> FinalReleaseReport:
    derived = validated.derived_calibration
    output_root = str(
        PurePosixPath(request.horizontal_output_pattern).parent.parent
    )
    calibration_report = ReportArtifact(
        role="final-calibration-v2",
        path=_relative_posix(root, calibration_path),
        sha256=calibration_sha256,
    )
    technical_report = ReportArtifact(
        role="technical-authorization-v2",
        path=_relative_posix(root, technical_path),
        sha256=technical_sha256,
    )
    common_start = (
        "powershell -NoProfile -ExecutionPolicy Bypass -File "
        ".\\production\\andromeda-v2\\invoke-production.ps1 "
        "-Action StartOrResume "
        f'-ScenePath "{artifacts["final-scene"].path}" '
        f'-RenderProfilePath "{artifacts["horizontal-render-profile"].path}" '
        f'-OutputDirectory "{output_root}" '
        f'-CalibrationPath "{_relative_posix(root, calibration_path)}" '
        f'-PackageManifestPath "{_relative_posix(root, package_path)}" '
        f'-TechnicalAuthorizationPath "{_relative_posix(root, technical_path)}" '
        '-OperatorAuthorizationPath "<exact local operator artifact>" '
        '-SourceAudioPath "<exact private source-audio path>" '
        '-SourceCuePath "<exact private visual-cues path>" '
        '-AuthorizationToken "<exact horizontal scene/profile token>"'
    )
    operator_command = (
        "powershell -NoProfile -ExecutionPolicy Bypass -File "
        ".\\production\\andromeda-v2\\new-operator-authorization.ps1 "
        f'-CalibrationPath "{_relative_posix(root, calibration_path)}" '
        f'-PackageManifestPath "{_relative_posix(root, package_path)}" '
        f'-TechnicalAuthorizationPath "{_relative_posix(root, technical_path)}" '
        "-OutputPath "
        f'"production/andromeda-v2/.operator-authorizations/'
        f"{identity.output_matrix.matrix_id}.operator-start-authorization.json"
        '"'
    )
    dual_reference = (
        "powershell -NoProfile -ExecutionPolicy Bypass -File "
        ".\\production\\andromeda-v2\\invoke-production.ps1 "
        "-Action StartOrResume -EnableVertical "
        "-ScenePath \"<dual-release horizontal scene>\" "
        "-RenderProfilePath \"<dual-release horizontal profile>\" "
        "-OutputDirectory \"<exact dual-release horizontal output root>\" "
        "-VerticalScenePath \"<dual-release vertical scene>\" "
        "-VerticalRenderProfilePath \"<dual-release vertical profile>\" "
        "-VerticalOutputDirectory \"<exact dual-release vertical output root>\" "
        "-CalibrationPath \"<dual-matrix calibration>\" "
        "-PackageManifestPath \"<dual-matrix package>\" "
        "-TechnicalAuthorizationPath \"<dual-matrix technical authorization>\" "
        "-OperatorAuthorizationPath \"<dual-matrix operator authorization>\" "
        "-SourceAudioPath \"<exact private source-audio path>\" "
        "-SourceCuePath \"<exact private visual-cues path>\" "
        "-AuthorizationToken \"<exact horizontal scene/profile token>\" "
        "-VerticalAuthorizationToken \"<exact vertical scene/profile token>\""
    )
    detailed_stages = {
        forecast.stage: forecast
        for forecast in derived.detailed_stage_forecasts
    }
    enabled_variant_forecast = ReportEnabledVariantForecast(
        output_variant_id=HORIZONTAL_VARIANT_ID,
        frame_count=ANDROMEDA_V2_FRAME_END,
        render=ReportForecastPercentiles(
            p50_seconds=derived.projected_render_seconds_p50,
            p90_seconds=derived.projected_render_seconds_p90,
        ),
        encoding=ReportForecastPercentiles(
            p50_seconds=detailed_stages["encoding"].p50_seconds,
            p90_seconds=detailed_stages["encoding"].p90_seconds,
        ),
        qa=ReportForecastPercentiles(
            p50_seconds=detailed_stages["final-qa"].p50_seconds,
            p90_seconds=detailed_stages["final-qa"].p90_seconds,
        ),
        total=ReportForecastPercentiles(
            p50_seconds=derived.aggregate_p50_seconds,
            p90_seconds=derived.aggregate_p90_seconds,
        ),
    )
    aggregate_forecast = ReportAggregateForecast(
        enabled_variant_ids=[HORIZONTAL_VARIANT_ID],
        render=enabled_variant_forecast.render,
        encoding=enabled_variant_forecast.encoding,
        qa=enabled_variant_forecast.qa,
        total=enabled_variant_forecast.total,
    )
    hold_path = root.joinpath(*_RELEASE_HOLD_PATH.parts)
    return FinalReleaseReport(
        schema_version="4.1.0",
        kind="trackprompt-andromeda-v2-release-report",
        project_id="trip-to-andromeda-v2",
        release_tag=request.release_tag,
        recorded_at=request.recorded_at,
        release_identity_sha256=final_release_identity_sha256(identity),
        superseded_release_identity_sha256=(
            request.supersedes_release_identity_sha256
        ),
        release_hold=ReportArtifact(
            role="release-hold",
            path=_RELEASE_HOLD_PATH.as_posix(),
            sha256=file_sha256(hold_path),
        ),
        human_review_closure=_artifact_report(
            artifacts["human-review-closure"]
        ),
        git=ReportGitStatus(
            branch=validated.git_revision.branch,
            starting_commit_sha=request.starting_commit_sha,
            implementation_commit_sha=(
                validated.git_revision.implementation_commit_sha
            ),
            commit_list=list(validated.git_revision.commit_list),
            source_tree_sha256=validated.git_revision.source_tree_sha256,
            source_tree_entry_count=(
                validated.git_revision.source_tree_entry_count
            ),
            release_owned_paths_clean=True,
            remote_push_status=validated.git_revision.remote_push_status,
            remote_tracking_ref=validated.git_revision.remote_tracking_ref,
            remote_commit_sha=validated.git_revision.remote_commit_sha,
        ),
        owner_creative_acceptance=_artifact_report(
            artifacts["owner-creative-acceptance"]
        ),
        story=ReportStoryPlan(
            story_plan=_artifact_report(artifacts["story-plan"]),
            shot_plan=_artifact_report(artifacts["shot-plan"]),
            act_count=7,
            shot_count=35,
            sub_shot_count=0,
        ),
        final_look_profile=_artifact_report(artifacts["final-look-profile"]),
        final_scene_package_artifacts=[
            _artifact_report(artifacts[role])
            for role in (
                "final-scene",
                "horizontal-scene-build-receipt",
                "builder-source",
                "vertical-master-scene",
                "vertical-scene-build-receipt",
            )
        ],
        full_animatic=validated.animatic_qa,
        hardware_fingerprint=validated.hardware,
        enabled_output_matrix_id=identity.output_matrix.matrix_id,
        enabled_variant_ids=[HORIZONTAL_VARIANT_ID],
        optional_disabled_variant_ids=[VERTICAL_VARIANT_ID],
        output_variants=[
            ReportOutputVariant(
                id=HORIZONTAL_VARIANT_ID,
                enabled=True,
                scene=_artifact_report(artifacts["final-scene"]),
                render_profile=_artifact_report(
                    artifacts["horizontal-render-profile"]
                ),
                width=1920,
                height=1080,
                fps=30,
                frame_start=1,
                frame_end=13029,
                output_pattern=request.horizontal_output_pattern,
                exact_settings=validated.horizontal_profile,
            ),
            ReportOutputVariant(
                id=VERTICAL_VARIANT_ID,
                enabled=False,
                scene=_artifact_report(artifacts["vertical-master-scene"]),
                render_profile=_artifact_report(
                    artifacts["vertical-render-profile"]
                ),
                width=1080,
                height=1920,
                fps=30,
                frame_start=1,
                frame_end=13029,
                output_pattern=None,
                exact_settings=validated.vertical_profile,
            ),
        ],
        enabled_variant_forecasts=[enabled_variant_forecast],
        aggregate_forecast=aggregate_forecast,
        calibration=ReportCalibration(
            evidence=_artifact_report(
                artifacts["final-resolution-calibration-evidence"]
            ),
            generated_calibration=calibration_report,
            representative_measurements=validated.calibration_evidence.samples,
            p50_seconds_per_frame=derived.p50_seconds_per_frame,
            p90_seconds_per_frame=derived.p90_seconds_per_frame,
            weighted_seconds_per_frame=derived.weighted_seconds_per_frame,
            projected_render_seconds_p50=derived.projected_render_seconds_p50,
            projected_render_seconds_p90=derived.projected_render_seconds_p90,
            projected_render_seconds_weighted=(
                derived.projected_render_seconds_weighted
            ),
            detailed_stage_forecasts=list(derived.detailed_stage_forecasts),
            aggregate_p50_seconds=derived.aggregate_p50_seconds,
            aggregate_p90_seconds=derived.aggregate_p90_seconds,
            sla_limit_seconds=86400,
            sla_satisfied=True,
        ),
        disk_free_bytes=validated.hardware.storage.free_bytes,
        projected_peak_disk_bytes=(
            validated.hardware.storage.projected_peak_disk_bytes
        ),
        disk_safety_multiplier=validated.hardware.storage.safety_multiplier,
        disk_headroom_satisfied=True,
        minimum_worker_vram_mib=validated.worker.minimum_vram_mib,
        detected_gpu_vram_bytes=max(
            gpu.vram_bytes for gpu in validated.hardware.gpus
        ),
        vram_stable=True,
        live_frame_and_eta_dashboard_proof=validated.dashboard,
        generated_documents=ReportGeneratedDocuments(
            calibration=calibration_report,
            technical_authorization=technical_report,
            package_manifest_path=_relative_posix(root, package_path),
            package_manifest_sha256_delivery=(
                "ReleaseFinalizationResult.sha256.packageManifest"
            ),
            release_report_path=_relative_posix(root, report_path),
            release_report_sha256_delivery=(
                "ReleaseFinalizationResult.sha256.releaseReport"
            ),
        ),
        commands=ReportCommands(
            operator_authorization=operator_command,
            horizontal_start_or_resume=common_start,
            horizontal_resume=common_start,
            horizontal_plus_vertical_reference=dual_reference,
            dashboard_launch=".\\WZHK-Media-Launcher.cmd",
            production_start_authorized=False,
        ),
        verification=validated.verification,
        remaining_known_limitations=validated.verification.known_limitations,
        full_production_render_started=False,
        external_render_or_encode_process_started=False,
        codex_human_artistic_approval=False,
        operator_start_gate="not-authorized",
        production_start_allowed=False,
    )


def finalize_horizontal_release(
    repository_root: Path,
    request: ReleaseFinalizationRequest,
    output_directory: Path,
    *,
    overwrite: bool = False,
) -> ReleaseFinalizationResult:
    root = _repository_root(repository_root)
    output_root = _resolve_output_directory(root, output_directory)
    calibration_path = output_root / "v2-calibration.json"
    technical_path = output_root / "technical-authorization-v2.json"
    report_path = output_root / "evidence" / "release-report.json"
    package_path = output_root / "package-manifest-v2.json"

    artifacts = {artifact.role: artifact for artifact in request.artifacts}
    paths = {
        role: _resolve_repository_file(root, artifact)
        for role, artifact in artifacts.items()
    }
    validated = _validate_finalization_inputs(root, request, artifacts, paths)
    owner_sha256 = artifacts["owner-creative-acceptance"].sha256
    encoding_sha256 = artifacts["encoding-profiles"].sha256
    if owner_sha256 != OWNER_CREATIVE_ACCEPTANCE_SHA256:
        raise ReleaseFinalizationError("owner creative-acceptance hash is not immutable")
    if encoding_sha256 != ENCODING_PROFILES_SHA256:
        raise ReleaseFinalizationError("encoding-profiles hash is not immutable")
    if (
        validated.story_plan.look_profile_sha256
        != artifacts["final-look-profile"].sha256
    ):
        raise ReleaseFinalizationError("StoryPlan does not bind the exact look profile")
    if validated.shot_plan.story_plan_sha256 != artifacts["story-plan"].sha256:
        raise ReleaseFinalizationError("ShotPlan does not bind the exact StoryPlan")

    output_matrix = EnabledFinalOutputMatrix(
        schema_version="2.0.0",
        matrix_id=f"andromeda-v2-horizontal-only-{request.release_tag}",
        enabled_variant_ids=[HORIZONTAL_VARIANT_ID],
        variants=[
            EnabledFinalOutputVariant(
                id=HORIZONTAL_VARIANT_ID,
                enabled=True,
                required=True,
                width=1920,
                height=1080,
                fps=30,
                frame_start=1,
                frame_end=13029,
                composition_profile_id=(
                    validated.horizontal_profile.output_variant.composition_profile_id
                ),
                camera_name=validated.horizontal_profile.output_variant.camera_name,
                scene_sha256=artifacts["final-scene"].sha256,
                render_profile_sha256=artifacts[
                    "horizontal-render-profile"
                ].sha256,
                worker_requirement_id=validated.worker.id,
                output_pattern=request.horizontal_output_pattern,
            )
        ],
    )
    identity = FinalReleaseIdentity(
        schema_version="2.0.0",
        release_id="andromeda-v2-final-release-v2",
        project_id="trip-to-andromeda-v2",
        source_audio_sha256=SOURCE_AUDIO_SHA256,
        source_cue_sha256=SOURCE_CUE_SHA256,
        owner_creative_acceptance_sha256=owner_sha256,
        encoding_profiles_sha256=encoding_sha256,
        look_profile_sha256=artifacts["final-look-profile"].sha256,
        story_plan_sha256=artifacts["story-plan"].sha256,
        shot_plan_sha256=artifacts["shot-plan"].sha256,
        output_variant_contract_sha256=artifacts["output-variants"].sha256,
        builder_source_sha256=artifacts["builder-source"].sha256,
        source_tree_sha256=request.source_tree_sha256,
        git_commit_sha=request.implementation_commit_sha,
        deterministic_seed=ANDROMEDA_V2_SEED,
        output_matrix=output_matrix,
        worker_requirements=[validated.worker],
    )
    identity_sha256 = final_release_identity_sha256(identity)
    if identity_sha256 == request.supersedes_release_identity_sha256:
        raise ReleaseFinalizationError(
            "fresh finalization produced the superseded release identity"
        )

    derived = validated.derived_calibration
    calibration = AndromedaV2FinalCalibration(
        schema_version="2.0.0",
        kind="trackprompt-final-render-calibration",
        calibration_id="andromeda-v2-final-calibration-v2",
        identity=identity,
        release_identity_sha256=identity_sha256,
        variant_calibrations=[
            FinalVariantCalibration(
                output_variant_id=HORIZONTAL_VARIANT_ID,
                scene_sha256=artifacts["final-scene"].sha256,
                render_profile_sha256=artifacts[
                    "horizontal-render-profile"
                ].sha256,
                composition_profile_id=(
                    validated.horizontal_profile.output_variant.composition_profile_id
                ),
                camera_name=validated.horizontal_profile.output_variant.camera_name,
                worker_requirement_id=validated.worker.id,
                width=1920,
                height=1080,
                fps=30,
                sample_frames=list(derived.sample_frames),
                p50_seconds_per_frame=derived.p50_seconds_per_frame,
                p90_seconds_per_frame=derived.p90_seconds_per_frame,
                weighted_seconds_per_frame=derived.weighted_seconds_per_frame,
                projected_render_seconds_p50=(
                    derived.projected_render_seconds_p50
                ),
                projected_render_seconds_p90=derived.projected_render_seconds_p90,
                projected_output_bytes=(
                    validated.calibration_evidence.projected_output_bytes
                ),
                calibration_complete=True,
            )
        ],
        stage_forecasts=list(derived.canonical_stage_forecasts),
        aggregate_p50_seconds=derived.aggregate_p50_seconds,
        aggregate_p90_seconds=derived.aggregate_p90_seconds,
        sla_limit_seconds=86_400,
        sla_satisfied=derived.aggregate_p90_seconds <= 86_400,
        deterministic_effects_verified=True,
        all_required_bakes_complete=True,
        dependency_health_passed=True,
        vram_stable=True,
        disk_free_bytes=validated.hardware.storage.free_bytes,
        projected_peak_disk_bytes=(
            validated.hardware.storage.projected_peak_disk_bytes
        ),
        disk_safety_multiplier=validated.hardware.storage.safety_multiplier,
        disk_headroom_satisfied=True,
    )
    calibration_bytes = _serialized_json(calibration)
    calibration_sha256 = _bytes_sha256(calibration_bytes)

    gates = [
        FinalReleaseObjectiveGate(
            id=gate_id,
            status="satisfied",
            evidence_artifact_roles=list(_OBJECTIVE_GATE_EVIDENCE[gate_id]),
            summary=_OBJECTIVE_GATE_SUMMARIES[gate_id],
            evidence_kind="objective-technical-evidence",
        )
        for gate_id in FinalReleaseObjectiveGateId
    ]
    technical_authorization = AndromedaV2TechnicalAuthorization(
        schema_version="2.0.0",
        kind="trackprompt-final-technical-authorization",
        authorization_id="andromeda-v2-technical-authorization-v2",
        package_id="andromeda-v2-final-render-package-v2",
        calibration_id="andromeda-v2-final-calibration-v2",
        calibration_sha256=calibration_sha256,
        identity=identity,
        release_identity_sha256=identity_sha256,
        status="technically-ready",
        technical_ready=True,
        objective_gates=gates,
        operator_start_gate=ExplicitOperatorStartGate(
            status="not-authorized",
            authorization_id=None,
            authorized_by_role=None,
            authorized_at=None,
            authorized_release_identity_sha256=None,
            explicit_full_render_start_authorized=False,
        ),
        production_start_allowed=False,
        final_render_started=False,
        codex_human_artistic_approval=False,
        human_artistic_judgment_source=(
            "owner-attested-r13.1-baseline-not-codex"
        ),
    )
    technical_bytes = _serialized_json(technical_authorization)
    technical_sha256 = _bytes_sha256(technical_bytes)
    report_payload = _build_release_report(
        request,
        root=root,
        artifacts=artifacts,
        identity=identity,
        validated=validated,
        calibration_path=calibration_path,
        calibration_sha256=calibration_sha256,
        technical_path=technical_path,
        technical_sha256=technical_sha256,
        package_path=package_path,
        report_path=report_path,
    )
    report_bytes = _serialized_json(report_payload)
    report_sha256 = _bytes_sha256(report_bytes)

    package_artifacts = [
        _package_artifact(
            root,
            role,
            paths[role],
            artifacts[role].sha256,
        )
        for role in sorted(artifacts)
    ]
    package_artifacts.extend(
        [
            _package_artifact(
                root,
                "final-calibration-v2",
                calibration_path,
                calibration_sha256,
            ),
            _package_artifact(
                root,
                "technical-authorization-v2",
                technical_path,
                technical_sha256,
            ),
            _package_artifact(
                root,
                "release-report",
                report_path,
                report_sha256,
            ),
        ]
    )
    package_manifest = AndromedaV2FinalPackageManifest(
        schema_version="2.0.0",
        kind="trackprompt-final-render-package",
        package_id="andromeda-v2-final-render-package-v2",
        project_id="trip-to-andromeda-v2",
        identity=identity,
        release_identity_sha256=identity_sha256,
        calibration_id="andromeda-v2-final-calibration-v2",
        calibration_sha256=calibration_sha256,
        technical_authorization_id=(
            "andromeda-v2-technical-authorization-v2"
        ),
        status="technically-ready-operator-start-blocked",
        technical_ready=True,
        artifacts=package_artifacts,
        source_bindings=[
            PrivateSourceBinding.model_validate(
                binding.model_dump(mode="json", by_alias=True)
            )
            for binding in request.source_bindings
        ],
        production_start_allowed=False,
        codex_human_artistic_approval=False,
    )
    package_bytes = _serialized_json(package_manifest)
    package_sha256 = _bytes_sha256(package_bytes)

    # Reparse every generated payload before touching the release destination.
    AndromedaV2FinalCalibration.model_validate_json(calibration_bytes)
    AndromedaV2TechnicalAuthorization.model_validate_json(technical_bytes)
    FinalReleaseReport.model_validate_json(report_bytes)
    AndromedaV2FinalPackageManifest.model_validate_json(package_bytes)

    staging_root = _new_staging_directory(output_root, overwrite=overwrite)
    staged_calibration = staging_root / "v2-calibration.json"
    staged_technical = staging_root / "technical-authorization-v2.json"
    staged_report = staging_root / "evidence" / "release-report.json"
    staged_package = staging_root / "package-manifest-v2.json"
    published = False
    publication_validated = False
    try:
        _write_bytes(staged_calibration, calibration_bytes, overwrite=False)
        _write_bytes(staged_technical, technical_bytes, overwrite=False)
        _write_bytes(staged_report, report_bytes, overwrite=False)
        _write_bytes(staged_package, package_bytes, overwrite=False)
        _publish_staged_directory(staging_root, output_root)
        published = True
        loaded = load_and_validate_final_release(
            calibration_path,
            package_path,
            technical_path,
            repository_root=root,
        )
        loaded_report = FinalReleaseReport.model_validate_json(
            report_path.read_bytes()
        )
        if loaded_report.release_identity_sha256 != identity_sha256:
            raise ReleaseFinalizationError(
                "generated release report does not bind the final identity"
            )
        generated_hashes = {
            calibration_path: calibration_sha256,
            technical_path: technical_sha256,
            report_path: report_sha256,
            package_path: package_sha256,
        }
        for generated_path, expected_sha256 in generated_hashes.items():
            if file_sha256(generated_path) != expected_sha256:
                raise ReleaseFinalizationError(
                    f"published output hash mismatch: {generated_path.name}"
                )
        if (
            loaded.technical_authorization.operator_start_gate.status
            != "not-authorized"
            or loaded.technical_authorization.production_start_allowed
            or loaded.technical_authorization.final_render_started
        ):
            raise ReleaseFinalizationError(
                "generated release crossed the operator-controlled "
                "production boundary"
            )
        publication_validated = True
    except (OSError, ValueError) as exc:
        raise ReleaseFinalizationError(
            f"generated final release failed filesystem validation: {exc}"
        ) from exc
    finally:
        if published and not publication_validated:
            shutil.rmtree(output_root, ignore_errors=True)
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
    return ReleaseFinalizationResult(
        ok=True,
        release_identity_sha256=identity_sha256,
        matrix_id=output_matrix.matrix_id,
        enabled_variant_ids=[HORIZONTAL_VARIANT_ID],
        files=FinalizationPaths(
            calibration=_relative_posix(root, calibration_path),
            package_manifest=_relative_posix(root, package_path),
            technical_authorization=_relative_posix(root, technical_path),
            release_report=_relative_posix(root, report_path),
        ),
        sha256=FinalizationHashes(
            calibration=calibration_sha256,
            package_manifest=package_sha256,
            technical_authorization=technical_sha256,
            release_report=report_sha256,
        ),
        technical_ready=True,
        operator_start_gate="not-authorized",
        production_start_allowed=False,
        final_render_started=False,
        external_processes_started=False,
    )

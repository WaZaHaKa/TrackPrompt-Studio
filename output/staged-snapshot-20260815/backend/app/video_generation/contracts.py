from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .jsonio import read_json, sha256_json

SCHEMA_VERSION = "1.0.0"
SUPPORTED_DURATIONS = {4, 6, 8}
SUPPORTED_ASPECT_RATIOS = {"16:9", "9:16"}
SUPPORTED_RESOLUTIONS = {"720p", "1080p", "4k"}
SUPPORTED_PERSON_GENERATION = {"allow_adult", "disallow"}


class ContractError(ValueError):
    """Raised when an input artifact violates a fast-lane contract."""


class ShotStatus(StrEnum):
    PLANNED = "planned"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FILTERED = "filtered"
    FAILED = "failed"
    DOWNLOADED = "downloaded"
    VERIFIED = "verified"
    REJECTED = "rejected"


@dataclass(frozen=True)
class GenerationProfile:
    profile_id: str
    model_id: str
    resolution: str
    aspect_ratio: str = "16:9"
    fps: int = 24
    duration_seconds: int = 8
    sample_count: int = 1
    generate_audio: bool = False
    enhance_prompt: bool = True
    compression_quality: str = "optimized"
    person_generation: str = "allow_adult"

    def validate(self) -> None:
        if not self.profile_id.strip():
            raise ContractError("profileId must not be empty")
        if not self.model_id.startswith("veo-"):
            raise ContractError("modelId must be a Veo model ID")
        if self.resolution not in SUPPORTED_RESOLUTIONS:
            raise ContractError(f"unsupported resolution: {self.resolution}")
        if self.aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
            raise ContractError(f"unsupported aspect ratio: {self.aspect_ratio}")
        if self.duration_seconds not in SUPPORTED_DURATIONS:
            raise ContractError("durationSeconds must be 4, 6, or 8")
        if self.fps != 24:
            raise ContractError("Veo 3.1 output is fixed at 24 FPS")
        if not 1 <= self.sample_count <= 4:
            raise ContractError("sampleCount must be between 1 and 4")
        if self.person_generation not in SUPPORTED_PERSON_GENERATION:
            raise ContractError("personGeneration must be allow_adult or disallow")
        if self.generate_audio:
            raise ContractError(
                "This music-video fast lane requires generateAudio=false; the final track is muxed locally"
            )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GenerationProfile:
        profile = cls(
            profile_id=str(value["profileId"]),
            model_id=str(value["modelId"]),
            resolution=str(value["resolution"]).lower(),
            aspect_ratio=str(value.get("aspectRatio", "16:9")),
            fps=int(value.get("fps", 24)),
            duration_seconds=int(value.get("durationSeconds", 8)),
            sample_count=int(value.get("sampleCount", 1)),
            generate_audio=bool(value.get("generateAudio", False)),
            enhance_prompt=bool(value.get("enhancePrompt", True)),
            compression_quality=str(value.get("compressionQuality", "optimized")),
            person_generation=str(value.get("personGeneration", "allow_adult")),
        )
        profile.validate()
        return profile

    def to_dict(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "modelId": self.model_id,
            "resolution": self.resolution,
            "aspectRatio": self.aspect_ratio,
            "fps": self.fps,
            "durationSeconds": self.duration_seconds,
            "sampleCount": self.sample_count,
            "generateAudio": self.generate_audio,
            "enhancePrompt": self.enhance_prompt,
            "compressionQuality": self.compression_quality,
            "personGeneration": self.person_generation,
        }


@dataclass(frozen=True)
class ShotSpec:
    shot_id: str
    chapter_id: str
    order: int
    title: str
    narrative_intent: str
    prompt: str
    negative_prompt: str
    source_section_hints: tuple[str, ...]
    energy_range: tuple[float, float]
    seed: int
    required: bool = True
    reuse_modes: tuple[str, ...] = ("forward", "alternate-inpoint")
    continuity_tokens: tuple[str, ...] = ()
    review_notes: tuple[str, ...] = ()
    continuity_group_ids: tuple[str, ...] = ()
    previous_shot_id: str | None = None
    continuation_mode: str = "prompt-anchors"

    def validate(self) -> None:
        if not self.shot_id or not self.chapter_id:
            raise ContractError("shotId and chapterId must not be empty")
        if self.order < 1:
            raise ContractError(f"{self.shot_id}: order must be positive")
        if len(self.prompt.strip()) < 40:
            raise ContractError(f"{self.shot_id}: prompt is too short")
        if len(self.negative_prompt.strip()) < 10:
            raise ContractError(f"{self.shot_id}: negativePrompt is too short")
        low, high = self.energy_range
        if not 0 <= low <= high <= 1:
            raise ContractError(f"{self.shot_id}: energyRange must be within 0..1")
        if not 0 <= self.seed <= 4_294_967_295:
            raise ContractError(f"{self.shot_id}: seed is outside uint32")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ShotSpec:
        shot = cls(
            shot_id=str(value["shotId"]),
            chapter_id=str(value["chapterId"]),
            order=int(value["order"]),
            title=str(value["title"]),
            narrative_intent=str(value["narrativeIntent"]),
            prompt=str(value["prompt"]),
            negative_prompt=str(value["negativePrompt"]),
            source_section_hints=tuple(str(item) for item in value.get("sourceSectionHints", [])),
            energy_range=(
                float(value.get("energyRange", [0, 1])[0]),
                float(value.get("energyRange", [0, 1])[1]),
            ),
            seed=int(value["seed"]),
            required=bool(value.get("required", True)),
            reuse_modes=tuple(
                str(item) for item in value.get("reuseModes", ["forward", "alternate-inpoint"])
            ),
            continuity_tokens=tuple(str(item) for item in value.get("continuityTokens", [])),
            review_notes=tuple(str(item) for item in value.get("reviewNotes", [])),
            continuity_group_ids=tuple(str(item) for item in value.get("continuityGroupIds", [])),
            previous_shot_id=(str(value["previousShotId"]) if value.get("previousShotId") else None),
            continuation_mode=str(value.get("continuationMode", "prompt-anchors")),
        )
        shot.validate()
        return shot

    def to_dict(self) -> dict[str, Any]:
        return {
            "shotId": self.shot_id,
            "chapterId": self.chapter_id,
            "order": self.order,
            "title": self.title,
            "narrativeIntent": self.narrative_intent,
            "prompt": self.prompt,
            "negativePrompt": self.negative_prompt,
            "sourceSectionHints": list(self.source_section_hints),
            "energyRange": list(self.energy_range),
            "seed": self.seed,
            "required": self.required,
            "reuseModes": list(self.reuse_modes),
            "continuityTokens": list(self.continuity_tokens),
            "reviewNotes": list(self.review_notes),
            "continuityGroupIds": list(self.continuity_group_ids),
            "previousShotId": self.previous_shot_id,
            "continuationMode": self.continuation_mode,
        }


@dataclass(frozen=True)
class CreativeBible:
    project_id: str
    title: str
    visual_identity: str
    protagonist: str
    palette: tuple[str, ...]
    camera_language: tuple[str, ...]
    lighting_language: tuple[str, ...]
    texture_language: tuple[str, ...]
    global_negative_prompt: str
    privacy_rules: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CreativeBible:
        bible = cls(
            project_id=str(value["projectId"]),
            title=str(value["title"]),
            visual_identity=str(value["visualIdentity"]),
            protagonist=str(value["protagonist"]),
            palette=tuple(str(item) for item in value.get("palette", [])),
            camera_language=tuple(str(item) for item in value.get("cameraLanguage", [])),
            lighting_language=tuple(str(item) for item in value.get("lightingLanguage", [])),
            texture_language=tuple(str(item) for item in value.get("textureLanguage", [])),
            global_negative_prompt=str(value["globalNegativePrompt"]),
            privacy_rules=tuple(str(item) for item in value.get("privacyRules", [])),
        )
        if not bible.project_id or not bible.visual_identity:
            raise ContractError("creative bible is missing identity fields")
        return bible


@dataclass(frozen=True)
class ProjectConfig:
    schema_version: str
    project_id: str
    title: str
    selected_profile_id: str
    generation_profiles: tuple[GenerationProfile, ...]
    max_spend_usd: float
    retry_reserve_factor: float
    required_shot_ids: tuple[str, ...]
    storage_prefix: str
    local_output_root: str
    timeline: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProjectConfig:
        if value.get("schemaVersion") != SCHEMA_VERSION:
            raise ContractError(f"project schemaVersion must be {SCHEMA_VERSION}")
        profiles = tuple(GenerationProfile.from_dict(item) for item in value.get("generationProfiles", []))
        profile_ids = {profile.profile_id for profile in profiles}
        selected = str(value["selectedProfileId"])
        if selected not in profile_ids:
            raise ContractError(f"selectedProfileId {selected!r} is not declared")
        config = cls(
            schema_version=SCHEMA_VERSION,
            project_id=str(value["projectId"]),
            title=str(value["title"]),
            selected_profile_id=selected,
            generation_profiles=profiles,
            max_spend_usd=float(value["maxSpendUsd"]),
            retry_reserve_factor=float(value.get("retryReserveFactor", 1.5)),
            required_shot_ids=tuple(str(item) for item in value.get("requiredShotIds", [])),
            storage_prefix=str(value.get("storagePrefix", "video-generation")),
            local_output_root=str(
                value.get(
                    "localOutputRoot",
                    ".trackprompt-data/video-generation",
                )
            ),
            timeline=dict(value.get("timeline", {})),
        )
        if config.max_spend_usd <= 0:
            raise ContractError("maxSpendUsd must be positive")
        if config.retry_reserve_factor < 1:
            raise ContractError("retryReserveFactor must be at least 1")
        if not config.required_shot_ids:
            raise ContractError("requiredShotIds must not be empty")
        return config

    def selected_profile(self) -> GenerationProfile:
        for profile in self.generation_profiles:
            if profile.profile_id == self.selected_profile_id:
                return profile
        raise ContractError("selected generation profile disappeared")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "projectId": self.project_id,
            "title": self.title,
            "selectedProfileId": self.selected_profile_id,
            "generationProfiles": [profile.to_dict() for profile in self.generation_profiles],
            "maxSpendUsd": self.max_spend_usd,
            "retryReserveFactor": self.retry_reserve_factor,
            "requiredShotIds": list(self.required_shot_ids),
            "storagePrefix": self.storage_prefix,
            "localOutputRoot": self.local_output_root,
            "timeline": self.timeline,
        }


@dataclass(frozen=True)
class CompiledReferenceImage:
    asset_id: str
    gcs_uri: str
    mime_type: str
    sha256: str
    source_kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assetId": self.asset_id,
            "gcsUri": self.gcs_uri,
            "mimeType": self.mime_type,
            "sha256": self.sha256,
            "sourceKind": self.source_kind,
        }


@dataclass(frozen=True)
class CompiledShot:
    shot_id: str
    chapter_id: str
    order: int
    title: str
    duration_seconds: int
    prompt: str
    negative_prompt: str
    seed: int
    model_id: str
    resolution: str
    aspect_ratio: str
    sample_count: int
    generate_audio: bool
    enhance_prompt: bool
    compression_quality: str
    person_generation: str
    storage_uri: str | None
    required: bool
    estimated_cost_usd: float
    source_section_hints: tuple[str, ...]
    review_notes: tuple[str, ...]
    variation_index: int = 0
    continuity_group_ids: tuple[str, ...] = ()
    previous_shot_id: str | None = None
    continuation_mode: str = "prompt-anchors"
    first_frame_reference: CompiledReferenceImage | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "shotId": self.shot_id,
            "chapterId": self.chapter_id,
            "order": self.order,
            "title": self.title,
            "durationSeconds": self.duration_seconds,
            "prompt": self.prompt,
            "negativePrompt": self.negative_prompt,
            "seed": self.seed,
            "modelId": self.model_id,
            "resolution": self.resolution,
            "aspectRatio": self.aspect_ratio,
            "sampleCount": self.sample_count,
            "generateAudio": self.generate_audio,
            "enhancePrompt": self.enhance_prompt,
            "compressionQuality": self.compression_quality,
            "personGeneration": self.person_generation,
            "storageUri": self.storage_uri,
            "required": self.required,
            "estimatedCostUsd": round(self.estimated_cost_usd, 4),
            "sourceSectionHints": list(self.source_section_hints),
            "reviewNotes": list(self.review_notes),
            "variationIndex": self.variation_index,
            "continuityGroupIds": list(self.continuity_group_ids),
            "previousShotId": self.previous_shot_id,
            "continuationMode": self.continuation_mode,
            "firstFrameReference": (
                self.first_frame_reference.to_dict() if self.first_frame_reference else None
            ),
        }


@dataclass(frozen=True)
class CompiledPlan:
    schema_version: str
    project_id: str
    title: str
    profile: GenerationProfile
    shots: tuple[CompiledShot, ...]
    base_estimated_cost_usd: float
    conservative_estimated_cost_usd: float
    max_spend_usd: float
    source_artifacts: dict[str, str]
    analysis_job_id: str | None = None
    pricing_snapshot_date: str = ""
    rate_usd_per_output_second: float = 0.0
    request_contract_version: str = "vertex-veo-predict-long-running-v2"
    continuity: dict[str, Any] = field(default_factory=dict)
    plan_digest: str = ""

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "projectId": self.project_id,
            "title": self.title,
            "analysisJobId": self.analysis_job_id,
            "profile": self.profile.to_dict(),
            "shots": [shot.to_dict() for shot in self.shots],
            "cost": {
                "baseEstimatedUsd": round(self.base_estimated_cost_usd, 4),
                "conservativeEstimatedUsd": round(self.conservative_estimated_cost_usd, 4),
                "maxSpendUsd": round(self.max_spend_usd, 2),
                "pricingSnapshotDate": self.pricing_snapshot_date,
                "rateUsdPerOutputSecond": round(self.rate_usd_per_output_second, 4),
            },
            "sourceArtifacts": dict(sorted(self.source_artifacts.items())),
            "requestContractVersion": self.request_contract_version,
            "continuity": self.continuity,
        }

    def with_digest(self) -> CompiledPlan:
        digest = sha256_json(self.unsigned_dict())
        return CompiledPlan(
            **{**asdict(self), "profile": self.profile, "shots": self.shots, "plan_digest": digest}
        )

    def to_dict(self) -> dict[str, Any]:
        value = self.unsigned_dict()
        value["planDigest"] = self.plan_digest or sha256_json(value)
        return value


def load_project_config(path: Path) -> ProjectConfig:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ContractError("project config root must be an object")
    return ProjectConfig.from_dict(value)


def load_creative_bible(path: Path) -> CreativeBible:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ContractError("creative bible root must be an object")
    return CreativeBible.from_dict(value)


def load_shot_bank(path: Path) -> tuple[ShotSpec, ...]:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise ContractError(f"shot bank schemaVersion must be {SCHEMA_VERSION}")
    shots = tuple(ShotSpec.from_dict(item) for item in value.get("shots", []))
    if not shots:
        raise ContractError("shot bank contains no shots")
    ids = [shot.shot_id for shot in shots]
    if len(ids) != len(set(ids)):
        raise ContractError("shot IDs must be unique")
    orders = [shot.order for shot in shots]
    if len(orders) != len(set(orders)):
        raise ContractError("shot orders must be unique")
    by_id = {shot.shot_id: shot for shot in shots}
    for shot in shots:
        if shot.previous_shot_id is not None:
            previous = by_id.get(shot.previous_shot_id)
            if previous is None or previous.order >= shot.order:
                raise ContractError(f"{shot.shot_id}: previousShotId must reference an earlier shot")
    return tuple(sorted(shots, key=lambda item: item.order))


def require_shots(shots: Iterable[ShotSpec], required_ids: Iterable[str]) -> tuple[ShotSpec, ...]:
    by_id = {shot.shot_id: shot for shot in shots}
    missing = sorted(set(required_ids) - set(by_id))
    if missing:
        raise ContractError(f"required shots are missing: {', '.join(missing)}")
    return tuple(by_id[shot_id] for shot_id in required_ids)

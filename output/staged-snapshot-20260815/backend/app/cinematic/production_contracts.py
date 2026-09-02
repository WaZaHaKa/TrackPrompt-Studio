from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from ..schemas import APIModel

ANDROMEDA_V2_PROJECT_ID = "trip-to-andromeda-v2"
ANDROMEDA_V2_SEED = 84291
ANDROMEDA_V2_FRAME_START = 1
ANDROMEDA_V2_FRAME_END = 13029
ANDROMEDA_V2_FPS = 30.0
ANDROMEDA_V2_SHOT_COUNT = 35

R131_PROOF_MANIFEST_SHA256 = "acb896e9d445e2d0f7a187c3667dc22b01e91d9056644b510861e8b555685140"
R131_SCENE_SHA256 = "9b58fe8e3a98c8b6a7b4e7a2bdb4ead72b1fc7e7de1d3cc7602e6fe623f4f1a6"
R131_RENDER_MANIFEST_SHA256 = "13712629fa6cce15f5ae2b764f476023777e30ced55a94df48e076c392e3a086"
R131_MOTION_PREVIEW_SHA256 = "1b1a02a52c241552e0b4a6c3b3490a04168fdd954b19f17d47e90332997a72c0"
R131_REVIEW_SHA256 = "8843cecbe55ff6a6a7bb1f69da36b1305e8632504ea2e56f9bb98b72c9136d3e"

SOURCE_AUDIO_SHA256 = "6adf4f3e75f1f775226571ace56883b6e72ad11775bde6c94adc1b95112e5cd5"
SOURCE_CUE_SHA256 = "b58ba759feb44aa869391ade40e72b8450d0e2917e40255ccf60af2e0205c1b2"
OWNER_CREATIVE_ACCEPTANCE_SHA256 = (
    "023173db958ae7627757e7799d55caf427342d7a9b7da73af82fd0a1de2ded48"
)
ENCODING_PROFILES_SHA256 = (
    "6e312848fde7e06f734d2c5e19c31be18e0dddaedf6403ea7be65d0f58f0fab8"
)

_HEX_64 = r"^[0-9a-f]{64}$"
_ACT_ORDER = (
    "signal",
    "awakening",
    "departure",
    "gates",
    "rupture",
    "transformation",
    "arrival",
)
_FINAL_ACTS = frozenset({"rupture", "transformation", "arrival"})
_CREATIVE_ACCEPTANCE_EXCLUSIONS = (
    "skipping technical QA",
    "using stale scene or render profiles",
    "cloud provisioning",
    "starting the full render before the final technical gates",
)
_MODEL = TypeVar("_MODEL", bound=APIModel)


def canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_repository_path(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("artifact paths must be non-empty normalized repository paths")
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != value
        or candidate == PurePosixPath(".")
        or ".." in candidate.parts
        or "\\" in value
        or (candidate.parts and ":" in candidate.parts[0])
    ):
        raise ValueError("artifact paths must be safe repository-relative POSIX paths")
    return value


class ImmutableR131Proof(APIModel):
    revision_id: Literal["andromeda-r13.1-selected-refinement"]
    proof_root: Literal["test-output/cinematic-v2-andromeda-r131-20260722-213840"]
    proof_manifest_sha256: str = Field(pattern=_HEX_64)
    authoritative_scene_sha256: str = Field(pattern=_HEX_64)
    render_manifest_sha256: str = Field(pattern=_HEX_64)
    motion_preview_sha256: str = Field(pattern=_HEX_64)
    historical_review_sha256: str = Field(pattern=_HEX_64)
    historical_proof_remains_immutable: Literal[True]

    @model_validator(mode="after")
    def exact_historical_proof(self) -> ImmutableR131Proof:
        expected = (
            R131_PROOF_MANIFEST_SHA256,
            R131_SCENE_SHA256,
            R131_RENDER_MANIFEST_SHA256,
            R131_MOTION_PREVIEW_SHA256,
            R131_REVIEW_SHA256,
        )
        actual = (
            self.proof_manifest_sha256,
            self.authoritative_scene_sha256,
            self.render_manifest_sha256,
            self.motion_preview_sha256,
            self.historical_review_sha256,
        )
        if actual != expected:
            raise ValueError("the immutable R13.1 proof hashes may not drift")
        return self


class OwnerAttestedCreativeAcceptance(APIModel):
    schema_version: Literal["1.0.0"]
    kind: Literal["trackprompt-project-creative-acceptance"]
    approval_kind: Literal["operator-confirmed-audience-avatar-acceptance"]
    acceptance_id: Literal["andromeda-r13.1-owner-creative-acceptance-v1"]
    project_id: Literal["trip-to-andromeda-v2"]
    accepted_baseline: Literal["andromeda-r13.1-selected-refinement"]
    revision: Literal["andromeda-r13.1-selected-refinement"]
    attestation_kind: Literal["owner-attested"]
    attested_by_role: Literal["project-owner-operator"]
    attested_at: Literal["2026-07-23T00:00:00+03:00"]
    decision: Literal["approved-as-project-creative-baseline"]
    human_approval: Literal["approved"]
    human_approval_source: Literal["operator-attested-audience-avatar-review"]
    operator_creative_direction_approved: Literal[True]
    style_lock_status: Literal["approved-for-this-project"]
    scope: Literal["overall visual and motion quality target for Trip to Andromeda V2"]
    source: Literal["andromeda-finish-line-codex-sprint-dual-format"]
    attestation_source: Literal["user-supplied-finish-line-brief"]
    recorded_by: Literal["codex-recording-user-supplied-attestation"]
    proof: ImmutableR131Proof
    does_not_rewrite_historical_review: Literal[True]
    does_not_by_itself_authorize: list[str] = Field(min_length=4, max_length=4)
    does_not_authorize_production: Literal[True]

    @model_validator(mode="after")
    def exact_acceptance_scope(self) -> OwnerAttestedCreativeAcceptance:
        if tuple(self.does_not_by_itself_authorize) != _CREATIVE_ACCEPTANCE_EXCLUSIONS:
            raise ValueError("creative acceptance technical exclusions may not drift")
        return self


class SelectedLookLanguage(APIModel):
    protagonist_design: Literal["protagonist-b-ancient-engine"]
    architectural_material_language: Literal["weathered-stone-metal-crystal-v1"]
    gate_construction: Literal["nested-ring-monolith-v1"]
    exposure_lighting_treatment: Literal["restrained-teal-cyan-amber-v1"]


class ProtagonistLook(APIModel):
    silhouette_reference: Literal["protagonist-a-directional-shell"]
    identity: Literal["protagonist-b-ancient-engine"]
    front_back_orientation: Literal["clear-directional-front-and-back"]
    major_armor_bands: Literal[1]
    transparent_atmosphere_layers: Literal[1]
    integrated_front_aperture: Literal[True]
    asymmetric_orientation_cues: Literal[True]
    restrained_rear_wake: Literal[True]
    wire_cage: Literal[False]
    hud_overlay: Literal[False]
    bounded_compression: Literal[True]


class ArchitectureLook(APIModel):
    connected_supports: Literal[True]
    rails_and_conduits: Literal[True]
    repeated_structural_rhythm: Literal[True]
    functional_crystal_routing: Literal[True]
    floating_panel_field: Literal[False]
    procedural_blockout_accepted_as_final: Literal[False]


class GateLook(APIModel):
    outer_monolith: Literal[True]
    moving_lock_rings: Literal[True]
    localized_membrane: Literal[True]
    destination_depth: Literal[True]
    closing_mechanism: Literal[True]
    uniform_cyan_wash: Literal[False]
    localized_deformation_only: Literal[True]
    localized_membrane_count: Literal[1]


class LightingLook(APIModel):
    palette: Literal["restrained-teal-cyan-amber-v1"]
    exposure_policy: Literal["subject-first-highlight-protected"]
    contrast: Literal["agx-medium-high-contrast"]
    unmotivated_full_frame_wash: Literal[False]
    featureless_white_wash: Literal[False]


class MaterialHierarchyLook(APIModel):
    primary_structure: Literal["weathered-stone"]
    secondary_structure: Literal["aged-metal"]
    signal_material: Literal["functional-crystal"]
    hierarchy_is_coherent: Literal[True]


class MotionLook(APIModel):
    protagonist_movement: Literal["independent-authored"]
    camera_lag: Literal["authored"]
    foreground_parallax: Literal["authored"]
    localized_deformation: Literal[True]
    raw_audio_controls_major_camera_travel: Literal[False]
    raw_audio_controls_major_protagonist_travel: Literal[False]


class TransparencyLook(APIModel):
    blend_mode: Literal["DITHERED"]
    maximum_layers: Literal[2]
    localized_gate_membranes: Literal[1]
    frame_filling_transparent_volume: Literal[False]


class RenderBaseline(APIModel):
    blender_version: Literal["5.2"]
    render_engine: Literal["BLENDER_EEVEE"]
    temporal_samples: Literal[64]
    volumetric_samples: Literal[32]
    temporal_antialiasing: Literal[True]
    temporal_reprojection: Literal[True]
    color_management: Literal["AgX Medium High Contrast"]
    transparency_mode: Literal["DITHERED"]
    motion_blur: Literal[False]
    maximum_transparent_layers: Literal[2]
    localized_gate_membranes: Literal[1]
    compositor_denoising: Literal[False]


class ControlledShotVariance(APIModel):
    shared_invariants: list[
        Literal[
            "renderer-and-color-management",
            "material-and-lighting-grammar",
            "motion-constraints",
            "protagonist-architecture-and-gate-systems",
        ]
    ] = Field(min_length=4, max_length=4)
    authored_per_shot_or_variant: list[
        Literal[
            "lens-and-camera-distance",
            "composition-and-palette-emphasis",
            "subject-occupancy-and-foreground-placement",
            "narrative-action",
        ]
    ] = Field(min_length=4, max_length=4)


class FinalLookProfile(APIModel):
    schema_version: Literal["1.0.0"]
    profile_id: Literal["andromeda-r13.1-final-look-v1"]
    project_id: Literal["trip-to-andromeda-v2"]
    source_acceptance_id: Literal["andromeda-r13.1-owner-creative-acceptance-v1"]
    source_revision: Literal["andromeda-r13.1-selected-refinement"]
    status: Literal["locked-for-story-production"]
    locked: Literal[True]
    preview_only: Literal[False]
    aspect_neutral: Literal[True]
    composition_policy: Literal["native-authored-per-output-variant"]
    selected_language: SelectedLookLanguage
    protagonist: ProtagonistLook
    architecture: ArchitectureLook
    gate: GateLook
    lighting: LightingLook
    material_hierarchy: MaterialHierarchyLook
    motion: MotionLook
    transparency: TransparencyLook
    render_baseline: RenderBaseline
    controlled_shot_variance: ControlledShotVariance
    production_authorization: Literal[False]


class NormalizedSafeZone(APIModel):
    x_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def ordered_bounds(self) -> NormalizedSafeZone:
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("safe-zone bounds must be ordered")
        return self


class OutputVariant(APIModel):
    id: Literal["horizontal-16x9-1080p", "vertical-9x16-1080p"]
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    fps: Literal[30]
    required: bool
    enabled_by_default: bool
    composition_mode: Literal["authored"]
    deliverable_role: Literal["primary-master", "optional-social"]
    composition_profile_id: str = Field(min_length=1, max_length=100)
    camera_name: str = Field(pattern=r"^TP_ANDROMEDA_V2_CAMERA_[A-Z0-9_]+$")
    safe_zone: NormalizedSafeZone
    occlusion_policy_id: Literal["andromeda-subject-landmark-protection-v1"]
    crop_policy: Literal["native-authored-never-crop"]


class OutputVariantSet(APIModel):
    schema_version: Literal["1.0.0"]
    project_id: Literal["trip-to-andromeda-v2"]
    variants: list[OutputVariant] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def exact_default_matrix(self) -> OutputVariantSet:
        by_id = {variant.id: variant for variant in self.variants}
        if set(by_id) != {"horizontal-16x9-1080p", "vertical-9x16-1080p"}:
            raise ValueError("the output contract requires exactly horizontal and vertical variants")
        horizontal = by_id["horizontal-16x9-1080p"]
        vertical = by_id["vertical-9x16-1080p"]
        if (
            (horizontal.width, horizontal.height) != (1920, 1080)
            or not horizontal.required
            or not horizontal.enabled_by_default
            or horizontal.deliverable_role != "primary-master"
        ):
            raise ValueError("horizontal 1080p must be required and enabled")
        if (
            (vertical.width, vertical.height) != (1080, 1920)
            or vertical.required
            or vertical.enabled_by_default
            or vertical.deliverable_role != "optional-social"
        ):
            raise ValueError("vertical 1080p must be optional and disabled by default")
        if horizontal.composition_profile_id == vertical.composition_profile_id:
            raise ValueError("output variants require independent authored compositions")
        return self


class StoryBeatV2(APIModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    frame: int = Field(ge=ANDROMEDA_V2_FRAME_START, le=ANDROMEDA_V2_FRAME_END)
    purpose: str = Field(min_length=1, max_length=240)


class StoryActV2(APIModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    name: str = Field(min_length=1, max_length=64)
    frame_start: int = Field(ge=ANDROMEDA_V2_FRAME_START)
    frame_end: int = Field(le=ANDROMEDA_V2_FRAME_END)
    purpose: str = Field(min_length=1, max_length=300)
    protagonist_state_start: str = Field(min_length=1, max_length=64)
    protagonist_state_end: str = Field(min_length=1, max_length=64)
    environment_blueprint_id: str = Field(pattern=r"^andromeda-v2-[a-z0-9-]+$")
    transition_out: str = Field(min_length=1, max_length=120)
    shot_ids: list[str] = Field(min_length=5, max_length=5)
    beats: list[StoryBeatV2] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def bounded_content(self) -> StoryActV2:
        if self.frame_end < self.frame_start:
            raise ValueError("story act bounds are invalid")
        if any(not self.frame_start <= beat.frame <= self.frame_end for beat in self.beats):
            raise ValueError("story beat falls outside its act")
        return self


class StoryPlanV2(APIModel):
    schema_version: Literal["2.0.0"]
    template_id: Literal["trip-to-andromeda-story-v2"]
    project_id: Literal["trip-to-andromeda-v2"]
    seed: Literal[84291]
    frame_start: Literal[1]
    frame_end: Literal[13029]
    fps: float = Field(gt=0.0, le=120.0)
    duration_seconds: float = Field(gt=0.0)
    source_audio_sha256: str = Field(pattern=_HEX_64)
    source_cue_sha256: str = Field(pattern=_HEX_64)
    look_profile_id: Literal["andromeda-r13.1-final-look-v1"]
    look_profile_sha256: str = Field(pattern=_HEX_64)
    acts: list[StoryActV2] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def exact_story_contract(self) -> StoryPlanV2:
        if self.fps != ANDROMEDA_V2_FPS or self.duration_seconds != 434.286:
            raise ValueError("the Andromeda V2 timing contract may not drift")
        if self.source_audio_sha256 != SOURCE_AUDIO_SHA256:
            raise ValueError("source audio binding does not match the preserved Andromeda analysis")
        if self.source_cue_sha256 != SOURCE_CUE_SHA256:
            raise ValueError("source cue binding does not match the preserved Andromeda analysis")
        if tuple(act.id for act in self.acts) != _ACT_ORDER:
            raise ValueError("the seven-act story order is fixed")
        if self.acts[0].frame_start != self.frame_start or self.acts[-1].frame_end != self.frame_end:
            raise ValueError("story acts must cover the full timeline")
        for previous, current in zip(self.acts, self.acts[1:], strict=False):
            if current.frame_start != previous.frame_end + 1:
                raise ValueError("story acts must be contiguous")
        if len({shot_id for act in self.acts for shot_id in act.shot_ids}) != ANDROMEDA_V2_SHOT_COUNT:
            raise ValueError("the story must bind exactly 35 unique authored shots")
        return self


class RenderComplexityClass(StrEnum):
    LIGHT = "light"
    STANDARD = "standard"
    HEAVY = "heavy"
    EXTREME = "extreme"


class CompositionProfileBindings(APIModel):
    horizontal: str = Field(pattern=r"^andromeda-v2-horizontal-[a-z0-9-]+$")
    vertical: str = Field(pattern=r"^andromeda-v2-vertical-[a-z0-9-]+$")

    @model_validator(mode="after")
    def independent_profiles(self) -> CompositionProfileBindings:
        if self.horizontal == self.vertical:
            raise ValueError("horizontal and vertical compositions must be independently authored")
        return self


class ShotSpatialLayers(APIModel):
    foreground: str = Field(min_length=1, max_length=120)
    midground: str = Field(min_length=1, max_length=120)
    background: str = Field(min_length=1, max_length=120)


class BoundedAudioReactiveLayer(APIModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    source_feature: Literal[
        "smoothed-rms-energy",
        "smoothed-onset-density",
        "smoothed-spectral-centroid",
        "smoothed-bass-energy",
    ]
    target: str = Field(min_length=1, max_length=120)
    smoothing_frames: int = Field(ge=3, le=180)
    maximum_influence_fraction: float = Field(gt=0.0, le=0.25)
    controls_major_camera_or_protagonist_travel: Literal[False]


class ShotCompositionOverride(APIModel):
    composition_profile_id: str = Field(pattern=r"^andromeda-v2-(horizontal|vertical)-[a-z0-9-]+$")
    independently_authored: Literal[True]
    derived_by_crop: Literal[False]
    shared_event_timing: Literal[True]
    camera_rig_id: str = Field(pattern=r"^andromeda-v2-rig-[a-z0-9-]+$")
    lens_mm: float = Field(ge=18.0, le=135.0)
    framing_intent: str = Field(min_length=1, max_length=240)
    subject_occupancy_fraction: float = Field(ge=0.05, le=0.70)
    foreground_placement: str = Field(min_length=1, max_length=160)
    safe_zone: NormalizedSafeZone
    maximum_foreground_occlusion_fraction: float = Field(ge=0.0, le=0.30)
    minimum_landmark_visibility_fraction: float = Field(ge=0.50, le=1.0)
    title_safe_space: str = Field(min_length=1, max_length=100)
    screen_direction: str = Field(min_length=1, max_length=80)


class ShotCompositionOverrides(APIModel):
    horizontal: ShotCompositionOverride
    vertical: ShotCompositionOverride

    @model_validator(mode="after")
    def independently_authored_formats(self) -> ShotCompositionOverrides:
        independently_variable_fields = (
            "camera_rig_id",
            "lens_mm",
            "framing_intent",
            "subject_occupancy_fraction",
            "foreground_placement",
            "safe_zone",
            "title_safe_space",
        )
        differences = sum(
            getattr(self.horizontal, field) != getattr(self.vertical, field)
            for field in independently_variable_fields
        )
        if differences < 5:
            raise ValueError("shot formats require substantive independent composition overrides")
        if "horizontal" not in self.horizontal.composition_profile_id:
            raise ValueError("horizontal override must bind a horizontal composition profile")
        if "vertical" not in self.vertical.composition_profile_id:
            raise ValueError("vertical override must bind a vertical composition profile")
        return self


class ShotV2(APIModel):
    id: str = Field(pattern=r"^andromeda-v2-shot-[0-9]{2}-[a-z0-9-]+$")
    sequence: int = Field(ge=1, le=ANDROMEDA_V2_SHOT_COUNT)
    act_id: str
    name: str = Field(min_length=1, max_length=100)
    frame_start: int = Field(ge=ANDROMEDA_V2_FRAME_START)
    frame_end: int = Field(le=ANDROMEDA_V2_FRAME_END)
    duration_frames: int = Field(ge=1)
    story_purpose: str = Field(min_length=1, max_length=320)
    protagonist_state: str = Field(min_length=1, max_length=64)
    environment_blueprint_id: str = Field(pattern=r"^andromeda-v2-[a-z0-9-]+$")
    environment_signature: list[str] = Field(min_length=3, max_length=8)
    transition_in: str = Field(min_length=1, max_length=120)
    transition_out: str = Field(min_length=1, max_length=120)
    camera_intent: str = Field(min_length=1, max_length=180)
    camera_rig_id: str = Field(pattern=r"^andromeda-v2-rig-[a-z0-9-]+$")
    lens_mm: float = Field(ge=18.0, le=135.0)
    spatial_layers: ShotSpatialLayers
    dominant_shape: str = Field(min_length=1, max_length=120)
    secondary_narrative_action: str = Field(min_length=1, max_length=160)
    lighting_identity: str = Field(min_length=1, max_length=120)
    intentional_cut: bool
    audio_reactive_layers: list[BoundedAudioReactiveLayer] = Field(
        min_length=1,
        max_length=3,
    )
    complexity_class: RenderComplexityClass
    look_profile_id: Literal["andromeda-r13.1-final-look-v1"]
    look_profile_sha256: str = Field(pattern=_HEX_64)
    composition_profile_ids: CompositionProfileBindings
    composition_overrides: ShotCompositionOverrides
    required_landmarks: list[str] = Field(min_length=1, max_length=8)
    occlusion_policy_id: Literal["andromeda-subject-landmark-protection-v1"]
    review_frames: list[int] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def valid_frame_contract(self) -> ShotV2:
        if self.frame_end < self.frame_start:
            raise ValueError("shot bounds are invalid")
        if self.duration_frames != self.frame_end - self.frame_start + 1:
            raise ValueError("shot duration does not match its frame range")
        if any(not self.frame_start <= frame <= self.frame_end for frame in self.review_frames):
            raise ValueError("review frame falls outside its shot")
        if (
            self.composition_overrides.horizontal.composition_profile_id
            != self.composition_profile_ids.horizontal
            or self.composition_overrides.vertical.composition_profile_id
            != self.composition_profile_ids.vertical
        ):
            raise ValueError("shot composition override/profile bindings do not match")
        return self


class ShotPlanV2(APIModel):
    schema_version: Literal["2.0.0"]
    story_plan_schema_version: Literal["2.0.0"]
    project_id: Literal["trip-to-andromeda-v2"]
    seed: Literal[84291]
    frame_start: Literal[1]
    frame_end: Literal[13029]
    fps: float = Field(gt=0.0, le=120.0)
    story_plan_sha256: str = Field(pattern=_HEX_64)
    look_profile_id: Literal["andromeda-r13.1-final-look-v1"]
    look_profile_sha256: str = Field(pattern=_HEX_64)
    shots: list[ShotV2] = Field(min_length=ANDROMEDA_V2_SHOT_COUNT, max_length=ANDROMEDA_V2_SHOT_COUNT)

    @model_validator(mode="after")
    def exact_shot_contract(self) -> ShotPlanV2:
        if self.fps != ANDROMEDA_V2_FPS:
            raise ValueError("the Andromeda V2 shot-plan FPS may not drift")
        if [shot.sequence for shot in self.shots] != list(range(1, ANDROMEDA_V2_SHOT_COUNT + 1)):
            raise ValueError("shot sequence must be exactly 1 through 35")
        if self.shots[0].frame_start != self.frame_start or self.shots[-1].frame_end != self.frame_end:
            raise ValueError("shots must cover the full timeline")
        if len({shot.id for shot in self.shots}) != ANDROMEDA_V2_SHOT_COUNT:
            raise ValueError("shot IDs must be unique")
        for previous, current in zip(self.shots, self.shots[1:], strict=False):
            if current.frame_start != previous.frame_end + 1:
                raise ValueError("shots must be contiguous and non-overlapping")
        counts = {act_id: 0 for act_id in _ACT_ORDER}
        for shot in self.shots:
            if shot.act_id not in counts:
                raise ValueError("shot references an unknown act")
            counts[shot.act_id] += 1
            if shot.look_profile_id != self.look_profile_id:
                raise ValueError("every shot must resolve the locked look profile")
            if shot.look_profile_sha256 != self.look_profile_sha256:
                raise ValueError("every shot must resolve the locked look-profile hash")
            if shot.act_id in _FINAL_ACTS:
                lowered = " ".join(
                    [shot.environment_blueprint_id, *shot.environment_signature]
                ).lower()
                if "future" in lowered or "generic" in lowered or "placeholder" in lowered:
                    raise ValueError("final acts may not use generic future landmarks")
        if any(count != 5 for count in counts.values()):
            raise ValueError("each of the seven acts must contain exactly five authored shots")
        return self


class AuthorizationGateStatus(StrEnum):
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    NOT_REQUIRED_WHILE_DISABLED = "not-required-while-disabled"


class AuthorizationGate(APIModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    status: AuthorizationGateStatus
    evidence_required: str = Field(min_length=1, max_length=300)


class ProductionAuthorization(APIModel):
    schema_version: Literal["1.0.0"]
    authorization_id: Literal["andromeda-v2-production-authorization-v1"]
    project_id: Literal["trip-to-andromeda-v2"]
    status: Literal["blocked"]
    enabled_output_variants: list[Literal["horizontal-16x9-1080p"]] = Field(
        min_length=1,
        max_length=1,
    )
    production_start_allowed: Literal[False]
    final_render_started: Literal[False]
    gates: list[AuthorizationGate] = Field(min_length=8, max_length=12)

    @model_validator(mode="after")
    def enforce_finish_line_gates(self) -> ProductionAuthorization:
        by_id = {gate.id: gate for gate in self.gates}
        required_blockers = {
            "full-audio-animatic",
            "representative-visual-qa",
            "horizontal-calibration",
            "enabled-matrix-24h-sla",
            "exact-operator-production-authorization",
        }
        if len(by_id) != len(self.gates) or not required_blockers.issubset(by_id):
            raise ValueError("production authorization gates are incomplete or duplicated")
        if any(by_id[gate_id].status != AuthorizationGateStatus.BLOCKED for gate_id in required_blockers):
            raise ValueError("production must remain blocked until all finish-line evidence exists")
        vertical = by_id.get("vertical-calibration")
        if vertical is None or vertical.status != AuthorizationGateStatus.NOT_REQUIRED_WHILE_DISABLED:
            raise ValueError("vertical calibration is not required while vertical output is disabled")
        return self


class PackageArtifact(APIModel):
    role: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    path: str
    sha256: str = Field(pattern=_HEX_64)
    immutable: Literal[True]

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return _safe_repository_path(value)


class PrivateSourceBinding(APIModel):
    role: Literal["source-audio", "source-cue"]
    sha256: str = Field(pattern=_HEX_64)
    size_bytes: int = Field(ge=1)
    private_local_artifact: Literal[True]
    committed: Literal[False]


class PackageManifest(APIModel):
    schema_version: Literal["1.0.0"]
    package_id: Literal["andromeda-v2-foundation-v1"]
    project_id: Literal["trip-to-andromeda-v2"]
    content_version: Literal[1]
    status: Literal["foundation-ready-production-gates-blocked"]
    artifacts: list[PackageArtifact] = Field(min_length=6, max_length=12)
    source_bindings: list[PrivateSourceBinding] = Field(min_length=2, max_length=2)
    default_enabled_output_variants: list[Literal["horizontal-16x9-1080p"]] = Field(
        min_length=1,
        max_length=1,
    )
    optional_disabled_output_variants: list[Literal["vertical-9x16-1080p"]] = Field(
        min_length=1,
        max_length=1,
    )
    production_start_allowed: Literal[False]

    @model_validator(mode="after")
    def unique_bindings(self) -> PackageManifest:
        paths = [artifact.path for artifact in self.artifacts]
        roles = [artifact.role for artifact in self.artifacts]
        if len(set(paths)) != len(paths) or len(set(roles)) != len(roles):
            raise ValueError("package artifact paths and roles must be unique")
        sources = {binding.role: binding for binding in self.source_bindings}
        if set(sources) != {"source-audio", "source-cue"}:
            raise ValueError("the package must bind the private source audio and cue artifacts")
        if sources["source-audio"].sha256 != SOURCE_AUDIO_SHA256:
            raise ValueError("the private source-audio hash is invalid")
        if sources["source-cue"].sha256 != SOURCE_CUE_SHA256:
            raise ValueError("the private source-cue hash is invalid")
        return self


class AndromedaV2Foundation(APIModel):
    acceptance: OwnerAttestedCreativeAcceptance
    look_profile: FinalLookProfile
    output_variants: OutputVariantSet
    story_plan: StoryPlanV2
    shot_plan: ShotPlanV2
    authorization: ProductionAuthorization
    package_manifest: PackageManifest


def _load_model(path: Path, model_type: type[_MODEL]) -> _MODEL:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def load_and_validate_foundation(repository_root: Path) -> AndromedaV2Foundation:
    production_root = repository_root / "production" / "andromeda-v2"
    acceptance_path = production_root / "creative-acceptance.json"
    look_path = production_root / "final-look-profile.json"
    variants_path = production_root / "output-variants.json"
    authorization_path = production_root / "production-authorization.json"
    manifest_path = production_root / "package-manifest.json"
    story_path = (
        repository_root
        / "backend"
        / "app"
        / "cinematic"
        / "templates"
        / "trip_to_andromeda_story_v2.json"
    )
    shots_path = (
        repository_root
        / "backend"
        / "app"
        / "cinematic"
        / "templates"
        / "trip_to_andromeda_shots_v2.json"
    )

    foundation = AndromedaV2Foundation(
        acceptance=_load_model(acceptance_path, OwnerAttestedCreativeAcceptance),
        look_profile=_load_model(look_path, FinalLookProfile),
        output_variants=_load_model(variants_path, OutputVariantSet),
        story_plan=_load_model(story_path, StoryPlanV2),
        shot_plan=_load_model(shots_path, ShotPlanV2),
        authorization=_load_model(authorization_path, ProductionAuthorization),
        package_manifest=_load_model(manifest_path, PackageManifest),
    )

    look_sha256 = file_sha256(look_path)
    story_sha256 = file_sha256(story_path)
    if foundation.story_plan.look_profile_sha256 != look_sha256:
        raise ValueError("story plan does not bind the exact locked look-profile file")
    if foundation.shot_plan.look_profile_sha256 != look_sha256:
        raise ValueError("shot plan does not bind the exact locked look-profile file")
    if foundation.shot_plan.story_plan_sha256 != story_sha256:
        raise ValueError("shot plan does not bind the exact story-plan file")
    story_shot_ids = [
        shot_id
        for act in foundation.story_plan.acts
        for shot_id in act.shot_ids
    ]
    if story_shot_ids != [shot.id for shot in foundation.shot_plan.shots]:
        raise ValueError("StoryPlan and ShotPlan do not bind the same ordered shot identities")
    story_act_bounds = {
        act.id: (act.frame_start, act.frame_end)
        for act in foundation.story_plan.acts
    }
    for shot in foundation.shot_plan.shots:
        act_start, act_end = story_act_bounds[shot.act_id]
        if not act_start <= shot.frame_start <= shot.frame_end <= act_end:
            raise ValueError(f"shot {shot.id} falls outside its StoryPlan act")

    for artifact in foundation.package_manifest.artifacts:
        actual = file_sha256(repository_root / PurePosixPath(artifact.path))
        if actual != artifact.sha256:
            raise ValueError(f"package artifact hash mismatch for role {artifact.role}")
    return foundation


# These V2 final-release contracts are intentionally separate from the immutable
# foundation models above.  The foundation records the locked creative baseline;
# these models bind measured finish-line evidence to an exact, operator-controlled
# production job.
class FinalReleaseObjectiveGateId(StrEnum):
    DETERMINISTIC_EFFECTS_AND_DISK = "deterministic-effects-and-disk"
    LIVE_DASHBOARD = "live-dashboard"
    ANIMATIC_AND_MEDIA_QA = "animatic-and-media-qa"
    CALIBRATION_AND_ENABLED_MATRIX_SLA = "calibration-and-enabled-matrix-sla"
    DEPENDENCY_HEALTH = "dependency-health"
    ENABLED_OUTPUT_MATRIX_IDENTITY = "enabled-output-matrix-identity"
    SCENE_PROFILE_SOURCE_IDENTITY = "scene-profile-source-identity"
    WORKER_REQUIREMENTS = "worker-requirements"


_FINAL_RELEASE_OBJECTIVE_GATE_IDS = frozenset(FinalReleaseObjectiveGateId)
_FINAL_RELEASE_GATE_EVIDENCE_ROLES: dict[
    FinalReleaseObjectiveGateId,
    tuple[str, ...],
] = {
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
    FinalReleaseObjectiveGateId.ENABLED_OUTPUT_MATRIX_IDENTITY: ("output-variants",),
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
_FINAL_RELEASE_REQUIRED_ARTIFACT_ROLES = frozenset(
    {
        "final-scene",
        "output-variants",
        "story-plan",
        "shot-plan",
        "owner-creative-acceptance",
        "final-look-profile",
        "horizontal-render-profile",
        "encoding-profiles",
        "final-calibration-v2",
        "technical-authorization-v2",
        "deterministic-effects-and-disk-report",
        "live-dashboard-proof",
        "full-audio-animatic",
        "animatic-media-qa-report",
        "dependency-health-report",
        "source-revision-report",
        "worker-requirements",
        "release-report",
    }
)


class FinalReleaseGateStatus(StrEnum):
    SATISFIED = "satisfied"
    BLOCKED = "blocked"


class OperatorStartGateStatus(StrEnum):
    NOT_AUTHORIZED = "not-authorized"
    AUTHORIZED = "authorized"


class FinalReleaseWorkerRequirement(APIModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    device_class: Literal["cpu", "gpu"]
    renderer: Literal["BLENDER_EEVEE"]
    blender_version: Literal["5.2.0 LTS"]
    minimum_vram_mib: int = Field(ge=0)
    maximum_workers_per_device: int = Field(ge=1)
    chunk_size_frames: int = Field(ge=1)
    deterministic_seed: Literal[84291]
    required_capabilities: list[str] = Field(min_length=1)

    @field_validator("required_capabilities")
    @classmethod
    def unique_capabilities(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values) or len(set(values)) != len(values):
            raise ValueError("worker capabilities must be non-empty and unique")
        return values


class EnabledFinalOutputVariant(APIModel):
    id: Literal["horizontal-16x9-1080p", "vertical-9x16-1080p"]
    enabled: Literal[True]
    required: bool
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    fps: Literal[30]
    frame_start: Literal[1]
    frame_end: Literal[13029]
    composition_profile_id: str = Field(
        pattern=r"^andromeda-v2-(horizontal|vertical)-[a-z0-9-]+$"
    )
    camera_name: str = Field(pattern=r"^TP_ANDROMEDA_V2_CAMERA_[A-Z0-9_]+$")
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    worker_requirement_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    output_pattern: str = Field(min_length=1, max_length=1024)

    @field_validator("output_pattern")
    @classmethod
    def safe_output_pattern(cls, value: str) -> str:
        normalized = _safe_repository_path(value)
        if normalized.count("######") != 1:
            raise ValueError(
                "enabled output patterns require exactly one six-digit frame placeholder"
            )
        return normalized

    @model_validator(mode="after")
    def exact_variant_geometry(self) -> EnabledFinalOutputVariant:
        if self.id == "horizontal-16x9-1080p":
            if (self.width, self.height) != (1920, 1080) or not self.required:
                raise ValueError("the enabled horizontal master must be required 1920x1080")
            if "horizontal" not in self.composition_profile_id:
                raise ValueError("horizontal output must bind a horizontal composition profile")
        else:
            if (self.width, self.height) != (1080, 1920) or self.required:
                raise ValueError("the enabled vertical deliverable must be optional 1080x1920")
            if "vertical" not in self.composition_profile_id:
                raise ValueError("vertical output must bind a vertical composition profile")
        return self


class EnabledFinalOutputMatrix(APIModel):
    schema_version: Literal["2.0.0"]
    matrix_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    enabled_variant_ids: list[
        Literal["horizontal-16x9-1080p", "vertical-9x16-1080p"]
    ] = Field(min_length=1)
    variants: list[EnabledFinalOutputVariant] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_enabled_variants(self) -> EnabledFinalOutputMatrix:
        variant_ids = [variant.id for variant in self.variants]
        if len(set(variant_ids)) != len(variant_ids):
            raise ValueError("enabled output variants must be unique")
        if variant_ids != self.enabled_variant_ids:
            raise ValueError("enabled variant IDs must exactly match the ordered output matrix")
        if variant_ids[0] != "horizontal-16x9-1080p":
            raise ValueError("the required horizontal master must be the first enabled variant")
        return self


class FinalReleaseIdentity(APIModel):
    schema_version: Literal["2.0.0"]
    release_id: Literal["andromeda-v2-final-release-v2"]
    project_id: Literal["trip-to-andromeda-v2"]
    source_audio_sha256: str = Field(pattern=_HEX_64)
    source_cue_sha256: str = Field(pattern=_HEX_64)
    owner_creative_acceptance_sha256: str = Field(pattern=_HEX_64)
    encoding_profiles_sha256: str = Field(pattern=_HEX_64)
    look_profile_sha256: str = Field(pattern=_HEX_64)
    story_plan_sha256: str = Field(pattern=_HEX_64)
    shot_plan_sha256: str = Field(pattern=_HEX_64)
    output_variant_contract_sha256: str = Field(pattern=_HEX_64)
    builder_source_sha256: str = Field(pattern=_HEX_64)
    source_tree_sha256: str = Field(pattern=_HEX_64)
    git_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    deterministic_seed: Literal[84291]
    output_matrix: EnabledFinalOutputMatrix
    worker_requirements: list[FinalReleaseWorkerRequirement] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_workers(self) -> FinalReleaseIdentity:
        if self.source_audio_sha256 != SOURCE_AUDIO_SHA256:
            raise ValueError("final-release identity source-audio hash is invalid")
        if self.source_cue_sha256 != SOURCE_CUE_SHA256:
            raise ValueError("final-release identity source-cue hash is invalid")
        if self.owner_creative_acceptance_sha256 != OWNER_CREATIVE_ACCEPTANCE_SHA256:
            raise ValueError(
                "final-release identity owner creative-acceptance hash is invalid"
            )
        if self.encoding_profiles_sha256 != ENCODING_PROFILES_SHA256:
            raise ValueError("final-release identity encoding-profiles hash is invalid")
        requirements = {requirement.id: requirement for requirement in self.worker_requirements}
        if len(requirements) != len(self.worker_requirements):
            raise ValueError("worker requirement identities must be unique")
        referenced = {
            variant.worker_requirement_id for variant in self.output_matrix.variants
        }
        if not referenced.issubset(requirements):
            raise ValueError("every enabled output variant must resolve a worker requirement")
        return self


def final_release_identity_sha256(identity: FinalReleaseIdentity) -> str:
    return canonical_sha256(identity.model_dump(mode="json", by_alias=True))


class FinalVariantCalibration(APIModel):
    output_variant_id: Literal[
        "horizontal-16x9-1080p",
        "vertical-9x16-1080p",
    ]
    scene_sha256: str = Field(pattern=_HEX_64)
    render_profile_sha256: str = Field(pattern=_HEX_64)
    composition_profile_id: str = Field(
        pattern=r"^andromeda-v2-(horizontal|vertical)-[a-z0-9-]+$"
    )
    camera_name: str = Field(pattern=r"^TP_ANDROMEDA_V2_CAMERA_[A-Z0-9_]+$")
    worker_requirement_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    fps: Literal[30]
    sample_frames: list[int] = Field(min_length=1)
    p50_seconds_per_frame: float = Field(gt=0.0)
    p90_seconds_per_frame: float = Field(gt=0.0)
    weighted_seconds_per_frame: float = Field(gt=0.0)
    projected_render_seconds_p50: float = Field(gt=0.0)
    projected_render_seconds_p90: float = Field(gt=0.0)
    projected_output_bytes: int = Field(ge=1)
    calibration_complete: bool

    @model_validator(mode="after")
    def valid_measurements(self) -> FinalVariantCalibration:
        if len(set(self.sample_frames)) != len(self.sample_frames):
            raise ValueError("calibration sample frames must be unique")
        if any(
            frame < ANDROMEDA_V2_FRAME_START or frame > ANDROMEDA_V2_FRAME_END
            for frame in self.sample_frames
        ):
            raise ValueError("calibration sample frames must be inside the production range")
        if self.p50_seconds_per_frame > self.p90_seconds_per_frame:
            raise ValueError("variant P50 seconds/frame may not exceed P90")
        if self.projected_render_seconds_p50 > self.projected_render_seconds_p90:
            raise ValueError("variant P50 render forecast may not exceed P90")
        return self


class FinalStageForecast(APIModel):
    stage: Literal["rendering", "frame-validation", "encoding", "media-qa", "publication"]
    p50_seconds: float = Field(ge=0.0)
    p90_seconds: float = Field(ge=0.0)

    @model_validator(mode="after")
    def ordered_percentiles(self) -> FinalStageForecast:
        if self.p50_seconds > self.p90_seconds:
            raise ValueError("stage P50 may not exceed P90")
        return self


class AndromedaV2FinalCalibration(APIModel):
    schema_version: Literal["2.0.0"]
    kind: Literal["trackprompt-final-render-calibration"]
    calibration_id: Literal["andromeda-v2-final-calibration-v2"]
    identity: FinalReleaseIdentity
    release_identity_sha256: str = Field(pattern=_HEX_64)
    variant_calibrations: list[FinalVariantCalibration] = Field(min_length=1)
    stage_forecasts: list[FinalStageForecast] = Field(min_length=5)
    aggregate_p50_seconds: float = Field(gt=0.0)
    aggregate_p90_seconds: float = Field(gt=0.0)
    sla_limit_seconds: Literal[86400]
    sla_satisfied: bool
    deterministic_effects_verified: bool
    all_required_bakes_complete: bool
    dependency_health_passed: bool
    vram_stable: bool
    disk_free_bytes: int = Field(ge=1)
    projected_peak_disk_bytes: int = Field(ge=1)
    disk_safety_multiplier: float = Field(ge=1.25)
    disk_headroom_satisfied: bool

    @model_validator(mode="after")
    def exact_calibration_identity(self) -> AndromedaV2FinalCalibration:
        if self.release_identity_sha256 != final_release_identity_sha256(self.identity):
            raise ValueError("calibration release identity hash does not match its identity")
        matrix_variants = self.identity.output_matrix.variants
        calibrations = {
            calibration.output_variant_id: calibration
            for calibration in self.variant_calibrations
        }
        if len(calibrations) != len(self.variant_calibrations):
            raise ValueError("variant calibration identities must be unique")
        if list(calibrations) != [variant.id for variant in matrix_variants]:
            raise ValueError("calibration variants must exactly match the enabled output matrix")
        for variant in matrix_variants:
            calibration = calibrations[variant.id]
            expected = (
                variant.scene_sha256,
                variant.render_profile_sha256,
                variant.composition_profile_id,
                variant.camera_name,
                variant.worker_requirement_id,
                variant.width,
                variant.height,
                variant.fps,
            )
            actual = (
                calibration.scene_sha256,
                calibration.render_profile_sha256,
                calibration.composition_profile_id,
                calibration.camera_name,
                calibration.worker_requirement_id,
                calibration.width,
                calibration.height,
                calibration.fps,
            )
            if actual != expected:
                raise ValueError(
                    f"calibration identity does not match enabled variant {variant.id}"
                )
        stages = [forecast.stage for forecast in self.stage_forecasts]
        expected_stages = [
            "rendering",
            "frame-validation",
            "encoding",
            "media-qa",
            "publication",
        ]
        if stages != expected_stages:
            raise ValueError(
                "calibration must contain every final production stage once in execution order"
            )
        if self.aggregate_p50_seconds > self.aggregate_p90_seconds:
            raise ValueError("aggregate P50 may not exceed aggregate P90")
        # Final production stages run sequentially. Their per-stage forecasts may
        # already aggregate parallel work inside an enabled output matrix, but the
        # release-level aggregate is always the sum across the five ordered stages.
        # One microsecond of tolerance only absorbs JSON-to-binary-float conversion.
        expected_p50 = math.fsum(
            forecast.p50_seconds for forecast in self.stage_forecasts
        )
        expected_p90 = math.fsum(
            forecast.p90_seconds for forecast in self.stage_forecasts
        )
        if not math.isclose(
            self.aggregate_p50_seconds,
            expected_p50,
            rel_tol=0.0,
            abs_tol=1e-6,
        ) or not math.isclose(
            self.aggregate_p90_seconds,
            expected_p90,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise ValueError(
                "aggregate forecasts must equal the sequential sum of stage forecasts"
            )
        if self.sla_satisfied != (self.aggregate_p90_seconds <= self.sla_limit_seconds):
            raise ValueError("SLA status must agree with the aggregate P90 forecast")
        required_free_bytes = self.projected_peak_disk_bytes * self.disk_safety_multiplier
        if self.disk_headroom_satisfied != (self.disk_free_bytes >= required_free_bytes):
            raise ValueError("disk headroom status must include the declared safety multiplier")
        return self


class FinalReleaseObjectiveGate(APIModel):
    id: FinalReleaseObjectiveGateId
    status: FinalReleaseGateStatus
    evidence_artifact_roles: list[str] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=300)
    evidence_kind: Literal["objective-technical-evidence"]

    @field_validator("evidence_artifact_roles")
    @classmethod
    def unique_evidence_roles(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values) or len(set(values)) != len(values):
            raise ValueError("objective-gate evidence roles must be non-empty and unique")
        return values


class ExplicitOperatorStartGate(APIModel):
    status: OperatorStartGateStatus
    authorization_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,119}$",
    )
    authorized_by_role: Literal["project-owner-operator"] | None = None
    authorized_at: str | None = Field(default=None, min_length=1)
    authorized_release_identity_sha256: str | None = Field(
        default=None,
        pattern=_HEX_64,
    )
    explicit_full_render_start_authorized: bool

    @model_validator(mode="after")
    def exact_operator_decision(self) -> ExplicitOperatorStartGate:
        authorization_fields = (
            self.authorization_id,
            self.authorized_by_role,
            self.authorized_at,
            self.authorized_release_identity_sha256,
        )
        if self.status == OperatorStartGateStatus.AUTHORIZED:
            if not self.explicit_full_render_start_authorized or any(
                value is None for value in authorization_fields
            ):
                raise ValueError("operator start authorization must be explicit and identity-bound")
        elif self.explicit_full_render_start_authorized or any(
            value is not None for value in authorization_fields
        ):
            raise ValueError("an unauthorized operator-start gate may not contain approval data")
        return self


class AndromedaV2TechnicalAuthorization(APIModel):
    schema_version: Literal["2.0.0"]
    kind: Literal["trackprompt-final-technical-authorization"]
    authorization_id: Literal["andromeda-v2-technical-authorization-v2"]
    package_id: Literal["andromeda-v2-final-render-package-v2"]
    calibration_id: Literal["andromeda-v2-final-calibration-v2"]
    calibration_sha256: str = Field(pattern=_HEX_64)
    identity: FinalReleaseIdentity
    release_identity_sha256: str = Field(pattern=_HEX_64)
    status: Literal["technically-ready", "blocked"]
    technical_ready: bool
    objective_gates: list[FinalReleaseObjectiveGate] = Field(min_length=8)
    operator_start_gate: ExplicitOperatorStartGate
    production_start_allowed: bool
    final_render_started: bool
    codex_human_artistic_approval: Literal[False]
    human_artistic_judgment_source: Literal[
        "owner-attested-r13.1-baseline-not-codex"
    ]

    @model_validator(mode="after")
    def enforce_technical_and_operator_boundaries(
        self,
    ) -> AndromedaV2TechnicalAuthorization:
        identity_sha256 = final_release_identity_sha256(self.identity)
        if self.release_identity_sha256 != identity_sha256:
            raise ValueError("technical authorization identity hash does not match its identity")
        gates = {gate.id: gate for gate in self.objective_gates}
        if len(gates) != len(self.objective_gates) or set(gates) != set(
            _FINAL_RELEASE_OBJECTIVE_GATE_IDS
        ):
            raise ValueError("technical authorization objective gates are incomplete or duplicated")
        for gate in self.objective_gates:
            expected_roles = _FINAL_RELEASE_GATE_EVIDENCE_ROLES[gate.id]
            if tuple(gate.evidence_artifact_roles) != expected_roles:
                raise ValueError(
                    f"objective gate {gate.id} must reference its exact canonical evidence roles"
                )
        all_objective_gates_satisfied = all(
            gate.status == FinalReleaseGateStatus.SATISFIED
            for gate in self.objective_gates
        )
        if self.technical_ready and not all_objective_gates_satisfied:
            raise ValueError("technical readiness requires every objective gate to be satisfied")
        if (self.status == "technically-ready") != self.technical_ready:
            raise ValueError("technical authorization status must agree with technical readiness")
        if (
            self.operator_start_gate.status == OperatorStartGateStatus.AUTHORIZED
            and self.operator_start_gate.authorized_release_identity_sha256
            != identity_sha256
        ):
            raise ValueError("operator start authorization must bind the exact release identity")
        expected_start_allowed = (
            self.technical_ready
            and self.operator_start_gate.status == OperatorStartGateStatus.AUTHORIZED
        )
        if self.production_start_allowed != expected_start_allowed:
            raise ValueError(
                "production start requires technical readiness and exact operator authorization"
            )
        if self.final_render_started and not self.production_start_allowed:
            raise ValueError("the final render may not start while production start is blocked")
        return self


class AndromedaV2FinalPackageManifest(APIModel):
    schema_version: Literal["2.0.0"]
    kind: Literal["trackprompt-final-render-package"]
    package_id: Literal["andromeda-v2-final-render-package-v2"]
    project_id: Literal["trip-to-andromeda-v2"]
    identity: FinalReleaseIdentity
    release_identity_sha256: str = Field(pattern=_HEX_64)
    calibration_id: Literal["andromeda-v2-final-calibration-v2"]
    calibration_sha256: str = Field(pattern=_HEX_64)
    technical_authorization_id: Literal["andromeda-v2-technical-authorization-v2"]
    status: Literal["technically-ready-operator-start-blocked", "blocked", "start-authorized"]
    technical_ready: bool
    artifacts: list[PackageArtifact] = Field(min_length=1)
    source_bindings: list[PrivateSourceBinding] = Field(min_length=2, max_length=2)
    production_start_allowed: bool
    codex_human_artistic_approval: Literal[False]

    @model_validator(mode="after")
    def complete_final_package(self) -> AndromedaV2FinalPackageManifest:
        if self.release_identity_sha256 != final_release_identity_sha256(self.identity):
            raise ValueError("package release identity hash does not match its identity")
        if self.production_start_allowed and not self.technical_ready:
            raise ValueError("a final package cannot allow start before technical readiness")
        paths = [artifact.path for artifact in self.artifacts]
        roles = [artifact.role for artifact in self.artifacts]
        if len(set(paths)) != len(paths) or len(set(roles)) != len(roles):
            raise ValueError("final package artifact paths and roles must be unique")
        missing_roles = _FINAL_RELEASE_REQUIRED_ARTIFACT_ROLES.difference(roles)
        if missing_roles:
            raise ValueError(
                f"final package is missing mandatory artifact roles: {sorted(missing_roles)}"
            )
        artifacts = {artifact.role: artifact for artifact in self.artifacts}
        exact_artifact_hashes = {
            "output-variants": self.identity.output_variant_contract_sha256,
            "story-plan": self.identity.story_plan_sha256,
            "shot-plan": self.identity.shot_plan_sha256,
            "owner-creative-acceptance": (
                self.identity.owner_creative_acceptance_sha256
            ),
            "final-look-profile": self.identity.look_profile_sha256,
            "encoding-profiles": self.identity.encoding_profiles_sha256,
        }
        for role, expected_sha256 in exact_artifact_hashes.items():
            if artifacts[role].sha256 != expected_sha256:
                raise ValueError(
                    f"final package artifact {role} does not match release identity"
                )
        artifact_hashes = {artifact.sha256 for artifact in self.artifacts}
        for variant in self.identity.output_matrix.variants:
            if variant.scene_sha256 not in artifact_hashes:
                raise ValueError(
                    f"final package is missing exact scene for enabled variant {variant.id}"
                )
            if variant.render_profile_sha256 not in artifact_hashes:
                raise ValueError(
                    "final package is missing exact render profile for enabled "
                    f"variant {variant.id}"
                )
        sources = {binding.role: binding for binding in self.source_bindings}
        if set(sources) != {"source-audio", "source-cue"}:
            raise ValueError("the final package must bind source audio and cue identities")
        if sources["source-audio"].sha256 != SOURCE_AUDIO_SHA256:
            raise ValueError("the final package source-audio hash is invalid")
        if sources["source-cue"].sha256 != SOURCE_CUE_SHA256:
            raise ValueError("the final package source-cue hash is invalid")
        if sources["source-audio"].sha256 != self.identity.source_audio_sha256:
            raise ValueError(
                "the final package source-audio binding does not match release identity"
            )
        if sources["source-cue"].sha256 != self.identity.source_cue_sha256:
            raise ValueError(
                "the final package source-cue binding does not match release identity"
            )
        expected_status = (
            "start-authorized"
            if self.production_start_allowed
            else (
                "technically-ready-operator-start-blocked"
                if self.technical_ready
                else "blocked"
            )
        )
        if self.status != expected_status:
            raise ValueError("final package status must reflect technical and operator gates")
        return self


class AndromedaV2FinalRelease(APIModel):
    calibration: AndromedaV2FinalCalibration
    package_manifest: AndromedaV2FinalPackageManifest
    technical_authorization: AndromedaV2TechnicalAuthorization

    @model_validator(mode="after")
    def exact_release_bindings(self) -> AndromedaV2FinalRelease:
        identities = (
            self.calibration.identity,
            self.package_manifest.identity,
            self.technical_authorization.identity,
        )
        if any(identity != identities[0] for identity in identities[1:]):
            raise ValueError(
                "enabled matrix, calibration, package, and authorization identities must agree"
            )
        identity_hashes = {
            self.calibration.release_identity_sha256,
            self.package_manifest.release_identity_sha256,
            self.technical_authorization.release_identity_sha256,
        }
        if len(identity_hashes) != 1:
            raise ValueError("final-release identity hashes must agree")
        if (
            self.package_manifest.calibration_id != self.calibration.calibration_id
            or self.technical_authorization.calibration_id
            != self.calibration.calibration_id
            or self.package_manifest.calibration_sha256
            != self.technical_authorization.calibration_sha256
        ):
            raise ValueError("package and authorization must bind the exact V2 calibration")
        if (
            self.package_manifest.package_id
            != self.technical_authorization.package_id
            or self.package_manifest.technical_authorization_id
            != self.technical_authorization.authorization_id
        ):
            raise ValueError("package and technical-authorization identities must agree")

        artifacts = {
            artifact.role: artifact for artifact in self.package_manifest.artifacts
        }
        if artifacts["final-calibration-v2"].sha256 != self.package_manifest.calibration_sha256:
            raise ValueError("package calibration artifact hash does not match its binding")
        for gate in self.technical_authorization.objective_gates:
            if not set(gate.evidence_artifact_roles).issubset(artifacts):
                raise ValueError(f"objective gate {gate.id} references missing package evidence")

        gates = {
            gate.id: gate.status
            for gate in self.technical_authorization.objective_gates
        }
        calibration_ready = (
            all(
                calibration.calibration_complete
                for calibration in self.calibration.variant_calibrations
            )
            and self.calibration.sla_satisfied
        )
        if (
            gates[
                FinalReleaseObjectiveGateId.CALIBRATION_AND_ENABLED_MATRIX_SLA
            ]
            == FinalReleaseGateStatus.SATISFIED
            and not calibration_ready
        ):
            raise ValueError("the calibration/SLA gate contradicts measured calibration")
        effects_and_disk_ready = (
            self.calibration.deterministic_effects_verified
            and self.calibration.all_required_bakes_complete
            and self.calibration.disk_headroom_satisfied
            and self.calibration.vram_stable
        )
        if (
            gates[FinalReleaseObjectiveGateId.DETERMINISTIC_EFFECTS_AND_DISK]
            == FinalReleaseGateStatus.SATISFIED
            and not effects_and_disk_ready
        ):
            raise ValueError("the deterministic-effects/disk gate contradicts calibration")
        if (
            gates[FinalReleaseObjectiveGateId.DEPENDENCY_HEALTH]
            == FinalReleaseGateStatus.SATISFIED
            and not self.calibration.dependency_health_passed
        ):
            raise ValueError("the dependency-health gate contradicts calibration")
        if (
            self.package_manifest.technical_ready
            != self.technical_authorization.technical_ready
            or self.package_manifest.production_start_allowed
            != self.technical_authorization.production_start_allowed
        ):
            raise ValueError("package and authorization readiness states must agree")
        return self


def _resolve_repository_file(repository_root: Path, path: Path) -> Path:
    try:
        root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("final-release repository root does not exist") from exc
    if not root.is_dir():
        raise ValueError("final-release repository root must be a directory")
    candidate = path if path.is_absolute() else root / path
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("final-release artifacts must resolve inside the repository root") from exc
    if not resolved.is_file():
        raise ValueError("final-release artifact paths must resolve to files")
    return resolved


def load_and_validate_final_release(
    calibration_path: Path,
    package_manifest_path: Path,
    technical_authorization_path: Path,
    *,
    repository_root: Path,
) -> AndromedaV2FinalRelease:
    calibration_file = _resolve_repository_file(repository_root, calibration_path)
    package_manifest_file = _resolve_repository_file(
        repository_root,
        package_manifest_path,
    )
    technical_authorization_file = _resolve_repository_file(
        repository_root,
        technical_authorization_path,
    )
    release = AndromedaV2FinalRelease(
        calibration=_load_model(calibration_file, AndromedaV2FinalCalibration),
        package_manifest=_load_model(
            package_manifest_file,
            AndromedaV2FinalPackageManifest,
        ),
        technical_authorization=_load_model(
            technical_authorization_file,
            AndromedaV2TechnicalAuthorization,
        ),
    )
    artifact_files: dict[str, Path] = {}
    for artifact in release.package_manifest.artifacts:
        relative_path = Path(*PurePosixPath(artifact.path).parts)
        artifact_file = _resolve_repository_file(repository_root, relative_path)
        artifact_files[artifact.role] = artifact_file
        if file_sha256(artifact_file) != artifact.sha256:
            raise ValueError(
                f"final-release package artifact hash mismatch for role {artifact.role}"
            )

    exact_document_paths = {
        "final-calibration-v2": calibration_file,
        "technical-authorization-v2": technical_authorization_file,
    }
    for role, document_path in exact_document_paths.items():
        if artifact_files[role] != document_path:
            raise ValueError(
                f"final-release loader path does not match declared artifact role {role}"
            )
    return release

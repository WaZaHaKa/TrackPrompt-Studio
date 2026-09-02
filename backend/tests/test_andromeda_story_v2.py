from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.cinematic.production_contracts import (
    ANDROMEDA_V2_FRAME_END,
    ANDROMEDA_V2_FRAME_START,
    ANDROMEDA_V2_SHOT_COUNT,
    R131_MOTION_PREVIEW_SHA256,
    R131_RENDER_MANIFEST_SHA256,
    R131_SCENE_SHA256,
    OutputVariantSet,
    OwnerAttestedCreativeAcceptance,
    ShotPlanV2,
    load_and_validate_foundation,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = REPOSITORY_ROOT / "production" / "andromeda-v2"
STORY_PATH = (
    REPOSITORY_ROOT
    / "backend"
    / "app"
    / "cinematic"
    / "templates"
    / "trip_to_andromeda_story_v2.json"
)
SHOT_PATH = (
    REPOSITORY_ROOT
    / "backend"
    / "app"
    / "cinematic"
    / "templates"
    / "trip_to_andromeda_shots_v2.json"
)


def _payload(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_owner_acceptance_is_user_supplied_and_does_not_rewrite_r131() -> None:
    foundation = load_and_validate_foundation(REPOSITORY_ROOT)
    acceptance = foundation.acceptance

    assert acceptance.kind == "trackprompt-project-creative-acceptance"
    assert acceptance.approval_kind == "operator-confirmed-audience-avatar-acceptance"
    assert acceptance.revision == "andromeda-r13.1-selected-refinement"
    assert acceptance.human_approval == "approved"
    assert acceptance.human_approval_source == "operator-attested-audience-avatar-review"
    assert acceptance.operator_creative_direction_approved is True
    assert acceptance.style_lock_status == "approved-for-this-project"
    assert acceptance.scope == "overall visual and motion quality target for Trip to Andromeda V2"
    assert acceptance.attestation_source == "user-supplied-finish-line-brief"
    assert acceptance.recorded_by == "codex-recording-user-supplied-attestation"
    assert acceptance.attested_by_role == "project-owner-operator"
    assert acceptance.does_not_by_itself_authorize == [
        "skipping technical QA",
        "using stale scene or render profiles",
        "cloud provisioning",
        "starting the full render before the final technical gates",
    ]
    assert acceptance.does_not_rewrite_historical_review is True
    assert acceptance.does_not_authorize_production is True
    assert acceptance.proof.authoritative_scene_sha256 == R131_SCENE_SHA256
    assert acceptance.proof.render_manifest_sha256 == R131_RENDER_MANIFEST_SHA256
    assert acceptance.proof.motion_preview_sha256 == R131_MOTION_PREVIEW_SHA256


def test_locked_look_and_output_variants_are_exact_and_aspect_neutral() -> None:
    foundation = load_and_validate_foundation(REPOSITORY_ROOT)
    look = foundation.look_profile
    assert look.locked is True
    assert look.preview_only is False
    assert look.aspect_neutral is True
    assert look.production_authorization is False
    assert look.protagonist.identity == "protagonist-b-ancient-engine"
    assert look.protagonist.front_back_orientation == "clear-directional-front-and-back"
    assert look.protagonist.wire_cage is False
    assert look.protagonist.hud_overlay is False
    assert look.architecture.procedural_blockout_accepted_as_final is False
    assert look.gate.localized_deformation_only is True
    assert look.gate.localized_membrane_count == 1
    assert look.motion.protagonist_movement == "independent-authored"
    assert look.motion.camera_lag == "authored"
    assert look.motion.foreground_parallax == "authored"
    assert look.motion.raw_audio_controls_major_camera_travel is False
    assert look.motion.raw_audio_controls_major_protagonist_travel is False
    assert look.transparency.maximum_layers == 2
    assert look.transparency.frame_filling_transparent_volume is False
    assert look.render_baseline.model_dump(by_alias=True) == {
        "blenderVersion": "5.2",
        "renderEngine": "BLENDER_EEVEE",
        "temporalSamples": 64,
        "volumetricSamples": 32,
        "temporalAntialiasing": True,
        "temporalReprojection": True,
        "colorManagement": "AgX Medium High Contrast",
        "transparencyMode": "DITHERED",
        "motionBlur": False,
        "maximumTransparentLayers": 2,
        "localizedGateMembranes": 1,
        "compositorDenoising": False,
    }
    assert look.selected_language.model_dump(by_alias=True) == {
        "protagonistDesign": "protagonist-b-ancient-engine",
        "architecturalMaterialLanguage": "weathered-stone-metal-crystal-v1",
        "gateConstruction": "nested-ring-monolith-v1",
        "exposureLightingTreatment": "restrained-teal-cyan-amber-v1",
    }

    variants = {variant.id: variant for variant in foundation.output_variants.variants}
    horizontal = variants["horizontal-16x9-1080p"]
    vertical = variants["vertical-9x16-1080p"]
    assert (horizontal.width, horizontal.height) == (1920, 1080)
    assert horizontal.required is True
    assert horizontal.enabled_by_default is True
    assert horizontal.composition_mode == "authored"
    assert horizontal.deliverable_role == "primary-master"
    assert (vertical.width, vertical.height) == (1080, 1920)
    assert vertical.required is False
    assert vertical.enabled_by_default is False
    assert vertical.composition_mode == "authored"
    assert vertical.deliverable_role == "optional-social"
    assert horizontal.composition_profile_id != vertical.composition_profile_id


def test_story_and_shots_are_exactly_35_contiguous_authored_ranges() -> None:
    foundation = load_and_validate_foundation(REPOSITORY_ROOT)
    story = foundation.story_plan
    shots = foundation.shot_plan.shots

    assert [act.id for act in story.acts] == [
        "signal",
        "awakening",
        "departure",
        "gates",
        "rupture",
        "transformation",
        "arrival",
    ]
    assert len(shots) == ANDROMEDA_V2_SHOT_COUNT
    assert shots[0].frame_start == ANDROMEDA_V2_FRAME_START
    assert shots[-1].frame_end == ANDROMEDA_V2_FRAME_END
    assert all(
        current.frame_start == previous.frame_end + 1
        for previous, current in zip(shots, shots[1:], strict=False)
    )
    assert sum(shot.duration_frames for shot in shots) == ANDROMEDA_V2_FRAME_END
    assert {
        act.id: len([shot for shot in shots if shot.act_id == act.id])
        for act in story.acts
    } == {act.id: 5 for act in story.acts}


def test_final_three_acts_are_specific_environments_and_transitions() -> None:
    foundation = load_and_validate_foundation(REPOSITORY_ROOT)
    expected_environments = {
        "rupture": "andromeda-v2-broken-route-fracture-canyon",
        "transformation": "andromeda-v2-reconstruction-cathedral",
        "arrival": "andromeda-v2-andromeda-lattice-horizon",
    }
    for act_id, environment_id in expected_environments.items():
        act_shots = [shot for shot in foundation.shot_plan.shots if shot.act_id == act_id]
        assert len(act_shots) == 5
        assert {shot.environment_blueprint_id for shot in act_shots} == {environment_id}
        assert len({shot.transition_out for shot in act_shots}) == 5
        text = json.dumps(
            [shot.model_dump(by_alias=True) for shot in act_shots],
            sort_keys=True,
        ).lower()
        assert "future" not in text
        assert "generic" not in text
        assert "placeholder" not in text


def test_every_shot_binds_look_complexity_and_two_compositions() -> None:
    foundation = load_and_validate_foundation(REPOSITORY_ROOT)
    shot_plan = foundation.shot_plan
    assert all(shot.look_profile_id == shot_plan.look_profile_id for shot in shot_plan.shots)
    assert all(shot.look_profile_sha256 == shot_plan.look_profile_sha256 for shot in shot_plan.shots)
    assert all(
        shot.composition_profile_ids.horizontal != shot.composition_profile_ids.vertical
        for shot in shot_plan.shots
    )
    assert {"light", "standard", "heavy", "extreme"}.issubset(
        {shot.complexity_class.value for shot in shot_plan.shots}
    )
    assert all(shot.camera_rig_id.startswith("andromeda-v2-rig-") for shot in shot_plan.shots)
    assert all(18.0 <= shot.lens_mm <= 135.0 for shot in shot_plan.shots)
    assert all(shot.spatial_layers.foreground for shot in shot_plan.shots)
    assert all(shot.spatial_layers.midground for shot in shot_plan.shots)
    assert all(shot.spatial_layers.background for shot in shot_plan.shots)
    assert all(shot.dominant_shape for shot in shot_plan.shots)
    assert all(shot.secondary_narrative_action for shot in shot_plan.shots)
    assert all(shot.lighting_identity for shot in shot_plan.shots)
    assert all(shot.audio_reactive_layers for shot in shot_plan.shots)
    assert all(
        layer.controls_major_camera_or_protagonist_travel is False
        and layer.maximum_influence_fraction <= 0.25
        and layer.smoothing_frames >= 3
        for shot in shot_plan.shots
        for layer in shot.audio_reactive_layers
    )
    assert all(
        shot.composition_overrides.horizontal.composition_profile_id
        == shot.composition_profile_ids.horizontal
        and shot.composition_overrides.vertical.composition_profile_id
        == shot.composition_profile_ids.vertical
        and shot.composition_overrides.horizontal.derived_by_crop is False
        and shot.composition_overrides.vertical.derived_by_crop is False
        and shot.composition_overrides.horizontal.model_dump()
        != shot.composition_overrides.vertical.model_dump()
        for shot in shot_plan.shots
    )


def test_package_and_authorization_remain_deterministically_blocked() -> None:
    foundation = load_and_validate_foundation(REPOSITORY_ROOT)
    authorization = foundation.authorization
    assert authorization.status == "blocked"
    assert authorization.production_start_allowed is False
    assert authorization.final_render_started is False
    assert authorization.enabled_output_variants == ["horizontal-16x9-1080p"]
    gates = {gate.id: gate.status.value for gate in authorization.gates}
    assert gates["full-audio-animatic"] == "blocked"
    assert gates["representative-visual-qa"] == "blocked"
    assert gates["horizontal-calibration"] == "blocked"
    assert gates["vertical-calibration"] == "not-required-while-disabled"
    assert gates["enabled-matrix-24h-sla"] == "blocked"
    assert gates["exact-operator-production-authorization"] == "blocked"
    assert foundation.package_manifest.production_start_allowed is False
    historical_roles = {
        artifact.role for artifact in foundation.package_manifest.artifacts
    }
    assert {
        "typed-production-contracts",
        "authored-composition-profiles",
        "master-scene-builder",
        "foundation-documentation",
    }.issubset(historical_roles)


def test_contracts_reject_proof_variant_timeline_and_look_drift() -> None:
    acceptance = _payload(PRODUCTION_ROOT / "creative-acceptance.json")
    tampered_acceptance = copy.deepcopy(acceptance)
    proof = tampered_acceptance["proof"]
    assert isinstance(proof, dict)
    proof["authoritativeSceneSha256"] = "0" * 64
    with pytest.raises(ValidationError, match="immutable R13.1 proof"):
        OwnerAttestedCreativeAcceptance.model_validate(tampered_acceptance)

    altered_exclusions = copy.deepcopy(acceptance)
    exclusions = altered_exclusions["doesNotByItselfAuthorize"]
    assert isinstance(exclusions, list)
    exclusions[-1] = "starting production without Codex approval"
    with pytest.raises(ValidationError, match="technical exclusions"):
        OwnerAttestedCreativeAcceptance.model_validate(altered_exclusions)

    variants = _payload(PRODUCTION_ROOT / "output-variants.json")
    tampered_variants = copy.deepcopy(variants)
    variant_entries = tampered_variants["variants"]
    assert isinstance(variant_entries, list)
    variant_entries[1]["enabledByDefault"] = True
    with pytest.raises(ValidationError, match="disabled by default"):
        OutputVariantSet.model_validate(tampered_variants)

    shot_plan = _payload(SHOT_PATH)
    tampered_shots = copy.deepcopy(shot_plan)
    shots = tampered_shots["shots"]
    assert isinstance(shots, list)
    shots[19]["frameEnd"] = int(shots[19]["frameEnd"]) - 1
    shots[19]["durationFrames"] = int(shots[19]["durationFrames"]) - 1
    shots[19]["reviewFrames"][-1] = int(shots[19]["frameEnd"])
    with pytest.raises(ValidationError, match="contiguous"):
        ShotPlanV2.model_validate(tampered_shots)

    wrong_look = copy.deepcopy(shot_plan)
    wrong_look_shots = wrong_look["shots"]
    assert isinstance(wrong_look_shots, list)
    wrong_look_shots[-1]["lookProfileSha256"] = "f" * 64
    with pytest.raises(ValidationError, match="look-profile hash"):
        ShotPlanV2.model_validate(wrong_look)

    unbounded_audio = copy.deepcopy(shot_plan)
    unbounded_audio_shots = unbounded_audio["shots"]
    assert isinstance(unbounded_audio_shots, list)
    unbounded_audio_shots[0]["audioReactiveLayers"][0]["maximumInfluenceFraction"] = 0.8
    with pytest.raises(ValidationError, match="less than or equal to 0.25"):
        ShotPlanV2.model_validate(unbounded_audio)

    cropped_vertical = copy.deepcopy(shot_plan)
    cropped_vertical_shots = cropped_vertical["shots"]
    assert isinstance(cropped_vertical_shots, list)
    cropped_vertical_shots[0]["compositionOverrides"]["vertical"]["derivedByCrop"] = True
    with pytest.raises(ValidationError):
        ShotPlanV2.model_validate(cropped_vertical)

    fake_independent_composition = copy.deepcopy(shot_plan)
    fake_independent_shots = fake_independent_composition["shots"]
    assert isinstance(fake_independent_shots, list)
    first_overrides = fake_independent_shots[0]["compositionOverrides"]
    for field in (
        "cameraRigId",
        "lensMm",
        "framingIntent",
        "subjectOccupancyFraction",
        "foregroundPlacement",
        "safeZone",
        "titleSafeSpace",
    ):
        first_overrides["vertical"][field] = copy.deepcopy(first_overrides["horizontal"][field])
    with pytest.raises(ValidationError, match="substantive independent"):
        ShotPlanV2.model_validate(fake_independent_composition)


def test_templates_are_json_objects_with_no_private_paths() -> None:
    for path in (STORY_PATH, SHOT_PATH):
        payload = _payload(path)
        serialized = json.dumps(payload, sort_keys=True)
        assert "C:\\" not in serialized
        assert ".." not in serialized

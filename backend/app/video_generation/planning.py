from __future__ import annotations

from pathlib import Path

from .continuity import compile_reference_image, derive_shot_seed, load_continuity_profile
from .contracts import (
    CompiledPlan,
    CompiledShot,
    ContractError,
    load_creative_bible,
    load_project_config,
    load_shot_bank,
    require_shots,
)
from .costs import PRICE_SNAPSHOT_DATE, estimate, per_shot_cost, rate_for
from .jsonio import sha256_file
from .prompting import compile_prompt


def compile_project_plan(
    *,
    project_config_path: Path,
    creative_bible_path: Path,
    shot_bank_path: Path,
    gcs_bucket: str | None = None,
    analysis_job_id: str | None = None,
    audio_master_path: Path | None = None,
    story_plan_path: Path | None = None,
    shot_plan_path: Path | None = None,
    continuity_profile_path: Path | None = None,
    master_seed: int | None = None,
    seed_locked: bool | None = None,
    reference_image_path: Path | None = None,
) -> CompiledPlan:
    config = load_project_config(project_config_path)
    bible = load_creative_bible(creative_bible_path)
    shots = load_shot_bank(shot_bank_path)
    if bible.project_id != config.project_id:
        raise ContractError("project config and creative bible project IDs differ")
    if continuity_profile_path is None:
        continuity_profile_path = project_config_path.parent / "continuity-profile.json"
    if not continuity_profile_path.is_file():
        raise ContractError("a continuity profile is required")
    continuity = load_continuity_profile(continuity_profile_path)
    if continuity.project_id != config.project_id:
        raise ContractError("project config and continuity profile project IDs differ")
    selected_master_seed = continuity.master_seed if master_seed is None else master_seed
    if not 0 <= selected_master_seed <= 4_294_967_295:
        raise ContractError("masterSeed is outside uint32")

    selected = require_shots(shots, config.required_shot_ids)
    profile = config.selected_profile()
    if profile.resolution == "4k" and profile.model_id in {
        "veo-3.1-generate-001",
        "veo-3.1-fast-generate-001",
    }:
        raise ContractError(
            f"{profile.model_id} currently supports 720p/1080p through this Vertex API, not 4k; "
            "1080p is the supported final-delivery target"
        )
    estimate_result = estimate(profile, len(selected), config.retry_reserve_factor)
    if float(estimate_result.base_usd) > config.max_spend_usd:
        raise ContractError(
            "Base generation estimate exceeds maxSpendUsd: "
            f"${estimate_result.base_usd} > ${config.max_spend_usd:.2f}"
        )

    source_artifacts: dict[str, str] = {
        "projectConfigSha256": sha256_file(project_config_path),
        "creativeBibleSha256": sha256_file(creative_bible_path),
        "shotBankSha256": sha256_file(shot_bank_path),
        "continuityProfileSha256": sha256_file(continuity_profile_path),
    }
    if story_plan_path:
        source_artifacts["storyPlanSha256"] = sha256_file(story_plan_path)
    if shot_plan_path:
        source_artifacts["shotPlanSha256"] = sha256_file(shot_plan_path)
    if audio_master_path:
        source_artifacts["audioMasterSha256"] = sha256_file(audio_master_path)

    compiled: list[CompiledShot] = []
    cost_per_shot = float(per_shot_cost(profile))
    first_frame_reference = None
    if reference_image_path is not None:
        if not gcs_bucket:
            raise ContractError("a GCS bucket is required when a reference image is selected")
        first_frame_reference = compile_reference_image(
            path=reference_image_path,
            asset_id="primary-character-reference",
            gcs_bucket=gcs_bucket,
            storage_prefix=config.storage_prefix,
            project_id=config.project_id,
            source_kind="operator-selected-character-reference",
        )
        source_artifacts["primaryCharacterReferenceSha256"] = first_frame_reference.sha256
    for shot in selected:
        group_anchors = tuple(
            token
            for group_id in shot.continuity_group_ids
            for token in continuity.group(group_id).locked_tokens
        )
        prompt, negative = compile_prompt(bible, shot, continuity_anchors=group_anchors)
        storage_uri = None
        if gcs_bucket:
            normalized_bucket = gcs_bucket.removeprefix("gs://").strip("/")
            storage_uri = (
                f"gs://{normalized_bucket}/{config.storage_prefix}/{config.project_id}/{shot.shot_id}/"
            )
        compiled.append(
            CompiledShot(
                shot_id=shot.shot_id,
                chapter_id=shot.chapter_id,
                order=shot.order,
                title=shot.title,
                duration_seconds=profile.duration_seconds,
                prompt=prompt,
                negative_prompt=negative,
                seed=derive_shot_seed(
                    master_seed=selected_master_seed,
                    project_id=config.project_id,
                    continuity_group_ids=shot.continuity_group_ids,
                    shot_id=shot.shot_id,
                    variation_index=0,
                ),
                model_id=profile.model_id,
                resolution=profile.resolution,
                aspect_ratio=profile.aspect_ratio,
                sample_count=profile.sample_count,
                generate_audio=profile.generate_audio,
                enhance_prompt=profile.enhance_prompt,
                compression_quality=profile.compression_quality,
                person_generation=profile.person_generation,
                storage_uri=storage_uri,
                required=shot.required,
                estimated_cost_usd=cost_per_shot,
                source_section_hints=shot.source_section_hints,
                review_notes=shot.review_notes,
                variation_index=0,
                continuity_group_ids=shot.continuity_group_ids,
                previous_shot_id=shot.previous_shot_id,
                continuation_mode=(
                    "first-frame-reference" if first_frame_reference else shot.continuation_mode
                ),
                first_frame_reference=first_frame_reference,
            )
        )

    return CompiledPlan(
        schema_version=config.schema_version,
        project_id=config.project_id,
        title=config.title,
        profile=profile,
        shots=tuple(compiled),
        base_estimated_cost_usd=float(estimate_result.base_usd),
        conservative_estimated_cost_usd=float(estimate_result.conservative_usd),
        max_spend_usd=config.max_spend_usd,
        source_artifacts=source_artifacts,
        analysis_job_id=analysis_job_id,
        pricing_snapshot_date=PRICE_SNAPSHOT_DATE,
        rate_usd_per_output_second=float(rate_for(profile)),
        continuity=continuity.to_plan_dict(
            master_seed=selected_master_seed,
            seed_locked=seed_locked,
        ),
    ).with_digest()

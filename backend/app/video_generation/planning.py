from __future__ import annotations

from pathlib import Path

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
) -> CompiledPlan:
    config = load_project_config(project_config_path)
    bible = load_creative_bible(creative_bible_path)
    shots = load_shot_bank(shot_bank_path)
    if bible.project_id != config.project_id:
        raise ContractError("project config and creative bible project IDs differ")

    selected = require_shots(shots, config.required_shot_ids)
    profile = config.selected_profile()
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
    }
    if story_plan_path:
        source_artifacts["storyPlanSha256"] = sha256_file(story_plan_path)
    if shot_plan_path:
        source_artifacts["shotPlanSha256"] = sha256_file(shot_plan_path)
    if audio_master_path:
        source_artifacts["audioMasterSha256"] = sha256_file(audio_master_path)

    compiled: list[CompiledShot] = []
    cost_per_shot = float(per_shot_cost(profile))
    for shot in selected:
        prompt, negative = compile_prompt(bible, shot)
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
                seed=shot.seed,
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
    ).with_digest()

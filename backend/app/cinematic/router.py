from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request, Response

from ..analysis.sanity import validate_analysis_result
from ..schemas import AnalysisResult, JobStatus
from ..visualizer.compiler import VisualCueCompilationError, compile_visual_cues
from ..visualizer.presets import (
    SpaceJourneyStoryResolvedVisualizerConfig,
    resolve_visualizer_config,
)
from ..visualizer.schemas import VisualFeatureArtifact
from .compiler import compile_cinematic_plan
from .schemas import (
    ArtDirectionReview,
    ArtDirectionReviewCollection,
    CinematicCompileRequest,
    CinematicPlanBundle,
)
from .store import read_plan_bundle, read_reviews, write_plan_bundle, write_reviews

router = APIRouter(prefix="/api/analyses/{job_id}/cinematic", tags=["cinematic"])


class CinematicAPIError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        safe_message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.status_code = status_code
        self.code = code
        self.safe_message = safe_message
        self.details = details


async def _compile(job_id: str, request: Request, payload: CinematicCompileRequest) -> CinematicPlanBundle:
    store = request.app.state.store
    manager = request.app.state.manager
    try:
        async with manager.job_lock(job_id):
            record = await asyncio.to_thread(store.get_job, job_id)
            if record is None:
                raise CinematicAPIError(404, "job_not_found", "Analysis job was not found or has expired.")
            if record.status != JobStatus.COMPLETED:
                raise CinematicAPIError(409, "analysis_not_ready", "Analysis must complete before cinematic planning.")
            analysis_payload = await asyncio.to_thread(store.read_json, job_id, "analysis.json")
            feature_payload = await asyncio.to_thread(store.read_json, job_id, "visual-features.json")
            if analysis_payload is None:
                raise CinematicAPIError(409, "analysis_not_ready", "Analysis must complete before cinematic planning.")
            analysis = validate_analysis_result(AnalysisResult.model_validate(analysis_payload))
            try:
                features = VisualFeatureArtifact.model_validate(feature_payload) if feature_payload else None
                cues = await asyncio.to_thread(
                    compile_visual_cues,
                    analysis,
                    features,
                    payload.cue_preferences,
                )
            except VisualCueCompilationError as exc:
                status = 409 if exc.code == "visual_features_unavailable" else 422
                raise CinematicAPIError(status, exc.code, exc.safe_message) from exc
            except ValueError as exc:
                raise CinematicAPIError(422, "cue_compilation_failed", "Visual cues could not be compiled safely.") from exc
            resolved = resolve_visualizer_config(payload.visualizer_config)
            if not isinstance(resolved, SpaceJourneyStoryResolvedVisualizerConfig):
                raise CinematicAPIError(422, "v2_preset_required", "Cinematic planning requires space-journey-story.")
            story, shots = await asyncio.to_thread(compile_cinematic_plan, cues, resolved)
            bundle = CinematicPlanBundle(story_plan=story, shot_plan=shots)
            try:
                await asyncio.to_thread(write_plan_bundle, store, job_id, bundle)
            except OSError as exc:
                raise CinematicAPIError(503, "cinematic_storage_failed", "Cinematic plans could not be stored safely.") from exc
            return bundle
    except KeyError as exc:
        raise CinematicAPIError(404, "job_not_found", "Analysis job was not found or has expired.") from exc


@router.post("/plan", response_model=CinematicPlanBundle)
async def compile_plan(
    job_id: str,
    payload: CinematicCompileRequest,
    request: Request,
) -> CinematicPlanBundle:
    return await _compile(job_id, request, payload)


@router.get("/plan", response_model=CinematicPlanBundle)
async def get_plan(job_id: str, request: Request) -> CinematicPlanBundle:
    store = request.app.state.store
    try:
        if await asyncio.to_thread(store.get_job, job_id) is None:
            raise CinematicAPIError(404, "job_not_found", "Analysis job was not found or has expired.")
        bundle = await asyncio.to_thread(read_plan_bundle, store, job_id)
    except ValueError as exc:
        raise CinematicAPIError(422, "invalid_cinematic_plan", "Stored cinematic plans failed validation.") from exc
    if bundle is None:
        raise CinematicAPIError(409, "cinematic_plan_not_ready", "Compile the cinematic plan before reading it.")
    return bundle


@router.get("/story-plan/export")
async def export_story_plan(job_id: str, request: Request) -> Response:
    bundle = await get_plan(job_id, request)
    content = json.dumps(
        bundle.story_plan.model_dump(mode="json", by_alias=True),
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="trackprompt-{job_id}-story-plan.json"'},
    )


@router.get("/shot-plan/export")
async def export_shot_plan(job_id: str, request: Request) -> Response:
    bundle = await get_plan(job_id, request)
    content = json.dumps(
        bundle.shot_plan.model_dump(mode="json", by_alias=True),
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="trackprompt-{job_id}-shot-plan.json"'},
    )


@router.get("/reviews", response_model=ArtDirectionReviewCollection)
async def list_reviews(job_id: str, request: Request) -> ArtDirectionReviewCollection:
    store = request.app.state.store
    if await asyncio.to_thread(store.get_job, job_id) is None:
        raise CinematicAPIError(404, "job_not_found", "Analysis job was not found or has expired.")
    try:
        return await asyncio.to_thread(read_reviews, store, job_id)
    except ValueError as exc:
        raise CinematicAPIError(422, "invalid_cinematic_review", "Stored cinematic reviews failed validation.") from exc


@router.put("/reviews/{shot_id}", response_model=ArtDirectionReviewCollection)
async def put_review(
    job_id: str,
    shot_id: str,
    review: ArtDirectionReview,
    request: Request,
) -> ArtDirectionReviewCollection:
    if shot_id != review.shot_id:
        raise CinematicAPIError(422, "shot_id_mismatch", "Review shot ID does not match the route.")
    bundle = await get_plan(job_id, request)
    shot = next((candidate for candidate in bundle.shot_plan.shots if candidate.id == shot_id), None)
    if shot is None or not shot.frame_start <= review.review_frame <= shot.frame_end:
        raise CinematicAPIError(422, "invalid_review_frame", "Review frame must belong to the selected shot.")
    current = await list_reviews(job_id, request)
    reviews = [item for item in current.reviews if item.shot_id != shot_id]
    reviews.append(review)
    updated = ArtDirectionReviewCollection(reviews=sorted(reviews, key=lambda item: item.shot_id))
    try:
        await asyncio.to_thread(write_reviews, request.app.state.store, job_id, updated)
    except OSError as exc:
        raise CinematicAPIError(503, "cinematic_review_storage_failed", "Cinematic review could not be stored safely.") from exc
    return updated

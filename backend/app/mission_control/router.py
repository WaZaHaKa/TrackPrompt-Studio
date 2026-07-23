from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from ..cinematic.schemas import ArtDirectionReview
from .config import MissionControlConfig
from .errors import MissionControlError
from .models import (
    AuthorizationRequest,
    AuthorizationResult,
    CalibrationCandidateRunRequest,
    CalibrationPlanRequest,
    CalibrationPlanResult,
    CalibrationReviewRequest,
    CalibrationSummary,
    CancelStopRequest,
    CapabilityActionResult,
    CloudPackageRequest,
    CloudReadiness,
    CloudValidateRequest,
    DirectorWorkspace,
    DryRunResult,
    EncodeJobStatus,
    EncodeReadiness,
    EncodeStartRequest,
    ErrorEnvelope,
    JobRecord,
    LogPage,
    MissionSettings,
    MissionSettingsPatch,
    NativePickerRequest,
    NativePickerResponse,
    OpenPathRequest,
    OpenPathResult,
    OutputCreateChildRequest,
    OutputCreateChildResult,
    OutputInspection,
    OutputInspectRequest,
    PerformanceEnableRequest,
    PerformanceRestoreRequest,
    PerformanceStatus,
    PreflightRequest,
    PreflightResult,
    ProfileSummary,
    ProfileValidation,
    ProjectSummary,
    ResumeRequest,
    RuntimeIdentity,
    SceneSummary,
    StartFakeRenderRequest,
    StartRenderRequest,
    StructuredError,
    SystemHealth,
    SystemPaths,
    SystemStatus,
)
from .service import MissionControlService

router = APIRouter(prefix="/api/mission-control", tags=["mission-control"])


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _service(request: Request) -> MissionControlService:
    existing = getattr(request.app.state, "mission_control_service", None)
    if isinstance(existing, MissionControlService):
        existing.start_background_tasks()
        return existing
    settings = getattr(request.app.state, "settings", None)
    configured_data_dir = getattr(settings, "data_dir", None)
    state_root = (
        Path(configured_data_dir) / "mission-control"
        if configured_data_dir is not None
        else None
    )
    config = MissionControlConfig.from_repository(
        _repository_root(),
        state_root=state_root,
        native_dialog_enabled=(
            os.getenv("TRACKPROMPT_MC_DISABLE_NATIVE_DIALOG", "").strip().lower()
            not in {"1", "true", "yes", "on"}
        ),
    )
    runtime_value = getattr(request.app.state, "mission_control_runtime", None)
    runtime = (
        runtime_value
        if isinstance(runtime_value, RuntimeIdentity)
        else RuntimeIdentity.model_validate(runtime_value)
        if isinstance(runtime_value, dict)
        else None
    )
    created = MissionControlService(config, runtime=runtime)
    request.app.state.mission_control_service = created
    return created


async def mission_control_error_handler(
    _request: Request,
    exc: MissionControlError,
) -> JSONResponse:
    payload = ErrorEnvelope(error=exc.error).model_dump(mode="json", by_alias=True)
    return JSONResponse(status_code=exc.status_code, content=payload)


async def mission_control_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    if not request.url.path.startswith("/api/mission-control"):
        return await request_validation_exception_handler(request, exc)
    error = StructuredError(
        code="request_validation_failed",
        title="Request validation failed",
        summary="The request body, path, or query parameters do not match the Mission Control API contract.",
        likely_cause="One or more required fields are missing or use an unsupported value.",
        recommended_action="Review the request fields and retry with the documented types.",
        retryable=False,
        context={"errorCount": len(exc.errors())},
        timestamp=datetime.now(UTC),
    )
    payload = ErrorEnvelope(error=error).model_dump(mode="json", by_alias=True)
    return JSONResponse(status_code=422, content=payload)


def install_mission_control(application: FastAPI) -> None:
    if not any(
        str(getattr(route, "path", "")).startswith("/api/mission-control")
        for route in application.routes
    ):
        application.include_router(router)
    application.add_exception_handler(
        MissionControlError,
        cast(Any, mission_control_error_handler),
    )
    application.add_exception_handler(
        RequestValidationError,
        cast(Any, mission_control_validation_error_handler),
    )


@router.get("/health", response_model=SystemHealth)
async def health(request: Request) -> SystemHealth:
    return _service(request).health()


@router.get("/system/status", response_model=SystemStatus)
async def system_status(request: Request) -> SystemStatus:
    return await _service(request).system_status()


@router.get("/system/paths", response_model=SystemPaths)
async def system_paths(request: Request) -> SystemPaths:
    return _service(request).paths()


@router.get("/system/settings", response_model=MissionSettings)
async def get_settings(request: Request) -> MissionSettings:
    return await _service(request).settings()


@router.patch("/system/settings", response_model=MissionSettings)
async def patch_settings(
    payload: MissionSettingsPatch,
    request: Request,
) -> MissionSettings:
    return await _service(request).patch_settings(payload)


@router.post("/system/select-folder", response_model=NativePickerResponse)
async def select_folder(
    payload: NativePickerRequest,
    request: Request,
) -> NativePickerResponse:
    return await _service(request).select_folder(payload)


@router.post("/system/select-file", response_model=NativePickerResponse)
async def select_file(
    payload: NativePickerRequest,
    request: Request,
) -> NativePickerResponse:
    return await _service(request).select_file(payload)


@router.post("/system/open-path", response_model=OpenPathResult)
async def open_path(
    payload: OpenPathRequest,
    request: Request,
) -> OpenPathResult:
    return await _service(request).open_path(payload)


@router.get("/projects", response_model=list[ProjectSummary])
async def projects(request: Request) -> list[ProjectSummary]:
    return _service(request).projects()


@router.get("/scenes", response_model=list[SceneSummary])
async def scenes(request: Request) -> list[SceneSummary]:
    return _service(request).scenes()


@router.get("/scenes/{scene_id}", response_model=SceneSummary)
async def scene(scene_id: str, request: Request) -> SceneSummary:
    return _service(request).scene(scene_id)


@router.get("/profiles", response_model=list[ProfileSummary])
async def profiles(request: Request) -> list[ProfileSummary]:
    return _service(request).profiles()


@router.get("/profiles/{profile_id}", response_model=ProfileSummary)
async def profile(profile_id: str, request: Request) -> ProfileSummary:
    return _service(request).profile(profile_id)


@router.post("/profiles/{profile_id}/validate", response_model=ProfileValidation)
async def validate_profile(profile_id: str, request: Request) -> ProfileValidation:
    return _service(request).validate_profile(profile_id)


@router.post("/profiles/{profile_id}/authorize", response_model=AuthorizationResult)
async def authorize_profile(
    profile_id: str,
    payload: AuthorizationRequest,
    request: Request,
) -> AuthorizationResult:
    return _service(request).authorize_profile(
        profile_id,
        payload.scene_id,
        settings_and_hashes_reviewed=payload.settings_and_hashes_reviewed,
        production_render_authorized=payload.production_render_authorized,
    )


@router.post("/output/inspect", response_model=OutputInspection)
async def inspect_output(
    payload: OutputInspectRequest,
    request: Request,
) -> OutputInspection:
    return _service(request).inspect_output(
        payload.path,
        profile_id=payload.profile_id,
        scene_id=payload.scene_id,
    )


@router.post("/output/create-child", response_model=OutputCreateChildResult)
async def create_output_child(
    payload: OutputCreateChildRequest,
    request: Request,
) -> OutputCreateChildResult:
    return _service(request).create_output_child(
        payload.parent_directory,
        project_id=payload.project_id,
        profile_id=payload.profile_id,
        base_name=payload.base_name,
    )


@router.post("/render/preflight", response_model=PreflightResult)
async def render_preflight(
    payload: PreflightRequest,
    request: Request,
) -> PreflightResult:
    return await _service(request).preflight(payload)


@router.post("/render/dry-run", response_model=DryRunResult)
async def render_dry_run(
    payload: PreflightRequest,
    request: Request,
) -> DryRunResult:
    return await _service(request).dry_run(payload)


@router.post("/render/start", response_model=JobRecord, status_code=202)
async def render_start(
    payload: StartRenderRequest | StartFakeRenderRequest,
    request: Request,
) -> JobRecord:
    return await _service(request).start_render(
        payload,
        fake_options=payload.fake if isinstance(payload, StartFakeRenderRequest) else None,
    )


@router.get("/render/{job_id}", response_model=JobRecord)
async def render_job(job_id: str, request: Request) -> JobRecord:
    return _service(request).get_job(job_id)


@router.get("/jobs", response_model=list[JobRecord])
async def jobs(request: Request) -> list[JobRecord]:
    return _service(request).jobs()


@router.get("/director/workspace", response_model=DirectorWorkspace | None)
async def director_workspace(request: Request) -> DirectorWorkspace | None:
    return _service(request).director_workspace()


@router.put(
    "/director/workspace/{analysis_job_id}/reviews/{shot_id}",
    response_model=DirectorWorkspace,
)
async def put_director_review(
    analysis_job_id: str,
    shot_id: str,
    payload: ArtDirectionReview,
    request: Request,
) -> DirectorWorkspace:
    return _service(request).put_director_review(analysis_job_id, shot_id, payload)


@router.get("/render/{job_id}/logs", response_model=LogPage)
async def render_logs(
    job_id: str,
    request: Request,
    after_sequence: Annotated[int, Query(alias="afterSequence", ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2_000)] = 200,
) -> LogPage:
    return _service(request).logs(job_id, after_sequence=after_sequence, limit=limit)


@router.get("/render/{job_id}/preview")
async def render_preview(
    job_id: str,
    request: Request,
    version: Annotated[int | None, Query(alias="v", ge=1)] = None,
    output_variant_id: Annotated[
        str | None,
        Query(alias="output_variant_id", min_length=1, max_length=160),
    ] = None,
) -> Response:
    path = _service(request).preview_path(
        job_id,
        frame=version,
        output_variant_id=output_variant_id,
    )
    if path is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    preview_frame = int(path.stem.removeprefix("frame_"))
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-TrackPrompt-Preview-Frame": str(preview_frame),
        },
    )


@router.get("/render/{job_id}/frame")
async def render_full_frame(
    job_id: str,
    request: Request,
    version: Annotated[int | None, Query(alias="v", ge=1)] = None,
    output_variant_id: Annotated[
        str | None,
        Query(alias="output_variant_id", min_length=1, max_length=160),
    ] = None,
) -> Response:
    service = _service(request)
    path = service.full_frame_path(
        job_id,
        frame=version,
        output_variant_id=output_variant_id,
    )
    if path is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    job = service.get_job(job_id)
    frame = version if version is not None else job.latest_preview_frame
    variant = next(
        (
            item
            for item in job.output_variants
            if item.enabled and item.id == output_variant_id
        ),
        None,
    )
    safe = bool(
        frame is not None
        and (
            (
                variant is not None
                and variant.progress.latest_safe_frame is not None
                and frame <= variant.progress.latest_safe_frame
            )
            or (
                variant is None
                and frame
                <= job.frame_start + max(0, job.published_frame_count) - 1
                and path.parent.name == "frames"
                and path.parent.parent == Path(job.identity.output_directory)
            )
        )
    )
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-TrackPrompt-Frame-Safety": (
                "validated-published" if safe else "rendered-inflight"
            ),
            "Content-Disposition": f'inline; filename="{path.name}"',
        },
    )


@router.post("/render/{job_id}/stop-after-chunk", response_model=JobRecord)
async def stop_after_chunk(job_id: str, request: Request) -> JobRecord:
    return await _service(request).request_stop_after_chunk(job_id)


@router.post("/render/{job_id}/cancel-stop", response_model=JobRecord)
async def cancel_stop(
    job_id: str,
    payload: CancelStopRequest,
    request: Request,
) -> JobRecord:
    return await _service(request).cancel_stop(job_id, payload)


@router.post("/render/{job_id}/resume", response_model=JobRecord)
async def resume_render(
    job_id: str,
    payload: ResumeRequest,
    request: Request,
) -> JobRecord:
    return await _service(request).resume(job_id, payload)


@router.get("/events")
async def events(
    request: Request,
    after_sequence: Annotated[int, Query(alias="afterSequence", ge=0)] = 0,
    job_id: Annotated[str | None, Query(alias="jobId")] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    if last_event_id is not None:
        try:
            after_sequence = max(after_sequence, int(last_event_id))
        except ValueError:
            pass
    service = _service(request)

    async def stream() -> AsyncIterator[str]:
        sequence = after_sequence
        while True:
            if await request.is_disconnected():
                return
            observed_generation = service.event_generation
            replay = service.events_after(sequence, job_id=job_id)
            if replay:
                for event in replay:
                    sequence = event.sequence
                    data = json.dumps(
                        event.model_dump(mode="json", by_alias=True),
                        separators=(",", ":"),
                    )
                    yield f"id: {event.sequence}\nevent: render\ndata: {data}\n\n"
                continue
            yield ": heartbeat\n\n"
            await service.wait_for_events(observed_generation)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/calibrations", response_model=list[CalibrationSummary])
async def calibrations(request: Request) -> list[CalibrationSummary]:
    return _service(request).calibrations()


@router.get("/calibrations/{calibration_id}", response_model=CalibrationSummary)
async def calibration(calibration_id: str, request: Request) -> CalibrationSummary:
    return _service(request).calibration(calibration_id)


@router.post("/calibrations/plan", response_model=CalibrationPlanResult)
async def calibration_plan(
    payload: CalibrationPlanRequest,
    request: Request,
) -> CalibrationPlanResult:
    return _service(request).plan_calibration(payload)


@router.post(
    "/calibrations/{calibration_id}/run-candidate",
    response_model=CapabilityActionResult,
)
async def calibration_run_candidate(
    calibration_id: str,
    payload: CalibrationCandidateRunRequest,
    request: Request,
) -> CapabilityActionResult:
    _service(request).calibration(calibration_id)
    _ = payload
    return CapabilityActionResult(
        available=False,
        detail="Bounded candidate execution remains in the validated PowerShell calibration workflow; no Blender process was started.",
    )


@router.post(
    "/calibrations/{calibration_id}/review",
    response_model=CapabilityActionResult,
)
async def calibration_review(
    calibration_id: str,
    payload: CalibrationReviewRequest,
    request: Request,
) -> CapabilityActionResult:
    _service(request).calibration(calibration_id)
    _ = payload
    return CapabilityActionResult(
        available=False,
        detail="Candidate review mutation remains disabled until the bounded calibration adapter is connected.",
    )


@router.get("/cloud/readiness", response_model=CloudReadiness)
async def cloud_readiness(request: Request) -> CloudReadiness:
    return _service(request).cloud_readiness()


@router.post("/cloud/package", response_model=CapabilityActionResult)
async def cloud_package(
    payload: CloudPackageRequest,
    request: Request,
) -> CapabilityActionResult:
    _ = payload
    readiness = _service(request).cloud_readiness()
    return CapabilityActionResult(
        available=False,
        detail=(
            "Offline package tooling is present, but package mutation is disabled until its privacy confirmations are wired."
            if readiness.offline_preparation_available
            else "Offline package tooling is unavailable."
        ),
    )


@router.post("/cloud/validate", response_model=CapabilityActionResult)
async def cloud_validate(
    payload: CloudValidateRequest,
    request: Request,
) -> CapabilityActionResult:
    _ = payload
    readiness = _service(request).cloud_readiness()
    return CapabilityActionResult(
        available=False,
        detail=(
            "Package validation tooling is present, but the server-managed package registry is not connected."
            if readiness.package_validation_available
            else "Package validation tooling is unavailable."
        ),
    )


@router.get("/encode/{job_id}/readiness", response_model=EncodeReadiness)
async def encode_readiness(job_id: str, request: Request) -> EncodeReadiness:
    return _service(request).encode_readiness(job_id)


@router.get("/encode/{job_id}", response_model=EncodeJobStatus)
async def encode_status(job_id: str, request: Request) -> EncodeJobStatus:
    return _service(request).encode_status(job_id)


@router.post("/encode/{job_id}/start", response_model=EncodeJobStatus)
async def encode_start(
    job_id: str,
    payload: EncodeStartRequest,
    request: Request,
) -> EncodeJobStatus:
    return await _service(request).start_encode(job_id, payload)


@router.get("/performance/status", response_model=PerformanceStatus)
async def performance_status(request: Request) -> PerformanceStatus:
    return await _service(request).performance_status()


@router.post("/performance/enable", response_model=PerformanceStatus)
async def performance_enable(
    payload: PerformanceEnableRequest,
    request: Request,
) -> PerformanceStatus:
    return await _service(request).performance_enable(payload)


@router.post("/performance/restore", response_model=PerformanceStatus)
async def performance_restore(
    payload: PerformanceRestoreRequest,
    request: Request,
) -> PerformanceStatus:
    return await _service(request).performance_restore(payload)

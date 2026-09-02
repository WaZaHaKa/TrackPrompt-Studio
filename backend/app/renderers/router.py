from __future__ import annotations

import asyncio
from typing import cast

from fastapi import APIRouter, Request

from .registry import RendererRegistry
from .schemas import (
    RendererDescriptor,
    RendererRegistryResponse,
    SpectrumCapturePreflightRequest,
    SpectrumProductionCancelRequest,
    SpectrumProductionStartRequest,
    SpectrumWorkspaceJob,
    SpectrumWorkspacePrepareRequest,
)
from .wzhk_spectrum.workspace import SpectrumWorkspaceError


class RendererAPIError(RuntimeError):
    def __init__(self, status_code: int, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.status_code = status_code
        self.code = code
        self.safe_message = safe_message


router = APIRouter(prefix="/api/renderers", tags=["renderers"])


def _registry(request: Request) -> RendererRegistry:
    return cast(RendererRegistry, request.app.state.renderer_registry)


@router.get("", response_model=RendererRegistryResponse)
async def list_renderers(request: Request) -> RendererRegistryResponse:
    return await asyncio.to_thread(_registry(request).list)


@router.get("/{renderer_id}", response_model=RendererDescriptor)
async def get_renderer(request: Request, renderer_id: str) -> RendererDescriptor:
    try:
        return await asyncio.to_thread(_registry(request).get, renderer_id)
    except KeyError as exc:
        raise RendererAPIError(
            404,
            "renderer_not_found",
            "The requested renderer was not found.",
        ) from exc


@router.post(
    "/wzhk-spectrum/jobs",
    response_model=SpectrumWorkspaceJob,
    status_code=201,
)
async def prepare_wzhk_spectrum_workspace(
    request: Request,
    payload: SpectrumWorkspacePrepareRequest,
) -> SpectrumWorkspaceJob:
    try:
        return await asyncio.to_thread(
            _registry(request).prepare_wzhk_spectrum,
            payload,
        )
    except SpectrumWorkspaceError as exc:
        raise RendererAPIError(
            409,
            "wzhk_spectrum_workspace_not_ready",
            str(exc),
        ) from exc


@router.get(
    "/wzhk-spectrum/jobs/{job_id}",
    response_model=SpectrumWorkspaceJob,
)
async def get_wzhk_spectrum_workspace(
    request: Request,
    job_id: str,
) -> SpectrumWorkspaceJob:
    try:
        return await asyncio.to_thread(
            _registry(request).get_wzhk_spectrum_job,
            job_id,
        )
    except SpectrumWorkspaceError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 422
        raise RendererAPIError(
            status_code,
            "wzhk_spectrum_workspace_unavailable",
            str(exc),
        ) from exc


@router.post(
    "/wzhk-spectrum/jobs/{job_id}/capture-preflight",
    response_model=SpectrumWorkspaceJob,
)
async def preflight_wzhk_spectrum_capture(
    request: Request,
    job_id: str,
    _payload: SpectrumCapturePreflightRequest,
) -> SpectrumWorkspaceJob:
    try:
        return await asyncio.to_thread(
            _registry(request).preflight_wzhk_spectrum_capture,
            job_id,
        )
    except SpectrumWorkspaceError as exc:
        raise RendererAPIError(
            409,
            "wzhk_spectrum_capture_preflight_failed",
            str(exc),
        ) from exc


@router.post(
    "/wzhk-spectrum/jobs/{job_id}/production",
    response_model=SpectrumWorkspaceJob,
    status_code=202,
)
async def start_wzhk_spectrum_production(
    request: Request,
    job_id: str,
    _payload: SpectrumProductionStartRequest,
) -> SpectrumWorkspaceJob:
    try:
        return await asyncio.to_thread(
            _registry(request).start_wzhk_spectrum_production,
            job_id,
        )
    except SpectrumWorkspaceError as exc:
        raise RendererAPIError(
            409,
            "wzhk_spectrum_production_not_ready",
            str(exc),
        ) from exc


@router.post(
    "/wzhk-spectrum/jobs/{job_id}/cancel",
    response_model=SpectrumWorkspaceJob,
)
async def cancel_wzhk_spectrum_production(
    request: Request,
    job_id: str,
    payload: SpectrumProductionCancelRequest,
) -> SpectrumWorkspaceJob:
    try:
        return await asyncio.to_thread(
            _registry(request).cancel_wzhk_spectrum_production,
            job_id,
            payload.reason,
        )
    except SpectrumWorkspaceError as exc:
        raise RendererAPIError(
            409,
            "wzhk_spectrum_production_not_active",
            str(exc),
        ) from exc

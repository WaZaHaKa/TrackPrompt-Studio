from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response

from ..media import MediaValidationError, probe_media
from ..privacy import secure_private_directory, secure_private_file
from ..schemas import Confidence
from .reports import build_mastering_report, report_csv, report_json, report_markdown
from .schemas import (
    ArtifactResponse,
    AuditEventResponse,
    AuditVerificationResponse,
    BatchCreate,
    BatchPatch,
    BatchResponse,
    BatchState,
    ClientCreate,
    ClientPatch,
    ClientResponse,
    ManualBoundaryImport,
    MasteringReport,
    Page,
    ProjectCreate,
    ProjectDeletionResponse,
    ProjectPatch,
    ProjectResponse,
    QueueItemResponse,
    QueueState,
    ReviewState,
    RevisionResponse,
    SegmentationJobResponse,
    SegmentationRequest,
    SegmentationResponse,
    SegmentBoundaryInput,
    SegmentEditRequest,
    SegmentReplaceRequest,
    SegmentResponse,
    SourceAssetResponse,
    TransitionType,
    UploadChunkResponse,
    UploadSessionCreate,
    UploadSessionResponse,
    UploadState,
)
from .segmentation import segment_longform_source
from .store import CatalogueConflict, CatalogueStore, StorageAdmissionError

router = APIRouter(prefix="/api")
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)$")


class CatalogueAPIError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.safe_message = message
        self.details = details


def _store(request: Request) -> CatalogueStore:
    return cast(CatalogueStore, request.app.state.catalog_store)


def _page(items: list[Any], total: int, offset: int, limit: int) -> Page:
    return Page(items=items, total=total, offset=offset, limit=limit)


def _queue_response(item: dict[str, Any]) -> QueueItemResponse:
    public = {
        field_name: item[field_name]
        for field_name in QueueItemResponse.model_fields
        if field_name in item
    }
    return QueueItemResponse.model_validate(public)


@router.post("/clients", response_model=ClientResponse, status_code=201)
async def create_client(payload: ClientCreate, request: Request) -> ClientResponse:
    return ClientResponse.model_validate(await asyncio.to_thread(_store(request).create_client, payload))


@router.get("/clients", response_model=Page)
async def list_clients(
    request: Request,
    search: Annotated[str, Query(max_length=160)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page:
    items, total = await asyncio.to_thread(
        _store(request).list_clients, search=search, offset=offset, limit=limit
    )
    return _page([ClientResponse.model_validate(item) for item in items], total, offset, limit)


@router.get("/clients/{client_id}", response_model=ClientResponse)
async def get_client(client_id: str, request: Request) -> ClientResponse:
    return ClientResponse.model_validate(await asyncio.to_thread(_store(request).get_client, client_id))


@router.patch("/clients/{client_id}", response_model=ClientResponse)
async def patch_client(client_id: str, payload: ClientPatch, request: Request) -> ClientResponse:
    return ClientResponse.model_validate(
        await asyncio.to_thread(_store(request).patch_client, client_id, payload)
    )


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(payload: ProjectCreate, request: Request) -> ProjectResponse:
    return ProjectResponse.model_validate(await asyncio.to_thread(_store(request).create_project, payload))


@router.get("/clients/{client_id}/projects", response_model=Page)
async def list_projects(
    client_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page:
    items, total = await asyncio.to_thread(
        _store(request).list_projects, client_id, offset=offset, limit=limit
    )
    return _page([ProjectResponse.model_validate(item) for item in items], total, offset, limit)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, request: Request) -> ProjectResponse:
    return ProjectResponse.model_validate(await asyncio.to_thread(_store(request).get_project, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def patch_project(project_id: str, payload: ProjectPatch, request: Request) -> ProjectResponse:
    return ProjectResponse.model_validate(
        await asyncio.to_thread(_store(request).patch_project, project_id, payload)
    )


@router.delete("/projects/{project_id}", response_model=ProjectDeletionResponse)
async def delete_project(
    project_id: str,
    request: Request,
    confirm: Annotated[bool, Query()] = False,
) -> ProjectDeletionResponse:
    if not confirm:
        raise CatalogueAPIError(
            400,
            "deletion_confirmation_required",
            "Permanent project deletion requires confirm=true.",
        )
    try:
        result = await asyncio.to_thread(_store(request).delete_project, project_id)
    except CatalogueConflict as exc:
        raise CatalogueAPIError(409, "project_delete_conflict", str(exc)) from exc
    return ProjectDeletionResponse.model_validate(result)


@router.post("/projects/{project_id}/batches", response_model=BatchResponse, status_code=201)
async def create_batch(project_id: str, payload: BatchCreate, request: Request) -> BatchResponse:
    return BatchResponse.model_validate(
        await asyncio.to_thread(_store(request).create_batch, project_id, payload)
    )


@router.get("/projects/{project_id}/batches", response_model=Page)
async def list_batches(
    project_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page:
    items, total = await asyncio.to_thread(
        _store(request).list_batches, project_id, offset=offset, limit=limit
    )
    return _page([BatchResponse.model_validate(item) for item in items], total, offset, limit)


@router.get("/batches/{batch_id}", response_model=BatchResponse)
async def get_batch(batch_id: str, request: Request) -> BatchResponse:
    return BatchResponse.model_validate(await asyncio.to_thread(_store(request).get_batch, batch_id))


@router.patch("/batches/{batch_id}", response_model=BatchResponse)
async def patch_batch(batch_id: str, payload: BatchPatch, request: Request) -> BatchResponse:
    return BatchResponse.model_validate(
        await asyncio.to_thread(_store(request).patch_batch, batch_id, payload)
    )


@router.post("/upload-sessions", response_model=UploadSessionResponse, status_code=201)
async def create_upload_session(
    payload: UploadSessionCreate,
    request: Request,
) -> UploadSessionResponse:
    if not payload.permission_confirmed:
        raise CatalogueAPIError(
            400,
            "permission_required",
            "Confirm permission to archive and analyze this audio before creating an upload session.",
        )
    try:
        result = await asyncio.to_thread(_store(request).create_upload_session, payload)
    except StorageAdmissionError as exc:
        raise CatalogueAPIError(507, "storage_admission_failed", str(exc)) from exc
    return UploadSessionResponse.model_validate(result)


@router.get("/upload-sessions/{upload_id}", response_model=UploadSessionResponse)
async def get_upload_session(upload_id: str, request: Request) -> UploadSessionResponse:
    return UploadSessionResponse.model_validate(
        await asyncio.to_thread(_store(request).get_upload_session, upload_id)
    )


@router.patch("/upload-sessions/{upload_id}", response_model=UploadChunkResponse)
async def upload_chunk(
    upload_id: str,
    request: Request,
    content_range: Annotated[str, Header(alias="Content-Range")],
    upload_offset: Annotated[int, Header(alias="Upload-Offset", ge=0)],
    supplied_chunk_sha256: Annotated[str | None, Header(alias="X-Chunk-SHA256")] = None,
) -> UploadChunkResponse:
    store = _store(request)
    session = await asyncio.to_thread(store.get_upload_session, upload_id)
    match = _CONTENT_RANGE.fullmatch(content_range.strip())
    if match is None:
        raise CatalogueAPIError(400, "invalid_content_range", "Content-Range must use bytes start-end/total.")
    start, end, total = (int(value) for value in match.groups())
    if start != upload_offset or total != int(session["total_bytes"]) or end < start:
        raise CatalogueAPIError(409, "upload_range_mismatch", "The chunk range does not match the upload session.")
    expected_length = end - start + 1
    if expected_length > store.settings.resumable_upload_chunk_bytes:
        raise CatalogueAPIError(413, "chunk_too_large", "The chunk exceeds the configured resumable-upload chunk size.")
    if start != int(session["received_bytes"]):
        raise CatalogueAPIError(
            409,
            "upload_offset_mismatch",
            "Chunks must be appended at the next durable byte offset.",
            {"expectedOffset": int(session["received_bytes"])},
        )
    path = store.upload_dir(upload_id) / "source.partial"
    digest = hashlib.sha256()
    received = 0
    try:
        with path.open("r+b") as output:
            output.seek(start)
            async for block in request.stream():
                if not block:
                    continue
                received += len(block)
                if received > expected_length:
                    output.truncate(start)
                    raise CatalogueAPIError(400, "chunk_length_mismatch", "The chunk body exceeds Content-Range.")
                digest.update(block)
                await asyncio.to_thread(output.write, block)
            if received != expected_length:
                output.truncate(start)
                raise CatalogueAPIError(400, "chunk_length_mismatch", "The chunk body is shorter than Content-Range.")
            output.flush()
            os.fsync(output.fileno())
    except CatalogueAPIError:
        raise
    except OSError as exc:
        with suppress(OSError):
            with path.open("r+b") as output:
                output.truncate(start)
        raise CatalogueAPIError(507, "chunk_storage_failed", "The upload chunk could not be stored locally.") from exc
    chunk_hash = digest.hexdigest()
    if supplied_chunk_sha256 is not None and supplied_chunk_sha256.casefold() != chunk_hash:
        with path.open("r+b") as output:
            output.truncate(start)
        raise CatalogueAPIError(422, "chunk_hash_mismatch", "The uploaded chunk failed its SHA-256 check.")
    try:
        updated = await asyncio.to_thread(
            store.record_chunk,
            upload_id,
            offset=start,
            length=received,
            chunk_sha256=chunk_hash,
        )
    except CatalogueConflict as exc:
        with path.open("r+b") as output:
            output.truncate(start)
        raise CatalogueAPIError(409, "upload_state_conflict", str(exc)) from exc
    return UploadChunkResponse(
        upload_id=upload_id,
        received_bytes=int(updated["received_bytes"]),
        total_bytes=int(updated["total_bytes"]),
        state=UploadState(str(updated["state"])),
        chunk_sha256=chunk_hash,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@router.post("/upload-sessions/{upload_id}/complete", response_model=SourceAssetResponse)
async def complete_upload(upload_id: str, request: Request) -> SourceAssetResponse:
    store = _store(request)
    settings = store.settings
    session = await asyncio.to_thread(store.get_upload_session, upload_id)
    if int(session["received_bytes"]) != int(session["total_bytes"]):
        raise CatalogueAPIError(409, "upload_incomplete", "Every declared source byte must be received before completion.")
    partial = store.upload_dir(upload_id) / "source.partial"
    content_hash = await asyncio.to_thread(_sha256_file, partial)
    expected_hash = session.get("expected_sha256")
    if expected_hash and str(expected_hash) != content_hash:
        await asyncio.to_thread(store.mark_upload_failed, upload_id, "complete_hash_mismatch")
        raise CatalogueAPIError(422, "complete_hash_mismatch", "The completed source failed its SHA-256 check.")
    try:
        probe = await asyncio.to_thread(
            probe_media,
            partial,
            str(session["display_name"]),
            settings,
            None,
            max_bytes=settings.max_source_upload_bytes,
            max_duration_seconds=settings.max_longform_duration_seconds,
            source_kind="long-form source",
        )
    except MediaValidationError as exc:
        await asyncio.to_thread(store.mark_upload_failed, upload_id, exc.code)
        raise CatalogueAPIError(exc.status_code, exc.code, exc.safe_message) from exc
    await asyncio.to_thread(store.admit_source, int(session["total_bytes"]))
    destination, storage_key = store.blob_destination(content_hash)
    destination.parent.mkdir(parents=True, exist_ok=True)
    secure_private_directory(destination.parent)
    if destination.exists():
        if await asyncio.to_thread(_sha256_file, destination) != content_hash:
            raise CatalogueAPIError(500, "archive_hash_collision", "The local archive contains a conflicting blob.")
        partial.unlink(missing_ok=True)
    else:
        os.replace(partial, destination)
        secure_private_file(destination)
    asset = await asyncio.to_thread(
        store.complete_asset,
        upload_id,
        content_sha256=content_hash,
        duration_seconds=probe.file.duration_seconds,
        codec=probe.file.codec,
        container=probe.file.container,
        sample_rate=probe.file.sample_rate,
        channels=probe.file.channels,
        storage_key=storage_key,
    )
    with suppress(OSError):
        store.upload_dir(upload_id).rmdir()
    return SourceAssetResponse.model_validate(asset)


@router.delete("/upload-sessions/{upload_id}", status_code=204)
async def cancel_upload(upload_id: str, request: Request) -> Response:
    try:
        await asyncio.to_thread(_store(request).cancel_upload, upload_id)
    except CatalogueConflict as exc:
        raise CatalogueAPIError(409, "upload_state_conflict", str(exc)) from exc
    return Response(status_code=204)


@router.get("/batches/{batch_id}/assets", response_model=Page)
async def list_assets(
    batch_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page:
    items, total = await asyncio.to_thread(
        _store(request).list_assets, batch_id, offset=offset, limit=limit
    )
    return _page([SourceAssetResponse.model_validate(item) for item in items], total, offset, limit)


@router.get("/assets/{asset_id}", response_model=SourceAssetResponse)
async def get_asset(asset_id: str, request: Request) -> SourceAssetResponse:
    return SourceAssetResponse.model_validate(await asyncio.to_thread(_store(request).get_asset, asset_id))


@router.post("/assets/{asset_id}/segment", response_model=SegmentationResponse)
async def segment_asset(
    asset_id: str,
    payload: SegmentationRequest,
    request: Request,
) -> SegmentationResponse:
    store = _store(request)
    asset = await asyncio.to_thread(store.get_asset, asset_id)
    result = await asyncio.to_thread(
        segment_longform_source,
        asset_id,
        store.asset_source_path(asset_id),
        float(asset["duration_seconds"]),
        store.settings,
        minimum_expected_seconds=payload.minimum_expected_track_seconds,
        maximum_expected_seconds=payload.maximum_expected_track_seconds,
    )
    await asyncio.to_thread(
        store.replace_segments,
        asset_id,
        result.segments,
        reason="Deterministic multi-signal long-form scan",
        detected=True,
    )
    return result


@router.post(
    "/assets/{asset_id}/segmentation-jobs",
    response_model=SegmentationJobResponse,
    status_code=202,
)
async def start_segmentation_job(asset_id: str, request: Request) -> SegmentationJobResponse:
    result = await request.app.state.scan_scheduler.start_asset(asset_id)
    return SegmentationJobResponse.model_validate(result)


@router.get("/segmentation-jobs/{job_id}", response_model=SegmentationJobResponse)
async def get_segmentation_job(job_id: str, request: Request) -> SegmentationJobResponse:
    result = await asyncio.to_thread(_store(request).get_segmentation_job, job_id)
    return SegmentationJobResponse.model_validate(result)


@router.delete("/segmentation-jobs/{job_id}", response_model=SegmentationJobResponse)
async def cancel_segmentation_job(job_id: str, request: Request) -> SegmentationJobResponse:
    result = await request.app.state.scan_scheduler.cancel(job_id)
    return SegmentationJobResponse.model_validate(result)


@router.get("/assets/{asset_id}/segments", response_model=list[SegmentResponse])
async def list_segments(asset_id: str, request: Request) -> list[SegmentResponse]:
    return [
        SegmentResponse.model_validate(item)
        for item in await asyncio.to_thread(_store(request).list_segments, asset_id)
    ]


def _segments_from_boundaries(asset: dict[str, Any], inputs: list[SegmentBoundaryInput]) -> list[SegmentResponse]:
    ordered = sorted(inputs, key=lambda item: item.start_seconds)
    duration = float(asset["duration_seconds"])
    result: list[SegmentResponse] = []
    previous_end = 0.0
    for index, item in enumerate(ordered):
        if item.start_seconds < previous_end - 1e-6 or item.end_seconds > duration + 1e-6:
            raise ValueError("Segments must be ordered, non-overlapping, and within the source duration")
        stable_start = item.stable_core_start_seconds if item.stable_core_start_seconds is not None else item.start_seconds
        stable_end = item.stable_core_end_seconds if item.stable_core_end_seconds is not None else item.end_seconds
        if stable_start < item.start_seconds or stable_end > item.end_seconds or stable_end < stable_start:
            raise ValueError("Stable cores must be contained by their segment")
        segment_id = str(uuid5(NAMESPACE_URL, f"trackprompt:{asset['id']}:{item.start_seconds:.3f}:{item.end_seconds:.3f}"))
        result.append(
            SegmentResponse(
                id=segment_id,
                source_asset_id=str(asset["id"]),
                sequence_index=index,
                label=item.label or f"Track {index + 1}",
                start_seconds=item.start_seconds,
                end_seconds=item.end_seconds,
                stable_core_start_seconds=stable_start,
                stable_core_end_seconds=stable_end,
                confidence=item.confidence,
                transition_type=item.transition_type,
                review_state=ReviewState.USER_EDITED,
                accepted=item.accepted,
                revision=1,
            )
        )
        previous_end = item.end_seconds
    return result


@router.put("/assets/{asset_id}/segments", response_model=list[SegmentResponse])
async def replace_segments(
    asset_id: str,
    payload: SegmentReplaceRequest,
    request: Request,
) -> list[SegmentResponse]:
    store = _store(request)
    asset = await asyncio.to_thread(store.get_asset, asset_id)
    segments = _segments_from_boundaries(asset, payload.segments)
    rows = await asyncio.to_thread(
        store.replace_segments, asset_id, segments, reason=payload.reason, detected=False
    )
    return [SegmentResponse.model_validate(item) for item in rows]


def _normalize_segments(asset: dict[str, Any], segments: list[SegmentResponse]) -> list[SegmentResponse]:
    ordered = sorted(segments, key=lambda item: item.start_seconds)
    duration = float(asset["duration_seconds"])
    for index, item in enumerate(ordered):
        if item.start_seconds < 0 or item.end_seconds > duration or item.end_seconds <= item.start_seconds:
            raise ValueError("Edited segment lies outside the source")
        if index and item.start_seconds < ordered[index - 1].end_seconds - 1e-6:
            raise ValueError("Edited segments overlap")
        item.sequence_index = index
        item.revision += 1
    return ordered


@router.patch("/assets/{asset_id}/segments", response_model=list[SegmentResponse])
async def edit_segments(asset_id: str, payload: SegmentEditRequest, request: Request) -> list[SegmentResponse]:
    store = _store(request)
    asset = await asyncio.to_thread(store.get_asset, asset_id)
    if payload.operation == "restore":
        detected = await asyncio.to_thread(store.detected_segment_revision, asset_id)
        segments = [SegmentResponse.model_validate(item) for item in detected]
    else:
        segments = [
            SegmentResponse.model_validate(item)
            for item in await asyncio.to_thread(store.list_segments, asset_id)
        ]
        if payload.operation == "add":
            if payload.start_seconds is None or payload.end_seconds is None:
                raise CatalogueAPIError(422, "invalid_segment", "Add requires startSeconds and endSeconds.")
            segments.append(
                SegmentResponse(
                    id=str(uuid5(NAMESPACE_URL, f"trackprompt:{asset_id}:{payload.start_seconds:.3f}:{payload.end_seconds:.3f}")),
                    source_asset_id=asset_id,
                    sequence_index=len(segments),
                    label=payload.label or "Added track",
                    start_seconds=payload.start_seconds,
                    end_seconds=payload.end_seconds,
                    stable_core_start_seconds=payload.start_seconds,
                    stable_core_end_seconds=payload.end_seconds,
                    confidence=Confidence.UNKNOWN,
                    transition_type=TransitionType.UNCERTAIN,
                    review_state=ReviewState.USER_EDITED,
                    accepted=False,
                    revision=1,
                )
            )
        else:
            target_index = next(
                (index for index, item in enumerate(segments) if item.id == payload.segment_id), None
            )
            if target_index is None:
                raise CatalogueAPIError(404, "segment_not_found", "The virtual segment was not found.")
            target = segments[target_index]
            if payload.operation == "move":
                target.start_seconds = payload.start_seconds if payload.start_seconds is not None else target.start_seconds
                target.end_seconds = payload.end_seconds if payload.end_seconds is not None else target.end_seconds
                target.stable_core_start_seconds = max(target.start_seconds, target.stable_core_start_seconds)
                target.stable_core_end_seconds = min(target.end_seconds, target.stable_core_end_seconds)
                target.review_state = ReviewState.USER_EDITED
            elif payload.operation == "delete":
                segments.pop(target_index)
            elif payload.operation == "rename":
                target.label = payload.label or target.label
                target.review_state = ReviewState.USER_EDITED
            elif payload.operation in {"accept", "reject"}:
                target.accepted = payload.operation == "accept"
                target.review_state = ReviewState.ACCEPTED if target.accepted else ReviewState.REJECTED
            elif payload.operation == "split":
                at = payload.at_seconds
                if at is None or not target.start_seconds < at < target.end_seconds:
                    raise CatalogueAPIError(422, "invalid_split", "Split time must lie inside the segment.")
                left = target.model_copy(deep=True)
                right = target.model_copy(deep=True)
                left.id = str(uuid5(NAMESPACE_URL, f"trackprompt:{asset_id}:{left.start_seconds:.3f}:{at:.3f}"))
                left.end_seconds = at
                left.stable_core_end_seconds = min(left.stable_core_end_seconds, at)
                right.id = str(uuid5(NAMESPACE_URL, f"trackprompt:{asset_id}:{at:.3f}:{right.end_seconds:.3f}"))
                right.start_seconds = at
                right.stable_core_start_seconds = max(right.stable_core_start_seconds, at)
                right.label = payload.label or f"{target.label} B"
                left.review_state = right.review_state = ReviewState.USER_EDITED
                segments[target_index : target_index + 1] = [left, right]
            elif payload.operation == "merge":
                other_index = next(
                    (index for index, item in enumerate(segments) if item.id == payload.adjacent_segment_id), None
                )
                if other_index is None or abs(other_index - target_index) != 1:
                    raise CatalogueAPIError(422, "invalid_merge", "Only adjacent segments can be merged.")
                first_index, second_index = sorted((target_index, other_index))
                first = segments[first_index].model_copy(deep=True)
                second = segments[second_index]
                first.id = str(uuid5(NAMESPACE_URL, f"trackprompt:{asset_id}:{first.start_seconds:.3f}:{second.end_seconds:.3f}"))
                first.end_seconds = second.end_seconds
                first.stable_core_end_seconds = max(first.stable_core_end_seconds, second.stable_core_end_seconds)
                first.label = payload.label or first.label
                first.review_state = ReviewState.USER_EDITED
                segments[first_index : second_index + 1] = [first]
    normalized = _normalize_segments(asset, segments)
    rows = await asyncio.to_thread(
        store.replace_segments, asset_id, normalized, reason=payload.reason, detected=False
    )
    return [SegmentResponse.model_validate(item) for item in rows]


def _parse_manual_boundaries(payload: ManualBoundaryImport, duration: float) -> list[SegmentBoundaryInput]:
    starts: list[tuple[float, str]] = []
    if payload.format == "json":
        parsed = json.loads(payload.content)
        if not isinstance(parsed, list):
            raise ValueError("JSON boundary input must be a list")
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError("Every JSON boundary must be an object")
            starts.append((float(item["startSeconds"]), str(item.get("label", ""))[:200]))
    elif payload.format == "csv":
        reader = csv.DictReader(io.StringIO(payload.content))
        for row in reader:
            starts.append((float(row.get("start_seconds") or row.get("start") or ""), str(row.get("label") or "")[:200]))
    elif payload.format == "cue":
        current_label = ""
        for line in payload.content.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("TITLE "):
                current_label = stripped[6:].strip().strip('"')[:200]
            match = re.match(r"INDEX 01 (\d+):(\d+):(\d+)$", stripped, re.IGNORECASE)
            if match:
                minutes, seconds, frames = (int(value) for value in match.groups())
                starts.append((minutes * 60 + seconds + frames / 75.0, current_label))
    else:
        for line in payload.content.splitlines():
            match = re.search(r"(?:^|[?&#])(?:t|start)=([0-9]+(?:\.[0-9]+)?)", line)
            if match:
                starts.append((float(match.group(1)), ""))
    starts = sorted({(round(start, 3), label) for start, label in starts}, key=lambda item: item[0])
    if not starts or starts[0][0] > 0:
        starts.insert(0, (0.0, ""))
    if any(start < 0 or start >= duration for start, _label in starts):
        raise ValueError("Imported boundary lies outside the source")
    return [
        SegmentBoundaryInput(
            label=label or f"Track {index + 1}",
            start_seconds=start,
            end_seconds=starts[index + 1][0] if index + 1 < len(starts) else duration,
            transition_type=TransitionType.UNCERTAIN,
            confidence=Confidence.UNKNOWN,
            accepted=True,
        )
        for index, (start, label) in enumerate(starts)
    ]


@router.post("/assets/{asset_id}/segments/import", response_model=list[SegmentResponse])
async def import_boundaries(
    asset_id: str,
    payload: ManualBoundaryImport,
    request: Request,
) -> list[SegmentResponse]:
    store = _store(request)
    asset = await asyncio.to_thread(store.get_asset, asset_id)
    try:
        inputs = _parse_manual_boundaries(payload, float(asset["duration_seconds"]))
        segments = _segments_from_boundaries(asset, inputs)
        for item in segments:
            item.review_state = ReviewState.IMPORTED
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CatalogueAPIError(422, "invalid_boundary_import", "The boundary import is invalid or unordered.") from exc
    rows = await asyncio.to_thread(
        store.replace_segments,
        asset_id,
        segments,
        reason=f"Imported {payload.format.upper()} boundaries",
        detected=False,
    )
    return [SegmentResponse.model_validate(item) for item in rows]


@router.post("/assets/{asset_id}/segments/analyze", response_model=list[QueueItemResponse], status_code=202)
async def enqueue_segment_analyses(asset_id: str, request: Request) -> list[QueueItemResponse]:
    store = _store(request)
    asset = await asyncio.to_thread(store.get_asset, asset_id)
    segments = [
        item for item in await asyncio.to_thread(store.list_segments, asset_id) if bool(item["accepted"])
    ]
    if not segments:
        raise CatalogueAPIError(409, "segments_not_reviewed", "Accept at least one reviewed segment before analysis.")
    items = await asyncio.to_thread(
        store.enqueue_segments,
        str(asset["batch_id"]),
        [str(item["id"]) for item in segments],
    )
    scheduler = request.app.state.catalog_scheduler
    scheduler.wake()
    return [_queue_response(item) for item in items]


@router.get("/batches/{batch_id}/queue", response_model=list[QueueItemResponse])
async def list_batch_queue(batch_id: str, request: Request) -> list[QueueItemResponse]:
    rows = await asyncio.to_thread(_store(request).list_queue_items, batch_id)
    return [_queue_response(item) for item in rows]


async def _set_batch_state(batch_id: str, request: Request, state: BatchState) -> BatchResponse:
    result = await asyncio.to_thread(
        _store(request).patch_batch, batch_id, BatchPatch(state=state)
    )
    request.app.state.catalog_scheduler.wake()
    return BatchResponse.model_validate(result)


@router.post("/batches/{batch_id}/start", response_model=BatchResponse)
async def start_batch(batch_id: str, request: Request) -> BatchResponse:
    return await _set_batch_state(batch_id, request, BatchState.RUNNING)


@router.post("/batches/{batch_id}/pause", response_model=BatchResponse)
async def pause_batch(batch_id: str, request: Request) -> BatchResponse:
    return await _set_batch_state(batch_id, request, BatchState.PAUSED)


@router.post("/batches/{batch_id}/resume", response_model=BatchResponse)
async def resume_batch(batch_id: str, request: Request) -> BatchResponse:
    return await _set_batch_state(batch_id, request, BatchState.RUNNING)


@router.post("/batches/{batch_id}/cancel", response_model=BatchResponse)
async def cancel_batch(batch_id: str, request: Request) -> BatchResponse:
    store = _store(request)
    await _set_batch_state(batch_id, request, BatchState.CANCELLED)
    for item in await asyncio.to_thread(store.list_queue_items, batch_id):
        if str(item["state"]) in {QueueState.QUEUED.value, QueueState.STORED.value, QueueState.PAUSED.value}:
            await asyncio.to_thread(store.transition_queue_item, str(item["id"]), QueueState.CANCELLED)
    await request.app.state.catalog_scheduler.cancel_batch(batch_id)
    return BatchResponse.model_validate(await asyncio.to_thread(store.get_batch, batch_id))


@router.post("/batches/{batch_id}/retry-failed", response_model=list[QueueItemResponse])
async def retry_failed(batch_id: str, request: Request) -> list[QueueItemResponse]:
    store = _store(request)
    failed = await asyncio.to_thread(store.list_queue_items, batch_id, states=(QueueState.FAILED.value,))
    results = [
        await asyncio.to_thread(store.transition_queue_item, str(item["id"]), QueueState.QUEUED)
        for item in failed
    ]
    request.app.state.catalog_scheduler.wake()
    return [_queue_response(item) for item in results]


@router.get("/projects/{project_id}/audit", response_model=Page)
async def list_audit(
    project_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
) -> Page:
    items, total = await asyncio.to_thread(
        _store(request).list_audit, project_id, offset=offset, limit=limit
    )
    return _page([AuditEventResponse.model_validate(item) for item in items], total, offset, limit)


@router.get("/projects/{project_id}/audit/verify", response_model=AuditVerificationResponse)
async def verify_audit(project_id: str, request: Request) -> AuditVerificationResponse:
    return AuditVerificationResponse.model_validate(
        await asyncio.to_thread(_store(request).verify_audit, project_id)
    )


@router.get("/projects/{project_id}/audit.jsonl")
async def export_audit_jsonl(project_id: str, request: Request) -> Response:
    items, _total = await asyncio.to_thread(
        _store(request).list_audit, project_id, offset=0, limit=1_000_000
    )
    content = "\n".join(
        AuditEventResponse.model_validate(item).model_dump_json(by_alias=True) for item in items
    ) + ("\n" if items else "")
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="project-{project_id}-audit.jsonl"'},
    )


@router.get("/projects/{project_id}/audit.csv")
async def export_audit_csv(project_id: str, request: Request) -> Response:
    items, _total = await asyncio.to_thread(
        _store(request).list_audit, project_id, offset=0, limit=1_000_000
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "eventId", "timestamp", "sequence", "projectId", "batchId", "entityType",
            "entityId", "eventType", "actorType", "correlationId", "schemaVersion",
            "previousEventHash", "eventHash", "payloadJson",
        ],
    )
    writer.writeheader()
    for item in items:
        event = AuditEventResponse.model_validate(item)
        writer.writerow(
            {
                "eventId": event.event_id,
                "timestamp": event.timestamp.isoformat(),
                "sequence": event.sequence,
                "projectId": event.project_id,
                "batchId": event.batch_id,
                "entityType": event.entity_type,
                "entityId": event.entity_id,
                "eventType": event.event_type,
                "actorType": event.actor_type,
                "correlationId": event.correlation_id,
                "schemaVersion": event.schema_version,
                "previousEventHash": event.previous_event_hash,
                "eventHash": event.event_hash,
                "payloadJson": json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
            }
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="project-{project_id}-audit.csv"'},
    )


@router.get("/projects/{project_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(project_id: str, request: Request) -> list[ArtifactResponse]:
    return [
        ArtifactResponse.model_validate(item)
        for item in await asyncio.to_thread(_store(request).artifact_rows, project_id)
    ]


@router.get("/projects/{project_id}/revisions", response_model=list[RevisionResponse])
async def list_revisions(project_id: str, request: Request) -> list[RevisionResponse]:
    return [
        RevisionResponse.model_validate(item)
        for item in await asyncio.to_thread(_store(request).revision_rows, project_id)
    ]


async def _mastering_report(batch_id: str, request: Request) -> MasteringReport:
    return await asyncio.to_thread(build_mastering_report, _store(request), batch_id)


@router.post("/batches/{batch_id}/report", response_model=dict[str, ArtifactResponse])
async def generate_batch_report(
    batch_id: str,
    request: Request,
) -> dict[str, ArtifactResponse]:
    store = _store(request)
    batch = await asyncio.to_thread(store.get_batch, batch_id)
    report = await _mastering_report(batch_id, request)
    contents = {
        "json": (report_json(report), "application/json"),
        "markdown": (report_markdown(report).encode("utf-8"), "text/markdown; charset=utf-8"),
        "csv": (report_csv(report).encode("utf-8"), "text/csv; charset=utf-8"),
    }
    artifacts: dict[str, ArtifactResponse] = {}
    for name, (content, media_type) in contents.items():
        row = await asyncio.to_thread(
            store.register_artifact,
            project_id=str(batch["project_id"]),
            batch_id=batch_id,
            owner_type="batch",
            owner_id=batch_id,
            artifact_type=f"mastering_report_{name}",
            schema_version=report.schema_version,
            media_type=media_type,
            content=content,
            producer_versions={"trackprompt": request.app.version},
            reason="Set-level mastering comparison",
        )
        artifacts[name] = ArtifactResponse.model_validate(row)
    return artifacts


@router.get("/batches/{batch_id}/report.json")
async def batch_report_json(batch_id: str, request: Request) -> Response:
    report = await _mastering_report(batch_id, request)
    return Response(
        content=report_json(report),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="batch-{batch_id}-mastering.json"'},
    )


@router.get("/batches/{batch_id}/report.md")
async def batch_report_markdown(batch_id: str, request: Request) -> Response:
    report = await _mastering_report(batch_id, request)
    return Response(
        content=report_markdown(report),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="batch-{batch_id}-mastering.md"'},
    )


@router.get("/batches/{batch_id}/report.csv")
async def batch_report_csv(batch_id: str, request: Request) -> Response:
    report = await _mastering_report(batch_id, request)
    return Response(
        content=report_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="batch-{batch_id}-mastering.csv"'},
    )

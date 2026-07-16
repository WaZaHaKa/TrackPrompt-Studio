from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .adapters import get_capabilities
from .config import Settings
from .editing import PatchError, apply_analysis_patch
from .exports import analysis_json_export, analysis_markdown_export
from .jobs import JobManager
from .media import MediaValidationError, probe_media, sanitize_display_name
from .privacy import secure_private_file
from .prompting import generate_prompt_package
from .prompting.composer import _clean_user_text
from .schemas import (
    AnalysisMode,
    AnalysisPatch,
    AnalysisResult,
    CapabilitiesResponse,
    Confidence,
    ErrorDetail,
    ErrorResponse,
    GenreAnalysis,
    GenreCandidate,
    GenrePatch,
    HealthResponse,
    JobResponse,
    JobStatus,
    LyricsAnalysisSummary,
    LyricsPatch,
    PrivateLyricsTranscript,
    PromptPackage,
    PromptPreferences,
)
from .security import LocalRequestBoundaryMiddleware
from .store import DeletionError, JobStore

LOGGER = logging.getLogger("trackprompt.api")


class APIError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = ErrorDetail(code=code, message=message, details=details)


def _state(request: Request) -> tuple[Settings, JobStore, JobManager]:
    return request.app.state.settings, request.app.state.store, request.app.state.manager


def _not_found() -> APIError:
    return APIError(404, "job_not_found", "Analysis job was not found or has expired.")


async def _require_job(store: JobStore, job_id: str) -> None:
    if await asyncio.to_thread(store.get_job, job_id) is None:
        raise _not_found()


async def _cleanup_rejected_upload(
    store: JobStore,
    manager: JobManager,
    job_id: str,
) -> None:
    await manager.release_admission(job_id)
    try:
        await asyncio.to_thread(store.delete_job, job_id)
    except DeletionError as exc:
        with suppress(OSError, KeyError):
            await asyncio.to_thread(store.mark_cleanup_pending, job_id)
        raise APIError(
            503,
            "cleanup_pending",
            "The upload was rejected, but private-file cleanup is still pending and will be retried.",
            details={"jobId": job_id, "retryDeletePath": f"/api/analyses/{job_id}"},
        ) from exc


async def _stream_upload(upload: UploadFile, destination: Path, settings: Settings) -> int:
    total = 0
    try:
        with destination.open("xb") as output:
            secure_private_file(destination)
            while True:
                chunk = await upload.read(settings.upload_chunk_bytes)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise APIError(
                        413,
                        "upload_too_large",
                        f"The upload exceeds the configured {settings.max_upload_mb} MB limit.",
                    )
                await asyncio.to_thread(output.write, chunk)
    except APIError:
        raise
    except OSError as exc:
        raise APIError(500, "upload_storage_failed", "The upload could not be stored locally.") from exc
    finally:
        await upload.close()
    if total == 0:
        raise APIError(400, "empty_upload", "The uploaded file is empty.")
    return total


def _media_type(analysis: AnalysisResult) -> str:
    if analysis.file.container in {"wav", "wave"}:
        return "audio/wav"
    if analysis.file.container == "flac":
        return "audio/flac"
    if analysis.file.container in {"mp3", "mpeg"}:
        return "audio/mpeg"
    if analysis.file.container == "aac":
        return "audio/aac"
    if analysis.file.container in {"m4a", "mp4", "mov", "ipod"}:
        return "audio/mp4"
    if analysis.file.container in {"ogg", "oga"}:
        return "audio/ogg"
    return "application/octet-stream"


def _sse_event_name(status: JobStatus | str) -> str:
    status_value = status.value if isinstance(status, JobStatus) else str(status)
    if status_value in {
        JobStatus.COMPLETED.value,
        JobStatus.CANCELLED.value,
        JobStatus.FAILED.value,
        JobStatus.EXPIRED.value,
    }:
        return status_value
    return "progress"


RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)$")


def _byte_iterator(path: Path, start: int, end: int, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    with path.open("rb") as stream:
        stream.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = stream.read(min(chunk_size, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configured.ensure_directories()
        store = JobStore(configured)
        manager = JobManager(store, configured)
        application.state.settings = configured
        application.state.store = store
        application.state.manager = manager
        await manager.startup()
        try:
            yield
        finally:
            await manager.shutdown()

    application = FastAPI(
        title="TrackPrompt Studio API",
        description="Local-only audio analysis and deterministic prompt composition.",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Range"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Disposition"],
    )
    application.add_middleware(
        LocalRequestBoundaryMiddleware,
        max_upload_request_bytes=configured.max_upload_bytes + 1024 * 1024,
        max_api_request_bytes=256 * 1024,
        allowed_origins=configured.cors_origins,
        allowed_hosts=configured.allowed_hosts,
    )

    @application.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        payload = ErrorResponse(error=exc.detail).model_dump(mode="json", by_alias=True)
        return JSONResponse(status_code=exc.status_code, content=payload)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        issues = [
            {"location": [str(part) for part in error.get("loc", ())], "type": str(error.get("type", "invalid"))}
            for error in exc.errors()
        ]
        payload = ErrorResponse(
            error=ErrorDetail(
                code="request_validation_failed",
                message="The request did not match the API schema.",
                details={"issues": issues},
            )
        ).model_dump(mode="json", by_alias=True)
        return JSONResponse(status_code=422, content=payload)

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        error_by_status = {
            400: ("bad_request", "The request could not be parsed."),
            404: ("route_not_found", "The requested API route was not found."),
            405: ("method_not_allowed", "This HTTP method is not allowed for the requested API route."),
        }
        code, message = error_by_status.get(
            exc.status_code,
            ("http_error", "The request could not be completed."),
        )
        payload = ErrorResponse(error=ErrorDetail(code=code, message=message)).model_dump(
            mode="json",
            by_alias=True,
        )
        return JSONResponse(status_code=exc.status_code, content=payload, headers=exc.headers)

    @application.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        LOGGER.error("unexpected_api_failure error_type=%s", type(exc).__name__)
        payload = ErrorResponse(
            error=ErrorDetail(
                code="internal_error",
                message="The request could not be completed safely.",
            )
        ).model_dump(mode="json", by_alias=True)
        return JSONResponse(status_code=500, content=payload)

    @application.get("/api/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        settings_value, store, manager = _state(request)
        capabilities = await asyncio.to_thread(get_capabilities, settings_value)
        if capabilities.gpu_task_queue is not None:
            capabilities.gpu_task_queue.active = manager.gpu_active
            capabilities.gpu_task_queue.waiting = manager.gpu_waiting
        database_available = await asyncio.to_thread(store.healthcheck)
        return HealthResponse(
            status="ok" if capabilities.ffmpeg.available and capabilities.ffprobe.available and database_available else "degraded",
            service_version=__version__,
            schema_version="1.2.0",
            ffmpeg=capabilities.ffmpeg,
            ffprobe=capabilities.ffprobe,
            database_available=database_available,
            analysis_workers=settings_value.analysis_workers,
            deep_mode_available=capabilities.deep_mode.available,
            genre_tagger_available=bool(capabilities.genre_tagger and capabilities.genre_tagger.available),
            lyrics_adapter_available=bool(capabilities.lyrics_adapter and capabilities.lyrics_adapter.available),
            local_prompt_writer_available=bool(capabilities.prompt_writer and capabilities.prompt_writer.available),
            network_features_enabled=False,
        )

    @application.get("/api/capabilities", response_model=CapabilitiesResponse)
    async def capabilities(request: Request) -> CapabilitiesResponse:
        settings_value, _store, manager = _state(request)
        response = await asyncio.to_thread(get_capabilities, settings_value)
        if response.gpu_task_queue is not None:
            response.gpu_task_queue.active = manager.gpu_active
            response.gpu_task_queue.waiting = manager.gpu_waiting
        return response

    @application.post("/api/analyses", response_model=JobResponse, status_code=202)
    async def create_analysis(
        request: Request,
        file: Annotated[UploadFile, File(description="Local audio file")],
        mode: Annotated[AnalysisMode, Form()] = AnalysisMode.FAST,
        permission_confirmed: Annotated[bool, Form(alias="permissionConfirmed")] = False,
        enable_lyrical_analysis: Annotated[bool, Form(alias="enableLyricalAnalysis")] = False,
        enable_genre_analysis: Annotated[bool, Form(alias="enableGenreAnalysis")] = False,
        lyrics_consent_confirmed: Annotated[bool, Form(alias="lyricsConsentConfirmed")] = False,
        derive_lyrical_themes: Annotated[bool, Form(alias="deriveLyricalThemes")] = False,
        allow_feature_fallback: Annotated[bool, Form(alias="allowFeatureFallback")] = False,
    ) -> JobResponse:
        settings_value, store, manager = _state(request)
        if not permission_confirmed:
            await file.close()
            raise APIError(400, "permission_required", "Confirm permission to analyze this audio before uploading.")
        if enable_lyrical_analysis and mode != AnalysisMode.DEEP:
            await file.close()
            raise APIError(422, "lyrics_requires_deep", "Lyrics analysis requires Deep mode and its private vocal stem.")
        if enable_lyrical_analysis and not lyrics_consent_confirmed:
            await file.close()
            raise APIError(400, "lyrics_permission_required", "Confirm separate permission for private approximate lyrics transcription.")
        if derive_lyrical_themes and not enable_lyrical_analysis:
            await file.close()
            raise APIError(422, "themes_require_lyrics", "Abstract lyrical themes require explicit Lyrics analysis.")
        requested_capabilities = await asyncio.to_thread(get_capabilities, settings_value)
        unavailable: list[str] = []
        if enable_genre_analysis and not bool(requested_capabilities.genre_tagger and requested_capabilities.genre_tagger.available):
            unavailable.append("genre tagging")
        if enable_lyrical_analysis and not bool(requested_capabilities.lyrics_adapter and requested_capabilities.lyrics_adapter.available):
            unavailable.append("lyrics analysis")
        if unavailable and not allow_feature_fallback:
            await file.close()
            raise APIError(
                409,
                "requested_adapter_unavailable",
                f"The requested local feature is not ready: {', '.join(unavailable)}. Enable explicit fallback to continue without it.",
            )
        display_name = sanitize_display_name(file.filename or "audio-file")
        job_id = str(uuid4())
        if not await manager.try_admit(job_id):
            await file.close()
            raise APIError(
                429,
                "analysis_capacity_reached",
                "The local analysis queue is full. Wait for a running job to finish and try again.",
                details={"maxPendingJobs": settings_value.max_pending_jobs},
            )
        try:
            await asyncio.to_thread(
                store.create_job,
                job_id,
                mode,
                display_name,
                permission_confirmed,
                enable_lyrical_analysis,
                enable_genre_analysis,
                lyrics_consent_confirmed,
                derive_lyrical_themes,
                allow_feature_fallback,
            )
            source = store.job_dir(job_id) / "source.bin"
            await _stream_upload(file, source, settings_value)
            # Reject unreadable/unsupported uploads synchronously with a safe
            # 4xx. The background job intentionally performs a fresh probe so
            # the visible validating stage represents real work and can be
            # cancelled independently.
            await asyncio.to_thread(
                probe_media,
                source,
                display_name,
                settings_value,
            )
            response = await manager.response(job_id)
            manager.start(job_id)
            return response
        except MediaValidationError as exc:
            await _cleanup_rejected_upload(store, manager, job_id)
            raise APIError(exc.status_code, exc.code, exc.safe_message) from exc
        except APIError:
            await _cleanup_rejected_upload(store, manager, job_id)
            raise
        except Exception as exc:
            await _cleanup_rejected_upload(store, manager, job_id)
            raise APIError(500, "upload_failed", "The upload could not be accepted safely.") from exc

    @application.get("/api/analyses/{job_id}", response_model=JobResponse)
    async def get_analysis(job_id: str, request: Request) -> JobResponse:
        _settings, _store, manager = _state(request)
        try:
            return await manager.response(job_id)
        except KeyError as exc:
            raise _not_found() from exc

    @application.get("/api/analyses/{job_id}/events")
    async def analysis_events(job_id: str, request: Request) -> StreamingResponse:
        _settings, _store, manager = _state(request)
        try:
            subscription = await manager.open_subscription(job_id)
        except KeyError as exc:
            raise _not_found() from exc

        async def stream() -> AsyncIterator[bytes]:
            async for event in subscription:
                if await request.is_disconnected():
                    return
                event_name = _sse_event_name(event.status)
                data = event.model_dump_json(by_alias=True)
                yield f"event: {event_name}\nid: {event.sequence}\ndata: {data}\n\n".encode()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @application.post("/api/analyses/{job_id}/cancel", response_model=JobResponse)
    async def cancel_analysis(job_id: str, request: Request) -> JobResponse:
        _settings, _store, manager = _state(request)
        try:
            await manager.cancel(job_id)
            return await manager.response(job_id)
        except KeyError as exc:
            raise _not_found() from exc

    @application.patch("/api/analyses/{job_id}", response_model=JobResponse)
    async def patch_analysis(job_id: str, patch: AnalysisPatch, request: Request) -> JobResponse:
        _settings, store, manager = _state(request)
        try:
            async with manager.job_lock(job_id):
                await _require_job(store, job_id)
                analysis_data = await asyncio.to_thread(store.read_json, job_id, "analysis.json")
                detected_data = await asyncio.to_thread(store.read_json, job_id, "detected-analysis.json")
                if analysis_data is None or detected_data is None:
                    raise APIError(409, "analysis_not_ready", "Analysis values are not ready to edit.")
                try:
                    edited = apply_analysis_patch(
                        AnalysisResult.model_validate(analysis_data),
                        AnalysisResult.model_validate(detected_data),
                        patch,
                    )
                except PatchError as exc:
                    raise APIError(422, "invalid_analysis_edit", str(exc)) from exc
                # A prompt package is a snapshot of the analysis. Invalidate it
                # before edits so no response/export can report a stale prompt.
                try:
                    await asyncio.to_thread(store.delete_json, job_id, "prompt.json")
                    await asyncio.to_thread(
                        store.write_json,
                        job_id,
                        "analysis.json",
                        edited.model_dump(mode="json", by_alias=True),
                    )
                except OSError as exc:
                    raise APIError(
                        503,
                        "analysis_edit_storage_failed",
                        "The edited analysis could not be stored safely.",
                    ) from exc
                return await manager.response(job_id)
        except KeyError as exc:
            raise _not_found() from exc

    @application.post("/api/analyses/{job_id}/prompt", response_model=PromptPackage)
    async def generate_prompt(
        job_id: str,
        preferences: PromptPreferences,
        request: Request,
    ) -> PromptPackage:
        _settings, store, manager = _state(request)
        try:
            async with manager.job_lock(job_id):
                await _require_job(store, job_id)
                analysis_data = await asyncio.to_thread(store.read_json, job_id, "analysis.json")
                if analysis_data is None:
                    raise APIError(409, "analysis_not_ready", "The prompt cannot be generated before analysis completes.")
                analysis = AnalysisResult.model_validate(analysis_data)
                if preferences.user_overrides:
                    try:
                        analysis = apply_analysis_patch(
                            analysis,
                            analysis,
                            AnalysisPatch(user_overrides=preferences.user_overrides),
                        )
                    except PatchError as exc:
                        raise APIError(422, "invalid_prompt_override", str(exc)) from exc
                # Persisted disabled paths are always respected; a request can
                # add more but cannot erase prior PATCH decisions.
                merged_disabled = sorted(
                    set(analysis.disabled_feature_paths)
                    | set(preferences.disabled_feature_paths)
                )
                preferences = preferences.model_copy(
                    update={"disabled_feature_paths": merged_disabled}
                )
                transcript_data = await asyncio.to_thread(store.read_json, job_id, "lyrics.json")
                transcript = PrivateLyricsTranscript.model_validate(transcript_data) if transcript_data else None
                if preferences.prompt_engine_mode.value == "reliable":
                    package = await asyncio.to_thread(
                        generate_prompt_package,
                        analysis,
                        preferences,
                        request.app.state.settings,
                        transcript,
                    )
                else:
                    async with manager.gpu_task():
                        package = await asyncio.to_thread(
                            generate_prompt_package,
                            analysis,
                            preferences,
                            request.app.state.settings,
                            transcript,
                        )
                await asyncio.to_thread(
                    store.write_json,
                    job_id,
                    "prompt.json",
                    package.model_dump(mode="json", by_alias=True),
                )
                await asyncio.to_thread(
                    store.write_json,
                    job_id,
                    "preferences.json",
                    preferences.model_dump(mode="json", by_alias=True),
                )
                return package
        except KeyError as exc:
            raise _not_found() from exc

    @application.get("/api/analyses/{job_id}/lyrics", response_model=PrivateLyricsTranscript)
    async def get_lyrics(job_id: str, request: Request) -> PrivateLyricsTranscript:
        _settings, store, _manager = _state(request)
        await _require_job(store, job_id)
        payload = await asyncio.to_thread(store.read_json, job_id, "lyrics.json")
        if payload is None:
            raise APIError(404, "lyrics_not_found", "The private approximate transcript is unavailable or was deleted.")
        return PrivateLyricsTranscript.model_validate(payload)

    @application.patch("/api/analyses/{job_id}/lyrics", response_model=PrivateLyricsTranscript)
    async def patch_lyrics(job_id: str, patch: LyricsPatch, request: Request) -> PrivateLyricsTranscript:
        _settings, store, manager = _state(request)
        try:
            async with manager.job_lock(job_id):
                await _require_job(store, job_id)
                payload = await asyncio.to_thread(store.read_json, job_id, "lyrics.json")
                detected_payload = await asyncio.to_thread(store.read_json, job_id, "detected-lyrics.json")
                if payload is None or detected_payload is None:
                    raise APIError(404, "lyrics_not_found", "The private approximate transcript is unavailable or was deleted.")
                transcript = PrivateLyricsTranscript.model_validate(payload)
                detected = PrivateLyricsTranscript.model_validate(detected_payload)
                current = {segment.id: segment for segment in transcript.segments}
                originals = {segment.id: segment for segment in detected.segments}
                for update in patch.updates:
                    segment = current.get(update.segment_id)
                    if segment is None:
                        raise APIError(422, "lyrics_segment_not_found", "A requested transcript segment was not found.")
                    if update.delete:
                        current.pop(update.segment_id, None)
                        continue
                    if update.restore_detected:
                        original = originals.get(update.segment_id)
                        if original is None:
                            raise APIError(422, "lyrics_segment_not_found", "The detected transcript segment is unavailable.")
                        current[update.segment_id] = original
                        continue
                    if update.text is not None:
                        text = re.sub(r"\s+", " ", "".join(character for character in update.text if character.isprintable())).strip()
                        segment = segment.model_copy(update={"text": text, "user_edited": True})
                    if update.mark_uncertain:
                        flags = list(dict.fromkeys([*segment.quality_flags, "user_marked_uncertain"]))
                        segment = segment.model_copy(update={"confidence": "low", "quality_flags": flags, "user_edited": True})
                    current[update.segment_id] = segment
                transcript = transcript.model_copy(
                    update={"segments": sorted(current.values(), key=lambda item: item.start_seconds), "user_edited": True}
                )
                await asyncio.to_thread(
                    store.write_json,
                    job_id,
                    "lyrics.json",
                    transcript.model_dump(mode="json", by_alias=True),
                )
                analysis_payload = await asyncio.to_thread(store.read_json, job_id, "analysis.json")
                if analysis_payload is not None:
                    analysis = AnalysisResult.model_validate(analysis_payload)
                    summary = analysis.lyrics_summary or LyricsAnalysisSummary(enabled=True)
                    summary.segment_count = len(transcript.segments)
                    summary.transcript_available = bool(transcript.segments)
                    if patch.abstract_themes is not None:
                        summary.abstract_themes = [
                            cleaned
                            for item in patch.abstract_themes
                            if (cleaned := _clean_user_text(item, maximum=120)) is not None
                        ]
                        summary.theme_confidence = Confidence.MEDIUM if summary.abstract_themes else Confidence.UNKNOWN
                    analysis.lyrics_summary = summary
                    await asyncio.to_thread(
                        store.write_json,
                        job_id,
                        "analysis.json",
                        analysis.model_dump(mode="json", by_alias=True),
                    )
                    await asyncio.to_thread(store.delete_json, job_id, "prompt.json")
                return transcript
        except KeyError as exc:
            raise _not_found() from exc

    @application.delete("/api/analyses/{job_id}/lyrics", status_code=204)
    async def delete_lyrics(job_id: str, request: Request) -> Response:
        _settings, store, manager = _state(request)
        try:
            async with manager.job_lock(job_id):
                await _require_job(store, job_id)
                for filename in ("lyrics.json", "detected-lyrics.json", "lyrics-summary.json"):
                    await asyncio.to_thread(store.delete_json, job_id, filename)
                analysis_payload = await asyncio.to_thread(store.read_json, job_id, "analysis.json")
                if analysis_payload is not None:
                    analysis = AnalysisResult.model_validate(analysis_payload)
                    analysis.lyrics_summary = LyricsAnalysisSummary(
                        enabled=True,
                        status="deleted",
                        transcript_available=False,
                    )
                    await asyncio.to_thread(
                        store.write_json,
                        job_id,
                        "analysis.json",
                        analysis.model_dump(mode="json", by_alias=True),
                    )
                    await asyncio.to_thread(store.delete_json, job_id, "prompt.json")
            return Response(status_code=204)
        except KeyError as exc:
            raise _not_found() from exc

    @application.get("/api/analyses/{job_id}/lyrics/export")
    async def export_lyrics(job_id: str, request: Request) -> Response:
        transcript = await get_lyrics(job_id, request)
        content = json.dumps(
            transcript.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="trackprompt-{transcript.job_id}-private-lyrics.json"'},
        )

    @application.patch("/api/analyses/{job_id}/genre", response_model=GenreAnalysis)
    async def patch_genre(job_id: str, patch: GenrePatch, request: Request) -> GenreAnalysis:
        _settings, store, manager = _state(request)
        try:
            async with manager.job_lock(job_id):
                await _require_job(store, job_id)
                analysis_payload = await asyncio.to_thread(store.read_json, job_id, "analysis.json")
                detected_payload = await asyncio.to_thread(store.read_json, job_id, "detected-analysis.json")
                if analysis_payload is None or detected_payload is None:
                    raise APIError(409, "analysis_not_ready", "Genre results are not ready to edit.")
                analysis = AnalysisResult.model_validate(analysis_payload)
                detected = AnalysisResult.model_validate(detected_payload)
                if patch.restore_all:
                    analysis.genre_analysis = detected.genre_analysis
                genre = analysis.genre_analysis
                if genre is None:
                    raise APIError(404, "genre_not_found", "No local genre result is available.")
                candidate_lists = [genre.broad_candidates, genre.subgenre_candidates, genre.descriptive_tags]
                candidates = {candidate.id: candidate for group in candidate_lists for candidate in group}
                originals = (
                    {candidate.id: candidate for group in [detected.genre_analysis.broad_candidates, detected.genre_analysis.subgenre_candidates, detected.genre_analysis.descriptive_tags] for candidate in group}
                    if detected.genre_analysis is not None
                    else {}
                )
                for update in patch.updates:
                    candidate = candidates.get(update.candidate_id)
                    if candidate is None:
                        raise APIError(422, "genre_candidate_not_found", "A requested genre candidate was not found.")
                    if update.restore_detected and update.candidate_id in originals:
                        restored = originals[update.candidate_id]
                        for group in candidate_lists:
                            group[:] = [restored if item.id == update.candidate_id else item for item in group]
                        candidates[update.candidate_id] = restored
                        continue
                    changes: dict[str, Any] = {}
                    if update.label is not None:
                        safe_label = _clean_user_text(update.label, maximum=120)
                        if safe_label is None:
                            raise APIError(422, "unsafe_genre_label", "The genre label is not in the prompt-safe musical vocabulary.")
                        changes.update({"label": safe_label, "user_edited": True})
                    if update.accepted is not None:
                        changes.update({"accepted": update.accepted, "rejected": False if update.accepted else candidate.rejected})
                    if update.rejected is not None:
                        changes.update({"rejected": update.rejected, "accepted": False if update.rejected else candidate.accepted})
                    if update.locked is not None:
                        changes["locked"] = update.locked
                    replacement = candidate.model_copy(update=changes)
                    for group in candidate_lists:
                        group[:] = [replacement if item.id == update.candidate_id else item for item in group]
                    candidates[update.candidate_id] = replacement
                if patch.custom_genre is not None:
                    safe_custom = _clean_user_text(patch.custom_genre, maximum=120)
                    if safe_custom is None:
                        raise APIError(422, "unsafe_genre_label", "The custom genre is not in the prompt-safe musical vocabulary.")
                    genre.subgenre_candidates.append(
                        GenreCandidate(
                            id=f"custom-{uuid4()}",
                            label=safe_custom,
                            canonical_label=safe_custom,
                            similarity=0.0,
                            confidence="unknown",
                            accepted=True,
                            user_edited=True,
                            custom=True,
                        )
                    )
                if patch.disabled_for_prompt is not None:
                    genre.disabled_for_prompt = patch.disabled_for_prompt
                genre.user_edited = True
                genre.user_accepted = any(
                    candidate.accepted for group in candidate_lists for candidate in group
                )
                analysis.genre_analysis = genre
                await asyncio.to_thread(
                    store.write_json,
                    job_id,
                    "analysis.json",
                    analysis.model_dump(mode="json", by_alias=True),
                )
                await asyncio.to_thread(store.delete_json, job_id, "prompt.json")
                return genre
        except KeyError as exc:
            raise _not_found() from exc

    @application.delete("/api/analyses/{job_id}", status_code=204)
    async def delete_analysis(job_id: str, request: Request) -> Response:
        _settings, _store, manager = _state(request)
        try:
            await manager.delete(job_id)
        except KeyError as exc:
            raise _not_found() from exc
        except DeletionError as exc:
            raise APIError(503, "deletion_incomplete", str(exc)) from exc
        return Response(status_code=204)

    async def _export_parts(job_id: str, request: Request) -> tuple[AnalysisResult, PromptPackage | None]:
        _settings, store, _manager = _state(request)
        await _require_job(store, job_id)
        analysis_data = await asyncio.to_thread(store.read_json, job_id, "analysis.json")
        prompt_data = await asyncio.to_thread(store.read_json, job_id, "prompt.json")
        if analysis_data is None:
            if await asyncio.to_thread(store.get_job, job_id) is None:
                raise _not_found()
            raise APIError(409, "analysis_not_ready", "Analysis is not ready to export.")
        return (
            AnalysisResult.model_validate(analysis_data),
            PromptPackage.model_validate(prompt_data) if prompt_data else None,
        )

    @application.get("/api/analyses/{job_id}/export.json")
    async def export_json(job_id: str, request: Request) -> Response:
        analysis, prompt = await _export_parts(job_id, request)
        content = analysis_json_export(analysis, prompt)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="trackprompt-{analysis.job_id}.json"'},
        )

    @application.get("/api/analyses/{job_id}/export.md")
    async def export_markdown(job_id: str, request: Request) -> Response:
        analysis, prompt = await _export_parts(job_id, request)
        content = analysis_markdown_export(analysis, prompt)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="trackprompt-{analysis.job_id}.md"'},
        )

    @application.get("/api/analyses/{job_id}/audio")
    async def stream_audio(job_id: str, request: Request) -> Response:
        _settings, store, _manager = _state(request)
        await _require_job(store, job_id)
        analysis_data = await asyncio.to_thread(store.read_json, job_id, "analysis.json")
        if analysis_data is None:
            raise _not_found()
        analysis = AnalysisResult.model_validate(analysis_data)
        path = store.job_dir(job_id) / "source.bin"
        if not path.is_file():
            raise _not_found()
        size = path.stat().st_size
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Disposition": 'inline; filename="track-audio"',
        }
        range_header = request.headers.get("range")
        if not range_header:
            headers["Content-Length"] = str(size)
            return StreamingResponse(
                _byte_iterator(path, 0, size - 1),
                status_code=200,
                media_type=_media_type(analysis),
                headers=headers,
            )
        match = RANGE_PATTERN.fullmatch(range_header.strip())
        if match is None:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        start_raw, end_raw = match.groups()
        if not start_raw and not end_raw:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if start_raw:
            start = int(start_raw)
            end = int(end_raw) if end_raw else size - 1
        else:
            suffix = int(end_raw)
            start = max(0, size - suffix)
            end = size - 1
        if start >= size or start < 0 or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(
            _byte_iterator(path, start, end),
            status_code=206,
            media_type=_media_type(analysis),
            headers=headers,
        )

    return application


_default_settings = Settings.from_env()
logging.basicConfig(
    level=getattr(logging, _default_settings.log_level, logging.INFO),
    format="%(levelname)s %(name)s %(message)s",
)
app = create_app(_default_settings)

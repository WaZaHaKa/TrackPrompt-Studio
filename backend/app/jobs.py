from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import traceback
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from .analysis.pipeline import AnalysisCancelled
from .analysis.sanity import validate_analysis_result
from .analysis_archive import AnalysisArchiveError
from .config import Settings
from .media import (
    MediaCancelled,
    MediaProcessError,
    MediaValidationError,
    decode_for_analysis,
    probe_media,
)
from .privacy import secure_private_file
from .prompting import generate_prompt_package
from .prompting.local_writer import derive_abstract_themes
from .schemas import (
    AnalysisMode,
    AnalysisResult,
    Confidence,
    ErrorDetail,
    JobEvent,
    JobResponse,
    JobStatus,
    PrivateLyricsTranscript,
    PromptPackage,
    PromptPreferences,
)
from .store import DeletionError, JobRecord, JobStore
from .subprocess_utils import ProcessTimedOut, ProcessWasCancelled, run_process_bounded

LOGGER = logging.getLogger("trackprompt.jobs")
TERMINAL_STATUSES = {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED, JobStatus.EXPIRED}


class JobManager:
    def __init__(self, store: JobStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.subscribers: dict[str, set[asyncio.Queue[JobEvent]]] = defaultdict(set)
        self.sequences: dict[str, int] = defaultdict(int)
        self.deleted_jobs: set[str] = set()
        self.admitted_jobs: set[str] = set()
        self.admission_lock = asyncio.Lock()
        self.worker_slots = asyncio.Semaphore(settings.analysis_workers)
        self.gpu_slots = asyncio.Semaphore(settings.gpu_task_workers)
        self.gpu_active = 0
        self.gpu_waiting = 0
        self.job_locks: dict[str, asyncio.Lock] = {}
        self.deletion_retry_tasks: dict[str, asyncio.Task[None]] = {}

    @asynccontextmanager
    async def gpu_task(self) -> AsyncIterator[None]:
        self.gpu_waiting += 1
        try:
            await self.gpu_slots.acquire()
        finally:
            self.gpu_waiting -= 1
        self.gpu_active += 1
        try:
            yield
        finally:
            self.gpu_active -= 1
            self.gpu_slots.release()

    def job_lock(self, job_id: str) -> asyncio.Lock:
        canonical = self.store.canonical_job_id(job_id)
        return self.job_locks.setdefault(canonical, asyncio.Lock())

    async def try_admit(self, job_id: str) -> bool:
        canonical = self.store.canonical_job_id(job_id)
        async with self.admission_lock:
            if canonical in self.admitted_jobs:
                return True
            if len(self.admitted_jobs) >= self.settings.max_pending_jobs:
                return False
            self.admitted_jobs.add(canonical)
            return True

    async def release_admission(self, job_id: str) -> None:
        with suppress(KeyError):
            canonical = self.store.canonical_job_id(job_id)
            async with self.admission_lock:
                self.admitted_jobs.discard(canonical)

    async def startup(self) -> None:
        reconciliation = await asyncio.to_thread(self.store.reconcile_archive)
        if reconciliation["degraded"]:
            LOGGER.warning(
                "analysis_archive_reconciliation_degraded count=%s",
                reconciliation["degraded"],
            )
        # Audio left by an interrupted server run is not resumed implicitly.
        for job_id in self.store.active_job_ids():
            self._touch_cancel(job_id)
            self._remove_private_media(job_id, keep_results=False)
            with suppress(KeyError):
                self.store.update_job(
                    job_id,
                    status=JobStatus.FAILED,
                    stage="interrupted",
                    message="Analysis was interrupted by a previous server shutdown.",
                    progress=0,
                    error_code="analysis_interrupted",
                    error_message="Analysis was interrupted; upload the track again.",
                )
            self._cancel_path(job_id).unlink(missing_ok=True)

    async def shutdown(self) -> None:
        for job_id in list(self.tasks):
            self._touch_cancel(job_id)
        if self.tasks:
            done, pending = await asyncio.wait(self.tasks.values(), timeout=5)
            for task in pending:
                task.cancel()
            for task in done:
                with suppress(Exception):
                    task.result()
        for task in self.deletion_retry_tasks.values():
            task.cancel()
        if self.deletion_retry_tasks:
            await asyncio.gather(
                *self.deletion_retry_tasks.values(),
                return_exceptions=True,
            )

    def start(self, job_id: str) -> None:
        if job_id in self.tasks:
            return
        self.tasks[job_id] = asyncio.create_task(self._run_job(job_id), name=f"analysis-{job_id}")

    def _cancel_path(self, job_id: str) -> Path:
        canonical = self.store.canonical_job_id(job_id)
        return self.settings.cancellations_dir / f"{canonical}.cancel"

    def _touch_cancel(self, job_id: str) -> None:
        path = self._cancel_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    def _cancelled(self, job_id: str) -> bool:
        return self._cancel_path(job_id).exists()

    async def _emit(self, record: JobRecord) -> JobEvent:
        self.sequences[record.job_id] += 1
        event = JobEvent(
            job_id=record.job_id,
            status=record.status,
            mode=record.effective_mode,
            stage=record.stage,
            message=record.message,
            sequence=self.sequences[record.job_id],
            progress=record.progress,
        )
        for queue in tuple(self.subscribers.get(record.job_id, set())):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        return event

    async def _transition(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        effective_mode: AnalysisMode | None = None,
        stage: str | None = None,
        message: str | None = None,
        progress: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobRecord:
        record = await asyncio.to_thread(
            self.store.update_job,
            job_id,
            status=status,
            effective_mode=effective_mode,
            stage=stage,
            message=message,
            progress=progress,
            error_code=error_code,
            error_message=error_message,
        )
        await self._emit(record)
        return record

    async def open_subscription(self, job_id: str) -> AsyncIterator[JobEvent]:
        canonical = self.store.canonical_job_id(job_id)
        queue: asyncio.Queue[JobEvent] = asyncio.Queue(maxsize=32)
        async with self.job_lock(canonical):
            record = await asyncio.to_thread(self.store.require_job, canonical)
            self.subscribers[canonical].add(queue)
            await self._emit(record)
        return self._subscription_events(canonical, queue)

    async def _subscription_events(
        self,
        job_id: str,
        queue: asyncio.Queue[JobEvent],
    ) -> AsyncIterator[JobEvent]:
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    current_record = await asyncio.to_thread(self.store.get_job, job_id)
                    if current_record is None:
                        return
                    heartbeat = JobEvent(
                        job_id=job_id,
                        status=current_record.status,
                        mode=current_record.effective_mode,
                        stage=current_record.stage,
                        message=current_record.message,
                        sequence=self.sequences[job_id],
                        progress=current_record.progress,
                    )
                    yield heartbeat
                    if heartbeat.status in TERMINAL_STATUSES:
                        return
                    continue
                yield event
                if event.status in TERMINAL_STATUSES:
                    return
        finally:
            subscribers = self.subscribers.get(job_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self.subscribers.pop(job_id, None)

    def _remove_private_media(self, job_id: str, *, keep_results: bool) -> bool:
        success = True
        with suppress(KeyError):
            directory = self.store.job_dir(job_id)
            filenames = [
                "decoded.wav",
                "progress.json",
                "progress.tmp",
                "worker-input.json",
                "worker-output.json",
                "worker-output.tmp",
            ]
            if not keep_results:
                filenames.extend(
                    [
                        "source.bin", "analysis.json", "detected-analysis.json", "prompt.json", "preferences.json",
                        "lyrics.json", "detected-lyrics.json", "lyrics-summary.json",
                        "visual-features.json",
                    ]
                )
            for filename in filenames:
                try:
                    (directory / filename).unlink(missing_ok=True)
                except OSError:
                    success = False
                if (directory / filename).exists():
                    success = False
            stems = (directory / "stems").resolve()
            if stems.parent == directory.resolve() and stems.name == "stems" and stems.exists():
                try:
                    shutil.rmtree(stems)
                except OSError:
                    success = False
                if stems.exists():
                    success = False
        return success

    def _ensure_job_active(self, job_id: str) -> None:
        if job_id in self.deleted_jobs or self._cancelled(job_id):
            raise AnalysisCancelled("Analysis was cancelled.")

    async def _terminal_transition(
        self,
        job_id: str,
        *,
        status: JobStatus,
        stage: str,
        message: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobRecord:
        async with self.job_lock(job_id):
            return await self._transition(
                job_id,
                status=status,
                stage=stage,
                message=message,
                progress=0,
                error_code=error_code,
                error_message=error_message,
            )

    async def _finalize_success(self, job_id: str, analysis: AnalysisResult) -> None:
        async with self.job_lock(job_id):
            self._ensure_job_active(job_id)
            payload = analysis.model_dump(mode="json", by_alias=True)
            await asyncio.to_thread(self.store.write_json, job_id, "analysis.json", payload)
            self._ensure_job_active(job_id)
            await asyncio.to_thread(
                self.store.write_json,
                job_id,
                "detected-analysis.json",
                payload,
            )
            self._ensure_job_active(job_id)
            if not self._remove_private_media(job_id, keep_results=True):
                raise MediaProcessError("Private intermediate cleanup was incomplete.")

            await self._transition(
                job_id,
                status=JobStatus.GENERATING_PROMPT,
                effective_mode=AnalysisMode(analysis.effective_mode),
                stage="composing_prompt",
                message="Composing the deterministic prompt package",
                progress=92,
            )
            preferences = PromptPreferences()
            package = await asyncio.to_thread(
                generate_prompt_package,
                analysis,
                preferences,
                self.settings,
            )
            self._ensure_job_active(job_id)
            await asyncio.to_thread(
                self.store.write_json,
                job_id,
                "prompt.json",
                package.model_dump(mode="json", by_alias=True),
            )
            self._ensure_job_active(job_id)
            await asyncio.to_thread(
                self.store.write_json,
                job_id,
                "preferences.json",
                preferences.model_dump(mode="json", by_alias=True),
            )
            self._ensure_job_active(job_id)
            await asyncio.to_thread(self.store.archive_completed, job_id)
            self._ensure_job_active(job_id)
            await self._transition(
                job_id,
                status=JobStatus.GENERATING_PROMPT,
                effective_mode=AnalysisMode(analysis.effective_mode),
                stage="finalizing",
                message="Finalizing the private analysis workspace",
                progress=98,
            )
            self._ensure_job_active(job_id)
            await self._transition(
                job_id,
                status=JobStatus.COMPLETED,
                effective_mode=AnalysisMode(analysis.effective_mode),
                stage="completed",
                message="Analysis and prompt are ready",
                progress=100,
            )

    async def _retry_deferred_delete(self, job_id: str) -> bool:
        deleted = False
        async with self.job_lock(job_id):
            try:
                deleted = await asyncio.to_thread(self.store.delete_job, job_id)
            except DeletionError:
                return False
            # Once durable/private state is gone, no observer should still see
            # a retry advertised for this job. The loop's finally block keeps
            # this idempotent when the current task removes its own registry
            # entry before returning.
            self.deletion_retry_tasks.pop(job_id, None)
            self._cancel_path(job_id).unlink(missing_ok=True)
            self.deleted_jobs.discard(job_id)
            self.sequences.pop(job_id, None)
            self.subscribers.pop(job_id, None)
            await self.release_admission(job_id)
        self.job_locks.pop(job_id, None)
        return deleted

    def _schedule_delete_retry(self, job_id: str) -> None:
        existing = self.deletion_retry_tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        self.deletion_retry_tasks[job_id] = asyncio.create_task(
            self._delete_retry_loop(job_id),
            name=f"private-delete-retry-{job_id}",
        )

    async def _delete_retry_loop(self, job_id: str) -> None:
        try:
            for delay in (0.25, 0.5, 1.0, 2.0, 4.0, 5.0, 5.0, 5.0):
                await asyncio.sleep(delay)
                await self._retry_deferred_delete(job_id)
                record = await asyncio.to_thread(self.store.get_job, job_id)
                if record is None and not self.store.job_dir(job_id).exists():
                    return
        finally:
            self.deletion_retry_tasks.pop(job_id, None)

    async def _cancel_queued_job(self, job_id: str) -> None:
        cleanup_complete = self._remove_private_media(job_id, keep_results=False)
        if job_id not in self.deleted_jobs and self.store.get_job(job_id) is not None:
            await self._terminal_transition(
                job_id,
                status=JobStatus.CANCELLED,
                stage="cancelled",
                message=(
                    "Queued analysis was cancelled and private media was removed"
                    if cleanup_complete
                    else "Queued analysis was cancelled; private media cleanup will be retried"
                ),
                error_code=None if cleanup_complete else "cleanup_incomplete",
                error_message=None if cleanup_complete else "Private media cleanup is incomplete.",
            )

    async def _run_job(self, job_id: str) -> None:
        acquired = False
        gpu_acquired = False
        try:
            while not acquired:
                if self._cancelled(job_id):
                    await self._cancel_queued_job(job_id)
                    return
                try:
                    await asyncio.wait_for(self.worker_slots.acquire(), timeout=0.1)
                    acquired = True
                except TimeoutError:
                    continue
            record = await asyncio.to_thread(self.store.require_job, job_id)
            heavy = (
                record.requested_mode == AnalysisMode.DEEP
                or record.enable_genre_analysis
                or record.enable_lyrical_analysis
            )
            if heavy:
                self.gpu_waiting += 1
                try:
                    await self.gpu_slots.acquire()
                    gpu_acquired = True
                finally:
                    self.gpu_waiting -= 1
                self.gpu_active += 1
            await self._run_job_with_slot(job_id)
        except asyncio.CancelledError:
            if not acquired:
                await self._cancel_queued_job(job_id)
            raise
        finally:
            if gpu_acquired:
                self.gpu_active -= 1
                self.gpu_slots.release()
            if acquired:
                self.worker_slots.release()
            self.tasks.pop(job_id, None)
            await self.release_admission(job_id)
            if job_id not in self.deleted_jobs:
                self._cancel_path(job_id).unlink(missing_ok=True)
            else:
                await self._retry_deferred_delete(job_id)

    async def _run_job_with_slot(self, job_id: str) -> None:
        try:
            record = await asyncio.to_thread(self.store.require_job, job_id)
            if self._cancelled(job_id):
                raise AnalysisCancelled("Analysis was cancelled.")
            await self._transition(
                job_id,
                status=JobStatus.VALIDATING,
                stage="validating",
                message="Validating the actual media container and audio stream",
                progress=5,
            )
            directory = self.store.job_dir(job_id)
            source = directory / "source.bin"
            probe = await asyncio.to_thread(
                probe_media,
                source,
                record.display_name,
                self.settings,
                lambda: self._cancelled(job_id),
            )
            if self._cancelled(job_id):
                raise AnalysisCancelled("Analysis was cancelled.")

            await self._transition(
                job_id,
                status=JobStatus.DECODING,
                stage="decoding",
                message="Decoding a private analysis signal with metadata removed",
                progress=15,
            )
            decoded = directory / "decoded.wav"
            await asyncio.to_thread(
                decode_for_analysis,
                probe,
                decoded,
                self.settings,
                lambda: self._cancelled(job_id),
            )
            if self._cancelled(job_id):
                raise AnalysisCancelled("Analysis was cancelled.")
            await self._transition(
                job_id,
                status=JobStatus.ANALYZING_CORE,
                stage="analyzing_core",
                message="Starting deterministic CPU audio analysis",
                progress=20,
            )
            progress_file = directory / "progress.json"
            worker_input = directory / "worker-input.json"
            worker_output = directory / "worker-output.json"
            settings_payload = asdict(self.settings)
            settings_payload["data_dir"] = str(self.settings.data_dir)
            settings_payload["model_cache_dir"] = str(self.settings.model_cache_dir)
            worker_input.write_text(
                json.dumps(
                    {
                        "file": probe.file.model_dump(mode="json", by_alias=True),
                        "jobId": job_id,
                        "requestedMode": record.requested_mode.value,
                        "enableGenreAnalysis": record.enable_genre_analysis,
                        "enableLyricsAnalysis": record.enable_lyrical_analysis,
                        "lyricsConsentConfirmed": record.lyrics_consent_confirmed,
                        "deriveLyricalThemes": record.derive_lyrical_themes,
                        "settings": settings_payload,
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            secure_private_file(worker_input)
            worker_task = asyncio.create_task(
                asyncio.to_thread(
                    run_process_bounded,
                    [
                        sys.executable,
                        "-m",
                        "app.analysis.worker",
                        "--input",
                        str(worker_input),
                        "--output",
                        str(worker_output),
                        "--decoded",
                        str(decoded),
                        "--progress",
                        str(progress_file),
                        "--cancel",
                        str(self._cancel_path(job_id)),
                    ],
                    timeout_seconds=self.settings.analysis_timeout_seconds,
                    cancel_requested=lambda: self._cancelled(job_id),
                    capture_stdout=False,
                    stderr_limit=8_000,
                ),
                name=f"analysis-worker-process-{job_id}",
            )
            last_progress: tuple[str, int] | None = None
            while not worker_task.done():
                if progress_file.is_file():
                    try:
                        progress_data = json.loads(progress_file.read_text(encoding="utf-8"))
                        stage = str(progress_data["stage"])
                        message = str(progress_data["message"])
                        progress = max(0, min(99, int(progress_data["progress"])))
                        marker = (stage, progress)
                        if marker != last_progress:
                            if stage == "separating_stems":
                                status = JobStatus.SEPARATING_STEMS
                            elif stage == "running_enhanced_taggers":
                                status = JobStatus.ANALYZING_DEEP
                            elif stage == "transcribing_lyrics":
                                status = JobStatus.TRANSCRIBING_LYRICS
                            elif stage == "tagging_genre":
                                status = JobStatus.TAGGING_GENRE
                            else:
                                status = JobStatus.ANALYZING_CORE
                            await self._transition(
                                job_id,
                                status=status,
                                stage=stage,
                                message=message,
                                progress=progress,
                            )
                            last_progress = marker
                    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                        pass
                await asyncio.sleep(0.1)
            try:
                worker_result = await worker_task
            except ProcessWasCancelled as exc:
                raise AnalysisCancelled("Analysis was cancelled.") from exc
            except ProcessTimedOut as exc:
                raise MediaProcessError("The local analysis worker exceeded its hard time limit.") from exc
            except OSError as worker_exc:
                LOGGER.error(
                    "analysis_worker_failed error_type=%s last_stage=%s",
                    type(worker_exc).__name__,
                    last_progress[0] if last_progress is not None else "worker_start",
                    extra={"job_id": job_id},
                )
                raise
            if self._cancelled(job_id):
                raise AnalysisCancelled("Analysis was cancelled.")
            if (
                worker_result.returncode != 0
                or worker_result.stdout_exceeded
                or worker_result.stderr_exceeded
            ):
                safe_worker_error = worker_result.stderr.decode(
                    "ascii", errors="ignore"
                ).strip()[:80]
                LOGGER.error(
                    "analysis_worker_failed detail=%s last_stage=%s",
                    safe_worker_error if safe_worker_error.startswith("worker_error=") else "worker_error=unknown",
                    last_progress[0] if last_progress is not None else "worker_start",
                    extra={"job_id": job_id},
                )
                raise MediaProcessError("The isolated local analysis worker failed safely.")
            if not worker_output.is_file() or worker_output.stat().st_size > 20_000_000:
                raise MediaProcessError("The isolated local analysis worker produced no valid result.")
            serialized = worker_output.read_text(encoding="utf-8")
            worker_input.unlink(missing_ok=True)
            worker_output.unlink(missing_ok=True)
            if self._cancelled(job_id):
                raise AnalysisCancelled("Analysis was cancelled.")
            analysis = AnalysisResult.model_validate_json(serialized)
            if record.derive_lyrical_themes:
                lyrics_data = await asyncio.to_thread(self.store.read_json, job_id, "lyrics.json")
                if lyrics_data is not None and analysis.lyrics_summary is not None:
                    await self._transition(
                        job_id,
                        status=JobStatus.DERIVING_LYRICAL_THEMES,
                        stage="deriving_lyrical_themes",
                        message="Deriving bounded abstract themes through the isolated private local theme path",
                        progress=92,
                    )
                    transcript = PrivateLyricsTranscript.model_validate(lyrics_data)
                    themes, theme_warnings = await asyncio.to_thread(
                        derive_abstract_themes,
                        self.settings,
                        transcript,
                    )
                    analysis.lyrics_summary.abstract_themes = themes
                    analysis.lyrics_summary.theme_confidence = (
                        Confidence.MEDIUM if themes else Confidence.UNKNOWN
                    )
                    analysis.lyrics_summary.themes_user_approved = False
                    analysis.lyrics_summary.warnings = list(
                        dict.fromkeys([*analysis.lyrics_summary.warnings, *theme_warnings])
                    )
                    await asyncio.to_thread(
                        self.store.write_json,
                        job_id,
                        "lyrics-summary.json",
                        analysis.lyrics_summary.model_dump(mode="json", by_alias=True),
                    )
            await self._finalize_success(job_id, analysis)
        except (AnalysisCancelled, MediaCancelled, asyncio.CancelledError):
            cleanup_complete = self._remove_private_media(job_id, keep_results=False)
            if job_id not in self.deleted_jobs and self.store.get_job(job_id) is not None:
                await self._terminal_transition(
                    job_id,
                    status=JobStatus.CANCELLED,
                    stage="cancelled",
                    message=(
                        "Analysis was cancelled and private media was removed"
                        if cleanup_complete
                        else "Analysis was cancelled; private media cleanup is incomplete and will be retried"
                    ),
                    error_code=None if cleanup_complete else "cleanup_incomplete",
                    error_message=None if cleanup_complete else "Private media cleanup is incomplete and will be retried.",
                )
        except MediaValidationError as exc:
            cleanup_complete = self._remove_private_media(job_id, keep_results=False)
            if job_id not in self.deleted_jobs and self.store.get_job(job_id) is not None:
                await self._terminal_transition(
                    job_id,
                    status=JobStatus.FAILED,
                    stage="failed",
                    message=exc.safe_message if cleanup_complete else f"{exc.safe_message} Private media cleanup will be retried.",
                    error_code=exc.code,
                    error_message=exc.safe_message,
                )
        except AnalysisArchiveError as exc:
            cleanup_complete = self._remove_private_media(job_id, keep_results=True)
            LOGGER.error(
                "analysis_archive_failed error_type=%s",
                type(exc).__name__,
                extra={"job_id": job_id},
            )
            if job_id not in self.deleted_jobs and self.store.get_job(job_id) is not None:
                await self._terminal_transition(
                    job_id,
                    status=JobStatus.FAILED,
                    stage="archive_failed",
                    message=(
                        "Analysis completed but its persistent archive could not be verified. Canonical local results were retained."
                        if cleanup_complete
                        else "Analysis completed; archive verification and temporary cleanup require repair."
                    ),
                    error_code="analysis_archive_failed",
                    error_message="Persistent analysis archive verification failed; canonical local results were retained.",
                )
        except (MediaProcessError, ValidationError, ValueError, OSError) as exc:
            cleanup_complete = self._remove_private_media(job_id, keep_results=False)
            LOGGER.error(
                "analysis_job_failed error_type=%s frames=%s",
                type(exc).__name__,
                [
                    (Path(frame.filename).name, frame.lineno, frame.name)
                    for frame in traceback.extract_tb(exc.__traceback__)[-4:]
                ],
                extra={"job_id": job_id, "error_type": type(exc).__name__},
            )
            if job_id not in self.deleted_jobs and self.store.get_job(job_id) is not None:
                await self._terminal_transition(
                    job_id,
                    status=JobStatus.FAILED,
                    stage="failed",
                    message=(
                        "Analysis could not be completed safely."
                        if cleanup_complete
                        else "Analysis failed and private media cleanup will be retried."
                    ),
                    error_code="analysis_failed",
                    error_message="Analysis could not be completed safely.",
                )
        except Exception as exc:
            cleanup_complete = self._remove_private_media(job_id, keep_results=False)
            LOGGER.error(
                "analysis_job_failed error_type=%s frames=%s",
                type(exc).__name__,
                [
                    (Path(frame.filename).name, frame.lineno, frame.name)
                    for frame in traceback.extract_tb(exc.__traceback__)[-4:]
                ],
                extra={"job_id": job_id, "error_type": type(exc).__name__},
            )
            if job_id not in self.deleted_jobs and self.store.get_job(job_id) is not None:
                await self._terminal_transition(
                    job_id,
                    status=JobStatus.FAILED,
                    stage="failed",
                    message=(
                        "An unexpected local analysis error occurred."
                        if cleanup_complete
                        else "Analysis failed and private media cleanup will be retried."
                    ),
                    error_code="internal_error",
                    error_message="An unexpected local analysis error occurred.",
                )
    async def cancel(self, job_id: str) -> JobRecord:
        canonical = self.store.canonical_job_id(job_id)
        self._touch_cancel(canonical)
        try:
            async with self.job_lock(canonical):
                record = await asyncio.to_thread(self.store.require_job, canonical)
                if record.status in TERMINAL_STATUSES:
                    self._cancel_path(canonical).unlink(missing_ok=True)
                    return record
                await self._transition(
                    canonical,
                    stage="cancellation_requested",
                    message="Cancellation requested; stopping at the current safe boundary",
                )
                task = self.tasks.get(canonical)
                if task is None:
                    cleanup_complete = self._remove_private_media(canonical, keep_results=False)
                    record = await self._transition(
                        canonical,
                        status=JobStatus.CANCELLED,
                        stage="cancelled",
                        message="Analysis was cancelled",
                        progress=0,
                        error_code=None if cleanup_complete else "cleanup_incomplete",
                        error_message=None if cleanup_complete else "Private media cleanup is incomplete.",
                    )
                    self._cancel_path(canonical).unlink(missing_ok=True)
                    return record
        except KeyError:
            self._cancel_path(canonical).unlink(missing_ok=True)
            raise
        with suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        return await asyncio.to_thread(self.store.require_job, canonical)

    async def delete(self, job_id: str) -> None:
        canonical = self.store.canonical_job_id(job_id)
        self.deleted_jobs.add(canonical)
        self._touch_cancel(canonical)
        async with self.job_lock(canonical):
            record = await asyncio.to_thread(self.store.get_job, canonical)
            if record is not None and record.status not in TERMINAL_STATUSES:
                await self._transition(
                    canonical,
                    status=JobStatus.CANCELLED,
                    stage="deleting",
                    message="Deleting private analysis data",
                    progress=0,
                )
            task = self.tasks.get(canonical)
        if task is not None:
            with suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            if not task.done():
                self._schedule_delete_retry(canonical)
                raise DeletionError(
                    "Analysis is still stopping; a private-data deletion retry is scheduled."
                )
        async with self.job_lock(canonical):
            try:
                await asyncio.to_thread(self.store.delete_job, canonical)
            except DeletionError as exc:
                self._schedule_delete_retry(canonical)
                raise DeletionError(
                    "Private files are still in use; background deletion retry is scheduled."
                ) from exc
            self._cancel_path(canonical).unlink(missing_ok=True)
            self.deleted_jobs.discard(canonical)
            self.sequences.pop(canonical, None)
            self.subscribers.pop(canonical, None)
            await self.release_admission(canonical)
        self.job_locks.pop(canonical, None)

    async def response(self, job_id: str) -> JobResponse:
        record = await asyncio.to_thread(self.store.require_job, job_id)
        analysis_data = await asyncio.to_thread(self.store.read_json, job_id, "analysis.json")
        lyrics_data = await asyncio.to_thread(self.store.read_json, job_id, "lyrics.json")
        prompt_data = await asyncio.to_thread(self.store.read_json, job_id, "prompt.json")
        analysis = (
            validate_analysis_result(
                AnalysisResult.model_validate(analysis_data),
                private_lyrics_artifact_available=lyrics_data is not None,
            )
            if analysis_data
            else None
        )
        prompt = PromptPackage.model_validate(prompt_data) if prompt_data else None
        error = (
            ErrorDetail(code=record.error_code, message=record.error_message or record.message)
            if record.error_code
            else None
        )
        return JobResponse(
            job_id=record.job_id,
            status=record.status,
            requested_mode=record.requested_mode,
            mode=record.effective_mode,
            stage=record.stage,
            message=record.message,
            progress=record.progress,
            analysis=analysis,
            prompt_package=prompt,
            error=error,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

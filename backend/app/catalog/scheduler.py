from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from .. import __version__
from ..jobs import JobManager
from ..privacy import secure_private_file
from ..schemas import AnalysisMode, JobStatus
from ..store import JobStore
from ..subprocess_utils import ProcessTimedOut, run_process_bounded
from .schemas import BatchPatch, BatchState, QueueState
from .store import CatalogueStore


def _safe_failure_reason(exc: Exception) -> str:
    message = str(exc).strip()
    if (
        isinstance(exc, RuntimeError)
        and message
        and "/" not in message
        and "\\" not in message
        and "\n" not in message
        and "\r" not in message
    ):
        return message[:500]
    return f"Child analysis failed safely ({type(exc).__name__})."


class CatalogueScheduler:
    """Durable fair child-analysis dispatcher.

    Queue state is persisted before dispatch. Restart recovery changes an
    interrupted `running` item back to `queued`; the ordinary analysis manager
    independently fails and cleans its interrupted temporary job.
    """

    def __init__(
        self,
        catalog: CatalogueStore,
        jobs: JobStore,
        manager: JobManager,
    ) -> None:
        self.catalog = catalog
        self.jobs = jobs
        self.manager = manager
        self._wake = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._batch_cursor = 0
        self._cancelled_batches: set[str] = set()

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    def wake(self) -> None:
        self._wake.set()

    async def startup(self) -> None:
        await asyncio.to_thread(self.catalog.cleanup_abandoned_uploads)
        await asyncio.to_thread(self.catalog.recover_queue)
        self._runner = asyncio.create_task(self._loop(), name="catalogue-scheduler")
        self.wake()

    async def shutdown(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner
        for item_id, task in list(self._tasks.items()):
            context = await asyncio.to_thread(self.catalog.queue_context, item_id)
            job_id = context.get("job_id")
            if isinstance(job_id, str):
                with suppress(KeyError):
                    await self.manager.cancel(job_id)
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def cancel_batch(self, batch_id: str) -> None:
        self._cancelled_batches.add(batch_id)
        for item_id in list(self._tasks):
            context = await asyncio.to_thread(self.catalog.queue_context, item_id)
            if str(context["batch_id"]) != batch_id:
                continue
            job_id = context.get("job_id")
            if isinstance(job_id, str):
                with suppress(KeyError):
                    await self.manager.cancel(job_id)

    async def _loop(self) -> None:
        while True:
            self._tasks = {item_id: task for item_id, task in self._tasks.items() if not task.done()}
            while len(self._tasks) < self.catalog.settings.max_active_analyses:
                item = await asyncio.to_thread(self._next_item)
                if item is None:
                    break
                item_id = str(item["id"])
                task = asyncio.create_task(self._process(item_id), name=f"catalogue-item-{item_id}")
                self._tasks[item_id] = task
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except TimeoutError:
                pass

    def _next_item(self) -> dict[str, object] | None:
        batch_ids = self.catalog.queued_batch_ids()
        if not batch_ids:
            return None
        self._batch_cursor %= len(batch_ids)
        for advance in range(len(batch_ids)):
            batch_id = batch_ids[(self._batch_cursor + advance) % len(batch_ids)]
            item = self.catalog.next_queued_item(batch_id)
            if item is not None:
                self._batch_cursor = (self._batch_cursor + advance + 1) % len(batch_ids)
                return item
        return None

    async def _extract_range(
        self,
        source: Path,
        destination: Path,
        *,
        start_seconds: float,
        duration_seconds: float,
    ) -> None:
        args = [
            self.catalog.settings.ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_seconds:.6f}",
            "-t",
            f"{duration_seconds:.6f}",
            "-i",
            str(source),
            "-map_metadata",
            "-1",
            "-vn",
            "-sn",
            "-dn",
            "-c:a",
            "flac",
            "-f",
            "flac",
            "-y",
            str(destination),
        ]
        try:
            result = await asyncio.to_thread(
                run_process_bounded,
                args,
                timeout_seconds=max(
                    self.catalog.settings.subprocess_timeout_seconds,
                    min(1_800, round(duration_seconds * 2)),
                ),
                capture_stdout=False,
                stderr_limit=32_000,
            )
        except ProcessTimedOut as exc:
            raise RuntimeError("Bounded child-range decoding timed out") from exc
        if result.returncode != 0 or result.stderr_exceeded or not destination.is_file():
            destination.unlink(missing_ok=True)
            raise RuntimeError("FFmpeg could not decode the bounded child range")
        secure_private_file(destination)

    async def _process(self, item_id: str) -> None:
        job_id: str | None = None
        try:
            context = await asyncio.to_thread(self.catalog.queue_context, item_id)
            if str(context["batch_state"]) not in {
                BatchState.QUEUED.value,
                BatchState.RUNNING.value,
            }:
                return
            start = float(context["stable_core_start_seconds"])
            end = float(context["stable_core_end_seconds"])
            used_stable_core = end - start >= 20.0
            if not used_stable_core:
                start = float(context["start_seconds"])
                end = float(context["end_seconds"])
            duration = end - start
            if duration <= 0:
                raise RuntimeError("The reviewed segment has no analyzable range")
            limit = self.catalog.settings.max_single_track_analysis_seconds
            if duration > limit:
                raise RuntimeError(
                    f"The reviewed child range exceeds the configured {limit}-second analysis limit"
                )
            job_id = str(uuid4())
            if not await self.manager.try_admit(job_id):
                self.wake()
                return
            await asyncio.to_thread(
                self.jobs.create_job,
                job_id,
                AnalysisMode(str(context["analysis_mode"])),
                f"{str(context['segment_label'])[:96]}.flac",
                True,
                bool(context["enable_lyrical_analysis"]),
                bool(context["enable_genre_analysis"]),
                bool(context["lyrics_consent_confirmed"]),
                False,
                True,
            )
            await asyncio.to_thread(
                self.catalog.transition_queue_item,
                item_id,
                QueueState.RUNNING,
                job_id=job_id,
            )
            source = self.catalog.asset_source_path(str(context["source_asset_id"]))
            destination = self.jobs.job_dir(job_id) / "source.bin"
            await self._extract_range(
                source,
                destination,
                start_seconds=start,
                duration_seconds=duration,
            )
            self.manager.start(job_id)
            terminal = None
            while terminal is None:
                response = await self.manager.response(job_id)
                if response.status in {
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                    JobStatus.EXPIRED,
                }:
                    terminal = response
                    break
                await asyncio.sleep(0.5)
            if terminal.status != JobStatus.COMPLETED:
                if str(context["batch_id"]) in self._cancelled_batches:
                    await asyncio.to_thread(
                        self.catalog.transition_queue_item,
                        item_id,
                        QueueState.CANCELLED,
                        job_id=job_id,
                        failure_reason="Cancelled with batch",
                    )
                    return
                message = terminal.error.message if terminal.error else terminal.message
                raise RuntimeError(message)
            analysis = await asyncio.to_thread(self.jobs.read_json, job_id, "analysis.json")
            if analysis is None:
                raise RuntimeError("Completed child analysis has no stored result")
            analysis["sourceRange"] = {
                "sourceAssetId": str(context["source_asset_id"]),
                "segmentId": str(context["segment_id"]),
                "startSeconds": float(context["start_seconds"]),
                "endSeconds": float(context["end_seconds"]),
                "stableCoreStartSeconds": float(context["stable_core_start_seconds"]),
                "stableCoreEndSeconds": float(context["stable_core_end_seconds"]),
                "transitionInStartSeconds": context["transition_in_start_seconds"],
                "transitionInEndSeconds": context["transition_in_end_seconds"],
                "transitionOutStartSeconds": context["transition_out_start_seconds"],
                "transitionOutEndSeconds": context["transition_out_end_seconds"],
                "analysisUsedStableCore": used_stable_core,
            }
            if not used_stable_core:
                warnings = analysis.setdefault("warnings", [])
                if isinstance(warnings, list):
                    warnings.append(
                        "No sufficiently long stable core was available; the bounded full segment includes transition evidence."
                    )
            content = json.dumps(
                analysis,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            await asyncio.to_thread(
                self.catalog.register_artifact,
                project_id=str(context["project_id"]),
                batch_id=str(context["batch_id"]),
                owner_type="segment",
                owner_id=str(context["segment_id"]),
                artifact_type="analysis_json",
                schema_version=str(analysis.get("schemaVersion", "unknown")),
                media_type="application/json",
                content=content,
                producer_versions={
                    "trackprompt": __version__,
                    "analysis": str(analysis.get("analysisVersion", "unknown")),
                },
                reason="Bounded child analysis",
            )
            prompt = await asyncio.to_thread(self.jobs.read_json, job_id, "prompt.json")
            if prompt is not None:
                prompt_content = json.dumps(
                    prompt,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                await asyncio.to_thread(
                    self.catalog.register_artifact,
                    project_id=str(context["project_id"]),
                    batch_id=str(context["batch_id"]),
                    owner_type="segment",
                    owner_id=str(context["segment_id"]),
                    artifact_type="prompt_json",
                    schema_version=str(prompt.get("schemaVersion", "unknown")),
                    media_type="application/json",
                    content=prompt_content,
                    producer_versions={"trackprompt": __version__},
                    reason="Bounded child prompt package",
                )
            await asyncio.to_thread(
                self.catalog.set_segment_job_id, str(context["segment_id"]), job_id
            )
            await asyncio.to_thread(
                self.catalog.transition_queue_item,
                item_id,
                QueueState.COMPLETED,
                job_id=job_id,
            )
        except asyncio.CancelledError:
            if job_id is not None:
                with suppress(KeyError):
                    await self.manager.cancel(job_id)
            with suppress(KeyError):
                await asyncio.to_thread(
                    self.catalog.transition_queue_item,
                    item_id,
                    QueueState.QUEUED,
                    failure_reason="Scheduler shutdown recovery",
                )
            raise
        except Exception as exc:
            with suppress(KeyError):
                context = await asyncio.to_thread(self.catalog.queue_context, item_id)
                state = (
                    QueueState.CANCELLED
                    if str(context["batch_id"]) in self._cancelled_batches
                    else QueueState.FAILED
                )
                await asyncio.to_thread(
                    self.catalog.transition_queue_item,
                    item_id,
                    state,
                    job_id=job_id,
                    failure_reason=_safe_failure_reason(exc),
                )
        finally:
            if job_id is not None:
                with suppress(KeyError, OSError):
                    await self.manager.delete(job_id)
                await self.manager.release_admission(job_id)
            self._tasks.pop(item_id, None)
            self.wake()
            with suppress(KeyError):
                context = await asyncio.to_thread(self.catalog.queue_context, item_id)
                remaining = await asyncio.to_thread(
                    self.catalog.list_queue_items,
                    str(context["batch_id"]),
                    states=(QueueState.QUEUED.value, QueueState.RUNNING.value),
                )
                if not remaining:
                    batch_id = str(context["batch_id"])
                    batch = await asyncio.to_thread(self.catalog.get_batch, batch_id)
                    if str(batch["state"]) != BatchState.CANCELLED.value:
                        await asyncio.to_thread(
                            self.catalog.patch_batch,
                            batch_id,
                            BatchPatch(state=BatchState.COMPLETED),
                        )
                    self._cancelled_batches.discard(batch_id)

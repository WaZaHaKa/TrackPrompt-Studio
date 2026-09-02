from __future__ import annotations

import asyncio
from contextlib import suppress

from .schemas import SegmentationJobState
from .segmentation import LongformScanCancelled, segment_longform_source
from .store import CatalogueStore


class LongformScanScheduler:
    """One-at-a-time durable scheduler for bounded long-form source scans."""

    def __init__(self, catalog: CatalogueStore) -> None:
        self.catalog = catalog
        self._wake = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._active: asyncio.Task[None] | None = None
        self._active_job_id: str | None = None
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._stopping = False

    async def startup(self) -> None:
        await asyncio.to_thread(self.catalog.recover_segmentation_jobs)
        self._runner = asyncio.create_task(self._loop(), name="longform-scan-scheduler")
        self.wake()

    async def shutdown(self) -> None:
        self._stopping = True
        if self._active_job_id is not None:
            self._cancel_events.setdefault(self._active_job_id, asyncio.Event()).set()
        if self._active is not None:
            await asyncio.gather(self._active, return_exceptions=True)
        if self._runner is not None:
            self._runner.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner

    def wake(self) -> None:
        self._wake.set()

    async def start_asset(self, asset_id: str) -> dict[str, object]:
        result = await asyncio.to_thread(self.catalog.create_segmentation_job, asset_id)
        self.wake()
        return result

    async def cancel(self, job_id: str) -> dict[str, object]:
        result = await asyncio.to_thread(self.catalog.request_segmentation_cancel, job_id)
        if result["state"] == SegmentationJobState.RUNNING:
            self._cancel_events.setdefault(job_id, asyncio.Event()).set()
        else:
            self._cancel_events.pop(job_id, None)
        self.wake()
        return result

    async def _loop(self) -> None:
        while True:
            if self._active is not None and self._active.done():
                await asyncio.gather(self._active, return_exceptions=True)
                self._active = None
                self._active_job_id = None
            if self._active is None:
                queued = await asyncio.to_thread(self.catalog.next_queued_segmentation_job)
                if queued is not None:
                    job_id = str(queued["id"])
                    self._active_job_id = job_id
                    self._active = asyncio.create_task(
                        self._process(job_id), name=f"longform-scan-{job_id}"
                    )
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.5)
            except TimeoutError:
                pass

    async def _process(self, job_id: str) -> None:
        cancel_event = self._cancel_events.setdefault(job_id, asyncio.Event())
        started = asyncio.get_running_loop().time()
        try:
            job = await asyncio.to_thread(self.catalog.get_segmentation_job, job_id)
            asset_id = str(job["asset_id"])
            asset = await asyncio.to_thread(self.catalog.get_asset, asset_id)
            await asyncio.to_thread(
                self.catalog.update_segmentation_job,
                job_id,
                state=SegmentationJobState.RUNNING,
                stage="Inspecting media",
                progress=1,
            )

            def progress(stage: str, value: int) -> None:
                self.catalog.update_segmentation_job(
                    job_id,
                    stage=stage,
                    progress=value,
                )

            result = await asyncio.to_thread(
                segment_longform_source,
                asset_id,
                self.catalog.asset_source_path(asset_id),
                float(asset["duration_seconds"]),
                self.catalog.settings,
                cancel_requested=cancel_event.is_set,
                progress_callback=progress,
            )
            await asyncio.to_thread(
                self.catalog.replace_segments,
                asset_id,
                result.segments,
                reason="Deterministic multi-signal long-form scan",
                detected=True,
            )
            await asyncio.to_thread(
                self.catalog.update_segmentation_job,
                job_id,
                state=SegmentationJobState.COMPLETED,
                stage="Awaiting boundary review",
                progress=100,
                observation_count=result.observation_count,
                candidate_count=result.candidate_count,
                refined_boundary_count=max(0, len(result.segments) - 1),
                peak_buffer_bytes=result.peak_buffer_bytes,
                elapsed_seconds=result.elapsed_seconds,
                audit_terminal=True,
            )
        except LongformScanCancelled:
            state = (
                SegmentationJobState.QUEUED
                if self._stopping
                else SegmentationJobState.CANCELLED
            )
            await asyncio.to_thread(
                self.catalog.update_segmentation_job,
                job_id,
                state=state,
                stage=("Recovered after backend shutdown" if self._stopping else "Cancelled"),
                elapsed_seconds=round(asyncio.get_running_loop().time() - started, 3),
                error_code=None,
                audit_terminal=not self._stopping,
            )
        except Exception as exc:
            await asyncio.to_thread(
                self.catalog.update_segmentation_job,
                job_id,
                state=SegmentationJobState.FAILED,
                stage="Source scan failed",
                elapsed_seconds=round(asyncio.get_running_loop().time() - started, 3),
                error_code=type(exc).__name__,
                audit_terminal=True,
            )
        finally:
            self._cancel_events.pop(job_id, None)
            self.wake()

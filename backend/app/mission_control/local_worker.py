from __future__ import annotations

import asyncio
import math
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

from .render_contracts import ShotRenderTask, WorkerCapabilities, WorkerLease
from .scheduler import LeaseGrant, ScheduledRenderTask, SchedulerWorker


class LocalWorkerError(RuntimeError):
    """A safe local worker configuration or execution failure."""


class LocalWorkerRunStatus(StrEnum):
    COMPLETED = "completed"
    REQUEUED = "requeued"


@dataclass(frozen=True, slots=True)
class LocalTaskCommand:
    arguments: tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.arguments or any(
            not isinstance(argument, str)
            or not argument
            or "\x00" in argument
            for argument in self.arguments
        ):
            raise LocalWorkerError(
                "local task commands must be non-empty safe argument arrays"
            )
        if not self.working_directory.is_dir():
            raise LocalWorkerError("local task working directory does not exist")
        if any(
            not isinstance(key, str)
            or not key
            or "\x00" in key
            or not isinstance(value, str)
            or "\x00" in value
            for key, value in self.environment.items()
        ):
            raise LocalWorkerError("local task environment is invalid")


@dataclass(frozen=True, slots=True)
class LocalWorkerRunResult:
    status: LocalWorkerRunStatus
    task_id: str
    task_sha256: str
    lease_id: str
    attempt: int
    exit_code: int | None
    captured_output_bytes: int
    output_truncated: bool
    reason: str | None = None


class LocalTaskCommandBuilder(Protocol):
    def build(
        self,
        task: ShotRenderTask,
        *,
        worker_id: str,
    ) -> LocalTaskCommand: ...


class RenderWorkerControlPlane(Protocol):
    def register_render_worker(
        self,
        capabilities: WorkerCapabilities,
        *,
        now: datetime | None = None,
        heartbeat_timeout: timedelta | None = None,
    ) -> SchedulerWorker: ...

    async def claim_render_task(
        self,
        worker_id: str,
        *,
        job_id: str | None = None,
        now: datetime | None = None,
        lease_duration: timedelta | None = None,
    ) -> LeaseGrant | None: ...

    async def start_scheduled_render_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> ScheduledRenderTask: ...

    async def heartbeat_scheduled_render_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
        lease_duration: timedelta | None = None,
        worker_timeout: timedelta | None = None,
    ) -> WorkerLease: ...

    async def complete_scheduled_render_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> ScheduledRenderTask: ...

    async def fail_scheduled_render_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: str,
        *,
        retry: bool = True,
        reason: str = "worker-reported-failure",
        now: datetime | None = None,
    ) -> ScheduledRenderTask: ...


class AsyncByteReader(Protocol):
    async def read(self, count: int = -1) -> bytes: ...


class AsyncLocalProcess(Protocol):
    pid: int
    returncode: int | None
    stdout: AsyncByteReader | None

    async def wait(self) -> int: ...

    def kill(self) -> None: ...


LocalProcessFactory = Callable[
    [Sequence[str], Mapping[str, str], Path],
    Awaitable[AsyncLocalProcess],
]
LocalProcessTerminator = Callable[[AsyncLocalProcess], Awaitable[None]]


class TrackPromptTaskCommandBuilder:
    """Map one immutable task to the provider-neutral local worker CLI."""

    def __init__(
        self,
        *,
        package_directory: Path,
        blender_executable: Path,
        artifact_root: Path,
        worker_script: Path,
        python_executable: Path | None = None,
        inner_timeout_seconds: float = 21_600,
        max_log_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.package_directory = package_directory.resolve(strict=True)
        self.blender_executable = blender_executable.resolve(strict=True)
        self.worker_script = worker_script.resolve(strict=True)
        self.python_executable = (
            python_executable or Path(sys.executable)
        ).resolve(strict=True)
        artifact_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root = artifact_root.resolve(strict=True)
        if (
            not math.isfinite(inner_timeout_seconds)
            or not 1 <= inner_timeout_seconds <= 86_400
        ):
            raise LocalWorkerError(
                "inner render timeout must be between 1 second and 24 hours"
            )
        if not 1024 <= max_log_bytes <= 64 * 1024 * 1024:
            raise LocalWorkerError(
                "worker log limit must be between 1 KiB and 64 MiB"
            )
        self.inner_timeout_seconds = inner_timeout_seconds
        self.max_log_bytes = max_log_bytes

    def build(
        self,
        task: ShotRenderTask,
        *,
        worker_id: str,
    ) -> LocalTaskCommand:
        relative_root = PurePosixPath(task.output_root)
        output_directory = (
            self.artifact_root.joinpath(*relative_root.parts)
            / "worker-returns"
            / task.id
            / f"attempt-{task.attempt:03d}"
        ).resolve()
        if (
            output_directory == self.artifact_root
            or self.artifact_root not in output_directory.parents
        ):
            raise LocalWorkerError("task output root escapes the local artifact store")
        environment = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "TRACKPROMPT_RENDER_TASK_ID": task.id,
            "TRACKPROMPT_RENDER_TASK_SHA256": task.task_sha256,
            "TRACKPROMPT_RENDER_JOB_ID": task.job_id,
            "TRACKPROMPT_RENDER_PACKAGE_SHA256": task.package_sha256,
            "TRACKPROMPT_RENDER_SCENE_SHA256": task.scene_sha256,
            "TRACKPROMPT_OUTPUT_VARIANT_ID": task.output_variant_id,
            "TRACKPROMPT_OUTPUT_VARIANT_SHA256": task.output_variant_sha256,
            "TRACKPROMPT_OUTPUT_MATRIX_SHA256": task.matrix_sha256,
            "TRACKPROMPT_OUTPUT_WIDTH": str(task.width),
            "TRACKPROMPT_OUTPUT_HEIGHT": str(task.height),
            "TRACKPROMPT_OUTPUT_FPS": str(task.fps),
            "TRACKPROMPT_RENDER_PROFILE_SHA256": task.render_profile_sha256,
            "TRACKPROMPT_COMPOSITION_SHA256": task.composition_sha256,
            "TRACKPROMPT_RENDER_CHUNK_ID": task.chunk_id,
            "TRACKPROMPT_RENDER_SHOT_ID": task.shot_id,
            "TRACKPROMPT_RENDER_TASK_ATTEMPT": str(task.attempt),
        }
        return LocalTaskCommand(
            arguments=(
                str(self.python_executable),
                str(self.worker_script),
                "--package-directory",
                str(self.package_directory),
                "--blender",
                str(self.blender_executable),
                "--start",
                str(task.frame_start),
                "--end",
                str(task.frame_end),
                "--worker-id",
                worker_id,
                "--output-directory",
                str(output_directory),
                "--render-timeout-seconds",
                str(self.inner_timeout_seconds),
                "--max-log-bytes",
                str(self.max_log_bytes),
            ),
            working_directory=self.package_directory,
            environment=environment,
        )


async def _spawn_local_process(
    arguments: Sequence[str],
    environment: Mapping[str, str],
    working_directory: Path,
) -> AsyncLocalProcess:
    child_environment = os.environ.copy()
    child_environment.update(environment)
    if os.name == "nt":
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(working_directory),
            env=child_environment,
            creationflags=int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            ),
        )
    else:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(working_directory),
            env=child_environment,
            start_new_session=True,
        )
    return cast(AsyncLocalProcess, process)


async def _terminate_local_process_tree(process: AsyncLocalProcess) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt" and process.pid > 0:
        try:
            cleanup = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(cleanup.wait(), timeout=10)
        except (OSError, TimeoutError):
            process.kill()
    else:
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()


@dataclass(slots=True)
class _OutputCapture:
    captured_bytes: int = 0
    truncated: bool = False


async def _drain_output(
    reader: AsyncByteReader,
    capture: _OutputCapture,
    maximum_bytes: int,
) -> None:
    while chunk := await reader.read(65_536):
        remaining = max(0, maximum_bytes - capture.captured_bytes)
        capture.captured_bytes += min(remaining, len(chunk))
        if len(chunk) > remaining:
            capture.truncated = True


class LocalSubprocessRenderWorker:
    """Run one claimed render task while maintaining its authenticated lease."""

    def __init__(
        self,
        controller: RenderWorkerControlPlane,
        capabilities: WorkerCapabilities,
        command_builder: LocalTaskCommandBuilder,
        *,
        process_factory: LocalProcessFactory = _spawn_local_process,
        process_terminator: LocalProcessTerminator = _terminate_local_process_tree,
        heartbeat_interval_seconds: float = 5,
        lease_duration: timedelta = timedelta(seconds=30),
        worker_timeout: timedelta = timedelta(seconds=30),
        task_timeout_seconds: float = 21_660,
        max_captured_output_bytes: int = 1_048_576,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not math.isfinite(heartbeat_interval_seconds)
            or heartbeat_interval_seconds <= 0
        ):
            raise LocalWorkerError("heartbeat interval must be positive and finite")
        if lease_duration.total_seconds() <= heartbeat_interval_seconds:
            raise LocalWorkerError("lease duration must exceed the heartbeat interval")
        if worker_timeout.total_seconds() <= heartbeat_interval_seconds:
            raise LocalWorkerError("worker timeout must exceed the heartbeat interval")
        if not math.isfinite(task_timeout_seconds) or task_timeout_seconds <= 0:
            raise LocalWorkerError("task timeout must be positive and finite")
        if max_captured_output_bytes < 0:
            raise LocalWorkerError("captured output limit cannot be negative")
        self.controller = controller
        self.capabilities = capabilities
        self.command_builder = command_builder
        self.process_factory = process_factory
        self.process_terminator = process_terminator
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.lease_duration = lease_duration
        self.worker_timeout = worker_timeout
        self.task_timeout_seconds = task_timeout_seconds
        self.max_captured_output_bytes = max_captured_output_bytes
        self.now = now or (lambda: datetime.now(UTC))

    async def run_once(
        self,
        *,
        job_id: str | None = None,
    ) -> LocalWorkerRunResult | None:
        registered_at = self.now()
        self.controller.register_render_worker(
            self.capabilities,
            now=registered_at,
            heartbeat_timeout=self.worker_timeout,
        )
        grant = await self.controller.claim_render_task(
            self.capabilities.worker_id,
            job_id=job_id,
            now=registered_at,
            lease_duration=self.lease_duration,
        )
        if grant is None:
            return None
        token = grant.lease_token.get_secret_value()
        try:
            command = self.command_builder.build(
                grant.task,
                worker_id=self.capabilities.worker_id,
            )
        except Exception:
            failed = await self.controller.fail_scheduled_render_task(
                grant.lease.id,
                self.capabilities.worker_id,
                token,
                retry=True,
                reason="command-build-failed",
                now=self.now(),
            )
            return self._requeued_result(
                grant,
                failed,
                exit_code=None,
                capture=_OutputCapture(),
                reason="command-build-failed",
            )
        await self.controller.start_scheduled_render_task(
            grant.lease.id,
            self.capabilities.worker_id,
            token,
            now=self.now(),
        )
        environment = dict(command.environment)
        environment["TRACKPROMPT_WORKER_LEASE_ID"] = grant.lease.id
        try:
            process = await self.process_factory(
                command.arguments,
                environment,
                command.working_directory,
            )
        except (OSError, ValueError):
            failed = await self.controller.fail_scheduled_render_task(
                grant.lease.id,
                self.capabilities.worker_id,
                token,
                retry=True,
                reason="subprocess-start-failed",
                now=self.now(),
            )
            return self._requeued_result(
                grant,
                failed,
                exit_code=None,
                capture=_OutputCapture(),
                reason="subprocess-start-failed",
            )

        capture = _OutputCapture()
        try:
            exit_code = await self._monitor_process(
                process,
                grant,
                token,
                capture,
            )
        except TimeoutError:
            await self.process_terminator(process)
            failed = await self.controller.fail_scheduled_render_task(
                grant.lease.id,
                self.capabilities.worker_id,
                token,
                retry=True,
                reason="subprocess-timeout",
                now=self.now(),
            )
            return self._requeued_result(
                grant,
                failed,
                exit_code=None,
                capture=capture,
                reason="subprocess-timeout",
            )
        except asyncio.CancelledError:
            await self.process_terminator(process)
            try:
                await self.controller.fail_scheduled_render_task(
                    grant.lease.id,
                    self.capabilities.worker_id,
                    token,
                    retry=True,
                    reason="worker-cancelled",
                    now=self.now(),
                )
            except Exception:
                pass
            raise
        except Exception:
            await self.process_terminator(process)
            try:
                await self.controller.fail_scheduled_render_task(
                    grant.lease.id,
                    self.capabilities.worker_id,
                    token,
                    retry=True,
                    reason="worker-monitor-failed",
                    now=self.now(),
                )
            except Exception:
                pass
            raise

        if exit_code != 0:
            failed = await self.controller.fail_scheduled_render_task(
                grant.lease.id,
                self.capabilities.worker_id,
                token,
                retry=True,
                reason="subprocess-exit-nonzero",
                now=self.now(),
            )
            return self._requeued_result(
                grant,
                failed,
                exit_code=exit_code,
                capture=capture,
                reason="subprocess-exit-nonzero",
            )
        completed = await self.controller.complete_scheduled_render_task(
            grant.lease.id,
            self.capabilities.worker_id,
            token,
            now=self.now(),
        )
        return LocalWorkerRunResult(
            status=LocalWorkerRunStatus.COMPLETED,
            task_id=completed.task.id,
            task_sha256=completed.task.task_sha256,
            lease_id=grant.lease.id,
            attempt=completed.attempt,
            exit_code=0,
            captured_output_bytes=capture.captured_bytes,
            output_truncated=capture.truncated,
        )

    async def _monitor_process(
        self,
        process: AsyncLocalProcess,
        grant: LeaseGrant,
        token: str,
        capture: _OutputCapture,
    ) -> int:
        if process.stdout is None:
            raise LocalWorkerError("local subprocess output pipe is unavailable")
        output_task = asyncio.create_task(
            _drain_output(
                process.stdout,
                capture,
                self.max_captured_output_bytes,
            )
        )
        wait_task = asyncio.create_task(process.wait())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.task_timeout_seconds
        try:
            while not wait_task.done():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.wait(
                    {wait_task},
                    timeout=min(self.heartbeat_interval_seconds, remaining),
                )
                if wait_task.done():
                    break
                await self.controller.heartbeat_scheduled_render_task(
                    grant.lease.id,
                    self.capabilities.worker_id,
                    token,
                    now=self.now(),
                    lease_duration=self.lease_duration,
                    worker_timeout=self.worker_timeout,
                )
            exit_code = int(await wait_task)
            try:
                await asyncio.wait_for(output_task, timeout=5)
            except TimeoutError:
                output_task.cancel()
                await asyncio.gather(output_task, return_exceptions=True)
                capture.truncated = True
            return exit_code
        finally:
            if not wait_task.done():
                wait_task.cancel()
            if not output_task.done():
                output_task.cancel()
            await asyncio.gather(wait_task, output_task, return_exceptions=True)

    @staticmethod
    def _requeued_result(
        grant: LeaseGrant,
        failed: ScheduledRenderTask,
        *,
        exit_code: int | None,
        capture: _OutputCapture,
        reason: str,
    ) -> LocalWorkerRunResult:
        return LocalWorkerRunResult(
            status=LocalWorkerRunStatus.REQUEUED,
            task_id=grant.task.id,
            task_sha256=grant.task.task_sha256,
            lease_id=grant.lease.id,
            attempt=failed.attempt,
            exit_code=exit_code,
            captured_output_bytes=capture.captured_bytes,
            output_truncated=capture.truncated,
            reason=reason,
        )

from __future__ import annotations

import asyncio
import math
import os
import shutil
import struct
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from ..cinematic.schemas import (
    ArtDirectionReview,
    ArtDirectionReviewCollection,
    ShotPlan,
    StoryPlan,
)
from ..cinematic.validation import validate_cinematic_privacy, validate_plan_pair
from .config import MissionControlConfig
from .discovery import (
    MissionDiscovery,
    atomic_write_json,
    load_json_object,
    validate_authorization_record,
)
from .errors import MissionControlError
from .models import (
    AuthorizationResult,
    CalibrationPlanRequest,
    CalibrationPlanResult,
    CalibrationSummary,
    CancelStopRequest,
    CheckStatus,
    CloudReadiness,
    ComponentStatus,
    DirectorWorkspace,
    DryRunResult,
    EncodeJobStatus,
    EncodeReadiness,
    EncodeStartRequest,
    FakeRenderOptions,
    JobPhase,
    JobRecord,
    JobState,
    LogPage,
    MissionSettings,
    MissionSettingsPatch,
    NativePickerRequest,
    NativePickerResponse,
    OpenPathRequest,
    OpenPathResult,
    OutputCreateChildResult,
    OutputInspection,
    PerformanceEnableRequest,
    PerformanceRestoreRequest,
    PerformanceStatus,
    PreflightCheck,
    PreflightRequest,
    PreflightResult,
    ProfileSummary,
    ProfileValidation,
    ProjectSummary,
    RendererKind,
    RenderEvent,
    RenderIdentity,
    ResumeRequest,
    RuntimeIdentity,
    SafeStopStatus,
    SceneSummary,
    StructuredError,
    SystemHealth,
    SystemPaths,
    SystemStatus,
)
from .outputs import OutputManager
from .processes import find_descendant_process_id, process_is_alive
from .renderers import (
    FakeRenderer,
    ProductionRenderer,
    artifact_progress_changes,
    inspect_render_artifacts,
)
from .store import MissionControlStore
from .system_adapters import NativePicker, PerformanceAdapter

_ACTIVE_STATES = {
    JobState.STARTING,
    JobState.RUNNING,
    JobState.STOP_REQUESTED,
    JobState.FINISHING_CURRENT_CHUNK,
    JobState.ENCODING,
    JobState.VERIFYING,
}
_TERMINAL_STATES = {JobState.COMPLETE, JobState.FAILED, JobState.CANCELLED}
_PREVIEW_FALLBACK_WINDOW = 256
_ENCODE_ACTIVE_STATUSES = {"queued", "encoding", "verifying"}
_ENCODE_SETTING_PREFIX = "encode_job:"
_DIRECTOR_FILE_LIMIT = 2_000_000


def _with_unbound_performance_detail(status: PerformanceStatus) -> PerformanceStatus:
    if (
        status.restore_required
        and not status.blender_process_id
        and "manual restore" not in status.detail.casefold()
    ):
        return status.model_copy(
            update={
                "detail": (
                    f"{status.detail.rstrip()} No Blender process is bound; "
                    "this session will remain active until an explicit manual restore."
                )
            }
        )
    return status


class MissionControlService:
    def __init__(
        self,
        config: MissionControlConfig,
        *,
        runtime: RuntimeIdentity | None = None,
    ) -> None:
        config.ensure_directories()
        self.config = config
        self.runtime = runtime
        self.discovery = MissionDiscovery(config)
        self.outputs = OutputManager(self.discovery)
        self.store = MissionControlStore(
            config.database_path,
            event_retention=config.event_retention,
        )
        self.production_renderer = ProductionRenderer(config)
        self.fake_renderer = FakeRenderer()
        self.native_picker = NativePicker(config)
        self.performance = PerformanceAdapter(config)
        self._event_condition = asyncio.Condition()
        self._event_generation = self.store.latest_event_sequence()
        self._job_lock = asyncio.Lock()
        self.gpu_operation_lock = asyncio.Lock()
        self._preview_cache: dict[str, tuple[float, Path | None]] = {}
        self._encode_tasks: dict[str, asyncio.Task[None]] = {}
        self._orphan_monitor_task: asyncio.Task[None] | None = None
        self._closed = False
        self._recover_jobs()
        self._event_generation = self.store.latest_event_sequence()
        self.start_background_tasks()

    def close(self) -> None:
        self._closed = True
        if self._orphan_monitor_task is not None:
            self._orphan_monitor_task.cancel()
            self._orphan_monitor_task = None
        self.store.close()

    def start_background_tasks(self) -> None:
        if self._closed or (
            self._orphan_monitor_task is not None
            and not self._orphan_monitor_task.done()
        ):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._orphan_monitor_task = loop.create_task(
            self._monitor_orphaned_jobs(),
            name="mission-control-orphan-monitor",
        )

    def _recover_jobs(self) -> None:
        for job in self.store.list_jobs(limit=1_000):
            if job.state not in _ACTIVE_STATES:
                continue
            process_alive = self._process_alive(job.process_id)
            now = datetime.now(UTC)
            if process_alive:
                recovered = job.model_copy(
                    update={
                        "orphaned": True,
                        "updated_at": now,
                        "renderer_active": True,
                        "watcher_active": False,
                        "current_frame_started_at": None,
                        "warning": "An existing renderer process was detected after Mission Control restarted.",
                    }
                )
            else:
                recovered = self._recovered_dead_job(job, now=now)
            self.store.put_job(recovered)
            self.store.append_event(self._event_from_job(recovered))

    def _process_alive(self, process_id: int | None) -> bool:
        return process_is_alive(process_id)

    async def _monitor_orphaned_jobs(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(1.0)
                async with self._job_lock:
                    await self._reconcile_orphaned_jobs_locked()
        except asyncio.CancelledError:
            return

    async def _reconcile_orphaned_jobs_locked(self) -> None:
        for job in self.store.list_jobs(limit=1_000):
            if (
                not job.orphaned
                or job.state not in _ACTIVE_STATES
                or self._process_alive(job.process_id)
            ):
                continue
            recovered = self._recovered_dead_job(job, now=datetime.now(UTC))
            self.store.put_job(recovered)
            event = self.store.append_event(self._event_from_job(recovered))
            await self._notify_event(event.sequence)

    def _recovered_dead_job(self, job: JobRecord, *, now: datetime) -> JobRecord:
        base: dict[str, object] = {
            "process_id": None,
            "orphaned": False,
            "updated_at": now,
            "renderer_active": False,
            "watcher_active": False,
            "current_frame_started_at": None,
        }
        if job.renderer == RendererKind.FAKE:
            base.update(
                state=JobState.RESUMABLE,
                warning="The fake renderer stopped with the prior Mission Control process; resume is available.",
                error=StructuredError(
                    code="fake_renderer_interrupted",
                    title="Fake render was interrupted",
                    summary="The deterministic fake renderer task did not survive the Mission Control restart.",
                    recommended_action="Resume the fake render from its last persisted chunk.",
                    retryable=True,
                    timestamp=now,
                    job_id=job.id,
                ),
            )
            return job.model_copy(update=base)
        snapshot = inspect_render_artifacts(job)
        base.update(artifact_progress_changes(job, snapshot))
        if snapshot.disposition == "complete":
            base.update(
                state=JobState.COMPLETE,
                phase=JobPhase.FINAL_VERIFY,
                completed_at=now,
                safe_stop_status=SafeStopStatus.NONE,
                warning=None,
                error=None,
            )
        elif snapshot.disposition == "paused":
            base.update(
                state=JobState.PAUSED_SAFELY,
                phase=JobPhase.PUBLISH_CHUNK,
                safe_stop_status=SafeStopStatus.PAUSED,
                warning="The authoritative manifest confirms a safe operator stop.",
                error=None,
            )
        elif snapshot.disposition in {"resumable", "missing"}:
            base.update(
                state=JobState.RESUMABLE,
                safe_stop_status=SafeStopStatus.NONE,
                warning=(
                    "The prior renderer is no longer active; exact-identity resume is available."
                ),
                error=StructuredError(
                    code="renderer_exit_unobserved",
                    title="Renderer exit was not observed",
                    summary="Mission Control restarted before it observed the renderer's final exit code.",
                    likely_cause=snapshot.reason,
                    recommended_action="Inspect the saved output and resume the exact render if more frames remain.",
                    retryable=True,
                    context={"artifactState": snapshot.disposition},
                    timestamp=now,
                    job_id=job.id,
                ),
            )
        else:
            base.update(
                state=JobState.FAILED,
                completed_at=now,
                safe_stop_status=SafeStopStatus.NONE,
                warning=None,
                error=StructuredError(
                    code="render_artifact_recovery_failed",
                    title="Render state could not be recovered",
                    summary="The saved production artifacts do not match this job's exact render contract.",
                    likely_cause=snapshot.reason,
                    recommended_action="Inspect the output manifest before attempting any resume.",
                    retryable=False,
                    context={"artifactState": snapshot.disposition},
                    timestamp=now,
                    job_id=job.id,
                ),
            )
        return job.model_copy(update=base)

    def health(self) -> SystemHealth:
        return SystemHealth(
            status="ok" if self.store.healthcheck() else "degraded",
            instance=self.runtime,
        )

    def paths(self) -> SystemPaths:
        blender = self._blender_path()
        ffmpeg = self._ffmpeg_path()
        return SystemPaths(
            repository_root=str(self.config.repository_root),
            profile_root=str(self.config.profile_root),
            calibration_root=str(self.config.calibration_root),
            state_root=str(self.config.state_root),
            default_output_root=str(self._default_output_root()),
            blender_path=str(blender) if blender is not None else None,
            ffmpeg_path=str(ffmpeg) if ffmpeg is not None else None,
            powershell_path=self.production_renderer.powershell_path(),
        )

    def _blender_path(self) -> Path | None:
        configured = os.getenv("TRACKPROMPT_MC_BLENDER_PATH")
        candidates = [
            Path(configured) if configured else None,
            Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"),
        ]
        for candidate in candidates:
            if candidate is not None and candidate.is_file():
                return candidate.resolve()
        command = shutil.which("blender.exe") or shutil.which("blender")
        return Path(command).resolve() if command else None

    def _ffmpeg_path(self) -> Path | None:
        configured = os.getenv("TRACKPROMPT_MC_FFMPEG_PATH")
        command = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
        candidates = [Path(configured) if configured else None, Path(command) if command else None]
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if resolved.is_file():
                return resolved
        return None

    def _ffprobe_path(self, ffmpeg: Path) -> Path | None:
        configured = os.getenv("TRACKPROMPT_MC_FFPROBE_PATH")
        command = shutil.which("ffprobe.exe") or shutil.which("ffprobe")
        candidates = [
            Path(configured) if configured else None,
            ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe"),
            Path(command) if command else None,
        ]
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if resolved.is_file():
                return resolved
        return None

    def _default_output_root(self) -> Path:
        stored = self.store.get_setting("default_output_root")
        if isinstance(stored, str) and stored.strip():
            candidate = Path(stored)
            if candidate.is_absolute():
                return candidate
        return self.config.default_output_root

    async def settings(self) -> MissionSettings:
        performance = _with_unbound_performance_detail(await self.performance.status())
        theme = self.store.get_setting("theme", "system")
        if theme not in {"system", "light", "dark"}:
            theme = "system"
        preferred_drive = self.store.get_setting("preferred_drive")
        return MissionSettings(
            theme=theme,
            preferred_drive=preferred_drive if isinstance(preferred_drive, str) else None,
            default_output_root=str(self._default_output_root()),
            performance_mode_enabled=performance.active,
            performance_mode_available=performance.available,
            performance_mode_detail=performance.detail,
            fake_renderer_available=self.config.allow_fake_renderer,
        )

    async def patch_settings(self, patch: MissionSettingsPatch) -> MissionSettings:
        if patch.theme is not None:
            self.store.put_setting("theme", patch.theme)
        if patch.preferred_drive is not None:
            value = patch.preferred_drive.strip()
            if value and (len(value) != 2 or value[1] != ":" or not value[0].isalpha()):
                raise MissionControlError(
                    422,
                    "invalid_preferred_drive",
                    "Preferred drive is invalid",
                    "Preferred drive must use a Windows drive label such as D:.",
                    "Select a local drive from system settings.",
                )
            self.store.put_setting("preferred_drive", value or None)
        if patch.default_output_root is not None:
            output = self.outputs.validated_path(patch.default_output_root)
            self.store.put_setting("default_output_root", str(output))
        return await self.settings()

    async def system_status(self) -> SystemStatus:
        profiles = self.discovery.list_profiles()
        scenes = self.discovery.list_scenes()
        blender = self._blender_path()
        powershell = self.production_renderer.powershell_path()
        ffmpeg = self._ffmpeg_path()
        current = next((job for job in self.store.list_jobs() if job.state in _ACTIVE_STATES), None)
        components = [
            ComponentStatus(
                id="profile-discovery",
                label="Saved profiles",
                status=CheckStatus.PASS if profiles else CheckStatus.FAIL,
                detail=f"{len(profiles)} saved render profile(s) discovered." if profiles else "No saved render profiles were found.",
            ),
            ComponentStatus(
                id="scene-discovery",
                label="Approved scenes",
                status=CheckStatus.PASS if any(scene.verified for scene in scenes) else CheckStatus.FAIL,
                detail=f"{sum(scene.verified for scene in scenes)} approved scene(s) verified.",
            ),
            ComponentStatus(
                id="blender",
                label="Blender",
                status=CheckStatus.PASS if blender else CheckStatus.FAIL,
                detail="Blender 5.2 was found." if blender else "Blender 5.2 was not found.",
                path=str(blender) if blender else None,
            ),
            ComponentStatus(
                id="powershell",
                label="PowerShell",
                status=CheckStatus.PASS if powershell else CheckStatus.FAIL,
                detail="PowerShell is ready." if powershell else "PowerShell was not found.",
                path=powershell,
            ),
            ComponentStatus(
                id="ffmpeg",
                label="FFmpeg",
                status=CheckStatus.PASS if ffmpeg else CheckStatus.FAIL,
                detail="The verified local encoder was found." if ffmpeg else "FFmpeg was not found.",
                path=str(ffmpeg) if ffmpeg else None,
            ),
            ComponentStatus(
                id="state-store",
                label="Mission Control state",
                status=CheckStatus.PASS if self.store.healthcheck() else CheckStatus.FAIL,
                detail="Persistent local state is available." if self.store.healthcheck() else "Persistent local state is unavailable.",
            ),
        ]
        return SystemStatus(
            status="ready" if all(item.status != CheckStatus.FAIL for item in components) else "needs_attention",
            current_job_id=current.id if current else None,
            recommended_profile_id=self.discovery.recommended_profile_id(),
            components=components,
        )

    async def select_folder(self, request: NativePickerRequest) -> NativePickerResponse:
        return await self.native_picker.choose(
            "folder",
            initial_directory=request.initial_directory,
            title=request.title,
        )

    async def select_file(self, request: NativePickerRequest) -> NativePickerResponse:
        return await self.native_picker.choose(
            "file",
            initial_directory=request.initial_directory,
            title=request.title,
        )

    async def open_path(self, request: OpenPathRequest) -> OpenPathResult:
        if os.name != "nt":
            raise MissionControlError(
                409,
                "open_path_unavailable",
                "Open in Explorer is unavailable",
                "This action requires the Windows Mission Control host.",
                "Open the output path manually on the host.",
            )
        job = self.get_job(request.job_id)
        output = Path(job.identity.output_directory).resolve(strict=True)
        target = Path(request.path).expanduser().resolve(strict=True) if request.path else output
        try:
            target.relative_to(output)
        except ValueError as exc:
            raise MissionControlError(
                403,
                "open_path_outside_job_output",
                "Path cannot be opened",
                "Mission Control only opens paths inside the selected job's output directory.",
                "Choose an output artifact from this render job.",
                job_id=job.id,
            ) from exc
        try:
            await asyncio.to_thread(
                subprocess.Popen,
                ["explorer.exe", str(target)],
                shell=False,
                close_fds=True,
            )
        except OSError as exc:
            raise MissionControlError(
                409,
                "open_path_failed",
                "Path could not be opened",
                "Windows Explorer could not open the selected output path.",
                "Open the output path manually or retry.",
                retryable=True,
                technical_details=type(exc).__name__,
                job_id=job.id,
            ) from exc
        return OpenPathResult(opened=True, path=str(target))

    def projects(self) -> list[ProjectSummary]:
        return self.discovery.list_projects()

    def scenes(self) -> list[SceneSummary]:
        return self.discovery.list_scenes()

    def scene(self, scene_id: str) -> SceneSummary:
        return self.discovery.get_scene(scene_id)

    def profiles(self) -> list[ProfileSummary]:
        return self.discovery.list_profiles()

    def profile(self, profile_id: str) -> ProfileSummary:
        return self.discovery.get_profile(profile_id)

    def validate_profile(self, profile_id: str) -> ProfileValidation:
        return self.discovery.validate_profile(profile_id)

    def authorize_profile(
        self,
        profile_id: str,
        scene_id: str,
        *,
        settings_and_hashes_reviewed: bool,
        production_render_authorized: bool,
    ) -> AuthorizationResult:
        return self.discovery.authorize_profile(
            profile_id,
            scene_id,
            settings_and_hashes_reviewed=settings_and_hashes_reviewed,
            production_render_authorized=production_render_authorized,
        )

    def inspect_output(
        self,
        path: str,
        *,
        profile_id: str | None,
        scene_id: str | None,
    ) -> OutputInspection:
        return self.outputs.inspect(path, profile_id=profile_id, scene_id=scene_id)

    def create_output_child(
        self,
        parent_directory: str,
        *,
        project_id: str,
        profile_id: str,
        base_name: str | None,
    ) -> OutputCreateChildResult:
        return self.outputs.create_child(
            parent_directory,
            project_id=project_id,
            profile_id=profile_id,
            base_name=base_name,
        )

    def _identity(self, request: PreflightRequest) -> RenderIdentity:
        path = self.outputs.validated_path(request.output_directory)
        profile = self.discovery.get_profile(request.profile_id)
        scene = self.discovery.get_scene(request.scene_id)
        if profile.project_id != request.project_id or scene.project_id != request.project_id:
            raise MissionControlError(
                409,
                "render_project_identity_mismatch",
                "Render configuration does not match",
                "Project, scene, and profile do not belong to one exact render configuration.",
                "Return to project selection and choose matching items.",
            )
        if profile.scene_id != scene.id or profile.scene_sha256 != scene.sha256:
            raise MissionControlError(
                409,
                "render_scene_identity_mismatch",
                "Render configuration does not match",
                "The selected profile is bound to another exact approved scene.",
                "Choose the profile's approved scene.",
            )
        return RenderIdentity(
            project_id=request.project_id,
            scene_id=scene.id,
            scene_sha256=scene.sha256,
            profile_id=profile.id,
            profile_sha256=profile.saved_file_sha256,
            output_directory=str(path),
        )

    async def preflight(
        self,
        request: PreflightRequest,
        *,
        run_engine: bool = True,
    ) -> PreflightResult:
        identity = self._identity(request)
        profile = self.discovery.get_profile(request.profile_id)
        scene = self.discovery.get_scene(request.scene_id)
        validation = self.discovery.validate_profile(request.profile_id)
        output = self.outputs.inspect(
            request.output_directory,
            profile_id=request.profile_id,
            scene_id=request.scene_id,
        )
        blender = self._blender_path()
        blender_ready = blender is not None or request.renderer == RendererKind.FAKE
        checks = [
            PreflightCheck(
                id="blender",
                label="Blender found",
                status=CheckStatus.PASS if blender_ready else CheckStatus.FAIL,
                summary=(
                    "Blender 5.2 is ready."
                    if blender
                    else "Fake renderer does not require Blender."
                    if request.renderer == RendererKind.FAKE
                    else "Blender 5.2 was not found."
                ),
            ),
            PreflightCheck(
                id="scene",
                label="Scene verified",
                status=CheckStatus.PASS if scene.verified else CheckStatus.FAIL,
                summary="Approved scene hash matches." if scene.verified else "Approved scene hash changed.",
            ),
            PreflightCheck(
                id="profile",
                label="Profile verified",
                status=CheckStatus.PASS if validation.valid else CheckStatus.FAIL,
                summary="Saved profile passed validation." if validation.valid else "Saved profile validation failed.",
                detail="; ".join(validation.errors) or None,
            ),
            PreflightCheck(
                id="resolution",
                label="Resolution verified",
                status=CheckStatus.PASS if profile.resolution.width > 0 and profile.resolution.height > 0 else CheckStatus.FAIL,
                summary=f"{profile.resolution.width}×{profile.resolution.height} at {profile.fps:g} fps.",
            ),
            PreflightCheck(
                id="frame-range",
                label="Frame range verified",
                status=CheckStatus.PASS if profile.total_frames > 0 else CheckStatus.FAIL,
                summary=f"Frames {profile.frame_start}–{profile.frame_end} ({profile.total_frames:,} total).",
            ),
            PreflightCheck(
                id="output",
                label="Output folder ready",
                status=CheckStatus.PASS if output.usable else CheckStatus.FAIL,
                summary="Output is empty or exactly resumable." if output.usable else "Output folder cannot be used.",
                detail="; ".join(output.issues) or None,
            ),
        ]
        required_bytes = (
            0
            if request.renderer == RendererKind.FAKE
            else
            int((profile.minimum_launch_free_gib or 0.0) * 1024**3)
            if profile.minimum_launch_free_gib is not None
            else None
        )
        storage_ready = (
            output.free_bytes is not None
            and (required_bytes is None or output.free_bytes >= required_bytes)
        )
        checks.append(
            PreflightCheck(
                id="storage",
                label="Storage ready",
                status=CheckStatus.PASS if storage_ready else CheckStatus.FAIL,
                summary=(
                    "Available storage meets the saved profile requirement."
                    if storage_ready
                    else "Available storage is below or could not verify the saved requirement."
                ),
            )
        )
        conflict = next(
            (
                job
                for job in self.store.list_jobs()
                if job.state in _ACTIVE_STATES and job.identity.output_directory != identity.output_directory
            ),
            None,
        )
        checks.append(
            PreflightCheck(
                id="active-render",
                label="No conflicting render",
                status=CheckStatus.PASS if conflict is None else CheckStatus.FAIL,
                summary="No conflicting render is active." if conflict is None else "Another render is currently active.",
            )
        )
        checks.append(
            PreflightCheck(
                id="authorization",
                label="Authorization",
                status=CheckStatus.PASS if validation.authorized else CheckStatus.WARNING,
                summary="Exact scene and profile are authorized." if validation.authorized else "Authorization is required before start.",
                detail="; ".join(validation.authorization_issues) or None,
            )
        )
        raw_engine_result = None
        hard_ready = all(check.status != CheckStatus.FAIL for check in checks)
        if run_engine and request.renderer == RendererKind.PRODUCTION and hard_ready:
            profile_path, _payload, _hash = self.discovery.profile_source(profile.id)
            engine = await self.production_renderer.inspect(
                scene_path=Path(scene.path),
                profile_path=profile_path,
                output_directory=Path(identity.output_directory),
                mode="preflight",
            )
            raw_engine_result = engine.payload
            checks.append(
                PreflightCheck(
                    id="production-engine",
                    label="Production inspection",
                    status=CheckStatus.PASS if engine.ok else CheckStatus.FAIL,
                    summary="Production render inspection passed." if engine.ok else "Production render inspection failed.",
                    detail=None if engine.ok else "\n".join(engine.lines[-10:]),
                )
            )
        ready = all(check.status != CheckStatus.FAIL for check in checks) and validation.authorized
        return PreflightResult(
            ready=ready,
            authorization_required=not validation.authorized,
            identity=identity,
            checks=checks,
            expected_hours=profile.expected_hours,
            required_free_bytes=required_bytes,
            available_bytes=output.free_bytes,
            raw_engine_result=raw_engine_result,
        )

    async def dry_run(self, request: PreflightRequest) -> DryRunResult:
        result = await self.preflight(request, run_engine=False)
        hard_failures = [
            check for check in result.checks if check.status == CheckStatus.FAIL
        ]
        if hard_failures:
            raise MissionControlError(
                409,
                "dry_run_preflight_failed",
                "Dry run cannot start",
                "One or more required render checks failed.",
                "Resolve the failed checks, then run the dry run again.",
                context={"failedChecks": [check.id for check in hard_failures]},
            )
        if request.renderer == RendererKind.FAKE:
            return DryRunResult(
                ok=True,
                identity=result.identity,
                plan={"renderer": "fake", "note": "No Blender process was started."},
                log_lines=["Deterministic fake render dry run passed."],
            )
        profile_path, _payload, _hash = self.discovery.profile_source(request.profile_id)
        scene = self.discovery.get_scene(request.scene_id)
        engine = await self.production_renderer.inspect(
            scene_path=Path(scene.path),
            profile_path=profile_path,
            output_directory=Path(result.identity.output_directory),
            mode="dry-run",
        )
        return DryRunResult(
            ok=engine.ok,
            identity=result.identity,
            plan=engine.payload or {},
            log_lines=list(engine.lines[-100:]),
        )

    async def start_render(
        self,
        request: PreflightRequest,
        *,
        fake_options: FakeRenderOptions | None = None,
    ) -> JobRecord:
        async with self._job_lock:
            await self._reconcile_orphaned_jobs_locked()
            identity = self._identity(request)
            for existing in self.store.list_jobs():
                if existing.state in _ACTIVE_STATES:
                    if existing.identity == identity:
                        return existing
                    raise MissionControlError(
                        409,
                        "conflicting_render_active",
                        "Another render is active",
                        "Mission Control allows only one production GPU render at a time.",
                        "Return to the active render or wait for a safe stop.",
                        context={"activeJobId": existing.id},
                        job_id=existing.id,
                    )
            if request.renderer == RendererKind.FAKE and not self.config.allow_fake_renderer:
                raise MissionControlError(
                    403,
                    "fake_renderer_disabled",
                    "Fake renderer is disabled",
                    "The deterministic renderer is available only in explicitly configured test instances.",
                    "Start a test Mission Control instance with fake rendering enabled.",
                )
            preflight = await self.preflight(
                request,
                run_engine=request.renderer == RendererKind.PRODUCTION,
            )
            if preflight.authorization_required:
                raise MissionControlError(
                    409,
                    "authorization_required",
                    "Authorization required",
                    "This profile is valid and ready, but it has not been authorized for a full render.",
                    "Authorize now, then continue directly to Start Render.",
                    retryable=True,
                    context={"profileId": request.profile_id, "sceneId": request.scene_id},
                )
            if not preflight.ready:
                raise MissionControlError(
                    409,
                    "render_preflight_failed",
                    "Render is not ready",
                    "One or more required production checks failed.",
                    "Resolve failed checks, then run preflight again.",
                    context={
                        "failedChecks": [
                            check.id for check in preflight.checks if check.status == CheckStatus.FAIL
                        ]
                    },
                )
            profile = self.discovery.get_profile(request.profile_id)
            profile_path, profile_payload, _profile_hash = self.discovery.profile_source(
                profile.id
            )
            scene_path = Path(self.discovery.get_scene(request.scene_id).path)
            valid, issues, token = validate_authorization_record(
                profile_path,
                scene_path,
                profile_payload,
            )
            if not valid:
                raise MissionControlError(
                    409,
                    "authorization_invalidated",
                    "Authorization is no longer valid",
                    "The exact authorization record failed read-back validation.",
                    "Authorize the current scene and saved profile again.",
                    context={"issues": issues},
                )
            now = datetime.now(UTC)
            total = (
                fake_options.total_frames
                if request.renderer == RendererKind.FAKE and fake_options and fake_options.total_frames
                else profile.total_frames
            )
            chunk_size = (
                fake_options.frames_per_chunk
                if request.renderer == RendererKind.FAKE and fake_options and fake_options.frames_per_chunk
                else profile.frames_per_chunk
            )
            job = JobRecord(
                id=str(uuid4()),
                renderer=request.renderer,
                state=JobState.STARTING,
                phase=JobPhase.SCENE_LOAD,
                identity=identity,
                created_at=now,
                updated_at=now,
                frame_start=profile.frame_start,
                frame_end=profile.frame_start + total - 1,
                total_frame_count=total,
                chunks_total=max(1, math.ceil(total / max(1, chunk_size))),
                projected_storage_bytes=(
                    int((profile.planned_frame_sequence_gib or 0.0) * 1024**3)
                    if profile.planned_frame_sequence_gib is not None
                    else None
                ),
                free_storage_bytes=preflight.available_bytes,
            )
            self.store.put_job(job)
            stored_event = self.store.append_event(self._event_from_job(job))
            await self._notify_event(stored_event.sequence)
            try:
                if request.renderer == RendererKind.FAKE:
                    self.fake_renderer.start(
                        self,
                        job,
                        fake_options or FakeRenderOptions(),
                    )
                else:
                    self.production_renderer.start(
                        self,
                        job,
                        scene_path=scene_path,
                        profile_path=profile_path,
                        authorization_token=token,
                    )
            except MissionControlError as exc:
                await self.fail_job(
                    job.id,
                    exc.error.model_copy(update={"job_id": job.id}),
                )
                raise
            except Exception as exc:
                start_error = StructuredError(
                    code="renderer_start_failed",
                    title="Render process could not start",
                    summary="Mission Control could not start the selected renderer.",
                    likely_cause=type(exc).__name__,
                    recommended_action="Review system readiness, then retry the render.",
                    retryable=True,
                    timestamp=datetime.now(UTC),
                    job_id=job.id,
                )
                await self.fail_job(
                    job.id,
                    start_error,
                )
                raise MissionControlError(
                    500,
                    start_error.code,
                    start_error.title,
                    start_error.summary,
                    start_error.recommended_action,
                    retryable=True,
                    technical_details=type(exc).__name__,
                    job_id=job.id,
                ) from exc
            return job

    def get_job(self, job_id: str) -> JobRecord:
        job = self.store.get_job(job_id)
        if job is None:
            raise MissionControlError(
                404,
                "render_job_not_found",
                "Render job was not found",
                "The selected render job does not exist in persistent Mission Control state.",
                "Refresh job history and select an available job.",
            )
        return job

    def jobs(self) -> list[JobRecord]:
        return self.store.list_jobs()

    @property
    def _analysis_jobs_root(self) -> Path:
        return (self.config.state_root.parent / "jobs").resolve()

    def _director_job_directory(self, analysis_job_id: str) -> Path:
        try:
            canonical = str(UUID(analysis_job_id))
        except ValueError as exc:
            raise MissionControlError(
                404,
                "director_workspace_not_found",
                "Director workspace was not found",
                "The requested local cinematic workspace does not exist.",
                "Compile a local cinematic plan, then reopen Director.",
            ) from exc
        directory = (self._analysis_jobs_root / canonical).resolve()
        if directory.parent != self._analysis_jobs_root or not directory.is_dir():
            raise MissionControlError(
                404,
                "director_workspace_not_found",
                "Director workspace was not found",
                "The requested local cinematic workspace does not exist.",
                "Compile a local cinematic plan, then reopen Director.",
            )
        return directory

    @staticmethod
    def _director_json(path: Path, label: str) -> dict[str, object]:
        try:
            if path.stat().st_size > _DIRECTOR_FILE_LIMIT:
                raise MissionControlError(
                    422,
                    "director_artifact_too_large",
                    "Director artifact is invalid",
                    f"{label} exceeds the local review size limit.",
                    "Recompile the bounded cinematic plan.",
                )
        except OSError as exc:
            raise MissionControlError(
                404,
                "director_artifact_missing",
                "Director artifact is unavailable",
                f"{label} is unavailable.",
                "Recompile the local cinematic plan before opening Director.",
            ) from exc
        return load_json_object(path, label)

    def _load_director_workspace(self, directory: Path) -> DirectorWorkspace:
        story_path = directory / "story-plan.json"
        shot_path = directory / "shot-plan.json"
        story = StoryPlan.model_validate(self._director_json(story_path, "Story plan"))
        shots = ShotPlan.model_validate(self._director_json(shot_path, "Shot plan"))
        validate_plan_pair(story, shots)
        review_path = directory / "art-direction-reviews.json"
        reviews = (
            ArtDirectionReviewCollection.model_validate(
                self._director_json(review_path, "Art-direction reviews")
            )
            if review_path.is_file()
            else ArtDirectionReviewCollection()
        )
        updated_timestamp = max(
            path.stat().st_mtime
            for path in (story_path, shot_path, review_path)
            if path.is_file()
        )
        return DirectorWorkspace(
            analysis_job_id=directory.name,
            story_plan=story,
            shot_plan=shots,
            reviews=reviews,
            updated_at=datetime.fromtimestamp(updated_timestamp, tz=UTC),
        )

    def director_workspace(self) -> DirectorWorkspace | None:
        root = self._analysis_jobs_root
        if not root.is_dir():
            return None
        candidates = [
            path
            for path in root.iterdir()
            if path.is_dir()
            and (path / "story-plan.json").is_file()
            and (path / "shot-plan.json").is_file()
        ]
        if not candidates:
            return None
        latest = max(
            candidates,
            key=lambda path: max(
                (path / "story-plan.json").stat().st_mtime,
                (path / "shot-plan.json").stat().st_mtime,
            ),
        )
        try:
            return self._load_director_workspace(latest)
        except (ValueError, OSError) as exc:
            raise MissionControlError(
                422,
                "director_workspace_invalid",
                "Director workspace is invalid",
                "The latest local story or shot plan failed validation.",
                "Recompile the cinematic plan before reviewing shots.",
                technical_details=type(exc).__name__,
            ) from exc

    def put_director_review(
        self,
        analysis_job_id: str,
        shot_id: str,
        review: ArtDirectionReview,
    ) -> DirectorWorkspace:
        directory = self._director_job_directory(analysis_job_id)
        workspace = self._load_director_workspace(directory)
        shot = next((candidate for candidate in workspace.shot_plan.shots if candidate.id == shot_id), None)
        if (
            review.shot_id != shot_id
            or shot is None
            or not shot.frame_start <= review.review_frame <= shot.frame_end
        ):
            raise MissionControlError(
                422,
                "director_review_invalid",
                "Director review is invalid",
                "The review shot and representative frame must match the local shot plan.",
                "Select a representative frame from the current local shot plan.",
            )
        items = [item for item in workspace.reviews.reviews if item.shot_id != shot_id]
        items.append(review)
        reviews = ArtDirectionReviewCollection(reviews=sorted(items, key=lambda item: item.shot_id))
        payload = reviews.model_dump(mode="json", by_alias=True)
        validate_cinematic_privacy(payload)
        atomic_write_json(directory / "art-direction-reviews.json", payload)
        return self._load_director_workspace(directory)

    async def update_job(self, job_id: str, **changes: object) -> JobRecord:
        job = self.get_job(job_id)
        changes["updated_at"] = datetime.now(UTC)
        updated = job.model_copy(update=changes)
        self.store.put_job(updated)
        event = self.store.append_event(self._event_from_job(updated))
        await self._notify_event(event.sequence)
        return updated

    async def add_log(
        self,
        job_id: str,
        level: Literal["debug", "info", "warning", "error"],
        message: str,
    ) -> None:
        self.get_job(job_id)
        self.store.append_log(job_id, level, message)

    async def fail_job(self, job_id: str, error: StructuredError) -> JobRecord:
        await self.add_log(job_id, "error", error.summary)
        return await self.update_job(
            job_id,
            state=JobState.FAILED,
            process_id=None,
            renderer_active=False,
            watcher_active=False,
            current_frame_started_at=None,
            error=error,
            completed_at=datetime.now(UTC),
        )

    def _event_from_job(self, job: JobRecord) -> RenderEvent:
        return RenderEvent(
            sequence=0,
            timestamp=datetime.now(UTC),
            job_id=job.id,
            project_id=job.identity.project_id,
            state=job.state,
            phase=job.phase,
            scene_id=job.identity.scene_id,
            scene_sha256=job.identity.scene_sha256,
            profile_id=job.identity.profile_id,
            profile_sha256=job.identity.profile_sha256,
            renderer_active=job.renderer_active,
            watcher_active=job.watcher_active,
            current_frame_started_at=job.current_frame_started_at,
            last_output_at=job.last_output_at,
            frame_start=job.frame_start,
            frame_end=job.frame_end,
            current_frame=job.current_frame,
            latest_rendered_frame=job.latest_rendered_frame,
            renderer_event_type=job.renderer_event_type,
            renderer_event_sequence=job.renderer_event_sequence,
            renderer_status=job.renderer_status,
            worker_id=job.worker_id,
            active_chunk_id=job.active_chunk_id,
            current_act_id=job.current_act_id,
            current_act_name=job.current_act_name,
            current_shot_id=job.current_shot_id,
            current_shot_name=job.current_shot_name,
            rendered_frame_count=job.rendered_frame_count,
            inflight_frame_count=job.inflight_frame_count,
            validated_frame_count=job.validated_frame_count,
            published_frame_count=job.published_frame_count,
            total_frame_count=job.total_frame_count,
            chunk_start=job.chunk_start,
            chunk_end=job.chunk_end,
            current_chunk_progress=job.current_chunk_progress,
            chunks_completed=job.chunks_completed,
            chunks_total=job.chunks_total,
            current_seconds_per_frame=job.current_seconds_per_frame,
            rolling_median_seconds=job.rolling_median_seconds,
            rolling_mean_seconds=job.rolling_mean_seconds,
            p90_seconds=job.p90_seconds,
            estimated_completion_time=job.estimated_completion_time,
            eta_confidence=job.eta_confidence,
            current_storage_bytes=job.current_storage_bytes,
            projected_storage_bytes=job.projected_storage_bytes,
            free_storage_bytes=job.free_storage_bytes,
            gpu_utilization_percent=job.gpu_utilization_percent,
            vram_used_mib=job.vram_used_mib,
            gpu_temperature_c=job.gpu_temperature_c,
            cpu_utilization_percent=job.cpu_utilization_percent,
            ram_used_mib=job.ram_used_mib,
            latest_frame_preview=job.latest_frame_preview,
            latest_preview_frame=job.latest_preview_frame,
            latest_preview_at=job.latest_preview_at,
            latest_log_line=job.latest_log_line,
            warning=job.warning,
            error=job.error,
            safe_stop_status=job.safe_stop_status,
        )

    @property
    def event_generation(self) -> int:
        return self._event_generation

    async def _notify_event(self, sequence: int) -> None:
        async with self._event_condition:
            self._event_generation = max(self._event_generation, sequence)
            self._event_condition.notify_all()

    def events_after(
        self,
        after_sequence: int,
        *,
        job_id: str | None = None,
        limit: int = 1_000,
    ) -> list[RenderEvent]:
        return self.store.events_after(after_sequence, job_id=job_id, limit=limit)

    async def wait_for_events(
        self,
        observed_generation: int,
        timeout_seconds: float = 15.0,
    ) -> None:
        try:
            async with self._event_condition:
                await asyncio.wait_for(
                    self._event_condition.wait_for(
                        lambda: self._event_generation != observed_generation
                    ),
                    timeout=timeout_seconds,
                )
        except TimeoutError:
            return

    def logs(self, job_id: str, *, after_sequence: int, limit: int) -> LogPage:
        self.get_job(job_id)
        items = self.store.logs(job_id, after_sequence=after_sequence, limit=limit)
        return LogPage(
            items=items,
            next_sequence=items[-1].sequence if len(items) == limit else None,
        )

    async def request_stop_after_chunk(self, job_id: str) -> JobRecord:
        job = self.get_job(job_id)
        if job.state not in {JobState.RUNNING, JobState.STOP_REQUESTED}:
            raise MissionControlError(
                409,
                "render_not_running",
                "Render is not running",
                "Stop after current chunk is available only while rendering is active.",
                "Resume the job before requesting a safe stop.",
                job_id=job.id,
            )
        if job.safe_stop_status == SafeStopStatus.REQUESTED:
            return job
        if job.renderer == RendererKind.FAKE:
            self.fake_renderer.request_stop(job)
        else:
            profile_path, _payload, _hash = self.discovery.profile_source(job.identity.profile_id)
            scene_path = Path(self.discovery.get_scene(job.identity.scene_id).path)
            self.production_renderer.request_stop(
                job,
                profile_path=profile_path,
                scene_path=scene_path,
            )
        await self.add_log(job.id, "warning", "Stop after the current chunk was requested.")
        return await self.update_job(
            job.id,
            state=JobState.STOP_REQUESTED,
            safe_stop_status=SafeStopStatus.REQUESTED,
        )

    async def cancel_stop(self, job_id: str, request: CancelStopRequest) -> JobRecord:
        if not request.operator_confirmed:
            raise MissionControlError(
                422,
                "cancel_stop_confirmation_required",
                "Confirmation required",
                "Cancelling a safe stop allows the renderer to continue into another chunk.",
                "Confirm the cancellation before continuing.",
                job_id=job_id,
            )
        job = self.get_job(job_id)
        if job.safe_stop_status != SafeStopStatus.REQUESTED:
            raise MissionControlError(
                409,
                "stop_not_requested",
                "No stop request is pending",
                "This render does not have a pending stop-after-chunk request.",
                "Return to live progress.",
                job_id=job_id,
            )
        if job.renderer == RendererKind.FAKE:
            self.fake_renderer.cancel_stop(job)
        else:
            self.production_renderer.cancel_stop(job)
        await self.add_log(job.id, "info", "Pending safe-stop request was cancelled.")
        return await self.update_job(
            job.id,
            state=JobState.RUNNING,
            safe_stop_status=SafeStopStatus.NONE,
        )

    async def resume(self, job_id: str, request: ResumeRequest) -> JobRecord:
        async with self._job_lock:
            job = self.get_job(job_id)
            if job.state not in {JobState.PAUSED_SAFELY, JobState.RESUMABLE, JobState.FAILED}:
                raise MissionControlError(
                    409,
                    "render_not_resumable",
                    "Render cannot be resumed",
                    "Only safely paused, resumable, or failed exact-identity jobs can resume.",
                    "Return to job history and choose a resumable job.",
                    job_id=job.id,
                )
            if (
                request.scene_sha256 != job.identity.scene_sha256
                or request.profile_sha256 != job.identity.profile_sha256
            ):
                raise MissionControlError(
                    409,
                    "resume_identity_mismatch",
                    "Resume identity does not match",
                    "Resume requires the exact original saved scene and profile hashes.",
                    "Refresh job details and restore the original identity files.",
                    job_id=job.id,
                )
            profile_path, profile_payload, profile_hash = self.discovery.profile_source(
                job.identity.profile_id
            )
            scene_path = Path(self.discovery.get_scene(job.identity.scene_id).path)
            if profile_hash != job.identity.profile_sha256:
                raise MissionControlError(
                    409,
                    "resume_profile_changed",
                    "Saved profile changed",
                    "The profile file bytes no longer match this render job.",
                    "Restore the exact authorized saved profile before resuming.",
                    job_id=job.id,
                )
            if self.discovery.get_scene(job.identity.scene_id).sha256 != job.identity.scene_sha256:
                raise MissionControlError(
                    409,
                    "resume_scene_changed",
                    "Approved scene changed",
                    "The scene file bytes no longer match this render job.",
                    "Restore the exact approved scene before resuming.",
                    job_id=job.id,
                )
            authorized, issues, token = validate_authorization_record(
                profile_path,
                scene_path,
                profile_payload,
            )
            if not authorized:
                raise MissionControlError(
                    409,
                    "resume_authorization_invalid",
                    "Authorization is invalid",
                    "The exact authorization record no longer validates.",
                    "Authorize the exact scene and profile again.",
                    context={"issues": issues},
                    job_id=job.id,
                )
            if job.renderer == RendererKind.PRODUCTION:
                output = self.outputs.inspect(
                    job.identity.output_directory,
                    profile_id=job.identity.profile_id,
                    scene_id=job.identity.scene_id,
                )
                if not output.usable:
                    raise MissionControlError(
                        409,
                        "resume_output_incompatible",
                        "Output cannot be resumed",
                        "The existing output manifest no longer matches this exact render identity.",
                        "Inspect the conflicting output details and choose the matching output.",
                        context={"issues": output.issues},
                        job_id=job.id,
                    )
                self.production_renderer.cancel_stop(job)
            resumed = await self.update_job(
                job.id,
                state=JobState.STARTING,
                safe_stop_status=SafeStopStatus.NONE,
                error=None,
                warning=None,
                completed_at=None,
            )
            if job.renderer == RendererKind.FAKE:
                self.fake_renderer.start(
                    self,
                    resumed,
                    FakeRenderOptions(total_frames=job.total_frame_count),
                )
            else:
                self.production_renderer.start(
                    self,
                    resumed,
                    scene_path=scene_path,
                    profile_path=profile_path,
                    authorization_token=token,
                )
            return resumed

    def preview_path(self, job_id: str, *, frame: int | None = None) -> Path | None:
        job = self.get_job(job_id)
        cache_key = f"{job_id}:{frame if frame is not None else 'latest'}"
        if frame is not None:
            if frame < job.frame_start or frame > job.frame_end:
                return None
            requested = (
                Path(job.identity.output_directory)
                / "frames"
                / f"frame_{frame:06d}.png"
            )
            return self._preview_thumbnail(job, requested) if self._valid_png(requested) else None
        if job.latest_preview_frame is not None:
            latest_published = (
                Path(job.identity.output_directory)
                / "frames"
                / f"frame_{job.latest_preview_frame:06d}.png"
            )
            if self._valid_png(latest_published):
                preview = self._preview_thumbnail(job, latest_published)
                self._preview_cache[cache_key] = (time.monotonic(), preview)
                return preview
        cached = self._preview_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < 2.0:
            return cached[1]
        if job.renderer == RendererKind.PRODUCTION:
            snapshot = inspect_render_artifacts(job)
            production_result = (
                self._preview_thumbnail(job, snapshot.latest_preview_path)
                if snapshot.latest_preview_path is not None
                else None
            )
            self._preview_cache[cache_key] = (now, production_result)
            return production_result
        frames_root = Path(job.identity.output_directory) / "frames"
        upper = (
            min(job.frame_end, job.frame_start + job.published_frame_count - 1)
            if job.published_frame_count > 0
            else job.current_frame
        )
        result: Path | None = None
        if upper is not None and frames_root.is_dir():
            lower = max(job.frame_start, upper - _PREVIEW_FALLBACK_WINDOW + 1)
            for candidate_frame in range(upper, lower - 1, -1):
                candidate = frames_root / f"frame_{candidate_frame:06d}.png"
                if self._valid_png(candidate):
                    result = candidate
                    break
        self._preview_cache[cache_key] = (now, result)
        return result

    def _preview_thumbnail(self, job: JobRecord, source: Path) -> Path:
        """Publish a bounded preview outside Blender's render callbacks.

        The source has already passed structural validation and, for production
        jobs, manifest hash validation. A failed or unavailable thumbnailer never
        replaces the last complete source preview.
        """

        if job.renderer != RendererKind.PRODUCTION:
            return source
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return source
        target_root = self.config.state_root / "preview-thumbnails" / job.id
        target = target_root / source.name
        temporary: Path | None = None
        try:
            source_before = source.stat()
            if (
                self._valid_png(target)
                and target.stat().st_mtime_ns >= source_before.st_mtime_ns
                and max(self._png_dimensions(target)) <= 640
            ):
                return target
            target_root.mkdir(parents=True, exist_ok=True)
            temporary = target_root / f".{source.stem}.{uuid4().hex}.png"
            result = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source),
                    "-vf",
                    "scale=640:640:force_original_aspect_ratio=decrease",
                    "-frames:v",
                    "1",
                    str(temporary),
                ],
                shell=False,
                check=False,
                capture_output=True,
                timeout=15,
            )
            source_after = source.stat()
            if (
                result.returncode != 0
                or len(result.stdout) > 65_536
                or len(result.stderr) > 65_536
                or source_before.st_size != source_after.st_size
                or source_before.st_mtime_ns != source_after.st_mtime_ns
                or not self._valid_png(temporary)
                or temporary.stat().st_size > 2_000_000
                or max(self._png_dimensions(temporary)) > 640
            ):
                temporary.unlink(missing_ok=True)
                return source
            os.replace(temporary, target)
            return target
        except (OSError, subprocess.SubprocessError, ValueError):
            return source
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _png_dimensions(self, path: Path) -> tuple[int, int]:
        with path.open("rb") as stream:
            header = stream.read(24)
        if (
            len(header) != 24
            or header[:8] != b"\x89PNG\r\n\x1a\n"
            or header[12:16] != b"IHDR"
        ):
            raise ValueError("Invalid PNG header")
        width, height = struct.unpack(">II", header[16:24])
        if width < 1 or height < 1 or width > 32_768 or height > 32_768:
            raise ValueError("PNG dimensions are outside the preview contract")
        return width, height

    def _valid_png(self, path: Path) -> bool:
        try:
            if path.stat().st_size < 20:
                return False
            with path.open("rb") as stream:
                if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                    return False
                stream.seek(-12, os.SEEK_END)
                if stream.read(12)[4:8] != b"IEND":
                    return False
            self._png_dimensions(path)
            return True
        except (OSError, ValueError):
            return False

    def calibrations(self) -> list[CalibrationSummary]:
        return self.discovery.list_calibrations()

    def calibration(self, calibration_id: str) -> CalibrationSummary:
        return self.discovery.get_calibration(calibration_id)

    def plan_calibration(self, request: CalibrationPlanRequest) -> CalibrationPlanResult:
        scene = self.discovery.get_scene(request.scene_id)
        plan_id = f"plan-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        root = self.config.calibration_plan_root / plan_id
        root.mkdir(parents=True, exist_ok=False)
        plan_path = root / "plan.json"
        from .discovery import atomic_write_json

        atomic_write_json(
            plan_path,
            {
                "schemaVersion": "1.0.0",
                "kind": "trackprompt-mission-control-calibration-plan",
                "id": plan_id,
                "createdAt": datetime.now(UTC).isoformat(),
                "sceneId": scene.id,
                "sceneSha256": scene.sha256,
                "goal": request.goal,
                "executionAvailable": False,
                "detail": "Use the bounded calibration adapter to materialize the existing validated 73-candidate plan.",
            },
        )
        return CalibrationPlanResult(
            id=plan_id,
            path=str(root),
            execution_available=False,
            detail="An offline Mission Control plan was saved. Candidate execution is not started automatically.",
        )

    def cloud_readiness(self) -> CloudReadiness:
        package_ready = (self.config.repository_root / "cloud_render" / "__init__.py").is_file()
        brev_installed = bool(shutil.which("brev.exe") or shutil.which("brev"))
        return CloudReadiness(
            status="offline_ready" if package_ready else "setup_required",
            offline_preparation_available=package_ready,
            package_validation_available=(
                self.config.repository_root / "tools" / "remote_render_tooling.py"
            ).is_file(),
            brev_cli_installed=brev_installed,
            detail=(
                "Offline package preparation is available. Live provisioning remains disabled and unverified."
                if package_ready
                else "Cloud preparation tooling is incomplete. No provider command was run."
            ),
            setup_checklist=[
                "Install and authenticate the current official NVIDIA Brev CLI outside Mission Control.",
                "Inspect the installed CLI locally before provider discovery.",
                "Run one bounded benchmark worker before approving any fleet.",
                "Keep source audio local and mux it only after verified video-only output returns.",
            ],
        )

    def encode_readiness(self, job_id: str) -> EncodeReadiness:
        job = self.get_job(job_id)
        if job.renderer == RendererKind.PRODUCTION:
            artifact_state = inspect_render_artifacts(job)
            complete = (
                job.state == JobState.COMPLETE
                and artifact_state.disposition == "complete"
                and artifact_state.valid_frame_count == job.total_frame_count
            )
            published_frames = artifact_state.valid_frame_count
        else:
            complete = (
                job.state == JobState.COMPLETE
                and job.published_frame_count == job.total_frame_count
            )
            published_frames = job.published_frame_count
        ffmpeg = self._ffmpeg_path() is not None
        return EncodeReadiness(
            job_id=job.id,
            ready=complete and ffmpeg,
            frame_sequence_complete=complete,
            published_frames=published_frames,
            total_frames=job.total_frame_count,
            ffmpeg_available=ffmpeg,
            detail=(
                "The verified frame sequence is ready for local encode."
                if complete and ffmpeg
                else "Encoding requires a complete published frame sequence and FFmpeg."
            ),
        )

    def encode_status(self, job_id: str) -> EncodeJobStatus:
        job = self.get_job(job_id)
        stored = self.store.get_setting(f"{_ENCODE_SETTING_PREFIX}{job_id}")
        if isinstance(stored, dict):
            try:
                return EncodeJobStatus.model_validate(stored)
            except ValueError:
                pass
        return EncodeJobStatus(
            id=f"encode-{job_id}",
            render_job_id=job_id,
            status="idle",
            total_frames=job.total_frame_count,
            updated_at=datetime.now(UTC),
            detail="No local encode has been started for this render.",
        )

    def _put_encode_status(self, status: EncodeJobStatus) -> EncodeJobStatus:
        self.store.put_setting(
            f"{_ENCODE_SETTING_PREFIX}{status.render_job_id}",
            status.model_dump(mode="json", by_alias=False),
        )
        return status

    def _encode_output_paths(
        self,
        job: JobRecord,
        profile_payload: dict[str, object],
        kinds: list[Literal["delivery", "master"]],
    ) -> dict[str, str]:
        resolution = profile_payload.get("resolution")
        height = resolution.get("height") if isinstance(resolution, dict) else None
        if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
            raise MissionControlError(
                409,
                "encode_profile_resolution_missing",
                "Encode profile is incomplete",
                "The saved profile does not provide a valid output height.",
                "Restore the exact authorized saved profile.",
                job_id=job.id,
            )
        encoding = profile_payload.get("encoding")
        if not isinstance(encoding, dict):
            raise MissionControlError(
                409,
                "encode_profile_settings_missing",
                "Encode settings are unavailable",
                "The saved profile does not contain reviewed encoding settings.",
                "Use the exact authorized saved profile.",
                job_id=job.id,
            )
        output_root = Path(job.identity.output_directory).resolve(strict=True)
        base_name = f"{job.identity.project_id}-{height}p"
        output_paths: dict[str, str] = {}
        for kind in kinds:
            settings = encoding.get(kind)
            if not isinstance(settings, dict) or settings.get("enabled") is not True:
                raise MissionControlError(
                    409,
                    "encode_kind_disabled",
                    "Requested encode is disabled",
                    f"The authorized profile does not enable the {kind} output.",
                    "Choose an enabled reviewed output kind.",
                    job_id=job.id,
                )
            extension = settings.get("fileExtension")
            if not isinstance(extension, str) or not extension.startswith("."):
                raise MissionControlError(
                    409,
                    "encode_extension_missing",
                    "Encode destination is invalid",
                    f"The authorized {kind} settings do not provide a safe file extension.",
                    "Restore the exact authorized saved profile.",
                    job_id=job.id,
                )
            directory = (output_root / kind).resolve()
            destination = (directory / f"{base_name}-{kind}{extension}").resolve()
            try:
                destination.relative_to(directory)
            except ValueError as exc:
                raise MissionControlError(
                    409,
                    "encode_destination_escaped",
                    "Encode destination is unsafe",
                    "The generated destination escaped the managed output directory.",
                    "Inspect the render job output identity.",
                    job_id=job.id,
                ) from exc
            output_paths[kind] = str(destination)
        return output_paths

    async def start_encode(
        self,
        job_id: str,
        request: EncodeStartRequest,
    ) -> EncodeJobStatus:
        if not request.operator_confirmed:
            raise MissionControlError(
                409,
                "encode_confirmation_required",
                "Encode confirmation required",
                "Local encoding and private-audio mux require explicit confirmation.",
                "Review the exact outputs and confirm the local encode.",
                job_id=job_id,
            )
        if not request.include_audio:
            raise MissionControlError(
                409,
                "encode_audio_required",
                "Approved audio is required",
                "The reviewed master and delivery contracts both include the exact approved audio.",
                "Keep local audio enabled for this authorized profile.",
                job_id=job_id,
            )
        readiness = self.encode_readiness(job_id)
        if not readiness.ready:
            raise MissionControlError(
                409,
                "encode_not_ready",
                "Frame sequence is not ready",
                readiness.detail,
                "Verify the complete frame sequence and local FFmpeg installation.",
                job_id=job_id,
            )
        existing = self.encode_status(job_id)
        if existing.status in _ENCODE_ACTIVE_STATUSES:
            return existing
        kinds = list(dict.fromkeys(request.output_kinds))
        job = self.get_job(job_id)
        profile_path, profile_payload, profile_hash = self.discovery.profile_source(job.identity.profile_id)
        if profile_hash != job.identity.profile_sha256:
            raise MissionControlError(
                409,
                "encode_profile_changed",
                "Saved profile changed",
                "The saved profile no longer matches the completed render.",
                "Restore the exact authorized profile before encoding.",
                job_id=job_id,
            )
        _ = profile_path
        output_paths = self._encode_output_paths(job, profile_payload, kinds)
        for destination in output_paths.values():
            if Path(destination).exists():
                raise MissionControlError(
                    409,
                    "encode_destination_exists",
                    "Encode destination already exists",
                    "The reviewed encoder never overwrites an existing media file.",
                    "Open the existing output or choose a new managed name.",
                    related_path=destination,
                    job_id=job_id,
                )
        now = datetime.now(UTC)
        status = self._put_encode_status(
            EncodeJobStatus(
                id=f"encode-{uuid4()}",
                render_job_id=job_id,
                status="queued",
                output_kinds=kinds,
                total_frames=job.total_frame_count,
                output_paths=output_paths,
                started_at=now,
                updated_at=now,
                detail="The verified local encode is queued.",
            )
        )
        task = asyncio.create_task(
            self._run_encode_sequence(status),
            name=f"mission-control-encode-{job_id}",
        )
        self._encode_tasks[job_id] = task
        task.add_done_callback(lambda _task: self._encode_tasks.pop(job_id, None))
        return status

    def _read_encode_progress(self, path: Path) -> dict[str, str]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {}
        values: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if separator and key:
                values[key.strip()] = value.strip()
        return values

    async def _run_encode_sequence(self, initial: EncodeJobStatus) -> None:
        status = initial
        try:
            async with self.gpu_operation_lock:
                job = self.get_job(status.render_job_id)
                profile_path, profile_payload, profile_hash = self.discovery.profile_source(job.identity.profile_id)
                if profile_hash != job.identity.profile_sha256:
                    raise RuntimeError("The exact saved profile changed before encoding started.")
                scene = self.discovery.get_scene(job.identity.scene_id)
                audio = profile_payload.get("audio")
                audio_path = audio.get("path") if isinstance(audio, dict) else None
                if not isinstance(audio_path, str) or not audio_path.strip():
                    raise RuntimeError("The exact approved local audio path is unavailable.")
                ffmpeg = self._ffmpeg_path()
                ffprobe = self._ffprobe_path(ffmpeg) if ffmpeg is not None else None
                powershell = self.production_renderer.powershell_path()
                python = self.config.repository_root / "backend" / ".venv" / "Scripts" / "python.exe"
                script = self.config.repository_root / "encode-trackprompt-final.ps1"
                if ffmpeg is None or ffprobe is None or powershell is None or not python.is_file() or not script.is_file():
                    raise RuntimeError("The reviewed local encode runtime is incomplete.")
                progress_root = self.config.state_root / "encode-progress"
                progress_root.mkdir(parents=True, exist_ok=True)
                completed = list(status.completed_kinds)
                kind_count = len(status.output_kinds)
                for index, kind in enumerate(status.output_kinds):
                    if kind in completed:
                        continue
                    progress_path = progress_root / f"{status.id}-{kind}.txt"
                    progress_path.unlink(missing_ok=True)
                    destination = status.output_paths[kind]
                    status = self._put_encode_status(
                        status.model_copy(
                            update={
                                "status": "encoding",
                                "current_kind": kind,
                                "progress": (index / kind_count) * 100.0,
                                "current_frame": 0,
                                "fps": None,
                                "speed": None,
                                "eta_seconds": None,
                                "process_id": None,
                                "updated_at": datetime.now(UTC),
                                "detail": f"Encoding the verified {kind} output with approved local audio.",
                                "error": None,
                            }
                        )
                    )
                    arguments = [
                        powershell,
                        "-NoLogo",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                        "-ApprovedScenePath",
                        scene.path,
                        "-RenderProfilePath",
                        str(profile_path),
                        "-ProductionDirectory",
                        job.identity.output_directory,
                        "-AudioPath",
                        audio_path,
                        "-OutputPath",
                        destination,
                        "-OutputKind",
                        kind.capitalize(),
                        "-FfmpegExecutable",
                        str(ffmpeg),
                        "-FfprobeExecutable",
                        str(ffprobe),
                        "-PythonExecutable",
                        str(python),
                        "-ProgressPath",
                        str(progress_path),
                    ]
                    process = await asyncio.create_subprocess_exec(
                        *arguments,
                        cwd=str(self.config.repository_root),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                        | int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                    )
                    status = self._put_encode_status(
                        status.model_copy(
                            update={"process_id": process.pid, "updated_at": datetime.now(UTC)}
                        )
                    )
                    while process.returncode is None:
                        await asyncio.sleep(1.0)
                        progress = await asyncio.to_thread(self._read_encode_progress, progress_path)
                        try:
                            frame = max(0, min(job.total_frame_count, int(progress.get("frame", "0"))))
                        except ValueError:
                            frame = status.current_frame or 0
                        try:
                            current_fps = float(progress.get("fps", "0")) or None
                        except ValueError:
                            current_fps = None
                        fraction = frame / job.total_frame_count if job.total_frame_count else 0.0
                        verifying = progress.get("progress") == "end"
                        overall = ((index + min(fraction, 0.995)) / kind_count) * 100.0
                        eta = (
                            (job.total_frame_count - frame) / current_fps
                            if current_fps is not None and current_fps > 0 and not verifying
                            else None
                        )
                        status = self._put_encode_status(
                            status.model_copy(
                                update={
                                    "status": "verifying" if verifying else "encoding",
                                    "progress": overall,
                                    "current_frame": frame,
                                    "fps": current_fps,
                                    "speed": progress.get("speed") or None,
                                    "eta_seconds": eta,
                                    "updated_at": datetime.now(UTC),
                                    "detail": (
                                        f"Verifying and finalizing the {kind} output."
                                        if verifying
                                        else f"Encoding {kind}: frame {frame:,} of {job.total_frame_count:,}."
                                    ),
                                }
                            )
                        )
                    stdout = b""
                    if process.stdout is not None:
                        stdout = await process.stdout.read()
                    exit_code = await process.wait()
                    if exit_code != 0:
                        details = stdout.decode("utf-8", errors="replace")[-4_000:]
                        raise RuntimeError(f"{kind.capitalize()} encode failed with exit code {exit_code}. {details}")
                    if not Path(destination).is_file():
                        raise RuntimeError(f"The verified {kind} output was not published.")
                    completed.append(kind)
                    status = self._put_encode_status(
                        status.model_copy(
                            update={
                                "completed_kinds": completed,
                                "progress": ((index + 1) / kind_count) * 100.0,
                                "current_frame": job.total_frame_count,
                                "process_id": None,
                                "updated_at": datetime.now(UTC),
                                "detail": f"The verified {kind} output is complete.",
                            }
                        )
                    )
                now = datetime.now(UTC)
                self._put_encode_status(
                    status.model_copy(
                        update={
                            "status": "complete",
                            "current_kind": None,
                            "progress": 100.0,
                            "process_id": None,
                            "eta_seconds": 0.0,
                            "updated_at": now,
                            "completed_at": now,
                            "detail": "Both verified local outputs are complete.",
                        }
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            now = datetime.now(UTC)
            self._put_encode_status(
                status.model_copy(
                    update={
                        "status": "failed",
                        "process_id": None,
                        "updated_at": now,
                        "completed_at": now,
                        "detail": "The local encode stopped before both outputs completed.",
                        "error": StructuredError(
                            code="encode_failed",
                            title="Local encode failed",
                            summary=str(exc)[:1_000],
                            recommended_action="Inspect the encode logs and retry only the missing output.",
                            retryable=True,
                            timestamp=now,
                            job_id=status.render_job_id,
                        ),
                    }
                )
            )

    async def performance_status(self) -> PerformanceStatus:
        return _with_unbound_performance_detail(await self.performance.status())

    async def performance_enable(self, request: PerformanceEnableRequest) -> PerformanceStatus:
        blender_pid = 0
        if request.job_id is not None:
            job = self.get_job(request.job_id)
            if job.renderer != RendererKind.PRODUCTION or job.state not in _ACTIVE_STATES or job.process_id is None:
                raise MissionControlError(
                    409,
                    "performance_job_not_active",
                    "Active Blender job required",
                    "The selected job does not have an active production renderer process.",
                    "Choose the current production render or enable before Blender priority adjustment.",
                )
            for _attempt in range(20):
                resolved = find_descendant_process_id(
                    job.process_id,
                    ("blender.exe", "blender"),
                )
                if resolved is not None:
                    blender_pid = resolved
                    break
                await asyncio.sleep(0.25)
            if blender_pid == 0:
                raise MissionControlError(
                    409,
                    "performance_blender_not_found",
                    "Blender process is not ready",
                    "The active render supervisor has not started its Blender child yet.",
                    "Wait for the render phase to begin, then retry performance mode.",
                    retryable=True,
                    job_id=job.id,
                )
        status = await self.performance.enable(
            operator_confirmed=request.operator_confirmed,
            use_high_performance_power_plan=request.use_high_performance_power_plan,
            blender_process_id=blender_pid,
        )
        return _with_unbound_performance_detail(status)

    async def performance_restore(self, request: PerformanceRestoreRequest) -> PerformanceStatus:
        return await self.performance.restore(operator_confirmed=request.operator_confirmed)

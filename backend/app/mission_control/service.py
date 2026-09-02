from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from ..cinematic.schemas import (
    ArtDirectionReview,
    ArtDirectionReviewCollection,
    ShotPlan,
    StoryPlan,
)
from ..cinematic.validation import validate_cinematic_privacy, validate_plan_pair
from ..local_video import LocalVideoController
from ..video_generation.mission_controller import VideoGenerationController
from ..video_generation.mission_models import (
    VideoAudioBrowseRequest,
    VideoAudioSelection,
    VideoGenerationEvent,
)
from .config import MissionControlConfig
from .discovery import (
    MissionDiscovery,
    atomic_write_json,
    load_json_object,
    validate_authorization_record,
)
from .errors import MissionControlError
from .eta import EtaSample, EtaService, StageWorkload
from .local_worker import (
    LocalSubprocessRenderWorker,
    LocalTaskCommandBuilder,
    LocalWorkerRunResult,
)
from .models import (
    AuthorizationResult,
    CalibrationPlanRequest,
    CalibrationPlanResult,
    CalibrationSummary,
    CancelRenderRequest,
    CancelStopRequest,
    CheckStatus,
    CloudReadiness,
    ComponentStatus,
    DirectorWorkspace,
    DryRunResult,
    EncodeJobStatus,
    EncodeReadiness,
    EncodeStartRequest,
    EtaConfidence,
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
    RetryCurrentChunkRequest,
    RetryFailedRenderRequest,
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
from .render_contracts import (
    CompositionProfile,
    MediaRenderJob,
    OutputVariant,
    OutputVariantMatrixIdentity,
    OutputVariantProgress,
    PackageIdentity,
    ProgressState,
    ProjectRef,
    RenderStage,
    ShotRenderTask,
    StageProgress,
    TaskState,
    TenantRef,
    WorkerCapabilities,
    WorkerKind,
    WorkerLease,
)
from .renderers import (
    FakeRenderer,
    ProductionRenderer,
    RendererTelemetryEvent,
    artifact_progress_changes,
    inspect_render_artifacts,
)
from .scheduler import (
    LeaseGrant,
    PersistentRenderScheduler,
    ScheduledRenderTask,
    SchedulerReapResult,
    SchedulerWorker,
    TaskResourceRequirements,
    seal_task_identity,
)
from .store import MissionControlStore
from .system_adapters import NativePicker, PerformanceAdapter

_ACTIVE_STATES = {
    JobState.STARTING,
    JobState.RUNNING,
    JobState.STOP_REQUESTED,
    JobState.RETRY_REQUESTED,
    JobState.CANCEL_REQUESTED,
    JobState.FINISHING_CURRENT_CHUNK,
    JobState.ENCODING,
    JobState.VERIFYING,
}
_TERMINAL_STATES = {JobState.COMPLETE, JobState.FAILED, JobState.CANCELLED}
_PREVIEW_FALLBACK_WINDOW = 256
_ENCODE_ACTIVE_STATUSES = {"queued", "encoding", "verifying"}
_ENCODE_SETTING_PREFIX = "encode_job:"
_DIRECTOR_FILE_LIMIT = 2_000_000
_SHA256_LOWER_RE = re.compile(r"^[a-f0-9]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


@dataclass(frozen=True, slots=True)
class _OutputVariantDefinition:
    id: str
    enabled: bool
    required: bool
    width: int
    height: int
    fps: float
    deliverable_role: str
    render_profile_id: str
    render_profile_sha256: str
    composition_profile: CompositionProfile
    output_variant_sha256: str
    frames_root: str
    preview_root: str
    encode_root: str
    qa_root: str


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_sha256(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if _SHA256_LOWER_RE.fullmatch(normalized) else None


def _physical_memory_bytes() -> int | None:
    try:
        if os.name == "nt":
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            kernel32 = getattr(getattr(ctypes, "windll", None), "kernel32", None)
            query = getattr(kernel32, "GlobalMemoryStatusEx", None)
            if callable(query) and query(ctypes.byref(status)):
                return int(status.ullTotalPhys)
            return None
        sysconf = getattr(os, "sysconf", None)
        if not callable(sysconf):
            return None
        page_size = sysconf("SC_PAGE_SIZE")
        page_count = sysconf("SC_PHYS_PAGES")
        if isinstance(page_size, int) and isinstance(page_count, int):
            return page_size * page_count
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return None


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
        self.scheduler = PersistentRenderScheduler(config.database_path)
        self.scheduler.reap_expired()
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
        self.video_generation = VideoGenerationController(
            repository_root=config.repository_root,
            state_root=config.state_root,
            store=self.store,
            notify_event=self._notify_event,
            ffmpeg_path=self._ffmpeg_path,
            ffprobe_path=self._video_ffprobe_path,
        )
        self.local_video = LocalVideoController(
            repository_root=config.repository_root,
            state_root=config.state_root,
            analysis_data_root=Path(
                os.getenv("TRACKPROMPT_DATA_DIR", str(config.state_root.parent))
            ),
            ffmpeg_path=self._ffmpeg_path,
            ffprobe_path=self._video_ffprobe_path,
        )
        self._recover_jobs()
        self._restore_scheduler_projections()
        self._event_generation = self.store.latest_event_sequence()
        self.start_background_tasks()

    def close(self) -> None:
        self._closed = True
        if self._orphan_monitor_task is not None:
            self._orphan_monitor_task.cancel()
            self._orphan_monitor_task = None
        self.video_generation.close()
        self.scheduler.close()
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
        self.video_generation.start_background_tasks()

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

    def _restore_scheduler_projections(self) -> None:
        now = datetime.now(UTC)
        for job in self.store.list_jobs(limit=1_000):
            tasks = self.scheduler.list_tasks(job_id=job.id)
            if not tasks or all(
                task.state is TaskState.PENDING and task.attempt == 1
                for task in tasks
            ):
                # A pristine queue may coexist with the legacy local renderer
                # until that adapter adopts leases. Never regress renderer-owned
                # frame progress merely because unclaimed tasks were persisted.
                continue
            changes = self._scheduler_projection_changes(
                job,
                event_type="scheduler_restored",
                now=now,
            )
            if not changes:
                continue
            changes["updated_at"] = now
            restored = job.model_copy(update=changes)
            self.store.put_job(restored)
            canonical = self.store.get_media_render_job(job.id)
            if canonical is not None:
                self.store.put_media_render_job(
                    canonical.model_copy(
                        update={
                            "output_variants": restored.output_variants,
                            "updated_at": now,
                        }
                    )
                )
            self.store.append_event(self._event_from_job(restored))

    def _process_alive(self, process_id: int | None) -> bool:
        return process_is_alive(process_id)

    async def _monitor_orphaned_jobs(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(1.0)
                async with self._job_lock:
                    await self._reap_render_scheduler_locked(datetime.now(UTC))
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
            if job.state == JobState.CANCEL_REQUESTED:
                base.update(
                    state=JobState.CANCELLED,
                    completed_at=now,
                    safe_stop_status=SafeStopStatus.NONE,
                    warning=(
                        "The fake renderer stopped during the confirmed cancellation; "
                        "only previously published frames remain safe."
                    ),
                    error=None,
                )
                return job.model_copy(update=base)
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
        elif (
            job.state == JobState.CANCEL_REQUESTED
            and snapshot.disposition in {"paused", "resumable"}
        ):
            base.update(
                state=JobState.CANCELLED,
                phase=JobPhase.PUBLISH_CHUNK,
                completed_at=now,
                safe_stop_status=SafeStopStatus.NONE,
                warning=(
                    "The confirmed cancellation completed; validated and published "
                    "frames were preserved."
                ),
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

    def _video_ffprobe_path(self) -> Path | None:
        ffmpeg = self._ffmpeg_path()
        return self._ffprobe_path(ffmpeg) if ffmpeg is not None else None

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

    async def select_video_audio(
        self,
        job_id: str,
        request: VideoAudioBrowseRequest,
    ) -> VideoAudioSelection:
        selection = await self.native_picker.choose(
            "file",
            initial_directory=request.initial_directory,
            title="Select the original local audio master",
        )
        if not selection.selected:
            return VideoAudioSelection(selected=False, verified=False)
        assert selection.path is not None
        return await self.video_generation.bind_audio(
            job_id,
            Path(selection.path),
            source="local-selection",
            accept_local_delivery_revision=request.accept_local_delivery_revision,
            confirmation=request.confirmation,
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
        enabled_output_variant_ids: list[str] | None = None,
        settings_and_hashes_reviewed: bool,
        production_render_authorized: bool,
    ) -> AuthorizationResult:
        return self.discovery.authorize_profile(
            profile_id,
            scene_id,
            enabled_output_variant_ids=enabled_output_variant_ids,
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

    @staticmethod
    def _output_variant_contract_error(detail: str) -> MissionControlError:
        return MissionControlError(
            422,
            "invalid_output_variant_contract",
            "Output-variant contract is invalid",
            detail,
            "Repair the saved render profile and create a new authorization.",
        )

    def _output_variant_definitions(
        self,
        profile: ProfileSummary,
        payload: Mapping[str, Any],
        *,
        enabled_output_variant_ids: list[str] | None = None,
    ) -> tuple[_OutputVariantDefinition, ...]:
        raw_variants = payload.get("outputVariants")
        if raw_variants is None:
            singular_variant = payload.get("outputVariant")
            if not isinstance(singular_variant, dict):
                return ()
            compatible_variant = dict(singular_variant)
            compatible_variant.setdefault("enabled", True)
            compatible_variant.setdefault("required", True)
            compatible_variant.setdefault("width", profile.resolution.width)
            compatible_variant.setdefault("height", profile.resolution.height)
            compatible_variant.setdefault("fps", profile.fps)
            compatible_variant.setdefault("deliverableRole", "primary-master")
            compatible_variant.setdefault("renderProfileId", profile.id)
            if profile.composition_profile_sha256 is not None:
                compatible_variant.setdefault(
                    "compositionProfileSha256",
                    profile.composition_profile_sha256,
                )
            raw_variants = [compatible_variant]
        if not isinstance(raw_variants, list) or not raw_variants:
            raise self._output_variant_contract_error(
                "outputVariants must contain at least one variant declaration."
            )

        output_matrix = payload.get("outputMatrix")
        matrix = output_matrix if isinstance(output_matrix, dict) else {}
        selected_value: object = (
            enabled_output_variant_ids
            if enabled_output_variant_ids is not None
            else payload.get(
                "enabledOutputVariantIds",
                matrix.get("enabledVariantIds"),
            )
        )
        selected_ids: set[str] | None = None
        if selected_value is not None:
            if not isinstance(selected_value, list) or not all(
                isinstance(item, str) and _IDENTIFIER_RE.fullmatch(item.strip())
                for item in selected_value
            ):
                raise self._output_variant_contract_error(
                    "enabledOutputVariantIds must contain only portable variant IDs."
                )
            selected_ids = {item.strip() for item in selected_value}
            if len(selected_ids) != len(selected_value):
                raise self._output_variant_contract_error(
                    "enabledOutputVariantIds cannot contain duplicate IDs."
                )

        declarations: list[tuple[dict[str, Any], str, bool, bool]] = []
        declared_ids: set[str] = set()
        for raw_value in raw_variants:
            if not isinstance(raw_value, dict):
                raise self._output_variant_contract_error(
                    "Every output variant must be a JSON object."
                )
            raw = dict(raw_value)
            variant_id_value = raw.get("id")
            if (
                not isinstance(variant_id_value, str)
                or _IDENTIFIER_RE.fullmatch(variant_id_value.strip()) is None
            ):
                raise self._output_variant_contract_error(
                    "Every output variant requires a portable id."
                )
            variant_id = variant_id_value.strip()
            if variant_id in declared_ids:
                raise self._output_variant_contract_error(
                    f"Output variant {variant_id!r} is declared more than once."
                )
            declared_ids.add(variant_id)
            required = raw.get("required", False)
            if not isinstance(required, bool):
                raise self._output_variant_contract_error(
                    f"Output variant {variant_id!r} has a non-boolean required flag."
                )
            if selected_ids is None:
                enabled_value = raw.get(
                    "enabled",
                    raw.get("enabledByDefault", required),
                )
                if not isinstance(enabled_value, bool):
                    raise self._output_variant_contract_error(
                        f"Output variant {variant_id!r} has a non-boolean enabled flag."
                    )
                enabled = enabled_value
            else:
                enabled = variant_id in selected_ids
            if required and not enabled:
                raise self._output_variant_contract_error(
                    f"Required output variant {variant_id!r} cannot be disabled."
                )
            declarations.append((raw, variant_id, enabled, required))

        if selected_ids is not None and (unknown := selected_ids - declared_ids):
            raise self._output_variant_contract_error(
                f"Enabled output variants are not declared: {', '.join(sorted(unknown))}."
            )
        enabled_ids = [variant_id for _raw, variant_id, enabled, _required in declarations if enabled]
        if not enabled_ids:
            raise self._output_variant_contract_error(
                "At least one declared output variant must be enabled."
            )
        primary_variant_id = enabled_ids[0]

        definitions: list[_OutputVariantDefinition] = []
        for raw, variant_id, enabled, required in declarations:
            width = raw.get("width")
            height = raw.get("height")
            fps = raw.get("fps", profile.fps)
            if (
                isinstance(width, bool)
                or not isinstance(width, int)
                or width <= 0
                or isinstance(height, bool)
                or not isinstance(height, int)
                or height <= 0
                or isinstance(fps, bool)
                or not isinstance(fps, (int, float))
                or float(fps) <= 0
                or float(fps) > 240
            ):
                raise self._output_variant_contract_error(
                    f"Output variant {variant_id!r} has invalid width, height, or FPS."
                )
            if raw.get("compositionMode", "authored") != "authored":
                raise self._output_variant_contract_error(
                    f"Output variant {variant_id!r} must use an authored composition."
                )
            deliverable_value = raw.get(
                "deliverableRole",
                "primary-master" if required else "optional-deliverable",
            )
            if (
                not isinstance(deliverable_value, str)
                or _IDENTIFIER_RE.fullmatch(deliverable_value.strip()) is None
            ):
                raise self._output_variant_contract_error(
                    f"Output variant {variant_id!r} has an invalid deliverable role."
                )

            composition_value = raw.get("compositionProfile")
            composition = (
                dict(composition_value)
                if isinstance(composition_value, dict)
                else {}
            )
            composition_id_value = raw.get(
                "compositionProfileId",
                composition.get("id", f"{variant_id}-composition"),
            )
            revision_value = composition.get(
                "revision",
                raw.get("compositionRevision", "1"),
            )
            if (
                not isinstance(composition_id_value, str)
                or _IDENTIFIER_RE.fullmatch(composition_id_value.strip()) is None
                or not isinstance(revision_value, str)
                or not revision_value.strip()
            ):
                raise self._output_variant_contract_error(
                    f"Output variant {variant_id!r} has an invalid composition identity."
                )
            camera_name = raw.get(
                "cameraName",
                composition.get("cameraName", composition_id_value),
            )
            camera_sha256 = _normalized_sha256(
                composition.get("cameraSha256", raw.get("cameraSha256"))
            ) or _canonical_sha256(
                {
                    "cameraName": camera_name,
                    "compositionProfileId": composition_id_value,
                    "outputVariantId": variant_id,
                }
            )
            composition_sha256 = _normalized_sha256(
                composition.get(
                    "compositionSha256",
                    composition.get(
                        "sha256",
                        raw.get("compositionProfileSha256"),
                    ),
                )
            ) or _canonical_sha256(
                {
                    "compositionProfile": composition,
                    "compositionProfileId": composition_id_value,
                    "outputVariantId": variant_id,
                    "width": width,
                    "height": height,
                }
            )
            override_sha256 = _normalized_sha256(
                composition.get("overrideSha256", raw.get("compositionOverrideSha256"))
            )
            render_profile_id_value = raw.get("renderProfileId", profile.id)
            if (
                not isinstance(render_profile_id_value, str)
                or _IDENTIFIER_RE.fullmatch(render_profile_id_value.strip()) is None
            ):
                raise self._output_variant_contract_error(
                    f"Output variant {variant_id!r} has an invalid render profile ID."
                )
            render_profile_sha256 = _normalized_sha256(
                raw.get("renderProfileSha256")
            ) or profile.saved_file_sha256.lower()
            variant_sha256 = _normalized_sha256(
                raw.get("outputVariantSha256")
            ) or _canonical_sha256(
                {
                    "id": variant_id,
                    "required": required,
                    "width": width,
                    "height": height,
                    "fps": float(fps),
                    "compositionMode": "authored",
                    "deliverableRole": deliverable_value.strip(),
                    "renderProfileId": render_profile_id_value.strip(),
                    "renderProfileSha256": render_profile_sha256,
                    "compositionProfileId": composition_id_value.strip(),
                    "compositionSha256": composition_sha256,
                }
            )
            default_root = (
                ""
                if variant_id == primary_variant_id
                else f"variants/{variant_id}/"
            )
            artifact_roots: dict[str, str] = {}
            for field, leaf in (
                ("framesRoot", "frames"),
                ("previewRoot", "previews"),
                ("encodeRoot", "encodes"),
                ("qaRoot", "qa"),
            ):
                value = raw.get(field, f"{default_root}{leaf}")
                if not isinstance(value, str) or not value.strip():
                    raise self._output_variant_contract_error(
                        f"Output variant {variant_id!r} has an invalid {field}."
                    )
                artifact_roots[field] = value.strip()

            definitions.append(
                _OutputVariantDefinition(
                    id=variant_id,
                    enabled=enabled,
                    required=required,
                    width=width,
                    height=height,
                    fps=float(fps),
                    deliverable_role=deliverable_value.strip(),
                    render_profile_id=render_profile_id_value.strip(),
                    render_profile_sha256=render_profile_sha256,
                    composition_profile=CompositionProfile(
                        id=composition_id_value.strip(),
                        revision=revision_value.strip(),
                        scene_sha256=profile.scene_sha256.lower(),
                        camera_sha256=camera_sha256,
                        composition_sha256=composition_sha256,
                        override_sha256=override_sha256,
                    ),
                    output_variant_sha256=variant_sha256,
                    frames_root=artifact_roots["framesRoot"],
                    preview_root=artifact_roots["previewRoot"],
                    encode_root=artifact_roots["encodeRoot"],
                    qa_root=artifact_roots["qaRoot"],
                )
            )
        return tuple(definitions)

    @staticmethod
    def _initial_stage_progress(
        total_frames: int,
        now: datetime,
    ) -> tuple[StageProgress, ...]:
        stages: list[StageProgress] = []
        for stage in RenderStage:
            if stage is RenderStage.INPUT_VERIFICATION:
                stages.append(
                    StageProgress(
                        stage=stage,
                        state=ProgressState.COMPLETE,
                        completed_units=1,
                        total_units=1,
                        unit="checks",
                        updated_at=now,
                    )
                )
            elif stage in {RenderStage.RENDERING, RenderStage.FRAME_VALIDATION}:
                stages.append(
                    StageProgress(
                        stage=stage,
                        state=ProgressState.PENDING,
                        completed_units=0,
                        total_units=total_frames,
                        unit="frames",
                        updated_at=now,
                    )
                )
            else:
                stages.append(
                    StageProgress(
                        stage=stage,
                        state=ProgressState.PENDING,
                        updated_at=now,
                    )
                )
        return tuple(stages)

    @staticmethod
    def _local_worker_capabilities() -> tuple[WorkerCapabilities, ...]:
        memory_bytes = _physical_memory_bytes()
        if memory_bytes is None or memory_bytes <= 0:
            return ()
        return (
            WorkerCapabilities(
                worker_id="local-render-worker",
                kinds=(WorkerKind.LOCAL_CPU,),
                logical_cpu_count=max(1, os.cpu_count() or 1),
                memory_bytes=memory_bytes,
                max_concurrent_tasks=1,
                supported_artifact_formats=("png", "open-exr"),
            ),
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
        _profile_path, profile_payload, _profile_hash = self.discovery.profile_source(
            profile.id
        )
        definitions = self._output_variant_definitions(
            profile,
            profile_payload,
            enabled_output_variant_ids=request.enabled_output_variant_ids,
        )
        active_variant = next(
            (definition for definition in definitions if definition.enabled),
            None,
        )
        return RenderIdentity(
            project_id=request.project_id,
            scene_id=scene.id,
            scene_sha256=scene.sha256,
            profile_id=profile.id,
            profile_sha256=profile.saved_file_sha256,
            output_directory=str(path),
            enabled_output_variant_ids=tuple(
                definition.id for definition in definitions if definition.enabled
            ),
            output_variant_id=(
                active_variant.id
                if active_variant is not None
                else profile.output_variant_id
            ),
            output_width=(
                active_variant.width
                if active_variant is not None
                else profile.resolution.width
            ),
            output_height=(
                active_variant.height
                if active_variant is not None
                else profile.resolution.height
            ),
            composition_profile_id=(
                active_variant.composition_profile.id
                if active_variant is not None
                else profile.composition_profile_id
            ),
            composition_profile_sha256=(
                active_variant.composition_profile.composition_sha256
                if active_variant is not None
                else profile.composition_profile_sha256
            ),
        )

    def _shot_plan_payload(
        self,
        profile_payload: Mapping[str, Any],
        profile_path: Path,
    ) -> Mapping[str, Any] | None:
        inline = profile_payload.get("shotPlan")
        if isinstance(inline, dict) and isinstance(inline.get("shots"), list):
            return inline
        shot_plan_path: object = profile_payload.get("shotPlanPath")
        if shot_plan_path is None and isinstance(inline, dict):
            shot_plan_path = inline.get("path")
        production = profile_payload.get("production")
        if shot_plan_path is None and isinstance(production, dict):
            shot_plan_path = production.get("shotPlanPath")
        if not isinstance(shot_plan_path, str) or not shot_plan_path.strip():
            return None
        raw_path = Path(shot_plan_path.strip())
        candidates = (
            (raw_path,)
            if raw_path.is_absolute()
            else (
                profile_path.parent / raw_path,
                self.config.repository_root / raw_path,
            )
        )
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
                if not resolved.is_file() or resolved.stat().st_size > 8_000_000:
                    continue
                payload = load_json_object(resolved, "Shot plan")
                if isinstance(payload.get("shots"), list):
                    return payload
            except (MissionControlError, OSError):
                continue
        return None

    def _remaining_complexity_units(
        self,
        job: JobRecord,
        profile_payload: Mapping[str, Any],
        profile_path: Path,
        *,
        rendered_frames: int,
        latest_rendered_frame: int | None,
        fallback_complexity: str,
    ) -> tuple[tuple[str, int], ...]:
        next_frame = min(
            job.frame_end + 1,
            job.frame_start + max(0, rendered_frames),
        )
        if latest_rendered_frame is not None:
            next_frame = max(next_frame, latest_rendered_frame + 1)
        if next_frame > job.frame_end:
            return ((fallback_complexity, 0),)
        remaining = job.frame_end - next_frame + 1
        shot_plan = self._shot_plan_payload(profile_payload, profile_path)
        if shot_plan is None:
            return ((fallback_complexity, remaining),)
        raw_shots = shot_plan.get("shots")
        if not isinstance(raw_shots, list):
            return ((fallback_complexity, remaining),)

        spans: list[tuple[int, int, str]] = []
        for raw_value in raw_shots:
            if not isinstance(raw_value, dict):
                return ((fallback_complexity, remaining),)
            frame_start = raw_value.get("frameStart")
            frame_end = raw_value.get("frameEnd")
            complexity = raw_value.get("complexityClass", "default")
            if (
                isinstance(frame_start, bool)
                or not isinstance(frame_start, int)
                or isinstance(frame_end, bool)
                or not isinstance(frame_end, int)
                or frame_end < frame_start
                or not isinstance(complexity, str)
                or _IDENTIFIER_RE.fullmatch(complexity.strip()) is None
            ):
                return ((fallback_complexity, remaining),)
            clipped_start = max(next_frame, job.frame_start, frame_start)
            clipped_end = min(job.frame_end, frame_end)
            if clipped_start <= clipped_end:
                spans.append((clipped_start, clipped_end, complexity.strip()))
        if not spans:
            return ((fallback_complexity, remaining),)

        totals: dict[str, int] = {}
        cursor = next_frame
        for frame_start, frame_end, complexity in sorted(spans):
            if frame_start < cursor:
                return ((fallback_complexity, remaining),)
            if frame_start > cursor:
                totals["default"] = totals.get("default", 0) + frame_start - cursor
            totals[complexity] = totals.get(complexity, 0) + frame_end - frame_start + 1
            cursor = frame_end + 1
        if cursor <= job.frame_end:
            totals["default"] = totals.get("default", 0) + job.frame_end - cursor + 1
        if sum(totals.values()) != remaining:
            return ((fallback_complexity, remaining),)
        return tuple(sorted(totals.items()))

    def _render_workloads(
        self,
        job: JobRecord,
        variants: tuple[OutputVariant, ...],
        profile_payload: Mapping[str, Any],
        profile_path: Path,
        *,
        updated_variant_id: str | None = None,
        rendered_frames: int | None = None,
        latest_rendered_frame: int | None = None,
        fallback_complexity: str,
    ) -> tuple[StageWorkload, ...]:
        workloads: list[StageWorkload] = []
        for variant in variants:
            if not variant.enabled:
                continue
            progress = variant.progress
            variant_rendered = (
                rendered_frames
                if variant.id == updated_variant_id and rendered_frames is not None
                else progress.rendered_frames
            )
            variant_latest = (
                latest_rendered_frame
                if variant.id == updated_variant_id
                else progress.latest_rendered_frame
            )
            distribution = self._remaining_complexity_units(
                job,
                profile_payload,
                profile_path,
                rendered_frames=variant_rendered,
                latest_rendered_frame=variant_latest,
                fallback_complexity=fallback_complexity,
            )
            workloads.extend(
                StageWorkload(
                    output_variant_id=variant.id,
                    stage=RenderStage.RENDERING,
                    complexity_class=complexity,
                    remaining_units=units,
                    parallelizable=True,
                )
                for complexity, units in distribution
            )
        return tuple(workloads)

    def _scheduler_shot_ranges(
        self,
        job: JobRecord,
        profile_payload: Mapping[str, Any],
        profile_path: Path,
    ) -> tuple[tuple[str, str, int, int], ...]:
        fallback = (("timeline", "default", job.frame_start, job.frame_end),)
        shot_plan = self._shot_plan_payload(profile_payload, profile_path)
        if shot_plan is None:
            return fallback
        raw_shots = shot_plan.get("shots")
        if not isinstance(raw_shots, list):
            return fallback
        declared: list[tuple[str, str, int, int]] = []
        for raw in raw_shots:
            if not isinstance(raw, dict):
                return fallback
            shot_id = raw.get("id")
            complexity = raw.get("complexityClass", "default")
            frame_start = raw.get("frameStart")
            frame_end = raw.get("frameEnd")
            if (
                not isinstance(shot_id, str)
                or _IDENTIFIER_RE.fullmatch(shot_id.strip()) is None
                or not isinstance(complexity, str)
                or _IDENTIFIER_RE.fullmatch(complexity.strip()) is None
                or isinstance(frame_start, bool)
                or not isinstance(frame_start, int)
                or isinstance(frame_end, bool)
                or not isinstance(frame_end, int)
                or frame_end < frame_start
            ):
                return fallback
            clipped_start = max(job.frame_start, frame_start)
            clipped_end = min(job.frame_end, frame_end)
            if clipped_start <= clipped_end:
                declared.append(
                    (
                        shot_id.strip(),
                        complexity.strip(),
                        clipped_start,
                        clipped_end,
                    )
                )
        if not declared:
            return fallback
        result: list[tuple[str, str, int, int]] = []
        cursor = job.frame_start
        for shot_id, complexity, frame_start, frame_end in sorted(
            declared,
            key=lambda value: (value[2], value[3], value[0]),
        ):
            if frame_start < cursor:
                return fallback
            if frame_start > cursor:
                result.append(
                    (
                        f"timeline-{cursor:06d}-{frame_start - 1:06d}",
                        "default",
                        cursor,
                        frame_start - 1,
                    )
                )
            result.append((shot_id, complexity, frame_start, frame_end))
            cursor = frame_end + 1
        if cursor <= job.frame_end:
            result.append(
                (
                    f"timeline-{cursor:06d}-{job.frame_end:06d}",
                    "default",
                    cursor,
                    job.frame_end,
                )
            )
        return tuple(result)

    def _scheduler_resource_requirements(
        self,
        profile_payload: Mapping[str, Any],
    ) -> tuple[TaskResourceRequirements, WorkerKind | None]:
        scheduler_value = profile_payload.get("scheduler")
        scheduler_payload = (
            scheduler_value if isinstance(scheduler_value, dict) else {}
        )
        worker_value = scheduler_payload.get(
            "workerRequirements",
            profile_payload.get("workerRequirements"),
        )
        worker = worker_value if isinstance(worker_value, dict) else {}

        memory_value = worker.get("minimumMemoryBytes", 0)
        gpu_memory_value = worker.get("minimumGpuMemoryBytes", 0)
        for label, value in (
            ("minimumMemoryBytes", memory_value),
            ("minimumGpuMemoryBytes", gpu_memory_value),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise self._output_variant_contract_error(
                    f"Scheduler {label} must be a non-negative integer."
                )

        kind_value = worker.get("requiredWorkerKind")
        required_kind: WorkerKind | None = None
        if kind_value is not None:
            try:
                required_kind = WorkerKind(str(kind_value))
            except ValueError as exc:
                raise self._output_variant_contract_error(
                    "Scheduler requiredWorkerKind is not supported."
                ) from exc

        frame_sequence_value = profile_payload.get("frameSequence")
        frame_sequence = (
            frame_sequence_value
            if isinstance(frame_sequence_value, dict)
            else {}
        )
        format_value = frame_sequence.get(
            "format",
            profile_payload.get("imageFormat", "png"),
        )
        normalized_format = str(format_value).strip().lower().replace("_", "-")
        if normalized_format == "openexr":
            normalized_format = "open-exr"
        artifact_format = (
            normalized_format
            if _IDENTIFIER_RE.fullmatch(normalized_format) is not None
            else "png"
        )
        return (
            TaskResourceRequirements(
                memory_bytes=memory_value,
                gpu_memory_bytes=gpu_memory_value,
                required_artifact_format=artifact_format,
            ),
            required_kind,
        )

    def _schedule_media_render_job(
        self,
        job: JobRecord,
        media_job: MediaRenderJob,
        profile_payload: Mapping[str, Any],
        profile_path: Path,
        *,
        chunk_size: int,
    ) -> tuple[ScheduledRenderTask, ...]:
        requirements, required_worker_kind = (
            self._scheduler_resource_requirements(profile_payload)
        )
        shot_ranges = self._scheduler_shot_ranges(
            job,
            profile_payload,
            profile_path,
        )
        scheduled: list[ScheduledRenderTask] = []
        for variant in media_job.output_variants:
            if not variant.enabled:
                continue
            for shot_id, complexity, shot_start, shot_end in shot_ranges:
                chunk_start = shot_start
                while chunk_start <= shot_end:
                    chunk_end = min(shot_end, chunk_start + chunk_size - 1)
                    shot_digest = hashlib.sha256(
                        shot_id.encode("utf-8")
                    ).hexdigest()[:8]
                    chunk_id = (
                        f"chunk-{chunk_start:06d}-{chunk_end:06d}-{shot_digest}"
                    )
                    draft = ShotRenderTask(
                        id="unsealed-task",
                        job_id=media_job.id,
                        output_variant_id=variant.id,
                        shot_id=shot_id,
                        chunk_id=chunk_id,
                        frame_start=chunk_start,
                        frame_end=chunk_end,
                        width=variant.width,
                        height=variant.height,
                        fps=variant.fps,
                        complexity_class=complexity,
                        package_sha256=media_job.package.package_sha256,
                        matrix_sha256=media_job.output_matrix.matrix_sha256,
                        output_variant_sha256=variant.output_variant_sha256,
                        scene_sha256=variant.composition_profile.scene_sha256,
                        render_profile_sha256=variant.render_profile_sha256,
                        composition_sha256=(
                            variant.composition_profile.composition_sha256
                        ),
                        task_sha256="0" * 64,
                        output_root=variant.frames_root,
                        required_worker_kind=required_worker_kind,
                        minimum_gpu_memory_bytes=(
                            requirements.gpu_memory_bytes or None
                        ),
                    )
                    scheduled.append(
                        self.scheduler.submit_task(
                            seal_task_identity(draft),
                            requirements=requirements,
                            now=job.updated_at,
                        )
                    )
                    chunk_start = chunk_end + 1
        return tuple(scheduled)

    @staticmethod
    def _scheduler_stage_state(
        tasks: tuple[ScheduledRenderTask, ...],
    ) -> ProgressState:
        if tasks and all(task.state is TaskState.COMPLETE for task in tasks):
            return ProgressState.COMPLETE
        if any(
            task.state in {TaskState.LEASED, TaskState.RUNNING}
            for task in tasks
        ):
            return ProgressState.RUNNING
        if any(task.state is TaskState.FAILED for task in tasks):
            return ProgressState.FAILED
        if any(task.attempt > 1 for task in tasks):
            return ProgressState.PAUSED
        return ProgressState.PENDING

    def _scheduler_variant_projection(
        self,
        variant: OutputVariant,
        tasks: tuple[ScheduledRenderTask, ...],
        now: datetime,
    ) -> OutputVariant:
        if not variant.enabled or not tasks:
            return variant
        completed_units = sum(
            task.task.frame_count
            for task in tasks
            if task.state is TaskState.COMPLETE
        )
        total_units = sum(task.task.frame_count for task in tasks)
        active = tuple(
            task
            for task in tasks
            if task.state in {TaskState.LEASED, TaskState.RUNNING}
        )
        retry_count = sum(max(0, task.attempt - 1) for task in tasks)
        rendering_stage = StageProgress(
            stage=RenderStage.RENDERING,
            state=self._scheduler_stage_state(tasks),
            completed_units=completed_units,
            total_units=total_units,
            unit="frames",
            started_at=(
                min(task.created_at for task in active) if active else None
            ),
            updated_at=now,
        )
        stages = tuple(
            rendering_stage if stage.stage is RenderStage.RENDERING else stage
            for stage in variant.progress.stages
        )
        progress = variant.progress.model_copy(
            update={
                "stages": stages,
                "current_frame": (
                    min(task.task.frame_start for task in active)
                    if active
                    else None
                ),
                "in_flight_frames": tuple(
                    sorted({task.task.frame_start for task in active})
                ),
                "active_worker_ids": tuple(
                    sorted(
                        {
                            task.leased_worker_id
                            for task in active
                            if task.leased_worker_id is not None
                        }
                    )
                ),
                "retry_count": max(
                    variant.progress.retry_count,
                    retry_count,
                ),
                "updated_at": now,
            }
        )
        return variant.model_copy(update={"progress": progress})

    def _scheduler_projection_changes(
        self,
        job: JobRecord,
        *,
        event_type: str,
        now: datetime,
    ) -> dict[str, object]:
        tasks = self.scheduler.list_tasks(job_id=job.id)
        if not tasks:
            return {}
        tasks_by_variant = {
            variant.id: tuple(
                task
                for task in tasks
                if task.task.output_variant_id == variant.id
            )
            for variant in job.output_variants
        }
        variants = tuple(
            self._scheduler_variant_projection(
                variant,
                tasks_by_variant[variant.id],
                now,
            )
            for variant in job.output_variants
        )
        active_tasks = tuple(
            task
            for task in tasks
            if task.state in {TaskState.LEASED, TaskState.RUNNING}
        )
        failed_tasks = tuple(
            task for task in tasks if task.state is TaskState.FAILED
        )
        active_task = active_tasks[0] if active_tasks else None
        all_complete = all(task.state is TaskState.COMPLETE for task in tasks)
        retry_count = sum(max(0, task.attempt - 1) for task in tasks)
        workers = tuple(
            worker.capabilities
            for worker in self.scheduler.list_workers(
                include_inactive=False,
                now=now,
            )
        )
        active_variant_id = (
            active_task.task.output_variant_id
            if active_task is not None
            else job.active_variant_id
        )
        active_variant = next(
            (
                variant
                for variant in variants
                if variant.id == active_variant_id
            ),
            next((variant for variant in variants if variant.enabled), None),
        )
        changes: dict[str, object] = {
            "output_variants": variants,
            "active_variant_id": active_variant_id,
            "stages": active_variant.progress.stages if active_variant else (),
            "workers": workers,
            "chunks_total": len(tasks),
            "chunks_completed": sum(
                task.state is TaskState.COMPLETE for task in tasks
            ),
            "retry_count": max(job.retry_count, retry_count),
            "renderer_event_type": event_type,
            "renderer_event_sequence": (job.renderer_event_sequence or 0) + 1,
            "renderer_status": event_type,
            "last_output_at": now,
        }
        if active_task is not None:
            changes.update(
                {
                    "state": JobState.RUNNING,
                    "phase": JobPhase.RENDER_FRAME,
                    "renderer_active": True,
                    "watcher_active": True,
                    "started_at": job.started_at or now,
                    "worker_id": active_task.leased_worker_id,
                    "active_chunk_id": active_task.task.chunk_id,
                    "current_shot_id": active_task.task.shot_id,
                    "current_complexity_class": (
                        active_task.task.complexity_class
                    ),
                    "current_frame": active_task.task.frame_start,
                    "chunk_start": active_task.task.frame_start,
                    "chunk_end": active_task.task.frame_end,
                    "current_frame_started_at": now,
                }
            )
        else:
            changes.update(
                {
                    "renderer_active": False,
                    "watcher_active": False,
                    "worker_id": None,
                    "active_chunk_id": None,
                    "current_shot_id": None,
                    "current_complexity_class": None,
                    "current_frame": None,
                    "chunk_start": None,
                    "chunk_end": None,
                    "current_frame_started_at": None,
                }
            )
            if all_complete and job.state not in _TERMINAL_STATES:
                changes.update(
                    {
                        "state": JobState.VERIFYING,
                        "phase": JobPhase.FINAL_VERIFY,
                    }
                )
            elif event_type == "scheduler_task_failed" and failed_tasks:
                changes.update(
                    {
                        "state": JobState.FAILED,
                        "phase": JobPhase.RENDER_FRAME,
                        "completed_at": now,
                        "error": StructuredError(
                            code="scheduler_task_failed",
                            title="Scheduled render task failed",
                            summary=(
                                "A render worker reported a terminal failure for "
                                "an immutable render task."
                            ),
                            recommended_action=(
                                "Inspect the task failure and explicitly retry "
                                "only after its exact identity is still valid."
                            ),
                            retryable=False,
                            timestamp=now,
                            job_id=job.id,
                        ),
                    }
                )
            elif event_type in {"scheduler_worker_lost", "scheduler_task_failed"}:
                changes.update(
                    {
                        "state": JobState.RESUMABLE,
                        "phase": JobPhase.RENDER_FRAME,
                        "warning": (
                            "A render worker stopped reporting. Its immutable "
                            "task was requeued without overwriting completed work."
                        ),
                    }
                )
        return changes

    def _materialize_media_render_job(
        self,
        job: JobRecord,
        profile: ProfileSummary,
        profile_payload: Mapping[str, Any],
        profile_path: Path,
        definitions: tuple[_OutputVariantDefinition, ...],
        *,
        authorization_token_value: str,
    ) -> tuple[MediaRenderJob, tuple[WorkerCapabilities, ...]]:
        now = job.updated_at
        output_variants = tuple(
            OutputVariant(
                id=definition.id,
                enabled=definition.enabled,
                required=definition.required,
                width=definition.width,
                height=definition.height,
                fps=definition.fps,
                deliverable_role=definition.deliverable_role,
                render_profile_id=definition.render_profile_id,
                render_profile_sha256=definition.render_profile_sha256,
                composition_profile=definition.composition_profile,
                output_variant_sha256=definition.output_variant_sha256,
                frames_root=definition.frames_root,
                preview_root=definition.preview_root,
                encode_root=definition.encode_root,
                qa_root=definition.qa_root,
                progress=OutputVariantProgress(
                    output_variant_id=definition.id,
                    stages=(
                        self._initial_stage_progress(job.total_frame_count, now)
                        if definition.enabled
                        else ()
                    ),
                    total_frames=(
                        job.total_frame_count if definition.enabled else 0
                    ),
                    updated_at=now,
                ),
            )
            for definition in definitions
        )
        enabled = tuple(variant for variant in output_variants if variant.enabled)
        package_value = profile_payload.get("package")
        package = package_value if isinstance(package_value, dict) else {}
        package_sha256 = _normalized_sha256(
            package.get("sha256", profile_payload.get("packageSha256"))
        ) or _canonical_sha256(
            {
                "sceneSha256": job.identity.scene_sha256.lower(),
                "renderProfileSha256": job.identity.profile_sha256.lower(),
                "outputVariants": [
                    {
                        "id": variant.id,
                        "sha256": variant.output_variant_sha256,
                    }
                    for variant in output_variants
                ],
            }
        )
        package_id_value = package.get(
            "id",
            profile_payload.get("packageId", f"package-{package_sha256[:20]}"),
        )
        source_revision_value = package.get(
            "sourceRevision",
            profile_payload.get("sourceRevision", profile.id),
        )
        if (
            not isinstance(package_id_value, str)
            or _IDENTIFIER_RE.fullmatch(package_id_value.strip()) is None
            or not isinstance(source_revision_value, str)
            or not source_revision_value.strip()
        ):
            raise self._output_variant_contract_error(
                "The package ID or source revision is invalid."
            )
        matrix_value = profile_payload.get("outputMatrix")
        matrix = matrix_value if isinstance(matrix_value, dict) else {}
        enabled_ids = tuple(variant.id for variant in enabled)
        matrix_sha256 = _normalized_sha256(
            matrix.get("sha256", profile_payload.get("outputMatrixSha256"))
        ) or _canonical_sha256(
            {
                "packageSha256": package_sha256,
                "enabledVariantIds": enabled_ids,
                "variantSha256ById": {
                    variant.id: variant.output_variant_sha256
                    for variant in enabled
                },
            }
        )
        matrix_id_value = matrix.get(
            "id",
            profile_payload.get("outputMatrixId", f"matrix-{matrix_sha256[:20]}"),
        )
        if (
            not isinstance(matrix_id_value, str)
            or _IDENTIFIER_RE.fullmatch(matrix_id_value.strip()) is None
        ):
            raise self._output_variant_contract_error(
                "The output-matrix ID is invalid."
            )
        tenant_value = profile_payload.get("tenant")
        tenant = tenant_value if isinstance(tenant_value, dict) else {}
        namespace_value = tenant.get(
            "namespace",
            profile_payload.get("tenantNamespace", "local"),
        )
        deployment_value = tenant.get(
            "deploymentId",
            profile_payload.get("deploymentId"),
        )
        if (
            not isinstance(namespace_value, str)
            or _IDENTIFIER_RE.fullmatch(namespace_value.strip()) is None
            or (
                deployment_value is not None
                and (
                    not isinstance(deployment_value, str)
                    or _IDENTIFIER_RE.fullmatch(deployment_value.strip()) is None
                )
            )
        ):
            raise self._output_variant_contract_error(
                "The tenant or deployment identity is invalid."
            )
        project_revision = profile_payload.get("projectRevision")
        if project_revision is not None and not isinstance(project_revision, str):
            project_revision = str(project_revision)
        try:
            media_job = MediaRenderJob(
                id=job.id,
                project=ProjectRef(
                    tenant=TenantRef(
                        namespace=namespace_value.strip(),
                        deployment_id=(
                            deployment_value.strip()
                            if isinstance(deployment_value, str)
                            else None
                        ),
                    ),
                    project_id=job.identity.project_id,
                    revision=(
                        project_revision.strip()
                        if isinstance(project_revision, str)
                        and project_revision.strip()
                        else None
                    ),
                ),
                package=PackageIdentity(
                    package_id=package_id_value.strip(),
                    package_sha256=package_sha256,
                    source_revision=source_revision_value.strip(),
                    source_hashes={
                        "scene": job.identity.scene_sha256.lower(),
                        "render-profile": job.identity.profile_sha256.lower(),
                    },
                ),
                output_matrix=OutputVariantMatrixIdentity(
                    matrix_id=matrix_id_value.strip(),
                    matrix_sha256=matrix_sha256,
                    package_sha256=package_sha256,
                    enabled_variant_ids=enabled_ids,
                    variant_sha256_by_id={
                        variant.id: variant.output_variant_sha256
                        for variant in enabled
                    },
                ),
                output_variants=output_variants,
                created_at=job.created_at,
                updated_at=job.updated_at,
                authorization_sha256=hashlib.sha256(
                    authorization_token_value.encode("utf-8")
                ).hexdigest(),
            )
        except ValueError as exc:
            raise self._output_variant_contract_error(
                "The saved V2 output-variant identities or artifact roots are invalid."
            ) from exc
        workers = self._local_worker_capabilities()
        eta_service = EtaService()
        workloads = self._render_workloads(
            job,
            media_job.output_variants,
            profile_payload,
            profile_path,
            rendered_frames=0,
            latest_rendered_frame=None,
            fallback_complexity="default",
        )
        aggregate_eta = eta_service.forecast_matrix(media_job, workloads)
        active = next(variant for variant in media_job.output_variants if variant.enabled)
        job.output_variants = media_job.output_variants
        job.active_variant_id = active.id
        job.stages = active.progress.stages
        job.aggregate_eta = aggregate_eta
        job.workers = workers
        return media_job, workers

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
        _profile_path, profile_payload, _profile_hash = self.discovery.profile_source(
            request.profile_id
        )
        variant_definitions = self._output_variant_definitions(
            profile,
            profile_payload,
            enabled_output_variant_ids=request.enabled_output_variant_ids,
        )
        enabled_variants = tuple(
            definition for definition in variant_definitions if definition.enabled
        )
        authorization_matrix = (
            [variant.id for variant in enabled_variants]
            if "outputVariants" in profile_payload or "outputVariant" in profile_payload
            else None
        )
        matrix_authorized, matrix_authorization_issues, _matrix_token = (
            validate_authorization_record(
                Path(profile.path),
                Path(scene.path),
                profile_payload,
                enabled_output_variant_ids=authorization_matrix,
            )
        )
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
                id="output-variants",
                label="Output variants verified",
                status=CheckStatus.PASS,
                summary=(
                    "Enabled output matrix: "
                    + ", ".join(
                        f"{variant.id} ({variant.width}x{variant.height})"
                        for variant in enabled_variants
                    )
                    + "."
                ),
            ),
            PreflightCheck(
                id="output",
                label="Output folder ready",
                status=CheckStatus.PASS if output.usable else CheckStatus.FAIL,
                summary="Output is empty or exactly resumable." if output.usable else "Output folder cannot be used.",
                detail="; ".join(output.issues) or None,
            ),
        ]
        if (
            request.renderer == RendererKind.PRODUCTION
            and any(not variant.required for variant in enabled_variants)
        ):
            checks.append(
                PreflightCheck(
                    id="optional-variant-calibration",
                    label="Optional output calibrated",
                    status=(
                        CheckStatus.PASS
                        if profile.calibrated
                        else CheckStatus.FAIL
                    ),
                    summary=(
                        "The selected optional output has a measured local calibration."
                        if profile.calibrated
                        else (
                            "This optional output remains disabled until its own "
                            "final-resolution calibration and aggregate SLA gate pass."
                        )
                    ),
                )
            )
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
                status=CheckStatus.PASS if matrix_authorized else CheckStatus.WARNING,
                summary=(
                    "Exact scene, profile, and enabled output matrix are authorized."
                    if matrix_authorized
                    else "Authorization is required for the exact enabled output matrix."
                ),
                detail="; ".join(matrix_authorization_issues) or None,
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
        ready = all(check.status != CheckStatus.FAIL for check in checks) and matrix_authorized
        return PreflightResult(
            ready=ready,
            authorization_required=not matrix_authorized,
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
                enabled_output_variant_ids=(
                    list(identity.enabled_output_variant_ids)
                    if identity.enabled_output_variant_ids
                    else None
                ),
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
            definitions = self._output_variant_definitions(
                profile,
                profile_payload,
                enabled_output_variant_ids=request.enabled_output_variant_ids,
            )
            media_job: MediaRenderJob | None = None
            scheduled_tasks: tuple[ScheduledRenderTask, ...] = ()
            if definitions:
                media_job, _workers = self._materialize_media_render_job(
                    job,
                    profile,
                    profile_payload,
                    profile_path,
                    definitions,
                    authorization_token_value=token,
                )
                scheduled_tasks = self._schedule_media_render_job(
                    job,
                    media_job,
                    profile_payload,
                    profile_path,
                    chunk_size=max(1, chunk_size),
                )
                if scheduled_tasks:
                    job.chunks_total = len(scheduled_tasks)
            self.store.put_job(job)
            if media_job is not None:
                self.store.put_media_render_job(media_job)
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

    def scheduled_render_tasks(
        self,
        job_id: str,
    ) -> tuple[ScheduledRenderTask, ...]:
        self.get_job(job_id)
        return self.scheduler.list_tasks(job_id=job_id)

    def register_render_worker(
        self,
        capabilities: WorkerCapabilities,
        *,
        now: datetime | None = None,
        heartbeat_timeout: timedelta | None = None,
    ) -> SchedulerWorker:
        return self.scheduler.register_worker(
            capabilities,
            now=now,
            heartbeat_timeout=heartbeat_timeout,
        )

    def local_subprocess_worker(
        self,
        capabilities: WorkerCapabilities,
        command_builder: LocalTaskCommandBuilder,
    ) -> LocalSubprocessRenderWorker:
        """Bind the required local subprocess adapter to this persistent control plane."""
        return LocalSubprocessRenderWorker(
            self,
            capabilities,
            command_builder,
        )

    async def run_local_subprocess_task_once(
        self,
        capabilities: WorkerCapabilities,
        command_builder: LocalTaskCommandBuilder,
        *,
        job_id: str | None = None,
    ) -> LocalWorkerRunResult | None:
        """Run one scheduler-owned local task; callers control any worker loop."""
        return await self.local_subprocess_worker(
            capabilities,
            command_builder,
        ).run_once(job_id=job_id)

    def heartbeat_render_worker(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        heartbeat_timeout: timedelta | None = None,
    ) -> SchedulerWorker:
        return self.scheduler.heartbeat_worker(
            worker_id,
            now=now,
            heartbeat_timeout=heartbeat_timeout,
        )

    async def _synchronize_scheduler_job(
        self,
        job_id: str,
        *,
        event_type: str,
        now: datetime,
    ) -> JobRecord:
        job = self.get_job(job_id)
        changes = self._scheduler_projection_changes(
            job,
            event_type=event_type,
            now=now,
        )
        if not changes:
            return job
        return await self.update_job(job_id, **changes)

    async def claim_render_task(
        self,
        worker_id: str,
        *,
        job_id: str | None = None,
        now: datetime | None = None,
        lease_duration: timedelta | None = None,
    ) -> LeaseGrant | None:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._job_lock:
            if job_id is not None:
                self.get_job(job_id)
            grant = self.scheduler.claim_next_task(
                worker_id,
                job_id=job_id,
                now=timestamp,
                lease_duration=lease_duration,
            )
            if grant is not None:
                await self._synchronize_scheduler_job(
                    grant.task.job_id,
                    event_type="scheduler_task_leased",
                    now=timestamp,
                )
            return grant

    async def start_scheduled_render_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> ScheduledRenderTask:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._job_lock:
            task = self.scheduler.start_task(
                lease_id,
                worker_id,
                lease_token,
                now=timestamp,
            )
            await self._synchronize_scheduler_job(
                task.task.job_id,
                event_type="scheduler_task_started",
                now=timestamp,
            )
            return task

    async def heartbeat_scheduled_render_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
        lease_duration: timedelta | None = None,
        worker_timeout: timedelta | None = None,
    ) -> WorkerLease:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._job_lock:
            lease = self.scheduler.heartbeat_lease(
                lease_id,
                worker_id,
                lease_token,
                now=timestamp,
                lease_duration=lease_duration,
                worker_timeout=worker_timeout,
            )
            await self._synchronize_scheduler_job(
                lease.job_id,
                event_type="scheduler_heartbeat",
                now=timestamp,
            )
            return lease

    async def complete_scheduled_render_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> ScheduledRenderTask:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._job_lock:
            task = self.scheduler.complete_task(
                lease_id,
                worker_id,
                lease_token,
                now=timestamp,
            )
            await self._synchronize_scheduler_job(
                task.task.job_id,
                event_type="scheduler_task_completed",
                now=timestamp,
            )
            return task

    async def fail_scheduled_render_task(
        self,
        lease_id: str,
        worker_id: str,
        lease_token: str,
        *,
        retry: bool = True,
        reason: str = "worker-reported-failure",
        now: datetime | None = None,
    ) -> ScheduledRenderTask:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._job_lock:
            task = self.scheduler.fail_task(
                lease_id,
                worker_id,
                lease_token,
                retry=retry,
                reason=reason,
                now=timestamp,
            )
            await self._synchronize_scheduler_job(
                task.task.job_id,
                event_type="scheduler_task_failed",
                now=timestamp,
            )
            return task

    async def reap_render_scheduler(
        self,
        *,
        now: datetime | None = None,
    ) -> SchedulerReapResult:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        async with self._job_lock:
            return await self._reap_render_scheduler_locked(timestamp)

    async def _reap_render_scheduler_locked(
        self,
        timestamp: datetime,
    ) -> SchedulerReapResult:
        result = self.scheduler.reap_expired(now=timestamp)
        affected_job_ids = {
            task.task.job_id
            for task_id in result.requeued_task_ids
            if (task := self.scheduler.get_task(task_id)) is not None
        }
        for job_id in sorted(affected_job_ids):
            await self._synchronize_scheduler_job(
                job_id,
                event_type="scheduler_worker_lost",
                now=timestamp,
            )
        return result

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

    @staticmethod
    def _increment_attempt_telemetry(
        job: JobRecord,
        *,
        retry_delta: int = 0,
        failure_delta: int = 0,
    ) -> tuple[OutputVariant, ...]:
        if not job.output_variants or (retry_delta == 0 and failure_delta == 0):
            return job.output_variants
        target_id = job.active_variant_id
        if target_id is None:
            target_id = next(
                (variant.id for variant in job.output_variants if variant.enabled),
                None,
            )
        variants: list[OutputVariant] = []
        for variant in job.output_variants:
            if not variant.enabled or variant.id != target_id:
                variants.append(variant)
                continue
            progress = variant.progress.model_copy(
                update={
                    "retry_count": variant.progress.retry_count + retry_delta,
                    "failure_count": (
                        variant.progress.failure_count + failure_delta
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            variants.append(variant.model_copy(update={"progress": progress}))
        return tuple(variants)

    async def update_job(self, job_id: str, **changes: object) -> JobRecord:
        job = self.get_job(job_id)
        allow_retry_restart = bool(changes.pop("_allow_retry_restart", False))
        if (
            job.state == JobState.CANCEL_REQUESTED
            and changes.get("state")
            in {
                JobState.STARTING,
                JobState.RUNNING,
                JobState.STOP_REQUESTED,
                JobState.FINISHING_CURRENT_CHUNK,
            }
        ):
            changes["state"] = JobState.CANCEL_REQUESTED
        if (
            job.state == JobState.RETRY_REQUESTED
            and not allow_retry_restart
            and changes.get("state")
            in {
                JobState.STARTING,
                JobState.RUNNING,
                JobState.STOP_REQUESTED,
                JobState.FINISHING_CURRENT_CHUNK,
            }
        ):
            changes["state"] = JobState.RETRY_REQUESTED
        routed_variant = changes.get("output_variant_id")
        if routed_variant is not None:
            enabled_ids = {
                variant.id for variant in job.output_variants if variant.enabled
            }
            if (
                not isinstance(routed_variant, str)
                or (
                    enabled_ids
                    and routed_variant not in enabled_ids
                )
                or (
                    not enabled_ids
                    and routed_variant != job.identity.output_variant_id
                )
            ):
                raise MissionControlError(
                    409,
                    "render_event_variant_mismatch",
                    "Renderer event identity does not match",
                    "The renderer event targets an unknown or disabled output variant.",
                    "Reject the event and restart the exact authorized render worker.",
                    job_id=job.id,
                )
        updated_at = datetime.now(UTC)
        changes["updated_at"] = updated_at
        changes.update(self._persistent_render_eta_changes(job, changes, updated_at))
        if "output_variants" not in changes:
            changes.update(
                self._output_variant_progress_changes(job, changes, updated_at)
            )
        if changes.get("state") == JobState.FAILED and job.state != JobState.FAILED:
            changes.setdefault("failure_count", job.failure_count + 1)
            telemetry_job = job.model_copy(
                update={
                    "output_variants": changes.get(
                        "output_variants",
                        job.output_variants,
                    ),
                    "active_variant_id": changes.get(
                        "active_variant_id",
                        job.active_variant_id,
                    ),
                }
            )
            changes["output_variants"] = self._increment_attempt_telemetry(
                telemetry_job,
                failure_delta=1,
            )
        changes.pop("output_variant_id", None)
        updated = job.model_copy(update=changes)
        self.store.put_job(updated)
        canonical_job = self.store.get_media_render_job(job_id)
        if canonical_job is not None:
            self.store.put_media_render_job(
                canonical_job.model_copy(
                    update={
                        "output_variants": updated.output_variants,
                        "updated_at": updated.updated_at,
                    }
                )
            )
        event = self.store.append_event(self._event_from_job(updated))
        await self._notify_event(event.sequence)
        return updated

    def _persistent_render_eta_changes(
        self,
        job: JobRecord,
        changes: dict[str, object],
        recorded_at: datetime,
    ) -> dict[str, object]:
        """Record one completed-frame sample and rebuild restart-safe render ETA."""
        if changes.get("renderer_event_type") != "frame_written":
            return {}
        elapsed = changes.get("current_seconds_per_frame")
        rendered = changes.get("rendered_frame_count")
        frame = changes.get("latest_rendered_frame")
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or float(elapsed) <= 0
            or isinstance(rendered, bool)
            or not isinstance(rendered, int)
            or isinstance(frame, bool)
            or not isinstance(frame, int)
        ):
            return {}
        routed_variant = changes.get("output_variant_id")
        variant_id = (
            routed_variant
            if isinstance(routed_variant, str)
            else job.active_variant_id or job.identity.output_variant_id
        )
        target_variant = next(
            (
                variant
                for variant in job.output_variants
                if variant.enabled and variant.id == variant_id
            ),
            None,
        )
        complexity = str(
            changes.get("current_complexity_class")
            or job.current_complexity_class
            or "default"
        )
        worker_id = str(changes.get("worker_id") or job.worker_id or "local-worker")
        state = self.store.get_eta_state(job.id)
        service = EtaService.from_state(state) if state is not None else EtaService()
        service.record_sample(
            EtaSample(
                output_variant_id=variant_id,
                stage=RenderStage.RENDERING,
                complexity_class=complexity,
                task_id=f"frame-{frame}",
                worker_id=worker_id,
                duration_seconds=float(elapsed),
                completed_units=1,
                recorded_at=recorded_at,
            )
        )
        service.set_worker_count(
            variant_id,
            RenderStage.RENDERING,
            1,
            observed_at=recorded_at,
        )
        total_frames = (
            target_variant.progress.total_frames
            if target_variant is not None
            else job.total_frame_count
        )
        remaining = max(0, total_frames - rendered)
        current_estimate = service.estimate(
            StageWorkload(
                output_variant_id=variant_id,
                stage=RenderStage.RENDERING,
                complexity_class=complexity,
                remaining_units=(
                    (1 if remaining > 0 else 0)
                    if job.output_variants
                    else remaining
                ),
                parallelizable=True,
            )
        )
        p50_remaining = current_estimate.p50_remaining_seconds
        p90_remaining = current_estimate.p90_remaining_seconds
        aggregate_eta = job.aggregate_eta
        estimate_confidences = [current_estimate.confidence]
        if job.output_variants:
            try:
                profile_path, profile_payload, _profile_hash = (
                    self.discovery.profile_source(job.identity.profile_id)
                )
            except MissionControlError:
                profile_path = None
                profile_payload = {}
            canonical_job = self.store.get_media_render_job(job.id)
            if canonical_job is not None and profile_path is not None:
                workloads = self._render_workloads(
                    job,
                    canonical_job.output_variants,
                    profile_payload,
                    profile_path,
                    updated_variant_id=variant_id,
                    rendered_frames=rendered,
                    latest_rendered_frame=frame,
                    fallback_complexity=complexity,
                )
                active_estimates = [
                    service.estimate(workload)
                    for workload in workloads
                    if workload.output_variant_id == variant_id
                ]
                p50_values = [
                    estimate.p50_remaining_seconds
                    for estimate in active_estimates
                ]
                p90_values = [
                    estimate.p90_remaining_seconds
                    for estimate in active_estimates
                ]
                p50_remaining = (
                    None
                    if any(value is None for value in p50_values)
                    else sum(value for value in p50_values if value is not None)
                )
                p90_remaining = (
                    None
                    if any(value is None for value in p90_values)
                    else sum(value for value in p90_values if value is not None)
                )
                estimate_confidences = [
                    estimate.confidence for estimate in active_estimates
                ] or estimate_confidences
                aggregate_eta = service.forecast_matrix(
                    canonical_job,
                    workloads,
                )
        self.store.put_eta_state(job.id, service.snapshot())
        confidence_rank = {
            "unknown": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
        }
        confidence_value = min(
            (item.value for item in estimate_confidences),
            key=confidence_rank.__getitem__,
        )
        confidence = EtaConfidence(confidence_value)
        stage = StageProgress(
            stage=RenderStage.RENDERING,
            state=(
                ProgressState.COMPLETE if remaining == 0 else ProgressState.RUNNING
            ),
            completed_units=rendered,
            total_units=total_frames,
            unit="frames",
            throughput_per_second=(
                None
                if current_estimate.p50_seconds_per_unit is None
                else 1.0 / current_estimate.p50_seconds_per_unit
            ),
            elapsed_seconds=(
                max(0.0, (recorded_at - job.started_at).total_seconds())
                if job.started_at is not None
                else 0.0
            ),
            eta_p50_seconds=p50_remaining,
            eta_p90_seconds=p90_remaining,
            started_at=job.started_at,
            updated_at=recorded_at,
        )
        current_stages = (
            target_variant.progress.stages
            if target_variant is not None
            else job.stages
        )
        stages = tuple(
            item
            for item in current_stages
            if item.stage is not RenderStage.RENDERING
        ) + (stage,)
        return {
            "rolling_median_seconds": current_estimate.p50_seconds_per_unit,
            "rolling_mean_seconds": current_estimate.p50_seconds_per_unit,
            "p90_seconds": current_estimate.p90_seconds_per_unit,
            "estimated_completion_time": (
                None
                if p50_remaining is None
                else recorded_at + timedelta(seconds=p50_remaining)
            ),
            "eta_confidence": confidence,
            "stages": stages,
            "aggregate_eta": aggregate_eta,
        }

    def _output_variant_progress_changes(
        self,
        job: JobRecord,
        changes: dict[str, object],
        updated_at: datetime,
    ) -> dict[str, object]:
        if not job.output_variants:
            return {}
        routed_variant = changes.get("output_variant_id")
        variant_id = (
            routed_variant
            if isinstance(routed_variant, str)
            else job.active_variant_id or job.identity.output_variant_id
        )
        variants = []
        matched = False
        for variant in job.output_variants:
            if variant.id != variant_id:
                variants.append(variant)
                continue
            matched = True
            progress = variant.progress
            rendered_value = changes.get(
                "rendered_frame_count",
                progress.rendered_frames,
            )
            validated_value = changes.get(
                "validated_frame_count",
                progress.validated_frames,
            )
            rendered = (
                rendered_value
                if isinstance(rendered_value, int) and not isinstance(rendered_value, bool)
                else progress.rendered_frames
            )
            validated = (
                validated_value
                if isinstance(validated_value, int)
                and not isinstance(validated_value, bool)
                else progress.validated_frames
            )
            current = changes.get("current_frame", progress.current_frame)
            latest_rendered = changes.get(
                "latest_rendered_frame",
                progress.latest_rendered_frame,
            )
            latest_safe = (
                job.frame_start + validated - 1 if validated > 0 else None
            )
            in_flight = set(progress.in_flight_frames)
            if (
                changes.get("renderer_event_type") == "frame_written"
                and isinstance(latest_rendered, int)
            ):
                in_flight.add(latest_rendered)
            if latest_safe is not None:
                in_flight = {frame for frame in in_flight if frame > latest_safe}
            worker_id = changes.get("worker_id", job.worker_id)
            workers = set(progress.active_worker_ids)
            if isinstance(worker_id, str) and worker_id:
                workers.add(worker_id)
            preview_url = changes.get(
                "latest_frame_preview",
                progress.preview_url,
            )
            full_frame_url = changes.get(
                "latest_full_frame_url",
                progress.full_frame_url,
            )
            if isinstance(latest_rendered, int):
                preview_url = (
                    f"/api/mission-control/render/{job.id}/preview"
                    f"?v={latest_rendered}&output_variant_id={variant_id}"
                )
                full_frame_url = (
                    f"/api/mission-control/render/{job.id}/frame"
                    f"?v={latest_rendered}&output_variant_id={variant_id}"
                )
            latest_frame_artifact = progress.latest_frame_artifact
            latest_frame_artifact_frame = progress.latest_frame_artifact_frame
            latest_frame_written_at = progress.latest_frame_written_at
            artifact_value = changes.get("latest_frame_artifact")
            if (
                changes.get("renderer_event_type") == "frame_written"
                and isinstance(artifact_value, str)
                and isinstance(latest_rendered, int)
            ):
                latest_frame_artifact = artifact_value
                latest_frame_artifact_frame = latest_rendered
                latest_frame_written_at = updated_at
            elif (
                latest_frame_artifact_frame is not None
                and latest_frame_artifact_frame != latest_rendered
            ):
                latest_frame_artifact = None
                latest_frame_artifact_frame = None
                latest_frame_written_at = None
            variant_progress = progress.model_copy(
                update={
                    "stages": changes.get("stages", progress.stages),
                    "rendered_frames": rendered,
                    "validated_frames": validated,
                    "current_frame": current,
                    "latest_rendered_frame": latest_rendered,
                    "latest_safe_frame": latest_safe,
                    "in_flight_frames": tuple(sorted(in_flight)),
                    "active_worker_ids": tuple(sorted(workers)),
                    "preview_url": preview_url,
                    "full_frame_url": full_frame_url,
                    "latest_frame_artifact": latest_frame_artifact,
                    "latest_frame_artifact_frame": latest_frame_artifact_frame,
                    "latest_frame_written_at": latest_frame_written_at,
                    "updated_at": updated_at,
                }
            )
            variants.append(variant.model_copy(update={"progress": variant_progress}))
        if not matched:
            return {}
        return {
            "output_variants": tuple(variants),
            "active_variant_id": variant_id,
        }

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
        event_variant = next(
            (
                variant
                for variant in job.output_variants
                if variant.enabled and variant.id == job.active_variant_id
            ),
            None,
        )
        return RenderEvent(
            sequence=0,
            timestamp=datetime.now(UTC),
            job_id=job.id,
            project_id=job.identity.project_id,
            state=job.state,
            phase=job.phase,
            scene_id=job.identity.scene_id,
            scene_sha256=job.identity.scene_sha256,
            profile_id=(
                event_variant.render_profile_id
                if event_variant is not None
                else job.identity.profile_id
            ),
            profile_sha256=(
                event_variant.render_profile_sha256.upper()
                if event_variant is not None
                else job.identity.profile_sha256
            ),
            output_variant_id=(
                event_variant.id
                if event_variant is not None
                else job.identity.output_variant_id
            ),
            output_width=(
                event_variant.width
                if event_variant is not None
                else job.identity.output_width
            ),
            output_height=(
                event_variant.height
                if event_variant is not None
                else job.identity.output_height
            ),
            composition_profile_id=(
                event_variant.composition_profile.id
                if event_variant is not None
                else job.identity.composition_profile_id
            ),
            composition_profile_sha256=(
                event_variant.composition_profile.composition_sha256
                if event_variant is not None
                else job.identity.composition_profile_sha256
            ),
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
            current_complexity_class=job.current_complexity_class,
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
            latest_frame_artifact=job.latest_frame_artifact,
            latest_full_frame_url=job.latest_full_frame_url,
            latest_log_line=job.latest_log_line,
            warning=job.warning,
            error=job.error,
            retry_count=job.retry_count,
            failure_count=job.failure_count,
            safe_stop_status=job.safe_stop_status,
            output_variants=job.output_variants,
            active_variant_id=job.active_variant_id,
            stages=job.stages,
            aggregate_eta=job.aggregate_eta,
            workers=job.workers,
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
    ) -> list[RenderEvent | VideoGenerationEvent]:
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
        async with self._job_lock:
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
                profile_path, _payload, _hash = self.discovery.profile_source(
                    job.identity.profile_id
                )
                scene_path = Path(
                    self.discovery.get_scene(job.identity.scene_id).path
                )
                self.production_renderer.request_stop(
                    job,
                    profile_path=profile_path,
                    scene_path=scene_path,
                )
            await self.update_job(
                job.id,
                state=JobState.STOP_REQUESTED,
                safe_stop_status=SafeStopStatus.REQUESTED,
            )
            await self.add_log(
                job.id,
                "warning",
                "Stop after the current chunk was requested.",
            )
            return self.get_job(job.id)

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
        async with self._job_lock:
            job = self.get_job(job_id)
            if (
                job.state != JobState.STOP_REQUESTED
                or job.safe_stop_status != SafeStopStatus.REQUESTED
            ):
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
            await self.update_job(
                job.id,
                state=JobState.RUNNING,
                safe_stop_status=SafeStopStatus.NONE,
            )
            await self.add_log(
                job.id,
                "info",
                "Pending safe-stop request was cancelled.",
            )
            return self.get_job(job.id)

    async def cancel_render(
        self,
        job_id: str,
        request: CancelRenderRequest,
    ) -> JobRecord:
        if not request.operator_confirmed:
            raise MissionControlError(
                422,
                "cancel_render_confirmation_required",
                "Confirmation required",
                "Cancelling a render stops after the current chunk is validated and published.",
                "Confirm cancellation for this exact render identity.",
                job_id=job_id,
            )
        async with self._job_lock:
            job = self.get_job(job_id)
            if (
                request.scene_sha256 != job.identity.scene_sha256
                or request.profile_sha256 != job.identity.profile_sha256
            ):
                raise MissionControlError(
                    409,
                    "cancel_render_identity_mismatch",
                    "Cancel identity does not match",
                    "Cancellation requires the exact original saved scene and profile hashes.",
                    "Refresh job details and confirm cancellation for the active identity.",
                    job_id=job.id,
                )
            if job.state == JobState.CANCEL_REQUESTED:
                return job
            if job.state not in {
                JobState.RUNNING,
                JobState.STOP_REQUESTED,
                JobState.FINISHING_CURRENT_CHUNK,
            }:
                raise MissionControlError(
                    409,
                    "render_not_cancellable",
                    "Render cannot be cancelled",
                    "Only an active render can be cancelled at its safe chunk boundary.",
                    "Return to the active render or inspect its terminal state.",
                    job_id=job.id,
                )
            if job.safe_stop_status != SafeStopStatus.REQUESTED:
                if job.renderer == RendererKind.FAKE:
                    self.fake_renderer.request_stop(job)
                else:
                    profile_path, _payload, _hash = self.discovery.profile_source(
                        job.identity.profile_id
                    )
                    scene_path = Path(
                        self.discovery.get_scene(job.identity.scene_id).path
                    )
                    self.production_renderer.request_stop(
                        job,
                        profile_path=profile_path,
                        scene_path=scene_path,
                    )
            await self.update_job(
                job.id,
                state=JobState.CANCEL_REQUESTED,
                safe_stop_status=SafeStopStatus.REQUESTED,
                warning=(
                    "Cancellation is pending at the current chunk boundary; "
                    "validated and published frames will be preserved."
                ),
            )
            await self.add_log(
                job.id,
                "warning",
                (
                    "Confirmed render cancellation requested; the active chunk "
                    "will be validated and published before termination."
                ),
            )
            return self.get_job(job.id)

    async def resume(self, job_id: str, request: ResumeRequest) -> JobRecord:
        async with self._job_lock:
            job = self.get_job(job_id)
            if job.state not in {JobState.PAUSED_SAFELY, JobState.RESUMABLE}:
                raise MissionControlError(
                    409,
                    "render_not_resumable",
                    "Render cannot be resumed",
                    "Only safely paused or resumable exact-identity jobs can resume.",
                    "Use failed-render retry for a failed job.",
                    job_id=job.id,
                )
            return await self._restart_exact_job_locked(
                job,
                request,
                retry_failed=False,
            )

    async def retry_failed(
        self,
        job_id: str,
        request: RetryFailedRenderRequest,
    ) -> JobRecord:
        if not request.operator_confirmed:
            raise MissionControlError(
                422,
                "retry_failed_confirmation_required",
                "Confirmation required",
                "Retry reuses the exact render identity and only fills missing or invalid work.",
                "Confirm retry for the failed render.",
                job_id=job_id,
            )
        async with self._job_lock:
            job = self.get_job(job_id)
            if job.state != JobState.FAILED:
                raise MissionControlError(
                    409,
                    "render_not_failed",
                    "Render cannot be retried",
                    "Only a failed exact-identity render can use failed-render retry.",
                    "Return to the active job or use exact resume for a safely paused job.",
                    job_id=job.id,
                )
            if job.error is not None and not job.error.retryable:
                raise MissionControlError(
                    409,
                    "render_failure_not_retryable",
                    "Render failure is not retryable",
                    "The saved failure indicates an identity or artifact condition that cannot be retried safely.",
                    "Resolve the saved failure and create a new exact render package if required.",
                    job_id=job.id,
                )
            return await self._restart_exact_job_locked(
                job,
                request,
                retry_failed=True,
            )

    async def retry_current_chunk(
        self,
        job_id: str,
        request: RetryCurrentChunkRequest,
    ) -> JobRecord:
        if not request.operator_confirmed:
            raise MissionControlError(
                422,
                "retry_current_chunk_confirmation_required",
                "Confirmation required",
                (
                    "Retrying stops only the isolated current-chunk attempt and "
                    "requeues that exact saved chunk identity."
                ),
                "Confirm retry for the current chunk.",
                job_id=job_id,
            )
        async with self._job_lock:
            job = self.get_job(job_id)
            if (
                request.scene_sha256 != job.identity.scene_sha256
                or request.profile_sha256 != job.identity.profile_sha256
            ):
                raise MissionControlError(
                    409,
                    "retry_current_chunk_identity_mismatch",
                    "Retry identity does not match",
                    (
                        "Current-chunk retry requires the exact original saved "
                        "scene and profile hashes."
                    ),
                    "Refresh job details before confirming this retry.",
                    job_id=job.id,
                )
            if job.state == JobState.FAILED:
                if job.error is not None and not job.error.retryable:
                    raise MissionControlError(
                        409,
                        "render_failure_not_retryable",
                        "Render failure is not retryable",
                        (
                            "The saved failure indicates an identity or artifact "
                            "condition that cannot be retried safely."
                        ),
                        (
                            "Resolve the saved failure and create a new exact render "
                            "package if required."
                        ),
                        job_id=job.id,
                    )
                return await self._restart_exact_job_locked(
                    job,
                    request,
                    retry_failed=True,
                )
            if job.state == JobState.RETRY_REQUESTED:
                return job
            if job.state != JobState.RUNNING:
                raise MissionControlError(
                    409,
                    "current_chunk_not_active",
                    "Current chunk is not retryable",
                    (
                        "An active current-chunk attempt is required. Stop, cancel, "
                        "and retry requests cannot overlap."
                    ),
                    "Return to a running render or retry its saved failed chunk.",
                    job_id=job.id,
                )
            if (
                job.chunk_start is None
                or job.chunk_end is None
                or job.chunk_end < job.chunk_start
                or job.renderer_active is False
                or job.watcher_active is False
            ):
                raise MissionControlError(
                    409,
                    "current_chunk_identity_unavailable",
                    "Current chunk identity is unavailable",
                    (
                        "The renderer has not reported a watched active chunk with "
                        "exact frame bounds."
                    ),
                    "Wait for the current chunk to begin, then retry it.",
                    retryable=True,
                    job_id=job.id,
                )
            latest_safe_frame = (
                job.frame_start + job.published_frame_count - 1
                if job.published_frame_count > 0
                else job.frame_start - 1
            )
            if job.chunk_end <= latest_safe_frame:
                raise MissionControlError(
                    409,
                    "current_chunk_already_published",
                    "Current chunk is already safe",
                    (
                        "The displayed chunk was already validated and published "
                        "before this retry request."
                    ),
                    "Wait for the next active chunk before requesting a retry.",
                    job_id=job.id,
                )
            pending = await self.update_job(
                job.id,
                state=JobState.RETRY_REQUESTED,
                safe_stop_status=SafeStopStatus.NONE,
                warning=(
                    f"Retry requested for exact chunk {job.chunk_start}-"
                    f"{job.chunk_end}. Validated prior chunks remain untouched "
                    "while the isolated active attempt stops."
                ),
            )
            await self.add_log(
                job.id,
                "warning",
                (
                    f"Retry current chunk requested for frames {job.chunk_start}-"
                    f"{job.chunk_end}; stopping only the isolated in-flight attempt."
                ),
            )
            try:
                if job.renderer == RendererKind.FAKE:
                    await self.fake_renderer.request_retry_current_chunk(job)
                    return await self._restart_exact_job_locked(
                        pending,
                        request,
                        retry_failed=True,
                    )
                await self.production_renderer.request_retry_current_chunk(job)
            except MissionControlError:
                current = self.get_job(job.id)
                if (
                    current.state == JobState.RETRY_REQUESTED
                    and process_is_alive(current.process_id)
                ):
                    await self.update_job(
                        job.id,
                        state=JobState.RUNNING,
                        warning=None,
                        _allow_retry_restart=True,
                    )
                raise
            return self.get_job(job.id)

    async def restart_retry_current_chunk(self, job_id: str) -> JobRecord:
        """Continue a confirmed active retry after its isolated attempt exits."""
        async with self._job_lock:
            job = self.get_job(job_id)
            if job.state != JobState.RETRY_REQUESTED:
                return job
            if job.chunk_start is None or job.chunk_end is None:
                raise MissionControlError(
                    409,
                    "retry_current_chunk_identity_lost",
                    "Current chunk identity was lost",
                    "The saved retry no longer has exact chunk frame bounds.",
                    "Inspect the persisted job before attempting another retry.",
                    job_id=job.id,
                )
            latest_safe_frame = (
                job.frame_start + job.published_frame_count - 1
                if job.published_frame_count > 0
                else job.frame_start - 1
            )
            if job.chunk_end <= latest_safe_frame:
                return await self.update_job(
                    job.id,
                    state=JobState.RESUMABLE,
                    safe_stop_status=SafeStopStatus.NONE,
                    renderer_active=False,
                    watcher_active=False,
                    warning=(
                        "The requested chunk became validated and published before "
                        "its attempt stopped. Mission Control preserved it and did "
                        "not manufacture a retry by deleting authoritative frames."
                    ),
                    _allow_retry_restart=True,
                )
            await self.add_log(
                job.id,
                "info",
                (
                    f"Requeueing exact current chunk {job.chunk_start}-"
                    f"{job.chunk_end}; prior published chunks remain authoritative."
                ),
            )
            return await self._restart_exact_job_locked(
                job,
                RetryCurrentChunkRequest(
                    scene_sha256=job.identity.scene_sha256,
                    profile_sha256=job.identity.profile_sha256,
                    operator_confirmed=True,
                ),
                retry_failed=True,
            )

    async def _restart_exact_job_locked(
        self,
        job: JobRecord,
        request: (
            ResumeRequest
            | RetryFailedRenderRequest
            | RetryCurrentChunkRequest
        ),
        *,
        retry_failed: bool,
    ) -> JobRecord:
        operation = "retry" if retry_failed else "resume"
        if (
            request.scene_sha256 != job.identity.scene_sha256
            or request.profile_sha256 != job.identity.profile_sha256
        ):
            raise MissionControlError(
                409,
                f"{operation}_identity_mismatch",
                f"{operation.title()} identity does not match",
                f"{operation.title()} requires the exact original saved scene and profile hashes.",
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
                f"{operation}_profile_changed",
                "Saved profile changed",
                "The profile file bytes no longer match this render job.",
                "Restore the exact authorized saved profile before continuing.",
                job_id=job.id,
            )
        if (
            self.discovery.get_scene(job.identity.scene_id).sha256
            != job.identity.scene_sha256
        ):
            raise MissionControlError(
                409,
                f"{operation}_scene_changed",
                "Approved scene changed",
                "The scene file bytes no longer match this render job.",
                "Restore the exact approved scene before continuing.",
                job_id=job.id,
            )
        authorized, issues, token = validate_authorization_record(
            profile_path,
            scene_path,
            profile_payload,
            enabled_output_variant_ids=(
                list(job.identity.enabled_output_variant_ids)
                if job.identity.enabled_output_variant_ids
                else None
            ),
        )
        if not authorized:
            raise MissionControlError(
                409,
                f"{operation}_authorization_invalid",
                "Authorization is invalid",
                "The exact authorization record no longer validates.",
                "Authorize the exact scene and profile again.",
                context={"issues": issues},
                job_id=job.id,
            )
        restart_changes: dict[str, object] = {}
        if job.renderer == RendererKind.PRODUCTION:
            output = self.outputs.inspect(
                job.identity.output_directory,
                profile_id=job.identity.profile_id,
                scene_id=job.identity.scene_id,
            )
            if not output.usable:
                raise MissionControlError(
                    409,
                    f"{operation}_output_incompatible",
                    "Output cannot be restarted",
                    "The existing output manifest no longer matches this exact render identity.",
                    "Inspect the conflicting output details and choose the matching output.",
                    context={"issues": output.issues},
                    job_id=job.id,
                )
            snapshot = inspect_render_artifacts(job)
            if retry_failed and snapshot.disposition not in {"resumable", "missing"}:
                raise MissionControlError(
                    409,
                    "retry_output_not_resumable",
                    "Failed output cannot be retried",
                    "The authoritative artifacts are not a deterministic missing-frame resume set.",
                    "Inspect the manifest and create a new exact output when identity or frame indexes are invalid.",
                    context={
                        "artifactState": snapshot.disposition,
                        "reason": snapshot.reason,
                    },
                    job_id=job.id,
                )
            restart_changes.update(artifact_progress_changes(job, snapshot))
            self.production_renderer.cancel_stop(job)
        if retry_failed:
            telemetry_job = job
            if restart_changes and job.output_variants:
                routed_progress = self._output_variant_progress_changes(
                    job,
                    restart_changes,
                    datetime.now(UTC),
                )
                restart_changes.update(routed_progress)
                telemetry_job = job.model_copy(update=routed_progress)
            restart_changes.update(
                retry_count=job.retry_count + 1,
                output_variants=self._increment_attempt_telemetry(
                    telemetry_job,
                    retry_delta=1,
                ),
            )
        restarted = await self.update_job(
            job.id,
            **restart_changes,
            state=JobState.STARTING,
            process_id=None,
            orphaned=False,
            renderer_active=False,
            watcher_active=False,
            current_frame_started_at=None,
            safe_stop_status=SafeStopStatus.NONE,
            error=None,
            warning=None,
            completed_at=None,
            _allow_retry_restart=job.state == JobState.RETRY_REQUESTED,
        )
        if job.renderer == RendererKind.FAKE:
            self.fake_renderer.start(
                self,
                restarted,
                FakeRenderOptions(total_frames=job.total_frame_count),
            )
        else:
            self.production_renderer.start(
                self,
                restarted,
                scene_path=scene_path,
                profile_path=profile_path,
                authorization_token=token,
            )
        return restarted

    def _validated_relative_frame_path(
        self,
        job: JobRecord,
        *,
        relative: str,
        expected_frame: int,
        output_variant_id: str | None,
        written_at: datetime | None,
    ) -> Path | None:
        variant = next(
            (
                item
                for item in job.output_variants
                if item.enabled and item.id == output_variant_id
            ),
            None,
        )
        if job.output_variants and variant is None:
            return None
        expected_width = (
            variant.width if variant is not None else job.identity.output_width
        )
        expected_height = (
            variant.height if variant is not None else job.identity.output_height
        )
        frames_root_relative = variant.frames_root if variant is not None else "frames"
        relative_parts = tuple(relative.split("/"))
        frames_root_parts = tuple(frames_root_relative.split("/"))
        if (
            "\\" in relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative_parts)
            or any(part in {"", ".", ".."} for part in frames_root_parts)
            or expected_frame < job.frame_start
            or expected_frame > job.frame_end
            or Path(relative).suffix.casefold() != ".png"
            or Path(relative).name != f"frame_{expected_frame:06d}.png"
        ):
            return None
        published_parent = relative_parts[:-1]
        checkpoint_parent = relative_parts[:-1]
        variant_root_parts = frames_root_parts[:-1]
        is_published_frame = published_parent == frames_root_parts
        is_inflight_frame = (
            len(checkpoint_parent) == len(variant_root_parts) + 3
            and checkpoint_parent[: len(variant_root_parts)] == variant_root_parts
            and checkpoint_parent[len(variant_root_parts)] == "checkpoints"
            and checkpoint_parent[len(variant_root_parts) + 1].startswith(
                ".inflight-"
            )
            and len(checkpoint_parent[len(variant_root_parts) + 1])
            > len(".inflight-")
            and checkpoint_parent[len(variant_root_parts) + 2] == "frames"
        )
        if not is_published_frame and not is_inflight_frame:
            return None
        root = Path(job.identity.output_directory)
        try:
            resolved_root = root.resolve(strict=True)
            unresolved_source = root / Path(*relative_parts)
            if unresolved_source.is_symlink():
                return None
            source = unresolved_source.resolve(strict=True)
            source.relative_to(resolved_root)
            if not source.is_file():
                return None
            before = source.stat()
            dimensions = self._png_dimensions(source)
            if not self._valid_png(source):
                return None
            after = source.stat()
            source_timestamp = datetime.fromtimestamp(before.st_mtime, tz=UTC)
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_size < 20
                or (
                    expected_width is not None
                    and dimensions[0] != expected_width
                )
                or (
                    expected_height is not None
                    and dimensions[1] != expected_height
                )
                or (
                    written_at is not None
                    and source_timestamp + timedelta(minutes=5) < written_at
                )
            ):
                return None
            return source
        except (OSError, ValueError):
            return None

    def validate_and_prepare_frame_event(
        self,
        job: JobRecord,
        event: RendererTelemetryEvent,
    ) -> bool:
        """Validate the exact completed frame before progress or preview publication."""

        if (
            event.event_type != "frame_written"
            or event.frame is None
            or event.artifact_relative_path is None
        ):
            return event.event_type != "frame_written"
        source = self._validated_relative_frame_path(
            job,
            relative=event.artifact_relative_path,
            expected_frame=event.frame,
            output_variant_id=event.output_variant_id,
            written_at=event.emitted_at,
        )
        if source is None:
            return False
        # Thumbnail publication is deliberately best-effort. The source frame remains
        # authoritative and a thumbnailer failure must never fail production.
        preview = self._preview_thumbnail(job, source)
        now = time.monotonic()
        variant_key = event.output_variant_id or "legacy"
        self._preview_cache[f"{job.id}:{variant_key}:{event.frame}"] = (now, preview)
        self._preview_cache[f"{job.id}:{variant_key}:latest"] = (now, preview)
        return True

    def _telemetry_artifact_path(
        self,
        job: JobRecord,
        *,
        frame: int | None = None,
        output_variant_id: str | None = None,
    ) -> Path | None:
        variant_id = output_variant_id
        if variant_id is None and job.output_variants:
            variant_id = job.active_variant_id
        variant = next(
            (
                item
                for item in job.output_variants
                if item.enabled and item.id == variant_id
            ),
            None,
        )
        if job.output_variants and variant is None:
            return None
        if variant is not None:
            relative = variant.progress.latest_frame_artifact
            expected_frame = variant.progress.latest_frame_artifact_frame
            written_at = variant.progress.latest_frame_written_at
        else:
            relative = job.latest_frame_artifact
            expected_frame = job.latest_preview_frame
            written_at = job.latest_preview_at
        if relative is None:
            return None
        if frame is not None and expected_frame != frame:
            return None
        if expected_frame is None:
            return None
        return self._validated_relative_frame_path(
            job,
            relative=relative,
            expected_frame=expected_frame,
            output_variant_id=variant_id,
            written_at=written_at,
        )

    def _variant_frame_path(
        self,
        job: JobRecord,
        output_variant_id: str,
        *,
        frame: int | None,
    ) -> Path | None:
        variant = next(
            (
                item
                for item in job.output_variants
                if item.enabled and item.id == output_variant_id
            ),
            None,
        )
        requested = frame if frame is not None else (
            variant.progress.latest_rendered_frame if variant is not None else None
        )
        if variant is None or requested is None:
            return None
        progress = variant.progress
        known_rendered = requested == progress.latest_rendered_frame
        known_safe = (
            progress.latest_safe_frame is not None
            and job.frame_start <= requested <= progress.latest_safe_frame
        )
        if not known_rendered and not known_safe:
            return None
        root = Path(job.identity.output_directory)
        try:
            frames_root = (
                root / Path(*variant.frames_root.split("/"))
            ).resolve(strict=True)
            frames_root.relative_to(root.resolve(strict=True))
            source = (frames_root / f"frame_{requested:06d}.png").resolve(strict=True)
            source.relative_to(frames_root)
            before = source.stat()
            if (
                not self._valid_png(source)
                or self._png_dimensions(source) != (variant.width, variant.height)
            ):
                return None
            after = source.stat()
            return (
                source
                if before.st_size == after.st_size
                and before.st_mtime_ns == after.st_mtime_ns
                else None
            )
        except (OSError, ValueError):
            return None

    def full_frame_path(
        self,
        job_id: str,
        *,
        frame: int | None = None,
        output_variant_id: str | None = None,
    ) -> Path | None:
        job = self.get_job(job_id)
        selected_variant_id = (
            output_variant_id
            if output_variant_id is not None
            else job.active_variant_id if job.output_variants else None
        )
        if selected_variant_id is not None:
            variant_source = self._variant_frame_path(
                job,
                selected_variant_id,
                frame=frame,
            )
            if variant_source is not None:
                return variant_source
            return self._telemetry_artifact_path(
                job,
                frame=frame,
                output_variant_id=selected_variant_id,
            )
        requested_frame = frame if frame is not None else job.latest_preview_frame
        if requested_frame is None or not job.frame_start <= requested_frame <= job.frame_end:
            return None
        inflight = self._telemetry_artifact_path(job, frame=requested_frame)
        if inflight is not None:
            return inflight
        published = (
            Path(job.identity.output_directory)
            / "frames"
            / f"frame_{requested_frame:06d}.png"
        )
        return published if self._valid_png(published) else None

    def preview_path(
        self,
        job_id: str,
        *,
        frame: int | None = None,
        output_variant_id: str | None = None,
    ) -> Path | None:
        job = self.get_job(job_id)
        selected_variant_id = (
            output_variant_id
            if output_variant_id is not None
            else job.active_variant_id if job.output_variants else None
        )
        cache_key = (
            f"{job_id}:{selected_variant_id or 'legacy'}:"
            f"{frame if frame is not None else 'latest'}"
        )
        if selected_variant_id is not None:
            variant_source = self._variant_frame_path(
                job,
                selected_variant_id,
                frame=frame,
            )
            if variant_source is None:
                variant_source = self._telemetry_artifact_path(
                    job,
                    frame=frame,
                    output_variant_id=selected_variant_id,
                )
            if variant_source is not None:
                preview = self._preview_thumbnail(job, variant_source)
                self._preview_cache[cache_key] = (time.monotonic(), preview)
                return preview
            self._preview_cache[cache_key] = (time.monotonic(), None)
            return None
        inflight = self._telemetry_artifact_path(job, frame=frame)
        if inflight is not None:
            preview = self._preview_thumbnail(job, inflight)
            self._preview_cache[cache_key] = (time.monotonic(), preview)
            return preview
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

        The source has passed bounded structural and stable-size validation.
        Published frames additionally have manifest hash validation. A failed or
        unavailable thumbnailer never replaces the complete source frame.
        """

        if job.renderer != RendererKind.PRODUCTION:
            return source
        ffmpeg = self._ffmpeg_path()
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
                    str(ffmpeg),
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
        _profile_path, profile_payload, profile_hash = self.discovery.profile_source(
            job.identity.profile_id
        )
        encoding = profile_payload.get("encoding")
        enabled_output_kinds: list[Literal["delivery", "master"]] = []
        if profile_hash == job.identity.profile_sha256 and isinstance(encoding, dict):
            delivery = encoding.get("delivery")
            master = encoding.get("master")
            if isinstance(delivery, dict) and delivery.get("enabled") is True:
                enabled_output_kinds.append("delivery")
            if isinstance(master, dict) and master.get("enabled") is True:
                enabled_output_kinds.append("master")
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
        encode_contract_ready = bool(enabled_output_kinds)
        return EncodeReadiness(
            job_id=job.id,
            ready=complete and ffmpeg and encode_contract_ready,
            frame_sequence_complete=complete,
            published_frames=published_frames,
            total_frames=job.total_frame_count,
            ffmpeg_available=ffmpeg,
            enabled_output_kinds=enabled_output_kinds,
            detail=(
                "The verified frame sequence is ready for local encode."
                if complete and ffmpeg and encode_contract_ready
                else (
                    "Encoding requires at least one enabled reviewed output kind."
                    if complete and ffmpeg
                    else "Encoding requires a complete published frame sequence and FFmpeg."
                )
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

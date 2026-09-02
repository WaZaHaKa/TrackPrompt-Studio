from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from ..analysis_archive import AnalysisArchiveRepository
from ..privacy import secure_private_directory, secure_private_file
from .assembly import execute_assembly
from .audio import AudioBindingError, StagedAudio, probe_audio, stage_audio_master
from .authorization import BatchAuthorization, authorization_phrase
from .continuity import derive_shot_seed, load_continuity_profile
from .contracts import CompiledReferenceImage, CompiledShot, ContractError, load_project_config
from .costs import PRICE_SNAPSHOT_DATE, estimate
from .exporter import export_davinci_package
from .gcp_veo import (
    ProviderError,
    ProviderRequestContext,
    VeoRestClient,
    build_request_payload,
    copy_gcs_uri,
    doctor,
    response_output_uris,
    save_operation_failure_diagnostic,
    upload_reference_image,
)
from .jsonio import atomic_write_json, read_json, sha256_file, sha256_json
from .media import probe, verify_generated_clip
from .mission_models import (
    VideoAnalysisDependencyState,
    VideoAnalysisDependencyView,
    VideoAnalysisSource,
    VideoArtifactSummary,
    VideoAudioBinding,
    VideoAudioSelection,
    VideoAudioSelectionError,
    VideoAuthorizationRequest,
    VideoCatalog,
    VideoChainReferenceRequest,
    VideoContentPackage,
    VideoDoctorCheck,
    VideoDoctorRequest,
    VideoDoctorView,
    VideoError,
    VideoGenerationEvent,
    VideoJobRecord,
    VideoJobState,
    VideoJobView,
    VideoPlanCreateRequest,
    VideoProfileSummary,
    VideoRequestPreview,
    VideoRetryRequest,
    VideoReviewRequest,
    VideoReviewState,
    VideoShotAttempt,
    VideoShotRecord,
    VideoShotState,
    VideoShotView,
    utc_now,
)
from .planning import compile_project_plan
from .timeline import editorial_export_files, load_edit_blueprint, resolve_timeline

_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_ACTIVE_STATES = {
    VideoJobState.SMOKE_SUBMITTED,
    VideoJobState.GENERATING,
    VideoJobState.PARTIAL,
    VideoJobState.ASSEMBLING,
}
_PROFILE_FILES = {
    "fast-1080p": "project-config.json",
    "quality-1080p": "project-config.quality-1080p.json",
    "quality-4k": "project-config.4k-optional.json",
}


def _probe_stream_layout(path: Path, *, ffprobe: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels",
            "-of",
            "json",
            "--",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if result.returncode != 0 or len(result.stdout) > 1_000_000:
        raise ContractError("ffprobe could not verify the rough-cut stream layout")
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or not isinstance(value.get("streams"), list):
        raise ContractError("ffprobe returned an invalid rough-cut stream layout")
    streams = cast(list[dict[str, Any]], value["streams"])
    video = [item for item in streams if item.get("codec_type") == "video"]
    audio = [item for item in streams if item.get("codec_type") == "audio"]
    return {"video": video, "audio": audio}


class VideoMissionStore(Protocol):
    def put_video_job(self, job: VideoJobRecord) -> None: ...
    def get_video_job(self, job_id: str) -> VideoJobRecord | None: ...
    def list_video_jobs(self, *, limit: int = 100) -> list[VideoJobRecord]: ...
    def append_video_event(self, event: VideoGenerationEvent) -> VideoGenerationEvent: ...


class AsyncVideoProvider(Protocol):
    async def submit(self, shot: CompiledShot, *, context: ProviderRequestContext) -> dict[str, Any]: ...
    async def fetch(
        self, *, model_id: str, operation_name: str, context: ProviderRequestContext
    ) -> dict[str, Any]: ...
    async def download(self, uri: str, destination: Path) -> None: ...
    async def upload_reference(self, source: Path, destination_uri: str, expected_sha256: str) -> None: ...


class _VeoProvider:
    def __init__(self, project_id: str, region: str, diagnostics_root: Path) -> None:
        self.client = VeoRestClient(
            project_id=project_id,
            region=region,
            diagnostics_root=diagnostics_root,
        )

    async def submit(self, shot: CompiledShot, *, context: ProviderRequestContext) -> dict[str, Any]:
        return await asyncio.to_thread(self.client.submit, shot, context=context)

    async def fetch(
        self, *, model_id: str, operation_name: str, context: ProviderRequestContext
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.client.fetch,
            model_id=model_id,
            operation_name=operation_name,
            context=context,
        )

    async def download(self, uri: str, destination: Path) -> None:
        await asyncio.to_thread(copy_gcs_uri, uri, destination)

    async def upload_reference(self, source: Path, destination_uri: str, expected_sha256: str) -> None:
        await asyncio.to_thread(
            upload_reference_image,
            source,
            destination_uri,
            expected_sha256=expected_sha256,
        )


ProviderFactory = Callable[[str, str], AsyncVideoProvider]


class VideoGenerationController:
    """Mission Control-owned video generation lifecycle.

    There is intentionally no independent scheduler or database here. Work is
    persisted through MissionControlStore and resumed by MissionControlService.
    """

    def __init__(
        self,
        *,
        repository_root: Path,
        state_root: Path,
        store: VideoMissionStore,
        notify_event: Callable[[int], Awaitable[None]],
        provider_factory: ProviderFactory | None = None,
        ffmpeg_path: Callable[[], Path | None] | None = None,
        ffprobe_path: Callable[[], Path | None] | None = None,
        poll_interval_seconds: float = 15.0,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.analysis_jobs_root = (state_root.parent / "jobs").resolve()
        self.runtime_root = (state_root.parent / "video-generation").resolve()
        self.analysis_archive = AnalysisArchiveRepository(state_root.parent)
        self.store = store
        self.notify_event = notify_event
        self.provider_factory = provider_factory or (
            lambda project_id, region: _VeoProvider(
                project_id,
                region,
                self.runtime_root / "provider-errors",
            )
        )
        self.ffmpeg_path = ffmpeg_path or (lambda: None)
        self.ffprobe_path = ffprobe_path or (lambda: None)
        self.poll_interval_seconds = max(0.01, poll_interval_seconds)
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    @property
    def content_root(self) -> Path:
        return self.repository_root / "video-projects"

    def _project_root(self, project_id: str) -> Path:
        root = (self.content_root / project_id).resolve()
        if root.parent != self.content_root.resolve() or not root.is_dir():
            raise self._error(404, "video_project_not_found", "Video content package was not found")
        return root

    def _job_root(self, job: VideoJobRecord) -> Path:
        root = (self.runtime_root / job.project_id / job.id).resolve()
        expected_parent = (self.runtime_root / job.project_id).resolve()
        if root.parent != expected_parent:
            raise self._error(422, "video_job_path_invalid", "Video job storage identity is invalid")
        return root

    @staticmethod
    def _error(status: int, code: str, summary: str, *, retryable: bool = False) -> Exception:
        from app.mission_control.errors import MissionControlError

        return MissionControlError(
            status,
            code,
            "Video generation could not continue",
            summary,
            "Review the exact plan, provider readiness, and shot status before retrying.",
            retryable=retryable,
        )

    @staticmethod
    def _bucket(value: str) -> str:
        normalized = value.removeprefix("gs://").strip("/")
        if not _BUCKET.fullmatch(normalized):
            raise VideoGenerationController._error(
                422,
                "gcs_bucket_invalid",
                "The GCS bucket name is invalid.",
            )
        return normalized

    @staticmethod
    def _analysis_id(analysis_job_id: str) -> str:
        try:
            return str(UUID(analysis_job_id))
        except ValueError as exc:
            raise VideoGenerationController._error(
                404,
                "analysis_not_found",
                "The selected analysis does not exist",
            ) from exc

    def _live_analysis_directory(self, analysis_job_id: str) -> Path | None:
        canonical = self._analysis_id(analysis_job_id)
        path = (self.analysis_jobs_root / canonical).resolve()
        if path.parent != self.analysis_jobs_root or not path.is_dir():
            return None
        return path

    def _analysis_artifact(self, analysis_job_id: str, artifact_kind: str) -> tuple[Path | None, str]:
        archived = self.analysis_archive.resolve_artifact(analysis_job_id, artifact_kind)
        if archived is not None:
            return archived, VideoAnalysisDependencyState.ARCHIVED_ANALYSIS.value
        live = self._live_analysis_directory(analysis_job_id)
        filename = {
            "story-plan": "story-plan.json",
            "shot-plan": "shot-plan.json",
            "analysis": "analysis.json",
        }.get(artifact_kind)
        if live is not None and filename is not None and (live / filename).is_file():
            return (live / filename).resolve(), VideoAnalysisDependencyState.LIVE_ANALYSIS.value
        return None, VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING.value

    def _analysis_source(self, analysis_job_id: str) -> tuple[Path | None, str]:
        archived = self.analysis_archive.resolve_source(analysis_job_id)
        if archived is not None:
            return archived, VideoAnalysisDependencyState.ARCHIVED_ANALYSIS.value
        live = self._live_analysis_directory(analysis_job_id)
        if live is not None and (live / "source.bin").is_file():
            return (live / "source.bin").resolve(), VideoAnalysisDependencyState.LIVE_ANALYSIS.value
        return None, VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING.value

    def catalog(self) -> VideoCatalog:
        analyses_by_id: dict[str, VideoAnalysisSource] = {}
        archived_entries, _ = self.analysis_archive.list(limit=200, sort="created_desc")
        for entry in archived_entries:
            if entry["status"] == "explicitly_deleted":
                continue
            analysis_id = str(entry["analysisId"])
            analyses_by_id[analysis_id] = VideoAnalysisSource(
                analysis_job_id=analysis_id,
                display_name=str(entry["displayName"]),
                story_plan_available=bool(entry["storyPlanAvailable"]),
                shot_plan_available=bool(entry["shotPlanAvailable"]),
                retained_audio_available=bool(entry["retainedAudioAvailable"]),
                created_at=datetime.fromisoformat(str(entry["createdAt"])),
                duration_seconds=entry["durationSeconds"],
                archived=entry["archivedAt"] is not None,
                archive_health=str(entry["archiveHealth"]),
            )
        if self.analysis_jobs_root.is_dir():
            for path in sorted(
                (item for item in self.analysis_jobs_root.iterdir() if item.is_dir()),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            ):
                try:
                    canonical = str(UUID(path.name))
                except ValueError:
                    continue
                story = (path / "story-plan.json").is_file()
                shots = (path / "shot-plan.json").is_file()
                analysis = (path / "analysis.json").is_file()
                if not (story or shots or analysis):
                    continue
                if canonical in analyses_by_id:
                    continue
                analyses_by_id[canonical] = (
                    VideoAnalysisSource(
                        analysis_job_id=canonical,
                        display_name=f"Analysis {canonical[:8]}",
                        story_plan_available=story,
                        shot_plan_available=shots,
                        retained_audio_available=(path / "source.bin").is_file(),
                        created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                    )
                )

        packages: list[VideoContentPackage] = []
        for project_path in sorted(self.content_root.glob("*/project-config.json")):
            project_root = project_path.parent
            if project_root.name.startswith("_"):
                continue
            profiles: list[VideoProfileSummary] = []
            for profile_id, file_name in _PROFILE_FILES.items():
                config_path = project_root / file_name
                if not config_path.is_file():
                    continue
                config = load_project_config(config_path)
                profile = config.selected_profile()
                costs = estimate(profile, len(config.required_shot_ids), config.retry_reserve_factor)
                available = not (
                    profile.resolution == "4k"
                    and profile.model_id in {"veo-3.1-generate-001", "veo-3.1-fast-generate-001"}
                )
                profiles.append(
                    VideoProfileSummary(
                        id=profile_id,
                        display_name={
                            "fast-1080p": "Veo 3.1 Fast · 1080p",
                            "quality-1080p": "Veo 3.1 Quality · 1080p",
                            "quality-4k": "Veo 3.1 Quality · 4K (optional)",
                        }[profile_id],
                        model_id=profile.model_id,
                        resolution=profile.resolution,
                        duration_seconds=profile.duration_seconds,
                        fps=profile.fps,
                        sample_count=profile.sample_count,
                        default=profile_id == "fast-1080p",
                        optional=profile_id != "fast-1080p",
                        base_estimated_usd=float(costs.base_usd),
                        conservative_estimated_usd=float(costs.conservative_usd),
                        max_spend_usd=config.max_spend_usd,
                        available=available,
                        availability_note=(
                            None
                            if available
                            else "Current GA Vertex Veo 3.1 endpoints support 720p/1080p; 4K remains optional and disabled until a supported model contract is configured."
                        ),
                    )
                )
            config = load_project_config(project_path)
            continuity = load_continuity_profile(project_root / "continuity-profile.json")
            packages.append(
                VideoContentPackage(
                    project_id=config.project_id,
                    title=config.title,
                    shot_count=len(config.required_shot_ids),
                    default_master_seed=continuity.master_seed,
                    profiles=tuple(profiles),
                )
            )
        return VideoCatalog(
            analyses=tuple(analyses_by_id.values()),
            packages=tuple(packages),
            pricing_snapshot_date=PRICE_SNAPSHOT_DATE,
        )

    def _publish_dependency_artifact(
        self,
        source: Path,
        destination: Path,
        *,
        expected_sha256: str | None,
    ) -> dict[str, Any]:
        digest = sha256_file(source)
        if expected_sha256 is not None and digest != expected_sha256:
            raise self._error(
                422,
                "analysis_dependency_hash_mismatch",
                "An analysis dependency no longer matches the exact compiled plan",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        secure_private_directory(destination.parent)
        if destination.is_file():
            if sha256_file(destination) != digest:
                raise self._error(
                    409,
                    "analysis_dependency_conflict",
                    "The video job already contains a conflicting dependency snapshot",
                )
        else:
            temporary = destination.with_name(f".{destination.name}.partial-{uuid4().hex}")
            try:
                shutil.copyfile(source, temporary)
                secure_private_file(temporary)
                if sha256_file(temporary) != digest:
                    raise self._error(
                        500,
                        "analysis_dependency_copy_failed",
                        "The analysis dependency snapshot could not be verified",
                    )
                os.replace(temporary, destination)
                secure_private_file(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return {
            "available": True,
            "relativePath": destination.name,
            "sha256": digest,
            "byteSize": destination.stat().st_size,
        }

    def _snapshot_analysis_dependencies(
        self,
        job: VideoJobRecord,
        *,
        story_path: Path | None,
        shot_path: Path | None,
        source_state: str,
        audio_path: Path | None,
    ) -> VideoJobRecord:
        root = self._job_root(job) / "inputs" / "analysis"
        root.mkdir(parents=True, exist_ok=True)
        secure_private_directory(root)
        source_artifacts = cast(dict[str, Any], job.plan.get("sourceArtifacts", {}))
        story = (
            self._publish_dependency_artifact(
                story_path,
                root / "story-plan.json",
                expected_sha256=cast(str | None, source_artifacts.get("storyPlanSha256")),
            )
            if story_path is not None
            else {"available": False}
        )
        shots = (
            self._publish_dependency_artifact(
                shot_path,
                root / "shot-plan.json",
                expected_sha256=cast(str | None, source_artifacts.get("shotPlanSha256")),
            )
            if shot_path is not None
            else {"available": False}
        )
        audio = (
            {
                "available": True,
                "sha256": sha256_file(audio_path),
                "byteSize": audio_path.stat().st_size,
            }
            if audio_path is not None
            else {"available": False}
        )
        manifest = {
            "schemaVersion": "1.0.0",
            "analysisId": job.analysis_job_id,
            "providerPlanDigest": job.plan_digest,
            "sourceState": source_state,
            "createdAt": utc_now().isoformat(),
            "artifacts": {"storyPlan": story, "shotPlan": shots},
            "audioMaster": audio,
            "optionalArtifactPolicy": "missing-shot-plan-uses-normalized-chapter-timing",
        }
        manifest_path = root / "analysis-dependency-manifest.json"
        atomic_write_json(manifest_path, manifest)
        self.analysis_archive.register_dependency(
            job.analysis_job_id,
            dependent_kind="video-generation",
            dependent_id=job.id,
            snapshot_complete=True,
        )
        return job.model_copy(
            update={
                "analysis_dependency_state": VideoAnalysisDependencyState.SNAPSHOTTED,
                "dependency_manifest_path": str(manifest_path),
            }
        )

    async def create_plan(self, request: VideoPlanCreateRequest) -> VideoJobView:
        analysis_directory = self._live_analysis_directory(request.analysis_job_id)
        analysis_entry = self.analysis_archive.get(request.analysis_job_id)
        analysis_path, analysis_source_state = self._analysis_artifact(
            request.analysis_job_id, "analysis"
        )
        if analysis_directory is None and analysis_entry is None and analysis_path is None:
            raise self._error(404, "analysis_not_found", "The selected analysis does not exist")
        project_root = self._project_root(request.project_id)
        bucket = self._bucket(request.gcs_bucket)
        config_path = project_root / _PROFILE_FILES[request.profile_id]
        story_path, story_source_state = self._analysis_artifact(
            request.analysis_job_id, "story-plan"
        )
        shot_path, shot_source_state = self._analysis_artifact(
            request.analysis_job_id, "shot-plan"
        )
        retained_source, audio_source_state = self._analysis_source(request.analysis_job_id)
        source_state = next(
            (
                value
                for value in (story_source_state, shot_source_state, audio_source_state, analysis_source_state)
                if value != VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING.value
            ),
            VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING.value,
        )
        initial_audio_path: Path | None = None
        initial_audio_source: str | None = None
        reference_image_path: Path | None = None
        if request.audio_path:
            initial_audio_path = Path(request.audio_path).expanduser().resolve()
            initial_audio_source = "local-selection"
        elif retained_source is not None:
            initial_audio_path = retained_source
            initial_audio_source = "analysis-retained"
        if initial_audio_path is not None and not initial_audio_path.is_file():
            raise self._error(422, "audio_master_missing", "The selected local audio master is unavailable")
        if request.reference_image_path:
            reference_image_path = Path(request.reference_image_path).expanduser().resolve()
            if not reference_image_path.is_file():
                raise self._error(
                    422,
                    "continuity_reference_missing",
                    "The selected local continuity reference image is unavailable",
                )
        try:
            plan = await asyncio.to_thread(
                compile_project_plan,
                project_config_path=config_path,
                creative_bible_path=project_root / "creative-bible.json",
                shot_bank_path=project_root / "shot-bank.json",
                gcs_bucket=f"gs://{bucket}",
                analysis_job_id=request.analysis_job_id,
                # Audio is a local finishing input. It must never alter the
                # already-reviewed provider-generation digest.
                audio_master_path=None,
                story_plan_path=story_path,
                shot_plan_path=shot_path,
                continuity_profile_path=project_root / "continuity-profile.json",
                master_seed=request.master_seed,
                seed_locked=request.seed_locked,
                reference_image_path=reference_image_path,
            )
        except (ContractError, OSError, ValueError) as exc:
            raise self._error(422, "video_plan_invalid", str(exc)) from exc
        now = utc_now()
        job_id = str(uuid4())
        record = VideoJobRecord(
            id=job_id,
            analysis_job_id=request.analysis_job_id,
            project_id=request.project_id,
            title=plan.title,
            state=VideoJobState.PLANNED,
            plan_digest=plan.plan_digest,
            plan=plan.to_dict(),
            gcp_project_id=request.gcp_project_id,
            gcs_bucket=bucket,
            audio_path=None,
            audio_binding=None,
            reference_assets=(
                {
                    plan.shots[0].first_frame_reference.asset_id: {
                        **plan.shots[0].first_frame_reference.to_dict(),
                        "localPath": str(reference_image_path),
                    }
                }
                if reference_image_path is not None
                and plan.shots
                and plan.shots[0].first_frame_reference is not None
                else {}
            ),
            shots=tuple(
                VideoShotRecord(
                    shot_id=shot.shot_id,
                    chapter_id=shot.chapter_id,
                    order=shot.order,
                    title=shot.title,
                    prompt=shot.prompt,
                    negative_prompt=shot.negative_prompt,
                    seed=shot.seed,
                    required=shot.required,
                    estimated_cost_usd=shot.estimated_cost_usd,
                )
                for shot in plan.shots
            ),
            created_at=now,
            updated_at=now,
        )
        root = self._job_root(record)
        atomic_write_json(root / "plan.json", record.plan)
        for shot in plan.shots:
            atomic_write_json(root / "request-preview" / f"{shot.shot_id}.json", build_request_payload(shot))
        record = self._snapshot_analysis_dependencies(
            record,
            story_path=story_path,
            shot_path=shot_path,
            source_state=source_state,
            audio_path=initial_audio_path,
        )
        await self._save(record)
        if initial_audio_path is not None and initial_audio_source is not None:
            selection = await self.bind_audio(
                record.id,
                initial_audio_path,
                source=cast(Any, initial_audio_source),
            )
            if not selection.selected:
                assert selection.error is not None
                raise self._error(422, selection.error.code, selection.error.message)
        return self.get(record.id)

    def get(self, job_id: str) -> VideoJobView:
        return self._view(self._record(job_id))

    def jobs(self) -> list[VideoJobView]:
        return [self._view(self._record(item.id)) for item in self.store.list_video_jobs(limit=100)]

    @staticmethod
    def _audio_selection(job: VideoJobRecord) -> VideoAudioSelection:
        binding = job.audio_binding
        if binding is None:
            return VideoAudioSelection(selected=False, verified=False)
        finishing = Path(binding.finishing_path)
        if not finishing.is_file():
            return VideoAudioSelection(
                selected=False,
                verified=False,
                error=VideoAudioSelectionError(
                    code="audio_artifact_missing",
                    message="The bound private finishing audio is no longer available.",
                ),
            )
        return VideoAudioSelection(
            selected=True,
            verified=True,
            source=binding.source,
            audio_artifact_id=binding.audio_artifact_id,
            display_name=binding.display_name,
            duration_seconds=binding.duration_seconds,
            sample_rate_hz=binding.sample_rate_hz,
            channels=binding.channels,
            container=binding.container,
            audio_codec=binding.audio_codec,
            sha256=binding.sha256,
            finishing_sha256=binding.finishing_sha256,
            analysis_job_id=binding.analysis_job_id,
            bound_video_job_id=binding.bound_video_job_id,
            selected_at=binding.selected_at,
        )

    @staticmethod
    def _audio_failure(exc: AudioBindingError) -> VideoAudioSelection:
        return VideoAudioSelection(
            selected=False,
            verified=False,
            error=VideoAudioSelectionError(code=exc.code, message=exc.safe_message),
        )

    @staticmethod
    def _binding_from_staged(
        job: VideoJobRecord,
        staged: StagedAudio,
        *,
        source: Any,
    ) -> VideoAudioBinding:
        return VideoAudioBinding(
            source=source,
            audio_artifact_id=f"audio-{staged.source.sha256[:20]}",
            display_name=staged.display_name,
            source_runtime_path=str(staged.source.path),
            artifact_path=str(staged.artifact.path),
            finishing_path=str(staged.finishing.path),
            sha256=staged.source.sha256,
            finishing_sha256=staged.finishing.sha256,
            container=staged.source.container,
            audio_codec=staged.source.codec,
            sample_rate_hz=staged.source.sample_rate_hz,
            channels=staged.source.channels,
            duration_seconds=staged.source.duration_seconds,
            analysis_job_id=job.analysis_job_id,
            bound_video_job_id=job.id,
            selected_at=utc_now(),
        )

    def audio_selection(self, job_id: str) -> VideoAudioSelection:
        return self._audio_selection(self._record(job_id))

    def _archive_audio_binding(self, job: VideoJobRecord) -> None:
        current = self._job_root(job) / "audio" / "audio-binding.json"
        if not current.is_file():
            return
        value = read_json(current)
        if not isinstance(value, dict):
            return
        digest = str(value.get("sha256", "unknown"))
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            digest = sha256_json(value)
        history = current.parent / "binding-history" / f"{digest}-{uuid4().hex[:8]}.json"
        atomic_write_json(history, value)
        # The history copy is the recoverable audit record. Removing only the
        # active receipt prevents a cleared association from being restored on
        # the next restart; the immutable audio artifacts remain untouched.
        current.unlink(missing_ok=True)

    def _invalidate_finishing_outputs(self, job: VideoJobRecord) -> VideoJobRecord:
        root = self._job_root(job)
        davinci = root / "davinci"
        if davinci.is_dir():
            suffix = job.local_edit_digest or "unbound"
            timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
            archive = root / "finishing-history" / f"{timestamp}-{suffix[:16]}"
            archive.parent.mkdir(parents=True, exist_ok=True)
            if archive.exists():
                archive = archive.with_name(f"{archive.name}-{uuid4().hex[:8]}")
            os.replace(davinci, archive)
        state = (
            VideoJobState.REVIEW_READY
            if job.state
            in {
                VideoJobState.TIMELINE_READY,
                VideoJobState.EXPORTED,
                VideoJobState.COMPLETE,
            }
            else job.state
        )
        return job.model_copy(
            update={
                "state": state,
                "timeline_path": None,
                "export_root": None,
                "preview_path": None,
                "local_edit_digest": None,
                "error": None,
            }
        )

    def _expected_audio_identity(self, job: VideoJobRecord) -> tuple[str | None, float | None]:
        expected_sha256: str | None = None
        expected_duration: float | None = None
        manifest_path = Path(job.dependency_manifest_path) if job.dependency_manifest_path else None
        if manifest_path is not None and manifest_path.is_file():
            value = read_json(manifest_path)
            if isinstance(value, dict):
                audio = value.get("audioMaster")
                if isinstance(audio, dict):
                    candidate = audio.get("sha256")
                    if isinstance(candidate, str) and re.fullmatch(r"[0-9a-f]{64}", candidate):
                        expected_sha256 = candidate
                    duration = audio.get("durationSeconds")
                    if isinstance(duration, (int, float)) and duration > 0:
                        expected_duration = float(duration)
        source_artifacts = cast(dict[str, Any], job.plan.get("sourceArtifacts", {}))
        candidate = source_artifacts.get("audioMasterSha256")
        if expected_sha256 is None and isinstance(candidate, str) and re.fullmatch(r"[0-9a-f]{64}", candidate):
            expected_sha256 = candidate
        if job.audio_binding is not None:
            expected_sha256 = expected_sha256 or job.audio_binding.sha256
            expected_duration = expected_duration or job.audio_binding.duration_seconds
        return expected_sha256, expected_duration

    async def bind_audio(
        self,
        job_id: str,
        source_path: Path,
        *,
        source: Any,
        display_name: str | None = None,
        accept_local_delivery_revision: bool = False,
        confirmation: str | None = None,
    ) -> VideoAudioSelection:
        async with self._lock:
            job = self._record(job_id)
            if job.state is VideoJobState.ASSEMBLING:
                return VideoAudioSelection(
                    selected=False,
                    verified=False,
                    error=VideoAudioSelectionError(
                        code="audio_binding_busy",
                        message="Wait for the active local assembly to finish before replacing its audio.",
                    ),
                )
            if source not in {"analysis-retained", "local-selection", "legacy-local-path"}:
                return VideoAudioSelection(
                    selected=False,
                    verified=False,
                    error=VideoAudioSelectionError(
                        code="audio_source_invalid",
                        message="The selected audio source type is unsupported.",
                    ),
                )
            ffmpeg = self.ffmpeg_path()
            ffprobe = self.ffprobe_path()
            try:
                staged = await asyncio.to_thread(
                    stage_audio_master,
                    source_path,
                    artifact_root=self._job_root(job) / "audio" / "artifacts",
                    display_name=display_name,
                    ffmpeg=str(ffmpeg) if ffmpeg else None,
                    ffprobe=str(ffprobe) if ffprobe else None,
                )
            except AudioBindingError as exc:
                return self._audio_failure(exc)
            binding = self._binding_from_staged(job, staged, source=source)
            if (
                job.audio_binding is not None
                and job.audio_binding.sha256 == binding.sha256
                and job.audio_binding.duration_seconds == binding.duration_seconds
                and Path(job.audio_binding.finishing_path).is_file()
            ):
                return self._audio_selection(job)
            expected_sha256, expected_duration = self._expected_audio_identity(job)
            mismatch = expected_sha256 is not None and expected_sha256 != binding.sha256
            confirmation_phrase = (
                f"CONFIRM LOCAL DELIVERY AUDIO {expected_sha256[:12]} TO {binding.sha256[:12]}"
                if mismatch and expected_sha256 is not None
                else None
            )
            if mismatch and (
                not accept_local_delivery_revision
                or confirmation_phrase is None
                or confirmation != confirmation_phrase
            ):
                return VideoAudioSelection(
                    selected=False,
                    verified=False,
                    error=VideoAudioSelectionError(
                        code="audio_hash_mismatch_confirmation_required",
                        message="The selected audio differs from the original local delivery identity. Confirm a local-only delivery revision to continue without changing the paid Veo plan.",
                        expected_hash_prefix=expected_sha256[:12] if expected_sha256 else None,
                        selected_hash_prefix=binding.sha256[:12],
                        expected_duration_seconds=expected_duration,
                        selected_duration_seconds=binding.duration_seconds,
                        confirmation_phrase=confirmation_phrase,
                    ),
                )
            previous_plan_digest = job.plan_digest
            previous_authorization = job.authorization
            previous_reserved = job.reserved_cost_usd
            self._archive_audio_binding(job)
            delivery_revision = (
                {
                    "schemaVersion": "1.0.0",
                    "kind": "local-delivery-audio-revision",
                    "expectedSha256": expected_sha256,
                    "selectedSha256": binding.sha256,
                    "expectedDurationSeconds": expected_duration,
                    "selectedDurationSeconds": binding.duration_seconds,
                    "confirmedAt": utc_now().isoformat(),
                    "providerPlanDigest": job.plan_digest,
                }
                if mismatch
                else job.local_delivery_revision
            )
            updated = self._invalidate_finishing_outputs(job).model_copy(
                update={
                    "audio_path": binding.finishing_path,
                    "audio_binding": binding,
                    "local_delivery_revision": delivery_revision,
                    "updated_at": utc_now(),
                }
            )
            if (
                updated.plan_digest != previous_plan_digest
                or updated.authorization != previous_authorization
                or updated.reserved_cost_usd != previous_reserved
            ):
                raise RuntimeError("local audio binding altered provider generation identity")
            atomic_write_json(
                self._job_root(updated) / "audio" / "audio-binding.json",
                binding.model_dump(mode="json", by_alias=True),
            )
            atomic_write_json(
                self._job_root(updated) / "audio" / "audio-selection.json",
                binding.model_dump(mode="json", by_alias=True),
            )
            atomic_write_json(
                self._job_root(updated)
                / "repair"
                / f"audio-rebind-{binding.sha256[:16]}.json",
                {
                    "schemaVersion": "1.0.0",
                    "kind": "local-audio-rebind",
                    "recordedAt": utc_now().isoformat(),
                    "providerPlanDigest": updated.plan_digest,
                    "expectedSha256": expected_sha256,
                    "selectedSha256": binding.sha256,
                    "exactHashMatch": not mismatch,
                    "localDeliveryRevision": mismatch,
                    "reservedCostUsdBefore": previous_reserved,
                    "reservedCostUsdAfter": updated.reserved_cost_usd,
                    "providerRequestSubmitted": False,
                },
            )
            saved = await self._save(updated)
            return self._audio_selection(saved)

    async def bind_retained_audio(self, job_id: str) -> VideoAudioSelection:
        job = self._record(job_id)
        source, _ = self._analysis_source(job.analysis_job_id)
        if source is None:
            return VideoAudioSelection(
                selected=False,
                verified=False,
                error=VideoAudioSelectionError(
                    code="retained_analysis_audio_missing",
                    message="This analysis no longer retains its original private audio artifact.",
                ),
            )
        return await self.bind_audio(
            job_id,
            source,
            source="analysis-retained",
            display_name=f"analysis-{job.analysis_job_id[:8]}-retained-audio",
        )

    async def clear_audio(self, job_id: str) -> VideoAudioSelection:
        async with self._lock:
            job = self._record(job_id)
            if job.state is VideoJobState.ASSEMBLING:
                return VideoAudioSelection(
                    selected=False,
                    verified=False,
                    error=VideoAudioSelectionError(
                        code="audio_binding_busy",
                        message="Wait for the active local assembly to finish before clearing its audio.",
                    ),
                )
            self._archive_audio_binding(job)
            updated = self._invalidate_finishing_outputs(job).model_copy(
                update={"audio_path": None, "audio_binding": None, "updated_at": utc_now()}
            )
            atomic_write_json(
                self._job_root(updated) / "audio" / "audio-selection.json",
                {
                    "schemaVersion": "1.0.0",
                    "selected": False,
                    "verified": False,
                    "clearedAt": utc_now().isoformat(),
                    "videoJobId": updated.id,
                },
            )
            await self._save(updated)
            return VideoAudioSelection(selected=False, verified=False)

    def request_preview(self, job_id: str) -> VideoRequestPreview:
        job = self._record(job_id)
        requests = tuple(
            cast(dict[str, Any], read_json(self._job_root(job) / "request-preview" / f"{shot.shot_id}.json"))
            for shot in job.shots
        )
        return VideoRequestPreview(job_id=job.id, plan_digest=job.plan_digest, requests=requests)

    async def authorize(self, job_id: str, request: VideoAuthorizationRequest) -> VideoJobView:
        async with self._lock:
            job = self._record(job_id)
            self._require_current_request_contract(job)
            if job.state not in {VideoJobState.PLANNED, VideoJobState.AUTHORIZED}:
                raise self._error(
                    409, "video_plan_not_authorizable", "Only an unchanged planned batch can be authorized"
                )
            maximum = float(cast(dict[str, Any], job.plan["cost"])["maxSpendUsd"])
            try:
                authorization = BatchAuthorization.create(
                    project_id=job.project_id,
                    plan_digest=job.plan_digest,
                    max_spend_usd=maximum,
                    confirmation=request.confirmation,
                    valid_hours=24,
                )
            except ContractError as exc:
                raise self._error(422, "video_authorization_invalid", str(exc)) from exc
            updated = job.model_copy(
                update={
                    "authorization": authorization.to_dict(),
                    "state": VideoJobState.AUTHORIZED,
                    "error": None,
                    "updated_at": utc_now(),
                }
            )
            self._archive_authorization_receipt(updated)
            atomic_write_json(self._job_root(updated) / "authorization.json", authorization.to_dict())
            return self._view(await self._save(updated))

    async def start(self, job_id: str) -> VideoJobView:
        async with self._lock:
            job = self._record(job_id)
            self._authorization(job, next_cost=0)
            if (
                job.state
                not in {
                    VideoJobState.AUTHORIZED,
                    VideoJobState.PARTIAL,
                    VideoJobState.BLOCKED_PROVIDER_ACCESS,
                    VideoJobState.BLOCKED_PROVIDER_QUOTA,
                    VideoJobState.FAILED,
                }
                and job.state not in _ACTIVE_STATES
            ):
                raise self._error(409, "video_job_not_startable", "The video batch is not ready to start")
            if job.state not in _ACTIVE_STATES:
                job = job.model_copy(
                    update={
                        "state": VideoJobState.SMOKE_SUBMITTED,
                        "started_at": job.started_at or utc_now(),
                        "error": None,
                        "updated_at": utc_now(),
                    }
                )
                job = await self._save(job)
            self._schedule(job.id)
            return self._view(job)

    async def resume(self, job_id: str) -> VideoJobView:
        return await self.start(job_id)

    async def cancel(self, job_id: str) -> VideoJobView:
        async with self._lock:
            job = self._record(job_id)
            task = self._tasks.get(job_id)
            if task is not None and not task.done():
                task.cancel()
            cancelled = job.model_copy(
                update={
                    "state": VideoJobState.CANCELLED,
                    "error": None,
                    "updated_at": utc_now(),
                }
            )
            return self._view(await self._save(cancelled))

    async def retry(
        self,
        job_id: str,
        shot_id: str,
        request: VideoRetryRequest,
    ) -> VideoJobView:
        async with self._lock:
            job = self._record(job_id)
            shot = self._shot(job, shot_id)
            latest = shot.latest_attempt
            if latest is None or latest.state not in {
                VideoShotState.FAILED,
                VideoShotState.FILTERED,
                VideoShotState.VERIFIED,
            }:
                raise self._error(409, "video_shot_not_retryable", "This shot is not in a retryable state")
            if request.mode == "new_variation":
                return self._view(await self._prepare_new_variation(job, shot))
            self._authorization(job, next_cost=shot.estimated_cost_usd)
            shot = shot.model_copy(
                update={
                    "review_state": VideoReviewState.PENDING,
                    "accepted_attempt_id": None,
                    "retry_requested": True,
                }
            )
            job = self._replace_shot(job, shot).model_copy(
                update={"state": VideoJobState.PARTIAL, "error": None, "updated_at": utc_now()}
            )
            job = await self._save(job)
            self._schedule(job.id)
            return self._view(job)

    async def _prepare_new_variation(
        self,
        job: VideoJobRecord,
        shot: VideoShotRecord,
    ) -> VideoJobRecord:
        self._require_current_request_contract(job)
        plan = cast(dict[str, Any], json.loads(json.dumps(job.plan)))
        plan_shots = cast(list[dict[str, Any]], plan["shots"])
        planned_shot = next(item for item in plan_shots if item["shotId"] == shot.shot_id)
        variation_index = int(planned_shot.get("variationIndex", 0)) + 1
        continuity = cast(dict[str, Any], plan.get("continuity", {}))
        master_seed = int(continuity["masterSeed"])
        group_ids = tuple(str(item) for item in planned_shot.get("continuityGroupIds", []))
        seed = derive_shot_seed(
            master_seed=master_seed,
            project_id=job.project_id,
            continuity_group_ids=group_ids,
            shot_id=shot.shot_id,
            variation_index=variation_index,
        )
        planned_shot["variationIndex"] = variation_index
        planned_shot["seed"] = seed
        plan.pop("planDigest", None)
        digest = sha256_json(plan)
        plan["planDigest"] = digest
        revised_shot = shot.model_copy(
            update={
                "seed": seed,
                "review_state": VideoReviewState.PENDING,
                "accepted_attempt_id": None,
                "retry_requested": True,
            }
        )
        self._archive_authorization_receipt(job)
        updated = self._replace_shot(job, revised_shot).model_copy(
            update={
                "plan": plan,
                "plan_digest": digest,
                "authorization": None,
                "state": VideoJobState.PLANNED,
                "error": None,
                "updated_at": utc_now(),
            }
        )
        self._persist_plan_revision(job, updated)
        return await self._save(updated)

    def _persist_plan_revision(self, previous: VideoJobRecord, updated: VideoJobRecord) -> None:
        root = self._job_root(updated)
        old_plan = root / "plan.json"
        history = root / "plan-history" / f"{previous.plan_digest}.json"
        if old_plan.is_file() and not history.exists():
            value = read_json(old_plan)
            if isinstance(value, dict):
                atomic_write_json(history, value)
        previous_preview_root = root / "request-preview"
        history_preview_root = root / "request-preview-history" / previous.plan_digest
        for old_shot in previous.shots:
            preview = previous_preview_root / f"{old_shot.shot_id}.json"
            archived = history_preview_root / preview.name
            if preview.is_file() and not archived.exists():
                value = read_json(preview)
                if isinstance(value, dict):
                    atomic_write_json(archived, value)
        atomic_write_json(root / "plan.json", updated.plan)
        for revised in updated.shots:
            atomic_write_json(
                root / "request-preview" / f"{revised.shot_id}.json",
                build_request_payload(self._compiled(updated, revised.shot_id)),
            )

    async def review(self, job_id: str, shot_id: str, request: VideoReviewRequest) -> VideoJobView:
        async with self._lock:
            job = self._record(job_id)
            shot = self._shot(job, shot_id)
            latest = shot.latest_attempt
            if latest is None or latest.state is not VideoShotState.VERIFIED:
                raise self._error(
                    409, "video_shot_not_reviewable", "Only a technically verified clip can be reviewed"
                )
            updated_shot = shot.model_copy(
                update={
                    "review_state": request.decision,
                    "review_note": request.note,
                    "accepted_attempt_id": latest.id
                    if request.decision is VideoReviewState.ACCEPTED
                    else None,
                }
            )
            return self._view(await self._save(self._replace_shot(job, updated_shot)))

    def _optional_analysis_artifact_for_job(
        self,
        job: VideoJobRecord,
        artifact_kind: str,
    ) -> tuple[Path | None, str]:
        filename = {
            "story-plan": "story-plan.json",
            "shot-plan": "shot-plan.json",
        }[artifact_kind]
        snapshot = self._job_root(job) / "inputs" / "analysis" / filename
        if snapshot.is_file():
            manifest_path = Path(job.dependency_manifest_path) if job.dependency_manifest_path else None
            manifest = read_json(manifest_path) if manifest_path and manifest_path.is_file() else None
            if isinstance(manifest, dict):
                artifacts = manifest.get("artifacts")
                key = "storyPlan" if artifact_kind == "story-plan" else "shotPlan"
                identity = artifacts.get(key) if isinstance(artifacts, dict) else None
                expected = identity.get("sha256") if isinstance(identity, dict) else None
                if isinstance(expected, str) and sha256_file(snapshot) == expected:
                    return snapshot, VideoAnalysisDependencyState.SNAPSHOTTED.value
        archived = self.analysis_archive.resolve_artifact(job.analysis_job_id, artifact_kind)
        if archived is not None:
            return archived, VideoAnalysisDependencyState.ARCHIVED_ANALYSIS.value
        live = self._live_analysis_directory(job.analysis_job_id)
        if live is not None and (live / filename).is_file():
            return (live / filename).resolve(), VideoAnalysisDependencyState.LIVE_ANALYSIS.value
        return None, VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING.value

    async def repair_legacy_dependency(self, job_id: str) -> VideoJobView:
        async with self._lock:
            job = self._record(job_id)
            plan_digest = job.plan_digest
            authorization = json.dumps(job.authorization, sort_keys=True)
            reserved = job.reserved_cost_usd
            attempt_identity = [
                (
                    shot.shot_id,
                    attempt.id,
                    attempt.state.value,
                    attempt.clip_sha256,
                    attempt.operation_name,
                )
                for shot in job.shots
                for attempt in shot.attempts
            ]
            story_path, story_state = self._analysis_artifact(job.analysis_job_id, "story-plan")
            shot_path, shot_state = self._analysis_artifact(job.analysis_job_id, "shot-plan")
            live = self._live_analysis_directory(job.analysis_job_id)
            archive_entry = self.analysis_archive.get(job.analysis_job_id)
            legacy_missing = live is None and (
                archive_entry is None or bool(archive_entry.get("legacyMissing"))
            )
            source_state = (
                VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING.value
                if legacy_missing
                else next(
                    (
                        value
                        for value in (story_state, shot_state)
                        if value != VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING.value
                    ),
                    VideoAnalysisDependencyState.ARCHIVED_ANALYSIS.value,
                )
            )
            audio_path = (
                Path(job.audio_binding.artifact_path)
                if job.audio_binding is not None and Path(job.audio_binding.artifact_path).is_file()
                else None
            )
            repaired = self._snapshot_analysis_dependencies(
                job,
                story_path=story_path,
                shot_path=shot_path,
                source_state=source_state,
                audio_path=audio_path,
            )
            if legacy_missing:
                repaired = repaired.model_copy(
                    update={
                        "analysis_dependency_state": VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING
                    }
                )
                self.analysis_archive.register_legacy_tombstone(
                    job.analysis_job_id,
                    video_job_id=job.id,
                )
            all_verified = all(
                shot.latest_attempt is not None
                and shot.latest_attempt.state is VideoShotState.VERIFIED
                for shot in repaired.shots
            )
            target_state = repaired.state
            if repaired.preview_path and Path(repaired.preview_path).is_file():
                target_state = VideoJobState.COMPLETE
            elif repaired.export_root and Path(repaired.export_root).is_dir():
                target_state = VideoJobState.EXPORTED
            elif repaired.timeline_path and Path(repaired.timeline_path).is_file():
                target_state = VideoJobState.TIMELINE_READY
            elif all_verified:
                target_state = VideoJobState.REVIEW_READY
            repaired = repaired.model_copy(
                update={"state": target_state, "error": None, "updated_at": utc_now()}
            )
            if (
                repaired.plan_digest != plan_digest
                or json.dumps(repaired.authorization, sort_keys=True) != authorization
                or repaired.reserved_cost_usd != reserved
                or [
                    (
                        shot.shot_id,
                        attempt.id,
                        attempt.state.value,
                        attempt.clip_sha256,
                        attempt.operation_name,
                    )
                    for shot in repaired.shots
                    for attempt in shot.attempts
                ]
                != attempt_identity
            ):
                raise RuntimeError("legacy dependency repair altered paid generation identity")
            atomic_write_json(
                self._job_root(repaired) / "repair" / "legacy-analysis-dependency.json",
                {
                    "schemaVersion": "1.0.0",
                    "kind": "legacy-analysis-dependency-repair",
                    "recordedAt": utc_now().isoformat(),
                    "analysisId": repaired.analysis_job_id,
                    "sourceState": source_state,
                    "providerPlanDigest": repaired.plan_digest,
                    "storyPlanAvailable": story_path is not None,
                    "shotPlanAvailable": shot_path is not None,
                    "optionalArtifactsUnavailable": [
                        kind
                        for kind, available in (
                            ("story-plan", story_path is not None),
                            ("shot-plan", shot_path is not None),
                        )
                        if not available
                    ],
                    "clipAttemptIdentityDigest": sha256_json(attempt_identity),
                    "reservedCostUsdBefore": reserved,
                    "reservedCostUsdAfter": repaired.reserved_cost_usd,
                    "providerRequestSubmitted": False,
                },
            )
            return self._view(await self._save(repaired))

    async def chain_reference(
        self,
        job_id: str,
        shot_id: str,
        request: VideoChainReferenceRequest,
    ) -> VideoJobView:
        async with self._lock:
            job = self._record(job_id)
            self._require_current_request_contract(job)
            target = self._shot(job, shot_id)
            source = self._shot(job, request.source_shot_id)
            compiled_target = self._compiled(job, shot_id)
            if compiled_target.previous_shot_id != source.shot_id:
                raise self._error(
                    409,
                    "continuity_chain_invalid",
                    "Only the target shot's declared previous shot can supply its continuity frame",
                )
            if source.review_state is not VideoReviewState.ACCEPTED or not source.accepted_attempt_id:
                raise self._error(
                    409,
                    "continuity_source_not_accepted",
                    "Accept the previous technically verified shot before chaining its final frame",
                )
            source_attempt = next(
                (item for item in source.attempts if item.id == source.accepted_attempt_id),
                None,
            )
            if (
                source_attempt is None
                or source_attempt.state is not VideoShotState.VERIFIED
                or not source_attempt.local_clip_path
            ):
                raise self._error(
                    409,
                    "continuity_source_unavailable",
                    "The accepted previous shot has no durable verified local clip",
                )
            ffmpeg = self.ffmpeg_path()
            if ffmpeg is None:
                raise self._error(
                    409,
                    "continuity_ffmpeg_required",
                    "FFmpeg is required to extract an accepted-shot continuity frame",
                )
            frame = self._job_root(job) / "references" / f"{source_attempt.id}-end-frame.png"
            if not frame.is_file():
                frame.parent.mkdir(parents=True, exist_ok=True)
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        str(ffmpeg),
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-sseof",
                        "-0.05",
                        "-i",
                        source_attempt.local_clip_path,
                        "-frames:v",
                        "1",
                        "-compression_level",
                        "6",
                        str(frame),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0 or not frame.is_file():
                    raise self._error(
                        422,
                        "continuity_frame_extraction_failed",
                        "The accepted-shot final frame could not be extracted",
                    )
            frame_sha256 = sha256_file(frame)
            with frame.open("rb") as handle:
                if not handle.read(8).startswith(b"\x89PNG\r\n\x1a\n"):
                    raise self._error(
                        422,
                        "continuity_frame_invalid",
                        "The extracted continuity frame is not a valid PNG",
                    )
            asset_id = f"{source.shot_id}-accepted-end-frame-for-{target.shot_id}"
            if not compiled_target.storage_uri:
                raise self._error(
                    422,
                    "continuity_storage_uri_missing",
                    "The target shot has no exact GCS storage prefix",
                )
            project_storage_prefix = compiled_target.storage_uri.removesuffix(f"{target.shot_id}/")
            reference = CompiledReferenceImage(
                asset_id=asset_id,
                gcs_uri=f"{project_storage_prefix}_references/{frame_sha256}.png",
                mime_type="image/png",
                sha256=frame_sha256,
                source_kind="accepted-previous-shot-end-frame",
            )
            plan = cast(dict[str, Any], json.loads(json.dumps(job.plan)))
            plan_shots = cast(list[dict[str, Any]], plan["shots"])
            planned_target = next(item for item in plan_shots if item["shotId"] == target.shot_id)
            planned_target["firstFrameReference"] = reference.to_dict()
            planned_target["continuationMode"] = "accepted-previous-shot-end-frame"
            source_artifacts = cast(dict[str, Any], plan["sourceArtifacts"])
            source_artifacts[f"reference:{asset_id}:sha256"] = frame_sha256
            plan.pop("planDigest", None)
            digest = sha256_json(plan)
            plan["planDigest"] = digest
            reference_assets = dict(job.reference_assets)
            reference_assets[asset_id] = {**reference.to_dict(), "localPath": str(frame)}
            revised_target = target.model_copy(
                update={
                    "review_state": VideoReviewState.PENDING,
                    "accepted_attempt_id": None,
                    "retry_requested": bool(target.attempts),
                }
            )
            self._archive_authorization_receipt(job)
            updated = self._replace_shot(job, revised_target).model_copy(
                update={
                    "plan": plan,
                    "plan_digest": digest,
                    "authorization": None,
                    "reference_assets": reference_assets,
                    "state": VideoJobState.PLANNED,
                    "error": None,
                    "updated_at": utc_now(),
                }
            )
            self._persist_plan_revision(job, updated)
            return self._view(await self._save(updated))

    async def resolve(self, job_id: str) -> VideoJobView:
        async with self._lock:
            job = self._record(job_id)
        return self._view(await self._resolve(job))

    async def export(self, job_id: str) -> VideoJobView:
        job = self._record(job_id)
        if not job.timeline_path:
            job = await self._resolve(job)
        return self._view(await self._export(job))

    async def assemble(self, job_id: str) -> VideoJobView:
        job = self._record(job_id)
        if not job.export_root:
            job = await self._export(await self._resolve(job))
        return self._view(await self._assemble(job))

    async def doctor(self, request: VideoDoctorRequest) -> VideoDoctorView:
        result = await asyncio.to_thread(
            doctor,
            project_id=request.gcp_project_id,
            bucket=self._bucket(request.gcs_bucket),
            region=request.region,
        )
        checks = []
        for item in result.checks:
            passed = bool(item.get("ok"))
            checks.append(
                VideoDoctorCheck(
                    id=str(item.get("id", "unknown")),
                    status="pass" if passed else "fail",
                    code=str(item.get("code", item.get("id", "unknown"))),
                    detail=str(item.get("safeDetail", "Ready" if passed else "Unavailable"))[:500],
                )
            )
        checks.append(
            VideoDoctorCheck(
                id="model-access",
                status="unknown",
                code="model_access_not_spend_tested",
                detail="Model access is confirmed only by the authorized smoke request; no generation was submitted by this doctor.",
            )
        )
        return VideoDoctorView(
            ok=result.ok,
            network_contacted=result.network_contacted,
            checks=tuple(checks),
        )

    def artifact_path(self, job_id: str, artifact: str, *, shot_id: str | None = None) -> Path:
        job = self._record(job_id)
        if shot_id is not None:
            shot = self._shot(job, shot_id)
            attempt = next(
                (item for item in reversed(shot.attempts) if item.state is VideoShotState.VERIFIED),
                None,
            )
            if attempt is None or not attempt.local_clip_path:
                raise self._error(404, "video_clip_not_found", "The verified clip is unavailable")
            path = Path(attempt.local_clip_path).resolve()
        else:
            if not job.export_root:
                raise self._error(
                    404, "video_artifact_not_found", "The requested export has not been created"
                )
            timeline_value = read_json(Path(job.timeline_path)) if job.timeline_path else {}
            if not isinstance(timeline_value, dict):
                timeline_value = {}
            export_files = editorial_export_files(timeline_value)
            names = {
                "fcpxml": export_files["fcpxml"],
                "fcp7": export_files["fcp7"],
                "edl": export_files["edl"],
                "edit-sheet": "edit-sheet.csv",
                "markers": "davinci-markers.csv",
                "relink-map": "relink-map.csv",
                "coverage-report": "coverage-report.json",
                "render-manifest": "render-manifest.json",
                "verification-report": "verification-report.json",
                "preview": export_files[
                    "preview4k" if job.plan["profile"]["resolution"] == "4k" else "preview1080p"
                ],
            }
            if artifact not in names:
                raise self._error(404, "video_artifact_not_found", "The requested export does not exist")
            path = (Path(job.export_root) / names[artifact]).resolve()
        root = self._job_root(job)
        if root not in path.parents or not path.is_file():
            raise self._error(404, "video_artifact_not_found", "The requested export is unavailable")
        return path

    def output_root(self, job_id: str) -> Path:
        job = self._record(job_id)
        return Path(job.export_root).resolve() if job.export_root else self._job_root(job)

    async def open_output(self, job_id: str) -> Path:
        if os.name != "nt":
            raise self._error(409, "open_path_unavailable", "Opening output requires the Windows host")
        target = self.output_root(job_id)
        target.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(
                subprocess.Popen,
                ["explorer.exe", str(target)],
                shell=False,
                close_fds=True,
            )
        except OSError as exc:
            raise self._error(
                409,
                "open_path_failed",
                "Windows Explorer could not open the video output directory",
                retryable=True,
            ) from exc
        return target

    def start_background_tasks(self) -> None:
        if self._closed:
            return
        for job in self.store.list_video_jobs(limit=1_000):
            if job.state in _ACTIVE_STATES:
                self._schedule(job.id)

    def close(self) -> None:
        self._closed = True
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    def _schedule(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            return
        self._tasks[job_id] = asyncio.create_task(self._run(job_id), name=f"video-generation-{job_id}")

    async def _run(self, job_id: str) -> None:
        try:
            job = self._record(job_id)
            provider = self.provider_factory(job.gcp_project_id, job.region)
            ordered = sorted(job.shots, key=lambda item: item.order)
            smoke = ordered[0]
            if not await self._process_shot(job_id, smoke.shot_id, provider):
                return
            job = self._record(job_id)
            if job.state is not VideoJobState.GENERATING:
                await self._save(job.model_copy(update={"state": VideoJobState.GENERATING, "error": None}))
            for shot in ordered[1:]:
                await self._process_shot(job_id, shot.shot_id, provider)
            job = self._record(job_id)
            failed = [
                shot
                for shot in job.shots
                if shot.latest_attempt is None or shot.latest_attempt.state is not VideoShotState.VERIFIED
            ]
            if failed:
                await self._save(job.model_copy(update={"state": VideoJobState.PARTIAL}))
                return
            job = await self._save(
                job.model_copy(update={"state": VideoJobState.REVIEW_READY, "error": None})
            )
            # Technical verification and creative acceptance are distinct.
            # Local finishing begins only through an explicit operator action.
        except asyncio.CancelledError:
            return
        except Exception as exc:
            failed_job = self.store.get_video_job(job_id)
            if failed_job is not None:
                safe = self._safe_error(exc)
                await self._save(
                    failed_job.model_copy(
                        update={
                            "state": self._blocked_state(safe.code),
                            "error": safe,
                            "updated_at": utc_now(),
                        }
                    )
                )
        finally:
            self._tasks.pop(job_id, None)

    async def _process_shot(self, job_id: str, shot_id: str, provider: AsyncVideoProvider) -> bool:
        job = self._record(job_id)
        shot = self._shot(job, shot_id)
        attempt = shot.latest_attempt
        created_now = False
        if (
            attempt is None
            or attempt.state in {VideoShotState.FAILED, VideoShotState.FILTERED}
            or shot.retry_requested
        ):
            job, shot, attempt = await self._reserve(job_id, shot_id)
            created_now = True
        if attempt.state is VideoShotState.VERIFIED:
            return True
        if attempt.state is VideoShotState.RESERVED:
            if not created_now:
                await self._fail_attempt(
                    job_id,
                    shot_id,
                    attempt.id,
                    VideoError(
                        code="provider_submission_outcome_unknown",
                        summary="Mission Control restarted after cost reservation but before a durable provider operation name was recorded; the request will not be duplicated automatically.",
                        retryable=False,
                    ),
                )
                return False
            compiled = self._compiled(job, shot_id)
            try:
                await self._prepare_reference(job, compiled, provider)
                response = await provider.submit(
                    compiled,
                    context=ProviderRequestContext(
                        phase="submit",
                        job_id=job.id,
                        shot_id=shot_id,
                        attempt_id=attempt.id,
                    ),
                )
                operation_name = response.get("name")
                if not isinstance(operation_name, str) or not operation_name:
                    raise ProviderError(
                        "Provider submit response had no operation name.", code="provider_response_invalid"
                    )
                attempt = attempt.model_copy(
                    update={
                        "state": VideoShotState.SUBMITTED,
                        "operation_name": operation_name,
                        "updated_at": utc_now(),
                    }
                )
                await self._update_attempt(job_id, shot_id, attempt)
                atomic_write_json(
                    self._job_root(self._record(job_id)) / "operations" / f"{attempt.id}.json",
                    {
                        "schemaVersion": "1.0.0",
                        "operationId": attempt.id,
                        "planDigest": job.plan_digest,
                        "shotId": shot_id,
                        "attempt": attempt.attempt,
                        "operationName": operation_name,
                        "reservedCostUsd": attempt.reserved_cost_usd,
                    },
                )
            except Exception as exc:
                await self._fail_attempt(job_id, shot_id, attempt.id, self._safe_error(exc))
                return False

        attempt = self._shot(self._record(job_id), shot_id).latest_attempt
        assert attempt is not None
        transient_failures = 0
        while attempt.state in {VideoShotState.SUBMITTED, VideoShotState.RUNNING}:
            if not attempt.operation_name:
                await self._fail_attempt(
                    job_id,
                    shot_id,
                    attempt.id,
                    VideoError(
                        code="provider_operation_missing",
                        summary="The durable provider operation name is missing.",
                    ),
                )
                return False
            try:
                response = await provider.fetch(
                    model_id=self._compiled(self._record(job_id), shot_id).model_id,
                    operation_name=attempt.operation_name,
                    context=ProviderRequestContext(
                        phase="poll",
                        job_id=job_id,
                        shot_id=shot_id,
                        attempt_id=attempt.id,
                    ),
                )
                transient_failures = 0
            except ProviderError as exc:
                transient_failures += 1
                if exc.retryable and transient_failures <= 5:
                    await asyncio.sleep(
                        min(30.0, self.poll_interval_seconds * (2 ** (transient_failures - 1)))
                    )
                    continue
                await self._fail_attempt(job_id, shot_id, attempt.id, self._safe_error(exc))
                return False
            if not response.get("done"):
                attempt = attempt.model_copy(
                    update={"state": VideoShotState.RUNNING, "updated_at": utc_now()}
                )
                await self._update_attempt(job_id, shot_id, attempt)
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            if isinstance(response.get("error"), dict):
                error = cast(dict[str, Any], response["error"])
                text = json.dumps(error, ensure_ascii=False).lower()
                filtered = "safety" in text or "rai" in text or "filtered" in text
                diagnostic = save_operation_failure_diagnostic(
                    self.runtime_root / "provider-errors",
                    context=ProviderRequestContext(
                        phase="operation",
                        job_id=job_id,
                        shot_id=shot_id,
                        attempt_id=attempt.id,
                    ),
                    response=response,
                )
                await self._terminal_attempt(
                    job_id,
                    shot_id,
                    attempt.id,
                    state=VideoShotState.FILTERED if filtered else VideoShotState.FAILED,
                    error=VideoError(
                        code="provider_filtered" if filtered else "provider_operation_failed",
                        summary="The provider filtered this shot."
                        if filtered
                        else "The provider operation failed.",
                        http_status=diagnostic.http_status,
                        provider_status=diagnostic.provider_status,
                        provider_error_code=diagnostic.provider_error_code,
                        diagnostic_id=diagnostic.diagnostic_id,
                    ),
                )
                return False
            response_payload = response.get("response")
            filtered_count = (
                int(response_payload.get("raiMediaFilteredCount", 0) or 0)
                if isinstance(response_payload, dict)
                else 0
            )
            if filtered_count:
                diagnostic = save_operation_failure_diagnostic(
                    self.runtime_root / "provider-errors",
                    context=ProviderRequestContext(
                        phase="operation-filtered",
                        job_id=job_id,
                        shot_id=shot_id,
                        attempt_id=attempt.id,
                    ),
                    response=response,
                )
                await self._terminal_attempt(
                    job_id,
                    shot_id,
                    attempt.id,
                    state=VideoShotState.FILTERED,
                    error=VideoError(
                        code="provider_filtered",
                        summary="The provider filtered this shot.",
                        http_status=diagnostic.http_status,
                        diagnostic_id=diagnostic.diagnostic_id,
                    ),
                )
                return False
            compiled = self._compiled(self._record(job_id), shot_id)
            try:
                uris = response_output_uris(response, required_prefix=compiled.storage_uri)
            except ProviderError as exc:
                await self._fail_attempt(job_id, shot_id, attempt.id, self._safe_error(exc))
                return False
            if len(uris) != 1:
                await self._fail_attempt(
                    job_id,
                    shot_id,
                    attempt.id,
                    VideoError(
                        code="provider_response_invalid",
                        summary="The completed provider response did not contain exactly one authorized video URI.",
                    ),
                )
                return False
            attempt = attempt.model_copy(
                update={"state": VideoShotState.SUCCEEDED, "output_uri": uris[0], "updated_at": utc_now()}
            )
            await self._update_attempt(job_id, shot_id, attempt)

        attempt = self._shot(self._record(job_id), shot_id).latest_attempt
        assert attempt is not None
        if attempt.state is VideoShotState.SUCCEEDED:
            if not attempt.output_uri:
                return False
            destination = (
                self._job_root(self._record(job_id)) / "clips" / shot_id / attempt.id / "provider.mp4"
            )
            try:
                await provider.download(attempt.output_uri, destination)
            except Exception as exc:
                await self._fail_attempt(job_id, shot_id, attempt.id, self._safe_error(exc))
                return False
            attempt = attempt.model_copy(
                update={
                    "state": VideoShotState.DOWNLOADED,
                    "local_clip_path": str(destination),
                    "updated_at": utc_now(),
                }
            )
            await self._update_attempt(job_id, shot_id, attempt)

        attempt = self._shot(self._record(job_id), shot_id).latest_attempt
        assert attempt is not None
        if attempt.state is VideoShotState.DOWNLOADED and attempt.local_clip_path:
            compiled = self._compiled(self._record(job_id), shot_id)
            try:
                evidence = await asyncio.to_thread(
                    verify_generated_clip,
                    Path(attempt.local_clip_path),
                    resolution=compiled.resolution,
                    aspect_ratio=compiled.aspect_ratio,
                    expected_duration_seconds=compiled.duration_seconds,
                    ffprobe=str(self.ffprobe_path()) if self.ffprobe_path() else None,
                )
            except Exception as exc:
                await self._fail_attempt(job_id, shot_id, attempt.id, self._safe_error(exc))
                return False
            attempt = attempt.model_copy(
                update={
                    "state": VideoShotState.VERIFIED,
                    "clip_sha256": evidence.sha256,
                    "probe_evidence": evidence.to_dict(),
                    "updated_at": utc_now(),
                }
            )
            await self._update_attempt(job_id, shot_id, attempt)
            atomic_write_json(
                self._job_root(self._record(job_id)) / "verification" / f"{shot_id}-{attempt.id}.json",
                evidence.to_dict(),
            )
            return True
        return attempt.state is VideoShotState.VERIFIED

    async def _prepare_reference(
        self,
        job: VideoJobRecord,
        shot: CompiledShot,
        provider: AsyncVideoProvider,
    ) -> None:
        reference = shot.first_frame_reference
        if reference is None:
            return
        private = job.reference_assets.get(reference.asset_id)
        if not isinstance(private, dict):
            raise ProviderError(
                "The exact plan's private reference mapping is unavailable; compile a fresh plan.",
                code="reference_asset_missing",
            )
        local_path = private.get("localPath")
        if not isinstance(local_path, str):
            raise ProviderError(
                "The exact plan's private reference path is unavailable; compile a fresh plan.",
                code="reference_asset_missing",
            )
        receipt = self._job_root(job) / "reference-uploads" / f"{reference.asset_id}.json"
        if receipt.is_file():
            value = read_json(receipt)
            if (
                isinstance(value, dict)
                and value.get("sha256") == reference.sha256
                and value.get("gcsUri") == reference.gcs_uri
            ):
                return
        await provider.upload_reference(Path(local_path), reference.gcs_uri, reference.sha256)
        atomic_write_json(
            receipt,
            {
                "schemaVersion": "1.0.0",
                "assetId": reference.asset_id,
                "sha256": reference.sha256,
                "gcsUri": reference.gcs_uri,
                "mimeType": reference.mime_type,
                "uploadedAt": utc_now().isoformat(),
                "planDigest": job.plan_digest,
            },
        )

    async def _reserve(
        self, job_id: str, shot_id: str
    ) -> tuple[VideoJobRecord, VideoShotRecord, VideoShotAttempt]:
        async with self._lock:
            job = self._record(job_id)
            shot = self._shot(job, shot_id)
            self._authorization(job, next_cost=shot.estimated_cost_usd)
            number = len(shot.attempts) + 1
            compiled = self._compiled(job, shot_id)
            key = sha256_json(
                {
                    "planDigest": job.plan_digest,
                    "shotId": shot_id,
                    "attempt": number,
                    "modelId": compiled.model_id,
                    "request": build_request_payload(compiled),
                }
            )
            now = utc_now()
            attempt = VideoShotAttempt(
                id=f"{shot_id}-attempt-{number:02d}-{key[:12]}",
                attempt=number,
                idempotency_key=key,
                state=VideoShotState.RESERVED,
                reserved_cost_usd=shot.estimated_cost_usd,
                created_at=now,
                updated_at=now,
            )
            shot = shot.model_copy(
                update={
                    "attempts": (*shot.attempts, attempt),
                    "retry_requested": False,
                }
            )
            job = self._replace_shot(job, shot).model_copy(
                update={
                    "reserved_cost_usd": round(job.reserved_cost_usd + shot.estimated_cost_usd, 4),
                    "updated_at": now,
                }
            )
            return await self._save(job), shot, attempt

    async def _update_attempt(self, job_id: str, shot_id: str, attempt: VideoShotAttempt) -> VideoJobRecord:
        async with self._lock:
            job = self._record(job_id)
            shot = self._shot(job, shot_id)
            attempts = tuple(attempt if item.id == attempt.id else item for item in shot.attempts)
            return await self._save(self._replace_shot(job, shot.model_copy(update={"attempts": attempts})))

    async def _terminal_attempt(
        self,
        job_id: str,
        shot_id: str,
        attempt_id: str,
        *,
        state: VideoShotState,
        error: VideoError,
    ) -> None:
        job = self._record(job_id)
        shot = self._shot(job, shot_id)
        attempt = next(item for item in shot.attempts if item.id == attempt_id)
        await self._update_attempt(
            job_id,
            shot_id,
            attempt.model_copy(update={"state": state, "error": error, "updated_at": utc_now()}),
        )

    async def _fail_attempt(self, job_id: str, shot_id: str, attempt_id: str, error: VideoError) -> None:
        await self._terminal_attempt(
            job_id,
            shot_id,
            attempt_id,
            state=VideoShotState.FAILED,
            error=error,
        )
        job = self._record(job_id)
        await self._save(
            job.model_copy(
                update={"state": self._blocked_state(error.code), "error": error, "updated_at": utc_now()}
            )
        )

    async def _resolve(self, job: VideoJobRecord) -> VideoJobRecord:
        binding = job.audio_binding
        if binding is None or not binding.verified:
            raise self._error(
                409,
                "audio_master_required",
                "Select the original local audio master before resolving the timeline",
            )
        finishing_path = Path(binding.finishing_path)
        try:
            audio_evidence = await asyncio.to_thread(
                probe_audio,
                finishing_path,
                ffprobe=str(self.ffprobe_path()) if self.ffprobe_path() else None,
            )
        except AudioBindingError as exc:
            raise self._error(422, exc.code, exc.safe_message) from exc
        if (
            audio_evidence.sha256 != binding.finishing_sha256
            or abs(audio_evidence.duration_seconds - binding.duration_seconds) > 0.02
            or audio_evidence.sample_rate_hz != 48_000
            or audio_evidence.channels != 2
        ):
            raise self._error(
                422,
                "audio_binding_stale",
                "The verified finishing audio no longer matches its persisted binding.",
            )
        clip_paths: dict[str, Path] = {}
        clip_sha256: dict[str, str] = {}
        accepted_attempt_ids: dict[str, str] = {}
        for shot in job.shots:
            if shot.review_state is not VideoReviewState.ACCEPTED:
                raise self._error(
                    409,
                    "video_clip_not_accepted",
                    f"{shot.shot_id} must be accepted before timeline resolution",
                )
            attempt = (
                next(
                    (
                        item
                        for item in shot.attempts
                        if item.id == shot.accepted_attempt_id
                        and item.state is VideoShotState.VERIFIED
                        and item.local_clip_path
                    ),
                    None,
                )
                if shot.accepted_attempt_id
                else shot.latest_attempt
            )
            if attempt is None or attempt.local_clip_path is None:
                raise self._error(
                    409, "video_clip_missing", f"{shot.shot_id} has no technically verified clip"
                )
            if attempt.state is not VideoShotState.VERIFIED:
                raise self._error(
                    409, "video_clip_unverified", f"{shot.shot_id} latest attempt is not verified"
                )
            clip_path = Path(attempt.local_clip_path)
            actual_sha256 = await asyncio.to_thread(sha256_file, clip_path)
            if attempt.clip_sha256 and actual_sha256 != attempt.clip_sha256:
                raise self._error(
                    422,
                    "video_clip_hash_mismatch",
                    f"{shot.shot_id} no longer matches its verified clip hash",
                )
            clip_paths[shot.shot_id] = clip_path
            clip_sha256[shot.shot_id] = actual_sha256
            accepted_attempt_ids[shot.shot_id] = attempt.id
        project_root = self._project_root(job.project_id)
        edit_blueprint_path = project_root / "edit-blueprint.json"
        if not edit_blueprint_path.is_file():
            raise self._error(
                422,
                "video_edit_blueprint_missing",
                "The selected video package has no editorial blueprint",
            )
        try:
            edit_blueprint = load_edit_blueprint(
                edit_blueprint_path,
                project_id=job.project_id,
                title=job.title,
            )
        except (ContractError, OSError, ValueError) as exc:
            raise self._error(422, "video_edit_blueprint_invalid", str(exc)) from exc
        profile = cast(dict[str, Any], job.plan["profile"])
        width, height = (3840, 2160) if profile["resolution"] == "4k" else (1920, 1080)
        shot_plan, shot_plan_source = self._optional_analysis_artifact_for_job(
            job, "shot-plan"
        )
        timeline_path = self._job_root(job) / "davinci" / str(profile["profileId"]) / "resolved-timeline.json"
        operation_items = sorted(
            (
                shot.shot_id,
                attempt.id,
                attempt.operation_name,
            )
            for shot in job.shots
            for attempt in shot.attempts
            if attempt.operation_name
        )
        operation_snapshot = {
            "count": len(operation_items),
            "digest": sha256_json(operation_items),
        }
        local_edit_digest = sha256_json(
            {
                "schemaVersion": "1.0.0",
                "providerPlanDigest": job.plan_digest,
                "audioSha256": binding.sha256,
                "finishingSha256": binding.finishing_sha256,
                "audioDurationSeconds": binding.duration_seconds,
                "acceptedAttempts": accepted_attempt_ids,
                "clipSha256": clip_sha256,
                "editBlueprintSha256": sha256_file(edit_blueprint_path),
                "analysisShotPlanSha256": sha256_file(shot_plan) if shot_plan else None,
                "analysisShotPlanSource": shot_plan_source,
                "timelineTreatmentVersion": edit_blueprint["timelineTreatment"]["version"],
                "delivery": {"width": width, "height": height, "fps": 24},
            }
        )
        value = await asyncio.to_thread(
            resolve_timeline,
            project_id=job.project_id,
            title=job.title,
            audio_path=finishing_path,
            chapter_map_path=project_root / "chapter-map.json",
            edit_blueprint_path=edit_blueprint_path,
            clips_root=self._job_root(job) / "clips",
            clip_paths=clip_paths,
            output_width=width,
            output_height=height,
            fps=24,
            generated_clip_duration_seconds=int(profile["durationSeconds"]),
            analysis_shot_plan_path=shot_plan,
            local_edit_digest=local_edit_digest,
            ffprobe=str(self.ffprobe_path()) if self.ffprobe_path() else None,
        )
        value.update(
            {
                "providerPlanDigest": job.plan_digest,
                "providerOperationSnapshot": operation_snapshot,
                "sourceClipSha256": clip_sha256,
                "acceptedAttemptIds": accepted_attempt_ids,
                "audio": {
                    "audioArtifactId": binding.audio_artifact_id,
                    "sha256": binding.sha256,
                    "finishingSha256": binding.finishing_sha256,
                    "durationSeconds": binding.duration_seconds,
                    "sampleRateHz": binding.sample_rate_hz,
                    "channels": binding.channels,
                },
            }
        )
        if shot_plan is None:
            warnings = value.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.append(
                    "Exact ShotPlan boundary snapping was unavailable; normalized chapter timing was used."
                )
        atomic_write_json(timeline_path, value)
        return await self._save(
            job.model_copy(
                update={
                    "state": VideoJobState.TIMELINE_READY,
                    "timeline_path": str(timeline_path),
                    "local_edit_digest": local_edit_digest,
                    "error": None,
                }
            )
        )

    async def _export(self, job: VideoJobRecord) -> VideoJobRecord:
        if not job.timeline_path:
            raise self._error(409, "video_timeline_required", "Resolve the timeline before exporting")
        value = read_json(Path(job.timeline_path))
        if not isinstance(value, dict):
            raise self._error(422, "video_timeline_invalid", "The resolved timeline is invalid")
        output_root = Path(job.timeline_path).parent
        await asyncio.to_thread(
            export_davinci_package,
            value,
            output_root=output_root,
            ffmpeg=str(self.ffmpeg_path()) if self.ffmpeg_path() else None,
        )
        return await self._save(
            job.model_copy(
                update={"state": VideoJobState.EXPORTED, "export_root": str(output_root), "error": None}
            )
        )

    async def _assemble(self, job: VideoJobRecord) -> VideoJobRecord:
        if not job.export_root:
            raise self._error(
                409, "video_export_required", "Export the Resolve package before assembling the preview"
            )
        if not job.timeline_path:
            raise self._error(
                409, "video_timeline_required", "Resolve the timeline before assembling the preview"
            )
        export_root = Path(job.export_root)
        timeline_path = Path(job.timeline_path)
        plan_value = read_json(export_root / "assembly-plan.json")
        if not isinstance(plan_value, dict):
            raise self._error(422, "video_assembly_plan_invalid", "The assembly plan is invalid")
        from .assembly import AssemblyPlan

        plan = AssemblyPlan(
            ffmpeg=str(plan_value["ffmpeg"]),
            output_path=str(plan_value["outputPath"]),
            segment_directory=str(plan_value["segmentDirectory"]),
            commands=tuple(tuple(str(token) for token in command) for command in plan_value["commands"]),
            concat_list_path=str(plan_value["concatListPath"]),
            video_only_path=str(plan_value["videoOnlyPath"]),
            audio_path=str(plan_value["audioPath"]),
            total_frames=int(plan_value["totalFrames"]),
            fps=int(plan_value["fps"]),
        )
        job = await self._save(job.model_copy(update={"state": VideoJobState.ASSEMBLING, "error": None}))
        await asyncio.to_thread(execute_assembly, plan)
        evidence = await asyncio.to_thread(
            probe,
            Path(plan.output_path),
            ffprobe=str(self.ffprobe_path()) if self.ffprobe_path() else None,
        )
        if not evidence.has_audio:
            raise self._error(
                422, "video_preview_audio_missing", "The autonomous preview has no audio stream"
            )
        timeline = cast(dict[str, Any], read_json(timeline_path))
        export_files = editorial_export_files(timeline)
        expected_duration = float(cast(dict[str, Any], timeline["timeline"])["durationSeconds"])
        if abs(evidence.duration_seconds - expected_duration) > 0.75:
            raise self._error(
                422,
                "video_preview_duration_invalid",
                "The autonomous preview duration does not match the local audio clock",
            )
        current = self._record(job.id)
        current_operation_items = sorted(
            (shot.shot_id, attempt.id, attempt.operation_name)
            for shot in current.shots
            for attempt in shot.attempts
            if attempt.operation_name
        )
        operation_snapshot = cast(
            dict[str, Any],
            timeline.get("providerOperationSnapshot", {}),
        )
        provider_unchanged = (
            int(operation_snapshot.get("count", -1)) == len(current_operation_items)
            and str(operation_snapshot.get("digest", "")) == sha256_json(current_operation_items)
        )
        frame_tolerance = 1 / plan.fps
        ffprobe = self.ffprobe_path()
        if ffprobe is None:
            raise self._error(
                503,
                "ffprobe_unavailable",
                "ffprobe is required to verify the complete rough cut.",
            )
        stream_layout = await asyncio.to_thread(
            _probe_stream_layout,
            Path(plan.output_path),
            ffprobe=str(ffprobe),
        )
        audio_streams = cast(list[dict[str, Any]], stream_layout["audio"])
        video_streams = cast(list[dict[str, Any]], stream_layout["video"])
        derived_paths = [
            Path(str(item["derivedMediaPath"]))
            for item in cast(list[dict[str, Any]], timeline["segments"])
        ]
        xml_parseable = True
        try:
            import xml.etree.ElementTree as element_tree

            element_tree.parse(export_root / export_files["fcpxml"])
            element_tree.parse(export_root / export_files["fcp7"])
        except (OSError, element_tree.ParseError):
            xml_parseable = False
        coverage_value = read_json(export_root / "coverage-report.json")
        coverage = coverage_value if isinstance(coverage_value, dict) else {}
        verification = {
            "schemaVersion": "1.0.0",
            "status": "passed",
            "localEditDigest": job.local_edit_digest,
            "preview": evidence.to_dict(),
            "streamLayout": stream_layout,
            "checks": {
                "dimensionsMatchTimeline": (evidence.width, evidence.height)
                == (
                    int(cast(dict[str, Any], timeline["timeline"])["width"]),
                    int(cast(dict[str, Any], timeline["timeline"])["height"]),
                ),
                "fps24": abs(evidence.fps - 24.0) <= 0.01,
                "codecH264": evidence.codec == "h264",
                "audioPresent": evidence.has_audio,
                "oneVideoStream": len(video_streams) == 1,
                "oneAudioStream": len(audio_streams) == 1,
                "audioAac48kStereo": len(audio_streams) == 1
                and audio_streams[0].get("codec_name") == "aac"
                and str(audio_streams[0].get("sample_rate")) == "48000"
                and int(audio_streams[0].get("channels", 0)) == 2,
                "durationWithinOneFrame": abs(evidence.duration_seconds - expected_duration)
                <= frame_tolerance,
                "providerOperationsUnchanged": provider_unchanged,
                "allDerivedMediaExists": bool(derived_paths)
                and all(path.is_file() for path in derived_paths),
                "xmlParseable": xml_parseable,
                "timelineContinuous": coverage.get("continuous") is True,
                "allSixteenShotsUsed": coverage.get("allSixteenShotsUsed") is True,
                "eventCountInRequiredRange": coverage.get("eventCountInRequiredRange") is True,
                # Pre-blueprint packages did not persist this generic field.
                # Their other hash/media checks remain authoritative.
                "editorialChecksPassed": coverage.get("editorialChecksPassed", True) is True,
                "noIdenticalSourceTreatmentRepeats": coverage.get(
                    "noIdenticalSourceTreatmentRepeats"
                )
                is True,
            },
        }
        if not all(cast(dict[str, bool], verification["checks"]).values()):
            raise self._error(
                422,
                "video_preview_verification_failed",
                "The complete rough cut did not pass authoritative media verification.",
            )
        atomic_write_json(export_root / "verification-report.json", verification)
        manifest_value = read_json(export_root / "render-manifest.json")
        if isinstance(manifest_value, dict):
            package_files = [
                export_root / export_files["fcpxml"],
                export_root / export_files["fcp7"],
                export_root / export_files["edl"],
                export_root / "edit-plan.json",
                export_root / "edit-sheet.csv",
                export_root / "davinci-markers.csv",
                export_root / "relink-map.csv",
                export_root / "coverage-report.json",
                export_root / "verification-report.json",
                export_root / "README-DAVINCI.txt",
                Path(plan.output_path),
            ]
            manifest_value.update(
                {
                    "status": "complete",
                    "previewSha256": evidence.sha256,
                    "previewDurationSeconds": evidence.duration_seconds,
                    "completedAt": utc_now().isoformat(),
                    "verificationReport": str(export_root / "verification-report.json"),
                    "files": {
                        path.name: {"sha256": sha256_file(path), "sizeBytes": path.stat().st_size}
                        for path in package_files
                    },
                }
            )
            atomic_write_json(export_root / "render-manifest.json", manifest_value)
        return await self._save(
            job.model_copy(
                update={
                    "state": VideoJobState.COMPLETE,
                    "preview_path": plan.output_path,
                    "completed_at": utc_now(),
                    "error": None,
                }
            )
        )

    def _record(self, job_id: str) -> VideoJobRecord:
        try:
            canonical = str(UUID(job_id))
        except ValueError as exc:
            raise self._error(404, "video_job_not_found", "The video job does not exist") from exc
        job = self.store.get_video_job(canonical)
        if job is None:
            raise self._error(404, "video_job_not_found", "The video job does not exist")
        return self._normalize_legacy_audio(job)

    def _normalize_legacy_audio(self, job: VideoJobRecord) -> VideoJobRecord:
        if job.audio_binding is not None:
            return job
        audio_root = self._job_root(job) / "audio"
        binding_path = audio_root / "audio-binding.json"
        if binding_path.is_file():
            value = read_json(binding_path)
            if isinstance(value, dict):
                try:
                    restored = VideoAudioBinding.model_validate(value)
                except ValueError:
                    restored = None
                if restored is not None and Path(restored.finishing_path).is_file():
                    updated = job.model_copy(
                        update={"audio_path": restored.finishing_path, "audio_binding": restored}
                    )
                    self.store.put_video_job(updated)
                    return updated

        legacy_path: str | None = job.audio_path if isinstance(job.audio_path, str) else None
        legacy_selection = audio_root / "audio-selection.json"
        if legacy_path is None and legacy_selection.is_file():
            value = read_json(legacy_selection)
            if isinstance(value, dict):
                selected = value.get("selected")
                path = value.get("path")
                # Only these two historical shapes are understood. Never
                # coerce arbitrary values with bool(...).
                if isinstance(selected, str):
                    legacy_path = selected
                elif selected is True and isinstance(path, str):
                    legacy_path = path
                elif selected is False:
                    legacy_path = None
        if legacy_path is None:
            return job

        try:
            staged = stage_audio_master(
                Path(legacy_path),
                artifact_root=audio_root / "artifacts",
                ffmpeg=str(self.ffmpeg_path()) if self.ffmpeg_path() else None,
                ffprobe=str(self.ffprobe_path()) if self.ffprobe_path() else None,
            )
        except AudioBindingError as exc:
            updated = job.model_copy(update={"audio_path": None, "audio_binding": None})
            atomic_write_json(
                audio_root / "audio-selection.json",
                {
                    "schemaVersion": "1.0.0",
                    "selected": False,
                    "verified": False,
                    "error": {"code": exc.code, "message": exc.safe_message},
                    "migratedAt": utc_now().isoformat(),
                },
            )
            self.store.put_video_job(updated)
            return updated
        binding = self._binding_from_staged(job, staged, source="legacy-local-path")
        updated = self._invalidate_finishing_outputs(job).model_copy(
            update={"audio_path": binding.finishing_path, "audio_binding": binding}
        )
        atomic_write_json(
            binding_path,
            binding.model_dump(mode="json", by_alias=True),
        )
        atomic_write_json(
            audio_root / "audio-selection.json",
            binding.model_dump(mode="json", by_alias=True),
        )
        self.store.put_video_job(updated)
        return updated

    @staticmethod
    def _shot(job: VideoJobRecord, shot_id: str) -> VideoShotRecord:
        shot = job.shot(shot_id)
        if shot is None:
            raise VideoGenerationController._error(
                404, "video_shot_not_found", "The shot does not exist in this exact plan"
            )
        return shot

    @staticmethod
    def _replace_shot(job: VideoJobRecord, shot: VideoShotRecord) -> VideoJobRecord:
        return job.model_copy(
            update={
                "shots": tuple(shot if item.shot_id == shot.shot_id else item for item in job.shots),
                "updated_at": utc_now(),
            }
        )

    @staticmethod
    def _compiled(job: VideoJobRecord, shot_id: str) -> CompiledShot:
        values = cast(list[dict[str, Any]], job.plan["shots"])
        value = next(item for item in values if item["shotId"] == shot_id)
        reference_value = value.get("firstFrameReference")
        reference = (
            CompiledReferenceImage(
                asset_id=str(reference_value["assetId"]),
                gcs_uri=str(reference_value["gcsUri"]),
                mime_type=str(reference_value["mimeType"]),
                sha256=str(reference_value["sha256"]),
                source_kind=str(reference_value["sourceKind"]),
            )
            if isinstance(reference_value, dict)
            else None
        )
        return CompiledShot(
            shot_id=str(value["shotId"]),
            chapter_id=str(value["chapterId"]),
            order=int(value["order"]),
            title=str(value["title"]),
            duration_seconds=int(value["durationSeconds"]),
            prompt=str(value["prompt"]),
            negative_prompt=str(value["negativePrompt"]),
            seed=int(value["seed"]),
            model_id=str(value["modelId"]),
            resolution=str(value["resolution"]),
            aspect_ratio=str(value["aspectRatio"]),
            sample_count=int(value["sampleCount"]),
            generate_audio=bool(value["generateAudio"]),
            enhance_prompt=bool(value["enhancePrompt"]),
            compression_quality=str(value["compressionQuality"]),
            person_generation=str(value["personGeneration"]),
            storage_uri=cast(str | None, value.get("storageUri")),
            required=bool(value["required"]),
            estimated_cost_usd=float(value["estimatedCostUsd"]),
            source_section_hints=tuple(str(item) for item in value.get("sourceSectionHints", [])),
            review_notes=tuple(str(item) for item in value.get("reviewNotes", [])),
            variation_index=int(value.get("variationIndex", 0)),
            continuity_group_ids=tuple(str(item) for item in value.get("continuityGroupIds", [])),
            previous_shot_id=(str(value["previousShotId"]) if value.get("previousShotId") else None),
            continuation_mode=str(value.get("continuationMode", "prompt-anchors")),
            first_frame_reference=reference,
        )

    def _authorization(self, job: VideoJobRecord, *, next_cost: float) -> BatchAuthorization:
        self._require_current_request_contract(job)
        if not job.authorization:
            raise self._error(
                409,
                "video_authorization_required",
                "Enter the exact displayed plan-level maximum-spend phrase before starting",
            )
        try:
            authorization = BatchAuthorization.from_dict(job.authorization)
            authorization.validate_for(
                project_id=job.project_id,
                plan_digest=job.plan_digest,
                current_reserved_usd=job.reserved_cost_usd,
                next_request_usd=next_cost,
            )
        except ContractError as exc:
            code = "video_budget_exceeded" if "exceed" in str(exc).lower() else "video_authorization_invalid"
            raise self._error(409, code, str(exc)) from exc
        return authorization

    @staticmethod
    def _require_current_request_contract(job: VideoJobRecord) -> None:
        if job.plan.get("requestContractVersion") != "vertex-veo-predict-long-running-v2":
            raise VideoGenerationController._error(
                409,
                "video_plan_request_contract_changed",
                "This plan predates the corrected Veo request contract. Compile and authorize a fresh exact plan before any paid retry.",
            )
        profile = cast(dict[str, Any], job.plan.get("profile", {}))
        if profile.get("resolution") == "4k" and profile.get("modelId") in {
            "veo-3.1-generate-001",
            "veo-3.1-fast-generate-001",
        }:
            raise VideoGenerationController._error(
                409,
                "video_plan_model_capability_changed",
                "The selected GA Veo model does not accept 4K output. Compile and authorize a fresh 1080p exact plan.",
            )

    def _archive_authorization_receipt(self, job: VideoJobRecord) -> None:
        current = self._job_root(job) / "authorization.json"
        if not current.is_file():
            return
        value = read_json(current)
        if not isinstance(value, dict):
            return
        digest = str(value.get("planDigest", "unknown"))
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            digest = sha256_json(value)
        history = self._job_root(job) / "authorization-history" / f"{digest}.json"
        if not history.exists():
            atomic_write_json(history, value)

    async def _save(self, job: VideoJobRecord) -> VideoJobRecord:
        updated = job.model_copy(update={"updated_at": utc_now()})
        self.store.put_video_job(updated)
        event = self.store.append_video_event(self._event(updated))
        await self.notify_event(event.sequence)
        return updated

    def _event(self, job: VideoJobRecord) -> VideoGenerationEvent:
        verified = sum(
            1
            for shot in job.shots
            if shot.latest_attempt is not None and shot.latest_attempt.state is VideoShotState.VERIFIED
        )
        return VideoGenerationEvent(
            sequence=0,
            timestamp=utc_now(),
            job_id=job.id,
            project_id=job.project_id,
            state=job.state,
            progress_percent=(verified / len(job.shots) * 100) if job.shots else 0,
            verified_shot_count=verified,
            total_shot_count=len(job.shots),
            reserved_cost_usd=job.reserved_cost_usd,
            error=job.error,
        )

    def _view(self, job: VideoJobRecord) -> VideoJobView:
        verified = sum(
            1
            for shot in job.shots
            if shot.latest_attempt is not None and shot.latest_attempt.state is VideoShotState.VERIFIED
        )
        maximum = float(cast(dict[str, Any], job.plan["cost"])["maxSpendUsd"])
        shots = []
        for shot in sorted(job.shots, key=lambda item: item.order):
            attempt = shot.latest_attempt
            compiled = self._compiled(job, shot.shot_id)
            shots.append(
                VideoShotView(
                    shot_id=shot.shot_id,
                    chapter_id=shot.chapter_id,
                    order=shot.order,
                    title=shot.title,
                    prompt=shot.prompt,
                    negative_prompt=shot.negative_prompt,
                    seed=shot.seed,
                    state=attempt.state if attempt else VideoShotState.PLANNED,
                    review_state=shot.review_state,
                    review_note=shot.review_note,
                    attempt_count=len(shot.attempts),
                    reserved_cost_usd=sum(item.reserved_cost_usd for item in shot.attempts),
                    error=attempt.error if attempt else None,
                    clip_url=(
                        f"/api/mission-control/video/jobs/{job.id}/shots/{shot.shot_id}/clip"
                        if attempt is not None and attempt.state is VideoShotState.VERIFIED
                        else None
                    ),
                    variation_index=compiled.variation_index,
                    continuity_group_ids=compiled.continuity_group_ids,
                    previous_shot_id=compiled.previous_shot_id,
                    continuation_mode=compiled.continuation_mode,
                    reference_asset_id=(
                        compiled.first_frame_reference.asset_id if compiled.first_frame_reference else None
                    ),
                )
            )
        base = f"/api/mission-control/video/jobs/{job.id}/artifacts"
        export_ready = bool(job.export_root)
        preview_ready = bool(job.preview_path and Path(job.preview_path).is_file())
        plan_error = job.error
        if job.plan.get("requestContractVersion") != "vertex-veo-predict-long-running-v2":
            plan_error = VideoError(
                code="video_plan_request_contract_changed",
                summary="This saved plan used the previous Veo request contract and cannot be resumed. Compile and authorize a fresh exact 1080p plan.",
            )
        audio_selection = self._audio_selection(job)
        dependency_manifest = (
            read_json(Path(job.dependency_manifest_path))
            if job.dependency_manifest_path and Path(job.dependency_manifest_path).is_file()
            else None
        )
        dependency_artifacts = (
            dependency_manifest.get("artifacts")
            if isinstance(dependency_manifest, dict)
            else None
        )
        story_identity = (
            dependency_artifacts.get("storyPlan")
            if isinstance(dependency_artifacts, dict)
            else None
        )
        shot_identity = (
            dependency_artifacts.get("shotPlan")
            if isinstance(dependency_artifacts, dict)
            else None
        )
        legacy_missing = (
            job.analysis_dependency_state is VideoAnalysisDependencyState.LEGACY_ANALYSIS_MISSING
        )
        return VideoJobView(
            job_id=job.id,
            analysis_job_id=job.analysis_job_id,
            project_id=job.project_id,
            title=job.title,
            state=job.state,
            plan_digest=job.plan_digest,
            profile=cast(dict[str, Any], job.plan["profile"]),
            cost=cast(dict[str, Any], job.plan["cost"]),
            source_artifacts=cast(dict[str, str], job.plan["sourceArtifacts"]),
            authorization_phrase=authorization_phrase(job.project_id, job.plan_digest, maximum),
            authorization_expires_at=(str(job.authorization["expiresAt"]) if job.authorization else None),
            audio_master_bound=audio_selection.selected and audio_selection.verified,
            audio=audio_selection,
            analysis_dependency=VideoAnalysisDependencyView(
                source_state=job.analysis_dependency_state,
                manifest_ready=isinstance(dependency_manifest, dict),
                story_plan_available=isinstance(story_identity, dict)
                and story_identity.get("available") is True,
                shot_plan_available=isinstance(shot_identity, dict)
                and shot_identity.get("available") is True,
                legacy_analysis_missing=legacy_missing,
                warning=(
                    "Legacy analysis workspace is unavailable. Generated clips are safe; the compiled plan and local dependency manifest remain authoritative."
                    if legacy_missing
                    else None
                ),
            ),
            local_edit_digest=job.local_edit_digest,
            shots=tuple(shots),
            progress_percent=(verified / len(job.shots) * 100) if job.shots else 0,
            verified_shot_count=verified,
            total_shot_count=len(job.shots),
            reserved_cost_usd=job.reserved_cost_usd,
            remaining_authorized_usd=max(0, round(maximum - job.reserved_cost_usd, 4)),
            request_preview_url=f"/api/mission-control/video/plans/{job.id}/requests",
            consistency_notice="Locked continuity anchors and deterministic seeds improve repeatability. First-frame conditioning is applied only when an approved, hash-bound reference is in the exact plan; no generative model guarantees perfect identity lock.",
            continuity=cast(dict[str, Any], job.plan.get("continuity", {})),
            artifacts=VideoArtifactSummary(
                timeline_ready=bool(job.timeline_path),
                davinci_package_ready=export_ready,
                preview_ready=preview_ready,
                fcpxml_url=f"{base}/fcpxml" if export_ready else None,
                fcp7_xml_url=f"{base}/fcp7" if export_ready else None,
                edl_url=f"{base}/edl" if export_ready else None,
                edit_sheet_url=f"{base}/edit-sheet" if export_ready else None,
                markers_url=f"{base}/markers" if export_ready else None,
                relink_map_url=f"{base}/relink-map" if export_ready else None,
                coverage_report_url=f"{base}/coverage-report" if export_ready else None,
                render_manifest_url=f"{base}/render-manifest" if export_ready else None,
                verification_report_url=(
                    f"{base}/verification-report" if export_ready else None
                ),
                preview_url=f"{base}/preview" if preview_ready else None,
            ),
            error=plan_error,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    @staticmethod
    def _safe_error(exc: Exception) -> VideoError:
        from app.mission_control.errors import MissionControlError

        if isinstance(exc, ProviderError):
            return VideoError(
                code=exc.code,
                summary=str(exc),
                retryable=exc.retryable,
                http_status=exc.http_status,
                provider_status=exc.provider_status,
                provider_error_code=exc.provider_error_code,
                diagnostic_id=exc.diagnostic_id,
            )
        if isinstance(exc, MissionControlError):
            return VideoError(
                code=exc.error.code,
                summary=exc.error.summary,
                retryable=exc.error.retryable,
            )
        if isinstance(exc, ContractError):
            return VideoError(code="video_contract_error", summary=str(exc)[:1_000])
        return VideoError(code="video_operation_failed", summary="The video operation failed locally.")

    @staticmethod
    def _blocked_state(code: str) -> VideoJobState:
        if code in {"provider_quota_exhausted", "provider_rate_exhausted"}:
            return VideoJobState.BLOCKED_PROVIDER_QUOTA
        if code in {"provider_access_denied", "api_disabled", "provider_model_unavailable"}:
            return VideoJobState.BLOCKED_PROVIDER_ACCESS
        if code in {"video_budget_exceeded"}:
            return VideoJobState.BLOCKED_BUDGET
        return VideoJobState.FAILED

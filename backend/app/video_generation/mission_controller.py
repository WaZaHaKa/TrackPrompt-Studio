from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from .assembly import execute_assembly
from .authorization import BatchAuthorization, authorization_phrase
from .continuity import derive_shot_seed
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
    VideoAnalysisSource,
    VideoArtifactSummary,
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
from .timeline import resolve_timeline

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

    def _analysis_directory(self, analysis_job_id: str) -> Path:
        try:
            canonical = str(UUID(analysis_job_id))
        except ValueError as exc:
            raise self._error(404, "analysis_not_found", "The selected analysis does not exist") from exc
        path = (self.analysis_jobs_root / canonical).resolve()
        if path.parent != self.analysis_jobs_root or not path.is_dir():
            raise self._error(404, "analysis_not_found", "The selected analysis does not exist")
        return path

    def catalog(self) -> VideoCatalog:
        analyses: list[VideoAnalysisSource] = []
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
                analyses.append(
                    VideoAnalysisSource(
                        analysis_job_id=canonical,
                        display_name=f"Analysis {canonical[:8]}",
                        story_plan_available=story,
                        shot_plan_available=shots,
                        retained_audio_available=(path / "source.bin").is_file(),
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
            packages.append(
                VideoContentPackage(
                    project_id=config.project_id,
                    title=config.title,
                    shot_count=len(config.required_shot_ids),
                    profiles=tuple(profiles),
                )
            )
        return VideoCatalog(
            analyses=tuple(analyses),
            packages=tuple(packages),
            pricing_snapshot_date=PRICE_SNAPSHOT_DATE,
        )

    async def create_plan(self, request: VideoPlanCreateRequest) -> VideoJobView:
        analysis_directory = self._analysis_directory(request.analysis_job_id)
        project_root = self._project_root(request.project_id)
        bucket = self._bucket(request.gcs_bucket)
        config_path = project_root / _PROFILE_FILES[request.profile_id]
        story_path = analysis_directory / "story-plan.json"
        shot_path = analysis_directory / "shot-plan.json"
        audio_path: Path | None = None
        reference_image_path: Path | None = None
        if request.audio_path:
            audio_path = Path(request.audio_path).expanduser().resolve()
        elif (analysis_directory / "source.bin").is_file():
            audio_path = (analysis_directory / "source.bin").resolve()
        if audio_path is not None and not audio_path.is_file():
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
                audio_master_path=audio_path,
                story_plan_path=story_path if story_path.is_file() else None,
                shot_plan_path=shot_path if shot_path.is_file() else None,
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
            audio_path=str(audio_path) if audio_path else None,
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
        return self._view(await self._save(record))

    def get(self, job_id: str) -> VideoJobView:
        return self._view(self._record(job_id))

    def jobs(self) -> list[VideoJobView]:
        return [self._view(item) for item in self.store.list_video_jobs(limit=100)]

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
            names = {
                "fcpxml": "trackprompt-timeline.fcpxml",
                "fcp7": "trackprompt-timeline.xml",
                "edl": "trackprompt-timeline.edl",
                "edit-sheet": "edit-sheet.csv",
                "markers": "davinci-markers.csv",
                "preview": "autonomous-preview-4k.mp4"
                if job.plan["profile"]["resolution"] == "4k"
                else "autonomous-preview-1080p.mp4",
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
            if job.audio_path:
                job = await self._resolve(job)
                job = await self._export(job)
                await self._assemble(job)
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
        if not job.audio_path:
            raise self._error(
                409,
                "audio_master_required",
                "Select the original local audio master before resolving the timeline",
            )
        clip_paths: dict[str, Path] = {}
        for shot in job.shots:
            if shot.review_state is VideoReviewState.REJECTED:
                raise self._error(
                    409,
                    "video_clip_rejected",
                    f"{shot.shot_id} is rejected and must be retried before timeline resolution",
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
            clip_paths[shot.shot_id] = Path(attempt.local_clip_path)
        project_root = self._project_root(job.project_id)
        profile = cast(dict[str, Any], job.plan["profile"])
        width, height = (3840, 2160) if profile["resolution"] == "4k" else (1920, 1080)
        shot_plan = self._analysis_directory(job.analysis_job_id) / "shot-plan.json"
        timeline_path = self._job_root(job) / "davinci" / str(profile["profileId"]) / "resolved-timeline.json"
        value = await asyncio.to_thread(
            resolve_timeline,
            project_id=job.project_id,
            title=job.title,
            audio_path=Path(job.audio_path),
            chapter_map_path=project_root / "chapter-map.json",
            clips_root=self._job_root(job) / "clips",
            clip_paths=clip_paths,
            output_width=width,
            output_height=height,
            fps=24,
            generated_clip_duration_seconds=int(profile["durationSeconds"]),
            target_edit_seconds=6.0,
            analysis_shot_plan_path=shot_plan if shot_plan.is_file() else None,
            ffprobe=str(self.ffprobe_path()) if self.ffprobe_path() else None,
        )
        atomic_write_json(timeline_path, value)
        return await self._save(
            job.model_copy(
                update={
                    "state": VideoJobState.TIMELINE_READY,
                    "timeline_path": str(timeline_path),
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
        expected_duration = float(cast(dict[str, Any], timeline["timeline"])["durationSeconds"])
        if abs(evidence.duration_seconds - expected_duration) > 0.75:
            raise self._error(
                422,
                "video_preview_duration_invalid",
                "The autonomous preview duration does not match the local audio clock",
            )
        atomic_write_json(export_root / "preview-verification.json", evidence.to_dict())
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
        return job

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
            audio_master_bound=bool(job.audio_path),
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

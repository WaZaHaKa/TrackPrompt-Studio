from __future__ import annotations

import asyncio
import hashlib
import json
import os
import struct
import subprocess
import time
import zlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from ..analysis_archive import AnalysisArchiveRepository
from ..config import Settings
from ..video_generation.audio import probe_audio
from .analysis import (
    ProjectAnalysis,
    find_archived_analysis,
    load_archived_analysis,
    run_and_archive_analysis,
)
from .archive import LocalVideoArchiveError, LocalVideoProjectArchive
from .comfyui import ComfyUIClient, ComfyUIHealthSnapshot, ComfyUIProviderError
from .models import (
    ComfyUIDevice,
    ComfyUIReadiness,
    LocalVideoDeletePreview,
    LocalVideoDeleteRequest,
    LocalVideoError,
    LocalVideoPrepareRequest,
    LocalVideoProjectSummary,
    LocalVideoProjectView,
    LocalVideoProviderState,
    LocalVideoQualificationState,
    LocalVideoShot,
    LocalVideoStage,
    LocalVideoWorkflowRequest,
    LocalVideoWorkflowView,
    QualificationCandidate,
    QualificationView,
)
from .output_view import publish_output_view
from .package import discover_project_packages, load_project_package
from .planning import build_shot_plan, build_story_plan
from .prompting import compile_prompts
from .qualification import (
    HardwareIdentity,
    QualificationCache,
    QualificationProbe,
    QualificationRunner,
    QualificationSample,
    qualify_hardware,
)
from .registry import WorkflowRegistry
from .timeline import analysis_boundary_candidates, resolve_timeline
from .workflow import WorkflowContractError, compile_i2v_workflow


def _boolean_environment(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Local video artifact is invalid")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_neutral_png(path: Path, *, width: int, height: int) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    row = b"\x00" + bytes((42, 48, 61)) * width
    raw = row * height
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, level=6))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _history_output_count(value: dict[str, Any], prompt_id: str) -> tuple[int, bool, str]:
    raw = value.get(prompt_id)
    item = raw if isinstance(raw, dict) else value
    status = item.get("status") if isinstance(item, dict) else None
    status_text = json.dumps(status, ensure_ascii=False).casefold() if status is not None else ""
    failed = any(token in status_text for token in ("error", "failed", "interrupted"))
    outputs = item.get("outputs") if isinstance(item, dict) else None
    count = 0
    if isinstance(outputs, dict):
        for node in outputs.values():
            if not isinstance(node, dict):
                continue
            for key in ("images", "frames"):
                files = node.get(key)
                if isinstance(files, list):
                    count += len(files)
    return count, failed, status_text


class _ComfyQualificationRunner(QualificationRunner):
    def __init__(
        self,
        *,
        client: ComfyUIClient,
        health: ComfyUIHealthSnapshot,
        workflow: dict[str, Any],
        workspace: Path,
    ) -> None:
        self.client = client
        self.health = health
        self.workflow = workflow
        self.workspace = workspace

    def _models(self, tier: str) -> tuple[str | None, str | None]:
        quant = "q5" if tier == "A14B-Q5_K_M" else "q4" if tier == "A14B-Q4_K_M" else "5b"
        matches = [
            name
            for name in self.health.model_names
            if quant in name.casefold() and any(token in name.casefold() for token in ("wan2.2", "wan2_2", "wan22"))
        ]
        high = next((name for name in matches if "high" in name.casefold()), None)
        low = next((name for name in matches if "low" in name.casefold()), None)
        return high, low

    async def run_probe(self, probe: QualificationProbe) -> QualificationSample:
        started = time.monotonic()
        high, low = self._models(probe.tier)
        if high is None or low is None:
            return QualificationSample(
                valid_output=False,
                elapsed_seconds=0,
                failure_code="candidate_model_pair_missing",
            )
        image = self.workspace / "qualification-input.png"
        if not image.is_file():
            _write_neutral_png(image, width=probe.width, height=probe.height)
        upload_name = f"trackprompt-qualification-{hashlib.sha256(probe.tier.encode()).hexdigest()[:12]}.png"
        try:
            returned_name = await asyncio.to_thread(
                self.client.upload_image,
                image,
                upload_name=upload_name,
                overwrite=False,
            )
            compiled, _mapping = compile_i2v_workflow(
                self.workflow,
                uploaded_image_name=returned_name,
                positive_prompt="subtle paper bird breathing motion, locked camera, stable ink lines",
                negative_prompt="text, watermark, flicker, morphing, camera shake",
                seed=24_081_001,
                width=probe.width,
                height=probe.height,
                length_frames=probe.length_frames,
                steps=probe.steps,
                cfg=3.5,
                expert_boundary=0.5,
                output_prefix=f"trackprompt/qualification/{probe.tier.casefold()}",
                high_model_name=high,
                low_model_name=low,
            )
            client_id = str(uuid4())
            prompt_id = await asyncio.to_thread(self.client.queue_prompt, compiled, client_id=client_id)
            peak_vram = 0
            error = False
            async for event in self.client.progress_events(
                prompt_id=prompt_id,
                client_id=client_id,
                idle_timeout_seconds=min(300, probe.timeout_seconds),
                total_timeout_seconds=probe.timeout_seconds,
            ):
                error = error or event.error
                try:
                    snapshot = await asyncio.to_thread(self.client.health)
                    for device in snapshot.devices:
                        if device.vram_total_bytes is not None and device.vram_free_bytes is not None:
                            peak_vram = max(peak_vram, device.vram_total_bytes - device.vram_free_bytes)
                except ComfyUIProviderError:
                    pass
            history = await asyncio.to_thread(self.client.history, prompt_id)
            frame_count, history_failed, status_text = _history_output_count(history, prompt_id)
            oom = "out of memory" in status_text or "cuda oom" in status_text
            return QualificationSample(
                valid_output=frame_count >= probe.length_frames,
                elapsed_seconds=time.monotonic() - started,
                peak_vram_bytes=peak_vram or None,
                cuda_oom=oom,
                process_crashed=error and not oom,
                failure_code="frame_sequence_incomplete" if frame_count < probe.length_frames else None,
            )
        except ComfyUIProviderError as exc:
            return QualificationSample(
                valid_output=False,
                elapsed_seconds=time.monotonic() - started,
                cuda_oom=exc.code == "provider_cuda_oom",
                process_crashed=exc.code in {"provider_unavailable", "provider_websocket_failed"},
                stalled=exc.code == "provider_idle_timeout",
                timed_out=exc.code == "provider_total_timeout",
                failure_code=exc.code,
            )
        except WorkflowContractError as exc:
            return QualificationSample(
                valid_output=False,
                elapsed_seconds=time.monotonic() - started,
                failure_code=exc.code,
            )


class LocalVideoController:
    def __init__(
        self,
        *,
        repository_root: Path,
        state_root: Path,
        analysis_data_root: Path,
        ffmpeg_path: Callable[[], Path | None],
        ffprobe_path: Callable[[], Path | None],
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.projects_root = self.repository_root / "video-projects" / "local"
        self.state_root = state_root.resolve()
        base_settings = Settings.from_env()
        configured_analysis_root = analysis_data_root.resolve()
        self.settings = replace(
            base_settings,
            data_dir=configured_analysis_root,
            model_cache_dir=configured_analysis_root / "models",
        )
        self.analysis_archive = AnalysisArchiveRepository(self.settings.data_dir)
        self.archive = LocalVideoProjectArchive(self.state_root)
        self.registry = WorkflowRegistry(self.archive.state_root / "workflows")
        self.qualification_cache = QualificationCache(self.archive.state_root / "qualification-cache")
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.provider_url = os.getenv("TRACKPROMPT_COMFYUI_URL", "http://127.0.0.1:8188")
        self.allow_non_loopback = _boolean_environment("TRACKPROMPT_COMFYUI_ALLOW_NON_LOOPBACK", False)
        raw_root = os.getenv("TRACKPROMPT_COMFYUI_ROOT", "").strip()
        self.comfyui_root = Path(raw_root).resolve() if raw_root else None
        self._lock = asyncio.Lock()

    def _client(self) -> ComfyUIClient:
        return ComfyUIClient(
            self.provider_url,
            allow_non_loopback=self.allow_non_loopback,
        )

    def catalog(self, *, query: str | None = None) -> list[LocalVideoProjectSummary]:
        archived = {item.project_id: item for item in self.archive.list(query=query)}
        normalized_query = query.strip().casefold() if query else ""
        for package in discover_project_packages(self.projects_root):
            if package.project_id in archived:
                continue
            if normalized_query and normalized_query not in package.project_id and normalized_query not in package.title.casefold():
                continue
            audio = package.audio_files()
            archived[package.project_id] = LocalVideoProjectSummary(
                project_id=package.project_id,
                title=package.title,
                status="package_ready" if len(audio) == 1 else "audio_required",
            )
        return sorted(archived.values(), key=lambda item: (item.updated_at is None, item.title.casefold()))

    @staticmethod
    def _missing_node_roles(health: ComfyUIHealthSnapshot) -> list[str]:
        classes = {key.casefold() for key in health.object_info}
        checks = {
            "load start image": any("loadimage" == name for name in classes),
            "encode prompts": any("textencode" in name for name in classes),
            "create Wan image-to-video latent": any("wan" in name and "video" in name for name in classes),
            "sample video": any("sampler" in name or "sample" in name for name in classes),
            "save video": any("save" in name or "videocombine" in name for name in classes),
        }
        return [role for role, available in checks.items() if not available]

    async def readiness(self) -> ComfyUIReadiness:
        client = self._client()
        try:
            health = await asyncio.to_thread(client.health)
        except ComfyUIProviderError as exc:
            return ComfyUIReadiness(
                configured="TRACKPROMPT_COMFYUI_URL" in os.environ,
                reachable=False,
                local_endpoint=client.base_url,
                setup_required=True,
                local_api_contacted=True,
                provider_state=LocalVideoProviderState.COMFYUI_MISSING,
                status_message="Managed ComfyUI is not reachable",
                error=LocalVideoError(
                    code=exc.code,
                    summary=exc.safe_message,
                    action="Start or configure the local ComfyUI service, then run readiness again.",
                    retryable=exc.retryable,
                ),
            )
        missing = self._missing_node_roles(health)
        classes = set(health.object_info)
        gguf_ready = "UnetLoaderGGUF" in classes
        workflows = self.registry.list()
        flux_workflow_ready = any(item.capability == "keyframe-flux" for item in workflows)
        wan_workflow_ready = any(item.capability == "wan22-i2v" for item in workflows)
        required_models = {
            "flux1-schnell-fp8.safetensors",
            "wan2.2_i2v_high_noise_14B_Q5_K_M.gguf",
            "wan2.2_i2v_low_noise_14B_Q5_K_M.gguf",
            "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "wan_2.1_vae.safetensors",
        }
        discovered = set(health.model_names)
        models_ready = required_models.issubset(discovered)
        summaries = self.archive.list()
        selected_tier = next((item.selected_tier for item in summaries if item.selected_tier), None)
        qualification_state = (
            {
                "A14B-Q5_K_M": LocalVideoQualificationState.Q5_QUALIFIED,
                "A14B-Q4_K_M": LocalVideoQualificationState.Q4_QUALIFIED,
                "TI2V-5B": LocalVideoQualificationState.FALLBACK_TIER_QUALIFIED,
            }.get(selected_tier, LocalVideoQualificationState.NOT_RUN)
            if selected_tier is not None
            else LocalVideoQualificationState.NOT_RUN
        )
        post_ready = self._post_processing_ready()
        production_ready = bool(
            not missing
            and gguf_ready
            and flux_workflow_ready
            and wan_workflow_ready
            and models_ready
            and selected_tier
            and post_ready
        )
        if not gguf_ready:
            provider_state = LocalVideoProviderState.GGUF_NODE_MISSING
            status_message = "ComfyUI-GGUF is not available"
        elif not models_ready:
            provider_state = LocalVideoProviderState.MODELS_MISSING
            status_message = "Required FLUX or Wan model files are missing"
        elif not flux_workflow_ready:
            provider_state = LocalVideoProviderState.FLUX_WORKFLOW_UNAVAILABLE
            status_message = "The semantic FLUX API workflow is not registered"
        elif not wan_workflow_ready:
            provider_state = LocalVideoProviderState.WAN_WORKFLOW_UNAVAILABLE
            status_message = "The semantic Wan API workflow is not registered"
        elif selected_tier is None:
            provider_state = LocalVideoProviderState.QUALIFICATION_NOT_RUN
            status_message = "Real local qualification has not been imported"
        elif not post_ready:
            provider_state = LocalVideoProviderState.POST_PROCESSING_MISSING
            status_message = "Local interpolation or upscaling is unavailable"
        else:
            provider_state = LocalVideoProviderState.FULLY_PRODUCTION_READY
            status_message = "LOCAL VIDEO PROVIDER READY"
        return ComfyUIReadiness(
            configured=True,
            reachable=True,
            local_endpoint=client.base_url,
            comfyui_version=health.version,
            node_count=health.node_count,
            devices=[
                ComfyUIDevice(
                    name=item.name,
                    type=item.type,
                    vram_total_bytes=item.vram_total_bytes,
                    vram_free_bytes=item.vram_free_bytes,
                )
                for item in health.devices
            ],
            missing_node_roles=missing,
            discovered_model_names=sorted(required_models & discovered, key=str.casefold),
            setup_required=not production_ready,
            local_api_contacted=True,
            provider_state=provider_state,
            qualification_state=qualification_state,
            gguf_node_available=gguf_ready,
            flux_workflow_available=flux_workflow_ready,
            wan_workflow_available=wan_workflow_ready,
            models_available=models_ready,
            qualification_completed=selected_tier is not None,
            selected_tier=selected_tier,
            post_processing_ready=post_ready,
            production_ready=production_ready,
            status_message=status_message,
        )

    def _post_processing_ready(self) -> bool:
        paths = [
            os.getenv("TRACKPROMPT_RIFE_PATH", "").strip(),
            os.getenv("TRACKPROMPT_REALESRGAN_PATH", "").strip(),
        ]
        if all(value and Path(value).is_file() for value in paths):
            return True
        if self.comfyui_root is None:
            return False
        lock = self.comfyui_root / "trackprompt-local-generation-tools-lock.json"
        try:
            value = _read_json(lock)
            rife = Path(str(value["rife"]["executable"]))
            realesrgan = Path(str(value["realesrgan"]["executable"]))
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
            return False
        return rife.is_file() and realesrgan.is_file()

    def _effective_settings(self) -> Settings:
        ffmpeg = self.ffmpeg_path()
        ffprobe = self.ffprobe_path()
        return replace(
            self.settings,
            ffmpeg_path=str(ffmpeg) if ffmpeg else self.settings.ffmpeg_path,
            ffprobe_path=str(ffprobe) if ffprobe else self.settings.ffprobe_path,
        )

    async def prepare(self, request: LocalVideoPrepareRequest) -> LocalVideoProjectView:
        async with self._lock:
            package = load_project_package(self.projects_root, request.project_id)
            source = package.require_audio()
            settings = self._effective_settings()
            evidence = await asyncio.to_thread(probe_audio, source, ffprobe=settings.ffprobe_path)
            analysis: ProjectAnalysis | None
            if request.analysis_id:
                analysis = await asyncio.to_thread(
                    load_archived_analysis,
                    self.analysis_archive,
                    request.analysis_id,
                    expected_audio_sha256=evidence.sha256,
                )
            else:
                analysis = await asyncio.to_thread(
                    find_archived_analysis,
                    self.analysis_archive,
                    audio_sha256=evidence.sha256,
                )
                if analysis is None:
                    analysis = await asyncio.to_thread(
                        run_and_archive_analysis,
                        source_audio=source,
                        audio_sha256=evidence.sha256,
                        settings=settings,
                        archive=self.analysis_archive,
                    )
            assert analysis is not None
            candidates = analysis_boundary_candidates(analysis.value)
            timeline = resolve_timeline(
                package,
                actual_duration_seconds=evidence.duration_seconds,
                candidates=candidates,
            )
            prompts = compile_prompts(package)
            story_plan = build_story_plan(package, timeline)
            shot_plan = build_shot_plan(package, timeline, prompts)
            revision_id = await asyncio.to_thread(
                self.archive.create_revision,
                package=package,
                audio=evidence,
                analysis=analysis.value,
                analysis_id=analysis.analysis_id,
                story_plan=story_plan,
                shot_plan=shot_plan,
                timeline=[item.model_dump(mode="json", by_alias=True) for item in timeline],
                prompts=[item.to_dict() for item in prompts],
            )
            await asyncio.to_thread(
                self.analysis_archive.register_dependency,
                analysis.analysis_id,
                dependent_kind="local-video-project",
                dependent_id=f"{package.project_id}:{revision_id}",
                snapshot_complete=True,
            )
            await asyncio.to_thread(
                publish_output_view,
                package=package,
                revision_id=revision_id,
                analysis_id=analysis.analysis_id,
                analysis=analysis.value,
                audio_sha256=evidence.sha256,
                duration_seconds=evidence.duration_seconds,
                timeline=timeline,
            )
            provider = await self.readiness()
            status = "blocked_provider_setup" if provider.setup_required else "provider_ready"
            await asyncio.to_thread(self.archive.set_status, package.project_id, status)
            return self.get(package.project_id).model_copy(update={"provider": provider})

    def get(self, project_id: str) -> LocalVideoProjectView:
        package = load_project_package(self.projects_root, project_id)
        current = self.archive.current(project_id)
        if current is None:
            return LocalVideoProjectView(project_id=project_id, title=package.title)
        timeline_path = self.archive.artifact(project_id, "analysis/timeline.json")
        timeline_value = json.loads(timeline_path.read_text(encoding="utf-8")) if timeline_path else []
        timeline = timeline_value if isinstance(timeline_value, list) else []
        status = str(current["status"])
        stage = {
            "analysis_archived": LocalVideoStage.BLOCKED,
            "blocked_provider_setup": LocalVideoStage.BLOCKED,
            "provider_ready": LocalVideoStage.REFERENCES,
            "qualified": LocalVideoStage.REFERENCES,
            "generating_references": LocalVideoStage.REFERENCES,
            "generating_keyframes": LocalVideoStage.KEYFRAMES,
            "generating_video": LocalVideoStage.VIDEO,
            "post_processing": LocalVideoStage.POST,
            "editing": LocalVideoStage.EDIT,
            "final_qc": LocalVideoStage.QC,
            "complete": LocalVideoStage.COMPLETE,
            "cancelled": LocalVideoStage.CANCELLED,
            "failed": LocalVideoStage.FAILED,
        }.get(status, LocalVideoStage.BLOCKED)
        selected_tier = current.get("selected_tier")
        qualification: QualificationView | None = None
        qualification_path = self.archive.artifact(project_id, "manifests/qualification.json")
        if qualification_path is not None:
            try:
                qualification = QualificationView.model_validate(_read_json(qualification_path))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                qualification = None
        error = None
        if status == "blocked_provider_setup":
            error = LocalVideoError(
                code="local_comfyui_setup_required",
                summary="The project analysis is safely archived, but local ComfyUI is not ready.",
                action="Run the local provider setup plan, register current API workflows, and qualify the GPU.",
                retryable=True,
            )
        return LocalVideoProjectView(
            project_id=project_id,
            title=package.title,
            revision_id=str(current["current_revision_id"]),
            package_digest=str(current["package_digest"]),
            audio_hash_prefix=str(current["audio_sha256"])[:12],
            audio_duration_seconds=float(current["audio_duration_seconds"]),
            stage=stage,
            status_message=status.replace("_", " "),
            completed_units=0,
            total_units=len(package.shots),
            timeline=timeline,
            shots=[LocalVideoShot(shot_id=str(shot["shotId"]), order=index) for index, shot in enumerate(package.shots, 1)],
            analysis_archived=True,
            qualification=qualification,
            can_start=status == "qualified" and selected_tier is not None,
            can_resume=status in {"cancelled", "failed"},
            can_cancel=status.startswith("generating_") or status in {"post_processing", "editing", "final_qc"},
            final_qc_passed=status == "complete",
            output_available=status == "complete",
            error=error,
        )

    def install_workflow(self, request: LocalVideoWorkflowRequest) -> LocalVideoWorkflowView:
        return self.registry.install(request)

    def workflows(self) -> list[LocalVideoWorkflowView]:
        return self.registry.list()

    def record_qualification(self, project_id: str) -> QualificationView:
        package = load_project_package(self.projects_root, project_id)
        current = self.archive.current(project_id)
        if current is None or not isinstance(current.get("storage_key"), str):
            raise KeyError("Local video project not found")
        report_path = (package.root / "outputs" / "qualification" / "qualification-run.json").resolve()
        final_path = (package.root / "outputs" / "qualification" / "qualification-1080p24.mp4").resolve()
        if package.root not in report_path.parents or package.root not in final_path.parents:
            raise LocalVideoArchiveError("Qualification artifacts escaped the project root")
        if not report_path.is_file() or report_path.stat().st_size > 2_000_000:
            raise LocalVideoArchiveError("The canonical qualification report is unavailable")
        report = _read_json(report_path)
        if report.get("status") != "LOCAL_ANIME_PIPELINE_READY":
            raise LocalVideoArchiveError("The canonical qualification report did not pass")
        wan = report.get("wan")
        post = report.get("post")
        final = post.get("final") if isinstance(post, dict) else None
        if not isinstance(wan, dict) or not isinstance(final, dict):
            raise LocalVideoArchiveError("The canonical qualification report is invalid")
        tier = wan.get("tier")
        if tier not in {"A14B-Q5_K_M", "A14B-Q4_K_M", "TI2V-5B"}:
            raise LocalVideoArchiveError("The qualification tier is unsupported")
        if (
            final.get("width") != 1920
            or final.get("height") != 1080
            or float(final.get("fps", 0)) != 24.0
            or final.get("decodePassed") is not True
            or not final_path.is_file()
            or final_path.stat().st_size <= 0
            or final.get("sha256") != _sha256(final_path)
        ):
            raise LocalVideoArchiveError("The qualification video does not match its exact QC record")
        completed_raw = report.get("completedAt")
        try:
            completed_at = datetime.fromisoformat(str(completed_raw)).astimezone(UTC)
        except ValueError as exc:
            raise LocalVideoArchiveError("The qualification completion time is invalid") from exc
        report_digest = _sha256(report_path)
        tiers = ("A14B-Q5_K_M", "A14B-Q4_K_M", "TI2V-5B")
        candidates = [
            QualificationCandidate(
                tier=cast(Literal["A14B-Q5_K_M", "A14B-Q4_K_M", "TI2V-5B"], candidate),
                state="passed" if candidate == tier else "skipped",
                reason=(
                    "Real FLUX-to-Wan-to-post qualification passed"
                    if candidate == tier
                    else f"Skipped because {tier} passed"
                ),
                peak_vram_bytes=(
                    int(wan["peakVramBytes"])
                    if candidate == tier and isinstance(wan.get("peakVramBytes"), int)
                    else None
                ),
                peak_system_memory_bytes=(
                    int(wan["peakSystemMemoryBytes"])
                    if candidate == tier and isinstance(wan.get("peakSystemMemoryBytes"), int)
                    else None
                ),
                elapsed_seconds=(
                    float(wan["elapsedSeconds"])
                    if candidate == tier and isinstance(wan.get("elapsedSeconds"), (int, float))
                    else None
                ),
            )
            for candidate in tiers
        ]
        view = QualificationView(
            cache_key=report_digest,
            selected_tier=str(tier),
            cached=False,
            completed_at=completed_at,
            candidates=candidates,
        )
        revision_root = (self.archive.state_root / str(current["storage_key"])).resolve()
        if self.archive.state_root not in revision_root.parents:
            raise LocalVideoArchiveError("The current revision storage identity is invalid")
        target = revision_root / "manifests" / "qualification.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.partial-{uuid4().hex}")
        try:
            temporary.write_text(
                json.dumps(view.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        self.archive.set_status(project_id, "qualified", selected_tier=str(tier))
        return view

    def _configured_model_hashes(self, health: ComfyUIHealthSnapshot) -> dict[str, str]:
        if self.comfyui_root is None:
            raise LocalVideoArchiveError("TRACKPROMPT_COMFYUI_ROOT is required for hash-bound qualification")
        model_root = (self.comfyui_root / "models").resolve()
        if self.comfyui_root not in model_root.parents or not model_root.is_dir():
            raise LocalVideoArchiveError("The configured ComfyUI model root is unavailable")
        wanted = {
            name.casefold()
            for name in health.model_names
            if any(token in name.casefold() for token in ("wan2.2", "wan2_2", "wan22"))
        }
        result: dict[str, str] = {}
        for path in model_root.rglob("*"):
            if not path.is_file() or path.name.casefold() not in wanted:
                continue
            result[path.name] = _sha256(path)
        if not result:
            raise LocalVideoArchiveError("No configured Wan2.2 model file could be hash-bound")
        return result

    @staticmethod
    def _gpu_identity(health: ComfyUIHealthSnapshot) -> tuple[str, int, str]:
        device = next((item for item in health.devices if item.vram_total_bytes), None)
        name = device.name if device else "unknown-local-gpu"
        vram = device.vram_total_bytes if device and device.vram_total_bytes else 0
        driver = "unknown"
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=driver_version",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                shell=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                driver = result.stdout.strip().splitlines()[0][:80]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return name, vram, driver

    def _custom_node_revisions(self) -> dict[str, str]:
        if self.comfyui_root is None:
            return {}
        root = (self.comfyui_root / "custom_nodes").resolve()
        if self.comfyui_root not in root.parents or not root.is_dir():
            return {}
        revisions: dict[str, str] = {}
        for directory in root.iterdir():
            head = directory / ".git" / "HEAD"
            if not directory.is_dir() or not head.is_file():
                continue
            try:
                value = head.read_text(encoding="utf-8").strip()
                if value.startswith("ref: "):
                    ref = directory / ".git" / value[5:]
                    value = ref.read_text(encoding="utf-8").strip() if ref.is_file() else value
                revisions[directory.name] = value[:160]
            except (OSError, UnicodeError):
                continue
        return revisions

    async def qualify(self, project_id: str, workflow_id: str) -> QualificationView:
        async with self._lock:
            current = self.archive.current(project_id)
            if current is None:
                raise KeyError("Local video project not found")
            workflow_view, workflow = self.registry.load(workflow_id)
            if workflow_view.capability != "wan22-i2v":
                raise ValueError("Hardware qualification requires a Wan2.2 I2V workflow")
            client = self._client()
            health = await asyncio.to_thread(client.health)
            model_hashes = await asyncio.to_thread(self._configured_model_hashes, health)
            gpu_name, vram, driver = await asyncio.to_thread(self._gpu_identity, health)
            identity = HardwareIdentity(
                gpu_name=gpu_name,
                vram_bytes=vram,
                driver_version=driver,
                comfyui_revision=health.version or "unknown",
                custom_node_revisions=self._custom_node_revisions(),
                model_sha256=model_hashes,
            )
            runner = _ComfyQualificationRunner(
                client=client,
                health=health,
                workflow=workflow,
                workspace=self.archive.state_root / "qualification-work" / identity.cache_key(),
            )
            view = await qualify_hardware(identity, runner, self.qualification_cache)
            if view.selected_tier:
                await asyncio.to_thread(
                    self.archive.set_status,
                    project_id,
                    "qualified",
                    selected_tier=view.selected_tier,
                )
            return view

    def delete_preview(self, project_id: str, revision_id: str | None = None) -> LocalVideoDeletePreview:
        return self.archive.delete_preview(project_id, revision_id)

    def explicit_delete(self, project_id: str, request: LocalVideoDeleteRequest) -> None:
        self.archive.explicit_delete(
            project_id,
            revision_id=request.revision_id,
            confirmation=request.confirmation,
        )

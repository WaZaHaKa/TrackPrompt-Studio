from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .lyrics import create_lyrics_adapter
from .model_cache import verify_demucs_model_manifest
from .privacy import secure_private_directory
from .prompting.local_writer import create_prompt_writer
from .schemas import (
    CapabilitiesResponse,
    DeepAdapterCapability,
    FFmpegCapability,
    GPUTaskQueueCapability,
    LimitsCapability,
    ModeCapability,
    OptionalAnalyzerCapability,
)
from .subprocess_utils import ProcessTimedOut, ProcessWasCancelled, run_process_bounded
from .tagging.music import create_music_tagger


@dataclass(frozen=True, slots=True)
class ToolStatus:
    available: bool
    version: str | None


@dataclass(frozen=True, slots=True)
class TorchDeviceStatus:
    torch_installed: bool
    torch_version: str | None
    cuda_build_support: bool
    cuda_runtime_available: bool
    gpu_device_name: str | None
    selected_device: str
    fallback_reason: str | None


def inspect_torch_device(settings: Settings) -> TorchDeviceStatus:
    if importlib.util.find_spec("torch") is None:
        return TorchDeviceStatus(False, None, False, False, None, "cpu", "PyTorch is not installed.")
    try:
        version = importlib.metadata.version("torch")
    except importlib.metadata.PackageNotFoundError:
        version = None
    try:
        import torch

        cuda_build = torch.version.cuda is not None
        cuda_available = bool(torch.cuda.is_available())
        device_name = torch.cuda.get_device_name(0) if cuda_available else None
    except (ImportError, RuntimeError, OSError):
        cuda_build = False
        cuda_available = False
        device_name = None
    if settings.demucs_device == "cpu":
        return TorchDeviceStatus(True, version, cuda_build, cuda_available, device_name, "cpu", None)
    if cuda_available:
        return TorchDeviceStatus(True, version, cuda_build, True, device_name, "cuda", None)
    reason = (
        "The installed PyTorch build has no CUDA support."
        if not cuda_build
        else "CUDA is present in the PyTorch build but unavailable to this runtime."
    )
    return TorchDeviceStatus(True, version, cuda_build, False, None, "cpu", reason)


def inspect_tool(executable: str) -> ToolStatus:
    resolved = shutil.which(executable) if not any(mark in executable for mark in ("/", "\\")) else executable
    if not resolved:
        return ToolStatus(False, None)
    try:
        result = run_process_bounded(
            [resolved, "-version"],
            timeout_seconds=5,
            stdout_limit=8_000,
            stderr_limit=8_000,
        )
    except (OSError, ProcessTimedOut):
        return ToolStatus(False, None)
    if result.returncode != 0 or result.stdout_exceeded or result.stderr_exceeded:
        return ToolStatus(False, None)
    output = result.stdout or result.stderr
    first_line = output.splitlines()[0].decode("utf-8", errors="replace") if output else None
    return ToolStatus(True, first_line[:160] if first_line else None)


def _reviewed_demucs_weights(settings: Settings) -> list[Path]:
    files, _reason = verify_demucs_model_manifest(
        settings.demucs_model_dir,
        settings.demucs_model_name,
    )
    return files


def demucs_ready(settings: Settings) -> bool:
    return (
        settings.enable_demucs
        and importlib.util.find_spec("demucs") is not None
        and bool(_reviewed_demucs_weights(settings))
    )


def deep_adapters(settings: Settings) -> list[DeepAdapterCapability]:
    demucs_installed = settings.enable_demucs and importlib.util.find_spec("demucs") is not None
    weights_available = (
        demucs_installed and bool(_reviewed_demucs_weights(settings))
    )
    ready = settings.enable_demucs and demucs_installed and weights_available
    device = inspect_torch_device(settings)
    if ready:
        reason = "Enabled with locally available model weights; no model download is attempted."
    elif not settings.enable_demucs:
        reason = "Disabled by default. Set ENABLE_DEMUCS=true only after installing and caching reviewed weights."
    elif not demucs_installed:
        reason = "Optional Demucs dependency is not installed."
    else:
        reason = "No checksum-verified weights from demucs-models.json were found; downloads are never started silently."
    return [
        DeepAdapterCapability(
            id="demucs-four-stem",
            name="Demucs four-stem separator",
            available=ready,
            enabled=ready,
            reason=reason,
            disk_impact_mb=5000,
            license="MIT code; model-weight terms must be reviewed before enabling",
            torch_installed=device.torch_installed,
            torch_version=device.torch_version,
            cuda_build_support=device.cuda_build_support,
            cuda_runtime_available=device.cuda_runtime_available,
            gpu_device_name=device.gpu_device_name,
            selected_device=device.selected_device,
            fallback_reason=device.fallback_reason,
        )
    ]


def get_capabilities(settings: Settings) -> CapabilitiesResponse:
    ffmpeg = inspect_tool(settings.ffmpeg_path)
    ffprobe = inspect_tool(settings.ffprobe_path)
    adapters = deep_adapters(settings)
    deep_available = any(adapter.available and adapter.enabled for adapter in adapters)
    genre = create_music_tagger(settings).capability()
    lyrics = create_lyrics_adapter(settings).capability()
    prompt_writer = create_prompt_writer(settings).capability()
    return CapabilitiesResponse(
        fast_mode=ModeCapability(
            available=ffmpeg.available and ffprobe.available,
            features=[
                "media inspection",
                "waveform peaks",
                "signal quality",
                "rhythm and tempo",
                "key and approximate harmony",
                "structure",
                "timbre",
                "stereo and production",
                "deterministic prompt composition",
            ],
        ),
        deep_mode=ModeCapability(
            available=deep_available,
            will_fallback=not deep_available,
            features=["optional four-stem separation", "per-stem descriptors", "enhanced vocal presence"],
            adapters=adapters,
        ),
        ffmpeg=FFmpegCapability(available=ffmpeg.available, version=ffmpeg.version),
        ffprobe=FFmpegCapability(available=ffprobe.available, version=ffprobe.version),
        limits=LimitsCapability(
            max_upload_mb=settings.max_upload_mb,
            max_duration_seconds=settings.max_duration_seconds,
            job_ttl_minutes=settings.job_ttl_minutes,
            max_pending_jobs=settings.max_pending_jobs,
        ),
        optional_analyzers=[
            OptionalAnalyzerCapability(
                id="local-musical-descriptors",
                name="Local full-feature adapters",
                features=[
                    "genre and musical tags",
                    "instrument families",
                    "vocal register and delivery",
                    "melody contour",
                    "semantic section labels",
                ],
                available=genre.available or lyrics.available or prompt_writer.available,
                reason="Each adapter reports readiness independently; unavailable adapters never fabricate output.",
                license="See the independent adapter capabilities and docs/model-licenses.md",
            )
        ],
        genre_tagger=genre,
        lyrics_adapter=lyrics,
        prompt_writer=prompt_writer,
        gpu_task_queue=GPUTaskQueueCapability(workers=settings.gpu_task_workers),
        network_features_enabled=False,
    )


class DeepAdapterError(RuntimeError):
    pass


def run_demucs(
    source: Path,
    output_dir: Path,
    settings: Settings,
    cancel_requested: Callable[[], bool] | None = None,
    device: str | None = None,
) -> dict[str, Path]:
    """Run a preinstalled Demucs model strictly from the configured local repo.

    This function cannot make a model available: callers must first satisfy
    ``demucs_ready``. Passing ``--repo`` confines resolution to reviewed local
    weights and the offline environment flags prevent common model clients from
    attempting network access.
    """
    if not demucs_ready(settings):
        raise DeepAdapterError("The local Demucs adapter is not enabled and ready.")
    output_dir.mkdir(parents=True, exist_ok=True)
    secure_private_directory(output_dir)
    selected_device = device or inspect_torch_device(settings).selected_device
    if selected_device not in {"cpu", "cuda"}:
        raise DeepAdapterError("The selected Demucs device is invalid.")
    args = [
        sys.executable,
        "-m",
        "demucs.separate",
        "--name",
        settings.demucs_model_name,
        "--repo",
        str(settings.demucs_model_dir),
        "--out",
        str(output_dir),
        "--jobs",
        "1",
        "--segment",
        "7",
        "--device",
        selected_device,
        str(source),
    ]
    environment = os.environ.copy()
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TORCH_HOME": str(settings.demucs_model_dir),
            # PyTorch 2.6+ defaults torch.load to weights_only=True, while
            # Demucs 4.0.1 loads its own model class from the checkpoint. This
            # override is scoped to the subprocess after the exact repository
            # has passed the complete SHA-256 allowlist above.
            "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1",
            "NO_PROXY": "*",
        }
    )
    try:
        result = run_process_bounded(
            args,
            timeout_seconds=max(settings.subprocess_timeout_seconds, 600),
            cancel_requested=cancel_requested,
            environment=environment,
            stdout_limit=64_000,
            stderr_limit=64_000,
        )
    except ProcessWasCancelled as exc:
        raise DeepAdapterError("Local stem separation was cancelled.") from exc
    except ProcessTimedOut as exc:
        raise DeepAdapterError("Local stem separation timed out.") from exc
    except OSError as exc:
        raise DeepAdapterError("Local stem separation could not start.") from exc
    if result.returncode != 0 or result.stdout_exceeded or result.stderr_exceeded:
        raise DeepAdapterError("Local stem separation failed without producing usable private stems.")
    stem_root = output_dir / settings.demucs_model_name / source.stem
    stems = {name: stem_root / f"{name}.wav" for name in ("vocals", "drums", "bass", "other")}
    resolved_output = output_dir.resolve()
    if not all(path.resolve().is_relative_to(resolved_output) and path.is_file() for path in stems.values()):
        raise DeepAdapterError("Local stem separator did not produce the expected private stems.")
    return stems

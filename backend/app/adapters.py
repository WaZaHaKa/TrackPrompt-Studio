from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import Settings
from .lyrics import create_lyrics_adapter
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
from .tagging import create_music_tagger


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


@lru_cache(maxsize=64)
def _sha256_at_signature(
    path_text: str,
    _size: int,
    _mtime_ns: int,
    _ctime_ns: int,
) -> str:
    """Hash a stable local file signature once, avoiding repeated GB-scale reads."""
    digest = hashlib.sha256()
    with Path(path_text).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reviewed_demucs_weights(settings: Settings) -> list[Path]:
    """Return verified files only when the selected repository is fully manifested."""
    cache_root = settings.demucs_model_dir.resolve()
    manifest_path = cache_root / "demucs-models.json"
    try:
        if (
            not manifest_path.is_file()
            or manifest_path.resolve().parent != cache_root
            or manifest_path.stat().st_size > 1_000_000
        ):
            return []
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    model = models.get(settings.demucs_model_name) if isinstance(models, dict) else None
    files = model.get("files") if isinstance(model, dict) else None
    if not isinstance(files, dict) or not files:
        return []
    verified: list[Path] = []
    listed_paths: set[Path] = set()
    for relative_name, expected_hash in files.items():
        if (
            not isinstance(relative_name, str)
            or not isinstance(expected_hash, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None
        ):
            return []
        relative = Path(relative_name)
        if relative.is_absolute() or ".." in relative.parts:
            return []
        candidate = (cache_root / relative).resolve()
        if not candidate.is_relative_to(cache_root) or candidate == manifest_path.resolve():
            return []
        try:
            before = candidate.stat()
            digest = _sha256_at_signature(
                str(candidate),
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            after = candidate.stat()
        except OSError:
            return []
        if (
            not candidate.is_file()
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or digest.casefold() != expected_hash.casefold()
        ):
            return []
        verified.append(candidate)
        listed_paths.add(candidate)
    try:
        repository_files = {
            path.resolve()
            for path in cache_root.rglob("*")
            if path.is_file() and path.resolve() != manifest_path.resolve()
        }
    except OSError:
        return []
    # Demucs receives the repository root. Requiring completeness prevents it
    # from resolving an unreviewed checkpoint or config that was not hashed.
    if repository_files != listed_paths:
        return []
    return verified


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

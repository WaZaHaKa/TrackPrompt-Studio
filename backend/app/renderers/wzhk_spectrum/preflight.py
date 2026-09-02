from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ...adapters import inspect_tool
from ...config import Settings
from ...privacy import secure_private_directory
from ..schemas import (
    GenerativeGeometrySummary,
    RendererAvailabilityState,
    RendererContractSummary,
    RendererDescriptor,
    RendererRequirement,
    SpectrumDesignPresetSummary,
    SpectrumTimelineSectionSummary,
)
from .contracts import SpectrumRenderRequest
from .design import SpectrumDesignError, SpectrumDesignPreset, load_design_preset
from .production import (
    CaptureProviderCapabilities,
    GeometryCapabilityEvidence,
    SpectrumMasterTiming,
    SpectrumProductionAvailability,
    SpectrumProductionError,
    inspect_ffmpeg_capture_capabilities,
    probe_media_file,
    resolve_master_timing,
)

RENDERER_ID = "wzhk-spectrum"
EXPECTED_VENDOR_COMMIT = "553aa755ef0cc394259fb1a55560f1b31864d2e0"
SUPPORTED_LOGO_EXTENSIONS = (".png", ".svg", ".webp", ".jpg", ".jpeg")
SUPPORTED_AUDIO_EXTENSIONS = (".wav", ".flac", ".aiff", ".aif", ".mp3")


class SpectrumPathError(ValueError):
    """Raised when a renderer path could escape its configured root."""


@dataclass(frozen=True, slots=True)
class SpectrumPaths:
    repository_root: Path
    data_root: Path

    @property
    def vendor_root(self) -> Path:
        return self.repository_root / "vendor" / "wzhk-spectrum-visualizer"

    @property
    def contract_path(self) -> Path:
        return (
            self.repository_root
            / "tools"
            / "wzhk-spectrum"
            / "config"
            / "scattered.wzhk-spectrum.json"
        )

    @property
    def design_preset_path(self) -> Path:
        return (
            self.repository_root
            / "tools"
            / "wzhk-spectrum"
            / "config"
            / "scattered.visual-preset.json"
        )

    @property
    def logo_directory(self) -> Path:
        return self.data_root / "wzhk-spectrum" / "assets" / "logo"

    @property
    def track_directory(self) -> Path:
        return self.data_root / "wzhk-spectrum" / "assets" / "track"

    @property
    def jobs_root(self) -> Path:
        return self.data_root / "wzhk-spectrum" / "jobs"

    @property
    def geometry_runtime_root(self) -> Path:
        return self.repository_root / "tools" / "wzhk-spectrum" / "runtime"


@dataclass(frozen=True, slots=True)
class SpectrumInspection:
    platform_name: str | None = None
    rainmeter_available: bool | None = None
    rainmeter_path: Path | None = None
    ffmpeg_available: bool | None = None
    ffmpeg_version: str | None = None
    ffprobe_available: bool | None = None
    ffplay_available: bool | None = None
    ffplay_path: Path | None = None
    capture_provider_available: bool | None = None
    capture_encoder: str | None = None
    nvenc_available: bool | None = None
    alpha_composition_available: bool | None = None
    monitor_capture_available: bool | None = None
    chroma_composition_available: bool | None = None
    geometry_capability: GeometryCapabilityEvidence | None = None
    duration_reader: Callable[[Path], float] | None = None


@dataclass(frozen=True, slots=True)
class SpectrumPreflightOutcome:
    descriptor: RendererDescriptor
    contract: SpectrumRenderRequest | None
    design_preset: SpectrumDesignPreset | None
    logo_path: Path | None
    master_audio_path: Path | None
    measured_audio_duration_seconds: float | None
    master_timing: SpectrumMasterTiming | None
    vendor_source_hash: str | None
    provenance: dict[str, Any] | None
    ffmpeg_version: str | None
    rainmeter_path: Path | None
    ffplay_path: Path | None
    capture_provider: CaptureProviderCapabilities


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def ensure_within(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise SpectrumPathError("renderer workspace path escaped the configured data root") from exc
    return resolved_candidate


def iter_tree_files(root: Path) -> Iterator[Path]:
    if _is_link(root):
        raise SpectrumPathError("linked renderer roots are not accepted")
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in sorted(directory_names):
            child = base / name
            if _is_link(child):
                raise SpectrumPathError("linked renderer directories are not accepted")
        for name in sorted(file_names):
            child = base / name
            if _is_link(child):
                raise SpectrumPathError("linked renderer files are not accepted")
            yield child


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_tree(root: Path, *, prefix: str = "") -> str:
    entries: list[str] = []
    for path in sorted(iter_tree_files(root), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        logical_path = f"{prefix.rstrip('/')}/{relative}" if prefix else relative
        entries.append(f"{logical_path}\t{sha256_file(path)}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _resolve_asset(
    directory: Path,
    extensions: tuple[str, ...],
) -> tuple[Path | None, int]:
    if not directory.is_dir() or _is_link(directory):
        return None, 0
    candidates = sorted(
        (
            item
            for item in directory.iterdir()
            if item.is_file()
            and not _is_link(item)
            and item.suffix.lower() in extensions
        ),
        key=lambda item: (item.name.casefold(), item.name),
    )
    return (candidates[0] if candidates else None), len(candidates)


def _discover_rainmeter(inspection: SpectrumInspection) -> Path | None:
    if inspection.rainmeter_available is False:
        return None
    if inspection.rainmeter_path is not None:
        return inspection.rainmeter_path if inspection.rainmeter_path.is_file() else None

    candidates: list[Path] = []
    resolved = shutil.which("Rainmeter.exe") or shutil.which("Rainmeter")
    if resolved:
        candidates.append(Path(resolved))
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Rainmeter" / "Rainmeter.exe")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _read_duration(path: Path, inspection: SpectrumInspection, settings: Settings) -> float:
    reader = inspection.duration_reader
    if reader is not None:
        return float(reader(path))
    return probe_media_file(
        settings.ffprobe_path,
        path,
        count_frames=False,
        timeout_seconds=30,
    ).duration_seconds


def _discover_ffplay(settings: Settings, inspection: SpectrumInspection) -> Path | None:
    if inspection.ffplay_available is False:
        return None
    if inspection.ffplay_path is not None:
        return inspection.ffplay_path if inspection.ffplay_path.is_file() else None
    configured = Path(settings.ffmpeg_path)
    candidates: list[Path] = []
    if configured.parent != Path("."):
        candidates.append(configured.with_name("ffplay.exe"))
    resolved = shutil.which("ffplay.exe") or shutil.which("ffplay")
    if resolved:
        candidates.append(Path(resolved))
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def _workspace_writable(paths: SpectrumPaths) -> bool:
    try:
        jobs_root = ensure_within(paths.data_root, paths.jobs_root)
        jobs_root.mkdir(parents=True, exist_ok=True)
        secure_private_directory(jobs_root)
        with tempfile.NamedTemporaryFile(dir=jobs_root, prefix=".preflight-", delete=True):
            pass
    except (OSError, SpectrumPathError):
        return False
    return True


def _requirement(
    requirement_id: str,
    label: str,
    available: bool,
    detail: str,
    *,
    required_for_preparation: bool = True,
) -> RendererRequirement:
    return RendererRequirement(
        id=requirement_id,
        label=label,
        available=available,
        required_for_preparation=required_for_preparation,
        detail=detail,
    )


def inspect_wzhk_spectrum(
    settings: Settings,
    paths: SpectrumPaths,
    inspection: SpectrumInspection | None = None,
) -> SpectrumPreflightOutcome:
    resolved_inspection = inspection or SpectrumInspection()
    platform_name = (resolved_inspection.platform_name or sys.platform).lower()
    windows_available = platform_name.startswith("win")

    contract: SpectrumRenderRequest | None = None
    contract_valid = False
    try:
        contract = SpectrumRenderRequest.model_validate(_read_json(paths.contract_path))
        contract_valid = (
            contract.track.audio_asset_directory
            == ".trackprompt-data/wzhk-spectrum/assets/track"
            and contract.branding.logo_asset_directory
            == ".trackprompt-data/wzhk-spectrum/assets/logo"
            and contract.renderer.vendor_source_directory
            == "vendor/wzhk-spectrum-visualizer"
            and contract.renderer.workspace_root
            == ".trackprompt-data/wzhk-spectrum/jobs"
        )
    except (OSError, ValueError, json.JSONDecodeError, ValidationError):
        contract = None

    design_preset: SpectrumDesignPreset | None = None
    try:
        design_preset = load_design_preset(paths.design_preset_path)
        design_preset_valid = (
            contract is not None
            and design_preset.render.width == contract.renderer.capture.width
            and design_preset.render.height == contract.renderer.capture.height
            and design_preset.render.fps == contract.renderer.capture.fps
        )
    except SpectrumDesignError:
        design_preset = None
        design_preset_valid = False

    geometry_runtime_files = (
        paths.geometry_runtime_root / "index.html",
        paths.geometry_runtime_root / "runtime.css",
        paths.geometry_runtime_root / "runtime.js",
        paths.geometry_runtime_root / "shaders" / "neopixel.vert.glsl",
        paths.geometry_runtime_root / "shaders" / "neopixel.frag.glsl",
    )
    geometry_runtime_valid = (
        paths.geometry_runtime_root.is_dir()
        and not _is_link(paths.geometry_runtime_root)
        and all(path.is_file() and not _is_link(path) for path in geometry_runtime_files)
    )

    provenance: dict[str, Any] | None = None
    vendor_hash: str | None = None
    required_vendor_files = (
        paths.vendor_root / "visualizer.ini",
        paths.vendor_root / "@Resources" / "variables.ini",
        paths.vendor_root / "LICENSE",
        paths.vendor_root / "UPSTREAM-SOURCE.json",
    )
    vendor_valid = (
        paths.vendor_root.is_dir()
        and not _is_link(paths.vendor_root)
        and all(path.is_file() and not _is_link(path) for path in required_vendor_files)
        and not (paths.vendor_root / ".git").exists()
    )
    if vendor_valid:
        try:
            provenance = _read_json(paths.vendor_root / "UPSTREAM-SOURCE.json")
            vendor_valid = (
                bool(str(provenance.get("repository", "")).strip())
                and provenance.get("commit") == EXPECTED_VENDOR_COMMIT
                and provenance.get("vendoredWithoutGitMetadata") is True
            )
            if vendor_valid:
                vendor_hash = hash_tree(
                    paths.vendor_root,
                    prefix="vendor/wzhk-spectrum-visualizer",
                )
        except (OSError, ValueError, json.JSONDecodeError, SpectrumPathError):
            vendor_valid = False
            provenance = None
            vendor_hash = None

    logo_path, logo_count = _resolve_asset(
        paths.logo_directory,
        SUPPORTED_LOGO_EXTENSIONS,
    )
    master_path, master_count = _resolve_asset(
        paths.track_directory,
        SUPPORTED_AUDIO_EXTENSIONS,
    )

    measured_duration: float | None = None
    master_timing: SpectrumMasterTiming | None = None
    duration_valid = False
    if master_path is not None and contract is not None:
        try:
            measured_duration = _read_duration(master_path, resolved_inspection, settings)
            master_timing = resolve_master_timing(
                measured_duration,
                grid_duration_seconds=contract.track.grid_duration_seconds,
                final_fade_seconds=(
                    design_preset.transitions.final_fade_seconds
                    if design_preset is not None
                    else 4.0
                ),
            )
            duration_valid = True
        except (OSError, RuntimeError, ValueError, SpectrumProductionError):
            measured_duration = None
            master_timing = None

    rainmeter_path = _discover_rainmeter(resolved_inspection) if windows_available else None
    rainmeter_available = rainmeter_path is not None
    if resolved_inspection.ffmpeg_available is None:
        ffmpeg_status = inspect_tool(settings.ffmpeg_path)
        ffmpeg_available = ffmpeg_status.available
        ffmpeg_version = ffmpeg_status.version
    else:
        ffmpeg_available = resolved_inspection.ffmpeg_available
        ffmpeg_version = resolved_inspection.ffmpeg_version
    ffprobe_available = (
        inspect_tool(settings.ffprobe_path).available
        if resolved_inspection.ffprobe_available is None
        else resolved_inspection.ffprobe_available
    )
    ffplay_path = _discover_ffplay(settings, resolved_inspection) if windows_available else None
    ffplay_available = ffplay_path is not None
    if resolved_inspection.capture_provider_available is None:
        capture_provider = (
            inspect_ffmpeg_capture_capabilities(settings.ffmpeg_path)
            if ffmpeg_available and windows_available
            else CaptureProviderCapabilities(
                available=False,
                supports_window_capture=False,
                supports_constant_frame_rate=False,
                detail="FFmpeg capture inspection requires Windows and FFmpeg.",
            )
        )
    else:
        encoder = resolved_inspection.capture_encoder
        capture_provider = CaptureProviderCapabilities(
            available=resolved_inspection.capture_provider_available,
            supports_window_capture=resolved_inspection.capture_provider_available,
            supports_constant_frame_rate=resolved_inspection.capture_provider_available,
            supports_alpha_composition=(
                resolved_inspection.alpha_composition_available is True
            ),
            supports_monitor_capture=(
                resolved_inspection.monitor_capture_available is True
            ),
            supports_chroma_composition=(
                resolved_inspection.chroma_composition_available is True
            ),
            encoder=(encoder if encoder in {"h264_nvenc", "libx264"} else None),
            hardware_acceleration_verified=(resolved_inspection.nvenc_available is True),
            detail=(
                f"Synthetic FFmpeg capture capability uses {encoder}."
                if resolved_inspection.capture_provider_available
                else "Synthetic capture provider is unavailable."
            ),
        )
    workspace_writable = _workspace_writable(paths)

    grid_duration = contract.track.grid_duration_seconds if contract else 192.0
    duration_detail = (
        (
            f"Approved master is {master_timing.master_duration_seconds:.3f}s: "
            f"{master_timing.grid_duration_seconds:.3f}s musical grid plus "
            f"{master_timing.tail_duration_seconds:.3f}s intentional post-grid tail."
        )
        if master_timing is not None
        else f"Master duration could not be validated against the {grid_duration:.3f}s musical grid."
    )
    requirements = [
        _requirement(
            "windows-platform",
            "Windows",
            windows_available,
            "WZHK Spectrum requires Windows and remains isolated when another platform is used.",
        ),
        _requirement(
            "vendor-snapshot",
            "Vendor source",
            vendor_valid,
            "The pinned vendor snapshot, license, provenance, and nested-Git boundary were checked.",
        ),
        _requirement(
            "track-contract",
            "Scattered contract",
            contract_valid,
            "The canonical 120 BPM, 4/4, 96-bar, 192-second contract was validated.",
        ),
        _requirement(
            "scattered-design-preset",
            "Scattered visual preset",
            design_preset_valid,
            "The typed 1920x1080 Scattered design, section states, and transitions were validated.",
        ),
        _requirement(
            "generative-geometry-runtime",
            "Generative Geometry runtime",
            geometry_runtime_valid,
            (
                "Trusted local WebGL2, GLSL, and NeoPixel runtime assets are present."
                if geometry_runtime_valid
                else "The trusted local generative geometry runtime is incomplete; the static structured fallback remains available."
            ),
            required_for_preparation=False,
        ),
        _requirement(
            "wzhk-logo",
            "WZHK logo",
            logo_path is not None,
            "A supported private logo is available." if logo_path else "Add one supported logo to the private Spectrum logo folder.",
        ),
        _requirement(
            "master-audio",
            "Scattered master",
            master_path is not None,
            "A supported private master is available." if master_path else "Add one supported master track to the private Spectrum track folder.",
        ),
        _requirement(
            "master-duration",
            "Master duration",
            duration_valid,
            duration_detail,
        ),
        _requirement(
            "rainmeter",
            "Rainmeter",
            rainmeter_available,
            "Rainmeter is discoverable." if rainmeter_available else "Rainmeter was not found in PATH or standard Windows install locations; no download was attempted.",
            required_for_preparation=False,
        ),
        _requirement(
            "ffmpeg",
            "FFmpeg",
            ffmpeg_available,
            "FFmpeg is discoverable." if ffmpeg_available else "FFmpeg was not available; no download was attempted.",
            required_for_preparation=False,
        ),
        _requirement(
            "ffprobe",
            "ffprobe",
            ffprobe_available,
            "ffprobe is discoverable for authoritative media duration and validation." if ffprobe_available else "ffprobe was not available; configure FFPROBE_PATH.",
            required_for_preparation=False,
        ),
        _requirement(
            "playback-clock",
            "Controlled playback",
            ffplay_available,
            "ffplay is discoverable for the owned production playback process." if ffplay_available else "ffplay was not found beside FFmpeg or in PATH.",
            required_for_preparation=False,
        ),
        _requirement(
            "capture-provider",
            "Capture provider",
            capture_provider.available,
            capture_provider.detail,
            required_for_preparation=False,
        ),
        _requirement(
            "runtime-workspace",
            "Private workspace",
            workspace_writable,
            "The private Spectrum jobs directory is writable." if workspace_writable else "The private Spectrum jobs directory is not writable.",
        ),
    ]

    if not windows_available:
        availability = RendererAvailabilityState.UNSUPPORTED_PLATFORM
    elif not vendor_valid:
        availability = RendererAvailabilityState.INVALID_VENDOR_SNAPSHOT
    elif not contract_valid:
        availability = RendererAvailabilityState.INVALID_CONTRACT
    elif not design_preset_valid:
        availability = RendererAvailabilityState.INVALID_DESIGN_PRESET
    elif logo_path is None:
        availability = RendererAvailabilityState.MISSING_ASSETS
    elif master_path is None:
        availability = RendererAvailabilityState.MISSING_MASTER
    elif not duration_valid:
        availability = RendererAvailabilityState.INVALID_MASTER_DURATION
    elif not rainmeter_available:
        availability = RendererAvailabilityState.MISSING_RAINMETER
    elif not ffmpeg_available or not ffprobe_available or not ffplay_available:
        availability = RendererAvailabilityState.MISSING_FFMPEG
    elif not capture_provider.available:
        availability = RendererAvailabilityState.MISSING_CAPTURE_PROVIDER
    elif not workspace_writable:
        availability = RendererAvailabilityState.WORKSPACE_UNAVAILABLE
    else:
        availability = RendererAvailabilityState.READY_FOR_CAPTURE

    preview_availability = (
        SpectrumProductionAvailability.READY_FOR_PREVIEW
        if rainmeter_available and duration_valid
        else SpectrumProductionAvailability.MISSING_RAINMETER
        if not rainmeter_available
        else SpectrumProductionAvailability.INVALID_MASTER_DURATION
    )
    if not workspace_writable:
        preview_availability = SpectrumProductionAvailability.INVALID_WORKSPACE
    capture_availability = (
        SpectrumProductionAvailability.READY_FOR_CAPTURE
        if (
            preview_availability is SpectrumProductionAvailability.READY_FOR_PREVIEW
            and ffmpeg_available
            and ffprobe_available
            and ffplay_available
            and capture_provider.available
        )
        else SpectrumProductionAvailability.MISSING_RAINMETER
        if not rainmeter_available
        else SpectrumProductionAvailability.MISSING_FFMPEG
        if not ffmpeg_available or not ffprobe_available or not ffplay_available
        else SpectrumProductionAvailability.MISSING_CAPTURE_PROVIDER
        if not capture_provider.available
        else SpectrumProductionAvailability.INVALID_WORKSPACE
    )

    warnings = [item.detail for item in requirements if not item.available]
    if logo_count > 1:
        warnings.append("Multiple supported logos were found; deterministic lexical selection will be used.")
    if master_count > 1:
        warnings.append("Multiple supported master tracks were found; deterministic lexical selection will be used.")

    summary = None
    if contract is not None:
        summary = RendererContractSummary(
            artist=contract.project.artist,
            title=contract.project.title,
            bpm=contract.track.bpm,
            meter=(
                f"{contract.track.time_signature.numerator}/"
                f"{contract.track.time_signature.denominator}"
            ),
            total_bars=contract.track.total_bars,
            expected_duration_seconds=contract.track.grid_duration_seconds,
            grid_duration_seconds=contract.track.grid_duration_seconds,
            master_duration_seconds=(master_timing.master_duration_seconds if master_timing else None),
            tail_duration_seconds=(master_timing.tail_duration_seconds if master_timing else None),
            width=contract.renderer.capture.width,
            height=contract.renderer.capture.height,
            fps=contract.renderer.capture.fps,
        )

    design_summary = None
    if design_preset is not None and design_preset_valid:
        design_summary = SpectrumDesignPresetSummary(
            preset_id=design_preset.preset_id,
            display_name=design_preset.display_name,
            preview_timing_source=design_preset.controller.preview_timing_source,
            production_timing_source=design_preset.controller.production_timing_source,
            preview_timing_accuracy=design_preset.controller.preview_accuracy,
            production_timing_accuracy=design_preset.controller.production_accuracy,
            progress_visible=design_preset.progress.visible,
            background_mode=design_preset.background.mode,
            generative_geometry=GenerativeGeometrySummary(
                enabled=design_preset.generative_geometry.enabled,
                subsystem_id=design_preset.generative_geometry.subsystem_id,
                render_mode=design_preset.generative_geometry.render_mode,
                seed=design_preset.generative_geometry.seed,
                point_count=design_preset.generative_geometry.point_domain.point_count,
                performance_profile=design_preset.generative_geometry.performance_profile,
                shape_families=[
                    shape.value for shape in design_preset.generative_geometry.shape_library
                ],
            ),
            sections=[
                SpectrumTimelineSectionSummary(
                    id=section.id,
                    label=section.label,
                    start_seconds=section.start_seconds,
                    end_seconds=section.end_seconds,
                    spectrum_color=section.state.spectrum_color.upper(),
                )
                for section in design_preset.sections
            ]
            + [
                SpectrumTimelineSectionSummary(
                    id="post-grid-tail",
                    label=design_preset.post_grid_tail.label,
                    start_seconds=design_preset.post_grid_tail.start_seconds,
                    end_seconds=(master_timing.master_duration_seconds if master_timing else None),
                    spectrum_color=design_preset.post_grid_tail.state.spectrum_color.upper(),
                )
            ],
        )

    descriptor = RendererDescriptor(
        renderer_id=RENDERER_ID,
        display_name="WZHK Spectrum",
        description=(
            "Optional Windows Rainmeter spectrum renderer with deterministic capture, post-grid tail timing, original-master muxing, and validation."
        ),
        platform="windows",
        capabilities=[
            "availability-preflight",
            "deterministic-workspace",
            "wzhk-branding",
            "scattered-visual-preset",
            "section-aware-timeline",
            "fixed-section-preview",
            "ffmpeg-window-capture",
            "original-master-mux",
            "post-grid-tail",
            "review-frame-extraction",
            "webgl2-generative-geometry",
            "gpu-neopixel-point-field",
            "coordinate-morph-choreography",
            "static-structured-fallback",
            "renderer-performance-telemetry",
        ],
        availability=availability,
        available=availability is RendererAvailabilityState.READY_FOR_CAPTURE,
        preparation_available=all(
            item.available for item in requirements if item.required_for_preparation
        ),
        preview_availability=preview_availability,
        capture_availability=capture_availability,
        preview_available=preview_availability is SpectrumProductionAvailability.READY_FOR_PREVIEW,
        capture_available=capture_availability is SpectrumProductionAvailability.READY_FOR_CAPTURE,
        warnings=warnings,
        requirements=requirements,
        contract_summary=summary,
        design_preset=design_summary,
        geometry_capability=resolved_inspection.geometry_capability,
    )
    return SpectrumPreflightOutcome(
        descriptor=descriptor,
        contract=contract,
        design_preset=design_preset,
        logo_path=logo_path,
        master_audio_path=master_path,
        measured_audio_duration_seconds=measured_duration,
        master_timing=master_timing,
        vendor_source_hash=vendor_hash,
        provenance=provenance,
        ffmpeg_version=ffmpeg_version,
        rainmeter_path=rainmeter_path,
        ffplay_path=ffplay_path,
        capture_provider=capture_provider,
    )

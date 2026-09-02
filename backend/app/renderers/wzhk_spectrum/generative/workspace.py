from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

from ....privacy import secure_private_directory, secure_private_file
from ..design import GenerativeGeometryDesign, SpectrumDesignPreset
from ..production import SpectrumMasterTiming
from .choreography import musical_time_to_seconds
from .contracts import GeometryPreviewMode, GeometryPreviewOverride


class GeometryWorkspaceError(RuntimeError):
    """Raised when trusted geometry runtime assets cannot be materialized safely."""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    secure_private_file(path)


def _validate_runtime_source(repository_root: Path) -> Path:
    source = (repository_root / "tools" / "wzhk-spectrum" / "runtime").resolve()
    try:
        source.relative_to(repository_root.resolve())
    except ValueError as exc:
        raise GeometryWorkspaceError("The geometry runtime source escaped the repository.") from exc
    required = (
        source / "index.html",
        source / "runtime.css",
        source / "runtime.js",
        source / "shaders" / "neopixel.vert.glsl",
        source / "shaders" / "neopixel.frag.glsl",
    )
    if not source.is_dir() or any(not path.is_file() for path in required):
        raise GeometryWorkspaceError("Trusted WZHK geometry runtime assets are incomplete.")
    for path in (source, *source.rglob("*")):
        if path.is_symlink():
            raise GeometryWorkspaceError("Linked geometry runtime assets are not accepted.")
    return source


def _profile(geometry: GenerativeGeometryDesign, mode: str) -> dict[str, Any]:
    profile_id = "preview" if mode == "preview" else geometry.performance_profile
    profile = next(item for item in geometry.performance_profiles if item.id == profile_id)
    return profile.model_dump(mode="json", by_alias=True)


def _runtime_choreography(
    geometry: GenerativeGeometryDesign,
    timing: SpectrumMasterTiming,
    preview_override: GeometryPreviewOverride | None,
) -> list[dict[str, Any]]:
    if preview_override is not None and preview_override.mode in {
        GeometryPreviewMode.SHAPE,
        GeometryPreviewMode.MORPH,
        GeometryPreviewMode.LAB,
    }:
        shape_a = preview_override.shape_a
        if shape_a is None:
            raise GeometryWorkspaceError("The geometry preview override is incomplete.")
        shape_b = preview_override.shape_b or shape_a
        return [
            {
                "sourceShape": shape_a.shape_id.value,
                "targetShape": shape_b.shape_id.value,
                "startSeconds": 0.0,
                "durationSeconds": timing.master_duration_seconds,
                "easing": "smootherstep",
                "section": "intro",
            }
        ]

    transitions: list[dict[str, Any]] = []
    choreography = geometry.choreography
    for transition in choreography.transitions:
        start = musical_time_to_seconds(
            transition.start,
            choreography.bpm,
            choreography.beats_per_bar,
        )
        duration = musical_time_to_seconds(
            transition.duration,
            choreography.bpm,
            choreography.beats_per_bar,
        )
        if transition.section.value == "post-grid-tail":
            duration = max(0.001, timing.master_duration_seconds - start)
        transitions.append(
            {
                "sourceShape": transition.shape_a.shape_id.value,
                "targetShape": transition.shape_b.shape_id.value,
                "startSeconds": start,
                "durationSeconds": duration,
                "easing": transition.easing.value,
                "section": transition.section.value,
            }
        )
    if preview_override is not None and preview_override.mode is GeometryPreviewMode.SECTION:
        section = preview_override.section
        if section is None:
            raise GeometryWorkspaceError("The section preview override is incomplete.")
        selected = [item for item in transitions if item["section"] == section.value]
        if not selected:
            raise GeometryWorkspaceError("The selected geometry section has no choreography.")
        offset = float(selected[0]["startSeconds"])
        for item in selected:
            item["startSeconds"] = float(item["startSeconds"]) - offset
        return selected
    return transitions


def build_runtime_config(
    *,
    mode: str,
    design: SpectrumDesignPreset,
    timing: SpectrumMasterTiming,
    preview_override: GeometryPreviewOverride | None = None,
) -> dict[str, Any]:
    geometry = design.generative_geometry
    profile = _profile(geometry, mode)
    point_count = int(profile["pointCount"])
    if preview_override is not None and preview_override.point_count is not None:
        point_count = preview_override.point_count
    columns = geometry.point_domain.columns or int(math.sqrt(point_count))
    if point_count % columns != 0:
        columns = int(math.sqrt(point_count))
    rows = math.ceil(point_count / columns)
    mapping = geometry.audio_mapping
    camera = geometry.camera
    deterministic_design_id = f"{geometry.seed:08x}-0000-4000-8000-000000000000"
    preview_payload = (
        preview_override.model_dump(mode="json", by_alias=True)
        if preview_override is not None
        else None
    )
    return {
        "schemaVersion": "1.0.0",
        "rendererId": "wzhk-generative-geometry",
        # The actual UUID is runtime telemetry and arrives through the authenticated
        # loopback control channel. Keeping it out of this file preserves identical
        # deterministic workspace hashes for identical design inputs.
        "jobId": deterministic_design_id,
        "designId": deterministic_design_id,
        "mode": mode,
        "composition": design.composition.model_dump(mode="json", by_alias=True),
        "seed": geometry.seed,
        "pointCount": point_count,
        "pointDomain": {"width": columns, "height": rows},
        "targetFps": profile["targetFps"],
        "minimumSustainedFps": profile["minimumSustainedFps"],
        "maximumAverageFrameTimeMs": profile["maximumAverageFrameTimeMs"],
        "masterDurationSeconds": timing.master_duration_seconds,
        "pointSize": geometry.point_size,
        "globalScale": geometry.global_scale,
        "logoUrl": "/assets/logo",
        "audioUrl": "/assets/audio",
        "palette": {
            "background": design.palette.background,
            "primary": design.palette.spectrum,
            "secondary": design.palette.accent,
            "text": design.palette.text,
        },
        "camera": {
            "position": list(camera.position),
            "target": list(camera.target),
            "fovDegrees": camera.fov_degrees,
            "near": camera.near,
            "far": camera.far,
            "orbitAmplitudeDegrees": camera.orbit_amplitude_degrees,
            "orbitSpeed": camera.orbit_speed,
            "dollyAmplitude": camera.dolly_amplitude,
        },
        "audioMapping": {
            "fftSize": 2048,
            "smoothingTimeConstant": 0.76,
            "lowGain": mapping.low_scale_gain,
            "midGain": mapping.mid_displacement_gain,
            "highGain": mapping.high_brightness_gain,
            "energyGain": mapping.energy_motion_gain,
            "transientGain": mapping.transient_impulse_gain,
            "transientThreshold": 0.045,
            "propagationSpeed": geometry.propagation.speed,
            "propagationDecay": geometry.propagation.decay,
            "propagationWidth": geometry.propagation.width,
        },
        "branding": {
            "enabled": True,
            "artist": "DJ WaZaHaKa",
            "title": "SCATTERED",
            **({"meta": "120 BPM  /  4/4  /  96 BARS"} if mode == "preview" else {}),
        },
        "developerLab": {
            "enabled": (
                mode == "preview"
                and preview_override is not None
                and preview_override.mode
                in {GeometryPreviewMode.SHAPE, GeometryPreviewMode.MORPH, GeometryPreviewMode.LAB}
            ),
            "spectrumDiagnostics": False,
            "technicalMetadata": False,
            "previewOverride": preview_payload,
        },
        "control": {
            "pollMilliseconds": 100,
            "telemetryIntervalMilliseconds": 1000,
        },
        "trustedShapes": [shape.value for shape in geometry.shape_library],
        "choreography": _runtime_choreography(geometry, timing, preview_override),
    }


def materialize_geometry_runtime(
    *,
    repository_root: Path,
    job_root: Path,
    mode: str,
    design: SpectrumDesignPreset,
    timing: SpectrumMasterTiming,
    preview_override: GeometryPreviewOverride | None = None,
) -> Path:
    source = _validate_runtime_source(repository_root)
    target = job_root / "geometry"
    if target.exists():
        raise GeometryWorkspaceError("The geometry runtime target already exists.")
    shutil.copytree(source, target, copy_function=shutil.copyfile)
    secure_private_directory(target)
    for path in target.rglob("*"):
        if path.is_file():
            secure_private_file(path)
    config = build_runtime_config(
        mode=mode,
        design=design,
        timing=timing,
        preview_override=preview_override,
    )
    config_path = target / "config" / "runtime-config.json"
    _write_json(config_path, config)
    return config_path

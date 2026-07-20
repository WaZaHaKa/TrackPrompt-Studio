from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import math
import os
import re
import struct
import subprocess
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence, cast
from uuid import uuid4

TOOL_SCHEMA_VERSION = "1.0.0"
SUPPORTED_PROFILE_SCHEMA_VERSIONS = frozenset({"1.0.0", "1.1.0"})
BUILDER_PROFILE_SCHEMA_VERSION = "1.1.0"
CANONICAL_PROFILE_HASH_EXCLUDES = frozenset({"integrity", "profileSha256"})
GIB = 1024**3
RENDER_MANIFEST_KIND = "trackprompt-final-render-manifest"
ENCODE_MANIFEST_KIND = "trackprompt-final-encode-manifest"
FRAME_PATTERN = re.compile(r"^frame_%06d\.(png|exr)$")
FRAME_LIKE_PATTERN = re.compile(r"^frame_(\d+)(\.[^.]+)$", re.IGNORECASE)
HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
AUDIO_BITRATE_PATTERN = re.compile(r"^[1-9][0-9]{1,3}k$")
VIDEO_FILTER_PATTERN = re.compile(r"^[A-Za-z0-9_.,:=+\-*/()]+$")
SAFE_OUTPUT_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
WINDOWS_RESERVED_COMPONENTS = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{index}" for index in range(1, 10)), *(f"LPT{index}" for index in range(1, 10))}
)
FIXED_OUTPUT_SUBDIRECTORIES = frozenset({"logs", "checkpoints", "manifests", "master", "delivery", "qa"})
BLENDER_52_DISPLAY_DEVICES = frozenset({"sRGB", "Display P3", "Rec.1886"})
BLENDER_52_VIEW_LOOKS = {
    "AgX": frozenset({"AgX - Medium High Contrast", "AgX - Medium Low Contrast", "None"}),
    "Standard": frozenset({"Medium High Contrast", "None"}),
}
BLENDER_52_SEQUENCER_COLOR_SPACES = frozenset({"sRGB", "Linear Rec.709"})
ALLOWED_VIDEO_CODECS = {
    "master": {"ffv1", "prores_ks"},
    "delivery": {"libx264", "libx265"},
}
ALLOWED_AUDIO_CODECS = {"aac", "flac", "pcm_s16le", "pcm_s24le"}
ALLOWED_PIXEL_FORMATS = {"gbrp10le", "rgb48le", "yuv420p", "yuv420p10le", "yuv422p10le"}
CODEC_PROBE_NAMES = {
    "ffv1": "ffv1",
    "libx264": "h264",
    "libx265": "hevc",
    "prores_ks": "prores",
}


class ToolingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _require_dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolingError("invalid-profile", f"{label} must be a JSON object.")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolingError("invalid-profile", f"{label} must be a non-empty string.")
    if any(character in value for character in ("\r", "\n", "|")):
        raise ToolingError("invalid-profile", f"{label} contains a forbidden delimiter.")
    return value.strip()


def _require_int(value: object, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ToolingError("invalid-profile", f"{label} must be an integer of at least {minimum}.")
    return value


def _require_number(value: object, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolingError("invalid-profile", f"{label} must be numeric.")
    result = float(value)
    if not math.isfinite(result) or result <= minimum:
        raise ToolingError("invalid-profile", f"{label} must be finite and greater than {minimum}.")
    return result


def _first(mapping: dict[str, Any], *names: str) -> object:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _canonical_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
        raise ToolingError("invalid-profile", f"{label} must be a 64-character SHA-256 value.")
    return value.upper()


def _validated_video_filter(value: object, label: str) -> str:
    filter_value = _require_string(value, label)
    if len(filter_value) > 500 or VIDEO_FILTER_PATTERN.fullmatch(filter_value) is None:
        raise ToolingError(
            "invalid-profile",
            f"{label} must be at most 500 characters and use only reviewed FFmpeg filter characters.",
        )
    return filter_value


def _safe_output_component(value: object, label: str) -> str:
    component = _require_string(value, label)
    stem = component.partition(".")[0].upper()
    if (
        SAFE_OUTPUT_COMPONENT_PATTERN.fullmatch(component) is None
        or component.endswith((".", " "))
        or component in {".", ".."}
        or stem in WINDOWS_RESERVED_COMPONENTS
        or component.casefold() in FIXED_OUTPUT_SUBDIRECTORIES
    ):
        raise ToolingError(
            "invalid-profile",
            f"{label} must be one filesystem-safe directory name that does not collide with managed output folders.",
        )
    return component


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ToolingError("missing-input", f"{label} does not exist.") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ToolingError("invalid-json", f"{label} is not valid readable UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise ToolingError("invalid-json", f"{label} must contain a JSON object.")
    return payload


def _absolute_path(value: str | Path, label: str, *, must_exist: bool = False, file: bool = False) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ToolingError("path-not-absolute", f"{label} must be an absolute path.")
    try:
        resolved = candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise ToolingError("invalid-path", f"{label} could not be resolved.") from exc
    if must_exist and file and not resolved.is_file():
        raise ToolingError("missing-input", f"{label} must be an existing file.")
    return resolved


def _absolute_executable_path(value: str | Path, label: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ToolingError("path-not-absolute", f"{label} must be an absolute path.")
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file():
            raise ToolingError("missing-input", f"{label} must be an existing file.")
        return resolved
    except PermissionError:
        # WinGet package directories can permit process execution and
        # PowerShell Test-Path while denying Python realpath/stat traversal.
        # subprocess(shell=False) remains the authoritative executable check.
        return candidate
    except FileNotFoundError as exc:
        raise ToolingError("missing-input", f"{label} must be an existing file.") from exc
    except OSError as exc:
        raise ToolingError("invalid-path", f"{label} could not be resolved.") from exc


def _contained_path(candidate: Path, parent: Path, label: str) -> Path:
    resolved_candidate = _absolute_path(candidate, label)
    resolved_parent = _absolute_path(parent, "Containment root")
    try:
        resolved_candidate.relative_to(resolved_parent)
    except ValueError as exc:
        raise ToolingError("path-containment-failed", f"{label} must remain under the selected output root.") from exc
    if resolved_candidate == resolved_parent:
        raise ToolingError("path-containment-failed", f"{label} must be below, not equal to, its containment root.")
    return resolved_candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ToolingError("hash-failed", f"Could not hash {path.name}.") from exc
    return digest.hexdigest().upper()


def canonical_profile_sha256(raw: dict[str, Any]) -> str:
    """Return the Python canonical hash used for legacy diagnostic output only."""
    payload = copy.deepcopy(raw)
    for field in CANONICAL_PROFILE_HASH_EXCLUDES:
        payload.pop(field, None)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _normalize_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """Return the renderer view of a legacy or Mission Control builder profile."""
    normalized = copy.deepcopy(raw)
    schema_version = normalized.get("schemaVersion")
    if schema_version not in SUPPORTED_PROFILE_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_PROFILE_SCHEMA_VERSIONS))
        raise ToolingError("invalid-profile", f"Render profile schemaVersion must be one of: {supported}.")
    if schema_version == BUILDER_PROFILE_SCHEMA_VERSION:
        approved_scene = _require_dict(normalized.get("approvedScene"), "approvedScene")
        nested_scene_hash = _canonical_hash(approved_scene.get("sha256"), "approvedScene.sha256")
        root_scene_hash = normalized.get("approvedSceneSha256")
        if root_scene_hash is not None and not hmac.compare_digest(
            _canonical_hash(root_scene_hash, "approvedSceneSha256"), nested_scene_hash
        ):
            raise ToolingError("invalid-profile", "approvedScene.sha256 and approvedSceneSha256 must agree.")
        normalized["approvedSceneSha256"] = nested_scene_hash

        scene_path_value = approved_scene.get("path")
        if scene_path_value is not None:
            scene_path = Path(_require_string(scene_path_value, "approvedScene.path")).expanduser()
            if not scene_path.is_absolute() or scene_path.suffix.casefold() != ".blend":
                raise ToolingError("invalid-profile", "approvedScene.path must be an absolute .blend path.")
        manifest_path_value = approved_scene.get("manifestPath")
        manifest_hash_value = approved_scene.get("manifestSha256")
        manifest_path_set = isinstance(manifest_path_value, str) and bool(manifest_path_value.strip())
        manifest_hash_set = isinstance(manifest_hash_value, str) and bool(manifest_hash_value.strip())
        if manifest_path_set != manifest_hash_set:
            raise ToolingError(
                "invalid-profile",
                "approvedScene.manifestPath and approvedScene.manifestSha256 must either both be set or both be blank.",
            )
        if manifest_path_set:
            manifest_path = Path(_require_string(manifest_path_value, "approvedScene.manifestPath")).expanduser()
            if not manifest_path.is_absolute():
                raise ToolingError("invalid-profile", "approvedScene.manifestPath must be absolute when set.")
            _canonical_hash(manifest_hash_value, "approvedScene.manifestSha256")

        timeline = _require_dict(normalized.get("timeline"), "timeline")
        for nested_name, root_name in (("frameStart", "frameStart"), ("frameEnd", "frameEnd"), ("fps", "fps")):
            nested_value = timeline.get(nested_name)
            root_value = normalized.get(root_name)
            if root_value is not None and root_value != nested_value:
                raise ToolingError("invalid-profile", f"timeline.{nested_name} and {root_name} must agree.")
            normalized[root_name] = nested_value

        render_settings = _require_dict(normalized.get("render"), "render")
        if "filmTransparent" not in render_settings and "transparentFilm" in render_settings:
            render_settings["filmTransparent"] = render_settings["transparentFilm"]
        top_compositor = normalized.get("compositor")
        if isinstance(top_compositor, dict) and "enabled" in top_compositor:
            if "useCompositing" in render_settings and render_settings["useCompositing"] != top_compositor["enabled"]:
                raise ToolingError("invalid-profile", "compositor.enabled and render.useCompositing must agree.")
            render_settings["useCompositing"] = top_compositor["enabled"]
        normalized["render"] = render_settings

        production = _require_dict(normalized.get("production"), "production")
        chunking_value = normalized.get("chunking")
        chunking = _require_dict(chunking_value, "chunking")
        production_chunk_size = _first(production, "framesPerChunk", "chunkSize")
        chunking_chunk_size = _first(chunking, "framesPerChunk", "chunkSize")
        if production_chunk_size is None:
            raise ToolingError("invalid-profile", "production.framesPerChunk is required.")
        if production_chunk_size is not None and chunking_chunk_size != production_chunk_size:
            raise ToolingError("invalid-profile", "production.framesPerChunk and chunking.framesPerChunk must agree.")
        for name in ("resumeEnabled", "verifyExistingFrames"):
            if production.get(name) is not True:
                raise ToolingError("invalid-profile", f"production.{name} must be true for resumable production rendering.")
        overwrite_policy_value = production.get("overwritePolicy")
        if overwrite_policy_value is not None:
            overwrite_policy = _require_string(overwrite_policy_value, "production.overwritePolicy").casefold()
            if overwrite_policy not in {"never-valid-frames", "never-overwrite-valid", "preserve-valid"}:
                raise ToolingError("invalid-profile", "production.overwritePolicy must preserve every valid frame.")
        elif production.get("overwriteValidFrames") is not False:
            raise ToolingError("invalid-profile", "production.overwriteValidFrames must be false.")
        if not isinstance(production.get("overwriteInvalidFrames"), bool):
            raise ToolingError("invalid-profile", "production.overwriteInvalidFrames must be boolean.")
        if production.get("stopOnValidationFailure") is not True:
            raise ToolingError("invalid-profile", "production.stopOnValidationFailure must be true.")
        if "atomicChunkCommit" in production and production.get("atomicChunkCommit") is not True:
            raise ToolingError("invalid-profile", "production.atomicChunkCommit must be true.")
        if "minimumLaunchFreeGiB" in production:
            storage = _require_dict(normalized.get("storage"), "storage")
            if production["minimumLaunchFreeGiB"] != storage.get("minimumLaunchFreeGiB"):
                raise ToolingError(
                    "invalid-profile",
                    "production.minimumLaunchFreeGiB and storage.minimumLaunchFreeGiB must agree.",
                )

        declared_hash = _canonical_hash(normalized.get("profileSha256"), "profileSha256")
        integrity = _require_dict(normalized.get("integrity"), "integrity")
        if str(integrity.get("algorithm", "")).upper().replace("-", "") != "SHA256":
            raise ToolingError("invalid-profile", "integrity.algorithm must be SHA-256.")
        integrity_hash_value = _first(integrity, "canonicalSha256", "profileSha256")
        integrity_hash = _canonical_hash(integrity_hash_value, "integrity.profileSha256")
        if not hmac.compare_digest(declared_hash, integrity_hash):
            raise ToolingError("profile-integrity-mismatch", "profileSha256 and integrity.profileSha256 must agree.")
        exclusions = integrity.get("excludes")
        if exclusions is not None and (
            not isinstance(exclusions, list) or set(exclusions) != set(CANONICAL_PROFILE_HASH_EXCLUDES)
        ):
            raise ToolingError("invalid-profile", "integrity.excludes does not match the schema 1.1.0 hash contract.")
        canonicalization = integrity.get("canonicalization")
        if canonicalization is not None and canonicalization != "sorted-json-v1":
            raise ToolingError("invalid-profile", "integrity.canonicalization must be sorted-json-v1 when set.")
    return normalized


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass(frozen=True)
class ImageSequenceProfile:
    format: str
    extension: str
    bit_depth: int
    color_mode: str
    filename_pattern: str
    compression: object
    color_management: object

    def filename(self, frame: int) -> str:
        return self.filename_pattern % frame


@dataclass(frozen=True)
class StoragePolicy:
    planned_frame_sequence_bytes: int
    projected_master_bytes: int
    projected_delivery_bytes: int
    support_reserve_bytes: int
    contingency_multiplier: float
    minimum_launch_free_bytes: int

    def as_dict(self) -> dict[str, int | float]:
        return {
            "plannedFrameSequenceBytes": self.planned_frame_sequence_bytes,
            "projectedMasterBytes": self.projected_master_bytes,
            "projectedDeliveryBytes": self.projected_delivery_bytes,
            "supportReserveBytes": self.support_reserve_bytes,
            "contingencyMultiplier": self.contingency_multiplier,
            "minimumLaunchFreeBytes": self.minimum_launch_free_bytes,
        }


@dataclass(frozen=True)
class RenderProfile:
    source_path: Path
    source_sha256: str
    canonical_sha256: str
    schema_version: str
    raw: dict[str, Any]
    display_name: str
    project: str
    preset: str
    profile_id: str
    blender_version: str
    frame_start: int
    frame_end: int
    fps: float
    width: int
    height: int
    resolution_percentage: int
    pixel_aspect_x: float
    pixel_aspect_y: float
    frames_subdirectory: str
    image: ImageSequenceProfile
    color_management: dict[str, Any]
    approved_scene_sha256: str
    chunk_size: int
    chunk_rationale: str
    storage: StoragePolicy
    audio: dict[str, Any]
    encoding: dict[str, Any]

    @property
    def frame_count(self) -> int:
        return self.frame_end - self.frame_start + 1

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.fps

    @property
    def authorization_token(self) -> str:
        authorization = self.raw.get("authorization")
        if not isinstance(authorization, dict):
            authorization = {}
        project = str(authorization.get("project", self.project.replace("-", " ").replace("_", " "))).upper()
        preset = str(authorization.get("preset", self.preset)).upper()
        profile = str(authorization.get("profile", self.profile_id)).upper()
        return (
            f"AUTHORIZE FULL RENDER: {project} | {preset} | {profile} | "
            f"SCENE {self.approved_scene_sha256[:12]} | PROFILE {self.source_sha256[:12]}"
        )


def load_render_profile(path: Path) -> RenderProfile:
    path = _absolute_path(path, "Render profile", must_exist=True, file=True)
    source_raw = _read_json(path, "Render profile")
    raw = _normalize_profile(source_raw)
    schema_version = str(raw["schemaVersion"])
    project = _require_string(raw.get("project"), "project")
    preset = _require_string(raw.get("preset"), "preset")
    profile_id = _require_string(_first(raw, "profileId", "tokenProfile"), "profileId")
    display_name = str(raw.get("displayName", profile_id)).strip()
    if schema_version == BUILDER_PROFILE_SCHEMA_VERSION:
        display_name = _require_string(raw.get("displayName"), "displayName")
        _require_string(raw.get("templateId"), "templateId")
        _require_string(_first(raw, "id", "slug"), "id")
        timestamps = raw.get("timestamps")
        if isinstance(timestamps, dict):
            _require_string(timestamps.get("createdAt"), "timestamps.createdAt")
            _require_string(timestamps.get("updatedAt"), "timestamps.updatedAt")
        else:
            _require_string(_first(raw, "createdAtUtc", "createdAt"), "createdAt")
            _require_string(_first(raw, "updatedAtUtc", "updatedAt"), "updatedAt")
        dashboard = _first(raw, "progressDashboard", "dashboard")
        _require_dict(dashboard, "dashboard")
        _require_dict(raw.get("estimates"), "estimates")
        validation = _require_dict(raw.get("validation"), "validation")
        if validation.get("status") != "valid":
            raise ToolingError("invalid-profile", "validation.status must be valid before production use.")
        warnings = raw.get("warnings")
        if isinstance(warnings, dict) and not warnings:
            warnings = []  # PowerShell 5.1 can round-trip an empty object array as {}.
        if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
            raise ToolingError("invalid-profile", "warnings must be an array of strings.")
    blender_version = _require_string(raw.get("blenderVersion"), "blenderVersion")

    timeline_value = raw.get("timeline")
    timeline = timeline_value if isinstance(timeline_value, dict) else raw
    frame_start = _require_int(_first(timeline, "frameStart", "startFrame"), "frameStart")
    frame_end = _require_int(_first(timeline, "frameEnd", "endFrame"), "frameEnd")
    if frame_end < frame_start:
        raise ToolingError("invalid-profile", "frameEnd must be at least frameStart.")
    fps = _require_number(timeline.get("fps"), "fps")
    declared_duration = _first(timeline, "durationSeconds") or raw.get("durationSeconds")
    calculated_duration = (frame_end - frame_start + 1) / fps
    if declared_duration is not None:
        duration = _require_number(declared_duration, "timeline.durationSeconds")
        if not math.isclose(duration, calculated_duration, rel_tol=0.0, abs_tol=1e-6):
            raise ToolingError("invalid-profile", "timeline.durationSeconds does not match the frame range and FPS.")

    resolution = _require_dict(raw.get("resolution"), "resolution")
    width = _require_int(resolution.get("width"), "resolution.width", minimum=16)
    height = _require_int(resolution.get("height"), "resolution.height", minimum=16)
    if width > 16384 or height > 16384:
        raise ToolingError("invalid-profile", "Final render dimensions may not exceed 16384 pixels.")
    if width % 2 or height % 2:
        raise ToolingError("invalid-profile", "Final render dimensions must be even for the reviewed encoders.")
    percentage = _require_int(resolution.get("percentage", 100), "resolution.percentage")
    if percentage != 100:
        raise ToolingError("invalid-profile", "Final render resolution percentage must be exactly 100.")
    pixel_aspect_x = _require_number(resolution.get("pixelAspectX", 1), "resolution.pixelAspectX")
    pixel_aspect_y = _require_number(resolution.get("pixelAspectY", 1), "resolution.pixelAspectY")
    if schema_version == "1.0.0" and (
        not math.isclose(pixel_aspect_x, 1.0, abs_tol=1e-9)
        or not math.isclose(pixel_aspect_y, 1.0, abs_tol=1e-9)
    ):
        raise ToolingError("invalid-profile", "The current production renderer supports square pixels only.")

    output_value = raw.get("output")
    if output_value is None:
        frames_subdirectory = "frames"
    else:
        output_settings = _require_dict(output_value, "output")
        frames_subdirectory = _safe_output_component(
            output_settings.get("framesSubdirectory", "frames"),
            "output.framesSubdirectory",
        )

    sequence = _require_dict(raw.get("imageSequence"), "imageSequence")
    image_format = _require_string(sequence.get("format"), "imageSequence.format").upper().replace("OPENEXR", "OPEN_EXR")
    if image_format not in {"PNG", "OPEN_EXR"}:
        raise ToolingError("invalid-profile", "imageSequence.format must be PNG or OPEN_EXR.")
    expected_extension = "png" if image_format == "PNG" else "exr"
    extension = _require_string(sequence.get("extension", expected_extension), "imageSequence.extension").lower().lstrip(".")
    if extension != expected_extension:
        raise ToolingError("invalid-profile", "Image-sequence extension does not match its format.")
    bit_depth = _require_int(sequence.get("bitDepth"), "imageSequence.bitDepth")
    if image_format == "PNG" and bit_depth not in {8, 16}:
        raise ToolingError("invalid-profile", "PNG bit depth must be 8 or 16.")
    if image_format == "OPEN_EXR" and bit_depth != 16:
        raise ToolingError("invalid-profile", "OpenEXR output must use 16-bit half-float channels.")
    compression = sequence.get("compression")
    if image_format == "PNG" and (
        isinstance(compression, bool) or not isinstance(compression, int) or not 0 <= compression <= 100
    ):
        raise ToolingError("invalid-profile", "PNG compression must be an integer from 0 through 100.")
    if image_format == "OPEN_EXR" and str(compression).upper() not in {"ZIP", "PIZ"}:
        raise ToolingError("invalid-profile", "Final OpenEXR compression must be ZIP or PIZ.")
    color_mode = _require_string(sequence.get("colorMode", "RGB"), "imageSequence.colorMode").upper()
    if color_mode != "RGB":
        raise ToolingError("invalid-profile", "Final image sequences must use opaque RGB without alpha.")
    sequence_color_management = _require_dict(sequence.get("colorManagement"), "imageSequence.colorManagement")
    display_transform_baked = sequence_color_management.get("displayTransformBaked")
    if not isinstance(display_transform_baked, bool):
        raise ToolingError("invalid-profile", "imageSequence.colorManagement.displayTransformBaked must be boolean.")
    if (image_format == "PNG") != display_transform_baked:
        raise ToolingError(
            "invalid-profile",
            "PNG must bake the reviewed display transform; OpenEXR must remain scene-linear.",
        )
    filename_pattern = _require_string(sequence.get("filenamePattern"), "imageSequence.filenamePattern")
    if filename_pattern in {f"frame_######.{extension}", f"frame_{{frame:000000}}.{extension}"}:
        filename_pattern = f"frame_%06d.{extension}"
    match = FRAME_PATTERN.fullmatch(filename_pattern)
    if match is None or match.group(1) != extension:
        raise ToolingError("invalid-profile", f"filenamePattern must be frame_%06d.{extension}.")
    for frame in (frame_start, frame_end):
        if re.fullmatch(rf"frame_\d{{6}}\.{extension}", filename_pattern % frame) is None:
            raise ToolingError("invalid-profile", "filenamePattern does not produce six-digit frame names.")

    hashes = raw.get("hashes")
    if not isinstance(hashes, dict):
        hashes = {}
    scene_hash = _canonical_hash(
        _first(raw, "approvedSceneSha256", "sceneSha256") or _first(hashes, "approvedSceneSha256", "sceneSha256"),
        "approvedSceneSha256",
    )

    render_settings = _require_dict(raw.get("render"), "render")
    render_engine = _require_string(render_settings.get("engine"), "render.engine")
    if not render_engine.startswith("BLENDER_EEVEE"):
        raise ToolingError("invalid-profile", "Final render engine must be Blender EEVEE.")
    render_samples = _require_int(render_settings.get("samples"), "render.samples")
    if render_samples > 4096:
        raise ToolingError("invalid-profile", "render.samples cannot exceed 4096.")
    shadow_pool_size = render_settings.get("shadowPoolSize")
    if shadow_pool_size is not None and str(shadow_pool_size) not in {"128", "256", "512", "1024", "2048"}:
        raise ToolingError("invalid-profile", "render.shadowPoolSize is not an approved EEVEE enum value.")
    for name in ("motionBlur", "useCompositing", "filmTransparent"):
        if not isinstance(render_settings.get(name), bool):
            raise ToolingError("invalid-profile", f"render.{name} must be boolean.")
    for name in ("rayTracing", "highQualityNormals", "volumetricShadows"):
        if name in render_settings and not isinstance(render_settings.get(name), bool):
            raise ToolingError("invalid-profile", f"render.{name} must be boolean.")
    if render_settings.get("highQualityNormals") is True:
        raise ToolingError(
            "unsupported-render-setting",
            "Blender 5.2 EEVEE exposes no high-quality-normals setting; render.highQualityNormals must be false.",
        )
    if render_settings.get("filmTransparent") is True:
        raise ToolingError(
            "unsupported-render-setting",
            "render.filmTransparent must be false because the canonical production sequence is opaque RGB.",
        )
    if "shadowRayCount" in render_settings:
        shadow_ray_count = _require_int(render_settings.get("shadowRayCount"), "render.shadowRayCount")
        if shadow_ray_count > 4:
            raise ToolingError("invalid-profile", "render.shadowRayCount may not exceed 4.")
    if "shadowResolutionScale" in render_settings:
        shadow_resolution_scale_value = render_settings.get("shadowResolutionScale")
        if (
            isinstance(shadow_resolution_scale_value, bool)
            or not isinstance(shadow_resolution_scale_value, (int, float))
            or not math.isfinite(float(shadow_resolution_scale_value))
            or not 0.0 <= float(shadow_resolution_scale_value) <= 1.0
        ):
            raise ToolingError(
                "invalid-profile",
                "render.shadowResolutionScale must be a finite number from 0 through 1.",
            )
    ray_tracing_method = render_settings.get("rayTracingMethod")
    if ray_tracing_method is not None and str(ray_tracing_method).upper() not in {"PROBE", "SCREEN"}:
        raise ToolingError("invalid-profile", "render.rayTracingMethod must be PROBE or SCREEN.")
    volumetric_tile_size = render_settings.get("volumetricTileSize")
    if volumetric_tile_size is not None and str(volumetric_tile_size) not in {"1", "2", "4", "8", "16"}:
        raise ToolingError("invalid-profile", "render.volumetricTileSize must be 1, 2, 4, 8, or 16.")
    for name, maximum in (
        ("volumetricSamples", 256),
        ("volumetricShadowSamples", 128),
        ("volumetricRayDepth", 16),
    ):
        if name in render_settings:
            value = _require_int(render_settings.get(name), f"render.{name}")
            if value > maximum:
                raise ToolingError("invalid-profile", f"render.{name} may not exceed {maximum}.")
    dither = render_settings.get("ditherIntensity")
    if (
        isinstance(dither, bool)
        or not isinstance(dither, (int, float))
        or not math.isfinite(float(dither))
        or float(dither) < 0
    ):
        raise ToolingError("invalid-profile", "render.ditherIntensity must be finite.")

    compositor_value = raw.get("compositor", render_settings.get("compositor"))
    if compositor_value is not None:
        compositor = _require_dict(compositor_value, "compositor")
        compositor_enabled = compositor.get("enabled")
        if compositor_enabled is None and schema_version == "1.0.0":
            compositor_enabled = render_settings.get("useCompositing")
        if not isinstance(compositor_enabled, bool):
            raise ToolingError("invalid-profile", "compositor.enabled must be boolean.")
        if compositor_enabled != render_settings.get("useCompositing"):
            raise ToolingError("invalid-profile", "compositor.enabled and render.useCompositing must agree.")
        fog_glow = _first(compositor, "fogGlowEnabled", "fogGlow")
        if fog_glow is None and schema_version == "1.0.0":
            fog_glow = compositor_enabled
        if not isinstance(fog_glow, bool):
            raise ToolingError("invalid-profile", "compositor.fogGlowEnabled must be boolean.")
        if "fogGlow" in compositor and compositor.get("fogGlow") != fog_glow:
            raise ToolingError("invalid-profile", "compositor.fogGlow and fogGlowEnabled must agree.")
        if compositor_enabled is False and fog_glow is True:
            raise ToolingError("invalid-profile", "compositor.fogGlow must be false when the compositor is disabled.")
        if "name" in compositor:
            _require_string(compositor.get("name"), "compositor.name")
        quality = compositor.get("fogGlowQuality")
        if quality is not None and str(quality).upper() not in {"LOW", "MEDIUM", "HIGH"}:
            raise ToolingError("invalid-profile", "compositor.fogGlowQuality must be LOW, MEDIUM, or HIGH.")
        for compositor_name, compositor_minimum, compositor_maximum in (
            ("fogGlowThreshold", 0.0, 100.0),
            ("fogGlowStrength", 0.0, 1.0),
            ("fogGlowSize", 0.0, 1.0),
        ):
            compositor_setting_value = compositor.get(compositor_name)
            if compositor_setting_value is not None and (
                isinstance(compositor_setting_value, bool)
                or not isinstance(compositor_setting_value, (int, float))
                or not math.isfinite(float(compositor_setting_value))
                or not compositor_minimum <= float(compositor_setting_value) <= compositor_maximum
            ):
                raise ToolingError(
                    "invalid-profile",
                    f"compositor.{compositor_name} must be a finite number from "
                    f"{compositor_minimum} through {compositor_maximum}.",
                )
        if "fogGlowIterations" in compositor:
            iterations = _require_int(compositor.get("fogGlowIterations"), "compositor.fogGlowIterations")
            if iterations > 5:
                raise ToolingError("invalid-profile", "compositor.fogGlowIterations may not exceed 5.")

    color_management = _require_dict(raw.get("colorManagement"), "colorManagement")
    for name in ("displayDevice", "viewTransform", "look", "sequencerColorSpace"):
        _require_string(color_management.get(name), f"colorManagement.{name}")
    display_device = str(color_management["displayDevice"])
    view_transform = str(color_management["viewTransform"])
    look = str(color_management["look"])
    sequencer_color_space = str(color_management["sequencerColorSpace"])
    if display_device not in BLENDER_52_DISPLAY_DEVICES:
        raise ToolingError("invalid-profile", "colorManagement.displayDevice is unavailable in reviewed Blender 5.2.")
    if view_transform not in BLENDER_52_VIEW_LOOKS:
        raise ToolingError("invalid-profile", "colorManagement.viewTransform is unavailable in reviewed Blender 5.2.")
    if look not in BLENDER_52_VIEW_LOOKS[view_transform]:
        raise ToolingError(
            "invalid-profile",
            "colorManagement.look is incompatible with the selected Blender 5.2 view transform.",
        )
    if sequencer_color_space not in BLENDER_52_SEQUENCER_COLOR_SPACES:
        raise ToolingError(
            "invalid-profile",
            "colorManagement.sequencerColorSpace is unavailable in reviewed Blender 5.2.",
        )
    for color_name in ("exposure", "gamma"):
        color_value = color_management.get(color_name)
        if (
            isinstance(color_value, bool)
            or not isinstance(color_value, (int, float))
            or not math.isfinite(float(color_value))
        ):
            raise ToolingError("invalid-profile", f"colorManagement.{color_name} must be finite.")

    chunking_value = raw.get("chunking")
    chunking = _require_dict(chunking_value, "chunking")
    chunk_size = _require_int(_first(chunking, "framesPerChunk", "chunkSize"), "chunking.framesPerChunk")
    rationale = _require_string(chunking.get("rationale"), "chunking.rationale")
    if chunk_size > frame_end - frame_start + 1:
        raise ToolingError("invalid-profile", "Chunk size cannot exceed the complete frame count.")
    if chunk_size > 1200:
        raise ToolingError("invalid-profile", "Production chunks may not exceed 1,200 frames.")

    storage_value = _require_dict(raw.get("storage"), "storage")

    def storage_bytes(name: str) -> int:
        gib_value = _require_number(storage_value.get(name), f"storage.{name}")
        return math.ceil(gib_value * GIB)

    planned_frame_bytes = storage_bytes("plannedFrameSequenceGiB")
    projected_master_bytes = storage_bytes("projectedMasterGiB")
    projected_delivery_bytes = storage_bytes("projectedDeliveryGiB")
    support_reserve_bytes = storage_bytes("supportReserveGiB")
    contingency_value = storage_value.get("contingencyMultiplier")
    if isinstance(contingency_value, bool) or not isinstance(contingency_value, (int, float)):
        raise ToolingError("invalid-profile", "storage.contingencyMultiplier must be numeric.")
    contingency_multiplier = float(contingency_value)
    if not math.isfinite(contingency_multiplier) or contingency_multiplier < 1.0:
        raise ToolingError("invalid-profile", "storage.contingencyMultiplier must be finite and at least 1.0.")
    minimum_launch_free_bytes = storage_bytes("minimumLaunchFreeGiB")
    full_dynamic_requirement = math.ceil(
        (planned_frame_bytes + projected_master_bytes + projected_delivery_bytes + support_reserve_bytes)
        * contingency_multiplier
    )
    if minimum_launch_free_bytes < full_dynamic_requirement:
        raise ToolingError(
            "invalid-profile",
            "storage.minimumLaunchFreeGiB must cover the full projected output plus contingency.",
        )
    storage = StoragePolicy(
        planned_frame_sequence_bytes=planned_frame_bytes,
        projected_master_bytes=projected_master_bytes,
        projected_delivery_bytes=projected_delivery_bytes,
        support_reserve_bytes=support_reserve_bytes,
        contingency_multiplier=contingency_multiplier,
        minimum_launch_free_bytes=minimum_launch_free_bytes,
    )

    audio_value = raw.get("audio", {})
    audio = _require_dict(audio_value, "audio")
    if "sha256" in audio:
        audio = {**audio, "sha256": _canonical_hash(audio["sha256"], "audio.sha256")}
    for name in ("sampleRate", "channels"):
        if name in audio:
            _require_int(audio[name], f"audio.{name}")
    if "durationSeconds" in audio:
        _require_number(audio["durationSeconds"], "audio.durationSeconds")
    encoding_value = raw.get("encoding", {})
    encoding = _require_dict(encoding_value, "encoding")
    profile = RenderProfile(
        source_path=path,
        source_sha256=sha256_file(path),
        canonical_sha256=(
            _canonical_hash(source_raw.get("profileSha256"), "profileSha256")
            if schema_version == BUILDER_PROFILE_SCHEMA_VERSION
            else canonical_profile_sha256(source_raw)
        ),
        schema_version=schema_version,
        raw=raw,
        display_name=display_name,
        project=project,
        preset=preset,
        profile_id=profile_id,
        blender_version=blender_version,
        frame_start=frame_start,
        frame_end=frame_end,
        fps=fps,
        width=width,
        height=height,
        resolution_percentage=percentage,
        pixel_aspect_x=pixel_aspect_x,
        pixel_aspect_y=pixel_aspect_y,
        frames_subdirectory=frames_subdirectory,
        image=ImageSequenceProfile(
            format=image_format,
            extension=extension,
            bit_depth=bit_depth,
            color_mode=color_mode,
            filename_pattern=filename_pattern,
            compression=sequence.get("compression"),
            color_management=sequence.get("colorManagement"),
        ),
        color_management=color_management,
        approved_scene_sha256=scene_hash,
        chunk_size=chunk_size,
        chunk_rationale=rationale,
        storage=storage,
        audio=audio,
        encoding=encoding,
    )
    for output_kind in ("master", "delivery"):
        _encoding_settings(profile, output_kind, require_enabled=False)
    return profile


def validate_scene(profile: RenderProfile, scene_path: Path) -> tuple[Path, str]:
    scene = _absolute_path(scene_path, "Approved scene", must_exist=True, file=True)
    if scene.suffix.casefold() != ".blend":
        raise ToolingError("invalid-scene", "Approved scene must be a .blend file.")
    digest = sha256_file(scene)
    if not hmac.compare_digest(digest, profile.approved_scene_sha256):
        raise ToolingError("scene-hash-mismatch", "Approved scene hash does not match the render profile.")
    return scene, digest


def validate_authorization(profile: RenderProfile, supplied: str) -> None:
    if not hmac.compare_digest(supplied, profile.authorization_token):
        raise ToolingError("authorization-token-rejected", "The exact scene-specific authorization token was not supplied.")


def profile_validation_summary(profile: RenderProfile, scene_path: Path | None = None) -> dict[str, Any]:
    approved_scene = profile.raw.get("approvedScene")
    approved_scene_path = approved_scene.get("path") if isinstance(approved_scene, dict) else None
    result: dict[str, Any] = {
        "ok": True,
        "validationStatus": "valid",
        "schemaVersion": profile.schema_version,
        "displayName": profile.display_name,
        "profileId": profile.profile_id,
        "project": profile.project,
        "preset": profile.preset,
        "exactSavedFileSha256": profile.source_sha256,
        "informationalProfileSha256": profile.canonical_sha256,
        "informationalHashIsAuthorizationIdentity": False,
        "approvedScene": {
            "declaredPath": approved_scene_path,
            "sha256": profile.approved_scene_sha256,
            "verified": False,
        },
        "timeline": {
            "frameStart": profile.frame_start,
            "frameEnd": profile.frame_end,
            "frameCount": profile.frame_count,
            "fps": profile.fps,
            "durationSeconds": profile.duration_seconds,
        },
        "resolution": {
            "width": profile.width,
            "height": profile.height,
            "percentage": 100,
            "pixelAspectX": profile.pixel_aspect_x,
            "pixelAspectY": profile.pixel_aspect_y,
        },
        "imageSequence": {
            "format": profile.image.format,
            "bitDepth": profile.image.bit_depth,
            "colorMode": profile.image.color_mode,
            "filenamePattern": profile.image.filename_pattern,
            "framesSubdirectory": profile.frames_subdirectory,
        },
        "chunking": {"framesPerChunk": profile.chunk_size, "rationale": profile.chunk_rationale},
        "expectedBlenderVersion": profile.blender_version,
        "expectedAuthorizationToken": profile.authorization_token,
    }
    if scene_path is not None:
        scene, digest = validate_scene(profile, scene_path)
        result["approvedScene"] = {
            **result["approvedScene"],
            "verifiedPath": str(scene),
            "fileName": scene.name,
            "sha256": digest,
            "verified": True,
        }
    return result


@dataclass(frozen=True)
class FrameFileResult:
    frame: int
    file_name: str
    ok: bool
    width: int | None
    height: int | None
    bit_depth: int | None
    sha256: str | None
    size_bytes: int
    error: str | None


def _read_exact(handle: Any, length: int, label: str) -> bytes:
    data = cast(bytes, handle.read(length))
    if len(data) != length:
        raise ToolingError("corrupt-frame", f"Truncated {label}.")
    return data


def _validate_png(path: Path) -> tuple[int, int, int, str, int]:
    digest = hashlib.sha256()
    size = path.stat().st_size
    if size <= 0:
        raise ToolingError("corrupt-frame", "Frame is zero bytes.")
    width: int | None = None
    height: int | None = None
    bit_depth: int | None = None
    color_type: int | None = None
    saw_idat = False
    saw_iend = False
    with path.open("rb") as handle:
        signature = _read_exact(handle, 8, "PNG signature")
        digest.update(signature)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise ToolingError("corrupt-frame", "Invalid PNG signature.")
        while not saw_iend:
            header = _read_exact(handle, 8, "PNG chunk header")
            digest.update(header)
            length, chunk_type = struct.unpack(">I4s", header)
            if length > 512 * 1024 * 1024:
                raise ToolingError("corrupt-frame", "PNG chunk is unreasonably large.")
            checksum = zlib.crc32(chunk_type)
            remaining = length
            payload_prefix = bytearray()
            while remaining:
                block = _read_exact(handle, min(1024 * 1024, remaining), "PNG chunk data")
                digest.update(block)
                checksum = zlib.crc32(block, checksum)
                if chunk_type == b"IHDR" and len(payload_prefix) < 13:
                    payload_prefix.extend(block[: 13 - len(payload_prefix)])
                remaining -= len(block)
            stored_crc = _read_exact(handle, 4, "PNG chunk CRC")
            digest.update(stored_crc)
            if struct.unpack(">I", stored_crc)[0] != checksum & 0xFFFFFFFF:
                raise ToolingError("corrupt-frame", "PNG chunk CRC mismatch.")
            if chunk_type == b"IHDR":
                if width is not None or length != 13:
                    raise ToolingError("corrupt-frame", "Invalid PNG IHDR chunk.")
                width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                    ">IIBBBBB", bytes(payload_prefix)
                )
                if width <= 0 or height <= 0 or compression != 0 or filtering != 0 or interlace not in {0, 1}:
                    raise ToolingError("corrupt-frame", "Invalid PNG image header values.")
            elif chunk_type == b"IDAT":
                saw_idat = True
            elif chunk_type == b"IEND":
                if length != 0:
                    raise ToolingError("corrupt-frame", "Invalid PNG IEND chunk.")
                saw_iend = True
        trailing = handle.read(1)
        if trailing:
            raise ToolingError("corrupt-frame", "PNG contains trailing data after IEND.")
    if width is None or height is None or bit_depth is None or color_type != 2 or not saw_idat:
        raise ToolingError("corrupt-frame", "PNG must contain an opaque RGB image and image data.")
    return width, height, bit_depth, digest.hexdigest().upper(), size


def _read_c_string(handle: Any, limit: int = 4096) -> bytes:
    result = bytearray()
    while len(result) <= limit:
        character = handle.read(1)
        if not character:
            raise ToolingError("corrupt-frame", "Truncated OpenEXR header string.")
        if character == b"\0":
            return bytes(result)
        result.extend(character)
    raise ToolingError("corrupt-frame", "OpenEXR header string is unreasonably long.")


def _validate_exr(path: Path) -> tuple[int, int, int, str, int]:
    size = path.stat().st_size
    if size <= 0:
        raise ToolingError("corrupt-frame", "Frame is zero bytes.")
    digest = sha256_file(path)
    with path.open("rb") as handle:
        magic, version = struct.unpack("<II", _read_exact(handle, 8, "OpenEXR signature"))
        if magic != 20000630:
            raise ToolingError("corrupt-frame", "Invalid OpenEXR signature.")
        if version & (0x00000200 | 0x00000800 | 0x00001000):
            raise ToolingError("corrupt-frame", "Tiled, deep, or multipart OpenEXR frames are unsupported.")
        attributes: dict[str, tuple[str, bytes]] = {}
        while True:
            name_bytes = _read_c_string(handle)
            if not name_bytes:
                break
            type_bytes = _read_c_string(handle)
            length = struct.unpack("<I", _read_exact(handle, 4, "OpenEXR attribute size"))[0]
            if length > 64 * 1024 * 1024:
                raise ToolingError("corrupt-frame", "OpenEXR attribute is unreasonably large.")
            value = _read_exact(handle, length, "OpenEXR attribute")
            try:
                name = name_bytes.decode("ascii")
                type_name = type_bytes.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ToolingError("corrupt-frame", "OpenEXR attribute names must be ASCII.") from exc
            attributes[name] = (type_name, value)
        data_window = attributes.get("dataWindow")
        if data_window is None or data_window[0] != "box2i" or len(data_window[1]) != 16:
            raise ToolingError("corrupt-frame", "OpenEXR dataWindow is missing or invalid.")
        minimum_x, minimum_y, maximum_x, maximum_y = struct.unpack("<iiii", data_window[1])
        width = maximum_x - minimum_x + 1
        height = maximum_y - minimum_y + 1
        if width <= 0 or height <= 0:
            raise ToolingError("corrupt-frame", "OpenEXR dataWindow dimensions are invalid.")
        compression_attribute = attributes.get("compression")
        if compression_attribute is None or len(compression_attribute[1]) != 1:
            raise ToolingError("corrupt-frame", "OpenEXR compression attribute is missing or invalid.")
        compression = compression_attribute[1][0]
        scanlines_per_block = {0: 1, 1: 1, 2: 1, 3: 16, 4: 32, 5: 16, 6: 32, 7: 32, 8: 32, 9: 256}.get(compression)
        if scanlines_per_block is None:
            raise ToolingError("corrupt-frame", "OpenEXR compression mode is unsupported.")
        block_count = math.ceil(height / scanlines_per_block)
        offsets = [struct.unpack("<Q", _read_exact(handle, 8, "OpenEXR offset table"))[0] for _ in range(block_count)]
        if len(set(offsets)) != len(offsets) or any(offset < handle.tell() or offset + 8 > size for offset in offsets):
            raise ToolingError("corrupt-frame", "OpenEXR chunk offsets are invalid.")
        for offset in offsets:
            handle.seek(offset)
            _y_coordinate, data_size = struct.unpack("<iI", _read_exact(handle, 8, "OpenEXR scanline block"))
            if data_size <= 0 or offset + 8 + data_size > size:
                raise ToolingError("corrupt-frame", "OpenEXR scanline block is truncated or invalid.")
    return width, height, 16, digest, size


def _validate_frame(path: Path, frame: int, profile: RenderProfile) -> FrameFileResult:
    try:
        if profile.image.format == "PNG":
            width, height, bit_depth, digest, size = _validate_png(path)
        else:
            width, height, bit_depth, digest, size = _validate_exr(path)
        if width != profile.width or height != profile.height:
            raise ToolingError(
                "wrong-frame-dimensions",
                f"Expected {profile.width}x{profile.height}; found {width}x{height}.",
            )
        if bit_depth != profile.image.bit_depth:
            raise ToolingError("wrong-frame-bit-depth", f"Expected {profile.image.bit_depth}-bit; found {bit_depth}-bit.")
        return FrameFileResult(frame, path.name, True, width, height, bit_depth, digest, size, None)
    except (OSError, ToolingError) as exc:
        code = exc.code if isinstance(exc, ToolingError) else "frame-read-failed"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return FrameFileResult(frame, path.name, False, None, None, None, None, size, code)


def _ranges(frames: Iterable[int]) -> list[dict[str, int]]:
    ordered = sorted(set(frames))
    if not ordered:
        return []
    result: list[dict[str, int]] = []
    start = previous = ordered[0]
    for frame in ordered[1:]:
        if frame != previous + 1:
            result.append({"startFrame": start, "endFrame": previous, "frameCount": previous - start + 1})
            start = frame
        previous = frame
    result.append({"startFrame": start, "endFrame": previous, "frameCount": previous - start + 1})
    return result


@dataclass
class FrameScan:
    expected_start: int
    expected_end: int
    valid: dict[int, FrameFileResult]
    invalid: list[FrameFileResult]
    duplicates: dict[int, list[str]]
    unexpected: list[str]

    @property
    def expected_count(self) -> int:
        return self.expected_end - self.expected_start + 1

    @property
    def missing(self) -> list[int]:
        return [frame for frame in range(self.expected_start, self.expected_end + 1) if frame not in self.valid]

    @property
    def complete(self) -> bool:
        return (
            len(self.valid) == self.expected_count
            and not self.invalid
            and not self.duplicates
            and not self.unexpected
            and not self.missing
        )

    @property
    def frame_set_digest(self) -> str:
        digest = hashlib.sha256()
        for frame, result in sorted(self.valid.items()):
            digest.update(f"{frame:06d} {result.sha256}\n".encode())
        return digest.hexdigest().upper()

    def summary(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "expectedFrameCount": self.expected_count,
            "validFrameCount": len(self.valid),
            "missingFrameCount": len(self.missing),
            "missingRanges": _ranges(self.missing),
            "validRanges": _ranges(self.valid),
            "invalidFrames": [
                {"frame": item.frame, "fileName": item.file_name, "error": item.error, "sizeBytes": item.size_bytes}
                for item in sorted(self.invalid, key=lambda item: (item.frame, item.file_name.casefold()))
            ],
            "duplicateFrames": [
                {"frame": frame, "fileNames": sorted(names)} for frame, names in sorted(self.duplicates.items())
            ],
            "unexpectedFrameFiles": sorted(self.unexpected),
            "frameSetSha256": self.frame_set_digest,
        }


def scan_frames(
    directory: Path,
    profile: RenderProfile,
    *,
    expected_start: int | None = None,
    expected_end: int | None = None,
    workers: int = 4,
) -> FrameScan:
    start = profile.frame_start if expected_start is None else expected_start
    end = profile.frame_end if expected_end is None else expected_end
    if start < profile.frame_start or end > profile.frame_end or end < start:
        raise ToolingError("invalid-frame-range", "Requested scan range is outside the render profile.")
    if workers < 1 or workers > 32:
        raise ToolingError("invalid-workers", "Frame scanner workers must be between 1 and 32.")
    directory = _absolute_path(directory, "Frames directory")
    candidates: dict[int, list[Path]] = {}
    unexpected: list[str] = []
    if directory.exists():
        if not directory.is_dir():
            raise ToolingError("invalid-frames-directory", "Frames path is not a directory.")
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            raise ToolingError("frames-scan-failed", "Frames directory could not be listed.") from exc
        for entry in entries:
            if entry.is_symlink():
                if entry.name.casefold().startswith("frame_"):
                    unexpected.append(entry.name)
                continue
            if not entry.is_file():
                continue
            match = FRAME_LIKE_PATTERN.fullmatch(entry.name)
            if match is None:
                if entry.name.casefold().startswith("frame_"):
                    unexpected.append(entry.name)
                continue
            frame = int(match.group(1))
            expected_name = profile.image.filename(frame)
            if entry.name != expected_name:
                unexpected.append(entry.name)
            candidates.setdefault(frame, []).append(entry)
    duplicates = {frame: [item.name for item in items] for frame, items in candidates.items() if len(items) > 1}
    invalid: list[FrameFileResult] = []
    canonical: list[tuple[int, Path]] = []
    for frame, items in candidates.items():
        if frame < start or frame > end:
            invalid.extend(
                FrameFileResult(frame, item.name, False, None, None, None, None, item.stat().st_size, "frame-outside-range")
                for item in items
            )
        elif len(items) == 1 and items[0].name == profile.image.filename(frame):
            canonical.append((frame, items[0]))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda item: _validate_frame(item[1], item[0], profile), canonical))
    valid = {result.frame: result for result in results if result.ok and result.frame not in duplicates}
    invalid.extend(result for result in results if not result.ok)
    return FrameScan(start, end, valid, invalid, duplicates, unexpected)


def _split_chunks(ranges: Sequence[dict[str, int]], chunk_size: int) -> list[dict[str, int]]:
    result: list[dict[str, int]] = []
    for missing_range in ranges:
        current = missing_range["startFrame"]
        end = missing_range["endFrame"]
        while current <= end:
            chunk_end = min(end, current + chunk_size - 1)
            result.append({"startFrame": current, "endFrame": chunk_end, "frameCount": chunk_end - current + 1})
            current = chunk_end + 1
    return result


def storage_requirement(profile: RenderProfile, remaining_frame_count: int) -> dict[str, int | float]:
    if remaining_frame_count < 0 or remaining_frame_count > profile.frame_count:
        raise ToolingError("invalid-storage-count", "Remaining frame count is outside the render profile.")
    policy = profile.storage
    remaining_frame_bytes = (
        policy.planned_frame_sequence_bytes * remaining_frame_count + profile.frame_count - 1
    ) // profile.frame_count
    required_free_bytes = math.ceil(
        (
            remaining_frame_bytes
            + policy.projected_master_bytes
            + policy.projected_delivery_bytes
            + policy.support_reserve_bytes
        )
        * policy.contingency_multiplier
    )
    return {
        "remainingFrameCount": remaining_frame_count,
        "remainingFrameBytes": remaining_frame_bytes,
        "requiredFreeBytes": required_free_bytes,
    }


def _render_manifest_path(output: Path) -> Path:
    return output / "manifests" / "render-manifest.json"


def _validate_output_root(output: Path, scene: Path, profile: RenderProfile) -> Path:
    output = _absolute_path(output, "Output directory")
    if output == Path(output.anchor) or len(output.parts) < 3:
        raise ToolingError("unsafe-output-root", "Output directory is too broad.")
    for source, label in ((scene, "Approved scene"), (profile.source_path, "Render profile")):
        try:
            source.relative_to(output)
        except ValueError:
            continue
        raise ToolingError("unsafe-output-root", f"{label} must not be stored inside the production output directory.")
    return output


def _expected_frame_contract(profile: RenderProfile) -> dict[str, Any]:
    return {
        "frameStart": profile.frame_start,
        "frameEnd": profile.frame_end,
        "frameCount": profile.frame_count,
        "fps": profile.fps,
        "width": profile.width,
        "height": profile.height,
        "pixelAspectX": profile.pixel_aspect_x,
        "pixelAspectY": profile.pixel_aspect_y,
        "framesSubdirectory": profile.frames_subdirectory,
        "filenamePattern": profile.image.filename_pattern,
        "format": profile.image.format,
        "bitDepth": profile.image.bit_depth,
        "colorMode": profile.image.color_mode,
    }


def _manifest_contract_value(schema_version: str, contract: dict[str, Any], field: str) -> Any:
    if field in contract:
        return contract[field]
    if schema_version == "1.0.0":
        return {"pixelAspectX": 1.0, "pixelAspectY": 1.0, "framesSubdirectory": "frames"}.get(field)
    return None


def _manifest_identity(profile: RenderProfile, scene: Path, scene_hash: str, output: Path) -> dict[str, Any]:
    return {
        "scene": {"fileName": scene.name, "sha256": scene_hash},
        "renderProfile": {
            "fileName": profile.source_path.name,
            "sha256": profile.source_sha256,
            "informationalProfileSha256": profile.canonical_sha256,
            "schemaVersion": profile.schema_version,
            "profileId": profile.profile_id,
        },
        "outputDirectory": str(output),
        "frameContract": _expected_frame_contract(profile),
    }


def _assert_manifest_identity(manifest: dict[str, Any], identity: dict[str, Any]) -> None:
    if manifest.get("kind") != RENDER_MANIFEST_KIND or manifest.get("schemaVersion") != TOOL_SCHEMA_VERSION:
        raise ToolingError("render-manifest-mismatch", "Existing render manifest has an unsupported identity.")
    for group in ("scene", "renderProfile"):
        actual = manifest.get(group)
        expected = identity[group]
        if not isinstance(actual, dict) or actual.get("sha256") != expected["sha256"]:
            raise ToolingError("render-manifest-mismatch", f"Existing {group} hash does not match this run.")
    if manifest.get("outputDirectory") != identity["outputDirectory"]:
        raise ToolingError("render-manifest-mismatch", "Existing manifest belongs to another output directory.")
    actual_contract = manifest.get("frameContract")
    expected_contract = identity["frameContract"]
    if not isinstance(actual_contract, dict):
        raise ToolingError("output-contract-mismatch", "Existing manifest has no render frame contract.")
    contract_labels = {
        "frameStart": "frame range",
        "frameEnd": "frame range",
        "frameCount": "frame range",
        "fps": "FPS",
        "width": "resolution",
        "height": "resolution",
        "pixelAspectX": "pixel aspect",
        "pixelAspectY": "pixel aspect",
        "framesSubdirectory": "published frames directory",
        "filenamePattern": "image filename pattern",
        "format": "image format",
        "bitDepth": "image bit depth",
        "colorMode": "image color mode",
    }
    profile_identity = identity["renderProfile"]
    identity_schema_version = str(profile_identity.get("schemaVersion", ""))
    for field, label in contract_labels.items():
        if _manifest_contract_value(identity_schema_version, actual_contract, field) != expected_contract[field]:
            raise ToolingError(
                "output-contract-mismatch",
                f"Existing output {label} does not match this render profile ({field}).",
            )


def output_compatibility(profile: RenderProfile, scene_path: Path, output_path: Path) -> dict[str, Any]:
    """Inspect a potential output root without creating or mutating it."""
    scene, scene_hash = validate_scene(profile, scene_path)
    output = _validate_output_root(output_path, scene, profile)
    expected = _manifest_identity(profile, scene, scene_hash, output)
    manifest_path = _render_manifest_path(output)
    if not output.exists():
        return {"compatible": True, "status": "new-output", "mismatches": [], "manifestPath": str(manifest_path)}
    if not output.is_dir():
        return {
            "compatible": False,
            "status": "invalid-output",
            "mismatches": [{"code": "not-a-directory"}],
            "manifestPath": str(manifest_path),
        }
    entries = list(output.iterdir())
    if not manifest_path.is_file():
        return {
            "compatible": not entries,
            "status": "empty-output" if not entries else "unmanaged-output",
            "mismatches": [] if not entries else [{"code": "missing-render-manifest"}],
            "manifestPath": str(manifest_path),
        }
    manifest = _read_json(manifest_path, "Render manifest")
    mismatches: list[dict[str, Any]] = []
    if manifest.get("kind") != RENDER_MANIFEST_KIND or manifest.get("schemaVersion") != TOOL_SCHEMA_VERSION:
        mismatches.append({"code": "manifest-schema-mismatch"})
    actual_scene = manifest.get("scene")
    if not isinstance(actual_scene, dict) or actual_scene.get("sha256") != expected["scene"]["sha256"]:
        mismatches.append({"code": "scene-hash-mismatch"})
    actual_profile = manifest.get("renderProfile")
    if not isinstance(actual_profile, dict) or actual_profile.get("sha256") != expected["renderProfile"]["sha256"]:
        mismatches.append({"code": "profile-hash-mismatch"})
    if manifest.get("outputDirectory") != expected["outputDirectory"]:
        mismatches.append({"code": "output-path-mismatch"})
    actual_contract = manifest.get("frameContract")
    expected_contract = expected["frameContract"]
    contract_codes = {
        "frameStart": "frame-range-mismatch",
        "frameEnd": "frame-range-mismatch",
        "frameCount": "frame-range-mismatch",
        "fps": "fps-mismatch",
        "width": "resolution-mismatch",
        "height": "resolution-mismatch",
        "pixelAspectX": "pixel-aspect-mismatch",
        "pixelAspectY": "pixel-aspect-mismatch",
        "framesSubdirectory": "frames-subdirectory-mismatch",
        "filenamePattern": "image-pattern-mismatch",
        "format": "image-format-mismatch",
        "bitDepth": "image-bit-depth-mismatch",
        "colorMode": "image-color-mode-mismatch",
    }
    if not isinstance(actual_contract, dict):
        mismatches.append({"code": "missing-frame-contract"})
    else:
        seen_codes: set[str] = set()
        for field, code in contract_codes.items():
            actual_value = _manifest_contract_value(profile.schema_version, actual_contract, field)
            if actual_value != expected_contract[field] and code not in seen_codes:
                seen_codes.add(code)
                mismatches.append(
                    {
                        "code": code,
                        "field": field,
                        "expected": expected_contract[field],
                        "actual": actual_value,
                    }
                )
    frame_set = manifest.get("frameSet")
    existing_count = frame_set.get("validFrameCount", 0) if isinstance(frame_set, dict) else 0
    return {
        "compatible": not mismatches,
        "status": "matching-output" if not mismatches else "incompatible-output",
        "mismatches": mismatches,
        "manifestPath": str(manifest_path),
        "existingValidFrameCount": existing_count,
        "authorizationAccepted": _manifest_authorization_accepted(manifest, profile),
        "profileSha256": profile.source_sha256,
        "sceneSha256": scene_hash,
    }


def _authorization_digest(profile: RenderProfile) -> str:
    return hashlib.sha256(profile.authorization_token.encode()).hexdigest().upper()


def _manifest_authorization_accepted(manifest: dict[str, Any], profile: RenderProfile) -> bool:
    authorization = manifest.get("authorization")
    return (
        isinstance(authorization, dict)
        and authorization.get("status") == "operator-token-accepted"
        and hmac.compare_digest(str(authorization.get("expectedTokenSha256", "")), _authorization_digest(profile))
        and hmac.compare_digest(str(authorization.get("acceptedTokenSha256", "")), _authorization_digest(profile))
    )


def _assert_manifest_authorized(manifest: dict[str, Any], profile: RenderProfile) -> None:
    if not _manifest_authorization_accepted(manifest, profile):
        raise ToolingError(
            "authorization-not-recorded",
            "Render manifest does not record acceptance of the exact scene-and-profile authorization token.",
        )


def _new_render_manifest(profile: RenderProfile, identity: dict[str, Any]) -> dict[str, Any]:
    created = _now()
    return {
        "schemaVersion": TOOL_SCHEMA_VERSION,
        "kind": RENDER_MANIFEST_KIND,
        "status": "incomplete",
        "createdAt": created,
        "updatedAt": created,
        **identity,
        "authorization": {
            "status": "pending-operator-approval",
            "expectedTokenSha256": _authorization_digest(profile),
        },
        "frameContract": _expected_frame_contract(profile),
        "chunking": {"framesPerChunk": profile.chunk_size, "rationale": profile.chunk_rationale},
        "storagePolicy": profile.storage.as_dict(),
        "frameSet": {},
        "chunks": [],
    }


def _load_or_initialize_manifest(
    output: Path,
    profile: RenderProfile,
    identity: dict[str, Any],
    *,
    initialize: bool,
) -> dict[str, Any] | None:
    manifest_path = _render_manifest_path(output)
    if output.exists() and not output.is_dir():
        raise ToolingError("invalid-output-directory", "Output path exists and is not a directory.")
    if manifest_path.is_file():
        manifest = _read_json(manifest_path, "Render manifest")
        _assert_manifest_identity(manifest, identity)
        return manifest
    if output.exists():
        entries = list(output.iterdir())
        if entries:
            raise ToolingError(
                "unmanaged-output-directory",
                "Non-empty output directory has no matching TrackPrompt render manifest; refusing to reuse it.",
            )
    if not initialize:
        return None
    if not output.exists():
        output.mkdir(parents=True, exist_ok=False)
    for name in (
        profile.frames_subdirectory,
        "logs",
        "checkpoints",
        "manifests",
        "master",
        "delivery",
        "qa",
    ):
        (output / name).mkdir(exist_ok=False)
    manifest = _new_render_manifest(profile, identity)
    _atomic_write_json(manifest_path, manifest)
    return manifest


def _update_manifest_scan(manifest: dict[str, Any], scan: FrameScan, chunks: list[dict[str, int]]) -> None:
    manifest["updatedAt"] = _now()
    manifest["status"] = "complete" if scan.complete else "incomplete"
    manifest["frameSet"] = scan.summary()
    manifest["frameIndex"] = {
        f"{frame:06d}": {"sha256": item.sha256, "sizeBytes": item.size_bytes}
        for frame, item in sorted(scan.valid.items())
    }
    manifest["remainingChunkPlan"] = chunks


def _overwrite_invalid_frames_enabled(profile: RenderProfile) -> bool:
    production = profile.raw.get("production")
    return isinstance(production, dict) and production.get("overwriteInvalidFrames") is True


def quarantine_invalid_frames(
    profile: RenderProfile,
    scene_path: Path,
    output_path: Path,
    authorization_token: str,
    *,
    workers: int,
) -> dict[str, Any]:
    """Recoverably move invalid canonical frames out of an authorized managed output.

    Inspection and dry-run paths never call this operation. The production launcher
    invokes it only after acquiring the render mutex and validating the exact token.
    """
    scene, scene_hash = validate_scene(profile, scene_path)
    validate_authorization(profile, authorization_token)
    output = _validate_output_root(output_path, scene, profile)
    identity = _manifest_identity(profile, scene, scene_hash, output)
    manifest = _load_or_initialize_manifest(output, profile, identity, initialize=False)
    frames_directory = output / profile.frames_subdirectory
    if frames_directory.is_symlink():
        raise ToolingError("unsafe-frames-directory", "Published frames directory must not be a symbolic link.")
    scan = scan_frames(frames_directory, profile, workers=workers)
    enabled = _overwrite_invalid_frames_enabled(profile)
    base_result: dict[str, Any] = {
        "ok": True,
        "enabled": enabled,
        "quarantinedFrameCount": 0,
        "quarantinedFrames": [],
        "quarantineDirectory": None,
        "quarantineManifest": None,
        "frameScan": scan.summary(),
    }
    if not enabled:
        return base_result
    if scan.duplicates or scan.unexpected:
        raise ToolingError(
            "quarantine-ambiguity",
            "Invalid-frame quarantine refuses duplicate or noncanonical frame names; inspect the output manually.",
        )
    if not scan.invalid:
        return base_result
    if manifest is None:
        raise ToolingError(
            "quarantine-requires-manifest",
            "Invalid frames can be quarantined only from an existing matching managed render output.",
        )
    _assert_manifest_authorized(manifest, profile)

    resolved_frames = _absolute_path(frames_directory, "Published frames directory", must_exist=True)
    sources: list[tuple[FrameFileResult, Path, str, int]] = []
    for item in sorted(scan.invalid, key=lambda value: (value.frame, value.file_name.casefold())):
        source = frames_directory / item.file_name
        if source.is_symlink() or not source.is_file():
            raise ToolingError("unsafe-invalid-frame", "Invalid frame must be a direct regular file in the frames directory.")
        resolved_source = _absolute_path(source, "Invalid frame", must_exist=True, file=True)
        if resolved_source.parent != resolved_frames or resolved_source.name != item.file_name:
            raise ToolingError("unsafe-invalid-frame", "Invalid frame escaped the managed frames directory.")
        sources.append((item, resolved_source, sha256_file(resolved_source), resolved_source.stat().st_size))

    checkpoints_directory = output / "checkpoints"
    if checkpoints_directory.is_symlink() or not checkpoints_directory.is_dir():
        raise ToolingError("unsafe-checkpoints-directory", "Managed checkpoints directory must be a direct regular directory.")
    resolved_output = _absolute_path(output, "Output directory", must_exist=True)
    resolved_checkpoints = _absolute_path(checkpoints_directory, "Checkpoints directory", must_exist=True)
    if resolved_checkpoints.parent != resolved_output or resolved_checkpoints.name != "checkpoints":
        raise ToolingError("unsafe-checkpoints-directory", "Managed checkpoints directory escaped the output root.")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    quarantine_directory = resolved_checkpoints / f"quarantine-invalid-{timestamp}-{uuid4().hex}"
    quarantine_directory.mkdir(exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    records: list[dict[str, Any]] = []
    quarantine_manifest_path = quarantine_directory / "quarantine-manifest.json"
    try:
        for item, source, source_hash, size_bytes in sources:
            destination = quarantine_directory / source.name
            os.replace(source, destination)
            moved.append((source, destination))
            records.append(
                {
                    "frame": item.frame,
                    "fileName": item.file_name,
                    "validationError": item.error,
                    "sizeBytes": size_bytes,
                    "sha256": source_hash,
                    "originalPath": str(source.relative_to(output)),
                    "quarantinePath": str(destination.relative_to(output)),
                }
            )
        quarantine_payload = {
            "schemaVersion": TOOL_SCHEMA_VERSION,
            "kind": "trackprompt-invalid-frame-quarantine",
            "createdAt": _now(),
            "scene": identity["scene"],
            "renderProfile": identity["renderProfile"],
            "frameContract": identity["frameContract"],
            "frames": records,
        }
        _atomic_write_json(quarantine_manifest_path, quarantine_payload)

        post_scan = scan_frames(frames_directory, profile, workers=workers)
        renderable = not post_scan.invalid and not post_scan.duplicates and not post_scan.unexpected
        chunks = _split_chunks(post_scan.summary()["missingRanges"], profile.chunk_size) if renderable else []
        updated_manifest = copy.deepcopy(manifest)
        quarantines = updated_manifest.get("quarantines")
        if quarantines is None:
            quarantines = []
            updated_manifest["quarantines"] = quarantines
        if not isinstance(quarantines, list):
            raise ToolingError("render-manifest-mismatch", "Render manifest quarantine history is invalid.")
        quarantines.append(
            {
                "createdAt": quarantine_payload["createdAt"],
                "frameCount": len(records),
                "frames": [record["frame"] for record in records],
                "manifestPath": str(quarantine_manifest_path.relative_to(output)),
            }
        )
        _update_manifest_scan(updated_manifest, post_scan, chunks)
        _atomic_write_json(_render_manifest_path(output), updated_manifest)
    except Exception:
        rollback_error: OSError | None = None
        for source, destination in reversed(moved):
            try:
                if destination.exists() and not source.exists():
                    os.replace(destination, source)
            except OSError as exc:
                rollback_error = exc
        try:
            if quarantine_manifest_path.exists():
                quarantine_manifest_path.unlink()
            if quarantine_directory.exists() and not any(quarantine_directory.iterdir()):
                quarantine_directory.rmdir()
        except OSError as exc:
            rollback_error = rollback_error or exc
        if rollback_error is not None:
            raise ToolingError(
                "quarantine-rollback-failed",
                "Invalid-frame quarantine failed and could not fully restore the original files; inspect checkpoints.",
            ) from rollback_error
        raise

    return {
        **base_result,
        "quarantinedFrameCount": len(records),
        "quarantinedFrames": records,
        "quarantineDirectory": str(quarantine_directory),
        "quarantineManifest": str(quarantine_manifest_path),
        "frameScan": post_scan.summary(),
    }


def _frame_summary_from_index(profile: RenderProfile, frame_index: dict[str, Any]) -> dict[str, Any]:
    valid_frames: list[int] = []
    digest = hashlib.sha256()
    for key, value in sorted(frame_index.items()):
        if not re.fullmatch(r"\d{6}", key) or not isinstance(value, dict):
            raise ToolingError("render-manifest-mismatch", "Render manifest frame index is invalid.")
        frame = int(key)
        frame_hash = value.get("sha256")
        if frame < profile.frame_start or frame > profile.frame_end or not isinstance(frame_hash, str):
            raise ToolingError("render-manifest-mismatch", "Render manifest frame index is outside the profile.")
        frame_hash = _canonical_hash(frame_hash, "frameIndex.sha256")
        valid_frames.append(frame)
        digest.update(f"{frame:06d} {frame_hash}\n".encode())
    valid_set = set(valid_frames)
    missing = [frame for frame in range(profile.frame_start, profile.frame_end + 1) if frame not in valid_set]
    return {
        "complete": not missing and len(valid_frames) == profile.frame_count,
        "expectedFrameCount": profile.frame_count,
        "validFrameCount": len(valid_frames),
        "missingFrameCount": len(missing),
        "missingRanges": _ranges(missing),
        "validRanges": _ranges(valid_frames),
        "invalidFrames": [],
        "duplicateFrames": [],
        "unexpectedFrameFiles": [],
        "frameSetSha256": digest.hexdigest().upper(),
    }


def render_plan(
    profile: RenderProfile,
    scene_path: Path,
    output_path: Path,
    *,
    initialize: bool,
    authorization_token: str | None,
    require_authorization: bool,
    chunk_size_override: int | None,
    chunk_rationale_override: str | None,
    workers: int,
) -> dict[str, Any]:
    if initialize and not require_authorization:
        raise ToolingError(
            "authorization-required",
            "Render initialization requires exact operator authorization; inspection mode cannot initialize output.",
        )
    scene, scene_hash = validate_scene(profile, scene_path)
    if require_authorization:
        validate_authorization(profile, authorization_token or "")
    output = _validate_output_root(output_path, scene, profile)
    chunk_size = profile.chunk_size
    rationale = profile.chunk_rationale
    if chunk_size_override is not None:
        if chunk_size_override < 1 or chunk_size_override > profile.chunk_size:
            raise ToolingError("invalid-chunk-size", "Chunk-size override exceeds the reviewed profile chunk size.")
        if not chunk_rationale_override or not chunk_rationale_override.strip():
            raise ToolingError("missing-chunk-rationale", "A chunk-size override requires an explicit rationale.")
        chunk_size = chunk_size_override
        rationale = chunk_rationale_override.strip()
    identity = _manifest_identity(profile, scene, scene_hash, output)
    manifest = _load_or_initialize_manifest(output, profile, identity, initialize=initialize)
    frames = output / profile.frames_subdirectory
    scan = scan_frames(frames, profile, workers=workers)
    scan_summary = scan.summary()
    renderable = not scan.invalid and not scan.duplicates and not scan.unexpected
    chunks = _split_chunks(scan_summary["missingRanges"], chunk_size) if renderable else []
    remaining_frames = int(scan_summary["missingFrameCount"])
    chunk_storage_requirements: list[dict[str, int | float]] = []
    for chunk in chunks:
        chunk_storage_requirements.append(
            {
                "startFrame": chunk["startFrame"],
                "endFrame": chunk["endFrame"],
                **storage_requirement(profile, remaining_frames),
            }
        )
        remaining_frames -= chunk["frameCount"]
    if manifest is not None and initialize:
        manifest["chunking"] = {"framesPerChunk": chunk_size, "rationale": rationale}
        if require_authorization:
            manifest["authorization"] = {
                **manifest.get("authorization", {}),
                "status": "operator-token-accepted",
                "acceptedAt": _now(),
                "acceptedTokenSha256": _authorization_digest(profile),
            }
        _assert_manifest_authorized(manifest, profile)
        _update_manifest_scan(manifest, scan, chunks)
        _atomic_write_json(_render_manifest_path(output), manifest)
    warnings: list[str] = []
    if "onedrive" in (part.casefold() for part in output.parts):
        warnings.append("Output is under a cloud-synchronized OneDrive path; use a reliable local drive for production.")
    return {
        "ok": renderable,
        "mode": "initialized" if initialize else "inspection-only",
        "renderable": renderable,
        "complete": scan.complete,
        "authorizationRequired": require_authorization,
        "authorizationAccepted": manifest is not None and _manifest_authorization_accepted(manifest, profile),
        "expectedAuthorizationToken": profile.authorization_token if not require_authorization else None,
        "scene": {"fileName": scene.name, "sha256": scene_hash},
        "renderProfile": {
            "fileName": profile.source_path.name,
            "sha256": profile.source_sha256,
            "informationalProfileSha256": profile.canonical_sha256,
            "schemaVersion": profile.schema_version,
            "profileId": profile.profile_id,
            "displayName": profile.display_name,
        },
        "frameContract": _expected_frame_contract(profile),
        "outputDirectory": str(output),
        "framesDirectory": str(frames),
        "chunking": {"framesPerChunk": chunk_size, "rationale": rationale},
        "storage": {
            "policy": profile.storage.as_dict(),
            "currentRequirement": storage_requirement(profile, int(scan_summary["missingFrameCount"])),
            "chunkLaunchRequirements": chunk_storage_requirements,
        },
        "frameScan": scan_summary,
        "chunks": chunks,
        "warnings": warnings,
        "expectedBlenderVersion": profile.blender_version,
    }


def _relative_file(path_value: str | None, output: Path, parent_name: str) -> str | None:
    if path_value is None:
        return None
    path = _contained_path(Path(path_value), output / parent_name, f"{parent_name} file")
    if not path.is_file():
        raise ToolingError("missing-input", f"{parent_name} file does not exist.")
    return str(path.relative_to(output))


def commit_chunk(
    profile: RenderProfile,
    scene_path: Path,
    output_path: Path,
    temporary_frames_path: Path,
    *,
    start: int,
    end: int,
    stdout_log: str | None,
    stderr_log: str | None,
    workers: int,
) -> dict[str, Any]:
    scene, scene_hash = validate_scene(profile, scene_path)
    output = _validate_output_root(output_path, scene, profile)
    identity = _manifest_identity(profile, scene, scene_hash, output)
    manifest_path = _render_manifest_path(output)
    manifest = _read_json(manifest_path, "Render manifest")
    _assert_manifest_identity(manifest, identity)
    _assert_manifest_authorized(manifest, profile)
    temporary_frames = _contained_path(temporary_frames_path, output / "checkpoints", "Temporary frames directory")
    if not temporary_frames.is_dir():
        raise ToolingError("missing-input", "Temporary frames directory does not exist.")
    scan = scan_frames(temporary_frames, profile, expected_start=start, expected_end=end, workers=workers)
    if not scan.complete:
        raise ToolingError("chunk-validation-failed", "Chunk frames are incomplete, corrupt, duplicated, or unexpected.")
    frames_directory = output / profile.frames_subdirectory
    for frame in range(start, end + 1):
        destination = frames_directory / profile.image.filename(frame)
        if destination.exists():
            raise ToolingError("overwrite-refused", f"Destination frame {frame} already exists.")
    published: list[int] = []
    for frame in range(start, end + 1):
        source = temporary_frames / profile.image.filename(frame)
        destination = frames_directory / source.name
        try:
            os.link(source, destination)
            source.unlink()
        except FileExistsError as exc:
            raise ToolingError("overwrite-refused", f"Destination frame {frame} appeared during commit.") from exc
        except OSError as exc:
            raise ToolingError("atomic-frame-publish-failed", f"Could not atomically publish frame {frame}.") from exc
        published.append(frame)
    stdout_relative = _relative_file(stdout_log, output, "logs")
    stderr_relative = _relative_file(stderr_log, output, "logs")
    checkpoint_name = f"chunk_{start:06d}_{end:06d}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    checkpoint_path = output / "checkpoints" / checkpoint_name
    checkpoint = {
        "schemaVersion": TOOL_SCHEMA_VERSION,
        "kind": "trackprompt-final-render-chunk",
        "completedAt": _now(),
        "startFrame": start,
        "endFrame": end,
        "frameCount": end - start + 1,
        "frames": [
            {
                "frame": frame,
                "fileName": scan.valid[frame].file_name,
                "sizeBytes": scan.valid[frame].size_bytes,
                "sha256": scan.valid[frame].sha256,
            }
            for frame in range(start, end + 1)
        ],
        "stdoutLog": stdout_relative,
        "stderrLog": stderr_relative,
    }
    _atomic_write_json(checkpoint_path, checkpoint)
    frame_index = manifest.get("frameIndex")
    if not isinstance(frame_index, dict):
        raise ToolingError("render-manifest-mismatch", "Render manifest has no validated frame index.")
    for frame in range(start, end + 1):
        item = scan.valid[frame]
        frame_index[f"{frame:06d}"] = {"sha256": item.sha256, "sizeBytes": item.size_bytes}
    frame_summary = _frame_summary_from_index(profile, frame_index)
    chunks = _split_chunks(frame_summary["missingRanges"], int(manifest["chunking"]["framesPerChunk"]))
    existing_chunks = manifest.get("chunks")
    if not isinstance(existing_chunks, list):
        existing_chunks = []
        manifest["chunks"] = existing_chunks
    existing_chunks.append(
        {
            "startFrame": start,
            "endFrame": end,
            "frameCount": end - start + 1,
            "checkpoint": str(checkpoint_path.relative_to(output)),
            "completedAt": checkpoint["completedAt"],
        }
    )
    manifest["updatedAt"] = _now()
    manifest["status"] = "complete" if frame_summary["complete"] else "incomplete"
    manifest["frameSet"] = frame_summary
    manifest["remainingChunkPlan"] = chunks
    _atomic_write_json(manifest_path, manifest)
    try:
        temporary_frames.rmdir()
        temporary_frames.parent.rmdir()
    except OSError:
        pass
    return {
        "ok": True,
        "publishedFrames": published,
        "checkpoint": str(checkpoint_path),
        "renderStatus": manifest["status"],
        "frameScan": frame_summary,
    }


def record_chunk_failure(
    profile: RenderProfile,
    scene_path: Path,
    output_path: Path,
    *,
    start: int,
    end: int,
    exit_code: int,
    stdout_log: str | None,
    stderr_log: str | None,
) -> dict[str, Any]:
    scene, scene_hash = validate_scene(profile, scene_path)
    output = _validate_output_root(output_path, scene, profile)
    identity = _manifest_identity(profile, scene, scene_hash, output)
    manifest_path = _render_manifest_path(output)
    manifest = _read_json(manifest_path, "Render manifest")
    _assert_manifest_identity(manifest, identity)
    _assert_manifest_authorized(manifest, profile)
    failure = {
        "startFrame": start,
        "endFrame": end,
        "frameCount": end - start + 1,
        "status": "failed",
        "exitCode": exit_code,
        "failedAt": _now(),
        "stdoutLog": _relative_file(stdout_log, output, "logs"),
        "stderrLog": _relative_file(stderr_log, output, "logs"),
    }
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list):
        chunks = []
        manifest["chunks"] = chunks
    chunks.append(failure)
    manifest["status"] = "incomplete"
    manifest["updatedAt"] = _now()
    _atomic_write_json(manifest_path, manifest)
    checkpoint = output / "checkpoints" / f"chunk_{start:06d}_{end:06d}_failed_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    _atomic_write_json(checkpoint, {"schemaVersion": TOOL_SCHEMA_VERSION, "kind": "trackprompt-final-render-chunk", **failure})
    return {"ok": True, "recorded": True, "checkpoint": str(checkpoint)}


def record_operator_stop(
    profile: RenderProfile,
    scene_path: Path,
    output_path: Path,
    *,
    completed_start: int | None = None,
    completed_end: int | None = None,
) -> dict[str, Any]:
    """Record a safe operator stop only after publication has finished."""
    scene, scene_hash = validate_scene(profile, scene_path)
    output = _validate_output_root(output_path, scene, profile)
    identity = _manifest_identity(profile, scene, scene_hash, output)
    manifest_path = _render_manifest_path(output)
    manifest = _read_json(manifest_path, "Render manifest")
    _assert_manifest_identity(manifest, identity)
    _assert_manifest_authorized(manifest, profile)
    if (completed_start is None) != (completed_end is None):
        raise ToolingError("invalid-stop-checkpoint", "A completed stop checkpoint requires both frame bounds.")
    completed_chunk: dict[str, int] | None = None
    if completed_start is not None and completed_end is not None:
        if completed_start < profile.frame_start or completed_end > profile.frame_end or completed_end < completed_start:
            raise ToolingError("invalid-stop-checkpoint", "The completed stop checkpoint is outside the profile range.")
        chunks = manifest.get("chunks")
        matching = [
            item
            for item in chunks if isinstance(item, dict)
            if item.get("startFrame") == completed_start
            and item.get("endFrame") == completed_end
            and item.get("completedAt")
        ] if isinstance(chunks, list) else []
        if not matching:
            raise ToolingError(
                "stop-before-publication-refused",
                "The requested stop checkpoint has not been validated and published.",
            )
        completed_chunk = {"startFrame": completed_start, "endFrame": completed_end}
    stopped_at = _now()
    manifest["status"] = "complete" if bool(manifest.get("frameSet", {}).get("complete")) else "incomplete"
    manifest["updatedAt"] = stopped_at
    manifest["runState"] = {
        "status": "stopped-after-current-chunk-by-operator",
        "stoppedAt": stopped_at,
        "completedChunk": completed_chunk,
        "resumePolicy": "cancel the stop request, then resume only missing validated frames with the exact scene, profile, authorization, and output",
    }
    _atomic_write_json(manifest_path, manifest)
    return {
        "ok": True,
        "status": "stopped-after-current-chunk-by-operator",
        "completedChunk": completed_chunk,
        "renderComplete": bool(manifest.get("frameSet", {}).get("complete")),
    }


def _parse_rate(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        numerator, separator, denominator = value.partition("/")
        try:
            result = float(numerator) / (float(denominator) if separator else 1.0)
        except (ValueError, ZeroDivisionError):
            return None
    else:
        return None
    return result if math.isfinite(result) and result > 0 else None


def probe_media(ffprobe_path: Path, media_path: Path) -> dict[str, Any]:
    ffprobe = _absolute_executable_path(ffprobe_path, "ffprobe executable")
    media = _absolute_path(media_path, "Media file", must_exist=True, file=True)
    command = [
        str(ffprobe),
        "-v",
        "error",
        "-count_frames",
        "-show_entries",
        (
            "format=duration,size,format_name:"
            "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,"
            "nb_frames,nb_read_frames,sample_rate,channels,profile,color_range,color_space,color_transfer,"
            "color_primaries,pix_fmt,duration"
        ),
        "-of",
        "json",
        str(media),
    ]
    try:
        completed = subprocess.run(command, shell=False, check=False, capture_output=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolingError("ffprobe-failed", "ffprobe could not inspect the media file.") from exc
    if completed.returncode != 0 or len(completed.stdout) > 2_000_000:
        raise ToolingError("ffprobe-failed", "ffprobe rejected the media file.")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ToolingError("ffprobe-invalid-output", "ffprobe returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise ToolingError("ffprobe-invalid-output", "ffprobe JSON has the wrong shape.")
    streams = payload.get("streams")
    if not isinstance(streams, list):
        streams = []
    video = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), None)
    format_info = payload.get("format")
    if not isinstance(format_info, dict):
        format_info = {}
    fps = None
    if video is not None:
        fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))

    def number(value: object) -> float | None:
        try:
            result = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    frame_count_value = video.get("nb_read_frames") if video is not None else None
    if not str(frame_count_value or "").isdigit() and video is not None:
        frame_count_value = video.get("nb_frames")
    return {
        "formatName": format_info.get("format_name"),
        "formatDurationSeconds": number(format_info.get("duration")),
        "sizeBytes": int(format_info["size"]) if str(format_info.get("size", "")).isdigit() else media.stat().st_size,
        "videoPresent": video is not None,
        "audioPresent": audio is not None,
        "videoCodec": video.get("codec_name") if video is not None else None,
        "videoProfile": video.get("profile") if video is not None else None,
        "audioCodec": audio.get("codec_name") if audio is not None else None,
        "width": int(video["width"]) if video is not None and isinstance(video.get("width"), int) else None,
        "height": int(video["height"]) if video is not None and isinstance(video.get("height"), int) else None,
        "fps": fps,
        "videoDurationSeconds": number(video.get("duration")) if video is not None else None,
        "audioDurationSeconds": number(audio.get("duration")) if audio is not None else None,
        "frameCount": int(str(frame_count_value)) if str(frame_count_value or "").isdigit() else None,
        "pixelFormat": video.get("pix_fmt") if video is not None else None,
        "colorRange": video.get("color_range") if video is not None else None,
        "colorPrimaries": video.get("color_primaries") if video is not None else None,
        "colorTransfer": video.get("color_transfer") if video is not None else None,
        "colorSpace": video.get("color_space") if video is not None else None,
        "sampleRate": int(audio["sample_rate"]) if audio is not None and str(audio.get("sample_rate", "")).isdigit() else None,
        "channels": int(audio["channels"]) if audio is not None and isinstance(audio.get("channels"), int) else None,
    }


def _audio_preflight(profile: RenderProfile, audio_path: Path, ffprobe_path: Path) -> dict[str, Any]:
    audio = _absolute_path(audio_path, "Approved audio", must_exist=True, file=True)
    expected_hash = profile.audio.get("sha256")
    if not isinstance(expected_hash, str):
        raise ToolingError("invalid-profile", "audio.sha256 is required before final encoding.")
    actual_hash = sha256_file(audio)
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise ToolingError("audio-hash-mismatch", "Approved audio hash does not match the render profile.")
    probe = probe_media(ffprobe_path, audio)
    if probe["audioPresent"] is not True:
        raise ToolingError("audio-stream-missing", "Approved audio file has no audio stream.")
    expected_sample_rate = profile.audio.get("sampleRate")
    if isinstance(expected_sample_rate, int) and probe["sampleRate"] != expected_sample_rate:
        raise ToolingError("audio-sample-rate-mismatch", "Approved audio sample rate does not match the profile.")
    expected_channels = profile.audio.get("channels")
    if isinstance(expected_channels, int) and probe["channels"] != expected_channels:
        raise ToolingError("audio-channel-mismatch", "Approved audio channel count does not match the profile.")
    duration = probe["formatDurationSeconds"] or probe["audioDurationSeconds"]
    tolerance = (1.0 / profile.fps) + 1e-6
    if duration is None or abs(duration - profile.duration_seconds) > tolerance:
        raise ToolingError(
            "audio-duration-mismatch",
            "Approved audio duration differs from the image-sequence clock by more than one frame.",
        )
    return {
        "sizeBytes": audio.stat().st_size,
        "sha256": actual_hash,
        "durationSeconds": duration,
        "videoClockDurationSeconds": profile.duration_seconds,
        "durationDifferenceSeconds": duration - profile.duration_seconds,
        "durationToleranceSeconds": tolerance,
        "codec": probe["audioCodec"],
        "sampleRate": probe["sampleRate"],
        "channels": probe["channels"],
    }


def _load_render_manifest(profile: RenderProfile, scene: Path, output: Path, scene_hash: str) -> dict[str, Any]:
    identity = _manifest_identity(profile, scene, scene_hash, output)
    manifest = _read_json(_render_manifest_path(output), "Render manifest")
    _assert_manifest_identity(manifest, identity)
    _assert_manifest_authorized(manifest, profile)
    return manifest


def encode_preflight(
    profile: RenderProfile,
    scene_path: Path,
    output_path: Path,
    audio_path: Path,
    ffprobe_path: Path,
    destination_path: Path,
    *,
    kind: str,
    workers: int,
) -> dict[str, Any]:
    settings = _encoding_settings(profile, kind)
    scene, scene_hash = validate_scene(profile, scene_path)
    output = _validate_output_root(output_path, scene, profile)
    if not output.is_dir():
        raise ToolingError("missing-output", "Production output directory does not exist.")
    manifest = _load_render_manifest(profile, scene, output, scene_hash)
    scan = scan_frames(output / profile.frames_subdirectory, profile, workers=workers)
    if not scan.complete:
        raise ToolingError("incomplete-frame-sequence", "Encoding requires every expected frame to be valid.")
    manifest_frame_set = manifest.get("frameSet")
    if (
        manifest.get("status") != "complete"
        or not isinstance(manifest_frame_set, dict)
        or manifest_frame_set.get("validFrameCount") != profile.frame_count
        or manifest_frame_set.get("frameSetSha256") != scan.frame_set_digest
    ):
        raise ToolingError("render-manifest-incomplete", "Render manifest does not agree with the complete frame sequence.")
    destination_parent = output / kind
    destination = _contained_path(destination_path, destination_parent, "Encoding destination")
    expected_extension = settings.get("fileExtension")
    if not isinstance(expected_extension, str) or not expected_extension.startswith("."):
        raise ToolingError("invalid-profile", f"encoding.{kind}.fileExtension must include a leading dot.")
    if destination.suffix.casefold() != expected_extension.casefold():
        raise ToolingError("invalid-destination", f"{kind} output must use the reviewed {expected_extension} extension.")
    if destination.exists():
        raise ToolingError("overwrite-refused", "Encoding destination already exists.")
    if not destination_parent.is_dir():
        raise ToolingError("missing-output-layout", f"Production {kind} directory is missing.")
    audio = _audio_preflight(profile, audio_path, ffprobe_path)
    return {
        "ok": True,
        "kind": kind,
        "scene": {"fileName": scene.name, "sha256": scene_hash},
        "renderProfile": {"fileName": profile.source_path.name, "sha256": profile.source_sha256},
        "renderManifest": str(_render_manifest_path(output)),
        "frameScan": scan.summary(),
        "audio": audio,
        "destination": str(destination),
    }


def _encoding_settings(
    profile: RenderProfile,
    kind: str,
    *,
    require_enabled: bool = True,
) -> dict[str, Any]:
    settings = profile.encoding.get(kind)
    if not isinstance(settings, dict):
        raise ToolingError("invalid-profile", f"encoding.{kind} must be defined before encoding.")
    enabled = settings.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ToolingError("invalid-profile", f"encoding.{kind}.enabled must be boolean.")
    if not enabled:
        if require_enabled:
            raise ToolingError("encoding-disabled", f"encoding.{kind} is disabled by the saved render profile.")
        return {**settings, "enabled": False}
    video_codec = _require_string(settings.get("videoCodec"), f"encoding.{kind}.videoCodec")
    if video_codec not in ALLOWED_VIDEO_CODECS[kind]:
        raise ToolingError("invalid-profile", f"encoding.{kind}.videoCodec is not an approved codec.")
    audio_codec = _require_string(settings.get("audioCodec"), f"encoding.{kind}.audioCodec")
    if audio_codec not in ALLOWED_AUDIO_CODECS:
        raise ToolingError("invalid-profile", f"encoding.{kind}.audioCodec is not approved.")
    pixel_format = _require_string(settings.get("pixelFormat"), f"encoding.{kind}.pixelFormat")
    if pixel_format not in ALLOWED_PIXEL_FORMATS:
        raise ToolingError("invalid-profile", f"encoding.{kind}.pixelFormat is not approved.")
    container = _require_string(settings.get("container"), f"encoding.{kind}.container").lower()
    if container not in {"matroska", "mov", "mp4"}:
        raise ToolingError("invalid-profile", f"encoding.{kind}.container is unsupported.")
    if video_codec == "ffv1" and container != "matroska":
        raise ToolingError("invalid-profile", "Reviewed FFV1 master output must use the matroska container.")
    fast_start_default = kind == "delivery" and container in {"mov", "mp4"}
    fast_start = settings.get("fastStart", fast_start_default)
    if not isinstance(fast_start, bool):
        raise ToolingError("invalid-profile", f"encoding.{kind}.fastStart must be boolean.")
    if fast_start and container not in {"mov", "mp4"}:
        raise ToolingError("invalid-profile", f"encoding.{kind}.fastStart requires a mov or mp4 container.")
    require_rec709 = settings.get("requireRec709Metadata", True)
    if not isinstance(require_rec709, bool):
        raise ToolingError("invalid-profile", f"encoding.{kind}.requireRec709Metadata must be boolean.")
    normalized: dict[str, Any] = {
        **settings,
        "enabled": True,
        "videoCodec": video_codec,
        "audioCodec": audio_codec,
        "pixelFormat": pixel_format,
        "container": container,
        "fastStart": fast_start,
        "requireRec709Metadata": require_rec709,
    }
    if profile.image.format == "PNG":
        normalized["displayToDeliveryFilter"] = _validated_video_filter(
            settings.get("displayToDeliveryFilter"),
            f"encoding.{kind}.displayToDeliveryFilter",
        )
    else:
        normalized["linearToDeliveryFilter"] = _validated_video_filter(
            settings.get("linearToDeliveryFilter"),
            f"encoding.{kind}.linearToDeliveryFilter",
        )
    color = _require_dict(settings.get("color", {}), f"encoding.{kind}.color")
    if require_rec709:
        for name in ("primaries", "transfer", "space"):
            if color.get(name) != "bt709":
                raise ToolingError("invalid-profile", "The SDR final encode must use explicit bt709 color metadata.")
    if "range" in color and color.get("range") != "tv":
        raise ToolingError("invalid-profile", f"encoding.{kind}.color.range must be tv for limited-range SDR output.")
    normalized["color"] = color
    if video_codec == "libx264":
        h264_profile = _require_string(settings.get("profile"), f"encoding.{kind}.profile").lower()
        if h264_profile != "high":
            raise ToolingError("invalid-profile", "Reviewed libx264 output must use profile high.")
        if pixel_format != "yuv420p":
            raise ToolingError("invalid-profile", "Reviewed libx264 High output must use yuv420p.")
        normalized["profile"] = h264_profile
    elif video_codec == "libx265":
        h265_profile = re.sub(r"[\s_-]+", "", _require_string(settings.get("profile"), f"encoding.{kind}.profile")).lower()
        if h265_profile != "main10":
            raise ToolingError("invalid-profile", "Reviewed libx265 output must use profile Main10.")
        if pixel_format != "yuv420p10le":
            raise ToolingError("invalid-profile", "Reviewed libx265 Main10 output must use yuv420p10le.")
        normalized["profile"] = "main10"
    elif video_codec == "prores_ks":
        prores_profile = str(settings.get("profile", "3"))
        prores_probe_profiles = {
            "0": "Proxy",
            "1": "LT",
            "2": "Standard",
            "3": "HQ",
            "4": "4444",
            "5": "XQ",
        }
        if prores_profile not in prores_probe_profiles:
            raise ToolingError("invalid-profile", "ProRes profile must be between 0 and 5.")
        normalized["profile"] = prores_profile
        normalized["expectedProbeProfile"] = prores_probe_profiles[prores_profile]
    if video_codec in {"libx264", "libx265"}:
        crf = settings.get("crf")
        if isinstance(crf, bool) or not isinstance(crf, int) or not 0 <= crf <= 30:
            raise ToolingError("invalid-profile", f"encoding.{kind}.crf must be an integer from 0 through 30.")
        normalized["crf"] = crf
    return normalized


def build_encode_arguments(
    profile: RenderProfile,
    frames_directory: Path,
    audio_path: Path,
    temporary_output_path: Path,
    *,
    kind: str,
) -> list[str]:
    settings = _encoding_settings(profile, kind)
    frames = _absolute_path(frames_directory, "Frames directory", must_exist=True)
    audio = _absolute_path(audio_path, "Approved audio", must_exist=True, file=True)
    temporary = _absolute_path(temporary_output_path, "Temporary encode output")
    arguments = [
        "-n",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "image2",
        "-framerate",
        f"{profile.fps:.12g}",
        "-start_number",
        str(profile.frame_start),
        "-i",
        str(frames / profile.image.filename_pattern),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-frames:v",
        str(profile.frame_count),
        "-r",
        f"{profile.fps:.12g}",
        "-c:v",
        settings["videoCodec"],
    ]
    if settings["videoCodec"] == "prores_ks":
        profile_value = str(settings.get("profile", "3"))
        if profile_value not in {"0", "1", "2", "3", "4", "5"}:
            raise ToolingError("invalid-profile", "ProRes profile must be between 0 and 5.")
        arguments.extend(["-profile:v", profile_value])
    elif settings["videoCodec"] in {"libx264", "libx265"}:
        arguments.extend(["-profile:v", settings["profile"]])
        preset = str(settings.get("preset", "slow"))
        if preset not in {"medium", "slow", "slower", "veryslow"}:
            raise ToolingError("invalid-profile", "Delivery preset must be medium, slow, slower, or veryslow.")
        crf = settings.get("crf")
        if isinstance(crf, bool) or not isinstance(crf, int) or not 0 <= crf <= 30:
            raise ToolingError("invalid-profile", "Delivery CRF must be an integer from 0 through 30.")
        arguments.extend(["-preset", preset, "-crf", str(crf)])
    arguments.extend(["-pix_fmt", settings["pixelFormat"]])
    filter_key = "displayToDeliveryFilter" if profile.image.format == "PNG" else "linearToDeliveryFilter"
    arguments.extend(["-vf", settings[filter_key]])
    color = cast(dict[str, Any], settings["color"])
    if settings["requireRec709Metadata"]:
        for flag, key in (
            ("-color_primaries", "primaries"),
            ("-color_trc", "transfer"),
            ("-colorspace", "space"),
        ):
            arguments.extend([flag, color[key]])
    if "range" in color:
        arguments.extend(["-color_range", color["range"]])
    arguments.extend(["-c:a", settings["audioCodec"]])
    if settings["audioCodec"] == "aac":
        bitrate = settings.get("audioBitrate")
        if not isinstance(bitrate, str) or AUDIO_BITRATE_PATTERN.fullmatch(bitrate) is None:
            raise ToolingError("invalid-profile", "AAC audioBitrate must use a value such as 320k.")
        arguments.extend(["-b:a", bitrate])
    arguments.extend(["-map_metadata", "-1"])
    if settings["fastStart"]:
        arguments.extend(["-movflags", "+faststart"])
    arguments.extend(["-f", settings["container"], str(temporary)])
    return arguments


def verify_media_contract(profile: RenderProfile, probe: dict[str, Any], *, kind: str) -> list[dict[str, Any]]:
    settings = _encoding_settings(profile, kind)
    issues: list[dict[str, Any]] = []
    expected_video_codec = settings.get("expectedVideoCodec", CODEC_PROBE_NAMES[settings["videoCodec"]])
    expected_audio_codec = settings.get("expectedAudioCodec", settings["audioCodec"])
    if probe["videoPresent"] is not True:
        issues.append({"code": "video-stream-missing"})
    if probe["audioPresent"] is not True:
        issues.append({"code": "audio-stream-missing"})
    if probe["width"] != profile.width or probe["height"] != profile.height:
        issues.append({"code": "resolution-mismatch", "actual": [probe["width"], probe["height"]]})
    if probe["fps"] is None or not math.isclose(float(probe["fps"]), profile.fps, rel_tol=0.0, abs_tol=1e-4):
        issues.append({"code": "fps-mismatch", "actual": probe["fps"]})
    if probe["videoCodec"] != expected_video_codec:
        issues.append({"code": "video-codec-mismatch", "actual": probe["videoCodec"]})
    if settings["videoCodec"] == "libx264" and str(probe.get("videoProfile", "")).casefold() != str(
        settings["profile"]
    ).casefold():
        issues.append({"code": "video-profile-mismatch", "actual": probe.get("videoProfile")})
    if settings["videoCodec"] == "libx265" and re.sub(
        r"[\s_-]+", "", str(probe.get("videoProfile", ""))
    ).casefold() != str(settings["profile"]).casefold():
        issues.append({"code": "video-profile-mismatch", "actual": probe.get("videoProfile")})
    if settings["videoCodec"] == "prores_ks" and probe.get("videoProfile") != settings["expectedProbeProfile"]:
        issues.append({"code": "video-profile-mismatch", "actual": probe.get("videoProfile")})
    if probe["audioCodec"] != expected_audio_codec:
        issues.append({"code": "audio-codec-mismatch", "actual": probe["audioCodec"]})
    if probe["pixelFormat"] != settings["pixelFormat"]:
        issues.append({"code": "pixel-format-mismatch", "actual": probe["pixelFormat"]})
    if probe.get("frameCount") != profile.frame_count:
        issues.append({"code": "frame-count-mismatch", "actual": probe["frameCount"]})
    duration = probe["videoDurationSeconds"] or probe["formatDurationSeconds"]
    tolerance = (1.0 / profile.fps) + 1e-6
    if duration is None or abs(float(duration) - profile.duration_seconds) > tolerance:
        issues.append({"code": "duration-mismatch", "actual": duration, "toleranceSeconds": tolerance})
    audio_duration = probe.get("audioDurationSeconds")
    expected_audio_duration_value = profile.audio.get("durationSeconds", profile.duration_seconds)
    expected_audio_duration = float(expected_audio_duration_value)
    if audio_duration is None or abs(expected_audio_duration - float(audio_duration)) > tolerance:
        issues.append({"code": "output-audio-duration-mismatch", "actual": audio_duration, "toleranceSeconds": tolerance})
    expected_channels = profile.audio.get("channels")
    if isinstance(expected_channels, int) and probe["channels"] != expected_channels:
        issues.append({"code": "audio-channel-mismatch", "actual": probe["channels"]})
    expected_sample_rate = profile.audio.get("sampleRate")
    if isinstance(expected_sample_rate, int) and probe["sampleRate"] != expected_sample_rate:
        issues.append({"code": "audio-sample-rate-mismatch", "actual": probe["sampleRate"]})
    if bool(settings.get("requireRec709Metadata", True)):
        for field in ("colorPrimaries", "colorTransfer", "colorSpace"):
            if probe[field] != "bt709":
                issues.append({"code": "rec709-metadata-missing", "field": field, "actual": probe[field]})
    color = cast(dict[str, Any], settings["color"])
    if "range" in color and probe.get("colorRange") != color["range"]:
        issues.append({"code": "color-range-mismatch", "actual": probe.get("colorRange")})
    return issues


def _temporary_residue(output: Path) -> list[str]:
    residue: list[str] = []
    for path in output.rglob("*"):
        name = path.name.casefold()
        if name.startswith(".inflight-") or ".partial-" in name or ".pending-" in name or name.endswith(".tmp"):
            residue.append(str(path.relative_to(output)))
    return sorted(residue)


def finalize_encode(
    profile: RenderProfile,
    scene_path: Path,
    output_path: Path,
    temporary_media_path: Path,
    destination_path: Path,
    audio_path: Path,
    ffprobe_path: Path,
    *,
    kind: str,
    workers: int,
) -> dict[str, Any]:
    _encoding_settings(profile, kind)
    scene, scene_hash = validate_scene(profile, scene_path)
    output = _validate_output_root(output_path, scene, profile)
    render_manifest = _load_render_manifest(profile, scene, output, scene_hash)
    if render_manifest.get("status") != "complete":
        raise ToolingError("render-manifest-incomplete", "Finalization requires a complete render manifest.")
    final_frame_scan = scan_frames(output / profile.frames_subdirectory, profile, workers=workers)
    manifest_frame_set = render_manifest.get("frameSet")
    if (
        not final_frame_scan.complete
        or not isinstance(manifest_frame_set, dict)
        or manifest_frame_set.get("frameSetSha256") != final_frame_scan.frame_set_digest
    ):
        raise ToolingError(
            "encoding-input-changed",
            "Frame sequence changed or became invalid after encoding preflight; refusing to publish media.",
        )
    temporary = _contained_path(temporary_media_path, output / kind, "Temporary encoded media")
    destination = _contained_path(destination_path, output / kind, "Final encoded media")
    if not temporary.is_file() or temporary.stat().st_size <= 0:
        raise ToolingError("missing-encode-output", "Temporary encoded media is missing or empty.")
    if destination.exists():
        raise ToolingError("overwrite-refused", "Final encoded media already exists.")
    approved_audio = _audio_preflight(profile, audio_path, ffprobe_path)
    probe = probe_media(ffprobe_path, temporary)
    issues = verify_media_contract(profile, probe, kind=kind)
    if issues:
        raise ToolingError("encode-verification-failed", f"Encoded media failed {len(issues)} contract check(s).")
    digest = sha256_file(temporary)
    manifest_path = output / "manifests" / f"{destination.name}.encode-manifest.json"
    if manifest_path.exists():
        raise ToolingError("overwrite-refused", "Encode manifest already exists.")
    media_size = temporary.stat().st_size
    encode_manifest = {
        "schemaVersion": TOOL_SCHEMA_VERSION,
        "kind": ENCODE_MANIFEST_KIND,
        "outputKind": kind,
        "createdAt": _now(),
        "scene": {"fileName": scene.name, "sha256": scene_hash},
        "renderProfile": {"fileName": profile.source_path.name, "sha256": profile.source_sha256},
        "renderManifest": str(_render_manifest_path(output).relative_to(output)),
        "renderFrameSetSha256": final_frame_scan.frame_set_digest,
        "approvedAudio": approved_audio,
        "clockPolicy": {
            "videoFrameCount": profile.frame_count,
            "shortestAllowed": False,
            "approvedAudioDurationSeconds": approved_audio["durationSeconds"],
            "maximumAudioShortfallSeconds": (1.0 / profile.fps) + 1e-6,
        },
        "media": {
            "fileName": destination.name,
            "relativePath": str(destination.relative_to(output)),
            "sizeBytes": media_size,
            "sha256": digest,
        },
        "probe": probe,
        "checks": {"ok": True, "issues": []},
    }
    pending_manifest = manifest_path.with_name(f".{manifest_path.name}.pending-{uuid4().hex}")
    _atomic_write_json(pending_manifest, encode_manifest)
    try:
        os.rename(temporary, destination)
    except FileExistsError as exc:
        pending_manifest.unlink(missing_ok=True)
        raise ToolingError("overwrite-refused", "Final encoded media appeared during finalization.") from exc
    except OSError as exc:
        pending_manifest.unlink(missing_ok=True)
        raise ToolingError("atomic-rename-failed", "Verified media could not be atomically renamed.") from exc
    try:
        os.rename(pending_manifest, manifest_path)
    except OSError as exc:
        raise ToolingError(
            "atomic-manifest-publish-failed",
            "Verified media was published, but its pending encode manifest could not be finalized.",
        ) from exc
    return {
        "ok": True,
        "kind": kind,
        "media": str(destination),
        "sha256": digest,
        "probe": probe,
        "encodeManifest": str(manifest_path),
    }


def _qa_frames(profile: RenderProfile) -> list[dict[str, Any]]:
    frame_values = {
        profile.frame_start: {"opening", "first-frame"},
        profile.frame_end: {"outro", "final-frame"},
    }
    raw_qa = profile.raw.get("visualQa")
    if isinstance(raw_qa, dict):
        named = raw_qa.get("namedFrames")
        if isinstance(named, list):
            for item in named:
                if not isinstance(item, dict) or not isinstance(item.get("frame"), int):
                    continue
                frame = min(profile.frame_end, max(profile.frame_start, item["frame"]))
                frame_values.setdefault(frame, set()).add(str(item.get("role", "named")))
        boundaries = raw_qa.get("sectionAndTransitionFrames")
        if isinstance(boundaries, list):
            for value in boundaries:
                if isinstance(value, int) and profile.frame_start <= value <= profile.frame_end:
                    frame_values.setdefault(value, set()).add("section-or-transition")
        motion = raw_qa.get("highMotionRanges")
        if isinstance(motion, list):
            for item in motion:
                if not isinstance(item, dict):
                    continue
                start = item.get("startFrame")
                end = item.get("endFrame")
                if isinstance(start, int) and isinstance(end, int):
                    for frame in range(max(profile.frame_start, start), min(profile.frame_end, end) + 1):
                        frame_values.setdefault(frame, set()).add("high-motion-consecutive")
    periodic_step = max(1, int(round(profile.fps * 45)))
    for frame in range(profile.frame_start, profile.frame_end + 1, periodic_step):
        frame_values.setdefault(frame, set()).add("periodic-45-seconds")
    return [{"frame": frame, "reasons": sorted(reasons)} for frame, reasons in sorted(frame_values.items())]


def verify_final(
    profile: RenderProfile,
    scene_path: Path,
    output_path: Path,
    media_path: Path,
    audio_path: Path,
    ffprobe_path: Path,
    encode_manifest_path: Path,
    *,
    kind: str,
) -> dict[str, Any]:
    _encoding_settings(profile, kind)
    scene, scene_hash = validate_scene(profile, scene_path)
    output = _validate_output_root(output_path, scene, profile)
    render_manifest = _load_render_manifest(profile, scene, output, scene_hash)
    media = _contained_path(media_path, output / kind, "Final media")
    if not media.is_file():
        raise ToolingError("missing-final-media", "Final media file does not exist.")
    encode_manifest_file = _contained_path(encode_manifest_path, output / "manifests", "Encode manifest")
    encode_manifest = _read_json(encode_manifest_file, "Encode manifest")
    if encode_manifest.get("kind") != ENCODE_MANIFEST_KIND or encode_manifest.get("outputKind") != kind:
        raise ToolingError("encode-manifest-mismatch", "Encode manifest kind does not match the requested media.")
    for group, expected_hash in (("scene", scene_hash), ("renderProfile", profile.source_sha256)):
        value = encode_manifest.get(group)
        if not isinstance(value, dict) or value.get("sha256") != expected_hash:
            raise ToolingError("encode-manifest-mismatch", f"Encode manifest {group} hash does not agree.")
    current_frame_set = render_manifest.get("frameSet")
    if (
        not isinstance(current_frame_set, dict)
        or encode_manifest.get("renderFrameSetSha256") != current_frame_set.get("frameSetSha256")
    ):
        raise ToolingError("encode-manifest-mismatch", "Encode manifest frame-set identity does not agree.")
    approved_audio = _audio_preflight(profile, audio_path, ffprobe_path)
    manifest_audio = encode_manifest.get("approvedAudio")
    if not isinstance(manifest_audio, dict):
        raise ToolingError("encode-manifest-mismatch", "Encode manifest has no approved-audio identity.")
    for field in ("sha256", "sizeBytes", "sampleRate", "channels"):
        if manifest_audio.get(field) != approved_audio.get(field):
            raise ToolingError("encode-manifest-mismatch", f"Encode manifest approved audio {field} does not agree.")
    manifest_audio_duration = manifest_audio.get("durationSeconds")
    if not isinstance(manifest_audio_duration, (int, float)) or not math.isclose(
        float(manifest_audio_duration), float(approved_audio["durationSeconds"]), rel_tol=0.0, abs_tol=1e-6
    ):
        raise ToolingError("encode-manifest-mismatch", "Encode manifest approved audio duration does not agree.")
    clock_policy = encode_manifest.get("clockPolicy")
    if (
        not isinstance(clock_policy, dict)
        or clock_policy.get("videoFrameCount") != profile.frame_count
        or clock_policy.get("shortestAllowed") is not False
        or clock_policy.get("approvedAudioDurationSeconds") != manifest_audio_duration
        or clock_policy.get("maximumAudioShortfallSeconds") != (1.0 / profile.fps) + 1e-6
    ):
        raise ToolingError("encode-manifest-mismatch", "Encode manifest clock policy is missing or altered.")
    media_identity = encode_manifest.get("media")
    if not isinstance(media_identity, dict) or media_identity.get("relativePath") != str(media.relative_to(output)):
        raise ToolingError("encode-manifest-mismatch", "Encode manifest points to another media file.")
    digest = sha256_file(media)
    probe = probe_media(ffprobe_path, media)
    issues = verify_media_contract(profile, probe, kind=kind)
    if media_identity.get("sha256") != digest:
        issues.append({"code": "media-hash-mismatch"})
    if media_identity.get("sizeBytes") != media.stat().st_size:
        issues.append({"code": "media-size-mismatch"})
    if render_manifest.get("status") != "complete":
        issues.append({"code": "render-manifest-incomplete"})
    residue = _temporary_residue(output)
    if residue:
        issues.append({"code": "temporary-residue-present", "paths": residue})
    return {
        "schemaVersion": TOOL_SCHEMA_VERSION,
        "kind": "trackprompt-final-verification",
        "verifiedAt": _now(),
        "ok": not issues,
        "verdict": "PASS" if not issues else "FAIL",
        "outputKind": kind,
        "scene": {"fileName": scene.name, "sha256": scene_hash},
        "renderProfile": {"fileName": profile.source_path.name, "sha256": profile.source_sha256},
        "media": {"fileName": media.name, "sizeBytes": media.stat().st_size, "sha256": digest},
        "approvedAudio": approved_audio,
        "probe": probe,
        "issues": issues,
        "temporaryResidue": residue,
        "visualQa": {
            "status": "pending-human-review",
            "claim": "No final visual-QA pass is claimed by this structural verification.",
            "extractionFrames": _qa_frames(profile),
        },
    }


def _common_parser(parser: argparse.ArgumentParser, *, output: bool = False) -> None:
    parser.add_argument("--profile", required=True)
    parser.add_argument("--scene", required=True)
    if output:
        parser.add_argument("--output", required=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TrackPrompt final-render safety, resume, encode, and verification tooling.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_info = subparsers.add_parser("profile-info")
    _common_parser(profile_info)

    validate_profile = subparsers.add_parser("validate-profile")
    validate_profile.add_argument("--profile", required=True)
    validate_profile.add_argument("--scene")
    validate_profile.add_argument("--output")

    compatibility = subparsers.add_parser("output-compatibility")
    _common_parser(compatibility, output=True)

    token = subparsers.add_parser("validate-token")
    _common_parser(token)
    token.add_argument("--authorization-token", required=True)

    scan = subparsers.add_parser("scan-frames")
    scan.add_argument("--profile", required=True)
    scan.add_argument("--frames", required=True)
    scan.add_argument("--workers", type=int, default=4)
    scan.add_argument("--require-complete", action="store_true")
    scan.add_argument("--json-output")

    plan = subparsers.add_parser("render-plan")
    _common_parser(plan, output=True)
    plan.add_argument("--initialize", action="store_true")
    plan.add_argument("--require-authorization", action="store_true")
    plan.add_argument("--authorization-token")
    plan.add_argument("--chunk-size", type=int)
    plan.add_argument("--chunk-rationale")
    plan.add_argument("--workers", type=int, default=4)
    plan.add_argument("--report-blocked", action="store_true")

    quarantine = subparsers.add_parser("quarantine-invalid-frames")
    _common_parser(quarantine, output=True)
    quarantine.add_argument("--authorization-token", required=True)
    quarantine.add_argument("--workers", type=int, default=4)

    commit = subparsers.add_parser("commit-chunk")
    _common_parser(commit, output=True)
    commit.add_argument("--temporary-frames", required=True)
    commit.add_argument("--start", type=int, required=True)
    commit.add_argument("--end", type=int, required=True)
    commit.add_argument("--stdout-log")
    commit.add_argument("--stderr-log")
    commit.add_argument("--workers", type=int, default=4)

    failure = subparsers.add_parser("record-chunk-failure")
    _common_parser(failure, output=True)
    failure.add_argument("--start", type=int, required=True)
    failure.add_argument("--end", type=int, required=True)
    failure.add_argument("--exit-code", type=int, required=True)
    failure.add_argument("--stdout-log")
    failure.add_argument("--stderr-log")

    stop = subparsers.add_parser("record-operator-stop")
    _common_parser(stop, output=True)
    stop.add_argument("--completed-start", type=int)
    stop.add_argument("--completed-end", type=int)

    encode_check = subparsers.add_parser("encode-preflight")
    _common_parser(encode_check, output=True)
    encode_check.add_argument("--audio", required=True)
    encode_check.add_argument("--ffprobe", required=True)
    encode_check.add_argument("--destination", required=True)
    encode_check.add_argument("--kind", choices=("master", "delivery"), required=True)
    encode_check.add_argument("--workers", type=int, default=4)

    encode_args = subparsers.add_parser("encode-arguments")
    encode_args.add_argument("--profile", required=True)
    encode_args.add_argument("--frames", required=True)
    encode_args.add_argument("--audio", required=True)
    encode_args.add_argument("--temporary-output", required=True)
    encode_args.add_argument("--kind", choices=("master", "delivery"), required=True)

    finalize = subparsers.add_parser("finalize-encode")
    _common_parser(finalize, output=True)
    finalize.add_argument("--temporary-media", required=True)
    finalize.add_argument("--destination", required=True)
    finalize.add_argument("--audio", required=True)
    finalize.add_argument("--ffprobe", required=True)
    finalize.add_argument("--kind", choices=("master", "delivery"), required=True)
    finalize.add_argument("--workers", type=int, default=4)

    verify = subparsers.add_parser("verify-final")
    _common_parser(verify, output=True)
    verify.add_argument("--media", required=True)
    verify.add_argument("--audio", required=True)
    verify.add_argument("--ffprobe", required=True)
    verify.add_argument("--encode-manifest", required=True)
    verify.add_argument("--kind", choices=("master", "delivery"), required=True)
    verify.add_argument("--json-output")
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "profile-info":
            profile = load_render_profile(Path(args.profile))
            result = profile_validation_summary(profile, Path(args.scene))
            result["profileSha256"] = profile.source_sha256
            result["scene"] = {
                "fileName": result["approvedScene"]["fileName"],
                "sha256": result["approvedScene"]["sha256"],
            }
            result.update(
                {
                    "blenderVersion": profile.blender_version,
                    "frameStart": profile.frame_start,
                    "frameEnd": profile.frame_end,
                    "frameCount": profile.frame_count,
                    "fps": profile.fps,
                    "width": profile.width,
                    "height": profile.height,
                }
            )
        elif args.command == "validate-profile":
            if args.output and not args.scene:
                raise ToolingError("missing-input", "--output requires --scene for exact compatibility validation.")
            profile = load_render_profile(Path(args.profile))
            result = profile_validation_summary(profile, Path(args.scene) if args.scene else None)
            if args.output:
                result["outputCompatibility"] = output_compatibility(profile, Path(args.scene), Path(args.output))
                result["ok"] = bool(result["outputCompatibility"]["compatible"])
                result["validationStatus"] = "valid" if result["ok"] else "incompatible-output"
                if not result["ok"]:
                    _emit(result)
                    return 7
        elif args.command == "output-compatibility":
            profile = load_render_profile(Path(args.profile))
            compatibility_result = output_compatibility(profile, Path(args.scene), Path(args.output))
            result = {"ok": compatibility_result["compatible"], **compatibility_result}
            if not result["ok"]:
                _emit(result)
                return 7
        elif args.command == "validate-token":
            profile = load_render_profile(Path(args.profile))
            validate_scene(profile, Path(args.scene))
            validate_authorization(profile, args.authorization_token)
            result = {"ok": True, "authorizationAccepted": True}
        elif args.command == "scan-frames":
            profile = load_render_profile(Path(args.profile))
            frame_scan = scan_frames(Path(args.frames), profile, workers=args.workers)
            structurally_valid = not frame_scan.invalid and not frame_scan.duplicates and not frame_scan.unexpected
            result = {"ok": frame_scan.complete if args.require_complete else structurally_valid, "frameScan": frame_scan.summary()}
            if args.json_output:
                output = _absolute_path(args.json_output, "JSON output")
                _atomic_write_json(output, result)
            if args.require_complete and not frame_scan.complete:
                _emit(result)
                return 4
        elif args.command == "render-plan":
            profile = load_render_profile(Path(args.profile))
            result = render_plan(
                profile,
                Path(args.scene),
                Path(args.output),
                initialize=args.initialize,
                authorization_token=args.authorization_token,
                require_authorization=args.require_authorization,
                chunk_size_override=args.chunk_size,
                chunk_rationale_override=args.chunk_rationale,
                workers=args.workers,
            )
            if not result["ok"] and not args.report_blocked:
                _emit(result)
                return 5
        elif args.command == "quarantine-invalid-frames":
            profile = load_render_profile(Path(args.profile))
            result = quarantine_invalid_frames(
                profile,
                Path(args.scene),
                Path(args.output),
                args.authorization_token,
                workers=args.workers,
            )
        elif args.command == "commit-chunk":
            profile = load_render_profile(Path(args.profile))
            result = commit_chunk(
                profile,
                Path(args.scene),
                Path(args.output),
                Path(args.temporary_frames),
                start=args.start,
                end=args.end,
                stdout_log=args.stdout_log,
                stderr_log=args.stderr_log,
                workers=args.workers,
            )
        elif args.command == "record-chunk-failure":
            profile = load_render_profile(Path(args.profile))
            result = record_chunk_failure(
                profile,
                Path(args.scene),
                Path(args.output),
                start=args.start,
                end=args.end,
                exit_code=args.exit_code,
                stdout_log=args.stdout_log,
                stderr_log=args.stderr_log,
            )
        elif args.command == "record-operator-stop":
            profile = load_render_profile(Path(args.profile))
            result = record_operator_stop(
                profile,
                Path(args.scene),
                Path(args.output),
                completed_start=args.completed_start,
                completed_end=args.completed_end,
            )
        elif args.command == "encode-preflight":
            profile = load_render_profile(Path(args.profile))
            result = encode_preflight(
                profile,
                Path(args.scene),
                Path(args.output),
                Path(args.audio),
                Path(args.ffprobe),
                Path(args.destination),
                kind=args.kind,
                workers=args.workers,
            )
        elif args.command == "encode-arguments":
            profile = load_render_profile(Path(args.profile))
            result = {
                "ok": True,
                "arguments": build_encode_arguments(
                    profile,
                    Path(args.frames),
                    Path(args.audio),
                    Path(args.temporary_output),
                    kind=args.kind,
                ),
            }
        elif args.command == "finalize-encode":
            profile = load_render_profile(Path(args.profile))
            result = finalize_encode(
                profile,
                Path(args.scene),
                Path(args.output),
                Path(args.temporary_media),
                Path(args.destination),
                Path(args.audio),
                Path(args.ffprobe),
                kind=args.kind,
                workers=args.workers,
            )
        elif args.command == "verify-final":
            profile = load_render_profile(Path(args.profile))
            result = verify_final(
                profile,
                Path(args.scene),
                Path(args.output),
                Path(args.media),
                Path(args.audio),
                Path(args.ffprobe),
                Path(args.encode_manifest),
                kind=args.kind,
            )
            if args.json_output:
                output = _absolute_path(args.json_output, "JSON output")
                _atomic_write_json(output, result)
            if not result["ok"]:
                _emit(result)
                return 6
        else:
            raise ToolingError("unknown-command", "Unknown command.")
    except ToolingError as exc:
        _emit({"ok": False, "error": {"code": exc.code, "message": str(exc)}})
        return 2
    except OSError as exc:
        _emit({"ok": False, "error": {"code": "filesystem-error", "message": str(exc)[:500]}})
        return 3
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

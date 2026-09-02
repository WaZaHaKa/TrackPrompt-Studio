from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import shutil
import stat
import struct
import subprocess
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from .config import MissionControlConfig
from .discovery import atomic_write_json, load_json_object
from .errors import MissionControlError
from .models import (
    FakeRenderOptions,
    JobPhase,
    JobRecord,
    JobState,
    SafeStopStatus,
    StructuredError,
)
from .processes import process_is_alive

_FRAME_PATTERNS = (
    re.compile(r"\bFra:(\d+)\b"),
    re.compile(r"\bframe[_ ](\d{6})\b", re.IGNORECASE),
    re.compile(r"\bframe\s+(\d+)\b", re.IGNORECASE),
)

_RENDER_MANIFEST_KIND = "trackprompt-final-render-manifest"
_RENDER_MANIFEST_SCHEMA_VERSION = "1.0.0"
_HASH_PATTERN = re.compile(r"^[0-9A-Fa-f]{64}$")
_SAFE_OUTPUT_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CHUNK_STARTED_PATTERN = re.compile(
    r"^Rendering chunk (\d+)/(\d+): frames (\d+)-(\d+) \((\d+) frames\)$"
)
_CHUNK_PUBLISHED_PATTERN = re.compile(
    r"^Published frames (\d+)-(\d+); (\d+) valid of (\d+)\.$"
)
_ARTIFACT_PREVIEW_CANDIDATES = 16
RENDER_EVENT_PREFIX = "WZHK_RENDER_EVENT "
_RENDER_EVENT_TYPES = {
    "frame_started",
    "frame_written",
    "render_stats",
    "chunk_complete",
    "render_cancelled",
}
_RENDER_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RENDER_EVENT_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")
_RENDER_STATUSES = {"rendering", "awaiting_chunk_validation", "chunk_rendered", "cancelled"}

_FAKE_PREVIEW_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _fake_preview_png(width: int, height: int, frame: int) -> bytes:
    """Create a visible deterministic RGB fixture without an image dependency."""
    if width < 1 or height < 1:
        raise ValueError("fake preview dimensions must be positive")
    phase = frame % 256
    left_width = width // 3
    middle_width = width // 3
    right_width = width - left_width - middle_width
    scanlines = bytearray()
    denominator = max(1, height - 1)
    for row in range(height):
        vertical = row * 255 // denominator
        scanlines.append(0)
        scanlines.extend(bytes((12, (48 + vertical // 3) % 256, (96 + phase) % 256)) * left_width)
        scanlines.extend(
            bytes(((42 + phase // 4) % 256, (18 + vertical // 2) % 256, 132))
            * middle_width
        )
        scanlines.extend(
            bytes(((8 + vertical // 5) % 256, (112 + phase // 2) % 256, (176 + vertical // 4) % 256))
            * right_width
        )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9))
        + _png_chunk(b"IEND", b"")
    )


def _publish_fake_preview(
    output_directory: str,
    frame: int,
    *,
    width: int = 1280,
    height: int = 720,
) -> Path:
    """Atomically publish one deterministic preview only at a fake chunk boundary."""
    frames_directory = Path(output_directory) / "frames"
    frames_directory.mkdir(parents=True, exist_ok=True)
    destination = frames_directory / f"frame_{frame:06d}.png"
    temporary = (
        frames_directory
        / f".{destination.name}.{os.getpid()}.{os.urandom(6).hex()}.tmp"
    )
    try:
        with temporary.open("xb") as stream:
            stream.write(_fake_preview_png(width, height, frame))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


@dataclass(frozen=True, slots=True)
class CommandResult:
    ok: bool
    exit_code: int
    payload: dict[str, Any] | None
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RendererTelemetryEvent:
    schema_version: str
    event_type: str
    sequence: int
    job_id: str
    worker_id: str
    chunk_id: str
    chunk_start: int
    chunk_end: int
    frame: int | None
    elapsed_seconds: float | None
    renderer_status: str
    act_id: str | None
    act_name: str | None
    shot_id: str | None
    shot_name: str | None
    complexity_class: str | None
    project_id: str | None
    scene_sha256: str | None
    profile_sha256: str | None
    output_variant_id: str | None
    width: int | None
    height: int | None
    composition_profile_id: str | None
    artifact_relative_path: str | None
    emitted_at: datetime | None


@dataclass(frozen=True, slots=True)
class RendererVariantExpectation:
    output_variant_id: str
    render_profile_sha256: str
    width: int
    height: int
    composition_profile_id: str


def parse_renderer_telemetry_line(
    line: str,
    *,
    expected_job_id: str,
    expected_project_id: str | None = None,
    expected_scene_sha256: str | None = None,
    expected_profile_sha256: str | None = None,
    expected_output_variant_id: str | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_composition_profile_id: str | None = None,
    expected_output_variants: Mapping[str, RendererVariantExpectation] | None = None,
) -> RendererTelemetryEvent | None:
    """Parse only the exact, bounded renderer prefix; malformed payloads are inert."""
    if not line.startswith(RENDER_EVENT_PREFIX) or len(line) > 8_192:
        return None
    try:
        raw = json.loads(line[len(RENDER_EVENT_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or raw.get("schemaVersion") not in {"1.0.0", "2.0.0"}:
        return None
    schema_version = str(raw["schemaVersion"])

    def identifier(key: str, *, required: bool) -> str | None:
        value = raw.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str) or _RENDER_EVENT_ID.fullmatch(value) is None:
            raise ValueError(key)
        return value

    def name(key: str) -> str | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or _RENDER_EVENT_NAME.fullmatch(value) is None:
            raise ValueError(key)
        return value

    def canonical_hash(key: str) -> str | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
            raise ValueError(key)
        return value.upper()

    def positive_dimension(key: str) -> int | None:
        value = raw.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 16 <= value <= 16_384:
            raise ValueError(key)
        return int(value)

    def relative_artifact() -> str | None:
        value = raw.get("artifactRelativePath")
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or len(value) > 512
            or "\\" in value
            or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or Path(value).suffix.casefold() not in {".png", ".exr"}
        ):
            raise ValueError("artifactRelativePath")
        return value

    def emitted_timestamp() -> datetime | None:
        value = raw.get("emittedAt")
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("emittedAt")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("emittedAt")
        return parsed.astimezone(UTC)

    try:
        event_type = raw.get("eventType")
        renderer_status = raw.get("rendererStatus")
        sequence = raw.get("sequence")
        chunk_start = raw.get("chunkStart")
        chunk_end = raw.get("chunkEnd")
        frame = raw.get("frame")
        elapsed = raw.get("elapsedSeconds")
        job_id = identifier("jobId", required=True)
        worker_id = identifier("workerId", required=True)
        chunk_id = identifier("chunkId", required=True)
        project_id = identifier("projectId", required=False)
        output_variant_id = identifier("outputVariantId", required=False)
        composition_profile_id = identifier("compositionProfileId", required=False)
        scene_sha256 = canonical_hash("sceneSha256")
        profile_sha256 = canonical_hash("profileSha256")
        width = positive_dimension("width")
        height = positive_dimension("height")
        artifact_relative_path = relative_artifact()
        emitted_at = emitted_timestamp()
        if (
            event_type not in _RENDER_EVENT_TYPES
            or renderer_status not in _RENDER_STATUSES
            or job_id != expected_job_id
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or isinstance(chunk_start, bool)
            or not isinstance(chunk_start, int)
            or isinstance(chunk_end, bool)
            or not isinstance(chunk_end, int)
            or chunk_start < 1
            or chunk_end < chunk_start
            or chunk_end - chunk_start > 10_000
        ):
            return None
        if schema_version == "2.0.0" and (
            project_id is None
            or scene_sha256 is None
            or profile_sha256 is None
            or output_variant_id is None
            or width is None
            or height is None
            or composition_profile_id is None
            or emitted_at is None
            or (event_type == "frame_written" and artifact_relative_path is None)
        ):
            return None
        if any(
            actual != expected
            for actual, expected in (
                (project_id, expected_project_id),
                (scene_sha256, expected_scene_sha256),
            )
            if expected is not None
        ):
            return None
        if expected_output_variants is not None:
            expected_variant = (
                expected_output_variants.get(output_variant_id)
                if output_variant_id is not None
                else None
            )
            if expected_variant is None or (
                expected_variant.output_variant_id != output_variant_id
                or profile_sha256 != expected_variant.render_profile_sha256.upper()
                or width != expected_variant.width
                or height != expected_variant.height
                or composition_profile_id
                != expected_variant.composition_profile_id
            ):
                return None
        elif any(
            actual != expected
            for actual, expected in (
                (profile_sha256, expected_profile_sha256),
                (output_variant_id, expected_output_variant_id),
                (width, expected_width),
                (height, expected_height),
                (composition_profile_id, expected_composition_profile_id),
            )
            if expected is not None
        ):
            return None
        if frame is not None and (
            isinstance(frame, bool)
            or not isinstance(frame, int)
            or not chunk_start <= frame <= chunk_end
        ):
            return None
        if elapsed is not None and (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(float(elapsed))
            or not 0.0 <= float(elapsed) <= 86_400.0
        ):
            return None
        return RendererTelemetryEvent(
            schema_version=schema_version,
            event_type=str(event_type),
            sequence=sequence,
            job_id=job_id,
            worker_id=cast(str, worker_id),
            chunk_id=cast(str, chunk_id),
            chunk_start=chunk_start,
            chunk_end=chunk_end,
            frame=frame,
            elapsed_seconds=None if elapsed is None else float(elapsed),
            renderer_status=str(renderer_status),
            act_id=identifier("actId", required=False),
            act_name=name("actName"),
            shot_id=identifier("shotId", required=False),
            shot_name=name("shotName"),
            complexity_class=identifier("complexityClass", required=False),
            project_id=project_id,
            scene_sha256=scene_sha256,
            profile_sha256=profile_sha256,
            output_variant_id=output_variant_id,
            width=width,
            height=height,
            composition_profile_id=composition_profile_id,
            artifact_relative_path=artifact_relative_path,
            emitted_at=emitted_at,
        )
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class RenderArtifactSnapshot:
    """Cheap, manifest-backed render state without a recursive frame scan."""

    disposition: Literal["complete", "paused", "resumable", "missing", "invalid"]
    valid_frame_count: int = 0
    chunks_completed: int = 0
    latest_indexed_frame: int | None = None
    latest_preview_frame: int | None = None
    latest_preview_path: Path | None = None
    reason: str | None = None


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(details, "st_file_attributes", 0))
    return path.is_symlink() or bool(
        attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    )


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(
            str(right.resolve())
        )
    except OSError:
        return False


def _manifest_preview(
    output: Path,
    frames_subdirectory: str,
    filename_pattern: str,
    indexed_frames: list[tuple[int, str, int]],
) -> tuple[int | None, Path | None]:
    if filename_pattern.casefold() != "frame_%06d.png":
        return None, None
    frames_root = output / frames_subdirectory
    if not frames_root.is_dir() or _is_reparse_point(frames_root):
        return None, None
    try:
        if frames_root.resolve().parent != output.resolve():
            return None, None
    except OSError:
        return None, None
    for frame, expected_hash, expected_size in reversed(
        indexed_frames[-_ARTIFACT_PREVIEW_CANDIDATES:]
    ):
        candidate = frames_root / (filename_pattern % frame)
        try:
            details = candidate.lstat()
            if (
                not stat.S_ISREG(details.st_mode)
                or _is_reparse_point(candidate)
                or details.st_size != expected_size
            ):
                continue
            digest = hashlib.sha256()
            with candidate.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest().upper() == expected_hash:
                return frame, candidate
        except OSError:
            continue
    return None, None


def inspect_render_artifacts(job: JobRecord) -> RenderArtifactSnapshot:
    """Reconcile a production job from the atomic manifest and frame index.

    This intentionally does not claim encode-grade filesystem completeness. The
    validated renderer performs that full scan before writing a complete manifest.
    """

    output = Path(job.identity.output_directory)
    manifests = output / "manifests"
    manifest_path = manifests / "render-manifest.json"
    if any(_is_reparse_point(path) for path in (output, manifests, manifest_path)):
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason="The render manifest path crosses a linked or reparse-point entry.",
        )
    if not manifest_path.exists():
        return RenderArtifactSnapshot(
            disposition="missing",
            reason="The render manifest has not been published.",
        )
    if (
        not output.is_dir()
        or not manifests.is_dir()
        or not manifest_path.is_file()
    ):
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason="The render manifest path is not a direct regular file.",
        )
    try:
        manifest = load_json_object(manifest_path, "Render manifest")
    except MissionControlError as exc:
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason=exc.error.summary,
        )
    if (
        manifest.get("kind") != _RENDER_MANIFEST_KIND
        or manifest.get("schemaVersion") != _RENDER_MANIFEST_SCHEMA_VERSION
    ):
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason="The render manifest kind or schema is unsupported.",
        )
    scene = manifest.get("scene")
    profile = manifest.get("renderProfile")
    output_directory = manifest.get("outputDirectory")
    if (
        not isinstance(scene, dict)
        or str(scene.get("sha256", "")).upper() != job.identity.scene_sha256
        or not isinstance(profile, dict)
        or str(profile.get("sha256", "")).upper() != job.identity.profile_sha256
        or not isinstance(output_directory, str)
        or not _same_path(Path(output_directory), output)
    ):
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason="The render manifest does not match this job's exact identity.",
        )
    authorization = manifest.get("authorization")
    expected_token_hash = (
        authorization.get("expectedTokenSha256")
        if isinstance(authorization, dict)
        else None
    )
    accepted_token_hash = (
        authorization.get("acceptedTokenSha256")
        if isinstance(authorization, dict)
        else None
    )
    if (
        not isinstance(authorization, dict)
        or authorization.get("status") != "operator-token-accepted"
        or not isinstance(expected_token_hash, str)
        or _HASH_PATTERN.fullmatch(expected_token_hash) is None
        or not isinstance(accepted_token_hash, str)
        or accepted_token_hash.upper() != expected_token_hash.upper()
    ):
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason="The manifest does not record exact production authorization.",
        )
    contract = manifest.get("frameContract")
    if not isinstance(contract, dict) or any(
        contract.get(field) != expected
        for field, expected in (
            ("frameStart", job.frame_start),
            ("frameEnd", job.frame_end),
            ("frameCount", job.total_frame_count),
        )
    ):
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason="The manifest frame contract does not match this job.",
        )
    frames_subdirectory = contract.get("framesSubdirectory")
    filename_pattern = contract.get("filenamePattern")
    if (
        not isinstance(frames_subdirectory, str)
        or _SAFE_OUTPUT_COMPONENT.fullmatch(frames_subdirectory) is None
        or frames_subdirectory in {".", ".."}
        or not isinstance(filename_pattern, str)
        or filename_pattern.casefold() not in {"frame_%06d.png", "frame_%06d.exr"}
    ):
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason="The manifest frame output path contract is unsafe or unsupported.",
        )
    frame_index = manifest.get("frameIndex")
    if not isinstance(frame_index, dict):
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason="The render manifest has no validated frame index.",
        )
    digest = hashlib.sha256()
    indexed_frames: list[tuple[int, str, int]] = []
    for key, value in sorted(frame_index.items()):
        if (
            not isinstance(key, str)
            or re.fullmatch(r"\d{6}", key) is None
            or not isinstance(value, dict)
        ):
            return RenderArtifactSnapshot(
                disposition="invalid",
                reason="The render manifest frame index is malformed.",
            )
        frame = int(key)
        frame_hash = value.get("sha256")
        size_bytes = value.get("sizeBytes")
        if (
            frame < job.frame_start
            or frame > job.frame_end
            or not isinstance(frame_hash, str)
            or _HASH_PATTERN.fullmatch(frame_hash) is None
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 1
        ):
            return RenderArtifactSnapshot(
                disposition="invalid",
                reason="The render manifest frame index is outside the saved contract.",
            )
        canonical_hash = frame_hash.upper()
        digest.update(f"{key} {canonical_hash}\n".encode())
        indexed_frames.append((frame, canonical_hash, size_bytes))
    frame_set = manifest.get("frameSet")
    valid_count = len(indexed_frames)
    missing_count = job.total_frame_count - valid_count
    clean_lists = (
        "invalidFrames",
        "duplicateFrames",
        "unexpectedFrameFiles",
    )
    if (
        not isinstance(frame_set, dict)
        or frame_set.get("expectedFrameCount") != job.total_frame_count
        or frame_set.get("validFrameCount") != valid_count
        or frame_set.get("missingFrameCount") != missing_count
        or str(frame_set.get("frameSetSha256", "")).upper()
        != digest.hexdigest().upper()
        or any(frame_set.get(field) != [] for field in clean_lists)
        or not isinstance(frame_set.get("missingRanges"), list)
        or not isinstance(frame_set.get("validRanges"), list)
    ):
        return RenderArtifactSnapshot(
            disposition="invalid",
            reason="The manifest frame summary does not agree with its validated index.",
        )
    chunks = manifest.get("chunks")
    chunks_completed = sum(
        1
        for item in chunks
        if isinstance(item, dict)
        and isinstance(item.get("completedAt"), str)
        and isinstance(item.get("checkpoint"), str)
        and item.get("status") != "failed"
    ) if isinstance(chunks, list) else 0
    latest_indexed = indexed_frames[-1][0] if indexed_frames else None
    preview_frame, preview_path = _manifest_preview(
        output,
        frames_subdirectory,
        filename_pattern,
        indexed_frames,
    )
    complete = (
        manifest.get("status") == "complete"
        and frame_set.get("complete") is True
        and valid_count == job.total_frame_count
        and missing_count == 0
        and frame_set.get("missingRanges") == []
    )
    if complete:
        return RenderArtifactSnapshot(
            disposition="complete",
            valid_frame_count=valid_count,
            chunks_completed=chunks_completed,
            latest_indexed_frame=latest_indexed,
            latest_preview_frame=preview_frame,
            latest_preview_path=preview_path,
        )
    if manifest.get("status") != "incomplete" or frame_set.get("complete") is not False:
        return RenderArtifactSnapshot(
            disposition="invalid",
            valid_frame_count=valid_count,
            chunks_completed=chunks_completed,
            latest_indexed_frame=latest_indexed,
            latest_preview_frame=preview_frame,
            latest_preview_path=preview_path,
            reason="The manifest completion flags are internally inconsistent.",
        )
    run_state = manifest.get("runState")
    current_stop = (
        isinstance(run_state, dict)
        and run_state.get("status") == "stopped-after-current-chunk-by-operator"
        and isinstance(run_state.get("stoppedAt"), str)
        and run_state.get("stoppedAt") == manifest.get("updatedAt")
    )
    return RenderArtifactSnapshot(
        disposition="paused" if current_stop else "resumable",
        valid_frame_count=valid_count,
        chunks_completed=chunks_completed,
        latest_indexed_frame=latest_indexed,
        latest_preview_frame=preview_frame,
        latest_preview_path=preview_path,
    )


def artifact_progress_changes(
    job: JobRecord,
    snapshot: RenderArtifactSnapshot,
) -> dict[str, object]:
    if snapshot.disposition in {"missing", "invalid"}:
        return {}
    changes: dict[str, object] = {
        "validated_frame_count": snapshot.valid_frame_count,
        "published_frame_count": snapshot.valid_frame_count,
        "rendered_frame_count": max(job.rendered_frame_count, snapshot.valid_frame_count),
        "chunks_completed": snapshot.chunks_completed,
        "inflight_frame_count": 0,
    }
    if snapshot.latest_indexed_frame is not None:
        changes["current_frame"] = snapshot.latest_indexed_frame
    if snapshot.latest_preview_frame is not None:
        changes.update(
            latest_preview_frame=snapshot.latest_preview_frame,
            latest_frame_preview=(
                f"/api/mission-control/render/{job.id}/preview?v={snapshot.latest_preview_frame}"
            ),
        )
        if snapshot.latest_preview_frame != job.latest_preview_frame:
            changes["latest_preview_at"] = datetime.now(UTC)
    return changes


def telemetry_progress_changes(
    job: JobRecord,
    event: RendererTelemetryEvent,
    *,
    output_at: datetime,
) -> dict[str, object]:
    frame = event.frame
    target_variant = next(
        (
            variant
            for variant in job.output_variants
            if variant.enabled and variant.id == event.output_variant_id
        ),
        None,
    )
    if job.output_variants and (
        target_variant is None
        or event.width != target_variant.width
        or event.height != target_variant.height
        or event.composition_profile_id
        != target_variant.composition_profile.id
        or event.profile_sha256
        != target_variant.render_profile_sha256.upper()
    ):
        return {}
    changes: dict[str, object] = {
        "renderer_event_type": event.event_type,
        "renderer_event_sequence": event.sequence,
        "renderer_status": event.renderer_status,
        "worker_id": event.worker_id,
        "active_chunk_id": event.chunk_id,
        "chunk_start": event.chunk_start,
        "chunk_end": event.chunk_end,
        "last_output_at": output_at,
        "renderer_active": True,
        "watcher_active": True,
    }
    if event.output_variant_id is not None:
        changes["output_variant_id"] = event.output_variant_id
    for key, value in (
        ("current_act_id", event.act_id),
        ("current_act_name", event.act_name),
        ("current_shot_id", event.shot_id),
        ("current_shot_name", event.shot_name),
        ("current_complexity_class", event.complexity_class),
    ):
        if value is not None:
            changes[key] = value
    if event.elapsed_seconds is not None:
        changes["current_seconds_per_frame"] = event.elapsed_seconds
    if event.event_type == "frame_started" and frame is not None:
        changes.update(
            current_frame=frame,
            phase=JobPhase.RENDER_FRAME,
            current_frame_started_at=output_at,
            latest_log_line=f"Rendering frame {frame}",
        )
    elif event.event_type == "frame_written" and frame is not None:
        previous_rendered = (
            target_variant.progress.rendered_frames
            if target_variant is not None
            else job.rendered_frame_count
        )
        previous_safe = (
            target_variant.progress.validated_frames
            if target_variant is not None
            else job.published_frame_count
        )
        previous_latest = (
            target_variant.progress.latest_rendered_frame
            if target_variant is not None
            else job.latest_rendered_frame
        )
        if previous_latest is not None and frame < previous_latest:
            return {}
        rendered = max(previous_rendered, frame - job.frame_start + 1)
        changes.update(
            current_frame=frame,
            latest_rendered_frame=max(previous_latest or 0, frame),
            rendered_frame_count=rendered,
            inflight_frame_count=max(0, rendered - previous_safe),
            current_chunk_progress=(frame - event.chunk_start + 1)
            / (event.chunk_end - event.chunk_start + 1),
            phase=JobPhase.RENDER_FRAME,
            current_frame_started_at=None,
            latest_log_line=f"Rendered frame {frame}; awaiting chunk validation",
        )
        if event.artifact_relative_path is not None:
            changes.update(
                latest_frame_artifact=event.artifact_relative_path,
                latest_preview_frame=frame,
                latest_preview_at=output_at,
                latest_frame_preview=(
                    f"/api/mission-control/render/{job.id}/preview?v={frame}"
                    + (
                        f"&output_variant_id={event.output_variant_id}"
                        if event.output_variant_id is not None
                        else ""
                    )
                ),
                latest_full_frame_url=(
                    f"/api/mission-control/render/{job.id}/frame?v={frame}"
                    + (
                        f"&output_variant_id={event.output_variant_id}"
                        if event.output_variant_id is not None
                        else ""
                    )
                ),
            )
    elif event.event_type == "render_stats":
        changes.update(
            phase=JobPhase.RENDER_FRAME,
            latest_log_line=(
                f"Rendering frame {frame}" if frame is not None else "Renderer statistics received"
            ),
        )
    elif event.event_type == "chunk_complete":
        if frame is not None:
            previous_latest = (
                target_variant.progress.latest_rendered_frame
                if target_variant is not None
                else job.latest_rendered_frame
            )
            changes["latest_rendered_frame"] = max(previous_latest or 0, frame)
        changes.update(
            phase=JobPhase.PUBLISH_CHUNK,
            current_chunk_progress=1.0,
            current_frame_started_at=None,
            latest_log_line=(
                f"Chunk {event.chunk_id} rendered; validating before publication"
            ),
        )
    elif event.event_type == "render_cancelled":
        changes.update(
            current_frame_started_at=None,
            latest_log_line=f"Renderer cancelled in chunk {event.chunk_id}",
        )
    return changes


def telemetry_event_is_new(job: JobRecord, event: RendererTelemetryEvent) -> bool:
    if (
        job.active_variant_id != event.output_variant_id
        or job.worker_id != event.worker_id
        or job.active_chunk_id != event.chunk_id
    ):
        return True
    return event.sequence > (job.renderer_event_sequence or 0)


class RendererController(Protocol):
    def get_job(self, job_id: str) -> JobRecord: ...

    async def update_job(self, job_id: str, **changes: object) -> JobRecord: ...

    async def add_log(
        self,
        job_id: str,
        level: Literal["debug", "info", "warning", "error"],
        message: str,
    ) -> None: ...

    async def fail_job(self, job_id: str, error: StructuredError) -> JobRecord: ...


def _parse_json_output(lines: list[str]) -> dict[str, Any] | None:
    joined = "\n".join(lines).strip()
    if not joined:
        return None
    starts = [index for index, character in enumerate(joined) if character == "{"]
    for index in reversed(starts):
        try:
            value = json.loads(joined[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
    return None


class ProductionRenderer:
    def __init__(self, config: MissionControlConfig) -> None:
        self.config = config
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def powershell_path(self) -> str | None:
        return shutil.which("powershell.exe") or shutil.which("pwsh")

    def command(
        self,
        *,
        scene_path: Path,
        profile_path: Path,
        output_directory: Path,
        authorization_token: str | None = None,
        job_id: str | None = None,
        mode: Literal["preflight", "dry-run", "production"] = "production",
    ) -> list[str]:
        powershell = self.powershell_path()
        if powershell is None:
            raise MissionControlError(
                409,
                "powershell_unavailable",
                "PowerShell was not found",
                "The validated render engine requires Windows PowerShell or PowerShell Core.",
                "Restore PowerShell, then run preflight again.",
            )
        script = self.config.repository_root / "render-trackprompt-final.ps1"
        if not script.is_file():
            raise MissionControlError(
                409,
                "render_script_missing",
                "Render engine was not found",
                "The validated production render script is missing.",
                "Restore the repository render tooling.",
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
            str(scene_path),
            "-RenderProfilePath",
            str(profile_path),
            "-OutputDirectory",
            str(output_directory),
        ]
        if mode == "preflight":
            arguments.append("-Preflight")
        elif mode == "dry-run":
            arguments.append("-DryRun")
        else:
            if not authorization_token:
                raise MissionControlError(
                    409,
                    "authorization_required",
                    "Authorization required",
                    "Production rendering requires the exact scene-and-profile authorization token.",
                    "Authorize this render configuration, then start again.",
                )
            arguments.extend(("-AuthorizationToken", authorization_token))
            if job_id is not None:
                arguments.extend(("-MissionControlJobId", job_id))
        return arguments

    async def inspect(
        self,
        *,
        scene_path: Path,
        profile_path: Path,
        output_directory: Path,
        mode: Literal["preflight", "dry-run"],
        timeout_seconds: float = 180.0,
    ) -> CommandResult:
        arguments = self.command(
            scene_path=scene_path,
            profile_path=profile_path,
            output_directory=output_directory,
            mode=mode,
        )
        creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creation_flags,
            )
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise MissionControlError(
                504,
                "render_inspection_timeout",
                "Render inspection timed out",
                "The bounded render-engine inspection did not finish in time.",
                "Inspect Blender availability and retry preflight.",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise MissionControlError(
                409,
                "render_inspection_start_failed",
                "Render inspection could not start",
                "The validated PowerShell render inspection could not be launched.",
                "Inspect PowerShell and repository paths, then retry.",
                technical_details=type(exc).__name__,
            ) from exc
        text = stdout.decode("utf-8", errors="replace")[:2_000_000]
        lines = [line[:8_192] for line in text.splitlines()][-4_000:]
        return CommandResult(
            ok=process.returncode == 0,
            exit_code=int(process.returncode or 0),
            payload=_parse_json_output(lines),
            lines=tuple(lines),
        )

    def start(
        self,
        controller: RendererController,
        job: JobRecord,
        *,
        scene_path: Path,
        profile_path: Path,
        authorization_token: str,
    ) -> None:
        current = self.tasks.get(job.id)
        if current is not None and not current.done():
            raise MissionControlError(
                409,
                "render_already_running",
                "Render is already running",
                "This render job already owns an active renderer task.",
                "Return to the live progress screen.",
                job_id=job.id,
            )
        arguments = self.command(
            scene_path=scene_path,
            profile_path=profile_path,
            output_directory=Path(job.identity.output_directory),
            authorization_token=authorization_token,
            job_id=job.id,
            mode="production",
        )
        self.tasks[job.id] = asyncio.create_task(
            self._run_guarded(controller, job.id, arguments),
            name=f"mission-control-render-{job.id}",
        )

    async def _run_guarded(
        self,
        controller: RendererController,
        job_id: str,
        arguments: list[str],
    ) -> None:
        try:
            await self._run(controller, job_id, arguments)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                job = controller.get_job(job_id)
                if job.state in {
                    JobState.COMPLETE,
                    JobState.FAILED,
                    JobState.CANCELLED,
                    JobState.PAUSED_SAFELY,
                    JobState.RESUMABLE,
                }:
                    return
                error = StructuredError(
                    code="renderer_task_failed",
                    title="Render monitoring stopped unexpectedly",
                    summary="Mission Control lost its live renderer watcher before a terminal state was recorded.",
                    likely_cause="An unexpected local I/O or persistence error interrupted the watcher task.",
                    recommended_action="Keep Mission Control open while the renderer finishes, then inspect the reconciled manifest state.",
                    retryable=True,
                    technical_details=type(exc).__name__,
                    timestamp=datetime.now(UTC),
                    job_id=job_id,
                )
                await controller.add_log(job_id, "error", error.summary)
                if process_is_alive(job.process_id):
                    await controller.update_job(
                        job_id,
                        orphaned=True,
                        renderer_active=True,
                        watcher_active=False,
                        error=error,
                        warning=(
                            "The renderer is still active, but live monitoring stopped. "
                            "Mission Control will reconcile its atomic manifest after exit."
                        ),
                    )
                    return
                snapshot = inspect_render_artifacts(job)
                changes = artifact_progress_changes(job, snapshot)
                changes.update(
                    state=(
                        JobState.COMPLETE
                        if snapshot.disposition == "complete"
                        else JobState.PAUSED_SAFELY
                        if snapshot.disposition == "paused"
                        else JobState.RESUMABLE
                        if snapshot.disposition in {"resumable", "missing"}
                        else JobState.FAILED
                    ),
                    phase=(
                        JobPhase.FINAL_VERIFY
                        if snapshot.disposition == "complete"
                        else JobPhase.PUBLISH_CHUNK
                    ),
                    safe_stop_status=(
                        SafeStopStatus.PAUSED
                        if snapshot.disposition == "paused"
                        else SafeStopStatus.NONE
                    ),
                    process_id=None,
                    orphaned=False,
                    renderer_active=False,
                    watcher_active=False,
                    error=None if snapshot.disposition in {"complete", "paused"} else error,
                    completed_at=(
                        datetime.now(UTC)
                        if snapshot.disposition in {"complete", "invalid"}
                        else None
                    ),
                )
                await controller.update_job(job_id, **changes)
            except Exception:
                # The top-level guard must never leak an unobserved task exception.
                return

    async def _run(
        self,
        controller: RendererController,
        job_id: str,
        arguments: list[str],
    ) -> None:
        creation_flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creation_flags,
            )
        except OSError as exc:
            await controller.fail_job(
                job_id,
                StructuredError(
                    code="renderer_start_failed",
                    title="Render process could not start",
                    summary="The validated PowerShell render process could not be launched.",
                    likely_cause="PowerShell or the render script is unavailable.",
                    recommended_action="Inspect system paths and retry the render.",
                    retryable=True,
                    technical_details=type(exc).__name__,
                    timestamp=datetime.now(UTC),
                    job_id=job_id,
                ),
            )
            return
        await controller.update_job(
            job_id,
            process_id=process.pid,
            state=JobState.RUNNING,
            phase=JobPhase.SCENE_LOAD,
            started_at=datetime.now(UTC),
            renderer_active=True,
            watcher_active=True,
        )
        await controller.add_log(job_id, "info", "Validated production renderer started.")
        if process.stdout is None:
            process.kill()
            await process.wait()
            await controller.fail_job(
                job_id,
                StructuredError(
                    code="renderer_pipe_unavailable",
                    title="Render activity stream unavailable",
                    summary="The render process started without a readable activity stream.",
                    recommended_action="Inspect system logs and resume the exact render.",
                    retryable=True,
                    timestamp=datetime.now(UTC),
                    job_id=job_id,
                ),
            )
            return
        frame_started_at = datetime.now(UTC)
        telemetry_sequences: dict[tuple[str | None, str, str], int] = {}
        while True:
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=1.5)
            except TimeoutError:
                job = controller.get_job(job_id)
                elapsed = (datetime.now(UTC) - frame_started_at).total_seconds()
                await controller.update_job(
                    job_id,
                    current_seconds_per_frame=elapsed if job.current_frame is not None else None,
                    latest_log_line="Rendering is still active",
                    renderer_active=process.returncode is None,
                    watcher_active=True,
                )
                continue
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()[:8_192]
            if not line:
                continue
            if line.startswith(RENDER_EVENT_PREFIX):
                job = controller.get_job(job_id)
                variant_expectations = (
                    {
                        variant.id: RendererVariantExpectation(
                            output_variant_id=variant.id,
                            render_profile_sha256=variant.render_profile_sha256,
                            width=variant.width,
                            height=variant.height,
                            composition_profile_id=variant.composition_profile.id,
                        )
                        for variant in job.output_variants
                        if variant.enabled
                    }
                    if job.output_variants
                    else None
                )
                event = parse_renderer_telemetry_line(
                    line,
                    expected_job_id=job_id,
                    expected_project_id=job.identity.project_id,
                    expected_scene_sha256=job.identity.scene_sha256,
                    expected_profile_sha256=(
                        None
                        if variant_expectations is not None
                        else job.identity.profile_sha256
                    ),
                    expected_output_variant_id=(
                        None
                        if variant_expectations is not None
                        else job.identity.output_variant_id
                    ),
                    expected_width=(
                        None
                        if variant_expectations is not None
                        else job.identity.output_width
                    ),
                    expected_height=(
                        None
                        if variant_expectations is not None
                        else job.identity.output_height
                    ),
                    expected_composition_profile_id=(
                        None
                        if variant_expectations is not None
                        else job.identity.composition_profile_id
                    ),
                    expected_output_variants=variant_expectations,
                )
                if event is None:
                    continue
                sequence_key = (
                    event.output_variant_id,
                    event.worker_id,
                    event.chunk_id,
                )
                if event.sequence <= telemetry_sequences.get(sequence_key, 0):
                    continue
                telemetry_sequences[sequence_key] = event.sequence
                output_at = datetime.now(UTC)
                if not telemetry_event_is_new(job, event):
                    continue
                telemetry_changes = telemetry_progress_changes(job, event, output_at=output_at)
                if event.event_type == "frame_started":
                    frame_started_at = output_at
                await controller.update_job(job_id, **telemetry_changes)
                continue
            await controller.add_log(job_id, "info", line)
            output_at = datetime.now(UTC)
            frame = self._frame_from_line(line)
            changes: dict[str, object] = {
                "latest_log_line": line,
                "last_output_at": output_at,
                "renderer_active": True,
                "watcher_active": True,
            }
            if frame is not None:
                frame_started_at = output_at
                job = controller.get_job(job_id)
                changes.update(
                    current_frame=frame,
                    phase=JobPhase.RENDER_FRAME,
                    rendered_frame_count=max(job.rendered_frame_count, frame - job.frame_start + 1),
                    current_frame_started_at=frame_started_at,
                )
            chunk_started = _CHUNK_STARTED_PATTERN.fullmatch(line)
            if chunk_started is not None:
                job = controller.get_job(job_id)
                remaining_chunks = int(chunk_started.group(2))
                changes.update(
                    phase=JobPhase.RENDER_FRAME,
                    chunk_start=int(chunk_started.group(3)),
                    chunk_end=int(chunk_started.group(4)),
                    current_chunk_progress=0.0,
                    chunks_total=max(
                        job.chunks_total,
                        job.chunks_completed + remaining_chunks,
                    ),
                    current_frame_started_at=None,
                )
            chunk_published = _CHUNK_PUBLISHED_PATTERN.fullmatch(line)
            if chunk_published is not None:
                job = controller.get_job(job_id)
                snapshot = inspect_render_artifacts(job)
                if snapshot.disposition not in {"missing", "invalid"}:
                    changes.update(artifact_progress_changes(job, snapshot))
                changes.update(
                    phase=JobPhase.PUBLISH_CHUNK,
                    chunk_start=int(chunk_published.group(1)),
                    chunk_end=int(chunk_published.group(2)),
                    current_chunk_progress=1.0,
                    current_frame_started_at=None,
                )
            await controller.update_job(job_id, **changes)
        exit_code = await process.wait()
        await self._reconcile_exit(controller, job_id, exit_code)

    async def _reconcile_exit(
        self,
        controller: RendererController,
        job_id: str,
        exit_code: int,
    ) -> None:
        job = controller.get_job(job_id)
        snapshot = inspect_render_artifacts(job)
        changes = artifact_progress_changes(job, snapshot)
        changes.update(
            process_id=None,
            orphaned=False,
            renderer_active=False,
            watcher_active=False,
            current_frame_started_at=None,
        )
        if exit_code == 0 and snapshot.disposition == "complete":
            changes.update(
                state=JobState.COMPLETE,
                phase=JobPhase.FINAL_VERIFY,
                completed_at=datetime.now(UTC),
                safe_stop_status=SafeStopStatus.NONE,
                error=None,
                warning=None,
            )
            await controller.update_job(job_id, **changes)
            await controller.add_log(
                job_id,
                "info",
                "Validated render manifest confirms a complete frame sequence.",
            )
            return
        if exit_code == 0 and snapshot.disposition == "paused":
            changes.update(
                state=JobState.PAUSED_SAFELY,
                phase=JobPhase.PUBLISH_CHUNK,
                safe_stop_status=SafeStopStatus.PAUSED,
                error=None,
                warning=None,
            )
            await controller.update_job(job_id, **changes)
            await controller.add_log(
                job_id,
                "info",
                "Renderer stopped safely after the manifest recorded the published chunk.",
            )
            return
        if exit_code == 0:
            code = (
                "render_incomplete_after_exit"
                if snapshot.disposition == "resumable"
                else "render_manifest_missing_after_exit"
                if snapshot.disposition == "missing"
                else "render_manifest_invalid_after_exit"
            )
            summary = (
                "The renderer exited successfully, but its authoritative manifest does not prove completion or a current safe stop."
            )
            error = StructuredError(
                code=code,
                title="Render completion could not be verified",
                summary=summary,
                likely_cause=snapshot.reason or "The renderer ended outside the validated terminal protocol.",
                recommended_action="Inspect the output manifest and logs, then resume the exact render if its identity remains valid.",
                retryable=snapshot.disposition in {"resumable", "missing"},
                context={"exitCode": exit_code, "artifactState": snapshot.disposition},
                timestamp=datetime.now(UTC),
                job_id=job_id,
            )
        else:
            error = StructuredError(
                code="renderer_process_failed",
                title="Render process stopped",
                summary="The render ended before the authoritative manifest confirmed completion.",
                likely_cause=f"The renderer exited with code {exit_code}.",
                recommended_action="Inspect logs, resolve the cause, then resume the exact render.",
                retryable=snapshot.disposition != "invalid",
                context={"exitCode": exit_code, "artifactState": snapshot.disposition},
                timestamp=datetime.now(UTC),
                job_id=job_id,
            )
        changes.update(
            state=JobState.FAILED,
            completed_at=datetime.now(UTC),
            error=error,
            warning=None,
        )
        await controller.add_log(job_id, "error", error.summary)
        await controller.update_job(job_id, **changes)

    def _frame_from_line(self, line: str) -> int | None:
        for pattern in _FRAME_PATTERNS:
            match = pattern.search(line)
            if match:
                return int(match.group(1))
        return None

    def request_stop(self, job: JobRecord, *, profile_path: Path, scene_path: Path) -> Path:
        output = Path(job.identity.output_directory).resolve()
        manifest_path = output / "manifests" / "render-manifest.json"
        if not manifest_path.is_file():
            raise MissionControlError(
                409,
                "render_not_initialized",
                "Safe stop is not available yet",
                "The renderer has not published its identity manifest yet.",
                "Wait for render initialization, then request a safe stop again.",
                retryable=True,
                job_id=job.id,
            )
        manifest = load_json_object(manifest_path, "Render manifest")
        profile = cast(Mapping[str, Any], manifest.get("renderProfile", {}))
        scene = cast(Mapping[str, Any], manifest.get("scene", {}))
        if str(profile.get("sha256", "")).upper() != job.identity.profile_sha256 or str(
            scene.get("sha256", "")
        ).upper() != job.identity.scene_sha256:
            raise MissionControlError(
                409,
                "stop_identity_mismatch",
                "Safe stop identity does not match",
                "The selected output no longer matches this job's exact scene and profile.",
                "Inspect the output manifest before changing render controls.",
                job_id=job.id,
            )
        if not profile_path.is_file() or not scene_path.is_file():
            raise MissionControlError(
                409,
                "stop_identity_file_missing",
                "Safe stop identity is unavailable",
                "The exact scene or profile file is no longer available.",
                "Restore the identity files before changing render controls.",
                job_id=job.id,
            )
        request_path = output / "control" / "stop-after-current-chunk.request.json"
        atomic_write_json(
            request_path,
            {
                "schemaVersion": "1.0.0",
                "kind": "trackprompt-stop-after-current-chunk-request",
                "status": "requested",
                "requestedAt": datetime.now(UTC).isoformat(),
                "outputDirectory": str(output),
                "profileSha256": job.identity.profile_sha256,
                "sceneSha256": job.identity.scene_sha256,
                "behavior": "validate and publish the current chunk, then exit before starting the next chunk",
            },
        )
        return request_path

    def cancel_stop(self, job: JobRecord) -> None:
        request_path = (
            Path(job.identity.output_directory)
            / "control"
            / "stop-after-current-chunk.request.json"
        )
        if request_path.is_file():
            try:
                payload = load_json_object(request_path, "Safe-stop request")
                if (
                    payload.get("kind") != "trackprompt-stop-after-current-chunk-request"
                    or str(payload.get("profileSha256", "")).upper()
                    != job.identity.profile_sha256
                    or str(payload.get("sceneSha256", "")).upper() != job.identity.scene_sha256
                ):
                    raise MissionControlError(
                        409,
                        "stop_request_identity_mismatch",
                        "Stop request cannot be cancelled",
                        "The saved stop request belongs to another exact render identity.",
                        "Inspect the output control folder before changing it.",
                        job_id=job.id,
                    )
                request_path.unlink()
            except OSError as exc:
                raise MissionControlError(
                    409,
                    "stop_request_cancel_failed",
                    "Stop request could not be cancelled",
                    "The safe-stop marker could not be removed.",
                    "Check folder permissions and retry.",
                    retryable=True,
                    technical_details=type(exc).__name__,
                    job_id=job.id,
                ) from exc


class FakeRenderer:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def start(
        self,
        controller: RendererController,
        job: JobRecord,
        options: FakeRenderOptions,
    ) -> None:
        current = self.tasks.get(job.id)
        if current is not None and not current.done():
            raise MissionControlError(
                409,
                "render_already_running",
                "Render is already running",
                "This fake render job already has an active deterministic event source.",
                "Return to the live progress screen.",
                job_id=job.id,
            )
        self.tasks[job.id] = asyncio.create_task(
            self._run_guarded(controller, job.id, options),
            name=f"mission-control-fake-render-{job.id}",
        )

    async def _run_guarded(
        self,
        controller: RendererController,
        job_id: str,
        options: FakeRenderOptions,
    ) -> None:
        try:
            await self._run(controller, job_id, options)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                job = controller.get_job(job_id)
                if job.state in {
                    JobState.COMPLETE,
                    JobState.FAILED,
                    JobState.CANCELLED,
                    JobState.PAUSED_SAFELY,
                    JobState.RESUMABLE,
                }:
                    return
                await controller.fail_job(
                    job_id,
                    StructuredError(
                        code="fake_renderer_task_failed",
                        title="Fake renderer stopped unexpectedly",
                        summary="The deterministic fake renderer task encountered an unexpected local error.",
                        likely_cause="A test output or persistence operation failed.",
                        recommended_action="Inspect the saved error and retry the fake render.",
                        retryable=True,
                        technical_details=type(exc).__name__,
                        timestamp=datetime.now(UTC),
                        job_id=job_id,
                    ),
                )
            except Exception:
                return

    async def _run(
        self,
        controller: RendererController,
        job_id: str,
        options: FakeRenderOptions,
    ) -> None:
        job = controller.get_job(job_id)
        total = options.total_frames or job.total_frame_count
        chunk_size = options.frames_per_chunk or max(1, (job.frame_end - job.frame_start + 1) // job.chunks_total)
        start_index = job.published_frame_count
        await controller.update_job(
            job_id,
            state=JobState.RUNNING,
            phase=JobPhase.SCENE_LOAD,
            started_at=job.started_at or datetime.now(UTC),
            total_frame_count=total,
            frame_end=job.frame_start + total - 1,
            chunks_total=(total + chunk_size - 1) // chunk_size,
            error=None,
            warning=None,
            safe_stop_status=SafeStopStatus.NONE,
            renderer_active=True,
            watcher_active=True,
        )
        await controller.add_log(job_id, "info", "Deterministic fake renderer started.")
        timings: list[float] = []
        for index in range(start_index, total):
            frame = job.frame_start + index
            chunk_index = index // chunk_size
            chunk_start = job.frame_start + chunk_index * chunk_size
            chunk_end = min(job.frame_start + total - 1, chunk_start + chunk_size - 1)
            if options.long_frame_at == frame:
                for heartbeat in range(3):
                    await controller.update_job(
                        job_id,
                        state=JobState.RUNNING,
                        phase=JobPhase.RENDER_FRAME,
                        current_frame=frame,
                        current_seconds_per_frame=float(heartbeat + 1),
                        latest_log_line="Rendering is still active",
                        renderer_active=True,
                        watcher_active=True,
                    )
                    if options.step_delay_seconds:
                        await asyncio.sleep(options.step_delay_seconds)
            if options.fail_at_frame == frame:
                await controller.add_log(job_id, "error", f"Fake renderer failed at frame {frame}.")
                await controller.fail_job(
                    job_id,
                    StructuredError(
                        code="fake_renderer_failure",
                        title="Render process stopped",
                        summary=f"The fake renderer failed at frame {frame}.",
                        likely_cause="A deterministic test failure was requested.",
                        recommended_action="Inspect logs or resume with corrected test settings.",
                        retryable=True,
                        context={"frame": frame},
                        timestamp=datetime.now(UTC),
                        job_id=job_id,
                    ),
                )
                return
            seconds = 0.8 + (frame % 7) * 0.1
            timings.append(seconds)
            ordered = sorted(timings)
            p90_index = max(0, int(len(ordered) * 0.9) - 1)
            elapsed = sum(timings)
            remaining = max(0, total - index - 1)
            eta = datetime.now(UTC) + timedelta(seconds=remaining * (elapsed / len(timings)))
            warning = (
                "Storage is approaching the configured reserve."
                if options.storage_warning_at_frame == frame
                else None
            )
            await controller.update_job(
                job_id,
                state=JobState.RUNNING,
                phase=JobPhase.RENDER_FRAME,
                current_frame=frame,
                rendered_frame_count=index + 1,
                inflight_frame_count=(index % chunk_size) + 1,
                current_seconds_per_frame=seconds,
                rolling_median_seconds=ordered[len(ordered) // 2],
                rolling_mean_seconds=elapsed / len(timings),
                p90_seconds=ordered[p90_index],
                estimated_completion_time=eta,
                chunk_start=chunk_start,
                chunk_end=chunk_end,
                current_chunk_progress=((index % chunk_size) + 1) / (chunk_end - chunk_start + 1),
                latest_log_line=f"Rendered frame {frame}",
                warning=warning,
                renderer_active=True,
                watcher_active=True,
                current_frame_started_at=datetime.now(UTC),
                last_output_at=datetime.now(UTC),
            )
            if warning:
                await controller.add_log(job_id, "warning", warning)
            if options.step_delay_seconds:
                await asyncio.sleep(options.step_delay_seconds)
            is_chunk_end = frame == chunk_end or index == total - 1
            if not is_chunk_end:
                continue
            published = index + 1
            preview_path = _publish_fake_preview(
                job.identity.output_directory,
                frame,
                width=job.identity.output_width or 1280,
                height=job.identity.output_height or 720,
            )
            preview_relative_path = preview_path.relative_to(
                Path(job.identity.output_directory)
            ).as_posix()
            await controller.update_job(
                job_id,
                phase=JobPhase.PUBLISH_CHUNK,
                validated_frame_count=published,
                published_frame_count=published,
                inflight_frame_count=0,
                chunks_completed=chunk_index + 1,
                latest_frame_preview=(
                    f"/api/mission-control/render/{job_id}/preview?v={frame}"
                ),
                latest_preview_frame=frame,
                latest_preview_at=datetime.now(UTC),
                latest_frame_artifact=preview_relative_path,
                latest_full_frame_url=(
                    f"/api/mission-control/render/{job_id}/frame?v={frame}"
                ),
                latest_log_line=f"Published safe chunk {chunk_start}-{chunk_end}",
                current_frame_started_at=None,
            )
            await controller.add_log(job_id, "info", f"Published safe chunk {chunk_start}-{chunk_end}.")
            current = controller.get_job(job_id)
            if current.safe_stop_status == SafeStopStatus.REQUESTED:
                await controller.update_job(
                    job_id,
                    state=JobState.PAUSED_SAFELY,
                    safe_stop_status=SafeStopStatus.PAUSED,
                    latest_log_line="Stopped safely after the current chunk",
                    renderer_active=False,
                    watcher_active=False,
                    current_frame_started_at=None,
                )
                await controller.add_log(job_id, "info", "Stopped safely after the current chunk.")
                return
        await controller.update_job(
            job_id,
            state=JobState.COMPLETE,
            phase=JobPhase.FINAL_VERIFY,
            completed_at=datetime.now(UTC),
            current_frame=job.frame_start + total - 1,
            rendered_frame_count=total,
            validated_frame_count=total,
            published_frame_count=total,
            inflight_frame_count=0,
            safe_stop_status=SafeStopStatus.NONE,
            latest_log_line="Fake render complete",
            renderer_active=False,
            watcher_active=False,
            current_frame_started_at=None,
        )
        await controller.add_log(job_id, "info", "Deterministic fake render completed.")

    def request_stop(self, job: JobRecord) -> None:
        task = self.tasks.get(job.id)
        if task is None or task.done():
            raise MissionControlError(
                409,
                "render_not_running",
                "Render is not running",
                "A safe stop can only be requested while the fake renderer is active.",
                "Resume the job before requesting a safe stop.",
                job_id=job.id,
            )

    def cancel_stop(self, job: JobRecord) -> None:
        task = self.tasks.get(job.id)
        if task is None or task.done():
            raise MissionControlError(
                409,
                "render_not_running",
                "Render is not running",
                "There is no active fake renderer stop request to cancel.",
                "Resume the render if more frames remain.",
                job_id=job.id,
            )

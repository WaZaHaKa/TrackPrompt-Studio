from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .manifests import CHUNK_OUTPUT_KIND, safe_relative_key, validate_sealed_manifest
from .models import FrameRange, IdentityBundle, require_sha256
from .storage.base import sha256_path


class ReturnImportError(ValueError):
    pass


class FrameValidator(Protocol):
    def __call__(self, path: Path, frame: int) -> None: ...


@dataclass(frozen=True, slots=True)
class ImportConflict:
    frame: int
    local_sha256: str
    returned_sha256: str
    local_preferred: bool = True


@dataclass(frozen=True, slots=True)
class ReturnImportResult:
    quarantine: Path
    published_frames: tuple[int, ...]
    identical_frames: tuple[int, ...]
    conflicts: tuple[ImportConflict, ...]


def _reject_links(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ReturnImportError("returned data must not contain symbolic links")


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _reject_overlapping_paths(
    source: Path,
    quarantine_root: Path,
    output_frames: Path | None = None,
) -> None:
    if _paths_overlap(source, quarantine_root):
        raise ReturnImportError(
            "returned source and quarantine root must be disjoint directories"
        )
    if output_frames is None:
        return
    if _paths_overlap(source, output_frames):
        raise ReturnImportError(
            "returned source and output frames must be disjoint directories"
        )
    if _paths_overlap(quarantine_root, output_frames):
        raise ReturnImportError(
            "quarantine root and output frames must be disjoint directories"
        )


def quarantine_return(returned: Path, quarantine_root: Path) -> Path:
    source = returned.resolve(strict=True)
    if not source.is_dir():
        raise ReturnImportError("returned path must be a directory")
    quarantine_root = quarantine_root.resolve()
    _reject_overlapping_paths(source, quarantine_root)
    _reject_links(source)
    quarantine_root.mkdir(parents=True, exist_ok=True)
    target = quarantine_root / f"return-{uuid.uuid4().hex}"
    shutil.copytree(source, target, copy_function=shutil.copy2)
    return target


def _validate_return_manifest(
    payload: Mapping[str, Any],
    identities: IdentityBundle,
    frame_range: FrameRange,
) -> list[dict[str, Any]]:
    manifest = validate_sealed_manifest(payload, expected_kind=CHUNK_OUTPUT_KIND)
    for key, expected_value in (
        ("sceneSha256", identities.scene_sha256),
        ("profileSha256", identities.profile_sha256),
        ("packageSha256", identities.package_sha256),
    ):
        if str(manifest.get(key, "")).upper() != expected_value:
            raise ReturnImportError(f"returned {key} does not match the package")
    if manifest.get("privateAudioUsed") is not False or manifest.get("encodingPerformed") is not False:
        raise ReturnImportError("returned cloud frames must not use private audio or encode media")
    frames = manifest.get("frames")
    if not isinstance(frames, list):
        raise ReturnImportError("returned frame manifest is missing")
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in frames:
        if not isinstance(raw, dict):
            raise ReturnImportError("returned frame entries must be objects")
        frame = int(raw.get("frame", 0))
        if frame in seen or not frame_range.start <= frame <= frame_range.end:
            raise ReturnImportError("returned frame is duplicate or outside the assigned range")
        seen.add(frame)
        key = safe_relative_key(str(raw.get("objectKey", "")))
        digest = require_sha256(str(raw.get("sha256", "")), "returned frame sha256")
        result.append({"frame": frame, "objectKey": key, "sha256": digest})
    expected_frames = set(range(frame_range.start, frame_range.end + 1))
    if seen != expected_frames:
        raise ReturnImportError("returned frame set is incomplete")
    return result


def import_quarantined_return(
    *,
    returned: Path,
    quarantine_root: Path,
    output_frames: Path,
    manifest: Mapping[str, Any],
    identities: IdentityBundle,
    frame_range: FrameRange,
    extension: str,
    validate_frame: FrameValidator,
) -> ReturnImportResult:
    source_root = returned.resolve(strict=True)
    resolved_quarantine_root = quarantine_root.resolve()
    resolved_output_frames = output_frames.resolve()
    _reject_overlapping_paths(
        source_root,
        resolved_quarantine_root,
        resolved_output_frames,
    )
    quarantine = quarantine_return(source_root, resolved_quarantine_root)
    records = _validate_return_manifest(manifest, identities, frame_range)
    output_frames = resolved_output_frames
    output_frames.mkdir(parents=True, exist_ok=True)
    published: list[int] = []
    identical: list[int] = []
    conflicts: list[ImportConflict] = []
    extension = extension.lower().lstrip(".")
    if extension not in {"png", "exr"}:
        raise ReturnImportError("returned frame extension is unsupported")
    for record in records:
        frame = int(record["frame"])
        expected_name = f"frame_{frame:06d}.{extension}"
        relative = str(record["objectKey"])
        source = (quarantine / Path(*relative.split("/"))).resolve(strict=True)
        try:
            source.relative_to(quarantine)
        except ValueError as exc:
            raise ReturnImportError("returned frame path escapes quarantine") from exc
        if source.name != expected_name or not source.is_file():
            raise ReturnImportError("returned frame name is not canonical")
        if sha256_path(source) != record["sha256"]:
            raise ReturnImportError("returned frame hash does not match its manifest")
        validate_frame(source, frame)
        destination = output_frames / expected_name
        if destination.exists():
            validate_frame(destination, frame)
            local_hash = sha256_path(destination)
            if local_hash == record["sha256"]:
                identical.append(frame)
            else:
                conflicts.append(ImportConflict(frame, local_hash, str(record["sha256"])))
            continue
        temporary = output_frames / f".{expected_name}.{uuid.uuid4().hex}.tmp"
        try:
            shutil.copy2(source, temporary)
            validate_frame(temporary, frame)
            if sha256_path(temporary) != record["sha256"]:
                raise ReturnImportError("copied frame changed before atomic publication")
            if destination.exists():
                local_hash = sha256_path(destination)
                if local_hash == record["sha256"]:
                    identical.append(frame)
                else:
                    conflicts.append(ImportConflict(frame, local_hash, str(record["sha256"])))
                continue
            os.replace(temporary, destination)
            published.append(frame)
        finally:
            if temporary.exists():
                temporary.unlink()
    return ReturnImportResult(
        quarantine,
        tuple(published),
        tuple(identical),
        tuple(conflicts),
    )

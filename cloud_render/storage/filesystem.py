from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from ..manifests import safe_relative_key
from .base import (
    ObjectConflictError,
    ObjectMetadata,
    ObjectNotFoundError,
    sha256_bytes,
    sha256_path,
)


class FilesystemStorage:
    """Atomic object storage for tests and shared filesystems."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        normalized = safe_relative_key(key)
        candidate = (self.root / Path(*normalized.split("/"))).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("object key escapes storage root") from exc
        return candidate

    def head(self, key: str) -> ObjectMetadata | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return ObjectMetadata(key, path.stat().st_size, sha256_path(path))

    def _existing_or_conflict(self, key: str, expected_sha256: str) -> ObjectMetadata:
        existing = self.head(key)
        if existing is None:
            raise ObjectNotFoundError(key)
        if existing.sha256 != expected_sha256:
            raise ObjectConflictError(f"object {key!r} already exists with different content")
        return existing

    def _publish_temporary(
        self,
        key: str,
        temporary: Path,
        target: Path,
        digest: str,
        *,
        if_absent: bool,
    ) -> ObjectMetadata | None:
        if not if_absent:
            os.replace(temporary, target)
            return None
        try:
            # A same-volume hard link is an atomic create-if-absent operation;
            # unlike exists()+replace(), it cannot overwrite a concurrent put.
            os.link(temporary, target)
        except FileExistsError:
            return self._existing_or_conflict(key, digest)
        temporary.unlink()
        return None

    def put_bytes(self, key: str, data: bytes, *, if_absent: bool = True) -> ObjectMetadata:
        target = self._path(key)
        digest = sha256_bytes(data)
        if target.exists() and if_absent:
            return self._existing_or_conflict(key, digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            existing = self._publish_temporary(
                key,
                temporary,
                target,
                digest,
                if_absent=if_absent,
            )
            if existing is not None:
                return existing
        finally:
            if temporary.exists():
                temporary.unlink()
        return ObjectMetadata(key, len(data), digest)

    def put_file(self, key: str, source: Path, *, if_absent: bool = True) -> ObjectMetadata:
        source = source.resolve(strict=True)
        digest = sha256_path(source)
        target = self._path(key)
        if target.exists() and if_absent:
            return self._existing_or_conflict(key, digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            existing = self._publish_temporary(
                key,
                temporary,
                target,
                digest,
                if_absent=if_absent,
            )
            if existing is not None:
                return existing
        finally:
            if temporary.exists():
                temporary.unlink()
        return ObjectMetadata(key, source.stat().st_size, digest)

    def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError(key)
        return path.read_bytes()

    def list(self, prefix: str = "") -> list[ObjectMetadata]:
        if prefix:
            safe_relative_key(prefix.rstrip("/"))
        results: list[ObjectMetadata] = []
        for path in sorted(item for item in self.root.rglob("*") if item.is_file()):
            key = path.relative_to(self.root).as_posix()
            if key.startswith(prefix):
                results.append(ObjectMetadata(key, path.stat().st_size, sha256_path(path)))
        return results

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

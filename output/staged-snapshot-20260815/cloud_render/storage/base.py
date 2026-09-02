from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..models import require_sha256


class ObjectStorageError(RuntimeError):
    pass


class ObjectNotFoundError(ObjectStorageError):
    pass


class ObjectConflictError(ObjectStorageError):
    pass


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    key: str
    size_bytes: int
    sha256: str
    etag: str | None = None

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ValueError("object size cannot be negative")
        object.__setattr__(self, "sha256", require_sha256(self.sha256, "object sha256"))


class ObjectStorage(Protocol):
    def put_bytes(self, key: str, data: bytes, *, if_absent: bool = True) -> ObjectMetadata: ...

    def put_file(self, key: str, source: Path, *, if_absent: bool = True) -> ObjectMetadata: ...

    def get_bytes(self, key: str) -> bytes: ...

    def head(self, key: str) -> ObjectMetadata | None: ...

    def list(self, prefix: str = "") -> list[ObjectMetadata]: ...

    def delete(self, key: str) -> None: ...


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()

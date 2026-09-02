from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Protocol

from ..manifests import safe_relative_key
from .base import (
    ObjectConflictError,
    ObjectMetadata,
    ObjectNotFoundError,
    sha256_bytes,
    sha256_path,
)


class S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete_object(self, **kwargs: Any) -> dict[str, Any]: ...


def _is_missing(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
        return code in {"404", "NoSuchKey", "NotFound"}
    return False


def _is_precondition_failed(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
        return code in {"409", "412", "PreconditionFailed", "ConditionalRequestConflict"}
    return False


class S3CompatibleStorage:
    """S3-compatible storage using an injected client; construction is offline."""

    def __init__(self, client: S3Client, bucket: str, *, prefix: str = "") -> None:
        if not bucket.strip():
            raise ValueError("bucket is required")
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if self.prefix:
            safe_relative_key(self.prefix)

    def _key(self, key: str) -> str:
        normalized = safe_relative_key(key)
        return f"{self.prefix}/{normalized}" if self.prefix else normalized

    def _public_key(self, stored_key: str) -> str:
        if not self.prefix:
            return stored_key
        marker = f"{self.prefix}/"
        if not stored_key.startswith(marker):
            raise ValueError("S3 result escaped the configured prefix")
        return stored_key[len(marker) :]

    def head(self, key: str) -> ObjectMetadata | None:
        stored = self._key(key)
        try:
            result = self.client.head_object(Bucket=self.bucket, Key=stored)
        except BaseException as exc:
            if _is_missing(exc):
                return None
            raise
        metadata = result.get("Metadata", {})
        digest = str(metadata.get("sha256", "")).upper()
        if len(digest) != 64:
            data = self.get_bytes(key)
            digest = sha256_bytes(data)
        return ObjectMetadata(
            key,
            int(result.get("ContentLength", 0)),
            digest,
            str(result.get("ETag", "")).strip('"') or None,
        )

    def _put(self, key: str, body: Any, size: int, digest: str, if_absent: bool) -> ObjectMetadata:
        arguments: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._key(key),
            "Body": body,
            "Metadata": {"sha256": digest},
        }
        if if_absent:
            arguments["IfNoneMatch"] = "*"
        try:
            result = self.client.put_object(**arguments)
        except BaseException as exc:
            if not (if_absent and _is_precondition_failed(exc)):
                raise
            existing = self.head(key)
            if existing is not None and existing.sha256 == digest:
                return existing
            raise ObjectConflictError(f"object {key!r} already exists with different content") from exc
        return ObjectMetadata(
            key,
            size,
            digest,
            str(result.get("ETag", "")).strip('"') or None,
        )

    def put_bytes(self, key: str, data: bytes, *, if_absent: bool = True) -> ObjectMetadata:
        return self._put(key, data, len(data), sha256_bytes(data), if_absent)

    def put_file(self, key: str, source: Path, *, if_absent: bool = True) -> ObjectMetadata:
        source = source.resolve(strict=True)
        with source.open("rb") as handle:
            return self._put(key, handle, source.stat().st_size, sha256_path(source), if_absent)

    def get_bytes(self, key: str) -> bytes:
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except BaseException as exc:
            if _is_missing(exc):
                raise ObjectNotFoundError(key) from exc
            raise
        body = result.get("Body")
        if isinstance(body, bytes):
            return body
        if isinstance(body, io.BytesIO) or hasattr(body, "read"):
            reader = getattr(body, "read", None)
            if not callable(reader):
                raise ObjectNotFoundError(f"S3 object {key!r} returned no readable body")
            data = reader()
            if isinstance(data, bytes):
                return data
        raise ObjectNotFoundError(f"S3 object {key!r} returned no binary body")

    def list(self, prefix: str = "") -> list[ObjectMetadata]:
        if prefix:
            safe_relative_key(prefix.rstrip("/"))
        stored_prefix = self._key(prefix.rstrip("/")) if prefix else (
            f"{self.prefix}/" if self.prefix else ""
        )
        token: str | None = None
        results: list[ObjectMetadata] = []
        while True:
            arguments: dict[str, Any] = {"Bucket": self.bucket, "Prefix": stored_prefix}
            if token:
                arguments["ContinuationToken"] = token
            page = self.client.list_objects_v2(**arguments)
            for item in page.get("Contents", []):
                stored_key = str(item["Key"])
                key = self._public_key(stored_key)
                metadata = self.head(key)
                if metadata is not None:
                    results.append(metadata)
            if not page.get("IsTruncated"):
                break
            token = str(page.get("NextContinuationToken", "")) or None
            if token is None:
                break
        return sorted(results, key=lambda item: item.key)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from cloud_render.storage import FilesystemStorage, ObjectConflictError, S3CompatibleStorage


class FakeClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str], str]] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        key = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise FakeClientError("PreconditionFailed")
        body = kwargs["Body"]
        data = body if isinstance(body, bytes) else body.read()
        self.objects[key] = (data, dict(kwargs.get("Metadata", {})), "etag")
        return {"ETag": '"etag"'}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        key = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if key not in self.objects:
            raise FakeClientError("NoSuchKey")
        data, metadata, etag = self.objects[key]
        return {"ContentLength": len(data), "Metadata": metadata, "ETag": etag}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        key = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if key not in self.objects:
            raise FakeClientError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[key][0])}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        bucket = str(kwargs["Bucket"])
        prefix = str(kwargs.get("Prefix", ""))
        keys = sorted(key for owner, key in self.objects if owner == bucket and key.startswith(prefix))
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.objects.pop((str(kwargs["Bucket"]), str(kwargs["Key"])), None)
        return {}


def test_filesystem_storage_is_atomic_idempotent_and_hash_checked(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path / "objects")
    first = storage.put_bytes("jobs/a/frame.bin", b"one")
    second = storage.put_bytes("jobs/a/frame.bin", b"one")
    assert first.sha256 == second.sha256
    assert storage.get_bytes("jobs/a/frame.bin") == b"one"
    with pytest.raises(ObjectConflictError):
        storage.put_bytes("jobs/a/frame.bin", b"different")


def test_filesystem_storage_put_file_list_and_delete(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    storage = FilesystemStorage(tmp_path / "objects")
    metadata = storage.put_file("packages/pkg/source.bin", source)
    assert metadata.size_bytes == 7
    assert [item.key for item in storage.list("packages/pkg")] == ["packages/pkg/source.bin"]
    storage.delete("packages/pkg/source.bin")
    assert storage.head("packages/pkg/source.bin") is None


def test_filesystem_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path / "objects")
    with pytest.raises(ValueError):
        storage.put_bytes("../escape", b"bad")


def test_s3_storage_uses_injected_client_and_conditional_writes(tmp_path: Path) -> None:
    client = FakeS3Client()
    storage = S3CompatibleStorage(client, "bucket", prefix="trackprompt")
    source = tmp_path / "frame.png"
    source.write_bytes(b"frame")
    metadata = storage.put_file("jobs/job/frame.png", source)
    assert metadata.size_bytes == 5
    assert storage.get_bytes("jobs/job/frame.png") == b"frame"
    assert storage.put_file("jobs/job/frame.png", source).sha256 == metadata.sha256
    source.write_bytes(b"different")
    with pytest.raises(ObjectConflictError):
        storage.put_file("jobs/job/frame.png", source)
    assert [item.key for item in storage.list("jobs/")] == ["jobs/job/frame.png"]
    storage.delete("jobs/job/frame.png")
    assert storage.head("jobs/job/frame.png") is None

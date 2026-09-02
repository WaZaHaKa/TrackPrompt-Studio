from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from cloud_render.cli import main
from cloud_render.manifests import PACKAGE_KIND, SCHEMA_VERSION, seal_manifest
from cloud_render.models import FrameRange, IdentityBundle, WorkerKind
from cloud_render.package_bridge import PackageBridgeError, prepare_cloud_manifest
from cloud_render.scheduler import SqliteScheduler
from cloud_render.storage import FilesystemStorage
from cloud_render.storage.base import sha256_path
from cloud_render.worker.core import WorkerConfig, WorkerOutcome, WorkerService
from cloud_render.worker.mock import MockRenderRuntime


def _remote_package_hash(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "packageSha256"}
    encoded = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def build_remote_package(root: Path) -> tuple[Path, dict[str, Any]]:
    scene = root / "scene" / "trackprompt-remote.blend"
    profile = root / "profile" / "render-profile.remote.json"
    helper = root / "blender" / "render_remote_chunk.py"
    blender_init = root / "blender" / "__init__.py"
    tools_init = root / "tools" / "__init__.py"
    worker = root / "render_trackprompt_worker.py"
    for path in (scene, profile, helper, blender_init, tools_init, worker):
        path.parent.mkdir(parents=True, exist_ok=True)
    scene.write_bytes(b"synthetic sanitized scene")
    profile.write_text(
        json.dumps({"remoteWorker": {"privateAudioIncluded": False}}),
        encoding="utf-8",
    )
    helper.write_text("# synthetic packaged Blender helper\n", encoding="utf-8")
    blender_init.write_bytes(b"")
    tools_init.write_bytes(b"")
    worker.write_text("# synthetic legacy worker\n", encoding="utf-8")
    files = []
    for path in sorted((scene, profile, helper, blender_init, tools_init, worker)):
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sizeBytes": path.stat().st_size,
                "sha256": sha256_path(path),
            }
        )
    checksum = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-remote-checksum-manifest",
        "files": files,
    }
    checksum_path = root / "checksum-manifest.json"
    checksum_path.write_text(json.dumps(checksum), encoding="utf-8")
    manifest: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "kind": "trackprompt-remote-render-package",
        "packageId": "pkg-synthetic",
        "blenderVersion": "5.2.0",
        "scene": {
            "relativePath": "scene/trackprompt-remote.blend",
            "sha256": sha256_path(scene),
            "sourceSceneSha256": "E" * 64,
        },
        "profile": {
            "relativePath": "profile/render-profile.remote.json",
            "sha256": sha256_path(profile),
            "sourceProductionProfileSha256": "F" * 64,
        },
        "packageSha256": "",
        "checksumManifest": "checksum-manifest.json",
        "checksumSha256": sha256_path(checksum_path),
        "frameContract": {
            "frameStart": 1,
            "frameEnd": 2,
            "frameCount": 2,
            "fps": 30.0,
            "width": 2,
            "height": 2,
            "format": "PNG",
            "bitDepth": 8,
            "colorMode": "RGB",
            "filenamePattern": "frame_%06d.png",
        },
        "privateAudioIncluded": False,
        "networkUploadAuthorized": False,
        "encodingAllowed": False,
        "deterministicSeed": 0,
    }
    manifest["packageSha256"] = _remote_package_hash(manifest)
    (root / "package-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, manifest


def test_prepare_bridge_is_deterministic_private_and_cli_executable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package, remote = build_remote_package(tmp_path / "remote")
    first = prepare_cloud_manifest(package)
    second = prepare_cloud_manifest(package)
    assert first == second
    assert first["kind"] == PACKAGE_KIND
    assert first["schemaVersion"] == SCHEMA_VERSION
    assert first["identities"]["sceneSha256"] == remote["scene"]["sha256"]
    assert first["identities"]["profileSha256"] == remote["profile"]["sha256"]
    assert first["identities"]["packageSha256"] == remote["packageSha256"]
    assert first["sourcePackage"]["sourceProductionProfileSha256"] == "F" * 64
    assert first["sourcePackage"]["sourceSceneSha256"] == "E" * 64
    assert first["renderContract"]["fps"] == 30
    zero_byte_files = {
        item["path"]
        for item in first["files"]
        if item["sizeBytes"] == 0
    }
    assert zero_byte_files == {"blender/__init__.py", "tools/__init__.py"}
    serialized = json.dumps(first)
    assert str(package) not in serialized
    assert "private.wav" not in serialized

    output = tmp_path / "cloud-package.json"
    assert main(
        [
            "prepare-manifest",
            "--remote-package",
            str(package),
            "--output",
            str(output),
        ]
    ) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["data"]["sourcePackageValidated"] is True
    assert response["data"]["sourcePackage"] == first["sourcePackage"]
    assert json.loads(output.read_text(encoding="utf-8")) == first

    database = tmp_path / "scheduler.sqlite3"
    assert main(
        [
            "scheduler-init",
            "--database",
            str(database),
            "--job-id",
            "bridged-job",
            "--package-manifest",
            str(output),
            "--frames-per-chunk",
            "2",
        ]
    ) == 0
    scheduled = json.loads(capsys.readouterr().out)
    assert scheduled["data"]["manifestSha256"] == first["manifestSha256"]
    assert main(
        [
            "mock-worker",
            "--package-manifest",
            str(output),
            "--database",
            str(database),
            "--storage-root",
            str(tmp_path / "storage"),
            "--job-id",
            "bridged-job",
            "--worker-id",
            "mock-bridge",
            "--run-until-idle",
        ]
    ) == 0
    worker = json.loads(capsys.readouterr().out)
    assert worker["data"]["results"][0]["outcome"] == "COMPLETED_CHUNK"


def test_prepare_manifest_rejects_output_inside_immutable_remote_package(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package, _ = build_remote_package(tmp_path / "remote")
    before = {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file()
    }
    for output in (package, package / "cloud-package.json"):
        assert main(
            [
                "prepare-manifest",
                "--remote-package",
                str(package),
                "--output",
                str(output),
            ]
        ) == 2
        error = json.loads(capsys.readouterr().out)
        assert "outside the immutable remote package" in error["error"]["message"]
    after = {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (package / "cloud-package.json").exists()


def test_bridge_rejects_remote_content_drift_and_rehashed_private_fields(
    tmp_path: Path,
) -> None:
    package, _ = build_remote_package(tmp_path / "drift")
    (package / "scene" / "trackprompt-remote.blend").write_bytes(b"tampered")
    with pytest.raises(PackageBridgeError, match="validation reported issues"):
        prepare_cloud_manifest(package)

    private_package, manifest = build_remote_package(tmp_path / "private")
    manifest["audioPath"] = "private.wav"
    manifest["packageSha256"] = _remote_package_hash(manifest)
    (private_package / "package-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(PackageBridgeError, match="private fields"):
        prepare_cloud_manifest(private_package)


@pytest.mark.parametrize("fps", [29.97, float("nan"), True])
def test_bridge_rejects_non_integer_or_nonfinite_fps(tmp_path: Path, fps: object) -> None:
    package, manifest = build_remote_package(tmp_path / "remote")
    manifest["frameContract"]["fps"] = fps
    manifest["packageSha256"] = _remote_package_hash(manifest)
    (package / "package-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(PackageBridgeError, match="frames per second"):
        prepare_cloud_manifest(package)


@pytest.mark.parametrize("size_bytes", [None, False, 0.5, -1])
def test_bridge_rejects_invalid_remote_file_sizes(
    tmp_path: Path,
    size_bytes: object,
) -> None:
    package, manifest = build_remote_package(tmp_path / "remote")
    checksum_path = package / "checksum-manifest.json"
    checksum = json.loads(checksum_path.read_text(encoding="utf-8"))
    initializer = next(
        item for item in checksum["files"] if item["path"] == "blender/__init__.py"
    )
    initializer["sizeBytes"] = size_bytes
    checksum_path.write_text(json.dumps(checksum), encoding="utf-8")
    manifest["checksumSha256"] = sha256_path(checksum_path)
    manifest["packageSha256"] = _remote_package_hash(manifest)
    (package / "package-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(PackageBridgeError):
        prepare_cloud_manifest(package)


def test_scheduler_lease_binds_the_exact_cloud_manifest(tmp_path: Path) -> None:
    package, _ = build_remote_package(tmp_path / "remote")
    cloud = prepare_cloud_manifest(package)
    identities = IdentityBundle(
        cloud["identities"]["sceneSha256"],
        cloud["identities"]["profileSha256"],
        cloud["identities"]["packageSha256"],
    )
    tampered = seal_manifest(
        {
            key: value
            for key, value in cloud.items()
            if key != "manifestSha256"
        }
        | {"operatorNote": "different but otherwise safe"}
    )
    with SqliteScheduler(tmp_path / "scheduler.sqlite3") as scheduler:
        scheduler.create_job(
            "bound-job",
            identities,
            FrameRange(1, 2),
            frames_per_chunk=2,
            manifest_sha256=cloud["manifestSha256"],
        )
        worker = WorkerService(
            WorkerConfig("bound-job", "worker", WorkerKind.CLOUD),
            tampered,
            scheduler,
            FilesystemStorage(tmp_path / "storage"),
            MockRenderRuntime(identities, width=2, height=2),
        )
        result = worker.run_once()
    assert result.outcome == WorkerOutcome.FAILED
    assert "another cloud manifest" in result.reason

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from cloud_render.models import FrameRange, IdentityBundle, WorkerKind
from cloud_render.package_bridge import PackageBridgeError, prepare_cloud_manifest
from cloud_render.scheduler import SqliteScheduler
from cloud_render.storage import FilesystemStorage
from cloud_render.worker.blender import (
    BlenderSubprocessRuntime,
    WorkerCommandResult,
)
from cloud_render.worker.core import WorkerConfig, WorkerError, WorkerOutcome, WorkerService
from cloud_render.worker.mock import _write_png
from cloud_render.worker.render_worker import run as run_worker

from .test_package_bridge import _remote_package_hash, build_remote_package


class FakeWorkerRunner:
    def __init__(self, *, version: str = "5.2.0", renderer: str = "NVIDIA L40S") -> None:
        self.version = version
        self.renderer = renderer
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
        pulse: Callable[[], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> WorkerCommandResult:
        del timeout_seconds, cwd
        arguments = tuple(args)
        self.calls.append(arguments)
        if arguments[-1] == "--version":
            return WorkerCommandResult(arguments, 0, f"Blender {self.version}\n")
        if "--query-gpu=name,driver_version,memory.total" in arguments:
            return WorkerCommandResult(arguments, 0, "NVIDIA L40S, 550.54, 46068\n")
        if "--python-expr" in arguments:
            return WorkerCommandResult(
                arguments,
                0,
                (
                    'TRACKPROMPT_GPU_PROBE={"backend":"OPENGL",'
                    f'"device":"GPU","vendor":"NVIDIA","renderer":"{self.renderer}"}}\n'
                ),
            )
        if cancelled is not None and cancelled():
            raise AssertionError("test render was unexpectedly cancelled")
        if pulse is not None:
            pulse()
        output = Path(arguments[arguments.index("--output") + 1])
        start = int(arguments[arguments.index("--start") + 1])
        end = int(arguments[arguments.index("--end") + 1])
        for frame in range(start, end + 1):
            _write_png(output / f"frame_{frame:06d}.png", 2, 2, 8, frame)
        return WorkerCommandResult(arguments, 0, "rendered\n")


def _identities(manifest: dict[str, object]) -> IdentityBundle:
    raw = manifest["identities"]
    assert isinstance(raw, dict)
    return IdentityBundle(
        str(raw["sceneSha256"]),
        str(raw["profileSha256"]),
        str(raw["packageSha256"]),
    )


def test_production_runtime_uses_bounded_argument_array_and_real_pngs(
    tmp_path: Path,
) -> None:
    package, _ = build_remote_package(tmp_path / "remote")
    manifest = prepare_cloud_manifest(package)
    blender = tmp_path / "blender"
    blender.write_bytes(b"synthetic executable placeholder")
    runner = FakeWorkerRunner()
    runtime = BlenderSubprocessRuntime(
        package,
        blender,
        worker_id="gpu-worker",
        runner=runner,
        render_timeout_seconds=600,
    )
    with SqliteScheduler(tmp_path / "scheduler.sqlite3") as scheduler:
        scheduler.create_job(
            "production-job",
            _identities(manifest),
            FrameRange(1, 2),
            frames_per_chunk=2,
            manifest_sha256=str(manifest["manifestSha256"]),
        )
        service = WorkerService(
            WorkerConfig("production-job", "gpu-worker", WorkerKind.CLOUD),
            manifest,
            scheduler,
            FilesystemStorage(tmp_path / "storage"),
            runtime,
        )
        result = service.run_once()
        assert scheduler.status("production-job").complete is True
    assert result.outcome == WorkerOutcome.COMPLETED_CHUNK
    assert len(runner.calls) == 4
    render = runner.calls[-1]
    assert render[0] == str(blender.resolve())
    assert "--background" in render
    assert str(package / "blender" / "render_remote_chunk.py") in render
    assert str(package / "render_trackprompt_worker.py") not in render


def test_production_worker_entrypoint_accepts_injected_runner(tmp_path: Path) -> None:
    package, _ = build_remote_package(tmp_path / "remote")
    manifest = prepare_cloud_manifest(package)
    manifest_path = tmp_path / "cloud-package.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    database = tmp_path / "scheduler.sqlite3"
    with SqliteScheduler(database) as scheduler:
        scheduler.create_job(
            "entrypoint-job",
            _identities(manifest),
            FrameRange(1, 2),
            frames_per_chunk=2,
            manifest_sha256=str(manifest["manifestSha256"]),
        )
    blender = tmp_path / "blender"
    blender.write_bytes(b"synthetic executable placeholder")
    exit_code, payload = run_worker(
        [
            "--package-manifest",
            str(manifest_path),
            "--database",
            str(database),
            "--storage-root",
            str(tmp_path / "storage"),
            "--job-id",
            "entrypoint-job",
            "--worker-id",
            "gpu-worker",
            "--remote-package",
            str(package),
            "--blender",
            str(blender),
        ],
        runner=FakeWorkerRunner(),
    )
    assert exit_code == 0
    assert payload["mock"] is False
    assert payload["results"][0]["outcome"] == "COMPLETED_CHUNK"


def test_production_runtime_rejects_unbound_scheduler_lease(tmp_path: Path) -> None:
    package, _ = build_remote_package(tmp_path / "remote")
    manifest = prepare_cloud_manifest(package)
    blender = tmp_path / "blender"
    blender.write_bytes(b"synthetic executable placeholder")
    runner = FakeWorkerRunner()
    runtime = BlenderSubprocessRuntime(
        package,
        blender,
        worker_id="gpu-worker",
        runner=runner,
    )
    with SqliteScheduler(tmp_path / "scheduler.sqlite3") as scheduler:
        scheduler.create_job(
            "unbound-job",
            _identities(manifest),
            FrameRange(1, 2),
            frames_per_chunk=2,
        )
        service = WorkerService(
            WorkerConfig("unbound-job", "gpu-worker", WorkerKind.CLOUD),
            manifest,
            scheduler,
            FilesystemStorage(tmp_path / "storage"),
            runtime,
        )
        result = service.run_once()
    assert result.outcome == WorkerOutcome.FAILED
    assert "not bound to this exact cloud manifest" in result.reason
    assert len(runner.calls) == 3


def test_production_runtime_rejects_helper_tamper_after_inspection(
    tmp_path: Path,
) -> None:
    package, _ = build_remote_package(tmp_path / "remote")
    manifest = prepare_cloud_manifest(package)
    blender = tmp_path / "blender"
    blender.write_bytes(b"synthetic executable placeholder")
    runner = FakeWorkerRunner()
    runtime = BlenderSubprocessRuntime(
        package,
        blender,
        worker_id="gpu-worker",
        runner=runner,
    )
    with SqliteScheduler(tmp_path / "scheduler.sqlite3") as scheduler:
        scheduler.create_job(
            "tamper-job",
            _identities(manifest),
            FrameRange(1, 2),
            frames_per_chunk=2,
            manifest_sha256=str(manifest["manifestSha256"]),
        )
        service = WorkerService(
            WorkerConfig("tamper-job", "gpu-worker", WorkerKind.CLOUD),
            manifest,
            scheduler,
            FilesystemStorage(tmp_path / "storage"),
            runtime,
        )
        (package / "blender" / "render_remote_chunk.py").write_text(
            "# tampered after inspection\n", encoding="utf-8"
        )
        result = service.run_once()
    assert result.outcome == WorkerOutcome.FAILED
    assert "validation reported issues" in result.reason
    assert len(runner.calls) == 3


@pytest.mark.parametrize(
    ("version", "renderer", "message"),
    [
        ("5.3.0", "NVIDIA L40S", "Blender version differs"),
        ("5.2.0", "NVIDIA llvmpipe", "verified NVIDIA graphics renderer"),
    ],
)
def test_production_runtime_rejects_version_or_software_renderer(
    tmp_path: Path,
    version: str,
    renderer: str,
    message: str,
) -> None:
    package, _ = build_remote_package(tmp_path / "remote")
    manifest = prepare_cloud_manifest(package)
    blender = tmp_path / "blender"
    blender.write_bytes(b"synthetic executable placeholder")
    runtime = BlenderSubprocessRuntime(
        package,
        blender,
        worker_id="gpu-worker",
        runner=FakeWorkerRunner(version=version, renderer=renderer),
    )
    with pytest.raises(WorkerError, match=message):
        runtime.inspect(manifest)


def test_production_runtime_rejects_manifest_remote_mismatch_before_probe(
    tmp_path: Path,
) -> None:
    first, _ = build_remote_package(tmp_path / "first")
    second, second_manifest = build_remote_package(tmp_path / "second")
    second_manifest["scene"]["sourceSceneSha256"] = "D" * 64
    second_manifest["packageSha256"] = _remote_package_hash(second_manifest)
    (second / "package-manifest.json").write_text(
        json.dumps(second_manifest), encoding="utf-8"
    )
    manifest = prepare_cloud_manifest(first)
    blender = tmp_path / "blender"
    blender.write_bytes(b"synthetic executable placeholder")
    runner = FakeWorkerRunner()
    runtime = BlenderSubprocessRuntime(
        second,
        blender,
        worker_id="gpu-worker",
        runner=runner,
    )
    with pytest.raises(PackageBridgeError, match="does not exactly match"):
        runtime.inspect(manifest)
    assert runner.calls == []

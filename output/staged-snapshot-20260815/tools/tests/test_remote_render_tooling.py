from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
import render_trackprompt_worker as legacy_worker

from tools import final_render_tooling as final
from tools import remote_render_tooling as remote
from tools.tests.test_final_render_tooling import _builder_profile_payload, _sha, _write_png


class _FakeTimedOutProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.stdout = io.BytesIO(b"stdout" * 1000)
        self.stderr = io.BytesIO(b"stderr" * 1000)
        self.terminated = False

    def wait(self, timeout: float | None = None) -> int:
        if self.terminated:
            return -9
        raise subprocess.TimeoutExpired(["blender"], timeout)

    def poll(self) -> int | None:
        return -9 if self.terminated else None

    def kill(self) -> None:
        self.terminated = True


class _FakeSuccessfulRenderRunner:
    def __init__(self) -> None:
        self.command: tuple[str, ...] | None = None
        self.timeout_seconds = 0.0
        self.max_log_bytes = 0

    def run(
        self,
        command: list[str] | tuple[str, ...],
        *,
        timeout_seconds: float,
        stdout_path: Path,
        stderr_path: Path,
        environment: dict[str, str],
        max_log_bytes: int,
    ) -> int:
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self.max_log_bytes = max_log_bytes
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        stdout_path.write_text("synthetic bounded stdout", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        output = Path(command[command.index("--output") + 1])
        start = int(command[command.index("--start") + 1])
        end = int(command[command.index("--end") + 1])
        for frame in range(start, end + 1):
            _write_png(
                output / f"frame_{frame:06d}.png",
                width=16,
                height=16,
                bit_depth=16,
            )
        return 0


def _package_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source_scene = tmp_path / "approved.blend"
    source_scene.write_bytes(b"approved synthetic scene")
    sanitized_scene = tmp_path / "sanitized.blend"
    sanitized_scene.write_bytes(b"sanitized synthetic scene without audio")
    profile_path = tmp_path / "source-profile.json"
    payload = _builder_profile_payload(source_scene)
    payload["sourceFilename"] = "private-track.wav"
    payload["rawLyrics"] = "private words"
    payload["authorization"]["tokenHash"] = "secret"  # type: ignore[index]
    payload["output"] = {
        "framesSubdirectory": "frames",
        "rootDirectory": r"C:\private\local-output",
        "scratchPath": r"D:\private\scratch",
    }
    profile_path.write_text(json.dumps(payload), encoding="utf-8")
    report_path = tmp_path / "sanitization.json"
    report_path.write_text(
        json.dumps(
            {
                "sourceSceneSha256": _sha(source_scene),
                "sanitizedSceneSha256": _sha(sanitized_scene),
                "audioFullyBaked": True,
                "privateAudioIncluded": False,
            }
        ),
        encoding="utf-8",
    )
    package_path = tmp_path / "package"
    result = remote.create_package(
        sanitized_scene,
        profile_path,
        package_path,
        sanitization_report=report_path,
        workers=2,
        frames_per_chunk=2,
        blender_version="5.2.0 LTS",
    )
    assert result["ok"] is True
    return package_path, profile_path, source_scene, sanitized_scene


def test_package_is_private_relative_hashed_and_tamper_evident(tmp_path: Path) -> None:
    package, _, _, _ = _package_fixture(tmp_path)
    packaged_profile = (package / "profile" / "render-profile.remote.json").read_text(encoding="utf-8")
    assert "private-track" not in packaged_profile
    assert "private words" not in packaged_profile
    assert "C:\\private" not in packaged_profile
    assert '"privateAudioIncluded": false' in packaged_profile
    assert remote.validate_package(package)["ok"] is True

    unexpected = package / "unexpected.txt"
    unexpected.write_text("not checksummed", encoding="utf-8")
    validation = remote.validate_package(package)
    assert validation["ok"] is False
    assert {issue["code"] for issue in validation["issues"]} == {"unexpected-package-file"}


def test_packaged_worker_import_does_not_mutate_immutable_package(tmp_path: Path) -> None:
    package, _, _, _ = _package_fixture(tmp_path)
    completed = subprocess.run(
        [sys.executable, str(package / "render_trackprompt_worker.py"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert not list(package.rglob("__pycache__"))
    assert remote.validate_package(package)["ok"] is True


def test_bounded_legacy_runner_caps_logs_and_terminates_tree_on_timeout(
    tmp_path: Path,
) -> None:
    process = _FakeTimedOutProcess()
    factory_calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def process_factory(command: list[str], **kwargs: object) -> _FakeTimedOutProcess:
        factory_calls.append((tuple(command), kwargs))
        return process

    terminated: list[int] = []

    def terminate_tree(candidate: _FakeTimedOutProcess) -> None:
        terminated.append(candidate.pid)
        candidate.terminated = True

    runner = legacy_worker.BoundedRenderCommandRunner(
        process_factory=process_factory,
        tree_terminator=terminate_tree,
    )
    stdout = tmp_path / "worker.stdout.log"
    stderr = tmp_path / "worker.stderr.log"
    with pytest.raises(final.ToolingError, match="bounded render timeout"):
        runner.run(
            ["blender", "--background", "scene.blend"],
            timeout_seconds=1,
            stdout_path=stdout,
            stderr_path=stderr,
            environment={"PYTHONDONTWRITEBYTECODE": "1"},
            max_log_bytes=1024,
        )
    assert terminated == [4242]
    assert factory_calls[0][0] == ("blender", "--background", "scene.blend")
    assert factory_calls[0][1]["shell"] is False
    assert len(stdout.read_bytes()) <= 1024
    assert len(stderr.read_bytes()) <= 1024
    assert b"[output truncated]" in stdout.read_bytes()
    assert b"[output truncated]" in stderr.read_bytes()


def test_legacy_worker_uses_injected_bounded_argument_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package, _, _, _ = _package_fixture(tmp_path)
    package_manifest = json.loads(
        (package / "package-manifest.json").read_text(encoding="utf-8")
    )
    assignment = json.loads(
        (package / "chunk-manifest.json").read_text(encoding="utf-8")
    )["chunks"][0]
    blender = tmp_path / "blender"
    blender.write_bytes(b"synthetic executable placeholder")
    monkeypatch.setattr(
        legacy_worker,
        "_blender_version",
        lambda _path: package_manifest["blenderVersion"],
    )
    monkeypatch.setattr(
        legacy_worker,
        "_gpu_metadata",
        lambda: {"model": "Synthetic NVIDIA GPU", "driverVersion": "test", "vramMiB": 1},
    )
    runner = _FakeSuccessfulRenderRunner()
    output = tmp_path / "worker-return"
    assert legacy_worker.main(
        [
            "--package-directory",
            str(package),
            "--blender",
            str(blender),
            "--chunk-id",
            assignment["chunkId"],
            "--worker-id",
            "fake-worker",
            "--output-directory",
            str(output),
            "--render-timeout-seconds",
            "120",
            "--max-log-bytes",
            "2048",
        ],
        render_runner=runner,
    ) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is True
    assert runner.timeout_seconds == 120
    assert runner.max_log_bytes == 2048
    assert runner.command is not None
    assert runner.command[0] == str(blender.resolve())
    assert "--background" in runner.command
    worker_manifest = json.loads(
        (output / "worker-manifest.json").read_text(encoding="utf-8")
    )
    assert worker_manifest["expectedFrameCount"] == assignment["expectedFrameCount"]
    assert len(worker_manifest["frames"]) == assignment["expectedFrameCount"]


def test_chunk_distribution_is_nonoverlapping_and_complete() -> None:
    chunks = remote.generate_chunks(1, 13_029, 150, ["a", "b", "c"])
    frames = [frame for chunk in chunks for frame in range(chunk["startFrame"], chunk["endFrame"] + 1)]
    assert frames == list(range(1, 13_030))
    assert len(frames) == len(set(frames))
    assert {chunk["workerId"] for chunk in chunks} == {"a", "b", "c"}


def test_remote_return_is_quarantined_validated_and_atomically_published(tmp_path: Path) -> None:
    package, source_profile_path, source_scene, _ = _package_fixture(tmp_path)
    package_manifest = json.loads((package / "package-manifest.json").read_text(encoding="utf-8"))
    assignment = json.loads((package / "chunk-manifest.json").read_text(encoding="utf-8"))["chunks"][0]
    returned = tmp_path / "returned"
    frames = returned / "frames"
    frames.mkdir(parents=True)
    frame_records = []
    for frame in range(assignment["startFrame"], assignment["endFrame"] + 1):
        path = frames / f"frame_{frame:06d}.png"
        _write_png(path, width=16, height=16, bit_depth=16)
        frame_records.append({"frame": frame, "fileName": path.name, "sha256": _sha(path), "sizeBytes": path.stat().st_size})
    worker_manifest = {
        "schemaVersion": "1.0.0",
        "kind": remote.WORKER_KIND,
        "workerId": assignment["workerId"],
        "chunkId": assignment["chunkId"],
        "packageId": package_manifest["packageId"],
        "packageSha256": package_manifest["packageSha256"],
        "sceneSha256": package_manifest["scene"]["sha256"],
        "profileSha256": package_manifest["profile"]["sha256"],
        "blenderVersion": package_manifest["blenderVersion"],
        "startFrame": assignment["startFrame"],
        "endFrame": assignment["endFrame"],
        "expectedFrameCount": assignment["expectedFrameCount"],
        "frames": frame_records,
    }
    (returned / "worker-manifest.json").write_text(json.dumps(worker_manifest), encoding="utf-8")
    assert remote.validate_return(returned, package)["ok"] is True

    local_profile = final.load_render_profile(source_profile_path)
    output = tmp_path / "managed-output"
    final.render_plan(
        local_profile,
        source_scene,
        output,
        initialize=True,
        authorization_token=local_profile.authorization_token,
        require_authorization=True,
        chunk_size_override=None,
        chunk_rationale_override=None,
        workers=1,
    )
    imported = remote.import_return(returned, package, source_profile_path, source_scene, output)
    assert imported["ok"] is True
    assert imported["published"] == [1, 2]
    assert (output / "frames" / "frame_000001.png").is_file()
    assert Path(imported["quarantine"]).is_dir()

    imported_again = remote.import_return(returned, package, source_profile_path, source_scene, output)
    assert imported_again["ok"] is True
    assert imported_again["published"] == []
    assert all(item["sameSha256"] for item in imported_again["existing"])


def test_remote_return_rejects_assignment_and_profile_identity_changes(tmp_path: Path) -> None:
    package, _, _, _ = _package_fixture(tmp_path)
    package_manifest = json.loads((package / "package-manifest.json").read_text(encoding="utf-8"))
    returned = tmp_path / "bad-return"
    (returned / "frames").mkdir(parents=True)
    (returned / "worker-manifest.json").write_text(
        json.dumps(
            {
                "kind": remote.WORKER_KIND,
                "chunkId": "chunk-999999-999999",
                "packageId": package_manifest["packageId"],
                "packageSha256": package_manifest["packageSha256"],
                "sceneSha256": package_manifest["scene"]["sha256"],
                "profileSha256": "0" * 64,
                "blenderVersion": package_manifest["blenderVersion"],
                "startFrame": 1,
                "endFrame": 0,
                "expectedFrameCount": 0,
                "frames": [],
            }
        ),
        encoding="utf-8",
    )
    result = remote.validate_return(returned, package)
    codes = {issue["code"] for issue in result["issues"]}
    assert "profileSha256-mismatch" in codes
    assert "assignment-missing-or-ambiguous" in codes


def test_invalid_package_plan_is_rejected(tmp_path: Path) -> None:
    scene = tmp_path / "scene.blend"
    scene.write_bytes(b"scene")
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    with pytest.raises(final.ToolingError, match="at least one worker"):
        remote.create_package(scene, profile, tmp_path / "package", sanitization_report=None, workers=0, frames_per_chunk=2, blender_version="5.2")

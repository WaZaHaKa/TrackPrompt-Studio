from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Any, BinaryIO, Protocol, cast

from ..frame_validation import validate_png
from ..manifests import canonical_json_bytes, canonical_sha256, safe_relative_key
from ..models import ChunkLease, IdentityBundle
from ..package_bridge import validate_bridge_against_remote
from .core import (
    RenderedFrame,
    RuntimeInfo,
    WorkerCancelled,
    WorkerError,
)

_GPU_MARKER = "TRACKPROMPT_GPU_PROBE="
_GPU_PROBE = (
    "import gpu,json;"
    "print('TRACKPROMPT_GPU_PROBE='+json.dumps({"
    "'backend':gpu.platform.backend_type_get(),"
    "'device':gpu.platform.device_type_get(),"
    "'vendor':gpu.platform.vendor_get(),"
    "'renderer':gpu.platform.renderer_get()}))"
)
_SOFTWARE_RENDERERS = (
    "llvmpipe",
    "softpipe",
    "swiftshader",
    "software rasterizer",
    "microsoft basic render",
)


@dataclass(frozen=True, slots=True)
class WorkerCommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class WorkerCommandRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
        pulse: Callable[[], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> WorkerCommandResult: ...


@dataclass(frozen=True, slots=True)
class BoundedWorkerSubprocessRunner:
    """Non-shell runner with bounded output, time, cancellation, and tree cleanup."""

    maximum_timeout_seconds: float = 86_400.0
    poll_seconds: float = 5.0
    max_output_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_timeout_seconds <= 86_400:
            raise ValueError("worker subprocess timeout bound must be between 1s and 24h")
        if not 0.1 <= self.poll_seconds <= 60:
            raise ValueError("worker subprocess poll interval must be bounded")
        if self.max_output_bytes < 1:
            raise ValueError("worker subprocess output bound must be positive")

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
        pulse: Callable[[], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> WorkerCommandResult:
        if not args or any(not isinstance(item, str) or "\x00" in item for item in args):
            raise WorkerError("worker command must be a non-empty string argument array")
        timeout = min(max(float(timeout_seconds), 0.1), self.maximum_timeout_seconds)
        creation_flags = (
            int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            if os.name == "nt"
            else 0
        )
        try:
            process = subprocess.Popen(  # noqa: S603 - exact argument arrays, shell disabled
                list(args),
                cwd=str(cwd) if cwd is not None else None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )
        except (FileNotFoundError, OSError) as exc:
            raise WorkerError("worker subprocess executable could not be started") from exc
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        readers = (
            Thread(
                target=self._drain_bounded,
                args=(cast(BinaryIO, process.stdout), stdout),
                daemon=True,
            ),
            Thread(
                target=self._drain_bounded,
                args=(cast(BinaryIO, process.stderr), stderr),
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        returncode: int | None = None
        try:
            while returncode is None:
                if cancelled is not None and cancelled():
                    self._terminate_tree(process)
                    raise WorkerCancelled("render cancelled while Blender was running")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_tree(process)
                    raise WorkerError("worker subprocess exceeded its bounded timeout")
                try:
                    returncode = process.wait(timeout=min(self.poll_seconds, remaining))
                except subprocess.TimeoutExpired:
                    if pulse is not None:
                        pulse()
        finally:
            if process.poll() is None:
                self._terminate_tree(process)
            for reader in readers:
                reader.join(timeout=1)
        return WorkerCommandResult(
            tuple(args),
            int(returncode),
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    def _drain_bounded(self, stream: BinaryIO, output: bytearray) -> None:
        while chunk := stream.read(65_536):
            remaining = self.max_output_bytes - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(  # noqa: S603 - fixed system utility and numeric PID
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            kill_process_group = getattr(os, "killpg", None)
            kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
            try:
                if not callable(kill_process_group):
                    raise OSError("process-group termination is unavailable")
                kill_process_group(process.pid, kill_signal)
            except (OSError, ProcessLookupError):
                process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


class BlenderSubprocessRuntime:
    """Production runtime for the validated worker bundled in a sanitized package."""

    def __init__(
        self,
        remote_package: Path,
        blender_executable: Path,
        *,
        worker_id: str,
        nvidia_smi_executable: str = "nvidia-smi",
        render_timeout_seconds: float = 21_600.0,
        runner: WorkerCommandRunner | None = None,
    ) -> None:
        if not worker_id or worker_id.startswith("-"):
            raise ValueError("worker_id must be a non-option identifier")
        if not 1 <= render_timeout_seconds <= 86_400:
            raise ValueError("render timeout must be between 1s and 24h")
        self.remote_package = remote_package.resolve(strict=True)
        self.blender_executable = blender_executable.resolve(strict=True)
        self.nvidia_smi_executable = nvidia_smi_executable
        self.worker_id = worker_id
        self.render_timeout_seconds = render_timeout_seconds
        self.runner = runner or BoundedWorkerSubprocessRunner()
        self._identities: IdentityBundle | None = None
        self._manifest_sha256: str | None = None
        self._package_manifest: dict[str, Any] | None = None

    def _checked(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> WorkerCommandResult:
        result = self.runner.run(arguments, timeout_seconds=timeout_seconds)
        if result.returncode != 0:
            raise WorkerError("GPU or Blender readiness command failed")
        return result

    def inspect(self, package_manifest: dict[str, Any]) -> RuntimeInfo:
        identities, _ = validate_bridge_against_remote(
            package_manifest, self.remote_package
        )
        required_version = str(package_manifest.get("blenderVersion", ""))
        if not required_version.startswith("5.2"):
            raise WorkerError("production cloud worker requires Blender 5.2")
        version_result = self._checked(
            [str(self.blender_executable), "--version"], timeout_seconds=60
        )
        first_line = next(
            (line.strip() for line in version_result.stdout.splitlines() if line.strip()),
            "",
        )
        actual_version = first_line.removeprefix("Blender ").strip()
        if actual_version != required_version:
            raise WorkerError("Blender version differs from the bridged package")
        nvidia = self._checked(
            [
                self.nvidia_smi_executable,
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            timeout_seconds=30,
        )
        gpu_line = next((line.strip() for line in nvidia.stdout.splitlines() if line.strip()), "")
        gpu_name = gpu_line.split(",", maxsplit=1)[0].strip()
        if not gpu_name or "nvidia" not in gpu_name.casefold():
            raise WorkerError("nvidia-smi did not expose an NVIDIA GPU")
        probe = self._checked(
            [
                str(self.blender_executable),
                "--background",
                "--factory-startup",
                "--python-expr",
                _GPU_PROBE,
            ],
            timeout_seconds=120,
        )
        probe_line = next(
            (line for line in probe.stdout.splitlines() if line.startswith(_GPU_MARKER)),
            "",
        )
        if not probe_line:
            raise WorkerError("Blender did not report its graphics renderer")
        try:
            graphics = json.loads(probe_line.removeprefix(_GPU_MARKER))
        except json.JSONDecodeError as exc:
            raise WorkerError("Blender graphics renderer report is invalid") from exc
        renderer_text = " ".join(
            str(graphics.get(key, ""))
            for key in ("backend", "device", "vendor", "renderer")
        ).casefold()
        if "nvidia" not in renderer_text or any(
            marker in renderer_text for marker in _SOFTWARE_RENDERERS
        ):
            raise WorkerError("Blender is not using a verified NVIDIA graphics renderer")
        self._identities = identities
        self._manifest_sha256 = canonical_sha256(package_manifest)
        self._package_manifest = cast(
            dict[str, Any], json.loads(canonical_json_bytes(package_manifest))
        )
        return RuntimeInfo(required_version, gpu_name, True, False, identities)

    def render(
        self,
        lease: ChunkLease,
        output_directory: Path,
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
    ) -> list[RenderedFrame]:
        if (
            self._identities != lease.identities
            or self._manifest_sha256 is None
            or self._package_manifest is None
        ):
            raise WorkerError("production runtime was not inspected for this package")
        if lease.manifest_sha256 != self._manifest_sha256:
            raise WorkerError("production lease is not bound to this exact cloud manifest")
        if lease.worker_id != self.worker_id:
            raise WorkerError("production lease belongs to another worker identity")
        live_identities, _ = validate_bridge_against_remote(
            self._package_manifest, self.remote_package
        )
        if live_identities != lease.identities:
            raise WorkerError("sanitized package identity changed after worker inspection")
        if cancelled():
            raise WorkerCancelled("render cancelled before Blender started")
        remote_manifest_path = self.remote_package / "package-manifest.json"
        try:
            remote_manifest = json.loads(
                remote_manifest_path.read_text(encoding="utf-8-sig")
            )
            scene_relative = safe_relative_key(str(remote_manifest["scene"]["relativePath"]))
            profile_relative = safe_relative_key(
                str(remote_manifest["profile"]["relativePath"])
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise WorkerError("sanitized package render paths are invalid") from exc
        scene = self.remote_package / Path(*scene_relative.split("/"))
        profile = self.remote_package / Path(*profile_relative.split("/"))
        helper = self.remote_package / "blender" / "render_remote_chunk.py"
        if any(
            not path.is_file() or path.is_symlink()
            for path in (scene, profile, helper, remote_manifest_path)
        ):
            raise WorkerError("sanitized package render inputs are missing or unsafe")
        frames_root = output_directory / "frames"
        frames_root.mkdir(parents=True, exist_ok=False)
        arguments = [
            str(self.blender_executable),
            "--background",
            str(scene),
            "--python-exit-code",
            "1",
            "--python",
            str(helper),
            "--",
            "--profile",
            str(profile),
            "--package-manifest",
            str(remote_manifest_path),
            "--output",
            str(frames_root),
            "--start",
            str(lease.frame_range.start),
            "--end",
            str(lease.frame_range.end),
        ]

        def pulse() -> None:
            progress(lease.frame_range.start)

        result = self.runner.run(
            arguments,
            timeout_seconds=self.render_timeout_seconds,
            cwd=self.remote_package,
            pulse=pulse,
            cancelled=cancelled,
        )
        if result.returncode != 0:
            raise WorkerError(f"bounded Blender worker exited with code {result.returncode}")
        rendered: list[RenderedFrame] = []
        frames_root = frames_root.resolve(strict=True)
        for frame in range(lease.frame_range.start, lease.frame_range.end + 1):
            expected_name = f"frame_{frame:06d}.png"
            path = (frames_root / expected_name).resolve(strict=True)
            try:
                path.relative_to(frames_root)
            except ValueError as exc:
                raise WorkerError("packaged worker frame escaped its output directory") from exc
            if path.is_symlink():
                raise WorkerError("packaged worker frame must not be a symbolic link")
            header = validate_png(path)
            progress(frame)
            rendered.append(
                RenderedFrame(
                    frame,
                    path,
                    header.width,
                    header.height,
                    header.bit_depth,
                    header.image_format,
                )
            )
        expected_names = {f"frame_{frame:06d}.png" for frame in range(lease.frame_range.start, lease.frame_range.end + 1)}
        actual_names = {path.name for path in frames_root.iterdir() if path.is_file()}
        if actual_names != expected_names:
            raise WorkerError("packaged worker returned an unexpected frame set")
        return sorted(rendered, key=lambda item: item.frame)

    def shutdown(self, _reason: str) -> None:
        return None

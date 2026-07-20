from __future__ import annotations

import argparse
import json
import math
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from typing import Any, BinaryIO, Callable, Mapping, Protocol, Sequence

sys.dont_write_bytecode = True

PACKAGE_ROOT_DEFAULT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT_DEFAULT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT_DEFAULT))

from tools.final_render_tooling import ToolingError, _validate_exr, _validate_png, load_render_profile, sha256_file  # noqa: E402
from tools.remote_render_tooling import validate_package  # noqa: E402

DEFAULT_RENDER_TIMEOUT_SECONDS = 21_600.0
MAX_RENDER_TIMEOUT_SECONDS = 86_400.0
DEFAULT_MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 64 * 1024 * 1024
_TRUNCATION_MARKER = b"\n[output truncated]\n"


class RenderCommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        stdout_path: Path,
        stderr_path: Path,
        environment: Mapping[str, str],
        max_log_bytes: int,
    ) -> int: ...


class _BoundedCapture:
    def __init__(self) -> None:
        self.data = bytearray()
        self.truncated = False


def _validate_render_limits(timeout_seconds: float, max_log_bytes: int) -> None:
    if (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or not 1 <= timeout_seconds <= MAX_RENDER_TIMEOUT_SECONDS
    ):
        raise ToolingError(
            "invalid-render-timeout",
            "Render timeout must be finite and between 1 second and 24 hours.",
        )
    if (
        isinstance(max_log_bytes, bool)
        or not 1024 <= max_log_bytes <= MAX_LOG_BYTES
    ):
        raise ToolingError(
            "invalid-render-log-limit",
            "Per-stream render log limit must be between 1 KiB and 64 MiB.",
        )


def _drain_bounded(stream: BinaryIO, capture: _BoundedCapture, maximum: int) -> None:
    while chunk := stream.read(65_536):
        remaining = maximum - len(capture.data)
        if remaining > 0:
            capture.data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            capture.truncated = True


def _write_bounded_log(path: Path, capture: _BoundedCapture, maximum: int) -> None:
    content = bytes(capture.data)
    if capture.truncated and maximum >= len(_TRUNCATION_MARKER):
        content = content[: maximum - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
    path.write_bytes(content[:maximum])


def _terminate_process_tree(process: Any) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
    else:
        kill_process_group = getattr(os, "killpg", None)
        kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        try:
            if not callable(kill_process_group):
                raise OSError("process-group termination is unavailable")
            kill_process_group(process.pid, kill_signal)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


class BoundedRenderCommandRunner:
    def __init__(
        self,
        *,
        process_factory: Callable[..., Any] | None = None,
        tree_terminator: Callable[[Any], None] | None = None,
    ) -> None:
        self._process_factory = process_factory or subprocess.Popen
        self._tree_terminator = tree_terminator or _terminate_process_tree

    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        stdout_path: Path,
        stderr_path: Path,
        environment: Mapping[str, str],
        max_log_bytes: int,
    ) -> int:
        _validate_render_limits(timeout_seconds, max_log_bytes)
        if not command or any(not isinstance(item, str) or "\x00" in item for item in command):
            raise ToolingError(
                "invalid-render-command", "Blender command must be a safe argument array."
            )
        creation_flags = (
            int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            if os.name == "nt"
            else 0
        )
        try:
            process = self._process_factory(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=dict(environment),
                shell=False,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )
        except (OSError, ValueError) as exc:
            raise ToolingError(
                "worker-render-start-failed", "Blender could not be started safely."
            ) from exc
        if process.stdout is None or process.stderr is None:
            self._tree_terminator(process)
            raise ToolingError(
                "worker-render-start-failed", "Blender output pipes were unavailable."
            )
        stdout_capture = _BoundedCapture()
        stderr_capture = _BoundedCapture()
        readers = (
            Thread(
                target=_drain_bounded,
                args=(process.stdout, stdout_capture, max_log_bytes),
                daemon=True,
            ),
            Thread(
                target=_drain_bounded,
                args=(process.stderr, stderr_capture, max_log_bytes),
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            return_code = int(process.wait(timeout=timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            self._tree_terminator(process)
            return_code = -1
        except BaseException:
            self._tree_terminator(process)
            raise
        finally:
            for reader in readers:
                reader.join(timeout=5)
            _write_bounded_log(stdout_path, stdout_capture, max_log_bytes)
            _write_bounded_log(stderr_path, stderr_capture, max_log_bytes)
        if timed_out:
            raise ToolingError(
                "worker-render-timeout",
                "Blender exceeded the bounded render timeout; its process tree was terminated.",
            )
        return return_code


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _blender_version(blender: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="trackprompt-blender-version-") as temporary:
        root = Path(temporary)
        stdout_path = root / "stdout.log"
        return_code = BoundedRenderCommandRunner().run(
            [str(blender), "--version"],
            timeout_seconds=60,
            stdout_path=stdout_path,
            stderr_path=root / "stderr.log",
            environment=os.environ,
            max_log_bytes=65_536,
        )
        if return_code != 0:
            raise ToolingError("blender-version-failed", "Blender did not report its version.")
        lines = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        raise ToolingError("blender-version-failed", "Blender did not report its version.")
    return lines[0].removeprefix("Blender ").strip()


def _gpu_metadata() -> dict[str, Any]:
    try:
        with tempfile.TemporaryDirectory(prefix="trackprompt-nvidia-smi-") as temporary:
            stdout_path = Path(temporary) / "stdout.log"
            return_code = BoundedRenderCommandRunner().run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,driver_version,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                timeout_seconds=30,
                stdout_path=stdout_path,
                stderr_path=Path(temporary) / "stderr.log",
                environment=os.environ,
                max_log_bytes=65_536,
            )
            lines = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if return_code == 0 and lines:
            parts = [item.strip() for item in lines[0].split(",")]
            return {"model": parts[0], "driverVersion": parts[1], "vramMiB": float(parts[2])}
    except (OSError, IndexError, ValueError, ToolingError):
        pass
    return {"model": "unknown", "driverVersion": "unknown", "vramMiB": None}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render one verified provider-neutral TrackPrompt frame assignment.")
    parser.add_argument("--package-directory", default=str(PACKAGE_ROOT_DEFAULT))
    parser.add_argument("--blender", required=True)
    parser.add_argument("--chunk-id")
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--worker-id", default=platform.node() or "remote-worker")
    parser.add_argument("--output-directory")
    parser.add_argument(
        "--render-timeout-seconds",
        type=float,
        default=DEFAULT_RENDER_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--max-log-bytes",
        type=int,
        default=DEFAULT_MAX_LOG_BYTES,
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    render_runner: RenderCommandRunner | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        _validate_render_limits(args.render_timeout_seconds, args.max_log_bytes)
        package_root = Path(args.package_directory).resolve(strict=True)
        validation = validate_package(package_root)
        if not validation["ok"]:
            raise ToolingError("invalid-package", "Package validation failed before rendering.")
        manifest = json.loads((package_root / "package-manifest.json").read_text(encoding="utf-8-sig"))
        chunk_plan = json.loads((package_root / "chunk-manifest.json").read_text(encoding="utf-8-sig"))
        if args.chunk_id:
            matching = [item for item in chunk_plan.get("chunks", []) if item.get("chunkId") == args.chunk_id]
            if len(matching) != 1:
                raise ToolingError("unknown-chunk", "Chunk ID is absent or ambiguous in the package plan.")
            start, end, chunk_id = int(matching[0]["startFrame"]), int(matching[0]["endFrame"]), str(args.chunk_id)
        else:
            if args.start is None or args.end is None:
                raise ToolingError("missing-range", "Supply either --chunk-id or both --start and --end.")
            start, end = int(args.start), int(args.end)
            chunk_id = f"chunk-{start:06d}-{end:06d}"
        contract = manifest["frameContract"]
        if start < int(contract["frameStart"]) or end > int(contract["frameEnd"]) or end < start:
            raise ToolingError("range-outside-contract", "Worker range is outside the package frame contract.")
        blender = Path(args.blender).resolve(strict=True)
        version = _blender_version(blender)
        if version != manifest["blenderVersion"]:
            raise ToolingError("blender-version-mismatch", f"Package requires Blender {manifest['blenderVersion']}; worker has {version}.")
        output_root = Path(args.output_directory).resolve() if args.output_directory else package_root.parent / "worker-returns" / f"{chunk_id}-{args.worker_id}"
        if output_root.exists() and any(output_root.iterdir()):
            raise ToolingError("worker-output-not-empty", "Worker return directory must be new and empty.")
        frames = output_root / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        scene = package_root / manifest["scene"]["relativePath"]
        profile_path = package_root / manifest["profile"]["relativePath"]
        helper = package_root / "blender" / "render_remote_chunk.py"
        command = [
            str(blender),
            "--background",
            str(scene),
            "--python-exit-code",
            "1",
            "--python",
            str(helper),
            "--",
            "--profile",
            str(profile_path),
            "--package-manifest",
            str(package_root / "package-manifest.json"),
            "--output",
            str(frames),
            "--start",
            str(start),
            "--end",
            str(end),
        ]
        started = time.perf_counter()
        stdout_path = output_root / "worker.stdout.log"
        stderr_path = output_root / "worker.stderr.log"
        child_environment = os.environ.copy()
        child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
        runner = render_runner or BoundedRenderCommandRunner()
        return_code = runner.run(
            command,
            timeout_seconds=args.render_timeout_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            environment=child_environment,
            max_log_bytes=args.max_log_bytes,
        )
        if return_code != 0:
            raise ToolingError(
                "worker-render-failed",
                f"Blender exited with code {return_code}; bounded logs were preserved.",
            )
        profile = load_render_profile(profile_path)
        frame_records = []
        for frame in range(start, end + 1):
            path = frames / profile.image.filename(frame)
            if not path.is_file() or path.stat().st_size == 0:
                raise ToolingError("worker-frame-missing", f"Assigned frame {frame} is missing.")
            validator = _validate_png if profile.image.format == "PNG" else _validate_exr
            width, height, bit_depth, _, _ = validator(path)
            if (width, height, bit_depth) != (profile.width, profile.height, profile.image.bit_depth):
                raise ToolingError("worker-frame-contract-mismatch", f"Assigned frame {frame} has the wrong image contract.")
            frame_records.append({"frame": frame, "fileName": path.name, "sizeBytes": path.stat().st_size, "sha256": sha256_file(path)})
        worker_manifest = {
            "schemaVersion": "1.0.0",
            "kind": "trackprompt-remote-worker-return",
            "completedAt": datetime.now(UTC).isoformat(),
            "workerId": args.worker_id,
            "chunkId": chunk_id,
            "packageId": manifest["packageId"],
            "packageSha256": manifest["packageSha256"],
            "sceneSha256": manifest["scene"]["sha256"],
            "profileSha256": manifest["profile"]["sha256"],
            "sourceProductionProfileSha256": manifest["profile"]["sourceProductionProfileSha256"],
            "blenderVersion": version,
            "gpu": _gpu_metadata(),
            "startFrame": start,
            "endFrame": end,
            "expectedFrameCount": end - start + 1,
            "wallSeconds": time.perf_counter() - started,
            "privateAudioUsed": False,
            "encodingPerformed": False,
            "frames": frame_records,
        }
        _atomic_json(output_root / "worker-manifest.json", worker_manifest)
        print(json.dumps({"ok": True, "returnDirectory": str(output_root), "frameCount": len(frame_records)}))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired, ToolingError) as exc:
        code = exc.code if isinstance(exc, ToolingError) else "worker-error"
        print(json.dumps({"ok": False, "error": {"code": code, "message": str(exc)[:500]}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

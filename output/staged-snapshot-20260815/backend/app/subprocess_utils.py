from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


class ProcessTimedOut(RuntimeError):
    pass


class ProcessWasCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_exceeded: bool
    stderr_exceeded: bool


def _bounded_drain(stream: BinaryIO, limit: int, target: bytearray, exceeded: list[bool]) -> None:
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            return
        remaining = max(0, limit - len(target))
        if remaining:
            target.extend(chunk[:remaining])
        if len(chunk) > remaining:
            exceeded[0] = True


def _posix_descendants(root_pid: int) -> set[int]:
    if not Path("/proc").is_dir():
        try:
            result = subprocess.run(
                ["ps", "-A", "-o", "pid=,ppid="],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=2,
                check=False,
            )
            relationships = {
                int(parts[0]): int(parts[1])
                for line in result.stdout.decode("ascii", errors="ignore").splitlines()
                if len(parts := line.split()) == 2
            }
        except (OSError, subprocess.SubprocessError, ValueError):
            return set()
        ps_descendants: set[int] = set()
        pending = [root_pid]
        while pending:
            parent = pending.pop()
            children = {
                pid for pid, parent_pid in relationships.items() if parent_pid == parent
            }
            new_children = children - ps_descendants
            ps_descendants.update(new_children)
            pending.extend(new_children)
        return ps_descendants

    descendants: set[int] = set()
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        children_file = Path(f"/proc/{parent}/task/{parent}/children")
        try:
            children = {
                int(value)
                for value in children_file.read_text(encoding="ascii").split()
            }
        except (OSError, ValueError):
            children = set()
        new_children = children - descendants
        descendants.update(new_children)
        pending.extend(new_children)
    return descendants


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=5,
                check=False,
            )
            if result.returncode != 0 or process.poll() is None:
                with suppress(OSError):
                    process.kill()
        except (OSError, subprocess.SubprocessError):
            with suppress(OSError):
                process.kill()
        return

    get_process_group: Any = getattr(os, "getpgid", None)
    kill_process_group: Any = getattr(os, "killpg", None)
    kill_signal = int(getattr(signal, "SIGKILL", 9))
    root_group: int | None = None
    with suppress(OSError):
        if callable(get_process_group):
            root_group = int(get_process_group(process.pid))
    descendant_groups: set[int] = set()
    for pid in _posix_descendants(process.pid):
        with suppress(OSError):
            if callable(get_process_group):
                descendant_groups.add(int(get_process_group(pid)))
    for group in descendant_groups:
        if group != root_group:
            with suppress(OSError):
                if callable(kill_process_group):
                    kill_process_group(group, kill_signal)
    if root_group is not None:
        with suppress(OSError):
            if callable(kill_process_group):
                kill_process_group(root_group, kill_signal)
    else:
        with suppress(OSError):
            process.kill()
    if process.poll() is None:
        with suppress(OSError):
            process.kill()


def run_process_bounded(
    args: Sequence[str],
    *,
    timeout_seconds: float,
    cancel_requested: Callable[[], bool] | None = None,
    environment: Mapping[str, str] | None = None,
    capture_stdout: bool = True,
    stdout_limit: int = 1024 * 1024,
    stderr_limit: int = 64 * 1024,
) -> BoundedProcessResult:
    """Run a shell-free subprocess while draining output into fixed-size buffers."""
    process = subprocess.Popen(
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        shell=False,
        env=dict(environment) if environment is not None else None,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        start_new_session=os.name != "nt",
    )
    stdout = bytearray()
    stderr = bytearray()
    stdout_exceeded = [False]
    stderr_exceeded = [False]
    threads: list[threading.Thread] = []
    if process.stdout is not None:
        threads.append(
            threading.Thread(
                target=_bounded_drain,
                args=(process.stdout, stdout_limit, stdout, stdout_exceeded),
                daemon=True,
            )
        )
    if process.stderr is not None:
        threads.append(
            threading.Thread(
                target=_bounded_drain,
                args=(process.stderr, stderr_limit, stderr, stderr_exceeded),
                daemon=True,
            )
        )
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout_seconds
    try:
        while process.poll() is None:
            if stdout_exceeded[0] or stderr_exceeded[0]:
                _kill_process_tree(process)
                break
            if cancel_requested is not None and cancel_requested():
                _kill_process_tree(process)
                raise ProcessWasCancelled
            if time.monotonic() >= deadline:
                _kill_process_tree(process)
                raise ProcessTimedOut
            time.sleep(0.05)
    finally:
        if process.poll() is None:
            _kill_process_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Do not let cleanup defeat the caller's configured hard
                # timeout. The OS-level tree kill has already been attempted.
                process.returncode = -int(getattr(signal, "SIGKILL", 9))
        for thread in threads:
            thread.join(timeout=2)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    return BoundedProcessResult(
        returncode=int(process.returncode),
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        stdout_exceeded=stdout_exceeded[0],
        stderr_exceeded=stderr_exceeded[0],
    )

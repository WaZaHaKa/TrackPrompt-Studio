from __future__ import annotations

import io
import subprocess
from collections.abc import Sequence

import pytest

import cloud_render.providers.brev as brev_module
from cloud_render.providers import ProviderCommandError, SubprocessRunner


class FakeProcess:
    def __init__(self, wait_outcomes: Sequence[int | None]) -> None:
        self.pid = 4242
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.wait_outcomes = list(wait_outcomes)
        self.wait_timeouts: list[float | None] = []
        self.sent_signals: list[int] = []
        self.kill_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if not self.wait_outcomes:
            raise AssertionError("unexpected process wait")
        outcome = self.wait_outcomes.pop(0)
        if outcome is None:
            raise subprocess.TimeoutExpired(cmd=("brev",), timeout=timeout)
        return outcome

    def send_signal(self, signal_number: int) -> None:
        self.sent_signals.append(signal_number)

    def kill(self) -> None:
        self.kill_calls += 1


@pytest.mark.parametrize("is_windows", [False, True])
def test_spawn_process_creates_an_isolated_process_group(
    monkeypatch: pytest.MonkeyPatch, is_windows: bool
) -> None:
    sentinel = object()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(args: list[str], **options: object) -> object:
        calls.append((args, options))
        return sentinel

    monkeypatch.setattr(brev_module, "_IS_WINDOWS", is_windows)
    monkeypatch.setattr(brev_module.subprocess, "Popen", fake_popen)

    assert brev_module._spawn_process(("brev", "--version")) is sentinel

    args, options = calls.pop()
    assert args == ["brev", "--version"]
    assert options["shell"] is False
    assert options["text"] is False
    if is_windows:
        assert options["creationflags"] == brev_module._WINDOWS_CREATE_NEW_PROCESS_GROUP
        assert "start_new_session" not in options
    else:
        assert options["start_new_session"] is True
        assert "creationflags" not in options


def test_posix_timeout_terminates_then_kills_the_full_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess((None, -9))
    signals: list[tuple[int, int]] = []

    def fake_killpg(process_group_id: int, signal_number: int) -> None:
        signals.append((process_group_id, signal_number))

    monkeypatch.setattr(brev_module, "_IS_WINDOWS", False)
    monkeypatch.setattr(brev_module.os, "killpg", fake_killpg, raising=False)

    brev_module._terminate_process_tree(process)

    assert signals == [
        (process.pid, brev_module._POSIX_SIGTERM),
        (process.pid, brev_module._POSIX_SIGKILL),
    ]
    assert process.wait_timeouts == [
        brev_module._TERMINATION_GRACE_SECONDS,
        brev_module._FORCE_KILL_SECONDS,
    ]
    assert process.kill_calls == 0


def test_posix_force_kills_group_even_when_parent_exits_after_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess((0,))
    signals: list[tuple[int, int]] = []

    def fake_killpg(process_group_id: int, signal_number: int) -> None:
        signals.append((process_group_id, signal_number))

    monkeypatch.setattr(brev_module.os, "killpg", fake_killpg, raising=False)

    brev_module._terminate_posix_process_tree(process)

    assert signals == [
        (process.pid, brev_module._POSIX_SIGTERM),
        (process.pid, brev_module._POSIX_SIGKILL),
    ]
    assert process.wait_timeouts == [brev_module._TERMINATION_GRACE_SECONDS]


def test_windows_timeout_breaks_group_then_uses_bounded_tree_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess((None, -1))
    tree_kills: list[int] = []

    monkeypatch.setattr(brev_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        brev_module, "_run_windows_tree_kill", lambda process_id: tree_kills.append(process_id)
    )

    brev_module._terminate_process_tree(process)

    assert process.sent_signals == [brev_module._WINDOWS_CTRL_BREAK_EVENT]
    assert tree_kills == [process.pid]
    assert process.wait_timeouts == [
        brev_module._TERMINATION_GRACE_SECONDS,
        brev_module._FORCE_KILL_SECONDS,
    ]
    assert process.kill_calls == 0


def test_windows_force_kills_tree_even_when_parent_exits_after_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess((0,))
    tree_kills: list[int] = []
    monkeypatch.setattr(
        brev_module, "_run_windows_tree_kill", lambda process_id: tree_kills.append(process_id)
    )

    brev_module._terminate_windows_process_tree(process)

    assert process.sent_signals == [brev_module._WINDOWS_CTRL_BREAK_EVENT]
    assert tree_kills == [process.pid]
    assert process.wait_timeouts == [brev_module._TERMINATION_GRACE_SECONDS]


def test_windows_taskkill_targets_one_pid_tree_with_a_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(args: list[str], **options: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, options))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(brev_module.subprocess, "run", fake_run)

    brev_module._run_windows_tree_kill(4242)

    args, options = calls.pop()
    assert args == ["taskkill.exe", "/PID", "4242", "/T", "/F"]
    assert options["shell"] is False
    assert options["check"] is False
    assert options["timeout"] == brev_module._FORCE_KILL_SECONDS


def test_tree_cleanup_never_uses_an_unbounded_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess((None, None, None))
    monkeypatch.setattr(brev_module, "_run_windows_tree_kill", lambda _process_id: None)

    brev_module._terminate_windows_process_tree(process)

    assert process.wait_timeouts == [
        brev_module._TERMINATION_GRACE_SECONDS,
        brev_module._FORCE_KILL_SECONDS,
        brev_module._FINAL_REAP_SECONDS,
    ]
    assert all(timeout is not None for timeout in process.wait_timeouts)
    assert process.kill_calls == 1


def test_runner_timeout_uses_tree_cleanup_and_preserves_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess((None,))
    cleaned: list[FakeProcess] = []
    monkeypatch.setattr(brev_module, "_spawn_process", lambda _args: process)
    monkeypatch.setattr(
        brev_module, "_terminate_process_tree", lambda child: cleaned.append(child)
    )

    with pytest.raises(ProviderCommandError, match="bounded timeout"):
        SubprocessRunner().run(("brev", "--version"), timeout_seconds=0.1)

    assert cleaned == [process]
    assert process.wait_timeouts == [0.1]

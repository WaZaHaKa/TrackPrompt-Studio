"""Bounded, read-only Windows worker diagnosis; private evidence stays local.

Run --help for the operator command. This is an observer, never a controller.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import ctypes as C
from ctypes import wintypes as W
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import threading
import time
import tempfile
from typing import Any
import unittest
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]
INCIDENTS = REPO / ".trackprompt-data" / "wnp-incidents"
INVALID_HANDLE = C.c_void_p(-1).value


def start_stamp(ticks: int) -> str:
    seconds, fraction = divmod(ticks - 116444736000000000, 10000000)
    stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction:07d}Z"


def binding_matches(row: dict[str, Any], pid: int, start: str) -> bool:
    return row.get("pid") == pid and row.get("started_utc") == start


def public_process(row: dict[str, Any]) -> dict[str, Any]:
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return public_process(value)
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return {
        key: clean(value) for key, value in row.items() if not key.endswith("_private")
    }


def decode_x64_syscall_stub(code: bytes) -> int | None:
    # Read only known local ntdll stubs; never follow hooks or execute extracted bytes.
    if (
        code.startswith(bytes.fromhex("4c8bd1b8"))
        and bytes.fromhex("0f05") in code[8:32]
    ):
        return int.from_bytes(code[4:8], "little")
    return None


def oldest_proven_thread(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows or any("creation_filetime" not in row for row in rows):
        return None
    return min(rows, key=lambda row: row["creation_filetime"])


def compare_standard_handles(
    own: dict[str, int],
    expected: dict[str, int],
    target_reader: Callable[[], dict[str, Any]],
    first_argument: int,
) -> dict[str, Any]:
    names = {"stdin", "stdout", "stderr"}
    verified = (
        set(own) == names
        and set(expected) == names
        and own == expected
        and any(0 < value < 2**64 - 1 for value in own.values())
    )
    if not verified:
        return {
            "status": "UNKNOWN",
            "own_layout_verified": False,
            "reason": "own_PEB_standard_handles_do_not_match_GetStdHandle",
        }
    target = target_reader()
    if target.get("status") != "observed":
        return target | {"own_layout_verified": True}
    handles = target["handles_private"]
    return target | {
        "own_layout_verified": True,
        "firstArgMatchesStdin": first_argument == handles["stdin"],
        "firstArgMatchesStdout": first_argument == handles["stdout"],
        "firstArgMatchesStderr": first_argument == handles["stderr"],
    }


def write_new(path: Path, payload: dict[str, Any]) -> None:
    # Every audit filename is new; historical run/receipt files are never opened for write.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=True)


class PrivateProgress:
    """Append-only local journal; each boundary is durable before its native call."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sequence = 0
        with path.open("x", encoding="utf-8"):
            pass

    def __call__(self, boundary: str, details: dict[str, Any]) -> None:
        self.sequence += 1
        event = {
            "sequence": self.sequence,
            "sampled_utc": datetime.now(UTC).isoformat(),
            "boundary": boundary,
            "details_private": details,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def read_small(path: Path) -> tuple[bytes, dict[str, Any]]:
    with path.open("rb") as stream:
        value = stream.read(2 * 1024 * 1024 + 1)
    if len(value) > 2 * 1024 * 1024:
        raise ValueError("Evidence file exceeds 2 MiB limit")
    obj = json.loads(value)
    if not isinstance(obj, dict):
        raise ValueError("Evidence must be an object")
    return value, obj


class ThreadEntry(C.Structure):
    _fields_ = [
        ("size", W.DWORD),
        ("usage", W.DWORD),
        ("tid", W.DWORD),
        ("pid", W.DWORD),
        ("priority", W.LONG),
        ("delta", W.LONG),
        ("flags", W.DWORD),
    ]


class ProcessEntry(C.Structure):
    _fields_ = [
        ("size", W.DWORD),
        ("usage", W.DWORD),
        ("pid", W.DWORD),
        ("heap", C.c_size_t),
        ("module", W.DWORD),
        ("threads", W.DWORD),
        ("parent", W.DWORD),
        ("priority", W.LONG),
        ("flags", W.DWORD),
        ("exe", W.WCHAR * 260),
    ]


class UnicodeString(C.Structure):
    _fields_ = [("length", W.USHORT), ("maximum", W.USHORT), ("buffer", C.c_void_p)]


class LockNode(C.Structure):
    _fields_ = [("name", W.WCHAR * 128), ("timeout", C.c_int64), ("alertable", W.BOOL)]


class ThreadNode(C.Structure):
    _fields_ = [
        ("pid", W.DWORD),
        ("tid", W.DWORD),
        ("wait_ms", W.DWORD),
        ("context_switches", W.DWORD),
    ]


class NodeUnion(C.Union):
    _fields_ = [("lock", LockNode), ("thread", ThreadNode)]


class WaitNode(C.Structure):
    _fields_ = [("kind", C.c_int), ("status", C.c_int), ("value", NodeUnion)]


class Selection(C.Structure):
    _fields_ = [
        ("flags", W.DWORD),
        ("anchor_x", W.SHORT),
        ("anchor_y", W.SHORT),
        ("left", W.SHORT),
        ("top", W.SHORT),
        ("right", W.SHORT),
        ("bottom", W.SHORT),
    ]


class Luid(C.Structure):
    _fields_ = [("low", W.DWORD), ("high", W.LONG)]


class Privilege(C.Structure):
    _fields_ = [("luid", Luid), ("attributes", W.DWORD)]


class OnePrivilege(C.Structure):
    _fields_ = [("count", W.DWORD), ("entry", Privilege)]


class LastSystemCall(C.Structure):
    # THREAD_LAST_SYSCALL_INFORMATION, x64 layout (phnt ntpsapi.h).
    _fields_ = [
        ("first_argument", C.c_void_p),
        ("number", W.USHORT),
        ("wait_time", C.c_uint64),
    ]


class ProcessBasicInformation(C.Structure):
    _fields_ = [
        ("reserved1", C.c_void_p),
        ("peb", C.c_void_p),
        ("reserved2", C.c_void_p * 2),
        ("pid", C.c_size_t),
        ("reserved3", C.c_void_p),
    ]


class Native:
    def __init__(self) -> None:
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.a = C.WinDLL("advapi32", use_last_error=True)
        self.n = C.WinDLL("ntdll", use_last_error=True)
        self.configure(self.k, "OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
        self.configure(self.k, "CloseHandle", [W.HANDLE], W.BOOL)
        self.configure(
            self.k, "GetProcessTimes", [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL
        )
        self.configure(self.k, "CreateToolhelp32Snapshot", [W.DWORD, W.DWORD], W.HANDLE)
        for prefix, row in [("Thread32", ThreadEntry), ("Process32", ProcessEntry)]:
            for suffix in ("First", "Next"):
                self.configure(
                    self.k,
                    prefix + suffix + ("W" if prefix == "Process32" else ""),
                    [W.HANDLE, C.POINTER(row)],
                    W.BOOL,
                )
        self.configure(
            self.a, "OpenProcessToken", [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)], W.BOOL
        )
        self.configure(
            self.a,
            "GetTokenInformation",
            [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)],
            W.BOOL,
        )
        self.configure(
            self.n,
            "NtQueryInformationProcess",
            [W.HANDLE, C.c_int, C.c_void_p, W.ULONG, C.POINTER(W.ULONG)],
            W.LONG,
        )

    @staticmethod
    def configure(dll: Any, name: str, args: list[Any], result: Any) -> None:
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, result

    def token(self, process: Any) -> dict[str, Any]:
        handle = W.HANDLE()
        if not self.a.OpenProcessToken(process, 0x0008, C.byref(handle)):
            return {"token_query_error": C.get_last_error()}
        result: dict[str, Any] = {}
        try:
            for name, kind in [
                ("elevated", 20),
                ("elevation_type", 18),
                ("integrity", 25),
            ]:
                size = W.DWORD()
                self.a.GetTokenInformation(handle, kind, None, 0, C.byref(size))
                buf = C.create_string_buffer(min(max(size.value, 4), 65536))
                if not self.a.GetTokenInformation(
                    handle, kind, buf, len(buf), C.byref(size)
                ):
                    result[name + "_query_error"] = C.get_last_error()
                    continue
                if kind == 25:
                    sid = C.cast(buf, C.POINTER(C.c_void_p))[0]
                    self.configure(
                        self.a,
                        "GetSidSubAuthorityCount",
                        [C.c_void_p],
                        C.POINTER(C.c_ubyte),
                    )
                    self.configure(
                        self.a,
                        "GetSidSubAuthority",
                        [C.c_void_p, W.DWORD],
                        C.POINTER(W.DWORD),
                    )
                    count = self.a.GetSidSubAuthorityCount(sid)[0]
                    result["integrity_rid"] = self.a.GetSidSubAuthority(sid, count - 1)[
                        0
                    ]
                else:
                    value = C.cast(buf, C.POINTER(W.DWORD))[0]
                    result[name] = bool(value) if kind == 20 else value
        finally:
            self.k.CloseHandle(handle)
        return result

    def command_line(self, handle: Any) -> dict[str, Any]:
        needed = W.ULONG()
        self.n.NtQueryInformationProcess(handle, 60, None, 0, C.byref(needed))
        if not 0 < needed.value <= 131072:
            return {
                "command_line_available": False,
                "command_line_required_bytes": needed.value,
            }
        buf = C.create_string_buffer(needed.value)
        status = self.n.NtQueryInformationProcess(
            handle, 60, buf, len(buf), C.byref(needed)
        )
        if status < 0:
            return {
                "command_line_available": False,
                "command_line_ntstatus": f"0x{status & 0xFFFFFFFF:08x}",
            }
        value = C.cast(buf, C.POINTER(UnicodeString)).contents
        start = C.addressof(buf)
        if (
            not value.buffer
            or value.length % 2
            or not start <= value.buffer <= start + len(buf) - value.length
        ):
            return {
                "command_line_available": False,
                "command_line_bounds_invalid": True,
            }
        command = C.wstring_at(value.buffer, value.length // 2)
        return {
            "command_line_available": bool(command),
            "command_line_private": command,
        }

    def debug_privilege(self, *, enable: bool = False) -> dict[str, Any]:
        """Inspect, or explicitly enable, SeDebugPrivilege on this helper token only."""
        self.configure(
            self.a,
            "LookupPrivilegeValueW",
            [W.LPCWSTR, W.LPCWSTR, C.POINTER(Luid)],
            W.BOOL,
        )
        self.configure(
            self.a,
            "AdjustTokenPrivileges",
            [
                W.HANDLE,
                W.BOOL,
                C.POINTER(OnePrivilege),
                W.DWORD,
                C.c_void_p,
                C.c_void_p,
            ],
            W.BOOL,
        )
        token = W.HANDLE()
        if not self.a.OpenProcessToken(
            W.HANDLE(-1), 0x0008 | (0x0020 if enable else 0), C.byref(token)
        ):
            return {"query_error": C.get_last_error(), "enable_requested": enable}
        try:
            luid = Luid()
            if not self.a.LookupPrivilegeValueW(
                None, "SeDebugPrivilege", C.byref(luid)
            ):
                return {"lookup_error": C.get_last_error()}
            needed = W.DWORD()
            self.a.GetTokenInformation(token, 3, None, 0, C.byref(needed))
            if not 4 <= needed.value <= 65536:
                return {"privileges_size_invalid": True}
            data = C.create_string_buffer(needed.value)
            if not self.a.GetTokenInformation(
                token, 3, data, len(data), C.byref(needed)
            ):
                return {"query_error": C.get_last_error()}
            count = C.cast(data, C.POINTER(W.DWORD))[0]
            if count > (len(data) - 4) // C.sizeof(Privilege):
                return {"privileges_bounds_invalid": True}
            entries = C.cast(C.addressof(data) + 4, C.POINTER(Privilege))
            matches = [
                entries[i]
                for i in range(count)
                if entries[i].luid.low == luid.low and entries[i].luid.high == luid.high
            ]
            result = {
                "present": bool(matches),
                "enabled_before": bool(matches and matches[0].attributes & 2),
                "enable_requested": enable,
            }
            if enable and matches:
                request = OnePrivilege(1, Privilege(luid, 2))
                C.set_last_error(0)
                ok = self.a.AdjustTokenPrivileges(
                    token, False, C.byref(request), 0, None, None
                )
                error = C.get_last_error()
                result.update(
                    enabled_for_helper=bool(ok and error == 0), adjust_error=error
                )
            return result
        finally:
            self.k.CloseHandle(token)

    def process(self, pid: int) -> dict[str, Any]:
        row: dict[str, Any] = {"pid": pid}
        handle = self.k.OpenProcess(
            0x1000, False, pid
        )  # QUERY_LIMITED_INFORMATION only
        if not handle:
            return row | {"query_error": C.get_last_error()}
        try:
            times = [W.FILETIME() for _ in range(4)]
            if self.k.GetProcessTimes(handle, *(C.byref(value) for value in times)):
                ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
                row["started_utc"] = start_stamp(ticks)
                row["creation_filetime"] = ticks
            else:
                row["times_query_error"] = C.get_last_error()
            row.update(self.token(handle))
            row.update(self.command_line(handle))
        finally:
            self.k.CloseHandle(handle)
        return row

    def snapshot(self) -> tuple[dict[int, dict[str, Any]], dict[int, list[int]]]:
        handle = self.k.CreateToolhelp32Snapshot(0x00000002 | 0x00000004, 0)
        if handle == INVALID_HANDLE:
            raise OSError(C.get_last_error(), "Toolhelp snapshot unavailable")
        processes: dict[int, dict[str, Any]] = {}
        threads: dict[int, list[int]] = {}
        try:
            entry = ProcessEntry()
            entry.size = C.sizeof(entry)
            ok = self.k.Process32FirstW(handle, C.byref(entry))
            while ok:
                processes[entry.pid] = {
                    "pid": entry.pid,
                    "parent_pid": entry.parent,
                    "image_basename_private": entry.exe,
                }
                ok = self.k.Process32NextW(handle, C.byref(entry))
            thread = ThreadEntry()
            thread.size = C.sizeof(thread)
            ok = self.k.Thread32First(handle, C.byref(thread))
            while ok:
                threads.setdefault(thread.pid, []).append(thread.tid)
                ok = self.k.Thread32Next(handle, C.byref(thread))
        finally:
            self.k.CloseHandle(handle)
        return processes, threads

    def oldest_thread_native(
        self,
        pid: int,
        tids: list[int],
        progress: Callable[[str, dict[str, Any]], None] | None = None,
        expected_start_utc: str | None = None,
    ) -> dict[str, Any]:
        """One native syscall observation of the oldest proven live thread; never suspend."""
        emit = progress or (lambda _boundary, _details: None)
        result: dict[str, Any] = {
            "thread_times": [],
            "status": "UNKNOWN",
            "no_stack_collected": True,
            "last_syscall_is_not_by_itself_a_current_wait_proof": True,
        }
        if C.sizeof(C.c_void_p) != 8 or platform.machine().upper() not in (
            "AMD64",
            "X86_64",
        ):
            return result | {"reason": "requires_native_x64_helper"}
        if len(tids) > 128:
            return result | {"reason": "thread_limit_exceeded"}
        self.configure(self.k, "OpenThread", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
        self.configure(self.k, "GetProcessIdOfThread", [W.HANDLE], W.DWORD)
        self.configure(
            self.k, "GetThreadTimes", [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL
        )
        self.configure(
            self.n,
            "NtQueryInformationThread",
            [W.HANDLE, C.c_int, C.c_void_p, W.ULONG, C.POINTER(W.ULONG)],
            W.LONG,
        )
        for tid in tids:
            row: dict[str, Any] = {"tid": tid}
            emit("before_OpenThread_creation_query", row)
            thread = self.k.OpenThread(0x0800, False, tid)
            if not thread:
                row["query_error"] = C.get_last_error()
            else:
                try:
                    emit("before_GetProcessIdOfThread", row)
                    if self.k.GetProcessIdOfThread(thread) != pid:
                        row["owner_mismatch"] = True
                    else:
                        times = [W.FILETIME() for _ in range(4)]
                        emit("before_GetThreadTimes", row)
                        if self.k.GetThreadTimes(
                            thread, *(C.byref(value) for value in times)
                        ):
                            ticks = (times[0].dwHighDateTime << 32) | times[
                                0
                            ].dwLowDateTime
                            row.update(
                                creation_filetime=ticks, started_utc=start_stamp(ticks)
                            )
                        else:
                            row["times_error"] = C.get_last_error()
                finally:
                    emit("before_CloseHandle_creation_thread", row)
                    self.k.CloseHandle(thread)
            result["thread_times"].append(row)
        emit("thread_times_complete", result)
        oldest = oldest_proven_thread(result["thread_times"])
        if oldest is None:
            return result | {"reason": "oldest_live_thread_not_proven"}
        result["oldest_live_thread"] = oldest
        emit("before_OpenThread_syscall_query", oldest)
        thread = self.k.OpenThread(0x0040 | 0x0008, False, oldest["tid"])
        if not thread:
            return result | {"query_error": C.get_last_error()}
        try:
            times = [W.FILETIME() for _ in range(4)]
            emit("before_thread_identity_revalidation", oldest)
            if self.k.GetProcessIdOfThread(thread) != pid or not self.k.GetThreadTimes(
                thread, *(C.byref(value) for value in times)
            ):
                return result | {"reason": "thread_identity_revalidation_failed"}
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            if ticks != oldest["creation_filetime"]:
                return result | {"reason": "thread_creation_changed"}
            syscall = LastSystemCall()
            returned = W.ULONG()
            emit("before_NtQueryInformationThread_ThreadLastSystemCall", result)
            status = self.n.NtQueryInformationThread(
                thread, 21, C.byref(syscall), C.sizeof(syscall), C.byref(returned)
            )
            result.update(
                ntstatus=f"0x{status & 0xFFFFFFFF:08x}", returned_bytes=returned.value
            )
            if status < 0:
                emit("after_NtQueryInformationThread_ThreadLastSystemCall", result)
                return result
            result.update(
                status="observed",
                syscall_number=syscall.number,
                wait_time_raw=syscall.wait_time,
                first_argument_private=syscall.first_argument,
            )
            emit("after_NtQueryInformationThread_ThreadLastSystemCall", result)
            known_names = [
                "NtWriteFile",
                "NtReadFile",
                "NtDeviceIoControlFile",
                "NtFsControlFile",
                "NtWaitForSingleObject",
                "NtWaitForMultipleObjects",
                "NtDelayExecution",
                "NtAlpcSendWaitReceivePort",
                "NtRemoveIoCompletion",
                "NtWaitForWorkViaWorkerFactory",
            ]
            matches = []
            for name in known_names:
                fn = getattr(self.n, name, None)
                if fn is not None:
                    stub = C.string_at(C.cast(fn, C.c_void_p).value, 32)
                    if decode_x64_syscall_stub(stub) == syscall.number:
                        matches.append(name)
            result["syscall_name_candidates"] = matches
            result["mapping_scope"] = (
                "10 locally loaded x64 ntdll exports; unmatched is UNKNOWN"
            )
            emit("last_syscall_mapping_complete", result)
            if (
                len(matches) == 1
                and matches[0] in known_names[:4]
                and syscall.first_argument
            ):
                result["first_argument_standard_handles"] = (
                    self.standard_handle_matches(
                        pid,
                        syscall.first_argument,
                        expected_start_utc=expected_start_utc,
                        progress=emit,
                    )
                )
            emit("oldest_thread_native_complete", result)
            return result
        finally:
            emit("before_CloseHandle_syscall_thread", {"tid": oldest["tid"]})
            self.k.CloseHandle(thread)

    def read_standard_handles(
        self,
        pid: int,
        *,
        expected_start_utc: str | None = None,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Read only 40 PEB bytes and 56 process-parameter bytes on native x64."""
        emit = progress or (lambda _boundary, _details: None)
        if C.sizeof(C.c_void_p) != 8 or platform.machine().upper() not in (
            "AMD64",
            "X86_64",
        ):
            return {"status": "UNKNOWN", "reason": "requires_native_x64_helper"}
        self.configure(
            self.k,
            "ReadProcessMemory",
            [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)],
            W.BOOL,
        )
        self.configure(self.k, "IsWow64Process", [W.HANDLE, C.POINTER(W.BOOL)], W.BOOL)
        emit("before_OpenProcess_standard_handle_read", {"pid": pid})
        process = self.k.OpenProcess(
            0x0400 | 0x0010, False, pid
        )  # QUERY_INFORMATION | VM_READ only
        if not process:
            return {"status": "UNKNOWN", "process_open_error": C.get_last_error()}
        try:
            emit("before_standard_handle_process_identity", {"pid": pid})
            times = [W.FILETIME() for _ in range(4)]
            if not self.k.GetProcessTimes(
                process, *(C.byref(value) for value in times)
            ):
                return {"status": "UNKNOWN", "times_error": C.get_last_error()}
            started = start_stamp(
                (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            )
            if expected_start_utc is not None and started != expected_start_utc:
                return {"status": "UNKNOWN", "reason": "process_creation_mismatch"}
            wow64 = W.BOOL()
            if not self.k.IsWow64Process(process, C.byref(wow64)) or wow64.value:
                return {"status": "UNKNOWN", "reason": "target_not_proven_native_x64"}
            basic, returned = ProcessBasicInformation(), W.ULONG()
            emit(
                "before_NtQueryInformationProcess_ProcessBasicInformation", {"pid": pid}
            )
            status = self.n.NtQueryInformationProcess(
                process, 0, C.byref(basic), C.sizeof(basic), C.byref(returned)
            )
            emit(
                "after_NtQueryInformationProcess_ProcessBasicInformation",
                {
                    "pid": pid,
                    "ntstatus": f"0x{status & 0xFFFFFFFF:08x}",
                    "returned_bytes": returned.value,
                },
            )
            if (
                status < 0
                or basic.pid != pid
                or not basic.peb
                or returned.value != C.sizeof(basic)
            ):
                return {
                    "status": "UNKNOWN",
                    "reason": "process_basic_information_unproven",
                }

            def read_fixed(address: int, count: int, boundary: str) -> bytes | None:
                buffer, read = C.create_string_buffer(count), C.c_size_t()
                emit("before_" + boundary, {"pid": pid, "bytes": count})
                ok = self.k.ReadProcessMemory(
                    process, address, buffer, count, C.byref(read)
                )
                error = C.get_last_error() if not ok else 0
                emit(
                    "after_" + boundary,
                    {"pid": pid, "bytes_read": read.value, "winerror": error},
                )
                return buffer.raw if ok and read.value == count else None

            peb = read_fixed(basic.peb, 0x28, "ReadProcessMemory_PEB_40_bytes")
            if peb is None:
                return {"status": "UNKNOWN", "reason": "peb_fixed_read_failed"}
            params_address = int.from_bytes(peb[0x20:0x28], "little")
            if params_address == 0 or params_address % 8:
                return {
                    "status": "UNKNOWN",
                    "reason": "process_parameters_pointer_invalid",
                }
            params = read_fixed(
                params_address, 0x38, "ReadProcessMemory_parameters_56_bytes"
            )
            if params is None:
                return {"status": "UNKNOWN", "reason": "parameters_fixed_read_failed"}
            handles = {
                name: int.from_bytes(params[offset : offset + 8], "little")
                for name, offset in [
                    ("stdin", 0x20),
                    ("stdout", 0x28),
                    ("stderr", 0x30),
                ]
            }
            result = {
                "status": "observed",
                "handles_private": handles,
                "started_utc": started,
                "read_bytes": 96,
                "target_native_x64": True,
            }
            emit("standard_handles_read_complete", result)
            return result
        finally:
            self.k.CloseHandle(process)

    def standard_handle_matches(
        self,
        pid: int,
        first_argument: int,
        *,
        expected_start_utc: str | None = None,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        emit = progress or (lambda _boundary, _details: None)
        self.configure(self.k, "GetStdHandle", [W.DWORD], W.HANDLE)
        own = self.read_standard_handles(os.getpid(), progress=emit)
        native = {
            name: int(self.k.GetStdHandle(code & 0xFFFFFFFF) or 0)
            for name, code in [("stdin", -10), ("stdout", -11), ("stderr", -12)]
        }
        emit(
            "own_standard_handle_layout_validation",
            {"own_private": own, "native_handles_private": native},
        )
        result = compare_standard_handles(
            own.get("handles_private", {}),
            native,
            lambda: self.read_standard_handles(
                pid, expected_start_utc=expected_start_utc, progress=emit
            ),
            first_argument,
        )
        emit("standard_handle_comparison_complete", result)
        return result

    def wait_chains(self, tids: list[int]) -> dict[str, Any]:
        # Async WCT needs no debugger attach, stack suspension, or injected code.
        callback_type = C.WINFUNCTYPE(
            None,
            W.HANDLE,
            C.c_size_t,
            W.DWORD,
            C.POINTER(W.DWORD),
            C.POINTER(WaitNode),
            C.POINTER(W.BOOL),
        )
        self.configure(
            self.a, "OpenThreadWaitChainSession", [W.DWORD, callback_type], W.HANDLE
        )
        self.configure(self.a, "CloseThreadWaitChainSession", [W.HANDLE], None)
        self.configure(
            self.a,
            "GetThreadWaitChain",
            [
                W.HANDLE,
                C.c_size_t,
                W.DWORD,
                W.DWORD,
                C.POINTER(W.DWORD),
                C.POINTER(WaitNode),
                C.POINTER(W.BOOL),
            ],
            W.BOOL,
        )
        results: list[dict[str, Any]] = []
        types = {
            1: "critical_section",
            2: "send_message",
            3: "mutex",
            4: "alpc",
            5: "com",
            6: "thread_wait",
            7: "process_wait",
            8: "thread",
            9: "com_activation",
            10: "unknown",
        }
        statuses = {
            1: "no_access",
            2: "running",
            3: "blocked",
            4: "pid_only",
            5: "pid_only_rpcss",
            6: "owned",
            7: "not_owned",
            8: "abandoned",
            9: "unknown",
            10: "error",
        }
        deadline = time.monotonic() + 8
        for tid in tids[:32]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done = threading.Event()
            row: dict[str, Any] = {"tid": tid}

            def callback(
                _session: Any,
                _context: int,
                status: int,
                count: Any,
                nodes: Any,
                cycle: Any,
            ) -> None:
                row["winerror"] = status
                if status in (0, 234) and count and nodes:
                    row["cycle"] = bool(cycle[0]) if cycle else None
                    row["nodes"] = []
                    for i in range(min(count[0], 16)):
                        node = nodes[i]
                        item: dict[str, Any] = {
                            "kind": types.get(node.kind, str(node.kind)),
                            "status": statuses.get(node.status, str(node.status)),
                        }
                        if node.kind == 8:
                            item.update(
                                pid=node.value.thread.pid,
                                tid=node.value.thread.tid,
                                wait_ms=node.value.thread.wait_ms,
                                context_switches=node.value.thread.context_switches,
                            )
                        else:
                            # Even kernel object names can reveal private paths; never print them.
                            item["object_name_present"] = bool(node.value.lock.name)
                        row["nodes"].append(item)
                done.set()

            callback_ref = callback_type(callback)
            session = self.a.OpenThreadWaitChainSession(1, callback_ref)
            if not session:
                return {"open_error": C.get_last_error(), "threads": results}
            count, cycle = W.DWORD(16), W.BOOL()
            nodes = (WaitNode * 16)()
            try:
                C.set_last_error(0)
                ok = self.a.GetThreadWaitChain(
                    session, 0, 1, tid, C.byref(count), nodes, C.byref(cycle)
                )
                error = C.get_last_error()
                if not ok and error != 997:
                    row["winerror"] = error
                elif not done.wait(min(remaining, 2)):
                    row["timed_out"] = True
            finally:
                # Cancels outstanding async requests and waits for callbacks to return.
                self.a.CloseThreadWaitChainSession(session)
            results.append(row)
        return {
            "threads": results,
            "thread_count": len(tids),
            "sampled_count": len(results),
            "limited_to_32_threads_and_8_seconds": True,
            "one_node_is_inconclusive_for_unsupported_waits": True,
        }

    def console(self, pid: int) -> dict[str, Any]:
        self.configure(
            self.k, "GetConsoleProcessList", [C.POINTER(W.DWORD), W.DWORD], W.DWORD
        )
        self.configure(self.k, "AttachConsole", [W.DWORD], W.BOOL)
        self.configure(self.k, "FreeConsole", [], W.BOOL)
        self.configure(
            self.k, "GetConsoleSelectionInfo", [C.POINTER(Selection)], W.BOOL
        )
        self.configure(
            self.k,
            "CreateFileW",
            [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE],
            W.HANDLE,
        )
        self.configure(self.k, "GetConsoleMode", [W.HANDLE, C.POINTER(W.DWORD)], W.BOOL)
        ids = (W.DWORD * 256)()
        C.set_last_error(0)
        before = self.k.GetConsoleProcessList(ids, len(ids))
        result: dict[str, Any] = {
            "caller_console_process_count_before": before,
            "caller_console_query_error_before": C.get_last_error()
            if not before
            else 0,
        }
        if before:
            before_pids = list(ids)[: min(before, len(ids))]
            result["caller_console_pids_before"] = before_pids
            # The venv executable's launcher can create a fresh helper-only console
            # despite DETACHED_PROCESS. Detach just this helper, never the worker.
            if before_pids != [os.getpid()]:
                return result | {"status": "refused_nonexclusive_helper_console"}
            if not self.k.FreeConsole():
                return result | {
                    "status": "helper_detach_failed",
                    "detach_error": C.get_last_error(),
                }
            result["detached_helper_only_console"] = True
            after = self.k.GetConsoleProcessList(ids, len(ids))
            result["caller_console_process_count_after_detach"] = after
            if after:
                return result | {"status": "refused_caller_still_has_console"}
        if not self.k.AttachConsole(pid):
            return result | {
                "status": "attach_failed",
                "attach_error": C.get_last_error(),
            }
        try:
            result["status"] = "attached_for_read_only_query"
            count = self.k.GetConsoleProcessList(ids, len(ids))
            result["console_pids"] = list(ids)[: min(count, len(ids))]
            info = Selection()
            if self.k.GetConsoleSelectionInfo(C.byref(info)):
                result.update(
                    selection_flags=info.flags,
                    selection_in_progress=bool(info.flags & 1),
                    selection_not_empty=bool(info.flags & 2),
                    mouse_selection=bool(info.flags & 4),
                    mouse_down=bool(info.flags & 8),
                )
            else:
                result["selection_query_error"] = C.get_last_error()
            handle = self.k.CreateFileW("CONIN$", 0x80000000, 3, None, 3, 0, None)
            if handle != INVALID_HANDLE:
                try:
                    mode = W.DWORD()
                    if self.k.GetConsoleMode(handle, C.byref(mode)):
                        result.update(
                            input_mode=mode.value,
                            quick_edit_enabled=bool(mode.value & 0x40),
                        )
                    else:
                        result["mode_query_error"] = C.get_last_error()
                finally:
                    self.k.CloseHandle(handle)
            else:
                result["console_input_open_error"] = C.get_last_error()
        finally:
            # Detach only this fresh helper. No input, selection, window, or console mode is changed.
            self.k.FreeConsole()
        return result


def watchdog(seconds: float, output: Path) -> threading.Timer:
    def stop_helper() -> None:
        try:
            write_new(
                output / f"helper-timeout-{os.getpid()}.json",
                {
                    "timed_out": True,
                    "helper_pid": os.getpid(),
                    "target_processes_stopped": 0,
                },
            )
        finally:
            os._exit(124)  # Exit this diagnostic helper only; never terminate a target.

    timer = threading.Timer(seconds, stop_helper)
    timer.daemon = True
    timer.start()
    return timer


def manifest_binding(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"[a-f0-9]{12}", args.run_id):
        raise ValueError("Expected a 12-character run identifier")
    path = REPO / ".trackprompt-data" / "hayale-v21" / "runs" / args.run_id
    data, _manifest = read_small(path / "run.json")
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.run_manifest_sha256:
        raise ValueError("Run manifest hash mismatch; refusing target probes")
    _, provider = read_small(path / "comfy" / "server-process.json")
    return path, {
        "run_id": args.run_id,
        "run_manifest_sha256": digest,
        "provider_pid": provider["pid"],
        "provider_command_private": provider.get("command", []),
    }


def capture(args: argparse.Namespace) -> int:
    run, binding = manifest_binding(args)
    output = INCIDENTS / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-worker-" + uuid4().hex[:8]
    )
    output.mkdir(parents=True, exist_ok=False)
    timer = watchdog(40, output)
    try:
        native = Native()
        worker = native.process(args.worker_pid)
        write_new(output / "worker-private.json", worker)
        report: dict[str, Any] = {
            "schema": "hayale-worker-diagnostic/1",
            "observed_utc": datetime.now(UTC).isoformat(),
            "run": public_process(binding),
            "worker": public_process(worker),
            "caller": public_process(native.process(os.getpid())),
            "helper_debug_privilege": native.debug_privilege(),
            "jobs_submitted": 0,
            "target_processes_stopped": 0,
            "run_files_modified": False,
            "console_input_or_modes_changed": False,
            "root_cause": "UNKNOWN",
        }
        if not binding_matches(worker, args.worker_pid, args.expected_start_utc):
            report["status"] = "refused_worker_identity_unproven_or_mismatched"
            write_new(output / "SUMMARY.json", report)
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "audit_directory": str(output),
                        "worker": report["worker"],
                    }
                )
            )
            return 2
        processes, threads = native.snapshot()
        provider_pid = int(binding["provider_pid"])
        selected: set[int] = {args.worker_pid, provider_pid}
        parent = args.worker_pid
        for _ in range(8):
            parent = processes.get(parent, {}).get("parent_pid", 0)
            if not parent or parent in selected:
                break
            selected.add(parent)
        selected.update(
            pid for pid, item in processes.items() if item["parent_pid"] == provider_pid
        )
        rows = [
            processes.get(pid, {"pid": pid}) | native.process(pid)
            for pid in sorted(selected)
        ]
        write_new(output / "process-lineage-private.json", {"processes": rows})
        report["processes"] = [public_process(row) for row in rows]
        provider_row = next(row for row in rows if row["pid"] == provider_pid)
        expected_command = binding["provider_command_private"]
        actual_command = str(provider_row.get("command_line_private", ""))
        # Native parsing preserves Windows argument boundaries without invoking a shell.
        command_match: bool | None = None
        if actual_command and isinstance(expected_command, list):
            shell = C.WinDLL("shell32", use_last_error=True)
            Native.configure(
                shell,
                "CommandLineToArgvW",
                [W.LPCWSTR, C.POINTER(C.c_int)],
                C.POINTER(W.LPWSTR),
            )
            Native.configure(native.k, "LocalFree", [W.HLOCAL], W.HLOCAL)
            count = C.c_int()
            argv = shell.CommandLineToArgvW(actual_command, C.byref(count))
            if argv:
                try:
                    command_match = [
                        argv[i] for i in range(count.value)
                    ] == expected_command
                finally:
                    native.k.LocalFree(C.cast(argv, W.HLOCAL))
        report["run_process_binding"] = {
            "expected_worker_pid_start_match": True,
            "saved_provider_is_direct_worker_child": provider_row.get("parent_pid")
            == args.worker_pid,
            "saved_provider_argv_matches_live": command_match,
            "worker_command_contains_run_id": args.run_id
            in str(worker.get("command_line_private", ""))
            if worker.get("command_line_available")
            else None,
        }
        lineage_match = (
            provider_row.get("parent_pid") == args.worker_pid and command_match is True
        )
        if (
            not lineage_match
            and not report["run_process_binding"]["worker_command_contains_run_id"]
        ):
            report["status"] = "refused_run_process_association_unproven"
        else:
            report["status"] = "collected"
            write_new(
                output / "binding.json",
                public_process(binding)
                | {
                    "worker_pid": args.worker_pid,
                    "expected_start_utc": args.expected_start_utc,
                },
            )
            if args.enable_helper_debug_privilege:
                report["helper_debug_privilege"] = native.debug_privilege(enable=True)
            if args.query_oldest_thread_syscall:
                progress = PrivateProgress(
                    output / "oldest-thread-native-progress-private.jsonl"
                )
                progress(
                    "optional_native_probe_started", {"worker_pid": args.worker_pid}
                )
                native_thread = native.oldest_thread_native(
                    args.worker_pid,
                    threads.get(args.worker_pid, []),
                    progress=progress,
                    expected_start_utc=args.expected_start_utc,
                )
                write_new(output / "oldest-thread-native-private.json", native_thread)
                report["oldest_thread_native"] = public_process(native_thread)
            report["wait_chains"] = native.wait_chains(threads.get(args.worker_pid, []))
            # DETACHED_PROCESS ensures no pre-existing console. The child's own watchdog bounds it.
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--console-probe",
                    str(output),
                    "--worker-pid",
                    str(args.worker_pid),
                    "--expected-start-utc",
                    args.expected_start_utc,
                    "--run-id",
                    args.run_id,
                    "--run-manifest-sha256",
                    args.run_manifest_sha256,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS,
                close_fds=True,
                shell=False,
            )
            try:
                report["console_helper_exit"] = child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                report["console_helper_wait_expired"] = True
            if (output / "console.json").is_file():
                _, report["console"] = read_small(output / "console.json")
            report["worker_identity_still_matches"] = binding_matches(
                native.process(args.worker_pid),
                args.worker_pid,
                args.expected_start_utc,
            )
        report["limitations"] = [
            "No Python or native stack was collected; WCT is not a stack trace.",
            "A WCT single-node result can mean a running thread or an unsupported wait.",
            "Console selection and QuickEdit flags alone do not prove a blocked stdout write.",
            "Query denial does not by itself prove target elevation; compare token evidence.",
            "All observations are point-in-time; this helper never repairs or resumes the run.",
        ]
        write_new(output / "SUMMARY.json", report)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "audit_directory": str(output),
                    "run_process_binding": report["run_process_binding"],
                    "console": report.get("console"),
                    "wait_chains": report.get("wait_chains"),
                    "worker": report["worker"],
                    "caller": report["caller"],
                }
            )
        )
        return 0 if report["status"] == "collected" else 2
    finally:
        timer.cancel()


def console_probe(args: argparse.Namespace) -> int:
    output = Path(args.console_probe).resolve()
    if output.parent != INCIDENTS.resolve() or not output.is_dir():
        raise ValueError(
            "Internal helper audit directory is outside the private incident root"
        )
    timer = watchdog(8, output)
    try:
        manifest_binding(args)
        _, saved_binding = read_small(output / "binding.json")
        if (
            saved_binding.get("worker_pid") != args.worker_pid
            or saved_binding.get("expected_start_utc") != args.expected_start_utc
            or saved_binding.get("run_id") != args.run_id
            or saved_binding.get("run_manifest_sha256") != args.run_manifest_sha256
        ):
            raise ValueError("Internal helper binding mismatch")
        native = Native()
        before = native.process(args.worker_pid)
        if not binding_matches(before, args.worker_pid, args.expected_start_utc):
            result = {"status": "refused_worker_identity"}
        else:
            result = native.console(args.worker_pid)
            result["identity_matches_after"] = binding_matches(
                native.process(args.worker_pid),
                args.worker_pid,
                args.expected_start_utc,
            )
        write_new(output / "console.json", result)
        return 0
    finally:
        timer.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Requires exact 7-digit UTC process creation time and SHA-256 of the existing run.json. "
            "Run from an operator-owned elevated shell only if normal read-only access is denied. "
            "This tool never requests UAC, installs tools, submits work, "
            "changes console selection, suspends threads, or terminates any existing process. "
            "Private audit files may contain complete command lines; do not upload them."
        ),
    )
    parser.add_argument("--worker-pid", type=int)
    parser.add_argument("--expected-start-utc")
    parser.add_argument("--run-id")
    parser.add_argument("--run-manifest-sha256")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--query-oldest-thread-syscall",
        action="store_true",
        help="Read thread creation times and one last syscall; compare its handle to stdin/stdout/stderr using a self-validated x64 PEB layout",
    )
    parser.add_argument(
        "--enable-helper-debug-privilege",
        action="store_true",
        help="Explicitly enable existing SeDebugPrivilege on this diagnostic helper token only, "
        "for native wait-chain reads from an operator-owned Administrator shell",
    )
    parser.add_argument("--console-probe", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if os.name != "nt":
        parser.error("Windows is required")
    if not all(
        [
            args.worker_pid,
            args.expected_start_utc,
            args.run_id,
            args.run_manifest_sha256,
        ]
    ):
        parser.error(
            "worker PID, exact creation time, run ID and manifest hash are required"
        )
    if not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{7}Z", args.expected_start_utc
    ):
        parser.error("creation time must use YYYY-MM-DDTHH:MM:SS.fffffffZ")
    if not re.fullmatch(r"[a-f0-9]{64}", args.run_manifest_sha256):
        parser.error("manifest hash must be a lowercase SHA-256 digest")
    try:
        return console_probe(args) if args.console_probe else capture(args)
    except Exception as error:
        # Never emit exception strings, paths, command lines or traceback to shared logs.
        print(
            json.dumps(
                {
                    "status": "diagnostic_error",
                    "error_type": type(error).__name__,
                    "winerror": getattr(error, "winerror", None),
                }
            )
        )
        return 3


def self_test() -> int:
    class BindingTests(unittest.TestCase):
        def test_standard_handle_layout_mismatch_refuses_remote_read(self) -> None:
            calls: list[bool] = []
            result = compare_standard_handles(
                {"stdin": 1, "stdout": 2, "stderr": 3},
                {"stdin": 1, "stdout": 99, "stderr": 3},
                lambda: calls.append(True),
                2,
            )
            self.assertFalse(result["own_layout_verified"])
            self.assertEqual(calls, [])

        def test_standard_handle_match_after_layout_validation(self) -> None:
            own = {"stdin": 1, "stdout": 2, "stderr": 3}
            result = compare_standard_handles(
                own,
                own,
                lambda: {
                    "status": "observed",
                    "handles_private": {"stdin": 100, "stdout": 192, "stderr": 196},
                },
                192,
            )
            self.assertTrue(result["firstArgMatchesStdout"])
            self.assertFalse(result["firstArgMatchesStdin"])
            self.assertFalse(result["firstArgMatchesStderr"])

        def test_private_progress_survives_a_later_failed_operation(self) -> None:
            with tempfile.TemporaryDirectory(
                prefix="hayale-diagnostic-test-"
            ) as directory:
                path = Path(directory) / "private-progress.jsonl"
                progress = PrivateProgress(path)
                progress("syscall_complete", {"syscall_number": 7})
                progress("before_native_call", {"source_handle_private": 100})
                with self.assertRaises(RuntimeError):
                    raise RuntimeError("synthetic next-call failure")
                events = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual([event["sequence"] for event in events], [1, 2])
                self.assertEqual(events[-1]["boundary"], "before_native_call")
                self.assertEqual(events[0]["details_private"]["syscall_number"], 7)
                with self.assertRaises(FileExistsError):
                    PrivateProgress(path)

        def test_syscall_stub_requires_known_x64_shape(self) -> None:
            self.assertEqual(
                decode_x64_syscall_stub(bytes.fromhex("4c8bd1b8070000000f05c3")), 7
            )
            self.assertIsNone(
                decode_x64_syscall_stub(
                    bytes.fromhex("e9000000004c8bd1b8070000000f05c3")
                )
            )
            self.assertIsNone(
                decode_x64_syscall_stub(bytes.fromhex("4c8bd1b807000000"))
            )

        def test_oldest_thread_requires_complete_creation_evidence(self) -> None:
            rows = [
                {"tid": 8, "creation_filetime": 20},
                {"tid": 9, "creation_filetime": 10},
            ]
            self.assertEqual(oldest_proven_thread(rows)["tid"], 9)
            self.assertIsNone(
                oldest_proven_thread(rows + [{"tid": 7, "query_error": 5}])
            )

        def test_timestamp_precision(self) -> None:
            self.assertEqual(
                start_stamp(116444736012345678), "1970-01-01T00:00:01.2345678Z"
            )

        def test_pid_reuse_fails_closed(self) -> None:
            self.assertFalse(binding_matches({"pid": 5, "started_utc": "a"}, 5, "b"))

        def test_exact_identity(self) -> None:
            self.assertTrue(binding_matches({"pid": 5, "started_utc": "a"}, 5, "a"))

        def test_unavailable_identity_fails_closed(self) -> None:
            self.assertFalse(binding_matches({"pid": 5, "query_error": 5}, 5, "a"))

        def test_private_fields_removed(self) -> None:
            source = {
                "pid": 5,
                "command_line_private": "secret",
                "command_line_available": True,
            }
            self.assertNotIn("command_line_private", public_process(source))
            self.assertTrue(public_process(source)["command_line_available"])

    return (
        0
        if unittest.TextTestRunner(verbosity=2)
        .run(unittest.defaultTestLoader.loadTestsFromTestCase(BindingTests))
        .wasSuccessful()
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import ctypes
import os
from collections import defaultdict, deque
from collections.abc import Iterable
from ctypes import wintypes

_PROCESS_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_ERROR_ACCESS_DENIED = 5
_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * _MAX_PATH),
    ]


def process_is_alive(process_id: int | None) -> bool:
    """Probe a PID without ever signaling or terminating it."""
    if process_id is None or process_id <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_alive(process_id)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_is_alive(process_id: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single_object.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(_PROCESS_SYNCHRONIZE, False, process_id)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        status = int(wait_for_single_object(handle, 0))
        if status == _WAIT_TIMEOUT:
            return True
        if status == _WAIT_OBJECT_0:
            return False
        return True
    finally:
        close_handle(handle)


def find_descendant_process_id(
    supervisor_process_id: int,
    executable_names: Iterable[str],
) -> int | None:
    if os.name != "nt" or supervisor_process_id <= 0:
        return None
    names = {name.casefold() for name in executable_names}
    for process_id, _parent_process_id, executable_name in _windows_process_snapshot():
        if process_id == supervisor_process_id and executable_name.casefold() in names:
            return process_id
    candidate = _find_descendant_in_snapshot(
        _windows_process_snapshot(),
        supervisor_process_id,
        names,
    )
    return candidate if candidate is not None and process_is_alive(candidate) else None


def _find_descendant_in_snapshot(
    processes: Iterable[tuple[int, int, str]],
    supervisor_process_id: int,
    executable_names: Iterable[str],
) -> int | None:
    names = {name.casefold() for name in executable_names}
    children: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for process_id, parent_process_id, executable_name in processes:
        children[parent_process_id].append((process_id, executable_name))
    queue = deque([supervisor_process_id])
    visited = {supervisor_process_id}
    while queue:
        parent = queue.popleft()
        for process_id, executable_name in children.get(parent, []):
            if process_id in visited:
                continue
            if executable_name.casefold() in names:
                return process_id
            visited.add(process_id)
            queue.append(process_id)
    return None


def _windows_process_snapshot() -> list[tuple[int, int, str]]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W))
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(_TH32CS_SNAPPROCESS, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid_handle:
        return []
    processes: list[tuple[int, int, str]] = []
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        if not process_first(snapshot, ctypes.byref(entry)):
            return processes
        while True:
            processes.append(
                (
                    int(entry.th32ProcessID),
                    int(entry.th32ParentProcessID),
                    str(entry.szExeFile),
                )
            )
            entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
            if not process_next(snapshot, ctypes.byref(entry)):
                break
        return processes
    finally:
        close_handle(snapshot)

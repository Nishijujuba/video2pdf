"""Stable OS process and executable identity observations."""

from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


EXECUTION_IDENTITY_FIELDS = frozenset(
    {
        "pid",
        "process_creation_identity",
        "executable_path",
        "executable_file_identity",
        "parent_pid",
        "parent_process_creation_identity",
        "observation_sha256",
    }
)


def execution_identity_observation_sha256(
    identity: dict[str, Any],
) -> str:
    """Fingerprint every process-relation field except the fingerprint."""

    return hashlib.sha256(
        (
            json.dumps(
                {
                    key: value
                    for key, value in identity.items()
                    if key != "observation_sha256"
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


def execution_identity_is_complete(value: Any) -> bool:
    """Validate the closed, self-fingerprinted process identity shape."""

    if (
        not isinstance(value, dict)
        or set(value) != EXECUTION_IDENTITY_FIELDS
        or type(value.get("pid")) is not int
        or value["pid"] <= 0
        or not isinstance(value.get("process_creation_identity"), str)
        or not value["process_creation_identity"]
        or not isinstance(value.get("executable_path"), str)
        or not value["executable_path"]
        or type(value.get("parent_pid")) is not int
        or value["parent_pid"] <= 0
        or not isinstance(
            value.get("parent_process_creation_identity"),
            str,
        )
        or not value["parent_process_creation_identity"]
    ):
        return False
    executable = value.get("executable_file_identity")
    return (
        isinstance(executable, dict)
        and set(executable) == {"device", "inode", "size", "mtime_ns"}
        and all(type(item) is int and item >= 0 for item in executable.values())
        and isinstance(value.get("observation_sha256"), str)
        and value["observation_sha256"]
        == execution_identity_observation_sha256(value)
    )


def _windows_final_file_path(file_descriptor: int) -> Path | None:
    if os.name != "nt":
        return None
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(file_descriptor))
    required = get_final_path(handle, None, 0, 0)
    if required == 0:
        return None
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_final_path(handle, buffer, len(buffer), 0)
    if written == 0 or written >= len(buffer):
        return None
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _windows_process_details(
    pid: int,
) -> tuple[str, str, dict[str, int], int, str | None] | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    get_process_times.restype = wintypes.BOOL
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_exit_code.restype = wintypes.BOOL
    query_image = kernel32.QueryFullProcessImageNameW
    query_image.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query_image.restype = wintypes.BOOL
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    def creation_ticks(process_handle: Any) -> int | None:
        creation = wintypes.FILETIME()
        discarded = [wintypes.FILETIME() for _ in range(3)]
        if not get_process_times(
            process_handle,
            ctypes.byref(creation),
            *(ctypes.byref(value) for value in discarded),
        ):
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime

    def process_is_active(process_handle: Any) -> bool:
        exit_code = wintypes.DWORD()
        return bool(
            get_exit_code(process_handle, ctypes.byref(exit_code))
            and exit_code.value == 259
        )

    handle = open_process(0x1000, False, pid)
    if not handle:
        return None
    try:
        ticks = creation_ticks(handle)
        if ticks is None or not process_is_active(handle):
            return None
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not query_image(
            handle,
            0,
            buffer,
            ctypes.byref(capacity),
        ):
            return None
        executable = buffer.value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        snapshot = create_snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return None
        try:
            parent_pid = 0
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            found = process_first(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32ProcessID == pid:
                    parent_pid = int(entry.th32ParentProcessID)
                    break
                found = process_next(snapshot, ctypes.byref(entry))
        finally:
            close_handle(snapshot)
        if parent_pid <= 0:
            return None

        descriptor = os.open(
            executable,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0),
        )
        try:
            executable_stat = os.fstat(descriptor)
            executable_final_path = _windows_final_file_path(descriptor)
        finally:
            os.close(descriptor)
        if executable_final_path is None or (
            os.path.normcase(os.path.abspath(executable_final_path))
            != os.path.normcase(os.path.abspath(executable))
        ):
            return None

        parent_creation = None
        parent_handle = open_process(0x1000, False, parent_pid)
        if parent_handle:
            try:
                parent_ticks = creation_ticks(parent_handle)
                if parent_ticks is not None:
                    parent_creation = f"windows-filetime:{parent_ticks}"
            finally:
                close_handle(parent_handle)
        if creation_ticks(handle) != ticks or not process_is_active(handle):
            return None
        return (
            f"windows-filetime:{ticks}",
            executable,
            {
                "device": int(executable_stat.st_dev),
                "inode": int(executable_stat.st_ino),
                "size": int(executable_stat.st_size),
                "mtime_ns": int(executable_stat.st_mtime_ns),
            },
            parent_pid,
            parent_creation,
        )
    except OSError:
        return None
    finally:
        close_handle(handle)


def _linux_process_details(pid: int) -> tuple[str, str, int] | None:
    try:
        stat_record = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        command_end = stat_record.rfind(")")
        fields = stat_record[command_end + 2 :].split()
        executable = str(Path(f"/proc/{pid}/exe").resolve(strict=True))
    except (OSError, UnicodeError):
        return None
    if command_end < 0 or len(fields) <= 19:
        return None
    return f"linux-starttime:{fields[19]}", executable, int(fields[1])


def _file_identity(path: Path) -> dict[str, int] | None:
    try:
        identity = path.stat()
    except OSError:
        return None
    return {
        "device": int(identity.st_dev),
        "inode": int(identity.st_ino),
        "size": int(identity.st_size),
        "mtime_ns": int(identity.st_mtime_ns),
    }


def process_execution_identity(pid: int) -> dict[str, Any] | None:
    """Return PID, creation, executable-file, and parent-process identity."""

    if type(pid) is not int or pid <= 0:
        return None
    details = (
        _windows_process_details(pid)
        if os.name == "nt"
        else _linux_process_details(pid)
        if sys.platform.startswith("linux")
        else None
    )
    if details is None:
        return None
    if os.name == "nt":
        (
            creation_identity,
            executable_text,
            executable_identity,
            parent_pid,
            parent_creation_identity,
        ) = details
    else:
        creation_identity, executable_text, parent_pid = details
        executable_identity = _file_identity(
            Path(executable_text).resolve(strict=False)
        )
        parent_creation_identity = None
    executable = Path(executable_text).resolve(strict=False)
    if executable_identity is None:
        return None
    if (
        os.name != "nt"
        and parent_pid > 0
        and parent_pid != pid
    ):
        parent_details = (
            _windows_process_details(parent_pid)
            if os.name == "nt"
            else _linux_process_details(parent_pid)
            if sys.platform.startswith("linux")
            else None
        )
        if parent_details is not None:
            parent_creation_identity = parent_details[0]
    identity = {
        "pid": pid,
        "process_creation_identity": creation_identity,
        "executable_path": str(executable),
        "executable_file_identity": executable_identity,
        "parent_pid": parent_pid,
        "parent_process_creation_identity": parent_creation_identity,
    }
    identity["observation_sha256"] = execution_identity_observation_sha256(
        identity
    )
    return identity

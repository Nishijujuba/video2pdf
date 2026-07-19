from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, BinaryIO


COMMAND_SCHEMA_VERSION = "1.0.0"
STATUS_SCHEMA_VERSION = "1.0.0"
HEARTBEAT_INTERVAL_SECONDS = 29.0
STATUS_LOCK_FILENAME = ".status.lock"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="persisted_command.py")
    commands = parser.add_subparsers(dest="operation", required=True)

    start = commands.add_parser("start")
    start.add_argument("--task-name", required=True)
    start.add_argument("--cwd", type=Path)
    start.add_argument("target_command", nargs=argparse.REMAINDER)

    show = commands.add_parser("show")
    show.add_argument("--run-dir", required=True, type=Path)

    wait = commands.add_parser("wait")
    wait.add_argument("--run-dir", required=True, type=Path)
    wait.add_argument("--timeout-seconds", type=float)

    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--run-dir", required=True, type=Path)

    commands.add_parser("_supervise").add_argument(
        "--run-dir", required=True, type=Path
    )
    return parser


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextmanager
def _status_lock(run_dir: Path) -> Iterator[None]:
    lock_path = run_dir / STATUS_LOCK_FILENAME
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            flock = getattr(fcntl, "flock")
            lock_exclusive = getattr(fcntl, "LOCK_EX")
            lock_unlock = getattr(fcntl, "LOCK_UN")
            flock(handle.fileno(), lock_exclusive)
            try:
                yield
            finally:
                flock(handle.fileno(), lock_unlock)


def _write_status_atomic(run_dir: Path, status: dict[str, Any]) -> None:
    with _status_lock(run_dir):
        _write_json_atomic(run_dir / "status.json", status)


def _write_text_atomic(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _normalized_task_name(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in {" ", "_"} else "_"
        for character in value
    )
    normalized = re.sub(r" +", " ", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    normalized = normalized.strip(" _.")
    return normalized or "command"


def _timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _windows_process_observation(pid: int) -> tuple[str, str | None]:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_invalid_parameter = 87
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
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        if ctypes.get_last_error() == error_invalid_parameter:
            return "missing", None
        return "unknown", None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not get_process_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return "unknown", None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return "present", f"windows-filetime:{ticks}"
    finally:
        close_handle(handle)


def _process_observation(pid: int) -> tuple[str, str | None]:
    if pid <= 0:
        return "unknown", None
    if os.name == "nt":
        return _windows_process_observation(pid)
    if sys.platform.startswith("linux"):
        try:
            stat_record = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        except FileNotFoundError:
            return "missing", None
        except (OSError, UnicodeError):
            return "unknown", None
        command_end = stat_record.rfind(")")
        fields_after_command = stat_record[command_end + 2 :].split()
        if command_end < 0 or len(fields_after_command) <= 19:
            return "unknown", None
        return "present", f"linux-starttime:{fields_after_command[19]}"

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "missing", None
    except (PermissionError, OSError):
        return "unknown", None
    return "present", None


def _process_identity(pid: int) -> str | None:
    observation, creation_identity = _process_observation(pid)
    return creation_identity if observation == "present" else None


def _create_run_directory(project_root: Path, task_name: str) -> tuple[str, Path]:
    root = project_root / "待删除/long-running"
    root.mkdir(parents=True, exist_ok=True)
    normalized = _normalized_task_name(task_name)
    while True:
        run_id = str(uuid.uuid4())
        run_dir = root / f"{normalized}_{_timestamp()}_{run_id[:8]}"
        try:
            run_dir.mkdir()
        except FileExistsError:
            continue
        return run_id, run_dir


def _status(run_id: str, state: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema_name": "persisted-command-status",
        "schema_version": STATUS_SCHEMA_VERSION,
        "run_id": run_id,
        "state": state,
        "updated_at": _now(),
        **fields,
    }


def _log_sizes(run_dir: Path) -> dict[str, int]:
    return {
        "stdout": (run_dir / "stdout.log").stat().st_size,
        "stderr": (run_dir / "stderr.log").stat().st_size,
        "merged": (run_dir / "command.log").stat().st_size,
    }


def _execution_status(
    run_id: str,
    state: str,
    run_dir: Path,
    *,
    started_at: str,
    elapsed_seconds: float,
    supervisor_pid: int,
    supervisor_creation_identity: str | None,
    target_pid: int,
    target_creation_identity: str | None,
    latest_output_at: str | None,
    exit_code: int | None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    status = _status(
        run_id,
        state,
        started_at=started_at,
        elapsed_seconds=round(elapsed_seconds, 3),
        heartbeat_at=None,
        latest_output_at=latest_output_at,
        log_sizes=_log_sizes(run_dir),
        supervisor_pid=supervisor_pid,
        child_pid=target_pid,
        supervisor_identity={
            "pid": supervisor_pid,
            "process_creation_identity": supervisor_creation_identity,
        },
        target_identity={
            "pid": target_pid,
            "process_creation_identity": target_creation_identity,
        },
        exit_code=exit_code,
    )
    status["heartbeat_at"] = status["updated_at"]
    if finished_at is not None:
        status["finished_at"] = finished_at
    return status


def _start(args: argparse.Namespace, project_root: Path) -> dict[str, Any]:
    target_command = list(args.target_command)
    if target_command[:1] == ["--"]:
        target_command = target_command[1:]
    if not target_command:
        raise ValueError("start requires a target command after --")

    run_id, run_dir = _create_run_directory(project_root, args.task_name)
    working_directory = (args.cwd or project_root).resolve()
    created_at = _now()
    command_record = {
        "schema_name": "persisted-command",
        "schema_version": COMMAND_SCHEMA_VERSION,
        "run_id": run_id,
        "task_name": args.task_name,
        "normalized_task_name": _normalized_task_name(args.task_name),
        "created_at": created_at,
        "cwd": str(working_directory),
        "argv": target_command,
    }
    _write_json_atomic(run_dir / "command.json", command_record)
    for filename in ("stdout.log", "stderr.log", "command.log"):
        (run_dir / filename).touch(exist_ok=False)
    _write_status_atomic(
        run_dir,
        _status(
            run_id,
            "running",
            started_at=created_at,
            elapsed_seconds=0.0,
            heartbeat_at=None,
            latest_output_at=None,
            log_sizes=_log_sizes(run_dir),
            supervisor_pid=None,
            child_pid=None,
            supervisor_identity={
                "pid": None,
                "process_creation_identity": None,
            },
            target_identity={
                "pid": None,
                "process_creation_identity": None,
            },
            exit_code=None,
        ),
    )

    launcher = project_root / "scripts/persisted_command.py"
    supervisor_command = [
        sys.executable,
        "-X",
        "utf8",
        "-B",
        str(launcher),
        "_supervise",
        "--run-dir",
        str(run_dir),
    ]
    options: dict[str, Any] = {
        "cwd": project_root,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        options["start_new_session"] = True
    subprocess.Popen(supervisor_command, **options)
    return {"run_id": run_id, "run_dir": str(run_dir)}


def _copy_stream(
    source: BinaryIO,
    stream_name: str,
    events: queue.Queue[tuple[str, bytes] | None],
) -> None:
    while True:
        chunk = source.readline()
        if not chunk:
            break
        events.put((stream_name, chunk))
    events.put(None)


def _supervise(run_dir: Path) -> int:
    command_record = json.loads((run_dir / "command.json").read_text(encoding="utf-8"))
    run_id = command_record["run_id"]
    started_monotonic = time.monotonic()
    process = subprocess.Popen(
        command_record["argv"],
        cwd=command_record["cwd"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    supervisor_pid = os.getpid()
    supervisor_creation_identity = _process_identity(supervisor_pid)
    target_creation_identity = _process_identity(process.pid)
    latest_output_at = None
    _write_status_atomic(
        run_dir,
        _execution_status(
            run_id,
            "running",
            run_dir,
            started_at=command_record["created_at"],
            elapsed_seconds=time.monotonic() - started_monotonic,
            supervisor_pid=supervisor_pid,
            supervisor_creation_identity=supervisor_creation_identity,
            target_pid=process.pid,
            target_creation_identity=target_creation_identity,
            latest_output_at=latest_output_at,
            exit_code=None,
        ),
    )

    assert process.stdout is not None
    assert process.stderr is not None
    events: queue.Queue[tuple[str, bytes] | None] = queue.Queue()
    readers = [
        threading.Thread(
            target=_copy_stream,
            args=(process.stdout, "stdout", events),
            daemon=True,
        ),
        threading.Thread(
            target=_copy_stream,
            args=(process.stderr, "stderr", events),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    finished_readers = 0
    next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
    with (run_dir / "stdout.log").open("ab", buffering=0) as stdout_log, (
        run_dir / "stderr.log"
    ).open("ab", buffering=0) as stderr_log, (run_dir / "command.log").open(
        "ab", buffering=0
    ) as merged_log:
        stream_logs = {"stdout": stdout_log, "stderr": stderr_log}
        while finished_readers < len(readers) or process.poll() is None:
            heartbeat_due = False
            event_timeout = max(0.0, next_heartbeat - time.monotonic())
            if finished_readers == len(readers):
                event_timeout = min(event_timeout, 0.1)
            try:
                event = events.get(timeout=event_timeout)
            except queue.Empty:
                heartbeat_due = time.monotonic() >= next_heartbeat
            else:
                if event is None:
                    finished_readers += 1
                else:
                    stream_name, chunk = event
                    stream_logs[stream_name].write(chunk)
                    merged_log.write(f"[{stream_name}] ".encode("ascii") + chunk)
                    latest_output_at = _now()

            current_monotonic = time.monotonic()
            if heartbeat_due or current_monotonic >= next_heartbeat:
                _write_status_atomic(
                    run_dir,
                    _execution_status(
                        run_id,
                        "running",
                        run_dir,
                        started_at=command_record["created_at"],
                        elapsed_seconds=current_monotonic - started_monotonic,
                        supervisor_pid=supervisor_pid,
                        supervisor_creation_identity=supervisor_creation_identity,
                        target_pid=process.pid,
                        target_creation_identity=target_creation_identity,
                        latest_output_at=latest_output_at,
                        exit_code=None,
                    ),
                )
                while next_heartbeat <= current_monotonic:
                    next_heartbeat += HEARTBEAT_INTERVAL_SECONDS

    exit_code = process.wait()
    for reader in readers:
        reader.join()
    _write_text_atomic(run_dir / "exit-code.txt", f"{exit_code}\n")
    _write_status_atomic(
        run_dir,
        _execution_status(
            run_id,
            "succeeded" if exit_code == 0 else "failed",
            run_dir,
            started_at=command_record["created_at"],
            elapsed_seconds=time.monotonic() - started_monotonic,
            finished_at=_now(),
            supervisor_pid=supervisor_pid,
            supervisor_creation_identity=supervisor_creation_identity,
            target_pid=process.pid,
            target_creation_identity=target_creation_identity,
            latest_output_at=latest_output_at,
            exit_code=exit_code,
        ),
    )
    return 0


def _inspect(run_dir: Path, project_root: Path) -> dict[str, Any]:
    resolved = run_dir.resolve()
    durable_root = (project_root / "待删除/long-running").resolve()
    if not resolved.is_relative_to(durable_root):
        raise ValueError(f"run directory is outside {durable_root}")
    command = json.loads((resolved / "command.json").read_text(encoding="utf-8"))
    status = json.loads((resolved / "status.json").read_text(encoding="utf-8"))
    if command["run_id"] != status["run_id"]:
        raise ValueError("command and status records have different run IDs")
    return {
        "run_dir": str(resolved),
        "command": command,
        "status": status,
        "logs": {
            "stdout": str(resolved / "stdout.log"),
            "stderr": str(resolved / "stderr.log"),
            "merged": str(resolved / "command.log"),
        },
        "exit_code_path": (
            str(resolved / "exit-code.txt")
            if (resolved / "exit-code.txt").is_file()
            else None
        ),
    }


def _reconcile(run_dir: Path, project_root: Path) -> dict[str, Any]:
    snapshot = _inspect(run_dir, project_root)
    resolved_run_dir = Path(snapshot["run_dir"])
    with _status_lock(resolved_run_dir):
        return _reconcile_locked(resolved_run_dir, project_root)


def _reconcile_locked(run_dir: Path, project_root: Path) -> dict[str, Any]:
    snapshot = _inspect(run_dir, project_root)
    status = snapshot["status"]

    def persist_correction(
        decision: str,
        reason: str,
        *,
        observation: str,
        observed_pid: int | None,
        observed_creation_identity: str | None,
    ) -> dict[str, Any]:
        reconciliation = {
            "decision": decision,
            "reason": reason,
            "observed_at": _now(),
            "persisted_target_identity": status.get("target_identity"),
            "observed_target_identity": {
                "observation": observation,
                "pid": observed_pid,
                "process_creation_identity": observed_creation_identity,
            },
        }
        corrected_status = {
            **status,
            "state": decision,
            "updated_at": reconciliation["observed_at"],
            "reconciliation": reconciliation,
        }
        _write_json_atomic(
            Path(snapshot["run_dir"]) / "status.json",
            corrected_status,
        )
        corrected_snapshot = _inspect(run_dir, project_root)
        corrected_snapshot["reconciliation"] = reconciliation
        return corrected_snapshot

    target_identity = status.get("target_identity") or {}
    target_pid = target_identity.get("pid")
    persisted_creation_identity = target_identity.get("process_creation_identity")
    state = status.get("state")
    if state != "running":
        existing_reconciliation = status.get("reconciliation")
        if isinstance(existing_reconciliation, dict):
            snapshot["reconciliation"] = existing_reconciliation
        else:
            snapshot["reconciliation"] = {
                "decision": state,
                "reason": "status_already_terminal",
                "observed_at": _now(),
            }
        return snapshot
    if not isinstance(target_pid, int):
        return persist_correction(
            "unknown",
            "target_identity_incomplete",
            observation="unknown",
            observed_pid=None,
            observed_creation_identity=None,
        )

    observation, observed_creation_identity = _process_observation(target_pid)
    if observation == "missing":
        return persist_correction(
            "interrupted",
            "target_process_missing",
            observation=observation,
            observed_pid=target_pid,
            observed_creation_identity=None,
        )
    if not isinstance(persisted_creation_identity, str):
        return persist_correction(
            "unknown",
            "target_identity_incomplete",
            observation=observation,
            observed_pid=target_pid,
            observed_creation_identity=observed_creation_identity,
        )
    if observation != "present" or observed_creation_identity is None:
        return persist_correction(
            "unknown",
            "target_identity_unavailable",
            observation=observation,
            observed_pid=target_pid,
            observed_creation_identity=observed_creation_identity,
        )
    if observed_creation_identity != persisted_creation_identity:
        return persist_correction(
            "unknown",
            "target_process_creation_mismatch",
            observation=observation,
            observed_pid=target_pid,
            observed_creation_identity=observed_creation_identity,
        )

    snapshot["reconciliation"] = {
        "decision": "running",
        "reason": "target_identity_matches",
        "observed_at": _now(),
        "target_identity": {
            "pid": target_pid,
            "process_creation_identity": observed_creation_identity,
        },
    }
    return snapshot


def _wait(
    run_dir: Path,
    project_root: Path,
    timeout_seconds: float | None,
) -> tuple[dict[str, Any], bool]:
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while True:
        snapshot = _inspect(run_dir, project_root)
        if snapshot["status"]["state"] != "running":
            return snapshot, False
        if deadline is not None and time.monotonic() >= deadline:
            return snapshot, True
        time.sleep(0.1)


def _result(operation: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_name": "persisted-command-result",
        "schema_version": "1.0.0",
        "operation": operation,
        "status": "ok",
        "data": data,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.operation == "_supervise":
        return _supervise(args.run_dir.resolve())
    project_root = _project_root()
    timed_out = False
    if args.operation == "start":
        data = _start(args, project_root)
    elif args.operation == "show":
        data = _inspect(args.run_dir, project_root)
    elif args.operation == "reconcile":
        data = _reconcile(args.run_dir, project_root)
    elif args.operation == "wait":
        data, timed_out = _wait(
            args.run_dir,
            project_root,
            args.timeout_seconds,
        )
    else:
        raise AssertionError(f"unsupported parsed operation: {args.operation}")
    sys.stdout.write(json.dumps(_result(args.operation, data), ensure_ascii=False, sort_keys=True) + "\n")
    return 124 if timed_out else 0


if __name__ == "__main__":
    raise SystemExit(main())

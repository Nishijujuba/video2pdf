from __future__ import annotations

import argparse
from contextlib import ExitStack
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import errno
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
from typing import Any, BinaryIO, Literal, Mapping, Sequence


COMMAND_SCHEMA_VERSION = "1.0.0"
STATUS_SCHEMA_VERSION = "1.0.0"
HEARTBEAT_INTERVAL_SECONDS = 29.0
STATUS_LOCK_FILENAME = ".status.lock"
STATUS_LOCK_RETRY_ATTEMPTS = 1500
STATUS_LOCK_RETRY_DELAY_SECONDS = 0.02
STATUS_PUBLICATION_ERROR_FILENAME = "status-publication-error.json"
STATUS_REPLACE_RETRY_DELAYS_SECONDS = (0.01, 0.025, 0.05, 0.1, 0.2)

_COOKIE_FILE_ARGUMENTS = frozenset(
    {
        "--cookie-file",
        "--cookies",
    }
)
_SECRET_VALUE_ARGUMENTS = frozenset(
    {
        "--api-key",
        "--apikey",
        "--auth",
        "--auth-token",
        "--authorization",
        "--bearer-token",
        "--client-secret",
        "--cookie",
        "--credential",
        "--password",
        "--secret",
        "--token",
    }
)
_SENSITIVE_HEADER_ARGUMENTS = frozenset({"--add-header", "--header"})
_SENSITIVE_ENVIRONMENT_NAME = re.compile(
    r"(?i)(?:^|_)(?:api_?key|auth(?:orization|_token)?|client_secret|cookie|credentials?|password|passwd|secret|token)(?:_|$)"
)
_SENSITIVE_HEADER = re.compile(
    r"(?i)^(\s*(?:cookie|set-cookie|authorization)\s*:).*$"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:auth|authorization|cookie|csrf|po_token|session|signature|token|visitor_data)=)[^&\s\"']+"
)
_QUERY_SECRET_VALUE = re.compile(
    r"(?i)[?&](?:auth|authorization|cookie|csrf|po_token|session|signature|token|visitor_data)=([^&\s\"']+)"
)


@dataclass(frozen=True)
class _ClassifiedArgument:
    original: str
    option: str
    inline_value: str | None
    kind: Literal["cookie_file", "secret", "sensitive_header", "ordinary"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="persisted_command.py")
    commands = parser.add_subparsers(dest="operation", required=True)

    start = commands.add_parser("start")
    start.add_argument("--task-name", required=True)
    start.add_argument("--cwd", type=Path)
    start.add_argument("--accepted-exit-code", action="append", type=int)
    start.add_argument("target_command", nargs=argparse.REMAINDER)

    show = commands.add_parser("show")
    show.add_argument("--run-dir", required=True, type=Path)

    wait = commands.add_parser("wait")
    wait.add_argument("--run-dir", required=True, type=Path)
    wait.add_argument("--timeout-seconds", type=float)

    commands.add_parser("list")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--run-dir", required=True, type=Path)

    commands.add_parser("_supervise").add_argument(
        "--run-dir", required=True, type=Path
    )
    return parser


def _is_windows_sharing_conflict(error: OSError) -> bool:
    return os.name == "nt" and getattr(error, "winerror", None) in {5, 32, 33}


def _write_json_atomic(
    path: Path,
    value: dict[str, Any],
    *,
    replace_retry_delays: Sequence[float] = (),
) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(len(replace_retry_delays) + 1):
        try:
            os.replace(temporary, path)
            return
        except OSError as error:
            if (
                not _is_windows_sharing_conflict(error)
                or attempt == len(replace_retry_delays)
            ):
                raise
            time.sleep(replace_retry_delays[attempt])


def _write_json_new_locked(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


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

            for attempt in range(STATUS_LOCK_RETRY_ATTEMPTS):
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if (
                        error.errno
                        not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                        or attempt == STATUS_LOCK_RETRY_ATTEMPTS - 1
                    ):
                        raise
                    time.sleep(STATUS_LOCK_RETRY_DELAY_SECONDS)
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
        _write_status_locked(run_dir, status)


def _write_status_locked(run_dir: Path, status: dict[str, Any]) -> None:
    _write_json_atomic(
        run_dir / "status.json",
        status,
        replace_retry_delays=STATUS_REPLACE_RETRY_DELAYS_SECONDS,
    )


def _read_status_locked(run_dir: Path) -> dict[str, Any]:
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    publication_error_path = run_dir / STATUS_PUBLICATION_ERROR_FILENAME
    if publication_error_path.is_file():
        publication_error = json.loads(
            publication_error_path.read_text(encoding="utf-8")
        )
        if publication_error["run_id"] != status["run_id"]:
            raise ValueError(
                "status and publication error records have different run IDs"
            )
        return _unverifiable_status_view(
            status,
            recorded_at=publication_error["recorded_at"],
            exit_code=publication_error["exit_code"],
            reason="publication_error_recorded",
            evidence_path=str(publication_error_path),
        )

    exit_code_path = run_dir / "exit-code.txt"
    if status.get("state") != "running" or not exit_code_path.is_file():
        return status
    supervisor_pid = status.get("supervisor_pid")
    if not isinstance(supervisor_pid, int):
        return status
    observation, observed_creation_identity = _process_observation(supervisor_pid)
    persisted_identity = (
        status.get("supervisor_identity") or {}
    ).get("process_creation_identity")
    supervisor_exited = observation == "missing" or (
        observation == "present"
        and isinstance(persisted_identity, str)
        and observed_creation_identity != persisted_identity
    )
    if not supervisor_exited:
        return status
    try:
        exit_code = int(exit_code_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        exit_code = None
    recorded_at = datetime.fromtimestamp(
        exit_code_path.stat().st_mtime
    ).astimezone().isoformat(timespec="milliseconds")
    return _unverifiable_status_view(
        status,
        recorded_at=recorded_at,
        exit_code=exit_code,
        reason="terminal_status_missing_after_supervisor_exit",
        evidence_path=None,
    )


def _unverifiable_status_view(
    status: dict[str, Any],
    *,
    recorded_at: str,
    exit_code: int | None,
    reason: str,
    evidence_path: str | None,
) -> dict[str, Any]:
    publication = {
        "state": "failed",
        "reason": reason,
        "recorded_at": recorded_at,
    }
    if evidence_path is not None:
        publication["evidence_path"] = evidence_path
    return {
        **status,
        "state": "unknown",
        "updated_at": recorded_at,
        "finished_at": recorded_at,
        "exit_code": exit_code,
        "failure": {
            "kind": "status_publication_failed",
            "message": "terminal status could not be atomically published",
        },
        "status_publication": publication,
    }


def _record_terminal_status_publication_error(
    run_dir: Path,
    run_id: str,
    *,
    exit_code: int | None,
) -> None:
    record = {
        "schema_name": "persisted-command-status-publication-error",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "state": "unknown",
        "recorded_at": _now(),
        "exit_code": exit_code,
        "failure": {
            "kind": "status_publication_failed",
            "message": "terminal status could not be atomically published",
        },
    }
    with _status_lock(run_dir):
        _write_json_new_locked(
            run_dir / STATUS_PUBLICATION_ERROR_FILENAME,
            record,
        )


def _publish_terminal_status(
    run_dir: Path,
    status: dict[str, Any],
) -> bool:
    try:
        _write_status_atomic(run_dir, status)
        return True
    except OSError:
        try:
            _record_terminal_status_publication_error(
                run_dir,
                status["run_id"],
                exit_code=status.get("exit_code"),
            )
        except OSError:
            return False
        return False


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
def _environment_secret_values(environment: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value
                for name, value in environment.items()
                if value and _SENSITIVE_ENVIRONMENT_NAME.search(name)
            },
            key=len,
            reverse=True,
        )
    )


def _redact_text(value: str, secret_values: Sequence[str]) -> str:
    safe = value
    for secret in secret_values:
        safe = safe.replace(secret, "<redacted>")
    safe = _SENSITIVE_HEADER.sub(r"\1 <redacted>", safe)
    return _QUERY_SECRET.sub(r"\1<redacted>", safe)


def _add_secret_value(values: set[str], value: str) -> None:
    if not value:
        return
    values.update({value, value.replace("\\", "/"), value.replace("/", "\\")})


def _add_cookie_file_secrets(
    values: set[str],
    value: str,
    working_directory: Path,
) -> None:
    _add_secret_value(values, value)
    cookie_file = Path(value)
    if not cookie_file.is_absolute():
        cookie_file = working_directory / cookie_file
    _add_secret_value(values, str(cookie_file.resolve(strict=False)))
    if not cookie_file.is_file():
        return
    try:
        cookie_text = cookie_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in cookie_text.splitlines():
        if not line or (
            line.startswith("#") and not line.startswith("#HttpOnly_")
        ):
            continue
        fields = line.split("\t")
        if len(fields) >= 7 and fields[-1]:
            _add_secret_value(values, fields[-1])


def _add_header_secret(values: set[str], value: str) -> None:
    payload = value.partition(":")[2].strip()
    _add_secret_value(values, payload)
    if payload.lower().startswith("bearer "):
        _add_secret_value(values, payload[7:].strip())


def _classify_argument(argument: str) -> _ClassifiedArgument:
    option, separator, value = argument.partition("=")
    lowered = option.lower()
    if lowered in _COOKIE_FILE_ARGUMENTS:
        kind = "cookie_file"
    elif lowered in _SECRET_VALUE_ARGUMENTS:
        kind = "secret"
    elif lowered in _SENSITIVE_HEADER_ARGUMENTS:
        kind = "sensitive_header"
    else:
        kind = "ordinary"
    return _ClassifiedArgument(
        original=argument,
        option=option,
        inline_value=value if separator else None,
        kind=kind,
    )


def _capture_command_secret_values(
    arguments: Sequence[_ClassifiedArgument],
    *,
    environment: Mapping[str, str],
    working_directory: Path,
) -> tuple[str, ...]:
    secret_values = set(_environment_secret_values(environment))
    capture_next = False
    next_value_is_cookie_file = False
    for argument in arguments:
        if capture_next:
            if next_value_is_cookie_file:
                _add_cookie_file_secrets(
                    secret_values,
                    argument.original,
                    working_directory,
                )
            else:
                _add_secret_value(secret_values, argument.original)
            capture_next = False
            next_value_is_cookie_file = False
            continue

        if argument.kind == "cookie_file":
            if argument.inline_value is not None:
                _add_cookie_file_secrets(
                    secret_values,
                    argument.inline_value,
                    working_directory,
                )
            else:
                capture_next = True
                next_value_is_cookie_file = True
            continue
        if argument.kind == "secret":
            if argument.inline_value is not None:
                _add_secret_value(secret_values, argument.inline_value)
            else:
                capture_next = True
            continue
        if (
            argument.kind == "sensitive_header"
            and argument.inline_value is not None
        ):
            if _SENSITIVE_HEADER.match(argument.inline_value):
                _add_header_secret(secret_values, argument.inline_value)
            continue
        for matched in _QUERY_SECRET_VALUE.finditer(argument.original):
            _add_secret_value(secret_values, matched.group(1))
        if _SENSITIVE_HEADER.match(argument.original):
            _add_header_secret(secret_values, argument.original)
    return tuple(sorted(secret_values, key=len, reverse=True))


def _prepare_command_security(
    argv: Sequence[str],
    *,
    environment: Mapping[str, str],
    working_directory: Path,
) -> tuple[list[str], tuple[str, ...]]:
    arguments = tuple(_classify_argument(argument) for argument in argv)
    secret_values = _capture_command_secret_values(
        arguments,
        environment=environment,
        working_directory=working_directory,
    )
    redacted: list[str] = []
    redact_next_as: str | None = None
    for argument in arguments:
        if redact_next_as is not None:
            redacted.append(redact_next_as)
            redact_next_as = None
            continue

        if argument.kind == "cookie_file":
            redacted.append(
                f"{argument.option}=<localized-cookie-file>"
                if argument.inline_value is not None
                else argument.original
            )
            if argument.inline_value is None:
                redact_next_as = "<localized-cookie-file>"
            continue
        if argument.kind == "secret":
            redacted.append(
                f"{argument.option}=<redacted>"
                if argument.inline_value is not None
                else argument.original
            )
            if argument.inline_value is None:
                redact_next_as = "<redacted>"
            continue
        if (
            argument.kind == "sensitive_header"
            and argument.inline_value is not None
        ):
            redacted.append(
                f"{argument.option}="
                f"{_redact_text(argument.inline_value, secret_values)}"
            )
            continue
        redacted.append(_redact_text(argument.original, secret_values))
    return redacted, secret_values


def _contains_detected_secret(value: bytes, secret_values: Sequence[str]) -> bool:
    text = value.decode("utf-8", errors="replace")
    return _redact_text(text, secret_values) != text


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
    **fields: Any,
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
        **fields,
    )
    status["heartbeat_at"] = status["updated_at"]
    if finished_at is not None:
        status["finished_at"] = finished_at
    return status
def _record_supervisor_handoff_failure(
    run_dir: Path,
    command_record: Mapping[str, Any],
    *,
    supervisor_pid: int,
) -> None:
    _publish_terminal_status(
        run_dir,
        _status(
            command_record["run_id"],
            "launch_failed",
            started_at=command_record["created_at"],
            finished_at=_now(),
            supervisor_pid=supervisor_pid,
            child_pid=None,
            exit_code=None,
            failure={
                "kind": "supervisor_handoff_failed",
                "message": "target launch request could not be received",
            },
        ),
    )


def _start(args: argparse.Namespace, project_root: Path) -> dict[str, Any]:
    target_command = list(args.target_command)
    if target_command[:1] == ["--"]:
        target_command = target_command[1:]
    if not target_command:
        raise ValueError("start requires a target command after --")

    environment = os.environ.copy()
    working_directory = (args.cwd or project_root).resolve()
    redacted_argv, _secret_values = _prepare_command_security(
        target_command,
        environment=environment,
        working_directory=working_directory,
    )
    safe_task_name = _redact_text(args.task_name, _secret_values)
    safe_working_directory = _redact_text(
        str(working_directory),
        _secret_values,
    )
    run_id, run_dir = _create_run_directory(project_root, safe_task_name)
    created_at = _now()
    command_record = {
        "schema_name": "persisted-command",
        "schema_version": COMMAND_SCHEMA_VERSION,
        "run_id": run_id,
        "task_name": safe_task_name,
        "normalized_task_name": _normalized_task_name(safe_task_name),
        "created_at": created_at,
        "cwd": safe_working_directory,
        "argv": redacted_argv,
        "accepted_exit_codes": sorted(set(args.accepted_exit_code or [0])),
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
        "stdin": subprocess.PIPE,
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
    supervisor = subprocess.Popen(supervisor_command, **options)
    assert supervisor.stdin is not None
    launch_request = json.dumps(
        {
            "argv": target_command,
            "cwd": str(working_directory),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        supervisor.stdin.write(launch_request)
        supervisor.stdin.close()
    except OSError:
        try:
            supervisor.stdin.close()
        except OSError:
            pass
        _record_supervisor_handoff_failure(
            run_dir,
            command_record,
            supervisor_pid=supervisor.pid,
        )
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


class _LogExitStack(ExitStack):
    """Close command logs while converting close errors into terminal evidence."""

    close_failed = False

    def __exit__(self, *details: object) -> bool:
        try:
            return bool(super().__exit__(*details))
        except OSError:
            self.close_failed = True
            return True


def _supervise(run_dir: Path) -> int:
    command_record = json.loads((run_dir / "command.json").read_text(encoding="utf-8"))
    run_id = command_record["run_id"]
    try:
        launch_request = json.load(sys.stdin)
        target_command = launch_request["argv"]
        raw_working_directory = launch_request["cwd"]
        if not isinstance(target_command, list) or not all(
            isinstance(argument, str) for argument in target_command
        ):
            raise ValueError("target argv must be a list of strings")
        if not isinstance(raw_working_directory, str):
            raise ValueError("target cwd must be a string")
        working_directory = Path(raw_working_directory)
    except (KeyError, OSError, TypeError, ValueError):
        _record_supervisor_handoff_failure(
            run_dir, command_record, supervisor_pid=os.getpid()
        )
        return 1

    _redacted_argv, secret_values = _prepare_command_security(
        target_command,
        environment=os.environ,
        working_directory=working_directory,
    )
    started_monotonic = time.monotonic()
    try:
        process = subprocess.Popen(
            target_command,
            cwd=working_directory,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        _publish_terminal_status(
            run_dir,
            _status(
                run_id,
                "launch_failed",
                started_at=command_record["created_at"],
                finished_at=_now(),
                supervisor_pid=os.getpid(),
                child_pid=None,
                exit_code=None,
                failure={
                    "kind": "child_launch_failed",
                    "message": "target process could not be launched",
                },
            ),
        )
        return 1

    supervisor_pid = os.getpid()
    supervisor_creation_identity = _process_identity(supervisor_pid)
    target_creation_identity = _process_identity(process.pid)
    latest_output_at = None
    nonterminal_status_publication_failures = 0
    try:
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
    except OSError:
        nonterminal_status_publication_failures += 1

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

    log_persistence_failed = False
    secret_detected = False
    stream_logs: dict[str, BinaryIO] = {}
    merged_log: BinaryIO | None = None
    logs = _LogExitStack()
    with logs:
        try:
            stream_logs = {
                "stdout": logs.enter_context(
                    (run_dir / "stdout.log").open("ab", buffering=0)
                ),
                "stderr": logs.enter_context(
                    (run_dir / "stderr.log").open("ab", buffering=0)
                ),
            }
            merged_log = logs.enter_context(
                (run_dir / "command.log").open("ab", buffering=0)
            )
        except OSError:
            log_persistence_failed = True

        finished_readers = 0
        next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
        while finished_readers < len(readers) or process.poll() is None:
            current_monotonic = time.monotonic()
            event_timeout = max(0.0, next_heartbeat - current_monotonic)
            if finished_readers == len(readers):
                event_timeout = min(event_timeout, 0.1)
            try:
                event = events.get(timeout=event_timeout)
            except queue.Empty:
                event_received = False
            else:
                event_received = True
            if event_received:
                if event is None:
                    finished_readers += 1
                else:
                    stream_name, chunk = event
                    if _contains_detected_secret(chunk, secret_values):
                        secret_detected = True
                    if not log_persistence_failed:
                        try:
                            stream_logs[stream_name].write(chunk)
                            assert merged_log is not None
                            merged_log.write(
                                f"[{stream_name}] ".encode("ascii") + chunk
                            )
                            latest_output_at = _now()
                        except OSError:
                            log_persistence_failed = True

            current_monotonic = time.monotonic()
            if current_monotonic >= next_heartbeat:
                try:
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
                except OSError:
                    nonterminal_status_publication_failures += 1
                while next_heartbeat <= current_monotonic:
                    next_heartbeat += HEARTBEAT_INTERVAL_SECONDS

        if not log_persistence_failed:
            try:
                for log in (*stream_logs.values(), merged_log):
                    assert log is not None
                    log.flush()
                    os.fsync(log.fileno())
            except OSError:
                log_persistence_failed = True

    log_persistence_failed = log_persistence_failed or logs.close_failed
    exit_code = process.wait()
    for reader in readers:
        reader.join()
    _write_text_atomic(run_dir / "exit-code.txt", f"{exit_code}\n")
    terminal_state = (
        "failed"
        if log_persistence_failed
        else (
            "succeeded"
            if exit_code in command_record["accepted_exit_codes"]
            else "failed"
        )
    )
    terminal_fields: dict[str, Any] = {
        "security": {
            "acceptance_evidence_eligible": not secret_detected,
            "classification": (
                "security_failure" if secret_detected else "no_secret_detected"
            ),
        }
    }
    if log_persistence_failed:
        terminal_fields["failure"] = {
            "kind": "log_persistence_failed",
            "message": "one or more command logs could not be persisted",
        }
    if nonterminal_status_publication_failures:
        terminal_fields["status_publication"] = {
            "state": "recovered",
            "nonterminal_failures": nonterminal_status_publication_failures,
        }
    terminal_published = _publish_terminal_status(
        run_dir,
        _execution_status(
            run_id,
            terminal_state,
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
            **terminal_fields,
        ),
    )
    return 0 if terminal_published else 1

def _resolve_run_directory(run_dir: Path, project_root: Path) -> Path:
    resolved = run_dir.resolve()
    durable_root = (project_root / "待删除/long-running").resolve()
    if not resolved.is_relative_to(durable_root):
        raise ValueError(f"run directory is outside {durable_root}")
    return resolved


def _inspect(run_dir: Path, project_root: Path) -> dict[str, Any]:
    resolved = _resolve_run_directory(run_dir, project_root)
    with _status_lock(resolved):
        return _inspect_locked(resolved)


def _inspect_locked(resolved: Path) -> dict[str, Any]:
    command = json.loads((resolved / "command.json").read_text(encoding="utf-8"))
    status = _read_status_locked(resolved)
    if command["run_id"] != status["run_id"]:
        raise ValueError("command and status records have different run IDs")
    evidence_paths = {
        "command": str(resolved / "command.json"),
        "status": str(resolved / "status.json"),
        "stdout": str(resolved / "stdout.log"),
        "stderr": str(resolved / "stderr.log"),
        "merged": str(resolved / "command.log"),
        "exit_code": (
            str(resolved / "exit-code.txt")
            if (resolved / "exit-code.txt").is_file()
            else None
        ),
        "status_publication_error": (
            str(resolved / STATUS_PUBLICATION_ERROR_FILENAME)
            if (resolved / STATUS_PUBLICATION_ERROR_FILENAME).is_file()
            else None
        ),
    }
    return {
        "run_id": command["run_id"],
        "task_name": command["task_name"],
        "state": status["state"],
        "process_identity": {
            "supervisor_pid": status.get("supervisor_pid"),
            "child_pid": status.get("child_pid"),
        },
        "evidence_paths": evidence_paths,
        "exit_code": status.get("exit_code"),
        "run_dir": str(resolved),
        "command": command,
        "status": status,
        "logs": {
            name: evidence_paths[name] for name in ("stdout", "stderr", "merged")
        },
        "exit_code_path": evidence_paths["exit_code"],
    }


def _list_runs(project_root: Path) -> dict[str, Any]:
    durable_root = project_root / "待删除/long-running"
    if not durable_root.is_dir():
        return {"runs": []}
    runs = [
        _inspect(run_dir, project_root)
        for run_dir in durable_root.iterdir()
        if run_dir.is_dir()
        and (run_dir / "command.json").is_file()
        and (run_dir / "status.json").is_file()
    ]
    runs.sort(
        key=lambda run: (run["command"]["created_at"], run["run_id"]),
        reverse=True,
    )
    return {"runs": runs}


def _reconcile(run_dir: Path, project_root: Path) -> dict[str, Any]:
    resolved_run_dir = _resolve_run_directory(run_dir, project_root)
    with _status_lock(resolved_run_dir):
        return _reconcile_locked(_inspect_locked(resolved_run_dir))


def _reconcile_locked(snapshot: dict[str, Any]) -> dict[str, Any]:
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
        resolved_run_dir = Path(snapshot["run_dir"])
        _write_status_locked(resolved_run_dir, corrected_status)
        corrected_snapshot = _inspect_locked(resolved_run_dir)
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
    project_root = Path(__file__).resolve().parents[2]
    timed_out = False
    if args.operation == "start":
        data = _start(args, project_root)
    elif args.operation == "show":
        data = _inspect(args.run_dir, project_root)
    elif args.operation == "list":
        data = _list_runs(project_root)
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

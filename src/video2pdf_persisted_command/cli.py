from __future__ import annotations

import argparse
from contextlib import ExitStack
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
from typing import Any, BinaryIO, Mapping, Sequence


COMMAND_SCHEMA_VERSION = "1.0.0"
STATUS_SCHEMA_VERSION = "1.0.0"

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


def _add_cookie_file_secrets(values: set[str], value: str) -> None:
    _add_secret_value(values, value)
    cookie_file = Path(value)
    if not cookie_file.is_file():
        return
    try:
        cookie_text = cookie_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in cookie_text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7 and len(fields[-1]) >= 4:
            _add_secret_value(values, fields[-1])


def _add_header_secret(values: set[str], value: str) -> None:
    payload = value.partition(":")[2].strip()
    _add_secret_value(values, payload)
    if payload.lower().startswith("bearer "):
        _add_secret_value(values, payload[7:].strip())


def _capture_command_secret_values(
    argv: Sequence[str],
    *,
    environment: Mapping[str, str],
) -> tuple[str, ...]:
    secret_values = set(_environment_secret_values(environment))
    capture_next = False
    next_value_is_cookie_file = False
    for argument in argv:
        if capture_next:
            if next_value_is_cookie_file:
                _add_cookie_file_secrets(secret_values, argument)
            else:
                _add_secret_value(secret_values, argument)
            capture_next = False
            next_value_is_cookie_file = False
            continue

        option, separator, value = argument.partition("=")
        lowered = option.lower()
        if lowered in _COOKIE_FILE_ARGUMENTS:
            if separator:
                _add_cookie_file_secrets(secret_values, value)
            else:
                capture_next = True
                next_value_is_cookie_file = True
            continue
        if lowered in _SECRET_VALUE_ARGUMENTS:
            if separator:
                _add_secret_value(secret_values, value)
            else:
                capture_next = True
            continue
        if lowered in _SENSITIVE_HEADER_ARGUMENTS and separator:
            if _SENSITIVE_HEADER.match(value):
                _add_header_secret(secret_values, value)
            continue
        for matched in _QUERY_SECRET_VALUE.finditer(argument):
            _add_secret_value(secret_values, matched.group(1))
        if _SENSITIVE_HEADER.match(argument):
            _add_header_secret(secret_values, argument)
    return tuple(sorted(secret_values, key=len, reverse=True))


def _prepare_command_security(
    argv: Sequence[str],
    *,
    environment: Mapping[str, str],
) -> tuple[list[str], tuple[str, ...]]:
    secret_values = _capture_command_secret_values(
        argv,
        environment=environment,
    )
    redacted: list[str] = []
    redact_next_as: str | None = None
    for argument in argv:
        if redact_next_as is not None:
            redacted.append(redact_next_as)
            redact_next_as = None
            continue

        option, separator, value = argument.partition("=")
        lowered = option.lower()
        if lowered in _COOKIE_FILE_ARGUMENTS:
            redacted.append(
                f"{option}=<localized-cookie-file>"
                if separator
                else argument
            )
            if not separator:
                redact_next_as = "<localized-cookie-file>"
            continue
        if lowered in _SECRET_VALUE_ARGUMENTS:
            redacted.append(f"{option}=<redacted>" if separator else argument)
            if not separator:
                redact_next_as = "<redacted>"
            continue
        if lowered in _SENSITIVE_HEADER_ARGUMENTS and separator:
            redacted.append(f"{option}={_redact_text(value, secret_values)}")
            continue
        redacted.append(_redact_text(argument, secret_values))
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


def _start(args: argparse.Namespace, project_root: Path) -> dict[str, Any]:
    target_command = list(args.target_command)
    if target_command[:1] == ["--"]:
        target_command = target_command[1:]
    if not target_command:
        raise ValueError("start requires a target command after --")

    environment = os.environ.copy()
    environment_secrets = _environment_secret_values(environment)
    safe_task_name = _redact_text(args.task_name, environment_secrets)
    redacted_argv, _secret_values = _prepare_command_security(
        target_command,
        environment=environment,
    )
    run_id, run_dir = _create_run_directory(project_root, safe_task_name)
    working_directory = (args.cwd or project_root).resolve()
    created_at = _now()
    command_record = {
        "schema_name": "persisted-command",
        "schema_version": COMMAND_SCHEMA_VERSION,
        "run_id": run_id,
        "task_name": safe_task_name,
        "normalized_task_name": _normalized_task_name(safe_task_name),
        "created_at": created_at,
        "cwd": str(working_directory),
        "argv": redacted_argv,
        "accepted_exit_codes": sorted(set(args.accepted_exit_code or [0])),
    }
    _write_json_atomic(run_dir / "command.json", command_record)
    for filename in ("stdout.log", "stderr.log", "command.log"):
        (run_dir / filename).touch(exist_ok=False)
    _write_json_atomic(
        run_dir / "status.json",
        _status(
            run_id,
            "running",
            started_at=created_at,
            supervisor_pid=None,
            child_pid=None,
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
        {"argv": target_command},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    supervisor.stdin.write(launch_request)
    supervisor.stdin.close()
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


def _supervise(run_dir: Path, target_command: Sequence[str]) -> int:
    command_record = json.loads((run_dir / "command.json").read_text(encoding="utf-8"))
    run_id = command_record["run_id"]
    _redacted_argv, secret_values = _prepare_command_security(
        target_command,
        environment=os.environ,
    )
    try:
        process = subprocess.Popen(
            target_command,
            cwd=command_record["cwd"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        _write_json_atomic(
            run_dir / "status.json",
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
    _write_json_atomic(
        run_dir / "status.json",
        _status(
            run_id,
            "running",
            started_at=command_record["created_at"],
            supervisor_pid=os.getpid(),
            child_pid=process.pid,
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

    log_persistence_failed = False
    secret_detected = False
    try:
        with ExitStack() as logs:
            stream_logs: dict[str, BinaryIO] = {}
            merged_log: BinaryIO | None = None
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
            while finished_readers < len(readers):
                event = events.get()
                if event is None:
                    finished_readers += 1
                    continue
                stream_name, chunk = event
                if _contains_detected_secret(chunk, secret_values):
                    secret_detected = True
                if log_persistence_failed:
                    continue
                try:
                    stream_logs[stream_name].write(chunk)
                    assert merged_log is not None
                    merged_log.write(f"[{stream_name}] ".encode("ascii") + chunk)
                except OSError:
                    log_persistence_failed = True

            if not log_persistence_failed:
                try:
                    for log in (*stream_logs.values(), merged_log):
                        assert log is not None
                        log.flush()
                        os.fsync(log.fileno())
                except OSError:
                    log_persistence_failed = True
    except OSError:
        log_persistence_failed = True

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
    terminal_fields: dict[str, Any] = {}
    if log_persistence_failed:
        terminal_fields["failure"] = {
            "kind": "log_persistence_failed",
            "message": "one or more command logs could not be persisted",
        }
    terminal_fields["security"] = {
        "acceptance_evidence_eligible": not secret_detected,
        "classification": (
            "security_failure" if secret_detected else "no_secret_detected"
        ),
    }
    _write_json_atomic(
        run_dir / "status.json",
        _status(
            run_id,
            terminal_state,
            started_at=command_record["created_at"],
            finished_at=_now(),
            supervisor_pid=os.getpid(),
            child_pid=process.pid,
            exit_code=exit_code,
            **terminal_fields,
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
        launch_request = json.load(sys.stdin)
        return _supervise(args.run_dir.resolve(), launch_request["argv"])
    project_root = _project_root()
    timed_out = False
    if args.operation == "start":
        data = _start(args, project_root)
    elif args.operation == "show":
        data = _inspect(args.run_dir, project_root)
    else:
        data, timed_out = _wait(
            args.run_dir,
            project_root,
            args.timeout_seconds,
        )
    sys.stdout.write(json.dumps(_result(args.operation, data), ensure_ascii=False, sort_keys=True) + "\n")
    return 124 if timed_out else 0


if __name__ == "__main__":
    raise SystemExit(main())

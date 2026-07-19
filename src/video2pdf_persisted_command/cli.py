from __future__ import annotations

import argparse
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

    commands.add_parser("list")

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
    process = subprocess.Popen(
        command_record["argv"],
        cwd=command_record["cwd"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
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

    finished_readers = 0
    with (run_dir / "stdout.log").open("ab", buffering=0) as stdout_log, (
        run_dir / "stderr.log"
    ).open("ab", buffering=0) as stderr_log, (run_dir / "command.log").open(
        "ab", buffering=0
    ) as merged_log:
        stream_logs = {"stdout": stdout_log, "stderr": stderr_log}
        while finished_readers < len(readers):
            event = events.get()
            if event is None:
                finished_readers += 1
                continue
            stream_name, chunk = event
            stream_logs[stream_name].write(chunk)
            merged_log.write(f"[{stream_name}] ".encode("ascii") + chunk)

    exit_code = process.wait()
    for reader in readers:
        reader.join()
    _write_text_atomic(run_dir / "exit-code.txt", f"{exit_code}\n")
    _write_json_atomic(
        run_dir / "status.json",
        _status(
            run_id,
            "succeeded" if exit_code == 0 else "failed",
            started_at=command_record["created_at"],
            finished_at=_now(),
            supervisor_pid=os.getpid(),
            child_pid=process.pid,
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
    elif args.operation == "list":
        data = _list_runs(project_root)
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

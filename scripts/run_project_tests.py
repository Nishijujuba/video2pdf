"""Public CLI for external-root project test discovery and execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.project_test_discovery import (  # noqa: E402
    DiscoveryError,
)
from scripts.project_test_external_root import (  # noqa: E402
    ExternalRootError,
    create_unique_run_directory,
    ensure_project_root,
)
from scripts.project_test_registry import (  # noqa: E402
    Registry,
    RegistryError,
    load_registry,
)
from scripts.project_test_results import (  # noqa: E402
    ResultIntegrityError,
    canonical_json_bytes,
    sha256_file,
    write_json_exclusive,
)
from scripts.project_test_scheduler import (  # noqa: E402
    SchedulerError,
    run_modules,
)


REGISTRY_RELATIVE_PATH = Path("config/test-suites.v1.json")
TEST_RUN_SCHEMA_NAME = "video2pdf.project-test-run"
SCHEMA_VERSION = 1


class CliError(RuntimeError):
    def __init__(self, message: str, *, failure_kind: str = "argument_failure"):
        super().__init__(message)
        self.failure_kind = failure_kind


class _StrictArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError(message)


def _emit(value: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    stream.write(canonical_json_bytes(value).decode("utf-8"))
    stream.flush()


def _remote_identity(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    remote = completed.stdout.strip()
    if completed.returncode != 0 or not remote:
        raise CliError(
            "cannot resolve remote.origin.url",
            failure_kind="external_root_failure",
        )
    return remote


def _suite_key(registry: Registry, suite_ids: Sequence[str] | None) -> str:
    suites = registry.select_suites(suite_ids)
    return suites[0].suite_key if len(suites) == 1 else "all"


def _prepare_run(
    repo_root: Path,
    registry: Registry,
    suite_ids: Sequence[str] | None,
    test_root: Path,
) -> tuple[Path, list[str]]:
    selected = registry.select_suites(suite_ids)
    project_root = ensure_project_root(
        test_root,
        _remote_identity(repo_root),
    )
    run_dir = create_unique_run_directory(
        project_root,
        _suite_key(registry, suite_ids),
        registered_suite_keys={suite.suite_key for suite in registry.suites},
    )
    return run_dir, [suite.suite_id for suite in selected]


def _forward_child_output(completed_stdout: bytes, completed_stderr: bytes) -> None:
    stdout = getattr(sys.stdout, "buffer", None)
    stderr = getattr(sys.stderr, "buffer", None)
    if completed_stdout:
        if stdout is None:
            sys.stdout.write(completed_stdout.decode("utf-8", errors="replace"))
        else:
            stdout.write(completed_stdout)
            stdout.flush()
    if completed_stderr:
        if stderr is None:
            sys.stderr.write(completed_stderr.decode("utf-8", errors="replace"))
        else:
            stderr.write(completed_stderr)
            stderr.flush()


def _independent_discovery(
    *,
    repo_root: Path,
    registry_path: Path,
    suite_ids: Sequence[str] | None,
    destination: Path,
) -> tuple[dict[str, Any], str, int]:
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-B",
        "-m",
        "scripts.project_test_discovery",
        "--repo-root",
        str(repo_root),
        "--registry",
        str(registry_path),
        "--destination",
        str(destination),
    ]
    for suite_id in suite_ids or ():
        command.extend(("--suite", suite_id))
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    child = subprocess.Popen(
        command,
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    child_stdout, child_stderr = child.communicate()
    _forward_child_output(child_stdout, child_stderr)
    if child.returncode != 0:
        raise CliError(
            f"independent discovery process exited with {child.returncode}",
            failure_kind="discovery_failure",
        )
    try:
        raw = destination.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CliError(
            f"independent discovery manifest is unreadable: {error}",
            failure_kind="discovery_failure",
        ) from error
    if not isinstance(manifest, dict):
        raise CliError(
            "independent discovery manifest must be an object",
            failure_kind="discovery_failure",
        )
    return manifest, hashlib.sha256(raw).hexdigest(), child.pid


def _validate_timings_path(value: Path | None) -> Path | None:
    if value is None:
        return None
    if not value.is_absolute():
        raise CliError("--timings-from must be an absolute path")
    try:
        resolved = value.resolve(strict=True)
    except OSError as error:
        raise CliError(f"--timings-from is not readable: {value}") from error
    if not resolved.is_file():
        raise CliError("--timings-from must identify a file")
    return resolved


def _public_command(arguments: argparse.Namespace) -> int:
    repo_root = REPO_ROOT.resolve(strict=True)
    registry = load_registry(repo_root, repo_root / REGISTRY_RELATIVE_PATH)
    run_dir, selected_suite_ids = _prepare_run(
        repo_root,
        registry,
        arguments.suite,
        arguments.test_root,
    )
    discovery, discovery_sha256, discovery_pid = _independent_discovery(
        repo_root=repo_root,
        registry_path=registry.registry_path,
        suite_ids=selected_suite_ids,
        destination=run_dir / "discovery.json",
    )
    if discovery.get("registry_sha256") != registry.fingerprint:
        raise CliError(
            "discovery registry fingerprint changed before scheduling",
            failure_kind="discovery_failure",
        )
    timings_from = (
        _validate_timings_path(arguments.timings_from)
        if arguments.command == "run"
        else None
    )
    test_run = {
        "schema_name": TEST_RUN_SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "command": arguments.command,
        "project": discovery.get("project"),
        "commit": discovery.get("commit"),
        "registry_sha256": registry.fingerprint,
        "discovery_sha256": discovery_sha256,
        "suite_ids": selected_suite_ids,
        "requested_jobs": arguments.jobs
        if arguments.command == "run"
        else None,
        "timings_from": str(timings_from) if timings_from is not None else None,
        "runner_pid": os.getpid(),
        "discovery_process": {
            "pid": discovery_pid,
            "exit_code": 0,
        },
    }
    write_json_exclusive(run_dir / "test-run.json", test_run)
    if arguments.command == "discover":
        _emit(
            {
                "event": "project_test_discovery_complete",
                "success": True,
                "failure_kind": None,
                "run_dir": str(run_dir),
                "suite_ids": selected_suite_ids,
                "total_count": discovery.get("total_count"),
                "discovery_sha256": discovery_sha256,
            }
        )
        return 0

    if sha256_file(run_dir / "discovery.json") != discovery_sha256:
        raise CliError(
            "discovery manifest changed before scheduling",
            failure_kind="discovery_failure",
        )
    _emit(
        {
            "event": "project_test_scheduling_started",
            "run_dir": str(run_dir),
            "discovery_sha256": discovery_sha256,
            "total_count": discovery.get("total_count"),
        }
    )
    summary = run_modules(
        repo_root=repo_root,
        run_dir=run_dir,
        discovery=discovery,
        jobs=arguments.jobs,
        timings_from=timings_from,
    )
    _emit(
        {
            "event": "project_test_run_complete",
            "success": summary["success"],
            "failure_kind": summary["failure_kind"],
            "run_dir": str(run_dir),
            "summary_sha256": sha256_file(run_dir / "summary.json"),
            "discovery_sha256": discovery_sha256,
        }
    )
    return 0 if summary["success"] else 1


def _parser() -> argparse.ArgumentParser:
    parser = _StrictArgumentParser()
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_StrictArgumentParser,
    )
    for command in ("discover", "run"):
        public = subparsers.add_parser(command)
        public.add_argument("--suite", action="append")
        public.add_argument("--test-root", required=True, type=Path)
        if command == "run":
            public.add_argument(
                "--jobs",
                type=int,
                choices=range(1, 5),
                default=4,
            )
            public.add_argument("--timings-from", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    command = None
    try:
        arguments = _parser().parse_args(argv)
        command = arguments.command
        return _public_command(arguments)
    except CliError as error:
        failure_kind = error.failure_kind
        detail = str(error)
    except RegistryError as error:
        failure_kind = "registry_failure"
        detail = str(error)
    except ExternalRootError as error:
        failure_kind = "external_root_failure"
        detail = str(error)
    except DiscoveryError as error:
        failure_kind = "discovery_failure"
        detail = str(error)
    except SchedulerError as error:
        failure_kind = error.failure_kind
        detail = str(error)
    except ResultIntegrityError as error:
        failure_kind = "result_integrity_failure"
        detail = str(error)
    except (OSError, ValueError) as error:
        failure_kind = "runner_failure"
        detail = f"{type(error).__name__}: {error}"
    _emit(
        {
            "event": "project_test_command_failed",
            "command": command,
            "success": False,
            "failure_kind": failure_kind,
            "detail": detail,
        }
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

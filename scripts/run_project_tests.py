"""Public CLI for external-root project test discovery and execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.project_test_discovery import (  # noqa: E402
    DiscoveryError,
)
from scripts.project_test_external_root import (  # noqa: E402
    ExternalRootError,
    ExternalRootPathBudgetError,
    create_unique_run_directory,
    ensure_project_root,
    validate_external_test_root_path_budget,
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
from scripts.project_test_source_provenance import (  # noqa: E402
    FIXED_EXECUTION_SOURCE_PATHS,
    RUN_FINALIZATION_RELATIVE_PATH,
    SOURCE_MANIFEST_RELATIVE_PATH,
    SOURCE_SNAPSHOT_RELATIVE_PATH,
    SourceBinding,
    SourceProvenanceError,
    build_execution_source_manifest,
    create_source_snapshot,
    create_frozen_git_authority,
    finalize_source_snapshot,
    freeze_execution_source_files,
    module_inventory,
    planned_execution_source_paths,
)
from src.video2pdf_persisted_command.process_identity import (  # noqa: E402
    execution_identity_is_complete,
    process_execution_identity,
)


REGISTRY_RELATIVE_PATH = Path("config/test-suites.v1.json")
TEST_RUN_SCHEMA_NAME = "video2pdf.project-test-run"
SCHEMA_VERSION = 2
_MODULE_KEY_BUDGET_COMPONENT = "0123456789ab"
_RESERVED_RUN_ARTIFACT_PATHS = (
    "test-run.json",
    "discovery.json",
    "events.jsonl",
    "summary.json",
    "timings.json",
    SOURCE_MANIFEST_RELATIVE_PATH.as_posix(),
    SOURCE_SNAPSHOT_RELATIVE_PATH.as_posix(),
    RUN_FINALIZATION_RELATIVE_PATH.as_posix(),
    f"modules/{_MODULE_KEY_BUDGET_COMPONENT}.assignment.json",
    f"modules/{_MODULE_KEY_BUDGET_COMPONENT}.result.json",
    f"logs/{_MODULE_KEY_BUDGET_COMPONENT}.stdout.log",
    f"logs/{_MODULE_KEY_BUDGET_COMPONENT}.stderr.log",
    "execution-source-files/.git",
    "execution-git/config",
    "execution-git/objects/info/alternates",
    "execution-git/refs/heads/execution-source",
)
_SELF_HOSTED_RESERVED_ARTIFACT_PATHS = (
    *_RESERVED_RUN_ARTIFACT_PATHS,
    *(
        f"execution-source-files/{path}"
        for path in FIXED_EXECUTION_SOURCE_PATHS
    ),
)


class CliError(RuntimeError):
    def __init__(self, message: str, *, failure_kind: str = "argument_failure"):
        super().__init__(message)
        self.failure_kind = failure_kind


class _StrictArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError(message)


def _clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        environment.pop(name, None)
    return environment


def _persisted_launch_binding() -> dict[str, Any] | None:
    run_id = os.environ.get("VIDEO2PDF_PERSISTED_RUN_ID")
    run_nonce = os.environ.get("VIDEO2PDF_PERSISTED_RUN_NONCE")
    run_dir_text = os.environ.get("VIDEO2PDF_PERSISTED_RUN_DIR")
    if not run_id and not run_nonce and not run_dir_text:
        return None
    if not run_id or not run_nonce or not run_dir_text:
        raise CliError(
            "persisted launch environment is incomplete",
            failure_kind="persisted_binding_failure",
        )
    status_path = Path(run_dir_text) / "status.json"
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            time.sleep(0.01)
            continue
        if (
            isinstance(status, dict)
            and status.get("run_id") == run_id
            and status.get("run_nonce") == run_nonce
            and execution_identity_is_complete(
                status.get("target_identity")
            )
            and execution_identity_is_complete(
                status.get("supervisor_identity")
            )
        ):
            return {
                "run_id": run_id,
                "run_nonce": run_nonce,
                "target_identity": status["target_identity"],
                "supervisor_identity": status["supervisor_identity"],
            }
        time.sleep(0.01)
    raise CliError(
        "persisted launch identity is unavailable",
        failure_kind="persisted_binding_failure",
    )


def _git_directory(repo_root: Path) -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_clean_git_environment(),
    )
    try:
        value = Path(completed.stdout.strip()).resolve(strict=True)
    except OSError as error:
        raise SourceProvenanceError(
            "cannot resolve the repository Git directory"
        ) from error
    if completed.returncode != 0 or not value.is_dir():
        raise SourceProvenanceError(
            "cannot resolve the repository Git directory"
        )
    return value


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
        env=_clean_git_environment(),
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
    execution_root: Path,
    registry_path: Path,
    commit: str,
    suite_ids: Sequence[str] | None,
    destination: Path,
) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-B",
        "-m",
        "scripts.project_test_discovery",
        "--repo-root",
        str(execution_root),
        "--registry",
        str(registry_path),
        "--destination",
        str(destination),
        "--commit",
        commit,
        "--launcher-binding-stdin",
    ]
    for suite_id in suite_ids or ():
        command.extend(("--suite", suite_id))
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(execution_root)
    child = subprocess.Popen(
        command,
        cwd=execution_root,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    deadline = time.monotonic() + 2.0
    child_identity = None
    while child_identity is None and time.monotonic() < deadline:
        child_identity = process_execution_identity(child.pid)
        if child_identity is None:
            time.sleep(0.01)
    if not execution_identity_is_complete(child_identity):
        child.kill()
        child.communicate()
        raise CliError(
            "independent discovery launcher identity is unavailable",
            failure_kind="discovery_failure",
        )
    child_stdout, child_stderr = child.communicate(
        input=canonical_json_bytes(
            {
                "launcher_identity": child_identity,
                "command": command,
            }
        )
    )
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
    discovery_process = manifest.get("discovery_process")
    if (
        not isinstance(discovery_process, dict)
        or discovery_process.get("launcher_identity") != child_identity
    ):
        raise CliError(
            "independent discovery identity binding is invalid",
            failure_kind="discovery_failure",
        )
    return (
        manifest,
        hashlib.sha256(raw).hexdigest(),
        {**discovery_process, "exit_code": 0},
    )


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


def _post_snapshot_failure_kind(
    error: BaseException,
    *,
    summary_exists: bool,
) -> str:
    if isinstance(error, CliError):
        if error.failure_kind == "argument_failure":
            return "scheduler_setup_failure"
        return error.failure_kind
    if isinstance(error, SchedulerError):
        if not summary_exists:
            return "scheduler_setup_failure"
        return error.failure_kind
    if isinstance(error, ResultIntegrityError):
        return "result_integrity_failure"
    if isinstance(error, SourceProvenanceError):
        return "source_binding_failure"
    if isinstance(error, (OSError, ValueError)):
        return "scheduler_setup_failure"
    return "coordinator_failure"


def _public_command(arguments: argparse.Namespace) -> int:
    repo_root = REPO_ROOT.resolve(strict=True)
    registry = load_registry(repo_root, repo_root / REGISTRY_RELATIVE_PATH)
    selected = registry.select_suites(arguments.suite)
    suite_keys = {suite.suite_key for suite in selected}
    registered_test_files = registry.registered_test_files(
        [suite.suite_id for suite in selected]
    )
    planned_source_paths = planned_execution_source_paths(
        repo_root,
        registered_test_files,
    )
    validate_external_test_root_path_budget(
        arguments.test_root,
        suite_keys=suite_keys,
        reserved_artifact_paths=(
            *_RESERVED_RUN_ARTIFACT_PATHS,
            *(
                f"execution-source-files/{path}"
                for path in planned_source_paths
            ),
        ),
        self_hosted_reserved_artifact_paths=(
            _SELF_HOSTED_RESERVED_ARTIFACT_PATHS
        ),
    )
    source_manifest = build_execution_source_manifest(
        repo_root,
        registered_test_files,
    )
    validate_external_test_root_path_budget(
        arguments.test_root,
        suite_keys=suite_keys,
        reserved_artifact_paths=(
            *_RESERVED_RUN_ARTIFACT_PATHS,
            *(
                item["frozen_path"]
                for item in source_manifest["entries"]
            ),
        ),
        self_hosted_reserved_artifact_paths=(
            _SELF_HOSTED_RESERVED_ARTIFACT_PATHS
        ),
    )
    git_dir = _git_directory(repo_root)
    persisted_launch = _persisted_launch_binding()
    run_dir, selected_suite_ids = _prepare_run(
        repo_root,
        registry,
        arguments.suite,
        arguments.test_root,
    )
    source_manifest_path = run_dir / SOURCE_MANIFEST_RELATIVE_PATH
    freeze_execution_source_files(repo_root, run_dir, source_manifest)
    execution_root = run_dir / "execution-source-files"
    create_frozen_git_authority(
        repo_root,
        run_dir,
        execution_root,
        git_dir,
        source_manifest,
    )
    source_manifest_sha256 = write_json_exclusive(
        source_manifest_path,
        source_manifest,
    )
    (
        discovery,
        discovery_sha256,
        discovery_identity,
    ) = _independent_discovery(
        execution_root=execution_root,
        registry_path=execution_root / REGISTRY_RELATIVE_PATH,
        commit=source_manifest["commit"],
        suite_ids=selected_suite_ids,
        destination=run_dir / "discovery.json",
    )
    if discovery.get("registry_sha256") != registry.fingerprint:
        raise CliError(
            "discovery registry fingerprint changed before scheduling",
            failure_kind="discovery_failure",
        )
    runner_identity = process_execution_identity(os.getpid())
    project_marker_sha256 = sha256_file(
        run_dir.parent.parent / "project.json"
    )
    source_snapshot, source_snapshot_sha256 = create_source_snapshot(
        repo_root,
        run_dir,
        execution_root,
        source_manifest_path=source_manifest_path,
        source_manifest_sha256=source_manifest_sha256,
        source_manifest=source_manifest,
        expected_test_module_paths=registered_test_files,
        project=discovery["project"],
        registry_sha256=registry.fingerprint,
        project_marker_sha256=project_marker_sha256,
        persisted_run_id=os.environ.get("VIDEO2PDF_PERSISTED_RUN_ID"),
        persisted_run_nonce=os.environ.get(
            "VIDEO2PDF_PERSISTED_RUN_NONCE"
        ),
        runner_identity=runner_identity,
        modules=discovery["modules"],
    )
    source_binding = SourceBinding(
        execution_root=execution_root,
        source_manifest_sha256=source_manifest_sha256,
        source_snapshot_id=source_snapshot["source_snapshot_id"],
        source_snapshot_sha256=source_snapshot_sha256,
        module_inventory=module_inventory(discovery["modules"]),
        source_sha256_by_path={
            item["path"]: item["runtime_sha256"]
            for item in source_manifest["entries"]
        },
    )
    try:
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
            "source_manifest_path": str(source_manifest_path),
            "source_manifest_sha256": source_manifest_sha256,
            "source_snapshot_path": str(
                run_dir / SOURCE_SNAPSHOT_RELATIVE_PATH
            ),
            "source_snapshot_id": source_snapshot["source_snapshot_id"],
            "source_snapshot_sha256": source_snapshot_sha256,
            "suite_ids": selected_suite_ids,
            "run_dir": str(run_dir),
            "project_marker_sha256": project_marker_sha256,
            "persisted_run_id": os.environ.get(
                "VIDEO2PDF_PERSISTED_RUN_ID"
            ),
            "persisted_run_nonce": os.environ.get(
                "VIDEO2PDF_PERSISTED_RUN_NONCE"
            ),
            "persisted_target_identity": (
                persisted_launch["target_identity"]
                if persisted_launch is not None
                else None
            ),
            "persisted_supervisor_identity": (
                persisted_launch["supervisor_identity"]
                if persisted_launch is not None
                else None
            ),
            "requested_jobs": (
                arguments.jobs if arguments.command == "run" else None
            ),
            "timings_from": (
                str(timings_from) if timings_from is not None else None
            ),
            "runner_identity": runner_identity,
            "discovery_process": {
                **discovery_identity,
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
                    "discovery_process": discovery_identity,
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
                "discovery_process": discovery_identity,
            }
        )
        summary = run_modules(
            repo_root=repo_root,
            run_dir=run_dir,
            discovery=discovery,
            jobs=arguments.jobs,
            timings_from=timings_from,
            source_binding=source_binding,
        )
        summary_sha256 = sha256_file(run_dir / "summary.json")
    except Exception as error:
        summary_path = run_dir / "summary.json"
        summary_exists = summary_path.is_file()
        failure_kind = _post_snapshot_failure_kind(
            error,
            summary_exists=summary_exists,
        )
        failed_summary_sha256 = (
            sha256_file(summary_path) if summary_exists else None
        )
        finalization, finalization_sha256 = finalize_source_snapshot(
            repo_root,
            run_dir,
            source_snapshot=source_snapshot,
            source_snapshot_sha256=source_snapshot_sha256,
            source_manifest=source_manifest,
            expected_test_module_paths=registered_test_files,
            scheduler_success=False,
            scheduler_failure_kind=failure_kind,
            summary_sha256=failed_summary_sha256,
        )
        _emit(
            {
                "event": "project_test_run_complete",
                "success": False,
                "failure_kind": finalization["failure_kind"],
                "detail": f"{type(error).__name__}: {error}",
                "run_dir": str(run_dir),
                "summary_sha256": failed_summary_sha256,
                "discovery_sha256": discovery_sha256,
                "discovery_process": discovery_identity,
                "source_snapshot_id": source_snapshot[
                    "source_snapshot_id"
                ],
                "source_snapshot_sha256": source_snapshot_sha256,
                "run_finalization_path": str(
                    run_dir / RUN_FINALIZATION_RELATIVE_PATH
                ),
                "run_finalization_sha256": finalization_sha256,
            }
        )
        return 1
    finalization, finalization_sha256 = finalize_source_snapshot(
        repo_root,
        run_dir,
        source_snapshot=source_snapshot,
        source_snapshot_sha256=source_snapshot_sha256,
        source_manifest=source_manifest,
        expected_test_module_paths=registered_test_files,
        scheduler_success=summary["success"],
        scheduler_failure_kind=summary["failure_kind"],
        summary_sha256=summary_sha256,
    )
    _emit(
        {
            "event": "project_test_run_complete",
            "success": finalization["success"],
            "failure_kind": finalization["failure_kind"],
            "run_dir": str(run_dir),
            "summary_sha256": summary_sha256,
            "discovery_sha256": discovery_sha256,
            "discovery_process": discovery_identity,
            "source_snapshot_id": source_snapshot["source_snapshot_id"],
            "source_snapshot_sha256": source_snapshot_sha256,
            "run_finalization_path": str(
                run_dir / RUN_FINALIZATION_RELATIVE_PATH
            ),
            "run_finalization_sha256": finalization_sha256,
        }
    )
    return 0 if finalization["success"] else 1


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
    except ExternalRootPathBudgetError as error:
        failure_kind = "external_root_path_budget_failure"
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
    except SourceProvenanceError as error:
        failure_kind = "source_preflight_failure"
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

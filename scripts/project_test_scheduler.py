"""Bounded process scheduler for dynamically discovered unittest modules."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import unittest
from typing import Any, BinaryIO, Iterator, Mapping, Sequence

from scripts.project_test_results import (
    ResultIntegrityError,
    canonical_json_bytes,
    file_artifact_identity,
    read_module_result,
    sha256_file,
    write_bytes_exclusive,
    write_json_exclusive,
)
from scripts.project_test_external_root import (
    ExternalRootError,
    assert_safe_write_path,
    validate_owned_run_directory,
)
from scripts.project_test_source_provenance import (
    SourceProvenanceError,
    validate_source_snapshot_binding,
)
from src.video2pdf_persisted_command.process_identity import (
    execution_identity_is_complete,
    process_execution_identity,
)


MODULE_RESULT_SCHEMA_NAME = "video2pdf.project-test-module-result"
SUMMARY_SCHEMA_NAME = "video2pdf.project-test-summary"
TIMINGS_SCHEMA_NAME = "video2pdf.project-test-timings"
SCHEMA_VERSION = 2
MODULE_KEY_PATTERN = re.compile(r"[0-9a-f]{12}\Z")


class SchedulerError(RuntimeError):
    """The scheduler cannot prove complete closed-set execution."""

    def __init__(self, message: str, *, failure_kind: str = "result_integrity_failure"):
        super().__init__(message)
        self.failure_kind = failure_kind


class _WorkerImportPath(str):
    """Identity-bearing sys.path entry owned by one worker import."""


def validate_jobs(jobs: object) -> int:
    if type(jobs) is not int or not 1 <= jobs <= 4:
        raise SchedulerError("--jobs must be an integer in the range 1..4")
    return jobs


def _safe_artifact_path(run_dir: Path, candidate: Path) -> Path:
    """Apply the owned-run containment and reparse policy at an I/O boundary."""

    try:
        return assert_safe_write_path(run_dir, candidate)
    except ExternalRootError as error:
        raise SchedulerError(
            f"scheduler artifact path is unsafe: {error}",
            failure_kind="coordinator_failure",
        ) from error


def _flatten_suite(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten_suite(item)
        elif isinstance(item, unittest.TestCase):
            yield item
        else:
            raise SchedulerError(
                f"unsupported unittest item: {item!r}",
                failure_kind="import_failure",
            )


class _RecordingResult(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.executions: list[dict[str, Any]] = []
        self._started: dict[str, float] = {}

    def startTest(self, test: unittest.TestCase) -> None:
        self._started[test.id()] = time.monotonic()
        super().startTest(test)

    def _record(
        self,
        test: unittest.TestCase,
        status: str,
        detail: str | None = None,
    ) -> None:
        started = self._started.pop(test.id(), time.monotonic())
        item: dict[str, Any] = {
            "test_id": test.id(),
            "status": status,
            "duration_seconds": max(0.0, time.monotonic() - started),
        }
        if detail:
            item["detail"] = detail
        self.executions.append(item)

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(
        self,
        test: unittest.TestCase,
        err: tuple[type[BaseException], BaseException, Any],
    ) -> None:
        super().addFailure(test, err)
        self._record(test, "failed", self._exc_info_to_string(err, test))

    def addError(
        self,
        test: unittest.TestCase,
        err: tuple[type[BaseException], BaseException, Any],
    ) -> None:
        super().addError(test, err)
        self._record(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        self._record(test, "skipped", reason)

    def addExpectedFailure(
        self,
        test: unittest.TestCase,
        err: tuple[type[BaseException], BaseException, Any],
    ) -> None:
        super().addExpectedFailure(test, err)
        self._record(test, "expected_failure")

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        super().addUnexpectedSuccess(test)
        self._record(test, "unexpected_success")

    def addSubTest(
        self,
        test: unittest.TestCase,
        subtest: unittest.TestCase,
        err: tuple[type[BaseException], BaseException, Any] | None,
    ) -> None:
        super().addSubTest(test, subtest, err)
        if err is not None and test.id() in self._started:
            self._record(test, "failed", self._exc_info_to_string(err, test))

    def stopTest(self, test: unittest.TestCase) -> None:
        if test.id() in self._started:
            self._record(test, "failed", "test ended without a terminal result")
        super().stopTest(test)


def _load_assigned_suite(
    repo_root: Path,
    source_path: str,
    assigned_test_ids: Sequence[str],
) -> unittest.TestSuite:
    source = repo_root / Path(source_path)
    try:
        source.resolve(strict=True).relative_to(repo_root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as error:
        raise SchedulerError(
            f"assigned module source is outside the repository: {source_path}",
            failure_kind="import_failure",
        ) from error

    loader = unittest.TestLoader()
    previous = sys.modules.pop(source.stem, None)
    worker_import_path = _WorkerImportPath(str(source.parent))
    sys.path.insert(0, worker_import_path)
    try:
        suite = loader.discover(
            start_dir=str(source.parent),
            pattern=source.name,
            top_level_dir=str(source.parent),
        )
        tests = tuple(_flatten_suite(suite))
    finally:
        for index, entry in enumerate(sys.path):
            if entry is worker_import_path:
                sys.path.pop(index)
                break
        sys.modules.pop(source.stem, None)
        if previous is not None:
            sys.modules[source.stem] = previous
    if loader.errors or any(
        test.__class__.__name__ == "_FailedTest"
        or test.__class__.__module__ == "unittest.loader"
        for test in tests
    ):
        detail = " | ".join(loader.errors) or "unittest returned _FailedTest"
        raise SchedulerError(detail, failure_kind="import_failure")
    actual_ids = [test.id() for test in tests]
    if Counter(actual_ids) != Counter(assigned_test_ids):
        raise SchedulerError(
            "worker import inventory does not match assigned test IDs",
            failure_kind="result_integrity_failure",
        )
    by_id = {test.id(): test for test in tests}
    return unittest.TestSuite(by_id[test_id] for test_id in assigned_test_ids)


def run_module_worker(assignment_path: Path, result_path: Path) -> int:
    """Execute one assignment and exclusively publish its terminal result."""

    started = time.monotonic()
    assignment: dict[str, Any] = {}
    run_dir_text = os.environ.get("VIDEO2PDF_PROJECT_TEST_RUN_DIR")
    if not run_dir_text:
        print("worker run directory identity is missing", file=sys.stderr)
        return 1
    try:
        run_dir = validate_owned_run_directory(Path(run_dir_text))
        assignment_path = _safe_artifact_path(run_dir, assignment_path)
        result_path = _safe_artifact_path(run_dir, result_path)
    except (ExternalRootError, SchedulerError) as error:
        print(f"worker artifact path is invalid: {error}", file=sys.stderr)
        return 1
    try:
        assignment_path = _safe_artifact_path(run_dir, assignment_path)
        assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
        repo_root = Path(assignment["repo_root"])
        execution_root = Path(
            assignment.get("execution_root", assignment["repo_root"])
        )
        if assignment.get("source_snapshot_id") is not None:
            validate_source_snapshot_binding(run_dir, assignment)
        suite = _load_assigned_suite(
            execution_root,
            assignment["source_path"],
            assignment["test_ids"],
        )
        runner = unittest.TextTestRunner(
            stream=sys.stderr,
            verbosity=2,
            resultclass=_RecordingResult,
        )
        recorded = runner.run(suite)
        executions = recorded.executions
        failure_kind = None if recorded.wasSuccessful() else "test_failure"
        exit_code = 0 if failure_kind is None else 1
        detail = None
    except (SchedulerError, SourceProvenanceError) as error:
        executions = []
        failure_kind = (
            error.failure_kind
            if isinstance(error, SchedulerError)
            else "source_binding_failure"
        )
        exit_code = 1
        detail = str(error)
        traceback.print_exc(file=sys.stderr)
    except Exception as error:  # child boundary must always publish a result
        executions = []
        failure_kind = "import_failure"
        exit_code = 1
        detail = f"{type(error).__name__}: {error}"
        traceback.print_exc(file=sys.stderr)

    result: dict[str, Any] = {
        "schema_name": MODULE_RESULT_SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "module_key": assignment.get("module_key"),
        "suite_id": assignment.get("suite_id"),
        "source_path": assignment.get("source_path"),
        "assigned_test_ids": assignment.get("test_ids", []),
        "worker_launch_nonce": assignment.get("worker_launch_nonce"),
        "source_manifest_sha256": assignment.get(
            "source_manifest_sha256"
        ),
        "source_snapshot_id": assignment.get("source_snapshot_id"),
        "source_snapshot_sha256": assignment.get(
            "source_snapshot_sha256"
        ),
        "worker_identity": {
            **(process_execution_identity(os.getpid()) or {
                "pid": os.getpid(),
                "process_creation_identity": None,
                "executable_path": None,
                "executable_file_identity": None,
                "parent_pid": None,
                "parent_process_creation_identity": None,
                "observation_sha256": None,
            }),
        },
        "executions": executions,
        "failure_kind": failure_kind,
        "exit_code": exit_code,
        "duration_seconds": max(0.0, time.monotonic() - started),
    }
    if detail is not None:
        result["detail"] = detail
    try:
        result_path = _safe_artifact_path(run_dir, result_path)
        write_json_exclusive(result_path, result)
    except (ResultIntegrityError, SchedulerError):
        traceback.print_exc(file=sys.stderr)
        return 1
    return exit_code


def _canonical_discovery_path(
    repo_root: Path,
    value: Any,
    label: str,
    *,
    directory: bool,
) -> tuple[str, Path, PurePosixPath]:
    if not isinstance(value, str) or not value:
        raise SchedulerError(f"discovery {label} must be a non-empty path")
    parts = value.split("/")
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        "\\" in value
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or any(part in ("", ".", "..") for part in parts)
        or posix_path.as_posix() != value
    ):
        raise SchedulerError(
            f"discovery {label} must be a canonical repository-relative "
            "POSIX path"
        )
    absolute = repo_root.joinpath(*posix_path.parts)
    try:
        resolved = absolute.resolve(strict=True)
        resolved.relative_to(repo_root)
    except (FileNotFoundError, ValueError) as error:
        raise SchedulerError(
            f"discovery {label} does not identify a repository path: {value}"
        ) from error
    if directory and not resolved.is_dir():
        raise SchedulerError(
            f"discovery {label} must identify a directory: {value}"
        )
    if not directory and not resolved.is_file():
        raise SchedulerError(
            f"discovery {label} must identify a file: {value}"
        )
    return value, resolved, posix_path


def _validate_discovery(
    repo_root: Path,
    discovery: Mapping[str, Any],
) -> list[dict[str, Any]]:
    repo_root = repo_root.resolve(strict=True)
    modules = discovery.get("modules")
    if not isinstance(modules, list) or not modules:
        raise SchedulerError("discovery has no modules")
    keys: list[str] = []
    sources: list[str] = []
    all_ids: list[str] = []
    normalized: list[dict[str, Any]] = []
    for value in modules:
        if not isinstance(value, dict):
            raise SchedulerError("discovery module must be an object")
        try:
            key = value["module_key"]
            suite_id = value["suite_id"]
            root = value["root_path"]
            source = value["source_path"]
            test_ids = value["test_ids"]
            test_count = value["test_count"]
        except KeyError as error:
            raise SchedulerError(
                f"discovery module is missing field: {error.args[0]}"
            ) from error
        if (
            not isinstance(key, str)
            or MODULE_KEY_PATTERN.fullmatch(key) is None
        ):
            raise SchedulerError(
                "discovery module_key must be exactly 12 lowercase hex characters"
            )
        if (
            not isinstance(suite_id, str)
            or not suite_id
            or not isinstance(test_ids, list)
            or not all(isinstance(item, str) and item for item in test_ids)
            or type(test_count) is not int
            or test_count != len(test_ids)
        ):
            raise SchedulerError("discovery module fields are invalid")
        root, resolved_root, root_posix = _canonical_discovery_path(
            repo_root,
            root,
            "root_path",
            directory=True,
        )
        source, resolved_source, source_posix = _canonical_discovery_path(
            repo_root,
            source,
            "source_path",
            directory=False,
        )
        try:
            source_posix.relative_to(root_posix)
            resolved_source.relative_to(resolved_root)
        except ValueError as error:
            raise SchedulerError(
                "discovery source_path must be inside its root_path"
            ) from error
        module_name = source_posix.stem
        if any(
            test_id.partition(".")[0] != module_name
            and test_id != f"unittest.loader._FailedTest.{module_name}"
            for test_id in test_ids
        ):
            raise SchedulerError(
                "discovery test ID does not match its module source"
            )
        expected_key = hashlib.sha256(
            f"{suite_id}\0{source}".encode("utf-8")
        ).hexdigest()[:12]
        if key != expected_key:
            raise SchedulerError(
                "discovery module_key does not match suite/source identity"
            )
        keys.append(key)
        sources.append(source)
        all_ids.extend(test_ids)
        normalized.append(dict(value))
    if len(set(keys)) != len(keys):
        raise SchedulerError("discovery contains duplicate module keys")
    if len(set(sources)) != len(sources):
        raise SchedulerError("discovery contains duplicate module sources")
    duplicates = sorted(
        test_id for test_id, count in Counter(all_ids).items() if count > 1
    )
    if duplicates or discovery.get("duplicate_test_ids"):
        raise SchedulerError(
            "discovery contains duplicate test IDs: "
            + ", ".join(duplicates or discovery["duplicate_test_ids"])
        )
    if discovery.get("total_count") != len(all_ids):
        raise SchedulerError("discovery total_count mismatch")
    expected_set_hash = hashlib.sha256(
        canonical_json_bytes(sorted(all_ids))
    ).hexdigest()
    if discovery.get("test_id_set_sha256") != expected_set_hash:
        raise SchedulerError("discovery test-ID fingerprint mismatch")
    return normalized


def _load_timing_durations(
    timings_from: Path,
    discovery: Mapping[str, Any],
    modules: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    def reject_nonfinite_constant(value: str) -> None:
        raise ValueError(f"non-finite numeric constant: {value}")

    try:
        value = json.loads(
            timings_from.read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite_constant,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise SchedulerError(f"invalid timing provenance: {error}") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_name") != TIMINGS_SCHEMA_NAME
        or value.get("schema_version") not in (1, SCHEMA_VERSION)
        or value.get("project") != discovery.get("project")
        or value.get("suite_ids") != discovery.get("suite_ids")
        or not isinstance(value.get("modules"), list)
    ):
        raise SchedulerError("invalid timing provenance")
    expected = {
        item["module_key"]: item["source_path"]
        for item in modules
    }
    durations: dict[str, float] = {}
    for item in value["modules"]:
        if not isinstance(item, dict) or set(item) != {
            "module_key",
            "source_path",
            "duration_seconds",
        }:
            raise SchedulerError("invalid timing provenance module")
        key = item["module_key"]
        duration = item["duration_seconds"]
        if (
            key not in expected
            or expected[key] != item["source_path"]
            or key in durations
            or type(duration) not in (int, float)
            or not math.isfinite(duration)
            or duration < 0
        ):
            raise SchedulerError("invalid timing provenance module")
        durations[key] = float(duration)
    return durations


def _ordered_modules(
    modules: Sequence[dict[str, Any]],
    durations: Mapping[str, float] | None,
) -> list[dict[str, Any]]:
    if durations is None:
        return sorted(
            modules,
            key=lambda item: (
                -item["test_count"],
                item["suite_id"],
                item["source_path"],
            ),
        )
    return sorted(
        modules,
        key=lambda item: (
            -durations.get(item["module_key"], -1.0),
            -item["test_count"],
            item["suite_id"],
            item["source_path"],
        ),
    )


def _append_event(
    events: list[dict[str, Any]],
    event: str,
    module: Mapping[str, Any],
    sequence: int,
    **extra: Any,
) -> None:
    value = {
        "sequence": sequence,
        "event": event,
        "module_key": module["module_key"],
        "suite_id": module["suite_id"],
        "source_path": module["source_path"],
        "time_unix_ns": time.time_ns(),
    }
    value.update(extra)
    events.append(value)


def _drain_pipe(
    pipe: BinaryIO,
    log: BinaryIO,
    forwarded: BinaryIO,
    lock: threading.Lock,
    errors: list[str],
) -> None:
    try:
        while chunk := pipe.read(64 * 1024):
            log.write(chunk)
            log.flush()
            try:
                with lock:
                    forwarded.write(chunk)
                    forwarded.flush()
            except Exception as error:
                errors.append(
                    f"output forwarding failed: {type(error).__name__}: {error}"
                )
    except Exception as error:
        errors.append(
            f"output drain failed: {type(error).__name__}: {error}"
        )
    finally:
        try:
            pipe.close()
            log.flush()
        except Exception as error:
            errors.append(
                f"output drain finalization failed: "
                f"{type(error).__name__}: {error}"
            )


@dataclass
class _ActiveModule:
    module: dict[str, Any]
    process: subprocess.Popen[bytes]
    assignment_sha256: str
    assignment_file_identity: dict[str, int]
    result_path: Path
    stdout_path: Path
    stderr_path: Path
    stdout_handle: BinaryIO
    stderr_handle: BinaryIO
    drain_threads: tuple[threading.Thread, threading.Thread]
    drain_errors: list[str]
    worker_launch_nonce: str
    worker_execution_identity: dict[str, Any] | None


_DIRECT_WORKER_CLEANUP_TIMEOUT_SECONDS = 1.0


def _fingerprint_if_present(run_dir: Path, path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        path = _safe_artifact_path(run_dir, path)
        return sha256_file(path)
    except (ResultIntegrityError, SchedulerError):
        return None


def _cleanup_direct_worker(launched: _ActiveModule) -> str | None:
    """Boundedly settle one direct worker after a coordinator exception.

    This is coordinator lifecycle cleanup. It is deliberately separate from
    test fail-fast behavior and does not attempt process-tree termination.
    """

    errors: list[str] = []
    process = launched.process
    if process.poll() is None:
        try:
            process.terminate()
        except Exception as error:
            errors.append(
                f"worker terminate failed: {type(error).__name__}: {error}"
            )
        try:
            process.wait(timeout=_DIRECT_WORKER_CLEANUP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        except Exception as error:
            errors.append(
                f"worker wait failed: {type(error).__name__}: {error}"
            )
        if process.poll() is None:
            try:
                process.kill()
            except Exception as error:
                errors.append(
                    f"worker kill failed: {type(error).__name__}: {error}"
                )
            try:
                process.wait(timeout=_DIRECT_WORKER_CLEANUP_TIMEOUT_SECONDS)
            except Exception as error:
                errors.append(
                    f"worker post-kill wait failed: "
                    f"{type(error).__name__}: {error}"
                )

    for thread in launched.drain_threads:
        try:
            thread.join(timeout=_DIRECT_WORKER_CLEANUP_TIMEOUT_SECONDS)
            if thread.is_alive():
                errors.append("output drain thread did not settle")
        except Exception as error:
            errors.append(
                f"output drain join failed: {type(error).__name__}: {error}"
            )
    for handle in (launched.stdout_handle, launched.stderr_handle):
        try:
            handle.close()
        except Exception as error:
            errors.append(
                f"log close failed: {type(error).__name__}: {error}"
            )
    errors.extend(launched.drain_errors)
    return " | ".join(errors) if errors else None


def _binary_stream(stream: BinaryIO | None, fallback: Any) -> BinaryIO:
    if stream is not None:
        return stream
    value = getattr(fallback, "buffer", None)
    return value if value is not None else io.BytesIO()


def _launch_module(
    *,
    repo_root: Path,
    execution_root: Path,
    run_dir: Path,
    module: dict[str, Any],
    stdout: BinaryIO,
    stderr: BinaryIO,
    stdout_lock: threading.Lock,
    stderr_lock: threading.Lock,
    child_environment: Mapping[str, str] | None,
    source_manifest_sha256: str | None,
    source_snapshot_id: str | None,
    source_snapshot_sha256: str | None,
    module_inventory_sha256: str | None,
    source_sha256: str | None,
) -> _ActiveModule:
    module_dir = _safe_artifact_path(run_dir, run_dir / "modules")
    logs_dir = _safe_artifact_path(run_dir, run_dir / "logs")
    generated_dir = _safe_artifact_path(
        run_dir,
        run_dir / "generated" / module["module_key"],
    )
    generated_dir = _safe_artifact_path(run_dir, generated_dir)
    generated_dir.mkdir(parents=True, exist_ok=False)
    assignment_path = _safe_artifact_path(
        run_dir,
        module_dir / f"{module['module_key']}.assignment.json",
    )
    result_path = _safe_artifact_path(
        run_dir,
        module_dir / f"{module['module_key']}.result.json",
    )
    worker_launch_nonce = secrets.token_hex(32)
    assignment = {
        "schema_name": "video2pdf.project-test-module-assignment",
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(repo_root),
        "execution_root": str(execution_root),
        "module_key": module["module_key"],
        "suite_id": module["suite_id"],
        "source_path": module["source_path"],
        "test_ids": module["test_ids"],
        "worker_launch_nonce": worker_launch_nonce,
        "source_manifest_sha256": source_manifest_sha256,
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_sha256": source_snapshot_sha256,
        "module_inventory_sha256": module_inventory_sha256,
        "source_sha256": source_sha256,
    }
    assignment_path = _safe_artifact_path(run_dir, assignment_path)
    assignment_sha256 = write_json_exclusive(assignment_path, assignment)
    assignment_file_identity = file_artifact_identity(assignment_path)
    stdout_path = _safe_artifact_path(
        run_dir,
        logs_dir / f"{module['module_key']}.stdout.log",
    )
    stderr_path = _safe_artifact_path(
        run_dir,
        logs_dir / f"{module['module_key']}.stderr.log",
    )
    process: subprocess.Popen[bytes] | None = None
    drain_threads: list[threading.Thread] = []
    with ExitStack() as log_handles:
        stdout_path = _safe_artifact_path(run_dir, stdout_path)
        stdout_handle = log_handles.enter_context(stdout_path.open("xb"))
        stderr_path = _safe_artifact_path(run_dir, stderr_path)
        stderr_handle = log_handles.enter_context(stderr_path.open("xb"))
        environment = os.environ.copy()
        if child_environment is not None:
            environment.update(child_environment)
        environment.update(
            {
                "VIDEO2PDF_PROJECT_TEST_RUN_DIR": str(run_dir),
                "VIDEO2PDF_PROJECT_TEST_SUITE_ID": module["suite_id"],
                "VIDEO2PDF_PROJECT_TEST_MODULE_KEY": module["module_key"],
                "TEMP": str(generated_dir),
                "TMP": str(generated_dir),
                "TMPDIR": str(generated_dir),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        environment["PYTHONPATH"] = str(execution_root)
        command = [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(execution_root / "scripts" / "project_test_scheduler.py"),
            "_worker",
            "--assignment",
            str(assignment_path),
            "--result",
            str(result_path),
        ]
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        )
        try:
            process = subprocess.Popen(
                command,
                cwd=execution_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
            worker_execution_identity = process_execution_identity(
                process.pid
            )
            if process.stdout is None or process.stderr is None:
                raise SchedulerError("worker pipes were not created")
            drain_errors: list[str] = []
            stdout_thread = threading.Thread(
                target=_drain_pipe,
                args=(
                    process.stdout,
                    stdout_handle,
                    stdout,
                    stdout_lock,
                    drain_errors,
                ),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=_drain_pipe,
                args=(
                    process.stderr,
                    stderr_handle,
                    stderr,
                    stderr_lock,
                    drain_errors,
                ),
                daemon=True,
            )
            stdout_thread.start()
            drain_threads.append(stdout_thread)
            stderr_thread.start()
            drain_threads.append(stderr_thread)
        except BaseException:
            if process is not None:
                log_handles.pop_all()
                partial = _ActiveModule(
                    module=module,
                    process=process,
                    assignment_sha256=assignment_sha256,
                    assignment_file_identity=assignment_file_identity,
                    result_path=result_path,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    stdout_handle=stdout_handle,
                    stderr_handle=stderr_handle,
                    drain_threads=tuple(drain_threads),
                    drain_errors=[],
                    worker_launch_nonce=worker_launch_nonce,
                    worker_execution_identity=worker_execution_identity,
                )
                _cleanup_direct_worker(partial)
            raise
        log_handles.pop_all()
    return _ActiveModule(
        module=module,
        process=process,
        assignment_sha256=assignment_sha256,
        assignment_file_identity=assignment_file_identity,
        result_path=result_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_handle=stdout_handle,
        stderr_handle=stderr_handle,
        drain_threads=(stdout_thread, stderr_thread),
        drain_errors=drain_errors,
        worker_launch_nonce=worker_launch_nonce,
        worker_execution_identity=worker_execution_identity,
    )


def _validate_module_result(
    module: Mapping[str, Any],
    value: Mapping[str, Any],
    process_exit_code: int,
    *,
    worker_launch_nonce: str,
    source_manifest_sha256: str | None,
    source_snapshot_id: str | None,
    source_snapshot_sha256: str | None,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    worker_identity = value.get("worker_identity")
    if (
        value.get("schema_name") != MODULE_RESULT_SCHEMA_NAME
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("module_key") != module["module_key"]
        or value.get("suite_id") != module["suite_id"]
        or value.get("source_path") != module["source_path"]
        or value.get("assigned_test_ids") != module["test_ids"]
        or value.get("worker_launch_nonce") != worker_launch_nonce
        or value.get("source_manifest_sha256") != source_manifest_sha256
        or value.get("source_snapshot_id") != source_snapshot_id
        or value.get("source_snapshot_sha256")
        != source_snapshot_sha256
        or not execution_identity_is_complete(worker_identity)
        or value.get("exit_code") != process_exit_code
        or not isinstance(value.get("executions"), list)
    ):
        raise ResultIntegrityError("module result identity mismatch")
    executions = value["executions"]
    execution_ids = [
        item.get("test_id") for item in executions if isinstance(item, dict)
    ]
    if len(execution_ids) != len(executions) or any(
        not isinstance(test_id, str) for test_id in execution_ids
    ):
        raise ResultIntegrityError("module result execution shape mismatch")
    failure_kind = value.get("failure_kind")
    if failure_kind not in (
        None,
        "test_failure",
        "import_failure",
        "result_integrity_failure",
        "source_binding_failure",
    ):
        raise ResultIntegrityError("unexpected terminal failure kind")
    if failure_kind in (
        "import_failure",
        "result_integrity_failure",
        "source_binding_failure",
    ):
        if execution_ids:
            raise ResultIntegrityError(
                "pre-execution failure reported executed tests"
            )
        return list(executions), failure_kind, worker_identity
    if Counter(execution_ids) != Counter(module["test_ids"]):
        raise ResultIntegrityError("module result test execution mismatch")
    return list(executions), failure_kind, worker_identity


def run_modules(
    *,
    repo_root: Path,
    run_dir: Path,
    discovery: Mapping[str, Any],
    jobs: int = 4,
    timings_from: Path | None = None,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
    child_environment: Mapping[str, str] | None = None,
    source_manifest_sha256: str | None = None,
    source_snapshot_id: str | None = None,
    source_snapshot_sha256: str | None = None,
    module_inventory_sha256: str | None = None,
    source_sha256_by_path: Mapping[str, str] | None = None,
    execution_root: Path | None = None,
) -> dict[str, Any]:
    """Run every discovered module once and write immutable result artifacts."""

    validated_jobs = validate_jobs(jobs)
    repo_root = repo_root.resolve(strict=True)
    try:
        run_dir = validate_owned_run_directory(run_dir)
    except ExternalRootError as error:
        raise SchedulerError(
            f"run directory ownership is invalid: {error}",
            failure_kind="coordinator_failure",
        ) from error
    execution_root = (
        repo_root if execution_root is None else execution_root.resolve(strict=True)
    )
    modules = _validate_discovery(execution_root, discovery)
    durations = (
        _load_timing_durations(timings_from, discovery, modules)
        if timings_from is not None
        else None
    )
    for directory in ("modules", "logs", "generated"):
        artifact_directory = _safe_artifact_path(
            run_dir,
            run_dir / directory,
        )
        artifact_directory = _safe_artifact_path(run_dir, artifact_directory)
        artifact_directory.mkdir(exist_ok=False)
    pending = _ordered_modules(modules, durations)
    events: list[dict[str, Any]] = []
    sequence = 0
    for module in pending:
        sequence += 1
        _append_event(events, "queued", module, sequence)

    out = _binary_stream(stdout, sys.stdout)
    err = _binary_stream(stderr, sys.stderr)
    stdout_lock = threading.Lock()
    stderr_lock = threading.Lock()
    active: dict[str, _ActiveModule] = {}
    terminal: dict[str, dict[str, Any]] = {}
    started_module_keys: set[str] = set()
    peak = 0
    scheduler_exception: BaseException | None = None
    scheduler_traceback = None

    try:
        while pending or active:
            while pending and len(active) < validated_jobs:
                module = pending.pop(0)
                sequence += 1
                try:
                    launched = _launch_module(
                        repo_root=repo_root,
                        execution_root=execution_root,
                        run_dir=run_dir,
                        module=module,
                        stdout=out,
                        stderr=err,
                        stdout_lock=stdout_lock,
                        stderr_lock=stderr_lock,
                        child_environment=child_environment,
                        source_manifest_sha256=source_manifest_sha256,
                        source_snapshot_id=source_snapshot_id,
                        source_snapshot_sha256=source_snapshot_sha256,
                        module_inventory_sha256=module_inventory_sha256,
                        source_sha256=(
                            source_sha256_by_path.get(module["source_path"])
                            if source_sha256_by_path is not None
                            else None
                        ),
                    )
                except Exception as error:
                    assignment_path = (
                        run_dir
                        / "modules"
                        / f"{module['module_key']}.assignment.json"
                    )
                    stdout_path = (
                        run_dir / "logs" / f"{module['module_key']}.stdout.log"
                    )
                    stderr_path = (
                        run_dir / "logs" / f"{module['module_key']}.stderr.log"
                    )
                    terminal[module["module_key"]] = {
                        "module": module,
                        "failure_kind": "launch_failure",
                        "detail": f"{type(error).__name__}: {error}",
                        "executions": [],
                        "exit_code": None,
                        "assignment_sha256": _fingerprint_if_present(
                            run_dir,
                            assignment_path
                        ),
                        "result_sha256": None,
                        "stdout_sha256": _fingerprint_if_present(
                            run_dir,
                            stdout_path,
                        ),
                        "stderr_sha256": _fingerprint_if_present(
                            run_dir,
                            stderr_path,
                        ),
                        "duration_seconds": 0.0,
                        "worker_launch_nonce": None,
                        "worker_identity": None,
                        "worker_launcher_identity": None,
                        "artifact_identities": {
                            "assignment": (
                                file_artifact_identity(assignment_path)
                                if assignment_path.is_file()
                                else None
                            ),
                            "result": None,
                            "stdout": (
                                file_artifact_identity(stdout_path)
                                if stdout_path.is_file()
                                else None
                            ),
                            "stderr": (
                                file_artifact_identity(stderr_path)
                                if stderr_path.is_file()
                                else None
                            ),
                        },
                    }
                    _append_event(
                        events,
                        "launch_failed",
                        module,
                        sequence,
                        failure_kind="launch_failure",
                        exit_code=None,
                    )
                    continue
                active[module["module_key"]] = launched
                started_module_keys.add(module["module_key"])
                _append_event(
                    events,
                    "started",
                    module,
                    sequence,
                    worker_launcher_identity=(
                        launched.worker_execution_identity
                    ),
                    artifact_identities={
                        "assignment": launched.assignment_file_identity,
                    },
                    worker_launch_nonce=launched.worker_launch_nonce,
                    source_manifest_sha256=source_manifest_sha256,
                )
                peak = max(peak, len(active))

            finished_keys = [
                key
                for key, launched in active.items()
                if launched.process.poll() is not None
            ]
            if not finished_keys:
                time.sleep(0.005)
                continue
            for key in finished_keys:
                launched = active[key]
                for thread in launched.drain_threads:
                    thread.join()
                launched.stdout_handle.close()
                launched.stderr_handle.close()
                exit_code = launched.process.returncode
                failure_kind: str | None = None
                detail: str | None = None
                executions: list[dict[str, Any]] = []
                result_sha256: str | None = None
                duration_seconds = 0.0
                worker_identity = {
                    **(
                        launched.worker_execution_identity
                        or {
                            "pid": launched.process.pid,
                            "process_creation_identity": None,
                            "executable_path": None,
                            "executable_file_identity": None,
                            "parent_pid": None,
                            "parent_process_creation_identity": None,
                            "observation_sha256": None,
                        }
                    ),
                }
                try:
                    launched.result_path = _safe_artifact_path(
                        run_dir,
                        launched.result_path,
                    )
                    result_sha256 = sha256_file(launched.result_path)
                    launched.result_path = _safe_artifact_path(
                        run_dir,
                        launched.result_path,
                    )
                    value = read_module_result(
                        launched.result_path,
                        result_sha256,
                    )
                    duration = value.get("duration_seconds")
                    if (
                        type(duration) not in (int, float)
                        or not math.isfinite(duration)
                        or duration < 0
                    ):
                        raise ResultIntegrityError(
                            "module result duration is invalid"
                        )
                    duration_seconds = float(duration)
                    (
                        executions,
                        failure_kind,
                        worker_identity,
                    ) = _validate_module_result(
                        launched.module,
                        value,
                        exit_code,
                        worker_launch_nonce=launched.worker_launch_nonce,
                        source_manifest_sha256=source_manifest_sha256,
                        source_snapshot_id=source_snapshot_id,
                        source_snapshot_sha256=source_snapshot_sha256,
                    )
                    launcher_identity = launched.worker_execution_identity
                    if (
                        not isinstance(launcher_identity, dict)
                        or worker_identity["parent_pid"]
                        != launcher_identity["pid"]
                        or worker_identity[
                            "parent_process_creation_identity"
                        ]
                        != launcher_identity["process_creation_identity"]
                    ):
                        raise ResultIntegrityError(
                            "worker parent identity differs from scheduler launch identity"
                        )
                    if launched.drain_errors:
                        raise ResultIntegrityError(
                            " | ".join(launched.drain_errors)
                        )
                except ResultIntegrityError as error:
                    failure_kind = "result_integrity_failure"
                    detail = str(error)
                sequence += 1
                terminal[key] = {
                    "module": launched.module,
                    "failure_kind": failure_kind,
                    "detail": detail,
                    "executions": executions,
                    "exit_code": exit_code,
                    "assignment_sha256": launched.assignment_sha256,
                    "result_sha256": result_sha256,
                    "stdout_sha256": sha256_file(
                        _safe_artifact_path(run_dir, launched.stdout_path)
                    ),
                    "stderr_sha256": sha256_file(
                        _safe_artifact_path(run_dir, launched.stderr_path)
                    ),
                    "duration_seconds": duration_seconds,
                    "worker_launch_nonce": launched.worker_launch_nonce,
                    "worker_identity": worker_identity,
                    "worker_launcher_identity": (
                        launched.worker_execution_identity
                    ),
                    "artifact_identities": {
                        "assignment": launched.assignment_file_identity,
                        "result": (
                            file_artifact_identity(launched.result_path)
                            if launched.result_path.is_file()
                            else None
                        ),
                        "stdout": file_artifact_identity(
                            launched.stdout_path
                        ),
                        "stderr": file_artifact_identity(
                            launched.stderr_path
                        ),
                    },
                }
                _append_event(
                    events,
                    "completed",
                    launched.module,
                    sequence,
                    failure_kind=failure_kind,
                    exit_code=exit_code,
                    worker_identity=worker_identity,
                    worker_launcher_identity=(
                        launched.worker_execution_identity
                    ),
                    worker_launch_nonce=launched.worker_launch_nonce,
                    source_manifest_sha256=source_manifest_sha256,
                    artifact_identities=terminal[key][
                        "artifact_identities"
                    ],
                )
                active.pop(key)
    except BaseException as error:
        scheduler_exception = error
        scheduler_traceback = error.__traceback__
    finally:
        if active:
            cleanup_failure_kind = (
                "result_integrity_failure"
                if isinstance(scheduler_exception, ResultIntegrityError)
                else "coordinator_failure"
            )
            exception_detail = (
                f"{type(scheduler_exception).__name__}: {scheduler_exception}"
                if scheduler_exception is not None
                else "coordinator exited with active workers"
            )
            for key, launched in list(active.items()):
                cleanup_detail = _cleanup_direct_worker(launched)
                sequence += 1
                terminal[key] = {
                    "module": launched.module,
                    "failure_kind": cleanup_failure_kind,
                    "detail": "coordinator exception cleanup: "
                    + exception_detail
                    + (f" | {cleanup_detail}" if cleanup_detail else ""),
                    "executions": [],
                    "exit_code": launched.process.returncode,
                    "assignment_sha256": launched.assignment_sha256,
                    "result_sha256": _fingerprint_if_present(
                        run_dir,
                        launched.result_path
                    ),
                    "stdout_sha256": _fingerprint_if_present(
                        run_dir,
                        launched.stdout_path
                    ),
                    "stderr_sha256": _fingerprint_if_present(
                        run_dir,
                        launched.stderr_path
                    ),
                    "duration_seconds": 0.0,
                    "worker_launch_nonce": launched.worker_launch_nonce,
                    "worker_identity": {
                        **(
                            launched.worker_execution_identity
                            or {
                                "pid": launched.process.pid,
                                "process_creation_identity": None,
                                "executable_path": None,
                                "executable_file_identity": None,
                                "parent_pid": None,
                                "parent_process_creation_identity": None,
                                "observation_sha256": None,
                            }
                        ),
                    },
                    "worker_launcher_identity": (
                        launched.worker_execution_identity
                    ),
                    "artifact_identities": {
                        "assignment": launched.assignment_file_identity,
                        "result": (
                            file_artifact_identity(launched.result_path)
                            if launched.result_path.is_file()
                            else None
                        ),
                        "stdout": (
                            file_artifact_identity(launched.stdout_path)
                            if launched.stdout_path.is_file()
                            else None
                        ),
                        "stderr": (
                            file_artifact_identity(launched.stderr_path)
                            if launched.stderr_path.is_file()
                            else None
                        ),
                    },
                }
                _append_event(
                    events,
                    "completed",
                    launched.module,
                    sequence,
                    failure_kind=cleanup_failure_kind,
                    exit_code=launched.process.returncode,
                    coordinator_cleanup=True,
                )
                active.pop(key)

    if scheduler_exception is not None:
        cleanup_failure_kind = (
            "result_integrity_failure"
            if isinstance(scheduler_exception, ResultIntegrityError)
            else "coordinator_failure"
        )
        while pending:
            module = pending.pop(0)
            sequence += 1
            terminal[module["module_key"]] = {
                "module": module,
                "failure_kind": cleanup_failure_kind,
                "detail": "not launched after coordinator exception: "
                f"{type(scheduler_exception).__name__}: {scheduler_exception}",
                "executions": [],
                "exit_code": None,
                "assignment_sha256": None,
                "result_sha256": None,
                "stdout_sha256": None,
                "stderr_sha256": None,
                "duration_seconds": 0.0,
                "worker_launch_nonce": None,
                "worker_identity": None,
                "worker_launcher_identity": None,
                "artifact_identities": {
                    "assignment": None,
                    "result": None,
                    "stdout": None,
                    "stderr": None,
                },
            }
            _append_event(
                events,
                "completed",
                module,
                sequence,
                failure_kind=cleanup_failure_kind,
                exit_code=None,
                coordinator_cleanup=True,
            )

    expected_ids = [
        test_id for module in modules for test_id in module["test_ids"]
    ]
    executed_ids = [
        item["test_id"]
        for outcome in terminal.values()
        for item in outcome["executions"]
    ]
    executed_counts = Counter(executed_ids)
    missing_ids = sorted(set(expected_ids) - set(executed_ids))
    multiply_executed = sorted(
        test_id for test_id, count in executed_counts.items() if count > 1
    )
    unassigned_ids = sorted(set(executed_ids) - set(expected_ids))
    duplicate_ids = sorted(
        test_id for test_id, count in Counter(expected_ids).items() if count > 1
    )
    integrity_failed = bool(
        missing_ids or multiply_executed or unassigned_ids or duplicate_ids
    )
    failure_kinds = {
        outcome["failure_kind"]
        for outcome in terminal.values()
        if outcome["failure_kind"] is not None
    }
    if "coordinator_failure" in failure_kinds:
        overall_failure = "coordinator_failure"
    elif "source_binding_failure" in failure_kinds:
        overall_failure = "source_binding_failure"
    elif "result_integrity_failure" in failure_kinds:
        overall_failure = "result_integrity_failure"
    elif "launch_failure" in failure_kinds:
        overall_failure = "launch_failure"
    elif "import_failure" in failure_kinds:
        overall_failure = "import_failure"
    elif integrity_failed:
        overall_failure = "result_integrity_failure"
    elif "test_failure" in failure_kinds:
        overall_failure = "test_failure"
    else:
        overall_failure = None

    module_summaries = []
    for key in sorted(terminal):
        outcome = terminal[key]
        module = outcome["module"]
        executions = []
        for execution in sorted(
            outcome["executions"], key=lambda item: item["test_id"]
        ):
            stable_execution = {
                key: value
                for key, value in execution.items()
                if key != "duration_seconds"
            }
            executions.append(stable_execution)
        module_summaries.append(
            {
                "module_key": key,
                "suite_id": module["suite_id"],
                "source_path": module["source_path"],
                "test_ids": sorted(module["test_ids"]),
                "executions": executions,
                "failure_kind": outcome["failure_kind"],
                "detail": outcome["detail"],
                "exit_code": outcome["exit_code"],
                "assignment_sha256": outcome["assignment_sha256"],
                "result_sha256": outcome["result_sha256"],
                "stdout_sha256": outcome["stdout_sha256"],
                "stderr_sha256": outcome["stderr_sha256"],
                "worker_launch_nonce": outcome["worker_launch_nonce"],
                "source_manifest_sha256": source_manifest_sha256,
                "source_snapshot_id": source_snapshot_id,
                "source_snapshot_sha256": source_snapshot_sha256,
                "worker_identity": outcome["worker_identity"],
                "worker_launcher_identity": outcome[
                    "worker_launcher_identity"
                ],
                "artifact_identities": outcome["artifact_identities"],
            }
        )
    coverage = {
        "discovered": len(expected_ids),
        "assigned": len(expected_ids),
        "started": sum(
            len(module["test_ids"])
            for module in modules
            if module["module_key"] in started_module_keys
        ),
        "terminal": len(expected_ids)
        if len(terminal) == len(modules)
        else sum(
            len(terminal[key]["module"]["test_ids"]) for key in terminal
        ),
        "module_count": len(modules),
        "executed_test_ids": len(executed_ids),
        "missing_test_ids": missing_ids,
        "duplicate_test_ids": duplicate_ids,
        "unassigned_test_ids": unassigned_ids,
        "multiply_executed_test_ids": multiply_executed,
    }
    summary = {
        "schema_name": SUMMARY_SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "project": discovery.get("project"),
        "commit": discovery.get("commit"),
        "suite_ids": discovery.get("suite_ids"),
        "requested_jobs": validated_jobs,
        "observed_peak_concurrency": peak,
        "failure_kind": overall_failure,
        "success": overall_failure is None,
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_sha256": source_snapshot_sha256,
        "coverage": coverage,
        "modules": module_summaries,
    }
    timing_manifest = {
        "schema_name": TIMINGS_SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "project": discovery.get("project"),
        "commit": discovery.get("commit"),
        "suite_ids": discovery.get("suite_ids"),
        "modules": [
            {
                "module_key": key,
                "source_path": terminal[key]["module"]["source_path"],
                "duration_seconds": terminal[key]["duration_seconds"],
            }
            for key in sorted(terminal)
        ],
    }
    for event in events:
        event["source_snapshot_id"] = source_snapshot_id
        event["source_snapshot_sha256"] = source_snapshot_sha256
    # events.jsonl intentionally remains line-oriented JSON, one event per line.
    event_bytes = b"".join(canonical_json_bytes(event) for event in events)
    events_path = _safe_artifact_path(run_dir, run_dir / "events.jsonl")
    write_bytes_exclusive(events_path, event_bytes)
    timings_path = _safe_artifact_path(run_dir, run_dir / "timings.json")
    write_json_exclusive(timings_path, timing_manifest)
    summary_path = _safe_artifact_path(run_dir, run_dir / "summary.json")
    write_json_exclusive(summary_path, summary)
    if scheduler_exception is not None:
        raise scheduler_exception.with_traceback(scheduler_traceback)
    return summary


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker = subparsers.add_parser("_worker")
    worker.add_argument("--assignment", required=True, type=Path)
    worker.add_argument("--result", required=True, type=Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "_worker":
        return run_module_worker(arguments.assignment, arguments.result)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())

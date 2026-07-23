"""Validate the fixed, one-time project test runner Promotion Report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.project_test_results import canonical_json_bytes, sha256_file


REPORT_RELATIVE_PATH = Path("evidence/project-test-runner/promotion-report.json")
SCHEMA_RELATIVE_PATH = Path(
    "schemas/project-test-promotion-report.v1.schema.json"
)
SCHEMA_NAME = "video2pdf.project-test-promotion-report"
SCHEMA_VERSION = 1
HISTORICAL_BASELINE = {
    "implementation_commit": "18f78fad0be5a66d2da6250dc268bc8de81fdbcc",
    "test_count": 474,
    "result": "OK",
    "test_duration_seconds": 4847.218,
    "persisted_elapsed_seconds": 4849.187,
}
TOP_FIELDS = frozenset(
    {
        "schema_name",
        "schema_version",
        "issue",
        "historical_performance_baseline",
        "final_issue9_closed_set",
        "implementation",
        "promotion_closed_set",
        "parallel_runs",
        "semantic_parity",
        "migration_review",
        "performance",
        "promotion_fingerprint",
        "cutover_authorized",
    }
)
RUN_FIELDS = frozenset(
    {
        "run_dir",
        "discovery_path",
        "discovery_sha256",
        "summary_path",
        "summary_sha256",
        "persisted_run_dir",
        "persisted_status_path",
        "persisted_status_sha256",
        "persisted_exit_code_path",
        "persisted_exit_code_sha256",
        "persisted_command_path",
        "persisted_command_sha256",
        "persisted_stdout_path",
        "persisted_stdout_sha256",
    }
)


class PromotionValidationError(RuntimeError):
    """Promotion evidence does not prove the one-time cutover gate."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PromotionValidationError(f"{label} must be an object")
    return value


def _fields(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise PromotionValidationError(
            f"{label} has unknown field: {', '.join(unknown)}"
        )
    if missing:
        raise PromotionValidationError(
            f"{label} is missing field: {', '.join(missing)}"
        )


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PromotionValidationError(f"{label} must be a lowercase SHA-256")
    return value


def _commit(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PromotionValidationError(f"{label} must be a full Git commit")
    return value


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PromotionValidationError(f"{label} is unreadable: {path}") from error
    return _object(value, label)


def _bound_path(
    repo_root: Path,
    raw_path: Any,
    expected_sha256: Any,
    label: str,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(raw_path, str) or not raw_path:
        raise PromotionValidationError(f"{label} path must be a non-empty string")
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root / path
    try:
        path = path.resolve(strict=True)
    except OSError as error:
        raise PromotionValidationError(f"{label} path is missing: {path}") from error
    fingerprint = _sha256(expected_sha256, f"{label}.sha256")
    if sha256_file(path) != fingerprint:
        raise PromotionValidationError(f"{label} fingerprint mismatch")
    return path, _load_json(path, label)


def _bound_file(
    repo_root: Path,
    raw_path: Any,
    expected_sha256: Any,
    label: str,
) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise PromotionValidationError(f"{label} path must be a non-empty string")
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root / path
    try:
        path = path.resolve(strict=True)
    except OSError as error:
        raise PromotionValidationError(f"{label} path is missing: {path}") from error
    fingerprint = _sha256(expected_sha256, f"{label}.sha256")
    if sha256_file(path) != fingerprint:
        raise PromotionValidationError(f"{label} fingerprint mismatch")
    return path


def _closed_set(
    value: Any,
    label: str,
    *,
    include_suites: bool,
    include_evidence: bool = False,
) -> dict[str, Any]:
    item = _object(value, label)
    expected = frozenset({"commit", "test_count", "test_id_set_sha256"})
    # promotion_closed_set derives its commit from implementation and omits it.
    if include_suites:
        expected = frozenset({"suite_ids", "test_count", "test_id_set_sha256"})
    elif include_evidence:
        expected = frozenset(
            {
                "commit",
                "test_count",
                "test_id_set_sha256",
                "evidence_path",
                "evidence_sha256",
            }
        )
    _fields(item, expected, label)
    if not include_suites:
        _commit(item["commit"], f"{label}.commit")
    if type(item["test_count"]) is not int or item["test_count"] < 1:
        raise PromotionValidationError(f"{label}.test_count must be positive")
    _sha256(item["test_id_set_sha256"], f"{label}.test_id_set_sha256")
    if include_evidence:
        _sha256(item["evidence_sha256"], f"{label}.evidence_sha256")
    if include_suites and item["suite_ids"] != ["video-workflow"]:
        raise PromotionValidationError(
            "promotion closed set must select video-workflow"
        )
    return item


def _validate_migration_review(value: dict[str, Any]) -> None:
    try:
        checks = value["migration_review"]["semantic_change_checks"]
    except (KeyError, TypeError) as error:
        raise PromotionValidationError(
            "migration review lacks semantic change checks"
        ) from error
    expected = {
        "test_method_names_changed",
        "assertion_lines_changed",
        "committed_fixtures_changed_by_migration",
        "production_inputs_changed",
        "expected_behavior_changed",
    }
    if not isinstance(checks, dict) or set(checks) != expected:
        raise PromotionValidationError(
            "migration review semantic change checks are incomplete"
        )
    if any(type(value) is not int or value != 0 for value in checks.values()):
        raise PromotionValidationError(
            "migration review detected semantic test changes"
        )


def _validate_parallel_run(
    repo_root: Path,
    value: Any,
    promotion: Mapping[str, Any],
    implementation_commit: str,
    maximum_elapsed: float,
) -> dict[str, str]:
    run = _object(value, "parallel run")
    _fields(run, RUN_FIELDS, "parallel run")
    if not isinstance(run["run_dir"], str) or not run["run_dir"]:
        raise PromotionValidationError("parallel run_dir is invalid")
    discovery_path, discovery = _bound_path(
        repo_root,
        run["discovery_path"],
        run["discovery_sha256"],
        "parallel discovery",
    )
    summary_path, summary = _bound_path(
        repo_root,
        run["summary_path"],
        run["summary_sha256"],
        "parallel summary",
    )
    persisted_status_path, status = _bound_path(
        repo_root,
        run["persisted_status_path"],
        run["persisted_status_sha256"],
        "parallel persisted status",
    )
    persisted_exit_code_path = _bound_file(
        repo_root,
        run["persisted_exit_code_path"],
        run["persisted_exit_code_sha256"],
        "parallel persisted exit code",
    )
    persisted_command_path, persisted_command = _bound_path(
        repo_root,
        run["persisted_command_path"],
        run["persisted_command_sha256"],
        "parallel persisted command",
    )
    persisted_stdout_path = _bound_file(
        repo_root,
        run["persisted_stdout_path"],
        run["persisted_stdout_sha256"],
        "parallel persisted stdout",
    )
    run_dir = Path(run["run_dir"])
    if not run_dir.is_absolute():
        run_dir = repo_root / run_dir
    try:
        resolved_run_dir = run_dir.resolve(strict=True)
        discovery_path.relative_to(resolved_run_dir)
        summary_path.relative_to(resolved_run_dir)
    except (OSError, ValueError) as error:
        raise PromotionValidationError(
            "parallel discovery and summary must be inside run_dir"
        ) from error
    persisted_run_dir = Path(run["persisted_run_dir"])
    if not persisted_run_dir.is_absolute():
        persisted_run_dir = repo_root / persisted_run_dir
    try:
        resolved_persisted_run_dir = persisted_run_dir.resolve(strict=True)
        persisted_status_path.relative_to(resolved_persisted_run_dir)
        persisted_exit_code_path.relative_to(resolved_persisted_run_dir)
        persisted_command_path.relative_to(resolved_persisted_run_dir)
        persisted_stdout_path.relative_to(resolved_persisted_run_dir)
    except (OSError, ValueError) as error:
        raise PromotionValidationError(
            "persisted status and exit code must be inside persisted_run_dir"
        ) from error
    argv = persisted_command.get("argv")
    command_cwd = persisted_command.get("cwd")
    if (
        persisted_command.get("schema_name") != "persisted-command"
        or persisted_command.get("accepted_exit_codes") != [0]
        or not isinstance(argv, list)
        or not all(isinstance(item, str) for item in argv)
        or not isinstance(command_cwd, str)
    ):
        raise PromotionValidationError(
            "parallel persisted command metadata is invalid"
        )
    try:
        resolved_command_cwd = Path(command_cwd).resolve(strict=True)
        if resolved_command_cwd != repo_root:
            raise ValueError
        if (
            len(argv) < 12
            or Path(argv[0]).name.casefold()
            not in {"python", "python.exe", "python3", "python3.exe"}
            or argv[1:4] != ["-X", "utf8", "-B"]
            or argv[5] != "run"
        ):
            raise ValueError
        runner_path = Path(argv[4])
        if not runner_path.is_absolute():
            runner_path = resolved_command_cwd / runner_path
        if (
            runner_path.resolve(strict=True)
            != repo_root / "scripts/run_project_tests.py"
        ):
            raise ValueError
        suite_index = argv.index("--suite", 6)
        jobs_index = argv.index("--jobs", 6)
        root_index = argv.index("--test-root", 6)
        command_test_root = Path(argv[root_index + 1]).resolve(strict=True)
    except (ValueError, IndexError, OSError) as error:
        raise PromotionValidationError(
            "parallel persisted command does not invoke the project test runner"
        ) from error
    if (
        argv[suite_index + 1] != "video-workflow"
        or argv[jobs_index + 1] != "4"
        or command_test_root != resolved_run_dir.parents[2]
    ):
        raise PromotionValidationError(
            "parallel persisted command arguments do not bind run_dir"
        )
    try:
        stdout_lines = persisted_stdout_path.read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError) as error:
        raise PromotionValidationError(
            "parallel persisted stdout is invalid"
        ) from error
    stdout_records = []
    for line in stdout_lines:
        if not line.lstrip().startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            stdout_records.append(record)
    matching_records = [
        record
        for record in stdout_records
        if record.get("event") == "project_test_run_complete"
        and record.get("success") is True
        and record.get("failure_kind") is None
        and isinstance(record.get("run_dir"), str)
        and Path(record["run_dir"]).resolve() == resolved_run_dir
        and record.get("discovery_sha256") == run["discovery_sha256"]
        and record.get("summary_sha256") == run["summary_sha256"]
    ]
    if not matching_records:
        raise PromotionValidationError(
            "parallel persisted stdout does not bind successful runner completion"
        )
    try:
        persisted_exit_code = int(
            persisted_exit_code_path.read_text(encoding="utf-8").strip()
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise PromotionValidationError(
            "parallel persisted exit code is invalid"
        ) from error

    if (
        discovery.get("schema_name") != "video2pdf.project-test-discovery"
        or discovery.get("schema_version") != 1
        or discovery.get("commit") != implementation_commit
        or discovery.get("suite_ids") != promotion["suite_ids"]
        or discovery.get("total_count") != promotion["test_count"]
        or discovery.get("test_id_set_sha256")
        != promotion["test_id_set_sha256"]
    ):
        raise PromotionValidationError(
            "parallel discovery does not match promotion closed set"
        )
    modules = discovery.get("modules")
    if not isinstance(modules, list):
        raise PromotionValidationError("parallel discovery modules are invalid")
    discovered_ids = [
        test_id
        for module in modules
        if isinstance(module, dict)
        for test_id in module.get("test_ids", [])
    ]
    if (
        len(discovered_ids) != promotion["test_count"]
        or len(set(discovered_ids)) != len(discovered_ids)
        or hashlib.sha256(
            canonical_json_bytes(sorted(discovered_ids))
        ).hexdigest()
        != promotion["test_id_set_sha256"]
    ):
        raise PromotionValidationError(
            "parallel discovery has an invalid dynamic test inventory"
        )
    coverage = summary.get("coverage")
    expected_count = promotion["test_count"]
    if (
        summary.get("schema_name") != "video2pdf.project-test-summary"
        or summary.get("schema_version") != 1
        or summary.get("commit") != implementation_commit
        or summary.get("suite_ids") != promotion["suite_ids"]
        or summary.get("requested_jobs") != 4
        or summary.get("success") is not True
        or summary.get("failure_kind") is not None
        or not isinstance(coverage, dict)
        or any(
            coverage.get(field) != expected_count
            for field in (
                "discovered",
                "assigned",
                "started",
                "terminal",
                "executed_test_ids",
            )
        )
        or any(
            coverage.get(field) != []
            for field in (
                "missing_test_ids",
                "duplicate_test_ids",
                "unassigned_test_ids",
                "multiply_executed_test_ids",
            )
        )
    ):
        raise PromotionValidationError(
            "parallel summary is not a complete closed-set success"
        )
    security = status.get("security")
    elapsed = status.get("elapsed_seconds")
    if (
        status.get("schema_name") != "persisted-command-status"
        or status.get("state") != "succeeded"
        or status.get("exit_code") != 0
        or persisted_exit_code != 0
        or type(elapsed) not in (int, float)
        or elapsed > maximum_elapsed
        or not isinstance(security, dict)
        or security.get("classification") != "no_secret_detected"
        or security.get("acceptance_evidence_eligible") is not True
    ):
        raise PromotionValidationError(
            "parallel persisted run is ineligible or exceeds the performance gate"
        )
    return {
        "discovery_sha256": run["discovery_sha256"],
        "summary_sha256": run["summary_sha256"],
        "run_dir": str(resolved_run_dir),
    }


def validate_promotion_report(repo_root: Path) -> dict[str, Any]:
    """Validate the fixed report and every bound artifact without mutation."""

    repo_root = repo_root.resolve(strict=True)
    if not (repo_root / SCHEMA_RELATIVE_PATH).is_file():
        raise PromotionValidationError("promotion schema is missing")
    report = _load_json(repo_root / REPORT_RELATIVE_PATH, "promotion report")
    _fields(report, TOP_FIELDS, "promotion report")
    if (
        report["schema_name"] != SCHEMA_NAME
        or report["schema_version"] != SCHEMA_VERSION
        or report["issue"] != 27
    ):
        raise PromotionValidationError("promotion report identity is invalid")
    baseline = _object(
        report["historical_performance_baseline"],
        "historical_performance_baseline",
    )
    _fields(
        baseline,
        frozenset(
            set(HISTORICAL_BASELINE)
            | {
                "persisted_run_dir",
                "persisted_status_path",
                "persisted_status_sha256",
                "persisted_exit_code_path",
                "persisted_exit_code_sha256",
            }
        ),
        "historical_performance_baseline",
    )
    if any(
        baseline.get(field) != expected
        for field, expected in HISTORICAL_BASELINE.items()
    ):
        raise PromotionValidationError(
            "historical 474-test performance baseline is invalid"
        )
    baseline_status_path, baseline_status = _bound_path(
        repo_root,
        baseline["persisted_status_path"],
        baseline["persisted_status_sha256"],
        "historical persisted status",
    )
    baseline_exit_code_path = _bound_file(
        repo_root,
        baseline["persisted_exit_code_path"],
        baseline["persisted_exit_code_sha256"],
        "historical persisted exit code",
    )
    baseline_run_dir = Path(baseline["persisted_run_dir"])
    if not baseline_run_dir.is_absolute():
        baseline_run_dir = repo_root / baseline_run_dir
    try:
        baseline_status_path.relative_to(baseline_run_dir.resolve(strict=True))
        baseline_exit_code_path.relative_to(
            baseline_run_dir.resolve(strict=True)
        )
    except (OSError, ValueError) as error:
        raise PromotionValidationError(
            "historical persisted status must be inside persisted_run_dir"
        ) from error
    if (
        baseline_status.get("schema_name") != "persisted-command-status"
        or baseline_status.get("state") != "succeeded"
        or baseline_status.get("exit_code") != 0
        or baseline_status.get("elapsed_seconds")
        != HISTORICAL_BASELINE["persisted_elapsed_seconds"]
        or baseline_status.get("security")
        != {
            "classification": "no_secret_detected",
            "acceptance_evidence_eligible": True,
        }
    ):
        raise PromotionValidationError(
            "historical persisted status does not prove the 474-test baseline"
        )
    try:
        baseline_exit_code = int(
            baseline_exit_code_path.read_text(encoding="utf-8").strip()
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise PromotionValidationError(
            "historical persisted exit code is invalid"
        ) from error
    if baseline_exit_code != 0:
        raise PromotionValidationError(
            "historical persisted exit code is not successful"
        )
    final_issue9 = _closed_set(
        report["final_issue9_closed_set"],
        "final_issue9_closed_set",
        include_suites=False,
        include_evidence=True,
    )
    _, final_issue9_evidence = _bound_path(
        repo_root,
        final_issue9["evidence_path"],
        final_issue9["evidence_sha256"],
        "final Issue #9 closed-set evidence",
    )
    discovery_review = final_issue9_evidence.get("discovery_review")
    if (
        not isinstance(discovery_review, dict)
        or discovery_review.get("video_workflow_test_count")
        != final_issue9["test_count"]
        or discovery_review.get("duplicate_test_ids") != 0
        or discovery_review.get("test_id_set_sha256")
        != final_issue9["test_id_set_sha256"]
    ):
        raise PromotionValidationError(
            "final Issue #9 evidence does not match its closed set"
        )
    implementation = _object(report["implementation"], "implementation")
    _fields(implementation, frozenset({"commit"}), "implementation")
    implementation_commit = _commit(
        implementation["commit"], "implementation.commit"
    )
    promotion = _closed_set(
        report["promotion_closed_set"],
        "promotion_closed_set",
        include_suites=True,
    )
    if (
        final_issue9["test_count"] != promotion["test_count"]
        or final_issue9["test_id_set_sha256"]
        != promotion["test_id_set_sha256"]
    ):
        raise PromotionValidationError(
            "promotion closed set lacks semantic parity with final Issue #9"
        )
    performance = _object(report["performance"], "performance")
    _fields(
        performance,
        frozenset({"maximum_elapsed_seconds", "passed"}),
        "performance",
    )
    if (
        performance["maximum_elapsed_seconds"] != 1800
        or performance["passed"] is not True
    ):
        raise PromotionValidationError("performance decision does not pass")

    runs = report["parallel_runs"]
    if not isinstance(runs, list) or len(runs) != 2:
        raise PromotionValidationError("exactly two parallel runs are required")
    validated_runs = [
        _validate_parallel_run(
            repo_root,
            run,
            promotion,
            implementation_commit,
            performance["maximum_elapsed_seconds"],
        )
        for run in runs
    ]
    if len({run["run_dir"] for run in validated_runs}) != 2:
        raise PromotionValidationError("parallel run directories must be distinct")

    semantic = _object(report["semantic_parity"], "semantic_parity")
    _fields(
        semantic,
        frozenset({"passed", "test_id_set_sha256", "ignored_fields"}),
        "semantic_parity",
    )
    if (
        semantic["passed"] is not True
        or semantic["test_id_set_sha256"] != promotion["test_id_set_sha256"]
        or set(semantic["ignored_fields"])
        != {"timestamps", "pids", "durations", "completion_order"}
    ):
        raise PromotionValidationError("semantic parity decision is invalid")

    migration_binding = _object(report["migration_review"], "migration_review")
    _fields(
        migration_binding,
        frozenset({"path", "sha256", "passed"}),
        "migration_review",
    )
    if migration_binding["passed"] is not True:
        raise PromotionValidationError("migration review did not pass")
    _, migration_review = _bound_path(
        repo_root,
        migration_binding["path"],
        migration_binding["sha256"],
        "migration review",
    )
    _validate_migration_review(migration_review)

    fingerprint_input = {
        "implementation_commit": implementation_commit,
        "historical_baseline_commit": baseline["implementation_commit"],
        "historical_status_sha256": baseline["persisted_status_sha256"],
        "historical_exit_code_sha256": baseline[
            "persisted_exit_code_sha256"
        ],
        "final_issue9_commit": final_issue9["commit"],
        "final_issue9_test_count": final_issue9["test_count"],
        "final_issue9_test_id_set_sha256": final_issue9[
            "test_id_set_sha256"
        ],
        "final_issue9_evidence_sha256": final_issue9["evidence_sha256"],
        "suite_ids": promotion["suite_ids"],
        "test_count": promotion["test_count"],
        "test_id_set_sha256": promotion["test_id_set_sha256"],
        "parallel_discovery_sha256": [
            run["discovery_sha256"] for run in validated_runs
        ],
        "parallel_summary_sha256": [
            run["summary_sha256"] for run in validated_runs
        ],
        "parallel_persisted_status_sha256": [
            run["persisted_status_sha256"] for run in runs
        ],
        "parallel_persisted_exit_code_sha256": [
            run["persisted_exit_code_sha256"] for run in runs
        ],
        "parallel_persisted_command_sha256": [
            run["persisted_command_sha256"] for run in runs
        ],
        "parallel_persisted_stdout_sha256": [
            run["persisted_stdout_sha256"] for run in runs
        ],
        "migration_review_sha256": migration_binding["sha256"],
    }
    expected_fingerprint = hashlib.sha256(
        canonical_json_bytes(fingerprint_input)
    ).hexdigest()
    if report["promotion_fingerprint"] != expected_fingerprint:
        raise PromotionValidationError("promotion fingerprint mismatch")
    if report["cutover_authorized"] is not True:
        raise PromotionValidationError("cutover_authorized must be true")
    return {
        "valid": True,
        "cutover_authorized": True,
        "promotion_fingerprint": expected_fingerprint,
        "test_count": promotion["test_count"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        print("usage: validate_project_test_promotion.py", file=sys.stderr)
        return 1
    try:
        result = validate_promotion_report(Path(__file__).resolve().parents[1])
    except PromotionValidationError as error:
        print(
            json.dumps(
                {
                    "valid": False,
                    "cutover_authorized": False,
                    "failure_kind": "promotion_validation_failure",
                    "detail": str(error),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Validate the fixed, one-time project test runner Promotion Report."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.project_test_results import (
    ResultIntegrityError,
    canonical_json_bytes,
    read_file_snapshot,
    sha256_file,
)
from scripts.project_test_registry import RegistryError, load_registry
from scripts.project_test_source_provenance import (
    PROMOTION_AUTHORITY_SOURCE_PATHS,
    RUN_FINALIZATION_RELATIVE_PATH,
    SOURCE_MANIFEST_RELATIVE_PATH,
    SOURCE_SNAPSHOT_RELATIVE_PATH,
    SourceProvenanceError,
    committed_source_fingerprints,
    validate_evidence_only_commit_range,
    validate_execution_source_manifest,
)


REPORT_RELATIVE_PATH = Path("evidence/project-test-runner/promotion-report.json")
SCHEMA_RELATIVE_PATHS = {
    1: Path("schemas/project-test-promotion-report.v1.schema.json"),
    2: Path("schemas/project-test-promotion-report.v2.schema.json"),
}
SCHEMA_NAME = "video2pdf.project-test-promotion-report"
BASELINE_TEST_COUNT = 475
BASELINE_TEST_ID_SET_SHA256 = (
    "b315b255a81e06847f3c41a01fa36115dd40390924df395108684a0a3967f98f"
)
FINAL_ISSUE9_DISCOVERY_PATH = Path(
    r"D:\tests\video2pdf\video-workflow"
    r"\20260726_214234_b50d1c2c\discovery.json"
)
FINAL_ISSUE9_DISCOVERY_SHA256 = (
    "262f74a0268d4c1bd7b66ad5543070cdfa95f4bc249c91f39e5323aad897ceb7"
)
AUTHORIZED_DELTA_TEST_IDS = (
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_alternate_registry_path_and_project_root_are_cache_isolated",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_clear_hook_and_bounded_lru_force_rebuilds",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_concurrent_construction_builds_one_prepared_snapshot",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_new_unregistered_schema_fails_completeness",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_prepared_snapshot_retains_only_reusable_schema_bytes",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_registry_authority_mutation_is_not_hidden_by_cache",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_runtime_lock_change_during_prepare_is_not_cached",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_same_instance_check_detects_runtime_input_drift",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_same_instance_check_rebuilds_after_schema_drift",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_same_instance_check_rejects_new_schema_inventory_drift",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_same_instance_check_rejects_registry_authority_drift",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_same_root_reuses_prepared_schemas_without_sharing_instances",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_schema_change_rebuilds_and_invalid_schema_is_never_cached",
    "test_contract_registry_cache.ContractRegistryPreparedCacheTests.test_validate_does_not_cache_instance_decisions",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_current_v9_store_skips_migration_snapshot_planning",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_future_schema_version_fails_before_planning",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_healthy_check_runs_resource_validation_once",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_migration_ledger_gap_fails_before_planning",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_missing_maintenance_index_uses_old_repair_path",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_non_resource_check_constraint_damage_keeps_generic_error",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_recovery_sentinel_still_allows_reads_and_blocks_mutation",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_v8_store_still_uses_planner_and_migrates_to_v9",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_v9_resource_tamper_keeps_specific_constructor_diagnostic",
    "test_control_store_v9_fastpath.ControlStoreV9FastPathTests.test_v9_schema_tamper_remains_a_check_failure",
)
AUTHORIZED_DELTA_TEST_ID_SET_SHA256 = (
    "53a65650fb48bab050e4e236e3fd0e2b448a0aac7ff9b6599ebf0d1cae549121"
)
CURRENT_TEST_COUNT = 499
CURRENT_TEST_ID_SET_SHA256 = (
    "ea008eb2d56cf7bed8e489a0bf1dfabeabbb8f70410b65f1891c68a067ce36b7"
)
SUPERSET_AUTHORITY_RELATIVE_PATH = Path(
    "evidence/project-test-runner/promotion-superset-authority.v2.json"
)
OPTIMIZATION_SAFETY_REVIEW_RELATIVE_PATH = Path(
    "evidence/project-test-runner/optimization-safety-review.v1.json"
)
MIGRATION_REVIEW_RELATIVE_PATH = Path(
    "evidence/project-test-runner/test-path-migration-review.json"
)
AUTHORIZED_TEST_MODULES = {
    "tests/video_workflow/test_contract_registry_cache.py": (14, 26),
    "tests/video_workflow/test_control_store_v9_fastpath.py": (10, 32),
}
AUTHORIZED_PRODUCTION_PATHS = {
    "src/video2pdf_workflow_kernel/contracts.py",
    "src/video2pdf_workflow_kernel/control_store.py",
}
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
V2_RUN_FIELDS = frozenset(
    set(RUN_FIELDS)
    | {
        "semantic_outcomes_sha256",
        "module_assignment_sha256",
        "registry_sha256",
    }
)
PERSISTED_COMMAND_ALLOWED_FIELDS = frozenset(
    {
        "accepted_exit_codes",
        "argv",
        "created_at",
        "cwd",
        "normalized_task_name",
        "run_id",
        "run_nonce",
        "schema_name",
        "schema_version",
        "task_name",
    }
)
PERSISTED_STATUS_ALLOWED_FIELDS = frozenset(
    {
        "child_pid",
        "artifact_identities",
        "elapsed_seconds",
        "exit_code",
        "finished_at",
        "heartbeat_at",
        "latest_output_at",
        "log_sizes",
        "run_id",
        "run_nonce",
        "schema_name",
        "schema_version",
        "security",
        "started_at",
        "state",
        "status_publication",
        "supervisor_identity",
        "supervisor_pid",
        "target_identity",
        "updated_at",
    }
)
LEGACY_PERSISTED_RECORD_SHAPE = "legacy-v1.0.0-minimal"
CURRENT_SUCCESS_PERSISTED_RECORD_SHAPE = (
    "persisted-command-v1.0.0-current-success-shape"
)
DISCOVERY_ALLOWED_FIELDS = frozenset(
    {
        "commit",
        "discovery_arguments",
        "discovery_process",
        "duplicate_test_ids",
        "modules",
        "project",
        "registry_path",
        "registry_sha256",
        "schema_name",
        "schema_version",
        "suite_ids",
        "suites",
        "test_id_set_sha256",
        "total_count",
    }
)
SUMMARY_ALLOWED_FIELDS = frozenset(
    {
        "commit",
        "coverage",
        "failure_kind",
        "modules",
        "observed_peak_concurrency",
        "project",
        "requested_jobs",
        "schema_name",
        "schema_version",
        "source_snapshot_id",
        "source_snapshot_sha256",
        "success",
        "suite_ids",
    }
)
RUN_DIRECTORY_PATTERN = re.compile(r"\d{8}_\d{6}_[0-9a-f]{8}\Z")
PERSISTED_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}\Z"
)
CANONICAL_WORKTREE_ROOT = REPO_ROOT.resolve()
TRUSTED_EXTERNAL_RUN_ROOT = Path(
    r"D:\tests\video2pdf\video-workflow"
)
TRUSTED_PERSISTED_RUN_ROOT = (
    CANONICAL_WORKTREE_ROOT / "待删除" / "long-running"
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


def _closed_fields(
    value: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise PromotionValidationError(
            f"{label} has unknown field: {', '.join(unknown)}"
        )
    if missing:
        raise PromotionValidationError(
            f"{label} is missing field: {', '.join(missing)}"
        )


def _strict_persisted_command(
    value: Mapping[str, Any],
    label: str,
    *,
    record_shape: str,
) -> None:
    if record_shape == LEGACY_PERSISTED_RECORD_SHAPE:
        required = frozenset(
            {
                "schema_name",
                "schema_version",
                "accepted_exit_codes",
                "argv",
                "cwd",
            }
        )
    elif record_shape == CURRENT_SUCCESS_PERSISTED_RECORD_SHAPE:
        required = PERSISTED_COMMAND_ALLOWED_FIELDS
    else:
        raise AssertionError(f"unknown persisted command shape: {record_shape}")
    _closed_fields(
        value,
        allowed=PERSISTED_COMMAND_ALLOWED_FIELDS,
        required=required,
        label=label,
    )
    if (
        value.get("schema_name") != "persisted-command"
        or value.get("schema_version") != "1.0.0"
    ):
        raise PromotionValidationError(
            f"{label} identity or version is invalid"
        )
    accepted_exit_codes = value.get("accepted_exit_codes")
    argv = value.get("argv")
    cwd = value.get("cwd")
    if (
        not isinstance(accepted_exit_codes, list)
        or not accepted_exit_codes
        or any(type(code) is not int for code in accepted_exit_codes)
        or accepted_exit_codes != sorted(set(accepted_exit_codes))
        or not isinstance(argv, list)
        or not argv
        or not all(isinstance(argument, str) for argument in argv)
        or not isinstance(cwd, str)
        or not cwd
    ):
        raise PromotionValidationError(f"{label} values are invalid")
    if record_shape == CURRENT_SUCCESS_PERSISTED_RECORD_SHAPE:
        task_name = value.get("task_name")
        run_id = value.get("run_id")
        run_nonce = value.get("run_nonce")
        if (
            not isinstance(task_name, str)
            or not isinstance(value.get("normalized_task_name"), str)
            or value["normalized_task_name"]
            != _normalized_persisted_task_name(task_name)
        ):
            raise PromotionValidationError(
                f"{label} task identity is invalid"
            )
        if (
            not isinstance(run_id, str)
            or re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                run_id,
            )
            is None
            or not isinstance(run_nonce, str)
            or re.fullmatch(r"[0-9a-f]{64}", run_nonce) is None
        ):
            raise PromotionValidationError(
                f"{label} run identity is invalid"
            )
        _aware_timestamp(value.get("created_at"), f"{label}.created_at")


def _strict_persisted_status(
    value: Mapping[str, Any],
    label: str,
    *,
    require_elapsed: bool,
    record_shape: str,
) -> None:
    required = {
        "schema_name",
        "schema_version",
        "state",
        "exit_code",
        "security",
    }
    if require_elapsed:
        required.add("elapsed_seconds")
    if record_shape == CURRENT_SUCCESS_PERSISTED_RECORD_SHAPE:
        required.update(
            {
                "child_pid",
                "artifact_identities",
                "finished_at",
                "heartbeat_at",
                "latest_output_at",
                "log_sizes",
                "run_id",
                "run_nonce",
                "started_at",
                "supervisor_identity",
                "supervisor_pid",
                "target_identity",
                "updated_at",
            }
        )
    elif record_shape != LEGACY_PERSISTED_RECORD_SHAPE:
        raise AssertionError(f"unknown persisted status shape: {record_shape}")
    _closed_fields(
        value,
        allowed=PERSISTED_STATUS_ALLOWED_FIELDS,
        required=frozenset(required),
        label=label,
    )
    if (
        value.get("schema_name") != "persisted-command-status"
        or value.get("schema_version") != "1.0.0"
    ):
        raise PromotionValidationError(
            f"{label} identity or version is invalid"
        )
    security = value.get("security")
    if not isinstance(security, dict) or set(security) != {
        "classification",
        "acceptance_evidence_eligible",
    }:
        raise PromotionValidationError(
            f"{label}.security has missing or unknown fields"
        )
    for identity_name in ("supervisor_identity", "target_identity"):
        identity = value.get(identity_name)
        if identity is not None and (
            not isinstance(identity, dict)
            or set(identity)
            != {
                "pid",
                "process_creation_identity",
                "executable_path",
                "executable_file_identity",
                "parent_pid",
                "parent_process_creation_identity",
                "observation_sha256",
            }
        ):
            raise PromotionValidationError(
                f"{label}.{identity_name} has missing or unknown fields"
            )
    log_sizes = value.get("log_sizes")
    if log_sizes is not None and (
        not isinstance(log_sizes, dict)
        or set(log_sizes) != {"stdout", "stderr", "merged"}
    ):
        raise PromotionValidationError(
            f"{label}.log_sizes has missing or unknown fields"
        )
    if log_sizes is not None and any(
        type(size) is not int or size < 0 for size in log_sizes.values()
    ):
        raise PromotionValidationError(f"{label}.log_sizes is invalid")
    artifact_identities = value.get("artifact_identities")
    if artifact_identities is not None and (
        not isinstance(artifact_identities, dict)
        or set(artifact_identities)
        != {
            "command",
            "supervisor_launch",
            "stdout",
            "stderr",
            "merged",
            "exit_code",
        }
        or any(
            identity is not None
            and (
                not isinstance(identity, dict)
                or set(identity)
                != {"device", "inode", "size", "mtime_ns", "ctime_ns"}
                or any(
                    type(item) is not int or item < 0
                    for item in identity.values()
                )
            )
            for identity in artifact_identities.values()
        )
    ):
        raise PromotionValidationError(
            f"{label}.artifact_identities is invalid"
        )
    elapsed = value.get("elapsed_seconds")
    if require_elapsed and (
        type(elapsed) not in {int, float}
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        raise PromotionValidationError(
            f"{label}.elapsed_seconds is invalid"
        )
    if record_shape == CURRENT_SUCCESS_PERSISTED_RECORD_SHAPE:
        if value.get("state") != "succeeded":
            raise PromotionValidationError(
                f"{label} is not a successful terminal status"
            )
        if type(value.get("exit_code")) is not int:
            raise PromotionValidationError(
                f"{label}.exit_code is not an integer"
            )
        for field in (
            "started_at",
            "finished_at",
            "heartbeat_at",
            "updated_at",
        ):
            _aware_timestamp(value.get(field), f"{label}.{field}")
        latest_output_at = value.get("latest_output_at")
        if latest_output_at is not None:
            _aware_timestamp(
                latest_output_at, f"{label}.latest_output_at"
            )
        publication = value.get("status_publication")
        if publication is not None and (
            not isinstance(publication, dict)
            or set(publication) != {"state", "nonterminal_failures"}
            or publication.get("state") != "recovered"
            or type(publication.get("nonterminal_failures")) is not int
            or publication["nonterminal_failures"] <= 0
        ):
            raise PromotionValidationError(
                f"{label}.status_publication is invalid"
            )


def _aware_timestamp(value: Any, label: str) -> datetime:
    if (
        not isinstance(value, str)
        or PERSISTED_TIMESTAMP_PATTERN.fullmatch(value) is None
    ):
        raise PromotionValidationError(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise PromotionValidationError(f"{label} is not a timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PromotionValidationError(
            f"{label} must include a UTC offset"
        )
    return parsed


def _normalized_persisted_task_name(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in {" ", "_"} else "_"
        for character in value
    )
    normalized = re.sub(r" +", " ", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip(" _.") or "command"


def _persisted_run_directory_matches(
    directory_name: str,
    normalized_task_name: str,
    run_id: str,
) -> bool:
    return (
        re.fullmatch(
            rf"{re.escape(normalized_task_name)}_"
            rf"\d{{8}}_\d{{6}}_{re.escape(run_id[:8])}",
            directory_name,
        )
        is not None
    )


def _validate_current_success_record_pair(
    command: Mapping[str, Any],
    status: Mapping[str, Any],
    persisted_run_dir: Path,
    *,
    stdout_size: int,
) -> None:
    if not _persisted_run_directory_matches(
        persisted_run_dir.name,
        command["normalized_task_name"],
        command["run_id"],
    ):
        raise PromotionValidationError(
            "parallel persisted task identity does not match run directory"
        )
    created_at = _aware_timestamp(
        command["created_at"], "parallel persisted command.created_at"
    )
    started_at = _aware_timestamp(
        status["started_at"], "parallel persisted status.started_at"
    )
    finished_at = _aware_timestamp(
        status["finished_at"], "parallel persisted status.finished_at"
    )
    heartbeat_at = _aware_timestamp(
        status["heartbeat_at"], "parallel persisted status.heartbeat_at"
    )
    updated_at = _aware_timestamp(
        status["updated_at"], "parallel persisted status.updated_at"
    )
    latest_raw = status["latest_output_at"]
    latest_output_at = (
        None
        if latest_raw is None
        else _aware_timestamp(
            latest_raw, "parallel persisted status.latest_output_at"
        )
    )
    if started_at != created_at:
        raise PromotionValidationError(
            "parallel persisted timeline started_at does not match created_at"
        )
    if not (
        started_at <= finished_at <= updated_at
        and heartbeat_at == updated_at
    ):
        raise PromotionValidationError(
            "parallel persisted terminal timeline is impossible"
        )
    if latest_output_at is not None and not (
        started_at <= latest_output_at <= finished_at
    ):
        raise PromotionValidationError(
            "parallel persisted latest output time is impossible"
        )

    actual_log_sizes = {"stdout": stdout_size}
    for field, filename in (
        ("stderr", "stderr.log"),
        ("merged", "command.log"),
    ):
        _path, content, _sha256 = _canonical_snapshot(
            persisted_run_dir / filename,
            f"parallel persisted {field} log",
        )
        actual_log_sizes[field] = len(content)
    if (
        actual_log_sizes["stdout"] > 0
        or actual_log_sizes["stderr"] > 0
    ) and latest_output_at is None:
        raise PromotionValidationError(
            "parallel persisted latest output time is missing for nonempty logs"
        )
    if status["log_sizes"] != actual_log_sizes:
        raise PromotionValidationError(
            "parallel persisted status.log_sizes do not match log artifacts"
        )
    expected_identities = {
        name: _artifact_identity(persisted_run_dir / filename)
        for name, filename in (
            ("command", "command.json"),
            ("supervisor_launch", "supervisor-identity.json"),
            ("stdout", "stdout.log"),
            ("stderr", "stderr.log"),
            ("merged", "command.log"),
            ("exit_code", "exit-code.txt"),
        )
    }
    if status["artifact_identities"] != expected_identities:
        raise PromotionValidationError(
            "parallel persisted artifact file identity or timestamp differs"
        )


def _strict_discovery_nested(value: Mapping[str, Any], label: str) -> None:
    project = value.get("project")
    arguments = value.get("discovery_arguments")
    suites = value.get("suites")
    if not isinstance(project, dict) or set(project) != {
        "project_key",
        "repository",
    }:
        raise PromotionValidationError(
            f"{label}.project has missing or unknown fields"
        )
    if not isinstance(arguments, dict) or set(arguments) != {"suite_ids"}:
        raise PromotionValidationError(
            f"{label}.discovery_arguments has missing or unknown fields"
        )
    if not isinstance(suites, list):
        raise PromotionValidationError(f"{label}.suites is invalid")
    for suite in suites:
        if (
            not isinstance(suite, dict)
            or set(suite) != {"suite_id", "suite_key", "roots"}
            or not isinstance(suite.get("roots"), list)
        ):
            raise PromotionValidationError(
                f"{label}.suites[] has missing or unknown field"
            )
        for root in suite["roots"]:
            if not isinstance(root, dict) or set(root) != {
                "path",
                "pattern",
            }:
                raise PromotionValidationError(
                    f"{label}.suites[].roots[] has missing or unknown field"
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


def _source_fingerprint_map(
    value: Any,
    *,
    expected_paths: Sequence[str],
    label: str,
) -> dict[str, str]:
    if not isinstance(value, list) or len(value) != len(expected_paths):
        raise PromotionValidationError(f"{label} inventory is incomplete")
    result: dict[str, str] = {}
    for source in value:
        item = _object(source, label)
        _fields(item, frozenset({"path", "sha256"}), label)
        path = item["path"]
        if (
            not isinstance(path, str)
            or path not in expected_paths
            or path in result
        ):
            raise PromotionValidationError(f"{label} path is invalid")
        result[path] = _sha256(item["sha256"], f"{label} SHA")
    if tuple(sorted(result)) != tuple(sorted(expected_paths)):
        raise PromotionValidationError(f"{label} inventory is incomplete")
    return result


def _valid_process_creation_identity(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(
        r"windows-filetime:[1-9]\d*",
        value,
    ) is not None


def _valid_execution_identity(value: Any) -> bool:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "pid",
            "process_creation_identity",
            "executable_path",
            "executable_file_identity",
            "parent_pid",
            "parent_process_creation_identity",
            "observation_sha256",
        }
        or type(value.get("pid")) is not int
        or value["pid"] <= 0
        or not _valid_process_creation_identity(
            value.get("process_creation_identity")
        )
        or not isinstance(value.get("executable_path"), str)
        or not value["executable_path"]
        or type(value.get("parent_pid")) is not int
        or value["parent_pid"] <= 0
        or not _valid_process_creation_identity(
            value.get("parent_process_creation_identity")
        )
    ):
        return False
    file_identity = value.get("executable_file_identity")
    if not (
        isinstance(file_identity, dict)
        and set(file_identity) == {"device", "inode", "size", "mtime_ns"}
        and all(
            type(item) is int and item >= 0
            for item in file_identity.values()
        )
    ):
        return False
    declared = value.get("observation_sha256")
    return (
        isinstance(declared, str)
        and re.fullmatch(r"[0-9a-f]{64}", declared) is not None
        and declared
        == hashlib.sha256(
            canonical_json_bytes(
                {
                    key: item
                    for key, item in value.items()
                    if key != "observation_sha256"
                }
            )
        ).hexdigest()
    )


def _runner_is_bound_to_persisted_target(
    runner_identity: Mapping[str, Any],
    target_identity: Mapping[str, Any],
) -> bool:
    """Accept a direct target or the target launcher's observed child."""

    return runner_identity == target_identity or (
        runner_identity.get("parent_pid") == target_identity.get("pid")
        and runner_identity.get("parent_process_creation_identity")
        == target_identity.get("process_creation_identity")
    )


def _artifact_identity(path: Path) -> dict[str, int]:
    try:
        _, _, identity = read_file_snapshot(path)
    except ResultIntegrityError as error:
        raise PromotionValidationError(
            f"artifact file identity is unavailable: {path}"
        ) from error
    return identity


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PromotionValidationError(f"{label} is unreadable: {path}") from error
    return _object(value, label)


def _reject_reparse_components(path: Path, label: str) -> None:
    for component in (path, *path.parents):
        if not component.exists():
            continue
        attributes = getattr(
            component.stat(follow_symlinks=False),
            "st_file_attributes",
            0,
        )
        if component.is_symlink() or attributes & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise PromotionValidationError(
                f"{label} path contains a reparse point: {component}"
            )


def _windows_final_handle_path(file_descriptor: int) -> Path | None:
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


def _path_identity_matches_open_handle(
    lexical_path: Path,
    canonical_before: Path,
    file_descriptor: int,
    handle_stat: os.stat_result,
) -> bool:
    try:
        _reject_reparse_components(lexical_path, "opened artifact")
        canonical_after = lexical_path.resolve(strict=True)
        path_stat = lexical_path.stat(follow_symlinks=False)
    except (OSError, PromotionValidationError):
        return False
    if canonical_after != canonical_before:
        return False
    if (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_size,
    ) != (
        handle_stat.st_dev,
        handle_stat.st_ino,
        handle_stat.st_size,
    ):
        return False
    final_handle_path = _windows_final_handle_path(file_descriptor)
    if os.name == "nt":
        return final_handle_path is not None and (
            os.path.normcase(os.path.abspath(final_handle_path))
            == os.path.normcase(str(canonical_before))
        )
    return True


def _read_file_snapshot(
    lexical_path: Path,
    label: str,
) -> tuple[Path, bytes, str]:
    """Freeze one file or deny authorization when path identity is uncertain."""

    try:
        absolute_path = Path(os.path.abspath(lexical_path))
        _reject_reparse_components(absolute_path, label)
        canonical_before = absolute_path.resolve(strict=True)
        if not canonical_before.is_file():
            raise OSError("not an ordinary file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
            os, "O_NOINHERIT", 0
        )
        descriptor = os.open(absolute_path, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            before = os.fstat(handle.fileno())
            content = handle.read()
            after = os.fstat(handle.fileno())
            path_identity_stable = _path_identity_matches_open_handle(
                absolute_path,
                canonical_before,
                handle.fileno(),
                after,
            )
    except PromotionValidationError:
        raise
    except OSError as error:
        raise PromotionValidationError(
            f"{label} path is missing or unreadable: {lexical_path}"
        ) from error
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(content) != before.st_size:
        raise PromotionValidationError(f"{label} changed while being read")
    if not path_identity_stable:
        raise PromotionValidationError(
            f"{label} path identity is unproved; authorization unavailable"
        )
    fingerprint = hashlib.sha256(content).hexdigest()
    return canonical_before, content, fingerprint


def _snapshot_bound_file(
    repo_root: Path,
    raw_path: Any,
    expected_sha256: Any,
    label: str,
) -> tuple[Path, bytes]:
    """Bind a declared fingerprint to the exact bytes consumed later."""

    if not isinstance(raw_path, str) or not raw_path:
        raise PromotionValidationError(f"{label} path must be a non-empty string")
    lexical_path = Path(raw_path)
    if not lexical_path.is_absolute():
        lexical_path = repo_root / lexical_path
    path, content, actual_fingerprint = _read_file_snapshot(
        lexical_path, label
    )
    fingerprint = _sha256(expected_sha256, f"{label}.sha256")
    if actual_fingerprint != fingerprint:
        raise PromotionValidationError(f"{label} fingerprint mismatch")
    return path, content


def _canonical_snapshot(
    expected_path: Path,
    label: str,
) -> tuple[Path, bytes, str]:
    resolved, content, fingerprint = _read_file_snapshot(expected_path, label)
    try:
        canonical = expected_path.resolve(strict=True)
    except OSError as error:
        raise PromotionValidationError(
            f"{label} canonical path is missing: {expected_path}"
        ) from error
    if resolved != canonical:
        raise PromotionValidationError(f"{label} is not the canonical artifact")
    return resolved, content, fingerprint


def _json_from_snapshot(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PromotionValidationError(f"{label} is unreadable") from error
    return _object(value, label)


def _require_canonical_declared_path(
    raw_path: Any,
    expected_path: Path,
    label: str,
) -> None:
    if not isinstance(raw_path, str) or not raw_path:
        raise PromotionValidationError(f"{label} path is invalid")
    windows_text = raw_path.replace("/", "\\")
    if (
        windows_text.startswith("\\\\")
        or windows_text.startswith("\\??\\")
        or "\\.\\" in windows_text
        or ".." in Path(raw_path).parts
        or not Path(raw_path).is_absolute()
        or os.path.normcase(os.path.abspath(raw_path))
        != os.path.normcase(str(expected_path))
    ):
        raise PromotionValidationError(
            f"{label} must use the canonical absolute artifact path"
        )


def _bound_path(
    repo_root: Path,
    raw_path: Any,
    expected_sha256: Any,
    label: str,
) -> tuple[Path, dict[str, Any]]:
    path, content = _snapshot_bound_file(
        repo_root, raw_path, expected_sha256, label
    )
    return path, _json_from_snapshot(content, label)


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


def _validate_against_schema(
    repo_root: Path, report: dict[str, Any], version: int
) -> None:
    schema_path = repo_root / SCHEMA_RELATIVE_PATHS[version]
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(report),
            key=lambda item: list(item.absolute_path),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PromotionValidationError(
            f"promotion report v{version} schema is unreadable"
        ) from error
    if errors:
        path = ".".join(str(part) for part in errors[0].absolute_path)
        location = f" at {path}" if path else ""
        raise PromotionValidationError(
            f"promotion report v{version} schema validation failed"
            f"{location}: {errors[0].message}"
        )


def _canonical_test_ids(
    value: Any,
    *,
    label: str,
    expected_count: int,
    expected_sha256: str,
) -> list[str]:
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or value != sorted(value)
        or len(value) != len(set(value))
        or len(value) != expected_count
    ):
        raise PromotionValidationError(
            f"{label} must contain {expected_count} sorted unique Test IDs"
        )
    fingerprint = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    if fingerprint != expected_sha256:
        raise PromotionValidationError(f"{label} fingerprint mismatch")
    return value


def _test_module_inventory(path: Path) -> tuple[list[str], int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise PromotionValidationError(
            f"authorized test module is unreadable: {path}"
        ) from error
    test_ids: list[str] = []
    assertion_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name.startswith("test_"):
                        test_ids.append(f"{path.stem}.{node.name}.{child.name}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr.startswith("assert")
        ):
            assertion_calls += 1
    return sorted(test_ids), assertion_calls


def _validate_superset_authority(
    repo_root: Path,
    binding: Mapping[str, Any],
    final_issue9: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str], dict[str, str]]:
    _fields(
        binding,
        frozenset({"path", "sha256"}),
        "superset_authority",
    )
    if binding["path"] != SUPERSET_AUTHORITY_RELATIVE_PATH.as_posix():
        raise PromotionValidationError(
            "superset authority path is not the fixed v2 authority"
        )
    _, authority = _bound_path(
        repo_root,
        binding["path"],
        binding["sha256"],
        "superset authority",
    )
    _fields(
        authority,
        frozenset(
            {
                "schema_name",
                "schema_version",
                "issue",
                "baseline",
                "authorized_delta",
                "authority_sources",
                "derived_current",
                "semantic_review",
            }
        ),
        "superset authority",
    )
    if (
        authority["schema_name"]
        != "video2pdf.project-test-promotion-superset-authority"
        or authority["schema_version"] != 2
        or authority["issue"] != 27
    ):
        raise PromotionValidationError("superset authority identity is invalid")
    authority_sources = _source_fingerprint_map(
        authority["authority_sources"],
        expected_paths=PROMOTION_AUTHORITY_SOURCE_PATHS,
        label="Promotion authority source",
    )
    for path, expected_sha256 in authority_sources.items():
        if sha256_file((repo_root / path).resolve(strict=True)) != expected_sha256:
            raise PromotionValidationError(
                f"Promotion authority source binding is stale: {path}"
            )
    baseline = _object(authority["baseline"], "superset authority baseline")
    _fields(
        baseline,
        frozenset(
            {
                "commit",
                "test_count",
                "test_id_set_sha256",
                "test_ids",
                "source_evidence_path",
                "source_evidence_sha256",
            }
        ),
        "superset authority baseline",
    )
    if (
        baseline["commit"] != final_issue9["commit"]
        or baseline["test_count"] != BASELINE_TEST_COUNT
        or baseline["test_id_set_sha256"] != BASELINE_TEST_ID_SET_SHA256
        or baseline["source_evidence_path"]
        != MIGRATION_REVIEW_RELATIVE_PATH.as_posix()
        or baseline["source_evidence_path"] != final_issue9["evidence_path"]
        or baseline["source_evidence_sha256"] != final_issue9["evidence_sha256"]
    ):
        raise PromotionValidationError(
            "superset authority baseline binding is invalid"
        )
    baseline_ids = _canonical_test_ids(
        baseline["test_ids"],
        label="baseline Test-ID inventory",
        expected_count=BASELINE_TEST_COUNT,
        expected_sha256=BASELINE_TEST_ID_SET_SHA256,
    )
    delta = _object(
        authority["authorized_delta"], "superset authority authorized_delta"
    )
    _fields(
        delta,
        frozenset(
            {
                "authorization",
                "test_count",
                "test_id_set_sha256",
                "test_ids",
                "modules",
                "production_paths",
            }
        ),
        "superset authority authorized_delta",
    )
    if (
        delta["authorization"] != "issue-27-option-b"
        or delta["test_count"] != len(AUTHORIZED_DELTA_TEST_IDS)
        or delta["test_id_set_sha256"]
        != AUTHORIZED_DELTA_TEST_ID_SET_SHA256
    ):
        raise PromotionValidationError("authorized delta identity is invalid")
    delta_ids = _canonical_test_ids(
        delta["test_ids"],
        label="authorized delta Test-ID inventory",
        expected_count=len(AUTHORIZED_DELTA_TEST_IDS),
        expected_sha256=AUTHORIZED_DELTA_TEST_ID_SET_SHA256,
    )
    if tuple(delta_ids) != AUTHORIZED_DELTA_TEST_IDS:
        raise PromotionValidationError(
            "authorized delta differs from the fixed 24 Test IDs"
        )
    if set(baseline_ids) & set(delta_ids):
        raise PromotionValidationError(
            "baseline and authorized delta Test IDs overlap"
        )
    modules = delta["modules"]
    if not isinstance(modules, list) or len(modules) != 2:
        raise PromotionValidationError(
            "authorized delta must bind exactly two test modules"
        )
    module_ids: list[str] = []
    seen_paths: set[str] = set()
    for module in modules:
        item = _object(module, "authorized delta module")
        _fields(
            item,
            frozenset(
                {
                    "source_path",
                    "test_count",
                    "module_source_sha256",
                    "ast_assertion_call_count",
                }
            ),
            "authorized delta module",
        )
        source_path = item["source_path"]
        if source_path not in AUTHORIZED_TEST_MODULES or source_path in seen_paths:
            raise PromotionValidationError(
                "authorized delta module partition is invalid"
            )
        seen_paths.add(source_path)
        expected_count, expected_assertions = AUTHORIZED_TEST_MODULES[source_path]
        resolved = (repo_root / source_path).resolve(strict=True)
        if (
            item["test_count"] != expected_count
            or item["module_source_sha256"] != authority_sources[source_path]
            or item["module_source_sha256"] != sha256_file(resolved)
        ):
            raise PromotionValidationError(
                "authorized delta module source binding is stale"
            )
        discovered, assertions = _test_module_inventory(resolved)
        if (
            len(discovered) != expected_count
            or assertions != expected_assertions
            or item["ast_assertion_call_count"] != expected_assertions
        ):
            raise PromotionValidationError(
                "authorized delta module AST inventory is invalid"
            )
        module_ids.extend(discovered)
    if set(seen_paths) != set(AUTHORIZED_TEST_MODULES) or sorted(module_ids) != delta_ids:
        raise PromotionValidationError(
            "authorized delta Test IDs do not match their module assignment"
        )
    production_paths = delta["production_paths"]
    if not isinstance(production_paths, list) or len(production_paths) != 2:
        raise PromotionValidationError(
            "authorized delta production source bindings are invalid"
        )
    seen_production: set[str] = set()
    for source in production_paths:
        item = _object(source, "authorized production source")
        _fields(item, frozenset({"path", "sha256"}), "authorized production source")
        path = item["path"]
        if path not in AUTHORIZED_PRODUCTION_PATHS or path in seen_production:
            raise PromotionValidationError(
                "authorized production source path is invalid"
            )
        seen_production.add(path)
        if (
            item["sha256"] != authority_sources[path]
            or sha256_file((repo_root / path).resolve(strict=True))
            != item["sha256"]
        ):
            raise PromotionValidationError(
                "authorized production source binding is stale"
            )
    if seen_production != AUTHORIZED_PRODUCTION_PATHS:
        raise PromotionValidationError(
            "authorized production source inventory is incomplete"
        )
    current_ids = sorted(set(baseline_ids) | set(delta_ids))
    if (
        len(current_ids) != CURRENT_TEST_COUNT
        or hashlib.sha256(canonical_json_bytes(current_ids)).hexdigest()
        != CURRENT_TEST_ID_SET_SHA256
    ):
        raise PromotionValidationError(
            "derived 475 plus 24 Promotion closed set is invalid"
        )
    derived = _object(authority["derived_current"], "derived_current")
    _fields(
        derived,
        frozenset({"test_count", "test_id_set_sha256"}),
        "derived_current",
    )
    if (
        derived["test_count"] != CURRENT_TEST_COUNT
        or derived["test_id_set_sha256"] != CURRENT_TEST_ID_SET_SHA256
    ):
        raise PromotionValidationError("derived_current summary is invalid")
    semantic = _object(authority["semantic_review"], "semantic_review")
    _fields(
        semantic,
        frozenset(
            {
                "path",
                "sha256",
                "baseline_test_ids_removed_or_renamed",
                "unauthorized_test_ids_added",
                "unsafe_health_memo_present",
            }
        ),
        "semantic_review",
    )
    if (
        semantic["path"] != MIGRATION_REVIEW_RELATIVE_PATH.as_posix()
        or semantic["sha256"] != final_issue9["evidence_sha256"]
        or semantic["baseline_test_ids_removed_or_renamed"] != 0
        or semantic["unauthorized_test_ids_added"] != 0
        or semantic["unsafe_health_memo_present"] is not False
    ):
        raise PromotionValidationError("semantic review summary is invalid")
    return baseline_ids, delta_ids, current_ids, authority_sources


def _validate_optimization_safety_review(
    repo_root: Path,
    binding: Mapping[str, Any],
    implementation_commit: str,
    authority_sources: Mapping[str, str],
) -> str:
    _fields(
        binding,
        frozenset({"path", "sha256", "passed"}),
        "optimization_safety_review",
    )
    if binding["passed"] is not True:
        raise PromotionValidationError(
            "optimization safety review did not pass"
        )
    if binding["path"] != OPTIMIZATION_SAFETY_REVIEW_RELATIVE_PATH.as_posix():
        raise PromotionValidationError(
            "optimization safety review path is not the fixed authority"
        )
    _, evidence = _bound_path(
        repo_root,
        binding["path"],
        binding["sha256"],
        "optimization safety review",
    )
    _fields(
        evidence,
        frozenset(
            {
                "schema_name",
                "schema_version",
                "issue",
                "reviewed_source_commit",
                "source_files",
                "focused_run",
                "health_profile",
                "independent_reviews",
            }
        ),
        "optimization safety review evidence",
    )
    if (
        evidence["schema_name"]
        != "video2pdf.project-test-optimization-safety-review"
        or evidence["schema_version"] != 1
        or evidence["issue"] != 27
        or _commit(
            evidence["reviewed_source_commit"],
            "optimization safety reviewed_source_commit",
        )
        != implementation_commit
    ):
        raise PromotionValidationError(
            "optimization safety review identity is invalid"
        )
    safety_sources = _source_fingerprint_map(
        evidence["source_files"],
        expected_paths=tuple(sorted(AUTHORIZED_PRODUCTION_PATHS)),
        label="optimization safety source",
    )
    for path, declared_sha256 in safety_sources.items():
        if (
            declared_sha256 != authority_sources[path]
            or sha256_file((repo_root / path).resolve(strict=True))
            != declared_sha256
        ):
            raise PromotionValidationError(
                "optimization safety source fingerprint mismatch"
            )
    focused = _object(evidence["focused_run"], "focused safety run")
    _fields(
        focused,
        frozenset(
            {
                "persisted_run_dir",
                "status_path",
                "status_sha256",
                "exit_code_path",
                "exit_code_sha256",
                "command_path",
                "command_sha256",
                "stderr_path",
                "stderr_sha256",
                "test_count",
                "required_selectors",
            }
        ),
        "focused safety run",
    )
    status_path, status = _bound_path(
        repo_root,
        focused["status_path"],
        focused["status_sha256"],
        "focused safety status",
    )
    exit_path, exit_bytes = _snapshot_bound_file(
        repo_root,
        focused["exit_code_path"],
        focused["exit_code_sha256"],
        "focused safety exit code",
    )
    command_path, command = _bound_path(
        repo_root,
        focused["command_path"],
        focused["command_sha256"],
        "focused safety command",
    )
    stderr_path, stderr_bytes = _snapshot_bound_file(
        repo_root,
        focused["stderr_path"],
        focused["stderr_sha256"],
        "focused safety stderr",
    )
    run_dir = Path(focused["persisted_run_dir"])
    if not run_dir.is_absolute():
        run_dir = repo_root / run_dir
    try:
        resolved_run = run_dir.resolve(strict=True)
        for path in (status_path, exit_path, command_path, stderr_path):
            path.relative_to(resolved_run)
    except (OSError, ValueError) as error:
        raise PromotionValidationError(
            "focused safety artifacts escape persisted_run_dir"
        ) from error
    selectors = focused["required_selectors"]
    argv = command.get("argv")
    _strict_persisted_command(
        command,
        "focused safety command",
        record_shape=LEGACY_PERSISTED_RECORD_SHAPE,
    )
    _strict_persisted_status(
        status,
        "focused safety status",
        require_elapsed=True,
        record_shape=LEGACY_PERSISTED_RECORD_SHAPE,
    )
    if (
        focused["test_count"] != 76
        or not isinstance(selectors, list)
        or selectors
        != [
            "tests.video_workflow.test_control_store_recovery",
            "tests.video_workflow.test_control_store_transaction_scope",
            "tests.video_workflow.test_control_store_v9_fastpath",
            "tests.video_workflow.test_resource_control_store_integrity",
            "tests.video_workflow.test_source_ready_hardening",
        ]
        or not isinstance(argv, list)
        or not all(selector in argv for selector in selectors)
        or command.get("cwd") != str(repo_root)
        or command.get("accepted_exit_codes") != [0]
        or status.get("state") != "succeeded"
        or status.get("exit_code") != 0
        or status.get("security")
        != {
            "classification": "no_secret_detected",
            "acceptance_evidence_eligible": True,
        }
        or exit_bytes.decode("utf-8").strip() != "0"
    ):
        raise PromotionValidationError(
            "focused safety persisted evidence is invalid"
        )
    stderr = stderr_bytes.decode("utf-8")
    if "Ran 76 tests" not in stderr or "\nOK" not in stderr:
        raise PromotionValidationError(
            "focused safety raw unittest evidence is incomplete"
        )
    profile = _object(evidence["health_profile"], "health profile")
    _fields(
        profile,
        frozenset(
            {
                "path",
                "sha256",
                "persisted_status_path",
                "persisted_status_sha256",
                "persisted_exit_code_path",
                "persisted_exit_code_sha256",
            }
        ),
        "health profile",
    )
    _, profile_json = _bound_path(
        repo_root, profile["path"], profile["sha256"], "health profile result"
    )
    _, profile_status = _bound_path(
        repo_root,
        profile["persisted_status_path"],
        profile["persisted_status_sha256"],
        "health profile persisted status",
    )
    profile_exit_path, profile_exit_bytes = _snapshot_bound_file(
        repo_root,
        profile["persisted_exit_code_path"],
        profile["persisted_exit_code_sha256"],
        "health profile persisted exit code",
    )
    _strict_persisted_status(
        profile_status,
        "health profile persisted status",
        require_elapsed=True,
        record_shape=LEGACY_PERSISTED_RECORD_SHAPE,
    )
    classification = profile_json.get("control_store_check_classification")
    timed = profile_json.get("timed_calls")
    if (
        profile_json.get("success") is not True
        or profile_json.get("tests_run") != 3
        or not isinstance(classification, dict)
        or classification.get("full_checks") != 807
        or classification.get("memo_hits") != 0
        or not isinstance(timed, dict)
        or timed.get("control_store_lock_probe", {}).get("count") != 807
        or profile_status.get("state") != "succeeded"
        or profile_status.get("exit_code") != 0
        or profile_status.get("security")
        != {
            "classification": "no_secret_detected",
            "acceptance_evidence_eligible": True,
        }
        or profile_exit_bytes.decode("utf-8").strip() != "0"
    ):
        raise PromotionValidationError(
            "health profile permits a memo or lacks full live checks"
        )
    reviews = evidence["independent_reviews"]
    if (
        not isinstance(reviews, list)
        or len(reviews) != 2
        or {item.get("axis") for item in reviews if isinstance(item, dict)}
        != {"standards", "spec"}
    ):
        raise PromotionValidationError(
            "optimization independent review bindings are incomplete"
        )
    for review in reviews:
        item = _object(review, "optimization independent review")
        _fields(
            item,
            frozenset({"axis", "status", "path", "sha256"}),
            "optimization independent review",
        )
        if item["status"] != "PASS":
            raise PromotionValidationError(
                "optimization independent review did not pass"
            )
        _snapshot_bound_file(
            repo_root,
            item["path"],
            item["sha256"],
            "optimization independent review",
        )
    return binding["sha256"]


def _validate_historical_baseline(
    repo_root: Path, value: Any
) -> dict[str, Any]:
    baseline = _object(value, "historical_performance_baseline")
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
    status_path, status = _bound_path(
        repo_root,
        baseline["persisted_status_path"],
        baseline["persisted_status_sha256"],
        "historical persisted status",
    )
    exit_path, exit_bytes = _snapshot_bound_file(
        repo_root,
        baseline["persisted_exit_code_path"],
        baseline["persisted_exit_code_sha256"],
        "historical persisted exit code",
    )
    run_dir = Path(baseline["persisted_run_dir"])
    if not run_dir.is_absolute():
        run_dir = repo_root / run_dir
    try:
        resolved = run_dir.resolve(strict=True)
        status_path.relative_to(resolved)
        exit_path.relative_to(resolved)
    except (OSError, ValueError) as error:
        raise PromotionValidationError(
            "historical persisted artifacts escape persisted_run_dir"
        ) from error
    _strict_persisted_status(
        status,
        "historical persisted status",
        require_elapsed=True,
        record_shape=LEGACY_PERSISTED_RECORD_SHAPE,
    )
    if (
        status.get("schema_name") != "persisted-command-status"
        or status.get("state") != "succeeded"
        or status.get("exit_code") != 0
        or status.get("elapsed_seconds")
        != HISTORICAL_BASELINE["persisted_elapsed_seconds"]
        or status.get("security")
        != {
            "classification": "no_secret_detected",
            "acceptance_evidence_eligible": True,
        }
        or exit_bytes.decode("utf-8").strip() != "0"
    ):
        raise PromotionValidationError(
            "historical persisted evidence does not prove the 474-test baseline"
        )
    return baseline


def _canonical_json_artifact(path: Path, label: str) -> tuple[dict[str, Any], str]:
    _, content, fingerprint = _canonical_snapshot(path, label)
    return _json_from_snapshot(content, label), fingerprint


def _validate_runner_artifact_chain(
    repo_root: Path,
    run_dir: Path,
    discovery: Mapping[str, Any],
    discovery_sha256: str,
    summary: Mapping[str, Any],
    *,
    implementation_commit: str,
    registry_sha256: str,
    persisted_run_id: str,
    persisted_run_nonce: str,
    target_identity: Mapping[str, Any],
    supervisor_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove marker -> run manifest -> workers -> events -> terminal summary."""

    if (
        run_dir.name != run_dir.name.casefold()
        or RUN_DIRECTORY_PATTERN.fullmatch(run_dir.name) is None
        or run_dir.parent.name != "video-workflow"
        or run_dir.parent.parent.name != "video2pdf"
    ):
        raise PromotionValidationError("parallel run directory identity is invalid")
    project = {
        "project_key": "video2pdf",
        "repository": "Nishijujuba/video2pdf",
    }
    marker, marker_sha256 = _canonical_json_artifact(
        run_dir.parent.parent / "project.json",
        "external project marker",
    )
    if marker != {
        "schema_name": "external-test-project",
        "schema_version": "1.0.0",
        **project,
        "remote_identity": "github.com/Nishijujuba/video2pdf",
    }:
        raise PromotionValidationError("external project marker identity is invalid")

    test_run, test_run_sha256 = _canonical_json_artifact(
        run_dir / "test-run.json", "parallel test-run manifest"
    )
    test_run_schema_version = test_run.get("schema_version")
    test_run_fields = {
        "schema_name",
        "schema_version",
        "command",
        "project",
        "commit",
        "registry_sha256",
        "discovery_sha256",
        "source_manifest_path",
        "source_manifest_sha256",
        "suite_ids",
        "run_dir",
        "project_marker_sha256",
        "persisted_run_id",
        "persisted_run_nonce",
        "persisted_target_identity",
        "persisted_supervisor_identity",
        "requested_jobs",
        "timings_from",
        "runner_identity",
        "discovery_process",
    }
    if test_run_schema_version == 2:
        test_run_fields.update(
            {
                "source_snapshot_path",
                "source_snapshot_id",
                "source_snapshot_sha256",
            }
        )
    _fields(
        test_run,
        frozenset(test_run_fields),
        "parallel test-run manifest",
    )
    discovery_process = test_run["discovery_process"]
    raw_discovery_process = discovery.get("discovery_process")
    expected_discovery_command_prefix = [
        discovery_process.get("command", [None])[0]
        if isinstance(discovery_process, dict)
        and isinstance(discovery_process.get("command"), list)
        and discovery_process["command"]
        else None,
        "-X",
        "utf8",
        "-B",
        "-m",
        "scripts.project_test_discovery",
        "--repo-root",
        str(run_dir / "execution-source-files"),
        "--registry",
        str(
            run_dir
            / "execution-source-files"
            / "config/test-suites.v1.json"
        ),
        "--destination",
        str(run_dir / "discovery.json"),
        "--commit",
        implementation_commit,
        "--launcher-binding-stdin",
        "--suite",
        "video-workflow",
    ]
    launcher_identity = (
        discovery_process.get("launcher_identity")
        if isinstance(discovery_process, dict)
        else None
    )
    discovery_self_identity = (
        discovery_process.get("self_identity")
        if isinstance(discovery_process, dict)
        else None
    )
    relationship = (
        discovery_process.get("relationship")
        if isinstance(discovery_process, dict)
        else None
    )
    direct_discovery = (
        relationship == "direct"
        and discovery_self_identity == launcher_identity
    )
    launcher_child_discovery = (
        relationship == "launcher_child"
        and isinstance(discovery_self_identity, dict)
        and isinstance(launcher_identity, dict)
        and discovery_self_identity.get("pid")
        != launcher_identity.get("pid")
        and discovery_self_identity.get("process_creation_identity")
        != launcher_identity.get("process_creation_identity")
        and discovery_self_identity.get("parent_pid")
        == launcher_identity.get("pid")
        and discovery_self_identity.get(
            "parent_process_creation_identity"
        )
        == launcher_identity.get("process_creation_identity")
    )
    if (
        test_run["schema_name"] != "video2pdf.project-test-run"
        or test_run["schema_version"] not in (1, 2)
        or test_run["command"] != "run"
        or test_run["project"] != project
        or test_run["commit"] != implementation_commit
        or test_run["registry_sha256"] != registry_sha256
        or test_run["discovery_sha256"] != discovery_sha256
        or test_run["suite_ids"] != ["video-workflow"]
        or Path(test_run["run_dir"]).resolve(strict=True) != run_dir
        or test_run["project_marker_sha256"] != marker_sha256
        or test_run["persisted_run_id"] != persisted_run_id
        or test_run["persisted_run_nonce"] != persisted_run_nonce
        or test_run["persisted_target_identity"] != target_identity
        or test_run["persisted_supervisor_identity"] != supervisor_identity
        or test_run["requested_jobs"] != 4
        or test_run["timings_from"] is not None
        or not _valid_execution_identity(test_run["runner_identity"])
        or not _runner_is_bound_to_persisted_target(
            test_run["runner_identity"],
            target_identity,
        )
        or not isinstance(discovery_process, dict)
        or set(discovery_process)
        != {
            "relationship",
            "command",
            "launcher_identity",
            "self_identity",
            "exit_code",
        }
        or raw_discovery_process
        != {
            key: value
            for key, value in discovery_process.items()
            if key != "exit_code"
        }
        or not _valid_execution_identity(launcher_identity)
        or not _valid_execution_identity(discovery_self_identity)
        or launcher_identity["process_creation_identity"]
        == test_run["runner_identity"]["process_creation_identity"]
        or discovery_process["exit_code"] != 0
        or launcher_identity["pid"]
        == test_run["runner_identity"]["pid"]
        or launcher_identity["parent_pid"]
        != test_run["runner_identity"]["pid"]
        or launcher_identity["parent_process_creation_identity"]
        != test_run["runner_identity"]["process_creation_identity"]
        or not (direct_discovery or launcher_child_discovery)
        or discovery_process["command"]
        != expected_discovery_command_prefix
        or Path(discovery_process["command"][0]).resolve(strict=False)
        != Path(launcher_identity["executable_path"]).resolve(strict=False)
    ):
        raise PromotionValidationError("parallel test-run identity is invalid")

    source_manifest_path = run_dir / SOURCE_MANIFEST_RELATIVE_PATH
    _require_canonical_declared_path(
        test_run["source_manifest_path"],
        source_manifest_path.resolve(strict=True),
        "parallel execution source manifest",
    )
    _, source_manifest_content, source_manifest_sha256 = _canonical_snapshot(
        source_manifest_path,
        "parallel execution source manifest",
    )
    if source_manifest_sha256 != test_run["source_manifest_sha256"]:
        raise PromotionValidationError(
            "parallel execution source manifest fingerprint mismatch"
        )
    source_manifest = _json_from_snapshot(
        source_manifest_content,
        "parallel execution source manifest",
    )
    try:
        recomputed_source_sha256 = validate_execution_source_manifest(
            repo_root,
            source_manifest,
            expected_test_module_paths=[
                module.get("source_path")
                for module in discovery.get("modules", [])
                if isinstance(module, dict)
                and isinstance(module.get("source_path"), str)
            ],
            require_worktree_match=False,
            frozen_run_dir=run_dir,
        )
    except SourceProvenanceError as error:
        raise PromotionValidationError(
            f"execution source is not bound to commit: {error}"
        ) from error
    if (
        recomputed_source_sha256 != source_manifest_sha256
        or source_manifest.get("commit") != implementation_commit
    ):
        raise PromotionValidationError(
            "execution source manifest differs from implementation commit"
        )
    source_snapshot_id = None
    source_snapshot_sha256 = None
    run_finalization_sha256 = None
    if test_run_schema_version == 2:
        source_snapshot_path = run_dir / SOURCE_SNAPSHOT_RELATIVE_PATH
        _require_canonical_declared_path(
            test_run["source_snapshot_path"],
            source_snapshot_path.resolve(strict=True),
            "parallel source snapshot",
        )
        source_snapshot, source_snapshot_sha256 = _canonical_json_artifact(
            source_snapshot_path,
            "parallel source snapshot",
        )
        source_snapshot_id = source_snapshot.get("source_snapshot_id")
        source_snapshot_payload = {
            key: value
            for key, value in source_snapshot.items()
            if key != "source_snapshot_id"
        }
        expected_entry_inventory = [
            {
                "path": item["path"],
                "git_blob": item["git_blob"],
                "runtime_sha256": item["runtime_sha256"],
                "runtime_size": item["runtime_size"],
            }
            for item in source_manifest["entries"]
        ]
        expected_module_inventory = [
            {
                "module_key": item["module_key"],
                "suite_id": item["suite_id"],
                "source_path": item["source_path"],
                "test_count": item["test_count"],
                "test_ids_sha256": hashlib.sha256(
                    canonical_json_bytes(sorted(item["test_ids"]))
                ).hexdigest(),
            }
            for item in sorted(
                discovery["modules"],
                key=lambda value: (
                    value["suite_id"],
                    value["source_path"],
                ),
            )
        ]
        if (
            set(source_snapshot)
            != {
                "schema_name",
                "schema_version",
                "run_dir",
                "execution_root",
                "persisted_run_id",
                "persisted_run_nonce",
                "runner_identity",
                "project",
                "registry_sha256",
                "project_marker_sha256",
                "source_manifest_path",
                "source_manifest_sha256",
                "commit",
                "git_tree",
                "entry_inventory",
                "module_inventory",
                "prevalidation",
                "source_snapshot_id",
            }
            or
            source_snapshot.get("schema_name")
            != "video2pdf.project-test-source-snapshot"
            or source_snapshot.get("schema_version") != 1
            or hashlib.sha256(
                canonical_json_bytes(source_snapshot_payload)
            ).hexdigest()
            != source_snapshot_id
            or test_run["source_snapshot_id"] != source_snapshot_id
            or test_run["source_snapshot_sha256"] != source_snapshot_sha256
            or source_snapshot.get("run_dir") != str(run_dir)
            or source_snapshot.get("execution_root")
            != str((run_dir / "execution-source-files").resolve(strict=True))
            or source_snapshot.get("persisted_run_id") != persisted_run_id
            or source_snapshot.get("persisted_run_nonce")
            != persisted_run_nonce
            or source_snapshot.get("runner_identity")
            != test_run["runner_identity"]
            or source_snapshot.get("project") != project
            or source_snapshot.get("registry_sha256") != registry_sha256
            or source_snapshot.get("project_marker_sha256") != marker_sha256
            or source_snapshot.get("source_manifest_sha256")
            != source_manifest_sha256
            or source_snapshot.get("commit") != implementation_commit
            or source_snapshot.get("git_tree")
            != source_manifest.get("git_tree")
            or source_snapshot.get("entry_inventory")
            != {
                "count": len(expected_entry_inventory),
                "sha256": hashlib.sha256(
                    canonical_json_bytes(expected_entry_inventory)
                ).hexdigest(),
            }
            or source_snapshot.get("module_inventory")
            != {
                "count": len(expected_module_inventory),
                "sha256": hashlib.sha256(
                    canonical_json_bytes(expected_module_inventory)
                ).hexdigest(),
            }
            or source_snapshot.get("prevalidation")
            != {
                "result": "passed",
                "source_manifest_sha256": source_manifest_sha256,
            }
        ):
            raise PromotionValidationError(
                "parallel source snapshot binding is invalid"
            )
    execution_source_bindings = {
        item["path"]: {
            "committed_sha256": item["committed_sha256"],
            "runtime_sha256": item["runtime_sha256"],
        }
        for item in source_manifest["entries"]
    }

    try:
        registry = load_registry(
            repo_root, repo_root / "config/test-suites.v1.json"
        )
    except RegistryError as error:
        raise PromotionValidationError(
            f"current Registry is invalid: {error}"
        ) from error
    if registry.fingerprint != registry_sha256:
        raise PromotionValidationError("current Registry fingerprint differs")
    authoritative: dict[str, tuple[str, str]] = {}
    for suite in registry.select_suites(["video-workflow"]):
        for root in suite.roots:
            for source in sorted((repo_root / root.path).glob(root.pattern)):
                if source.is_file():
                    relative = source.relative_to(repo_root).as_posix()
                    authoritative[relative] = (suite.suite_id, root.path)
    modules = discovery.get("modules")
    if (
        discovery.get("project") != project
        or discovery.get("commit") != implementation_commit
        or discovery.get("registry_path") != "config/test-suites.v1.json"
        or discovery.get("registry_sha256") != registry_sha256
        or discovery.get("discovery_arguments")
        != {"suite_ids": ["video-workflow"]}
        or discovery.get("suites")
        != [
            {
                "suite_id": "video-workflow",
                "suite_key": "video-workflow",
                "roots": [
                    {
                        "path": "tests/video_workflow",
                        "pattern": "test_*.py",
                    }
                ],
            }
        ]
        or discovery.get("suite_ids") != ["video-workflow"]
        or discovery.get("duplicate_test_ids") != []
    ):
        raise PromotionValidationError(
            "parallel discovery project or Registry identity is invalid"
        )
    if not isinstance(modules, list) or len(modules) != len(authoritative):
        raise PromotionValidationError(
            "parallel discovery differs from the current Registry module inventory"
        )
    by_key: dict[str, dict[str, Any]] = {}
    for module in modules:
        item = _object(module, "parallel discovery module")
        _fields(
            item,
            frozenset(
                {
                    "module_key",
                    "root_path",
                    "source_path",
                    "suite_id",
                    "test_count",
                    "test_ids",
                }
            ),
            "parallel discovery module",
        )
        source_path = item["source_path"]
        expected_owner = authoritative.get(source_path)
        test_ids = item["test_ids"]
        expected_key = hashlib.sha256(
            f"{item['suite_id']}\0{source_path}".encode("utf-8")
        ).hexdigest()[:12]
        if (
            expected_owner != (item["suite_id"], item["root_path"])
            or item["suite_id"] != "video-workflow"
            or item["module_key"] != expected_key
            or item["module_key"] in by_key
            or type(item["test_count"]) is not int
            or not isinstance(test_ids, list)
            or test_ids != sorted(test_ids)
            or len(test_ids) != item["test_count"]
            or len(test_ids) != len(set(test_ids))
            or any(
                test_id.partition(".")[0] != Path(source_path).stem
                for test_id in test_ids
            )
        ):
            raise PromotionValidationError(
                "parallel discovery module assignment is not Registry-authoritative"
            )
        by_key[item["module_key"]] = item
    if set(authoritative) != {
        item["source_path"] for item in by_key.values()
    }:
        raise PromotionValidationError(
            "parallel discovery omits a current Registry module"
        )

    summary_modules = summary.get("modules")
    if (
        summary.get("project") != project
        or summary.get("commit") != implementation_commit
        or summary.get("suite_ids") != ["video-workflow"]
    ):
        raise PromotionValidationError(
            "parallel summary project identity is invalid"
        )
    if not isinstance(summary_modules, list) or len(summary_modules) != len(by_key):
        raise PromotionValidationError("parallel summary module inventory is invalid")
    summary_by_key: dict[str, dict[str, Any]] = {}
    worker_pids: set[int] = set()
    worker_identities_by_key: dict[str, tuple[int, str, str]] = {}
    for module in summary_modules:
        item = _object(module, "parallel summary module")
        summary_module_fields = {
            "module_key",
            "suite_id",
            "source_path",
            "test_ids",
            "executions",
            "failure_kind",
            "detail",
            "exit_code",
            "assignment_sha256",
            "result_sha256",
            "stdout_sha256",
            "stderr_sha256",
            "worker_launch_nonce",
            "worker_identity",
            "worker_launcher_identity",
            "source_manifest_sha256",
            "artifact_identities",
        }
        if test_run_schema_version == 2:
            summary_module_fields.update(
                {"source_snapshot_id", "source_snapshot_sha256"}
            )
        _fields(
            item,
            frozenset(summary_module_fields),
            "parallel summary module",
        )
        key = item["module_key"]
        discovered = by_key.get(key)
        if (
            discovered is None
            or key in summary_by_key
            or item["suite_id"] != discovered["suite_id"]
            or item["source_path"] != discovered["source_path"]
            or item["test_ids"] != discovered["test_ids"]
            or item["failure_kind"] is not None
            or item["detail"] is not None
            or item["exit_code"] != 0
        ):
            raise PromotionValidationError(
                "parallel summary module does not match discovery"
            )
        assignment, assignment_fingerprint = _canonical_json_artifact(
            run_dir / "modules" / f"{key}.assignment.json",
            "parallel worker assignment",
        )
        artifact_identities = item["artifact_identities"]
        if (
            not isinstance(artifact_identities, dict)
            or set(artifact_identities)
            != {"assignment", "result", "stdout", "stderr"}
        ):
            raise PromotionValidationError(
                "parallel worker artifact identity inventory is invalid"
            )
        worker_launch_nonce = item["worker_launch_nonce"]
        worker_identity = item["worker_identity"]
        worker_launcher_identity = item["worker_launcher_identity"]
        if (
            not isinstance(worker_launch_nonce, str)
            or re.fullmatch(r"[0-9a-f]{64}", worker_launch_nonce) is None
            or not _valid_execution_identity(worker_identity)
            or not _valid_execution_identity(worker_launcher_identity)
            or worker_launcher_identity["parent_pid"]
            != test_run["runner_identity"]["pid"]
            or worker_launcher_identity["parent_process_creation_identity"]
            != test_run["runner_identity"]["process_creation_identity"]
            or worker_identity["parent_pid"]
            != worker_launcher_identity["pid"]
            or worker_identity["parent_process_creation_identity"]
            != worker_launcher_identity["process_creation_identity"]
            or item["source_manifest_sha256"]
            != source_manifest_sha256
        ):
            raise PromotionValidationError(
                "parallel worker identity is invalid"
            )
        expected_assignment = {
            "schema_name": "video2pdf.project-test-module-assignment",
            "schema_version": test_run_schema_version,
            "repo_root": str(repo_root),
            "execution_root": str(run_dir / "execution-source-files"),
            "module_key": key,
            "suite_id": discovered["suite_id"],
            "source_path": discovered["source_path"],
            "test_ids": discovered["test_ids"],
            "worker_launch_nonce": worker_launch_nonce,
            "source_manifest_sha256": source_manifest_sha256,
        }
        if test_run_schema_version == 2:
            source_entry = next(
                entry
                for entry in source_manifest["entries"]
                if entry["path"] == discovered["source_path"]
            )
            expected_assignment.update(
                {
                    "source_snapshot_id": source_snapshot_id,
                    "source_snapshot_sha256": source_snapshot_sha256,
                    "module_inventory_sha256": source_snapshot[
                        "module_inventory"
                    ]["sha256"],
                    "module_inventory": expected_module_inventory,
                    "source_sha256": source_entry["runtime_sha256"],
                }
            )
        if (
            assignment_fingerprint != item["assignment_sha256"]
            or assignment != expected_assignment
        ):
            raise PromotionValidationError("parallel worker assignment is invalid")
        result, result_fingerprint = _canonical_json_artifact(
            run_dir / "modules" / f"{key}.result.json",
            "parallel worker result",
        )
        duration = result.get("duration_seconds")
        executions = result.get("executions")
        result_fields = {
            "schema_name",
            "schema_version",
            "module_key",
            "suite_id",
            "source_path",
            "assigned_test_ids",
            "executions",
            "failure_kind",
            "exit_code",
            "duration_seconds",
            "worker_launch_nonce",
            "worker_identity",
            "source_manifest_sha256",
        }
        if test_run_schema_version == 2:
            result_fields.update(
                {"source_snapshot_id", "source_snapshot_sha256"}
            )
        if (
            result_fingerprint != item["result_sha256"]
            or set(result) != result_fields
            or result["schema_name"] != "video2pdf.project-test-module-result"
            or result["schema_version"] != test_run_schema_version
            or result["module_key"] != key
            or result["suite_id"] != discovered["suite_id"]
            or result["source_path"] != discovered["source_path"]
            or result["assigned_test_ids"] != discovered["test_ids"]
            or result["worker_launch_nonce"] != worker_launch_nonce
            or result["source_manifest_sha256"] != source_manifest_sha256
            or (
                test_run_schema_version == 2
                and (
                    result["source_snapshot_id"] != source_snapshot_id
                    or result["source_snapshot_sha256"]
                    != source_snapshot_sha256
                    or item["source_snapshot_id"] != source_snapshot_id
                    or item["source_snapshot_sha256"]
                    != source_snapshot_sha256
                )
            )
            or result["worker_identity"] != worker_identity
            or result["failure_kind"] is not None
            or result["exit_code"] != 0
            or type(duration) not in (int, float)
            or not math.isfinite(duration)
            or duration < 0
            or not isinstance(executions, list)
            or [
                {"test_id": value.get("test_id"), "status": value.get("status")}
                for value in executions
                if isinstance(value, dict)
            ]
            != item["executions"]
            or any(
                set(value) != {"test_id", "status", "duration_seconds"}
                or value["status"] != "passed"
                or type(value["duration_seconds"]) not in (int, float)
                or not math.isfinite(value["duration_seconds"])
                or value["duration_seconds"] < 0
                for value in executions
                if isinstance(value, dict)
            )
            or not all(isinstance(value, dict) for value in executions)
        ):
            raise PromotionValidationError("parallel worker result is invalid")
        for stream in ("stdout", "stderr"):
            _, _, actual_sha256 = _canonical_snapshot(
                run_dir / "logs" / f"{key}.{stream}.log",
                f"parallel worker {stream}",
            )
            if actual_sha256 != item[f"{stream}_sha256"]:
                raise PromotionValidationError(
                    f"parallel worker {stream} fingerprint mismatch"
                )
        expected_artifact_identities = {
            "assignment": _artifact_identity(
                run_dir / "modules" / f"{key}.assignment.json"
            ),
            "result": _artifact_identity(
                run_dir / "modules" / f"{key}.result.json"
            ),
            "stdout": _artifact_identity(
                run_dir / "logs" / f"{key}.stdout.log"
            ),
            "stderr": _artifact_identity(
                run_dir / "logs" / f"{key}.stderr.log"
            ),
        }
        if artifact_identities != expected_artifact_identities:
            raise PromotionValidationError(
                "parallel worker artifact file identity or timestamp differs"
            )
        summary_by_key[key] = item
        worker_identities_by_key[key] = (
            worker_identity["pid"],
            worker_identity["process_creation_identity"],
            worker_launch_nonce,
        )
    if (
        len(set(worker_identities_by_key.values())) != len(by_key)
        or len(
            {
                (identity[0], identity[1])
                for identity in worker_identities_by_key.values()
            }
        )
        != len(by_key)
        or len(
            {
                identity[2]
                for identity in worker_identities_by_key.values()
            }
        )
        != len(by_key)
    ):
        raise PromotionValidationError(
            "parallel modules lack independent process identities"
        )

    _, events_bytes, events_sha256 = _canonical_snapshot(
        run_dir / "events.jsonl", "parallel events"
    )
    try:
        event_lines = events_bytes.decode("utf-8").splitlines()
        events = [json.loads(line) for line in event_lines]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PromotionValidationError("parallel events are unreadable") from error
    if (
        not events
        or len(events) != len(by_key) * 3
        or [event.get("sequence") for event in events]
        != list(range(1, len(events) + 1))
    ):
        raise PromotionValidationError("parallel events state machine is incomplete")
    states: dict[str, list[str]] = {key: [] for key in by_key}
    active: set[str] = set()
    observed_peak = 0
    prior_time = -1
    for event in events:
        if not isinstance(event, dict):
            raise PromotionValidationError("parallel event is invalid")
        key = event.get("module_key")
        discovered = by_key.get(key)
        state = event.get("event")
        common = {
            "sequence",
            "event",
            "module_key",
            "suite_id",
            "source_path",
            "time_unix_ns",
        }
        if test_run_schema_version == 2:
            common.update(
                {"source_snapshot_id", "source_snapshot_sha256"}
            )
        expected_fields = (
            common
            if state == "queued"
            else common
            | (
                {
                    "worker_launcher_identity",
                    "worker_launch_nonce",
                    "source_manifest_sha256",
                    "artifact_identities",
                }
                if state == "started"
                else {
                    "failure_kind",
                    "exit_code",
                    "worker_identity",
                    "worker_launcher_identity",
                    "worker_launch_nonce",
                    "source_manifest_sha256",
                    "artifact_identities",
                }
            )
        )
        if (
            discovered is None
            or state not in {"queued", "started", "completed"}
            or set(event) != expected_fields
            or event["suite_id"] != discovered["suite_id"]
            or event["source_path"] != discovered["source_path"]
            or type(event["time_unix_ns"]) is not int
            or event["time_unix_ns"] < 0
            or event["time_unix_ns"] < prior_time
            or (
                test_run_schema_version == 2
                and (
                    event["source_snapshot_id"] != source_snapshot_id
                    or event["source_snapshot_sha256"]
                    != source_snapshot_sha256
                )
            )
        ):
            raise PromotionValidationError("parallel event is invalid")
        prior_time = event["time_unix_ns"]
        if state == "started":
            if (
                event["worker_launcher_identity"]
                != summary_by_key[key]["worker_launcher_identity"]
                or event["worker_launch_nonce"]
                != worker_identities_by_key[key][2]
                or event["source_manifest_sha256"]
                != source_manifest_sha256
                or event["artifact_identities"]
                != {
                    "assignment": summary_by_key[key][
                        "artifact_identities"
                    ]["assignment"]
                }
            ):
                raise PromotionValidationError(
                    "parallel worker identity is invalid"
                )
            if key in active:
                raise PromotionValidationError(
                    "parallel worker started more than once"
                )
            active.add(key)
            observed_peak = max(observed_peak, len(active))
            worker_pids.add(
                summary_by_key[key]["worker_identity"]["pid"]
            )
        if state == "completed" and (
            event["failure_kind"] is not None or event["exit_code"] != 0
        ):
            raise PromotionValidationError("parallel completed event failed")
        if state == "completed" and (
            event["worker_identity"] != summary_by_key[key]["worker_identity"]
            or event["worker_launcher_identity"]
            != summary_by_key[key]["worker_launcher_identity"]
            or event["worker_launch_nonce"] != worker_identities_by_key[key][2]
            or event["source_manifest_sha256"] != source_manifest_sha256
            or event["artifact_identities"]
            != summary_by_key[key]["artifact_identities"]
        ):
            raise PromotionValidationError(
                "parallel completed event worker identity differs"
            )
        if state == "completed":
            if key not in active:
                raise PromotionValidationError(
                    "parallel worker completed before start"
                )
            active.remove(key)
        states[key].append(state)
    if (
        active
        or observed_peak != 4
        or observed_peak != summary.get("observed_peak_concurrency")
        or any(
            value != ["queued", "started", "completed"]
            for value in states.values()
        )
    ):
        raise PromotionValidationError("parallel events state machine is invalid")

    timings, timings_sha256 = _canonical_json_artifact(
        run_dir / "timings.json", "parallel timings"
    )
    if (
        set(timings)
        != {
            "schema_name",
            "schema_version",
            "project",
            "commit",
            "suite_ids",
            "modules",
        }
        or timings["schema_name"] != "video2pdf.project-test-timings"
        or timings["schema_version"] not in (1, 2)
        or timings["project"] != project
        or timings["commit"] != implementation_commit
        or timings["suite_ids"] != ["video-workflow"]
        or not isinstance(timings["modules"], list)
        or {
            item.get("module_key") for item in timings["modules"]
            if isinstance(item, dict)
        }
        != set(by_key)
        or any(
            not isinstance(item, dict)
            or set(item) != {"module_key", "source_path", "duration_seconds"}
            or item["source_path"] != by_key[item["module_key"]]["source_path"]
            or type(item["duration_seconds"]) not in (int, float)
            or not math.isfinite(item["duration_seconds"])
            or item["duration_seconds"] < 0
            for item in timings["modules"]
        )
    ):
        raise PromotionValidationError("parallel timings manifest is invalid")
    if test_run_schema_version == 2:
        _, _, summary_sha256 = _canonical_snapshot(
            run_dir / "summary.json",
            "parallel summary finalization binding",
        )
        finalization, run_finalization_sha256 = _canonical_json_artifact(
            run_dir / RUN_FINALIZATION_RELATIVE_PATH,
            "parallel run finalization",
        )
        if finalization != {
            "schema_name": "video2pdf.project-test-run-finalization",
            "schema_version": 1,
            "source_snapshot_id": source_snapshot_id,
            "source_snapshot_sha256": source_snapshot_sha256,
            "source_manifest_sha256": source_manifest_sha256,
            "summary_sha256": summary_sha256,
            "scheduler_success": True,
            "scheduler_failure_kind": None,
            "postvalidation": {
                "result": "passed",
                "source_manifest_sha256": source_manifest_sha256,
                "detail": None,
            },
            "success": True,
            "failure_kind": None,
        }:
            raise PromotionValidationError(
                "parallel run finalization is invalid"
            )
    return {
        "marker_sha256": marker_sha256,
        "test_run_sha256": test_run_sha256,
        "events_sha256": events_sha256,
        "timings_sha256": timings_sha256,
        "runner_pid": test_run["runner_identity"]["pid"],
        "discovery_pid": discovery_self_identity["pid"],
        "source_manifest_sha256": source_manifest_sha256,
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_sha256": source_snapshot_sha256,
        "run_finalization_sha256": run_finalization_sha256,
        "execution_source_bindings": execution_source_bindings,
        "worker_pids": sorted(worker_pids),
        "worker_identity_lineage_sha256": hashlib.sha256(
            canonical_json_bytes(
                [
                    {
                        "module_key": key,
                        "worker_identity": summary_by_key[key][
                            "worker_identity"
                        ],
                        "worker_launcher_identity": summary_by_key[key][
                            "worker_launcher_identity"
                        ],
                        "worker_launch_nonce": identity[2],
                    }
                    for key, identity in sorted(
                        worker_identities_by_key.items()
                    )
                ]
            )
        ).hexdigest(),
    }


def _validate_parallel_run(
    repo_root: Path,
    value: Any,
    promotion: Mapping[str, Any],
    implementation_commit: str,
    maximum_elapsed: float,
    *,
    expected_test_ids: Sequence[str] | None = None,
    expected_registry_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        canonical_repo_root = repo_root.resolve(strict=True)
    except OSError as error:
        raise PromotionValidationError(
            "canonical worktree root is unavailable"
        ) from error
    if (
        expected_test_ids is not None
        and canonical_repo_root != CANONICAL_WORKTREE_ROOT
    ):
        raise PromotionValidationError(
            "Promotion v2 requires the canonical worktree root"
        )
    run = _object(value, "parallel run")
    _fields(
        run,
        V2_RUN_FIELDS if expected_test_ids is not None else RUN_FIELDS,
        "parallel run",
    )
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
    persisted_exit_code_path, persisted_exit_code_bytes = _snapshot_bound_file(
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
    persisted_stdout_path, persisted_stdout_bytes = _snapshot_bound_file(
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
        if (
            expected_test_ids is not None
            and resolved_run_dir.parent
            != TRUSTED_EXTERNAL_RUN_ROOT.resolve(strict=True)
        ):
            raise PromotionValidationError(
                "parallel run is outside the trusted external run root"
            )
        _require_canonical_declared_path(
            run["run_dir"], resolved_run_dir, "parallel run_dir"
        )
        _require_canonical_declared_path(
            run["discovery_path"],
            resolved_run_dir / "discovery.json",
            "parallel discovery",
        )
        _require_canonical_declared_path(
            run["summary_path"],
            resolved_run_dir / "summary.json",
            "parallel summary",
        )
        if (
            discovery_path != (resolved_run_dir / "discovery.json").resolve(strict=True)
            or summary_path != (resolved_run_dir / "summary.json").resolve(strict=True)
        ):
            raise ValueError
    except (OSError, ValueError) as error:
        raise PromotionValidationError(
            "parallel discovery and summary must be inside run_dir"
        ) from error
    persisted_run_dir = Path(run["persisted_run_dir"])
    if not persisted_run_dir.is_absolute():
        persisted_run_dir = repo_root / persisted_run_dir
    try:
        resolved_persisted_run_dir = persisted_run_dir.resolve(strict=True)
        if (
            expected_test_ids is not None
            and resolved_persisted_run_dir.parent
            != TRUSTED_PERSISTED_RUN_ROOT.resolve(strict=True)
        ):
            raise PromotionValidationError(
                "parallel run is outside the trusted persisted run root"
            )
        _require_canonical_declared_path(
            run["persisted_run_dir"],
            resolved_persisted_run_dir,
            "parallel persisted_run_dir",
        )
        expected_persisted_paths = {
            persisted_status_path: (
                "persisted_status_path",
                "status.json",
            ),
            persisted_exit_code_path: (
                "persisted_exit_code_path",
                "exit-code.txt",
            ),
            persisted_command_path: (
                "persisted_command_path",
                "command.json",
            ),
            persisted_stdout_path: (
                "persisted_stdout_path",
                "stdout.log",
            ),
        }
        for path, (field, name) in expected_persisted_paths.items():
            _require_canonical_declared_path(
                run[field],
                resolved_persisted_run_dir / name,
                f"parallel {field}",
            )
        if any(
            path != (resolved_persisted_run_dir / name).resolve(strict=True)
            for path, (_field, name) in expected_persisted_paths.items()
        ):
            raise ValueError
    except (OSError, ValueError) as error:
        raise PromotionValidationError(
            "persisted status and exit code must be inside persisted_run_dir"
        ) from error
    argv = persisted_command.get("argv")
    command_cwd = persisted_command.get("cwd")
    if expected_test_ids is not None:
        _strict_persisted_command(
            persisted_command,
            "parallel persisted command",
            record_shape=CURRENT_SUCCESS_PERSISTED_RECORD_SHAPE,
        )
        _strict_persisted_status(
            status,
            "parallel persisted status",
            require_elapsed=True,
            record_shape=CURRENT_SUCCESS_PERSISTED_RECORD_SHAPE,
        )
        _validate_current_success_record_pair(
            persisted_command,
            status,
            resolved_persisted_run_dir,
            stdout_size=len(persisted_stdout_bytes),
        )
        _closed_fields(
            discovery,
            allowed=DISCOVERY_ALLOWED_FIELDS,
            required=frozenset(
                {
                    "schema_name",
                    "schema_version",
                    "commit",
                    "suite_ids",
                    "total_count",
                    "test_id_set_sha256",
                    "registry_sha256",
                    "modules",
                }
            ),
            label="parallel discovery",
        )
        _strict_discovery_nested(discovery, "parallel discovery")
        _closed_fields(
            summary,
            allowed=SUMMARY_ALLOWED_FIELDS,
            required=frozenset(
                {
                    "schema_name",
                    "schema_version",
                    "commit",
                    "suite_ids",
                    "requested_jobs",
                    "observed_peak_concurrency",
                    "success",
                    "failure_kind",
                    "coverage",
                    "modules",
                }
            ),
            label="parallel summary",
        )
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
    persisted_run_id = persisted_command.get("run_id")
    persisted_run_nonce = persisted_command.get("run_nonce")
    target_identity = status.get("target_identity")
    supervisor_identity = status.get("supervisor_identity")
    if expected_test_ids is not None and (
        not isinstance(persisted_run_id, str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            persisted_run_id,
        )
        is None
        or status.get("run_id") != persisted_run_id
        or not isinstance(persisted_run_nonce, str)
        or re.fullmatch(r"[0-9a-f]{64}", persisted_run_nonce) is None
        or status.get("run_nonce") != persisted_run_nonce
        or not _persisted_run_directory_matches(
            resolved_persisted_run_dir.name,
            persisted_command["normalized_task_name"],
            persisted_run_id,
        )
        or not _valid_execution_identity(target_identity)
        or status.get("child_pid") != target_identity["pid"]
        or not _valid_execution_identity(supervisor_identity)
        or status.get("supervisor_pid") != supervisor_identity["pid"]
        or supervisor_identity["pid"] == target_identity["pid"]
        or supervisor_identity["process_creation_identity"]
        == target_identity["process_creation_identity"]
        or target_identity["parent_pid"] != supervisor_identity["pid"]
        or target_identity["parent_process_creation_identity"]
        != supervisor_identity["process_creation_identity"]
    ):
        raise PromotionValidationError(
            "parallel persisted run identity or process evidence is invalid"
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
        stdout_lines = persisted_stdout_bytes.decode("utf-8").splitlines()
    except UnicodeError as error:
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
    raw_discovery_process = discovery.get("discovery_process")
    discovery_process = (
        {**raw_discovery_process, "exit_code": 0}
        if isinstance(raw_discovery_process, dict)
        else None
    )
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
        and record.get("discovery_process") == discovery_process
    ]
    if not matching_records:
        raise PromotionValidationError(
            "parallel persisted stdout does not bind successful runner completion"
        )
    if expected_test_ids is not None and any(
        set(record)
        != {
            "event",
            "success",
            "failure_kind",
            "run_dir",
            "discovery_sha256",
            "summary_sha256",
            "discovery_process",
        }
        for record in matching_records
    ):
        raise PromotionValidationError(
            "parallel persisted completion event has unknown fields"
        )
    scheduling_records = [
        record
        for record in stdout_records
        if record.get("event") == "project_test_scheduling_started"
        and isinstance(record.get("run_dir"), str)
        and Path(record["run_dir"]).resolve() == resolved_run_dir
        and record.get("discovery_sha256") == run["discovery_sha256"]
        and record.get("total_count") == discovery.get("total_count")
        and record.get("discovery_process") == discovery_process
    ]
    if expected_test_ids is not None and (
        len(scheduling_records) != 1
        or set(scheduling_records[0])
        != {
            "event",
            "run_dir",
            "discovery_sha256",
            "total_count",
            "discovery_process",
        }
    ):
        raise PromotionValidationError(
            "parallel persisted scheduling event does not bind discovery identity"
        )
    try:
        persisted_exit_code = int(
            persisted_exit_code_bytes.decode("utf-8").strip()
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
    module_assignment = [
        {
            "suite_id": module.get("suite_id"),
            "source_path": module.get("source_path"),
            "test_ids": sorted(module.get("test_ids", [])),
        }
        for module in modules
        if isinstance(module, dict)
    ]
    module_assignment.sort(
        key=lambda item: (str(item["suite_id"]), str(item["source_path"]))
    )
    module_assignment_sha256 = hashlib.sha256(
        canonical_json_bytes(module_assignment)
    ).hexdigest()
    if expected_test_ids is not None:
        if (
            sorted(discovered_ids) != list(expected_test_ids)
            or discovery.get("registry_sha256") != expected_registry_sha256
            or run["registry_sha256"] != expected_registry_sha256
            or run["module_assignment_sha256"] != module_assignment_sha256
        ):
            raise PromotionValidationError(
                "parallel discovery Registry, Test IDs, or module assignment differ"
            )
    coverage = summary.get("coverage")
    expected_count = promotion["test_count"]
    if expected_test_ids is not None and (
        not isinstance(coverage, dict)
        or set(coverage)
        != {
            "discovered",
            "assigned",
            "started",
            "terminal",
            "module_count",
            "executed_test_ids",
            "missing_test_ids",
            "duplicate_test_ids",
            "unassigned_test_ids",
            "multiply_executed_test_ids",
        }
    ):
        raise PromotionValidationError(
            "parallel summary coverage fields are invalid"
        )
    if (
        summary.get("schema_name") != "video2pdf.project-test-summary"
        or summary.get("schema_version") not in (1, 2)
        or summary.get("commit") != implementation_commit
        or summary.get("suite_ids") != promotion["suite_ids"]
        or summary.get("requested_jobs") != 4
        or summary.get("success") is not True
        or summary.get("failure_kind") is not None
        or (
            summary.get("schema_version") == 2
            and (
                not isinstance(summary.get("source_snapshot_id"), str)
                or not isinstance(
                    summary.get("source_snapshot_sha256"), str
                )
            )
        )
        or not isinstance(coverage, dict)
        or (
            expected_test_ids is not None
            and coverage.get("module_count") != len(modules)
        )
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
    semantic_outcomes_sha256 = ""
    if expected_test_ids is not None:
        if summary.get("observed_peak_concurrency") != 4:
            raise PromotionValidationError(
                "parallel summary observed peak concurrency must be 4"
            )
        summary_modules = summary.get("modules")
        if not isinstance(summary_modules, list):
            raise PromotionValidationError(
                "parallel summary modules are invalid"
            )
        outcomes: list[dict[str, str]] = []
        summary_assignments: list[dict[str, Any]] = []
        for module in summary_modules:
            if not isinstance(module, dict):
                raise PromotionValidationError(
                    "parallel summary module is invalid"
                )
            module_ids = module.get("test_ids")
            executions = module.get("executions")
            if (
                not isinstance(module_ids, list)
                or not isinstance(executions, list)
            ):
                raise PromotionValidationError(
                    "parallel summary execution inventory is invalid"
                )
            summary_assignments.append(
                {
                    "suite_id": module.get("suite_id"),
                    "source_path": module.get("source_path"),
                    "test_ids": sorted(module_ids),
                }
            )
            for execution in executions:
                if (
                    not isinstance(execution, dict)
                    or set(execution) != {"test_id", "status"}
                    or execution.get("status") != "passed"
                    or not isinstance(execution.get("test_id"), str)
                ):
                    raise PromotionValidationError(
                        "parallel summary has a non-passing semantic outcome"
                    )
                outcomes.append(
                    {
                        "test_id": execution["test_id"],
                        "status": execution["status"],
                    }
                )
        outcomes.sort(key=lambda item: item["test_id"])
        if (
            [item["test_id"] for item in outcomes] != list(expected_test_ids)
            or len({item["test_id"] for item in outcomes}) != len(outcomes)
            or sorted(
                summary_assignments,
                key=lambda item: (
                    str(item["suite_id"]),
                    str(item["source_path"]),
                ),
            )
            != module_assignment
        ):
            raise PromotionValidationError(
                "parallel summary semantic outcomes are incomplete or reassigned"
            )
        semantic_outcomes_sha256 = hashlib.sha256(
            canonical_json_bytes(outcomes)
        ).hexdigest()
        if run["semantic_outcomes_sha256"] != semantic_outcomes_sha256:
            raise PromotionValidationError(
                "parallel semantic outcome fingerprint mismatch"
            )
    security = status.get("security")
    elapsed = status.get("elapsed_seconds")
    if (
        status.get("schema_name") != "persisted-command-status"
        or status.get("state") != "succeeded"
        or status.get("exit_code") != 0
        or persisted_exit_code != 0
        or type(elapsed) not in (int, float)
        or not math.isfinite(elapsed)
        or elapsed < 0
        or elapsed > maximum_elapsed
        or not isinstance(security, dict)
        or security.get("classification") != "no_secret_detected"
        or security.get("acceptance_evidence_eligible") is not True
    ):
        raise PromotionValidationError(
            "parallel persisted run is ineligible or exceeds the performance gate"
        )
    raw_chain = (
        _validate_runner_artifact_chain(
            repo_root,
            resolved_run_dir,
            discovery,
            run["discovery_sha256"],
            summary,
            implementation_commit=implementation_commit,
            registry_sha256=expected_registry_sha256,
            persisted_run_id=persisted_run_id or "",
            persisted_run_nonce=persisted_run_nonce or "",
            target_identity=(
                target_identity if isinstance(target_identity, dict) else {}
            ),
            supervisor_identity=(
                supervisor_identity
                if isinstance(supervisor_identity, dict)
                else {}
            ),
        )
        if expected_test_ids is not None
        else {}
    )
    return {
        "discovery_sha256": run["discovery_sha256"],
        "summary_sha256": run["summary_sha256"],
        "run_dir": str(resolved_run_dir),
        "persisted_run_dir": str(resolved_persisted_run_dir),
        "semantic_outcomes_sha256": semantic_outcomes_sha256,
        "module_assignment_sha256": module_assignment_sha256,
        "persisted_run_id": persisted_run_id or "",
        "persisted_run_nonce": persisted_run_nonce or "",
        "target_process_identity": (
            target_identity.get("process_creation_identity", "")
            if isinstance(target_identity, dict)
            else ""
        ),
        **raw_chain,
    }


def _validate_v1(
    repo_root: Path, report: dict[str, Any]
) -> dict[str, Any]:
    """Preserve the Promotion Report v1 475-to-475 validation contract."""

    _fields(report, TOP_FIELDS, "promotion report")
    if (
        report["schema_name"] != SCHEMA_NAME
        or report["schema_version"] != 1
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
    baseline_exit_code_path, baseline_exit_code_bytes = _snapshot_bound_file(
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
            baseline_exit_code_bytes.decode("utf-8").strip()
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
    if (
        final_issue9["test_count"] != BASELINE_TEST_COUNT
        or final_issue9["test_id_set_sha256"]
        != BASELINE_TEST_ID_SET_SHA256
    ):
        raise PromotionValidationError(
            "Promotion Report v1 is fixed to the 475-ID Issue #9 closed set"
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


def _validate_v2(
    repo_root: Path, report: dict[str, Any]
) -> dict[str, Any]:
    """Validate the fixed 475 plus authorized 24 Promotion contract."""

    _validate_against_schema(repo_root, report, 2)
    authorization_model = _object(
        report["authorization_model"],
        "authorization_model",
    )
    if authorization_model != {
        "decision_semantics": "local-fail-closed-non-cryptographic-v1",
        "cryptographic_provenance": False,
        "path_identity_required": True,
        "unproved_path_identity_authorizes": False,
        "persisted_record_contract": (
            "persisted-command-v1.0.0-current-success-shape"
        ),
        "execution_source_contract": (
            "clean-git-tree-plus-frozen-runtime-bytes-v1"
        ),
        "process_identity_contract": (
            "pid-creation-executable-parent-file-identity-v1"
        ),
        "local_attacker_limit": (
            "an arbitrary local writer can forge the worktree, frozen source, "
            "and every unsigned evidence artifact"
        ),
    }:
        raise PromotionValidationError(
            "authorization model does not disclose local trust limits"
        )
    baseline = _validate_historical_baseline(
        repo_root, report["historical_performance_baseline"]
    )
    final_issue9 = _object(
        report["final_issue9_closed_set"], "final_issue9_closed_set"
    )
    _fields(
        final_issue9,
        frozenset(
            {
                "commit",
                "test_count",
                "test_id_set_sha256",
                "test_ids",
                "evidence_path",
                "evidence_sha256",
                "inventory_path",
                "inventory_sha256",
            }
        ),
        "final_issue9_closed_set",
    )
    _commit(final_issue9["commit"], "final_issue9_closed_set.commit")
    if (
        final_issue9["test_count"] != BASELINE_TEST_COUNT
        or final_issue9["test_id_set_sha256"]
        != BASELINE_TEST_ID_SET_SHA256
        or final_issue9["evidence_path"]
        != MIGRATION_REVIEW_RELATIVE_PATH.as_posix()
    ):
        raise PromotionValidationError(
            "final Issue #9 closed set must be the fixed 475-ID baseline"
        )
    baseline_ids = _canonical_test_ids(
        final_issue9["test_ids"],
        label="final Issue #9 Test-ID inventory",
        expected_count=BASELINE_TEST_COUNT,
        expected_sha256=BASELINE_TEST_ID_SET_SHA256,
    )
    _require_canonical_declared_path(
        final_issue9["inventory_path"],
        FINAL_ISSUE9_DISCOVERY_PATH,
        "final Issue #9 original discovery",
    )
    if (
        final_issue9["inventory_sha256"]
        != FINAL_ISSUE9_DISCOVERY_SHA256
    ):
        raise PromotionValidationError(
            "final Issue #9 original discovery fingerprint is invalid"
        )
    _, issue9_inventory = _bound_path(
        repo_root,
        final_issue9["inventory_path"],
        final_issue9["inventory_sha256"],
        "final Issue #9 original discovery",
    )
    inventory_modules = issue9_inventory.get("modules")
    inventory_ids = (
        sorted(
            test_id
            for module in inventory_modules
            if isinstance(module, dict)
            for test_id in module.get("test_ids", [])
        )
        if isinstance(inventory_modules, list)
        else []
    )
    if (
        issue9_inventory.get("schema_name")
        != "video2pdf.project-test-discovery"
        or issue9_inventory.get("schema_version") != 1
        or issue9_inventory.get("suite_ids") != ["video-workflow"]
        or issue9_inventory.get("total_count") != BASELINE_TEST_COUNT
        or issue9_inventory.get("duplicate_test_ids") != []
        or issue9_inventory.get("test_id_set_sha256")
        != BASELINE_TEST_ID_SET_SHA256
        or inventory_ids != baseline_ids
        or hashlib.sha256(canonical_json_bytes(inventory_ids)).hexdigest()
        != BASELINE_TEST_ID_SET_SHA256
    ):
        raise PromotionValidationError(
            "final Issue #9 original discovery does not prove the 475-ID inventory"
        )
    _, issue9_evidence = _bound_path(
        repo_root,
        final_issue9["evidence_path"],
        final_issue9["evidence_sha256"],
        "final Issue #9 closed-set evidence",
    )
    discovery_review = issue9_evidence.get("discovery_review")
    if (
        not isinstance(discovery_review, dict)
        or discovery_review.get("video_workflow_test_count")
        != BASELINE_TEST_COUNT
        or discovery_review.get("duplicate_test_ids") != 0
        or discovery_review.get("test_id_set_sha256")
        != BASELINE_TEST_ID_SET_SHA256
    ):
        raise PromotionValidationError(
            "final Issue #9 evidence does not prove the 475-ID baseline"
        )
    (
        bound_baseline_ids,
        delta_ids,
        current_ids,
        authority_sources,
    ) = _validate_superset_authority(
        repo_root,
        _object(report["superset_authority"], "superset_authority"),
        final_issue9,
    )
    if bound_baseline_ids != baseline_ids:
        raise PromotionValidationError(
            "report and superset authority baseline Test IDs differ"
        )
    implementation = _object(report["implementation"], "implementation")
    _fields(
        implementation,
        frozenset(
            {
                "reviewed_implementation_commit",
                "execution_evidence_commit",
                "authority_sources",
                "registry_path",
                "registry_sha256",
                "runner_path",
                "runner_sha256",
                "scheduler_path",
                "scheduler_sha256",
            }
        ),
        "implementation",
    )
    reviewed_implementation_commit = _commit(
        implementation["reviewed_implementation_commit"],
        "implementation.reviewed_implementation_commit",
    )
    execution_evidence_commit = _commit(
        implementation["execution_evidence_commit"],
        "implementation.execution_evidence_commit",
    )
    try:
        validate_evidence_only_commit_range(
            repo_root,
            reviewed_implementation_commit,
            execution_evidence_commit,
            label="reviewed implementation to execution evidence",
        )
    except SourceProvenanceError as error:
        raise PromotionValidationError(str(error)) from error
    head_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    live_head = head_result.stdout.strip()
    if head_result.returncode != 0:
        raise PromotionValidationError("validator-time live HEAD is invalid")
    _commit(live_head, "validator-time live HEAD")
    try:
        validate_evidence_only_commit_range(
            repo_root,
            execution_evidence_commit,
            live_head,
            label="execution evidence to validator-time live HEAD",
        )
    except SourceProvenanceError as error:
        raise PromotionValidationError(str(error)) from error
    report_authority_sources = _source_fingerprint_map(
        implementation["authority_sources"],
        expected_paths=PROMOTION_AUTHORITY_SOURCE_PATHS,
        label="report Promotion authority source",
    )
    if (
        report_authority_sources != authority_sources
        or implementation["registry_path"] != "config/test-suites.v1.json"
        or implementation["runner_path"] != "scripts/run_project_tests.py"
        or implementation["scheduler_path"]
        != "scripts/project_test_scheduler.py"
        or sha256_file(repo_root / implementation["registry_path"])
        != implementation["registry_sha256"]
    ):
        raise PromotionValidationError(
            "implementation Registry or runner binding is stale"
        )
    promotion = _object(
        report["promotion_closed_set"], "promotion_closed_set"
    )
    _fields(
        promotion,
        frozenset(
            {
                "suite_ids",
                "test_count",
                "test_id_set_sha256",
                "baseline_test_count",
                "baseline_test_id_set_sha256",
                "added_test_count",
                "added_test_id_set_sha256",
                "removed_test_count",
                "renamed_test_count",
            }
        ),
        "promotion_closed_set",
    )
    if (
        promotion["suite_ids"] != ["video-workflow"]
        or promotion["test_count"] != CURRENT_TEST_COUNT
        or promotion["test_id_set_sha256"] != CURRENT_TEST_ID_SET_SHA256
        or promotion["baseline_test_count"] != len(baseline_ids)
        or promotion["baseline_test_id_set_sha256"]
        != BASELINE_TEST_ID_SET_SHA256
        or promotion["added_test_count"] != len(delta_ids)
        or promotion["added_test_id_set_sha256"]
        != AUTHORIZED_DELTA_TEST_ID_SET_SHA256
        or promotion["removed_test_count"] != 0
        or promotion["renamed_test_count"] != 0
    ):
        raise PromotionValidationError(
            "promotion closed-set relation is invalid"
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
            execution_evidence_commit,
            1800,
            expected_test_ids=current_ids,
            expected_registry_sha256=implementation["registry_sha256"],
        )
        for run in runs
    ]
    if any(
        item["source_snapshot_id"] is None
        or item["run_finalization_sha256"] is None
        for item in validated_runs
    ):
        raise PromotionValidationError(
            "Promotion v2 parallel runs require source snapshot and "
            "run finalization authority"
        )
    if (
        implementation["registry_sha256"]
        != authority_sources[implementation["registry_path"]]
        or implementation["runner_sha256"]
        != authority_sources[implementation["runner_path"]]
        or implementation["scheduler_sha256"]
        != authority_sources[implementation["scheduler_path"]]
    ):
        raise PromotionValidationError(
            "named implementation bindings differ from the authority source "
            "closed set"
        )
    try:
        committed_authority_sources = committed_source_fingerprints(
            repo_root,
            reviewed_implementation_commit,
            authority_sources,
        )
    except SourceProvenanceError as error:
        raise PromotionValidationError(
            f"Promotion authority source commit is invalid: {error}"
        ) from error
    if committed_authority_sources != authority_sources:
        raise PromotionValidationError(
            "Promotion authority source differs from implementation commit"
        )
    try:
        evidence_commit_authority_sources = committed_source_fingerprints(
            repo_root,
            execution_evidence_commit,
            authority_sources,
        )
    except SourceProvenanceError as error:
        raise PromotionValidationError(
            f"Promotion evidence source commit is invalid: {error}"
        ) from error
    if evidence_commit_authority_sources != authority_sources:
        raise PromotionValidationError(
            "Promotion authority source differs between reviewed "
            "implementation and execution evidence commits"
        )
    for relative_path, declared_sha256 in authority_sources.items():
        run_bindings = [
            item["execution_source_bindings"].get(relative_path)
            for item in validated_runs
        ]
        if (
            any(not isinstance(binding, dict) for binding in run_bindings)
            or any(
                binding["committed_sha256"] != declared_sha256
                or binding["runtime_sha256"] != declared_sha256
                for binding in run_bindings
            )
            or sha256_file(repo_root / relative_path) != declared_sha256
        ):
            raise PromotionValidationError(
                "Promotion authority source differs from the implementation "
                "commit, run manifests, frozen bytes, report authority, or "
                "validator-time live bytes"
            )
    if (
        len({item["run_dir"] for item in validated_runs}) != 2
        or len({item["persisted_run_dir"] for item in validated_runs}) != 2
        or len({item["persisted_run_id"] for item in validated_runs}) != 2
        or len({item["persisted_run_nonce"] for item in validated_runs}) != 2
        or len(
            {item["target_process_identity"] for item in validated_runs}
        )
        != 2
        or len({item["test_run_sha256"] for item in validated_runs}) != 2
        or len({item["events_sha256"] for item in validated_runs}) != 2
        or len(
            {
                item["worker_identity_lineage_sha256"]
                for item in validated_runs
            }
        )
        != 2
    ):
        raise PromotionValidationError(
            "parallel runs must have distinct immutable execution identities"
        )
    outcome_hashes = {
        item["semantic_outcomes_sha256"] for item in validated_runs
    }
    assignment_hashes = {
        item["module_assignment_sha256"] for item in validated_runs
    }
    if len(outcome_hashes) != 1 or len(assignment_hashes) != 1:
        raise PromotionValidationError(
            "parallel run semantic outcomes or module assignments differ"
        )
    semantic = _object(report["semantic_parity"], "semantic_parity")
    _fields(
        semantic,
        frozenset(
            {
                "passed",
                "test_id_set_sha256",
                "semantic_outcomes_sha256",
                "module_assignment_sha256",
                "ignored_fields",
            }
        ),
        "semantic_parity",
    )
    if (
        semantic["passed"] is not True
        or semantic["test_id_set_sha256"] != CURRENT_TEST_ID_SET_SHA256
        or semantic["semantic_outcomes_sha256"] != next(iter(outcome_hashes))
        or semantic["module_assignment_sha256"]
        != next(iter(assignment_hashes))
        or set(semantic["ignored_fields"])
        != {"timestamps", "pids", "durations", "completion_order"}
    ):
        raise PromotionValidationError("semantic parity decision is invalid")
    migration = _object(report["migration_review"], "migration_review")
    _fields(
        migration,
        frozenset({"path", "sha256", "passed"}),
        "migration_review",
    )
    if (
        migration["path"] != MIGRATION_REVIEW_RELATIVE_PATH.as_posix()
        or migration["sha256"] != final_issue9["evidence_sha256"]
        or migration["passed"] is not True
    ):
        raise PromotionValidationError("migration review binding is invalid")
    _validate_migration_review(issue9_evidence)
    safety_sha256 = _validate_optimization_safety_review(
        repo_root,
        _object(
            report["optimization_safety_review"],
            "optimization_safety_review",
        ),
        reviewed_implementation_commit,
        authority_sources,
    )
    fingerprint_input = {
        "schema_version": 2,
        "authorization_model": authorization_model,
        "reviewed_implementation_commit": reviewed_implementation_commit,
        "execution_evidence_commit": execution_evidence_commit,
        "historical_baseline_commit": baseline["implementation_commit"],
        "historical_status_sha256": baseline["persisted_status_sha256"],
        "historical_exit_code_sha256": baseline[
            "persisted_exit_code_sha256"
        ],
        "final_issue9_commit": final_issue9["commit"],
        "final_issue9_inventory_sha256": final_issue9["inventory_sha256"],
        "baseline_test_id_set_sha256": BASELINE_TEST_ID_SET_SHA256,
        "authorized_delta_test_id_set_sha256": (
            AUTHORIZED_DELTA_TEST_ID_SET_SHA256
        ),
        "current_test_id_set_sha256": CURRENT_TEST_ID_SET_SHA256,
        "superset_authority_sha256": report["superset_authority"]["sha256"],
        "authority_sources": [
            {"path": path, "sha256": authority_sources[path]}
            for path in sorted(authority_sources)
        ],
        "registry_sha256": implementation["registry_sha256"],
        "runner_sha256": implementation["runner_sha256"],
        "scheduler_sha256": implementation["scheduler_sha256"],
        "parallel_runs": [
            {
                "discovery_sha256": run["discovery_sha256"],
                "summary_sha256": run["summary_sha256"],
                "persisted_status_sha256": run["persisted_status_sha256"],
                "persisted_exit_code_sha256": run[
                    "persisted_exit_code_sha256"
                ],
                "persisted_command_sha256": run["persisted_command_sha256"],
                "persisted_stdout_sha256": run["persisted_stdout_sha256"],
                "semantic_outcomes_sha256": run[
                    "semantic_outcomes_sha256"
                ],
                "module_assignment_sha256": run[
                    "module_assignment_sha256"
                ],
                "marker_sha256": validated_runs[index]["marker_sha256"],
                "test_run_sha256": validated_runs[index]["test_run_sha256"],
                "events_sha256": validated_runs[index]["events_sha256"],
                "timings_sha256": validated_runs[index]["timings_sha256"],
                "persisted_run_id": validated_runs[index][
                    "persisted_run_id"
                ],
                "persisted_run_nonce": validated_runs[index][
                    "persisted_run_nonce"
                ],
                "target_process_identity": validated_runs[index][
                    "target_process_identity"
                ],
                "worker_identity_lineage_sha256": validated_runs[index][
                    "worker_identity_lineage_sha256"
                ],
                "source_manifest_sha256": validated_runs[index][
                    "source_manifest_sha256"
                ],
                **(
                    {
                        "source_snapshot_id": validated_runs[index][
                            "source_snapshot_id"
                        ],
                        "source_snapshot_sha256": validated_runs[index][
                            "source_snapshot_sha256"
                        ],
                        "run_finalization_sha256": validated_runs[index][
                            "run_finalization_sha256"
                        ],
                    }
                    if validated_runs[index]["source_snapshot_id"] is not None
                    else {}
                ),
            }
            for index, run in enumerate(runs)
        ],
        "migration_review_sha256": migration["sha256"],
        "optimization_safety_review_sha256": safety_sha256,
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
        "schema_version": 2,
        "cutover_authorized": True,
        "promotion_fingerprint": expected_fingerprint,
        "test_count": CURRENT_TEST_COUNT,
    }


def validate_promotion_report(repo_root: Path) -> dict[str, Any]:
    """Dispatch and validate the fixed report without mutating evidence."""

    repo_root = repo_root.resolve(strict=True)
    report = _load_json(repo_root / REPORT_RELATIVE_PATH, "promotion report")
    if report.get("schema_name") != SCHEMA_NAME:
        raise PromotionValidationError("promotion report schema_name is invalid")
    version = report.get("schema_version")
    if type(version) is not int or version not in SCHEMA_RELATIVE_PATHS:
        raise PromotionValidationError(
            "promotion report schema_version must be integer 1 or 2"
        )
    if not (repo_root / SCHEMA_RELATIVE_PATHS[version]).is_file():
        raise PromotionValidationError(
            f"promotion report v{version} schema is missing"
        )
    if version == 1:
        return _validate_v1(repo_root, report)
    return _validate_v2(repo_root, report)


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

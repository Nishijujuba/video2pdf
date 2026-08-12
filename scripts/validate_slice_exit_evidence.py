from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.global_gate_exit_evidence import (
    ExitEvidenceValidationError,
    validate_global_gate_exit_evidence,
)
from video2pdf_workflow_kernel.guarded_delivery import (
    validate_acceptance_report,
    validate_delivery_guard_report,
)
from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    git_output,
    sha256_file,
    sha256_git_blob,
)
from video2pdf_workflow_kernel.source_acquisition import derive_source_identity
from slice3_exit_evidence_contract import (
    COMMANDS as SLICE3_COMMANDS,
    EVIDENCE_PREFIX as SLICE3_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as SLICE3_EXPECTED_CHECKPOINTS,
    FAULT_POINTS as SLICE3_FAULT_POINTS,
    FIXTURE_SPECS as SLICE3_FIXTURE_SPECS,
    RESULT_BINDINGS as SLICE3_RESULT_BINDINGS,
    RESULTS as SLICE3_RESULTS,
    SLICE_BASE_COMMIT as SLICE3_BASE_COMMIT,
)
from slice4_exit_evidence_contract import (
    COMMANDS as SLICE4_COMMANDS,
    EVIDENCE_PREFIX as SLICE4_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as SLICE4_EXPECTED_CHECKPOINTS,
    FAULT_POINT_BINDINGS as SLICE4_FAULT_POINT_BINDINGS,
    FAULT_POINTS as SLICE4_FAULT_POINTS,
    FIXTURE_SPECS as SLICE4_FIXTURE_SPECS,
    PLATFORM_SMOKE_SPECS as SLICE4_PLATFORM_SMOKE_SPECS,
    RESULT_BINDINGS as SLICE4_RESULT_BINDINGS,
    RESULTS as SLICE4_RESULTS,
    SLICE_BASE_COMMIT as SLICE4_BASE_COMMIT,
)
from slice5_exit_evidence_contract import (
    COMMANDS as SLICE5_COMMANDS,
    EVIDENCE_PREFIX as SLICE5_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as SLICE5_EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS as SLICE5_FIXTURE_SPECS,
    RESULT_BINDINGS as SLICE5_RESULT_BINDINGS,
    RESULTS as SLICE5_RESULTS,
    SLICE_BASE_COMMIT as SLICE5_BASE_COMMIT,
)
from slice6_exit_evidence_contract import (
    COMMANDS as SLICE6_COMMANDS,
    EVIDENCE_PREFIX as SLICE6_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as SLICE6_EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS as SLICE6_FIXTURE_SPECS,
    RESULT_BINDINGS as SLICE6_RESULT_BINDINGS,
    RESULTS as SLICE6_RESULTS,
    SLICE_BASE_COMMIT as SLICE6_BASE_COMMIT,
)
from slice7_exit_evidence_contract import (
    COMMANDS as SLICE7_COMMANDS,
    EVIDENCE_PREFIX as SLICE7_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as SLICE7_EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS as SLICE7_FIXTURE_SPECS,
    RESULT_BINDINGS as SLICE7_RESULT_BINDINGS,
    RESULTS as SLICE7_RESULTS,
    SLICE_BASE_COMMIT as SLICE7_BASE_COMMIT,
)
from slice8_exit_evidence_contract import (
    COMMANDS as SLICE8_COMMANDS,
    EVIDENCE_PREFIX as SLICE8_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as SLICE8_EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS as SLICE8_FIXTURE_SPECS,
    RESULT_BINDINGS as SLICE8_RESULT_BINDINGS,
    RESULTS as SLICE8_RESULTS,
    SLICE_BASE_COMMIT as SLICE8_BASE_COMMIT,
)
from slice9_exit_evidence_contract import (
    COMMANDS as SLICE9_COMMANDS,
    EVIDENCE_PREFIX as SLICE9_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as SLICE9_EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS as SLICE9_FIXTURE_SPECS,
    RESULT_BINDINGS as SLICE9_RESULT_BINDINGS,
    RESULTS as SLICE9_RESULTS,
    SLICE_BASE_COMMIT as SLICE9_BASE_COMMIT,
)
from slice10_exit_evidence_contract import (
    COMMANDS as SLICE10_COMMANDS,
    EVIDENCE_PREFIX as SLICE10_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as SLICE10_EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS as SLICE10_FIXTURE_SPECS,
    RESULT_BINDINGS as SLICE10_RESULT_BINDINGS,
    RESULTS as SLICE10_RESULTS,
    SLICE_BASE_COMMIT as SLICE10_BASE_COMMIT,
)
from issue43_exit_evidence_contract import (
    ACTIVATION_SCOPE as ISSUE43_ACTIVATION_SCOPE,
    ATOMIC_MEMBERS as ISSUE43_ATOMIC_MEMBERS,
    COMMANDS as ISSUE43_COMMANDS,
    EVIDENCE_PREFIX as ISSUE43_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as ISSUE43_EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS as ISSUE43_FIXTURE_SPECS,
    MIRROR_SPECS as ISSUE43_MIRROR_SPECS,
    POLICY_STATUS as ISSUE43_POLICY_STATUS,
    RESULT_BINDINGS as ISSUE43_RESULT_BINDINGS,
    RESULTS as ISSUE43_RESULTS,
    SLICE_BASE_COMMIT as ISSUE43_BASE_COMMIT,
)
from issue13_exit_evidence_contract import (
    ACTIVATION_SCOPE as ISSUE13_ACTIVATION_SCOPE,
    ATOMIC_MEMBERS as ISSUE13_ATOMIC_MEMBERS,
    COMMANDS as ISSUE13_COMMANDS,
    EVIDENCE_PREFIX as ISSUE13_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as ISSUE13_EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS as ISSUE13_FIXTURE_SPECS,
    PLATFORM_STATUSES as ISSUE13_PLATFORM_STATUSES,
    RESULT_BINDINGS as ISSUE13_RESULT_BINDINGS,
    RESULTS as ISSUE13_RESULTS,
    SLICE_BASE_COMMIT as ISSUE13_BASE_COMMIT,
)
from issue14_exit_evidence_contract import (
    ACTIVATION_SCOPE as ISSUE14_ACTIVATION_SCOPE,
    ATOMIC_MEMBERS as ISSUE14_ATOMIC_MEMBERS,
    COMMANDS as ISSUE14_COMMANDS,
    EVIDENCE_PREFIX as ISSUE14_EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS as ISSUE14_EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS as ISSUE14_FIXTURE_SPECS,
    PLATFORM_STATUSES as ISSUE14_PLATFORM_STATUSES,
    RESULT_BINDINGS as ISSUE14_RESULT_BINDINGS,
    RESULTS as ISSUE14_RESULTS,
    SLICE_BASE_COMMIT as ISSUE14_BASE_COMMIT,
)


SCHEMA_PATH = PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json"
SLICE_CONFIGS = {
    1: {
        "base_commit": "96089b99c9ae63fff61107e1920fc3481ffc0802",
        "evidence_prefix": "evidence/slice-01/",
        "checkpoints": [{"name": "source_ready", "status": "current"}],
        "command_ids": [
            "slice0-regression",
            "slice1-contracts",
            "slice1-public-deep-tests",
            "slice1-review-hardening-tests",
            "slice1-gate4-saga-containment-tests",
            "slice1-gate7-review-repair-tests",
            "slice0-exit-evidence",
            "slice1-syntax",
            "slice1-diff-check",
        ],
        "result_kinds": ["positive", "negative", "recovery"],
    },
    2: {
        "base_commit": "904f46409b87aca96aeecf5cb0be4855c2cfdafa",
        "evidence_prefix": "evidence/slice-02/",
        "checkpoints": [
            {"name": "source_ready", "status": "current"},
            {"name": "source_acquisition_decision_ready", "status": "current"},
        ],
        "command_ids": [
            "slice0-regression",
            "slice2-contracts",
            "slice1-regression",
            "slice2-task-promotion",
            "slice2-task-promotion-hardening",
            "slice2-review-repairs",
            "slice2-control-store-transaction-scope",
            "slice1-exit-evidence",
            "slice2-syntax",
            "slice2-diff-check",
        ],
        "result_kinds": ["positive", "negative", "fencing", "recovery"],
        "required_fencing_results": ["late_and_superseded_workers_are_fenced"],
    },
    3: {
        "base_commit": SLICE3_BASE_COMMIT,
        "evidence_prefix": SLICE3_EVIDENCE_PREFIX,
        "checkpoints": SLICE3_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _ in SLICE3_COMMANDS],
        "commands": [
            {
                "test_id": test_id,
                "command": list(command),
                "expected_exit_code": 0,
            }
            for test_id, command in SLICE3_COMMANDS
        ],
        "result_kinds": [
            "positive",
            "negative",
            "quota",
            "fencing",
            "fairness",
            "restart",
            "recovery",
        ],
        "results": SLICE3_RESULTS,
        "result_bindings": SLICE3_RESULT_BINDINGS,
        "fixture_specs": SLICE3_FIXTURE_SPECS,
        "fault_points": list(SLICE3_FAULT_POINTS),
        "fault_point_bindings": [
            {"fault_point": point, "command_id": "slice3-resource-admission"}
            for point in SLICE3_FAULT_POINTS
        ],
    },
    4: {
        "base_commit": SLICE4_BASE_COMMIT,
        "evidence_prefix": SLICE4_EVIDENCE_PREFIX,
        "checkpoints": SLICE4_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _ in SLICE4_COMMANDS],
        "commands": [
            {
                "test_id": test_id,
                "command": list(command),
                "expected_exit_code": 0,
            }
            for test_id, command in SLICE4_COMMANDS
        ],
        "result_kinds": ["positive", "negative", "fencing", "restart", "recovery"],
        "results": SLICE4_RESULTS,
        "result_bindings": SLICE4_RESULT_BINDINGS,
        "fixture_specs": SLICE4_FIXTURE_SPECS,
        "fault_points": list(SLICE4_FAULT_POINTS),
        "fault_point_bindings": list(SLICE4_FAULT_POINT_BINDINGS),
        "platform_smoke_specs": list(SLICE4_PLATFORM_SMOKE_SPECS),
    },
    5: {
        "base_commit": SLICE5_BASE_COMMIT,
        "evidence_prefix": SLICE5_EVIDENCE_PREFIX,
        "checkpoints": SLICE5_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _ in SLICE5_COMMANDS],
        "commands": [
            {
                "test_id": test_id,
                "command": list(command),
                "expected_exit_code": 0,
            }
            for test_id, command in SLICE5_COMMANDS
        ],
        "result_kinds": ["positive", "negative", "recovery"],
        "results": SLICE5_RESULTS,
        "result_bindings": SLICE5_RESULT_BINDINGS,
        "fixture_specs": SLICE5_FIXTURE_SPECS,
    },
    6: {
        "base_commit": SLICE6_BASE_COMMIT,
        "evidence_prefix": SLICE6_EVIDENCE_PREFIX,
        "checkpoints": SLICE6_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _ in SLICE6_COMMANDS],
        "commands": [{"test_id":test_id,"command":list(command),"expected_exit_code":0} for test_id, command in SLICE6_COMMANDS],
        "result_kinds": ["positive", "negative", "fencing", "restart", "recovery"],
        "results": SLICE6_RESULTS,
        "result_bindings": SLICE6_RESULT_BINDINGS,
        "fixture_specs": SLICE6_FIXTURE_SPECS,
    },
    7: {
        "base_commit": SLICE7_BASE_COMMIT,
        "evidence_prefix": SLICE7_EVIDENCE_PREFIX,
        "checkpoints": SLICE7_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _, _ in SLICE7_COMMANDS],
        "result_kinds": ["positive", "negative", "recovery"],
        "results": SLICE7_RESULTS,
        "result_bindings": SLICE7_RESULT_BINDINGS,
        "fixture_specs": SLICE7_FIXTURE_SPECS,
    },
    8: {
        "base_commit": SLICE8_BASE_COMMIT,
        "evidence_prefix": SLICE8_EVIDENCE_PREFIX,
        "checkpoints": SLICE8_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _, _ in SLICE8_COMMANDS],
        "commands": [
            {
                "test_id": test_id,
                "command": list(command),
                "expected_exit_code": expected_exit_code,
            }
            for test_id, command, expected_exit_code in SLICE8_COMMANDS
        ],
        "result_kinds": ["positive", "negative", "recovery"],
        "results": SLICE8_RESULTS,
        "result_bindings": SLICE8_RESULT_BINDINGS,
        "fixture_specs": SLICE8_FIXTURE_SPECS,
    },
    9: {
        "base_commit": SLICE9_BASE_COMMIT,
        "evidence_prefix": SLICE9_EVIDENCE_PREFIX,
        "checkpoints": SLICE9_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _, _ in SLICE9_COMMANDS],
        "commands": [
            {"test_id": test_id, "command": list(command), "expected_exit_code": expected_exit_code}
            for test_id, command, expected_exit_code in SLICE9_COMMANDS
        ],
        "result_kinds": ["positive", "negative"],
        "results": SLICE9_RESULTS,
        "result_bindings": SLICE9_RESULT_BINDINGS,
        "fixture_specs": SLICE9_FIXTURE_SPECS,
    },
    10: {
        "base_commit": SLICE10_BASE_COMMIT,
        "evidence_prefix": SLICE10_EVIDENCE_PREFIX,
        "checkpoints": SLICE10_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _, _ in SLICE10_COMMANDS],
        "commands": [
            {"test_id": test_id, "command": list(command), "expected_exit_code": expected_exit_code}
            for test_id, command, expected_exit_code in SLICE10_COMMANDS
        ],
        "result_kinds": ["positive", "negative", "recovery", "fencing"],
        "results": SLICE10_RESULTS,
        "result_bindings": SLICE10_RESULT_BINDINGS,
        "fixture_specs": SLICE10_FIXTURE_SPECS,
    },
    11: {
        "base_commit": ISSUE43_BASE_COMMIT,
        "evidence_prefix": ISSUE43_EVIDENCE_PREFIX,
        "checkpoints": ISSUE43_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _, _ in ISSUE43_COMMANDS],
        "commands": [
            {
                "test_id": test_id,
                "command": list(command),
                "expected_exit_code": expected_exit_code,
            }
            for test_id, command, expected_exit_code in ISSUE43_COMMANDS
        ],
        "result_kinds": ["positive", "negative", "recovery", "fencing"],
        "results": ISSUE43_RESULTS,
        "result_bindings": ISSUE43_RESULT_BINDINGS,
        "fixture_specs": ISSUE43_FIXTURE_SPECS,
        "activation_scope": ISSUE43_ACTIVATION_SCOPE,
    },
    12: {
        "base_commit": ISSUE13_BASE_COMMIT,
        "evidence_prefix": ISSUE13_EVIDENCE_PREFIX,
        "checkpoints": ISSUE13_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _, _ in ISSUE13_COMMANDS],
        "commands": [
            {"test_id": test_id, "command": list(command), "expected_exit_code": expected_exit_code}
            for test_id, command, expected_exit_code in ISSUE13_COMMANDS
        ],
        "result_kinds": ["positive", "negative", "recovery"],
        "results": ISSUE13_RESULTS,
        "result_bindings": ISSUE13_RESULT_BINDINGS,
        "fixture_specs": ISSUE13_FIXTURE_SPECS,
        "activation_scope": ISSUE13_ACTIVATION_SCOPE,
    },
    13: {
        "base_commit": ISSUE14_BASE_COMMIT,
        "evidence_prefix": ISSUE14_EVIDENCE_PREFIX,
        "checkpoints": ISSUE14_EXPECTED_CHECKPOINTS,
        "command_ids": [test_id for test_id, _, _ in ISSUE14_COMMANDS],
        "commands": [
            {"test_id": test_id, "command": list(command), "expected_exit_code": expected_exit_code}
            for test_id, command, expected_exit_code in ISSUE14_COMMANDS
        ],
        "result_kinds": ["positive", "negative", "recovery"],
        "results": ISSUE14_RESULTS,
        "result_bindings": ISSUE14_RESULT_BINDINGS,
        "fixture_specs": ISSUE14_FIXTURE_SPECS,
        "activation_scope": ISSUE14_ACTIVATION_SCOPE,
    },
}


class EvidenceError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        first_failing_gate: str = "exit_evidence_validation",
        error_code: str = "invalid_exit_evidence",
    ) -> None:
        super().__init__(message)
        self.first_failing_gate = first_failing_gate
        self.error_code = error_code


def git(*arguments: str) -> str:
    try:
        # Path-bearing outputs (diff-tree --name-only, ls-files) must stay in
        # raw UTF-8 so they compare against manifest evidence paths; git quotes
        # non-ASCII paths by default when core.quotePath is true.
        return git_output(PROJECT_ROOT, "-c", "core.quotePath=false", *arguments)
    except EvidenceSupportError as exc:
        raise EvidenceError(str(exc)) from exc


def resolve_project_path(value: str) -> Path:
    root = PROJECT_ROOT.resolve()
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise EvidenceError(f"evidence path escapes project root: {value}") from exc
    return path


def changed_worktree_paths() -> set[str]:
    changed: set[str] = set()
    for arguments in (
        ("diff", "--name-only", "HEAD"),
        ("diff", "--cached", "--name-only", "HEAD"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        output = git(*arguments)
        changed.update(line for line in output.splitlines() if line)
    return changed


def commit_paths(commit: str) -> set[str]:
    parent_line = git("rev-list", "--parents", "-n", "1", commit).split()
    if len(parent_line) != 2:
        raise EvidenceError(f"commit must have exactly one parent: {commit}")
    return set(
        line
        for line in git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", commit
        ).splitlines()
        if line
    )


def validate_lineage(
    manifest: dict[str, Any], manifest_path: Path, *, pre_publication: bool
) -> None:
    implementation_commit = manifest["implementation_commit"]
    git("cat-file", "-e", f"{implementation_commit}^{{commit}}")
    allowed = set(manifest["evidence_paths"])
    manifest_relative = manifest_path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    if pre_publication:
        if git("rev-parse", "HEAD") != implementation_commit:
            raise EvidenceError(
                "pre-publication HEAD must equal implementation_commit"
            )
        changed = changed_worktree_paths()
        if not changed or manifest_relative not in changed:
            raise EvidenceError("pre-publication evidence changes are missing")
        forbidden = sorted(changed - allowed)
        if forbidden:
            raise EvidenceError(
                f"pre-publication worktree contains non-evidence changes: {forbidden}"
            )
        return

    head_blob = git("rev-parse", f"HEAD:{manifest_relative}")
    worktree_blob = git("hash-object", f"--path={manifest_relative}", "--", manifest_relative)
    if head_blob != worktree_blob:
        raise EvidenceError("current manifest differs from its committed HEAD blob")
    publication_commit: str | None = None
    for candidate in git("log", "--format=%H", "HEAD", "--", manifest_relative).splitlines():
        try:
            candidate_blob = git("rev-parse", f"{candidate}:{manifest_relative}")
        except EvidenceError:
            continue
        if candidate_blob == head_blob:
            publication_commit = candidate
            break
    if publication_commit is None:
        raise EvidenceError("cannot locate evidence publication commit")
    parents = git("rev-list", "--parents", "-n", "1", publication_commit).split()
    if len(parents) != 2 or parents[1] != implementation_commit:
        raise EvidenceError(
            "evidence publication must be the direct child of implementation_commit"
        )
    published = commit_paths(publication_commit)
    if published != allowed:
        raise EvidenceError(
            f"evidence publication paths differ from closed allowlist: {sorted(published ^ allowed)}"
        )
    if not (commit_paths(implementation_commit) - allowed):
        raise EvidenceError("implementation_commit cannot be evidence-only")


def slice_config(manifest: dict[str, Any]) -> dict[str, Any]:
    if "slice" not in manifest:
        base = manifest.get("slice_base_commit")
        matches = [
            config
            for config in SLICE_CONFIGS.values()
            if config["base_commit"] == base
        ]
        if len(matches) == 1:
            return matches[0]
        raise EvidenceError("Slice Exit Evidence authority cannot be inferred")
    number = manifest["slice"]["number"]
    try:
        return SLICE_CONFIGS[number]
    except KeyError as exc:
        raise EvidenceError(f"unsupported Slice Exit Evidence number: {number}") from exc


def validate_issue43_cutover(manifest: dict[str, Any]) -> None:
    if manifest.get("slice", {}).get("number") != 11:
        return

    scope = manifest.get("activation_scope")
    if isinstance(scope, dict) and scope.get("platform_kernel_authority") != "unchanged":
        raise EvidenceError(
            "Global Gate Exit Evidence cannot change platform Kernel authority",
            first_failing_gate="activation_scope",
            error_code="platform_kernel_authority_changed",
        )
    if scope != ISSUE43_ACTIVATION_SCOPE:
        raise EvidenceError(
            "Global Gate Exit Evidence activation scope differs from its closed authority",
            first_failing_gate="activation_scope",
            error_code="unsupported_activation_scope",
        )

    if manifest.get("atomic_members") != list(ISSUE43_ATOMIC_MEMBERS):
        raise EvidenceError(
            "Global Gate atomic member registry is incomplete or reordered",
            first_failing_gate="atomic_members",
            error_code="atomic_member_set_mismatch",
        )
    expected_member_status = {member: "active" for member in ISSUE43_ATOMIC_MEMBERS}
    if manifest.get("atomic_member_status") != expected_member_status:
        raise EvidenceError(
            "Every Global Gate atomic member must be active",
            first_failing_gate="atomic_member_status",
            error_code="atomic_member_inactive",
        )

    mirror_checks = manifest.get("mirror_checks")
    if not isinstance(mirror_checks, list) or len(mirror_checks) != len(ISSUE43_MIRROR_SPECS):
        raise EvidenceError(
            "Global Gate mirror checks are incomplete",
            first_failing_gate="mirror_checks",
            error_code="incomplete_mirror_checks",
        )
    for check, (source_relative, mirror_relative) in zip(
        mirror_checks, ISSUE43_MIRROR_SPECS, strict=True
    ):
        source = (PROJECT_ROOT / source_relative).resolve()
        mirror = (PROJECT_ROOT / mirror_relative).resolve()
        source_sha256 = sha256_file(source)
        mirror_sha256 = sha256_file(mirror)
        if (
            check.get("source_path") != str(source)
            or check.get("mirror_path") != str(mirror)
            or check.get("source_sha256") != source_sha256
            or check.get("mirror_sha256") != mirror_sha256
            or check.get("status") != "equal"
            or source_sha256 != mirror_sha256
        ):
            raise EvidenceError(
                f"Global Gate mirror check is stale: {source_relative}",
                first_failing_gate="mirror_checks",
                error_code="stale_or_unequal_mirror",
            )

    if manifest.get("policy_status") != ISSUE43_POLICY_STATUS:
        raise EvidenceError(
            "Global Gate policy status is inactive",
            first_failing_gate="policy_status",
            error_code="inactive_global_gate_policy",
        )

    failed_commands = [
        command.get("test_id", "<unknown>")
        for command in manifest.get("commands", [])
        if command.get("actual_exit_code") != command.get("expected_exit_code")
        or command.get("conforms") is not True
    ]
    if failed_commands:
        raise EvidenceError(
            f"Global Gate atomic members failed: {failed_commands}",
            first_failing_gate="atomic_group",
            error_code="atomic_member_failed",
        )

    blocking_contract_gaps = [
        item
        for item in manifest.get("unresolved_exceptions", [])
        if item.get("blocking") and item.get("code") == "contract_gap"
    ]
    if blocking_contract_gaps:
        raise EvidenceError(
            "Global Gate Exit Evidence contains an unresolved Contract Gap",
            first_failing_gate="contract_gap",
            error_code="unresolved_contract_gap",
        )

    expected_ids = {
        result_id for values in ISSUE43_RESULTS.values() for result_id in values
    }
    provided_ids = {
        result_id
        for values in manifest.get("results", {}).values()
        for result_id in values
    }
    missing = sorted(expected_ids - provided_ids)
    if missing:
        raise EvidenceError(
            f"Global Gate qualification results are incomplete: {missing}",
            first_failing_gate="qualification_result_coverage",
            error_code="incomplete_results",
        )
    unsupported = sorted(provided_ids - expected_ids)
    if unsupported:
        raise EvidenceError(
            f"Global Gate qualification results use unsupported identities: {unsupported}",
            first_failing_gate="qualification_result_coverage",
            error_code="unsupported_result_identity",
        )


def validate_issue13_cutover(manifest: dict[str, Any]) -> None:
    if manifest.get("slice", {}).get("number") != 12:
        return
    if manifest.get("activation_scope") != ISSUE13_ACTIVATION_SCOPE:
        raise EvidenceError(
            "Bilibili cutover activation scope differs from its closed authority",
            first_failing_gate="activation_scope",
            error_code="unsupported_activation_scope",
        )
    statuses = manifest.get("platform_statuses")
    if isinstance(statuses, dict) and statuses.get("youtube") != "active_legacy":
        raise EvidenceError(
            "YouTube must retain Legacy platform authority during Bilibili cutover",
            first_failing_gate="platform_statuses",
            error_code="youtube_platform_authority_changed",
        )
    if statuses != ISSUE13_PLATFORM_STATUSES:
        raise EvidenceError(
            "Bilibili cutover platform statuses differ from their closed authority",
            first_failing_gate="platform_statuses",
            error_code="platform_status_set_mismatch",
        )
    if manifest.get("atomic_members") != list(ISSUE13_ATOMIC_MEMBERS):
        raise EvidenceError(
            "Bilibili cutover atomic member registry is incomplete or reordered",
            first_failing_gate="atomic_members",
            error_code="atomic_member_set_mismatch",
        )
    expected_status = {member: "active" for member in ISSUE13_ATOMIC_MEMBERS}
    if manifest.get("atomic_member_status") != expected_status:
        raise EvidenceError(
            "Every Bilibili cutover atomic member must be active",
            first_failing_gate="atomic_member_status",
            error_code="atomic_member_inactive",
        )
    guarded = manifest.get("guarded_delivery_evidence")
    if not isinstance(guarded, dict):
        raise EvidenceError(
            "Bilibili cutover lacks collected guarded-delivery evidence",
            first_failing_gate="guarded_delivery_evidence",
            error_code="guarded_delivery_evidence_missing",
        )
    expected_roles = {
        "run_record",
        "source_manifest",
        "acceptance_report_v2",
        "delivery_guard_report",
        "video_delivery_target",
        "session_delivery_target",
        "delivery_task_index",
        "global_gate_authority",
        "final_pdf",
    }
    artifacts = guarded.get("artifacts")
    roles = {
        artifact.get("role")
        for artifact in artifacts
        if isinstance(artifact, dict)
    } if isinstance(artifacts, list) else set()
    if (
        guarded.get("canonical_platform") != "bilibili"
        or guarded.get("delivery_stage") != "delivered"
        or roles != expected_roles
        or not isinstance(guarded.get("collection"), dict)
        or not isinstance(guarded.get("qualification_run"), dict)
    ):
        raise EvidenceError(
            "Bilibili guarded-delivery collection is incomplete",
            first_failing_gate="guarded_delivery_evidence",
            error_code="guarded_delivery_evidence_invalid",
        )


def validate_issue14_cutover(manifest: dict[str, Any]) -> None:
    if manifest.get("slice", {}).get("number") != 13:
        return
    if manifest.get("activation_scope") != ISSUE14_ACTIVATION_SCOPE:
        raise EvidenceError(
            "YouTube cutover activation scope differs from its closed authority",
            first_failing_gate="activation_scope",
            error_code="unsupported_activation_scope",
        )
    statuses = manifest.get("platform_statuses")
    if isinstance(statuses, dict) and statuses.get("bilibili") != "active_kernel":
        raise EvidenceError(
            "Bilibili must retain active Kernel platform authority during YouTube cutover",
            first_failing_gate="platform_statuses",
            error_code="bilibili_platform_authority_changed",
        )
    if statuses != ISSUE14_PLATFORM_STATUSES:
        raise EvidenceError(
            "YouTube cutover platform statuses differ from their closed authority",
            first_failing_gate="platform_statuses",
            error_code="platform_status_set_mismatch",
        )
    if manifest.get("atomic_members") != list(ISSUE14_ATOMIC_MEMBERS):
        raise EvidenceError(
            "YouTube cutover atomic member registry is incomplete or reordered",
            first_failing_gate="atomic_members",
            error_code="atomic_member_set_mismatch",
        )
    expected_status = {member: "active" for member in ISSUE14_ATOMIC_MEMBERS}
    if manifest.get("atomic_member_status") != expected_status:
        raise EvidenceError(
            "Every YouTube cutover atomic member must be active",
            first_failing_gate="atomic_member_status",
            error_code="atomic_member_inactive",
        )
    guarded = manifest.get("guarded_delivery_evidence")
    if not isinstance(guarded, dict):
        raise EvidenceError(
            "YouTube cutover lacks collected guarded-delivery evidence",
            first_failing_gate="guarded_delivery_evidence",
            error_code="guarded_delivery_evidence_missing",
        )
    expected_roles = {
        "run_record",
        "source_manifest",
        "acceptance_report_v2",
        "delivery_guard_report",
        "video_delivery_target",
        "session_delivery_target",
        "delivery_task_index",
        "global_gate_authority",
        "final_pdf",
    }
    artifacts = guarded.get("artifacts")
    roles = {
        artifact.get("role")
        for artifact in artifacts
        if isinstance(artifact, dict)
    } if isinstance(artifacts, list) else set()
    if (
        guarded.get("canonical_platform") != "youtube"
        or guarded.get("delivery_stage") != "delivered"
        or roles != expected_roles
        or not isinstance(guarded.get("collection"), dict)
        or not isinstance(guarded.get("qualification_run"), dict)
    ):
        raise EvidenceError(
            "YouTube guarded-delivery collection is incomplete",
            first_failing_gate="guarded_delivery_evidence",
            error_code="guarded_delivery_evidence_invalid",
        )


def validate_semantics(manifest: dict[str, Any]) -> None:
    validate_issue43_cutover(manifest)
    validate_issue13_cutover(manifest)
    validate_issue14_cutover(manifest)
    commands = manifest["commands"]
    identities = [command["test_id"] for command in commands]
    if len(identities) != len(set(identities)):
        raise EvidenceError("command test_id values must be unique")
    expected_command_ids = slice_config(manifest)["command_ids"]
    if identities != expected_command_ids:
        raise EvidenceError(
            "Slice Exit Evidence commands differ from the registered closed test set"
        )
    config = slice_config(manifest)
    expected_commands = config.get("commands")
    if expected_commands is not None:
        provided_commands = [
            {
                "test_id": command["test_id"],
                "command": command["command"],
                "expected_exit_code": command["expected_exit_code"],
            }
            for command in commands
        ]
        expected_exit_codes = [command["expected_exit_code"] for command in commands]
        if any(code != 0 for code in expected_exit_codes):
            raise EvidenceError(
                "Slice Exit Evidence expected exit code must be zero for every closed command"
            )
        if provided_commands != expected_commands:
            raise EvidenceError(
                "Slice Exit Evidence closed command vector differs from its registered authority"
            )
        if any(command["actual_exit_code"] != 0 for command in commands):
            raise EvidenceError(
                "Slice Exit Evidence closed command did not exit successfully"
            )
    for command in commands:
        derived = command["actual_exit_code"] == command["expected_exit_code"]
        if command["conforms"] != derived:
            raise EvidenceError(
                f"command conforms is stale: {command['test_id']}"
            )
    expected_checkpoints = slice_config(manifest)["checkpoints"]
    if manifest["expected_checkpoints"] != expected_checkpoints:
        raise EvidenceError(
            "Slice Exit Evidence checkpoints differ from the registered Slice authority"
        )
    expected_fixture_specs = config.get("fixture_specs")
    if expected_fixture_specs is not None:
        provided_fixture_specs = [
            (fixture["role"], fixture["path"]) for fixture in manifest["fixtures"]
        ]
        if provided_fixture_specs != list(expected_fixture_specs):
            raise EvidenceError(
                "Slice Exit Evidence fixtures differ from the registered closed fixture set"
            )
    expected_fault_points = config.get("fault_points")
    if expected_fault_points is not None and manifest.get("fault_points") != expected_fault_points:
        raise EvidenceError(
            "Slice Exit Evidence fault points differ from the registered closed fault set"
        )
    result_identities = [
        identity
        for values in manifest["results"].values()
        for identity in values
    ]
    if len(result_identities) != len(set(result_identities)):
        raise EvidenceError("result identities must be unique across evidence kinds")
    missing_fencing = set(config.get("required_fencing_results", [])) - set(
        manifest["results"].get("fencing", [])
    )
    if missing_fencing:
        raise EvidenceError(
            f"Slice fencing evidence is incomplete: {sorted(missing_fencing)}"
        )
    expected_results = config.get("results")
    if expected_results is not None and manifest["results"] != expected_results:
        raise EvidenceError(
            "Slice Exit Evidence results differ from the registered closed result set"
        )
    expected_bindings = config.get("result_bindings")
    if expected_bindings is not None:
        bindings = manifest.get("result_bindings", [])
        expected_result_pairs = {
            (result_id, kind)
            for kind, values in manifest["results"].items()
            for result_id in values
        }
        provided_result_pairs = {
            (binding["result_id"], binding["result_kind"])
            for binding in bindings
        }
        if (
            len(bindings) != len(provided_result_pairs)
            or provided_result_pairs != expected_result_pairs
        ):
            raise EvidenceError(
                "Slice Exit Evidence result bindings differ from the complete result set",
                first_failing_gate="qualification_result_binding",
                error_code="result_binding_coverage_invalid",
            )
        command_by_id = {command["test_id"]: command for command in commands}
        for binding in bindings:
            command = command_by_id.get(binding["command_id"])
            if command is None:
                raise EvidenceError(
                    "Slice Exit Evidence result binding names an unknown command",
                    first_failing_gate="qualification_result_binding",
                    error_code="result_binding_command_unknown",
                )
            if binding["test_target"] not in command["command"]:
                raise EvidenceError(
                    "Slice Exit Evidence result binding lacks an explicitly executed test target",
                    first_failing_gate="qualification_result_binding",
                    error_code="result_binding_public_tracer_missing",
                )
        if bindings != expected_bindings:
            raise EvidenceError(
                "Slice Exit Evidence result bindings differ from the registered authority",
                first_failing_gate="qualification_result_binding",
                error_code="result_binding_authority_stale",
            )
    validate_platform_smokes(manifest, config)
    derived_pass = (
        all(command["conforms"] for command in commands)
        and all(manifest["results"][kind] for kind in config["result_kinds"])
        and not any(item["blocking"] for item in manifest["unresolved_exceptions"])
    )
    if (manifest["overall_decision"] == "pass") != derived_pass:
        raise EvidenceError("overall_decision differs from its evidence")
    if manifest["overall_decision"] == "pass" and manifest["unresolved_exceptions"]:
        raise EvidenceError("passing evidence cannot contain unresolved exceptions")


def validate_platform_smokes(
    manifest: dict[str, Any], config: dict[str, Any]
) -> None:
    expected_specs = config.get("platform_smoke_specs")
    provided = manifest.get("platform_smokes")
    if expected_specs is None:
        if provided is not None:
            raise EvidenceError("platform smoke evidence is unsupported for this Slice")
        return
    if not isinstance(provided, list) or len(provided) != len(expected_specs):
        raise EvidenceError("platform smoke evidence differs from the registered closed set")
    command_by_id = {command["test_id"]: command for command in manifest["commands"]}
    for smoke, spec in zip(provided, expected_specs, strict=True):
        platform = spec["platform"]
        command = command_by_id.get(spec["command_id"])
        source_manifest = smoke.get("source_manifest", {})
        canonical_item_id = source_manifest.get("canonical_item_id")
        expected_source_identity = (
            derive_source_identity(platform, canonical_item_id)
            if isinstance(canonical_item_id, str) and canonical_item_id
            else None
        )
        if (
            smoke.get("platform") != platform
            or smoke.get("adapter_id") != platform
            or smoke.get("command_id") != spec["command_id"]
            or command is None
            or smoke.get("authentication_classification") != "cookie_accepted"
            or smoke.get("target_checkpoint", {}).get("name") != "source_ready"
            or smoke.get("target_checkpoint", {}).get("status") != "current"
            or source_manifest.get("path") != spec["source_manifest_path"]
            or smoke.get("sanitized_log", {}).get("path")
            != spec["sanitized_log_path"]
            or smoke.get("sanitized_log", {}).get("path")
            != command["log"]["path"]
            or smoke.get("sanitized_log", {}).get("sha256")
            != command["log"]["sha256"]
            or smoke.get("target_checkpoint", {}).get("evidence_sha256")
            != source_manifest.get("sha256")
            or source_manifest.get("canonical_platform") != platform
            or source_manifest.get("source_identity") != expected_source_identity
        ):
            raise EvidenceError(
                f"platform smoke evidence differs from the registered {platform} authority"
            )
        argv = smoke.get("command_argv_redacted", [])
        cookie_indexes = [index for index, token in enumerate(argv) if token == "--cookies"]
        placeholder_indexes = [
            index for index, token in enumerate(argv) if token == "<COOKIE_FILE>"
        ]
        unsafe_tokens = [
            token
            for token in argv
            if token != "<COOKIE_FILE>"
            and (
                token.lower().startswith("cookie:")
                or "private-cookie" in token.lower()
                or token.lower().endswith("cookies.txt")
                or (len(token) > 2 and token[1] == ":" and token[2] in "/\\")
            )
        ]
        if (
            len(cookie_indexes) != 1
            or placeholder_indexes != [cookie_indexes[0] + 1]
            or unsafe_tokens
        ):
            raise EvidenceError(
                f"platform smoke redacted command argv is unsafe for {platform}"
            )
        if platform == "youtube":
            runtime_indexes = [
                index for index, token in enumerate(argv) if token == "--js-runtimes"
            ]
            if (
                len(runtime_indexes) != 1
                or runtime_indexes[0] + 1 >= len(argv)
                or argv[runtime_indexes[0] + 1] != "node"
            ):
                raise EvidenceError(
                    "platform smoke redacted command argv omits the YouTube Node.js runtime"
                )


def validate_implementation_artifacts(manifest: dict[str, Any]) -> None:
    slice_base_commit = manifest["slice_base_commit"]
    implementation_commit = manifest["implementation_commit"]
    config = slice_config(manifest)
    if slice_base_commit != config["base_commit"]:
        raise EvidenceError("Slice base commit differs from its fixed authority")
    git("merge-base", "--is-ancestor", slice_base_commit, implementation_commit)
    try:
        expected = fingerprint_implementation_changes(
            PROJECT_ROOT,
            slice_base_commit,
            implementation_commit,
            excluded_prefixes=(config["evidence_prefix"],),
        )
    except EvidenceSupportError as exc:
        raise EvidenceError(str(exc)) from exc
    provided = manifest["artifact_fingerprints"]
    provided_paths = [item["path"] for item in provided]
    if len(provided_paths) != len(set(provided_paths)):
        raise EvidenceError("complete implementation change set has duplicate paths")
    expected_by_path = {item["path"]: item for item in expected}
    provided_by_path = {item["path"]: item for item in provided}
    if set(provided_by_path) != set(expected_by_path):
        missing = sorted(set(expected_by_path) - set(provided_by_path))
        extra = sorted(set(provided_by_path) - set(expected_by_path))
        raise EvidenceError(
            "artifact_fingerprints must equal the complete implementation change set: "
            f"missing={missing}, extra={extra}"
        )
    for path, expected_item in expected_by_path.items():
        provided_item = provided_by_path[path]
        if provided_item != expected_item:
            raise EvidenceError(
                "complete implementation change set fingerprint differs for "
                f"{path}: expected {expected_item}, got {provided_item}"
            )


def validate_bindings(manifest: dict[str, Any], manifest_path: Path) -> None:
    manifest_relative = manifest_path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    log_paths = {command["log"]["path"] for command in manifest["commands"]}
    persisted_artifacts = [
        artifact
        for command in manifest["commands"]
        for key, artifact in command.get("persisted_run", {}).items()
        if key != "run_id"
    ]
    persisted_paths = {artifact["path"] for artifact in persisted_artifacts}
    smoke_manifest_paths = {
        smoke["source_manifest"]["path"]
        for smoke in manifest.get("platform_smokes", [])
    }
    guarded = manifest.get("guarded_delivery_evidence")
    guarded_artifacts: list[dict[str, Any]] = []
    if isinstance(guarded, dict):
        collection = guarded.get("collection")
        if isinstance(collection, dict):
            guarded_artifacts.append(collection)
        guarded_artifacts.extend(
            artifact
            for artifact in guarded.get("artifacts", [])
            if isinstance(artifact, dict)
        )
        qualification = guarded.get("qualification_run")
        if isinstance(qualification, dict):
            guarded_artifacts.extend(
                artifact
                for key, artifact in qualification.items()
                if key != "run_id" and isinstance(artifact, dict)
            )
    guarded_paths = {artifact["path"] for artifact in guarded_artifacts}
    expected_evidence_paths = {
        manifest_relative,
        *log_paths,
        *persisted_paths,
        *smoke_manifest_paths,
        *guarded_paths,
    }
    if set(manifest["evidence_paths"]) != expected_evidence_paths:
        raise EvidenceError(
            "evidence_paths must be exactly manifest, command logs, persisted terminal artifacts, and platform smoke manifests"
        )
    seen: set[str] = set()
    bound = [command["log"] for command in manifest["commands"]]
    bound.extend(persisted_artifacts)
    bound.extend(manifest["fixtures"])
    bound.extend(
        smoke["source_manifest"] for smoke in manifest.get("platform_smokes", [])
    )
    bound.extend(guarded_artifacts)
    fixture_paths = {fixture["path"] for fixture in manifest["fixtures"]}
    for item in bound:
        path = resolve_project_path(item["path"])
        identity = str(path).casefold()
        if identity in seen:
            raise EvidenceError(f"fingerprinted path is duplicated: {item['path']}")
        seen.add(identity)
        if item["path"] in fixture_paths:
            try:
                actual = sha256_git_blob(
                    PROJECT_ROOT,
                    manifest["implementation_commit"],
                    item["path"],
                )
            except EvidenceSupportError as exc:
                raise EvidenceError(str(exc)) from exc
        else:
            if not path.is_file():
                raise EvidenceError(
                    f"fingerprinted path does not exist: {item['path']}"
                )
            actual = sha256_file(path)
        if actual != item["sha256"]:
            raise EvidenceError(
                f"fingerprint mismatch for {item['path']}: expected {item['sha256']}, got {actual}"
            )
    if manifest.get("slice", {}).get("number") == 11:
        for command in manifest["commands"]:
            persisted = command.get("persisted_run")
            if not isinstance(persisted, dict):
                raise EvidenceError(
                    "Issue #43 command lacks persisted terminal evidence",
                    first_failing_gate="persisted_command_evidence",
                    error_code="persisted_command_evidence_missing",
                )
            try:
                command_record = json.loads(
                    resolve_project_path(persisted["command_record"]["path"]).read_text(encoding="utf-8")
                )
                terminal_status = json.loads(
                    resolve_project_path(persisted["terminal_status"]["path"]).read_text(encoding="utf-8")
                )
                exit_code = int(
                    resolve_project_path(persisted["exit_code"]["path"]).read_text(encoding="utf-8").strip()
                )
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise EvidenceError(
                    "Issue #43 persisted evidence cannot be decoded",
                    first_failing_gate="persisted_command_evidence",
                    error_code="persisted_command_artifact_invalid",
                ) from exc
            run_id = persisted["run_id"]
            if (
                command_record.get("run_id") != run_id
                or command_record.get("argv") != command["command"]
                or command_record.get("cwd") != str(PROJECT_ROOT.resolve())
            ):
                raise EvidenceError(
                    "Issue #43 persisted command identity differs from its manifest binding",
                    first_failing_gate="persisted_command_identity",
                    error_code="persisted_command_identity_stale",
                )
            if command_record.get("accepted_exit_codes") != [command["expected_exit_code"]]:
                raise EvidenceError(
                    "Issue #43 persisted accepted exit codes differ from the command contract",
                    first_failing_gate="persisted_command_identity",
                    error_code="persisted_command_argv_stale",
                )
            if terminal_status.get("run_id") != run_id or terminal_status.get("state") != "succeeded":
                raise EvidenceError(
                    "Issue #43 persisted run lacks successful terminal status",
                    first_failing_gate="persisted_command_terminal",
                    error_code="persisted_command_terminal_invalid",
                )
            if terminal_status.get("exit_code") != exit_code or exit_code != command["actual_exit_code"]:
                raise EvidenceError(
                    "Issue #43 persisted exit-code evidence is inconsistent",
                    first_failing_gate="persisted_command_terminal",
                    error_code="persisted_command_terminal_invalid",
                )
            if terminal_status.get("security", {}).get("acceptance_evidence_eligible") is not True:
                raise EvidenceError(
                    "Issue #43 persisted run is ineligible for acceptance evidence",
                    first_failing_gate="persisted_command_security",
                    error_code="persisted_command_security_failure",
                )
    if manifest.get("slice", {}).get("number") == 12:
        guarded = manifest["guarded_delivery_evidence"]
        artifact_paths = {
            item["role"]: resolve_project_path(item["path"])
            for item in guarded["artifacts"]
        }
        try:
            validate_acceptance_report(
                project_root=PROJECT_ROOT,
                report_path=artifact_paths["acceptance_report_v2"],
                run_id=guarded["run_id"],
            )
            validate_delivery_guard_report(
                report_path=artifact_paths["delivery_guard_report"]
            )
        except (ContractError, KeyError) as exc:
            raise EvidenceError(
                "Issue #13 guarded-delivery decisions are not authoritative passes",
                first_failing_gate="guarded_delivery_evidence",
                error_code="guarded_delivery_decision_invalid",
            ) from exc
        qualification = guarded["qualification_run"]
        try:
            command_record = json.loads(
                resolve_project_path(qualification["command_record"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            terminal_status = json.loads(
                resolve_project_path(qualification["terminal_status"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            exit_code = int(
                resolve_project_path(qualification["exit_code"]["path"])
                .read_text(encoding="utf-8")
                .strip()
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise EvidenceError(
                "Issue #13 persisted qualification evidence cannot be decoded",
                first_failing_gate="guarded_delivery_evidence",
                error_code="guarded_delivery_qualification_invalid",
            ) from exc
        expected_argv = list(ISSUE13_COMMANDS[1][1])
        if (
            command_record.get("argv") != expected_argv
            or command_record.get("cwd") != str(PROJECT_ROOT.resolve())
            or command_record.get("accepted_exit_codes") != [0]
        ):
            raise EvidenceError(
                "Issue #13 persisted qualification command differs from its closed contract",
                first_failing_gate="guarded_delivery_evidence",
                error_code="guarded_delivery_qualification_identity_stale",
            )
        if (
            command_record.get("run_id") != qualification["run_id"]
            or terminal_status.get("run_id") != qualification["run_id"]
            or terminal_status.get("state") != "succeeded"
            or terminal_status.get("exit_code") != 0
            or exit_code != 0
            or terminal_status.get("security", {}).get(
                "acceptance_evidence_eligible"
            )
            is not True
        ):
            raise EvidenceError(
                "Issue #13 qualification Run is not succeeded eligible evidence",
                first_failing_gate="guarded_delivery_evidence",
                error_code="guarded_delivery_qualification_failed",
            )


def validate_command_log_provenance(manifest: dict[str, Any]) -> None:
    marker = (
        f"EVIDENCE_IMPLEMENTATION_COMMIT: {manifest['implementation_commit']}".encode(
            "ascii"
        )
    )
    for command in manifest["commands"]:
        path = resolve_project_path(command["log"]["path"])
        marker_lines = [line for line in path.read_bytes().splitlines() if line == marker]
        if len(marker_lines) != 1:
            raise EvidenceError(
                "command log implementation commit marker is missing, duplicated, or stale: "
                f"{command['test_id']}"
            )
    fault_points = manifest.get("fault_points")
    if fault_points is not None:
        if "slice" in manifest or "slice_base_commit" in manifest:
            bindings = slice_config(manifest).get("fault_point_bindings", [])
        else:
            # Compatibility for the focused Slice 3 provenance unit test. Full
            # manifests always resolve through the registered Slice authority.
            bindings = [
                {"fault_point": point, "command_id": "slice3-resource-admission"}
                for point in fault_points
            ]
        if [item["fault_point"] for item in bindings] != fault_points:
            raise EvidenceError("fault point provenance binding authority is stale")
        command_by_id = {
            command["test_id"]: command for command in manifest["commands"]
        }
        for command_id in dict.fromkeys(item["command_id"] for item in bindings):
            command = command_by_id.get(command_id)
            if command is None:
                raise EvidenceError(
                    f"fault point provenance requires registered command: {command_id}"
                )
            lines = resolve_project_path(command["log"]["path"]).read_bytes().splitlines()
            expected_lines = [
                f"EVIDENCE_FAULT_POINT: {item['fault_point']}".encode("ascii")
                for item in bindings
                if item["command_id"] == command_id
            ]
            actual_lines = [
                line for line in lines if line.startswith(b"EVIDENCE_FAULT_POINT: ")
            ]
            if actual_lines != expected_lines:
                raise EvidenceError(
                    f"fault point provenance is missing, duplicated, or stale: {command_id}"
                )


def validate_manifest(
    manifest_path: Path, *, schema_only: bool, pre_publication: bool
) -> None:
    ContractRegistry(PROJECT_ROOT).check()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise EvidenceError(f"Exit Evidence v2 Schema is invalid: {exc.message}") from exc
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        path = "/".join(str(part) for part in exc.absolute_path) or "$"
        raise EvidenceError(f"Schema validation failed at {path}: {exc.message}") from exc
    if schema_only:
        return
    if value.get("slice", {}).get("number") == 11 and not pre_publication:
        try:
            validate_global_gate_exit_evidence(
                manifest_path,
                project_root=PROJECT_ROOT,
            )
        except ExitEvidenceValidationError as exc:
            raise EvidenceError(
                str(exc),
                first_failing_gate=exc.first_failing_gate,
                error_code=exc.error_code,
            ) from exc
        return
    validate_semantics(value)
    validate_bindings(value, manifest_path)
    validate_command_log_provenance(value)
    validate_implementation_artifacts(value)
    validate_lineage(value, manifest_path, pre_publication=pre_publication)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate generic Slice Exit Evidence v2.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--pre-publication", action="store_true")
    args = parser.parse_args(argv or sys.argv[1:])
    try:
        validate_manifest(
            args.manifest.resolve(),
            schema_only=args.schema_only,
            pre_publication=args.pre_publication,
        )
    except EvidenceError as exc:
        print(
            "INVALID: "
            f"first_failing_gate={exc.first_failing_gate}; "
            f"error_code={exc.error_code}; {exc}",
            file=sys.stderr,
        )
        return 1
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

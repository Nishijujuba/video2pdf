from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    git_output,
    sha256_file,
    sha256_git_blob,
)


SLICE = {"number": 11, "name": "global-acceptance-v2-gate"}
SLICE_BASE_COMMIT = "64f3fb1638f601b533cb0ee4dec908203c1bef71"
EVIDENCE_PREFIX = "evidence/global-gate/"
QUALIFICATION_CONTRACT_SHA256 = "96800f8c08dc5d1a48bbe7a5d64da6e78630677695eb0e692faa527cb319b701"
ATOMIC_MEMBERS = (
    "catalogs", "projections", "criteria_migration", "schemas", "providers",
    "validators", "hooks", "skills", "project_instructions", "mirrors", "tests",
    "activation_documentation",
)
ACTIVATION_SCOPE = {
    "kind": "active_global_gate",
    "runtime_authority_change": True,
    "components_activated": ["acceptance_report_v2", "delivery_quality_context"],
    "legacy_track_authority": "acceptance_report_v2",
    "platform_kernel_authority": "unchanged",
    "qualification_contract_sha256": QUALIFICATION_CONTRACT_SHA256,
}
FIXTURE_SPECS = (
    ("legacy_acceptance_input_contract", "schemas/global-gate/legacy-acceptance-input-set.v1.schema.json"),
    ("acceptance_input_binding_contract", "schemas/delivery-quality/v1/acceptance-v2-input-binding.v1.schema.json"),
    ("acceptance_report_v2_contract", "schemas/delivery-quality/v1/acceptance-report-v2.v1.schema.json"),
    ("exit_evidence_manifest_contract", "schemas/exit-evidence-manifest.v2.schema.json"),
)
MIRROR_SPECS = tuple(
    (f".agents/skills/{name}/SKILL.md", f".claude/skills/{name}/SKILL.md")
    for name in ("final-delivery-acceptance", "bilibili-render-pdf", "youtube-render-pdf")
)
MIRROR_SPECS += ((
    ".agents/skills/final-delivery-acceptance/scripts/delivery_guard.py",
    ".claude/skills/final-delivery-acceptance/scripts/delivery_guard.py",
),)
EXPECTED_CHECKPOINTS = [
    {"name": "acceptance_report_v2_global_authority", "status": "current"},
    {"name": "legacy_acceptance_report_v1_authority", "status": "retired"},
    {"name": "platform_kernel_authority", "status": "preserved"},
]


class ExitEvidenceValidationError(ValueError):
    def __init__(self, message: str, *, first_failing_gate: str, error_code: str) -> None:
        super().__init__(message)
        self.first_failing_gate = first_failing_gate
        self.error_code = error_code


@dataclass(frozen=True)
class ValidatedExitEvidence:
    path: Path
    sha256: str
    value: dict[str, Any]


def _fail(message: str, gate: str, code: str) -> None:
    raise ExitEvidenceValidationError(message, first_failing_gate=gate, error_code=code)


def _git(project_root: Path, *arguments: str, gate: str, code: str) -> str:
    try:
        return git_output(project_root, *arguments)
    except EvidenceSupportError as exc:
        raise ExitEvidenceValidationError(
            str(exc), first_failing_gate=gate, error_code=code
        ) from exc


def _project_file(project_root: Path, value: str, *, gate: str, missing_code: str) -> Path:
    candidate = (project_root / value).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        _fail(f"evidence path escapes project root: {value}", gate, "evidence_path_escape")
    if not candidate.is_file():
        _fail(f"evidence path is missing: {value}", gate, missing_code)
    return candidate


def _commit_paths(project_root: Path, commit: str) -> set[str]:
    parents = _git(
        project_root, "rev-list", "--parents", "-n", "1", commit,
        gate="historical_evidence", code="historical_evidence_lineage_invalid",
    ).split()
    if len(parents) != 2:
        _fail("evidence commit must have exactly one parent", "historical_evidence", "historical_evidence_lineage_invalid")
    return set(filter(None, _git(
        project_root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit,
        gate="historical_evidence", code="historical_evidence_lineage_invalid",
    ).splitlines()))


def _validate_schema(project_root: Path, value: Any) -> dict[str, Any]:
    schema_path = project_root / "schemas/exit-evidence-manifest.v2.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.absolute_path))
    except (OSError, UnicodeError, json.JSONDecodeError, SchemaError) as exc:
        raise ExitEvidenceValidationError(
            f"Exit Evidence v2 schema is unavailable or invalid: {exc}",
            first_failing_gate="exit_evidence_schema", error_code="exit_evidence_schema_invalid",
        ) from exc
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path) or "$"
        _fail(f"Schema validation failed at {location}: {errors[0].message}", "exit_evidence_schema", "exit_evidence_schema_invalid")
    return value


def _validate_closed_policy(project_root: Path, value: dict[str, Any]) -> None:
    if value["slice"] != SLICE or value["slice_base_commit"] != SLICE_BASE_COMMIT:
        _fail("Global Gate Slice authority is stale", "slice_authority", "slice_authority_stale")
    scope = value["activation_scope"]
    if scope.get("platform_kernel_authority") != "unchanged":
        _fail("Global Gate cannot change platform Kernel authority", "activation_scope", "platform_kernel_authority_changed")
    if scope != ACTIVATION_SCOPE:
        _fail("Global Gate activation scope is unsupported", "activation_scope", "unsupported_activation_scope")
    if value["atomic_members"] != list(ATOMIC_MEMBERS):
        _fail("Global Gate atomic member registry is incomplete", "atomic_members", "atomic_member_set_mismatch")
    if value["atomic_member_status"] != {member: "active" for member in ATOMIC_MEMBERS}:
        _fail("Global Gate atomic member is inactive", "atomic_member_status", "global_gate_atomic_member_failed")
    if value["expected_checkpoints"] != EXPECTED_CHECKPOINTS:
        _fail("Global Gate checkpoints are stale", "expected_checkpoints", "expected_checkpoints_stale")
    if [(item["role"], item["path"]) for item in value["fixtures"]] != list(FIXTURE_SPECS):
        _fail("Global Gate fixtures differ from authority", "fixture_binding", "fixture_set_stale")
    if value["policy_status"] != "active_global_gate":
        _fail("Global Gate policy is inactive", "policy_status", "global_gate_policy_inactive")
    commands = value["commands"]
    if any(item["expected_exit_code"] != 0 or item["actual_exit_code"] != 0 or item["conforms"] is not True for item in commands):
        _fail("Global Gate qualification command failed", "atomic_group", "atomic_member_failed")
    if any(item["blocking"] for item in value["unresolved_exceptions"]):
        _fail("Global Gate has an unresolved contract gap", "contract_gap", "unresolved_contract_gap")
    result_pairs = {(result_id, kind) for kind, ids in value["results"].items() for result_id in ids}
    bindings = value["result_bindings"]
    binding_bytes = (
        json.dumps(bindings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if hashlib.sha256(binding_bytes).hexdigest() != QUALIFICATION_CONTRACT_SHA256:
        _fail("Global Gate qualification result contract is stale", "qualification_result_binding", "global_gate_qualification_contract_stale")
    binding_pairs = {(item["result_id"], item["result_kind"]) for item in bindings}
    if len(bindings) != len(binding_pairs) or binding_pairs != result_pairs:
        _fail("Global Gate qualification results are incomplete", "qualification_result_coverage", "incomplete_results")
    command_by_id = {item["test_id"]: item for item in commands}
    for binding in bindings:
        command = command_by_id.get(binding["command_id"])
        if command is None or binding["test_target"] not in command["command"]:
            _fail("Global Gate result lacks an executed public tracer", "qualification_result_binding", "result_binding_public_tracer_missing")
    derived_pass = all(item["conforms"] for item in commands) and all(value["results"].values()) and not value["unresolved_exceptions"]
    if value["overall_decision"] != ("pass" if derived_pass else "fail"):
        _fail("Global Gate overall decision is stale", "overall_decision", "overall_decision_stale")


def _validate_implementation(project_root: Path, value: dict[str, Any]) -> None:
    implementation = value["implementation_commit"]
    _git(project_root, "cat-file", "-e", f"{implementation}^{{commit}}", gate="implementation_lineage", code="implementation_commit_invalid")
    _git(project_root, "merge-base", "--is-ancestor", SLICE_BASE_COMMIT, implementation, gate="implementation_lineage", code="implementation_not_descendant")
    try:
        expected = fingerprint_implementation_changes(
            project_root, SLICE_BASE_COMMIT, implementation,
            excluded_prefixes=(EVIDENCE_PREFIX,),
        )
    except EvidenceSupportError as exc:
        raise ExitEvidenceValidationError(str(exc), first_failing_gate="artifact_fingerprints", error_code="artifact_fingerprints_stale") from exc
    if value["artifact_fingerprints"] != expected:
        _fail("artifact_fingerprints do not equal the implementation commit diff", "artifact_fingerprints", "artifact_fingerprints_stale")


def _validate_bindings(project_root: Path, value: dict[str, Any], manifest_path: Path) -> None:
    relative_manifest = manifest_path.relative_to(project_root).as_posix()
    log_paths = {item["log"]["path"] for item in value["commands"]}
    persisted_paths: set[str] = set()
    seen: set[str] = set()
    marker = f"EVIDENCE_IMPLEMENTATION_COMMIT: {value['implementation_commit']}".encode("ascii")
    for command in value["commands"]:
        log = command["log"]
        path = _project_file(project_root, log["path"], gate="command_log", missing_code="command_log_missing")
        identity = str(path).casefold()
        if identity in seen:
            _fail("command log path is duplicated", "command_log", "command_log_duplicated")
        seen.add(identity)
        if sha256_file(path) != log["sha256"]:
            _fail("command log fingerprint is stale", "command_log", "command_log_sha256_stale")
        if [line for line in path.read_bytes().splitlines() if line == marker] != [marker]:
            _fail("command log implementation marker is missing, duplicated, or stale", "command_log_provenance", "command_log_provenance_invalid")
        persisted = command.get("persisted_run")
        if not isinstance(persisted, dict):
            _fail("qualification command lacks persisted terminal evidence", "persisted_command_evidence", "persisted_command_evidence_missing")
        artifacts: dict[str, Path] = {}
        for artifact_name, expected_role in (
            ("command_record", "persisted_command_record"),
            ("terminal_status", "persisted_terminal_status"),
            ("exit_code", "persisted_exit_code"),
        ):
            artifact = persisted[artifact_name]
            if artifact["role"] != expected_role:
                _fail("persisted evidence role is stale", "persisted_command_evidence", "persisted_command_role_stale")
            artifact_path = _project_file(
                project_root,
                artifact["path"],
                gate="persisted_command_evidence",
                missing_code="persisted_command_artifact_missing",
            )
            if artifact["path"] in persisted_paths or sha256_file(artifact_path) != artifact["sha256"]:
                _fail("persisted evidence fingerprint is stale or duplicated", "persisted_command_evidence", "persisted_command_artifact_stale")
            persisted_paths.add(artifact["path"])
            artifacts[artifact_name] = artifact_path
        try:
            command_record = json.loads(artifacts["command_record"].read_text(encoding="utf-8"))
            terminal_status = json.loads(artifacts["terminal_status"].read_text(encoding="utf-8"))
            exit_code = int(artifacts["exit_code"].read_text(encoding="utf-8").strip())
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ExitEvidenceValidationError(
                "persisted evidence cannot be decoded",
                first_failing_gate="persisted_command_evidence",
                error_code="persisted_command_artifact_invalid",
            ) from exc
        run_id = persisted["run_id"]
        if command_record.get("schema_name") != "persisted-command" or command_record.get("run_id") != run_id:
            _fail("persisted command identity is stale", "persisted_command_identity", "persisted_command_identity_stale")
        if (
            command_record.get("argv") != command["command"]
            or command_record.get("accepted_exit_codes") != [command["expected_exit_code"]]
            or command_record.get("cwd") != str(project_root)
        ):
            _fail("persisted command contract differs from manifest", "persisted_command_identity", "persisted_command_argv_stale")
        if terminal_status.get("schema_name") != "persisted-command-status" or terminal_status.get("run_id") != run_id:
            _fail("persisted terminal identity is stale", "persisted_command_terminal", "persisted_command_terminal_identity_stale")
        if terminal_status.get("state") != "succeeded" or terminal_status.get("exit_code") != exit_code or exit_code != command["actual_exit_code"]:
            _fail("persisted run does not prove successful terminal completion", "persisted_command_terminal", "persisted_command_terminal_invalid")
        security = terminal_status.get("security", {})
        if security.get("acceptance_evidence_eligible") is not True or security.get("classification") == "security_failure":
            _fail("persisted run is ineligible for acceptance evidence", "persisted_command_security", "persisted_command_security_failure")
    for fixture in value["fixtures"]:
        try:
            actual = sha256_git_blob(project_root, value["implementation_commit"], fixture["path"])
        except EvidenceSupportError as exc:
            raise ExitEvidenceValidationError(str(exc), first_failing_gate="fixture_fingerprint", error_code="fixture_missing_at_implementation") from exc
        if actual != fixture["sha256"]:
            _fail("fixture fingerprint is stale", "fixture_fingerprint", "fixture_sha256_stale")
    if set(value["evidence_paths"]) != {relative_manifest, *log_paths, *persisted_paths}:
        _fail("evidence_paths differ from canonical manifest and logs", "evidence_paths", "evidence_paths_stale")


def _validate_mirrors(project_root: Path, value: dict[str, Any]) -> None:
    checks = value["mirror_checks"]
    if len(checks) != len(MIRROR_SPECS):
        _fail("Global Gate mirror checks are incomplete", "mirror_checks", "global_gate_mirror_stale")
    for check, (source_relative, mirror_relative) in zip(checks, MIRROR_SPECS, strict=True):
        source = _project_file(project_root, source_relative, gate="mirror_checks", missing_code="mirror_missing")
        mirror = _project_file(project_root, mirror_relative, gate="mirror_checks", missing_code="mirror_missing")
        source_sha = sha256_file(source)
        mirror_sha = sha256_file(mirror)
        if check != {"source_path": str(source), "mirror_path": str(mirror), "source_sha256": source_sha, "mirror_sha256": mirror_sha, "status": "equal"} or source_sha != mirror_sha:
            _fail("Global Gate mirror is stale or unequal", "mirror_checks", "global_gate_mirror_stale")


def _validate_publication(project_root: Path, value: dict[str, Any], manifest_path: Path) -> None:
    relative = manifest_path.relative_to(project_root).as_posix()
    head_blob = _git(project_root, "rev-parse", f"HEAD:{relative}", gate="historical_evidence", code="canonical_manifest_uncommitted")
    worktree_blob = _git(project_root, "hash-object", f"--path={relative}", "--", relative, gate="historical_evidence", code="canonical_manifest_uncommitted")
    if head_blob != worktree_blob:
        _fail("canonical manifest differs from committed HEAD", "historical_evidence", "canonical_manifest_uncommitted")
    publication = None
    for candidate in _git(project_root, "log", "--format=%H", "HEAD", "--", relative, gate="historical_evidence", code="historical_evidence_lineage_invalid").splitlines():
        try:
            if _git(project_root, "rev-parse", f"{candidate}:{relative}", gate="historical_evidence", code="historical_evidence_lineage_invalid") == head_blob:
                publication = candidate
                break
        except ExitEvidenceValidationError:
            continue
    if publication is None:
        _fail("canonical manifest publication commit is absent", "historical_evidence", "historical_evidence_lineage_invalid")
    head = _git(
        project_root, "rev-parse", "HEAD",
        gate="implementation_currentness", code="evidence_publication_not_current",
    )
    if publication != head:
        _fail(
            "Global Gate evidence publication is not the current committed authority",
            "implementation_currentness",
            "evidence_publication_not_current",
        )
    parents = _git(project_root, "rev-list", "--parents", "-n", "1", publication, gate="historical_evidence", code="historical_evidence_lineage_invalid").split()
    if len(parents) != 2 or parents[1] != value["implementation_commit"]:
        _fail("evidence publication is not the direct child of implementation commit", "historical_evidence", "historical_evidence_lineage_invalid")
    if _commit_paths(project_root, publication) != set(value["evidence_paths"]):
        _fail("evidence publication paths differ from manifest", "historical_evidence", "historical_evidence_paths_stale")
    if not (_commit_paths(project_root, value["implementation_commit"]) - set(value["evidence_paths"])):
        _fail("implementation commit is evidence-only", "historical_evidence", "implementation_commit_evidence_only")


def validate_global_gate_exit_evidence(manifest_path: Path, *, project_root: Path) -> ValidatedExitEvidence:
    """Validate the complete committed Issue 43 Manifest v2 before activation CAS."""
    root = project_root.resolve()
    path = manifest_path.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        _fail("canonical manifest escapes project root", "evidence_paths", "evidence_path_escape")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExitEvidenceValidationError(str(exc), first_failing_gate="exit_evidence_schema", error_code="exit_evidence_unavailable") from exc
    _validate_schema(root, value)
    _validate_closed_policy(root, value)
    _validate_mirrors(root, value)
    _validate_implementation(root, value)
    _validate_bindings(root, value, path)
    _validate_publication(root, value, path)
    return ValidatedExitEvidence(path=path, sha256=sha256_file(path), value=value)

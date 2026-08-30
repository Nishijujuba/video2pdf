from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    implementation_change_tombstones,
    git_blob_bytes,
    git_output,
    sha256_file,
    sha256_git_blob,
)


VerificationPurpose = Literal["activation", "candidate_publication", "release_audit"]
"""Why the evidence manifest is being validated.

``activation`` validates that the evidence is the current committed authority:
the worktree schema and qualification contract must equal the running
constants, and the publication must be HEAD.

``candidate_publication`` validates a complete release package against the
worktree: manifest-bound schema and contract semantics, worktree mirror and
persisted-artifact consistency with the declared fingerprints, and a proven
publication lineage.

``release_audit`` validates the same complete package against its historical
publication tree: manifest-bound schema and contract semantics, mirrors and
persisted artifacts read from the publication commit, and the same proven
publication lineage. No authentication depends on later worktree drift.
"""


SLICE = {"number": 11, "name": "global-acceptance-v2-gate"}
SLICE_BASE_COMMIT = "64f3fb1638f601b533cb0ee4dec908203c1bef71"
EVIDENCE_PREFIX = "evidence/global-gate/"
QUALIFICATION_CONTRACT_SHA256 = "62a3ed565b264a1b4b29d4b61a8803afef4bc637eae6b37a899a1672483750c7"
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


def _project_path(project_root: Path, value: str, *, gate: str) -> Path:
    """Resolve and escape-check a declared evidence path without touching the worktree."""
    candidate = (project_root / value).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        _fail(f"evidence path escapes project root: {value}", gate, "evidence_path_escape")
    return candidate


def _evidence_bytes(
    project_root: Path,
    value: str,
    *,
    anchor: str | None,
    gate: str,
    missing_code: str,
) -> bytes:
    """Read evidence file bytes from the worktree or a committed tree.

    ``anchor=None`` reads the current worktree (publication-gate semantics).
    A commit anchor reads the immutable publication tree (release-audit
    semantics) so a valid historical package is never rejected because the
    worktree drifted after publication.
    """
    candidate = (project_root / value).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        _fail(f"evidence path escapes project root: {value}", gate, "evidence_path_escape")
    if anchor is None:
        if not candidate.is_file():
            _fail(f"evidence path is missing: {value}", gate, missing_code)
        return candidate.read_bytes()
    try:
        relative = candidate.relative_to(project_root).as_posix()
        return git_blob_bytes(project_root, anchor, relative)
    except EvidenceSupportError as exc:
        _fail(
            f"evidence path is unavailable in the publication tree: {value}",
            gate,
            missing_code,
        )


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


def _validate_schema(
    project_root: Path,
    value: Any,
    *,
    use_historical_schema: bool,
) -> dict[str, Any]:
    try:
        if use_historical_schema:
            implementation_commit = value.get("implementation_commit")
            if (
                not isinstance(implementation_commit, str)
                or len(implementation_commit) != 40
                or any(character not in "0123456789abcdef" for character in implementation_commit)
            ):
                _fail(
                    "Global Gate implementation commit identity is invalid",
                    "exit_evidence_schema",
                    "exit_evidence_schema_invalid",
                )
            schema_text = _git(
                project_root,
                "show",
                f"{implementation_commit}:schemas/exit-evidence-manifest.v2.schema.json",
                gate="exit_evidence_schema",
                code="exit_evidence_schema_invalid",
            )
        else:
            schema_text = (
                project_root / "schemas/exit-evidence-manifest.v2.schema.json"
            ).read_text(encoding="utf-8")
        schema = json.loads(schema_text)
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


def _validate_closed_policy(
    project_root: Path,
    value: dict[str, Any],
    *,
    use_historical_contract: bool,
) -> None:
    if value["slice"] != SLICE or value["slice_base_commit"] != SLICE_BASE_COMMIT:
        _fail("Global Gate Slice authority is stale", "slice_authority", "slice_authority_stale")
    scope = value["activation_scope"]
    if scope.get("platform_kernel_authority") != "unchanged":
        _fail("Global Gate cannot change platform Kernel authority", "activation_scope", "platform_kernel_authority_changed")
    expected_scope = dict(ACTIVATION_SCOPE)
    if use_historical_contract:
        expected_scope["qualification_contract_sha256"] = scope.get(
            "qualification_contract_sha256"
        )
    if scope != expected_scope:
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
    expected_contract_sha256 = (
        scope["qualification_contract_sha256"]
        if use_historical_contract
        else QUALIFICATION_CONTRACT_SHA256
    )
    if hashlib.sha256(binding_bytes).hexdigest() != expected_contract_sha256:
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
        expected_tombstones = implementation_change_tombstones(
            project_root, SLICE_BASE_COMMIT, implementation,
            excluded_prefixes=(EVIDENCE_PREFIX,),
        )
    except EvidenceSupportError as exc:
        raise ExitEvidenceValidationError(str(exc), first_failing_gate="artifact_fingerprints", error_code="artifact_fingerprints_stale") from exc
    if value["artifact_fingerprints"] != expected:
        _fail("artifact_fingerprints do not equal the implementation commit diff", "artifact_fingerprints", "artifact_fingerprints_stale")
    if value.get("implementation_tombstones", []) != expected_tombstones:
        _fail("implementation_tombstones do not equal the removed implementation paths", "implementation_tombstones", "implementation_tombstones_stale")


def _validate_bindings(
    project_root: Path,
    value: dict[str, Any],
    manifest_path: Path,
    *,
    anchor: str | None,
) -> None:
    relative_manifest = manifest_path.relative_to(project_root).as_posix()
    log_paths = {item["log"]["path"] for item in value["commands"]}
    persisted_paths: set[str] = set()
    seen: set[str] = set()
    marker = f"EVIDENCE_IMPLEMENTATION_COMMIT: {value['implementation_commit']}".encode("ascii")
    for command in value["commands"]:
        log = command["log"]
        path = (
            _project_path(project_root, log["path"], gate="command_log")
            if anchor is not None
            else _project_file(project_root, log["path"], gate="command_log", missing_code="command_log_missing")
        )
        identity = str(path).casefold()
        if identity in seen:
            _fail("command log path is duplicated", "command_log", "command_log_duplicated")
        seen.add(identity)
        log_bytes = _evidence_bytes(
            project_root, log["path"], anchor=anchor,
            gate="command_log", missing_code="command_log_missing",
        )
        if log["sha256"] != hashlib.sha256(log_bytes).hexdigest():
            _fail("command log fingerprint is stale", "command_log", "command_log_sha256_stale")
        if [line for line in log_bytes.splitlines() if line == marker] != [marker]:
            _fail("command log implementation marker is missing, duplicated, or stale", "command_log_provenance", "command_log_provenance_invalid")
        persisted = command.get("persisted_run")
        if not isinstance(persisted, dict):
            _fail("qualification command lacks persisted terminal evidence", "persisted_command_evidence", "persisted_command_evidence_missing")
        for artifact_name, expected_role in (
            ("command_record", "persisted_command_record"),
            ("terminal_status", "persisted_terminal_status"),
            ("exit_code", "persisted_exit_code"),
        ):
            artifact = persisted[artifact_name]
            if artifact["role"] != expected_role:
                _fail("persisted evidence role is stale", "persisted_command_evidence", "persisted_command_role_stale")
            if anchor is None:
                _project_file(
                    project_root,
                    artifact["path"],
                    gate="persisted_command_evidence",
                    missing_code="persisted_command_artifact_missing",
                )
            else:
                _project_path(
                    project_root,
                    artifact["path"],
                    gate="persisted_command_evidence",
                )
            artifact_bytes = _evidence_bytes(
                project_root, artifact["path"], anchor=anchor,
                gate="persisted_command_evidence",
                missing_code="persisted_command_artifact_missing",
            )
            if artifact["path"] in persisted_paths or hashlib.sha256(artifact_bytes).hexdigest() != artifact["sha256"]:
                _fail("persisted evidence fingerprint is stale or duplicated", "persisted_command_evidence", "persisted_command_artifact_stale")
            persisted_paths.add(artifact["path"])
        try:
            command_record = json.loads(
                _evidence_bytes(
                    project_root, persisted["command_record"]["path"], anchor=anchor,
                    gate="persisted_command_evidence",
                    missing_code="persisted_command_artifact_missing",
                ).decode("utf-8")
            )
            terminal_status = json.loads(
                _evidence_bytes(
                    project_root, persisted["terminal_status"]["path"], anchor=anchor,
                    gate="persisted_command_evidence",
                    missing_code="persisted_command_artifact_missing",
                ).decode("utf-8")
            )
            exit_code = int(
                _evidence_bytes(
                    project_root, persisted["exit_code"]["path"], anchor=anchor,
                    gate="persisted_command_evidence",
                    missing_code="persisted_command_artifact_missing",
                ).decode("utf-8").strip()
            )
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


def _validate_mirrors(
    project_root: Path, value: dict[str, Any], *, anchor: str | None = None
) -> None:
    checks = value["mirror_checks"]
    if len(checks) != len(MIRROR_SPECS):
        _fail("Global Gate mirror checks are incomplete", "mirror_checks", "global_gate_mirror_stale")
    for check, (source_relative, mirror_relative) in zip(checks, MIRROR_SPECS, strict=True):
        source_sha = hashlib.sha256(
            _evidence_bytes(
                project_root, source_relative, anchor=anchor,
                gate="mirror_checks", missing_code="mirror_missing",
            )
        ).hexdigest()
        mirror_sha = hashlib.sha256(
            _evidence_bytes(
                project_root, mirror_relative, anchor=anchor,
                gate="mirror_checks", missing_code="mirror_missing",
            )
        ).hexdigest()
        if anchor is None:
            source = _project_file(project_root, source_relative, gate="mirror_checks", missing_code="mirror_missing")
            mirror = _project_file(project_root, mirror_relative, gate="mirror_checks", missing_code="mirror_missing")
            if check != {"source_path": str(source), "mirror_path": str(mirror), "source_sha256": source_sha, "mirror_sha256": mirror_sha, "status": "equal"} or source_sha != mirror_sha:
                _fail("Global Gate mirror is stale or unequal", "mirror_checks", "global_gate_mirror_stale")
        elif (
            check.get("source_sha256") != source_sha
            or check.get("mirror_sha256") != mirror_sha
            or check.get("status") != "equal"
            or source_sha != mirror_sha
        ):
            _fail("Global Gate mirror is stale or unequal", "mirror_checks", "global_gate_mirror_stale")


def _find_publication_commit(
    project_root: Path, relative: str, head_blob: str
) -> str:
    """Locate the commit whose tree holds the identical canonical blob."""
    for candidate in _git(
        project_root, "log", "--format=%H", "HEAD", "--", relative,
        gate="historical_evidence", code="historical_evidence_lineage_invalid",
    ).splitlines():
        try:
            if _git(
                project_root, "rev-parse", f"{candidate}:{relative}",
                gate="historical_evidence", code="historical_evidence_lineage_invalid",
            ) == head_blob:
                return candidate
        except ExitEvidenceValidationError:
            continue
    _fail(
        "canonical manifest publication commit is absent",
        "historical_evidence", "historical_evidence_lineage_invalid",
    )


def _validate_publication(
    project_root: Path,
    value: dict[str, Any],
    manifest_path: Path,
    *,
    require_current_publication: bool,
) -> None:
    relative = manifest_path.relative_to(project_root).as_posix()
    head_blob = _git(project_root, "rev-parse", f"HEAD:{relative}", gate="historical_evidence", code="canonical_manifest_uncommitted")
    worktree_blob = _git(project_root, "hash-object", f"--path={relative}", "--", relative, gate="historical_evidence", code="canonical_manifest_uncommitted")
    if head_blob != worktree_blob:
        _fail("canonical manifest differs from committed HEAD", "historical_evidence", "canonical_manifest_uncommitted")
    publication = _find_publication_commit(project_root, relative, head_blob)
    if require_current_publication:
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
    evidence_paths = set(value["evidence_paths"])
    publication_paths = _commit_paths(project_root, publication)
    if not publication_paths <= evidence_paths:
        _fail("evidence publication contains undeclared paths", "historical_evidence", "historical_evidence_paths_stale")
    # Gate-ordering dependency: the evidence_paths canonical-set gate in
    # _validate_bindings runs first, so every declared path is already the
    # canonical manifest, log, or persisted-artifact path and is safe to use
    # as an exact pathspec here. A republication inherits byte-identical
    # blobs from its parent tree, so the publication diff is only required to
    # stay within the declared set, while every declared path must still
    # resolve as a regular blob (never a symlink or gitlink) in the
    # publication tree.
    published: dict[str, tuple[str, str]] = {}
    for line in _git(
        project_root, "ls-tree", "-r", publication, "--", *sorted(evidence_paths),
        gate="historical_evidence", code="historical_evidence_lineage_invalid",
    ).splitlines():
        mode, kind, _, path = line.split(None, 3)
        published[path] = (mode, kind)
    for relative_path in sorted(evidence_paths):
        entry = published.get(relative_path)
        if entry is not None and entry[0] in {"100644", "100755"} and entry[1] == "blob":
            continue
        if relative_path in publication_paths:
            _fail(
                f"evidence path is not a regular blob in the publication tree: {relative_path}",
                "historical_evidence", "historical_evidence_paths_stale",
            )
        _fail(
            f"evidence path does not resolve to a regular blob in the publication tree: {relative_path}",
            "historical_evidence", "historical_evidence_path_unpublished",
        )
    # Byte binding: the publication-tree blob bytes of every non-manifest
    # evidence path must hash to the manifest-declared fingerprint, closing
    # the dirty-worktree window between on-disk bytes and committed bytes.
    declared_fingerprints = {
        item["log"]["path"]: item["log"]["sha256"] for item in value["commands"]
    }
    for item in value["commands"]:
        for key, artifact in item["persisted_run"].items():
            if key != "run_id":
                declared_fingerprints[artifact["path"]] = artifact["sha256"]
    for relative_path, expected_sha256 in sorted(declared_fingerprints.items()):
        try:
            actual = sha256_git_blob(project_root, publication, relative_path)
        except EvidenceSupportError as exc:
            raise ExitEvidenceValidationError(
                str(exc),
                first_failing_gate="historical_evidence",
                error_code="historical_evidence_lineage_invalid",
            ) from exc
        if actual != expected_sha256:
            _fail(
                f"evidence publication bytes differ from the manifest fingerprint: {relative_path}",
                "historical_evidence", "historical_evidence_paths_stale",
            )
    if not (_commit_paths(project_root, value["implementation_commit"]) - set(value["evidence_paths"])):
        _fail("implementation commit is evidence-only", "historical_evidence", "implementation_commit_evidence_only")


def validate_global_gate_exit_evidence(
    manifest_path: Path,
    *,
    project_root: Path,
    purpose: VerificationPurpose = "activation",
) -> ValidatedExitEvidence:
    """Validate committed Issue 43 evidence for activation, publication, or release audit."""
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
    manifest_generation = purpose in ("candidate_publication", "release_audit")
    anchor: str | None = None
    if purpose == "release_audit":
        relative = path.relative_to(root).as_posix()
        head_blob = _git(
            root, "rev-parse", f"HEAD:{relative}",
            gate="historical_evidence", code="canonical_manifest_uncommitted",
        )
        worktree_blob = _git(
            root, "hash-object", f"--path={relative}", "--", relative,
            gate="historical_evidence", code="canonical_manifest_uncommitted",
        )
        if head_blob != worktree_blob:
            _fail(
                "canonical manifest differs from committed HEAD",
                "historical_evidence", "canonical_manifest_uncommitted",
            )
        anchor = _find_publication_commit(root, relative, head_blob)
    _validate_schema(root, value, use_historical_schema=manifest_generation)
    _validate_closed_policy(root, value, use_historical_contract=manifest_generation)
    _validate_mirrors(root, value, anchor=anchor)
    _validate_implementation(root, value)
    _validate_bindings(root, value, path, anchor=anchor)
    _validate_publication(
        root,
        value,
        path,
        require_current_publication=purpose == "activation",
    )
    return ValidatedExitEvidence(path=path, sha256=sha256_file(path), value=value)

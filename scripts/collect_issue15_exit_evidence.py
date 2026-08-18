from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT, PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from scripts.issue15_exit_evidence_contract import (
    ACTIVATION_SCOPE,
    ATOMIC_MEMBERS,
    ATOMIC_MEMBER_STATUS,
    COMMANDS,
    EXPECTED_CHECKPOINTS,
    EVIDENCE_PREFIX,
    FIXTURE_SPECS,
    MIRROR_SPECS,
    PLATFORM_STATUSES,
    POLICY_STATUS,
    QUALIFICATION_CWD_ROLE,
    QUALIFICATION_INTERPRETER_ROLE,
    RESULT_BINDINGS,
    RESULTS,
    SLICE_BASE_COMMIT,
    SLICE_NAME,
    SLICE_NUMBER,
)
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    implementation_change_tombstones,
)

BATCH_RECORD_SCHEMA = PROJECT_ROOT / "schemas/video-workflow/v5/batch-record.v1.schema.json"
BATCH_ITEM_PROJECTION_SCHEMA = (
    PROJECT_ROOT / "schemas/video-workflow/v5/batch-item-projection.v1.schema.json"
)

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class CollectionError(RuntimeError):
    pass


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise CollectionError(f"{label} must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_resolved(path: Path, *, label: str) -> Path:
    """Resolve a path and require it to stay inside the repository root."""
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise CollectionError(
            f"{label} must be inside the repository"
        ) from exc
    return resolved


def _binding(path: Path, *, label: str) -> dict[str, str]:
    """Record a repo-relative binding for a file inside the repository."""
    resolved = _repo_resolved(path, label=label)
    if not resolved.is_file():
        raise CollectionError(f"{label} is unavailable")
    relative = resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    return {"path": relative, "sha256": _sha256(resolved)}


def _bound_path(binding: Any, *, base: Path, label: str) -> Path:
    """Resolve a binding path that may be relative or absolute-in-repo."""
    if not isinstance(binding, dict):
        raise CollectionError(f"{label} binding is absent")
    raw = binding.get("path")
    expected = binding.get("sha256")
    if not isinstance(raw, str) or not isinstance(expected, str):
        raise CollectionError(f"{label} binding is invalid")
    candidate = Path(raw)
    resolved = (
        _repo_resolved(candidate, label=label)
        if candidate.is_absolute()
        else (base / candidate).resolve()
    )
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise CollectionError(f"{label} binding escapes the repository") from exc
    if not resolved.is_file() or _sha256(resolved) != expected:
        raise CollectionError(f"{label} binding is stale")
    return resolved


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.issue15-new")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _validate_contract_instance(path: Path, *, schema_name: str, label: str) -> dict[str, Any]:
    """Validate a Batch Record or Batch Item Projection through the Kernel registry.

    The Kernel Schema Registry (registered ``batch-record`` and
    ``batch-item-projection`` contracts) owns structural field authority and
    resolves the schemas' cross-file ``$ref``s (for example the shared
    ``common.v1`` definitions), so contract validation must route through
    ``ContractRegistry.validate`` instead of a bare validator.
    """
    instance = _read_json(path, label=label)
    if instance.get("schema_name") != schema_name:
        raise CollectionError(f"{label} identity is invalid")
    try:
        ContractRegistry(PROJECT_ROOT).validate(schema_name, instance)
    except ContractError as exc:
        raise CollectionError(f"{label} is not contract-valid: {exc}") from exc
    return instance


def qualification_manifest_skeleton() -> dict:
    """Return authority-owned Slice 14 fields without collecting runtime evidence."""
    return {
        "slice": {"number": SLICE_NUMBER, "name": SLICE_NAME},
        "slice_base_commit": SLICE_BASE_COMMIT,
        "activation_scope": deepcopy(ACTIVATION_SCOPE),
        "platform_statuses": deepcopy(PLATFORM_STATUSES),
        "atomic_members": list(ATOMIC_MEMBERS),
        "atomic_member_status": deepcopy(ATOMIC_MEMBER_STATUS),
        "expected_checkpoints": deepcopy(EXPECTED_CHECKPOINTS),
        "results": deepcopy(RESULTS),
    }


def _decode_qualification_run(
    qualification_run_dir: Path,
    *,
    command_id: str,
    expected_argv: list[str],
    expected_exit: int,
) -> dict[str, Any]:
    """Validate one persisted qualification run against its closed contract.

    Command identity is semantic, not machine-bound: argv[1:] must equal the
    closed contract's semantic arguments (flags, module, verbosity, test
    targets), and argv[0] must be a Python interpreter path (the interpreter
    role). The recorded cwd is execution-environment evidence, not semantic
    identity, so it is recorded as a cwd_role rather than compared to the
    current project root. Every remaining identity field (run_id, accepted
    exit codes, terminal state, real exit code, eligibility) plus the
    execution-time Git binding (git_commit, worktree_clean) must match. Any
    mismatch fails closed.
    """
    qualification_root = qualification_run_dir.resolve()
    command_path = qualification_root / "command.json"
    status_path = qualification_root / "status.json"
    exit_code_path = qualification_root / "exit-code.txt"
    command = _read_json(command_path, label="persisted command record")
    status = _read_json(status_path, label="persisted terminal status")
    try:
        exit_code = int(exit_code_path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError, ValueError) as exc:
        raise CollectionError("persisted exit code is unavailable or invalid") from exc
    eligible = status.get("security", {}).get("acceptance_evidence_eligible")
    git_commit = command.get("git_commit")
    worktree_clean = command.get("worktree_clean")
    recorded_argv = command.get("argv")
    interpreter_path = recorded_argv[0] if isinstance(recorded_argv, list) and recorded_argv else None
    if (
        command.get("run_id") != status.get("run_id")
        or not isinstance(recorded_argv, list)
        or len(recorded_argv) < 1
        or not isinstance(interpreter_path, str)
        or not Path(interpreter_path).name.lower().startswith("python")
        or recorded_argv[1:] != list(expected_argv)[1:]
        or command.get("accepted_exit_codes") != [expected_exit]
        or status.get("state") != "succeeded"
        or status.get("exit_code") != expected_exit
        or exit_code != expected_exit
        or eligible is not True
        or not isinstance(git_commit, str)
        or COMMIT_RE.fullmatch(git_commit) is None
        or worktree_clean is not True
    ):
        raise CollectionError(
            f"qualification run is not succeeded evidence: {command_id}"
        )
    target_identity = status.get("target_identity")
    executable_sha256 = (
        target_identity.get("observation_sha256")
        if isinstance(target_identity, dict)
        else None
    )
    python_version = command.get("python_version")
    if not isinstance(python_version, str) or not python_version:
        python_version = (
            status.get("python_version")
            if isinstance(status.get("python_version"), str)
            else None
        )
    return {
        "run_id": command["run_id"],
        "state": "succeeded",
        "exit_code": expected_exit,
        "acceptance_evidence_eligible": True,
        "git_commit": git_commit,
        "worktree_clean": True,
        "executable_role": QUALIFICATION_INTERPRETER_ROLE,
        "cwd_role": QUALIFICATION_CWD_ROLE,
        "executable_sha256": executable_sha256,
        "python_version": python_version,
        "command_record": _binding(command_path, label=f"{command_id} command record"),
        "terminal_status": _binding(status_path, label=f"{command_id} terminal status"),
        "exit_code_artifact": _binding(exit_code_path, label=f"{command_id} exit code"),
        "log": _binding(
            qualification_root / "command.log", label=f"{command_id} command log"
        ),
    }


def _collect_projections(
    projections_dir: Path,
    *,
    batch_id: str,
    run_mappings: dict[int, str],
) -> list[dict[str, Any]]:
    """Decode and contract-validate every Batch Item Projection in a directory.

    Each ``*.json`` file in ``projections_dir`` must be a schema-valid Batch
    Item Projection instance. Only guarded-delivered projections (per the
    contract invariant) may report ``guarded_delivered: true``; the collector
    derives each projection summary strictly from the validated instance.
    """
    if not projections_dir.is_dir():
        raise CollectionError("projections directory is unavailable")
    files = sorted(
        path for path in projections_dir.glob("*.json") if path.is_file()
    )
    if not files:
        raise CollectionError("projections directory contains no projection files")
    projections: list[dict[str, Any]] = []
    for path in files:
        projection = _validate_contract_instance(
            path,
            schema_name="batch-item-projection",
            label=f"Batch Item Projection {path.name}",
        )
        item_index = projection.get("item_index")
        run_id = projection.get("run_id")
        delivery_outcome = projection.get("delivery_outcome", {})
        delivery_stage = delivery_outcome.get("delivery_stage")
        guarded_delivered = delivery_outcome.get("guarded_delivered")
        if (
            not isinstance(item_index, int)
            or item_index < 1
            or not isinstance(run_id, str)
            or RUN_ID_RE.fullmatch(run_id) is None
            or not isinstance(delivery_stage, str)
            or not isinstance(guarded_delivered, bool)
        ):
            raise CollectionError(
                f"Batch Item Projection {path.name} is missing required identity"
            )
        if guarded_delivered and (
            delivery_stage != "delivered"
            or not isinstance(delivery_outcome.get("guard_report_sha256"), str)
            or SHA256_RE.fullmatch(delivery_outcome["guard_report_sha256"]) is None
            or not isinstance(projection.get("source_authority", {}).get("run_record_sha256"), str)
        ):
            raise CollectionError(
                f"Batch Item Projection {path.name} self-declares guarded delivery"
            )
        if (
            projection.get("batch_id") != batch_id
            or run_mappings.get(item_index) != run_id
        ):
            raise CollectionError(
                f"Batch Item Projection {path.name} does not belong to the Batch Record"
            )
        projection_sha256 = _sha256(path)
        projections.append(
            {
                "item_index": item_index,
                "run_id": run_id,
                "delivery_stage": delivery_stage,
                "guarded_delivered": guarded_delivered,
                "artifact": {
                    "role": "batch_item_projection",
                    **_binding(path, label=f"Batch Item Projection {path.name}"),
                },
            }
        )
    return projections


def collect(
    *,
    batch_record_path: Path,
    projections_dir: Path,
    negative_evidence_path: Path,
    fairness_group_id: str,
    qualification_runs: dict[str, Path],
    output: Path,
) -> dict[str, Any]:
    """Collect Slice 14 Batch exit evidence (no live video required).

    Verifies the Batch Record against its closed contract, requires at least
    one guarded-delivered projection, and requires all three pinned negative
    evidence flags (duplicate-run, PDF-existence-success, per-video-mutation
    rejection) to be true from the collected test outputs. The two closed
    qualification runs must be succeeded, eligible, and share one execution
    time Git commit.
    """
    batch_record = _validate_contract_instance(
        batch_record_path,
        schema_name="batch-record",
        label="Batch Record",
    )
    if batch_record.get("schema_name") != "batch-record":
        raise CollectionError("Batch Record identity is invalid")
    batch_id = batch_record.get("batch_id")
    if fairness_group_id != batch_id:
        raise CollectionError("fairness group id must equal the Batch Record batch_id")
    run_mappings = {
        item["item_index"]: item["run_id"]
        for item in batch_record.get("run_mappings", [])
        if isinstance(item, dict)
    }
    batch_record_binding = _binding(batch_record_path, label="Batch Record")
    batch_record_contract_sha256 = _sha256(BATCH_RECORD_SCHEMA)
    batch_item_projection_contract_sha256 = _sha256(
        BATCH_ITEM_PROJECTION_SCHEMA
    )

    projections = _collect_projections(
        projections_dir,
        batch_id=batch_id,
        run_mappings=run_mappings,
    )
    batch_guarded_delivered_count = sum(
        1 for projection in projections if projection["guarded_delivered"]
    )
    if batch_guarded_delivered_count < 1:
        raise CollectionError(
            "Batch evidence requires at least one guarded-delivered projection"
        )
    if len({projection["item_index"] for projection in projections}) != len(projections):
        raise CollectionError("Batch projections contain duplicate item indexes")

    negative = _read_json(negative_evidence_path, label="negative evidence")
    for flag in (
        "duplicate_run_rejected",
        "pdf_existence_success_rejected",
        "per_video_mutation_rejected",
        "fairness_group_is_batch_id",
        "auth_breaker_delegated_to_resource_admission",
    ):
        if negative.get(flag) is not True:
            raise CollectionError(f"negative evidence {flag} is not proven true")

    decoded_runs: dict[str, dict[str, Any]] = {}
    for command_id, command_argv, expected_exit in COMMANDS:
        run_dir_for_command = qualification_runs.get(command_id)
        if run_dir_for_command is None:
            raise CollectionError(
                f"qualification run is missing for closed command: {command_id}"
            )
        decoded_runs[command_id] = _decode_qualification_run(
            run_dir_for_command,
            command_id=command_id,
            expected_argv=list(command_argv),
            expected_exit=expected_exit,
        )
    commits = {decoded["git_commit"] for decoded in decoded_runs.values()}
    if len(commits) != 1:
        raise CollectionError(
            "qualification runs do not share one execution-time Git commit"
        )
    implementation_commit = next(iter(commits))

    value = {
        "schema_name": "issue15-exit-evidence-collection",
        "schema_version": "2.0.0",
        "implementation_commit": implementation_commit,
        "batch_evidence": {
            "batch_record_contract_sha256": batch_record_contract_sha256,
            "batch_item_projection_contract_sha256": batch_item_projection_contract_sha256,
            "batch_record": batch_record_binding,
            "projections": projections,
            "batch_guarded_delivered_count": batch_guarded_delivered_count,
            "negative_evidence": {
                "artifact": {
                    "role": "batch_authority_evidence",
                    **_binding(negative_evidence_path, label="negative evidence"),
                },
                "duplicate_run_rejected": negative["duplicate_run_rejected"],
                "pdf_existence_success_rejected": negative["pdf_existence_success_rejected"],
                "per_video_mutation_rejected": negative["per_video_mutation_rejected"],
                "fairness_group_is_batch_id": negative["fairness_group_is_batch_id"],
                "auth_breaker_delegated_to_resource_admission": negative[
                    "auth_breaker_delegated_to_resource_admission"
                ],
            },
            "fairness_group_id": fairness_group_id,
        },
        "qualification_runs": decoded_runs,
    }
    _write_json(output.resolve(), value)
    return value


def _project_binding(binding: dict[str, str], *, role: str) -> dict[str, str]:
    candidate = Path(binding["path"])
    path = (
        _repo_resolved(candidate, label=role)
        if candidate.is_absolute()
        else _repo_resolved(PROJECT_ROOT / candidate, label=role)
    )
    if not path.is_file() or _sha256(path) != binding["sha256"]:
        raise CollectionError(f"collected artifact is stale: {role}")
    relative = path.relative_to(PROJECT_ROOT.resolve()).as_posix()
    return {"role": role, "path": relative, "sha256": binding["sha256"]}


def _git(*arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CollectionError(
            f"git {' '.join(arguments)} failed"
        ) from exc
    return completed.stdout.strip()


def _worktree_changed_paths() -> set[str]:
    changed: set[str] = set()
    for arguments in (
        ("diff", "--name-only", "-z", "--no-renames", "HEAD"),
        ("diff", "--cached", "--name-only", "-z", "--no-renames", "HEAD"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        raw = _git(*arguments)
        changed.update(item for item in raw.split("\0") if item)
    return changed


def _enforce_finalization_anchor(
    implementation_commit: str, *, declared_evidence_paths: set[str]
) -> None:
    """Bind the manifest to the persisted runs' execution-time Git commit.

    Mirrors the Issue 14 pre-publication pattern: current HEAD must equal the
    runs' recorded git_commit, and the worktree may only differ from HEAD at
    declared evidence paths.
    """
    if not COMMIT_RE.fullmatch(implementation_commit):
        raise CollectionError(
            "implementation commit must be a full lowercase Git commit SHA"
        )
    current_head = _git("rev-parse", "HEAD")
    if current_head != implementation_commit:
        raise CollectionError(
            "finalize requires HEAD to equal the qualification runs' "
            f"execution-time commit ({implementation_commit}); current HEAD "
            f"is {current_head}"
        )
    changed = _worktree_changed_paths()
    forbidden = sorted(changed - declared_evidence_paths)
    if forbidden:
        raise CollectionError(
            "finalize requires a clean worktree except declared evidence "
            f"paths; non-evidence changes: {forbidden}"
        )


def _collect_mirror_checks() -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for source_relative, mirror_relative in MIRROR_SPECS:
        source = (PROJECT_ROOT / source_relative).resolve()
        mirror = (PROJECT_ROOT / mirror_relative).resolve()
        source_sha256 = _sha256(source)
        mirror_sha256 = _sha256(mirror)
        checks.append(
            {
                "source_path": source_relative,
                "mirror_path": mirror_relative,
                "source_sha256": source_sha256,
                "mirror_sha256": mirror_sha256,
                "status": "equal" if source_sha256 == mirror_sha256 else "stale",
            }
        )
    return checks


def finalize(*, collection_path: Path, manifest_path: Path) -> dict[str, Any]:
    collection = _read_json(collection_path.resolve(), label="Issue 15 collection")
    if collection.get("schema_name") != "issue15-exit-evidence-collection":
        raise CollectionError("Issue 15 collection identity is invalid")
    batch_evidence = collection.get("batch_evidence")
    qualification_runs = collection.get("qualification_runs")
    if not isinstance(batch_evidence, dict) or not isinstance(qualification_runs, dict):
        raise CollectionError("Issue 15 collection is incomplete")

    if not SLICE_BASE_COMMIT or COMMIT_RE.fullmatch(SLICE_BASE_COMMIT) is None:
        raise CollectionError(
            "slice-14 SLICE_BASE_COMMIT is not pinned; the master agent must "
            "replace the placeholder in scripts/issue15_exit_evidence_contract.py "
            "before finalization"
        )

    collection_binding = _binding(collection_path.resolve(), label="Issue 15 collection")
    batch_record_binding = _project_binding(
        batch_evidence["batch_record"], role="batch_record_evidence"
    )
    projections = [
        {
            "item_index": entry["item_index"],
            "run_id": entry["run_id"],
            "delivery_stage": entry["delivery_stage"],
            "guarded_delivered": entry["guarded_delivered"],
            "artifact": _project_binding(
                entry["artifact"], role="batch_item_projection"
            ),
        }
        for entry in batch_evidence["projections"]
    ]
    guarded = {
        "collection": _project_binding(
            collection_binding, role="batch_evidence_collection"
        ),
        "batch_record_contract_sha256": batch_evidence["batch_record_contract_sha256"],
        "batch_item_projection_contract_sha256": batch_evidence["batch_item_projection_contract_sha256"],
        "batch_record": batch_record_binding,
        "projections": projections,
        "batch_guarded_delivered_count": batch_evidence["batch_guarded_delivered_count"],
        "negative_evidence": {
            **{
                key: value
                for key, value in batch_evidence["negative_evidence"].items()
                if key != "artifact"
            },
            "artifact": _project_binding(
                batch_evidence["negative_evidence"]["artifact"],
                role="batch_authority_evidence",
            ),
        },
        "fairness_group_id": batch_evidence["fairness_group_id"],
    }

    persisted_by_command: dict[str, dict[str, Any]] = {}
    commits: set[str] = set()
    for command_id, _command, _expected_exit in COMMANDS:
        run = qualification_runs.get(command_id)
        if not isinstance(run, dict):
            raise CollectionError(
                f"Issue 15 collection lacks qualification run: {command_id}"
            )
        command_record_path = _project_binding(
            run["command_record"], role="persisted_command_record"
        )
        command_record = _read_json(
            _repo_resolved(
                PROJECT_ROOT / command_record_path["path"],
                label=f"{command_id} command record",
            ),
            label=f"{command_id} persisted command record",
        )
        git_commit = command_record.get("git_commit")
        if not isinstance(git_commit, str) or COMMIT_RE.fullmatch(git_commit) is None:
            raise CollectionError(
                f"qualification run lacks an execution-time Git commit: {command_id}"
            )
        commits.add(git_commit)
        persisted = {
            "run_id": run["run_id"],
            "command_record": command_record_path,
            "terminal_status": _project_binding(
                run["terminal_status"], role="persisted_terminal_status"
            ),
            "exit_code": _project_binding(
                run["exit_code_artifact"], role="persisted_exit_code"
            ),
        }
        for optional_field in ("executable_sha256", "python_version"):
            if isinstance(run.get(optional_field), str) and run[optional_field]:
                persisted[optional_field] = run[optional_field]
        persisted_by_command[command_id] = persisted
    if len(commits) != 1:
        raise CollectionError(
            "qualification runs do not share one execution-time Git commit"
        )
    implementation_commit = next(iter(commits))

    manifest_relative = manifest_path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    log_dir = manifest_path.resolve().parent / "logs"
    commands = []
    for command_id, command, expected_exit in COMMANDS:
        run = qualification_runs[command_id]
        real_exit_code = run["exit_code"]
        log_path = log_dir / f"{command_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Re-verify the collect-time binding of the original persisted log
        # before copying it. The persisted log lives under 待删除/ (gitignored),
        # so the finalization-anchor worktree check cannot detect a drift
        # between collect and finalize; the hash re-check closes that
        # check-then-use gap.
        source_log = _project_binding(
            run["log"],
            role="persisted_command_log",
        )
        real_output = _repo_resolved(
            PROJECT_ROOT / source_log["path"],
            label=f"{command_id} command log",
        ).read_bytes()
        # LF-normalize the source log exactly like the Issue 14 collector so
        # the published log's bytes survive Git's core.autocrlf normalization
        # and its sha256 matches the committed blob in any checkout.
        normalized = real_output.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        marker = (
            f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n"
        ).encode("utf-8")
        published_output = normalized + marker
        log_path.write_bytes(published_output)
        commands.append(
            {
                "test_id": command_id,
                "command": list(command),
                "expected_exit_code": expected_exit,
                "actual_exit_code": real_exit_code,
                "source_log_sha256": hashlib.sha256(normalized).hexdigest(),
                "published_log_sha256": hashlib.sha256(published_output).hexdigest(),
                "log": _project_binding(
                    _binding(log_path, label=f"{command_id} log"),
                    role="command_log",
                ),
                "persisted_run": persisted_by_command[command_id],
                "conforms": real_exit_code == expected_exit,
            }
        )
    evidence_paths = {
        manifest_relative,
        *[command["log"]["path"] for command in commands],
        *[
            binding["path"]
            for persisted in persisted_by_command.values()
            for binding in (
                persisted["command_record"],
                persisted["terminal_status"],
                persisted["exit_code"],
            )
        ],
        batch_record_binding["path"],
        *[entry["artifact"]["path"] for entry in projections],
        guarded["negative_evidence"]["artifact"]["path"],
        collection_binding["path"],
    }
    _enforce_finalization_anchor(implementation_commit, declared_evidence_paths=evidence_paths)

    manifest = {
        "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
        "schema_version": 2,
        "kind": "video-workflow-exit-evidence",
        "fingerprint_algorithm": "sha256-raw-v1",
        **qualification_manifest_skeleton(),
        "implementation_commit": implementation_commit,
        "evidence_paths": sorted(evidence_paths),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "activation_scope": deepcopy(ACTIVATION_SCOPE),
        "platform_statuses": deepcopy(PLATFORM_STATUSES),
        "batch_evidence": guarded,
        "atomic_members": list(ATOMIC_MEMBERS),
        "atomic_member_status": deepcopy(ATOMIC_MEMBER_STATUS),
        "mirror_checks": _collect_mirror_checks(),
        "policy_status": POLICY_STATUS,
        "commands": commands,
        "expected_checkpoints": deepcopy(EXPECTED_CHECKPOINTS),
        "fixtures": [
            {
                "role": role,
                "path": path,
                "sha256": _sha256(PROJECT_ROOT / path),
            }
            for role, path in FIXTURE_SPECS
        ],
        "results": deepcopy(RESULTS),
        "result_bindings": deepcopy(RESULT_BINDINGS),
        "artifact_fingerprints": fingerprint_implementation_changes(
            PROJECT_ROOT,
            SLICE_BASE_COMMIT,
            implementation_commit,
            excluded_prefixes=(EVIDENCE_PREFIX,),
        ),
        "implementation_tombstones": implementation_change_tombstones(
            PROJECT_ROOT,
            SLICE_BASE_COMMIT,
            implementation_commit,
            excluded_prefixes=(EVIDENCE_PREFIX,),
        ),
        "unresolved_exceptions": [],
        "overall_decision": "pass",
    }
    _write_json(manifest_path.resolve(), manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Issue 15 Exit Evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--batch-record", type=Path, required=True)
    collect_parser.add_argument("--projections-dir", type=Path, required=True)
    collect_parser.add_argument("--negative-evidence", type=Path, required=True)
    collect_parser.add_argument("--fairness-group-id", type=str, required=True)
    collect_parser.add_argument(
        "--qualification-run-dir",
        action="append",
        metavar="[command_id=]PATH",
        help=(
            "Persisted qualification run directory. May be repeated as "
            "command_id=PATH for every closed command; a bare PATH binds the "
            "issue15-exit-evidence-tests command for backward compatibility."
        ),
    )
    collect_parser.add_argument("--output", type=Path, required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--collection", type=Path, required=True)
    finalize_parser.add_argument("--manifest", type=Path, required=True)
    return parser


def _parse_qualification_run_dirs(
    values: list[str] | None,
) -> dict[str, Path]:
    """Map repeatable --qualification-run-dir values to closed command ids."""
    known = {command_id for command_id, _argv, _exit in COMMANDS}
    result: dict[str, Path] = {}
    for value in values or []:
        if "=" in value:
            command_id, _, raw_path = value.partition("=")
            if command_id not in known:
                raise CollectionError(f"unknown qualification command: {command_id}")
            if command_id in result:
                raise CollectionError(
                    f"qualification run is repeated for command: {command_id}"
                )
            result[command_id] = Path(raw_path)
        else:
            if COMMANDS[1][0] in result:
                raise CollectionError(
                    f"qualification run is repeated for command: {COMMANDS[1][0]}"
                )
            result[COMMANDS[1][0]] = Path(value)
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "collect":
            result = collect(
                batch_record_path=args.batch_record,
                projections_dir=args.projections_dir,
                negative_evidence_path=args.negative_evidence,
                fairness_group_id=args.fairness_group_id,
                qualification_runs=_parse_qualification_run_dirs(
                    args.qualification_run_dir
                ),
                output=args.output,
            )
        else:
            result = finalize(
                collection_path=args.collection,
                manifest_path=args.manifest,
            )
    except (CollectionError, EvidenceSupportError, KeyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

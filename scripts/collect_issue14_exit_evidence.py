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
for import_root in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from scripts.issue14_exit_evidence_contract import (
    ACTIVATION_SCOPE,
    ATOMIC_MEMBERS,
    ATOMIC_MEMBER_STATUS,
    COMMANDS,
    EXPECTED_CHECKPOINTS,
    EVIDENCE_PREFIX,
    FIXTURE_SPECS,
    PLATFORM_STATUSES,
    RESULT_BINDINGS,
    RESULTS,
    SLICE_BASE_COMMIT,
    SLICE_NAME,
    SLICE_NUMBER,
)
from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
)
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.guarded_delivery import (
    validate_acceptance_report,
    validate_delivery_guard_report,
)


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


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
    temporary = path.with_name(f".{path.name}.issue14-new")
    temporary.write_bytes(payload)
    temporary.replace(path)


def qualification_manifest_skeleton() -> dict:
    """Return authority-owned Slice 13 fields without collecting runtime evidence."""
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

    Every identity field (run_id, argv, cwd, accepted exit codes, terminal
    state, real exit code, eligibility) plus the execution-time Git binding
    (git_commit, worktree_clean) must match. Any mismatch fails closed.
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
    if (
        command.get("run_id") != status.get("run_id")
        or command.get("argv") != expected_argv
        or Path(str(command.get("cwd", ""))).resolve() != PROJECT_ROOT.resolve()
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
    return {
        "run_id": command["run_id"],
        "state": "succeeded",
        "exit_code": expected_exit,
        "acceptance_evidence_eligible": True,
        "git_commit": git_commit,
        "worktree_clean": True,
        "command_record": _binding(command_path, label=f"{command_id} command record"),
        "terminal_status": _binding(status_path, label=f"{command_id} terminal status"),
        "exit_code_artifact": _binding(exit_code_path, label=f"{command_id} exit code"),
        "log": _binding(
            qualification_root / "command.log", label=f"{command_id} command log"
        ),
    }


def collect(
    *,
    run_dir: Path,
    current_target: Path,
    qualification_runs: dict[str, Path],
    output: Path,
) -> dict[str, Any]:
    run_root = run_dir.resolve()
    run_path = run_root / "workflow" / "run.json"
    run_record = _read_json(run_path, label="Run Record")
    if (
        run_record.get("schema_version") != "4.0.0"
        or run_record.get("canonical_platform") != "youtube"
        or run_record.get("delivery", {}).get("stage") != "delivered"
        or Path(str(run_record.get("output_path", ""))).resolve() != run_root
    ):
        raise CollectionError("Run Record is not a delivered YouTube Kernel Run")

    delivery = run_record["delivery"]
    projections = delivery["projections"]
    video_path = _bound_path(
        projections.get("video_target"), base=run_root, label="video delivery target"
    )
    session_path = _bound_path(
        projections.get("session_target"), base=run_root, label="session delivery target"
    )
    if session_path != current_target.resolve():
        raise CollectionError("current target differs from the Run authority")
    task_index_path = _bound_path(
        projections.get("task_index"), base=run_root, label="delivery task index"
    )
    video_target = _read_json(video_path, label="video delivery target")
    session_target = _read_json(session_path, label="session delivery target")
    task_index = _read_json(task_index_path, label="delivery task index")
    run_id = run_record.get("run_id")
    if (
        video_target.get("run_id") != run_id
        or video_target.get("stage") != "delivered"
        or session_target.get("run_id") != run_id
        or session_target.get("stage") != "delivered"
        or len([entry for entry in task_index.get("entries", []) if entry.get("run_id") == run_id]) != 1
    ):
        raise CollectionError("delivery projections do not identify one delivered Run")

    source_generation = run_record.get("artifact_generations", {}).get("source_manifest")
    source_path = _bound_path(source_generation, base=run_root, label="source manifest")
    artifact_bindings = video_target.get("artifacts", {})
    acceptance_path = _bound_path(
        artifact_bindings.get("acceptance_report"),
        base=run_root,
        label="Acceptance Report v2",
    )
    guard_path = _bound_path(
        artifact_bindings.get("delivery_guard_report"),
        base=run_root,
        label="Delivery Guard Report",
    )
    final_pdf_path = _bound_path(
        artifact_bindings.get("final_pdf"), base=run_root, label="final PDF"
    )
    global_gate_path = _bound_path(
        video_target.get("global_gate_authority"),
        base=run_root,
        label="Global Gate authority",
    )

    try:
        validate_acceptance_report(
            project_root=PROJECT_ROOT,
            report_path=acceptance_path,
            run_id=run_id,
        )
    except ContractError as exc:
        raise CollectionError("Acceptance Report v2 is not a passing Kernel decision")
    try:
        validate_delivery_guard_report(report_path=guard_path)
    except ContractError as exc:
        raise CollectionError("Delivery Guard Report is not a passing Run decision")

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
        "schema_name": "issue14-exit-evidence-collection",
        "schema_version": "2.0.0",
        "run_id": run_id,
        "canonical_platform": "youtube",
        "delivery_stage": "delivered",
        "implementation_commit": implementation_commit,
        "artifacts": {
            "run_record": _binding(run_path, label="Run Record"),
            "source_manifest": _binding(source_path, label="source manifest"),
            "acceptance_report_v2": _binding(acceptance_path, label="Acceptance Report v2"),
            "delivery_guard_report": _binding(guard_path, label="Delivery Guard Report"),
            "video_delivery_target": _binding(video_path, label="video delivery target"),
            "session_delivery_target": _binding(session_path, label="session delivery target"),
            "delivery_task_index": _binding(task_index_path, label="delivery task index"),
            "global_gate_authority": _binding(global_gate_path, label="Global Gate authority"),
            "final_pdf": _binding(final_pdf_path, label="final PDF"),
        },
        "qualification_run": {
            key: value
            for key, value in decoded_runs[COMMANDS[1][0]].items()
            if key != "log" and key != "git_commit" and key != "worktree_clean"
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

    Mirrors the legacy_baseline_contracts pre-publication pattern: current
    HEAD must equal the runs' recorded git_commit, and the worktree may only
    differ from HEAD at declared evidence paths.
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


def finalize(*, collection_path: Path, manifest_path: Path) -> dict[str, Any]:
    collection = _read_json(collection_path.resolve(), label="Issue 14 collection")
    if collection.get("schema_name") != "issue14-exit-evidence-collection":
        raise CollectionError("Issue 14 collection identity is invalid")
    artifacts = collection.get("artifacts")
    qualification_runs = collection.get("qualification_runs")
    if not isinstance(artifacts, dict) or not isinstance(qualification_runs, dict):
        raise CollectionError("Issue 14 collection is incomplete")

    guarded_artifacts = [
        _project_binding(artifacts[role], role=role)
        for role in (
            "run_record",
            "source_manifest",
            "acceptance_report_v2",
            "delivery_guard_report",
            "video_delivery_target",
            "session_delivery_target",
            "delivery_task_index",
            "global_gate_authority",
            "final_pdf",
        )
    ]
    persisted_by_command: dict[str, dict[str, Any]] = {}
    commits: set[str] = set()
    for command_id, _command, _expected_exit in COMMANDS:
        run = qualification_runs.get(command_id)
        if not isinstance(run, dict):
            raise CollectionError(
                f"Issue 14 collection lacks qualification run: {command_id}"
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
        persisted_by_command[command_id] = {
            "run_id": run["run_id"],
            "command_record": command_record_path,
            "terminal_status": _project_binding(
                run["terminal_status"], role="persisted_terminal_status"
            ),
            "exit_code": _project_binding(
                run["exit_code_artifact"], role="persisted_exit_code"
            ),
        }
    if len(commits) != 1:
        raise CollectionError(
            "qualification runs do not share one execution-time Git commit"
        )
    implementation_commit = next(iter(commits))

    collection_binding = _binding(collection_path.resolve(), label="Issue 14 collection")
    persisted_primary = persisted_by_command[COMMANDS[1][0]]
    guarded = {
        "collection": _project_binding(
            collection_binding, role="guarded_delivery_collection"
        ),
        "run_id": collection["run_id"],
        "canonical_platform": collection["canonical_platform"],
        "delivery_stage": collection["delivery_stage"],
        "artifacts": guarded_artifacts,
        "qualification_run": persisted_primary,
    }

    manifest_relative = manifest_path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    log_dir = manifest_path.resolve().parent / "logs"
    commands = []
    for command_id, command, expected_exit in COMMANDS:
        run = qualification_runs[command_id]
        real_exit_code = run["exit_code"]
        log_path = log_dir / f"{command_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        real_output = _repo_resolved(
            PROJECT_ROOT / run["log"]["path"], label=f"{command_id} command log"
        ).read_bytes()
        marker = (
            f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n"
        ).encode("utf-8")
        log_path.write_bytes(real_output + marker)
        commands.append(
            {
                "test_id": command_id,
                "command": list(command),
                "expected_exit_code": expected_exit,
                "actual_exit_code": real_exit_code,
                "log": _project_binding(
                    _binding(log_path, label=f"{command_id} log"),
                    role="command_log",
                ),
                "persisted_run": persisted_by_command[command_id],
                "conforms": real_exit_code == expected_exit,
            }
        )
    guarded_bindings = [
        guarded["collection"],
        *guarded["artifacts"],
        *[
            binding
            for persisted in persisted_by_command.values()
            for binding in (
                persisted["command_record"],
                persisted["terminal_status"],
                persisted["exit_code"],
            )
        ],
    ]
    evidence_paths = {
        manifest_relative,
        *[command["log"]["path"] for command in commands],
        *[binding["path"] for binding in guarded_bindings],
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
        "guarded_delivery_evidence": guarded,
        "commands": commands,
        "fixtures": [
            {
                "role": role,
                "path": path,
                "sha256": _sha256(PROJECT_ROOT / path),
            }
            for role, path in FIXTURE_SPECS
        ],
        "result_bindings": deepcopy(RESULT_BINDINGS),
        "artifact_fingerprints": fingerprint_implementation_changes(
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
    parser = argparse.ArgumentParser(description="Collect Issue 14 Exit Evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--run-dir", type=Path, required=True)
    collect_parser.add_argument("--current-target", type=Path, required=True)
    collect_parser.add_argument(
        "--qualification-run-dir",
        action="append",
        metavar="[command_id=]PATH",
        help=(
            "Persisted qualification run directory. May be repeated as "
            "command_id=PATH for every closed command; a bare PATH binds the "
            "issue14-exit-evidence-tests command for backward compatibility."
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
                run_dir=args.run_dir,
                current_target=args.current_target,
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

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from issue43_exit_evidence_contract import (
    ACTIVATION_SCOPE,
    ATOMIC_MEMBERS,
    ATOMIC_MEMBER_STATUS,
    COMMANDS,
    EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS,
    MIRROR_SPECS,
    POLICY_STATUS,
    RESULT_BINDINGS,
    RESULTS,
    SLICE_BASE_COMMIT,
    SLICE_NAME,
    SLICE_NUMBER,
)
from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    implementation_change_tombstones,
    git_output,
    sha256_file,
    sha256_git_blob,
)


EVIDENCE_DIR = PROJECT_ROOT / "evidence/global-gate"
LOG_DIR = EVIDENCE_DIR / "logs"
MANIFEST_PATH = EVIDENCE_DIR / "exit-evidence-manifest.json"
PERSISTED_COMMAND = PROJECT_ROOT / "scripts/persisted_command.py"
REFRESH_ROOT = PROJECT_ROOT / "待删除/exit-evidence-refresh/global-gate"


def git(*arguments: str) -> str:
    try:
        return git_output(PROJECT_ROOT, *arguments)
    except EvidenceSupportError as exc:
        raise RuntimeError(str(exc)) from exc


def relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def preserve_previous_evidence() -> None:
    existing = [
        path
        for path in [
            MANIFEST_PATH,
            *LOG_DIR.glob("*.log"),
            EVIDENCE_DIR / "persisted",
        ]
        if path.exists()
    ]
    if not existing:
        return
    destination = REFRESH_ROOT / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    destination.mkdir(parents=True, exist_ok=False)
    for path in existing:
        os.replace(path, destination / relative(path).replace("/", "__"))


def _write_collection(path: Path, collection: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(collection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _start_command(
    command_id: str, command: Sequence[str], expected_exit_code: int
) -> dict[str, str]:
    completed = subprocess.run(
        [
            sys.executable, "-X", "utf8", "-B", str(PERSISTED_COMMAND),
            "start", "--task-name", f"issue43-exit-evidence-{command_id}",
            "--cwd", str(PROJECT_ROOT), "--accepted-exit-code",
            str(expected_exit_code), "--", *command,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"persisted command start failed for {command_id}: "
            f"{completed.stderr or completed.stdout}"
        )
    payload = json.loads(completed.stdout)
    return {
        "command_id": command_id,
        "run_id": payload["data"]["run_id"],
        "run_dir": payload["data"]["run_dir"],
    }


def _observe_run(run: dict[str, str]) -> dict[str, Any]:
    run_dir = Path(run["run_dir"]).resolve()
    try:
        run_dir.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeError(f"persisted run escapes project root: {run_dir}") from exc
    completed = subprocess.run(
        [
            sys.executable, "-X", "utf8", "-B", str(PERSISTED_COMMAND),
            "show", "--run-dir", str(run_dir),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"persisted run observation failed for {run['command_id']}: "
            f"{completed.stderr or completed.stdout}"
        )
    status = json.loads(completed.stdout)["data"]["status"]
    if status.get("run_id") != run["run_id"]:
        raise RuntimeError(f"persisted run identity mismatch: {run['command_id']}")
    return status


def advance_collection(
    implementation_commit: str, collection_path: Path | None = None
) -> dict[str, Any]:
    if collection_path is None:
        collection_dir = REFRESH_ROOT / datetime.now(timezone.utc).strftime(
            "%Y%m%d_%H%M%S_%f"
        )
        collection_dir.mkdir(parents=True, exist_ok=False)
        collection_path = collection_dir / "collection.json"
        collection: dict[str, Any] = {
            "schema_name": "issue43-exit-evidence-collection",
            "schema_version": "1.0.0",
            "implementation_commit": implementation_commit,
            "runs": [],
        }
        _write_collection(collection_path, collection)
    else:
        collection_path = collection_path.resolve()
        collection = _load_json(collection_path)
        if collection.get("implementation_commit") != implementation_commit:
            raise RuntimeError("collection implementation commit differs from current HEAD")

    command_ids = [command_id for command_id, _, _ in COMMANDS]
    started_ids = [run["command_id"] for run in collection.get("runs", [])]
    if started_ids != command_ids[: len(started_ids)]:
        raise RuntimeError("collection runs are not an ordered prefix of the command contract")

    if collection["runs"]:
        active_run = collection["runs"][-1]
        status = _observe_run(active_run)
        state = status.get("state")
        if state == "running":
            return {
                "collection_path": str(collection_path),
                "orchestration_state": "running",
                "active_run": active_run,
                **collection,
            }
        if state != "succeeded":
            return {
                "collection_path": str(collection_path),
                "orchestration_state": "blocked",
                "active_run": active_run,
                "persisted_state": state,
                **collection,
            }
        command_id, _, expected_exit_code = COMMANDS[len(collection["runs"]) - 1]
        exit_path = Path(active_run["run_dir"]) / "exit-code.txt"
        if (
            not exit_path.is_file()
            or int(exit_path.read_text(encoding="utf-8").strip()) != expected_exit_code
            or status.get("security", {}).get("acceptance_evidence_eligible") is not True
        ):
            return {
                "collection_path": str(collection_path),
                "orchestration_state": "blocked",
                "active_run": active_run,
                "persisted_state": state,
                **collection,
            }

    if len(collection["runs"]) == len(COMMANDS):
        return {
            "collection_path": str(collection_path),
            "orchestration_state": "ready_to_finalize",
            **collection,
        }

    command_id, command, expected_exit_code = COMMANDS[len(collection["runs"])]
    run = _start_command(command_id, command, expected_exit_code)
    collection["runs"].append(run)
    _write_collection(collection_path, collection)
    return {
        "collection_path": str(collection_path),
        "orchestration_state": "running",
        "active_run": run,
        **collection,
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object: {path}")
    return value


def require_collection_terminal(collection: dict[str, Any]) -> None:
    runs = {item["command_id"]: item for item in collection.get("runs", [])}
    if set(runs) != {command_id for command_id, _, _ in COMMANDS}:
        raise RuntimeError("collection command set differs from the Issue #43 command contract")
    for command_id, command, expected_exit_code in COMMANDS:
        run = runs[command_id]
        run_dir = Path(run["run_dir"]).resolve()
        try:
            run_dir.relative_to(PROJECT_ROOT.resolve())
        except ValueError as exc:
            raise RuntimeError(f"persisted run escapes project root: {run_dir}") from exc
        command_path = run_dir / "command.json"
        status_path = run_dir / "status.json"
        exit_path = run_dir / "exit-code.txt"
        if not command_path.is_file() or not status_path.is_file() or not exit_path.is_file():
            raise RuntimeError(f"persisted run is not terminal or incomplete: {run_dir}")
        command_record = _load_json(command_path)
        status = _load_json(status_path)
        exit_code = int(exit_path.read_text(encoding="utf-8").strip())
        if command_record.get("run_id") != run["run_id"] or status.get("run_id") != run["run_id"]:
            raise RuntimeError(f"persisted run identity mismatch: {command_id}")
        if (
            command_record.get("argv") != list(command)
            or command_record.get("accepted_exit_codes") != [expected_exit_code]
            or command_record.get("cwd") != str(PROJECT_ROOT.resolve())
        ):
            raise RuntimeError(f"persisted command contract mismatch: {command_id}")
        if status.get("state") not in {"succeeded", "failed"} or status.get("exit_code") != exit_code:
            raise RuntimeError(f"persisted run has no coherent terminal state: {command_id}")
        if status.get("security", {}).get("acceptance_evidence_eligible") is not True:
            raise RuntimeError(f"persisted run is ineligible for acceptance evidence: {command_id}")


def finalize_commands(
    collection: dict[str, Any], implementation_commit: str
) -> list[dict[str, Any]]:
    expected_runs = {item["command_id"]: item for item in collection.get("runs", [])}
    if set(expected_runs) != {command_id for command_id, _, _ in COMMANDS}:
        raise RuntimeError("collection command set differs from the Issue #43 command contract")
    command_evidence: list[dict[str, Any]] = []
    for command_id, command, expected_exit_code in COMMANDS:
        run = expected_runs[command_id]
        run_dir = Path(run["run_dir"]).resolve()
        try:
            run_dir.relative_to(PROJECT_ROOT.resolve())
        except ValueError as exc:
            raise RuntimeError(f"persisted run escapes project root: {run_dir}") from exc
        source_paths = {
            "command_record": run_dir / "command.json",
            "terminal_status": run_dir / "status.json",
            "exit_code": run_dir / "exit-code.txt",
        }
        if any(not path.is_file() for path in source_paths.values()):
            raise RuntimeError(f"persisted run is not terminal or incomplete: {run_dir}")
        command_record = _load_json(source_paths["command_record"])
        terminal_status = _load_json(source_paths["terminal_status"])
        exit_code = int(source_paths["exit_code"].read_text(encoding="utf-8").strip())
        if command_record.get("run_id") != run["run_id"] or terminal_status.get("run_id") != run["run_id"]:
            raise RuntimeError(f"persisted run identity mismatch: {command_id}")
        if command_record.get("argv") != list(command) or command_record.get("cwd") != str(PROJECT_ROOT.resolve()):
            raise RuntimeError(f"persisted command argv mismatch: {command_id}")
        if command_record.get("accepted_exit_codes") != [expected_exit_code]:
            raise RuntimeError(f"persisted accepted exit-code contract mismatch: {command_id}")
        if terminal_status.get("state") not in {"succeeded", "failed"}:
            raise RuntimeError(f"persisted run has no terminal state: {command_id}")
        if terminal_status.get("exit_code") != exit_code:
            raise RuntimeError(f"persisted terminal exit code mismatch: {command_id}")
        if terminal_status.get("security", {}).get("acceptance_evidence_eligible") is not True:
            raise RuntimeError(f"persisted run is ineligible for acceptance evidence: {command_id}")

        persisted_dir = EVIDENCE_DIR / "persisted" / command_id
        persisted_dir.mkdir(parents=True, exist_ok=True)
        persisted_artifacts: dict[str, dict[str, str]] = {}
        for role, source_path in source_paths.items():
            destination = persisted_dir / source_path.name
            shutil.copyfile(source_path, destination)
            persisted_artifacts[role] = {
                "role": f"persisted_{role}",
                "path": relative(destination),
                "sha256": sha256_file(destination),
            }

        raw = (run_dir / "stdout.log").read_bytes() + (run_dir / "stderr.log").read_bytes()
        raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        raw += f"\nEVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n".encode("ascii")
        log_path = LOG_DIR / f"{command_id}.log"
        log_path.write_bytes(raw)
        command_evidence.append({
            "test_id": command_id,
            "command": list(command),
            "expected_exit_code": expected_exit_code,
            "actual_exit_code": exit_code,
            "log": {"role": "command_log", "path": relative(log_path), "sha256": sha256_file(log_path)},
            "persisted_run": {"run_id": run["run_id"], **persisted_artifacts},
            "conforms": terminal_status["state"] == "succeeded" and exit_code == expected_exit_code,
        })
    return command_evidence


def collect_mirror_checks() -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for source_relative, mirror_relative in MIRROR_SPECS:
        source = (PROJECT_ROOT / source_relative).resolve()
        mirror = (PROJECT_ROOT / mirror_relative).resolve()
        source_sha256 = sha256_file(source)
        mirror_sha256 = sha256_file(mirror)
        checks.append(
            {
                "source_path": str(source),
                "mirror_path": str(mirror),
                "source_sha256": source_sha256,
                "mirror_sha256": mirror_sha256,
                "status": "equal" if source_sha256 == mirror_sha256 else "stale",
            }
        )
    return checks


def collect(collection_path: Path | None = None) -> int:
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        print(
            "ERROR: Issue #43 Exit Evidence collection requires a clean implementation HEAD",
            file=sys.stderr,
        )
        return 2
    implementation_commit = git("rev-parse", "HEAD")
    git("merge-base", "--is-ancestor", SLICE_BASE_COMMIT, implementation_commit)
    collection = advance_collection(implementation_commit, collection_path)
    print(json.dumps(collection, ensure_ascii=False, indent=2))
    return 1 if collection["orchestration_state"] == "blocked" else 0


def finalize(collection_path: Path) -> int:
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        print(
            "ERROR: Issue #43 Exit Evidence finalization requires a clean implementation HEAD",
            file=sys.stderr,
        )
        return 2
    implementation_commit = git("rev-parse", "HEAD")
    collection = _load_json(collection_path)
    if collection.get("implementation_commit") != implementation_commit:
        raise RuntimeError("collection implementation commit differs from current HEAD")
    git("merge-base", "--is-ancestor", SLICE_BASE_COMMIT, implementation_commit)
    # The publication boundary is crossed only after every parent run has
    # auditable terminal state. This keeps the previous formal evidence intact
    # while a run is still active, interrupted, unknown, or incomplete.
    require_collection_terminal(collection)
    preserve_previous_evidence()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    command_evidence = finalize_commands(collection, implementation_commit)
    fixtures = [
        {
            "role": role,
            "path": path,
            # Validator gate served: fixture_fingerprint
            # (global_gate_exit_evidence, error code fixture_sha256_stale)
            # binds fixture identity to the GIT BLOB bytes at
            # implementation_commit via evidence.sha256_git_blob. Mirror that
            # exact algorithm here: finalize guarantees HEAD ==
            # implementation_commit with a clean tree, so the blob bytes are
            # the canonical content and on-disk drift (e.g. CRLF checkout)
            # cannot stale the fingerprint.
            "sha256": sha256_git_blob(PROJECT_ROOT, implementation_commit, path),
        }
        for role, path in FIXTURE_SPECS
    ]
    decision = "pass" if all(item["conforms"] for item in command_evidence) else "fail"
    manifest = {
        "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
        "schema_version": 2,
        "kind": "video-workflow-exit-evidence",
        "fingerprint_algorithm": "sha256-raw-v1",
        "slice": {"number": SLICE_NUMBER, "name": SLICE_NAME},
        "slice_base_commit": SLICE_BASE_COMMIT,
        "implementation_commit": implementation_commit,
        "evidence_paths": [
            relative(MANIFEST_PATH),
            *[item["log"]["path"] for item in command_evidence],
            *[
                artifact["path"]
                for item in command_evidence
                for key, artifact in item["persisted_run"].items()
                if key != "run_id"
            ],
        ],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "activation_scope": ACTIVATION_SCOPE,
        "atomic_members": list(ATOMIC_MEMBERS),
        "atomic_member_status": ATOMIC_MEMBER_STATUS,
        "mirror_checks": collect_mirror_checks(),
        "policy_status": POLICY_STATUS,
        "commands": command_evidence,
        "expected_checkpoints": EXPECTED_CHECKPOINTS,
        "fixtures": fixtures,
        "results": RESULTS,
        "result_bindings": RESULT_BINDINGS,
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
        "overall_decision": decision,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(MANIFEST_PATH)
    return 0 if decision == "pass" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect or finalize Issue #43 Exit Evidence"
    )
    subparsers = parser.add_subparsers(dest="phase", required=True)
    collect_parser = subparsers.add_parser(
        "collect",
        help="start or resume the sequential persisted qualification state machine",
    )
    collect_parser.add_argument("--collection", type=Path)
    finalize_parser = subparsers.add_parser(
        "finalize",
        help="bind terminal persisted evidence and publish the manifest",
    )
    finalize_parser.add_argument("--collection", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.phase == "collect":
        return collect(args.collection)
    return finalize(args.collection)


if __name__ == "__main__":
    raise SystemExit(main())

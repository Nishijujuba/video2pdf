from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import subprocess

from scripts import issue43_exit_evidence_contract as contract
from video2pdf_workflow_kernel.evidence import (
    fingerprint_implementation_changes,
    sha256_git_blob,
)
from video2pdf_workflow_kernel.global_gate_exit_evidence import (
    EVIDENCE_PREFIX,
    SLICE_BASE_COMMIT,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUTHORITY_ROOT = PROJECT_ROOT / "待删除/kernel-test-runs/issue43-authority"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, text=True, encoding="utf-8",
        capture_output=True, check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)
    return completed.stdout.strip()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def build_current_global_gate_authority(root: Path) -> tuple[Path, Path]:
    """Create a real two-commit Issue 43 authority without mutating the source repo."""
    authority_id = hashlib.sha256(
        str(root.resolve()).casefold().encode("utf-8")
    ).hexdigest()[:24]
    repository = AUTHORITY_ROOT / authority_id
    origin_path = AUTHORITY_ROOT / f"{authority_id}.origin.json"
    manifest = repository / "evidence/global-gate/exit-evidence-manifest.json"
    if manifest.is_file():
        origin = json.loads(origin_path.read_text(encoding="utf-8"))
        if origin.get("control_store_root") != str(root.resolve()):
            raise AssertionError(f"Issue 43 authority id collision: {authority_id}")
        return repository, manifest
    repository.mkdir(parents=True, exist_ok=False)
    _write_json(
        origin_path,
        {"authority_id": authority_id, "control_store_root": str(root.resolve())},
    )
    _git(repository, "init")
    alternates = repository / ".git/objects/info/alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_bytes(
        str((PROJECT_ROOT / ".git/objects").resolve()).encode("utf-8") + b"\n"
    )
    _git(repository, "config", "core.longpaths", "true")
    _git(repository, "sparse-checkout", "init", "--cone")
    _git(
        repository, "sparse-checkout", "set",
        "schemas", ".agents", ".claude", "src", "scripts", "tests", "evidence/global-gate",
    )
    source_head = _git(PROJECT_ROOT, "rev-parse", "HEAD")
    changed = set(filter(None, _git(PROJECT_ROOT, "diff-tree", "--no-commit-id", "--name-only", "-r", source_head).splitlines()))
    implementation = (
        _git(PROJECT_ROOT, "rev-parse", f"{source_head}^")
        if changed and all(path.startswith(EVIDENCE_PREFIX) for path in changed)
        else source_head
    )
    _git(repository, "checkout", "--detach", implementation)
    _git(repository, "config", "user.name", "Issue43 Test Authority")
    _git(repository, "config", "user.email", "issue43-authority@example.invalid")

    commands = [
        {
            "test_id": command_id,
            "command": list(command),
            "expected_exit_code": expected_exit_code,
            "actual_exit_code": expected_exit_code,
            "log": {
                "role": "command_log",
                "path": f"evidence/global-gate/logs/{command_id}.log",
                "sha256": "",
            },
            "conforms": True,
        }
        for command_id, command, expected_exit_code in contract.COMMANDS
    ]
    for command in commands:
        log_path = repository / command["log"]["path"]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation}\n"
            f"qualified command: {command['test_id']}\n",
            encoding="utf-8",
        )
        command["log"]["sha256"] = hashlib.sha256(log_path.read_bytes()).hexdigest()

    artifact_fingerprints = fingerprint_implementation_changes(
        repository,
        SLICE_BASE_COMMIT,
        implementation,
        excluded_prefixes=(EVIDENCE_PREFIX,),
    )
    mirror_checks = []
    for source_relative, mirror_relative in contract.MIRROR_SPECS:
        source = repository / source_relative
        mirror = repository / mirror_relative
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        mirror_sha = hashlib.sha256(mirror.read_bytes()).hexdigest()
        mirror_checks.append({
            "source_path": str(source.resolve()),
            "mirror_path": str(mirror.resolve()),
            "source_sha256": source_sha,
            "mirror_sha256": mirror_sha,
            "status": "equal" if source_sha == mirror_sha else "stale",
        })
    fixtures = [
        {
            "role": role,
            "path": path,
            "sha256": sha256_git_blob(repository, implementation, path),
        }
        for role, path in contract.FIXTURE_SPECS
    ]
    template = {
        "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
        "schema_version": 2,
        "kind": "video-workflow-exit-evidence",
        "fingerprint_algorithm": "sha256-raw-v1",
        "slice": {"number": contract.SLICE_NUMBER, "name": contract.SLICE_NAME},
        "slice_base_commit": contract.SLICE_BASE_COMMIT,
        "implementation_commit": implementation,
        "evidence_paths": [
            "evidence/global-gate/exit-evidence-manifest.json",
            *[item["log"]["path"] for item in commands],
        ],
        "generated_at": "2026-08-03T00:00:00Z",
        "activation_scope": deepcopy(contract.ACTIVATION_SCOPE),
        "atomic_members": list(contract.ATOMIC_MEMBERS),
        "atomic_member_status": deepcopy(contract.ATOMIC_MEMBER_STATUS),
        "mirror_checks": mirror_checks,
        "policy_status": contract.POLICY_STATUS,
        "commands": commands,
        "expected_checkpoints": deepcopy(contract.EXPECTED_CHECKPOINTS),
        "fixtures": fixtures,
        "results": deepcopy(contract.RESULTS),
        "result_bindings": deepcopy(contract.RESULT_BINDINGS),
        "artifact_fingerprints": artifact_fingerprints,
        "unresolved_exceptions": [],
        "overall_decision": "pass",
    }
    _write_json(manifest, template)
    _git(repository, "add", "evidence/global-gate")
    _git(repository, "commit", "-m", "Publish test Global Gate evidence")
    return repository, manifest


def commit_later_implementation_change(repository: Path) -> str:
    path = repository / "src/later_implementation_change.py"
    path.write_text("CURRENT_AUTHORITY = True\n", encoding="utf-8")
    _git(repository, "add", "src/later_implementation_change.py")
    _git(repository, "commit", "-m", "Add later implementation change")
    return _git(repository, "rev-parse", "HEAD")

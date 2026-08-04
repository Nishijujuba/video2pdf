from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import threading
import uuid

from scripts import issue43_exit_evidence_contract as contract
from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    sha256_git_blob,
)
from video2pdf_workflow_kernel.global_gate_exit_evidence import (
    EVIDENCE_PREFIX,
    SLICE_BASE_COMMIT,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AUTHORITY_SESSION_ID = uuid.uuid4().hex
_AUTHORITY_BUILD_LOCK = threading.RLock()
_CURRENT_AUTHORITIES: dict[str, tuple[Path, Path]] = {}
_AUTHORITY_GENERATIONS: dict[str, int] = {}
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
    with _AUTHORITY_BUILD_LOCK:
        return _build_current_global_gate_authority(root)


def _build_current_global_gate_authority(root: Path) -> tuple[Path, Path]:
    """Materialize one process-isolated authority and reuse it within its test case."""
    root_identity = str(root.resolve()).casefold()
    cached = _CURRENT_AUTHORITIES.get(root_identity)
    if cached is not None and _authority_is_reusable(*cached):
        return cached
    _CURRENT_AUTHORITIES.pop(root_identity, None)

    authority_base = hashlib.sha256(
        (
            root_identity
            + "\0"
            + contract.QUALIFICATION_CONTRACT_SHA256
            + "\0fixture-graph-v4\0"
            + _AUTHORITY_SESSION_ID
        ).encode("utf-8")
    ).hexdigest()[:24]
    generation = _AUTHORITY_GENERATIONS.get(root_identity, 0)
    authority_id = f"{authority_base}-{generation:02d}"
    while (AUTHORITY_ROOT / authority_id).exists():
        generation += 1
        authority_id = f"{authority_base}-{generation:02d}"
    _AUTHORITY_GENERATIONS[root_identity] = generation + 1
    repository = AUTHORITY_ROOT / authority_id
    origin_path = AUTHORITY_ROOT / f"{authority_id}.origin.json"
    manifest = repository / "evidence/global-gate/exit-evidence-manifest.json"
    repository.mkdir(parents=True, exist_ok=False)
    _write_json(
        origin_path,
        {
            "authority_id": authority_id,
            "authority_session_id": _AUTHORITY_SESSION_ID,
            "control_store_root": str(root.resolve()),
        },
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
        "schemas", "delivery-quality", ".agents", ".claude", "src", "scripts", "tests", "evidence/global-gate",
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

    # Fixture dependency graph:
    # source authority -> evidence-free implementation boundary -> fully
    # rematerialized evidence closure -> publication commit -> manifest paths.
    # Preserve source evidence under the disposable fixture's 待删除 boundary,
    # then remove it from the implementation tree. This makes every governed
    # member part of the publication commit, including byte-identical files.
    source_evidence = repository / EVIDENCE_PREFIX
    if source_evidence.exists():
        preserved_evidence = repository / "待删除/source-global-gate-evidence"
        preserved_evidence.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_evidence), str(preserved_evidence))
        _git(repository, "add", "-A", EVIDENCE_PREFIX)

    authority_sources = (
        "schemas/exit-evidence-manifest.v2.schema.json",
        "schemas/delivery-quality/registry.v1.json",
        "schemas/delivery-quality/v1/acceptance-v2-input-binding.v1.schema.json",
        "schemas/delivery-quality/v1/acceptance-report-v2.v1.schema.json",
        "delivery-quality/v1/acceptance-v2-input-binding.example.v1.json",
        "delivery-quality/v1/acceptance-report-v2.example.v1.json",
        "scripts/issue43_exit_evidence_contract.py",
        "src/video2pdf_workflow_kernel/global_gate.py",
        "src/video2pdf_workflow_kernel/global_gate_exit_evidence.py",
        "tests/video_workflow/_issue43_git_authority.py",
        "tests/video_workflow/test_issue43_activation_fencing.py",
        "tests/video_workflow/test_issue43_spec_gap_contracts.py",
    )
    for relative in authority_sources:
        source = PROJECT_ROOT / relative
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _git(repository, "add", *authority_sources)
    if _git(repository, "status", "--porcelain=v1"):
        _git(repository, "commit", "-m", "Materialize test implementation authority")
        implementation = _git(repository, "rev-parse", "HEAD")

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
            "persisted_run": {
                "run_id": f"00000000-0000-4000-8000-{index:012d}",
                "command_record": {
                    "role": "persisted_command_record",
                    "path": f"evidence/global-gate/persisted/{command_id}/command.json",
                    "sha256": "",
                },
                "terminal_status": {
                    "role": "persisted_terminal_status",
                    "path": f"evidence/global-gate/persisted/{command_id}/status.json",
                    "sha256": "",
                },
                "exit_code": {
                    "role": "persisted_exit_code",
                    "path": f"evidence/global-gate/persisted/{command_id}/exit-code.txt",
                    "sha256": "",
                },
            },
            "conforms": True,
        }
        for index, (command_id, command, expected_exit_code) in enumerate(
            contract.COMMANDS, 1
        )
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
        run_id = command["persisted_run"]["run_id"]
        persisted_values = {
            "command_record": {
                "schema_name": "persisted-command",
                "schema_version": "1.0.0",
                "run_id": run_id,
                "cwd": str(repository.resolve()),
                "argv": command["command"],
                "accepted_exit_codes": [command["expected_exit_code"]],
            },
            "terminal_status": {
                "schema_name": "persisted-command-status",
                "schema_version": "1.0.0",
                "run_id": run_id,
                "state": "succeeded",
                "exit_code": command["actual_exit_code"],
                "security": {
                    "acceptance_evidence_eligible": True,
                    "classification": "no_secret_detected",
                },
            },
        }
        for key, value in persisted_values.items():
            artifact = command["persisted_run"][key]
            artifact_path = repository / artifact["path"]
            _write_json(artifact_path, value)
            artifact["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        exit_artifact = command["persisted_run"]["exit_code"]
        exit_path = repository / exit_artifact["path"]
        exit_path.write_text(f"{command['actual_exit_code']}\n", encoding="utf-8")
        exit_artifact["sha256"] = hashlib.sha256(exit_path.read_bytes()).hexdigest()

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
            *[
                artifact["path"]
                for item in commands
                for key, artifact in item["persisted_run"].items()
                if key != "run_id"
            ],
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
    _git(repository, "add", "-f", "evidence/global-gate")
    _git(repository, "commit", "-m", "Publish test Global Gate evidence")
    _CURRENT_AUTHORITIES[root_identity] = (repository, manifest)
    return repository, manifest


def _authority_is_reusable(repository: Path, manifest: Path) -> bool:
    """Accept a cache hit only while its complete publication graph is intact."""
    if not manifest.is_file():
        return False
    try:
        # The governed cache closure is every tracked file. Preserved source
        # evidence under 待删除 is intentionally ignored/untracked fixture
        # history and is outside this authority boundary.
        if _git(repository, "status", "--porcelain=v1", "--untracked-files=no"):
            return False
        value = json.loads(manifest.read_text(encoding="utf-8"))
        publication_paths = set(
            filter(
                None,
                _git(
                    repository,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ).splitlines(),
            )
        )
        if publication_paths != set(value["evidence_paths"]):
            return False
        repository_root = repository.resolve()
        for relative in value["evidence_paths"]:
            artifact = (repository / relative).resolve()
            artifact.relative_to(repository_root)
            if not artifact.is_file():
                return False
            head_blob = _git(repository, "rev-parse", f"HEAD:{relative}")
            worktree_blob = _git(
                repository,
                "hash-object",
                f"--path={relative}",
                "--",
                relative,
            )
            if worktree_blob != head_blob:
                return False
        parents = _git(repository, "rev-list", "--parents", "-n", "1", "HEAD").split()
        implementation = value["implementation_commit"]
        if len(parents) != 2 or parents[1] != implementation:
            return False
        expected_fingerprints = fingerprint_implementation_changes(
            repository,
            SLICE_BASE_COMMIT,
            implementation,
            excluded_prefixes=(EVIDENCE_PREFIX,),
        )
        return value["artifact_fingerprints"] == expected_fingerprints
    except (
        AssertionError,
        EvidenceSupportError,
        KeyError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ):
        return False


def commit_later_implementation_change(repository: Path) -> str:
    path = repository / "src/later_implementation_change.py"
    path.write_text("CURRENT_AUTHORITY = True\n", encoding="utf-8")
    _git(repository, "add", "src/later_implementation_change.py")
    _git(repository, "commit", "-m", "Add later implementation change")
    return _git(repository, "rev-parse", "HEAD")

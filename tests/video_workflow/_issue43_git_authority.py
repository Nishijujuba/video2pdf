from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from video2pdf_workflow_kernel.evidence import fingerprint_implementation_changes
from video2pdf_workflow_kernel.global_gate_exit_evidence import (
    EVIDENCE_PREFIX,
    SLICE_BASE_COMMIT,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_MANIFEST = PROJECT_ROOT / "evidence/global-gate/exit-evidence-manifest.json"


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
    repository = root / "git-authority"
    repository.mkdir(parents=True)
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
    template = json.loads(CANONICAL_MANIFEST.read_text(encoding="utf-8"))
    implementation = template["implementation_commit"]
    _git(repository, "checkout", "--detach", implementation)
    _git(repository, "config", "user.name", "Issue43 Test Authority")
    _git(repository, "config", "user.email", "issue43-authority@example.invalid")

    for command in template["commands"]:
        log_path = repository / command["log"]["path"]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation}\n"
            f"qualified command: {command['test_id']}\n",
            encoding="utf-8",
        )
        command["log"]["sha256"] = hashlib.sha256(log_path.read_bytes()).hexdigest()

    template["artifact_fingerprints"] = fingerprint_implementation_changes(
        repository,
        SLICE_BASE_COMMIT,
        implementation,
        excluded_prefixes=(EVIDENCE_PREFIX,),
    )
    for check in template["mirror_checks"]:
        source = repository / Path(check["source_path"]).relative_to(PROJECT_ROOT)
        mirror = repository / Path(check["mirror_path"]).relative_to(PROJECT_ROOT)
        check["source_path"] = str(source.resolve())
        check["mirror_path"] = str(mirror.resolve())
        check["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        check["mirror_sha256"] = hashlib.sha256(mirror.read_bytes()).hexdigest()
        check["status"] = "equal"

    manifest = repository / "evidence/global-gate/exit-evidence-manifest.json"
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

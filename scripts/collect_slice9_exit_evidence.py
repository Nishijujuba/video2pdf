from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from slice9_exit_evidence_contract import (
    COMMANDS, EVIDENCE_PREFIX, EXPECTED_CHECKPOINTS, FIXTURE_SPECS,
    RESULT_BINDINGS, RESULTS, SLICE_BASE_COMMIT, SLICE_NAME, SLICE_NUMBER,
)
from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError, fingerprint_implementation_changes, git_output, sha256_file,
)


EVIDENCE_DIR = PROJECT_ROOT / "evidence/slice-09"
LOG_DIR = EVIDENCE_DIR / "logs"
MANIFEST_PATH = EVIDENCE_DIR / "exit-evidence-manifest.json"
REFRESH_ROOT = PROJECT_ROOT / "待删除/exit-evidence-refresh/slice-09"


def git(*arguments: str) -> str:
    try:
        return git_output(PROJECT_ROOT, *arguments)
    except EvidenceSupportError as exc:
        raise RuntimeError(str(exc)) from exc


def relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def preserve_previous_evidence() -> None:
    existing = [path for path in [MANIFEST_PATH, *LOG_DIR.glob("*.log")] if path.exists()]
    if not existing:
        return
    destination = REFRESH_ROOT / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    destination.mkdir(parents=True, exist_ok=False)
    for path in existing:
        os.replace(path, destination / relative(path).replace("/", "__"))


def main() -> int:
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        print("ERROR: Slice 9 evidence collection requires a clean implementation HEAD", file=sys.stderr)
        return 2
    implementation_commit = git("rev-parse", "HEAD")
    git("merge-base", "--is-ancestor", SLICE_BASE_COMMIT, implementation_commit)
    preserve_previous_evidence()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    command_evidence: list[dict[str, Any]] = []
    for command_id, command, expected_exit_code in COMMANDS:
        completed = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, check=False)
        raw = (completed.stdout + completed.stderr).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        raw += f"\nEVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n".encode("utf-8")
        log_path = LOG_DIR / f"{command_id}.log"
        log_path.write_bytes(raw)
        command_evidence.append({
            "test_id": command_id, "command": list(command),
            "expected_exit_code": expected_exit_code, "actual_exit_code": completed.returncode,
            "log": {"role": "command_log", "path": relative(log_path), "sha256": sha256_file(log_path)},
            "conforms": completed.returncode == expected_exit_code,
        })
    fixtures = [
        {"role": role, "path": path, "sha256": sha256_file(PROJECT_ROOT / path)}
        for role, path in FIXTURE_SPECS
    ]
    decision = "pass" if all(item["conforms"] for item in command_evidence) else "fail"
    manifest = {
        "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
        "schema_version": 2, "kind": "video-workflow-exit-evidence",
        "fingerprint_algorithm": "sha256-raw-v1",
        "slice": {"number": SLICE_NUMBER, "name": SLICE_NAME},
        "slice_base_commit": SLICE_BASE_COMMIT, "implementation_commit": implementation_commit,
        "evidence_paths": [relative(MANIFEST_PATH), *[item["log"]["path"] for item in command_evidence]],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "activation_scope": {"kind": "none", "runtime_authority_change": False, "components_activated": [], "legacy_track_authority": "preserved"},
        "commands": command_evidence, "expected_checkpoints": EXPECTED_CHECKPOINTS,
        "fixtures": fixtures, "results": RESULTS, "result_bindings": RESULT_BINDINGS,
        "artifact_fingerprints": fingerprint_implementation_changes(PROJECT_ROOT, SLICE_BASE_COMMIT, implementation_commit, excluded_prefixes=(EVIDENCE_PREFIX,)),
        "unresolved_exceptions": [], "overall_decision": decision,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(MANIFEST_PATH)
    return 0 if decision == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

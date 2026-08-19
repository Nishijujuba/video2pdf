from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from slice7_exit_evidence_contract import (
    COMMANDS,
    EVIDENCE_PREFIX,
    EXPECTED_CHECKPOINTS,
    FIXTURE_SPECS,
    RESULT_BINDINGS,
    RESULTS,
    SLICE_BASE_COMMIT,
    SLICE_NAME,
    SLICE_NUMBER,
)
from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    git_output,
    sha256_file,
)


EVIDENCE_DIR = PROJECT_ROOT / "evidence/slice-07"
LOG_DIR = EVIDENCE_DIR / "logs"
MANIFEST_PATH = EVIDENCE_DIR / "exit-evidence-manifest.json"
CONFORMANCE_REPORT = PROJECT_ROOT / "待删除/slice7-evidence-conformance-report.json"
EVIDENCE_REFRESH_ROOT = PROJECT_ROOT / "待删除/exit-evidence-refresh/slice-07"


def git(*arguments: str) -> str:
    try:
        return git_output(PROJECT_ROOT, *arguments)
    except EvidenceSupportError as exc:
        raise RuntimeError(str(exc)) from exc


def project_relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def preserve_previous_evidence() -> None:
    existing = [
        path
        for path in [
            MANIFEST_PATH,
            CONFORMANCE_REPORT,
            *LOG_DIR.glob("*.log"),
        ]
        if path.exists()
    ]
    if not existing:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    destination = EVIDENCE_REFRESH_ROOT / timestamp
    destination.mkdir(parents=True, exist_ok=False)
    for path in existing:
        os.replace(
            path,
            destination / project_relative(path).replace("/", "__"),
        )


def implementation_artifacts(implementation_commit: str) -> list[dict[str, str]]:
    try:
        return fingerprint_implementation_changes(
            PROJECT_ROOT,
            SLICE_BASE_COMMIT,
            implementation_commit,
            excluded_prefixes=(EVIDENCE_PREFIX,),
        )
    except EvidenceSupportError as exc:
        raise RuntimeError(str(exc)) from exc


def main() -> int:
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        print(
            "ERROR: Slice 7 evidence collection requires a clean implementation HEAD",
            file=sys.stderr,
        )
        return 2
    implementation_commit = git("rev-parse", "HEAD")
    git("merge-base", "--is-ancestor", SLICE_BASE_COMMIT, implementation_commit)
    preserve_previous_evidence()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    command_evidence: list[dict[str, Any]] = []
    for test_id, raw_command, expected_exit_code in COMMANDS:
        command = [
            implementation_commit if token == "<IMPLEMENTATION_COMMIT>" else token
            for token in raw_command
        ]
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
        )
        raw = (completed.stdout + completed.stderr).replace(
            b"\r\n", b"\n"
        ).replace(b"\r", b"\n")
        raw += (
            f"\nEVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n"
        ).encode("utf-8")
        log_path = LOG_DIR / f"{test_id}.log"
        log_path.write_bytes(raw)
        command_evidence.append(
            {
                "test_id": test_id,
                "command": command,
                "expected_exit_code": expected_exit_code,
                "actual_exit_code": completed.returncode,
                "log": {
                    "role": "command_log",
                    "path": project_relative(log_path),
                    "sha256": sha256_file(log_path),
                },
                "conforms": completed.returncode == expected_exit_code,
            }
        )

    fixtures = [
        {
            "role": role,
            "path": relative_path,
            "sha256": sha256_file(PROJECT_ROOT / relative_path),
        }
        for role, relative_path in FIXTURE_SPECS
    ]
    decision = (
        "pass"
        if all(command["conforms"] for command in command_evidence)
        else "fail"
    )
    manifest = {
        "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
        "schema_version": 2,
        "kind": "video-workflow-exit-evidence",
        "fingerprint_algorithm": "sha256-raw-v1",
        "slice": {"number": SLICE_NUMBER, "name": SLICE_NAME},
        "slice_base_commit": SLICE_BASE_COMMIT,
        "implementation_commit": implementation_commit,
        "evidence_paths": [
            project_relative(MANIFEST_PATH),
            *[item["log"]["path"] for item in command_evidence],
        ],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "activation_scope": {
            "kind": "none",
            "runtime_authority_change": False,
            "components_activated": [],
            "legacy_track_authority": "preserved",
        },
        "commands": command_evidence,
        "expected_checkpoints": EXPECTED_CHECKPOINTS,
        "fixtures": fixtures,
        "results": RESULTS,
        "result_bindings": RESULT_BINDINGS,
        "artifact_fingerprints": implementation_artifacts(implementation_commit),
        "unresolved_exceptions": [],
        "overall_decision": decision,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(MANIFEST_PATH)
    return 0 if decision == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

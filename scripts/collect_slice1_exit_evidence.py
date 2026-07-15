from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = PROJECT_ROOT / "evidence/slice-01"
LOG_DIR = EVIDENCE_DIR / "logs"
MANIFEST_PATH = EVIDENCE_DIR / "exit-evidence-manifest.json"
FIXTURE_ROOT = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"


COMMANDS = (
    (
        "slice0-regression",
        [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.video_workflow.test_legacy_baseline"],
    ),
    (
        "slice1-contracts",
        [sys.executable, "-X", "utf8", "-B", "scripts/video_workflow.py", "contracts-check"],
    ),
    (
        "slice1-public-deep-tests",
        [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.video_workflow.test_source_ready_tracer"],
    ),
    (
        "slice1-review-hardening-tests",
        [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.video_workflow.test_source_ready_hardening"],
    ),
    (
        "slice0-exit-evidence",
        [sys.executable, "-X", "utf8", "-B", "scripts/validate_exit_evidence_manifest.py", "evidence/slice-00/exit-evidence-manifest.json"],
    ),
    (
        "slice1-syntax",
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-c",
            (
                "import ast,pathlib;"
                "p=list(pathlib.Path('src/video2pdf_workflow_kernel').rglob('*.py'))+"
                "[pathlib.Path('scripts/video_workflow.py'),"
                "pathlib.Path('scripts/validate_slice_exit_evidence.py'),"
                "pathlib.Path('scripts/collect_slice1_exit_evidence.py')];"
                "[ast.parse(x.read_text(encoding='utf-8'),filename=str(x)) for x in p];"
                "print(f'AST_OK {len(p)}')"
            ),
        ],
    ),
    (
        "slice1-diff-check",
        ["git", "diff", "--check", "HEAD^", "HEAD"],
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def project_relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def preserve_previous_evidence() -> None:
    existing = [path for path in [MANIFEST_PATH, *LOG_DIR.glob("*.log")] if path.exists()]
    if not existing:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    destination = PROJECT_ROOT / "待删除/exit-evidence-refresh" / timestamp
    destination.mkdir(parents=True, exist_ok=False)
    for path in existing:
        target = destination / project_relative(path).replace("/", "__")
        os.replace(path, target)


def run_commands() -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    for test_id, command in COMMANDS:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
        )
        raw = completed.stdout + completed.stderr
        captured.append(
            {
                "test_id": test_id,
                "command": command,
                "expected_exit_code": 0,
                "actual_exit_code": completed.returncode,
                "raw": raw,
            }
        )
    return captured


def implementation_artifacts(implementation_commit: str) -> list[dict[str, str]]:
    paths = [
        line
        for line in git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", implementation_commit
        ).splitlines()
        if line
    ]
    fixture_prefix = "tests/video_workflow/fixtures/source-ready-tracer/"
    result: list[dict[str, str]] = []
    for path_value in sorted(paths):
        if path_value.startswith("evidence/") or path_value.startswith(fixture_prefix):
            continue
        path = PROJECT_ROOT / path_value
        if path.is_file():
            result.append(
                {
                    "role": "implementation_artifact",
                    "path": path_value,
                    "sha256": sha256(path),
                }
            )
    return result


def main() -> int:
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        print(
            "ERROR: Slice 1 evidence collection requires a clean implementation HEAD",
            file=sys.stderr,
        )
        return 2
    implementation_commit = git("rev-parse", "HEAD")
    captured = run_commands()
    preserve_previous_evidence()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    command_evidence: list[dict[str, Any]] = []
    for item in captured:
        log_path = LOG_DIR / f"{item['test_id']}.log"
        log_path.write_bytes(item.pop("raw"))
        command_evidence.append(
            {
                **item,
                "log": {
                    "role": "command_log",
                    "path": project_relative(log_path),
                    "sha256": sha256(log_path),
                },
                "conforms": item["actual_exit_code"] == item["expected_exit_code"],
            }
        )
    fixture_paths = [
        FIXTURE_ROOT / "fixture.json",
        FIXTURE_ROOT / "metadata/platform.json",
        FIXTURE_ROOT / "subtitles/subtitle.en.srt",
        FIXTURE_ROOT / "media/video.fixture",
        FIXTURE_ROOT / "cover/cover.fixture",
    ]
    fixtures = [
        {
            "role": "immutable_offline_fixture",
            "path": project_relative(path),
            "sha256": sha256(path),
        }
        for path in fixture_paths
    ]
    decision = "pass" if all(item["conforms"] for item in command_evidence) else "fail"
    manifest = {
        "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
        "schema_version": 2,
        "kind": "video-workflow-exit-evidence",
        "fingerprint_algorithm": "sha256-raw-v1",
        "slice": {"number": 1, "name": "offline-source-ready-tracer"},
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
        "expected_checkpoints": [{"name": "source_ready", "status": "current"}],
        "fixtures": fixtures,
        "results": {
            "positive": [
                "contracts_check_closed_registry_and_locked_runtime",
                "bootstrap_initialization_verified_import_source_ready",
                "deterministic_run_path_record_plan_and_generation",
                "utf16_239_and_240_pass",
                "idempotent_rerun_and_collision_safe_same_second_run",
                "fixture_adapter_deprived_of_production_capabilities",
                "bootstrap_probe_schema_and_canonical_identity_exact_binding",
                "control_store_marker_database_identity_and_health_probes",
                "registered_path_fingerprint_and_freshness_invariants",
                "locked_runtime_import_failure_returns_machine_envelope",
            ],
            "negative": [
                "unknown_contract_and_run_versions_rejected",
                "unknown_or_remote_schema_reference_rejected",
                "utf16_241_fails_before_binding_intent_or_run",
                "run_identity_path_and_true_same_second_collision_fail_closed",
                "imported_source_drift_invalidates_source_ready",
                "unknown_control_store_migration_blocks_startup",
                "missing_or_incomplete_control_store_never_auto_recreated",
                "published_or_committed_canonical_state_loss_blocks_retry",
            ],
            "recovery": [
                "every_initialization_persistence_boundary_converges_old_or_new_complete",
                "public_reconcile_run_completes_published_initialization",
                "prepublication_abort_can_retry_to_source_ready",
                "initialization_intent_transitions_use_compare_and_swap",
            ],
        },
        "artifact_fingerprints": implementation_artifacts(implementation_commit),
        "unresolved_exceptions": [],
        "overall_decision": decision,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(MANIFEST_PATH)
    return 0 if decision == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

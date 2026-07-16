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

from video2pdf_workflow_kernel.evidence import (
    EvidenceSupportError,
    fingerprint_implementation_changes,
    git_output,
    sha256_file,
)


EVIDENCE_DIR = PROJECT_ROOT / "evidence/slice-02"
LOG_DIR = EVIDENCE_DIR / "logs"
MANIFEST_PATH = EVIDENCE_DIR / "exit-evidence-manifest.json"
FIXTURE_ROOT = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
SLICE_BASE_COMMIT = "904f46409b87aca96aeecf5cb0be4855c2cfdafa"
EVIDENCE_PREFIX = "evidence/slice-02/"


COMMANDS = (
    (
        "slice0-regression",
        [
            sys.executable, "-X", "utf8", "-B", "-m", "unittest",
            "tests.video_workflow.test_legacy_baseline",
        ],
    ),
    (
        "slice2-contracts",
        [
            sys.executable, "-X", "utf8", "-B",
            "scripts/video_workflow.py", "contracts-check",
        ],
    ),
    (
        "slice1-regression",
        [
            sys.executable, "-X", "utf8", "-B", "-m", "unittest",
            "tests.video_workflow.test_source_ready_tracer",
            "tests.video_workflow.test_source_ready_hardening",
            "tests.video_workflow.test_issue4_gate4",
            "tests.video_workflow.test_issue4_gate7",
        ],
    ),
    (
        "slice2-task-promotion",
        [
            sys.executable, "-X", "utf8", "-B", "-m", "unittest",
            "tests.video_workflow.test_task_promotion",
        ],
    ),
    (
        "slice2-task-promotion-hardening",
        [
            sys.executable, "-X", "utf8", "-B", "-m", "unittest",
            "tests.video_workflow.test_task_promotion_hardening",
        ],
    ),
    (
        "slice2-review-repairs",
        [
            sys.executable, "-X", "utf8", "-B", "-m", "unittest",
            "tests.video_workflow.test_issue5_review_repairs",
        ],
    ),
    (
        "slice1-exit-evidence",
        [
            sys.executable, "-X", "utf8", "-B",
            "scripts/validate_slice_exit_evidence.py",
            "evidence/slice-01/exit-evidence-manifest.json",
        ],
    ),
    (
        "slice2-syntax",
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
                "pathlib.Path('scripts/collect_slice2_exit_evidence.py')];"
                "[ast.parse(x.read_text(encoding='utf-8'),filename=str(x)) for x in p];"
                "print(f'AST_OK {len(p)}')"
            ),
        ],
    ),
    (
        "slice2-diff-check",
        ["git", "diff", "--check", f"{SLICE_BASE_COMMIT}...HEAD"],
    ),
)


def git(*arguments: str) -> str:
    try:
        return git_output(PROJECT_ROOT, *arguments)
    except EvidenceSupportError as exc:
        raise RuntimeError(str(exc)) from exc


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


def run_commands(implementation_commit: str) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    for test_id, command in COMMANDS:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
        )
        command_output = (completed.stdout + completed.stderr).replace(
            b"\r\n", b"\n"
        ).replace(b"\r", b"\n")
        raw = (
            command_output
            + f"\nEVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n".encode("utf-8")
        )
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


def implementation_artifacts(
    slice_base_commit: str, implementation_commit: str
) -> list[dict[str, str]]:
    try:
        return fingerprint_implementation_changes(
            PROJECT_ROOT,
            slice_base_commit,
            implementation_commit,
            excluded_prefixes=(EVIDENCE_PREFIX,),
        )
    except EvidenceSupportError as exc:
        raise RuntimeError(str(exc)) from exc


def main() -> int:
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        print(
            "ERROR: Slice 2 evidence collection requires a clean implementation HEAD",
            file=sys.stderr,
        )
        return 2
    implementation_commit = git("rev-parse", "HEAD")
    captured = run_commands(implementation_commit)
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
                    "sha256": sha256_file(log_path),
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
            "sha256": sha256_file(path),
        }
        for path in fixture_paths
    ]
    decision = "pass" if all(item["conforms"] for item in command_evidence) else "fail"
    manifest = {
        "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
        "schema_version": 2,
        "kind": "video-workflow-exit-evidence",
        "fingerprint_algorithm": "sha256-raw-v1",
        "slice": {"number": 2, "name": "fenced-task-execution-and-promotion"},
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
        "expected_checkpoints": [
            {"name": "source_ready", "status": "current"},
            {"name": "source_acquisition_decision_ready", "status": "current"},
        ],
        "fixtures": fixtures,
        "results": {
            "positive": [
                "versioned_task_envelope_prompt_attempt_and_completion_contracts",
                "bounded_source_acquisition_judgment_patch",
                "completion_gate_validates_declared_outputs_without_promotion",
                "promotion_registers_artifact_generation_and_checkpoint",
                "same_run_publication_is_serialized_with_cross_run_independence",
                "second_generation_preserves_prior_bytes",
                "multiple_reclaims_preserve_complete_ordered_audit_history",
                "public_reconcile_authority_dispatches_kernel_run",
                "reconcile_run_wraps_the_same_authority_handler",
            ],
            "negative": [
                "late_and_superseded_workers_are_fenced",
                "undeclared_outputs_and_direct_canonical_writes_fail_closed",
                "protected_fields_stale_inputs_and_stale_revisions_fail_closed",
                "absolute_backslash_device_reparse_and_trailing_paths_fail_closed",
                "unknown_authority_kind_identifier_and_contract_versions_fail_closed",
                "prompt_envelope_completion_journal_and_canonical_tamper_fail_closed",
                "promotion_intent_coordinated_tamper_and_identity_downgrade_fail_closed",
                "run_wide_files_directories_and_unknown_task_roots_fail_closed",
                "every_nonterminal_recovery_revalidates_source_freshness",
                "missing_or_drifted_prior_generation_preservation_fails_closed",
                "reclaim_history_corruption_blocks_global_mutation",
            ],
            "recovery": [
                "claim_boundaries_resume_idempotently",
                "completion_boundaries_resume_idempotently",
                "promotion_boundaries_converge_to_one_committed_generation",
                "committed_mutation_hash_chain_covers_task_and_source_drift",
                "control_store_v1_through_v5_migrate_atomically_to_v6",
                "legacy_v4_committed_promotion_is_verified_and_backfilled",
                "legacy_v4_nonterminal_and_partial_v5_v6_migrations_fail_closed",
                "completion_retry_preserves_first_trusted_event_time",
                "prepared_prior_generation_preservation_rebuilds_before_publication",
                "v5_reclaim_history_migration_is_lossless_or_fails_atomically",
            ],
        },
        "artifact_fingerprints": implementation_artifacts(
            SLICE_BASE_COMMIT, implementation_commit
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


if __name__ == "__main__":
    raise SystemExit(main())

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


EVIDENCE_DIR = PROJECT_ROOT / "evidence/slice-01"
LOG_DIR = EVIDENCE_DIR / "logs"
MANIFEST_PATH = EVIDENCE_DIR / "exit-evidence-manifest.json"
FIXTURE_ROOT = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
SLICE_BASE_COMMIT = "96089b99c9ae63fff61107e1920fc3481ffc0802"
EVIDENCE_PREFIX = "evidence/slice-01/"


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
        "slice1-gate4-saga-containment-tests",
        [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.video_workflow.test_issue4_gate4"],
    ),
    (
        "slice1-gate7-review-repair-tests",
        [sys.executable, "-X", "utf8", "-B", "-m", "unittest", "tests.video_workflow.test_issue4_gate7"],
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
        raw = (
            completed.stdout
            + completed.stderr
            + f"\nEVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n".encode(
                "utf-8"
            )
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
            "ERROR: Slice 1 evidence collection requires a clean implementation HEAD",
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
        "slice": {"number": 1, "name": "offline-source-ready-tracer"},
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
        "expected_checkpoints": [{"name": "source_ready", "status": "current"}],
        "fixtures": fixtures,
        "results": {
            "positive": [
                "registry_exact_canonical_set_and_locked_runtime_validated",
                "bootstrap_initialization_verified_import_source_ready",
                "artifact_plan_exact_six_bindings_generated",
                "artifact_plan_builder_and_invariant_share_one_immutable_binding_source",
                "fixture_schema_invariant_and_runtime_containment_validated",
                "run_record_canonical_absolute_output_path_validated",
                "utf16_239_and_240_pass",
                "idempotent_rerun_and_collision_safe_same_second_run",
                "fixture_adapter_deprived_of_production_capabilities",
                "bootstrap_probe_schema_and_canonical_identity_exact_binding",
                "control_store_external_anchor_marker_database_identity_validated",
                "control_store_lock_and_same_volume_atomic_replace_probes_pass",
                "initialization_intent_binds_expected_run_and_source_identity_before_publication",
                "control_store_v1_intent_schema_migrates_safely_to_v3",
                "workflow_entries_run_full_control_store_preflight_before_queries",
                "registered_path_fingerprint_and_freshness_invariants",
                "scaffold_contract_and_runtime_root_containment_validated",
                "scaffold_contract_exact_registered_canonical_instance_validated",
                "contract_registry_single_locked_preparation_path_validated",
                "alternate_registry_exact_authority_metadata_and_file_completeness_validated",
                "source_drift_run_state_mutation_saga_committed",
                "run_record_authority_predecessor_checked_before_source_drift",
                "locked_runtime_import_failure_returns_machine_envelope",
            ],
            "negative": [
                "unknown_contract_and_run_versions_rejected",
                "unknown_or_remote_schema_reference_rejected",
                "partial_duplicate_or_extra_registry_rejected",
                "same_version_weaker_or_retargeted_alternate_registry_rejected_before_validation",
                "artifact_plan_missing_or_extra_binding_rejected",
                "fixture_backslash_traversal_device_and_trailing_paths_rejected",
                "relative_or_noncanonical_run_output_path_rejected",
                "utf16_241_fails_before_binding_intent_or_run",
                "run_identity_path_and_true_same_second_collision_fail_closed",
                "imported_source_drift_invalidates_source_ready",
                "unknown_control_store_migration_blocks_startup",
                "full_or_partial_control_store_loss_never_auto_recreated",
                "control_store_external_anchor_tamper_blocks_startup",
                "published_or_committed_canonical_state_loss_blocks_retry",
                "published_prepared_run_identity_tamper_blocks_recovery",
                "committed_coordinated_run_rewrite_rejected_without_mutation",
                "schema_valid_run_record_tamper_creates_zero_mutation_intents",
                "schema_valid_scaffold_parent_escape_rejected_before_creation",
                "live_anchor_or_store_displacement_returns_control_store_unavailable",
            ],
            "recovery": [
                "every_initialization_persistence_boundary_converges_old_or_new_complete",
                "public_reconcile_run_completes_published_initialization",
                "prepublication_abort_can_retry_to_source_ready",
                "initialization_intent_transitions_use_compare_and_swap",
                "source_drift_mutation_faults_converge_to_committed_stale_state",
                "run_record_current_hash_is_proven_by_committed_mutation_chain",
                "prepared_drift_resume_revalidates_predecessor_revision_and_identity",
            ],
        },
        "artifact_fingerprints": implementation_artifacts(
            SLICE_BASE_COMMIT, implementation_commit
        ),
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

from __future__ import annotations

import sys


SLICE_BASE_COMMIT = "7b33a2dcf8b19608943f12efd814907a69c35e8f"
SLICE_NUMBER = 5
SLICE_NAME = "single-section-production"
EVIDENCE_PREFIX = "evidence/slice-05/"

EXPECTED_CHECKPOINTS = [
    {"name": "source_ready", "status": "current"},
    {"name": "draft_compile_ready", "status": "current"},
]

PRODUCTION_TEST_TARGETS = (
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_public_plan_and_advance_reach_guarded_diagnostic_compile",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_public_cli_plan_is_idempotent_and_advance_fail_closed_machine_readable",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_compile_preflight_rejects_shell_escape_before_engine_launch",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_compile_manifest_rejects_undeclared_or_escaping_project_input",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_runtime_policy_and_recorder_evidence_fail_closed",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_multi_file_figure_promotion_recovers_after_partial_publication",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_state_commit_receipt_is_retry_idempotent_and_fences_late_attempt",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_writer_and_figure_promotions_are_serialized_across_processes",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_production_artifact_drift_blocks_the_next_gate",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_figure_manifest_rejects_a_mismatched_slot_contribution",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_promotion_journal_recovers_prepared_and_committed_boundaries",
)

COMMANDS = (
    (
        "slice5-contracts",
        (sys.executable, "-X", "utf8", "-B", "scripts/video_workflow.py", "contracts-check"),
    ),
    (
        "slice5-production",
        (sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-v", *PRODUCTION_TEST_TARGETS),
    ),
    (
        "slice5-full-video-workflow",
        (sys.executable, "-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests/video_workflow", "-p", "test_*.py"),
    ),
    (
        "slice4-exit-evidence",
        (sys.executable, "-X", "utf8", "-B", "scripts/validate_slice_exit_evidence.py", "evidence/slice-04/exit-evidence-manifest.json"),
    ),
    (
        "slice5-syntax",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-c",
            "import ast,pathlib;p=list(pathlib.Path('src/video2pdf_workflow_kernel').rglob('*.py'))+list(pathlib.Path('scripts').glob('*.py'))+list(pathlib.Path('tests/video_workflow').glob('test_*.py'));[ast.parse(x.read_text(encoding='utf-8'),filename=str(x)) for x in p];print(f'AST_OK {len(p)}')",
        ),
    ),
    ("slice5-diff-check", ("git", "diff", "--check", f"{SLICE_BASE_COMMIT}...HEAD")),
)

RESULTS = {
    "positive": [
        "public_operations_reach_guarded_diagnostic_compile",
        "writer_and_figure_attempts_have_disjoint_write_sets",
        "recorder_proves_declared_compile_dependency_closure",
        "writer_and_figure_promotions_are_serialized",
    ],
    "negative": [
        "shell_escape_and_path_escape_fail_closed",
        "undeclared_inputs_and_recursive_workspace_content_are_excluded",
        "unsafe_runtime_policy_and_recorder_gap_fail_closed",
        "artifact_drift_fails_closed",
        "figure_contribution_mismatch_fails_closed",
    ],
    "recovery": [
        "production_plan_is_idempotent_and_missing_attempt_is_rejected",
        "multi_file_figure_promotion_recovers_after_partial_publication",
        "committed_receipt_retry_is_idempotent",
        "promotion_boundaries_recover",
    ],
}


def _binding(result_id: str, kind: str, target: str) -> dict[str, str]:
    return {
        "result_id": result_id,
        "result_kind": kind,
        "command_id": "slice5-production",
        "test_target": target,
    }


RESULT_BINDINGS = [
    _binding(RESULTS["positive"][0], "positive", PRODUCTION_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][1], "positive", PRODUCTION_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][2], "positive", PRODUCTION_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][3], "positive", PRODUCTION_TEST_TARGETS[7]),
    _binding(RESULTS["negative"][0], "negative", PRODUCTION_TEST_TARGETS[2]),
    _binding(RESULTS["negative"][1], "negative", PRODUCTION_TEST_TARGETS[3]),
    _binding(RESULTS["negative"][2], "negative", PRODUCTION_TEST_TARGETS[4]),
    _binding(RESULTS["negative"][3], "negative", PRODUCTION_TEST_TARGETS[8]),
    _binding(RESULTS["negative"][4], "negative", PRODUCTION_TEST_TARGETS[9]),
    _binding(RESULTS["recovery"][0], "recovery", PRODUCTION_TEST_TARGETS[1]),
    _binding(RESULTS["recovery"][1], "recovery", PRODUCTION_TEST_TARGETS[5]),
    _binding(RESULTS["recovery"][2], "recovery", PRODUCTION_TEST_TARGETS[6]),
    _binding(RESULTS["recovery"][3], "recovery", PRODUCTION_TEST_TARGETS[10]),
]

FIXTURE_SPECS = (
    ("compile_provider_fixture", "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py"),
    ("positive_schema_fixture", "tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice5.valid.json"),
    ("negative_schema_fixture", "tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice5.missing-closure.invalid.json"),
)

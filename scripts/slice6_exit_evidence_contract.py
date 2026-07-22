from __future__ import annotations
import sys

SLICE_BASE_COMMIT = "6f8241ddb4bd725d3b584dd1c403ed59dda32219"
SLICE_NUMBER = 6
SLICE_NAME = "multi-section-production"
EVIDENCE_PREFIX = "evidence/slice-06/"
EXPECTED_CHECKPOINTS = [{"name":"source_ready","status":"current"},{"name":"draft_compile_ready","status":"current"}]
PRODUCTION_TEST_TARGETS = (
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_three_sections_release_isolated_writers_and_required_figures_after_outline_barrier",
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_disjoint_section_attempts_execute_concurrently_and_promote_serially",
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_overlapping_write_sets_are_rejected_before_plan_or_promotion",
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_required_figure_wave_runs_while_other_writers_remain_active",
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_writer_candidate_launches_one_deterministic_incremental_wave",
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_cross_section_candidate_is_rejected_before_promotion",
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_second_incremental_figure_wave_fails_closed_after_writer_supersede",
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_identical_inputs_with_reversed_completion_order_produce_identical_manifest",
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_branch_change_invalidates_only_transitive_dependants",
    "tests.video_workflow.test_multi_section_production.MultiSectionProductionTests.test_late_worker_cannot_overwrite_advanced_section_generation",
    "tests.video_workflow.test_single_section_production.SingleSectionProductionTests.test_state_commit_receipt_is_retry_idempotent_and_fences_late_attempt",
)
SLICE5_COMPATIBILITY_TEST_TARGET = (
    "tests.video_workflow.test_issue9_exit_evidence."
    "Slice6ExitEvidenceTests.test_slice5_exit_evidence_remains_valid"
)
COMMANDS = (
    ("slice6-contracts",(sys.executable,"-X","utf8","-B","scripts/video_workflow.py","contracts-check")),
    ("slice6-production",(sys.executable,"-X","utf8","-B","-m","unittest","-v",*PRODUCTION_TEST_TARGETS)),
    ("slice6-full-video-workflow",(sys.executable,"-X","utf8","-B","-m","unittest","discover","-s","tests/video_workflow","-p","test_*.py")),
    ("slice5-exit-evidence",(sys.executable,"-X","utf8","-B","-m","unittest","-v",SLICE5_COMPATIBILITY_TEST_TARGET)),
    ("slice6-syntax",(sys.executable,"-X","utf8","-B","-c","import ast,pathlib;p=list(pathlib.Path('src/video2pdf_workflow_kernel').rglob('*.py'))+list(pathlib.Path('scripts').glob('*.py'))+list(pathlib.Path('tests/video_workflow').glob('test_*.py'));[ast.parse(x.read_text(encoding='utf-8'),filename=str(x)) for x in p];print(f'AST_OK {len(p)}')")),
    ("slice6-diff-check",("git","diff","--check",f"{SLICE_BASE_COMMIT}...HEAD")),
)
RESULTS = {
    "positive":["three_section_parallel_release","required_wave_concurrency","serial_promotion","deterministic_integration"],
    "negative":["overlapping_write_sets_rejected","second_incremental_wave_rejected","cross_section_candidate_rejected"],
    "fencing":["late_worker_rejected"],
    "restart":["receipt_retry_idempotent"],
    "recovery":["selective_branch_invalidation","slice5_evidence_remains_valid"],
}
def _binding(result_id: str, kind: str, target: str, command_id: str = "slice6-production") -> dict[str,str]:
    return {"result_id":result_id,"result_kind":kind,"command_id":command_id,"test_target":target}
RESULT_BINDINGS = [
    _binding(RESULTS["positive"][0],"positive",PRODUCTION_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][1],"positive",PRODUCTION_TEST_TARGETS[3]),
    _binding(RESULTS["positive"][2],"positive",PRODUCTION_TEST_TARGETS[1]),
    _binding(RESULTS["positive"][3],"positive",PRODUCTION_TEST_TARGETS[7]),
    _binding(RESULTS["negative"][0],"negative",PRODUCTION_TEST_TARGETS[2]),
    _binding(RESULTS["negative"][1],"negative",PRODUCTION_TEST_TARGETS[6]),
    _binding(RESULTS["negative"][2],"negative",PRODUCTION_TEST_TARGETS[5]),
    _binding(RESULTS["fencing"][0],"fencing",PRODUCTION_TEST_TARGETS[9]),
    _binding(RESULTS["restart"][0],"restart",PRODUCTION_TEST_TARGETS[10]),
    _binding(RESULTS["recovery"][0],"recovery",PRODUCTION_TEST_TARGETS[8]),
    _binding(RESULTS["recovery"][1],"recovery",SLICE5_COMPATIBILITY_TEST_TARGET,"slice5-exit-evidence"),
]
FIXTURE_SPECS = (
    ("multi_section_outline","tests/video_workflow/fixtures/contracts/outline-contract.v2.valid.json"),
    ("positive_schema_fixture","tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice6.valid.json"),
    ("negative_schema_fixture","tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice6.missing-determinism.invalid.json"),
)

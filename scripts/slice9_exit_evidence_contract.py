from __future__ import annotations

import sys


SLICE_BASE_COMMIT = "abcf95744110070a435baebb7900393f6ffb75fd"
SLICE_NUMBER = 9
SLICE_NAME = "final-compile-and-rendered-text-reconciliation"
EVIDENCE_PREFIX = "evidence/slice-09/"

QUALIFICATION_TEST_TARGETS = (
    "tests.video_workflow.test_rendered_text_reconciliation.RenderedTextReconciliationCliTests.test_complete_current_final_compile_evidence_passes",
    "tests.video_workflow.test_rendered_text_reconciliation.RenderedTextReconciliationCliTests.test_omission_substitution_addition_and_generated_mismatch_are_failures",
    "tests.video_workflow.test_rendered_text_reconciliation.RenderedTextReconciliationCliTests.test_unmapped_unsupported_recipe_and_incomplete_coverage_block_as_contract_gaps",
    "tests.video_workflow.test_rendered_text_reconciliation.RenderedTextReconciliationCliTests.test_stale_seal_is_rejected_before_report_publication",
    "tests.video_workflow.test_rendered_text_reconciliation.RenderedTextReconciliationCliTests.test_stale_compile_report_is_rejected_before_report_publication",
    "tests.video_workflow.test_rendered_text_reconciliation.RenderedTextReconciliationCliTests.test_compile_closure_duplicate_origin_missing_page_and_unsupported_object_fail_closed",
)

COMMANDS = (
    (
        "slice9-contracts",
        (sys.executable, "-X", "utf8", "-B", "scripts/video_workflow.py", "delivery-quality-contracts-check"),
        0,
    ),
    (
        "slice9-qualification-tests",
        (sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-v", *QUALIFICATION_TEST_TARGETS),
        0,
    ),
)

EXPECTED_CHECKPOINTS = [
    {"name": "final_artifact_seal_current", "status": "current"},
    {"name": "rendered_text_reconciliation_current", "status": "current"},
]

RESULT_BINDINGS = [
    {"result_id": result_id, "result_kind": result_kind, "command_id": "slice9-qualification-tests", "test_target": test_target}
    for result_id, result_kind, test_target in (
        ("complete_final_evidence_passes", "positive", QUALIFICATION_TEST_TARGETS[0]),
        ("classified_fidelity_failures_block", "negative", QUALIFICATION_TEST_TARGETS[1]),
        ("contract_gaps_fail_closed", "negative", QUALIFICATION_TEST_TARGETS[2]),
        ("stale_precompile_seal_rejected", "negative", QUALIFICATION_TEST_TARGETS[3]),
        ("stale_compile_report_rejected", "negative", QUALIFICATION_TEST_TARGETS[4]),
        ("closure_and_coverage_gaps_rejected", "negative", QUALIFICATION_TEST_TARGETS[5]),
    )
]

RESULTS = {
    "positive": ["complete_final_evidence_passes"],
    "negative": [
        "classified_fidelity_failures_block",
        "contract_gaps_fail_closed",
        "stale_precompile_seal_rejected",
        "stale_compile_report_rejected",
        "closure_and_coverage_gaps_rejected",
    ],
}

FIXTURE_SPECS = (
    ("final_artifact_seal_contract", "schemas/delivery-quality/v1/final-artifact-seal.v1.schema.json"),
    ("render_evidence_contract", "schemas/delivery-quality/v1/render-evidence-manifest.v1.schema.json"),
    ("rendered_text_inventory_contract", "schemas/delivery-quality/v1/rendered-text-object-inventory.v1.schema.json"),
    ("text_origin_manifest_contract", "schemas/delivery-quality/v1/text-origin-manifest.v1.schema.json"),
    ("rendered_text_report_contract", "schemas/delivery-quality/v1/rendered-text-reconciliation-report.v1.schema.json"),
)

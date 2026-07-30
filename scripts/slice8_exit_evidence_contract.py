from __future__ import annotations

import sys


SLICE_BASE_COMMIT = "bb18eb2112bd4d9f3403d46230dbee8131389eaf"
SLICE_NUMBER = 8
SLICE_NAME = "precompile-quality-and-text-seal"
EVIDENCE_PREFIX = "evidence/slice-08/"

QUALIFICATION_TEST_TARGETS = (
    "tests.video_workflow.test_precompile_quality.PrecompileQualityCliTests.test_independent_complete_patches_materialize_pass_and_create_initial_seal",
    "tests.video_workflow.test_precompile_quality.PrecompileQualityCliTests.test_failed_generation_is_repaired_then_fresh_reviewers_pass_successor",
    "tests.video_workflow.test_precompile_quality.PrecompileQualityCliTests.test_successor_inputs_mutated_after_equivalence_cannot_be_sealed",
    "tests.video_workflow.test_precompile_quality.PrecompileQualityCliTests.test_each_successor_advances_from_the_immediate_predecessor_seal",
    "tests.video_workflow.test_precompile_quality.PrecompileQualityCliTests.test_reviewer_identity_must_be_distinct_and_outside_generation_producers",
    "tests.video_workflow.test_precompile_quality.PrecompileQualityCliTests.test_contract_gap_blocks_materialization_without_consuming_attempt",
)

COMMANDS = (
    (
        "slice8-contracts",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "scripts/video_workflow.py",
            "delivery-quality-contracts-check",
        ),
        0,
    ),
    (
        "slice8-qualification-tests",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "-v",
            *QUALIFICATION_TEST_TARGETS,
        ),
        0,
    ),
)

EXPECTED_CHECKPOINTS = [
    {"name": "precompile_quality_report_current", "status": "current"},
    {"name": "precompile_text_seal_current", "status": "current"},
]

RESULT_BINDINGS = [
    {
        "result_id": result_id,
        "result_kind": result_kind,
        "command_id": "slice8-qualification-tests",
        "test_target": test_target,
    }
    for result_id, result_kind, test_target in (
        ("passing_report_creates_seal", "positive", QUALIFICATION_TEST_TARGETS[0]),
        ("fail_repair_pass_advances_generation", "recovery", QUALIFICATION_TEST_TARGETS[1]),
        ("stale_successor_rejected", "negative", QUALIFICATION_TEST_TARGETS[2]),
        ("immediate_predecessor_lineage", "positive", QUALIFICATION_TEST_TARGETS[3]),
        ("reviewer_isolation_enforced", "negative", QUALIFICATION_TEST_TARGETS[4]),
        ("contract_gap_preserves_budget", "negative", QUALIFICATION_TEST_TARGETS[5]),
    )
]

RESULTS = {
    "positive": ["passing_report_creates_seal", "immediate_predecessor_lineage"],
    "negative": [
        "stale_successor_rejected",
        "reviewer_isolation_enforced",
        "contract_gap_preserves_budget",
    ],
    "recovery": ["fail_repair_pass_advances_generation"],
}

FIXTURE_SPECS = (
    (
        "artifact_generation_contract",
        "schemas/delivery-quality/v1/precompile-artifact-generation-set.v1.schema.json",
    ),
    (
        "reader_text_inventory_contract",
        "schemas/delivery-quality/v1/reader-facing-text-inventory.v1.schema.json",
    ),
    (
        "precompile_report_contract",
        "schemas/delivery-quality/v1/precompile-quality-report.v1.schema.json",
    ),
    (
        "text_seal_contract",
        "schemas/delivery-quality/v1/precompile-text-seal.v1.schema.json",
    ),
)

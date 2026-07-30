from __future__ import annotations

import sys


SLICE_BASE_COMMIT = "68189e7744e22c9ce78b3ee1a58def69d09e711a"
SLICE_NUMBER = 7
SLICE_NAME = "delivery-quality-contracts-and-conformance"
EVIDENCE_PREFIX = "evidence/slice-07/"

EXPECTED_CHECKPOINTS = [
    {"name": "delivery_quality_contracts_current", "status": "current"},
    {"name": "delivery_quality_conformance_current", "status": "current"},
]

QUALIFICATION_TEST_TARGETS = (
    "tests.video_workflow.test_delivery_quality_contracts.DeliveryQualityContractsCliTests.test_public_contract_check_proves_closed_target_only_policy_surface",
    "tests.video_workflow.test_delivery_quality_contracts.DeliveryQualityContractsCliTests.test_contract_check_rejects_each_required_fail_closed_class",
    "tests.video_workflow.test_delivery_quality_contracts.DeliveryQualityContractsCliTests.test_public_conformance_runs_three_isolated_attempts_per_profile_case",
    "tests.video_workflow.test_delivery_quality_contracts.DeliveryQualityContractsCliTests.test_conformance_reports_semantic_variance_without_hiding_other_results",
)

COMMANDS = (
    (
        "slice7-contracts",
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
        "slice7-conformance",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "scripts/video_workflow.py",
            "delivery-quality-conformance",
            "--reviewer-adapter",
            "tests/video_workflow/fixtures/delivery-quality/deterministic_reviewer_adapter.py",
            "--output",
            "待删除/slice7-evidence-conformance-report.json",
            "--implementation-commit",
            "<IMPLEMENTATION_COMMIT>",
        ),
        0,
    ),
    (
        "slice7-qualification-tests",
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

RESULTS = {
    "positive": [
        "registered_contracts_validate",
        "isolated_semantic_conformance",
    ],
    "negative": [
        "fail_closed_contract_classes",
        "semantic_variance_reported",
    ],
    "recovery": ["target_only_legacy_authority_preserved"],
}

RESULT_BINDINGS = [
    {
        "result_id": "registered_contracts_validate",
        "result_kind": "positive",
        "command_id": "slice7-qualification-tests",
        "test_target": QUALIFICATION_TEST_TARGETS[0],
    },
    {
        "result_id": "isolated_semantic_conformance",
        "result_kind": "positive",
        "command_id": "slice7-qualification-tests",
        "test_target": QUALIFICATION_TEST_TARGETS[2],
    },
    {
        "result_id": "fail_closed_contract_classes",
        "result_kind": "negative",
        "command_id": "slice7-qualification-tests",
        "test_target": QUALIFICATION_TEST_TARGETS[1],
    },
    {
        "result_id": "semantic_variance_reported",
        "result_kind": "negative",
        "command_id": "slice7-qualification-tests",
        "test_target": QUALIFICATION_TEST_TARGETS[3],
    },
    {
        "result_id": "target_only_legacy_authority_preserved",
        "result_kind": "recovery",
        "command_id": "slice7-qualification-tests",
        "test_target": QUALIFICATION_TEST_TARGETS[0],
    },
]

FIXTURE_SPECS = (
    ("canonical_rule_catalog", "delivery-quality/v1/rule-catalog.v1.json"),
    ("canonical_language_profiles", "delivery-quality/v1/language-profiles.v1.json"),
    ("canonical_role_projections", "delivery-quality/v1/role-projections.v1.json"),
    ("conformance_corpus", "delivery-quality/v1/conformance-corpus.v1.json"),
    (
        "isolated_reviewer_adapter",
        "tests/video_workflow/fixtures/delivery-quality/deterministic_reviewer_adapter.py",
    ),
)

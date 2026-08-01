from __future__ import annotations

import sys


SLICE_BASE_COMMIT = "3fa54d09ab39349dd05bf225fefbc408046d4015"
SLICE_NUMBER = 10
SLICE_NAME = "acceptance-report-v2-and-bounded-repair"
EVIDENCE_PREFIX = "evidence/slice-10/"

QUALIFICATION_TEST_TARGETS = (
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_complete_current_evidence_materializes_all_catalog_rules_and_guard_eligibility",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_visual_patch_missing_page_fails_at_visual_page_coverage_gate",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_incomplete_read_set_and_stale_fencing_token_fail_closed",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_page_fingerprint_and_reviewer_identity_are_authority_bound",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_authorized_root_escape_fails_before_input_io",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_workspace_rejects_a_second_nonterminal_execution",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_prepare_retry_recovers_after_control_commit",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_cross_phase_finding_can_only_add_precompile_failure",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_unknown_violation_routes_to_contract_gap_without_attempt",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_stale_artifact_rejects_materialization_at_input_freshness_gate",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_repair_requires_fresh_artifact_generation_and_bounds_three_failures",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_contract_gap_does_not_consume_semantic_attempt",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_reconcile_rejects_changed_published_bytes_and_finishes_intact_publication",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_reconcile_finishes_an_intact_interrupted_report_publication",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_guard_rejects_stale_report_bytes",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_successful_patch_and_terminal_materialization_retries_are_idempotent",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_two_writers_are_fenced_at_patch_and_report_publication",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_run_record_control_authority_and_skeleton_drift_fail_closed",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_render_manifest_page_set_must_equal_visual_binding",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_guard_binds_immutable_attempt_and_ledger_history",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_text_equivalence_successor_retains_precompile_judgments",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_final_quality_authority_rejects_coherent_stale_evidence_mix",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_guard_rejects_tampered_prior_semantic_attempt_history",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_repair_rejects_underdeclared_changed_generation_set",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_idempotent_retries_reject_drifted_committed_bytes",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_final_quality_authority_requires_control_store_cas_publication",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_report_bundle_recovery_and_terminal_retry_reject_companion_drift",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_reconcile_rejects_mutable_intent_authority_and_path_substitution",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_materialization_rejects_post_commit_patch_authority_substitution",
    "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests.test_patch_exact_retry_rejects_tampered_committed_intent_authority",
)

COMMANDS = (
    ("slice10-contracts", (sys.executable, "-X", "utf8", "-B", "scripts/video_workflow.py", "delivery-quality-contracts-check"), 0),
    ("slice10-acceptance-tests", (sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-v", *QUALIFICATION_TEST_TARGETS), 0),
)

EXPECTED_CHECKPOINTS = [
    {"name": "acceptance_report_v2_target_only", "status": "current"},
    {"name": "legacy_delivery_authority", "status": "preserved"},
]

RESULT_BINDINGS = [
    {"result_id": result_id, "result_kind": result_kind, "command_id": "slice10-acceptance-tests", "test_target": target}
    for result_id, result_kind, target in (
        ("passing_report_is_guard_eligible", "positive", QUALIFICATION_TEST_TARGETS[0]),
        ("visual_page_gap_fails_first", "negative", QUALIFICATION_TEST_TARGETS[1]),
        ("read_set_and_fencing_fail_closed", "negative", QUALIFICATION_TEST_TARGETS[2]),
        ("page_and_reviewer_authority_bound", "negative", QUALIFICATION_TEST_TARGETS[3]),
        ("path_escape_fails_before_io", "negative", QUALIFICATION_TEST_TARGETS[4]),
        ("single_active_execution_enforced", "negative", QUALIFICATION_TEST_TARGETS[5]),
        ("prepare_control_commit_recovers", "recovery", QUALIFICATION_TEST_TARGETS[6]),
        ("cross_phase_is_add_failure_only", "negative", QUALIFICATION_TEST_TARGETS[7]),
        ("unknown_violation_is_contract_gap", "negative", QUALIFICATION_TEST_TARGETS[8]),
        ("stale_input_fails_first", "negative", QUALIFICATION_TEST_TARGETS[9]),
        ("third_semantic_failure_routes_manual", "negative", QUALIFICATION_TEST_TARGETS[10]),
        ("contract_gap_consumes_no_attempt", "negative", QUALIFICATION_TEST_TARGETS[11]),
        ("patch_publication_detects_contradiction_and_recovers", "recovery", QUALIFICATION_TEST_TARGETS[12]),
        ("report_publication_recovers", "recovery", QUALIFICATION_TEST_TARGETS[13]),
        ("guard_rejects_stale_report", "negative", QUALIFICATION_TEST_TARGETS[14]),
        ("persistent_mutations_are_idempotent", "recovery", QUALIFICATION_TEST_TARGETS[15]),
        ("concurrent_writers_are_fenced", "fencing", QUALIFICATION_TEST_TARGETS[16]),
        ("run_authority_and_skeleton_are_current", "negative", QUALIFICATION_TEST_TARGETS[17]),
        ("render_manifest_pages_are_authoritative", "negative", QUALIFICATION_TEST_TARGETS[18]),
        ("guard_binds_attempt_and_ledger", "negative", QUALIFICATION_TEST_TARGETS[19]),
        ("equivalence_retains_precompile_judgments", "positive", QUALIFICATION_TEST_TARGETS[20]),
        ("final_quality_authority_rejects_stale_mix", "negative", QUALIFICATION_TEST_TARGETS[21]),
        ("guard_binds_prior_attempt_history", "negative", QUALIFICATION_TEST_TARGETS[22]),
        ("repair_changed_set_is_derived", "negative", QUALIFICATION_TEST_TARGETS[23]),
        ("idempotent_retries_revalidate_bytes", "negative", QUALIFICATION_TEST_TARGETS[24]),
        ("final_quality_authority_requires_cas", "fencing", QUALIFICATION_TEST_TARGETS[25]),
        ("report_bundle_companions_are_atomic", "recovery", QUALIFICATION_TEST_TARGETS[26]),
        ("recovery_intent_authority_is_immutable", "negative", QUALIFICATION_TEST_TARGETS[27]),
        ("committed_patch_authority_is_immutable", "negative", QUALIFICATION_TEST_TARGETS[28]),
        ("patch_exact_retry_revalidates_authority", "negative", QUALIFICATION_TEST_TARGETS[29]),
    )
]

RESULTS = {
    "positive": ["passing_report_is_guard_eligible", "equivalence_retains_precompile_judgments"],
    "negative": ["visual_page_gap_fails_first", "read_set_and_fencing_fail_closed", "page_and_reviewer_authority_bound", "path_escape_fails_before_io", "single_active_execution_enforced", "cross_phase_is_add_failure_only", "unknown_violation_is_contract_gap", "stale_input_fails_first", "third_semantic_failure_routes_manual", "contract_gap_consumes_no_attempt", "guard_rejects_stale_report", "run_authority_and_skeleton_are_current", "render_manifest_pages_are_authoritative", "guard_binds_attempt_and_ledger", "final_quality_authority_rejects_stale_mix", "guard_binds_prior_attempt_history", "repair_changed_set_is_derived", "idempotent_retries_revalidate_bytes", "recovery_intent_authority_is_immutable", "committed_patch_authority_is_immutable", "patch_exact_retry_revalidates_authority"],
    "recovery": ["prepare_control_commit_recovers", "patch_publication_detects_contradiction_and_recovers", "report_publication_recovers", "persistent_mutations_are_idempotent", "report_bundle_companions_are_atomic"],
    "fencing": ["concurrent_writers_are_fenced", "final_quality_authority_requires_cas"],
}

FIXTURE_SPECS = tuple(
    (role, f"schemas/delivery-quality/v1/{filename}")
    for role, filename in (
        ("acceptance_input_binding_contract", "acceptance-v2-input-binding.v1.schema.json"),
        ("acceptance_skeleton_contract", "acceptance-v2-review-skeleton.v1.schema.json"),
        ("acceptance_patch_contract", "acceptance-v2-judgment-patch.v1.schema.json"),
        ("acceptance_execution_contract", "acceptance-v2-execution-context.v1.schema.json"),
        ("acceptance_report_v2_contract", "acceptance-report-v2.v1.schema.json"),
        ("acceptance_attempt_record_contract", "acceptance-v2-attempt-record.v1.schema.json"),
        ("acceptance_repair_ledger_contract", "acceptance-v2-repair-ledger.v1.schema.json"),
    )
)

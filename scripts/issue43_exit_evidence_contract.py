from __future__ import annotations

import sys


SLICE_BASE_COMMIT = "64f3fb1638f601b533cb0ee4dec908203c1bef71"
SLICE_NUMBER = 11
SLICE_NAME = "global-acceptance-v2-gate"
EVIDENCE_PREFIX = "evidence/global-gate/"

ATOMIC_MEMBERS = (
    "catalogs",
    "projections",
    "criteria_migration",
    "schemas",
    "providers",
    "validators",
    "hooks",
    "skills",
    "project_instructions",
    "mirrors",
    "tests",
    "activation_documentation",
)
ATOMIC_MEMBER_STATUS = {member: "active" for member in ATOMIC_MEMBERS}
MIRROR_SPECS = tuple(
    (
        f".agents/skills/{name}/SKILL.md",
        f".claude/skills/{name}/SKILL.md",
    )
    for name in (
        "final-delivery-acceptance",
        "bilibili-render-pdf",
        "youtube-render-pdf",
    )
)
MIRROR_SPECS += ((
    ".agents/skills/final-delivery-acceptance/scripts/delivery_guard.py",
    ".claude/skills/final-delivery-acceptance/scripts/delivery_guard.py",
),)
POLICY_STATUS = "active_global_gate"
QUALIFICATION_CONTRACT_SHA256 = "0e24ee82c2ff68124523546e5891c39227fe4268dbc581b74a319cdde22ef411"

ACTIVATION_SCOPE = {
    "kind": "active_global_gate",
    "runtime_authority_change": True,
    "components_activated": ["acceptance_report_v2", "delivery_quality_context"],
    "legacy_track_authority": "acceptance_report_v2",
    "platform_kernel_authority": "unchanged",
    "qualification_contract_sha256": QUALIFICATION_CONTRACT_SHA256,
}

GLOBAL_GATE_TESTS = "tests.video_workflow.test_issue43_global_gate.Issue43GlobalGateTests"
POLICY_TESTS = "tests.video_workflow.test_issue43_workflow_policy.Issue43WorkflowPolicyTests"
GUARD_TESTS = "tests.video_workflow.test_issue43_active_guard.Issue43ActiveGuardTests"
MIRROR_TEST = (
    "tests.video_workflow.test_issue43_active_guard_policy."
    "Issue43ActiveGuardPolicyTests."
    "test_active_global_gate_policy_and_mirrors_are_synchronized"
)
ACCEPTANCE_TESTS = "tests.video_workflow.test_acceptance_v2.AcceptanceV2CliTests"
SPEC_GAP_TESTS = (
    "tests.video_workflow.test_issue43_spec_gap_contracts."
    "Issue43SpecGapContractTests"
)
ACTIVATION_FENCING_TESTS = (
    "tests.video_workflow.test_issue43_activation_fencing."
    "Issue43ActivationFencingTests"
)
POLICY_AUTHORITY_REFRESH_TESTS = (
    "tests.video_workflow.test_global_gate_policy_authority_refresh."
    "GlobalGatePolicyAuthorityRefreshTests"
)

RESULT_SPECS = (
    ("legacy_run_record_free_v2_pass", "positive", f"{GLOBAL_GATE_TESTS}.test_run_record_free_legacy_completes_provider_chain_and_guard_eligibility", None, None),
    ("legacy_active_guard_pass", "positive", f"{GUARD_TESTS}.test_active_guard_accepts_run_record_free_legacy_v2_authority", None, None),
    ("kernel_v2_pass", "positive", f"{ACCEPTANCE_TESTS}.test_complete_current_evidence_materializes_all_catalog_rules_and_guard_eligibility", None, None),
    ("kernel_active_guard_pass", "positive", f"{GUARD_TESTS}.test_active_guard_accepts_current_passing_v2_authority", None, None),
    ("active_global_gate_only", "positive", f"{POLICY_TESTS}.test_workflow_policy_check_accepts_current_atomic_policy_authority", None, None),
    ("mirrors_current", "positive", MIRROR_TEST, None, None),
    ("delivery_guard_runtime_mirrors_bound", "positive", f"{SPEC_GAP_TESTS}.test_exit_evidence_binds_delivery_guard_runtime_script_mirrors", None, None),
    ("v1_rejected", "negative", f"{GUARD_TESTS}.test_active_guard_rejects_v1_fallback", "acceptance_authority", "acceptance_report_v1_rejected"),
    ("stale_legacy_authority_rejected", "negative", f"{GUARD_TESTS}.test_active_guard_rejects_stale_global_gate_authority", "global_gate_authority", "global_gate_authority_stale"),
    ("incomplete_mirrors_rejected", "negative", f"{POLICY_TESTS}.test_mirror_and_policy_status_are_distinct_first_gates", "mirror_checks", "global_gate_mirror_stale"),
    ("incomplete_delivery_guard_runtime_mirror_rejected", "negative", f"{SPEC_GAP_TESTS}.test_incomplete_delivery_guard_runtime_mirror_evidence_fails_closed", "mirror_checks", "global_gate_mirror_stale"),
    ("unsupported_identity_rejected", "negative", f"{POLICY_TESTS}.test_legacy_v2_provider_rejects_unsupported_identity", "input_identity", "legacy_provider_unsupported"),
    ("contract_gap_rejected", "negative", f"{POLICY_TESTS}.test_legacy_v2_provider_rejects_contract_gap", "contract_gap", "legacy_contract_gap_blocked"),
    ("failed_atomic_member_rejected", "negative", f"{POLICY_TESTS}.test_failed_atomic_member_is_first_rejected_by_atomic_member_status", "atomic_member_status", "global_gate_atomic_member_failed"),
    ("control_store_unavailable_rejected", "negative", f"{POLICY_TESTS}.test_control_store_unavailable_corrupt_locked_and_incompatible_fail_closed", "control_store", "global_gate_control_store_unavailable"),
    ("control_store_corrupt_rejected", "negative", f"{POLICY_TESTS}.test_control_store_unavailable_corrupt_locked_and_incompatible_fail_closed", "control_store", "global_gate_control_store_corrupt"),
    ("control_store_locked_rejected", "negative", f"{POLICY_TESTS}.test_control_store_unavailable_corrupt_locked_and_incompatible_fail_closed", "control_store", "global_gate_control_store_locked"),
    ("control_store_incompatible_rejected", "negative", f"{POLICY_TESTS}.test_control_store_unavailable_corrupt_locked_and_incompatible_fail_closed", "control_store", "global_gate_control_store_incompatible"),
    ("fallback_rejected", "negative", f"{GUARD_TESTS}.test_active_guard_rejects_v1_fallback", "acceptance_authority", "acceptance_report_v1_rejected"),
    ("translation_rejected", "negative", f"{GUARD_TESTS}.test_active_guard_rejects_compatibility_translation", "acceptance_authority", "acceptance_compatibility_translation_rejected"),
    ("synthetic_legacy_run_rejected", "negative", f"{POLICY_TESTS}.test_legacy_v2_provider_rejects_synthetic_run", "input_identity", "legacy_synthetic_run_rejected"),
    ("dual_authority_rejected", "negative", f"{GUARD_TESTS}.test_active_guard_rejects_dual_authority", "acceptance_authority", "acceptance_dual_authority_rejected"),
    ("activation_reconcile_stale_publication_rejected", "negative", f"{POLICY_TESTS}.test_after_intent_reconcile_rejects_evidence_made_stale_by_later_commit", "implementation_currentness", "evidence_publication_not_current"),
    ("patch_publication_recovered", "recovery", f"{ACCEPTANCE_TESTS}.test_reconcile_rejects_changed_published_bytes_and_finishes_intact_publication", None, None),
    ("report_publication_recovered", "recovery", f"{ACCEPTANCE_TESTS}.test_reconcile_finishes_an_intact_interrupted_report_publication", None, None),
    ("patch_retry_idempotent", "recovery", f"{ACCEPTANCE_TESTS}.test_successful_patch_and_terminal_materialization_retries_are_idempotent", None, None),
    ("patch_after_control_commit_recovered_idempotent", "recovery", f"{ACCEPTANCE_TESTS}.test_patch_after_control_commit_recovers_intact_and_exact_retry_is_idempotent", None, None),
    ("report_retry_idempotent", "recovery", f"{ACCEPTANCE_TESTS}.test_successful_patch_and_terminal_materialization_retries_are_idempotent", None, None),
    ("activation_publication_recovered", "recovery", f"{POLICY_TESTS}.test_activation_interruption_reconciles_and_exact_retry_is_idempotent", None, None),
    ("activation_retry_idempotent", "recovery", f"{POLICY_TESTS}.test_activation_interruption_reconciles_and_exact_retry_is_idempotent", None, None),
    ("activation_after_control_commit_recovered_idempotent", "recovery", f"{POLICY_TESTS}.test_activation_after_control_commit_is_intact_and_exact_retry_is_idempotent", None, None),
    ("patch_writers_fenced", "fencing", f"{ACCEPTANCE_TESTS}.test_two_writers_are_fenced_at_patch_and_report_publication", None, None),
    ("report_writers_fenced", "fencing", f"{ACCEPTANCE_TESTS}.test_two_writers_are_fenced_at_patch_and_report_publication", None, None),
    ("activation_writers_fenced", "fencing", f"{ACTIVATION_FENCING_TESTS}.test_distinct_valid_activation_authorities_reach_the_cas_fence", None, None),
    (
        "policy_authority_refresh_preserves_base_global_gate",
        "positive",
        f"{POLICY_AUTHORITY_REFRESH_TESTS}.test_refresh_publishes_current_policy_and_preserves_base_authority",
        None,
        None,
    ),
    (
        "policy_authority_refresh_recovered_with_stable_base",
        "recovery",
        f"{POLICY_AUTHORITY_REFRESH_TESTS}.test_reconcile_finishes_interrupted_refresh_and_preserves_base",
        None,
        None,
    ),
    (
        "modern_legacy_adoption_pass",
        "positive",
        f"{GLOBAL_GATE_TESTS}.test_legacy_adoption_accepts_relationally_current_final_compile_report",
        None,
        None,
    ),
    (
        "modern_legacy_adoption_schema_rejected",
        "negative",
        f"{GLOBAL_GATE_TESTS}.test_legacy_adoption_rejects_invalid_final_compile_report_schema",
        "compile_provenance",
        "legacy_compile_provenance_invalid",
    ),
    (
        "modern_legacy_guard_pass",
        "positive",
        f"{GUARD_TESTS}.test_public_guard_accepts_registry_valid_modern_legacy_report",
        None,
        None,
    ),
    (
        "modern_legacy_guard_schema_rejected",
        "negative",
        f"{GUARD_TESTS}.test_public_guard_rejects_single_modern_schema_contradiction",
        "delivery_guard",
        "delivery_guard_failed",
    ),
)
QUALIFICATION_TEST_TARGETS = tuple(dict.fromkeys(target for _, _, target, _, _ in RESULT_SPECS))

COMPLETE_MODULE_RESULT_SPECS = (
    (
        "complete_acceptance_v2_module",
        "positive",
        "issue43-complete-acceptance-v2",
        "tests.video_workflow.test_acceptance_v2",
    ),
    (
        "complete_issue43_active_guard_module",
        "positive",
        "issue43-complete-active-guard",
        "tests.video_workflow.test_issue43_active_guard",
    ),
    (
        "complete_delivery_guard_module",
        "positive",
        "issue43-complete-delivery-guard",
        "test_delivery_guard.py",
    ),
    (
        "complete_policy_authority_refresh_module",
        "positive",
        "issue43-complete-policy-authority-refresh",
        "tests.video_workflow.test_global_gate_policy_authority_refresh",
    ),
    (
        "complete_issue41_legacy_final_compile_module",
        "positive",
        "issue43-complete-issue41-legacy-final-compile",
        "tests.video_workflow.test_issue41_legacy_final_compile",
    ),
    (
        "complete_issue43_global_gate_module",
        "positive",
        "issue43-complete-global-gate",
        "tests.video_workflow.test_issue43_global_gate",
    ),
    (
        "complete_guarded_final_compile_adapter_module",
        "positive",
        "issue43-complete-guarded-final-compile-adapter",
        "tests.video_workflow.test_guarded_final_compile_adapter",
    ),
    (
        "complete_rendered_text_reconciliation_module",
        "positive",
        "issue43-complete-rendered-text-reconciliation",
        "tests.video_workflow.test_rendered_text_reconciliation",
    ),
    (
        "complete_issue13_final_evidence_module",
        "positive",
        "issue43-complete-issue13-final-evidence",
        "tests.video_workflow.test_issue13_final_evidence_cli",
    ),
)

COMMANDS = (
    (
        "issue43-global-gate-tests",
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
    (
        "issue43-exit-evidence-tests",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "-v",
            "tests.video_workflow.test_issue43_exit_evidence",
        ),
        0,
    ),
    *(
        (
            command_id,
            (
                sys.executable,
                "-X",
                "utf8",
                "-B",
                "-m",
                "unittest",
                "-v",
                test_target,
            ),
            0,
        )
        for _, _, command_id, test_target in COMPLETE_MODULE_RESULT_SPECS
        if test_target != "test_delivery_guard.py"
    ),
    (
        "issue43-complete-delivery-guard",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "discover",
            "-v",
            "-s",
            ".agents/skills/final-delivery-acceptance/scripts",
            "-p",
            "test_delivery_guard.py",
        ),
        0,
    ),
)

EXPECTED_CHECKPOINTS = [
    {"name": "acceptance_report_v2_global_authority", "status": "current"},
    {"name": "legacy_acceptance_report_v1_authority", "status": "retired"},
    {"name": "platform_kernel_authority", "status": "preserved"},
]

RESULT_BINDINGS = [
    {
        "result_id": result_id,
        "result_kind": result_kind,
        "command_id": "issue43-global-gate-tests",
        "test_target": target,
        **({
            "expected_first_failing_gate": first_gate,
            "expected_error_code": error_code,
        } if result_kind == "negative" else {}),
    }
    for result_id, result_kind, target, first_gate, error_code in RESULT_SPECS
] + [
    {
        "result_id": result_id,
        "result_kind": result_kind,
        "command_id": command_id,
        "test_target": test_target,
    }
    for result_id, result_kind, command_id, test_target in COMPLETE_MODULE_RESULT_SPECS
]

RESULTS = {
    kind: [
        binding["result_id"]
        for binding in RESULT_BINDINGS
        if binding["result_kind"] == kind
    ]
    for kind in ("positive", "negative", "recovery", "fencing")
}

FIXTURE_SPECS = (
    (
        "legacy_acceptance_input_contract",
        "schemas/global-gate/legacy-acceptance-input-set.v1.schema.json",
    ),
    (
        "acceptance_input_binding_contract",
        "schemas/delivery-quality/v1/acceptance-v2-input-binding.v1.schema.json",
    ),
    (
        "acceptance_report_v2_contract",
        "schemas/delivery-quality/v1/acceptance-report-v2.v1.schema.json",
    ),
    (
        "exit_evidence_manifest_contract",
        "schemas/exit-evidence-manifest.v2.schema.json",
    ),
)

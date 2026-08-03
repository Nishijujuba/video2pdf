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
POLICY_STATUS = "active_global_gate"

ACTIVATION_SCOPE = {
    "kind": "active_global_gate",
    "runtime_authority_change": True,
    "components_activated": ["acceptance_report_v2", "delivery_quality_context"],
    "legacy_track_authority": "acceptance_report_v2",
    "platform_kernel_authority": "unchanged",
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

RESULT_SPECS = (
    ("legacy_run_record_free_v2_pass", "positive", f"{GLOBAL_GATE_TESTS}.test_legacy_adoption_materializes_a_fresh_run_record_free_input_set"),
    ("kernel_v2_pass", "positive", f"{ACCEPTANCE_TESTS}.test_complete_current_evidence_materializes_all_catalog_rules_and_guard_eligibility"),
    ("active_global_gate_only", "positive", f"{POLICY_TESTS}.test_workflow_policy_check_accepts_current_atomic_policy_authority"),
    ("mirrors_current", "positive", MIRROR_TEST),
    ("v1_rejected", "negative", f"{POLICY_TESTS}.test_required_negative_policy_results_have_stable_first_gate_codes"),
    ("stale_legacy_authority_rejected", "negative", f"{POLICY_TESTS}.test_mirror_and_policy_status_are_distinct_first_gates"),
    ("incomplete_mirrors_rejected", "negative", f"{POLICY_TESTS}.test_mirror_and_policy_status_are_distinct_first_gates"),
    ("unsupported_identity_rejected", "negative", f"{POLICY_TESTS}.test_required_negative_policy_results_have_stable_first_gate_codes"),
    ("contract_gap_rejected", "negative", f"{POLICY_TESTS}.test_required_negative_policy_results_have_stable_first_gate_codes"),
    ("failed_atomic_member_rejected", "negative", f"{POLICY_TESTS}.test_failed_atomic_member_is_first_rejected_by_atomic_member_status"),
    ("control_store_unavailable_rejected", "negative", f"{POLICY_TESTS}.test_control_store_unavailable_corrupt_locked_and_incompatible_fail_closed"),
    ("control_store_corrupt_rejected", "negative", f"{POLICY_TESTS}.test_control_store_unavailable_corrupt_locked_and_incompatible_fail_closed"),
    ("control_store_locked_rejected", "negative", f"{POLICY_TESTS}.test_control_store_unavailable_corrupt_locked_and_incompatible_fail_closed"),
    ("control_store_incompatible_rejected", "negative", f"{POLICY_TESTS}.test_control_store_unavailable_corrupt_locked_and_incompatible_fail_closed"),
    ("fallback_rejected", "negative", f"{GUARD_TESTS}.test_active_guard_rejects_v1_fallback"),
    ("translation_rejected", "negative", f"{GUARD_TESTS}.test_active_guard_rejects_compatibility_translation"),
    ("synthetic_legacy_run_rejected", "negative", f"{POLICY_TESTS}.test_required_negative_policy_results_have_stable_first_gate_codes"),
    ("dual_authority_rejected", "negative", f"{GUARD_TESTS}.test_active_guard_rejects_dual_authority"),
    ("patch_publication_recovered", "recovery", f"{ACCEPTANCE_TESTS}.test_reconcile_rejects_changed_published_bytes_and_finishes_intact_publication"),
    ("report_publication_recovered", "recovery", f"{ACCEPTANCE_TESTS}.test_reconcile_finishes_an_intact_interrupted_report_publication"),
    ("activation_publication_recovered", "recovery", f"{POLICY_TESTS}.test_activation_interruption_reconciles_and_exact_retry_is_idempotent"),
    ("activation_retry_idempotent", "recovery", f"{POLICY_TESTS}.test_activation_interruption_reconciles_and_exact_retry_is_idempotent"),
    ("activation_writers_fenced", "fencing", f"{POLICY_TESTS}.test_competing_activation_is_fenced"),
)
QUALIFICATION_TEST_TARGETS = tuple(dict.fromkeys(target for _, _, target in RESULT_SPECS))

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
    }
    for result_id, result_kind, target in RESULT_SPECS
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
        "global_gate_exit_evidence_contract",
        "schemas/global-gate/global-gate-exit-evidence.v1.schema.json",
    ),
    (
        "legacy_acceptance_input_contract",
        "schemas/global-gate/legacy-acceptance-input-set.v1.schema.json",
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

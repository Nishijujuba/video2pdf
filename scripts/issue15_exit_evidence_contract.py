from __future__ import annotations

import hashlib
import json
import sys


# Pinned by the master agent at publication time. The slice-14 base is the
# last commit before the issue-15 Batch implementation began: bb50b1c.
SLICE_BASE_COMMIT = "bb50b1ce8a0f91961d9c7077e8e130ac8eaa955f"
SLICE_NUMBER = 14
SLICE_NAME = "batch-projection-cutover"
EVIDENCE_PREFIX = "evidence/slice-14/"

ATOMIC_MEMBERS = (
    "batch_record_contract",
    "batch_item_projection_contract",
    "control_store_batch_persistence",
    "batch_projection_provider",
    "batch_cutover_authority",
    "batch_cli",
    "legacy_batch_retirement",
    "batch_skill",
    "project_instructions",
    "validators",
    "tests",
    "activation_documentation",
    "mirrors",
    "exit_evidence_schema",
    "evidence_collector",
)
ATOMIC_MEMBER_STATUS = {member: "active" for member in ATOMIC_MEMBERS}
PLATFORM_STATUSES = {"bilibili": "active_kernel", "youtube": "active_kernel"}

RESULT_SPECS = (
    (
        "batch_record_contract_pass",
        "positive",
        "tests.video_workflow.test_issue15_batch_projection.Issue15BatchProjectionTests.test_batch_record_contract_pass",
        None,
        None,
    ),
    (
        "projection_rebuild_pass",
        "positive",
        "tests.video_workflow.test_issue15_batch_projection.Issue15BatchProjectionTests.test_projection_rebuild_pass",
        None,
        None,
    ),
    (
        "guarded_delivered_only_success",
        "positive",
        "tests.video_workflow.test_issue15_batch_authority.Issue15BatchAuthorityTests.test_guarded_delivered_only_success",
        None,
        None,
    ),
    (
        "duplicate_run_rejected",
        "negative",
        "tests.video_workflow.test_issue15_batch_authority.Issue15BatchAuthorityTests.test_duplicate_run_rejected",
        "batch_authority",
        "duplicate_run_rejected",
    ),
    (
        "pdf_existence_success_rejected",
        "negative",
        "tests.video_workflow.test_issue15_batch_authority.Issue15BatchAuthorityTests.test_pdf_existence_success_rejected",
        "batch_authority",
        "pdf_existence_success_rejected",
    ),
    (
        "per_video_mutation_rejected",
        "negative",
        "tests.video_workflow.test_issue15_batch_authority.Issue15BatchAuthorityTests.test_per_video_mutation_rejected",
        "batch_authority",
        "per_video_mutation_rejected",
    ),
    (
        "reconcile_interrupted_item_creation",
        "recovery",
        "tests.video_workflow.test_issue15_batch_projection.Issue15BatchProjectionTests.test_reconcile_interrupted_item_creation",
        None,
        None,
    ),
    (
        "projection_revision_fencing",
        "fencing",
        "tests.video_workflow.test_issue15_batch_projection.Issue15BatchProjectionTests.test_projection_revision_fencing",
        None,
        None,
    ),
    (
        "fairness_group_is_batch_id",
        "fairness",
        "tests.video_workflow.test_issue15_batch_authority.Issue15BatchAuthorityTests.test_fairness_group_id_is_batch_id",
        None,
        None,
    ),
    (
        "auth_breaker_uses_resource_admission",
        "positive",
        "tests.video_workflow.test_issue15_batch_authority.Issue15BatchAuthorityTests.test_auth_breaker_flows_through_resource_admission",
        None,
        None,
    ),
    (
        "batch_cutover_activation_publishes_current_authority",
        "positive",
        "tests.video_workflow.test_issue15_batch_cutover.Issue15BatchCutoverTests.test_activate_publishes_current_batch_authority",
        None,
        None,
    ),
    (
        "batch_cutover_current_authority_is_verified",
        "fencing",
        "tests.video_workflow.test_issue15_batch_cutover.Issue15BatchCutoverTests.test_current_authority_rejects_authority_evidence_and_database_tamper",
        None,
        None,
    ),
    (
        "batch_cutover_reconcile_completes_publication",
        "recovery",
        "tests.video_workflow.test_issue15_batch_cutover.Issue15BatchCutoverTests.test_reconcile_completes_interrupted_authority_publication",
        None,
        None,
    ),
    (
        "batch_authority_refresh_advances_generation",
        "positive",
        "tests.video_workflow.test_issue15_batch_cutover.Issue15BatchCutoverTests.test_refresh_advances_generation_and_rebinds_current_prerequisites",
        None,
        None,
    ),
    (
        "batch_authority_refresh_rejects_stale_generation",
        "fencing",
        "tests.video_workflow.test_issue15_batch_cutover.Issue15BatchCutoverTests.test_refresh_rejects_a_stale_expected_generation_before_publication",
        "batch_cutover_authority",
        "batch_authority_refresh_fenced",
    ),
    (
        "batch_authority_refresh_reconciles_interrupted_publication",
        "recovery",
        "tests.video_workflow.test_issue15_batch_cutover.Issue15BatchCutoverTests.test_reconcile_completes_interrupted_authority_refresh",
        None,
        None,
    ),
    (
        "batch_authority_old_generation_binding_fails_closed",
        "fencing",
        "tests.video_workflow.test_issue15_batch_activation_integration.Issue15BatchActivationIntegrationTests.test_refresh_makes_old_batch_record_run_recover_and_status_fail_closed",
        "batch_authority_binding",
        "batch_authority_binding_stale",
    ),
    (
        "batch_plan_preactivation_closure",
        "positive",
        "tests.video_workflow.test_issue15_batch_activation_integration.Issue15BatchActivationIntegrationTests.test_plan_requires_authority_before_enumeration_or_mutation",
        None,
        None,
    ),
    (
        "batch_run_preactivation_closure",
        "positive",
        "tests.video_workflow.test_issue15_batch_activation_integration.Issue15BatchActivationIntegrationTests.test_run_rejects_missing_or_stale_binding_before_any_mutation",
        None,
        None,
    ),
)
QUALIFICATION_TEST_TARGETS = tuple(target for _, _, target, _, _ in RESULT_SPECS)
COMMANDS = (
    (
        "issue15-batch-projection-tests",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "-v",
            "tests.video_workflow.test_issue15_batch_projection",
            "tests.video_workflow.test_issue15_batch_authority",
            "tests.video_workflow.test_issue15_batch_cutover",
            "tests.video_workflow.test_issue15_batch_activation_integration",
            "tests.video_workflow.test_issue15_batch_contracts",
            "tests.video_workflow.test_issue15_control_store_batch",
            "tests.video_workflow.test_issue15_batch_cli",
            "tests.video_workflow.test_issue15_batch_policy_docs",
        ),
        0,
    ),
    (
        "issue15-exit-evidence-tests",
        (sys.executable, "-X", "utf8", "-B", "-m", "unittest", "-v", "tests.video_workflow.test_issue15_exit_evidence"),
        0,
    ),
)

# Semantic command identity: the interpreter (argv[0]) is machine-local
# execution-environment evidence, not semantic identity. The semantic identity
# of a closed qualification command is argv[1:] -- the Python flags, module,
# verbosity, and test targets. Validation of persisted command records matches
# these roles so a repository can be relocated or re-interpreted without the
# interpreter's absolute path becoming part of the identity contract.
QUALIFICATION_INTERPRETER_ROLE = "qualification_python"
QUALIFICATION_CWD_ROLE = "project_root"
SEMANTIC_ARGUMENTS = tuple(
    tuple(command_argv[1:]) for _command_id, command_argv, _exit in COMMANDS
)
RESULT_BINDINGS = [
    {
        "result_id": result_id,
        "result_kind": result_kind,
        "command_id": (
            "issue15-batch-projection-tests"
            if target.startswith(
                (
                    "tests.video_workflow.test_issue15_batch_projection",
                    "tests.video_workflow.test_issue15_batch_authority",
                    "tests.video_workflow.test_issue15_batch_cutover",
                    "tests.video_workflow.test_issue15_batch_activation_integration",
                    "tests.video_workflow.test_issue15_batch_contracts",
                    "tests.video_workflow.test_issue15_control_store_batch",
                    "tests.video_workflow.test_issue15_batch_cli",
                    "tests.video_workflow.test_issue15_batch_policy_docs",
                )
            )
            else "issue15-exit-evidence-tests"
        ),
        "test_target": target,
        **(
            {"expected_first_failing_gate": gate, "expected_error_code": code}
            if gate is not None and code is not None
            else {}
        ),
    }
    for (result_id, result_kind, target, gate, code) in RESULT_SPECS
]
RESULTS = {
    kind: [item["result_id"] for item in RESULT_BINDINGS if item["result_kind"] == kind]
    for kind in ("positive", "negative", "recovery", "fencing", "fairness")
}
EXPECTED_CHECKPOINTS = [
    {"name": "batch_projection", "status": "current"},
    {"name": "bilibili_platform_kernel", "status": "preserved"},
    {"name": "youtube_platform_kernel", "status": "preserved"},
    {"name": "active_global_gate", "status": "preserved"},
]
FIXTURE_SPECS = (
    ("batch_record_contract", "schemas/video-workflow/v5/batch-record.v1.schema.json"),
    ("batch_item_projection_contract", "schemas/video-workflow/v5/batch-item-projection.v1.schema.json"),
    ("batch_record_positive_fixture", "tests/video_workflow/fixtures/contracts/batch-record.valid.json"),
    ("batch_record_negative_fixture", "tests/video_workflow/fixtures/contracts/batch-record.invalid.json"),
    ("batch_item_projection_positive_fixture", "tests/video_workflow/fixtures/contracts/batch-item-projection.valid.json"),
    ("batch_item_projection_negative_fixture", "tests/video_workflow/fixtures/contracts/batch-item-projection.invalid.json"),
    ("exit_evidence_manifest_contract", "schemas/exit-evidence-manifest.v2.schema.json"),
    ("slice14_positive_fixture", "tests/video_workflow/fixtures/exit_evidence/slice14.valid.json"),
    ("slice14_single_contradiction_fixture", "tests/video_workflow/fixtures/exit_evidence/slice14.invalid.json"),
)
MIRROR_SPECS = (
    (".agents/skills/bilibili-batch-render-pdf/SKILL.md", ".claude/skills/bilibili-batch-render-pdf/SKILL.md"),
)
POLICY_STATUS = "active_global_gate"

QUALIFICATION_CONTRACT_SHA256 = hashlib.sha256(
    (json.dumps(RESULT_BINDINGS, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
).hexdigest()
ACTIVATION_SCOPE = {
    "kind": "batch_cutover",
    "runtime_authority_change": True,
    "components_activated": [
        "batch_record_contract",
        "batch_item_projection_contract",
        "control_store_batch_persistence",
        "batch_projection_provider",
        "batch_cutover_authority",
        "batch_cli",
        "batch_skill",
    ],
    "global_gate_authority": "unchanged",
    "platform_kernel_authority": "unchanged",
    "qualification_contract_sha256": QUALIFICATION_CONTRACT_SHA256,
}

from __future__ import annotations

import sys


SLICE_BASE_COMMIT = "654362017fa974946fb252af5311868bb47efcf0"
SLICE_NUMBER = 4
SLICE_NAME = "production-source-acquisition"
EVIDENCE_PREFIX = "evidence/slice-04/"

EXPECTED_CHECKPOINTS = [
    {"name": "source_candidates_ready", "status": "current"},
    {"name": "source_acquisition_decision_ready", "status": "current"},
    {"name": "source_ready", "status": "current"},
]

PRODUCTION_SOURCE_TEST_TARGETS = (
    "tests.video_workflow.test_production_source_acquisition.ProductionSourceAcquisitionTests.test_source_identity_and_content_version_are_distinct_authorities",
    "tests.video_workflow.test_production_source_acquisition.ProductionSourceAcquisitionTests.test_english_subtitle_policy_and_explicit_whisper_fallback_are_bounded",
    "tests.video_workflow.test_production_source_acquisition.ProductionSourceAcquisitionTests.test_downstream_reader_rejects_noncurrent_validated_source_package",
    "tests.video_workflow.test_production_source_acquisition_hardening.ProductionSourceAcquisitionHardeningTests.test_fresh_download_and_whisper_launch_only_through_resource_admission",
    "tests.video_workflow.test_production_source_acquisition_hardening.ProductionSourceAcquisitionHardeningTests.test_cookie_rejection_is_user_input_and_opens_only_platform_breaker",
    "tests.video_workflow.test_source_publication.SourcePublicationTests.test_publication_recovers_every_fault_and_commits_current_source",
    "tests.video_workflow.test_production_source_acquisition_hardening.ProductionSourceAcquisitionHardeningTests.test_source_reopen_transitively_stales_dependents_and_recovers_fault_boundaries",
    "tests.video_workflow.test_production_source_acquisition_hardening.ProductionSourceAcquisitionHardeningTests.test_cookie_rejection_persists_run_blocker_and_scoped_breaker",
)

PRODUCTION_TASK_TEST_TARGETS = (
    "tests.video_workflow.test_production_source_tasks.ProductionSourceTaskTests.test_three_stage_source_tasks_complete_through_resource_admission",
    "tests.video_workflow.test_production_source_tasks.ProductionSourceTaskTests.test_provider_inventory_authenticates_the_exact_candidate_staging_set",
    "tests.video_workflow.test_production_source_tasks.ProductionSourceTaskTests.test_whisper_output_uses_a_strict_utf8_lf_srt_byte_contract",
)

SOURCE_PACKAGE_TEST_TARGETS = (
    "tests.video_workflow.test_source_package.SourcePackageTests.test_fresh_materializer_binds_controls_and_canonicalizes_every_artifact",
    "tests.video_workflow.test_source_package.SourcePackageTests.test_verified_import_skips_agent_and_preserves_content_version",
    "tests.video_workflow.test_source_package.SourcePackageTests.test_materializer_rejects_binding_drift_and_agent_mechanical_fields",
)

VERIFIED_IMPORT_CLI_TEST_TARGETS = (
    "tests.video_workflow.test_verified_source_import_cli.VerifiedSourceImportCliTests.test_public_source_import_creates_current_v2_package_without_semantic_task",
    "tests.video_workflow.test_verified_source_import_cli.VerifiedSourceImportCliTests.test_public_source_import_rejects_a_noncurrent_prior_package",
)

SOURCE_LIVE_SMOKE_TEST_TARGETS = (
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_deterministic_locator_bootstrap_never_calls_an_adapter_or_runner",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_recorded_live_provider_only_starts_inside_resource_admission",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_cookie_rejection_from_admitted_provider_persists_run_blocker",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_provider_lease_is_released_when_candidate_materialization_fails",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_semantic_lease_is_released_when_judgment_callback_fails",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_semantic_lease_is_released_when_judgment_output_is_missing",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_whisper_lease_is_released_when_provider_fails",
)

SOURCE_PUBLICATION_CONTROL_STORE_TEST_TARGETS = (
    "tests.video_workflow.test_source_publication_control_store.SourcePublicationControlStoreTests.test_journal_binding_is_single_assignment_and_state_machine_commits_chain",
    "tests.video_workflow.test_source_publication_control_store.SourcePublicationControlStoreTests.test_nonterminal_publication_owns_the_single_run_promotion_slot",
)

SOURCE_PUBLICATION_INTEGRATION_TEST_TARGETS = (
    "tests.video_workflow.test_source_publication_integration.SourcePublicationIntegrationTests.test_kernel_finalizer_and_reconciler_commit_real_v9_publication",
)

SOURCE_REOPEN_INTEGRATION_TEST_TARGETS = (
    "tests.video_workflow.test_source_reopen_integration.SourceReopenIntegrationTests.test_reconcile_commits_reopened_v3_run_to_file_backed_authority",
)

PLATFORM_ADAPTER_TEST_TARGETS = (
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_production_adapters_share_one_runtime_checkable_interface",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_bilibili_recording_is_cookie_first_and_materializes_canonical_outputs",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_youtube_recording_puts_node_on_every_ytdlp_command",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_cookie_rejection_is_a_closed_user_input_classification_without_secret_leak",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_recorded_runner_rejects_an_unconsumed_or_out_of_order_recording",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_subprocess_runner_redacts_secret_arguments_cookie_lines_and_values",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_youtube_translated_subtitle_filename_binds_requested_track",
)

REVIEW_REPAIR_PRODUCTION_TEST_TARGETS = (
    "tests.video_workflow.test_provider_secret_snapshot.ProviderSecretSnapshotTests.test_runner_redacts_pre_execution_cookie_after_provider_rewrites_jar",
    "tests.video_workflow.test_validated_source_gate.ValidatedSourceGateTests.test_gate_rejects_an_extra_source_file",
    "tests.video_workflow.test_validated_source_gate.ValidatedSourceGateTests.test_gate_rejects_a_hardlinked_source_artifact",
    "tests.video_workflow.test_source_publication_preintent.SourcePublicationPreIntentTests.test_candidate_write_is_fenced_and_changed_timestamp_replays",
    "tests.video_workflow.test_source_publication_preintent.SourcePublicationPreIntentTests.test_partial_candidate_write_resumes_from_frozen_intent",
    "tests.video_workflow.test_source_reopen_contract.SourceReopenJournalContractTests.test_malformed_and_unknown_version_journals_fail_recovery_closed",
    "tests.video_workflow.test_source_reopen_contract.SourceReopenJournalContractTests.test_batch_preflight_rejects_candidate_drift_before_any_move",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_whisper_lease_is_released_when_transcript_output_is_missing",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_whisper_lease_is_released_when_transcript_output_drifts",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_whisper_lease_is_released_when_launch_token_is_ambiguous",
    "tests.video_workflow.test_source_live_smoke.SourceLiveSmokeTests.test_smoke_case_rejects_an_ancestor_link_outside_its_boundary",
    "tests.video_workflow.test_source_candidates.SourceCandidateTests.test_candidate_target_parent_junction_fails_before_any_publication",
    "tests.video_workflow.test_source_package.SourcePackageTests.test_materializer_rejects_descendant_junction_before_writing_outside_run",
    "tests.video_workflow.test_source_package.SourcePackageTests.test_materializer_rejects_a_hardlinked_candidate_file",
    "tests.video_workflow.test_source_publication.SourcePublicationTests.test_publication_rejects_canonical_parent_junction_before_external_write",
    "tests.video_workflow.test_verified_source_import_cli.VerifiedSourceImportCliTests.test_public_source_import_rejects_a_linked_prior_source_descendant",
    "tests.video_workflow.test_provider_candidate_promotion.ProviderCandidatePromotionTests.test_reclaimed_provider_attempt_promotes_only_the_replacement_candidate_set",
    "tests.video_workflow.test_provider_candidate_promotion.ProviderCandidatePromotionTests.test_completion_rejects_a_hardlinked_candidate_file",
    "tests.video_workflow.test_task_promotion_hardening.TaskFailClosedTests.test_hardlinked_required_output_is_rejected_before_completion",
    "tests.video_workflow.test_production_source_tasks.ProductionSourceTaskTests.test_cookie_rejected_run_resolves_after_platform_breaker_closes",
    "tests.video_workflow.test_production_source_tasks.ProductionSourceTaskTests.test_source_blocker_resolution_cli_closes_the_user_input_transition",
    "tests.video_workflow.test_production_source_tasks.ProductionSourceTaskTests.test_credential_resolution_reconcile_requires_bound_evidence",
    "tests.video_workflow.test_production_source_tasks.ProductionSourceTaskTests.test_credential_resolution_recovery_rejects_missing_or_drifted_evidence",
    "tests.video_workflow.test_production_source_tasks.ProductionSourceTaskTests.test_ready_source_reopens_and_republishes_changed_candidate_content",
    "tests.video_workflow.test_production_source_drift_recovery.ProductionSourceDriftRecoveryTests.test_reconcile_commits_a_contract_valid_stale_v3_run",
    "tests.video_workflow.test_production_artifact_plan_honesty.ProductionArtifactPlanHonestyTests.test_run_plan_excludes_circular_transaction_journal_authority",
)

REVIEW_REPAIR_ADAPTER_TEST_TARGETS = (
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_recorded_provider_manifest_is_closed_and_versioned",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_recorded_provider_rejects_declared_stdio_hash_drift",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_bilibili_p2_probe_selection_drives_the_acquisition_command",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_bilibili_acquire_rejects_a_different_item_before_provider_launch",
    "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_youtube_acquire_rejects_a_different_item_before_provider_launch",
)

EXIT_EVIDENCE_TEST_TARGETS = (
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_slice4_collector_binds_report_to_validated_source_manifest",
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_slice4_collector_scans_every_blob_before_first_write",
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_slice4_contract_registers_closed_commands_and_platform_smokes",
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_slice4_exit_evidence_command_covers_closed_test_class",
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_slice4_fault_markers_use_generic_command_bindings",
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_slice4_schema_requires_exact_platform_smoke_set",
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_slice4_semantics_bind_closed_smokes_results_and_faults",
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_slice4_smoke_runner_only_delegates_to_product_cli",
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_slice4_closed_command_covers_independent_review_repairs",
    "tests.video_workflow.test_issue7_exit_evidence.Slice4ExitEvidenceTests.test_prior_slice_fixture_bindings_use_their_implementation_commit",
)

PLATFORM_SMOKE_SPECS = (
    {
        "platform": "bilibili",
        "command_id": "slice4-bilibili-live-smoke",
        "credential_profile": "bilibili-project-cookie",
        "case_path": "tests/video_workflow/fixtures/providers/bilibili/live-smoke-case.json",
        "work_root": "workspace/待删除/slice4-live-smoke/bilibili",
        "source_manifest_path": "evidence/slice-04/smokes/bilibili.source-manifest.json",
        "sanitized_log_path": "evidence/slice-04/logs/slice4-bilibili-live-smoke.log",
    },
    {
        "platform": "youtube",
        "command_id": "slice4-youtube-live-smoke",
        "credential_profile": "youtube-project-cookie",
        "case_path": "tests/video_workflow/fixtures/providers/youtube/live-smoke-case.json",
        "work_root": "workspace/待删除/slice4-live-smoke/youtube",
        "source_manifest_path": "evidence/slice-04/smokes/youtube.source-manifest.json",
        "sanitized_log_path": "evidence/slice-04/logs/slice4-youtube-live-smoke.log",
    },
)


def _smoke_command(spec: dict[str, str]) -> tuple[str, ...]:
    return (
        sys.executable,
        "-X",
        "utf8",
        "-B",
        "scripts/run_slice4_platform_smoke.py",
        "--spec",
        spec["case_path"],
        "--credential-profile",
        spec["credential_profile"],
        "--work-root",
        spec["work_root"],
    )


COMMANDS = (
    (
        "slice0-regression",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "tests.video_workflow.test_legacy_baseline",
        ),
    ),
    (
        "slice4-contracts",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "scripts/video_workflow.py",
            "contracts-check",
        ),
    ),
    (
        "slice1-regression",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "tests.video_workflow.test_source_ready_tracer",
            "tests.video_workflow.test_source_ready_hardening",
            "tests.video_workflow.test_issue4_gate4",
            "tests.video_workflow.test_issue4_gate7",
        ),
    ),
    (
        "slice2-regression",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "tests.video_workflow.test_task_promotion",
            "tests.video_workflow.test_task_promotion_hardening",
            "tests.video_workflow.test_issue5_review_repairs",
            "tests.video_workflow.test_control_store_transaction_scope",
        ),
    ),
    (
        "slice3-regression",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "tests.video_workflow.test_resource_admission",
            "tests.video_workflow.test_resource_admission_multiprocess",
            "tests.video_workflow.test_resource_control_store_integrity",
            "tests.video_workflow.test_control_store_recovery",
        ),
    ),
    (
        "slice4-production-source-acquisition",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "-v",
            *PRODUCTION_SOURCE_TEST_TARGETS,
            *PRODUCTION_TASK_TEST_TARGETS,
            *SOURCE_PACKAGE_TEST_TARGETS,
            *VERIFIED_IMPORT_CLI_TEST_TARGETS,
            *SOURCE_LIVE_SMOKE_TEST_TARGETS,
            *SOURCE_PUBLICATION_CONTROL_STORE_TEST_TARGETS,
            *SOURCE_PUBLICATION_INTEGRATION_TEST_TARGETS,
            *SOURCE_REOPEN_INTEGRATION_TEST_TARGETS,
            *REVIEW_REPAIR_PRODUCTION_TEST_TARGETS,
        ),
    ),
    (
        "slice4-platform-adapters",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "-v",
            *PLATFORM_ADAPTER_TEST_TARGETS,
            *REVIEW_REPAIR_ADAPTER_TEST_TARGETS,
        ),
    ),
    (
        "slice4-exit-evidence-contracts",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "unittest",
            "-v",
            *EXIT_EVIDENCE_TEST_TARGETS,
        ),
    ),
    (
        "slice3-exit-evidence",
        (
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "scripts/validate_slice_exit_evidence.py",
            "evidence/slice-03/exit-evidence-manifest.json",
        ),
    ),
    ("slice4-bilibili-live-smoke", _smoke_command(PLATFORM_SMOKE_SPECS[0])),
    ("slice4-youtube-live-smoke", _smoke_command(PLATFORM_SMOKE_SPECS[1])),
    (
        "slice4-syntax",
        (
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
                "pathlib.Path('scripts/slice4_exit_evidence_contract.py'),"
                "pathlib.Path('scripts/collect_slice4_exit_evidence.py'),"
                "pathlib.Path('scripts/run_slice4_platform_smoke.py'),"
                "pathlib.Path('tests/video_workflow/test_issue7_exit_evidence.py')];"
                "[ast.parse(x.read_text(encoding='utf-8'),filename=str(x)) for x in p];"
                "print(f'AST_OK {len(p)}')"
            ),
        ),
    ),
    (
        "slice4-diff-check",
        ("git", "diff", "--check", f"{SLICE_BASE_COMMIT}...HEAD"),
    ),
)

# The marker authority is command-specific. The validator uses this mapping for
# every Slice instead of assuming a single Resource Admission command.
FAULT_POINT_BINDINGS = (
    *(
        {"fault_point": point, "command_id": "slice4-production-source-acquisition"}
        for point in (
            "after_source_publication_intent_prepared",
            "after_source_publication_journal_written",
            "after_source_publication_journal_bound",
            "after_prior_source_preserved",
            "after_source_tree_published",
            "after_source_files_state_commit",
            "after_source_run_record_commit_marker",
            "after_source_record_state_commit",
            "before_source_publication_intent_commit",
            "after_source_publication_intent_commit",
            "after_reopen_prepared",
            "after_reopen_source_preserved",
            "after_reopen_run_record_commit",
        )
    ),
)
FAULT_POINTS = tuple(item["fault_point"] for item in FAULT_POINT_BINDINGS)

RESULTS = {
    "positive": [
        "platform_adapters_share_one_neutral_interface",
        "recorded_bilibili_acquisition_is_deterministic",
        "recorded_youtube_acquisition_is_deterministic",
        "source_identity_and_content_version_are_distinct",
        "english_subtitle_selection_and_whisper_fallback_are_bounded",
        "provider_semantic_and_whisper_tasks_complete_through_admission",
        "validated_source_package_is_script_materialized",
        "verified_import_preserves_content_authority_without_agent_judgment",
        "deterministic_locator_defers_provider_launch_until_run_admission",
        "source_publication_authority_is_durable",
        "youtube_translated_subtitle_outputs_bind_deterministically",
        "public_verified_import_reaches_current_source_ready_without_semantic_task",
    ],
    "negative": [
        "cookie_rejection_is_scoped_and_secret_free",
        "recorded_provider_order_mismatch_fails_closed",
        "provider_output_and_exit_evidence_are_secret_free",
        "noncurrent_validated_source_package_is_rejected",
        "provider_candidate_staging_drift_is_rejected",
        "agent_mechanical_source_fields_are_rejected",
        "cookie_rejection_opens_only_the_platform_breaker",
        "public_verified_import_rejects_noncurrent_prior_package",
    ],
    "fencing": [
        "download_and_whisper_launches_require_resource_admission",
        "recorded_provider_commands_start_only_inside_admission",
        "source_publication_owns_the_single_run_promotion_slot",
    ],
    "restart": [
        "source_publication_retry_is_idempotent",
        "source_reopen_resumes_from_every_persistent_boundary",
    ],
    "recovery": [
        "source_publication_faults_recover",
        "source_reopen_recovers_without_stale_generation",
    ],
}


def _binding(
    result_id: str, result_kind: str, command_id: str, test_target: str
) -> dict[str, str]:
    return {
        "result_id": result_id,
        "result_kind": result_kind,
        "command_id": command_id,
        "test_target": test_target,
    }


RESULT_BINDINGS = [
    _binding(RESULTS["positive"][0], "positive", "slice4-platform-adapters", PLATFORM_ADAPTER_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][1], "positive", "slice4-platform-adapters", PLATFORM_ADAPTER_TEST_TARGETS[1]),
    _binding(RESULTS["positive"][2], "positive", "slice4-platform-adapters", PLATFORM_ADAPTER_TEST_TARGETS[2]),
    _binding(RESULTS["positive"][3], "positive", "slice4-production-source-acquisition", PRODUCTION_SOURCE_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][4], "positive", "slice4-production-source-acquisition", PRODUCTION_SOURCE_TEST_TARGETS[1]),
    _binding(RESULTS["positive"][5], "positive", "slice4-production-source-acquisition", PRODUCTION_TASK_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][6], "positive", "slice4-production-source-acquisition", SOURCE_PACKAGE_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][7], "positive", "slice4-production-source-acquisition", SOURCE_PACKAGE_TEST_TARGETS[1]),
    _binding(RESULTS["positive"][8], "positive", "slice4-production-source-acquisition", SOURCE_LIVE_SMOKE_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][9], "positive", "slice4-production-source-acquisition", SOURCE_PUBLICATION_INTEGRATION_TEST_TARGETS[0]),
    _binding(RESULTS["positive"][10], "positive", "slice4-platform-adapters", PLATFORM_ADAPTER_TEST_TARGETS[6]),
    _binding(RESULTS["positive"][11], "positive", "slice4-production-source-acquisition", VERIFIED_IMPORT_CLI_TEST_TARGETS[0]),
    _binding(RESULTS["negative"][0], "negative", "slice4-platform-adapters", PLATFORM_ADAPTER_TEST_TARGETS[3]),
    _binding(RESULTS["negative"][1], "negative", "slice4-platform-adapters", PLATFORM_ADAPTER_TEST_TARGETS[4]),
    _binding(RESULTS["negative"][2], "negative", "slice4-platform-adapters", PLATFORM_ADAPTER_TEST_TARGETS[5]),
    _binding(RESULTS["negative"][3], "negative", "slice4-production-source-acquisition", PRODUCTION_SOURCE_TEST_TARGETS[2]),
    _binding(RESULTS["negative"][4], "negative", "slice4-production-source-acquisition", PRODUCTION_TASK_TEST_TARGETS[1]),
    _binding(RESULTS["negative"][5], "negative", "slice4-production-source-acquisition", SOURCE_PACKAGE_TEST_TARGETS[2]),
    _binding(RESULTS["negative"][6], "negative", "slice4-production-source-acquisition", SOURCE_LIVE_SMOKE_TEST_TARGETS[2]),
    _binding(RESULTS["negative"][7], "negative", "slice4-production-source-acquisition", VERIFIED_IMPORT_CLI_TEST_TARGETS[1]),
    _binding(RESULTS["fencing"][0], "fencing", "slice4-production-source-acquisition", PRODUCTION_SOURCE_TEST_TARGETS[3]),
    _binding(RESULTS["fencing"][1], "fencing", "slice4-production-source-acquisition", SOURCE_LIVE_SMOKE_TEST_TARGETS[1]),
    _binding(RESULTS["fencing"][2], "fencing", "slice4-production-source-acquisition", SOURCE_PUBLICATION_CONTROL_STORE_TEST_TARGETS[1]),
    _binding(RESULTS["restart"][0], "restart", "slice4-production-source-acquisition", PRODUCTION_SOURCE_TEST_TARGETS[5]),
    _binding(RESULTS["restart"][1], "restart", "slice4-production-source-acquisition", PRODUCTION_SOURCE_TEST_TARGETS[6]),
    _binding(RESULTS["recovery"][0], "recovery", "slice4-production-source-acquisition", PRODUCTION_SOURCE_TEST_TARGETS[5]),
    _binding(RESULTS["recovery"][1], "recovery", "slice4-production-source-acquisition", SOURCE_REOPEN_INTEGRATION_TEST_TARGETS[0]),
]

FIXTURE_SPECS = (
    (
        "recorded_provider_fixture_package",
        "tests/video_workflow/fixtures/providers/bilibili/fresh-download/recording.json",
    ),
    (
        "recorded_provider_fixture_package",
        "tests/video_workflow/fixtures/providers/bilibili/cookie-rejected/recording.json",
    ),
    (
        "recorded_provider_fixture_package",
        "tests/video_workflow/fixtures/providers/youtube/fresh-download/recording.json",
    ),
    (
        "positive_schema_fixture",
        "tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice4.valid.json",
    ),
    (
        "negative_schema_fixture",
        "tests/video_workflow/fixtures/exit_evidence_manifest.v2.slice4.missing-platform-smoke.invalid.json",
    ),
)

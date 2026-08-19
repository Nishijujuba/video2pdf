from __future__ import annotations

import copy
import json
import importlib.util
from pathlib import Path
import sys
import unittest
import uuid
from unittest import mock

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json"
FIXTURE_ROOT = PROJECT_ROOT / "tests/video_workflow/fixtures"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_script(name: str, relative_path: str):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Slice4ExitEvidenceTests(unittest.TestCase):
    def contract(self):
        return load_script(
            f"slice4_evidence_contract_{uuid.uuid4().hex}",
            "scripts/slice4_exit_evidence_contract.py",
        )

    def validator(self):
        return load_script(
            f"slice4_evidence_validator_{uuid.uuid4().hex}",
            "scripts/validate_slice_exit_evidence.py",
        )

    def collector(self):
        return load_script(
            f"slice4_evidence_collector_{uuid.uuid4().hex}",
            "scripts/collect_slice4_exit_evidence.py",
        )

    def smoke_runner(self):
        return load_script(
            f"slice4_smoke_runner_{uuid.uuid4().hex}",
            "scripts/run_slice4_platform_smoke.py",
        )

    def semantic_manifest(self) -> dict:
        contract = self.contract()
        manifest = read_json(
            FIXTURE_ROOT / "exit_evidence_manifest.v2.slice4.valid.json"
        )
        manifest["commands"] = [
            {
                "test_id": test_id,
                "command": list(command),
                "expected_exit_code": 0,
                "actual_exit_code": 0,
                "log": {
                    "role": "command_log",
                    "path": f"evidence/slice-04/logs/{test_id}.log",
                    "sha256": "0" * 64,
                },
                "conforms": True,
            }
            for test_id, command in contract.COMMANDS
        ]
        manifest["expected_checkpoints"] = copy.deepcopy(
            contract.EXPECTED_CHECKPOINTS
        )
        manifest["fixtures"] = [
            {"role": role, "path": path, "sha256": "0" * 64}
            for role, path in contract.FIXTURE_SPECS
        ]
        manifest["results"] = copy.deepcopy(contract.RESULTS)
        manifest["result_bindings"] = copy.deepcopy(contract.RESULT_BINDINGS)
        manifest["fault_points"] = list(contract.FAULT_POINTS)
        command_by_id = {
            command["test_id"]: command for command in manifest["commands"]
        }
        for smoke, spec in zip(
            manifest["platform_smokes"], contract.PLATFORM_SMOKE_SPECS, strict=True
        ):
            smoke["command_id"] = spec["command_id"]
            smoke["adapter_id"] = spec["platform"]
            smoke["source_manifest"]["path"] = spec["source_manifest_path"]
            smoke["sanitized_log"] = copy.deepcopy(
                command_by_id[spec["command_id"]]["log"]
            )
            smoke["sanitized_log"].pop("role")
            smoke["sanitized_log"]["no_secret_scan"] = "pass"
            smoke["target_checkpoint"]["evidence_sha256"] = smoke[
                "source_manifest"
            ]["sha256"]
        return manifest

    def test_slice4_schema_requires_exact_platform_smoke_set(self) -> None:
        validator = Draft202012Validator(read_json(SCHEMA_PATH))
        valid = read_json(
            FIXTURE_ROOT / "exit_evidence_manifest.v2.slice4.valid.json"
        )
        missing = read_json(
            FIXTURE_ROOT
            / "exit_evidence_manifest.v2.slice4.missing-platform-smoke.invalid.json"
        )

        self.assertEqual(list(validator.iter_errors(valid)), [])
        errors = list(validator.iter_errors(missing))
        self.assertTrue(errors)
        self.assertIn("platform_smokes", " ".join(str(error) for error in errors))

    def test_slice4_contract_registers_closed_commands_and_platform_smokes(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_acquisition import (
            SOURCE_REOPEN_FAULT_POINTS,
        )
        from video2pdf_workflow_kernel.source_publication import (
            SOURCE_PUBLICATION_FAULT_POINTS,
        )

        contract = self.contract()

        self.assertEqual(contract.SLICE_NUMBER, 4)
        self.assertEqual(contract.SLICE_NAME, "production-source-acquisition")
        self.assertEqual(
            contract.SLICE_BASE_COMMIT,
            "654362017fa974946fb252af5311868bb47efcf0",
        )
        self.assertEqual(
            [test_id for test_id, _ in contract.COMMANDS],
            [
                "slice0-regression",
                "slice4-contracts",
                "slice1-regression",
                "slice2-regression",
                "slice3-regression",
                "slice4-production-source-acquisition",
                "slice4-platform-adapters",
                "slice4-exit-evidence-contracts",
                "slice3-exit-evidence",
                "slice4-bilibili-live-smoke",
                "slice4-youtube-live-smoke",
                "slice4-syntax",
                "slice4-diff-check",
            ],
        )
        self.assertEqual(
            [spec["platform"] for spec in contract.PLATFORM_SMOKE_SPECS],
            ["bilibili", "youtube"],
        )
        self.assertEqual(
            [spec["command_id"] for spec in contract.PLATFORM_SMOKE_SPECS],
            ["slice4-bilibili-live-smoke", "slice4-youtube-live-smoke"],
        )
        self.assertEqual(
            contract.EXPECTED_CHECKPOINTS,
            [
                {"name": "source_candidates_ready", "status": "current"},
                {
                    "name": "source_acquisition_decision_ready",
                    "status": "current",
                },
                {"name": "source_ready", "status": "current"},
            ],
        )
        for _, command in contract.COMMANDS[:-1]:
            self.assertEqual(command[0], sys.executable)
        self.assertEqual(contract.COMMANDS[-1][1][0], "git")
        self.assertEqual(
            contract.PRODUCTION_SOURCE_TEST_TARGETS[3:],
            (
                "tests.video_workflow.test_production_source_acquisition_hardening.ProductionSourceAcquisitionHardeningTests.test_fresh_download_and_whisper_launch_only_through_resource_admission",
                "tests.video_workflow.test_production_source_acquisition_hardening.ProductionSourceAcquisitionHardeningTests.test_cookie_rejection_is_user_input_and_opens_only_platform_breaker",
                "tests.video_workflow.test_source_publication.SourcePublicationTests.test_publication_recovers_every_fault_and_commits_current_source",
                "tests.video_workflow.test_production_source_acquisition_hardening.ProductionSourceAcquisitionHardeningTests.test_source_reopen_transitively_stales_dependents_and_recovers_fault_boundaries",
                "tests.video_workflow.test_production_source_acquisition_hardening.ProductionSourceAcquisitionHardeningTests.test_cookie_rejection_persists_run_blocker_and_scoped_breaker",
            ),
        )
        self.assertIn(
            "tests.video_workflow.test_production_source_tasks.ProductionSourceTaskTests.test_three_stage_source_tasks_complete_through_resource_admission",
            contract.COMMANDS[5][1],
        )
        self.assertIn(
            "tests.video_workflow.test_source_package.SourcePackageTests.test_verified_import_skips_agent_and_preserves_content_version",
            contract.COMMANDS[5][1],
        )
        self.assertIn(
            "tests.video_workflow.test_source_publication_integration.SourcePublicationIntegrationTests.test_kernel_finalizer_and_reconciler_commit_real_v9_publication",
            contract.COMMANDS[5][1],
        )
        self.assertIn(
            "tests.video_workflow.test_source_reopen_integration.SourceReopenIntegrationTests.test_reconcile_commits_reopened_v3_run_to_file_backed_authority",
            contract.COMMANDS[5][1],
        )
        self.assertEqual(
            set(contract.FAULT_POINTS),
            set(SOURCE_PUBLICATION_FAULT_POINTS) | set(SOURCE_REOPEN_FAULT_POINTS),
        )

    def test_slice4_semantics_bind_closed_smokes_results_and_faults(self) -> None:
        validator = self.validator()
        manifest = self.semantic_manifest()
        validator.validate_semantics(manifest)

        secret_argv = copy.deepcopy(manifest)
        secret_argv["platform_smokes"][0]["command_argv_redacted"][4] = (
            "C:/Users/example/private-cookie.txt"
        )
        with self.assertRaisesRegex(
            validator.EvidenceError, "redacted command argv"
        ):
            validator.validate_semantics(secret_argv)

        wrong_platform = copy.deepcopy(manifest)
        wrong_platform["platform_smokes"][0]["adapter_id"] = "youtube"
        with self.assertRaisesRegex(
            validator.EvidenceError, "platform smoke"
        ):
            validator.validate_semantics(wrong_platform)

        wrong_identity = copy.deepcopy(manifest)
        wrong_identity["platform_smokes"][0]["source_manifest"]["source_identity"] = (
            "0" * 64
        )
        with self.assertRaisesRegex(
            validator.EvidenceError, "platform smoke"
        ):
            validator.validate_semantics(wrong_identity)

    def test_slice4_fault_markers_use_generic_command_bindings(self) -> None:
        validator = self.validator()
        contract = self.contract()
        root = PROJECT_ROOT / "workspace/待删除/slice4-fault-tests" / uuid.uuid4().hex
        root.mkdir(parents=True, exist_ok=False)
        log = root / "source-acquisition.log"
        relative = log.relative_to(PROJECT_ROOT).as_posix()
        implementation_commit = "a" * 40
        manifest = {
            "slice": {"number": 4, "name": contract.SLICE_NAME},
            "slice_base_commit": contract.SLICE_BASE_COMMIT,
            "implementation_commit": implementation_commit,
            "fault_points": list(contract.FAULT_POINTS),
            "commands": [
                {
                    "test_id": "slice4-production-source-acquisition",
                    "log": {"path": relative},
                }
            ],
        }
        marker = f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n"
        fault_lines = "".join(
            f"EVIDENCE_FAULT_POINT: {point}\n" for point in contract.FAULT_POINTS
        )
        log.write_text(marker + fault_lines, encoding="utf-8")
        validator.validate_command_log_provenance(manifest)

        log.write_text(marker + fault_lines.rsplit("\n", 2)[0] + "\n", encoding="utf-8")
        with self.assertRaisesRegex(validator.EvidenceError, "fault point provenance"):
            validator.validate_command_log_provenance(manifest)

    def test_slice4_collector_scans_every_blob_before_first_write(self) -> None:
        collector = self.collector()
        pending = {
            PROJECT_ROOT / "evidence/slice-04/logs/one.log": b"safe",
            PROJECT_ROOT / "evidence/slice-04/logs/two.log": b"Cookie: secret-value",
        }
        writer = mock.Mock()

        with self.assertRaisesRegex(collector.SecretExposureError, "secret"):
            collector.publish_evidence_blobs(
                pending,
                sensitive_values=(b"secret-value",),
                writer=writer,
            )

        writer.assert_not_called()

    def test_slice4_collector_binds_report_to_validated_source_manifest(self) -> None:
        collector = self.collector()
        source_manifest = read_json(
            FIXTURE_ROOT / "contracts/source-manifest.v2.valid.json"
        )
        path = (
            PROJECT_ROOT
            / "workspace/待删除/slice4-source-manifest-tests"
            / uuid.uuid4().hex
            / "source-manifest.json"
        )
        path.parent.mkdir(parents=True, exist_ok=False)
        raw = (
            json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        path.write_bytes(raw)
        report = {
            "source_manifest": {
                "path": path.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": collector.sha256_bytes(raw),
                "source_identity": source_manifest["source_identity"],
                "source_version": source_manifest["source_version"],
            }
        }

        resolved_raw, identity = collector.resolve_source_manifest(
            report, expected_platform="bilibili"
        )

        self.assertEqual(resolved_raw, raw)
        self.assertEqual(identity["canonical_item_id"], "BV1Issue7001")
        stale = copy.deepcopy(report)
        stale["source_manifest"]["source_version"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "differs"):
            collector.resolve_source_manifest(stale, expected_platform="bilibili")

    def test_prior_slice_fixture_bindings_use_their_implementation_commit(self) -> None:
        validator = self.validator()
        manifest_path = (
            PROJECT_ROOT / "evidence/slice-03/exit-evidence-manifest.json"
        )
        manifest = read_json(manifest_path)
        changed_fixture = next(
            item
            for item in manifest["fixtures"]
            if item["path"].endswith(
                "control-store-backup-manifest.valid.json"
            )
        )
        self.assertNotEqual(
            changed_fixture["sha256"],
            validator.sha256_file(PROJECT_ROOT / changed_fixture["path"]),
        )

        validator.validate_bindings(manifest, manifest_path)

        stale_log = copy.deepcopy(manifest)
        stale_log["commands"][0]["log"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(validator.EvidenceError, "fingerprint mismatch"):
            validator.validate_bindings(stale_log, manifest_path)

    def test_slice4_smoke_runner_only_delegates_to_product_cli(self) -> None:
        runner = self.smoke_runner()
        command = runner.build_product_cli_command(
            Path("case.json"), "bilibili-project-cookie", Path("workspace/待删除/case")
        )

        self.assertEqual(
            command,
            (
                sys.executable,
                "-X",
                "utf8",
                "-B",
                "scripts/video_workflow.py",
                "source-live-smoke",
                "--spec",
                "case.json",
                "--credential-profile",
                "bilibili-project-cookie",
                "--work-root",
                "workspace/待删除/case",
            ),
        )
        self.assertFalse(hasattr(runner, "download"))

    def test_slice4_exit_evidence_command_covers_closed_test_class(self) -> None:
        contract = self.contract()
        actual = {
            (
                "tests.video_workflow.test_issue7_exit_evidence."
                f"Slice4ExitEvidenceTests.{method_name}"
            )
            for method_name in unittest.TestLoader().getTestCaseNames(
                Slice4ExitEvidenceTests
            )
        }

        self.assertEqual(set(contract.EXIT_EVIDENCE_TEST_TARGETS), actual)

    def test_slice4_closed_command_covers_independent_review_repairs(self) -> None:
        contract = self.contract()
        production_command = set(contract.COMMANDS[5][1])
        adapter_command = set(contract.COMMANDS[6][1])

        required_production_targets = {
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
        }
        required_adapter_targets = {
            "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_recorded_provider_manifest_is_closed_and_versioned",
            "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_recorded_provider_rejects_declared_stdio_hash_drift",
            "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_bilibili_p2_probe_selection_drives_the_acquisition_command",
            "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_bilibili_acquire_rejects_a_different_item_before_provider_launch",
            "tests.video_workflow.test_platform_adapters.PlatformAdapterTests.test_youtube_acquire_rejects_a_different_item_before_provider_launch",
        }

        self.assertTrue(required_production_targets <= production_command)
        self.assertTrue(required_adapter_targets <= adapter_command)


if __name__ == "__main__":
    unittest.main()

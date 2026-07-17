from __future__ import annotations

import copy
from contextlib import redirect_stderr
import importlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest import mock

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json"
FIXTURE_ROOT = PROJECT_ROOT / "tests/video_workflow/fixtures"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
SLICE_BASE_COMMIT = "aaaaeac5747fddc0915a59df34de47e6e8cfec48"


def load_script(name: str, relative_path: str):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Slice3ExitEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = read_json(SCHEMA_PATH)
        self.schema_validator = Draft202012Validator(self.schema)

    def contract(self):
        return load_script(
            f"slice3_evidence_contract_{uuid.uuid4().hex}",
            "scripts/slice3_exit_evidence_contract.py",
        )

    def validator(self):
        return load_script(
            f"slice3_evidence_validator_{uuid.uuid4().hex}",
            "scripts/validate_slice_exit_evidence.py",
        )

    def collector(self):
        return load_script(
            f"slice3_evidence_collector_{uuid.uuid4().hex}",
            "scripts/collect_slice3_exit_evidence.py",
        )

    def semantic_manifest(self) -> dict:
        contract = self.contract()
        manifest = read_json(
            FIXTURE_ROOT / "exit_evidence_manifest.v2.slice3.valid.json"
        )
        manifest["commands"] = [
            {
                "test_id": test_id,
                "command": list(command),
                "expected_exit_code": 0,
                "actual_exit_code": 0,
                "log": {
                    "role": "command_log",
                    "path": f"evidence/slice-03/logs/{test_id}.log",
                    "sha256": "0" * 64,
                },
                "conforms": True,
            }
            for test_id, command in contract.COMMANDS
        ]
        manifest["expected_checkpoints"] = copy.deepcopy(
            contract.EXPECTED_CHECKPOINTS
        )
        manifest["results"] = copy.deepcopy(contract.RESULTS)
        manifest["result_bindings"] = copy.deepcopy(contract.RESULT_BINDINGS)
        manifest["fault_points"] = list(contract.FAULT_POINTS)
        manifest["fixtures"] = [
            {"role": role, "path": path, "sha256": "0" * 64}
            for role, path in contract.FIXTURE_SPECS
        ]
        return manifest

    def test_slice3_schema_requires_first_class_result_kinds(self) -> None:
        valid = read_json(
            FIXTURE_ROOT / "exit_evidence_manifest.v2.slice3.valid.json"
        )
        missing = read_json(
            FIXTURE_ROOT
            / "exit_evidence_manifest.v2.slice3.missing-domain.invalid.json"
        )

        self.assertEqual(list(self.schema_validator.iter_errors(valid)), [])
        errors = list(self.schema_validator.iter_errors(missing))
        self.assertTrue(errors)
        self.assertIn("fairness", " ".join(str(error) for error in errors))

    def test_slice3_semantics_bind_exact_results_commands_and_fqn_targets(
        self,
    ) -> None:
        validator = self.validator()
        contract = self.contract()
        manifest = self.semantic_manifest()
        validator.validate_semantics(manifest)

        replaced = copy.deepcopy(manifest)
        replaced["commands"][4]["command"] = [sys.executable, "-c", "pass"]
        with self.assertRaisesRegex(
            validator.EvidenceError, "closed command vector"
        ):
            validator.validate_semantics(replaced)

        nonzero = copy.deepcopy(manifest)
        nonzero["commands"][0]["expected_exit_code"] = 1
        nonzero["commands"][0]["actual_exit_code"] = 1
        with self.assertRaisesRegex(
            validator.EvidenceError, "expected exit code"
        ):
            validator.validate_semantics(nonzero)

        unexecuted = copy.deepcopy(manifest)
        unexecuted["result_bindings"][0]["test_target"] = (
            "tests.video_workflow.test_resource_admission."
            "ResourceAdmissionTests.test_unregistered_claim"
        )
        with self.assertRaisesRegex(
            validator.EvidenceError, "explicitly executed test target"
        ):
            validator.validate_semantics(unexecuted)

        unrelated_fixture = copy.deepcopy(manifest)
        unrelated_fixture["fixtures"][0]["path"] = "README.md"
        with self.assertRaisesRegex(
            validator.EvidenceError, "closed fixture set"
        ):
            validator.validate_semantics(unrelated_fixture)

        recovery_bindings = {
            binding["result_id"]: (
                binding["result_kind"],
                binding["test_target"],
            )
            for binding in manifest["result_bindings"]
            if binding["command_id"] == "slice3-control-store-recovery"
        }
        expected_resume_bindings = {
            "public_restore_resume_cli_is_operational": (
                "positive",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[10],
            ),
            "invalid_restore_lock_or_committed_report_drift_fails_closed": (
                "negative",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[11],
            ),
            "concurrent_restore_resume_is_fenced_by_operation_lock": (
                "fencing",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[10],
            ),
            "restore_resume_converges_from_every_persistent_boundary": (
                "restart",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[6],
            ),
            "state_sentinel_half_write_windows_are_repaired": (
                "restart",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[8],
            ),
            "partial_control_store_publication_resumes": (
                "restart",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[9],
            ),
            "hard_exit_restore_resume_converges_via_public_cli": (
                "restart",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[10],
            ),
            "subsequent_orphan_staging_and_preservation_evidence_is_exact": (
                "recovery",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[7],
            ),
            "initial_sentinel_before_prepared_history_resumes_via_public_cli": (
                "restart",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[12],
            ),
            "resource_transition_reconciliation_resumes_after_hard_exit": (
                "restart",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[13],
            ),
            "resource_transition_evidence_survives_reconcile_hard_exit": (
                "recovery",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[13],
            ),
            "blocked_state_ahead_window_resumes": (
                "restart",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[14],
            ),
            "blocked_recovery_and_orphan_reports_remain_hash_bound": (
                "recovery",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[14],
            ),
            "non_file_active_restore_sentinel_authority_fails_closed": (
                "negative",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[15],
            ),
            "contradictory_resource_inventory_contract_is_rejected": (
                "negative",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[16],
            ),
            "windows_junction_restore_authority_is_rejected": (
                "negative",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[17],
            ),
            "restore_lock_open_handle_path_identity_is_rechecked": (
                "fencing",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[18],
            ),
            "first_restore_committed_report_drift_blocks_archive": (
                "negative",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[19],
            ),
            "first_restore_report_revalidation_preserves_resumable_authority": (
                "recovery",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[19],
            ),
            "busy_quiescence_failure_does_not_quarantine_or_publish": (
                "negative",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[20],
            ),
            "busy_quiescence_prepared_restore_resumes_after_contention": (
                "restart",
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS[20],
            ),
        }
        for result_id, expected in expected_resume_bindings.items():
            with self.subTest(result_id=result_id):
                self.assertEqual(recovery_bindings[result_id], expected)

        resource_bindings = {
            binding["result_id"]: (
                binding["result_kind"],
                binding["test_target"],
            )
            for binding in manifest["result_bindings"]
            if binding["command_id"] == "slice3-resource-admission"
        }
        expected_release_bindings = {
            "trusted_matching_local_process_proof_releases_normal_lease": (
                "positive",
                contract.RESOURCE_ADMISSION_TEST_TARGETS[27],
            ),
            "unverified_provider_result_cannot_release_capacity": (
                "negative",
                contract.RESOURCE_ADMISSION_TEST_TARGETS[25],
            ),
            "provider_terminal_result_is_bound_to_lease_attempt_and_launch_authority": (
                "fencing",
                contract.RESOURCE_ADMISSION_TEST_TARGETS[26],
            ),
            "reconciled_inflight_launch_identity_supports_trusted_resolution": (
                "recovery",
                contract.RESOURCE_ADMISSION_TEST_TARGETS[24],
            ),
            "quota_zero_reclaim_advances_claim_and_queues_replacement": (
                "quota",
                contract.RESOURCE_ADMISSION_TEST_TARGETS[28],
            ),
            "provider_resolution_replay_reuses_persisted_proof": (
                "restart",
                contract.RESOURCE_ADMISSION_TEST_TARGETS[29],
            ),
            "local_process_resolution_replay_reuses_persisted_proof": (
                "restart",
                contract.RESOURCE_ADMISSION_TEST_TARGETS[30],
            ),
            "active_reservation_blocks_its_full_set_while_disjoint_resources_progress": (
                "fairness",
                contract.RESOURCE_ADMISSION_TEST_TARGETS[19],
            ),
        }
        for result_id, expected in expected_release_bindings.items():
            with self.subTest(result_id=result_id):
                self.assertEqual(resource_bindings[result_id], expected)
        result_ids = {
            result_id
            for values in manifest["results"].values()
            for result_id in values
        }
        self.assertNotIn(
            "breaker_and_zero_capacity_do_not_freeze_unrelated_progress",
            result_ids,
        )
        self.assertNotIn(
            "temporarily_unschedulable_reservation_preserves_healthy_progress",
            result_ids,
        )
        self.assertNotIn(
            "temporarily_blocked_reservation_recovers_without_global_freeze",
            result_ids,
        )

    def test_slice3_semantics_reject_unbound_result_fixture(self) -> None:
        validator = self.validator()
        contract = self.contract()
        manifest = self.semantic_manifest()
        unbound = read_json(
            FIXTURE_ROOT / "exit_evidence_manifest.v2.slice3.unbound-result.invalid.json"
        )
        manifest["result_bindings"] = copy.deepcopy(unbound["result_bindings"])

        self.assertEqual(list(self.schema_validator.iter_errors(unbound)), [])
        with self.assertRaisesRegex(
            validator.EvidenceError, "result bindings differ"
        ):
            validator.validate_semantics(manifest)
        self.assertGreater(len(contract.RESULT_BINDINGS), len(unbound["result_bindings"]))

    def test_slice3_collector_registers_ten_closed_commands_and_correct_paths(
        self,
    ) -> None:
        collector = self.collector()
        contract = self.contract()
        expected_ids = [
            "slice0-regression",
            "slice3-contracts",
            "slice1-regression",
            "slice2-regression",
            "slice3-resource-admission",
            "slice3-control-store-recovery",
            "slice3-exit-evidence-contracts",
            "slice2-exit-evidence",
            "slice3-syntax",
            "slice3-diff-check",
        ]

        self.assertEqual(collector.COMMANDS, contract.COMMANDS)
        self.assertEqual([item[0] for item in collector.COMMANDS], expected_ids)
        self.assertEqual(len(collector.COMMANDS), 10)
        recovery_command = dict(collector.COMMANDS)[
            "slice3-control-store-recovery"
        ]
        resource_command = dict(collector.COMMANDS)[
            "slice3-resource-admission"
        ]
        self.assertEqual(len(contract.RESOURCE_ADMISSION_TEST_TARGETS), 31)
        self.assertEqual(len(contract.CONTROL_STORE_RECOVERY_TEST_TARGETS), 21)
        for target in (
            *contract.RESOURCE_ADMISSION_TEST_TARGETS,
            *contract.MULTIPROCESS_TEST_TARGETS,
            *contract.CONTROL_STORE_INTEGRITY_TEST_TARGETS,
        ):
            self.assertEqual(resource_command.count(target), 1)
        for target in contract.CONTROL_STORE_RECOVERY_TEST_TARGETS:
            self.assertEqual(recovery_command.count(target), 1)
        self.assertEqual(collector.SLICE_BASE_COMMIT, SLICE_BASE_COMMIT)
        self.assertEqual(
            collector.EVIDENCE_REFRESH_ROOT,
            PROJECT_ROOT / "workspace/待删除/exit-evidence-refresh/slice-03",
        )

    def test_slice3_collector_logs_include_exact_implementation_marker(self) -> None:
        collector = self.collector()
        completed = subprocess.CompletedProcess(
            args=["command"], returncode=0, stdout=b"stdout\r\n", stderr=b"stderr\r"
        )
        implementation_commit = "a" * 40
        with mock.patch.object(collector.subprocess, "run", return_value=completed):
            captured = collector.run_commands(implementation_commit)

        self.assertEqual(len(captured), 10)
        marker = f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n".encode()
        for item in captured:
            self.assertNotIn(b"\r", item["raw"])
            self.assertEqual(item["raw"].count(marker), 1)
        admission = next(
            item for item in captured if item["test_id"] == "slice3-resource-admission"
        )
        contract = self.contract()
        for fault_point in contract.FAULT_POINTS:
            marker = f"EVIDENCE_FAULT_POINT: {fault_point}\n".encode()
            self.assertEqual(admission["raw"].count(marker), 1)

    def test_slice3_collector_refuses_dirty_implementation_head(self) -> None:
        collector = self.collector()
        with (
            mock.patch.object(collector, "git", return_value=" M kernel.py"),
            mock.patch.object(collector, "run_commands") as run_commands,
            redirect_stderr(io.StringIO()),
        ):
            returncode = collector.main()

        self.assertEqual(returncode, 2)
        run_commands.assert_not_called()

    def test_slice3_validator_rejects_missing_or_wrong_log_commit_marker(self) -> None:
        validator = self.validator()
        root = TEST_RUNS / f"slice3-log-provenance-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        log_path = root / "command.log"
        relative = log_path.relative_to(PROJECT_ROOT).as_posix()
        implementation_commit = "a" * 40
        manifest = {
            "implementation_commit": implementation_commit,
            "commands": [{"test_id": "command", "log": {"path": relative}}],
        }

        log_path.write_text(
            f"OK\nEVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n",
            encoding="utf-8",
        )
        validator.validate_command_log_provenance(manifest)

        for content in (
            "OK\n",
            f"OK\nEVIDENCE_IMPLEMENTATION_COMMIT: {'b' * 40}\n",
            (
                f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n"
                f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n"
            ),
        ):
            with self.subTest(content=content):
                log_path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(
                    validator.EvidenceError, "implementation commit marker"
                ):
                    validator.validate_command_log_provenance(manifest)

    def test_slice3_validator_binds_six_fault_points_to_admission_log(self) -> None:
        validator = self.validator()
        contract = self.contract()
        root = TEST_RUNS / f"slice3-fault-provenance-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        log_path = root / "resource-admission.log"
        relative = log_path.relative_to(PROJECT_ROOT).as_posix()
        implementation_commit = "a" * 40
        manifest = {
            "implementation_commit": implementation_commit,
            "fault_points": list(contract.FAULT_POINTS),
            "commands": [
                {
                    "test_id": "slice3-resource-admission",
                    "log": {"path": relative},
                }
            ],
        }
        marker = f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation_commit}\n"
        fault_lines = [
            f"EVIDENCE_FAULT_POINT: {fault_point}\n"
            for fault_point in contract.FAULT_POINTS
        ]

        log_path.write_text(marker + "".join(fault_lines), encoding="utf-8")
        validator.validate_command_log_provenance(manifest)

        for content in (
            marker + "".join(fault_lines[:-1]),
            marker + "".join([*fault_lines, fault_lines[0]]),
            marker + "".join([*fault_lines, "EVIDENCE_FAULT_POINT: stale\n"]),
            marker + "".join(reversed(fault_lines)),
        ):
            with self.subTest(content=content):
                log_path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(
                    validator.EvidenceError, "fault point provenance"
                ):
                    validator.validate_command_log_provenance(manifest)

    def test_slice3_closed_test_targets_and_fixtures_resolve(self) -> None:
        contract = self.contract()
        target_groups = (
            (
                contract.RESOURCE_ADMISSION_TEST_TARGETS,
                "tests.video_workflow.test_resource_admission",
                "ResourceAdmissionTests",
            ),
            (
                contract.MULTIPROCESS_TEST_TARGETS,
                "tests.video_workflow.test_resource_admission_multiprocess",
                "ResourceAdmissionMultiprocessTests",
            ),
            (
                contract.CONTROL_STORE_INTEGRITY_TEST_TARGETS,
                "tests.video_workflow.test_resource_control_store_integrity",
                "ResourceControlStoreIntegrityTests",
            ),
            (
                contract.CONTROL_STORE_RECOVERY_TEST_TARGETS,
                "tests.video_workflow.test_control_store_recovery",
                "ControlStoreRecoveryTests",
            ),
            (
                contract.EXIT_EVIDENCE_TEST_TARGETS,
                "tests.video_workflow.test_issue6_exit_evidence",
                "Slice3ExitEvidenceTests",
            ),
        )
        for configured_targets, module_name, class_name in target_groups:
            with self.subTest(module=module_name):
                test_class = getattr(importlib.import_module(module_name), class_name)
                actual_targets = {
                    f"{module_name}.{class_name}.{method_name}"
                    for method_name in unittest.TestLoader().getTestCaseNames(test_class)
                }
                self.assertEqual(set(configured_targets), actual_targets)
        targets = (
            *contract.RESOURCE_ADMISSION_TEST_TARGETS,
            *contract.MULTIPROCESS_TEST_TARGETS,
            *contract.CONTROL_STORE_INTEGRITY_TEST_TARGETS,
            *contract.CONTROL_STORE_RECOVERY_TEST_TARGETS,
            *contract.EXIT_EVIDENCE_TEST_TARGETS,
        )
        self.assertEqual(len(targets), 76)
        self.assertEqual(len(targets), len(set(targets)))
        bound_targets = {
            binding["test_target"] for binding in contract.RESULT_BINDINGS
        }
        implementation_targets = {
            *contract.RESOURCE_ADMISSION_TEST_TARGETS,
            *contract.MULTIPROCESS_TEST_TARGETS,
            *contract.CONTROL_STORE_INTEGRITY_TEST_TARGETS,
            *contract.CONTROL_STORE_RECOVERY_TEST_TARGETS,
        }
        self.assertEqual(implementation_targets - bound_targets, set())
        for target in targets:
            with self.subTest(target=target):
                loader = unittest.TestLoader()
                suite = loader.loadTestsFromName(target)
                self.assertEqual(loader.errors, [])
                self.assertEqual(suite.countTestCases(), 1)
        for _, relative_path in contract.FIXTURE_SPECS:
            with self.subTest(fixture=relative_path):
                self.assertTrue((PROJECT_ROOT / relative_path).is_file())

    def test_slice3_fixed_base_is_registered_exactly(self) -> None:
        validator = self.validator()
        third = next(
            branch
            for branch in self.schema["oneOf"]
            if branch["properties"]["slice"]["properties"]["number"]["const"] == 3
        )

        self.assertEqual(third["properties"]["slice_base_commit"]["const"], SLICE_BASE_COMMIT)
        self.assertEqual(validator.SLICE_CONFIGS[3]["base_commit"], SLICE_BASE_COMMIT)

        implementation_commit = "a" * 40
        manifest_path = PROJECT_ROOT / "evidence/slice-03/exit-evidence-manifest.json"
        manifest_relative = manifest_path.relative_to(PROJECT_ROOT).as_posix()
        log_relative = "evidence/slice-03/logs/slice3-contracts.log"
        manifest = {
            "implementation_commit": implementation_commit,
            "evidence_paths": [manifest_relative, log_relative],
        }

        def lineage_git(*arguments: str) -> str:
            if arguments == ("rev-parse", "HEAD"):
                return implementation_commit
            if arguments == (
                "cat-file",
                "-e",
                f"{implementation_commit}^{{commit}}",
            ):
                return ""
            raise AssertionError(f"unexpected git call: {arguments}")

        with (
            mock.patch.object(validator, "git", side_effect=lineage_git),
            mock.patch.object(
                validator,
                "changed_worktree_paths",
                return_value={manifest_relative, log_relative},
            ),
        ):
            validator.validate_lineage(
                manifest,
                manifest_path,
                pre_publication=True,
            )

        with (
            mock.patch.object(validator, "git", side_effect=lineage_git),
            mock.patch.object(
                validator,
                "changed_worktree_paths",
                return_value={manifest_relative, log_relative, "src/uncommitted.py"},
            ),
            self.assertRaisesRegex(
                validator.EvidenceError,
                "non-evidence changes",
            ),
        ):
            validator.validate_lineage(
                manifest,
                manifest_path,
                pre_publication=True,
            )


if __name__ == "__main__":
    unittest.main()

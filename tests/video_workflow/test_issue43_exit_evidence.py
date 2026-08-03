from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from jsonschema import Draft202012Validator

from scripts import issue43_exit_evidence_contract as contract
from scripts import collect_issue43_exit_evidence as collector
from scripts import validate_slice_exit_evidence as validator
from tests.video_workflow._issue43_git_authority import build_current_global_gate_authority
from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json"
CLI = PROJECT_ROOT / "scripts/video_workflow.py"


class Issue43ExitEvidenceContractTests(unittest.TestCase):
    def manifest(self) -> dict:
        sha256 = "1" * 64
        return {
            "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
            "schema_version": 2,
            "kind": "video-workflow-exit-evidence",
            "fingerprint_algorithm": "sha256-raw-v1",
            "slice": {"number": contract.SLICE_NUMBER, "name": contract.SLICE_NAME},
            "slice_base_commit": contract.SLICE_BASE_COMMIT,
            "implementation_commit": "2" * 40,
            "evidence_paths": [
                "evidence/global-gate/exit-evidence-manifest.json",
                *[
                    f"evidence/global-gate/logs/{command_id}.log"
                    for command_id, _, _ in contract.COMMANDS
                ],
            ],
            "generated_at": "2026-08-02T00:00:00Z",
            "activation_scope": deepcopy(contract.ACTIVATION_SCOPE),
            "atomic_members": list(contract.ATOMIC_MEMBERS),
            "atomic_member_status": deepcopy(contract.ATOMIC_MEMBER_STATUS),
            "mirror_checks": [
                {
                    "source_path": str((PROJECT_ROOT / source).resolve()),
                    "mirror_path": str((PROJECT_ROOT / mirror).resolve()),
                    "source_sha256": hashlib.sha256(
                        (PROJECT_ROOT / source).read_bytes()
                    ).hexdigest(),
                    "mirror_sha256": hashlib.sha256(
                        (PROJECT_ROOT / mirror).read_bytes()
                    ).hexdigest(),
                    "status": "equal",
                }
                for source, mirror in contract.MIRROR_SPECS
            ],
            "policy_status": contract.POLICY_STATUS,
            "commands": [
                {
                    "test_id": command_id,
                    "command": list(command),
                    "expected_exit_code": expected_exit_code,
                    "actual_exit_code": expected_exit_code,
                    "log": {
                        "role": "command_log",
                        "path": f"evidence/global-gate/logs/{command_id}.log",
                        "sha256": sha256,
                    },
                    "conforms": True,
                }
                for command_id, command, expected_exit_code in contract.COMMANDS
            ],
            "expected_checkpoints": deepcopy(contract.EXPECTED_CHECKPOINTS),
            "fixtures": [
                {"role": role, "path": path, "sha256": sha256}
                for role, path in contract.FIXTURE_SPECS
            ],
            "results": deepcopy(contract.RESULTS),
            "result_bindings": deepcopy(contract.RESULT_BINDINGS),
            "artifact_fingerprints": [
                {
                    "role": "implementation_artifact",
                    "path": "scripts/issue43_exit_evidence_contract.py",
                    "sha256": sha256,
                }
            ],
            "unresolved_exceptions": [],
            "overall_decision": "pass",
        }

    def test_contract_registers_the_complete_global_gate_qualification_surface(self) -> None:
        required_results = {
            "legacy_run_record_free_v2_pass",
            "kernel_v2_pass",
            "v1_rejected",
            "stale_legacy_authority_rejected",
            "incomplete_mirrors_rejected",
            "unsupported_identity_rejected",
            "contract_gap_rejected",
            "failed_atomic_member_rejected",
            "patch_publication_recovered",
            "report_publication_recovered",
            "activation_publication_recovered",
            "activation_writers_fenced",
            "activation_retry_idempotent",
            "control_store_unavailable_rejected",
            "control_store_corrupt_rejected",
            "control_store_locked_rejected",
            "control_store_incompatible_rejected",
            "fallback_rejected",
            "translation_rejected",
            "synthetic_legacy_run_rejected",
            "dual_authority_rejected",
            "active_global_gate_only",
        }
        actual_results = {
            result_id for values in contract.RESULTS.values() for result_id in values
        }
        self.assertTrue(required_results <= actual_results)
        self.assertEqual(
            actual_results,
            {binding["result_id"] for binding in contract.RESULT_BINDINGS},
        )
        self.assertEqual(
            set(contract.QUALIFICATION_TEST_TARGETS),
            {binding["test_target"] for binding in contract.RESULT_BINDINGS},
        )
        self.assertGreaterEqual(
            len(contract.QUALIFICATION_TEST_TARGETS),
            12,
            "Issue #43 evidence cannot collapse the cutover matrix into a few broad tracers",
        )
        self.assertEqual("active_global_gate", contract.ACTIVATION_SCOPE["kind"])
        self.assertEqual("unchanged", contract.ACTIVATION_SCOPE["platform_kernel_authority"])
        self.assertEqual(
            contract.QUALIFICATION_CONTRACT_SHA256,
            hashlib.sha256((
                json.dumps(
                    contract.RESULT_BINDINGS,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n"
            ).encode("utf-8")).hexdigest(),
        )

    def test_v2_schema_admits_global_gate_activation_and_historical_manifests(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        schema_validator = Draft202012Validator(schema)
        schema_validator.validate(self.manifest())
        for number in range(7, 11):
            path = PROJECT_ROOT / f"evidence/slice-{number:02d}/exit-evidence-manifest.json"
            schema_validator.validate(json.loads(path.read_text(encoding="utf-8")))

    def test_manifest_v2_is_the_direct_sha_bound_global_gate_activation_contract(self) -> None:
        root = new_case_dir(self.id(), label="issue43-manifest-v2-activation")
        repository, manifest_path = build_current_global_gate_authority(root)
        result = GlobalGatePublisher(project_root=repository).activate(
            control_store_root=root,
            exit_evidence=manifest_path,
            activated_at="2026-08-03T00:00:00Z",
        )
        authority = json.loads(
            Path(result["authority_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            authority["exit_evidence_sha256"],
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        )

    def test_negative_result_bindings_require_public_failure_diagnostics(self) -> None:
        manifest = self.manifest()
        negative = next(
            binding
            for binding in manifest["result_bindings"]
            if binding["result_kind"] == "negative"
        )
        self.assertIn("expected_first_failing_gate", negative)
        self.assertIn("expected_error_code", negative)
        damaged = deepcopy(manifest)
        damaged_binding = next(
            binding
            for binding in damaged["result_bindings"]
            if binding["result_kind"] == "negative"
        )
        damaged_binding.pop("expected_error_code")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(damaged))
        self.assertTrue(errors, "a negative result without a stable public error code must be invalid")

    def test_schema_valid_result_tracer_substitution_cannot_activate(self) -> None:
        # scenario_id: qualification_tracer_substitution; the binding remains schema-valid
        # and command-bound, while its registered qualification identity is stale.
        root = new_case_dir(self.id(), label="issue43-tracer-substitution")
        manifest = self.manifest()
        manifest["result_bindings"][0]["test_target"] = manifest["result_bindings"][1]["test_target"]
        manifest_path = root / "exit-evidence-manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(CLI), "global-gate-activate",
             "--control-store-root", str(root), "--exit-evidence", str(manifest_path),
             "--activated-at", "2026-08-03T00:00:00Z"],
            cwd=PROJECT_ROOT, text=True, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        envelope = json.loads(completed.stdout)
        self.assertEqual(envelope["data"]["first_failing_gate"], "qualification_result_binding")
        self.assertEqual(envelope["data"]["error_code"], "global_gate_qualification_contract_stale")

    def test_negative_result_diagnostic_drift_has_a_stable_binding_authority_gate(self) -> None:
        # scenario_id: negative_tracer_diagnostic_drift
        # authority: registered public tracer -> boundary: qualification publication
        # mutation: expected error code only; rematerialized: none; stale: binding authority
        # expected_first_gate: qualification_result_binding
        # expected_error_code: result_binding_authority_stale
        manifest = self.manifest()
        negative = next(
            binding
            for binding in manifest["result_bindings"]
            if binding["result_kind"] == "negative"
        )
        negative["expected_error_code"] = "invented_self_reported_rejection"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("qualification_result_binding", raised.exception.first_failing_gate)
        self.assertEqual("result_binding_authority_stale", raised.exception.error_code)

    def test_historical_slice_7_through_10_evidence_bytes_match_the_cutover_base(self) -> None:
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--exit-code",
                contract.SLICE_BASE_COMMIT,
                "--",
                "evidence/slice-07",
                "evidence/slice-08",
                "evidence/slice-09",
                "evidence/slice-10",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_platform_kernel_activation_is_a_single_contradiction_with_stable_diagnostics(self) -> None:
        # scenario_id: platform_kernel_scope_change
        # authority: Issue #43 activation scope -> boundary: Exit Evidence publication
        # mutation: platform Kernel authority only; rematerialized: none; stale: none
        # expected_first_gate: activation_scope; expected_error_code: platform_kernel_authority_changed
        manifest = self.manifest()
        manifest["activation_scope"]["platform_kernel_authority"] = "active_kernel"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("activation_scope", raised.exception.first_failing_gate)
        self.assertEqual("platform_kernel_authority_changed", raised.exception.error_code)

    def test_inactive_atomic_member_blocks_before_mirror_and_policy_checks(self) -> None:
        # scenario_id: inactive_atomic_member
        # authority: atomic member registry -> boundary: cutover publication
        # mutation: one member status; rematerialized: none; stale: none
        # expected_first_gate: atomic_member_status; expected_error_code: atomic_member_inactive
        manifest = self.manifest()
        manifest["atomic_member_status"]["validators"] = "failed"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("atomic_member_status", raised.exception.first_failing_gate)
        self.assertEqual("atomic_member_inactive", raised.exception.error_code)

    def test_incomplete_mirror_checks_block_before_policy_status(self) -> None:
        # scenario_id: incomplete_mirror_checks
        # authority: registered mirror pairs -> boundary: cutover publication
        # mutation: omit one pair; rematerialized: none; stale: none
        # expected_first_gate: mirror_checks; expected_error_code: incomplete_mirror_checks
        manifest = self.manifest()
        manifest["mirror_checks"].pop()
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("mirror_checks", raised.exception.first_failing_gate)
        self.assertEqual("incomplete_mirror_checks", raised.exception.error_code)

    def test_missing_v1_rejection_is_a_single_coverage_contradiction(self) -> None:
        # scenario_id: missing_v1_rejection
        # authority: closed result set -> boundary: qualification publication
        # mutation: omit v1 result and binding; rematerialized: bindings; stale: none
        # expected_first_gate: qualification_result_coverage; expected_error_code: incomplete_results
        manifest = self.manifest()
        manifest["results"]["negative"].remove("v1_rejected")
        manifest["result_bindings"] = [
            item for item in manifest["result_bindings"] if item["result_id"] != "v1_rejected"
        ]
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("qualification_result_coverage", raised.exception.first_failing_gate)
        self.assertEqual("incomplete_results", raised.exception.error_code)

    def test_failed_atomic_member_precedes_activation_decision(self) -> None:
        # scenario_id: failed_atomic_member
        # authority: closed command vector -> boundary: activation publication
        # mutation: one command exit only; rematerialized: conforms and overall decision; stale: none
        # expected_first_gate: atomic_group; expected_error_code: atomic_member_failed
        manifest = self.manifest()
        manifest["commands"][0]["actual_exit_code"] = 1
        manifest["commands"][0]["conforms"] = False
        manifest["overall_decision"] = "fail"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("atomic_group", raised.exception.first_failing_gate)
        self.assertEqual("atomic_member_failed", raised.exception.error_code)

    def test_unsupported_result_identity_has_a_stable_first_gate(self) -> None:
        # scenario_id: unsupported_result_identity
        # authority: closed result registry -> boundary: qualification publication
        # mutation: add one result and binding identity; rematerialized: bindings; stale: none
        # expected_first_gate: qualification_result_coverage; expected_error_code: unsupported_result_identity
        manifest = self.manifest()
        manifest["results"]["negative"].append("acceptance_v3_rejected")
        extra_binding = deepcopy(manifest["result_bindings"][0])
        extra_binding["result_id"] = "acceptance_v3_rejected"
        extra_binding["result_kind"] = "negative"
        manifest["result_bindings"].append(extra_binding)
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("qualification_result_coverage", raised.exception.first_failing_gate)
        self.assertEqual("unsupported_result_identity", raised.exception.error_code)

    def test_contract_gap_blocks_before_activation_decision(self) -> None:
        # scenario_id: unresolved_contract_gap
        # authority: exception ledger -> boundary: activation publication
        # mutation: one blocking Contract Gap; rematerialized: overall decision; stale: none
        # expected_first_gate: contract_gap; expected_error_code: unresolved_contract_gap
        manifest = self.manifest()
        manifest["unresolved_exceptions"] = [
            {"blocking": True, "code": "contract_gap", "message": "unsupported identity"}
        ]
        manifest["overall_decision"] = "fail"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("contract_gap", raised.exception.first_failing_gate)
        self.assertEqual("unresolved_contract_gap", raised.exception.error_code)

    def test_collector_materializes_the_registered_schema_valid_cutover_shape(self) -> None:
        root = new_case_dir(self.id(), label="issue43-exit-evidence-collector")
        evidence_dir = root / "evidence/global-gate"
        logs = evidence_dir / "logs"
        manifest_path = evidence_dir / "exit-evidence-manifest.json"
        sha256 = "1" * 64
        commands = [
            {
                "test_id": command_id,
                "command": list(command),
                "expected_exit_code": expected_exit_code,
                "actual_exit_code": expected_exit_code,
                "log": {
                    "role": "command_log",
                    "path": f"{root.relative_to(PROJECT_ROOT).as_posix()}/evidence/global-gate/logs/{command_id}.log",
                    "sha256": sha256,
                },
                "conforms": True,
            }
            for command_id, command, expected_exit_code in contract.COMMANDS
        ]

        def fake_git(*arguments: str) -> str:
            if arguments[:2] == ("status", "--porcelain=v1"):
                return ""
            if arguments == ("rev-parse", "HEAD"):
                return "2" * 40
            return ""

        with (
            mock.patch.object(collector, "EVIDENCE_DIR", evidence_dir),
            mock.patch.object(collector, "LOG_DIR", logs),
            mock.patch.object(collector, "MANIFEST_PATH", manifest_path),
            mock.patch.object(collector, "git", side_effect=fake_git),
            mock.patch.object(collector, "preserve_previous_evidence"),
            mock.patch.object(collector, "run_commands", return_value=commands),
            mock.patch.object(
                collector,
                "fingerprint_implementation_changes",
                return_value=[
                    {
                        "role": "implementation_artifact",
                        "path": "scripts/issue43_exit_evidence_contract.py",
                        "sha256": sha256,
                    }
                ],
            ),
        ):
            self.assertEqual(collector.main(), 0)

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(json.loads(manifest_path.read_text(encoding="utf-8")))
        activated = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-B", str(CLI),
                "global-gate-activate", "--control-store-root", str(root),
                "--exit-evidence", str(manifest_path),
                "--activated-at", "2026-08-03T00:00:00Z",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(activated.returncode, 0)
        envelope = json.loads(activated.stdout)
        self.assertEqual(envelope["data"]["first_failing_gate"], "implementation_lineage")
        self.assertEqual(envelope["data"]["error_code"], "implementation_commit_invalid")


if __name__ == "__main__":
    unittest.main()

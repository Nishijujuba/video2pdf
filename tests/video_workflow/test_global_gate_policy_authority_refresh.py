from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel import cli as workflow_cli
from video2pdf_workflow_kernel.errors import AcceptanceV2Rejected, GlobalGateFault
from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher, _fingerprint
from video2pdf_workflow_kernel.utils import sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts/video_workflow.py"


class GlobalGatePolicyAuthorityRefreshTests(unittest.TestCase):
    """Policy evidence advances without changing the delivery-authority bytes."""

    def authority(self) -> tuple[Path, Path, GlobalGatePublisher, bytes, mock.Mock]:
        root = new_case_dir(self.id(), label="global-gate-policy-refresh")
        evidence = root / "current-exit-evidence.json"
        evidence.write_text('{"policy_status":"active_global_gate"}\n', encoding="utf-8")
        publisher = GlobalGatePublisher(project_root=PROJECT_ROOT)
        authority_path = root / "active_global_gate.json"
        authority = {
            "schema_name": "global-gate-authority",
            "schema_version": "1.0.0",
            "generation": 1,
            "active_global_gate": "acceptance_report_v2",
            "acceptance_report_schema_version": "2.0.0",
            "legacy_acceptance_authority": "legacy_acceptance_input_set_v1",
            "platform_kernel_authority": "unchanged",
            "exit_evidence_path": str(evidence),
            "exit_evidence_sha256": sha256_file(evidence),
            "activated_at": "2026-08-23T00:00:00Z",
        }
        authority["authority_sha256"] = _fingerprint(authority, "authority_sha256")
        authority_path.write_text(
            json.dumps(authority, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with publisher._connect(root) as control:
            control.execute(
                "INSERT INTO gate_authority(singleton,generation,evidence_sha256,authority_sha256) VALUES(1,1,?,?)",
                (sha256_file(evidence), sha256_file(authority_path)),
            )
        validator = mock.patch.object(
            publisher,
            "_validate_publication_identity",
            return_value=(
                SimpleNamespace(
                    sha256=sha256_file(evidence),
                    value={
                        "implementation_commit": "1" * 40,
                        "policy_status": "active_global_gate",
                        "atomic_member_status": {},
                        "mirror_checks": [],
                        "results": {},
                    },
                ),
                "2" * 40,
            ),
        ).start()
        self.addCleanup(mock.patch.stopall)
        stable_bytes = authority_path.read_bytes()
        return root, evidence, publisher, stable_bytes, validator

    def test_public_command_declares_the_policy_refresh_contract(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(CLI),
                "global-gate-policy-authority-refresh",
                "--help",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        for option in (
            "--control-store-root",
            "--exit-evidence",
            "--expected-generation",
            "--refreshed-at",
        ):
            self.assertIn(option, completed.stdout)

    def test_refresh_publishes_current_policy_and_preserves_base_authority(self) -> None:
        root, evidence, publisher, stable_bytes, _ = self.authority()
        result = publisher.refresh_policy_authority(
            control_store_root=root,
            exit_evidence=evidence,
            expected_generation=0,
            refreshed_at="2026-08-23T00:01:00Z",
        )
        self.assertEqual(1, result["generation"])
        self.assertFalse(result["idempotent"])
        self.assertEqual(stable_bytes, (root / "active_global_gate.json").read_bytes())
        checked = publisher.check_policy(control_store_root=root)
        self.assertTrue(checked["current"])
        self.assertEqual(1, checked["policy_authority"]["generation"])

    def test_refresh_rejects_stale_expected_generation(self) -> None:
        root, evidence, publisher, _, _ = self.authority()
        with self.assertRaises(AcceptanceV2Rejected) as raised:
            publisher.refresh_policy_authority(
                control_store_root=root,
                exit_evidence=evidence,
                expected_generation=1,
                refreshed_at="2026-08-23T00:01:00Z",
            )
        self.assertEqual(
            "global_gate_policy_refresh_fenced",
            raised.exception.data["error_code"],
        )

    def test_policy_check_rejects_stale_committed_evidence(self) -> None:
        root, evidence, publisher, _, validator = self.authority()
        publisher.refresh_policy_authority(
            control_store_root=root,
            exit_evidence=evidence,
            expected_generation=0,
            refreshed_at="2026-08-23T00:01:00Z",
        )
        validator.side_effect = AcceptanceV2Rejected(
            "stale",
            data={
                "first_failing_gate": "implementation_currentness",
                "error_code": "evidence_publication_not_current",
            },
        )
        with self.assertRaises(AcceptanceV2Rejected):
            publisher.check_policy(control_store_root=root)

    def test_reconcile_finishes_interrupted_refresh_and_preserves_base(self) -> None:
        root, evidence, publisher, stable_bytes, _ = self.authority()
        with self.assertRaises(GlobalGateFault):
            publisher.refresh_policy_authority(
                control_store_root=root,
                exit_evidence=evidence,
                expected_generation=0,
                refreshed_at="2026-08-23T00:01:00Z",
                fault_point="after_intent",
            )
        result = publisher.reconcile(control_store_root=root)
        self.assertTrue(result["reconciled"])
        self.assertEqual(1, result["generation"])
        self.assertEqual(stable_bytes, (root / "active_global_gate.json").read_bytes())

    def test_reconcile_second_generation_replaces_exact_committed_policy(self) -> None:
        root, evidence, publisher, stable_bytes, validator = self.authority()
        publisher.refresh_policy_authority(
            control_store_root=root,
            exit_evidence=evidence,
            expected_generation=0,
            refreshed_at="2026-08-23T00:01:00Z",
        )
        first_policy_bytes = (root / "active_global_gate_policy.json").read_bytes()

        evidence.write_text('{"policy_status":"active_global_gate","revision":2}\n', encoding="utf-8")
        validator.return_value = (
            SimpleNamespace(
                sha256=sha256_file(evidence),
                value={
                    "implementation_commit": "3" * 40,
                    "policy_status": "active_global_gate",
                    "atomic_member_status": {},
                    "mirror_checks": [],
                    "results": {},
                },
            ),
            "4" * 40,
        )
        with self.assertRaises(GlobalGateFault):
            publisher.refresh_policy_authority(
                control_store_root=root,
                exit_evidence=evidence,
                expected_generation=1,
                refreshed_at="2026-08-23T00:02:00Z",
                fault_point="after_intent",
            )

        result = publisher.reconcile(control_store_root=root)

        self.assertTrue(result["reconciled"])
        self.assertEqual(2, result["generation"])
        self.assertNotEqual(
            first_policy_bytes,
            (root / "active_global_gate_policy.json").read_bytes(),
        )
        self.assertEqual(stable_bytes, (root / "active_global_gate.json").read_bytes())

    def test_reconcile_second_generation_rejects_non_committed_policy_bytes(self) -> None:
        root, evidence, publisher, _, validator = self.authority()
        publisher.refresh_policy_authority(
            control_store_root=root,
            exit_evidence=evidence,
            expected_generation=0,
            refreshed_at="2026-08-23T00:01:00Z",
        )
        evidence.write_text('{"policy_status":"active_global_gate","revision":2}\n', encoding="utf-8")
        validator.return_value = (
            SimpleNamespace(
                sha256=sha256_file(evidence),
                value={
                    "implementation_commit": "3" * 40,
                    "policy_status": "active_global_gate",
                    "atomic_member_status": {},
                    "mirror_checks": [],
                    "results": {},
                },
            ),
            "4" * 40,
        )
        with self.assertRaises(GlobalGateFault):
            publisher.refresh_policy_authority(
                control_store_root=root,
                exit_evidence=evidence,
                expected_generation=1,
                refreshed_at="2026-08-23T00:02:00Z",
                fault_point="after_intent",
            )
        policy_path = root / "active_global_gate_policy.json"
        tampered = json.loads(policy_path.read_text(encoding="utf-8"))
        tampered["refreshed_at"] = "tampered"
        policy_path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

        with self.assertRaises(AcceptanceV2Rejected) as raised:
            publisher.reconcile(control_store_root=root)

        self.assertEqual(
            "global_gate_policy_authority_stale",
            raised.exception.data["error_code"],
        )

    def test_public_refresh_classifies_a_write_lock_as_control_store_unavailable(self) -> None:
        root, evidence, _, _, validator = self.authority()
        locked = sqlite3.connect(
            root / "global-gate-control.sqlite3",
            isolation_level=None,
        )
        try:
            locked.execute("BEGIN IMMEDIATE")
            stdout = io.StringIO()
            with mock.patch.object(
                GlobalGatePublisher,
                "_validate_publication_identity",
                return_value=validator.return_value,
            ), redirect_stdout(stdout):
                exit_code = workflow_cli.main(
                    [
                        "global-gate-policy-authority-refresh",
                        "--control-store-root",
                        str(root),
                        "--exit-evidence",
                        str(evidence),
                        "--expected-generation",
                        "0",
                        "--refreshed-at",
                        "2026-08-23T00:01:00Z",
                    ]
                )
        finally:
            locked.execute("ROLLBACK")
            locked.close()

        envelope = json.loads(stdout.getvalue())
        self.assertEqual(50, exit_code)
        self.assertEqual("control_store_unavailable", envelope["classification"])
        self.assertEqual(
            "global_gate_control_store_locked",
            envelope["data"]["error_code"],
        )


if __name__ == "__main__":
    unittest.main()

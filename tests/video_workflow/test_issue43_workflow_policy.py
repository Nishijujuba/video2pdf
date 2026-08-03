from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

from tests.video_workflow._test_run import new_case_dir


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"
ATOMIC_MEMBERS = {
    "catalogs", "projections", "criteria_migration", "schemas", "providers",
    "validators", "hooks", "skills", "project_instructions", "mirrors", "tests",
    "activation_documentation",
}
REQUIRED_RESULTS = {
    "kernel_v2_pass", "legacy_v2_pass", "v1_rejected", "fallback_rejected",
    "translation_rejected", "dual_authority_rejected", "contract_gap_rejected",
    "unsupported_identity_rejected", "synthetic_legacy_run_rejected",
}


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def _run(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=PROJECT_ROOT, text=True, encoding="utf-8", capture_output=True, check=False,
    )
    return completed, json.loads(completed.stdout)


class Issue43WorkflowPolicyTests(unittest.TestCase):
    """Public Seam 4 fixtures start valid and mutate one declared policy invariant."""

    def evidence(self, root: Path, **changes: object) -> Path:
        mirror_source = root / "policy-source.txt"
        mirror_target = root / "policy-mirror.txt"
        mirror_source.write_text("active-global-gate\n", encoding="utf-8")
        mirror_target.write_text("active-global-gate\n", encoding="utf-8")
        mirror_sha = hashlib.sha256(mirror_source.read_bytes()).hexdigest()
        value = {
            "schema_name": "global-gate-exit-evidence", "schema_version": "1.0.0",
            "cutover": "global_acceptance_v2", "overall_decision": "pass",
            "atomic_members": sorted(ATOMIC_MEMBERS),
            "atomic_member_status": {member: "active" for member in sorted(ATOMIC_MEMBERS)},
            "mirror_checks": [{
                "source_path": str(mirror_source.resolve()), "mirror_path": str(mirror_target.resolve()),
                "source_sha256": mirror_sha, "mirror_sha256": mirror_sha, "status": "equal",
            }],
            "policy_status": "active_global_gate",
            "results": {result: True for result in sorted(REQUIRED_RESULTS)},
        }
        value.update(changes)
        return _write(root / "exit-evidence.json", value)

    def activate(self, root: Path, evidence: Path | None = None, *extra: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        return _run(
            "global-gate-activate", "--control-store-root", str(root),
            "--exit-evidence", str(evidence or self.evidence(root)),
            "--activated-at", "2026-08-03T00:00:00Z", *extra,
        )

    def policy_check(self, root: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
        return _run("workflow-policy-check", "--control-store-root", str(root))

    def test_workflow_policy_check_accepts_current_atomic_policy_authority(self) -> None:
        root = new_case_dir(self.id(), label="issue43-policy")
        activated, _ = self.activate(root)
        self.assertEqual(activated.returncode, 0, activated.stdout)
        completed, envelope = self.policy_check(root)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(envelope["data"]["policy_status"], "active_global_gate")
        self.assertEqual(set(envelope["data"]["active_atomic_members"]), ATOMIC_MEMBERS)
        self.assertTrue(envelope["data"]["current"])

    def test_failed_atomic_member_is_first_rejected_by_atomic_member_status(self) -> None:
        # target: one inactive member; mutation: status before publication; first gate: atomic_member_status.
        root = new_case_dir(self.id(), label="issue43-policy")
        statuses = {member: "active" for member in sorted(ATOMIC_MEMBERS)}
        statuses["validators"] = "failed"
        completed, envelope = self.activate(root, self.evidence(root, atomic_member_status=statuses))
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["first_failing_gate"], "atomic_member_status")
        self.assertEqual(envelope["data"]["error_code"], "global_gate_atomic_member_failed")

    def test_mirror_and_policy_status_are_distinct_first_gates(self) -> None:
        scenarios = (
            ("mirror_checks", [], "mirror_checks", "global_gate_mirror_stale"),
            ("policy_status", "target_only", "policy_status", "global_gate_policy_inactive"),
        )
        for field, value, gate, code in scenarios:
            with self.subTest(field=field):
                root = new_case_dir(f"{self.id()}-{field}", label="issue43-policy")
                completed, envelope = self.activate(root, self.evidence(root, **{field: value}))
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(envelope["data"]["first_failing_gate"], gate)
                self.assertEqual(envelope["data"]["error_code"], code)

    def test_required_negative_policy_results_have_stable_first_gate_codes(self) -> None:
        cases = {
            "v1_rejected": "global_gate_v1_not_rejected",
            "contract_gap_rejected": "global_gate_contract_gap_not_rejected",
            "unsupported_identity_rejected": "global_gate_unsupported_identity_not_rejected",
            "synthetic_legacy_run_rejected": "global_gate_synthetic_legacy_run_not_rejected",
            "fallback_rejected": "global_gate_fallback_not_rejected",
            "translation_rejected": "global_gate_translation_not_rejected",
            "dual_authority_rejected": "global_gate_dual_authority_not_rejected",
        }
        for result, code in cases.items():
            with self.subTest(result=result):
                root = new_case_dir(f"{self.id()}-{result}", label="issue43-policy")
                results = {name: True for name in sorted(REQUIRED_RESULTS)}
                results[result] = False
                completed, envelope = self.activate(root, self.evidence(root, results=results))
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(envelope["data"]["first_failing_gate"], "policy_results")
                self.assertEqual(envelope["data"]["error_code"], code)

    def test_activation_interruption_reconciles_and_exact_retry_is_idempotent(self) -> None:
        root = new_case_dir(self.id(), label="issue43-policy")
        evidence = self.evidence(root)
        interrupted, envelope = self.activate(root, evidence, "--fault-point", "after_authority_write")
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertEqual(envelope["classification"], "injected_global_gate_fault")
        reconciled, value = _run("global-gate-reconcile", "--control-store-root", str(root))
        self.assertEqual(reconciled.returncode, 0, reconciled.stdout)
        self.assertTrue(value["data"]["reconciled"])
        retried, value = self.activate(root, evidence)
        self.assertEqual(retried.returncode, 0, retried.stdout)
        self.assertTrue(value["data"]["idempotent"])

    def test_competing_activation_is_fenced(self) -> None:
        root = new_case_dir(self.id(), label="issue43-policy")
        first, _ = self.activate(root)
        self.assertEqual(first.returncode, 0, first.stdout)
        alternate = self.evidence(root)
        value = json.loads(alternate.read_text(encoding="utf-8"))
        value["evidence_nonce"] = "different-authority"
        # Keep the contract closed: change a governed result order through a second valid file path.
        value.pop("evidence_nonce")
        alternate = _write(root / "alternate-evidence.json", value)
        # A distinct byte identity with the same semantics is still a competing publication.
        alternate.write_text(alternate.read_text(encoding="utf-8") + " ", encoding="utf-8")
        completed, envelope = self.activate(root, alternate)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["first_failing_gate"], "activation_fencing")
        self.assertEqual(envelope["data"]["error_code"], "global_gate_authority_conflict")

    def test_control_store_unavailable_corrupt_locked_and_incompatible_fail_closed(self) -> None:
        unavailable = new_case_dir(f"{self.id()}-unavailable", label="issue43-policy") / "missing"
        completed, envelope = self.policy_check(unavailable)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["error_code"], "global_gate_control_store_unavailable")

        corrupt = new_case_dir(f"{self.id()}-corrupt", label="issue43-policy")
        (corrupt / "global-gate-control.sqlite3").write_bytes(b"not sqlite")
        completed, envelope = self.policy_check(corrupt)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["error_code"], "global_gate_control_store_corrupt")

        incompatible = new_case_dir(f"{self.id()}-incompatible", label="issue43-policy")
        with sqlite3.connect(incompatible / "global-gate-control.sqlite3") as database:
            database.execute("PRAGMA user_version=99")
        completed, envelope = self.policy_check(incompatible)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["error_code"], "global_gate_control_store_incompatible")

        locked = new_case_dir(f"{self.id()}-locked", label="issue43-policy")
        activated, _ = self.activate(locked)
        self.assertEqual(activated.returncode, 0)
        database = sqlite3.connect(locked / "global-gate-control.sqlite3", isolation_level=None)
        try:
            database.execute("BEGIN EXCLUSIVE")
            completed, envelope = self.policy_check(locked)
        finally:
            database.execute("ROLLBACK")
            database.close()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["error_code"], "global_gate_control_store_locked")


if __name__ == "__main__":
    unittest.main()

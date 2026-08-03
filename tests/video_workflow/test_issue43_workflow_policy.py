from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

from tests.video_workflow._test_run import new_case_dir
from scripts import issue43_exit_evidence_contract as contract


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"
ATOMIC_MEMBERS = {
    "catalogs", "projections", "criteria_migration", "schemas", "providers",
    "validators", "hooks", "skills", "project_instructions", "mirrors", "tests",
    "activation_documentation",
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
            "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
            "schema_version": 2, "kind": "video-workflow-exit-evidence",
            "fingerprint_algorithm": "sha256-raw-v1",
            "slice": {"number": contract.SLICE_NUMBER, "name": contract.SLICE_NAME},
            "slice_base_commit": contract.SLICE_BASE_COMMIT,
            "implementation_commit": "2" * 40,
            "evidence_paths": ["evidence/global-gate/exit-evidence-manifest.json", "evidence/global-gate/logs/test.log"],
            "generated_at": "2026-08-03T00:00:00Z",
            "activation_scope": deepcopy(contract.ACTIVATION_SCOPE),
            "atomic_members": list(contract.ATOMIC_MEMBERS),
            "atomic_member_status": deepcopy(contract.ATOMIC_MEMBER_STATUS),
            "mirror_checks": [{
                "source_path": str(mirror_source.resolve()), "mirror_path": str(mirror_target.resolve()),
                "source_sha256": mirror_sha, "mirror_sha256": mirror_sha, "status": "equal",
            }],
            "policy_status": "active_global_gate",
            "commands": [
                {"test_id": command_id, "command": list(command), "expected_exit_code": code,
                 "actual_exit_code": code, "log": {"role": "command_log", "path": f"evidence/global-gate/logs/{command_id}.log", "sha256": "1" * 64}, "conforms": True}
                for command_id, command, code in contract.COMMANDS
            ],
            "expected_checkpoints": deepcopy(contract.EXPECTED_CHECKPOINTS),
            "fixtures": [{"role": role, "path": path, "sha256": "1" * 64} for role, path in contract.FIXTURE_SPECS],
            "results": deepcopy(contract.RESULTS),
            "result_bindings": deepcopy(contract.RESULT_BINDINGS),
            "artifact_fingerprints": [{"role": "implementation_artifact", "path": "src/video2pdf_workflow_kernel/global_gate.py", "sha256": "1" * 64}],
            "unresolved_exceptions": [],
            "overall_decision": "pass",
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

    def acceptance_prepare(self, root: Path, binding: dict) -> tuple[subprocess.CompletedProcess[str], dict]:
        binding_path = _write(root / "legacy-input-set.json", binding)
        return _run(
            "acceptance-prepare", "--workspace-root", str(root / "review/acceptance"),
            "--input-binding", str(binding_path), "--attempt-number", "1",
            "--prepared-at", "2026-08-03T00:01:00Z",
        )

    def test_legacy_v2_provider_rejects_contract_gap(self) -> None:
        # scenario_id: legacy_contract_gap; mutation seam: before provider validation.
        root = new_case_dir(self.id(), label="issue43-legacy-provider")
        completed, envelope = self.acceptance_prepare(root, {
            "schema_name": "legacy-acceptance-input-set", "input_track": "legacy",
            "contract_gaps": [{"code": "unsupported_evidence"}],
        })
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["first_failing_gate"], "contract_gap")
        self.assertEqual(envelope["data"]["error_code"], "legacy_contract_gap_blocked")

    def test_legacy_v2_provider_rejects_synthetic_run(self) -> None:
        # scenario_id: synthetic_legacy_run; mutation seam: before provider validation.
        root = new_case_dir(self.id(), label="issue43-legacy-provider")
        completed, envelope = self.acceptance_prepare(root, {
            "schema_name": "legacy-acceptance-input-set", "input_track": "legacy",
            "run": {"synthetic": True},
        })
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["first_failing_gate"], "input_identity")
        self.assertEqual(envelope["data"]["error_code"], "legacy_synthetic_run_rejected")

    def test_legacy_v2_provider_rejects_unsupported_identity(self) -> None:
        # scenario_id: unsupported_legacy_provider; start from adopted positive graph,
        # mutate provider_id, then rematerialize input_set_sha256.
        from tests.video_workflow.test_issue43_global_gate import Issue43GlobalGateTests

        fixture = Issue43GlobalGateTests(methodName="runTest")
        root, paths = fixture.legacy_graph()
        adopted_completed, adopted = fixture.adopt(root, paths)
        self.assertEqual(adopted_completed.returncode, 0, adopted_completed.stdout)
        binding_path = Path(adopted["data"]["input_set_path"])
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["provider"]["provider_id"] = "unsupported-legacy-provider"
        binding["input_set_sha256"] = hashlib.sha256((
            json.dumps(
                {key: value for key, value in binding.items() if key != "input_set_sha256"},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ) + "\n"
        ).encode("utf-8")).hexdigest()
        completed, envelope = self.acceptance_prepare(root, binding)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["first_failing_gate"], "input_identity")
        self.assertEqual(envelope["data"]["error_code"], "legacy_provider_unsupported")

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

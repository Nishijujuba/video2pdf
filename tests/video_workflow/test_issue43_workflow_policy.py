from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_case_dir
from scripts import issue43_exit_evidence_contract as contract
from tests.video_workflow._issue43_git_authority import (
    build_current_global_gate_authority,
    commit_later_implementation_change,
)
from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher


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
        canonical = PROJECT_ROOT / "evidence/global-gate/exit-evidence-manifest.json"
        value = json.loads(canonical.read_text(encoding="utf-8"))
        value.update(changes)
        return _write(root / "exit-evidence.json", value)

    def activate(self, root: Path, evidence: Path | None = None, *extra: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        return _run(
            "global-gate-activate", "--control-store-root", str(root),
            "--exit-evidence", str(evidence or PROJECT_ROOT / "evidence/global-gate/exit-evidence-manifest.json"),
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

    def test_activation_rejects_nonexistent_implementation_commit_before_cas(self) -> None:
        # scenario_id: nonexistent_implementation_commit
        # One contradiction: the otherwise accepted policy-shaped manifest names no Git commit.
        root = new_case_dir(self.id(), label="issue43-policy")
        completed, envelope = self.activate(root, self.evidence(root, implementation_commit="2" * 40))
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["first_failing_gate"], "implementation_lineage")
        self.assertEqual(envelope["data"]["error_code"], "implementation_commit_invalid")
        self.assertFalse((root / "global-gate-control.sqlite3").exists())

    def test_activation_rejects_stale_provenance_before_cas(self) -> None:
        scenarios = (
            ("command_log_missing", "command_log", "command_log_missing"),
            ("command_log_sha", "command_log", "command_log_sha256_stale"),
            ("command_log_marker", "command_log_provenance", "command_log_provenance_invalid"),
            ("fixture_sha", "fixture_fingerprint", "fixture_sha256_stale"),
            ("artifact_fingerprint", "artifact_fingerprints", "artifact_fingerprints_stale"),
        )
        for scenario, gate, code in scenarios:
            with self.subTest(scenario=scenario):
                root = new_case_dir(f"{self.id()}-{scenario}", label="issue43-policy")
                value = json.loads((PROJECT_ROOT / "evidence/global-gate/exit-evidence-manifest.json").read_text(encoding="utf-8"))
                if scenario == "command_log_missing":
                    value["commands"][0]["log"]["path"] = "evidence/global-gate/logs/absent.log"
                elif scenario == "command_log_sha":
                    value["commands"][0]["log"]["sha256"] = "0" * 64
                elif scenario == "command_log_marker":
                    log = root / "markerless.log"
                    log.write_text("tests passed without a commit marker\n", encoding="utf-8")
                    value["commands"][0]["log"] = {
                        "role": "command_log",
                        "path": log.relative_to(PROJECT_ROOT).as_posix(),
                        "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                    }
                elif scenario == "fixture_sha":
                    value["fixtures"][0]["sha256"] = "0" * 64
                else:
                    value["artifact_fingerprints"][0]["sha256"] = "0" * 64
                evidence = _write(root / "exit-evidence.json", value)
                completed, envelope = self.activate(root, evidence)
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(envelope["data"]["first_failing_gate"], gate)
                self.assertEqual(envelope["data"]["error_code"], code)
                self.assertFalse((root / "global-gate-control.sqlite3").exists())

    def test_activation_rejects_publication_followed_by_an_implementation_commit(self) -> None:
        # scenario_id: stale_after_publication; the later implementation commit is the only contradiction.
        root = new_case_dir(self.id(), label="issue43-policy")
        repository, manifest = build_current_global_gate_authority(root)
        commit_later_implementation_change(repository)
        with self.assertRaises(Exception) as raised:
            GlobalGatePublisher(project_root=repository).activate(
                control_store_root=root,
                exit_evidence=manifest,
                activated_at="2026-08-03T00:00:00Z",
            )
        error = raised.exception
        self.assertEqual(error.data["first_failing_gate"], "implementation_currentness")
        self.assertEqual(error.data["error_code"], "evidence_publication_not_current")
        self.assertFalse((root / "global-gate-control.sqlite3").exists())

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
        evidence = PROJECT_ROOT / "evidence/global-gate/exit-evidence-manifest.json"
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
        # A distinct uncommitted byte identity is rejected before it can reach the CAS fence.
        alternate.write_text(alternate.read_text(encoding="utf-8") + " ", encoding="utf-8")
        completed, envelope = self.activate(root, alternate)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["first_failing_gate"], "evidence_paths")
        self.assertEqual(envelope["data"]["error_code"], "evidence_paths_stale")

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

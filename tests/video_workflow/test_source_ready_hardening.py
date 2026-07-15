from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"
FIXTURE = (
    PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "source-ready-tracer"
)
CONTRACT_FIXTURES = PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "contracts"
TEST_RUNS = PROJECT_ROOT / "待删除" / "kernel-hardening-test-runs"


def new_test_root(label: str) -> Path:
    root = TEST_RUNS / f"{label}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def run_cli(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout is not one JSON result envelope: {completed.stdout!r}; "
            f"stderr={completed.stderr!r}"
        ) from exc
    return completed, envelope


class BootstrapAndStoreHardeningTests(unittest.TestCase):
    def _probe(self, workspace: Path, request_id: str = "hardening") -> Path:
        completed, envelope = run_cli(
            "bootstrap-probe",
            "--workspace-root",
            str(workspace),
            "--fixture",
            str(FIXTURE),
            "--task-start",
            "2026-07-15T01:02:03+08:00",
            "--request-id",
            request_id,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return Path(envelope["data"]["probe_record"])

    def test_control_store_check_is_read_only_when_store_is_absent(self) -> None:
        workspace = new_test_root("store-absent") / "workspace"

        completed, envelope = run_cli(
            "control-store-check", "--workspace-root", str(workspace)
        )

        self.assertEqual(completed.returncode, 50)
        self.assertEqual(envelope["classification"], "control_store_unavailable")
        self.assertFalse((workspace / ".workflow-control").exists())

    def test_first_bootstrap_explicitly_creates_bound_store_marker_and_database(self) -> None:
        workspace = new_test_root("store-bootstrap") / "workspace"
        self._probe(workspace)
        marker_path = workspace / ".workflow-control" / "control-store.json"
        database_path = workspace / ".workflow-control" / "control.sqlite3"

        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["schema_name"], "control-store-marker")
        self.assertEqual(marker["database_relpath"], ".workflow-control/control.sqlite3")
        with sqlite3.connect(database_path) as connection:
            stored_id = connection.execute(
                "SELECT value FROM control_store_metadata WHERE key='store_id'"
            ).fetchone()[0]
        self.assertEqual(stored_id, marker["store_id"])

    def test_marker_without_database_fails_closed_without_replacement(self) -> None:
        root = new_test_root("store-loss")
        workspace = root / "workspace"
        self._probe(workspace)
        database_path = workspace / ".workflow-control" / "control.sqlite3"
        displaced = root / "待删除" / "control.sqlite3.missing"
        displaced.parent.mkdir(parents=True, exist_ok=True)
        database_path.replace(displaced)

        completed, envelope = run_cli(
            "control-store-check", "--workspace-root", str(workspace)
        )

        self.assertEqual(completed.returncode, 50)
        self.assertEqual(envelope["classification"], "control_store_unavailable")
        self.assertFalse(database_path.exists())

    def test_loaded_probe_schema_and_exact_fixture_identity_are_both_enforced(self) -> None:
        root = new_test_root("probe-tamper")
        workspace = root / "workspace"
        probe_path = self._probe(workspace)
        original = json.loads(probe_path.read_text(encoding="utf-8"))

        extra = dict(original, unexpected="value")
        probe_path.write_text(json.dumps(extra), encoding="utf-8")
        completed, envelope = run_cli(
            "source-import",
            "--workspace-root",
            str(workspace),
            "--probe",
            str(probe_path),
            "--fixture",
            str(FIXTURE),
        )
        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")

        tampered = dict(original, original_title="Schema-valid forged title")
        probe_path.write_text(json.dumps(tampered), encoding="utf-8")
        completed, envelope = run_cli(
            "source-import",
            "--workspace-root",
            str(workspace),
            "--probe",
            str(probe_path),
            "--fixture",
            str(FIXTURE),
        )
        self.assertEqual(completed.returncode, 30)
        self.assertEqual(envelope["classification"], "identity_or_path_conflict")


class PersistenceHardeningTests(unittest.TestCase):
    def test_intent_transition_uses_expected_state_compare_and_swap(self) -> None:
        from video2pdf_workflow_kernel import KernelConflict, VideoWorkflowKernel

        workspace = new_test_root("intent-cas") / "workspace"
        kernel = VideoWorkflowKernel(workspace)
        probe = kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="intent-cas",
        )
        with self.assertRaises(Exception):
            kernel.initialize_verified_import(
                probe=probe,
                fixture=FIXTURE,
                fault_point="after_intent_prepared",
            )
        intent = kernel.control_store.intent_for_run(probe.run_id)
        self.assertEqual(intent["state"], "PREPARED")
        kernel.control_store.transition_intent(
            intent["intent_id"], expected_state="PREPARED", new_state="PUBLISHED"
        )
        with self.assertRaises(KernelConflict):
            kernel.control_store.transition_intent(
                intent["intent_id"], expected_state="PREPARED", new_state="COMMITTED"
            )

    def test_committed_binding_with_missing_run_record_blocks_retry(self) -> None:
        from video2pdf_workflow_kernel import KernelConflict, VideoWorkflowKernel

        root = new_test_root("committed-loss")
        kernel = VideoWorkflowKernel(root / "workspace")
        first = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="committed-loss",
        )
        run_record = first.run_dir / "workflow" / "run.json"
        displaced = first.run_dir / "待删除" / "run.json.missing"
        run_record.replace(displaced)

        with self.assertRaises(KernelConflict):
            kernel.trace_source_ready(
                fixture=FIXTURE,
                task_start="2026-07-15T01:02:03+08:00",
                request_id="committed-loss",
            )
        self.assertEqual(
            kernel.control_store.intent_for_run(first.run_id)["state"], "COMMITTED"
        )
        self.assertIsNotNone(kernel.control_store.binding_for_run(first.run_id))

    def test_published_intent_with_missing_canonical_output_is_never_aborted(self) -> None:
        from video2pdf_workflow_kernel import (
            InitializationFault,
            KernelConflict,
            VideoWorkflowKernel,
        )

        root = new_test_root("published-loss")
        kernel = VideoWorkflowKernel(root / "workspace")
        probe = kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="published-loss",
        )
        with self.assertRaises(InitializationFault):
            kernel.initialize_verified_import(
                probe=probe,
                fixture=FIXTURE,
                fault_point="after_run_record_commit_marker",
            )
        intent = kernel.control_store.intent_for_run(probe.run_id)
        output = Path(intent["output_path"])
        displaced = root / "待删除" / "published-output.missing"
        displaced.parent.mkdir(parents=True, exist_ok=True)
        output.replace(displaced)

        with self.assertRaises(KernelConflict):
            kernel.reconcile_initialization(probe.run_id)
        self.assertEqual(
            kernel.control_store.intent_for_run(probe.run_id)["state"], "PUBLISHED"
        )
        self.assertIsNotNone(kernel.control_store.binding_for_run(probe.run_id))


class ContractAndPathHardeningTests(unittest.TestCase):
    def test_collision_suffix_preserves_full_timestamp_at_96_utf16_units(self) -> None:
        from video2pdf_workflow_kernel.scaffold import output_name
        from video2pdf_workflow_kernel.utils import utf16_units

        value = output_name(
            original_title="𐐀" * 100,
            timestamp="20260715_010203",
            adapter_id="fixture",
            item_id="offline-source-ready-001",
            max_units=96,
            collision_suffix="_r1234abcd",
        )

        self.assertLessEqual(utf16_units(value), 96)
        self.assertTrue(value.endswith("_20260715_010203_r1234abcd"))

    def test_registry_rejects_unknown_registered_invariant(self) -> None:
        root = new_test_root("unknown-invariant")
        registry = json.loads(
            (PROJECT_ROOT / "schemas/video-workflow/registry.v1.json").read_text(
                encoding="utf-8"
            )
        )
        registry["contracts"][3]["invariants"] = ["unknown-invariant"]
        registry_path = root / "registry.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        completed, envelope = run_cli(
            "contracts-check", "--registry", str(registry_path)
        )

        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")

    def test_registered_path_and_freshness_invariants_reject_schema_valid_values(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        contracts = ContractRegistry(PROJECT_ROOT)
        contracts.check()
        source = json.loads(
            (CONTRACT_FIXTURES / "source-manifest.valid.json").read_text(encoding="utf-8")
        )
        artifact_plan = json.loads(
            (CONTRACT_FIXTURES / "artifact-plan.valid.json").read_text(encoding="utf-8")
        )
        run_record = json.loads(
            (CONTRACT_FIXTURES / "run-record.valid.json").read_text(encoding="utf-8")
        )

        invalid_paths = (
            "source/../workflow/run.json",
            "C:/absolute/source.bin",
            "source\\escape.bin",
            "source/CON/file.bin",
            "source/trailing. ",
        )
        for value in invalid_paths:
            with self.subTest(path=value), self.assertRaises(ContractError):
                mutated = json.loads(json.dumps(source))
                mutated["artifacts"][0]["path"] = value
                contracts.validate("source-manifest", mutated)

        artifact_plan["artifacts"][0]["path"] = "source/../workflow/run.json"
        with self.assertRaises(ContractError):
            contracts.validate("artifact-plan", artifact_plan)

        run_record["checkpoints"]["source_ready"]["evidence_sha256"] = "f" * 64
        self.assertNotEqual(
            run_record["checkpoints"]["source_ready"]["evidence_sha256"],
            run_record["artifact_generations"]["source_manifest"]["sha256"],
        )
        with self.assertRaises(ContractError):
            contracts.validate("run-record", run_record)


class HealthAndLauncherHardeningTests(unittest.TestCase):
    def test_health_proves_exact_timeout_lock_contention_and_same_volume_replace(self) -> None:
        from video2pdf_workflow_kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.control_store import BUSY_TIMEOUT_MS

        root = new_test_root("health-probes")
        kernel = VideoWorkflowKernel(root / "workspace")
        kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="health-probes",
        )

        report = kernel.control_store.check()

        self.assertEqual(int(report.pragmas["busy_timeout"]), BUSY_TIMEOUT_MS)
        self.assertTrue(report.lock_contention_checked)
        self.assertTrue(report.atomic_replace_checked)
        self.assertTrue(
            any((root / "待删除" / "atomic staging").rglob("replace-target"))
        )

    def test_python_no_site_dependency_failure_is_one_machine_envelope(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-S", "-X", "utf8", "-B", str(CLI), "contracts-check"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        envelope = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 70)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(envelope["schema_name"], "workflow-result")
        self.assertEqual(envelope["schema_version"], "1.0.0")
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["classification"], "runtime_dependency_unavailable")


if __name__ == "__main__":
    unittest.main()

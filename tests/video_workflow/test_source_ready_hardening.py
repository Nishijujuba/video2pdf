from __future__ import annotations

import json
import importlib.util
import hashlib
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
import uuid
from unittest import mock


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
        self.assertEqual(marker["schema_name"], "control-store-identity")
        self.assertEqual(marker["record_kind"], "marker")
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

    def test_external_identity_anchor_blocks_recreation_after_full_store_loss(self) -> None:
        from video2pdf_workflow_kernel.errors import ControlStoreUnavailable
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel

        root = new_test_root("store-full-loss")
        workspace = root / "workspace"
        self._probe(workspace)
        anchors = list((root / ".video-workflow-control-anchors").glob("*.json"))
        self.assertEqual(len(anchors), 1)
        anchor = json.loads(anchors[0].read_text(encoding="utf-8"))
        self.assertEqual(anchor["schema_name"], "control-store-identity")
        self.assertEqual(anchor["record_kind"], "anchor")

        displaced = root / "待删除" / "lost-control-store"
        displaced.parent.mkdir(parents=True, exist_ok=True)
        (workspace / ".workflow-control").replace(displaced)

        with self.assertRaises(ControlStoreUnavailable):
            kernel = VideoWorkflowKernel(workspace)
            kernel.bootstrap_probe(
                fixture=FIXTURE,
                task_start="2026-07-15T01:02:03+08:00",
                request_id="must-not-recreate",
            )
        self.assertFalse((workspace / ".workflow-control").exists())

    def test_anchor_identity_tamper_blocks_existing_store(self) -> None:
        from video2pdf_workflow_kernel.errors import ControlStoreUnavailable
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel

        root = new_test_root("store-anchor-tamper")
        workspace = root / "workspace"
        self._probe(workspace)
        anchor_path = next(
            (root / ".video-workflow-control-anchors").glob("*.json")
        )
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        anchor["store_id"] = "0" * 64
        anchor_path.write_text(json.dumps(anchor), encoding="utf-8")

        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(workspace)

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
    def test_publication_expectations_are_bound_before_output_publish(self) -> None:
        from video2pdf_workflow_kernel import InitializationFault, VideoWorkflowKernel

        root = new_test_root("ipb")
        kernel = VideoWorkflowKernel(root / "workspace")
        probe = kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="intent-publication-bindings",
        )
        with self.assertRaises(InitializationFault):
            kernel.initialize_verified_import(
                probe=probe,
                fixture=FIXTURE,
                fault_point="after_contracts_written",
            )
        intent = kernel.control_store.intent_for_run(probe.run_id)
        self.assertEqual(intent["state"], "PREPARED")
        self.assertRegex(intent["expected_run_record_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(intent["source_manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(intent["canonical_platform"], "fixture")
        self.assertEqual(intent["canonical_item_id"], probe.canonical_item_id)
        self.assertEqual(intent["source_identity"], probe.fixture_manifest_sha256)

    def test_published_prepared_run_identity_tamper_blocks_recovery(self) -> None:
        from video2pdf_workflow_kernel import (
            InitializationFault,
            KernelConflict,
            VideoWorkflowKernel,
        )
        from video2pdf_workflow_kernel.utils import write_json_atomic

        root = new_test_root("prepared-run-tamper")
        kernel = VideoWorkflowKernel(root / "workspace")
        probe = kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="prepared-run-tamper",
        )
        with self.assertRaises(InitializationFault):
            kernel.initialize_verified_import(
                probe=probe,
                fixture=FIXTURE,
                fault_point="after_output_dir_publish",
            )
        intent = kernel.control_store.intent_for_run(probe.run_id)
        prepared_path = Path(intent["output_path"]) / "待删除/bootstrap/prepared-run.json"
        prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
        prepared["original_title"] = "Schema-valid forged title"
        prepared["normalized_title"] = "Schema_valid forged title"
        write_json_atomic(prepared_path, prepared)

        with self.assertRaises(KernelConflict):
            kernel.reconcile_initialization(probe.run_id)
        self.assertNotEqual(
            kernel.control_store.intent_for_run(probe.run_id)["state"], "COMMITTED"
        )

    def test_committed_self_consistent_rewrite_is_artifact_drift_without_run_mutation(self) -> None:
        from video2pdf_workflow_kernel import ArtifactDrift, VideoWorkflowKernel
        from video2pdf_workflow_kernel.utils import write_json_atomic

        root = new_test_root("ccd")
        kernel = VideoWorkflowKernel(root / "workspace")
        result = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="committed-coordinated-drift",
        )
        subtitle = result.run_dir / "source/subtitles/subtitle.en.srt"
        subtitle.write_bytes(subtitle.read_bytes() + b"\ncoordinated drift\n")
        manifest_path = result.run_dir / "source/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact = next(
            item for item in manifest["artifacts"] if item["logical_id"] == "subtitle_en"
        )
        artifact["sha256"] = hashlib.sha256(subtitle.read_bytes()).hexdigest()
        artifact["size_bytes"] = subtitle.stat().st_size
        manifest_sha = write_json_atomic(manifest_path, manifest)
        run_path = result.run_dir / "workflow/run.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        run_record["artifact_generations"]["source_manifest"]["sha256"] = manifest_sha
        run_record["checkpoints"]["source_ready"]["evidence_sha256"] = manifest_sha
        write_json_atomic(run_path, run_record)

        with self.assertRaises(ArtifactDrift):
            kernel.reconcile_run(result.run_dir)
        unchanged = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertEqual(unchanged["checkpoints"]["source_ready"]["status"], "current")
        with sqlite3.connect(kernel.control_store.path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM run_state_mutation_intents WHERE run_id=?",
                (result.run_id,),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_live_kernel_preflight_detects_anchor_and_store_displacement(self) -> None:
        from video2pdf_workflow_kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.errors import ControlStoreUnavailable

        for displaced_kind in ("anchor", "store"):
            with self.subTest(displaced_kind=displaced_kind):
                root = new_test_root(f"lp-{displaced_kind[0]}")
                workspace = root / "workspace"
                kernel = VideoWorkflowKernel(workspace)
                probe = kernel.bootstrap_probe(
                    fixture=FIXTURE,
                    task_start="2026-07-15T01:02:03+08:00",
                    request_id=f"live-preflight-{displaced_kind}",
                )
                destination = root / "待删除" / displaced_kind
                destination.parent.mkdir(parents=True, exist_ok=True)
                if displaced_kind == "anchor":
                    kernel.control_store.anchor_path.replace(destination)
                else:
                    kernel.control_store.control_dir.replace(destination)

                with self.assertRaises(ControlStoreUnavailable):
                    kernel.initialize_verified_import(probe=probe, fixture=FIXTURE)
                completed, envelope = run_cli(
                    "source-import",
                    "--workspace-root",
                    str(workspace),
                    "--probe",
                    str(probe.record_path),
                    "--fixture",
                    str(FIXTURE),
                )
                self.assertEqual(completed.returncode, 50)
                self.assertEqual(
                    envelope["classification"], "control_store_unavailable"
                )

    def test_slice1_v1_control_store_migrates_forward_to_intent_identity_columns(self) -> None:
        from video2pdf_workflow_kernel import VideoWorkflowKernel

        root = new_test_root("store-v1-migration")
        workspace = root / "workspace"
        kernel = VideoWorkflowKernel(workspace)
        kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="store-v1-migration",
        )
        database = kernel.control_store.path
        expected_columns = {
            "expected_run_record_sha256",
            "canonical_platform",
            "canonical_item_id",
            "source_identity",
            "source_manifest_sha256",
        }
        with sqlite3.connect(database) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(initialization_intents)"
                ).fetchall()
            }
            if expected_columns.issubset(columns):
                for column in expected_columns:
                    connection.execute(
                        f"ALTER TABLE initialization_intents DROP COLUMN {column}"
                    )
                connection.execute("DELETE FROM schema_migrations WHERE version>=2")

        migrated = VideoWorkflowKernel(workspace)
        self.assertEqual(migrated.control_store.check().schema_version, 4)
        with sqlite3.connect(database) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(initialization_intents)"
                ).fetchall()
            }
            task_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'task_%'"
                ).fetchall()
            }
        self.assertTrue(expected_columns.issubset(columns))
        self.assertEqual(
            task_tables,
            {"task_claims", "task_attempts", "task_promotion_intents"},
        )

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
        kernel.control_store.bind_publication_expectations(
            intent["intent_id"],
            expected_run_record_sha256="0" * 64,
            canonical_platform="fixture",
            canonical_item_id=probe.canonical_item_id,
            source_identity=probe.fixture_manifest_sha256,
            source_manifest_sha256="1" * 64,
        )
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
    def test_artifact_plan_bindings_are_one_shared_immutable_six_item_source(self) -> None:
        from video2pdf_workflow_kernel.artifact_plan import ARTIFACT_PLAN_BINDINGS
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel

        self.assertIsInstance(ARTIFACT_PLAN_BINDINGS, tuple)
        self.assertEqual(len(ARTIFACT_PLAN_BINDINGS), 6)
        plan = VideoWorkflowKernel._artifact_plan("0" * 32)
        self.assertEqual(
            [item["logical_id"] for item in plan["artifacts"]],
            [binding.logical_id for binding in ARTIFACT_PLAN_BINDINGS],
        )
    def test_registry_requires_exact_canonical_contract_name_version_set(self) -> None:
        canonical = json.loads(
            (PROJECT_ROOT / "schemas/video-workflow/registry.v1.json").read_text(
                encoding="utf-8"
            )
        )
        root = new_test_root("registry-closed-set")
        partial = json.loads(json.dumps(canonical))
        partial["contracts"].pop()
        partial_path = root / "partial.json"
        partial_path.write_text(json.dumps(partial), encoding="utf-8")

        completed, envelope = run_cli(
            "contracts-check", "--registry", str(partial_path)
        )

        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")

        duplicate = json.loads(json.dumps(canonical))
        duplicate["contracts"].append(dict(duplicate["contracts"][-1]))
        duplicate_path = root / "duplicate.json"
        duplicate_path.write_text(json.dumps(duplicate), encoding="utf-8")
        completed, envelope = run_cli(
            "contracts-check", "--registry", str(duplicate_path)
        )
        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")

    def test_contracts_check_reports_registry_closed_set_completeness(self) -> None:
        completed, envelope = run_cli("contracts-check")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(envelope["data"]["registry_complete"])
        self.assertEqual(
            set(envelope["data"]["registered_schema_names"]),
            {
                "artifact-plan",
                "bootstrap-record",
                "common-definitions",
                "control-store-identity",
                "fixture-package",
                "run-record",
                "run-record-task-capable",
                "scaffold-contract",
                "scaffold-ledger",
                "source-acquisition-judgment-patch",
                "source-manifest",
                "subagent-task-envelope",
                "task-attempt",
                "task-completion-record",
                "task-promotion-journal",
                "workflow-result",
            },
        )

    def test_control_store_anchor_and_marker_share_a_registered_schema(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry

        root = new_test_root("store-identity-schema")
        workspace = root / "workspace"
        completed, _ = run_cli(
            "bootstrap-probe",
            "--workspace-root",
            str(workspace),
            "--fixture",
            str(FIXTURE),
            "--task-start",
            "2026-07-15T01:02:03+08:00",
            "--request-id",
            "store-identity-schema",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        contracts = ContractRegistry(PROJECT_ROOT)
        contracts.check()
        marker = json.loads(
            (workspace / ".workflow-control/control-store.json").read_text(
                encoding="utf-8"
            )
        )
        anchor_path = next(
            (root / ".video-workflow-control-anchors").glob("*.json")
        )
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        contracts.validate("control-store-identity", marker)
        contracts.validate("control-store-identity", anchor)
        self.assertEqual(marker["record_kind"], "marker")
        self.assertEqual(anchor["record_kind"], "anchor")

    def test_artifact_plan_is_the_exact_slice1_artifact_set(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        contracts = ContractRegistry(PROJECT_ROOT)
        contracts.check()
        positive = json.loads(
            (CONTRACT_FIXTURES / "artifact-plan.valid.json").read_text(
                encoding="utf-8"
            )
        )
        expected = {
            "artifact_plan",
            "bootstrap_record",
            "run_record",
            "scaffold_contract",
            "scaffold_ledger",
            "source_manifest",
        }
        self.assertEqual(
            {item["logical_id"] for item in positive["artifacts"]}, expected
        )
        contracts.validate("artifact-plan", positive)

        missing = json.loads(json.dumps(positive))
        missing["artifacts"].pop()
        with self.assertRaises(ContractError):
            contracts.validate("artifact-plan", missing)
        extra = json.loads(json.dumps(positive))
        extra["artifacts"].append(
            {
                "logical_id": "unexpected",
                "path": "workflow/unexpected.json",
                "schema_name": "run-record",
                "generator": "kernel:init-run",
                "earliest_checkpoint": "run_initialized",
            }
        )
        with self.assertRaises(ContractError):
            contracts.validate("artifact-plan", extra)

        root = new_test_root("ap")
        completed, envelope = run_cli(
            "trace-source-ready",
            "--workspace-root",
            str(root / "w"),
            "--fixture",
            str(FIXTURE),
            "--task-start",
            "2026-07-15T01:02:03+08:00",
            "--request-id",
            "artifact-plan-runtime",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run_dir = Path(envelope["data"]["run_dir"])
        generated = json.loads(
            (run_dir / "workflow/artifact-plan.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {item["logical_id"] for item in generated["artifacts"]}, expected
        )
        self.assertTrue((run_dir / "workflow/scaffold-contract.json").is_file())

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

    def test_fixture_paths_are_schema_and_semantically_contained(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        contracts = ContractRegistry(PROJECT_ROOT)
        contracts.check()
        fixture = json.loads(
            (CONTRACT_FIXTURES / "fixture-package.valid.json").read_text(
                encoding="utf-8"
            )
        )
        invalid_paths = (
            "media/..\\..\\outside.bin",
            "/absolute.bin",
            "media/./video.fixture",
            "media/../video.fixture",
            "media/CON/file.bin",
            "media/con/file.bin",
            "media/trailing. ",
        )
        for path in invalid_paths:
            with self.subTest(path=path), self.assertRaises(ContractError):
                mutated = json.loads(json.dumps(fixture))
                mutated["artifacts"][0]["path"] = path
                contracts.validate("fixture-package", mutated)

    def test_fixture_adapter_runtime_rejects_backslash_escape_even_with_matching_hash(self) -> None:
        from video2pdf_workflow_kernel.adapters import FixturePlatformAdapter
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        root = new_test_root("fixture-runtime-escape")
        copied = root / "fixture"
        shutil.copytree(FIXTURE, copied)
        outside = root / f"outside-{uuid.uuid4().hex}.bin"
        outside.write_bytes(b"outside fixture root")
        manifest_path = copied / "fixture.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["path"] = (
            f"media/..\\..\\{outside.name}"
        )
        manifest["artifacts"][0]["sha256"] = __import__("hashlib").sha256(
            outside.read_bytes()
        ).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        contracts = ContractRegistry(PROJECT_ROOT)
        contracts.check()
        with mock.patch.object(contracts, "validate", return_value=None):
            with self.assertRaises(ContractError):
                FixturePlatformAdapter(copied, contracts)

    def test_run_record_output_path_must_be_canonical_absolute(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        contracts = ContractRegistry(PROJECT_ROOT)
        contracts.check()
        run_record = json.loads(
            (CONTRACT_FIXTURES / "run-record.valid.json").read_text(encoding="utf-8")
        )
        for output_path in (
            str((PROJECT_ROOT / "待删除" / "absolute-run").resolve()),
            r"\\server\share\workspace\run",
        ):
            accepted = json.loads(json.dumps(run_record))
            accepted["output_path"] = output_path
            contracts.validate("run-record", accepted)
        for output_path in (
            "abc",
            "relative/workspace/run",
            "D:\\workspace\\..\\escape",
            "D:drive-relative",
        ):
            with self.subTest(output_path=output_path), self.assertRaises(ContractError):
                mutated = json.loads(json.dumps(run_record))
                mutated["output_path"] = output_path
                contracts.validate("run-record", mutated)


class HealthAndLauncherHardeningTests(unittest.TestCase):
    def test_evidence_collector_appends_byte_safe_implementation_provenance(self) -> None:
        collector_path = PROJECT_ROOT / "scripts/collect_slice1_exit_evidence.py"
        spec = importlib.util.spec_from_file_location("slice1_collector_test", collector_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        completed = subprocess.CompletedProcess(
            args=["command"], returncode=0, stdout=b"stdout\n", stderr=b""
        )
        with mock.patch.object(module.subprocess, "run", return_value=completed):
            captured = module.run_commands("a" * 40)
        self.assertEqual(len(captured), len(module.COMMANDS))
        for item in captured:
            self.assertIsInstance(item["raw"], bytes)
            self.assertIn(
                b"EVIDENCE_IMPLEMENTATION_COMMIT: " + b"a" * 40,
                item["raw"],
            )

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

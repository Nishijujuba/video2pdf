from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
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
EXIT_V2_VALIDATOR = PROJECT_ROOT / "scripts" / "validate_slice_exit_evidence.py"
FIXTURE = (
    PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "source-ready-tracer"
)
TEST_RUNS = PROJECT_ROOT / "待删除" / "kernel-test-runs"
SCHEMA_ROOT = PROJECT_ROOT / "schemas" / "video-workflow"


def utf16_units(value: str | Path) -> int:
    return len(str(value).encode("utf-16-le")) // 2


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def new_test_root(label: str) -> Path:
    root = TEST_RUNS / f"{label}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def run_cli(*arguments: str, cwd: Path = PROJECT_ROOT) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - diagnostic assertion
        raise AssertionError(
            f"stdout is not one JSON result envelope: {completed.stdout!r}; "
            f"stderr={completed.stderr!r}"
        ) from exc
    return completed, envelope


class ContractsCliTests(unittest.TestCase):
    def test_contracts_check_uses_pinned_standards_runtime_and_all_examples(self) -> None:
        completed, envelope = run_cli("contracts-check")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(envelope["schema_name"], "workflow-result")
        self.assertEqual(envelope["schema_version"], "1.0.0")
        self.assertEqual(envelope["command"], "contracts-check")
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["classification"], "contracts_valid")
        self.assertEqual(envelope["data"]["jsonschema_version"], "4.26.0")
        self.assertEqual(importlib.metadata.version("jsonschema"), "4.26.0")
        self.assertGreaterEqual(envelope["data"]["contract_count"], 7)
        self.assertEqual(
            envelope["data"]["positive_examples_validated"],
            envelope["data"]["contract_count"],
        )
        self.assertEqual(
            envelope["data"]["negative_examples_rejected"],
            envelope["data"]["contract_count"],
        )

    def test_contracts_check_rejects_unknown_contract_version(self) -> None:
        root = new_test_root("unknown-contract-version")
        registry = json.loads(
            (SCHEMA_ROOT / "registry.v1.json").read_text(encoding="utf-8")
        )
        registry["contracts"][0]["schema_version"] = "99.0.0"
        path = root / "registry.json"
        path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")

        completed, envelope = run_cli("contracts-check", "--registry", str(path))

        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["classification"], "contract_invalid")

    def test_contracts_check_rejects_unresolved_or_remote_reference(self) -> None:
        root = new_test_root("bad-schema-ref")
        registry = json.loads(
            (SCHEMA_ROOT / "registry.v1.json").read_text(encoding="utf-8")
        )
        schema_path = root / "bad.schema.json"
        schema_path.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": "https://video2pdf.local/schemas/video-workflow/v1/common.v1.schema.json",
                    "$ref": "https://unregistered.invalid/remote.schema.json",
                }
            ),
            encoding="utf-8",
        )
        registry["contracts"][0]["schema_path"] = str(schema_path)
        registry_path = root / "registry.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        completed, envelope = run_cli(
            "contracts-check", "--registry", str(registry_path)
        )

        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")

    def test_registered_schema_examples_are_positive_and_negative(self) -> None:
        registry = json.loads(
            (SCHEMA_ROOT / "registry.v1.json").read_text(encoding="utf-8")
        )
        for contract in registry["contracts"]:
            if contract["kind"] != "contract":
                continue
            with self.subTest(contract=contract["schema_name"]):
                self.assertTrue((PROJECT_ROOT / contract["positive_example"]).is_file())
                self.assertTrue((PROJECT_ROOT / contract["negative_example"]).is_file())

    def test_generic_exit_evidence_v2_schema_fixtures(self) -> None:
        valid = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(EXIT_V2_VALIDATOR),
                str(FIXTURE.parent / "exit_evidence_manifest.v2.valid.json"),
                "--schema-only",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        invalid = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(EXIT_V2_VALIDATOR),
                str(FIXTURE.parent / "exit_evidence_manifest.v2.invalid.json"),
                "--schema-only",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("INVALID:", invalid.stderr)

    def test_parser_failure_uses_the_same_machine_result_envelope(self) -> None:
        completed, envelope = run_cli("trace-source-ready")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(envelope["schema_name"], "workflow-result")
        self.assertEqual(envelope["schema_version"], "1.0.0")
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["classification"], "usage_error")


class SourceReadyCliTests(unittest.TestCase):
    def _trace(
        self,
        root: Path,
        *,
        request_id: str = "request-a",
        task_start: str = "2026-07-15T01:02:03+08:00",
        extra: tuple[str, ...] = (),
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        return run_cli(
            "trace-source-ready",
            "--workspace-root",
            str(root / "workspace"),
            "--fixture",
            str(FIXTURE),
            "--task-start",
            task_start,
            "--request-id",
            request_id,
            *extra,
        )

    def test_offline_fixture_reaches_current_source_ready(self) -> None:
        root = new_test_root("source-ready-positive")

        completed, envelope = self._trace(root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(envelope["classification"], "source_ready")
        self.assertEqual(envelope["data"]["checkpoint"], "source_ready")
        self.assertEqual(envelope["data"]["checkpoint_status"], "current")
        run_dir = Path(envelope["data"]["run_dir"])
        run_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(run_record["run_id"], envelope["data"]["run_id"])
        self.assertEqual(run_record["output_path"], str(run_dir.resolve()))
        self.assertEqual(run_record["source_acquisition_mode"], "verified_import")
        self.assertEqual(
            run_record["checkpoints"]["source_ready"]["status"], "current"
        )
        source_generation = run_record["artifact_generations"]["source_manifest"]
        self.assertEqual(source_generation["generation"], 1)
        self.assertEqual(
            source_generation["sha256"], sha256(run_dir / "source" / "manifest.json")
        )
        self.assertTrue((run_dir / "workflow" / "artifact-plan.json").is_file())
        self.assertTrue((run_dir / "source" / "manifest.json").is_file())

    def test_split_public_commands_cover_probe_import_init_reconcile_and_store_health(self) -> None:
        root = new_test_root("public-commands")
        workspace = root / "workspace"
        completed, probe_envelope = run_cli(
            "bootstrap-probe",
            "--workspace-root",
            str(workspace),
            "--fixture",
            str(FIXTURE),
            "--task-start",
            "2026-07-15T01:02:03+08:00",
            "--request-id",
            "split-public",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(probe_envelope["classification"], "probe_complete")
        probe_path = probe_envelope["data"]["probe_record"]

        completed, envelope = run_cli(
            "control-store-check", "--workspace-root", str(workspace)
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(envelope["classification"], "control_store_healthy")

        completed, import_envelope = run_cli(
            "source-import",
            "--workspace-root",
            str(workspace),
            "--probe",
            probe_path,
            "--fixture",
            str(FIXTURE),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(import_envelope["classification"], "source_ready")

        completed, init_envelope = run_cli(
            "init-run",
            "--workspace-root",
            str(workspace),
            "--probe",
            probe_path,
            "--fixture",
            str(FIXTURE),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(init_envelope["classification"], "already_source_ready")

        completed, reconcile_envelope = run_cli(
            "reconcile-run", "--run-dir", import_envelope["data"]["run_dir"]
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(reconcile_envelope["classification"], "source_ready_current")

    def test_complete_scaffold_matches_contract_and_kernel_creation_ledger(self) -> None:
        root = new_test_root("scaffold")
        completed, envelope = self._trace(root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run_dir = Path(envelope["data"]["run_dir"])
        scaffold = json.loads(
            (SCHEMA_ROOT / "v1" / "scaffold.v1.json").read_text(encoding="utf-8")
        )
        expected = set(scaffold["managed_directories"])
        actual = {
            path.relative_to(run_dir).as_posix()
            for path in run_dir.rglob("*")
            if path.is_dir()
        }
        self.assertEqual(actual, expected)
        ledger = json.loads(
            (run_dir / "workflow" / "scaffold-ledger.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {item["path"] for item in ledger["directories"]}, expected
        )
        self.assertTrue(
            all(item["created_by"] == "kernel:init-run" for item in ledger["directories"])
        )

    def test_fixture_adapter_has_no_production_capabilities(self) -> None:
        root = new_test_root("adapter-capabilities")
        completed, envelope = self._trace(root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            envelope["data"]["adapter_capabilities"],
            ["offline_probe", "verified_import"],
        )
        completed, envelope = run_cli(
            "adapter-capability-check",
            "--fixture",
            str(FIXTURE),
            "--capability",
            "network_download",
        )
        self.assertEqual(completed.returncode, 30)
        self.assertEqual(envelope["classification"], "capability_forbidden")

    def test_identical_rerun_is_idempotent(self) -> None:
        root = new_test_root("idempotent")
        first, first_envelope = self._trace(root)
        second, second_envelope = self._trace(root)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second_envelope["classification"], "already_source_ready")
        self.assertEqual(
            first_envelope["data"]["run_id"], second_envelope["data"]["run_id"]
        )
        self.assertEqual(
            first_envelope["data"]["run_dir"], second_envelope["data"]["run_dir"]
        )

    def test_identity_and_contract_generation_are_deterministic_across_workspaces(self) -> None:
        first_root = new_test_root("determinism-a")
        second_root = new_test_root("determinism-b")
        first, first_envelope = self._trace(first_root, request_id="stable-request")
        second, second_envelope = self._trace(second_root, request_id="stable-request")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first_envelope["data"]["run_id"], second_envelope["data"]["run_id"])
        first_dir = Path(first_envelope["data"]["run_dir"])
        second_dir = Path(second_envelope["data"]["run_dir"])
        self.assertEqual(first_dir.name, second_dir.name)
        self.assertEqual(
            (first_dir / "workflow/artifact-plan.json").read_bytes(),
            (second_dir / "workflow/artifact-plan.json").read_bytes(),
        )
        self.assertEqual(
            (first_dir / "source/manifest.json").read_bytes(),
            (second_dir / "source/manifest.json").read_bytes(),
        )

    def test_same_second_second_identity_uses_collision_safe_path(self) -> None:
        root = new_test_root("same-second")
        first, first_envelope = self._trace(root, request_id="request-a")
        second, second_envelope = self._trace(root, request_id="request-b")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(
            first_envelope["data"]["run_id"], second_envelope["data"]["run_id"]
        )
        self.assertRegex(Path(second_envelope["data"]["run_dir"]).name, r"_r[0-9a-f]{8}$")

    def test_source_drift_invalidates_source_ready(self) -> None:
        root = new_test_root("source-drift")
        completed, envelope = self._trace(root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run_dir = Path(envelope["data"]["run_dir"])
        subtitle = run_dir / "source" / "subtitles" / "subtitle.en.srt"
        subtitle.write_text(subtitle.read_text(encoding="utf-8") + "\nDRIFT", encoding="utf-8")

        completed, envelope = run_cli("reconcile-run", "--run-dir", str(run_dir))

        self.assertEqual(completed.returncode, 40)
        self.assertEqual(envelope["classification"], "artifact_drift")
        record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(record["checkpoints"]["source_ready"]["status"], "stale")

    def test_moved_imported_source_is_drift_and_not_silently_recreated(self) -> None:
        root = new_test_root("source-missing")
        completed, envelope = self._trace(root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run_dir = Path(envelope["data"]["run_dir"])
        source = run_dir / "source/media/video.fixture"
        displaced = run_dir / "待删除/video.fixture.moved-for-drift-test"
        source.replace(displaced)
        completed, envelope = run_cli("reconcile-run", "--run-dir", str(run_dir))
        self.assertEqual(completed.returncode, 40)
        self.assertEqual(envelope["classification"], "artifact_drift")
        self.assertFalse(source.exists())
        self.assertTrue(displaced.exists())

    def test_unknown_run_contract_version_blocks_resume(self) -> None:
        root = new_test_root("unknown-run-version")
        completed, envelope = self._trace(root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run_dir = Path(envelope["data"]["run_dir"])
        record_path = run_dir / "workflow/run.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["schema_version"] = "99.0.0"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        completed, envelope = run_cli("reconcile-run", "--run-dir", str(run_dir))
        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["classification"], "unknown_contract_version")

    def test_public_reconcile_run_recovers_interrupted_initialization(self) -> None:
        root = new_test_root("public-init-recovery")
        workspace = root / "workspace"
        completed, probe_envelope = run_cli(
            "bootstrap-probe",
            "--workspace-root",
            str(workspace),
            "--fixture",
            str(FIXTURE),
            "--task-start",
            "2026-07-15T01:02:03+08:00",
            "--request-id",
            "public-recovery",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        completed, fault_envelope = run_cli(
            "init-run",
            "--workspace-root",
            str(workspace),
            "--probe",
            probe_envelope["data"]["probe_record"],
            "--fixture",
            str(FIXTURE),
            "--fault-point",
            "after_output_dir_publish",
        )
        self.assertEqual(completed.returncode, 60)
        self.assertEqual(
            fault_envelope["classification"], "injected_initialization_fault"
        )
        completed, recovery_envelope = run_cli(
            "reconcile-run",
            "--workspace-root",
            str(workspace),
            "--run-id",
            probe_envelope["data"]["run_id"],
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(recovery_envelope["classification"], "initialization_reconciled")
        self.assertEqual(recovery_envelope["data"]["outcome"], "new_state_complete")


class PathBudgetCliTests(unittest.TestCase):
    def test_utf16_pairs_reserved_names_and_trailing_characters_are_normalized(self) -> None:
        from video2pdf_workflow_kernel.scaffold import output_name

        candidate = output_name(
            original_title="CON. 𐐀... ",
            timestamp="20260715_010203",
            adapter_id="fixture",
            item_id="offline-source-ready-001",
            max_units=96,
        )
        self.assertLessEqual(utf16_units(candidate), 96)
        self.assertTrue(candidate.endswith("_20260715_010203"))
        self.assertNotIn(candidate.upper(), {"CON", "PRN", "AUX", "NUL"})
        self.assertNotRegex(candidate, r"[ .]$")
        self.assertEqual(utf16_units("𐐀"), 2)

    def _workspace_for_target(self, root: Path, target: int) -> Path:
        scaffold = json.loads(
            (SCHEMA_ROOT / "v1" / "scaffold.v1.json").read_text(encoding="utf-8")
        )
        longest = max(scaffold["reserved_descendant_paths"], key=utf16_units)
        output_name = "A_20260715_010203"
        base = root / "x"
        current = utf16_units(base / output_name / Path(longest))
        pad = target - current + 1
        self.assertGreaterEqual(pad, 1)
        self.assertLessEqual(pad, 200)
        return root / ("x" * pad)

    def _run_at(self, target: int) -> tuple[Path, subprocess.CompletedProcess[str], dict]:
        root = new_test_root(f"path-{target}")
        workspace = self._workspace_for_target(root, target)
        return root, *run_cli(
            "trace-source-ready",
            "--workspace-root",
            str(workspace),
            "--fixture",
            str(FIXTURE),
            "--task-start",
            "2026-07-15T01:02:03+08:00",
            "--request-id",
            f"path-{target}",
            "--title-override",
            "A",
        )

    def test_239_and_240_utf16_unit_paths_pass(self) -> None:
        for target in (239, 240):
            with self.subTest(target=target):
                _, completed, envelope = self._run_at(target)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(envelope["data"]["max_path_utf16_units"], target)

    def test_241_utf16_unit_path_fails_without_partial_run(self) -> None:
        root, completed, envelope = self._run_at(241)
        self.assertEqual(completed.returncode, 30)
        self.assertEqual(envelope["classification"], "path_budget_exceeded")
        workspace = Path(envelope["data"]["workspace_root"])
        created_runs = [
            path
            for path in workspace.glob("*")
            if path.name not in {".workflow-control"}
        ] if workspace.exists() else []
        self.assertEqual(created_runs, [])
        self.assertFalse(any(root.rglob("workflow/run.json")))
        database = workspace / ".workflow-control" / "control.sqlite3"
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM run_bindings").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM initialization_intents").fetchone()[0],
                0,
            )


class SourceReadyDeepModuleTests(unittest.TestCase):
    FAULT_POINTS = (
        "after_intent_prepared",
        "after_scaffold_staged",
        "after_bootstrap_evidence_staged",
        "after_contracts_written",
        "after_output_dir_publish",
        "after_run_record_commit_marker",
        "before_intent_commit",
        "after_intent_commit",
    )

    def test_each_initialization_boundary_recovers_old_or_new_complete_state(self) -> None:
        from video2pdf_workflow_kernel import InitializationFault, VideoWorkflowKernel

        for index, fault_point in enumerate(self.FAULT_POINTS):
            with self.subTest(fault_point=fault_point):
                root = new_test_root(f"f{index}")
                kernel = VideoWorkflowKernel(root / "workspace")
                probe = kernel.bootstrap_probe(
                    fixture=FIXTURE,
                    task_start="2026-07-15T01:02:03+08:00",
                    request_id=f"request-{fault_point}",
                )
                with self.assertRaises(InitializationFault):
                    kernel.initialize_verified_import(
                        probe=probe,
                        fixture=FIXTURE,
                        fault_point=fault_point,
                    )

                result = kernel.reconcile_initialization(probe.run_id)

                self.assertIn(result.outcome, {"old_state_complete", "new_state_complete"})
                intent = kernel.control_store.intent_for_run(probe.run_id)
                self.assertIsNotNone(intent)
                if result.outcome == "old_state_complete":
                    self.assertFalse(result.run_dir.exists())
                    self.assertEqual(intent["state"], "ABORTED")
                    self.assertIsNone(kernel.control_store.binding_for_run(probe.run_id))
                else:
                    self.assertEqual(intent["state"], "COMMITTED")
                    self.assertIsNotNone(kernel.control_store.binding_for_run(probe.run_id))
                    record = json.loads(
                        (result.run_dir / "workflow" / "run.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertEqual(
                        record["checkpoints"]["source_ready"]["status"], "current"
                    )

    def test_binding_conflicts_fail_closed(self) -> None:
        from video2pdf_workflow_kernel import KernelConflict, VideoWorkflowKernel

        root = new_test_root("binding-conflict")
        kernel = VideoWorkflowKernel(root / "workspace")
        first = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="first",
        )
        with self.assertRaises(KernelConflict):
            kernel.control_store.bind_run(
                run_id="f" * 32,
                output_path=first.run_dir,
                initialization_intent_id="conflicting-intent",
            )
        with self.assertRaises(KernelConflict):
            kernel.control_store.bind_run(
                run_id=first.run_id,
                output_path=root / "workspace" / "other",
                initialization_intent_id="conflicting-intent",
            )

    def test_same_second_collision_safe_path_also_fails_when_really_occupied(self) -> None:
        from video2pdf_workflow_kernel import KernelConflict, VideoWorkflowKernel

        root = new_test_root("true-collision")
        kernel = VideoWorkflowKernel(root / "workspace")
        first = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="first",
        )
        second_probe = kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="second",
        )
        collision = first.run_dir.parent / f"{first.run_dir.name}_r{second_probe.run_id[:8]}"
        kernel.control_store.bind_run(
            run_id="e" * 32,
            output_path=collision,
            initialization_intent_id="occupied-collision",
        )
        with self.assertRaises(KernelConflict):
            kernel.initialize_verified_import(probe=second_probe, fixture=FIXTURE)

    def test_prepublish_abort_can_retry_to_complete_new_state(self) -> None:
        from video2pdf_workflow_kernel import InitializationFault, VideoWorkflowKernel

        root = new_test_root("retry-abort")
        kernel = VideoWorkflowKernel(root / "workspace")
        probe = kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="retry",
        )
        with self.assertRaises(InitializationFault):
            kernel.initialize_verified_import(
                probe=probe, fixture=FIXTURE, fault_point="after_scaffold_staged"
            )
        self.assertEqual(
            kernel.reconcile_initialization(probe.run_id).outcome,
            "old_state_complete",
        )
        result = kernel.initialize_verified_import(probe=probe, fixture=FIXTURE)
        self.assertEqual(result.classification, "source_ready")

    def test_fixture_path_succeeds_when_production_services_are_fail_fast(self) -> None:
        from video2pdf_workflow_kernel import VideoWorkflowKernel

        root = new_test_root("deprived-services")
        kernel = VideoWorkflowKernel(root / "workspace")
        with mock.patch("socket.socket", side_effect=AssertionError("network forbidden")), mock.patch(
            "subprocess.run", side_effect=AssertionError("subprocess forbidden")
        ):
            result = kernel.trace_source_ready(
                fixture=FIXTURE,
                task_start="2026-07-15T01:02:03+08:00",
                request_id="deprived",
            )
        self.assertEqual(result.classification, "source_ready")

    def test_control_store_health_and_migration_are_real_file_backed(self) -> None:
        from video2pdf_workflow_kernel import VideoWorkflowKernel

        root = new_test_root("control-store")
        kernel = VideoWorkflowKernel(root / "workspace")
        kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="control-store-health",
        )
        report = kernel.control_store.check()
        self.assertEqual(report.status, "ok")
        self.assertEqual(report.schema_version, 5)
        self.assertEqual(report.pragmas["journal_mode"].lower(), "delete")
        self.assertEqual(int(report.pragmas["synchronous"]), 3)
        self.assertEqual(int(report.pragmas["foreign_keys"]), 1)
        self.assertEqual(int(report.pragmas["trusted_schema"]), 0)
        self.assertEqual(report.quick_check, "ok")
        self.assertTrue(kernel.control_store.path.is_file())

    def test_unknown_control_store_migration_level_blocks_startup(self) -> None:
        from video2pdf_workflow_kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.errors import ControlStoreUnavailable

        root = new_test_root("unknown-store-version")
        kernel = VideoWorkflowKernel(root / "workspace")
        kernel.bootstrap_probe(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id="unknown-store-version",
        )
        with sqlite3.connect(kernel.control_store.path) as connection:
            connection.execute("INSERT INTO schema_migrations(version) VALUES (99)")
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(root / "workspace")


if __name__ == "__main__":
    unittest.main()

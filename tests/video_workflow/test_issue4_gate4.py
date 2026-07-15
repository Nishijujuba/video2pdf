from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
import uuid
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"


class ScaffoldContainmentTests(unittest.TestCase):
    def test_schema_valid_parent_escape_is_rejected_by_registry_and_runtime(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.scaffold import create_scaffold

        scaffold = json.loads(
            (PROJECT_ROOT / "schemas/video-workflow/v1/scaffold.v1.json").read_text(
                encoding="utf-8"
            )
        )
        scaffold["managed_directories"][0] = "../outside"
        registry = ContractRegistry(PROJECT_ROOT)
        with self.assertRaises(ContractError):
            registry.validate("scaffold-contract", scaffold)

        parent = PROJECT_ROOT / "待删除" / f"gate4-scaffold-{uuid.uuid4().hex}"
        parent.mkdir(parents=True, exist_ok=False)
        with self.assertRaises(ContractError):
            create_scaffold(parent / "run", scaffold, "0" * 32)
        self.assertFalse((parent / "outside").exists())

    def test_managed_directory_paths_are_canonical_posix_relative(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        registry = ContractRegistry(PROJECT_ROOT)
        scaffold = json.loads(
            (PROJECT_ROOT / "schemas/video-workflow/v1/scaffold.v1.json").read_text(
                encoding="utf-8"
            )
        )
        for path in ("", "/absolute", ".", "..", "foo/./bar", "foo/../bar", "foo\\bar", "CON", "foo/trailing."):
            with self.subTest(path=path), self.assertRaises(ContractError):
                mutated = json.loads(json.dumps(scaffold))
                mutated["managed_directories"][0] = path
                registry.validate("scaffold-contract", mutated)


class RegistryPreparationTests(unittest.TestCase):
    def test_alternate_registry_validate_uses_the_single_full_preparation_path(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        canonical = json.loads(
            (PROJECT_ROOT / "schemas/video-workflow/registry.v1.json").read_text(
                encoding="utf-8"
            )
        )
        root = PROJECT_ROOT / "待删除" / f"gate4-registry-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        bad_schema = json.loads(
            (PROJECT_ROOT / "schemas/video-workflow/v1/workflow-result.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        bad_schema["$schema"] = "http://json-schema.org/draft-07/schema#"
        schema_path = root / "bad.schema.json"
        schema_path.write_text(json.dumps(bad_schema), encoding="utf-8")
        alternate = json.loads(json.dumps(canonical))
        next(item for item in alternate["contracts"] if item["schema_name"] == "workflow-result")["schema_path"] = str(schema_path)
        registry_path = root / "registry.json"
        registry_path.write_text(json.dumps(alternate), encoding="utf-8")

        registry = ContractRegistry(PROJECT_ROOT, registry_path)
        with mock.patch.object(registry, "_check_locked_runtime", wraps=registry._check_locked_runtime) as locked:
            with self.assertRaises(ContractError):
                registry.validate("workflow-result", {})
            locked.assert_called_once_with()


class RunStateMutationSagaTests(unittest.TestCase):
    def _trace_and_drift(self, label: str):
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel

        root = PROJECT_ROOT / "待删除" / f"gate4-{label}-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        kernel = VideoWorkflowKernel(root / "workspace")
        result = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id=label,
        )
        subtitle = result.run_dir / "source/subtitles/subtitle.en.srt"
        subtitle.write_bytes(subtitle.read_bytes() + b"\ndrift\n")
        return kernel, result

    def test_source_drift_mutation_is_prepared_written_and_committed(self) -> None:
        from video2pdf_workflow_kernel import ArtifactDrift

        kernel, result = self._trace_and_drift("saga-commit")
        with self.assertRaises(ArtifactDrift):
            kernel.reconcile_run(result.run_dir)
        run_path = result.run_dir / "workflow/run.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertEqual(record["checkpoints"]["source_ready"]["status"], "stale")
        self.assertEqual(record["coordination_revision"], 2)
        with sqlite3.connect(kernel.control_store.path) as connection:
            row = connection.execute(
                "SELECT operation, expected_run_revision, state, replacement_run_record_sha256 "
                "FROM run_state_mutation_intents WHERE run_id=?",
                (result.run_id,),
            ).fetchone()
        self.assertEqual(row[:3], ("source_drift_invalidation", 1, "COMMITTED"))
        self.assertEqual(row[3], hashlib.sha256(run_path.read_bytes()).hexdigest())
        self.assertEqual(kernel.control_store.current_run_record_sha(result.run_id), row[3])

    def test_prepared_mutation_recovers_after_fault_without_uncoordinated_state(self) -> None:
        from video2pdf_workflow_kernel import ArtifactDrift, InitializationFault

        for fault_point in (
            "after_run_state_mutation_prepared",
            "after_stale_run_record_write",
            "after_run_state_mutation_commit",
        ):
            with self.subTest(fault_point=fault_point):
                kernel, result = self._trace_and_drift(fault_point)
                with self.assertRaises(InitializationFault):
                    kernel.reconcile_run(result.run_dir, fault_point=fault_point)
                with self.assertRaises(ArtifactDrift):
                    kernel.reconcile_run(result.run_dir)
                with self.assertRaises(ArtifactDrift):
                    kernel.reconcile_run(result.run_dir)
                with sqlite3.connect(kernel.control_store.path) as connection:
                    rows = connection.execute(
                        "SELECT state FROM run_state_mutation_intents WHERE run_id=?",
                        (result.run_id,),
                    ).fetchall()
                self.assertEqual(rows, [("COMMITTED",)])


if __name__ == "__main__":
    unittest.main()

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
    def test_scaffold_contract_is_the_exact_registered_canonical_instance(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        registry = ContractRegistry(PROJECT_ROOT)
        scaffold = json.loads(
            (PROJECT_ROOT / "schemas/video-workflow/v1/scaffold.v1.json").read_text(
                encoding="utf-8"
            )
        )
        mutations = []
        rogue = json.loads(json.dumps(scaffold))
        rogue["managed_directories"].append("rogue")
        mutations.append(rogue)
        missing = json.loads(json.dumps(scaffold))
        missing["managed_directories"].pop()
        mutations.append(missing)
        reordered = json.loads(json.dumps(scaffold))
        reordered["managed_directories"][0:2] = reversed(
            reordered["managed_directories"][0:2]
        )
        mutations.append(reordered)
        for mutated in mutations:
            with self.assertRaises(ContractError):
                registry.validate("scaffold-contract", mutated)

        canonical = json.loads(
            (PROJECT_ROOT / "schemas/video-workflow/registry.v1.json").read_text(
                encoding="utf-8"
            )
        )
        root = PROJECT_ROOT / "待删除" / f"gate5-registry-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        rogue_path = root / "rogue.json"
        rogue_path.write_text(json.dumps(rogue), encoding="utf-8")
        entry = next(
            item for item in canonical["contracts"]
            if item["schema_name"] == "scaffold-contract"
        )
        entry["canonical_instance"] = str(rogue_path)
        alternate_path = root / "registry.json"
        alternate_path.write_text(json.dumps(canonical), encoding="utf-8")
        with self.assertRaises(ContractError):
            ContractRegistry(PROJECT_ROOT, alternate_path)

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
    def test_alternate_registry_must_be_an_exact_authority_copy(self) -> None:
        from video2pdf_workflow_kernel.contracts import ContractRegistry
        from video2pdf_workflow_kernel.errors import ContractError

        canonical = json.loads(
            (PROJECT_ROOT / "schemas/video-workflow/registry.v1.json").read_text(
                encoding="utf-8"
            )
        )
        root = PROJECT_ROOT / "待删除" / f"gate6-registry-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        exact_path = root / "exact.json"
        exact_path.write_text(json.dumps(canonical), encoding="utf-8")
        ContractRegistry(PROJECT_ROOT, exact_path).check()

        mutations = []
        for field, value in (
            ("kind", "supporting_schema"),
            ("schema_id", "https://example.invalid/weaker.json"),
            ("schema_path", "schemas/video-workflow/v1/common.v1.schema.json"),
            ("positive_example", None),
            ("invariants", []),
        ):
            mutated = json.loads(json.dumps(canonical))
            entry = next(
                item for item in mutated["contracts"]
                if item["schema_name"] == "scaffold-contract"
            )
            if value is None:
                entry.pop(field)
            else:
                entry[field] = value
            mutations.append(mutated)
        top_level = json.loads(json.dumps(canonical))
        top_level["schema_version"] = "1.0.1"
        mutations.append(top_level)
        for index, mutated in enumerate(mutations):
            path = root / f"tampered-{index}.json"
            path.write_text(json.dumps(mutated), encoding="utf-8")
            with self.assertRaises(ContractError):
                ContractRegistry(PROJECT_ROOT, path)

    def test_weaker_alternate_registry_fails_before_schema_preparation(self) -> None:
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

        with self.assertRaises(ContractError):
            ContractRegistry(PROJECT_ROOT, registry_path)


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

    def test_schema_valid_run_record_tamper_creates_no_mutation_and_is_not_written(self) -> None:
        from video2pdf_workflow_kernel import ArtifactDrift
        from video2pdf_workflow_kernel.utils import sha256_file, write_json_atomic

        kernel, result = self._trace_and_drift("authority-tamper")
        # Restore source bytes so the only drift is the schema-valid Run Record mutation.
        subtitle = result.run_dir / "source/subtitles/subtitle.en.srt"
        subtitle.write_bytes(subtitle.read_bytes()[: -len(b"\ndrift\n")])
        run_path = result.run_dir / "workflow/run.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        record["normalized_title"] = "schema_valid_tamper"
        write_json_atomic(run_path, record)
        tampered_sha = sha256_file(run_path)

        with self.assertRaises(ArtifactDrift):
            kernel.reconcile_run(result.run_dir)

        self.assertEqual(sha256_file(run_path), tampered_sha)
        with sqlite3.connect(kernel.control_store.path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM run_state_mutation_intents WHERE run_id=?",
                (result.run_id,),
            ).fetchone()[0]
        self.assertEqual(count, 0)

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

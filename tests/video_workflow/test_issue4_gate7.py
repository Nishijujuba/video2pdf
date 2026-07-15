from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "\u5f85\u5220\u9664/kernel-test-runs"
SLICE_BASE_COMMIT = "96089b99c9ae63fff61107e1920fc3481ffc0802"


def new_test_root(label: str) -> Path:
    root = TEST_RUNS / f"{label}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def load_script(name: str, relative_path: str):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import diagnostics
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClosedSourceInventoryTests(unittest.TestCase):
    def trace(self, label: str):
        from video2pdf_workflow_kernel import VideoWorkflowKernel

        root = new_test_root(label)
        kernel = VideoWorkflowKernel(root / "workspace")
        result = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:03+08:00",
            request_id=label,
        )
        return root, kernel, result

    def assert_source_drift(self, kernel, result, expected_path: str) -> None:
        from video2pdf_workflow_kernel import ArtifactDrift

        with self.assertRaises(ArtifactDrift) as raised:
            kernel.reconcile_run(result.run_dir)
        self.assertIn(expected_path, raised.exception.data["drifted_paths"])
        record = json.loads(
            (result.run_dir / "workflow/run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(record["checkpoints"]["source_ready"]["status"], "stale")

    def test_undeclared_file_under_canonical_source_directory_is_drift(self) -> None:
        _, kernel, result = self.trace("extra-source-file")
        extra = result.run_dir / "source/media/undeclared.bin"
        extra.write_bytes(b"undeclared source evidence")

        self.assert_source_drift(kernel, result, "source/media/undeclared.bin")

    def test_extra_empty_source_directory_is_drift(self) -> None:
        _, kernel, result = self.trace("extra-source-directory")
        (result.run_dir / "source/undeclared-empty").mkdir()

        self.assert_source_drift(kernel, result, "source/undeclared-empty")

    def test_source_symlink_to_outside_file_is_drift(self) -> None:
        root, kernel, result = self.trace("source-file-symlink")
        outside = root / "outside-source.bin"
        outside.write_bytes(b"outside source")
        link = result.run_dir / "source/media/escaped.bin"
        os.symlink(outside, link)

        self.assert_source_drift(kernel, result, "source/media/escaped.bin")

    def test_source_symlink_to_outside_directory_is_drift(self) -> None:
        root, kernel, result = self.trace("source-directory-symlink")
        outside = root / "outside-source-directory"
        outside.mkdir()
        link = result.run_dir / "source/escaped-directory"
        os.symlink(outside, link, target_is_directory=True)

        self.assert_source_drift(kernel, result, "source/escaped-directory")


class SharedExitEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.collector = load_script(
            "slice1_collector_gate7", "scripts/collect_slice1_exit_evidence.py"
        )
        cls.validator = load_script(
            "slice1_validator_gate7", "scripts/validate_slice_exit_evidence.py"
        )

    def test_shared_evidence_hash_has_known_sha256_result(self) -> None:
        from video2pdf_workflow_kernel.evidence import sha256_file

        path = new_test_root("shared-sha") / "abc.bin"
        path.write_bytes(b"abc")
        self.assertEqual(
            sha256_file(path),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_collector_canonicalizes_command_logs_before_fingerprinting(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["command"],
            returncode=0,
            stdout=b"stdout\r\n",
            stderr=b"stderr\r\n",
        )
        with mock.patch.object(self.collector.subprocess, "run", return_value=completed):
            captured = self.collector.run_commands("a" * 40)

        for item in captured:
            self.assertNotIn(b"\r", item["raw"])
            self.assertIn(b"stdout\nstderr\n", item["raw"])

    def test_collector_fingerprints_complete_slice_diff_from_fixed_base(self) -> None:
        implementation_commit = self.collector.git("rev-parse", "HEAD")
        artifacts = self.collector.implementation_artifacts(
            SLICE_BASE_COMMIT, implementation_commit
        )
        paths = {item["path"] for item in artifacts}

        self.assertIn(".gitattributes", paths)
        self.assertIn("src/video2pdf_workflow_kernel/kernel.py", paths)
        self.assertIn("tests/video_workflow/test_source_ready_tracer.py", paths)
        self.assertFalse(any(path.startswith("evidence/slice-01/") for path in paths))

    def test_validator_rejects_omission_of_early_issue4_file(self) -> None:
        implementation_commit = self.collector.git("rev-parse", "HEAD")
        artifacts = self.collector.implementation_artifacts(
            SLICE_BASE_COMMIT, implementation_commit
        )
        self.assertTrue(any(item["path"] == ".gitattributes" for item in artifacts))
        manifest = {
            "slice_base_commit": SLICE_BASE_COMMIT,
            "implementation_commit": implementation_commit,
            "artifact_fingerprints": [
                item for item in artifacts if item["path"] != ".gitattributes"
            ],
        }

        with self.assertRaisesRegex(
            self.validator.EvidenceError, "complete implementation change set"
        ):
            self.validator.validate_implementation_artifacts(manifest)

    def test_v2_schema_fixes_slice_base_commit(self) -> None:
        schema = json.loads(
            (PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn("slice_base_commit", schema["required"])
        fixed_authorities = {
            (
                branch["properties"]["slice"]["properties"]["number"]["const"],
                branch["properties"]["slice_base_commit"]["const"],
            )
            for branch in schema["oneOf"]
        }
        self.assertEqual(
            fixed_authorities,
            {
                (1, SLICE_BASE_COMMIT),
                (2, "904f46409b87aca96aeecf5cb0be4855c2cfdafa"),
            },
        )

    def test_collector_and_validator_map_shared_git_failures(self) -> None:
        with self.assertRaises(RuntimeError):
            self.collector.git("gate7-command-that-does-not-exist")
        with self.assertRaises(self.validator.EvidenceError):
            self.validator.git("gate7-command-that-does-not-exist")


if __name__ == "__main__":
    unittest.main()

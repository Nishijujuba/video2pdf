from __future__ import annotations

import json
from pathlib import Path
import unittest
import uuid

from scripts.project_test_results import (
    ResultIntegrityError,
    read_module_result,
    verify_summary_artifacts,
    write_json_exclusive,
)

FIXTURES = (
    Path(__file__).parent / "fixtures" / "scheduler_results" / "待删除"
)


def run_directory(label: str) -> Path:
    path = FIXTURES / f"{label}-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


class ResultArtifactTests(unittest.TestCase):
    def test_json_artifacts_are_exclusive_and_sha256_bound(self) -> None:
        destination = run_directory("exclusive") / "result.json"
        fingerprint = write_json_exclusive(
            destination, {"value": "complete"}
        )
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(
            read_module_result(destination, fingerprint)["value"],
            "complete",
        )
        with self.assertRaisesRegex(ResultIntegrityError, "already exists"):
            write_json_exclusive(destination, {"value": "replacement"})

    def test_missing_corrupt_and_fingerprint_mismatched_results_fail_closed(
        self,
    ) -> None:
        run_dir = run_directory("integrity")
        destination = run_dir / "result.json"
        with self.assertRaisesRegex(ResultIntegrityError, "missing"):
            read_module_result(destination)
        destination.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ResultIntegrityError, "invalid JSON"):
            read_module_result(destination)
        second = run_dir / "second.json"
        second.write_text(json.dumps({"ok": True}), encoding="utf-8")
        with self.assertRaisesRegex(ResultIntegrityError, "fingerprint"):
            read_module_result(second, "0" * 64)

    def test_summary_fingerprints_detect_lost_or_changed_logs_and_results(
        self,
    ) -> None:
        run_dir = run_directory("summary-fingerprints")
        (run_dir / "modules").mkdir()
        (run_dir / "logs").mkdir()
        key = "module00"
        assignment = run_dir / "modules" / f"{key}.assignment.json"
        result = run_dir / "modules" / f"{key}.result.json"
        stdout = run_dir / "logs" / f"{key}.stdout.log"
        stderr = run_dir / "logs" / f"{key}.stderr.log"
        fingerprints = {
            "assignment_sha256": write_json_exclusive(assignment, {"a": 1}),
            "result_sha256": write_json_exclusive(result, {"r": 1}),
            "stdout_sha256": write_json_exclusive(stdout, {"out": 1}),
            "stderr_sha256": write_json_exclusive(stderr, {"err": 1}),
        }
        summary = {
            "modules": [{"module_key": key, **fingerprints}]
        }
        verify_summary_artifacts(run_dir, summary)
        stdout.write_bytes(b"changed")
        with self.assertRaisesRegex(
            ResultIntegrityError, "fingerprint mismatch"
        ):
            verify_summary_artifacts(run_dir, summary)


if __name__ == "__main__":
    unittest.main()

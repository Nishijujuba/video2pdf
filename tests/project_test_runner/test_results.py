from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest import mock

from scripts.project_test_results import (
    ResultIntegrityError,
    read_file_snapshot,
    read_module_result,
)
from tests.project_test_runner._fixture_root import new_fixture_dir


class StableFileSnapshotTests(unittest.TestCase):
    def test_module_result_rejects_nonfinite_json_constants(self) -> None:
        root = new_fixture_dir("strict-module-result")
        for label, duration_token in (
            ("nan", "NaN"),
            ("positive-infinity", "Infinity"),
            ("negative-infinity", "-Infinity"),
        ):
            with self.subTest(duration=label):
                artifact = root / f"{label}.json"
                artifact.write_text(
                    f'{{"duration_seconds":{duration_token}}}',
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    ResultIntegrityError,
                    "invalid JSON",
                ):
                    read_module_result(artifact)

    def test_snapshot_binds_content_and_identity_from_open_handle(self) -> None:
        root = new_fixture_dir("stable-snapshot")
        artifact = root / "artifact.bin"
        artifact.write_bytes(b"committed bytes")

        canonical, content, identity = read_file_snapshot(artifact)

        self.assertEqual(canonical, artifact.resolve())
        self.assertEqual(content, b"committed bytes")
        self.assertEqual(identity["size"], len(content))
        self.assertEqual(
            set(identity),
            {"device", "inode", "size", "mtime_ns", "ctime_ns"},
        )

    @unittest.skipUnless(os.name == "nt", "Windows final-handle identity")
    def test_snapshot_rejects_unproved_windows_handle_path(self) -> None:
        root = new_fixture_dir("unproved-handle")
        artifact = root / "artifact.bin"
        artifact.write_bytes(b"content")

        with mock.patch(
            "scripts.project_test_results._windows_final_handle_path",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                ResultIntegrityError,
                "identity is unproved",
            ):
                read_file_snapshot(artifact)


if __name__ == "__main__":
    unittest.main()

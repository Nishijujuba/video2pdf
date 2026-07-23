from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from unittest import mock

from scripts.project_test_external_root import ensure_project_root
from tests.project_test_runner._fixture_root import new_fixture_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from tests.video_workflow import _test_run


class VideoWorkflowTestRunBoundaryTests(unittest.TestCase):
    def fixture_run_dir(self) -> Path:
        external_root = new_fixture_dir("video-boundary")
        project_root = ensure_project_root(
            external_root,
            "https://github.com/Nishijujuba/video2pdf.git",
        )
        suite_root = project_root / "video-workflow"
        suite_root.mkdir()
        run_dir = suite_root / "20260724_120000_01234567"
        run_dir.mkdir()
        return run_dir.resolve()

    def test_direct_execution_uses_the_project_local_compatibility_root(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                _test_run.module_test_root(PROJECT_ROOT),
                PROJECT_ROOT / "待删除" / "kernel-test-runs",
            )

    def test_runner_execution_uses_short_contained_module_path_and_manifest(self) -> None:
        run_dir = self.fixture_run_dir()
        try:
            environment = {
                _test_run.RUN_DIR_ENV: str(run_dir),
                _test_run.SUITE_ID_ENV: "video-workflow",
                _test_run.MODULE_KEY_ENV: "0123456789ab",
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                module_root = _test_run.module_test_root(PROJECT_ROOT)
                case_root = _test_run.new_case_dir(
                    self.id(), label="long scenario name ignored"
                )

            self.assertEqual(
                module_root,
                run_dir / "generated" / "0123456789ab",
            )
            self.assertEqual(case_root.parent, module_root)
            self.assertRegex(case_root.name, r"^c-[0-9a-f]{10}-[0-9a-f]{8}$")
            records = [
                json.loads(line)
                for line in (module_root / "generated-paths.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                [record["kind"] for record in records],
                ["module_root", "case_dir"],
            )
            self.assertTrue(
                all(
                    Path(record["path"]).is_relative_to(run_dir)
                    for record in records
                )
            )
        finally:
            pass

    def test_runner_environment_fails_closed_for_invalid_identity(self) -> None:
        forged_external = new_fixture_dir("forged-boundary")
        forged_run = (
            forged_external
            / "video2pdf"
            / "video-workflow"
            / "20260724_120000_01234567"
        )
        forged_run.mkdir(parents=True)
        cases = (
            {_test_run.RUN_DIR_ENV: "relative"},
            {
                _test_run.RUN_DIR_ENV: str(PROJECT_ROOT),
                _test_run.SUITE_ID_ENV: "wrong-suite",
                _test_run.MODULE_KEY_ENV: "0123456789ab",
            },
            {
                _test_run.RUN_DIR_ENV: str(PROJECT_ROOT),
                _test_run.SUITE_ID_ENV: "video-workflow",
                _test_run.MODULE_KEY_ENV: "../escape",
            },
            {
                _test_run.RUN_DIR_ENV: str(forged_run),
                _test_run.SUITE_ID_ENV: "video-workflow",
                _test_run.MODULE_KEY_ENV: "0123456789ab",
            },
        )
        for environment in cases:
            with self.subTest(environment=environment):
                with mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(_test_run.TestRunBoundaryError):
                        _test_run.module_test_root(PROJECT_ROOT)
        self.assertFalse((forged_external / "video2pdf" / "project.json").exists())

    def test_runner_environment_rejects_wrong_project_marker_and_run_level(
        self,
    ) -> None:
        valid_run = self.fixture_run_dir()
        marker_path = valid_run.parents[1] / "project.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["repository"] = "someone/else"
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        environment = {
            _test_run.RUN_DIR_ENV: str(valid_run),
            _test_run.SUITE_ID_ENV: "video-workflow",
            _test_run.MODULE_KEY_ENV: "0123456789ab",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "ownership",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

        wrong_level = self.fixture_run_dir().parent
        environment[_test_run.RUN_DIR_ENV] = str(wrong_level)
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "suite key|identity|marker",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

        invalid_run = valid_run.parent / "caller-chosen-output"
        invalid_run.mkdir()
        environment[_test_run.RUN_DIR_ENV] = str(invalid_run)
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "timestamp_short-run-id",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

    def test_runner_environment_rejects_reparse_owned_hierarchy(self) -> None:
        run_dir = self.fixture_run_dir()
        environment = {
            _test_run.RUN_DIR_ENV: str(run_dir),
            _test_run.SUITE_ID_ENV: "video-workflow",
            _test_run.MODULE_KEY_ENV: "0123456789ab",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch(
                "scripts.project_test_external_root._is_reparse_point",
                side_effect=lambda path: path == run_dir.parent,
            ),
        ):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "reparse",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

    def test_child_environment_preserves_parent_and_pins_temp_to_case(self) -> None:
        run_dir = self.fixture_run_dir()
        try:
            environment = {
                "PARENT_VALUE": "preserved",
                _test_run.RUN_DIR_ENV: str(run_dir),
                _test_run.SUITE_ID_ENV: "video-workflow",
                _test_run.MODULE_KEY_ENV: "0123456789ab",
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                child = _test_run.child_environment(self.id())

            self.assertEqual(child["PARENT_VALUE"], "preserved")
            self.assertEqual(child["PYTHONIOENCODING"], "utf-8")
            self.assertEqual(child["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertEqual(child["TEMP"], child["TMP"])
            self.assertEqual(child["TMP"], child["TMPDIR"])
            self.assertTrue(Path(child["TEMP"]).is_relative_to(run_dir))
        finally:
            pass


if __name__ == "__main__":
    unittest.main()

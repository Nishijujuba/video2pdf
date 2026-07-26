from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest import mock

from scripts.project_test_run_identity import (
    create_synthetic_project_test_run,
    freeze_worker_environment,
)
from scripts.project_test_results import canonical_json_bytes
from tests.project_test_runner._fixture_root import new_fixture_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from tests.video_workflow import _test_run


class VideoWorkflowTestRunBoundaryTests(unittest.TestCase):
    MODULE_KEY = "0123456789ab"
    TEST_IDS = ["synthetic_boundary.SyntheticBoundaryTests.test_identity"]

    def fixture_run_dir(
        self,
        *,
        run_suite_key: str = "video-workflow",
        selected_suite_ids: list[str] | None = None,
        module_suite_id: str = "video-workflow",
        write_manifests: bool = True,
    ) -> Path:
        external_root = new_fixture_dir("video-boundary")
        requested = selected_suite_ids or ["video-workflow"]
        baseline_selected = (
            requested if "video-workflow" in requested else ["video-workflow"]
        )
        run_dir = create_synthetic_project_test_run(
            external_root=external_root,
            project_root=PROJECT_ROOT,
            suite_id="video-workflow",
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
            selected_suite_ids=baseline_selected,
            run_suite_key=(
                run_suite_key
                if baseline_selected == requested
                else "video-workflow"
            ),
        )
        if requested != baseline_selected or module_suite_id != "video-workflow":
            discovery_path = run_dir / "discovery.json"
            discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
            discovery["suite_ids"] = requested
            discovery["discovery_arguments"] = {"suite_ids": requested}
            discovery["modules"][0]["suite_id"] = module_suite_id
            discovery_bytes = canonical_json_bytes(discovery)
            discovery_path.write_bytes(discovery_bytes)
            test_run_path = run_dir / "test-run.json"
            test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
            test_run["suite_ids"] = requested
            test_run["discovery_sha256"] = hashlib.sha256(
                discovery_bytes
            ).hexdigest()
            test_run_path.write_bytes(canonical_json_bytes(test_run))
        if not write_manifests:
            (run_dir / "discovery.json").rename(
                run_dir / "discovery.json.tampered"
            )
        return run_dir

    def environment(self, run_dir: Path) -> dict[str, str]:
        return {
            _test_run.RUN_DIR_ENV: str(run_dir),
            _test_run.SUITE_ID_ENV: "video-workflow",
            _test_run.MODULE_KEY_ENV: self.MODULE_KEY,
        }

    def frozen_worker(self, environment: dict[str, str]):
        return mock.patch.object(
            _test_run,
            "FROZEN_RUN_ENV",
            freeze_worker_environment(environment),
        )

    def test_direct_execution_uses_the_project_local_compatibility_root(self) -> None:
        residual_environment = {
            _test_run.RUN_DIR_ENV: "later-process-mutation",
            _test_run.SUITE_ID_ENV: "video-workflow",
            _test_run.MODULE_KEY_ENV: self.MODULE_KEY,
        }
        with (
            mock.patch.dict(os.environ, residual_environment, clear=True),
            self.frozen_worker({}),
        ):
            self.assertEqual(
                _test_run.module_test_root(PROJECT_ROOT),
                PROJECT_ROOT / "待删除" / "kernel-test-runs",
            )

    def test_runner_execution_uses_short_contained_module_path_and_manifest(self) -> None:
        run_dir = self.fixture_run_dir()
        try:
            environment = self.environment(run_dir)
            with self.frozen_worker(environment):
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

    def test_all_suite_run_accepts_a_selected_video_workflow_worker(self) -> None:
        run_dir = self.fixture_run_dir(
            run_suite_key="all",
            selected_suite_ids=["project-test-runner", "video-workflow"],
        )

        with self.frozen_worker(self.environment(run_dir)):
            self.assertEqual(
                _test_run.module_test_root(PROJECT_ROOT),
                run_dir / "generated" / self.MODULE_KEY,
            )

    def test_single_suite_run_rejects_worker_outside_its_selected_suite(self) -> None:
        run_dir = self.fixture_run_dir(
            selected_suite_ids=["project-test-runner"],
            module_suite_id="video-workflow",
        )

        with self.frozen_worker(self.environment(run_dir)):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "selected suite|suite identity",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

    def test_owned_named_run_without_runner_manifests_fails_closed(self) -> None:
        run_dir = self.fixture_run_dir(write_manifests=False)

        with self.frozen_worker(self.environment(run_dir)):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "test-run.json|discovery.json",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

    def test_tampered_discovery_fingerprint_fails_closed(self) -> None:
        run_dir = self.fixture_run_dir()
        discovery_path = run_dir / "discovery.json"
        discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
        discovery["modules"][0]["test_ids"].append("tampered.test_id")
        discovery_path.write_bytes(canonical_json_bytes(discovery))

        with self.frozen_worker(self.environment(run_dir)):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "fingerprint",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

    def test_manifest_run_directory_identity_mismatch_fails_closed(self) -> None:
        run_dir = self.fixture_run_dir()
        test_run_path = run_dir / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        test_run["run_dir"] = str(
            run_dir.parent / "20260724_120000_deadbeef"
        )
        test_run_path.write_bytes(canonical_json_bytes(test_run))

        with self.frozen_worker(self.environment(run_dir)):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "run directory identity",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

    def test_manifest_project_commit_and_registry_provenance_fail_closed(
        self,
    ) -> None:
        variants = (
            ("project", {"project_key": "video2pdf", "repository": "other/repo"}),
            ("commit", "0" * 40),
            ("registry_sha256", "0" * 64),
        )
        for field, replacement in variants:
            with self.subTest(field=field):
                run_dir = self.fixture_run_dir()
                test_run_path = run_dir / "test-run.json"
                test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
                test_run[field] = replacement
                test_run_path.write_bytes(canonical_json_bytes(test_run))

                with self.frozen_worker(self.environment(run_dir)):
                    with self.assertRaisesRegex(
                        _test_run.TestRunBoundaryError,
                        "project|repository|commit|registry",
                    ):
                        _test_run.module_test_root(PROJECT_ROOT)

    def test_discovery_must_bind_active_module_and_nonempty_test_ids(self) -> None:
        variants = (
            {"module_key": "fedcba987654", "test_ids": self.TEST_IDS},
            {"module_key": self.MODULE_KEY, "test_ids": []},
        )
        for variant in variants:
            with self.subTest(variant=variant):
                run_dir = self.fixture_run_dir()
                discovery_path = run_dir / "discovery.json"
                discovery = json.loads(
                    discovery_path.read_text(encoding="utf-8")
                )
                discovery["modules"][0]["module_key"] = variant["module_key"]
                discovery["modules"][0]["test_ids"] = variant["test_ids"]
                discovery_bytes = canonical_json_bytes(discovery)
                discovery_path.write_bytes(discovery_bytes)
                test_run_path = run_dir / "test-run.json"
                test_run = json.loads(
                    test_run_path.read_text(encoding="utf-8")
                )
                test_run["discovery_sha256"] = hashlib.sha256(
                    discovery_bytes
                ).hexdigest()
                test_run_path.write_bytes(canonical_json_bytes(test_run))
                with self.frozen_worker(self.environment(run_dir)):
                    with self.assertRaisesRegex(
                        _test_run.TestRunBoundaryError,
                        "module|test IDs",
                    ):
                        _test_run.module_test_root(PROJECT_ROOT)

    def test_runner_environment_fails_closed_for_invalid_identity(self) -> None:
        forged_external = new_fixture_dir("forged-boundary")
        forged_run = create_synthetic_project_test_run(
            external_root=forged_external,
            project_root=PROJECT_ROOT,
            suite_id="video-workflow",
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
        )
        marker = forged_run.parent.parent / "project.json"
        marker.rename(marker.with_suffix(".tampered"))
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
                with self.frozen_worker(environment):
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
        environment = self.environment(valid_run)
        with self.frozen_worker(environment):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "ownership",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

        wrong_level = self.fixture_run_dir().parent
        environment[_test_run.RUN_DIR_ENV] = str(wrong_level)
        with self.frozen_worker(environment):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "suite key|identity|marker|project root",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

        invalid_source = self.fixture_run_dir()
        invalid_run = invalid_source.parent / "caller-chosen-output"
        invalid_source.rename(invalid_run)
        environment[_test_run.RUN_DIR_ENV] = str(invalid_run)
        with self.frozen_worker(environment):
            with self.assertRaisesRegex(
                _test_run.TestRunBoundaryError,
                "timestamp_short-run-id",
            ):
                _test_run.module_test_root(PROJECT_ROOT)

    def test_runner_environment_rejects_reparse_owned_hierarchy(self) -> None:
        run_dir = self.fixture_run_dir()
        environment = self.environment(run_dir)
        with (
            self.frozen_worker(environment),
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
            environment = {"PARENT_VALUE": "preserved", **self.environment(run_dir)}
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                self.frozen_worker(environment),
            ):
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

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from scripts.project_test_run_identity import (
    create_synthetic_project_test_run,
    resolve_active_worker_identity,
)
from scripts.project_test_results import canonical_json_bytes
from tests.project_test_runner import _fixture_root


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FixtureRootBoundaryTests(unittest.TestCase):
    MODULE_KEY = "0123456789ab"
    TEST_IDS = ["synthetic.SyntheticTests.test_identity"]

    def test_production_identity_api_accepts_factory_baseline(self) -> None:
        external_root = _fixture_root.new_fixture_dir("identity-api")
        run_dir = create_synthetic_project_test_run(
            external_root=external_root,
            project_root=PROJECT_ROOT,
            suite_id="project-test-runner",
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
        )

        identity = resolve_active_worker_identity(
            self.environment(run_dir, "project-test-runner"),
            project_root=PROJECT_ROOT,
            expected_suite="project-test-runner",
        )

        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.run_dir, run_dir)
        self.assertEqual(identity.suite_id, "project-test-runner")
        self.assertEqual(identity.module_key, self.MODULE_KEY)
        self.assertEqual(identity.test_ids, tuple(self.TEST_IDS))

    def make_run(
        self,
        *,
        suite_id: str,
        selected_suite_ids: list[str] | None = None,
        run_suite_key: str | None = None,
        module_suite_id: str | None = None,
        module_key: str | None = None,
        test_ids: list[str] | None = None,
    ) -> Path:
        external_root = _fixture_root.new_fixture_dir("fixture-boundary")
        selected = selected_suite_ids or [suite_id]
        run_dir = create_synthetic_project_test_run(
            external_root=external_root,
            project_root=PROJECT_ROOT,
            suite_id=suite_id,
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
            selected_suite_ids=selected,
            run_suite_key=run_suite_key,
        )
        if (
            module_suite_id is not None
            or module_key is not None
            or test_ids is not None
        ):
            discovery_path = run_dir / "discovery.json"
            discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
            module = discovery["modules"][0]
            module["suite_id"] = module_suite_id or suite_id
            module["module_key"] = module_key or self.MODULE_KEY
            module["test_ids"] = self.TEST_IDS if test_ids is None else test_ids
            discovery_bytes = canonical_json_bytes(discovery)
            discovery_path.write_bytes(discovery_bytes)
            test_run_path = run_dir / "test-run.json"
            test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
            test_run["discovery_sha256"] = hashlib.sha256(
                discovery_bytes
            ).hexdigest()
            test_run_path.write_bytes(canonical_json_bytes(test_run))
        return run_dir

    def environment(self, run_dir: Path, suite_id: str) -> dict[str, str]:
        return {
            _fixture_root.RUN_DIR_ENV: str(run_dir),
            _fixture_root.SUITE_ID_ENV: suite_id,
            _fixture_root.MODULE_KEY_ENV: self.MODULE_KEY,
        }

    def test_direct_fallback_is_partitioned_by_declared_suite(self) -> None:
        self.assertEqual(
            _fixture_root.fixture_root_from_environment(
                {},
                PROJECT_ROOT,
                expected_suite="video-workflow",
            ),
            PROJECT_ROOT
            / "待删除"
            / "kernel-test-runs"
            / "video-workflow",
        )

    def test_runner_identity_accepts_single_and_all_suite_runs(self) -> None:
        single = self.make_run(suite_id="project-test-runner")
        keyed_single = self.make_run(
            suite_id="project-test-runner",
            run_suite_key="runner-short-key",
        )
        all_run = self.make_run(
            suite_id="project-test-runner",
            selected_suite_ids=["project-test-runner", "video-workflow"],
            run_suite_key="all",
        )
        for run_dir in (single, keyed_single, all_run):
            with self.subTest(run_dir=run_dir):
                self.assertEqual(
                    _fixture_root.fixture_root_from_environment(
                        self.environment(run_dir, "project-test-runner"),
                        PROJECT_ROOT,
                        expected_suite="project-test-runner",
                    ),
                    run_dir / "generated" / self.MODULE_KEY,
                )
        self.assertEqual(
            _fixture_root.fixture_root_from_environment(
                self.environment(single, "project-test-runner"),
                PROJECT_ROOT,
            ),
            single / "generated" / self.MODULE_KEY,
        )

    def test_declared_suite_verifies_identity_without_selecting_output(self) -> None:
        run_dir = self.make_run(suite_id="project-test-runner")
        with self.assertRaisesRegex(
            _fixture_root.FixtureRootError,
            "expected suite|suite identity",
        ):
            _fixture_root.fixture_root_from_environment(
                self.environment(run_dir, "project-test-runner"),
                PROJECT_ROOT,
                expected_suite="video-workflow",
            )

    def test_runner_identity_rejects_incomplete_and_residual_environment(
        self,
    ) -> None:
        run_dir = self.make_run(suite_id="project-test-runner")
        cases = (
            {_fixture_root.RUN_DIR_ENV: str(run_dir)},
            {
                _fixture_root.RUN_DIR_ENV: str(run_dir),
                _fixture_root.SUITE_ID_ENV: "project-test-runner",
                _fixture_root.MODULE_KEY_ENV: "../escape",
            },
            {
                _fixture_root.RUN_DIR_ENV: str(PROJECT_ROOT),
                _fixture_root.SUITE_ID_ENV: "project-test-runner",
                _fixture_root.MODULE_KEY_ENV: self.MODULE_KEY,
            },
        )
        for environment in cases:
            with self.subTest(environment=environment):
                with self.assertRaises(_fixture_root.FixtureRootError):
                    _fixture_root.fixture_root_from_environment(
                        environment,
                        PROJECT_ROOT,
                        expected_suite="project-test-runner",
                    )

    def test_runner_identity_binds_live_provenance_module_and_test_ids(
        self,
    ) -> None:
        cases = (
            {"module_suite_id": "video-workflow"},
            {"module_key": "fedcba987654"},
            {"test_ids": []},
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                run_dir = self.make_run(
                    suite_id="project-test-runner",
                    **mutation,
                )
                with self.assertRaises(_fixture_root.FixtureRootError):
                    _fixture_root.fixture_root_from_environment(
                        self.environment(run_dir, "project-test-runner"),
                        PROJECT_ROOT,
                        expected_suite="project-test-runner",
                    )

        run_dir = self.make_run(suite_id="project-test-runner")
        test_run_path = run_dir / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        test_run["registry_sha256"] = "0" * 64
        test_run_path.write_bytes(canonical_json_bytes(test_run))
        with self.assertRaisesRegex(
            _fixture_root.FixtureRootError,
            "project|repository|commit|registry",
        ):
            _fixture_root.fixture_root_from_environment(
                self.environment(run_dir, "project-test-runner"),
                PROJECT_ROOT,
                expected_suite="project-test-runner",
            )


if __name__ == "__main__":
    unittest.main()

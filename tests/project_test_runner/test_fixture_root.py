from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import unittest

from scripts.project_test_run_identity import (
    ProjectTestRunIdentityError,
    build_project_test_run_v2,
    create_synthetic_project_test_run,
    resolve_active_worker_identity,
)
from scripts.project_test_results import canonical_json_bytes
from scripts.project_test_source_provenance import (
    build_execution_source_manifest,
    create_source_snapshot,
    module_inventory,
)
from tests.project_test_runner import _fixture_root


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FixtureRootBoundaryTests(unittest.TestCase):
    MODULE_KEY = "0123456789ab"
    TEST_IDS = ["synthetic.SyntheticTests.test_identity"]
    TEST_RUN_V2_FIELDS = {
        "schema_name",
        "schema_version",
        "command",
        "project",
        "commit",
        "registry_sha256",
        "discovery_sha256",
        "source_manifest_path",
        "source_manifest_sha256",
        "source_snapshot_path",
        "source_snapshot_id",
        "source_snapshot_sha256",
        "suite_ids",
        "run_dir",
        "project_marker_sha256",
        "persisted_run_id",
        "persisted_run_nonce",
        "persisted_target_identity",
        "persisted_supervisor_identity",
        "requested_jobs",
        "timings_from",
        "runner_identity",
        "discovery_process",
    }

    def test_public_source_artifact_builders_keep_closed_signatures(
        self,
    ) -> None:
        self.assertEqual(
            tuple(inspect.signature(build_execution_source_manifest).parameters),
            ("repo_root", "test_module_paths"),
        )
        self.assertNotIn(
            "expected_source_paths",
            inspect.signature(create_source_snapshot).parameters,
        )

    def authority_assignment(
        self,
        run_dir: Path,
        *,
        nonce: str = "a" * 64,
    ) -> dict:
        discovery = json.loads(
            (run_dir / "discovery.json").read_text(encoding="utf-8")
        )
        test_run = json.loads(
            (run_dir / "test-run.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (run_dir / "execution-source.json").read_text(encoding="utf-8")
        )
        module = discovery["modules"][0]
        inventory = list(module_inventory(discovery["modules"]))
        source_entry = next(
            item for item in manifest["entries"]
            if item["path"] == module["source_path"]
        )
        return {
            "schema_name": "video2pdf.project-test-module-assignment",
            "schema_version": 2,
            "repo_root": str(PROJECT_ROOT),
            "execution_root": str(
                (run_dir / "execution-source-files").resolve(strict=True)
            ),
            "module_key": module["module_key"],
            "suite_id": module["suite_id"],
            "source_path": module["source_path"],
            "test_ids": module["test_ids"],
            "worker_launch_nonce": nonce,
            "source_manifest_sha256": test_run[
                "source_manifest_sha256"
            ],
            "source_snapshot_id": test_run["source_snapshot_id"],
            "source_snapshot_sha256": test_run[
                "source_snapshot_sha256"
            ],
            "module_inventory": inventory,
            "module_inventory_sha256": hashlib.sha256(
                canonical_json_bytes(inventory)
            ).hexdigest(),
            "source_sha256": source_entry["runtime_sha256"],
        }

    def install_capability(self, run_dir: Path, suite_id: str) -> None:
        identity = resolve_active_worker_identity(
            self.environment(run_dir, suite_id),
            project_root=PROJECT_ROOT,
            expected_suite=suite_id,
            authority_assignment=self.authority_assignment(run_dir),
        )
        self.assertIsNotNone(identity)

    def test_fixture_helper_rejects_cold_worker_without_assignment(self) -> None:
        run_dir = create_synthetic_project_test_run(
            external_root=Path("D:/tests"),
            project_root=PROJECT_ROOT,
            suite_id="project-test-runner",
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
        )

        with self.assertRaisesRegex(
            _fixture_root.FixtureRootError,
            "complete scheduler assignment",
        ):
            _fixture_root.fixture_root_from_environment(
                self.environment(run_dir, "project-test-runner"),
                PROJECT_ROOT,
                expected_suite="project-test-runner",
            )

    def test_zero_nonce_cannot_seed_cold_capability_without_assignment(
        self,
    ) -> None:
        run_dir = create_synthetic_project_test_run(
            external_root=Path("D:/tests"),
            project_root=PROJECT_ROOT,
            suite_id="project-test-runner",
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
        )
        zero_nonce_assignment = self.authority_assignment(
            run_dir,
            nonce="0" * 64,
        )

        with self.assertRaisesRegex(
            ProjectTestRunIdentityError,
            "complete scheduler assignment",
        ):
            resolve_active_worker_identity(
                self.environment(run_dir, "project-test-runner"),
                project_root=PROJECT_ROOT,
                expected_suite="project-test-runner",
            )
        identity = resolve_active_worker_identity(
            self.environment(run_dir, "project-test-runner"),
            project_root=PROJECT_ROOT,
            expected_suite="project-test-runner",
            authority_assignment=zero_nonce_assignment,
        )
        self.assertIsNotNone(identity)
        self.assertIsNotNone(
            resolve_active_worker_identity(
                self.environment(run_dir, "project-test-runner"),
                project_root=PROJECT_ROOT,
                expected_suite="project-test-runner",
            )
        )

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
            authority_assignment=self.authority_assignment(run_dir),
        )

        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.run_dir, run_dir)
        self.assertEqual(identity.suite_id, "project-test-runner")
        self.assertEqual(identity.module_key, self.MODULE_KEY)
        self.assertEqual(identity.test_ids, tuple(self.TEST_IDS))
        self.assertIsNotNone(
            resolve_active_worker_identity(
                self.environment(run_dir, "project-test-runner"),
                project_root=PROJECT_ROOT,
                expected_suite="project-test-runner",
            )
        )

    def test_production_identity_api_accepts_current_v2_run_schema(self) -> None:
        run_dir = create_synthetic_project_test_run(
            external_root=Path("D:/tests"),
            project_root=PROJECT_ROOT,
            suite_id="project-test-runner",
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
        )
        test_run_path = run_dir / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        self.assertEqual(test_run["schema_version"], 2)
        self.assertEqual(set(test_run), self.TEST_RUN_V2_FIELDS)
        source_manifest_path = Path(test_run["source_manifest_path"])
        source_snapshot_path = Path(test_run["source_snapshot_path"])
        self.assertTrue(source_manifest_path.is_file())
        self.assertTrue(source_snapshot_path.is_file())
        source_manifest_bytes = source_manifest_path.read_bytes()
        source_snapshot_bytes = source_snapshot_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(source_manifest_bytes).hexdigest(),
            test_run["source_manifest_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(source_snapshot_bytes).hexdigest(),
            test_run["source_snapshot_sha256"],
        )
        source_manifest = json.loads(source_manifest_bytes)
        source_snapshot = json.loads(source_snapshot_bytes)
        self.assertEqual(
            canonical_json_bytes(source_manifest),
            source_manifest_bytes,
        )
        self.assertEqual(
            canonical_json_bytes(source_snapshot),
            source_snapshot_bytes,
        )
        self.assertEqual(
            source_manifest["schema_name"],
            "video2pdf.project-test-execution-source",
        )
        self.assertEqual(
            source_snapshot["schema_name"],
            "video2pdf.project-test-source-snapshot",
        )
        self.assertEqual(
            source_snapshot["source_manifest_sha256"],
            test_run["source_manifest_sha256"],
        )
        self.assertEqual(
            source_snapshot["prevalidation"],
            {
                "result": "passed",
                "source_manifest_sha256": test_run[
                    "source_manifest_sha256"
                ],
            },
        )
        self.assertEqual(
            source_snapshot["entry_inventory"]["count"],
            len(source_manifest["entries"]),
        )
        self.assertEqual(
            source_snapshot["source_snapshot_id"],
            test_run["source_snapshot_id"],
        )
        snapshot_payload = {
            key: value
            for key, value in source_snapshot.items()
            if key != "source_snapshot_id"
        }
        self.assertEqual(
            hashlib.sha256(
                canonical_json_bytes(snapshot_payload)
            ).hexdigest(),
            source_snapshot["source_snapshot_id"],
        )
        self.assertGreaterEqual(len(source_manifest["entries"]), 1)
        for entry in source_manifest["entries"]:
            frozen_path = run_dir / entry["frozen_path"]
            self.assertTrue(frozen_path.is_file())
            frozen_bytes = frozen_path.read_bytes()
            self.assertEqual(
                hashlib.sha256(frozen_bytes).hexdigest(),
                entry["runtime_sha256"],
            )
            committed_blob = subprocess.run(
                [
                    "git",
                    "rev-parse",
                    (
                        f"{source_manifest['commit']}:"
                        f"{entry['path']}"
                    ),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout.strip()
            committed_bytes = subprocess.run(
                ["git", "cat-file", "blob", committed_blob],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
            ).stdout
            self.assertEqual(entry["git_blob"], committed_blob)
            self.assertEqual(frozen_bytes, committed_bytes)

        identity = resolve_active_worker_identity(
            self.environment(run_dir, "project-test-runner"),
            project_root=PROJECT_ROOT,
            expected_suite="project-test-runner",
            authority_assignment=self.authority_assignment(run_dir),
        )

        self.assertIsNotNone(identity)
        self.assertIsNotNone(
            resolve_active_worker_identity(
                self.environment(run_dir, "project-test-runner"),
                project_root=PROJECT_ROOT,
                expected_suite="project-test-runner",
            )
        )

    def test_production_identity_api_rejects_v1_run_schema(self) -> None:
        run_dir = create_synthetic_project_test_run(
            external_root=Path("D:/tests"),
            project_root=PROJECT_ROOT,
            suite_id="project-test-runner",
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
        )
        test_run_path = run_dir / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        test_run["schema_version"] = 1
        test_run_path.write_bytes(canonical_json_bytes(test_run))

        with self.assertRaisesRegex(
            _fixture_root.FixtureRootError,
            "test-run.json schema is invalid",
        ):
            resolve_active_worker_identity(
                self.environment(run_dir, "project-test-runner"),
                project_root=PROJECT_ROOT,
                expected_suite="project-test-runner",
                authority_assignment=self.authority_assignment(run_dir),
            )

    def test_production_identity_api_rejects_unknown_run_schema(self) -> None:
        cases = (
            {"schema_name": "video2pdf.project-test-run-unknown"},
            {"schema_version": 3},
            {"schema_version": True},
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                run_dir = create_synthetic_project_test_run(
                    external_root=Path("D:/tests"),
                    project_root=PROJECT_ROOT,
                    suite_id="project-test-runner",
                    module_key=self.MODULE_KEY,
                    test_ids=self.TEST_IDS,
                )
                test_run_path = run_dir / "test-run.json"
                test_run = json.loads(
                    test_run_path.read_text(encoding="utf-8")
                )
                test_run.update(mutation)
                test_run_path.write_bytes(canonical_json_bytes(test_run))

                with self.assertRaisesRegex(
                    _fixture_root.FixtureRootError,
                    "test-run.json schema is invalid",
                ):
                    resolve_active_worker_identity(
                        self.environment(run_dir, "project-test-runner"),
                        project_root=PROJECT_ROOT,
                        expected_suite="project-test-runner",
                        authority_assignment=self.authority_assignment(
                            run_dir
                        ),
                    )

    def test_production_identity_api_rejects_source_artifact_drift(self) -> None:
        run_dir = create_synthetic_project_test_run(
            external_root=Path("D:/tests"),
            project_root=PROJECT_ROOT,
            suite_id="project-test-runner",
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
        )
        test_run_path = run_dir / "test-run.json"
        test_run = json.loads(test_run_path.read_text(encoding="utf-8"))
        test_run["source_manifest_sha256"] = "0" * 64
        test_run_path.write_bytes(canonical_json_bytes(test_run))

        with self.assertRaisesRegex(
            _fixture_root.FixtureRootError,
            "source authority is invalid",
        ):
            resolve_active_worker_identity(
                self.environment(run_dir, "project-test-runner"),
                project_root=PROJECT_ROOT,
                expected_suite="project-test-runner",
                authority_assignment=self.authority_assignment(run_dir),
            )

    def test_v2_constructor_rejects_all_field_drift_classes(self) -> None:
        run_dir = create_synthetic_project_test_run(
            external_root=Path("D:/tests"),
            project_root=PROJECT_ROOT,
            suite_id="project-test-runner",
            module_key=self.MODULE_KEY,
            test_ids=self.TEST_IDS,
        )
        test_run = json.loads(
            (run_dir / "test-run.json").read_text(encoding="utf-8")
        )
        fields = {
            key: value
            for key, value in test_run.items()
            if key not in {"schema_name", "schema_version"}
        }
        cases = (
            {key: value for key, value in fields.items() if key != "commit"},
            {**fields, "unknown": None},
            {**fields, "schema_name": "caller-owned"},
            {**fields, "schema_version": 2},
        )
        for drifted in cases:
            with self.subTest(fields=set(drifted)):
                with self.assertRaises(ProjectTestRunIdentityError) as raised:
                    build_project_test_run_v2(drifted)
                self.assertEqual(
                    type(raised.exception).__name__,
                    "ProjectTestRunContractError",
                )

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
                self.install_capability(run_dir, "project-test-runner")
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
        self.install_capability(run_dir, "project-test-runner")
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
                    resolve_active_worker_identity(
                        self.environment(run_dir, "project-test-runner"),
                        project_root=PROJECT_ROOT,
                        expected_suite="project-test-runner",
                        authority_assignment=self.authority_assignment(
                            run_dir
                        ),
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
            resolve_active_worker_identity(
                self.environment(run_dir, "project-test-runner"),
                project_root=PROJECT_ROOT,
                expected_suite="project-test-runner",
                authority_assignment=self.authority_assignment(run_dir),
            )


if __name__ == "__main__":
    unittest.main()

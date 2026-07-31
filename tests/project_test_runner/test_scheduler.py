from __future__ import annotations

from contextlib import nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

import scripts.project_test_scheduler as scheduler
import scripts.project_test_run_identity as run_identity
import scripts.project_test_source_provenance as source_provenance

from scripts.project_test_external_root import (
    create_unique_run_directory,
    ensure_project_root,
)
from scripts.project_test_scheduler import (
    SchedulerError,
    run_modules,
    validate_jobs,
)
from scripts.project_test_source_provenance import (
    SourceProvenanceError,
    finalize_source_snapshot,
    validate_source_snapshot_binding,
)
from tests.project_test_runner._fixture_root import (
    committed_fixture_root,
    new_fixture_dir,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = (
    committed_fixture_root() / "scheduler_results"
)


def run_directory(label: str) -> Path:
    external_root = new_fixture_dir(label)
    project_root = ensure_project_root(
        external_root,
        "https://github.com/Nishijujuba/video2pdf.git",
    )
    return create_unique_run_directory(
        project_root,
        "fixture",
        registered_suite_keys={"fixture"},
    )


def _test_id(source: Path) -> str:
    names = {
        "worker_fast.py": "worker_fast.FastTests.test_output_and_environment",
        "worker_slow.py": "worker_slow.SlowTests.test_slow",
        "worker_failure.py": "worker_failure.FailureTests.test_failure",
        "worker_after_failure.py": (
            "worker_after_failure.AfterFailureTests.test_records_execution"
        ),
        "worker_import_error.py": (
            "unittest.loader._FailedTest.worker_import_error"
        ),
    }
    return names[source.name]


def _module_key(suite_id: str, source_path: str) -> str:
    return hashlib.sha256(
        f"{suite_id}\0{source_path}".encode("utf-8")
    ).hexdigest()[:12]


def discovery(*names: str) -> dict:
    modules = []
    all_ids = []
    for index, name in enumerate(names):
        source = FIXTURES / name
        test_id = _test_id(source)
        source_path = source.relative_to(REPO_ROOT).as_posix()
        all_ids.append(test_id)
        modules.append(
            {
                "suite_id": "fixture",
                "root_path": FIXTURES.relative_to(REPO_ROOT).as_posix(),
                "source_path": source_path,
                "module_key": _module_key("fixture", source_path),
                "test_count": 1,
                "test_ids": [test_id],
            }
        )
    canonical_ids = (
        json.dumps(
            sorted(all_ids),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return {
        "schema_name": "video2pdf.project-test-discovery",
        "schema_version": 1,
        "project": {
            "project_key": "video2pdf",
            "repository": "Nishijujuba/video2pdf",
        },
        "commit": "fixture-commit",
        "registry_path": "config/test-suites.v1.json",
        "registry_sha256": "a" * 64,
        "discovery_arguments": {"suite_ids": ["fixture"]},
        "suite_ids": ["fixture"],
        "suites": [],
        "modules": modules,
        "duplicate_test_ids": [],
        "total_count": len(all_ids),
        "test_id_set_sha256": hashlib.sha256(canonical_ids).hexdigest(),
    }


def synthetic_authority_case(
    *,
    suite_id: str = "fixture",
    test_ids: list[str] | None = None,
) -> tuple[Path, dict, dict]:
    from scripts.project_test_run_identity import (
        create_synthetic_project_test_run,
    )
    from scripts.project_test_results import canonical_json_bytes
    from scripts.project_test_source_provenance import module_inventory

    assigned_test_ids = test_ids or ["case.CaseTests.test_bound"]
    run_dir = create_synthetic_project_test_run(
        external_root=Path("D:/tests"),
        project_root=REPO_ROOT,
        suite_id=suite_id,
        module_key="123456789abc",
        test_ids=assigned_test_ids,
    )
    discovery_value = json.loads(
        (run_dir / "discovery.json").read_text(encoding="utf-8")
    )
    test_run = json.loads(
        (run_dir / "test-run.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (run_dir / "execution-source.json").read_text(encoding="utf-8")
    )
    module = discovery_value["modules"][0]
    inventory = list(module_inventory(discovery_value["modules"]))
    source_entry = next(
        item for item in manifest["entries"]
        if item["path"] == module["source_path"]
    )
    assignment = {
        "repo_root": str(REPO_ROOT),
        "execution_root": str(
            (run_dir / "execution-source-files").resolve(strict=True)
        ),
        "source_manifest_sha256": test_run["source_manifest_sha256"],
        "source_snapshot_id": test_run["source_snapshot_id"],
        "source_snapshot_sha256": test_run["source_snapshot_sha256"],
        "module_inventory_sha256": hashlib.sha256(
            canonical_json_bytes(inventory)
        ).hexdigest(),
        "module_inventory": inventory,
        "module_key": module["module_key"],
        "suite_id": module["suite_id"],
        "source_path": module["source_path"],
        "test_ids": assigned_test_ids,
        "source_sha256": source_entry["runtime_sha256"],
    }
    return run_dir, assignment, test_run


def clear_worker_validation_caches() -> None:
    with source_provenance._WORKER_AUTHORITY_CACHE_LOCK:
        source_provenance._WORKER_AUTHORITY_CACHE.clear()
    with run_identity._ACTIVE_WORKER_IDENTITY_CAPABILITY_LOCK:
        run_identity._ACTIVE_WORKER_IDENTITY_CAPABILITIES.clear()


def complete_worker_assignment(
    authority_assignment: dict,
    *,
    nonce: str = "a" * 64,
) -> dict:
    return {
        "schema_name": "video2pdf.project-test-module-assignment",
        "schema_version": 2,
        **authority_assignment,
        "worker_launch_nonce": nonce,
    }


class SchedulerTests(unittest.TestCase):
    def test_postrun_source_drift_writes_blocking_finalization(self) -> None:
        run_dir = run_directory("postrun-source-drift")
        snapshot = {
            "source_snapshot_id": "a" * 64,
            "source_manifest_sha256": "b" * 64,
        }
        with mock.patch(
            "scripts.project_test_source_provenance."
            "validate_execution_source_manifest",
            side_effect=SourceProvenanceError("frozen source drift"),
        ):
            finalization, finalization_sha256 = finalize_source_snapshot(
                REPO_ROOT,
                run_dir,
                source_snapshot=snapshot,
                source_snapshot_sha256="c" * 64,
                source_manifest={},
                expected_test_module_paths=[],
                scheduler_success=True,
                scheduler_failure_kind=None,
                summary_sha256="d" * 64,
            )

        self.assertFalse(finalization["success"])
        self.assertEqual(
            finalization["failure_kind"],
            "source_postrun_failure",
        )
        self.assertEqual(finalization["postvalidation"]["result"], "failed")
        self.assertEqual(
            hashlib.sha256(
                (run_dir / "run-finalization.json").read_bytes()
            ).hexdigest(),
            finalization_sha256,
        )

    def test_worker_snapshot_binding_rejects_replay_and_assigned_module_drift(
        self,
    ) -> None:
        from scripts.project_test_run_identity import (
            create_synthetic_project_test_run,
        )
        from scripts.project_test_results import canonical_json_bytes
        from scripts.project_test_source_provenance import module_inventory

        test_ids = ["case.CaseTests.test_bound"]
        run_dir = create_synthetic_project_test_run(
            external_root=Path("D:/tests"),
            project_root=REPO_ROOT,
            suite_id="fixture",
            module_key="123456789abc",
            test_ids=test_ids,
        )
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
        module_inventory_sha256 = hashlib.sha256(
            canonical_json_bytes(inventory)
        ).hexdigest()
        source_entry = next(
            item
            for item in manifest["entries"]
            if item["path"] == module["source_path"]
        )
        assignment = {
            "repo_root": str(REPO_ROOT),
            "execution_root": str(
                (run_dir / "execution-source-files").resolve(strict=True)
            ),
            "source_manifest_sha256": test_run["source_manifest_sha256"],
            "source_snapshot_id": test_run["source_snapshot_id"],
            "source_snapshot_sha256": test_run["source_snapshot_sha256"],
            "module_inventory_sha256": module_inventory_sha256,
            "module_inventory": inventory,
            "module_key": module["module_key"],
            "suite_id": module["suite_id"],
            "source_path": module["source_path"],
            "test_ids": test_ids,
            "source_sha256": source_entry["runtime_sha256"],
        }

        validate_source_snapshot_binding(run_dir, assignment)

        with self.assertRaisesRegex(
            SourceProvenanceError,
            "module inventory membership",
        ):
            validate_source_snapshot_binding(
                run_dir,
                {
                    **assignment,
                    "module_key": "fedcba987654",
                },
            )
        replay = {**assignment, "source_snapshot_id": "c" * 64}
        with self.assertRaisesRegex(SourceProvenanceError, "snapshot identity"):
            validate_source_snapshot_binding(run_dir, replay)

    def test_worker_snapshot_binding_caches_full_validation_by_artifact_identity(
        self,
    ) -> None:
        from scripts.project_test_run_identity import (
            create_synthetic_project_test_run,
        )
        from scripts.project_test_results import canonical_json_bytes
        from scripts.project_test_source_provenance import module_inventory

        test_ids = ["case.CaseTests.test_bound"]
        run_dir = create_synthetic_project_test_run(
            external_root=Path("D:/tests"),
            project_root=REPO_ROOT,
            suite_id="fixture",
            module_key="123456789abc",
            test_ids=test_ids,
        )
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
        assignment = {
            "repo_root": str(REPO_ROOT),
            "execution_root": str(
                (run_dir / "execution-source-files").resolve(strict=True)
            ),
            "source_manifest_sha256": test_run["source_manifest_sha256"],
            "source_snapshot_id": test_run["source_snapshot_id"],
            "source_snapshot_sha256": test_run["source_snapshot_sha256"],
            "module_inventory_sha256": hashlib.sha256(
                canonical_json_bytes(inventory)
            ).hexdigest(),
            "module_inventory": inventory,
            "module_key": module["module_key"],
            "suite_id": module["suite_id"],
            "source_path": module["source_path"],
            "test_ids": test_ids,
            "source_sha256": source_entry["runtime_sha256"],
        }

        clear_worker_validation_caches()
        real_validate = (
            source_provenance._validate_source_snapshot_binding_uncached
        )
        with mock.patch.object(
            source_provenance,
            "_validate_source_snapshot_binding_uncached",
            wraps=real_validate,
        ) as full_validate:
            validate_source_snapshot_binding(run_dir, assignment)
            validate_source_snapshot_binding(run_dir, assignment)
            self.assertEqual(full_validate.call_count, 1)

            source_path = (
                run_dir / "execution-source-files" / module["source_path"]
            )
            source_path.write_bytes(source_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                SourceProvenanceError,
                "fingerprint mismatch|not committed",
            ):
                validate_source_snapshot_binding(run_dir, assignment)
            self.assertEqual(full_validate.call_count, 2)

    def test_video_case_and_workspace_helpers_share_one_full_validation(
        self,
    ) -> None:
        import tests.video_workflow._test_run as video_test_run
        from scripts.project_test_run_identity import (
            RUN_DIR_ENV,
            SUITE_ID_ENV,
            MODULE_KEY_ENV,
            create_synthetic_project_test_run,
        )

        test_ids = [
            "case.CaseTests.test_case",
            "case.CaseTests.test_workspace",
        ]
        run_dir = create_synthetic_project_test_run(
            external_root=Path("D:/tests"),
            project_root=REPO_ROOT,
            suite_id="video-workflow",
            module_key="123456789abc",
            test_ids=test_ids,
        )
        environment = {
            RUN_DIR_ENV: str(run_dir),
            SUITE_ID_ENV: "video-workflow",
            MODULE_KEY_ENV: "123456789abc",
        }
        clear_worker_validation_caches()
        video_test_run._recorded_paths.clear()
        real_validate = (
            source_provenance._validate_source_snapshot_binding_uncached
        )
        with mock.patch.object(
            video_test_run,
            "FROZEN_RUN_ENV",
            environment,
        ), mock.patch.object(
            source_provenance,
            "_validate_source_snapshot_binding_uncached",
            wraps=real_validate,
        ) as full_validate:
            case_dir = video_test_run.new_case_dir(
                test_ids[0],
                label="case",
            )
            workspace = video_test_run.new_workflow_workspace(
                test_ids[1],
                label="workspace",
            )

        self.assertTrue(case_dir.is_dir())
        self.assertTrue(workspace.is_dir())
        self.assertEqual(full_validate.call_count, 1)

    def test_worker_scheduler_validation_and_fixture_helper_share_cache_key(
        self,
    ) -> None:
        import tests.video_workflow._test_run as video_test_run
        from scripts.project_test_run_identity import (
            MODULE_KEY_ENV,
            RUN_DIR_ENV,
            SUITE_ID_ENV,
        )
        from scripts.project_test_results import canonical_json_bytes

        test_ids = ["case.CaseTests.test_worker_lifecycle"]
        run_dir, authority_assignment, _test_run = synthetic_authority_case(
            suite_id="video-workflow",
            test_ids=test_ids,
        )
        modules_dir = run_dir / "modules"
        modules_dir.mkdir()
        assignment = complete_worker_assignment(authority_assignment)
        assignment_path = modules_dir / "123456789abc.assignment.json"
        assignment_path.write_bytes(canonical_json_bytes(assignment))
        result_path = modules_dir / "123456789abc.result.json"
        environment = {
            RUN_DIR_ENV: str(run_dir),
            SUITE_ID_ENV: "video-workflow",
            MODULE_KEY_ENV: "123456789abc",
        }

        class FixtureHelperCase(unittest.TestCase):
            def runTest(self) -> None:
                video_test_run.new_case_dir(
                    test_ids[0],
                    label="worker-lifecycle",
                )
                video_test_run.new_workflow_workspace(
                    test_ids[0],
                    label="worker-workspace",
                )

        clear_worker_validation_caches()
        video_test_run._recorded_paths.clear()
        real_validate = (
            source_provenance._validate_source_snapshot_binding_uncached
        )
        real_resolve = run_identity._resolve_active_worker_identity_uncached
        real_live_commit = run_identity._live_commit
        real_live_registry = run_identity._live_registry_sha256
        with mock.patch.dict(os.environ, environment), mock.patch.object(
            video_test_run,
            "FROZEN_RUN_ENV",
            environment,
        ), mock.patch.object(
            scheduler,
            "_load_assigned_suite",
            return_value=unittest.TestSuite([FixtureHelperCase()]),
        ), mock.patch.object(
            source_provenance,
            "_validate_source_snapshot_binding_uncached",
            wraps=real_validate,
        ) as full_validate, mock.patch.object(
            run_identity,
            "_resolve_active_worker_identity_uncached",
            wraps=real_resolve,
        ) as full_resolve, mock.patch.object(
            run_identity,
            "_live_commit",
            wraps=real_live_commit,
        ) as live_commit, mock.patch.object(
            run_identity,
            "_live_registry_sha256",
            wraps=real_live_registry,
        ) as live_registry:
            exit_code = scheduler.run_module_worker(
                assignment_path,
                result_path,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(full_validate.call_count, 1)
        self.assertEqual(full_resolve.call_count, 1)
        self.assertEqual(live_commit.call_count, 1)
        self.assertEqual(live_registry.call_count, 1)

    def test_worker_cache_live_inputs_invalidate_and_reject_drift(self) -> None:
        run_dir, authority_assignment, _test_run = synthetic_authority_case()
        assignment = complete_worker_assignment(authority_assignment)
        clear_worker_validation_caches()
        validate_source_snapshot_binding(run_dir, assignment)
        real_validate = (
            source_provenance._validate_source_snapshot_binding_uncached
        )

        with self.subTest(input="head"), mock.patch.object(
            source_provenance,
            "_commit_and_tree",
            return_value=("0" * 40, "1" * 40),
        ), mock.patch.object(
            source_provenance,
            "_validate_source_snapshot_binding_uncached",
            wraps=real_validate,
        ) as full_validate:
            with self.assertRaisesRegex(
                SourceProvenanceError,
                "authority binding",
            ):
                validate_source_snapshot_binding(run_dir, assignment)
            self.assertEqual(full_validate.call_count, 1)

        for label, target in (
            ("registry", REPO_ROOT / "config/test-suites.v1.json"),
            ("project-marker", run_dir.parent.parent / "project.json"),
        ):
            with self.subTest(input=label):
                clear_worker_validation_caches()
                validate_source_snapshot_binding(run_dir, assignment)
                real_snapshot = source_provenance.read_file_snapshot

                def drift_snapshot(path):
                    resolved, content, identity = real_snapshot(path)
                    if Path(path).resolve(strict=True) == target.resolve(
                        strict=True
                    ):
                        return resolved, content + b"\n", identity
                    return resolved, content, identity

                with mock.patch.object(
                    source_provenance,
                    "read_file_snapshot",
                    side_effect=drift_snapshot,
                ), mock.patch.object(
                    source_provenance,
                    "_validate_source_snapshot_binding_uncached",
                    wraps=real_validate,
                ) as full_validate:
                    with self.assertRaisesRegex(
                        SourceProvenanceError,
                        "authority binding",
                    ):
                        validate_source_snapshot_binding(run_dir, assignment)
                    self.assertEqual(full_validate.call_count, 1)

        clear_worker_validation_caches()
        validate_source_snapshot_binding(run_dir, assignment)
        with mock.patch.object(
            source_provenance,
            "process_execution_identity",
            return_value=None,
        ), mock.patch.object(
            source_provenance,
            "_validate_source_snapshot_binding_uncached",
            wraps=real_validate,
        ) as full_validate:
            with self.assertRaisesRegex(
                SourceProvenanceError,
                "authority binding",
            ):
                validate_source_snapshot_binding(run_dir, assignment)
            self.assertEqual(full_validate.call_count, 1)

    def test_worker_cache_cold_path_rejects_toctou_and_does_not_cache_aba(
        self,
    ) -> None:
        run_dir, authority_assignment, _test_run = synthetic_authority_case()
        assignment = complete_worker_assignment(authority_assignment)
        clear_worker_validation_caches()
        marker_path = run_dir.parent.parent / "project.json"
        original = marker_path.read_bytes()
        self.addCleanup(marker_path.write_bytes, original)
        pre_validation_key = (
            source_provenance._worker_authority_cache_key(
                run_dir,
                assignment,
            )
        )
        self.assertNotIn(
            pre_validation_key,
            source_provenance._WORKER_AUTHORITY_CACHE,
        )

        def mutate_during_validation(*_args):
            marker_path.write_bytes(original + b"\n")
            return "a" * 64, "b" * 64

        try:
            with mock.patch.object(
                source_provenance,
                "_validate_source_snapshot_binding_uncached",
                side_effect=mutate_during_validation,
            ) as full_validate:
                with self.assertRaisesRegex(
                    SourceProvenanceError,
                    "changed during validation",
                ):
                    validate_source_snapshot_binding(run_dir, assignment)
                self.assertEqual(full_validate.call_count, 1)
                self.assertNotIn(
                    pre_validation_key,
                    source_provenance._WORKER_AUTHORITY_CACHE,
                )
        finally:
            marker_path.write_bytes(original)

        with mock.patch.object(
            source_provenance,
            "_validate_source_snapshot_binding_uncached",
            return_value=("a" * 64, "b" * 64),
        ) as full_validate:
            validate_source_snapshot_binding(run_dir, assignment)
            self.assertEqual(full_validate.call_count, 1)

    def test_worker_cache_warm_lookup_computes_one_key(self) -> None:
        run_dir, assignment, _test_run = synthetic_authority_case()
        clear_worker_validation_caches()
        validate_source_snapshot_binding(run_dir, assignment)
        real_key = source_provenance._worker_authority_cache_key
        with mock.patch.object(
            source_provenance,
            "_worker_authority_cache_key",
            wraps=real_key,
        ) as cache_key:
            validate_source_snapshot_binding(run_dir, assignment)
        self.assertEqual(cache_key.call_count, 1)

    def test_active_identity_capability_partitions_pid_environment_and_assignment(
        self,
    ) -> None:
        from scripts.project_test_run_identity import (
            MODULE_KEY_ENV,
            RUN_DIR_ENV,
            SUITE_ID_ENV,
        )

        run_dir, authority_assignment, _test_run = synthetic_authority_case()
        assignment = complete_worker_assignment(authority_assignment)
        environment = {
            RUN_DIR_ENV: str(run_dir),
            SUITE_ID_ENV: "fixture",
            MODULE_KEY_ENV: "123456789abc",
        }
        clear_worker_validation_caches()
        identity = run_identity.resolve_active_worker_identity(
            environment,
            project_root=REPO_ROOT,
            expected_suite="fixture",
            authority_assignment=assignment,
        )
        self.assertIsNotNone(identity)

        with self.assertRaisesRegex(
            run_identity.ProjectTestRunIdentityError,
            "differs from scheduler assignment",
        ):
            run_identity.resolve_active_worker_identity(
                environment,
                project_root=REPO_ROOT,
                expected_suite="fixture",
                authority_assignment={
                    **assignment,
                    "source_sha256": "0" * 64,
                },
            )

        real_resolve = run_identity._resolve_active_worker_identity_uncached
        with mock.patch.object(
            run_identity.os,
            "getpid",
            return_value=os.getpid() + 1000,
        ), mock.patch.object(
            run_identity,
            "_resolve_active_worker_identity_uncached",
            wraps=real_resolve,
        ) as full_resolve:
            self.assertIsNotNone(
                run_identity.resolve_active_worker_identity(
                    environment,
                    project_root=REPO_ROOT,
                    expected_suite="fixture",
                    authority_assignment=assignment,
                )
            )
            self.assertEqual(full_resolve.call_count, 1)

        for label, mutation in (
            ("nonce", {"worker_launch_nonce": "b" * 64}),
            ("schema", {"schema_version": 1}),
            ("unknown", {"unknown": True}),
        ):
            with self.subTest(label=label):
                invalid_assignment = {
                    **assignment,
                    **mutation,
                }
                with self.assertRaises(
                    run_identity.ProjectTestRunIdentityError
                ):
                    run_identity.resolve_active_worker_identity(
                        environment,
                        project_root=REPO_ROOT,
                        expected_suite="fixture",
                        authority_assignment=invalid_assignment,
                    )
                if label != "nonce":
                    clear_worker_validation_caches()
                    with self.assertRaises(
                        run_identity.ProjectTestRunIdentityError
                    ):
                        run_identity.resolve_active_worker_identity(
                            environment,
                            project_root=REPO_ROOT,
                            expected_suite="fixture",
                            authority_assignment=invalid_assignment,
                        )
                    self.assertIsNotNone(
                        run_identity.resolve_active_worker_identity(
                            environment,
                            project_root=REPO_ROOT,
                            expected_suite="fixture",
                            authority_assignment=assignment,
                        )
                    )

        with mock.patch.object(
            run_identity,
            "_resolve_active_worker_identity_uncached",
            wraps=real_resolve,
        ) as full_resolve:
            with self.assertRaisesRegex(
                run_identity.ProjectTestRunIdentityError,
                "active module|module",
            ):
                run_identity.resolve_active_worker_identity(
                    {
                        **environment,
                        MODULE_KEY_ENV: "fedcba987654",
                    },
                    project_root=REPO_ROOT,
                    expected_suite="fixture",
                    authority_assignment=assignment,
                )
            self.assertEqual(full_resolve.call_count, 1)

    def test_worker_capability_assignment_mismatch_is_source_binding_failure(
        self,
    ) -> None:
        from scripts.project_test_run_identity import (
            MODULE_KEY_ENV,
            RUN_DIR_ENV,
            SUITE_ID_ENV,
        )
        from scripts.project_test_results import canonical_json_bytes

        run_dir, authority_assignment, _test_run = synthetic_authority_case()
        environment = {
            RUN_DIR_ENV: str(run_dir),
            SUITE_ID_ENV: "fixture",
            MODULE_KEY_ENV: "123456789abc",
        }
        installed_assignment = complete_worker_assignment(
            authority_assignment,
            nonce="a" * 64,
        )
        clear_worker_validation_caches()
        self.assertIsNotNone(
            run_identity.resolve_active_worker_identity(
                environment,
                project_root=REPO_ROOT,
                expected_suite="fixture",
                authority_assignment=installed_assignment,
            )
        )

        modules_dir = run_dir / "modules"
        modules_dir.mkdir()
        drifted_assignment = {
            **installed_assignment,
            "worker_launch_nonce": "b" * 64,
        }
        assignment_path = modules_dir / "123456789abc.assignment.json"
        assignment_path.write_bytes(
            canonical_json_bytes(drifted_assignment)
        )
        result_path = modules_dir / "123456789abc.result.json"
        with mock.patch.dict(os.environ, environment):
            exit_code = scheduler.run_module_worker(
                assignment_path,
                result_path,
            )

        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            result["failure_kind"],
            "source_binding_failure",
        )

    def test_worker_snapshot_binding_rejects_authority_mutation_matrix(
        self,
    ) -> None:
        from scripts.project_test_run_identity import (
            create_synthetic_project_test_run,
        )
        from scripts.project_test_results import canonical_json_bytes
        from scripts.project_test_source_provenance import module_inventory

        def make_case(label: str):
            test_ids = [f"case.{label}.test_bound"]
            run_dir = create_synthetic_project_test_run(
                external_root=Path("D:/tests"),
                project_root=REPO_ROOT,
                suite_id="fixture",
                module_key="123456789abc",
                test_ids=test_ids,
            )
            discovery = json.loads(
                (run_dir / "discovery.json").read_text(encoding="utf-8")
            )
            test_run = json.loads(
                (run_dir / "test-run.json").read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (run_dir / "execution-source.json").read_text(
                    encoding="utf-8"
                )
            )
            snapshot = json.loads(
                (run_dir / "source-snapshot.json").read_text(
                    encoding="utf-8"
                )
            )
            module = discovery["modules"][0]
            inventory = list(module_inventory(discovery["modules"]))
            source_entry = next(
                item for item in manifest["entries"]
                if item["path"] == module["source_path"]
            )
            assignment = {
                "repo_root": str(REPO_ROOT),
                "execution_root": str(
                    (run_dir / "execution-source-files").resolve(strict=True)
                ),
                "source_manifest_sha256": test_run[
                    "source_manifest_sha256"
                ],
                "source_snapshot_id": test_run["source_snapshot_id"],
                "source_snapshot_sha256": test_run[
                    "source_snapshot_sha256"
                ],
                "module_inventory_sha256": hashlib.sha256(
                    canonical_json_bytes(inventory)
                ).hexdigest(),
                "module_inventory": inventory,
                "module_key": module["module_key"],
                "suite_id": module["suite_id"],
                "source_path": module["source_path"],
                "test_ids": test_ids,
                "source_sha256": source_entry["runtime_sha256"],
            }
            return run_dir, test_run, manifest, snapshot, assignment

        def resign(
            run_dir: Path,
            test_run: dict,
            manifest: dict,
            snapshot: dict,
            assignment: dict,
        ) -> None:
            manifest_bytes = canonical_json_bytes(manifest)
            (run_dir / "execution-source.json").write_bytes(manifest_bytes)
            manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
            snapshot["source_manifest_sha256"] = manifest_sha
            snapshot["prevalidation"]["source_manifest_sha256"] = manifest_sha
            inventory = [
                {
                    "path": item["path"],
                    "git_blob": item["git_blob"],
                    "runtime_sha256": item["runtime_sha256"],
                    "runtime_size": item["runtime_size"],
                }
                for item in manifest["entries"]
            ]
            snapshot["entry_inventory"] = {
                "count": len(inventory),
                "sha256": hashlib.sha256(
                    canonical_json_bytes(inventory)
                ).hexdigest(),
            }
            payload = {
                key: value for key, value in snapshot.items()
                if key != "source_snapshot_id"
            }
            snapshot["source_snapshot_id"] = hashlib.sha256(
                canonical_json_bytes(payload)
            ).hexdigest()
            snapshot_bytes = canonical_json_bytes(snapshot)
            (run_dir / "source-snapshot.json").write_bytes(snapshot_bytes)
            test_run["source_manifest_sha256"] = manifest_sha
            test_run["source_snapshot_id"] = snapshot["source_snapshot_id"]
            test_run["source_snapshot_sha256"] = hashlib.sha256(
                snapshot_bytes
            ).hexdigest()
            (run_dir / "test-run.json").write_bytes(
                canonical_json_bytes(test_run)
            )
            assignment.update(
                {
                    "source_manifest_sha256": manifest_sha,
                    "source_snapshot_id": snapshot["source_snapshot_id"],
                    "source_snapshot_sha256": test_run[
                        "source_snapshot_sha256"
                    ],
                }
            )

        assignment_mutations = (
            ("suite-id", {"suite_id": "other"}, "membership"),
            ("test-ids", {"test_ids": ["other.test"]}, "membership"),
            (
                "execution-root",
                {"execution_root": str(REPO_ROOT)},
                "snapshot binding",
            ),
        )
        for label, mutation, message in assignment_mutations:
            with self.subTest(label=label):
                run_dir, _test_run, _manifest, _snapshot, assignment = (
                    make_case(label)
                )
                with self.assertRaisesRegex(SourceProvenanceError, message):
                    validate_source_snapshot_binding(
                        run_dir,
                        {**assignment, **mutation},
                    )

        for label, nested, message in (
            ("snapshot-extra", False, "snapshot is invalid"),
            ("prevalidation-extra", True, "nested authority"),
        ):
            with self.subTest(label=label):
                run_dir, test_run, manifest, snapshot, assignment = (
                    make_case(label)
                )
                if nested:
                    snapshot["prevalidation"]["unknown"] = True
                    resign(
                        run_dir,
                        test_run,
                        manifest,
                        snapshot,
                        assignment,
                    )
                else:
                    snapshot["unknown"] = True
                    (run_dir / "source-snapshot.json").write_bytes(
                        canonical_json_bytes(snapshot)
                    )
                with self.assertRaisesRegex(SourceProvenanceError, message):
                    validate_source_snapshot_binding(run_dir, assignment)

        run_dir, _test_run, _manifest, _snapshot, assignment = make_case(
            "frozen-source-drift"
        )
        frozen_source = (
            run_dir / "execution-source-files" / assignment["source_path"]
        )
        frozen_source.write_bytes(frozen_source.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            SourceProvenanceError,
            "fingerprint mismatch|not committed",
        ):
            validate_source_snapshot_binding(run_dir, assignment)

        run_dir, test_run, manifest, snapshot, assignment = make_case(
            "escaped-authority"
        )
        manifest["git_authority"]["authority_path"] = "../execution-git"
        resign(run_dir, test_run, manifest, snapshot, assignment)
        with self.assertRaisesRegex(SourceProvenanceError, "frozen Git authority"):
            validate_source_snapshot_binding(run_dir, assignment)

        run_dir, _test_run, _manifest, _snapshot, assignment = make_case(
            "authority-file-drift"
        )
        config_path = run_dir / "execution-git" / "config"
        config_path.write_bytes(config_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(SourceProvenanceError, "frozen Git authority"):
            validate_source_snapshot_binding(run_dir, assignment)

        selected_entry_mutations = (
            ("entry-type", {"runtime_size": "invalid"}),
            ("entry-path", {"path": "../escaped"}),
            ("entry-blob", {"git_blob": "0" * 40}),
        )
        for label, mutation in selected_entry_mutations:
            with self.subTest(label=label):
                run_dir, test_run, manifest, snapshot, assignment = make_case(
                    label
                )
                entry = next(
                    item for item in manifest["entries"]
                    if item["path"] == assignment["source_path"]
                )
                entry.update(mutation)
                resign(run_dir, test_run, manifest, snapshot, assignment)
                with self.assertRaisesRegex(
                    SourceProvenanceError,
                    "source manifest|assigned module|inventory",
                ):
                    validate_source_snapshot_binding(run_dir, assignment)

        run_dir, test_run, manifest, snapshot, assignment = make_case(
            "self-consistent-resign"
        )
        forged_commit = "0" * 40
        test_run["commit"] = forged_commit
        manifest["commit"] = forged_commit
        manifest["git_authority"]["head_commit"] = forged_commit
        snapshot["commit"] = forged_commit
        resign(run_dir, test_run, manifest, snapshot, assignment)
        with self.assertRaisesRegex(SourceProvenanceError, "authority binding"):
            validate_source_snapshot_binding(run_dir, assignment)

    def test_worker_preserves_import_paths_added_by_test_module(self) -> None:
        fixture_repo = new_fixture_dir(
            "worker-path-lifecycle",
            expected_suite="project-test-runner",
        )
        tests_dir = fixture_repo / "tests"
        source_dir = fixture_repo / "src"
        tests_dir.mkdir()
        source_dir.mkdir()
        dependency_name = f"delayed_dependency_{fixture_repo.name[-8:]}"
        module_name = f"worker_path_lifecycle_{fixture_repo.name[-8:]}"
        (source_dir / f"{dependency_name}.py").write_text(
            "VALUE = 'available after discovery'\n",
            encoding="utf-8",
        )
        source = tests_dir / f"{module_name}.py"
        source.write_text(
            "\n".join(
                (
                    "import importlib",
                    "from pathlib import Path",
                    "import sys",
                    "import unittest",
                    "",
                    "DEPENDENCY_ROOT = Path(__file__).parents[1] / 'src'",
                    "sys.path.insert(0, str(DEPENDENCY_ROOT))",
                    "class ModulePathEntry(str):",
                    "    pass",
                    "",
                    "WORKER_PATH_ENTRY = ModulePathEntry(",
                    "    str(Path(__file__).parent)",
                    ")",
                    "sys.path.insert(0, WORKER_PATH_ENTRY)",
                    "",
                    "class PathLifecycleTests(unittest.TestCase):",
                    "    def test_delayed_import(self):",
                    "        self.assertTrue(any(",
                    "            entry is WORKER_PATH_ENTRY",
                    "            for entry in sys.path",
                    "        ))",
                    f"        dependency = importlib.import_module({dependency_name!r})",
                    "        self.assertEqual(",
                    "            dependency.VALUE,",
                    "            'available after discovery',",
                    "        )",
                    "",
                )
            ),
            encoding="utf-8",
        )
        dependency_path = str(source_dir)
        worker_path = str(tests_dir)
        test_id = (
            f"{module_name}.PathLifecycleTests.test_delayed_import"
        )

        try:
            suite = scheduler._load_assigned_suite(
                fixture_repo,
                source.relative_to(fixture_repo).as_posix(),
                [test_id],
            )
            result = unittest.TestResult()
            suite.run(result)

            self.assertTrue(result.wasSuccessful(), result.errors)
            self.assertIn(dependency_path, sys.path)
            self.assertEqual(
                [entry for entry in sys.path if entry == worker_path],
                [worker_path],
            )
        finally:
            while dependency_path in sys.path:
                sys.path.remove(dependency_path)
            while worker_path in sys.path:
                sys.path.remove(worker_path)
            sys.modules.pop(dependency_name, None)

    def test_discovery_rejects_noncanonical_module_paths(self) -> None:
        manifest = discovery("worker_fast.py")
        module = manifest["modules"][0]
        valid_root = module["root_path"]
        valid_source = module["source_path"]
        invalid_values = (
            "",
            f"/{valid_source}",
            f"C:/{valid_source}",
            f"C:\\{valid_source}",
            rf"\\server\share\{valid_source}",
            rf"\\?\C:\{valid_source}",
            f"./{valid_source}",
            f"{valid_root}/../{Path(valid_source).name}",
            valid_source.replace("/", "//", 1),
        )

        for field in ("root_path", "source_path"):
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=invalid):
                    changed = {**module, field: invalid}
                    if field == "source_path":
                        changed["module_key"] = _module_key(
                            changed["suite_id"], invalid
                        )
                    malformed = {**manifest, "modules": [changed]}
                    with self.assertRaisesRegex(
                        SchedulerError,
                        field,
                    ):
                        scheduler._validate_discovery(REPO_ROOT, malformed)

    def test_discovery_rejects_missing_or_inconsistent_module_sources(
        self,
    ) -> None:
        manifest = discovery("worker_fast.py")
        module = manifest["modules"][0]
        cases = (
            {
                **module,
                "root_path": "scripts",
            },
            {
                **module,
                "source_path": (
                    f"{module['root_path']}/missing_scheduler_fixture.py"
                ),
                "module_key": _module_key(
                    module["suite_id"],
                    f"{module['root_path']}/missing_scheduler_fixture.py",
                ),
            },
            {
                **module,
                "source_path": discovery("worker_slow.py")["modules"][0][
                    "source_path"
                ],
                "module_key": _module_key(
                    module["suite_id"],
                    discovery("worker_slow.py")["modules"][0]["source_path"],
                ),
            },
        )

        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(
                    SchedulerError,
                    "root_path|source_path|module source",
                ):
                    scheduler._validate_discovery(
                        REPO_ROOT,
                        {**manifest, "modules": [changed]},
                    )

    def test_discovery_rejects_noncanonical_or_mismatched_module_keys(self) -> None:
        manifest = discovery("worker_fast.py")
        invalid_keys = (
            "../escape000",
            "abc/def01234",
            r"abc\def01234",
            "ABCDEF012345",
            "abcdef01234",
            "abcdef0123456",
            "abcdef01234g",
            "000000000000",
        )

        for invalid_key in invalid_keys:
            with self.subTest(module_key=invalid_key):
                malformed = {
                    **manifest,
                    "modules": [
                        {**manifest["modules"][0], "module_key": invalid_key}
                    ],
                }
                with self.assertRaisesRegex(
                    SchedulerError,
                    "module_key",
                ):
                    scheduler._validate_discovery(REPO_ROOT, malformed)

    def test_discovery_rejects_duplicate_module_key_collision(self) -> None:
        manifest = discovery("worker_fast.py")
        manifest["modules"].append(dict(manifest["modules"][0]))
        manifest["total_count"] = 2
        manifest["test_id_set_sha256"] = hashlib.sha256(
            scheduler.canonical_json_bytes(
                sorted(
                    manifest["modules"][0]["test_ids"]
                    + manifest["modules"][1]["test_ids"]
                )
            )
        ).hexdigest()

        with self.assertRaisesRegex(
            SchedulerError,
            "duplicate module keys",
        ):
            scheduler._validate_discovery(REPO_ROOT, manifest)

    def test_rechecks_every_scheduler_artifact_path_before_creation_or_open(
        self,
    ) -> None:
        run_dir = run_directory("safe-artifact-paths")
        manifest = discovery("worker_fast.py")
        checked: list[Path] = []
        real_check = scheduler.assert_safe_write_path

        def record_check(base: Path, candidate: Path) -> Path:
            checked.append(Path(candidate))
            return real_check(base, candidate)

        with mock.patch.object(
            scheduler,
            "assert_safe_write_path",
            side_effect=record_check,
        ):
            summary = run_modules(
                repo_root=REPO_ROOT,
                run_dir=run_dir,
                discovery=manifest,
                jobs=1,
                stdout=io.BytesIO(),
                stderr=io.BytesIO(),
            )

        module_key = manifest["modules"][0]["module_key"]
        expected = {
            run_dir / "modules",
            run_dir / "logs",
            run_dir / "generated",
            run_dir / "generated" / module_key,
            run_dir / "modules" / f"{module_key}.assignment.json",
            run_dir / "modules" / f"{module_key}.result.json",
            run_dir / "logs" / f"{module_key}.stdout.log",
            run_dir / "logs" / f"{module_key}.stderr.log",
        }
        self.assertTrue(summary["success"])
        self.assertTrue(expected.issubset(set(checked)))

    def test_artifact_reparse_race_fails_before_assignment_write(self) -> None:
        run_dir = run_directory("assignment-reparse-race")
        manifest = discovery("worker_fast.py")
        module_key = manifest["modules"][0]["module_key"]
        assignment_path = (
            run_dir / "modules" / f"{module_key}.assignment.json"
        )
        real_check = scheduler.assert_safe_write_path

        def reject_assignment(base: Path, candidate: Path) -> Path:
            if Path(candidate) == assignment_path:
                raise scheduler.ExternalRootError("simulated reparse race")
            return real_check(base, candidate)

        with mock.patch.object(
            scheduler,
            "assert_safe_write_path",
            side_effect=reject_assignment,
        ):
            summary = run_modules(
                repo_root=REPO_ROOT,
                run_dir=run_dir,
                discovery=manifest,
                jobs=1,
                stdout=io.BytesIO(),
                stderr=io.BytesIO(),
            )

        self.assertEqual(summary["failure_kind"], "launch_failure")
        self.assertFalse(assignment_path.exists())

    def test_launch_setup_failures_close_every_opened_log_handle(self) -> None:
        cases = ("second-open", "environment")
        for case in cases:
            with self.subTest(case=case):
                run_dir = run_directory(f"handle-{case}")
                opened_handles = []
                real_open = Path.open

                def tracked_open(path, *args, **kwargs):
                    if (
                        case == "second-open"
                        and path.name.endswith(".stderr.log")
                        and args
                        and args[0] == "xb"
                    ):
                        raise OSError("simulated second log open failure")
                    handle = real_open(path, *args, **kwargs)
                    if (
                        path.name.endswith((".stdout.log", ".stderr.log"))
                        and args
                        and args[0] == "xb"
                    ):
                        opened_handles.append(handle)
                    return handle

                environment_patch = (
                    mock.patch.object(
                        scheduler.os.environ,
                        "copy",
                        side_effect=RuntimeError(
                            "simulated environment construction failure"
                        ),
                    )
                    if case == "environment"
                    else nullcontext()
                )
                with mock.patch.object(Path, "open", new=tracked_open):
                    with environment_patch:
                        summary = run_modules(
                            repo_root=REPO_ROOT,
                            run_dir=run_dir,
                            discovery=discovery("worker_fast.py"),
                            jobs=1,
                            stdout=io.BytesIO(),
                            stderr=io.BytesIO(),
                        )

                self.assertEqual(summary["failure_kind"], "launch_failure")
                self.assertTrue(opened_handles)
                self.assertTrue(all(handle.closed for handle in opened_handles))

    def test_jobs_are_bounded_to_one_through_four(self) -> None:
        self.assertEqual(validate_jobs(1), 1)
        self.assertEqual(validate_jobs(4), 4)
        for invalid in (0, 5, True, 1.5, "2"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(SchedulerError, "1..4"):
                    validate_jobs(invalid)

    def test_dynamic_queue_drains_output_and_continues_after_failure(self) -> None:
        run_dir = run_directory("failure-aggregation")
        forwarded_stdout = io.BytesIO()
        forwarded_stderr = io.BytesIO()
        summary = run_modules(
            repo_root=REPO_ROOT,
            run_dir=run_dir,
            discovery=discovery(
                "worker_failure.py",
                "worker_after_failure.py",
                "worker_slow.py",
                "worker_fast.py",
            ),
            jobs=2,
            stdout=forwarded_stdout,
            stderr=forwarded_stderr,
        )

        self.assertEqual(summary["failure_kind"], "test_failure")
        self.assertEqual(summary["requested_jobs"], 2)
        self.assertEqual(summary["observed_peak_concurrency"], 2)
        self.assertTrue((run_dir / "after-failure-ran.txt").is_file())
        self.assertIn(b"fast-stdout", forwarded_stdout.getvalue())
        self.assertIn(b"fast-stderr", forwarded_stderr.getvalue())
        self.assertIn(b"failure-output", forwarded_stderr.getvalue())
        self.assertEqual(summary["coverage"]["discovered"], 4)
        self.assertEqual(summary["coverage"]["assigned"], 4)
        self.assertEqual(summary["coverage"]["started"], 4)
        self.assertEqual(summary["coverage"]["terminal"], 4)
        self.assertEqual(summary["coverage"]["missing_test_ids"], [])
        self.assertEqual(summary["coverage"]["duplicate_test_ids"], [])
        self.assertEqual(summary["coverage"]["unassigned_test_ids"], [])
        self.assertEqual(
            summary["coverage"]["multiply_executed_test_ids"], []
        )

    def test_events_are_real_order_and_summary_is_stably_sorted(self) -> None:
        run_dir = run_directory("event-order")
        manifest = discovery("worker_fast.py", "worker_slow.py")
        # Reverse the manifest to prove result ordering is independent of
        # completion order.
        manifest["modules"].reverse()
        summary = run_modules(
            repo_root=REPO_ROOT,
            run_dir=run_dir,
            discovery=manifest,
            jobs=2,
            stdout=io.BytesIO(),
            stderr=io.BytesIO(),
        )
        events = [
            json.loads(line)
            for line in (run_dir / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        completions = [
            event["module_key"]
            for event in events
            if event["event"] == "completed"
        ]
        expected_keys = [
            module["module_key"]
            for module in manifest["modules"]
        ]
        self.assertEqual(completions, list(reversed(expected_keys)))
        self.assertEqual(
            [item["module_key"] for item in summary["modules"]],
            sorted(expected_keys),
        )
        self.assertEqual(summary["failure_kind"], None)

    def test_historical_timings_use_longest_first_and_validate_provenance(
        self,
    ) -> None:
        run_dir = run_directory("historical-timings")
        manifest = discovery("worker_fast.py", "worker_slow.py")
        timings = {
                "schema_name": "video2pdf.project-test-timings",
                "schema_version": 1,
                "project": manifest["project"],
                "suite_ids": manifest["suite_ids"],
                "modules": [
                    {
                        "module_key": manifest["modules"][0]["module_key"],
                        "source_path": manifest["modules"][0]["source_path"],
                        "duration_seconds": 99.0,
                    },
                    {
                        "module_key": manifest["modules"][1]["module_key"],
                        "source_path": manifest["modules"][1]["source_path"],
                        "duration_seconds": 1.0,
                    },
                ],
        }
        timing_path = run_dir / "historical.json"
        timing_path.write_text(json.dumps(timings), encoding="utf-8")
        run_modules(
                repo_root=REPO_ROOT,
                run_dir=run_dir,
                discovery=manifest,
                jobs=1,
                timings_from=timing_path,
                stdout=io.BytesIO(),
                stderr=io.BytesIO(),
        )
        events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
        ]
        starts = [
                event["module_key"]
                for event in events
                if event["event"] == "started"
        ]
        self.assertEqual(
            starts,
            [
                manifest["modules"][0]["module_key"],
                manifest["modules"][1]["module_key"],
            ],
        )

        bad_run = run_directory("bad-timings")
        timings["project"] = {
                "project_key": "other",
                "repository": "other/repo",
        }
        bad_path = bad_run / "bad.json"
        bad_path.write_text(json.dumps(timings), encoding="utf-8")
        with self.assertRaisesRegex(SchedulerError, "timing provenance"):
            run_modules(
                    repo_root=REPO_ROOT,
                    run_dir=bad_run,
                    discovery=manifest,
                    jobs=1,
                    timings_from=bad_path,
                    stdout=io.BytesIO(),
                stderr=io.BytesIO(),
            )

    def test_historical_timings_reject_nonfinite_and_negative_durations(
        self,
    ) -> None:
        manifest = discovery("worker_fast.py")
        module = manifest["modules"][0]
        for label, duration_token in (
            ("nan", "NaN"),
            ("positive-infinity", "Infinity"),
            ("negative-infinity", "-Infinity"),
            ("negative", "-0.001"),
        ):
            with self.subTest(duration=label):
                run_dir = run_directory(f"invalid-duration-{label}")
                timing_path = run_dir / "historical.json"
                timing_path.write_text(
                    (
                        '{"schema_name":"video2pdf.project-test-timings",'
                        '"schema_version":1,'
                        f'"project":{json.dumps(manifest["project"])},'
                        f'"suite_ids":{json.dumps(manifest["suite_ids"])},'
                        '"modules":[{'
                        f'"module_key":{json.dumps(module["module_key"])},'
                        f'"source_path":{json.dumps(module["source_path"])},'
                        f'"duration_seconds":{duration_token}'
                        "}]}"
                    ),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(
                    SchedulerError,
                    "timing provenance",
                ):
                    run_modules(
                        repo_root=REPO_ROOT,
                        run_dir=run_dir,
                        discovery=manifest,
                        jobs=1,
                        timings_from=timing_path,
                        stdout=io.BytesIO(),
                        stderr=io.BytesIO(),
                    )
                self.assertFalse((run_dir / "modules").exists())

    def test_worker_result_rejects_nonfinite_duration_before_timings(
        self,
    ) -> None:
        run_dir = run_directory("nonfinite-worker-duration")
        real_read_module_result = scheduler.read_module_result

        def nonfinite_result(*args, **kwargs):
            value = real_read_module_result(*args, **kwargs)
            return {**value, "duration_seconds": float("nan")}

        with mock.patch.object(
            scheduler,
            "read_module_result",
            side_effect=nonfinite_result,
        ):
            summary = run_modules(
                repo_root=REPO_ROOT,
                run_dir=run_dir,
                discovery=discovery("worker_fast.py"),
                jobs=1,
                stdout=io.BytesIO(),
                stderr=io.BytesIO(),
            )

        self.assertEqual(
            summary["failure_kind"],
            "result_integrity_failure",
        )
        timings = json.loads(
            (run_dir / "timings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(timings["modules"]), 1)
        self.assertEqual(
            timings["modules"][0]["duration_seconds"],
            0.0,
        )

    def test_launch_and_import_failures_are_structured_and_do_not_fail_fast(
        self,
    ) -> None:
        launch_run = run_directory("launch-failure")
        with mock.patch.object(
            scheduler.subprocess,
            "Popen",
            side_effect=OSError("simulated launch failure"),
        ):
            launch_summary = run_modules(
                repo_root=REPO_ROOT,
                run_dir=launch_run,
                discovery=discovery("worker_fast.py", "worker_slow.py"),
                jobs=2,
                stdout=io.BytesIO(),
                stderr=io.BytesIO(),
            )
        self.assertEqual(launch_summary["failure_kind"], "launch_failure")
        self.assertEqual(launch_summary["coverage"]["terminal"], 2)
        self.assertEqual(launch_summary["coverage"]["started"], 0)
        launch_events = [
            json.loads(line)
            for line in (launch_run / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            [
                event["event"]
                for event in launch_events
                if event["event"] != "queued"
            ],
            ["launch_failed", "launch_failed"],
        )

        import_run = run_directory("import-failure")
        manifest = discovery("worker_import_error.py", "worker_fast.py")
        # Discovery normally rejects this module. The handcrafted manifest
        # verifies the scheduler's execution-time import-failure boundary.
        import_summary = run_modules(
            repo_root=REPO_ROOT,
            run_dir=import_run,
            discovery=manifest,
            jobs=2,
            stdout=io.BytesIO(),
            stderr=io.BytesIO(),
        )
        self.assertEqual(import_summary["failure_kind"], "import_failure")
        self.assertEqual(import_summary["coverage"]["terminal"], 2)
        self.assertEqual(import_summary["coverage"]["executed_test_ids"], 1)

    def test_coordinator_exception_terminates_active_workers_and_records_failure(
        self,
    ) -> None:
        run_dir = run_directory("coordinator-exception")
        launched = []
        real_launch = scheduler._launch_module

        def capture_launch(**kwargs):
            active = real_launch(**kwargs)
            launched.append(active)
            return active

        with mock.patch.object(
            scheduler, "_launch_module", side_effect=capture_launch
        ), mock.patch.object(
            scheduler.time,
            "sleep",
            side_effect=KeyboardInterrupt("simulated coordinator interrupt"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_modules(
                    repo_root=REPO_ROOT,
                    run_dir=run_dir,
                    discovery=discovery("worker_slow.py", "worker_fast.py"),
                    jobs=1,
                    stdout=io.BytesIO(),
                    stderr=io.BytesIO(),
                )

        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].process.poll())
        summary = json.loads(
            (run_dir / "summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["failure_kind"], "coordinator_failure")
        self.assertEqual(summary["coverage"]["started"], 1)
        self.assertEqual(summary["coverage"]["terminal"], 2)
        self.assertEqual(
            {
                module["failure_kind"] for module in summary["modules"]
            },
            {"coordinator_failure"},
        )


if __name__ == "__main__":
    unittest.main()

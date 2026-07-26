from __future__ import annotations

from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

import scripts.project_test_scheduler as scheduler

from scripts.project_test_external_root import (
    create_unique_run_directory,
    ensure_project_root,
)
from scripts.project_test_scheduler import (
    SchedulerError,
    run_modules,
    validate_jobs,
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


class SchedulerTests(unittest.TestCase):
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

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
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


def discovery(*names: str) -> dict:
    modules = []
    all_ids = []
    for index, name in enumerate(names):
        source = FIXTURES / name
        test_id = _test_id(source)
        all_ids.append(test_id)
        modules.append(
            {
                "suite_id": "fixture",
                "root_path": FIXTURES.relative_to(REPO_ROOT).as_posix(),
                "source_path": source.relative_to(REPO_ROOT).as_posix(),
                "module_key": f"module{index:02d}",
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
        self.assertEqual(completions, ["module00", "module01"])
        self.assertEqual(
            [item["module_key"] for item in summary["modules"]],
            ["module00", "module01"],
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
                        "module_key": "module00",
                        "source_path": manifest["modules"][0]["source_path"],
                        "duration_seconds": 99.0,
                    },
                    {
                        "module_key": "module01",
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
        self.assertEqual(starts, ["module00", "module01"])

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

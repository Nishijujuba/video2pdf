from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests/project_test_runner/fixtures/cli_promotion"
SCRATCH = PROJECT_ROOT / "tests/project_test_runner/fixtures/external_root"
SCRIPT_NAMES = (
    "run_project_tests.py",
    "project_test_discovery.py",
    "project_test_external_root.py",
    "project_test_registry.py",
    "project_test_results.py",
    "project_test_scheduler.py",
)


class ProjectTestRunnerCliTests(unittest.TestCase):
    def make_fixture_repo(self) -> tuple[Path, Path]:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        repo = SCRATCH / f"repo-{uuid.uuid4().hex}"
        external = SCRATCH / f"external-{uuid.uuid4().hex}"
        repo.mkdir()
        external.mkdir()
        shutil.copytree(FIXTURE / "config", repo / "config")
        shutil.copytree(FIXTURE / "tests", repo / "tests")
        (repo / "scripts").mkdir()
        (repo / "scripts/__init__.py").write_text("", encoding="utf-8")
        for name in SCRIPT_NAMES:
            shutil.copy2(PROJECT_ROOT / "scripts" / name, repo / "scripts" / name)
        return repo, external

    def invoke(
        self, repo: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(repo / "scripts/run_project_tests.py"),
                *arguments,
            ],
            cwd=repo,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def final_record(self, completed: subprocess.CompletedProcess[str]) -> dict:
        records = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.startswith("{")
        ]
        self.assertTrue(records, completed.stdout)
        return records[-1]

    def test_discover_omits_suite_to_select_all_and_writes_external_manifest(
        self,
    ) -> None:
        repo, external = self.make_fixture_repo()
        completed = self.invoke(
            repo, "discover", "--test-root", str(external)
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = self.final_record(completed)
        self.assertEqual(record["event"], "project_test_discovery_complete")
        self.assertEqual(record["total_count"], 2)
        run_dir = Path(record["run_dir"])
        discovery = json.loads(
            (run_dir / "discovery.json").read_text(encoding="utf-8")
        )
        test_run = json.loads(
            (run_dir / "test-run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(discovery["suite_ids"], ["alpha", "beta"])
        self.assertEqual(test_run["command"], "discover")
        self.assertIsNone(test_run["requested_jobs"])
        self.assertEqual(len(record["discovery_sha256"]), 64)

    def test_run_repeats_suite_executes_child_discovery_and_forwards_output(
        self,
    ) -> None:
        repo, external = self.make_fixture_repo()
        completed = self.invoke(
            repo,
            "run",
            "--suite",
            "beta",
            "--suite",
            "alpha",
            "--jobs",
            "2",
            "--test-root",
            str(external),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("fixture-alpha-stdout", completed.stdout)
        self.assertIn("fixture-alpha-stderr", completed.stderr)
        record = self.final_record(completed)
        self.assertEqual(record["event"], "project_test_run_complete")
        self.assertTrue(record["success"])
        run_dir = Path(record["run_dir"])
        test_run = json.loads(
            (run_dir / "test-run.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(
            test_run["runner_pid"], test_run["discovery_process"]["pid"]
        )
        self.assertEqual(test_run["suite_ids"], ["alpha", "beta"])
        self.assertEqual(test_run["requested_jobs"], 2)

    def test_invalid_arguments_and_test_failure_return_one(self) -> None:
        repo, external = self.make_fixture_repo()
        for arguments in (
            ("run", "--jobs", "0", "--test-root", str(external)),
            ("run", "--jobs", "5", "--test-root", str(external)),
            ("run", "--test-root", str(external), "--unknown"),
            ("discover", "--suite", "missing", "--test-root", str(external)),
        ):
            with self.subTest(arguments=arguments):
                completed = self.invoke(repo, *arguments)
                self.assertEqual(completed.returncode, 1)
                record = self.final_record(completed)
                self.assertFalse(record["success"])
                self.assertTrue(record["failure_kind"])

        help_result = self.invoke(repo, "--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("{discover,run}", help_result.stdout)
        self.assertNotIn("_discover", help_result.stdout)


if __name__ == "__main__":
    unittest.main()

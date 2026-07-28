from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from scripts.project_test_source_provenance import (
    FIXED_EXECUTION_SOURCE_PATHS,
)
from tests.project_test_runner._fixture_root import (
    committed_fixture_root,
    new_fixture_dir,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = committed_fixture_root() / "cli_promotion"
class ProjectTestRunnerCliTests(unittest.TestCase):
    def make_fixture_repo(self) -> tuple[Path, Path]:
        fixture_root = new_fixture_dir("cli", suffix_hex_length=8)
        repo = fixture_root / "repo"
        external = fixture_root / "external"
        repo.mkdir()
        external.mkdir()
        shutil.copytree(FIXTURE / "config", repo / "config")
        shutil.copytree(FIXTURE / "tests", repo / "tests")
        (repo / "requirements").mkdir()
        (repo / "requirements/video-workflow-runtime.in").write_text(
            "jsonschema==4.26.0\n",
            encoding="utf-8",
        )
        (repo / "scripts").mkdir()
        (repo / "scripts/__init__.py").write_text("", encoding="utf-8")
        for relative in FIXED_EXECUTION_SOURCE_PATHS:
            target = repo / relative
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT_ROOT / relative, target)
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "remote",
                "add",
                "origin",
                "https://github.com/Nishijujuba/video2pdf.git",
            ],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
            cwd=repo,
            check=True,
            capture_output=True,
        )
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
        self.assertEqual(
            discovery["discovery_process"],
            {
                key: value
                for key, value in test_run["discovery_process"].items()
                if key != "exit_code"
            },
        )
        self.assertEqual(
            record["discovery_process"],
            test_run["discovery_process"],
        )
        process = test_run["discovery_process"]
        self.assertIn(
            process["relationship"],
            {"direct", "launcher_child"},
        )
        self.assertEqual(process["exit_code"], 0)
        self.assertEqual(
            process["launcher_identity"]["pid"],
            (
                process["self_identity"]["pid"]
                if process["relationship"] == "direct"
                else process["self_identity"]["parent_pid"]
            ),
        )
        self.assertEqual(
            process["launcher_identity"]["process_creation_identity"],
            (
                process["self_identity"]["process_creation_identity"]
                if process["relationship"] == "direct"
                else process["self_identity"][
                    "parent_process_creation_identity"
                ]
            ),
        )

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
            test_run["runner_identity"]["pid"],
            test_run["discovery_process"]["self_identity"]["pid"],
        )
        self.assertEqual(
            record["discovery_process"],
            test_run["discovery_process"],
        )
        discovery = json.loads(
            (run_dir / "discovery.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            discovery["discovery_process"],
            {
                key: value
                for key, value in test_run["discovery_process"].items()
                if key != "exit_code"
            },
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

    @unittest.skipUnless(os.name == "nt", "Windows path budget")
    def test_self_hosted_over_budget_root_fails_before_creating_project_or_worker(
        self,
    ) -> None:
        repo, external = self.make_fixture_repo()
        registry_path = repo / "config/test-suites.v1.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["suites"][0]["suite_id"] = "project-test-runner"
        registry["suites"][0]["suite_key"] = "project-test-runner"
        registry_path.write_text(
            json.dumps(registry),
            encoding="utf-8",
        )

        completed = self.invoke(
            repo,
            "run",
            "--suite",
            "project-test-runner",
            "--test-root",
            str(external),
        )

        self.assertEqual(completed.returncode, 1)
        record = self.final_record(completed)
        self.assertEqual(
            record["failure_kind"],
            "external_root_path_budget_failure",
        )
        self.assertIn("240", record["detail"])
        self.assertIn("214", record["detail"])
        self.assertFalse((external / "video2pdf").exists())

    def test_dirty_execution_source_fails_before_external_run_creation(
        self,
    ) -> None:
        repo, external = self.make_fixture_repo()
        runner = repo / "scripts/run_project_tests.py"
        runner.write_bytes(runner.read_bytes() + b"\n# dirty source\n")

        completed = self.invoke(
            repo,
            "discover",
            "--test-root",
            str(external),
        )

        self.assertEqual(completed.returncode, 1)
        record = self.final_record(completed)
        self.assertEqual(
            record["failure_kind"],
            "source_provenance_failure",
        )
        self.assertIn("clean Git worktree", record["detail"])
        self.assertFalse((external / "video2pdf").exists())


if __name__ == "__main__":
    unittest.main()

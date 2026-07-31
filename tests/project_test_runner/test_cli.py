from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from scripts import run_project_tests as project_test_runner
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
    def test_run_contract_failure_is_machine_readable(self) -> None:
        with self.assertRaises(
            project_test_runner.ProjectTestRunContractError
        ) as raised:
            project_test_runner.build_project_test_run_v2({})
        self.assertEqual(
            project_test_runner._post_snapshot_failure_kind(
                raised.exception,
                summary_exists=False,
            ),
            "run_contract_failure",
        )

        def reject_invalid_fields(_arguments) -> int:
            project_test_runner.build_project_test_run_v2({})
            raise AssertionError("unreachable")

        with (
            mock.patch.object(
                project_test_runner,
                "_public_command",
                side_effect=reject_invalid_fields,
            ),
            mock.patch.object(project_test_runner, "_emit") as emit,
        ):
            exit_code = project_test_runner.main(
                ["discover", "--test-root", "D:\\tests"]
            )

        self.assertEqual(exit_code, 1)
        emit.assert_called_once_with(
            {
                "command": "discover",
                "detail": (
                    "test-run v2 fields do not match the current contract"
                ),
                "event": "project_test_command_failed",
                "failure_kind": "run_contract_failure",
                "success": False,
            },
        )

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
        source_snapshot = json.loads(
            (run_dir / "source-snapshot.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            test_run["source_snapshot_id"],
            source_snapshot["source_snapshot_id"],
        )
        self.assertEqual(
            test_run["source_snapshot_sha256"],
            record["source_snapshot_sha256"],
        )
        self.assertEqual(source_snapshot["prevalidation"]["result"], "passed")
        self.assertEqual(source_snapshot["module_inventory"]["count"], 2)
        assignments = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(
                (run_dir / "modules").glob("*.assignment.json")
            )
        ]
        self.assertEqual(len(assignments), 2)
        for assignment in assignments:
            inventory = assignment["module_inventory"]
            self.assertEqual(len(inventory), 2)
            self.assertEqual(
                assignment["module_inventory_sha256"],
                source_snapshot["module_inventory"]["sha256"],
            )
            assigned_member = {
                "module_key": assignment["module_key"],
                "suite_id": assignment["suite_id"],
                "source_path": assignment["source_path"],
                "test_count": len(assignment["test_ids"]),
                "test_ids_sha256": hashlib.sha256(
                    (
                        json.dumps(
                            sorted(assignment["test_ids"]),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                ).hexdigest(),
            }
            self.assertIn(assigned_member, inventory)
        finalization = json.loads(
            (run_dir / "run-finalization.json").read_text(encoding="utf-8")
        )
        self.assertTrue(finalization["success"])
        self.assertEqual(finalization["postvalidation"]["result"], "passed")
        self.assertEqual(
            finalization["source_snapshot_id"],
            source_snapshot["source_snapshot_id"],
        )
        self.assertEqual(
            record["run_finalization_sha256"],
            hashlib.sha256(
                (run_dir / "run-finalization.json").read_bytes()
            ).hexdigest(),
        )

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

    def test_post_snapshot_setup_failure_writes_failed_finalization(self) -> None:
        repo, external = self.make_fixture_repo()
        missing_timings = (external / "missing-timings.json").resolve()

        completed = self.invoke(
            repo,
            "run",
            "--test-root",
            str(external),
            "--timings-from",
            str(missing_timings),
        )

        self.assertEqual(completed.returncode, 1)
        record = self.final_record(completed)
        self.assertEqual(record["event"], "project_test_run_complete")
        self.assertFalse(record["success"])
        self.assertEqual(
            record["failure_kind"],
            "scheduler_setup_failure",
        )
        finalization_path = Path(record["run_finalization_path"])
        finalization = json.loads(
            finalization_path.read_text(encoding="utf-8")
        )
        self.assertFalse(finalization["success"])
        self.assertEqual(
            finalization["scheduler_failure_kind"],
            "scheduler_setup_failure",
        )
        self.assertIsNone(finalization["summary_sha256"])
        self.assertEqual(
            record["run_finalization_sha256"],
            hashlib.sha256(finalization_path.read_bytes()).hexdigest(),
        )

    def test_persistent_summary_hash_failure_writes_failed_finalization(
        self,
    ) -> None:
        repo, external = self.make_fixture_repo()
        real_sha256_file = project_test_runner.sha256_file

        def fail_summary_hash(path: Path) -> str:
            if Path(path).name == "summary.json":
                raise project_test_runner.ResultIntegrityError(
                    "summary remains unreadable"
                )
            return real_sha256_file(path)

        with (
            mock.patch.object(project_test_runner, "REPO_ROOT", repo),
            mock.patch.object(
                project_test_runner,
                "sha256_file",
                side_effect=fail_summary_hash,
            ),
        ):
            return_code = project_test_runner.main(
                [
                    "run",
                    "--jobs",
                    "2",
                    "--test-root",
                    str(external),
                ]
            )

        self.assertEqual(return_code, 1)
        run_directories = list(
            (external / "video2pdf" / "all").iterdir()
        )
        self.assertEqual(len(run_directories), 1)
        finalization_path = (
            run_directories[0] / "run-finalization.json"
        )
        finalization = json.loads(
            finalization_path.read_text(encoding="utf-8")
        )
        self.assertFalse(finalization["success"])
        self.assertEqual(
            finalization["scheduler_failure_kind"],
            "result_integrity_failure",
        )
        self.assertIsNone(finalization["summary_sha256"])

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
            "source_preflight_failure",
        )
        self.assertIn("clean Git worktree", record["detail"])
        self.assertFalse((external / "video2pdf").exists())


if __name__ == "__main__":
    unittest.main()

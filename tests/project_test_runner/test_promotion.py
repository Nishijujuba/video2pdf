from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import unittest

from jsonschema import Draft202012Validator

from scripts.project_test_results import canonical_json_bytes
from scripts.validate_project_test_promotion import (
    PromotionValidationError,
    validate_promotion_report,
)
from tests.project_test_runner._fixture_root import (
    committed_fixture_root,
    new_fixture_dir,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = committed_fixture_root() / "cli_promotion"


def write_json(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json_bytes(value)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def write_bytes(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


class PromotionReportTests(unittest.TestCase):
    def make_report(self) -> tuple[Path, dict]:
        repo = new_fixture_dir("promotion")
        (repo / "scripts").mkdir()
        (repo / "scripts/run_project_tests.py").write_text(
            "# fixture entrypoint\n", encoding="utf-8"
        )
        (repo / "evidence/project-test-runner").mkdir(parents=True)
        (repo / "schemas").mkdir()
        shutil.copy2(
            PROJECT_ROOT
            / "schemas/project-test-promotion-report.v1.schema.json",
            repo / "schemas/project-test-promotion-report.v1.schema.json",
        )
        test_ids = json.loads(
            (
                PROJECT_ROOT
                / "evidence/project-test-runner/"
                "promotion-superset-authority.v2.json"
            ).read_text(encoding="utf-8")
        )["baseline"]["test_ids"]
        test_set_sha = hashlib.sha256(
            canonical_json_bytes(test_ids)
        ).hexdigest()
        migration = {
            "schema_name": "video2pdf.test-path-migration-review",
            "schema_version": 1,
            "migration_review": {
                "semantic_change_checks": {
                    "test_method_names_changed": 0,
                    "assertion_lines_changed": 0,
                    "committed_fixtures_changed_by_migration": 0,
                    "production_inputs_changed": 0,
                    "expected_behavior_changed": 0,
                }
            },
            "discovery_review": {
                "video_workflow_test_count": len(test_ids),
                "video_workflow_module_count": 38,
                "duplicate_test_ids": 0,
                "test_id_set_sha256": test_set_sha,
            },
        }
        migration_path = repo / "evidence/project-test-runner/migration.json"
        migration_sha = write_json(migration_path, migration)
        baseline_dir = repo / "historical-baseline"
        baseline_status = {
            "schema_name": "persisted-command-status",
            "schema_version": "1.0.0",
            "state": "succeeded",
            "exit_code": 0,
            "elapsed_seconds": 4849.187,
            "security": {
                "classification": "no_secret_detected",
                "acceptance_evidence_eligible": True,
            },
        }
        baseline_status_path = baseline_dir / "status.json"
        baseline_status_sha = write_json(
            baseline_status_path, baseline_status
        )
        baseline_exit_path = baseline_dir / "exit-code.txt"
        baseline_exit_sha = write_bytes(baseline_exit_path, b"0\n")
        runs = []
        for index in (1, 2):
            run_dir = repo / f"parallel-{index}"
            discovery = {
                "schema_name": "video2pdf.project-test-discovery",
                "schema_version": 1,
                "project": {
                    "project_key": "video2pdf",
                    "repository": "Nishijujuba/video2pdf",
                },
                "commit": "b" * 40,
                "suite_ids": ["video-workflow"],
                "total_count": len(test_ids),
                "test_id_set_sha256": test_set_sha,
                "modules": [
                    {
                        "module_key": "module",
                        "test_ids": test_ids,
                    }
                ],
            }
            summary = {
                "schema_name": "video2pdf.project-test-summary",
                "schema_version": 1,
                "project": discovery["project"],
                "commit": discovery["commit"],
                "suite_ids": discovery["suite_ids"],
                "requested_jobs": 4,
                "success": True,
                "failure_kind": None,
                "coverage": {
                    "discovered": len(test_ids),
                    "assigned": len(test_ids),
                    "started": len(test_ids),
                    "terminal": len(test_ids),
                    "executed_test_ids": len(test_ids),
                    "missing_test_ids": [],
                    "duplicate_test_ids": [],
                    "unassigned_test_ids": [],
                    "multiply_executed_test_ids": [],
                },
            }
            status = {
                "schema_name": "persisted-command-status",
                "schema_version": "1.0.0",
                "state": "succeeded",
                "exit_code": 0,
                "elapsed_seconds": 900 + index,
                "security": {
                    "classification": "no_secret_detected",
                    "acceptance_evidence_eligible": True,
                },
            }
            discovery_sha = write_json(run_dir / "discovery.json", discovery)
            summary_sha = write_json(run_dir / "summary.json", summary)
            persisted_dir = repo / f"persisted-{index}"
            persisted_status_path = persisted_dir / "status.json"
            persisted_status_sha = write_json(persisted_status_path, status)
            persisted_exit_path = persisted_dir / "exit-code.txt"
            persisted_exit_sha = write_bytes(persisted_exit_path, b"0\n")
            command_path = persisted_dir / "command.json"
            command_sha = write_json(
                command_path,
                {
                    "schema_name": "persisted-command",
                    "schema_version": "1.0.0",
                    "accepted_exit_codes": [0],
                    "cwd": str(repo),
                    "argv": [
                        "python.exe",
                        "-X",
                        "utf8",
                        "-B",
                        str(repo / "scripts/run_project_tests.py"),
                        "run",
                        "--suite",
                        "video-workflow",
                        "--jobs",
                        "4",
                        "--test-root",
                        str(run_dir.parents[2]),
                    ],
                },
            )
            stdout_path = persisted_dir / "stdout.log"
            stdout_sha = write_bytes(
                stdout_path,
                canonical_json_bytes(
                    {
                        "event": "project_test_run_complete",
                        "success": True,
                        "failure_kind": None,
                        "run_dir": str(run_dir),
                        "discovery_sha256": discovery_sha,
                        "summary_sha256": summary_sha,
                    }
                ),
            )
            runs.append(
                {
                    "run_dir": str(run_dir),
                    "discovery_path": str(run_dir / "discovery.json"),
                    "discovery_sha256": discovery_sha,
                    "summary_path": str(run_dir / "summary.json"),
                    "summary_sha256": summary_sha,
                    "persisted_run_dir": str(persisted_dir),
                    "persisted_status_path": str(persisted_status_path),
                    "persisted_status_sha256": persisted_status_sha,
                    "persisted_exit_code_path": str(persisted_exit_path),
                    "persisted_exit_code_sha256": persisted_exit_sha,
                    "persisted_command_path": str(command_path),
                    "persisted_command_sha256": command_sha,
                    "persisted_stdout_path": str(stdout_path),
                    "persisted_stdout_sha256": stdout_sha,
                }
            )
        report = {
            "schema_name": "video2pdf.project-test-promotion-report",
            "schema_version": 1,
            "issue": 27,
            "historical_performance_baseline": {
                "implementation_commit": "18f78fad0be5a66d2da6250dc268bc8de81fdbcc",
                "test_count": 474,
                "result": "OK",
                "test_duration_seconds": 4847.218,
                "persisted_elapsed_seconds": 4849.187,
                "persisted_run_dir": str(baseline_dir),
                "persisted_status_path": str(baseline_status_path),
                "persisted_status_sha256": baseline_status_sha,
                "persisted_exit_code_path": str(baseline_exit_path),
                "persisted_exit_code_sha256": baseline_exit_sha,
            },
            "final_issue9_closed_set": {
                "commit": "a" * 40,
                "test_count": len(test_ids),
                "test_id_set_sha256": test_set_sha,
                "evidence_path": str(migration_path.relative_to(repo)),
                "evidence_sha256": migration_sha,
            },
            "implementation": {"commit": "b" * 40},
            "promotion_closed_set": {
                "suite_ids": ["video-workflow"],
                "test_count": len(test_ids),
                "test_id_set_sha256": test_set_sha,
            },
            "parallel_runs": runs,
            "semantic_parity": {
                "passed": True,
                "test_id_set_sha256": test_set_sha,
                "ignored_fields": [
                    "timestamps",
                    "pids",
                    "durations",
                    "completion_order",
                ],
            },
            "migration_review": {
                "path": str(migration_path.relative_to(repo)),
                "sha256": migration_sha,
                "passed": True,
            },
            "performance": {
                "maximum_elapsed_seconds": 1800,
                "passed": True,
            },
            "cutover_authorized": True,
        }
        report["promotion_fingerprint"] = hashlib.sha256(
            canonical_json_bytes(
                {
                    "implementation_commit": report["implementation"]["commit"],
                    "historical_baseline_commit": report[
                        "historical_performance_baseline"
                    ]["implementation_commit"],
                    "historical_status_sha256": baseline_status_sha,
                    "historical_exit_code_sha256": baseline_exit_sha,
                    "final_issue9_commit": report["final_issue9_closed_set"][
                        "commit"
                    ],
                    "final_issue9_test_count": len(test_ids),
                    "final_issue9_test_id_set_sha256": test_set_sha,
                    "final_issue9_evidence_sha256": migration_sha,
                    "suite_ids": report["promotion_closed_set"]["suite_ids"],
                    "test_count": report["promotion_closed_set"]["test_count"],
                    "test_id_set_sha256": test_set_sha,
                    "parallel_discovery_sha256": [
                        item["discovery_sha256"] for item in runs
                    ],
                    "parallel_summary_sha256": [
                        item["summary_sha256"] for item in runs
                    ],
                    "parallel_persisted_status_sha256": [
                        item["persisted_status_sha256"] for item in runs
                    ],
                    "parallel_persisted_exit_code_sha256": [
                        item["persisted_exit_code_sha256"] for item in runs
                    ],
                    "parallel_persisted_command_sha256": [
                        item["persisted_command_sha256"] for item in runs
                    ],
                    "parallel_persisted_stdout_sha256": [
                        item["persisted_stdout_sha256"] for item in runs
                    ],
                    "migration_review_sha256": migration_sha,
                }
            )
        ).hexdigest()
        write_json(
            repo / "evidence/project-test-runner/promotion-report.json",
            report,
        )
        return repo, report

    def test_valid_report_binds_dynamic_closed_set_and_two_parallel_runs(
        self,
    ) -> None:
        repo, report = self.make_report()
        schema = json.loads(
            (
                repo / "schemas/project-test-promotion-report.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(report)
        result = validate_promotion_report(repo)
        self.assertTrue(result["valid"])
        self.assertTrue(result["cutover_authorized"])
        self.assertEqual(
            result["promotion_fingerprint"], report["promotion_fingerprint"]
        )

    def test_schema_version_dispatch_rejects_boolean_and_unknown_versions(
        self,
    ) -> None:
        for invalid_version in (True, 0, -1, 3, "1"):
            with self.subTest(schema_version=invalid_version):
                repo, report = self.make_report()
                report["schema_version"] = invalid_version
                write_json(
                    repo
                    / "evidence/project-test-runner/promotion-report.json",
                    report,
                )
                with self.assertRaisesRegex(
                    PromotionValidationError, "schema_version"
                ):
                    validate_promotion_report(repo)

    def test_count_is_dynamic_and_failures_block_cutover(self) -> None:
        repo, report = self.make_report()
        report["promotion_closed_set"]["test_count"] = 499
        write_json(
            repo / "evidence/project-test-runner/invalid-report.json",
            report,
        )
        fixed = repo / "evidence/project-test-runner/promotion-report.json"
        fixed.write_bytes(
            (repo / "evidence/project-test-runner/invalid-report.json").read_bytes()
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "promotion closed set"
        ):
            validate_promotion_report(repo)

    def test_schema_is_strict_and_report_path_is_fixed(self) -> None:
        repo, report = self.make_report()
        report["unexpected"] = True
        write_json(
            repo / "evidence/project-test-runner/replacement.json",
            report,
        )
        fixed = repo / "evidence/project-test-runner/promotion-report.json"
        fixed.write_bytes(
            (repo / "evidence/project-test-runner/replacement.json").read_bytes()
        )
        with self.assertRaisesRegex(PromotionValidationError, "unknown field"):
            validate_promotion_report(repo)

    def test_unrelated_persisted_success_cannot_substitute_for_runner(
        self,
    ) -> None:
        repo, report = self.make_report()
        run = report["parallel_runs"][0]
        command_path = Path(run["persisted_command_path"])
        unrelated = {
            "schema_name": "persisted-command",
            "schema_version": "1.0.0",
            "accepted_exit_codes": [0],
            "cwd": str(repo),
            "argv": [
                "python.exe",
                "-c",
                "print('unrelated success')",
                str(repo / "scripts/run_project_tests.py"),
                "run",
                "--suite",
                "video-workflow",
                "--jobs",
                "4",
                "--test-root",
                str(Path(run["run_dir"]).parents[2]),
            ],
        }
        run["persisted_command_sha256"] = write_json(
            command_path, unrelated
        )
        write_json(
            repo / "evidence/project-test-runner/replacement.json",
            report,
        )
        fixed = repo / "evidence/project-test-runner/promotion-report.json"
        fixed.write_bytes(
            (repo / "evidence/project-test-runner/replacement.json").read_bytes()
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "does not invoke"
        ):
            validate_promotion_report(repo)

    def test_final_issue9_evidence_must_prove_test_id_fingerprint(self) -> None:
        repo, report = self.make_report()
        forged = "f" * 64
        report["final_issue9_closed_set"]["test_id_set_sha256"] = forged
        report["promotion_closed_set"]["test_id_set_sha256"] = forged
        report["semantic_parity"]["test_id_set_sha256"] = forged
        write_json(
            repo / "evidence/project-test-runner/replacement.json",
            report,
        )
        fixed = repo / "evidence/project-test-runner/promotion-report.json"
        fixed.write_bytes(
            (repo / "evidence/project-test-runner/replacement.json").read_bytes()
        )
        with self.assertRaisesRegex(
            PromotionValidationError, "evidence does not match"
        ):
            validate_promotion_report(repo)


if __name__ == "__main__":
    unittest.main()

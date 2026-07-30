from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from tests.video_workflow._test_run import new_case_dir


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"


def run_cli(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout is not one JSON result envelope: {completed.stdout!r}; "
            f"stderr={completed.stderr!r}"
        ) from exc
    return completed, envelope


def mutated_registry(
    test_id: str,
    schema_name: str,
    mutate,
) -> Path:
    run_root = new_case_dir(test_id, label=f"mutated-{schema_name}")
    registry = json.loads(
        (
            PROJECT_ROOT / "schemas/delivery-quality/registry.v1.json"
        ).read_text(encoding="utf-8")
    )
    entry = next(
        item for item in registry["contracts"] if item["schema_name"] == schema_name
    )
    instance = json.loads(
        (PROJECT_ROOT / entry["canonical_instance"]).read_text(encoding="utf-8")
    )
    mutate(instance)
    instance_path = run_root / f"{schema_name}.json"
    instance_bytes = (
        json.dumps(instance, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    instance_path.write_bytes(instance_bytes)
    relative = instance_path.relative_to(PROJECT_ROOT).as_posix()
    entry["canonical_instance"] = relative
    entry["positive_example"] = relative
    entry["canonical_sha256"] = hashlib.sha256(instance_bytes).hexdigest()
    registry_path = run_root / "registry.json"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return registry_path


class DeliveryQualityContractsCliTests(unittest.TestCase):
    def test_public_contract_check_proves_closed_target_only_policy_surface(
        self,
    ) -> None:
        completed, envelope = run_cli("delivery-quality-contracts-check")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(envelope["command"], "delivery-quality-contracts-check")
        self.assertEqual(envelope["classification"], "delivery_quality_contracts_valid")
        self.assertEqual(envelope["data"]["authority"], "target_only")
        self.assertEqual(envelope["data"]["contract_count"], 7)
        self.assertEqual(envelope["data"]["positive_examples_validated"], 7)
        self.assertEqual(envelope["data"]["negative_examples_rejected"], 7)
        self.assertTrue(envelope["data"]["registry_complete"])
        self.assertTrue(envelope["data"]["primary_semantic_ownership_complete"])
        self.assertTrue(envelope["data"]["generated_prompts_current"])

    def test_contract_check_rejects_each_required_fail_closed_class(self) -> None:
        def duplicate_rule(instance: dict) -> None:
            instance["rules"].append(instance["rules"][0])

        def dangling_waiver(instance: dict) -> None:
            instance["waivers"].append(
                {
                    "waiver_id": "dangling-rule",
                    "rule_id": "unknown_rule",
                    "violation_ids": ["unknown_violation"],
                    "scope_binding": "fixture",
                    "approved_by": "fixture",
                    "approved_at": "2026-07-30T00:00:00Z",
                    "expires_at": "2026-07-31T00:00:00Z",
                    "rationale": "Negative fixture.",
                }
            )

        def rewrite_projection(instance: dict) -> None:
            instance["projections"][0]["rules"][0]["requirement"] = "Rewritten."

        def omit_owner(instance: dict) -> None:
            evaluation = next(
                projection
                for projection in instance["projections"]
                if projection["projection_kind"] == "evaluation"
            )
            evaluation["rules"] = evaluation["rules"][1:]

        def assign_wrong_migration_owner(instance: dict) -> None:
            instance["entries"][0][
                "primary_semantic_decision_owner"
            ] = "visual-quality-reviewer"

        cases = (
            (
                "unknown-field",
                "delivery-quality-rule-catalog",
                lambda instance: instance.__setitem__("unexpected", True),
            ),
            (
                "duplicate-rule",
                "delivery-quality-rule-catalog",
                duplicate_rule,
            ),
            (
                "dangling-waiver",
                "delivery-quality-waiver-ledger",
                dangling_waiver,
            ),
            (
                "policy-rewrite",
                "delivery-quality-role-projections",
                rewrite_projection,
            ),
            (
                "incomplete-owner",
                "delivery-quality-role-projections",
                omit_owner,
            ),
            (
                "wrong-migration-owner",
                "delivery-quality-migration-ledger",
                assign_wrong_migration_owner,
            ),
            (
                "invalid-semantic-fingerprint",
                "delivery-quality-rule-catalog",
                lambda instance: instance["rules"][0].__setitem__(
                    "semantic_sha256", "0" * 64
                ),
            ),
        )
        for label, schema_name, mutation in cases:
            with self.subTest(label=label):
                registry_path = mutated_registry(
                    f"{self.id()}.{label}",
                    schema_name,
                    mutation,
                )
                completed, envelope = run_cli(
                    "delivery-quality-contracts-check",
                    "--registry",
                    str(registry_path),
                )
                self.assertEqual(completed.returncode, 20)
                self.assertEqual(envelope["classification"], "contract_invalid")

        version_root = new_case_dir(
            f"{self.id()}.unsupported-version",
            label="unsupported-version",
        )
        registry = json.loads(
            (
                PROJECT_ROOT / "schemas/delivery-quality/registry.v1.json"
            ).read_text(encoding="utf-8")
        )
        registry["schema_version"] = "2.0.0"
        registry_path = version_root / "registry.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        completed, envelope = run_cli(
            "delivery-quality-contracts-check",
            "--registry",
            str(registry_path),
        )
        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["classification"], "unknown_contract_version")

    def test_public_conformance_runs_three_isolated_attempts_per_profile_case(
        self,
    ) -> None:
        run_root = new_case_dir(self.id(), label="delivery-quality-conformance")
        report_path = run_root / "conformance-report.json"
        adapter = (
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/delivery-quality/"
            "deterministic_reviewer_adapter.py"
        )

        completed, envelope = run_cli(
            "delivery-quality-conformance",
            "--reviewer-adapter",
            str(adapter),
            "--output",
            str(report_path),
            "--implementation-commit",
            "1" * 40,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            envelope["classification"], "delivery_quality_conformance_passed"
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["authority"], "implementation_qualification_only")
        self.assertEqual(report["implementation"]["activation_status"], "target_only")
        self.assertEqual(report["overall_decision"], "pass")
        self.assertEqual(len(report["semantic_results"]), 36)
        self.assertEqual(
            sum(len(item["attempts"]) for item in report["semantic_results"]),
            108,
        )
        attempts = [
            attempt
            for item in report["semantic_results"]
            for attempt in item["attempts"]
        ]
        self.assertEqual(len({item["context_id"] for item in attempts}), 108)
        self.assertEqual(len({item["task_id"] for item in attempts}), 108)
        self.assertTrue(all(item["process_id"] > 0 for item in attempts))
        self.assertTrue(
            all(not item["semantic_variance"] for item in report["semantic_results"])
        )
        self.assertEqual(len(report["mechanical_results"]), 6)
        self.assertTrue(
            all(item["conforms"] for item in report["mechanical_results"])
        )

    def test_conformance_reports_semantic_variance_without_hiding_other_results(
        self,
    ) -> None:
        run_root = new_case_dir(
            self.id(), label="delivery-quality-semantic-variance"
        )
        report_path = run_root / "conformance-report.json"
        adapter = (
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/delivery-quality/"
            "variance_reviewer_adapter.py"
        )

        completed, envelope = run_cli(
            "delivery-quality-conformance",
            "--reviewer-adapter",
            str(adapter),
            "--output",
            str(report_path),
            "--implementation-commit",
            "2" * 40,
        )

        self.assertEqual(completed.returncode, 30)
        self.assertEqual(
            envelope["classification"], "delivery_quality_conformance_failed"
        )
        self.assertTrue(envelope["data"]["semantic_variance"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["overall_decision"], "fail")
        self.assertEqual(len(report["semantic_results"]), 36)
        varied = [
            item for item in report["semantic_results"] if item["semantic_variance"]
        ]
        self.assertEqual(
            [item["case_id"] for item in varied],
            ["en.predicate-object-compliant"],
        )
        self.assertIn(
            "semantic_variance:en.predicate-object-compliant",
            report["failures"],
        )

    def test_slice7_exit_evidence_schema_proves_target_only_positive_and_negative_results(
        self,
    ) -> None:
        schema = json.loads(
            (
                PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        manifest = {
            "$schema": schema["$id"],
            "schema_version": 2,
            "kind": "video-workflow-exit-evidence",
            "fingerprint_algorithm": "sha256-raw-v1",
            "slice": {
                "number": 7,
                "name": "delivery-quality-contracts-and-conformance",
            },
            "slice_base_commit": "68189e7744e22c9ce78b3ee1a58def69d09e711a",
            "implementation_commit": "1" * 40,
            "evidence_paths": [
                "evidence/slice-07/exit-evidence-manifest.json",
                "evidence/slice-07/logs/contracts.log",
            ],
            "generated_at": "2026-07-30T00:00:00Z",
            "activation_scope": {
                "kind": "none",
                "runtime_authority_change": False,
                "components_activated": [],
                "legacy_track_authority": "preserved",
            },
            "commands": [
                {
                    "test_id": f"slice7-command-{number}",
                    "command": ["python", "-m", "unittest"],
                    "expected_exit_code": 0,
                    "actual_exit_code": 0,
                    "log": {
                        "role": "command_log",
                        "path": f"evidence/slice-07/logs/{number}.log",
                        "sha256": "2" * 64,
                    },
                    "conforms": True,
                }
                for number in range(1, 4)
            ],
            "expected_checkpoints": [
                {"name": "delivery_quality_contracts_current", "status": "current"}
            ],
            "fixtures": [
                {
                    "role": "canonical_rule_catalog",
                    "path": "delivery-quality/v1/rule-catalog.v1.json",
                    "sha256": "3" * 64,
                }
            ],
            "results": {
                "positive": ["contracts_validate"],
                "negative": ["rewrites_fail_closed"],
                "recovery": ["legacy_authority_preserved"],
            },
            "result_bindings": [
                {
                    "result_id": result_id,
                    "result_kind": result_kind,
                    "command_id": "slice7-command-1",
                    "test_target": (
                        "tests.video_workflow.test_delivery_quality_contracts."
                        "DeliveryQualityContractsCliTests."
                        "test_public_contract_check_proves_closed_target_only_policy_surface"
                    ),
                }
                for result_kind, result_id in (
                    ("positive", "contracts_validate"),
                    ("negative", "rewrites_fail_closed"),
                    ("recovery", "legacy_authority_preserved"),
                )
            ],
            "artifact_fingerprints": [
                {
                    "role": "implementation",
                    "path": "src/video2pdf_workflow_kernel/delivery_quality.py",
                    "sha256": "4" * 64,
                }
            ],
            "unresolved_exceptions": [],
            "overall_decision": "pass",
        }
        validator = Draft202012Validator(schema)
        validator.validate(manifest)
        manifest["activation_scope"]["runtime_authority_change"] = True
        with self.assertRaises(ValidationError):
            validator.validate(manifest)


if __name__ == "__main__":
    unittest.main()

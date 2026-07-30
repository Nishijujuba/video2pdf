from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

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


def valid_semantic_results(corpus: dict) -> dict:
    results = {
        "schema_name": "delivery-quality-semantic-results",
        "schema_version": "1.0.0",
        "provider": {
            "name": "recorded-reviewer-fixture",
            "model_revision": "reviewer-fixture-v1",
            "sampling": "deterministic-fixture",
        },
        "case_results": [],
    }
    for profile_id in corpus["applicable_language_profiles"]:
        for template in corpus["case_templates"]:
            attempts = []
            for attempt_number in range(1, 4):
                attempts.append(
                    {
                        "context_id": (
                            f"{profile_id}-{template['template_id']}-"
                            f"{attempt_number}"
                        ),
                        **template["expected"],
                        "evidence_locator": template["evidence_locator"],
                        "rationale": (
                            "Recorded isolated Reviewer result matches the "
                            "structured oracle."
                        ),
                    }
                )
            results["case_results"].append(
                {
                    "case_id": f"{profile_id}.{template['template_id']}",
                    "profile_id": profile_id,
                    "template_id": template["template_id"],
                    "attempts": attempts,
                }
            )
    return results


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
        corpus = json.loads(
            (
                PROJECT_ROOT
                / "delivery-quality/v1/conformance-corpus.v1.json"
            ).read_text(encoding="utf-8")
        )
        results = valid_semantic_results(corpus)
        results_path = run_root / "semantic-results.json"
        results_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report_path = run_root / "conformance-report.json"

        completed, envelope = run_cli(
            "delivery-quality-conformance",
            "--semantic-results",
            str(results_path),
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
        corpus = json.loads(
            (
                PROJECT_ROOT
                / "delivery-quality/v1/conformance-corpus.v1.json"
            ).read_text(encoding="utf-8")
        )
        results = valid_semantic_results(corpus)
        target = next(
            result
            for result in results["case_results"]
            if result["case_id"] == "en.predicate-object-compliant"
        )
        target["attempts"][2]["decision"] = "fail"
        results_path = run_root / "semantic-results.json"
        results_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report_path = run_root / "conformance-report.json"

        completed, envelope = run_cli(
            "delivery-quality-conformance",
            "--semantic-results",
            str(results_path),
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


if __name__ == "__main__":
    unittest.main()

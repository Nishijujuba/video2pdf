from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel.delivery_quality import DeliveryQualityRegistry
from video2pdf_workflow_kernel.errors import ContractError


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
    instance_locator = instance_path.name
    entry["canonical_instance"] = instance_locator
    entry["positive_example"] = instance_locator
    entry["canonical_sha256"] = hashlib.sha256(instance_bytes).hexdigest()
    registry_path = run_root / "registry.json"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return registry_path


class DeliveryQualityContractsCliTests(unittest.TestCase):
    def test_rendered_inventory_accepts_bound_declared_raster_source_path(self) -> None:
        registry = DeliveryQualityRegistry(PROJECT_ROOT)
        entry = next(
            item
            for item in registry.entries
            if item.schema_name == "rendered-text-object-inventory"
        )
        inventory = json.loads(entry.positive_example.read_text(encoding="utf-8"))
        inventory["objects"][0].update({
            "object_kind": "declared_raster_text",
            "source_artifact_logical_id": "figure_asset",
            "source_generation": 3,
            "source_sha256": "6" * 64,
            "source_path": "figures/figure_asset.png",
        })
        registry.validate("rendered-text-object-inventory", inventory)

    def test_acceptance_v2_negative_fixtures_are_single_contradiction_valid_graphs(
        self,
    ) -> None:
        scenarios = (
            ("acceptance-v2-input-binding", "activation_status", "active"),
            ("acceptance-v2-review-skeleton", "aggregation_policy", "unsupported"),
            ("acceptance-v2-judgment-patch", "dimension", "text"),
            ("acceptance-v2-judgment-patch-authoring-contract", "schema_version", "2.0.0"),
            ("acceptance-v2-execution-context", "state", "delivered"),
            ("acceptance-v2-task-envelope", "input_access", "read_write"),
            ("acceptance-report-v2", "overall_status", "unknown"),
            ("acceptance-v2-attempt-record", "overall_status", "unknown"),
            ("acceptance-v2-repair-ledger", "attempt_limit", 4),
        )
        registry = DeliveryQualityRegistry(PROJECT_ROOT)
        entries = {entry.schema_name: entry for entry in registry.entries}
        for schema_name, target_field, contradictory_value in scenarios:
            with self.subTest(
                scenario_id=f"{schema_name}-{target_field}",
                expected_first_gate="delivery_quality_schema_validation",
                expected_error_code="contract_invalid",
            ):
                entry = entries[schema_name]
                positive = json.loads(entry.positive_example.read_text(encoding="utf-8"))
                negative = json.loads(entry.negative_example.read_text(encoding="utf-8"))
                expected = dict(positive)
                expected[target_field] = contradictory_value
                self.assertEqual(expected, negative)
                with self.assertRaises(ContractError) as raised:
                    registry.validate(schema_name, negative)
                self.assertEqual("delivery_quality_schema_validation", raised.exception.data["first_failing_gate"])
                self.assertEqual("contract_invalid", raised.exception.data["error_code"])
                self.assertEqual(schema_name, raised.exception.data["schema_name"])

    def test_registry_path_resolution_rejects_relative_escape_before_io(
        self,
    ) -> None:
        registry = DeliveryQualityRegistry(PROJECT_ROOT)
        for locator in ("../", "../missing-delivery-quality-instance.json"):
            with self.subTest(locator=locator):
                with self.assertRaises(ContractError):
                    registry._resolve_project_path(
                        locator,
                        "adversarial fixture",
                        allow_registry_root=True,
                    )

        catalog = (
            PROJECT_ROOT / "delivery-quality/v1/rule-catalog.v1.json"
        ).resolve()
        self.assertEqual(
            registry._resolve_project_path(
                catalog.as_posix(),
                "absolute project fixture",
                allow_registry_root=True,
            ),
            catalog,
        )

    def test_migration_source_fingerprint_uses_canonical_lf_bytes(self) -> None:
        source_relative_path = "docs/acceptance/acceptance_criteria.v1.json"
        attributes = subprocess.run(
            ["git", "check-attr", "eol", "--", source_relative_path],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, attributes.returncode, attributes.stderr)
        self.assertEqual(
            f"{source_relative_path}: eol: lf",
            attributes.stdout.strip(),
        )

        ledger = json.loads(
            (
                PROJECT_ROOT / "delivery-quality/v1/migration-ledger.v1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(source_relative_path, ledger["source_contract"]["path"])
        source_bytes = (PROJECT_ROOT / source_relative_path).read_bytes()
        self.assertNotIn(b"\r\n", source_bytes)
        self.assertEqual(
            hashlib.sha256(source_bytes).hexdigest(),
            ledger["source_contract"]["sha256"],
        )

    def test_public_contract_check_proves_closed_target_only_policy_surface(
        self,
    ) -> None:
        completed, envelope = run_cli("delivery-quality-contracts-check")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(envelope["command"], "delivery-quality-contracts-check")
        self.assertEqual(envelope["classification"], "delivery_quality_contracts_valid")
        self.assertEqual(envelope["data"]["authority"], "target_only")
        self.assertEqual(envelope["data"]["contract_count"], 31)
        self.assertEqual(envelope["data"]["positive_examples_validated"], 31)
        self.assertEqual(envelope["data"]["negative_examples_rejected"], 31)
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
            "-",
            "--implementation-commit",
            "1" * 40,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            envelope["classification"], "delivery_quality_conformance_passed"
        )
        report = envelope["data"]["report"]
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
            "-",
            "--implementation-commit",
            "2" * 40,
        )

        self.assertEqual(completed.returncode, 30)
        self.assertEqual(
            envelope["classification"], "delivery_quality_conformance_failed"
        )
        self.assertTrue(envelope["data"]["semantic_variance"])
        report = envelope["data"]["report"]
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

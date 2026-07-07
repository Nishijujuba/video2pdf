#!/usr/bin/env python3
"""Tests for Final Delivery Acceptance criteria validation."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from validate_acceptance_criteria import ValidationError, validate_acceptance_criteria


def default_criteria() -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[4]
    return json.loads((repo_root / "docs" / "acceptance" / "acceptance_criteria.v1.json").read_text(encoding="utf-8"))


class AcceptanceCriteriaValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        self.run_dir = repo_root / "待删除" / "final-delivery-acceptance-test-runs"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, payload: dict[str, object], name: str) -> Path:
        path = self.run_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def assert_invalid(self, payload: dict[str, object], message: str, name: str) -> None:
        with self.assertRaisesRegex(ValidationError, message):
            validate_acceptance_criteria(self.write_json(payload, name))

    def test_default_criteria_file_satisfies_minimum_contract(self) -> None:
        criteria_path = Path(__file__).resolve().parents[4] / "docs" / "acceptance" / "acceptance_criteria.v1.json"

        warnings = validate_acceptance_criteria(criteria_path)

        self.assertEqual(warnings, [])

    def test_rejects_missing_version_unknown_category_and_non_blocking_fields(self) -> None:
        missing_version = default_criteria()
        missing_version.pop("criteria_version", None)
        self.assert_invalid(missing_version, "criteria missing keys: criteria_version", "missing-version.json")

        unknown_category = default_criteria()
        criterion = deepcopy(unknown_category["criteria"][0])
        assert isinstance(criterion, dict)
        criterion["category"] = "source_faithfulness"
        unknown_category["criteria"] = [criterion]
        self.assert_invalid(unknown_category, "criteria\\[0\\].category is invalid", "unknown-category.json")

        advisory = default_criteria()
        criterion = deepcopy(advisory["criteria"][0])
        assert isinstance(criterion, dict)
        criterion["severity"] = "advisory"
        advisory["criteria"] = [criterion]
        self.assert_invalid(advisory, "criteria\\[0\\] has forbidden keys: severity", "advisory.json")

        score_only = default_criteria()
        criterion = deepcopy(score_only["criteria"][0])
        assert isinstance(criterion, dict)
        criterion["score"] = 0.9
        score_only["criteria"] = [criterion]
        self.assert_invalid(score_only, "criteria\\[0\\] has forbidden keys: score", "score-only.json")

    def test_requires_argument_chain_integrity_logic_readability_criterion(self) -> None:
        criteria = default_criteria()
        criteria["criteria"] = [
            {**item, "id": "renamed_argument_chain"} if isinstance(item, dict) and item.get("id") == "argument_chain_integrity" else item
            for item in criteria["criteria"]
        ]

        self.assert_invalid(
            criteria,
            "criteria must include argument_chain_integrity in category logic_readability",
            "missing-argument-chain-integrity.json",
        )

    def test_requires_formula_information_gain_criterion(self) -> None:
        criteria = default_criteria()
        criteria["criteria"] = [
            {**item, "id": "renamed_formula_gate"} if isinstance(item, dict) and item.get("id") == "formula_information_gain" else item
            for item in criteria["criteria"]
        ]

        self.assert_invalid(
            criteria,
            "criteria must include formula_information_gain in category formula_information_gain",
            "missing-formula-information-gain.json",
        )

    def test_requires_delivery_glossary_term_strategy_style_criterion(self) -> None:
        criteria = default_criteria()
        criteria["criteria"] = [
            item
            for item in criteria["criteria"]
            if not isinstance(item, dict) or item.get("id") != "delivery_glossary_term_strategy"
        ]

        self.assert_invalid(
            criteria,
            "criteria must include delivery_glossary_term_strategy in category style",
            "missing-delivery-glossary-term-strategy.json",
        )

    def test_delivery_glossary_term_strategy_text_names_v1_contract_fields(self) -> None:
        criteria = default_criteria()
        glossary_gate = next(
            item
            for item in criteria["criteria"]
            if isinstance(item, dict) and item.get("id") == "delivery_glossary_term_strategy"
        )

        combined_text = json.dumps(glossary_gate, ensure_ascii=False)

        for required_text in [
            "body_display_strategy",
            "where_to_preserve_english",
            "chinese_primary_only",
            "delivery_glossary_only",
            "forbidden_body_forms",
            "future optional extension",
            "not a v1 required field",
        ]:
            self.assertIn(required_text, combined_text)

    def test_delivery_glossary_term_strategy_must_stay_style_scan(self) -> None:
        wrong_category = default_criteria()
        wrong_category["criteria"] = [
            {
                **item,
                "category": "formula_information_gain",
                "scan_policy": "full_artifact_formula_scan",
            }
            if isinstance(item, dict) and item.get("id") == "delivery_glossary_term_strategy"
            else item
            for item in wrong_category["criteria"]
        ]
        self.assert_invalid(
            wrong_category,
            "criteria must include delivery_glossary_term_strategy in category style",
            "wrong-glossary-category.json",
        )

        wrong_scan_policy = default_criteria()
        wrong_scan_policy["criteria"] = [
            {**item, "scan_policy": "full_rendered_pdf_visual_scan"}
            if isinstance(item, dict) and item.get("id") == "delivery_glossary_term_strategy"
            else item
            for item in wrong_scan_policy["criteria"]
        ]
        self.assert_invalid(
            wrong_scan_policy,
            "criteria\\[\\d+\\].scan_policy must be 'full_artifact_style_scan'",
            "wrong-glossary-scan-policy.json",
        )

    def test_rejects_missing_allowed_category_criterion(self) -> None:
        criteria = default_criteria()
        criteria["criteria"] = [
            item
            for item in criteria["criteria"]
            if not isinstance(item, dict) or item.get("category") != "table_layout_integrity"
        ]

        self.assert_invalid(
            criteria,
            "criteria must include at least one criterion for each allowed category",
            "missing-table-layout-category.json",
        )

    def test_formula_information_gain_uses_full_formula_scan(self) -> None:
        criteria = default_criteria()
        formula_gate = next(
            item
            for item in criteria["criteria"]
            if isinstance(item, dict) and item.get("id") == "formula_information_gain"
        )
        wrong_policy = default_criteria()
        wrong_policy_item = deepcopy(formula_gate)
        assert isinstance(wrong_policy_item, dict)
        wrong_policy_item["scan_policy"] = "full_artifact_style_scan"
        wrong_policy["criteria"] = [
            wrong_policy_item if isinstance(item, dict) and item.get("id") == "formula_information_gain" else item
            for item in wrong_policy["criteria"]
        ]

        self.assert_invalid(
            wrong_policy,
            "criteria\\[\\d+\\].scan_policy must be 'full_artifact_formula_scan'",
            "wrong-formula-scan-policy.json",
        )

    def test_triggered_structural_scan_requires_boundary_fields_and_examples(self) -> None:
        criteria = default_criteria()
        argument_chain = next(
            item
            for item in criteria["criteria"]
            if isinstance(item, dict) and item.get("id") == "argument_chain_integrity"
        )

        missing_trigger = default_criteria()
        missing_item = deepcopy(argument_chain)
        assert isinstance(missing_item, dict)
        missing_item.pop("trigger_condition", None)
        missing_trigger["criteria"] = [
            missing_item if isinstance(item, dict) and item.get("id") == "argument_chain_integrity" else item
            for item in missing_trigger["criteria"]
        ]
        self.assert_invalid(
            missing_trigger,
            "criteria\\[\\d+\\] missing keys: trigger_condition",
            "missing-trigger-condition.json",
        )

        malformed_examples = default_criteria()
        malformed_item = deepcopy(argument_chain)
        assert isinstance(malformed_item, dict)
        malformed_item["examples"] = {"fail": {"text": "bad", "reason": "missing pass"}}
        malformed_examples["criteria"] = [
            malformed_item if isinstance(item, dict) and item.get("id") == "argument_chain_integrity" else item
            for item in malformed_examples["criteria"]
        ]
        self.assert_invalid(
            malformed_examples,
            "criteria\\[\\d+\\].examples missing keys: pass",
            "malformed-examples.json",
        )

        wrong_policy = default_criteria()
        wrong_policy_item = deepcopy(argument_chain)
        assert isinstance(wrong_policy_item, dict)
        wrong_policy_item["scan_policy"] = "full_rendered_pdf_visual_scan"
        wrong_policy["criteria"] = [
            wrong_policy_item if isinstance(item, dict) and item.get("id") == "argument_chain_integrity" else item
            for item in wrong_policy["criteria"]
        ]
        self.assert_invalid(
            wrong_policy,
            "criteria\\[\\d+\\].scan_policy must be 'triggered_structural_expression_scan'",
            "wrong-argument-chain-scan-policy.json",
        )


if __name__ == "__main__":
    unittest.main()

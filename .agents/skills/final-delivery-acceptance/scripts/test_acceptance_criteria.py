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


if __name__ == "__main__":
    unittest.main()

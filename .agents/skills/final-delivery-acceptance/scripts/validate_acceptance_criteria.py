#!/usr/bin/env python3
"""Validate Final Delivery Acceptance criteria files.

The validator is intentionally dependency-light. The JSON schema files document
the contract, while this script provides the fail-closed checks used by tests
and workflow automation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ALLOWED_CATEGORIES = {
    "style",
    "figure_visual_integrity",
    "table_layout_integrity",
    "credibility_disclosure_placement",
}
TOP_LEVEL_KEYS = {
    "schema_version",
    "criteria_version",
    "name",
    "scope",
    "review_context_policy",
    "evaluation_policy",
    "criteria",
}
SCOPE_KEYS = {"final_artifacts", "excluded_artifacts"}
REVIEW_CONTEXT_POLICY_KEYS = {"final_artifacts_and_criteria_only", "forbid_generation_process"}
EVALUATION_POLICY_KEYS = {"blocking_only", "fail_fast", "report_all_failures", "allowed_categories"}
CRITERION_KEYS = {
    "id",
    "category",
    "rule",
    "violation_patterns",
    "allowed_exceptions",
    "scan_policy",
    "pass_condition",
    "fail_condition",
}
FORBIDDEN_CRITERION_KEYS = {
    "severity",
    "priority",
    "weight",
    "score",
    "advisory",
    "non_blocking",
    "blocking",
    "must",
}
EXIT_VALID = 0
EXIT_INVALID = 1


class ValidationError(Exception):
    """Raised when an acceptance criteria file is malformed."""


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _validate_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing:
        raise ValidationError(f"{label} missing keys: {', '.join(sorted(missing))}")
    if extra:
        raise ValidationError(f"{label} has unknown keys: {', '.join(sorted(extra))}")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{label} must be a boolean")
    return value


def _require_string_array(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be an array")
    if not allow_empty and not value:
        raise ValidationError(f"{label} must not be empty")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_require_string(item, f"{label}[{index}]"))
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"criteria file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc


def _validate_scope(criteria: dict[str, Any]) -> None:
    scope = _require_object(criteria["scope"], "scope")
    _validate_keys(scope, SCOPE_KEYS, "scope")
    _require_string_array(scope["final_artifacts"], "scope.final_artifacts", allow_empty=False)
    _require_string_array(scope["excluded_artifacts"], "scope.excluded_artifacts")


def _validate_review_context_policy(criteria: dict[str, Any]) -> None:
    policy = _require_object(criteria["review_context_policy"], "review_context_policy")
    _validate_keys(policy, REVIEW_CONTEXT_POLICY_KEYS, "review_context_policy")
    if _require_bool(policy["final_artifacts_and_criteria_only"], "review_context_policy.final_artifacts_and_criteria_only") is not True:
        raise ValidationError("review_context_policy.final_artifacts_and_criteria_only must be true")
    if _require_bool(policy["forbid_generation_process"], "review_context_policy.forbid_generation_process") is not True:
        raise ValidationError("review_context_policy.forbid_generation_process must be true")


def _validate_evaluation_policy(criteria: dict[str, Any]) -> None:
    policy = _require_object(criteria["evaluation_policy"], "evaluation_policy")
    _validate_keys(policy, EVALUATION_POLICY_KEYS, "evaluation_policy")
    if _require_bool(policy["blocking_only"], "evaluation_policy.blocking_only") is not True:
        raise ValidationError("evaluation_policy.blocking_only must be true")
    if _require_bool(policy["fail_fast"], "evaluation_policy.fail_fast") is not False:
        raise ValidationError("evaluation_policy.fail_fast must be false")
    if _require_bool(policy["report_all_failures"], "evaluation_policy.report_all_failures") is not True:
        raise ValidationError("evaluation_policy.report_all_failures must be true")
    allowed = set(_require_string_array(policy["allowed_categories"], "evaluation_policy.allowed_categories", allow_empty=False))
    if allowed != ALLOWED_CATEGORIES:
        raise ValidationError(
            "evaluation_policy.allowed_categories must exactly match: "
            + ", ".join(sorted(ALLOWED_CATEGORIES))
        )


def _expected_scan_policy(category: str) -> str:
    if category == "style":
        return "full_artifact_style_scan"
    return "full_rendered_pdf_visual_scan"


def _validate_criterion(value: Any, index: int, seen_ids: set[str]) -> None:
    criterion = _require_object(value, f"criteria[{index}]")
    extra = set(criterion) - CRITERION_KEYS
    forbidden = extra & FORBIDDEN_CRITERION_KEYS
    if forbidden:
        raise ValidationError(f"criteria[{index}] has forbidden keys: {', '.join(sorted(forbidden))}")
    missing = CRITERION_KEYS - set(criterion)
    if missing:
        raise ValidationError(f"criteria[{index}] missing keys: {', '.join(sorted(missing))}")
    if extra:
        raise ValidationError(f"criteria[{index}] has unknown keys: {', '.join(sorted(extra))}")

    criterion_id = _require_string(criterion["id"], f"criteria[{index}].id")
    if criterion_id in seen_ids:
        raise ValidationError(f"criteria[{index}].id is duplicated: {criterion_id}")
    seen_ids.add(criterion_id)

    category = _require_string(criterion["category"], f"criteria[{index}].category")
    if category not in ALLOWED_CATEGORIES:
        raise ValidationError(f"criteria[{index}].category is invalid")

    scan_policy = _require_string(criterion["scan_policy"], f"criteria[{index}].scan_policy")
    expected = _expected_scan_policy(category)
    if scan_policy != expected:
        raise ValidationError(f"criteria[{index}].scan_policy must be {expected!r}")

    _require_string(criterion["rule"], f"criteria[{index}].rule")
    _require_string_array(criterion["violation_patterns"], f"criteria[{index}].violation_patterns")
    _require_string_array(criterion["allowed_exceptions"], f"criteria[{index}].allowed_exceptions")
    _require_string(criterion["pass_condition"], f"criteria[{index}].pass_condition")
    _require_string(criterion["fail_condition"], f"criteria[{index}].fail_condition")


def validate_acceptance_criteria(path: Path) -> list[str]:
    """Validate a criteria JSON file and return non-fatal warnings."""

    criteria = _require_object(_load_json(path), "criteria")
    _validate_keys(criteria, TOP_LEVEL_KEYS, "criteria")
    if criteria["schema_version"] != "1.0":
        raise ValidationError("schema_version must be '1.0'")
    if criteria["criteria_version"] != "1.0":
        raise ValidationError("criteria_version must be '1.0'")
    _require_string(criteria["name"], "name")
    _validate_scope(criteria)
    _validate_review_context_policy(criteria)
    _validate_evaluation_policy(criteria)

    criteria_list = criteria["criteria"]
    if not isinstance(criteria_list, list):
        raise ValidationError("criteria must be an array")
    if not criteria_list:
        raise ValidationError("criteria must not be empty")
    seen_ids: set[str] = set()
    for index, criterion in enumerate(criteria_list):
        _validate_criterion(criterion, index, seen_ids)
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Final Delivery Acceptance criteria JSON file.")
    parser.add_argument("criteria", type=Path)
    args = parser.parse_args()

    try:
        warnings = validate_acceptance_criteria(args.criteria)
    except ValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"VALID: {args.criteria}")
    return EXIT_VALID


if __name__ == "__main__":
    raise SystemExit(main())

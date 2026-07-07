#!/usr/bin/env python3
"""Validate Delivery Glossary contract files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


TOP_LEVEL_KEYS = {
    "schema_version",
    "language_profile",
    "default_reader_mode",
    "terms",
}
TERM_KEYS = {
    "english",
    "chinese_primary",
    "plain_language_boundary",
    "related_terms",
    "opposed_terms",
    "first_use_expected_location",
    "body_display_strategy",
    "where_to_preserve_english",
    "required_after_first_use",
}
SCHEMA_VERSION = "delivery_glossary.v1"
LANGUAGE_PROFILE = "non_english_teaching_pdf"
BODY_DISPLAY_STRATEGIES = {
    "preserve_english",
    "chinese_with_english_parenthetical",
    "chinese_primary_only",
    "quote_only",
}
ENGLISH_PRESERVATION_LOCATIONS = {
    "body_parenthetical",
    "body_after_definition",
    "footnote",
    "caption",
    "quote_only",
    "delivery_glossary_only",
    "none",
}
EXIT_VALID = 0
EXIT_INVALID = 1


class ValidationError(Exception):
    """Raised when a Delivery Glossary file is malformed."""


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _validate_required_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = expected - set(value)
    if missing:
        raise ValidationError(f"{label} missing keys: {', '.join(sorted(missing))}")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value


def _require_string_array(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_require_string(item, f"{label}[{index}]"))
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"delivery glossary file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc


def validate_delivery_glossary(path: Path) -> list[str]:
    """Validate one Delivery Glossary JSON file and return non-fatal warnings."""

    glossary = _require_object(_load_json(path), "delivery glossary")
    _validate_required_keys(glossary, TOP_LEVEL_KEYS, "delivery glossary")
    if _require_string(glossary["schema_version"], "schema_version") != SCHEMA_VERSION:
        raise ValidationError(f"schema_version must be {SCHEMA_VERSION!r}")
    if _require_string(glossary["language_profile"], "language_profile") != LANGUAGE_PROFILE:
        raise ValidationError(f"language_profile must be {LANGUAGE_PROFILE!r}")
    _require_string(glossary["default_reader_mode"], "default_reader_mode")
    terms = glossary["terms"]
    if not isinstance(terms, list):
        raise ValidationError("terms must be an array")
    if not terms:
        raise ValidationError("terms must not be empty")
    seen_english: set[str] = set()
    for index, term in enumerate(terms):
        term_obj = _require_object(term, f"terms[{index}]")
        _validate_required_keys(term_obj, TERM_KEYS, f"terms[{index}]")
        english = _require_string(term_obj["english"], f"terms[{index}].english")
        if english in seen_english:
            raise ValidationError(f"terms[{index}].english is duplicated: {english}")
        seen_english.add(english)
        _require_string(term_obj["chinese_primary"], f"terms[{index}].chinese_primary")
        _require_string(term_obj["plain_language_boundary"], f"terms[{index}].plain_language_boundary")
        _require_string_array(term_obj["related_terms"], f"terms[{index}].related_terms")
        _require_string_array(term_obj["opposed_terms"], f"terms[{index}].opposed_terms")
        _require_string(term_obj["first_use_expected_location"], f"terms[{index}].first_use_expected_location")
        strategy = _require_string(term_obj["body_display_strategy"], f"terms[{index}].body_display_strategy")
        if strategy not in BODY_DISPLAY_STRATEGIES:
            raise ValidationError(f"terms[{index}].body_display_strategy is invalid")
        location = _require_string(term_obj["where_to_preserve_english"], f"terms[{index}].where_to_preserve_english")
        if location not in ENGLISH_PRESERVATION_LOCATIONS:
            raise ValidationError(f"terms[{index}].where_to_preserve_english is invalid")
        _require_string(term_obj["required_after_first_use"], f"terms[{index}].required_after_first_use")
        if "forbidden_body_forms" in term_obj:
            _require_string_array(term_obj["forbidden_body_forms"], f"terms[{index}].forbidden_body_forms")
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one Delivery Glossary JSON file.")
    parser.add_argument("glossary", type=Path)
    args = parser.parse_args()

    try:
        warnings = validate_delivery_glossary(args.glossary)
    except ValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"VALID: {args.glossary}")
    return EXIT_VALID


if __name__ == "__main__":
    raise SystemExit(main())

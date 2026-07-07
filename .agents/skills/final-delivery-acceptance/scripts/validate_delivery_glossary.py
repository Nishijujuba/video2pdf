#!/usr/bin/env python3
"""Validate Delivery Glossary contract files."""

from __future__ import annotations

import argparse
import json
import re
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


def _english_expression_source(english: str) -> str:
    return r"\s+".join(re.escape(part) for part in english.split())


def _english_expression_pattern(english: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9_]){_english_expression_source(english)}(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )


def _parenthetical_pattern(chinese_primary: str, english: str) -> re.Pattern[str]:
    return re.compile(
        rf"{re.escape(chinese_primary)}\s*[（(]\s*{_english_expression_source(english)}\s*[)）]",
        re.IGNORECASE,
    )


def _line_location(body_text: str, offset: int, artifact_path: str) -> str:
    line = body_text.count("\n", 0, max(offset, 0)) + 1
    return f"{artifact_path}:line {line}"


def _first_match_location(body_text: str, artifact_path: str, *patterns: re.Pattern[str]) -> str:
    offsets = [match.start() for pattern in patterns if (match := pattern.search(body_text))]
    if offsets:
        return _line_location(body_text, min(offsets), artifact_path)
    return f"{artifact_path}:full artifact"


def _finding(
    *,
    term: str,
    status: str,
    location: str,
    summary: str,
    strategy: str,
    preservation: str,
) -> dict[str, str]:
    return {
        "term": term,
        "status": status,
        "location": location,
        "summary": summary,
        "body_display_strategy": strategy,
        "where_to_preserve_english": preservation,
    }


def evaluate_delivery_glossary_body_text(
    glossary: dict[str, Any],
    body_text: str,
    *,
    artifact_path: str = "main.tex",
) -> dict[str, Any]:
    """Evaluate representative final body text against Delivery Glossary strategies.

    This helper is intentionally conservative fixture support. It checks explicit
    string patterns used by regression tests and produces evidence that can be
    embedded in an Acceptance Report; the read-only reviewer still owns semantic
    judgment for real PDFs.
    """

    glossary_obj = _require_object(glossary, "delivery glossary")
    _validate_required_keys(glossary_obj, TOP_LEVEL_KEYS, "delivery glossary")
    if not isinstance(body_text, str):
        raise ValidationError("body_text must be a string")
    terms = glossary_obj["terms"]
    if not isinstance(terms, list):
        raise ValidationError("terms must be an array")

    findings: list[dict[str, str]] = []
    failed_terms: list[str] = []
    checked_terms: list[str] = []
    for index, term in enumerate(terms):
        term_obj = _require_object(term, f"terms[{index}]")
        english = _require_string(term_obj.get("english"), f"terms[{index}].english") or ""
        chinese_primary = _require_string(term_obj.get("chinese_primary"), f"terms[{index}].chinese_primary") or ""
        strategy = _require_string(term_obj.get("body_display_strategy"), f"terms[{index}].body_display_strategy") or ""
        preservation = _require_string(
            term_obj.get("where_to_preserve_english"),
            f"terms[{index}].where_to_preserve_english",
        ) or ""
        english_pattern = _english_expression_pattern(english)
        chinese_pattern = re.compile(re.escape(chinese_primary))
        parenthetical_pattern = _parenthetical_pattern(chinese_primary, english)
        has_english = english_pattern.search(body_text) is not None
        has_chinese = chinese_pattern.search(body_text) is not None
        has_parenthetical = parenthetical_pattern.search(body_text) is not None
        if not has_english and not has_chinese:
            continue

        checked_terms.append(english)
        location = _first_match_location(body_text, artifact_path, english_pattern, chinese_pattern)
        status = "pass"
        summary = "Body wording matches the Delivery Glossary strategy."
        if strategy == "chinese_primary_only":
            if has_english:
                status = "fail"
                summary = (
                    f"`{english}` appears in body prose even though the glossary requires "
                    f"`chinese_primary_only` with `{preservation}` preservation."
                )
            elif not has_chinese:
                status = "fail"
                summary = f"`{chinese_primary}` is missing from body prose for `{english}`."
        elif strategy == "chinese_with_english_parenthetical":
            if not has_parenthetical:
                status = "fail"
                summary = (
                    f"`{english}` must appear in a body parenthetical next to `{chinese_primary}` "
                    "for this fixture strategy."
                )
        elif strategy == "preserve_english":
            if not has_english:
                status = "fail"
                summary = f"`{english}` is missing even though the strategy preserves English body wording."
        elif strategy == "quote_only":
            if has_english and preservation not in {"quote_only", "delivery_glossary_only"}:
                status = "fail"
                summary = f"`{english}` appears outside a quote-only fixture allowance."

        if preservation == "body_parenthetical" and not has_parenthetical:
            status = "fail"
            summary = f"`{english}` is not preserved in the required body parenthetical position."
        if preservation in {"delivery_glossary_only", "none"} and has_english and strategy == "chinese_primary_only":
            status = "fail"
        if status == "fail":
            failed_terms.append(english)
        findings.append(
            _finding(
                term=english,
                status=status,
                location=location,
                summary=summary,
                strategy=strategy,
                preservation=preservation,
            )
        )

    return {
        "scan_policy": "delivery_glossary_body_text_fixture_scan",
        "scanned_artifacts": [artifact_path],
        "status": "fail" if failed_terms else "pass",
        "terms_checked": checked_terms,
        "failed_terms": failed_terms,
        "findings": findings,
        "limitations": "Deterministic representative fixture scan; real acceptance still requires reviewer judgment.",
    }


def classify_delivery_glossary_candidate(
    english: str,
    usage_text: str,
    *,
    candidate_kind: str = "concept",
    defines_new_core_concept: bool = False,
) -> dict[str, Any]:
    """Classify representative new-term candidates for governance fixtures."""

    english = _require_string(english, "english") or ""
    usage_text = _require_string(usage_text, "usage_text") or ""
    candidate_kind = _require_string(candidate_kind, "candidate_kind") or ""
    lowered_expression = english.lower()
    lowered_usage = usage_text.lower()
    excluded_name_kinds = {
        "product_name",
        "company_name",
        "person_name",
        "code_identifier",
        "command",
        "file_extension",
    }
    if candidate_kind in excluded_name_kinds and not defines_new_core_concept:
        return {
            "include": False,
            "reason": "Name-only usage does not define a new core concept.",
            "english": english,
        }
    if defines_new_core_concept:
        return {
            "include": True,
            "reason": "The candidate is marked as defining a new core concept.",
            "english": english,
        }

    if lowered_expression == "html mockup":
        method_signals = (
            "method",
            "reference artifact",
            "layout and interaction intent",
            "interaction intent",
            "communicates layout",
            "model",
        )
        file_only_signals = ("html file", "file named", "index.html", ".html")
        has_method_signal = any(signal in lowered_usage for signal in method_signals)
        has_file_only_signal = any(signal in lowered_usage for signal in file_only_signals)
        if has_method_signal:
            return {
                "include": True,
                "reason": "HTML mockup is used as a method concept.",
                "english": english,
            }
        if has_file_only_signal:
            return {
                "include": False,
                "reason": "HTML mockup only names an HTML file in this usage.",
                "english": english,
            }

    concept_signals = ("defines", "concept", "framework", "method", "category", "recurring")
    include = any(signal in lowered_usage for signal in concept_signals)
    return {
        "include": include,
        "reason": "Usage carries explanatory work." if include else "Usage does not carry core explanatory work.",
        "english": english,
    }


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

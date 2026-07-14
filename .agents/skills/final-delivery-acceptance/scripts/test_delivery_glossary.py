#!/usr/bin/env python3
"""Tests for Delivery Glossary validation."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from validate_delivery_glossary import ValidationError, validate_delivery_glossary


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = Path(__file__).resolve().with_name("validate_delivery_glossary.py")
DOC_PATH = REPO_ROOT / "docs" / "acceptance" / "delivery_glossary.v1.md"
SCHEMA_PATH = REPO_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "references" / "delivery-glossary.schema.json"
SKILL_PATH = REPO_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "SKILL.md"


def minimal_glossary() -> dict[str, object]:
    return {
        "schema_version": "delivery_glossary.v1",
        "language_profile": "non_english_teaching_pdf",
        "default_reader_mode": "standalone_readable_video_learning_note",
        "terms": [
            {
                "english": "grief",
                "chinese_primary": "\u5931\u843d\u611f",
                "plain_language_boundary": "A narrow sense of loss when an old working identity is reinterpreted.",
                "related_terms": ["sense of loss", "craft identity"],
                "opposed_terms": ["relief", "increased agency"],
                "first_use_expected_location": "section_04.tex",
                "body_display_strategy": "chinese_primary_only",
                "where_to_preserve_english": "delivery_glossary_only",
                "required_after_first_use": "\u540e\u6587\u4f18\u5148\u4f7f\u7528\u201c\u5931\u843d\u611f\u201d\u3002",
            }
        ],
    }


class DeliveryGlossaryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_dir = REPO_ROOT / "\u5f85\u5220\u9664" / "delivery-glossary-tests"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, payload: dict[str, object], name: str) -> Path:
        path = self.run_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def assert_invalid(self, payload: dict[str, object], message: str, name: str) -> None:
        with self.assertRaisesRegex(ValidationError, message):
            validate_delivery_glossary(self.write_json(payload, name))

    def test_valid_minimal_delivery_glossary_passes(self) -> None:
        path = self.write_json(minimal_glossary(), "minimal-valid.json")

        warnings = validate_delivery_glossary(path)

        self.assertEqual(warnings, [])

    def test_rejects_missing_top_level_fields_and_empty_required_strings(self) -> None:
        missing_version = minimal_glossary()
        missing_version.pop("schema_version", None)
        self.assert_invalid(missing_version, "delivery glossary missing keys: schema_version", "missing-schema-version.json")

        blank_language_profile = minimal_glossary()
        blank_language_profile["language_profile"] = " "
        self.assert_invalid(
            blank_language_profile,
            "language_profile must be a non-empty string",
            "blank-language-profile.json",
        )

    def test_rejects_invalid_language_profile(self) -> None:
        glossary = minimal_glossary()
        glossary["language_profile"] = "english_learning_pdf"

        self.assert_invalid(glossary, "language_profile must be 'non_english_teaching_pdf'", "invalid-profile.json")

    def test_rejects_wrong_schema_version_and_empty_terms(self) -> None:
        wrong_version = minimal_glossary()
        wrong_version["schema_version"] = "delivery_glossary.v2"
        self.assert_invalid(wrong_version, "schema_version must be 'delivery_glossary.v1'", "wrong-schema-version.json")

        empty_terms = minimal_glossary()
        empty_terms["terms"] = []
        self.assert_invalid(empty_terms, "terms must not be empty", "empty-terms.json")

    def test_rejects_incomplete_or_blank_term_entries(self) -> None:
        missing_english = minimal_glossary()
        term = dict(missing_english["terms"][0])  # type: ignore[index]
        term.pop("english", None)
        missing_english["terms"] = [term]
        self.assert_invalid(missing_english, "terms\\[0\\] missing keys: english", "missing-term-english.json")

        blank_chinese = minimal_glossary()
        term = dict(blank_chinese["terms"][0])  # type: ignore[index]
        term["chinese_primary"] = ""
        blank_chinese["terms"] = [term]
        self.assert_invalid(blank_chinese, "terms\\[0\\].chinese_primary must be a non-empty string", "blank-term-chinese.json")

    def test_rejects_invalid_body_display_and_english_preservation_enums(self) -> None:
        invalid_strategy = minimal_glossary()
        term = dict(invalid_strategy["terms"][0])  # type: ignore[index]
        term["body_display_strategy"] = "always_parenthesize"
        invalid_strategy["terms"] = [term]
        self.assert_invalid(
            invalid_strategy,
            "terms\\[0\\].body_display_strategy is invalid",
            "invalid-body-display-strategy.json",
        )

        invalid_location = minimal_glossary()
        term = dict(invalid_location["terms"][0])  # type: ignore[index]
        term["where_to_preserve_english"] = "appendix"
        invalid_location["terms"] = [term]
        self.assert_invalid(
            invalid_location,
            "terms\\[0\\].where_to_preserve_english is invalid",
            "invalid-english-preservation-location.json",
        )

    def test_rejects_duplicate_english_source_terms(self) -> None:
        duplicate = minimal_glossary()
        duplicate["terms"] = [
            dict(duplicate["terms"][0]),  # type: ignore[index]
            dict(duplicate["terms"][0]),  # type: ignore[index]
        ]

        self.assert_invalid(duplicate, "terms\\[1\\].english is duplicated: grief", "duplicate-english.json")

    def test_accepts_future_extensions_without_requiring_forbidden_body_forms(self) -> None:
        glossary = minimal_glossary()
        glossary["future_profile_note"] = "Reserved extension point."
        term = dict(glossary["terms"][0])  # type: ignore[index]
        term["forbidden_body_forms"] = ["bare grief"]
        term["future_review_hint"] = "Allowed without schema churn."
        glossary["terms"] = [term]

        warnings = validate_delivery_glossary(self.write_json(glossary, "future-extensions.json"))

        self.assertEqual(warnings, [])

        malformed_known_extension = minimal_glossary()
        term = dict(malformed_known_extension["terms"][0])  # type: ignore[index]
        term["forbidden_body_forms"] = "bare grief"
        malformed_known_extension["terms"] = [term]
        self.assert_invalid(
            malformed_known_extension,
            "terms\\[0\\].forbidden_body_forms must be an array",
            "malformed-forbidden-body-forms.json",
        )

    def test_fixture_covers_grief_capability_overhang_and_product_name_exclusion(self) -> None:
        glossary = minimal_glossary()
        capability_overhang = {
            "english": "capability overhang",
            "chinese_primary": "\u80fd\u529b\u6ede\u540e\u91ca\u653e",
            "plain_language_boundary": "A technical label for capability that becomes usable after tooling or workflow catches up.",
            "related_terms": ["latent capability", "deployment bottleneck"],
            "opposed_terms": ["capability gap"],
            "first_use_expected_location": "section_02.tex",
            "body_display_strategy": "chinese_with_english_parenthetical",
            "where_to_preserve_english": "body_parenthetical",
            "required_after_first_use": "\u540e\u6587\u4f7f\u7528\u7a33\u5b9a\u4e2d\u6587\u540d\uff0c\u5fc5\u8981\u65f6\u4fdd\u7559\u82f1\u6587\u62ec\u6ce8\u3002",
        }
        glossary["terms"] = [dict(glossary["terms"][0]), capability_overhang]  # type: ignore[index]

        warnings = validate_delivery_glossary(self.write_json(glossary, "representative-terms.json"))

        english_terms = {term["english"] for term in glossary["terms"] if isinstance(term, dict)}
        self.assertEqual(warnings, [])
        self.assertIn("grief", english_terms)
        self.assertIn("capability overhang", english_terms)
        self.assertNotIn("Cursor", english_terms)

    def test_cli_validates_one_glossary_file_independently(self) -> None:
        valid_path = self.write_json(minimal_glossary(), "cli-valid.json")
        valid = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(valid_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(valid.returncode, 0)
        self.assertIn("VALID:", valid.stdout)

        invalid_payload = minimal_glossary()
        invalid_payload["language_profile"] = "english_learning_pdf"
        invalid_path = self.write_json(invalid_payload, "cli-invalid.json")
        invalid = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(invalid_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("INVALID:", invalid.stderr)

    def test_documentation_and_schema_record_contract_boundary(self) -> None:
        doc = DOC_PATH.read_text(encoding="utf-8")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("Delivery Glossary is a contract artifact", doc)
        self.assertIn("not a default PDF appendix", doc)
        self.assertIn("validate_delivery_glossary.py", doc)
        self.assertEqual(schema["properties"]["schema_version"]["const"], "delivery_glossary.v1")
        self.assertIn("body_display_strategy", schema["properties"]["terms"]["items"]["required"])
        self.assertIn("where_to_preserve_english", schema["properties"]["terms"]["items"]["required"])
        self.assertIn("scripts/validate_delivery_glossary.py", skill)


if __name__ == "__main__":
    unittest.main()

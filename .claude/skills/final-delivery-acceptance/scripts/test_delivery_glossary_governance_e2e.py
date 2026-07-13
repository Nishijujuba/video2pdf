#!/usr/bin/env python3
"""End-to-end regression fixtures for Delivery Glossary governance."""

from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path
from typing import Any

import fitz

import validate_delivery_glossary as glossary_module
from validate_acceptance_report import (
    GateBlockedError,
    compute_artifact_fingerprint,
    create_allowed_artifacts_manifest,
    validate_acceptance_report,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
CRITERIA_PATH = REPO_ROOT / "docs" / "acceptance" / "acceptance_criteria.v1.json"
DELIVERY_GLOSSARY_PATH = "review/acceptance/delivery_glossary.json"
GLOSSARY_CRITERION_ID = "delivery_glossary_term_strategy"
TEXT_CATEGORIES = {"style", "logic_readability"}
FORMULA_CATEGORIES = {"formula_information_gain"}
VISUAL_CATEGORIES = {
    "figure_visual_integrity",
    "table_layout_integrity",
    "credibility_disclosure_placement",
}


def load_criteria() -> dict[str, Any]:
    return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))


def representative_glossary() -> dict[str, Any]:
    return {
        "schema_version": "delivery_glossary.v1",
        "language_profile": "non_english_teaching_pdf",
        "default_reader_mode": "standalone_readable_video_learning_note",
        "terms": [
            {
                "english": "grief",
                "chinese_primary": "失落感",
                "plain_language_boundary": "A bounded sense of loss when an old working identity is reinterpreted.",
                "related_terms": ["craft identity", "sense of loss"],
                "opposed_terms": ["relief", "increased agency"],
                "first_use_expected_location": "main.tex section 1",
                "body_display_strategy": "chinese_primary_only",
                "where_to_preserve_english": "delivery_glossary_only",
                "required_after_first_use": "Body prose should use the bounded Chinese expression “失落感”.",
            },
            {
                "english": "capability overhang",
                "chinese_primary": "能力悬置",
                "plain_language_boundary": "A technical label for latent capability that becomes usable after tooling catches up.",
                "related_terms": ["latent capability", "deployment bottleneck"],
                "opposed_terms": ["capability gap"],
                "first_use_expected_location": "main.tex section 2",
                "body_display_strategy": "chinese_with_english_parenthetical",
                "where_to_preserve_english": "body_parenthetical",
                "required_after_first_use": "Preserve the English label at the expected first body parenthetical use.",
            },
            {
                "english": "HTML mockup",
                "chinese_primary": "HTML 原型稿",
                "plain_language_boundary": "A method concept for giving a model a reference artifact that communicates layout and interaction intent.",
                "related_terms": ["reference artifact", "interaction intent"],
                "opposed_terms": ["plain HTML file"],
                "first_use_expected_location": "main.tex section 3",
                "body_display_strategy": "chinese_with_english_parenthetical",
                "where_to_preserve_english": "body_parenthetical",
                "required_after_first_use": "Use the Chinese method name after the first parenthetical alignment.",
            },
        ],
    }


def body_checker() -> Any:
    checker = getattr(glossary_module, "evaluate_delivery_glossary_body_text", None)
    if checker is None:
        raise AssertionError("validate_delivery_glossary.evaluate_delivery_glossary_body_text is missing")
    return checker


def candidate_classifier() -> Any:
    classifier = getattr(glossary_module, "classify_delivery_glossary_candidate", None)
    if classifier is None:
        raise AssertionError("validate_delivery_glossary.classify_delivery_glossary_candidate is missing")
    return classifier


class DeliveryGlossaryGovernanceFixturesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.video_dir = REPO_ROOT / "待删除" / "delivery-glossary-governance-e2e" / f"{self._testMethodName}-{uuid.uuid4().hex}"
        self.acceptance_dir = self.video_dir / "review" / "acceptance"
        self.rendered_dir = self.acceptance_dir / "rendered_pages"
        self.rendered_dir.mkdir(parents=True, exist_ok=True)
        self.glossary = representative_glossary()
        (self.acceptance_dir / "delivery_glossary.json").write_text(
            json.dumps(self.glossary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.video_dir / "main.tex").write_text("Placeholder final body text.\n", encoding="utf-8")
        self._write_pdf(self.video_dir / "final.pdf", pages=1)
        (self.rendered_dir / "page_0001.png").write_bytes(b"png evidence")
        self.manifest_path = create_allowed_artifacts_manifest(
            self.video_dir,
            CRITERIA_PATH,
            [("tex", "main.tex"), ("pdf", "final.pdf")],
            include_delivery_glossary=True,
        )

    def _write_pdf(self, path: Path, *, pages: int) -> None:
        doc = fitz.open()
        for page_number in range(1, pages + 1):
            page = doc.new_page(width=300, height=300)
            page.insert_text((72, 72), f"Page {page_number}")
        doc.save(path)
        doc.close()

    def write_body(self, body: str) -> None:
        (self.video_dir / "main.tex").write_text(body, encoding="utf-8")

    def valid_report(self, scan_evidence: dict[str, Any], *, status: str) -> dict[str, Any]:
        criteria = load_criteria()
        criteria_items = criteria["criteria"]
        assert isinstance(criteria_items, list)
        is_fail = status == "fail"
        criterion_results: list[dict[str, Any]] = []
        for item in criteria_items:
            criterion_id = item["id"]
            category = item["category"]
            result_status = "fail" if is_fail and criterion_id == GLOSSARY_CRITERION_ID else "pass"
            evidence = [
                {
                    "artifact_path": "main.tex" if category in TEXT_CATEGORIES | FORMULA_CATEGORIES else "final.pdf",
                    "location": "full artifact",
                    "summary": "No blocking defect detected.",
                }
            ]
            revision_guidance = None
            if result_status == "fail":
                evidence = [
                    {
                        "artifact_path": "main.tex",
                        "location": scan_evidence["findings"][0]["location"],
                        "summary": scan_evidence["findings"][0]["summary"],
                    }
                ]
                revision_guidance = {
                    "required_change": "Rewrite body terminology to match Delivery Glossary strategy.",
                    "allowed_fix_types": ["rewrite body prose", "update glossary if the contract is wrong"],
                }
            criterion_results.append(
                {
                    "criterion_id": criterion_id,
                    "category": category,
                    "status": result_status,
                    "evidence": evidence,
                    "scan_evidence": (
                        {
                            "scan_policy": item["scan_policy"],
                            "scanned_artifacts": ["main.tex"],
                            "formulas_checked": [],
                            "no_body_formula_found": True,
                        }
                        if category in FORMULA_CATEGORIES
                        else scan_evidence
                        if criterion_id == GLOSSARY_CRITERION_ID
                        else {
                            "scan_policy": item["scan_policy"],
                            "scanned_artifacts": ["main.tex" if category in TEXT_CATEGORIES else "final.pdf"],
                        }
                    ),
                    "revision_guidance": revision_guidance,
                }
            )

        return {
            "schema_version": "1.0",
            "criteria_version": criteria["criteria_version"],
            "criteria_file": "docs/acceptance/acceptance_criteria.v1.json",
            "overall_status": status,
            "decision_source": "acceptance_report_json",
            "review_context_used": {
                "allowed_artifacts_manifest": "review/acceptance/allowed_artifacts_manifest.json",
                "final_artifacts_only": True,
                "generation_process_used": False,
                "artifacts_read": [
                    "main.tex",
                    "final.pdf",
                    DELIVERY_GLOSSARY_PATH,
                    "docs/acceptance/acceptance_criteria.v1.json",
                ],
            },
            "artifact_fingerprints": [
                compute_artifact_fingerprint(self.video_dir / "main.tex", "main.tex"),
                compute_artifact_fingerprint(self.video_dir / "final.pdf", "final.pdf"),
                compute_artifact_fingerprint(self.acceptance_dir / "delivery_glossary.json", DELIVERY_GLOSSARY_PATH),
            ],
            "criterion_results": criterion_results,
            "visual_scan_evidence": {
                "pdf": "final.pdf",
                "page_count": 1,
                "rendered_pages_dir": "review/acceptance/rendered_pages",
                "pages_checked": [
                    {
                        "page": 1,
                        "rendered_page_image": "review/acceptance/rendered_pages/page_0001.png",
                        "status": "pass",
                        "criteria_checked": [
                            "figure_visual_integrity",
                            "table_layout_integrity",
                            "credibility_disclosure_placement",
                        ],
                        "failures": [],
                    }
                ],
            },
            "failed_criteria": [GLOSSARY_CRITERION_ID] if is_fail else [],
            "revision_required": is_fail,
        }

    def validate_report(self, report: dict[str, Any]) -> list[str]:
        report_path = self.acceptance_dir / "acceptance_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return validate_acceptance_report(
            report_path,
            criteria_path=CRITERIA_PATH,
            video_output_dir=self.video_dir,
            manifest_path=self.manifest_path,
            enforce_decision=True,
        )

    def test_good_body_text_passes_glossary_strategy_and_report_validation(self) -> None:
        self.write_body(
            "本节讨论的“失落感”，指的是工具转型时旧身份被重写的窄义心理断裂。\n"
            "能力悬置（capability overhang）说明模型能力早已存在，只是等待工作流释放。\n"
            "这里的 HTML 原型稿（HTML mockup）是一种把布局和交互意图交给模型的方法概念。\n"
        )

        scan_evidence = body_checker()(self.glossary, (self.video_dir / "main.tex").read_text(encoding="utf-8"))
        report = self.valid_report(scan_evidence, status="pass")

        self.assertEqual(scan_evidence["status"], "pass")
        self.assertEqual(self.validate_report(report), [])

    def test_bad_grief_body_text_fails_glossary_strategy_and_blocks_report(self) -> None:
        self.write_body(
            "本节讨论的 grief 是工具转型时旧身份被重写的窄义心理断裂。\n"
            "能力悬置（capability overhang）说明模型能力早已存在，只是等待工作流释放。\n"
            "这里的 HTML 原型稿（HTML mockup）是一种把布局和交互意图交给模型的方法概念。\n"
        )

        scan_evidence = body_checker()(self.glossary, (self.video_dir / "main.tex").read_text(encoding="utf-8"))
        report = self.valid_report(scan_evidence, status="fail")

        self.assertEqual(scan_evidence["status"], "fail")
        self.assertEqual(scan_evidence["findings"][0]["term"], "grief")
        with self.assertRaisesRegex(GateBlockedError, "acceptance report status 'fail' blocks delivery"):
            self.validate_report(report)

    def test_candidate_boundary_keeps_html_mockup_method_concept_and_excludes_plain_file_use(self) -> None:
        classify = candidate_classifier()

        method_use = classify(
            "HTML mockup",
            "The speaker uses HTML mockup as a method for giving the model a reference artifact that communicates layout and interaction intent.",
        )
        plain_file_use = classify(
            "HTML mockup",
            "The source folder contains an HTML mockup file named index.html.",
        )

        self.assertTrue(method_use["include"], method_use)
        self.assertFalse(plain_file_use["include"], plain_file_use)

    def test_product_names_are_excluded_unless_they_define_a_new_core_concept(self) -> None:
        classify = candidate_classifier()

        product_only = classify(
            "Cursor",
            "The speaker opens Cursor and edits one file.",
            candidate_kind="product_name",
            defines_new_core_concept=False,
        )
        product_as_concept = classify(
            "Cursor memory boundary",
            "The speaker defines Cursor memory boundary as a reusable concept for deciding which context belongs in rules.",
            candidate_kind="product_name",
            defines_new_core_concept=True,
        )

        self.assertFalse(product_only["include"], product_only)
        self.assertTrue(product_as_concept["include"], product_as_concept)

    def test_default_workflow_does_not_require_reader_facing_glossary_appendix(self) -> None:
        self.write_body(
            "\\section{正文}\n"
            "本节讨论的“失落感”，指的是工具转型时旧身份被重写的窄义心理断裂。\n"
            "能力悬置（capability overhang）说明模型能力早已存在，只是等待工作流释放。\n"
            "这里的 HTML 原型稿（HTML mockup）是一种把布局和交互意图交给模型的方法概念。\n"
        )
        scan_evidence = body_checker()(self.glossary, (self.video_dir / "main.tex").read_text(encoding="utf-8"))
        report = self.valid_report(scan_evidence, status="pass")

        appendix_headings = ["\\section{Delivery Glossary}", "\\section{术语表}", "\\section{Glossary}"]
        body_text = (self.video_dir / "main.tex").read_text(encoding="utf-8")

        self.assertFalse(any(heading in body_text for heading in appendix_headings))
        self.assertEqual(self.validate_report(report), [])


if __name__ == "__main__":
    unittest.main()

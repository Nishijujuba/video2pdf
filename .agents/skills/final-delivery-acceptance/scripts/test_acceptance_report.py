#!/usr/bin/env python3
"""Tests for Final Delivery Acceptance report validation."""

from __future__ import annotations

import json
import unittest
import uuid
from copy import deepcopy
from pathlib import Path

import fitz

from validate_acceptance_report import (
    GateBlockedError,
    ValidationError,
    compute_artifact_fingerprint,
    create_allowed_artifacts_manifest,
    validate_acceptance_report,
    validate_delivery_decision,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
CRITERIA_PATH = REPO_ROOT / "docs" / "acceptance" / "acceptance_criteria.v1.json"
TEXT_CATEGORIES = {"style", "logic_readability"}
FORMULA_CATEGORIES = {"formula_information_gain"}
VISUAL_CATEGORIES = {
    "figure_visual_integrity",
    "table_layout_integrity",
    "credibility_disclosure_placement",
}


def load_criteria() -> dict[str, object]:
    return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))


class AcceptanceReportValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.video_dir = (
            REPO_ROOT
            / "待删除"
            / "final-delivery-acceptance-report-tests"
            / f"{self._testMethodName}-{uuid.uuid4().hex}"
        )
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.acceptance_dir = self.video_dir / "review" / "acceptance"
        self.rendered_dir = self.acceptance_dir / "rendered_pages"
        self.rendered_dir.mkdir(parents=True, exist_ok=True)
        (self.video_dir / "main.tex").write_text("Final article text.\n", encoding="utf-8")
        self._write_pdf(self.video_dir / "final.pdf", pages=1)
        (self.rendered_dir / "page_0001.png").write_bytes(b"png evidence")
        self.manifest_path = create_allowed_artifacts_manifest(
            self.video_dir,
            CRITERIA_PATH,
            [("tex", "main.tex"), ("pdf", "final.pdf")],
        )

    def _write_pdf(self, path: Path, *, pages: int) -> None:
        doc = fitz.open()
        for page_number in range(1, pages + 1):
            page = doc.new_page(width=300, height=300)
            page.insert_text((72, 72), f"Page {page_number}")
        doc.save(path)
        doc.close()

    def write_report(self, report: dict[str, object], name: str = "acceptance_report.json") -> Path:
        path = self.acceptance_dir / name
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def valid_report(self) -> dict[str, object]:
        criteria = load_criteria()
        criteria_items = criteria["criteria"]
        assert isinstance(criteria_items, list)
        return {
            "schema_version": "1.0",
            "criteria_version": criteria["criteria_version"],
            "criteria_file": "docs/acceptance/acceptance_criteria.v1.json",
            "overall_status": "pass",
            "decision_source": "acceptance_report_json",
            "review_context_used": {
                "allowed_artifacts_manifest": "review/acceptance/allowed_artifacts_manifest.json",
                "final_artifacts_only": True,
                "generation_process_used": False,
                "artifacts_read": [
                    "main.tex",
                    "final.pdf",
                    "docs/acceptance/acceptance_criteria.v1.json",
                ],
            },
            "artifact_fingerprints": [
                compute_artifact_fingerprint(self.video_dir / "main.tex", "main.tex"),
                compute_artifact_fingerprint(self.video_dir / "final.pdf", "final.pdf"),
            ],
            "criterion_results": [
                {
                    "criterion_id": item["id"],
                    "category": item["category"],
                    "status": "pass",
                    "evidence": [
                        {
                            "artifact_path": "main.tex" if item["category"] in TEXT_CATEGORIES | FORMULA_CATEGORIES else "final.pdf",
                            "location": "full artifact",
                            "summary": "No blocking defect detected.",
                        }
                    ],
                    "scan_evidence": (
                        {
                            "scan_policy": item["scan_policy"],
                            "scanned_artifacts": ["main.tex"],
                            "formulas_checked": [],
                            "no_body_formula_found": True,
                        }
                        if item["category"] in FORMULA_CATEGORIES
                        else {
                            "scan_policy": item["scan_policy"],
                            "scanned_artifacts": ["main.tex" if item["category"] in TEXT_CATEGORIES else "final.pdf"],
                        }
                    ),
                    "revision_guidance": None,
                }
                for item in criteria_items
            ],
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
            "failed_criteria": [],
            "revision_required": False,
        }

    def validate(self, report: dict[str, object]) -> list[str]:
        return validate_acceptance_report(
            self.write_report(report),
            criteria_path=CRITERIA_PATH,
            video_output_dir=self.video_dir,
            manifest_path=self.manifest_path,
            enforce_decision=True,
        )

    def test_fresh_passing_report_allows_delivery(self) -> None:
        warnings = self.validate(self.valid_report())

        self.assertEqual(warnings, [])
        self.assertEqual(validate_delivery_decision(self.video_dir, CRITERIA_PATH), [])

    def test_manifest_records_allowed_artifacts_and_forbidden_categories(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["criteria_file"], "docs/acceptance/acceptance_criteria.v1.json")
        self.assertEqual(manifest["review_output_dir"], "review/acceptance")
        self.assertEqual(
            manifest["final_artifacts"],
            [{"role": "tex", "path": "main.tex"}, {"role": "pdf", "path": "final.pdf"}],
        )
        self.assertIn("review/pyramid/", manifest["forbidden_artifacts"])

    def test_rejects_incoherent_decision_forbidden_context_and_stale_fingerprint(self) -> None:
        pass_with_failed_result = self.valid_report()
        first = deepcopy(pass_with_failed_result["criterion_results"][0])
        assert isinstance(first, dict)
        first["status"] = "fail"
        first["revision_guidance"] = {
            "required_change": "Remove the reader-facing defect.",
            "allowed_fix_types": ["rewrite"],
        }
        pass_with_failed_result["criterion_results"][0] = first
        pass_with_failed_result["failed_criteria"] = [first["criterion_id"]]
        pass_with_failed_result["revision_required"] = True
        with self.assertRaisesRegex(ValidationError, "overall_status pass conflicts with failed criteria"):
            self.validate(pass_with_failed_result)

        forbidden_context = self.valid_report()
        context = deepcopy(forbidden_context["review_context_used"])
        assert isinstance(context, dict)
        context["generation_process_used"] = True
        forbidden_context["review_context_used"] = context
        with self.assertRaisesRegex(ValidationError, "generation_process_used must be false"):
            self.validate(forbidden_context)

        stale = self.valid_report()
        (self.video_dir / "main.tex").write_text("Changed final article text.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "artifact_fingerprints entry is stale: main.tex"):
            self.validate(stale)

    def test_rejects_visual_page_gaps_and_evidence_outside_manifest(self) -> None:
        missing_page = self.valid_report()
        visual = deepcopy(missing_page["visual_scan_evidence"])
        assert isinstance(visual, dict)
        visual["pages_checked"] = []
        missing_page["visual_scan_evidence"] = visual
        with self.assertRaisesRegex(ValidationError, "visual_scan_evidence.pages_checked must cover every page exactly once"):
            self.validate(missing_page)

        outside_evidence = self.valid_report()
        result = deepcopy(outside_evidence["criterion_results"][0])
        assert isinstance(result, dict)
        result["evidence"] = [
            {
                "artifact_path": "work/internal_notes.md",
                "location": "line 1",
                "summary": "Forbidden evidence path.",
            }
        ]
        outside_evidence["criterion_results"][0] = result
        with self.assertRaisesRegex(ValidationError, "evidence path is outside allowed final artifacts"):
            self.validate(outside_evidence)

    def test_rejects_missing_malformed_missing_result_and_duplicate_result_reports(self) -> None:
        with self.assertRaisesRegex(GateBlockedError, "missing acceptance report blocks delivery"):
            validate_delivery_decision(self.video_dir, CRITERIA_PATH)

        malformed_path = self.acceptance_dir / "malformed.json"
        malformed_path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "acceptance report invalid JSON"):
            validate_acceptance_report(
                malformed_path,
                criteria_path=CRITERIA_PATH,
                video_output_dir=self.video_dir,
                manifest_path=self.manifest_path,
                enforce_decision=True,
            )

        missing_result = self.valid_report()
        missing_result["criterion_results"] = missing_result["criterion_results"][:-1]
        with self.assertRaisesRegex(ValidationError, "criterion_results missing configured criteria"):
            self.validate(missing_result)

        duplicate_result = self.valid_report()
        duplicate_result["criterion_results"] = duplicate_result["criterion_results"] + [
            deepcopy(duplicate_result["criterion_results"][0])
        ]
        with self.assertRaisesRegex(ValidationError, "criterion_results\\[\\d+\\].criterion_id is duplicated"):
            self.validate(duplicate_result)

        mismatch = self.valid_report()
        mismatch["failed_criteria"] = ["no_meta_writing_content"]
        with self.assertRaisesRegex(ValidationError, "failed_criteria must match failed criterion results"):
            self.validate(mismatch)

    def test_rejects_visual_page_count_duplicate_pages_and_failed_page_without_failure_details(self) -> None:
        wrong_count = self.valid_report()
        visual = deepcopy(wrong_count["visual_scan_evidence"])
        assert isinstance(visual, dict)
        visual["page_count"] = 2
        wrong_count["visual_scan_evidence"] = visual
        with self.assertRaisesRegex(ValidationError, "visual_scan_evidence.page_count disagrees with rendered PDF"):
            self.validate(wrong_count)

        duplicate_page = self.valid_report()
        visual = deepcopy(duplicate_page["visual_scan_evidence"])
        assert isinstance(visual, dict)
        visual["pages_checked"] = visual["pages_checked"] + [deepcopy(visual["pages_checked"][0])]
        duplicate_page["visual_scan_evidence"] = visual
        with self.assertRaisesRegex(ValidationError, "visual_scan_evidence.pages_checked must cover every page exactly once"):
            self.validate(duplicate_page)

        failed_page_without_failures = self.valid_report()
        visual = deepcopy(failed_page_without_failures["visual_scan_evidence"])
        assert isinstance(visual, dict)
        page_entry = deepcopy(visual["pages_checked"][0])
        assert isinstance(page_entry, dict)
        page_entry["status"] = "fail"
        page_entry["failures"] = []
        visual["pages_checked"] = [page_entry]
        failed_page_without_failures["visual_scan_evidence"] = visual
        with self.assertRaisesRegex(ValidationError, "failed page entry requires failures"):
            self.validate(failed_page_without_failures)

        visual_failure_without_page_failure = self.valid_report()
        visual_failure_without_page_failure["overall_status"] = "fail"
        visual_failure_without_page_failure["revision_required"] = True
        result = deepcopy(
            next(
                item
                for item in visual_failure_without_page_failure["criterion_results"]
                if isinstance(item, dict) and item.get("category") in VISUAL_CATEGORIES
            )
        )
        assert isinstance(result, dict)
        result["status"] = "fail"
        result["revision_guidance"] = {
            "required_change": "Repair the visible figure defect.",
            "allowed_fix_types": ["redraw"],
        }
        visual_failure_without_page_failure["criterion_results"] = [
            result
            if isinstance(item, dict) and item.get("criterion_id") == result["criterion_id"]
            else item
            for item in visual_failure_without_page_failure["criterion_results"]
        ]
        visual_failure_without_page_failure["failed_criteria"] = [result["criterion_id"]]
        with self.assertRaisesRegex(ValidationError, "failed visual criteria require page failure evidence"):
            self.validate(visual_failure_without_page_failure)

    def test_failed_report_requires_revision_guidance_and_blocks_delivery(self) -> None:
        failed = self.valid_report()
        failed["overall_status"] = "fail"
        failed["revision_required"] = True
        result = deepcopy(failed["criterion_results"][0])
        assert isinstance(result, dict)
        result["status"] = "fail"
        result["revision_guidance"] = None
        failed["criterion_results"][0] = result
        failed["failed_criteria"] = [result["criterion_id"]]
        with self.assertRaisesRegex(ValidationError, "failed criterion requires revision_guidance"):
            self.validate(failed)

        result["revision_guidance"] = {
            "required_change": "Remove the reader-facing defect.",
            "allowed_fix_types": ["rewrite"],
        }
        failed["criterion_results"][0] = result
        with self.assertRaisesRegex(GateBlockedError, "acceptance report status 'fail' blocks delivery"):
            self.validate(failed)

    def test_logic_readability_failure_does_not_require_page_failure(self) -> None:
        failed = self.valid_report()
        failed["overall_status"] = "fail"
        failed["revision_required"] = True
        result = deepcopy(
            next(
                item
                for item in failed["criterion_results"]
                if isinstance(item, dict) and item.get("criterion_id") == "argument_chain_integrity"
            )
        )
        assert isinstance(result, dict)
        result["status"] = "fail"
        result["evidence"] = [
            {
                "artifact_path": "main.tex",
                "location": "section 2 paragraph 3",
                "summary": "A structural label list lacks an explicit causal chain and evidence role.",
            }
        ]
        result["scan_evidence"] = {
            "scan_policy": "triggered_structural_expression_scan",
            "scanned_artifacts": ["main.tex"],
            "trigger_count": 1,
            "failed_trigger_count": 1,
        }
        result["revision_guidance"] = {
            "required_change": "Rewrite the structural label list into an explicit argument chain.",
            "allowed_fix_types": ["rewrite", "expand explanation"],
        }
        failed["criterion_results"] = [
            result
            if isinstance(item, dict) and item.get("criterion_id") == "argument_chain_integrity"
            else item
            for item in failed["criterion_results"]
        ]
        failed["failed_criteria"] = ["argument_chain_integrity"]

        with self.assertRaisesRegex(GateBlockedError, "acceptance report status 'fail' blocks delivery"):
            self.validate(failed)

    def test_rejects_missing_formula_scan_evidence_for_formula_gate(self) -> None:
        missing_scan = self.valid_report()
        result = deepcopy(
            next(
                item
                for item in missing_scan["criterion_results"]
                if isinstance(item, dict) and item.get("criterion_id") == "formula_information_gain"
            )
        )
        assert isinstance(result, dict)
        result["scan_evidence"] = {
            "scan_policy": "full_artifact_formula_scan",
            "scanned_artifacts": ["main.tex"],
        }
        missing_scan["criterion_results"] = [
            result
            if isinstance(item, dict) and item.get("criterion_id") == "formula_information_gain"
            else item
            for item in missing_scan["criterion_results"]
        ]

        with self.assertRaisesRegex(ValidationError, "formula scan evidence missing keys: formulas_checked, no_body_formula_found"):
            self.validate(missing_scan)

    def test_formula_gate_requires_each_body_formula_to_be_checked(self) -> None:
        report = self.valid_report()
        result = deepcopy(
            next(
                item
                for item in report["criterion_results"]
                if isinstance(item, dict) and item.get("criterion_id") == "formula_information_gain"
            )
        )
        assert isinstance(result, dict)
        result["scan_evidence"] = {
            "scan_policy": "full_artifact_formula_scan",
            "scanned_artifacts": ["main.tex"],
            "formulas_checked": [
                {
                    "location": "section 3 display equation",
                    "formula_excerpt": "Y = f(a, b, c)",
                    "source_type": "interpretive_teaching_model",
                    "status": "pass",
                    "information_gain_summary": "The formula identifies a veto factor and decision boundary.",
                }
            ],
            "no_body_formula_found": True,
        }
        report["criterion_results"] = [
            result
            if isinstance(item, dict) and item.get("criterion_id") == "formula_information_gain"
            else item
            for item in report["criterion_results"]
        ]

        with self.assertRaisesRegex(ValidationError, "no_body_formula_found must be false when formulas_checked is non-empty"):
            self.validate(report)

    def test_formula_failure_blocks_delivery_without_page_failure(self) -> None:
        failed = self.valid_report()
        failed["overall_status"] = "fail"
        failed["revision_required"] = True
        result = deepcopy(
            next(
                item
                for item in failed["criterion_results"]
                if isinstance(item, dict) and item.get("criterion_id") == "formula_information_gain"
            )
        )
        assert isinstance(result, dict)
        result["status"] = "fail"
        result["evidence"] = [
            {
                "artifact_path": "main.tex",
                "location": "section 3 display equation",
                "summary": "The formula repeats a prose list without adding a decision rule.",
            }
        ]
        result["scan_evidence"] = {
            "scan_policy": "full_artifact_formula_scan",
            "scanned_artifacts": ["main.tex"],
            "formulas_checked": [
                {
                    "location": "section 3 display equation",
                    "formula_excerpt": "Y = f(a, b, c)",
                    "source_type": "interpretive_teaching_model",
                    "status": "fail",
                    "information_gain_summary": "The formula only renames the adjacent prose list.",
                }
            ],
            "no_body_formula_found": False,
        }
        result["revision_guidance"] = {
            "required_change": "Replace the low-gain formula with prose, a list, or a table.",
            "allowed_fix_types": ["delete formula", "rewrite as prose", "replace with table"],
        }
        failed["criterion_results"] = [
            result
            if isinstance(item, dict) and item.get("criterion_id") == "formula_information_gain"
            else item
            for item in failed["criterion_results"]
        ]
        failed["failed_criteria"] = ["formula_information_gain"]

        with self.assertRaisesRegex(GateBlockedError, "acceptance report status 'fail' blocks delivery"):
            self.validate(failed)


if __name__ == "__main__":
    unittest.main()

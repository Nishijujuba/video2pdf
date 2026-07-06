#!/usr/bin/env python3
"""Tests for Final Delivery Guard target resolution and CLI checks."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

import fitz

from validate_acceptance_report import compute_artifact_fingerprint, create_allowed_artifacts_manifest


REPO_ROOT = Path(__file__).resolve().parents[4]
CRITERIA_PATH = REPO_ROOT / "docs" / "acceptance" / "acceptance_criteria.v1.json"
SCRIPT = REPO_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "scripts" / "delivery_guard.py"
WRAPPER_SCRIPT = REPO_ROOT / ".agents" / "skills" / "bilibili-render-pdf" / "scripts" / "compile_latex_ascii.py"


def load_criteria() -> dict[str, object]:
    return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))


class DeliveryGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case_dir = REPO_ROOT / "待删除" / "delivery-guard-tests" / f"{self._testMethodName}-{uuid.uuid4().hex}"
        self.video_dir = self.case_dir / "video"
        self.acceptance_dir = self.video_dir / "review" / "acceptance"
        self.rendered_dir = self.acceptance_dir / "rendered_pages"
        self.rendered_dir.mkdir(parents=True, exist_ok=True)
        (self.video_dir / "待删除").mkdir(parents=True, exist_ok=True)
        (self.video_dir / "main.tex").write_text("Final article text.\n", encoding="utf-8")
        self.write_pdf(self.video_dir / "final.pdf", pages=1)
        (self.rendered_dir / "page_0001.png").write_bytes(b"png evidence")
        self.manifest_path = create_allowed_artifacts_manifest(
            self.video_dir,
            CRITERIA_PATH,
            [("tex", "main.tex"), ("pdf", "final.pdf")],
        )
        self.compile_report_path = self.video_dir / "review" / "latex" / "compile_report.json"
        self.report_path = self.acceptance_dir / "acceptance_report.json"
        self.target_path = self.acceptance_dir / "delivery_target.json"
        self.current_target_path = self.case_dir / ".codex" / "delivery-targets" / "current.json"
        self.current_target_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_compile_report(self.valid_compile_report())
        self.write_report(self.valid_report())
        self.write_delivery_target()
        self.write_current_target()

    def write_pdf(self, path: Path, *, pages: int) -> None:
        doc = fitz.open()
        for page_number in range(1, pages + 1):
            page = doc.new_page(width=300, height=300)
            page.insert_text((72, 72), f"Page {page_number}")
        doc.save(path)
        doc.close()

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
                            "artifact_path": "main.tex" if item["category"] == "style" else "final.pdf",
                            "location": "full artifact",
                            "summary": "No blocking defect detected.",
                        }
                    ],
                    "scan_evidence": {
                        "scan_policy": item["scan_policy"],
                        "scanned_artifacts": ["main.tex" if item["category"] == "style" else "final.pdf"],
                    },
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

    def write_report(self, report: dict[str, object]) -> None:
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def compile_fingerprint(self, path: Path) -> dict[str, object]:
        raw = path.read_bytes()
        return {
            "algorithm": "sha256",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }

    def valid_compile_report(self, *, final_pdf_name: str = "final.pdf") -> dict[str, object]:
        source_tex = self.video_dir / "main.tex"
        final_pdf = self.video_dir / final_pdf_name
        return {
            "schema_version": "latex_compile_report.v1",
            "mode": "final",
            "status": "passed",
            "producer": "compile_latex_ascii.py",
            "producer_contract": "latex_compile_guard.v1",
            "producer_mode": "final",
            "wrapper_script": str(WRAPPER_SCRIPT.resolve()),
            "wrapper_script_fingerprint": self.compile_fingerprint(WRAPPER_SCRIPT),
            "argv": [
                "--tex",
                str(source_tex.resolve()),
                "--mode",
                "final",
                "--engine",
                "fake-xelatex",
                "--final-pdf",
                str(final_pdf.resolve()),
            ],
            "source_tex": str(source_tex.resolve()),
            "main_tex": str(source_tex.resolve()),
            "final_pdf": str(final_pdf.resolve()),
            "source_tex_fingerprint": self.compile_fingerprint(source_tex),
            "final_pdf_fingerprint": self.compile_fingerprint(final_pdf),
            "build_directory": str((self.video_dir / "待删除" / "latex-build" / "run").resolve()),
            "log_paths": [],
            "source_skill": "test-fixture",
        }

    def handwritten_compile_report_without_wrapper_producer(self) -> dict[str, object]:
        report = self.valid_compile_report()
        for key in ("producer", "producer_contract", "producer_mode", "wrapper_script", "wrapper_script_fingerprint", "argv"):
            del report[key]
        return report

    def write_compile_report(self, report: dict[str, object]) -> None:
        self.compile_report_path.parent.mkdir(parents=True, exist_ok=True)
        self.compile_report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def failed_report(self) -> dict[str, object]:
        report = self.valid_report()
        report["overall_status"] = "fail"
        report["revision_required"] = True
        first_result = dict(report["criterion_results"][0])
        first_result["status"] = "fail"
        first_result["evidence"] = [
            {
                "artifact_path": "main.tex",
                "location": "paragraph 1",
                "summary": "Meta writing content remains in the final artifact.",
            }
        ]
        first_result["revision_guidance"] = {
            "required_change": "Remove meta writing process language from the final article.",
            "allowed_fix_types": ["rewrite"],
        }
        report["criterion_results"][0] = first_result
        report["failed_criteria"] = [first_result["criterion_id"]]
        return report

    def write_delivery_target(
        self,
        *,
        stage: str = "accepted",
        final_pdf: str = "final.pdf",
        compile_report: str | None = "review/latex/compile_report.json",
        compile_provenance_required: bool | None = None,
        legacy_existing_pdf: bool | None = None,
        recompiled: bool | None = None,
    ) -> None:
        target = {
            "schema_version": "1.0",
            "stage": stage,
            "video_output_dir": ".",
            "final_pdf": final_pdf,
            "main_tex": "main.tex",
            "allowed_artifacts_manifest": "review/acceptance/allowed_artifacts_manifest.json",
            "acceptance_report": "review/acceptance/acceptance_report.json",
            "delivery_guard_report": "review/acceptance/delivery_guard_report.json",
            "attempt_limit": 3,
        }
        if compile_report is not None:
            target["compile_report"] = compile_report
        if compile_provenance_required is not None:
            target["compile_provenance_required"] = compile_provenance_required
        if legacy_existing_pdf is not None:
            target["legacy_existing_pdf"] = legacy_existing_pdf
        if recompiled is not None:
            target["recompiled"] = recompiled
        self.target_path.write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_current_target(self, *, stage: str = "accepted", video_output_dir: str | None = None) -> None:
        rel_video = video_output_dir or self.video_dir.relative_to(REPO_ROOT).as_posix()
        current = {
            "schema_version": "1.0",
            "stage": stage,
            "video_output_dir": rel_video,
            "target_file": self.target_path.relative_to(REPO_ROOT).as_posix(),
            "source_skill": "test-fixture",
            "updated_at": "2026-07-05T12:00:00+08:00",
        }
        self.current_target_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_guard(self, *extra: str, current_target: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(SCRIPT),
                *extra,
                "--project-root",
                str(REPO_ROOT),
                "--current-target",
                str(current_target or self.current_target_path),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_check_writes_fresh_passing_guard_report(self) -> None:
        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((self.acceptance_dir / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["stage"], "accepted")
        self.assertEqual(report["final_pdf"], "final.pdf")
        self.assertEqual(report["validated_by"], "delivery_guard.py")
        self.assertEqual(report["acceptance_report_status"], "pass")
        self.assertIsNone(report["blocking_message"])
        fingerprint_paths = {item["path"] for item in report["artifact_fingerprints"]}
        self.assertIn("final.pdf", fingerprint_paths)
        self.assertIn("review/latex/compile_report.json", fingerprint_paths)
        self.assertIn("acceptance_report_enforced", {item["condition"] for item in report["checked_conditions"]})

    def test_check_rejects_missing_final_compile_report_for_new_video_target(self) -> None:
        self.write_delivery_target(compile_report="review/latex/missing_compile_report.json")

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report is missing", completed.stderr)
        self.assertIn("review/latex/missing_compile_report.json", completed.stderr)

    def test_check_rejects_quick_mode_compile_report(self) -> None:
        report = self.valid_compile_report()
        report["mode"] = "quick"
        self.write_compile_report(report)

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report mode must be 'final'", completed.stderr)
        self.assertIn("quick", completed.stderr)

    def test_check_rejects_failed_final_compile_report(self) -> None:
        report = self.valid_compile_report()
        report["status"] = "failed"
        self.write_compile_report(report)

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report status must be 'passed'", completed.stderr)
        self.assertIn("failed", completed.stderr)

    def test_check_rejects_handwritten_compile_report_without_wrapper_producer(self) -> None:
        self.write_compile_report(self.handwritten_compile_report_without_wrapper_producer())

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("producer", completed.stderr)

    def test_check_rejects_compile_report_with_stale_wrapper_fingerprint(self) -> None:
        report = self.valid_compile_report()
        report["wrapper_script_fingerprint"] = {
            "algorithm": "sha256",
            "sha256": "0" * 64,
            "size_bytes": 1,
        }
        self.write_compile_report(report)

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("wrapper_script_fingerprint is stale", completed.stderr)

    def test_check_rejects_compile_report_without_final_mode_argv(self) -> None:
        report = self.valid_compile_report()
        report["argv"] = ["--tex", str((self.video_dir / "main.tex").resolve()), "--mode", "quick"]
        self.write_compile_report(report)

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("argv must include --mode final", completed.stderr)

    def test_check_rejects_malformed_final_compile_report(self) -> None:
        report = self.valid_compile_report()
        del report["final_pdf"]
        self.write_compile_report(report)

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("malformed final compile report", completed.stderr)
        self.assertIn("final_pdf", completed.stderr)

    def test_check_rejects_final_compile_report_with_missing_or_wrong_schema(self) -> None:
        for label, schema_value in (("missing", None), ("wrong", "1.0")):
            with self.subTest(label=label):
                report = self.valid_compile_report()
                if schema_value is None:
                    del report["schema_version"]
                else:
                    report["schema_version"] = schema_value
                self.write_compile_report(report)

                completed = self.run_guard("check")

                self.assertEqual(completed.returncode, 2)
                self.assertIn("schema_version", completed.stderr)

    def test_check_rejects_compile_report_for_wrong_pdf(self) -> None:
        wrong_pdf = self.video_dir / "wrong.pdf"
        self.write_pdf(wrong_pdf, pages=1)
        report = self.valid_compile_report()
        report["final_pdf"] = str(wrong_pdf.resolve())
        report["final_pdf_fingerprint"] = self.compile_fingerprint(wrong_pdf)
        self.write_compile_report(report)

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report final_pdf does not match delivery_target.final_pdf", completed.stderr)

    def test_check_rejects_compile_report_for_wrong_tex(self) -> None:
        wrong_tex = self.video_dir / "wrong.tex"
        wrong_tex.write_text("Wrong TeX source.\n", encoding="utf-8")
        report = self.valid_compile_report()
        report["source_tex"] = str(wrong_tex.resolve())
        report["main_tex"] = str(wrong_tex.resolve())
        report["source_tex_fingerprint"] = self.compile_fingerprint(wrong_tex)
        self.write_compile_report(report)

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report source_tex does not match delivery_target.main_tex", completed.stderr)

    def test_check_rejects_stale_compile_report_pdf_fingerprint(self) -> None:
        with (self.video_dir / "final.pdf").open("ab") as handle:
            handle.write(b"\nchanged after final compile report")

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report final_pdf_fingerprint is stale", completed.stderr)

    def test_check_rejects_stale_compile_report_tex_fingerprint(self) -> None:
        (self.video_dir / "main.tex").write_text("Changed after final compile report.\n", encoding="utf-8")

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report source_tex_fingerprint is stale", completed.stderr)

    def test_legacy_existing_pdf_can_explicitly_skip_compile_provenance(self) -> None:
        self.write_delivery_target(
            compile_report="review/latex/missing_legacy_compile_report.json",
            compile_provenance_required=False,
            legacy_existing_pdf=True,
            recompiled=False,
        )

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((self.acceptance_dir / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "pass")

    def test_new_video_target_cannot_disable_compile_provenance(self) -> None:
        self.write_delivery_target(
            compile_report="review/latex/missing_compile_report.json",
            compile_provenance_required=False,
        )

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("compile_provenance_required may be false only for legacy_existing_pdf", completed.stderr)

    def test_legacy_skip_requires_explicit_no_recompile_claim(self) -> None:
        self.write_delivery_target(
            compile_report="review/latex/missing_legacy_compile_report.json",
            compile_provenance_required=False,
            legacy_existing_pdf=True,
        )

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("recompiled is explicitly false", completed.stderr)

    def test_legacy_recompile_target_requires_compile_provenance(self) -> None:
        self.write_delivery_target(
            compile_report="review/latex/missing_recompile_compile_report.json",
            compile_provenance_required=True,
            legacy_existing_pdf=True,
            recompiled=True,
        )

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report is missing", completed.stderr)
        self.assertIn("missing_recompile_compile_report.json", completed.stderr)

    def test_check_rejects_stale_acceptance_report(self) -> None:
        (self.video_dir / "main.tex").write_text("Changed after acceptance.\n", encoding="utf-8")
        self.write_compile_report(self.valid_compile_report())

        completed = self.run_guard("check")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("artifact_fingerprints entry is stale: main.tex", completed.stderr)
        report = json.loads((self.acceptance_dir / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "fail")
        self.assertIn("Final Delivery Guard blocked delivery", report["blocking_message"])

    def test_check_rejects_manifest_mismatch_and_missing_rendered_page_coverage(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["final_artifacts"] = [{"role": "tex", "path": "main.tex"}]
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest_mismatch = self.run_guard("check")

        self.assertEqual(manifest_mismatch.returncode, 2)
        self.assertIn("final PDF is absent from allowed_artifacts_manifest.json", manifest_mismatch.stderr)

        two_page_pdf = self.video_dir / "final-two-page.pdf"
        self.write_pdf(two_page_pdf, pages=2)
        self.write_delivery_target(final_pdf="final-two-page.pdf")
        self.manifest_path = create_allowed_artifacts_manifest(
            self.video_dir,
            CRITERIA_PATH,
            [("tex", "main.tex"), ("pdf", "final-two-page.pdf")],
        )
        self.write_compile_report(self.valid_compile_report(final_pdf_name="final-two-page.pdf"))
        missing_page_report = self.valid_report()
        context = dict(missing_page_report["review_context_used"])
        context["artifacts_read"] = [
            "main.tex",
            "final-two-page.pdf",
            "docs/acceptance/acceptance_criteria.v1.json",
        ]
        missing_page_report["review_context_used"] = context
        missing_page_report["artifact_fingerprints"] = [
            compute_artifact_fingerprint(self.video_dir / "main.tex", "main.tex"),
            compute_artifact_fingerprint(two_page_pdf, "final-two-page.pdf"),
        ]
        criterion_results = []
        for result in missing_page_report["criterion_results"]:
            result = dict(result)
            if result["category"] != "style":
                result["evidence"] = [
                    {
                        "artifact_path": "final-two-page.pdf",
                        "location": "full artifact",
                        "summary": "No blocking defect detected.",
                    }
                ]
                result["scan_evidence"] = {
                    "scan_policy": result["scan_evidence"]["scan_policy"],
                    "scanned_artifacts": ["final-two-page.pdf"],
                }
            criterion_results.append(result)
        missing_page_report["criterion_results"] = criterion_results
        visual = dict(missing_page_report["visual_scan_evidence"])
        visual["pdf"] = "final-two-page.pdf"
        visual["page_count"] = 2
        visual["pages_checked"] = visual["pages_checked"][:1]
        missing_page_report["visual_scan_evidence"] = visual
        self.write_report(missing_page_report)

        missing_page = self.run_guard("check")

        self.assertEqual(missing_page.returncode, 2)
        self.assertIn("visual_scan_evidence.pages_checked must cover every page exactly once", missing_page.stderr)

    def test_resolver_rejects_invalid_stage_and_path_escape(self) -> None:
        self.write_current_target(stage="almost_ready")

        invalid_stage = self.run_guard("check")

        self.assertEqual(invalid_stage.returncode, 2)
        self.assertIn("current target stage is invalid", invalid_stage.stderr)

        self.write_current_target(video_output_dir="../outside")
        path_escape = self.run_guard("check")

        self.assertEqual(path_escape.returncode, 2)
        self.assertIn("current target video_output_dir", path_escape.stderr)

    def test_hook_stop_runs_guard_once_then_reuses_fresh_report(self) -> None:
        first = self.run_guard("hook-stop")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("PASS:", first.stdout)
        self.assertTrue((self.acceptance_dir / "delivery_guard_report.json").exists())

        second = self.run_guard("hook-stop")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("fresh passing guard report", second.stdout)

    def test_successful_fixture_workflow_reaches_guard_and_clears_current_target(self) -> None:
        guarded = self.run_guard("hook-stop")
        self.assertEqual(guarded.returncode, 0, guarded.stderr)

        cleared = self.run_guard("clear-target", "--video-output-dir", str(self.video_dir))

        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        archived_targets = list((self.video_dir / "待删除" / "delivery-targets").glob("current-*.json"))
        self.assertTrue(archived_targets)
        archived = json.loads(archived_targets[0].read_text(encoding="utf-8"))
        self.assertEqual(archived["stage"], "delivered")
        if self.current_target_path.exists():
            active = json.loads(self.current_target_path.read_text(encoding="utf-8"))
            self.assertEqual(active["stage"], "delivered")

    def test_hook_stop_allows_missing_target_and_generating_stage(self) -> None:
        missing_target = self.case_dir / ".codex" / "delivery-targets" / "missing-current.json"

        no_target = self.run_guard("hook-stop", current_target=missing_target)

        self.assertEqual(no_target.returncode, 0, no_target.stderr)
        self.assertIn("No active delivery target", no_target.stdout)

        self.write_delivery_target(stage="generating")
        self.write_current_target(stage="generating")
        generating = self.run_guard("hook-stop")

        self.assertEqual(generating.returncode, 0, generating.stderr)
        self.assertIn("stage generating", generating.stdout)
        self.assertFalse((self.acceptance_dir / "delivery_guard_report.json").exists())

    def test_hook_stop_blocks_blocked_stage_with_recovery_instruction(self) -> None:
        self.write_delivery_target(stage="blocked")
        self.write_current_target(stage="blocked")

        completed = self.run_guard("hook-stop")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("Final Delivery Guard blocked delivery", completed.stderr)
        self.assertIn("Use a separate Acceptance Reviewer subagent and repair subagents", completed.stderr)
        self.assertIn("manual_repair_brief.md", completed.stderr)

    def test_old_pdf_prepare_infers_video_dir_and_rejects_isolated_pdf_without_boundary(self) -> None:
        completed = self.run_guard("old-pdf-prepare", str(self.video_dir / "final.pdf"))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        prepared_target = json.loads(self.target_path.read_text(encoding="utf-8"))
        active_target = json.loads(self.current_target_path.read_text(encoding="utf-8"))
        self.assertEqual(prepared_target["stage"], "ready_for_delivery")
        self.assertEqual(prepared_target["video_output_dir"], ".")
        self.assertEqual(prepared_target["final_pdf"], "final.pdf")
        self.assertEqual(prepared_target["attempt_limit"], 3)
        self.assertIs(prepared_target["compile_provenance_required"], False)
        self.assertIs(prepared_target["legacy_existing_pdf"], True)
        self.assertIs(prepared_target["recompiled"], False)
        self.assertEqual(active_target["stage"], "ready_for_delivery")
        self.assertEqual(active_target["target_file"], self.target_path.relative_to(REPO_ROOT).as_posix())

        isolated_pdf = self.case_dir / "isolated.pdf"
        self.write_pdf(isolated_pdf, pages=1)
        isolated = self.run_guard("old-pdf-prepare", str(isolated_pdf))

        self.assertEqual(isolated.returncode, 2)
        self.assertIn("requires an explicit video_output_dir", isolated.stderr)

        outside_pdf = REPO_ROOT.parent / "outside.pdf"
        escaped = self.run_guard("old-pdf-prepare", str(outside_pdf))

        self.assertEqual(escaped.returncode, 2)
        self.assertIn("pdf escapes project boundary", escaped.stderr)

    def test_record_failed_attempt_preserves_evidence_and_blocks_after_third_attempt(self) -> None:
        self.write_delivery_target(stage="ready_for_delivery")
        self.write_current_target(stage="ready_for_delivery")
        self.write_report(self.failed_report())
        (self.acceptance_dir / "acceptance_summary.md").write_text("Failed acceptance summary.\n", encoding="utf-8")

        first = self.run_guard(
            "record-failed-attempt",
            "--video-output-dir",
            str(self.video_dir),
            "--attempt-number",
            "1",
            "--changed-file",
            "main.tex",
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        attempt_dir = self.acceptance_dir / "attempts" / "attempt_01"
        self.assertTrue((attempt_dir / "acceptance_report.json").exists())
        self.assertTrue((attempt_dir / "acceptance_summary.md").exists())
        self.assertTrue((attempt_dir / "repair_brief.md").exists())
        self.assertTrue((attempt_dir / "changed_files.json").exists())
        repair_brief = (attempt_dir / "repair_brief.md").read_text(encoding="utf-8")
        self.assertIn("no_meta_writing_content", repair_brief)
        self.assertIn("visual_scan_evidence", repair_brief)
        self.assertIn("Remove meta writing process language", repair_brief)

        for attempt_number in (2, 3):
            completed = self.run_guard(
                "record-failed-attempt",
                "--video-output-dir",
                str(self.video_dir),
                "--attempt-number",
                str(attempt_number),
                "--changed-file",
                "main.tex",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

        manual_brief = self.acceptance_dir / "manual_repair_brief.md"
        self.assertTrue(manual_brief.exists())
        self.assertIn("attempt_03", manual_brief.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.target_path.read_text(encoding="utf-8"))["stage"], "blocked")
        self.assertEqual(json.loads(self.current_target_path.read_text(encoding="utf-8"))["stage"], "blocked")


if __name__ == "__main__":
    unittest.main()

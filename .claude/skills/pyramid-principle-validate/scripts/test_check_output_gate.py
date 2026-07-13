#!/usr/bin/env python3
"""Targeted tests for check_output_gate.py."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4


SCRIPT_PATH = Path(__file__).resolve().with_name("check_output_gate.py")


def load_checker_module() -> Any:
    spec = importlib.util.spec_from_file_location("check_output_gate_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load check_output_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckOutputGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_dir = Path.cwd() / "待删除" / "pyramid-output-gate-test-runs" / uuid4().hex
        self.output_dir = self.run_dir / "video-output"
        self.review_dir = self.output_dir / "review" / "pyramid"
        self.review_dir.mkdir(parents=True, exist_ok=True)

    def write_artifact(self, relative_path: str, text: str) -> Path:
        path = self.output_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_report(
        self,
        report_name: str,
        *,
        artifact_path: Path,
        artifact_type: str,
        context_label: str,
        status: str = "pass",
        score: float = 0.9,
        required_revisions: list[str] | None = None,
        waiver: dict[str, object] | None = None,
    ) -> Path:
        raw = artifact_path.read_bytes()
        text = raw.decode("utf-8")
        report = {
            "target": str(artifact_path),
            "artifact_type": artifact_type,
            "context_label": context_label,
            "status": status,
            "score": score,
            "dimensions": {
                "top_down_clarity": score,
                "support_hierarchy": score,
                "grouping_mece": score,
                "teaching_progression": score,
                "title_body_alignment": score,
            },
            "findings": [
                {
                    "severity": "minor",
                    "location": context_label,
                    "issue": "The checkpoint has a clear controlling claim.",
                    "recommendation": "Keep the current hierarchy visible in the next workflow step.",
                }
            ],
            "required_revisions": required_revisions or [],
            "waiver": waiver
            or {
                "state": "none",
                "approved_by": None,
                "reason": None,
                "approved_at": None,
            },
            "audit": {
                "standard_name": "Pyramid Principle Text Standard",
                "backend": "codex-exec",
                "prompt_version": "pyramid-principle-text-v1",
                "input_sha256": hashlib.sha256(raw).hexdigest(),
                "input_size_chars": len(text),
                "max_input_size_chars": 160000,
                "large_input_approval_state": "not_required",
                "evaluation_context": f"Teaching-PDF {context_label} checkpoint.",
                "generated_at": "2026-06-30T09:15:00Z",
            },
        }
        path = self.review_dir / report_name
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def write_default_artifacts(self) -> dict[str, Path]:
        return {
            "outline": self.write_artifact(
            "outline_contract.md",
            "# Teaching claim\n\nThe outline starts with the controlling claim.\n",
            ),
            "section_01": self.write_artifact(
            "section_01.tex",
            "\\section{Core idea}\nThe section states a parent claim before examples.\n",
            ),
            "section_02": self.write_artifact(
            "section_02.tex",
            "\\section{Practice}\nThe section groups examples under one teaching purpose.\n",
            ),
            "main": self.write_artifact(
            "main.tex",
            "\\documentclass{article}\n\\begin{document}\nThe main document integrates the teaching claim.\n\\end{document}\n",
            ),
        }

    def write_complete_passing_evidence(self) -> None:
        artifacts = self.write_default_artifacts()
        self.write_report(
            "outline.pyramid.json",
            artifact_path=artifacts["outline"],
            artifact_type="outline_contract",
            context_label="outline",
        )
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
        )
        self.write_report(
            "section_02.pyramid.json",
            artifact_path=artifacts["section_02"],
            artifact_type="tex_section",
            context_label="section_02",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

    def test_complete_evidence_validates_and_writes_human_summary(self) -> None:
        checker = load_checker_module()
        self.write_complete_passing_evidence()

        reports = checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

        self.assertEqual([path.name for path in reports], [
            "outline.pyramid.json",
            "section_01.pyramid.json",
            "section_02.pyramid.json",
            "main.pyramid.json",
        ])
        summary_path = self.review_dir / "summary.md"
        self.assertTrue(summary_path.exists(), "output gate should write review/pyramid/summary.md")
        summary = summary_path.read_text(encoding="utf-8")
        self.assertIn("| Checkpoint | Report | Status | Score | Required revisions | Waiver reason |", summary)
        self.assertIn("| outline | outline.pyramid.json | pass | 0.90 | None | None |", summary)
        self.assertIn("| section_01 | section_01.pyramid.json | pass | 0.90 | None | None |", summary)
        self.assertIn("| section_02 | section_02.pyramid.json | pass | 0.90 | None | None |", summary)
        self.assertIn("| main | main.pyramid.json | pass | 0.90 | None | None |", summary)

    def test_stale_section_report_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        self.write_complete_passing_evidence()
        (self.output_dir / "section_01.tex").write_text(
            "\\section{Changed}\nThis text no longer matches the report fingerprint.\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(checker.ValidationError, "audit.input_sha256 does not match"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_stale_section_report_cli_returns_validation_failure_exit_code(self) -> None:
        self.write_complete_passing_evidence()
        (self.output_dir / "section_01.tex").write_text(
            "\\section{Changed}\nThis text no longer matches the report fingerprint.\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(SCRIPT_PATH),
                str(self.output_dir),
                "--enforce-gate",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("INVALID", result.stderr)

    def test_checkpoint_metadata_mismatch_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        self.write_complete_passing_evidence()
        report_path = self.review_dir / "outline.pyramid.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["context_label"] = "section_01"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(checker.ValidationError, "context_label must be 'outline'"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_target_path_mismatch_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        self.write_complete_passing_evidence()
        report_path = self.review_dir / "section_01.pyramid.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["target"] = str(self.run_dir / "other-output" / "section_01.tex")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(checker.ValidationError, "target must resolve"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_missing_outline_report_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        artifacts = self.write_default_artifacts()
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

        with self.assertRaisesRegex(checker.ValidationError, "missing required pyramid reports"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_missing_section_report_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        artifacts = self.write_default_artifacts()
        self.write_report(
            "outline.pyramid.json",
            artifact_path=artifacts["outline"],
            artifact_type="outline_contract",
            context_label="outline",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

        with self.assertRaisesRegex(checker.ValidationError, "missing section pyramid reports"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_missing_one_section_report_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        artifacts = self.write_default_artifacts()
        self.write_report(
            "outline.pyramid.json",
            artifact_path=artifacts["outline"],
            artifact_type="outline_contract",
            context_label="outline",
        )
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

        with self.assertRaisesRegex(checker.ValidationError, "section_02.pyramid.json"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_orphan_section_report_without_source_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        self.write_complete_passing_evidence()
        artifact = self.output_dir / "section_01.tex"
        self.write_report(
            "section_99.pyramid.json",
            artifact_path=artifact,
            artifact_type="tex_section",
            context_label="section_99",
        )

        with self.assertRaisesRegex(checker.ValidationError, "section_99.tex"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_missing_main_report_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        artifacts = self.write_default_artifacts()
        self.write_report(
            "outline.pyramid.json",
            artifact_path=artifacts["outline"],
            artifact_type="outline_contract",
            context_label="outline",
        )
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
        )

        with self.assertRaisesRegex(checker.ValidationError, "missing required pyramid reports"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_failing_report_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        artifacts = self.write_default_artifacts()
        self.write_report(
            "outline.pyramid.json",
            artifact_path=artifacts["outline"],
            artifact_type="outline_contract",
            context_label="outline",
        )
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
            status="needs_revision",
            score=0.72,
            required_revisions=["Move the controlling claim before source details."],
        )
        self.write_report(
            "section_02.pyramid.json",
            artifact_path=artifacts["section_02"],
            artifact_type="tex_section",
            context_label="section_02",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

        with self.assertRaisesRegex(checker.ValidationError, "gate status 'needs_revision' blocks"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_failing_report_cli_returns_gate_blocked_exit_code(self) -> None:
        artifacts = self.write_default_artifacts()
        self.write_report(
            "outline.pyramid.json",
            artifact_path=artifacts["outline"],
            artifact_type="outline_contract",
            context_label="outline",
        )
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
            status="needs_revision",
            score=0.72,
            required_revisions=["Move the controlling claim before source details."],
        )
        self.write_report(
            "section_02.pyramid.json",
            artifact_path=artifacts["section_02"],
            artifact_type="tex_section",
            context_label="section_02",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(SCRIPT_PATH),
                str(self.output_dir),
                "--enforce-gate",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("GATE_BLOCKED", result.stderr)

    def test_malformed_report_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        artifacts = self.write_default_artifacts()
        (self.review_dir / "outline.pyramid.json").write_text("{ this is not json", encoding="utf-8")
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
        )
        self.write_report(
            "section_02.pyramid.json",
            artifact_path=artifacts["section_02"],
            artifact_type="tex_section",
            context_label="section_02",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

        with self.assertRaisesRegex(checker.ValidationError, "invalid JSON"):
            checker.check_output_dir(self.output_dir, enforce_gate=True, allow_no_sections=False)

    def test_missing_waiver_evidence_blocks_output_validation(self) -> None:
        checker = load_checker_module()
        artifacts = self.write_default_artifacts()
        self.write_report(
            "outline.pyramid.json",
            artifact_path=artifacts["outline"],
            artifact_type="outline_contract",
            context_label="outline",
        )
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
            status="needs_revision",
            score=0.72,
            required_revisions=["Preserve the dialogue order under an explicit parent claim."],
            waiver={
                "state": "approved",
                "approved_by": "workflow-owner",
                "reason": None,
                "approved_at": "2026-06-30T10:00:00Z",
            },
        )
        self.write_report(
            "section_02.pyramid.json",
            artifact_path=artifacts["section_02"],
            artifact_type="tex_section",
            context_label="section_02",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

        with self.assertRaisesRegex(checker.ValidationError, "waiver.reason must be a non-empty string"):
            checker.check_output_dir(
                self.output_dir,
                enforce_gate=True,
                allow_no_sections=False,
                allow_waivers=True,
            )

    def test_missing_waiver_evidence_cli_returns_waiver_invalid_exit_code(self) -> None:
        artifacts = self.write_default_artifacts()
        self.write_report(
            "outline.pyramid.json",
            artifact_path=artifacts["outline"],
            artifact_type="outline_contract",
            context_label="outline",
        )
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
            status="needs_revision",
            score=0.72,
            required_revisions=["Preserve the dialogue order under an explicit parent claim."],
            waiver={
                "state": "approved",
                "approved_by": "workflow-owner",
                "reason": None,
                "approved_at": "2026-06-30T10:00:00Z",
            },
        )
        self.write_report(
            "section_02.pyramid.json",
            artifact_path=artifacts["section_02"],
            artifact_type="tex_section",
            context_label="section_02",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(SCRIPT_PATH),
                str(self.output_dir),
                "--enforce-gate",
                "--allow-waivers",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("WAIVER_INVALID", result.stderr)

    def test_valid_waiver_allows_output_validation_and_is_recorded_in_summary(self) -> None:
        checker = load_checker_module()
        artifacts = self.write_default_artifacts()
        self.write_report(
            "outline.pyramid.json",
            artifact_path=artifacts["outline"],
            artifact_type="outline_contract",
            context_label="outline",
        )
        self.write_report(
            "section_01.pyramid.json",
            artifact_path=artifacts["section_01"],
            artifact_type="tex_section",
            context_label="section_01",
            status="needs_revision",
            score=0.72,
            required_revisions=["Preserve the dialogue order under an explicit parent claim."],
            waiver={
                "state": "approved",
                "approved_by": "workflow-owner",
                "reason": "The user approved preserving a dialogue-heavy source structure.",
                "approved_at": "2026-06-30T10:00:00Z",
            },
        )
        self.write_report(
            "section_02.pyramid.json",
            artifact_path=artifacts["section_02"],
            artifact_type="tex_section",
            context_label="section_02",
        )
        self.write_report(
            "main.pyramid.json",
            artifact_path=artifacts["main"],
            artifact_type="tex_document",
            context_label="main",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(SCRIPT_PATH),
                str(self.output_dir),
                "--enforce-gate",
                "--allow-waivers",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        summary = (self.review_dir / "summary.md").read_text(encoding="utf-8")
        self.assertIn(
            "| section_01 | section_01.pyramid.json | needs_revision | 0.72 | "
            "Preserve the dialogue order under an explicit parent claim. | "
            "The user approved preserving a dialogue-heavy source structure. |",
            summary,
        )


if __name__ == "__main__":
    unittest.main()

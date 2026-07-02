#!/usr/bin/env python3
"""Targeted tests for validate_report.py."""

from __future__ import annotations

import json
import hashlib
import math
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path

from validate_report import ValidationError, validate_report


def run_cli(args: list[str | Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def generalized_report() -> dict[str, object]:
    return {
        "target": "outline_contract.md",
        "artifact_type": "outline_contract",
        "context_label": "outline",
        "status": "pass",
        "score": 0.9,
        "dimensions": {
            "top_down_clarity": 0.9,
            "support_hierarchy": 0.9,
            "grouping_mece": 0.9,
            "teaching_progression": 0.9,
            "title_body_alignment": 0.9,
        },
        "findings": [
            {
                "severity": "minor",
                "location": "opening",
                "issue": "The central teaching claim is present and mostly clear.",
                "recommendation": "Tighten the first sentence so the reader sees the controlling claim immediately.",
            }
        ],
        "required_revisions": [],
        "waiver": {
            "state": "none",
            "approved_by": None,
            "reason": None,
            "approved_at": None,
        },
        "audit": {
            "standard_name": "Pyramid Principle Text Standard",
            "backend": "codex-exec",
            "prompt_version": "pyramid-principle-text-v1",
            "input_sha256": "a" * 64,
            "input_size_chars": 1200,
            "max_input_size_chars": 160000,
            "large_input_approval_state": "not_required",
            "evaluation_context": "Teaching-PDF outline checkpoint supplied by the video workflow.",
            "generated_at": "2026-06-30T09:15:00Z",
        },
    }


def needs_revision_report() -> dict[str, object]:
    report = generalized_report()
    report["status"] = "needs_revision"
    report["score"] = 0.72
    report["dimensions"] = {
        "top_down_clarity": 0.72,
        "support_hierarchy": 0.72,
        "grouping_mece": 0.72,
        "teaching_progression": 0.72,
        "title_body_alignment": 0.72,
    }
    report["required_revisions"] = ["State the controlling claim before listing source details."]
    return report


def blocked_report() -> dict[str, object]:
    report = generalized_report()
    report["status"] = "blocked"
    report["score"] = 0.5
    report["dimensions"] = {
        "top_down_clarity": 0.5,
        "support_hierarchy": 0.5,
        "grouping_mece": 0.5,
        "teaching_progression": 0.5,
        "title_body_alignment": 0.5,
    }
    report["required_revisions"] = ["Rebuild the hierarchy around one explicit controlling claim."]
    return report


class ValidateReportTests(unittest.TestCase):
    def write_report(self, report: dict[str, object], name: str = "report.json") -> Path:
        path = self.run_dir / name
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def setUp(self) -> None:
        self.run_dir = Path.cwd() / "待删除" / "pyramid-validate-test-runs"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def test_accepts_generalized_gate_report(self) -> None:
        warnings = validate_report(self.write_report(generalized_report()), enforce_gate=False)

        self.assertEqual(warnings, [])

    def test_enforce_gate_allows_valid_explicit_waiver(self) -> None:
        report = needs_revision_report()
        report["waiver"] = {
            "state": "approved",
            "approved_by": "workflow-owner",
            "reason": "The source is a dialogue transcript where preserving parallel turns is more useful.",
            "approved_at": "2026-06-30T10:00:00Z",
        }

        warnings = validate_report(self.write_report(report), enforce_gate=True, allow_waiver=True)

        self.assertEqual(warnings, [])

    def test_enforce_gate_blocks_needs_revision_and_blocked_without_waiver(self) -> None:
        validate_report(self.write_report(generalized_report(), "enforced-pass.json"), enforce_gate=True)

        for name, report in (("needs_revision", needs_revision_report()), ("blocked", blocked_report())):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValidationError, f"gate status '{report['status']}' blocks"):
                    validate_report(self.write_report(report, f"enforced-{name}.json"), enforce_gate=True)

    def test_cli_exit_codes_distinguish_validation_gate_and_waiver_failures(self) -> None:
        script_path = Path(__file__).resolve().with_name("validate_report.py")

        pass_path = self.write_report(generalized_report(), "pass.json")
        pass_result = run_cli(
            [sys.executable, "-B", str(script_path), str(pass_path), "--enforce-gate"],
        )
        self.assertEqual(pass_result.returncode, 0)

        invalid_report = generalized_report()
        invalid_report["score"] = 1.2
        invalid_result = run_cli(
            [sys.executable, "-B", str(script_path), str(self.write_report(invalid_report, "invalid.json"))],
        )
        self.assertEqual(invalid_result.returncode, 1)
        self.assertIn("INVALID:", invalid_result.stderr)

        for name, report in (("needs_revision", needs_revision_report()), ("blocked", blocked_report())):
            with self.subTest(name=name):
                result = run_cli(
                    [
                        sys.executable,
                        "-B",
                        str(script_path),
                        str(self.write_report(report, f"{name}.json")),
                        "--enforce-gate",
                    ],
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("GATE_BLOCKED:", result.stderr)

        waived_report = needs_revision_report()
        waived_report["waiver"] = {
            "state": "approved",
            "approved_by": "workflow-owner",
            "reason": "The user accepted a dialogue-preserving structure.",
            "approved_at": "2026-06-30T10:00:00Z",
        }
        waived_result = run_cli(
            [
                sys.executable,
                "-B",
                str(script_path),
                str(self.write_report(waived_report, "valid-waiver.json")),
                "--enforce-gate",
                "--allow-waiver",
            ],
        )
        self.assertEqual(waived_result.returncode, 0)

        malformed_waiver_cases = [
            ("missing-approver", {"approved_by": None}),
            ("missing-reason", {"reason": None}),
        ]
        for name, updates in malformed_waiver_cases:
            with self.subTest(name=name):
                malformed_waiver = needs_revision_report()
                waiver = {
                    "state": "approved",
                    "approved_by": "workflow-owner",
                    "reason": "The user accepted a dialogue-preserving structure.",
                    "approved_at": "2026-06-30T10:00:00Z",
                }
                waiver.update(updates)
                malformed_waiver["waiver"] = waiver
                malformed_result = run_cli(
                    [
                        sys.executable,
                        "-B",
                        str(script_path),
                        str(self.write_report(malformed_waiver, f"{name}.json")),
                        "--enforce-gate",
                        "--allow-waiver",
                    ],
                )
                self.assertEqual(malformed_result.returncode, 3)
                self.assertIn("WAIVER_INVALID:", malformed_result.stderr)

    def test_rejects_invalid_generalized_reports(self) -> None:
        cases = []

        missing_required = generalized_report()
        missing_required.pop("artifact_type")
        cases.append((missing_required, "report missing keys: artifact_type"))

        extra_field = generalized_report()
        extra_field["stage"] = "outline"
        cases.append((extra_field, "report has unknown keys: stage"))

        invalid_score = generalized_report()
        invalid_score["score"] = 1.2
        cases.append((invalid_score, "score must be between 0 and 1"))

        non_finite_score = generalized_report()
        non_finite_score["score"] = math.nan
        cases.append((non_finite_score, "score must be finite"))

        score_mismatch = generalized_report()
        score_mismatch["score"] = 0.5
        cases.append((score_mismatch, "score 0.500 differs from weighted dimensions 0.900"))

        over_permissive_status = generalized_report()
        over_permissive_status["score"] = 0.5
        over_permissive_status["dimensions"] = {
            "top_down_clarity": 0.5,
            "support_hierarchy": 0.5,
            "grouping_mece": 0.5,
            "teaching_progression": 0.5,
            "title_body_alignment": 0.5,
        }
        cases.append((over_permissive_status, "status 'pass' is more permissive than score-derived status 'blocked'"))

        pass_with_required_revisions = generalized_report()
        pass_with_required_revisions["required_revisions"] = ["Fix the unsupported section hierarchy."]
        cases.append((pass_with_required_revisions, "pass reports cannot contain required_revisions"))

        failed_without_findings = needs_revision_report()
        failed_without_findings["findings"] = []
        cases.append((failed_without_findings, "needs_revision reports must include at least one finding"))

        failed_without_required_revisions = needs_revision_report()
        failed_without_required_revisions["required_revisions"] = []
        cases.append((failed_without_required_revisions, "needs_revision reports must include required_revisions"))

        malformed_dimensions = generalized_report()
        dimensions = deepcopy(malformed_dimensions["dimensions"])
        assert isinstance(dimensions, dict)
        dimensions.pop("support_hierarchy")
        malformed_dimensions["dimensions"] = dimensions
        cases.append((malformed_dimensions, "dimensions missing keys: support_hierarchy"))

        non_finite_dimension = generalized_report()
        dimensions = deepcopy(non_finite_dimension["dimensions"])
        assert isinstance(dimensions, dict)
        dimensions["support_hierarchy"] = math.nan
        non_finite_dimension["dimensions"] = dimensions
        cases.append((non_finite_dimension, "dimensions.support_hierarchy must be finite"))

        inconsistent_waiver = generalized_report()
        waiver = deepcopy(inconsistent_waiver["waiver"])
        assert isinstance(waiver, dict)
        waiver["reason"] = "A waiver reason without approval is inconsistent."
        inconsistent_waiver["waiver"] = waiver
        cases.append((inconsistent_waiver, "waiver.reason must be null"))

        malformed_audit = generalized_report()
        audit = deepcopy(malformed_audit["audit"])
        assert isinstance(audit, dict)
        audit["input_sha256"] = "A" * 64
        malformed_audit["audit"] = audit
        cases.append((malformed_audit, "audit.input_sha256 must be a lowercase SHA-256 hex digest"))

        waived_status = generalized_report()
        waived_status["status"] = "waived"
        cases.append((waived_status, "status must be one of: blocked, needs_revision, pass"))

        for index, (report, message) in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaisesRegex(ValidationError, message):
                    validate_report(self.write_report(report, f"invalid_{index}.json"), enforce_gate=False)

    def test_cli_detects_mismatched_input_fingerprint(self) -> None:
        original_text = "The original artifact text.\n"
        changed_text = "The changed artifact text.\n"

        report = generalized_report()
        audit = deepcopy(report["audit"])
        assert isinstance(audit, dict)
        audit["input_sha256"] = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
        audit["input_size_chars"] = len(original_text)
        report["audit"] = audit

        report_path = self.write_report(report, "fingerprint_report.json")
        input_path = self.run_dir / "changed_input.md"
        input_path.write_text(changed_text, encoding="utf-8")

        script_path = Path(__file__).resolve().with_name("validate_report.py")
        result = run_cli(
            [
                sys.executable,
                "-B",
                str(script_path),
                str(report_path),
                "--input-file",
                str(input_path),
            ],
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("audit.input_sha256 does not match", result.stderr)


if __name__ == "__main__":
    unittest.main()

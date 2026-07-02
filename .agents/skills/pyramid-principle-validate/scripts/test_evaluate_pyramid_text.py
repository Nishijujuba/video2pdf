#!/usr/bin/env python3
"""Targeted tests for evaluate_pyramid_text.py."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCRIPT_PATH = Path(__file__).resolve().with_name("evaluate_pyramid_text.py")


def load_evaluator_module() -> Any:
    spec = importlib.util.spec_from_file_location("evaluate_pyramid_text_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load evaluate_pyramid_text.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except FileNotFoundError as exc:
        raise AssertionError("missing evaluator script: evaluate_pyramid_text.py") from exc
    return module


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class EvaluatePyramidTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_dir = Path.cwd() / "待删除" / "pyramid-evaluator-test-runs" / uuid4().hex
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.input_path = self.run_dir / "artifact.md"
        self.output_path = self.run_dir / "artifact.pyramid.json"

    def write_input(self, text: str = "# Main claim\n\nThe structure starts with a controlling claim.\n") -> str:
        self.input_path.write_bytes(text.encode("utf-8"))
        return text

    def semantic_result(self) -> dict[str, object]:
        return {
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
                    "issue": "The controlling claim is visible.",
                    "recommendation": "Keep the opening claim before supporting detail.",
                }
            ],
            "required_revisions": [],
        }

    def needs_revision_semantic_result(self) -> dict[str, object]:
        result = self.semantic_result()
        result["status"] = "needs_revision"
        result["score"] = 0.72
        result["dimensions"] = {
            "top_down_clarity": 0.72,
            "support_hierarchy": 0.72,
            "grouping_mece": 0.72,
            "teaching_progression": 0.72,
            "title_body_alignment": 0.72,
        }
        result["findings"] = [
            {
                "severity": "major",
                "location": "opening",
                "issue": "The artifact lists details before stating a controlling claim.",
                "recommendation": "Move the controlling claim ahead of the detail list.",
            }
        ]
        result["required_revisions"] = ["State the controlling claim before listing source details."]
        return result

    def successful_runner(self, semantic_result: dict[str, object] | None = None) -> tuple[list[dict[str, object]], Any]:
        calls: list[dict[str, object]] = []

        def fake_runner(command: list[str], **kwargs: object) -> FakeCompletedProcess:
            calls.append({"command": command, "kwargs": kwargs})
            output_file = Path(command[command.index("--output-last-message") + 1])
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(json.dumps(semantic_result or self.semantic_result()), encoding="utf-8")
            return FakeCompletedProcess(returncode=0, stdout='{"event":"done"}\n')

        return calls, fake_runner

    def test_writes_report_and_invokes_codex_with_constrained_command(self) -> None:
        evaluator = load_evaluator_module()
        input_text = self.write_input()
        calls, fake_runner = self.successful_runner()

        evaluator.evaluate_file(
            input_path=self.input_path,
            output_path=self.output_path,
            artifact_type="markdown",
            context_label="outline",
            evaluation_context="Teaching-PDF outline checkpoint.",
            runner=fake_runner,
            now=lambda: datetime(2026, 6, 30, 9, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(len(calls), 1)
        command = calls[0]["command"]
        self.assertIsInstance(command, list)
        assert isinstance(command, list)
        self.assertTrue(str(command[0]).lower().endswith("codex.cmd") if os.name == "nt" else command[0] == "codex")
        self.assertEqual(command[1:4], ["exec", "-m", "gpt-5.5"])
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--output-schema", command)
        self.assertIn("--output-last-message", command)
        self.assertIn("--json", command)
        self.assertIn("-c", command)
        self.assertIn('approval_policy="never"', command)
        self.assertEqual(command[-1], "-")

        prompt = calls[0]["kwargs"]["input"]
        self.assertIsInstance(prompt, str)
        assert isinstance(prompt, str)
        self.assertIn("Pyramid Principle Text Standard", prompt)
        self.assertIn(input_text, prompt)

        report = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(report["target"], str(self.input_path))
        self.assertEqual(report["artifact_type"], "markdown")
        self.assertEqual(report["context_label"], "outline")
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["waiver"]["state"], "none")
        self.assertEqual(report["audit"]["backend"], "codex-exec")
        self.assertEqual(report["audit"]["input_sha256"], hashlib.sha256(input_text.encode("utf-8")).hexdigest())
        self.assertEqual(report["audit"]["input_size_chars"], len(input_text))
        self.assertEqual(report["audit"]["max_input_size_chars"], 160000)
        self.assertEqual(report["audit"]["large_input_approval_state"], "not_required")

    def test_explicit_codex_model_overrides_default(self) -> None:
        evaluator = load_evaluator_module()
        self.write_input()
        calls, fake_runner = self.successful_runner()

        evaluator.evaluate_file(
            input_path=self.input_path,
            output_path=self.output_path,
            artifact_type="markdown",
            context_label="outline",
            evaluation_context="Teaching-PDF outline checkpoint.",
            codex_executable="codex.cmd",
            codex_model="gpt-5.4",
            runner=fake_runner,
            now=lambda: datetime(2026, 6, 30, 9, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(len(calls), 1)
        command = calls[0]["command"]
        self.assertIsInstance(command, list)
        assert isinstance(command, list)
        self.assertTrue(str(command[0]).lower().endswith("codex.cmd"))
        self.assertEqual(command[1:4], ["exec", "-m", "gpt-5.4"])

    @unittest.skipUnless(os.name == "nt", "Windows executable resolution is only required on Windows")
    def test_default_codex_executable_prefers_cmd_shim_on_windows(self) -> None:
        evaluator = load_evaluator_module()
        self.write_input()
        calls, fake_runner = self.successful_runner()

        evaluator.evaluate_file(
            input_path=self.input_path,
            output_path=self.output_path,
            artifact_type="markdown",
            context_label="outline",
            evaluation_context="Teaching-PDF outline checkpoint.",
            runner=fake_runner,
            now=lambda: datetime(2026, 6, 30, 9, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(len(calls), 1)
        command = calls[0]["command"]
        self.assertIsInstance(command, list)
        assert isinstance(command, list)
        self.assertTrue(
            str(command[0]).lower().endswith("codex.cmd"),
            f"expected default codex executable to avoid the PowerShell shim, got {command[0]!r}",
        )

    def test_codex_runner_decodes_output_as_utf8_with_replacement(self) -> None:
        evaluator = load_evaluator_module()
        self.write_input()
        calls, fake_runner = self.successful_runner()

        evaluator.evaluate_file(
            input_path=self.input_path,
            output_path=self.output_path,
            artifact_type="markdown",
            context_label="outline",
            evaluation_context="Teaching-PDF outline checkpoint.",
            codex_executable="codex.cmd",
            runner=fake_runner,
            now=lambda: datetime(2026, 6, 30, 9, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(len(calls), 1)
        kwargs = calls[0]["kwargs"]
        self.assertEqual(kwargs["text"], True)
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_video_report_scratch_stays_under_video_output_trash(self) -> None:
        evaluator = load_evaluator_module()
        video_dir = self.run_dir / "video-output"
        input_path = video_dir / "main.tex"
        output_path = video_dir / "review" / "pyramid" / "main.pyramid.json"
        input_text = "\\section{Main claim}\nThe document starts from the teaching claim.\n"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text(input_text, encoding="utf-8")
        calls, fake_runner = self.successful_runner()

        evaluator.evaluate_file(
            input_path=input_path,
            output_path=output_path,
            artifact_type="tex_document",
            context_label="main",
            evaluation_context="Teaching-PDF main checkpoint.",
            runner=fake_runner,
            now=lambda: datetime(2026, 6, 30, 9, 15, tzinfo=timezone.utc),
        )

        command = calls[0]["command"]
        self.assertIsInstance(command, list)
        assert isinstance(command, list)
        semantic_output = Path(command[command.index("--output-last-message") + 1])
        expected_scratch = video_dir / "待删除" / "pyramid-evaluator"
        self.assertEqual(semantic_output.parent, expected_scratch)
        self.assertTrue(any(expected_scratch.glob("*.candidate-report.json")))
        self.assertFalse((Path.cwd() / "待删除" / "pyramid-evaluator" / semantic_output.name).exists())

    def test_writes_caller_owned_explicit_waiver_without_changing_semantic_status(self) -> None:
        evaluator = load_evaluator_module()
        input_text = self.write_input()
        calls, fake_runner = self.successful_runner(self.needs_revision_semantic_result())

        evaluator.evaluate_file(
            input_path=self.input_path,
            output_path=self.output_path,
            artifact_type="markdown",
            context_label="dialogue_notes",
            evaluation_context="Teaching-PDF dialogue-preservation checkpoint.",
            waiver_approved_by="workflow-owner",
            waiver_reason="The source is best preserved as parallel dialogue turns.",
            waiver_approved_at="2026-06-30T10:00:00Z",
            runner=fake_runner,
            now=lambda: datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(calls), 1)
        report = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "needs_revision")
        self.assertEqual(report["required_revisions"], ["State the controlling claim before listing source details."])
        self.assertEqual(report["waiver"]["state"], "approved")
        self.assertEqual(report["waiver"]["approved_by"], "workflow-owner")
        self.assertEqual(report["waiver"]["reason"], "The source is best preserved as parallel dialogue turns.")
        self.assertEqual(report["waiver"]["approved_at"], "2026-06-30T10:00:00Z")
        self.assertEqual(report["audit"]["input_sha256"], hashlib.sha256(input_text.encode("utf-8")).hexdigest())

    def test_wrapper_waiver_requires_approver_and_reason_before_codex_runs(self) -> None:
        evaluator = load_evaluator_module()
        self.write_input()
        calls, fake_runner = self.successful_runner(self.needs_revision_semantic_result())

        with self.assertRaisesRegex(evaluator.EvaluationError, "--waiver-approved-by is required"):
            evaluator.evaluate_file(
                input_path=self.input_path,
                output_path=self.run_dir / "missing-approver.pyramid.json",
                artifact_type="markdown",
                context_label="dialogue_notes",
                evaluation_context="Teaching-PDF dialogue-preservation checkpoint.",
                waiver_reason="The source is best preserved as parallel dialogue turns.",
                runner=fake_runner,
            )

        with self.assertRaisesRegex(evaluator.EvaluationError, "--waiver-reason is required"):
            evaluator.evaluate_file(
                input_path=self.input_path,
                output_path=self.run_dir / "missing-reason.pyramid.json",
                artifact_type="markdown",
                context_label="dialogue_notes",
                evaluation_context="Teaching-PDF dialogue-preservation checkpoint.",
                waiver_approved_by="workflow-owner",
                runner=fake_runner,
            )

        self.assertEqual(calls, [])

    def test_size_limit_blocks_over_limit_without_approval_and_records_approval_when_allowed(self) -> None:
        evaluator = load_evaluator_module()
        input_text = self.write_input("x" * 6)
        calls, fake_runner = self.successful_runner()

        exact_limit_output = self.run_dir / "exact-limit.pyramid.json"
        evaluator.evaluate_file(
            input_path=self.input_path,
            output_path=exact_limit_output,
            artifact_type="plain_text",
            context_label="exact_limit",
            evaluation_context="Size-limit boundary test.",
            max_input_chars=len(input_text),
            runner=fake_runner,
            now=lambda: datetime(2026, 6, 30, 9, 15, tzinfo=timezone.utc),
        )
        exact_report = json.loads(exact_limit_output.read_text(encoding="utf-8"))
        self.assertEqual(exact_report["audit"]["large_input_approval_state"], "not_required")

        blocked_output = self.run_dir / "blocked-large.pyramid.json"
        with self.assertRaisesRegex(evaluator.EvaluationError, "exceeding --max-input-chars"):
            evaluator.evaluate_file(
                input_path=self.input_path,
                output_path=blocked_output,
                artifact_type="plain_text",
                context_label="over_limit",
                evaluation_context="Size-limit rejection test.",
                max_input_chars=len(input_text) - 1,
                runner=fake_runner,
            )
        self.assertFalse(blocked_output.exists())
        self.assertEqual(len(calls), 1)

        approved_output = self.run_dir / "approved-large.pyramid.json"
        evaluator.evaluate_file(
            input_path=self.input_path,
            output_path=approved_output,
            artifact_type="plain_text",
            context_label="approved_large",
            evaluation_context="Size-limit approval test.",
            max_input_chars=len(input_text) - 1,
            allow_large_input=True,
            runner=fake_runner,
            now=lambda: datetime(2026, 6, 30, 9, 16, tzinfo=timezone.utc),
        )
        approved_report = json.loads(approved_output.read_text(encoding="utf-8"))
        self.assertEqual(approved_report["audit"]["large_input_approval_state"], "approved")
        self.assertEqual(approved_report["audit"]["max_input_size_chars"], len(input_text) - 1)

    def test_cli_returns_nonzero_for_codex_and_json_failures(self) -> None:
        evaluator = load_evaluator_module()
        self.write_input()

        def args(output_name: str) -> list[str]:
            return [
                str(self.input_path),
                str(self.run_dir / output_name),
                "--artifact-type",
                "markdown",
                "--context-label",
                "outline",
                "--evaluation-context",
                "CLI failure test.",
            ]

        def command_failure_runner(command: list[str], **kwargs: object) -> FakeCompletedProcess:
            return FakeCompletedProcess(returncode=7, stderr="codex crashed")

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            command_status = evaluator.main(args("command-failure.json"), runner=command_failure_runner)
        self.assertEqual(command_status, 1)
        self.assertIn("codex exec failed with exit code 7", stderr.getvalue())

        def non_json_runner(command: list[str], **kwargs: object) -> FakeCompletedProcess:
            output_file = Path(command[command.index("--output-last-message") + 1])
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text("this is not json", encoding="utf-8")
            return FakeCompletedProcess(returncode=0)

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            non_json_status = evaluator.main(args("non-json.json"), runner=non_json_runner)
        self.assertEqual(non_json_status, 1)
        self.assertIn("non-JSON semantic output", stderr.getvalue())

        invalid_semantic = self.semantic_result()
        invalid_semantic["audit"] = {"model_owned": True}
        _calls, invalid_runner = self.successful_runner(invalid_semantic)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            invalid_status = evaluator.main(args("schema-violation.json"), runner=invalid_runner)
        self.assertEqual(invalid_status, 1)
        self.assertIn("semantic output has unknown keys: audit", stderr.getvalue())

        self_waiver_semantic = self.needs_revision_semantic_result()
        self_waiver_semantic["waiver"] = {
            "state": "approved",
            "approved_by": "model",
            "reason": "The model tried to approve continuation.",
            "approved_at": "2026-06-30T10:00:00Z",
        }
        _calls, self_waiver_runner = self.successful_runner(self_waiver_semantic)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self_waiver_status = evaluator.main(args("self-waiver.json"), runner=self_waiver_runner)
        self.assertEqual(self_waiver_status, 1)
        self.assertIn("semantic output has unknown keys: waiver", stderr.getvalue())

        non_finite_semantic = self.semantic_result()
        non_finite_semantic["score"] = math.nan
        _calls, non_finite_runner = self.successful_runner(non_finite_semantic)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            non_finite_status = evaluator.main(args("non-finite.json"), runner=non_finite_runner)
        self.assertEqual(non_finite_status, 1)
        self.assertIn("semantic output score must be finite", stderr.getvalue())

        no_weakness_semantic = self.needs_revision_semantic_result()
        no_weakness_semantic["findings"] = []
        no_weakness_semantic["required_revisions"] = []
        _calls, no_weakness_runner = self.successful_runner(no_weakness_semantic)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            no_weakness_status = evaluator.main(args("no-weakness.json"), runner=no_weakness_runner)
        self.assertEqual(no_weakness_status, 1)
        self.assertIn("needs_revision reports must include at least one finding", stderr.getvalue())

    def test_semantic_schema_limits_codex_output_to_judgment_fields(self) -> None:
        schema_path = SCRIPT_PATH.resolve().parents[1] / "references" / "evaluator-output-schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]),
            {"status", "score", "dimensions", "findings", "required_revisions"},
        )
        self.assertNotIn("audit", schema["properties"])
        self.assertNotIn("waiver", schema["properties"])
        self.assertNotIn("target", schema["properties"])


if __name__ == "__main__":
    unittest.main()

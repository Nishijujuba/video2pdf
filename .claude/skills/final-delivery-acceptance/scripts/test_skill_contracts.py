#!/usr/bin/env python3
"""Tests for Final Delivery Acceptance skill and workflow documentation."""

from __future__ import annotations

import unittest
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FinalDeliveryAcceptanceSkillContractTests(unittest.TestCase):
    def test_reviewer_skill_defines_read_only_context_and_outputs(self) -> None:
        text = read(REPO_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "SKILL.md")

        required = [
            "Acceptance Reviewer",
            "docs/acceptance/acceptance_criteria.v1.json",
            "review/acceptance/allowed_artifacts_manifest.json",
            "review/acceptance/rendered_pages/",
            "review/acceptance/delivery_glossary.json",
            "--include-delivery-glossary",
            "review/acceptance/acceptance_report.json",
            "review/acceptance/acceptance_summary.md",
            "acceptance_report.json is the only machine-readable delivery decision source",
            "read-only",
            "final delivered artifacts",
            "generation notes",
            "writer drafts",
            "chat history",
            "work/",
            "review/pyramid/",
            "review/consistency/",
            "evaluate every criterion",
            "one result for every rendered PDF page",
            "repair brief",
            "fresh Acceptance Reviewer run",
        ]
        for item in required:
            with self.subTest(item=item):
                self.assertIn(item, text)

    def test_project_instructions_require_acceptance_reviewer_and_repair_separation(self) -> None:
        for relative in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(relative=relative):
                text = read(REPO_ROOT / relative)
                self.assertIn("Independent review agent", text)
                self.assertIn("Acceptance Reviewer", text)
                self.assertIn("read-only", text)
                self.assertIn("final delivered artifacts", text)
                self.assertIn("repair subagents", text)

    def test_render_skills_place_acceptance_after_render_before_delivery(self) -> None:
        cases = [
            (
                ".agents/skills/bilibili-render-pdf/SKILL.md",
                "## PDF Verification",
            ),
            (
                ".agents/skills/youtube-render-pdf/SKILL.md",
                "## Visualization",
            ),
        ]
        for relative, preceding_anchor in cases:
            with self.subTest(relative=relative):
                text = read(REPO_ROOT / relative)
                acceptance = text.index("## Final Delivery Acceptance Gate")
                checklist = text.index("## Final Checklist")
                delivery = text.index("## Delivery")
                self.assertLess(text.index(preceding_anchor), acceptance)
                self.assertLess(acceptance, checklist)
                self.assertLess(checklist, delivery)
                required = [
                    "docs/acceptance/acceptance_criteria.v1.json",
                    "review/acceptance/allowed_artifacts_manifest.json",
                    "review/acceptance/rendered_pages/",
                    "review/acceptance/acceptance_report.json",
                    "acceptance_report.json is the only machine-readable",
                    "missing, failed, malformed, stale, or forbidden-context report blocks final delivery",
                    "Pyramid Gate and independent content review remain separate",
                    "repair subagents",
                    "session-scoped active target",
                    ".codex/delivery-targets/sessions/<session_id>/current.json",
                    ".codex/delivery-targets/task-index.json",
                    "clear-target --session-id",
                    "does not scan all active tasks",
                ]
                for item in required:
                    self.assertIn(item, text)

    def test_guard_and_bounded_repair_contracts_are_synchronized(self) -> None:
        common_phrases = [
            ".codex/delivery-targets/sessions/<session_id>/current.json",
            ".codex/delivery-targets/task-index.json",
            "review/acceptance/delivery_target.json",
            "review/acceptance/delivery_guard_report.json",
            "delivery_guard.py check",
            "generating",
            "ready_for_delivery",
            "accepted",
            "delivered",
            "blocked",
            "attempt_limit: 3",
            "review/acceptance/attempts/attempt_01/",
            "review/acceptance/manual_repair_brief.md",
            "delivery_guard_report.json is a mechanical proof of freshness and contract validity",
            "Do not deliver this PDF until delivery_guard.py records a fresh pass",
            "task-index ownership",
            "explicit handoff",
            "clear-target --session-id",
            "The legacy `.codex/delivery-targets/current.json` singleton path is unsupported for `delivery_guard.py check`",
            "The Stop hook reads the official hook `session_id`",
            "Official Stop hook command on Windows",
            "Official hook stdin payload",
            '{"session_id":"<session_id>"}',
            ".codex\\delivery-targets\\sessions\\<session_id>\\current.json",
            "does not scan all active tasks",
            "UserPromptSubmit remains out of scope",
        ]
        for relative in (
            "AGENTS.md",
            "CLAUDE.md",
            ".agents/skills/final-delivery-acceptance/SKILL.md",
            ".agents/skills/bilibili-render-pdf/SKILL.md",
            ".agents/skills/youtube-render-pdf/SKILL.md",
        ):
            with self.subTest(relative=relative):
                text = read(REPO_ROOT / relative)
                for phrase in common_phrases:
                    self.assertIn(phrase, text)

        final_delivery = read(REPO_ROOT / ".agents/skills/final-delivery-acceptance" / "SKILL.md")
        self.assertIn("Old-PDF repair requires an explicit video_output_dir unless the PDF is already inside one valid video output directory", final_delivery)
        self.assertIn("Repair subagents may inspect and modify only files inside that video output directory", final_delivery)
        self.assertIn('old-pdf-prepare "<pdf-path>" --session-id "<session_id>"', final_delivery)
        self.assertIn('record-failed-attempt --session-id "<session_id>"', final_delivery)
        self.assertIn('task-handoff --from-session-id "<from_session_id>" --to-session-id "<to_session_id>"', final_delivery)
        self.assertIn('--target-file "<video-output-dir>\\review\\acceptance\\delivery_target.json"', final_delivery)
        self.assertIn('--stage "ready_for_delivery"', final_delivery)
        self.assertIn('--previous-owner-status "superseded"', final_delivery)
        self.assertIn("Final Delivery Guard blocked delivery. Use a separate Acceptance Reviewer subagent and repair subagents", final_delivery)
        self.assertIn("The Stop hook must not launch the Acceptance Reviewer, repair subagents, page rendering, or LaTeX compilation", final_delivery)

        hooks = json.loads(read(REPO_ROOT / ".codex" / "hooks.json"))
        self.assertIn("Stop", hooks["hooks"])
        self.assertNotIn("UserPromptSubmit", hooks["hooks"])
        stop_hooks = hooks["hooks"]["Stop"][0]["hooks"]
        command = stop_hooks[0].get("commandWindows") or stop_hooks[0]["command"]
        self.assertIn("delivery_guard.py", command)
        self.assertIn("hook-stop", command)

    def test_render_skills_require_guarded_latex_compile_contract(self) -> None:
        cases = [
            (
                ".agents/skills/bilibili-render-pdf/SKILL.md",
                "bilibili-render-pdf",
            ),
            (
                ".agents/skills/youtube-render-pdf/SKILL.md",
                "youtube-render-pdf",
            ),
        ]
        required = [
            "LaTeX Compile Guard",
            "compile_latex_ascii.py",
            "--mode quick",
            "--mode final",
            "--tex",
            "--final-pdf",
            "--engine",
            "temporary diagnostic compile",
            "delivery compile",
            "review\\latex\\compile_report.json",
            "quick mode",
            "final mode",
        ]
        forbidden = [
            "Compile twice with `xelatex`",
            "before calling `xelatex`",
            "prefer the bundled ASCII staging compiler for final XeLaTeX builds",
        ]
        for relative, source_skill in cases:
            with self.subTest(relative=relative):
                text = read(REPO_ROOT / relative)
                for phrase in required:
                    self.assertIn(phrase, text)
                self.assertIn(f"--source-skill \"{source_skill}\"", text)
                for phrase in forbidden:
                    self.assertNotIn(phrase, text)

    def test_project_and_acceptance_docs_separate_compile_provenance_from_quality_decision(self) -> None:
        for relative in (
            "AGENTS.md",
            "CLAUDE.md",
            ".agents/skills/final-delivery-acceptance/SKILL.md",
        ):
            with self.subTest(relative=relative):
                text = read(REPO_ROOT / relative)
                self.assertIn("review\\latex\\compile_report.json", text)
                self.assertIn("compile provenance", text)
                self.assertIn("compile report cannot replace acceptance_report.json", text)
                self.assertIn("acceptance_report.json is the only machine-readable delivery decision source", text)
                self.assertIn("The Stop hook must not launch the Acceptance Reviewer, repair subagents, page rendering, or LaTeX compilation", text)


if __name__ == "__main__":
    unittest.main()

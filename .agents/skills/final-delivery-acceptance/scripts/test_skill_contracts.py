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
    def test_new_task_skills_use_current_kernel_authority_and_preserve_legacy_directories(self) -> None:
        required_by_skill = {
            "bilibili-render-pdf": (
                "Bilibili is `active_kernel` for new tasks through the Workflow Release Profile.",
                "For every new Bilibili task, invoke `start-run --project-config`",
                "Cutover Authority Tombstone",
                "Existing Bilibili directories remain on explicit Legacy maintenance routes",
            ),
            "youtube-render-pdf": (
                "YouTube is `active_kernel` for new tasks through the Workflow Release Profile.",
                "For every new YouTube task, invoke `start-run --project-config`",
                "Cutover Authority Tombstone",
                "Existing YouTube directories remain on explicit Legacy maintenance routes",
            ),
            "bilibili-batch-render-pdf": (
                "Batch is `active_batch` for new batches through the Workflow Release Profile.",
                "For every new batch, invoke `batch-plan --project-config`.",
                "Cutover Authority Tombstone",
                "Pre-existing legacy batch directories",
            ),
            "final-delivery-acceptance": (
                "For newly generated Bilibili and YouTube work, `start-run --project-config` validates the Workflow Release Profile",
                "Existing Legacy directories remain on explicit Legacy maintenance routes",
            ),
        }

        for name, required in required_by_skill.items():
            with self.subTest(skill=name):
                source = REPO_ROOT / ".agents" / "skills" / name / "SKILL.md"
                source_text = read(source)
                mirror_text = read(REPO_ROOT / ".claude" / "skills" / name / "SKILL.md")
                self.assertEqual(source_text, mirror_text)
                for phrase in required:
                    self.assertIn(phrase, source_text)

        for name in ("bilibili-render-pdf", "youtube-render-pdf"):
            with self.subTest(no_executable_cutover_candidate=name):
                text = read(REPO_ROOT / ".agents" / "skills" / name / "SKILL.md")
                self.assertNotIn("Cold-start cutover bootstrap", text)
                self.assertNotIn("cutover-candidate seam", text)
                self.assertNotIn("init-cutover-candidate", text)
                self.assertNotIn("platform-kernel-candidate-activate", text)

    def test_global_gate_cutover_contract_and_mirrors_are_synchronized(self) -> None:
        authoritative = {
            "final-delivery-acceptance": REPO_ROOT / ".agents/skills/final-delivery-acceptance/SKILL.md",
            "bilibili-render-pdf": REPO_ROOT / ".agents/skills/bilibili-render-pdf/SKILL.md",
            "youtube-render-pdf": REPO_ROOT / ".agents/skills/youtube-render-pdf/SKILL.md",
        }
        required = (
            "active_global_gate",
            "Acceptance Report v2",
            "acceptance-prepare",
            "acceptance-patch-commit",
            "acceptance-materialize",
            "Acceptance Report v1 is rejected",
            "synthetic Legacy Run",
            "fallback",
            "translation",
            "dual authority",
        )
        for name, source in authoritative.items():
            with self.subTest(skill=name):
                source_text = read(source)
                mirror_text = read(REPO_ROOT / ".claude/skills" / name / "SKILL.md")
                self.assertEqual(source_text, mirror_text)
                for phrase in required:
                    self.assertIn(phrase, source_text)

        for relative in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(instructions=relative):
                text = read(REPO_ROOT / relative)
                for phrase in required:
                    self.assertIn(phrase, text)
                self.assertIn("Bilibili and YouTube are `active_kernel` for new tasks", text)
                self.assertIn("Batch is `active_batch` for new batches", text)

        final_context = read(REPO_ROOT / "docs/contexts/final-acceptance/CONTEXT.md")
        delivery_context = read(REPO_ROOT / "docs/contexts/delivery-quality/CONTEXT.md")
        decision_map = read(REPO_ROOT / "docs/adr/video-workflow-kernel-2.0-decision-map.md")
        context_map = read(REPO_ROOT / "CONTEXT-MAP.md")
        self.assertIn("Status: superseded", final_context)
        self.assertIn("Status: active_global_gate", delivery_context)
        self.assertIn("`active_global_gate`", decision_map)
        self.assertIn("Bilibili and YouTube are `active_kernel` for new tasks", decision_map)
        self.assertIn("Batch is `active_batch` for new batches", decision_map)
        self.assertIn("active_global_gate", context_map)

    def test_reviewer_skill_defines_read_only_context_and_outputs(self) -> None:
        text = read(REPO_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "SKILL.md")

        required = [
            "Acceptance Reviewer",
            "Acceptance Report v2",
            "Delivery Quality policy",
            "review/acceptance/allowed_artifacts_manifest.json",
            "review/acceptance/rendered_pages/",
            "`authorized_read_set`",
            "review/acceptance/acceptance_report.json",
            "acceptance_report.json is the only machine-readable delivery decision source",
            "read-only",
            "Final delivered artifacts",
            "generation notes",
            "writer drafts",
            "chat history",
            "work/",
            "review/pyramid/",
            "review/consistency/",
            "every `criterion_ids` entry",
            "one `visual_scan_evidence.pages_checked[]` entry for every rendered PDF page",
            "repair brief",
            "fresh provider Attempt",
        ]
        for item in required:
            with self.subTest(item=item):
                self.assertIn(item, text)

    def test_visual_reviewer_policy_uses_only_the_provider_attempt_contract(self) -> None:
        skills = (
            ".agents/skills/final-delivery-acceptance/SKILL.md",
            ".agents/skills/bilibili-render-pdf/SKILL.md",
            ".agents/skills/youtube-render-pdf/SKILL.md",
        )
        required = (
            "precompile semantic owners",
            "`writing-quality-reviewer`",
            "full reader-facing text, formula, and Delivery Glossary semantic review",
            "provider-created Task Envelope",
            "`authorized_read_set`",
            "provider-created Attempt directory",
            "`declared_write_set`",
            "`required_output.path`",
            "one Visual Quality Judgment Patch",
            "`acceptance-materialize`",
            "provider materializes",
        )
        forbidden = (
            "run a full final text scan for style criteria",
            "run a full final formula scan",
            "`generation_process_used: false`",
            "`review_context_used.artifacts_read`",
            "replace every skeleton placeholder",
            "final-artifacts-only context",
        )
        for relative in skills:
            with self.subTest(relative=relative):
                text = read(REPO_ROOT / relative)
                for phrase in required:
                    self.assertIn(phrase, text)
                for phrase in forbidden:
                    self.assertNotIn(phrase, text)

    def test_project_instructions_require_acceptance_reviewer_and_repair_separation(self) -> None:
        for relative in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(relative=relative):
                text = read(REPO_ROOT / relative)
                self.assertIn("Independent review agent", text)
                self.assertIn("Acceptance Reviewer", text)
                self.assertIn("read-only", text)
                self.assertIn("exact path-and-SHA read set", text)
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
                    "Acceptance Report v2",
                    "acceptance-prepare",
                    "acceptance-patch-commit",
                    "acceptance-materialize",
                    "review/acceptance/allowed_artifacts_manifest.json",
                    "review/acceptance/rendered_pages/",
                    "review/acceptance/acceptance_report.json",
                    "acceptance_report.json is the only machine-readable",
                    "non-v2 report blocks final delivery",
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
            "--help",
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
            "automatic short launch alias",
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
                self.assertIn("compile report cannot replace acceptance_report.json", text)
                self.assertIn("acceptance_report.json is the only machine-readable delivery decision source", text)
                self.assertIn("The Stop hook must not launch the Acceptance Reviewer, repair subagents, page rendering, or LaTeX compilation", text)

        final_skill = read(REPO_ROOT / ".agents/skills/final-delivery-acceptance/SKILL.md")
        self.assertIn("review\\latex\\compile_report.json", final_skill)


if __name__ == "__main__":
    unittest.main()

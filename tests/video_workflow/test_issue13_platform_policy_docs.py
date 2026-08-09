from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class Issue13PlatformPolicyDocumentationTests(unittest.TestCase):
    def test_platform_activation_authority_is_consistent_across_active_docs(self) -> None:
        paths = (
            "AGENTS.md",
            "CLAUDE.md",
            "CONTEXT-MAP.md",
            "docs/contexts/video-workflow/CONTEXT.md",
            "docs/contexts/delivery-quality/CONTEXT.md",
            "docs/adr/video-workflow-kernel-2.0-decision-map.md",
        )
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn("Bilibili", text)
                self.assertIn("`active_kernel`", text)
                self.assertIn("YouTube", text)
                self.assertIn("`active_legacy`", text)
                self.assertIn("`active_global_gate`", text)

    def test_bilibili_skill_mirror_delegates_kernel_mechanics_to_public_cli(self) -> None:
        authority = (ROOT / ".agents/skills/bilibili-render-pdf/SKILL.md").read_text(encoding="utf-8")
        mirror = (ROOT / ".claude/skills/bilibili-render-pdf/SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(authority, mirror)
        for command in (
            "bootstrap-probe",
            "init-run",
            "production-plan",
            "production-advance",
            "guarded-compile",
            "delivery-transition",
            "delivery-archive",
        ):
            self.assertIn(command, authority)
        self.assertIn("New Bilibili Runs are `active_kernel`", authority)
        self.assertIn("Existing Bilibili directories remain Legacy", authority)
        self.assertIn("YouTube remains `active_legacy`", authority)
        self.assertIn("Global Gate remains `active_global_gate`", authority)

    def test_skill_keeps_semantic_roles_and_independent_reviewer(self) -> None:
        text = (ROOT / ".agents/skills/bilibili-render-pdf/SKILL.md").read_text(encoding="utf-8")
        for role in (
            "Data Preparation agent",
            "Outline agent",
            "Writer agents",
            "Figure agents",
            "Consistency agent",
            "Independent review agent",
            "Acceptance Reviewer",
        ):
            self.assertIn(role, text)


if __name__ == "__main__":
    unittest.main()

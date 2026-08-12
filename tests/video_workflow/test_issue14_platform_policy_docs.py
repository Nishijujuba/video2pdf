from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]

YOUTUBE_PENDING_STATUS = (
    "YouTube remains `active_legacy`; the Platform Kernel implementation and "
    "one-candidate cutover seam are available."
)
YOUTUBE_CONFIRMED_STATUS = (
    "`active_kernel` begins only after runtime `CONFIRMED` platform authority "
    "and published Slice 13 Exit Evidence."
)


class Issue14PlatformPolicyDocumentationTests(unittest.TestCase):
    def test_youtube_cutover_status_requires_runtime_confirmation(self) -> None:
        paths = (
            "AGENTS.md",
            "CLAUDE.md",
            "CONTEXT-MAP.md",
            "docs/adr/video-workflow-kernel-2.0-decision-map.md",
            "docs/contexts/video-workflow/CONTEXT.md",
            "docs/contexts/delivery-quality/CONTEXT.md",
            ".agents/skills/youtube-render-pdf/SKILL.md",
            ".claude/skills/youtube-render-pdf/SKILL.md",
        )
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn(YOUTUBE_PENDING_STATUS, text)
                self.assertIn(YOUTUBE_CONFIRMED_STATUS, text)

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

    def test_youtube_skill_mirror_delegates_kernel_mechanics_to_public_cli(self) -> None:
        authority = (ROOT / ".agents/skills/youtube-render-pdf/SKILL.md").read_text(encoding="utf-8")
        mirror = (ROOT / ".claude/skills/youtube-render-pdf/SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(authority, mirror)
        self.assertIn(
            "Run `platform-kernel-candidate-activate` for that exact ready candidate.",
            authority,
        )
        self.assertNotIn("that exact accepted candidate", authority)
        for command in (
            "bootstrap-probe",
            "init-run",
            "source-acquire",
            "source-acquire-reconcile",
            "production-plan",
            "production-advance",
            "guarded-compile",
            "delivery-transition",
            "delivery-archive",
        ):
            self.assertIn(command, authority)
        self.assertIn(YOUTUBE_PENDING_STATUS, authority)
        self.assertIn("Existing Bilibili directories remain Legacy", authority)
        self.assertIn("Global Gate remains `active_global_gate`", authority)
        self.assertIn("Slice 13", authority)

    def test_youtube_skill_keeps_semantic_roles_and_independent_reviewer(self) -> None:
        text = (ROOT / ".agents/skills/youtube-render-pdf/SKILL.md").read_text(encoding="utf-8")
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

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]

YOUTUBE_ACTIVE_STATUS = (
    "YouTube is `active_kernel` for new tasks through the Workflow Release Profile."
)


class Issue14PlatformPolicyDocumentationTests(unittest.TestCase):
    def test_new_youtube_tasks_use_active_kernel_and_existing_directories_stay_legacy(self) -> None:
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
                self.assertIn("Workflow Release Profile", text)
                self.assertIn("`active_kernel`", text)
                self.assertNotIn("YouTube remains `active_legacy`", text)
                self.assertNotIn(
                    "The current repository has no confirmed YouTube platform authority",
                    text,
                )

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
                self.assertIn("Workflow Release Profile", text)
                self.assertIn("`active_global_gate`", text)

    def test_youtube_skill_mirror_delegates_kernel_mechanics_to_public_cli(self) -> None:
        authority = (ROOT / ".agents/skills/youtube-render-pdf/SKILL.md").read_text(encoding="utf-8")
        mirror = (ROOT / ".claude/skills/youtube-render-pdf/SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(authority, mirror)
        for command in (
            "start-run",
            "source-acquire",
            "source-acquire-reconcile",
            "production-plan",
            "production-advance",
            "guarded-compile",
            "delivery-transition",
            "delivery-archive",
        ):
            self.assertIn(command, authority)
        self.assertIn(YOUTUBE_ACTIVE_STATUS, authority)
        self.assertIn("Existing YouTube directories remain on explicit Legacy maintenance routes", authority)
        self.assertIn("Global Gate remains `active_global_gate`", authority)
        self.assertIn("Slice 13", authority)
        self.assertNotIn("YouTube remains `active_legacy`", authority)
        self.assertNotIn("Cold-start cutover bootstrap", authority)
        self.assertIn("A new request has no Legacy fallback", authority)
        self.assertNotIn(
            "The current repository has no confirmed YouTube platform authority",
            authority,
        )
        self.assertIn(
            "Direct `yt-dlp`, `whisper`, and `compile_latex_ascii.py` commands in "
            "this skill are Legacy recovery references for pre-existing directories "
            "only; they are forbidden for a new task.",
            authority,
        )
        self.assertIn(
            "For a new Kernel task, invoke compilation only through the Workflow CLI:",
            authority,
        )
        self.assertIn(
            "The `workspace` directory is the only authorized parent for new YouTube "
            "PDF outputs.",
            authority,
        )
        self.assertNotIn(
            "unless the user explicitly asks for a legacy/root-level location",
            authority,
        )

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

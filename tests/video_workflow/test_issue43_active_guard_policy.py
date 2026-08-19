from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Issue43ActiveGuardPolicyTests(unittest.TestCase):
    def test_active_global_gate_policy_and_mirrors_are_synchronized(self) -> None:
        required = (
            "active_global_gate",
            "Acceptance Report v2",
            "Acceptance Report v1 is rejected",
            "fallback",
            "translation",
            "dual authority",
            "synthetic Legacy Run",
        )
        for name in ("final-delivery-acceptance", "bilibili-render-pdf", "youtube-render-pdf"):
            authority = (PROJECT_ROOT / ".agents/skills" / name / "SKILL.md").read_text(encoding="utf-8")
            mirror = (PROJECT_ROOT / ".claude/skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertEqual(authority, mirror)
            for phrase in required:
                self.assertIn(phrase, authority)

        for relative in ("AGENTS.md", "CLAUDE.md"):
            instructions = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
            for phrase in required:
                self.assertIn(phrase, instructions)
            self.assertIn("Bilibili is `active_kernel` for all new tasks", instructions)
            self.assertIn("YouTube is `active_kernel` for all new tasks", instructions)
            self.assertIn("Batch is `active_batch` for all new batches", instructions)

        decision_map = (PROJECT_ROOT / "docs/adr/video-workflow-kernel-2.0-decision-map.md").read_text(encoding="utf-8")
        self.assertIn("The shared final-quality gate has `active_global_gate` status.", decision_map)
        self.assertIn(
            "Bilibili and YouTube have `active_kernel` status for new Runs, and Batch "
            "has `active_batch` status for new batches.",
            decision_map,
        )
        self.assertIn("Existing Bilibili and YouTube directories retain Legacy", decision_map)


if __name__ == "__main__":
    unittest.main()

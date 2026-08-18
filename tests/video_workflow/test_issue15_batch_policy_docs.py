from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]

BATCH_PENDING_STATUS = (
    "Batch remains `target_only` until runtime authority activation; the "
    "Batch Supervisor, Batch Record, and Batch Item Projections are implemented "
    "and the `batch-*` CLI is available, but new-batch authority begins only "
    "with a published Slice 14 Exit Evidence Manifest. The Legacy batch driver "
    "is retained for pre-existing batch directories only; PDF-existence success "
    "and global `--concurrency` are retired."
)

SUCCESS_DEFINITION = "cannot establish success"


class Issue15BatchPolicyDocumentationTests(unittest.TestCase):
    def test_batch_activation_status_is_consistent_across_active_docs(self) -> None:
        paths = (
            "AGENTS.md",
            "CLAUDE.md",
            "CONTEXT-MAP.md",
            "docs/adr/video-workflow-kernel-2.0-decision-map.md",
            "docs/contexts/video-workflow/CONTEXT.md",
            ".agents/skills/bilibili-batch-render-pdf/SKILL.md",
            ".claude/skills/bilibili-batch-render-pdf/SKILL.md",
        )
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn("Slice 14 Exit Evidence", text)
                self.assertIn("PDF-existence success", text)
                self.assertIn("`--concurrency`", text)

    def test_batch_skill_success_definition_requires_guarded_delivered(self) -> None:
        authority = (
            ROOT / ".agents/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn(SUCCESS_DEFINITION, authority)
        self.assertIn("delivery_guard_report.json", authority)
        self.assertIn("delivered", authority)

    def test_batch_skill_mirror_is_byte_identical(self) -> None:
        authority = (
            ROOT / ".agents/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_bytes()
        mirror = (
            ROOT / ".claude/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_bytes()
        self.assertEqual(authority, mirror)

    def test_batch_skill_exposes_kernel_cli_commands(self) -> None:
        text = (
            ROOT / ".agents/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_text(encoding="utf-8")
        for command in (
            "batch-plan",
            "batch-run",
            "batch-recover",
            "batch-rebuild-projections",
            "batch-status",
        ):
            self.assertIn(command, text)

    def test_batch_skill_documents_cli_default_bindings(self) -> None:
        text = (
            ROOT / ".agents/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("When `--workspace-root` is omitted", text)
        self.assertIn("`--run-task-start` is optional", text)
        self.assertIn("reuses that exact bound value", text)
        self.assertIn("Supplying a different value after binding fails closed", text)

    def test_batch_skill_retires_legacy_authorities(self) -> None:
        text = (
            ROOT / ".agents/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("global `--concurrency`", text)
        self.assertIn("PDF path, output-directory existence, process exit code", text)
        self.assertIn("Resource Circuit Breaker", text)
        self.assertIn("Resource Admission", text)
        self.assertIn("never writes a video's phase, checkpoint", text)
        self.assertIn("never creates duplicate runs", text)
        self.assertIn("legacy/", text)

    def test_existing_platform_status_sentences_unchanged(self) -> None:
        # The Bilibili/YouTube/Slice 12/13 sentences stay intact (pinned by
        # test_issue14_platform_policy_docs.py); batch adds without replacing.
        for relative in ("AGENTS.md", "CLAUDE.md", "CONTEXT-MAP.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn("Slice 13 Exit Evidence", text)
                self.assertIn("active_global_gate", text)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]

BATCH_ACTIVE_STATUS = (
    "Batch is `active_batch` for all new batches under the current runtime authority "
    "and published Slice 14 Exit Evidence Manifest. The Legacy batch driver is "
    "retained for pre-existing batch directories only; PDF-existence success and "
    "global `--concurrency` are retired."
)

SUCCESS_DEFINITION = "cannot establish success"


class Issue15BatchPolicyDocumentationTests(unittest.TestCase):
    def test_new_batches_use_active_batch_and_existing_directories_keep_legacy_driver(self) -> None:
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
                self.assertIn(BATCH_ACTIVE_STATUS, text)
                self.assertNotIn("Batch remains `target_only`", text)

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
            "batch-activate",
            "batch-authority-refresh",
            "batch-reconcile",
            "batch-authority-check",
            "batch-plan",
            "batch-run",
            "batch-recover",
            "batch-rebuild-projections",
            "batch-status",
        ):
            self.assertIn(command, text)

    def test_batch_skill_routes_absent_and_stale_authority_repairs(self) -> None:
        text = (
            ROOT / ".agents/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Absent Batch authority", text)
        self.assertIn("Stale Batch authority", text)
        self.assertIn(
            "batch-authority-refresh --control-store-root \"<control-store-root>\" "
            "--exit-evidence \"<refreshed-slice14-manifest>\" "
            "--expected-generation \"<current-generation>\" "
            "--refreshed-at \"<iso>\"",
            text,
        )
        refresh = text.index("Stale Batch authority")
        reconcile = text.index("batch-reconcile", refresh)
        check = text.index("batch-authority-check", reconcile)
        self.assertLess(refresh, reconcile)
        self.assertLess(reconcile, check)
        self.assertIn(
            "Existing Legacy batch directories remain on the Legacy driver",
            text,
        )
        self.assertIn("<control-store-root>/active_batch.json", text)
        self.assertIn("integer greater than or equal to 1", text)
        self.assertIn("does not supply the current generation", text)

    def test_batch_skill_documents_governed_activation_sequence(self) -> None:
        text = (
            ROOT / ".agents/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Publishing Slice 14 and activating Batch authority are separate governed recovery steps.",
            text,
        )
        self.assertIn("active_batch.json", text)
        self.assertIn(BATCH_ACTIVE_STATUS, text)
        self.assertNotIn("leaves Batch `target_only`", text)
        self.assertIn("Use `batch-reconcile` after an interrupted activation.", text)
        self.assertIn("`batch-authority-check` is required before `batch-plan`", text)

    def test_batch_skill_documents_cli_default_bindings(self) -> None:
        text = (
            ROOT / ".agents/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("When `--workspace-root` is omitted", text)
        self.assertIn("`--control-store-root` owns the Batch Record", text)
        self.assertIn("every later command resolves it from the record", text)
        self.assertIn("`--run-task-start` is optional", text)
        self.assertIn("reuses that exact bound value", text)
        self.assertIn("Supplying a different value after binding fails closed", text)

    def test_rebuild_and_status_examples_supply_only_control_store_root(self) -> None:
        text = (
            ROOT / ".agents/skills/bilibili-batch-render-pdf/SKILL.md"
        ).read_text(encoding="utf-8")
        for command in ("batch-rebuild-projections", "batch-status"):
            examples = [
                line
                for line in text.splitlines()
                if f"video_workflow.py {command}" in line
            ]
            with self.subTest(command=command):
                self.assertTrue(examples)
                self.assertTrue(
                    all("--control-store-root" in line for line in examples), examples
                )
                self.assertTrue(
                    all("--workspace-root" not in line for line in examples), examples
                )

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

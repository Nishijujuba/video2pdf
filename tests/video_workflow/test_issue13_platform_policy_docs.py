from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]

COLD_START_CUTOVER_SEQUENCE = (
    "`ready_for_delivery` with a provider-current passing Acceptance Report v2 -> "
    "`PROVISIONAL` -> `accepted` -> fresh current Delivery Guard -> `delivered` -> "
    "published Slice 12 Exit Evidence -> `CONFIRMED`"
)

FULL_SOURCE_CUTOVER_SEQUENCE = (
    "`PREPARED` -> `INITIALIZED` -> `source_ready` -> `ready_for_delivery` with a "
    "provider-current passing Acceptance Report v2 -> `PROVISIONAL` -> `accepted` -> "
    "fresh current Delivery Guard -> `delivered` -> published Slice 12 Exit Evidence -> "
    "`CONFIRMED`"
)

SAME_RUN_SOURCE_ACQUISITION = (
    "After `init-cutover-candidate`, run the public `source-acquire` command against "
    "the candidate's existing `--run-dir`; it attaches source evidence to that same "
    "Run and must not create a second Run."
)

WHISPER_TASK_PROMOTION = (
    "When no usable CC subtitle exists, `source-acquire` must stage Whisper output "
    "through the Kernel-issued Whisper Task/Attempt and promote the validated Attempt "
    "before `source_ready` becomes current."
)

NO_SECOND_SMOKE_RUN = (
    "The candidate workflow must never call `source-live-smoke`; no second Run may be "
    "created for source acquisition."
)

RECOVERABLE_COOKIE_BLOCKER = (
    "An expired or rejected Cookie is a recoverable `user_input` Source Blocker: "
    "preserve the same Run and its evidence, do not count it as a delivery attempt "
    "failure, and immediately request a refreshed Cookie from the user."
)

COOKIE_RETRY_SEQUENCE = (
    "After receiving the refreshed Cookie, close the source circuit breaker, run "
    "`source-blocker-resolve`, and retry `source-acquire` on the same Run with a new "
    "`source_epoch`."
)

COOKIE_SECRET_BOUNDARY = (
    "The Cookie path and Cookie contents are credential-bearing secrets and must "
    "never appear in logs, reports, shared evidence, or task prompts."
)

SOURCE_ACQUIRE_RECONCILE = (
    "If acquisition is interrupted after terminal proof persistence and before "
    "Resource Lease release, run `source-acquire-reconcile --run-dir "
    "<run-dir>`."
)

SOURCE_RECONCILE_SAME_RUN = (
    "`source-acquire-reconcile` reloads the persisted terminal proof, releases the "
    "existing Lease, and advances or retries the interrupted Task on the same Run; "
    "it must not initialize or attach another Run."
)

BILIBILI_ACTIVE_STATUS = (
    "Bilibili is `active_kernel` for new tasks through the Workflow Release Profile."
)
YOUTUBE_ACTIVE_STATUS = (
    "YouTube is `active_kernel` for new tasks through the Workflow Release Profile."
)
class Issue13PlatformPolicyDocumentationTests(unittest.TestCase):
    def test_new_bilibili_tasks_use_active_kernel_and_existing_directories_stay_legacy(self) -> None:
        paths = (
            "AGENTS.md",
            "CLAUDE.md",
            "CONTEXT-MAP.md",
            "docs/adr/video-workflow-kernel-2.0-decision-map.md",
            "docs/contexts/video-workflow/CONTEXT.md",
            "docs/contexts/delivery-quality/CONTEXT.md",
            ".agents/skills/bilibili-render-pdf/SKILL.md",
            ".claude/skills/bilibili-render-pdf/SKILL.md",
        )
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn("Workflow Release Profile", text)
                self.assertIn("`active_kernel`", text)
                self.assertNotIn("Bilibili remains `active_legacy`", text)
                self.assertNotIn("No component has `active_kernel` status", text)

    def test_bilibili_skill_routes_new_tasks_through_ordinary_kernel_entrypoint(self) -> None:
        for relative in (
            ".agents/skills/bilibili-render-pdf/SKILL.md",
            ".claude/skills/bilibili-render-pdf/SKILL.md",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn("start-run --project-config", text)
                self.assertNotIn("workflow-policy-check", text)
                self.assertNotIn(
                    "The current repository has no confirmed Bilibili platform authority",
                    text,
                )
                self.assertNotIn("Cold-start cutover bootstrap", text)
                self.assertIn("A new request has no Legacy fallback", text)

    def test_cold_start_cutover_order_is_consistent_across_authority_docs(self) -> None:
        paths = (
            "docs/adr/0040-cut-over-to-the-kernel-one-platform-at-a-time.md",
        )
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn(COLD_START_CUTOVER_SEQUENCE, text)

    def test_active_decision_map_has_no_candidate_route_for_new_tasks(self) -> None:
        text = (ROOT / "docs/adr/video-workflow-kernel-2.0-decision-map.md").read_text(
            encoding="utf-8"
        )
        self.assertIn('SR -->|"yes"| BP["Bootstrap Probe"]', text)
        self.assertIn('SR -->|"no"| FC["Fail closed before Run creation"]', text)
        self.assertNotIn('CS -->|"cold-start candidate"|', text)
        self.assertNotIn('PR["Prepare one bound cutover candidate"]', text)

    def test_candidate_source_acquisition_uses_one_public_same_run_seam(self) -> None:
        paths = (
            "docs/adr/0012-use-two-phase-bootstrap-and-run-initialization.md",
            "docs/adr/0019-bound-source-agent-judgment-with-script-owned-acquisition-evidence.md",
            "docs/adr/0040-cut-over-to-the-kernel-one-platform-at-a-time.md",
        )
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn(SAME_RUN_SOURCE_ACQUISITION, text)
                self.assertIn(WHISPER_TASK_PROMOTION, text)
                self.assertIn(NO_SECOND_SMOKE_RUN, text)
                self.assertIn(FULL_SOURCE_CUTOVER_SEQUENCE, text)

    def test_cookie_authentication_blocker_is_recoverable_on_the_same_run(self) -> None:
        paths = (
            "docs/adr/0012-use-two-phase-bootstrap-and-run-initialization.md",
            "docs/adr/0019-bound-source-agent-judgment-with-script-owned-acquisition-evidence.md",
            "docs/adr/0040-cut-over-to-the-kernel-one-platform-at-a-time.md",
            "docs/adr/video-workflow-kernel-2.0-decision-map.md",
            "docs/contexts/video-workflow/CONTEXT.md",
        )
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn(RECOVERABLE_COOKIE_BLOCKER, text)
                self.assertIn(COOKIE_RETRY_SEQUENCE, text)
                self.assertIn(COOKIE_SECRET_BOUNDARY, text)

    def test_source_acquisition_recovery_reconciles_the_same_run(self) -> None:
        paths = (
            "docs/adr/video-workflow-kernel-2.0-decision-map.md",
            "docs/contexts/video-workflow/CONTEXT.md",
        )
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIn(SOURCE_ACQUIRE_RECONCILE, text)
                self.assertIn(SOURCE_RECONCILE_SAME_RUN, text)

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

    def test_bilibili_skill_mirror_delegates_kernel_mechanics_to_public_cli(self) -> None:
        authority = (ROOT / ".agents/skills/bilibili-render-pdf/SKILL.md").read_text(encoding="utf-8")
        mirror = (ROOT / ".claude/skills/bilibili-render-pdf/SKILL.md").read_text(encoding="utf-8")
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
        self.assertIn(BILIBILI_ACTIVE_STATUS, authority)
        self.assertIn("Existing Bilibili directories remain on explicit Legacy maintenance routes", authority)
        self.assertNotIn("Bilibili remains `active_legacy`", authority)
        self.assertIn("A new request has no Legacy fallback", authority)
        self.assertIn("Global Gate remains `active_global_gate`", authority)
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
            "The `workspace` directory is the only authorized parent for new Bilibili "
            "PDF outputs.",
            authority,
        )
        self.assertNotIn(
            "unless the user explicitly asks for a legacy/root-level location",
            authority,
        )
        self.assertNotIn(
            "compile through the LaTeX Compile Guard with "
            "`scripts/compile_latex_ascii.py`",
            authority,
        )

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

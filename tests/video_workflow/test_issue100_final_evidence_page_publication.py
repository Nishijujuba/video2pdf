from __future__ import annotations

"""Issue #100 fixture migration impact list.

Graph: compiler page files (authority input) -> render manifest (derived node) ->
PREPARED / FILES_PUBLISHED / COMMITTED (boundaries) -> canonical page set and
Input Binding (derived nodes) -> Acceptance Task Envelope (observation).

Positive fixtures and the shared Issue #13 builder are rematerialized through
the public commands. Negative fixtures below declare their single contradiction,
mutation seam, rematerialized and stale nodes, first gate, and stable error code.
Affected snapshots and golden data: none. Precedence scenario:
pending_publication_with_missing_upstream_source. Focused contracts: this module
plus the touched Issue #13, Issue #94, and Acceptance v2 seams. The
complete affected acceptance suite remains deferred by the Spec #83 test boundary.
"""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from tests.video_workflow import test_issue13_final_evidence_cli as final_evidence_tests
from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow.test_issue13_run_initialization import (
    PROJECT_ROOT,
    _run_start_cli_with_recording,
    _write_start_run_project,
)


class Issue100FinalEvidencePagePublicationTests(unittest.TestCase):
    def _fixture(
        self,
    ) -> final_evidence_tests.Issue13FinalEvidenceCliTests:
        fixture = final_evidence_tests.Issue13FinalEvidenceCliTests(
            methodName="test_public_cli_prepares_kernel_final_evidence_for_acceptance_v2"
        )

        def current_source_ready_run() -> tuple[Path, Path]:
            case_root = new_case_dir(self.id(), label="issue100-final-evidence")
            project_config, control_root, credential = _write_start_run_project(
                case_root
            )
            completed, envelope = _run_start_cli_with_recording(
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/providers/bilibili/fresh-download",
                "start-run",
                "--project-config",
                str(project_config),
                "--platform",
                "bilibili",
                "--source-url",
                "https://www.bilibili.com/video/BV1TEST00001/?p=1",
                "--session-id",
                "session-issue100",
                "--credential-ref",
                str(credential),
            )
            fixture.assertEqual(
                0, completed.returncode, completed.stdout + completed.stderr
            )
            run_dir = Path(envelope["data"]["run_dir"])
            acquired = fixture._require_ok(
                "source-acquire",
                "--run-dir",
                str(run_dir),
                "--cookie-file",
                str(credential),
                "--provider-recording",
                str(
                    PROJECT_ROOT
                    / "tests/video_workflow/fixtures/providers/bilibili/fresh-download"
                ),
            )
            fixture.assertEqual("source_acquired", acquired["classification"])
            return run_dir, control_root

        fixture._source_ready_v4_run = current_source_ready_run  # type: ignore[method-assign]
        return fixture

    def _current_evidence(
        self,
    ) -> tuple[
        final_evidence_tests.Issue13FinalEvidenceCliTests,
        Path,
        Path,
        dict[str, Path],
    ]:
        fixture = self._fixture()
        run_dir, control_root = fixture._source_ready_v4_run()
        fixture._production_complete(run_dir)
        evidence = fixture._current_quality_evidence(run_dir)
        evidence["render_evidence_manifest"] = (
            evidence["final_compile_report"].parent / "render-evidence-manifest.json"
        )
        return fixture, run_dir, control_root, evidence

    def _commit_predecessor_and_open_revision(
        self,
        fixture: final_evidence_tests.Issue13FinalEvidenceCliTests,
        run_dir: Path,
        control_root: Path,
        evidence: dict[str, Path],
    ) -> bytes:
        completed, prepared = fixture._invoke_prepare(
            run_dir, control_root, evidence
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        binding_path = Path(prepared["data"]["input_binding_path"])
        canonical_page = (
            run_dir / "review" / "acceptance" / "rendered_pages" / "page_0001.png"
        )
        predecessor = canonical_page.read_bytes()
        displaced_binding = (
            run_dir / "待删除" / "issue100-predecessor-input-binding.json"
        )
        displaced_binding.parent.mkdir(parents=True, exist_ok=True)
        binding_path.replace(displaced_binding)
        return predecessor

    def test_final_evidence_publishes_pages_before_reviewer_dispatch(self) -> None:
        fixture, run_dir, control_root, evidence = self._current_evidence()
        final_compile_root = evidence["final_compile_report"].parent
        source_manifest = final_compile_root / "render-evidence-manifest.json"
        canonical_root = run_dir / "review" / "acceptance" / "rendered_pages"

        self.assertTrue(source_manifest.is_file())
        self.assertEqual([], list(canonical_root.glob("page_*.png")))

        completed, prepared = fixture._invoke_prepare(
            run_dir, control_root, evidence
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        binding_path = Path(prepared["data"]["input_binding_path"])
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        expected_page = canonical_root / "page_0001.png"
        self.assertEqual(str(expected_page.resolve()), binding["rendered_pages"][0]["path"])
        self.assertTrue(expected_page.is_file())
        published = fixture._require_ok(
            "acceptance-final-authority-publish",
            "--input-binding",
            str(binding_path),
        )
        self.assertEqual(
            "acceptance_v2_final_authority_published",
            published["classification"],
        )

        accepted = fixture._require_ok(
            "acceptance-prepare",
            "--workspace-root",
            str(run_dir / "review" / "acceptance"),
            "--input-binding",
            str(binding_path),
            "--attempt-number",
            "1",
            "--prepared-at",
            "2026-08-11T02:01:00Z",
            "--coordinator-session",
            "coordinator-issue100",
        )
        task_path = next(
            Path(accepted["data"]["execution_root"]).glob("tasks/*/task.json")
        )
        task = json.loads(task_path.read_text(encoding="utf-8"))
        bound_read = next(
            item
            for item in task["authorized_read_set"]
            if item["logical_id"] == "rendered_page:1"
        )
        self.assertEqual(binding["rendered_pages"][0], {
            "page": 1,
            "path": bound_read["path"],
            "sha256": bound_read["sha256"],
        })

    def test_prepared_page_publication_resumes_through_same_public_command(
        self,
    ) -> None:
        fixture, run_dir, control_root, evidence = self._current_evidence()
        canonical_page = (
            run_dir
            / "review"
            / "acceptance"
            / "rendered_pages"
            / "page_0001.png"
        )

        interrupted, fault = fixture._invoke_prepare(
            run_dir,
            control_root,
            evidence,
            fault_point="after_page_staging",
        )

        self.assertEqual(60, interrupted.returncode)
        self.assertEqual("injected_final_evidence_fault", fault["classification"])
        self.assertFalse(canonical_page.is_file())

        # scenario_id: prepared_publication_interrupted
        # target_invariant: PREPARED never remains the canonical authority
        # mutation_seam: after PREPARED and before page publication
        # rematerialized_nodes: empty canonical set; intentionally_stale_nodes: none
        # expected_first_gate/error: final_evidence_page_reconciliation /
        # final_evidence_page_publication_restored; class: single_contradiction
        restored, rejection = fixture._invoke_prepare(
            run_dir, control_root, evidence
        )

        self.assertEqual(20, restored.returncode)
        self.assertEqual(
            "final_evidence_page_publication_restored",
            rejection["data"]["error_code"],
        )
        self.assertFalse(canonical_page.is_file())

        completed, prepared = fixture._invoke_prepare(run_dir, control_root, evidence)

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        binding = json.loads(
            Path(prepared["data"]["input_binding_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(binding["rendered_pages"][0]["sha256"], final_evidence_tests._sha256(canonical_page))

    def test_split_move_interruption_preserves_committed_predecessor_identity(
        self,
    ) -> None:
        fixture, run_dir, control_root, evidence = self._current_evidence()
        preceding_bytes = self._commit_predecessor_and_open_revision(
            fixture, run_dir, control_root, evidence
        )
        canonical_page = (
            run_dir / "review" / "acceptance" / "rendered_pages" / "page_0001.png"
        )

        interrupted, fault = fixture._invoke_prepare(
            run_dir,
            control_root,
            evidence,
            fault_point="after_previous_archived",
        )

        self.assertEqual(60, interrupted.returncode)
        self.assertEqual("injected_final_evidence_fault", fault["classification"])
        self.assertFalse(canonical_page.exists())

        manifest_path = evidence["render_evidence_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_page = manifest_path.parent / manifest["pages"][0]["path"]
        preserved_source = run_dir / "待删除" / "issue100-pending-source-page.png"
        preserved_source.parent.mkdir(parents=True, exist_ok=True)
        source_page.replace(preserved_source)

        # scenario_id: pending_publication_with_missing_upstream_source
        # target_invariant: recovery precedes fresh-input validation
        # mutation_seam: after canonical is archived and before candidate publish
        # rematerialized_nodes: canonical predecessor; intentionally_stale_nodes: source page
        # expected_first_gate/error: final_evidence_page_reconciliation /
        # final_evidence_page_publication_restored; class: precedence
        restored, rejection = fixture._invoke_prepare(
            run_dir, control_root, evidence
        )

        self.assertEqual(20, restored.returncode)
        self.assertEqual(
            "final_evidence_page_publication_restored",
            rejection["data"]["error_code"],
        )
        self.assertEqual(preceding_bytes, canonical_page.read_bytes())
        preserved_source.replace(source_page)

        completed, prepared = fixture._invoke_prepare(run_dir, control_root, evidence)

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(preceding_bytes, canonical_page.read_bytes())
        self.assertTrue(Path(prepared["data"]["input_binding_path"]).is_file())

    def test_drifted_files_published_state_restores_preceding_page_set(
        self,
    ) -> None:
        fixture, run_dir, control_root, evidence = self._current_evidence()
        canonical_page = (
            run_dir
            / "review"
            / "acceptance"
            / "rendered_pages"
            / "page_0001.png"
        )
        preceding_bytes = self._commit_predecessor_and_open_revision(
            fixture, run_dir, control_root, evidence
        )

        # scenario_id: files_published_page_drift
        # target_invariant: canonical bytes match the FILES_PUBLISHED candidate
        # mutation_seam: after FILES_PUBLISHED and before COMMITTED
        # rematerialized_nodes: none; intentionally_stale_nodes: canonical page
        # expected_first_gate/error: final_evidence_page_reconciliation /
        # final_evidence_page_publication_restored; class: single_contradiction
        interrupted, fault = fixture._invoke_prepare(
            run_dir,
            control_root,
            evidence,
            fault_point="after_pages_published",
        )

        self.assertEqual(60, interrupted.returncode)
        self.assertEqual("injected_final_evidence_fault", fault["classification"])
        canonical_page.write_bytes(b"drifted-published-page")

        restored, rejection = fixture._invoke_prepare(
            run_dir, control_root, evidence
        )

        self.assertEqual(20, restored.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "final_evidence_page_reconciliation",
                "error_code": "final_evidence_page_publication_restored",
            },
            {
                "first_failing_gate": rejection["data"].get("first_failing_gate"),
                "error_code": rejection["data"].get("error_code"),
            },
        )
        self.assertEqual(preceding_bytes, canonical_page.read_bytes())
        self.assertTrue(
            any(
                path.read_bytes() == b"drifted-published-page"
                for path in (
                    run_dir / "待删除" / "final-evidence-publications"
                ).glob("revision-*/failed/rendered_pages/page_0001.png")
            )
        )

    def test_files_published_page_set_restores_then_reprepares_committed_binding(
        self,
    ) -> None:
        fixture, run_dir, control_root, evidence = self._current_evidence()
        canonical_page = (
            run_dir
            / "review"
            / "acceptance"
            / "rendered_pages"
            / "page_0001.png"
        )

        interrupted, fault = fixture._invoke_prepare(
            run_dir,
            control_root,
            evidence,
            fault_point="after_pages_published",
        )

        self.assertEqual(60, interrupted.returncode)
        self.assertEqual("injected_final_evidence_fault", fault["classification"])
        published_sha256 = final_evidence_tests._sha256(canonical_page)

        # scenario_id: files_published_publication_interrupted
        # target_invariant: FILES_PUBLISHED never remains the canonical authority
        # mutation_seam: after FILES_PUBLISHED and before COMMITTED
        # rematerialized_nodes: empty canonical set; intentionally_stale_nodes: none
        # expected_first_gate/error: final_evidence_page_reconciliation /
        # final_evidence_page_publication_restored; class: single_contradiction
        restored, rejection = fixture._invoke_prepare(
            run_dir, control_root, evidence
        )

        self.assertEqual(20, restored.returncode)
        self.assertEqual(
            "final_evidence_page_publication_restored",
            rejection["data"]["error_code"],
        )
        self.assertFalse(canonical_page.is_file())

        completed, prepared = fixture._invoke_prepare(run_dir, control_root, evidence)

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        binding = json.loads(
            Path(prepared["data"]["input_binding_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(published_sha256, binding["rendered_pages"][0]["sha256"])

    def test_missing_and_stale_source_pages_fail_before_reviewer_dispatch(
        self,
    ) -> None:
        fixture, run_dir, control_root, evidence = self._current_evidence()
        manifest_path = evidence["render_evidence_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_page = manifest_path.parent / manifest["pages"][0]["path"]
        preserved_page = run_dir / "待删除" / "issue100-missing-source-page.png"
        preserved_page.parent.mkdir(parents=True, exist_ok=True)
        source_page.replace(preserved_page)

        # scenario_id: source_page_missing
        # target_invariant: every manifest page exists at its declared source path
        # mutation_seam: before PREPARED; rematerialized_nodes: none
        # intentionally_stale_nodes: source page only
        # expected_first_gate/error: final_evidence_source_pages /
        # final_evidence_source_page_missing; class: single_contradiction
        missing, missing_error = fixture._invoke_prepare(
            run_dir, control_root, evidence
        )

        self.assertEqual(20, missing.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "final_evidence_source_pages",
                "error_code": "final_evidence_source_page_missing",
            },
            {
                "first_failing_gate": missing_error["data"].get(
                    "first_failing_gate"
                ),
                "error_code": missing_error["data"].get("error_code"),
            },
        )
        preserved_page.replace(source_page)
        source_page.write_bytes(source_page.read_bytes() + b"stale")

        # scenario_id: source_page_stale
        # target_invariant: source bytes match the manifest SHA-256
        # mutation_seam: before PREPARED; rematerialized_nodes: source page only
        # intentionally_stale_nodes: render manifest page SHA-256
        # expected_first_gate/error: final_evidence_source_pages /
        # final_evidence_source_page_stale; class: single_contradiction
        stale, stale_error = fixture._invoke_prepare(
            run_dir, control_root, evidence
        )

        self.assertEqual(40, stale.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "final_evidence_source_pages",
                "error_code": "final_evidence_source_page_stale",
            },
            {
                "first_failing_gate": stale_error["data"].get(
                    "first_failing_gate"
                ),
                "error_code": stale_error["data"].get("error_code"),
            },
        )

    def test_legacy_renderer_rejects_kernel_canonical_publication(self) -> None:
        _, run_dir, _, evidence = self._current_evidence()
        script = (
            PROJECT_ROOT
            / ".agents/skills/final-delivery-acceptance/scripts/render_pdf_pages.py"
        )

        # scenario_id: legacy_renderer_targets_kernel_root
        # target_invariant: Final Evidence is the sole Kernel canonical publisher
        # mutation_seam: legacy renderer entry; rematerialized_nodes: none
        # intentionally_stale_nodes: none
        # expected_first_gate/error: rendered_page_publication_owner /
        # kernel_rendered_page_publisher_forbidden; class: single_contradiction
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                str(evidence["final_pdf"]),
                "--video-output-dir",
                str(run_dir),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn(
            '"first_failing_gate": "rendered_page_publication_owner"',
            completed.stderr,
        )
        self.assertIn(
            '"error_code": "kernel_rendered_page_publisher_forbidden"',
            completed.stderr,
        )
        self.assertEqual(
            [],
            list(
                (run_dir / "review" / "acceptance" / "rendered_pages").glob(
                    "page_*.png"
                )
            ),
        )


if __name__ == "__main__":
    unittest.main()

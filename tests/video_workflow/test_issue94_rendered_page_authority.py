from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

from tests.video_workflow import test_guarded_final_compile_adapter as final_compile_tests
from tests.video_workflow import test_acceptance_v2 as acceptance_v2_tests
from tests.video_workflow import test_issue100_final_evidence_page_publication as issue100_tests
from tests.video_workflow import test_issue13_candidate_confirmation as candidate_tests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD_SCRIPTS = (
    PROJECT_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "scripts"
)
if str(GUARD_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(GUARD_SCRIPTS))

class Issue94RenderedPageAuthorityTests(unittest.TestCase):
    def test_final_compile_materializes_staged_rendered_pages(self) -> None:
        fixture = final_compile_tests.GuardedFinalCompileProviderAuthorityTests(
            methodName="test_public_final_compile_allows_unread_governance_entries"
        )
        fixture.setUp()

        workspace = fixture._run_public_final_compile_fixture(via_cli=True)

        video_root = workspace.parents[1]
        manifest_path = workspace / "render-evidence-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_page = workspace / "adapter-output" / "rendered_pages" / "page_001.png"
        self.assertTrue(expected_page.is_file())
        self.assertEqual(
            [{"page": 1, "path": "adapter-output/rendered_pages/page_001.png", "sha256": manifest["pages"][0]["sha256"]}],
            manifest["pages"],
        )
        self.assertEqual(
            [],
            list(
                (
                    video_root
                    / "review"
                    / "acceptance"
                    / "rendered_pages"
                ).glob("page_*.png")
            ),
        )

    def test_kernel_multi_page_guard_passes_without_coordinator_page_copy(self) -> None:
        fixture = issue100_tests.Issue100FinalEvidencePagePublicationTests(
            methodName="test_final_evidence_publishes_pages_before_reviewer_dispatch"
        )._fixture()
        run_dir, control_root = fixture._source_ready_v4_run()
        fixture._production_complete(
            run_dir,
            second_blank_page=True,
        )
        evidence = fixture._current_quality_evidence(run_dir)
        evidence["render_evidence_manifest"] = (
            evidence["final_compile_report"].parent / "render-evidence-manifest.json"
        )

        transition_fixture = candidate_tests.Issue13CandidateConfirmationTests(
            methodName="test_candidate_activation_rejects_generating_candidate"
        )
        ready_evidence = transition_fixture._transition_evidence(
            run_dir,
            from_stage="generating",
            to_stage="ready_for_delivery",
            artifacts={
                "final_pdf": evidence["final_pdf"],
                "main_tex": evidence["main_tex"],
                "final_compile_report": evidence["final_compile_report"],
                "render_evidence_manifest": evidence["render_evidence_manifest"],
            },
        )
        run = json.loads((run_dir / "workflow" / "run.json").read_text(encoding="utf-8"))
        fixture._require_ok(
            "delivery-transition",
            "--run-dir", str(run_dir),
            "--from-stage", "generating",
            "--to-stage", "ready_for_delivery",
            "--session-id", "session-issue100",
            "--expected-run-revision", str(run["coordination_revision"]),
            "--expected-ownership-generation", str(run["delivery"]["ownership"]["generation"]),
            "--evidence", str(ready_evidence),
            "--transitioned-at", "2026-08-30T01:00:00Z",
        )

        prepared_command, prepared = fixture._invoke_prepare(
            run_dir, control_root, evidence
        )
        self.assertEqual(
            0,
            prepared_command.returncode,
            prepared_command.stdout + prepared_command.stderr,
        )
        input_binding = Path(prepared["data"]["input_binding_path"])
        fixture._require_ok(
            "acceptance-final-authority-publish",
            "--input-binding", str(input_binding),
        )
        acceptance_root = run_dir / "review" / "acceptance"
        fixture._require_ok(
            "acceptance-prepare",
            "--workspace-root", str(acceptance_root),
            "--input-binding", str(input_binding),
            "--attempt-number", "1",
            "--prepared-at", "2026-08-30T01:01:00Z",
            "--coordinator-session", "session-issue100",
        )
        acceptance_fixture = acceptance_v2_tests.AcceptanceV2CliTests(
            methodName="test_complete_current_evidence_materializes_all_catalog_rules_and_guard_eligibility"
        )
        patch_path = acceptance_fixture.patch(acceptance_root)
        fixture._require_ok(
            "acceptance-patch-commit",
            "--workspace-root", str(acceptance_root),
            "--dimension", "visual_quality",
            "--patch", str(patch_path),
            "--committed-at", "2026-08-30T01:02:00Z",
        )
        fixture._require_ok(
            "acceptance-materialize",
            "--workspace-root", str(acceptance_root),
            "--provider-id", "acceptance-v2-provider",
            "--provider-version", "1.0.0",
            "--materialized-at", "2026-08-30T01:03:00Z",
        )
        report_path = acceptance_root / "acceptance_report.json"
        run = json.loads((run_dir / "workflow" / "run.json").read_text(encoding="utf-8"))
        fixture._require_ok(
            "delivery-acceptance-bind",
            "--run-dir", str(run_dir),
            "--session-id", "session-issue100",
            "--acceptance-report", str(report_path),
            "--expected-run-revision", str(run["coordination_revision"]),
            "--expected-ownership-generation", str(run["delivery"]["ownership"]["generation"]),
            "--bound-at", "2026-08-30T01:04:00Z",
        )
        run = json.loads((run_dir / "workflow" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("accepted", run["delivery"]["stage"])
        current_target = Path(run["delivery"]["projections"]["session_target"]["path"])
        completed = subprocess.run(
            [
                sys.executable,
                "-X", "utf8", "-B",
                str(GUARD_SCRIPTS / "delivery_guard.py"),
                "check",
                "--project-root", str(current_target.parents[4]),
                "--current-target", str(current_target),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        guard_report = json.loads(
            (acceptance_root / "delivery_guard_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual("pass", guard_report["status"])
        self.assertEqual(
            [1, 2],
            [
                page["page"]
                for page in json.loads(input_binding.read_text(encoding="utf-8"))[
                    "rendered_pages"
                ]
            ],
        )


if __name__ == "__main__":
    unittest.main()

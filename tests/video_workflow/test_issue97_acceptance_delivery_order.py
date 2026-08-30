from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from tests.video_workflow import test_acceptance_v2 as acceptance_v2_tests
from tests.video_workflow import test_issue100_final_evidence_page_publication as issue100_tests
from tests.video_workflow import test_issue13_candidate_confirmation as candidate_tests
from tests.video_workflow import test_issue95_profile_backed_delivery as profile_delivery_tests
from video2pdf_workflow_kernel.acceptance_v2 import AcceptanceV2Provider
from video2pdf_workflow_kernel.delivery_acceptance_binding import (
    DeliveryAcceptanceBindingProvider,
)
from video2pdf_workflow_kernel.errors import ContractError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD_SCRIPTS = (
    PROJECT_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "scripts"
)


class Issue97AcceptanceDeliveryOrderTests(unittest.TestCase):
    # Fixture graph and validator-migration impact list:
    # - authority inputs: current Run, final evidence, Acceptance Patch, report, Guard;
    # - derived nodes: delivery projections, Acceptance execution, report, Guard report;
    # - boundaries: ready transition -> report publication -> acceptance bind -> Guard -> delivered;
    # - ordered gates: bindable revision, provider authority, lifecycle bind, Guard, delivered;
    # - observations: one execution id and terminal delivered stage.
    # Positive fixture: this complete public flow. Negative fixtures: two bounded
    # provider-contract cases plus failed-attempt repair preflight. Shared Issue #100
    # builders and derived snapshots are unchanged. First-gate assertions are
    # run_lifecycle / unbindable and projection_currentness / stale. No precedence
    # scenario changes. Focused #97 tests run now; the complete suite remains deferred
    # by the operator until all Spec #83 issues are complete.

    def test_prepare_contract_rejects_non_ready_revision_before_dispatch(self) -> None:
        run = {"delivery": {"stage": "generating"}}
        target = {
            "stage": "ready_for_delivery",
            "artifacts": {"acceptance_report": None, "delivery_guard_report": None},
        }

        with self.assertRaises(ContractError) as raised:
            DeliveryAcceptanceBindingProvider._require_bindable_target(run, target)

        self.assertEqual(
            {
                "first_failing_gate": "run_lifecycle",
                "error_code": "acceptance_delivery_revision_unbindable",
            },
            raised.exception.data,
        )

    def test_prepare_contract_rejects_occupied_decision_slot_before_dispatch(self) -> None:
        run = {"delivery": {"stage": "ready_for_delivery"}}
        target = {
            "stage": "ready_for_delivery",
            "artifacts": {
                "acceptance_report": {"path": "stale.json", "sha256": "0" * 64},
                "delivery_guard_report": None,
            },
        }

        with self.assertRaises(ContractError) as raised:
            DeliveryAcceptanceBindingProvider._require_bindable_target(run, target)

        self.assertEqual(
            {
                "first_failing_gate": "run_lifecycle",
                "error_code": "acceptance_delivery_revision_unbindable",
            },
            raised.exception.data,
        )

    def test_repair_prepare_rechecks_bindability_before_dispatch(self) -> None:
        fixture = acceptance_v2_tests.AcceptanceV2CliTests(
            methodName="test_repair_requires_fresh_artifact_generation_and_bounds_three_failures"
        )
        root = acceptance_v2_tests.new_case_dir(
            self.id(), label="issue97-repair-preflight"
        )
        workspace = root / "review" / "acceptance"
        initial_binding = fixture.build_binding(root, 1)
        provider = AcceptanceV2Provider(PROJECT_ROOT)
        with mock.patch.object(
            provider, "_preflight_delivery_binding", return_value=None
        ):
            provider.prepare(
                workspace_root=workspace,
                input_binding_path=initial_binding,
                attempt_number=1,
                prepared_at="2026-08-30T00:00:00Z",
                coordinator_session="coordinator-session",
            )
        fixture.commit_visual(workspace, decision="fail")
        materialized, _ = fixture.materialize(workspace)
        self.assertEqual(0, materialized.returncode, materialized.stderr)
        successor = fixture.build_binding(root, 2)
        executions_before = sorted((workspace / "executions").iterdir())
        rejection = ContractError(
            "repair successor cannot bind",
            data={
                "first_failing_gate": "projection_currentness",
                "error_code": "delivery_projection_stale",
            },
        )

        with mock.patch.object(
            provider, "_preflight_delivery_binding", side_effect=rejection
        ) as preflight:
            with self.assertRaises(ContractError) as raised:
                provider.prepare_repair(
                    workspace_root=workspace,
                    input_binding_path=successor,
                    prepared_at="2026-08-30T00:30:00Z",
                    coordinator_session="coordinator-session",
                )

        self.assertEqual(rejection.data, raised.exception.data)
        preflight.assert_called_once()
        repair_domain = preflight.call_args.kwargs["domain"]
        self.assertEqual(2, repair_domain.run["acceptance_revision"])
        self.assertEqual(
            executions_before,
            sorted((workspace / "executions").iterdir()),
        )

    def test_one_acceptance_execution_reaches_delivered_through_public_cli(self) -> None:
        fixture = issue100_tests.Issue100FinalEvidencePagePublicationTests(
            methodName="test_final_evidence_publishes_pages_before_reviewer_dispatch"
        )._fixture()
        run_dir, control_root = fixture._source_ready_v4_run()
        fixture._production_complete(run_dir, second_blank_page=True)
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
        ready = fixture._require_ok(
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
        self.assertEqual("ready_for_delivery", ready["data"]["stage"])

        prepared_command, prepared = fixture._invoke_prepare(run_dir, control_root, evidence)
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
        session_target = Path(
            json.loads(
                (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
            )["delivery"]["projections"]["session_target"]["path"]
        )
        session_bytes = session_target.read_bytes()
        session_target.write_bytes(session_bytes.rstrip(b"\n") + b" \n")
        try:
            # scenario_id: stale_session_projection_before_reviewer_dispatch
            # target_invariant: session projection bytes match the Run binding
            # mutation_seam: after final authority publication, before prepare
            # rematerialized_nodes: none
            # intentionally_stale_nodes: session delivery target only
            # expected_first_gate/error: projection_currentness / delivery_projection_stale
            # scenario_class: single_contradiction
            stale_command, stale = fixture._cli(
                "acceptance-prepare",
                "--workspace-root", str(acceptance_root),
                "--input-binding", str(input_binding),
                "--attempt-number", "1",
                "--prepared-at", "2026-08-30T01:00:30Z",
                "--coordinator-session", "session-issue100",
            )
            self.assertNotEqual(0, stale_command.returncode)
            self.assertEqual(
                {
                    "first_failing_gate": "projection_currentness",
                    "error_code": "delivery_projection_stale",
                },
                {
                    "first_failing_gate": stale["data"]["first_failing_gate"],
                    "error_code": stale["data"]["error_code"],
                },
            )
            self.assertFalse((acceptance_root / "current.json").exists())
        finally:
            session_target.write_bytes(session_bytes)
        fixture._require_ok(
            "acceptance-prepare",
            "--workspace-root", str(acceptance_root),
            "--input-binding", str(input_binding),
            "--attempt-number", "1",
            "--prepared-at", "2026-08-30T01:01:00Z",
            "--coordinator-session", "session-issue100",
        )
        execution_id = json.loads(
            (acceptance_root / "current.json").read_text(encoding="utf-8")
        )["execution_id"]
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
        self.assertEqual(
            [execution_id],
            sorted(path.name for path in (acceptance_root / "executions").iterdir()),
        )

        current_target = Path(run["delivery"]["projections"]["session_target"]["path"])
        guarded = subprocess.run(
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
        self.assertEqual(0, guarded.returncode, guarded.stdout + guarded.stderr)
        guard_report = acceptance_root / "delivery_guard_report.json"
        delivered_evidence = transition_fixture._transition_evidence(
            run_dir,
            from_stage="accepted",
            to_stage="delivered",
            artifacts={"delivery_guard_report": guard_report},
        )
        delivered_command, delivered_envelope = profile_delivery_tests._run_cli(
            "delivery-transition",
            "--run-dir", str(run_dir),
            "--from-stage", "accepted",
            "--to-stage", "delivered",
            "--session-id", "session-issue100",
            "--expected-run-revision", str(run["coordination_revision"]),
            "--expected-ownership-generation", str(run["delivery"]["ownership"]["generation"]),
            "--evidence", str(delivered_evidence),
            "--transitioned-at", "2026-08-30T01:05:00Z",
            guard_authority=True,
        )
        self.assertEqual(
            0,
            delivered_command.returncode,
            delivered_command.stdout + delivered_command.stderr,
        )
        self.assertEqual("delivered", delivered_envelope["data"]["stage"])
        delivered = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual("delivered", delivered["delivery"]["stage"])


if __name__ == "__main__":
    unittest.main()

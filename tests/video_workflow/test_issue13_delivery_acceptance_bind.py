from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]

from tests.video_workflow import test_acceptance_v2 as acceptance_v2_test  # noqa: E402
from tests.video_workflow import (  # noqa: E402
    test_issue13_candidate_confirmation as candidate_test,
)
from tests.video_workflow import test_issue13_cold_start_cutover as cold_start_test  # noqa: E402
from tests.video_workflow import test_issue13_delivery_lifecycle as lifecycle_test  # noqa: E402
from video2pdf_workflow_kernel.contracts import ContractRegistry  # noqa: E402
from video2pdf_workflow_kernel.control_store import ControlStore  # noqa: E402
from video2pdf_workflow_kernel.cli import main as workflow_cli_main  # noqa: E402
from video2pdf_workflow_kernel.delivery_acceptance_binding import (  # noqa: E402
    DeliveryAcceptanceBindingProvider,
)


PROVIDER_RECORDING = (
    PROJECT_ROOT
    / "tests"
    / "video_workflow"
    / "fixtures"
    / "providers"
    / "bilibili"
    / "fresh-download"
)


def _run_in_process_public_cli(*arguments: str) -> tuple[int, dict]:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        returncode = workflow_cli_main(list(arguments))
    return returncode, json.loads(stdout.getvalue())


class Issue13DeliveryAcceptanceBindTests(unittest.TestCase):
    """Acceptance registration is tested only through the public Workflow CLI."""

    def _materialize_provider_current_ready_candidate(
        self,
    ) -> tuple[Path, Path, Path, int, int]:
        cold_start = cold_start_test.Issue13ColdStartCutoverTests(
            "test_prepared_candidate_can_initialize_v4_through_public_cli"
        )
        control_root, workspace, probe_path, implementation_commit = (
            cold_start._cold_start_case()
        )
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        probe.update(
            {
                "canonical_item_id": "BV1TEST00001:p1",
                "source_identity": (
                    "51b5b6809799e799b780ea3dcbf50322d5ada3dae052fe50e0da65e98f328129"
                ),
                "original_title": "Bilibili Adapter Fixture",
                "source_request": {
                    "kind": "fresh_download",
                    "canonical_locator": (
                        "https://www.bilibili.com/video/BV1TEST00001/"
                    ),
                },
            }
        )
        probe_path.write_text(
            json.dumps(probe, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        cold_start._prepare_candidate(
            control_store_root=control_root,
            probe_path=probe_path,
            implementation_commit=implementation_commit,
        )
        initialized = candidate_test._run_public_cli(
            self.id() + "-candidate-init",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control_root),
            "--probe",
            str(probe_path),
            "--session-id",
            candidate_test.CANDIDATE_SESSION_ID,
        )
        self.assertEqual(
            0,
            initialized.returncode,
            initialized.stdout + initialized.stderr,
        )
        run_dir = Path(json.loads(initialized.stdout)["data"]["run_dir"])
        candidate = candidate_test.Issue13CandidateConfirmationTests(
            "test_candidate_activation_rejects_generating_candidate"
        )

        cookie_file = control_root.parent / "credentials" / "bilibili-cookies.txt"
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\trecorded\n",
            encoding="utf-8",
        )
        faulted = candidate_test._run_public_cli(
            self.id() + "-source-acquire-faulted",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(PROVIDER_RECORDING),
            "--fault-point",
            "after_provider_terminal_proof_persisted",
        )
        self.assertNotEqual(0, faulted.returncode)
        reconciled = candidate_test._run_public_cli(
            self.id() + "-source-acquire-reconcile",
            "source-acquire-reconcile",
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(
            0,
            reconciled.returncode,
            reconciled.stdout + reconciled.stderr,
        )
        acquired = candidate_test._run_public_cli(
            self.id() + "-source-acquire",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(PROVIDER_RECORDING),
        )
        self.assertEqual(0, acquired.returncode, acquired.stdout + acquired.stderr)

        acceptance = acceptance_v2_test.AcceptanceV2CliTests(
            "test_prepare_materializes_exact_read_only_reviewer_task_envelope"
        )

        def candidate_run_authority(
            _fixture: acceptance_v2_test.AcceptanceV2CliTests, root: Path
        ) -> tuple[dict, Path, Path]:
            run_path = root / "workflow" / "run.json"
            return (
                json.loads(run_path.read_text(encoding="utf-8")),
                run_path,
                root.parent,
            )

        acceptance.ensure_run_authority = types.MethodType(  # type: ignore[method-assign]
            candidate_run_authority,
            acceptance,
        )

        # First materialization creates the exact artifacts consumed by the
        # normal ready_for_delivery transition.  Publication is deferred until
        # the transition's Run revision exists.
        draft_binding_path = acceptance.build_binding(
            run_dir,
            1,
            publish_authority=False,
        )
        draft_binding = json.loads(draft_binding_path.read_text(encoding="utf-8"))
        artifacts = {
            item["logical_id"]: Path(item["path"])
            for item in draft_binding["artifacts"]
        }
        quality = {
            logical_id: Path(item["path"])
            for logical_id, item in draft_binding["quality_inputs"].items()
        }
        ready_evidence = candidate._transition_evidence(
            run_dir,
            from_stage="generating",
            to_stage="ready_for_delivery",
            artifacts={
                "final_pdf": artifacts["final_pdf"],
                "main_tex": artifacts["main_tex"],
                "final_compile_report": quality["final_compile_manifest"],
                "render_evidence_manifest": quality["render_evidence_manifest"],
            },
        )
        before_ready = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        ready = candidate_test._run_public_cli(
            self.id() + "-ready",
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "generating",
            "--to-stage",
            "ready_for_delivery",
            "--session-id",
            candidate_test.CANDIDATE_SESSION_ID,
            "--expected-run-revision",
            str(before_ready["coordination_revision"]),
            "--expected-ownership-generation",
            str(before_ready["delivery"]["ownership"]["generation"]),
            "--evidence",
            str(ready_evidence),
            "--transitioned-at",
            "2026-08-11T02:00:00Z",
        )
        self.assertEqual(0, ready.returncode, ready.stdout + ready.stderr)

        # Re-materialize the fixture from the new Run authority.  The provider
        # owns preparation, Patch commit, report publication, and eligibility.
        binding_path = acceptance.build_binding(run_dir, 1)
        acceptance_root = run_dir / "review" / "acceptance"
        prepared = candidate_test._run_public_cli(
            self.id() + "-acceptance-prepare",
            "acceptance-prepare",
            "--workspace-root",
            str(acceptance_root),
            "--input-binding",
            str(binding_path),
            "--attempt-number",
            "1",
            "--prepared-at",
            "2026-08-11T02:01:00Z",
            "--coordinator-session",
            candidate_test.CANDIDATE_SESSION_ID,
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        acceptance.commit_visual(acceptance_root)
        materialized, materialized_envelope = acceptance.materialize(acceptance_root)
        self.assertEqual(
            0,
            materialized.returncode,
            materialized.stdout + materialized.stderr,
        )

        report_path = acceptance_root / "acceptance_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        guarded = candidate_test._run_public_cli(
            self.id() + "-acceptance-eligible",
            "acceptance-guard-eligibility",
            "--workspace-root",
            str(acceptance_root),
        )
        self.assertEqual(0, guarded.returncode, guarded.stdout + guarded.stderr)
        eligibility = json.loads(guarded.stdout)["data"]
        self.assertTrue(eligibility["eligible"])
        self.assertTrue(eligibility["delivery_authority"])
        self.assertEqual(report["report_sha256"], eligibility["report_sha256"])
        self.assertEqual(
            report["report_sha256"],
            materialized_envelope["data"]["report_sha256"],
        )

        run = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual("4.0.0", run["schema_version"])
        self.assertEqual("ready_for_delivery", run["delivery"]["stage"])
        self.assertEqual(
            run["coordination_revision"],
            report["run_binding"]["coordination_revision"],
        )
        return (
            control_root,
            run_dir,
            report_path,
            run["coordination_revision"],
            run["delivery"]["ownership"]["generation"],
        )

    def _authority_paths(self, run_dir: Path) -> dict[str, Path]:
        run_path = run_dir / "workflow" / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        paths = {"run": run_path}
        for name in ("video_target", "session_target", "task_index"):
            path = Path(run["delivery"]["projections"][name]["path"])
            paths[name] = path if path.is_absolute() else run_dir / path
        return paths

    def _authority_snapshot(self, run_dir: Path) -> dict[str, object]:
        paths = self._authority_paths(run_dir)
        control_path = run_dir.parent / ".workflow-control" / "control.sqlite3"
        with sqlite3.connect(f"file:{control_path.as_posix()}?mode=ro", uri=True) as db:
            intents = db.execute(
                "SELECT intent_id,run_id,session_id,expected_run_revision,"
                "expected_ownership_generation,prior_stage,target_stage,operation,state "
                "FROM delivery_lifecycle_intents ORDER BY intent_id"
            ).fetchall()
            slots = db.execute(
                "SELECT intent_id,normalized_path,expected_state,expected_sha256,"
                "proposed_state,proposed_sha256,state "
                "FROM projection_publication_slots ORDER BY intent_id,normalized_path"
            ).fetchall()
        return {
            "files": {name: path.read_bytes() for name, path in paths.items()},
            "intents": intents,
            "slots": slots,
        }

    def _bind_arguments(
        self,
        run_dir: Path,
        report_path: Path,
        revision: int,
        ownership_generation: int,
    ) -> tuple[str, ...]:
        return (
            "delivery-acceptance-bind",
            "--run-dir",
            str(run_dir),
            "--session-id",
            candidate_test.CANDIDATE_SESSION_ID,
            "--acceptance-report",
            str(report_path),
            "--expected-run-revision",
            str(revision),
            "--expected-ownership-generation",
            str(ownership_generation),
            "--bound-at",
            "2026-08-11T02:02:00Z",
        )

    def test_public_cli_binds_provider_current_acceptance_as_exact_ready_successor(
        self,
    ) -> None:
        control_root, run_dir, report_path, revision, ownership_generation = (
            self._materialize_provider_current_ready_candidate()
        )
        before = self._authority_snapshot(run_dir)
        arguments = self._bind_arguments(
            run_dir,
            report_path,
            revision,
            ownership_generation,
        )

        completed = candidate_test._run_public_cli(self.id() + "-bind", *arguments)

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        envelope = json.loads(completed.stdout)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        paths = self._authority_paths(run_dir)
        run = json.loads(paths["run"].read_text(encoding="utf-8"))
        video = json.loads(paths["video_target"].read_text(encoding="utf-8"))
        session = json.loads(paths["session_target"].read_text(encoding="utf-8"))
        task_index = json.loads(paths["task_index"].read_text(encoding="utf-8"))
        task_entry = next(
            item for item in task_index["entries"] if item["run_id"] == run["run_id"]
        )

        self.assertEqual("delivery_acceptance_bound", envelope["classification"])
        self.assertFalse(envelope["data"]["idempotent"])
        self.assertEqual("ready_for_delivery", video["stage"])
        self.assertEqual(revision + 1, run["coordination_revision"])
        self.assertEqual(
            report["run_binding"]["coordination_revision"] + 1,
            run["coordination_revision"],
        )
        self.assertEqual(
            {
                "path": str(report_path.resolve()),
                "sha256": candidate_test._sha256(report_path),
            },
            video["artifacts"]["acceptance_report"],
        )
        self.assertIsNone(video["artifacts"]["delivery_guard_report"])
        self.assertEqual(video["projection_revision"], session["projection_revision"])
        self.assertEqual(video["projection_revision"], task_entry["video_target"]["projection_revision"])
        self.assertEqual(candidate_test._sha256(paths["video_target"]), session["video_target"]["sha256"])
        self.assertEqual(candidate_test._sha256(paths["video_target"]), task_entry["video_target"]["sha256"])
        self.assertEqual(candidate_test._sha256(paths["session_target"]), task_entry["session_target"]["sha256"])
        store = ControlStore(run_dir.parent, ContractRegistry(PROJECT_ROOT))
        self.assertEqual(
            candidate_test._sha256(paths["run"]),
            store.current_run_record_sha(run["run_id"]),
        )
        guarded_returncode, guarded_envelope = _run_in_process_public_cli(
            "acceptance-guard-eligibility",
            "--workspace-root",
            str(report_path.parent),
        )
        self.assertEqual(0, guarded_returncode, guarded_envelope)
        post_bind_eligibility = guarded_envelope["data"]
        self.assertTrue(post_bind_eligibility["eligible"])
        self.assertEqual(
            report["report_sha256"],
            post_bind_eligibility["report_sha256"],
        )
        activation_returncode, activation_envelope = _run_in_process_public_cli(
            "platform-kernel-candidate-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_root),
            "--candidate-run-dir",
            str(run_dir),
            "--activated-at",
            "2026-08-11T02:03:00Z",
        )
        self.assertEqual(0, activation_returncode, activation_envelope)
        self.assertEqual("PROVISIONAL", activation_envelope["data"]["cutover_state"])

        after_first = self._authority_snapshot(run_dir)
        self.assertNotEqual(before, after_first)
        new_intents = [row for row in after_first["intents"] if row not in before["intents"]]
        self.assertEqual(1, len(new_intents))
        self.assertEqual(
            (revision, ownership_generation, "ready_for_delivery", "ready_for_delivery", "transition", "COMMITTED"),
            (
                new_intents[0][3],
                new_intents[0][4],
                new_intents[0][5],
                new_intents[0][6],
                new_intents[0][7],
                new_intents[0][8],
            ),
        )
        new_slots = [row for row in after_first["slots"] if row not in before["slots"]]
        self.assertEqual(4, len(new_slots))
        self.assertEqual({"RELEASED"}, {row[6] for row in new_slots})
        self.assertEqual(
            {str(path.resolve()).casefold() for path in paths.values()},
            {row[1] for row in new_slots},
        )

        retry_returncode, retry_envelope = _run_in_process_public_cli(*arguments)
        self.assertEqual(0, retry_returncode, retry_envelope)
        self.assertTrue(retry_envelope["data"]["idempotent"])
        self.assertEqual(after_first, self._authority_snapshot(run_dir))

    def test_public_accepted_transition_consumes_exact_committed_ready_successor(
        self,
    ) -> None:
        control_root, run_dir, report_path, revision, ownership_generation = (
            self._materialize_provider_current_ready_candidate()
        )
        bind_returncode, bind_envelope = _run_in_process_public_cli(
            *self._bind_arguments(
                run_dir,
                report_path,
                revision,
                ownership_generation,
            )
        )
        self.assertEqual(0, bind_returncode, bind_envelope)
        activation_returncode, activation_envelope = _run_in_process_public_cli(
            "platform-kernel-candidate-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_root),
            "--candidate-run-dir",
            str(run_dir),
            "--activated-at",
            "2026-08-11T02:03:00Z",
        )
        self.assertEqual(0, activation_returncode, activation_envelope)
        self.assertEqual("PROVISIONAL", activation_envelope["data"]["cutover_state"])

        candidate = candidate_test.Issue13CandidateConfirmationTests(
            "test_candidate_activation_rejects_generating_candidate"
        )
        accepted_evidence = candidate._transition_evidence(
            run_dir,
            from_stage="ready_for_delivery",
            to_stage="accepted",
            artifacts={"acceptance_report": report_path},
        )
        accepted_arguments = (
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "ready_for_delivery",
            "--to-stage",
            "accepted",
            "--session-id",
            candidate_test.CANDIDATE_SESSION_ID,
            "--expected-run-revision",
            str(revision + 1),
            "--expected-ownership-generation",
            str(ownership_generation),
            "--evidence",
            str(accepted_evidence),
            "--transitioned-at",
            "2026-08-11T02:04:00Z",
        )

        # Fixture graph: report predecessor R -> committed ready successor R+1
        # -> public accepted transition.  Each negative mutates one boundary
        # node, asserts the provider's first failing gate, then restores it.
        authority_paths = self._authority_paths(run_dir)
        public_bytes = {
            name: path.read_bytes() for name, path in authority_paths.items()
        }
        ready_run = json.loads(public_bytes["run"])
        bind_intent_id = ready_run["last_mutation_intent_id"]
        lifecycle_control = run_dir.parent / ".workflow-control" / "control.sqlite3"

        # scenario_id: uncommitted_ready_successor
        # target_invariant: the ready R+1 lifecycle intent must be COMMITTED
        # mutation_seam: committed intent state; rematerialized_nodes: none
        # expected_first_gate/code: run_lifecycle / successor_uncommitted
        with sqlite3.connect(lifecycle_control) as db:
            db.execute(
                "UPDATE delivery_lifecycle_intents SET state='RECORD_COMMITTED' "
                "WHERE intent_id=?",
                (bind_intent_id,),
            )
        try:
            uncommitted_code, uncommitted = _run_in_process_public_cli(
                *accepted_arguments
            )
            self.assertNotEqual(0, uncommitted_code)
            self.assertEqual(
                "run_lifecycle", uncommitted["data"]["first_failing_gate"]
            )
            self.assertEqual(
                "acceptance_delivery_successor_uncommitted",
                uncommitted["data"]["error_code"],
            )
            self.assertEqual(
                public_bytes,
                {name: path.read_bytes() for name, path in authority_paths.items()},
            )
        finally:
            with sqlite3.connect(lifecycle_control) as db:
                db.execute(
                    "UPDATE delivery_lifecycle_intents SET state='COMMITTED' "
                    "WHERE intent_id=?",
                    (bind_intent_id,),
                )

        # scenario_id: stale_ready_predecessor
        # target_invariant: the journal must preserve the exact report-bound R
        # mutation_seam: prior Run bytes; rematerialized_nodes: none by design
        # expected_first_gate/code: run_lifecycle / successor_uncommitted
        prior_run_path = (
            run_dir
            / "待删除"
            / "delivery-lifecycle"
            / bind_intent_id
            / "prior"
            / "03-run.json"
        )
        prior_run_bytes = prior_run_path.read_bytes()
        prior_run_path.write_bytes(prior_run_bytes.rstrip(b"\n") + b" \n")
        try:
            stale_code, stale = _run_in_process_public_cli(*accepted_arguments)
            self.assertNotEqual(0, stale_code)
            self.assertEqual("run_lifecycle", stale["data"]["first_failing_gate"])
            self.assertEqual(
                "acceptance_delivery_successor_uncommitted",
                stale["data"]["error_code"],
            )
            self.assertEqual(
                public_bytes,
                {name: path.read_bytes() for name, path in authority_paths.items()},
            )
        finally:
            prior_run_path.write_bytes(prior_run_bytes)

        accepted_returncode, accepted_envelope = _run_in_process_public_cli(
            *accepted_arguments
        )

        self.assertEqual(0, accepted_returncode, accepted_envelope)
        paths = self._authority_paths(run_dir)
        run = json.loads(paths["run"].read_text(encoding="utf-8"))
        video = json.loads(paths["video_target"].read_text(encoding="utf-8"))
        session = json.loads(paths["session_target"].read_text(encoding="utf-8"))
        task_index = json.loads(paths["task_index"].read_text(encoding="utf-8"))
        task_entry = next(
            item for item in task_index["entries"] if item["run_id"] == run["run_id"]
        )
        report_authority = {
            "path": str(report_path.resolve()),
            "sha256": candidate_test._sha256(report_path),
        }
        self.assertEqual(revision + 2, run["coordination_revision"])
        self.assertEqual(
            {"accepted"},
            {run["delivery"]["stage"], video["stage"], session["stage"], task_entry["stage"]},
        )
        self.assertEqual(report_authority, video["artifacts"]["acceptance_report"])
        for name in ("video_target", "session_target", "task_index"):
            self.assertEqual(
                candidate_test._sha256(paths[name]),
                run["delivery"]["projections"][name]["sha256"],
            )
        store = ControlStore(run_dir.parent, ContractRegistry(PROJECT_ROOT))
        self.assertEqual(
            candidate_test._sha256(paths["run"]),
            store.current_run_record_sha(run["run_id"]),
        )

    def test_public_cli_fences_expected_revision_drift_without_any_bind_write(
        self,
    ) -> None:
        _control_root, run_dir, report_path, revision, ownership_generation = (
            self._materialize_provider_current_ready_candidate()
        )
        before_stale_bind = self._authority_snapshot(run_dir)

        completed = candidate_test._run_public_cli(
            self.id() + "-stale-bind",
            *self._bind_arguments(
                run_dir,
                report_path,
                revision - 1,
                ownership_generation,
            ),
        )

        self.assertNotEqual(0, completed.returncode)
        envelope = json.loads(completed.stdout)
        self.assertEqual(
            {
                "first_failing_gate": "lifecycle_fencing",
                "error_code": "delivery_acceptance_bind_fence_lost",
            },
            {
                "first_failing_gate": envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": envelope.get("data", {}).get("error_code"),
            },
        )
        self.assertEqual(before_stale_bind, self._authority_snapshot(run_dir))

    def test_report_drift_after_provider_validation_aborts_before_successor_commit(
        self,
    ) -> None:
        _control, run_dir, report_path, revision, ownership_generation = (
            self._materialize_provider_current_ready_candidate()
        )
        before_bind = self._authority_snapshot(run_dir)
        original_report = report_path.read_bytes()
        provider_validated = threading.Event()
        release_bind = threading.Event()
        real_validator = (
            DeliveryAcceptanceBindingProvider._require_provider_current_report
        )

        def pause_after_provider_validation(**kwargs: object) -> tuple[dict, dict]:
            result = real_validator(**kwargs)
            provider_validated.set()
            if not release_bind.wait(timeout=10):
                raise TimeoutError("report drift barrier was not released")
            return result

        arguments = self._bind_arguments(
            run_dir,
            report_path,
            revision,
            ownership_generation,
        )
        with patch.object(
            DeliveryAcceptanceBindingProvider,
            "_require_provider_current_report",
            side_effect=pause_after_provider_validation,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_in_process_public_cli, *arguments)
                self.assertTrue(provider_validated.wait(timeout=30))
                report_path.write_bytes(original_report.rstrip(b"\n") + b" \n")
                release_bind.set()
                completed, envelope = future.result(timeout=30)

        self.assertNotEqual(0, completed)
        self.assertEqual(
            {
                "first_failing_gate": "acceptance_provider_authority",
                "error_code": "delivery_acceptance_report_drifted",
            },
            {
                "first_failing_gate": envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": envelope.get("data", {}).get("error_code"),
            },
        )
        self.assertEqual(before_bind, self._authority_snapshot(run_dir))

        report_path.write_bytes(original_report)
        retried, retry_envelope = _run_in_process_public_cli(*arguments)
        self.assertEqual(0, retried, retry_envelope)
        self.assertFalse(retry_envelope["data"]["idempotent"])

    def test_malformed_canonical_report_has_stable_provider_authority_error(
        self,
    ) -> None:
        fixture = lifecycle_test.Issue13DeliveryLifecycleTests(
            "test_generating_to_ready_commits_run_last_and_refreshes_all_projections"
        )
        run_dir = fixture._make_ready_run()
        run = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        report_path = run_dir / "review" / "acceptance" / "acceptance_report.json"
        report_path.write_bytes(b"{\n")
        before_bind = self._authority_snapshot(run_dir)

        completed, envelope = _run_in_process_public_cli(
            *self._bind_arguments(
                run_dir,
                report_path,
                run["coordination_revision"],
                run["delivery"]["ownership"]["generation"],
            )
        )

        self.assertNotEqual(0, completed)
        self.assertEqual(
            {
                "first_failing_gate": "acceptance_provider_authority",
                "error_code": "delivery_acceptance_report_malformed",
            },
            {
                "first_failing_gate": envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": envelope.get("data", {}).get("error_code"),
            },
        )
        self.assertEqual(before_bind, self._authority_snapshot(run_dir))

    def test_report_drift_before_run_commit_restores_predecessor_and_retries(
        self,
    ) -> None:
        _control, run_dir, report_path, revision, ownership_generation = (
            self._materialize_provider_current_ready_candidate()
        )
        before_bind = self._authority_snapshot(run_dir)
        original_report = report_path.read_bytes()
        commit_fence_reached = threading.Event()
        release_bind = threading.Event()
        real_snapshot_check = (
            DeliveryAcceptanceBindingProvider._require_report_snapshot
        )
        checks = 0

        def pause_at_commit_fence(**kwargs: object) -> None:
            nonlocal checks
            checks += 1
            if checks == 2:
                commit_fence_reached.set()
                if not release_bind.wait(timeout=10):
                    raise TimeoutError("Run commit report fence was not released")
            real_snapshot_check(**kwargs)

        arguments = self._bind_arguments(
            run_dir,
            report_path,
            revision,
            ownership_generation,
        )
        with patch.object(
            DeliveryAcceptanceBindingProvider,
            "_require_report_snapshot",
            side_effect=pause_at_commit_fence,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_in_process_public_cli, *arguments)
                self.assertTrue(commit_fence_reached.wait(timeout=30))
                report_path.write_bytes(original_report.rstrip(b"\n") + b" \n")
                release_bind.set()
                completed, envelope = future.result(timeout=30)

        self.assertNotEqual(0, completed)
        self.assertEqual(
            {
                "first_failing_gate": "acceptance_provider_authority",
                "error_code": "delivery_acceptance_report_drifted",
            },
            {
                "first_failing_gate": envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": envelope.get("data", {}).get("error_code"),
            },
        )
        after_abort = self._authority_snapshot(run_dir)
        self.assertEqual(before_bind["files"], after_abort["files"])
        new_intents = [
            row for row in after_abort["intents"] if row not in before_bind["intents"]
        ]
        self.assertEqual(1, len(new_intents))
        self.assertEqual("ABORTED", new_intents[0][-1])
        new_slots = [
            row for row in after_abort["slots"] if row not in before_bind["slots"]
        ]
        self.assertEqual(4, len(new_slots))
        self.assertTrue(all(row[-1] == "RELEASED" for row in new_slots))

        report_path.write_bytes(original_report)
        retried, retry_envelope = _run_in_process_public_cli(*arguments)
        self.assertEqual(0, retried, retry_envelope)
        self.assertFalse(retry_envelope["data"]["idempotent"])


if __name__ == "__main__":
    unittest.main()

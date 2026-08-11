from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]

from tests.video_workflow import test_issue13_candidate_confirmation as candidate_test
from tests.video_workflow import test_issue13_cold_start_cutover as cold_start_test
from tests.video_workflow import test_issue13_platform_cutover as platform_test
from video2pdf_workflow_kernel.errors import KernelConflict
from video2pdf_workflow_kernel.guarded_delivery import _load_active_delivery_guard
from video2pdf_workflow_kernel.platform_kernel import BilibiliPlatformCutoverPublisher
import video2pdf_workflow_kernel.platform_kernel as platform_kernel_module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


class Issue13CandidateHardeningTests(unittest.TestCase):
    def test_canonical_delivery_guard_loader_resolves_real_authority(self) -> None:
        guard = _load_active_delivery_guard(PROJECT_ROOT)

        self.assertTrue(callable(guard.resolve_delivery_target))
        self.assertTrue(callable(guard.guard_report_is_fresh))

    def _candidate_harness(self) -> candidate_test.Issue13CandidateConfirmationTests:
        return candidate_test.Issue13CandidateConfirmationTests(
            "test_candidate_activation_rejects_generating_candidate"
        )

    def _bind_acceptance_to_delivery_projections(
        self, run_dir: Path, acceptance: Path
    ) -> None:
        run_path = run_dir / "workflow" / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        projections = run["delivery"]["projections"]
        video_path = Path(projections["video_target"]["path"])
        if not video_path.is_absolute():
            video_path = run_dir / video_path
        session_path = Path(projections["session_target"]["path"])
        if not session_path.is_absolute():
            session_path = run_dir.parents[1] / session_path
        index_path = Path(projections["task_index"]["path"])
        if not index_path.is_absolute():
            index_path = run_dir.parents[1] / index_path
        video = json.loads(video_path.read_text(encoding="utf-8"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        index = json.loads(index_path.read_text(encoding="utf-8"))
        video["artifacts"]["acceptance_report"] = {
            "path": str(acceptance.resolve()),
            "sha256": _sha256(acceptance),
        }
        _write_json(video_path, video)
        video_sha = _sha256(video_path)
        projections["video_target"]["sha256"] = video_sha
        session["video_target"]["sha256"] = video_sha
        _write_json(session_path, session)
        session_sha = _sha256(session_path)
        projections["session_target"]["sha256"] = session_sha
        entry = next(
            item for item in index["entries"] if item["run_id"] == run["run_id"]
        )
        entry["video_target"]["sha256"] = video_sha
        entry["session_target"]["sha256"] = session_sha
        _write_json(index_path, index)
        projections["task_index"]["sha256"] = _sha256(index_path)
        run_json = (
            json.dumps(
                run, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        )
        run_path.write_bytes(run_json.encode("utf-8"))
        # This local formal-provider seam models the provider committing its
        # projection refresh into the Run hash chain.
        control_store = run_dir.parent / ".workflow-control" / "control.sqlite3"
        run_sha = _sha256(run_path)
        with sqlite3.connect(control_store) as database:
            row = database.execute(
                "SELECT intent_id FROM delivery_lifecycle_intents "
                "WHERE run_id=? AND state='COMMITTED' "
                "ORDER BY expected_run_revision DESC LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            if row is not None:
                database.execute(
                    "UPDATE delivery_lifecycle_intents SET "
                    "replacement_run_record_json=?, replacement_run_record_sha256=? "
                    "WHERE intent_id=?",
                    (run_json, run_sha, row[0]),
                )

    def _ready_candidate(
        self, *, bind_acceptance: bool = True
    ) -> tuple[
        candidate_test.Issue13CandidateConfirmationTests,
        Path,
        Path,
        Path,
        Path,
        Path,
    ]:
        harness = self._candidate_harness()
        control, evidence, _workspace, run_dir = harness._start_candidate()
        acceptance, guard = harness._make_ready_with_passing_decisions(run_dir)
        if bind_acceptance:
            self._bind_acceptance_to_delivery_projections(run_dir, acceptance)
        return harness, control, evidence, run_dir, acceptance, guard

    def _bind_exit_evidence_to_candidate(
        self, exit_evidence: Path, run_dir: Path
    ) -> None:
        run_path = run_dir / "workflow" / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        source_path = run_dir / "source" / "manifest.json"
        _write_json(source_path, {"run_id": run["run_id"]})
        run.setdefault("artifact_generations", {})["source_manifest"] = {
            "path": "source/manifest.json",
            "sha256": _sha256(source_path),
        }
        _write_json(run_path, run)
        projections = run["delivery"]["projections"]
        video_path = run_dir / projections["video_target"]["path"]
        project_root = run_dir.parents[1]
        session_path = project_root / projections["session_target"]["path"]
        index_path = project_root / projections["task_index"]["path"]
        video = json.loads(video_path.read_text(encoding="utf-8"))
        expected = {
            "run_record": run_path,
            "source_manifest": source_path,
            "acceptance_report_v2": Path(
                video["artifacts"]["acceptance_report"]["path"]
            ),
            "delivery_guard_report": Path(
                video["artifacts"]["delivery_guard_report"]["path"]
            ),
            "video_delivery_target": video_path,
            "session_delivery_target": session_path,
            "delivery_task_index": index_path,
            "global_gate_authority": Path(video["global_gate_authority"]["path"]),
            "final_pdf": Path(video["artifacts"]["final_pdf"]["path"]),
        }
        manifest = json.loads(exit_evidence.read_text(encoding="utf-8"))
        guarded = manifest["guarded_delivery_evidence"]
        guarded["run_id"] = run["run_id"]
        artifacts = {item["role"]: item for item in guarded["artifacts"]}
        collection_path = PROJECT_ROOT / guarded["collection"]["path"]
        collection = json.loads(collection_path.read_text(encoding="utf-8"))
        collection["run_id"] = run["run_id"]
        for role, path in expected.items():
            binding = {
                "path": path.resolve().relative_to(PROJECT_ROOT).as_posix(),
                "sha256": _sha256(path),
            }
            artifacts[role].update(binding)
            collection["artifacts"][role] = {
                "path": str(path.resolve()),
                "sha256": binding["sha256"],
            }
        _write_json(collection_path, collection)
        guarded["collection"]["sha256"] = _sha256(collection_path)
        _write_json(exit_evidence, manifest)

    def _deliver_candidate(
        self,
    ) -> tuple[
        candidate_test.Issue13CandidateConfirmationTests,
        Path,
        Path,
        Path,
    ]:
        harness, control, evidence, run_dir, acceptance, guard = (
            self._ready_candidate()
        )
        harness._provisionally_activate(control, run_dir)
        for from_stage, to_stage, revision, artifacts in (
            (
                "ready_for_delivery",
                "accepted",
                2,
                {"acceptance_report": acceptance},
            ),
            (
                "accepted",
                "delivered",
                3,
                {"delivery_guard_report": guard},
            ),
        ):
            if to_stage == "delivered":
                _write_json(guard, candidate_test._guard_report("pass"))
            transition_evidence = harness._transition_evidence(
                run_dir,
                from_stage=from_stage,
                to_stage=to_stage,
                artifacts=artifacts,
            )
            arguments = (
                "delivery-transition",
                "--run-dir",
                str(run_dir),
                "--from-stage",
                from_stage,
                "--to-stage",
                to_stage,
                "--session-id",
                candidate_test.CANDIDATE_SESSION_ID,
                "--expected-run-revision",
                str(revision),
                "--expected-ownership-generation",
                "1",
                "--evidence",
                str(transition_evidence),
                "--transitioned-at",
                "2026-08-09T14:00:00Z",
            )
            if to_stage == "delivered":
                with patch.object(
                    platform_kernel_module,
                    "require_current_kernel_guarded_decision",
                    return_value={
                        "run_id": candidate_test.CANDIDATE_RUN_ID,
                        "acceptance_report": {
                            "path": str(acceptance.resolve()),
                            "sha256": _sha256(acceptance),
                        },
                        "delivery_guard_report": {
                            "path": str(guard.resolve()),
                            "sha256": _sha256(guard),
                        },
                    },
                ):
                    completed = platform_test._run_cli(*arguments)
            else:
                completed = candidate_test._run_public_cli(
                    self.id() + f"-{to_stage}", *arguments
                )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self._bind_exit_evidence_to_candidate(evidence, run_dir)
        return harness, control, evidence, run_dir

    def test_provisional_activation_rejects_unbound_synthetic_acceptance(self) -> None:
        _harness, control, _evidence, run_dir, _acceptance, _guard = (
            self._ready_candidate(bind_acceptance=False)
        )
        video_target = json.loads(
            (run_dir / "review" / "acceptance" / "delivery_target.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIsNone(video_target["artifacts"]["acceptance_report"])
        self.assertIsNone(video_target["artifacts"]["delivery_guard_report"])
        self.assertFalse(
            (run_dir / "review" / "acceptance" / "delivery_guard_report.json").exists()
        )

        activated = candidate_test._run_public_cli(
            self.id(),
            "platform-kernel-candidate-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--candidate-run-dir",
            str(run_dir),
            "--activated-at",
            "2026-08-09T14:01:00Z",
        )

        self.assertEqual(30, activated.returncode, activated.stdout + activated.stderr)
        envelope = json.loads(activated.stdout)
        self.assertEqual(
            "bilibili_candidate_guarded_decision_unbound",
            envelope["data"]["error_code"],
        )

    def test_final_confirmation_rejects_guarded_role_path_not_candidate_run(self) -> None:
        _harness, control, exit_evidence, _run_dir = self._deliver_candidate()
        manifest = json.loads(exit_evidence.read_text(encoding="utf-8"))
        guarded = manifest["guarded_delivery_evidence"]
        artifacts = {item["role"]: item for item in guarded["artifacts"]}
        fake_run = exit_evidence.with_name("other-run-record.json")
        _write_json(fake_run, {"run_id": candidate_test.CANDIDATE_RUN_ID})
        artifacts["run_record"].update(
            {
                "path": fake_run.resolve().relative_to(PROJECT_ROOT).as_posix(),
                "sha256": _sha256(fake_run),
            }
        )
        collection_path = PROJECT_ROOT / guarded["collection"]["path"]
        collection = json.loads(collection_path.read_text(encoding="utf-8"))
        collection["artifacts"]["run_record"] = {
            "path": str(fake_run.resolve()),
            "sha256": _sha256(fake_run),
        }
        _write_json(collection_path, collection)
        guarded["collection"]["sha256"] = _sha256(collection_path)
        mismatched = exit_evidence.with_name("role-path-mismatch.json")
        _write_json(mismatched, manifest)

        confirmed = platform_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(mismatched),
            "--activated-at",
            "2026-08-09T14:02:00Z",
        )

        self.assertEqual(30, confirmed.returncode, confirmed.stdout + confirmed.stderr)
        envelope = json.loads(confirmed.stdout)
        self.assertEqual(
            "guarded_delivery_candidate_binding",
            envelope["data"]["first_failing_gate"],
        )

    def test_activation_reconcile_rejects_missing_candidate_row(self) -> None:
        _harness, control, exit_evidence, _run_dir = self._deliver_candidate()
        interrupted = platform_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T14:03:00Z",
            "--fault-point",
            "after_intent",
        )
        self.assertEqual(60, interrupted.returncode, interrupted.stdout + interrupted.stderr)
        with sqlite3.connect(control / "platform-kernel-control.sqlite3") as database:
            database.execute("DELETE FROM platform_cutover_candidates")

        reconciled = platform_test._run_cli(
            "platform-kernel-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
        )

        self.assertEqual(30, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        envelope = json.loads(reconciled.stdout)
        self.assertEqual(
            "bilibili_provisional_candidate_absent", envelope["data"]["error_code"]
        )

    def test_activation_reconcile_rejects_candidate_snapshot_drift(self) -> None:
        _harness, control, exit_evidence, run_dir = self._deliver_candidate()
        interrupted = platform_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T14:03:30Z",
            "--fault-point",
            "after_authority_write",
        )
        self.assertEqual(60, interrupted.returncode, interrupted.stdout + interrupted.stderr)
        with sqlite3.connect(
            control / "platform-kernel-control.sqlite3"
        ) as database:
            candidate = json.loads(
                database.execute(
                    "SELECT candidate_json FROM platform_cutover_candidates "
                    "WHERE platform='bilibili'"
                ).fetchone()[0]
            )
            candidate["candidate_snapshot_drift"] = True
            database.execute(
                "UPDATE platform_cutover_candidates SET candidate_json=? "
                "WHERE platform='bilibili'",
                (json.dumps(candidate, sort_keys=True, separators=(",", ":")),),
            )

        reconciled = platform_test._run_cli(
            "platform-kernel-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
        )

        self.assertEqual(30, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        with sqlite3.connect(control / "platform-kernel-control.sqlite3") as database:
            self.assertEqual(
                0,
                database.execute(
                    "SELECT COUNT(*) FROM platform_cutover_authority"
                ).fetchone()[0],
            )
            self.assertEqual(
                "PROVISIONAL",
                database.execute(
                    "SELECT state FROM platform_cutover_candidates WHERE platform='bilibili'"
                ).fetchone()[0],
            )

    def test_legacy_prepared_intent_snapshot_is_backfilled_and_reconciled(self) -> None:
        _harness, control, exit_evidence, _run_dir = self._deliver_candidate()
        interrupted = platform_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T14:03:35Z",
            "--fault-point",
            "after_authority_write",
        )
        self.assertEqual(60, interrupted.returncode, interrupted.stdout + interrupted.stderr)
        with sqlite3.connect(control / "platform-kernel-control.sqlite3") as database:
            database.execute(
                "UPDATE platform_cutover_intents SET candidate_snapshot_sha256=NULL "
                "WHERE state='PREPARED'"
            )

        reconciled = platform_test._run_cli(
            "platform-kernel-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
        )

        self.assertEqual(0, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        with sqlite3.connect(control / "platform-kernel-control.sqlite3") as database:
            snapshot, intent_state = database.execute(
                "SELECT candidate_snapshot_sha256,state FROM platform_cutover_intents"
            ).fetchone()
            self.assertEqual(64, len(snapshot))
            self.assertEqual("COMMITTED", intent_state)
            self.assertEqual(
                "CONFIRMED",
                database.execute(
                    "SELECT state FROM platform_cutover_candidates WHERE platform='bilibili'"
                ).fetchone()[0],
            )

    def test_legacy_prepared_intent_snapshot_backfill_rejects_candidate_drift(self) -> None:
        _harness, control, exit_evidence, _run_dir = self._deliver_candidate()
        interrupted = platform_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T14:03:40Z",
            "--fault-point",
            "after_authority_write",
        )
        self.assertEqual(60, interrupted.returncode, interrupted.stdout + interrupted.stderr)
        with sqlite3.connect(control / "platform-kernel-control.sqlite3") as database:
            row = database.execute(
                "SELECT candidate_json FROM platform_cutover_candidates "
                "WHERE platform='bilibili'"
            ).fetchone()
            candidate = json.loads(row[0])
            candidate["source_identity"] = "f" * 64
            database.execute(
                "UPDATE platform_cutover_candidates SET candidate_json=? "
                "WHERE platform='bilibili'",
                (json.dumps(candidate, sort_keys=True, separators=(",", ":")),),
            )
            database.execute(
                "UPDATE platform_cutover_intents SET candidate_snapshot_sha256=NULL "
                "WHERE state='PREPARED'"
            )

        reconciled = platform_test._run_cli(
            "platform-kernel-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
        )

        self.assertEqual(30, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        with sqlite3.connect(control / "platform-kernel-control.sqlite3") as database:
            self.assertEqual(
                0,
                database.execute(
                    "SELECT COUNT(*) FROM platform_cutover_authority"
                ).fetchone()[0],
            )

    def test_activation_final_transaction_rejects_candidate_snapshot_drift(self) -> None:
        _harness, control, exit_evidence, _run_dir = self._deliver_candidate()
        original_write = platform_kernel_module.write_json_atomic

        def write_authority_then_drift(path: Path, value: dict) -> str:
            fingerprint = original_write(path, value)
            if path.name == "bilibili.json":
                with sqlite3.connect(
                    control / "platform-kernel-control.sqlite3"
                ) as database:
                    candidate = json.loads(
                        database.execute(
                            "SELECT candidate_json FROM platform_cutover_candidates "
                            "WHERE platform='bilibili'"
                        ).fetchone()[0]
                    )
                    candidate["candidate_snapshot_drift"] = True
                    database.execute(
                        "UPDATE platform_cutover_candidates SET candidate_json=? "
                        "WHERE platform='bilibili'",
                        (
                            json.dumps(
                                candidate, sort_keys=True, separators=(",", ":")
                            ),
                        ),
                    )
            return fingerprint

        with patch.object(
            platform_kernel_module,
            "write_json_atomic",
            side_effect=write_authority_then_drift,
        ):
            activated = platform_test._run_cli(
                "platform-kernel-activate",
                "--platform",
                "bilibili",
                "--control-store-root",
                str(control),
                "--exit-evidence",
                str(exit_evidence),
                "--activated-at",
                "2026-08-09T14:03:45Z",
            )

        self.assertEqual(30, activated.returncode, activated.stdout + activated.stderr)
        with sqlite3.connect(control / "platform-kernel-control.sqlite3") as database:
            self.assertEqual(
                0,
                database.execute(
                    "SELECT COUNT(*) FROM platform_cutover_authority"
                ).fetchone()[0],
            )
            self.assertEqual(
                "PROVISIONAL",
                database.execute(
                    "SELECT state FROM platform_cutover_candidates WHERE platform='bilibili'"
                ).fetchone()[0],
            )

    def test_confirmed_delivery_authority_rejects_authority_and_evidence_drift(self) -> None:
        _harness, control, exit_evidence, run_dir = self._deliver_candidate()
        confirmed = platform_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T14:04:00Z",
        )
        self.assertEqual(0, confirmed.returncode, confirmed.stdout + confirmed.stderr)
        authority_path = control / "platform-authorities" / "bilibili.json"
        original_authority = authority_path.read_bytes()
        original_evidence = exit_evidence.read_bytes()
        publisher = BilibiliPlatformCutoverPublisher()
        try:
            for label, drift in (
                ("authority", lambda: authority_path.write_bytes(b"{}\n")),
                ("evidence", lambda: exit_evidence.write_bytes(b"{}\n")),
            ):
                with self.subTest(label=label):
                    authority_path.write_bytes(original_authority)
                    exit_evidence.write_bytes(original_evidence)
                    drift()
                    with self.assertRaises(KernelConflict):
                        publisher.authorize_delivery_transition(
                            platform="bilibili",
                            control_store_root=control,
                            run_dir=run_dir,
                            run_id=candidate_test.CANDIDATE_RUN_ID,
                            to_stage="delivered",
                        )
        finally:
            authority_path.write_bytes(original_authority)
            exit_evidence.write_bytes(original_evidence)

    def test_concurrent_candidate_init_publishes_only_one_run(self) -> None:
        cold = cold_start_test.Issue13ColdStartCutoverTests(
            "test_cold_start_prepare_binds_one_candidate_without_activation"
        )
        control, workspace, probe, implementation_commit = cold._cold_start_case()
        cold._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )
        workspaces = (
            control.parent / "candidate-project-first" / "workspace",
            control.parent / "candidate-project-second" / "workspace",
        )
        for item in workspaces:
            item.mkdir(parents=True)

        def initialize(item: Path) -> subprocess.CompletedProcess[str]:
            return candidate_test._run_public_cli(
                self.id() + item.name,
                "init-cutover-candidate",
                "--workspace-root",
                str(item),
                "--control-store-root",
                str(control),
                "--probe",
                str(probe),
                "--session-id",
                "session-issue13-candidate",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(initialize, workspaces))

        run_records = [path for item in workspaces for path in item.rglob("run.json")]
        self.assertEqual(1, len(run_records), [result.stdout for result in results])

    def test_faulted_candidate_init_has_public_reconcile_seam(self) -> None:
        cold = cold_start_test.Issue13ColdStartCutoverTests(
            "test_cold_start_prepare_binds_one_candidate_without_activation"
        )
        control, workspace, probe, implementation_commit = cold._cold_start_case()
        cold._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )
        faulted = candidate_test._run_public_cli(
            self.id() + "-fault",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(probe),
            "--session-id",
            "session-issue13-candidate",
            "--fault-point",
            "after_output_dir_publish",
        )
        self.assertEqual(60, faulted.returncode, faulted.stdout + faulted.stderr)

        reconciled = candidate_test._run_public_cli(
            self.id() + "-reconcile",
            "platform-kernel-candidate-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--workspace-root",
            str(workspace),
            "--candidate-probe",
            str(probe),
            "--candidate-session-id",
            "session-issue13-candidate",
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        envelope = json.loads(reconciled.stdout)
        self.assertEqual("candidate_initialization_reconciled", envelope["classification"])

    def test_candidate_begin_fault_can_roll_back_and_retry_publicly(self) -> None:
        cold = cold_start_test.Issue13ColdStartCutoverTests(
            "test_cold_start_prepare_binds_one_candidate_without_activation"
        )
        control, workspace, probe, implementation_commit = cold._cold_start_case()
        cold._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )
        faulted = candidate_test._run_public_cli(
            self.id() + "-fault",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(probe),
            "--session-id",
            "session-issue13-candidate",
            "--fault-point",
            "after_candidate_begin",
        )
        self.assertEqual(60, faulted.returncode, faulted.stdout + faulted.stderr)

        reconciled = candidate_test._run_public_cli(
            self.id() + "-reconcile",
            "platform-kernel-candidate-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--workspace-root",
            str(workspace),
            "--candidate-probe",
            str(probe),
            "--candidate-session-id",
            "session-issue13-candidate",
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        envelope = json.loads(reconciled.stdout)
        self.assertEqual(
            "candidate_initialization_rolled_back", envelope["classification"]
        )

        retried = candidate_test._run_public_cli(
            self.id() + "-retry",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(probe),
            "--session-id",
            "session-issue13-candidate",
        )
        self.assertEqual(0, retried.returncode, retried.stdout + retried.stderr)

    def test_aborted_kernel_initialization_rolls_candidate_back_for_retry(self) -> None:
        cold = cold_start_test.Issue13ColdStartCutoverTests(
            "test_cold_start_prepare_binds_one_candidate_without_activation"
        )
        control, workspace, probe, implementation_commit = cold._cold_start_case()
        cold._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )
        faulted = candidate_test._run_public_cli(
            self.id() + "-fault",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(probe),
            "--session-id",
            "session-issue13-candidate",
            "--fault-point",
            "after_intent_prepared",
        )
        self.assertEqual(60, faulted.returncode, faulted.stdout + faulted.stderr)

        reconciled = candidate_test._run_public_cli(
            self.id() + "-reconcile",
            "platform-kernel-candidate-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--workspace-root",
            str(workspace),
            "--candidate-probe",
            str(probe),
            "--candidate-session-id",
            "session-issue13-candidate",
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        envelope = json.loads(reconciled.stdout)
        self.assertEqual(
            "candidate_initialization_rolled_back", envelope["classification"]
        )

        retried = candidate_test._run_public_cli(
            self.id() + "-retry",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(probe),
            "--session-id",
            "session-issue13-candidate",
        )
        self.assertEqual(0, retried.returncode, retried.stdout + retried.stderr)

    def test_candidate_activation_rejects_prepared_session_handoff(self) -> None:
        _harness, control, _evidence, run_dir, acceptance, _guard = (
            self._ready_candidate()
        )
        handed_off = candidate_test._run_public_cli(
            self.id() + "-handoff",
            "delivery-handoff",
            "--run-dir",
            str(run_dir),
            "--from-session-id",
            candidate_test.CANDIDATE_SESSION_ID,
            "--to-session-id",
            "session-issue13-replacement",
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--handed-off-at",
            "2026-08-09T14:05:00Z",
        )
        self.assertEqual(30, handed_off.returncode, handed_off.stdout + handed_off.stderr)
        envelope = json.loads(handed_off.stdout)
        self.assertEqual(
            "bilibili_candidate_handoff_forbidden",
            envelope["data"]["error_code"],
        )

    def test_candidate_activation_rejects_source_identity_drift(self) -> None:
        _harness, control, _evidence, run_dir, _acceptance, _guard = (
            self._ready_candidate()
        )
        run_path = run_dir / "workflow" / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["source_identity"] = "f" * 64
        _write_json(run_path, run)

        activated = candidate_test._run_public_cli(
            self.id(),
            "platform-kernel-candidate-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--candidate-run-dir",
            str(run_dir),
            "--activated-at",
            "2026-08-09T14:07:00Z",
        )
        self.assertEqual(30, activated.returncode, activated.stdout + activated.stderr)
        envelope = json.loads(activated.stdout)
        self.assertEqual(
            "bilibili_candidate_source_binding_mismatch",
            envelope["data"]["error_code"],
        )

    def test_candidate_activation_rejects_projection_escape(self) -> None:
        _harness, control, _evidence, run_dir, _acceptance, _guard = (
            self._ready_candidate()
        )
        outside = control.parent / "outside-project-projections"
        outside.mkdir()
        run_path = run_dir / "workflow" / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        role = "session_target"
        binding = run["delivery"]["projections"][role]
        source = Path(binding["path"])
        if not source.is_absolute():
            source = (run_dir.parents[1] / source).resolve()
        destination = outside / f"{role}.json"
        shutil.copyfile(source, destination)
        binding.update({"path": str(destination.resolve()), "sha256": _sha256(destination)})
        _write_json(run_path, run)

        activated = candidate_test._run_public_cli(
            self.id(),
            "platform-kernel-candidate-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--candidate-run-dir",
            str(run_dir),
            "--activated-at",
            "2026-08-09T14:08:00Z",
        )
        self.assertEqual(30, activated.returncode, activated.stdout + activated.stderr)
        envelope = json.loads(activated.stdout)
        self.assertEqual("path_boundary", envelope["data"]["first_failing_gate"])
        self.assertEqual(
            "bilibili_candidate_projection_escape", envelope["data"]["error_code"]
        )

    def test_prepare_idempotency_rejects_sql_json_state_drift(self) -> None:
        cold = cold_start_test.Issue13ColdStartCutoverTests(
            "test_cold_start_prepare_binds_one_candidate_without_activation"
        )
        control, _workspace, probe, implementation_commit = cold._cold_start_case()
        cold._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )
        database_path = control / "platform-kernel-control.sqlite3"
        with sqlite3.connect(database_path) as database:
            candidate = json.loads(
                database.execute(
                    "SELECT candidate_json FROM platform_cutover_candidates "
                    "WHERE platform='bilibili'"
                ).fetchone()[0]
            )
            candidate["state"] = "INITIALIZED"
            database.execute(
                "UPDATE platform_cutover_candidates SET candidate_json=? "
                "WHERE platform='bilibili'",
                (json.dumps(candidate, sort_keys=True, separators=(",", ":")),),
            )

        repeated = candidate_test._run_public_cli(
            self.id(),
            "platform-kernel-prepare",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--implementation-commit",
            implementation_commit,
            "--candidate-probe",
            str(probe),
            "--candidate-session-id",
            "session-issue13-candidate",
            "--prepared-at",
            "2026-08-09T13:00:00Z",
        )
        self.assertEqual(30, repeated.returncode, repeated.stdout + repeated.stderr)
        envelope = json.loads(repeated.stdout)
        self.assertEqual(
            "bilibili_candidate_state_inconsistent", envelope["data"]["error_code"]
        )

    def test_prepare_rebinds_only_current_implementation_for_exact_prepared_candidate(
        self,
    ) -> None:
        cold = cold_start_test.Issue13ColdStartCutoverTests(
            "test_cold_start_prepare_binds_one_candidate_without_activation"
        )
        control, _workspace, probe, _implementation_commit = cold._cold_start_case()
        old_commit = "a" * 40
        repaired_commit = "b" * 40

        def run_prepare(commit: str, prepared_at: str) -> subprocess.CompletedProcess[str]:
            def fake_git_output(_root: Path, *arguments: str) -> str:
                if arguments == ("cat-file", "-e", f"{commit}^{{commit}}"):
                    return ""
                if arguments == ("rev-parse", "HEAD"):
                    return commit
                raise AssertionError(f"unexpected git command: {arguments!r}")

            with patch.object(platform_kernel_module, "git_output", fake_git_output):
                return platform_test._run_cli(
                    "platform-kernel-prepare",
                    "--platform",
                    "bilibili",
                    "--control-store-root",
                    str(control),
                    "--implementation-commit",
                    commit,
                    "--candidate-probe",
                    str(probe),
                    "--candidate-session-id",
                    "session-issue13-candidate",
                    "--prepared-at",
                    prepared_at,
                )

        initial = run_prepare(old_commit, "2026-08-09T13:00:00Z")
        self.assertEqual(0, initial.returncode, initial.stdout + initial.stderr)

        timestamp_only_change = run_prepare(old_commit, "2026-08-11T08:59:00Z")
        self.assertEqual(
            30,
            timestamp_only_change.returncode,
            timestamp_only_change.stdout + timestamp_only_change.stderr,
        )

        repaired = run_prepare(repaired_commit, "2026-08-11T09:00:00Z")
        self.assertEqual(0, repaired.returncode, repaired.stdout + repaired.stderr)
        repaired_envelope = json.loads(repaired.stdout)
        self.assertFalse(repaired_envelope["data"]["idempotent"])
        self.assertTrue(repaired_envelope["data"]["reprepared"])

        with sqlite3.connect(control / "platform-kernel-control.sqlite3") as database:
            database.row_factory = sqlite3.Row
            row = database.execute(
                "SELECT * FROM platform_cutover_candidates WHERE platform='bilibili'"
            ).fetchone()
        self.assertIsNotNone(row)
        candidate = json.loads(row["candidate_json"])
        self.assertEqual("PREPARED", row["state"])
        self.assertEqual("PREPARED", candidate["state"])
        self.assertEqual(repaired_commit, row["implementation_commit"])
        self.assertEqual(repaired_commit, candidate["implementation_commit"])
        self.assertNotIn("workspace_root", candidate)
        self.assertNotIn("candidate_run_dir", candidate)

        exact_retry = run_prepare(repaired_commit, "2026-08-11T09:00:00Z")
        self.assertEqual(
            0, exact_retry.returncode, exact_retry.stdout + exact_retry.stderr
        )
        retry_envelope = json.loads(exact_retry.stdout)
        self.assertTrue(retry_envelope["data"]["idempotent"])
        self.assertFalse(retry_envelope["data"]["reprepared"])

    def test_candidate_activation_rejects_sql_json_state_drift(self) -> None:
        _harness, control, _evidence, run_dir, _acceptance, _guard = (
            self._ready_candidate()
        )
        with sqlite3.connect(control / "platform-kernel-control.sqlite3") as database:
            row = database.execute(
                "SELECT candidate_json FROM platform_cutover_candidates "
                "WHERE platform='bilibili'"
            ).fetchone()
            candidate = json.loads(row[0])
            candidate["state"] = "PREPARED"
            database.execute(
                "UPDATE platform_cutover_candidates SET candidate_json=? "
                "WHERE platform='bilibili'",
                (json.dumps(candidate, sort_keys=True, separators=(",", ":")),),
            )

        activated = candidate_test._run_public_cli(
            self.id(),
            "platform-kernel-candidate-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--candidate-run-dir",
            str(run_dir),
            "--activated-at",
            "2026-08-09T14:09:00Z",
        )
        self.assertEqual(30, activated.returncode, activated.stdout + activated.stderr)
        envelope = json.loads(activated.stdout)
        self.assertEqual(
            "bilibili_candidate_state_inconsistent", envelope["data"]["error_code"]
        )


if __name__ == "__main__":
    unittest.main()

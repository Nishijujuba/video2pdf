from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]

from tests.video_workflow._test_run import child_environment
from tests.video_workflow.test_issue13_delivery_lifecycle import (
    _acceptance_report,
    _guard_report,
)
from tests.video_workflow import test_issue13_platform_cutover as platform_cutover_test
import video2pdf_workflow_kernel.delivery_lifecycle as delivery_lifecycle_module
import video2pdf_workflow_kernel.platform_kernel as platform_kernel_module
from video2pdf_workflow_kernel.cli import main as workflow_cli_main


CANDIDATE_RUN_ID = "13131313131313131313131313131313"
CANDIDATE_SESSION_ID = "session-issue13-candidate"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
    )


def _run_public_cli(test_id: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            subprocess.sys.executable,
            "-X",
            "utf8",
            "-B",
            str(PROJECT_ROOT / "scripts" / "video_workflow.py"),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=child_environment(test_id),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _run_cli_with_formal_authority(
    *arguments: str,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Run one lifecycle command with an explicit Acceptance provider seam.

    The candidate cutover fixtures handcraft the acceptance report and predate
    the committed two-hop delivery-successor contract (issue #13 slice 12).
    Mirroring ``test_issue13_delivery_lifecycle._run_cli_with_formal_platform_authority``,
    this seam supplies only the formal acceptance-provider results while keeping
    every lifecycle, decision-evidence, and platform-candidate validator active,
    so the candidate-authorization gate remains the discriminator for premature
    transitions.
    """

    def formal_guard_eligibility(*, workspace_root: Path) -> dict[str, object]:
        report_path = workspace_root / "acceptance_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        passing = report.get("overall_status") == "pass"
        return {
            "eligible": passing,
            "delivery_authority": passing,
            "report_sha256": report.get("report_sha256"),
        }

    def formal_committed_successor(*, workspace_root: Path) -> dict[str, object]:
        run_path = workspace_root.parents[1] / "workflow" / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        return {
            "run_id": run["run_id"],
            "run_revision": run["coordination_revision"],
            "run_record_sha256": _sha256(run_path),
        }

    stdout = io.StringIO()
    with patch.object(
        delivery_lifecycle_module.AcceptanceV2Provider,
        "guard_eligibility",
        side_effect=formal_guard_eligibility,
    ), patch.object(
        delivery_lifecycle_module.AcceptanceV2Provider,
        "require_committed_delivery_successor",
        side_effect=formal_committed_successor,
    ), redirect_stdout(stdout):
        returncode = workflow_cli_main(list(arguments))
    completed = subprocess.CompletedProcess(
        args=list(arguments),
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr="",
    )
    return completed, json.loads(completed.stdout)


class Issue13CandidateConfirmationTests(unittest.TestCase):
    def _start_candidate(self) -> tuple[Path, Path, Path, Path]:
        fixture = platform_cutover_test.Issue13PlatformCutoverTests(
            "test_bilibili_activation_publishes_single_platform_authority"
        )
        control_store_root, exit_evidence = fixture._write_valid_cutover_manifest()
        with sqlite3.connect(
            control_store_root / "platform-kernel-control.sqlite3"
        ) as platform_db:
            platform_db.execute("DELETE FROM platform_cutover_candidates")
        workspace_root = control_store_root.parent / "candidate-project" / "workspace"
        workspace_root.mkdir(parents=True)
        probe_path = control_store_root.parent / "candidate-probe.json"
        probe = json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "contracts"
                / "bootstrap-record.v2.valid.json"
            ).read_text(encoding="utf-8")
        )
        probe["run_id"] = CANDIDATE_RUN_ID
        _write_json(probe_path, probe)
        implementation_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout.strip()

        prepared = _run_public_cli(
            self.id() + "-prepare",
            "platform-kernel-prepare",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--implementation-commit",
            implementation_commit,
            "--candidate-probe",
            str(probe_path),
            "--candidate-session-id",
            CANDIDATE_SESSION_ID,
            "--prepared-at",
            "2026-08-09T13:00:00Z",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)

        initialized = _run_public_cli(
            self.id() + "-initialize",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace_root),
            "--control-store-root",
            str(control_store_root),
            "--probe",
            str(probe_path),
            "--session-id",
            CANDIDATE_SESSION_ID,
        )
        self.assertEqual(0, initialized.returncode, initialized.stdout + initialized.stderr)
        envelope = json.loads(initialized.stdout)
        return (
            control_store_root,
            exit_evidence,
            workspace_root,
            Path(envelope["data"]["run_dir"]),
        )

    def _transition_evidence(
        self,
        run_dir: Path,
        *,
        from_stage: str,
        to_stage: str,
        artifacts: dict[str, Path],
    ) -> Path:
        run = json.loads((run_dir / "workflow" / "run.json").read_text(encoding="utf-8"))
        video_target = json.loads(
            (run_dir / "review" / "acceptance" / "delivery_target.json").read_text(
                encoding="utf-8"
            )
        )
        evidence_path = (
            run_dir
            / "review"
            / "acceptance"
            / f"delivery-transition-{from_stage}-{to_stage}.json"
        )
        _write_json(
            evidence_path,
            {
                "schema_name": "delivery-transition-evidence",
                "schema_version": "1.0.0",
                "run_id": run["run_id"],
                "from_stage": from_stage,
                "to_stage": to_stage,
                "artifacts": {
                    role: {"path": str(path.resolve()), "sha256": _sha256(path)}
                    for role, path in artifacts.items()
                },
                "global_gate_authority": video_target["global_gate_authority"],
            },
        )
        return evidence_path

    def _make_ready_with_passing_decisions(
        self, run_dir: Path
    ) -> tuple[Path, Path]:
        final_pdf = run_dir / "article.pdf"
        final_pdf.write_bytes(b"%PDF-1.7\nissue13 candidate\n")
        main_tex = run_dir / "main.tex"
        main_tex.write_text("\\documentclass{article}\n", encoding="utf-8")
        compile_report = run_dir / "review" / "latex" / "compile_report.json"
        _write_json(compile_report, {"status": "pass"})
        render_manifest = run_dir / "review" / "acceptance" / "rendered-pages.json"
        _write_json(render_manifest, {"status": "pass", "page_count": 1})
        ready_evidence = self._transition_evidence(
            run_dir,
            from_stage="generating",
            to_stage="ready_for_delivery",
            artifacts={
                "final_pdf": final_pdf,
                "main_tex": main_tex,
                "final_compile_report": compile_report,
                "render_evidence_manifest": render_manifest,
            },
        )
        ready = _run_public_cli(
            self.id() + "-ready",
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "generating",
            "--to-stage",
            "ready_for_delivery",
            "--session-id",
            CANDIDATE_SESSION_ID,
            "--expected-run-revision",
            "1",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(ready_evidence),
            "--transitioned-at",
            "2026-08-09T13:10:00Z",
        )
        self.assertEqual(0, ready.returncode, ready.stdout + ready.stderr)

        acceptance_report = run_dir / "review" / "acceptance" / "acceptance_report.json"
        _write_json(acceptance_report, _acceptance_report(CANDIDATE_RUN_ID, 2, "pass"))
        guard_report = run_dir / "review" / "acceptance" / "delivery_guard_report.json"
        return acceptance_report, guard_report

    def _bind_acceptance_to_ready_candidate(
        self, run_dir: Path, acceptance_report: Path
    ) -> None:
        run_path = run_dir / "workflow" / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        projections = run["delivery"]["projections"]
        project_root = run_dir.parents[1]
        video_path = run_dir / projections["video_target"]["path"]
        session_path = project_root / projections["session_target"]["path"]
        index_path = project_root / projections["task_index"]["path"]
        video = json.loads(video_path.read_text(encoding="utf-8"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        index = json.loads(index_path.read_text(encoding="utf-8"))
        video["artifacts"]["acceptance_report"] = {
            "path": str(acceptance_report.resolve()),
            "sha256": _sha256(acceptance_report),
        }
        _write_json(video_path, video)
        video_sha = _sha256(video_path)
        projections["video_target"]["sha256"] = video_sha
        session["video_target"]["sha256"] = video_sha
        _write_json(session_path, session)
        session_sha = _sha256(session_path)
        projections["session_target"]["sha256"] = session_sha
        entry = next(item for item in index["entries"] if item["run_id"] == run["run_id"])
        entry["video_target"]["sha256"] = video_sha
        entry["session_target"]["sha256"] = session_sha
        _write_json(index_path, index)
        projections["task_index"]["sha256"] = _sha256(index_path)
        _write_json(run_path, run)
        with sqlite3.connect(
            run_dir.parent / ".workflow-control" / "control.sqlite3"
        ) as database:
            row = database.execute(
                "SELECT intent_id FROM delivery_lifecycle_intents WHERE run_id=? "
                "AND state='COMMITTED' ORDER BY expected_run_revision DESC LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            database.execute(
                "UPDATE delivery_lifecycle_intents SET "
                "replacement_run_record_json=?, replacement_run_record_sha256=? "
                "WHERE intent_id=?",
                (run_path.read_text(encoding="utf-8"), _sha256(run_path), row[0]),
            )

    def _bind_exit_evidence_to_delivered_candidate(
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
        project_root = run_dir.parents[1]
        video_path = run_dir / projections["video_target"]["path"]
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

    def _provisionally_activate(self, control_store_root: Path, run_dir: Path) -> dict:
        report = json.loads(
            (run_dir / "review" / "acceptance" / "acceptance_report.json").read_text(
                encoding="utf-8"
            )
        )
        with patch.object(
            platform_kernel_module, "AcceptanceV2Provider"
        ) as provider_type:
            provider_type.return_value.guard_eligibility.return_value = {
                "eligible": True,
                "delivery_authority": True,
                "report_sha256": report["report_sha256"],
            }
            activated = platform_cutover_test._run_cli(
                "platform-kernel-candidate-activate",
                "--platform",
                "bilibili",
                "--control-store-root",
                str(control_store_root),
                "--candidate-run-dir",
                str(run_dir),
                "--activated-at",
                "2026-08-09T13:20:00Z",
            )
        self.assertEqual(0, activated.returncode, activated.stdout + activated.stderr)
        envelope = json.loads(activated.stdout)
        self.assertEqual("PROVISIONAL", envelope["data"]["cutover_state"])
        self.assertEqual(CANDIDATE_RUN_ID, envelope["data"]["candidate_run_id"])
        return envelope

    def test_candidate_activation_rejects_generating_candidate(self) -> None:
        control, _exit_evidence, _workspace, run_dir = self._start_candidate()

        activated = _run_public_cli(
            self.id() + "-too-early",
            "platform-kernel-candidate-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--candidate-run-dir",
            str(run_dir),
            "--activated-at",
            "2026-08-09T13:05:00Z",
        )

        self.assertEqual(30, activated.returncode, activated.stdout + activated.stderr)
        envelope = json.loads(activated.stdout)
        self.assertEqual("platform_kernel_candidate", envelope["data"]["first_failing_gate"])
        self.assertEqual(
            "bilibili_candidate_not_ready_for_activation",
            envelope["data"]["error_code"],
        )

    def test_provisional_authority_is_candidate_only_and_candidate_can_deliver(self) -> None:
        control, _exit_evidence, workspace, run_dir = self._start_candidate()
        acceptance_report, guard_report = self._make_ready_with_passing_decisions(run_dir)
        self._bind_acceptance_to_ready_candidate(run_dir, acceptance_report)
        accepted_evidence = self._transition_evidence(
            run_dir,
            from_stage="ready_for_delivery",
            to_stage="accepted",
            artifacts={"acceptance_report": acceptance_report},
        )
        premature, premature_envelope = _run_cli_with_formal_authority(
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "ready_for_delivery",
            "--to-stage",
            "accepted",
            "--session-id",
            CANDIDATE_SESSION_ID,
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(accepted_evidence),
            "--transitioned-at",
            "2026-08-09T13:15:00Z",
        )
        self.assertEqual(30, premature.returncode, premature.stdout + premature.stderr)
        self.assertEqual(
            "bilibili_candidate_delivery_not_authorized",
            premature_envelope["data"]["error_code"],
        )
        self._provisionally_activate(control, run_dir)

        fake_root = control.parent / "untrusted-control"
        fake_gate = fake_root / "active_global_gate.json"
        _write_json(fake_gate, {"active_global_gate": "acceptance_report_v2"})
        spoofed_value = json.loads(accepted_evidence.read_text(encoding="utf-8"))
        spoofed_value["global_gate_authority"] = {
            "path": str(fake_gate.resolve()),
            "generation": 1,
            "sha256": _sha256(fake_gate),
        }
        spoofed_evidence = accepted_evidence.with_name("spoofed-control-root.json")
        _write_json(spoofed_evidence, spoofed_value)
        spoofed, spoofed_envelope = _run_cli_with_formal_authority(
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "ready_for_delivery",
            "--to-stage",
            "accepted",
            "--session-id",
            CANDIDATE_SESSION_ID,
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(spoofed_evidence),
            "--transitioned-at",
            "2026-08-09T13:16:00Z",
        )
        self.assertEqual(20, spoofed.returncode, spoofed.stdout + spoofed.stderr)
        self.assertEqual(
            "delivery_global_gate_binding_conflict",
            spoofed_envelope["data"]["error_code"],
        )

        ordinary = _run_public_cli(
            self.id() + "-ordinary-init",
            "init-run",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(control.parent / "candidate-probe.json"),
            "--session-id",
            "session-ordinary-run",
        )
        self.assertEqual(30, ordinary.returncode, ordinary.stdout + ordinary.stderr)
        ordinary_envelope = json.loads(ordinary.stdout)
        self.assertEqual(
            "bilibili_platform_authority_pending_confirmation",
            ordinary_envelope["data"]["error_code"],
        )

        transitions = (
            ("ready_for_delivery", "accepted", 2, {"acceptance_report": acceptance_report}),
            ("accepted", "delivered", 3, {"delivery_guard_report": guard_report}),
        )
        for from_stage, to_stage, revision, artifacts in transitions:
            if to_stage == "delivered":
                _write_json(guard_report, _guard_report("pass"))
            evidence = self._transition_evidence(
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
                CANDIDATE_SESSION_ID,
                "--expected-run-revision",
                str(revision),
                "--expected-ownership-generation",
                "1",
                "--evidence",
                str(evidence),
                "--transitioned-at",
                "2026-08-09T13:30:00Z",
            )
            if to_stage == "delivered":
                with patch.object(
                    platform_kernel_module,
                    "require_current_kernel_guarded_decision",
                    return_value={
                        "run_id": CANDIDATE_RUN_ID,
                        "acceptance_report": {
                            "sha256": _sha256(acceptance_report)
                        },
                    },
                ):
                    transitioned = platform_cutover_test._run_cli(*arguments)
            else:
                transitioned, _transition_envelope = (
                    _run_cli_with_formal_authority(*arguments)
                )
            self.assertEqual(
                0, transitioned.returncode, transitioned.stdout + transitioned.stderr
            )
        run = json.loads((run_dir / "workflow" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("delivered", run["delivery"]["stage"])

    def test_final_activation_confirms_only_matching_delivered_candidate(self) -> None:
        control, exit_evidence, _workspace, run_dir = self._start_candidate()
        acceptance_report, guard_report = self._make_ready_with_passing_decisions(run_dir)
        self._bind_acceptance_to_ready_candidate(run_dir, acceptance_report)
        self._provisionally_activate(control, run_dir)
        for from_stage, to_stage, revision, artifacts in (
            ("ready_for_delivery", "accepted", 2, {"acceptance_report": acceptance_report}),
            ("accepted", "delivered", 3, {"delivery_guard_report": guard_report}),
        ):
            if to_stage == "delivered":
                _write_json(guard_report, _guard_report("pass"))
            evidence = self._transition_evidence(
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
                CANDIDATE_SESSION_ID,
                "--expected-run-revision",
                str(revision),
                "--expected-ownership-generation",
                "1",
                "--evidence",
                str(evidence),
                "--transitioned-at",
                "2026-08-09T13:40:00Z",
            )
            if to_stage == "delivered":
                with patch.object(
                    platform_kernel_module,
                    "require_current_kernel_guarded_decision",
                    return_value={
                        "run_id": CANDIDATE_RUN_ID,
                        "acceptance_report": {
                            "sha256": _sha256(acceptance_report)
                        },
                    },
                ):
                    transitioned = platform_cutover_test._run_cli(*arguments)
            else:
                transitioned, _transition_envelope = (
                    _run_cli_with_formal_authority(*arguments)
                )
            self.assertEqual(
                0, transitioned.returncode, transitioned.stdout + transitioned.stderr
            )

        self._bind_exit_evidence_to_delivered_candidate(exit_evidence, run_dir)

        manifest = json.loads(exit_evidence.read_text(encoding="utf-8"))
        stale_implementation = subprocess.run(
            ["git", "rev-parse", "HEAD^"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout.strip()
        lineage_manifest = json.loads(json.dumps(manifest))
        lineage_manifest["implementation_commit"] = stale_implementation
        for fingerprint in lineage_manifest["artifact_fingerprints"]:
            blob = subprocess.run(
                ["git", "show", f"{stale_implementation}:{fingerprint['path']}"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=True,
            ).stdout
            fingerprint["sha256"] = hashlib.sha256(blob).hexdigest()
        lineage_evidence = exit_evidence.with_name("stale-implementation-evidence.json")
        _write_json(lineage_evidence, lineage_manifest)
        stale = platform_cutover_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(lineage_evidence),
            "--activated-at",
            "2026-08-09T13:45:00Z",
        )
        self.assertEqual(30, stale.returncode, stale.stdout + stale.stderr)
        stale_envelope = json.loads(stale.stdout)
        self.assertEqual(
            "bilibili_candidate_implementation_evidence_mismatch",
            stale_envelope["data"]["error_code"],
        )

        mismatched_run_id = "24242424242424242424242424242424"
        guarded = manifest["guarded_delivery_evidence"]
        guarded["run_id"] = mismatched_run_id
        artifacts = {item["role"]: item for item in guarded["artifacts"]}
        acceptance_path = PROJECT_ROOT / artifacts["acceptance_report_v2"]["path"]
        original_acceptance = acceptance_path.read_bytes()
        acceptance = json.loads(original_acceptance)
        acceptance["run_binding"]["run_id"] = mismatched_run_id
        acceptance["report_sha256"] = hashlib.sha256(
            (
                json.dumps(
                    {
                        key: value
                        for key, value in acceptance.items()
                        if key != "report_sha256"
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        _write_json(acceptance_path, acceptance)
        acceptance_sha = _sha256(acceptance_path)
        artifacts["acceptance_report_v2"]["sha256"] = acceptance_sha

        collection_path = PROJECT_ROOT / guarded["collection"]["path"]
        original_collection = collection_path.read_bytes()
        collection = json.loads(original_collection)
        collection["run_id"] = mismatched_run_id
        collection["artifacts"]["acceptance_report_v2"]["sha256"] = acceptance_sha
        _write_json(collection_path, collection)
        guarded["collection"]["sha256"] = _sha256(collection_path)
        mismatched = exit_evidence.with_name("mismatched-exit-evidence.json")
        _write_json(mismatched, manifest)
        rejected = platform_cutover_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(mismatched),
            "--activated-at",
            "2026-08-09T13:50:00Z",
        )
        self.assertEqual(30, rejected.returncode, rejected.stdout + rejected.stderr)
        rejected_envelope = json.loads(rejected.stdout)
        self.assertEqual(
            "guarded_delivery_candidate_binding",
            rejected_envelope["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "bilibili_guarded_run_differs_from_delivered_candidate",
            rejected_envelope["data"]["error_code"],
        )

        acceptance_path.write_bytes(original_acceptance)
        collection_path.write_bytes(original_collection)

        confirmed = platform_cutover_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T14:00:00Z",
        )
        self.assertEqual(0, confirmed.returncode, confirmed.stdout + confirmed.stderr)
        confirmed_envelope = json.loads(confirmed.stdout)
        self.assertEqual("CONFIRMED", confirmed_envelope["data"]["cutover_state"])
        self.assertEqual(
            {"bilibili": "active_kernel", "youtube": "active_legacy"},
            confirmed_envelope["data"]["platform_statuses"],
        )
        with sqlite3.connect(
            control / "platform-kernel-control.sqlite3"
        ) as platform_db:
            platform_db.row_factory = sqlite3.Row
            candidate = platform_db.execute(
                "SELECT state,candidate_json FROM platform_cutover_candidates "
                "WHERE platform='bilibili'"
            ).fetchone()
        self.assertEqual("CONFIRMED", candidate["state"])
        self.assertEqual("CONFIRMED", json.loads(candidate["candidate_json"])["state"])

    def test_final_activation_rejects_root_without_candidate(self) -> None:
        fixture = platform_cutover_test.Issue13PlatformCutoverTests(
            "test_bilibili_activation_publishes_single_platform_authority"
        )
        control, exit_evidence = fixture._write_valid_cutover_manifest()
        with sqlite3.connect(
            control / "platform-kernel-control.sqlite3"
        ) as platform_db:
            platform_db.execute("DELETE FROM platform_cutover_candidates")

        activated = platform_cutover_test._run_cli(
            "platform-kernel-activate",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--exit-evidence",
            str(exit_evidence),
            "--activated-at",
            "2026-08-09T14:00:00Z",
        )

        self.assertEqual(30, activated.returncode, activated.stdout + activated.stderr)
        envelope = json.loads(activated.stdout)
        self.assertEqual("bilibili_provisional_candidate_absent", envelope["data"]["error_code"])


if __name__ == "__main__":
    unittest.main()

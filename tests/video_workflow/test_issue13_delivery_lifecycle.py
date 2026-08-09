from __future__ import annotations

import hashlib
import copy
from contextlib import redirect_stdout
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore
import video2pdf_workflow_kernel.delivery_lifecycle as delivery_lifecycle_module
from video2pdf_workflow_kernel.delivery_lifecycle import (
    DeliveryLifecycleFault,
    DeliveryLifecycleProvider,
)
from video2pdf_workflow_kernel.errors import KernelConflict
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
from video2pdf_workflow_kernel.models import ProductionBootstrapResult
from video2pdf_workflow_kernel.platform_kernel import BilibiliPlatformCutoverPublisher
from video2pdf_workflow_kernel.cli import main as workflow_cli_main


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _acceptance_report(run_id: str, revision: int, status: str) -> dict:
    report = json.loads(
        (
            PROJECT_ROOT
            / "delivery-quality"
            / "v1"
            / "acceptance-report-v2.example.v1.json"
        ).read_text(encoding="utf-8")
    )
    report["overall_status"] = status
    report["routing_state"] = (
        "ready_for_delivery" if status == "pass" else "repair_required"
    )
    report["input_track"] = "kernel"
    report.pop("legacy_binding", None)
    report["run_binding"]["run_id"] = run_id
    report["run_binding"]["coordination_revision"] = revision
    report["report_sha256"] = hashlib.sha256(
        (
            json.dumps(
                {key: item for key, item in report.items() if key != "report_sha256"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
    return report


def _guard_report(status: str) -> dict:
    required_conditions = (
        "target_resolved",
        "allowed_artifacts_manifest_loaded",
        "final_pdf_in_manifest",
        "final_compile_provenance_current",
        "acceptance_report_v2_authority_current",
        "rendered_page_evidence_current",
        "artifact_fingerprints_current",
    )
    return {
        "schema_version": "1.0",
        "status": status,
        "stage": "accepted",
        "validated_by": "delivery_guard.py",
        "acceptance_report_status": "pass",
        "artifact_fingerprints": [
            {
                "path": "main.tex",
                "sha256": f"sha256:{'a' * 64}",
                "size_bytes": 1,
                "size_chars": 1,
            }
        ],
        "checked_conditions": [
            {"condition": condition, "status": "pass"}
            for condition in required_conditions
        ],
    }


def _run_cli(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(PROJECT_ROOT / "scripts" / "video_workflow.py"),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    return completed, json.loads(completed.stdout)


def _run_cli_with_formal_platform_authority(
    *arguments: str,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Run one lifecycle command with an explicit current-authority test seam.

    These lifecycle fixtures predate the independently published Platform Kernel
    authority graph.  They exercise lifecycle persistence rather than platform
    publication, so the test supplies only the formal ``require_current`` result
    while leaving all lifecycle and decision validators active.
    """

    stdout = io.StringIO()
    with patch.object(
        BilibiliPlatformCutoverPublisher,
        "require_current",
        return_value={
            "platform": "bilibili",
            "authority_status": "current",
        },
    ), redirect_stdout(stdout):
        returncode = workflow_cli_main(list(arguments))
    completed = subprocess.CompletedProcess(
        args=list(arguments),
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr="",
    )
    return completed, json.loads(completed.stdout)


class Issue13DeliveryLifecycleTests(unittest.TestCase):
    def _make_generating_run(
        self,
        *,
        project: Path | None = None,
        run_name: str = "bilibili_run",
        session_id: str = "session-a",
        run_id: str = "13131313131313131313131313131313",
    ) -> tuple[Path, Path]:
        if project is None:
            case = new_case_dir(self.id(), label="issue13-delivery-lifecycle")
            project = case / "project"
        workspace = project / "workspace"
        run_dir = workspace / run_name
        run_dir.mkdir(parents=True)

        video_target_path = run_dir / "review" / "acceptance" / "delivery_target.json"
        session_target_path = (
            project
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / session_id
            / "current.json"
        )
        task_index_path = project / ".codex" / "delivery-targets" / "task-index.json"
        video_target = {
            "schema_name": "kernel-delivery-target",
            "schema_version": "1.0.0",
            "projection_kind": "video_target",
            "projection_revision": 1,
            "run_id": run_id,
            "run_revision": 1,
            "lifecycle_intent_id": "0" * 64,
            "video_output_dir": str(run_dir.resolve()),
            "stage": "generating",
            "ownership": {"session_id": session_id, "generation": 1},
            "artifacts": {
                "final_pdf": None,
                "main_tex": None,
                "final_compile_report": None,
                "acceptance_report": None,
                "delivery_guard_report": None,
            },
            "global_gate_authority": None,
        }
        _write_json(video_target_path, video_target)
        session_target = {
            "schema_name": "kernel-session-delivery-target",
            "schema_version": "1.0.0",
            "projection_kind": "session_target",
            "projection_revision": 1,
            "projection_path": str(session_target_path.resolve()),
            "session_id": session_id,
            "run_id": run_id,
            "run_revision": 1,
            "lifecycle_intent_id": "0" * 64,
            "stage": "generating",
            "ownership_generation": 1,
            "owner_status": "active",
            "video_output_dir": str(run_dir.resolve()),
            "video_target": {
                "path": str(video_target_path.resolve()),
                "projection_revision": 1,
                "sha256": _sha256(video_target_path),
            },
        }
        _write_json(session_target_path, session_target)
        entry = {
            "run_id": run_id,
            "canonical_platform": "bilibili",
            "video_output_dir": str(run_dir.resolve()),
            "run_revision": 1,
            "lifecycle_intent_id": "0" * 64,
            "stage": "generating",
            "session_id": session_id,
            "ownership_generation": 1,
            "video_target": {
                "path": str(video_target_path.resolve()),
                "projection_revision": 1,
                "sha256": _sha256(video_target_path),
            },
            "session_target": {
                "path": str(session_target_path.resolve()),
                "projection_revision": 1,
                "sha256": _sha256(session_target_path),
            },
            "archive": None,
        }
        if task_index_path.is_file():
            task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
            task_index["projection_revision"] += 1
            task_index["entries"] = sorted(
                [*task_index["entries"], entry], key=lambda item: item["run_id"]
            )
        else:
            task_index = {
                "schema_name": "kernel-delivery-task-index",
                "schema_version": "1.0.0",
                "projection_kind": "task_index",
                "projection_revision": 1,
                "entries": [entry],
            }
        _write_json(task_index_path, task_index)

        run_record = json.loads(
            (PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "contracts" / "run-record.v4.valid.json").read_text(
                encoding="utf-8"
            )
        )
        run_record.update(
            {
                "run_id": run_id,
                "output_path": str(run_dir.resolve()),
                "initialization_intent_id": f"initialize-issue13-lifecycle-{run_id}",
                "coordination_revision": 1,
                "last_mutation_intent_id": None,
            }
        )
        run_record["delivery"] = {
            "stage": "generating",
            "ownership": {"session_id": session_id, "generation": 1},
            "projections": {
                "video_target": {
                    "path": "review/acceptance/delivery_target.json",
                    "projection_revision": 1,
                    "sha256": _sha256(video_target_path),
                },
                "session_target": {
                    "path": str(session_target_path.resolve()),
                    "projection_revision": 1,
                    "sha256": _sha256(session_target_path),
                },
                "task_index": {
                    "path": str(task_index_path.resolve()),
                    "projection_revision": task_index["projection_revision"],
                    "sha256": _sha256(task_index_path),
                },
                "archive": None,
            },
        }
        run_path = run_dir / "workflow" / "run.json"
        _write_json(run_path, run_record)
        store = ControlStore.initialize(workspace, ContractRegistry(PROJECT_ROOT))
        store.prepare_initialization(
            run_id=run_id,
            output_path=run_dir,
            intent_id=run_record["initialization_intent_id"],
            staging_path=workspace / ".initialization-staging" / run_id,
        )
        store.bind_publication_expectations(
            run_record["initialization_intent_id"],
            expected_run_record_sha256=_sha256(run_path),
            canonical_platform="bilibili",
            canonical_item_id=run_record["canonical_item_id"],
            source_identity=run_record["source_identity"],
            source_manifest_sha256=None,
        )
        store.transition_intent(
            run_record["initialization_intent_id"],
            expected_state="PREPARED",
            new_state="PUBLISHED",
        )
        store.transition_intent(
            run_record["initialization_intent_id"],
            expected_state="PUBLISHED",
            new_state="RECORD_COMMITTED",
            run_record_sha256=_sha256(run_path),
        )
        store.transition_intent(
            run_record["initialization_intent_id"],
            expected_state="RECORD_COMMITTED",
            new_state="COMMITTED",
            run_record_sha256=_sha256(run_path),
        )

        final_pdf = run_dir / "article.pdf"
        final_pdf.write_bytes(b"%PDF-1.7\nissue13\n")
        main_tex = run_dir / "main.tex"
        main_tex.write_text("\\documentclass{article}\n", encoding="utf-8")
        compile_report = run_dir / "review" / "latex" / "compile_report.json"
        _write_json(compile_report, {"status": "pass"})
        render_manifest = run_dir / "review" / "acceptance" / "rendered-pages.json"
        _write_json(render_manifest, {"status": "pass", "page_count": 1})
        gate = project / ".workflow-control" / "active_global_gate.json"
        _write_json(gate, {"active_global_gate": "acceptance_report_v2", "generation": 1})
        with sqlite3.connect(
            project / ".workflow-control" / "platform-kernel-control.sqlite3"
        ) as platform_control:
            platform_control.execute(
                "CREATE TABLE IF NOT EXISTS platform_cutover_authority ("
                "platform TEXT PRIMARY KEY, generation INTEGER NOT NULL, "
                "evidence_sha256 TEXT NOT NULL, authority_sha256 TEXT NOT NULL)"
            )
            platform_control.execute(
                "INSERT OR REPLACE INTO platform_cutover_authority "
                "VALUES('bilibili',1,?,?)",
                ("e" * 64, "a" * 64),
            )
        evidence_path = run_dir / "review" / "acceptance" / "delivery-transition-evidence.json"
        _write_json(
            evidence_path,
            {
                "schema_name": "delivery-transition-evidence",
                "schema_version": "1.0.0",
                "run_id": run_id,
                "from_stage": "generating",
                "to_stage": "ready_for_delivery",
                "artifacts": {
                    "final_pdf": {"path": str(final_pdf), "sha256": _sha256(final_pdf)},
                    "main_tex": {"path": str(main_tex), "sha256": _sha256(main_tex)},
                    "final_compile_report": {
                        "path": str(compile_report),
                        "sha256": _sha256(compile_report),
                    },
                    "render_evidence_manifest": {
                        "path": str(render_manifest),
                        "sha256": _sha256(render_manifest),
                    },
                },
                "global_gate_authority": {
                    "path": str(gate),
                    "generation": 1,
                    "sha256": _sha256(gate),
                },
            },
        )
        return run_dir, evidence_path

    def _make_ready_run(self) -> Path:
        run_dir, evidence = self._make_generating_run()
        completed, envelope = _run_cli(
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "generating",
            "--to-stage",
            "ready_for_delivery",
            "--session-id",
            "session-a",
            "--expected-run-revision",
            "1",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(evidence),
            "--transitioned-at",
            "2026-08-09T02:00:00Z",
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stdout + completed.stderr)
        if envelope["data"]["stage"] != "ready_for_delivery":
            raise AssertionError(envelope)
        return run_dir

    def test_transition_refreshes_delivery_snapshot_after_non_delivery_run_mutation(
        self,
    ) -> None:
        run_dir, evidence = self._make_generating_run()
        run_path = run_dir / "workflow" / "run.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        store = ControlStore(run_dir.parent, ContractRegistry(PROJECT_ROOT))
        prior_sha = _sha256(run_path)
        mutation_id = store.derive_run_state_mutation_id(
            run_id=record["run_id"],
            expected_run_revision=1,
            old_run_record_sha256=prior_sha,
        )
        replacement = json.loads(json.dumps(record))
        replacement["coordination_revision"] = 2
        replacement["last_mutation_intent_id"] = mutation_id
        replacement["source_state"] = "stale"
        store.prepare_run_state_mutation(
            run_id=record["run_id"],
            expected_run_revision=1,
            old_run_record_sha256=prior_sha,
            replacement_run_record=replacement,
        )
        _write_json(run_path, replacement)
        store.commit_run_state_mutation(mutation_id)
        self._make_generating_run(
            project=run_dir.parents[1],
            run_name="other_bilibili_run",
            session_id="session-b",
            run_id="24242424242424242424242424242424",
        )

        completed, envelope = _run_cli(
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "generating",
            "--to-stage",
            "ready_for_delivery",
            "--session-id",
            "session-a",
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(evidence),
            "--transitioned-at",
            "2026-08-09T02:00:00Z",
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        current = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertEqual(3, current["coordination_revision"])
        self.assertEqual("ready_for_delivery", current["delivery"]["stage"])

    def _write_transition_evidence(
        self,
        run_dir: Path,
        *,
        from_stage: str,
        to_stage: str,
        artifacts: dict[str, Path],
    ) -> Path:
        run_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        project = run_dir.parents[1]
        gate = project / ".workflow-control" / "active_global_gate.json"
        evidence = (
            run_dir
            / "review"
            / "acceptance"
            / f"delivery-transition-{from_stage}-{to_stage}.json"
        )
        _write_json(
            evidence,
            {
                "schema_name": "delivery-transition-evidence",
                "schema_version": "1.0.0",
                "run_id": run_record["run_id"],
                "from_stage": from_stage,
                "to_stage": to_stage,
                "artifacts": {
                    role: {"path": str(path), "sha256": _sha256(path)}
                    for role, path in artifacts.items()
                },
                "global_gate_authority": {
                    "path": str(gate),
                    "generation": 1,
                    "sha256": _sha256(gate),
                },
            },
        )
        return evidence

    def _make_delivered_run(self) -> Path:
        run_dir = self._make_ready_run()
        acceptance_report = (
            run_dir / "review" / "acceptance" / "acceptance_report.json"
        )
        run_id = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )["run_id"]
        _write_json(acceptance_report, _acceptance_report(run_id, 2, "pass"))
        guard_report = (
            run_dir
            / "review"
            / "acceptance"
            / "delivery_guard_report.json"
        )
        _write_json(guard_report, _guard_report("pass"))
        transitions = (
            (
                "ready_for_delivery",
                "accepted",
                2,
                {"acceptance_report": acceptance_report},
                "2026-08-09T02:02:00Z",
            ),
            (
                "accepted",
                "delivered",
                3,
                {"delivery_guard_report": guard_report},
                "2026-08-09T02:03:00Z",
            ),
        )
        for from_stage, to_stage, revision, artifacts, transitioned_at in transitions:
            evidence = self._write_transition_evidence(
                run_dir,
                from_stage=from_stage,
                to_stage=to_stage,
                artifacts=artifacts,
            )
            completed, envelope = _run_cli_with_formal_platform_authority(
                "delivery-transition",
                "--run-dir",
                str(run_dir),
                "--from-stage",
                from_stage,
                "--to-stage",
                to_stage,
                "--session-id",
                "session-a",
                "--expected-run-revision",
                str(revision),
                "--expected-ownership-generation",
                "1",
                "--evidence",
                str(evidence),
                "--transitioned-at",
                transitioned_at,
            )
            if completed.returncode != 0:
                raise AssertionError(completed.stdout + completed.stderr)
            if envelope["data"]["stage"] != to_stage:
                raise AssertionError(envelope)
        return run_dir

    def test_acceptance_and_delivery_transitions_reject_failed_decision_evidence(
        self,
    ) -> None:
        run_dir = self._make_ready_run()
        run_path = run_dir / "workflow" / "run.json"
        run_id = json.loads(run_path.read_text(encoding="utf-8"))["run_id"]
        acceptance_report = (
            run_dir / "review" / "acceptance" / "acceptance_report.json"
        )
        _write_json(acceptance_report, _acceptance_report(run_id, 2, "fail"))
        evidence = self._write_transition_evidence(
            run_dir,
            from_stage="ready_for_delivery",
            to_stage="accepted",
            artifacts={"acceptance_report": acceptance_report},
        )
        before = self._delivery_authority_sha256_snapshot(run_dir)
        failed, envelope = _run_cli(
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "ready_for_delivery",
            "--to-stage",
            "accepted",
            "--session-id",
            "session-a",
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(evidence),
            "--transitioned-at",
            "2026-08-09T02:02:00Z",
        )
        self.assertNotEqual(0, failed.returncode)
        self.assertEqual("acceptance_decision", envelope["data"]["first_failing_gate"])
        self.assertEqual(before, self._delivery_authority_sha256_snapshot(run_dir))

        acceptance = json.loads(acceptance_report.read_text(encoding="utf-8"))
        acceptance["overall_status"] = "pass"
        acceptance["routing_state"] = "ready_for_delivery"
        acceptance["report_sha256"] = hashlib.sha256(
            (
                json.dumps(
                    {
                        key: item
                        for key, item in acceptance.items()
                        if key != "report_sha256"
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        _write_json(acceptance_report, acceptance)
        accepted_evidence = self._write_transition_evidence(
            run_dir,
            from_stage="ready_for_delivery",
            to_stage="accepted",
            artifacts={"acceptance_report": acceptance_report},
        )
        accepted, accepted_envelope = _run_cli_with_formal_platform_authority(
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "ready_for_delivery",
            "--to-stage",
            "accepted",
            "--session-id",
            "session-a",
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(accepted_evidence),
            "--transitioned-at",
            "2026-08-09T02:02:30Z",
        )
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        self.assertEqual("accepted", accepted_envelope["data"]["stage"])
        guard_report = (
            run_dir / "review" / "acceptance" / "delivery_guard_report.json"
        )
        _write_json(guard_report, _guard_report("fail"))
        delivered_evidence = self._write_transition_evidence(
            run_dir,
            from_stage="accepted",
            to_stage="delivered",
            artifacts={"delivery_guard_report": guard_report},
        )
        before_delivery = self._delivery_authority_sha256_snapshot(run_dir)
        delivered, delivered_envelope = _run_cli(
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "accepted",
            "--to-stage",
            "delivered",
            "--session-id",
            "session-a",
            "--expected-run-revision",
            "3",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(delivered_evidence),
            "--transitioned-at",
            "2026-08-09T02:03:00Z",
        )
        self.assertNotEqual(0, delivered.returncode)
        self.assertEqual(
            "delivery_guard_decision",
            delivered_envelope["data"]["first_failing_gate"],
        )
        self.assertEqual(
            before_delivery, self._delivery_authority_sha256_snapshot(run_dir)
        )

    def _projection_sha256_snapshot(self, run_dir: Path) -> dict[str, str]:
        run_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        snapshot: dict[str, str] = {}
        for name in ("video_target", "session_target", "task_index"):
            path = Path(run_record["delivery"]["projections"][name]["path"])
            if not path.is_absolute():
                path = run_dir / path
            snapshot[name] = _sha256(path)
        return snapshot

    def _prepare_source_promotion(self, run_dir: Path) -> str:
        run_path = run_dir / "workflow" / "run.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        source_ready = json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "contracts"
                / "run-record.v3.valid.json"
            ).read_text(encoding="utf-8")
        )
        old_run_sha256 = _sha256(run_path)
        source_epoch = run_record["source_epoch"] + 1
        source_manifest_sha256 = "e" * 64
        source_version = "9" * 64
        intent_id = ControlStore.derive_source_publication_intent_id(
            run_id=run_record["run_id"],
            source_epoch=source_epoch,
            expected_run_revision=run_record["coordination_revision"],
            old_run_record_sha256=old_run_sha256,
        )
        replacement = copy.deepcopy(run_record)
        replacement.update(
            {
                "source_version": source_version,
                "source_epoch": source_epoch,
                "source_state": source_ready["source_state"],
                "phase": source_ready["phase"],
                "coordination_revision": run_record["coordination_revision"] + 1,
                "last_mutation_intent_id": intent_id,
            }
        )
        replacement["artifact_generations"] = copy.deepcopy(
            source_ready["artifact_generations"]
        )
        for generation in replacement["artifact_generations"].values():
            generation["source_epoch"] = source_epoch
        replacement["artifact_generations"]["source_manifest"][
            "sha256"
        ] = source_manifest_sha256
        replacement["checkpoints"] = copy.deepcopy(source_ready["checkpoints"])
        replacement["checkpoints"]["source_ready"]["artifact_bindings"][-1][
            "sha256"
        ] = source_manifest_sha256
        replacement["checkpoints"]["source_ready"][
            "evidence_sha256"
        ] = source_manifest_sha256
        store = ControlStore(run_dir.parent, ContractRegistry(PROJECT_ROOT))
        prepared = store.prepare_source_publication(
            run_id=run_record["run_id"],
            source_epoch=source_epoch,
            expected_run_revision=run_record["coordination_revision"],
            old_run_record_sha256=old_run_sha256,
            replacement_run_record=replacement,
            source_manifest_sha256=source_manifest_sha256,
            source_identity=run_record["source_identity"],
            source_version=source_version,
        )
        self.assertEqual("PREPARED", prepared["state"])
        return intent_id

    def test_delivery_transition_is_rejected_without_writes_while_source_promotion_owns_slot(
        self,
    ) -> None:
        run_dir, evidence = self._make_generating_run()
        source_intent_id = self._prepare_source_promotion(run_dir)
        run_path = run_dir / "workflow" / "run.json"
        projection_sha256s = self._projection_sha256_snapshot(run_dir)
        run_sha256 = _sha256(run_path)
        provider = DeliveryLifecycleProvider(PROJECT_ROOT)

        with self.assertRaisesRegex(KernelConflict, "Promotion Slot"):
            provider.transition(
                run_dir=run_dir,
                from_stage="generating",
                to_stage="ready_for_delivery",
                session_id="session-a",
                expected_run_revision=1,
                expected_ownership_generation=1,
                evidence_path=evidence,
                transitioned_at="2026-08-09T02:09:00Z",
            )

        store = ControlStore(run_dir.parent, ContractRegistry(PROJECT_ROOT))
        self.assertEqual(run_sha256, _sha256(run_path))
        self.assertEqual(projection_sha256s, self._projection_sha256_snapshot(run_dir))
        self.assertEqual(
            source_intent_id,
            store.active_source_publication(
                json.loads(run_path.read_text(encoding="utf-8"))["run_id"]
            )["intent_id"],
        )
        with sqlite3.connect(store.path) as connection:
            delivery_intent_count = connection.execute(
                "SELECT COUNT(*) FROM delivery_lifecycle_intents"
            ).fetchone()[0]
        self.assertEqual(0, delivery_intent_count)

    def test_reconciled_abort_fences_the_original_transition_writer(self) -> None:
        run_dir, evidence = self._make_generating_run()
        provider = DeliveryLifecycleProvider(PROJECT_ROOT)
        arguments = {
            "run_dir": run_dir,
            "from_stage": "generating",
            "to_stage": "ready_for_delivery",
            "session_id": "session-a",
            "expected_run_revision": 1,
            "expected_ownership_generation": 1,
            "evidence_path": evidence,
            "transitioned_at": "2026-08-09T02:09:30Z",
        }

        with self.assertRaises(DeliveryLifecycleFault):
            provider.transition(**arguments, fault_point="after_intent_prepared")
        reconciliation = provider.reconcile(run_dir=run_dir)
        self.assertEqual("rolled_back", reconciliation["recovery_outcome"])

        store = ControlStore(run_dir.parent, ContractRegistry(PROJECT_ROOT))
        with sqlite3.connect(store.path) as connection:
            rows = connection.execute(
                "SELECT intent_id,state FROM delivery_lifecycle_intents"
            ).fetchall()
        self.assertEqual(1, len(rows))
        with self.assertRaises(KernelConflict):
            provider._advance_intent_state(
                store,
                intent_id=rows[0][0],
                expected_state="PREPARED",
                new_state="FILES_PUBLISHED",
            )
        with sqlite3.connect(store.path) as connection:
            states = connection.execute(
                "SELECT state FROM delivery_lifecycle_intents"
            ).fetchall()
        self.assertEqual([("ABORTED",)], states)

    def _delivery_authority_sha256_snapshot(self, run_dir: Path) -> dict[str, str]:
        run_path = run_dir / "workflow" / "run.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        store = ControlStore(run_dir.parent, ContractRegistry(PROJECT_ROOT))
        return {
            "run_record": _sha256(run_path),
            **self._projection_sha256_snapshot(run_dir),
            "control_store": store.current_run_record_sha(run_record["run_id"]),
        }

    def test_delivery_handoff_rejects_parent_session_id_without_authority_mutation(
        self,
    ) -> None:
        run_dir = self._make_ready_run()
        before = self._delivery_authority_sha256_snapshot(run_dir)

        completed, envelope = _run_cli(
            "delivery-handoff",
            "--run-dir",
            str(run_dir),
            "--from-session-id",
            "session-a",
            "--to-session-id",
            "..",
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--handed-off-at",
            "2026-08-09T02:00:01Z",
        )

        self.assertEqual(
            {
                "returncode": 2,
                "status": "error",
                "classification": "usage_error",
                "authority_sha256s": before,
            },
            {
                "returncode": completed.returncode,
                "status": envelope["status"],
                "classification": envelope["classification"],
                "authority_sha256s": self._delivery_authority_sha256_snapshot(
                    run_dir
                ),
            },
            completed.stdout + completed.stderr,
        )

    def test_delivery_archive_rejects_parent_session_id_without_authority_mutation(
        self,
    ) -> None:
        run_dir = self._make_delivered_run()
        before = self._delivery_authority_sha256_snapshot(run_dir)

        completed, envelope = _run_cli(
            "delivery-archive",
            "--run-dir",
            str(run_dir),
            "--session-id",
            "..",
            "--expected-run-revision",
            "4",
            "--expected-ownership-generation",
            "1",
            "--archived-at",
            "2026-08-09T02:04:00Z",
        )

        self.assertEqual(
            {
                "returncode": 2,
                "status": "error",
                "classification": "usage_error",
                "authority_sha256s": before,
            },
            {
                "returncode": completed.returncode,
                "status": envelope["status"],
                "classification": envelope["classification"],
                "authority_sha256s": self._delivery_authority_sha256_snapshot(
                    run_dir
                ),
            },
            completed.stdout + completed.stderr,
        )

    def test_generating_to_ready_commits_run_last_and_refreshes_all_projections(self) -> None:
        run_dir, evidence = self._make_generating_run()

        completed, envelope = _run_cli(
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "generating",
            "--to-stage",
            "ready_for_delivery",
            "--session-id",
            "session-a",
            "--expected-run-revision",
            "1",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(evidence),
            "--transitioned-at",
            "2026-08-09T02:00:00Z",
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("delivery_lifecycle_transitioned", envelope["classification"])
        self.assertEqual("ready_for_delivery", envelope["data"]["stage"])
        self.assertEqual(2, envelope["data"]["run_revision"])
        run_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual("ready_for_delivery", run_record["delivery"]["stage"])
        self.assertEqual(2, run_record["coordination_revision"])
        self.assertEqual(
            envelope["data"]["intent_id"], run_record["last_mutation_intent_id"]
        )
        store = ControlStore(run_dir.parent, ContractRegistry(PROJECT_ROOT))
        self.assertEqual(
            _sha256(run_dir / "workflow" / "run.json"),
            store.current_run_record_sha(run_record["run_id"]),
        )
        for binding in ("video_target", "session_target", "task_index"):
            projection = run_record["delivery"]["projections"][binding]
            projection_path = Path(projection["path"])
            if not projection_path.is_absolute():
                projection_path = run_dir / projection_path
            self.assertEqual(_sha256(projection_path), projection["sha256"])

    def test_ready_delivery_handoff_transfers_owner_and_preserves_old_session_audit(
        self,
    ) -> None:
        run_dir = self._make_ready_run()

        completed, envelope = _run_cli(
            "delivery-handoff",
            "--run-dir",
            str(run_dir),
            "--from-session-id",
            "session-a",
            "--to-session-id",
            "session-b",
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--handed-off-at",
            "2026-08-09T02:01:00Z",
        )

        self.assertEqual(
            {
                "returncode": 0,
                "classification": "delivery_ownership_handed_off",
                "stage": "ready_for_delivery",
                "run_revision": 3,
                "ownership_generation": 2,
            },
            {
                "returncode": completed.returncode,
                "classification": envelope["classification"],
                "stage": envelope.get("data", {}).get("stage"),
                "run_revision": envelope.get("data", {}).get(
                    "run_revision"
                ),
                "ownership_generation": envelope.get("data", {}).get(
                    "ownership_generation"
                ),
            },
        )

        project = run_dir.parents[1]
        old_session_target = json.loads(
            (
                project
                / ".codex"
                / "delivery-targets"
                / "sessions"
                / "session-a"
                / "current.json"
            ).read_text(encoding="utf-8")
        )
        new_session_target = json.loads(
            (
                project
                / ".codex"
                / "delivery-targets"
                / "sessions"
                / "session-b"
                / "current.json"
            ).read_text(encoding="utf-8")
        )
        run_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        task_index = json.loads(
            (
                project
                / ".codex"
                / "delivery-targets"
                / "task-index.json"
            ).read_text(encoding="utf-8")
        )
        run_entries = [
            entry
            for entry in task_index["entries"]
            if entry["run_id"] == run_record["run_id"]
        ]

        self.assertEqual(
            {
                "run": {
                    "stage": "ready_for_delivery",
                    "run_revision": 3,
                    "session_id": "session-b",
                    "ownership_generation": 2,
                },
                "old_session": {
                    "stage": "ready_for_delivery",
                    "session_id": "session-a",
                    "owner_status": "superseded",
                    "ownership_generation": 1,
                },
                "new_session": {
                    "stage": "ready_for_delivery",
                    "session_id": "session-b",
                    "owner_status": "active",
                    "ownership_generation": 2,
                },
                "task_index_owners": ["session-b"],
            },
            {
                "run": {
                    "stage": run_record["delivery"]["stage"],
                    "run_revision": run_record["coordination_revision"],
                    "session_id": run_record["delivery"]["ownership"][
                        "session_id"
                    ],
                    "ownership_generation": run_record["delivery"][
                        "ownership"
                    ]["generation"],
                },
                "old_session": {
                    "stage": old_session_target["stage"],
                    "session_id": old_session_target["session_id"],
                    "owner_status": old_session_target["owner_status"],
                    "ownership_generation": old_session_target[
                        "ownership_generation"
                    ],
                },
                "new_session": {
                    "stage": new_session_target["stage"],
                    "session_id": new_session_target["session_id"],
                    "owner_status": new_session_target["owner_status"],
                    "ownership_generation": new_session_target[
                        "ownership_generation"
                    ],
                },
                "task_index_owners": [
                    entry["session_id"] for entry in run_entries
                ],
            },
        )

    def test_delivered_target_archive_removes_session_projection_and_keeps_audit(
        self,
    ) -> None:
        run_dir = self._make_delivered_run()

        completed, envelope = _run_cli(
            "delivery-archive",
            "--run-dir",
            str(run_dir),
            "--session-id",
            "session-a",
            "--expected-run-revision",
            "4",
            "--expected-ownership-generation",
            "1",
            "--archived-at",
            "2026-08-09T02:04:00Z",
        )

        self.assertEqual(
            {
                "returncode": 0,
                "classification": "delivery_target_archived",
                "stage": "delivered",
                "run_revision": 5,
            },
            {
                "returncode": completed.returncode,
                "classification": envelope["classification"],
                "stage": envelope.get("data", {}).get("stage"),
                "run_revision": envelope.get("data", {}).get(
                    "run_revision"
                ),
            },
        )

        project = run_dir.parents[1]
        session_target_path = (
            project
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / "session-a"
            / "current.json"
        )
        task_index = json.loads(
            (
                project
                / ".codex"
                / "delivery-targets"
                / "task-index.json"
            ).read_text(encoding="utf-8")
        )
        run_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        archive_binding = run_record["delivery"]["projections"]["archive"]
        archive_path = Path(archive_binding["path"])
        run_entries = [
            entry
            for entry in task_index["entries"]
            if entry["run_id"] == run_record["run_id"]
        ]

        self.assertEqual(
            {
                "run": {
                    "stage": "delivered",
                    "run_revision": 5,
                    "session_projection": None,
                    "archive_projection_revision": 1,
                },
                "session_target_exists": False,
                "archive_exists": True,
                "task_index": {
                    "entry_count": 1,
                    "session_target": None,
                    "archive_path": str(archive_path),
                },
            },
            {
                "run": {
                    "stage": run_record["delivery"]["stage"],
                    "run_revision": run_record["coordination_revision"],
                    "session_projection": run_record["delivery"][
                        "projections"
                    ]["session_target"],
                    "archive_projection_revision": archive_binding[
                        "projection_revision"
                    ],
                },
                "session_target_exists": session_target_path.exists(),
                "archive_exists": archive_path.is_file(),
                "task_index": {
                    "entry_count": len(run_entries),
                    "session_target": run_entries[0]["session_target"],
                    "archive_path": run_entries[0]["archive"]["path"],
                },
            },
        )

    def test_interrupted_delivery_transition_rolls_back_and_retries_cleanly(
        self,
    ) -> None:
        run_dir, evidence = self._make_generating_run()
        initial_projection_sha256s = self._projection_sha256_snapshot(run_dir)
        transition_arguments = (
            "delivery-transition",
            "--run-dir",
            str(run_dir),
            "--from-stage",
            "generating",
            "--to-stage",
            "ready_for_delivery",
            "--session-id",
            "session-a",
            "--expected-run-revision",
            "1",
            "--expected-ownership-generation",
            "1",
            "--evidence",
            str(evidence),
            "--transitioned-at",
            "2026-08-09T02:05:00Z",
        )

        interrupted, fault = _run_cli(
            *transition_arguments,
            "--fault-point",
            "after_task_index_write",
        )
        self.assertNotEqual(0, interrupted.returncode)
        self.assertEqual(
            "injected_delivery_lifecycle_fault",
            fault["classification"],
        )
        run_after_fault = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"stage": "generating", "run_revision": 1},
            {
                "stage": run_after_fault["delivery"]["stage"],
                "run_revision": run_after_fault["coordination_revision"],
            },
        )

        reconciled, reconciliation = _run_cli(
            "delivery-reconcile",
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(
            {
                "returncode": 0,
                "classification": "delivery_lifecycle_reconciled",
                "recovery_outcome": "rolled_back",
                "stage": "generating",
                "run_revision": 1,
                "projection_sha256s": initial_projection_sha256s,
            },
            {
                "returncode": reconciled.returncode,
                "classification": reconciliation["classification"],
                "recovery_outcome": reconciliation.get("data", {}).get(
                    "recovery_outcome"
                ),
                "stage": reconciliation.get("data", {}).get("stage"),
                "run_revision": reconciliation.get("data", {}).get(
                    "run_revision"
                ),
                "projection_sha256s": self._projection_sha256_snapshot(
                    run_dir
                ),
            },
        )

        retried, retry = _run_cli(*transition_arguments)
        self.assertEqual(
            {
                "returncode": 0,
                "classification": "delivery_lifecycle_transitioned",
                "stage": "ready_for_delivery",
                "run_revision": 2,
            },
            {
                "returncode": retried.returncode,
                "classification": retry["classification"],
                "stage": retry.get("data", {}).get("stage"),
                "run_revision": retry.get("data", {}).get("run_revision"),
            },
        )

    def test_interrupted_delivery_handoff_rolls_back_by_path_and_retries_cleanly(
        self,
    ) -> None:
        run_dir = self._make_ready_run()
        project = run_dir.parents[1]
        run_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        video_target_path = run_dir / run_record["delivery"]["projections"][
            "video_target"
        ]["path"]
        old_session_path = Path(
            run_record["delivery"]["projections"]["session_target"]["path"]
        )
        task_index_path = Path(
            run_record["delivery"]["projections"]["task_index"]["path"]
        )
        new_session_path = (
            project
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / "session-b"
            / "current.json"
        )
        initial_sha256s = {
            "video_target": _sha256(video_target_path),
            "old_session": _sha256(old_session_path),
            "task_index": _sha256(task_index_path),
        }
        handoff_arguments = (
            "delivery-handoff",
            "--run-dir",
            str(run_dir),
            "--from-session-id",
            "session-a",
            "--to-session-id",
            "session-b",
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--handed-off-at",
            "2026-08-09T02:05:30Z",
        )

        interrupted, fault = _run_cli(
            *handoff_arguments,
            "--fault-point",
            "after_video_target_write",
        )
        self.assertNotEqual(0, interrupted.returncode)
        self.assertEqual("injected_delivery_lifecycle_fault", fault["classification"])

        reconciled, reconciliation = _run_cli(
            "delivery-reconcile",
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(
            {
                "returncode": 0,
                "classification": "delivery_lifecycle_reconciled",
                "recovery_outcome": "rolled_back",
                "projection_sha256s": initial_sha256s,
                "new_session_exists": False,
            },
            {
                "returncode": reconciled.returncode,
                "classification": reconciliation["classification"],
                "recovery_outcome": reconciliation.get("data", {}).get(
                    "recovery_outcome"
                ),
                "projection_sha256s": {
                    "video_target": _sha256(video_target_path),
                    "old_session": _sha256(old_session_path),
                    "task_index": _sha256(task_index_path),
                },
                "new_session_exists": new_session_path.exists(),
            },
        )

        retried, retry = _run_cli(*handoff_arguments)
        self.assertEqual(
            {
                "returncode": 0,
                "classification": "delivery_ownership_handed_off",
                "session_id": "session-b",
                "new_session_exists": True,
            },
            {
                "returncode": retried.returncode,
                "classification": retry["classification"],
                "session_id": json.loads(
                    (run_dir / "workflow" / "run.json").read_text(
                        encoding="utf-8"
                    )
                )["delivery"]["ownership"]["session_id"],
                "new_session_exists": new_session_path.is_file(),
            },
        )

    def test_interrupted_delivery_archive_rolls_back_by_path_and_retries_cleanly(
        self,
    ) -> None:
        run_dir = self._make_delivered_run()
        project = run_dir.parents[1]
        run_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        video_target_path = run_dir / run_record["delivery"]["projections"][
            "video_target"
        ]["path"]
        session_path = Path(
            run_record["delivery"]["projections"]["session_target"]["path"]
        )
        task_index_path = Path(
            run_record["delivery"]["projections"]["task_index"]["path"]
        )
        archive_root = (
            project / ".codex" / "delivery-targets" / "archive" / "session-a"
        )
        initial_sha256s = {
            "video_target": _sha256(video_target_path),
            "session_target": _sha256(session_path),
            "task_index": _sha256(task_index_path),
        }
        archive_arguments = (
            "delivery-archive",
            "--run-dir",
            str(run_dir),
            "--session-id",
            "session-a",
            "--expected-run-revision",
            "4",
            "--expected-ownership-generation",
            "1",
            "--archived-at",
            "2026-08-09T02:05:45Z",
        )

        interrupted, fault = _run_cli(
            *archive_arguments,
            "--fault-point",
            "after_video_target_write",
        )
        self.assertNotEqual(0, interrupted.returncode)
        self.assertEqual("injected_delivery_lifecycle_fault", fault["classification"])

        reconciled, reconciliation = _run_cli(
            "delivery-reconcile",
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(
            {
                "returncode": 0,
                "classification": "delivery_lifecycle_reconciled",
                "recovery_outcome": "rolled_back",
                "projection_sha256s": initial_sha256s,
                "session_target_exists": True,
                "archive_files": [],
            },
            {
                "returncode": reconciled.returncode,
                "classification": reconciliation["classification"],
                "recovery_outcome": reconciliation.get("data", {}).get(
                    "recovery_outcome"
                ),
                "projection_sha256s": {
                    "video_target": _sha256(video_target_path),
                    "session_target": _sha256(session_path),
                    "task_index": _sha256(task_index_path),
                },
                "session_target_exists": session_path.is_file(),
                "archive_files": (
                    sorted(path.name for path in archive_root.glob("*.json"))
                    if archive_root.is_dir()
                    else []
                ),
            },
        )

        retried, retry = _run_cli(*archive_arguments)
        retried_run = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        archive_binding = retried_run["delivery"]["projections"]["archive"]
        archive_path = Path(archive_binding["path"])
        self.assertEqual(
            {
                "returncode": 0,
                "classification": "delivery_target_archived",
                "session_target_exists": False,
                "archive_sha256": archive_binding["sha256"],
            },
            {
                "returncode": retried.returncode,
                "classification": retry["classification"],
                "session_target_exists": session_path.exists(),
                "archive_sha256": _sha256(archive_path),
            },
        )

    def test_stale_competing_handoff_is_fenced_without_overwriting_winner(
        self,
    ) -> None:
        run_dir = self._make_ready_run()
        winner, winner_envelope = _run_cli(
            "delivery-handoff",
            "--run-dir",
            str(run_dir),
            "--from-session-id",
            "session-a",
            "--to-session-id",
            "session-b",
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--handed-off-at",
            "2026-08-09T02:06:00Z",
        )
        if winner.returncode != 0:
            raise AssertionError(winner.stdout + winner.stderr)

        loser, loser_envelope = _run_cli(
            "delivery-handoff",
            "--run-dir",
            str(run_dir),
            "--from-session-id",
            "session-a",
            "--to-session-id",
            "session-c",
            "--expected-run-revision",
            "2",
            "--expected-ownership-generation",
            "1",
            "--handed-off-at",
            "2026-08-09T02:06:01Z",
        )
        self.assertNotEqual(0, loser.returncode)
        self.assertEqual(
            {
                "first_failing_gate": "lifecycle_fencing",
                "error_code": "delivery_handoff_fence_lost",
            },
            {
                "first_failing_gate": loser_envelope.get("data", {}).get(
                    "first_failing_gate"
                ),
                "error_code": loser_envelope.get("data", {}).get(
                    "error_code"
                ),
            },
        )

        project = run_dir.parents[1]
        run_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        session_b = json.loads(
            (
                project
                / ".codex"
                / "delivery-targets"
                / "sessions"
                / "session-b"
                / "current.json"
            ).read_text(encoding="utf-8")
        )
        session_c_path = (
            project
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / "session-c"
            / "current.json"
        )
        task_index = json.loads(
            (
                project
                / ".codex"
                / "delivery-targets"
                / "task-index.json"
            ).read_text(encoding="utf-8")
        )
        owners = [
            entry["session_id"]
            for entry in task_index["entries"]
            if entry["run_id"] == run_record["run_id"]
        ]

        self.assertEqual(
            {
                "run": {
                    "stage": "ready_for_delivery",
                    "run_revision": 3,
                    "session_id": "session-b",
                    "ownership_generation": 2,
                    "intent_id": winner_envelope["data"]["intent_id"],
                },
                "session_b": {
                    "owner_status": "active",
                    "ownership_generation": 2,
                },
                "session_c_exists": False,
                "task_index_owners": ["session-b"],
            },
            {
                "run": {
                    "stage": run_record["delivery"]["stage"],
                    "run_revision": run_record["coordination_revision"],
                    "session_id": run_record["delivery"]["ownership"][
                        "session_id"
                    ],
                    "ownership_generation": run_record["delivery"][
                        "ownership"
                    ]["generation"],
                    "intent_id": run_record["last_mutation_intent_id"],
                },
                "session_b": {
                    "owner_status": session_b["owner_status"],
                    "ownership_generation": session_b[
                        "ownership_generation"
                    ],
                },
                "session_c_exists": session_c_path.exists(),
                "task_index_owners": owners,
            },
        )

    def test_concurrent_runs_preserve_shared_task_index_entries(self) -> None:
        case = new_case_dir(self.id(), label="issue13-shared-task-index")
        project = case / "project"
        run_a, evidence_a = self._make_generating_run(
            project=project,
            run_name="bilibili_run_a",
            session_id="session-a",
            run_id="13131313131313131313131313131313",
        )
        run_b, evidence_b = self._make_generating_run(
            project=project,
            run_name="bilibili_run_b",
            session_id="session-b",
            run_id="24242424242424242424242424242424",
        )
        commands = [
            (run_a, evidence_a, "session-a", "2026-08-09T02:08:00Z"),
            (run_b, evidence_b, "session-b", "2026-08-09T02:08:01Z"),
        ]
        barrier = threading.Barrier(2)

        def transition(
            command: tuple[Path, Path, str, str]
        ) -> tuple[subprocess.CompletedProcess[str], dict]:
            run_dir, evidence_path, session_id, transitioned_at = command
            barrier.wait()
            return _run_cli(
                "delivery-transition",
                "--run-dir",
                str(run_dir),
                "--from-stage",
                "generating",
                "--to-stage",
                "ready_for_delivery",
                "--session-id",
                session_id,
                "--expected-run-revision",
                "1",
                "--expected-ownership-generation",
                "1",
                "--evidence",
                str(evidence_path),
                "--transitioned-at",
                transitioned_at,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent_results = list(executor.map(transition, commands))

        self.assertEqual(
            2,
            sum(result.returncode == 0 for result, _ in concurrent_results),
            concurrent_results,
        )

        task_index = json.loads(
            (
                project / ".codex" / "delivery-targets" / "task-index.json"
            ).read_text(encoding="utf-8")
        )
        entries = [
            {
                "run_id": entry["run_id"],
                "stage": entry["stage"],
                "session_id": entry["session_id"],
            }
            for entry in task_index["entries"]
        ]
        run_bindings = []
        store = ControlStore(project / "workspace", ContractRegistry(PROJECT_ROOT))
        for run_dir in (run_a, run_b):
            record = json.loads(
                (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                _sha256(run_dir / "workflow" / "run.json"),
                store.current_run_record_sha(record["run_id"]),
            )
            run_bindings.append(
                {
                    "run_id": record["run_id"],
                    "stage": record["delivery"]["stage"],
                }
            )
        self.assertEqual(
            [
                {
                    "run_id": "13131313131313131313131313131313",
                    "stage": "ready_for_delivery",
                    "session_id": "session-a",
                },
                {
                    "run_id": "24242424242424242424242424242424",
                    "stage": "ready_for_delivery",
                    "session_id": "session-b",
                },
            ],
            entries,
        )
        self.assertEqual(
            [
                {
                    "run_id": "13131313131313131313131313131313",
                    "stage": "ready_for_delivery",
                },
                {
                    "run_id": "24242424242424242424242424242424",
                    "stage": "ready_for_delivery",
                },
            ],
            run_bindings,
        )

    def test_active_bilibili_init_and_delivery_transition_share_task_index_fence(
        self,
    ) -> None:
        case = new_case_dir(self.id(), label="issue13-init-transition-task-index")
        project = case / "project"
        existing_run, transition_evidence = self._make_generating_run(
            project=project,
            run_name="existing_bilibili_run",
            session_id="session-existing",
            run_id="13131313131313131313131313131313",
        )
        transition_gate = json.loads(
            transition_evidence.read_text(encoding="utf-8")
        )["global_gate_authority"]
        current_global_gate_binding = {
            "activation_status": "active_global_gate",
            "authority_path": transition_gate["path"],
            "authority_sha256": transition_gate["sha256"],
            "generation": transition_gate["generation"],
        }
        workspace = project / "workspace"
        new_run_id = "24242424242424242424242424242424"
        new_item_id = "BV1Issue13InitTransition"
        source_identity = hashlib.sha256(
            (
                json.dumps(
                    {
                        "canonical_item_id": new_item_id,
                        "canonical_platform": "bilibili",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        bootstrap = json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "contracts"
                / "bootstrap-record.v2.valid.json"
            ).read_text(encoding="utf-8")
        )
        bootstrap.update(
            {
                "run_id": new_run_id,
                "request_id": "issue-13-init-transition",
                "task_start": "2026-08-09T09:15:00+08:00",
                "canonical_item_id": new_item_id,
                "source_identity": source_identity,
                "original_title": "Issue 13 init transition shared index",
            }
        )
        bootstrap["source_request"]["canonical_locator"] = (
            f"https://www.bilibili.com/video/{new_item_id}"
        )
        probe_path = (
            project
            / "待删除"
            / "pipeline-bootstrap"
            / new_run_id
            / "probe.json"
        )
        _write_json(probe_path, bootstrap)
        probe = ProductionBootstrapResult(
            run_id=new_run_id,
            request_id=bootstrap["request_id"],
            record_path=probe_path,
            original_title=bootstrap["original_title"],
            task_start=bootstrap["task_start"],
            canonical_platform="bilibili",
            canonical_item_id=new_item_id,
            source_identity=source_identity,
        )

        task_index_path = (
            project / ".codex" / "delivery-targets" / "task-index.json"
        ).resolve()
        lifecycle_read = threading.Event()
        initialization_started = threading.Event()
        lifecycle_read_json = delivery_lifecycle_module.read_json

        def synchronize_lifecycle_task_index_read(path: Path) -> dict:
            value = lifecycle_read_json(path)
            if Path(path).resolve() == task_index_path and not lifecycle_read.is_set():
                lifecycle_read.set()
                if not initialization_started.wait(timeout=10):
                    raise AssertionError("initialization did not contend for the shared fence")
            return value

        def initialize() -> tuple[str, object]:
            initialization_started.set()
            try:
                result = VideoWorkflowKernel(workspace).initialize_production_source(
                    probe,
                    session_id="session-new",
                    global_gate_binding=current_global_gate_binding,
                )
            except Exception as error:
                return "error", error
            return "ok", result

        def transition() -> tuple[str, object]:
            try:
                result = DeliveryLifecycleProvider(PROJECT_ROOT).transition(
                    run_dir=existing_run,
                    from_stage="generating",
                    to_stage="ready_for_delivery",
                    session_id="session-existing",
                    expected_run_revision=1,
                    expected_ownership_generation=1,
                    evidence_path=transition_evidence,
                    transitioned_at="2026-08-09T01:15:00Z",
                )
            except Exception as error:
                return "error", error
            return "ok", result

        with patch.object(
            delivery_lifecycle_module,
            "read_json",
            side_effect=synchronize_lifecycle_task_index_read,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                transition_future = executor.submit(transition)
                self.assertTrue(lifecycle_read.wait(timeout=10))
                initialize_future = executor.submit(initialize)
                results = [transition_future.result(), initialize_future.result()]

        self.assertEqual(["ok", "ok"], [status for status, _ in results], results)
        task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [
                {
                    "run_id": "13131313131313131313131313131313",
                    "stage": "ready_for_delivery",
                },
                {
                    "run_id": new_run_id,
                    "stage": "generating",
                },
            ],
            [
                {"run_id": entry["run_id"], "stage": entry["stage"]}
                for entry in task_index["entries"]
            ],
        )

    def test_shared_task_index_concurrent_writers_are_serialized_and_preserve_both_entries(
        self,
    ) -> None:
        case = new_case_dir(self.id(), label="issue13-task-index-toctou")
        project = case / "project"
        run_a, evidence_a = self._make_generating_run(
            project=project,
            run_name="bilibili_run_a",
            session_id="session-a",
            run_id="13131313131313131313131313131313",
        )
        run_b, evidence_b = self._make_generating_run(
            project=project,
            run_name="bilibili_run_b",
            session_id="session-b",
            run_id="24242424242424242424242424242424",
        )
        task_index_path = (
            project / ".codex" / "delivery-targets" / "task-index.json"
        ).resolve()
        commands = (
            (run_a, evidence_a, "session-a"),
            (run_b, evidence_b, "session-b"),
        )

        def transition(
            command: tuple[Path, Path, str]
        ) -> tuple[str, object]:
            run_dir, evidence, session_id = command
            try:
                result = DeliveryLifecycleProvider(PROJECT_ROOT).transition(
                    run_dir=run_dir,
                    from_stage="generating",
                    to_stage="ready_for_delivery",
                    session_id=session_id,
                    expected_run_revision=1,
                    expected_ownership_generation=1,
                    evidence_path=evidence,
                    transitioned_at="2026-08-09T02:10:00Z",
                )
            except Exception as error:
                return "error", error
            return "ok", result

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_round = list(executor.map(transition, commands))

        self.assertEqual(["ok", "ok"], [status for status, _ in first_round])

        task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [
                ("13131313131313131313131313131313", "ready_for_delivery"),
                ("24242424242424242424242424242424", "ready_for_delivery"),
            ],
            [(entry["run_id"], entry["stage"]) for entry in task_index["entries"]],
        )

    def test_control_store_check_rejects_committed_delivery_replacement_tamper(
        self,
    ) -> None:
        run_dir = self._make_ready_run()
        workspace = run_dir.parent
        with sqlite3.connect(
            workspace / ".workflow-control" / "control.sqlite3"
        ) as connection:
            connection.execute(
                "UPDATE delivery_lifecycle_intents "
                "SET replacement_run_record_json='{}' WHERE state='COMMITTED'"
            )

        completed, envelope = _run_cli(
            "control-store-check",
            "--workspace-root",
            str(workspace),
        )

        self.assertEqual(
            {
                "returncode": 50,
                "status": "error",
                "classification": "control_store_unavailable",
                "message": "Delivery Lifecycle replacement Run Record is invalid",
            },
            {
                "returncode": completed.returncode,
                "status": envelope["status"],
                "classification": envelope["classification"],
                "message": envelope["data"].get("message"),
            },
        )

if __name__ == "__main__":
    unittest.main()

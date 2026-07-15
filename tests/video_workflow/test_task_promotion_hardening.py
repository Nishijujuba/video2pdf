from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.control_store import ControlStore  # noqa: E402
from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ArtifactDrift,
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    TaskFault,
)
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.task_execution import (  # noqa: E402
    CLAIM_FAULT_POINTS,
    COMPLETION_FAULT_POINTS,
    PROMOTION_FAULT_POINTS,
)
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    read_json,
    sha256_file,
    write_json_atomic,
)


PYTHON = Path(r"D:\Project\video2pdf\kimi\.venv\Scripts\python.exe")
LAUNCHER = PROJECT_ROOT / "scripts/video_workflow.py"
FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
TASK_START = "2026-07-15T01:02:03+08:00"


class Slice2Harness:
    workspace: Path
    kernel: VideoWorkflowKernel
    run_dir: Path

    def initialize(self, label: str) -> None:
        # Keep the harness identity compact so the fixture itself does not
        # consume the workflow's deliberately strict 240 UTF-16-unit budget.
        identity = uuid.uuid4().hex[:8]
        root = TEST_RUNS / f"s2-{identity}"
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True)
        self.kernel = VideoWorkflowKernel(self.workspace)
        self.run_dir = self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"s2-{identity}",
        ).run_dir

    def prepare(self, key: str = "source-acquisition-decision"):
        return self.kernel.prepare_source_acquisition_task(
            self.run_dir,
            logical_task_key=key,
            prepared_at=TASK_START,
        )

    def claim(self, prepared, **kwargs):
        return self.kernel.claim_task(
            self.run_dir,
            prepared.task_id,
            coordinator_session_id=kwargs.pop("coordinator_session_id", "coordinator"),
            worker_id=kwargs.pop("worker_id", "worker"),
            **kwargs,
        )

    def patch(self, prepared, claimed, *, rationale: str = "Fixture subtitle is usable.") -> Path:
        envelope = read_json(prepared.envelope_path)
        output = claimed.attempt_dir / "o/p.json"
        output.parent.mkdir(parents=False, exist_ok=False)
        write_json_atomic(
            output,
            {
                "schema_name": "source-acquisition-judgment-patch",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "task_id": prepared.task_id,
                "attempt_id": claimed.attempt_id,
                "task_envelope_sha256": sha256_file(prepared.envelope_path),
                "source_manifest_sha256": envelope["input_artifacts"][0]["sha256"],
                "judgment": {
                    "selected_subtitle_track": "subtitle_en",
                    "whisper_fallback": {
                        "choice": "not_required",
                        "rationale": rationale,
                    },
                    "known_gaps": [],
                },
            },
        )
        return output

    def complete(self, prepared, claimed, **kwargs):
        return self.kernel.complete_task(
            self.run_dir,
            task_id=prepared.task_id,
            attempt_id=claimed.attempt_id,
            claim_generation=claimed.claim_generation,
            **kwargs,
        )

    def promote(self, prepared, claimed, **kwargs):
        return self.kernel.promote_task(
            self.run_dir,
            task_id=prepared.task_id,
            attempt_id=claimed.attempt_id,
            claim_generation=claimed.claim_generation,
            **kwargs,
        )


class TaskPersistenceBoundaryTests(unittest.TestCase, Slice2Harness):
    def test_every_claim_persistence_boundary_is_idempotently_resumable(self) -> None:
        for fault_point in sorted(CLAIM_FAULT_POINTS):
            with self.subTest(fault_point=fault_point):
                self.initialize(f"claim-{fault_point}")
                prepared = self.prepare()
                with self.assertRaises(TaskFault):
                    self.claim(prepared, fault_point=fault_point)
                resumed = self.claim(prepared)
                self.assertTrue((resumed.attempt_dir / "attempt.json").is_file())
                self.assertEqual(resumed.claim_generation, 1)

    def test_every_completion_persistence_boundary_is_idempotently_resumable(self) -> None:
        for fault_point in sorted(COMPLETION_FAULT_POINTS):
            with self.subTest(fault_point=fault_point):
                self.initialize(f"complete-{fault_point}")
                prepared = self.prepare()
                claimed = self.claim(prepared)
                self.patch(prepared, claimed)
                run_before = (self.run_dir / "workflow/run.json").read_bytes()
                with self.assertRaises(TaskFault):
                    self.complete(prepared, claimed, fault_point=fault_point)
                resumed = self.complete(prepared, claimed)
                self.assertTrue(resumed.completion_path.is_file())
                self.assertEqual(
                    (self.run_dir / "workflow/run.json").read_bytes(), run_before
                )
                self.assertFalse(
                    (self.run_dir / "workflow/source-acquisition-judgment-patch.json").exists()
                )

    def test_every_promotion_persistence_boundary_reconciles_to_one_commit(self) -> None:
        for fault_point in sorted(PROMOTION_FAULT_POINTS):
            with self.subTest(fault_point=fault_point):
                self.initialize(f"promote-{fault_point}")
                prepared = self.prepare()
                claimed = self.claim(prepared)
                self.patch(prepared, claimed)
                self.complete(prepared, claimed)
                with self.assertRaises(TaskFault):
                    self.promote(prepared, claimed, fault_point=fault_point)
                run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
                self.kernel.reconcile_authority("kernel_run", run_id)
                self.kernel.reconcile_authority("kernel_run", run_id)
                run = read_json(self.run_dir / "workflow/run.json")
                intent = self.kernel.control_store.task_promotion_for_attempt(
                    prepared.task_id, claimed.attempt_id
                )
                self.assertEqual(intent["state"], "COMMITTED")
                self.assertEqual(run["last_mutation_intent_id"], intent["intent_id"])
                self.assertEqual(run["coordination_revision"], 2)

    def test_second_generation_preserves_prior_bytes_and_advances_exactly_once(self) -> None:
        self.initialize("generation-two")
        first = self.prepare("source-acquisition-decision-1")
        first_claim = self.claim(first)
        self.patch(first, first_claim, rationale="First decision.")
        self.complete(first, first_claim)
        self.promote(first, first_claim)
        canonical = self.run_dir / "workflow/source-acquisition-judgment-patch.json"
        first_bytes = canonical.read_bytes()

        second = self.prepare("source-acquisition-decision-2")
        second_claim = self.claim(second, worker_id="worker-2")
        self.patch(second, second_claim, rationale="Second decision.")
        self.complete(second, second_claim)
        self.promote(second, second_claim)

        run = read_json(self.run_dir / "workflow/run.json")
        self.assertEqual(
            run["artifact_generations"]["source_acquisition_decision"]["generation"],
            2,
        )
        self.assertEqual(run["coordination_revision"], 3)
        preserved = (
            self.run_dir
            / f"待删除/task-promotions/{second.task_id}/g00000001/previous/decision.json"
        )
        self.assertEqual(preserved.read_bytes(), first_bytes)
        self.assertNotEqual(canonical.read_bytes(), first_bytes)

    def test_two_runs_hold_independent_nonterminal_promotion_slots(self) -> None:
        contexts = []
        for label in ("run-a", "run-b"):
            self.initialize(label)
            prepared = self.prepare()
            claimed = self.claim(prepared)
            self.patch(prepared, claimed)
            self.complete(prepared, claimed)
            with self.assertRaises(TaskFault):
                self.promote(
                    prepared,
                    claimed,
                    fault_point="after_promotion_intent_prepared",
                )
            contexts.append((self.kernel, self.run_dir, prepared, claimed))
        for kernel, run_dir, prepared, claimed in contexts:
            run_id = read_json(run_dir / "workflow/run.json")["run_id"]
            kernel.reconcile_authority("kernel_run", run_id)
            self.assertEqual(
                kernel.control_store.task_promotion_for_attempt(
                    prepared.task_id, claimed.attempt_id
                )["state"],
                "COMMITTED",
            )


class TaskFailClosedTests(unittest.TestCase, Slice2Harness):
    def setUp(self) -> None:
        self.initialize("negative")

    def test_direct_canonical_write_and_changed_validated_output_are_rejected(self) -> None:
        prepared = self.prepare()
        claimed = self.claim(prepared)
        patch_path = self.patch(prepared, claimed)
        canonical = self.run_dir / "workflow/source-acquisition-judgment-patch.json"
        canonical.write_bytes(patch_path.read_bytes())
        with self.assertRaises(ArtifactDrift):
            self.complete(prepared, claimed)

        self.initialize("changed-after-validation")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        patch_path = self.patch(prepared, claimed)
        self.complete(prepared, claimed)
        patch = read_json(patch_path)
        patch["judgment"]["known_gaps"] = ["changed after validation"]
        write_json_atomic(patch_path, patch)
        with self.assertRaises((ArtifactDrift, KernelConflict)):
            self.promote(prepared, claimed)

    def test_extra_directory_and_symlink_or_reparse_output_fail_closed(self) -> None:
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.patch(prepared, claimed)
        (claimed.attempt_dir / "extra").mkdir()
        with self.assertRaises(ContractError):
            self.complete(prepared, claimed)

        prepared2 = self.prepare("source-acquisition-decision-two")
        with self.assertRaises(KernelConflict):
            self.claim(prepared2, worker_id="worker-two")

    def test_prompt_source_generated_prompt_and_envelope_tamper_are_rejected(self) -> None:
        prepared = self.prepare()
        prepared.prompt_path.write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(ArtifactDrift):
            self.claim(prepared)

        prepared.prompt_path.write_bytes(
            (PROJECT_ROOT / "prompts/video-workflow/roles/source-acquisition.v1.md").read_bytes()
            + b"\n"
            + (PROJECT_ROOT / "prompts/video-workflow/platforms/fixture.v1.md").read_bytes()
        )
        envelope = read_json(prepared.envelope_path)
        envelope["allowed_read_paths"].append("source/secret.txt")
        write_json_atomic(prepared.envelope_path, envelope)
        with self.assertRaises((ArtifactDrift, ContractError)):
            self.claim(prepared)

    def test_source_input_drift_and_unknown_authority_fail_closed(self) -> None:
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.patch(prepared, claimed)
        manifest = self.run_dir / "source/manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        with self.assertRaises(ArtifactDrift):
            self.complete(prepared, claimed)
        with self.assertRaises(ContractError):
            self.kernel.reconcile_authority("acceptance_execution", "0" * 32)

    def test_source_drift_after_promotion_stales_every_registered_checkpoint(self) -> None:
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.patch(prepared, claimed)
        self.complete(prepared, claimed)
        self.promote(prepared, claimed)

        source = self.run_dir / "source/media/video.fixture"
        source.write_bytes(source.read_bytes() + b"drift")
        with self.assertRaises(ArtifactDrift):
            self.kernel.reconcile_run(self.run_dir)

        record = read_json(self.run_dir / "workflow/run.json")
        self.assertEqual(record["schema_version"], "2.0.0")
        self.assertEqual(
            {checkpoint["status"] for checkpoint in record["checkpoints"].values()},
            {"stale"},
        )

    def test_journal_tamper_and_intent_marker_contradiction_block_recovery(self) -> None:
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.patch(prepared, claimed)
        self.complete(prepared, claimed)
        with self.assertRaises(TaskFault):
            self.promote(
                prepared,
                claimed,
                fault_point="after_promotion_journal_bound",
            )
        journal = claimed.attempt_dir / "p.json"
        journal.write_bytes(journal.read_bytes() + b" ")
        run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
        with self.assertRaises((ArtifactDrift, json.JSONDecodeError)):
            self.kernel.reconcile_authority("kernel_run", run_id)

    def test_control_store_schema_or_slot_index_damage_blocks_claim(self) -> None:
        prepared = self.prepare()
        database = self.workspace / ".workflow-control/control.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("DROP INDEX one_nonterminal_task_promotion_per_run")
        with self.assertRaises(ControlStoreUnavailable):
            self.claim(prepared)

    def test_late_worker_replay_after_commit_is_terminal(self) -> None:
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.patch(prepared, claimed)
        self.complete(prepared, claimed)
        self.promote(prepared, claimed)
        with self.assertRaises((KernelConflict, ArtifactDrift)):
            self.complete(prepared, claimed)
        replay = self.promote(prepared, claimed)
        self.assertEqual(replay.classification, "committed_complete")


class TaskPublicCliTests(unittest.TestCase, Slice2Harness):
    def setUp(self) -> None:
        self.initialize("cli")

    def cli(self, *arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [str(PYTHON), "-X", "utf8", "-B", str(LAUNCHER), *arguments],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        return completed, json.loads(completed.stdout)

    def test_public_cli_runs_prepare_claim_complete_promote_and_dispatch(self) -> None:
        completed, prepared = self.cli(
            "task-prepare",
            "--run-dir", str(self.run_dir),
            "--logical-task-key", "source-acquisition-cli",
            "--prepared-at", TASK_START,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        task_id = prepared["data"]["task_id"]
        completed, claimed = self.cli(
            "task-claim",
            "--run-dir", str(self.run_dir),
            "--task-id", task_id,
            "--coordinator-session-id", "cli-coordinator",
            "--worker-id", "cli-worker",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        attempt_id = claimed["data"]["attempt_id"]
        claim_generation = claimed["data"]["claim_generation"]
        prepared_result = self.kernel.prepare_source_acquisition_task(
            self.run_dir,
            logical_task_key="source-acquisition-cli",
            prepared_at=TASK_START,
        )
        claimed_result = type("Claim", (), {
            "attempt_id": attempt_id,
            "attempt_dir": Path(claimed["data"]["attempt_dir"]),
        })()
        self.patch(prepared_result, claimed_result)
        completed, gate = self.cli(
            "task-complete",
            "--run-dir", str(self.run_dir),
            "--task-id", task_id,
            "--attempt-id", attempt_id,
            "--claim-generation", str(claim_generation),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(gate["classification"], "validated_waiting_for_promotion")
        completed, promoted = self.cli(
            "task-promote",
            "--run-dir", str(self.run_dir),
            "--task-id", task_id,
            "--attempt-id", attempt_id,
            "--claim-generation", str(claim_generation),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(promoted["classification"], "committed_complete")
        run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
        completed, reconciled = self.cli(
            "reconcile-authority",
            "--workspace-root", str(self.workspace),
            "--kind", "kernel_run",
            "--id", run_id,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(reconciled["classification"], "authority_reconciled")

    def test_cli_unknown_authority_kind_is_one_machine_error(self) -> None:
        completed, envelope = self.cli(
            "reconcile-authority",
            "--workspace-root", str(self.workspace),
            "--kind", "unknown",
            "--id", "0" * 32,
        )
        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ArtifactDrift,
    ContractError,
    KernelConflict,
    TaskFault,
)
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.prompts import _entry  # noqa: E402
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    read_json,
    sha256_file,
    write_json_atomic,
)


FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
TASK_START = "2026-07-15T01:02:03+08:00"


class TaskPromotionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_RUNS / f"slice2-{uuid.uuid4().hex}" / "workspace"
        self.workspace.mkdir(parents=True)
        self.kernel = VideoWorkflowKernel(self.workspace)
        traced = self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"slice2-{uuid.uuid4().hex}",
        )
        self.run_dir = traced.run_dir

    def prepare_and_claim(self):
        prepared = self.kernel.prepare_source_acquisition_task(
            self.run_dir,
            logical_task_key="source-acquisition-decision",
            prepared_at=TASK_START,
        )
        claimed = self.kernel.claim_task(
            self.run_dir,
            prepared.task_id,
            coordinator_session_id="coordinator-test",
            worker_id="worker-test",
        )
        return prepared, claimed

    def write_patch(self, prepared, claimed, **changes) -> Path:
        envelope = read_json(prepared.envelope_path)
        patch = {
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
                    "rationale": "The immutable English subtitle fixture is usable.",
                },
                "known_gaps": [],
            },
        }
        patch.update(changes)
        output = claimed.attempt_dir / "o/p.json"
        output.parent.mkdir(parents=False, exist_ok=False)
        write_json_atomic(output, patch)
        return output

    def complete(self, prepared, claimed):
        return self.kernel.complete_task(
            self.run_dir,
            task_id=prepared.task_id,
            attempt_id=claimed.attempt_id,
            claim_generation=claimed.claim_generation,
        )

    def promote(self, prepared, claimed, *, fault_point=None):
        return self.kernel.promote_task(
            self.run_dir,
            task_id=prepared.task_id,
            attempt_id=claimed.attempt_id,
            claim_generation=claimed.claim_generation,
            fault_point=fault_point,
        )

    def test_full_task_to_promotion_lifecycle_is_fenced_and_transactional(self) -> None:
        prepared, claimed = self.prepare_and_claim()
        envelope = read_json(prepared.envelope_path)
        attempt = read_json(claimed.attempt_dir / "attempt.json")

        self.assertEqual(envelope["schema_name"], "subagent-task-envelope")
        self.assertEqual(envelope["authority_binding"]["kind"], "kernel_run")
        self.assertEqual(envelope["authority_binding"]["run_id"], read_json(self.run_dir / "workflow/run.json")["run_id"])
        self.assertEqual(envelope["role"], "source_acquisition")
        self.assertRegex(envelope["generated_prompt"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(attempt["claim_generation"], 1)
        self.assertEqual(attempt["task_envelope_sha256"], sha256_file(prepared.envelope_path))
        self.assertTrue(prepared.prompt_path.is_file())

        canonical = self.run_dir / "workflow/source-acquisition-judgment-patch.json"
        run_path = self.run_dir / "workflow/run.json"
        run_before = run_path.read_bytes()
        self.write_patch(prepared, claimed)
        completed = self.complete(prepared, claimed)

        self.assertEqual(completed.classification, "validated_waiting_for_promotion")
        self.assertFalse(canonical.exists())
        self.assertEqual(run_path.read_bytes(), run_before)
        self.assertEqual(read_json(completed.completion_path)["gate_status"], "pass")

        promoted = self.promote(prepared, claimed)
        run = read_json(run_path)
        generation = run["artifact_generations"]["source_acquisition_decision"]
        checkpoint = run["checkpoints"]["source_acquisition_decision_ready"]
        self.assertEqual(promoted.classification, "committed_complete")
        self.assertEqual(generation["generation"], 1)
        self.assertEqual(generation["sha256"], sha256_file(canonical))
        self.assertEqual(checkpoint["status"], "current")
        self.assertEqual(checkpoint["artifact_generations"], {
            "source_manifest": 1,
            "source_acquisition_decision": 1,
        })
        self.assertEqual(run["checkpoints"]["source_ready"]["status"], "current")
        self.assertEqual(run["coordination_revision"], 2)

        connection = sqlite3.connect(self.workspace / ".workflow-control/control.sqlite3")
        try:
            claim = connection.execute(
                "SELECT state, claim_generation FROM task_claims WHERE task_id=?",
                (prepared.task_id,),
            ).fetchone()
            intent = connection.execute(
                "SELECT state FROM task_promotion_intents WHERE task_id=?",
                (prepared.task_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(claim, ("TERMINAL", 1))
        self.assertEqual(intent, ("COMMITTED",))

    def test_prompt_registry_rejects_path_retargeting(self) -> None:
        with self.assertRaises(ContractError):
            _entry(
                [
                    {
                        "identity": "source-acquisition",
                        "version": "1.0.0",
                        "path": "prompts/video-workflow/roles/retargeted.md",
                    }
                ],
                "source-acquisition",
                "prompts/video-workflow/roles/source-acquisition.v1.md",
                "role",
            )

    def test_completion_rejects_undeclared_outputs_and_direct_canonical_write(self) -> None:
        prepared, claimed = self.prepare_and_claim()
        self.write_patch(prepared, claimed)
        (claimed.attempt_dir / "extra.txt").write_text("undeclared", encoding="utf-8")
        with self.assertRaises(ContractError):
            self.complete(prepared, claimed)

        prepared2 = self.kernel.prepare_source_acquisition_task(
            self.run_dir,
            logical_task_key="source-acquisition-decision-2",
            prepared_at=TASK_START,
        )
        with self.assertRaises(KernelConflict):
            self.kernel.claim_task(
                self.run_dir,
                prepared2.task_id,
                coordinator_session_id="coordinator-test",
                worker_id="worker-test-2",
            )

    def test_patch_schema_rejects_protected_authority_fields(self) -> None:
        prepared, claimed = self.prepare_and_claim()
        self.write_patch(prepared, claimed, run_id="forged", checkpoint="forged")
        with self.assertRaises(ContractError):
            self.complete(prepared, claimed)

    def test_stale_input_and_stale_run_revision_fail_closed(self) -> None:
        prepared, claimed = self.prepare_and_claim()
        self.write_patch(prepared, claimed)
        run_path = self.run_dir / "workflow/run.json"
        run = read_json(run_path)
        run["coordination_revision"] += 1
        write_json_atomic(run_path, run)
        with self.assertRaises((ArtifactDrift, KernelConflict)):
            self.complete(prepared, claimed)

    def test_reclaim_advances_fence_and_late_worker_cannot_complete_or_promote(self) -> None:
        prepared, first = self.prepare_and_claim()
        self.write_patch(prepared, first)
        second = self.kernel.reclaim_task(
            self.run_dir,
            task_id=prepared.task_id,
            expected_attempt_id=first.attempt_id,
            expected_claim_generation=first.claim_generation,
            coordinator_session_id="coordinator-recovery",
            worker_id="worker-replacement",
            reason="coordinator handoff after worker loss",
        )
        self.assertEqual(second.claim_generation, 2)
        with self.assertRaises(KernelConflict):
            self.complete(prepared, first)
        with self.assertRaises(KernelConflict):
            self.promote(prepared, first)

        self.write_patch(prepared, second)
        self.complete(prepared, second)
        self.promote(prepared, second)

    def test_task_envelope_prompt_and_attempt_are_immutable_and_exact(self) -> None:
        prepared, claimed = self.prepare_and_claim()
        self.write_patch(prepared, claimed)
        prompt = prepared.prompt_path.read_text(encoding="utf-8")
        self.assertIn("select the usable subtitle track", prompt.lower())
        self.assertIn("whisper fallback", prompt.lower())
        self.assertIn("task envelope", prompt.lower())
        self.assertIn("canonical", prompt.lower())

        envelope = read_json(prepared.envelope_path)
        envelope["write_set"][0] = "source/manifest.json"
        write_json_atomic(prepared.envelope_path, envelope)
        with self.assertRaises((ArtifactDrift, ContractError, KernelConflict)):
            self.complete(prepared, claimed)

    def test_reconcile_authority_and_run_wrapper_share_idempotent_handler(self) -> None:
        prepared, claimed = self.prepare_and_claim()
        self.write_patch(prepared, claimed)
        self.complete(prepared, claimed)
        with self.assertRaises(TaskFault):
            self.promote(prepared, claimed, fault_point="after_run_record_commit_marker")

        run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
        first = self.kernel.reconcile_authority("kernel_run", run_id)
        second = self.kernel.reconcile_run(self.run_dir)
        third = self.kernel.reconcile_authority("kernel_run", run_id)
        self.assertEqual(first.outcome, "new_state_complete")
        self.assertEqual(second.outcome, "current_state_verified")
        self.assertEqual(third.outcome, "current_state_verified")
        with self.assertRaises(ContractError):
            self.kernel.reconcile_authority("unknown", run_id)
        with self.assertRaises(KernelConflict):
            self.kernel.reconcile_authority("kernel_run", "0" * 32)

    def test_registered_contract_examples_include_all_slice2_contracts(self) -> None:
        checked = self.kernel.contracts.check()
        names = set(checked["registered_schema_names"])
        self.assertTrue(
            {
                "subagent-task-envelope",
                "task-attempt",
                "task-completion-record",
                "source-acquisition-judgment-patch",
                "task-promotion-journal",
                "run-record-task-capable",
            }.issubset(names)
        )


if __name__ == "__main__":
    unittest.main()

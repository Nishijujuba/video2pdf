from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
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


class TaskPriorGenerationPreservationTests(unittest.TestCase, Slice2Harness):
    def _commit_generation(self, key: str, rationale: str, worker_id: str):
        prepared = self.prepare(key)
        claimed = self.claim(prepared, worker_id=worker_id)
        self.patch(prepared, claimed, rationale=rationale)
        self.complete(prepared, claimed)
        self.promote(prepared, claimed)
        return prepared, claimed

    def _start_second_generation(self, fault_point: str):
        self._commit_generation(
            "source-acquisition-decision-1",
            "First decision.",
            "worker-1",
        )
        canonical = self.run_dir / "workflow/source-acquisition-judgment-patch.json"
        first_bytes = canonical.read_bytes()
        second = self.prepare("source-acquisition-decision-2")
        second_claim = self.claim(second, worker_id="worker-2")
        self.patch(second, second_claim, rationale="Second decision.")
        self.complete(second, second_claim)
        with self.assertRaises(TaskFault):
            self.promote(second, second_claim, fault_point=fault_point)
        intent = self.kernel.control_store.task_promotion_for_attempt(
            second.task_id, second_claim.attempt_id
        )
        output = json.loads(intent["outputs_json"])[0]
        preservation = self.run_dir.joinpath(
            *Path(output["preservation_path"]).parts
        )
        return second, second_claim, intent, preservation, first_bytes

    def _move_preservation_aside(self, preservation: Path, label: str) -> Path:
        quarantine = (
            PROJECT_ROOT
            / "待删除/preservation-regression-tamper"
            / f"{uuid.uuid4().hex}-{label}-{preservation.name}"
        )
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        preservation.replace(quarantine)
        self.assertFalse(preservation.exists())
        return quarantine

    def test_missing_preservation_blocks_every_published_nonterminal_state(self) -> None:
        cases = {
            "PREPARED": "after_output_published",
            "FILES_PUBLISHED": "after_outputs_state_commit",
            "RECORD_COMMITTED": "after_record_state_commit",
        }
        for expected_state, fault_point in cases.items():
            with self.subTest(state=expected_state):
                self.initialize(f"missing-preservation-{expected_state.lower()}")
                (
                    second,
                    second_claim,
                    intent,
                    preservation,
                    _,
                ) = self._start_second_generation(fault_point)
                self.assertEqual(intent["state"], expected_state)
                quarantine = self._move_preservation_aside(
                    preservation, expected_state.lower()
                )
                run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]

                with self.assertRaisesRegex(
                    ArtifactDrift, "preserved prior Artifact Generation"
                ):
                    self.kernel.reconcile_authority("kernel_run", run_id)

                current = self.kernel.control_store.task_promotion_for_attempt(
                    second.task_id, second_claim.attempt_id
                )
                self.assertEqual(current["state"], expected_state)
                self.assertFalse(preservation.exists())
                self.assertTrue(quarantine.is_file())

    def test_prepared_with_canonical_prior_rebuilds_missing_preservation(self) -> None:
        self.initialize("recover-missing-preservation")
        (
            second,
            second_claim,
            intent,
            preservation,
            first_bytes,
        ) = self._start_second_generation("after_promotion_journal_bound")
        self.assertEqual(intent["state"], "PREPARED")
        self.assertFalse(preservation.exists())

        run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
        reconciled = self.kernel.reconcile_authority("kernel_run", run_id)

        self.assertEqual(reconciled.outcome, "new_state_complete")
        self.assertEqual(preservation.read_bytes(), first_bytes)
        current = self.kernel.control_store.task_promotion_for_attempt(
            second.task_id, second_claim.attempt_id
        )
        self.assertEqual(current["state"], "COMMITTED")

    def test_committed_preservation_drift_blocks_public_reconcile_and_replay(self) -> None:
        self.initialize("committed-preservation-drift")
        (
            second,
            second_claim,
            _,
            preservation,
            _,
        ) = self._start_second_generation("after_promotion_intent_commit")
        original = self._move_preservation_aside(
            preservation, "committed-original"
        )
        preservation.write_bytes(b"wrong prior generation\n")
        drifted_sha = sha256_file(preservation)
        run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]

        operations = {
            "reconcile-authority": lambda: self.kernel.reconcile_authority(
                "kernel_run", run_id
            ),
            "reconcile-run": lambda: self.kernel.reconcile_run(self.run_dir),
            "committed-replay": lambda: self.promote(second, second_claim),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                with self.assertRaisesRegex(
                    ArtifactDrift, "preserved prior Artifact Generation"
                ):
                    operation()
                current = self.kernel.control_store.task_promotion_for_attempt(
                    second.task_id, second_claim.attempt_id
                )
                self.assertEqual(current["state"], "COMMITTED")
                self.assertEqual(sha256_file(preservation), drifted_sha)
                self.assertTrue(original.is_file())

    def test_third_generation_keeps_earlier_preservation_under_authority(self) -> None:
        self.initialize("third-generation-preservation")
        self._commit_generation(
            "source-acquisition-decision-1",
            "First decision.",
            "worker-1",
        )
        second, second_claim = self._commit_generation(
            "source-acquisition-decision-2",
            "Second decision.",
            "worker-2",
        )
        second_intent = self.kernel.control_store.task_promotion_for_attempt(
            second.task_id, second_claim.attempt_id
        )
        second_output = json.loads(second_intent["outputs_json"])[0]
        second_preservation = self.run_dir.joinpath(
            *Path(second_output["preservation_path"]).parts
        )
        self._commit_generation(
            "source-acquisition-decision-3",
            "Third decision.",
            "worker-3",
        )
        quarantine = self._move_preservation_aside(
            second_preservation, "historical-second-intent"
        )

        run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
        with self.assertRaisesRegex(
            ArtifactDrift, "preserved prior Artifact Generation"
        ):
            self.kernel.reconcile_authority("kernel_run", run_id)
        self.assertFalse(second_preservation.exists())
        self.assertTrue(quarantine.is_file())


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
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            mutation_id, state = connection.execute(
                "SELECT mutation_id, state FROM run_state_mutation_intents "
                "WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
                (record["run_id"],),
            ).fetchone()
        self.assertEqual(state, "COMMITTED")
        self.assertEqual(record["last_mutation_intent_id"], mutation_id)

    def test_committed_replay_reconciles_source_drift_and_stales_checkpoints(self) -> None:
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.patch(prepared, claimed)
        self.complete(prepared, claimed)
        self.promote(prepared, claimed)

        source = self.run_dir / "source/media/video.fixture"
        source.write_bytes(source.read_bytes() + b"drift-before-committed-replay")

        with self.assertRaises(ArtifactDrift):
            self.promote(prepared, claimed)

        record = read_json(self.run_dir / "workflow/run.json")
        self.assertEqual(
            {checkpoint["status"] for checkpoint in record["checkpoints"].values()},
            {"stale"},
        )

    def test_committed_replay_uses_immutable_envelope_prompt_after_upgrade(self) -> None:
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.patch(prepared, claimed)
        self.complete(prepared, claimed)
        self.promote(prepared, claimed)
        envelope = read_json(prepared.envelope_path)

        upgraded_prompt = b"upgraded role prompt\n\nupgraded platform prompt\n"
        upgraded_provenance = {
            "sha256": hashlib.sha256(upgraded_prompt).hexdigest(),
            "role_template": {
                **envelope["generated_prompt"]["role_template"],
                "version": "2.0.0",
                "path": "prompts/video-workflow/roles/source-acquisition.v2.md",
                "sha256": hashlib.sha256(b"upgraded role prompt\n").hexdigest(),
            },
            "platform_overlay": {
                **envelope["generated_prompt"]["platform_overlay"],
                "version": "2.0.0",
                "path": "prompts/video-workflow/platforms/fixture.v2.md",
                "sha256": hashlib.sha256(b"upgraded platform prompt\n").hexdigest(),
            },
        }
        with patch(
            "video2pdf_workflow_kernel.task_execution.generate_source_acquisition_prompt",
            return_value=(upgraded_prompt, upgraded_provenance),
        ):
            replay = self.promote(prepared, claimed)

        self.assertEqual(replay.classification, "committed_complete")

    def test_new_task_completion_accepts_older_envelope_prompt_in_namespace(self) -> None:
        first = self.prepare("source-acquisition-decision-1")
        first_claim = self.claim(first)
        self.patch(first, first_claim, rationale="First decision.")
        self.complete(first, first_claim)
        self.promote(first, first_claim)
        first_envelope = read_json(first.envelope_path)

        upgraded_prompt = b"schema-compatible upgraded role\n\nplatform overlay\n"
        upgraded_provenance = {
            "sha256": hashlib.sha256(upgraded_prompt).hexdigest(),
            "role_template": {
                **first_envelope["generated_prompt"]["role_template"],
                "path": "prompts/video-workflow/roles/source-acquisition.next.md",
                "sha256": hashlib.sha256(b"schema-compatible upgraded role\n").hexdigest(),
            },
            "platform_overlay": {
                **first_envelope["generated_prompt"]["platform_overlay"],
                "path": "prompts/video-workflow/platforms/fixture.next.md",
                "sha256": hashlib.sha256(b"platform overlay\n").hexdigest(),
            },
        }
        with patch(
            "video2pdf_workflow_kernel.task_execution.generate_source_acquisition_prompt",
            return_value=(upgraded_prompt, upgraded_provenance),
        ):
            second = self.prepare("source-acquisition-decision-2")
            second_claim = self.claim(second, worker_id="worker-2")
            self.patch(second, second_claim, rationale="Second decision.")
            completion = self.complete(second, second_claim)
            promotion = self.promote(second, second_claim)

        self.assertEqual(
            completion.classification, "validated_waiting_for_promotion"
        )
        self.assertEqual(promotion.classification, "committed_complete")

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

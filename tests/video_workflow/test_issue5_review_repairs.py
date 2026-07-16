from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
import uuid
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.contracts import ContractRegistry  # noqa: E402
from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ArtifactDrift,
    ContractError,
    ControlStoreUnavailable,
    KernelConflict,
    TaskFault,
    UnknownContractVersion,
)
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.task_execution import TaskExecution  # noqa: E402
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    canonical_json_bytes,
    read_json,
    sha256_file,
    write_json_atomic,
)


FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
TASK_START = "2026-07-15T01:02:03+08:00"
PATCH_CANONICAL = "workflow/source-acquisition-judgment-patch.json"


class Issue5RepairHarness:
    def initialize(self, label: str) -> None:
        identity = uuid.uuid4().hex[:8]
        root = TEST_RUNS / f"r5-{identity}"
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=False)
        self.kernel = VideoWorkflowKernel(self.workspace)
        self.run_dir = self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"r5-{label}-{identity}",
        ).run_dir

    def prepare(self, key: str = "source-acquisition-decision"):
        return self.kernel.prepare_source_acquisition_task(
            self.run_dir,
            logical_task_key=key,
            prepared_at=TASK_START,
        )

    def claim(self, prepared):
        return self.kernel.claim_task(
            self.run_dir,
            prepared.task_id,
            coordinator_session_id="repair-coordinator",
            worker_id="repair-worker",
        )

    def claim_disjoint(self, prepared):
        envelope = read_json(prepared.envelope_path)
        run = read_json(self.run_dir / "workflow/run.json")
        attempt_id = hashlib.sha256(
            f"task-attempt\0{prepared.task_id}\0{1}".encode("utf-8")
        ).hexdigest()[:24]
        attempt_path = (
            f"workflow/tasks/{prepared.task_id}/attempts/{attempt_id}"
        )
        claim = self.kernel.control_store.claim_task(
            authority_id=run["run_id"],
            task_id=prepared.task_id,
            envelope_sha256=sha256_file(prepared.envelope_path),
            write_set=(f"workflow/disjoint-{prepared.task_id}.json",),
            attempt_path=attempt_path,
            coordinator_session_id="disjoint-coordinator",
            worker_id="disjoint-worker",
            claimed_at="2026-07-15T04:00:00+00:00",
        )
        TaskExecution(self.kernel)._create_attempt_record(
            self.run_dir,
            envelope,
            claim,
            fault_point=None,
            after_write_fault_point="unused",
        )
        return claim, self.run_dir / attempt_path

    @staticmethod
    def leave_atomic_output_temp(attempt_dir: Path) -> Path:
        output = attempt_dir / "o/p.json"
        output.parent.mkdir(parents=False, exist_ok=False)
        with mock.patch(
            "video2pdf_workflow_kernel.utils.os.replace",
            side_effect=OSError("simulated worker crash before atomic replace"),
        ):
            try:
                write_json_atomic(output, {"partial": "durable temporary bytes"})
            except OSError:
                pass
            else:
                raise AssertionError("atomic write crash simulation did not fail")
        return attempt_dir / "o/.p.json.kernel-new"

    def write_patch(self, prepared, claimed) -> Path:
        envelope = read_json(prepared.envelope_path)
        path = claimed.attempt_dir / "o/p.json"
        path.parent.mkdir(parents=False, exist_ok=False)
        write_json_atomic(
            path,
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
                        "rationale": "The immutable English subtitle fixture is usable.",
                    },
                    "known_gaps": [],
                },
            },
        )
        return path

    def complete(self, prepared, claimed):
        return self.kernel.complete_task(
            self.run_dir,
            task_id=prepared.task_id,
            attempt_id=claimed.attempt_id,
            claim_generation=claimed.claim_generation,
        )

    def promote(self, prepared, claimed, **kwargs):
        return self.kernel.promote_task(
            self.run_dir,
            task_id=prepared.task_id,
            attempt_id=claimed.attempt_id,
            claim_generation=claimed.claim_generation,
            **kwargs,
        )

    def ready(self, label: str):
        self.initialize(label)
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        self.complete(prepared, claimed)
        return prepared, claimed

    def prepare_source_mutation(self):
        record_path = self.run_dir / "workflow/run.json"
        record = read_json(record_path)
        replacement = copy.deepcopy(record)
        replacement["coordination_revision"] += 1
        for checkpoint in replacement["checkpoints"].values():
            checkpoint["status"] = "stale"
        return self.kernel.control_store.prepare_run_state_mutation(
            run_id=record["run_id"],
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=sha256_file(record_path),
            replacement_run_record=replacement,
        )

    def commit_source_mutation(self):
        record_path = self.run_dir / "workflow/run.json"
        record = read_json(record_path)
        replacement = copy.deepcopy(record)
        replacement["coordination_revision"] += 1
        for checkpoint in replacement["checkpoints"].values():
            checkpoint["status"] = "stale"
        mutation_id = self.kernel.control_store.derive_run_state_mutation_id(
            run_id=record["run_id"],
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=sha256_file(record_path),
        )
        if replacement.get("schema_version") == "2.0.0":
            replacement["last_mutation_intent_id"] = mutation_id
        mutation = self.kernel.control_store.prepare_run_state_mutation(
            run_id=record["run_id"],
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=sha256_file(record_path),
            replacement_run_record=replacement,
        )
        self.assertEqual(
            write_json_atomic(record_path, replacement),
            mutation["replacement_run_record_sha256"],
        )
        self.kernel.control_store.commit_run_state_mutation(
            mutation["mutation_id"]
        )
        return mutation

    def downgrade_source_mutation_to_real_legacy_v4(self, mutation) -> str:
        legacy_identity = hashlib.sha256(
            "\0".join(
                (
                    mutation["operation"],
                    mutation["run_id"],
                    str(mutation["expected_run_revision"]),
                    mutation["old_run_record_sha256"],
                    mutation["predecessor_committed_sha256"],
                    mutation["replacement_run_record_sha256"],
                )
            ).encode("utf-8")
        ).hexdigest()
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "DROP TABLE IF EXISTS run_state_mutation_identity_versions"
            )
            connection.execute("DROP TABLE task_reclaim_transitions")
            connection.execute("DROP TABLE task_promotion_identity_versions")
            connection.execute("DROP TABLE task_completion_authorities")
            connection.execute("DROP TABLE task_attempt_authorities")
            connection.execute(
                "UPDATE run_state_mutation_intents SET mutation_id=?, "
                "mutation_identity=? WHERE mutation_id=?",
                (legacy_identity, legacy_identity, mutation["mutation_id"]),
            )
            connection.execute(
                "DELETE FROM schema_migrations WHERE version IN (5, 6)"
            )
        return legacy_identity

    def downgrade_promotion_to_real_legacy_v4(
        self, prepared, claimed
    ) -> str:
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.row_factory = sqlite3.Row
            intent = connection.execute(
                "SELECT * FROM task_promotion_intents "
                "WHERE task_id=? AND attempt_id=?",
                (prepared.task_id, claimed.attempt_id),
            ).fetchone()
        outputs = json.loads(intent["outputs_json"])
        legacy_id = hashlib.sha256(
            "\0".join(
                (
                    "task_artifact_promotion",
                    intent["run_id"],
                    intent["task_id"],
                    intent["attempt_id"],
                    str(intent["claim_generation"]),
                    str(intent["expected_run_revision"]),
                    intent["old_run_record_sha256"],
                    outputs[0]["sha256"],
                )
            ).encode("utf-8")
        ).hexdigest()
        replacement = json.loads(intent["replacement_run_record_json"])
        replacement["last_mutation_intent_id"] = legacy_id
        replacement_json = canonical_json_bytes(replacement).decode("utf-8")
        replacement_sha = hashlib.sha256(
            replacement_json.encode("utf-8")
        ).hexdigest()
        legacy_identity = hashlib.sha256(
            "\0".join(
                (
                    "task_artifact_promotion",
                    intent["run_id"],
                    intent["task_id"],
                    intent["attempt_id"],
                    str(intent["claim_generation"]),
                    str(intent["expected_run_revision"]),
                    intent["old_run_record_sha256"],
                    replacement_sha,
                    hashlib.sha256(
                        intent["outputs_json"].encode("utf-8")
                    ).hexdigest(),
                )
            ).encode("utf-8")
        ).hexdigest()
        journal_path = claimed.attempt_dir / "p.json"
        journal_sha = intent["journal_sha256"]
        if journal_path.is_file():
            journal = read_json(journal_path)
            journal["intent_id"] = legacy_id
            journal["replacement_run_record_sha256"] = replacement_sha
            journal_sha = write_json_atomic(journal_path, journal)
        run_path = self.run_dir / "workflow/run.json"
        if sha256_file(run_path) == intent["replacement_run_record_sha256"]:
            self.assertEqual(write_json_atomic(run_path, replacement), replacement_sha)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("DROP TABLE task_reclaim_transitions")
            connection.execute(
                "DROP TABLE run_state_mutation_identity_versions"
            )
            connection.execute("DROP TABLE task_promotion_identity_versions")
            connection.execute("DROP TABLE task_completion_authorities")
            connection.execute("DROP TABLE task_attempt_authorities")
            connection.execute(
                "UPDATE task_promotion_intents SET intent_id=?, "
                "replacement_run_record_sha256=?, replacement_run_record_json=?, "
                "journal_sha256=?, intent_identity=? WHERE intent_id=?",
                (
                    legacy_id,
                    replacement_sha,
                    replacement_json,
                    journal_sha,
                    legacy_identity,
                    intent["intent_id"],
                ),
            )
            connection.execute(
                "DELETE FROM schema_migrations WHERE version IN (5, 6)"
            )
        return legacy_id


class PromotionAuthorityRepairTests(unittest.TestCase, Issue5RepairHarness):
    def test_source_drift_intent_replay_rejects_changed_replacement(self) -> None:
        prepared, claimed = self.ready("source-drift-replay")
        self.promote(prepared, claimed)
        record_path = self.run_dir / "workflow/run.json"
        record = read_json(record_path)
        old_sha = sha256_file(record_path)
        replacement = copy.deepcopy(record)
        replacement["coordination_revision"] += 1
        for checkpoint in replacement["checkpoints"].values():
            checkpoint["status"] = "stale"
        replacement["last_mutation_intent_id"] = (
            self.kernel.control_store.derive_run_state_mutation_id(
                run_id=record["run_id"],
                expected_run_revision=record["coordination_revision"],
                old_run_record_sha256=old_sha,
            )
        )
        self.kernel.control_store.prepare_run_state_mutation(
            run_id=record["run_id"],
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=old_sha,
            replacement_run_record=replacement,
        )
        conflicting = copy.deepcopy(replacement)
        first_checkpoint = next(iter(conflicting["checkpoints"].values()))
        first_checkpoint["status"] = "current"
        with self.assertRaises(KernelConflict):
            self.kernel.control_store.prepare_run_state_mutation(
                run_id=record["run_id"],
                expected_run_revision=record["coordination_revision"],
                old_run_record_sha256=old_sha,
                replacement_run_record=conflicting,
            )

    def test_recovery_reauthenticates_intent_envelope_completion_and_staging(self) -> None:
        for tamper in (
            "intent_outputs",
            "intent_outputs_and_identity",
            "envelope",
            "completion",
            "attempt",
            "staging",
        ):
            with self.subTest(tamper=tamper):
                prepared, claimed = self.ready(f"auth-{tamper}")
                with self.assertRaises(TaskFault):
                    self.promote(
                        prepared,
                        claimed,
                        fault_point="after_promotion_intent_prepared",
                    )
                plan = self.run_dir / "workflow/artifact-plan.json"
                plan_before = plan.read_bytes()
                if tamper in {"intent_outputs", "intent_outputs_and_identity"}:
                    intent = self.kernel.control_store.task_promotion_for_attempt(
                        prepared.task_id, claimed.attempt_id
                    )
                    outputs = read_json_string(intent["outputs_json"])
                    outputs[0]["canonical_path"] = "workflow/artifact-plan.json"
                    outputs[0]["prior_sha256"] = sha256_file(plan)
                    outputs_json = canonical_json_bytes(outputs).decode("utf-8")
                    replacement_identity = intent["intent_identity"]
                    if tamper == "intent_outputs_and_identity":
                        claim = self.kernel.control_store.task_claim_for_attempt(
                            prepared.task_id, claimed.attempt_id
                        )
                        derived_id = self.kernel.control_store.derive_task_promotion_intent_id(
                            run_id=intent["run_id"],
                            task_id=intent["task_id"],
                            attempt_id=intent["attempt_id"],
                            claim_generation=int(intent["claim_generation"]),
                            expected_run_revision=int(intent["expected_run_revision"]),
                            old_run_record_sha256=intent["old_run_record_sha256"],
                            envelope_sha256=claim["envelope_sha256"],
                            completion_sha256=claim["completion_sha256"],
                            outputs_json=outputs_json,
                        )
                        replacement_identity = hashlib.sha256(
                            "\0".join(
                                (
                                    "task_promotion_intent_row_v2",
                                    derived_id,
                                    intent["replacement_run_record_sha256"],
                                    hashlib.sha256(outputs_json.encode("utf-8")).hexdigest(),
                                    claim["envelope_sha256"],
                                    claim["completion_sha256"],
                                )
                            ).encode("utf-8")
                        ).hexdigest()
                    with sqlite3.connect(self.kernel.control_store.path) as connection:
                        connection.execute(
                            "UPDATE task_promotion_intents SET outputs_json=?, "
                            "intent_identity=? WHERE intent_id=?",
                            (
                                outputs_json,
                                replacement_identity,
                                intent["intent_id"],
                            ),
                        )
                elif tamper == "envelope":
                    envelope = read_json(prepared.envelope_path)
                    envelope["allowed_read_paths"].append("source/metadata/platform.json")
                    write_json_atomic(prepared.envelope_path, envelope)
                elif tamper == "completion":
                    completion = read_json(claimed.attempt_dir / "completion.json")
                    completion["validated_at"] = "2026-07-15T02:02:03+08:00"
                    write_json_atomic(claimed.attempt_dir / "completion.json", completion)
                elif tamper == "attempt":
                    attempt = read_json(claimed.attempt_dir / "attempt.json")
                    attempt["worker_id"] = "tampered-recovery-worker"
                    write_json_atomic(claimed.attempt_dir / "attempt.json", attempt)
                else:
                    patch = read_json(claimed.attempt_dir / "o/p.json")
                    patch["judgment"]["known_gaps"] = ["tampered after validation"]
                    write_json_atomic(claimed.attempt_dir / "o/p.json", patch)

                run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
                with self.assertRaises(
                    (ArtifactDrift, ContractError, ControlStoreUnavailable, KernelConflict)
                ):
                    self.kernel.reconcile_authority("kernel_run", run_id)
                with sqlite3.connect(self.kernel.control_store.path) as connection:
                    state = connection.execute(
                        "SELECT state FROM task_promotion_intents "
                        "WHERE task_id=? AND attempt_id=?",
                        (prepared.task_id, claimed.attempt_id),
                    ).fetchone()[0]
                self.assertNotEqual(state, "COMMITTED")
                self.assertEqual(plan.read_bytes(), plan_before)
                self.assertFalse((self.run_dir / PATCH_CANONICAL).exists())

    def test_run_promotion_slot_is_symmetric_for_both_prepare_orderings(self) -> None:
        prepared, claimed = self.ready("task-first")
        with self.assertRaises(TaskFault):
            self.promote(
                prepared,
                claimed,
                fault_point="after_promotion_intent_prepared",
            )
        self.downgrade_promotion_to_real_legacy_v4(prepared, claimed)
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                4,
            )
        with self.assertRaises(KernelConflict):
            self.prepare_source_mutation()

        prepared, claimed = self.ready("source-first")
        self.prepare_source_mutation()
        with self.assertRaises(KernelConflict):
            self.promote(prepared, claimed)

    def test_post_slot_completion_gate_closes_source_drift_toctou(self) -> None:
        prepared, claimed = self.ready("toctou")
        run_path = self.run_dir / "workflow/run.json"
        run_before = run_path.read_bytes()
        original = self.kernel.control_store.prepare_task_promotion

        def prepare_then_drift(**kwargs):
            intent = original(**kwargs)
            source = self.run_dir / "source/media/video.fixture"
            source.write_bytes(source.read_bytes() + b"post-slot-drift")
            return intent

        with mock.patch.object(
            self.kernel.control_store,
            "prepare_task_promotion",
            side_effect=prepare_then_drift,
        ):
            with self.assertRaises(ArtifactDrift):
                self.promote(prepared, claimed)

        intent = self.kernel.control_store.task_promotion_for_attempt(
            prepared.task_id, claimed.attempt_id
        )
        self.assertEqual(intent["state"], "ABORTED")
        self.assertEqual(run_path.read_bytes(), run_before)
        self.assertFalse((self.run_dir / PATCH_CANONICAL).exists())
        self.assertEqual(
            read_json(run_path)["checkpoints"]["source_ready"]["status"],
            "current",
        )

    def test_committed_replay_validates_run_path_authority(self) -> None:
        prepared, claimed = self.ready("replay-a")
        self.promote(prepared, claimed)
        run_a = self.run_dir
        run_b = self.kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start="2026-07-15T01:02:04+08:00",
            request_id=f"r5-replay-b-{uuid.uuid4().hex[:8]}",
        ).run_dir
        with self.assertRaises((ArtifactDrift, KernelConflict)):
            self.kernel.promote_task(
                run_b,
                task_id=prepared.task_id,
                attempt_id=claimed.attempt_id,
                claim_generation=claimed.claim_generation,
            )
        self.assertTrue((run_a / PATCH_CANONICAL).is_file())
        self.assertFalse((run_b / PATCH_CANONICAL).exists())

    def test_every_nonterminal_recovery_revalidates_source_freshness(self) -> None:
        cases = {
            "after_outputs_state_commit": "FILES_PUBLISHED",
            "after_run_record_commit_marker": "FILES_PUBLISHED",
            "after_record_state_commit": "RECORD_COMMITTED",
        }
        for fault_point, expected_state in cases.items():
            with self.subTest(fault_point=fault_point):
                prepared, claimed = self.ready(f"recovery-source-{fault_point}")
                with self.assertRaises(TaskFault):
                    self.promote(prepared, claimed, fault_point=fault_point)
                source = self.run_dir / "source/media/video.fixture"
                source.write_bytes(source.read_bytes() + b"recovery-source-drift")
                run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
                with self.assertRaises(ArtifactDrift):
                    self.kernel.reconcile_authority("kernel_run", run_id)
                with sqlite3.connect(self.kernel.control_store.path) as connection:
                    state = connection.execute(
                        "SELECT state FROM task_promotion_intents "
                        "WHERE task_id=? AND attempt_id=?",
                        (prepared.task_id, claimed.attempt_id),
                    ).fetchone()[0]
                self.assertEqual(state, expected_state)
                self.assertNotEqual(state, "COMMITTED")

    def test_every_nonterminal_recovery_rejects_cross_task_corruption(self) -> None:
        cases = {
            "after_promotion_intent_prepared": "ABORTED",
            "after_outputs_state_commit": "FILES_PUBLISHED",
            "after_record_state_commit": "RECORD_COMMITTED",
        }
        for fault_point, expected_state in cases.items():
            with self.subTest(fault_point=fault_point):
                prepared, claimed = self.ready(
                    f"recovery-cross-task-{fault_point}"
                )
                other = self.prepare(f"other-{fault_point.replace('_', '-')}")
                _, other_attempt = self.claim_disjoint(other)
                with self.assertRaises(TaskFault):
                    self.promote(prepared, claimed, fault_point=fault_point)

                (other_attempt / "cross-task-corruption.txt").write_text(
                    "undeclared cross-task write during promotion recovery",
                    encoding="utf-8",
                )

                run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
                with self.assertRaises(ContractError):
                    self.kernel.reconcile_authority("kernel_run", run_id)
                intent = self.kernel.control_store.task_promotion_for_attempt(
                    prepared.task_id, claimed.attempt_id
                )
                self.assertEqual(intent["state"], expected_state)
                self.assertNotEqual(intent["state"], "COMMITTED")

    def test_files_published_recovery_accepts_old_or_replacement_marker(self) -> None:
        for fault_point in (
            "after_outputs_state_commit",
            "after_run_record_commit_marker",
        ):
            with self.subTest(fault_point=fault_point):
                prepared, claimed = self.ready(f"marker-matrix-{fault_point}")
                with self.assertRaises(TaskFault):
                    self.promote(prepared, claimed, fault_point=fault_point)
                run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
                self.kernel.reconcile_authority("kernel_run", run_id)
                intent = self.kernel.control_store.task_promotion_for_attempt(
                    prepared.task_id, claimed.attempt_id
                )
                self.assertEqual(intent["state"], "COMMITTED")

    def test_disjoint_atomic_output_temp_does_not_block_promotion(self) -> None:
        for state in ("CLAIMED", "ABANDONED", "FAILED"):
            with self.subTest(state=state):
                prepared, claimed = self.ready(f"atomic-temp-{state.lower()}")
                other = self.prepare(f"atomic-temp-other-{state.lower()}")
                other_claim, other_attempt = self.claim_disjoint(other)
                temporary = self.leave_atomic_output_temp(other_attempt)
                self.assertTrue(temporary.is_file())
                if state == "ABANDONED":
                    self.kernel.reclaim_task(
                        self.run_dir,
                        task_id=other.task_id,
                        expected_attempt_id=str(other_claim["attempt_id"]),
                        expected_claim_generation=int(
                            other_claim["claim_generation"]
                        ),
                        coordinator_session_id="replacement-coordinator",
                        worker_id="replacement-worker",
                        reason="recover crashed atomic output write",
                    )
                elif state == "FAILED":
                    with sqlite3.connect(
                        self.kernel.control_store.path
                    ) as connection:
                        connection.execute(
                            "UPDATE task_attempts SET state='FAILED' "
                            "WHERE attempt_id=?",
                            (other_claim["attempt_id"],),
                        )

                self.promote(prepared, claimed)
                intent = self.kernel.control_store.task_promotion_for_attempt(
                    prepared.task_id, claimed.attempt_id
                )
                self.assertEqual(intent["state"], "COMMITTED")


class CompletionBoundaryRepairTests(unittest.TestCase, Issue5RepairHarness):
    def test_completion_rejects_run_wide_undeclared_worker_file(self) -> None:
        self.initialize("outside-write")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        (self.run_dir / "review/undeclared-worker-output.txt").write_text(
            "undeclared", encoding="utf-8"
        )
        with self.assertRaises(ContractError):
            self.complete(prepared, claimed)

    def test_claimed_worker_cannot_prewrite_completion_record(self) -> None:
        self.initialize("prewritten-completion")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        patch_path = self.write_patch(prepared, claimed)
        envelope = read_json(prepared.envelope_path)
        run = read_json(self.run_dir / "workflow/run.json")
        source = run["artifact_generations"]["source_manifest"]
        write_json_atomic(
            claimed.attempt_dir / "completion.json",
            {
                "schema_name": "task-completion-record",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "task_id": prepared.task_id,
                "attempt_id": claimed.attempt_id,
                "claim_generation": claimed.claim_generation,
                "task_envelope_sha256": sha256_file(prepared.envelope_path),
                "validated_authority_revision": run["coordination_revision"],
                "validated_run_record_sha256": sha256_file(
                    self.run_dir / "workflow/run.json"
                ),
                "validated_inputs": [
                    {
                        "logical_id": "source_manifest",
                        "generation": source["generation"],
                        "sha256": source["sha256"],
                    }
                ],
                "outputs": [
                    {
                        "logical_id": "source_acquisition_decision",
                        "attempt_path": "o/p.json",
                        "canonical_path": PATCH_CANONICAL,
                        "sha256": sha256_file(patch_path),
                    }
                ],
                "gate_status": "pass",
                "validated_at": envelope["prepared_at"],
            },
        )
        with self.assertRaises((ArtifactDrift, ContractError)):
            self.complete(prepared, claimed)

    def test_completion_rejects_noncanonical_current_attempt_record(self) -> None:
        self.initialize("completion-current-attempt-noncanonical")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        record_path = claimed.attempt_dir / "attempt.json"
        record_path.write_bytes(record_path.read_bytes() + b" ")

        with self.assertRaises(ArtifactDrift):
            self.complete(prepared, claimed)

    def test_reclaimed_attempt_identity_remains_immutable_authority(self) -> None:
        tampered_values = {
            "coordinator_session_id": "tampered-coordinator",
            "worker_id": "tampered-worker",
            "claimed_at": "2026-07-15T23:59:59+00:00",
        }
        for field, value in tampered_values.items():
            with self.subTest(field=field):
                self.initialize(f"reclaimed-attempt-{field.replace('_', '-')}")
                prepared = self.prepare()
                first = self.claim(prepared)
                replacement = self.kernel.reclaim_task(
                    self.run_dir,
                    task_id=prepared.task_id,
                    expected_attempt_id=first.attempt_id,
                    expected_claim_generation=first.claim_generation,
                    coordinator_session_id="replacement-coordinator",
                    worker_id="replacement-worker",
                    reason="replace abandoned worker",
                )
                self.write_patch(prepared, replacement)
                record_path = first.attempt_dir / "attempt.json"
                record = read_json(record_path)
                record[field] = value
                write_json_atomic(record_path, record)
                with self.assertRaises(ArtifactDrift):
                    self.complete(prepared, replacement)

    def test_validation_and_commit_timestamps_are_runtime_events(self) -> None:
        prepared, claimed = self.ready("event-times")
        completion = read_json(claimed.attempt_dir / "completion.json")
        self.assertNotEqual(completion["validated_at"], TASK_START)
        self.promote(prepared, claimed)
        run = read_json(self.run_dir / "workflow/run.json")
        self.assertNotEqual(
            run["artifact_generations"]["source_acquisition_decision"]["committed_at"],
            TASK_START,
        )

    def test_completion_retry_preserves_first_event_and_promotion_uses_later_event(self) -> None:
        self.initialize("trusted-event-order")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        first = "2026-07-15T03:00:00+00:00"
        retry = "2026-07-15T03:01:00+00:00"
        promoted = "2026-07-15T03:02:00+00:00"
        with mock.patch(
            "video2pdf_workflow_kernel.task_execution._utc_now",
            side_effect=[first, retry, promoted],
        ):
            with self.assertRaises(TaskFault):
                self.kernel.complete_task(
                    self.run_dir,
                    task_id=prepared.task_id,
                    attempt_id=claimed.attempt_id,
                    claim_generation=claimed.claim_generation,
                    fault_point="after_completion_prepared",
                )
            self.complete(prepared, claimed)
            self.promote(prepared, claimed)
        completion = read_json(claimed.attempt_dir / "completion.json")
        run = read_json(self.run_dir / "workflow/run.json")
        self.assertEqual(completion["validated_at"], first)
        self.assertEqual(
            run["artifact_generations"]["source_acquisition_decision"]["committed_at"],
            promoted,
        )

    def test_completion_retry_rejects_noncanonical_completion_bytes(self) -> None:
        self.initialize("completion-retry-noncanonical-bytes")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        with self.assertRaises(TaskFault):
            self.kernel.complete_task(
                self.run_dir,
                task_id=prepared.task_id,
                attempt_id=claimed.attempt_id,
                claim_generation=claimed.claim_generation,
                fault_point="after_completion_record_written",
            )
        completion_path = claimed.attempt_dir / "completion.json"
        completion_path.write_bytes(completion_path.read_bytes() + b" ")

        with self.assertRaises((ArtifactDrift, KernelConflict)):
            self.complete(prepared, claimed)

        durable = self.kernel.control_store.task_claim_for_attempt(
            prepared.task_id, claimed.attempt_id
        )
        self.assertEqual(durable["attempt_state"], "CLAIMED")

    def test_completion_retry_accepts_unchanged_canonical_completion_bytes(self) -> None:
        self.initialize("completion-retry-canonical-bytes")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        with self.assertRaises(TaskFault):
            self.kernel.complete_task(
                self.run_dir,
                task_id=prepared.task_id,
                attempt_id=claimed.attempt_id,
                claim_generation=claimed.claim_generation,
                fault_point="after_completion_record_written",
            )
        original_bytes = (claimed.attempt_dir / "completion.json").read_bytes()

        resumed = self.complete(prepared, claimed)
        replayed = self.complete(prepared, claimed)

        self.assertEqual(resumed, replayed)
        self.assertEqual(
            (claimed.attempt_dir / "completion.json").read_bytes(), original_bytes
        )
        durable = self.kernel.control_store.task_claim_for_attempt(
            prepared.task_id, claimed.attempt_id
        )
        self.assertEqual(
            durable["attempt_state"], "VALIDATED_WAITING_FOR_PROMOTION"
        )

    def test_shared_empty_directories_and_task_namespace_forgery_fail_closed(self) -> None:
        mutations = (
            "shared-empty",
            "task-root-file",
            "fake-attempt",
            "fake-task-root",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.initialize(mutation)
                prepared = self.prepare()
                claimed = self.claim(prepared)
                self.write_patch(prepared, claimed)
                if mutation == "shared-empty":
                    (self.run_dir / "review/worker-empty-dir").mkdir()
                elif mutation == "task-root-file":
                    (prepared.task_dir / "evil.txt").write_text(
                        "undeclared", encoding="utf-8"
                    )
                elif mutation == "fake-attempt":
                    (prepared.task_dir / "attempts/fake-attempt").mkdir()
                else:
                    fake = self.run_dir / f"workflow/tasks/{'f' * 32}"
                    fake.mkdir()
                    (fake / "evil.txt").write_text("undeclared", encoding="utf-8")
                with self.assertRaises(ContractError):
                    self.complete(prepared, claimed)

    def test_shared_empty_directory_removal_fails_closed(self) -> None:
        self.initialize("shared-empty-removal")
        baseline = self.run_dir / "review/baseline-empty-dir"
        baseline.mkdir()
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        archive = self.run_dir / "待删除/removed-baseline-empty-dir"
        baseline.replace(archive)
        with self.assertRaises(ContractError):
            self.complete(prepared, claimed)

    def test_other_durable_task_root_and_attempt_do_not_break_completion(self) -> None:
        self.initialize("disjoint-task-namespace")
        prepared = self.prepare("first-task")
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        other = self.prepare("second-task")
        envelope = read_json(other.envelope_path)
        run = read_json(self.run_dir / "workflow/run.json")
        other_attempt_id = hashlib.sha256(
            f"task-attempt\0{other.task_id}\0{1}".encode("utf-8")
        ).hexdigest()[:24]
        claim = self.kernel.control_store.claim_task(
            authority_id=run["run_id"],
            task_id=other.task_id,
            envelope_sha256=sha256_file(other.envelope_path),
            write_set=("workflow/disjoint-output.json",),
            attempt_path=(
                f"workflow/tasks/{other.task_id}/attempts/{other_attempt_id}"
            ),
            coordinator_session_id="disjoint-coordinator",
            worker_id="disjoint-worker",
            claimed_at="2026-07-15T04:00:00+00:00",
        )
        TaskExecution(self.kernel)._create_attempt_record(
            self.run_dir,
            envelope,
            claim,
            fault_point=None,
            after_write_fault_point="unused",
        )
        self.complete(prepared, claimed)

    def test_other_durable_task_attempt_rejects_undeclared_content(self) -> None:
        self.initialize("cross-task-attempt-corruption")
        prepared = self.prepare("first-task")
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        other = self.prepare("second-task")
        envelope = read_json(other.envelope_path)
        run = read_json(self.run_dir / "workflow/run.json")
        other_attempt_id = hashlib.sha256(
            f"task-attempt\0{other.task_id}\0{1}".encode("utf-8")
        ).hexdigest()[:24]
        claim = self.kernel.control_store.claim_task(
            authority_id=run["run_id"],
            task_id=other.task_id,
            envelope_sha256=sha256_file(other.envelope_path),
            write_set=("workflow/disjoint-output.json",),
            attempt_path=(
                f"workflow/tasks/{other.task_id}/attempts/{other_attempt_id}"
            ),
            coordinator_session_id="disjoint-coordinator",
            worker_id="disjoint-worker",
            claimed_at="2026-07-15T04:00:00+00:00",
        )
        TaskExecution(self.kernel)._create_attempt_record(
            self.run_dir,
            envelope,
            claim,
            fault_point=None,
            after_write_fault_point="unused",
        )
        other_attempt = (
            self.run_dir
            / f"workflow/tasks/{other.task_id}/attempts/{other_attempt_id}"
        )
        (other_attempt / "worker-corruption.txt").write_text(
            "undeclared cross-task write", encoding="utf-8"
        )
        with self.assertRaises(ContractError):
            self.complete(prepared, claimed)

    def test_prior_committed_attempt_rejects_late_tampering(self) -> None:
        first, first_claim = self.ready("prior-attempt-corruption")
        self.promote(first, first_claim)
        second = self.prepare("second-generation-task")
        second_claim = self.claim(second)
        self.write_patch(second, second_claim)
        (first_claim.attempt_dir / "late-worker-corruption.txt").write_text(
            "late undeclared write", encoding="utf-8"
        )
        with self.assertRaises(ContractError):
            self.complete(second, second_claim)


class ControlStoreRepairTests(unittest.TestCase, Issue5RepairHarness):
    def test_mark_task_validated_rejects_compare_and_set_sha_mismatch(self) -> None:
        self.initialize("completion-validation-cas")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        self.write_patch(prepared, claimed)
        with self.assertRaises(TaskFault):
            self.kernel.complete_task(
                self.run_dir,
                task_id=prepared.task_id,
                attempt_id=claimed.attempt_id,
                claim_generation=claimed.claim_generation,
                fault_point="after_completion_prepared",
            )

        with self.assertRaises(KernelConflict):
            self.kernel.control_store.mark_task_validated(
                task_id=prepared.task_id,
                attempt_id=claimed.attempt_id,
                claim_generation=claimed.claim_generation,
                completion_sha256="0" * 64,
            )

        durable = self.kernel.control_store.task_claim_for_attempt(
            prepared.task_id, claimed.attempt_id
        )
        self.assertEqual(durable["attempt_state"], "CLAIMED")

    def test_v5_health_rejects_weakened_index_and_table_sql(self) -> None:
        damages = {
            "nonunique-index": (
                "DROP INDEX one_nonterminal_task_promotion_per_run",
                "CREATE INDEX one_nonterminal_task_promotion_per_run "
                "ON task_promotion_intents(run_id) "
                "WHERE state IN ('PREPARED','FILES_PUBLISHED','RECORD_COMMITTED')",
            ),
            "weakened-predicate": (
                "DROP INDEX one_nonterminal_task_promotion_per_run",
                "CREATE UNIQUE INDEX one_nonterminal_task_promotion_per_run "
                "ON task_promotion_intents(run_id) WHERE state='PREPARED'",
            ),
        }
        for label, statements in damages.items():
            with self.subTest(label=label):
                self.initialize(label)
                with sqlite3.connect(self.kernel.control_store.path) as connection:
                    for statement in statements:
                        connection.execute(statement)
                with self.assertRaises(ControlStoreUnavailable):
                    self.kernel.control_store.check()

        self.initialize("weak-table")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("DROP TABLE task_promotion_intents")
            connection.execute(
                "CREATE TABLE task_promotion_intents ("
                "intent_id TEXT, run_id TEXT, task_id TEXT, attempt_id TEXT, "
                "claim_generation INTEGER, expected_run_revision INTEGER, "
                "old_run_record_sha256 TEXT, replacement_run_record_sha256 TEXT, "
                "replacement_run_record_json TEXT, outputs_json TEXT, "
                "journal_sha256 TEXT, state TEXT, intent_identity TEXT)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX one_nonterminal_task_promotion_per_run "
                "ON task_promotion_intents(run_id) "
                "WHERE state IN ('PREPARED','FILES_PUBLISHED','RECORD_COMMITTED')"
            )
        with self.assertRaises(ControlStoreUnavailable):
            self.kernel.control_store.check()

        self.initialize("weak-attempt-authority")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("DROP TABLE task_attempt_authorities")
            connection.execute(
                "CREATE TABLE task_attempt_authorities ("
                "attempt_id TEXT, attempt_record_json TEXT, "
                "attempt_record_sha256 TEXT)"
            )
        with self.assertRaises(ControlStoreUnavailable):
            self.kernel.control_store.check()

    def test_claim_fault_recovers_from_durable_attempt_record_authority(self) -> None:
        self.initialize("claim-attempt-authority")
        prepared = self.prepare()
        with self.assertRaises(TaskFault):
            self.kernel.claim_task(
                self.run_dir,
                prepared.task_id,
                coordinator_session_id="repair-coordinator",
                worker_id="repair-worker",
                fault_point="after_claim_committed",
            )
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            authority = connection.execute(
                "SELECT aa.attempt_record_json, aa.attempt_record_sha256 "
                "FROM task_attempt_authorities aa JOIN task_attempts a "
                "ON a.attempt_id=aa.attempt_id WHERE a.task_id=?",
                (prepared.task_id,),
            ).fetchone()
        self.assertIsNotNone(authority)
        resumed = self.claim(prepared)
        record_path = resumed.attempt_dir / "attempt.json"
        self.assertEqual(record_path.read_text(encoding="utf-8"), authority[0])
        self.assertEqual(sha256_file(record_path), authority[1])

    def test_claim_retry_rejects_noncanonical_attempt_record_bytes(self) -> None:
        self.initialize("claim-attempt-noncanonical")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        record_path = claimed.attempt_dir / "attempt.json"
        record_path.write_bytes(record_path.read_bytes() + b" ")
        with self.assertRaises(ArtifactDrift):
            self.claim(prepared)

    def test_partial_v5_migration_fails_closed(self) -> None:
        self.initialize("partial-v5")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("DELETE FROM schema_migrations WHERE version=5")
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)

    def test_source_mutation_rows_reject_consistent_replacement_tamper(
        self,
    ) -> None:
        for state in ("PREPARED", "COMMITTED"):
            with self.subTest(state=state):
                self.initialize(f"{state.lower()}-source-mutation-tamper")
                mutation = (
                    self.prepare_source_mutation()
                    if state == "PREPARED"
                    else self.commit_source_mutation()
                )
                replacement = json.loads(
                    mutation["replacement_run_record_json"]
                )
                replacement["normalized_title"] = (
                    "internally_consistent_tamper"
                )
                replacement_json = canonical_json_bytes(replacement).decode(
                    "utf-8"
                )
                replacement_sha = hashlib.sha256(
                    replacement_json.encode("utf-8")
                ).hexdigest()
                with sqlite3.connect(
                    self.kernel.control_store.path
                ) as connection:
                    connection.execute(
                        "UPDATE run_state_mutation_intents SET "
                        "replacement_run_record_json=?, "
                        "replacement_run_record_sha256=? WHERE mutation_id=?",
                        (
                            replacement_json,
                            replacement_sha,
                            mutation["mutation_id"],
                        ),
                    )

                checks = {
                    "health": self.kernel.control_store.check,
                    "reconciliation": lambda: self.kernel.reconcile_authority(
                        "kernel_run", mutation["run_id"]
                    ),
                }
                if state == "COMMITTED":
                    checks["hash-chain"] = (
                        lambda: self.kernel.control_store.current_run_record_sha(
                            mutation["run_id"]
                        )
                    )
                for label, check in checks.items():
                    with self.subTest(state=state, entrypoint=label):
                        with self.assertRaises(ControlStoreUnavailable):
                            check()

    def test_v5_source_mutation_identity_table_and_rows_are_mandatory(
        self,
    ) -> None:
        self.initialize("missing-source-mutation-identity-table")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute(
                "DROP TABLE IF EXISTS run_state_mutation_identity_versions"
            )
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)

        self.initialize("partial-source-mutation-identity-table")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute(
                "DROP TABLE run_state_mutation_identity_versions"
            )
            connection.execute(
                "CREATE TABLE run_state_mutation_identity_versions ("
                "mutation_id TEXT PRIMARY KEY REFERENCES "
                "run_state_mutation_intents(mutation_id), "
                "identity_version TEXT NOT NULL CHECK(identity_version IN "
                "('legacy-v1','evidence-v2')))"
            )
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)

        self.initialize("missing-source-mutation-identity-row")
        mutation = self.commit_source_mutation()
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS "
                "run_state_mutation_identity_versions ("
                "mutation_id TEXT PRIMARY KEY REFERENCES "
                "run_state_mutation_intents(mutation_id), "
                "identity_version TEXT NOT NULL CHECK(identity_version IN "
                "('legacy-v1','evidence-v2')), "
                "row_identity TEXT NOT NULL UNIQUE)"
            )
            connection.execute(
                "DELETE FROM run_state_mutation_identity_versions "
                "WHERE mutation_id=?",
                (mutation["mutation_id"],),
            )
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)

    def test_real_v4_source_mutation_identity_migrates_or_fails_closed(
        self,
    ) -> None:
        self.initialize("legacy-source-mutation-positive")
        mutation = self.commit_source_mutation()
        legacy_id = self.downgrade_source_mutation_to_real_legacy_v4(mutation)
        migrated = VideoWorkflowKernel(self.workspace)
        self.assertEqual(migrated.control_store.check().schema_version, 6)
        with sqlite3.connect(migrated.control_store.path) as connection:
            version_row = connection.execute(
                "SELECT identity_version, row_identity FROM "
                "run_state_mutation_identity_versions WHERE mutation_id=?",
                (legacy_id,),
            ).fetchone()
        self.assertEqual(version_row, ("legacy-v1", legacy_id))

        self.initialize("legacy-source-mutation-negative")
        mutation = self.commit_source_mutation()
        legacy_id = self.downgrade_source_mutation_to_real_legacy_v4(mutation)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            row = connection.execute(
                "SELECT replacement_run_record_json FROM "
                "run_state_mutation_intents WHERE mutation_id=?",
                (legacy_id,),
            ).fetchone()
            replacement = json.loads(row[0])
            replacement["normalized_title"] = "legacy_tamper"
            replacement_json = canonical_json_bytes(replacement).decode("utf-8")
            replacement_sha = hashlib.sha256(
                replacement_json.encode("utf-8")
            ).hexdigest()
            connection.execute(
                "UPDATE run_state_mutation_intents SET "
                "replacement_run_record_json=?, "
                "replacement_run_record_sha256=? WHERE mutation_id=?",
                (replacement_json, replacement_sha, legacy_id),
            )
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND "
                    "name='run_state_mutation_identity_versions'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                4,
            )

    def test_real_v3_and_v4_stores_migrate_atomically_to_v6(self) -> None:
        self.initialize("v3-upgrade")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DROP TABLE task_reclaim_transitions")
            connection.execute(
                "DROP TABLE run_state_mutation_identity_versions"
            )
            connection.execute("DROP TABLE task_promotion_identity_versions")
            connection.execute("DROP TABLE task_completion_authorities")
            connection.execute("DROP TABLE task_attempt_authorities")
            connection.execute("DROP TABLE task_promotion_intents")
            connection.execute("DROP TABLE task_attempts")
            connection.execute("DROP TABLE task_claims")
            connection.execute(
                "DELETE FROM schema_migrations WHERE version IN (4, 5, 6)"
            )
        migrated_v3 = VideoWorkflowKernel(self.workspace)
        self.assertEqual(migrated_v3.control_store.check().schema_version, 6)
        with sqlite3.connect(migrated_v3.control_store.path) as connection:
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            attempt_authority_count = connection.execute(
                "SELECT COUNT(*) FROM task_attempt_authorities"
            ).fetchone()[0]
        self.assertEqual(versions, [1, 2, 3, 4, 5, 6])
        self.assertEqual(attempt_authority_count, 0)

        prepared, claimed = self.ready("v4-committed-upgrade")
        self.promote(prepared, claimed)
        legacy_id = self.downgrade_promotion_to_real_legacy_v4(
            prepared, claimed
        )
        migrated_v4 = VideoWorkflowKernel(self.workspace)
        self.assertEqual(migrated_v4.control_store.check().schema_version, 6)
        with mock.patch(
            "video2pdf_workflow_kernel.task_execution.generate_source_acquisition_prompt",
            return_value=(b"future prompt version\n", {}),
        ):
            migrated_v4.promote_task(
                self.run_dir,
                task_id=prepared.task_id,
                attempt_id=claimed.attempt_id,
                claim_generation=claimed.claim_generation,
            )
        TaskExecution(migrated_v4).verify_committed_task_state(self.run_dir)
        with sqlite3.connect(migrated_v4.control_store.path) as connection:
            version = connection.execute(
                "SELECT identity_version FROM task_promotion_identity_versions "
                "WHERE intent_id=?",
                (legacy_id,),
            ).fetchone()[0]
            authority = connection.execute(
                "SELECT attempt_record_json, attempt_record_sha256 "
                "FROM task_attempt_authorities WHERE attempt_id=?",
                (claimed.attempt_id,),
            ).fetchone()
        self.assertEqual(version, "legacy-v1")
        self.assertEqual(
            authority[0],
            (claimed.attempt_dir / "attempt.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            authority[1], sha256_file(claimed.attempt_dir / "attempt.json")
        )

    def test_v4_completion_backfill_tamper_rolls_back(self) -> None:
        prepared, claimed = self.ready("v4-backfill-tamper")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("DROP TABLE task_reclaim_transitions")
            connection.execute(
                "DROP TABLE run_state_mutation_identity_versions"
            )
            connection.execute("DROP TABLE task_promotion_identity_versions")
            connection.execute("DROP TABLE task_completion_authorities")
            connection.execute("DROP TABLE task_attempt_authorities")
            connection.execute(
                "DELETE FROM schema_migrations WHERE version IN (5, 6)"
            )
        completion = read_json(claimed.attempt_dir / "completion.json")
        completion["validated_at"] = "2026-07-15T09:00:00+00:00"
        write_json_atomic(claimed.attempt_dir / "completion.json", completion)
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='task_completion_authorities'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                4,
            )

    def test_v4_attempt_authority_backfill_tamper_rolls_back(self) -> None:
        self.initialize("v4-attempt-backfill-tamper")
        prepared = self.prepare()
        claimed = self.claim(prepared)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("DROP TABLE task_reclaim_transitions")
            connection.execute(
                "DROP TABLE run_state_mutation_identity_versions"
            )
            connection.execute("DROP TABLE task_promotion_identity_versions")
            connection.execute("DROP TABLE task_completion_authorities")
            connection.execute("DROP TABLE task_attempt_authorities")
            connection.execute(
                "DELETE FROM schema_migrations WHERE version IN (5, 6)"
            )
        attempt = read_json(claimed.attempt_dir / "attempt.json")
        attempt["worker_id"] = "tampered-before-v5-backfill"
        write_json_atomic(claimed.attempt_dir / "attempt.json", attempt)
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='task_attempt_authorities'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                4,
            )

    def test_v4_historical_attempt_identity_cannot_be_backfilled_from_disk(
        self,
    ) -> None:
        tampered_values = {
            "coordinator_session_id": "forged-historical-coordinator",
            "worker_id": "forged-historical-worker",
            "claimed_at": "2026-07-15T23:59:59+00:00",
        }
        for field, value in tampered_values.items():
            with self.subTest(field=field):
                self.initialize(f"v4-historical-attempt-{field}")
                prepared = self.prepare()
                first = self.claim(prepared)
                self.kernel.reclaim_task(
                    self.run_dir,
                    task_id=prepared.task_id,
                    expected_attempt_id=first.attempt_id,
                    expected_claim_generation=first.claim_generation,
                    coordinator_session_id="replacement-coordinator",
                    worker_id="replacement-worker",
                    reason="replace first generation before migration",
                )
                with sqlite3.connect(
                    self.kernel.control_store.path
                ) as connection:
                    connection.execute("DROP TABLE task_reclaim_transitions")
                    connection.execute(
                        "DROP TABLE run_state_mutation_identity_versions"
                    )
                    connection.execute(
                        "DROP TABLE task_promotion_identity_versions"
                    )
                    connection.execute("DROP TABLE task_completion_authorities")
                    connection.execute("DROP TABLE task_attempt_authorities")
                    connection.execute(
                        "DELETE FROM schema_migrations WHERE version IN (5, 6)"
                    )
                record_path = first.attempt_dir / "attempt.json"
                record = read_json(record_path)
                record[field] = value
                write_json_atomic(record_path, record)

                with self.assertRaises(ControlStoreUnavailable):
                    VideoWorkflowKernel(self.workspace)
                with sqlite3.connect(
                    self.kernel.control_store.path
                ) as connection:
                    self.assertIsNone(
                        connection.execute(
                            "SELECT name FROM sqlite_master WHERE "
                            "type='table' AND "
                            "name='task_attempt_authorities'"
                        ).fetchone()
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT MAX(version) FROM schema_migrations"
                        ).fetchone()[0],
                        4,
                    )

    def test_legacy_v4_nonterminal_promotion_is_blocked(self) -> None:
        prepared, claimed = self.ready("legacy-v4-prepared")
        with self.assertRaises(TaskFault):
            self.promote(
                prepared,
                claimed,
                fault_point="after_promotion_intent_prepared",
            )

    def test_legacy_v4_outputs_identity_tamper_rolls_back(self) -> None:
        prepared, claimed = self.ready("legacy-v4-output-tamper")
        self.promote(prepared, claimed)
        legacy_id = self.downgrade_promotion_to_real_legacy_v4(
            prepared, claimed
        )
        alias = self.run_dir / "workflow/migration-alias.json"
        alias.write_bytes((self.run_dir / PATCH_CANONICAL).read_bytes())
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.row_factory = sqlite3.Row
            intent = connection.execute(
                "SELECT * FROM task_promotion_intents WHERE intent_id=?",
                (legacy_id,),
            ).fetchone()
            outputs = json.loads(intent["outputs_json"])
            outputs[0]["canonical_path"] = "workflow/migration-alias.json"
            outputs_json = canonical_json_bytes(outputs).decode("utf-8")
            identity = hashlib.sha256(
                "\0".join(
                    (
                        "task_artifact_promotion",
                        intent["run_id"],
                        intent["task_id"],
                        intent["attempt_id"],
                        str(intent["claim_generation"]),
                        str(intent["expected_run_revision"]),
                        intent["old_run_record_sha256"],
                        intent["replacement_run_record_sha256"],
                        hashlib.sha256(outputs_json.encode("utf-8")).hexdigest(),
                    )
                ).encode("utf-8")
            ).hexdigest()
            connection.execute(
                "UPDATE task_promotion_intents SET outputs_json=?, "
                "intent_identity=? WHERE intent_id=?",
                (outputs_json, identity, legacy_id),
            )
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE "
                    "name='task_completion_authorities'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                4,
            )
    def test_evidence_v2_identity_marker_cannot_be_downgraded(self) -> None:
        prepared, claimed = self.ready("identity-marker-downgrade")
        with self.assertRaises(TaskFault):
            self.promote(
                prepared,
                claimed,
                fault_point="after_promotion_intent_prepared",
            )
        intent = self.kernel.control_store.task_promotion_for_attempt(
            prepared.task_id, claimed.attempt_id
        )
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute(
                "UPDATE task_promotion_identity_versions "
                "SET identity_version='legacy-v1' WHERE intent_id=?",
                (intent["intent_id"],),
            )
        with self.assertRaises(ControlStoreUnavailable):
            self.kernel.control_store.task_promotion_for_attempt(
                prepared.task_id, claimed.attempt_id
            )

    def test_reclaim_faults_resume_same_replacement_and_conflicts_reject(self) -> None:
        for fault_point in (
            "after_reclaim_committed",
            "after_reclaim_attempt_record_written",
        ):
            with self.subTest(fault_point=fault_point):
                self.initialize(fault_point)
                prepared = self.prepare()
                first = self.claim(prepared)
                arguments = {
                    "task_id": prepared.task_id,
                    "expected_attempt_id": first.attempt_id,
                    "expected_claim_generation": first.claim_generation,
                    "coordinator_session_id": "replacement-coordinator",
                    "worker_id": "replacement-worker",
                    "reason": "deterministic worker replacement",
                }
                with self.assertRaises(TaskFault):
                    self.kernel.reclaim_task(
                        self.run_dir,
                        **arguments,
                        fault_point=fault_point,
                    )
                resumed = self.kernel.reclaim_task(self.run_dir, **arguments)
                self.assertEqual(resumed.claim_generation, 2)
                self.assertTrue((resumed.attempt_dir / "attempt.json").is_file())
                with self.assertRaises(KernelConflict):
                    self.kernel.reclaim_task(
                        self.run_dir,
                        **{**arguments, "worker_id": "conflicting-worker"},
                    )


class TaskReclaimHistoryAuthorityTests(unittest.TestCase, Issue5RepairHarness):
    def reclaim(self, prepared, prior, *, suffix: str, reason: str):
        return self.kernel.reclaim_task(
            self.run_dir,
            task_id=prepared.task_id,
            expected_attempt_id=prior.attempt_id,
            expected_claim_generation=prior.claim_generation,
            coordinator_session_id=f"replacement-coordinator-{suffix}",
            worker_id=f"replacement-worker-{suffix}",
            reason=reason,
        )

    def downgrade_reclaim_history_to_v5(self) -> None:
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("DROP TABLE task_reclaim_transitions")
            connection.execute("DELETE FROM schema_migrations WHERE version=6")

    def test_multiple_reclaims_preserve_complete_ordered_audit_history(self) -> None:
        self.initialize("reclaim-history-two-transitions")
        prepared = self.prepare()
        first = self.claim(prepared)
        second = self.reclaim(
            prepared,
            first,
            suffix="two",
            reason="first coordinator disappeared",
        )
        third = self.reclaim(
            prepared,
            second,
            suffix="three",
            reason="second worker returned invalid evidence",
        )

        history = self.kernel.control_store.task_reclaim_history(prepared.task_id)
        self.assertEqual(
            [entry["recovery_reason"] for entry in history],
            [
                "first coordinator disappeared",
                "second worker returned invalid evidence",
            ],
        )
        self.assertEqual(
            [
                (
                    entry["prior_attempt_id"],
                    entry["replacement_attempt_id"],
                    entry["prior_claim_generation"],
                    entry["replacement_claim_generation"],
                )
                for entry in history
            ],
            [
                (first.attempt_id, second.attempt_id, 1, 2),
                (second.attempt_id, third.attempt_id, 2, 3),
            ],
        )
        self.assertEqual(
            [
                (
                    entry["prior_coordinator_session_id"],
                    entry["prior_worker_id"],
                    entry["replacement_coordinator_session_id"],
                    entry["replacement_worker_id"],
                )
                for entry in history
            ],
            [
                (
                    "repair-coordinator",
                    "repair-worker",
                    "replacement-coordinator-two",
                    "replacement-worker-two",
                ),
                (
                    "replacement-coordinator-two",
                    "replacement-worker-two",
                    "replacement-coordinator-three",
                    "replacement-worker-three",
                ),
            ],
        )
        projection = self.kernel.control_store.task_claim_for_task(prepared.task_id)
        self.assertEqual(projection["attempt_id"], third.attempt_id)
        self.assertEqual(projection["reclaim_reason"], history[-1]["recovery_reason"])
        self.assertEqual(self.kernel.control_store.check().schema_version, 6)

        reopened = VideoWorkflowKernel(self.workspace)
        self.assertEqual(
            reopened.control_store.task_reclaim_history(prepared.task_id),
            history,
        )

    def test_initial_claim_and_reclaim_retry_have_exact_history_coverage(self) -> None:
        self.initialize("reclaim-history-retry")
        prepared = self.prepare()
        first = self.claim(prepared)
        self.assertEqual(
            self.kernel.control_store.task_reclaim_history(prepared.task_id), []
        )
        arguments = {
            "task_id": prepared.task_id,
            "expected_attempt_id": first.attempt_id,
            "expected_claim_generation": first.claim_generation,
            "coordinator_session_id": "retry-coordinator",
            "worker_id": "retry-worker",
            "reason": "retryable deterministic reclaim",
        }
        replacement = self.kernel.reclaim_task(self.run_dir, **arguments)
        replay = self.kernel.reclaim_task(self.run_dir, **arguments)
        self.assertEqual(replay.attempt_id, replacement.attempt_id)
        history = self.kernel.control_store.task_reclaim_history(prepared.task_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["replacement_attempt_id"], replacement.attempt_id)
        with self.assertRaises(KernelConflict):
            self.kernel.reclaim_task(
                self.run_dir,
                **{**arguments, "reason": "conflicting replay reason"},
            )
        self.assertEqual(
            self.kernel.control_store.task_reclaim_history(prepared.task_id),
            history,
        )

    def test_reclaim_projection_and_history_append_share_one_transaction(self) -> None:
        self.initialize("reclaim-history-atomic")
        prepared = self.prepare()
        first = self.claim(prepared)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_reclaim_history BEFORE INSERT ON "
                "task_reclaim_transitions BEGIN SELECT RAISE(ABORT, "
                "'injected reclaim history failure'); END"
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.reclaim(
                prepared,
                first,
                suffix="failed",
                reason="must roll back projection too",
            )
        projection = self.kernel.control_store.task_claim_for_task(prepared.task_id)
        attempts = self.kernel.control_store.task_attempts_for_task(prepared.task_id)
        self.assertEqual(projection["attempt_id"], first.attempt_id)
        self.assertEqual(projection["claim_generation"], 1)
        self.assertIsNone(projection["reclaim_reason"])
        self.assertEqual([row["attempt_id"] for row in attempts], [first.attempt_id])
        self.assertEqual(
            self.kernel.control_store.task_reclaim_history(prepared.task_id), []
        )

    def test_history_duplicate_missing_extra_tamper_reorder_and_binding_drift_fail_closed(
        self,
    ) -> None:
        cases = ("missing", "extra", "tamper", "reorder", "binding")
        for case in cases:
            with self.subTest(case=case):
                self.initialize(f"reclaim-history-{case}")
                prepared = self.prepare()
                first = self.claim(prepared)
                second = self.reclaim(
                    prepared,
                    first,
                    suffix="two",
                    reason="first immutable reason",
                )
                third = self.reclaim(
                    prepared,
                    second,
                    suffix="three",
                    reason="second immutable reason",
                )
                other = (
                    self.prepare("other-task-after-history-corruption")
                    if case == "missing"
                    else None
                )
                with sqlite3.connect(self.kernel.control_store.path) as connection:
                    connection.row_factory = sqlite3.Row
                    if case == "missing":
                        connection.execute(
                            "DELETE FROM task_reclaim_transitions "
                            "WHERE task_id=? AND replacement_claim_generation=2",
                            (prepared.task_id,),
                        )
                    elif case == "extra":
                        connection.execute("PRAGMA foreign_keys=OFF")
                        source = connection.execute(
                            "SELECT * FROM task_reclaim_transitions "
                            "WHERE task_id=? ORDER BY replacement_claim_generation LIMIT 1",
                            (prepared.task_id,),
                        ).fetchone()
                        connection.execute(
                            "INSERT INTO task_reclaim_transitions("
                            "transition_id, authority_id, task_id, prior_attempt_id, "
                            "replacement_attempt_id, prior_claim_generation, "
                            "replacement_claim_generation, recovery_reason, "
                            "prior_coordinator_session_id, prior_worker_id, "
                            "replacement_coordinator_session_id, replacement_worker_id, "
                            "reclaimed_at, transition_record_json) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                "f" * 64,
                                source["authority_id"],
                                "orphan-task",
                                "e" * 24,
                                "d" * 24,
                                1,
                                2,
                                source["recovery_reason"],
                                source["prior_coordinator_session_id"],
                                source["prior_worker_id"],
                                source["replacement_coordinator_session_id"],
                                source["replacement_worker_id"],
                                source["reclaimed_at"],
                                source["transition_record_json"],
                            ),
                        )
                    elif case == "tamper":
                        connection.execute(
                            "UPDATE task_reclaim_transitions SET recovery_reason=? "
                            "WHERE task_id=? AND replacement_claim_generation=2",
                            ("tampered reason", prepared.task_id),
                        )
                    elif case == "reorder":
                        connection.execute(
                            "UPDATE task_reclaim_transitions SET "
                            "prior_claim_generation=3, replacement_claim_generation=4 "
                            "WHERE task_id=? AND replacement_claim_generation=2",
                            (prepared.task_id,),
                        )
                    else:
                        connection.execute(
                            "UPDATE task_reclaim_transitions SET prior_attempt_id=? "
                            "WHERE task_id=? AND replacement_claim_generation=2",
                            (third.attempt_id, prepared.task_id),
                        )
                with self.assertRaises(ControlStoreUnavailable):
                    self.kernel.control_store.check()
                if case == "missing":
                    with self.assertRaises(ControlStoreUnavailable):
                        self.claim_disjoint(other)

        self.initialize("reclaim-history-duplicate")
        prepared = self.prepare()
        first = self.claim(prepared)
        self.reclaim(
            prepared,
            first,
            suffix="duplicate",
            reason="one authoritative transition",
        )
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM task_reclaim_transitions WHERE task_id=?",
                (prepared.task_id,),
            ).fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO task_reclaim_transitions("
                    "transition_id, authority_id, task_id, prior_attempt_id, "
                    "replacement_attempt_id, prior_claim_generation, "
                    "replacement_claim_generation, recovery_reason, "
                    "prior_coordinator_session_id, prior_worker_id, "
                    "replacement_coordinator_session_id, replacement_worker_id, "
                    "reclaimed_at, transition_record_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "f" * 64,
                        row["authority_id"],
                        row["task_id"],
                        row["prior_attempt_id"],
                        row["replacement_attempt_id"],
                        row["prior_claim_generation"],
                        row["replacement_claim_generation"],
                        row["recovery_reason"],
                        row["prior_coordinator_session_id"],
                        row["prior_worker_id"],
                        row["replacement_coordinator_session_id"],
                        row["replacement_worker_id"],
                        row["reclaimed_at"],
                        row["transition_record_json"],
                    ),
                )
        self.assertEqual(len(self.kernel.control_store.task_reclaim_history(prepared.task_id)), 1)

    def test_v5_fresh_and_single_reclaim_migrate_but_lost_multi_reclaim_fails_atomically(
        self,
    ) -> None:
        self.initialize("reclaim-history-v5-fresh")
        fresh = self.prepare()
        self.claim(fresh)
        self.downgrade_reclaim_history_to_v5()
        migrated = VideoWorkflowKernel(self.workspace)
        self.assertEqual(migrated.control_store.check().schema_version, 6)
        self.assertEqual(migrated.control_store.task_reclaim_history(fresh.task_id), [])

        self.initialize("reclaim-history-v5-single")
        prepared = self.prepare()
        first = self.claim(prepared)
        replacement = self.reclaim(
            prepared,
            first,
            suffix="single",
            reason="recoverable current v5 reason",
        )
        self.downgrade_reclaim_history_to_v5()
        migrated = VideoWorkflowKernel(self.workspace)
        history = migrated.control_store.task_reclaim_history(prepared.task_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["prior_attempt_id"], first.attempt_id)
        self.assertEqual(history[0]["replacement_attempt_id"], replacement.attempt_id)
        self.assertEqual(history[0]["recovery_reason"], "recoverable current v5 reason")

        self.initialize("reclaim-history-v5-multiple")
        prepared = self.prepare()
        first = self.claim(prepared)
        second = self.reclaim(
            prepared,
            first,
            suffix="two",
            reason="lost v5 reason one",
        )
        self.reclaim(
            prepared,
            second,
            suffix="three",
            reason="retained v5 reason two",
        )
        self.downgrade_reclaim_history_to_v5()
        with self.assertRaises(ControlStoreUnavailable):
            VideoWorkflowKernel(self.workspace)
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='task_reclaim_transitions'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                5,
            )


class GlobalTaskAuthorityHealthTests(unittest.TestCase, Issue5RepairHarness):
    def test_completion_authority_content_drift_blocks_global_check_and_other_claim(
        self,
    ) -> None:
        prepared, claimed = self.ready("completion-authority-global-drift")
        other = self.prepare("other-completion-authority-claim")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            row = connection.execute(
                "SELECT completion_record_json FROM task_completion_authorities "
                "WHERE attempt_id=?",
                (claimed.attempt_id,),
            ).fetchone()
            completion = json.loads(row[0])
            completion["validated_at"] = "2026-07-15T23:59:59+00:00"
            connection.execute(
                "UPDATE task_completion_authorities SET completion_record_json=? "
                "WHERE attempt_id=?",
                (
                    canonical_json_bytes(completion).decode("utf-8"),
                    claimed.attempt_id,
                ),
            )

        with self.assertRaises(ControlStoreUnavailable):
            self.kernel.control_store.check()
        with self.assertRaises(ControlStoreUnavailable):
            self.claim(other)

    def test_missing_promotion_identity_version_blocks_global_check_and_other_claim(
        self,
    ) -> None:
        prepared, claimed = self.ready("promotion-version-global-gap")
        self.promote(prepared, claimed)
        other = self.prepare("other-promotion-version-claim")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute(
                "DELETE FROM task_promotion_identity_versions WHERE intent_id IN "
                "(SELECT intent_id FROM task_promotion_intents WHERE task_id=?)",
                (prepared.task_id,),
            )

        with self.assertRaises(ControlStoreUnavailable):
            self.kernel.control_store.check()
        with self.assertRaises(ControlStoreUnavailable):
            self.claim(other)

    def test_completion_authority_coverage_rejects_missing_extra_and_partial_rows(
        self,
    ) -> None:
        cases = ("missing", "extra", "authority-without-fingerprint")
        for case in cases:
            with self.subTest(case=case):
                if case == "authority-without-fingerprint":
                    self.initialize(f"completion-coverage-{case}")
                    prepared = self.prepare()
                    claimed = self.claim(prepared)
                    completion = {
                        "schema_name": "task-completion-record",
                        "schema_version": "1.0.0",
                        "kernel_version": "2.0.0",
                        "task_id": prepared.task_id,
                        "attempt_id": claimed.attempt_id,
                        "claim_generation": claimed.claim_generation,
                        "task_envelope_sha256": sha256_file(prepared.envelope_path),
                        "validated_authority_revision": 1,
                        "validated_run_record_sha256": sha256_file(
                            self.run_dir / "workflow/run.json"
                        ),
                        "validated_inputs": [
                            {
                                "logical_id": "source_manifest",
                                "generation": 1,
                                "sha256": read_json(prepared.envelope_path)[
                                    "input_artifacts"
                                ][0]["sha256"],
                            }
                        ],
                        "outputs": [
                            {
                                "logical_id": "source_acquisition_decision",
                                "attempt_path": "o/p.json",
                                "canonical_path": PATCH_CANONICAL,
                                "sha256": "0" * 64,
                            }
                        ],
                        "gate_status": "pass",
                        "validated_at": "2026-07-15T04:00:00+00:00",
                    }
                    authority_attempt_id = claimed.attempt_id
                    authority_json = canonical_json_bytes(completion).decode("utf-8")
                else:
                    _, claimed = self.ready(f"completion-coverage-{case}")
                    authority_attempt_id = claimed.attempt_id
                    authority_json = None
                with sqlite3.connect(self.kernel.control_store.path) as connection:
                    if case == "missing":
                        connection.execute(
                            "DELETE FROM task_completion_authorities WHERE attempt_id=?",
                            (authority_attempt_id,),
                        )
                    elif case == "extra":
                        connection.execute("PRAGMA foreign_keys=OFF")
                        existing = connection.execute(
                            "SELECT completion_record_json FROM "
                            "task_completion_authorities WHERE attempt_id=?",
                            (authority_attempt_id,),
                        ).fetchone()[0]
                        connection.execute(
                            "INSERT INTO task_completion_authorities("
                            "attempt_id, completion_record_json) VALUES (?, ?)",
                            ("f" * 24, existing),
                        )
                    else:
                        connection.execute(
                            "INSERT INTO task_completion_authorities("
                            "attempt_id, completion_record_json) VALUES (?, ?)",
                            (authority_attempt_id, authority_json),
                        )
                with self.assertRaises(ControlStoreUnavailable):
                    self.kernel.control_store.check()

    def test_promotion_identity_coverage_rejects_orphan_version_row(self) -> None:
        self.initialize("promotion-version-orphan")
        with sqlite3.connect(self.kernel.control_store.path) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "INSERT INTO task_promotion_identity_versions("
                "intent_id, identity_version) VALUES (?, 'evidence-v2')",
                ("f" * 64,),
            )
        with self.assertRaises(ControlStoreUnavailable):
            self.kernel.control_store.check()

    def test_global_task_authority_validation_accepts_fresh_and_v4_migrated_rows(
        self,
    ) -> None:
        prepared, claimed = self.ready("global-authority-fresh")
        self.promote(prepared, claimed)
        self.assertEqual(self.kernel.control_store.check().schema_version, 6)

        prepared, claimed = self.ready("global-authority-v4")
        self.promote(prepared, claimed)
        self.downgrade_promotion_to_real_legacy_v4(prepared, claimed)
        migrated = VideoWorkflowKernel(self.workspace)
        self.assertEqual(migrated.control_store.check().schema_version, 6)


class ContractAndPromptRepairTests(unittest.TestCase):
    def test_run_record_versions_share_true_identity_and_major_paths(self) -> None:
        manifest = read_json(PROJECT_ROOT / "schemas/video-workflow/registry.v1.json")
        run_entries = [
            entry for entry in manifest["contracts"] if entry["schema_name"] == "run-record"
        ]
        self.assertEqual(
            {(entry["schema_name"], entry["schema_version"]) for entry in run_entries},
            {("run-record", "1.0.0"), ("run-record", "2.0.0")},
        )
        v2 = next(entry for entry in run_entries if entry["schema_version"] == "2.0.0")
        self.assertEqual(
            v2["schema_path"], "schemas/video-workflow/v2/run-record.v2.schema.json"
        )
        self.assertEqual(
            v2["schema_id"],
            "https://video2pdf.local/schemas/video-workflow/v2/run-record.v2.schema.json",
        )
        schema = read_json(PROJECT_ROOT / v2["schema_path"])
        self.assertEqual(schema["$id"], v2["schema_id"])
        contracts = ContractRegistry(PROJECT_ROOT)
        contracts.validate(
            "run-record",
            read_json(
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/contracts/run-record.v2.valid.json"
            ),
        )
        with self.assertRaises(UnknownContractVersion):
            contracts.validate_run_record(
                {"schema_name": "run-record", "schema_version": "3.0.0"}
            )

    def test_duplicate_run_record_version_is_rejected(self) -> None:
        contracts = ContractRegistry(PROJECT_ROOT)
        duplicate = copy.deepcopy(contracts._canonical["contracts"][-1])
        contracts._manifest = copy.deepcopy(contracts._canonical)
        contracts._manifest["contracts"].append(duplicate)
        contracts._canonical = copy.deepcopy(contracts._manifest)
        with self.assertRaises(ContractError):
            contracts._load_entries()

    def test_generated_prompt_contains_semantics_and_boundaries_only(self) -> None:
        prompt = (
            PROJECT_ROOT / "prompts/video-workflow/roles/source-acquisition.v1.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Completion Gate", prompt)
        self.assertNotIn("promotion", prompt.casefold())
        self.assertIn("bounded Source Acquisition Judgment Patch", prompt)

    def test_slice2_evidence_closes_the_review_repair_command_set(self) -> None:
        collector = (
            PROJECT_ROOT / "scripts/collect_slice2_exit_evidence.py"
        ).read_text(encoding="utf-8")
        validator = (
            PROJECT_ROOT / "scripts/validate_slice_exit_evidence.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"slice2-review-repairs"', collector)
        self.assertIn(
            '"tests.video_workflow.test_issue5_review_repairs"', collector
        )
        self.assertIn(
            "control_store_v1_through_v4_migrate_atomically_to_v5", collector
        )
        self.assertIn('"slice2-review-repairs"', validator)


def read_json_string(value: str) -> object:
    import json

    return json.loads(value)


if __name__ == "__main__":
    unittest.main()

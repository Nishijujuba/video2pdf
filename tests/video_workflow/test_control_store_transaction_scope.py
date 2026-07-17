from __future__ import annotations

import copy
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
import uuid
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel import control_store as control_store_module  # noqa: E402
from video2pdf_workflow_kernel.control_store import ControlStore  # noqa: E402
from video2pdf_workflow_kernel.contracts import ContractRegistry  # noqa: E402
from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ControlStoreUnavailable,
    KernelConflict,
    TaskFault,
)
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    read_json,
    sha256_file,
    write_json_atomic,
)


FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
TASK_START = "2026-07-15T01:02:03+08:00"
RESOURCE_V8_TABLES = (
    "resource_lease_resources",
    "resource_leases",
    "resource_control_events",
    "resource_circuit_breakers",
    "resource_fairness_cursors",
    "resource_queue_entries",
    "resource_sequences",
    "resource_configurations",
)


def trusted_transaction_provider_verifier(**identity: object) -> str:
    return f"provider-proof://transaction-scope/{identity['terminal_result_id']}"


class ControlStoreTransactionScopeTests(unittest.TestCase):
    def new_workspace(self, label: str) -> Path:
        root = TEST_RUNS / f"transaction-scope-{label}-{uuid.uuid4().hex[:8]}"
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=False)
        return workspace

    def new_store(self, label: str) -> ControlStore:
        return ControlStore.initialize(
            self.new_workspace(label),
            ContractRegistry(PROJECT_ROOT),
        )

    def prepare_distinct_initialization(
        self,
        store: ControlStore,
        label: str,
    ) -> str:
        root = store.workspace_root / "runs" / label
        return store.prepare_initialization(
            run_id=f"run-{label}-{uuid.uuid4().hex[:8]}",
            output_path=root,
            intent_id=f"intent-{label}-{uuid.uuid4().hex}",
            staging_path=store.workspace_root / "待删除" / f"staging-{label}",
        )

    def prepare_completed_task(
        self,
        kernel: VideoWorkflowKernel,
        run_dir: Path,
        *,
        logical_task_key: str,
    ):
        prepared = kernel.prepare_source_acquisition_task(
            run_dir,
            logical_task_key=logical_task_key,
            prepared_at=TASK_START,
        )
        claimed = kernel.claim_task(
            run_dir,
            prepared.task_id,
            coordinator_session_id="transaction-coordinator",
            worker_id=f"worker-{logical_task_key}",
        )
        launch_tokens: list[str] = []
        kernel.launch_admitted_task(
            claimed.attempt_id,
            claimed.claim_generation,
            ("codex_semantic",),
            lambda launch_token: launch_tokens.append(launch_token) or "started",
        )
        kernel.release_resource_lease(
            claimed.attempt_id,
            claimed.claim_generation,
            launch_tokens[0],
            terminal_evidence={
                "evidence_class": "provider_terminal_result",
                "provider": "transaction-scope-provider",
                "terminal_result_id": f"transaction-scope-{claimed.attempt_id}",
                "declared_outcome": "succeeded",
                "observed_at": TASK_START,
            },
        )
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
                        "rationale": "The immutable English subtitle fixture is usable.",
                    },
                    "known_gaps": [],
                },
            },
        )
        kernel.complete_task(
            run_dir,
            task_id=prepared.task_id,
            attempt_id=claimed.attempt_id,
            claim_generation=claimed.claim_generation,
        )
        return prepared, claimed

    def test_global_authority_preflight_runs_before_begin_immediate(self) -> None:
        store = self.new_store("preflight-before-lock")
        events: list[str] = []
        original_connect = store._connect
        original_validate = store._validate_reclaim_history_before_mutation

        def observed_connect() -> sqlite3.Connection:
            connection = original_connect()
            connection.set_trace_callback(lambda statement: events.append(statement))
            return connection

        def observed_validate(connection: sqlite3.Connection) -> None:
            events.append("GLOBAL_AUTHORITY_PREFLIGHT")
            original_validate(connection)

        with mock.patch.object(store, "_connect", side_effect=observed_connect), mock.patch.object(
            store,
            "_validate_reclaim_history_before_mutation",
            side_effect=observed_validate,
        ):
            self.assertEqual(
                self.prepare_distinct_initialization(store, "preflight-before-lock"),
                "PREPARED",
            )

        preflight_index = events.index("GLOBAL_AUTHORITY_PREFLIGHT")
        writer_index = next(
            index
            for index, statement in enumerate(events)
            if statement.strip().upper() == "BEGIN IMMEDIATE"
        )
        self.assertLess(preflight_index, writer_index)

    def test_active_claim_lookup_uses_authority_state_index(self) -> None:
        store = self.new_store("claim-query-plan")
        with sqlite3.connect(store.path) as connection:
            connection.executemany(
                "INSERT INTO task_claims(task_id, authority_kind, authority_id, "
                "envelope_sha256, write_set_json, state, claim_generation, "
                "attempt_id, coordinator_session_id, worker_id, reclaim_reason, "
                "updated_at) VALUES (?, 'kernel_run', ?, ?, ?, 'TERMINAL', 1, "
                "?, 'historical-coordinator', 'historical-worker', NULL, ?)",
                (
                    (
                        f"historical-task-{index:05d}",
                        f"historical-run-{index % 20:02d}",
                        f"{index:064x}",
                        f'["workflow/archive/{index:05d}.json"]',
                        f"historical-attempt-{index:05d}",
                        TASK_START,
                    )
                    for index in range(3_000)
                ),
            )
            connection.execute("ANALYZE task_claims")
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT task_id, write_set_json "
                "FROM task_claims WHERE authority_kind='kernel_run' "
                "AND authority_id=? AND state='ACTIVE'",
                ("run-query-plan",),
            ).fetchall()
            cardinality = int(
                connection.execute("SELECT COUNT(*) FROM task_claims").fetchone()[0]
            )

        detail = " ".join(str(row[3]).upper() for row in plan)
        self.assertEqual(cardinality, 3_000)
        self.assertIn("SEARCH TASK_CLAIMS USING INDEX", detail)
        self.assertNotIn("SCAN TASK_CLAIMS", detail)

    def test_claim_overlap_scan_runs_in_read_snapshot_before_writer_lock(self) -> None:
        workspace = self.new_workspace("claim-overlap-phase")
        kernel = VideoWorkflowKernel(
            workspace,
            resource_provider_verifiers={
                "transaction-scope-provider": trusted_transaction_provider_verifier,
            },
        )
        run_dir = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"transaction-claim-{uuid.uuid4().hex[:8]}",
        ).run_dir
        first = kernel.prepare_source_acquisition_task(
            run_dir,
            logical_task_key="transaction-claim-left",
            prepared_at=TASK_START,
        )
        kernel.claim_task(
            run_dir,
            first.task_id,
            coordinator_session_id="claim-coordinator-left",
            worker_id="claim-worker-left",
        )
        second = kernel.prepare_source_acquisition_task(
            run_dir,
            logical_task_key="transaction-claim-right",
            prepared_at=TASK_START,
        )
        original_overlap = kernel.control_store._write_sets_overlap
        overlap_calls = 0
        writer_phase_violations = 0

        def observed_overlap(left_json: str, right: tuple[str, ...]) -> bool:
            nonlocal overlap_calls, writer_phase_violations
            overlap_calls += 1
            probe = sqlite3.connect(
                kernel.control_store.path,
                isolation_level=None,
                timeout=0.05,
            )
            try:
                probe.execute("PRAGMA busy_timeout=50")
                try:
                    probe.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError:
                    writer_phase_violations += 1
                else:
                    probe.execute("ROLLBACK")
            finally:
                if probe.in_transaction:
                    probe.execute("ROLLBACK")
                probe.close()
            return original_overlap(left_json, right)

        with mock.patch.object(
            kernel.control_store,
            "_write_sets_overlap",
            side_effect=observed_overlap,
        ):
            with self.assertRaises(KernelConflict):
                kernel.claim_task(
                    run_dir,
                    second.task_id,
                    coordinator_session_id="claim-coordinator-right",
                    worker_id="claim-worker-right",
                )

        self.assertGreaterEqual(overlap_calls, 1)
        self.assertEqual(writer_phase_violations, 0)

    def test_promotion_and_run_state_authority_work_precedes_writer_lock(self) -> None:
        workspace = self.new_workspace("promotion-writer-phase")
        kernel = VideoWorkflowKernel(
            workspace,
            resource_provider_verifiers={
                "transaction-scope-provider": trusted_transaction_provider_verifier,
            },
        )
        run_dir = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"transaction-promotion-{uuid.uuid4().hex[:8]}",
        ).run_dir
        first, first_claim = self.prepare_completed_task(
            kernel,
            run_dir,
            logical_task_key="transaction-promotion-first",
        )
        observed_operations: list[str] = []
        writer_phase_violations: list[str] = []
        instrument_authority_work = True

        def observe(operation: str) -> None:
            if not instrument_authority_work:
                return
            observed_operations.append(operation)
            probe = sqlite3.connect(
                kernel.control_store.path,
                isolation_level=None,
                timeout=0.05,
            )
            try:
                probe.execute("PRAGMA busy_timeout=50")
                try:
                    probe.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError:
                    writer_phase_violations.append(operation)
                else:
                    probe.execute("ROLLBACK")
            finally:
                if probe.in_transaction:
                    probe.execute("ROLLBACK")
                probe.close()

        original_sha256 = control_store_module.hashlib.sha256
        original_canonical = control_store_module.canonical_json_bytes
        original_promotion_validate = (
            kernel.control_store._validate_task_promotion_intent
        )
        original_run_sha = kernel.control_store._current_run_record_sha
        original_next_revision = kernel.control_store._next_run_revision
        original_run_validate = kernel.control_store._validate_run_state_mutation_row
        original_prepared_validate = (
            kernel.control_store._validate_prepared_run_state_mutation
        )

        def observed_sha256(*args, **kwargs):
            observe("hashlib.sha256")
            return original_sha256(*args, **kwargs)

        def observed_canonical(*args, **kwargs):
            observe("canonical_json_bytes")
            return original_canonical(*args, **kwargs)

        def observed_promotion_validate(*args, **kwargs):
            observe("validate_task_promotion")
            return original_promotion_validate(*args, **kwargs)

        def observed_run_sha(*args, **kwargs):
            observe("current_run_record_sha")
            return original_run_sha(*args, **kwargs)

        def observed_next_revision(*args, **kwargs):
            observe("next_run_revision")
            return original_next_revision(*args, **kwargs)

        def observed_run_validate(*args, **kwargs):
            observe("validate_run_state_mutation")
            return original_run_validate(*args, **kwargs)

        def observed_prepared_validate(*args, **kwargs):
            observe("validate_prepared_run_state_mutation")
            return original_prepared_validate(*args, **kwargs)

        with mock.patch.object(
            control_store_module.hashlib,
            "sha256",
            side_effect=observed_sha256,
        ), mock.patch.object(
            control_store_module,
            "canonical_json_bytes",
            side_effect=observed_canonical,
        ), mock.patch.object(
            kernel.control_store,
            "_validate_task_promotion_intent",
            side_effect=observed_promotion_validate,
        ), mock.patch.object(
            kernel.control_store,
            "_current_run_record_sha",
            side_effect=observed_run_sha,
        ), mock.patch.object(
            kernel.control_store,
            "_next_run_revision",
            side_effect=observed_next_revision,
        ), mock.patch.object(
            kernel.control_store,
            "_validate_run_state_mutation_row",
            side_effect=observed_run_validate,
        ), mock.patch.object(
            kernel.control_store,
            "_validate_prepared_run_state_mutation",
            side_effect=observed_prepared_validate,
        ):
            kernel.promote_task(
                run_dir,
                task_id=first.task_id,
                attempt_id=first_claim.attempt_id,
                claim_generation=first_claim.claim_generation,
            )
            first_intent = kernel.control_store.task_promotion_for_attempt(
                first.task_id,
                first_claim.attempt_id,
            )
            self.assertIsNotNone(first_intent)

            instrument_authority_work = False
            try:
                second, second_claim = self.prepare_completed_task(
                    kernel,
                    run_dir,
                    logical_task_key="transaction-promotion-abort",
                )
            finally:
                instrument_authority_work = True
            with self.assertRaises(TaskFault):
                kernel.promote_task(
                    run_dir,
                    task_id=second.task_id,
                    attempt_id=second_claim.attempt_id,
                    claim_generation=second_claim.claim_generation,
                    fault_point="after_promotion_intent_prepared",
                )
            abort_intent = kernel.control_store.task_promotion_for_attempt(
                second.task_id,
                second_claim.attempt_id,
            )
            self.assertIsNotNone(abort_intent)
            kernel.control_store.abort_task_promotion(
                str(abort_intent["intent_id"])
            )

            run_path = run_dir / "workflow/run.json"
            run_record = read_json(run_path)
            old_run_sha = sha256_file(run_path)
            mutation_id = kernel.control_store.derive_run_state_mutation_id(
                run_id=run_record["run_id"],
                expected_run_revision=run_record["coordination_revision"],
                old_run_record_sha256=old_run_sha,
            )
            replacement = copy.deepcopy(run_record)
            replacement["coordination_revision"] += 1
            replacement["last_mutation_intent_id"] = mutation_id
            for checkpoint in replacement["checkpoints"].values():
                checkpoint["status"] = "stale"
            mutation = kernel.control_store.prepare_run_state_mutation(
                run_id=run_record["run_id"],
                expected_run_revision=run_record["coordination_revision"],
                old_run_record_sha256=old_run_sha,
                replacement_run_record=replacement,
            )
            kernel.control_store.commit_run_state_mutation(
                str(mutation["mutation_id"])
            )
            # Both historical COMMITTED replay paths authenticate the complete
            # current chain while allowing the later valid Run revision.
            kernel.control_store.commit_task_promotion(
                str(first_intent["intent_id"])
            )
            kernel.control_store.commit_run_state_mutation(
                str(mutation["mutation_id"])
            )

        for operation in {
            "hashlib.sha256",
            "canonical_json_bytes",
            "validate_task_promotion",
            "current_run_record_sha",
            "next_run_revision",
            "validate_run_state_mutation",
            "validate_prepared_run_state_mutation",
        }:
            self.assertIn(operation, observed_operations)
        self.assertEqual(writer_phase_violations, [])

        with sqlite3.connect(kernel.control_store.path) as connection:
            connection.execute(
                "UPDATE initialization_intents SET run_record_sha256=? "
                "WHERE run_id=? AND state='COMMITTED'",
                ("0" * 64, run_record["run_id"]),
            )
        with self.assertRaises(ControlStoreUnavailable):
            kernel.control_store.commit_task_promotion(
                str(first_intent["intent_id"])
            )
        with self.assertRaises(ControlStoreUnavailable):
            kernel.control_store.commit_run_state_mutation(
                str(mutation["mutation_id"])
            )

    def test_commit_between_preflight_and_writer_lock_retries_fresh_snapshot(
        self,
    ) -> None:
        store = self.new_store("racing-raw-writer")
        original_transition = getattr(
            store,
            "_begin_immediate_if_snapshot_unchanged",
            None,
        )
        transition_calls = 0

        def inject_raw_commit(
            connection: sqlite3.Connection,
            expected_data_version: int,
        ) -> bool:
            nonlocal transition_calls
            transition_calls += 1
            if transition_calls == 1:
                racing = sqlite3.connect(store.path, isolation_level=None, timeout=1)
                try:
                    racing.execute("PRAGMA busy_timeout=1000")
                    racing.execute("BEGIN IMMEDIATE")
                    racing.execute(
                        "INSERT INTO control_store_metadata(key, value) VALUES (?, ?)",
                        ("transaction-scope-race", "committed"),
                    )
                    racing.execute("COMMIT")
                finally:
                    if racing.in_transaction:
                        racing.execute("ROLLBACK")
                    racing.close()
            if original_transition is None:
                return True
            return original_transition(connection, expected_data_version)

        with mock.patch.object(
            store,
            "_begin_immediate_if_snapshot_unchanged",
            side_effect=inject_raw_commit,
            create=True,
        ):
            self.assertEqual(
                self.prepare_distinct_initialization(store, "racing-raw-writer"),
                "PREPARED",
            )

        self.assertEqual(transition_calls, 2)
        with sqlite3.connect(store.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM control_store_metadata "
                    "WHERE key='transaction-scope-race'"
                ).fetchone()[0],
                "committed",
            )

    def test_v6_migration_artifact_reads_and_hashes_precede_writer_phase(self) -> None:
        workspace = self.new_workspace("migration-artifact-phase")
        kernel = VideoWorkflowKernel(workspace)
        run_dir = kernel.trace_source_ready(
            fixture=FIXTURE,
            task_start=TASK_START,
            request_id=f"transaction-migration-{uuid.uuid4().hex[:8]}",
        ).run_dir
        prepared = kernel.prepare_source_acquisition_task(
            run_dir,
            logical_task_key="transaction-migration",
            prepared_at=TASK_START,
        )
        kernel.claim_task(
            run_dir,
            prepared.task_id,
            coordinator_session_id="migration-coordinator",
            worker_id="migration-worker",
        )
        with sqlite3.connect(kernel.control_store.path) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            for table in RESOURCE_V8_TABLES:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute("DROP TABLE task_claim_authorities")
            connection.execute(
                "DELETE FROM schema_migrations WHERE version IN (7, 8)"
            )

        original_read_json = control_store_module.read_json
        original_sha256_file = control_store_module.sha256_file
        inspected: list[tuple[str, Path]] = []
        writer_phase_violations: list[tuple[str, Path]] = []

        def observe_phase(operation: str, path: Path) -> None:
            candidate = Path(path)
            if candidate.name != "task.json" or "tasks" not in candidate.parts:
                return
            inspected.append((operation, candidate))
            probe = sqlite3.connect(
                kernel.control_store.path,
                isolation_level=None,
                timeout=0.05,
            )
            try:
                probe.execute("PRAGMA busy_timeout=50")
                try:
                    probe.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError:
                    writer_phase_violations.append((operation, candidate))
                else:
                    probe.execute("ROLLBACK")
            finally:
                if probe.in_transaction:
                    probe.execute("ROLLBACK")
                probe.close()

        def observed_read_json(path: Path) -> dict:
            observe_phase("read_json", Path(path))
            return original_read_json(path)

        def observed_sha256_file(path: Path) -> str:
            observe_phase("sha256_file", Path(path))
            return original_sha256_file(path)

        with mock.patch.object(
            control_store_module,
            "read_json",
            side_effect=observed_read_json,
        ), mock.patch.object(
            control_store_module,
            "sha256_file",
            side_effect=observed_sha256_file,
        ):
            migrated = VideoWorkflowKernel(workspace)

        self.assertGreaterEqual(len(inspected), 2)
        self.assertEqual(writer_phase_violations, [])
        self.assertEqual(migrated.control_store.check().schema_version, 8)

    def test_concurrent_valid_mutations_retry_without_lost_updates(self) -> None:
        store = self.new_store("concurrent-valid")
        peer = ControlStore(store.workspace_root, ContractRegistry(PROJECT_ROOT))
        barrier = threading.Barrier(2)
        results: list[str] = []
        failures: list[BaseException] = []

        def run_mutation(target: ControlStore, label: str) -> None:
            original_transition = target._begin_immediate_if_snapshot_unchanged
            first = True

            def synchronize_once(
                connection: sqlite3.Connection,
                expected_data_version: int,
            ) -> bool:
                nonlocal first
                if first:
                    first = False
                    barrier.wait(timeout=5)
                return original_transition(connection, expected_data_version)

            try:
                with mock.patch.object(
                    target,
                    "_begin_immediate_if_snapshot_unchanged",
                    side_effect=synchronize_once,
                ):
                    results.append(self.prepare_distinct_initialization(target, label))
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        threads = [
            threading.Thread(target=run_mutation, args=(store, "left")),
            threading.Thread(target=run_mutation, args=(peer, "right")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(sorted(results), ["PREPARED", "PREPARED"])
        with sqlite3.connect(store.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM initialization_intents"
                ).fetchone()[0],
                2,
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

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
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402


FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
TASK_START = "2026-07-15T01:02:03+08:00"


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
            connection.execute("DROP TABLE task_claim_authorities")
            connection.execute("DELETE FROM schema_migrations WHERE version=7")

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
        self.assertEqual(migrated.control_store.check().schema_version, 7)

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

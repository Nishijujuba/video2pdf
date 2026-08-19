from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
from tests.video_workflow._test_run import new_workflow_workspace

SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel import control_store as control_store_module  # noqa: E402
from video2pdf_workflow_kernel.contracts import ContractRegistry  # noqa: E402
from video2pdf_workflow_kernel.control_store import ControlStore  # noqa: E402
from video2pdf_workflow_kernel.errors import ControlStoreUnavailable  # noqa: E402
from video2pdf_workflow_kernel.utils import write_json_atomic  # noqa: E402


class ControlStoreV11FastPathTests(unittest.TestCase):
    def new_store(self, label: str) -> ControlStore:
        return ControlStore.initialize(
            new_workflow_workspace(self.id(), label=f"v11-fastpath-{label}"),
            ContractRegistry(PROJECT_ROOT),
        )

    def test_current_v11_store_skips_migration_snapshot_planning(self) -> None:
        store = self.new_store("current")
        original_connect = sqlite3.connect
        backup_calls: list[str] = []

        class TrackingConnection(sqlite3.Connection):
            def backup(self, *args, **kwargs):
                backup_calls.append("backup")
                return super().backup(*args, **kwargs)

        def tracking_connect(*args, **kwargs):
            kwargs["factory"] = TrackingConnection
            return original_connect(*args, **kwargs)

        with mock.patch.object(
            control_store_module.sqlite3,
            "connect",
            side_effect=tracking_connect,
        ), mock.patch.object(
            ControlStore,
            "_prepare_migration_plan",
            autospec=True,
            wraps=ControlStore._prepare_migration_plan,
        ) as planner, mock.patch.object(
            ControlStore,
            "_migration_rows",
            autospec=True,
            wraps=ControlStore._migration_rows,
        ) as migration_rows, mock.patch.object(
            ControlStore,
            "_validate_resource_tables",
            autospec=True,
            wraps=ControlStore._validate_resource_tables,
        ) as validate_resources:
            reopened = ControlStore(store.workspace_root, store.contracts)

        self.assertEqual(planner.call_count, 0)
        self.assertEqual(migration_rows.call_count, 0)
        self.assertEqual(validate_resources.call_count, 1)
        self.assertEqual(backup_calls, [])
        self.assertEqual(reopened.check().schema_version, 11)
        self.assertEqual(validate_resources.call_count, 1)

        primary = store._connect()
        bypassing_secondary = mock.Mock()
        try:
            with mock.patch.object(
                store,
                "_connect_raw",
                return_value=bypassing_secondary,
            ), self.assertRaisesRegex(
                ControlStoreUnavailable,
                "Control Store second connection bypassed an immediate writer lock",
            ):
                store._probe_lock_contention(primary)
        finally:
            primary.close()
        self.assertEqual(
            bypassing_secondary.execute.call_args_list[0],
            mock.call("PRAGMA busy_timeout=0"),
        )
        bypassing_secondary.close.assert_called_once_with()

    def test_v9_store_adds_lifecycle_contracts_and_migrates_to_v11(
        self,
    ) -> None:
        store = self.new_store("v9-delivery-lifecycle")
        with sqlite3.connect(store.path) as connection:
            for index in (
                "one_nonterminal_delivery_lifecycle_per_run",
                "one_delivery_lifecycle_revision_per_run",
                "one_held_projection_publication_slot_per_path",
            ):
                connection.execute(f"DROP INDEX {index}")
            connection.execute("DROP TABLE projection_publication_slots")
            connection.execute("DROP TABLE delivery_lifecycle_intents")
            connection.execute("DROP INDEX one_projection_per_batch_item")
            connection.execute("DROP TABLE batch_item_projections")
            connection.execute("DROP TABLE batch_records")
            connection.execute(
                "DELETE FROM schema_migrations WHERE version IN (10, 11)"
            )

        reopened = ControlStore(store.workspace_root, store.contracts)

        self.assertEqual(reopened.check().schema_version, 11)
        with sqlite3.connect(store.path) as connection:
            objects = {
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT type, name FROM sqlite_master WHERE name IN ("
                    "'delivery_lifecycle_intents',"
                    "'projection_publication_slots',"
                    "'batch_records',"
                    "'batch_item_projections',"
                    "'one_nonterminal_delivery_lifecycle_per_run',"
                    "'one_delivery_lifecycle_revision_per_run',"
                    "'one_held_projection_publication_slot_per_path',"
                    "'one_projection_per_batch_item')"
                )
            }
        self.assertEqual(
            {
                ("table", "delivery_lifecycle_intents"),
                ("table", "projection_publication_slots"),
                ("table", "batch_records"),
                ("table", "batch_item_projections"),
                ("index", "one_nonterminal_delivery_lifecycle_per_run"),
                ("index", "one_delivery_lifecycle_revision_per_run"),
                ("index", "one_held_projection_publication_slot_per_path"),
                ("index", "one_projection_per_batch_item"),
            },
            objects,
        )

    def test_partial_v10_delivery_lifecycle_migration_fails_closed(self) -> None:
        store = self.new_store("partial-v10")
        with sqlite3.connect(store.path) as connection:
            for index in (
                "one_nonterminal_delivery_lifecycle_per_run",
                "one_delivery_lifecycle_revision_per_run",
                "one_held_projection_publication_slot_per_path",
            ):
                connection.execute(f"DROP INDEX {index}")
            connection.execute("DROP TABLE projection_publication_slots")
            connection.execute(
                "DELETE FROM schema_migrations WHERE version IN (10, 11)"
            )

        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "partial v10 Delivery Lifecycle migration",
        ):
            ControlStore(store.workspace_root, store.contracts)

    def test_v10_delivery_lifecycle_wrong_sql_fails_closed(self) -> None:
        store = self.new_store("wrong-v10-sql")
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "DROP INDEX one_delivery_lifecycle_revision_per_run"
            )
            connection.execute(
                "CREATE UNIQUE INDEX "
                "one_delivery_lifecycle_revision_per_run "
                "ON delivery_lifecycle_intents(run_id, expected_run_revision)"
            )

        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "SQL authority differs for "
            "one_delivery_lifecycle_revision_per_run",
        ):
            store.check()

    def test_v8_store_still_uses_planner_and_migrates_to_v11(self) -> None:
        store = self.new_store("v8")
        with sqlite3.connect(store.path) as connection:
            for index in (
                "one_nonterminal_delivery_lifecycle_per_run",
                "one_delivery_lifecycle_revision_per_run",
                "one_held_projection_publication_slot_per_path",
            ):
                connection.execute(f"DROP INDEX {index}")
            connection.execute("DROP TABLE projection_publication_slots")
            connection.execute("DROP TABLE delivery_lifecycle_intents")
            for index in (
                "one_nonterminal_source_publication_per_run",
                "one_source_publication_epoch_per_run",
                "one_source_publication_revision_per_run",
            ):
                connection.execute(f"DROP INDEX {index}")
            connection.execute("DROP TABLE source_publication_intents")
            connection.execute("DROP INDEX one_projection_per_batch_item")
            connection.execute("DROP TABLE batch_item_projections")
            connection.execute("DROP TABLE batch_records")
            connection.execute(
                "DELETE FROM schema_migrations WHERE version IN (9, 10, 11)"
            )

        with mock.patch.object(
            ControlStore,
            "_prepare_migration_plan",
            autospec=True,
            wraps=ControlStore._prepare_migration_plan,
        ) as planner:
            reopened = ControlStore(store.workspace_root, store.contracts)

        self.assertGreaterEqual(planner.call_count, 1)
        self.assertEqual(reopened.check().schema_version, 11)

    def test_migration_ledger_gap_fails_before_planning(self) -> None:
        store = self.new_store("gap")
        with sqlite3.connect(store.path) as connection:
            connection.execute("DELETE FROM schema_migrations WHERE version=5")

        with mock.patch.object(
            ControlStore,
            "_prepare_migration_plan",
            side_effect=AssertionError("ledger gap invoked migration planner"),
        ), self.assertRaisesRegex(
            ControlStoreUnavailable,
            "migration ledger is not contiguous",
        ):
            ControlStore(store.workspace_root, store.contracts)

    def test_future_schema_version_fails_before_planning(self) -> None:
        store = self.new_store("future")
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (12)"
            )

        with mock.patch.object(
            ControlStore,
            "_prepare_migration_plan",
            side_effect=AssertionError("future version invoked migration planner"),
        ), self.assertRaisesRegex(
            ControlStoreUnavailable,
            "unknown Control Store schema version: 12",
        ):
            ControlStore(store.workspace_root, store.contracts)

    def test_missing_maintenance_index_uses_old_repair_path(self) -> None:
        store = self.new_store("maintenance-index")
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP INDEX task_claims_by_authority_state_task")

        with mock.patch.object(
            ControlStore,
            "_prepare_migration_plan",
            autospec=True,
            wraps=ControlStore._prepare_migration_plan,
        ) as planner:
            reopened = ControlStore(store.workspace_root, store.contracts)

        self.assertGreaterEqual(planner.call_count, 1)
        self.assertEqual(reopened.check().status, "ok")

    def test_v10_schema_tamper_remains_a_check_failure(self) -> None:
        store = self.new_store("tamper")
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP TABLE source_publication_intents")

        reopened = ControlStore(store.workspace_root, store.contracts)
        with self.assertRaises(ControlStoreUnavailable):
            reopened.check()

    def test_v10_resource_tamper_keeps_specific_constructor_diagnostic(self) -> None:
        store = self.new_store("resource-tamper")
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE resource_configurations "
                "SET configuration_sha256=? WHERE state='ACTIVE'",
                ("0" * 64,),
            )

        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "Resource Admission Configuration fingerprint is invalid",
        ):
            ControlStore(store.workspace_root, store.contracts)

    def test_healthy_check_runs_resource_validation_once(self) -> None:
        store = self.new_store("healthy-check")
        self.assertEqual(control_store_module.LOCK_PROBE_TIMEOUT_MS, 0)

        with mock.patch.object(
            ControlStore,
            "_validate_resource_tables",
            autospec=True,
            wraps=ControlStore._validate_resource_tables,
        ) as validate_resources, mock.patch.object(
            ControlStore,
            "_probe_lock_contention",
            autospec=True,
            wraps=ControlStore._probe_lock_contention,
        ) as probe_lock:
            health = store.check()

        self.assertEqual(health.status, "ok")
        self.assertTrue(health.lock_contention_checked)
        self.assertEqual(health.pragmas["busy_timeout"], control_store_module.BUSY_TIMEOUT_MS)
        self.assertEqual(validate_resources.call_count, 1)
        self.assertEqual(probe_lock.call_count, 1)

    def test_non_resource_check_constraint_damage_keeps_generic_error(self) -> None:
        store = self.new_store("generic-integrity")
        with sqlite3.connect(store.path) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "INSERT INTO initialization_intents("
                "intent_id, run_id, output_path, staging_path, state"
                ") VALUES ('damaged-intent', 'damaged-run', "
                "'damaged-output', 'damaged-staging', 'DAMAGED')"
            )

        with mock.patch.object(
            ControlStore,
            "_validate_resource_tables",
            autospec=True,
            wraps=ControlStore._validate_resource_tables,
        ) as validate_resources:
            with self.assertRaisesRegex(
                ControlStoreUnavailable,
                "^Control Store integrity check failed$",
            ):
                store.check()

        self.assertEqual(validate_resources.call_count, 0)

        primary = store._connect()
        failing_secondary = mock.Mock()
        failing_secondary.execute.side_effect = (
            None,
            sqlite3.OperationalError("disk I/O error"),
        )
        try:
            with mock.patch.object(
                store,
                "_connect_raw",
                return_value=failing_secondary,
            ), self.assertRaisesRegex(
                ControlStoreUnavailable,
                "Control Store lock probe failed unexpectedly: disk I/O error",
            ) as raised:
                store._probe_lock_contention(primary)
        finally:
            primary.close()
        self.assertIsInstance(raised.exception.__cause__, sqlite3.OperationalError)
        self.assertEqual(
            failing_secondary.execute.call_args_list[0],
            mock.call("PRAGMA busy_timeout=0"),
        )
        failing_secondary.close.assert_called_once_with()

        expected_contention = (
            ("busy-primary-code", sqlite3.SQLITE_BUSY, "数据库正忙"),
            ("locked-primary-code", sqlite3.SQLITE_LOCKED, "写入冲突"),
            (
                "busy-extended-code",
                sqlite3.SQLITE_BUSY_RECOVERY,
                "recovery contention",
            ),
            (
                "locked-extended-code",
                sqlite3.SQLITE_LOCKED_SHAREDCACHE,
                "shared-cache contention",
            ),
            ("missing-code-busy-compatibility", None, "database is busy"),
            ("missing-code-compatibility", None, "database is locked"),
        )
        for label, error_code, message in expected_contention:
            with self.subTest(label=label):
                contention = sqlite3.OperationalError(message)
                if error_code is not None:
                    contention.sqlite_errorcode = error_code
                secondary = mock.Mock()
                secondary.execute.side_effect = (None, contention, None, None)
                primary = store._connect()
                try:
                    with mock.patch.object(
                        store,
                        "_connect_raw",
                        return_value=secondary,
                    ):
                        store._probe_lock_contention(primary)
                finally:
                    primary.close()
                self.assertEqual(
                    secondary.execute.call_args_list,
                    [
                        mock.call("PRAGMA busy_timeout=0"),
                        mock.call("BEGIN IMMEDIATE"),
                        mock.call("BEGIN IMMEDIATE"),
                        mock.call("ROLLBACK"),
                    ],
                )
                secondary.close.assert_called_once_with()

        misleading_unknown = sqlite3.OperationalError("database is locked")
        misleading_unknown.sqlite_errorcode = sqlite3.SQLITE_IOERR
        unknown_secondary = mock.Mock()
        unknown_secondary.execute.side_effect = (None, misleading_unknown)
        primary = store._connect()
        try:
            with mock.patch.object(
                store,
                "_connect_raw",
                return_value=unknown_secondary,
            ), self.assertRaisesRegex(
                ControlStoreUnavailable,
                "Control Store lock probe failed unexpectedly: database is locked",
            ) as unknown_raised:
                store._probe_lock_contention(primary)
        finally:
            primary.close()
        self.assertIs(unknown_raised.exception.__cause__, misleading_unknown)
        unknown_secondary.close.assert_called_once_with()

    def test_recovery_sentinel_still_allows_reads_and_blocks_mutation(self) -> None:
        store = self.new_store("sentinel")
        write_json_atomic(
            store.recovery_sentinel_path,
            {
                "operation_id": "restore-fastpath-test",
                "operation": "restore",
                "state": "QUIESCING",
            },
        )

        reopened = ControlStore(store.workspace_root, store.contracts)
        self.assertEqual(reopened.check().status, "ok")
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "persistent recovery authority",
        ):
            reopened.prepare_initialization(
                run_id="blocked-run",
                output_path=store.workspace_root / "blocked-output",
                intent_id="blocked-intent",
                staging_path=store.workspace_root / "blocked-staging",
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_workflow_workspace
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore, SCHEMA_VERSION
from video2pdf_workflow_kernel.errors import ControlStoreUnavailable, KernelConflict
from video2pdf_workflow_kernel.utils import canonical_json_bytes


RUN_TASK_START = "2026-08-16T10:00:00+08:00"


def _record(batch_id: str = "a" * 32, stage: str = "planned") -> dict:
    return {
        "schema_name": "batch-record",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "batch_id": batch_id,
        "batch_identity": {
            "kind": "url_set",
            "canonical_platform": "bilibili",
            "batch_source_identity": "b" * 64,
            "source_url": "https://example.com/playlist",
            "original_title": "Batch",
            "task_start": "2026-08-16T09:05:00+08:00",
            "request_id": "request-one",
        },
        "output_root": "D:/workspace",
        "batch_dir": "D:/workspace/batch/batch-control",
        "control_dir": "D:/workspace/.workflow-control/batches/batch",
        "batch_stage": stage,
        "batch_authority_binding": None,
        "run_task_start": None,
        "item_order": [
            {
                "item_index": 1,
                "part_id": None,
                "canonical_item_id": "BV1xx411c7mD:p1",
                "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/",
                "title": "Part One",
                "selected": True,
            }
        ],
        "run_mappings": [],
        "projections": [],
        "created_at": "2026-08-16T09:05:00+08:00",
        "updated_at": "2026-08-16T09:05:00+08:00",
    }


def _mapping(record: dict, item_index: int = 1) -> dict:
    request_id = f"{record['batch_id']}:{item_index}"
    canonical_item_id = next(
        item["canonical_item_id"]
        for item in record["item_order"]
        if item["item_index"] == item_index
    )
    run_id = hashlib.sha256(
        "\0".join(
            (
                record["batch_identity"]["canonical_platform"],
                canonical_item_id,
                RUN_TASK_START,
                request_id,
            )
        ).encode("utf-8")
    ).hexdigest()[:32]
    return {"item_index": item_index, "run_id": run_id, "request_id": request_id}


class Issue15BatchControlStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = new_workflow_workspace(self.id(), label="batch-store")
        self.store = ControlStore.initialize(self.workspace, ContractRegistry(PROJECT_ROOT))

    def test_fresh_store_is_v11_with_batch_tables(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 11)
        health = self.store.check()
        self.assertEqual(health.schema_version, 11)
        with sqlite3.connect(self.store.path) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("batch_records", tables)
        self.assertIn("batch_item_projections", tables)

    def test_create_batch_record_replays_identical_identity(self) -> None:
        record = _record()
        batch_id, outcome = self.store.create_batch_record(record, record["batch_identity"])
        self.assertEqual(batch_id, record["batch_id"])
        self.assertEqual(outcome, "CREATED")
        batch_id, outcome = self.store.create_batch_record(record, record["batch_identity"])
        self.assertEqual(batch_id, record["batch_id"])
        self.assertEqual(outcome, "REPLAY")

    def test_create_batch_record_conflicts_on_changed_identity(self) -> None:
        record = _record()
        self.store.create_batch_record(record, record["batch_identity"])
        changed = dict(record)
        changed["original_title"] = "Changed Title"
        with self.assertRaises(KernelConflict):
            self.store.create_batch_record(changed, changed["batch_identity"])

    def test_get_and_list_batch_records_round_trip(self) -> None:
        record = _record()
        self.store.create_batch_record(record, record["batch_identity"])
        loaded = self.store.get_batch_record(record["batch_id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["batch_id"], record["batch_id"])
        records = self.store.list_batch_records()
        self.assertEqual([item["batch_id"] for item in records], [record["batch_id"]])

    def test_update_batch_stage_cas(self) -> None:
        record = _record()
        self.store.create_batch_record(record, record["batch_identity"])
        self.store.bind_batch_run_task_start(record["batch_id"], RUN_TASK_START)
        self.store.commit_batch_run_mappings(record["batch_id"], [_mapping(record)])
        self.assertEqual(
            self.store.update_batch_stage(record["batch_id"], "running", "completed"),
            "completed",
        )
        with self.assertRaises(KernelConflict):
            self.store.update_batch_stage(record["batch_id"], "running", "blocked")
        self.assertEqual(
            self.store.get_batch_record(record["batch_id"])["batch_stage"], "completed"
        )

    def test_bind_batch_run_task_start_is_idempotent_and_conflict_closed(self) -> None:
        record = _record()
        self.store.create_batch_record(record, record["batch_identity"])
        self.assertEqual(
            self.store.bind_batch_run_task_start(record["batch_id"], RUN_TASK_START),
            "BOUND",
        )
        self.assertEqual(
            self.store.bind_batch_run_task_start(record["batch_id"], RUN_TASK_START),
            "REPLAY",
        )
        with self.assertRaises(KernelConflict):
            self.store.bind_batch_run_task_start(
                record["batch_id"], "2026-08-16T10:00:01+08:00"
            )

    def test_commit_batch_run_mappings_is_atomic_and_replays(self) -> None:
        record = _record()
        self.store.create_batch_record(record, record["batch_identity"])
        self.store.bind_batch_run_task_start(record["batch_id"], RUN_TASK_START)
        mappings = [_mapping(record)]
        self.assertEqual(
            self.store.commit_batch_run_mappings(record["batch_id"], mappings),
            "COMMITTED",
        )
        stored = self.store.get_batch_record(record["batch_id"])
        self.assertEqual(stored["batch_stage"], "running")
        self.assertEqual(stored["run_task_start"], RUN_TASK_START)
        self.assertEqual(stored["run_mappings"], mappings)
        self.assertEqual(
            self.store.commit_batch_run_mappings(record["batch_id"], mappings),
            "REPLAY",
        )
        with self.assertRaises(KernelConflict):
            self.store.commit_batch_run_mappings(
                record["batch_id"], [{**mappings[0], "run_id": "f" * 32}]
            )

    def test_commit_batch_run_mappings_rejects_partial_selected_set(self) -> None:
        record = _record()
        record["item_order"].append(
            {
                "item_index": 2,
                "part_id": None,
                "canonical_item_id": "BV1xx411c7mD:p2",
                "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/?p=2",
                "title": "Part Two",
                "selected": True,
            }
        )
        self.store.create_batch_record(record, record["batch_identity"])
        self.store.bind_batch_run_task_start(record["batch_id"], RUN_TASK_START)
        with self.assertRaises(KernelConflict):
            self.store.commit_batch_run_mappings(
                record["batch_id"], [_mapping(record, 1)]
            )
        stored = self.store.get_batch_record(record["batch_id"])
        self.assertEqual(stored["batch_stage"], "planned")
        self.assertEqual(stored["run_mappings"], [])

    def test_put_item_projection_revision_increments_and_replays(self) -> None:
        record = _record()
        self.store.create_batch_record(record, record["batch_identity"])
        mapping = _mapping(record)
        run_dir = self.workspace / "runs" / "run-one"
        self.store.bind_run(
            run_id=mapping["run_id"],
            output_path=run_dir,
            initialization_intent_id="intent-one",
        )
        self.store.bind_batch_run_task_start(record["batch_id"], RUN_TASK_START)
        self.store.commit_batch_run_mappings(record["batch_id"], [mapping])
        projection = {
            "schema_name": "batch-item-projection",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "batch_id": record["batch_id"],
            "item_index": 1,
            "run_id": mapping["run_id"],
            "run_state": {
                "phase": "source_acquisition",
                "source_state": "pending",
                "source_blocker": None,
                "coordination_revision": 1,
                "output_path": "D:/workspace/run",
                "delivery": {"stage": "generating", "ownership": {"session_id": "s", "generation": 1}},
            },
            "checkpoint": {"name": "run_initialized", "status": "current"},
            "blocker": None,
            "delivery_outcome": {
                "delivery_stage": "generating",
                "guarded_delivered": False,
                "acceptance_report_sha256": None,
                "guard_report_sha256": None,
                "delivered_at": None,
            },
            "projection_revision": 1,
            "projected_at": "2026-08-16T10:00:00+08:00",
            "source_authority": {
                "run_record_sha256": "5" * 64,
                "guard_report_sha256": None,
                "accepted_at_projection": "2026-08-16T10:00:00+08:00",
            },
        }
        revision = self.store.put_item_projection(
            record["batch_id"], 1, mapping["run_id"], projection
        )
        self.assertEqual(revision, 1)
        stored = self.store.get_item_projection(record["batch_id"], 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["projection_revision"], 1)

        # identical content replays the same revision
        revision = self.store.put_item_projection(
            record["batch_id"], 1, mapping["run_id"], projection
        )
        self.assertEqual(revision, 1)

        # changed content increments the revision
        changed = dict(projection)
        changed["blocker"] = "cookie expired"
        changed["run_state"] = dict(projection["run_state"])
        changed["run_state"]["source_blocker"] = "cookie expired"
        revision = self.store.put_item_projection(
            record["batch_id"], 1, mapping["run_id"], changed
        )
        self.assertEqual(revision, 2)
        stored = self.store.get_item_projection(record["batch_id"], 1)
        batch_record = self.store.get_batch_record(record["batch_id"])
        self.assertEqual(len(batch_record["projections"]), 1)
        mirror = batch_record["projections"][0]
        self.assertEqual(mirror["item_index"], 1)
        self.assertEqual(mirror["run_id"], mapping["run_id"])
        self.assertEqual(mirror["projection_revision"], 2)
        self.assertEqual(mirror["item_projection"], stored)
        self.assertEqual(
            mirror["projection_sha256"],
            hashlib.sha256(canonical_json_bytes(stored)).hexdigest(),
        )

    def test_partial_v11_batch_migration_fails_closed(self) -> None:
        # A partial v11 migration means the batch tables exist while the
        # ledger still reports version 10: reopening must fail closed.
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("DELETE FROM schema_migrations WHERE version=11")
        with self.assertRaisesRegex(
            ControlStoreUnavailable,
            "partial v11 Batch migration",
        ):
            ControlStore(self.workspace, ContractRegistry(PROJECT_ROOT))

    def test_v10_store_migrates_to_v11(self) -> None:
        # Simulate a real v10 store: remove the v11 tables and ledger row.
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("DROP TABLE batch_item_projections")
            connection.execute("DROP TABLE batch_records")
            connection.execute("DELETE FROM schema_migrations WHERE version=11")
        migrated = ControlStore(self.workspace, ContractRegistry(PROJECT_ROOT))
        self.assertEqual(migrated.check().schema_version, 11)
        with sqlite3.connect(self.store.path) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("batch_records", tables)
        self.assertIn("batch_item_projections", tables)

    def test_check_passes_with_batch_tables(self) -> None:
        health = self.store.check()
        self.assertEqual(health.status, "ok")
        self.assertEqual(health.schema_version, 11)


if __name__ == "__main__":
    unittest.main()

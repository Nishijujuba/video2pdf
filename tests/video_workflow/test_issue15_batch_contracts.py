from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.errors import ContractError


REGISTRY = ContractRegistry(PROJECT_ROOT)
RUN_TASK_START = "2026-08-16T10:00:00+08:00"


def _expected_run_id(
    platform: str, canonical_item_id: str, run_task_start: str, request_id: str
) -> str:
    return hashlib.sha256(
        "\0".join(
            (platform, canonical_item_id, run_task_start, request_id)
        ).encode("utf-8")
    ).hexdigest()[:32]


def _planned_record(**overrides: object) -> dict:
    record = {
        "schema_name": "batch-record",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "batch_id": "a" * 32,
        "batch_identity": {
            "kind": "url_set",
            "canonical_platform": "bilibili",
            "batch_source_identity": "b" * 64,
            "source_url": "https://example.com/playlist",
            "original_title": "Batch Title",
            "task_start": "2026-08-16T09:05:00+08:00",
            "request_id": "request-one",
        },
        "output_root": "D:/workspace",
        "batch_dir": "D:/workspace/batch/batch-control",
        "control_dir": "D:/workspace/.workflow-control/batches/batch",
        "batch_stage": "planned",
        "run_task_start": None,
        "item_order": [
            {
                "item_index": 1,
                "part_id": None,
                "canonical_item_id": "BV1xx411c7mD:p1",
                "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/",
                "title": "Part One",
                "selected": True,
            },
            {
                "item_index": 2,
                "part_id": None,
                "canonical_item_id": "BV1xx411c7mD:p2",
                "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/",
                "title": "Part Two",
                "selected": True,
            },
        ],
        "run_mappings": [],
        "projections": [],
        "created_at": "2026-08-16T09:05:00+08:00",
        "updated_at": "2026-08-16T09:05:00+08:00",
    }
    record.update(overrides)
    return record


def _running_record(**overrides: object) -> dict:
    record = _planned_record(batch_stage="running", run_task_start=RUN_TASK_START)
    record["run_mappings"] = [
        {
            "item_index": index,
            "run_id": _expected_run_id(
                "bilibili",
                record["item_order"][index - 1]["canonical_item_id"],
                RUN_TASK_START,
                f"{'a' * 32}:{index}",
            ),
            "request_id": f"{'a' * 32}:{index}",
        }
        for index in (1, 2)
    ]
    record.update(overrides)
    return record


def _projection(**overrides: object) -> dict:
    projection = {
        "schema_name": "batch-item-projection",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "batch_id": "a" * 32,
        "item_index": 1,
        "run_id": "1" * 32,
        "run_state": {
            "phase": "source_acquisition",
            "source_state": "ready",
            "source_blocker": None,
            "coordination_revision": 3,
            "output_path": "D:/workspace/run",
            "delivery": {
                "stage": "delivered",
                "ownership": {"session_id": "session-one", "generation": 1},
            },
        },
        "checkpoint": {"name": "source_ready", "status": "current"},
        "blocker": None,
        "delivery_outcome": {
            "delivery_stage": "delivered",
            "guarded_delivered": True,
            "acceptance_report_sha256": "3" * 64,
            "guard_report_sha256": "4" * 64,
            "delivered_at": "2026-08-16T12:00:00+08:00",
        },
        "projection_revision": 2,
        "projected_at": "2026-08-16T12:00:00+08:00",
        "source_authority": {
            "run_record_sha256": "5" * 64,
            "guard_report_sha256": "4" * 64,
            "accepted_at_projection": "2026-08-16T12:00:00+08:00",
        },
    }
    projection.update(overrides)
    return projection


class Issue15BatchContractTests(unittest.TestCase):
    def test_batch_record_and_projection_registered_in_registry(self) -> None:
        entries = {entry.schema_name: entry for entry in REGISTRY.entries}
        self.assertIn("batch-record", entries)
        self.assertEqual(entries["batch-record"].schema_version, "1.0.0")
        self.assertEqual(entries["batch-record"].kind, "contract")
        self.assertIn("batch-item-projection", entries)
        self.assertEqual(entries["batch-item-projection"].schema_version, "1.0.0")
        self.assertEqual(entries["batch-item-projection"].kind, "contract")

    def test_committed_positive_fixtures_validate(self) -> None:
        for name in ("batch-record", "batch-item-projection"):
            with self.subTest(name=name):
                path = (
                    PROJECT_ROOT
                    / "tests"
                    / "video_workflow"
                    / "fixtures"
                    / "contracts"
                    / f"{name}.valid.json"
                )
                value = json.loads(path.read_text(encoding="utf-8"))
                REGISTRY.validate(name, value)

    def test_committed_negative_fixtures_reject(self) -> None:
        for name in ("batch-record", "batch-item-projection"):
            with self.subTest(name=name):
                path = (
                    PROJECT_ROOT
                    / "tests"
                    / "video_workflow"
                    / "fixtures"
                    / "contracts"
                    / f"{name}.invalid.json"
                )
                value = json.loads(path.read_text(encoding="utf-8"))
                if name == "batch-record":
                    # scenario_id: duplicate_canonical_item_identity
                    # target_invariant: canonical_item_id uniqueness
                    # mutation_seam: planned item_order[1].canonical_item_id
                    # rematerialized_nodes: none; planned graph has no mappings/projections
                    # intentionally_stale_nodes: none
                    # expected_first_gate: batch-record-v1 invariant
                    # expected_error_code: unavailable; stable message fragment is temporary
                    # scenario_class: single_contradiction
                    with self.assertRaisesRegex(
                        ContractError,
                        "Batch item canonical identities must be unique",
                    ):
                        REGISTRY.validate(name, value)
                else:
                    with self.assertRaises(ContractError):
                        REGISTRY.validate(name, value)

    def test_planned_record_with_selection_validates(self) -> None:
        # The pinned design requires a planned record to carry its selection
        # without any run mappings yet.
        REGISTRY.validate("batch-record", _planned_record())

    def test_planned_record_may_bind_run_task_start_before_commit(self) -> None:
        REGISTRY.validate(
            "batch-record", _planned_record(run_task_start=RUN_TASK_START)
        )

    def test_running_record_requires_bound_run_task_start(self) -> None:
        record = _running_record(run_task_start=None)
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_mapping_request_id_must_bind_batch_and_item(self) -> None:
        record = _running_record()
        record["run_mappings"][0]["request_id"] = "wrong:1"
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_mapping_run_id_must_match_kernel_formula(self) -> None:
        record = _running_record()
        record["run_mappings"][0]["run_id"] = "f" * 32
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_item_canonical_identities_are_unique(self) -> None:
        record = _planned_record()
        record["item_order"][1]["canonical_item_id"] = record["item_order"][0][
            "canonical_item_id"
        ]
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_mapping_run_and_request_identities_are_unique(self) -> None:
        record = _running_record()
        record["run_mappings"][1]["run_id"] = record["run_mappings"][0]["run_id"]
        record["run_mappings"][1]["request_id"] = record["run_mappings"][0][
            "request_id"
        ]
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_planned_record_with_mappings_rejected(self) -> None:
        record = _planned_record()
        record["run_mappings"] = [
            {"item_index": 1, "run_id": "1" * 32, "request_id": "batch:1"}
        ]
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_planned_record_with_projections_rejected(self) -> None:
        record = _planned_record()
        record["projections"] = [_projection()]
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_gapped_item_order_rejected(self) -> None:
        record = _planned_record()
        record["item_order"][1]["item_index"] = 3
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_duplicate_item_order_index_rejected(self) -> None:
        record = _planned_record()
        record["item_order"][1]["item_index"] = 1
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_running_mappings_must_cover_selected_items(self) -> None:
        record = _running_record()
        record["run_mappings"] = [
            {"item_index": 1, "run_id": "1" * 32, "request_id": "batch:1"}
        ]
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_running_mapping_unknown_item_index_rejected(self) -> None:
        record = _running_record()
        record["run_mappings"] = [
            {"item_index": 1, "run_id": "1" * 32, "request_id": "batch:1"},
            {"item_index": 9, "run_id": "2" * 32, "request_id": "batch:9"},
        ]
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_projection_unknown_item_index_rejected(self) -> None:
        record = _running_record()
        record["projections"] = [_projection(item_index=9)]
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-record", record)

    def test_guarded_delivered_requires_delivered_stage(self) -> None:
        projection = _projection()
        projection["delivery_outcome"]["delivery_stage"] = "generating"
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-item-projection", projection)

    def test_guarded_delivered_requires_guard_report_sha(self) -> None:
        projection = _projection()
        projection["delivery_outcome"]["guard_report_sha256"] = None
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-item-projection", projection)

    def test_guarded_delivered_requires_run_record_authority(self) -> None:
        projection = _projection()
        projection["source_authority"]["run_record_sha256"] = None
        with self.assertRaises(ContractError):
            REGISTRY.validate("batch-item-projection", projection)

    def test_unguarded_projection_validates(self) -> None:
        projection = _projection()
        projection["delivery_outcome"]["guarded_delivered"] = False
        projection["delivery_outcome"]["delivery_stage"] = "generating"
        projection["delivery_outcome"]["guard_report_sha256"] = None
        projection["delivery_outcome"]["delivered_at"] = None
        projection["source_authority"]["guard_report_sha256"] = None
        REGISTRY.validate("batch-item-projection", projection)


if __name__ == "__main__":
    unittest.main()

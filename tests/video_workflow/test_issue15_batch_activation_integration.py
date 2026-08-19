from __future__ import annotations

import copy
import hashlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_workflow_workspace
from video2pdf_workflow_kernel.batch_projection import BatchProjectionProvider
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore
from video2pdf_workflow_kernel.errors import ControlStoreUnavailable, KernelConflict


TASK_START = "2026-08-19T09:00:00+08:00"
RUN_TASK_START = "2026-08-19T10:00:00+08:00"


def _authority(*, generation: int = 1, sha: str = "a" * 64) -> dict:
    return {
        "authority_path": "D:/control/active-batch-cutover.json",
        "authority_sha256": sha,
        "exit_evidence_sha256": "b" * 64,
        "generation": generation,
        "publication_commit": "c" * 40,
        "global_gate_binding": {
            "authority_path": "D:/control/active-global-gate.json",
            "authority_sha256": "d" * 64,
            "generation": 1,
        },
        "platform_authority_bindings": {
            "bilibili": {
                "platform": "bilibili",
                "authority_path": "D:/control/active-bilibili-platform.json",
                "authority_sha256": "e" * 64,
                "generation": 1,
            },
            "youtube": {
                "platform": "youtube",
                "authority_path": "D:/control/active-youtube-platform.json",
                "authority_sha256": "f" * 64,
                "generation": 1,
            },
        },
        "current": True,
    }


def _binding(authority: dict) -> dict:
    return {
        key: authority[key]
        for key in (
            "authority_path",
            "authority_sha256",
            "exit_evidence_sha256",
            "generation",
            "publication_commit",
        )
    }


def _record(workspace: Path, binding: dict | None) -> dict:
    batch_id = "a" * 32
    return {
        "schema_name": "batch-record",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "batch_id": batch_id,
        "batch_identity": {
            "kind": "url_set",
            "canonical_platform": "bilibili",
            "batch_source_identity": "1" * 64,
            "source_url": "https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
            "original_title": "Authority batch",
            "task_start": TASK_START,
            "request_id": "issue-15-authority",
        },
        "output_root": str(workspace.resolve()),
        "batch_dir": str((workspace / "authority-batch" / "batch-control").resolve()),
        "control_dir": str(
            (workspace / ".workflow-control" / "batches" / batch_id).resolve()
        ),
        "batch_stage": "planned",
        "batch_authority_binding": binding,
        "run_task_start": None,
        "item_order": [
            {
                "item_index": 1,
                "part_id": "p1",
                "canonical_item_id": "BV1xx411c7mD:p1",
                "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
                "title": "Part one",
                "selected": True,
            }
        ],
        "run_mappings": [],
        "projections": [],
        "created_at": TASK_START,
        "updated_at": TASK_START,
    }


class Issue15BatchActivationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = new_workflow_workspace(
            self.id(), label="batch-activation-integration"
        )
        self.contracts = ContractRegistry(PROJECT_ROOT)

    def test_plan_requires_authority_before_enumeration_or_mutation(self) -> None:
        publisher = Mock()
        publisher.require_current.side_effect = KernelConflict("missing Batch authority")
        provider = BatchProjectionProvider(batch_authority_publisher=publisher)

        with patch.object(provider, "_enumerate_items") as enumerate_items:
            with self.assertRaisesRegex(KernelConflict, "missing Batch authority"):
                provider.plan(
                    self.workspace,
                    self.contracts,
                    platform="bilibili",
                    source_url=None,
                    url_set="https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
                    task_start=TASK_START,
                    request_id="issue-15-no-authority",
                    control_store_root=self.workspace,
                )

        enumerate_items.assert_not_called()
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_plan_rechecks_after_enumeration_before_directory_or_database_mutation(self) -> None:
        current = _authority()
        publisher = Mock()
        publisher.require_current.side_effect = [current, _authority(generation=2)]
        provider = BatchProjectionProvider(batch_authority_publisher=publisher)

        with self.assertRaisesRegex(KernelConflict, "changed during Batch planning"):
            provider.plan(
                self.workspace,
                self.contracts,
                platform="bilibili",
                source_url=None,
                url_set="https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
                task_start=TASK_START,
                request_id="issue-15-drift",
                control_store_root=self.workspace,
            )

        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_plan_records_exact_current_authority_binding(self) -> None:
        current = _authority()
        publisher = Mock()
        publisher.require_current.return_value = current
        provider = BatchProjectionProvider(batch_authority_publisher=publisher)

        result = provider.plan(
            self.workspace,
            self.contracts,
            platform="bilibili",
            source_url=None,
            url_set="https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
            task_start=TASK_START,
            request_id="issue-15-current",
            control_store_root=self.workspace,
        )

        record = ControlStore.initialize(self.workspace, self.contracts).get_batch_record(
            result["batch_id"]
        )
        self.assertEqual(record["batch_authority_binding"], _binding(current))
        self.assertEqual(publisher.require_current.call_count, 2)

    def test_plan_replay_rejects_a_record_bound_to_an_older_authority(self) -> None:
        publisher = Mock()
        publisher.require_current.return_value = _authority()
        provider = BatchProjectionProvider(batch_authority_publisher=publisher)
        arguments = {
            "platform": "bilibili",
            "source_url": None,
            "url_set": "https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
            "task_start": TASK_START,
            "request_id": "issue-15-replay-drift",
            "control_store_root": self.workspace,
        }
        provider.plan(self.workspace, self.contracts, **arguments)
        publisher.require_current.return_value = _authority(generation=2)

        with self.assertRaises(KernelConflict):
            provider.plan(self.workspace, self.contracts, **arguments)

    def test_run_rejects_missing_or_stale_binding_before_any_mutation(self) -> None:
        for label, binding in (
            ("missing", None),
            ("stale", _binding(_authority(sha="9" * 64))),
        ):
            with self.subTest(label=label):
                workspace = self.workspace / label
                workspace.mkdir()
                store = ControlStore.initialize(workspace, self.contracts)
                record = _record(workspace, binding)
                store.create_batch_record(record, record["batch_identity"])
                before = copy.deepcopy(store.get_batch_record(record["batch_id"]))
                publisher = Mock()
                publisher.require_current.return_value = _authority()
                provider = BatchProjectionProvider(batch_authority_publisher=publisher)

                with patch.object(provider, "_kernel") as kernel:
                    with self.assertRaisesRegex(KernelConflict, "authority binding"):
                        provider.run(
                            workspace,
                            self.contracts,
                            batch_id=record["batch_id"],
                            control_store_root=workspace,
                            session_id="session-issue-15",
                            run_task_start=RUN_TASK_START,
                        )

                kernel.assert_not_called()
                self.assertEqual(store.get_batch_record(record["batch_id"]), before)

    def test_run_accepts_the_exact_current_binding(self) -> None:
        current = _authority()
        store = ControlStore.initialize(self.workspace, self.contracts)
        record = _record(self.workspace, _binding(current))
        store.create_batch_record(record, record["batch_identity"])
        publisher = Mock()
        publisher.require_current.return_value = current
        provider = BatchProjectionProvider(batch_authority_publisher=publisher)
        run_id = provider._derive_run_id(
            "bilibili",
            "BV1xx411c7mD:p1",
            RUN_TASK_START,
            f"{record['batch_id']}:1",
        )

        class FakeKernel:
            def initialize_production_source(inner_self, probe, **kwargs):
                run_dir = self.workspace / "runs" / probe.run_id
                run_dir.mkdir(parents=True)
                store.bind_run(
                    run_id=probe.run_id,
                    output_path=run_dir,
                    initialization_intent_id=f"initialize-{probe.run_id}",
                )
                return SimpleNamespace(run_id=probe.run_id, run_dir=run_dir)

        with patch.object(provider, "_kernel", return_value=FakeKernel()), patch.object(
            provider,
            "_bootstrap_probe",
            return_value=SimpleNamespace(run_id=run_id),
        ), patch.object(
            provider,
            "_submit_first_admitted_task",
            return_value=SimpleNamespace(queue_state="admitted"),
        ):
            result = provider.run(
                self.workspace,
                self.contracts,
                batch_id=record["batch_id"],
                control_store_root=self.workspace,
                session_id="session-issue-15",
                run_task_start=RUN_TASK_START,
            )

        stored = store.get_batch_record(record["batch_id"])
        self.assertEqual(stored["run_task_start"], RUN_TASK_START)
        self.assertEqual(stored["run_mappings"][0]["run_id"], run_id)
        self.assertEqual(result["items"][0]["stage"], "admitted")

    def test_recover_requires_current_authority_before_store_initialization(self) -> None:
        control_root = self.workspace / "missing-control"
        publisher = Mock()
        publisher.require_current.side_effect = KernelConflict("missing Batch authority")
        provider = BatchProjectionProvider(batch_authority_publisher=publisher)

        with self.assertRaisesRegex(KernelConflict, "missing Batch authority"):
            provider.recover(
                self.workspace / "outputs",
                self.contracts,
                batch_id="a" * 32,
                control_store_root=control_root,
            )

        self.assertFalse(control_root.exists())

    def test_recover_rejects_stale_record_binding_before_run_store_mutation(self) -> None:
        control_root = self.workspace / "control-recover-stale"
        output_root = self.workspace / "outputs-recover-stale"
        control_root.mkdir()
        output_root.mkdir()
        store = ControlStore.initialize(control_root, self.contracts)
        record = _record(output_root, _binding(_authority(sha="9" * 64)))
        store.create_batch_record(record, record["batch_identity"])
        before = copy.deepcopy(store.get_batch_record(record["batch_id"]))
        publisher = Mock()
        publisher.require_current.return_value = _authority()
        provider = BatchProjectionProvider(batch_authority_publisher=publisher)

        with patch.object(provider, "_kernel") as kernel:
            with self.assertRaisesRegex(KernelConflict, "authority binding"):
                provider.recover(
                    output_root,
                    self.contracts,
                    batch_id=record["batch_id"],
                    control_store_root=control_root,
                )

        kernel.assert_not_called()
        self.assertEqual(before, store.get_batch_record(record["batch_id"]))
        self.assertFalse(ControlStore.identity_evidence_exists(output_root))

    def test_unmapped_status_and_rebuild_do_not_create_run_store(self) -> None:
        output_root = self.workspace / "outputs-readonly"
        control_root = self.workspace / "control-readonly"
        output_root.mkdir()
        control_root.mkdir()
        publisher = Mock()
        publisher.require_current.return_value = _authority()
        provider = BatchProjectionProvider(batch_authority_publisher=publisher)
        planned = provider.plan(
            output_root,
            self.contracts,
            platform="bilibili",
            source_url=None,
            url_set="https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
            task_start=TASK_START,
            request_id="issue-15-readonly-missing-run-store",
            control_store_root=control_root,
        )

        status = provider.status(
            output_root,
            self.contracts,
            batch_id=planned["batch_id"],
            control_store_root=control_root,
        )
        projections = provider.rebuild_projections(
            output_root,
            self.contracts,
            batch_id=planned["batch_id"],
            control_store_root=control_root,
        )

        self.assertEqual(status["batch_stage"], "planned")
        self.assertEqual(len(status["items"]), 1)
        self.assertIsNone(status["items"][0]["run_id"])
        self.assertFalse(status["items"][0]["guarded_delivered"])
        self.assertEqual(projections, [])
        self.assertFalse(ControlStore.identity_evidence_exists(output_root))

    def test_mapped_status_and_rebuild_fail_closed_without_run_store(self) -> None:
        output_root = self.workspace / "outputs-readonly-mapped"
        control_root = self.workspace / "control-readonly-mapped"
        output_root.mkdir()
        control_root.mkdir()
        store = ControlStore.initialize(control_root, self.contracts)
        record = _record(output_root, _binding(_authority()))
        record["run_task_start"] = RUN_TASK_START
        record["batch_stage"] = "running"
        request_id = f"{record['batch_id']}:1"
        run_id = hashlib.sha256(
            "\0".join(
                (
                    "bilibili",
                    "BV1xx411c7mD:p1",
                    RUN_TASK_START,
                    request_id,
                )
            ).encode("utf-8")
        ).hexdigest()[:32]
        record["run_mappings"] = [
            {
                "item_index": 1,
                "run_id": run_id,
                "request_id": request_id,
            }
        ]
        store.create_batch_record(record, record["batch_identity"])
        provider = BatchProjectionProvider(
            batch_authority_publisher=Mock(require_current=Mock(return_value=_authority()))
        )

        for operation in (
            lambda: provider.status(
                output_root,
                self.contracts,
                batch_id=record["batch_id"],
                control_store_root=control_root,
            ),
            lambda: provider.rebuild_projections(
                output_root,
                self.contracts,
                batch_id=record["batch_id"],
                control_store_root=control_root,
            ),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ControlStoreUnavailable):
                    operation()
                self.assertFalse(ControlStore.identity_evidence_exists(output_root))

    def test_distinct_output_and_control_roots_plan_and_run_the_same_batch(self) -> None:
        output_root = self.workspace / "outputs"
        control_root = self.workspace / "control"
        output_root.mkdir()
        control_root.mkdir()
        current = _authority()
        publisher = Mock()
        publisher.require_current.return_value = current
        provider = BatchProjectionProvider(batch_authority_publisher=publisher)

        planned = provider.plan(
            output_root,
            self.contracts,
            platform="bilibili",
            source_url=None,
            url_set="https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
            task_start=TASK_START,
            request_id="issue-15-distinct-roots",
            control_store_root=control_root,
        )
        batch_store = ControlStore.initialize(control_root, self.contracts)
        record = batch_store.get_batch_record(planned["batch_id"])
        self.assertIsNotNone(record)
        self.assertTrue(Path(record["batch_dir"]).is_relative_to(output_root))
        self.assertIsNone(
            ControlStore.initialize(output_root, self.contracts).get_batch_record(
                planned["batch_id"]
            )
        )

        run_id = provider._derive_run_id(
            "bilibili",
            "BV1xx411c7mD:p1",
            RUN_TASK_START,
            f"{planned['batch_id']}:1",
        )
        run_store = ControlStore(output_root, self.contracts)

        class FakeKernel:
            def initialize_production_source(inner_self, probe, **kwargs):
                run_dir = output_root / "runs" / probe.run_id
                run_dir.mkdir(parents=True)
                run_store.bind_run(
                    run_id=probe.run_id,
                    output_path=run_dir,
                    initialization_intent_id=f"initialize-{probe.run_id}",
                )
                return SimpleNamespace(run_id=probe.run_id, run_dir=run_dir)

        with patch.object(provider, "_kernel", return_value=FakeKernel()), patch.object(
            provider,
            "_bootstrap_probe",
            return_value=SimpleNamespace(run_id=run_id),
        ), patch.object(
            provider,
            "_submit_first_admitted_task",
            return_value=SimpleNamespace(queue_state="admitted"),
        ):
            provider.run(
                output_root,
                self.contracts,
                batch_id=planned["batch_id"],
                control_store_root=control_root,
                session_id="session-distinct-roots",
                run_task_start=RUN_TASK_START,
            )

        stored = batch_store.get_batch_record(planned["batch_id"])
        self.assertEqual(run_id, stored["run_mappings"][0]["run_id"])


if __name__ == "__main__":
    unittest.main()

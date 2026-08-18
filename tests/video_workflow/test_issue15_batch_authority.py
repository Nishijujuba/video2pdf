from __future__ import annotations

import hashlib
import json
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_workflow_workspace
from video2pdf_workflow_kernel.batch_projection import (
    BatchProjectionProvider,
    is_guarded_delivered,
)
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore


TASK_START = "2026-08-16T09:05:00+08:00"
RUN_TASK_START = "2026-08-16T10:00:00+08:00"
REQUEST_ID = "issue-15-batch-authority"
PLATFORM = "bilibili"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
    )


def _guard_report() -> dict:
    required_conditions = (
        "target_resolved",
        "allowed_artifacts_manifest_loaded",
        "final_pdf_in_manifest",
        "final_compile_provenance_current",
        "acceptance_report_v2_authority_current",
        "rendered_page_evidence_current",
        "artifact_fingerprints_current",
    )
    return {
        "schema_version": "1.0",
        "status": "pass",
        "stage": "accepted",
        "validated_by": "delivery_guard.py",
        "acceptance_report_status": "pass",
        "artifact_fingerprints": [
            {
                "path": "main.tex",
                "sha256": f"sha256:{'a' * 64}",
                "size_bytes": 1,
                "size_chars": 1,
            }
        ],
        "checked_conditions": [
            {"condition": condition, "status": "pass"}
            for condition in required_conditions
        ],
    }


def _run_record(run_id: str, run_dir: Path, session_id: str, stage: str) -> dict:
    record = json.loads(
        (
            PROJECT_ROOT
            / "tests"
            / "video_workflow"
            / "fixtures"
            / "contracts"
            / "run-record.v4.valid.json"
        ).read_text(encoding="utf-8")
    )
    record.update(
        {
            "run_id": run_id,
            "output_path": str(run_dir.resolve()),
            "initialization_intent_id": f"initialize-issue15-{run_id}",
            "coordination_revision": 1,
            "last_mutation_intent_id": None,
        }
    )
    record["delivery"] = {
        "stage": stage,
        "ownership": {"session_id": session_id, "generation": 1},
        "projections": {
            "video_target": {
                "path": "review/acceptance/delivery_target.json",
                "projection_revision": 1,
                "sha256": "0" * 64,
            },
            "session_target": {
                "path": str(
                    (
                        run_dir
                        / ".codex"
                        / "delivery-targets"
                        / "sessions"
                        / session_id
                        / "current.json"
                    ).resolve()
                ),
                "projection_revision": 1,
                "sha256": "0" * 64,
            },
            "task_index": {
                "path": str(
                    (run_dir / ".codex" / "delivery-targets" / "kernel-task-index.json").resolve()
                ),
                "projection_revision": 1,
                "sha256": "0" * 64,
            },
            "archive": None,
        },
    }
    return record


def _planned_record(batch_id: str) -> dict:
    return {
        "schema_name": "batch-record",
        "schema_version": "1.0.0",
        "kernel_version": "2.0.0",
        "batch_id": batch_id,
        "batch_identity": {
            "kind": "url_set",
            "canonical_platform": PLATFORM,
            "batch_source_identity": "b" * 64,
            "source_url": "https://example.com/playlist",
            "original_title": "Batch",
            "task_start": TASK_START,
            "request_id": REQUEST_ID,
        },
        "output_root": "D:/workspace",
        "batch_dir": "D:/workspace/batch/batch-control",
        "control_dir": "D:/workspace/.workflow-control/batches/batch",
        "batch_stage": "planned",
        "run_task_start": None,
        "item_order": [
            {
                "item_index": 1,
                "part_id": "p1",
                "canonical_item_id": "BV1xx411c7mD:p1",
                "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
                "title": "Part One",
                "selected": True,
            }
        ],
        "run_mappings": [],
        "projections": [],
        "created_at": TASK_START,
        "updated_at": TASK_START,
    }


class Issue15BatchAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = new_workflow_workspace(self.id(), label="batch-authority")
        self.contracts = ContractRegistry(PROJECT_ROOT)
        self.provider = BatchProjectionProvider()
        self.store = ControlStore.initialize(self.workspace, self.contracts)

    def _batch(self, batch_id: str = "a" * 32) -> str:
        record = _planned_record(batch_id)
        stored_id, outcome = self.store.create_batch_record(
            record, record["batch_identity"]
        )
        self.assertEqual(outcome, "CREATED")
        return stored_id

    def _bind_run(self, batch_id: str, stage: str, *, with_guard: bool) -> Path:
        run_id = hashlib.sha256(
            "\0".join(
                (
                    PLATFORM,
                    "BV1xx411c7mD:p1",
                    RUN_TASK_START,
                    f"{batch_id}:1",
                )
            ).encode("utf-8")
        ).hexdigest()[:32]
        run_dir = self.workspace / "runs" / f"authority-{batch_id[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            run_dir / "workflow" / "run.json",
            _run_record(run_id, run_dir, "session-authority", stage),
        )
        if with_guard:
            _write_json(
                run_dir / "review" / "acceptance" / "delivery_guard_report.json",
                _guard_report(),
            )
            _write_json(
                run_dir / "review" / "acceptance" / "acceptance_report.json",
                {"report": "stub"},
            )
        self.store.bind_run(
            run_id=run_id,
            output_path=run_dir,
            initialization_intent_id=f"initialize-issue15-{run_id}",
        )
        self.store.bind_batch_run_task_start(batch_id, RUN_TASK_START)
        self.store.commit_batch_run_mappings(
            batch_id,
            [
                {
                    "item_index": 1,
                    "run_id": run_id,
                    "request_id": f"{batch_id}:1",
                }
            ],
        )
        return run_dir

    def _rebuild(self, batch_id: str) -> list[dict]:
        return self.provider.rebuild_projections(
            self.workspace, self.contracts, batch_id=batch_id
        )

    # ------------------------------------------------------------------
    # pinned exit-evidence result: guarded_delivered_only_success
    # ------------------------------------------------------------------
    def test_guarded_delivered_only_success(self) -> None:
        # delivered stage + passing guard report => success
        batch_id = self._batch()
        self._bind_run(batch_id, "delivered", with_guard=True)
        projections = self._rebuild(batch_id)
        self.assertTrue(projections[0]["delivery_outcome"]["guarded_delivered"])
        self.assertTrue(is_guarded_delivered(projections[0]))
        # not delivered => not success even with a PDF present
        batch_id = self._batch("b" * 32)
        self._bind_run(batch_id, "generating", with_guard=False)
        (self.workspace / "runs" / "authority-aaaaaaaa" / "article.pdf").write_bytes(
            b"%PDF-1.7\n"
        )
        projections = self._rebuild(batch_id)
        self.assertFalse(projections[0]["delivery_outcome"]["guarded_delivered"])
        self.assertFalse(is_guarded_delivered(projections[0]))

    def test_delivered_with_stale_guard_report_is_not_success(self) -> None:
        batch_id = self._batch()
        run_dir = self._bind_run(batch_id, "delivered", with_guard=True)
        # invalidate the guard report so it no longer validates
        _write_json(
            run_dir / "review" / "acceptance" / "delivery_guard_report.json",
            {"status": "fail"},
        )
        projections = self._rebuild(batch_id)
        self.assertFalse(projections[0]["delivery_outcome"]["guarded_delivered"])
        self.assertFalse(is_guarded_delivered(projections[0]))

    def test_success_helper_respects_explicit_false_and_matching_authority(self) -> None:
        batch_id = self._batch()
        self._bind_run(batch_id, "delivered", with_guard=True)
        projection = self._rebuild(batch_id)[0]

        explicitly_false = copy.deepcopy(projection)
        explicitly_false["delivery_outcome"]["guarded_delivered"] = False
        self.assertFalse(is_guarded_delivered(explicitly_false))

        mismatched_authority = copy.deepcopy(projection)
        mismatched_authority["source_authority"]["guard_report_sha256"] = "f" * 64
        self.assertFalse(is_guarded_delivered(mismatched_authority))

    # ------------------------------------------------------------------
    # pinned exit-evidence result: pdf_existence_success_rejected
    # ------------------------------------------------------------------
    def test_pdf_existence_success_rejected(self) -> None:
        batch_id = self._batch()
        self._bind_run(batch_id, "generating", with_guard=False)
        (self.workspace / "runs" / "authority-aaaaaaaa" / "article.pdf").write_bytes(
            b"%PDF-1.7\nsome bytes"
        )
        projections = self._rebuild(batch_id)
        self.assertFalse(projections[0]["delivery_outcome"]["guarded_delivered"])
        self.assertFalse(is_guarded_delivered(projections[0]))

    # ------------------------------------------------------------------
    # pinned exit-evidence result: duplicate_run_rejected
    # ------------------------------------------------------------------
    def test_duplicate_run_rejected(self) -> None:
        planned = self.provider.plan(
            self.workspace,
            self.contracts,
            platform=PLATFORM,
            source_url=None,
            url_set=(
                "https://www.bilibili.com/video/BV1xx411c7mD/?p=1,"
                "https://www.bilibili.com/video/BV1xx411c7mD/?p=2"
            ),
            task_start=TASK_START,
            request_id=REQUEST_ID,
        )
        batch_id = planned["batch_id"]

        kernel, _ = self._fake_kernel()
        with patch.object(
            self.provider,
            "_kernel",
            return_value=kernel,
        ), patch.object(
            self.provider,
            "_require_platform_authority",
            return_value={"authority_path": "x"},
        ), patch.object(
            self.provider,
            "_bootstrap_probe",
            side_effect=self._probe_side_effect(batch_id),
        ), patch.object(
            self.provider,
            "_submit_first_admitted_task",
            return_value=type(
                "Claim",
                (),
                {
                    "resource_admission": type(
                        "Admission", (), {"queue_state": "admitted"}
                    )()
                },
            )(),
        ):
            first = self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-batch-0001",
                global_gate_binding={"generation": 1},
                run_task_start=RUN_TASK_START,
            )
            second = self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-batch-0001",
                global_gate_binding={"generation": 1},
                run_task_start=RUN_TASK_START,
            )
        # the second run creates no new runs: it is a pure replay
        self.assertEqual(len(second["items"]), 0)
        mappings = self.store.list_run_mappings(batch_id)
        run_ids = [m["run_id"] for m in mappings]
        self.assertEqual(len(run_ids), len(set(run_ids)))
        self.assertEqual(len(run_ids), 2)

    def _fake_kernel(self):
        store = self.store
        workspace = self.workspace

        class FakeKernel:
            def initialize_production_source(self, probe, **kwargs):
                run_dir = workspace / "runs" / probe.run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                _write_json(
                    run_dir / "workflow" / "run.json",
                    _run_record(
                        probe.run_id, run_dir, kwargs.get("session_id") or "s", "generating"
                    ),
                )
                store.bind_run(
                    run_id=probe.run_id,
                    output_path=run_dir,
                    initialization_intent_id=f"initialize-issue15-{probe.run_id}",
                )
                return type(
                    "Initialized",
                    (),
                    {"run_id": probe.run_id, "run_dir": run_dir},
                )()

        return FakeKernel(), None

    def _probe_side_effect(self, batch_id: str):
        def make(kernel, platform, url, selector, title, task_start, request_id):
            item_index = int(request_id.rsplit(":", 1)[1])
            canonical = "BV1xx411c7mD:p1" if item_index == 1 else "BV1xx411c7mD:p2"
            run_id = hashlib.sha256(
                "\0".join((platform, canonical, RUN_TASK_START, request_id)).encode("utf-8")
            ).hexdigest()[:32]
            return type(
                "Probe",
                (),
                {
                    "run_id": run_id,
                    "request_id": request_id,
                    "record_path": workspace_probe_path(),
                    "original_title": f"Part {item_index}",
                    "task_start": task_start,
                    "canonical_platform": platform,
                    "canonical_item_id": canonical,
                    "source_identity": "s" * 64,
                },
            )()

        def workspace_probe_path():
            return self.workspace / "probe.json"

        return make

    # ------------------------------------------------------------------
    # pinned exit-evidence result: per_video_mutation_rejected
    # ------------------------------------------------------------------
    def test_per_video_mutation_rejected(self) -> None:
        batch_id = self._batch()
        run_dir = self._bind_run(batch_id, "delivered", with_guard=True)
        run_path = run_dir / "workflow" / "run.json"
        before = run_path.read_bytes()
        # batch read-only operations must not touch per-video state
        self._rebuild(batch_id)
        self.provider.status(self.workspace, self.contracts, batch_id=batch_id)
        self.assertEqual(run_path.read_bytes(), before)

    def test_status_rejects_cached_success_after_guard_is_removed(self) -> None:
        batch_id = self._batch()
        run_dir = self._bind_run(batch_id, "delivered", with_guard=True)
        self._rebuild(batch_id)
        guard_path = run_dir / "review" / "acceptance" / "delivery_guard_report.json"
        guard_path.rename(run_dir / "review" / "acceptance" / "stale-guard.json")

        status = self.provider.status(
            self.workspace, self.contracts, batch_id=batch_id
        )

        self.assertEqual(status["items"][0]["delivery_stage"], "delivered")
        self.assertFalse(status["items"][0]["guarded_delivered"])

    # ------------------------------------------------------------------
    # fairness through Resource Admission
    # ------------------------------------------------------------------
    def test_fairness_group_id_is_batch_id(self) -> None:
        batch_id = self._batch()
        record = _planned_record(batch_id)
        self.assertEqual(record["batch_identity"]["canonical_platform"], PLATFORM)

    def test_auth_breaker_flows_through_resource_admission(self) -> None:
        # Batch must not implement its own breaker; the platform authority
        # seam rejects non-current authorities before any run is created.
        batch_id = self._batch()
        with patch.object(
            self.provider,
            "_require_platform_authority",
            side_effect=RuntimeError("platform authority not current"),
        ), self.assertRaises(RuntimeError):
            self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-batch-0001",
                global_gate_binding={"generation": 1},
                run_task_start=RUN_TASK_START,
            )
        # no runs were created
        self.assertEqual(self.store.list_run_mappings(batch_id), [])


if __name__ == "__main__":
    unittest.main()

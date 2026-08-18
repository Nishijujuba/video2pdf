from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_workflow_workspace
from video2pdf_workflow_kernel.batch_projection import BatchProjectionProvider
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore
from video2pdf_workflow_kernel.errors import ContractError, KernelConflict


TASK_START = "2026-08-16T09:05:00+08:00"
RUN_TASK_START = "2026-08-16T10:00:00+08:00"
REQUEST_ID = "issue-15-batch-projection"
PLATFORM = "bilibili"
ITEM_1 = "BV1xx411c7mD:p1"
ITEM_2 = "BV1xx411c7mD:p2"
URL = "https://www.bilibili.com/video/BV1xx411c7mD/"


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


def _run_record(
    run_id: str,
    run_dir: Path,
    session_id: str,
    delivery_stage: str = "delivered",
) -> dict:
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
        "stage": delivery_stage,
        "ownership": {"session_id": session_id, "generation": 1},
        "projections": {
            "video_target": {
                "path": "review/acceptance/delivery_target.json",
                "projection_revision": 1,
                "sha256": "0" * 64,
            },
            "session_target": {
                "path": str(
                    (run_dir / ".codex" / "delivery-targets" / "sessions" / session_id / "current.json").resolve()
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


class Issue15BatchProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = new_workflow_workspace(self.id(), label="batch")
        self.contracts = ContractRegistry(PROJECT_ROOT)
        self.provider = BatchProjectionProvider()
        self.store = ControlStore.initialize(self.workspace, self.contracts)

    def _plan(self, selection: list[object] | None = None) -> dict:
        return self.provider.plan(
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
            selection=selection,
        )

    def _expected_run_id(self, batch_id: str, item_index: int) -> str:
        request_id = f"{batch_id}:{item_index}"
        return hashlib.sha256(
            "\0".join(
                (PLATFORM, ITEM_1 if item_index == 1 else ITEM_2, RUN_TASK_START, request_id)
            ).encode("utf-8")
        ).hexdigest()[:32]

    def _bind_delivered_run(
        self, batch_id: str, item_index: int, *, with_guard: bool = True
    ) -> tuple[str, Path]:
        run_id = self._expected_run_id(batch_id, item_index)
        run_dir = self.workspace / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        _write_json(run_dir / "workflow" / "run.json", _run_record(run_id, run_dir, f"session-{item_index}"))
        if with_guard:
            _write_json(run_dir / "review" / "acceptance" / "delivery_guard_report.json", _guard_report())
            _write_json(run_dir / "review" / "acceptance" / "acceptance_report.json", {"report": "stub"})
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
                    "item_index": item_index,
                    "run_id": run_id,
                    "request_id": f"{batch_id}:{item_index}",
                }
            ],
        )
        return run_id, run_dir

    # ------------------------------------------------------------------
    # pinned exit-evidence result: batch_record_contract_pass
    # ------------------------------------------------------------------
    def test_batch_record_contract_pass(self) -> None:
        result = self._plan()
        self.assertEqual(result["created_or_replayed"], "CREATED")
        record = self.store.get_batch_record(result["batch_id"])
        self.assertIsNotNone(record)
        self.assertEqual(record["batch_stage"], "planned")
        self.assertEqual(record["run_mappings"], [])
        self.contracts.validate("batch-record", record)
        # plan must not create any Runs
        bindings = self.store.list_run_mappings(result["batch_id"])
        self.assertEqual(bindings, [])

    def test_plan_is_idempotent_and_writes_no_runs(self) -> None:
        first = self._plan()
        second = self._plan()
        self.assertEqual(first["batch_id"], second["batch_id"])
        self.assertEqual(second["created_or_replayed"], "REPLAY")
        record = self.store.get_batch_record(first["batch_id"])
        self.assertEqual(record["batch_stage"], "planned")
        self.assertEqual(record["run_mappings"], [])

    def test_source_enumeration_failure_does_not_create_batch_record(self) -> None:
        with patch(
            "video2pdf_workflow_kernel.batch_projection._flat_playlist",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                ContractError, "batch source enumeration failed"
            ):
                self.provider.plan(
                    self.workspace,
                    self.contracts,
                    platform=PLATFORM,
                    source_url=URL,
                    url_set=None,
                    task_start=TASK_START,
                    request_id=f"{REQUEST_ID}-enumeration-failure",
                )
        self.assertEqual(self.store.list_batch_records(), [])

    def test_invalid_url_set_item_does_not_create_batch_record(self) -> None:
        with self.assertRaisesRegex(
            ContractError, "Bilibili batch item URL is invalid"
        ):
            self.provider.plan(
                self.workspace,
                self.contracts,
                platform=PLATFORM,
                source_url=None,
                url_set="https://example.com/not-bilibili",
                task_start=TASK_START,
                request_id=f"{REQUEST_ID}-invalid-url-set",
            )
        self.assertEqual(self.store.list_batch_records(), [])

    # ------------------------------------------------------------------
    # deterministic run id
    # ------------------------------------------------------------------
    def _fake_kernel(
        self, fail_first: bool = False, fail_after_second_initialization: bool = False
    ):
        """A minimal kernel stub that commits a valid run per initialized item.

        The real ``initialize_production_source`` writes ``workflow/run.json``
        and binds the run in the Control Store; the stub mirrors exactly those
        two side effects so ``record_run_mapping`` and projection rebuilds see
        authoritative Run state.
        """

        store = self.store
        workspace = self.workspace
        contracts = self.contracts
        state = {"calls": 0}

        class FakeKernel:
            def bootstrap_production_source(self, **kwargs):  # pragma: no cover
                raise AssertionError("bootstrap_production_source must be mocked")

            def initialize_production_source(
                self,
                probe,
                *,
                session_id=None,
                global_gate_binding=None,
                fault_point=None,
            ):
                state["calls"] += 1
                if fail_first and state["calls"] == 1:
                    from video2pdf_workflow_kernel.errors import InitializationFault

                    raise InitializationFault("before_run_record_commit")
                run_dir = workspace / "runs" / probe.run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                _write_json(
                    run_dir / "workflow" / "run.json",
                    _run_record(
                        probe.run_id, run_dir, session_id or "session-item"
                    ),
                )
                store.bind_run(
                    run_id=probe.run_id,
                    output_path=run_dir,
                    initialization_intent_id=f"initialize-issue15-{probe.run_id}",
                )
                if fail_after_second_initialization and state["calls"] == 2:
                    from video2pdf_workflow_kernel.errors import InitializationFault

                    raise InitializationFault("after_second_run_record_commit")
                return type(
                    "Initialized",
                    (),
                    {"run_id": probe.run_id, "run_dir": run_dir},
                )()

            def reconcile_run(self, run_dir):
                return type(
                    "Reconciled", (), {"run_dir": run_dir, "outcome": "current"}
                )()

        return FakeKernel(), state

    def _probe_for(self, batch_id: str, item_index: int, run_id: str):
        item = ITEM_1 if item_index == 1 else ITEM_2
        return type(
            "Probe",
            (),
            {
                "run_id": run_id,
                "request_id": f"{batch_id}:{item_index}",
                "record_path": self.workspace / f"probe-{item_index}.json",
                "original_title": f"Part {item_index}",
                "task_start": RUN_TASK_START,
                "canonical_platform": PLATFORM,
                "canonical_item_id": item,
                "source_identity": "s" * 64,
            },
        )()

    def _run_batch(
        self,
        batch_id: str,
        fail_first: bool = False,
        fail_after_second_initialization: bool = False,
        run_task_start: str = RUN_TASK_START,
    ) -> dict:
        kernel, state = self._fake_kernel(
            fail_first=fail_first,
            fail_after_second_initialization=fail_after_second_initialization,
        )
        with patch.object(
            self.provider,
            "_kernel",
            return_value=kernel,
        ), patch.object(
            self.provider,
            "_require_platform_authority",
            return_value={"authority_path": str(self.workspace / "authority.json")},
        ), patch.object(
            self.provider,
            "_bootstrap_probe",
            side_effect=lambda kernel, platform, url, selector, title, task_start, request_id: (
                self._probe_for(
                    batch_id,
                    int(request_id.rsplit(":", 1)[1]),
                    self._expected_run_id(
                        batch_id, int(request_id.rsplit(":", 1)[1])
                    ),
                )
            ),
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
            return self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-batch-0001",
                global_gate_binding={"generation": 1},
                run_task_start=run_task_start,
            )

    def test_batch_run_deterministic_run_id(self) -> None:
        planned = self._plan()
        batch_id = planned["batch_id"]
        result = self._run_batch(batch_id)
        self.assertEqual(result["batch_id"], batch_id)
        self.assertEqual(len(result["items"]), 2)
        for item in result["items"]:
            self.assertEqual(
                item["run_id"], self._expected_run_id(batch_id, item["item_index"])
            )
        mappings = self.store.list_run_mappings(batch_id)
        self.assertEqual(len(mappings), 2)

    # ------------------------------------------------------------------
    # pinned exit-evidence result: projection_rebuild_pass
    # ------------------------------------------------------------------
    def test_projection_rebuild_pass(self) -> None:
        planned = self._plan(selection=[1])
        batch_id = planned["batch_id"]
        run_id, run_dir = self._bind_delivered_run(batch_id, 1)
        projections = self.provider.rebuild_projections(
            self.workspace, self.contracts, batch_id=batch_id
        )
        self.assertEqual(len(projections), 1)
        projection = projections[0]
        self.assertEqual(projection["run_id"], run_id)
        self.assertEqual(projection["delivery_outcome"]["delivery_stage"], "delivered")
        self.assertTrue(projection["delivery_outcome"]["guarded_delivered"])
        self.assertEqual(projection["source_authority"]["run_record_sha256"], hashlib.sha256((run_dir / "workflow/run.json").read_bytes()).hexdigest())
        # rebuild is idempotent at the same revision
        projections = self.provider.rebuild_projections(
            self.workspace, self.contracts, batch_id=batch_id
        )
        self.assertEqual(len(projections), 1)
        self.assertEqual(projections[0]["projection_revision"], 1)
        # roll-up: every selected item guarded-delivered => completed
        record = self.store.get_batch_record(batch_id)
        self.assertEqual(record["batch_stage"], "completed")

    def test_projection_rebuild_without_guard_is_not_delivered(self) -> None:
        planned = self._plan(selection=[1])
        batch_id = planned["batch_id"]
        self._bind_delivered_run(batch_id, 1, with_guard=False)
        projections = self.provider.rebuild_projections(
            self.workspace, self.contracts, batch_id=batch_id
        )
        projection = projections[0]
        self.assertFalse(projection["delivery_outcome"]["guarded_delivered"])
        self.assertIsNone(projection["delivery_outcome"]["guard_report_sha256"])

    # ------------------------------------------------------------------
    # pinned exit-evidence result: reconcile_interrupted_item_creation
    # ------------------------------------------------------------------
    def test_reconcile_interrupted_item_creation(self) -> None:
        planned = self._plan()
        batch_id = planned["batch_id"]
        run_id = self._expected_run_id(batch_id, 1)

        from video2pdf_workflow_kernel.errors import InitializationFault

        # First attempt is interrupted mid-initialization (item 1), leaving
        # no binding; the second attempt must converge to the same run_id.
        with self.assertRaises(InitializationFault):
            self._run_batch(batch_id, fail_first=True)
        result = self._run_batch(batch_id)
        item_1 = next(item for item in result["items"] if item["item_index"] == 1)
        self.assertEqual(item_1["run_id"], run_id)
        # item 1 maps exactly once despite the interrupted attempt
        mappings = self.store.list_run_mappings(batch_id)
        mapped = [m for m in mappings if m["item_index"] == 1]
        self.assertEqual(len(mapped), 1)
        self.assertEqual(mapped[0]["run_id"], run_id)
        # recover reconciles and rebuilds projections
        with patch.object(
            self.provider,
            "_reconcile_one",
            side_effect=lambda kernel, store, run_id: type(
                "R",
                (),
                {
                    "run_dir": self.workspace / "runs" / run_id,
                    "outcome": "reconciled",
                },
            )(),
        ):
            recovered = self.provider.recover(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
            )
        self.assertEqual(len(recovered["reconciled"]), 2)
        self.assertEqual(len(recovered["projections"]), 2)

    def test_second_item_initialization_interruption_keeps_batch_record_valid(self) -> None:
        planned = self._plan()
        batch_id = planned["batch_id"]

        from video2pdf_workflow_kernel.errors import InitializationFault

        with self.assertRaises(InitializationFault):
            self._run_batch(batch_id, fail_after_second_initialization=True)

        interrupted = self.store.get_batch_record(batch_id)
        self.assertEqual(interrupted["batch_stage"], "planned")
        self.assertEqual(interrupted["run_mappings"], [])
        self.contracts.validate("batch-record", interrupted)

        resumed = self._run_batch(batch_id)
        self.assertEqual(len(resumed["items"]), 2)
        recovered = self.store.get_batch_record(batch_id)
        self.assertEqual(recovered["batch_stage"], "running")
        self.assertEqual(
            [mapping["item_index"] for mapping in recovered["run_mappings"]],
            [1, 2],
        )
        self.contracts.validate("batch-record", recovered)

    # ------------------------------------------------------------------
    # pinned exit-evidence result: projection_revision_fencing
    # ------------------------------------------------------------------
    def test_projection_revision_fencing(self) -> None:
        planned = self._plan(selection=[1])
        batch_id = planned["batch_id"]
        self._bind_delivered_run(batch_id, 1)
        projections = self.provider.rebuild_projections(
            self.workspace, self.contracts, batch_id=batch_id
        )
        self.assertEqual(projections[0]["projection_revision"], 1)
        # same content replays the same revision
        projections = self.provider.rebuild_projections(
            self.workspace, self.contracts, batch_id=batch_id
        )
        self.assertEqual(projections[0]["projection_revision"], 1)
        # changed content advances the revision and never reuses an old one
        run_dir = self.workspace / "runs" / self._expected_run_id(batch_id, 1)
        record = json.loads((run_dir / "workflow/run.json").read_text(encoding="utf-8"))
        record["coordination_revision"] = 2
        _write_json(run_dir / "workflow/run.json", record)
        projections = self.provider.rebuild_projections(
            self.workspace, self.contracts, batch_id=batch_id
        )
        revisions = [p["projection_revision"] for p in projections]
        self.assertEqual(len(revisions), len(set(revisions)))
        self.assertGreaterEqual(projections[0]["projection_revision"], 2)

    def test_unknown_batch_id_rejected(self) -> None:
        with self.assertRaises(KernelConflict):
            self.provider.status(
                self.workspace, self.contracts, batch_id="f" * 32
            )

    def test_status_reports_projection_summaries(self) -> None:
        planned = self._plan(selection=[1])
        batch_id = planned["batch_id"]
        self._bind_delivered_run(batch_id, 1)
        self.provider.rebuild_projections(
            self.workspace, self.contracts, batch_id=batch_id
        )
        status = self.provider.status(
            self.workspace, self.contracts, batch_id=batch_id
        )
        self.assertEqual(status["batch_id"], batch_id)
        self.assertEqual(len(status["items"]), 1)
        item = status["items"][0]
        self.assertEqual(item["delivery_stage"], "delivered")
        self.assertTrue(item["guarded_delivered"])


if __name__ == "__main__":
    unittest.main()

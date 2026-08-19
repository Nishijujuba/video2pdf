from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_workflow_workspace
from tests.video_workflow._batch_authority import CurrentBatchAuthorityPublisher
from video2pdf_workflow_kernel.batch_projection import BatchProjectionProvider
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore
from video2pdf_workflow_kernel.errors import (
    ContractError,
    InitializationFault,
    KernelConflict,
)


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_fingerprint(path: Path, relative_path: str) -> dict:
    raw = path.read_bytes()
    try:
        size_chars = len(raw.decode("utf-8"))
    except UnicodeDecodeError:
        size_chars = None
    return {
        "path": relative_path,
        "sha256": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "size_bytes": len(raw),
        "size_chars": size_chars,
    }


def _guard_report(run_dir: Path, artifact_paths: list[Path]) -> dict:
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
            _artifact_fingerprint(path, path.relative_to(run_dir).as_posix())
            for path in artifact_paths
        ],
        "checked_conditions": [
            {"condition": condition, "status": "pass"}
            for condition in required_conditions
        ],
    }


def _guard_fingerprints(target, manifest: dict) -> list[dict]:
    paths = [
        target.main_tex,
        target.final_pdf,
        target.manifest_path,
        target.acceptance_report_path,
        target.compile_report_path,
        *(
            target.video_output_dir / item["path"]
            for item in manifest["final_artifacts"]
        ),
    ]
    seen: set[Path] = set()
    return [
        _artifact_fingerprint(path, path.relative_to(target.video_output_dir).as_posix())
        for path in paths
        if not (path in seen or seen.add(path))
    ]


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
        self.provider = BatchProjectionProvider(
            batch_authority_publisher=CurrentBatchAuthorityPublisher()
        )
        self.store = ControlStore.initialize(self.workspace, self.contracts)
        guard_loader = patch(
            "video2pdf_workflow_kernel.guarded_delivery._load_active_delivery_guard",
            return_value=SimpleNamespace(
                resolve_delivery_target=self._resolve_delivered_target,
                guard_report_is_fresh=lambda _target: False,
                compute_artifact_fingerprint=_artifact_fingerprint,
                guard_fingerprints=_guard_fingerprints,
            ),
        )
        guard_loader.start()
        self.addCleanup(guard_loader.stop)

    @staticmethod
    def _resolve_delivered_target(*, project_root, current_target_path, **_kwargs):
        del project_root
        session = json.loads(current_target_path.read_text(encoding="utf-8"))
        video_path = Path(session["video_target"]["path"]).resolve()
        video = json.loads(video_path.read_text(encoding="utf-8"))
        run_dir = Path(video["video_output_dir"]).resolve()
        artifacts = video["artifacts"]
        resolved = {
            role: Path(artifacts[role]["path"]).resolve()
            for role in (
                "final_pdf",
                "main_tex",
                "final_compile_report",
                "acceptance_report",
                "delivery_guard_report",
            )
        }
        return SimpleNamespace(
            video_output_dir=run_dir,
            current_target_path=current_target_path.resolve(),
            target_file=video_path,
            stage=video["stage"],
            final_pdf=resolved["final_pdf"],
            main_tex=resolved["main_tex"],
            compile_report_path=resolved["final_compile_report"],
            acceptance_report_path=resolved["acceptance_report"],
            guard_report_path=resolved["delivery_guard_report"],
            final_pdf_relative=resolved["final_pdf"].relative_to(run_dir).as_posix(),
            main_tex_relative=resolved["main_tex"].relative_to(run_dir).as_posix(),
            compile_report_relative=resolved["final_compile_report"].relative_to(run_dir).as_posix(),
            acceptance_report_relative=resolved["acceptance_report"].relative_to(run_dir).as_posix(),
            manifest_path=run_dir / "review" / "acceptance" / "allowed_artifacts_manifest.json",
        )

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
            control_store_root=self.workspace,
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
        record = _run_record(run_id, run_dir, f"session-{item_index}")
        if with_guard:
            record["last_mutation_intent_id"] = "1" * 64
            guard_path = run_dir / "review" / "acceptance" / "delivery_guard_report.json"
            acceptance_path = run_dir / "review" / "acceptance" / "acceptance_report.json"
            _write_json(acceptance_path, {"report": "stub"})
            final_pdf = run_dir / "article.pdf"
            final_pdf.write_bytes(b"%PDF-1.7\n")
            main_tex = run_dir / "main.tex"
            main_tex.write_text("\\documentclass{article}\n", encoding="utf-8")
            compile_report = run_dir / "build" / "final-compile-report.json"
            _write_json(compile_report, {"status": "pass"})
            rendered_page = (
                run_dir
                / "review"
                / "acceptance"
                / "rendered_pages"
                / "page_0001.png"
            )
            rendered_page.parent.mkdir(parents=True, exist_ok=True)
            rendered_page.write_bytes(b"rendered-page-before-guard")
            allowed_manifest = (
                run_dir
                / "review"
                / "acceptance"
                / "allowed_artifacts_manifest.json"
            )
            _write_json(
                allowed_manifest,
                {
                    "final_artifacts": [
                        {"path": "article.pdf"},
                        {
                            "path": "review/acceptance/rendered_pages/page_0001.png"
                        },
                    ]
                },
            )
            _write_json(
                guard_path,
                _guard_report(
                    run_dir,
                    [
                        main_tex,
                        final_pdf,
                        allowed_manifest,
                        acceptance_path,
                        compile_report,
                        rendered_page,
                    ],
                ),
            )
            target_path = run_dir / "review" / "acceptance" / "delivery_target.json"
            target = {
                "schema_name": "kernel-delivery-target",
                "schema_version": "1.0.0",
                "projection_kind": "video_target",
                "projection_revision": 1,
                "run_id": run_id,
                "run_revision": record["coordination_revision"],
                "lifecycle_intent_id": record["last_mutation_intent_id"],
                "video_output_dir": str(run_dir.resolve()),
                "stage": "delivered",
                "ownership": record["delivery"]["ownership"],
                "artifacts": {
                    "final_pdf": {"path": str(final_pdf.resolve()), "sha256": _sha256(final_pdf)},
                    "main_tex": {"path": str(main_tex.resolve()), "sha256": _sha256(main_tex)},
                    "final_compile_report": {"path": str(compile_report.resolve()), "sha256": _sha256(compile_report)},
                    "acceptance_report": {"path": str(acceptance_path.resolve()), "sha256": _sha256(acceptance_path)},
                    "delivery_guard_report": {"path": str(guard_path.resolve()), "sha256": _sha256(guard_path)},
                },
                "global_gate_authority": {
                    "path": str((self.workspace / "active-global-gate.json").resolve()),
                    "generation": 1,
                    "sha256": "2" * 64,
                },
            }
            self.contracts.validate("kernel-delivery-target", target)
            _write_json(target_path, target)
            record["delivery"]["projections"]["video_target"] = {
                "path": "review/acceptance/delivery_target.json",
                "projection_revision": target["projection_revision"],
                "sha256": _sha256(target_path),
            }
            session_path = Path(
                record["delivery"]["projections"]["session_target"]["path"]
            )
            session_target = {
                "schema_name": "kernel-session-delivery-target",
                "schema_version": "1.0.0",
                "projection_kind": "session_target",
                "projection_revision": 1,
                "projection_path": str(session_path.resolve()),
                "session_id": f"session-{item_index}",
                "run_id": run_id,
                "run_revision": record["coordination_revision"],
                "lifecycle_intent_id": record["last_mutation_intent_id"],
                "stage": "delivered",
                "ownership_generation": 1,
                "owner_status": "active",
                "video_output_dir": str(run_dir.resolve()),
                "video_target": {
                    "path": str(target_path.resolve()),
                    "projection_revision": target["projection_revision"],
                    "sha256": _sha256(target_path),
                },
            }
            _write_json(session_path, session_target)
            record["delivery"]["projections"]["session_target"] = {
                "path": str(session_path.resolve()),
                "projection_revision": 1,
                "sha256": _sha256(session_path),
            }
        _write_json(run_dir / "workflow" / "run.json", record)
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

    def test_batch_source_identity_covers_unselected_items_and_source_order(self) -> None:
        left_root = new_workflow_workspace(self.id(), label="batch-source-left")
        right_root = new_workflow_workspace(self.id(), label="batch-source-right")
        provider = BatchProjectionProvider(
            batch_authority_publisher=CurrentBatchAuthorityPublisher()
        )
        common = {
            "platform": PLATFORM,
            "source_url": None,
            "task_start": TASK_START,
            "request_id": f"{REQUEST_ID}-source-identity",
            "selection": [1],
        }
        left = provider.plan(
            left_root,
            self.contracts,
            url_set=(
                "https://www.bilibili.com/video/BV1xx411c7mD/?p=1,"
                "https://www.bilibili.com/video/BV1xx411c7mD/?p=2"
            ),
            control_store_root=left_root,
            **common,
        )
        right = provider.plan(
            right_root,
            self.contracts,
            url_set=(
                "https://www.bilibili.com/video/BV1xx411c7mD/?p=1,"
                "https://www.bilibili.com/video/BV1xx411c7mE/?p=2"
            ),
            control_store_root=right_root,
            **common,
        )
        left_record = ControlStore(left_root, self.contracts).get_batch_record(
            left["batch_id"]
        )
        right_record = ControlStore(right_root, self.contracts).get_batch_record(
            right["batch_id"]
        )

        self.assertNotEqual(
            left_record["batch_identity"]["batch_source_identity"],
            right_record["batch_identity"]["batch_source_identity"],
        )

    def test_plan_replay_rejects_changed_authoritative_item_order(self) -> None:
        first = self._plan(selection=[1])
        first_record = self.store.get_batch_record(first["batch_id"])
        changed_items = [dict(item) for item in first_record["item_order"]]
        changed_items[0]["title"] = "Changed title from a later enumeration"

        with patch.object(self.provider, "_enumerate_items", return_value=changed_items):
            with self.assertRaisesRegex(KernelConflict, "replay"):
                self._plan(selection=[1])

    def test_plan_replay_rematerializes_record_file_from_database_authority(self) -> None:
        with patch(
            "video2pdf_workflow_kernel.batch_projection.write_json_atomic",
            side_effect=OSError("injected DB-before-file interruption"),
        ):
            with self.assertRaisesRegex(OSError, "DB-before-file"):
                self._plan(selection=[1])

        stored = self.store.list_batch_records()[0]
        record_path = Path(stored["batch_dir"]) / "batch-record.json"
        self.assertFalse(record_path.exists())

        replayed = self._plan(selection=[1])

        self.assertEqual("REPLAY", replayed["created_or_replayed"])
        self.assertEqual(stored["batch_dir"], replayed["batch_dir"])
        self.assertEqual(stored["item_order"], replayed["item_order"])
        self.assertEqual(stored, json.loads(record_path.read_text(encoding="utf-8")))

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
                    control_store_root=self.workspace,
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
                control_store_root=self.workspace,
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
        two side effects so the atomic Batch mapping commit and projection
        rebuilds see authoritative Run state.
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
                "Admission",
                (),
                {"queue_state": "admitted"},
            )(),
        ):
            return self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-batch-0001",
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

    def test_new_session_recovers_claimed_run_before_mapping_commit(self) -> None:
        planned = self._plan(selection=[1])
        batch_id = planned["batch_id"]
        run_id = self._expected_run_id(batch_id, 1)
        gate_path = self.workspace / "active-global-gate.json"
        _write_json(gate_path, {"generation": 1, "status": "active"})
        gate_binding = {
            "authority_path": str(gate_path.resolve()),
            "authority_sha256": _sha256(gate_path),
            "generation": 1,
        }
        self.provider.batch_authority_publisher = CurrentBatchAuthorityPublisher(
            global_gate_binding=gate_binding
        )

        with self.assertRaises(InitializationFault):
            self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-original",
                run_task_start=RUN_TASK_START,
                fault_point="after_first_task_claim_before_mapping_commit",
            )

        interrupted = self.store.get_batch_record(batch_id)
        claims_before = self.store.active_task_claims()
        self.assertEqual([], interrupted["run_mappings"])
        self.assertEqual(1, len(claims_before))
        self.assertEqual(run_id, claims_before[0]["authority_id"])
        self.assertEqual("session-original-1", claims_before[0]["coordinator_session_id"])

        recovered = self.provider.run(
            self.workspace,
            self.contracts,
            batch_id=batch_id,
            control_store_root=self.workspace,
            session_id="session-recovery",
            run_task_start=RUN_TASK_START,
        )

        mappings = self.store.list_run_mappings(batch_id)
        claims_after = self.store.active_task_claims()
        self.assertEqual([run_id], [item["run_id"] for item in recovered["items"]])
        self.assertEqual([run_id], [item["run_id"] for item in mappings])
        self.assertEqual(1, len(claims_after))
        self.assertEqual(claims_before[0]["attempt_id"], claims_after[0]["attempt_id"])
        self.assertEqual(
            "session-original-1", claims_after[0]["coordinator_session_id"]
        )

    def test_batch_recover_discovers_claimed_run_before_mapping_commit(self) -> None:
        planned = self._plan(selection=[1])
        batch_id = planned["batch_id"]
        run_id = self._expected_run_id(batch_id, 1)
        gate_path = self.workspace / "active-global-gate.json"
        _write_json(gate_path, {"generation": 1, "status": "active"})
        self.provider.batch_authority_publisher = CurrentBatchAuthorityPublisher(
            global_gate_binding={
                "authority_path": str(gate_path.resolve()),
                "authority_sha256": _sha256(gate_path),
                "generation": 1,
            }
        )

        with self.assertRaises(InitializationFault):
            self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-original",
                run_task_start=RUN_TASK_START,
                fault_point="after_first_task_claim_before_mapping_commit",
            )

        recovered = self.provider.recover(
            self.workspace,
            self.contracts,
            batch_id=batch_id,
            control_store_root=self.workspace,
        )

        self.assertEqual([run_id], [item["run_id"] for item in recovered["reconciled"]])
        self.assertEqual(
            [run_id],
            [item["run_id"] for item in self.store.list_run_mappings(batch_id)],
        )

    def test_batch_recover_does_not_commit_partial_discovered_mappings(self) -> None:
        planned = self._plan()
        batch_id = planned["batch_id"]
        first_run_id = self._expected_run_id(batch_id, 1)
        gate_path = self.workspace / "active-global-gate.json"
        _write_json(gate_path, {"generation": 1, "status": "active"})
        self.provider.batch_authority_publisher = CurrentBatchAuthorityPublisher(
            global_gate_binding={
                "authority_path": str(gate_path.resolve()),
                "authority_sha256": _sha256(gate_path),
                "generation": 1,
            }
        )

        with self.assertRaises(InitializationFault):
            self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-original",
                run_task_start=RUN_TASK_START,
                fault_point="after_first_task_claim_before_mapping_commit",
            )

        recovered = self.provider.recover(
            self.workspace,
            self.contracts,
            batch_id=batch_id,
            control_store_root=self.workspace,
        )

        self.assertEqual(
            [first_run_id], [item["run_id"] for item in recovered["reconciled"]]
        )
        self.assertEqual([2], recovered["missing_item_indexes"])
        self.assertEqual([], self.store.list_run_mappings(batch_id))
        self.assertEqual([], recovered["projections"])

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

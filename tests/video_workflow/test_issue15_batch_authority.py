from __future__ import annotations

import hashlib
import json
import copy
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
from tests.video_workflow._batch_authority import (
    BATCH_AUTHORITY_BINDING,
    CurrentBatchAuthorityPublisher,
)
from video2pdf_workflow_kernel.batch_projection import (
    BatchProjectionProvider,
    is_guarded_delivered,
)
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel


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
        "batch_authority_binding": BATCH_AUTHORITY_BINDING,
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

    def _batch(self, batch_id: str = "a" * 32) -> str:
        record = _planned_record(batch_id)
        record["output_root"] = str(self.workspace.resolve())
        record["batch_dir"] = str(
            (self.workspace / "batch" / "batch-control").resolve()
        )
        record["control_dir"] = str(
            (
                self.workspace
                / ".workflow-control"
                / "batches"
                / batch_id
            ).resolve()
        )
        stored_id, outcome = self.store.create_batch_record(
            record, record["batch_identity"]
        )
        self.assertEqual(outcome, "CREATED")
        return stored_id

    def _run_real_batch_first_task(
        self,
        *,
        platform: str,
        source_url: str,
        request_id: str,
        task_start: str,
        run_task_start: str,
        session_id: str,
        kernel: VideoWorkflowKernel | None = None,
    ) -> tuple[str, dict, dict, object]:
        gate_path = self.workspace / "active-global-gate.json"
        if not gate_path.is_file():
            _write_json(gate_path, {"generation": 1, "status": "current"})
        provider = BatchProjectionProvider(
            batch_authority_publisher=CurrentBatchAuthorityPublisher(
                global_gate_binding={
                    "authority_path": str(gate_path.resolve()),
                    "authority_sha256": _sha256(gate_path),
                    "generation": 1,
                }
            )
        )
        planned = provider.plan(
            self.workspace,
            self.contracts,
            platform=platform,
            source_url=None,
            url_set=source_url,
            task_start=task_start,
            request_id=request_id,
            control_store_root=self.workspace,
        )
        active_kernel = kernel or VideoWorkflowKernel(self.workspace)
        with patch.object(provider, "_kernel", return_value=active_kernel):
            result = provider.run(
                self.workspace,
                self.contracts,
                batch_id=planned["batch_id"],
                control_store_root=self.workspace,
                session_id=session_id,
                run_task_start=run_task_start,
            )
        self.assertEqual(len(result["items"]), 1)
        run_dir = Path(result["items"][0]["run_dir"])
        task_path = next((run_dir / "workflow" / "tasks").glob("*/task.json"))
        envelope = json.loads(task_path.read_text(encoding="utf-8"))
        claim = active_kernel.control_store.task_claim_for_task(envelope["task_id"])
        self.assertIsNotNone(claim)
        admission = active_kernel.resource_status(
            envelope["task_id"], str(claim["attempt_id"])
        )
        return planned["batch_id"], result["items"][0], envelope, admission

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
        record = _run_record(run_id, run_dir, "session-authority", stage)
        if with_guard:
            acceptance_path = (
                run_dir / "review" / "acceptance" / "acceptance_report.json"
            )
            _write_json(acceptance_path, {"report": "stub"})
            if stage == "delivered":
                record["last_mutation_intent_id"] = "1" * 64
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
                guard_path = (
                    run_dir / "review" / "acceptance" / "delivery_guard_report.json"
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
                target_path = (
                    run_dir / "review" / "acceptance" / "delivery_target.json"
                )
                target = {
                    "schema_name": "kernel-delivery-target",
                    "schema_version": "1.0.0",
                    "projection_kind": "video_target",
                    "projection_revision": 1,
                    "run_id": run_id,
                    "run_revision": record["coordination_revision"],
                    "lifecycle_intent_id": "1" * 64,
                    "video_output_dir": str(run_dir.resolve()),
                    "stage": "delivered",
                    "ownership": record["delivery"]["ownership"],
                    "artifacts": {
                        "final_pdf": {
                            "path": str(final_pdf.resolve()),
                            "sha256": _sha256(final_pdf),
                        },
                        "main_tex": {
                            "path": str(main_tex.resolve()),
                            "sha256": _sha256(main_tex),
                        },
                        "final_compile_report": {
                            "path": str(compile_report.resolve()),
                            "sha256": _sha256(compile_report),
                        },
                        "acceptance_report": {
                            "path": str(acceptance_path.resolve()),
                            "sha256": _sha256(acceptance_path),
                        },
                        "delivery_guard_report": {
                            "path": str(guard_path.resolve()),
                            "sha256": _sha256(guard_path),
                        },
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
                    "session_id": "session-authority",
                    "run_id": run_id,
                    "run_revision": record["coordination_revision"],
                    "lifecycle_intent_id": "1" * 64,
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

    def test_delivered_with_foreign_guard_report_is_not_success(self) -> None:
        batch_id = self._batch()
        run_dir = self._bind_run(batch_id, "delivered", with_guard=True)
        foreign_guard = json.loads(
            (
                run_dir / "review" / "acceptance" / "delivery_guard_report.json"
            ).read_text(encoding="utf-8")
        )
        foreign_guard["artifact_fingerprints"][0]["path"] = "foreign-main.tex"
        _write_json(
            run_dir / "review" / "acceptance" / "delivery_guard_report.json",
            foreign_guard,
        )

        projections = self._rebuild(batch_id)

        self.assertFalse(projections[0]["delivery_outcome"]["guarded_delivered"])
        self.assertFalse(is_guarded_delivered(projections[0]))

    def test_delivered_with_stale_lifecycle_binding_is_not_success(self) -> None:
        batch_id = self._batch()
        run_dir = self._bind_run(batch_id, "delivered", with_guard=True)
        run_path = run_dir / "workflow" / "run.json"
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        run_record["delivery"]["projections"]["video_target"]["sha256"] = "f" * 64
        _write_json(run_path, run_record)

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
            control_store_root=self.workspace,
        )
        batch_id = planned["batch_id"]

        kernel, _ = self._fake_kernel()
        with patch.object(
            self.provider,
            "_kernel",
            return_value=kernel,
        ), patch.object(
            self.provider,
            "_bootstrap_probe",
            side_effect=self._probe_side_effect(batch_id),
        ), patch.object(
            self.provider,
            "_submit_first_admitted_task",
            return_value=type(
                "Admission",
                (),
                {"queue_state": "admitted"},
            )(),
        ):
            first = self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-batch-0001",
                run_task_start=RUN_TASK_START,
            )
            second = self.provider.run(
                self.workspace,
                self.contracts,
                batch_id=batch_id,
                control_store_root=self.workspace,
                session_id="session-batch-0001",
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

    def test_final_pdf_mutation_invalidates_guarded_delivered_success(self) -> None:
        batch_id = self._batch()
        run_dir = self._bind_run(batch_id, "delivered", with_guard=True)
        (run_dir / "article.pdf").write_bytes(b"%PDF-1.7\nmutated-after-guard\n")

        projections = self._rebuild(batch_id)

        self.assertFalse(projections[0]["delivery_outcome"]["guarded_delivered"])
        self.assertFalse(is_guarded_delivered(projections[0]))
        self.assertNotEqual(
            self.store.get_batch_record(batch_id)["batch_stage"], "completed"
        )

    def test_rendered_page_mutation_invalidates_guarded_delivered_success(self) -> None:
        batch_id = self._batch()
        run_dir = self._bind_run(batch_id, "delivered", with_guard=True)
        rendered_page = (
            run_dir
            / "review"
            / "acceptance"
            / "rendered_pages"
            / "page_0001.png"
        )
        rendered_page.write_bytes(b"rendered-page-mutated-after-guard")

        projections = self._rebuild(batch_id)

        self.assertFalse(projections[0]["delivery_outcome"]["guarded_delivered"])
        self.assertFalse(is_guarded_delivered(projections[0]))
        self.assertNotEqual(
            self.store.get_batch_record(batch_id)["batch_stage"], "completed"
        )

    # ------------------------------------------------------------------
    # fairness through Resource Admission
    # ------------------------------------------------------------------
    def test_fairness_group_id_is_batch_id(self) -> None:
        batch_id, item, envelope, admission = self._run_real_batch_first_task(
            platform="bilibili",
            source_url="https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
            request_id="issue-15-fairness-envelope",
            task_start="2026-08-16T11:00:00+08:00",
            run_task_start="2026-08-16T11:01:00+08:00",
            session_id="session-issue-15-fairness",
        )
        self.assertEqual(envelope["batch_id"], batch_id)
        self.assertEqual(envelope["fairness_group_id"], batch_id)
        self.assertEqual(item["stage"], "admitted")
        self.assertEqual(admission.queue_state, "admitted")
        self.assertEqual(admission.batch_id, batch_id)
        self.assertEqual(admission.fairness_group_id, batch_id)

    def test_auth_breaker_flows_through_resource_admission(self) -> None:
        kernel = VideoWorkflowKernel(self.workspace)
        opened = kernel.set_resource_circuit_breaker(
            "youtube_download",
            state="open",
            reason="cookie rejected",
            platform="youtube",
        )
        self.assertEqual(opened["scope_kind"], "platform")
        self.assertEqual(opened["platform"], "youtube")

        batch_id, youtube_item, youtube_envelope, queued = (
            self._run_real_batch_first_task(
                platform="youtube",
                source_url="https://www.youtube.com/watch?v=yt-test-001",
                request_id="issue-15-breaker-youtube",
                task_start="2026-08-16T12:00:00+08:00",
                run_task_start="2026-08-16T12:01:00+08:00",
                session_id="session-issue-15-youtube",
                kernel=kernel,
            )
        )
        self.assertEqual(youtube_envelope["resource_request"], ["youtube_download"])
        self.assertEqual(youtube_item["stage"], "queued")
        self.assertEqual(queued.queue_state, "queued")
        self.assertEqual(queued.batch_id, batch_id)
        self.assertEqual(queued.fairness_group_id, batch_id)

        _, bilibili_item, bilibili_envelope, other_platform = (
            self._run_real_batch_first_task(
                platform="bilibili",
                source_url="https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
                request_id="issue-15-breaker-bilibili",
                task_start="2026-08-16T12:02:00+08:00",
                run_task_start="2026-08-16T12:03:00+08:00",
                session_id="session-issue-15-bilibili",
                kernel=kernel,
            )
        )
        self.assertEqual(bilibili_envelope["resource_request"], ["bilibili_download"])
        self.assertEqual(bilibili_item["stage"], "admitted")
        self.assertEqual(other_platform.queue_state, "admitted")

        semantic = kernel.trace_source_ready(
            fixture=PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer",
            task_start="2026-08-16T12:04:00+08:00",
            request_id="issue-15-breaker-semantic",
        )
        semantic_task = kernel.prepare_source_acquisition_task(
            semantic.run_dir,
            logical_task_key="issue-15-semantic-task",
            prepared_at="2026-08-16T12:05:00+08:00",
            required_resources=("codex_semantic",),
            batch_id="c" * 32,
        )
        other_resource = kernel.claim_task(
            semantic.run_dir,
            semantic_task.task_id,
            coordinator_session_id="session-issue-15-semantic",
            worker_id="worker-issue-15-semantic",
        )
        self.assertEqual(other_resource.resource_admission.queue_state, "admitted")


if __name__ == "__main__":
    unittest.main()

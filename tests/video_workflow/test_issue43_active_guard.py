from __future__ import annotations

import hashlib
import gc
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
import uuid
from unittest import mock

import fitz

from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow import test_acceptance_v2 as acceptance_v2_tests
from tests.video_workflow.test_issue43_global_gate import Issue43GlobalGateTests

PROJECT_ROOT = acceptance_v2_tests.PROJECT_ROOT
file_sha = acceptance_v2_tests.file_sha
run_cli = acceptance_v2_tests.run_cli
write_json = acceptance_v2_tests.write_json

SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore
from video2pdf_workflow_kernel.utils import (
    canonical_json_bytes,
    normalized_physical_path,
)


GUARD = PROJECT_ROOT / ".agents/skills/final-delivery-acceptance/scripts/delivery_guard.py"
SOURCE_WRAPPER = PROJECT_ROOT / ".agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py"
GUARD_SCRIPTS = GUARD.parent
if str(GUARD_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(GUARD_SCRIPTS))

from validate_acceptance_report import create_allowed_artifacts_manifest


class Issue43ActiveGuardTests(unittest.TestCase):
    """The active Guard consumes only committed Acceptance Report v2 authority."""

    refresh_final_authority = acceptance_v2_tests.AcceptanceV2CliTests.refresh_final_authority
    patch = acceptance_v2_tests.AcceptanceV2CliTests.patch
    commit_visual = acceptance_v2_tests.AcceptanceV2CliTests.commit_visual
    materialize = acceptance_v2_tests.AcceptanceV2CliTests.materialize

    def ensure_run_authority(self, root: Path) -> tuple[dict, Path, Path]:
        run_path = root / "workflow/run.json"
        control_root = root / "control-store"
        record = json.loads(
            (PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/run-record.v3.valid.json").read_text(
                encoding="utf-8"
            )
        )
        record["schema_version"] = "4.0.0"
        record["run_id"] = hashlib.md5(str(root).encode()).hexdigest()
        record["output_path"] = str(root.resolve())
        record["initialization_intent_id"] = f"acceptance-fixture-{record['run_id']}"
        record["coordination_revision"] = 1
        record["delivery"] = {
            "stage": "ready_for_delivery",
            "ownership": {"session_id": "acceptance-fixture", "generation": 1},
            "projections": {
                "video_target": {
                    "path": "review/acceptance/delivery_target.json",
                    "projection_revision": 1,
                    "sha256": "b" * 64,
                },
                "session_target": {
                    "path": str(
                        (
                            root.parent
                            / ".codex/delivery-targets/sessions/acceptance-fixture/current.json"
                        ).resolve()
                    ),
                    "projection_revision": 1,
                    "sha256": "c" * 64,
                },
                "task_index": {
                    "path": str(
                        (root.parent / ".codex/delivery-targets/task-index.json").resolve()
                    ),
                    "projection_revision": 1,
                    "sha256": "d" * 64,
                },
                "archive": None,
            },
        }
        write_json(run_path, record)
        digest = file_sha(run_path)
        store = ControlStore.initialize(control_root, ContractRegistry(PROJECT_ROOT))
        store.prepare_initialization(
            run_id=record["run_id"],
            output_path=root,
            intent_id=record["initialization_intent_id"],
            staging_path=control_root / "staging" / record["run_id"],
        )
        store.bind_publication_expectations(
            record["initialization_intent_id"],
            expected_run_record_sha256=digest,
            canonical_platform=record["canonical_platform"],
            canonical_item_id=record["canonical_item_id"],
            source_identity=record["source_identity"],
            source_manifest_sha256="f" * 64,
        )
        for expected, new in (
            ("PREPARED", "PUBLISHED"),
            ("PUBLISHED", "RECORD_COMMITTED"),
            ("RECORD_COMMITTED", "COMMITTED"),
        ):
            store.transition_intent(
                record["initialization_intent_id"],
                expected_state=expected,
                new_state=new,
                run_record_sha256=digest,
            )
        return record, run_path, control_root

    @staticmethod
    def _valid_pdf_bytes() -> bytes:
        document = fitz.open()
        for page_number in (1, 2):
            page = document.new_page(width=300, height=300)
            page.insert_text((72, 72), f"Page {page_number}")
        value = document.tobytes()
        document.close()
        return value

    def build_binding(self, root: Path, generation: int, **kwargs: object) -> Path:
        original_write_bytes = Path.write_bytes
        pdf_bytes = self._valid_pdf_bytes()

        def write_fixture_bytes(path: Path, data: bytes) -> int:
            if path.name == "final.pdf" and data == b"pdf":
                data = pdf_bytes
            return original_write_bytes(path, data)

        with mock.patch.object(Path, "write_bytes", new=write_fixture_bytes):
            binding_path = acceptance_v2_tests.AcceptanceV2CliTests.build_binding(
                self, root, generation, **kwargs
            )
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        final_pdf = Path(next(
            item["path"] for item in binding["artifacts"]
            if item["logical_id"] == "final_pdf"
        ))
        main_tex = Path(next(
            item["path"] for item in binding["artifacts"]
            if item["logical_id"] == "main_tex"
        ))
        create_allowed_artifacts_manifest(
            root,
            PROJECT_ROOT / "docs/acceptance/acceptance_criteria.v1.json",
            [
                ("tex", main_tex.relative_to(root).as_posix()),
                ("pdf", final_pdf.relative_to(root).as_posix()),
            ],
        )
        return binding_path

    @staticmethod
    def _fingerprint(path: Path) -> dict[str, object]:
        raw = path.read_bytes()
        return {"algorithm": "sha256", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}

    def setUp(self) -> None:
        self.project_root = new_case_dir(self.id(), label="issue43-active-guard")
        self.wrapper = self.project_root / ".agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py"
        self.wrapper.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_WRAPPER, self.wrapper)
        self.video_root = self.project_root / "workspace" / "video"
        self.workspace = self.video_root / "review/acceptance"
        binding_path = self.build_binding(self.video_root, 1)
        prepared, envelope = run_cli(
            "acceptance-prepare",
            "--workspace-root",
            str(self.workspace),
            "--input-binding",
            str(binding_path),
            "--attempt-number",
            "1",
            "--prepared-at",
            "2026-08-03T00:00:00Z",
            "--coordinator-session",
            "coordinator-session",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        self.commit_visual(self.workspace)
        materialized, envelope = self.materialize(self.workspace)
        self.assertEqual(0, materialized.returncode, materialized.stdout + materialized.stderr)
        self.binding = json.loads((self.workspace / "input-binding.json").read_text(encoding="utf-8"))
        self.final_pdf = Path(next(item["path"] for item in self.binding["artifacts"] if item["logical_id"] == "final_pdf"))
        self.main_tex = Path(next(item["path"] for item in self.binding["artifacts"] if item["logical_id"] == "main_tex"))
        rendered_dir = self.workspace / "rendered_pages"
        rendered_dir.mkdir(parents=True, exist_ok=True)
        for item in self.binding["rendered_pages"]:
            shutil.copy2(item["path"], rendered_dir / f"page_{item['page']:04d}.png")
        self.manifest = create_allowed_artifacts_manifest(
            self.video_root,
            PROJECT_ROOT / "docs/acceptance/acceptance_criteria.v1.json",
            [
                ("tex", self.main_tex.relative_to(self.video_root).as_posix()),
                ("pdf", self.final_pdf.relative_to(self.video_root).as_posix()),
            ],
        )
        compile_report = self.video_root / "review/latex/compile_report.json"
        write_json(
            compile_report,
            {
                "schema_version": "latex_compile_report.v1",
                "mode": "final",
                "status": "passed",
                "producer": "compile_latex_ascii.py",
                "producer_contract": "latex_compile_guard.v1",
                "producer_mode": "final",
                "wrapper_script": str(self.wrapper.resolve()),
                "wrapper_script_fingerprint": self._fingerprint(self.wrapper),
                "argv": ["--mode", "final"],
                "source_tex": str(self.main_tex.resolve()),
                "main_tex": str(self.main_tex.resolve()),
                "final_pdf": str(self.final_pdf.resolve()),
                "source_tex_fingerprint": self._fingerprint(self.main_tex),
                "final_pdf_fingerprint": self._fingerprint(self.final_pdf),
            },
        )
        gate = self.binding["global_gate_authority"]
        self.target = write_json(
            self.workspace / "delivery_target.json",
            {
                "schema_version": "1.0",
                "stage": "accepted",
                "video_output_dir": ".",
                "final_pdf": self.final_pdf.relative_to(self.video_root).as_posix(),
                "main_tex": self.main_tex.relative_to(self.video_root).as_posix(),
                "allowed_artifacts_manifest": self.manifest.relative_to(self.video_root).as_posix(),
                "acceptance_report": "review/acceptance/acceptance_report.json",
                "delivery_guard_report": "review/acceptance/delivery_guard_report.json",
                "compile_report": compile_report.relative_to(self.video_root).as_posix(),
                "global_gate_authority": {
                    "path": Path(gate["path"]).relative_to(self.project_root).as_posix(),
                    "sha256": gate["file_sha256"],
                },
                "attempt_limit": 3,
            },
        )
        self.session_id = f"session-{uuid.uuid4().hex}"
        self.current = write_json(
            self.project_root / f".codex/delivery-targets/sessions/{self.session_id}/current.json",
            {
                "schema_version": "1.1",
                "scope": "session",
                "session_id": self.session_id,
                "turn_id": "turn-fixture",
                "observed_codex_thread_id": "thread-fixture",
                "stage": "accepted",
                "video_output_dir": self.video_root.relative_to(self.project_root).as_posix(),
                "target_file": self.target.relative_to(self.project_root).as_posix(),
                "source_skill": "test-fixture",
                "started_at": "2026-08-03T00:00:00Z",
                "updated_at": "2026-08-03T00:00:00Z",
            },
        )

    def run_guard(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(GUARD),
                "check",
                "--project-root",
                str(self.project_root),
                "--current-target",
                str(self.current),
            ],
            cwd=self.project_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def run_hook_stop(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(GUARD),
                "hook-stop",
                "--project-root",
                str(self.project_root),
                "--current-target",
                str(self.project_root / ".codex/delivery-targets/current.json"),
            ],
            cwd=self.project_root,
            input=json.dumps({"session_id": self.session_id}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def kernelize_bilibili_target(self) -> tuple[Path, Path]:
        session_target_path = self.current
        task_index_path = (
            self.project_root / ".codex" / "delivery-targets" / "task-index.json"
        )
        run_path = self.video_root / "workflow" / "run.json"
        intent_id = hashlib.sha256(
            f"kernel-guard:{self.id()}".encode("utf-8")
        ).hexdigest()
        predecessor = json.loads(run_path.read_text(encoding="utf-8"))
        predecessor_sha = file_sha(run_path)
        run_id = predecessor["run_id"]
        predecessor_revision = predecessor["coordination_revision"]
        successor_revision = predecessor_revision + 1
        legacy_target = json.loads(self.target.read_text(encoding="utf-8"))
        gate_path = (
            self.project_root / legacy_target["global_gate_authority"]["path"]
        ).resolve()
        artifact_paths = {
            "final_pdf": self.final_pdf,
            "main_tex": self.main_tex,
            "final_compile_report": self.video_root
            / legacy_target["compile_report"],
            "acceptance_report": self.workspace / "acceptance_report.json",
        }
        video_target = {
            "schema_name": "kernel-delivery-target",
            "schema_version": "1.0.0",
            "projection_kind": "video_target",
            "projection_revision": 2,
            "run_id": run_id,
            "run_revision": successor_revision,
            "lifecycle_intent_id": intent_id,
            "video_output_dir": str(self.video_root.resolve()),
            "stage": "accepted",
            "ownership": {"session_id": self.session_id, "generation": 1},
            "artifacts": {
                role: {"path": str(path.resolve()), "sha256": file_sha(path)}
                for role, path in artifact_paths.items()
            }
            | {"delivery_guard_report": None},
            "global_gate_authority": {
                "path": str(gate_path),
                "generation": 1,
                "sha256": file_sha(gate_path),
            },
        }
        write_json(self.target, video_target)
        session_target = {
            "schema_name": "kernel-session-delivery-target",
            "schema_version": "1.0.0",
            "projection_kind": "session_target",
            "projection_revision": 2,
            "projection_path": str(session_target_path.resolve()),
            "session_id": self.session_id,
            "run_id": run_id,
            "run_revision": successor_revision,
            "lifecycle_intent_id": intent_id,
            "stage": "accepted",
            "ownership_generation": 1,
            "owner_status": "active",
            "video_output_dir": str(self.video_root.resolve()),
            "video_target": {
                "path": str(self.target.resolve()),
                "projection_revision": 2,
                "sha256": file_sha(self.target),
            },
        }
        write_json(session_target_path, session_target)
        task_index = {
            "schema_name": "kernel-delivery-task-index",
            "schema_version": "1.0.0",
            "projection_kind": "task_index",
            "projection_revision": 2,
            "entries": [
                {
                    "run_id": run_id,
                    "canonical_platform": "bilibili",
                    "video_output_dir": str(self.video_root.resolve()),
                    "run_revision": successor_revision,
                    "lifecycle_intent_id": intent_id,
                    "stage": "accepted",
                    "session_id": self.session_id,
                    "ownership_generation": 1,
                    "video_target": {
                        "path": str(self.target.resolve()),
                        "projection_revision": 2,
                        "sha256": file_sha(self.target),
                    },
                    "session_target": {
                        "path": str(session_target_path.resolve()),
                        "projection_revision": 2,
                        "sha256": file_sha(session_target_path),
                    },
                    "archive": None,
                }
            ],
        }
        write_json(task_index_path, task_index)
        run_record = predecessor
        run_record.update(
            {
                "run_id": run_id,
                "platform_adapter": "bilibili",
                "canonical_platform": "bilibili",
                "output_path": str(self.video_root.resolve()),
                "coordination_revision": successor_revision,
                "last_mutation_intent_id": intent_id,
                "delivery": {
                    "stage": "accepted",
                    "ownership": {"session_id": self.session_id, "generation": 1},
                    "projections": {
                        "video_target": {
                            "path": "review/acceptance/delivery_target.json",
                            "projection_revision": 2,
                            "sha256": file_sha(self.target),
                        },
                        "session_target": {
                            "path": str(session_target_path.resolve()),
                            "projection_revision": 2,
                            "sha256": file_sha(session_target_path),
                        },
                        "task_index": {
                            "path": str(task_index_path.resolve()),
                            "projection_revision": 2,
                            "sha256": file_sha(task_index_path),
                        },
                        "archive": None,
                    },
                },
            }
        )
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_bytes(canonical_json_bytes(run_record))
        store = ControlStore(
            Path(self.binding["run"]["control_store_root"]),
            ContractRegistry(PROJECT_ROOT),
        )
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "INSERT INTO delivery_lifecycle_intents("
                "intent_id,run_id,session_id,expected_run_revision,"
                "expected_ownership_generation,prior_stage,target_stage,operation,"
                "prior_run_record_sha256,replacement_run_record_sha256,"
                "replacement_run_record_json,state,intent_identity) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    intent_id,
                    run_id,
                    self.session_id,
                    predecessor_revision,
                    1,
                    "ready_for_delivery",
                    "accepted",
                    "transition",
                    predecessor_sha,
                    file_sha(run_path),
                    canonical_json_bytes(run_record).decode("utf-8"),
                    "COMMITTED",
                    intent_id,
                ),
            )
            normalized_task_index = normalized_physical_path(task_index_path)
            slot_id = hashlib.sha256(
                (intent_id + "\0" + normalized_task_index).encode("utf-8")
            ).hexdigest()
            connection.execute(
                "INSERT INTO projection_publication_slots("
                "slot_id,intent_id,normalized_path,expected_state,expected_sha256,"
                "proposed_state,proposed_sha256,state,slot_identity) "
                "VALUES(?,?,?,'present',?,'present',?,'RELEASED',?)",
                (
                    slot_id,
                    intent_id,
                    normalized_task_index,
                    file_sha(task_index_path),
                    file_sha(task_index_path),
                    slot_id,
                ),
            )
        return store.path, task_index_path

    def publish_other_run_task_index_revision(
        self, store_path: Path, task_index_path: Path
    ) -> str:
        other_run_id = "24242424242424242424242424242424"
        other_session_id = "session-other-run"
        other_run_root = self.project_root / "workspace" / "other-video"
        other_target_path = (
            other_run_root / "review" / "acceptance" / "delivery_target.json"
        )
        other_session_path = (
            self.project_root
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / other_session_id
            / "current.json"
        )
        intent_id = hashlib.sha256(b"other-run-task-index-publication").hexdigest()

        own_target = json.loads(self.target.read_text(encoding="utf-8"))
        other_target = {
            **own_target,
            "run_id": other_run_id,
            "run_revision": 2,
            "lifecycle_intent_id": intent_id,
            "video_output_dir": str(other_run_root.resolve()),
            "ownership": {"session_id": other_session_id, "generation": 1},
        }
        write_json(other_target_path, other_target)
        own_session = json.loads(self.current.read_text(encoding="utf-8"))
        other_session = {
            **own_session,
            "projection_path": str(other_session_path.resolve()),
            "session_id": other_session_id,
            "run_id": other_run_id,
            "run_revision": 2,
            "lifecycle_intent_id": intent_id,
            "video_output_dir": str(other_run_root.resolve()),
            "video_target": {
                "path": str(other_target_path.resolve()),
                "projection_revision": other_target["projection_revision"],
                "sha256": file_sha(other_target_path),
            },
        }
        write_json(other_session_path, other_session)

        prior_index_sha = file_sha(task_index_path)
        task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        task_index["projection_revision"] += 1
        task_index["entries"] = sorted(
            [
                *task_index["entries"],
                {
                    "run_id": other_run_id,
                    "canonical_platform": "bilibili",
                    "video_output_dir": str(other_run_root.resolve()),
                    "run_revision": 2,
                    "lifecycle_intent_id": intent_id,
                    "stage": "accepted",
                    "session_id": other_session_id,
                    "ownership_generation": 1,
                    "video_target": {
                        "path": str(other_target_path.resolve()),
                        "projection_revision": other_target["projection_revision"],
                        "sha256": file_sha(other_target_path),
                    },
                    "session_target": {
                        "path": str(other_session_path.resolve()),
                        "projection_revision": other_session["projection_revision"],
                        "sha256": file_sha(other_session_path),
                    },
                    "archive": None,
                },
            ],
            key=lambda entry: entry["run_id"],
        )
        write_json(task_index_path, task_index)

        own_run = json.loads(
            (self.video_root / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        initialization_intent_id = "initialize-other-delivery-run"
        predecessor = {
            **own_run,
            "run_id": other_run_id,
            "output_path": str(other_run_root.resolve()),
            "initialization_intent_id": initialization_intent_id,
            "coordination_revision": 1,
            "last_mutation_intent_id": None,
            "delivery": {
                "stage": "ready_for_delivery",
                "ownership": {"session_id": other_session_id, "generation": 1},
                "projections": {
                    "video_target": {
                        "path": "review/acceptance/delivery_target.json",
                        "projection_revision": 1,
                        "sha256": "a" * 64,
                    },
                    "session_target": {
                        "path": str(other_session_path.resolve()),
                        "projection_revision": 1,
                        "sha256": "b" * 64,
                    },
                    "task_index": {
                        "path": str(task_index_path.resolve()),
                        "projection_revision": task_index["projection_revision"] - 1,
                        "sha256": prior_index_sha,
                    },
                    "archive": None,
                },
            },
        }
        other_run_path = other_run_root / "workflow" / "run.json"
        other_run_path.parent.mkdir(parents=True, exist_ok=True)
        other_run_path.write_bytes(canonical_json_bytes(predecessor))
        predecessor_sha = file_sha(other_run_path)
        store = ControlStore(
            Path(self.binding["run"]["control_store_root"]),
            ContractRegistry(PROJECT_ROOT),
        )
        store.prepare_initialization(
            run_id=other_run_id,
            output_path=other_run_root,
            intent_id=initialization_intent_id,
            staging_path=store.workspace_root / "staging" / other_run_id,
        )
        store.bind_publication_expectations(
            initialization_intent_id,
            expected_run_record_sha256=predecessor_sha,
            canonical_platform=predecessor["canonical_platform"],
            canonical_item_id=predecessor["canonical_item_id"],
            source_identity=predecessor["source_identity"],
            source_manifest_sha256="f" * 64,
        )
        for expected, new in (
            ("PREPARED", "PUBLISHED"),
            ("PUBLISHED", "RECORD_COMMITTED"),
            ("RECORD_COMMITTED", "COMMITTED"),
        ):
            store.transition_intent(
                initialization_intent_id,
                expected_state=expected,
                new_state=new,
                run_record_sha256=predecessor_sha,
            )

        successor = json.loads(json.dumps(predecessor))
        successor["coordination_revision"] = 2
        successor["last_mutation_intent_id"] = intent_id
        successor["delivery"] = {
            "stage": "accepted",
            "ownership": {"session_id": other_session_id, "generation": 1},
            "projections": {
                "video_target": {
                    "path": "review/acceptance/delivery_target.json",
                    "projection_revision": other_target["projection_revision"],
                    "sha256": file_sha(other_target_path),
                },
                "session_target": {
                    "path": str(other_session_path.resolve()),
                    "projection_revision": other_session["projection_revision"],
                    "sha256": file_sha(other_session_path),
                },
                "task_index": {
                    "path": str(task_index_path.resolve()),
                    "projection_revision": task_index["projection_revision"],
                    "sha256": file_sha(task_index_path),
                },
                "archive": None,
            },
        }
        other_run_path.write_bytes(canonical_json_bytes(successor))
        normalized_task_index = normalized_physical_path(task_index_path)
        slot_id = hashlib.sha256(
            (intent_id + "\0" + normalized_task_index).encode("utf-8")
        ).hexdigest()
        with sqlite3.connect(store_path) as connection:
            connection.execute(
                "INSERT INTO delivery_lifecycle_intents("
                "intent_id,run_id,session_id,expected_run_revision,"
                "expected_ownership_generation,prior_stage,target_stage,operation,"
                "prior_run_record_sha256,replacement_run_record_sha256,"
                "replacement_run_record_json,state,intent_identity) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    intent_id,
                    other_run_id,
                    other_session_id,
                    1,
                    1,
                    "ready_for_delivery",
                    "accepted",
                    "transition",
                    predecessor_sha,
                    file_sha(other_run_path),
                    canonical_json_bytes(successor).decode("utf-8"),
                    "COMMITTED",
                    intent_id,
                ),
            )
            connection.execute(
                "INSERT INTO projection_publication_slots("
                "slot_id,intent_id,normalized_path,expected_state,expected_sha256,"
                "proposed_state,proposed_sha256,state,slot_identity) "
                "VALUES(?,?,?,'present',?,'present',?,'RELEASED',?)",
                (
                    slot_id,
                    intent_id,
                    normalized_task_index,
                    prior_index_sha,
                    file_sha(task_index_path),
                    slot_id,
                ),
            )
        return slot_id

    @staticmethod
    def tree_fingerprints(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): file_sha(path)
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_active_guard_accepts_committed_bilibili_kernel_authority_read_only(
        self,
    ) -> None:
        store_path, task_index_path = self.kernelize_bilibili_target()
        authority_paths = (
            self.video_root / "workflow" / "run.json",
            self.target,
            self.current,
            task_index_path,
            store_path,
        )
        before = {str(path): file_sha(path) for path in authority_paths}

        completed = self.run_guard()

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("PASS", completed.stdout)
        self.assertEqual(
            before, {str(path): file_sha(path) for path in authority_paths}
        )
        guard_report_path = (
            self.video_root / "review" / "acceptance" / "delivery_guard_report.json"
        )
        guard_report = json.loads(guard_report_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "schema_version": "1.0",
                "status": "pass",
                "stage": "accepted",
                "validated_by": "delivery_guard.py",
                "acceptance_report_status": "pass",
            },
            {
                field: guard_report[field]
                for field in (
                    "schema_version",
                    "status",
                    "stage",
                    "validated_by",
                    "acceptance_report_status",
                )
            },
        )

    def test_active_guard_rejects_uncommitted_bilibili_kernel_intent_read_only(
        self,
    ) -> None:
        store_path, _ = self.kernelize_bilibili_target()
        with sqlite3.connect(store_path) as connection:
            connection.execute(
                "UPDATE delivery_lifecycle_intents SET state='FILES_PUBLISHED'"
            )
        before = self.tree_fingerprints(self.project_root)

        completed = self.run_guard()

        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("committed Delivery Lifecycle", completed.stderr)
        self.assertEqual(before, self.tree_fingerprints(self.project_root))

    def test_active_guard_accepts_committed_shared_task_index_advance_and_rejects_held_slot(
        self,
    ) -> None:
        store_path, task_index_path = self.kernelize_bilibili_target()
        slot_id = self.publish_other_run_task_index_revision(
            store_path, task_index_path
        )
        authority_before = {
            "control_store": file_sha(store_path),
            "task_index": file_sha(task_index_path),
        }

        committed = self.run_guard()

        self.assertEqual(0, committed.returncode, committed.stdout + committed.stderr)
        self.assertIn("PASS", committed.stdout)
        self.assertEqual(
            authority_before,
            {
                "control_store": file_sha(store_path),
                "task_index": file_sha(task_index_path),
            },
        )

        with sqlite3.connect(store_path) as connection:
            connection.execute(
                "UPDATE projection_publication_slots SET state='HELD' WHERE slot_id=?",
                (slot_id,),
            )

        held = self.run_guard()

        self.assertEqual(2, held.returncode, held.stdout + held.stderr)
        self.assertIn("projection", held.stderr.lower())

    def test_active_guard_rejects_stale_bilibili_kernel_projection_read_only(
        self,
    ) -> None:
        self.kernelize_bilibili_target()
        session = json.loads(self.current.read_text(encoding="utf-8"))
        session["run_revision"] = 1
        write_json(self.current, session)
        before = self.tree_fingerprints(self.project_root)

        completed = self.run_guard()

        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("projection", completed.stderr.lower())
        self.assertEqual(before, self.tree_fingerprints(self.project_root))

    def test_bilibili_kernel_target_rejects_legacy_guard_mutation_commands(
        self,
    ) -> None:
        _, task_index = self.kernelize_bilibili_target()
        commands = {
            "task-claim": [
                "--session-id", self.session_id, "--video-output-dir", str(self.video_root),
                "--target-file", str(self.target), "--stage", "accepted",
            ],
            "task-update": [
                "--session-id", self.session_id, "--video-output-dir", str(self.video_root),
                "--stage", "accepted", "--owner-status", "active",
            ],
            "clear-target": [
                "--session-id", self.session_id, "--video-output-dir", str(self.video_root),
            ],
            "task-handoff": [
                "--from-session-id", self.session_id, "--to-session-id", "session-b",
                "--video-output-dir", str(self.video_root), "--target-file", str(self.target),
                "--stage", "accepted", "--previous-owner-status", "superseded",
            ],
        }
        for command, arguments in commands.items():
            with self.subTest(command=command):
                before = self.tree_fingerprints(self.project_root)
                completed = subprocess.run(
                    [
                        sys.executable, "-X", "utf8", "-B", str(GUARD), command,
                        "--project-root", str(self.project_root),
                        "--current-target", str(self.current),
                        "--task-index", str(task_index), *arguments,
                    ],
                    cwd=self.project_root,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn("Kernel delivery authority is read-only", completed.stderr)
                self.assertEqual(before, self.tree_fingerprints(self.project_root))

    def test_old_pdf_prepare_rejects_bilibili_kernel_run_before_authority_mutation(
        self,
    ) -> None:
        (self.video_root / "main.tex").write_text(
            self.main_tex.read_text(encoding="utf-8"), encoding="utf-8"
        )
        store_path, task_index_path = self.kernelize_bilibili_target()
        authority_paths = {
            "video_target": self.target,
            "session_target": self.current,
            "task_index": task_index_path,
            "control_store": store_path,
        }
        before = {name: file_sha(path) for name, path in authority_paths.items()}

        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(GUARD),
                "old-pdf-prepare",
                str(self.final_pdf),
                "--session-id",
                self.session_id,
                "--video-output-dir",
                str(self.video_root),
                "--project-root",
                str(self.project_root),
                "--current-target",
                str(self.current),
                "--task-index",
                str(task_index_path),
            ],
            cwd=self.project_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            before,
            {name: file_sha(path) for name, path in authority_paths.items()},
        )
        # This Guard command exposes prose rather than structured gate codes.
        self.assertIn("Bilibili Kernel Run", completed.stderr)

    def assert_cached_hook_passes(self) -> None:
        checked = self.run_guard()
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
        cached = self.run_hook_stop()
        self.assertEqual(0, cached.returncode, cached.stdout + cached.stderr)
        self.assertIn("fresh passing guard report", cached.stdout)

    def test_active_guard_accepts_current_passing_v2_authority(self) -> None:
        completed = self.run_guard()

        self.assertEqual(0, completed.returncode, completed.stderr)
        report = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("pass", report["status"])
        self.assertEqual("pass", report["acceptance_report_status"])
        self.assertIn("acceptance_report_v2_authority_current", {item["condition"] for item in report["checked_conditions"]})

    def test_cached_hook_rejects_missing_acceptance_control_store(self) -> None:
        self.assert_cached_hook_passes()
        control_store = self.workspace / "acceptance-control.sqlite3"
        quarantine = self.project_root / "待删除" / control_store.name
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(control_store, quarantine)

        completed = self.run_hook_stop()

        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("Final Delivery Guard blocked delivery", completed.stderr)
        report = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("execution_identity", report["first_failing_gate"])
        self.assertEqual("acceptance_dimension_authority_stale", report["error_code"])

    def test_cached_hook_rejects_corrupt_acceptance_control_store(self) -> None:
        self.assert_cached_hook_passes()
        (self.workspace / "acceptance-control.sqlite3").write_bytes(b"not sqlite")

        completed = self.run_hook_stop()

        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("Final Delivery Guard blocked delivery", completed.stderr)
        report = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("control_store", report["first_failing_gate"])
        self.assertEqual("acceptance_v2_control_store_unavailable", report["error_code"])

    def test_cached_hook_rejects_corrupt_global_gate_control_store(self) -> None:
        self.assert_cached_hook_passes()
        gate_root = Path(self.binding["global_gate_authority"]["path"]).parent
        (gate_root / "global-gate-control.sqlite3").write_bytes(b"not sqlite")

        completed = self.run_hook_stop()

        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("Final Delivery Guard blocked delivery", completed.stderr)
        report = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("control_store", report["first_failing_gate"])
        self.assertEqual("global_gate_control_store_corrupt", report["error_code"])

    def test_cached_hook_rejects_missing_global_gate_control_store(self) -> None:
        self.assert_cached_hook_passes()
        gate_root = Path(self.binding["global_gate_authority"]["path"]).parent
        control_store = gate_root / "global-gate-control.sqlite3"
        quarantine = self.project_root / "待删除" / control_store.name
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        gc.collect()
        shutil.move(control_store, quarantine)

        completed = self.run_hook_stop()

        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("Final Delivery Guard blocked delivery", completed.stderr)
        report = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("global_gate_authority", report["first_failing_gate"])
        self.assertEqual("global_gate_authority_stale", report["error_code"])

    def test_active_guard_accepts_run_record_free_legacy_v2_authority(self) -> None:
        project_root = new_case_dir(self.id(), label="issue43-active-guard-legacy")
        wrapper = project_root / ".agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_WRAPPER, wrapper)
        video_root = project_root / "video"
        original_write_bytes = Path.write_bytes
        pdf_bytes = self._valid_pdf_bytes()

        def write_fixture_bytes(path: Path, data: bytes) -> int:
            if path.name == "final.pdf" and data == b"pdf":
                data = pdf_bytes
            return original_write_bytes(path, data)

        fixture = Issue43GlobalGateTests()
        with mock.patch.object(Path, "write_bytes", new=write_fixture_bytes):
            _, paths = fixture.legacy_graph(video_root, compile_wrapper=wrapper)
        adopted, envelope = fixture.adopt(video_root, paths)
        self.assertEqual(0, adopted.returncode, adopted.stdout + adopted.stderr)
        workspace = video_root / "review/acceptance"
        prepared, _ = run_cli(
            "acceptance-prepare", "--workspace-root", str(workspace),
            "--input-binding", envelope["data"]["input_set_path"], "--attempt-number", "1",
            "--prepared-at", "2026-08-03T00:00:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        fixture.commit_visual(workspace)
        materialized, _ = fixture.materialize(workspace)
        self.assertEqual(0, materialized.returncode, materialized.stdout + materialized.stderr)
        binding = json.loads((workspace / "input-binding.json").read_text(encoding="utf-8"))
        final_pdf = Path(next(item["path"] for item in binding["artifacts"] if item["logical_id"] == "final_pdf"))
        main_tex = Path(next(item["path"] for item in binding["artifacts"] if item["logical_id"] == "main_tex"))
        rendered_dir = workspace / "rendered_pages"
        rendered_dir.mkdir(parents=True, exist_ok=True)
        for item in binding["rendered_pages"]["pages"]:
            shutil.copy2(item["path"], rendered_dir / f"page_{item['page']:04d}.png")
        gate = binding["global_gate_authority"]
        target = write_json(workspace / "delivery_target.json", {
            "schema_version": "1.0", "stage": "accepted", "video_output_dir": ".",
            "final_pdf": final_pdf.relative_to(video_root).as_posix(),
            "main_tex": main_tex.relative_to(video_root).as_posix(),
            "allowed_artifacts_manifest": paths["manifest"].relative_to(video_root).as_posix(),
            "acceptance_report": "review/acceptance/acceptance_report.json",
            "delivery_guard_report": "review/acceptance/delivery_guard_report.json",
            "compile_report": paths["compile"].relative_to(video_root).as_posix(),
            "global_gate_authority": {
                "path": Path(gate["path"]).relative_to(project_root).as_posix(), "sha256": gate["file_sha256"],
            },
            "attempt_limit": 3,
        })
        session_id = f"session-{uuid.uuid4().hex}"
        current = write_json(project_root / f".codex/delivery-targets/sessions/{session_id}/current.json", {
            "schema_version": "1.1", "scope": "session", "session_id": session_id,
            "turn_id": "turn-fixture", "observed_codex_thread_id": "thread-fixture", "stage": "accepted",
            "video_output_dir": video_root.relative_to(project_root).as_posix(),
            "target_file": target.relative_to(project_root).as_posix(), "source_skill": "test-fixture",
            "started_at": "2026-08-03T00:00:00Z", "updated_at": "2026-08-03T00:00:00Z",
        })
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(GUARD), "check", "--project-root", str(project_root),
             "--current-target", str(current)],
            cwd=project_root, text=True, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        report = json.loads((workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("pass", report["status"])
        self.assertFalse((video_root / "workflow/run.json").exists())

    def test_active_guard_rejects_v1_fallback(self) -> None:
        report_path = self.workspace / "acceptance_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report.pop("schema_name")
        report["schema_version"] = "1.0"
        write_json(report_path, report)

        completed = self.run_guard()

        self.assertEqual(2, completed.returncode)
        guard = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("acceptance_authority", guard["first_failing_gate"])
        self.assertEqual("acceptance_report_v1_rejected", guard["error_code"])

    def test_active_guard_rejects_compatibility_translation(self) -> None:
        report_path = self.workspace / "acceptance_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["translated_from"] = "acceptance_report_v1"
        write_json(report_path, report)

        completed = self.run_guard()

        self.assertEqual(2, completed.returncode)
        guard = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("acceptance_authority", guard["first_failing_gate"])
        self.assertEqual("acceptance_compatibility_translation_rejected", guard["error_code"])

    def test_active_guard_rejects_dual_authority(self) -> None:
        target = json.loads(self.target.read_text(encoding="utf-8"))
        target["acceptance_report_v1"] = "review/acceptance/historical_acceptance_report.json"
        write_json(self.target, target)

        completed = self.run_guard()

        self.assertEqual(2, completed.returncode)
        guard = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("acceptance_authority", guard["first_failing_gate"])
        self.assertEqual("acceptance_dual_authority_rejected", guard["error_code"])

    def test_active_guard_rejects_stale_global_gate_authority(self) -> None:
        target = json.loads(self.target.read_text(encoding="utf-8"))
        target["global_gate_authority"]["sha256"] = "0" * 64
        write_json(self.target, target)

        completed = self.run_guard()

        self.assertEqual(2, completed.returncode)
        guard = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("global_gate_authority", guard["first_failing_gate"])
        self.assertEqual("global_gate_authority_stale", guard["error_code"])

    def test_active_guard_rejects_stale_artifact_authority(self) -> None:
        self.main_tex.write_text("Changed after Acceptance Report v2 publication.\n", encoding="utf-8")
        compile_report_path = self.video_root / "review/latex/compile_report.json"
        compile_report = json.loads(compile_report_path.read_text(encoding="utf-8"))
        compile_report["source_tex_fingerprint"] = self._fingerprint(self.main_tex)
        write_json(compile_report_path, compile_report)

        completed = self.run_guard()

        self.assertEqual(2, completed.returncode)
        guard = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("input_freshness", guard["first_failing_gate"])
        self.assertEqual("acceptance_input_stale", guard["error_code"])

    def test_active_guard_rejects_stale_report_publication_authority(self) -> None:
        execution = json.loads((self.workspace / "execution.json").read_text(encoding="utf-8"))
        immutable_report_path = Path(execution["report_publication"]["path"])
        report = json.loads((self.workspace / "acceptance_report.json").read_text(encoding="utf-8"))
        report["routing_state"] = "repair_required"
        write_json(self.workspace / "acceptance_report.json", report)
        write_json(immutable_report_path, report)

        completed = self.run_guard()

        self.assertEqual(2, completed.returncode)
        guard = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("report_fingerprint_current", guard["first_failing_gate"])
        self.assertEqual("acceptance_v2_report_fingerprint_current_stale", guard["error_code"])


if __name__ == "__main__":
    unittest.main()

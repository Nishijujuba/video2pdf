from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest import mock

import fitz

from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow import test_acceptance_v2 as acceptance_v2_tests

PROJECT_ROOT = acceptance_v2_tests.PROJECT_ROOT
file_sha = acceptance_v2_tests.file_sha
run_cli = acceptance_v2_tests.run_cli
write_json = acceptance_v2_tests.write_json

SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore


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
        record["run_id"] = hashlib.md5(str(root).encode()).hexdigest()
        record["output_path"] = str(root.resolve())
        record["initialization_intent_id"] = f"acceptance-fixture-{record['run_id']}"
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
            return acceptance_v2_tests.AcceptanceV2CliTests.build_binding(self, root, generation, **kwargs)

    @staticmethod
    def _fingerprint(path: Path) -> dict[str, object]:
        raw = path.read_bytes()
        return {"algorithm": "sha256", "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}

    def setUp(self) -> None:
        self.project_root = new_case_dir(self.id(), label="issue43-active-guard")
        self.wrapper = self.project_root / ".agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py"
        self.wrapper.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_WRAPPER, self.wrapper)
        self.video_root = self.project_root / "video"
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

    def test_active_guard_accepts_current_passing_v2_authority(self) -> None:
        completed = self.run_guard()

        self.assertEqual(0, completed.returncode, completed.stderr)
        report = json.loads((self.workspace / "delivery_guard_report.json").read_text(encoding="utf-8"))
        self.assertEqual("pass", report["status"])
        self.assertEqual("pass", report["acceptance_report_status"])
        self.assertIn("acceptance_report_v2_authority_current", {item["condition"] for item in report["checked_conditions"]})

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

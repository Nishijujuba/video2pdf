from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import unittest
from unittest import mock
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

SOURCE_FIXTURE = PROJECT_ROOT / "tests/video_workflow/fixtures/source-ready-tracer"
TEST_RUNS = PROJECT_ROOT / "待删除/kernel-test-runs"
CLI = PROJECT_ROOT / "scripts/video_workflow.py"
SYSTEM_FONT = Path("C:/Windows/Fonts/arial.ttf")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def new_root(label: str) -> Path:
    root = TEST_RUNS / f"{label}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


class SingleSectionProductionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = new_root("single-section")
        from tests.video_workflow.test_source_publication_integration import (
            build_decision_ready_authority,
        )

        self.kernel, self.run_dir, _ = build_decision_ready_authority()
        self.kernel.finalize_production_source(
            self.run_dir,
            published_at="2026-07-21T12:00:00+08:00",
        )

    def _cli(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(CLI), *args],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        return completed, json.loads(completed.stdout)

    def test_public_cli_plan_is_idempotent_and_advance_fail_closed_machine_readable(self) -> None:
        completed, envelope = self._cli(
            "production-plan", "--run-dir", str(self.run_dir)
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("production_tasks_runnable", envelope["classification"])
        task = envelope["data"]["runnable_tasks"][0]
        completed, repeated = self._cli(
            "production-plan", "--run-dir", str(self.run_dir)
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(task, repeated["data"]["runnable_tasks"][0])
        completed, envelope = self._cli(
            "production-advance",
            "--run-dir", str(self.run_dir),
            "--task-id", task["task_id"],
            "--attempt-id", "0" * 24,
        )
        self.assertEqual(20, completed.returncode)
        self.assertEqual("contract_invalid", envelope["classification"])
        state_path = self.run_dir / "workflow/production-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["source_binding"]["sha256"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")
        completed, stale = self._cli(
            "production-plan", "--run-dir", str(self.run_dir)
        )
        self.assertEqual(30, completed.returncode)
        self.assertEqual("identity_or_path_conflict", stale["classification"])

    def _attempt(self, envelope: dict, outputs: dict[str, bytes]) -> str:
        attempt_id = uuid.uuid4().hex[:24]
        attempt_dir = (
            self.run_dir
            / "workflow/tasks"
            / envelope["task_id"]
            / "attempts"
            / attempt_id
        )
        attempt_dir.mkdir(parents=True)
        for relative, payload in outputs.items():
            target = attempt_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        record = {
            "schema_name": "production-task-attempt",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "task_id": envelope["task_id"],
            "attempt_id": attempt_id,
            "claim_generation": envelope["claim_generation"],
            "claim_token": envelope["claim_token"],
            "envelope_sha256": sha256(
                self.run_dir / "workflow/tasks" / envelope["task_id"] / "envelope.json"
            ),
            "outputs": [
                {"path": path, "sha256": hashlib.sha256(payload).hexdigest()}
                for path, payload in sorted(outputs.items())
            ],
        }
        (attempt_dir / "attempt.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        return attempt_id

    def _authorize_compile_main(self, main: Path) -> tuple[str, dict[str, object]]:
        run = json.loads((self.run_dir / "workflow/run.json").read_text(encoding="utf-8"))
        source = run["artifact_generations"]["source_manifest"]
        integration_path = self.run_dir / "workflow/integration-manifest.json"
        main_generation = {
            "logical_id": "integrated_main",
            "path": "work/integration/main.tex",
            "generation": 1,
            "sha256": sha256(main),
            "size": main.stat().st_size,
            "producer": "test",
        }
        integration_path.write_text(
            json.dumps(
                {
                    "schema_name": "integration-manifest",
                    "schema_version": "1.0.0",
                    "kernel_version": "2.0.0",
                    "run_id": run["run_id"],
                    "main": main_generation,
                    "sections": [dict(main_generation, logical_id="integrated_section")],
                    "figures": [dict(main_generation, logical_id="figure_asset")],
                    "terminology": [{"term": "fixture", "definition": "fixture"}],
                    "source_binding": {
                        "logical_id": "source_manifest",
                        "generation": source["generation"],
                        "sha256": source["sha256"],
                    },
                }
            ),
            encoding="utf-8",
        )
        artifacts = {
            "integrated_main": {
                "path": "work/integration/main.tex",
                "generation": 1,
                "sha256": sha256(main),
                "size": main.stat().st_size,
                "producer": "test",
            },
            "integrated_section": {
                "path": "work/integration/main.tex",
                "generation": 1,
                "sha256": sha256(main),
                "size": main.stat().st_size,
                "producer": "test",
            },
            "figure_asset": {
                "path": "work/integration/main.tex",
                "generation": 1,
                "sha256": sha256(main),
                "size": main.stat().st_size,
                "producer": "test",
            },
            "integration_manifest": {
                "path": "workflow/integration-manifest.json",
                "generation": 1,
                "sha256": sha256(integration_path),
                "size": integration_path.stat().st_size,
                "producer": "test",
            },
        }
        state = {
            "schema_name": "production-state",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": run["run_id"],
            "source_binding": {
                "logical_id": "source_manifest",
                "generation": source["generation"],
                "sha256": source["sha256"],
            },
            "artifacts": artifacts,
            "completed_roles": [],
            "claims": {},
            "receipts": {},
            "checkpoints": {"source_ready": "current", "draft_compile_ready": "pending"},
        }
        (self.run_dir / "workflow/production-state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        return run["run_id"], {
            "generation": 1,
            "sha256": artifacts["integration_manifest"]["sha256"],
        }

    @staticmethod
    def _pyramid_payload(envelope: dict) -> bytes:
        target = envelope["pyramid_target"]
        return json.dumps(
            {
                "schema_name": "pyramid-evaluation-binding",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "target": target,
                "evaluation_context": envelope["evaluation_context"],
                "status": "pass",
            },
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def _outline_payload() -> bytes:
        return json.dumps(
            {
                "schema_name": "outline-contract",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "article_title": "One guarded section",
                "terminology": [{"term": "closure", "definition": "declared inputs actually read"}],
                "sections": [{"section_id": "section_01", "title": "Core claim"}],
                "required_figure_slots": [{
                    "slot_id": "figure_01",
                    "section_id": "section_01",
                    "teaching_purpose": "Show the dependency boundary",
                    "placement_marker": "% FIGURE_SLOT:figure_01",
                }],
                "compile_support": {
                    "document_class": "course",
                    "class_content": "\\NeedsTeXFormat{LaTeX2e}\n\\ProvidesClass{course}\n\\LoadClass{article}\n",
                    "style_name": "local",
                    "style_content": "\\ProvidesPackage{local}\n",
                    "bibliography_name": "refs.bib",
                    "bibliography_content": "@misc{fixture,title={Fixture}}\n",
                },
            },
            sort_keys=True,
        ).encode("utf-8")

    def _parallel_tasks(self) -> list[dict]:
        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        attempt = self._attempt(outline, {"outline.json": self._outline_payload()})
        self.kernel.production_advance(self.run_dir, outline["task_id"], attempt)
        outline_gate = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        attempt = self._attempt(
            outline_gate, {"pyramid-report.json": self._pyramid_payload(outline_gate)}
        )
        self.kernel.production_advance(self.run_dir, outline_gate["task_id"], attempt)
        return self.kernel.production_plan(self.run_dir)["runnable_tasks"]

    def test_public_plan_and_advance_reach_guarded_diagnostic_compile(self) -> None:
        plan = self.kernel.production_plan(self.run_dir)
        self.assertEqual(["outline"], [task["role"] for task in plan["runnable_tasks"]])
        outline = plan["runnable_tasks"][0]
        outline_payload = self._outline_payload()
        attempt = self._attempt(outline, {"outline.json": outline_payload})
        self.kernel.production_advance(self.run_dir, outline["task_id"], attempt)

        outline_gate = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        attempt = self._attempt(
            outline_gate, {"pyramid-report.json": self._pyramid_payload(outline_gate)}
        )
        self.kernel.production_advance(self.run_dir, outline_gate["task_id"], attempt)

        tasks = self.kernel.production_plan(self.run_dir)["runnable_tasks"]
        self.assertEqual({"writer", "figure"}, {task["role"] for task in tasks})
        writer = next(task for task in tasks if task["role"] == "writer")
        figure = next(task for task in tasks if task["role"] == "figure")
        self.assertTrue(set(writer["write_set"]).isdisjoint(figure["write_set"]))
        self.assertEqual(
            {"source_manifest", "outline_contract", "pyramid_outline_report"},
            {item["logical_id"] for item in writer["input_generations"]},
        )
        self.assertEqual(writer["input_generations"], figure["input_generations"])

        writer_result = json.dumps(
            {
                "schema_name": "writer-result",
                "schema_version": "1.0.0",
                "section_id": "section_01",
                "new_figure_candidates": [],
            },
            sort_keys=True,
        ).encode("utf-8")
        attempt = self._attempt(
            writer,
            {
                "section_01.tex": b"\\section{Core claim}\nDeclared inputs establish closure.\n% FIGURE_SLOT:figure_01\n",
                "writer-result.json": writer_result,
            },
        )
        self.kernel.production_advance(self.run_dir, writer["task_id"], attempt)

        figure_contribution = (
            b"\\begin{figure}\n\\centering\n"
            b"\\includegraphics{figures/figure_01}\n"
            b"\\caption{Declared and observed compile inputs.}\n"
            b"\\par\\small Source (source_timestamp): 00:00:01\n"
            b"\\end{figure}\n"
        )
        figure_manifest = json.dumps(
            {
                "schema_name": "figure-manifest",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "slot_id": "figure_01",
                "section_id": "section_01",
                "asset_path": "figures/figure_01.png",
                "asset_sha256": hashlib.sha256(b"fixture-png").hexdigest(),
                "caption": "Declared and observed compile inputs.",
                "source": {"kind": "source_timestamp", "value": "00:00:01"},
                "slot_contribution_path": "work/figures/figure_01.tex",
                "slot_contribution_sha256": hashlib.sha256(
                    figure_contribution
                ).hexdigest(),
            },
            sort_keys=True,
        ).encode("utf-8")
        attempt = self._attempt(
            figure,
            {
                "figure_01.png": b"fixture-png",
                "figure-manifest.json": figure_manifest,
                "figure_01.tex": figure_contribution,
            },
        )
        self.kernel.production_advance(self.run_dir, figure["task_id"], attempt)

        section_gate = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        self.assertEqual("pyramid_section", section_gate["role"])
        attempt = self._attempt(
            section_gate, {"pyramid-report.json": self._pyramid_payload(section_gate)}
        )
        self.kernel.production_advance(self.run_dir, section_gate["task_id"], attempt)

        main_gate = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        self.assertEqual("pyramid_main", main_gate["role"])
        attempt = self._attempt(
            main_gate, {"pyramid-report.json": self._pyramid_payload(main_gate)}
        )

        unrelated = self.run_dir / "work/integration/unrelated-draft.tex"
        unrelated.write_text("must never be copied", encoding="utf-8")

        from video2pdf_workflow_kernel.guarded_compile import runtime_policy_for_fixture

        policy = runtime_policy_for_fixture(
            run_dir=self.run_dir,
            engine_executable=Path(sys.executable),
            engine_prefix_args=[str(PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py")],
            system_fonts=[SYSTEM_FONT],
        )
        result = self.kernel.production_advance(
            self.run_dir,
            main_gate["task_id"],
            attempt,
            compile_runtime_policy=policy,
        )
        self.assertEqual("diagnostic_compile_ready", result["classification"])
        report = json.loads(Path(result["compile_report_path"]).read_text(encoding="utf-8"))
        self.assertEqual("pass", report["status"])
        self.assertEqual("diagnostic", report["mode"])
        self.assertFalse(report["delivery_authority"])
        self.assertTrue(report["dependency_closure"]["complete"])
        self.assertEqual(
            {"attempt_generated_auxiliary", "manifest_entry", "registered_system_font"},
            {item["classification"] for item in report["dependency_closure"]["inputs"]},
        )
        self.assertTrue(
            {
                "main.tex",
                "section_01.tex",
                "course.cls",
                "local.sty",
                "refs.bib",
                "figure_01.png",
                "main.aux",
                "arial.ttf",
            }.issubset(
                {Path(item["path"]).name for item in report["dependency_closure"]["inputs"]}
            )
        )
        self.assertNotIn(
            str(unrelated.resolve()).casefold(),
            {item["path"].casefold() for item in report["dependency_closure"]["inputs"]},
        )
        self.assertIn("--disable-installer", report["invocation"]["argv"])
        self.assertIn("-no-shell-escape", report["invocation"]["argv"])
        self.assertEqual(1, len(report["executed_passes"]))
        self.assertTrue(
            {"main.aux", "main.fls", "main.pdf"}.issubset(
                {item["path"] for item in report["generated_outputs"]}
            )
        )
        manifest = json.loads(
            (self.run_dir / "workflow/compile-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"bibliography", "entry_tex", "figure", "local_class", "local_style", "section_tex"},
            {item["role"] for item in manifest["entries"]},
        )
        self.assertEqual(
            {"application/x-bibtex", "application/x-tex", "image/png"},
            {item["media_type"] for item in manifest["entries"]},
        )
        self.assertEqual(
            "current",
            json.loads((self.run_dir / "workflow/production-state.json").read_text(encoding="utf-8"))["checkpoints"]["draft_compile_ready"],
        )
        completed, provider_result = self._cli(
            "guarded-compile",
            "--run-dir", str(self.run_dir),
            "--manifest", str(self.run_dir / "workflow/compile-manifest.json"),
            "--runtime-policy", str(self.run_dir / "workflow/compile-runtime-policy.json"),
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("diagnostic_compile_ready", provider_result["classification"])
        self.assertFalse(provider_result["data"]["delivery_authority"])

    def test_compile_preflight_rejects_shell_escape_before_engine_launch(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.guarded_compile import runtime_policy_for_fixture

        source = self.run_dir / "work/integration/main.tex"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("\\immediate\\write18{whoami}\n", encoding="utf-8")
        run_id, integration_generation = self._authorize_compile_main(source)
        policy = runtime_policy_for_fixture(
            run_dir=self.run_dir,
            engine_executable=Path(sys.executable),
            engine_prefix_args=[str(PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py")],
            system_fonts=[SYSTEM_FONT],
        )
        with self.assertRaisesRegex(ContractError, "trusted Python engine"):
            runtime_policy_for_fixture(
                run_dir=self.run_dir,
                engine_executable=SYSTEM_FONT,
                engine_prefix_args=[str(PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py")],
                system_fonts=[SYSTEM_FONT],
            )
        policy_path = self.run_dir / "workflow/compile-runtime-policy.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        manifest = {
            "schema_name": "compile-manifest",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": run_id,
            "mode": "diagnostic",
            "delivery_authority": False,
            "integration_manifest_generation": integration_generation,
            "runtime_policy_sha256": policy["policy_sha256"],
            "dependency_discovery_policy_version": "recorder-closure-v1",
            "entries": [{
                "logical_id": "integrated_main",
                "generation": 1,
                "sha256": sha256(source),
                "size": source.stat().st_size,
                "producer": "test",
                "source_path": "work/integration/main.tex",
                "staging_path": "main.tex",
                "role": "entry_tex",
                "media_type": "application/x-tex",
                "required": True,
            }],
        }
        manifest_path = self.run_dir / "workflow/compile-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        completed, result = self._cli(
            "guarded-compile",
            "--run-dir", str(self.run_dir),
            "--manifest", str(manifest_path),
            "--runtime-policy", str(policy_path),
        )
        self.assertEqual(20, completed.returncode)
        self.assertEqual("contract_invalid", result["classification"])
        self.assertIn("shell escape", result["data"]["message"])
        self.assertFalse(any(self.run_dir.rglob("*.fls")))

    def test_compile_manifest_rejects_undeclared_or_escaping_project_input(self) -> None:
        from video2pdf_workflow_kernel.guarded_compile import GuardedCompileProvider
        from video2pdf_workflow_kernel.errors import ContractError

        provider = GuardedCompileProvider(self.run_dir)
        with self.assertRaisesRegex(ContractError, "escapes"):
            provider.validate_manifest_entry_path("../outside.tex", "main.tex")
        with self.assertRaisesRegex(ContractError, "absolute"):
            provider.validate_manifest_entry_path(str(Path(sys.executable)), "main.tex")

        from video2pdf_workflow_kernel.errors import CompileDependencyGap

        with self.assertRaisesRegex(CompileDependencyGap, "undeclared direct"):
            provider._validate_declared_references(
                "\\input{missing-section}", {"main.tex"}, {"graphicx"}
            )

    def test_registered_real_miktex_policy_binds_exact_runtime_inputs(self) -> None:
        from video2pdf_workflow_kernel.guarded_compile import (
            GuardedCompileProvider,
            runtime_policy_for_miktex,
        )

        engine = Path(r"D:\kits\MiKTex\miktex\bin\x64\xelatex.exe")
        self.assertTrue(engine.is_file())
        inventory = self.run_dir / "workflow/runtime/miktex-package-inventory.json"
        inventory.parent.mkdir(parents=True, exist_ok=True)
        inventory.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "files": [{"path": str(engine), "sha256": sha256(engine)}],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        policy = runtime_policy_for_miktex(
            package_inventory=inventory,
            system_fonts=[SYSTEM_FONT],
        )
        registered = GuardedCompileProvider(self.run_dir)._validate_runtime_policy(policy)
        self.assertEqual("miktex-xelatex-runtime", policy["policy_id"])
        self.assertEqual([], policy["engine"]["prefix_args"])
        self.assertEqual(sha256(engine), registered[str(engine.resolve()).casefold()])

    def test_runtime_policy_and_recorder_evidence_fail_closed(self) -> None:
        from video2pdf_workflow_kernel.errors import CompileDependencyGap, ContractError
        from video2pdf_workflow_kernel.guarded_compile import (
            GuardedCompileProvider,
            runtime_policy_for_fixture,
        )

        main = self.run_dir / "work/integration/main.tex"
        main.parent.mkdir(parents=True, exist_ok=True)
        main.write_text("\\begin{document}fixture\\end{document}\n", encoding="utf-8")
        run_id, integration_generation = self._authorize_compile_main(main)
        policy = runtime_policy_for_fixture(
            run_dir=self.run_dir,
            engine_executable=Path(sys.executable),
            engine_prefix_args=[str(PROJECT_ROOT / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py")],
            system_fonts=[SYSTEM_FONT],
        )
        unsafe = dict(policy)
        unsafe["automatic_package_install"] = True
        with self.assertRaisesRegex(ContractError, "automatic package"):
            GuardedCompileProvider(self.run_dir)._validate_runtime_policy(unsafe)
        from video2pdf_workflow_kernel.utils import canonical_json_bytes

        unsafe_root = json.loads(json.dumps(policy))
        unsafe_root["allowed_runtime_roots"] = [str(self.run_dir)]
        unbound = dict(unsafe_root)
        unbound.pop("policy_sha256")
        unsafe_root["policy_sha256"] = hashlib.sha256(
            canonical_json_bytes(unbound)
        ).hexdigest()
        with self.assertRaisesRegex(ContractError, "overlaps project authority"):
            GuardedCompileProvider(self.run_dir)._validate_runtime_policy(unsafe_root)

        manifest = {
            "schema_name": "compile-manifest",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": run_id,
            "mode": "diagnostic",
            "delivery_authority": False,
            "integration_manifest_generation": integration_generation,
            "runtime_policy_sha256": policy["policy_sha256"],
            "dependency_discovery_policy_version": "recorder-closure-v1",
            "entries": [{
                "logical_id": "integrated_main",
                "generation": 1,
                "sha256": sha256(main),
                "size": main.stat().st_size,
                "producer": "test",
                "source_path": "work/integration/main.tex",
                "staging_path": "main.tex",
                "role": "entry_tex",
                "media_type": "application/x-tex",
                "required": True,
            }],
        }
        manifest_path = self.run_dir / "workflow/compile-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        forged = dict(manifest)
        forged["entries"] = [dict(manifest["entries"][0], generation=2)]
        forged_path = self.run_dir / "workflow/forged-compile-manifest.json"
        forged_path.write_text(json.dumps(forged), encoding="utf-8")
        with self.assertRaisesRegex(CompileDependencyGap, "committed Artifact Generation"):
            GuardedCompileProvider(self.run_dir).compile(forged_path, policy)
        foreign = dict(manifest)
        foreign["run_id"] = "f" * 32
        foreign_path = self.run_dir / "workflow/foreign-compile-manifest.json"
        foreign_path.write_text(json.dumps(foreign), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "another Run"):
            GuardedCompileProvider(self.run_dir).compile(foreign_path, policy)
        escaped_manifest = self.root / "compile-manifest.json"
        escaped_manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "escapes"):
            GuardedCompileProvider(self.run_dir).compile(escaped_manifest, policy)
        outside = self.root / "undeclared.tex"
        outside.write_text("outside", encoding="utf-8")
        with mock.patch.dict(
            os.environ,
            {"VIDEO2PDF_FIXTURE_UNDECLARED_INPUT": str(outside)},
        ):
            with self.assertRaisesRegex(CompileDependencyGap, "undeclared compile inputs"):
                GuardedCompileProvider(self.run_dir).compile(manifest_path, policy)
        runtime_candidates = [
            path
            for path in Path(sys.executable).resolve().parent.iterdir()
            if path.is_file() and path.resolve() != Path(sys.executable).resolve()
        ]
        self.assertTrue(runtime_candidates)
        with mock.patch.dict(
            os.environ,
            {"VIDEO2PDF_FIXTURE_UNDECLARED_INPUT": str(runtime_candidates[0])},
        ):
            with self.assertRaisesRegex(CompileDependencyGap, "undeclared compile inputs"):
                GuardedCompileProvider(self.run_dir).compile(manifest_path, policy)

    def test_figure_manifest_rejects_a_mismatched_slot_contribution(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError

        figure = next(task for task in self._parallel_tasks() if task["role"] == "figure")
        contribution = b"\\includegraphics{figures/another_asset}\n"
        manifest = {
            "schema_name": "figure-manifest",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "slot_id": "figure_01",
            "section_id": "section_01",
            "asset_path": "figures/figure_01.png",
            "asset_sha256": hashlib.sha256(b"fixture-png").hexdigest(),
            "caption": "Bound caption.",
            "source": {"kind": "generated_diagram", "value": "fixture"},
            "slot_contribution_path": "work/figures/figure_01.tex",
            "slot_contribution_sha256": hashlib.sha256(contribution).hexdigest(),
        }
        attempt = self._attempt(
            figure,
            {
                "figure_01.png": b"fixture-png",
                "figure-manifest.json": json.dumps(manifest, sort_keys=True).encode("utf-8"),
                "figure_01.tex": contribution,
            },
        )
        with self.assertRaisesRegex(ContractError, "differs from its Manifest"):
            self.kernel.production_advance(self.run_dir, figure["task_id"], attempt)

    def test_promotion_journal_recovers_prepared_and_committed_boundaries(self) -> None:
        from video2pdf_workflow_kernel.errors import ProductionFault

        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        outline_attempt = self._attempt(
            outline, {"outline.json": self._outline_payload()}
        )
        with self.assertRaisesRegex(ProductionFault, "after_promotion_prepared"):
            self.kernel.production_advance(
                self.run_dir,
                outline["task_id"],
                outline_attempt,
                fault_point="after_promotion_prepared",
            )
        self.kernel.production_advance(
            self.run_dir, outline["task_id"], outline_attempt
        )
        gate = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        gate_attempt = self._attempt(
            gate, {"pyramid-report.json": self._pyramid_payload(gate)}
        )
        with self.assertRaisesRegex(ProductionFault, "after_promotion_committed"):
            self.kernel.production_advance(
                self.run_dir,
                gate["task_id"],
                gate_attempt,
                fault_point="after_promotion_committed",
            )
        result = self.kernel.production_advance(
            self.run_dir, gate["task_id"], gate_attempt
        )
        self.assertEqual("production_advanced", result["classification"])
        self.assertEqual("production_tasks_runnable", result["next_classification"])
        self.assertEqual(
            {"writer", "figure"},
            {task["role"] for task in result["runnable_tasks"]},
        )

    def test_committed_promotion_is_fenced_before_state_receipt(self) -> None:
        from video2pdf_workflow_kernel.errors import KernelConflict, ProductionFault

        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        attempt_a = self._attempt(outline, {"outline.json": self._outline_payload()})
        attempt_b = self._attempt(outline, {"outline.json": self._outline_payload()})
        with self.assertRaisesRegex(ProductionFault, "after_promotion_committed"):
            self.kernel.production_advance(
                self.run_dir,
                outline["task_id"],
                attempt_a,
                fault_point="after_promotion_committed",
            )
        with self.assertRaisesRegex(KernelConflict, "fenced Attempt"):
            self.kernel.production_advance(self.run_dir, outline["task_id"], attempt_b)
        recovered = self.kernel.production_advance(
            self.run_dir, outline["task_id"], attempt_a
        )
        self.assertEqual("production_advanced", recovered["classification"])

    def test_explicit_reclaim_increments_generation_and_fences_old_attempt(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError

        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        old_attempt = self._attempt(outline, {"outline.json": self._outline_payload()})
        reclaimed = self.kernel.production_plan(
            self.run_dir,
            supersede_task_id=outline["task_id"],
            expected_claim_generation=1,
        )["runnable_tasks"][0]
        self.assertEqual(2, reclaimed["claim_generation"])
        self.assertNotEqual(outline["claim_token"], reclaimed["claim_token"])
        with self.assertRaisesRegex(ContractError, "binding is invalid"):
            self.kernel.production_advance(
                self.run_dir, outline["task_id"], old_attempt
            )
        new_attempt = self._attempt(
            reclaimed, {"outline.json": self._outline_payload()}
        )
        result = self.kernel.production_advance(
            self.run_dir, reclaimed["task_id"], new_attempt
        )
        self.assertEqual("production_advanced", result["classification"])

    def test_attempt_output_hard_link_cannot_escape_staging_authority(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError

        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        attempt_id = uuid.uuid4().hex[:24]
        attempt_dir = (
            self.run_dir / "workflow/tasks" / outline["task_id"] / "attempts" / attempt_id
        )
        attempt_dir.mkdir(parents=True)
        external = self.run_dir / "worker-owned-outline.json"
        payload = self._outline_payload()
        external.write_bytes(payload)
        os.link(external, attempt_dir / "outline.json")
        record = {
            "schema_name": "production-task-attempt",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "task_id": outline["task_id"],
            "attempt_id": attempt_id,
            "claim_generation": outline["claim_generation"],
            "claim_token": outline["claim_token"],
            "envelope_sha256": sha256(
                self.run_dir / "workflow/tasks" / outline["task_id"] / "envelope.json"
            ),
            "outputs": [{"path": "outline.json", "sha256": hashlib.sha256(payload).hexdigest()}],
        }
        (attempt_dir / "attempt.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "independent regular file"):
            self.kernel.production_advance(
                self.run_dir, outline["task_id"], attempt_id
            )

    def test_state_commit_receipt_is_retry_idempotent_and_fences_late_attempt(self) -> None:
        from video2pdf_workflow_kernel.errors import KernelConflict, ProductionFault

        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        attempt = self._attempt(outline, {"outline.json": self._outline_payload()})
        with self.assertRaisesRegex(ProductionFault, "after_state_committed"):
            self.kernel.production_advance(
                self.run_dir,
                outline["task_id"],
                attempt,
                fault_point="after_state_committed",
            )
        result = self.kernel.production_advance(
            self.run_dir, outline["task_id"], attempt
        )
        self.assertEqual("production_advanced", result["classification"])
        late = self._attempt(outline, {"outline.json": self._outline_payload()})
        with self.assertRaisesRegex(KernelConflict, "fenced Attempt"):
            self.kernel.production_advance(self.run_dir, outline["task_id"], late)

    def test_receipt_commit_is_atomic_with_completed_state(self) -> None:
        from video2pdf_workflow_kernel.errors import ProductionFault

        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        attempt = self._attempt(outline, {"outline.json": self._outline_payload()})
        with self.assertRaisesRegex(ProductionFault, "before_receipt_committed"):
            self.kernel.production_advance(
                self.run_dir,
                outline["task_id"],
                attempt,
                fault_point="before_receipt_committed",
            )
        persisted = json.loads(
            (self.run_dir / "workflow/production-state.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("outline", persisted["completed_roles"])
        self.assertNotIn("outline", persisted["receipts"])
        recovered = self.kernel.production_advance(
            self.run_dir, outline["task_id"], attempt
        )
        self.assertEqual("production_advanced", recovered["classification"])

    def test_production_artifact_drift_blocks_the_next_gate(self) -> None:
        from video2pdf_workflow_kernel.errors import ArtifactDrift

        outline = self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]
        attempt = self._attempt(outline, {"outline.json": self._outline_payload()})
        self.kernel.production_advance(self.run_dir, outline["task_id"], attempt)
        (self.run_dir / "work/outline/outline.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ArtifactDrift, "outline_contract"):
            self.kernel.production_plan(self.run_dir)

    def test_writer_and_figure_promotions_are_serialized_across_processes(self) -> None:
        tasks = self._parallel_tasks()
        writer = next(task for task in tasks if task["role"] == "writer")
        figure = next(task for task in tasks if task["role"] == "figure")
        writer_attempt = self._attempt(
            writer,
            {
                "section_01.tex": b"\\section{Core claim}\n% FIGURE_SLOT:figure_01\n",
                "writer-result.json": json.dumps(
                    {
                        "schema_name": "writer-result",
                        "schema_version": "1.0.0",
                        "section_id": "section_01",
                        "new_figure_candidates": [],
                    },
                    sort_keys=True,
                ).encode("utf-8"),
            },
        )
        contribution = (
            b"\\begin{figure}\n\\centering\n"
            b"\\includegraphics{figures/figure_01}\n"
            b"\\caption{Concurrent figure.}\n"
            b"\\par\\small Source (generated_diagram): concurrency fixture\n"
            b"\\end{figure}\n"
        )
        figure_attempt = self._attempt(
            figure,
            {
                "figure_01.png": b"fixture-png",
                "figure-manifest.json": json.dumps(
                    {
                        "schema_name": "figure-manifest",
                        "schema_version": "1.0.0",
                        "kernel_version": "2.0.0",
                        "slot_id": "figure_01",
                        "section_id": "section_01",
                        "asset_path": "figures/figure_01.png",
                        "asset_sha256": hashlib.sha256(b"fixture-png").hexdigest(),
                        "caption": "Concurrent figure.",
                        "source": {"kind": "generated_diagram", "value": "concurrency fixture"},
                        "slot_contribution_path": "work/figures/figure_01.tex",
                        "slot_contribution_sha256": hashlib.sha256(contribution).hexdigest(),
                    },
                    sort_keys=True,
                ).encode("utf-8"),
                "figure_01.tex": contribution,
            },
        )
        commands = [
            [
                sys.executable, "-X", "utf8", "-B", str(CLI),
                "production-advance", "--run-dir", str(self.run_dir),
                "--task-id", task["task_id"], "--attempt-id", attempt,
            ]
            for task, attempt in ((writer, writer_attempt), (figure, figure_attempt))
        ]
        processes = [
            subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for command in commands
        ]
        completed = [process.communicate(timeout=120) for process in processes]
        self.assertEqual([0, 0], [process.returncode for process in processes], completed)
        self.assertEqual(
            "pyramid_section",
            self.kernel.production_plan(self.run_dir)["runnable_tasks"][0]["role"],
        )

    def test_multi_file_figure_promotion_recovers_after_partial_publication(self) -> None:
        from video2pdf_workflow_kernel.errors import ProductionFault

        figure = next(task for task in self._parallel_tasks() if task["role"] == "figure")
        contribution = (
            b"\\begin{figure}\n\\centering\n"
            b"\\includegraphics{figures/figure_01}\n"
            b"\\caption{Recovered caption.}\n"
            b"\\par\\small Source (generated_diagram): fixture\n"
            b"\\end{figure}\n"
        )
        manifest = json.dumps(
            {
                "schema_name": "figure-manifest",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "slot_id": "figure_01",
                "section_id": "section_01",
                "asset_path": "figures/figure_01.png",
                "asset_sha256": hashlib.sha256(b"fixture-png").hexdigest(),
                "caption": "Recovered caption.",
                "source": {"kind": "generated_diagram", "value": "fixture"},
                "slot_contribution_path": "work/figures/figure_01.tex",
                "slot_contribution_sha256": hashlib.sha256(contribution).hexdigest(),
            },
            sort_keys=True,
        ).encode("utf-8")
        attempt = self._attempt(
            figure,
            {
                "figure_01.png": b"fixture-png",
                "figure-manifest.json": manifest,
                "figure_01.tex": contribution,
            },
        )
        with self.assertRaisesRegex(ProductionFault, "after_first_output"):
            self.kernel.production_advance(
                self.run_dir,
                figure["task_id"],
                attempt,
                fault_point="after_first_output",
            )
        self.assertTrue((self.run_dir / "figures/figure_01.png").is_file())
        self.assertFalse((self.run_dir / "work/figures/figure-manifest.json").exists())

        result = self.kernel.production_advance(
            self.run_dir, figure["task_id"], attempt
        )
        self.assertEqual("production_advanced", result["classification"])
        self.assertTrue((self.run_dir / "work/figures/figure-manifest.json").is_file())
        self.assertTrue((self.run_dir / "work/figures/figure_01.tex").is_file())


if __name__ == "__main__":
    unittest.main()

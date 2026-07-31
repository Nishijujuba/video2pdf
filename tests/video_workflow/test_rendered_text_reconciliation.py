from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel.final_compile import (
    final_compile_provider_identity,
    registered_generator_identity,
)

CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"


def canonical_sha(value: object) -> str:
    data = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    return path


def run_cli(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    return completed, json.loads(completed.stdout)


class RenderedTextReconciliationCliTests(unittest.TestCase):
    FINAL_COMPILE_ADAPTER = (
        PROJECT_ROOT
        / "tests/video_workflow/fixtures/delivery-quality/guarded_final_compile_adapter.py"
    )
    FINAL_COMPILE_ADAPTER = (
        PROJECT_ROOT
        / "tests/video_workflow/fixtures/delivery-quality/guarded_final_compile_adapter.py"
    )
    def fixture(self) -> tuple[Path, dict[str, Path]]:
        root = new_case_dir(self.id(), label="rendered-text-reconciliation")
        quality = root / "precompile"
        evidence = root / "final-evidence"
        compile_input = root / "integrated-main.tex"
        compile_input.write_text("guarded final compile fixture\n", encoding="utf-8")
        compile_input_sha256 = hashlib.sha256(compile_input.read_bytes()).hexdigest()
        recorder = root / "adapter-output" / "compile-recorder.fls"
        recorder.parent.mkdir()
        recorder.write_text(f"INPUT {compile_input}\n", encoding="utf-8")
        recorder_sha256 = hashlib.sha256(recorder.read_bytes()).hexdigest()
        pdf = root / "final.pdf"
        pdf.write_bytes(b"%PDF-fixture\n")
        pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()

        generations = {
            "schema_name": "precompile-artifact-generation-set",
            "schema_version": "1.0.0",
            "generation_set_id": "integrated-final-8",
            "producer_ids": ["integration-attempt-8"],
            "artifacts": [
                {"logical_id": "integrated_main_tex", "generation": 8, "sha256": compile_input_sha256}
            ],
        }
        generations["generation_set_sha256"] = canonical_sha(generations)
        texts = {
            "main.title": "从视频证据到可交付课程讲义",
            "main.paragraph.001": "可靠交付要求每条读者可见文字都能追溯到当前源工件。",
        }
        items = []
        for item_id, text in texts.items():
            item = {
                "item_id": item_id,
                "kind": "title" if item_id.endswith("title") else "paragraph",
                "semantic_region": item_id,
                "language_profile_id": "zh-hans",
                "source_artifact_logical_id": "integrated_main_tex",
                "source_generation": 8,
                "source_sha256": compile_input_sha256,
                "locator": f"latex:{item_id}",
                "representation": "structured_text",
                "text_sha256": text_sha(text),
                "applicable_rule_ids": ["no_meta_writing_content"],
            }
            item["item_sha256"] = canonical_sha(item)
            items.append(item)
        inventory = {
            "schema_name": "reader-facing-text-inventory",
            "schema_version": "1.0.0",
            "inventory_id": "inventory-final-8",
            "language_profile_id": "zh-hans",
            "delivery_glossary": None,
            "generation_set_sha256": generations["generation_set_sha256"],
            "declared_surface": [
                {"region_id": item["item_id"], "kind": item["kind"]} for item in items
            ],
            "items": items,
            "coverage_ledger": [
                {"region_id": item["item_id"], "item_id": item["item_id"], "status": "covered"}
                for item in items
            ],
            "extractors": [{"extractor_id": "latex-text-v1", "extractor_sha256": "2" * 64}],
        }
        inventory["reader_text_set_sha256"] = canonical_sha(
            [
                {
                    "item_id": item["item_id"],
                    "kind": item["kind"],
                    "representation": item["representation"],
                    "text_sha256": item["text_sha256"],
                }
                for item in items
            ]
        )
        inventory["inventory_sha256"] = canonical_sha(inventory)
        seal = {
            "schema_name": "precompile-text-seal",
            "schema_version": "1.0.0",
            "seal_id": "3" * 32,
            "activation_status": "target_only",
            "sealed_at": "2026-07-31T01:00:00Z",
            "decision_origin": "fresh_evaluation",
            "generation_set_sha256": generations["generation_set_sha256"],
            "catalog_sha256": "4" * 64,
            "role_projections_sha256": "5" * 64,
            "language_profile_id": "zh-hans",
            "delivery_glossary": None,
            "semantic_dependencies_sha256": "6" * 64,
            "inventory_sha256": inventory["inventory_sha256"],
            "reader_text_set_sha256": inventory["reader_text_set_sha256"],
            "precompile_quality_report_sha256": "7" * 64,
            "provider": {"provider_id": "precompile-quality-provider"},
            "predecessor_seal_sha256": None,
            "text_equivalence_report_sha256": None,
        }
        seal["seal_sha256"] = canonical_sha(seal)
        write_json(quality / "precompile-text-seal.json", seal)
        binding = quality / "seal-bindings" / seal["seal_sha256"]
        write_json(binding / "reader-facing-text-inventory.json", inventory)
        write_json(binding / "artifact-generations.json", generations)

        compile_manifest = {
            "schema_name": "final-compile-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "mode": "final",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "entries": [
                {
                    "logical_id": "integrated_main_tex",
                    "generation": 8,
                    "sha256": compile_input_sha256,
                    "source_path": str(compile_input),
                    "staging_path": "main.tex",
                }
            ],
            "approved_runtime_inputs": [],
        }
        compile_manifest["manifest_sha256"] = canonical_sha(compile_manifest)
        compiler_provider = final_compile_provider_identity(PROJECT_ROOT)
        final_seal = {
            "schema_name": "final-artifact-seal",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "sealed_at": "2026-07-31T01:10:00Z",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "generation_set_sha256": generations["generation_set_sha256"],
            "compile_manifest_sha256": compile_manifest["manifest_sha256"],
            "compile_provider": compiler_provider,
            "final_pdf": {"path": "final.pdf", "sha256": pdf_sha, "size": pdf.stat().st_size},
        }
        final_seal["seal_sha256"] = canonical_sha(final_seal)
        compile_report = {
            "schema_name": "final-compile-report",
            "schema_version": "1.0.0",
            "mode": "final",
            "status": "pass",
            "delivery_authority": False,
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "final_artifact_seal_sha256": final_seal["seal_sha256"],
            "compile_manifest_sha256": compile_manifest["manifest_sha256"],
            "dependency_closure": {
                "complete": True,
                "inputs": [
                    {
                        "logical_id": "integrated_main_tex",
                        "generation": 8,
                        "sha256": compile_input_sha256,
                    }
                ],
                "runtime_inputs": [],
                "generated_inputs": [],
                "recorder_sha256": recorder_sha256,
                "recorder_path": "adapter-output/compile-recorder.fls",
            },
            "pdf": final_seal["final_pdf"],
            "compiler_provider": compiler_provider,
            "compile_adapter": {
                "adapter_path": str(self.FINAL_COMPILE_ADAPTER.resolve()),
                "adapter_sha256": hashlib.sha256(
                    self.FINAL_COMPILE_ADAPTER.read_bytes()
                ).hexdigest(),
                "protocol_version": "guarded-final-compile-v1",
            },
            "text_origin_plan_sha256": "8" * 64,
        }
        rendered_objects = [
            {
                "object_id": "p1.title.01",
                "page": 1,
                "object_kind": "pdf_text_run",
                "bbox": [10, 10, 100, 20],
                "exact_utf8_text": texts["main.title"],
                "text_sha256": text_sha(texts["main.title"]),
                "extractor_id": "pdf-text-v1",
                "evidence_locator": "page:1/object:p1.title.01",
            },
            {
                "object_id": "p1.paragraph.01a",
                "page": 1,
                "object_kind": "pdf_text_run",
                "bbox": [10, 30, 100, 40],
                "exact_utf8_text": "可靠交付要求每条读者可见文字",
                "text_sha256": text_sha("可靠交付要求每条读者可见文字"),
                "extractor_id": "pdf-text-v1",
                "evidence_locator": "page:1/object:p1.paragraph.01a",
            },
            {
                "object_id": "p1.paragraph.01b",
                "page": 1,
                "object_kind": "pdf_text_run",
                "bbox": [10, 42, 100, 52],
                "exact_utf8_text": "都能追溯到当前源工件。",
                "text_sha256": text_sha("都能追溯到当前源工件。"),
                "extractor_id": "pdf-text-v1",
                "evidence_locator": "page:1/object:p1.paragraph.01b",
            },
            {
                "object_id": "p1.page-number.01",
                "page": 1,
                "object_kind": "pdf_text_run",
                "bbox": [90, 190, 100, 200],
                "exact_utf8_text": "1",
                "text_sha256": text_sha("1"),
                "extractor_id": "pdf-text-v1",
                "evidence_locator": "page:1/object:p1.page-number.01",
            },
        ]
        for obj in rendered_objects:
            obj["object_sha256"] = canonical_sha(obj)
        rendered = {
            "schema_name": "rendered-text-object-inventory",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "final_pdf_sha256": pdf_sha,
            "extractor_suite": [{"extractor_id": "pdf-text-v1", "extractor_sha256": "a" * 64}],
            "coverage": {
                "page_count": 1,
                "pages_scanned": [1],
                "content_streams_complete": True,
                "annotations_complete": True,
                "form_xobjects_complete": True,
                "declared_raster_text_complete": True,
            },
            "objects": rendered_objects,
        }
        rendered["inventory_sha256"] = canonical_sha(rendered)
        origins = {
            "schema_name": "text-origin-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "compiler_provider": final_seal["compile_provider"],
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "final_artifact_seal_sha256": final_seal["seal_sha256"],
            "rendered_text_inventory_sha256": rendered["inventory_sha256"],
            "edges": [
                {
                    "edge_id": "origin.title",
                    "disposition": "sealed_origin",
                    "sealed_item_id": "main.title",
                    "sealed_text_utf8": texts["main.title"],
                    "rendered_object_ids": ["p1.title.01"],
                    "recipe": "exact_utf8",
                },
                {
                    "edge_id": "origin.paragraph",
                    "disposition": "sealed_origin",
                    "sealed_item_id": "main.paragraph.001",
                    "sealed_text_utf8": texts["main.paragraph.001"],
                    "rendered_object_ids": ["p1.paragraph.01a", "p1.paragraph.01b"],
                    "recipe": "layout_whitespace",
                },
                {
                    "edge_id": "generated.page-number",
                    "disposition": "generated",
                    "rendered_object_ids": ["p1.page-number.01"],
                    "recipe": "declared_generated",
                    "generator": {
                        **registered_generator_identity("page-number-v1"),
                        "inputs": {"page": 1},
                    },
                },
            ],
        }
        origins["manifest_sha256"] = canonical_sha(origins)
        rendered_page = root / "rendered_pages" / "page_001.png"
        rendered_page.parent.mkdir(parents=True, exist_ok=True)
        rendered_page.write_bytes(b"png-page-1")
        render_evidence = {
            "schema_name": "render-evidence-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "final_pdf_sha256": pdf_sha,
            "page_count": 1,
            "pages": [{"page": 1, "path": "rendered_pages/page_001.png", "sha256": hashlib.sha256(rendered_page.read_bytes()).hexdigest()}],
        }
        render_evidence["manifest_sha256"] = canonical_sha(render_evidence)
        compile_report.update({
            "render_evidence_manifest_sha256": render_evidence["manifest_sha256"],
            "rendered_text_inventory_sha256": rendered["inventory_sha256"],
            "text_origin_manifest_sha256": origins["manifest_sha256"],
        })
        compile_report["report_sha256"] = canonical_sha(compile_report)
        paths = {
            "precompile_workspace": quality,
            "output": evidence / "rendered-text-reconciliation-report.json",
            "final_pdf": pdf,
            "compile_manifest": write_json(root / "compile-manifest.json", compile_manifest),
            "compile_report": write_json(root / "compile-report.json", compile_report),
            "final_seal": write_json(root / "final-artifact-seal.json", final_seal),
            "render_evidence": write_json(root / "render-evidence-manifest.json", render_evidence),
            "rendered": write_json(root / "rendered-text-object-inventory.json", rendered),
            "origins": write_json(root / "text-origin-manifest.json", origins),
        }
        return root, paths

    def reconcile(self, paths: dict[str, Path]) -> tuple[subprocess.CompletedProcess[str], dict]:
        return run_cli(
            "delivery-quality-rendered-text-reconcile",
            "--precompile-workspace-root", str(paths["precompile_workspace"]),
            "--compile-manifest", str(paths["compile_manifest"]),
            "--compile-report", str(paths["compile_report"]),
            "--final-artifact-seal", str(paths["final_seal"]),
            "--final-pdf", str(paths["final_pdf"]),
            "--render-evidence-manifest", str(paths["render_evidence"]),
            "--rendered-text-inventory", str(paths["rendered"]),
            "--text-origin-manifest", str(paths["origins"]),
            "--output", str(paths["output"]),
            "--reconciled-at", "2026-07-31T01:20:00Z",
        )

    def final_compile(
        self,
        root: Path,
        paths: dict[str, Path],
        *,
        plan_updates: dict | None = None,
        plan_mutator: Callable[[dict], None] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
        origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
        rendered = json.loads(paths["rendered"].read_text(encoding="utf-8"))
        seal = json.loads(
            (paths["precompile_workspace"] / "precompile-text-seal.json").read_text(
                encoding="utf-8"
            )
        )
        plan = {
            "schema_name": "text-origin-plan",
            "schema_version": "1.0.0",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "sealed_items": [
                {
                    "item_id": edge["sealed_item_id"],
                    "exact_utf8_text": edge["sealed_text_utf8"],
                }
                for edge in origins["edges"]
                if edge["disposition"] == "sealed_origin"
            ],
            "page_count": rendered["coverage"]["page_count"],
            "extractor_suite": rendered["extractor_suite"],
            "rendered_objects": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"text_sha256", "object_sha256"}
                }
                for item in rendered["objects"]
            ],
            "edges": origins["edges"],
        }
        if plan_updates:
            plan.update(plan_updates)
        if plan_mutator:
            plan_mutator(plan)
        plan["plan_sha256"] = canonical_sha(plan)
        plan_path = write_json(root / "text-origin-plan.json", plan)
        workspace = root / "guarded-final-compile"
        completed, envelope = run_cli(
            "delivery-quality-final-compile",
            "--precompile-workspace-root", str(paths["precompile_workspace"]),
            "--compile-manifest", str(paths["compile_manifest"]),
            "--text-origin-plan", str(plan_path),
            "--compiler-adapter", str(self.FINAL_COMPILE_ADAPTER),
            "--workspace-root", str(workspace),
            "--compiled-at", "2026-07-31T01:15:00Z",
        )
        return completed, envelope, workspace

    def test_public_final_compile_invokes_adapter_and_produces_reconcilable_evidence(self) -> None:
        root, paths = self.fixture()
        completed, envelope, workspace = self.final_compile(root, paths)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("guarded_final_compile_complete", envelope["classification"])
        paths.update({
            "compile_report": workspace / "final-compile-report.json",
            "final_seal": workspace / "final-artifact-seal.json",
            "final_pdf": workspace / "adapter-output/final.pdf",
            "render_evidence": workspace / "render-evidence-manifest.json",
            "rendered": workspace / "adapter-output/rendered-text-object-inventory.json",
            "origins": workspace / "text-origin-manifest.json",
            "output": workspace / "rendered-text-reconciliation-report.json",
        })
        reconciled, result = self.reconcile(paths)
        self.assertEqual(0, reconciled.returncode, reconciled.stderr)
        self.assertEqual("rendered_text_reconciliation_passed", result["classification"])

    def test_public_final_compile_rejects_stale_precompile_seal_before_adapter(self) -> None:
        root, paths = self.fixture()
        seal_path = paths["precompile_workspace"] / "precompile-text-seal.json"
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        seal["sealed_at"] = "2026-07-31T01:00:01Z"
        write_json(seal_path, seal)
        completed, envelope, workspace = self.final_compile(root, paths)
        self.assertEqual(20, completed.returncode)
        self.assertEqual("contract_invalid", envelope["classification"])
        self.assertFalse((workspace / "adapter-output/final.pdf").exists())

    def test_public_final_compile_rejects_incomplete_origin_plan_before_adapter(self) -> None:
        invalid_updates = (
            {"page_count": 0},
            {
                "edges": [
                    {
                        "edge_id": "dangling",
                        "disposition": "sealed_origin",
                        "sealed_item_id": "missing",
                        "sealed_text_utf8": "missing",
                        "rendered_object_ids": ["missing-object"],
                        "recipe": "exact_utf8",
                    }
                ]
            },
        )
        for updates in invalid_updates:
            with self.subTest(updates=updates):
                root, paths = self.fixture()
                completed, envelope, workspace = self.final_compile(
                    root, paths, plan_updates=updates
                )
                self.assertEqual(20, completed.returncode)
                self.assertEqual("contract_invalid", envelope["classification"])
                self.assertFalse((workspace / "adapter-output/final.pdf").exists())
        invalid_edge_mutators = (
            lambda plan: plan["edges"][-1].pop("recipe"),
            lambda plan: plan["edges"][-1]["generator"].pop("generator_sha256"),
            lambda plan: plan["edges"][0].update(
                disposition="unexpected_addition", recipe=None
            ),
        )
        for mutate in invalid_edge_mutators:
            with self.subTest(mutate=mutate):
                root, paths = self.fixture()
                completed, envelope, workspace = self.final_compile(
                    root, paths, plan_mutator=mutate
                )
                self.assertEqual(20, completed.returncode)
                self.assertEqual("contract_invalid", envelope["classification"])
                self.assertFalse((workspace / "adapter-output/final.pdf").exists())

    def test_complete_current_final_compile_evidence_passes(self) -> None:
        _, paths = self.fixture()
        completed, envelope = self.reconcile(paths)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("rendered_text_reconciliation_passed", envelope["classification"])
        report = json.loads(paths["output"].read_text(encoding="utf-8"))
        self.assertEqual("pass", report["overall_decision"])
        self.assertEqual(2, report["coverage_proof"]["sealed_items_disposed"])
        self.assertEqual(4, report["coverage_proof"]["rendered_objects_disposed"])
        self.assertFalse(report["semantic_reinterpretation_performed"])

    def test_omission_substitution_addition_and_generated_mismatch_are_failures(self) -> None:
        mutations = {
            "omission": lambda origins, rendered: self._omit_paragraph(origins, rendered),
            "substitution": lambda origins, rendered: rendered["objects"][1].update(
                exact_utf8_text="被替换的文字", text_sha256=text_sha("被替换的文字")
            ),
            "addition": lambda origins, rendered: self._add_unexpected(origins, rendered),
            "generated_mismatch": lambda origins, rendered: rendered["objects"][3].update(
                exact_utf8_text="2", text_sha256=text_sha("2")
            ),
        }
        for expected, mutate in mutations.items():
            with self.subTest(expected=expected):
                _, paths = self.fixture()
                origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
                rendered = json.loads(paths["rendered"].read_text(encoding="utf-8"))
                mutate(origins, rendered)
                for obj in rendered["objects"]:
                    obj["object_sha256"] = canonical_sha({k: v for k, v in obj.items() if k != "object_sha256"})
                rendered["inventory_sha256"] = canonical_sha({k: v for k, v in rendered.items() if k != "inventory_sha256"})
                origins["rendered_text_inventory_sha256"] = rendered["inventory_sha256"]
                origins["manifest_sha256"] = canonical_sha({k: v for k, v in origins.items() if k != "manifest_sha256"})
                write_json(paths["rendered"], rendered)
                write_json(paths["origins"], origins)
                self._refresh_compile_output_bindings(paths)
                completed, _ = self.reconcile(paths)
                self.assertEqual(30, completed.returncode)
                report = json.loads(paths["output"].read_text(encoding="utf-8"))
                self.assertIn(expected, {item["decision"] for item in report["findings"]})

    def test_unmapped_unsupported_recipe_and_incomplete_coverage_block_as_contract_gaps(self) -> None:
        mutations = {
            "UNMAPPED_RENDERED_TEXT": lambda origins, rendered: origins["edges"].pop(),
            "UNSUPPORTED_TRANSFORMATION_RECIPE": lambda origins, rendered: origins["edges"][0].update(recipe="similarity_guess"),
            "INCOMPLETE_EXTRACTION_COVERAGE": lambda origins, rendered: rendered["coverage"].update(form_xobjects_complete=False),
            "UNSUPPORTED_GENERATOR_RECIPE": lambda origins, rendered: origins["edges"][2]["generator"].update(generator_sha256="0" * 64),
        }
        for expected, mutate in mutations.items():
            with self.subTest(expected=expected):
                _, paths = self.fixture()
                origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
                rendered = json.loads(paths["rendered"].read_text(encoding="utf-8"))
                mutate(origins, rendered)
                rendered["inventory_sha256"] = canonical_sha({k: v for k, v in rendered.items() if k != "inventory_sha256"})
                origins["rendered_text_inventory_sha256"] = rendered["inventory_sha256"]
                origins["manifest_sha256"] = canonical_sha({k: v for k, v in origins.items() if k != "manifest_sha256"})
                write_json(paths["rendered"], rendered)
                write_json(paths["origins"], origins)
                self._refresh_compile_output_bindings(paths)
                completed, envelope = self.reconcile(paths)
                self.assertEqual(20, completed.returncode)
                self.assertEqual("rendered_text_reconciliation_contract_gap", envelope["classification"])
                report = json.loads(paths["output"].read_text(encoding="utf-8"))
                self.assertIn(expected, {item["code"] for item in report["contract_gaps"]})

    def test_stale_seal_is_rejected_before_report_publication(self) -> None:
        _, paths = self.fixture()
        seal_path = paths["precompile_workspace"] / "precompile-text-seal.json"
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        seal["sealed_at"] = "2026-07-31T01:00:01Z"
        write_json(seal_path, seal)
        completed, envelope = self.reconcile(paths)
        self.assertEqual(20, completed.returncode)
        self.assertEqual("contract_invalid", envelope["classification"])
        self.assertFalse(paths["output"].exists())

    def test_stale_compile_report_is_rejected_before_report_publication(self) -> None:
        _, paths = self.fixture()
        report = json.loads(paths["compile_report"].read_text(encoding="utf-8"))
        report["status"] = "failed"
        write_json(paths["compile_report"], report)
        completed, envelope = self.reconcile(paths)
        self.assertEqual(20, completed.returncode)
        self.assertEqual("contract_invalid", envelope["classification"])
        self.assertFalse(paths["output"].exists())

    def test_compile_closure_duplicate_origin_missing_page_and_unsupported_object_fail_closed(self) -> None:
        mutations = {
            "compile_input_closure_exact": self._break_compile_closure,
            "OVERLAPPING_RENDERED_OBJECT_DISPOSITION": self._overlap_origin,
            "INCOMPLETE_EXTRACTION_COVERAGE": self._omit_page_coverage,
            "UNSUPPORTED_RENDERED_OBJECT_KIND": self._unsupported_object_kind,
            "rendered_page_files_current": self._tamper_rendered_page,
            "compile_recorder_current": self._tamper_compile_recorder,
        }
        for expected, mutate in mutations.items():
            with self.subTest(expected=expected):
                _, paths = self.fixture()
                mutate(paths)
                completed, envelope = self.reconcile(paths)
                self.assertEqual(20, completed.returncode)
                self.assertEqual("rendered_text_reconciliation_contract_gap", envelope["classification"])
                report = json.loads(paths["output"].read_text(encoding="utf-8"))
                observed = {item.get("code") for item in report["contract_gaps"]}
                observed.update(
                    item.get("check") for item in report["contract_gaps"]
                    if item.get("code") in {
                        "STALE_OR_MISMATCHED_INPUT",
                        "INCOMPLETE_EXTRACTION_COVERAGE",
                    }
                )
                self.assertIn(expected, observed)

    @staticmethod
    def _add_unexpected(origins: dict, rendered: dict) -> None:
        obj = {
            "object_id": "p1.stray.01", "page": 1, "object_kind": "pdf_text_run",
            "bbox": [10, 60, 100, 70], "exact_utf8_text": "内部草稿",
            "text_sha256": text_sha("内部草稿"), "extractor_id": "pdf-text-v1",
            "evidence_locator": "page:1/object:p1.stray.01",
        }
        obj["object_sha256"] = canonical_sha(obj)
        rendered["objects"].append(obj)
        origins["edges"].append({
            "edge_id": "unexpected.stray.01", "disposition": "unexpected_addition",
            "rendered_object_ids": ["p1.stray.01"], "recipe": "exact_utf8",
        })

    @staticmethod
    def _omit_paragraph(origins: dict, rendered: dict) -> None:
        origins["edges"] = [
            edge for edge in origins["edges"] if edge["edge_id"] != "origin.paragraph"
        ]
        rendered["objects"] = [
            obj for obj in rendered["objects"]
            if obj["object_id"] not in {"p1.paragraph.01a", "p1.paragraph.01b"}
        ]

    @staticmethod
    def _break_compile_closure(paths: dict[str, Path]) -> None:
        manifest = json.loads(paths["compile_manifest"].read_text(encoding="utf-8"))
        manifest["entries"].append(dict(manifest["entries"][0]))
        manifest["manifest_sha256"] = canonical_sha({k: v for k, v in manifest.items() if k != "manifest_sha256"})
        write_json(paths["compile_manifest"], manifest)
        final_seal = json.loads(paths["final_seal"].read_text(encoding="utf-8"))
        final_seal["compile_manifest_sha256"] = manifest["manifest_sha256"]
        final_seal["seal_sha256"] = canonical_sha({k: v for k, v in final_seal.items() if k != "seal_sha256"})
        write_json(paths["final_seal"], final_seal)
        report = json.loads(paths["compile_report"].read_text(encoding="utf-8"))
        report["compile_manifest_sha256"] = manifest["manifest_sha256"]
        report["final_artifact_seal_sha256"] = final_seal["seal_sha256"]
        report["report_sha256"] = canonical_sha({k: v for k, v in report.items() if k != "report_sha256"})
        write_json(paths["compile_report"], report)
        origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
        origins["final_artifact_seal_sha256"] = final_seal["seal_sha256"]
        origins["manifest_sha256"] = canonical_sha({k: v for k, v in origins.items() if k != "manifest_sha256"})
        write_json(paths["origins"], origins)
        RenderedTextReconciliationCliTests._refresh_compile_output_bindings(paths)

    @staticmethod
    def _overlap_origin(paths: dict[str, Path]) -> None:
        origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
        origins["edges"][1]["rendered_object_ids"].append("p1.title.01")
        origins["manifest_sha256"] = canonical_sha({k: v for k, v in origins.items() if k != "manifest_sha256"})
        write_json(paths["origins"], origins)
        RenderedTextReconciliationCliTests._refresh_compile_output_bindings(paths)

    @staticmethod
    def _omit_page_coverage(paths: dict[str, Path]) -> None:
        rendered = json.loads(paths["rendered"].read_text(encoding="utf-8"))
        rendered["coverage"]["page_count"] = 2
        rendered["inventory_sha256"] = canonical_sha({k: v for k, v in rendered.items() if k != "inventory_sha256"})
        write_json(paths["rendered"], rendered)
        origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
        origins["rendered_text_inventory_sha256"] = rendered["inventory_sha256"]
        origins["manifest_sha256"] = canonical_sha({k: v for k, v in origins.items() if k != "manifest_sha256"})
        write_json(paths["origins"], origins)
        RenderedTextReconciliationCliTests._refresh_compile_output_bindings(paths)

    @staticmethod
    def _unsupported_object_kind(paths: dict[str, Path]) -> None:
        rendered = json.loads(paths["rendered"].read_text(encoding="utf-8"))
        rendered["objects"][0]["object_kind"] = "raw_pdf_operator"
        rendered["objects"][0]["object_sha256"] = canonical_sha(
            {k: v for k, v in rendered["objects"][0].items() if k != "object_sha256"}
        )
        rendered["inventory_sha256"] = canonical_sha({k: v for k, v in rendered.items() if k != "inventory_sha256"})
        write_json(paths["rendered"], rendered)
        origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
        origins["rendered_text_inventory_sha256"] = rendered["inventory_sha256"]
        origins["manifest_sha256"] = canonical_sha({k: v for k, v in origins.items() if k != "manifest_sha256"})
        write_json(paths["origins"], origins)
        RenderedTextReconciliationCliTests._refresh_compile_output_bindings(paths)

    @staticmethod
    def _tamper_rendered_page(paths: dict[str, Path]) -> None:
        manifest = json.loads(paths["render_evidence"].read_text(encoding="utf-8"))
        page_path = paths["render_evidence"].parent / manifest["pages"][0]["path"]
        page_path.write_bytes(b"tampered-page")

    @staticmethod
    def _tamper_compile_recorder(paths: dict[str, Path]) -> None:
        report = json.loads(paths["compile_report"].read_text(encoding="utf-8"))
        recorder_path = paths["compile_report"].parent / report["dependency_closure"][
            "recorder_path"
        ]
        recorder_path.write_text("INPUT undeclared.tex\n", encoding="utf-8")

    @staticmethod
    def _refresh_compile_output_bindings(paths: dict[str, Path]) -> None:
        report = json.loads(paths["compile_report"].read_text(encoding="utf-8"))
        rendered = json.loads(paths["rendered"].read_text(encoding="utf-8"))
        origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
        render_evidence = json.loads(
            paths["render_evidence"].read_text(encoding="utf-8")
        )
        report["render_evidence_manifest_sha256"] = render_evidence[
            "manifest_sha256"
        ]
        report["rendered_text_inventory_sha256"] = rendered["inventory_sha256"]
        report["text_origin_manifest_sha256"] = origins["manifest_sha256"]
        report["report_sha256"] = canonical_sha(
            {key: value for key, value in report.items() if key != "report_sha256"}
        )
        write_json(paths["compile_report"], report)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import json
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import fitz


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.guarded_final_compile_adapter import render_and_derive, sha
from tests.video_workflow import test_guarded_final_compile_adapter as compile_fixture
from tests.video_workflow import test_rendered_text_reconciliation as rtr_fixture
from video2pdf_workflow_kernel.errors import CompileDependencyGap


def trace_mutating_process(mutate_trace):
    real_popen = subprocess.Popen

    class TraceMutatingProcess:
        def __init__(self, *args, **kwargs):
            self._process = real_popen(*args, **kwargs)
            candidate = Path(args[0][-1])
            self._request_path = (
                candidate if candidate.name == "compile-request.json" else None
            )

        def __getattr__(self, name):
            return getattr(self._process, name)

        def __enter__(self):
            self._process.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self._process.__exit__(exc_type, exc_value, traceback)

        def communicate(self, *args, **kwargs):
            stdout, stderr = self._process.communicate(*args, **kwargs)
            request = (
                json.loads(self._request_path.read_text(encoding="utf-8"))
                if self._request_path is not None
                else None
            )
            trace_path = (
                Path(request["output_root"]) / "text-origin-trace.json"
                if request is not None
                else None
            )
            if (
                self._process.returncode == 0
                and trace_path is not None
                and trace_path.is_file()
            ):
                trace = json.loads(trace_path.read_text(encoding="utf-8"))
                mutate_trace(trace)
                rtr_fixture.write_json(trace_path, trace)
            return stdout, stderr

    return TraceMutatingProcess


class Issue125DisplayMathOriginsTests(unittest.TestCase):
    def test_guarded_adapter_resolves_display_body_without_switching_source_file(self) -> None:
        root = Path("待删除/test-runs") / f"issue125-adapter-{uuid.uuid4().hex}"
        staging = root / "staging"
        output = root / "adapter-output"
        staging.mkdir(parents=True)
        output.mkdir()
        formula_source = staging / "section_01.tex"
        formula_source.write_text(
            "$$\n"
            "E(n)=A_i\\cdot n-A_t\\cdot\\frac{n(n-1)}{2}\n"
            "$$\n",
            encoding="utf-8",
        )
        unrelated_source = staging / "section_02.tex"
        unrelated_source.write_text(
            "unrelated\n"
            "unrelated\n"
            "E n A i t\n",
            encoding="utf-8",
        )
        pdf = root / "formula.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_font(
            fontname="issue125-unicode",
            fontfile=str(Path(r"C:\Windows\Fonts\arial.ttf")),
        )
        x = 72.0
        for text in ("E", "(", "n", ") =", "A", "i", " ·", "n", " −", "A", "t", " ·", " ", "n", "(", "n", " −", "1", ")", "2"):
            page.insert_text(
                (x, 72),
                text,
                fontname="issue125-unicode" if "−" in text else "helv",
            )
            x += max(7.0, len(text) * 8.0)
        document.save(pdf)
        document.close()

        formula_sha = sha(formula_source)
        unrelated_sha = sha(unrelated_source)
        inventory = {
            "items": [
                {
                    "item_id": "formula",
                    "declared_text": "E(n)=A_i\\cdot n-A_t\\cdot\\frac{n(n-1)}{2}",
                    "representation": "structured_text",
                    "source_artifact_logical_id": "integrated_section_01",
                    "source_generation": 2,
                    "source_sha256": formula_sha,
                },
            ]
        }
        manifest_entries = [
            {
                "logical_id": "integrated_section_01",
                "generation": 2,
                "sha256": formula_sha,
                "staging_path": formula_source.name,
            },
            {
                "logical_id": "integrated_section_02",
                "generation": 2,
                "sha256": unrelated_sha,
                "staging_path": unrelated_source.name,
            },
        ]
        xelatex = Path(r"D:\kits\MiKTex\miktex\bin\x64\xelatex.exe").resolve()

        def reverse_synctex(command, **kwargs):
            del command, kwargs
            return subprocess.CompletedProcess(
                [],
                0,
                (
                    "SyncTeX result begin\n"
                    f"Input:{formula_source.resolve()}\n"
                    "Line:3\n"
                    "Column:-1\n"
                    "SyncTeX result end\n"
                ),
                "",
            )

        with patch(
            "scripts.guarded_final_compile_adapter.subprocess.run",
            side_effect=reverse_synctex,
        ):
            objects, edges, extractor_suite, _ = render_and_derive(
                pdf,
                output,
                inventory,
                {},
                policy={
                    "policy_id": "miktex-xelatex-runtime",
                    "engine": {"executable": str(xelatex)},
                    "allowed_runtime_roots": [str(xelatex.parent)],
                },
                staging=staging,
                entry=formula_source,
                manifest_entries=manifest_entries,
                stable_final_round_auxiliaries={},
                observed_declared_paths={
                    formula_source.resolve(),
                    unrelated_source.resolve(),
                },
                runtime_environment={
                    "MIKTEX_USERLOGDIRECTORY": str(root / "miktex-logs"),
                },
            )

        formula_edge = next(
            edge for edge in edges if edge.get("sealed_item_id") == "formula"
        )
        formula_object_ids = {
            item["object_id"] for item in objects
        }
        self.assertEqual(
            formula_object_ids,
            set(formula_edge["rendered_object_ids"]),
        )
        self.assertFalse(
            any(
                source["source_path"] == str(unrelated_source.resolve())
                for edge in edges
                for source in edge.get("source_mapping", {}).get("object_sources", [])
            )
        )
        for source in formula_edge["source_mapping"]["object_sources"]:
            self.assertEqual(str(formula_source.resolve()), source["source_path"])
            self.assertEqual(2, source["line"])
            self.assertEqual(
                "compiler-display-math-span-v1", source["derivation"]
            )
            self.assertEqual(3, source["resolution"]["compiler_line"])
            self.assertEqual(
                str(formula_source.resolve()),
                source["resolution"]["compiler_source_path"],
            )
            self.assertEqual(
                {"start_line": 2, "end_line": 2},
                source["resolution"]["resolved_span"],
            )
            rendered = next(
                item for item in objects if item["object_id"] == source["object_id"]
            )
            self.assertEqual(rendered["page"], source["query"]["page"])

        reconciliation = rtr_fixture.RenderedTextReconciliationCliTests()
        reconciliation_root, paths = reconciliation.fixture()
        precompile_seal_path = (
            paths["precompile_workspace"] / "precompile-text-seal.json"
        )
        precompile_seal = json.loads(
            precompile_seal_path.read_text(encoding="utf-8")
        )
        previous_binding = (
            paths["precompile_workspace"]
            / "seal-bindings"
            / precompile_seal["seal_sha256"]
        )
        reader_inventory = json.loads(
            (previous_binding / "reader-facing-text-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        generations = json.loads(
            (previous_binding / "artifact-generations.json").read_text(encoding="utf-8")
        )
        generations["artifacts"].append(
            {
                "logical_id": "integrated_section_01",
                "generation": 2,
                "sha256": formula_sha,
            }
        )
        generations["generation_set_sha256"] = rtr_fixture.canonical_sha(
            {
                key: value
                for key, value in generations.items()
                if key != "generation_set_sha256"
            }
        )
        paragraph_item = next(
            item
            for item in reader_inventory["items"]
            if item["item_id"] == "main.paragraph.001"
        )
        formula_item = {
            "item_id": "formula",
            "kind": "equation",
            "semantic_region": "formula",
            "language_profile_id": "zh-hans",
            "source_artifact_logical_id": "integrated_section_01",
            "source_generation": 2,
            "source_sha256": formula_sha,
            "locator": "latex:formula",
            "representation": "structured_text",
            "text_sha256": rtr_fixture.text_sha(
                formula_edge["sealed_text_utf8"]
            ),
            "applicable_rule_ids": ["no_meta_writing_content"],
        }
        formula_item["item_sha256"] = rtr_fixture.canonical_sha(formula_item)
        reader_inventory["generation_set_sha256"] = generations[
            "generation_set_sha256"
        ]
        reader_inventory["items"] = [paragraph_item, formula_item]
        reader_inventory["declared_surface"] = [
            {"region_id": item["item_id"], "kind": item["kind"]}
            for item in reader_inventory["items"]
        ]
        reader_inventory["coverage_ledger"] = [
            {
                "region_id": item["item_id"],
                "item_id": item["item_id"],
                "status": "covered",
            }
            for item in reader_inventory["items"]
        ]
        reader_inventory["reader_text_set_sha256"] = rtr_fixture.canonical_sha(
            [
                {
                    "item_id": item["item_id"],
                    "kind": item["kind"],
                    "representation": item["representation"],
                    "text_sha256": item["text_sha256"],
                }
                for item in reader_inventory["items"]
            ]
        )
        reader_inventory["inventory_sha256"] = rtr_fixture.canonical_sha(
            {
                key: value
                for key, value in reader_inventory.items()
                if key != "inventory_sha256"
            }
        )
        precompile_seal.update(
            {
                "generation_set_sha256": generations["generation_set_sha256"],
                "inventory_sha256": reader_inventory["inventory_sha256"],
                "reader_text_set_sha256": reader_inventory[
                    "reader_text_set_sha256"
                ],
            }
        )
        precompile_seal["seal_sha256"] = rtr_fixture.canonical_sha(
            {
                key: value
                for key, value in precompile_seal.items()
                if key != "seal_sha256"
            }
        )
        rtr_fixture.write_json(precompile_seal_path, precompile_seal)
        current_binding = (
            paths["precompile_workspace"]
            / "seal-bindings"
            / precompile_seal["seal_sha256"]
        )
        rtr_fixture.write_json(
            current_binding / "reader-facing-text-inventory.json", reader_inventory
        )
        rtr_fixture.write_json(
            current_binding / "artifact-generations.json", generations
        )

        rendered_inventory = json.loads(paths["rendered"].read_text(encoding="utf-8"))
        rendered_inventory["extractor_suite"].extend(
            extractor
            for extractor in extractor_suite
            if extractor["extractor_id"]
            not in {
                current["extractor_id"]
                for current in rendered_inventory["extractor_suite"]
            }
        )
        rendered_inventory["objects"] = objects + [
            item
            for item in rendered_inventory["objects"]
            if item["object_id"] not in {"p1.title.01", "p1.page-number.01"}
        ]
        rendered_inventory["inventory_sha256"] = rtr_fixture.canonical_sha(
            {
                key: value
                for key, value in rendered_inventory.items()
                if key != "inventory_sha256"
            }
        )
        rtr_fixture.write_json(paths["rendered"], rendered_inventory)

        compile_manifest = json.loads(
            paths["compile_manifest"].read_text(encoding="utf-8")
        )
        compile_manifest["precompile_text_seal_sha256"] = precompile_seal[
            "seal_sha256"
        ]
        compile_manifest["entries"].append(
            {
                "logical_id": "integrated_section_01",
                "generation": 2,
                "sha256": formula_sha,
                "source_path": str(formula_source.resolve()),
                "staging_path": formula_source.name,
            }
        )
        compile_manifest["manifest_sha256"] = rtr_fixture.canonical_sha(
            {
                key: value
                for key, value in compile_manifest.items()
                if key != "manifest_sha256"
            }
        )
        rtr_fixture.write_json(paths["compile_manifest"], compile_manifest)

        final_seal = json.loads(paths["final_seal"].read_text(encoding="utf-8"))
        final_seal.update(
            {
                "precompile_text_seal_sha256": precompile_seal["seal_sha256"],
                "generation_set_sha256": generations["generation_set_sha256"],
                "compile_manifest_sha256": compile_manifest["manifest_sha256"],
            }
        )
        final_seal["seal_sha256"] = rtr_fixture.canonical_sha(
            {key: value for key, value in final_seal.items() if key != "seal_sha256"}
        )
        rtr_fixture.write_json(paths["final_seal"], final_seal)

        origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
        origins["precompile_text_seal_sha256"] = precompile_seal["seal_sha256"]
        origins["final_artifact_seal_sha256"] = final_seal["seal_sha256"]
        origins["rendered_text_inventory_sha256"] = rendered_inventory[
            "inventory_sha256"
        ]
        origins["edges"] = [
            formula_edge,
            *(edge for edge in origins["edges"][1:] if edge["disposition"] != "generated"),
        ]
        origins["manifest_sha256"] = rtr_fixture.canonical_sha(
            {key: value for key, value in origins.items() if key != "manifest_sha256"}
        )
        rtr_fixture.write_json(paths["origins"], origins)
        self.assertEqual(
            formula_edge,
            json.loads(paths["origins"].read_text(encoding="utf-8"))["edges"][0],
        )
        compile_report = json.loads(
            paths["compile_report"].read_text(encoding="utf-8")
        )
        compile_report.pop("text_origin_plan_sha256", None)
        compile_report.update(
            {
                "precompile_text_seal_sha256": precompile_seal["seal_sha256"],
                "final_artifact_seal_sha256": final_seal["seal_sha256"],
                "compile_manifest_sha256": compile_manifest["manifest_sha256"],
                "reader_facing_text_inventory_sha256": reader_inventory[
                    "inventory_sha256"
                ],
            }
        )
        compile_report["dependency_closure"]["inputs"] = [
            {
                "logical_id": entry["logical_id"],
                "generation": entry["generation"],
                "sha256": entry["sha256"],
            }
            for entry in compile_manifest["entries"]
        ]
        recorder = reconciliation_root / compile_report["dependency_closure"][
            "recorder_path"
        ]
        recorder.write_text(
            recorder.read_text(encoding="utf-8")
            + f"INPUT {formula_source.resolve()}\n",
            encoding="utf-8",
        )
        compile_report["dependency_closure"]["recorder_sha256"] = sha(recorder)
        compile_report["report_sha256"] = rtr_fixture.canonical_sha(
            {
                key: value
                for key, value in compile_report.items()
                if key != "report_sha256"
            }
        )
        rtr_fixture.write_json(paths["compile_report"], compile_report)
        reconciliation._refresh_compile_output_bindings(paths)

        completed, envelope = reconciliation.reconcile(paths)

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("rendered_text_reconciliation_passed", envelope["classification"])
        report = json.loads(paths["output"].read_text(encoding="utf-8"))
        self.assertEqual("pass", report["overall_decision"])

    def test_public_final_compile_rejects_unsupported_display_resolution(self) -> None:
        fixture = compile_fixture.GuardedFinalCompileProviderAuthorityTests(
            methodName="test_public_final_compile_allows_unread_governance_and_registered_runtime_inputs"
        )
        fixture.setUp()

        def add_unsupported_resolution(trace):
            source = trace["edges"][0]["source_mapping"]["object_sources"][0]
            source["resolution"] = {
                "kind": "display_math_span_v1",
                "compiler_source_path": source["source_path"],
                "compiler_line": source["line"],
                "compiler_column": source["column"],
                "delimiter_lines": {
                    "opening": source["line"],
                    "closing": source["line"] + 2,
                },
                "resolved_span": {
                    "start_line": source["line"] + 1,
                    "end_line": source["line"] + 1,
                },
                "supported_rendered_text": "Core claim",
            }

        # scenario_id: issue125_unsupported_display_resolution
        # target_invariant: display resolution is supported by one delimiter-bound span
        # mutation_seam: add one unsupported display-resolution record to a passing adapter package
        # rematerialized_nodes: compiler Text Origin trace
        # intentionally_stale_nodes: none
        # expected_first_gate: final_compile_source_origin_evidence
        # expected_error_code: compile_dependency_gap
        # scenario_class: single_contradiction
        with (
            patch(
                "video2pdf_workflow_kernel.final_compile.subprocess.Popen",
                trace_mutating_process(add_unsupported_resolution),
            ),
            self.assertRaisesRegex(
                CompileDependencyGap,
                "source origin evidence is unsupported or inconsistent",
            ) as raised,
        ):
            fixture._run_public_final_compile_fixture()

        self.assertEqual(
            "final_compile_source_origin_evidence",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual("compile_dependency_gap", raised.exception.data["error_code"])

    def test_public_final_compile_rejects_missing_display_resolution(self) -> None:
        fixture = compile_fixture.GuardedFinalCompileProviderAuthorityTests(
            methodName="test_public_final_compile_allows_unread_governance_and_registered_runtime_inputs"
        )
        fixture.setUp()

        def identify_display_span_without_proof(trace):
            source = trace["edges"][0]["source_mapping"]["object_sources"][0]
            source["derivation"] = "compiler-display-math-span-v1"
            source.pop("resolution", None)

        # scenario_id: issue125_missing_display_resolution
        # target_invariant: display-span derivation requires its resolution proof
        # mutation_seam: mark one passing source record as display-derived without proof
        # rematerialized_nodes: compiler Text Origin trace
        # intentionally_stale_nodes: none
        # expected_first_gate: final_compile_source_origin_evidence
        # expected_error_code: compile_dependency_gap
        # scenario_class: single_contradiction
        with (
            patch(
                "video2pdf_workflow_kernel.final_compile.subprocess.Popen",
                trace_mutating_process(identify_display_span_without_proof),
            ),
            self.assertRaisesRegex(
                CompileDependencyGap,
                "source origin evidence is unsupported or inconsistent",
            ) as raised,
        ):
            fixture._run_public_final_compile_fixture()

        self.assertEqual(
            "final_compile_source_origin_evidence",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual("compile_dependency_gap", raised.exception.data["error_code"])


if __name__ == "__main__":
    unittest.main()

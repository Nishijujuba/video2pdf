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

from scripts.guarded_final_compile_adapter import (
    _complete_compiler_source_locations,
    render_and_derive,
    sha,
)
from tests.video_workflow import test_rendered_text_reconciliation as rtr_fixture


class Issue127LeadingWrappedLineOriginTests(unittest.TestCase):
    def _source_root(self) -> Path:
        root = (
            Path("待删除/test-runs")
            / f"issue127-leading-wrap-{uuid.uuid4().hex}"
        )
        root.mkdir(parents=True)
        return root

    @staticmethod
    def _object(
        object_id: str,
        text: str,
        bbox: list[float],
        *,
        page: int = 1,
    ) -> dict:
        return {
            "object_id": object_id,
            "object_kind": "pdf_text_run",
            "page": page,
            "bbox": bbox,
            "exact_utf8_text": text,
        }

    @staticmethod
    def _location(
        object_id: str,
        source: Path,
        line: int,
        bbox: list[float],
    ) -> dict:
        return {
            "object_id": object_id,
            "source_path": str(source),
            "line": line,
            "column": -1,
            "query": {
                "page": 1,
                "x": (bbox[0] + bbox[2]) / 2,
                "y": (bbox[1] + bbox[3]) / 2,
            },
        }

    def _bind_adapter_evidence_for_reconciliation(
        self,
        *,
        paths: dict[str, Path],
        reconciliation_root: Path,
        source: Path,
        source_sha: str,
        sealed_text: str,
        objects: list[dict],
        edge: dict,
        extractor_suite: list[dict],
    ) -> None:
        precompile_seal_path = (
            paths["precompile_workspace"] / "precompile-text-seal.json"
        )
        precompile_seal = json.loads(
            precompile_seal_path.read_text(encoding="utf-8")
        )
        prior_binding = (
            paths["precompile_workspace"]
            / "seal-bindings"
            / precompile_seal["seal_sha256"]
        )
        reader_inventory = json.loads(
            (prior_binding / "reader-facing-text-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        generations = json.loads(
            (prior_binding / "artifact-generations.json").read_text(encoding="utf-8")
        )
        generations["artifacts"] = [
            {
                "logical_id": "integrated_main_tex",
                "generation": 8,
                "sha256": source_sha,
            }
        ]
        generations["generation_set_sha256"] = rtr_fixture.canonical_sha(
            {
                key: value
                for key, value in generations.items()
                if key != "generation_set_sha256"
            }
        )
        item = {
            "item_id": "main.paragraph.001",
            "kind": "paragraph",
            "semantic_region": "main.paragraph.001",
            "language_profile_id": "zh-hans",
            "source_artifact_logical_id": "integrated_main_tex",
            "source_generation": 8,
            "source_sha256": source_sha,
            "locator": "latex:main.paragraph.001",
            "representation": "structured_text",
            "text_sha256": rtr_fixture.text_sha(sealed_text),
            "applicable_rule_ids": ["no_meta_writing_content"],
        }
        item["item_sha256"] = rtr_fixture.canonical_sha(item)
        reader_inventory.update(
            {
                "generation_set_sha256": generations["generation_set_sha256"],
                "items": [item],
                "declared_surface": [
                    {"region_id": item["item_id"], "kind": item["kind"]}
                ],
                "coverage_ledger": [
                    {
                        "region_id": item["item_id"],
                        "item_id": item["item_id"],
                        "status": "covered",
                    }
                ],
            }
        )
        reader_inventory["reader_text_set_sha256"] = rtr_fixture.canonical_sha(
            [
                {
                    "item_id": item["item_id"],
                    "kind": item["kind"],
                    "representation": item["representation"],
                    "text_sha256": item["text_sha256"],
                }
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
            current_binding / "reader-facing-text-inventory.json",
            reader_inventory,
        )
        rtr_fixture.write_json(
            current_binding / "artifact-generations.json",
            generations,
        )

        rendered_inventory = json.loads(paths["rendered"].read_text(encoding="utf-8"))
        rendered_inventory["extractor_suite"] = extractor_suite
        rendered_inventory["objects"] = objects
        rendered_inventory["coverage"].update(
            page_count=1,
            pages_scanned=[1],
        )
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
        compile_manifest["entries"] = [
            {
                "logical_id": "integrated_main_tex",
                "generation": 8,
                "sha256": source_sha,
                "source_path": str(source.resolve()),
                "staging_path": source.name,
            }
        ]
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
            {
                key: value
                for key, value in final_seal.items()
                if key != "seal_sha256"
            }
        )
        rtr_fixture.write_json(paths["final_seal"], final_seal)

        origins = json.loads(paths["origins"].read_text(encoding="utf-8"))
        origins.update(
            {
                "precompile_text_seal_sha256": precompile_seal["seal_sha256"],
                "final_artifact_seal_sha256": final_seal["seal_sha256"],
                "rendered_text_inventory_sha256": rendered_inventory[
                    "inventory_sha256"
                ],
                "edges": [edge],
            }
        )
        origins["manifest_sha256"] = rtr_fixture.canonical_sha(
            {
                key: value
                for key, value in origins.items()
                if key != "manifest_sha256"
            }
        )
        rtr_fixture.write_json(paths["origins"], origins)

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
                "rendered_text_inventory_sha256": rendered_inventory[
                    "inventory_sha256"
                ],
                "text_origin_manifest_sha256": origins["manifest_sha256"],
            }
        )
        compile_report["dependency_closure"]["inputs"] = [
            {
                "logical_id": "integrated_main_tex",
                "generation": 8,
                "sha256": source_sha,
            }
        ]
        recorder = reconciliation_root / compile_report["dependency_closure"][
            "recorder_path"
        ]
        recorder.write_text(
            f"INPUT {source.resolve()}\n",
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

    def test_public_adapter_and_reconciliation_complete_leading_wrapped_line_from_following_anchor(
        self,
    ) -> None:
        reconciliation = rtr_fixture.RenderedTextReconciliationCliTests()
        reconciliation_root, paths = reconciliation.fixture()
        staging = reconciliation_root / "issue127-staging"
        output = reconciliation_root / "issue127-adapter-output"
        staging.mkdir()
        output.mkdir()
        source = staging / "section_01.tex"
        heading = "Chapter summary"
        leading = "The chapter explains how individual intent and knowledge become coordinated "
        continuation = "execution when a design team grows."
        source.write_text(
            f"{heading}\n{leading}{continuation}\n",
            encoding="utf-8",
        )
        unrelated = staging / "section_02.tex"
        unrelated.write_text("unrelated\n\n", encoding="utf-8")
        source_sha = sha(source)
        unrelated_sha = sha(unrelated)
        pdf = reconciliation_root / "issue127-leading-wrap.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), heading, fontsize=12)
        page.insert_text((72, 94), leading, fontsize=13)
        page.insert_text((72, 112), continuation, fontsize=10)
        document.save(pdf)
        document.close()
        inventory = {
            "items": [
                {
                    "item_id": "main.paragraph.001",
                    "declared_text": f"{heading}\n{leading}{continuation}",
                    "representation": "structured_text",
                    "source_artifact_logical_id": "integrated_main_tex",
                    "source_generation": 8,
                    "source_sha256": source_sha,
                }
            ]
        }
        manifest_entries = [
            {
                "logical_id": "integrated_main_tex",
                "generation": 8,
                "sha256": source_sha,
                "staging_path": source.name,
            },
            {
                "logical_id": "unrelated_section",
                "generation": 1,
                "sha256": unrelated_sha,
                "staging_path": unrelated.name,
            },
        ]
        engine = Path(r"D:\kits\MiKTex\miktex\bin\x64\xelatex.exe").resolve()

        def reverse_synctex(command, **kwargs):
            del kwargs
            query = next(value for value in command if str(value).startswith("1:"))
            y = float(str(query).split(":", 3)[2])
            if y < 80:
                candidates = [(source, 1)]
            elif y < 101:
                candidates = [(unrelated, 1), (unrelated, 2)]
            else:
                candidates = [(source, 2)]
            stdout = "SyncTeX result begin\n" + "".join(
                f"Input:{path.resolve()}\nLine:{line}\nColumn:-1\n"
                for path, line in candidates
            ) + "SyncTeX result end\n"
            return subprocess.CompletedProcess([], 0, stdout, "")

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
                    "engine": {"executable": str(engine)},
                    "allowed_runtime_roots": [str(engine.parent)],
                },
                staging=staging,
                entry=source,
                manifest_entries=manifest_entries,
                stable_final_round_auxiliaries={},
                observed_declared_paths={source.resolve(), unrelated.resolve()},
                runtime_environment={
                    "MIKTEX_USERLOGDIRECTORY": str(
                        reconciliation_root / "miktex-logs"
                    )
                },
            )

        self.assertEqual(
            1,
            len(edges),
            json.dumps(edges, ensure_ascii=False, indent=2),
        )
        edge = edges[0]
        self.assertEqual("sealed_origin", edge["disposition"])
        object_by_text = {item["exact_utf8_text"]: item for item in objects}
        leading_id = object_by_text[leading]["object_id"]
        sources = {
            item["object_id"]: item
            for item in edge["source_mapping"]["object_sources"]
        }
        self.assertEqual(str(source.resolve()), sources[leading_id]["source_path"])
        self.assertEqual(2, sources[leading_id]["line"])
        self.assertEqual(
            "compiler-line-layout-v1",
            sources[leading_id]["completion"],
        )

        self._bind_adapter_evidence_for_reconciliation(
            paths=paths,
            reconciliation_root=reconciliation_root,
            source=source,
            source_sha=source_sha,
            sealed_text=edge["sealed_text_utf8"],
            objects=objects,
            edge=edge,
            extractor_suite=extractor_suite,
        )
        reconciliation._refresh_compile_output_bindings(paths)
        completed, envelope = reconciliation.reconcile(paths)

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "rendered_text_reconciliation_passed",
            envelope["classification"],
        )
        report = json.loads(paths["output"].read_text(encoding="utf-8"))
        self.assertEqual("pass", report["overall_decision"])

    def test_leading_completion_rejects_conflict_and_excludes_unsupported_identity(
        self,
    ) -> None:
        root = self._source_root()
        source = root / "section_01.tex"
        source.write_text("Alpha leading continuation\n", encoding="utf-8")
        conflicting = root / "section_02.tex"
        conflicting.write_text("Alpha leading \n", encoding="utf-8")
        unsupported = root / "outside.tex"
        unsupported.write_text("Alpha leading\n", encoding="utf-8")
        leading_bbox = [72.0, 90.0, 525.0, 100.0]
        continuation_bbox = [72.0, 104.0, 160.0, 114.0]
        objects = [
            self._object("leading", "Alpha leading ", leading_bbox),
            self._object("continuation", "continuation", continuation_bbox),
        ]
        conflicting_location = self._location(
            "leading", conflicting, 1, leading_bbox
        )
        locations = {
            "leading": conflicting_location,
            "continuation": self._location(
                "continuation",
                source,
                1,
                continuation_bbox,
            ),
        }
        _complete_compiler_source_locations(
            objects,
            locations,
            {source, conflicting},
        )
        self.assertEqual(
            conflicting.resolve(),
            Path(locations["leading"]["source_path"]).resolve(),
        )

        unsupported_location = self._location(
            "leading", unsupported, 1, leading_bbox
        )
        locations = {
            "leading": unsupported_location,
            "continuation": self._location(
                "continuation",
                source,
                1,
                continuation_bbox,
            ),
        }
        _complete_compiler_source_locations(objects, locations, {source})
        self.assertEqual(str(source.resolve()), locations["leading"]["source_path"])
        self.assertEqual(
            "compiler-line-layout-v1",
            locations["leading"]["completion"],
        )

        locations = {"leading": unsupported_location}
        _complete_compiler_source_locations(objects, locations, {source})
        self.assertEqual(
            unsupported.resolve(),
            Path(locations["leading"]["source_path"]).resolve(),
        )
        self.assertNotIn("completion", locations["leading"])

    def test_forward_wrapped_line_completion_remains_supported(self) -> None:
        root = self._source_root()
        source = root / "section.tex"
        source.write_text("Alpha leading continuation\n", encoding="utf-8")
        leading_bbox = [72.0, 90.0, 525.0, 100.0]
        continuation_bbox = [72.0, 104.0, 160.0, 114.0]
        objects = [
            self._object("leading", "Alpha leading ", leading_bbox),
            self._object("continuation", "continuation", continuation_bbox),
        ]
        locations = {
            "leading": self._location("leading", source, 1, leading_bbox),
        }

        _complete_compiler_source_locations(objects, locations, {source})

        self.assertEqual(str(source.resolve()), locations["leading"]["source_path"])
        self.assertEqual(str(source.resolve()), locations["continuation"]["source_path"])
        self.assertEqual(1, locations["continuation"]["line"])
        self.assertEqual(
            "compiler-line-layout-v1",
            locations["continuation"]["completion"],
        )


if __name__ == "__main__":
    unittest.main()

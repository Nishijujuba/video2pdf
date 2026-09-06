from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.latex_generated_text import (
    extract_tcolorbox_invocations,
)
from video2pdf_workflow_kernel.precompile_repair_promotion import (
    PrecompileRepairPromotionProvider,
)
from scripts.guarded_final_compile_adapter import AdapterError, render_and_derive, sha


class Issue118DefaultBoxTitleMaterializationTests(unittest.TestCase):
    def test_adapter_accepts_end_line_mapping_then_rejects_one_extra_rendered_occurrence(self) -> None:
        root = Path("待删除/test-runs") / f"issue118-adapter-{uuid.uuid4().hex}"
        staging = root / "staging"
        staging.mkdir(parents=True)
        main = staging / "main.tex"
        main.write_text(
            "\\begin{dialoguebox}\nbody\n\\end{dialoguebox}\n",
            encoding="utf-8",
        )
        style = staging / "video2pdfnotes.sty"
        style.write_text(
            "\\newtcolorbox{dialoguebox}[1][]"
            "{breakable,title={Original excerpt},fonttitle=\\bfseries,#1}\n",
            encoding="utf-8",
        )
        style_sha = sha(style)
        inventory = {
            "items": [
                {
                    "item_id": "generated.local_style.box_titles",
                    "declared_text": "Original excerpt",
                    "representation": "declared_generated_text",
                    "source_artifact_logical_id": "local_style",
                    "source_generation": 1,
                    "source_sha256": style_sha,
                }
            ]
        }
        manifest_entries = [
            {
                "logical_id": "integrated_main",
                "generation": 1,
                "sha256": sha(main),
                "staging_path": "main.tex",
            },
            {
                "logical_id": "local_style",
                "generation": 1,
                "sha256": style_sha,
                "staging_path": "video2pdfnotes.sty",
            },
        ]

        def source_locations(**kwargs):
            locations = {}
            for item in kwargs["objects"]:
                bbox = item["bbox"]
                locations[item["object_id"]] = {
                    "object_id": item["object_id"],
                    "source_path": str(main),
                    "line": 3,
                    "column": 1,
                    "query": {
                        "page": 1,
                        "x": (bbox[0] + bbox[2]) / 2,
                        "y": (bbox[1] + bbox[3]) / 2,
                    },
                }
            return locations, {
                "provider_id": "fixture-source-map",
                "provider_sha256": "d" * 64,
            }

        def derive(occurrences: int, suffix: str):
            pdf = root / f"{suffix}.pdf"
            document = fitz.open()
            page = document.new_page()
            for index in range(occurrences):
                page.insert_text((72, 72 + index * 24), "Original excerpt")
            document.save(pdf)
            document.close()
            output = root / f"output-{suffix}"
            output.mkdir()
            return render_and_derive(
                pdf,
                output,
                inventory,
                {},
                policy={},
                staging=staging,
                entry=main,
                manifest_entries=manifest_entries,
                stable_final_round_auxiliaries={},
                observed_declared_paths={main.resolve(), style.resolve()},
                runtime_environment={},
            )

        with patch(
            "scripts.guarded_final_compile_adapter.compiler_source_locations",
            side_effect=source_locations,
        ):
            _, baseline_edges, _, _ = derive(1, "baseline")
            baseline = next(
                edge
                for edge in baseline_edges
                if edge.get("generator", {}).get("kind") == "latex_style_box_title"
            )
            self.assertEqual(
                [3],
                [
                    source["line"]
                    for source in baseline["generator"]["source_mapping"]["object_sources"]
                ],
            )

            # scenario_id: issue118_ambiguous_rendered_default_title
            # target_invariant: one PDF occurrence per bare invocation
            # mutation_seam: add one rendered title span after a passing baseline
            # rematerialized_nodes: rendered objects and source-map records
            # intentionally_stale_nodes: none
            # expected_first_gate: generated style title occurrence
            # expected_error_code: adapter stable message gap
            # scenario_class: single_contradiction
            with self.assertRaisesRegex(
                AdapterError,
                "generated style title occurrence is absent or ambiguous",
            ):
                derive(2, "ambiguous")

    def test_bare_and_override_invocations_materialize_one_default_with_end_boundary(self) -> None:
        invocations = extract_tcolorbox_invocations(
            "\\begin{dialoguebox}\n"
            "body\n"
            "\\end{dialoguebox}\n"
            "\\begin{dialoguebox}[title={Original excerpt (00:01--00:02)}]\n"
            "body\n"
            "\\end{dialoguebox}\n",
            {"dialoguebox"},
        )

        self.assertEqual(2, len(invocations))
        self.assertEqual((1, 3, None), (
            invocations[0].begin_line,
            invocations[0].end_line,
            invocations[0].title_override,
        ))
        self.assertEqual("Original excerpt (00:01--00:02)", invocations[1].title_override)

    def test_all_override_usage_omits_the_style_default_from_successor_inventory(self) -> None:
        run_dir = Path("待删除/test-runs") / f"issue118-{uuid.uuid4().hex}"
        run_dir.mkdir(parents=True)
        style = run_dir / "video2pdfnotes.sty"
        section = run_dir / "section.tex"
        style.write_text(
            "\\newtcolorbox{dialoguebox}[1][]"
            "{breakable,title={Original excerpt},fonttitle=\\bfseries,#1}\n",
            encoding="utf-8",
        )
        section.write_text(
            "\\begin{dialoguebox}[title={Original excerpt (00:01--00:02)}]\n"
            "body\n"
            "\\end{dialoguebox}\n",
            encoding="utf-8",
        )

        self.assertIsNone(
            PrecompileRepairPromotionProvider._tcolorbox_titles(
                source_path=style,
                run_dir=run_dir,
                manifest_entries=[{"source_path": "section.tex"}],
                locator="latex-generated:local_style/video2pdfnotes.sty/newtcolorbox-title",
                item_id="generated.local_style.box_titles",
            )
        )

    def test_ambiguous_supported_boundary_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unmatched"):
            extract_tcolorbox_invocations(
                "\\begin{dialoguebox}\n",
                {"dialoguebox"},
            )


if __name__ == "__main__":
    unittest.main()

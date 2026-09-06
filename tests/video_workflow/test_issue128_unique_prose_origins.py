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
from tests.video_workflow import test_rendered_text_reconciliation as rtr_fixture
from tests.video_workflow.test_issue127_leading_wrapped_line_origins import (
    Issue127LeadingWrappedLineOriginTests,
)


class Issue128UniqueProseOriginTests(unittest.TestCase):
    def _root(self) -> Path:
        root = (
            Path("待删除/test-runs")
            / f"issue128-unique-prose-{uuid.uuid4().hex}"
        )
        root.mkdir(parents=True)
        return root

    def test_guarded_adapter_and_public_reconciliation_complete_unique_prose_origins(
        self,
    ) -> None:
        reconciliation = rtr_fixture.RenderedTextReconciliationCliTests()
        reconciliation_root, paths = reconciliation.fixture()
        staging = reconciliation_root / "issue128-staging"
        output = reconciliation_root / "issue128-adapter-output"
        staging.mkdir()
        output.mkdir()
        source = staging / "section_01.tex"
        list_prose = "Product behavior observed during the demonstration."
        caption_prose = "Top: the sandbox policy rejects the direct write."
        paragraph_prefix = "某个命令得到"
        source.write_text(
            "\n".join(
                (
                    rf"\item \textbf{{Analysis}}: {list_prose}",
                    "Analysis appears elsewhere, so the short label is ambiguous.",
                    rf"\caption{{{caption_prose}}}",
                    f"{paragraph_prefix} access denied, and the operation stops.",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        source_sha = sha(source)
        decoy_one = staging / "decoy_one.tex"
        decoy_two = staging / "decoy_two.tex"
        decoy_one.write_text("irrelevant compiler candidate\n", encoding="utf-8")
        decoy_two.write_text("another irrelevant compiler candidate\n", encoding="utf-8")
        pdf = reconciliation_root / "issue128-unique-prose.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Analysis", fontsize=11)
        page.insert_text((117, 72), ": ", fontsize=11)
        page.insert_text((127, 72), list_prose, fontsize=11)
        page.insert_text((72, 100), "Figure 10:", fontsize=11)
        page.insert_text((130, 100), caption_prose, fontsize=11)
        page.insert_text((72, 130), paragraph_prefix, fontsize=11, fontname="china-s")
        page.insert_text((150, 130), " access de-", fontsize=11)
        page.insert_text((72, 148), "nied, and the operation stops.", fontsize=11)
        document.save(pdf)
        document.close()
        declared_text = (
            f"Analysis: {list_prose}"
            f"Figure 10:{caption_prose}"
            f"{paragraph_prefix} access denied, and the operation stops."
        )
        inventory = {
            "items": [
                {
                    "item_id": "main.paragraph.001",
                    "declared_text": declared_text,
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
            }
        ]
        engine = Path(r"D:\kits\MiKTex\miktex\bin\x64\xelatex.exe").resolve()

        def ambiguous_synctex(command, **kwargs):
            del command, kwargs
            stdout = "SyncTeX result begin\n" + "".join(
                f"Input:{path.resolve()}\nLine:1\nColumn:-1\n"
                for path in (decoy_one, decoy_two)
            ) + "SyncTeX result end\n"
            return subprocess.CompletedProcess([], 0, stdout, "")

        with patch(
            "scripts.guarded_final_compile_adapter.subprocess.run",
            side_effect=ambiguous_synctex,
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
                observed_declared_paths={source.resolve()},
                runtime_environment={
                    "MIKTEX_USERLOGDIRECTORY": str(
                        reconciliation_root / "miktex-logs"
                    )
                },
            )

        self.assertEqual(1, len(edges), json.dumps(edges, ensure_ascii=False, indent=2))
        edge = edges[0]
        object_by_text = {item["exact_utf8_text"]: item for item in objects}
        sources = {
            item["object_id"]: item
            for item in edge["source_mapping"]["object_sources"]
        }
        for text, line in (
            (f": {list_prose}", 1),
            (caption_prose, 3),
            (paragraph_prefix, 4),
        ):
            self.assertIn(text, object_by_text, json.dumps(objects, ensure_ascii=False))
            location = sources[object_by_text[text]["object_id"]]
            self.assertEqual(str(source.resolve()), location["source_path"])
            self.assertEqual(line, location["line"])
            self.assertEqual("compiler-source-text-v1", location["completion"])
        for text, line in (("Analysis", 1), ("Figure 10:", 3), (" access de-", 4)):
            location = sources[object_by_text[text]["object_id"]]
            self.assertEqual(line, location["line"])
            self.assertEqual("compiler-line-layout-v1", location["completion"])

        Issue127LeadingWrappedLineOriginTests()._bind_adapter_evidence_for_reconciliation(
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

    def test_guarded_adapter_rejects_nonunique_unsupported_and_display_prose_seeds(
        self,
    ) -> None:
        # scenario_id: issue128_unique_prose_seed_boundaries
        # target_invariant: a prose seed has one authenticated non-display path/line identity
        # mutation_seam: rendered runs with duplicate, unsupported, display-only, and
        #   prose-plus-display source matches share one public adapter invocation
        # rematerialized_nodes: PDF, authenticated source closure, compiler origin trace
        # intentionally_stale_nodes: none
        # expected_first_gate: provider unique prose source completion
        # expected_error_code: unexpected_addition
        # scenario_class: precedence
        root = self._root()
        staging = root / "staging"
        output = root / "adapter-output"
        staging.mkdir()
        output.mkdir()
        source = staging / "section_01.tex"
        source.write_text(
            "\n".join(
                (
                    "Duplicate prose run.",
                    "$$",
                    "Formula-only run.",
                    "Shared display competitor.",
                    "$$",
                    r"$$\text{Same-line display phrase.}$$",
                    r"\[",
                    r"\text{Bracket display phrase.}",
                    r"\]",
                    r"\begin{equation}",
                    r"\text{Equation display phrase.}",
                    r"\end{equation}",
                    r"\begin{align*}",
                    r"\text{Align star display phrase.} &= x",
                    r"\end{align*}",
                    r"$$\text{Same-line display competitor.}$$",
                    r"Ordinary formatted prose.\\[0.5em]",
                    r"Comment-safe prose. % $$ \[ \begin{align*}",
                    "Control anchor.",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        duplicate = staging / "section_02.tex"
        duplicate.write_text("Duplicate prose run.\n", encoding="utf-8")
        prose_competitor = staging / "section_03.tex"
        prose_competitor.write_text(
            "Shared display competitor.\nSame-line display competitor.\n",
            encoding="utf-8",
        )
        unsupported = staging / "unsupported.txt"
        unsupported.write_text("Unsupported source run.\n", encoding="utf-8")
        decoy_one = staging / "decoy_one.tex"
        decoy_two = staging / "decoy_two.tex"
        decoy_one.write_text("irrelevant candidate one\n", encoding="utf-8")
        decoy_two.write_text("irrelevant candidate two\n", encoding="utf-8")
        source_sha = sha(source)
        duplicate_sha = sha(duplicate)
        competitor_sha = sha(prose_competitor)
        pdf = root / "issue128-rejected-seeds.pdf"
        document = fitz.open()
        page = document.new_page()
        rendered_texts = (
            "Duplicate prose run.",
            "Formula-only run.",
            "Shared display competitor.",
            "Same-line display phrase.",
            "Bracket display phrase.",
            "Equation display phrase.",
            "Align star display phrase.",
            "Same-line display competitor.",
            "Unsupported source run.",
            "Ordinary formatted prose.",
            "Comment-safe prose.",
            "Control anchor.",
        )
        for index, text in enumerate(rendered_texts):
            page.insert_text((72, 72 + index * 28), text, fontsize=11)
        document.save(pdf)
        document.close()

        def ambiguous_synctex(command, **kwargs):
            del command, kwargs
            stdout = "SyncTeX result begin\n" + "".join(
                f"Input:{path.resolve()}\nLine:1\nColumn:-1\n"
                for path in (decoy_one, decoy_two)
            ) + "SyncTeX result end\n"
            return subprocess.CompletedProcess([], 0, stdout, "")

        manifest_entries = [
            {
                "logical_id": logical_id,
                "generation": generation,
                "sha256": digest,
                "staging_path": path.name,
            }
            for logical_id, generation, digest, path in (
                ("integrated_main_tex", 8, source_sha, source),
                ("duplicate_section", 1, duplicate_sha, duplicate),
                ("prose_competitor", 1, competitor_sha, prose_competitor),
            )
        ]
        engine = Path(r"D:\kits\MiKTex\miktex\bin\x64\xelatex.exe").resolve()
        with patch(
            "scripts.guarded_final_compile_adapter.subprocess.run",
            side_effect=ambiguous_synctex,
        ):
            objects, edges, _, _ = render_and_derive(
                pdf,
                output,
                {
                    "items": [
                        {
                            "item_id": "main.paragraph.001",
                            "declared_text": "".join(rendered_texts),
                            "representation": "structured_text",
                            "source_artifact_logical_id": "integrated_main_tex",
                            "source_generation": 8,
                            "source_sha256": source_sha,
                        }
                    ]
                },
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
                observed_declared_paths={
                    source.resolve(),
                    duplicate.resolve(),
                    prose_competitor.resolve(),
                },
                runtime_environment={"MIKTEX_USERLOGDIRECTORY": str(root / "logs")},
            )

        sealed_edges = [edge for edge in edges if edge["disposition"] == "sealed_origin"]
        self.assertEqual(
            1,
            len(sealed_edges),
            json.dumps(edges, ensure_ascii=False, indent=2),
        )
        object_by_text = {item["exact_utf8_text"]: item for item in objects}
        mapped_ids = set(sealed_edges[0]["rendered_object_ids"])
        for text in (
            "Ordinary formatted prose.",
            "Comment-safe prose.",
            "Control anchor.",
        ):
            self.assertIn(object_by_text[text]["object_id"], mapped_ids)
        for text in rendered_texts[:-3]:
            self.assertNotIn(object_by_text[text]["object_id"], mapped_ids)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import hashlib
import sys
import unittest
import uuid
from unittest.mock import patch

import fitz
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.final_compile import (
    registered_generator_identity,
    validate_latex_running_header,
)
from scripts.guarded_final_compile_adapter import render_and_derive, sha


class Issue120MixedScriptRunningHeaderTests(unittest.TestCase):
    def test_adapter_groups_source_mapped_english_span_with_the_generated_header(self) -> None:
        root = Path("待删除/test-runs") / f"issue120-adapter-{uuid.uuid4().hex}"
        staging = root / "staging"
        output = root / "output"
        staging.mkdir(parents=True)
        output.mkdir()
        main = staging / "main.tex"
        title = "OS SANDBOX（操作系统沙箱）：强制执行仍需要闭合的策略生命周期"
        main.write_text(f"Body\n\\section{{{title}}}\n", encoding="utf-8")
        toc = root / "main.toc"
        toc.write_text("", encoding="utf-8")
        pdf = root / "document.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Body")
        page.insert_text((60, 30), "4 ")
        page.insert_text((74, 30), "OS SANDBOX", fontname="courier")
        page.insert_text(
            (145, 30),
            "（操作系统沙箱）：强制执行仍需要闭合的策略生命周期",
            fontname="china-s",
        )
        page.insert_text((510, 30), "1")
        document.save(pdf)
        document.close()
        main_sha = sha(main)
        inventory = {
            "items": [
                {
                    "item_id": "main",
                    "declared_text": main.read_text(encoding="utf-8"),
                    "representation": "structured_text",
                    "source_artifact_logical_id": "integrated_main",
                    "source_generation": 1,
                    "source_sha256": main_sha,
                }
            ]
        }
        manifest_entries = [
            {
                "logical_id": "integrated_main",
                "generation": 1,
                "sha256": main_sha,
                "staging_path": "main.tex",
            }
        ]

        def source_locations(**kwargs):
            locations = {}
            for item in kwargs["objects"]:
                text = item["exact_utf8_text"]
                if text not in {"Body", "OS SANDBOX"}:
                    continue
                bbox = item["bbox"]
                locations[item["object_id"]] = {
                    "object_id": item["object_id"],
                    "source_path": str(main),
                    "line": 1 if text == "Body" else 2,
                    "column": 1,
                    "query": {
                        "page": 1,
                        "x": (bbox[0] + bbox[2]) / 2,
                        "y": (bbox[1] + bbox[3]) / 2,
                    },
                    **(
                        {"completion": "compiler-line-layout-v1"}
                        if text == "OS SANDBOX"
                        else {}
                    ),
                }
            return locations, {
                "provider_id": "fixture-source-map",
                "provider_sha256": "c" * 64,
            }

        with patch(
            "scripts.guarded_final_compile_adapter.compiler_source_locations",
            side_effect=source_locations,
        ):
            objects, edges, _, _ = render_and_derive(
                pdf,
                output,
                inventory,
                {},
                policy={},
                staging=staging,
                entry=main,
                manifest_entries=manifest_entries,
                stable_final_round_auxiliaries={toc.resolve(): sha(toc)},
                observed_declared_paths={main.resolve()},
                runtime_environment={},
            )

        objects_by_id = {item["object_id"]: item for item in objects}
        header_edge = next(
            edge
            for edge in edges
            if edge.get("generator", {}).get("kind") == "latex_running_header"
        )
        header_texts = [
            objects_by_id[object_id]["exact_utf8_text"]
            for object_id in header_edge["rendered_object_ids"]
        ]
        self.assertIn("OS SANDBOX", header_texts)
        self.assertEqual(
            {
                item["object_id"]
                for item in objects
                if item["bbox"][3] <= 45
            },
            set(header_edge["rendered_object_ids"]),
        )
        english_source = next(
            value
            for value in header_edge["generator"]["source_mapping"]["object_sources"]
            if objects_by_id[value["object_id"]]["exact_utf8_text"] == "OS SANDBOX"
        )
        self.assertEqual("compiler-line-layout-v1", english_source["completion"])

    def _fixture(self) -> tuple[dict, dict, list[dict], list[str]]:
        test_root = Path("待删除/test-runs") / f"issue120-{uuid.uuid4().hex}"
        test_root.mkdir(parents=True)
        toc = test_root / "main.toc"
        title = "OS SANDBOX（操作系统沙箱）：强制执行仍需要闭合的策略生命周期"
        toc.write_text(
            "\\contentsline {section}{\\numberline {1}First}{1}{section.1}%\n"
            "\\contentsline {section}{\\numberline {2}Second}{5}{section.2}%\n"
            "\\contentsline {section}{\\numberline {3}Third}{10}{section.3}%\n"
            f"\\contentsline {{section}}{{\\numberline {{4}}{title}}}{{16}}{{section.4}}%\n",
            encoding="utf-8",
        )
        generator = {
            **registered_generator_identity("latex-running-header-v1"),
            "inputs": {
                "page_count": 17,
                "toc_source_path": str(toc),
                "toc_source_sha256": hashlib.sha256(toc.read_bytes()).hexdigest(),
                "final_pdf_sha256": "b" * 64,
                "pdf_page_labels": [str(index) for index in range(1, 18)],
            },
        }
        objects = {
            "number": {"object_id": "number", "page": 17, "bbox": [60, 20, 72, 35], "exact_utf8_text": "4 "},
            "english": {"object_id": "english", "page": 17, "bbox": [72, 20, 160, 35], "exact_utf8_text": "OS SANDBOX"},
            "cjk": {"object_id": "cjk", "page": 17, "bbox": [160, 20, 490, 35], "exact_utf8_text": "（操作系统沙箱）：强制执行仍需要闭合的策略生命周期"},
            "folio": {"object_id": "folio", "page": 17, "bbox": [510, 20, 530, 35], "exact_utf8_text": "17"},
        }
        sealed = [{
            "representation": "structured_text",
            "exact_utf8_text": (
                "\\section{First}\n\\section{Second}\n\\section{Third}\n"
                f"\\section{{{title}}}"
            ),
        }]
        return generator, objects, sealed, list(objects)

    def test_mixed_script_header_keeps_all_fragments_under_complete_title_authority(self) -> None:
        generator, objects, sealed, rendered_ids = self._fixture()

        self.assertTrue(validate_latex_running_header(generator, objects, rendered_ids, sealed, 17))

    def test_wrong_mixed_script_header_title_remains_rejected(self) -> None:
        generator, objects, sealed, rendered_ids = self._fixture()
        objects["english"]["exact_utf8_text"] = "OS CONTAINER"

        self.assertFalse(validate_latex_running_header(generator, objects, rendered_ids, sealed, 17))


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.guarded_final_compile_adapter import (
    _complete_compiler_source_locations,
)


class Issue122BodyOriginCompletionTests(unittest.TestCase):
    def _source_root(self) -> Path:
        root = Path("待删除/test-runs") / f"issue122-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        return root

    @staticmethod
    def _location(object_id: str, source: Path, line: int, bbox: list[float]) -> dict:
        return {
            "object_id": object_id,
            "source_path": str(source),
            "line": line,
            "column": 1,
            "query": {
                "page": 1,
                "x": (bbox[0] + bbox[2]) / 2,
                "y": (bbox[1] + bbox[3]) / 2,
            },
            "completion": "compiler-line-layout-v1",
        }

    def test_centered_continuation_uses_unique_joined_source_line(self) -> None:
        root = self._source_root()
        source = root / "section.tex"
        source.write_text(
            "\\par\\small Source (generated\\_diagram): teaching redraw of the "
            "conditional WSL boundary from the source Q\\&A at 00:31:43--00:33:24\n",
            encoding="utf-8",
        )
        objects = [
            {"object_id": "first-a", "page": 1, "bbox": [74, 316, 205, 326], "exact_utf8_text": "Source (generated_diagram):"},
            {"object_id": "first-b", "page": 1, "bbox": [205, 316, 209, 326], "exact_utf8_text": " "},
            {"object_id": "first-c", "page": 1, "bbox": [209, 316, 521, 326], "exact_utf8_text": "teaching redraw of the conditional WSL boundary from the"},
            {"object_id": "continuation", "page": 1, "bbox": [222, 332, 373, 342], "exact_utf8_text": "source Q&A at 00:31:43–00:33:24"},
        ]
        locations = {
            item["object_id"]: self._location(item["object_id"], source, 1, item["bbox"])
            for item in objects[:3]
        }

        _complete_compiler_source_locations(objects, locations, {source})

        self.assertEqual(1, locations["continuation"]["line"])
        self.assertEqual("compiler-line-layout-v1", locations["continuation"]["completion"])

    def test_centered_continuation_rejects_ambiguous_joined_source(self) -> None:
        root = self._source_root()
        source = root / "section.tex"
        line = (
            "\\par\\small Source (generated\\_diagram): teaching redraw of the "
            "conditional WSL boundary from the source Q\\&A at 00:31:43--00:33:24"
        )
        source.write_text(line + "\n" + line + "\n", encoding="utf-8")
        objects = [
            {"object_id": "first", "page": 1, "bbox": [74, 316, 521, 326], "exact_utf8_text": "Source (generated_diagram): teaching redraw of the conditional WSL boundary from the"},
            {"object_id": "continuation", "page": 1, "bbox": [222, 332, 373, 342], "exact_utf8_text": "source Q&A at 00:31:43–00:33:24"},
        ]
        locations = {
            "first": self._location("first", source, 1, objects[0]["bbox"]),
        }

        # scenario_id: issue122_ambiguous_centered_wrap_source
        # target_invariant: joined rendered wrap has one authenticated source line
        # mutation_seam: duplicate the otherwise valid authority line
        # rematerialized_nodes: source lines and existing compiler anchor
        # intentionally_stale_nodes: none
        # expected_first_gate: compiler source completion
        # expected_error_code: remains_unassigned
        # scenario_class: single_contradiction
        _complete_compiler_source_locations(objects, locations, {source})

        self.assertNotIn("continuation", locations)

    def test_dialoguebox_end_anchor_completes_exact_title_punctuation(self) -> None:
        root = self._source_root()
        style = root / "video2pdfnotes.sty"
        style.write_text(
            "\\newtcolorbox{dialoguebox}[1][]"
            "{breakable,title={Original excerpt},#1}\n",
            encoding="utf-8",
        )
        source = root / "section.tex"
        source.write_text(
            "\\begin{dialoguebox}\n"
            "\\textbf{问答要点（00:31:43--00:32:42）：}\n"
            "听众：使用 WSL 是否意味着所有东西都在 VM 中？\n"
            "\\end{dialoguebox}\n",
            encoding="utf-8",
        )
        objects = [
            {"object_id": "left", "page": 1, "bbox": [78, 463, 225, 474], "exact_utf8_text": "问答要点（00:31:43–00:32:42"},
            {"object_id": "punctuation", "page": 1, "bbox": [225, 463, 241, 474], "exact_utf8_text": "）："},
            {"object_id": "right", "page": 1, "bbox": [241, 463, 442, 474], "exact_utf8_text": "听众：使用 WSL 是否意味着所有东西都在 VM 中？"},
        ]
        locations = {
            "left": self._location("left", source, 4, objects[0]["bbox"]),
            "right": self._location("right", source, 4, objects[2]["bbox"]),
        }
        for location in locations.values():
            location.pop("completion")

        _complete_compiler_source_locations(objects, locations, {source, style})

        self.assertEqual(4, locations["punctuation"]["line"])
        self.assertEqual("compiler-line-layout-v1", locations["punctuation"]["completion"])

    def test_dialoguebox_end_anchor_rejects_title_contradiction(self) -> None:
        root = self._source_root()
        style = root / "video2pdfnotes.sty"
        style.write_text(
            "\\newtcolorbox{dialoguebox}[1][]"
            "{breakable,title={Original excerpt},#1}\n",
            encoding="utf-8",
        )
        source = root / "section.tex"
        source.write_text(
            "\\begin{dialoguebox}\n"
            "\\textbf{问答要点（00:31:43--00:32:42】}\n"
            "听众：使用 WSL 是否意味着所有东西都在 VM 中？\n"
            "\\end{dialoguebox}\n",
            encoding="utf-8",
        )
        objects = [
            {"object_id": "left", "page": 1, "bbox": [78, 463, 225, 474], "exact_utf8_text": "问答要点（00:31:43–00:32:42"},
            {"object_id": "punctuation", "page": 1, "bbox": [225, 463, 241, 474], "exact_utf8_text": "）："},
            {"object_id": "right", "page": 1, "bbox": [241, 463, 442, 474], "exact_utf8_text": "听众：使用 WSL 是否意味着所有东西都在 VM 中？"},
        ]
        locations = {
            "left": self._location("left", source, 4, objects[0]["bbox"]),
            "right": self._location("right", source, 4, objects[2]["bbox"]),
        }
        for location in locations.values():
            location.pop("completion")

        # scenario_id: issue122_dialogue_title_contradiction
        # target_invariant: rendered title prefix equals the paired invocation title
        # mutation_seam: change only the source title punctuation
        # rematerialized_nodes: paired invocation boundary
        # intentionally_stale_nodes: none
        # expected_first_gate: compiler source completion
        # expected_error_code: remains_unassigned
        # scenario_class: single_contradiction
        _complete_compiler_source_locations(objects, locations, {source, style})

        self.assertNotIn("punctuation", locations)


if __name__ == "__main__":
    unittest.main()

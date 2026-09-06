from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.guarded_final_compile_adapter import (
    DISPLAY_MATH_DERIVATION,
    _complete_compiler_source_locations,
)
from video2pdf_workflow_kernel.final_compile import (
    display_math_resolution_is_valid,
    resolve_display_math_source,
)


class Issue126DisplayResolutionIndexTests(unittest.TestCase):
    def _complete_document(
        self,
        *,
        line_count: int,
        include_display: bool,
    ) -> tuple[int, list[dict], dict[str, dict], Path]:
        root = (
            Path("待删除/test-runs")
            / f"issue126-resolution-index-{uuid.uuid4().hex}"
        )
        root.mkdir(parents=True)
        source = root / "section.tex"
        source_lines = [f"Body{line_number:03d}" for line_number in range(line_count)]
        if include_display:
            source_lines = ["$$", "FormulaE=mc2", "$$", *source_lines]
        source.write_text("\n".join(source_lines) + "\n", encoding="utf-8")

        objects: list[dict] = []
        locations: dict[str, dict] = {}
        visible_lines = [
            (line_number + (4 if include_display else 1), "Body", f"{line_number:03d}")
            for line_number in range(line_count)
        ]
        if include_display:
            visible_lines.insert(0, (2, "Formula", "E=mc2"))
        for visual_line, (source_line, prefix, suffix) in enumerate(visible_lines, 1):
            y = 20.0 + visual_line * 4.0
            anchor_id = f"line-{visual_line}-anchor"
            body_id = f"line-{visual_line}-body"
            anchor = {
                "object_id": anchor_id,
                "object_kind": "pdf_text_run",
                "page": 1,
                "bbox": [20.0, y, 50.0, y + 3.0],
                "exact_utf8_text": prefix,
            }
            body = {
                "object_id": body_id,
                "object_kind": "pdf_text_run",
                "page": 1,
                "bbox": [50.0, y, 80.0, y + 3.0],
                "exact_utf8_text": suffix,
            }
            objects.extend((anchor, body))
            locations[anchor_id] = {
                "object_id": anchor_id,
                "source_path": str(source),
                "line": source_line,
                "column": -1,
                "query": {"page": 1, "x": 35.0, "y": y + 1.5},
            }

        if include_display:
            resolution = resolve_display_math_source(
                source,
                compiler_line=3,
                compiler_column=-1,
                rendered_text="Formula",
            )
            self.assertIsNotNone(resolution)
            locations["line-1-anchor"].update(
                {
                    "derivation": DISPLAY_MATH_DERIVATION,
                    "resolution": resolution,
                }
            )

        real_resolve = Path.resolve
        resolve_count = 0

        def counted_resolve(path: Path, *args, **kwargs) -> Path:
            nonlocal resolve_count
            resolve_count += 1
            return real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", counted_resolve):
            _complete_compiler_source_locations(objects, locations, {source})

        return resolve_count, objects, locations, source.resolve()

    def test_sparse_display_proof_completion_has_linear_source_identity_work(self) -> None:
        small_count, _, _, _ = self._complete_document(
            line_count=8,
            include_display=True,
        )
        large_count, objects, locations, source = self._complete_document(
            line_count=32,
            include_display=True,
        )

        self.assertLessEqual(large_count - small_count, 8 * (66 - 18))
        self.assertEqual(66, len(locations))
        for obj in objects:
            location = locations[obj["object_id"]]
            self.assertEqual(str(source), location["source_path"])
            self.assertEqual(obj["page"], location["query"]["page"])
            if obj["object_id"].startswith("line-1-"):
                self.assertEqual(DISPLAY_MATH_DERIVATION, location["derivation"])
                self.assertEqual(
                    {"start_line": 2, "end_line": 2},
                    location["resolution"]["resolved_span"],
                )
                self.assertEqual(
                    obj["exact_utf8_text"],
                    location["resolution"]["supported_rendered_text"],
                )
                self.assertTrue(
                    display_math_resolution_is_valid(
                        source,
                        location["resolution"],
                        rendered_text=obj["exact_utf8_text"],
                    )
                )
            else:
                self.assertNotIn("resolution", location)
                self.assertNotIn("derivation", location)

    def test_no_display_completion_has_linear_source_identity_work(self) -> None:
        small_count, _, _, _ = self._complete_document(
            line_count=8,
            include_display=False,
        )
        large_count, objects, locations, source = self._complete_document(
            line_count=32,
            include_display=False,
        )

        self.assertLessEqual(large_count - small_count, 8 * (64 - 16))
        self.assertEqual(64, len(locations))
        self.assertEqual(
            {str(source)},
            {location["source_path"] for location in locations.values()},
        )
        self.assertFalse(
            any("resolution" in locations[obj["object_id"]] for obj in objects)
        )

    def test_conflicting_display_resolutions_remain_ambiguous(self) -> None:
        root = (
            Path("待删除/test-runs")
            / f"issue126-ambiguous-resolution-{uuid.uuid4().hex}"
        )
        root.mkdir(parents=True)
        source = root / "section.tex"
        source.write_text("$$\nFormulaE=mc2\n$$\n", encoding="utf-8")
        objects = [
            {
                "object_id": "opening-anchor",
                "object_kind": "pdf_text_run",
                "page": 1,
                "bbox": [20.0, 20.0, 50.0, 23.0],
                "exact_utf8_text": "Formula",
            },
            {
                "object_id": "closing-anchor",
                "object_kind": "pdf_text_run",
                "page": 1,
                "bbox": [50.0, 20.0, 65.0, 23.0],
                "exact_utf8_text": "E=",
            },
            {
                "object_id": "body",
                "object_kind": "pdf_text_run",
                "page": 1,
                "bbox": [65.0, 20.0, 80.0, 23.0],
                "exact_utf8_text": "mc2",
            },
        ]
        locations = {}
        for object_id, compiler_line in (("opening-anchor", 1), ("closing-anchor", 3)):
            obj = next(item for item in objects if item["object_id"] == object_id)
            resolution = resolve_display_math_source(
                source,
                compiler_line=compiler_line,
                compiler_column=-1,
                rendered_text=obj["exact_utf8_text"],
            )
            self.assertIsNotNone(resolution)
            locations[object_id] = {
                "object_id": object_id,
                "source_path": str(source),
                "line": 2,
                "column": -1,
                "query": {
                    "page": 1,
                    "x": (obj["bbox"][0] + obj["bbox"][2]) / 2,
                    "y": (obj["bbox"][1] + obj["bbox"][3]) / 2,
                },
                "derivation": DISPLAY_MATH_DERIVATION,
                "resolution": resolution,
            }

        _complete_compiler_source_locations(objects, locations, {source})

        self.assertEqual(set(item["object_id"] for item in objects), set(locations))
        self.assertTrue(
            all("resolution" not in location for location in locations.values())
        )
        self.assertTrue(
            all("derivation" not in location for location in locations.values())
        )


if __name__ == "__main__":
    unittest.main()

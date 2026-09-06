from pathlib import Path
import hashlib
import sys
import unittest
import uuid

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.final_compile import (
    pdf_page_labels,
    registered_generator_identity,
    validate_latex_running_header,
)


class Issue119RunningHeaderPageLabelsTests(unittest.TestCase):
    def test_pdf_page_labels_preserve_explicit_reset_and_implicit_numbering(self) -> None:
        implicit = fitz.open()
        for _ in range(3):
            implicit.new_page()
        self.assertEqual(["1", "2", "3"], pdf_page_labels(implicit))
        implicit.close()

        explicit = fitz.open()
        for _ in range(4):
            explicit.new_page()
        explicit.set_page_labels(
            [
                {"startpage": 0, "prefix": "", "firstpagenum": 1, "style": "D"},
                {"startpage": 1, "prefix": "", "firstpagenum": 1, "style": "D"},
            ]
        )
        self.assertEqual(["1", "1", "2", "3"], pdf_page_labels(explicit))
        explicit.close()

    def _fixture(self) -> tuple[dict, dict, list[dict], list[str]]:
        test_root = Path("待删除/test-runs") / f"issue119-{uuid.uuid4().hex}"
        test_root.mkdir(parents=True)
        toc = test_root / "main.toc"
        toc.write_text(
            "\\contentsline {section}{\\numberline {1}First}{3}{section.1}%\n",
            encoding="utf-8",
        )
        labels = ["1", "1", "2", "3", "4"]
        generator = {
            **registered_generator_identity("latex-running-header-v1"),
            "inputs": {
                "page_count": 5,
                "toc_source_path": str(toc),
                "toc_source_sha256": hashlib.sha256(toc.read_bytes()).hexdigest(),
                "final_pdf_sha256": "a" * 64,
                "pdf_page_labels": labels,
            },
        }
        objects = {
            "front-left": {"object_id": "front-left", "page": 2, "bbox": [60, 20, 200, 35], "exact_utf8_text": "目录"},
            "front-right": {"object_id": "front-right", "page": 2, "bbox": [510, 20, 530, 35], "exact_utf8_text": "1"},
            "body-left-1": {"object_id": "body-left-1", "page": 4, "bbox": [60, 20, 300, 35], "exact_utf8_text": "1 FIRST"},
            "body-right-1": {"object_id": "body-right-1", "page": 4, "bbox": [510, 20, 530, 35], "exact_utf8_text": "3"},
            "body-left-2": {"object_id": "body-left-2", "page": 5, "bbox": [60, 20, 300, 35], "exact_utf8_text": "1 FIRST"},
            "body-right-2": {"object_id": "body-right-2", "page": 5, "bbox": [510, 20, 530, 35], "exact_utf8_text": "4"},
        }
        sealed = [{"representation": "structured_text", "exact_utf8_text": "\\section{First}"}]
        return generator, objects, sealed, list(objects)

    def test_pagelabel_reset_then_continued_body_numbering_is_authoritative(self) -> None:
        generator, objects, sealed, rendered_ids = self._fixture()

        self.assertTrue(validate_latex_running_header(generator, objects, rendered_ids, sealed, 5))

    def test_one_folio_label_contradiction_fails_at_label_gate(self) -> None:
        generator, objects, sealed, rendered_ids = self._fixture()
        objects["body-right-2"]["exact_utf8_text"] = "5"

        self.assertFalse(validate_latex_running_header(generator, objects, rendered_ids, sealed, 5))


if __name__ == "__main__":
    unittest.main()

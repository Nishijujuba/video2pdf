from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
import unittest
import uuid

import fitz
from PIL import Image, ImageDraw

from scripts.guarded_final_compile_adapter import AdapterError, render_and_derive
from tests.video_workflow._test_run import module_test_root


PROJECT = Path(__file__).resolve().parents[2]
CALRGB = (
    "[/CalRGB<</WhitePoint[.95046 1 1.08906]/Gamma[2.2 2.2 2.2]"
    "/Matrix[.41239 .21264 .01933 .35758 .71517 .11919 .18048 .07219 .95053]>>]"
)


class RgbAlphaRasterIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = module_test_root(PROJECT) / f"rgb-alpha-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        self.source = self.root / "figure.png"
        self.image = Image.new("RGBA", (100, 70), (32, 130, 200, 255))
        ImageDraw.Draw(self.image).text((20, 25), "Q&A", fill=(0, 0, 0, 255))
        self.image.save(self.source)
        self.entry = self.root / "main.tex"
        self.entry.write_text(r"\includegraphics{figure.png}" + "\n", encoding="utf-8")
        self.source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.binding = ("figure", 1, self.source_sha)

    def _compiled_image(self, image: Image.Image, *, calibrated: bool) -> Path:
        stream = BytesIO()
        image.save(stream, format="PNG")
        pdf = self.root / f"compiled-{uuid.uuid4().hex}.pdf"
        with fitz.open() as document:
            page = document.new_page(width=240, height=180)
            xref = page.insert_image(fitz.Rect(30, 30, 210, 156), stream=stream.getvalue())
            if calibrated:
                document.xref_set_key(xref, "ColorSpace", CALRGB)
            document.save(pdf)
        return pdf

    def _derive(self, pdf: Path) -> tuple:
        # The staged PNG owns the declared binding; the real PDF owns observed
        # pixels, mask, page and rectangle. No operator-authored origin plan.
        output = self.root / f"observed-{uuid.uuid4().hex}"
        output.mkdir()
        return render_and_derive(
            pdf, output,
            {"items": [{
                "item_id": "raster.figure", "representation": "authoritative_raster_text",
                "declared_text": "Q&A", "source_artifact_logical_id": "figure",
                "source_generation": 1, "source_sha256": self.source_sha,
            }]},
            {self.binding: self.source},
            policy={
                "policy_id": "fixture-miktex-runtime",
                "engine": {"prefix_file_fingerprints": [{
                    "sha256": hashlib.sha256(self.entry.read_bytes()).hexdigest(),
                }]},
            },
            staging=self.root, entry=self.entry,
            manifest_entries=[{
                "logical_id": "figure", "generation": 1, "sha256": self.source_sha,
                "staging_path": "figure.png",
            }],
            stable_final_round_auxiliaries={}, observed_declared_paths={self.entry},
            runtime_environment={},
        )

    def _assert_bound(self, pdf: Path) -> None:
        objects, edges, _suite, page_count = self._derive(pdf)
        self.assertEqual(1, page_count)
        self.assertEqual(1, len(objects))
        self.assertEqual("declared_raster_text", objects[0]["object_kind"])
        self.assertEqual("raster.figure", edges[0]["sealed_item_id"])
        self.assertEqual([objects[0]["object_id"]], edges[0]["rendered_object_ids"])

    def test_calibrated_pdf_preserves_matching_rgba_raster_origin(self) -> None:
        self._assert_bound(self._compiled_image(self.image, calibrated=False))
        pdf = self._compiled_image(self.image, calibrated=True)
        with fitz.open(pdf) as document:
            xref, smask = document[0].get_images(full=True)[0][:2]
            self.assertNotEqual(0, smask)
            embedded = fitz.Pixmap(fitz.Pixmap(document, xref), fitz.Pixmap(document, smask))
            self.assertEqual("CalRGB", embedded.colorspace.name)
            self.assertTrue(embedded.alpha)
            self.assertEqual(fitz.Pixmap(self.source).samples, embedded.samples)
        self._assert_bound(pdf)

    def test_changed_pixels_or_alpha_reject_declared_raster_origin(self) -> None:
        # scenario_id: rgb-alpha-raster-mismatch
        # target_invariant: observed samples match the declared source binding
        # mutation_seam: embedded PDF image pixel or soft-mask value
        # rematerialized_nodes: PDF image/mask and observed raster objects
        # intentionally_stale_nodes: none
        # expected_first_gate: declared raster source-to-PDF identity binding
        # expected_error_code: unavailable (existing AdapterError text interface)
        # expected_message_fragment: declared raster text is absent
        # scenario_class: single_contradiction (one mutation per subtest)
        self._assert_bound(self._compiled_image(self.image, calibrated=True))
        for mutation, pixel in (
            ("color", (33, 130, 200, 255)),
            ("alpha", (32, 130, 200, 128)),
        ):
            with self.subTest(mutation=mutation):
                changed = self.image.copy()
                changed.putpixel((1, 1), pixel)
                with self.assertRaises(AdapterError) as raised:
                    self._derive(self._compiled_image(changed, calibrated=True))
                self.assertIn("declared raster text is absent", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

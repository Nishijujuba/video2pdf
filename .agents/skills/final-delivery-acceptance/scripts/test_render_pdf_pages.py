#!/usr/bin/env python3
"""Tests for rendered PDF page evidence generation."""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

import fitz

from render_pdf_pages import render_pdf_pages


REPO_ROOT = Path(__file__).resolve().parents[4]


class RenderPdfPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.video_dir = (
            REPO_ROOT
            / "待删除"
            / "final-delivery-acceptance-render-tests"
            / f"{self._testMethodName}-{uuid.uuid4().hex}"
        )
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_path = self.video_dir / "final.pdf"

    def write_pdf(self, pages: int) -> None:
        doc = fitz.open()
        for page_number in range(1, pages + 1):
            page = doc.new_page(width=300, height=300)
            page.insert_text((72, 72), f"Rendered page {page_number}")
        doc.save(self.pdf_path)
        doc.close()

    def test_renders_one_zero_padded_png_per_pdf_page(self) -> None:
        self.write_pdf(2)

        result = render_pdf_pages(self.pdf_path, video_output_dir=self.video_dir)

        self.assertEqual(result["page_count"], 2)
        self.assertEqual(
            [Path(path).name for path in result["rendered_page_paths"]],
            ["page_0001.png", "page_0002.png"],
        )
        for relative_path in result["rendered_page_paths"]:
            self.assertTrue((self.video_dir / relative_path).exists())

    def test_refresh_moves_stale_extra_rendered_pages_to_video_trash(self) -> None:
        self.write_pdf(2)
        render_pdf_pages(self.pdf_path, video_output_dir=self.video_dir)
        stale_page = self.video_dir / "review" / "acceptance" / "rendered_pages" / "page_0002.png"
        self.assertTrue(stale_page.exists())

        self.pdf_path = self.video_dir / "final-one-page.pdf"
        self.write_pdf(1)
        result = render_pdf_pages(self.pdf_path, video_output_dir=self.video_dir)

        self.assertEqual(result["page_count"], 1)
        self.assertFalse(stale_page.exists())
        trash_matches = list((self.video_dir / "待删除").rglob("page_0002.png"))
        self.assertTrue(trash_matches)


if __name__ == "__main__":
    unittest.main()

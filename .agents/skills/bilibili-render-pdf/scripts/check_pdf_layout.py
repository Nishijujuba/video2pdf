#!/usr/bin/env python3
"""Detect obvious blank-space regressions in generated lecture PDFs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF is required. Run this with the shared kimi virtual environment.", file=sys.stderr)
    sys.exit(3)


PAGE_NUMBER_RE = re.compile(r"^\s*(\d+|[ivxlcdmIVXLCDM]+)\s*$")


def is_footer_page_number(page_rect: fitz.Rect, block: tuple) -> bool:
    x0, y0, x1, y1, text, *_ = block
    if y0 < page_rect.y0 + page_rect.height * 0.88:
        return False
    if not PAGE_NUMBER_RE.match(text):
        return False
    center_x = (x0 + x1) / 2
    return abs(center_x - page_rect.width / 2) < page_rect.width * 0.18


def body_rects(page: fitz.Page) -> list[fitz.Rect]:
    page_rect = page.rect
    rects: list[fitz.Rect] = []

    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text, *_ = block
        if not text.strip():
            continue
        if is_footer_page_number(page_rect, block):
            continue
        if y1 < page_rect.y0 + page_rect.height * 0.04:
            continue
        rects.append(fitz.Rect(x0, y0, x1, y1))

    for image in page.get_images(full=True):
        xref = image[0]
        rects.extend(page.get_image_rects(xref))

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect and not rect.is_empty and rect.width > 2 and rect.height > 2:
            rects.append(rect)

    return rects


def union_rect(rects: list[fitz.Rect]) -> fitz.Rect | None:
    if not rects:
        return None
    result = fitz.Rect(rects[0])
    for rect in rects[1:]:
        result |= rect
    return result


def analyze(pdf_path: Path, max_bottom_blank: float, max_top_blank: float) -> dict:
    doc = fitz.open(pdf_path)
    pages = []
    flagged = []

    for index, page in enumerate(doc, start=1):
        page_rect = page.rect
        rects = body_rects(page)
        content = union_rect(rects)

        if content is None:
            row = {
                "page": index,
                "blank": True,
                "body_boxes": 0,
                "top_blank_ratio": 1.0,
                "bottom_blank_ratio": 1.0,
            }
            pages.append(row)
            flagged.append(row | {"reason": "blank page"})
            continue

        top_blank = max(0.0, content.y0 - page_rect.y0) / page_rect.height
        bottom_blank = max(0.0, page_rect.y1 - content.y1) / page_rect.height
        row = {
            "page": index,
            "blank": False,
            "body_boxes": len(rects),
            "top_blank_ratio": round(top_blank, 3),
            "bottom_blank_ratio": round(bottom_blank, 3),
            "content_y0": round(content.y0, 1),
            "content_y1": round(content.y1, 1),
        }
        pages.append(row)

        if bottom_blank > max_bottom_blank:
            flagged.append(row | {"reason": "large bottom blank"})
        elif top_blank > max_top_blank:
            flagged.append(row | {"reason": "large top blank"})

    return {
        "pdf": str(pdf_path),
        "page_count": len(doc),
        "max_bottom_blank": max_bottom_blank,
        "max_top_blank": max_top_blank,
        "flagged": flagged,
        "largest_bottom_blank": sorted(
            pages, key=lambda row: row["bottom_blank_ratio"], reverse=True
        )[:10],
        "largest_top_blank": sorted(
            pages, key=lambda row: row["top_blank_ratio"], reverse=True
        )[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--max-bottom-blank", type=float, default=0.35)
    parser.add_argument("--max-top-blank", type=float, default=0.30)
    parser.add_argument("--json", action="store_true", help="Emit full JSON output.")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    result = analyze(args.pdf, args.max_bottom_blank, args.max_top_blank)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"PDF: {result['pdf']}")
        print(f"Pages: {result['page_count']}")
        print(f"Flagged pages: {len(result['flagged'])}")
        for row in result["flagged"]:
            print(
                f"- page {row['page']}: {row['reason']}; "
                f"top={row['top_blank_ratio']}, bottom={row['bottom_blank_ratio']}, "
                f"boxes={row['body_boxes']}"
            )
        if not result["flagged"]:
            print("Layout check passed.")

    return 1 if result["flagged"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

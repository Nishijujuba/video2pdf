#!/usr/bin/env python3
"""Render final PDF pages into acceptance evidence PNG files."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import uuid
from pathlib import Path


EXIT_RENDERED = 0
EXIT_INPUT_ERROR = 2
EXIT_DEPENDENCY_ERROR = 3


class RenderError(Exception):
    """Raised when rendered page evidence cannot be generated."""


def _path_under(base: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _relative_to_video(video_output_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(video_output_dir.resolve()).as_posix()


def _move_stale_page(video_output_dir: Path, rendered_pages_dir: Path, stale_page: Path) -> None:
    if not _path_under(rendered_pages_dir, stale_page):
        raise RenderError(f"stale rendered page escapes rendered_pages_dir: {stale_page}")
    trash_dir = video_output_dir / "待删除" / "acceptance-rendered-pages" / uuid.uuid4().hex
    trash_dir.mkdir(parents=True, exist_ok=True)
    target = trash_dir / stale_page.name
    shutil.move(str(stale_page), str(target))


def render_pdf_pages(pdf_path: Path, *, video_output_dir: Path | None = None, dpi: int = 200) -> dict[str, object]:
    """Render every page of a final PDF to review/acceptance/rendered_pages."""

    try:
        import fitz
    except ImportError as exc:
        raise RenderError("PyMuPDF is required. Run with the shared kimi virtual environment.") from exc

    pdf_path = pdf_path.resolve()
    if not pdf_path.exists():
        raise RenderError(f"PDF not found: {pdf_path}")
    video_output_dir = (video_output_dir or pdf_path.parent).resolve()
    if not video_output_dir.exists():
        raise RenderError(f"video output directory not found: {video_output_dir}")
    if not _path_under(video_output_dir, pdf_path):
        raise RenderError("PDF must be inside the video output directory")
    if dpi < 72:
        raise RenderError("dpi must be at least 72")

    rendered_pages_dir = video_output_dir / "review" / "acceptance" / "rendered_pages"
    rendered_pages_dir.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise RenderError(f"cannot open PDF: {pdf_path}") from exc

    rendered_paths: list[str] = []
    try:
        page_count = len(doc)
        if page_count < 1:
            raise RenderError("PDF contains no pages")
        expected_names = {f"page_{page_number:04d}.png" for page_number in range(1, page_count + 1)}
        for existing in rendered_pages_dir.glob("page_*.png"):
            if existing.name not in expected_names:
                _move_stale_page(video_output_dir, rendered_pages_dir, existing)

        for page_number, page in enumerate(doc, start=1):
            output_path = rendered_pages_dir / f"page_{page_number:04d}.png"
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            pixmap.save(output_path)
            rendered_paths.append(_relative_to_video(video_output_dir, output_path))
    finally:
        doc.close()

    return {
        "pdf": _relative_to_video(video_output_dir, pdf_path),
        "page_count": page_count,
        "rendered_pages_dir": _relative_to_video(video_output_dir, rendered_pages_dir),
        "rendered_page_paths": rendered_paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render final PDF pages for Final Delivery Acceptance.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--video-output-dir", type=Path)
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args()

    try:
        result = render_pdf_pages(args.pdf, video_output_dir=args.video_output_dir, dpi=args.dpi)
    except RenderError as exc:
        text = str(exc)
        print(f"RENDER_FAILED: {text}", file=sys.stderr)
        return EXIT_DEPENDENCY_ERROR if re.search(r"PyMuPDF", text) else EXIT_INPUT_ERROR

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_RENDERED


if __name__ == "__main__":
    raise SystemExit(main())

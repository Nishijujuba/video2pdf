from __future__ import annotations

from pathlib import Path
import os
import sys

import fitz


entry = Path(sys.argv[-1])
stem = entry.stem
cwd = Path.cwd()
source = entry.read_text(encoding="utf-8")
if "VIDEO2PDF_FIXTURE_STDERR" in source:
    sys.stderr.buffer.write(b"fixture log4cxx root overlap warning\n")
if "VIDEO2PDF_FIXTURE_NONZERO_EXIT" in source:
    raise SystemExit(7)
inputs = sorted(path.relative_to(cwd) for path in cwd.rglob("*") if path.is_file())
(cwd / f"{stem}.aux").write_text("generated auxiliary", encoding="utf-8")
with (cwd / f"{stem}.fls").open("w", encoding="utf-8") as handle:
    for path in inputs:
        handle.write(f"INPUT {path}\n")
    handle.write(f"INPUT {stem}.aux\n")
    for value in os.environ.get("VIDEO2PDF_FIXTURE_FONTS", "").split(os.pathsep):
        if value:
            handle.write(f"INPUT {Path(value).resolve()}\n")
    undeclared = os.environ.get("VIDEO2PDF_FIXTURE_UNDECLARED_INPUT")
    if undeclared:
        handle.write(f"INPUT {Path(undeclared).resolve()}\n")
document = fitz.open()
page = document.new_page()
page.insert_text((72, 72), "Core claim")
figure = cwd / "figure.png"
if figure.is_file():
    page.insert_image(fitz.Rect(200, 100, 300, 200), filename=figure)
for name, rect in (("figure_a.png", fitz.Rect(200, 100, 300, 200)),
                   ("figure_b.png", fitz.Rect(320, 100, 420, 200))):
    figure = cwd / name
    if figure.is_file():
        page.insert_image(rect, filename=figure)
if "VIDEO2PDF_FIXTURE_OMIT_PDF" not in source:
    document.save(cwd / f"{stem}.pdf")
document.close()

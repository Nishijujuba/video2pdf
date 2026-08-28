from __future__ import annotations

from pathlib import Path
import os
import sys

import fitz


entry = Path(sys.argv[-1])
stem = entry.stem
cwd = Path.cwd()
source = entry.read_text(encoding="utf-8")
captured_environment = (
    dict(os.environ)
    if "VIDEO2PDF_FIXTURE_CAPTURE_ENVIRONMENT" in source
    else None
)
if "VIDEO2PDF_FIXTURE_STDERR" in source:
    sys.stderr.buffer.write(b"fixture log4cxx root overlap warning\n")
if "VIDEO2PDF_FIXTURE_NONZERO_EXIT" in source:
    raise SystemExit(7)
if "VIDEO2PDF_FIXTURE_UNDECLARED_RECORDER_INPUT" in source:
    (cwd / "undeclared.tex").write_text("undeclared", encoding="utf-8")
inputs = sorted(
    path.relative_to(cwd)
    for path in cwd.rglob("*")
    if (
        path.is_file()
        and path.name not in {f"{stem}.pdf", f"{stem}.fls", "engine-environment.json"}
        and path.suffix != ".unread"
        and not (
            "VIDEO2PDF_FIXTURE_OMIT_ENTRYPOINT_INPUT" in source
            and path == cwd / entry.name
        )
    )
)
if captured_environment is not None:
    import json

    (cwd / "engine-environment.json").write_text(
        json.dumps(captured_environment, sort_keys=True),
        encoding="utf-8",
    )
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
if "VIDEO2PDF_FIXTURE_UNEXPECTED_ANNOTATION" in source:
    page.add_text_annot((120, 120), "Unexpected annotation")
for figure_name in ("figure.png", "figure.jpg"):
    figure = cwd / figure_name
    if figure.is_file():
        page.insert_image(fitz.Rect(200, 100, 300, 200), filename=figure)
        break
for name, rect in (("figure_a.png", fitz.Rect(200, 100, 300, 200)),
                   ("figure_b.png", fitz.Rect(320, 100, 420, 200))):
    figure = cwd / name
    if figure.is_file():
        page.insert_image(rect, filename=figure)
if "VIDEO2PDF_FIXTURE_OMIT_PDF" not in source:
    document.save(cwd / f"{stem}.pdf")
document.close()
(cwd / f"{stem}.log").write_text(
    f"Output written on {stem}.pdf (1 page).\n",
    encoding="utf-8",
)

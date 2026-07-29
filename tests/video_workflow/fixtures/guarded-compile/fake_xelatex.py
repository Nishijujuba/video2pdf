from __future__ import annotations

from pathlib import Path
import os
import sys


entry = Path(sys.argv[-1])
stem = entry.stem
cwd = Path.cwd()
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
(cwd / f"{stem}.pdf").write_bytes(b"%PDF-1.4\n% fixture diagnostic\n")

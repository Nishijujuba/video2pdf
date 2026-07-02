from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARTS = ROOT / "CS146S现代软件开发" / "parts"


FIGURE_FOOTNOTE_RE = re.compile(
    r"\\caption\{(?P<caption>.*?)\\protect\\footnotemark\}\s*"
    r"\\end\{figure\}\s*"
    r"\\footnotetext\{(?P<note>.*?)\}",
    re.DOTALL,
)


def normalize_figure_time_notes() -> int:
    changed = 0
    for path in sorted(PARTS.glob("P*/figure_blocks/*.tex")):
        text = path.read_text(encoding="utf-8")

        def replace(match: re.Match[str]) -> str:
            caption = match.group("caption").strip()
            note = match.group("note").strip()
            return (
                rf"\caption{{{caption}}}" + "\n"
                rf"{{\footnotesize\emph{{{note}}}\par}}" + "\n"
                r"\end{figure}"
            )

        new_text, count = FIGURE_FOOTNOTE_RE.subn(replace, text)
        if count:
            path.write_text(new_text, encoding="utf-8", newline="\n")
            changed += count
    return changed


def main() -> None:
    changed = normalize_figure_time_notes()
    print(f"normalized figure time notes: {changed}")


if __name__ == "__main__":
    main()

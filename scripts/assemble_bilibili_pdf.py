from __future__ import annotations

import json
import re
import sys
from pathlib import Path


TEMPLATE = Path(
    r"D:\Project\video2pdf\newskill-kimi\.agents\skills\bilibili-render-pdf\assets\notes-template.tex"
)

FORBIDDEN_RE = re.compile(
    r"\\clearpage|\\newpage|\\pagebreak|\\vfill|height=0\.[5-9][0-9]*\\textheight|width=\\textwidth"
)


def latex_inline(value: str) -> str:
    return (
        value.replace("\\", r"\textbackslash{}")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("#", r"\#")
        .replace("_", r"\_")
    )


def latex_macro_arg(value: str) -> str:
    return value.replace("\\", r"\textbackslash{}").replace("{", r"\{").replace("}", r"\}").replace("&", r"\&")


def convert_backticks(text: str) -> str:
    return re.sub(r"`([^`\n]+)`", lambda m: rf"\texttt{{{latex_inline(m.group(1))}}}", text)


def normalize_fragment(text: str) -> str:
    text = convert_backticks(text)
    text = text.replace("“不是模型本身，而是", "决定效果的核心变量是")
    text = text.replace("不是模型本身，而是", "决定效果的核心变量是")
    return text.strip() + "\n"


def load_sections(root: Path) -> str:
    section_dir = root / "sections"
    expected = [
        section_dir / "section_01.tex",
        section_dir / "section_02.tex",
        section_dir / "section_03.tex",
        section_dir / "section_04.tex",
    ]
    missing = [path.name for path in expected if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing section fragments: {', '.join(missing)}")
    fragments = [normalize_fragment(path.read_text(encoding="utf-8")) for path in expected]
    return "\n\n".join(fragments)


def replace_macro(template: str, name: str, value: str) -> str:
    pattern = re.compile(rf"\\newcommand\{{\\{name}\}}\{{.*?\}}")
    replacement = rf"\newcommand{{\{name}}}{{{value}}}"
    result, count = pattern.subn(lambda _match: replacement, template, count=1)
    if count != 1:
        raise RuntimeError(f"Could not replace macro: {name}")
    return result


def assemble(root: Path) -> Path:
    metadata = json.loads((root / "source" / "metadata.info.json").read_text(encoding="utf-8"))
    template = TEMPLATE.read_text(encoding="utf-8")

    title = f"Claude Code 在大型代码库中的工作原理：最佳实践与入门指南"
    template = replace_macro(template, "notetitle", latex_macro_arg(title))
    template = replace_macro(template, "videochannel", latex_macro_arg(metadata.get("uploader", "")))
    template = replace_macro(template, "videopublishdate", "2026-05-19")
    template = replace_macro(template, "videoduration", "00:17:57")
    template = replace_macro(template, "videourl", metadata.get("webpage_url", "https://www.bilibili.com/video/BV1gCLq6YEW4/"))
    template = replace_macro(template, "videocoverpath", "assets/cover.jpg")

    body = load_sections(root)
    start = template.index("%% --- 正文内容开始 --- %%")
    end = template.index("%% --- 正文内容结束 --- %%")
    assembled = (
        template[: start + len("%% --- 正文内容开始 --- %%")]
        + "\n\n"
        + body
        + "\n"
        + template[end:]
    )

    body_forbidden = []
    for path in list((root / "sections").glob("*.tex")) + list((root / "figure_blocks").glob("*.tex")):
        text = path.read_text(encoding="utf-8")
        for match in FORBIDDEN_RE.finditer(text):
            body_forbidden.append(f"{path}: {match.group(0)}")
    if body_forbidden:
        raise RuntimeError("Forbidden layout command or oversize graphic found:\n" + "\n".join(body_forbidden))

    main_tex = root / "main.tex"
    main_tex.write_text(assembled, encoding="utf-8", newline="\n")
    return main_tex


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: assemble_bilibili_pdf.py <target-folder>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    main_tex = assemble(root)
    print(main_tex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

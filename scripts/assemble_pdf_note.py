from __future__ import annotations

import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_TEMPLATE = ROOT / ".agents" / "skills" / "bilibili-render-pdf" / "assets" / "notes-template.tex"
OUT = ROOT / "【AI Engineer】-真正能够落地交付的多智能体架构路径下"
VIDEO_URL = (
    "https://www.bilibili.com/video/BV1B15j62Eg1/"
    "?spm_id_from=333.1387.favlist.content.click&vd_source=6457e444c35aa58365caef6c074dd705"
)


SLOT_ORDER = {
    1: ["ch1_opening_speaker", "ch1_five_modes_taxonomy"],
    2: ["ch2_task_system_workflow", "ch2_cwv_triangle"],
    3: [
        "ch3_posthoc_tests_vs_contract",
        "ch3_two_verifiers",
        "ch3_structured_handoff_fields",
    ],
    4: ["ch4_parallel_vs_in_goal", "ch4_task_console"],
    5: ["ch5_model_routing_table", "ch5_slack_clone_metrics", "ch5_enterprise_scenarios"],
    6: [
        "ch6_prompt_skill_vs_state_machine",
        "ch6_attention_bottleneck_shift",
        "ch6_synthesis_architecture_loop",
    ],
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def sanitize_figure_block(block: str) -> str:
    block = block.replace("协调器 $C$ 生成计划", "协调器 $C_o$ 生成计划")
    block = block.replace("目标 $G$ 与断言集 $Q$", "目标 $T$ 与断言集 $Q$")
    block = block.replace("$K=(G,Q)$", "$K=(T,Q)$")
    lines = []
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(r"\caption{"):
            if r"\protect\footnotemark" not in line:
                if r"}\footnotemark" in line:
                    line = line.replace(r"}\footnotemark", r"\protect\footnotemark}")
                else:
                    line = line.replace(r"\footnotemark}", r"\protect\footnotemark}")
        lines.append(line)
    return "\n".join(lines)


def split_figure_blocks(snippet: str) -> list[str]:
    blocks: list[str] = []
    lines = snippet.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip().lstrip("\ufeff").startswith(r"\begin{figure}[H]"):
            i += 1
            continue

        block_lines = [lines[i].lstrip("\ufeff")]
        i += 1
        while i < len(lines):
            block_lines.append(lines[i])
            end_seen = lines[i].strip() == r"\end{figure}"
            i += 1
            if end_seen:
                break

        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines) or not lines[i].strip().startswith(r"\footnotetext{"):
            raise RuntimeError("Figure block is missing following footnotetext")
        block_lines.append(lines[i])
        i += 1
        blocks.append(sanitize_figure_block("\n".join(block_lines).strip()))

    return blocks


def build_figure_map() -> dict[str, str]:
    figure_map: dict[str, str] = {}
    for chapter, slots in SLOT_ORDER.items():
        snippet_path = OUT / "figure_snippets" / f"figures_ch{chapter}.tex"
        blocks = split_figure_blocks(read_text(snippet_path))
        if len(blocks) != len(slots):
            raise RuntimeError(
                f"{snippet_path} has {len(blocks)} figure blocks, expected {len(slots)}"
            )
        figure_map.update(dict(zip(slots, blocks)))
    return figure_map


def insert_figures(section_text: str, figure_map: dict[str, str]) -> str:
    def replace_slot(match: re.Match[str]) -> str:
        slot = match.group(1).strip()
        if slot not in figure_map:
            raise RuntimeError(f"No figure block for slot {slot}")
        return figure_map[slot] + "\n\n"

    return re.sub(r"%\s*FIGURE_SLOT:\s*([A-Za-z0-9_]+)\s*", replace_slot, section_text)


def metadata() -> dict[str, str]:
    data = json.loads(read_text(OUT / "source" / "video.info.json"))
    upload = data.get("upload_date", "")
    if len(upload) == 8:
        upload = f"{upload[:4]}-{upload[4:6]}-{upload[6:]}"
    return {
        "title": data.get("title", "【AI Engineer】 | 真正能够落地交付的多智能体架构"),
        "channel": data.get("uploader", "KrillinAI小林"),
        "upload": upload or "2026-05-10",
        "duration": data.get("duration_string", "18:30"),
    }


def replace_command(tex: str, command: str, value: str) -> str:
    return re.sub(
        rf"\\newcommand\{{\\{command}\}}\{{.*?\}}",
        rf"\\newcommand{{\\{command}}}{{{value}}}",
        tex,
        count=1,
    )


def main() -> None:
    assets = OUT / "assets"
    assets.mkdir(exist_ok=True)
    source_cover = OUT / "source" / "video.jpg"
    cover = assets / "cover.jpg"
    if source_cover.exists():
        shutil.copy2(source_cover, cover)

    figure_map = build_figure_map()
    sections = []
    for i in range(1, 7):
        section = read_text(OUT / "sections" / f"section_{i}.tex")
        sections.append(insert_figures(section, figure_map).strip())
    body = "\n\n".join(sections)

    meta = metadata()
    tex = read_text(SKILL_TEMPLATE)
    tex = tex.replace(r"\usepackage[margin=2.5cm]{geometry}", r"\usepackage[margin=2.25cm]{geometry}")
    tex = tex.replace(r"\begin{document}", "\\raggedbottom\n\\begin{document}")
    tex = tex.replace(
        "\\tableofcontents\n\\newpage",
        "{\\small\n\\setlength{\\parskip}{0pt}\n\\setlength{\\parindent}{0pt}\n\\tableofcontents\n}\n\\newpage",
    )
    tex = replace_command(tex, "notetitle", latex_escape(meta["title"]))
    tex = replace_command(tex, "noteauthors", r"五道口纳什 \& Codex")
    tex = replace_command(tex, "videochannel", latex_escape(meta["channel"]))
    tex = replace_command(tex, "videopublishdate", latex_escape(meta["upload"]))
    tex = replace_command(tex, "videoduration", latex_escape(meta["duration"]))
    tex = replace_command(tex, "videourl", VIDEO_URL)
    tex = replace_command(tex, "videocoverpath", "assets/cover.jpg")
    tex = re.sub(
        r"%% --- 正文内容开始 --- %%.*?%% --- 正文内容结束 --- %%",
        lambda _match: "%% --- 正文内容开始 --- %%\n\n"
        + body
        + "\n\n%% --- 正文内容结束 --- %%",
        tex,
        flags=re.S,
    )
    write_text(OUT / "main.tex", tex)


if __name__ == "__main__":
    main()

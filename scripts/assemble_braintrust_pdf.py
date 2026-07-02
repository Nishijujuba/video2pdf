from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(r"D:\Project\video2pdf\newskill-kimi\Braintrust专家解析可观测性新方案")
SECTIONS = ROOT / "sections"
FIGURES = ROOT / "figures"
SOURCE = ROOT / "source"

SECTION_FILES = [
    "section_01_02.tex",
    "section_03_04.tex",
    "section_05_06.tex",
    "section_07_08.tex",
]

FIGURE_BY_SLOT = {
    "intro_agenda": [
        "fig01_agenda_overview",
        "fig02_braintrust_quality_loop",
    ],
    "traditional_o11y": [
        "fig03_traditional_observability_established",
        "fig04_traditional_observability_scope",
        "fig05_observability_building_blocks",
    ],
    "agent_metrics": [
        "fig06_agent_non_determinism_codepaths",
        "fig07_ai_observability_quality_metrics",
    ],
    "agent_trace_ui": [
        "fig08_braintrust_trace_log_table",
        "fig09_agent_trace_system_challenges",
        "fig11_braintrust_trace_detail_tool_calls",
    ],
    "database_architecture": [
        "fig10_agent_trace_database_architecture",
    ],
    "personas_evals": [
        "fig12_persona_expansion_technical_nontechnical",
        "fig13_evals_observability_iceberg_flywheel",
    ],
    "clustering_topics": [
        "fig14_future_topic_modeling_clusters",
    ],
    "human_annotation": [
        "fig15_human_annotation_review_ui",
    ],
}


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(replacements.get(ch, ch) for ch in value)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def load_summary() -> dict:
    return json.loads((SOURCE / "video_summary.json").read_text(encoding="utf-8"))


def load_figure_manifest() -> list[dict]:
    manifest = FIGURES / "figure_manifest.json"
    if not manifest.exists():
        return []
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("figures", [])
    return data


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def build_figure_blocks() -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    for item in load_figure_manifest():
        path = item.get("path") or item.get("file") or item.get("file_path")
        if not path:
            continue
        rel_path = path.replace("\\", "/")
        absolute = ROOT / rel_path
        if not absolute.exists():
            continue
        caption = item.get("caption") or item.get("caption_zh") or item.get("title") or ""
        interval = item.get("interval") or item.get("time_interval") or item.get("source_time") or ""
        key_values = [
            item.get("slot", ""),
            item.get("key", ""),
            item.get("id", ""),
            item.get("name", ""),
            Path(rel_path).stem,
        ]
        block = (
            "\\begin{figure}[H]\n"
            "\\centering\n"
            f"\\includegraphics[width=0.86\\linewidth,height=0.38\\textheight,keepaspectratio]{{{rel_path}}}\n"
            f"\\caption{{{caption} \\protect\\footnotemark}}\n"
            "\\end{figure}\n"
            f"\\footnotetext{{视频画面时间区间：{interval}。}}\n"
        )
        for key in key_values:
            if key:
                blocks.setdefault(normalize_key(key), []).append(block)
    return blocks


def insert_figures(text: str, figure_blocks: dict[str, list[str]]) -> str:
    output = []
    used_blocks = set()
    for line in text.splitlines():
        output.append(line)
        match = re.match(r"\s*%\s*FIGURE_SLOT:\s*(\S+)", line)
        if not match:
            continue
        slot = match.group(1)
        wanted_values = FIGURE_BY_SLOT.get(slot, [slot])
        if isinstance(wanted_values, str):
            wanted_values = [wanted_values]
        for wanted in wanted_values:
            blocks = figure_blocks.get(normalize_key(wanted), [])
            for block in blocks:
                if block in used_blocks:
                    continue
                output.append("")
                output.append(block)
                output.append("")
                used_blocks.add(block)
    return "\n".join(output) + "\n"


def preamble(summary: dict) -> str:
    title = latex_escape(summary["title"])
    channel = latex_escape(summary["channel"])
    publish_date = latex_escape(summary["publish_date"])
    duration = latex_escape(summary["duration"])
    url = summary["url"]
    cover_path = summary.get("cover_path", "assets/cover.jpg")
    return rf"""\documentclass[a4paper,11pt]{{article}}

\usepackage[fontset=windows]{{ctex}}
\usepackage{{fontspec}}
\usepackage{{amsmath,amssymb}}
\usepackage{{xcolor}}
\usepackage{{graphicx}}
\usepackage[margin=2.35cm]{{geometry}}
\usepackage[most]{{tcolorbox}}
\usepackage{{listings}}
\usepackage{{hyperref}}
\usepackage{{booktabs}}
\usepackage{{longtable}}
\usepackage{{subcaption}}
\usepackage{{float}}
\usepackage{{tikz}}
\usepackage{{enumitem}}
\usepackage{{caption}}

\hypersetup{{
  colorlinks=true,
  linkcolor=blue!55!black,
  urlcolor=blue!60!black,
  citecolor=blue!55!black
}}

\setlist{{nosep,leftmargin=2em}}
\graphicspath{{{{./}}}}
\linespread{{1.12}}
\emergencystretch=2em
\sloppy

\newtcolorbox{{knowledgebox}}[1]{{
  enhanced,
  breakable,
  colback=blue!5!white,
  colframe=blue!65!black,
  colbacktitle=blue!65!black,
  coltitle=white,
  fonttitle=\bfseries,
  title=#1,
  attach boxed title to top left={{yshift=-2mm, xshift=2mm}},
  boxrule=.8pt,
  sharp corners
}}

\newtcolorbox{{importantbox}}[1]{{
  enhanced,
  breakable,
  colback=yellow!10!white,
  colframe=yellow!75!black,
  colbacktitle=yellow!75!black,
  coltitle=black,
  fonttitle=\bfseries,
  title=#1,
  boxrule=.8pt,
  sharp corners
}}

\newtcolorbox{{warningbox}}[1]{{
  enhanced,
  breakable,
  colback=red!5!white,
  colframe=red!70!black,
  colbacktitle=red!70!black,
  coltitle=white,
  fonttitle=\bfseries,
  title=#1,
  boxrule=.8pt,
  sharp corners
}}

\newtcolorbox{{dialoguebox}}[1]{{
  enhanced,
  breakable,
  colback=green!4!white,
  colframe=green!45!black,
  colbacktitle=green!45!black,
  coltitle=white,
  fonttitle=\bfseries,
  title=#1,
  boxrule=.8pt,
  sharp corners
}}

\lstset{{
  language=Python,
  basicstyle=\ttfamily\small,
  keywordstyle=\color{{blue}},
  stringstyle=\color{{red!60!black}},
  commentstyle=\color{{green!50!black}},
  breaklines=true,
  frame=single,
  numbers=left,
  numberstyle=\tiny\color{{gray}},
  captionpos=b,
  extendedchars=false
}}

\newcommand{{\notetitle}}{{{title}}}
\newcommand{{\noteauthors}}{{AI Engineer \& Codex}}
\newcommand{{\notedate}}{{2026-05-30}}
\newcommand{{\videochannel}}{{{channel}}}
\newcommand{{\videopublishdate}}{{{publish_date}}}
\newcommand{{\videoduration}}{{{duration}}}
\newcommand{{\videourl}}{{{url}}}
\newcommand{{\videocoverpath}}{{{cover_path}}}

\begin{{document}}

\begin{{titlepage}}
\centering
{{\Large 视频课程笔记\par}}
\vspace{{1.0cm}}
{{\huge\bfseries \notetitle\par}}
\vspace{{0.8cm}}
{{\large \noteauthors\par}}
\vspace{{0.25cm}}
{{\large \notedate\par}}
\vspace{{0.9cm}}

\includegraphics[width=0.86\textwidth,height=0.43\textheight,keepaspectratio]{{\videocoverpath}}\par

\vfill
\begin{{tcolorbox}}[width=0.92\textwidth, colback=black!2!white, colframe=black!55, sharp corners]
\textbf{{视频频道}}: \videochannel\par
\textbf{{发布日期}}: \videopublishdate\par
\textbf{{视频时长}}: \videoduration\par
\textbf{{视频链接}}: \href{{\videourl}}{{\nolinkurl{{\videourl}}}}\par
\textbf{{资料说明}}: 本笔记以 YouTube 英文自动字幕、视频画面、封面图与本地元数据为基础整理。自动字幕中的明显识别错误已按上下文修正，公司名统一写作 Braintrust。
\end{{tcolorbox}}
\end{{titlepage}}

\tableofcontents
\newpage
"""


def final_synthesis() -> str:
    return r"""
\section{总结与延伸}

\subsection{演讲者的收束观点}

Phil Hetzel 在主体部分给出的路线很清楚：传统可观测性已经能很好回答系统是否在线、延迟是否达标、错误率是否异常；Agent 可观测性继续继承这些底座，同时必须把行为质量、语义证据、工具使用、领域判断和生产反馈纳入同一条链路。Q\&A 中的补充进一步说明，Braintrust 把 observability 与 evals 看成同一类系统问题：离线评测提前知道输入，生产观测在真实流量中等待未知输入出现，二者都依赖 trace、评分、检索和分析。

\subsection{概念压缩：从状态监控到行为诊断}

可以把整场演讲压缩成一个公式：

$$
Agent\ O11y = Technical\ Signals + Semantic\ Evidence + Human\ Judgment + Feedback\ Loop
$$

\begin{itemize}
  \item $Technical\ Signals$：延迟、错误率、TTFT、token 数、cache hit 等工程信号。
  \item $Semantic\ Evidence$：prompt、response、tool output、上下文证据和长文本检索。
  \item $Human\ Judgment$：领域专家对 trace 的好坏判断，以及判断背后的理由。
  \item $Feedback\ Loop$：把生产问题转成离线实验，把人工理由转成可扩展评分函数。
\end{itemize}

\begin{importantbox}{核心判断}
Agent 可观测性的难点已经超出“采集更多日志”。系统要把一次 agent 交互还原成可查询、可解释、可评分、可被领域专家共同改进的证据链。
\end{importantbox}

\subsection{给工程团队的落地启示}

第一，trace 结构要为未来的问题预留空间。若系统只保存最终回答，就很难追问 groundedness、工具选择、上下文证据和失败路径。第二，质量指标要与业务定义绑定。医疗、财富管理、法律、客服等场景对“好回答”的定义不同，通用延迟指标无法替代领域判断。第三，人工标注的价值在理由。单纯打分只能告诉团队哪里坏了，理由才能暴露 failure mode，并逐步形成自动评分函数。第四，生产 trace 和离线 eval 数据集应当连通。生产环境发现的问题需要进入实验系统，实验修复后的版本再回到生产观测中接受检验。

\subsection{开放问题}

\begin{itemize}
  \item 当 trace 体积极大时，团队应如何在成本、隐私、实时性和可解释性之间设定保留策略？
  \item 领域专家参与标注后，怎样避免评分标准漂移，并让不同专家的判断保持一致？
  \item 聚类和主题建模能发现 unknown unknowns，但这些发现进入产品优先级后，还需要怎样的人类审查流程？
  \item Agent 可观测性平台需要处理大量 prompt、用户输入和工具输出，安全边界与数据治理会成为系统设计的一部分。
\end{itemize}

\subsection{拓展阅读}

\begin{itemize}
  \item Braintrust 官方博客可作为进一步了解 agent trace 数据系统与 eval 工作流的入口。
  \item Apache Lucene 与 Tantivy 的资料适合补充理解全文索引在长文本检索中的作用。
\end{itemize}
"""


def main() -> None:
    summary = load_summary()
    figures = build_figure_blocks()
    body_parts = []
    for filename in SECTION_FILES:
        path = SECTIONS / filename
        if not path.exists():
            raise FileNotFoundError(path)
        body_parts.append(insert_figures(read_text(path), figures))
    text = preamble(summary) + "\n\n".join(body_parts) + final_synthesis() + "\n\\end{document}\n"
    (ROOT / "main.tex").write_text(text, encoding="utf-8")
    print(ROOT / "main.tex")


if __name__ == "__main__":
    main()

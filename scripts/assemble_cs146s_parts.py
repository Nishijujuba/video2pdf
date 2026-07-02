from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COURSE = ROOT / "CS146S现代软件开发"
PARTS = COURSE / "parts"


TITLES = {
    "P01": ("从基础模型到可用助手：LLM 训练管线与提示技术", "00:14:06"),
    "P02": ("让 LLM 连接真实世界：MCP、工具调用与安全授权", "00:16:10"),
    "P03": ("AI IDE 工作流革命：同步/异步代理与上下文失效", "00:14:29"),
    "P04": ("编码代理模式：上下文工程、Claude Code 与多代理管理", "00:15:21"),
    "P05": ("现代 AI Terminal：代理配置、速度/正确性与规则文件", "00:16:43"),
    "P06": ("AI 测试与安全：代理攻击面、长上下文脆弱性与纵深防御", "00:17:10"),
    "P07": ("现代软件支持：AI 代码审查、人机分工与可操作反馈", "00:14:26"),
    "P08": ("自动化 UI 与应用构建：从提示到生产级代码库", "00:13:54"),
    "P09": ("部署后的代理：SRE 演化、可观测性与 AI 原生运维", "00:13:34"),
}


COVER_FOCUS = {
    "P01": "本讲把基础模型、指令微调、提示技术和工具使用串成一条训练到可用助手的工程链路。",
    "P02": "本讲聚焦 MCP、工具调用时序和授权边界，解释代理如何安全接入真实系统。",
    "P03": "本讲区分同步 IDE 辅助与异步代理委派，并把上下文失效拆成可观察的工程故障。",
    "P04": "本讲把 Claude Code、多代理协作和上下文工程放在同一个代码库工作面中分析。",
    "P05": "本讲讨论 AI Terminal 的产品原则、代理配置、规则文件和速度与正确性的取舍。",
    "P06": "本讲围绕代理攻击面、安全测试、长上下文脆弱性和纵深防御建立安全模型。",
    "P07": "本讲从代码审查进入现代软件支持，强调可操作反馈、人机分工和 720P 源视频清晰度限制。",
    "P08": "本讲拆解 prompt-to-app 的生成、验证、修复和部署闭环，连接 UI 自动化与生产代码库。",
    "P09": "本讲把 SRE、trace/span、SLO 和 AI-native 运维代理放到部署后的可靠性体系中。",
}


COVER_KEYWORDS = {
    "P01": "RAG、提示工程、Software 3.0、工具使用",
    "P02": "MCP、OAuth、工具发现、授权边界",
    "P03": "AI IDE、异步代理、上下文失效、规格即源码",
    "P04": "Claude Code、上下文工程、多代理、规则文件",
    "P05": "AI Terminal、agent profile、warp.md、快速反馈",
    "P06": "攻击面、prompt injection、RCE、纵深防御",
    "P07": "AI code review、action rate、人机协作、720P 备用流",
    "P08": "UI 自动化、app builder、autofixer、质量门",
    "P09": "SRE、SLO、error budget、trace/span",
}


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


def section_inputs(part_dir: Path) -> str:
    files = sorted((part_dir / "sections").glob("*.tex"))
    if not files:
        raise RuntimeError(f"No section files found for {part_dir.name}")
    return "\n".join(rf"\input{{sections/{file.name}}}" for file in files)


def verify_figure_blocks(part_dir: Path) -> None:
    missing: list[str] = []
    for section in (part_dir / "sections").glob("*.tex"):
        text = section.read_text(encoding="utf-8")
        for marker in text.split(r"\input{figure_blocks/")[1:]:
            name = marker.split("}", 1)[0]
            if not (part_dir / "figure_blocks" / name).exists():
                missing.append(f"{section.name}: {name}")
    if missing:
        raise RuntimeError(f"Missing figure blocks for {part_dir.name}: {missing}")


def make_main(part_id: str, title: str, duration: str) -> str:
    part_num = int(part_id[1:])
    full_title = f"CS146S {part_id}｜{title}"
    url = f"https://www.bilibili.com/video/BV1c1NMzpEKE/?p={part_num}"
    inputs = section_inputs(PARTS / part_id)
    focus = COVER_FOCUS[part_id]
    keywords = COVER_KEYWORDS[part_id]
    return rf"""\documentclass[a4paper]{{article}}

\usepackage[fontset=fandol]{{ctex}}
\usepackage{{amsmath, amssymb}}
\usepackage{{graphicx}}
\usepackage{{xcolor}}
\usepackage[margin=2.25cm]{{geometry}}
\usepackage[most]{{tcolorbox}}
\usepackage{{etoolbox}}
\usepackage{{listings}}
\usepackage{{hyperref}}
\usepackage{{booktabs}}
\usepackage{{subcaption}}
\usepackage{{float}}
\usepackage{{tikz}}
\IfFileExists{{pgfplots.sty}}{{
  \usepackage{{pgfplots}}
  \pgfplotsset{{compat=1.18}}
}}{{}}

\hypersetup{{
  colorlinks=true,
  linkcolor=blue!55!black,
  urlcolor=blue!55!black
}}

\newtcolorbox{{knowledgebox}}[1]{{
  enhanced, colback=blue!5!white, colframe=blue!75!black, colbacktitle=blue!75!black,
  coltitle=white, fonttitle=\bfseries, title=#1, attach boxed title to top left={{yshift=-2mm, xshift=2mm}},
  boxrule=1pt, sharp corners
}}

\newtcolorbox{{importantbox}}[1]{{
  enhanced, colback=yellow!10!white, colframe=yellow!80!black, colbacktitle=yellow!80!black,
  coltitle=black, fonttitle=\bfseries, title=#1, sharp corners
}}

\newtcolorbox{{warningbox}}[1]{{
  enhanced, colback=red!5!white, colframe=red!75!black, colbacktitle=red!75!black,
  coltitle=white, fonttitle=\bfseries, title=#1, sharp corners
}}

\lstset{{
  language=Python,
  basicstyle=\ttfamily\small,
  keywordstyle=\color{{blue}},
  stringstyle=\color{{red!60!black}},
  commentstyle=\color{{green!60!black}},
  breaklines=true,
  frame=single,
  numbers=left,
  numberstyle=\tiny\color{{gray}},
  captionpos=b,
  extendedchars=false
}}

\newcommand{{\notetitle}}{{{latex_escape(full_title)}}}
\newcommand{{\noteauthors}}{{五道口纳什 \& Codex}}
\newcommand{{\notedate}}{{2026年5月13日}}
\newcommand{{\videochannel}}{{Bilibili：CS146S现代软件开发}}
\newcommand{{\videopublishdate}}{{未在本地元数据中提供}}
\newcommand{{\videoduration}}{{{latex_escape(part_id)} 单独时长：{latex_escape(duration)}}}
\newcommand{{\videourl}}{{{url}}}
\newcommand{{\videocoverpath}}{{assets/cover.jpg}}
\newcommand{{\coverfocus}}{{{latex_escape(focus)}}}
\newcommand{{\coverkeywords}}{{{latex_escape(keywords)}}}

\raggedbottom
\begin{{document}}

\begin{{titlepage}}
\setlength{{\parindent}}{{0pt}}
\begin{{tikzpicture}}[remember picture, overlay]
  \fill[blue!4!white] (current page.north west) rectangle (current page.south east);
  \fill[blue!65!black] (current page.north west) rectangle ([yshift=-1.35cm]current page.north east);
\end{{tikzpicture}}
\centering
{{\color{{white}}\Large CS146S 现代软件开发\par}}
\vspace{{0.55cm}}
{{\huge\bfseries \notetitle\par}}
\vspace{{0.35cm}}
{{\large \noteauthors \quad|\quad \notedate\par}}
\vspace{{0.55cm}}
\begin{{minipage}}[t]{{0.56\textwidth}}
\centering
\includegraphics[width=0.98\linewidth,height=0.34\textheight,keepaspectratio]{{\videocoverpath}}\par
\vspace{{0.35cm}}
\begin{{tcolorbox}}[width=0.98\linewidth, colback=white, colframe=black!55, boxrule=0.7pt, sharp corners]
\small
\textbf{{视频/频道}}：\videochannel\par
\textbf{{发布时间}}：\videopublishdate\par
\textbf{{分 P 时长}}：\videoduration
\end{{tcolorbox}}
\end{{minipage}}
\hfill
\begin{{minipage}}[t]{{0.38\textwidth}}
\begin{{tcolorbox}}[width=0.98\linewidth, colback=yellow!9!white, colframe=yellow!70!black, boxrule=0.8pt, sharp corners, title={{本讲定位}}, colbacktitle=yellow!75!black, coltitle=black, fonttitle=\bfseries]
\small \coverfocus
\end{{tcolorbox}}
\vspace{{0.28cm}}
\begin{{tcolorbox}}[width=0.98\linewidth, colback=blue!5!white, colframe=blue!70!black, boxrule=0.8pt, sharp corners, title={{关键词}}, colbacktitle=blue!70!black, coltitle=white, fonttitle=\bfseries]
\small \coverkeywords
\end{{tcolorbox}}
\vspace{{0.28cm}}
\begin{{tcolorbox}}[width=0.98\linewidth, colback=white, colframe=black!55, boxrule=0.7pt, sharp corners, title={{阅读方式}}, colbacktitle=black!65, coltitle=white, fonttitle=\bfseries]
\small 先按目录建立全局地图，再回到图注和术语框核对概念边界；每张图的时间来源随图给出，方便回看原视频。
\end{{tcolorbox}}
\end{{minipage}}
\vspace{{0.5cm}}
\begin{{tcolorbox}}[width=0.96\textwidth, colback=white, colframe=blue!60!black, boxrule=0.8pt, sharp corners]
\small
\textbf{{视频链接}}：\href{{\videourl}}{{\nolinkurl{{\videourl}}}}\par
\textbf{{排版说明}}：本 PDF 为该分 P 的独立讲义，包含封面、目录、正文、图文小节和本讲总结与延伸。
\end{{tcolorbox}}
\end{{titlepage}}

{{\small
\setlength{{\parskip}}{{0pt}}
\setlength{{\parindent}}{{0pt}}
\tableofcontents
}}

\bigskip

{inputs}

\end{{document}}
"""


def main() -> None:
    for part_id, (title, duration) in TITLES.items():
        part_dir = PARTS / part_id
        verify_figure_blocks(part_dir)
        (part_dir / "main.tex").write_text(make_main(part_id, title, duration), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

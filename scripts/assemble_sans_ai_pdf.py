from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "【SANS-AI网络安全峰会2026】第5集-三只小猪启示录-AI安全架构设计比模型更重要"


FIGURES: dict[str, tuple[str, str, str, str, str]] = {
    "ch1_opening_title": (
        "figures/ch01/ch1_opening_title.jpg",
        "fig:ch1-opening-title",
        "开场标题页显示本场 keynote 的主题：\\emph{Claw and Order: Protecting Your Shell from Bottom Dwellers}。",
        "视频画面时间区间：00:02--00:10；选用帧：00:04。",
        r"width=\linewidth,height=0.34\textheight,keepaspectratio",
    ),
    "ch1_three_pigs_materials": (
        "figures/ch01/ch1_three_pigs_materials.jpg",
        "fig:ch1-three-pigs-materials",
        "三只小猪的草屋页把“材料”问题摆到台前：材料选择可见，但它只解释了安全故事的一部分。",
        "视频画面时间区间：01:19--01:32；选用帧：01:24。",
        r"width=\linewidth,height=0.34\textheight,keepaspectratio",
    ),
    "ch1_architecture_matters": (
        "figures/ch01/ch1_architecture_matters.jpg",
        "fig:ch1-architecture-matters",
        "砖房对照页把架构论点说清：安全还取决于部件怎样排列、连接和承重。",
        "视频画面时间区间：01:33--01:55；选用帧：01:35。",
        r"width=\linewidth,height=0.34\textheight,keepaspectratio",
    ),
    "ch2_architecture_quote": (
        "figures/ch02/ch2_architecture_quote.jpg",
        "fig:ch2-architecture-quote",
        "Mies van der Rohe 的架构引语强调，两块砖被谨慎放置时，材料才开始进入组织关系。",
        "视频画面时间区间：00:02:03--00:02:18。",
        r"width=0.96\linewidth,height=0.34\textheight,keepaspectratio",
    ),
    "ch2_ctf_prompt": (
        "figures/ch02/ch2_ctf_prompt.jpg",
        "fig:ch2-ctf-prompt",
        "强模型加极简 CTF prompt 的例子：任务入口几乎只要求寻找漏洞并写入报告。",
        "视频画面时间区间：00:02:31--00:02:58。",
        r"width=0.96\linewidth,height=0.34\textheight,keepaspectratio",
    ),
    "ch2_nano_analyzer_cost": (
        "figures/ch02/ch2_nano_analyzer_cost.jpg",
        "fig:ch2-nano-analyzer-cost",
        "\\texttt{nano-analyzer} 所代表的基础扫描架构与 token 成本对比：OpenBSD 相关成本从约 \\$20,000 降到低于 \\$100。",
        "视频画面时间区间：00:03:16--00:04:24。",
        r"width=0.96\linewidth,height=0.34\textheight,keepaspectratio",
    ),
    "ch2_scaffolding_openant": (
        "figures/ch02/ch2_scaffolding_openant.jpg",
        "fig:ch2-scaffolding-openant",
        "\\texttt{OpenAnt} 作为更精炼的漏洞发现架构，与更强模型组合后形成更强的放大效应。",
        "视频画面时间区间：00:05:24--00:05:52。",
        r"width=0.96\linewidth,height=0.34\textheight,keepaspectratio",
    ),
    "ch3_model_materials_progression": (
        "figures/ch03/ch3_model_materials_progression.jpg",
        "fig:ch3-model-materials-progression",
        "模型材料阶梯从 Sonnet 3.7 推进到 Mythos，说明更强材料会推动更大胆的应用，也会迫使构建规范持续更新。",
        "视频画面时间区间：06:08--08:56；选用帧：08:16。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch3_andre_quote": (
        "figures/ch03/ch3_andre_quote.jpg",
        "fig:ch3-andre-quote",
        "Andrej Karpathy 的 “Mere Mortals Are Cooked” 引语把 agent 生态的表面积列出来：prompts、modes、workflows、permissions、guardrails、MCP、plugins、tools、skills、memory 等。",
        "视频画面时间区间：08:56--10:13；选用帧：10:13。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch3_openclaw_growth": (
        "figures/ch03/ch3_openclaw_growth.jpg",
        "fig:ch3-openclaw-growth",
        "OpenClaw 增长曲线页把它称为增长最快的 GitHub 项目之一，星标历史曲线明显高于对照项目。",
        "视频画面时间区间：10:30--11:16；选用帧：10:45。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch3_openclaw_bricks": (
        "figures/ch03/ch3_openclaw_bricks.jpg",
        "fig:ch3-openclaw-bricks",
        "\\texttt{SOUL.md} 示例把 OpenClaw 部件写成岗位功能说明，画面中可见角色 \\texttt{Risk Assessor}、身份与职责边界。",
        "视频画面时间区间：12:10--12:36；选用帧：12:24。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch3_reckless_warning": (
        "figures/ch03/ch3_reckless_warning.jpg",
        "fig:ch3-reckless-warning",
        "安全警示页给出本章的关键转折：从能力视角看 OpenClaw 很有突破性，把数据交给早期形态却可能极不稳妥。",
        "视频画面时间区间：13:20--13:48；选用帧：13:47。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch4_rule_of_two_diagram": (
        "figures/ch04/ch4_rule_of_two_diagram.jpg",
        "fig:ch4-rule-of-two-diagram",
        "完整的 Agent Rule of Two 图：团队要在三类风险维度中最多选择两类，默认 OpenClaw 被标在危险区。",
        "视频画面时间区间：14:31--15:12；选用帧：15:01。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch4_tool_exposure_examples": (
        "figures/ch04/ch4_tool_exposure_examples.jpg",
        "fig:ch4-tool-exposure-examples",
        "OpenClaw 暴露面与缓解工具示例：Clawhub、本地敏感资产、外联动作、NVIDIA NemoClaw/Openshell、Cisco DefenseClaw 与 Knostic 可见性工具。",
        "视频画面时间区间：15:18--16:09；选用帧：15:18、15:44、15:55、16:09。",
        r"width=\linewidth,height=0.38\textheight,keepaspectratio",
    ),
    "ch4_controls_matrix": (
        "figures/ch04/ch4_controls_matrix.jpg",
        "fig:ch4-controls-matrix",
        "OpenClaw 防护矩阵把可信技能来源、扫描、allowlists/blocklists、最小权限、默认拒绝、动作护栏和审计日志映射到风险维度。",
        "视频画面时间区间：16:09--17:46；选用帧：16:23。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch4_audit_logging": (
        "figures/ch04/ch4_audit_logging.jpg",
        "fig:ch4-audit-logging",
        "Knostic 可见性与控制页把审计落到操作层：OpenClaw Telemetry 捕获工具调用、LLM 请求、agent 会话、脱敏、哈希链和 SIEM 转发。",
        "视频画面时间区间：15:56--16:09；选用帧：16:09。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch5_agent_maturity_ladder": (
        "figures/ch05/ch5_agent_maturity_ladder.png",
        "fig:ch5-agent-maturity-ladder",
        "自治助手成熟度阶梯：Chatbot、Tool User、Persistent Operator 与 Agent Fleet 代表从问答到多 agent 管理的升级。",
        "视频画面时间区间：18:47--19:18；选用帧：19:04。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch5_markdown_scaffolding": (
        "figures/ch05/ch5_markdown_scaffolding.png",
        "fig:ch5-markdown-scaffolding",
        "脚手架未来图把界面、持久上下文、架构能力和 agent 管理放在模型外层；讲者在同一段落说明 Markdown 文件会成为可分享的架构蓝图。",
        "视频画面时间区间：20:04--20:31；选用帧：20:40。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch5_agent_ui_memory_shift": (
        "figures/ch05/ch5_agent_ui_memory_shift.png",
        "fig:ch5-agent-ui-memory-shift",
        "界面与记忆迁移的现场视觉笔记：面向 agent 的界面、持久记忆、日程和“个人贡献者消失”共同改变工作边界。",
        "视频画面时间区间：20:31--22:22；选用帧：22:44。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch5_ic_manager_multiplier": (
        "figures/ch05/ch5_ic_manager_multiplier.png",
        "fig:ch5-ic-manager-multiplier",
        "agentic AI 给操作模型带来的压力：span of control、Dunbar's Number 与 Autodesk 增长示意共同说明组织复杂度会被放大。",
        "视频画面时间区间：22:37--25:38；选用帧：24:26。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
    "ch5_closing_architecture_takeaway": (
        "figures/ch05/ch5_closing_architecture_takeaway.png",
        "fig:ch5-closing-architecture-takeaway",
        "结尾架构引语将 “To create architecture is to put in order” 归于 Le Corbusier，呼应全场对 order 的强调。",
        "视频画面时间区间：25:27--25:51；选用帧：25:46。",
        r"width=\linewidth,height=0.36\textheight,keepaspectratio",
    ),
}


DEFAULT_BOX_TITLES = {
    "importantbox": "关键结论",
    "knowledgebox": "补充背景",
    "warningbox": "常见误区",
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


def add_default_box_titles(text: str) -> str:
    for env, title in DEFAULT_BOX_TITLES.items():
        text = re.sub(
            rf"\\begin\{{{env}\}}(?!\{{)",
            rf"\\begin{{{env}}}{{{title}}}",
            text,
        )
    return text


def apply_consistency_fixes(name: str, text: str) -> str:
    if name == "section_02.tex":
        text = text.replace(
            r"\begin{importantbox}" + "\n本章只按 speaker",
            r"\begin{importantbox}{教学边界}" + "\n本章只按 speaker",
        )
        text = text.replace(
            r"\begin{knowledgebox}" + "\n\textbf{CTF 与漏洞发现的差别。} ",
            r"\begin{knowledgebox}{CTF 与漏洞发现的差别}" + "\n",
        )
        text = text.replace(
            r"\begin{importantbox}" + "\n\textbf{脚手架}（scaffolding）",
            r"\begin{importantbox}{术语定义：脚手架}" + "\n\\textbf{脚手架}（scaffolding）",
        )
        text = text.replace(
            r"\begin{warningbox}" + "\n成本下降会改变",
            r"\begin{warningbox}{成本下降会改变安全队列}" + "\n成本下降会改变",
        )
        text = text.replace(
            r"\begin{knowledgebox}" + "\n\textbf{脚手架与架构的关系。} ",
            r"\begin{knowledgebox}{脚手架与架构的关系}" + "\n",
        )
        text = text.replace(
            "属于 \\texttt{nano analyzer} 的一部分。这个专名来自 ASR，后续需要 figure agent 通过画面核验拼写。按 transcript 描述",
            "属于 \\texttt{nano-analyzer} 的一部分。figure manifest 已确认画面和来源写法使用带连字符的拼写。按 transcript 描述",
        )
        text = text.replace(
            "这里 \\(R\\) 表示综合风险，\\(M\\) 表示模型材料能力，\\(A\\) 表示架构，\\(T\\) 表示威胁，\\(C\\) 表示控制措施。第 2 章只展开",
            "这里 \\(R\\) 表示综合风险，\\(M\\) 表示模型材料能力，\\(A\\) 表示架构，\\(T\\) 表示威胁，\\(C\\) 表示控制措施。这里的 \\(C\\) 只作为后文预告，具体控制措施会在第 4 章随 Agent Rule of Two 展开。第 2 章只展开",
        )
        text = text.replace(
            "这里出现了 \\texttt{OpenAnt}，同样保留英文原词并交给 figure agent 核验。speaker 说他和 co-founder 开发并开源了这个更精炼的架构，用来帮助寻找漏洞。对本章来说，\\texttt{OpenAnt} 的意义不在实现细节，而在它代表从“基础架构”走向“更精炼架构”的下一层：一旦流程被产品化、开源化、模板化，它就会获得传播速度。",
            "这里出现了 \\texttt{OpenAnt}，figure manifest 已确认画面拼写为大写 A。speaker 说他和 co-founder 开发并开源了这个更精炼的架构，用来帮助寻找漏洞。对本章来说，\\texttt{OpenAnt} 的意义聚焦于从“基础架构”走向“更精炼架构”的下一层：一旦流程被产品化、开源化、模板化，它就会获得传播速度。",
        )
        if "灾难化命名" not in text:
            text = text.replace(
                "speaker 用 wake-up call 来描述这个现象：没有架构、只有强材料时已经能找到漏洞；那么，当强材料再配上基础架构，会发生什么？再进一步，如果基础架构升级成更强、更精炼的架构，影响面会继续扩大。",
                "speaker 用 wake-up call 来描述这个现象：没有架构、只有强材料时已经能找到漏洞；那么，当强材料再配上基础架构，会发生什么？再进一步，如果基础架构升级成更强、更精炼的架构，影响面会继续扩大。\n\nSpeaker 在这里还用了几组带玩笑色彩的灾难化命名，意思接近“漏洞报告大爆发”。这些词本身不需要逐字翻译成固定术语，重要的是语气：当强材料和可复制脚手架结合，漏洞发现会从少数专家的高成本活动，变成更多人、更多模型、更多流程同时参与的低成本活动。风险来源包括单个高质量发现，也包括大量报告同时涌入后形成的验证与分诊压力。",
            )
    elif name == "section_03.tex":
        text = text.replace(
            "讲者随后进入 “bricks” 视角：OpenClaw 由许多可组合的部件构成，其中他特别强调了一个能表达 job function 的组件。字幕里该术语听起来像 “SOLM” 或 “soulm”，具体拼写需要 figure agent 依据画面确认；本章保留 ASR 不确定性，不擅自改写成确定专名。",
            "讲者随后进入 “bricks” 视角：OpenClaw 由许多可组合的部件构成，其中 \\texttt{SOUL.md} 用来表达 job function。figure manifest 已确认相关画面显示 \\texttt{Example SOUL.md} 与 \\texttt{Agent: Risk Assessor}。",
        )
        text = text.replace("例子是 risk assessor", "例子是 \\texttt{Risk Assessor}")
        text = text.replace("例如 risk assessor 到底", "例如 \\texttt{Risk Assessor} 到底")
    elif name == "section_04.tex":
        text = text.replace("不可可信输入", "不可信输入")
        text = text.replace(
            "规则的口号很短：三类里最多给两类。它不是形式主义口号，因为三类风险的组合会改变事故性质。",
            "规则的口号很短：三类里最多给两类。它有实际工程含义，因为三类风险的组合会改变事故性质。",
        )
        text = text.replace("Claw Hub", "Clawhub")
        text = text.replace(
            "讲者随后提到 NVIDIA、Cisco Defense Claw 以及 Knostic 相关工具，",
            "讲者随后提到 NVIDIA NemoClaw/Openshell、Cisco DefenseClaw 以及 Knostic 相关工具，",
        )
        text = text.replace("Cisco Defense Claw", "Cisco DefenseClaw")
        text = text.replace(
            "讲者还提到 kernel isolation 一类隔离设计。它压低的是 agent 动作逃逸到宿主系统的概率，尤其适用于会执行代码、操作文件或调用底层系统接口的场景。这里要看清楚：隔离不是给 \\(I_u\\) 消毒，也不是自动保护所有 \\(S\\)，它主要限制 \\(E\\) 的作用范围，并降低动作造成系统级破坏的概率。",
            "讲者还提到 kernel isolation 一类隔离设计。它的主目标是限制 \\(E\\) 的作用范围，并降低 agent 动作逃逸到宿主系统后造成系统级破坏的概率，尤其适用于会执行代码、操作文件或调用底层系统接口的场景。",
        )
        text = text.replace(
            "这里的关键不是追求一个“完美安全配置”。讲者明确说，没有完美安全设置。关键是让风险从隐式变成显式，从默认放开变成刻意授权，从事后猜测变成可追溯证据。",
            "这里的关键在于让风险从隐式变成显式，从默认放开变成刻意授权，从事后猜测变成可追溯证据。讲者明确说，没有完美安全设置。",
        )
        if "证据来源分清" not in text:
            text = text.replace(
                "%% FIGURE_SLOT: ch4_audit_logging",
                "需要把证据来源分清：Cisco DefenseClaw 的 audit logging 是 speaker 在 17:46 之后口头点出的控制点；本书选用的可见画面则来自稍早的 Knostic visibility/control 页，它把 telemetry、tool call、LLM request、agent session、hash chain 和 SIEM forwarding 等日志能力显示得更清楚。因此，这张图承担“审计可见性”这一类能力的视觉证据；Cisco 的实现细节主要依赖字幕口头证据。\n\n%% FIGURE_SLOT: ch4_audit_logging",
            )
    elif name == "section_05.tex":
        text = text.replace("Dunbar's number", "Dunbar's Number")
        text = text.replace(
            "Speaker 的 closing point 可以概括为：architecture puts order into things，但 order 会持续变化，因为 materials 在变，环境也在变。",
            "Speaker 的 closing point 可以概括为：architecture puts order into things。画面把这句话归于 Le Corbusier；在本讲语境里，它被用来说明架构的核心动作是把事物放入秩序。这个 order 会持续变化，因为 materials 在变，环境也在变。",
        )
        if "Unprompted" not in text:
            text = text.replace(
                "这也是“落后两个月”仍然可追的原因。这个领域变化快到让领先者也很难长期领先；同时，好的脚手架一旦出现，就会被社区、团队、攻击者快速复用。对企业来说，问题不只是“是否拥有最强模型”，还包括“是否能快速吸收新脚手架，并把它纳入审计、权限和变更管理”。",
                "这也是“落后两个月”仍然可追的原因。这个领域变化快到让领先者也很难长期领先；同时，好的脚手架一旦出现，就会被社区、团队、攻击者快速复用。对企业来说，问题不只是“是否拥有最强模型”，还包括“是否能快速吸收新脚手架，并把它纳入审计、权限和变更管理”。\n\n这里的“两个月”来自一个具体场景。Speaker 说自己在 Unprompted 看到许多看似走在前面的人之后，反而感到鼓舞：这个领域移动太快，领先者和追赶者之间的差距会被可复制脚手架迅速压缩。Markdown 文件降低了学习门槛，也降低了误用门槛；它让后进团队能快速补课，也让攻击者能快速复刻别人的任务蓝图。对安全团队来说，这意味着培训、评审和治理不能只围绕模型 API，还要围绕那些正在被复制的 \\texttt{.md} 蓝图、skills 和岗位说明文件。",
            )
        if "Jensen Huang" not in text:
            text = text.replace(
                "Speaker 给出的实用建议很直接：如果你的组织有 300 人，而 agent 让它表现得像 3000 人，那么去观察真实的 3000 人组织怎样设计组织图、平台团队、治理流程、审批层级和共享服务。个人也一样。很多 IC 早期没有机会管理人，但现在可以通过管理 agent 练习管理技能：拆任务、设验收标准、做复盘、处理失败、建立节奏。",
                "Speaker 给出的实用建议很直接：如果你的组织有 300 人，而 agent 让它表现得像 3000 人，那么去观察真实的 3000 人组织怎样设计组织图、平台团队、治理流程、审批层级和共享服务。个人也一样。很多 IC 早期没有机会管理人，但现在可以通过管理 agent 练习管理技能：拆任务、设验收标准、做复盘、处理失败、建立节奏。\n\nSpeaker 在这里还借用 Jensen Huang 的说法做了一次转译：外界常说每家公司都需要 OpenAI strategy，放到本场语境里又会变成 OpenClaw strategy；更准确的管理命题是 agentic AI strategy。组织要处理的重点已经超出某个供应商或某个工具的采购清单，转向当 agent 把执行体数量、权限流动和任务并发放大之后，企业怎样重新设计授权、审批、平台能力和责任链。若一家 300 人公司在 agent 辅助下表现得像 3000 人公司，安全和管理问题会从“谁会用 AI”升级为“谁能治理被放大的组织行为”。",
            )
    return add_default_box_titles(text)


def figure_block(slot: str) -> str:
    path, label, caption, footnote, options = FIGURES[slot]
    return "\n".join(
        [
            r"\begin{figure}[H]",
            r"  \centering",
            rf"  \includegraphics[{options}]{{{path}}}",
            rf"  \caption{{{caption}\protect\footnotemark}}",
            rf"  \label{{{label}}}",
            r"\end{figure}",
            rf"\footnotetext{{{footnote}}}",
        ]
    )


def insert_figures(text: str) -> str:
    slots = re.findall(r"^%% FIGURE_SLOT:\s*([A-Za-z0-9_]+)\s*$", text, flags=re.M)
    for slot in slots:
        if slot not in FIGURES:
            raise RuntimeError(f"Missing figure metadata for slot: {slot}")
        text, count = re.subn(
            rf"^%% FIGURE_SLOT:\s*{re.escape(slot)}\s*$",
            lambda _match, slot=slot: figure_block(slot),
            text,
            count=1,
            flags=re.M,
        )
        if count != 1:
            raise RuntimeError(f"Could not replace figure slot: {slot}")
    return text


def patched_sections() -> list[str]:
    sections: list[str] = []
    for path in sorted((OUT / "sections").glob("section_*.tex")):
        text = read_text(path)
        patched = apply_consistency_fixes(path.name, text)
        if patched != text:
            write_text(path, patched)
        sections.append(insert_figures(patched).strip())
    return sections


def duration_label(seconds: int | float | None) -> str:
    if not seconds:
        return "25:57"
    total = int(round(float(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def upload_label(upload: str | None) -> str:
    if upload and len(upload) == 8:
        return f"{upload[:4]}-{upload[4:6]}-{upload[6:]}"
    return upload or "2026-05-04"


def build_tex(body: str) -> str:
    info = json.loads(read_text(OUT / "source" / "Fboj9EC7aa4.info.json"))
    actual_title = info.get("title", "Keynote: Claw and Order: Protecting Your Shell")
    channel = info.get("channel") or info.get("uploader") or "SANS Institute"
    upload = upload_label(info.get("upload_date"))
    duration = duration_label(info.get("duration"))
    url = info.get("webpage_url") or "https://www.youtube.com/watch?v=Fboj9EC7aa4"
    note_title = "Claw and Order：保护你的 Shell，AI 安全架构设计比模型更重要"
    return rf"""\documentclass[a4paper]{{article}}

\usepackage[fontset=windows]{{ctex}}
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
  enhanced, breakable, colback=blue!5!white, colframe=blue!70!black, colbacktitle=blue!70!black,
  coltitle=white, fonttitle=\bfseries, title=#1, boxrule=0.8pt, sharp corners
}}

\newtcolorbox{{importantbox}}[1]{{
  enhanced, breakable, colback=yellow!10!white, colframe=yellow!75!black, colbacktitle=yellow!75!black,
  coltitle=black, fonttitle=\bfseries, title=#1, boxrule=0.8pt, sharp corners
}}

\newtcolorbox{{warningbox}}[1]{{
  enhanced, breakable, colback=red!5!white, colframe=red!70!black, colbacktitle=red!70!black,
  coltitle=white, fonttitle=\bfseries, title=#1, boxrule=0.8pt, sharp corners
}}

\newtcolorbox{{dialoguebox}}[1]{{
  enhanced, breakable, colback=green!4!white, colframe=green!45!black, colbacktitle=green!45!black,
  coltitle=white, fonttitle=\bfseries, title=#1, boxrule=0.8pt, sharp corners
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

\setlength{{\parskip}}{{0.55em}}
\setlength{{\parindent}}{{2em}}
\raggedbottom

\begin{{document}}

\begin{{titlepage}}
\setlength{{\parindent}}{{0pt}}
\begin{{tikzpicture}}[remember picture, overlay]
  \fill[black!3] (current page.north west) rectangle (current page.south east);
  \fill[blue!65!black] (current page.north west) rectangle ([yshift=-1.25cm]current page.north east);
\end{{tikzpicture}}
\centering
{{\color{{white}}\Large SANS AI Cybersecurity Summit 2026\par}}
\vspace{{0.75cm}}
{{\huge\bfseries {latex_escape(note_title)}\par}}
\vspace{{0.35cm}}
{{\Large 基于视频：{latex_escape(actual_title)}\par}}
\vspace{{0.45cm}}
{{\large Sounil Yu，Knostic Co-founder and Chief AI Safety Officer\par}}
\vspace{{0.55cm}}
\includegraphics[width=0.86\textwidth,height=0.43\textheight,keepaspectratio]{{source/Fboj9EC7aa4.cover.jpg}}\par
\vfill
\begin{{tcolorbox}}[width=0.92\textwidth, colback=white, colframe=black!55, sharp corners]
\small
\textbf{{视频频道}}：{latex_escape(channel)}\par
\textbf{{发布日期}}：{latex_escape(upload)}\par
\textbf{{视频时长}}：{latex_escape(duration)}\par
\textbf{{视频链接}}：\href{{{url}}}{{\nolinkurl{{{url}}}}}
\end{{tcolorbox}}
\end{{titlepage}}

{{\small
\setlength{{\parskip}}{{0pt}}
\tableofcontents
}}
\newpage

{body}

\end{{document}}
"""


def main() -> None:
    body = "\n\n".join(patched_sections())
    main_tex = build_tex(body)
    write_text(OUT / "main.tex", main_tex)
    report = [
        "# Assembly Report",
        "",
        "- Applied consistency fixes to section files.",
        "- Replaced 21 figure slots with verified figure blocks.",
        "- Wrote `main.tex` with local cover image and video metadata.",
        "- Normalized figure footnotes outside the `figure` environment.",
    ]
    write_text(OUT / "agents" / "assembly_report.md", "\n".join(report) + "\n")


if __name__ == "__main__":
    main()

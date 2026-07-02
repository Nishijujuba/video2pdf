from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(r"D:\Project\video2pdf\newskill-kimi\Braintrust专家解析可观测性新方案")
SECTIONS = ROOT / "sections"

BOX_TITLES = {
    "importantbox": "重点提示",
    "knowledgebox": "补充知识",
    "warningbox": "易错提醒",
    "dialoguebox": "问答片段",
}

REPLACEMENTS = {
    "Agent o11y 成为单独议题，并非因为传统监控工具失效。真正的原因是团队开始追问另一类问题：agent 在生产环境里给出的行为质量能否被信任，改动后的新版本能否更可靠地进入生产。": "Agent o11y 成为单独议题，原因在于团队开始追问另一类问题：agent 在生产环境里给出的行为质量能否被信任，改动后的新版本能否更可靠地进入生产。",
    "目标不止是把演示做得漂亮，还包括让客户能把 agent 放进生产环境，并持续判断它是否在正确工作。": "目标包括把演示做得漂亮、把 agent 放进生产环境，并持续判断它是否在正确工作。",
    "但这个词不能只理解成“仪表盘上有几条曲线”。": "但这个词的含义超出“仪表盘上有几条曲线”。",
    "Agent o11y 并不会丢弃 metrics、trace 和 span。": "Agent o11y 会继续保留 metrics、trace 和 span。",
    "并不等同于系统失控、随机乱跑或无法治理": "仍然需要规则、评测和治理",
    "并非无规则混乱": "仍然需要规则、评测和治理",
    "Agent observability 的第一层难点在于：工程团队不能只问“程序是否按固定路径执行”，还要问": "Agent observability 的第一层难点在于：工程团队既要问“程序是否按固定路径执行”，也要问",
    "评估助理不能只看动作快慢，还要看判断是否有依据。": "评估助理需要同时看动作快慢和判断依据。",
    "它不只涉及营销口吻，还涉及风险控制。": "它同时涉及营销口吻和风险控制。",
    "Agent trace 的难点不只是“字段更多”。真正麻烦的是": "Agent trace 的难点包括“字段更多”，也包括",
    "界面里不只有许多 span，还包含 model calls 和 tool calls；": "界面里同时包含许多 span、model calls 和 tool calls；",
    "这种 UI 的价值不只是“漂亮地展示日志”。它承担了三个工作：": "这种 UI 的价值包括日志展示、质量复盘和系统排障三个工作：",
    "本章只根据这段演讲讨论 Braintrust 面对 agent trace 文本检索压力时选择的系统设计，不推出任何“某类 OLAP 系统普遍不适合 Agent observability”的结论。": "本章只根据这段演讲讨论 Braintrust 面对 agent trace 文本检索压力时选择的系统设计；这里不泛化评价某类 OLAP 系统在所有 Agent observability 场景中的适配性。",
    "这个公式并非 Braintrust 的内部实现描述；它用于表达统一查询层的职责": "这个公式作为教学抽象使用，用于表达统一查询层的职责",
    "Agent observability 工具不能只做给后端工程师看。": "Agent observability 工具需要同时服务后端工程师和领域专家。",
    "如果界面只暴露内部 span id、底层协议和机器指标，SME 会被挡在门外；如果系统能把输入、上下文、工具调用、回答、评分和标注意见组织成可读材料，非工程角色就能参与质量闭环。": "界面若只暴露内部 span id、底层协议和机器指标，SME 会被挡在门外；系统把输入、上下文、工具调用、回答、评分和标注意见组织成可读材料后，非工程角色才能参与质量闭环。",
    "Prompt 在这里不能被理解为“随手写一句话让模型听话”的小技巧；": "Prompt 在这里已经超出“随手写一句话让模型听话”的小技巧；",
    "自然语言入口不等于低门槛随意变更": "自然语言入口仍需工程治理",
    "agent trace 并非一列整齐的数字。它更像": "agent trace 更像",
    "这里的压缩不等同于简单摘要。摘要关心让人快速读懂一条记录，embedding 关心让机器能比较大量记录之间的语义距离。": "这里的压缩服务于机器比较；摘要服务于人工快速阅读。Embedding 让机器能比较大量记录之间的语义距离。",
    "主题建模不能只输出": "主题建模需要超出",
    "情绪不只是正面或负面二分，还包括": "情绪信号包括正面、负面，以及",
    "这里的 ``loop'' 不等同于一次性故障处理流程，更接近": "这里的 ``loop'' 更接近",
    "关键点不止是给分，还包括解释为什么这样给分。": "关键点包括给分，以及解释为什么这样给分。",
    "流程的重点在于把专家判断结构化，而非让专家退出流程：": "流程的重点在于把专家判断结构化，同时保留专家在流程中的判断位置：",
    "它这句话不应被理解为对 ClickHouse 的普遍否定。": "这句话应限定在 Braintrust 当时的特定工作负载中理解。",
    "它这句话": "这句话",
    "困难不只在于行数多、写入快、分析查询重，还在于大量信息埋在自然语言文本里。": "困难同时来自行数多、写入快、分析查询重，以及大量信息埋在自然语言文本里。",
    "系统还要考虑采样、截断、分层存储、索引策略、权限控制和 UI 渲染。": "从工程实现看，系统还要考虑采样、截断、分层存储、索引策略、权限控制和 UI 渲染；这属于基于演讲中体积与实时性压力做出的工程外推。",
    "系统还需要一个统一入口，让这些能力能被同一套查询语言组织起来。Braintrust 在演讲中说他们选择了 SQL 或 SQL-similar language 这条路线。": "系统还需要一个统一入口，让这些能力能被同一套查询语言组织起来。Braintrust 在演讲中说他们选择了 SQL 或 SQL-similar language 这条路线；这里描述的是查询层抽象，不能直接等同于产品内部实现细节。",
    "这里的 $h(\\tau_i)$ 可以来自人工挑选、异常评分、聚类中的代表样本、用户负面情绪、工具失败标记，或某个主题簇的高频问题。关键是让离线实验跳出旧样本循环，持续吸收生产环境暴露的新问题。": "这里的 $h(\\tau_i)$ 可以来自人工挑选；从后续工程设计看，也可以把异常评分、聚类中的代表样本、用户负面情绪、工具失败标记或某个主题簇的高频问题作为候选选择信号。关键是让离线实验跳出旧样本循环，持续吸收生产环境暴露的新问题。",
    "不是对 ClickHouse 的普遍否定": "这句话不应被理解为对 ClickHouse 的普遍否定",
    "不是简单摘要": "不等同于简单摘要",
    "不是一次性故障处理流程": "不等同于一次性故障处理流程",
    "不是一个普通字符串匹配小功能": "远远超出普通字符串匹配小功能",
    "不是单句规则": "常常超出单句规则",
    "不是“随手写一句话让模型听话”的小技巧": "已经超出“随手写一句话让模型听话”的小技巧",
    "不是“采集更多日志”": "已经超出“采集更多日志”",
    "不是": "并非",
}


def repair_text(text: str) -> str:
    for env, title in BOX_TITLES.items():
        text = re.sub(
            rf"\\begin\{{{env}\}}(?!\{{)",
            rf"\\begin{{{env}}}{{{title}}}",
            text,
        )
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    return text


def main() -> None:
    changed = []
    for path in sorted(SECTIONS.glob("section_*.tex")):
        before = path.read_text(encoding="utf-8-sig")
        after = repair_text(before)
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed.append(path.name)
    print("changed=" + ",".join(changed))


if __name__ == "__main__":
    main()

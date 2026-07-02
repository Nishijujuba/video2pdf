from __future__ import annotations

import re
from pathlib import Path


SOURCE_HTML = Path(
    r"D:\Project\2026\BreachWeave\docs\enterprise-autonomous-pentest-agent-methodology\index.html"
)
OUTPUT_DIR = Path(
    r"D:\Project\video2pdf\newskill-kimi\agent_reports\breachweave-cairn-pentest-agent-methodology"
)
OUTPUT_HTML = OUTPUT_DIR / "index.html"


SUPPLEMENT = r"""
<h3 id="3-5-新增视频材料校准">3.5 新增视频材料校准：从能跑通到企业级</h3>
<p>本版额外读取 5 月 29 日新增的两份视频总结 Markdown：冠军方案的智能渗透 Harness 设计，以及 Bytex/Cairn 的答辩视频总结。它们没有推翻原报告的主线，反而把两条边界压得更清楚：BreachWeave 重在控制面、执行面、Observer 与 Memory/Idea 的工程闭环；Cairn 重在最小图协议、三类任务循环和强执行器的外层约束。</p>
<div class="table-wrap"><table>
<thead><tr><th>新增材料</th><th>补强结论</th><th>材料证据</th></tr></thead>
<tbody>
<tr><td>绿盟冠军视频总结</td><td>四条设计理念可以直接转成企业验收项：智能决策有工程边界、执行环境隔离可重置、经验沉淀成共享状态、架构保持稳定骨架。</td><td><code>MinerU_markdown_ai_小分队（绿盟)_的智能渗透Harness设计_2060302661409579008.md:196-227</code></td></tr>
<tr><td>绿盟冠军视频总结</td><td>控制面和执行面经消息总线连接，Solver 与 Observer 在隔离容器里成对运行；这给企业落地提供了可审计、可重置、可扩展的运行时边界。</td><td><code>MinerU_markdown_ai_小分队（绿盟)_的智能渗透Harness设计_2060302661409579008.md:249-300</code></td></tr>
<tr><td>绿盟冠军视频总结</td><td>Manager 的写权限应收敛为受控工具集，调度目标是单位时间与单位 token 的边际收益；企业版可把“赛题卡槽”替换成“授权资产窗口”和“风险预算”。</td><td><code>MinerU_markdown_ai_小分队（绿盟)_的智能渗透Harness设计_2060302661409579008.md:345-442</code></td></tr>
<tr><td>绿盟冠军视频总结</td><td>Memory/Idea 的关键并非多存信息，关键是由 Observer 单点维护、按门槛写入、把事实和假设分层，避免 Solver 自己污染共享状态。</td><td><code>MinerU_markdown_ai_小分队（绿盟)_的智能渗透Harness设计_2060302661409579008.md:608-644</code>, <code>855-903</code></td></tr>
<tr><td>绿盟冠军视频总结</td><td>Context Harness 的迁移重点是摘要、索引、检索三段式：主上下文保留摘要，原始结果落盘，模型按需回看。上下文压缩不能变成证据丢失。</td><td><code>MinerU_markdown_ai_小分队（绿盟)_的智能渗透Harness设计_2060302661409579008.md:700-752</code>, <code>867-887</code></td></tr>
<tr><td>Bytex/Cairn 视频总结</td><td>渗透测试被建模为未知状态空间里的有向搜索；企业要先定义起点、目标状态、可回写事实、失败边界，再谈工具和模型。</td><td><code>MinerU_markdown_起零衍迹-Bytex-⽆径之径-Cairn_AI从渗透测试到通⽤问题的求解_2060302620997464064.md:147-190</code></td></tr>
<tr><td>Bytex/Cairn 视频总结</td><td>Fact、Intent、Hint 构成最小黑板图，bootstrap、reason、explore 构成最小任务循环；失败只要结构化回写，就会成为后续推理材料。</td><td><code>MinerU_markdown_起零衍迹-Bytex-⽆径之径-Cairn_AI从渗透测试到通⽤问题的求解_2060302620997464064.md:211-324</code></td></tr>
<tr><td>Bytex/Cairn 视频总结</td><td>Cairn 的外层协议价值在于把 Codex、Claude Code 等强执行器压进同一张图，而成本、依赖解释和并发瓶颈是必须正面治理的代价。</td><td><code>MinerU_markdown_起零衍迹-Bytex-⽆径之径-Cairn_AI从渗透测试到通⽤问题的求解_2060302620997464064.md:672-787</code></td></tr>
</tbody>
</table></div>
<p>把两份新增材料压缩成企业方法论，可以得到一个更硬的设计判据：一个自主渗透 Agent 若无法回答“状态写在哪里、谁能改状态、动作如何受控、失败如何变成边界、原始证据如何回看、成本何时熔断”，它就还停留在比赛原型或演示系统层面。</p>
<div class="math-block">$$
\mathrm{EnterpriseReady} =
\mathrm{Scope} \cap \mathrm{State} \cap \mathrm{ActionControl} \cap \mathrm{Evidence} \cap \mathrm{Budget}
$$</div>
<p>这个表达的意思很直白：企业级是一组交集约束，单点能力得分无法替代这些硬前提。缺少授权范围，系统会越界；缺少状态平面，系统会失忆；缺少动作控制，系统会失控；缺少证据层，报告无法复盘；缺少预算层，长程探索会持续烧钱。</p>
"""


STYLE = r"""
    *, *::before, *::after { box-sizing: border-box; }
    :root {
      color-scheme: light;
      --bg-primary:#fbfbfd;
      --bg-secondary:#ffffff;
      --bg-tertiary:#f5f5f7;
      --text-primary:#1d1d1f;
      --text-secondary:#6e6e73;
      --accent:#0066cc;
      --accent-hover:#0077ed;
      --accent-soft:#e8f2ff;
      --border:#d2d2d7;
      --code-bg:#f5f5f7;
    }
    html {
      scroll-behavior: smooth;
      scroll-padding-top: 5rem;
      max-width: 100%;
      overflow-x: hidden;
    }
    body {
      margin: 0;
      background: var(--bg-primary);
      color: var(--text-primary);
      font: 16px/1.65 -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
      max-width: 100%;
      overflow-x: hidden;
    }
    img, svg, video, canvas { max-width: 100%; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-hover); text-decoration: underline; }
    .nav {
      position: sticky;
      top: 0;
      z-index: 20;
      background: rgba(251,251,253,.86);
      backdrop-filter: blur(22px) saturate(1.4);
      border-bottom: 1px solid var(--border);
    }
    .nav-inner {
      max-width: 1540px;
      margin: 0 auto;
      padding: .8rem 1.5rem;
      display: flex;
      gap: 1rem;
      overflow-x: auto;
      align-items: center;
    }
    .nav a {
      color: var(--text-secondary);
      font-size: .86rem;
      font-weight: 650;
      white-space: nowrap;
      letter-spacing: 0;
    }
    .hero {
      max-width: 1540px;
      margin: 0 auto;
      padding: 4.5rem 1.5rem 2.5rem;
    }
    .badge {
      display: inline-flex;
      width: fit-content;
      padding: .32rem .72rem;
      border-radius: 8px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: .84rem;
      font-weight: 700;
      margin-bottom: 1.15rem;
      letter-spacing: 0;
    }
    h1 {
      font-size: 3.35rem;
      line-height: 1.08;
      letter-spacing: 0;
      margin: 0 0 1rem;
      max-width: 980px;
    }
    .hero p {
      font-size: 1.12rem;
      color: var(--text-secondary);
      max-width: 780px;
      margin: 0 0 1.2rem;
    }
    .meta { display: flex; flex-wrap: wrap; gap: .6rem; }
    .meta span {
      padding: .42rem .68rem;
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text-secondary);
      background: var(--bg-secondary);
      font-size: .88rem;
    }
    .report-layout {
      max-width: 1540px;
      margin: 0 auto;
      padding: 0 1.5rem 5rem;
      display: grid;
      grid-template-columns: minmax(0, 820px) minmax(430px, 1fr);
      gap: 2rem;
      align-items: start;
    }
    .content {
      width: 100%;
      max-width: 820px;
      min-width: 0;
    }
    .content h3 {
      font-size: 1.72rem;
      line-height: 1.18;
      letter-spacing: 0;
      margin: 2.45rem 0 .95rem;
    }
    .content h4 {
      font-size: 1.08rem;
      margin: 2rem 0 .7rem;
      letter-spacing: 0;
    }
    p { max-width: 72ch; margin: 0 0 1.12rem; }
    ul, ol { max-width: 74ch; padding-left: 1.35rem; margin: 0 0 1.3rem; }
    li { margin: .5rem 0; }
    p, li, th, td, .meta span, .source-index a {
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    code {
      font-family: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
      font-size: .9em;
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: .08rem .3rem;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .source-ref code {
      color: var(--accent);
      border-color: #9bc7ef;
    }
    .math-block {
      margin: 1.6rem 0;
      overflow-x: auto;
      padding: 1rem;
      border-radius: 8px;
      background: var(--bg-secondary);
      border: 1px solid var(--border);
    }
    .table-wrap {
      overflow-x: auto;
      max-width: 100%;
      margin: 1.4rem 0 1.8rem;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--bg-secondary);
    }
    table { border-collapse: collapse; width: 100%; font-size: .92rem; }
    th, td {
      padding: .72rem .9rem;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }
    th { background: var(--bg-tertiary); font-weight: 700; }
    tr:last-child td { border-bottom: 0; }
    .callout-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .85rem;
      margin: 0 0 2rem;
    }
    .callout {
      border: 1px solid var(--border);
      background: var(--bg-secondary);
      border-radius: 8px;
      padding: 1rem;
    }
    .callout strong { display: block; font-size: 1rem; margin-bottom: .35rem; }
    .callout span { color: var(--text-secondary); font-size: .9rem; }
    .update-block {
      border: 1px solid var(--border);
      background: var(--bg-secondary);
      border-radius: 8px;
      padding: 1.2rem;
      margin: 2rem 0;
    }
    .code-pane {
      position: sticky;
      top: 4.2rem;
      max-height: calc(100vh - 5rem);
      overflow-y: auto;
      padding-left: 1.35rem;
      border-left: 1px solid var(--border);
      scrollbar-width: thin;
    }
    #source-evidence {
      max-width: none;
      margin: 0;
    }
    #source-evidence > h2 {
      font-size: 1.45rem;
      line-height: 1.2;
      margin: .2rem 0 .7rem;
      letter-spacing: 0;
    }
    #source-evidence > p {
      color: var(--text-secondary);
      font-size: .92rem;
      max-width: 62ch;
    }
    .source-index {
      display: flex;
      flex-wrap: wrap;
      gap: .45rem;
      margin: 1rem 0 1.2rem;
    }
    .source-index a {
      padding: .34rem .52rem;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--bg-secondary);
      color: var(--text-secondary);
      font-size: .78rem;
      line-height: 1.25;
    }
    .source-card {
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--bg-secondary);
      padding: 1rem;
      margin: .9rem 0;
    }
    .source-card-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: .8rem;
      align-items: start;
      margin-bottom: .65rem;
    }
    .source-card h3 {
      margin: .15rem 0 0;
      font-size: 1.05rem;
      line-height: 1.28;
      letter-spacing: 0;
    }
    .eyebrow {
      color: var(--text-secondary);
      font-size: .72rem;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .source-open {
      white-space: nowrap;
      padding: .34rem .56rem;
      border-radius: 8px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: .78rem;
      font-weight: 700;
    }
    pre {
      margin: .8rem 0 0;
      padding: .85rem;
      overflow-x: auto;
      max-width: 100%;
      border-radius: 8px;
      background: var(--code-bg);
      border: 1px solid var(--border);
    }
    pre code {
      border: 0;
      background: transparent;
      padding: 0;
      font-size: .78rem;
      line-height: 1.5;
      color: var(--text-primary);
      overflow-wrap: normal;
      word-break: normal;
      white-space: pre;
    }
    footer {
      max-width: 1540px;
      margin: 0 auto;
      padding: 2rem 1.5rem 4rem;
      color: var(--text-secondary);
      font-size: .9rem;
    }
    .reveal { opacity: 1; transform: none; }
    .js .reveal {
      opacity: 0;
      transform: translateY(18px);
      transition: opacity .7s cubic-bezier(.16,1,.3,1), transform .7s cubic-bezier(.16,1,.3,1);
    }
    .js .reveal.visible { opacity: 1; transform: translateY(0); }
    @media (max-width: 1180px) {
      .report-layout { grid-template-columns: 1fr; }
      .code-pane {
        position: static;
        max-height: none;
        border-left: 0;
        padding-left: 0;
      }
      .content { max-width: 860px; }
    }
    @media (max-width: 720px) {
      body { font-size: 15.5px; }
      .nav-inner { padding: .75rem 1.1rem; }
      .hero { padding: 3.1rem 1.1rem 1.75rem; }
      h1 { font-size: 2.12rem; overflow-wrap: anywhere; }
      .report-layout { padding: 0 1.1rem 4rem; gap: 1.25rem; }
      .callout-grid { grid-template-columns: 1fr; }
      .source-card-head { grid-template-columns: 1fr; }
      .source-open { width: fit-content; }
    }
"""


def extract(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"could not extract {label}")
    return match.group(1)


def patch_article(article: str) -> str:
    article = article.replace(
        '<h3 id="4-企业级参考架构">4. 企业级参考架构</h3>',
        SUPPLEMENT + '\n<h3 id="4-企业级参考架构">4. 企业级参考架构</h3>',
    )
    article = article.replace(
        "本报告已覆盖 <code>分析文章&amp;答辩ppt</code> 下 8 个 Markdown 文件：",
        "本报告主体底稿覆盖 <code>分析文章&amp;答辩ppt</code> 下 8 个 Markdown 文件；本版额外补读 2 个 5 月 29 日新增视频总结 Markdown：",
    )
    article = article.replace(
        '<tr><td><code>MinerU_markdown_腾讯云智能渗透挑战赛中AI_First的学习与思考_2060249016110424064.md</code></td><td>AI First、黑板可解释性、Harness 与涌现平衡</td></tr></tbody>',
        '<tr><td><code>MinerU_markdown_腾讯云智能渗透挑战赛中AI_First的学习与思考_2060249016110424064.md</code></td><td>AI First、黑板可解释性、Harness 与涌现平衡</td></tr>'
        '<tr><td><code>MinerU_markdown_ai_小分队（绿盟)_的智能渗透Harness设计_2060302661409579008.md</code></td><td>冠军答辩视频总结、控制面/执行面、Manager 工具、Observer、Memory/Idea、Ralph-Loop、Context Harness</td></tr>'
        '<tr><td><code>MinerU_markdown_起零衍迹-Bytex-⽆径之径-Cairn_AI从渗透测试到通⽤问题的求解_2060302620997464064.md</code></td><td>Cairn 答辩视频总结、状态空间、黑板图、三类任务循环、协议成本、依赖与并发边界</td></tr></tbody>',
    )
    return article


def build() -> str:
    source = SOURCE_HTML.read_text(encoding="utf-8")
    article = extract(
        r'<article class="content">(.*?)</article>\s*    <section id="source-evidence">',
        source,
        "article",
    )
    source_section = extract(
        r'<section id="source-evidence">(.*?)</section>\s*</main>\s*<footer>',
        source,
        "source evidence",
    )
    article = patch_article(article)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>企业级自主渗透 Agent 方法论报告</title>
  <script>
    MathJax = {{
      tex: {{
        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
        packages: {{'[+]': ['ams', 'bm', 'mathtools']}},
        processEscapes: true,
        processEnvironments: true,
        macros: {{
          E: '\\\\mathbb{{E}}',
          R: '\\\\mathbb{{R}}',
          N: '\\\\mathbb{{N}}',
          P: '\\\\mathbb{{P}}',
          argmax: '\\\\mathop{{\\\\arg\\\\max}}',
          argmin: '\\\\mathop{{\\\\arg\\\\min}}',
          softmax: '\\\\operatorname{{softmax}}',
          loss: '\\\\mathcal{{L}}'
        }}
      }},
      svg: {{ fontCache: 'global' }},
      options: {{ skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'] }}
    }};
  </script>
  <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.min.js"></script>
  <style>
{STYLE}
  </style>
</head>
<body>
  <nav class="nav">
    <div class="nav-inner">
      <a href="#0-结论先行">结论</a>
      <a href="#1-长程渗透-agent-的本质">长程本质</a>
      <a href="#2-breachweave-冠军方案的可迁移机制">BreachWeave</a>
      <a href="#3-前四名方案的横向启发">横向对比</a>
      <a href="#3-5-新增视频材料校准">新增校准</a>
      <a href="#4-企业级参考架构">参考架构</a>
      <a href="#5-落地风险与治理清单">治理清单</a>
      <a href="#6-建设路线与评估指标">路线指标</a>
      <a href="#source-evidence">右侧源码</a>
    </div>
  </nav>
  <header class="hero">
    <span class="badge">BreachWeave main + Cairn · 左文右码视图</span>
    <h1>企业级自主渗透 Agent 方法论报告</h1>
    <p>左侧是方法论文章，右侧是 BreachWeave 与 Cairn 的关键源码证据卡片。正文中的源码引用固定到 GitHub 行号，右侧卡片保留可读代码片段和跳转链接。</p>
    <div class="meta">
      <span>资料目录 10 个 Markdown</span>
      <span>BreachWeave {get_commit_label('BreachWeave')}</span>
      <span>Cairn {get_commit_label('Cairn')}</span>
      <span>审批式自治</span>
    </div>
  </header>
  <main class="report-layout">
    <article class="content">
{article}
    </article>
    <aside class="code-pane" aria-label="源码证据">
      <section id="source-evidence">{source_section}</section>
    </aside>
  </main>
  <footer>
    <p>Generated from BreachWeave report draft plus 2026-05-29 video-summary Markdown. Source links are pinned to BreachWeave <code>{get_commit_label('BreachWeave')}</code> and Cairn <code>{get_commit_label('Cairn')}</code>.</p>
  </footer>
  <script>
    document.documentElement.classList.add('js');
    const observer = new IntersectionObserver((entries) => {{
      entries.forEach((entry) => {{
        if (entry.isIntersecting) entry.target.classList.add('visible');
      }});
    }}, {{ threshold: 0.08 }});
    document.querySelectorAll('.reveal').forEach((node) => observer.observe(node));
  </script>
</body>
</html>
"""


def get_commit_label(repo: str) -> str:
    if repo == "BreachWeave":
        return "1de8d8692bfc5598c33beca2d4e647c2c9902edb"
    if repo == "Cairn":
        return "e0ef2f850e5805f824815ee38f049e066deeb7d1"
    raise ValueError(repo)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(build(), encoding="utf-8", newline="\n")
    print(OUTPUT_HTML)


if __name__ == "__main__":
    main()

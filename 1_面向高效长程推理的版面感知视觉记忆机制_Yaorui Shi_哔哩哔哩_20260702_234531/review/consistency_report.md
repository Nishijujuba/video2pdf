# Consistency Report

## 顶层结论

Consistency agent 已读取 `outline_contract.md`、`section_01.tex` 到 `section_08.tex`、`figures/figure_plan.md`、`source/video.ai-zh.srt`，并对章节正文做了 6 处局部安全修正。当前正文未发现缺失图片路径、禁用二段转折句式、明显 ASR 错词残留，跨章链路也已补齐到 Q&A 总结章。

仍需主控在集成前决策的风险有 3 个：预算单位写法在 `memory tokens`、`visual tokens`、`image tokens` 之间漂移；高显著区域术语在 `crucial area`、`crucial part`、`crucial memory`、`crucial region` 之间漂移；section_04 使用了未登记在 `figure_plan.md` 的额外布局效率图。

本报告未执行整本 XeLaTeX 编译；结论限于请求文件的静态一致性检查与局部文本补丁。

## 检查范围

- 大纲契约：`outline_contract.md`
- 章节正文：`section_01.tex` 到 `section_08.tex`
- 图计划：`figures/figure_plan.md`
- 原始中文字幕：`source/video.ai-zh.srt`
- 辅助核验：`source/video.ai-en.srt` 用于确认 section_08 中可疑术语 `COSTART`

## 已直接修正

- `section_05.tex:33-39`：把公式符号解释从 Markdown `-` 列表改为 LaTeX `itemize`。
- `section_05.tex:57-61`：把三种记忆策略对比从 Markdown `-` 列表改为 LaTeX `itemize`。
- `section_06.tex:21-28`：把总时间公式符号解释改为 LaTeX `itemize`。
- `section_06.tex:62-65`：把底层能力与策略对齐两点改为 LaTeX `itemize`。
- `section_04.tex:99-101`：去掉读者可见的 `section_04 / section_05 / section_07` 文件名式表达，并补上学习行为、成本、失败模式三章的顺序承接。
- `section_07.tex:85`：补上到 Q&A 总结章的桥接句。
- `section_08.tex:37`：把可疑 `co-start` 表述改为保守说明，记录为中英机器字幕中的未确认 `COSTART`，避免把机器字幕词当成确定概念。

复检结果：章节正文中 `^- ` 残留为 0；请求检查的禁用二段转折句式命中为 0。

## 发现与判断

### 1. 重复定义

- `memory budget` 与 `context budget` 在 `section_01.tex:29-30`、`section_03.tex:15-16`、`section_05.tex:66`、`section_08.tex:52-53` 多次解释。定义方向一致，属于教学性重复。最终集成时可以保留 section_01 的完整定义，把后文改成短提醒。
- 版面抽象 `L(I_i) = (x_i, y_i, s_i, \rho_i)` 在 `section_05.tex:28-39` 出现，`section_08.tex:20-31` 又加入 $w_i$ 作为贡献权重。两处没有数学冲突，但 section_08 可明确说这是“前文抽象的扩展”，避免读者以为是新公式。
- `B` 在 `section_03.tex:19-34` 与 `section_04.tex:18-25` 都表示视觉记忆预算。含义一致。

### 2. 术语不一致

- 高显著区域的写法存在漂移：`crucial area`、`crucial part`、`crucial memory`、`crucial region` 均在正文出现。建议主控统一主术语为 `crucial area（高显著区域）`，在首次出现时说明它可指标题、中心区域、粗体块等高可见版面单元。
- 低显著区域同样有多种写法：`detail area`、`detail memory`、`detailed memory`、`auxiliary information`。建议统一为 `detail area（低显著区域）`，把 `auxiliary information` 只作为 Q&A 原话或补充表达。
- 预算单位存在漂移：`memory tokens`、`visual tokens`、`image tokens` 都用于描述同一类视觉记忆预算。大纲术语表把 `Memory Budget` 定义为最终回答阶段分配给记忆表示的 token 数，常指 `image tokens`。建议正文统一写成“视觉记忆 token（通常表现为 image tokens）”，在保留字幕原话时加一句说明。
- `Mem-$\alpha$` 与图计划中的 `Mem-a` 指向同一前作基线的可能性高，但需要看清晰画面或论文确认。当前正文没有强行扩展其全称，风险可控。
- `Qwen-VL 7B / Qwen2.5-VL 7B / 32B / 72B` 的模型名在 `section_06.tex:58` 已标为机器字幕噪声风险，写法合格；后续 Review agent 仍应按论文图表复核。

### 3. 章节转场

- `section_01` 到 `section_06` 的推进顺序清楚：问题背景、表示转向、训练目标、实验结果、学习行为、成本规模。
- `section_04` 原先跳到 `section_05` 与 `section_07`，中间的成本章节位置不够清楚；已补成学习行为、成本、失败模式三章依次承接。
- `section_07` 原先没有显式引到 Q&A 总结章；已补充桥接句。
- `section_08` 作为总结章能回扣适用条件、边界、实践启示和开放问题，当前没有明显断裂。

### 4. 图路径与图计划

- 所有正文 `\includegraphics` 路径均存在：
  - `figures/selected/fig01_agentic_memory_background.png`
  - `figures/selected/fig02_textual_memory_baselines.png`
  - `figures/selected/fig03_text_to_vision_domain.png`
  - `figures/selected/fig04_budget_aware_training.png`
  - `figures/selected/fig05_memory_budget_results.png`
  - `figures/selected/frame_0032_layout_efficiency_density_emergence.png`
  - `figures/selected/fig06_layout_efficiency_density.png`
  - `figures/selected/fig07_case_study_layout_control.png`
  - `figures/selected/fig08_complexity_analysis.png`
  - `figures/selected/fig09_failure_analysis.png`
- `figures/figure_plan.md` 中声明的 `source/video_480_range.mp4` 已确认存在。
- 遗留风险：`section_04.tex:70` 使用 `figures/selected/frame_0032_layout_efficiency_density_emergence.png`，该图未登记在 `figure_plan.md`；同时 `section_05.tex:13` 使用已登记的 `fig06_layout_efficiency_density.png`，两者共享 `00:15:30--00:16:00` 时间区间。建议主控二选一：把 section_04 的额外图补入图计划，或在最终集成时合并 section_04/section_05 对该图的叙述，减少重复截图。

### 5. 符号一致性

- $H$、$h_i$、$M_{\text{text}}$、$M_{\text{para}}$、$R$、$V$ 在 `section_02.tex` 与大纲符号表一致。
- $Q$、$A$、$V$、$e$、$B$ 在 `section_03.tex` 使用稳定。
- $\mathcal{L}$、$\mathcal{L}_{\text{standard}}$、$\mathcal{L}_{\text{compressed-global}}$、$\mathcal{L}_{\text{detail-highres}}$、$\lambda_0,\lambda_c,\lambda_d$ 与大纲一致。
- $\text{Retention}(B)$ 在 `section_04.tex` 含义清楚。
- $C_{\text{readable}}$ 在 `section_07.tex` 是局部抽象，已在同段解释。
- 主要符号风险仍是预算单位的自然语言写法，数学符号本身没有冲突。

### 6. 禁用句式

- 章节、大纲、图计划中未命中请求检查的中文二段转折句式。
- 章节、大纲、图计划中未命中对应英文二段转折模式。

### 7. ASR 误词残留

- `source/video.ai-zh.srt` 中仍有大量原始 ASR 噪声，例如 `长城推理`、`长相温压缩`、`memory budget记预算`、`G版面`、`Mac os r`、`麦MOSR`、`卖萌卖萌`、`请问2.5BL7B`、`wet transformer`、`基膜` 等。它们属于原始字幕证据，未修改。
- `outline_contract.md:32` 与 `outline_contract.md:620-632` 明确把这些作为写作风险记录，这种保留合理。
- 章节正文复检未发现上述明显 ASR 错词残留。
- `section_08.tex:37` 保留 `COSTART` 作为未确认字幕词，并明确提示字幕与画面不足以确认准确写法。该处理比直接写成 `co-start` 更稳妥。

## 建议的集成前动作

1. 主控统一预算单位写法，推荐“视觉记忆 token（通常表现为 image tokens）”。
2. 主控统一高显著/低显著区域术语，推荐 `crucial area` 与 `detail area`。
3. 主控处理 `section_04` 额外图：登记进 `figure_plan.md`，或合并到 `section_05` 的同时间图解释。
4. 最终集成后运行 XeLaTeX 编译与布局检查，因为本次只做文件级静态一致性检查。

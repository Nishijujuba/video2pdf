# 《MemOCR：面向高效长程推理的版面感知视觉记忆机制》PDF 全局大纲契约

生成身份：Outline agent  
生成日期：2026-07-03  
视频目录：`D:\Project\video2pdf\newskill-kimi\1_面向高效长程推理的版面感知视觉记忆机制_Yaorui Shi_哔哩哔哩_20260702_234531`

## 1. 源材料与证据边界

本契约依据以下本地材料制定：

| 材料 | 用途 | 证据状态 |
|---|---|---|
| `source/video.info.json` | 标题、上传者、时长、Bilibili ID、简介、封面 URL、可用格式 | 元数据主证据 |
| `source/video.ai-zh.srt` | 中文讲述内容、时间戳、Q&A 内容 | 内容主证据；存在少量 ASR 错词 |
| `source/video.ai-en.srt` | 英文术语校准、句义交叉验证 | 术语校准证据；仍属机器字幕 |
| `figures/contact_sheet_30s.jpg` | 全局视觉结构、章节转场、图表候选定位 | 图表召回证据 |
| `figures/candidates_range/frame_*.jpg` | 30 秒采样帧，辅助确认 contact sheet 中的代表画面 | 候选帧证据；最终图仍需密集抽帧复核 |

视频元数据：

- 标题：`1-面向高效长程推理的版面感知视觉记忆机制-Yaorui Shi`
- 论文/演讲英文题名：`MemOCR: Layout-Aware Visual Memory for Efficient Long-Horizon Reasoning`
- 上传者：美团技术团队
- Bilibili ID：`BV1PWTv6FE2f_p1`
- 时长：`24:46`
- 上传日期：`20260701`
- 简介核心：长时间跨度智能体推理需要把不断增长的交互历史压缩进有限上下文窗口；MemOCR 用视觉布局实现自适应信息密度分配，在紧张上下文预算下提升长程推理。

证据使用规则：

- 正文以中文写作，保留关键英文术语，首次出现给出中英对照与直觉解释。
- `video.ai-zh.srt` 中的明显 ASR 错词需要按英文轨、标题、画面校正，例如“长城推理”统一为“长程推理”，“MacOS R / MAMOOCR / 麦 MOSR”等统一为 `MemOCR`。
- 前作名称、模型名称、数据集名称仅在画面或英文字幕足够清晰时写入正文；无法确认的名称放入“待核验”注释，交给 Consistency agent 或 Independent review agent 复核。
- 引入数学表达时先解释直觉，再给公式，再逐项解释符号。
- 讲者的 Q&A 属于实质内容，应纳入最后综合章节。

## 2. 金字塔顶层答案

PDF 的顶层答案：

> MemOCR 的核心贡献是把智能体记忆从一维文本串转换为二维视觉画布，让模型通过版面显著性分配信息密度：关键证据占据更醒目的位置并承受较低压缩，辅助事实与细节进入较低显著区域并承受较高压缩。配合 budget-aware 的强化学习训练后，模型能在极小 memory budget 下保留更多长程推理所需证据；它的主要风险来自细粒度比较信息丢失与视觉记忆过载导致的可读性崩塌。

对应的教学主线：

1. 长程智能体的瓶颈来自“历史越来越长、上下文窗口有限、token 成本均匀”。
2. 文本记忆的统一 token 密度让关键证据与辅助细节承担相同预算成本。
3. 视觉画布提供可调信息密度：字体、位置、层级、显著性可以表达优先级。
4. MemOCR 将文本域的记忆起草与视觉域的记忆读取联合训练，并用不同 budget 和不同证据层级构造训练任务。
5. 实验显示 layout control 在极低 memory budget 下尤其重要，oracle analysis 与 case study 支持“关键信息放在高显著区域”这一机制解释。
6. 方法边界同样明确：比较关系、细粒度事实、过密视觉写入最容易出错；底层视觉语言模型的文字识别与下采样鲁棒性决定压缩极限。

## 3. 目标读者与写作定位

目标读者：

- 熟悉大模型、RAG、Agent 或多模态模型的技术读者。
- 对长上下文压缩、记忆机制、强化学习训练目标有基本兴趣，但未必读过 MemOCR 论文。

PDF 定位：

- 作为技术讲座的中文学习笔记，帮助读者掌握问题、机制、实验和边界。
- 以“概念解释 + 公式化抽象 + 图表证据 + 风险边界”组织。
- 保持忠实字幕和画面，允许对 ASR 噪声做明确校正。

## 4. 文章标题建议

最终 PDF 文章标题建议：

`MemOCR：从一维文本到二维画布的智能体视觉记忆`

备选标题：

- `版面感知视觉记忆：MemOCR 如何压缩长程智能体历史`
- `长程推理中的视觉记忆机制：MemOCR 讲座笔记`

最终文件名应按项目归一化规则生成，建议基名：

`MemOCR_从一维文本到二维画布的智能体视觉记忆`

## 5. 章节边界与写作任务

### section_01.tex：问题总览：长程智能体为什么需要新记忆形态

时间边界：`00:00--04:30`

本章主张：

> 长程智能体的记忆瓶颈来自统一 token 密度：文本记忆把关键证据、辅助事实、背景细节都装进相同类型的 token，导致预算紧张时难以优先保住真正影响答案的证据。

应覆盖内容：

- 演讲主题：`MemOCR: Layout-Aware Visual Memory for Efficient Long-Horizon Reasoning`。
- 长程任务场景：代码智能体、LongCat 这类 `long-horizon task`。
- 旧记忆形态两类：
  - `raw history memory`：把历史交互放入大 corpus，通过检索找回。
  - `textual summary memory`：由智能体把历史压缩为文本摘要。
- 关键问题：文本中不同重要性的内容共享相同 token 密度。
- 直觉例子：约 `100 tokens` 的关键信息可能需要携带约 `900 tokens` 的 supporting facts 与 details。

建议小节：

1. `\subsection{长程推理的真实瓶颈：历史持续增长}`
2. `\subsection{两类传统记忆：原始历史与文本摘要}`
3. `\subsection{统一 token 密度为什么浪费预算}`
4. `\subsection{本章小结}`

需要解释的概念：

- `agentic memory`
- `long-horizon reasoning`
- `memory budget`
- `raw history memory`
- `textual summary memory`
- `supporting facts`

可用类比：

- 文本记忆像把所有资料打印成同一字号的长清单；视觉记忆像在白板上用标题、字号、位置、颜色表达优先级。
- token 预算像旅行箱容量，关键证据像证件，辅助事实像衣物，细节像备用物品。容量紧张时，系统需要知道哪个物品应放在最容易拿到的位置。

图表计划：

- Figure 1：标题页，候选 `figures/candidates_range/frame_0001.jpg`，时间 `00:00--00:45`。
- Figure 2：`Background: Agentic Memory` 三类记忆对比，候选 `figures/candidates_range/frame_0007.jpg`，时间 `02:45--03:30`。

### section_02.tex：核心转向：把记忆从一维文本搬到二维画布

时间边界：`04:30--08:00`

本章主张：

> MemOCR 的第一层创新是表示空间转向：从线性文本摘要转为视觉画布，让信息的重要性通过二维布局、层级和可见性表达出来。

应覆盖内容：

- 传统文本记忆流程：记忆智能体逐段处理原始上下文，迭代更新 memory state，再交给回答模型。
- 讲者提出的范式转移：从 text domain 转到 vision domain。
- MemOCR 流程：
  - 在文本域浏览上下文窗口。
  - 起草 `para-textual memory`。
  - 经过 renderer 渲染成视觉记忆。
  - 在视觉域由模型读取。
- Markdown 渲染为 PDF、HTML 渲染为网页的类比：文本内容经过 layout 后带有层级和视觉显著性。

建议小节：

1. `\subsection{传统文本记忆如何迭代更新}`
2. `\subsection{从 text domain 到 vision domain}`
3. `\subsection{渲染器的角色：把记忆变成可读画布}`
4. `\subsection{本章小结}`

需要解释的概念：

- `para-textual memory`
- `renderer`
- `visual memory`
- `layout control`
- `visibility / salience`

关键公式化抽象：

记忆片段集合可写为：

$$
H = \{h_1, h_2, \ldots, h_n\}
$$

文本记忆把它们压缩为一段线性状态：

$$
M_{\text{text}} = f_{\theta}(H)
$$

MemOCR 先生成可渲染的副文本记忆，再把它映射到视觉画布：

$$
V = R(M_{\text{para}})
$$

符号解释必须贴近正文：

- $H$：历史交互或检索到的上下文片段。
- $h_i$：第 $i$ 个片段。
- $M_{\text{text}}$：线性文本记忆。
- $M_{\text{para}}$：带有版面意图的副文本记忆。
- $R$：renderer，把副文本记忆渲染成图像。
- $V$：最终由视觉模型读取的视觉记忆。

图表计划：

- Figure 3：前作文本记忆流程，候选 `figures/candidates_range/frame_0011.jpg`，时间 `05:00--05:45`。
- Figure 4：`From Text Domain to Vision Domain` 方法总览，候选 `figures/candidates_range/frame_0014.jpg`，时间 `06:15--07:00`。

### section_03.tex：训练目标：让模型在不同预算下学会读视觉记忆

时间边界：`08:00--11:30`

本章主张：

> MemOCR 的第二层创新是训练目标设计：用证据层级和 memory budget 两个维度构造任务，让模型同时学会读取全局关键信息、读取细节信息、处理被压缩后的视觉记忆。

应覆盖内容：

- 证据层级维度：
  - `crucial answer`：高层、总结性、直接影响最终答案的信息。
  - `detail question`：图像上较小、较细、低显著性的事实。
- memory budget 维度：
  - 高预算：图像更清晰，模型可读取完整信息。
  - 低预算：图像被压缩到例如 `32` 或 `64` image tokens。
- 三类训练模式：
  - 标准 QA：无明显压缩，偏全局认知。
  - compressed memory + global question：低预算下读取显著区域。
  - high-resolution memory + detail question：高清下读取细节。
- 多任务 loss 联合优化 memory drafting 与 memory reading。

建议小节：

1. `\subsection{两个训练维度：证据层级与记忆预算}`
2. `\subsection{三类数据增强任务}`
3. `\subsection{联合优化 memory drafting 与 memory reading}`
4. `\subsection{本章小结}`

关键公式化抽象：

可以把训练目标写成概念式多任务损失：

$$
\mathcal{L}
= \lambda_0 \mathcal{L}_{\text{standard}}
+ \lambda_c \mathcal{L}_{\text{compressed-global}}
+ \lambda_d \mathcal{L}_{\text{detail-highres}}
$$

符号解释：

- $\mathcal{L}$：总训练损失。
- $\mathcal{L}_{\text{standard}}$：普通 QA 任务损失。
- $\mathcal{L}_{\text{compressed-global}}$：低 memory budget 下回答全局问题的损失。
- $\mathcal{L}_{\text{detail-highres}}$：高清视觉记忆下回答细节问题的损失。
- $\lambda_0,\lambda_c,\lambda_d$：不同任务的权重；如果论文或画面未给具体值，正文只作为抽象表达。

图表计划：

- Figure 5：`Aligning evidence importance with visibility via layout control`，候选 `figures/candidates_range/frame_0019.jpg`，时间 `08:45--09:30`。
- Figure 6：训练任务二维坐标图，优先从 `09:00--10:30` 密集抽帧，候选 `figures/candidates_range/frame_0019.jpg`。

### section_04.tex：主实验：layout control 如何提升极低预算下的鲁棒性

时间边界：`11:30--15:10`

本章主张：

> 主实验说明 MemOCR 在单跳和多跳长上下文问答中整体优于文本记忆基线；优势在 memory budget 缩小时最明显，原因来自 layout control 对关键证据可见性的保护。

应覆盖内容：

- 实验任务：单跳、多跳长上下文任务；`10K / 30K / 100K` 等上下文长度设定需按清晰画面复核。
- 横轴含义：QA 过程中模型看到的总 token 或字符规模。
- 纵向颜色或分组含义：用于存储最终 memory state 的 memory budget。
- 主要结果：
  - MemOCR 在所有 memory-based agents 中整体性能最佳。
  - 极小预算下仍保留较高能力；字幕提到 `16` image tokens 下保留完整上下文能力的 `80%+`。
  - 文本基线在极端预算下平均性能可能下降 `50%--60%`。
- `oracle analysis`：
  - 把正确答案注入高显著的 crucial part，性能提升更明显。
  - 注入低显著 detail area，提升较弱，部分高压缩或单跳场景可能出现下降。
  - 解释：低效区域被塞入答案后可能进一步损害可读性。

建议小节：

1. `\subsection{实验设置：上下文长度与记忆预算}`
2. `\subsection{主结果：极低预算下的性能保持}`
3. `\subsection{Oracle analysis：证据放在哪里更有价值}`
4. `\subsection{本章小结}`

关键公式化抽象：

记忆预算可以写成：

$$
B = |\text{visual memory tokens}|
$$

压缩鲁棒性可用性能保持率表达：

$$
\text{Retention}(B) =
\frac{\text{Score}(B)}{\text{Score}_{\text{full context}}}
$$

符号解释：

- $B$：视觉记忆在最终回答阶段占用的 image token 数。
- $\text{Score}(B)$：给定记忆预算下的任务分数。
- $\text{Score}_{\text{full context}}$：完整上下文条件下的参考分数。
- $\text{Retention}(B)$：预算压缩后的能力保持率。

图表计划：

- Figure 7：主实验表格与柱状图，候选 `figures/candidates_range/frame_0027.jpg`，时间 `12:30--13:30`。
- Figure 8：Oracle injection / crucial vs detail 对比，优先抽取 `14:00--15:10`，contact sheet 显示该段有关键图。

### section_05.tex：学习行为与案例：模型是否真的学会把证据放到显著区域

时间边界：`15:10--17:30`

本章主张：

> 训练过程分析和案例研究共同支持一个机制解释：随着强化学习收敛，模型更倾向于把直接影响答案的关键证据放进视觉上更醒目的区域，把辅助事实放进较低显著区域。

应覆盖内容：

- 训练 step 分析：
  - 统计模型在 crucial memory 与 detail memory 中放置关键证据的比例。
  - 随训练推进，关键证据进入 crucial memory 的比例上升，进入低显著区域的比例下降。
- Case study：
  - 文本截断会直接丢失关键证据。
  - 无分区视觉记忆在高压缩下文字太小，模型难以读。
  - MemOCR 通过高显著区域保留 `Ocean Band` 与 `Gene MacLellan` 等关键信息。
- 解释重点：布局在这里相当于“记忆索引 + 信息重要性标注 + 压缩率分配器”。

建议小节：

1. `\subsection{训练过程中 evidence placement 的变化}`
2. `\subsection{Case study：16-token budget 下证据如何存活}`
3. `\subsection{为什么显著区域像一个可读索引}`
4. `\subsection{本章小结}`

图表计划：

- Figure 9：关键证据分配随训练 step 变化，优先抽取 `15:10--16:10`。
- Figure 10：Case Study 三路对比，候选 `figures/candidates_range/frame_0035.jpg`，时间 `16:30--17:20`。

### section_06.tex：成本、规模与基线：MemOCR 的收益是否值得

时间边界：`17:30--19:10`

本章主张：

> MemOCR 增加了视觉渲染和读取开销，但在更长上下文中由更高压缩率抵消部分成本；与更大但缺少强化学习训练的基模型相比，受训后的 layout-aware 策略更能支撑复杂多跳推理。

应覆盖内容：

- 复杂度分析：
  - 每个样本处理秒数。
  - 短上下文中额外 overhead 更明显。
  - 上下文变长后，较高压缩率减少最终推理 token，overhead 下降。
- 大基模对比：
  - 字幕提到未经过 RL 的 `Qwen-VL 7B / 32B / 72B` 一类基线；具体模型名需按清晰帧与论文复核。
  - 经强化学习训练的较小模型在平均表现和多跳问题上更有优势。
- 结论：规模增大不能自动替代任务对齐；布局策略与 budget-aware 训练是性能来源之一。

建议小节：

1. `\subsection{计算开销来自哪里}`
2. `\subsection{长上下文下压缩率如何抵消开销}`
3. `\subsection{大模型规模与强化学习对齐的关系}`
4. `\subsection{本章小结}`

图表计划：

- Figure 11：复杂度/每样本耗时表或图，优先抽取 `17:30--18:20`。
- Figure 12：未 RL 大基模对比表，优先抽取 `18:20--19:10`。

### section_07.tex：失败模式：什么信息最容易在视觉压缩里丢失

时间边界：`19:10--21:05`

本章主张：

> MemOCR 的边界集中在两个地方：比较推理里的细粒度关系可能被放错区域，视觉记忆写入过密会让文字可读性急剧下降。

应覆盖内容：

- Failure Mode A：比较推理中细粒度细节丢失。
  - 例子：询问两个人谁更 professional。
  - 模型可能把两个人姓名或标题放在高层级，却把真正支持比较的相对关系放进辅助区域。
  - 最终记忆对答案贡献不足。
- Failure Mode B：memory capacity overflow。
  - 模型写入过多信息。
  - 高压缩后部分文字不可读。
  - 例子：日期类问题正确答案为 `2015-02-14`，模型输出错误日期。
- 风险解释：视觉布局带来密度调节能力，也引入可读性约束；版面仍是受容量限制的容器。

建议小节：

1. `\subsection{Failure Mode A：比较关系被低估}`
2. `\subsection{Failure Mode B：视觉记忆过载}`
3. `\subsection{从失败模式反推安全使用边界}`
4. `\subsection{本章小结}`

图表计划：

- Figure 13：Failure Analysis 总览，候选 `figures/candidates_range/frame_0041.jpg`，时间 `19:20--20:40`。

### section_08.tex：Q&A 与总结：MemOCR 适合哪些长程推理场景

时间边界：`21:05--24:46`

本章主张：

> Q&A 进一步明确了 MemOCR 的适用条件：它特别适合需要保留图文、视频、音频等混合证据的多模态交互；极限受底层视觉语言模型对压缩图像中文字与结构的识别能力约束。

应覆盖内容：

- Q1：视觉布局中不同区域的信息密度如何分配？
  - 讲者回答：依靠强化学习让模型自主管理分配策略。
  - 收敛后，模型倾向于把对 final answer 正确性贡献更大的信息放入 crucial area，把贡献较小的 supporting facts 放入次要区域。
  - 易丢失信息：比较问题中 A 与 B 的相对关系，可能被错误放入 auxiliary information。
  - 潜在改进方向：从 starting point、co-start 或预训练阶段改善。
- Q2：相较纯文本序列化，视觉布局记忆在哪些场景优势最明显？
  - 多模态交互：图片 + 文本、视频 + 音频等混合信息。
  - 极限边界：依赖基模型自身视觉文字识别能力，以及下采样后仍能辨认视觉特征的能力。
  - 只要基模型支撑压缩图像的识别，基于压缩信息继续推理就是可训练能力。
- 终章综合：
  - MemOCR 的机制可压缩为三句话：二维画布表达优先级，budget-aware 训练塑造读取能力，失败边界来自细节关系与视觉可读性。
  - 工程启示：未来 agent memory 需要把“存什么、放哪里、压多狠、读得出吗”作为同一个设计问题处理。

建议小节：

1. `\subsection{Q&A 1：信息密度如何分配}`
2. `\subsection{Q&A 2：多模态场景与压缩边界}`
3. `\subsection{总结：四个问题看懂 MemOCR}`
4. `\subsection{拓展阅读}`

拓展阅读建议：

- 论文主页和代码权重链接应从标题页或元数据中提取清晰 URL 后写入；当前 contact sheet 中 URL 过小，需 figure/review 阶段从原图或论文页复核。
- 若能定位 ICML 2026 论文页面，可补充论文链接；无可靠来源时省略外链。

## 6. 术语表

| 英文术语 | 中文建议译名 | 解释与写作要求 |
|---|---|---|
| MemOCR | MemOCR | 本演讲方法名。正文统一使用 `MemOCR`，避免 ASR 变体。 |
| Layout-Aware Visual Memory | 版面感知视觉记忆 | 把记忆写入二维视觉画布，并通过版面、显著性、大小、位置表达信息优先级。 |
| Long-Horizon Reasoning | 长程推理 | 智能体跨长时间、多步骤、多轮交互完成任务的能力。 |
| Agentic Memory | 智能体记忆 | 智能体保存、压缩、检索和使用历史交互的机制。 |
| Raw History Memory | 原始历史记忆 | 直接保留或索引历史交互，再通过检索使用。 |
| Textual Summary Memory | 文本摘要记忆 | 由智能体把长历史压缩为短文本摘要。 |
| Para-textual Memory | 副文本记忆 | 带有布局意图、可被 renderer 渲染成视觉记忆的中间表示。 |
| Visual Memory | 视觉记忆 | 模型最终读取的图像化记忆。 |
| Renderer | 渲染器 | 把副文本记忆渲染为图像画布的模块。 |
| Memory Budget | 记忆预算 | 最终回答阶段分配给记忆表示的 token 数，常指 image tokens。 |
| Context Budget | 上下文预算 | 模型一次推理可接收的整体上下文容量。需与 memory budget 区分。 |
| Crucial Memory / Crucial Area | 关键记忆区 / 关键区域 | 高显著性区域，用于承载直接影响 final answer 的证据。 |
| Detail Memory / Detail Area | 细节记忆区 / 细节区域 | 低显著性区域，用于承载 supporting facts、背景、细节。 |
| Supporting Facts | 支撑事实 | 辅助推理的事实，重要性低于直接答案证据。 |
| Layout Control | 版面控制 | 通过布局决定信息显著性、密度与压缩承受程度。 |
| Budget-Aware Objectives | 预算感知目标 | 让模型在不同 memory budget 下训练读取视觉记忆的目标设计。 |
| Memory Drafting | 记忆起草 | 在文本域迭代生成或更新副文本记忆。 |
| Memory Reading | 记忆读取 | 在视觉域读取渲染后的视觉记忆并回答问题。 |
| Oracle Injection | Oracle 注入 | 把正确答案或关键证据直接注入某个区域，以测试区域位置对性能的影响。 |
| Failure Mode | 失败模式 | 方法在特定查询或压缩条件下系统性出错的方式。 |
| Readability | 可读性 | 压缩后的视觉记忆仍能被底层视觉语言模型识别的程度。 |

## 7. 符号表

| 符号 | 含义 | 使用位置 |
|---|---|---|
| $H$ | 历史交互或检索到的上下文片段集合 | section_02 |
| $h_i$ | 第 $i$ 个历史片段 | section_02 |
| $M_{\text{text}}$ | 线性文本记忆状态 | section_02 |
| $M_{\text{para}}$ | 副文本记忆状态 | section_02 |
| $R$ | renderer，渲染函数 | section_02 |
| $V$ | 视觉记忆图像 | section_02 |
| $B$ | memory budget，即视觉记忆 token 数 | section_03、section_04 |
| $Q$ | 问题 | section_03 |
| $A$ | 答案 | section_03 |
| $I_c$ | 关键证据信息 | section_03、section_05 |
| $I_d$ | 细节或辅助信息 | section_03、section_05 |
| $s_i$ | 第 $i$ 条信息的视觉显著性 | section_03、section_05 |
| $\rho_i$ | 第 $i$ 条信息的压缩强度或密度配置 | section_03、section_05 |
| $\mathcal{L}$ | 总训练损失 | section_03 |
| $\lambda_0,\lambda_c,\lambda_d$ | 多任务损失权重 | section_03 |
| $\text{Retention}(B)$ | 给定预算下的能力保持率 | section_04 |

可选版面抽象：

$$
L(I_i) = (x_i, y_i, s_i, \rho_i)
$$

解释：

- $L$ 表示 layout function。
- $I_i$ 是一条待写入记忆的信息。
- $(x_i,y_i)$ 是画布位置。
- $s_i$ 是视觉显著性，例如标题层级、字号、颜色、位置。
- $\rho_i$ 是压缩配置，例如写入密度、字号、下采样后可读性。

该公式只作为教学抽象；若论文未给出同名形式，正文需说明这是讲义为帮助理解而建立的抽象。

## 8. 图表计划总表

最终 figure agent 需从原视频或已抽帧目录中密集抽取和复核，contact sheet 仅作为召回线索。每张最终图必须有时间脚注，脚注使用字幕对齐区间。

| 图号 | 建议标题 | 时间区间 | 当前候选 | 教学作用 | 后续要求 |
|---|---|---:|---|---|---|
| Fig. 1 | 标题页：MemOCR 与演讲身份 | `00:00--00:45` | `figures/candidates_range/frame_0001.jpg` | 建立主题与来源 | 可用作封面或第一页素材；需核清论文/代码链接 |
| Fig. 2 | Agentic Memory 背景图 | `02:45--03:30` | `figures/candidates_range/frame_0007.jpg` | 对比 raw history、textual summary、visual memory | 裁掉右侧人像区，保留 slide 主体 |
| Fig. 3 | 传统文本记忆如何工作 | `05:00--05:45` | `figures/candidates_range/frame_0011.jpg` | 解释前作流水线与范式转移前的状态 | 前作名称需复核清晰帧 |
| Fig. 4 | From Text Domain to Vision Domain | `06:15--07:00` | `figures/candidates_range/frame_0014.jpg` | 展示 MemOCR 总流程 | 作为 section_02 核心图 |
| Fig. 5 | Budget-aware objectives 总览 | `08:45--09:30` | `figures/candidates_range/frame_0019.jpg` | 展示训练目标二维结构 | 需要保留右侧坐标图 |
| Fig. 6 | 三类训练任务 | `09:00--10:30` | `frame_0019` 附近密集候选 | 解释 standard / compressed-global / detail-highres | 若图太密，重绘为 TikZ 或矢量示意 |
| Fig. 7 | 主实验：layout control provides compression robustness | `12:30--13:30` | `figures/candidates_range/frame_0027.jpg` | 支撑极低 budget 下性能保持 | 表格可能过密，建议裁表格与柱状图分别呈现 |
| Fig. 8 | Oracle injection：crucial part vs detail area | `14:00--15:10` | contact sheet 中对应实验页 | 解释 layout efficiency 来源 | 需补抽清晰帧 |
| Fig. 9 | 训练 step 中关键证据分配变化 | `15:10--16:10` | contact sheet 中彩色柱状图页 | 证明模型学会分配关键证据 | 可重绘为简化趋势图 |
| Fig. 10 | Case Study：16-token budget 下的三路对比 | `16:30--17:20` | `figures/candidates_range/frame_0035.jpg` | 展示文本截断、无布局视觉记忆、MemOCR 的差异 | 裁成大图，确保 `Ocean Band` 与 `Gene MacLellan` 可读 |
| Fig. 11 | 复杂度与每样本耗时 | `17:30--18:20` | contact sheet 对应复杂度页 | 说明 overhead 随上下文长度变化 | 表格优先重绘或裁局部 |
| Fig. 12 | 无 RL 大基模对比 | `18:20--19:10` | contact sheet 对应 scaling / baseline 页 | 说明训练对齐的重要性 | 模型名需复核 |
| Fig. 13 | Failure Analysis：两类失败模式 | `19:20--20:40` | `figures/candidates_range/frame_0041.jpg` | 解释方法边界 | 可直接作为 section_07 主图，必要时裁上下两块 |

图表插入约定：

- 每张图必须服务于当前段落的具体问题。
- 图注需包含“图中要看什么”和“它证明了什么”。
- 时间脚注格式统一为 `视频时间：00:12:30--00:13:30`。
- 对过密表格，优先重绘摘要图；截图保留在附注或局部裁剪中。
- 任何语义化文件名必须在视觉确认后确定；候选帧保留时间戳或原始编号。

## 9. 跨章节写作约定

结构约定：

- 每个顶层 `\section{...}` 开头先给本章一句话结论。
- 每章按“问题 -> 机制 -> 证据 -> 启示 -> 本章小结”推进。
- 每个顶层章节结尾必须有 `\subsection{本章小结}`。
- 最后一章承担综合职责，纳入讲者 Q&A 的实质回答。

语言约定：

- 中文正文为主，关键英文术语首次出现保留英文。
- 术语译名按本契约术语表执行。
- 避免字幕流水账，字幕只作为事实证据。
- 避免把 ASR 噪声写进结论。
- 避免把教学抽象伪装成论文原公式。

盒子使用：

- `importantbox`：MemOCR 顶层定义、layout control 核心机制、失败模式总括。
- `knowledgebox`：raw history memory、textual summary memory、image token、oracle analysis 等背景概念。
- `warningbox`：ASR 误词、memory budget 与 context budget 混淆、视觉记忆过载、细粒度比较关系丢失。
- `dialoguebox`：仅在 Q&A 中保留短而高信息的问答片段；常规主持串场省略。

数学与公式：

- 公式只用于把机制讲清楚。
- 所有符号必须在公式下方逐项解释。
- 如果公式为讲义抽象，需要明确标注“为帮助理解建立的抽象”。

视觉节奏：

- 图前先提出读图问题。
- 图后解释读者应观察的证据。
- 连续图之间必须有短段落解释。
- 避免把多张大图连续堆叠。

忠实性规则：

- 重要数字需绑定字幕或清晰画面，例如 `16 image tokens`、`80%+`、`50%--60%`。
- 前作、数据集、模型名需复核后写入。
- 对无法确认的信息，使用“画面需复核”或“字幕疑似”标记。

## 10. 章节衔接设计

section_01 到 section_02：

- 从“文本 token 密度均匀导致预算浪费”自然过渡到“二维视觉画布能表达优先级”。

section_02 到 section_03：

- 从“表示形式改变”过渡到“模型如何学会读这种表示”。

section_03 到 section_04：

- 从“训练目标设计”过渡到“这种设计在实验中带来什么收益”。

section_04 到 section_05：

- 从“性能提升”过渡到“提升是否来自预期机制”。

section_05 到 section_06：

- 从“模型学到显著性分配”过渡到“这种策略的成本与规模关系”。

section_06 到 section_07：

- 从“收益与成本”过渡到“方法在哪些情况下失效”。

section_07 到 section_08：

- 从“失败模式”过渡到“讲者 Q&A 给出的适用场景与边界”。

## 11. Writer agents 分工建议

| Writer | 负责章节 | 输入重点 | 输出要求 |
|---|---|---|---|
| Writer A | section_01、section_02 | 背景、问题、范式转移 | 概念解释清楚，避免堆术语 |
| Writer B | section_03、section_04 | 训练目标、主实验、oracle analysis | 公式和图表证据准确，数字需可追溯 |
| Writer C | section_05、section_06 | 学习行为、case study、复杂度、基线 | 把机制解释与实验图绑定 |
| Writer D | section_07、section_08 | 失败模式、Q&A、总结 | 边界写清楚，Q&A 纳入最终综合 |

Consistency agent 必查：

- `MemOCR` 命名统一。
- `memory budget` 与 `context budget` 区分。
- `crucial area`、`detail area`、`supporting facts` 的译法一致。
- 所有 figure 时间脚注存在。
- 前作名、模型名、数据集名无 ASR 噪声。
- 每章有明确本章小结。

Independent review agent 必查：

- 对照 `video.ai-zh.srt` 与 `video.ai-en.srt`，确认重要细节无遗漏。
- 核查 `21:05--24:46` Q&A 内容是否进入终章。
- 检查失败模式是否写得足够具体。
- 检查图表计划中每张最终图是否有实际教学作用。

## 12. 未知风险与后续核验清单

| 风险 | 影响 | 处理方式 |
|---|---|---|
| 中文 ASR 错词较多 | 术语可能写错，影响技术准确性 | 用英文轨、清晰帧、论文信息交叉校正 |
| 英文字幕也是机器轨 | 不能单独作为最终术语依据 | 与画面和上下文一起判断 |
| contact sheet 只做 30 秒采样 | 可能漏掉渐进式 slide 的最终状态 | figure agent 必须围绕目标时间密集抽帧 |
| 部分表格过密 | PDF 中截图可能不可读 | 局部裁剪或重绘摘要图 |
| 前作名称与数据集名称不清 | 容易生成错误引用 | 暂不写死，后续从清晰帧或论文页复核 |
| `R1`、`RL`、`reinforcement learning` 表述可能混杂 | 训练方法表述可能失真 | 统一先写“强化学习 / RL”，具体算法名待核验 |
| `16 tokens` 指代可能是 image tokens / memory tokens | 读者可能误解预算单位 | 正文解释为视觉记忆 token 预算，并注明按字幕与图表核验 |
| 图表截取含讲者人像和直播边栏 | PDF 视觉噪声较大 | 最终图裁出 slide 主体，保留必要来源脚注 |
| Q&A 中提到 `co-start / starting point` 可能为 ASR 误识别 | 改进方向可能写错 | 以“预训练或初始化阶段可能影响能力”表达，具体术语待核验 |

## 13. 最终综合应保留的核心 takeaway

1. MemOCR 的第一性原理是“信息价值不同，压缩强度也应不同”。
2. 文本 token 串很难自然表达优先级；二维视觉画布能用大小、位置、层级和显著性表达优先级。
3. 仅有视觉表示还不够，模型必须通过 budget-aware tasks 学会在低预算下读关键区域。
4. 实验与 oracle analysis 共同说明：关键证据放在 crucial area 时，极低 memory budget 下收益最大。
5. 方法边界同样来自视觉表示本身：比较关系容易被低估，信息写入过密会破坏可读性。
6. 多模态长程交互是最自然的应用场景；压缩极限受底层视觉语言模型的下采样识别能力约束。

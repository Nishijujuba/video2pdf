Decision: PASS

# Independent Review Rerun After Fixes

本独立审阅者复查了 `main.tex`、`section_01.tex` 到 `section_04.tex`、最终 PDF、英中字幕、`figures/*.png`、旧审阅报告 `review/independent_review_rerun.md`，以及 `review/pyramid/summary.md` 和相关 JSON gate 报告。正文 TeX、PDF 和 gate JSON 未被修改；本轮只写入本审阅报告，并把渲染临时文件放在输出目录的 `待删除` 下。

## Old Blocker Resolution

1. `section_04.tex` 的 RAG/AWE 表格已把视频直接支持的瓶颈和工程延伸分开。
   - `section_04.tex:45` 明确说明“主要瓶颈”列只保留视频机制与问答直接支持的说法，右侧列才是工程延伸。
   - `section_04.tex:53` 的 RAG 瓶颈列现在只写“原始对话直接进入检索系统，缺少写入前筛选，检索时可能带入 noisy context”；摘要、元数据、删除和隐私边界被放到工程场景列。
   - `section_04.tex:54` 的 AWE 瓶颈列现在只写 agent 自主决定写入、表现更均衡、长对话更值得考虑；审计、权限和安全约束被放到工程场景列。
   - `section_04.tex:109` 再次声明产品流程、权限、过期、删除和审计建议属于工程延伸。该边界与字幕 `source_ai_en.srt:1845-1955`、`source_ai_zh.srt:1827-1955` 支持的直接内容一致。

2. `section_03.tex` 已补入后期 period 的严重退化结论和 caveat。
   - `section_03.tex:43` 写明后期 period 中多数模型低于 perfect-memory upper bound 的 50%，有些格子低于 random score。
   - 同一行同时给出 caveat：截图单元格适合支持趋势判断，逐格复述精确小数会制造虚假精度。
   - `section_03.tex:47` 在本章小结中重复该关键结论。该修复对应 `source_ai_en.srt:1087-1099`、`source_ai_zh.srt:1087-1099`，也与 `figures/fig07_memory_score_heatmaps.png` 的 key findings 一致。

3. `section_03.tex` 已补入 native long-context LLM 的 off-policy 对照。
   - `section_03.tex:21` 明确写出右侧 native long-context LLM 表格排序相对稳定，off-policy 的伤害主要集中于 memory-agent policy。
   - `section_03.tex:23` 继续解释 reuse bias 对 AWE/RAG/AWI 这类写入、读取、使用链路的影响。
   - `section_03.tex:47` 在小结里把该对照收束成结论。该修复对应 `source_ai_en.srt:959-1015`、`source_ai_zh.srt:959-1015` 和 `figures/fig06_offpolicy_mislead.png`。

4. `section_03.tex` 已补入 update frequency、retained local messages、noise、top-k 的诊断意义。
   - `section_03.tex:85` 写明 memory-write/update frequency 越低，read failure 往往越高，可能原因是 retained local messages 过多造成读取混淆。
   - 同一段说明 noise 与 top-k 是视频点到的调参旋钮：noise 带回无关片段，top-k 过小漏事实，top-k 过大增加干扰。
   - `section_03.tex:93` 在小结里把这些旋钮和 read failure 关联起来。该修复对应 `source_ai_en.srt:1357-1399`、`source_ai_zh.srt:1369-1390`。

5. 图片来源区间已从 caption-only 改成同页底部脚注，PDF 中可读且未脱离图片所在页。
   - TeX 结构检查：`section_01.tex` 有 3 个 `\caption`、3 个 `\footnotemark`、3 个 `\footnotetext`；`section_02.tex` 为 2/2/2；`section_03.tex` 为 4/4/4；`section_04.tex` 为 1/1/1。
   - PDF 文本抽取显示 10 个来源区间分别出现在图所在页：第 3、4、6、8、9、12、13、14、15、16 页。
   - 渲染检查显示每个图页底部都能读到对应“视频时间”脚注，未观察到脚注漂移到下一页、裁切、重叠或不可读问题。

## Pyramid Gate Evidence

`review/pyramid/summary.md` 当前记录：

| Checkpoint | Status | Score |
| --- | --- | ---: |
| outline | pass | 0.89 |
| section_01 | pass | 0.88 |
| section_02 | pass | 0.90 |
| section_03 | pass | 0.88 |
| section_04 | pass | 0.88 |
| main | pass | 0.91 |

本轮还逐个运行 `validate_report.py --input-file ... --enforce-gate` 检查 `outline_contract.md`、四个 section 和 `main.tex` 对应的 JSON 报告，全部返回 `VALID`。因此本审阅者未发现 stale fingerprint、gate blocked、schema drift 或 malformed waiver。

## Visual And Source Checks

- 最终 PDF 共 20 页。重点渲染图所在页：3、4、6、8、9、12、13、14、15、16。
- `figures/*.png` 直接合成检查后，10 张图与正文引用主题一致：动机、benchmark 对比、on/off-policy、框架、结构化数据生成、off-policy 排序、memory score heatmap、external memory、failure trade-off、key takeaways。
- 字幕证据覆盖旧 blocker 所需片段：off-policy/native 对照、后期 period 退化、diagnostic metrics、RAG/AWE/AWI 瓶颈均可在英中字幕对应区间找到。
- 临时视觉证据保存在 `待删除/independent_review_rerun_after_fixes_pdf_pages/`，包括 `full_pages_contact.png`、`bottom_regions_contact.png`、`figures_contact.png` 和单页渲染图。

## Remaining Non-Blocking Risks

- 字幕来自 ASR，存在 `of policy`、`random school`、`native vlm` 等识别噪声。正文已经在关键位置做术语校正，后续若追求论文级引用精度，仍应以原视频音频或正式讲稿复核这些词。
- `fig07_memory_score_heatmaps.png` 的单元格细数值可读性有限；正文当前采用趋势级表述，这一点合适。
- 页底脚注字号偏小，但在本轮渲染图中可读。若未来面向低分辨率打印版本，可考虑略增脚注字号或减少单页图文密度。

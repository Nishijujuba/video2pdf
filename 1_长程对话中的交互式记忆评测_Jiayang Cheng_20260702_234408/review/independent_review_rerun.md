# Independent Review Rerun

Decision: **FAIL / blockers remain**.

The rerun reviewer inspected `main.tex`, `section_01.tex` through `section_04.tex`, `AMemGym_长程对话中的交互式记忆评测与诊断.pdf`, `source_ai_en.srt`, `source_ai_zh.srt`, and the included figure assets. The transcript remains the source of truth; rendered slide figures were also inspected because they carry experimental details.

## Required Fixes

1. **High: `section_04.tex` presents unsupported engineering-governance material as part of the source-backed bottleneck table.**

   In `section_04.tex:44`, the draft says the first two table columns come from the video mechanisms and Q&A. The middle column then adds source-unsupported material: `section_04.tex:52` adds `摘要、元数据与删除策略`, and `section_04.tex:53` adds `审计与安全约束更复杂`. The transcript supports narrower bottlenecks: native long-context LLM mainly struggles to use information as context grows; RAG's bottleneck is write/no-filter raw conversation storage; AWE is the most balanced and lets the agent decide what to write; AWI has a read bottleneck from fixed-size in-context compression (`source_ai_zh.srt:1827-1955`, `source_ai_en.srt:1845-1951`). Governance items such as audit, permissions, expiry, deletion, metadata policy, and safety constraints are reasonable product extensions, and `section_04.tex:108` later labels them as extension. The table itself still blurs the source boundary.

   Required fix: move those governance items into the right-side engineering-extension column or the `来源边界` paragraph, and revise `section_04.tex:44` so the table's evidence boundary is truthful. Keep the source-backed middle column limited to the actual bottlenecks stated in the Q&A.

2. **High: `section_03.tex` underreports the severity of the long-period memory degradation.**

   `section_03.tex:28`, `section_03.tex:37`, and `section_03.tex:41` only say later periods show a clear decline. The source gives a sharper result: most models fall below 50% of the perfect-memory upper bound in later periods, and sometimes drop below the random score (`source_ai_en.srt:1089-1099`, `source_ai_zh.srt:1089-1095`). The rendered slide in `figures/fig07_memory_score_heatmaps.png` also lists this as a key finding.

   Required fix: add this severity statement with a careful caveat that the exact cell values should still avoid over-reading. This detail is important because it changes the reader's interpretation from "performance degrades" to "the normalized memory score can collapse beneath a useful baseline."

3. **Medium: `section_03.tex` loses the native-LM contrast in the off-policy ranking experiment.**

   `section_03.tex:11-23` focuses on AWE dropping from first to third and correctly frames this as harmful for memory agents. The source also states that the right-hand native large-model table has relatively stable rankings, and that off-policy drawbacks are less obvious for native large models while the harm is clear on the agent side (`source_ai_en.srt:959-1015`, `source_ai_zh.srt:959-1015`). This nuance is visible in `figures/fig06_offpolicy_mislead.png`.

   Required fix: add one paragraph after Figure 6 explaining the contrast: the off-policy reuse bias is most damaging when evaluating memory-agent policies, while native long-context model ranking appears comparatively stable in this experiment.

4. **Medium: `section_03.tex` drops diagnostic-analysis details that matter for optimization.**

   The current section covers write/read/utilization failure and the 24% to 7% utilization-failure drop. It omits the diagnostic details around update frequency, local-message confusion, noise, and top-k factors (`source_ai_en.srt:1357-1399`, `source_ai_zh.srt:1373-1387`). The speaker explicitly presents these as reasons diagnostic metrics enable targeted optimization.

   Required fix: add a short subsection or paragraph near `section_03.tex:75` covering this diagnostic layer: lower memory-write/update frequency raises read failure, likely due to excessive retained local messages; noise and top-k are additional knobs mentioned without expansion.

5. **Medium: figure provenance is visible, yet the project contract asked for bottom-footnote provenance.**

   All figures in `section_01.tex`, `section_02.tex`, `section_03.tex`, and `section_04.tex` include concrete source intervals in captions, and rendered pages keep those intervals with their figures. That gives usable provenance. The Bilibili workflow contract, however, asks for source intervals as same-page bottom footnotes. The current caption-only pattern is stable for readers, yet it does not strictly satisfy that contract.

   Required fix: if the workflow contract is enforced literally, convert figure source intervals into same-page footnotes or add a documented waiver. If caption provenance is accepted as the project standard, record that waiver in the review evidence.

## Pass Checks

- `main.tex` includes the required section inputs, cover, `booktabs`, `float`, and highlight box definitions.
- The final PDF rendered successfully through PyMuPDF: 20 pages, no blank pages, no missing CJK text, no visually obvious overlap, and the Q&A table is readable.
- `check_pdf_layout.py` passed with `Flagged pages: 0`.
- `check_output_gate.py --enforce-gate` passed for the workspace; all Pyramid reports are present and valid.
- Figure assets are semantically aligned with their surrounding sections. The slide frames are readable enough for the central claims, including the benchmark comparison, framework overview, heatmaps, external-memory comparison, failure table, and takeaways.
- The source-supported main arc is present: motivation, static/off-policy limitations, on-policy coupling, three-stage AMemGym pipeline, checkpoint memory queries, diagnostic metrics, ranking shift, memory degradation, RAG/AWE/AWI comparison, failure trade-off, takeaways, self-evolution, and Q&A recommendations.

## Non-Blocking Notes

- `section_04.tex:104` and `section_04.tex:108` correctly label broader product advice as engineering extension. The blocker is the table boundary at `section_04.tex:44-54`, where unsupported extension text sits in a column described as source-backed.
- The prior `review/independent_review.md` was a fallback review because the spawned independent agent was blocked. This rerun replaces that limitation with a direct local inspection pass.
- Temporary render evidence was staged under `待删除/independent_review_rerun_pdf_pages/` and `待删除/independent_review_rerun_figures_contact.png`.

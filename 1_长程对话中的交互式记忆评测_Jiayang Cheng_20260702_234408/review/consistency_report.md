# Consistency Report

Scope: `outline_contract.md`, `section_01.tex` through `section_04.tex`, with `source_ai_en.srt` / `source_ai_zh.srt` consulted only for claim support. No source files were edited.

Decision: **FAIL / needs revision before integration**. Core terminology is mostly stable, and the chapter order follows the outline, yet several source-support and integration risks remain.

## Findings

1. **High: unsupported engineering-governance expansion in `section_04.tex`.**  
   `section_04.tex:41`, `section_04.tex:51-58`, `section_04.tex:68`, `section_04.tex:97`, and `section_04.tex:103` add cost, privacy, security, audit, permission, expiry, deletion, `failure ledger`, and three-table product process advice. The source Q&A supports the shorter recommendation: AWE still performs best, short conversations can use native long-context capability, and longer conversations can use AWE (`source_ai_en.srt:1991-2015`). The broader governance material is useful as engineering commentary, yet it needs an explicit label such as “工程延伸” or should be reduced.

2. **Medium: summary hierarchy is repetitive in `section_04.tex`.**  
   `section_04.tex:1` starts “总结、问答与工程落地”, then `section_04.tex:70` starts another top-level “总结与延伸”, with `\subsection{本章小结}` at both `section_04.tex:66` and `section_04.tex:105`. This creates two adjacent summary chapters. A cleaner integration would rename the first top-level section around Q&A and engineering landing, then keep the final top-level section as the whole-document synthesis.

3. **Medium: repeated definitions should be compressed after first use.**  
   `off-policy` / `on-policy` are defined in the outline (`outline_contract.md:28-29`), then again in `section_01.tex:23-29`, `section_03.tex:5-7`, and `section_04.tex:21-23`. `diagnostic metrics` are defined in `section_02.tex:59-67` and restated in `section_04.tex:64`. The definitions agree, so this is not a contradiction. The issue is reader fatigue in the integrated PDF. Later sections should use one-line reminders or cross-references.

4. **Medium: figures after `section_01.tex` lack prose cross-references.**  
   Label extraction found no missing `\ref{...}` targets and no duplicate labels. However, only `section_01.tex` uses prose references (`section_01.tex:15`, `section_01.tex:33`, `section_01.tex:81`). Figures in `section_02.tex`, `section_03.tex`, and `section_04.tex` have labels but no surrounding `图~\ref{...}` references. Add one lead-in or interpretation sentence for each figure so the integrated article reads as a guided note rather than a caption sequence.

5. **Medium: figure time footnotes may detach from floats.**  
   The fragments use `\footnotemark` inside captions and `\footnotetext` after the figure (`section_01.tex:10-13`, `section_02.tex:12-15`, `section_03.tex:16-19`, `section_04.tex:14-17`). For `[htbp]` floats, the footnote can land away from the rendered figure, which weakens the required same-page time provenance. Section 03 uses `\protect\footnotemark`; the other sections do not. Prefer putting the time interval directly in the caption, or wrap figure plus source-time note in a stable non-floating block/minipage.

6. **Low: `memory score` is handled conservatively, with one clarity gap.**  
   The outline says to explain memory score only as normalization between random baseline and perfect memory upper bound (`outline_contract.md:17`). `section_02.tex:57` follows that contract and matches the source (`source_ai_en.srt:795-823`). `section_03.tex:29-39` gives a useful qualitative explanation, but it should briefly point back to the normalization described in section 2 so readers do not treat it as a new metric definition.

7. **Low: `RAG` / `AWE` / `AWI` terminology is stable, with one source-label caution.**  
   The draft consistently explains these by behavior: RAG stores/retrieves from a vector store, AWE lets the agent decide what to write, AWI compresses into an in-context buffer (`section_03.tex:47-65`, `section_04.tex:43-53`). That is the right choice because the ASR/source wording gives unstable expansions for AWE (`source_ai_en.srt:911-927`, `source_ai_en.srt:1179-1195`, `source_ai_en.srt:1899-1903`). Keep behavior definitions and avoid committing to a full English expansion unless the figure is visually confirmed.

8. **Low: table compilation dependency should be verified during integration.**  
   `section_04.tex:45-56` uses `\toprule`, `\midrule`, and `\bottomrule`, which require `booktabs` in the main preamble. The reviewed scope excludes `main.tex`, so this report cannot confirm that dependency. The table is also dense; if PDF layout flags overfull lines, switch to `tabularx` or reduce wording.

## Pass Checks

- Forbidden转折短语 scan: passed. The four requested Chinese target phrases did not appear in the reviewed outline/section fragments.
- ASR corrections: mostly consistent. `AMemGym`, `off-policy`, `on-policy`, `diagnostic metrics`, `RAG`, `AWE`, and `AWI` follow the outline contract.
- Source-supported quantitative claims: supported. AWE rank change is backed by `source_ai_en.srt:899-939`; normalized memory score and long-period decline are backed by `source_ai_en.srt:1031-1099`; utilization failure change from about `24%` to about `7%` is backed by `source_ai_en.srt:1435-1451`.
- Label/ref scan: no missing references and no duplicate labels.
- LaTeX layout scan: no `\clearpage`, `\newpage`, `\pagebreak`, `\vfill`, oversized `height=0.5+\textheight`, `width=\textwidth`, TODO, or `[cite]` placeholder in the reviewed section files.

## Actionable Fixes

1. In `section_04.tex`, mark the cost/privacy/security/governance process material as explicit engineering extension, or trim it to the source-backed short-vs-long conversation recommendation.
2. Rename or merge the two summary top-level sections in `section_04.tex` so the final synthesis has a single clear role.
3. Reduce repeated definitions after their first full explanation; use short reminders and figure/section references.
4. Add prose `图~\ref{...}` references for figures in sections 2-4.
5. Replace caption footnote pairs with a more stable source-time pattern before final PDF compilation.
6. Confirm `booktabs` support and table layout when `main.tex` is assembled.

## Master Resolution After Revisions

The main agent addressed the actionable findings after this report:

- `section_04.tex` now marks broader product and governance advice as `工程延伸` or `来源边界`.
- The first top-level `section_04.tex` heading was changed from `总结、问答与工程落地` to `问答与工程落地`; the final top-level `总结与延伸` now carries the whole-document synthesis role.
- Repeated late definitions were compressed into reminders and cross-references.
- Prose `图~\ref{...}` references were added for figures in sections 2--4.
- Caption footnote pairs were replaced by source time intervals directly in captions.
- `main.tex` includes `booktabs`, and final XeLaTeX compilation succeeded.

The second-pass consistency-agent rerun was attempted, but it did not produce an updated report before downstream work continued. The final local scans and PDF layout check are recorded in `review/independent_review.md`.

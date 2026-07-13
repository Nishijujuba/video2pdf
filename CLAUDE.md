# Project Agent Instructions

## Scope

These instructions apply whenever this project uses either of the following skills:

- `/bilibili-render-pdf`
- `/youtube-render-pdf`

The goal is to produce a complete, accurate, figure-rich Chinese PDF from a video source while keeping long-running work split across isolated subagents.

## Multi-Agent Orchestration

When using `/bilibili-render-pdf` or `/youtube-render-pdf`, the master agent must spawn multiple subagents to isolate context and reduce master-agent context pressure.

Required subagent roles:

- **Outline agent**: define the global table of contents, terminology, symbol table, chapter boundaries, writing contract, and cross-section conventions before chapter writing begins.
- **Writer agents**: use one or more writer agents depending on the number of chapters. Each writer agent must write complete chapter drafts directly and save them as `section_*.tex`.
- **Figure agents**: use one or more figure agents depending on the number of chapters. Figure agents are responsible for frame extraction, image selection, cropping, generating new explanatory diagrams or scripts, writing captions, and adding timestamp footnotes.
- **Consistency agent**: check for duplicate definitions, inconsistent terminology, broken transitions between chapters, missing cross-references, and unclear notation. When a Delivery Glossary exists, the Consistency agent must check `section_*.tex` and `main.tex` against it and record evidence for first-use wording, later-use stability, source-English preservation location, body display strategy stability, and chapter-to-chapter terminology consistency.
- **Independent review agent**: after the first PDF is delivered, spawn an independent review agent. This agent must compare the draft against the original subtitle files and check for missing important details or subtle information. The workflow must continue through interaction and revision until the review agent judges that the TeX content is complete enough.
- **Acceptance Reviewer**: after the final PDF is rendered and before delivery, spawn a read-only Acceptance Reviewer. Before launch, create or refresh `review/acceptance/acceptance_report.skeleton.json` from the project validator so the reviewer receives the fixed report shape, current artifact fingerprints, and rendered-page slots. This reviewer may inspect only final delivered artifacts, `docs/acceptance/acceptance_criteria.v1.json`, `review/acceptance/allowed_artifacts_manifest.json`, `review/acceptance/acceptance_report.skeleton.json`, and rendered page evidence under `review/acceptance/rendered_pages/`. It writes `review/acceptance/acceptance_report.json` and optional `review/acceptance/acceptance_summary.md`. When acceptance fails, repair subagents perform artifact changes, the PDF is rendered again, stale evidence is refreshed, a fresh skeleton is generated, and a fresh Acceptance Reviewer run decides delivery.

### Subagent Wait Policy

For Acceptance Reviewer subagents, use `wait_agent` with `timeout_ms: 3600000` so the reviewer has a one-hour wait window.

A `wait_agent` timeout means only that the current wait window expired. It does not mean the subagent failed, stalled, or produced an invalid review. Do not interrupt or close a normally running Acceptance Reviewer because of `wait_agent` timeout alone. Interrupt or close it only when the reviewer reports an explicit error, violates the allowed artifact boundary, exceeds the one-hour wait window without producing required output, or the user asks to stop.

### Visual Acceptance Input Policy

Visual acceptance must inspect every rendered PDF page image individually. A `contact_sheet`, montage, overview image, selected key pages, thumbnails, sampled pages, or any reduced visual input set is auxiliary navigation material only and cannot serve as the basis for a pass/fail decision.

The Acceptance Reviewer must inspect each `review/acceptance/rendered_pages/page_*.png` file corresponding to pages `1..page_count` and must record one page-specific `visual_scan_evidence.pages_checked[]` entry for every rendered PDF page.

If the reviewer cannot complete this per-page inspection within the allowed wait window, delivery must stay blocked. The coordinator must preserve the blocked state and run a fresh Acceptance Reviewer. The coordinator must not shrink the evidence set, replace per-page review with a contact sheet, or convert the task into key-page sampling to obtain a pass.

`delivery_guard.py` proves freshness, manifest membership, path boundaries, and rendered-page coverage. It cannot prove that the reviewer actually inspected every page. A structurally valid `acceptance_report.json` based on reduced visual input is invalid workflow evidence and must be treated as delivery-blocking.

## Final Delivery Guard

Every render workflow must bind final delivery to the session-scoped active target `.codex/delivery-targets/sessions/<session_id>/current.json`, the project task index `.codex/delivery-targets/task-index.json`, and the video-level `review/acceptance/delivery_target.json` before delivery. The lifecycle stages are `generating`, `ready_for_delivery`, `accepted`, `delivered`, `blocked`.

The video-level target records `attempt_limit: 3`, the final PDF, the main TeX file, `review/acceptance/allowed_artifacts_manifest.json`, `review/acceptance/acceptance_report.json`, and `review/acceptance/delivery_guard_report.json`. Newly generated video PDFs must also have final compile provenance at `review\latex\compile_report.json`. `acceptance_report.json is the only machine-readable delivery decision source`. `delivery_guard_report.json is a mechanical proof of freshness and contract validity`. The compile report cannot replace acceptance_report.json; it only proves guarded compilation provenance for `delivery_guard.py check`.

Before final delivery, run `delivery_guard.py check` against the session-scoped current target. The legacy `.codex/delivery-targets/current.json` singleton path is unsupported for `delivery_guard.py check`. Final delivery is allowed only after a fresh passing guard report exists. Do not deliver this PDF until delivery_guard.py records a fresh pass.

The task index records task-index ownership for startup, recovery, and observability. It is not a Stop hook blocking source; the Stop hook does not scan all active tasks. A new session may take over a video output directory only through explicit handoff recorded in `.codex/delivery-targets/task-index.json`.

When acceptance fails, preserve attempt evidence under `review/acceptance/attempts/attempt_01/`, `attempt_02/`, and `attempt_03/` as needed. After the third failed attempt, write `review/acceptance/manual_repair_brief.md` and set the target stage to `blocked`.

After successful delivery, archive the session target with `clear-target --session-id` so stale delivered state cannot affect later work.

The project Stop hook calls `delivery_guard.py hook-stop`. The Stop hook reads the official hook `session_id`, resolves `.codex/delivery-targets/sessions/<session_id>/current.json`, and may run `delivery_guard.py check` once for `ready_for_delivery` or `accepted`. The Stop hook must not launch the Acceptance Reviewer, repair subagents, page rendering, or LaTeX compilation. UserPromptSubmit remains out of scope.

Official Stop hook command on Windows:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B D:\Project\video2pdf\newskill-kimi\.agents\skills\final-delivery-acceptance\scripts\delivery_guard.py hook-stop
```

Official hook stdin payload:

```json
{"session_id":"<session_id>"}
```

The Stop hook resolves the active target from `.codex\delivery-targets\sessions\<session_id>\current.json`.

Blocking text must include: Final Delivery Guard blocked delivery. Use a separate Acceptance Reviewer subagent and repair subagents. Do not deliver this PDF until delivery_guard.py records a fresh pass.

## Required Tool Paths

The project has an existing `uv` environment management setup.

Use these local paths:

- Skill virtual environment: `D:/Project/video2pdf/kimi/.venv/`
- Skill tool directory: `D:\Project\video2pdf\kimi\tools`
- LaTeX engine path for the guarded wrapper `--engine` argument: `D:\kits\MiKTex\miktex\bin\x64\xelatex.exe`

## Cookies

Cookie files are already expected at:

- `C:\Users\juju\Downloads\www.bilibili.com_cookies.txt`
- `C:\Users\juju\Downloads\www.youtube.com_cookies.txt`

Use the relevant cookie file first. If the cookie is expired or rejected, stop and ask the user to provide a refreshed cookie file before continuing.

## Output Layout

All produced files for a video must be placed under:

```text
D:\Project\video2pdf\newskill-kimi\workspace\{video-name}_{task-start-timestamp}
```

`D:\Project\video2pdf\newskill-kimi\workspace` is the default parent for all new video output directories. Root-level video output directories under `D:\Project\video2pdf\newskill-kimi\` are legacy or pre-migration directories only, not the target location for new work.

The `{video-name}` directory must be derived from the original video title. Use the task start time in the local machine timezone for `{task-start-timestamp}`, formatted as `yyyyMMdd_HHmmss`.

Normalize the title before creating the directory:

- preserve Unicode letters and numbers
- preserve only these special characters: ASCII space and `_`
- replace every other character with `_`
- collapse repeated spaces, collapse repeated `_`, and trim leading or trailing spaces, `_`, and `.`
- if the normalized title is too long, shorten it while preserving the timestamp suffix

The final delivered PDF must use the same normalization rule. Its base filename must be derived from the PDF article title when a clear article title exists; otherwise use the original video title. The `.pdf` extension is appended after normalization.

Each video output directory must include a subfolder named:

```text
待删除
```

Place disposable intermediate files in `待删除`. Do not permanently delete intermediate files during the workflow.

## Existing Output Normalization

When reorganizing already generated video documentation directories, use:

```text
python scripts\normalize_video_workspace.py --root D:\Project\video2pdf\newskill-kimi
```

The script defaults to a dry run and writes `workspace\migration-plan.csv` and `workspace\migration-plan.json`. It only moves directories when `--apply` is passed.

For this historical migration, high-confidence legacy root-level directories move as whole directories into `workspace\<normalized-video-documentation-name>`. Low-confidence directories move as whole directories into `workspace\低置信目录\<original-directory-name>`. A directory is a valid video output only when internal final-delivery identity evidence exists, such as a delivered video name, article title, `main.pdf`, `notes.pdf`, or a direct `build\*.pdf` delivered PDF. Directory `CreationTime` is not authoritative for the migration date; infer the date from durable artifacts, with PDF time as a fallback.

## Language and Writing Requirements

Subtitle download priority:

- Prefer English subtitles when downloading subtitles.

Working language:

- Use English when collecting materials, reasoning, planning, and organizing intermediate results.
- Use Chinese for the final written PDF content.

English teaching and IELTS videos:

- When the source video is about English teaching, IELTS preparation, IELTS speaking, IELTS writing, or similar language-learning topics, prioritize English subtitles first.
- If English subtitles are unavailable or unusable, use local Whisper transcription before relying on non-English subtitles or a purely translated transcript.
- The final PDF for these videos should preserve as much original English wording as useful, especially authentic phrasing, high-scoring expressions, sample answers, model essays, sentence patterns, collocations, discourse markers, and examiner-style wording.
- Explain advanced English expressions with Chinese explanations, usage notes, register, typical contexts, and learner-facing examples.
- For IELTS writing or speaking model answers, include Chinese-English parallel presentation where helpful so the reader can compare the original expression with the Chinese meaning.
- Avoid producing an all-Chinese PDF for these videos; the PDF should function as a bilingual learning note with the English source language treated as primary evidence.

The final PDF should be comprehensive, technically precise, and faithful to the original subtitle content.

### Formula Information-Gain Gate

For important terms and concepts, provide necessary explanations using clear prose first. Use analogies, comparisons, contrasts, examples, tables, or diagrams when they improve understanding.

Use LaTeX mathematical notation or formulas only when one of these conditions is met:

1. the source material itself contains a formula, equation, algorithm, statistical relation, or quantitative model;
2. the concept is inherently mathematical, computational, algorithmic, statistical, or physically quantitative;
3. the formula adds reasoning value that prose cannot express as clearly, such as a constraint, trade-off, threshold, dependency, proportional relation, or reusable decision rule.

Avoid inventing ad hoc formulas for qualitative life experience, business discussion, management reflection, creator growth, personal biography, or interview narratives when the formula only renames ideas already stated in prose.

Before adding an invented teaching formula, apply this information-gain test:

- What new relationship does the formula reveal beyond the previous sentence?
- Can the reader use it to reason, compare, estimate, or make a decision?
- Will the symbol definitions reduce cognitive load compared with a short sentence, list, or table?
- Does the formula avoid repeating the same semantic content twice?

If the answer is weak, use concise prose, a bullet list, or a table instead of a formula.

When an invented formula is still useful, label it as an interpretive teaching model, keep variables minimal, define symbols only once, and avoid restating the same idea immediately after the formula.

A formula is allowed only when it earns its cognitive cost.

## Agent skills

### Issue tracker

Project 2.0 specs and tickets are tracked as human-approved GitHub Issues in `Nishijujuba/video2pdf`. The completed local planning record under `docs/prd/` and `docs/issues/` is the read-only Project 1.0 archive. See `docs/agents/issue-tracker.md`.

### Triage labels

The repo uses the default five-label triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a single-context domain-doc layout: root `CONTEXT.md` plus root `docs/adr/`. See `docs/agents/domain.md`.

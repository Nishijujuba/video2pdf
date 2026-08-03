---
name: final-delivery-acceptance
description: Run the final, read-only delivery acceptance gate for rendered video-to-PDF outputs.
---

# Final Delivery Acceptance

Use this skill after the final PDF has been rendered and before delivery of a `/bilibili-render-pdf` or `/youtube-render-pdf` result.

Global Gate status is `active_global_gate`. The Acceptance Reviewer is an independent read-only semantic actor working from provider-created Task Envelopes. Delivery Quality policy, Role Projections, the immutable input binding, and the Acceptance Report v2 Skeleton define the allowed evidence and decision ownership. Platform Kernel authority remains unchanged: Bilibili and YouTube still use their active Legacy coordination until their separate platform cutovers.

Acceptance Report v1 is rejected. Per-run fallback, v1-to-v2 translation, dual authority, and a synthetic Legacy Run are forbidden. A Legacy directory enters the active gate only through a fresh Run-record-free Legacy Acceptance Input Set created by `legacy-acceptance-adopt`; a Kernel input retains its real Run authority. Both tracks then use the same `acceptance-prepare`, `acceptance-patch-commit`, `acceptance-materialize`, `acceptance-reconcile`, and active Delivery Guard boundaries.

## Context Boundary

Allowed inputs are the exact path-and-SHA read set in the provider-created Task Envelope, including:

- final delivered artifacts listed in `review/acceptance/allowed_artifacts_manifest.json`
- current Delivery Quality policy and Role Projection bindings
- rendered page evidence under `review/acceptance/rendered_pages/`
- the fail-closed Acceptance Report v2 Skeleton `review/acceptance/acceptance_report.skeleton.json`
- for non-English teaching PDFs, `review/acceptance/delivery_glossary.json` only when it is listed in `review/acceptance/allowed_artifacts_manifest.json`

Forbidden context:

- generation notes
- writer drafts
- chat history
- repair discussion
- `work/`
- `review/pyramid/`
- `review/consistency/`
- intermediate drafts
- intermediate files outside the allowed manifest

The reviewer may write only its staged Judgment Patch inside the provider-created Attempt directory. Publication authority belongs to the provider:

- `acceptance-patch-commit` validates and commits the staged Judgment Patch
- `acceptance-materialize` publishes canonical `review/acceptance/acceptance_report.json` and its immutable companion records

The reviewer must not modify final artifacts, TeX source, figures, tables, criteria files, generated page images, subtitles, source materials, or intermediate files.

## Required Workflow

1. For a Legacy directory, run `legacy-acceptance-adopt` with explicit final-artifact, compile-provenance, policy, manifest, and rendered-page paths. It creates a fresh immutable input set and never creates `workflow/run.json`.
2. For a Kernel input, use the current committed final-quality input binding from the real Run and Control Store authority.
3. Run `acceptance-prepare` to create the Acceptance Execution Context, fixed Skeleton, Task Envelopes, Claims, and exact allowed read sets.
4. Launch the independent Reviewer named by each Task Envelope. Each Reviewer writes one bounded Judgment Patch and cannot publish the report.
5. Run `acceptance-patch-commit` for every patch. The provider validates task identity, fencing, independence, exact reads, complete rule coverage, and current fingerprints before committing it.
6. Run `acceptance-materialize` only after all required Patches are committed. Use `acceptance-reconcile` after an interrupted Patch or report publication.
7. Bind the session-scoped delivery target to the canonical Acceptance Report v2 and its explicit Global Gate authority path and SHA, set routing to `ready_for_delivery`, then run `delivery_guard.py check`.

`acceptance_report.json is the only machine-readable delivery decision source`. The active Guard accepts only a current passing Acceptance Report v2 with committed Patch, execution, report-publication, Global Gate, and fingerprint authority. An optional Markdown summary may explain the decision, and it cannot override the JSON result.

## Visual Input Scope

Visual acceptance must inspect every rendered PDF page image individually. A `contact_sheet`, montage, overview image, selected key pages, thumbnails, sampled pages, or any reduced visual input set is auxiliary navigation material only and cannot serve as the basis for a pass/fail decision.

The Acceptance Reviewer must inspect each `review/acceptance/rendered_pages/page_*.png` file corresponding to pages `1..page_count` and must record one page-specific `visual_scan_evidence.pages_checked[]` entry for every rendered PDF page.

If the reviewer cannot complete this per-page inspection within the allowed wait window, delivery must stay blocked. The coordinator must preserve the blocked state and run a fresh Acceptance Reviewer. The coordinator must not shrink the evidence set, replace per-page review with a contact sheet, or convert the task into key-page sampling to obtain a pass.

`delivery_guard.py` proves freshness, manifest membership, path boundaries, and rendered-page coverage. It cannot prove that the reviewer actually inspected every page. A structurally valid `acceptance_report.json` based on reduced visual input is invalid workflow evidence and must be treated as delivery-blocking.

## Report Duties

The Acceptance Reviewer must:

- evaluate every criterion from the criteria file, even after finding a failure
- run a full final text scan for style criteria
- run a full final formula scan for `formula_information_gain`
- inspect every rendered PDF page image for visual criteria
- write one `criterion_results[]` entry for every configured criterion
- write one `visual_scan_evidence.pages_checked[]` entry for every rendered PDF page
- write `scan_evidence.formulas_checked[]` for the formula criterion, with one entry for every reader-facing body formula
- include artifact-grounded evidence for each failed criterion
- include revision guidance for each failed criterion
- declare `generation_process_used: false`
- keep `review_context_used.artifacts_read` inside the manifest final artifacts plus the criteria file and `review/acceptance/acceptance_report.skeleton.json` when the skeleton was used
- replace every skeleton placeholder before writing the final `review/acceptance/acceptance_report.json`
- list `review/acceptance/delivery_glossary.json` in `review_context_used.artifacts_read` only when the manifest includes it
- when a manifest-listed Delivery Glossary is present, check for every Delivery Glossary term found in final body text that the body wording follows `body_display_strategy`, that the original English expression appears only where `where_to_preserve_english` allows, and that each finding includes artifact-grounded evidence
- bind the report to current artifact fingerprints

For glossary-backed non-English teaching PDFs, the Acceptance Reviewer must treat `body_display_strategy` as the rule for reader-facing body prose and `where_to_preserve_english` as the rule for where the original English expression may appear. If the Delivery Glossary says `grief` uses `chinese_primary_only` plus `delivery_glossary_only`, final body text should not make `grief` the sentence subject; body prose should use the Chinese primary wording, and the English expression should remain recoverable through the manifest-listed glossary.

For `formula_information_gain`, the reviewer must classify every body formula as `source_material`, `inherent_quantitative`, or `interpretive_teaching_model`. Each entry in `scan_evidence.formulas_checked[]` must include `location`, `formula_excerpt`, `source_type`, `status`, and `information_gain_summary`. If the final text contains no body formulas, the reviewer must write `formulas_checked: []` and `no_body_formula_found: true`. A formula fails when it only restates adjacent prose, wraps a list as `Y = f(...)` without a decision boundary, or adds symbols without lowering reader cognitive load.

## Failure And Repair Loop

A failed, missing, malformed, stale, or forbidden-context Acceptance Report blocks delivery.

When acceptance fails, the coordinator builds a repair brief from:

- `failed_criteria[]`
- failed `criterion_results[]`
- `visual_scan_evidence`
- each failed criterion's `revision_guidance`

Repair subagents may edit TeX, figures, tables, caveat placement, or other final artifacts needed to satisfy the failed criteria. The Acceptance Reviewer remains read-only.

After repair, the workflow must rerender affected final artifacts, refresh rendered page evidence, refresh any upstream evidence invalidated by the repair, and start a fresh Acceptance Reviewer run from final delivered artifacts plus criteria only. Old reports remain audit evidence and cannot approve changed artifacts.

## Delivery Target And Guard

Every active delivery workflow is represented by a session-scoped `.codex/delivery-targets/sessions/{session_id}/current.json` file, whose CLI placeholder form is `.codex/delivery-targets/sessions/<session_id>/current.json`, plus the project task index `.codex/delivery-targets/task-index.json` and the video-level `review/acceptance/delivery_target.json`. The lifecycle stages are `generating`, `ready_for_delivery`, `accepted`, `delivered`, `blocked`.

The video-level target binds the final PDF, main TeX file, `review/acceptance/allowed_artifacts_manifest.json`, `review/acceptance/acceptance_report.json`, and `review/acceptance/delivery_guard_report.json`. Newly generated video PDFs must also have final compile provenance at `review\latex\compile_report.json`. Compile provenance binds current TeX/PDF fingerprints plus guarded wrapper producer, wrapper contract, wrapper mode, wrapper script fingerprint, and final-mode invocation arguments. It must record `attempt_limit: 3`.

`acceptance_report.json is the only machine-readable delivery decision source`. `delivery_guard_report.json is a mechanical proof of freshness and contract validity`. The guard proves freshness, manifest membership, rendered page coverage, path boundaries, compile provenance for newly generated video PDFs, and enforced Acceptance Report decision. It does not replace the Acceptance Reviewer.

The Acceptance Reviewer evaluates delivery quality from final delivered artifacts and rendered page evidence. `review\latex\compile_report.json` is compile provenance for `delivery_guard.py check`. A compile report cannot replace acceptance_report.json, cannot override `overall_status`, and cannot serve as Acceptance Reviewer quality judgment.

The task index records task-index ownership for startup, recovery, and observability. It is not a Stop hook blocking source; the Stop hook does not scan all active tasks. Ownership changes require explicit handoff through `task-handoff --from-session-id "<from_session_id>" --to-session-id "<to_session_id>" --target-file "<video-output-dir>\review\acceptance\delivery_target.json" --stage "<stage>" --previous-owner-status "<superseded-or-abandoned>"`.

Before delivery from a non-hook render or acceptance workflow, run `delivery_guard.py check` with the explicit session-scoped current target:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\delivery_guard.py check --current-target ".codex\delivery-targets\sessions\<session_id>\current.json"
```

The legacy `.codex/delivery-targets/current.json` singleton path is unsupported for `delivery_guard.py check`.

Do not deliver this PDF until delivery_guard.py records a fresh pass.

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

## Old-PDF Repair Mode

Old-PDF repair requires an explicit video_output_dir unless the PDF is already inside one valid video output directory. The preparation command is:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\delivery_guard.py old-pdf-prepare "<pdf-path>" --session-id "<session_id>" --video-output-dir "<video-output-dir>"
```

When the PDF is already inside a valid video output directory, `--video-output-dir` may be omitted. Isolated PDFs must not trigger broad workspace search.

The prepare command writes `.codex/delivery-targets/sessions/<session_id>/current.json`, `.codex/delivery-targets/task-index.json`, `review/acceptance/delivery_target.json`, and the project task-index ownership entry for the selected video output directory. If another active session owns that video output directory, preparation blocks before writing target state.

When a new Codex session must continue a bound video output directory, perform explicit handoff with:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\delivery_guard.py task-handoff --from-session-id "<from_session_id>" --to-session-id "<to_session_id>" --task-index ".codex\delivery-targets\task-index.json" --video-output-dir "<video-output-dir>" --target-file "<video-output-dir>\review\acceptance\delivery_target.json" --stage "ready_for_delivery" --previous-owner-status "superseded"
```

Repair subagents may inspect and modify only files inside that video output directory. A failed attempt is archived with:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\delivery_guard.py record-failed-attempt --session-id "<session_id>" --current-target ".codex\delivery-targets\sessions\<session_id>\current.json" --video-output-dir "<video-output-dir>" --attempt-number 1 --changed-file main.tex
```

Each failed attempt preserves `acceptance_report.json`, optional `acceptance_summary.md`, `repair_brief.md`, and `changed_files.json` under `review/acceptance/attempts/attempt_01/`, then `attempt_02/` and `attempt_03/` when needed. After the third failed attempt, write `review/acceptance/manual_repair_brief.md`, set the target stage to `blocked`, and stop automatic repair.

Automatic waiver is unavailable in this repair lifecycle.

After successful final delivery, archive the session target and mark task ownership delivered with:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\delivery_guard.py clear-target --session-id "<session_id>" --current-target ".codex\delivery-targets\sessions\<session_id>\current.json" --task-index ".codex\delivery-targets\task-index.json" --video-output-dir "<video-output-dir>"
```

The delivered session target is moved under `<video-output-dir>\待删除\delivery-targets\sessions\`; no permanent deletion is performed.

## Scripts

- `scripts/validate_delivery_glossary.py`: validates one standalone `delivery_glossary.v1` contract file for non-English teaching PDFs
- `scripts/render_pdf_pages.py`: renders every final PDF page to `review/acceptance/rendered_pages/`
- `scripts/video_workflow.py`: exposes `legacy-acceptance-adopt` and the active `acceptance-*` provider operations
- `scripts/delivery_guard.py`: consumes only current Acceptance Report v2 authority, runs `delivery_guard.py check`, implements the Stop-hook `hook-stop` decision, and archives active target state with `delivery_guard.py clear-target`

Use the project virtual environment:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B -m unittest discover .agents\skills\final-delivery-acceptance\scripts -p "test_*.py"
```

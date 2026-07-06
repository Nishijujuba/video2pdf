---
name: final-delivery-acceptance
description: Run the final, read-only delivery acceptance gate for rendered video-to-PDF outputs.
---

# Final Delivery Acceptance

Use this skill after the final PDF has been rendered and before delivery of a `/bilibili-render-pdf` or `/youtube-render-pdf` result.

The Acceptance Reviewer is an independent read-only reviewer. The reviewer evaluates the final delivered artifacts against `docs/acceptance/acceptance_criteria.v1.json`, using the allowed artifact boundary in `review/acceptance/allowed_artifacts_manifest.json`.

## Context Boundary

Allowed inputs:

- final delivered artifacts listed in `review/acceptance/allowed_artifacts_manifest.json`
- the criteria file `docs/acceptance/acceptance_criteria.v1.json`
- rendered page evidence under `review/acceptance/rendered_pages/`

Forbidden context:

- generation notes
- writer drafts
- chat history
- repair discussion
- `work/`
- `review/pyramid/`
- `review/consistency/`
- intermediate files outside the allowed manifest

The reviewer may write only:

- `review/acceptance/acceptance_report.json`
- `review/acceptance/acceptance_summary.md`

The reviewer must not modify final artifacts, TeX source, figures, tables, criteria files, generated page images, subtitles, source materials, or intermediate files.

## Required Workflow

1. Validate the criteria file with `scripts/validate_acceptance_criteria.py`.
2. Create or refresh `review/acceptance/allowed_artifacts_manifest.json` with `scripts/validate_acceptance_report.py manifest`.
3. Render the final PDF pages with `scripts/render_pdf_pages.py` so every page has a `review/acceptance/rendered_pages/page_0001.png` style image.
4. Launch the Acceptance Reviewer from a clean context containing only the allowed manifest, the criteria file, final delivered artifacts, and rendered pages.
5. The Acceptance Reviewer must evaluate every criterion and must record one result for every rendered PDF page.
6. Validate `review/acceptance/acceptance_report.json` with `scripts/validate_acceptance_report.py validate --enforce-decision`.

`acceptance_report.json is the only machine-readable delivery decision source`. An optional Markdown summary may explain the decision, and it cannot override the JSON result.

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
- keep `review_context_used.artifacts_read` inside the manifest final artifacts plus the criteria file
- bind the report to current artifact fingerprints

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

Every active delivery workflow is represented by `.codex/delivery-targets/current.json` and the video-level `review/acceptance/delivery_target.json`. The lifecycle stages are `generating`, `ready_for_delivery`, `accepted`, `delivered`, `blocked`.

The video-level target binds the final PDF, main TeX file, `review/acceptance/allowed_artifacts_manifest.json`, `review/acceptance/acceptance_report.json`, and `review/acceptance/delivery_guard_report.json`. Newly generated video PDFs must also have final compile provenance at `review\latex\compile_report.json`. Compile provenance binds current TeX/PDF fingerprints plus guarded wrapper producer, wrapper contract, wrapper mode, wrapper script fingerprint, and final-mode invocation arguments. It must record `attempt_limit: 3`.

`acceptance_report.json is the only machine-readable delivery decision source`. `delivery_guard_report.json is a mechanical proof of freshness and contract validity`. The guard proves freshness, manifest membership, rendered page coverage, path boundaries, compile provenance for newly generated video PDFs, and enforced Acceptance Report decision. It does not replace the Acceptance Reviewer.

The Acceptance Reviewer evaluates delivery quality from final delivered artifacts and rendered page evidence. `review\latex\compile_report.json` is compile provenance for `delivery_guard.py check`. A compile report cannot replace acceptance_report.json, cannot override `overall_status`, and cannot serve as Acceptance Reviewer quality judgment.

Before delivery, run:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\delivery_guard.py check
```

Do not deliver this PDF until delivery_guard.py records a fresh pass.

The project Stop hook calls `delivery_guard.py hook-stop`. It may run `delivery_guard.py check` once for `ready_for_delivery` or `accepted`. The Stop hook must not launch the Acceptance Reviewer, repair subagents, page rendering, or LaTeX compilation. UserPromptSubmit remains out of scope.

Blocking text must include: Final Delivery Guard blocked delivery. Use a separate Acceptance Reviewer subagent and repair subagents. Do not deliver this PDF until delivery_guard.py records a fresh pass.

## Old-PDF Repair Mode

Old-PDF repair requires an explicit video_output_dir unless the PDF is already inside one valid video output directory. The preparation command is:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\delivery_guard.py old-pdf-prepare "<pdf-path>" --video-output-dir "<video-output-dir>"
```

When the PDF is already inside a valid video output directory, `--video-output-dir` may be omitted. Isolated PDFs must not trigger broad workspace search.

Repair subagents may inspect and modify only files inside that video output directory. A failed attempt is archived with:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\delivery_guard.py record-failed-attempt --video-output-dir "<video-output-dir>" --attempt-number 1 --changed-file main.tex
```

Each failed attempt preserves `acceptance_report.json`, optional `acceptance_summary.md`, `repair_brief.md`, and `changed_files.json` under `review/acceptance/attempts/attempt_01/`, then `attempt_02/` and `attempt_03/` when needed. After the third failed attempt, write `review/acceptance/manual_repair_brief.md`, set the target stage to `blocked`, and stop automatic repair.

## Scripts

- `scripts/validate_acceptance_criteria.py`: validates `docs/acceptance/acceptance_criteria.v1.json`
- `scripts/render_pdf_pages.py`: renders every final PDF page to `review/acceptance/rendered_pages/`
- `scripts/validate_acceptance_report.py`: creates the allowed manifest, checks fingerprints, validates report shape, checks visual page coverage, and enforces the JSON delivery decision
- `scripts/delivery_guard.py`: prepares bounded old-PDF repair, records failed attempts, runs `delivery_guard.py check`, implements the Stop-hook `hook-stop` decision, and archives active target state with `delivery_guard.py clear-target`

Use the project virtual environment:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B -m unittest discover .agents\skills\final-delivery-acceptance\scripts -p "test_*.py"
```

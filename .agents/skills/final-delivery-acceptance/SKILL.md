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

## Report Duties

The Acceptance Reviewer must:

- evaluate every criterion from the criteria file, even after finding a failure
- run a full final text scan for style criteria
- inspect every rendered PDF page image for visual criteria
- write one `criterion_results[]` entry for every configured criterion
- write one `visual_scan_evidence.pages_checked[]` entry for every rendered PDF page
- include artifact-grounded evidence for each failed criterion
- include revision guidance for each failed criterion
- declare `generation_process_used: false`
- keep `review_context_used.artifacts_read` inside the manifest final artifacts plus the criteria file
- bind the report to current artifact fingerprints

## Failure And Repair Loop

A failed, missing, malformed, stale, or forbidden-context Acceptance Report blocks delivery.

When acceptance fails, the coordinator builds a repair brief from:

- `failed_criteria[]`
- failed `criterion_results[]`
- `visual_scan_evidence`
- each failed criterion's `revision_guidance`

Repair subagents may edit TeX, figures, tables, caveat placement, or other final artifacts needed to satisfy the failed criteria. The Acceptance Reviewer remains read-only.

After repair, the workflow must rerender affected final artifacts, refresh rendered page evidence, refresh any upstream evidence invalidated by the repair, and start a fresh Acceptance Reviewer run from final delivered artifacts plus criteria only. Old reports remain audit evidence and cannot approve changed artifacts.

## Scripts

- `scripts/validate_acceptance_criteria.py`: validates `docs/acceptance/acceptance_criteria.v1.json`
- `scripts/render_pdf_pages.py`: renders every final PDF page to `review/acceptance/rendered_pages/`
- `scripts/validate_acceptance_report.py`: creates the allowed manifest, checks fingerprints, validates report shape, checks visual page coverage, and enforces the JSON delivery decision

Use the project virtual environment:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B -m unittest discover .agents\skills\final-delivery-acceptance\scripts -p "test_*.py"
```

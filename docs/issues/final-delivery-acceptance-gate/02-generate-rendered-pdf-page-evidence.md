---
type: issue
status: ready-for-agent
feature: "[[prd/final-delivery-acceptance-gate]]"
depends_on:
  - "[[issues/final-delivery-acceptance-gate/01-validate-acceptance-criteria-and-report-contracts]]"
blocks:
  - "[[issues/final-delivery-acceptance-gate/03-codify-read-only-acceptance-reviewer-skill]]"
  - "[[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]]"
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-04
updated: 2026-07-04
tags:
  - issue
  - status/ready-for-agent
---

# 02 - Generate rendered PDF page evidence

Status: ready-for-agent

## Goal

Render every final PDF page into deterministic page images under the video output acceptance review directory and make page coverage mechanically verifiable.

## What to build

Add the rendered PDF page helper and connect its output shape to Acceptance Report validation. A completed slice should take a final PDF, write one image per page to `review/acceptance/rendered_pages/page_0001.png` and following paths, report the PDF page count, and let the report validator reject any visual scan evidence that skips, duplicates, or misnumbers pages.

This slice is about observable page evidence. It should not implement subjective visual judgment; that belongs to the Acceptance Reviewer skill contract.

## Context

This issue implements the "Rendered page evidence" and "Testing Decisions" sections of [[prd/final-delivery-acceptance-gate]]. It preserves the ADR requirement that visual acceptance inspects rendered PDF pages instead of TeX source alone.

Relevant domain concepts from root `CONTEXT.md`: Rendered PDF Visual Review, Full Rendered PDF Visual Scan, Visual Scan Evidence, Figure Visual Integrity Criterion, Table Layout Integrity Criterion, and Credibility Disclosure Placement Criterion.

## Dependencies

- Depends on: [[issues/final-delivery-acceptance-gate/01-validate-acceptance-criteria-and-report-contracts]]
- Blocks: [[issues/final-delivery-acceptance-gate/03-codify-read-only-acceptance-reviewer-skill]], [[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]]

## User Stories Covered

1, 22, 23, 24, 25, 26, 27, 28, 29, 30, 35, 37, 38

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/render_pdf_pages.py`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_report.py`
- `.agents/skills/final-delivery-acceptance/references/acceptance-report.schema.json`
- Generated evidence path: `<video-output-dir>/review/acceptance/rendered_pages/page_0001.png`
- Tests or fixtures for small PDFs with clean pages, clipped tables, and visually broken figures

## Acceptance Tests

- A small multi-page fixture PDF renders to exactly one zero-padded PNG per page.
- The renderer reports the same page count as the PDF reader.
- Existing rendered page files are refreshed deterministically when the final PDF changes.
- Report validation accepts `visual_scan_evidence` only when `pages_checked[]` covers every page from `1` through `page_count` exactly once.
- Report validation rejects missing pages, duplicate pages, page numbers outside range, missing rendered image paths, and page counts that disagree with the rendered PDF.
- A failed page entry must include the criterion id, category, visible defect description, rendered page image path, and PDF page number.

## Delivery Blocking Behavior

- Delivery must block when the final PDF cannot be rendered into page evidence.
- Delivery must block when rendered page coverage is incomplete, duplicated, stale, or inconsistent with the PDF page count.
- Delivery must block when visual criteria claim a pass without full rendered page evidence.

## Acceptance Criteria

- [ ] `render_pdf_pages.py` creates `review/acceptance/rendered_pages/` and writes all pages with the required `page_0001.png` naming pattern.
- [ ] The renderer exposes the final PDF page count for downstream validation.
- [ ] Report validation enforces complete one-entry-per-page visual scan coverage.
- [ ] Tests cover valid rendering and all visual coverage rejection cases listed above.
- [ ] Documentation states that rendered page evidence is required for figure, table, and credibility disclosure placement checks.

## Execution Log

- 2026-07-04: Created from [[prd/final-delivery-acceptance-gate]].

## Comments

---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/final-delivery-guard-and-bounded-repair/01-establish-delivery-target-contracts]]"
blocks:
  - "[[issues/final-delivery-guard-and-bounded-repair/05-integrate-guard-and-repair-into-render-skills]]"
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 04 - Add bounded old PDF repair mode

Status: done

## Goal

Extend `final-delivery-acceptance` with an old-PDF repair mode that confines repair work to one explicit video output directory and stops after three failed attempts.

## What to build

Add the old-PDF acceptance and repair workflow to the final-delivery acceptance skill. A completed slice should accept a PDF plus a required video output boundary, infer the boundary only when the PDF already sits inside one valid video output directory, reject isolated or ambiguous PDFs without an explicit `video_output_dir`, and scope repair subagents to files inside the bound directory.

Each failed attempt should preserve the Acceptance Report and summary under `review/acceptance/attempts/attempt_NN/`, create a repair brief from failed criteria and visual evidence, record changed files, refresh stale final and upstream evidence, and start the next Acceptance Reviewer run from final artifacts only. After the third failed attempt, the workflow should block delivery, set target state to `blocked`, and write `review/acceptance/manual_repair_brief.md`.

## Context

This issue implements the bounded repair loop from [[prd/final-delivery-guard-and-bounded-repair]]. It tightens the earlier repair-rerun workflow in [[issues/final-delivery-acceptance-gate/05-define-acceptance-repair-rerun-loop]] by adding directory boundaries, attempt numbering, and a hard attempt limit.

## Dependencies

- Depends on: [[issues/final-delivery-guard-and-bounded-repair/01-establish-delivery-target-contracts]]
- Blocks: [[issues/final-delivery-guard-and-bounded-repair/05-integrate-guard-and-repair-into-render-skills]]

## User Stories Covered

1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 34, 35, 36, 37

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_report.py`
- `.agents/skills/final-delivery-acceptance/scripts/render_pdf_pages.py`
- `AGENTS.md`
- `CLAUDE.md`
- Repair-loop tests or fixtures under the established local test location

## Acceptance Tests

- A PDF inside one valid video output directory can infer `video_output_dir`.
- An isolated PDF requires an explicit `video_output_dir` and does not trigger broad workspace search.
- A PDF path or video output path that escapes the current project is rejected.
- Repair subagent instructions allow reads and writes only inside the bound video output directory.
- A failed Acceptance Report produces `attempt_NN/acceptance_report.json`, `attempt_NN/acceptance_summary.md`, `attempt_NN/repair_brief.md`, and `attempt_NN/changed_files.json`.
- A repair brief includes failed criteria, criterion results, visual scan evidence, rendered page evidence, page numbers, and reviewer revision guidance.
- Any repaired final artifact invalidates the previous report and requires rerendering plus a fresh Acceptance Reviewer run.
- After three failed attempts, the workflow writes `manual_repair_brief.md`, sets the active target to `blocked`, and stops automatic repair.

## Delivery Blocking Behavior

- Ambiguous old-PDF repair input must block and request an explicit video output directory.
- Delivery must stay blocked until a fresh independent Acceptance Reviewer run passes and the guard records a fresh pass.
- Three failed attempts must produce a manual repair brief and block delivery.
- Automatic waiver remains outside the workflow.

## Acceptance Criteria

- [x] Old-PDF repair mode requires or safely infers one video output directory.
- [x] Repair subagent scope is mechanically bounded to the selected video output directory.
- [x] Attempt evidence is preserved under numbered attempt directories.
- [x] Repair briefs are generated from failed Acceptance Report evidence and rendered-page evidence.
- [x] The repair loop refreshes stale evidence before every reviewer rerun.
- [x] The workflow blocks after three failed attempts and writes `manual_repair_brief.md`.
- [x] Tests cover inside-directory inference, isolated PDF rejection, path escape rejection, attempt evidence, and three-attempt failure.

## Execution Log

- 2026-07-05: Created from [[prd/final-delivery-guard-and-bounded-repair]].
- 2026-07-05: Added `old-pdf-prepare` and `record-failed-attempt`; legacy PDF smoke prepared the real old PDF, refreshed 32 rendered pages, archived attempt_01, and confirmed guard blocking on the failed Acceptance Report.

## Comments

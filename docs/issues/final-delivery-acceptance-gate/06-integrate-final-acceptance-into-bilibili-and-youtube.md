---
type: issue
status: ready-for-agent
feature: "[[prd/final-delivery-acceptance-gate]]"
depends_on:
  - "[[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]]"
  - "[[issues/final-delivery-acceptance-gate/05-define-acceptance-repair-rerun-loop]]"
blocks: []
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-04
updated: 2026-07-04
tags:
  - issue
  - status/ready-for-agent
---

# 06 - Integrate final acceptance into Bilibili and YouTube

Status: ready-for-agent

## Goal

Insert the Final Delivery Acceptance Gate into both single-video render workflows after final PDF rendering and before delivery, with the Final Checklist requiring a fresh passing Acceptance Report.

## What to build

Update the Bilibili and YouTube render skills so every completed video PDF run creates acceptance evidence under `<video-output-dir>/review/acceptance/`, renders page evidence, launches an independent Acceptance Reviewer, validates the report, blocks delivery on failure, and enters the repair rerun loop when needed.

The completed slice should make both workflows use the same default criteria file, output layout, context isolation contract, and delivery decision source.

## Context

This issue implements the workflow integration decisions from [[prd/final-delivery-acceptance-gate]]. It follows [[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]] and must keep the Final Delivery Acceptance Gate separate from Pyramid Gate and independent content review.

Relevant domain concepts from root `CONTEXT.md`: Acceptance Evidence, Acceptance Report, Acceptance Decision Source, Full Rendered PDF Visual Scan, Pyramid Gate, and Video Output Directory.

## Dependencies

- Depends on: [[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]], [[issues/final-delivery-acceptance-gate/05-define-acceptance-repair-rerun-loop]]
- Blocks: none

## User Stories Covered

18, 19, 46, 47, 48, 49, 50

## Expected Touched Paths

- `.agents/skills/bilibili-render-pdf/SKILL.md`
- `.agents/skills/youtube-render-pdf/SKILL.md`
- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `AGENTS.md`
- Generated workflow output paths under `<video-output-dir>/review/acceptance/`
- Skill validation or workflow tests that prove post-render pre-delivery placement

## Acceptance Tests

- Bilibili skill documentation requires final acceptance after PDF rendering and before delivery.
- YouTube skill documentation requires final acceptance after PDF rendering and before delivery.
- Each Final Checklist requires a fresh passing `review/acceptance/acceptance_report.json`.
- Each workflow uses `docs/acceptance/acceptance_criteria.v1.json` as the default criteria file.
- Each workflow creates or refreshes `allowed_artifacts_manifest.json`, rendered page evidence, `acceptance_report.json`, and optional `acceptance_summary.md` under `review/acceptance/`.
- A missing, failed, malformed, stale, or forbidden-context report blocks final delivery in both workflows.
- A failed report routes to repair subagents, rerendering, stale evidence refresh, and a new clean Acceptance Reviewer run.
- Existing Pyramid Gate and independent content review remain separate checks and do not imply acceptance pass.

## Delivery Blocking Behavior

- Bilibili delivery must block without a fresh passing Acceptance Report after render.
- YouTube delivery must block without a fresh passing Acceptance Report after render.
- A Pyramid pass, LaTeX compile success, layout blank-space pass, or independent content review pass must not bypass final acceptance.

## Acceptance Criteria

- [ ] `.agents/skills/bilibili-render-pdf/SKILL.md` adds the Final Delivery Acceptance Gate to the post-render delivery flow and Final Checklist.
- [ ] `.agents/skills/youtube-render-pdf/SKILL.md` adds the same gate to the post-render delivery flow and Final Checklist.
- [ ] Both workflows document the required acceptance evidence paths under `review/acceptance/`.
- [ ] Both workflows state that `acceptance_report.json` is the only machine-readable acceptance decision.
- [ ] Skill validation or tests prove the gate is documented after render and before delivery.
- [ ] The integration preserves separation from Pyramid Gate and independent subtitle/content review.

## Execution Log

- 2026-07-04: Created from [[prd/final-delivery-acceptance-gate]].

## Comments

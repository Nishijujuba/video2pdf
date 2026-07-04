---
type: issue
status: ready-for-agent
feature: "[[prd/final-delivery-acceptance-gate]]"
depends_on:
  - "[[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]]"
blocks:
  - "[[issues/final-delivery-acceptance-gate/06-integrate-final-acceptance-into-bilibili-and-youtube]]"
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-04
updated: 2026-07-04
tags:
  - issue
  - status/ready-for-agent
---

# 05 - Define acceptance repair rerun loop

Status: ready-for-agent

## Goal

Codify the recovery path after a failed Acceptance Report: repair subagents revise artifacts, affected outputs are regenerated, stale evidence is refreshed, and a new clean Acceptance Reviewer run decides delivery.

## What to build

Add the workflow contract for failed acceptance handling. A completed slice should make failures operationally useful by turning `failed_criteria[]`, page evidence, and revision guidance into the repair brief while keeping the Acceptance Reviewer read-only.

The old failed report remains evidence, but it must not approve revised artifacts. Any in-scope final artifact change must invalidate the old report and require a fresh reviewer run from a final-artifacts-only context.

## Context

This issue implements the repair and rerun decisions from [[prd/final-delivery-acceptance-gate]], especially the rule that repair and acceptance are separate roles. It follows [[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]], which requires every failed criterion to include artifact-grounded evidence and revision guidance.

Relevant domain concepts from root `CONTEXT.md`: Acceptance Revision Guidance, Acceptance Report Freshness, Complete Acceptance Evaluation, Acceptance Reviewer, and Acceptance Evidence.

## Dependencies

- Depends on: [[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]]
- Blocks: [[issues/final-delivery-acceptance-gate/06-integrate-final-acceptance-into-bilibili-and-youtube]]

## User Stories Covered

10, 11, 12, 13, 14, 41, 42, 44, 45

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `AGENTS.md`
- `.agents/skills/bilibili-render-pdf/SKILL.md`
- `.agents/skills/youtube-render-pdf/SKILL.md`
- Tests or fixture workflow documentation for failed report, repair, rerender, stale-report rejection, and fresh pass

## Acceptance Tests

- A failed Acceptance Report blocks delivery and produces a repair brief from `failed_criteria[]`, `criterion_results[]`, `visual_scan_evidence`, and revision guidance.
- The Acceptance Reviewer instructions remain read-only and do not authorize direct artifact edits.
- Repair subagents may modify final artifacts, TeX, figures, tables, or caveat placement as needed to satisfy failed criteria.
- After repair changes an in-scope artifact, the previous Acceptance Report is rejected as stale.
- The workflow rerenders or regenerates affected final artifacts before a new acceptance run.
- A new Acceptance Reviewer run starts from a clean final-artifacts-and-criteria-only context.
- Upstream evidence refresh requirements are stated when repairs change artifacts covered by other gates.

## Delivery Blocking Behavior

- Delivery must stay blocked after a failed report until repair completes and a fresh passing report validates.
- Delivery must stay blocked when a repaired artifact is paired with an old passing report.
- Delivery must stay blocked when the repair loop skips rerendering or skips a fresh reviewer run.

## Acceptance Criteria

- [ ] The final-delivery acceptance skill documents the repair brief shape and rerun sequence.
- [ ] Project instructions separate Acceptance Reviewer judgment from repair subagent mutation.
- [ ] Workflow documentation states that old reports remain evidence but cannot approve changed artifacts.
- [ ] Tests or validation fixtures demonstrate failed report, repair, stale old report, fresh reviewer rerun, and final pass.
- [ ] The repair loop states how affected upstream evidence is refreshed when repairs invalidate it.

## Execution Log

- 2026-07-04: Created from [[prd/final-delivery-acceptance-gate]].

## Comments

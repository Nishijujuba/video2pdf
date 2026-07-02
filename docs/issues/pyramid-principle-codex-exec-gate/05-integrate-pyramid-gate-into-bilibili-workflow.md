---
type: issue
status: done
feature: "[[prd/pyramid-principle-codex-exec-gate]]"
depends_on:
  - "[[issues/pyramid-principle-codex-exec-gate/02-build-codex-exec-evaluator-wrapper]]"
  - "[[issues/pyramid-principle-codex-exec-gate/03-enforce-gate-outcomes-and-waivers]]"
  - "[[issues/pyramid-principle-codex-exec-gate/04-maintain-pyramid-review-directory-evidence]]"
blocks: []
related_adrs:
  - "[[adr/0001-use-codex-exec-for-pyramid-semantic-evaluation]]"
owner: unassigned
created: 2026-06-30
updated: 2026-06-30
tags:
  - issue
  - status/done
---

# 05 - Integrate Pyramid Gate into Bilibili workflow

Status: done

## Goal

Update the Bilibili single-video render workflow so it runs the general Pyramid Gate at the outline, section, and main checkpoints and records review evidence under the video output directory.

## Context

This issue applies [[prd/pyramid-principle-codex-exec-gate]] to the Bilibili render path after the general evaluator and evidence contract exist. Batch orchestration remains deferred by the PRD.

The workflow must pass Teaching-PDF context into the general Pyramid Principle Text Standard evaluator.

## Dependencies

- Depends on: [[issues/pyramid-principle-codex-exec-gate/02-build-codex-exec-evaluator-wrapper]], [[issues/pyramid-principle-codex-exec-gate/03-enforce-gate-outcomes-and-waivers]], [[issues/pyramid-principle-codex-exec-gate/04-maintain-pyramid-review-directory-evidence]]
- Blocks: none

## User Stories Covered

3, 17, 18, 19, 23, 24, 28, 30

## Acceptance Criteria

- [x] The Bilibili render workflow runs the evaluator after the outline contract exists and before writer agents start.
- [x] The workflow runs the evaluator after each section draft exists and before integration.
- [x] The workflow runs the evaluator after integrated main document creation and before PDF compilation.
- [x] Each checkpoint passes `artifact_type`, `context_label`, and Teaching-PDF evaluation context to the general evaluator.
- [x] Each checkpoint writes reports under the Pyramid Review Directory and updates the human summary.
- [x] A failing outline gate stops writer work, a failing section gate stops integration, and a failing main gate stops PDF compilation unless explicit waiver evidence is recorded.
- [x] The skill instructions show exact expected gate calls and the final output-level gate check.
- [x] Verification reads the updated workflow instructions and confirms all three checkpoints, stop rules, waiver handling, and summary evidence are present.

## Execution Log

- 2026-06-30: Created from [[prd/pyramid-principle-codex-exec-gate]].
- 2026-06-30: Integrated the Bilibili single-video workflow with Pyramid Gate calls for outline, section, main, waiver, and output-level checks.
- 2026-06-30: Verified the updated workflow text contains all three checkpoints, stop rules, waiver handling, summary evidence, and the final output-level check; `quick_validate.py` reported `Skill is valid!`.

## Comments

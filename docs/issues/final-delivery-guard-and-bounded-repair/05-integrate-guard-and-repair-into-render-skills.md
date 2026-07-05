---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/final-delivery-guard-and-bounded-repair/02-implement-delivery-guard-cli]]"
  - "[[issues/final-delivery-guard-and-bounded-repair/04-add-bounded-old-pdf-repair-mode]]"
blocks:
  - "[[issues/final-delivery-guard-and-bounded-repair/06-add-end-to-end-guard-fixture-tests-and-doc-sync]]"
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 05 - Integrate guard and repair into render skills

Status: done

## Goal

Make `/bilibili-render-pdf` and `/youtube-render-pdf` the enforced delivery paths that set an active target, run acceptance, run bounded repair when needed, require a guard pass, and clear the target after delivery.

## What to build

Update the Bilibili and YouTube render skill workflows so each generated PDF has an explicit delivery target and a guarded final response. A completed slice should set `.codex/delivery-targets/current.json` while generation is active, create the video-output-level `delivery_target.json`, run Final Delivery Acceptance through a separate Acceptance Reviewer, call bounded repair after failed acceptance, rerender or regenerate affected artifacts, refresh page evidence, run `delivery_guard.py check`, and clear project-level target state after successful delivery.

The render skills should remain the normal entry points. Old-PDF repair remains owned by `final-delivery-acceptance`.

## Context

This issue implements the workflow integration in [[prd/final-delivery-guard-and-bounded-repair]]. It builds on the guard CLI from [[issues/final-delivery-guard-and-bounded-repair/02-implement-delivery-guard-cli]] and bounded repair mode from [[issues/final-delivery-guard-and-bounded-repair/04-add-bounded-old-pdf-repair-mode]].

## Dependencies

- Depends on: [[issues/final-delivery-guard-and-bounded-repair/02-implement-delivery-guard-cli]], [[issues/final-delivery-guard-and-bounded-repair/04-add-bounded-old-pdf-repair-mode]]
- Blocks: [[issues/final-delivery-guard-and-bounded-repair/06-add-end-to-end-guard-fixture-tests-and-doc-sync]]

## User Stories Covered

7, 8, 9, 10, 11, 17, 18, 28, 35, 40, 41, 45

## Expected Touched Paths

- `.agents/skills/bilibili-render-pdf/SKILL.md`
- `.agents/skills/youtube-render-pdf/SKILL.md`
- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `AGENTS.md`
- `CLAUDE.md`
- Workflow tests or fixture scripts that avoid real Bilibili and YouTube downloads

## Acceptance Tests

- A fixture Bilibili workflow sets `current.json` to `generating`, then `ready_for_delivery`, then `accepted`, then clears it after delivery.
- A fixture YouTube workflow follows the same target lifecycle.
- A failed Acceptance Report triggers bounded repair through a separate repair role before final delivery.
- Repaired artifacts are rerendered or regenerated before the next Acceptance Reviewer run.
- Rendered page evidence is refreshed after repair.
- `delivery_guard.py check` must pass before a render skill may deliver the PDF in the final response.
- A failed repair loop leaves target state as `blocked` and points to `manual_repair_brief.md`.
- Tests use fixture PDFs and fixture Acceptance Reports without real downloads or model calls.

## Delivery Blocking Behavior

- Render skills must block final delivery when acceptance fails, repair exhausts attempts, or the guard fails.
- Render skills must block final delivery when the active target points outside the current project.
- Render skills must clear delivered state after a successful final response so future tasks are not blocked by stale project state.

## Acceptance Criteria

- [x] Bilibili and YouTube render skills create and update project-level and video-output-level delivery targets.
- [x] Both render skills require separate Acceptance Reviewer execution before delivery.
- [x] Both render skills run bounded repair after failed acceptance and preserve attempt evidence.
- [x] Both render skills require a fresh passing guard report before final delivery.
- [x] Both render skills clear project-level target state after successful delivery.
- [x] `AGENTS.md` and `CLAUDE.md` document the same enforced final-delivery workflow.
- [x] Fixture workflow tests cover pass, failed-then-repaired, and failed-after-three-attempt states.

## Execution Log

- 2026-07-05: Created from [[prd/final-delivery-guard-and-bounded-repair]].
- 2026-07-05: Updated Bilibili/YouTube render skill contracts, added `clear-target`, and verified lifecycle behavior with fixture tests.

## Comments

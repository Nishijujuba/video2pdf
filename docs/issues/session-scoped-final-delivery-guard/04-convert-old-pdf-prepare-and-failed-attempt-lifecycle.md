---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]]"
  - "[[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]]"
blocks:
  - "[[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]]"
  - "[[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]]"
  - "[[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]]"
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
  - "[[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]]"
owner: unassigned
created: 2026-07-06
updated: 2026-07-07
tags:
  - issue
  - status/done
---

# 04 - Convert old-PDF prepare and failed-attempt lifecycle

Status: done

## Goal

Move old-PDF preparation and failed-attempt recording onto the session-scoped target and task-index lifecycle.

## What to build

Update bounded old-PDF repair setup so it writes the session-scoped active target, the video-output delivery target, and the task index in one coherent lifecycle. A completed slice should preserve explicit `video_output_dir` boundaries, infer the video output directory only when the PDF already lives inside a valid output directory, and record failed acceptance attempts under numbered attempt folders.

Failed attempt recording should update both the session target and task index when the repair loop reaches blocked state. After the third failed attempt, it should write `manual_repair_brief.md` and keep delivery blocked until a fresh repair plus acceptance plus guard pass succeeds.

## Context

This issue depends on [[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]] and [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]] because old-PDF prepare must write valid guard inputs and valid task ownership state.

The repair loop remains bounded to the video output directory. The Acceptance Reviewer remains read-only and independent.

## Dependencies

- Depends on: [[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]], [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]]
- Blocks: [[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]], [[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]], [[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]]

## User Stories Covered

1, 2, 3, 4, 5, 6, 12, 13, 14, 15, 27, 34, 36, 37

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/test_delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/SKILL.md`

## Acceptance Tests

- `old-pdf-prepare` writes a session-scoped active target for the provided session.
- `old-pdf-prepare` writes a video-level `delivery_target.json`.
- `old-pdf-prepare` creates or updates the task-index owner for the video output directory.
- A PDF already inside one valid video output directory can infer that directory.
- An isolated PDF requires explicit `video_output_dir`.
- Ambiguous or escaping `video_output_dir` values block before mutating target state.
- `record-failed-attempt` preserves failed reports, summaries, repair briefs, and changed-file evidence under `attempt_NN`.
- The latest `acceptance_report.json` remains at the fixed acceptance root.
- The third failed attempt writes `manual_repair_brief.md` and moves session target plus task-index state to `blocked`.
- Automatic waiver remains unavailable.

## Acceptance Criteria

- [x] Old-PDF repair setup writes session target, video target, and task-index state consistently.
- [x] Repair scope stays mechanically bound to the selected video output directory.
- [x] Failed attempt evidence is preserved under numbered attempt folders.
- [x] The third failed attempt creates a manual repair brief and blocks delivery.
- [x] Tests cover inferred directory, explicit directory, ambiguous directory, path escape, failed attempts, and blocked lifecycle state.

## Execution Log

- 2026-07-06: Created from [[prd/final-delivery-guard-and-bounded-repair]] and [[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]].
- 2026-07-07: Converted old-PDF prepare and failed-attempt recording to explicit session-scoped target and task-index lifecycle. Added tests for inferred and explicit video directories, mutation-free blocking on ambiguous or escaping scope, failed-attempt preservation, third-attempt blocked state, and unavailable automatic waiver; independent verification passed.

## Comments

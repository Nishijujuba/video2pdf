---
type: issue
status: ready-for-agent
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]]"
  - "[[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]]"
blocks:
  - "[[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]]"
  - "[[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]]"
related_adrs:
  - "[[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]]"
owner: unassigned
created: 2026-07-06
updated: 2026-07-06
tags:
  - issue
  - status/ready-for-agent
---

# 05 - Archive delivered session targets and update task index

Status: ready-for-agent

## Goal

Archive delivered session targets under the video output directory and mark the task index as delivered after successful final delivery.

## What to build

Update the delivered lifecycle so a successful final delivery preserves the session target in the video output directory's `待删除` area and leaves no active session target for later Stop-hook runs. A completed slice should mark the task-index task as `delivered`, record the delivered session id and timestamp, and preserve enough target metadata for audit.

This slice must use move-to-`待删除` behavior for cleanup, preserving the repository's no-permanent-deletion policy.

## Context

This issue depends on [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]] and [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]] because delivery archival requires validated ownership state and a working session target lifecycle.

The PRD requires delivered session targets to be archived after successful final delivery so stale delivered state does not affect later work.

## Dependencies

- Depends on: [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]], [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]]
- Blocks: [[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]], [[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]]

## User Stories Covered

28, 39

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/test_delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/SKILL.md`

## Acceptance Tests

- A delivered session target is moved to the video output directory under `待删除/delivery-targets/sessions/`.
- The archived filename contains the session id or otherwise remains traceable to the session target owner.
- The active session target path no longer exists after archive succeeds.
- `task-index.json` marks the task as `delivered`.
- A missing active session target is handled idempotently.
- Archive refuses path escapes from the project and video output directory.
- Existing archived targets are preserved.

## Acceptance Criteria

- [ ] Delivered session targets are archived under the owning video output directory's `待删除` folder.
- [ ] Permanent deletion is avoided.
- [ ] The task index records delivered state after final delivery.
- [ ] Archive behavior is idempotent for already-cleared sessions.
- [ ] Tests cover archive, missing target, existing archive, task-index update, and path-boundary cases.

## Execution Log

- 2026-07-06: Created from [[prd/final-delivery-guard-and-bounded-repair]] and [[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]].

## Comments

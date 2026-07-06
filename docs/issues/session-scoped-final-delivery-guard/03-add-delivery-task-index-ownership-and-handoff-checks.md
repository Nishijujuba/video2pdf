---
type: issue
status: ready-for-agent
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]]"
blocks:
  - "[[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]]"
  - "[[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]]"
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

# 03 - Add delivery task index ownership and handoff checks

Status: ready-for-agent

## Goal

Add project-level task ownership checks so two active sessions cannot advance the same video output directory without an explicit handoff.

## What to build

Implement the delivery task index as a recovery and observability surface for active delivery workflows. A completed slice should let workflow entry points claim ownership for one video output directory, reject conflicting active ownership, and record explicit handoff from one session to another.

This slice must expose a testable helper or CLI path used by old-PDF prepare and render workflow integration. The Stop hook must not read the task index as a blocking source.

## Context

This issue depends on [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]] because ownership uses the same documented session identity as the session-scoped target.

The task index is defined by [[prd/final-delivery-guard-and-bounded-repair]] as `.codex/delivery-targets/task-index.json`. [[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]] states that the index supports recovery, ownership checks, and observability.

## Dependencies

- Depends on: [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]]
- Blocks: [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]], [[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]], [[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]], [[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]]

## User Stories Covered

24, 28, 38, 39

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/test_delivery_guard.py`
- `.codex/delivery-targets/task-index.json` fixtures or generated test data

## Acceptance Tests

- A new session can claim an unowned video output directory.
- The same owner session can resume its own active video output directory.
- A different active session cannot claim the same video output directory.
- A different session can take over only through explicit handoff metadata.
- Handoff records `continued_from_session_id` and marks the previous owner as `superseded` or `abandoned`.
- An active task can move to `blocked`, `delivered`, `superseded`, or `abandoned` in the task index.
- Task-index path entries are confined to the project boundary.
- Stop-hook tests prove `task-index.json` does not affect hook blocking decisions.

## Acceptance Criteria

- [ ] `task-index.json` has a validated schema and test fixtures.
- [ ] Ownership claim, resume, conflict, and handoff behavior are reachable through a testable command or shared helper.
- [ ] Ownership checks prevent concurrent sessions from advancing the same video output directory.
- [ ] Stop hook behavior remains based only on the current session target.
- [ ] Tests cover active ownership, handoff, superseded, abandoned, blocked, delivered, and path-boundary cases.

## Execution Log

- 2026-07-06: Created from [[prd/final-delivery-guard-and-bounded-repair]] and [[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]].

## Comments

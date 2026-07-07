---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on: []
blocks:
  - "[[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]]"
  - "[[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]]"
  - "[[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]]"
related_adrs:
  - "[[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]]"
owner: unassigned
created: 2026-07-06
updated: 2026-07-07
tags:
  - issue
  - status/done
---

# 01 - Resolve hook session targets from official hook input

Status: done

## Goal

Make the Stop hook resolve the active delivery target from the official hook `session_id`, so one Codex session guards only its own PDF delivery workflow.

## What to build

Update the Stop-hook delivery decision so it reads the hook JSON object from standard input, requires `session_id`, and resolves the active target at the session-scoped location. A completed slice should let fixture hook input drive pass, block, and dispatch behavior without relying on a project-level singleton target.

This slice establishes the active-target identity model used by later guard, ownership, repair, and documentation work. It should remove project-level `current.json` fallback behavior from hook execution and treat `CODEX_THREAD_ID` only as optional diagnostic metadata when present.

## Context

This issue implements the session-scoped target decision from [[prd/final-delivery-guard-and-bounded-repair]] and [[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]].

The Stop hook must read the official hook input field `session_id`, resolve `.codex/delivery-targets/sessions/{session_id}/current.json`, and avoid scanning `task-index.json` as a blocking source.

## Dependencies

- Depends on: none
- Blocks: [[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]], [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]], [[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]]

## User Stories Covered

19, 21, 22, 23, 24, 25, 26, 27, 38, 39, 40, 44

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/test_delivery_guard.py`
- `.codex/hooks.json` when the command invocation needs stdin-compatible wiring

## Acceptance Tests

- Hook fixture input with `session_id` and no matching session target allows ordinary discussion.
- Hook fixture input without `session_id` blocks with the required Final Delivery Guard blocking text.
- Hook fixture input with `session_id` resolves `.codex/delivery-targets/sessions/{session_id}/current.json`.
- A stale project-level `.codex/delivery-targets/current.json` is ignored by `hook-stop`.
- A `generating` session target allows the response.
- A `blocked` session target blocks and points to repair evidence.
- A `delivered` session target allows the response and reports that stale session state should be archived by the workflow.
- A `ready_for_delivery` or `accepted` session target dispatches the same guard check using the resolved session target path.
- The Stop hook never scans `task-index.json` to decide whether to block.

## Acceptance Criteria

- [x] `hook-stop` consumes hook JSON from standard input and validates `session_id`.
- [x] Missing `session_id` blocks delivery safely.
- [x] No session target allows unrelated work for the current session.
- [x] The legacy singleton `current.json` path is unavailable as a hook fallback.
- [x] Tests cover no-target, missing-session, generating, ready-for-delivery, accepted, blocked, delivered, and stale singleton cases.

## Execution Log

- 2026-07-06: Created from [[prd/final-delivery-guard-and-bounded-repair]] and [[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]].
- 2026-07-07: Implemented `hook-stop` session target resolution from hook stdin, added fixture coverage for missing/no-target/stage/singleton/task-index cases, and verified with a separate subagent plus final-delivery-acceptance unittest discovery.

## Comments

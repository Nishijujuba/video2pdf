---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/final-delivery-guard-and-bounded-repair/02-implement-delivery-guard-cli]]"
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

# 03 - Enforce delivery guard with Stop hook

Status: done

## Goal

Add a project-local Stop hook that blocks final delivery when the active target lacks a fresh passing `delivery_guard_report.json`.

## What to build

Wire `.codex` hook configuration to the guard CLI from [[issues/final-delivery-guard-and-bounded-repair/02-implement-delivery-guard-cli]]. A completed slice should let ordinary discussion pass when no target exists, allow intermediate work while a target is `generating`, enforce guard freshness for `ready_for_delivery` and `accepted`, block `blocked`, and tolerate stale `delivered` state while reporting that the render workflow should clear it.

The hook must stay short and mechanical. It may run `delivery_guard.py check` once. It must avoid launching the Acceptance Reviewer, repair subagents, LaTeX compilation, page rendering, or the three-attempt repair loop.

## Context

This issue implements the final safety guard in [[prd/final-delivery-guard-and-bounded-repair]]. The guard proof remains the machine check. The Acceptance Reviewer remains the quality judge defined by [[prd/final-delivery-acceptance-gate]].

## Dependencies

- Depends on: [[issues/final-delivery-guard-and-bounded-repair/02-implement-delivery-guard-cli]]
- Blocks: [[issues/final-delivery-guard-and-bounded-repair/06-add-end-to-end-guard-fixture-tests-and-doc-sync]]

## User Stories Covered

21, 22, 23, 24, 25, 26, 27, 28, 40, 44

## Expected Touched Paths

- `.codex/` project hook configuration
- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `AGENTS.md`
- `CLAUDE.md`
- Hook tests or fixtures for active-target states

## Acceptance Tests

- The Stop hook allows the response when `.codex/delivery-targets/current.json` is absent.
- The Stop hook allows the response for `stage: "generating"`.
- The Stop hook allows the response for `stage: "accepted"` when the guard report is fresh and passing.
- The Stop hook runs `delivery_guard.py check` once when `ready_for_delivery` or `accepted` lacks a fresh pass.
- The Stop hook blocks when the guard check fails.
- The Stop hook blocks for `stage: "blocked"` and points to failed attempt evidence or `manual_repair_brief.md`.
- The Stop hook allows a stale `delivered` target while surfacing that workflow state should be cleared.
- Hook tests verify that no reviewer, repair, render, or LaTeX action is launched by the hook.

## Delivery Blocking Behavior

- A `ready_for_delivery` or `accepted` target without a fresh guard pass must block final delivery.
- A `blocked` target must block final delivery.
- Every blocking message must instruct the next agent to use a separate Acceptance Reviewer subagent and repair subagents before delivery.

## Acceptance Criteria

- [x] `.codex` Stop hook configuration invokes the guard path for active delivery targets.
- [x] Hook behavior matches every lifecycle stage in the PRD.
- [x] Hook failure text includes the required subagent-based recovery instruction.
- [x] The hook stays mechanical and avoids long-running acceptance or repair work.
- [x] Hook tests cover no-target, generating, ready, accepted, blocked, delivered, stale-report, and failed-guard states.
- [x] `AGENTS.md` and `CLAUDE.md` describe the Stop hook contract consistently.

## Execution Log

- 2026-07-05: Created from [[prd/final-delivery-guard-and-bounded-repair]].
- 2026-07-05: Added `.codex/hooks.json` Stop hook, `delivery_guard.py hook-stop`, and synchronized AGENTS/CLAUDE documentation; verified by contract tests.

## Comments

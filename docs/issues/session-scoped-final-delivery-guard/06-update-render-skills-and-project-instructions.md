---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]]"
  - "[[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]]"
  - "[[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]]"
  - "[[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]]"
blocks:
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

# 06 - Update render skills and project instructions

Status: done

## Goal

Synchronize the render skills and project instructions with the session-scoped final delivery guard lifecycle.

## What to build

Update the final-delivery acceptance, Bilibili render, YouTube render, and project instruction documents so future agents use the session-scoped target lifecycle consistently. A completed slice should document session target creation, task-index ownership, explicit handoff, bounded repair, delivered target archival, Stop-hook behavior, and the requirement to run Final Delivery Acceptance through separate subagents before delivery.

The docs should describe the supported guard command invocation for render workflows and preserve the Acceptance Reviewer boundary.

## Context

This issue depends on the working target, guard, ownership, old-PDF repair, and delivered archive slices. Documentation should align with implemented behavior instead of inventing future command contracts.

The project keeps both Codex and Claude Code instructions, so `AGENTS.md` and `CLAUDE.md` must stay synchronized where final delivery rules matter.

## Dependencies

- Depends on: [[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]], [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]], [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]], [[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]]
- Blocks: [[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]]

## User Stories Covered

17, 18, 40, 41, 42

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/bilibili-render-pdf/SKILL.md`
- `.agents/skills/youtube-render-pdf/SKILL.md`
- `AGENTS.md`
- `CLAUDE.md`
- `.agents/skills/final-delivery-acceptance/scripts/test_skill_contracts.py`

## Acceptance Tests

- Skill contract tests find session-scoped target creation rules in final-delivery acceptance documentation.
- Skill contract tests find session-scoped lifecycle rules in Bilibili render documentation.
- Skill contract tests find session-scoped lifecycle rules in YouTube render documentation.
- `AGENTS.md` and `CLAUDE.md` both describe session target ownership, task index handoff, delivered archive behavior, and Stop-hook limits.
- Documentation states that the Stop hook reads the current session target and does not scan all active tasks.
- Documentation keeps Acceptance Reviewer and repair subagents as separate roles.
- Documentation preserves the required blocking text.

## Acceptance Criteria

- [x] Final-delivery acceptance documentation matches the implemented session-scoped target commands.
- [x] Bilibili and YouTube render skills describe the full session lifecycle through delivered archive.
- [x] `AGENTS.md` and `CLAUDE.md` are synchronized for final delivery guard rules.
- [x] Contract tests cover the updated docs and required workflow ordering.
- [x] The docs preserve project-local scope and deferred plugin packaging.

## Execution Log

- 2026-07-06: Created from [[prd/final-delivery-guard-and-bounded-repair]] and [[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]].
- 2026-07-07: Updated final-delivery acceptance, Bilibili, YouTube, AGENTS, and CLAUDE documentation for the session-scoped guard lifecycle and strengthened skill contract tests.
- 2026-07-07: Independent verification subagent Carson found a blocking incomplete `task-handoff` command; the command and contract test were corrected.
- 2026-07-07: Independent verification subagent Franklin passed 87 unittest cases, dependency-view validation, and `git diff --check`; issue 06 approved for commit.

## Comments

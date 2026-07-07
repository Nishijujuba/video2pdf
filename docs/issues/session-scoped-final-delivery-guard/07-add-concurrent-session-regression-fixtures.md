---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]]"
  - "[[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]]"
  - "[[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]]"
  - "[[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]]"
  - "[[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]]"
  - "[[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]]"
blocks: []
related_adrs:
  - "[[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]]"
owner: unassigned
created: 2026-07-06
updated: 2026-07-07
tags:
  - issue
  - status/done
---

# 07 - Add concurrent-session regression fixtures

Status: done

## Goal

Prove the session-scoped delivery guard handles concurrent Codex sessions without cross-session blocking or approval leakage.

## What to build

Add regression fixtures that exercise multiple session targets in the same project. A completed slice should prove that two sessions can guard different PDFs, one blocked session does not affect another session, stale singleton state is ignored, and diagnostic entry points cannot become final delivery decision sources.

This slice is the final acceptance net for the session-scoped migration. It should verify behavior through observable fixture artifacts and generated issue dependency views where tracker metadata changes.

## Context

This issue depends on all earlier session-scoped final-delivery guard slices because it verifies the complete concurrent-session behavior across hook resolution, guard checks, task ownership, repair lifecycle, delivered archive, and documentation contracts.

The PRD lists concurrent session behavior as the reason for replacing the project-level singleton active target.

## Dependencies

- Depends on: [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]], [[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]], [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]], [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]], [[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]], [[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]]
- Blocks: none

## User Stories Covered

43, 44, 45

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/test_delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/test_skill_contracts.py`
- Test fixtures under the established local test fixture location
- `docs/issues/_views/` after dependency views are regenerated

## Acceptance Tests

- Session A and session B each have a different session-scoped active target in the same project.
- Session A can pass guard validation for PDF A while session B remains unrelated.
- Session B can block on PDF B while session A still passes.
- A stale `.codex/delivery-targets/current.json` cannot approve or block either session.
- Task-index ownership prevents two active sessions from advancing the same video output directory.
- Explicit handoff allows a new session to continue a previously owned task.
- Delivered archive for one session does not remove another session's active target.
- Any diagnostic `--video-output-dir` entry point cannot satisfy final delivery without a valid session-scoped active target.
- The issue dependency view generator validates this issue set after publication.

## Acceptance Criteria

- [x] Concurrent-session fixtures cover independent pass and block outcomes.
- [x] Singleton target regression coverage proves the legacy path is ignored.
- [x] Ownership conflict and handoff coverage prove same-directory safety.
- [x] Delivered archive coverage proves session isolation.
- [x] Documentation contract tests and dependency-view validation pass.

## Execution Log

- 2026-07-06: Created from [[prd/final-delivery-guard-and-bounded-repair]] and [[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]].
- 2026-07-07: Added concurrent session guard fixtures for A/B pass-block isolation, legacy singleton rejection, ownership conflict and handoff, delivered archive isolation, and diagnostic entrypoint boundaries.
- 2026-07-07: Implementation review subagent Hypatia confirmed the fixture coverage and passed the delivery guard test file.
- 2026-07-07: Independent verification subagent Huygens passed 91 unittest cases, dependency-view validation, and `git diff --check`; issue 07 approved for commit.

## Comments

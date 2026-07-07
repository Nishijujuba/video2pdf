---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]]"
blocks:
  - "[[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]]"
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

# 02 - Validate session-scoped delivery targets end to end

Status: done

## Goal

Make `delivery_guard.py check` validate a complete delivery target through the session-scoped active target model.

## What to build

Extend the guard check so a session-scoped active target drives the full mechanical delivery proof. A completed slice should validate the session target, the video-level delivery target, allowed artifact manifest, Acceptance Report, rendered page evidence, final PDF membership, artifact fingerprints, and guard report freshness from one explicit session target.

The check command must have a documented invocation path for non-hook render workflows. That path may use an explicit current-target argument or another explicit session-scoped input, and it must not fall back to the legacy singleton path.

## Context

This issue depends on [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]] because the active target path and missing-session behavior must be established first.

The semantic acceptance source remains `acceptance_report.json`. `delivery_guard.py` only proves that the acceptance evidence is fresh, complete, decision-enforced, and bound to the delivered artifacts.

## Dependencies

- Depends on: [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]]
- Blocks: [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]], [[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]], [[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]]

## User Stories Covered

20, 21, 22, 23, 26, 29, 30, 31, 32, 33, 38, 39, 43, 45

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/test_delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/SKILL.md` when command usage text changes

## Acceptance Tests

- A passing session target writes a fresh `delivery_guard_report.json`.
- A missing session target blocks check execution.
- A malformed session target blocks with a specific reason.
- A session target path escape blocks.
- A missing video-level `delivery_target.json` blocks.
- A missing allowed artifacts manifest blocks.
- A final PDF absent from the allowed manifest blocks.
- A failed, malformed, forbidden-context, or stale Acceptance Report blocks through the existing report validator.
- Missing or incomplete rendered page evidence blocks.
- Changed final PDF, main TeX, manifest, Acceptance Report, or final artifact fingerprints make an existing guard report stale.
- A fresh passing guard report is reusable only when all relevant fingerprints still match.

## Acceptance Criteria

- [x] `delivery_guard.py check` resolves session-scoped target state without using `.codex/delivery-targets/current.json`.
- [x] Guard reports bind the current session target and video-output evidence.
- [x] Guard failures remain mechanical and do not make semantic quality judgments.
- [x] Tests cover fresh pass, stale evidence, missing evidence, path escape, failed report, and page coverage cases.
- [x] The final-delivery acceptance skill documents the supported non-hook check invocation.

## Execution Log

- 2026-07-06: Created from [[prd/final-delivery-guard-and-bounded-repair]] and [[adr/0004-use-session-scoped-delivery-targets-for-final-delivery-guard]].
- 2026-07-07: Updated `delivery_guard.py check` to require explicit session-scoped current targets, bind session target fingerprints into guard reports, document the supported non-hook invocation, and cover missing/malformed/stale/path-escape cases. Independent verification initially caught a path-shape gap, then passed after the explicit session target path guard was added.

## Comments

---
type: issue
status: done
feature: "[[prd/issue-dependency-views]]"
depends_on:
  - "[[issues/issue-dependency-views/01-define-issue-metadata-model-and-fingerprint]]"
blocks:
  - "[[issues/issue-dependency-views/03-generate-single-feature-mermaid-dependency-views]]"
  - "[[issues/issue-dependency-views/04-generate-dependency-index-and-execution-summaries]]"
  - "[[issues/issue-dependency-views/05-add-cli-generation-and-validation-modes]]"
related_adrs: []
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 02 - Validate dependency consistency and status semantics

Status: done

## Goal

Make dependency metadata trustworthy before generated views are used as execution guides.

## What to build

Add consistency validation for Feature Issue Sets. Validation should detect missing issue links, `depends_on` and `blocks` inverse mismatches, circular dependencies, unknown statuses, and stale generated views.

Add calculation helpers for currently executable issues, status-blocked issues, and dependency-blocked issues.

## Context

This issue depends on the metadata model from [[issues/issue-dependency-views/01-define-issue-metadata-model-and-fingerprint]]. It implements the semantics defined in `CONTEXT.md`: Issue Dependency Edge, Currently Executable Issue, Status-Blocked Issue, Dependency-Blocked Issue, and Issue Dependency Consistency Error.

## Dependencies

- Depends on: [[issues/issue-dependency-views/01-define-issue-metadata-model-and-fingerprint]]
- Blocks: [[issues/issue-dependency-views/03-generate-single-feature-mermaid-dependency-views]], [[issues/issue-dependency-views/04-generate-dependency-index-and-execution-summaries]], [[issues/issue-dependency-views/05-add-cli-generation-and-validation-modes]]

## Expected Touched Paths

- `scripts/generate_issue_dependency_views.py`
- Tests for consistency validation and execution-state calculation

## Acceptance Tests

- Validation passes for a consistent issue set.
- Validation fails when an issue depends on a missing issue.
- Validation fails when `blocks` does not match the inverse of `depends_on`.
- Validation fails for a dependency cycle.
- Validation fails for an unknown status.
- Currently executable issues require status `ready-for-agent` or `ready-for-human` and completed dependencies.
- Dependency-blocked issues are separated from issues with `status: blocked`.

## Acceptance Criteria

- [x] Consistency errors include actionable issue paths and missing or mismatched links.
- [x] Cycle detection reports the cycle path.
- [x] Status semantics match `docs/agents/issue-tracker.md`.
- [x] Validation helpers are reusable by Mermaid, index, and CLI code.

## Execution Log

- 2026-07-05: Created from [[prd/issue-dependency-views]].
- 2026-07-05: Implemented consistency validation and execution-state helpers in `scripts/generate_issue_dependency_views.py`.

## Comments

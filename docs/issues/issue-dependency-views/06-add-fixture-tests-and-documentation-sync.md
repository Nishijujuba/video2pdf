---
type: issue
status: done
feature: "[[prd/issue-dependency-views]]"
depends_on:
  - "[[issues/issue-dependency-views/04-generate-dependency-index-and-execution-summaries]]"
  - "[[issues/issue-dependency-views/05-add-cli-generation-and-validation-modes]]"
blocks: []
related_adrs: []
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 06 - Add fixture tests and documentation sync

Status: done

## Goal

Close the implementation with fixture coverage and synchronized tracker documentation.

## What to build

Add end-to-end fixture tests for a small Feature Issue Set. The tests should cover parsing, consistency validation, generation, freshness checking, status color output, next executable calculation, waiting-on-dependencies calculation, and index output.

Update repository documentation if implementation details differ from the planned contract.

## Context

This issue closes [[prd/issue-dependency-views]]. It depends on the generated index and CLI modes.

## Dependencies

- Depends on: [[issues/issue-dependency-views/04-generate-dependency-index-and-execution-summaries]], [[issues/issue-dependency-views/05-add-cli-generation-and-validation-modes]]
- Blocks: none

## Expected Touched Paths

- Tests for `scripts/generate_issue_dependency_views.py`
- `docs/agents/issue-tracker.md`
- `CONTEXT.md` if terminology requires correction
- Generated `_views` fixtures or golden files as appropriate

## Acceptance Tests

- Fixture generation produces expected single-feature Markdown.
- Fixture generation produces expected index Markdown.
- Fixture validation passes after generation.
- Fixture validation fails after dependency metadata changes without regeneration.
- Fixture validation fails for an inverse mismatch between `depends_on` and `blocks`.
- Fixture validation fails for a cycle.
- Documentation examples match the implemented CLI.

## Acceptance Criteria

- [x] Tests cover the complete first-version Markdown-only scope.
- [x] `docs/agents/issue-tracker.md` matches the implemented CLI and output contract.
- [x] Any mismatch between implementation and `CONTEXT.md` terminology is corrected.
- [x] The issue set can be closed only after validation mode passes on the real repo issue tracker.

## Execution Log

- 2026-07-05: Created from [[prd/issue-dependency-views]].
- 2026-07-05: Added end-to-end CLI fixture coverage and synchronized `docs/agents/issue-tracker.md`.

## Comments

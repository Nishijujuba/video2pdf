---
type: issue
status: done
feature: "[[prd/issue-dependency-views]]"
depends_on:
  - "[[issues/issue-dependency-views/02-validate-dependency-consistency-and-status-semantics]]"
  - "[[issues/issue-dependency-views/03-generate-single-feature-mermaid-dependency-views]]"
blocks:
  - "[[issues/issue-dependency-views/05-add-cli-generation-and-validation-modes]]"
  - "[[issues/issue-dependency-views/06-add-fixture-tests-and-documentation-sync]]"
related_adrs: []
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 04 - Generate dependency index and execution summaries

Status: done

## Goal

Create the Obsidian entry point that summarizes all Feature Issue Sets and links to each dependency view.

## What to build

Generate `docs/issues/_views/index.md`. The index should include one row or section per Feature Issue Set and summarize issue count, status distribution, root issues, currently executable issues, status-blocked issues, and dependency-blocked issues.

The index should link to every generated `<feature-slug>-dependencies.md` file.

## Context

This issue implements the Issue Dependency Index defined in [[prd/issue-dependency-views]] and `CONTEXT.md`. It depends on consistency semantics and single-feature view generation.

## Dependencies

- Depends on: [[issues/issue-dependency-views/02-validate-dependency-consistency-and-status-semantics]], [[issues/issue-dependency-views/03-generate-single-feature-mermaid-dependency-views]]
- Blocks: [[issues/issue-dependency-views/05-add-cli-generation-and-validation-modes]], [[issues/issue-dependency-views/06-add-fixture-tests-and-documentation-sync]]

## Expected Touched Paths

- `scripts/generate_issue_dependency_views.py`
- `docs/issues/_views/index.md` generated fixture or golden file
- Tests for index output

## Acceptance Tests

- The index includes every Feature Issue Set under `docs/issues/<feature-slug>/`.
- The index excludes `docs/issues/_views/` from source issue set discovery.
- Each Feature Issue Set links to its generated dependency page.
- The index reports issue count and status distribution correctly.
- The index lists root issues with no same-set dependencies.
- The index lists currently executable issues, status-blocked issues, and dependency-blocked issues separately.

## Acceptance Criteria

- [x] `docs/issues/_views/index.md` is deterministic except for `generated_at`.
- [x] The index gives a one-screen answer to which batch and issue can be picked up next.
- [x] The index reports consistency errors without hiding the affected Feature Issue Set.

## Execution Log

- 2026-07-05: Created from [[prd/issue-dependency-views]].
- 2026-07-05: Implemented dependency index discovery and execution summaries in `scripts/generate_issue_dependency_views.py`.

## Comments

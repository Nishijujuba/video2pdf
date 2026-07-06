---
type: issue
status: done
feature: "[[prd/issue-dependency-views]]"
depends_on:
  - "[[issues/issue-dependency-views/02-validate-dependency-consistency-and-status-semantics]]"
  - "[[issues/issue-dependency-views/03-generate-single-feature-mermaid-dependency-views]]"
  - "[[issues/issue-dependency-views/04-generate-dependency-index-and-execution-summaries]]"
blocks:
  - "[[issues/issue-dependency-views/06-add-fixture-tests-and-documentation-sync]]"
related_adrs: []
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 05 - Add CLI generation and validation modes

Status: done

## Goal

Make dependency-view generation repeatable for both all-feature refreshes and single-feature editing.

## What to build

Add the CLI interface for `scripts/generate_issue_dependency_views.py`. The default command should process every Feature Issue Set. A single-feature option should refresh or validate one `feature-slug`.

Add a validation mode that checks consistency and freshness without writing files. Validation mode must exit non-zero when errors exist.

## Context

This issue wires together the generator pieces from [[issues/issue-dependency-views/02-validate-dependency-consistency-and-status-semantics]], [[issues/issue-dependency-views/03-generate-single-feature-mermaid-dependency-views]], and [[issues/issue-dependency-views/04-generate-dependency-index-and-execution-summaries]].

## Dependencies

- Depends on: [[issues/issue-dependency-views/02-validate-dependency-consistency-and-status-semantics]], [[issues/issue-dependency-views/03-generate-single-feature-mermaid-dependency-views]], [[issues/issue-dependency-views/04-generate-dependency-index-and-execution-summaries]]
- Blocks: [[issues/issue-dependency-views/06-add-fixture-tests-and-documentation-sync]]

## Expected Touched Paths

- `scripts/generate_issue_dependency_views.py`
- Tests for CLI generation and validation modes

## Acceptance Tests

- Running the script with no feature filter generates all single-feature dependency views and the index.
- Running the script with a feature filter generates only that feature's single-feature view and refreshes the index consistently.
- Validation mode performs no writes.
- Validation mode exits zero when generated views are fresh and metadata is consistent.
- Validation mode exits non-zero when generated views are stale.
- Validation mode exits non-zero when dependency consistency errors exist.

## Acceptance Criteria

- [x] CLI help documents generation, validation, and single-feature options.
- [x] Validation mode is safe for read-only checks.
- [x] The command works from the project root on Windows PowerShell.
- [x] Exit codes are documented in the script help or issue execution log.

## Execution Log

- 2026-07-05: Created from [[prd/issue-dependency-views]].
- 2026-07-05: Implemented CLI generation and validation modes. Exit code `0` means fresh and consistent; exit code `1` means consistency, freshness, or feature-selection errors were found.

## Comments

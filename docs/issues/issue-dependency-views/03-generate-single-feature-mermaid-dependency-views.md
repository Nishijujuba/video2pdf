---
type: issue
status: done
feature: "[[prd/issue-dependency-views]]"
depends_on:
  - "[[issues/issue-dependency-views/01-define-issue-metadata-model-and-fingerprint]]"
  - "[[issues/issue-dependency-views/02-validate-dependency-consistency-and-status-semantics]]"
blocks:
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

# 03 - Generate single-feature Mermaid dependency views

Status: done

## Goal

Generate a reviewable Markdown dependency view for each Feature Issue Set.

## What to build

Add Markdown rendering for `docs/issues/_views/<feature-slug>-dependencies.md`. The page should include header metadata, consistency errors, `Next executable`, `Waiting on dependencies`, and a left-to-right Mermaid dependency graph.

The graph should draw execution edges from `depends_on`, arrange issues by dependency layer, preserve issue numbers in node labels, and color nodes from frontmatter `status` using the fixed status palette.

## Context

This issue implements the single-feature view contract from [[prd/issue-dependency-views]]. It depends on metadata parsing and consistency semantics from the first two issues.

## Dependencies

- Depends on: [[issues/issue-dependency-views/01-define-issue-metadata-model-and-fingerprint]], [[issues/issue-dependency-views/02-validate-dependency-consistency-and-status-semantics]]
- Blocks: [[issues/issue-dependency-views/04-generate-dependency-index-and-execution-summaries]], [[issues/issue-dependency-views/05-add-cli-generation-and-validation-modes]]

## Expected Touched Paths

- `scripts/generate_issue_dependency_views.py`
- `docs/issues/_views/<feature-slug>-dependencies.md` generated fixtures or golden files
- Tests for Mermaid Markdown output

## Acceptance Tests

- A fixture issue set renders a Markdown dependency view with required header metadata.
- The `Consistency errors` section says `None` when no errors exist.
- `Next executable` lists currently executable issues sorted by issue number.
- `Waiting on dependencies` lists dependency-blocked issues and their unfinished upstream issues.
- Mermaid output uses `flowchart LR`.
- Mermaid nodes preserve issue numbers.
- Mermaid class definitions use the fixed status color palette.

## Acceptance Criteria

- [x] Generated Markdown is deterministic except for `generated_at`.
- [x] Mermaid output is readable in Obsidian.
- [x] Node color carries only issue status.
- [x] Dependency-blocked state is visible without overloading color.

## Execution Log

- 2026-07-05: Created from [[prd/issue-dependency-views]].
- 2026-07-05: Implemented single-feature Markdown and Mermaid rendering in `scripts/generate_issue_dependency_views.py`.

## Comments

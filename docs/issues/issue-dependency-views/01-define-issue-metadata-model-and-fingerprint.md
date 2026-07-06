---
type: issue
status: done
feature: "[[prd/issue-dependency-views]]"
depends_on: []
blocks:
  - "[[issues/issue-dependency-views/02-validate-dependency-consistency-and-status-semantics]]"
  - "[[issues/issue-dependency-views/03-generate-single-feature-mermaid-dependency-views]]"
related_adrs: []
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 01 - Define issue metadata model and fingerprint

Status: done

## Goal

Create the metadata model that `scripts/generate_issue_dependency_views.py` will use to read Feature Issue Sets and compute freshness fingerprints.

## What to build

Add the parser and data model for issue files under `docs/issues/<feature-slug>/*.md`. The model should read only dependency-view inputs: relative path, title, `status`, `feature`, `depends_on`, `blocks`, and `related_adrs`.

Add a deterministic source issue fingerprint that excludes issue body prose, execution logs, comments, and unrelated content.

## Context

This issue starts implementation for [[prd/issue-dependency-views]]. The domain terms are defined in root `CONTEXT.md`: Feature Issue Set, Issue Dependency View Generator, Issue Dependency View Freshness, and Issue Dependency Source Fingerprint.

## Dependencies

- Depends on: none
- Blocks: [[issues/issue-dependency-views/02-validate-dependency-consistency-and-status-semantics]], [[issues/issue-dependency-views/03-generate-single-feature-mermaid-dependency-views]]

## Expected Touched Paths

- `scripts/generate_issue_dependency_views.py`
- Tests for issue metadata parsing and fingerprinting

## Acceptance Tests

- A fixture issue file parses title, status, feature, dependencies, blockers, and related ADRs.
- The parser preserves issue relative paths using Obsidian-compatible link targets.
- The fingerprint changes when status or dependency metadata changes.
- The fingerprint does not change when body prose, execution logs, or comments change.
- Unknown or missing required metadata is reported as a consistency error for later validation.

## Acceptance Criteria

- [x] Issue metadata parsing is implemented without relying on non-standard dependencies.
- [x] Source issue fingerprints are deterministic across repeated runs.
- [x] Fingerprint inputs match the PRD contract exactly.
- [x] Tests cover metadata-only changes and body-only changes.

## Execution Log

- 2026-07-05: Created from [[prd/issue-dependency-views]].
- 2026-07-05: Implemented in `scripts/generate_issue_dependency_views.py` with tests in `scripts/test_generate_issue_dependency_views.py`.

## Comments

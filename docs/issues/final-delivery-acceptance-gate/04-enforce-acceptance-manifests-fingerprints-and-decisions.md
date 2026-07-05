---
type: issue
status: done
feature: "[[prd/final-delivery-acceptance-gate]]"
depends_on:
  - "[[issues/final-delivery-acceptance-gate/01-validate-acceptance-criteria-and-report-contracts]]"
  - "[[issues/final-delivery-acceptance-gate/02-generate-rendered-pdf-page-evidence]]"
  - "[[issues/final-delivery-acceptance-gate/03-codify-read-only-acceptance-reviewer-skill]]"
blocks:
  - "[[issues/final-delivery-acceptance-gate/05-define-acceptance-repair-rerun-loop]]"
  - "[[issues/final-delivery-acceptance-gate/06-integrate-final-acceptance-into-bilibili-and-youtube]]"
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-04
updated: 2026-07-04
tags:
  - issue
  - status/done
---

# 04 - Enforce acceptance manifests, fingerprints, and decisions

Status: done

## Goal

Make final delivery mechanically depend on a fresh passing Acceptance Report that matches the allowed artifacts manifest and current final artifact fingerprints.

## What to build

Add the orchestration contract for creating `review/acceptance/allowed_artifacts_manifest.json`, computing artifact fingerprints, validating reviewer output, and returning a delivery-blocking result. A completed slice should let a workflow coordinator decide pass or fail from artifacts on disk without reading reviewer prose or hidden context.

The manifest is the only artifact list passed to the Acceptance Reviewer. The report validator must verify that `review_context_used.artifacts_read` is a subset of manifest final artifacts plus the criteria file, and that report evidence does not cite forbidden paths.

## Context

This issue implements the "Context isolation mechanism", "Minimum report JSON contract", "Implementation Decisions", and "Testing Decisions" sections of [[prd/final-delivery-acceptance-gate]]. It applies the ADR rule that `acceptance_report.json` is the only machine-readable delivery decision source.

Relevant domain concepts from root `CONTEXT.md`: Acceptance Review Context, Acceptance Decision Source, Acceptance Report Freshness, Acceptance Evidence, Video Output Directory, and Acceptance Evidence.

## Dependencies

- Depends on: [[issues/final-delivery-acceptance-gate/01-validate-acceptance-criteria-and-report-contracts]], [[issues/final-delivery-acceptance-gate/02-generate-rendered-pdf-page-evidence]], [[issues/final-delivery-acceptance-gate/03-codify-read-only-acceptance-reviewer-skill]]
- Blocks: [[issues/final-delivery-acceptance-gate/05-define-acceptance-repair-rerun-loop]], [[issues/final-delivery-acceptance-gate/06-integrate-final-acceptance-into-bilibili-and-youtube]]

## User Stories Covered

1, 3, 4, 6, 15, 16, 19, 38, 39, 40, 46, 52

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_report.py`
- Generated manifest path: `<video-output-dir>/review/acceptance/allowed_artifacts_manifest.json`
- Generated report path: `<video-output-dir>/review/acceptance/acceptance_report.json`
- Generated summary path: `<video-output-dir>/review/acceptance/acceptance_summary.md`
- Tests or fixtures that exercise fresh pass, stale report, failed report, malformed report, and forbidden-context report cases

## Acceptance Tests

- Manifest creation records `criteria_file`, `review_output_dir`, final artifact roles and paths, and forbidden artifact categories.
- Fingerprint generation records `sha256`, `size_bytes`, and `size_chars` for text artifacts where character size is available.
- A fresh passing report validates and returns a delivery-allowing result.
- A missing report, malformed report, failed report, stale report, report with missing artifacts, and report with forbidden context each return a delivery-blocking result.
- Evidence paths outside manifest final artifacts and the criteria file are rejected.
- Optional `acceptance_summary.md` never overrides the JSON decision.

## Delivery Blocking Behavior

- Delivery must block without `review/acceptance/acceptance_report.json`.
- Delivery must block unless the report is fresh for every in-scope final artifact.
- Delivery must block unless the report's JSON decision is a coherent pass.
- Delivery must block if a Markdown summary claims approval while the JSON report fails or is invalid.

## Acceptance Criteria

- [x] The acceptance flow creates `allowed_artifacts_manifest.json` before reviewer launch.
- [x] The validator compares report context and evidence paths against the manifest.
- [x] The validator compares report fingerprints against current final artifacts.
- [x] Delivery automation can rely on validator exit status or equivalent result without parsing Markdown prose.
- [x] Test coverage proves missing, malformed, failed, stale, and forbidden-context reports all block delivery.

## Execution Log

- 2026-07-04: Created from [[prd/final-delivery-acceptance-gate]].
- 2026-07-04: Implemented manifest creation, fingerprint freshness checks, context/evidence boundary checks, and JSON decision enforcement in `validate_acceptance_report.py`.

## Comments

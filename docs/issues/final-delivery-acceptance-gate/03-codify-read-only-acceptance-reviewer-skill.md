---
type: issue
status: done
feature: "[[prd/final-delivery-acceptance-gate]]"
depends_on:
  - "[[issues/final-delivery-acceptance-gate/01-validate-acceptance-criteria-and-report-contracts]]"
  - "[[issues/final-delivery-acceptance-gate/02-generate-rendered-pdf-page-evidence]]"
blocks:
  - "[[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]]"
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

# 03 - Codify read-only Acceptance Reviewer skill

Status: done

## Goal

Define the Acceptance Reviewer as an independent, read-only subagent role that evaluates only final artifacts and the criteria file, then writes `acceptance_report.json` and optional `acceptance_summary.md`.

## What to build

Write the `final-delivery-acceptance` skill instructions and project-level role requirements so the Acceptance Reviewer has a clear context boundary, allowed inputs, forbidden inputs, report responsibilities, and output paths. The completed slice should make the reviewer executable by a future workflow coordinator without relying on chat memory or generation context.

The reviewer must continue evaluating all criteria after finding failures, must use full final text scans for style checks, must use rendered page evidence for visual checks, and must remain read-only with respect to final artifacts and criteria.

## Context

This issue implements the "Context isolation mechanism", "Minimum report JSON contract", "Rendered page evidence", and role-related implementation decisions from [[prd/final-delivery-acceptance-gate]]. It follows [[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]], which requires an independent reviewer and forbids generation-process-assisted review.

Relevant domain concepts from root `CONTEXT.md`: Acceptance Reviewer, Acceptance Review Context, Complete Acceptance Evaluation, Acceptance Revision Guidance, Full Artifact Style Scan, Full Rendered PDF Visual Scan, and Acceptance Evidence.

## Dependencies

- Depends on: [[issues/final-delivery-acceptance-gate/01-validate-acceptance-criteria-and-report-contracts]], [[issues/final-delivery-acceptance-gate/02-generate-rendered-pdf-page-evidence]]
- Blocks: [[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]], [[issues/final-delivery-acceptance-gate/05-define-acceptance-repair-rerun-loop]], [[issues/final-delivery-acceptance-gate/06-integrate-final-acceptance-into-bilibili-and-youtube]]

## User Stories Covered

2, 3, 4, 12, 31, 32, 34, 35, 36, 37, 40, 47

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `AGENTS.md`
- `.agents/skills/final-delivery-acceptance/references/acceptance-report.schema.json`
- Generated reviewer output paths: `<video-output-dir>/review/acceptance/acceptance_report.json` and `<video-output-dir>/review/acceptance/acceptance_summary.md`

## Acceptance Tests

- Skill validation passes for `.agents/skills/final-delivery-acceptance`.
- The skill instructions explicitly allow only final artifacts from `allowed_artifacts_manifest.json` plus the criteria file.
- The skill instructions explicitly forbid generation notes, writer drafts, chat history, `work/`, `review/pyramid/`, `review/consistency/`, repair discussion, and intermediate files.
- The report instructions require one `criterion_results[]` entry per configured criterion.
- Failed criterion instructions require artifact-grounded evidence and non-null revision guidance.
- Visual review instructions require every rendered page image to be inspected and represented in `visual_scan_evidence.pages_checked[]`.
- Project instructions require the Acceptance Reviewer as a final role and preserve separation from repair subagents.

## Delivery Blocking Behavior

- Delivery must block when no independent Acceptance Reviewer report exists.
- Delivery must block when the reviewer report declares forbidden context usage.
- Delivery must block when the reviewer omits any criterion or any rendered PDF page from the report.

## Acceptance Criteria

- [x] `.agents/skills/final-delivery-acceptance/SKILL.md` defines the reviewer inputs, forbidden context, outputs, and report duties.
- [x] `AGENTS.md` records the Acceptance Reviewer as a required final role for `/bilibili-render-pdf` and `/youtube-render-pdf` delivery.
- [x] The reviewer instructions require complete criteria evaluation and complete rendered page visual coverage.
- [x] The reviewer instructions preserve read-only review and forbid artifact modification.
- [x] Validation or tests prove the skill instructions contain the required context isolation and report-shape obligations.

## Execution Log

- 2026-07-04: Created from [[prd/final-delivery-acceptance-gate]].
- 2026-07-04: Added `final-delivery-acceptance` skill instructions plus AGENTS/CLAUDE role requirements; verified with `test_skill_contracts.py` and skill quick validation.

## Comments

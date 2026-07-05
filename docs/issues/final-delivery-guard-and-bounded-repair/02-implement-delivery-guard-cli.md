---
type: issue
status: done
feature: "[[prd/final-delivery-guard-and-bounded-repair]]"
depends_on:
  - "[[issues/final-delivery-guard-and-bounded-repair/01-establish-delivery-target-contracts]]"
blocks:
  - "[[issues/final-delivery-guard-and-bounded-repair/03-enforce-delivery-guard-with-stop-hook]]"
  - "[[issues/final-delivery-guard-and-bounded-repair/05-integrate-guard-and-repair-into-render-skills]]"
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/done
---

# 02 - Implement delivery guard CLI

Status: done

## Goal

Implement `delivery_guard.py check` as the shared mechanical gate that proves a current Acceptance Report can authorize final delivery of the current PDF.

## What to build

Add the guard CLI around the target contract from [[issues/final-delivery-guard-and-bounded-repair/01-establish-delivery-target-contracts]]. A completed slice should read the active delivery target, validate the current Acceptance Report with decision enforcement, verify that the reviewed final PDF is in the allowed artifact manifest, verify rendered-page evidence coverage, bind current artifact fingerprints, and write `review/acceptance/delivery_guard_report.json`.

The guard remains mechanical. It checks freshness, completeness, path boundaries, and decision validity. It leaves semantic and visual quality judgment to the independent Acceptance Reviewer.

## Context

This issue implements the guard proof described in [[prd/final-delivery-guard-and-bounded-repair]]. It reuses the existing Acceptance Report validator and final-artifact manifest decisions from [[prd/final-delivery-acceptance-gate]].

## Dependencies

- Depends on: [[issues/final-delivery-guard-and-bounded-repair/01-establish-delivery-target-contracts]]
- Blocks: [[issues/final-delivery-guard-and-bounded-repair/03-enforce-delivery-guard-with-stop-hook]], [[issues/final-delivery-guard-and-bounded-repair/05-integrate-guard-and-repair-into-render-skills]]

## User Stories Covered

19, 20, 21, 26, 29, 30, 31, 32, 33, 43

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_report.py`
- `.agents/skills/final-delivery-acceptance/scripts/render_pdf_pages.py`
- Tests or fixtures for guard pass, fail, stale, and page-coverage states

## Acceptance Tests

- `delivery_guard.py check` exits successfully for a fixture target with a passing Acceptance Report, current artifact fingerprints, a manifest containing the final PDF, and rendered page evidence for every PDF page.
- The command writes `review/acceptance/delivery_guard_report.json` with `schema_version`, `status`, `checked_at`, `stage`, `video_output_dir`, `final_pdf`, `validated_by`, `acceptance_report_status`, `artifact_fingerprints`, `checked_conditions`, and `blocking_message`.
- The command fails closed when the Acceptance Report is missing, malformed, failed, stale, or rejected by `validate_acceptance_report.py validate --enforce-decision`.
- The command fails when the final PDF is absent from `allowed_artifacts_manifest.json`.
- The command fails when rendered-page evidence is missing or covers fewer pages than the current PDF.
- A previous guard report is accepted only when its recorded fingerprints still match current artifacts.
- Failure reports include a specific blocking message suitable for a Stop hook response.

## Delivery Blocking Behavior

- A missing, failed, malformed, stale, or incomplete guard result must block final delivery.
- A guard pass must be tied to the current final PDF, manifest, Acceptance Report, main TeX, and final artifacts named by the manifest.
- The guard must never convert a failed Acceptance Report into a delivery pass.

## Acceptance Criteria

- [x] `delivery_guard.py check` validates the active target and writes a guard report.
- [x] The guard delegates Acceptance Report decision validation to the existing report validator with decision enforcement.
- [x] The guard enforces final-PDF membership in the allowed artifact manifest.
- [x] The guard checks rendered page evidence against current PDF page count.
- [x] The guard records current fingerprints and rejects stale guard reports.
- [x] Tests cover pass, failure, malformed, stale, missing-page, and manifest-mismatch cases.

## Execution Log

- 2026-07-05: Created from [[prd/final-delivery-guard-and-bounded-repair]].
- 2026-07-05: Implemented `delivery_guard.py check`, guard reports, manifest/page/fingerprint enforcement, and failure messages; verified with `test_delivery_guard.py`.

## Comments

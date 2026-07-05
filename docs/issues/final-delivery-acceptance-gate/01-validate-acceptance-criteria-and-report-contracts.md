---
type: issue
status: done
feature: "[[prd/final-delivery-acceptance-gate]]"
depends_on: []
blocks:
  - "[[issues/final-delivery-acceptance-gate/02-generate-rendered-pdf-page-evidence]]"
  - "[[issues/final-delivery-acceptance-gate/03-codify-read-only-acceptance-reviewer-skill]]"
  - "[[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]]"
related_adrs:
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-04
updated: 2026-07-04
tags:
  - issue
  - status/done
---

# 01 - Validate acceptance criteria and report contracts

Status: done

## Goal

Create the fail-closed JSON contract foundation for the Final Delivery Acceptance Gate so criteria files and Acceptance Reports can be validated before any workflow treats them as delivery decisions.

## What to build

Add the `final-delivery-acceptance` skill contract skeleton, JSON schemas, criteria validator, and report validator. Align `docs/acceptance/acceptance_criteria.v1.json` with the PRD's required minimum contract, including the currently missing `criteria_version` field.

The completed slice should let an agent validate a criteria file and a report JSON independently, with strict rejection for malformed contracts, non-blocking categories, advisory/severity fields, missing criterion results, incoherent pass/fail states, forbidden review context claims, and stale or missing artifact fingerprints.

## Context

This issue implements the contract and validator foundation from [[prd/final-delivery-acceptance-gate]] and follows the decision in [[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]].

The key domain concepts are defined in root `CONTEXT.md`: Acceptance Criteria File, Acceptance Criteria Schema, Acceptance Report, Acceptance Report Schema, Blocking-Only Acceptance Criteria, Acceptance Decision Source, Acceptance Report Freshness, and Acceptance Evidence.

Known source mismatch: `docs/acceptance/acceptance_criteria.v1.json` currently lacks `criteria_version`, while the PRD requires it in the minimum criteria JSON contract.

## Dependencies

- Depends on: none
- Blocks: [[issues/final-delivery-acceptance-gate/02-generate-rendered-pdf-page-evidence]], [[issues/final-delivery-acceptance-gate/03-codify-read-only-acceptance-reviewer-skill]], [[issues/final-delivery-acceptance-gate/04-enforce-acceptance-manifests-fingerprints-and-decisions]]

## User Stories Covered

5, 6, 7, 8, 9, 10, 11, 15, 16, 17, 31, 33, 36, 38, 39, 40, 46, 52

## Expected Touched Paths

- `docs/acceptance/acceptance_criteria.v1.json`
- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_criteria.py`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_report.py`
- `.agents/skills/final-delivery-acceptance/references/acceptance-criteria.schema.json`
- `.agents/skills/final-delivery-acceptance/references/acceptance-report.schema.json`
- Tests or fixtures under `.agents/skills/final-delivery-acceptance/scripts/` or the repo's established test location

## Acceptance Tests

- Valid criteria JSON passes validation.
- Criteria validation rejects malformed JSON, missing `criteria_version`, missing required fields, empty `criteria`, unknown categories, severity fields, advisory checks, score-only checks, and non-blocking criteria.
- Valid pass and fail reports pass validation when they match the criteria file and current artifacts.
- Report validation rejects missing criterion results, duplicate criterion results, invalid result status, overall `pass` with failed criteria, mismatched `failed_criteria`, inconsistent `revision_required`, missing failed-result evidence, and missing failed-result revision guidance.
- Report validation rejects reports that declare generation process context, reference artifacts outside the allowed manifest, or cite evidence outside allowed final artifacts and the criteria file.
- Fingerprint validation rejects changed text artifacts, changed PDF artifacts, missing artifacts, renamed artifacts, and reports missing required fingerprint entries.

## Delivery Blocking Behavior

- A missing, malformed, stale, or internally inconsistent Acceptance Report must block delivery.
- A report that claims `overall_status: "pass"` while any criterion fails must block delivery.
- A report that used forbidden context must block delivery.

## Acceptance Criteria

- [x] The criteria schema and validator enforce the PRD's minimum criteria JSON contract, including `criteria_version`.
- [x] The report schema and validator enforce the PRD's minimum Acceptance Report JSON contract.
- [x] The validators fail closed for malformed, incomplete, stale, or context-violating evidence.
- [x] The default criteria file validates against the new criteria schema.
- [x] Test coverage demonstrates all acceptance-test cases listed above.
- [x] Skill documentation identifies `acceptance_report.json` as the only machine-readable delivery decision source.

## Execution Log

- 2026-07-04: Created from [[prd/final-delivery-acceptance-gate]].
- 2026-07-04: Implemented criteria/report schemas and validators under `.agents/skills/final-delivery-acceptance/`; verified with final-delivery-acceptance unittest discovery.

## Comments

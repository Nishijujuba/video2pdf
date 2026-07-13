---
type: issue
status: done
feature: "[[prd/delivery-glossary-terminology-governance]]"
depends_on:
  - "[[issues/delivery-glossary-terminology-governance/02-thread-delivery-glossary-through-final-artifact-manifest]]"
blocks:
  - "[[issues/delivery-glossary-terminology-governance/06-enforce-delivery-glossary-in-review-roles]]"
related_adrs:
  - "[[adr/0005-use-delivery-glossary-for-non-english-pdf-terms]]"
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-07
updated: 2026-07-07
tags:
  - issue
  - status/done
---

# 03 - Add glossary-aware acceptance criterion

Status: done

## Goal

Make glossary-aware terminology review a delivery-blocking style criterion for non-English teaching PDFs.

## What to build

Extend the acceptance criteria contract so the Acceptance Reviewer must check final PDF body text against the Delivery Glossary. The criterion should require each term's actual body rendering to match `body_display_strategy` and `where_to_preserve_english`, rather than merely checking whether the term was explained somewhere.

The criterion must preserve the v1 decision that `forbidden_body_forms` is a future optional extension, not a required field.

## Context

This issue implements the acceptance rule from [[prd/delivery-glossary-terminology-governance]] after [[issues/delivery-glossary-terminology-governance/02-thread-delivery-glossary-through-final-artifact-manifest]] makes the glossary allowed reviewer context.

The style acceptance concepts are defined in root `CONTEXT.md`: Style Acceptance Criterion, Full Artifact Style Scan, and Acceptance Evidence.

## Dependencies

- Depends on: [[issues/delivery-glossary-terminology-governance/02-thread-delivery-glossary-through-final-artifact-manifest]]
- Blocks: [[issues/delivery-glossary-terminology-governance/06-enforce-delivery-glossary-in-review-roles]]

## User Stories Covered

1, 2, 3, 4, 12, 13, 14, 15

## Expected Touched Paths

- `docs/acceptance/acceptance_criteria.v1.json`
- `.agents/skills/final-delivery-acceptance/references/`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_criteria.py`
- Tests or fixtures for criteria validation

## Acceptance Criteria

- [x] The default acceptance criteria include a glossary-aware style criterion for non-English teaching PDFs.
- [x] The criterion requires checking `body_display_strategy` and `where_to_preserve_english`.
- [x] The criterion treats bare English body use as a failure when a term requires `chinese_primary_only`.
- [x] The criterion treats missing English source preservation as a failure when a term requires visible preservation.
- [x] The criterion explicitly keeps `forbidden_body_forms` out of the v1 required contract while allowing it as future extension language.
- [x] Criteria validation passes for the updated default criteria file.

## Execution Log

- 2026-07-07: Created from [[prd/delivery-glossary-terminology-governance]].
- 2026-07-08: Added the default `delivery_glossary_term_strategy` style acceptance criterion, enforced its presence in criteria validation, and covered strategy fields, `chinese_primary_only`, visible English preservation, and optional `forbidden_body_forms` extension language. Independent verification passed focused criteria tests and criteria validation.

## Comments

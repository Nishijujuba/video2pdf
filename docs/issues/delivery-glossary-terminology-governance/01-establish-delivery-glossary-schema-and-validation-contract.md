---
type: issue
status: done
feature: "[[prd/delivery-glossary-terminology-governance]]"
depends_on: []
blocks:
  - "[[issues/delivery-glossary-terminology-governance/02-thread-delivery-glossary-through-final-artifact-manifest]]"
related_adrs:
  - "[[adr/0005-use-delivery-glossary-for-non-english-pdf-terms]]"
owner: unassigned
created: 2026-07-07
updated: 2026-07-07
tags:
  - issue
  - status/done
---

# 01 - Establish Delivery Glossary schema and validation contract

Status: done

## Goal

Create the schema and validation foundation for the Delivery Glossary so non-English teaching PDFs can declare a stable terminology contract before manifest, workflow, and acceptance integration depend on it.

## What to build

Add a `delivery_glossary.v1` contract for non-English teaching PDFs. The completed slice should validate the required top-level profile fields, term entries, body display strategy enum, source-English preservation enum, and extension behavior for future optional fields such as `forbidden_body_forms`.

The slice should make one glossary file independently checkable without requiring a completed PDF workflow. It should reject malformed or ambiguous glossary files before a coordinator, Consistency agent, or Acceptance Reviewer relies on them.

## Context

This issue implements the contract foundation from [[prd/delivery-glossary-terminology-governance]] and follows [[adr/0005-use-delivery-glossary-for-non-english-pdf-terms]].

The key domain concepts are defined in root `CONTEXT.md`: Delivery Glossary, Core English Expression, Body Display Strategy, Source English Preservation Location, and New Term Candidate.

## Dependencies

- Depends on: none
- Blocks: [[issues/delivery-glossary-terminology-governance/02-thread-delivery-glossary-through-final-artifact-manifest]]

## User Stories Covered

5, 6, 12, 13, 16, 20

## Expected Touched Paths

- `docs/acceptance/`
- `.agents/skills/final-delivery-acceptance/`
- Validation scripts or schema references under the established final-delivery acceptance skill
- Tests or fixtures under the established acceptance test location

## Acceptance Criteria

- [ ] A valid minimal `delivery_glossary.v1` file passes validation.
- [ ] Validation rejects missing required fields, empty required strings, invalid `language_profile`, invalid `body_display_strategy`, and invalid `where_to_preserve_english`.
- [ ] Validation accepts future optional fields without making `forbidden_body_forms` mandatory in v1.
- [ ] Fixture coverage includes `grief`, `capability overhang`, and a product-name exclusion case.
- [ ] Documentation states that the Delivery Glossary is a contract artifact, not a default PDF appendix.

## Execution Log

- 2026-07-07: Created from [[prd/delivery-glossary-terminology-governance]].
- 2026-07-07: Implemented standalone `delivery_glossary.v1` validator, schema reference, focused tests, acceptance documentation, skill script listing, and `CONTEXT.md` terminology definitions. Independent verification passed focused validator tests and CLI validation.

## Comments

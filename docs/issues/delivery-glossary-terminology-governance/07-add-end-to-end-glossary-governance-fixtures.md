---
type: issue
status: done
feature: "[[prd/delivery-glossary-terminology-governance]]"
depends_on:
  - "[[issues/delivery-glossary-terminology-governance/06-enforce-delivery-glossary-in-review-roles]]"
blocks: []
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

# 07 - Add end-to-end glossary governance fixtures

Status: done

## Goal

Prove the Delivery Glossary workflow works end to end across glossary schema, manifest inclusion, acceptance criteria, render workflow instructions, review role instructions, and report validation.

## What to build

Add regression fixtures or tests that exercise representative non-English teaching PDF terminology behavior. The completed slice should show that glossary-aware acceptance can pass a correctly localized body and fail body text that preserves source English awkwardly or loses required English source alignment.

The fixtures should cover the `grief` motivating case, technical terms such as `capability overhang`, method concepts such as `HTML mockup`, product-name exclusions, and the default no-appendix rule.

## Context

This issue validates the full feature from [[prd/delivery-glossary-terminology-governance]] after the contract, manifest, criteria, workflow, and reviewer slices are complete.

The relevant decisions are [[adr/0005-use-delivery-glossary-for-non-english-pdf-terms]] and [[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]].

## Dependencies

- Depends on: [[issues/delivery-glossary-terminology-governance/06-enforce-delivery-glossary-in-review-roles]]
- Blocks: none

## User Stories Covered

1-20

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/`
- Test fixtures under the established acceptance test location
- Documentation examples if the workflow has example output packages

## Acceptance Criteria

- [x] A fixture where `grief` uses `chinese_primary_only` plus `delivery_glossary_only` passes when body text uses a bounded Chinese expression.
- [x] A fixture where body text says the equivalent of "本节讨论的 grief 是" fails for the same glossary entry.
- [x] A fixture where `capability overhang` uses `chinese_with_english_parenthetical` passes when body text preserves the English source label in the expected place.
- [x] A fixture where `HTML mockup` is included only when used as a method concept, not when it only means an HTML file.
- [x] A fixture proves product names are excluded unless they define a new core concept.
- [x] A fixture proves no reader-facing PDF glossary appendix is required by default.
- [x] Existing final-delivery acceptance fixtures still pass.

## Execution Log

- 2026-07-07: Created from [[prd/delivery-glossary-terminology-governance]].
- 2026-07-08: Added end-to-end glossary governance fixtures covering `grief`, `capability overhang`, `HTML mockup`, product-name boundaries, no default appendix, manifest inclusion, and Acceptance Report pass/fail behavior. Independent verification passed the new fixture test, acceptance report, glossary validator, review-role, acceptance-criteria, YouTube workflow, Bilibili workflow, and diff checks.

## Comments

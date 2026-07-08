---
type: issue
status: ready-for-agent
feature: "[[prd/delivery-glossary-terminology-governance]]"
depends_on:
  - "[[issues/delivery-glossary-terminology-governance/01-establish-delivery-glossary-schema-and-validation-contract]]"
blocks:
  - "[[issues/delivery-glossary-terminology-governance/03-add-glossary-aware-acceptance-criterion]]"
  - "[[issues/delivery-glossary-terminology-governance/04-integrate-delivery-glossary-into-youtube-render-workflow]]"
  - "[[issues/delivery-glossary-terminology-governance/05-integrate-delivery-glossary-into-bilibili-render-workflow]]"
related_adrs:
  - "[[adr/0005-use-delivery-glossary-for-non-english-pdf-terms]]"
  - "[[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]]"
owner: unassigned
created: 2026-07-07
updated: 2026-07-07
tags:
  - issue
  - status/ready-for-agent
---

# 02 - Thread Delivery Glossary through final artifact manifest

Status: ready-for-agent

## Goal

Make `delivery_glossary.json` an allowed final-delivery artifact for non-English teaching PDFs so Acceptance Reviewer context can include the terminology contract without violating the review boundary.

## What to build

Extend the final artifact manifest flow so a non-English teaching PDF can include `review/acceptance/delivery_glossary.json` as a final contract artifact. The manifest and report validation should recognize that a reviewer used the glossary as allowed context and should fail closed when a glossary-aware report claims review against a glossary that was absent, stale, or outside the manifest.

This slice should preserve the existing rule that `acceptance_report.json` remains the only machine-readable delivery decision source.

## Context

This issue implements manifest integration from [[prd/delivery-glossary-terminology-governance]], building on the glossary schema established by [[issues/delivery-glossary-terminology-governance/01-establish-delivery-glossary-schema-and-validation-contract]].

The related acceptance contract decision is [[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]].

## Dependencies

- Depends on: [[issues/delivery-glossary-terminology-governance/01-establish-delivery-glossary-schema-and-validation-contract]]
- Blocks: [[issues/delivery-glossary-terminology-governance/03-add-glossary-aware-acceptance-criterion]], [[issues/delivery-glossary-terminology-governance/04-integrate-delivery-glossary-into-youtube-render-workflow]], [[issues/delivery-glossary-terminology-governance/05-integrate-delivery-glossary-into-bilibili-render-workflow]]

## User Stories Covered

11, 13, 16

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_report.py`
- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `review/acceptance/allowed_artifacts_manifest.json` generation path
- Tests or fixtures for manifest and report validation

## Acceptance Criteria

- [ ] Manifest generation can include `review/acceptance/delivery_glossary.json` for non-English teaching PDF outputs.
- [ ] Manifest validation fingerprints the glossary as an in-scope final artifact.
- [ ] Report validation accepts reviewer context that includes the glossary only when the glossary appears in the manifest.
- [ ] Report validation rejects glossary-aware reports when the glossary is missing, stale, outside the video output directory, or absent from the allowed artifacts manifest.
- [ ] Existing final PDF and TeX artifact validation behavior remains unchanged.

## Execution Log

- 2026-07-07: Created from [[prd/delivery-glossary-terminology-governance]].

## Comments

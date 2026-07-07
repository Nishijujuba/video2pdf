---
type: issue
status: ready-for-agent
feature: "[[prd/delivery-glossary-terminology-governance]]"
depends_on:
  - "[[issues/delivery-glossary-terminology-governance/03-add-glossary-aware-acceptance-criterion]]"
  - "[[issues/delivery-glossary-terminology-governance/04-integrate-delivery-glossary-into-youtube-render-workflow]]"
  - "[[issues/delivery-glossary-terminology-governance/05-integrate-delivery-glossary-into-bilibili-render-workflow]]"
blocks:
  - "[[issues/delivery-glossary-terminology-governance/07-add-end-to-end-glossary-governance-fixtures]]"
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

# 06 - Enforce Delivery Glossary in review roles

Status: ready-for-agent

## Goal

Make the Consistency agent and final Acceptance Reviewer enforce the Delivery Glossary instead of treating English terminology style as an informal preference.

## What to build

Update review-role instructions so glossary checks are explicit and evidence-bearing. The Consistency agent should check TeX files for first-use wording, later-use stability, source-English preservation location, and chapter-to-chapter terminology consistency. The Acceptance Reviewer should read the glossary only through the allowed artifacts manifest and check final PDF body text against each term's declared strategy.

This slice should preserve reviewer boundaries: Acceptance Reviewer may use the final artifacts, criteria, manifest, glossary, and rendered page evidence, but must not read generation notes or intermediate drafts.

## Context

This issue implements review-role enforcement from [[prd/delivery-glossary-terminology-governance]] after glossary-aware acceptance criteria and both render workflows are ready.

The reviewer boundary follows [[adr/0002-use-json-acceptance-contract-for-final-delivery-quality]] and [[adr/0005-use-delivery-glossary-for-non-english-pdf-terms]].

## Dependencies

- Depends on: [[issues/delivery-glossary-terminology-governance/03-add-glossary-aware-acceptance-criterion]], [[issues/delivery-glossary-terminology-governance/04-integrate-delivery-glossary-into-youtube-render-workflow]], [[issues/delivery-glossary-terminology-governance/05-integrate-delivery-glossary-into-bilibili-render-workflow]]
- Blocks: [[issues/delivery-glossary-terminology-governance/07-add-end-to-end-glossary-governance-fixtures]]

## User Stories Covered

10, 11, 12, 13, 14

## Expected Touched Paths

- `AGENTS.md`
- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/youtube-render-pdf/SKILL.md`
- `.agents/skills/bilibili-render-pdf/SKILL.md`
- Acceptance report examples or fixtures if present

## Acceptance Criteria

- [ ] Consistency agent instructions require checking glossary first-use, later-use, and strategy stability.
- [ ] Acceptance Reviewer instructions list `delivery_glossary.json` as allowed input only when it appears in the manifest.
- [ ] Acceptance Reviewer instructions require checking `body_display_strategy` and `where_to_preserve_english`.
- [ ] Reviewer instructions include the `grief` case: `chinese_primary_only` plus `delivery_glossary_only` means body text should not make `grief` the sentence subject.
- [ ] Reviewer boundary remains read-only and final-artifact-only.

## Execution Log

- 2026-07-07: Created from [[prd/delivery-glossary-terminology-governance]].

## Comments

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
owner: unassigned
created: 2026-07-07
updated: 2026-07-07
tags:
  - issue
  - status/done
---

# 05 - Integrate Delivery Glossary into Bilibili render workflow

Status: done

## Goal

Make the Bilibili render workflow produce and preserve a Delivery Glossary for non-English teaching PDFs.

## What to build

Update the Bilibili PDF workflow so the Outline agent creates the initial global glossary, Writer agents report `new_term_candidates`, and the coordinator merges accepted candidates into `review/acceptance/delivery_glossary.json` before consistency and final acceptance. The workflow should mirror the YouTube behavior while respecting any Bilibili-specific source acquisition and subtitle handling rules.

This slice should make a Bilibili job capable of carrying the terminology contract through outline, section writing, consistency review, final manifest, and acceptance.

## Context

This issue implements the Bilibili workflow integration from [[prd/delivery-glossary-terminology-governance]] after [[issues/delivery-glossary-terminology-governance/02-thread-delivery-glossary-through-final-artifact-manifest]] establishes glossary manifest support.

The governing ADR is [[adr/0005-use-delivery-glossary-for-non-english-pdf-terms]].

## Dependencies

- Depends on: [[issues/delivery-glossary-terminology-governance/02-thread-delivery-glossary-through-final-artifact-manifest]]
- Blocks: [[issues/delivery-glossary-terminology-governance/06-enforce-delivery-glossary-in-review-roles]]

## User Stories Covered

5, 6, 7, 8, 9, 17, 18, 19

## Expected Touched Paths

- `.agents/skills/bilibili-render-pdf/SKILL.md`
- `AGENTS.md`
- Bilibili workflow examples or fixtures if they exist

## Acceptance Criteria

- [ ] Bilibili workflow instructions require the Outline agent to create a global Delivery Glossary for non-English teaching PDFs.
- [ ] Bilibili Writer agent instructions require `new_term_candidates` in handoff notes, or `new_term_candidates: none`.
- [ ] Bilibili workflow coordinator instructions merge accepted candidates into `review/acceptance/delivery_glossary.json`.
- [ ] Bilibili workflow final manifest instructions include the glossary when applicable.
- [ ] Bilibili workflow instructions state that the glossary is not a PDF appendix unless explicitly requested.
- [ ] English-learning and IELTS-like Bilibili content keeps its existing English-primary behavior.

## Execution Log

- 2026-07-07: Created from [[prd/delivery-glossary-terminology-governance]].
- 2026-07-08: Added Bilibili delivery glossary workflow instructions covering outline glossary creation, writer `new_term_candidates`, coordinator merge and validation, `--include-delivery-glossary` final manifest behavior, no default PDF appendix, and English-learning/IELTS-like carveout. Independent verification passed Bilibili glossary workflow and glossary validator tests.

## Comments

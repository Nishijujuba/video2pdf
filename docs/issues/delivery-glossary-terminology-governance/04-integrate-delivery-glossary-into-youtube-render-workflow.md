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

# 04 - Integrate Delivery Glossary into YouTube render workflow

Status: done

## Goal

Make the YouTube render workflow produce and preserve a Delivery Glossary for non-English teaching PDFs.

## What to build

Update the YouTube PDF workflow so the Outline agent creates the initial global glossary, Writer agents report `new_term_candidates`, and the coordinator merges accepted candidates into `review/acceptance/delivery_glossary.json` before consistency and final acceptance. The workflow should preserve the rule that the glossary is not a default PDF appendix.

This slice should make a YouTube job capable of carrying the terminology contract through source acquisition, outline, section writing, consistency review, final manifest, and acceptance.

## Context

This issue implements the YouTube workflow integration from [[prd/delivery-glossary-terminology-governance]] after [[issues/delivery-glossary-terminology-governance/02-thread-delivery-glossary-through-final-artifact-manifest]] establishes glossary manifest support.

The governing ADR is [[adr/0005-use-delivery-glossary-for-non-english-pdf-terms]].

## Dependencies

- Depends on: [[issues/delivery-glossary-terminology-governance/02-thread-delivery-glossary-through-final-artifact-manifest]]
- Blocks: [[issues/delivery-glossary-terminology-governance/06-enforce-delivery-glossary-in-review-roles]]

## User Stories Covered

5, 6, 7, 8, 9, 17, 18, 19

## Expected Touched Paths

- `.agents/skills/youtube-render-pdf/SKILL.md`
- `AGENTS.md`
- YouTube workflow examples or fixtures if they exist

## Acceptance Criteria

- [ ] YouTube workflow instructions require the Outline agent to create a global Delivery Glossary for non-English teaching PDFs.
- [ ] YouTube Writer agent instructions require `new_term_candidates` in handoff notes, or `new_term_candidates: none`.
- [ ] YouTube workflow coordinator instructions merge accepted candidates into `review/acceptance/delivery_glossary.json`.
- [ ] YouTube workflow final manifest instructions include the glossary when applicable.
- [ ] YouTube workflow instructions state that the glossary is not a PDF appendix unless explicitly requested.
- [ ] English-learning and IELTS YouTube content keeps its existing English-primary behavior.

## Execution Log

- 2026-07-07: Created from [[prd/delivery-glossary-terminology-governance]].
- 2026-07-08: Added YouTube delivery glossary workflow instructions covering outline glossary creation, writer `new_term_candidates`, coordinator merge and validation, `--include-delivery-glossary` final manifest behavior, no default PDF appendix, and English-learning/IELTS carveout. Independent verification passed YouTube glossary workflow and glossary validator tests.

## Comments

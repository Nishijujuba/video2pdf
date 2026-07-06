---
type: issue
status: done
feature: "[[prd/latex-compile-guard]]"
depends_on:
  - "[[issues/latex-compile-guard/02-add-final-compile-provenance-report]]"
  - "[[issues/latex-compile-guard/03-enforce-compile-provenance-in-delivery-guard]]"
  - "[[issues/latex-compile-guard/04-block-unsafe-latex-shell-calls-with-pretooluse]]"
blocks:
  - "[[issues/latex-compile-guard/06-add-end-to-end-guard-fixture-verification]]"
related_adrs:
  - "[[adr/0003-use-guarded-latex-compile-wrapper]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-06
tags:
  - issue
  - status/done
---

# 05 - Integrate guarded compile contract into render skills

Status: done

## Goal

Make the Bilibili and YouTube render workflows describe and require the guarded compile path as part of final PDF generation.

## What to build

Update the render workflow instructions so agents compile through the guarded wrapper, use `quick` mode for temporary diagnosis, use `final` mode for delivery, preserve compile reports in the expected locations, and run Final Delivery Guard with compile provenance enforced before delivery.

This slice should remove stale workflow wording that directs agents to raw `xelatex` and align Bilibili, YouTube, project instructions, and acceptance workflow language around the same LaTeX Compile Guard contract.

## Context

This issue depends on the final report, delivery guard enforcement, and PreToolUse blocking slices. It closes the documentation and workflow integration gap from [[prd/latex-compile-guard]] and preserves the final delivery boundaries from [[prd/final-delivery-guard-and-bounded-repair]].

## Dependencies

- Depends on: [[issues/latex-compile-guard/02-add-final-compile-provenance-report]], [[issues/latex-compile-guard/03-enforce-compile-provenance-in-delivery-guard]], [[issues/latex-compile-guard/04-block-unsafe-latex-shell-calls-with-pretooluse]]
- Blocks: [[issues/latex-compile-guard/06-add-end-to-end-guard-fixture-verification]]

## User Stories Covered

30, 31, 35

## Expected Touched Paths

- `.agents/skills/bilibili-render-pdf/SKILL.md`
- `.agents/skills/youtube-render-pdf/SKILL.md`
- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `AGENTS.md`
- `CLAUDE.md`
- Skill contract tests

## Acceptance Tests

- Bilibili render instructions require the guarded wrapper for final compilation.
- YouTube render instructions require the guarded wrapper for final compilation.
- Both render skills document `quick` mode as the temporary compile path.
- Both render skills document `final` mode as the delivery compile path.
- Both render skills mention `review\latex\compile_report.json` as final compile provenance.
- Skill contract tests fail if raw `xelatex` remains the recommended final compile command.
- Project instructions preserve the rule that the Stop hook must not launch LaTeX compilation.
- Final Delivery Acceptance documentation distinguishes compile provenance from Acceptance Report quality judgment.

## Acceptance Criteria

- [x] Bilibili and YouTube render workflows require the LaTeX Compile Guard.
- [x] Project and skill docs consistently separate compile provenance from acceptance quality judgment.
- [x] Raw direct engine calls are no longer presented as the normal final compile path.
- [x] Contract tests cover the updated workflow wording.

## Execution Log

- 2026-07-05: Created from [[prd/latex-compile-guard]].
- 2026-07-06: Updated `AGENTS.md`, `CLAUDE.md`, Bilibili/YouTube render skill docs, and final-delivery-acceptance docs to require guarded quick/final compile paths and separate compile provenance from acceptance quality judgment; verified with `test_skill_contracts.py`.

## Comments

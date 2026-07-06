---
type: issue
status: done
feature: "[[prd/latex-compile-guard]]"
depends_on:
  - "[[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]]"
blocks: []
related_adrs:
  - "[[adr/0003-use-guarded-latex-compile-wrapper]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-06
tags:
  - issue
  - status/done
---

# 06 - Add end-to-end guard fixture verification

Status: done

## Goal

Add final fixture verification that proves the wrapper, PreToolUse guard, delivery guard, render skills, and project hook configuration agree on the LaTeX Compile Guard contract.

## What to build

Create an end-to-end fixture verification path for the compile guard feature. A completed slice should validate a representative successful final compile provenance path, representative blocked shell commands, representative stale compile report failures, and the documented render-skill workflow requirements.

The verification should stay fixture-based and should not require full Bilibili or YouTube generation.

## Context

This issue closes [[prd/latex-compile-guard]] after [[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]] integrates the contract into user-facing workflows.

## Dependencies

- Depends on: [[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]]
- Blocks: none

## User Stories Covered

32, 33, 34, 35

## Expected Touched Paths

- End-to-end fixture tests for guarded compilation
- Hook decision fixture tests
- Delivery guard fixture tests
- Skill contract tests
- Dependency view generation output when issue views are refreshed

## Acceptance Tests

- A fixture new video PDF target with a fresh passing final compile report and passing acceptance evidence passes delivery guard.
- The same fixture blocks after the final PDF fingerprint changes.
- The same fixture blocks after the main TeX fingerprint changes.
- The same fixture blocks when the final compile report lacks guarded wrapper provenance.
- The same fixture blocks when the wrapper script fingerprint is stale.
- A fixture quick-mode report cannot satisfy final delivery.
- A fixture direct `xelatex` command is blocked by the PreToolUse guard.
- A fixture guarded wrapper command is allowed by the PreToolUse guard.
- Skill contract tests prove Bilibili and YouTube instructions use the guarded compile path.
- `.codex` hook configuration parses successfully and includes both the existing Stop guard and the new PreToolUse guard.
- Issue dependency metadata for the LaTeX Compile Guard issue set validates successfully.

## Acceptance Criteria

- [x] Fixture verification covers wrapper, PreToolUse, delivery guard, and skill contract behavior.
- [x] The verification path does not depend on downloading videos or calling models.
- [x] Stale artifact provenance or stale wrapper provenance cannot pass the fixture delivery guard.
- [x] Hook configuration and issue dependency metadata validate cleanly.

## Execution Log

- 2026-07-05: Created from [[prd/latex-compile-guard]].
- 2026-07-06: Added `test_latex_compile_guard_e2e.py` to compile a real one-page PDF through final mode, run delivery guard against the generated compile report, assert stale PDF/TeX and quick-mode blocks, verify PreToolUse decisions, run skill contract checks, parse hook configuration, and validate the latex-compile-guard dependency view.
- 2026-07-06: Re-reviewed the E2E contract and strengthened producer provenance validation so the passing fixture depends on a wrapper-generated final compile report.

## Comments

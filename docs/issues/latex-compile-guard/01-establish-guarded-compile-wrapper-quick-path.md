---
type: issue
status: ready-for-agent
feature: "[[prd/latex-compile-guard]]"
depends_on: []
blocks:
  - "[[issues/latex-compile-guard/02-add-final-compile-provenance-report]]"
  - "[[issues/latex-compile-guard/04-block-unsafe-latex-shell-calls-with-pretooluse]]"
related_adrs:
  - "[[adr/0003-use-guarded-latex-compile-wrapper]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/ready-for-agent
---

# 01 - Establish guarded compile wrapper quick path

Status: ready-for-agent

## Goal

Create the first usable LaTeX Compile Guard path by making temporary compilation available through a controlled wrapper instead of raw engine commands.

## What to build

Build the guarded wrapper's `quick` mode as the first tracer bullet. A completed slice should let an agent run a temporary compile with semantic options, keep all disposable output under the Video Output Directory's `待删除` area, enforce total and idle timeout settings, and write a diagnostic compile report for the run.

This slice should preserve the ability to debug LaTeX and layout issues while proving that temporary compilation no longer requires direct `xelatex` or arbitrary output-directory flags.

## Context

This issue starts [[prd/latex-compile-guard]] and implements the boundary chosen in [[adr/0003-use-guarded-latex-compile-wrapper]]. Existing ASCII staging behavior is useful prior art, yet the new quick path must also own timeout behavior, disposable output placement, and report writing.

## Dependencies

- Depends on: none
- Blocks: [[issues/latex-compile-guard/02-add-final-compile-provenance-report]], [[issues/latex-compile-guard/04-block-unsafe-latex-shell-calls-with-pretooluse]]

## User Stories Covered

1, 5, 6, 8, 9, 10, 11, 12, 13, 14, 16, 32

## Expected Touched Paths

- `.agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py` or a new shared guarded compile script
- `.agents/skills/bilibili-render-pdf/scripts/test_compile_latex_ascii.py` or colocated guarded wrapper tests
- `CONTEXT.md` only if glossary terms need correction

## Acceptance Tests

- A quick compile command accepts a TeX path and semantic options without requiring a caller-provided raw output directory.
- Quick mode creates its build run under the Video Output Directory's `待删除` area.
- Quick mode writes a run-local `compile_report.json` under the disposable build run.
- The quick compile report records mode, status, source TeX, engine, run count, timeout settings, log path, build directory, start time, finish time, and failure reason when the run fails.
- A fake or tiny engine fixture proves total timeout behavior without requiring a full MiKTeX run.
- A fake or tiny engine fixture proves idle timeout behavior without requiring a full MiKTeX run.
- Invalid TeX paths, invalid modes, invalid timeout values, and missing engine paths fail with clear diagnostics.

## Acceptance Criteria

- [ ] Temporary compilation is available through the guarded wrapper's `quick` mode.
- [ ] Quick mode never writes delivery evidence under `review\latex`.
- [ ] Quick mode keeps disposable compile outputs inside the video output boundary.
- [ ] Tests verify quick-mode output placement, report content, timeout behavior, and failure diagnostics.

## Execution Log

- 2026-07-05: Created from [[prd/latex-compile-guard]].

## Comments

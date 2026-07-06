---
type: issue
status: done
feature: "[[prd/latex-compile-guard]]"
depends_on:
  - "[[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]]"
blocks:
  - "[[issues/latex-compile-guard/03-enforce-compile-provenance-in-delivery-guard]]"
  - "[[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]]"
related_adrs:
  - "[[adr/0003-use-guarded-latex-compile-wrapper]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-06
tags:
  - issue
  - status/done
---

# 02 - Add final compile provenance report

Status: done

## Goal

Extend the guarded wrapper with a final compilation path that produces durable LaTeX Compile Report evidence for the PDF intended for delivery.

## What to build

Add `final` mode to the guarded wrapper. A completed slice should compile the delivery TeX source through the same controlled process boundary, copy or expose the final PDF at the durable output location, and write the latest final compile provenance report under the video output directory's LaTeX review area.

The report should bind the current main TeX source and final PDF through fingerprints so later delivery checks can distinguish fresh provenance from stale or unrelated compile evidence.

## Context

This issue depends on the quick path from [[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]]. It implements the final compile evidence required by [[prd/latex-compile-guard]] while preserving [[adr/0003-use-guarded-latex-compile-wrapper]].

## Dependencies

- Depends on: [[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]]
- Blocks: [[issues/latex-compile-guard/03-enforce-compile-provenance-in-delivery-guard]], [[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]]

## User Stories Covered

7, 15, 18, 19

## Expected Touched Paths

- Guarded compile wrapper script
- Guarded compile wrapper tests
- `review\latex\compile_report.json` fixture expectations

## Acceptance Tests

- Final mode writes the latest final compile report to `review\latex\compile_report.json`.
- Final mode reports `mode: "final"` and `status: "passed"` only after the final PDF exists.
- Final mode records the resolved main TeX path and final PDF path.
- Final mode records current fingerprints for the main TeX and final PDF.
- Final mode records guarded wrapper producer, contract, mode, wrapper script path/fingerprint, and wrapper argv.
- Final mode records log paths and build directory paths that remain inside the video output boundary.
- A changed PDF or changed TeX fixture can be detected as stale by comparing the report fingerprints.
- Failed final compilation writes a failed report with an actionable failure reason and does not claim a passing delivery provenance state.

## Acceptance Criteria

- [x] Final mode produces a durable LaTeX Compile Report under `review\latex`.
- [x] The report records enough artifact and wrapper provenance for delivery guard freshness checks.
- [x] Failed final compiles produce clear failed evidence without a false pass.
- [x] Tests verify final-mode success, failure, fingerprint recording, and wrapper provenance recording.

## Execution Log

- 2026-07-05: Created from [[prd/latex-compile-guard]].
- 2026-07-06: Added guarded `final` mode, durable `review\latex\compile_report.json`, source skill metadata, current TeX/PDF fingerprints, stale preexisting PDF rejection, and final-mode tests in `test_compile_latex_ascii.py`.
- 2026-07-06: Strengthened compile report provenance with producer, producer contract, producer mode, guarded wrapper path, wrapper script fingerprint, and wrapper argv fields.

## Comments

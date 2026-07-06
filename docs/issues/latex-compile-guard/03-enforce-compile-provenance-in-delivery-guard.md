---
type: issue
status: ready-for-agent
feature: "[[prd/latex-compile-guard]]"
depends_on:
  - "[[issues/latex-compile-guard/02-add-final-compile-provenance-report]]"
blocks:
  - "[[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]]"
related_adrs:
  - "[[adr/0003-use-guarded-latex-compile-wrapper]]"
owner: unassigned
created: 2026-07-05
updated: 2026-07-05
tags:
  - issue
  - status/ready-for-agent
---

# 03 - Enforce compile provenance in delivery guard

Status: ready-for-agent

## Goal

Make Final Delivery Guard reject newly generated video PDFs unless their final PDF is backed by a fresh passing final LaTeX Compile Report.

## What to build

Extend the delivery guard's mechanical check so new video PDF targets require final compile provenance. A completed slice should make the guard validate that the compile report exists, was produced in `final` mode, passed, resolves to the delivery target's main TeX and final PDF, and still matches current artifact fingerprints.

This slice should preserve the existing acceptance boundary: compile provenance proves controlled generation, while `acceptance_report.json` remains the only machine-readable quality decision source.

## Context

This issue depends on final compile reports from [[issues/latex-compile-guard/02-add-final-compile-provenance-report]]. It extends the guard established by [[prd/final-delivery-guard-and-bounded-repair]] without changing the Acceptance Reviewer role from [[prd/final-delivery-acceptance-gate]].

## Dependencies

- Depends on: [[issues/latex-compile-guard/02-add-final-compile-provenance-report]]
- Blocks: [[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]]

## User Stories Covered

17, 18, 19, 20, 21, 22, 23, 24, 34

## Expected Touched Paths

- `.agents/skills/final-delivery-acceptance/scripts/delivery_guard.py`
- `.agents/skills/final-delivery-acceptance/scripts/test_delivery_guard.py`
- Final-delivery skill documentation or contract tests when needed

## Acceptance Tests

- A new video PDF target with no final compile report blocks delivery.
- A quick-mode compile report blocks delivery.
- A failed final compile report blocks delivery.
- A malformed final compile report blocks delivery.
- A final compile report for the wrong PDF blocks delivery.
- A final compile report for the wrong TeX source blocks delivery.
- A stale report whose PDF fingerprint no longer matches blocks delivery.
- A stale report whose TeX fingerprint no longer matches blocks delivery.
- A fresh passing final compile report allows the existing acceptance freshness checks to proceed.
- An explicitly legacy existing-PDF target can skip compile provenance only when no recompilation is claimed.
- A legacy repair workflow that recompiles must satisfy the same final compile report requirement as new video PDFs.

## Acceptance Criteria

- [ ] `delivery_guard.py check` enforces final compile provenance for new video PDFs.
- [ ] Compile provenance failures have specific blocking messages.
- [ ] Legacy external PDF behavior stays explicit and bounded.
- [ ] Tests cover missing, failed, stale, wrong-artifact, quick-mode, and legacy cases.

## Execution Log

- 2026-07-05: Created from [[prd/latex-compile-guard]].

## Comments

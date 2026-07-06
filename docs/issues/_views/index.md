---
generated_at: 2026-07-06T02:52:57Z
source_feature_count: 6
source_issue_count: 37
source_issue_fingerprint: 9529e91c7b00dccb7b04320a8641c137b9c5645116e19b2d2b621da730be265c
---

# Issue Dependency Index

## final-delivery-acceptance-gate

- View: [[issues/_views/final-delivery-acceptance-gate-dependencies]]
- Issue count: 6
- Status distribution: done=6
- Root issues: [[issues/final-delivery-acceptance-gate/01-validate-acceptance-criteria-and-report-contracts]]
- Currently executable: None
- Status-blocked: None
- Dependency-blocked: None
- Consistency errors: None

## final-delivery-guard-and-bounded-repair

- View: [[issues/_views/final-delivery-guard-and-bounded-repair-dependencies]]
- Issue count: 6
- Status distribution: done=6
- Root issues: [[issues/final-delivery-guard-and-bounded-repair/01-establish-delivery-target-contracts]]
- Currently executable: None
- Status-blocked: None
- Dependency-blocked: None
- Consistency errors: None

## issue-dependency-views

- View: [[issues/_views/issue-dependency-views-dependencies]]
- Issue count: 6
- Status distribution: done=6
- Root issues: [[issues/issue-dependency-views/01-define-issue-metadata-model-and-fingerprint]]
- Currently executable: None
- Status-blocked: None
- Dependency-blocked: None
- Consistency errors: None

## latex-compile-guard

- View: [[issues/_views/latex-compile-guard-dependencies]]
- Issue count: 6
- Status distribution: ready-for-agent=6
- Root issues: [[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]]
- Currently executable: [[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]]
- Status-blocked: None
- Dependency-blocked: [[issues/latex-compile-guard/02-add-final-compile-provenance-report]] waits on [[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]]; [[issues/latex-compile-guard/03-enforce-compile-provenance-in-delivery-guard]] waits on [[issues/latex-compile-guard/02-add-final-compile-provenance-report]]; [[issues/latex-compile-guard/04-block-unsafe-latex-shell-calls-with-pretooluse]] waits on [[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]]; [[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]] waits on [[issues/latex-compile-guard/02-add-final-compile-provenance-report]], [[issues/latex-compile-guard/03-enforce-compile-provenance-in-delivery-guard]], [[issues/latex-compile-guard/04-block-unsafe-latex-shell-calls-with-pretooluse]]; [[issues/latex-compile-guard/06-add-end-to-end-guard-fixture-verification]] waits on [[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]]
- Consistency errors: None

## pyramid-principle-codex-exec-gate

- View: [[issues/_views/pyramid-principle-codex-exec-gate-dependencies]]
- Issue count: 6
- Status distribution: done=6
- Root issues: [[issues/pyramid-principle-codex-exec-gate/01-generalize-gate-report-contract]]
- Currently executable: None
- Status-blocked: None
- Dependency-blocked: None
- Consistency errors: None

## session-scoped-final-delivery-guard

- View: [[issues/_views/session-scoped-final-delivery-guard-dependencies]]
- Issue count: 7
- Status distribution: ready-for-agent=7
- Root issues: [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]]
- Currently executable: [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]]
- Status-blocked: None
- Dependency-blocked: [[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]] waits on [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]]; [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]] waits on [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]]; [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]] waits on [[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]], [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]]; [[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]] waits on [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]], [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]]; [[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]] waits on [[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]], [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]], [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]], [[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]]; [[issues/session-scoped-final-delivery-guard/07-add-concurrent-session-regression-fixtures]] waits on [[issues/session-scoped-final-delivery-guard/01-resolve-hook-session-targets-from-official-hook-input]], [[issues/session-scoped-final-delivery-guard/02-validate-session-scoped-delivery-targets-end-to-end]], [[issues/session-scoped-final-delivery-guard/03-add-delivery-task-index-ownership-and-handoff-checks]], [[issues/session-scoped-final-delivery-guard/04-convert-old-pdf-prepare-and-failed-attempt-lifecycle]], [[issues/session-scoped-final-delivery-guard/05-archive-delivered-session-targets-and-update-task-index]], [[issues/session-scoped-final-delivery-guard/06-update-render-skills-and-project-instructions]]
- Consistency errors: None

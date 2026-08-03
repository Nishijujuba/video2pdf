# Activate the global Acceptance Report v2 gate

## Context

Delivery Quality Slices A-D implemented the canonical rule catalog,
precompile semantic assurance, final evidence reconciliation, Acceptance Report
v2 materialization, and bounded repair behind target-only authority. Keeping
Acceptance Report v1 active after those contracts were complete would preserve
two delivery meanings during the staged Bilibili and YouTube platform
cutovers. Legacy directories also require an honest input contract without a
synthetic Workflow Kernel Run Record.

## Decision

Activate one repository-wide final-quality gate with status
`active_global_gate`.

- Acceptance Report v2 is the sole machine-readable semantic delivery
  decision for Kernel and Legacy inputs.
- A Kernel input retains its real Run Record, Control Store authority, current
  Artifact Generations, three committed precompile owner reports, and one
  independent Visual Quality Judgment Patch.
- A Legacy directory enters through a fresh, explicitly named,
  Run-record-free Legacy Acceptance Input Set. It uses the same materializer,
  report contract, decision rules, and active Delivery Guard.
- Global Gate publication is a fenced, idempotent SQLite compare-and-swap bound
  to a schema-valid Exit Evidence Manifest. Reconciliation can finish only the
  prepared publication whose evidence bytes still match.
- The active Delivery Guard preserves manifest, compile-provenance, path,
  rendered-page, and artifact-fingerprint checks. It additionally requires the
  current committed Acceptance execution, Judgment Patch, report-publication
  intent, and Global Gate JSON and SQLite authority.
- Acceptance Report v1, per-run fallback, compatibility translation, dual
  authority, unsupported identities, unresolved Contract Gaps, and synthetic
  Legacy Run Records fail closed.
- Bilibili and YouTube platform coordination remains `active_legacy`. No
  component receives `active_kernel` status through this decision.

The atomic publication covers schemas, providers, validators, hooks, skills,
project instructions, `.agents` and `.claude` mirrors, tests, context status,
the decision map, and cutover evidence. `workflow-policy-check` verifies those
members in the fixed order: authority, atomic members, mirrors, policy status,
then behavioral results.

## Consequences

The repository has one final-quality authority while retaining staged platform
migration. Any artifact, report, Patch, publication, mirror, policy member, or
Global Gate authority drift blocks delivery. Historical Slice 7-10 evidence
remains byte-stable and continues to prove target-only construction; the Issue
#43 Exit Evidence Manifest separately proves runtime activation.

This decision implements ADR 0051, amends the Reviewer topology in ADR 0056,
and activates the target-only provider defined by ADR 0063.

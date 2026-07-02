---
type: issue
status: done
feature: "[[prd/pyramid-principle-codex-exec-gate]]"
depends_on: []
blocks:
  - "[[issues/pyramid-principle-codex-exec-gate/02-build-codex-exec-evaluator-wrapper]]"
  - "[[issues/pyramid-principle-codex-exec-gate/03-enforce-gate-outcomes-and-waivers]]"
  - "[[issues/pyramid-principle-codex-exec-gate/04-maintain-pyramid-review-directory-evidence]]"
related_adrs:
  - "[[adr/0001-use-codex-exec-for-pyramid-semantic-evaluation]]"
owner: unassigned
created: 2026-06-30
updated: 2026-06-30
tags:
  - issue
  - status/done
---

# 01 - Generalize Gate Report contract

Status: done

## Goal

Turn the current Pyramid Gate report contract into a general-purpose text evaluation contract that uses `artifact_type`, `context_label`, and audit metadata instead of workflow-specific stage fields.

## Context

This issue implements the contract foundation from [[prd/pyramid-principle-codex-exec-gate]] and must preserve the backend decision in [[adr/0001-use-codex-exec-for-pyramid-semantic-evaluation]].

The key domain concepts are defined in root `CONTEXT.md`: Pyramid Principle Text Standard, Gate Report, Pyramid Gate, and Waiver.

## Dependencies

- Depends on: none
- Blocks: [[issues/pyramid-principle-codex-exec-gate/02-build-codex-exec-evaluator-wrapper]], [[issues/pyramid-principle-codex-exec-gate/03-enforce-gate-outcomes-and-waivers]], [[issues/pyramid-principle-codex-exec-gate/04-maintain-pyramid-review-directory-evidence]]

## User Stories Covered

2, 4, 10, 11, 12, 16, 20, 21, 22, 26, 29

## Acceptance Criteria

- [x] The report schema identifies reviewed content with `artifact_type` and `context_label`, and the old workflow-specific `stage` field is removed from the general evaluator contract.
- [x] The schema includes audit metadata for standard name, backend, prompt version, input hash, input size, maximum input size, large-input approval state, evaluation context, and generation time.
- [x] The semantic result fields allow `pass`, `needs_revision`, and `blocked`; waiver state is represented only through explicit waiver metadata owned by the wrapper or workflow.
- [x] Report examples cover at least one passing report and one revision or blocked report using the generalized fields.
- [x] Validation rejects missing required fields, extra fields, invalid scores, malformed dimensions, inconsistent waiver data, and malformed audit metadata.
- [x] Tests or validation fixtures demonstrate that a stale or mismatched input fingerprint can be detected by downstream gate checks.
- [x] Skill documentation names the Pyramid Principle Text Standard as the general standard and explains that Teaching-PDF context is supplied by video workflows.

## Execution Log

- 2026-06-30: Created from [[prd/pyramid-principle-codex-exec-gate]].
- 2026-06-30: RED: added `scripts/test_validate_report.py::test_accepts_generalized_gate_report`; it failed because the existing validator required `stage`.
- 2026-06-30: GREEN: migrated schema, examples, skill docs, and validator to `artifact_type`, `context_label`, `audit`, and explicit waiver metadata with statuses limited to `pass`, `needs_revision`, and `blocked`.
- 2026-06-30: RED: added downstream stale-fingerprint CLI coverage; it failed because `validate_report.py` did not accept `--input-file`.
- 2026-06-30: GREEN: added `--input-file` validation for `audit.input_sha256` and `audit.input_size_chars`; targeted validator tests pass.
- 2026-06-30: Verification: validator tests pass; JSON schema parses; pass example validates under `--enforce-gate`; needs-revision example validates without enforcement and blocks with `--enforce-gate`; `git diff --check` reports only Git line-ending warnings.
- 2026-06-30: Review fix: added RED coverage for over-permissive `pass` reports, score/dimension mismatches, and `pass` reports with required revisions; tightened validator errors and schema waiver invariants. Verification: `python -B .agents\skills\pyramid-principle-validate\scripts\test_validate_report.py` passed; `python -m json.tool .agents\skills\pyramid-principle-validate\references\report-schema.json` passed.
- 2026-06-30: Review fix: added RED coverage for non-finite JSON numbers such as `NaN`; validator now rejects non-finite `score` and dimension values. Verification: `python -B .agents\skills\pyramid-principle-validate\scripts\test_validate_report.py` passed.

## Comments

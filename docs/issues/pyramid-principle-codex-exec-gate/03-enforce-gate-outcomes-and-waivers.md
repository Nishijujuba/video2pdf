---
type: issue
status: done
feature: "[[prd/pyramid-principle-codex-exec-gate]]"
depends_on:
  - "[[issues/pyramid-principle-codex-exec-gate/01-generalize-gate-report-contract]]"
  - "[[issues/pyramid-principle-codex-exec-gate/02-build-codex-exec-evaluator-wrapper]]"
blocks:
  - "[[issues/pyramid-principle-codex-exec-gate/04-maintain-pyramid-review-directory-evidence]]"
  - "[[issues/pyramid-principle-codex-exec-gate/05-integrate-pyramid-gate-into-bilibili-workflow]]"
  - "[[issues/pyramid-principle-codex-exec-gate/06-integrate-pyramid-gate-into-youtube-workflow]]"
related_adrs:
  - "[[adr/0001-use-codex-exec-for-pyramid-semantic-evaluation]]"
owner: unassigned
created: 2026-06-30
updated: 2026-06-30
tags:
  - issue
  - status/done
---

# 03 - Enforce gate outcomes and explicit waivers

Status: done

## Goal

Make Pyramid Gate enforcement deterministic: `pass` allows continuation, `needs_revision` and `blocked` stop the workflow, and waiver continuation requires explicit user approval plus a reason.

## Context

This issue implements waiver authority and gate-stop behavior from [[prd/pyramid-principle-codex-exec-gate]]. It depends on the generalized Gate Report contract and the evaluator wrapper because enforcement must validate real report content.

The relevant domain concept is Waiver, defined in root `CONTEXT.md`.

## Dependencies

- Depends on: [[issues/pyramid-principle-codex-exec-gate/01-generalize-gate-report-contract]], [[issues/pyramid-principle-codex-exec-gate/02-build-codex-exec-evaluator-wrapper]]
- Blocks: [[issues/pyramid-principle-codex-exec-gate/04-maintain-pyramid-review-directory-evidence]], [[issues/pyramid-principle-codex-exec-gate/05-integrate-pyramid-gate-into-bilibili-workflow]], [[issues/pyramid-principle-codex-exec-gate/06-integrate-pyramid-gate-into-youtube-workflow]]

## User Stories Covered

17, 18, 19, 20, 21, 27, 29

## Acceptance Criteria

- [x] Gate validation succeeds for passing reports and fails for `needs_revision` or `blocked` reports when gate enforcement is enabled.
- [x] A semantic evaluator result cannot grant waiver status by itself.
- [x] Waiver continuation requires caller-provided approver and reason fields that are present in the final validated report.
- [x] Waived reports remain auditable by preserving the original semantic weakness, the approver, the reason, and the reviewed input fingerprint.
- [x] The CLI or helper exit codes clearly distinguish valid continuation, validation failure, gate-blocking status, and malformed waiver data.
- [x] Tests cover pass, needs-revision, blocked, semantic self-waiver attempt, missing waiver approver, missing waiver reason, and valid explicit waiver.

## Execution Log

- 2026-06-30: Created from [[prd/pyramid-principle-codex-exec-gate]].
- 2026-06-30: RED validator test added for explicit waiver continuation; observed `TypeError: validate_report() got an unexpected keyword argument 'allow_waiver'`.
- 2026-06-30: GREEN validator implementation added `allow_waiver`, `--allow-waiver`, typed gate and waiver failures, and exit codes `0` valid, `1` validation failure, `2` gate block, `3` malformed waiver.
- 2026-06-30: RED evaluator test added for caller-owned waiver metadata; observed `TypeError: evaluate_file() got an unexpected keyword argument 'waiver_approved_by'`.
- 2026-06-30: GREEN evaluator implementation added `--waiver-approved-by`, `--waiver-reason`, optional `--waiver-approved-at`, preserved original semantic status, and kept semantic self-waiver rejected.
- 2026-06-30: Verification passed: `python -B .agents/skills/pyramid-principle-validate/scripts/test_validate_report.py` and `python -B .agents/skills/pyramid-principle-validate/scripts/test_evaluate_pyramid_text.py`.
- 2026-06-30: Review fix: added RED coverage for waived failed reports without concrete preserved weakness; validator now requires `needs_revision` and `blocked` reports to include at least one finding and required revisions. Verification: targeted validator and evaluator tests passed.
- 2026-06-30: Final verification fix: updated validator CLI tests to decode subprocess output with replacement on Windows, preserving exit-code assertions while eliminating non-UTF-8 reader-thread trace noise.

## Comments

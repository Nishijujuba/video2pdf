---
type: issue
status: done
feature: "[[prd/pyramid-principle-codex-exec-gate]]"
depends_on:
  - "[[issues/pyramid-principle-codex-exec-gate/01-generalize-gate-report-contract]]"
  - "[[issues/pyramid-principle-codex-exec-gate/03-enforce-gate-outcomes-and-waivers]]"
blocks:
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

# 04 - Maintain Pyramid Review Directory evidence

Status: done

## Goal

Provide output-directory validation and human-readable evidence so a video output can prove that outline, section, and main Pyramid Checkpoints ran against the current artifacts.

## Context

This issue implements the Pyramid Review Directory evidence promised by [[prd/pyramid-principle-codex-exec-gate]]. It gives workflow integrations a stable checkpoint target before Bilibili and YouTube skills call the evaluator.

The relevant domain concepts are Pyramid Review Directory and Pyramid Checkpoint, defined in root `CONTEXT.md`.

## Dependencies

- Depends on: [[issues/pyramid-principle-codex-exec-gate/01-generalize-gate-report-contract]], [[issues/pyramid-principle-codex-exec-gate/03-enforce-gate-outcomes-and-waivers]]
- Blocks: [[issues/pyramid-principle-codex-exec-gate/05-integrate-pyramid-gate-into-bilibili-workflow]], [[issues/pyramid-principle-codex-exec-gate/06-integrate-pyramid-gate-into-youtube-workflow]]

## User Stories Covered

11, 12, 24, 27, 30

## Acceptance Criteria

- [x] The output-level gate checker requires Pyramid Review Directory evidence for the outline contract, each section draft, and the integrated main document.
- [x] The checker verifies report schema, gate status, waiver data, and input fingerprint freshness for each required report.
- [x] Missing reports, failed reports, stale reports, malformed reports, and missing waiver evidence block successful output-level validation.
- [x] A human-readable `summary.md` records checkpoint labels, report filenames, statuses, scores, required revisions, and waiver reasons.
- [x] The summary is maintained by workflow-facing helpers or documented workflow steps, while machine decisions still come from JSON reports.
- [x] Tests demonstrate output-level behavior for complete passing evidence, missing outline report, missing section report, missing main report, failing report, stale report, and valid waiver.

## Execution Log

- 2026-06-30: Created from [[prd/pyramid-principle-codex-exec-gate]].
- 2026-06-30: Added output-level checker tests for complete passing evidence, missing outline report, missing section evidence, missing one section report among multiple `section_*.tex` sources, missing main report, failing report, stale fingerprint, malformed report, malformed waiver evidence, and valid explicit waiver continuation.
- 2026-06-30: Updated `check_output_gate.py` to derive required section reports from root-level `section_*.tex`, map required reports to `outline_contract.md`, `section_*.tex`, and `main.tex`, validate each report through `validate_report(..., input_file=...)`, support explicit `--allow-waivers`, and write deterministic `review/pyramid/summary.md` after successful validation.
- 2026-06-30: Review fix: added checkpoint metadata binding for report `artifact_type`, `context_label`, and source path, preserved validation-failure, gate-blocked, and malformed-waiver exit-code classes in the output-level CLI, and added regression coverage for wrong-target reports and orphan section evidence.
- 2026-06-30: Verified with `python -X utf8 .agents\skills\pyramid-principle-validate\scripts\test_check_output_gate.py`, `$env:PYTHONUTF8='1'; python -X utf8 .agents\skills\pyramid-principle-validate\scripts\test_validate_report.py`, `python -X utf8 .agents\skills\pyramid-principle-validate\scripts\test_evaluate_pyramid_text.py`, and scoped `git diff --check`.

## Comments

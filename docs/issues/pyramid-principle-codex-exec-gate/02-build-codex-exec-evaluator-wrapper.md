---
type: issue
status: done
feature: "[[prd/pyramid-principle-codex-exec-gate]]"
depends_on:
  - "[[issues/pyramid-principle-codex-exec-gate/01-generalize-gate-report-contract]]"
blocks:
  - "[[issues/pyramid-principle-codex-exec-gate/03-enforce-gate-outcomes-and-waivers]]"
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

# 02 - Build `codex exec` evaluator wrapper

Status: done

## Goal

Provide a command-line evaluator wrapper that reads one text artifact, runs a constrained `codex exec` semantic review, validates the schema-shaped JSON result, adds wrapper-owned audit metadata, and writes the final Gate Report itself.

## Context

This issue implements the evaluator backend promised by [[prd/pyramid-principle-codex-exec-gate]] and the constrained `codex exec` decision in [[adr/0001-use-codex-exec-for-pyramid-semantic-evaluation]].

The central risk is nested-agent drift: the evaluator must behave like a narrow semantic review subprocess.

## Dependencies

- Depends on: [[issues/pyramid-principle-codex-exec-gate/01-generalize-gate-report-contract]]
- Blocks: [[issues/pyramid-principle-codex-exec-gate/03-enforce-gate-outcomes-and-waivers]], [[issues/pyramid-principle-codex-exec-gate/05-integrate-pyramid-gate-into-bilibili-workflow]], [[issues/pyramid-principle-codex-exec-gate/06-integrate-pyramid-gate-into-youtube-workflow]]

## User Stories Covered

1, 5, 6, 7, 8, 9, 13, 14, 15, 16, 22, 25, 27, 29

## Acceptance Criteria

- [x] The wrapper exposes a file-based CLI with input artifact path, output report path, `artifact_type`, `context_label`, evaluation context, maximum input size, and explicit large-input approval options.
- [x] The wrapper reads the input artifact itself, computes the input hash and size, injects the text into a fixed Pyramid Principle Text Standard prompt, and writes the output report file itself.
- [x] Inputs over the default `160000` character limit fail unless the caller passes the explicit large-input approval option.
- [x] The nested `codex exec` invocation is configured for read-only access, no approval prompts, disabled hooks, ephemeral session behavior, and schema-constrained final output.
- [x] The wrapper uses local Codex CLI authentication and introduces no OpenAI SDK or separate API-key configuration path.
- [x] Codex's semantic output is limited to judgment fields; audit metadata is added by the wrapper after generation.
- [x] Command failures, non-JSON output, malformed JSON, and schema violations produce clear non-zero exit behavior.
- [x] Tests exercise command construction, size limits, output writing, JSON validation, and failure paths with a fake `codex` executable or fake command runner.

## Execution Log

- 2026-06-30: Created from [[prd/pyramid-principle-codex-exec-gate]].
- 2026-06-30: RED: added `scripts/test_evaluate_pyramid_text.py::test_writes_report_and_invokes_codex_with_constrained_command`; it failed because `evaluate_pyramid_text.py` did not exist.
- 2026-06-30: GREEN: added `scripts/evaluate_pyramid_text.py` and `references/evaluator-output-schema.json`; the wrapper reads one UTF-8 artifact, builds the fixed Pyramid Principle prompt, runs constrained `codex exec`, injects audit/waiver metadata, validates with `validate_report.py`, and writes the final report.
- 2026-06-30: Coverage: added evaluator tests for command construction, output writing, default/exact/approved large-input behavior, command failure, non-JSON output, semantic schema violations, and the judgment-only output schema.
- 2026-06-30: Verification: `python -B -m unittest discover .agents\skills\pyramid-principle-validate\scripts -p "test_*.py"` passed 7 tests; `git diff --check` exited 0 with only Git line-ending warnings.
- 2026-06-30: Review fix: added RED coverage for disabled user config in the nested command, then added `--ignore-user-config` alongside `--ignore-rules` so configured hooks and execpolicy rules are skipped while Codex auth remains available. Verification: `python -B .agents\skills\pyramid-principle-validate\scripts\test_evaluate_pyramid_text.py` passed.
- 2026-06-30: Review fix: added semantic-output coverage for non-finite `NaN` scores and rejected them in the wrapper. Also made scratch semantic/candidate report filenames unique to prevent concurrent evaluator runs from clobbering each other. Verification: `python -B .agents\skills\pyramid-principle-validate\scripts\test_evaluate_pyramid_text.py` and full Pyramid script unittest discovery passed.
- 2026-06-30: Final review fix: derived evaluator scratch from the output report root so video reports under `<video-name>\review\pyramid` place semantic output and candidate reports under `<video-name>\待删除\pyramid-evaluator`.

## Comments

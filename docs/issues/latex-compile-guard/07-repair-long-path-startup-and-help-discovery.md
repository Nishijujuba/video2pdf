---
type: issue
status: done
feature: "[[prd/latex-compile-guard]]"
depends_on: []
blocks: []
related_adrs:
  - "[[adr/0003-use-guarded-latex-compile-wrapper]]"
owner: unassigned
created: 2026-07-10
updated: 2026-07-10
tags:
  - issue
  - status/ready-for-agent
---

# 07 - Repair long-path startup and help discovery

Status: done

## Goal

Make the guarded LaTeX compile path usable from deeply nested video output directories on Windows, while keeping every disposable build artifact physically inside the video output directory's `待删除` boundary and making the supported invocation discoverable without triggering a compile.

## Context

This issue repairs an observed wrapper-startup failure: a deep topic output produced a 290-character build `cwd`, then `subprocess.Popen` failed with `NotADirectoryError: [WinError 267]` before XeLaTeX started. The existing wrapper already owns build placement, process launch, timeout behavior, and compile reports under [[prd/latex-compile-guard]].

The repair must preserve the guarded-wrapper and compile-provenance contract in [[adr/0003-use-guarded-latex-compile-wrapper]]. It must also fix the PreToolUse decision boundary: mentioning the wrapper file in a read-only command must not be treated as invoking the wrapper, while actual quick/final execution remains controlled.

## Dependencies

- Depends on: none
- Blocks: none

## Acceptance Criteria

- [x] A quick or final compile whose physical build directory would exceed the Windows-safe `cwd` length uses an automatically managed short launch alias; build evidence and copied inputs remain physically under the owning video output directory's `待删除` subtree.
- [x] If a short launch alias cannot be established, the wrapper fails before starting the LaTeX engine and writes a compile report with a precise, actionable path-startup failure reason.
- [x] `--help` is a no-side-effect supported entry point that documents quick and final required parameters, optional timeout parameters, output/report locations, and automatic long-path handling.
- [x] The PreToolUse guard permits actual wrapper `--help` execution and harmless commands that merely reference the wrapper path, while continuing to reject wrapper compile requests that omit `--mode quick` or `--mode final`.
- [x] Wrapper CLI tests cover a deep-path launch, help behavior, fallback failure reporting, and preservation of the physical artifact boundary.
- [x] Hook decision tests cover `--help`, a source-reading command that names the wrapper, malformed wrapper invocation, and valid quick/final invocation.
- [x] The YouTube and Bilibili render instructions expose the discoverable help command and remain aligned with the guarded compile contract.
- [x] Verification evidence records the test commands and their outcomes without compiling a real video.

## Execution Log

- 2026-07-10: Created after diagnosis of the topic 03 5.6 quick-compile startup failure and user confirmation of the artifact-boundary, automatic-alias, and help-discovery decisions.
- 2026-07-10: Added Windows short-path launch alias handling while retaining physical build files under `<video-output-dir>\待删除\latex-build`; alias setup failures now stop before engine startup and remain recorded in `compile_report.json`.
- 2026-07-10: Added no-side-effect CLI help, invocation-aware PreToolUse decisions for help/read-only/source-check commands, and retained blocking for malformed quick/final requests.
- 2026-07-10: Verified with `python -X utf8 -B .agents\skills\bilibili-render-pdf\scripts\test_compile_latex_ascii.py` (25 tests passed), `python -X utf8 -B .agents\skills\bilibili-render-pdf\scripts\test_latex_compile_pretooluse_guard.py` (18 tests passed), and `python -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\test_skill_contracts.py` (6 tests passed). Tests use fake engines and do not compile a real video.
- 2026-07-10: Final regression passed `python -X utf8 -B .agents\skills\final-delivery-acceptance\scripts\test_latex_compile_guard_e2e.py` (5 tests) and `python -X utf8 -B scripts\generate_issue_dependency_views.py --check`; direct wrapper `--help` exited 0 without compile artifacts.

## Comments

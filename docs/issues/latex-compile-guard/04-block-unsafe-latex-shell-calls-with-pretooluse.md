---
type: issue
status: done
feature: "[[prd/latex-compile-guard]]"
depends_on:
  - "[[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]]"
blocks:
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

# 04 - Block unsafe LaTeX shell calls with PreToolUse

Status: done

## Goal

Add the strong pre-execution guard that blocks unsafe LaTeX shell commands before they can enter a long-running tool call.

## What to build

Create a project `PreToolUse` guard for shell commands. A completed slice should inspect Codex hook input for `Bash` commands, strongly block direct LaTeX engine invocations, strongly block dangerous output-directory forms, allow the guarded wrapper command, and perform a fast read-only scan for known compile anomalies.

The hook must remain an entry guard. It must not compile, kill processes, move files, or repair workspace state.

## Context

This issue depends on the wrapper quick path from [[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]] so the hook can point users to a real allowed compile entry point. Current local Codex schema uses the `PreToolUse` event with a `Bash` matcher for shell command hooks.

## Dependencies

- Depends on: [[issues/latex-compile-guard/01-establish-guarded-compile-wrapper-quick-path]]
- Blocks: [[issues/latex-compile-guard/05-integrate-guarded-compile-contract-into-render-skills]]

## User Stories Covered

2, 3, 4, 25, 26, 27, 28, 29, 33

## Expected Touched Paths

- New PreToolUse guard script under the project skill or hook scripts area
- `.codex/hooks.json` or project hook configuration
- Hook decision tests
- Hook configuration contract tests

## Acceptance Tests

- A hook input containing a direct `xelatex` command is blocked.
- A hook input containing direct `xelatex.exe`, `pdflatex`, `lualatex`, `latexmk`, or `tectonic` is blocked.
- A hook input containing `-output-directory $build`, `-output-directory=$build`, `${build}`, `%build%`, an empty output directory, project root, or plain `build` is blocked.
- A hook input that invokes the guarded wrapper is allowed.
- A non-LaTeX shell command is allowed.
- The hook emits a clear reason when it blocks a command.
- The hook's read-only scan can report literal `$build` directories, zero-byte compile logs, or stale LaTeX process indicators without writing or terminating anything.
- Existing Stop hook configuration remains present and keeps its Final Delivery Guard purpose.

## Acceptance Criteria

- [x] Project `PreToolUse` hook configuration invokes the LaTeX compile guard for shell commands.
- [x] The hook strongly blocks direct LaTeX engines and dangerous output-directory forms.
- [x] The hook allows the guarded wrapper command.
- [x] The anomaly scan is read-only, fast, and covered by tests.
- [x] Existing Stop hook behavior remains documented and configured.

## Execution Log

- 2026-07-05: Created from [[prd/latex-compile-guard]].
- 2026-07-06: Added `latex_compile_pretooluse_guard.py`, wired `.codex/hooks.json` PreToolUse `Bash` guard, preserved Stop hook configuration, and verified direct engine blocks, wrapper allows, unsafe output-directory blocks, and read-only anomaly scan with `test_latex_compile_pretooluse_guard.py`.

## Comments

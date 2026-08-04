# Issue #43 Global Gate Cutover handoff

## Resume boundary

Issue #43 remains in progress on branch `video-workflow-2.0` at committed HEAD
`49124f289149d309b0829daa75294a2ea1826de4`. The Issue #43 working set contains
an uncommitted Windows concurrency repair in these files:

- `src/video2pdf_workflow_kernel/acceptance_v2.py`
- `src/video2pdf_workflow_kernel/utils.py`
- `tests/video_workflow/test_acceptance_v2.py`

This handoff document is an additional untracked Issue #43 artifact until it is
staged intentionally. The shared worktree currently also contains unrelated
video-task activity in `.codex/delivery-targets/task-index.json` and under
`workspace/`; those paths are outside Issue #43 and must remain untouched. No
final validator, evidence publication, push, or GitHub issue closure should be
inferred from this handoff.

Authoritative requirements remain GitHub
[Issue #43](https://github.com/Nishijujuba/video2pdf/issues/43), with
[Issue #41](https://github.com/Nishijujuba/video2pdf/issues/41) as the preferred
supplement. The approved execution order is:

1. affected-module tests;
2. fast static, contract, and `git diff --check` checks;
3. independent spec and release/security/evidence reviews;
4. code freeze;
5. final validator and complete Delivery Evidence.

Any code repair after a review or validator failure returns to step 1. A full
repository test suite is outside the task scope.

## Current repair

The final evidence run exposed real Windows races in
`AcceptanceV2CliTests.test_two_writers_are_fenced_at_patch_and_report_publication`.
Two `acceptance-patch-commit` processes could reach `os.replace` for the same
content-addressed destination before the SQLite CAS. Windows returned
`PermissionError [WinError 5]`, which escaped as `kernel_error` instead of a
contractual fencing result. Review then found the same pre-CAS risk in Report
intent and staged bundle publication.

The current diff:

- adds `AtomicJsonReplaceError(OSError)` to distinguish only the final
  `os.replace` stage while preserving existing `except OSError` behavior and
  the original error identity;
- accepts a competing pre-CAS publication only for Windows replace errors 5,
  32, or 33 and only when the destination proves the same complete JSON
  identity;
- applies that boundary to Patch publication, Report intent, and all three
  staged Report bundle members (`acceptance_report.json`,
  `attempt-record.json`, and `repair-ledger.json`);
- leaves temp open/write/flush/fsync failures, POSIX errors, other Windows
  errors, missing or unreadable targets, and content conflicts fail-closed;
- prevents a losing writer from overwriting a winner's already controlled
  intent;
- cleans only the UUID temporary file owned by the current atomic write and
  preserves the primary exception if cleanup also fails;
- adds deterministic positive and negative tests for stage classification,
  content identity, platform/error-code boundaries, cleanup, and `OSError`
  compatibility.

Inspect the exact implementation with:

```powershell
git diff 49124f289149d309b0829daa75294a2ea1826de4 -- `
  src/video2pdf_workflow_kernel/acceptance_v2.py `
  src/video2pdf_workflow_kernel/utils.py `
  tests/video_workflow/test_acceptance_v2.py
```

## Valid current test evidence

The latest code state passed:

- focused Patch/Report concurrency and error-contract set: 16 tests,
  680.968 seconds,
  exit 0, `no_secret_detected`, evidence eligible;
  `待删除/long-running/issue43_report_race_focused_20260804_222102_6ce4a605`
- affected Workflow modules: 136 tests, 5004.064 seconds, exit 0,
  `no_secret_detected`, evidence eligible;
  `待删除/long-running/issue43_report_race_workflow_modules_20260804_223238_79959e01`
- affected Delivery Guard modules: 60 tests, 2595.035 seconds, exit 0,
  `no_secret_detected`, evidence eligible;
  `待删除/long-running/issue43_report_race_delivery_guard_20260804_235616_17c5a18f`
- fast contract checks: 30 positive and 30 expected-negative Delivery Quality
  contracts, plus 5 focused atomic/report contracts, all passed.

## Evidence that must not be reused

The following results predate the latest compatibility change and are useful
only for diagnosis:

- Delivery Guard pass before the latest change:
  `待删除/long-running/issue43_windows_race_postreview_delivery_guard_20260804_152615_454368d0`
- fast checks before the latest change;
- the earlier spec-axis PASS and release-axis FAIL that produced the structured
  replace-stage repair;
- failed final collection:
  `待删除/exit-evidence-refresh/global-gate/20260804_040425_407169/collection.json`
  (its first two stages passed, and `issue43-complete-acceptance-v2` failed on
  the Windows race).

Do not resume that collection. A fresh collection must bind the eventual clean
implementation HEAD.

## Exact next steps

1. Preserve the unrelated `.codex/delivery-targets/task-index.json` and
   `workspace/` activity; do not stage or modify those paths for Issue #43.
2. Run two independent subagents in parallel:
   - spec/requirements axis against Issues #43 and #41;
   - repository standards plus release/security/evidence axis.
3. If both axes pass, stage the reviewed implementation and tests. Preserve
   this repository handoff; include it intentionally in a commit or place it in
   a separate documented commit before evidence collection. Commit only the
   four Issue #43 paths.
4. Before evidence collection, require the complete shared worktree to be
   clean or use an explicitly approved isolated checkout. Do not hide, reset,
   move, or absorb the unrelated active-task changes.
5. Start a new collection with
   `scripts/collect_issue43_exit_evidence.py collect`. Run its five persisted
   stages sequentially, finalize only after all are terminal passes, publish
   the evidence-only child commit, then run the final validator from the
   published evidence commit.
6. If the validator changes no code, no new dual-axis review is required. If it
   requires a code repair, return to affected tests.
7. Push and update/close Issue #43 only after current final evidence and remote
   SHA verification succeed.

## Operational constraints

- Use subagents for each stage. Every spawn must set
  `model="gpt-5.6-sol"`, `reasoning_effort="medium"`, and a non-`all`
  `fork_turns` value.
- Commands expected to exceed five minutes, expensive reruns, and evidence
  commands must use `scripts/persisted_command.py`.
- Run persisted stages sequentially. Report a task name and `run_dir` once,
  then emit updates only for terminal, security, milestone, error, or decision
  events.
- Preserve all unrelated work. Permanent deletion is forbidden; material for
  later cleanup belongs under `待删除/`.
- GitHub operations must use `gh`.
- Responses follow the repository's third-person Chinese style constraints.

## Suggested skills

- `implement`: resume the approved Issue #43 implementation workflow and keep
  affected-test scope bounded.
- `code-review`: perform the required independent standards/release and spec
  reviews after fast checks pass.
- `handoff`: refresh this document only if another session boundary occurs
  before completion.

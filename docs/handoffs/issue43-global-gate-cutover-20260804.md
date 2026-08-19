# Issue #43 Global Gate Cutover handoff

## Resume boundary

Issue #43 remains in progress on branch `video-workflow-2.0` at committed HEAD
`06c205e4921addab1af0863b723d4e9b5c0ffa54`. That commit contains the reviewed
Windows Patch/Report concurrency repair. The current uncommitted Issue #43
working set is exactly:

- `src/video2pdf_workflow_kernel/acceptance_v2.py`
- `tests/video_workflow/test_acceptance_v2.py`
- `tests/video_workflow/_issue43_git_authority.py`
- `tests/video_workflow/test_issue43_exit_evidence.py`
- this handoff (`docs/handoffs/issue43-global-gate-cutover-20260804.md`)

This handoff document is a tracked but intentionally unstaged Issue #43 artifact until it is
staged intentionally. The shared worktree currently also contains unrelated
activity in `.codex/delivery-targets/task-index.json`, `.gitignore`, and under
`workspace/**`; those paths are outside Issue #43 and must remain untouched.
The isolated `q` worktree and its branch `codex/issue43-final-evidence-20260805`
remain at `06c205e` with failed diagnostic collections under ignored
`q/待删除/`; `q/` must not be staged or modified from this worktree. No final
validator, evidence publication, push, or GitHub issue closure should be
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

## Current uncommitted set

### Committed 06c205e context: Windows Patch/Report concurrency repair

The final evidence run exposed real Windows races in
`AcceptanceV2CliTests.test_two_writers_are_fenced_at_patch_and_report_publication`.
Two `acceptance-patch-commit` processes could reach `os.replace` for the same
content-addressed destination before the SQLite CAS; Windows returned
`PermissionError [WinError 5]`, which escaped as `kernel_error` instead of a
contractual fencing result, and review found the same pre-CAS risk in Report
intent and staged bundle publication. The committed `06c205e` repair adds
`AtomicJsonReplaceError(OSError)` to distinguish only the final `os.replace`
stage; accepts a competing pre-CAS publication only for Windows replace errors
5, 32, or 33 and only when the destination proves the same complete JSON
identity; applies that boundary to Patch publication, Report intent, and all
three staged Report bundle members (`acceptance_report.json`,
`attempt-record.json`, `repair-ledger.json`); keeps temp write failures, POSIX
errors, other Windows errors, unreadable targets, and content conflicts
fail-closed; prevents a losing writer from overwriting a winner's intent; and
cleans only the UUID temporary file owned by the current atomic write.

### Linked-worktree fixture repair

`_issue43_git_authority.py` assumed the source object database was
`PROJECT_ROOT/.git/objects`; a linked worktree uses a `.git` gitfile and shares
the main repository object database, so synthetic fixture repositories could
not read the frozen implementation tree. The repair resolves the shared
directory through `git rev-parse --git-common-dir`, handles relative and
absolute results, fails closed for Git errors, empty output, missing paths, or
non-directory object paths, freezes one `HEAD^{commit}` snapshot per authority
construction, and binds cache identity to the explicit source repository,
observed source HEAD, and selected implementation boundary. A real gitfile
linked-worktree regression in `test_issue43_exit_evidence.py` advances HEAD at
the same source path and proves a new authority generation is built.

### Patch-ownership repair (new in this set)

Patch intent and patch file publication moved inside the SQLite
`BEGIN IMMEDIATE` transaction in `acceptance-patch-commit`. Fencing
(execution authority, active claim, claim generation, fencing token, and
existing intent state) is now evaluated before any patch file write, so a
stale or unfenced writer never touches the content-addressed destination. On
fencing failure the new module helper `_abort_intent_and_reject` publishes an
`ABORTED` intent file before `ROLLBACK` — a later reconcile never mistakes the
abandoned preparation for a live one — and a failed abort publication rolls
the transaction back and propagates.

### Classification split (new in this set)

Outcomes that previously collapsed into one stale-fencing rejection are now
split: a same-intent retry of an interrupted publication rejects with gate
`publication_recovery` / code `acceptance_reconcile_required`, while genuinely
stale authority keeps gate `patch_fencing` / code
`acceptance_patch_fencing_stale`. `_abort_intent_and_reject` centralizes the
fail-closed abort path for the stale case.

### Report-side concurrency test rewrite (new in this set)

`test_acceptance_v2.py` report-side concurrency tests were rewritten for
determinism: mtime-poll and event barriers replace bare sleeps, and oracles
were widened with scenario records so each interleaving asserts its exact
observable outcome. Report-side production code was deliberately NOT
restructured; see Reviewed decisions.

## Reviewed decisions (dual-axis review, 2026-08-08)

1. Patch/report publication-boundary asymmetry is accepted. The report side
   retains the committed `06c205e` semantics — pre-transaction prepare plus
   `AtomicJsonReplaceError` competing-publication acceptance; a reconcile
   abort is not a tombstone, and a slow writer may legitimately republish and
   win — because that boundary already passes its interruption, fencing,
   idempotency, and reconciliation tests, and restructuring it would require
   another full review plus evidence cycle.
2. The previous version of this handoff understated the uncommitted working
   set (it listed only the two fixture files while `acceptance_v2.py` and
   `test_acceptance_v2.py` were also modified). That is recorded here as a
   corrected documentation defect.

## Valid current test evidence

Each run below binds the code state at HEAD `06c205e` plus the five-file
working set as of 2026-08-08 morning. After these runs, round-2 review applied
test-only amendments to `test_acceptance_v2.py` (scenario record
`patch_writer_commit_vs_waiting_reconcile`; deterministic rendezvous barrier
`run_rendezvousing_competing_writers` in the two failed-writer twins); each
touched test was re-run twice individually with exit 0, and the focused
concurrency set was re-run after the amendment (see the
`issue43_post_a3_focused_*` run when present). The authoritative full-suite
evidence is refreshed by the Phase 6 collection against the frozen commit.
The pre-amendment runs:

- focused concurrency/fencing set: 27 tests, 1363.681 seconds, exit 0;
  `待删除/long-running/issue43_repair_r1r3_focused_20260808_084340_fe09d015`
- exit-evidence / linked-worktree authority set: 28 tests, 155.272 seconds,
  exit 0;
  `待删除/long-running/issue43_r1r3_exit_evidence_20260808_091133_53bc11d2`
- affected Workflow modules: 141 tests, 4036.391 seconds, exit 0;
  `待删除/long-running/issue43_r1r3_workflow_modules_20260808_091430_72db2e73`
- affected Delivery Guard modules: 60 tests, 1663.704 seconds, exit 0;
  `待删除/long-running/issue43_r1r3_delivery_guard_20260808_103301_baffd552`
- fast checks 2026-08-08: `git diff --check` clean; `py_compile` clean;
  delivery-quality-contracts-check exit 0 (30 positive / 30 negative,
  catalog_sha256
  `b25dd274bf2072f75db1f26f9e26892a2e2718de70a358804937e773601f455f`); 5
  focused atomic/report contract tests pass in 154.336 seconds.

## Evidence that must not be reused

The following results predate the latest code state and are useful only for
diagnosis:

- Delivery Guard pass before the latest change:
  `待删除/long-running/issue43_windows_race_postreview_delivery_guard_20260804_152615_454368d0`
- fast checks before the latest change;
- the earlier spec-axis PASS and release-axis FAIL that produced the
  structured replace-stage repair;
- failed final collections in the main and isolated checkouts:
  `待删除/exit-evidence-refresh/global-gate/20260804_040425_407169/collection.json`
  (first two stages passed, `issue43-complete-acceptance-v2` failed on the
  Windows race), plus
  `q/待删除/exit-evidence-refresh/global-gate/20260804_165759_403503/collection.json`
  (missing runtime parent) and
  `q/待删除/exit-evidence-refresh/global-gate/20260804_170003_137482/collection.json`
  (invalid linked-worktree alternate object path);
- all three `issue43_source_head_cache_*` runs (pre-patch-ownership code
  state), including
  `待删除/long-running/issue43_source_head_cache_freshness_exit_evidence_20260805_032608_d0c61388`,
  `待删除/long-running/issue43_source_head_cache_workflow_modules_20260805_033008_88977246`,
  and
  `待删除/long-running/issue43_source_head_cache_delivery_guard_20260805_044038_1ec51620`;
- all `issue43_authority_overlay_*` runs (failed, pre-repair);
- all `issue43_patch_ownership_*` runs (failed full-suite run plus pre-review
  focused pass).

Do not resume any of those collections. A fresh collection must bind the next
reviewed commit.

## Exact next steps

1. Run a dual-axis independent re-review of the FULL uncommitted diff — spec
   axis against Issues #43 and #41, plus a standards/release/security/evidence
   axis. Re-review is required because the code changed after the first
   review.
2. If both axes pass without code change, commit ONLY the five Issue #43
   files listed in Resume boundary. Commit message convention:
   `fix: ...(#43)`, matching the existing `git log` style.
3. Fast-forward the isolated `q` worktree/branch
   `codex/issue43-final-evidence-20260805` to the new commit; preserve the
   ignored failed collections under `q/待删除/`; confirm `q` is Git-clean.
4. Ensure `q/待删除/kernel-test-runs` exists as the direct-unittest runtime
   compatibility parent, then start a fresh collection with
   `scripts/collect_issue43_exit_evidence.py collect`. Run its five persisted
   stages sequentially, finalize only after all are terminal passes, publish
   the evidence-only child commit, then run the final validator from the
   published evidence commit.
5. If the validator changes no code, no new dual-axis review is required. If
   it requires a code repair, return to affected tests.
6. Push and update/close Issue #43 only after final evidence and remote SHA
   verification succeed.

## Operational constraints

- Use subagents for each stage.
- Commands expected to exceed five minutes, expensive reruns, and
  evidence-bearing commands must use `scripts/persisted_command.py`
  (`start` → record `data.run_dir` → `wait`/`show`; report the task name and
  `run_dir` once, then emit updates only for terminal, security, milestone,
  error, or decision events).
- Run persisted stages sequentially.
- Preserve all unrelated work. Permanent deletion is forbidden; material for
  later cleanup belongs under `待删除/`.
- GitHub operations must use `gh`.
- Responses follow the repository's third-person Chinese style constraints.

## Suggested skills

- `implement`: resume the approved Issue #43 implementation workflow and keep
  affected-test scope bounded.
- `code-review`: perform the required independent standards/release and spec
  re-reviews of the full uncommitted diff.
- `handoff`: refresh this document only if another session boundary occurs
  before completion.

## Addendum 2026-08-09: A′ publication-gate contract change (`b1e926e`)

Committed `b1e926e` ("fix: bind evidence republication to subset
publication and blob bytes (#43)") repairs the contract deadlock found by
the Phase 7 validator: with the 21-path evidence generation already in the
parent tree and the five exit-code blobs immutable, no direct-child
republication could ever satisfy the old `diff-tree == evidence_paths`
equality gate. The `historical_evidence` publication gate in
`src/video2pdf_workflow_kernel/global_gate_exit_evidence.py` now requires:

1. the publication `diff-tree` paths to stay WITHIN the declared
   `evidence_paths` (subset, not equality; exactly-one-parent enforcement
   retained via `_commit_paths`);
2. every declared path to resolve as a REGULAR blob (modes 100644/100755;
   symlink 120000 and gitlink 160000 rejected) in the publication tree via
   one `git ls-tree -r <publication> -- <paths>` call, relying on the
   canonical-set gate running first so declared paths are safe pathspecs;
3. every non-manifest publication blob to sha256-match the
   manifest-declared fingerprint via `sha256_git_blob` (byte binding,
   closing the dirty-worktree stale-bytes window).

Error codes: a declared path that does not resolve to a regular blob and
is not in the publication diff fails with the NEW
`historical_evidence_path_unpublished`; smuggled undeclared paths, paths
deleted or non-regular in the diff, and byte-binding mismatches reuse
`historical_evidence_paths_stale`; git transport failures during byte
binding map to `historical_evidence_lineage_invalid`.

Deliberate semantic relaxation (recorded per maintainer decision A′): the
historical invariant "the publication publishes all evidence" becomes "the
publication tree contains all declared evidence at declared bytes". The
canonical-set gate (`evidence_paths` == manifest + per-command logs +
persisted artifacts, derived from the manifest itself) is unchanged and
still runs first, and `implementation_commit_evidence_only` still floors
the implementation commit. `scripts/validate_slice_exit_evidence.py`
`validate_lineage` intentionally retains exact equality for slices 1-10;
only slice 11 uses the new semantics.

Companion changes in the same commit:
`tests/video_workflow/_issue43_git_authority.py::_authority_is_reusable`
mirrors the subset+existence rule (F1); the collector reverts its
uncommitted incremental-`evidence_paths` experiment so its diff vs
`f2004e4` is exactly the `sha256_git_blob` fixture-fingerprint fix (F5);
fixture evidence writes are LF-only bytes and fixture repos set
`core.autocrlf=false`, so fixture disk bytes equal blob bytes under the
tree's `evidence/global-gate/** text eol=lf` attribute (production
evidence is LF on disk: the persisted runner writes with `newline="\n"`
and the collector LF-normalizes logs).

Review and evidence: dual-axis review (spec axis against Issues #43/#41;
standards/release/security/evidence axis) returned PASS after one
condition round (unpublished-branch diagnostic message accuracy,
byte-binding transport-error classification, and two added negative tests
covering the byte-binding failure and symlink rejection). Persisted test
evidence: 37/37 `tests.video_workflow.test_issue43_exit_evidence`, 4/4
global-gate suites (`test_issue43_activation_fencing` +
`test_issue43_spec_gap_contracts`), runs
`待删除/long-running/issue43_a_prime_review_fix_*`; earlier TDD red/green
runs `issue43_a_prime_red_*` and `issue43_a_prime_green3_*`.

# Issue #15 Parallel Failure Discovery — Final Report

## Conclusion-first

The 987-test `video-workflow` suite at commit `22b4593` has **8 modules that genuinely fail in a
clean short worktree** (reproducible implementation issues, confirmed in isolated serial runs),
plus **11 modules that fail only under the v3 project-runner's frozen-history execution** (runner
isolation incompatibility, not regressions) and **60 modules that pass outright**. The dominant real
problem is a single root cause: **`Delivery Quality migration ledger source fingerprint is stale`**
in `src/video2pdf_workflow_kernel/delivery_quality.py:466` (`_validate_relations`), which accounts
for 40+ failing test methods across four modules. No Slice 14 evidence was published, and **no
repairs were performed** — this is a read-only diagnostic.

## Isolation proof

- Implementation commit: `22b4593` (`test: isolate active guard discovery source (#15)`), detached HEAD.
- Manual lanes ran in fresh short worktrees created with `git -c core.autocrlf=false worktree add`:
  - Lane A: `D:\p15a` — Git/Exit Evidence/compile and rendered-text authority (16 modules)
  - Lane B: `D:\p15b` — Control Store/lifecycle/source publication (16 modules)
  - Lane C: `D:\p15c` — provider/acquisition/contracts/policy (19 modules)
- Serial isolated confirmation: `D:\p15d` (fourth clean worktree) — 8 modules, one at a time,
  no other test child process alive.
- Each worktree verified `## HEAD (no branch)` before/during/after every module (excepting one
  external-edit event documented below). All lanes used `scripts\persisted_command.py`; every run
  retained under `待删除\long-running` with `status.json` + `exit-code.txt`.

## v3 terminal state (baseline collector)

- v3 project-suite run: `state=failed`, `exit_code=1`, elapsed ~57,620 s (~16.0 h).
- Run dir: `D:\q15b\待删除\long-running\issue15_final_video_workflow_project_suite_v3_20260818_221021_e38d4bfb`
- Runner dir: `D:\t16\video2pdf\video-workflow\20260818_141428_e1f204e1`
- Final module tally: **79/79 modules with results; 831 passed, 91 failed, 65 errors** (987 executions).
- Module verdicts: 60 all-passed, 19 with ≥1 failed/error execution.

## Manual lane coverage

| Lane | Worktree | Manual runs | Skipped (clean v3) | Totals (manual) |
|---|---|---|---|---|
| A | D:\p15a | 15 | 1 (issue9) | 202 run, 157 pass, 36 fail, 9 err |
| B | D:\p15b | 14 | 2 (source_package, source_publication_control_store) | 83/83 pass |
| C | D:\p15c | 13 | 6 (issue15_batch_cli, issue15_batch_policy_docs, issue4_gate4, prod_source_acq_hardening, provider_candidate_promotion, resource_admission_multiprocess) | 65 run, 62 pass, 3 fail |

Manual-set size: **51 modules** (13 v3-failing + 38 assigned/pending at handoff). Lane lists cover
the 51 exactly with no overlap (verified programmatically).

## Canonical failure matrix — REPRODUCIBLE (confirmed in D:\p15d)

| Module | v3 (p/f+e) | Manual | Confirmed in D:\p15d | Cluster |
|---|---|---|---|---|
| test_delivery_quality_contracts | 5/3 | failed 8/5/3 | CONFIRMED, exit 1 | C1 |
| test_guarded_final_compile_adapter | 5/18 | failed 23/16/2/5 | CONFIRMED, exit 1 | A |
| test_issue13_final_evidence_cli | 0/8 | failed 8/0/8/0 | CONFIRMED (fresh retry), exit 1 | A |
| test_precompile_quality | 0/15 | failed 15/0/15/0 | CONFIRMED, exit 1 | A |
| test_rendered_text_reconciliation | 4/10 | failed 14/4/10/0 (23 sub) | CONFIRMED, exit 1 | A |
| test_issue14_exit_evidence | 16/10 | failed 26/25/0/1 | CONFIRMED, exit 1 | B |
| test_issue43_exit_evidence | 32/6 | failed 38/35/0/3 | CONFIRMED, exit 1 | C |
| test_issue14_platform_cutover | 3/8 | failed 11/10/1/0 | CONFIRMED, exit 1 | D |

## Root-cause clusters (semantic gates)

### Cluster A — Delivery Quality migration ledger source fingerprint is stale (40+ methods)
- Reproduces in 4 modules: `test_guarded_final_compile_adapter` (5 error + 2 fail),
  `test_issue13_final_evidence_cli` (8/8), `test_precompile_quality` (15/15),
  `test_rendered_text_reconciliation` (10 distinct methods / 23 subtests).
- Gate: `ContractError: Delivery Quality migration ledger source fingerprint is stale` at
  `src\video2pdf_workflow_kernel\delivery_quality.py:466` (`_validate_relations`), raised via
  `registry.check()` from `final_compile.py:313`. CLI form: `{"classification":"contract_invalid",
  "command":"delivery-quality-precompile-prepare", ...}` with **exit code 20** where tests expect
  0/30/40/60. `rendered_text_reconciliation` additionally sees its expected
  `rendered_text_reconciliation_contract_gap` misclassified as `contract_invalid`.
- This is the single highest-value fix target.

### Cluster C1 — conformance/contract-check CLI returns exit 20 (3 methods)
- `test_conformance_reports_semantic_variance_without_hiding_other_results` (`20 != 30`, line 348),
  `test_public_conformance_runs_three_isolated_attempts_per_profile_case` (`20 != 0`, line 300),
  `test_public_contract_check_proves_closed_target_only_policy_surface` (`20 != 0`, line 161).
- Same underlying delivery-quality failure family as Cluster A (tests expect CLI exit 0/30, get 20).

### Cluster B — stale pinned implementation fingerprint (1 error)
- `test_issue14_exit_evidence ... test_bilibili_kernel_preserved`: `EvidenceError: complete
  implementation change set fingerprint differs for .agents/skills/bilibili-render-pdf/SKILL.md` —
  expected `d1d43ce5…`, got `ee70aed9…` (SKILL.md last changed in `970198f`).

### Cluster C — git cat-file missing object in fixture clone (3 errors)
- `test_issue43_exit_evidence ... test_collector_materializes_the_registered_schema_valid_cutover_shape`,
  `test_finalize_fingerprints_fixtures_from_git_blob_bytes`, `test_finalize_first_publication_lists_the_complete_evidence_set`.
- Gate: `EvidenceSupportError: git cat-file -e <commit>^{commit} failed: fatal: Not a valid object name`
  in `evidence.py` — `22222222…` placeholder never exists, and historical `64f3fb16…` is absent
  from the fixture `shared-clone` under `待删除\kernel-test-runs` (exists in main repo only).

### Cluster D — workflow-policy-check rejects (1 failure)
- `test_issue14_platform_cutover ... test_workflow_policy_check_reports_both_kernel`: returns
  `returncode 30 / acceptance_v2_rejected / platform_statuses None` instead of
  `0 / workflow_policy_current / bilibili+youtube active_kernel` — consistent with Cluster A
  (acceptance-v2/precompile authority failing).

## Isolated confirmation results

All 8 failure modules re-ran **serially in `D:\p15d`** with no other test child process alive,
confirming each cluster:
- Module 1 `test_delivery_quality_contracts` — CONFIRMED (C1), run `...74b18eae`
- Module 2 `test_guarded_final_compile_adapter` — CONFIRMED (A), run `...7941312b`
- Module 3 `test_issue13_final_evidence_cli` — CONFIRMED (A), fresh retry run `...0f312406`
  (first launch `fc515a91` was a runner launch failure: supervisor exited without spawning a child;
  state `unknown` → reconciled; preserved untouched)
- Module 4 `test_precompile_quality` — CONFIRMED (A), run `...b44f8368`
- Module 5 `test_rendered_text_reconciliation` — CONFIRMED (A), run `...5613c332`
- Module 6 `test_issue14_exit_evidence` — CONFIRMED (B), run `...0d954842`
- Module 7 `test_issue43_exit_evidence` — CONFIRMED (C), run `...bc7b0aad`
- Module 8 `test_issue14_platform_cutover` — CONFIRMED (D), run `...f3fd0d76`

## Canonical failure matrix — v3-only (NOT regressions; frozen-history isolation)

These modules failed/errored under v3 but **passed 100% in the clean short worktree**, so their v3
failures are project-runner frozen-history isolation (missing baseline Git objects / historical
evidence paths outside the frozen execution root), not implementation regressions:

- test_issue13_candidate_confirmation (v3 4 errors → manual 4/4)
- test_issue13_candidate_hardening (v3 13+12 → manual 28/28)
- test_issue13_cold_start_cutover (v3 4 fail → manual 6/6)
- test_issue13_delivery_acceptance_bind (v3 6 fail → manual 7/7, slow 1796 s lifecycle)
- test_issue13_delivery_lifecycle (v3 1 fail → manual 17/17)
- test_issue13_exit_evidence (v3 2 fail → manual 10/10)
- test_issue13_platform_cutover (v3 1+12 → manual 13/13)
- test_issue13_run_initialization (v3 4 errors → manual 4/4)
- test_issue13_whisper_source_cli (v3 2 fail → manual 7/7)
- test_issue15_exit_evidence (v3 1+10 → manual 22/22)
- test_issue43_active_guard (v3 6 fail → manual 18/18, slow 1963 s)

## Pollution audit

- Lane A (D:\p15a): clean after every module; no Git locks, no WinError/SQLite/atomic-replace.
  One tool-layer transient `PermissionError` on a `wait` observer for module 11 — replaced with a
  fresh observer for the same run; persisted state never `unknown`/`interrupted`.
- Lane B (D:\p15b): clean after every module; no pollution indicators.
- Lane C (D:\p15c): clean after every module; no pollution indicators.
- Confirmation (D:\p15d): clean after every module; no pollution indicators.
- **External-edit event (flagged, not caused by this diagnostic):** on 2026-08-19 ~15:01–15:16,
  independent tracked-file edits appeared in `D:\p15a` (`tests/video_workflow/test_issue14_platform_cutover.py`,
  `tests/video_workflow/test_issue43_exit_evidence.py`) and `D:\p15b`
  (`src/video2pdf_workflow_kernel/evidence.py` — pinning `core.autocrlf` in `git archive` calls —
  and `tests/video_workflow/test_evidence_implementation_changes.py`), plus a successful external
  `fixture_cluster_d_test_issue43_exit_evidence` persisted run (38/38 OK) in D:\p15a. These are
  **not** products of this diagnostic (lanes finished ~11:00–13:00 and reported clean). The affected
  worktrees were left untouched. This suggests parallel repair work occurring in the same
  worktrees; the coordinator did not modify, terminate, or revert them. Live python test processes
  (unittest on delivery-quality/evidence/issue14/issue43 targets) were observed again at 15:19.

## Coverage / totals (deduplicated)

- 79 modules total. 60 v3-green (28 of these were also manually verified in scope).
- 11 modules isolated-frozen-history (v3-only failures, manual-green).
- 8 modules reproducible failures (all confirmed in D:\p15d).
- Survivor of launch failure (Module 3 first attempt) not counted as a test result.

## Explicit statement

**Manual lane and confirmation results are diagnostic only. They do not qualify and cannot publish
Slice 14 Exit Evidence.** The single closed full-suite authority required before publishing
`evidence/slice-14/` remains a full-suite pass at the final implementation commit; the v3 run is
itself not a qualifying run (it exited `failed`).

## Recommended next repairs (NOT performed — awaiting user authorization)

1. **Cluster A (highest value)** — fix the stale Delivery Quality migration ledger source
   fingerprint at `delivery_quality.py:466` `_validate_relations` so precompile/final-compile exit
   codes (20→0/30/40/60) restore. Likely a migration-ledger/registry fingerprint rebuild bound to
   the current commit. Re-run `test_delivery_quality_contracts`, `test_guarded_final_compile_adapter`,
   `test_issue13_final_evidence_cli`, `test_precompile_quality`, `test_rendered_text_reconciliation`.
   This single cluster also explains Cluster D (`workflow-policy-check` exit 30).
2. **Cluster B** — repin the `bilibili-render-pdf/SKILL.md` implementation fingerprint to the
   current SKILL.md blob (`ee70aed9…`) in the issue-14 exit-evidence contract.
3. **Cluster C** — make the issue43 `finalize` fixtures tolerant of absent historical Git objects in
   fixture clones (either seed the historical commit objects or treat `22222222…` placeholder /
   fixture-clone-absent object as an explicit expected negative case rather than a hard `git cat-file`
   failure).
4. **Cluster C1** — align the conformance CLI exit-code contract with the fixed delivery-quality
   behavior; may resolve automatically after Cluster A.
5. After repairs, run a `git diff` review (e.g., `/code-review`) and re-execute a single closed
   full-suite qualification at the final implementation commit before any Slice 14 publication.

No code, tests, schemas, fixtures, or policy documents were changed by this diagnostic run.

# Issue 45 Research Baseline: Project-Test Runner, Resource Hazards, and Performance Path

## 1. Executive conclusion

**Conclusion.** Issue 27 delivered a sound external-root, module-process scheduler and a rigorous Promotion-v2 evidence contract, and its remote head is already integrated into the live branch. Its Promotion remains unachieved. Every current 499-Test-ID formal attempt exceeded the 1,800-second ceiling; the best complete Attempt 8 scheduler was already 97.90% utilized, so scheduler refill latency is not the primary bottleneck. The next performance work should reduce repeated validation and high-cost module work while preserving module-process isolation. Class-level splitting is a separate semantic change and requires explicit fixture classification before implementation.

The most decision-relevant facts are:

1. **Confirmed current:** live `video-workflow-2.0` is at `1906639db42a1874b2481b266b18672523e48bb7`, 115 commits beyond the Issue 27 merge base; the Issue 27 worktree is at `159ce420c583581f85eda0664ac05564648d060b`, 20 commits beyond its remote head and has a four-file tracked dirty optimization patch.
2. **Confirmed historical:** Attempt 8 at Issue 27 commit `159ce42` executed exactly 499 Test IDs across 40 modules. Its persisted command succeeded technically, yet elapsed time was 3,042.781 seconds, which fails Promotion's 1,800-second ceiling.
3. **Confirmed historical:** Attempt 8's four-worker scheduler span was 2,220.644 seconds against 8,696.267 worker-seconds, giving 97.9025% utilization and only a 1.2015-second maximum refill gap. The four-worker lower bound was already 2,174.067 seconds, above the Promotion ceiling before source-freeze, discovery, validation, and persisted-run overhead.
4. **Confirmed current:** the execution unit is one discovered Python test module in one worker process. The runner has no class-level scheduling seam. Unique case/workspace/temp roots and process isolation handle most cross-module state safely.
5. **Unverified for Promotion:** the dirty operation-scoped Control Store optimization reduced `ControlStore.check` calls in focused profiles, while three unprofiled samples varied by 38.61% around their median. It has no clean-source full Promotion run, is absent from Git/PR/live, and its `kernel.py` hunk does not apply cleanly to the live branch.

No current evidence authorizes Promotion, merge of the 20 local Issue 27 commits, transplant of the dirty patch, or a class-level scheduler change.

## 2. Scope and evidence cutoff

This report is a read-only baseline for Wayfinder Issue 45 as of **2026-08-19 Asia/Shanghai**. It covers local Git and code, GitHub Issue 27, PR 28, Issue 45 and parent Issue 44 through `gh`, persisted-command records, and already-existing external-root artifacts. The background research agent did not run the complete suite, Promotion, discovery, or any expensive command and did not alter GitHub or host state. The coordinator separately performed the required Issue 45 claim before research began.

Status vocabulary is used strictly:

| Status | Meaning in this report |
|---|---|
| Confirmed current | Directly observed in the current local filesystem, current Git graph, or current GitHub state at the cutoff. |
| Confirmed historical | Proven by immutable first-party records for the stated commit/run, with no claim that the fact still describes live HEAD. |
| Superseded | A later explicit authority replaces the older claim. |
| Stale or divergent | The evidence is real, while code or branch state has moved beyond it. |
| Unverified | A plausible claim lacks the governed evidence needed for the claimed use. |
| Unavailable | The requested fact cannot be recovered from existing evidence without a prohibited new run or missing terminal record. |

Sensitive values were neither collected nor reproduced. Persisted records used below classify the cited successful runs as `no_secret_detected` and `acceptance_evidence_eligible: true`; that classification does not convert performance failure into Promotion success.

## 3. Current Git and integration topology

| Surface | Confirmed current state | Consequence |
|---|---|---|
| Live worktree | `video-workflow-2.0` at `1906639db42a1874b2481b266b18672523e48bb7`, four commits ahead of `origin/video-workflow-2.0`, dirty/untracked user work present | Live inventory must distinguish clean HEAD from the dirty filesystem. |
| Issue 27 worktree | `codex/issue27-external-root-runner` at `159ce420c583581f85eda0664ac05564648d060b`, 20 commits ahead of `origin/codex/issue27-external-root-runner`, exactly four tracked dirty files | Local committed and dirty work are absent from PR 28 and live. |
| PR 28 remote head | `edfd87eb6fbcd859ffb610e4a193b038f454d010` | GitHub's PR view omits the 20 local commits and dirty prototype. |
| Divergence | live-only 115 commits; Issue-27-local-only 20 commits | Any integration needs semantic rebase/reconstruction, not a blind stale-branch merge. |
| Historical integration | Issue 27 lineage merged through `86a745d7360290ab360bdcbf56f520f64987e9d9` and then `153dd675bf272599760514e981db8306959d267e`; `edfd87e` is an ancestor of live | The remote PR implementation is already in live history despite PR 28 remaining open. |

The 115 live-only commits add Delivery Quality/Global Gate, Bilibili and YouTube cutover work, Batch, validator-fixture governance, and a much larger video-workflow test surface. The 20 Issue-27-local commits center on source snapshot/finalization evidence, Promotion-v2 hardening, and authority refreshes. Four paths changed on both sides after `edfd87e`: `scripts/project_test_run_identity.py`, `scripts/project_test_source_provenance.py`, `tests/project_test_runner/test_cli.py`, and `tests/project_test_runner/test_fixture_root.py`. A read-only `git apply --check` of the 20-commit delta against live found a textual conflict in `test_cli.py`; the other three only passed textual application and still require semantic review.

The four-file dirty patch is:

- `src/video2pdf_workflow_kernel/kernel.py`
- `src/video2pdf_workflow_kernel/resource_admission.py`
- `tests/video_workflow/test_source_reopen_integration.py`
- `tests/video_workflow/test_task_promotion_hardening.py`

Applying the whole dirty patch to live fails at `kernel.py`; the other three files pass textual application individually. This is direct integration drift, not proof that the remaining hunks are semantically valid.

## 4. Issue 27 / ADR 0059 / PR 28 authority status

**Issue 27 remains open and ready for human review. Promotion remains false.** GitHub Issue 27 is `OPEN`, labeled `ready-for-human`, with no assignee or comments. Its body still states an exact 474-Test-ID Promotion condition. That count is stale: ADR 0059's later Promotion-v2 authority defines a 475-ID reviewed baseline plus exactly 24 authorized Option-B IDs, yielding 499 IDs with derived-set SHA-256 `ea008...` (`work/i9/docs/adr/0059-run-project-tests-from-an-external-root-with-bounded-process-parallelism.md:L11-L18 @ 159ce42`; `work/i9/evidence/project-test-runner/promotion-superset-authority.v2.json @ 159ce42`).

ADR 0059 is still `proposed`, records a historical serial baseline of 4,849.187 seconds, and requires two independent successful runs of the exact governed set, each at or below 1,800 seconds (`ADR 0059:L2,L11,L24-L35,L209`). It also separates reviewed implementation commit `I` from evidence commit `E`; Promotion must validate the immutable source/evidence chain rather than accept a mutable checkout.

PR 28 is currently open and draft, base `codex/issue9-multi-section`, head `codex/issue27-external-root-runner`, remote SHA `edfd87e`, reported `CLEAN`/mergeable by GitHub. That mergeability is against a stale base and has no authority over the live branch's 115-commit drift. Its remote head is already an ancestor of live, while local commits through `159ce42` and the dirty prototype are absent. PR 28 therefore presents an incomplete historical view.

Issue 45 is `OPEN`, assigned to `Nishijujuba`, labeled `wayfinder:research`, and linked to parent Issue 44 with no blockers reported. These are workflow-state facts only; they do not grant implementation or Promotion authority.

## 5. Current runner and Promotion contract

The public runner discovers registered suites, freezes a clean source snapshot, independently discovers Test IDs, schedules modules into bounded worker processes, validates exact coverage, and finalizes immutable results. Relevant implementation points are:

- `work/i9/config/test-suites.v1.json:L8-L65 @ 159ce42`: five registered suites and their roots/exclusions.
- `work/i9/scripts/run_project_tests.py:L409-L515,L585-L660 @ 159ce42`: path budget, source manifest/freeze/discovery, scheduling, and success/failure finalization.
- `work/i9/scripts/project_test_discovery.py:L31-L77,L98-L191 @ 159ce42`: TestCase/Test-ID flattening and module grouping.
- `work/i9/scripts/project_test_scheduler.py:L545-L565,L1031-L1160,L1434-L1548 @ 159ce42`: duration/count ordering, bounded queue refill, and fail-closed coverage accounting.

The scheduler chooses **module** as its atomic execution unit. With timing history it orders by descending module duration; without it, by descending Test-ID count. It keeps at most `jobs` module worker processes active and refills from the queue. Final coverage fails closed on missing, duplicated, multiply assigned, or unassigned Test IDs.

Promotion-v2 adds stronger authority constraints. The generator requires a fixed 499-ID set whose 24 authorized additions subtract to the 475-ID reviewed baseline (`work/i9/scripts/generate_project_test_promotion_v2_authority.py:L108-L139 @ 159ce42`). The validator enforces the two-run ceiling, dual-commit model, source snapshot/finalization chain, persisted terminal evidence, exact Test-ID set, and clean-source provenance (`work/i9/scripts/validate_project_test_promotion.py:L2006-L2700,L3535,L3790-L3822,L3908 @ 159ce42`; `work/i9/schemas/project-test-promotion-report.v2.schema.json @ 159ce42`).

An output directory or test summary cannot prove persisted terminal success. The repository contract requires terminal `status.json` plus `exit-code.txt`; `unknown`, `interrupted`, and observer failure remain non-terminal/unverified for acceptance.

## 6. Test inventory and execution-unit baseline

| Suite | Registered root | Module files at Issue 27 `159ce42` | Clean live HEAD `1906639` | Current execution unit |
|---|---|---:|---:|---|
| persisted-command | `tests/persisted_command` | 2 | 2 | module process |
| project-scripts | `scripts` | 2 | 2 | module process |
| project-test-runner | `tests/project_test_runner` | 11 | 11 | module process |
| skill-tests | `.agents/skills` | 17 | 17 | module process; `.claude/skills` mirror excluded |
| video-workflow | `tests/video_workflow` | 40 | 79 | module process |

The dirty live filesystem contains 81 matching video-workflow modules because two are untracked. That is not the clean-HEAD inventory. The exact current live Test-ID count is **unavailable** without a new clean discovery and must not be inferred as 499. The latest complete all-suite discovery found in existing evidence is 1,000 Test IDs at live commit `1d0bc82...`; it predates 115 commits and is stale for current inventory.

At Issue 27 `159ce42`, Attempt 8 confirms 499 IDs across 40 video-workflow modules with no missing, duplicate, unassigned, or multiply assigned IDs (`D:\tests\video2pdf\video-workflow\20260801_034136_0360e06c\discovery.json` and `summary.json`). This is a historical inventory bound to that source commit.

The worker fixture contract allocates a generated module root under the external run directory, then unique UUID case/workspace roots. Direct invocation retains a compatibility fallback under repository-local `待删除` (`work/i9/tests/video_workflow/_test_run.py:L1-L5,L39-L53,L81-L150`; `work/i9/tests/project_test_runner/_fixture_root.py:L1-L5,L40-L108`). Child `TEMP`, `TMP`, and `TMPDIR` are pinned to the case root. There is no class-level scheduling API or registry field.

## 7. Persisted-run evidence ledger

The investigation began persisted evidence recovery with `scripts/persisted_command.py list`, then used `show` only for relevant existing runs. No run was restarted. The ledger preserves the difference between target technical success and Promotion success.

| Evidence | Persisted `run_dir` | Terminal proof | Elapsed | Promotion interpretation |
|---|---|---|---:|---|
| Attempt 7 run 1 | `work/i9/待删除/long-running/issue27_formal_promotion_attempt7_e_159ce42_run1_20260801_100102_1a9ffe52` | `succeeded`, exit 0, eligible | 4,439.578 s | Exact technical run succeeded; exceeds 1,800 s. |
| Attempt 7 run 2 | `work/i9/待删除/long-running/issue27_formal_promotion_attempt7_e_159ce42_run2_20260801_100112_ceeb8f2a` | `succeeded`, exit 0, eligible | 4,436.313 s | Exact technical run succeeded; exceeds 1,800 s. |
| Attempt 8 run 1 | `work/i9/待删除/long-running/issue27_formal_promotion_attempt8_e_159ce42_run1_20260801_113901_5c75384c` | `succeeded`, exit 0, eligible | 3,042.781 s | Exact 499-ID run succeeded; exceeds 1,800 s. No run 2 exists. |
| Stage 97 single measurement | `work/i9/待删除/long-running/issue27_stage97_single_499_performance_measurement_20260728_101926_4dfbda5b` | `succeeded`, exit 0 | 612.765 s | Predates final Promotion-v2 evidence/commit shape; cannot authorize Promotion. |
| Full all-suite v4 | `待删除/long-running/issue11_full_project_tests_final_v4_20260731_165000_807bfc1c` | `failed`, exit 1 | 8,580 s | Historical 1,000-ID run failed. |
| Full all-suite v3 | `待删除/long-running/issue11_full_project_tests_final_v3_20260731_164610_efb13cc3` | `failed`, exit 1 | 188.281 s | Early failure; no performance conclusion. |
| Issue 42 full-suite v2 | `待删除/long-running/issue42_final_full_project_tests_v2_20260730_173551_9e0897e1` | `unknown`; no `exit-code.txt`; `status_publication_failed` | unavailable | Artifacts cannot establish terminal outcome. |

The Attempt 8 external runner directory is `D:\tests\video2pdf\video-workflow\20260801_034136_0360e06c`. Its `discovery.json`, `summary.json`, `timings.json`, `events.jsonl`, and `run-finalization.json` are historical first-party execution evidence. None overrides the persisted elapsed ceiling failure.

## 8. Performance decomposition

Attempt 8 provides the most relevant complete decomposition at 499 IDs/40 modules:

| Component | Value | Interpretation |
|---|---:|---|
| Sum of module worker durations | 8,696.267 s | Total parallelizable module work. |
| Four-worker work lower bound | 2,174.067 s | `worker_sum / 4`; already 374.067 s above the 1,800-s ceiling. |
| First module start to last completion | 2,220.644 s | Scheduler span. |
| Worker utilization | 97.9025% | `worker_sum / (4 * scheduler_span)`; workers were nearly saturated. |
| Maximum refill gap | 1.2015 s | Queue refill latency is negligible relative to module work. |
| Persisted end-to-end elapsed | 3,042.781 s | About 822.137 s beyond scheduler span, including freeze/discovery and pre/postvalidation/persisted overhead. |

The four largest modules alone were `test_task_promotion_hardening` 1,382.641 s, `test_issue5_review_repairs` 916.359 s, `test_resource_admission` 840.562 s, and `test_control_store_recovery` 613.187 s. The next six were 528.984, 459.235, 429.266, 367.500, 326.344, and 323.593 seconds. Evidence: `D:\tests\video2pdf\video-workflow\20260801_034136_0360e06c\timings.json` and `events.jsonl`.

This establishes two bottleneck layers:

1. **Worker work and long-module imbalance:** even ideal four-way packing cannot meet 1,800 seconds at the observed worker sum. The 1,382.641-second largest module also constrains the critical path.
2. **Non-scheduler overhead:** the approximately 822-second difference needs phase-timestamp attribution before optimization. It cannot be assigned wholly to any single stage from current evidence.

Increasing queue-refill sophistication has little supported upside. Increasing process count could lower the arithmetic bound, while SQLite/filesystem/subprocess contention and host variance may rise. The safe next path is phase instrumentation using existing event/finalization boundaries, focused repeated-validation removal, and module-specific profiling under clean-source evidence. A complete Promotion remains the only acceptance proof.

## 9. Resource and concurrency hazard inventory

The table separates scheduler isolation from within-module semantics. “Parallel-safe” means provisionally safe at the current module-process boundary; it is not a promise for class/test splitting or simultaneous independent runner invocations.

| Resource/hazard | Current evidence | Module-process classification | Class/test split implication |
|---|---|---|---|
| Generated roots and temp files | UUID case/workspace paths; child temp variables pinned (`_test_run.py:L81-L150`) | **Parallel-safe** between modules | Safe only if each split retains unique identity and containment. |
| Process environment and cwd | Tests mostly copy environments or use scoped `mock.patch.dict`; child `cwd` is process-local | **Parallel-safe** | Same-process class concurrency would make environment patches shared and requires investigation. |
| SQLite Control Stores | Many modules create `.workflow-control/control.sqlite3` under unique workspaces; some intentionally exercise contention | **Parallel-safe** between unique module roots; host I/O contention remains | **Requires investigation**; do not split tests that intentionally share a store or transaction sequence. |
| Internal threads/barriers | Multiple modules deliberately test races with threads/events/executors | **Parallel-safe** because contained inside a module worker | **Requires investigation** for test-level overlap and timing assumptions. |
| Multiprocess admission tests | UUID operation dir; controlled child collection/termination (`test_resource_admission_multiprocess.py:L53-L106`) | **Parallel-safe with contention risk** | Process-tree cleanup and shared-store assumptions require classification. |
| Subprocess-heavy tests | Numerous CLI/validator/persisted-process launches under generated roots | **Parallel-safe with capacity risk** | More workers amplify process startup, memory, antivirus, and I/O pressure; measure before raising jobs. |
| Network and fixed ports | No registered runtime listener/fixed-port use found; one test explicitly patches `socket.socket` to forbid network (`test_source_ready_tracer.py:L679-L680`) | **Parallel-safe, static-scan confidence** | Recheck only if a supported test begins real network use. |
| `setUpClass` state | Four modules: `test_issue4_gate7.py:L92`, `test_legacy_baseline.py:L74`, `test_source_candidates.py:L196`, `test_source_reopen_contract.py:L35` | **Parallel-safe** as module units | **Requires investigation**; splitting can duplicate expensive/shared class setup or alter order. |
| Module globals | `_test_run.py` freezes environment and records paths in process-local globals (`L32-L33`) | **Parallel-safe** at module-process boundary | New workers change initialization frequency; same-process concurrency shares state. |
| Repository-local write exceptions | Two exact Issue-7 path-boundary cases remain in `config/test-local-write-exceptions.v1.json:L1-L18` | **Exclusive across simultaneous whole-runner instances** because fixed repository paths can collide | Keep isolated or migrate paths; registry exceptions must stay exact and reviewed. |
| Persisted supervisor/process lifecycle | Persisted-command tests spawn supervisors/observers and retain run evidence | **Requires investigation** for abnormal worker death and orphan behavior | Avoid finer splitting until cleanup/ownership is proven under failure. |

No actual `os.chdir` mutation was found in registered tests. No supported real network listener was found. These areas are currently correct at module-process granularity and should not be turned into speculative hardening work.

The Validator Fixture Evolution Standard constrains any attempt to change validator concurrency or fixtures: preserve the dependency graph, create one intended contradiction, assert the first failing gate, rematerialize downstream artifacts, and treat interrupted/unknown complete runs as unverified (`docs/testing/validator-fixture-evolution.md:L3,L50-L71,L114,L137-L139 @ live 1906639`).

## 10. Dirty operation-scoped optimization status

The dirty prototype creates an operation-scoped Control Store preflight cache using a `ContextVar`, decorates public kernel operations, and lets resource-admission paths reuse `_preflight_control_store` inside the outer operation (`work/i9/src/video2pdf_workflow_kernel/kernel.py:L77-L88,L199+,L1913-L1920`, dirty relative to `159ce42`). Tests were added in `test_task_promotion_hardening.py:L174,L188,L212,L235,L660` and `test_source_reopen_integration.py:L23`.

Focused profiles show a real repeated-validation target:

| Profile/sample | Persisted or profiler result | Relevant calls |
|---|---:|---|
| Before scope prototype | 192.370728 s profiler total | `ControlStore.check`: 902 calls / 77.1148 s cumulative; `contracts.validate`: 9,423 / 49.6304 s. |
| After earlier scope | 167.510991 s profiler total | `check`: 674 / 55.2763 s; `validate`: 8,083 / 42.8467 s. |
| Final public operation scope | 327.219620 s profiler total; persisted 330.640 s | `check`: 160 / 32.5233 s; `validate`: 4,949 / 84.9851 s. |
| Unprofiled sample 1 | persisted 147.891 s | focused subset only |
| Unprofiled sample 2 | persisted 235.625 s | focused subset only |
| Unprofiled sample 3 | persisted 238.875 s | focused subset only |

The unprofiled range divided by median is 38.6139%, showing material host variance. The call reduction supports further controlled investigation; the elapsed results do not establish a stable speedup. The final public-scope profile also shows schema validation remaining expensive even after health-check calls fall.

This prototype is **unverified** for integration and Promotion: the worktree is dirty; clean-source preflight therefore blocks a formal runner; no complete 499-ID run exists with the patch; no second qualifying run exists; the patch is absent from PR 28 and live; and its main `kernel.py` hunk conflicts textually with live. The committed `optimization-safety-review.v1.json` covers an earlier committed Option-B optimization at reviewed source `40a9a08...` and a 76-test focused review. It does not authorize this current dirty operation-scope patch.

## 11. Facts that remain valid after integration drift

The following design facts remain usable constraints because they are present in the live lineage or describe stable runner contracts:

- External-root execution and generated unique case/workspace/temp containment are established mechanisms.
- Module-process scheduling is the only implemented execution granularity.
- Exact discovered Test-ID coverage must fail closed on missing, duplicate, multiply assigned, or unassigned IDs.
- Persisted terminal success requires `status.json` plus `exit-code.txt`; artifacts alone never establish terminal success.
- Promotion requires clean immutable source, a governed Test-ID set, two independent accepted runs, and each run at or below 1,800 seconds.
- Module-level process isolation is the current safety boundary for environment mutations, globals, SQLite workspaces, subprocesses, and intentionally concurrent tests.
- Attempt 8 is valid historical evidence that refill was efficient and worker work exceeded the four-worker performance budget at Issue 27 `159ce42`.
- The validator fixture evolution rules remain active repository governance on live HEAD.

These facts constrain later tickets. They do not prove the live branch's current Test-ID count, module timings, or Promotion readiness.

## 12. Facts that are stale, superseded, divergent, or unverified

| Claim/evidence | Status | Reason |
|---|---|---|
| Issue 27 body requires exactly 474 IDs | **Superseded** | Promotion-v2 authority defines 475 baseline + 24 authorized = 499. |
| Attempt 8 contains the current live test set | **Stale/divergent** | It is bound to `159ce42`; live has 115 divergent commits and 79 clean video-workflow modules. |
| Existing 1,000-ID all-suite discovery is current | **Stale/divergent** | It is bound to `1d0bc82...` and its persisted run failed. |
| PR 28 `CLEAN` means ready to merge to live | **Stale/divergent** | GitHub compares against its old base; its remote head is already in live and omits 20 local commits. |
| Stage 97's 612.765 seconds proves Promotion performance | **Superseded/unverified** | It predates final Promotion-v2 source/evidence shape and supplies only one run. |
| Attempt 7/8 technical success proves Promotion | **Incorrect** | All three persisted elapsed times exceed 1,800 seconds; Attempt 8 has no second run. |
| Issue 42 full-suite artifacts prove success | **Unavailable** | Persisted state is `unknown`, `exit-code.txt` is absent, and status publication failed. |
| Dirty operation scope is a proven optimization | **Unverified** | Focused call reductions coexist with high runtime variance; no clean full run or live integration exists. |
| Class-level scheduling is a safe incremental extension | **Unverified** | No seam exists; class fixtures, globals, shared stores, threads, and process lifecycle require classification. |
| Exact current live Test-ID count | **Unavailable** | No existing clean discovery matches `1906639`; a new run was outside this ticket's authority. |

## 13. Constraints handed to later Wayfinder tickets

1. **Rebase/integration ticket:** reconstruct the 20 committed Issue 27 changes against live, review the four overlapping paths semantically, and keep the dirty optimization separate. Do not treat PR 28 mergeability as live integration evidence.
2. **Inventory ticket:** obtain a clean live source SHA, run governed discovery from an external root through persisted execution, and publish exact suite/module/Test-ID counts. Dirty/untracked tests must be excluded or intentionally committed before authority is claimed.
3. **Performance attribution ticket:** instrument or derive freeze, discovery, scheduling, finalization, and validation phase durations from existing stable event identities. The check must explain the approximately 822-second Attempt 8 non-scheduler interval before optimizing it.
4. **Repeated-validation ticket:** preserve fail-closed semantics while testing operation-scoped health-check reuse and schema-validation reductions. Use clean commits, focused regression tests, and repeated unprofiled samples before a complete 499/current-set run.
5. **Long-module ticket:** profile the four dominant modules first. Any split must preserve fixture dependency graphs, first-failing-gate identities, class/module setup semantics, intentional shared-store contention, and exact Test-ID coverage.
6. **Concurrency-cap ticket:** measure higher `jobs` only after resource classification. Record CPU, disk/SQLite contention, child-process count, memory, worker utilization, and critical path. A higher process cap is not safe merely because arithmetic division predicts a lower bound.
7. **Class-level seam ticket:** treat class/test scheduling as a new design decision. Define discovery identity, fixture lifecycle, ordering, worker ownership, cleanup, and timing-history keys before implementation.
8. **Promotion ticket:** require two independent clean persisted terminal successes for the exact current governed set, each within 1,800 seconds. No focused sample, artifact-only result, or observer timeout may substitute.
9. **Repository-local exception ticket:** either serialize simultaneous whole-runner instances around the two fixed Issue-7 repository paths or migrate those tests to unique external roots and update the exact exception authority.

## 14. Sources

### Repository and governance

- Live source: `D:\Project\video2pdf\newskill-kimi`, `video-workflow-2.0`, SHA `1906639db42a1874b2481b266b18672523e48bb7`.
- Issue 27 worktree: `D:\Project\video2pdf\newskill-kimi\work\i9`, `codex/issue27-external-root-runner`, SHA `159ce420c583581f85eda0664ac05564648d060b`.
- `AGENTS.md`; `docs/agents/issue-tracker.md`; `docs/agents/domain.md`; `CONTEXT-MAP.md`; `docs/contexts/project-governance/CONTEXT.md`; `docs/contexts/video-workflow/CONTEXT.md` at live SHA `1906639`.
- `work/i9/docs/adr/0059-run-project-tests-from-an-external-root-with-bounded-process-parallelism.md:L2,L11-L18,L24-L35,L209 @ 159ce42`.
- `docs/testing/validator-fixture-evolution.md:L3,L50-L71,L114,L137-L139 @ 1906639`.
- `work/i9/evidence/project-test-runner/promotion-superset-authority.v2.json @ 159ce42`.
- `work/i9/evidence/project-test-runner/optimization-safety-review.v1.json @ 159ce42`.
- Runner/config citations listed in Sections 5, 6, 9, and 10.

### GitHub read state

- [Issue 27](https://github.com/Nishijujuba/video2pdf/issues/27)
- [PR 28](https://github.com/Nishijujuba/video2pdf/pull/28)
- [Issue 45](https://github.com/Nishijujuba/video2pdf/issues/45)
- [Parent Issue 44](https://github.com/Nishijujuba/video2pdf/issues/44)

All GitHub reads were performed through `gh`; no GitHub write occurred.

### Persisted and external execution records

- Attempt 7/8 and profiling persisted `run_dir` values are listed in Sections 7 and 10; each result was recovered via `persisted_command.py list` followed by relevant `show` operations.
- Attempt 8 external evidence: `D:\tests\video2pdf\video-workflow\20260801_034136_0360e06c\{discovery.json,summary.json,timings.json,events.jsonl,run-finalization.json}`.
- Historical all-suite discovery/run: `D:\tests\video2pdf\all\20260731_085259_5cd61d6a` and persisted `待删除/long-running/issue11_full_project_tests_final_v4_20260731_165000_807bfc1c`.

No secret, raw credential, cookie, authorization header, or credential-bearing URL is included in this report.

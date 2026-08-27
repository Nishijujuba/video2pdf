# Inventory of post-release admission checks and their authority lifetime

## 1. Executive conclusion

Ordinary Bilibili and YouTube startup currently makes completed-release evidence part of live admission. The render skills require `workflow-policy-check` before `init-run`; that policy command revalidates the Global Gate release manifest and each platform's complete historical cutover evidence, while `init-run` independently repeats the platform authority check. Those checks can fail because an old Exit Evidence path, mirror, qualification binding, persisted log, publication lineage, or artifact fingerprint is unavailable even though the releases are completed facts.

The investigation found **8 Completed Release Proof checks, 8 Live Runtime Coordination checks, 8 Current PDF Quality checks, and 3 Mixed or Misplaced Authority checks**. The first group is candidate material to move out of ordinary single-video admission. The second and third groups remain required. The mixed checks require later tickets to choose a replacement release signal and a new startup interface; this ticket does not choose that design.

The repository's own active language records all four cutovers as completed: Global Gate, Bilibili, YouTube, and Batch (`docs/adr/video-workflow-kernel-2.0-decision-map.md:149-157`). Live GitHub status agrees: [Activate the global Acceptance Report v2 gate](https://github.com/Nishijujuba/video2pdf/issues/43), [Cut Bilibili delivery over to Delivery Quality Kernel authority](https://github.com/Nishijujuba/video2pdf/issues/13), [Cut YouTube delivery over through the shared Kernel](https://github.com/Nishijujuba/video2pdf/issues/14), and [Replace Legacy Batch with projections over guarded single-video Runs](https://github.com/Nishijujuba/video2pdf/issues/15) are closed. [Restore Global Gate Exit Evidence schema consistency after qualification binding expansion](https://github.com/Nishijujuba/video2pdf/issues/56) and [Refresh stale Global Gate policy authority after canonical branch consolidation](https://github.com/Nishijujuba/video2pdf/issues/57) remain open; their final disposition belongs to a later Wayfinder ticket.

## 2. Scope and authority-lifetime model

This report follows the public interface used by a normal new task. It does not inventory unreachable helpers merely because they contain words such as `current`, `stale`, or `authority`.

| Lifetime | Meaning | End condition |
|---|---|---|
| Completed Release Proof | Proves that a cutover publication once satisfied its release contract. | The cutover becomes an accepted repository release fact; evidence remains audit material. |
| Live Runtime Coordination | Prevents two current actors from owning the same Run, path, Claim, Lease, Promotion, mutation, or delivery projection. | The governed transaction or Run is terminal and its ownership is released or archived. |
| Current PDF Quality | Proves that the current source, TeX, PDF, rendered pages, semantic judgment, and delivery target still agree. | The exact PDF generation is delivered and archived; any artifact mutation invalidates dependent evidence. |
| Mixed or Misplaced Authority | One interface combines two lifetimes or promotes cleanable machine-local history into ordinary admission authority. | A later design ticket separates the interface and assigns each check to its proper lifecycle. |

The domain model already distinguishes these owners. The Video Workflow Run Record owns per-run identity, checkpoints, Artifact Generations, and delivery references (`docs/contexts/video-workflow/CONTEXT.md:37-43`). The Cross-Run Control Store owns claims, leases, scheduling, publication slots, and Mutation Intents (`docs/contexts/video-workflow/CONTEXT.md:231-237`). Delivery Quality owns semantic policy, while the Delivery Guard owns mechanical freshness and no semantic judgment (`docs/contexts/delivery-quality/CONTEXT.md:9-13`; `docs/contexts/video-workflow/CONTEXT.md:499-505`).

## 3. Verified release-status facts

1. The canonical decision map says the Global Gate, both platform kernels, and Batch cutovers are completed (`docs/adr/video-workflow-kernel-2.0-decision-map.md:149-157`).
2. The active glossary says Bilibili and YouTube are `active_kernel`, the Global Gate is `active_global_gate`, and only existing output directories remain Legacy (`docs/contexts/video-workflow/CONTEXT.md:3-7`; `docs/contexts/delivery-quality/CONTEXT.md:1-7`).
3. `CONFIRMED` was the release-time event that opened ordinary platform admission. `PREPARED`, `INITIALIZED`, and `PROVISIONAL` were candidate-only states (`docs/adr/0040-cut-over-to-the-kernel-one-platform-at-a-time.md:20-44`; `docs/contexts/video-workflow/CONTEXT.md:61-73`). Requiring the candidate lifecycle to remain reconstructable for every later Run therefore proves a completed release again.
4. The local machine's ignored authority JSON files contain absolute paths to this checkout for the Global Gate, Bilibili, YouTube, and Batch Exit Evidence. The writers persist `str(evidence_path)` (`src/video2pdf_workflow_kernel/platform_kernel.py:2895-2906`, `3498-3513`), and ordinary checks later dereference those paths (`src/video2pdf_workflow_kernel/platform_kernel.py:3662-3682`). Their lifetime is consequently tied to one machine layout.
5. The remote branch under investigation is `origin/video-workflow-2.0` at `ff64199ba7249d98e9c1bce96ab198088c3400d9`. The main checkout has one unpushed local commit, `09bc30db945c4ed26098ba0c2aeab6995bb39f31` (`feat: add configurable evidence freshness switch`). That candidate adds an ignored project-local `workflow-policy-config.json` switch, defaults missing configuration to revalidation enabled, and bypasses only historical Exit Evidence revalidation when disabled. It is **not published remote behavior** and the local configuration file is absent, so it currently changes no ordinary task.
6. Batch authority is exposed through the separate `batch-authority-check` command (`src/video2pdf_workflow_kernel/cli.py:1017-1026`). Neither render skill nor `workflow-policy-check` calls it. It is not reachable in ordinary single-video startup.

## 4. Ordinary new-Run admission call graph

```text
youtube-render-pdf / bilibili-render-pdf
  -> workflow-policy-check
       -> GlobalGatePublisher.check_policy
            -> require_current(base Global Gate JSON + SQLite row)
            -> policy refresh intent/authority, or base Exit Evidence
            -> full Global Gate manifest validation
       -> for Bilibili and YouTube when platform DB has state
            -> BilibiliPlatformCutoverPublisher.check_policy
                 -> require_current(authority JSON + SQLite row + candidate/intents)
                 -> full platform Exit Evidence and post-publication validator
       -> otherwise reports active_legacy for a platform with no control presence
  -> bootstrap-probe
       -> VideoWorkflowKernel + ControlStore initialization/health
       -> deterministic source locator and immutable probe
  -> init-run
       -> ControlStore.initialize
       -> parse and validate probe
       -> platform require_current (repeats platform release proof)
       -> initialize_production_source
            -> output-path/run binding + initialization Mutation Intent
            -> Run Record and initial delivery projections at generating
  -> source-acquire / task claim / Resource Lease / task completion / promotion
  -> production-plan / production-advance
  -> guarded-compile and Delivery Quality final evidence
  -> acceptance-prepare -> reviewer Patch -> acceptance-patch-commit
       -> acceptance-materialize -> delivery-acceptance-bind
  -> delivery-transition generating -> ready_for_delivery -> accepted
  -> fresh delivery_guard.py check
  -> delivery-transition accepted -> delivered -> delivery-archive
```

The skill entrypoints explicitly require `workflow-policy-check`, then ordinary `init-run`, and forbid a Legacy redirect (`.agents/skills/youtube-render-pdf/SKILL.md:28-36`; `.agents/skills/bilibili-render-pdf/SKILL.md:40-48`). `workflow-policy-check` is implemented at `src/video2pdf_workflow_kernel/cli.py:1027-1048`; production `init-run` is at `src/video2pdf_workflow_kernel/cli.py:1897-1932`.

## 5. Complete check inventory

“Machine path” means the decision can change solely because a local absolute path moved. “Historical dependency” identifies checks that read prior cutover evidence or its retained execution records.

| Check | Public entrypoint | Implementation location | State or evidence read | Failure detected | Failure action | Authority owner | Authority lifetime | Machine path / historical dependency | Ordinary-run reachability | Classification | Recommended lifecycle placement |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CRP-1 Base Global Gate publication current | `workflow-policy-check` | `global_gate.py:617-637`, called by `1128-1131` | `global-gate-control.sqlite3`, `active_global_gate.json`, pending activation intents | Missing row/file, unfinished publication, JSON/row fingerprint conflict | Reject policy check (`global_gate_authority_stale` or `global_gate_authority_conflict`) | Global Gate Cutover | Release lifetime | Local control root; no old logs | Yes | Completed Release Proof | Repository release verification or explicit maintenance, outside each Run |
| CRP-2 Global Gate Exit Evidence path and bytes | `workflow-policy-check` | `global_gate.py:1157-1164` | Absolute `exit_evidence_path`, stored SHA, manifest bytes | Old manifest removed, relocated, or changed | Reject (`global_gate_exit_evidence_stale`) | Global Gate Cutover | Release lifetime | **Yes / yes** | Yes | Completed Release Proof | Release audit or authority maintenance |
| CRP-3 Global Gate manifest activation, commands, results, and qualification bindings | `workflow-policy-check` | `global_gate.py:56-130` | v2 schema, activation scope, atomic members, command results, unresolved exceptions, result bindings | Historical cutover package no longer reproduces the exact qualification contract | Reject with schema, atomic-member, contract-gap, or qualification-binding error | Global Gate Cutover | Release lifetime | Historical manifest and qualification facts | Yes | Completed Release Proof | Release publication validation |
| CRP-4 Global Gate mirror equality | `workflow-policy-check` | `global_gate.py:86-102` | Historical manifest mirror paths and live source/mirror bytes | A managed copy moved or changed after release | Reject (`global_gate_mirror_stale`) | Global Gate Cutover | Release lifetime | **Yes / yes** | Yes | Completed Release Proof | Maintenance command for managed mirrors |
| CRP-5 Global Gate policy refresh publication and evidence | `workflow-policy-check` | `global_gate.py:745-783`, `1128-1156` | `active_global_gate_policy.json`, policy DB row, refresh intent, Exit Evidence publication identity | Refresh incomplete; overlay bytes, lineage, or evidence changed | Reject (`global_gate_policy_reconcile_required`, `*_stale`, or conflict) | Global Gate policy overlay | Maintenance publication lifetime | **Yes / yes** | Yes when overlay exists; pending intent always checked | Completed Release Proof | Explicit refresh/reconcile interface |
| CRP-6 Platform Exit Evidence path and bytes | policy check and `init-run` | `platform_kernel.py:3615-3682` | platform authority JSON/row and absolute Exit Evidence path/SHA | Slice 12/13 evidence moved or bytes changed | Reject platform authority | Platform Kernel Cutover | Release lifetime | **Yes / yes** | **Twice** | Completed Release Proof | Platform release audit or authority maintenance |
| CRP-7 Full platform post-publication validation | policy check and `init-run` | `platform_kernel.py:154-179`, `260-450`, `452-566`, `3681-3682` | manifest lineage, publication Git blobs, historical qualification Run/status/log bindings, guarded candidate delivery artifacts, fingerprints | Historical cutover can no longer be fully replay-validated from retained paths and Git history | Reject (`*_exit_evidence_lineage_invalid` or platform conflict) | Platform Kernel Cutover | Release lifetime | Git history plus old local records and artifacts | **Twice** | Completed Release Proof | Release qualification/audit command |
| CRP-8 Batch cutover authority and Exit Evidence | `batch-authority-check` | `cli.py:1017-1026`; `batch_authority.py:1044-1112` | Batch JSON/SQLite authority, Slice 14 evidence and bindings | Batch release authority stale | Reject batch startup/maintenance | Batch Cutover | Batch release lifetime | Absolute local paths and historical evidence | **No** for an ordinary single-video Run | Completed Release Proof | Batch-only admission or maintenance; never single-video startup |
| LRC-1 Control Store identity and health | kernel construction, probe, `init-run`, every mutation | `control_store.py:411-520`, `762-820`, `4638-4774` | anchor, marker, SQLite DB, schema, PRAGMAs, integrity, foreign keys, tables, active slots, lock and atomic-replace probes | Shared coordination state absent, incomplete, corrupt, incompatible, or not safely writable | Enter Control Store Unavailable; block governed mutations | Cross-Run Control Store | Current shared-state lifetime | Local path by design; no release logs | Yes | Live Runtime Coordination | Keep before initialization and every governed mutation |
| LRC-2 Unique Run and output-path binding | `init-run` | `kernel.py:623-665`; `control_store.py:4834-4936` | Run binding, output-path binding, initialization intent | Duplicate Run/path, conflicting reuse, or unowned output path | Reject or reconcile exact existing intent | Cross-Run Control Store | Run initialization transaction | Local workspace path; current rows | Yes | Live Runtime Coordination | Keep at initialization seam |
| LRC-3 Initialization publication Saga | `init-run`, `reconcile-run` | `kernel.py:704-756`, `1113-1208`; `control_store.py:4937-5046` | PREPARED/PUBLISHED/RECORD_COMMITTED/COMMITTED intent, staged/canonical Run SHA | Partial multi-file publication or conflicting successor | Resume exact intent or fail closed | Run initialization Mutation Intent | Until committed/aborted | Current staging, Run Record and SQLite rows | Yes | Live Runtime Coordination | Keep in initialization/reconciliation |
| LRC-4 Run Record identity, revision, checkpoints and Artifact Generations | Run operations | `kernel.py:1652-1827`, `2582-2621`, `2657-2728` | `workflow/run.json`, Control Store predecessor SHA, Run/path/platform/source identity, generations/checkpoints | Drift, stale revision/checkpoint, orphaned commit, wrong path owner | Reject mutation or reconcile the known intent | Video Workflow Run Record + Control Store | Current Run lifetime | Current Run paths; no cutover logs | Yes after init | Live Runtime Coordination | Keep on every Run mutation |
| LRC-5 Task Claim, Attempt generation and fencing | task claim/reclaim/complete | `task_execution.py:1850-1978`; `control_store.py:7243-7953` | task envelope, Claim authority, attempt/generation, worker/session, write set, fencing token | Duplicate/stale worker or overlapping write authority | Deny claim/commit; require reclaim evidence | Cross-Run Control Store | Task attempt lifetime | Current Run/SQLite state | Yes | Live Runtime Coordination | Keep at task interface |
| LRC-6 Resource admission, Lease, circuit breaker and unknown execution | task launch/reconcile/release | `control_store.py:6287-7242` | capacity configuration, queue, Lease, terminal proof, circuit breaker | Capacity exceeded, unknown worker, source fault domain paused, stale release | Queue/block; retain unknown Lease; release only with terminal evidence | Resource Admission Module | Physical execution lifetime | Current SQLite and persisted terminal proof | Yes | Live Runtime Coordination | Keep around provider execution |
| LRC-7 Completion, Promotion and Mutation Intent fencing | task complete/promote/reconcile | `task_execution.py:2632-2710`, `2929-3415`, `3533-3686`; `control_store.py:7954-8708` | completion authority, expected Run SHA/revision, promotion intent, staging inventory, publication slot | Stale attempt, conflicting generation, overlapping promotion, partial publication | Reject or reconcile same intent | Run Record + Cross-Run Control Store | Promotion transaction | Current attempt/Run/SQLite state | Yes | Live Runtime Coordination | Keep at promotion seam |
| LRC-8 Delivery ownership and lifecycle CAS | `delivery-transition`, handoff, archive | `delivery_lifecycle.py:600-670`, `970-1700` | session owner, ownership generation, Run revision, legal stage, projection bindings and publication slots | Wrong owner, stale revision, illegal transition, competing projection mutation | Reject (`delivery_lifecycle_fence_lost` etc.) or reconcile | Run Record + delivery Mutation Intent | Current delivery lifecycle | Current Run/project projections | Yes | Live Runtime Coordination | Keep through archive |
| CPQ-1 Current Source Package | `source-acquire`, production/task/compile entrypoints | `kernel.py:2595-2621`, `2657-2894`; `source_acquisition.py:800-841` | source state/epoch, manifest, selected assets, credential-resolution evidence, source checkpoint and fingerprints | Missing, stale, incomplete, or identity-mismatched source | Block production/compile; preserve same Run for recovery | Source Acquisition Module + Run Record | Current source generation | Current Run files | Yes after init | Current PDF Quality | Keep; source faithfulness depends on it |
| CPQ-2 Precompile semantic quality and Pyramid authority | production quality commands | `docs/contexts/delivery-quality/CONTEXT.md:47-65`; provider entrypoints in `cli.py:1240-1398` | Reader-Facing Text Inventory, Source-Faithfulness, Writing Quality, Pyramid judgments, glossary, contract gaps | Current draft lacks complete passing semantic coverage | Block sealing/final compile; route repair or human contract-gap decision | Delivery Quality primary semantic owners | Current draft generation | Current reports | Yes in production | Current PDF Quality | Keep unchanged |
| CPQ-3 Final Artifact Seal and compile-input closure | final-evidence/compile commands | `docs/contexts/delivery-quality/CONTEXT.md:67-75`; `final_compile.py:434-798` | current precompile seal, exact Compile Manifest, sealed generations, adapter identity | Compile inputs or semantic predecessor changed | Block final compile/materialization | Delivery Quality final evidence provider | Current TeX/input generation | Current Run files | Yes before final compile | Current PDF Quality | Keep unchanged |
| CPQ-4 Guarded final compile provenance | `guarded-compile` / final compile | `cli.py:1399-1445`; `guarded_compile.py:174-500`; render skills at YouTube `527`, Bilibili `623` | validated source package, compile manifest/runtime policy, TeX closure, final PDF, final compile report | Undeclared input, stale source, unsafe/invalid compile contract, failed compile, stale PDF provenance | Fail compile; no delivery authority | Guarded Compile / Final Compile | Current PDF generation | Current paths and fingerprints | Yes | Current PDF Quality | Keep unchanged |
| CPQ-5 Rendered-page coverage and freshness | final evidence / acceptance input | `docs/contexts/video-workflow/CONTEXT.md:403-413`; `guarded_delivery.py:15-24` | final PDF SHA, page count, every rendered page and fingerprint, allowed artifact manifest | Missing page, stale render, incomplete allowlist | Block Acceptance/Guard | Render Evidence Manifest + workflow | Current PDF generation | Current rendered pages | Yes | Current PDF Quality | Keep every-page coverage |
| CPQ-6 Acceptance v2 preparation, Reviewer Claim/Patch and materialization | `acceptance-prepare`, `acceptance-patch-commit`, `acceptance-materialize` | `cli.py:1213-1239`; `acceptance_v2.py:386-579`, `580-754`, `755-1098` | exact read/write sets, skeleton/envelope, execution revision, Claim generation/token, committed Patches, policy/input fingerprints | Unauthorized/stale Reviewer output, incomplete dimensions, current artifact or policy drift | Fence Patch, require reconcile, or materialize fail/repair routing | Delivery Quality / Acceptance v2 provider | Current acceptance attempt | Current acceptance workspace | Yes | Current PDF Quality | Keep unchanged |
| CPQ-7 Current passing Acceptance Report v2 at `accepted` | `delivery-transition` | `delivery_lifecycle.py:337-387`; `guarded_delivery.py:33-61` | report schema/status/routing, Run id/revision, fingerprints, committed ready successor | Absent, failing, stale, or uncommitted semantic decision | Reject `accepted` transition | Acceptance Report v2 | Current PDF and Run revision | Current report and Run | Yes | Current PDF Quality | Keep unchanged |
| CPQ-8 Delivery Guard and delivered lifecycle | `delivery_guard.py check`, then `delivery-transition` | `guarded_delivery.py:64-120`; `delivery_lifecycle.py:388-420`; render skills YouTube `575-601`, Bilibili `674-700` | target, manifest, final compile, Acceptance v2, pages, artifact fingerprints, Global Gate binding, current stage | Any mechanical or semantic binding stale; lifecycle not accepted | Guard fails; reject `delivered`; delivery remains blocked | Delivery Guard + Run delivery lifecycle | Current exact delivery | Current report/targets/artifacts | Yes | Current PDF Quality | Keep through `generating -> ready_for_delivery -> accepted -> delivered` |
| MIX-1 Aggregate `workflow-policy-check` | render-skill startup | `cli.py:1027-1048` | Global Gate release package; platform release packages; default `active_legacy` statuses | Any historical package fails; missing platform control state silently becomes Legacy in output | Fail all startup, or report a Legacy fallback status | Multiple cutover owners | Mixed release/startup | Yes / historical | Yes | Mixed or Misplaced Authority | Later ticket should separate release maintenance from ordinary admission and remove new-task Legacy ambiguity |
| MIX-2 Platform `require_current` | policy check and `init-run` | `platform_kernel.py:3615-3703`; `cli.py:1911-1915` | current authority row/JSON, pending intents, candidate state, old Exit Evidence and full validator | Either live publication conflict or historical evidence decay | Same platform rejection for both lifetimes | Platform cutover publication + ordinary Run admission | Mixed | Yes / historical | Twice | Mixed or Misplaced Authority | Preserve a small current release binding at admission; move historical proof elsewhere, exact signal delegated |
| MIX-3 Global Gate binding carried into Run and delivery | `init-run`, delivery transitions | `kernel.py:680-718`; `delivery_lifecycle.py:406-420`, `660-670` | authority path/generation/SHA embedded in delivery targets and transition evidence | Current target disagrees with its bound gate, or local authority path disappears | Block initialization or later delivery transition | Global Gate release + current Delivery Target | Mixed release/current-PDF | Absolute local path; base release bytes | Yes | Mixed or Misplaced Authority | Keep current contract/version binding for the PDF; later design decides how it stops depending on cleanable release paths |

## 6. Completed Release Proof checks

CRP-2 through CRP-7 are the clearest candidates to move out of ordinary admission. They answer whether historical publication evidence is still locally replayable: manifest presence and hash, schema, atomic member list, mirror equality, command/result bindings, qualification artifacts, Git lineage, guarded cutover delivery, and candidate-state history. They do not inspect the new Run because it does not exist yet.

CRP-1 is narrower, yet its current implementation still uses machine-local JSON and SQLite as the durable proof that the Global Gate release happened. The later release-authority ticket must decide what ordinary startup trusts. This report only records that the stable base authority check and the historical manifest replay have different lifetimes.

CRP-8 confirms that Batch is a separate authority. Its presence in the repository does not make it a single-video prerequisite. An ordinary Bilibili or YouTube `workflow-policy-check`, `bootstrap-probe`, or `init-run` does not invoke `batch-authority-check`.

## 7. Live Runtime Coordination checks

LRC-1 through LRC-8 remain mandatory. ADR 0054 explains the reason: Run Records cannot reconstruct leases, fencing generations, reservation order, output-path ownership, or prepared intents; replacing a missing database could admit duplicate work (`docs/adr/0054-fail-closed-when-the-cross-run-control-store-is-unavailable.md:1-30`). The exact conditions for safe reinitialization are deliberately outside this ticket.

These checks are current-state fences. Their failure changes an immediate action: no new output binding, Claim, Lease, promotion, delivery transition, ownership handoff, or projection publication is committed. Completion of Workflow 2.0 does not shorten their lifetime.

## 8. Current PDF Quality checks

CPQ-1 through CPQ-8 remain mandatory and unchanged. They bind the actual source and current Artifact Generations to semantic review, final compilation, every rendered page, Acceptance Report v2, Delivery Guard, and the delivery lifecycle. A TeX, PDF, source, figure, manifest, or report mutation must stale the dependent evidence.

Acceptance Report v2 remains the sole semantic delivery decision; the Delivery Guard remains the mechanical proof of freshness and contract validity (`docs/contexts/delivery-quality/CONTEXT.md:96-104`; `docs/contexts/video-workflow/CONTEXT.md:499-505`). Removing historical cutover replay does not authorize weakening either one.

## 9. Mixed or misplaced authority

Three seams mix lifetimes:

1. `workflow-policy-check` combines Global Gate release replay, platform release replay, and a Legacy fallback projection. A platform with no committed control presence is reported as `active_legacy` (`src/video2pdf_workflow_kernel/cli.py:1029-1046`), contradicting the render skills' rule that every new Bilibili/YouTube task is Kernel-only.
2. Platform `require_current` combines a potentially useful current authority row/JSON check with full historical Exit Evidence replay, then both policy check and `init-run` call it.
3. The Global Gate path/SHA is embedded into each current delivery target. Current PDF delivery needs a gate-contract binding, while a machine-absolute path to old release bytes has a different lifetime.

This ticket does not specify the replacement signal, new CLI, migration, or deletion behavior.

## 10. Candidate checks to move out of ordinary admission

- Historical Global Gate and platform Exit Evidence file-existence and hash checks.
- Full Global Gate schema, activation-scope, atomic-member, command, result-binding, mirror, and qualification replay.
- Full platform manifest, lineage, qualification-run, guarded candidate delivery, mirror, and artifact-fingerprint replay.
- Candidate lifecycle reconstruction through `PREPARED`, `INITIALIZED`, `PROVISIONAL`, and `CONFIRMED` for a normal post-release Run.
- Policy-refresh and platform-refresh completion as permanent ordinary-startup prerequisites; incomplete publications still need an explicit maintenance/reconcile path.
- Any Batch activation or refresh condition attached to ordinary single-video startup; no such call is currently reachable.
- New-task `active_legacy` fallback reporting for Bilibili and YouTube.
- Permanent survival requirements for closed-release local logs and machine-absolute authority paths.

The local `09bc30d` candidate demonstrates a narrow bypass mechanism, not an approved design. Its default remains enabled, its switch is ignored workspace state, and it leaves current authority JSON/SQLite checks in place. It must not be treated as published resolution.

## 11. Checks that remain required

- Control Store identity, integrity, schema, lock, active-intent, Claim, Lease, output ownership, promotion, mutation, and reconciliation checks.
- Run Record identity, coordination revision, checkpoint, Artifact Generation, source epoch, and current-source checks.
- Source acquisition blocker/recovery and current Source Package checks.
- Task Attempt generation, fencing token, completion authority, Resource Lease, and Promotion checks.
- Current source-faithfulness, writing-quality, Pyramid, glossary, precompile seal, final seal, and compile-input closure.
- Guarded final compile and current compile provenance.
- Complete rendered-page coverage and freshness.
- Independent Acceptance v2 Claims, Patches, materialization, and current passing report.
- Delivery Guard and the governed `generating -> ready_for_delivery -> accepted -> delivered -> archived` lifecycle.

## 12. Boundaries delegated to the remaining Wayfinder tickets

- [Choose the ordinary-run release authority after cutover](https://github.com/Nishijujuba/video2pdf/issues/76): choose the durable signal trusted by a normal Run.
- [Define safe local Control Store reinitialization](https://github.com/Nishijujuba/video2pdf/issues/77): decide the observable no-active-authority conditions.
- [Retire completed cutover surfaces from the ordinary workflow](https://github.com/Nishijujuba/video2pdf/issues/78): decide which commands/checks are removed, maintenance-only, migrated, or archived.
- [Prototype the simplified Workflow 2.0 startup contract](https://github.com/Nishijujuba/video2pdf/issues/79): test the public CLI and project-local configuration shape.
- [Resolve open authority-repair issues under the post-release model](https://github.com/Nishijujuba/video2pdf/issues/80): decide the final disposition of the two open repair issues.

No conclusion here closes, rewrites, or implements those tickets.

## 13. Unknowns and limitations

- This is a static call-chain investigation. It did not create a Run, mutate authority state, download media, execute qualification, regenerate Exit Evidence, or run tests.
- The exact future repository-owned release signal is intentionally unknown.
- The exact safe Control Store reinitialization rule is intentionally unknown.
- The report distinguishes local ignored runtime authority from remote source history. Ignored JSON proves current machine configuration only; it is not published repository state.
- The local freshness-switch commit was inspected as candidate evidence. It may change or never be published.
- Historical GitHub resolution comments establish release facts and limitations; they do not extend the lifetime of machine-local logs into ordinary Run authority.

## 14. Evidence appendix

### Primary local sources

- Render startup contract: `.agents/skills/youtube-render-pdf/SKILL.md:28-36`; `.agents/skills/bilibili-render-pdf/SKILL.md:40-48`.
- Public CLI dispatch: `src/video2pdf_workflow_kernel/cli.py:1027-1048`, `1574-1629`, `1897-1932`.
- Global Gate admission: `src/video2pdf_workflow_kernel/global_gate.py:56-130`, `617-637`, `745-783`, `1128-1173`.
- Platform admission: `src/video2pdf_workflow_kernel/platform_kernel.py:154-179`, `260-566`, `3615-3703`.
- Cross-Run Control Store: `src/video2pdf_workflow_kernel/control_store.py:411-520`, `4638-4774`, `4834-5046`, `7243-8708`.
- Run initialization/current source: `src/video2pdf_workflow_kernel/kernel.py:590-758`, `1652-1827`, `2582-2894`.
- Task Claims and Promotion: `src/video2pdf_workflow_kernel/task_execution.py:1850-1978`, `2632-2710`, `2929-3686`.
- Acceptance and delivery: `src/video2pdf_workflow_kernel/acceptance_v2.py:386-1098`; `src/video2pdf_workflow_kernel/delivery_lifecycle.py:285-420`, `600-1700`; `src/video2pdf_workflow_kernel/guarded_delivery.py:15-120`.
- Domain authority: `docs/contexts/video-workflow/CONTEXT.md:1-73`, `231-255`, `385-505`; `docs/contexts/delivery-quality/CONTEXT.md:1-104`.
- Activation and recovery decisions: `docs/adr/0040-cut-over-to-the-kernel-one-platform-at-a-time.md:12-58`; `docs/adr/0050-separate-target-design-acceptance-from-runtime-activation.md:12-24`; `docs/adr/0054-fail-closed-when-the-cross-run-control-store-is-unavailable.md:12-34`; `docs/adr/0058-adopt-persisted-command-execution.md:1-40`.
- Persisted command records are execution evidence and do not replace Run, Acceptance, Guard, or Exit Evidence authority: `docs/operations/persisted-command-runner.md:145`.

### Live GitHub release facts

- [Activate the global Acceptance Report v2 gate](https://github.com/Nishijujuba/video2pdf/issues/43) — CLOSED.
- [Cut Bilibili delivery over to Delivery Quality Kernel authority](https://github.com/Nishijujuba/video2pdf/issues/13) — CLOSED.
- [Cut YouTube delivery over through the shared Kernel](https://github.com/Nishijujuba/video2pdf/issues/14) — CLOSED.
- [Replace Legacy Batch with projections over guarded single-video Runs](https://github.com/Nishijujuba/video2pdf/issues/15) — CLOSED.
- [Restore Global Gate Exit Evidence schema consistency after qualification binding expansion](https://github.com/Nishijujuba/video2pdf/issues/56) — OPEN at investigation time.
- [Refresh stale Global Gate policy authority after canonical branch consolidation](https://github.com/Nishijujuba/video2pdf/issues/57) — OPEN at investigation time.

### Verification discipline

Every command used for this investigation was read-only except the explicitly authorized Issue claim, isolated worktree/branch creation, this research document, and the later scoped Git/GitHub publication steps. No unit or integration test was added, changed, or run. No qualification command, old `q` evidence generation, video acquisition, Run initialization, authority mutation, or workflow repair was executed.

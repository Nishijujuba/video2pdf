# Implement the Kernel in thirteen vertical slices

The target architecture changes filesystem ownership, state authority, semantic task contracts, compilation, final acceptance, repair, and Batch scheduling. A layer-by-layer rewrite would leave long periods with schemas or modules that cannot prove an end-to-end checkpoint. A single production cutover would combine every failure domain. The implementation therefore needs ordered vertical slices with explicit public exit evidence.

## Considered Options

- Implement all schemas before executable behavior: rejected because contract volume would grow without a running consumer.
- Refactor each existing skill independently: rejected because the platform-neutral Kernel boundary would be reproduced several times.
- Replace the complete pipeline in one branch and test at the end: rejected because failures would have a wide and ambiguous cause set.
- Deliver checkpoint-oriented vertical slices: selected because every step proves a usable dependency for the next one.

## Decision

Implementation follows these thirteen ordered Kernel Implementation Slices:

### 0. Baseline protection

Fix the existing Batch reference to removed `args.venv_python` state and capture the current Pyramid, compile, acceptance, Delivery Guard, and Batch test baseline. The Exit Evidence Manifest lists every baseline test identity, command, expected status, actual status, and log fingerprint; any difference without an explicit approved baseline update blocks the slice.

### 1. Offline source-ready tracer bullet

Install and lock the explicit `jsonschema` runtime dependency, then implement the root package, thin CLI, Schema Registry, `contracts-check`, Fixture Platform Adapter, Bootstrap Probe, and the minimum Cross-Run Control Store schema required for database health, schema migrations, unique `run_id` to output-path binding, and initialization Mutation Intents. Add Run Initialization, path budget, complete deterministic Scaffold, initial Run Record, Artifact Plan, `verified_import`, `source_ready`, and initialization reconciliation under ADRs 0038, 0042, and 0054.

### 2. Common task execution and promotion

Implement Subagent Task Envelopes, Task Authority Bindings, Generated Task Prompts, Claims, Claim Fencing Tokens, Attempts, Task Completion Gate, Run Promotion Slot, Mutation Intent Saga, Artifact Generations, freshness, and recovery. Add the authority registry, coordination-record commit-marker dispatch, and public `reconcile-authority --kind kernel_run`; `reconcile-run` is its first wrapper. One bounded Source Acquisition Judgment Patch must cross the complete lifecycle without a live semantic provider.

### 3. Cross-run resource admission

Extend the existing Control Store with fixed quotas, atomic claim-and-enqueue, leases, unknown recovery, circuit breakers, two-level round-robin, overcommitted drain, disjoint Draining Reservations, configuration fingerprinting, backup, integrity, and restore drills. The restore drill must invoke the public `reconcile-authority --kind kernel_run` path from Slice 2 and prove orphaned filesystem commits block. No live downloader, Whisper, Codex semantic task, LaTeX provider, or Visual Acceptance task starts before this slice passes its multi-process quota and fencing tests.

### 4. Production source acquisition

Add Bilibili and YouTube Platform Adapters, fresh download, cookie failure classification, subtitle-language policy, Whisper fallback, the dedicated Source Acquisition Agent, deterministic Source Manifest materialization, and source reopen. Recorded provider fixtures must pass offline. Each platform smoke records the exact adapter command, redacted authentication classification, expected `source_ready` checkpoint, resulting Source Manifest fingerprint, and no-secret log proof in the Exit Evidence Manifest.

### 5. Single-section production and guarded draft compile

Implement `production-plan`, `production-advance`, Outline, one Writer, one Figure task, Figure Slot integration, applicable Pyramid gates, Integration Manifest, Compile Manifest, recorder-proven Compile Dependency Closure, and one guarded draft compile. Register and fingerprint the MiKTeX, package, and font Runtime Policy. Fixtures cover local `.sty/.cls`, bibliography input, extension-resolved graphics, a system font, disabled automatic package installation, path escape, and shell-escape rejection. No recursive directory copy may contribute a compile input.

### 6. Multi-section production

Enable concurrent isolated section Attempts, the required Figure Wave, at most one Incremental Figure Wave per section, deterministic section integration, and serial per-run promotion. A minimum three-section fixture proves write-set isolation and termination.

### 7. Content assurance

Implement parallel Consistency and Source-Faithfulness Adapters, fixed Skeletons, bounded Judgment Patches, deterministic materialization, same-generation binding, and dual-pass `content_assurance_ready`. The slice also implements Content Assurance Failure Set, deterministic repair routing, affected Pyramid reruns, diagnostic recompile, dual re-review, and one fail-repair-pass tracer under ADR 0052.

### 8. Final evidence and Global Acceptance v2 cutover

Implement Final Artifact Seal, guarded Final Compile, final Compile Report, final PDF promotion, complete page rendering, Render Evidence Manifest, allowed-artifact manifest, and `final_evidence_ready`. Then perform the Global Gate Cutover for the Acceptance Dimension Map, criterion-reference index, Text and Visual Reviewers, Legacy Acceptance Input Set, Acceptance Execution Context and Promotion Slot, v2 Skeleton and Judgment Patch contracts, per-Patch commit, v2 report-publication intent, materializer, per-page evidence, validator, Delivery Guard, skills, project instructions, and mirrored `.agents`/`.claude` tests. Register `acceptance_execution` in the Slice 2 authority dispatcher and add the public `acceptance-reconcile` wrapper. Acceptance Report v1 loses delivery authority globally in this slice. The Exit Evidence Manifest proves exact pages `1..page_count`, stale-evidence invalidation, v1 rejection, independently terminated Reviewer Claims, crash recovery at every Patch and report boundary, one Kernel input pass, and one Run-record-free Legacy input pass.

### 9. Repair closure

Implement Final Acceptance Repair Plans, capability routing, parallel disjoint repair tasks, Integration Repair for conflicts, invalidation through affected Pyramid and Content Assurance checkpoints, Final Compile, complete rerender, fresh dual acceptance, the three-materialized-failure budget, manual repair brief, and blocked terminal state.

### 10. Bilibili cutover

Implement the Kernel delivery lifecycle provider, Delivery Target Ownership generation, projection-revision schemas, Delivery Lifecycle Mutation Intents, Projection Publication Slots, archive-target publication, hook integration, and `delivery-reconcile`; then perform the first Platform Kernel Cutover. A new real Bilibili run must reach guarded delivery through the Kernel, and the Bilibili skill stops owning mechanical workflow steps. Exit evidence runs two concurrent Run lifecycle transitions and proves Projection Publication Slots preserve both Delivery Task Index entries. It also races `clear-target` against another transition for the same session, rejects an occupied expected-absent archive destination, and injects failure after archive publication, after moving session `current.json`, after each remaining projection, after the Run Record commit, and before the SQLite commit. Every recovery assertion covers archive/current/index/Run state, committed hashes and revisions, preserved prior files, and release or continued ownership of every path slot.

### 11. YouTube cutover

Apply the same atomic cutover to YouTube after the Bilibili Exit Evidence Manifest and one successful guarded Bilibili Kernel delivery. Shared behavior remains in the Kernel and only the Platform Adapter differs. The YouTube cutover requires its own real guarded delivery evidence.

### 12. Batch cutover

Replace the legacy Batch workflow engine with `batch-plan`, `batch-run`, and `batch-recover`; project independent Video Workflow Runs, use the already active Resource Admission Module, and remove global future submission, free-form child workflow prompts, PDF-existence success, and `--concurrency` authority. Exit evidence covers interrupted item creation without duplicate Runs, projection rebuild from Run Records, a platform authentication breaker, fairness between one independent Run and a multi-item Batch, and guarded delivered-only success.

Every slice has these minimum exit tests:

- a positive behavior at a Workflow Verification Seam;
- one fail-closed counterexample;
- positive and negative schema fixtures for new contracts;
- idempotency and fault injection for persistent mutations;
- stale-generation and unauthorized-path cases for semantic patches;
- quota, fairness, fencing, and restart cases for scheduling changes.

Every slice writes a schema-valid Exit Evidence Manifest containing the implementation commit, activation scope, commands, fixtures, expected checkpoints, positive and negative results, logs and artifact fingerprints, unresolved exceptions, and overall decision. A slice cannot unlock its dependent issue or runtime authority through narrative completion alone.

Gate Providers remain in their current locations during early slices and are invoked through executable contracts. Provider relocation is outside the critical path. Mechanical instructions migrate from skills only when their owning slice becomes executable, and every Platform Kernel Cutover updates `.agents`, `.claude`, `AGENTS.md`, `CLAUDE.md`, providers, validators, guards, and tests together.

## Consequences

Each milestone produces evidence that survives later implementation detail changes. Production delivery authority arrives late enough to include v2 acceptance and repair. The sequence prioritizes state integrity before concurrency and platform activation. Project 2.0 implementation tickets should preserve these dependency edges and use GitHub Issues under the existing human-approval policy.

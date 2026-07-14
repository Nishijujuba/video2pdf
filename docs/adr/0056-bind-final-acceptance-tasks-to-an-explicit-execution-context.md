# Bind final acceptance tasks to an explicit execution context

Acceptance Report v2 must review both Kernel and Legacy input tracks after the Global Gate Cutover. Kernel tasks can bind to `workflow/run.json`; Legacy directories intentionally have no synthesized Run Record. Final Acceptance also has two concurrent Reviewer Claims whose Judgment Patches must become durable evidence before one provider can materialize the combined report. A report-level promotion cannot safely act as the missing commit for two still-active Reviewer Claims.

## Considered Options

- Synthesize a minimal Video Workflow Run for Legacy acceptance: rejected because it would invent historical workflow authority and violate the deferred migration boundary.
- Run Legacy Reviewers without Task Claims or transactional promotion: rejected because duplicate review, stale Patch publication, and partial report replacement would remain possible.
- Keep both Reviewer Claims active until the provider publishes the final report: rejected because one provider intent would need to consume unrelated Claims and failure recovery could not identify which Patch was committed.
- Create one Final Acceptance transaction context and commit each Patch independently: selected because both input tracks receive the same task safety while workflow authority remains track-specific.

## Decision

`acceptance-prepare` creates a unique, schema-valid Acceptance Execution Context at:

```text
review/acceptance/executions/<execution-id>/execution.json
review/acceptance/executions/<execution-id>/tasks/<task-id>/task.json
review/acceptance/executions/<execution-id>/tasks/<task-id>/attempts/<attempt-id>/
```

Only Final Acceptance provider operations create these directories. The Cross-Run Control Store enforces at most one non-terminal context for the normalized pair of Video Output Directory and canonical `acceptance_report.json` path. A later repair cycle first marks the earlier context terminal or invalidated and then creates a new identity.

The context has one discriminated immutable input binding:

- `kernel_final_evidence`: `run_id`, expected `run_revision`, Final Artifact Seal, Final Evidence Checkpoint, allowed-artifact manifest, and Render Evidence Manifest fingerprints;
- `legacy_input_set`: Legacy Acceptance Input Set identity, schema version, canonical video directory, input-set fingerprint, allowed-artifact manifest, compile provenance, and rendered-page fingerprints.

The context record owns only Final Acceptance task identities, expected input, Skeleton fingerprint, Claim references, committed Text and Visual Patch generations, provider publication state, and its monotonic `execution_revision`. It owns no Video Workflow Run phase, delivery stage, delivery ownership, semantic decision, or historical migration state.

Each Reviewer Task Envelope uses `task_authority.kind: acceptance_execution`. Its Claim key contains the execution id, task id, attempt id, expected execution revision, coordinator session, declared write set, and Claim Fencing Token. Text and Visual Attempts may execute concurrently in their isolated staging directories.

After one Patch passes the Task Completion Gate, `acceptance-patch-commit` acquires the Acceptance Execution Promotion Slot, rereads the latest context, validates the current Claim token and immutable input binding, and creates a Patch Mutation Intent. It preserves any displaced evidence, publishes the validated Patch generation, and atomically replaces `execution.json` last as the module-local commit marker. The final SQLite commit ends only that Reviewer's Claim. The second Patch follows the same protocol against the next execution revision.

`acceptance-materialize` starts only when both committed Patch generations, the Skeleton, and all input fingerprints are current. It does not consume Reviewer Claims. The provider writes the report to staging, validates the complete v2 schema and runtime invariants, and uses a separate Acceptance Report Publication Intent to publish canonical `acceptance_report.json` plus any generated summary. `execution.json` is replaced last with the report fingerprint and materializer provenance, then the Intent commits.

For a Kernel binding, a later Run Promotion registers the already committed report generation and advances the applicable Run checkpoint. The Run Promotion Slot governs that registration. For a Legacy binding, Delivery Guard validates the committed Acceptance Execution Context, input-set fingerprint, report publication intent, and report directly. The Legacy execution record supplies module transaction authority and never becomes a Run Record.

Any input artifact, criteria, Dimension Map, rendered page, allowed manifest, or Skeleton change invalidates the context and both Patch generations. Reconciliation uses the context record, Mutation Intents, staged files, preserved prior files, and canonical hashes to finish or restore interrupted publication. Delivery remains blocked while an Acceptance Execution Mutation Intent is non-terminal or contradictory.

## Consequences

Legacy Acceptance v2 gains explicit task identity, Claims, Attempts, fencing, and crash-safe publication without workflow migration. Reviewer Claims reach independent terminal states before aggregation. Kernel and Legacy inputs share one Final Acceptance Module Interface and differ only at the input adapter and post-report registration seam. The implementation adds an execution-record schema, authority-binding union, two promotion commands, uniqueness constraints, and fault-injection tests for every Patch and report publication boundary.

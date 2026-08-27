# Safe local Control Store reinitialization

## Conclusion

An absent or incomplete local Control Store identity is safe to initialize only in one of two observable states:

1. **Pristine bootstrap:** the workspace has never held a Control Store or governed Workflow 2.0 authority. This remains `Fresh Control Store Initialization`.
2. **Pre-authorized authority transfer:** a healthy, quiesced Control Store previously published a complete `Control Store Reinitialization Eligibility Snapshot`; the replacement operation verifies that snapshot against all surviving filesystem authorities, changes the Store fencing epoch, imports every retained authority, and reconciles every imported Run before mutations resume. This is `Authority-Preserving Control Store Reinitialization`.

An unexpected loss without that pre-loss snapshot is not eligible, even when the current database directory looks empty and no worker process is visible. It remains `Control Store Unavailable` and requires backup restoration or an explicitly designed manual recovery path. Filesystem Run Records cannot prove the absence of Claims, Leases, queue reservations, fencing generations, held publication slots, or non-terminal Mutation Intents that existed only in SQLite.

This decision preserves the existing distinction between the per-run `Video Workflow Run Record` and the cross-run transactional authority. It also preserves the existing fail-closed rule: reinitialization eligibility is evidence established before loss, not a conclusion inferred from missing evidence after loss.

## Current source-backed baseline

`ControlStore.initialize` already has correct pristine-bootstrap behavior. It creates a Store only when anchor, marker, and database are all absent and the control directory contains no unrecognized state. Any partial identity fails closed; the method does not silently replace it. [`control_store.py:485-524`](../../src/video2pdf_workflow_kernel/control_store.py#L485-L524)

The external anchor is intentionally evidence that a workspace previously owned a Store. Its presence after local database loss therefore rules out pristine bootstrap. The recovery sentinel and any non-empty canonical control directory have the same effect. [`control_store.py:463-483`](../../src/video2pdf_workflow_kernel/control_store.py#L463-L483)

The Store owns more than the facts reconstructable from `workflow/run.json`. Its schema contains unique Run/output bindings, initialization history, committed Run mutation chains, Claims and Attempts, resource queues and Leases, resource sequencing and circuit breakers, task/source/delivery promotions, Projection Publication Slots, and Batch state. [`0042-use-sqlite-only-for-cross-run-transaction-authority.md`](../adr/0042-use-sqlite-only-for-cross-run-transaction-authority.md) [`control_store.py:77-366`](../../src/video2pdf_workflow_kernel/control_store.py#L77-L366)

Current Run authority is derived from the complete ordered chain of committed initialization, run-state, task-promotion, source-publication, and delivery-lifecycle intents. A surviving latest `workflow/run.json` supplies the current bytes and revision, but does not supply that complete transaction history. [`control_store.py:5331-5482`](../../src/video2pdf_workflow_kernel/control_store.py#L5331-L5482)

The existing restore path proves the correct recovery posture. It requires an explicitly selected SQLite-backup package, quarantines the prior identity, validates the restored Store, compares database Run identities with filesystem Run Records, reconciles matching authorities through the public Kernel seam, converts uncertain Leases to `unknown`, and blocks on orphaned commits or active Claims. [`control_store_recovery.py:430-610`](../../src/video2pdf_workflow_kernel/control_store_recovery.py#L430-L610) [`0054-fail-closed-when-the-cross-run-control-store-is-unavailable.md`](../adr/0054-fail-closed-when-the-cross-run-control-store-is-unavailable.md)

## Observable eligibility states

| Observed state | Classification | Allowed action |
| --- | --- | --- |
| No anchor, recovery sentinel, identity artifact, non-empty control directory, Kernel Run Record, Batch Record, delivery ownership projection, or registered reinitialization/backup authority exists in the governed workspace | Pristine bootstrap | Fresh initialization may create the first Store identity. |
| All identity artifacts exist and the Store passes its exact identity, schema, integrity, foreign-key, semantic-row, and writer-lock checks | Healthy Store | Continue using the Store; reinitialization is ineligible because no recovery is needed. |
| Identity is absent or incomplete and a valid selected SQLite backup exists | Restorable Store | Use `control-store-restore`; logical reinitialization is unnecessary. |
| Identity is absent or incomplete and a current Reinitialization Eligibility Snapshot passes every condition below | Authority transfer ready | Run authority-preserving reinitialization under an exclusive recovery sentinel. |
| Identity is absent or incomplete, prior identity or workflow authority is observable, and neither a valid selected backup nor a valid eligibility snapshot exists | Unproven loss | Stay globally blocked and produce a manual recovery brief. |
| Any snapshot, filesystem inventory, output binding, Run revision, Batch projection, delivery ownership, staging journal, or recovery sentinel contradicts another | Authority contradiction | Preserve all material, block globally, and resolve the contradiction through manual recovery. |

The pristine-bootstrap check is intentionally narrow. Absence of an operating-system process, recent log growth, or a database file is weak negative evidence. Those observations cannot establish that no late worker holds a valid Claim Fencing Token or that no prepared filesystem publication exists.

## Required pre-loss eligibility evidence

The Reinitialization Eligibility Snapshot must be published by the healthy Store while one exclusive maintenance fence prevents new Claims, admissions, Run initializations, promotions, delivery transitions, Batch mutations, and other Control Store writes. The fence remains authoritative until the reinitialization either commits or aborts back to the original Store.

The snapshot must bind:

- the Store identity, normalized workspace path, schema generation, maintenance-fence identity, and replacement fencing epoch;
- every Run binding, normalized output path, initialization identity, current coordination revision, current Run Record path, and current Run Record fingerprint;
- the committed mutation chain needed to prove each current Run Record predecessor and revision;
- every Batch Record and item projection owned by the Store, including nested item Run identities;
- every delivery ownership projection and normalized Projection Publication path that could conflict with another Run;
- the exhaustive Claims, Attempts, queue entries, reservations, Leases, held publication slots, and non-terminal run/task/source/delivery Mutation Intents;
- the active resource configuration, capacity usage, fairness cursors, circuit breakers, and sequence positions whose reset could alter admission behavior;
- the exact classification of every retained row as imported authority, proven terminal history, or intentionally retired history.

Eligibility requires zero unresolved non-terminal ownership at snapshot time. Specifically:

- no `ACTIVE` Task Claim;
- no Task Attempt in `CLAIMED` or `VALIDATED_WAITING_FOR_PROMOTION`;
- no queue entry in `QUEUED` or `ADMITTED`, and no `PENDING` or `ACTIVE` reservation;
- no Resource Lease in `starting`, `active`, or `unknown`;
- no held Projection Publication Slot;
- no run-state intent in `PREPARED`;
- no task-promotion, source-publication, or delivery-lifecycle intent in `PREPARED`, `FILES_PUBLISHED`, or `RECORD_COMMITTED`;
- no incomplete initialization intent;
- no active Control Store restore or reinitialization operation.

A non-terminal Video Workflow Run does not by itself make reinitialization unsafe. Its per-run lifecycle can survive when the snapshot imports the complete Run binding and committed mutation chain and the current `workflow/run.json` remains byte-identical. The replacement Store must treat the Run as current only after public reconciliation succeeds.

## Replacement fencing and reconciliation

Preserving rows is insufficient unless late work from the replaced Store is rejected. Every post-reinitialization mutation must therefore bind a new Store fencing epoch in addition to the existing per-Claim generation. A worker or coordinator prepared under the replaced epoch cannot claim, release, promote, publish, or mutate after the replacement becomes current.

The replacement operation must follow one durable lifecycle:

1. Revalidate the maintenance fence, snapshot, canonical identity layout, and complete filesystem authority inventory.
2. Quarantine partial identity artifacts under the governed `待删除` recovery area.
3. Materialize the new Store off the canonical path and import all retained authority in one validated generation.
4. Atomically publish the new anchor, marker, database, and fencing epoch as one recoverable operation.
5. Run the existing public reconciliation seam for every imported Run and the equivalent Batch and delivery projections.
6. Publish a passing reinitialization report only when the imported database, filesystem authorities, output bindings, mutation chains, and projections agree and no ownership remains unresolved.
7. Remove the global mutation block only after the report commits. Any interruption resumes the same operation; it never starts a second reinitialization.

If a valid eligibility snapshot records non-terminal ownership, it is diagnostic evidence rather than permission to reinitialize. Backup restoration retains the exact ownership states and can route uncertain Leases through existing recovery. A future manual-loss design may conservatively create unknown ownership, though that path additionally needs a fencing epoch that survives total local identity loss and is outside this decision.

## Module and interface boundary

The existing `ControlStore.initialize` interface should remain the small, strict first-bootstrap seam. Adding recovery switches to it would force ordinary callers to understand partial identity layouts, authority import, fencing, quarantine, and reconciliation.

Authority-preserving reinitialization belongs in one deep recovery module beside `ControlStoreRecovery`. Its external interface needs only three semantic operations:

- **prepare eligibility**: acquire the maintenance fence and publish the immutable eligibility snapshot from a healthy Store;
- **commit replacement**: consume one selected snapshot, publish/import the replacement Store, and return a committed or blocked report;
- **resume replacement**: continue the same durable operation after interruption.

The exact CLI names and JSON configuration shape remain delegated to the map's later startup-contract work. Internally, the module should reuse direct `ControlStore` queries and the existing public authority reconciliation dispatch. Thin wrappers around `initialize`, file moves, or path formatting would not own enough behavior.

## Domain consequences

The active Video Workflow glossary now distinguishes:

- `Fresh Control Store Initialization`: first creation in a pristine workspace;
- `Authority-Preserving Control Store Reinitialization`: replacement with complete authority transfer and new fencing;
- `Control Store Reinitialization Eligibility Snapshot`: the pre-loss proof that makes the second transition observable and safe;
- `Control Store Restoration`: recovery from an exact selected SQLite backup under the existing ADR 0054 lifecycle.

“Reset,” “rebuild,” and “create an empty database” are rejected terms for an authority-bearing workspace because they hide the authority-loss decision.

## Consequences for the remaining Wayfinder map

- The future ordinary-startup contract may treat a missing Store as automatic bootstrap only after the pristine condition passes. Any prior identity evidence routes to recovery.
- The future durable repository-owned release signal must remain independent from Store reinitialization eligibility; release status does not prove the absence of live runtime coordination.
- The migration ticket must decide whether to implement the pre-loss logical snapshot or retain backup restoration as the only supported recovery for existing installations.
- The CLI/configuration ticket must expose explicit bootstrap, restore, and authority-preserving reinitialization outcomes without a generic force flag.
- The final disposition of the open authority-repair issues must not require regenerating historical release evidence to prove local runtime safety.

## Limitations

This research defines the safety conditions and module seam. It does not select the final CLI spelling, JSON Schema, migration compatibility policy, or implementation slice. It adds no tests and changes no runtime behavior.

The current restore filesystem discovery scans direct workspace children for `workflow/run.json`; Batch item Runs can be nested beneath a Batch directory. Any implementation of authority-preserving reinitialization must use the authoritative bindings from the eligibility snapshot and validate nested Batch records explicitly. Broad recursive discovery without declared ownership would be ambiguous.

The current code has no Store-wide fencing epoch carried by every mutation. Authority-preserving reinitialization therefore remains a target design until that epoch and its fail-closed validation are implemented. Existing `control-store-restore` remains the only implemented recovery path for an authority-bearing workspace.

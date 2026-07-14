# Fail closed when the cross-run control store is unavailable

The Cross-Run Control Store owns the exhaustive cross-run coordination set in ADR 0042, including Claims, leases and scheduling state, unique output-path bindings, promotion and Projection Publication Slots, and non-terminal Mutation Intents. These records cannot be reconstructed completely from Run Records or Acceptance Execution Contexts. If the database is missing or corrupt, creating an empty replacement would forget possible active workers and permit duplicate path ownership, resource oversubscription, conflicting promotion, or lost projection updates.

## Considered Options

- Recreate the database automatically from `workflow/run.json` files: rejected because leases, fencing generations, reservation order, and prepared intents may be absent from those projections.
- Continue each run independently until the database returns: rejected because new commits could conflict with an unknown global owner.
- Restore the newest database file found in `待删除`: rejected because recency and integrity do not prove backup authority.
- Freeze Kernel mutations and require evidence-bearing restore plus reconciliation: selected because uncertain shared state remains conservative.

## Decision

`control-store-check` runs before Run Initialization, claim, admission, promotion, delivery lifecycle mutation, recovery ownership transfer, and every cutover Exit Evidence Manifest. It validates path, local-volume support, openability, effective PRAGMAs, registered schema migration level, `quick_check`, foreign keys, and a bounded writer-lock probe.

Any failure enters Control Store Unavailable. In this state:

- no new Run or output-directory binding is created;
- no Claim, queue item, Lease, reclaim, promotion, delivery transition, or cutover is committed;
- already running workers may finish only inside their isolated Attempt staging directories;
- their results cannot promote, and uncertain leases remain physically and logically reserved;
- user-visible diagnostics identify the failed check and known backup inventory without selecting a backup automatically.

`control-store-restore` requires a quiesced coordinator and an explicitly selected backup produced by SQLite's backup API. It closes all connections, moves the failed database and surviving sidecars together to `待删除/control-store/<timestamp>/`, restores to a fresh canonical path, verifies integrity and schema, and then reconciles every registered Run Record, Acceptance Execution Context, committed Acceptance Patch generation, report-publication state, output-path binding, Claim, Lease, Run or Acceptance promotion slot, Projection Publication Slot, Mutation Intent, Batch projection, and delivery projection. Any lease whose execution state cannot be proven becomes `unknown`.

Restore reconciliation applies the `reconcile-authority` dispatch in ADR 0042. A Run Record or Acceptance Execution Context that references an intent missing from the selected backup becomes `orphaned_filesystem_commit`; its files remain preserved and its authority stays blocked. The recovery report lists the absent intent, coordination revision, canonical artifact or Patch hashes, and available recovery evidence. It cannot mark global recovery complete while any orphaned commit, unmatched slot, or contradictory publication state remains.

The operation writes `workspace/.workflow-control/control_store_recovery_report.json` containing the selected backup fingerprint, quarantined paths, integrity results, schema migration result, all reconciled identities, unresolved gaps, and final global status. Kernel mutations resume only when the report passes and no blocking mismatch remains.

When no valid backup exists, automatic rebuild is unsupported. The system stays blocked and emits a manual recovery brief. A future human-authorized reinitialization may import path bindings and mark every possibly active ownership record `unknown`, but that is a separate destructive-recovery design and is not part of the first implementation.

## Consequences

Shared control-state loss has a wide but explicit failure radius and cannot silently create duplicate work. File-backed Run and Gate evidence remains inspectable during the freeze. Backup creation, restore drills, corrupted-page fixtures, schema mismatch, missing-file, and post-restore fencing tests become release requirements.

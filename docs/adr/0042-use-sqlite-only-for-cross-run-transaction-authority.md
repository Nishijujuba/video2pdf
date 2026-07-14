# Use SQLite only for cross-run transaction authority

The per-video `workflow/run.json` contract is already selected as the inspectable coordination authority for one Video Workflow Run. Resource Admission, Task Claim compare-and-set, Fairness Group cursors, leases, and circuit breakers span several runs and require serialized transactions. A JSON read-modify-write plus `os.replace` can publish one file atomically, but it cannot provide a cross-process compare-and-set across several files. Storing every workflow contract only in a database would remove the approved run-local, schema-valid evidence model.

## Considered Options

- Use JSON files plus process-local locks: rejected because independent Python processes can oversubscribe quotas or claim the same task.
- Add operating-system file locks around several JSON files: rejected because multi-file commit, crash recovery, lock ownership, and Windows replacement behavior would become a custom database protocol.
- Store all per-run and cross-run state in SQLite: rejected because `workflow/run.json` would become a stale projection instead of the approved per-run authority.
- Keep per-run contracts in JSON and use SQLite for cross-run transaction authority: selected because each store owns one coherent domain.

## Decision

The Kernel creates one project-level Cross-Run Control Store at:

```text
workspace/.workflow-control/control.sqlite3
```

The directory is a reserved Kernel path and is excluded from video-output discovery and historical migration classification.

The SQLite store is authoritative for this exhaustive cross-run coordination set:

- unique normalized `run_id` to Video Output Directory bindings and initialization intents;
- Task Claim compare-and-set, Claim Fencing Tokens, and active Task Attempt ownership for every Task Authority Binding;
- Resource Admission queue entries, quotas in use, and leases;
- Resource Circuit Breaker state;
- Fairness Group and group-internal run cursors;
- enqueue, admission, and scheduling decision sequences;
- Run Promotion Slots and Acceptance Execution Promotion Slots;
- Projection Publication Slots keyed by normalized canonical file path;
- initialization, artifact-promotion, acceptance-publication, and delivery-lifecycle Mutation Intents plus their recovery state.

Each `workflow/run.json` remains authoritative for its Video Workflow Run identity, phase, Workflow Checkpoints, Artifact Generations, declared dependencies, delivery state, and references to gate-specific reports. Each Acceptance Execution Context record owns only its module-local task and publication state. Materialized Gate Reports own gate decisions. Batch Records and delivery target files remain validated projections and never replace those authorities.

The first implementation uses Python's standard-library `sqlite3` and these connection requirements:

- `BEGIN IMMEDIATE` for every state-changing scheduler or claim transaction;
- `PRAGMA journal_mode=DELETE`;
- `PRAGMA synchronous=EXTRA`;
- `PRAGMA foreign_keys=ON`;
- `PRAGMA trusted_schema=OFF`;
- an explicit bounded `busy_timeout`;
- short write transactions with no network, LLM, subprocess, hashing, or bulk filesystem work inside the database transaction.

The connection verifies every effective PRAGMA value instead of assuming an unsupported setting failed. Active capacity is derived from normalized lease and lease-resource rows in `starting`, `active`, and `unknown` states; no independently maintained aggregate usage counter can authorize admission. `claim_generation` is a fencing token on every completion, promotion, release, and reclaim mutation, so a superseded worker cannot publish after returning late.

Rollback journal plus `synchronous=EXTRA` is selected for the first Windows implementation because scheduler throughput is low and the additional directory synchronization narrows the power-loss window after journal deletion. WAL remains deferred until local-volume behavior and operational backup procedures justify its extra `-wal` and `-shm` lifecycle.

SQLite cannot atomically commit ordinary filesystem artifacts. Every governed filesystem promotion therefore follows a recoverable protocol:

1. a short database transaction validates the expected prior state and records a `PREPARED` Mutation Intent;
2. the Kernel performs same-volume staging, preserves displaced canonical files under script-created recovery storage, promotes the complete output set, and verifies SHA-256 digests;
3. the Kernel atomically replaces the Task Authority Binding's coordination record with the new revision, generation, and intent identity: `workflow/run.json` for `kernel_run`, or the Acceptance Execution Context's `execution.json` for `acceptance_execution`;
4. a final short database transaction verifies the intent and marks it `COMMITTED`.

Downstream Kernel operations require a matching committed intent for a newly published generation. A crash before the final commit leaves an explicit reconciliation case. `reconcile-run` verifies the intent, run record, canonical hashes, staging evidence, and preserved prior generation, then deterministically completes the commit or restores the prior state. It never assumes that database commit and file publication occurred atomically.

The generic recovery entrypoint is `reconcile-authority --kind <kernel_run|acceptance_execution> --id <authority-id>`. It dispatches to the matching coordination-record schema and commit marker. `reconcile-run` remains the convenience wrapper for a Run binding; `acceptance-reconcile` is the wrapper for an Acceptance Execution Context and verifies its committed Patch generations plus report-publication state.

Control Store restore may expose a coordination record whose newest intent is absent from the selected database backup. This is an `orphaned_filesystem_commit`, not evidence of a successful transaction. Reconciliation preserves the Run or Acceptance files, blocks further mutation and delivery for that authority, and records the missing intent identity, coordination revision, Patch or Artifact Generations, canonical hashes, and available staging evidence. Automatic intent reconstruction is unsupported; recovery requires a backup containing the intent or a separately approved manual recovery procedure.

The first implementation supports only a validated local filesystem. Startup preflight rejects UNC paths, network shares, unsupported synchronization-backed locations when detectable, and any volume that fails SQLite locking plus same-volume atomic-replace probes. No distributed writer or cross-machine control store is supported.

Run Initialization records the project-level unique binding between `run_id` and the normalized, case-folded Video Output Directory in this Store. A second run cannot bind the same physical Windows path even when its input uses a different spelling or case.

Database backup or inspection must use SQLite's backup API or a coordinated quiescent snapshot. Copying only `control.sqlite3` during an active transaction is not accepted backup evidence.

Every Kernel startup and pre-cutover check runs `control-store-check`: it verifies the database can be opened at the canonical local path, required PRAGMAs took effect, the schema version is registered, `PRAGMA quick_check` returns `ok`, foreign-key checks are empty, and a bounded locking probe succeeds. Failure enters Control Store Unavailable under ADR 0054. The Kernel never substitutes a new empty database or reconstructs live control authority silently from directory contents.

## Consequences

Cross-process claims and resource quotas gain a real transactional compare-and-set. Run evidence remains human-readable and JSON-Schema validated. The Kernel must maintain an explicit authority table and recovery protocol across the two stores. Fault-injection tests are required before and after intent creation, every filesystem promotion boundary, run-record replacement, and final intent commit.

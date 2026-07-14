# Coordinate delivery projections through a lifecycle intent

A Kernel Track delivery transition must keep the Run Record, video delivery target, session-scoped target, and Delivery Task Index consistent. They live in several files and may span the video workspace and `.codex` control paths, so no filesystem operation can update them atomically. Treating each file as an independent stage writer would recreate conflicting authority after a crash.

## Considered Options

- Let every delivery component update its own stage independently: rejected because partial transitions have no deterministic winner.
- Make the project task index the only delivery authority: rejected because it is a cross-run observability projection and the Stop hook is session scoped.
- Store all delivery lifecycle state only in SQLite: rejected because the existing bounded file contracts remain required inputs for hooks, guards, and audit.
- Use the Run Record as commit marker and coordinate projections through a Saga intent: selected because one per-run authority remains inspectable and every partial write is recoverable.

## Decision

Every Kernel Track transition among `generating`, `ready_for_delivery`, `accepted`, `delivered`, and `blocked`, plus ownership handoff and target archival, uses one Delivery Lifecycle Mutation Intent in the Cross-Run Control Store. The prepared intent records:

- `run_id`, expected `run_revision`, expected Delivery Target Ownership generation, session identity, prior stage, and target stage;
- an optional producer Task Authority Binding plus Claim Fencing Token when a claimed provider task supplied transition evidence; handoff, delivery acknowledgment, and archival require no synthetic Task Claim;
- every mutation target's normalized canonical path, expected state, proposed state, revision, and SHA-256 for `workflow/run.json`, `review/acceptance/delivery_target.json`, the session `current.json`, and `task-index.json`; an archival intent also records its unique archive destination with `expected_state: absent` and its proposed document hash;
- required Acceptance Report, Delivery Guard Report, Final Compile Report, Final Evidence Checkpoint, and artifact fingerprints for the requested transition;
- unique sibling staging paths and preserved prior projections.

Every delivery target, session target, Delivery Task Index, and archive document schema carries a monotonic `projection_revision`. Before an intent reaches `PREPARED`, one short `BEGIN IMMEDIATE` transaction acquires the Run Promotion Slot plus a Projection Publication Slot for every projection and archive target path in sorted path order. A path whose expected state is absent still requires a slot. The uniqueness constraint permits only one non-terminal intent per path. The slots remain durable across filesystem work and are released only after commit or evidence-bearing reconciliation.

The transition protocol is:

1. after acquiring every slot, reread the latest Run Record and all projections, validate their expected revisions plus the legal transition and gate evidence, and derive each proposed projection from that latest state;
2. commit the complete intent as `PREPARED`; any compare-and-set mismatch releases the newly acquired slots without publishing files;
3. write, flush, hash, and atomically replace the video target, session target, task-index projection, and any archive document, preserving prior files under a registered `待删除/delivery-lifecycle/<intent-id>/` location; an archival intent publishes and verifies the archive document, then moves the active session `current.json` into that preservation location so its proposed state becomes absent;
4. atomically replace `workflow/run.json` last with the new stage, run revision, ownership generation, and intent identity; this is the per-run delivery commit marker;
5. verify every projection against the committed Run Record, mark the SQLite intent `COMMITTED`, and release its Run and Projection Publication Slots.

The global `task-index.json` path therefore serializes its whole-file publication across Runs. Two Runs may prepare lifecycle work concurrently only until publication-slot acquisition. A later intent always derives its index update from the revision committed by the earlier intent, preventing last-writer loss. Session `current.json` receives the same path-keyed protection when two transitions address one session.

Kernel-aware hooks, guards, and recovery commands accept a projection only when its intent, run revision, stage, and file hash agree with the current Run Record and committed lifecycle intent. A Stop hook encountering a prepared or mismatched transition blocks delivery and reports reconciliation; it does not launch reconciliation work itself.

Recovery follows a fixed rule while the durable path slots remain held. If the Run Record still contains the prior revision, the transition is uncommitted and `delivery-reconcile` restores all projections to their preserved prior state. For an archival intent it also moves any uncommitted archive document into `待删除/delivery-lifecycle/<intent-id>/uncommitted-archive/` and restores session `current.json`. If the Run Record contains the proposed revision and matching intent, recovery verifies or rewrites every required projection and archive document from the committed Run Record, enforces the proposed absence of session `current.json` for archival, and finishes the SQLite commit. Any contradictory hash, occupied expected-absent archive path, or missing preserved evidence blocks the run and emits a delivery-lifecycle recovery brief. No later intent may bypass a path slot owned by an unresolved earlier intent.

After `delivered`, `clear-target --session-id` is implemented as an archival lifecycle intent. Its deterministic destination is `.codex/delivery-targets/archive/<session-id>/<intent-id>.json`. The intent compares the Run revision, Delivery Target Ownership generation, session identity, current projection revisions, and destination absence; records the archive path and proposed SHA-256; and acquires that destination's Projection Publication Slot. It does not invent a Task Claim. Successful commit leaves the archive document current, session `current.json` absent, the task-index projection updated, and the Run Record at `delivered`; direct file deletion cannot satisfy archival.

Legacy Track delivery continues under its active contract until the applicable cutover. The Global Gate Cutover updates Legacy acceptance evidence to v2 but does not synthesize this Kernel Run lifecycle.

## Consequences

All Kernel delivery files have one commit order and one reconciliation rule. Shared whole-file projections gain path-scoped lost-update protection. Stop-hook behavior remains bounded and read-only. More persistent writes occur per stage change, requiring fault injection after slot acquisition, every projection replacement, Run Record commit, final SQLite commit, handoff, and archival step. The first implementation serializes all task-index publication; a sharded index requires a later contract.

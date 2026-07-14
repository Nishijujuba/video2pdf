# Serialize canonical promotion within each run

Writer, Figure, and other Task Attempts can safely execute in parallel when they write only to isolated staging directories. Their canonical artifacts may also be logically disjoint. Every successful promotion still updates the shared Artifact Generation registry and `run_revision` inside one `workflow/run.json`. Concurrent read-modify-replace operations against that file would lose one commit or require a complex multi-writer merge protocol.

## Considered Options

- Serialize all task execution within a run: rejected because isolated semantic work and staging would lose useful parallelism.
- Permit concurrent promotion for disjoint artifact paths: rejected because both promotions still mutate the same Run Record and Checkpoint graph.
- Let a losing writer merge its prior Run Record into the winner: rejected because a file-level merge cannot prove dependency freshness or preserve transaction ordering.
- Run Task Attempts concurrently and serialize their canonical promotions: selected because it retains expensive-work parallelism with one durable commit order.

## Decision

Every Video Workflow Run has one Run Promotion Slot enforced by a Cross-Run Control Store uniqueness constraint: at most one Mutation Intent for that `run_id` may be in a non-terminal state.

Task execution, semantic judgment, resource use, and attempt-local staging can proceed concurrently under their Task Claims and write sets. An Attempt whose outputs pass the Task Completion Gate enters `validated_waiting_for_promotion`; its Claim and Claim Fencing Token remain current while it waits for the Run Promotion Slot.

After acquiring the slot, the Kernel reruns the deterministic Task Completion Gate against the current Run Record. It verifies:

- the Attempt's Claim Fencing Token remains current;
- every declared input Artifact Generation and SHA-256 remains current;
- the current `run_revision` and required Workflow Checkpoints allow the promotion;
- staged outputs and evidence still match the Task Envelope;
- target canonical paths remain inside the declared write set.

If every dependency remains current, the Kernel creates the Mutation Intent against the latest `run_revision` and may promote the already completed staging outputs. If any dependency changed, the Attempt becomes `stale`; it cannot be rebased through a file merge or promoted under its old evidence. The production or repair planner decides whether a new Attempt is required.

The Run Promotion Slot remains held through filesystem publication, Run Record replacement, final intent commit, and required reconciliation. A crash leaves the slot occupied by the non-terminal intent until `reconcile-run` resolves it. Timeout alone never releases it.

Only a `COMMITTED` Mutation Intent moves the Attempt to `committed_complete` and ends its Task Claim. A stale, failed, cancelled, or reclaimed Attempt reaches its own explicit terminal state and cannot leave a reusable Claim behind.

Promotions for different Video Workflow Runs may proceed concurrently because each has an independent Run Record and physical output directory. Project-level resource quotas still apply to any provider work they require.

Final Acceptance Reviewer Patches bind to an Acceptance Execution Context under ADR 0056 and use its Acceptance Execution Promotion Slot. A Kernel-bound context registers only its committed Materialized Gate Report and provenance back into `workflow/run.json`; that registration uses the Run Promotion Slot. This keeps Reviewer Claims independent from the Run mutation and gives Legacy input the same task safety without synthesizing a Run Record.

## Consequences

Parallel agents retain their main wall-clock benefit while canonical state has one serial history. A completed Attempt can become stale while waiting, so planners and user-facing status must distinguish `waiting_for_promotion` from `requires_rerun`. Tests must cover two disjoint Attempts finishing simultaneously, stale inputs after the first commit, crash-held slots, repeated reconciliation, and promotion across different runs.

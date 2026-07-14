# Allow only resource-disjoint draining reservations

Several queued tasks may reach the effective configured bypass threshold while waiting for different or overlapping Resource Classes. Allowing only one global Draining Reservation would delay an unrelated starving task. Activating overlapping reservations without an order would make both tasks claim priority over the same future capacity and leave recovery behavior ambiguous.

## Considered Options

- Permit only one global reservation: rejected because independent resource sets would lose useful concurrency.
- Activate every reservation immediately: rejected because overlapping reservations would have competing priority over the same capacity.
- Merge overlapping reservations into one group: rejected because the group would still need a deterministic winner and could drain more resources than one task needs.
- Activate pairwise-disjoint reservations and queue overlaps by sequence: selected because unrelated drains can proceed while every contested resource has one priority owner.

## Decision

When a queued task reaches the `effective_bypass_threshold` recorded for the current scheduling decision, the Cross-Run Control Store allocates a monotonic `reservation_seq`. The initial v1 configuration sets this threshold to eight. The task requests starvation protection exactly once for that queue entry.

The scheduler maintains these invariants:

- active Draining Reservations have pairwise-disjoint complete Resource Class sets;
- a task whose resource set intersects any earlier active reservation enters `reservation_pending` while retaining its `reservation_seq`;
- pending candidates are considered in ascending `reservation_seq` order;
- when an active reservation terminates, the scheduler activates the earliest pending candidate whose set is disjoint from every remaining active reservation, then continues scanning for further compatible candidates;
- a new ordinary Admission cannot consume a resource held for an active reservation;
- Resource Classes outside every active reserved set remain schedulable;
- reservations never partially acquire capacity or preempt an active Lease.

An active reservation terminates when its task is atomically admitted, explicitly cancelled, invalidated by a changed Task Envelope or prerequisite, or resolved through an evidence-bearing reconciliation action. A pending reservation terminates under the same cancellation or invalidation rules. Re-enqueueing a logically new Task Attempt creates a new queue identity and cannot inherit the prior reservation sequence automatically.

All activation, pending, release, and promotion decisions occur in one `BEGIN IMMEDIATE` transaction and append a control event with the effective Resource Admission Configuration fingerprint. Recovery replays persisted reservation states and sequences; it does not derive priority from timestamps or filesystem order.

## Consequences

Starving tasks on unrelated resources can drain capacity concurrently. A contested Resource Class has one deterministic reservation owner at a time. The scheduler needs intersection, pending-promotion, cancellation, restart, and configuration-change tests, including three-way overlapping resource sets.

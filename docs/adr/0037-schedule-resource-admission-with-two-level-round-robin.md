# Schedule resource admission with two-level round-robin

Fixed Resource Class quotas require an ordering rule. A global FIFO queue is deterministic but can suffer head-of-line blocking. Independent per-resource queues make atomic multi-resource tasks difficult to order and recover. Critical-path and adaptive priority require trustworthy duration telemetry that the first implementation does not possess. A large Batch can also flood a flat queue ahead of an independently requested video.

## Considered Options

- Global FIFO with strict head-of-line blocking: rejected because an unavailable resource can leave unrelated capacity idle.
- Global oldest-feasible selection: rejected because a steady stream of feasible tasks can starve an older multi-resource task.
- One independent FIFO per Resource Class: rejected because a task that appears in several queues creates ambiguous atomic-selection and recovery state.
- Estimated critical-path priority: deferred until durable runtime telemetry can support it.
- Equal two-level round-robin with bounded bypass: selected because its decisions are deterministic, recoverable, and fair across standalone and Batch work.

## Decision

Every queued task has a `fairness_group_id`. A Batch item uses its `batch_id`; an independent Video Workflow Run uses its `run_id`. The Resource Admission Module performs equal round-robin across Fairness Groups. Inside a Batch group, it performs equal round-robin across member `run_id` values. Inside one run, it considers tasks by persistent `enqueue_seq`.

One scheduling pass admits at most one task from each Fairness Group. Further passes continue while at least one complete resource request is feasible. Admission acquires every declared Resource Class in one serialized transaction. No attempt may hold only part of its declared request.

A task may be bypassed when its complete request is unavailable and a later candidate can run. Each durable bypass increments `bypass_count`. When it reaches the `effective_bypass_threshold` from the Resource Admission Configuration active for that scheduling decision, the task receives a durable `reservation_seq` and requests a Draining Reservation. It becomes active immediately when its required resource set is disjoint from all earlier active reservations; otherwise it remains a Pending Draining Reservation under ADR 0044. The scheduler stops admitting new tasks that consume any resource required by an active reserved task. Existing holders finish normally, unrelated resources remain schedulable, and the reserved task receives its complete request atomically when capacity becomes available.

The initial threshold of eight is part of the versioned Resource Admission Configuration. Changing it affects later scheduling decisions and does not rewrite prior queue history. The first implementation has equal weights only; weighted groups, critical-path scoring, preemption, runtime adaptation, and duration-based aging are deferred.

Queue and scheduler records include at least:

- `queue_id`, `task_id`, `attempt_id`, `run_id`, `fairness_group_id`, and optional `batch_id`;
- `enqueue_seq`, `required_resources`, `claim_generation`, and configuration identity plus SHA-256;
- queue state, `bypass_count`, reservation state, `lease_id`, and optional `admitted_seq`;
- durable next sequence values, `reservation_seq`, and group/run round-robin cursors.

Scheduling has these invariants:

- one current Task Attempt has at most one queue item;
- a request exceeding configured capacity fails validation before enqueue;
- a new admission never exceeds available capacity or increases an existing overcommitted condition; active leases may temporarily exceed a newly lowered quota under ADR 0043;
- an open Resource Circuit Breaker excludes affected tasks from admission;
- one transactional scheduler writer serializes every queue decision;
- active tasks are never preempted;
- an unresolved `unknown` lease keeps consuming capacity until explicit reconciliation;
- configuration replacement does not reorder existing queue items.

Recovery replays durable queue decisions and cursors. It never reconstructs order from timestamps, filesystem enumeration, or process completion order.

## Consequences

Standalone requests receive service alongside large batches, and each Batch item progresses fairly inside its group. Feasible unrelated work can bypass a blocked task, while the configured-threshold reservation guarantees eventual capacity drainage for older multi-resource work. The scheduler gains durable cursor and reservation state that requires transaction, crash-recovery, and starvation tests.

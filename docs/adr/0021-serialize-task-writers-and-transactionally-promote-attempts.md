# Serialize task writers and transactionally promote attempts

Parallel section work is valuable, while duplicate agents writing the same artifact can corrupt evidence and waste execution. Long-running reviewers also make time-based lock expiry unsafe. A further constraint is that Windows does not provide a single atomic replacement operation for an arbitrary set of files, so multi-file publication needs a recoverable commit protocol.

## Considered Options

- Allow duplicate writers and keep the last result: rejected because completion order would decide the artifact silently.
- Expire task ownership automatically after a timeout: rejected because a healthy long-running agent could continue writing after a replacement agent starts.
- Let agents write canonical paths directly and rely on checkpoint hashes afterward: rejected because partial output sets become visible during generation.
- Use exclusive claims, attempt-scoped staging, and journaled promotion: selected because parallelism remains safe for disjoint work and recovery remains explicit.

## Decision

Each logical task has at most one active Task Claim. A claim records the Task Authority Binding, `task_id`, `attempt_id`, coordinator session identity, worker identity, claim time, and the envelope's declared write set. The Kernel rejects a new claim whenever its write set overlaps an active claim in the same authority scope. Writer and Figure tasks for different canonical sections may execute concurrently when their declared paths are disjoint.

Every execution receives a unique script-created Task Attempt directory inside the task location declared by its envelope. Run-scoped Attempts use `workflow/tasks/<task-id>/attempts/<attempt-id>/`; Final Acceptance Attempts use the matching Acceptance Execution Context. The subagent writes only within that staging boundary. It cannot write canonical artifacts, update task state, or promote its own outputs. Failed validation leaves the complete attempt and its evidence intact.

Task execution and Attempt staging may run concurrently when their declared writes and resources allow it. Run-scoped canonical promotion follows ADR 0046: one Video Workflow Run has at most one non-terminal Mutation Intent, so every promotion and `workflow/run.json` replacement commits serially even when artifact write sets are disjoint. Final Acceptance patch and report publication follows the module-scoped Acceptance Execution Promotion Slot in ADR 0056.

Claims do not expire solely because wall-clock time passes. Passing the Task Completion Gate moves an Attempt to `validated_waiting_for_promotion` and keeps its Claim active. An Attempt becomes terminal only after its own promotion Mutation Intent reaches `COMMITTED`; explicit `task-fail`, `task-cancel`, ownership handoff, or reclaim may also end the logical Claim under their evidence rules. Each Final Acceptance Reviewer Patch receives its own committed evidence promotion before report materialization, so the provider never inherits or collectively ends Reviewer Claims. After a coordinator or session failure, `task-reclaim` must name the expected prior claim, record a recovery reason, and advance the Claim Fencing Token. The prior attempt becomes logically `abandoned`, and the replacement receives a new attempt identity and staging directory. Its Resource Lease remains independently governed by ADR 0045. No automatic timeout may launch a duplicate Acceptance Reviewer or writer.

After the Task Completion Gate passes and the applicable promotion slot is available, the Kernel performs Transactional Artifact Promotion. It writes a promotion journal, preserves any displaced canonical files in a registered `待删除/task-promotions/` location, copies the validated Attempt outputs to their canonical paths, verifies the promoted fingerprints, and replaces the Task Authority Binding's coordination record as the commit marker. The final database transaction marks the Mutation Intent `COMMITTED` and ends that Attempt's Claim. Downstream tasks may consume only a committed generation. If promotion stops partway through, reconciliation uses the journal to complete or block recovery; partial files cannot advance a checkpoint.

Task Attempt identity is shared across ordinary retries, repair tasks, and Acceptance Reviewer executions. Acceptance's domain-specific `attempt_01` through `attempt_03` repair budget remains a separate gate-level concept and references the underlying task attempts.

## Consequences

Disjoint section work keeps its parallelism, and overlapping writes fail before agent launch. Long reviewers are protected from false timeout replacement. Every failed or abandoned execution remains inspectable. Promotion requires journaling, generation-aware readers, crash-recovery tests, and registered preservation paths instead of direct overwrite.

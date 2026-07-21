---
status: accepted
---

# Adopt persisted command execution

Repository operations can outlive the agent session that launched them. Ordinary terminal output provides no durable proof of the final exit code after that session disappears, and generated artifacts alone cannot prove that the complete command succeeded.

## Considered Options

- Keep long commands attached to the initiating agent session: rejected because session loss also loses reliable observation and may cause expensive duplicate execution.
- Redirect output from an ordinary background process: rejected because logs alone do not prove process identity, heartbeat freshness, terminal classification, or the actual exit code.
- Let a later session restart or take over an uncertain process: rejected because mutation during observation can destroy the distinction between the original execution and a rerun.
- Use a detached supervisor with versioned persisted records: selected because execution ownership, process identity, complete streams, heartbeat, exit code, and recovery remain queryable across sessions.

## Decision

A repository-wide persisted-command runner owns qualifying non-interactive operations. Qualification is based on duration, the need for later waits, possible survival beyond the initiating session, re-execution cost, or evidentiary value. The contract applies consistently to tests, downloads, transcription, rendering, compilation, migration, recovery, and batch operations.

The runner uses a detached supervisor to separate command lifetime from agent-session lifetime. Each start creates durable evidence under `待删除/long-running/`: versioned command and status records, complete stdout and stderr streams, a labelled merged log, heartbeat and process identity, and an actual terminal exit code when one exists.

Each run ID and directory form immutable history. Accepted exit codes are fixed before launch. Observation may reconcile persisted state against process identity, while it cannot restart, terminate, attach to, or take over the target. Every rerun creates a new identity and leaves earlier evidence unchanged.

Persisted execution heartbeat and user-facing progress are separate projections. The heartbeat proves execution observability; it does not create a notification obligation. User-facing updates are event-driven and limited to terminal state, security eligibility changes, explicit target milestones, errors, blockers, and user decisions. Repeated `running` observations, log growth, heartbeat refreshes, and individual observation-window timeouts remain silent.

When a result blocks delivery, the initiating task may continue silent observation. When it does not block delivery, the task returns the immutable run directory and allows a later session to recover observation. A `wait` timeout with a current `running` state changes no command authority or failure classification.

Retention is manual retention. The runner never truncates, rotates, overwrites, expires, or deletes a run. Repository operators may later move obsolete material within the deletion-staging policy, and permanent deletion remains outside automated execution.

Persisted metadata excludes environment values and redacts recognized credential-bearing arguments. Complete target output remains the target command's responsibility. Secret detection makes a run ineligible as acceptance evidence and preserves the failure classification without publishing the detected value.

## Rejected Alternatives

Automatic restart, automatic cleanup, log rotation, process takeover, environment capture, artifact-based success inference, and post-launch exit-code reclassification are rejected. Each weakens either provenance, safety, complete evidence, or immutable interpretation of the original command.

## Consequences

`AGENTS.md` and `CLAUDE.md` carry the same mandatory qualification, observation, and notification contract. Operators recover work through `list`, `show`, `reconcile`, and `wait`, using the run directory returned by `start` as the stable handle. The CLI help points operators to the detailed operations guide.

The runner remains repository execution infrastructure. It does not activate Workflow Kernel 2.0, and it does not replace Acceptance Reports, Delivery Guard reports, Exit Evidence manifests, Workflow Kernel Run Records, or their authority boundaries.

# Admit tasks through fixed resource-class quotas

The current Batch runner exposes one concurrency value for an end-to-end item even though platform downloads, Whisper, Codex semantic work, LaTeX, PDF rendering, and visual inspection stress different resources and have different failure domains. Submitting every item future at once also allows an authentication failure to arrive after many additional platform tasks have already started.

## Considered Options

- Keep one global Batch concurrency limit: rejected because it cannot express resource contention or isolate infrastructure failures.
- Let every role decide when to start: rejected because independent callers cannot prove aggregate capacity or prevent oversubscription.
- Adapt concurrency automatically from observed speed: deferred because the first implementation lacks stable workload and resource telemetry.
- Admit tasks through fixed versioned resource quotas: selected because behavior is predictable, configurable, and recoverable.

## Decision

Every executable task declares one or more Resource Classes in its immutable task contract. The initial Resource Admission Configuration defines these conservative defaults:

- one active download per source platform;
- one Whisper transcription;
- two Codex semantic-agent tasks;
- one LaTeX compilation;
- one PDF page-rendering task;
- one Visual Acceptance task.

The Resource Admission Module queues otherwise runnable tasks and atomically acquires capacity for every class a task requires. It does not partially acquire a task's resources, so multi-class tasks cannot create hold-and-wait deadlocks. Admission creates a durable record tied to the Task Claim and Task Attempt. The attempt records the configuration version and SHA-256 used for its admission.

Projects may change quotas through a validated, versioned configuration. A new configuration affects only later admissions and never preempts active tasks. The first implementation has no runtime auto-tuning, distributed queue, cross-machine scheduling, or hidden command-line concurrency override.

Resource Circuit Breakers isolate infrastructure faults. A rejected or expired cookie opens the breaker for downloads from that platform and requests refreshed user input. Codex, Whisper, MiKTeX, or PDF-rendering infrastructure faults pause only the corresponding resource queue. A video-specific content, quality, or semantic failure blocks only its Video Workflow Run and does not open a shared resource breaker.

A process or coordinator crash cannot itself write a new lease state. After restart, `resource-reconcile` compares admission records, Task Claims, Task Attempts, process identity evidence where available, and committed artifacts; unresolved ownership then moves to `unknown`. Capacity is not released merely through elapsed time or Task Claim reclaim. Resource Lease release follows the evidence rules in ADR 0045.

Batch Supervisors submit only tasks admitted by this Module and do not create every future in advance.

## Consequences

Heavy local tools no longer compete through one coarse setting. Authentication and infrastructure failures stop the relevant queue early while unrelated work continues. Defaults favor safety over peak throughput and remain user-configurable. The scheduler needs deterministic fairness, queue ordering, and configuration validation tests.

# Bind checkpoint freshness to SHA-256 artifact generations

Workflow reports can remain structurally valid after their inputs change. File existence, timestamps, and phase labels cannot prove that a checkpoint still applies to current content. Full rehashing of immutable video media on every orchestration step would add avoidable I/O, while replacing SHA-256 would conflict with existing Pyramid, compile, acceptance, and delivery-guard contracts.

On 2026-07-14, an in-memory benchmark in the project Python environment measured SHA-1 at approximately 1.337 GiB/s and SHA-256 at approximately 1.060 GiB/s. The gain was about 26 percent before filesystem I/O. BLAKE2 and MD5 were slower in that environment, and BLAKE3 and XXH3 were not installed. SHA-1 and MD5 also lack the collision resistance expected from durable evidence.

## Considered Options

- Treat timestamps and file size as authoritative freshness evidence: rejected because content can change without a reliable semantic generation boundary.
- Replace SHA-256 with SHA-1: rejected because the measured gain is modest, collision resistance is weaker, and current gate schemas explicitly require SHA-256.
- Recompute every large-file digest before every operation: rejected because finalized source packages are immutable generations and repeated reads add no new evidence under the workflow trust model.
- Keep SHA-256 authoritative and cache fingerprints for frozen generations: selected because gate compatibility and evidence strength remain stable while redundant work is removed.

## Decision

The Video Workflow Run Record keeps the current artifact registry. Every canonical artifact entry includes a logical artifact identifier, canonical path, monotonically increasing generation, SHA-256 digest, producing task or kernel operation, and commit time. Task Attempt files and prior canonical generations remain historical evidence and do not become current through filesystem recency.

Every completed Workflow Checkpoint binds the exact input artifact generations and SHA-256 values it evaluated, plus its prerequisite checkpoint evidence. When Transactional Artifact Promotion or explicit artifact adoption commits a new upstream generation, the kernel marks every transitively dependent checkpoint `stale`. Gate-specific reports retain their original decisions as history, but only a report bound to current inputs may authorize progress. Workflow Phase is recomputed from the checkpoint graph.

`reconcile-run` executes before resume, task preparation, task completion, delivery readiness, and the final delivery guard. It detects missing files, uncommitted promotion journals, path violations, and content that differs from the registry. An out-of-band canonical edit becomes Artifact Drift. `artifact-adopt --reason` preserves the previous evidence, hashes the current file, commits a new generation, and propagates invalidation. `artifact-restore` restores a registered generation through a journaled kernel operation.

SHA-256 is the sole authoritative content digest. Source finalization, Verified Source Import, source reopen completion, Task Completion Gate, Transactional Artifact Promotion, acceptance-skeleton generation, and final delivery checking perform or validate full SHA-256 hashing for their trust boundary. Small mutable TeX, Markdown, JSON, and report artifacts may be rehashed during routine reconciliation.

Large frozen source artifacts may use a Trusted Fingerprint Cache between trust boundaries. The cache records the committed SHA-256 together with filesystem identity, byte size, and nanosecond modification time. A change to any signal forces a full rehash. The cache is a performance assumption under the run's read-only source policy; it is not a second digest authority. A deliberate out-of-band rewrite that preserves every cache signal remains a limitation and is outside the cooperative local-agent threat model.

BLAKE3 remains a future optimization candidate. It requires profiling that proves hashing is a material bottleneck, a pinned dependency, explicit algorithm-tagged schemas, and a migration ADR. SHA-1, MD5, and non-cryptographic hashes do not enter the authoritative evidence chain.

## Consequences

Freshness becomes content-bound and transitively enforceable across Pyramid, consistency, independent review, compilation, acceptance, and delivery. Final PDF or `main.tex` changes automatically stale their dependent evidence. Immutable source media avoids repeated full reads during routine orchestration. Recovery and manual edits remain possible through explicit, evidence-preserving operations.

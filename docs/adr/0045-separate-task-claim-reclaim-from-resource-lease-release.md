# Separate task claim reclaim from resource lease release

A coordinator may disappear while its worker process, external provider request, or subagent continues running. Reclaiming logical write authority can fence a late worker from publishing, but it cannot prove that the old worker stopped consuming a constrained resource. Releasing capacity as a side effect of claim reclaim could therefore admit work above the real quota.

## Considered Options

- Release the old Resource Lease whenever the Task Claim is reclaimed: rejected because coordinator failure is not worker-termination evidence.
- Let leases expire after a fixed timeout: rejected because a slow valid task and an abandoned task are indistinguishable from elapsed time alone.
- Block logical reclaim until physical termination is proven: rejected because stale write authority should be fenced immediately even when capacity remains uncertain.
- Reclaim the claim immediately and resolve the lease independently from evidence: selected because it protects canonical state and physical capacity separately.

## Decision

`task-reclaim` performs a compare-and-set against the expected prior Task Claim, allocates a new Task Attempt, and increments the monotonic `claim_generation`. This generation is the Claim Fencing Token required by every task completion, Transactional Artifact Promotion, Resource Lease mutation, and recovery action. A late worker carrying the old generation cannot publish, complete, cancel, or release capacity.

Reclaim does not release or transfer the old Resource Lease. After coordinator restart, `resource-reconcile` may transition an unresolved `starting` or `active` lease to `unknown`; the crash itself cannot perform this write. An Unknown Resource Lease continues to count against every Resource Class in its immutable request.

A replacement Task Attempt may enter the queue after reclaim. It receives admission only when remaining capacity is available under the normal scheduler. The scheduler never discounts the old unknown lease merely because its claim was fenced.

`resource-resolve` may place an unknown lease in a terminal state only with durable resolution evidence. Accepted evidence classes are:

- local process identity evidence matching PID, process creation identity, and the unique launch token, followed by proof that the matching process no longer exists;
- an authenticated provider terminal result bound to the lease and attempt identity;
- an explicit human resolution that records the lease, declared outcome, reason, observed termination basis, coordinator identity, and time.

PID absence without the recorded process-creation identity is insufficient because operating systems reuse process identifiers. A heartbeat timeout, missing status file, session disappearance, or new Claim Generation is also insufficient termination evidence.

If no accepted evidence exists, the lease remains `unknown`, capacity stays occupied, and the run exposes a blocking recovery reason. An explicit human resolution is auditable risk acceptance and never occurs automatically.

## Consequences

Old workers cannot corrupt canonical artifacts after reclaim, and uncertain physical resource use cannot silently disappear from quotas. Some runs may remain capacity-blocked until process or provider evidence becomes available. Tests must cover late-worker return, PID reuse, reclaim with spare capacity, reclaim without spare capacity, repeated resolution, and concurrent resolve versus stale completion.

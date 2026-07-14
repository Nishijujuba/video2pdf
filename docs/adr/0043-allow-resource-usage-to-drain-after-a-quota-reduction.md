# Allow resource usage to drain after a quota reduction

A Resource Admission Configuration may lower a quota while more leases are already active than the new limit allows. Preempting those tasks would violate the approved no-preemption rule. Rejecting the configuration would prevent an operator from expressing an emergency lower limit. Requiring current usage to remain within every newly activated quota is therefore incompatible with both requirements.

## Considered Options

- Terminate active tasks until usage reaches the new quota: rejected because active Task Attempts are never preempted by configuration replacement.
- Reject any quota reduction below current usage: rejected because it prevents immediate throttling of future work.
- Treat the old quota as active until usage drains: rejected because later scheduling decisions would have ambiguous configuration authority.
- Activate the new quota and block capacity-increasing admissions while usage drains: selected because configuration identity remains clear and active work finishes safely.

## Decision

A validated Resource Admission Configuration activates immediately for later scheduling decisions. When current `starting`, `active`, and `unknown` Lease usage exceeds a lowered quota, the affected Resource Class enters `overcommitted` state.

Existing leases continue and retain their original admission evidence. A new task requiring the affected class cannot be admitted while its admission would increase or preserve usage above the new quota. As leases reach a proven terminal state and release capacity, the class automatically leaves `overcommitted` after usage falls below the quota; normal capacity calculation then resumes. Resource Classes outside the task's request continue scheduling independently.

The scheduling invariant is:

> A new Admission must fit the quota in force for that decision and must never increase an existing overcommitted excess. A configuration change may temporarily make existing usage exceed its new quota without invalidating or preempting existing leases.

The complete Resource Request is immutable from enqueue onward. Quotas, Resource Circuit Breaker state, and the configured bypass threshold are evaluated using the configuration active for each scheduling decision. Every enqueue, bypass, reservation, admission, and configuration-driven block event records the effective configuration version and SHA-256.

The Cross-Run Control Store derives usage from normalized lease-resource rows. It does not modify lease evidence merely to make aggregate usage appear compliant.

## Consequences

Operators can lower future pressure immediately while ongoing work finishes. Monitoring must distinguish ordinary full capacity from an overcommitted drain. Tests must cover quota reductions below active usage, unrelated-resource progress, crash-created `unknown` leases, and recovery to normal admission after sufficient releases.

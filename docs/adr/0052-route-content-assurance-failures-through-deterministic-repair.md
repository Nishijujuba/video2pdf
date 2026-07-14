# Route content-assurance failures through deterministic repair

Content Assurance requires both Consistency and Source-Faithfulness reports to pass. The prior design stated that failures route to repair without defining an executable input contract. The existing Repair Planning Module accepted only Acceptance Report v2, so an assurance failure could block before `content_assurance_ready` with no valid planner input.

## Considered Options

- Send either failed report directly to a general repair agent: rejected because write ownership and findings from the other completed review would be lost.
- Treat every assurance failure as a manual block: rejected because ordinary fidelity and consistency defects are expected repair cases.
- Merge the two reviews into a new combined decision authority: rejected because their separate report authority is already intentional.
- Materialize a normalized failure set and reuse deterministic Repair Planning: selected because repair mechanics remain shared while the two reports retain authority.

## Decision

`content-assurance-materialize` always waits for both technically valid reports. When either semantic result fails, the provider creates an attempt-scoped Content Assurance Failure Set under `work/repairs/content_assurance/cycle_XX/`. It contains:

- both source report identities, paths, SHA-256 digests, statuses, and common input Artifact Generations;
- every blocking finding, evidence location, affected artifact identity, required change, and registered repair capability;
- the earliest affected Workflow Checkpoint computed from the dependency graph;
- the assurance cycle identity and prior-cycle lineage.

The failure set does not replace `review/consistency/report.json` or `review/independent/report.json` and cannot grant a pass. It is valid only while both source reports and their common draft generation remain current.

The Repair Planning Module validates the failure set, computes candidate write sets, merges conflicts into Integration Repair, and writes `work/repairs/content_assurance/cycle_XX/plan.json`. Repair Agents receive only their assigned findings and evidence. Outputs pass normal Task Completion and Promotion rules.

After promotion, the Kernel invalidates the earliest affected checkpoint, reruns applicable Section and Main Pyramid gates, integrates a new `main.tex` generation, produces a new guarded diagnostic draft compile, and launches both Content Assurance Adapters against the same new generation. A failure in one Adapter never permits reusing the other Adapter's old pass after artifacts change.

Content Assurance cycles are distinct from the three materialized Final Acceptance attempts and do not consume that budget. Every cycle is durable and user-visible. An automatic cycle starts only when the deterministic plan has at least one valid repair task; an unmapped finding, impossible write boundary, repeated stale input, or explicit user stop produces a manual assurance-repair block rather than an invented fix.

## Consequences

Consistency and source-fidelity defects have a complete script-owned repair path before finalization. Final Acceptance receives only a draft that already passed both upstream reviews. The project needs failure-set schema fixtures, routing tests, conflict tests, one fail-repair-pass tracer, and manual-block evidence.

# Make cross-dimension acceptance failures dominant

Text and visual criteria have one primary owner so required coverage remains clear. During full review, either agent can still encounter a delivery-blocking defect assigned to the other dimension. Ignoring that evidence would knowingly permit a defect; asking every agent to evaluate every criterion would recreate the wide review context that the split removes.

## Considered Options

- Ignore findings outside an Adapter's primary partition: rejected because observed blocking defects could disappear from the final decision.
- Require both Adapters to evaluate every criterion: rejected because the partitions would cease to reduce context and duplication.
- Launch a third adjudication agent whenever dimensions disagree: deferred because it adds an unbounded semantic branch to the common path.
- Keep one primary owner and allow evidence-bearing cross-dimension failures to dominate: selected because coverage remains local and known failures remain blocking.

## Decision

Every configured Acceptance Criterion has exactly one Primary Acceptance Dimension. The criteria assignment registry must be disjoint and complete. Only the primary owner is required or permitted to provide that criterion's normal pass-or-fail result.

Both acceptance Judgment Patch schemas support `cross_dimension_findings`. Each Reviewer receives the Acceptance Criterion Reference Index defined by ADR 0041. A Cross-Dimension Finding references an existing `criterion_id` owned by the other dimension and supplies final-artifact path, concrete location, problem summary, required change, and allowed repair types. It may report only a blocking failure and cannot grant or reinforce a pass.

The materializer validates each finding against the shared skeleton, allowed manifest, criteria file, and reporting Adapter's actual read set. When the primary result is `pass` and a valid cross-dimension finding exists, the materialized criterion result becomes `fail`. When the primary result is already `fail`, evidence and compatible repair guidance are merged deterministically. Duplicate evidence is normalized by criterion, artifact, location, and summary. Failure is dominant; the first implementation launches no semantic adjudicator.

An observation that cannot map to a configured criterion is an Acceptance Contract Gap. The provider does not invent a criterion or place it into an unrelated category. It blocks report materialization, preserves both Task Attempts, and generates a criteria-gap brief containing the observation and evidence. This orchestration-level block does not consume the three materialized semantic repair attempts. Criteria changes or explicit human disposition remain outside the reviewer Adapter's authority.

Advisory improvements and non-blocking suggestions remain outside final acceptance and cannot be encoded as cross-dimension failures.

## Consequences

Each dimension retains a focused primary workload while either can stop delivery for a concrete defect it actually observes. Deterministic failure dominance avoids a third-agent loop. False-positive cross findings can create conservative failures, so patch schemas, evidence requirements, and regression cases must be strict. Unknown quality gaps become visible contract work instead of disappearing or being silently reclassified.

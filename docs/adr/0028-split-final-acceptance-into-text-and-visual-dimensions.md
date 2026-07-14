# Split final acceptance into text and visual dimensions

One Acceptance Reviewer currently carries full-text analysis, terminology and formula judgment, rendered-page inspection, every-page evidence, and the final report structure in one context. Text and visual evidence have different input costs and failure modes. Keeping them in one agent creates a wide Interface and makes long visual work compete with semantic text review for context.

## Considered Options

- Keep one reviewer for all criteria: rejected because unrelated evidence types remain coupled and cannot run concurrently.
- Expose separate text and visual workflows to every caller: rejected because coordination, aggregation, and retry rules would leak through a shallow Interface.
- Build an open-ended multi-dimension framework before the first split: rejected for initial use because unneeded dimensions would enlarge the schema and testing matrix.
- Keep a two-operation external Interface with an internal dimension Adapter registry: selected because the common path is small and future dimensions can remain an implementation change.

## Decision

Final acceptance becomes a deep Final Acceptance Module with two coordinator-facing operations: `acceptance-prepare` and `acceptance-materialize`. Its internal Acceptance Dimension Adapter Seam initially registers exactly two semantic Adapters.

The Text Acceptance Reviewer owns `style`, `logic_readability`, and `formula_information_gain`. The Visual Acceptance Reviewer owns `figure_visual_integrity`, `table_layout_integrity`, and `credibility_disclosure_placement`. The module validates that their primary criterion partitions are disjoint and their union covers every configured criterion.

`acceptance-prepare` requires fresh final compile and rendered-page evidence. Under ADR 0056, it creates one Acceptance Execution Context, one immutable Acceptance Report Skeleton, and two isolated Subagent Task Envelopes bound to the same criteria fingerprint, input authority, artifact generations, page set, and skeleton SHA-256. Both reviewers start from clean contexts, remain read-only, write attempt-scoped Judgment Patches, and cannot inspect each other's patch, generation notes, or repair discussion.

The Visual Acceptance Reviewer inspects every rendered page image individually and records exactly one entry for every page in `1..page_count`. Contact sheets, montages, thumbnails, or samples remain auxiliary navigation and cannot satisfy the review. The Text Acceptance Reviewer consumes the final text artifacts, optional allowed Delivery Glossary, and its assigned criteria without loading rendered pages as a duplicate visual scan.

Both dimensions run concurrently. Finding a semantic failure in one dimension does not cancel the other because the acceptance policy requires all failures to be reported. After each Task Completion Gate passes, `acceptance-patch-commit` independently promotes that dimension's Patch and ends its Reviewer Claim. `acceptance-materialize` runs only after both committed Patch generations are current. It validates complete criteria and page coverage, merges results in criteria-file order, derives overall status and failed criteria deterministically, creates repair routes, and publishes the sole authoritative `acceptance_report.json` through a provider Mutation Intent against the Acceptance Execution Context.

A missing, stale, malformed, timed-out, unauthorized, or uncommitted patch is an Acceptance Orchestration Failure. It blocks delivery and may retry only the failed dimension against the same still-current skeleton. It does not consume the three-attempt semantic repair budget because no complete Materialized Gate Report exists. A valid materialized failure consumes one repair attempt. Any repair that changes an in-scope artifact invalidates the execution context, skeleton, and both patches, so both dimensions run again. After the third materialized failure, the existing manual-repair and blocked-state rules apply.

`delivery_guard.py` continues to consume one Acceptance Report and cannot replace either semantic dimension. The internal Adapter registry may later support additional dimensions, but only Text and Visual are implemented in the first phase. Visual page sharding remains a separate future decision.

## Consequences

Text review avoids image context, visual review avoids unrelated TeX and terminology analysis, and wall-clock time approaches the slower parallel dimension. Callers learn two operations while criteria partitioning, patch schemas, aggregation, retries, and repair routing remain local to one Module. Two agent launches and two patch validations add orchestration cost. Cross-dimension findings and report provenance are governed by their dedicated follow-up ADRs.

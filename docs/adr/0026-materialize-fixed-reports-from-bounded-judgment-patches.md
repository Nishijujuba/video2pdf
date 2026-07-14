# Materialize fixed reports from bounded judgment patches

Giving an agent a skeleton prevents blind schema invention, but asking it to copy or edit the full report still consumes tokens and permits accidental changes to paths, fingerprints, page counts, and schema metadata. Review agents need authority over semantic judgments and findings only. Provider scripts can combine those judgments with current mechanical evidence.

## Considered Options

- Let the agent author the complete report after reading a schema: rejected because generated evidence fields remain writable and the full structure must be reproduced.
- Let the agent modify the skeleton file in place: rejected because the immutable input and agent changes become difficult to distinguish or audit.
- Have the agent submit a separate bounded response and materialize the final report: selected because structural evidence and semantic judgment retain separate owners.

## Decision

Every fixed semantic report flow has an immutable Contract Skeleton, a role-specific Judgment Patch schema, and a provider materialization operation. The skeleton is generated at its Earliest Valid Generation Checkpoint, validated, and fingerprinted before agent launch. The matching Subagent Task Envelope identifies the skeleton, its SHA-256, the judgment schema, and the attempt-scoped judgment output path.

The agent writes only a Judgment Patch. Every patch includes `task_id`, `attempt_id`, `skeleton_sha256`, and fields explicitly allowed by its schema. Generated identities, input paths, artifact fingerprints, rule identifiers, page count, schema metadata, and other structural fields are absent from the writable response shape. A mismatched skeleton hash or unauthorized field fails closed.

After the Task Completion Gate, the governing task runtime validates the current Claim Fencing Token and promotes the Judgment Patch as a committed evidence generation. That committed promotion ends the Patch Attempt's Claim. The Gate Provider then verifies the committed patch fingerprint, current skeleton fingerprint, authority binding, and runtime invariants, merges semantic values through a registered materializer, and validates the complete result against the authoritative report schema. It publishes the Materialized Gate Report through a separate provider Mutation Intent. The provider never reuses or collectively ends Reviewer Claims, and a Patch cannot independently authorize a checkpoint.

Final Acceptance is specialized by ADRs 0028–0030 and 0056. It uses one shared `acceptance_report.skeleton.json`, one Text Judgment Patch, and one Visual Judgment Patch inside an Acceptance Execution Context. Only the Visual Patch requires exactly one page-specific response for every skeleton page slot from `1..page_count`; the Text Patch evaluates its assigned text criteria without duplicating page inspection. The provider starts after both Patches have committed independently, validates them, and materializes `acceptance_report.json`, which remains the only machine-readable delivery decision source. Each Reviewer may read its immutable task envelope, Judgment Patch schema, shared skeleton, assigned criteria, and the Acceptance Criterion Reference Index as control artifacts; these files cannot add content evidence beyond the allowed-artifact manifest.

Source Acquisition Decision, Consistency Review, Independent Review, and later fixed semantic reports adopt the same pattern. Pyramid already keeps final report writing inside its evaluator wrapper and may converge on the same patch boundary without changing its gate authority. Writer and Figure agents produce staged content artifacts and do not use Judgment Patch materialization.

Optional Markdown summaries are generated from the validated final JSON and have no gate authority.

## Consequences

Agents spend tokens on judgments and evidence instead of reconstructing generated metadata. Provider scripts become the sole writers of canonical report structures. Skeleton tampering, stale responses, incomplete page coverage, and unauthorized structural changes become deterministic validation failures.

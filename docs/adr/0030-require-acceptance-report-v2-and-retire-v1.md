# Require Acceptance Report v2 and retire v1

The split acceptance design can materialize a v1-shaped final report, but that shape cannot prove which Text and Visual tasks, prompts, skeleton, or Judgment Patches produced its decision. Keeping parallel v1 and v2 branches would preserve compatibility at the cost of additional validator, guard, fixture, and recovery paths. The workflow is already making a deliberate breaking redesign.

## Considered Options

- Keep v1 for legacy outputs and require v2 only for new runs: rejected because two delivery-authorizing report contracts would remain active.
- Infer report version from field presence: rejected because compatibility and authority would be ambiguous.
- Convert old v1 reports mechanically to v2: rejected because missing dual-review provenance cannot be reconstructed honestly.
- Retire Acceptance Report v1 and require a fresh v2 review: selected because every delivery decision follows one evidence standard.

## Decision

`acceptance_report.json` uses Acceptance Report Schema v2 for every supported delivery flow, including previously generated PDFs that are submitted for fresh acceptance. Acceptance Report v1 is unsupported by the validator, materializer, and Delivery Guard. No compatibility branch or report migration command is implemented.

This retirement applies only to the Acceptance Report contract. `docs/acceptance/acceptance_criteria.v1.json` and its independently versioned criteria schema remain valid until a separate criteria decision changes them.

Acceptance Report v2 preserves criterion results, visual scan evidence, failed criteria, revision requirement, and the single `acceptance_report_json` decision source. It adds dimension-specific review-context records and required Acceptance Materialization Provenance. Provenance binds:

- the Final Acceptance Provider contract and provider script SHA-256;
- the Acceptance Execution Context identity, Task Authority Binding, input fingerprint, committed revision, and report-publication Mutation Intent;
- the shared Acceptance Report Skeleton path and SHA-256;
- the criteria file path and SHA-256;
- the supported aggregation-policy identifier;
- exactly one `text` and one `visual` dimension;
- each dimension's `task_id`, `attempt_id`, Subagent Task Envelope SHA-256, Generated Task Prompt SHA-256, committed Judgment Patch generation, path and SHA-256, and actual allowed artifact read set;
- the common final artifact generations reviewed by both dimensions.

The v2 materializer derives overall status, failed criteria, and revision requirement from validated dimension evidence. Agents cannot write provenance or aggregate decision fields. `delivery_guard.py` verifies the provider and aggregation versions, exact dimension set, all referenced fingerprints, common artifact generations, and full rendered-page coverage before accepting the report.

Existing v1 reports cannot authorize delivery. A prior PDF must receive a fresh skeleton, Text Acceptance review, Visual Acceptance review, v2 materialization, and fresh Delivery Guard check. During implementation, the superseded v1 schema and fixtures are preserved under a script-created `待删除/` location or retained as explicitly non-executable historical evidence according to the repository file-safety policy; they are never permanently deleted by an agent.

ADR 0051 activates this rule globally. A Legacy Acceptance Input Set may bind old final artifacts to the same v2 review without creating a Run Record. This is a gate-input adapter, not v1 report compatibility: it performs fresh fingerprinting, rendering, dual judgment, materialization, and Guard validation and cannot recover or translate prior reviewer provenance.

## Consequences

The Delivery Guard has one current acceptance contract and can mechanically prove dual-review materialization. Old reports lose authorization and require fresh semantic work. Implementation becomes simpler than dual-version support, while the rollout must clearly identify all v1 fixtures, documentation, reports, and commands that cease to be valid.

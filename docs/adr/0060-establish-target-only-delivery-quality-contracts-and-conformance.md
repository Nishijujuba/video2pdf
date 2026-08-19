# ADR 0060: Establish target-only Delivery Quality contracts and conformance

## Status

Accepted for target-only implementation.

## Conclusion

The repository registers one target-only Delivery Quality policy surface and two public qualification commands. The strict Rule Catalog is the sole normative policy source within that surface. Language Profiles, Role Projections, Waivers, the migration ledger, the conformance corpus, and the Conformance Report bind the catalog without gaining policy or delivery authority.

This decision creates executable target capability. It does not perform the Global Gate Cutover. Active Legacy Final Acceptance v1, current render skills, and Delivery Guard remain authoritative.

## Context

Issue #41 replaces repeated quality prose with one catalog and deterministic projections. Issue #42 is Slice A of that specification. It must prove closed contracts, immutable guidance, complete semantic ownership, and repeatable conformance before precompile assurance or final acceptance can depend on those contracts.

The existing Kernel Schema Registry owns Video Workflow contracts. Delivery Quality needs a separate registry because it has a different policy owner, activation boundary, and public command. Combining both registries would make a target policy change look like a Kernel lifecycle contract change.

## Decision

### Contract authority

- `schemas/delivery-quality/registry.v1.json` is the closed registry for seven versioned Delivery Quality contracts.
- `delivery-quality/v1/rule-catalog.v1.json` is the sole normative quality-policy artifact in Slice A.
- Exact UTF-8 artifact SHA-256 values live in the registry. Rule and Language Profile semantic SHA-256 values cover canonical JSON with the fingerprint field omitted.
- Role Projections reproduce immutable requirement text and blocking effect. Validation rejects any rewrite, stale rule fingerprint, omitted owner, or overlapping owner.
- Generated prompts are deterministic materializations of one Role Projection and the current Rule Catalog.
- Waivers are governed run-specific departures. Catalog exceptions remain repeatable policy conditions.
- The migration ledger preserves Acceptance Criteria v1 meaning, including the intentional split of credibility disclosure content from rendered placement.

### Public verification

- `delivery-quality-contracts-check` validates schemas, positive and negative fixtures, exact artifacts, closed identities, profile references, semantic fingerprints, projection identity, complete Primary Semantic Decision ownership, Waivers, migration entries, corpus completeness, and generated prompts.
- `delivery-quality-conformance` launches a fresh Reviewer-adapter process for every semantic attempt, requires three process-backed contexts for every case and applicable Language Profile, executes six mechanical fixtures through the public contracts command, and materializes one Delivery Quality Conformance Report.
- Any decision, violation, exception, or evidence-locator disagreement across the three semantic attempts records blocking `semantic_variance`.
- The Conformance Report has implementation-qualification authority only. It cannot authorize a video delivery, materialize an Acceptance Report, change activation status, or support a cutover by itself.

### Language and corpus boundary

The first registry includes `zh-hans`, `en`, and `zh-en-bilingual`. Each profile runs twelve semantic cases: five violation/compliant minimal pairs across distinct domains and one valid/rejected exception-boundary pair. The resulting qualification set contains 36 cases and 108 isolated semantic attempts.

Six deterministic cases cover valid and mutated projection identity, Reviewer ownership, and closed violation identities. Every negative mutation must fail through a public command boundary.

## Consequences

- Slice B can bind authoring and evaluation tasks to stable rule and projection identities.
- A policy edit changes the catalog artifact fingerprint; a semantic rule edit also changes that rule's fingerprint.
- A projection or generated prompt cannot drift independently from canonical policy.
- Conformance failures preserve a complete report and cannot be assembled across different failed runs.
- The separate registry and `target_only` fields make accidental runtime activation mechanically visible.

## Rejected alternatives

- Hand-maintained Writer and Reviewer policy prose was rejected because equivalent requirements could drift.
- Role-specific policy catalogs were rejected because projections would gain competing policy authority.
- One or sampled semantic execution was rejected because it cannot expose Reviewer variance.
- A Conformance Report with delivery authority was rejected because implementation qualification and video acceptance have different evidence scopes and owners.
- Registering these contracts as active Final Acceptance v1 artifacts was rejected because that would cross the Global Gate Cutover boundary.

## Activation and rollback

The component remains `target_only` until the separate Global Gate Cutover atomically updates the Delivery Quality Context authority, report materializer, Legacy and Kernel adapters, skills and mirrors, validators, hooks, Delivery Guard, tests, and activation status. Failure of this slice leaves all active Legacy behavior unchanged.

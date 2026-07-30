# ADR 0061: Materialize precompile quality and seal reader-facing text

## Status

Accepted for target-only implementation.

## Conclusion

The Delivery Quality provider uses dual identity before Final Compile: current
Artifact Generation bindings prove source lineage, and a complete
Reader-Facing Text Inventory proves the reader-visible surface evaluated by
independent semantic owners. Three isolated fixed Skeletons collect
Source-Faithfulness, Writing Quality, and Pyramid Judgment Patches. A complete
same-generation materialization may create an immutable Precompile Text Seal.

This implementation remains `target_only`. It does not perform the Global Gate
Cutover or change active Legacy Final Acceptance authority.

## Decision

### Public operations

- `delivery-quality-precompile-prepare` validates the inventory, Artifact
  Generation set, Language Profile, optional Delivery Glossary, current
  Delivery Quality contracts, and fingerprinted external semantic dependencies
  before writing three peer-hidden Reviewer Skeletons.
- `delivery-quality-precompile-patch-commit` accepts only the exact result set
  fixed by one current owner Skeleton. Reviewer independence, task identity,
  policy and provider identity, inventory, and generation bindings fail closed.
- `delivery-quality-precompile-materialize` consumes three immutable committed
  Patches and performs no semantic reinterpretation. Contract Gaps produce a
  human-policy brief and no report. Semantic failures produce the complete
  failure set and deterministic repair routing.
- `delivery-quality-seal` creates a Seal only from a current passing report with
  no Contract Gap.
- `delivery-quality-text-equivalence` accepts only a classified
  `presentation_only` mutation. It proves stable item-identity bijection,
  unchanged exact text, declared surface, policy, Language Profile, Delivery
  Glossary, and semantic dependencies before a successor Seal can reuse prior
  judgments.

### Identity and coverage

Every inventory item binds its stable identity, kind, semantic region,
Language Profile, source Artifact Generation, locator, representation, exact
text fingerprint, item fingerprint, and applicable Writing Quality rules.
Declared regions and coverage entries form a bijection. Raster text requires an
authoritative declared representation.

Writing Quality Skeletons enumerate every applicable `(rule_id, item_id)` pair.
Source-Faithfulness and Pyramid Skeletons bind separately fingerprinted
evaluation projections, providers, and complete scope identities. Each Reviewer
uses a distinct task identity and commits before peer results are available.

### Mutation and repair

Text, policy, projection, Language Profile, Delivery Glossary, provider,
semantic-dependency, and unclassified mutations invalidate affected prepared
judgments. Presentation changes also stale the current Seal and require a
deterministic Text Equivalence Report.

Failures with disjoint write sets may become parallel Content Repair tasks.
Overlapping connected failure sets become one Integration Repair task.
Contract Gaps route to human policy disposition and do not consume the semantic
attempt budget.

## Considered alternatives

- Source-file fingerprints alone were rejected because they cannot prove
  complete reader-visible coverage.
- One extracted-text fingerprint was rejected because it cannot prove current
  compile-input lineage or item-level applicability.
- A mutable Seal was rejected because it would erase predecessor evidence.
- Reviewer-authored reports were rejected because they would grant semantic
  workers mechanical materialization authority.
- String-similarity reuse was rejected because ambiguous mapping would convert
  uncertainty into a pass.

## Consequences

Issue #11 can consume a current Precompile Text Seal and compare it with guarded
Final Compile output. The active Legacy track remains unchanged. A later Global
Gate Cutover must still atomically update final report materialization,
Delivery Guard, hooks, skills, mirrors, and activation documentation.

# ADR 0062: Reconcile sealed text with guarded Final Compile evidence

## Status

Accepted for target-only implementation.

## Conclusion

Guarded Final Compile evidence is admissible only when it binds a current
Precompile Text Seal, an exact compile-input closure, a Final Artifact Seal, the
final PDF, every rendered page, every supported text-bearing object, and one
compiler-produced origin disposition for each rendered object. A deterministic
provider materializes the Rendered Text Reconciliation Report and performs no
semantic reinterpretation.

This implementation remains `target_only`. It does not perform the Global Gate
Cutover or change active Legacy Final Acceptance authority.

## Decision

### Public operation

`delivery-quality-final-compile` validates the current Precompile Text Seal,
exact compile-input closure, and complete Text Origin Plan before invoking a
fingerprinted compiler adapter. The adapter must produce the final PDF, compile
provenance, every rendered page, the object-level rendered inventory, and a
text-origin trace. The guarded provider validates and binds those outputs into
the Final Artifact Seal, Render Evidence Manifest, compiler-produced Text
Origin Manifest, and Final Compile Report.

`delivery-quality-rendered-text-reconcile` validates the sealed precompile
snapshot and the complete Final Compile evidence package before writing one
immutable report. It accepts only a final compile report with complete recorder
closure and `delivery_authority: false`; target capability therefore cannot
activate itself.

### Final evidence bindings

The Final Artifact Seal binds the current Precompile Text Seal, sealed Artifact
Generation set, exact Compile Manifest, guarded compiler provider, and final
PDF identity. The Render Evidence Manifest covers exactly pages
`1..page_count`. The Rendered Text Object Inventory records each object's page,
kind, bounding box, exact UTF-8 representation, extractor, evidence locator,
and fingerprints. Coverage includes page content streams, annotations, Form
XObjects, and declared raster text.

The Text Origin Manifest is produced at the guarded compiler boundary. Every
rendered object has exactly one `sealed_origin`, `generated`, or
`unexpected_addition` disposition. One sealed item may map to several rendered
objects when layout splits a text run. The provider never infers an origin from
string similarity.

### Closed reconciliation behavior

The registered recipes are `exact_utf8`, `layout_whitespace`,
`unicode_presentation`, and `declared_generated`. Generated text is reproduced
only from a registered deterministic generator and its declared inputs.
Unsupported recipes, generators, object kinds, stale identities, ambiguous
edges, unmapped text, missing pages, and incomplete raster or extractor
coverage are Contract Gaps.

Complete evidence distinguishes `omission`, `substitution`, `addition`, and
`generated_mismatch` fidelity failures. Contract Gaps produce
`blocked_contract_gap`; complete fidelity failures produce `fail`; only full
bidirectional coverage with no findings produces `pass`.

### Qualification evidence

Slice 9 Exit Evidence binds the public pass tracer and the public negative
classifications for omissions, substitutions, additions, generated mismatch,
unmapped objects, unsupported recipes, stale seals, compile-input drift,
overlapping origins, unsupported objects, and incomplete page extraction. The
manifest remains target-only and carries no runtime activation authority.

## Considered alternatives

- Aggregate PDF text extraction was rejected because it loses object identity,
  reading order, split-run provenance, generated-text lineage, and raster text.
- Similarity matching was rejected because uncertain provenance would become a
  guessed pass.
- Letting the final materializer repair missing provenance was rejected because
  final materialization may aggregate current evidence and cannot create it.

## Consequences

Issue #12 can consume a current Rendered Text Reconciliation Report as
mechanical evidence for Acceptance Report v2. It may validate freshness and
aggregate the decision. It cannot weaken a fidelity failure, fill a Contract
Gap, or reinterpret text.

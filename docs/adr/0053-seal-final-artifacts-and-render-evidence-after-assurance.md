# Seal final artifacts and render evidence after assurance

Content Assurance reviews a guarded diagnostic draft PDF, while Final Acceptance requires a fresh final PDF, final compile provenance, and one rendered image for every page. The prior implementation route did not define the transition that creates these final inputs. Letting Acceptance invoke compilation or rendering would mix semantic review with mutable artifact production.

## Considered Options

- Let the Acceptance Reviewer compile or render missing inputs: rejected because Reviewers are read-only and cannot establish their own evidence.
- Treat the diagnostic draft compile as final compile provenance: rejected because a Temporary Compile cannot authorize delivery.
- Final-compile before Content Assurance and reuse it afterward: rejected because assurance repair may change any compile input.
- Seal, final-compile, and render after assurance passes: selected because the final evidence has one current production boundary before read-only acceptance.

## Decision

The Kernel operation `finalize-delivery-evidence` requires a fresh `content_assurance_ready` checkpoint. It performs these deterministic steps:

1. validate the current integrated `main.tex`, Integration Manifest, Compile Manifest, all Pyramid reports, and both Content Assurance reports;
2. create a Final Artifact Seal that binds every final compile input generation and fingerprint;
3. invoke the guarded compile provider in `final` mode with the sealed Compile Manifest;
4. transactionally promote the final PDF and `review/latex/compile_report.json`, both bound to the Seal and compile-provider identity;
5. render every PDF page into attempt staging and generate a Render Evidence Manifest containing exactly pages `1..page_count` and their SHA-256 digests;
6. promote the rendered pages, Render Evidence Manifest, and allowed-artifact manifest;
7. compute `final_evidence_ready` only after all artifacts and reports validate and remain current.

The final PDF filename follows the project normalization and deliverable-version rules. Temporary compiler outputs and renderer intermediates remain under governed `待删除` paths and have no delivery authority.

`acceptance-prepare` for a Kernel Track run requires `final_evidence_ready`. It creates the v2 Skeleton and dimension tasks from the sealed final generations. The Visual Reviewer still inspects every page; the Render Evidence Manifest proves input coverage and freshness only.

Any change to source, outline, section, figure, glossary, integrated TeX, Compile Manifest, final PDF, compile report, allowed-artifact manifest, or rendered page invalidates the Seal and Final Evidence Checkpoint. Repairs therefore rerun the required upstream gates, Content Assurance, final compile, rendering, and both Acceptance dimensions as determined by the dependency graph.

Legacy Acceptance Input Set creation uses the same guarded page renderer and page-manifest contract, while its final input binding follows ADR 0051 instead of a Kernel Final Artifact Seal.

## Consequences

Final Acceptance always receives immutable, current, fully rendered evidence. Compile and render failures stay mechanical and occur before Reviewer launch. The workflow gains explicit Final Artifact Seal, Render Evidence Manifest, and `final_evidence_ready` schemas plus end-to-end invalidation tests.

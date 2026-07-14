# Final Acceptance Context

Status: active.

This context owns final-artifact quality standards, reviewer partitions, semantic judgments, and the authoritative Acceptance Report. Video Workflow owns evidence production, Judgment Patch mechanics, compilation, delivery targets, Delivery Guard checks, lifecycle state, and actions taken from that report.

## Acceptance Criteria File

A human-approved, read-only contract that defines the complete set of delivery-blocking quality rules for one Acceptance Scope. Reviewers apply it and have no authority to rewrite it during review.

## Acceptance Criterion

One stable, delivery-blocking quality rule whose evaluation requires a pass or fail judgment supported by final-artifact evidence.

## Acceptance Scope

The explicit boundary of final artifacts and evidence that Acceptance Reviewers may inspect. Generation history, intermediate drafts, and other unlisted process material remain outside the review context.

## Blocking-Only Acceptance Criteria

The policy that every configured Acceptance Criterion can block delivery. Advisory improvements and scoring-only preferences remain outside the Acceptance Criteria File.

## Complete Acceptance Evaluation

The rule that all assigned Acceptance Criteria receive a result even after one failure is found. The complete failure set supports one coherent repair cycle.

## Acceptance Revision Guidance

The evidence-bound repair direction attached to a failed Acceptance Criterion. It states the required outcome while preserving the Reviewer's read-only authority.

## Acceptance Dimension Adapter

A registered semantic review boundary that owns one disjoint partition of Acceptance Criteria and produces a workflow-owned Judgment Patch. Dimension topology remains internal to Final Acceptance.

## Primary Acceptance Dimension

The single Acceptance Dimension Adapter responsible for the complete pass-or-fail evaluation of one Acceptance Criterion. Another dimension may supply a valid Cross-Dimension Finding and gains no pass authority over that criterion.

## Acceptance Dimension Map

The versioned contract that assigns every Acceptance Criterion to exactly one Primary Acceptance Dimension. It defines reviewer responsibility while the Acceptance Criteria File remains the quality-policy authority.

## Acceptance Reviewer

A read-only semantic actor that evaluates assigned criteria within the Acceptance Review Context and writes one workflow-owned Judgment Patch. It cannot modify final artifacts or publish the Acceptance Report.

## Text Acceptance Reviewer

The Acceptance Dimension Adapter that evaluates final-text quality criteria from the allowed text artifacts.

## Visual Acceptance Reviewer

The Acceptance Dimension Adapter that evaluates final rendered-page quality criteria through individual inspection of every in-scope page.

## Cross-Dimension Finding

An evidence-bearing blocking failure observed against an Acceptance Criterion owned by another Primary Acceptance Dimension. It may add a failure and cannot grant or reinforce a pass.

## Acceptance Contract Gap

A potentially blocking final-artifact problem that cannot be mapped to any configured Acceptance Criterion. It prevents report materialization until the quality contract receives an explicit human disposition.

## Acceptance Review Context

The complete and exclusive evidence boundary available to one Acceptance Reviewer. It contains manifest-authorized final artifacts, assigned criteria, permitted control contracts, and rendered pages when the assignment requires them.

## Acceptance Evidence

Artifact-grounded proof that supports one Acceptance Criterion result through a concrete final-artifact location. Process discussion and reviewer self-attestation do not qualify.

## Full Artifact Style Scan

The required review of every in-scope final text artifact for declared style violations and allowed exceptions.

## Scan Evidence

The coverage proof that binds a Full Artifact Style Scan to the exact text artifacts and fingerprints reviewed.

## Full Rendered PDF Visual Scan

The required individual inspection of every in-scope rendered PDF page. Contact sheets, thumbnails, and sampled pages may support navigation and cannot establish acceptance coverage.

## Visual Scan Evidence

The page-specific coverage proof for a Full Rendered PDF Visual Scan, containing one result for every page in the reviewed final PDF.

## Acceptance Execution Context

The transaction boundary for one Final Acceptance review cycle, owning Reviewer task identities, committed Judgment Patches, and report-publication state. It owns no Video Workflow Run lifecycle, delivery state, delivery ownership, historical migration state, or semantic decision.

## Acceptance Report Skeleton

The immutable report shape that binds one review cycle to its criteria partition, artifact fingerprints, and required evidence slots before Reviewers begin.

## Acceptance Materialization Provenance

The script-owned proof that binds an Acceptance Report to its current execution context, report skeleton, committed Judgment Patches, and materialization policy.

## Acceptance Report Freshness

The condition that an Acceptance Report still refers to the exact criteria, final-artifact fingerprints, and rendered evidence it evaluated.

## Acceptance Report

The provider-materialized, machine-readable record of all Acceptance Criterion results, evidence, failures, revision guidance, and unresolved gaps. It is the sole machine-readable final quality decision consumed by Video Workflow.

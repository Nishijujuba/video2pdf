# Project Glossary

## Workflow Kernel 2.0 Activation Status

The accepted target design recorded by ADRs beginning with ADR 0008. Target vocabulary and decisions guide implementation planning but do not change Legacy Track runtime behavior until the applicable Global Gate Cutover or Platform Kernel Cutover atomically activates executable contracts, instructions, providers, guards, and tests.

_Avoid_: design merged means runtime active, mixed-track execution

## Project 2.0 Spec

A human-approved GitHub planning item that defines the problem, solution, user stories, implementation decisions, testing seams, and scope for a Project 2.0 change.

## Implementation Ticket

A human-approved, context-sized vertical slice derived from a Project 2.0 Spec. It carries independently verifiable acceptance criteria and explicit blocking relationships.

## Human Publication Gate

The required human approval checkpoint before an agent publishes or materially changes a Project 2.0 Spec, Implementation Ticket, milestone assignment, containment relationship, or dependency relationship.

## Legacy Planning Archive

The completed Project 1.0 planning record retained as read-only historical evidence. It may inform new work and does not receive new planning items or status changes.

## Feature Issue Set

A batch of implementation issues that belong to one feature-level planning unit. In this repo, a Feature Issue Set is represented by one `docs/issues/<feature-slug>/` directory, and its issue files normally come from the same PRD.

The Feature Issue Set boundary is used for execution planning and dependency visualization. PRDs, ADRs, status tags, and general documentation links may provide context, while execution dependency order is read from issue metadata such as `depends_on` and `blocks`.

## Issue Dependency Edge

An execution-order relationship between two issue files in the same Feature Issue Set. The authoritative direction is read from the downstream issue's `depends_on` metadata: if issue B depends on issue A, then A must be ready before B can be completed.

The `blocks` metadata is a redundant reverse index for human reading and graph browsing. It should match the inverse of `depends_on`, and dependency-view generators should treat mismatches as tracker consistency errors.

## Issue Dependency View

A generated view that makes the execution order of one Feature Issue Set readable without the noise of general documentation links. Its primary artifact is a Markdown file containing a Mermaid graph, because that form is reviewable in Git and easy to regenerate.

An Obsidian Canvas version may be generated as a secondary browsing artifact when spatial layout helps the user inspect the batch. The Canvas view must not become a separate source of truth for dependency order.

The first dependency-view generation scope is the Markdown Mermaid view plus the Issue Dependency Index. Canvas output is an optional later extension.

## Issue Dependency Index

A generated Obsidian entry point that summarizes all Feature Issue Sets and links to each Issue Dependency View. It helps the user see which batches exist, which batches are complete, and which batches still have executable or blocked work.

The index should show each batch's issue count, status distribution, root issues, currently executable issues, and blocked issues.

## Issue Dependency View Generator

A repository script that reads issue metadata and produces Issue Dependency Views plus the Issue Dependency Index. It has a generation mode that writes the generated Markdown files and a validation mode that checks tracker consistency without modifying files.

Validation should catch dependency metadata errors before a user trusts the Obsidian view. Required checks include missing issue links, `depends_on` and `blocks` inverse mismatches, circular dependencies, and generated views that are stale compared with current issue metadata.

By default, the generator processes every Feature Issue Set under `docs/issues/<feature-slug>/` so the Issue Dependency Index remains complete. It should also support a single-feature option for refreshing or validating one Feature Issue Set during active editing.

Generated dependency-view files should record their source metadata in the file header. Required freshness metadata includes generation time, source feature slug, source issue count, and a source issue fingerprint derived from the issue metadata used to build the view.

## Issue Dependency View Freshness

The condition that a generated Issue Dependency View still matches the current issue metadata. A view is fresh when its recorded source issue fingerprint equals the fingerprint recomputed from the current issue files.

Freshness checking is needed because generated Markdown and Mermaid files are static artifacts. When issue frontmatter or dependency metadata changes, the generated view can become stale until the Issue Dependency View Generator is run again.

## Issue Dependency Source Fingerprint

A deterministic digest of the issue metadata used to generate dependency views. The fingerprint covers only metadata that changes dependency-view output: issue relative path, title, `status`, `feature`, `depends_on`, `blocks`, and `related_adrs`.

Issue body prose, execution logs, comments, and unrelated content are outside the fingerprint because they do not change dependency order, status color, or dependency index summaries.

## Issue Dependency Consistency Error

A validation finding that means issue dependency metadata or a generated dependency view cannot be trusted as an execution guide. Examples include a missing issue link, `depends_on` and `blocks` inverse mismatch, circular dependency, unknown status, or stale generated view.

Generated views may display consistency errors so a human can locate the problem. The Issue Dependency View Generator validation mode must fail when consistency errors exist.

## Currently Executable Issue

An issue that is ready to be picked up according to both status and dependencies. In this repo, an issue is currently executable when its status is `ready-for-agent` or `ready-for-human`, and every issue listed in its `depends_on` metadata has status `done`.

Issues with status `in-progress`, `blocked`, `in-review`, `done`, or `wontfix` are outside the currently executable set. They may still appear in dependency views so the user can understand the full batch state.

## Next Executable Issue List

A generated section in each single-batch Issue Dependency View that lists the currently executable issues before the Mermaid graph. It is sorted by issue number and uses the Currently Executable Issue rule.

The list is an execution shortcut for agents and humans. The Mermaid graph explains structure, while the list identifies the next issue files that can be picked up immediately.

## Waiting On Dependencies List

A generated section in each single-batch Issue Dependency View that lists Dependency-Blocked Issues and the upstream issues they are waiting for. It is sorted by issue number.

The list makes pending work explainable without requiring the user to infer dependency-blocked state from the graph manually.

## Status-Blocked Issue

An issue whose own frontmatter has `status: blocked`. This means work has started or been evaluated and cannot continue because of a recorded blocker, such as a missing decision, failed verification, unavailable tool, or unresolved dependency.

## Dependency-Blocked Issue

An issue whose own status is otherwise executable, such as `ready-for-agent` or `ready-for-human`, while at least one issue listed in its `depends_on` metadata is not yet `done`.

Dependency-blocked issues should appear separately from Status-Blocked Issues in dependency indexes because their next action is to complete upstream work.

## Issue Dependency Layer

A visual ordering group in an Issue Dependency View. Layer 0 contains root issues with no `depends_on` entries inside the same Feature Issue Set. Later layers contain issues whose same-set dependencies appear in earlier layers.

Single-batch Mermaid dependency graphs should use left-to-right layout by dependency layer and keep the original issue number in each node label. The layer communicates execution order, while the number preserves the issue's stable identity from the original split.

## Issue Node Status Color

The visual color assigned to an issue node in an Issue Dependency View. The color represents the issue's current execution status from frontmatter `status`, such as `done`, `ready-for-agent`, `ready-for-human`, `in-progress`, `blocked`, `in-review`, or `wontfix`.

Node color has one meaning: current issue status. Dependency relationships are expressed by graph edges and dependency layers, while dependency-blocked state is reported in the Issue Dependency Index or a compact node annotation.

Because the Markdown Mermaid view is a generated artifact, status color is current at generation time. A stale generated view should be detected by the Issue Dependency View Generator validation mode.

## Issue Status Color Palette

The fixed status-to-color mapping for issue nodes in generated dependency views. The palette is:

- `done`: green
- `ready-for-agent`: blue
- `ready-for-human`: purple
- `in-progress`: yellow
- `blocked`: red
- `in-review`: orange
- `needs-info`: gray
- `needs-triage`: light gray
- `wontfix`: dark gray

The palette should remain stable across generated Mermaid and Canvas views so the user can read execution state without relearning colors per batch.

## Pyramid Principle Validation Skill

A review skill that evaluates whether a generated PDF note, document, or long-form writing artifact follows the Pyramid Principle. It focuses on reasoning structure: conclusion first, grouped support, clear hierarchy, and coherent progression from top-level claim to supporting sections.

Canonical local skill name: `pyramid-principle-validate`.

## Teaching-PDF Pyramid Standard

The adapted Pyramid Principle standard for video-derived teaching PDFs. It keeps the core structure of top-down claims and grouped support while allowing teaching-oriented flow, such as motivation, mechanism, example, formula, figure, and section summary. It checks whether the document helps the reader learn without forcing every source into a rigid business-report pattern.

## Pyramid Principle Text Standard

The general-purpose Pyramid Principle standard for written text. It checks whether a text presents its central claim early, organizes supporting ideas into a clear hierarchy, keeps same-level groups meaningfully distinct, progresses coherently for the intended reader, and makes each title or heading match the body below it. Video-to-PDF workflows apply this standard with a teaching-document context; the general standard itself is not tied to PDFs, videos, outlines, sections, or any specific workflow stage.

## Pyramid Gate

A required quality gate in the video-to-PDF workflow. The gate checks whether an artifact has enough pyramid structure to continue to the next workflow stage. A gate is expected to produce explicit evidence rather than a casual chat judgment.

A failing Pyramid Gate stops the workflow at that checkpoint. Continuing after failure requires an explicit user instruction, normally recorded as a waiver with the user's approval and reason.

## Pyramid Checkpoint

A workflow moment where the Pyramid Gate runs. The agreed checkpoints are the outline contract, each section draft, and the integrated main document before PDF compilation.

## Semantic Review

A judgment-based review that reads the meaning of claims, section titles, explanations, and supporting evidence. It is needed because pyramid structure depends on whether lower-level content truly supports higher-level claims.

## Gate Report

A machine-readable report that records the Pyramid Gate result. The report lets the workflow decide whether to continue, revise, stop, or continue under an explicit waiver.

Gate status is derived from a five-part score: top-down clarity, support hierarchy, MECE grouping, teaching progression, and title-body alignment. A score of `0.80` or higher passes, `0.60` to below `0.80` requires revision, and below `0.60` blocks the workflow. Severe structural failures can force `needs_revision` or `blocked` even when the numeric score is higher.

The general-purpose Gate Report identifies the reviewed content with an `artifact_type`, such as outline contract, TeX section, integrated TeX document, Markdown, plain text, or other text artifact, and a caller-provided `context_label`, such as `outline`, `section_01`, or `main`. Workflow-specific stage names are owned by the calling workflow rather than by the general Pyramid Principle Validation Skill.

A Gate Report also carries audit metadata that ties the judgment to the exact reviewed input and evaluation context. This includes the applicable standard, evaluation backend, prompt version, input fingerprint, input size, large-input approval state, and generation time, so a report can be recognized as stale when the reviewed text changes.

## Acceptance Criteria File

A user-authored, read-only JSON artifact that defines the acceptance standard for a final artifact or workflow checkpoint. The reviewer uses it as the acceptance ruler and must not rewrite it during review.

## Acceptance Standards Directory

The project documentation directory for acceptance criteria templates and related acceptance contract examples: `docs/acceptance/`.

## Delivery Glossary

A per-video JSON contract artifact for non-English teaching PDFs. It records core English expressions that carry explanatory work, their Chinese primary names, boundary explanations, body display strategies, and source-English preservation locations.

The Delivery Glossary is workflow evidence for coordinators, Consistency agents, and Acceptance Reviewers. It is not a reader-facing appendix by default; a visible glossary or concept index is produced only when the user or task explicitly requests one.

## Core English Expression

An English expression from the source video that carries explanatory work in the PDF. It belongs in the Delivery Glossary when the reader would lose the main argument without a stable definition, such as a method name, framework label, recurring concept, or an ordinary English word temporarily promoted into a document concept.

Product names, company names, person names, code identifiers, commands, file extensions, and familiar abbreviations stay outside the Delivery Glossary unless they define a new core concept.

## Body Display Strategy

The Delivery Glossary field that tells writers and reviewers how a core English expression may appear in reader-facing body text.

The v1 values are `preserve_english`, `chinese_with_english_parenthetical`, `chinese_primary_only`, and `quote_only`. This strategy decides whether body prose should keep the English term, use Chinese with an English parenthetical, use Chinese only, or reserve the English expression for direct quotes.

## Source English Preservation Location

The Delivery Glossary field that states where the original English expression remains recoverable for source alignment.

The v1 values are `body_parenthetical`, `body_after_definition`, `footnote`, `caption`, `quote_only`, `delivery_glossary_only`, and `none`. This location complements Body Display Strategy: one field controls body rendering, while the other controls source-term recoverability.

## New Term Candidate

A proposed Core English Expression found after the initial outline contract. Writer agents may report New Term Candidates in handoff notes with a proposed Chinese primary name, boundary explanation, body display strategy, and preservation location.

The coordinator accepts or rejects New Term Candidates before consistency review. Accepted candidates enter the Delivery Glossary; rejected candidates stay outside the terminology contract and should not become body-text concepts.

## Acceptance Criteria Schema

The minimal JSON contract for an Acceptance Criteria File. It identifies the schema version, criteria name, acceptance scope, review context policy, evaluation policy, and the blocking criteria list.

The current criteria category set is limited to `style`, `logic_readability`, `formula_information_gain`, `figure_visual_integrity`, `table_layout_integrity`, and `credibility_disclosure_placement`.

## Acceptance Criterion

A single delivery-blocking rule inside the criteria list of an Acceptance Criteria File. For style review, it carries a stable id, category, rule text, violation patterns, allowed exceptions, scan policy, pass condition, and fail condition.

## Acceptance Scope

The explicit artifact boundary declared by an Acceptance Criteria File. It lists which final delivered artifacts Acceptance Dimension Adapters may inspect and which artifacts are excluded from acceptance review.

## Acceptance Report

A Materialized Gate Report generated by the Final Acceptance Module from validated acceptance Judgment Patches. It records the pass/fail decision, evidence, and unresolved gaps separately from the criteria being applied and remains the only machine-readable delivery decision source.

## Acceptance Report Schema

The version-2 JSON contract for an Acceptance Report. It records the criteria file, overall status, decision source, dimension-specific review contexts, artifact fingerprints, criterion results, visual scan evidence, failed criteria, revision requirement, and Acceptance Materialization Provenance. Acceptance Report v1 is unsupported; this retirement does not change Acceptance Criteria File v1.

## Acceptance Materialization Provenance

The script-owned proof inside an Acceptance Report v2 that binds its provider and aggregation-policy versions to the current Acceptance Execution Context, Contract Skeleton, Text and Visual Task Envelopes, Generated Task Prompts, committed Task Attempts, Judgment Patch generations, and report-publication Mutation Intent used to derive the decision.

_Avoid_: Judgment Patch as decision source, reviewer self-attestation

## Acceptance Report Skeleton

A fail-closed template for an Acceptance Report. It binds both acceptance dimensions to one fixed report shape, current artifact fingerprints, criteria partition, and rendered-page coverage slots before judgment begins. Reviewers submit bounded Judgment Patches while the provider owns final report materialization.

## Acceptance Execution Context

The script-created transaction boundary for one Final Acceptance review cycle. Its schema-valid record under `review/acceptance/executions/<execution-id>/execution.json` binds either a Kernel Final Evidence Checkpoint or one Legacy Acceptance Input Set, owns the two Reviewer task identities, committed Judgment Patch generations, and report-materialization state, and acts as the module-local commit marker. It owns no Video Workflow Run lifecycle or delivery decision.

_Avoid_: synthesized Legacy Run Record, Acceptance Report

## Acceptance Execution Promotion Slot

The exclusive right to publish one Reviewer Patch or provider report mutation against the latest revision of an Acceptance Execution Context. Text and Visual review work may run concurrently, while their patch commits and final report publication are serialized through the context record.

_Avoid_: Run Promotion Slot, Reviewer execution lock

## Acceptance Decision Source

The Acceptance Report JSON is the only machine-readable source of truth for whether an artifact passes acceptance. Human-readable Markdown summaries may explain the result, but they cannot override the JSON decision.

## Acceptance Must Gate

A mandatory acceptance criterion whose failure blocks delivery regardless of any aggregate score. Aggregate scores may summarize quality, but they cannot override a failed must gate.

## Blocking-Only Acceptance Criteria

The rule that an Acceptance Criteria File contains only delivery-blocking criteria. Advisory, nice-to-have, scoring-only, or non-blocking improvement checks are outside this acceptance flow.

Every entry in the criteria list is implicitly delivery-blocking, so the Acceptance Criteria File does not need a severity field.

## Complete Acceptance Evaluation

The rule that all registered Acceptance Dimension Adapters must collectively evaluate every criterion in the Acceptance Criteria File even after one dimension finds a failure. The Acceptance Report lists all failed criteria so the artifact can be revised in one pass.

## Acceptance Revision Guidance

Required repair direction recorded for each failed criterion in an Acceptance Report. It tells the repair coordinator what must change and what fix types are allowed, while preserving every Acceptance Reviewer Adapter's read-only role.

## Repair Planning Module

The deterministic Module that converts a complete current semantic failure set from Content Assurance or Acceptance Report v2 into a conflict-aware Repair Plan. It maps registered repair capabilities to evidence-bearing failures, computes candidate write sets, and combines overlapping work before any Repair Agent starts.

_Avoid_: LLM repair planner, repair agent prompt

## Repair Plan

The schema-validated contract for one semantic repair cycle. It binds failed criteria and evidence to ordered or parallel repair tasks, their capabilities, exact read and write sets, dependencies, and the final integration compile requirement.

_Avoid_: acceptance report, informal fix list

## Repair Capability

A registered kind of artifact change that a Repair Agent may perform. The initial set is `text_repair`, `figure_repair`, `layout_repair`, and `integration_repair`; each capability has deterministic input, output, and write-set rules.

_Avoid_: reviewer category, arbitrary agent role

## Integration Repair

A single repair task that owns changes whose required write sets overlap or whose safe file ownership cannot be determined mechanically. It replaces competing writers for the same canonical artifact.

_Avoid_: final consistency review, merge conflict cleanup

## Content Assurance Module

The deep Module that prepares and materializes two independent draft-level semantic reviews against one integrated artifact generation: Consistency and Source-Faithfulness. It exposes a small orchestration Interface while preserving each review report's separate authority.

_Avoid_: Final Acceptance Module, Main Pyramid Gate

## Consistency Reviewer

The read-only Content Assurance Adapter that checks terminology, symbols, Delivery Glossary compliance, cross-references, transitions, duplicate definitions, figure-slot consistency, and chapter-to-chapter coherence across the integrated draft.

_Avoid_: proofreader, Source-Faithfulness Reviewer

## Source-Faithfulness Reviewer

The read-only Content Assurance Adapter formerly described generically as Independent Review. It compares the integrated draft and figure provenance against the Validated Source Package to detect omitted important details, unsupported additions, subtle source errors, and evidence gaps.

_Avoid_: Acceptance Reviewer, Consistency Reviewer

## Content Assurance Checkpoint

The computed `content_assurance_ready` state that requires fresh passing Consistency and Source-Faithfulness reports bound to the same integrated draft generation.

_Avoid_: combined semantic report, final delivery acceptance

## Content Assurance Failure Set

A script-materialized repair input that normalizes every failed Consistency and Source-Faithfulness finding bound to one integrated draft generation. It preserves both reports as decision authorities and supplies deterministic repair routing without becoming a combined pass/fail report.

_Avoid_: Acceptance Report, third semantic reviewer

## Final Artifact Seal

The immutable binding of the current integrated `main.tex`, Compile Manifest, and every declared final compile input after `content_assurance_ready`. It is invalidated by any later source, section, figure, terminology, integration, or compile-input change.

_Avoid_: draft compile, delivery decision

## Render Evidence Manifest

The script-generated contract that binds one final PDF generation to its page count and exact `page_1..page_count` PNG fingerprints. It proves freshness and coverage input for Visual Acceptance while semantic inspection remains the Reviewer's responsibility.

_Avoid_: contact sheet, sampled pages

## Final Evidence Checkpoint

The computed `final_evidence_ready` state reached only after a Final Artifact Seal, guarded Final Compile, final Compile Report, final PDF, allowed-artifact manifest, and complete Render Evidence Manifest are current. `acceptance-prepare` requires this checkpoint for Kernel Track input.

_Avoid_: content_assurance_ready, accepted delivery

## Style Acceptance Criterion

An acceptance criterion that checks the final artifact's writing style, structure, tone, redundancy, reader experience, or forbidden content patterns. Source-faithfulness evidence is outside its scope. Its pass or fail judgment must still cite concrete locations in the final artifact.

## Figure Visual Integrity Criterion

A delivery-blocking acceptance criterion that checks whether final figures, diagrams, charts, and visual explanations are readable, aligned, complete, and professionally laid out. It treats text overflow, text-arrow collisions, broken alignment, confusing callout relationships, mismatched font and container sizes, and draft-like visual polish as acceptance failures.

## Table Layout Integrity Criterion

A delivery-blocking acceptance criterion that checks whether final tables are readable, contained within page boundaries, and professionally laid out. It treats clipped columns, text running past the page edge, broken wrapping, unreadable density, ambiguous table structure, and caption-table mismatch as acceptance failures.

## Credibility Disclosure Placement Criterion

A delivery-blocking acceptance criterion that checks whether credibility caveats, such as ASR noise, OCR uncertainty, source limitations, or reviewer methodology notes, are placed without disrupting the main reading flow. Such caveats may appear in footnotes, captions, appendices, or source notes when needed, but should not interrupt the body as meta-process exposition.

## Final Delivery Quality Criterion

A delivery-blocking acceptance criterion that protects the final PDF's readability, professional finish, and credibility. It covers writing style, visual integrity, table layout, and credibility disclosure placement when defects would make the delivered PDF feel unreliable or unfinished.

## Rendered PDF Visual Review

The required review mode for visual and final-delivery quality acceptance criteria. The Visual Acceptance Reviewer must inspect rendered PDF pages before passing figure visual integrity, table layout integrity, or credibility disclosure placement criteria.

## Full Rendered PDF Visual Scan

The required visual scan policy for final-delivery quality acceptance. An independent Visual Acceptance Reviewer must inspect every rendered PDF page with Codex visual capability and report all detected delivery-blocking visual failures without using page sampling or a reduced visual evidence set.

## Visual Scan Evidence

Coverage proof recorded in an Acceptance Report for a Full Rendered PDF Visual Scan. It lists the reviewed PDF, total page count, and one result entry for every rendered page so the page coverage can be verified.

## Style Violation Pattern

A concrete textual pattern that tells the Text Acceptance Reviewer what a Style Acceptance Criterion treats as a violation. Style criteria also define allowed exceptions so the reviewer can distinguish genuine style failures from valid topic-driven usage.

## Full Artifact Style Scan

The required scan policy for a must-level Style Acceptance Criterion. The Text Acceptance Reviewer must inspect the full final text artifact for the declared style violation patterns before reporting a pass.

## Scan Evidence

Coverage proof recorded in an Acceptance Report for a Full Artifact Style Scan. It identifies the scanned final artifacts, scan range, and artifact fingerprint so a pass decision can be recognized as stale after the artifact changes.

## Acceptance Report Freshness

The rule that an Acceptance Report is valid only for the exact artifact fingerprints it reviewed. If any in-scope final artifact changes after review, the old report is stale and the artifact must be accepted again.

## Acceptance Evidence

Artifact-grounded proof used by an Acceptance Report to justify a pass or fail decision. For must gates, evidence must point to concrete artifact locations, such as files, pages, sections, timestamps, images, or source snippets, so the judgment can be independently checked.

## Acceptance Review Context

The only content evidence an acceptance reviewer is allowed to use: its manifest-authorized final delivered artifacts, assigned criteria, and rendered pages when applicable. Its immutable task envelope, Contract Skeleton, and Judgment Patch schema are permitted control artifacts and cannot embed generation-process content. Chat history, writer notes, repair discussion, and intermediate drafts remain outside the review context.

## Acceptance Reviewer

A read-only semantic Adapter that evaluates its assigned final-delivery criteria and writes one bounded Judgment Patch. It cannot materialize the Acceptance Report or modify final artifacts, source materials, criteria files, generation records, or intermediate drafts.

## Final Acceptance Module

The deep Module behind the final-acceptance Seam. Its external Interface prepares one review cycle and materializes its result, while its implementation hides criteria partitioning, dual task preparation, patch validation, complete-coverage checks, deterministic aggregation, repair routing, freshness, and final report publication.

_Avoid_: Acceptance Reviewer, Delivery Guard

## Acceptance Dimension Adapter

A registered semantic Adapter that owns one disjoint acceptance-criteria partition and produces a Judgment Patch bound to the shared Acceptance Report Skeleton. The first implementation registers Text and Visual dimensions while keeping future dimensions internal to the same Interface.

_Avoid_: repair role, criteria category alone

## Primary Acceptance Dimension

The single Acceptance Dimension Adapter required to provide the complete pass-or-fail evaluation for one configured Acceptance Criterion. Primary assignments are disjoint and collectively cover the criteria file.

_Avoid_: exclusive observer, only possible source of failure evidence

## Acceptance Dimension Map

The versioned script-consumed contract that assigns every configured acceptance criterion or criterion category to exactly one Primary Acceptance Dimension. Its path, version, and SHA-256 are bound into the Acceptance Report Skeleton alongside the unchanged Acceptance Criteria File.

_Avoid_: prompt-only role split, edit to criteria v1

## Acceptance Criterion Reference Index

A read-only control artifact listing every configured `criterion_id`, category, and Primary Acceptance Dimension without exposing the other dimension's full rule text. Both Reviewers receive it so a Cross-Dimension Finding can cite a valid criterion while normal pass authority remains partitioned.

_Avoid_: second criteria file, cross-dimension pass assignment

## Cross-Dimension Finding

A delivery-blocking failure observation submitted by an Acceptance Dimension Adapter against a criterion owned by another Primary Acceptance Dimension. It must cite the configured criterion and concrete final-artifact evidence, and it can add a failure without granting a pass.

_Avoid_: duplicate primary evaluation, advisory note

## Acceptance Contract Gap

A potentially delivery-blocking final-artifact problem that an Acceptance Reviewer cannot map to any configured Acceptance Criterion. It blocks report materialization and produces a criteria-gap brief without consuming the semantic repair budget.

_Avoid_: failed configured criterion, freeform new criterion

## Text Acceptance Reviewer

The Acceptance Dimension Adapter responsible for full final-text review of `style`, `logic_readability`, and `formula_information_gain` criteria. It does not consume rendered-page images merely to reproduce visual review.

_Avoid_: Independent Review Agent, Visual Acceptance Reviewer

## Visual Acceptance Reviewer

The Acceptance Dimension Adapter responsible for `figure_visual_integrity`, `table_layout_integrity`, and `credibility_disclosure_placement`, including an individual inspection record for every rendered PDF page.

_Avoid_: contact-sheet reviewer, sampled-page reviewer

## Acceptance Orchestration Failure

A missing, stale, malformed, unauthorized, timed-out, or otherwise incomplete acceptance Task Attempt that prevents report materialization. It blocks delivery without consuming one of the three semantic repair attempts because no complete acceptance decision exists.

_Avoid_: failed Acceptance Criterion, repair attempt

## Session-Scoped Delivery Target

The active delivery target owned by one Codex session. It lets the Stop hook guard the PDF delivery flow for the current session without reading or blocking delivery targets owned by other concurrent sessions.

## Delivery Task Index

A project-level index of video delivery tasks. It supports task recovery, ownership checks, and workflow observability, but it is not the Stop hook's delivery-blocking source.

## Delivery Target Ownership

The relationship between one Video Output Directory and the Codex session currently allowed to advance its generation, acceptance, repair, and final delivery workflow.

## Delivery Target Handoff

An explicit transfer of Delivery Target Ownership from one Codex session to another. It preserves the previous owner relationship so interrupted or superseded delivery workflows remain auditable.

## Delivery Lifecycle Mutation Intent

A Cross-Run Control Store Saga record that coordinates one Kernel Track delivery-stage change across `workflow/run.json`, the video delivery target, session target, Delivery Task Index, and any target-archive document. It uses the Run revision plus Delivery Target Ownership generation as its lifecycle compare-and-set. A producer Claim Fencing Token is present only when a task produced evidence for the transition. Every mutation target, including an expected-absent archive path, is declared before preparation. The Run Record is the commit marker; every other file is a validated projection tied to the same intent and run revision.

_Avoid_: independent stage writer, cross-filesystem atomic transaction

## Projection Publication Slot

A durable, exclusive Cross-Run Control Store reservation keyed by a normalized canonical projection or archive-target path. One lifecycle intent acquires every required path slot in sorted order, including paths expected to be absent, rereads the latest projection revisions, and retains the slots until commit or reconciliation, preventing concurrent whole-file replacements from losing another Run's update.

_Avoid_: process-local file lock, Run Promotion Slot

## Control Store Unavailable

The global fail-closed state entered when the Cross-Run Control Store is missing, corrupt, schema-incompatible, or fails its locking and integrity checks. New initialization, claim, admission, promotion, delivery transition, and Kernel cutover operations remain blocked until evidence-bearing restore and reconciliation complete.

_Avoid_: empty database fallback, partial run continuation

## Pyramid Review Directory

The persistent evidence directory for Pyramid Gate outputs inside each video output folder: `review/pyramid/`. It stores stage reports such as `outline.pyramid.json`, `section_01.pyramid.json`, `main.pyramid.json`, and a human-readable `summary.md`. Disposable drafts and temporary attempts belong under `待删除`.

## LaTeX Compile Guard

A required workflow guard that ensures video-to-PDF LaTeX compilation happens through a controlled compile path with bounded runtime, bounded output location, and persistent provenance evidence.

The LaTeX Compile Guard protects the workflow before final delivery. It does not judge PDF quality, replace Final Delivery Acceptance, or grant delivery approval by itself.

## LaTeX Compile Report

A machine-readable provenance report that records how a LaTeX compile ran, which TeX source it compiled, where logs and disposable build outputs were stored, and which fingerprints bind the report to current artifacts.

For a final report, compile provenance also identifies the produced final PDF and the controlled wrapper that produced the report. Wrapper provenance includes the producer identity, producer contract, producer mode, wrapper script identity/fingerprint, and semantic invocation arguments. These fields let the Final Delivery Guard reject a report that only matches current artifact hashes without proving the guarded wrapper path.

A final LaTeX Compile Report can be used by the Final Delivery Guard as mechanical evidence that the delivered PDF came from the controlled compile path. A temporary compile report is diagnostic evidence only and cannot satisfy final delivery.

The report is workflow provenance. It is not a cryptographic signature or tamper-proof attestation.

## Temporary Compile

A diagnostic LaTeX compile used to inspect errors, layout, or intermediate PDF output during generation. Temporary compile outputs belong under the Video Output Directory's `待删除` area and are outside final delivery evidence.

## Final Compile

The LaTeX compile that produces the PDF intended for delivery. A Final Compile must produce a final LaTeX Compile Report and bind that report to the current main TeX source, final PDF artifact, and wrapper provenance.

## Video Output Directory

The durable folder that contains all produced artifacts for one video task, including source metadata, subtitles, TeX files, figures, reviews, Pyramid Gate evidence, and the final PDF. Disposable intermediate files remain inside that folder's `待删除` subfolder.

## Video Workflow Kernel

The authoritative lifecycle boundary shared by single-video Bilibili and YouTube PDF tasks. It owns deterministic run identity, artifact bindings, checkpoint transitions, and fail-closed recovery while leaving content judgment to semantic reviewers and writers.

_Avoid_: workflow helper, orchestration prompt

## Workflow Kernel Package

The project-root Python package `src/video2pdf_workflow_kernel/` that implements the Video Workflow Kernel, its platform adapter interface, and its deterministic contracts. It is shared by every video skill and is not owned by any one platform or gate.

_Avoid_: skill-local helper script, delivery-guard extension

## Workflow CLI

The thin stable launcher `scripts/video_workflow.py` used by skills, coordinators, hooks, and tests to call the Workflow Kernel Package. It resolves the repository and `src` paths from its own location and does not depend on the caller's current directory or an editable package installation.

_Avoid_: semantic skill prompt, platform downloader

## Gate Provider

An independently authoritative validator or generator, such as Pyramid, compile, or acceptance, that the kernel invokes through a declared executable and evidence contract. Its domain decision and report schema remain owned by that gate.

_Avoid_: kernel checkpoint implementation, platform adapter

## Role Prompt Template

A versioned semantic instruction template for one workflow-agent role. It defines the judgment or content work that requires language understanding and contains no run-specific paths, artifact fingerprints, report structures, or state transitions.

_Avoid_: Task Envelope, workflow procedure copy

## Platform Prompt Overlay

A versioned semantic policy fragment that adapts a Role Prompt Template for Bilibili or YouTube source characteristics without reimplementing platform download mechanics or kernel workflow rules.

_Avoid_: Video Platform Adapter, duplicated platform skill

## Generated Task Prompt

The immutable prompt produced by `task-prepare` from a Role Prompt Template, an applicable Platform Prompt Overlay, and a reference to one Subagent Task Envelope. Its template identities and SHA-256 values are bound into the task evidence.

_Avoid_: handwritten launch prompt, Task Envelope contents

## Content Production Module

The deep Module that plans and advances the platform-neutral production DAG from a Validated Source Package through outline, section writing, figure generation, Pyramid gates, integration, and compile readiness. Its coordinator-facing Interface is `production-plan` plus `production-advance`.

_Avoid_: platform skill procedure, monolithic writer agent

## Figure Slot

A stable outline- or workflow-issued identity for one intended figure placement and teaching purpose. Writer and Figure Agents refer to the same slot without writing the same TeX artifact.

_Avoid_: image filename, agent-created placeholder name

## Figure Manifest

The fixed contract delivered by a Figure Agent for assigned Figure Slots. It binds generated or selected assets, source timestamps and provenance, captions, TeX snippets, and expected placement without granting write access to canonical section files.

_Avoid_: section draft, loose image folder

## Required Figure Wave

The first section-scoped Figure task set declared by the accepted outline. It resolves required Figure Slots concurrently with Writer work and is part of the section's planned teaching contract.

_Avoid_: all possible figures, post-review repair

## New Figure Candidate

A bounded semantic request proposed by a Writer Agent when the drafted explanation reveals a visual need absent from the accepted outline. It carries teaching purpose, placement, source evidence, figure type, insufficiency of prose, and priority without creating a Figure Slot itself.

_Avoid_: figure task, agent-created slot

## Incremental Figure Wave

The optional second and final production-time Figure task set for one section. It is created by the kernel from admitted New Figure Candidates after that section's Writer task completes.

_Avoid_: recursive figure loop, acceptance repair

## Integration Manifest

The script-generated inventory that binds accepted section drafts, Figure Manifests, slot resolution, terminology evidence, and artifact generations into one exact integration input set.

_Avoid_: Compile Manifest, main TeX file

## Compile Manifest

The exact allowlist of committed TeX, figure, font, bibliography, and support artifacts copied into one guarded compile attempt. It replaces recursive workspace copying as the compiler's input contract.

## Compile Dependency Closure

The proven set of project inputs actually consumed by a guarded LaTeX compile. Closure holds only when recorder evidence maps every project-local input to one fingerprinted Compile Manifest entry and every remaining input to a registered compile-runtime dependency.

_Avoid_: directory scan, static TeX parse alone

## Compile Dependency Gap

A machine-readable blocking result produced when staging, static preflight, recorder evidence, or compilation finds a missing, undeclared, escaped, dynamically generated, or unsupported input. It identifies the reference and required producer action without automatically expanding the Compile Manifest.

_Avoid_: automatic recursive copy, ignored missing file

_Avoid_: allowed acceptance manifest, directory scan

## Bootstrap Probe

The short-path metadata probe that identifies the source platform item and freezes the Video Workflow Run start time before the final Video Output Directory can be named. Its diagnostic evidence is retained for audit and moves into the initialized run's disposable area.

_Avoid_: source acquisition, provisional video output directory

## Run Initialization

The deterministic transition that converts a successful Bootstrap Probe into a named Video Output Directory and its initial Video Workflow Run Record. It establishes the durable run boundary before full source acquisition begins.

_Avoid_: directory creation, metadata probe

## Video Output Scaffold

The versioned standard directory structure created by Run Initialization for every new Video Workflow Run. Its canonical artifact zones are workflow coordination, source material, final figures, semantic work, durable review evidence, and disposable evidence.

_Avoid_: folder convention, example layout

## Scaffold Generator

The deterministic Video Workflow Kernel operation that creates and validates every allowed directory in a Video Output Scaffold. Workflow agents consume generated locations and cannot establish new directory conventions themselves.

_Avoid_: agent setup step, mkdir instruction

## Section Scaffold

The deterministic set of section-scoped figure and work directories generated after an accepted outline declares the canonical section identifiers. It gives Writer and Figure Agents isolated locations that share the same section identity.

_Avoid_: agent-named chapter folder

## Subagent Task Envelope

The script-issued, immutable execution contract for one subagent assignment. It binds a unique task identity and Task Authority Binding to a role, dependency evidence, exact read and write paths, required outputs, schemas or skeletons, and the semantic fields the agent may supply. Most tasks bind to one Video Workflow Run; Final Acceptance tasks bind to one Acceptance Execution Context so Legacy review does not require a synthetic Run Record.

_Avoid_: prompt-only handoff, role description

## Task Authority Binding

The discriminated contract that identifies the state authority allowed to validate and commit one Subagent Task. `kernel_run` binds a task to a `run_id`, expected Run revision, and applicable Workflow Checkpoint. `acceptance_execution` binds a Final Acceptance task to an execution id, expected context revision, and immutable Kernel or Legacy input fingerprint.

_Avoid_: optional run id, inferred task owner

## Task Completion Gate

The deterministic operation that validates a subagent's declared outputs against its Subagent Task Envelope, including its Task Authority Binding, path boundaries, format contracts, dependency freshness, and output fingerprints. Passing creates `validated_waiting_for_promotion`; it does not commit an artifact or advance a Workflow Checkpoint by itself.

_Avoid_: agent self-declared completion, checkpoint reviewer

## Task Claim

The durable, exclusive ownership record for one active Subagent Task Envelope and its declared write set within one Task Authority Binding. A claim identifies the coordinator session, worker, task attempt, authority kind and identity, and expected prior state. After output validation it remains active through `validated_waiting_for_promotion` and ends only after that Attempt's committed promotion, explicit failure, cancellation, handoff, or reclaim.

_Avoid_: time-based lock, agent status message

## Claim Fencing Token

The monotonic `claim_generation` attached to every Task Claim and required by completion, promotion, release, and recovery mutations. Reclaim advances the generation so a late worker holding an older value cannot change canonical state.

_Avoid_: proof that an old process stopped, resource lease

## Unknown Resource Lease

A Resource Lease whose worker termination or continuation cannot be proven after coordinator recovery. It keeps consuming its declared capacity until evidence-bearing `resource-resolve` establishes a terminal outcome.

_Avoid_: expired timeout, automatically reusable capacity

## Resource Admission Module

The deep Module that admits runnable tasks only when all declared local resource classes have capacity. It owns fixed configurable quotas, atomic multi-resource acquisition, queue state, fault-domain circuit breakers, and crash reconciliation without taking over Task Claim authority.

_Avoid_: one global concurrency flag, adaptive scheduler

## Resource Class

A named constrained execution capacity declared by a Subagent Task Envelope or provider task, such as platform download, Whisper transcription, Codex semantic work, LaTeX compilation, PDF rendering, or visual acceptance.

_Avoid_: agent role, workflow phase

## Resource Admission Configuration

The versioned project contract that defines fixed concurrency quotas for every Resource Class. Its identity and SHA-256 are recorded by admitted Task Attempts, and configuration changes affect only later admissions.

_Avoid_: command-line concurrency override, runtime autoscaling

## Overcommitted Resource Class

A Resource Class whose current `starting`, `active`, and `unknown` Lease usage exceeds a newly activated lower quota. Existing leases continue, new admissions cannot increase the excess, and the class returns to normal automatically as usage drains below the configured limit.

_Avoid_: quota violation by a new admission, active-task preemption

## Resource Circuit Breaker

A fault-domain pause that stops admitting new tasks for one affected Resource Class or platform while preserving unrelated queues and Video Workflow Runs. Authentication and infrastructure recovery explicitly close the breaker.

_Avoid_: semantic run failure, automatic retry loop

## Cross-Run Control Store

The project-level SQLite database at `workspace/.workflow-control/control.sqlite3` that is authoritative for this exhaustive cross-run coordination set: unique normalized output-path bindings; Task Claims and fencing generations; resource queues, quotas, leases, circuit breakers, fairness cursors, and scheduler sequences; Run and Acceptance Execution promotion slots; Projection Publication Slots; and initialization, artifact-promotion, acceptance-publication, and delivery-lifecycle Mutation Intents. Per-run lifecycle and artifact contracts remain in each Video Workflow Run Record. Acceptance module transaction state remains in its Acceptance Execution Context, and gate decisions remain in Materialized Gate Reports.

_Avoid_: artifact content store, replacement for `workflow/run.json`

## SQLite Engine Sidecar

A transient file such as `control.sqlite3-journal` created and reclaimed internally by SQLite to implement its transaction protocol. It is outside agent-issued artifact deletion operations; scripts never delete it manually, and database retirement moves any surviving sidecars together with the main database into `待删除`.

_Avoid_: workflow artifact, manually cleaned journal

## Disposable Test Control Store

A fresh file-backed SQLite database created under `待删除/kernel-test-runs/<test-run-id>/` for locking, persistence, migration, or crash-recovery tests. It has no runtime authority or long-term retention requirement; schemas, migrations, JSON fixtures, and assertions are the durable test assets.

_Avoid_: committed binary fixture, production backup

## Mutation Intent

A durable Cross-Run Control Store record that coordinates one filesystem mutation through explicit states such as `PREPARED` and `COMMITTED`. It binds a Task Authority Binding or lifecycle authority, expected prior revision and generations, staging and backup evidence, target paths, and hashes so reconciliation can complete or restore an interrupted change.

_Avoid_: atomic database-filesystem transaction, task instruction

## Run Promotion Slot

The exclusive per-run right to hold one non-terminal Mutation Intent that may publish canonical artifacts and replace `workflow/run.json`. Parallel Task Attempts may wait with complete staging outputs, while their promotions commit serially after fresh dependency validation.

_Avoid_: Resource Lease, task execution lock

## Batch Supervisor

The shallow coordinator that enumerates a multi-item source, orders selected items, asks the Video Workflow Kernel to create or resume independent Video Workflow Runs, submits their runnable tasks to the Resource Admission Module, and publishes a Batch Record projection. It owns no per-video workflow state or delivery decision.

_Avoid_: second workflow engine, full single-video prompt

## Batch Record

The durable batch-level record containing batch identity, source selection, deterministic item order, each item's `run_id` and Video Output Directory, and refreshable Batch Item Projections. It never becomes authoritative for a Video Workflow Run.

_Avoid_: duplicated run state, PDF-existence success marker

## Batch Item Projection

A read-only, rebuildable view of one Video Workflow Run inside a Batch Record. It exposes the current Workflow Phase, Workflow Checkpoint, blocker, and delivery outcome derived from the run's authoritative records and fresh delivery evidence.

_Avoid_: independently writable item status, cached success authority

## Fairness Group

The top-level scheduling identity used by the Resource Admission Module for equal round-robin admission. A Batch uses its `batch_id`; an independent Video Workflow Run uses its `run_id`. Batch groups perform a second equal round-robin across their member runs.

_Avoid_: resource class, priority weight

## Draining Reservation

The deterministic starvation-prevention state entered after an older queued task reaches the effective bypass threshold in the current Resource Admission Configuration; the initial v1 threshold is eight. New tasks that compete for any resource required by the reserved task stop receiving admission while active holders finish; unrelated Resource Classes remain available. The reserved task acquires its complete resource set atomically when capacity is ready.

_Avoid_: partial resource lock, elapsed-time timeout

## Pending Draining Reservation

A starvation-protection request that already has a durable `reservation_seq` but overlaps the resource set of an earlier active Draining Reservation. It retains ordered eligibility and becomes active when its resource set is disjoint from every remaining active reservation.

_Avoid_: active resource drain, reset bypass counter

## Task Attempt

One isolated execution of a logical subagent task. It has a unique attempt identity and script-created staging directory, retains its own outputs and validation evidence, and cannot write directly to canonical artifact paths.

_Avoid_: acceptance attempt only, retry counter without evidence

## Transactional Artifact Promotion

The journaled operation that makes validated Task Attempt outputs canonical under their Task Authority Binding. It preserves displaced canonical files, verifies promoted hashes, and replaces the binding's coordination record as the commit marker only after the complete output set is present. Downstream consumers ignore uncommitted generations.

_Avoid_: direct overwrite, multi-file atomic rename

## Workflow Path Budget

The end-to-end Windows path capacity reserved for a Video Workflow Run and every artifact defined by its scaffold version. It is measured in UTF-16 code units and constrains directory naming before any descendant is created.

_Avoid_: title character limit, LaTeX path workaround

## Stable Truncation Hash

A deterministic short digest retained in a normalized path component when human-readable title text must be shortened. It preserves collision resistance and source identity while the full title remains in workflow metadata.

_Avoid_: random suffix, task timestamp

## Artifact Plan

The machine-readable declaration of expected workflow artifacts, their governing schemas, authoritative generators, and earliest valid generation checkpoints. It makes every fixed contract discoverable from run initialization without pretending that unavailable evidence already exists.

_Avoid_: placeholder report, file checklist

## Earliest Valid Generation Checkpoint

The first Workflow Checkpoint where all inputs required to generate a schema-valid and evidence-current artifact exist. Generating the artifact earlier would create incomplete or stale workflow evidence.

_Avoid_: initialization time, first possible file write

## Video Platform Adapter

The source-specific boundary that supplies a Video Workflow Kernel with platform metadata and acquisition behavior. Bilibili and YouTube adapters may differ in subtitle, cookie, format, and download policy while sharing the same downstream lifecycle.

_Avoid_: separate video workflow, platform-specific pipeline

## Source Acquisition Agent

The isolated semantic subagent launched only for `fresh_download` source preparation. It uses the Video Platform Adapter through a Kernel-issued task, chooses among bounded subtitle and fallback options, records explicit source gaps, and returns a Source Acquisition Decision Judgment Patch. Adapter and Kernel scripts own downloading, canonical paths, technical probes, fingerprints, Source Manifest materialization, and checkpoint transitions.

_Avoid_: data downloader, data preparation agent

## Validated Source Package

The complete script-finalized source-material handoff for downstream outline, writing, and figure work. It may originate from a `fresh_download` Source Acquisition Decision or a successful deterministic `verified_import`. It contains the available platform metadata, cover, timestamped subtitles or transcription fallback, usable media, and a Source Manifest that proves technical validity and records explicit gaps.

_Avoid_: download folder, source summary

## Source Manifest

The machine-readable inventory and validation record for a Validated Source Package. It binds acquired source artifacts to their origin, language, technical properties, fingerprints, and availability status without adding content interpretation.

_Avoid_: workflow run record, content dossier

## Source Acquisition Decision

The bounded semantic input supplied by a Source Acquisition Agent after the kernel creates its task and fixed decision shape. It records choices such as subtitle-track selection, transcription fallback, and explicit source gaps. Scripts own its structural fields, paths, probes, fingerprints, and validation status.

_Avoid_: Source Manifest, freeform download report

## Source Reopen

The explicit kernel operation that permits a finalized Validated Source Package to change. It preserves prior acquisition evidence, returns source preparation to an active state, and invalidates every downstream Workflow Checkpoint whose inputs depend on the earlier source fingerprints.

_Avoid_: direct source edit, silent redownload

## Source Acquisition Mode

The Run-declared method used to prepare original-source evidence. `fresh_download` is the default and launches a Source Acquisition Agent around script-owned adapter mechanics. `verified_import` is a script-controlled import of a prior run's Validated Source Package after deterministic identity, schema, fingerprint, language-policy, and quality checks; a successful import launches no Source Acquisition Agent.

_Avoid_: Version Basis, automatic cache reuse

## Verified Source Import

A script-controlled, run-local copy of a prior Validated Source Package. It imports only original-source artifacts, produces fresh validation evidence for the receiving run, and falls back to a full acquisition task when any required invariant fails.

_Avoid_: external source reference, prior-delivery import

## Fixture Platform Adapter

A test-only Platform Adapter that supplies a small, immutable, locally recorded source package without network, cookie, downloader, Whisper, or semantic-agent dependencies. It exercises the same Bootstrap Probe, initialization, import, validation, artifact, checkpoint, and recovery contracts as a production adapter.

_Avoid_: production source mode, mocked kernel operation

## Source-Ready Tracer Bullet

The first vertical Kernel implementation slice. It starts with a Fixture Platform Adapter and `verified_import`, then crosses Bootstrap Probe, Run Initialization, path validation, deterministic Scaffold creation, schema validation, artifact registration, and reconciliation until the `source_ready` Workflow Checkpoint is current.

_Avoid_: schema-only scaffold, production downloader

## Kernel Implementation Slice

A bounded vertical delivery step that adds one externally verifiable Workflow Kernel capability and exits only after its public Workflow Verification Seams, fail-closed cases, schemas, and applicable crash-recovery behavior pass. Slices may prepare later authority without gaining delivery permission early.

_Avoid_: layer-only rewrite, unverified milestone

## Workflow Verification Seam

One of the public boundaries that carries release-level test authority: the `video_workflow.py` CLI, a deep Module Interface, or a Gate Provider executable contract. Required behavior, failure, persistence, and compatibility tests bind to these seams rather than private implementation structure.

_Avoid_: private helper, prompt snapshot

## Supplemental White-Box Test

A focused test of an internal algorithm or recovery branch used for diagnosis and implementation confidence. It carries no compatibility promise and cannot replace the corresponding Workflow Verification Seam tests.

_Avoid_: public contract, sole release gate

## Kernel Track

The execution track for a new Video Workflow Run created and coordinated entirely by the Video Workflow Kernel. A Kernel Track run has a schema-valid `workflow/run.json`, uses Kernel operations for every governed mutation, and can gain delivery authority only after its platform is atomically activated.

_Avoid_: partially adopted legacy directory, pilot delivery

## Legacy Track

The historical execution and artifact track for output directories created before a platform's Kernel activation. Legacy directories remain outside Kernel coordination and receive no automatically synthesized Video Workflow Run Record.

_Avoid_: migration candidate automatically upgraded, mixed-track run

## Legacy Acceptance Input Set

A script-generated, immutable v2 gate input contract that fingerprints explicitly named final Legacy artifacts, allowed evidence, compile provenance, and rendered pages without creating a Video Workflow Run Record or migrating historical workflow state. It supports a fresh dual-Reviewer decision and never converts a v1 report.

_Avoid_: Run migration, prior acceptance reuse

## Global Gate Cutover

The atomic repository change that activates a shared gate contract across both Legacy and Kernel Tracks. The first Global Gate Cutover activates Acceptance Report v2, its Dimension Map, dual Judgment Patches, materializer, validator, Delivery Guard rules, instructions, and tests before any Platform Kernel Cutover.

_Avoid_: platform run-ownership cutover, partial gate deployment

## Exit Evidence Manifest

A versioned, machine-readable proof that one Kernel Implementation Slice or cutover met its declared commands, fixtures, checkpoints, negative tests, contract fingerprints, and activation scope. Runtime authority may change only when the applicable Exit Evidence Manifest validates.

_Avoid_: narrative progress note, test command without results

## Platform Kernel Cutover

The atomic repository change that activates Kernel Track creation for one source platform and synchronizes its Kernel contracts, `.agents` and `.claude` skills, project instructions, providers, validators, guards, and tests. Activation order is Bilibili single-video, YouTube single-video, then Batch.

_Avoid_: per-run dual write, partial documentation switch

## Video Workflow Run

One durable execution of the Video Workflow Kernel for a single Video Output Directory. Its identity survives Codex session changes and explicit delivery-ownership handoffs.

_Avoid_: session, acceptance attempt

## Video Workflow Run Record

The authoritative coordination state for a Video Workflow Run. It binds declared artifacts and references current checkpoint evidence while preserving each gate-specific report as the authority for its own decision.

_Avoid_: delivery target, task index, acceptance report

## Contract Schema Version

The explicit version of one machine-readable workflow contract, such as a run record, task envelope, artifact plan, Source Manifest, or gate report. Writers always declare it, readers accept only registered compatible versions, and compatibility is never inferred from missing fields.

_Avoid_: kernel release, scaffold version

## Kernel Schema Registry

The closed registry of kernel-owned JSON Schema Draft 2020-12 contracts under `schemas/video-workflow/`. It maps each schema name and version to one authoritative schema, skeleton builder, examples, and any registered cross-artifact invariant checks.

_Avoid_: Python model duplication, unregistered JSON shape

## Contract Skeleton

A script-generated instance of a registered contract that already contains all structural fields, current paths, immutable identities, fingerprints, and bounded judgment slots available at its generation checkpoint. Every skeleton validates before an agent receives it.

_Avoid_: example report, agent-authored schema

## Judgment Patch

A schema-constrained semantic response bound to one immutable Contract Skeleton by task identity and skeleton SHA-256. It contains only fields an agent is authorized to judge and cannot redefine paths, fingerprints, page counts, contract versions, or other structural evidence.

_Avoid_: edited skeleton, complete freeform report

## Materialized Gate Report

The final authoritative report generated by a provider script after it validates and merges a Judgment Patch into its matching Contract Skeleton. Gate decisions belong to this validated report; the patch remains input evidence.

_Avoid_: reviewer prose, Judgment Patch as final decision

## Scaffold Version

The explicit version of the Video Output Scaffold definition that governs allowed directories, canonical artifact paths, dynamic slot rules, and path-budget calculations for one run.

_Avoid_: Contract Schema Version, Video Deliverable Version

## Run Migration Plan

A deterministic, reviewable description of changes required to move an existing run between supported contract or scaffold versions. It is generated without mutating the run and becomes executable only through a separately authorized migration operation.

_Avoid_: automatic resume repair, informal upgrade notes

## Explicit Run Resume

The only operation that continues an existing Video Workflow Run. It names the existing Video Output Directory directly and succeeds only when its `workflow/run.json`, run identity, source identity, and scaffold version satisfy the kernel's resume contract.

_Avoid_: latest-run discovery, same-URL reuse

## Video Deliverable Version

A human-selected positive version number for an intentional new deliverable derived from the same canonical video source, rendered for people as `v1`, `v2`, and later values. It is independent from the Video Workflow Run identity, acceptance-attempt number, schema version, and scaffold version. A normal run defaults to `v1`; higher values require explicit user selection and never auto-increment. The version is always stored in `workflow/run.json`. The default `v1` keeps the unversioned path form, while `v2` and later values add their version marker to both the Video Output Directory and Final PDF Filename.

_Avoid_: run retry, resume count, acceptance attempt

## Version Basis

The declared evidence basis for an intentional Video Deliverable Version. `source_only` rebuilds from original-source artifacts and does not require an earlier PDF. `prior_delivery` intentionally consumes an earlier delivered version and therefore binds its identity and fingerprints. Every version above `v1` records a human revision reason, while version numbering remains explicit and may be non-contiguous.

_Avoid_: workflow checkpoint dependency, automatic version lineage

## Workflow Phase

A coarse progress projection for human observability over a Video Workflow Run. It summarizes where the run is concentrated without deciding whether downstream work is allowed to proceed.

_Avoid_: authoritative stage, gate decision

## Workflow Checkpoint

A dependency-aware unit of workflow readiness whose current result is tied to declared input evidence. Checkpoints allow parallel work, local retry, and downstream invalidation when an upstream artifact changes.

_Avoid_: phase, task status

## Artifact Generation

One committed version of a canonical workflow artifact. Its current registry entry binds a logical artifact identity to a canonical path, generation number, SHA-256 digest, producing task, and commit time. Older generations remain historical evidence.

_Avoid_: file modification time, task attempt

## Checkpoint Freshness

The condition where every input generation and SHA-256 digest bound by a completed Workflow Checkpoint still matches the current artifact registry and every prerequisite checkpoint remains current. Freshness is computed by the kernel and cannot be declared by an agent.

_Avoid_: recently generated, report exists

## Artifact Drift

A canonical artifact state detected when filesystem content differs from its committed Artifact Generation outside a kernel promotion or adoption operation. Drift blocks dependent work until an explicit artifact adoption or restoration operation resolves it.

_Avoid_: automatically accepted manual edit

## Trusted Fingerprint Cache

A performance cache for the already computed SHA-256 digest of a kernel-frozen Artifact Generation. Routine reconciliation may reuse it while file identity, size, and nanosecond modification time remain unchanged; trust-boundary operations and any detected metadata change require full content hashing.

_Avoid_: alternative authoritative digest, metadata-only proof

## Checkpoint Dependency

A directed readiness relationship where one Workflow Checkpoint requires current evidence from another before it may complete. A changed or invalidated prerequisite prevents dependent checkpoints from remaining current.

_Avoid_: execution timestamp order

## Valid Video Output Directory

A Video Output Directory that can be included in path normalization or migration. Validity requires internal final-delivery identity evidence inside the directory.

Accepted identity evidence includes durable files that state the final delivered video name, the PDF article title, or a main/final PDF artifact such as `main.pdf`, `notes.pdf`, a direct `build/*.pdf` delivered PDF, or a normalized delivered PDF. Directories that only contain scratch files, extracted frames, logs, cache files, partial downloads, or inferred dates are outside this valid set.

## High Confidence Video Migration

A path migration case where a Valid Video Output Directory has enough internal evidence for automatic relocation into its normalized video documentation name.

Multiple PDF artifacts can still be high confidence when the directory contains final-delivery identity evidence. A Video Artifact Date inferred only from PDF time can also be high confidence. A series-like source directory whose internal title cannot match an episode number can still be high confidence when the normalized name uses the series identity plus the actual delivered content name.

## Low Confidence Video Migration

A path migration case that should be separated for human review. Low confidence applies when final-delivery identity evidence is missing, the target identity cannot be inferred from internal durable files, or a path conflict cannot be resolved mechanically.

## Video Documentation Workspace

The normalized holding area for migrated video documentation directories. High Confidence Video Migration cases are moved into the workspace under their normalized video documentation names. Low Confidence Video Migration cases are moved into a dedicated low-confidence review area inside the workspace while preserving their original directory names.

Migration moves the whole Video Output Directory as one unit. Internal artifact layout is preserved so TeX files, images, PDFs, reviews, and source metadata keep their relative paths.

## Normalized Video Title

The original platform video title after project path-name normalization. The normalized title is the human-readable identity seed for video output directories.

## Video Artifact Date

The date used in normalized output paths for video documentation artifacts. It is formatted as `yyyyMMdd` and represents the earliest trustworthy durable artifact date for a video output directory.

The date is inferred first from durable artifacts such as platform metadata, `outline_contract.md`, `main.tex`, final PDFs, and `review/pyramid/*.json`. Final PDF time is a fallback when earlier durable evidence is missing. Directory `CreationTime` is not authoritative because moving, copying, or reconstructing directories can distort it.

## Series Video Output Name

The normalized output directory name for one item inside a video series. It uses the series identity, a two-digit episode number, and the Video Artifact Date: `{series-name}_{episode-number}_{yyyyMMdd}`.

The episode number is zero-padded, such as `01`, `02`, or `15`, so directory ordering matches series order. Long per-episode titles remain in metadata, the TeX/PDF title, and review artifacts rather than dominating the directory name.

When a source directory looks like a series item but the internal final-delivery identity evidence cannot be matched to a reliable episode number, the normalized name remains a single directory name and uses the best available delivered content title instead: `{series-name}_{actual-content-title-or-article-title}_{yyyyMMdd}`.

## Final PDF Filename

The delivered PDF filename for a completed video note. It is based on the PDF article title when that title is explicit, or on the original video title when the PDF title has not been separately established.

## Pyramid Gate Integration Scope

The Pyramid Gate is integrated through the local `pyramid-principle-validate` skill, the Bilibili render PDF skill, the YouTube render PDF skill, and the project `AGENTS.md` instructions. This makes pyramid validation a project workflow rule instead of an optional reviewer habit.

## Waiver

A recorded exception that allows the workflow to continue even when pyramid structure is intentionally relaxed. A waiver requires explicit user approval and a reason, usually because the source material is better served by a different organization pattern such as question-bank grouping, dialogue preservation, or parallel case cataloging.

A semantic evaluator may identify why an artifact fails or needs revision, but it must not grant a waiver by itself. Waiver authority belongs to the human workflow owner because a waiver is a process decision to continue despite a known structural weakness.

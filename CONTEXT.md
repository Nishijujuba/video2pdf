# Project Glossary

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

The first-version criteria category set is limited to `style`, `figure_visual_integrity`, `table_layout_integrity`, and `credibility_disclosure_placement`.

## Acceptance Criterion

A single delivery-blocking rule inside the criteria list of an Acceptance Criteria File. For style review, it carries a stable id, category, rule text, violation patterns, allowed exceptions, scan policy, pass condition, and fail condition.

## Acceptance Scope

The explicit artifact boundary declared by an Acceptance Criteria File. It lists which final delivered artifacts the Acceptance Reviewer may inspect and which artifacts are excluded from acceptance review.

## Acceptance Report

A reviewer-authored JSON artifact that records the acceptance result against an Acceptance Criteria File. It must carry the pass/fail decision, evidence, and unresolved gaps separately from the criteria being applied.

## Acceptance Report Schema

The minimal JSON contract for an Acceptance Report. It records the criteria file, overall status, decision source, review context used, artifact fingerprints, criterion results, visual scan evidence, failed criteria, and whether revision is required.

## Acceptance Report Skeleton

A fail-closed template for an Acceptance Report. It gives the Acceptance Reviewer the fixed report shape, current artifact fingerprints, and rendered-page coverage slots before judgment begins, while still requiring the reviewer to replace placeholder judgments with artifact-grounded evidence.

## Acceptance Decision Source

The Acceptance Report JSON is the only machine-readable source of truth for whether an artifact passes acceptance. Human-readable Markdown summaries may explain the result, but they cannot override the JSON decision.

## Acceptance Must Gate

A mandatory acceptance criterion whose failure blocks delivery regardless of any aggregate score. Aggregate scores may summarize quality, but they cannot override a failed must gate.

## Blocking-Only Acceptance Criteria

The rule that an Acceptance Criteria File contains only delivery-blocking criteria. Advisory, nice-to-have, scoring-only, or non-blocking improvement checks are outside this acceptance flow.

Every entry in the criteria list is implicitly delivery-blocking, so the Acceptance Criteria File does not need a severity field.

## Complete Acceptance Evaluation

The rule that an Acceptance Reviewer must evaluate every criterion in the Acceptance Criteria File even after finding a failure. The Acceptance Report lists all failed criteria so the artifact can be revised in one pass.

## Acceptance Revision Guidance

Required repair direction recorded for each failed criterion in an Acceptance Report. It tells the writer what must change and what fix types are allowed, while preserving the Acceptance Reviewer's read-only role.

## Style Acceptance Criterion

An acceptance criterion that checks the final artifact's writing style, structure, tone, redundancy, reader experience, or forbidden content patterns. It does not require source-faithfulness evidence, but its pass or fail judgment must still cite concrete locations in the final artifact.

## Figure Visual Integrity Criterion

A delivery-blocking acceptance criterion that checks whether final figures, diagrams, charts, and visual explanations are readable, aligned, complete, and professionally laid out. It treats text overflow, text-arrow collisions, broken alignment, confusing callout relationships, mismatched font and container sizes, and draft-like visual polish as acceptance failures.

## Table Layout Integrity Criterion

A delivery-blocking acceptance criterion that checks whether final tables are readable, contained within page boundaries, and professionally laid out. It treats clipped columns, text running past the page edge, broken wrapping, unreadable density, ambiguous table structure, and caption-table mismatch as acceptance failures.

## Credibility Disclosure Placement Criterion

A delivery-blocking acceptance criterion that checks whether credibility caveats, such as ASR noise, OCR uncertainty, source limitations, or reviewer methodology notes, are placed without disrupting the main reading flow. Such caveats may appear in footnotes, captions, appendices, or source notes when needed, but should not interrupt the body as meta-process exposition.

## Final Delivery Quality Criterion

A delivery-blocking acceptance criterion that protects the final PDF's readability, professional finish, and credibility. It covers writing style, visual integrity, table layout, and credibility disclosure placement when defects would make the delivered PDF feel unreliable or unfinished.

## Rendered PDF Visual Review

The required review mode for visual and final-delivery quality acceptance criteria. The Acceptance Reviewer must inspect rendered PDF pages, not only source TeX, before passing figure visual integrity, table layout integrity, or credibility disclosure placement criteria.

## Full Rendered PDF Visual Scan

The required visual scan policy for final-delivery quality acceptance. An independent Acceptance Reviewer must inspect every rendered PDF page with Codex visual capability and report all detected delivery-blocking visual failures, without relying on a human manual review stage.

## Visual Scan Evidence

Coverage proof recorded in an Acceptance Report for a Full Rendered PDF Visual Scan. It lists the reviewed PDF, total page count, and one result entry for every rendered page so the page coverage can be verified.

## Style Violation Pattern

A concrete textual pattern that tells an Acceptance Reviewer what a Style Acceptance Criterion treats as a violation. Style criteria also define allowed exceptions so the reviewer can distinguish genuine style failures from valid topic-driven usage.

## Full Artifact Style Scan

The required scan policy for a must-level Style Acceptance Criterion. The Acceptance Reviewer must inspect the full final text artifact for the declared style violation patterns before reporting a pass.

## Scan Evidence

Coverage proof recorded in an Acceptance Report for a Full Artifact Style Scan. It identifies the scanned final artifacts, scan range, and artifact fingerprint so a pass decision can be recognized as stale after the artifact changes.

## Acceptance Report Freshness

The rule that an Acceptance Report is valid only for the exact artifact fingerprints it reviewed. If any in-scope final artifact changes after review, the old report is stale and the artifact must be accepted again.

## Acceptance Evidence

Artifact-grounded proof used by an Acceptance Report to justify a pass or fail decision. For must gates, evidence must point to concrete artifact locations, such as files, pages, sections, timestamps, images, or source snippets, so the judgment can be independently checked.

## Acceptance Review Context

The only information an acceptance reviewer is allowed to use: the final delivered artifacts and the Acceptance Criteria File. Generation process records, chat history, writer notes, and intermediate drafts are outside the review context.

## Acceptance Reviewer

A read-only reviewer that evaluates final delivered artifacts against an Acceptance Criteria File. The reviewer may write the Acceptance Report and optional human summary, but must not modify final artifacts, source materials, criteria files, generation records, or intermediate drafts.

## Session-Scoped Delivery Target

The active delivery target owned by one Codex session. It lets the Stop hook guard the PDF delivery flow for the current session without reading or blocking delivery targets owned by other concurrent sessions.

## Delivery Task Index

A project-level index of video delivery tasks. It supports task recovery, ownership checks, and workflow observability, but it is not the Stop hook's delivery-blocking source.

## Delivery Target Ownership

The relationship between one Video Output Directory and the Codex session currently allowed to advance its generation, acceptance, repair, and final delivery workflow.

## Delivery Target Handoff

An explicit transfer of Delivery Target Ownership from one Codex session to another. It preserves the previous owner relationship so interrupted or superseded delivery workflows remain auditable.

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

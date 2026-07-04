# Project Glossary

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

## Pyramid Review Directory

The persistent evidence directory for Pyramid Gate outputs inside each video output folder: `review/pyramid/`. It stores stage reports such as `outline.pyramid.json`, `section_01.pyramid.json`, `main.pyramid.json`, and a human-readable `summary.md`. Disposable drafts and temporary attempts belong under `待删除`.

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

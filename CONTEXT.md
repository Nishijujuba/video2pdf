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

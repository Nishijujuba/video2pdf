# PRD: Codex exec backed Pyramid Principle Gate

## Problem Statement

The video-to-PDF workflow needs a reliable Pyramid Gate that performs real semantic structure review instead of merely validating that a JSON report exists. The current Pyramid Principle validation skill mixes general text review with Bilibili and YouTube workflow stages, which makes the skill less reusable and leaves too much room for future agents to treat pyramid validation as a casual reviewer habit rather than a blocking quality gate.

The user also wants to avoid adding a separate OpenAI API key path for this workflow. The evaluation should reuse the local Codex CLI authentication already present in the project while still producing machine-readable reports that can stop the PDF workflow when structure is weak.

## Solution

Refactor the Pyramid Principle validation capability into a general-purpose text evaluator that uses `codex exec` as its first-version semantic evaluation backend. The evaluator reads a text artifact, injects the text into a fixed Pyramid Principle evaluation prompt, asks Codex CLI for a JSON-schema-constrained final report, adds audit metadata, validates the report, and writes the result to a caller-provided output file.

The Bilibili and YouTube render workflows will call that general evaluator at three checkpoints: after the outline contract exists and before writer agents start, after every section draft exists and before integration, and after the integrated main document exists and before PDF compilation. Each video output directory will keep the resulting Pyramid Gate evidence under its review area, and a failing gate will stop the workflow unless the user explicitly instructs continuation through a recorded waiver.

## User Stories

1. As a video-to-PDF workflow owner, I want Pyramid Gate checks to evaluate real text structure, so that generated PDFs do not pass quality gates with only syntactically valid reports.
2. As a video-to-PDF workflow owner, I want the Pyramid Principle validation skill to be general-purpose, so that it can evaluate any written text artifact without carrying Bilibili or YouTube-specific concepts.
3. As a video-to-PDF workflow owner, I want Bilibili and YouTube render skills to own their workflow checkpoints, so that the general evaluator remains reusable.
4. As a video-to-PDF workflow owner, I want each gate report to record the artifact type and context label, so that workflow-specific labels such as outline, section, and main are kept outside the evaluator core.
5. As a video-to-PDF workflow owner, I want the evaluator to reuse Codex CLI authentication, so that the workflow does not require a second API key configuration path.
6. As a video-to-PDF workflow owner, I want the evaluator to run with read-only permissions, so that a semantic review cannot accidentally modify project files.
7. As a video-to-PDF workflow owner, I want the evaluator to disable hooks during nested Codex execution, so that hook-triggered checks do not recursively start more Codex runs.
8. As a video-to-PDF workflow owner, I want the evaluator to run without approval prompts, so that long-running PDF workflows do not hang waiting for interactive approval.
9. As a video-to-PDF workflow owner, I want the evaluator to run ephemerally, so that every gate check does not leave unnecessary Codex session artifacts.
10. As a video-to-PDF workflow owner, I want the evaluator to produce JSON constrained by a schema, so that later workflow steps can make deterministic pass, revise, block, or waiver decisions.
11. As a video-to-PDF workflow owner, I want reports to include an input fingerprint, so that stale reports can be recognized after an artifact changes.
12. As a video-to-PDF workflow owner, I want reports to include input size and large-input approval state, so that unusually large evaluations remain auditable.
13. As a video-to-PDF workflow owner, I want the evaluator to fail when input exceeds the default limit without explicit approval, so that large prompts are a conscious workflow decision.
14. As a video-to-PDF workflow owner, I want explicit large-input approval to be represented by a command flag, so that automation never blocks on an interactive confirmation prompt.
15. As a video-to-PDF workflow owner, I want the evaluator to write the report file itself, so that PowerShell quoting and redirection do not become part of the workflow contract.
16. As a video-to-PDF workflow owner, I want the wrapper to validate Codex's JSON after generation, so that schema drift or malformed output fails immediately.
17. As a video-to-PDF workflow owner, I want a failing outline gate to stop writer agents, so that structurally weak outlines do not propagate into chapters.
18. As a video-to-PDF workflow owner, I want a failing section gate to stop integration, so that weak section drafts do not become part of the main document.
19. As a video-to-PDF workflow owner, I want a failing main-document gate to stop PDF compilation, so that final PDFs are not produced from structurally weak source.
20. As a workflow reviewer, I want waiver authority to belong to the user rather than the semantic evaluator, so that the model cannot grant itself permission to bypass a quality gate.
21. As a workflow reviewer, I want waiver reports to record the approver and reason, so that future readers can understand why a known weakness was accepted.
22. As a workflow reviewer, I want the general evaluator to use the Pyramid Principle Text Standard, so that the core standard remains independent of videos, PDFs, outlines, and sections.
23. As a workflow reviewer, I want Bilibili and YouTube workflows to pass teaching-document context into the evaluator, so that video-derived learning notes are judged appropriately without polluting the general skill.
24. As a workflow reviewer, I want the video workflows to maintain a human-readable summary of all Pyramid Gate reports, so that a user can inspect the full gate history for a video.
25. As a future agent, I want the `codex exec` backend decision documented, so that I do not replace it with the OpenAI SDK without understanding the credential and workflow trade-offs.
26. As a future agent, I want the report schema to use general fields rather than fixed workflow stages, so that the evaluator can later be used for articles, Markdown reports, or other long-form text.
27. As a future agent, I want command failures to have clear exit behavior, so that workflow orchestration can stop at the right checkpoint.
28. As a future agent, I want the Bilibili and YouTube render skills to show the exact gate calls they expect, so that each checkpoint is easy to execute consistently.
29. As a future agent, I want tests around the report validator and evaluator wrapper, so that command construction, size limits, waiver handling, and schema validation remain stable.
30. As a user consuming the final PDF, I want the generated document to present ideas in a clear top-down structure, so that the PDF reads like a coherent learning note rather than a stitched transcript summary.

## Implementation Decisions

- The Pyramid Principle validation skill will be refactored into a general text evaluation skill.
- The general standard will be named Pyramid Principle Text Standard.
- The Teaching-PDF Pyramid Standard remains a video-to-PDF workflow application of the general standard.
- The evaluator will use `codex exec` as the only first-version semantic backend.
- The OpenAI SDK backend is explicitly out of scope for this version.
- The evaluator will use a file-based interface with an input artifact and an output report.
- The evaluator will read the input file itself and inject the text into the prompt rather than asking the nested Codex run to explore the filesystem.
- The default input limit will be 160000 characters.
- Inputs over the default limit will fail unless the caller passes an explicit large-input approval flag.
- The nested `codex exec` run will be read-only, non-interactive, hook-disabled, ephemeral, and schema-constrained.
- The nested Codex run must return only semantic judgment fields. The wrapper owns audit metadata.
- The schema will replace workflow-specific `stage` with `artifact_type` and `context_label`.
- The report will include audit metadata for standard, backend, prompt version, input hash, input size, max size, large-input approval state, evaluation context, and generation time.
- The semantic evaluator may return pass, needs revision, or blocked. It may not grant waived status by itself.
- Waiver status requires explicit user approval and a reason.
- The Bilibili render workflow will call the evaluator after the outline contract, after each section draft, and after main document integration.
- The YouTube render workflow will call the evaluator at the same three checkpoints.
- Batch orchestration is deferred from the first cleanup and implementation pass. The `bilibili-batch-render-pdf` skill should be updated only after the general evaluator and the single-video Bilibili/YouTube contracts are coherent.
- Each checkpoint report will be written under the video output directory's Pyramid Review Directory.
- The video workflows, rather than the general evaluator, will maintain the human-readable gate summary.
- A failing Pyramid Gate stops the workflow at that checkpoint unless the user explicitly instructs continuation through waiver.
- The existing ADR records the `codex exec` backend choice and rejected SDK alternative.

## Testing Decisions

- The highest test seam is the evaluator wrapper command-line interface. Tests should exercise observable behavior through command arguments, output files, exit codes, and generated report content rather than internal helper implementation details.
- The report validator should be tested with valid reports, missing required fields, extra fields, invalid scores, inconsistent waiver fields, blocked statuses under gate enforcement, and stale or malformed audit data.
- The evaluator wrapper should be tested without making real Codex network calls by substituting a fake `codex` executable or command runner at the process boundary.
- Size-limit behavior should be tested at below-limit, exactly-at-limit, over-limit, and over-limit-with-explicit-approval cases.
- Waiver behavior should be tested to ensure the semantic model cannot emit an automatic waiver and that user-approved waiver data is required before a waived report can pass validation.
- The Bilibili and YouTube skill updates should be checked by reading their workflow instructions and verifying that all three checkpoints call the general evaluator and stop on failure.
- The whole Pyramid Gate set should still be validated at the output-directory level for outline, section, main, and summary evidence.
- Tests should use the existing project Python environment used for skill validation when the default Python environment lacks required dependencies.
- A good test checks external workflow promises: a missing report blocks, a failed report blocks, a passing report allows continuation, and an explicit waiver is recorded before continuation.

## Out of Scope

- Adding an OpenAI SDK backend.
- Abstracting multiple semantic evaluation backends.
- Packaging the new Bilibili and YouTube workflows as a plugin.
- Implementing or restoring project lifecycle hooks.
- Running full Bilibili or YouTube video-to-PDF generation.
- Updating batch orchestration or batch reconcile behavior in the first pass.
- Changing subtitle acquisition, figure extraction, LaTeX compilation, PDF layout checks, or independent content review.
- Allowing warn-only Pyramid Gate behavior by default.
- Letting the semantic model grant waiver status.
- Introducing a separate API key configuration path for Pyramid evaluation.

## Further Notes

The main implementation risk is accidental nested-agent behavior. The evaluator must stay shaped like a constrained text review subprocess, not a general Codex worker that can inspect the repository, trigger hooks, or modify files.

The second major risk is stale evidence. Reports need input fingerprints because a report that still exists after a source file changed is misleading unless the fingerprint shows it belongs to the previous content.

The project should keep plugin packaging out of this first version. Plugin work becomes useful only after the general evaluator, video workflow calls, and optional hook behavior are stable enough to distribute.

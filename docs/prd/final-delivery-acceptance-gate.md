# PRD: Final Delivery Acceptance Gate

## Problem Statement

The video-to-PDF workflow already has Pyramid Gate checks, LaTeX compilation, layout blank-space checks, and an independent content review against subtitles. Those checks do not enforce the user's final delivery acceptance standards for writing taste, rendered visual quality, table layout integrity, or credibility caveat placement.

The user needs a final hard gate before report delivery. This gate must be run by a separate Acceptance Reviewer subagent that reads only final delivered artifacts and the Acceptance Criteria File. If the gate fails, the workflow must use repair subagents to revise the artifact and then rerun acceptance until the Acceptance Report passes.

Without this gate, PDFs can still ship with body text explaining generation choices, draft-like diagrams, clipped tables, or ASR caveats interrupting the main reading flow. These defects damage readability, professional finish, and credibility even when the source content is complete.

## Solution

Implement a Final Delivery Acceptance Gate as the last workflow gate before delivering a Bilibili or YouTube PDF. The gate uses the JSON acceptance contract already defined by the project glossary and ADR: a user-authored Acceptance Criteria File is the read-only standard, and a reviewer-authored Acceptance Report JSON is the only machine-readable delivery decision source.

The end-to-end workflow will change in three places.

First, project orchestration instructions will define a new final Acceptance Reviewer role. This subagent is independent and read-only. It may inspect only final delivered artifacts and the Acceptance Criteria File. It must not inspect generation process records, chat history, writer notes, intermediate drafts, or prior repair discussion. It writes the Acceptance Report and optional human summary.

Second, the Bilibili and YouTube render workflows will call the Final Delivery Acceptance Gate after the final PDF has been rendered and before delivery. The gate must use the project acceptance criteria template, perform a full rendered PDF visual scan with Codex visual capability, run full text checks for style criteria, bind reports to artifact fingerprints, and stop delivery if any criterion fails.

Third, acceptance validation tooling will enforce the JSON contract. The tooling will validate the Acceptance Criteria File, validate Acceptance Reports, check artifact fingerprint freshness, verify that all criteria were evaluated, verify that all rendered PDF pages have a page-level visual scan entry, and reject reports that claim pass while violating the contract.

If acceptance fails, the workflow will spawn repair subagents using the Acceptance Report as the repair brief. Repair subagents may modify the final artifacts as needed. After repair, the workflow recompiles or regenerates affected final artifacts, refreshes any stale upstream evidence required by the changed files, and runs a new Acceptance Reviewer subagent from a clean acceptance context. The previous failed Acceptance Report remains evidence, but it cannot approve the revised artifact.

### Concrete artifact paths

The default project criteria template is:

- `docs/acceptance/acceptance_criteria.v1.json`

Each video output directory will contain acceptance evidence under:

- `<video-output-dir>/review/acceptance/allowed_artifacts_manifest.json`
- `<video-output-dir>/review/acceptance/rendered_pages/page_0001.png`
- `<video-output-dir>/review/acceptance/rendered_pages/page_0002.png`
- `<video-output-dir>/review/acceptance/rendered_pages/...`
- `<video-output-dir>/review/acceptance/acceptance_report.json`
- `<video-output-dir>/review/acceptance/acceptance_summary.md`

The acceptance report path is fixed: `review/acceptance/acceptance_report.json`. The optional human summary path is fixed: `review/acceptance/acceptance_summary.md`. Rendered page evidence always lives under `review/acceptance/rendered_pages/` with zero-padded one-based page numbers.

The project implementation will add a dedicated acceptance skill and validator tooling under:

- `.agents/skills/final-delivery-acceptance/SKILL.md`
- `.agents/skills/final-delivery-acceptance/scripts/render_pdf_pages.py`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_criteria.py`
- `.agents/skills/final-delivery-acceptance/scripts/validate_acceptance_report.py`
- `.agents/skills/final-delivery-acceptance/references/acceptance-criteria.schema.json`
- `.agents/skills/final-delivery-acceptance/references/acceptance-report.schema.json`

The Bilibili workflow integration target is `.agents/skills/bilibili-render-pdf/SKILL.md`, specifically its Final Checklist and delivery flow after PDF rendering. The YouTube workflow integration target is `.agents/skills/youtube-render-pdf/SKILL.md`, specifically its Final Checklist and delivery flow after PDF rendering. Project orchestration role requirements are recorded in `AGENTS.md`.

### Minimum criteria JSON contract

The criteria file must contain these top-level fields:

```json
{
  "schema_version": "1.0",
  "criteria_version": "1.0",
  "name": "final_delivery_quality_acceptance",
  "scope": {
    "final_artifacts": [
      "main.tex",
      "final.pdf"
    ],
    "excluded_artifacts": [
      "generation_notes",
      "writer_drafts",
      "chat_history",
      "intermediate_files"
    ]
  },
  "review_context_policy": {
    "final_artifacts_and_criteria_only": true,
    "forbid_generation_process": true
  },
  "evaluation_policy": {
    "blocking_only": true,
    "fail_fast": false,
    "report_all_failures": true,
    "allowed_categories": [
      "style",
      "figure_visual_integrity",
      "table_layout_integrity",
      "credibility_disclosure_placement"
    ]
  },
  "criteria": []
}
```

Each criterion entry must contain:

```json
{
  "id": "no_meta_writing_content",
  "category": "style",
  "rule": "Human-readable blocking rule.",
  "violation_patterns": [],
  "allowed_exceptions": [],
  "scan_policy": "full_artifact_style_scan",
  "pass_condition": "Human-readable pass condition.",
  "fail_condition": "Human-readable fail condition."
}
```

The schema must reject unknown categories, missing required fields, empty `criteria`, severity fields, advisory checks, scoring-only checks, and non-blocking criteria.

### Minimum report JSON contract

The Acceptance Report must contain these top-level fields:

```json
{
  "schema_version": "1.0",
  "criteria_version": "1.0",
  "criteria_file": "docs/acceptance/acceptance_criteria.v1.json",
  "overall_status": "pass",
  "decision_source": "acceptance_report_json",
  "review_context_used": {
    "allowed_artifacts_manifest": "review/acceptance/allowed_artifacts_manifest.json",
    "final_artifacts_only": true,
    "generation_process_used": false,
    "artifacts_read": []
  },
  "artifact_fingerprints": [],
  "criterion_results": [],
  "visual_scan_evidence": null,
  "failed_criteria": [],
  "revision_required": false
}
```

Each `artifact_fingerprints[]` entry must include:

```json
{
  "path": "main.tex",
  "sha256": "sha256:...",
  "size_bytes": 0,
  "size_chars": 0
}
```

Each `criterion_results[]` entry must include:

```json
{
  "criterion_id": "no_meta_writing_content",
  "category": "style",
  "status": "pass",
  "evidence": [],
  "scan_evidence": null,
  "revision_guidance": null
}
```

Failed criterion results must include non-empty `evidence` and non-null `revision_guidance`. Revision guidance must state the required change and allowed fix types.

### Context isolation mechanism

The master workflow must create `review/acceptance/allowed_artifacts_manifest.json` before spawning the Acceptance Reviewer. This manifest is the only artifact list passed to the reviewer.

The manifest must include:

```json
{
  "criteria_file": "docs/acceptance/acceptance_criteria.v1.json",
  "review_output_dir": "review/acceptance",
  "final_artifacts": [
    {
      "role": "tex",
      "path": "main.tex"
    },
    {
      "role": "pdf",
      "path": "final.pdf"
    }
  ],
  "forbidden_artifacts": [
    "generation_notes",
    "writer_drafts",
    "chat_history",
    "intermediate_files",
    "work/",
    "review/consistency/",
    "review/pyramid/"
  ]
}
```

The validator cannot prove what a model mentally considered. It can enforce the workflow contract: the master passes only the allowed manifest, the report must declare `generation_process_used: false`, `artifacts_read` must be a subset of manifest final artifacts plus the criteria file, and any evidence path outside the manifest causes validation failure.

### Rendered page evidence

The acceptance tooling must render the final PDF into page images before visual review. The required output directory is:

- `<video-output-dir>/review/acceptance/rendered_pages/`

The required filename format is:

- `page_0001.png`
- `page_0002.png`
- `page_0003.png`

The rendering helper must write every page image and provide the PDF page count. The Acceptance Reviewer uses those images for visual inspection. The validator checks that every page from `1` through `page_count` has exactly one `pages_checked[]` result.

The `visual_scan_evidence` object must use this shape:

```json
{
  "pdf": "final.pdf",
  "page_count": 32,
  "rendered_pages_dir": "review/acceptance/rendered_pages",
  "pages_checked": [
    {
      "page": 1,
      "rendered_page_image": "review/acceptance/rendered_pages/page_0001.png",
      "status": "pass",
      "criteria_checked": [
        "figure_visual_integrity",
        "table_layout_integrity",
        "credibility_disclosure_placement"
      ],
      "failures": []
    }
  ]
}
```

A failed page entry must include the criterion id, category, visible defect description, rendered page image path, and PDF page number. TeX source locations may be included as repair help, but the visual finding must cite the rendered page evidence.

## User Stories

1. As a PDF workflow owner, I want final delivery acceptance to run after the PDF is rendered, so that the reviewer judges the same artifact the reader will receive.
2. As a PDF workflow owner, I want the Acceptance Reviewer to be a separate subagent, so that the generator cannot approve its own final artifact.
3. As a PDF workflow owner, I want the Acceptance Reviewer to read only final artifacts and the criteria file, so that generation intent cannot compensate for reader-facing defects.
4. As a PDF workflow owner, I want generation process records excluded from acceptance context, so that the reviewer cannot use hidden author intent to excuse weak delivery.
5. As a PDF workflow owner, I want `acceptance_criteria.json` to be a read-only standard, so that the reviewer cannot rewrite the standard during review.
6. As a PDF workflow owner, I want `acceptance_report.json` to be the only machine decision source, so that delivery automation can deterministically pass or fail.
7. As a PDF workflow owner, I want Markdown summaries to be optional, so that human readability can be added without weakening the JSON decision.
8. As a PDF workflow owner, I want every criterion in the criteria file to be delivery-blocking, so that no advisory or scoring-only checks enter this flow.
9. As a PDF workflow owner, I want the gate to avoid severity levels, so that every configured criterion has the same hard delivery meaning.
10. As a PDF workflow owner, I want the reviewer to evaluate all criteria after finding a failure, so that repair can address the full failure set in one pass.
11. As a PDF workflow owner, I want each failed criterion to include revision guidance, so that repair subagents know what must change.
12. As a PDF workflow owner, I want the reviewer to remain read-only, so that repair work stays separate from final judgment.
13. As a PDF workflow owner, I want repair subagents to act after acceptance failure, so that the workflow can recover without the reviewer mutating artifacts.
14. As a PDF workflow owner, I want acceptance to rerun after repair, so that the revised artifact receives a fresh independent judgment.
15. As a PDF workflow owner, I want old reports to become stale when in-scope artifacts change, so that previous passes cannot be reused after edits.
16. As a PDF workflow owner, I want artifact fingerprints in acceptance reports, so that stale reports can be detected mechanically.
17. As a PDF workflow owner, I want the acceptance criteria template to live with project documentation, so that future agents can find and reuse it.
18. As a PDF workflow owner, I want Bilibili and YouTube render workflows to use the same final acceptance contract, so that final delivery quality is consistent across sources.
19. As a PDF workflow owner, I want the acceptance review area to sit alongside other review artifacts, so that final delivery evidence is preserved with each video output.
20. As a reader, I want the body text to avoid meta-writing about prompts, generation choices, review strategy, or rewrite reasons, so that the PDF reads like a finished article.
21. As a reader, I want writing caveats to appear only when they serve the topic or are placed unobtrusively, so that process notes do not interrupt the main reading flow.
22. As a reader, I want diagrams to be visually complete, so that arrows, boxes, labels, and callouts help explanation rather than look like a draft.
23. As a reader, I want figure text to stay inside containers, so that every visual can be read without guessing missing words.
24. As a reader, I want arrows and labels to avoid collisions, so that relationships in diagrams are immediately clear.
25. As a reader, I want generated figures to look professionally aligned, so that the PDF feels credible.
26. As a reader, I want tables to fit within page boundaries, so that no column content disappears off the page.
27. As a reader, I want table text to wrap or be resized correctly, so that dense source evidence remains readable.
28. As a reader, I want table captions to match table content, so that the table's role is clear.
29. As a reader, I want ASR, OCR, subtitle, and source limitation notes to avoid large body interruptions, so that the article stays focused.
30. As a reader, I want necessary credibility caveats in footnotes, captions, appendices, or source notes, so that trust information is available without derailing the section.
31. As an Acceptance Reviewer, I want an explicit criteria file, so that I can evaluate against the user's standards rather than my own preferences.
32. As an Acceptance Reviewer, I want a fixed review context policy, so that I know generation process material is forbidden.
33. As an Acceptance Reviewer, I want every criterion to define rule text, violation patterns, allowed exceptions, pass condition, and fail condition, so that the review is executable.
34. As an Acceptance Reviewer, I want style criteria to require full text scans, so that a pass is based on the whole final artifact.
35. As an Acceptance Reviewer, I want visual criteria to require rendered PDF inspection, so that source-level assumptions do not hide visual failures.
36. As an Acceptance Reviewer, I want to produce one result per criterion, so that the report can show complete coverage.
37. As an Acceptance Reviewer, I want to record one visual scan result per rendered page, so that full page coverage can be verified.
38. As an Acceptance Reviewer, I want report validation to reject missing page entries, so that a claimed full scan cannot silently skip pages.
39. As an Acceptance Reviewer, I want report validation to reject pass reports with failed criteria, so that the machine decision remains coherent.
40. As an Acceptance Reviewer, I want report validation to reject reports that used forbidden context, so that independence is enforceable.
41. As a repair subagent, I want failed criteria and evidence locations, so that I can target the actual delivery defects.
42. As a repair subagent, I want revision guidance for each failure, so that I can fix the artifact without guessing reviewer intent.
43. As a repair subagent, I want acceptance failures separated from source-faithfulness audits, so that final delivery defects can be repaired quickly.
44. As a workflow coordinator, I want a clean loop of render, accept, repair, rerender, and accept again, so that failures do not create ambiguous handoffs.
45. As a workflow coordinator, I want upstream gate evidence refreshed when repairs change upstream artifacts, so that all review evidence remains current.
46. As a workflow coordinator, I want final delivery blocked without a fresh passing Acceptance Report, so that no PDF ships on stale or missing acceptance evidence.
47. As a future agent, I want the acceptance gate documented in project instructions, so that I do not skip it when running existing render skills.
48. As a future agent, I want Bilibili and YouTube skills to list the exact final acceptance expectation, so that long-running workflows have the same done definition.
49. As a future agent, I want the accepted categories kept small in version one, so that the gate focuses on delivery quality rather than broad source auditing.
50. As a future agent, I want the acceptance contract separated from Pyramid Gate, so that structure review and final delivery quality review remain distinct.
51. As a future agent, I want examples of accepted criteria, so that user-defined standards can be written consistently.
52. As a future agent, I want acceptance validation to fail closed, so that missing or malformed reports cannot be treated as approval.

## Implementation Decisions

- The Final Delivery Acceptance Gate will be a post-render, pre-delivery hard gate.
- The gate applies after a final PDF exists. It judges final delivery artifacts, not drafts.
- The gate uses the project Acceptance Criteria File contract as the input standard.
- The gate produces an Acceptance Report JSON as the only machine-readable pass or fail decision.
- The Acceptance Reviewer is a separate read-only subagent.
- The Acceptance Reviewer can read only final delivered artifacts and the Acceptance Criteria File.
- Generation process records, chat history, writer notes, intermediate drafts, and repair discussion are forbidden acceptance context.
- The Acceptance Reviewer may write the Acceptance Report and an optional human summary.
- The Acceptance Reviewer must not modify final artifacts, source materials, criteria files, generation records, or intermediate drafts.
- The first-version criteria categories are `style`, `figure_visual_integrity`, `table_layout_integrity`, and `credibility_disclosure_placement`.
- Source-faithfulness, subtitle coverage, image provenance, and timestamp coverage are outside the first-version acceptance category set.
- The criteria file contains only delivery-blocking criteria.
- The criteria file does not include severity levels.
- The reviewer must evaluate every criterion and report all failures.
- Each criterion result must include pass or fail status, artifact-grounded evidence, and relevant artifact locations.
- Each failed criterion must include revision guidance.
- Style criteria require full final text scans.
- Figure, table, and credibility disclosure placement criteria require rendered PDF visual review.
- Visual acceptance requires a full rendered PDF visual scan by an independent Codex visual reviewer.
- The visual scan has no human manual review stage.
- The visual scan report must include one result entry for every rendered PDF page.
- The visual scan page count must match the rendered PDF page count.
- The Acceptance Report must bind to artifact fingerprints for all in-scope final artifacts.
- Any in-scope final artifact change makes the previous Acceptance Report stale.
- The Bilibili render workflow must add the Final Delivery Acceptance Gate to its delivery path.
- The YouTube render workflow must add the Final Delivery Acceptance Gate to its delivery path.
- Project-level agent instructions must add the Acceptance Reviewer as a required final role for video-to-PDF delivery.
- The final checklist for render workflows must require a fresh passing Acceptance Report before delivery.
- If acceptance fails, the coordinator must spawn repair subagents rather than allowing the Acceptance Reviewer to edit artifacts.
- Repair subagents use the failed Acceptance Report as the repair brief.
- After repair, affected artifacts are regenerated or recompiled.
- After repair, stale upstream evidence must be refreshed when artifact changes invalidate it.
- After repair, a new Acceptance Reviewer subagent runs from a clean final-artifacts-only context.
- The workflow repeats repair and acceptance until the Acceptance Report passes or the user stops the workflow.
- Acceptance evidence belongs in the video output review area alongside existing review evidence.
- The project acceptance criteria template remains the default version-one standard.
- The validator must fail closed when criteria or report JSON is malformed.
- The validator must fail closed when required criteria are missing results.
- The validator must fail closed when visual page coverage is incomplete.
- The validator must fail closed when an Acceptance Report claims generation process context was used.
- The validator must fail closed when artifact fingerprints are stale.

## Testing Decisions

- The highest test seam is the final acceptance gate at the video output directory level. It should be tested through criteria input, final artifacts, acceptance report output, exit status, and delivery-blocking behavior.
- The acceptance criteria validator should test valid criteria files, malformed JSON, unknown categories, missing required fields, empty criteria lists, and accidental severity fields.
- The acceptance report validator should test valid pass reports, valid fail reports, missing criterion results, incomplete failed criteria, missing revision guidance, malformed artifact evidence, and stale fingerprints.
- The report validator should test that every configured criterion has exactly one result or a clearly rejected duplicate/missing state.
- The report validator should test that overall pass fails when any criterion result fails.
- The report validator should test that `failed_criteria` matches failed criterion results.
- The report validator should test that `revision_required` matches overall status.
- The review context check should test that reports using generation process context fail validation.
- The fingerprint check should test unchanged artifacts, changed text artifacts, changed PDF artifacts, missing artifacts, and renamed artifacts.
- The visual scan coverage check should test reports where page count matches pages checked.
- The visual scan coverage check should reject reports with missing pages, duplicate pages, page numbers outside range, and page counts that disagree with the rendered PDF.
- The rendered PDF seam should be tested with small fixture PDFs containing a clean page, a clipped table page, and a visually broken figure page.
- The visual scan subagent prompt should be tested through observable report shape and failure evidence, not by inspecting internal reasoning.
- The Bilibili render skill should be tested by checking that its documented delivery flow requires final acceptance after rendering and before delivery.
- The YouTube render skill should be tested by checking the same final acceptance requirement.
- Project instructions should be tested by checking that Acceptance Reviewer is a required final role and stays separate from repair subagents.
- The repair loop should be tested at workflow level: a failed Acceptance Report blocks delivery, repair changes the artifact, old report becomes stale, and a fresh passing report allows delivery.
- Existing Pyramid Gate tests remain separate. A Pyramid pass must not imply final acceptance pass.
- Existing PDF layout blank-space checks remain useful, but they do not replace full rendered PDF visual scan acceptance.
- A good test checks external workflow promises: no fresh passing Acceptance Report means no delivery; a malformed report blocks; a stale report blocks; a failed report triggers repair; a fresh pass allows delivery.

## Out of Scope

- Splitting this PRD into issues.
- Implementing the changes in this PRD.
- Adding advisory, nice-to-have, or scoring-only acceptance checks.
- Adding a total quality score.
- Allowing acceptance waivers in the first version.
- Allowing the Acceptance Reviewer to read generation process records.
- Allowing the Acceptance Reviewer to repair final artifacts.
- Replacing the existing Pyramid Gate.
- Replacing the existing independent content review against subtitles.
- Expanding first-version acceptance into source-faithfulness or subtitle coverage auditing.
- Adding human manual visual review.
- Building a general UI for editing acceptance criteria.
- Making the acceptance standard configurable per user profile beyond the JSON file.
- Running a full Bilibili or YouTube PDF generation as part of PRD creation.

## Further Notes

This PRD deliberately separates three kinds of review that can otherwise blur together. Pyramid Gate checks document structure before expensive downstream work continues. Independent content review checks whether important source content was omitted or misrepresented. Final Delivery Acceptance checks whether the final PDF is acceptable as a reader-facing deliverable under the user's hard standards.

The most important operational rule is context isolation. The Acceptance Reviewer must judge the final artifact as delivered, using the criteria file as the only standard. Repair may use broader workflow context when needed, but repair and acceptance must remain different roles.

The second important rule is evidence freshness. Acceptance Reports are useful only when they bind to the exact final artifacts they reviewed. Any repair that changes the PDF, TeX, or other in-scope final artifact must invalidate the old report and trigger a new acceptance run.

The third important rule is full visual coverage. The user's example failures are page-level rendered defects: clipped tables, draft-like diagrams, body caveats interrupting reading flow, and mismatched visual hierarchy. A text-only or source-only check cannot reliably catch these defects, so full rendered PDF visual scan is part of the hard gate.

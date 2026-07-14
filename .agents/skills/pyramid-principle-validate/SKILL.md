---
name: pyramid-principle-validate
description: Validate whether generated PDFs, documents, outlines, section drafts, or long-form writing artifacts follow the Pyramid Principle. Use this skill whenever a workflow mentions Pyramid Principle, pyramid structure, conclusion-first writing, MECE grouping, structured document review, or mandatory writing-quality hooks. In video-to-PDF workflows, this skill is a required gate after the outline contract, after each section draft, and after the integrated main document before PDF compilation.
---

# Pyramid Principle Validate

Use this skill to run a mandatory Pyramid Principle quality gate on outlines, section drafts, integrated TeX documents, Markdown reports, or other long-form writing artifacts.

The goal is structural validation against the Pyramid Principle Text Standard. This skill checks whether the artifact gives the reader a clear top-level claim, groups supporting ideas coherently, and makes each lower-level part support the level above it.

## Scope

This skill validates writing structure only.

It checks:

- conclusion-first clarity
- hierarchy between parent claims and child support
- MECE-style grouping at the same level
- teaching progression from motivation to mechanism, example, evidence, and takeaway
- alignment between titles and body content

It does not check:

- subtitle factual coverage
- figure selection quality
- LaTeX compilation
- PDF blank-page layout
- source acquisition

Those checks belong to the render skill, consistency agent, independent review agent, figure agents, and PDF layout tooling.

## Workflow Checkpoints

This skill is a general text evaluator. Calling workflows own their checkpoint names and supply them as `context_label` values.

For video-to-PDF workflows, `bilibili-render-pdf` and `youtube-render-pdf` supply Teaching-PDF context when they call this evaluator. Those workflows normally run the Pyramid Gate at three checkpoints:

1. Outline checkpoint: after `outline_contract.md` exists and before writer agents start.
2. Section checkpoint: after each `section_*.tex` draft exists and before integration.
3. Main checkpoint: after `main.tex` is integrated and before PDF compilation.

Each checkpoint must leave a report under:

```text
<video-name>/review/pyramid/
```

Use these report names unless the workflow has a specific reason to add more:

```text
outline.pyramid.json
section_01.pyramid.json
section_02.pyramid.json
main.pyramid.json
summary.md
```

Temporary reasoning, failed reports, and scratch comparisons belong under `<video-name>/待删除/`.

## Review Standard

Apply the Pyramid Principle Text Standard. The standard checks whether written text presents its central claim early, organizes supporting ideas into a clear hierarchy, keeps same-level groups meaningfully distinct, progresses coherently for the intended reader, and makes each title match the body below it.

When the caller is a video workflow, the caller supplies Teaching-PDF context through `context_label` and `audit.evaluation_context`. In that context, the artifact should help the reader learn through a clear hierarchy:

- The top of a document or chapter states the main point or teaching goal before details.
- Each major section has a parent claim that its subsections actually support.
- Same-level groups are distinct enough that the reader understands why the material is divided this way.
- The explanation progresses naturally through motivation, core idea, mechanism, example, figure, formula, code, and takeaway when those elements are available.
- A title's promise is fulfilled by the body text below it.

Treat IELTS, language-learning question banks, interview notes, case catalogs, and dialogue-heavy material carefully. They may need looser pyramid structure to preserve source value. Waiver authority belongs to the wrapper or workflow after explicit user approval.

## Scoring

Score each dimension from `0.0` to `1.0`:

- `top_down_clarity`: weight `0.25`
- `support_hierarchy`: weight `0.25`
- `grouping_mece`: weight `0.20`
- `teaching_progression`: weight `0.20`
- `title_body_alignment`: weight `0.10`

Compute:

```text
score = 0.25 * top_down_clarity
      + 0.25 * support_hierarchy
      + 0.20 * grouping_mece
      + 0.20 * teaching_progression
      + 0.10 * title_body_alignment
```

Status mapping:

- `pass`: `score >= 0.80`
- `needs_revision`: `0.60 <= score < 0.80`
- `blocked`: `score < 0.60`

The semantic result status is only `pass`, `needs_revision`, or `blocked`. Waiver state is recorded separately in `waiver` metadata that is owned by the wrapper or calling workflow.

Severe structural failures force at least `needs_revision`, even when the numeric score is higher:

- no center claim, only stacked summaries
- title and body clearly mismatch
- chapter order reads like raw subtitle slicing
- parent claims lack child support
- same-level groups overlap so heavily that the division is confusing

## Report Format

Write one JSON report for each checkpoint. Follow `references/report-schema.json`.

The report must include:

- `target`: reviewed file or artifact
- `artifact_type`: general artifact category, such as `outline_contract`, `tex_section`, `tex_document`, `markdown`, or `plain_text`
- `context_label`: caller-supplied label, such as `outline`, `section_01`, or `main`
- `status`: `pass`, `needs_revision`, or `blocked`
- `score`: numeric score from `0.0` to `1.0`
- `dimensions`: five scored dimensions
- `findings`: concrete issues and recommendations
- `required_revisions`: changes required before the workflow may continue
- `waiver`: explicit waiver metadata with `state`, `approved_by`, `reason`, and `approved_at`
- `audit`: standard name, backend, prompt version, input hash, input size, maximum input size, large-input approval state, evaluation context, and generation time

The audit standard name must be `Pyramid Principle Text Standard`. For this version, the backend is `codex-exec`, matching the project ADR. The input hash is a SHA-256 fingerprint of the reviewed text so downstream gates can detect stale reports after the input changes.

## Evaluator Command

Use the wrapper command to create a report from one UTF-8 text artifact:

```powershell
python .agents\skills\pyramid-principle-validate\scripts\evaluate_pyramid_text.py `
  "<input-artifact>" `
  "<output-report>.pyramid.json" `
  --artifact-type "markdown" `
  --context-label "outline" `
  --evaluation-context "Teaching-PDF outline checkpoint supplied by the calling workflow."
```

The wrapper reads the input artifact, computes its SHA-256 fingerprint and character size, runs a constrained `codex exec` semantic evaluation, validates the generated Pyramid Gate Report, and writes the final JSON report itself.
The nested Codex command uses a read-only sandbox, `approval_policy="never"`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, and a schema-constrained final response so the evaluator stays a narrow semantic review subprocess.

Inputs above `160000` characters fail by default. Continue only after explicit approval and record that approval with:

```powershell
--allow-large-input
```

The nested evaluator returns only judgment fields: `status`, `score`, `dimensions`, `findings`, and `required_revisions`. The wrapper owns `target`, `artifact_type`, `context_label`, `waiver`, and `audit` metadata.

If the workflow owner explicitly approves continuation despite `needs_revision` or `blocked`, record that approval through wrapper-owned waiver flags:

```powershell
--waiver-approved-by "<approver>" `
--waiver-reason "<reason>" `
--waiver-approved-at "2026-06-30T10:00:00Z"
```

`--waiver-approved-at` is optional and defaults to the report generation time. `--waiver-approved-by` and `--waiver-reason` are required together for any waiver. A waiver preserves the original semantic `status`, findings, required revisions, and input fingerprint; there is no `status=waived`.

After writing a report, immediately run the synchronous hook validation:

```powershell
python .agents\skills\pyramid-principle-validate\scripts\validate_report.py `
  "<video-name>\review\pyramid\outline.pyramid.json" `
  --enforce-gate
```

When the reviewed source file is available to the gate, pass it as `--input-file` so the validator recomputes `audit.input_sha256` and `audit.input_size_chars`:

```powershell
python .agents\skills\pyramid-principle-validate\scripts\validate_report.py `
  "<video-name>\review\pyramid\outline.pyramid.json" `
  --input-file "<video-name>\outline_contract.md" `
  --enforce-gate
```

When a report already contains a human-approved waiver and continuation is explicitly allowed, add `--allow-waiver` to enforcement:

```powershell
python .agents\skills\pyramid-principle-validate\scripts\validate_report.py `
  "<video-name>\review\pyramid\outline.pyramid.json" `
  --input-file "<video-name>\outline_contract.md" `
  --enforce-gate `
  --allow-waiver
```

Validator exit codes are part of the hook contract:

- `0`: valid continuation, either `pass` or approved waiver under `--allow-waiver`
- `1`: validation failure, such as malformed JSON, schema drift, stale fingerprint, or invalid score
- `2`: gate-blocking `needs_revision` or `blocked` status under `--enforce-gate`
- `3`: malformed waiver metadata, such as missing approver, missing reason, or an invalid waiver timestamp

Before the workflow accepts a whole output directory as complete, run the output-level hook:

```powershell
python .agents\skills\pyramid-principle-validate\scripts\check_output_gate.py `
  "<video-name>" `
  --enforce-gate
```

The output-level hook validates the Pyramid Review Directory against deterministic source paths in the video output directory:

- `review/pyramid/outline.pyramid.json` validates against `outline_contract.md`
- every root-level `section_*.tex` requires `review/pyramid/section_*.pyramid.json`
- `review/pyramid/main.pyramid.json` validates against `main.tex`

Each JSON report is revalidated through `validate_report.py` with the matching source file, so schema errors, gate failures, malformed waiver metadata, and stale `audit.input_sha256` or `audit.input_size_chars` all block output-level success. Existing `section_*.pyramid.json` reports are also checked against their matching `section_*.tex` source, which prevents orphaned section evidence from looking current.
The hook also checks checkpoint identity metadata: outline reports must use `artifact_type=outline_contract` and `context_label=outline`, section reports must use `artifact_type=tex_section` and the matching section stem as `context_label`, and the main report must use `artifact_type=tex_document` and `context_label=main`.
The output-level CLI preserves the validator exit classes: gate-blocked reports return `2`, malformed waiver metadata returns `3`, and other validation failures return `1`.

If the workflow owner explicitly approves continuation under recorded waiver metadata, pass the output-level waiver flag:

```powershell
python .agents\skills\pyramid-principle-validate\scripts\check_output_gate.py `
  "<video-name>" `
  --enforce-gate `
  --allow-waivers
```

The workflow may continue only when the relevant validation command exits successfully. `needs_revision` and `blocked` stop the next workflow step under `--enforce-gate`. Any continuation under waiver is a wrapper or workflow decision and must be recorded in explicit waiver metadata.

## Human Summary

The output-level hook writes `<video-name>/review/pyramid/summary.md` after all required JSON reports validate. The summary is a concise audit trail that records:

- checkpoint label
- report filename
- status and score
- required revisions
- waiver reason

The summary is for humans. JSON reports remain the machine decision source.

## Output Discipline

Be concrete. A useful finding names the location, explains the structural failure, and gives an actionable fix.

The evaluator wrapper stores scratch semantic output and candidate reports under the artifact output root's `待删除\pyramid-evaluator` directory. For video workflows that write reports under `<video-name>\review\pyramid\*.pyramid.json`, scratch belongs under `<video-name>\待删除\pyramid-evaluator`.

Weak finding:

```text
The structure could be clearer.
```

Useful finding:

```text
section 3: The title promises the mechanism of speculative decoding, but the first two subsections only list examples. Add one mechanism-level parent claim before the examples, then group the examples under that claim.
```

Do not approve an artifact just because it is fluent. Fluency without hierarchy still fails this gate.

## Bundled Tools

- `references/report-schema.json`: report schema contract
- `references/evaluator-output-schema.json`: schema for the nested semantic evaluator response
- `scripts/evaluate_pyramid_text.py`: constrained Codex-backed evaluator wrapper
- `scripts/validate_report.py`: JSON report validator and synchronous hook gate
- `scripts/check_output_gate.py`: output-directory hook that requires outline, every section, and main reports, validates source fingerprints, and writes `review/pyramid/summary.md`
- `examples/pass_report.json`: minimal passing report example

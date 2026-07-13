# Separate delivery ownership from target lifecycle

`task-claim` records task ownership in `.codex/delivery-targets/task-index.json`. It does not create a session-scoped `current.json` or a video-level `review/acceptance/delivery_target.json`. The previous command shape accepted target-related arguments, which incorrectly implied that claiming a task had initialized a deliverable. The acceptance-report schema already defines report fields; the missing contract is the lifecycle that creates and advances delivery-target state for newly rendered PDFs.

## Considered Options

- Let `task-claim` initialize every target: rejected because an ownership claim and a delivery lifecycle transition have different preconditions, artifacts, and failure handling.
- Reuse `old-pdf-prepare` for new renders: rejected because it models legacy PDFs and relaxes guarded compile-provenance requirements.
- Infer the TeX file, final PDF, source skill, or Delivery Glossary by scanning the output directory: rejected because a guessed binding can approve or repair the wrong artifact.
- Preserve the old `task-claim` arguments through a compatibility layer: rejected. Repository users control versions manually, so the command contract may change directly.

## Decision

`task-claim` has ownership-only semantics. Its CLI accepts only the official Codex `session_id` and `video_output_dir`. It may update the task index for ownership and observability, but it must not create target files or promise that a delivery lifecycle has started. `--target-file` and `--stage` are removed without a compatibility path.

New renders use explicit lifecycle commands:

- `render-prepare` requires the official `session_id`, `video_output_dir`, relative `main_tex`, relative `final_pdf`, and `source_skill` (`youtube-render-pdf` or `bilibili-render-pdf`). It creates the session target, task-index entry, and video-level target at `generating`.
- `delivery-ready` is the only pre-acceptance preparation transition. It validates the declared artifacts, guarded final compile provenance, and fingerprints; renders every final PDF page; refreshes the allowed-artifacts manifest and acceptance-report skeleton; then advances the three target records to `ready_for_delivery`.
- `delivery-accept` runs after a read-only Acceptance Reviewer reports a passing decision. It revalidates the report, manifest, rendered-page coverage, and fingerprints, then advances all three records to `accepted`.

Declared artifact paths are inside the video output directory and may be predeclared before those files exist. They cannot be guessed, scanned, or silently rebound later. A repeated `render-prepare` with the same session, output directory, paths, and source skill resumes safely. A binding mismatch blocks the operation until an explicit handoff or a new session is used. A repeated `delivery-ready` refreshes derived evidence; changed artifacts invalidate prior acceptance evidence.

The session `current.json` is the commit marker for every multi-file target update. The implementation validates and writes the video-level target first, then the task index, and writes `current.json` last. State is active only when all records are consistent and the session marker exists.

For a non-English teaching PDF, `delivery-ready` requires an explicit `--include-delivery-glossary` declaration. Its absence is an error; the command must not infer or omit the glossary silently.

Acceptance reports use a conditional revision-guidance contract: a passing criterion has `revision_guidance: null`; a failing criterion has a non-empty `revision_guidance` object. The skeleton generator, schema validator, Acceptance Reviewer prompt, and tests enforce this rule.

After failed acceptance attempts one and two, the lifecycle returns from `ready_for_delivery` to `generating` through `record-failed-attempt`, preserves attempt evidence, and requires repair, guarded recompilation, and a fresh `delivery-ready` run. The third failed attempt writes a manual repair brief and sets the target to `blocked`. `old-pdf-prepare` remains a separate legacy-PDF command.

`delivery_guard.py check` remains the final mechanical proof immediately before delivery. It does not replace the Acceptance Reviewer decision and does not make lifecycle state decisions.

## Consequences

The command boundaries make incomplete state observable: a task-index claim alone cannot be mistaken for an initialized delivery target. New-render and legacy-PDF flows have separate provenance contracts. Every transition has explicit declared artifact identity, reducing recovery ambiguity after a failed attempt or interrupted session.

Implementation must update the command parser, target-writing helpers, schemas, skeleton generation, reviewer instructions, and tests as one contract. Documentation must describe the direct command replacement. No backward-compatible aliases, deprecated arguments, or automatic migration behavior are required.

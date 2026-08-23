# Enable Run-record-free Legacy Final Compile

## Context

ADR 0064 activated Acceptance Report v2 for Kernel and Legacy inputs. The
Legacy adoption path remained unable to produce the required modern final
quality evidence for a newly compiled PDF because Guarded Final Compile always
searched for `workflow/run.json` and required Kernel Diagnostic Compile
authority. Creating that Run Record would violate the Legacy input contract.

The compile adapter, Precompile Text Seal, exact input closure, Runtime Policy,
Final Artifact Seal, rendered-page evidence, and text-origin contracts are
track-independent. The missing boundary is compile admission and provenance
dispatch.

## Decision

Use the existing `delivery-quality-final-compile` command for both input tracks
and require an explicit `--input-track` selection.

- `kernel` retains the real Run Record and current Content Production
  Diagnostic Compile authority.
- `legacy` requires an explicit `--video-root`, current Global Gate authority,
  and the absence of the canonical `<video-root>/workflow/run.json`.
- Every Legacy precompile input, source entry, Runtime Policy, plan, manifest,
  and output workspace remains inside the named video root.
- Both tracks use the same registered current-HEAD compile adapter and produce
  the same target-only `final-compile-report/1.0.0` evidence. The report grants
  no delivery authority.
- Legacy compile admission rechecks the Global Gate before publishing the
  report, reruns the complete Global Gate policy-currentness check, and
  rechecks that the canonical Run Record remains absent. Authority, policy, or
  input-track drift during compilation leaves attempt artifacts and withholds
  the report.
- `legacy-acceptance-adopt` accepts a closed provenance set: historical
  `latex_compile_report.v1` for already compiled directories and relationally
  current `final-compile-report/1.0.0` for newly guarded compiles.
- Delivery Guard dispatches compile provenance by the supported report schema.
  Input-track authority remains defined by the Acceptance input set and never
  by provenance format.

No Legacy Run ID, revision, checkpoint, coordination state, or Kernel Final
Evidence is synthesized. Acceptance Report v2 remains the sole semantic
delivery decision.

## Consequences

New Legacy PDFs can traverse Precompile Text Seal, Guarded Final Compile,
rendered-text reconciliation, Legacy adoption, Acceptance Report v2, and
Delivery Guard without acquiring Kernel lifecycle authority. Existing Legacy
compile reports remain valid for their historical directories.

The public seam adds authority-root, synthetic-Run, path-escape, relational
provenance, adapter-currentness, recorder-closure, and schema-dispatch tests.
The capability becomes active only after a governed Global Gate policy
authority refresh publishes a current Exit Evidence Manifest. That refresh
preserves the stable Global Gate delivery-authority binding used by platform
and Batch authorities. Platform Kernel authority and track migration status
remain unchanged.

The refresh is published through
`global-gate-policy-authority-refresh`. It advances the independent
`active_global_gate_policy.json` generation with a SQLite compare-and-swap and
leaves `active_global_gate.json` byte-for-byte stable. Policy checks prefer the
committed policy authority when present and retain the original activation
evidence as the compatibility authority before the first refresh. An
interrupted refresh is completed only through `global-gate-reconcile` after
the prepared evidence and stable base authority are revalidated.

## Validator fixture migration impact

The ordered fail-closed gates changed in three validator families:

- `tests.video_workflow.test_issue41_legacy_final_compile` owns the public
  Legacy Final Compile positive builder and isolated authority-root,
  synthetic-Run, compile-time Run creation, and workspace-containment
  contradictions. Every negative scenario declares `single_contradiction` and
  asserts the first public error code.
- `tests.video_workflow.test_issue43_global_gate` owns the modern Legacy
  adoption lifecycle builder. Mutations to main TeX, adapter, recorder,
  runtime closure, or schema rematerialize every dependent seal,
  reconciliation, quality-manifest, and report binding except the one target
  contradiction.
- `tests.video_workflow.test_issue43_active_guard` owns the complete public
  `delivery_guard.py check` seam for modern Legacy provenance. Private helper
  calls remain diagnostic support and do not qualify the behavior.

The focused suites and their complete affected modules must finish with
terminal persisted-command evidence before the refreshed Exit Evidence
Manifest is published. The manifest, rather than this ADR, records the exact
run directories, commands, exit codes, logs, implementation commit, and
artifact fingerprints.

This decision amends ADR 0064's Legacy operational path and preserves ADR
0062's shared final-evidence contracts.

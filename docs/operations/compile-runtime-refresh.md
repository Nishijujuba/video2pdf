# Compile Runtime Refresh

`compile-runtime-refresh` owns resumable recovery when a retained Kernel Run's
Compile Runtime Policy has drifted. Its active authority is
`<run-dir>/workflow/runtime-refresh-active.json`. The operation directory below
`<run-dir>/待删除/runtime-refresh/<operation-id>/` retains predecessor evidence
and the earlier operation journal for audit; the active journal remains the sole
recovery authority.

The commands below are target commands. Launch them through
`scripts/persisted_command.py` as described in
[Persisted Command Runner](persisted-command-runner.md), retain the returned
`data.run_dir`, and verify terminal status plus `exit-code.txt` before using the
result as evidence.

When the active journal reaches `precompile_refresh_required`, Final Compile
remains blocked. A content repair may enter Production only through
`delivery-quality-precompile-repair-promote` with both exact handoff arguments:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B scripts\video_workflow.py delivery-quality-precompile-repair-promote `
  --run-dir "<run-dir>" `
  --repair-bundle "<bundle.json>" `
  --predecessor-workspace-root "<failed-precompile-workspace>" `
  --workspace-root "<repair-workspace>" `
  --inventory "<candidate-reader-facing-text-inventory.json>" `
  --semantic-dependencies "<candidate-semantic-dependencies.json>" `
  --repair-attempt-number <1..3> `
  --prepared-at "<timestamp>" `
  --runtime-refresh-operation-id "<operation-id>" `
  --runtime-predecessor-final-compile-manifest "<predecessor-final-compile-manifest.json>" `
  --runtime-content-repair-disposition "<approved-disposition.json>" `
  --runtime-predecessor-contract-gap-brief "<failed-workspace/precompile-contract-gap-brief.json>"
```

The operation id must equal the active `precompile_refresh_required` journal.
The predecessor manifest must carry that journal's predecessor Runtime Policy
identity. The repair bundle's Run-relative
`payload/compile-runtime-policy.json`, the canonical Runtime Policy, and the
journal's successor policy must identify the same bytes. These checks finish
before Production supersedes a task or materializes an Attempt.

When an earlier promotion reached `promotion_ready` and its fresh Precompile
workspace produced a Contract Gap brief, retry requires the two disposition
arguments shown above. The disposition binds the published Issue #105 approval
comment, approval time, gap id, runtime operation, immutable repair bundle,
current generation-set fingerprint, and Runtime Policy. Its
`contract_gap_brief_sha256` is the brief JSON's validated internal
`brief_sha256`; it is distinct from the file byte SHA. The disposition grants
the provider authority to rederive corrected inputs and does not grant a
semantic pass.

An exact replay of the already recorded ordinary promotion workspace keeps the
existing public command shape and does not require a disposition. After it
validates the recorded bundle, predecessor, repair Attempt, current Production
and Diagnostic Compile authority, the predecessor candidate inventory and
dependencies, and the separately published output bindings, the provider returns
the existing promotion without resuming Production or deriving a new output
root. A different workspace is a successor refresh and therefore requires the
exact disposition.

The provider validates the disposition and the failed workspace before creating
the successor promotion directory. It requires every Production replacement
from the original bundle to be committed already, leaves Production State and
Compile Manifest bytes unchanged, and derives the successor generations from
the original failed predecessor workspace. The new promotion therefore retains
the exact generation-set identity while its disposition identity selects a new
immutable output root. The active handoff retains the earlier promotion binding
and records exactly one successor; replay with the same inputs preserves its
bytes, while a stale disposition, generation drift, or competing workspace is
rejected before the provider creates derived output or a Precompile workspace.

Pyramid review binds the target's content identity: logical id, path, SHA, and
evaluation context. When later provider-owned integration retains that identity
and advances only the target generation, promotion copies the reviewed binding,
updates only its generation to the current envelope, and validates the current
target file before writing the Attempt. The repair bundle remains immutable.
Changes to the target SHA, path, logical id, or evaluation context remain stale
review evidence and block promotion.

Successor semantic inputs are derived from current Production Figure bindings.
Authoritative raster text takes its caption from the current Figure Manifest,
after the provider verifies the bound image and manifest bytes. Visual source
provenance is rebuilt under the new promotion output with current Figure asset
and manifest generations while retaining the validated source document
identities and unchanged Figure source timestamp. The predecessor provenance
file remains unchanged, and the successor dependency projection points to the
new derived provenance.

Successful promotion records the provider-derived Artifact Generations,
Reader-Facing Text Inventory, semantic dependencies, and repair workspace in the
active handoff. Final Compile remains blocked while fresh isolated judgments and
a passing Precompile Text Seal are absent.

`delivery-quality-seal` completes the handoff after publishing a passing fresh
Seal. For recovery testing, the public fault point stops after the Seal write and
before runtime supersession:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B scripts\video_workflow.py delivery-quality-seal --workspace-root "<repair-workspace>" --sealed-at "<timestamp>" --fault-point after_seal_before_runtime_supersession
```

At that fault boundary, the active runtime state remains
`precompile_refresh_required`, so Final Compile stays blocked. Replay the same
Seal command without `--fault-point`; the provider validates the existing Seal,
derives the successor Final Compile Manifest from the new generations and
current Runtime Policy, and records `superseded_by_content_repair` in the active
journal.

The superseded state grants Final Compile admission only while the matching
handoff fingerprint, passing Seal, generation set, successor manifest,
Diagnostic Compile Report, and Runtime Policy remain current. The old runtime
operation journal and predecessor evidence remain unchanged. Replays must use
the original runtime refresh timestamp, the promoted repair workspace, and the
recorded predecessor Final Compile Manifest.

## Validator fixture migration impact

- Positive fixture: the focused Seal fault fixture now starts from a coherent
  promoted generation set, fails after Seal publication, then proves that replay
  writes one current successor manifest and remains idempotent.
- Derived-input fixtures: focused caption and visual-provenance cases cover the
  current Figure Manifest caption, current nested Figure generations, immutable
  predecessor evidence, and the successor projection path.
- Negative fixture: generation-file drift is introduced after promotion while
  the recorded file binding remains fixed. Its first failing gate is
  `content_repair_generation_file_binding`, with error code
  `runtime_refresh_handoff_generation_file_drift`; the active journal remains
  pending and no successor manifest is written.
- Disposition fixtures: the positive case records one successor and exact replay;
  negative cases isolate an absent approval, a stale approval URL, changed
  generations, and a competing successor. Each refusal leaves the active journal
  byte-identical. The successful refresh also proves Final Compile remains
  blocked until a fresh passing Seal supersedes the runtime operation.
- Precedence fixture: the earlier forged supersession fixture combined missing
  Seal, manifest, and generation evidence, so it was removed instead of being
  treated as single-contradiction coverage.
- Affected modules: Compile Runtime refresh handoff validation and the focused
  Issue #105 public-boundary fixtures, plus Pyramid replacement Attempt
  materialization. Repository search found no earlier generation-only stale or
  `precompile_repair_pyramid_evaluation_stale` fixture to migrate. No shared
  golden data or cache requires rematerialization.
- Verification boundary: only the new Issue #105 focused contract methods were
  run. The complete acceptance suite was not run under the user's explicit test
  constraint.

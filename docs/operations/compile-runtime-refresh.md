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
  --runtime-predecessor-final-compile-manifest "<predecessor-final-compile-manifest.json>"
```

The operation id must equal the active `precompile_refresh_required` journal.
The predecessor manifest must carry that journal's predecessor Runtime Policy
identity. The repair bundle's Run-relative
`payload/compile-runtime-policy.json`, the canonical Runtime Policy, and the
journal's successor policy must identify the same bytes. These checks finish
before Production supersedes a task or materializes an Attempt.

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
- Negative fixture: generation-file drift is introduced after promotion while
  the recorded file binding remains fixed. Its first failing gate is
  `content_repair_generation_file_binding`, with error code
  `runtime_refresh_handoff_generation_file_drift`; the active journal remains
  pending and no successor manifest is written.
- Precedence fixture: the earlier forged supersession fixture combined missing
  Seal, manifest, and generation evidence, so it was removed instead of being
  treated as single-contradiction coverage.
- Affected modules: Compile Runtime refresh handoff validation and the focused
  Issue #105 public-boundary fixtures. No shared golden data or cache requires
  rematerialization.
- Verification boundary: only the new Issue #105 focused contract methods were
  run. The complete acceptance suite was not run under the user's explicit test
  constraint.

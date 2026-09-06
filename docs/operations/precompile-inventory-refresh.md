# Precompile derived-inventory refresh

## Conclusion

Use `delivery-quality-precompile-inventory-refresh` when an actual retained Final
Compile failure proves that a current passing, sealed Precompile workspace has a
stale generated-text declaration while its Production artifacts and semantic
dependencies remain current. The provider derives a successor inventory,
preserves the semantic-attempt counter, and dispatches three fresh Reviewer
tasks.

This route does not authorize Production changes, reuse semantic judgments, or
edit the predecessor workspace. A semantic content change continues through the
ordinary Precompile repair-promotion route.

## Required authority

The command requires:

- the active Kernel Run directory;
- the complete current Content Production authority, whose diagnostic Compile
  Manifest has the same logical IDs, generations, and SHA-256 values as the
  sealed predecessor generation set;
- the current passing sealed Precompile predecessor and its repair-attempt
  ledger;
- the exact Final Compile Manifest bound to that Seal and the unchanged current
  Production generation set;
- one retained persisted Final Compile command with terminal failure status,
  exit code, eligible security classification, workflow-result envelope, and
  the adapter diagnostic that identifies the generated inventory item;
- the approved repair reference and a preparation timestamp.

The provider copies only the small command, status, output, exit-code, adapter
diagnostic, and Compile Manifest evidence into immutable Run-owned custody. A
Run-scoped claim binds the predecessor Seal to one successor path. Exact replay
returns the same authority after current Production and successor bindings are
revalidated; a different successor path fails closed.

## Command

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B scripts\video_workflow.py delivery-quality-precompile-inventory-refresh `
  --run-dir "<run-dir>" `
  --predecessor-workspace-root "<passing-sealed-precompile-workspace>" `
  --workspace-root "<new-successor-workspace>" `
  --compile-manifest "<failed-final-compile-manifest>" `
  --failed-command-run-dir "<persisted-command-run-dir>" `
  --approval-reference "<approved-issue-or-decision-reference>" `
  --prepared-at "<ISO-8601-timestamp>"
```

The successful result points to `precompile-inventory-refresh.json`, the new
`repair-attempt.json`, and fresh Reviewer Skeletons. The successor then follows
the ordinary patch-commit, precompile-materialize, and seal operations. Any
later semantic failure increments the retained semantic-attempt counter.

## Limits

The provider accepts only the retained generated-title absence/ambiguity
failure emitted by the guarded Final Compile adapter. Non-generated inventory
membership and content must remain exact. Provider derivation may change or
remove an existing generated declaration when current usage proves the change;
it cannot add a new item ID. The Artifact Generation set and
semantic-dependency file must remain byte-identical to the predecessor.

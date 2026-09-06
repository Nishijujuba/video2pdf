# Final Compile Workspace Selection and Reconciliation

## Select a compact workspace

For the supported Windows MiKTeX runtime, use a short, fresh workspace leaf
inside the existing video Run, for example:

```text
<video-root>/review/final-compile/f1
```

Pass this path through the existing public `--workspace-root` argument. Use a
new leaf, such as `f2`, for a new attempt; preserve earlier attempts. Keep the
provider-created `adapter-output/compiler-staging` subtree in place because
recorder and downstream quality evidence bind that layout. Keep the original
video directory and its source files in place as well. The final PDF basename
continues to come from the existing `--pdf-basename` input, independently of
the short workspace leaf.

A bounded comparison of the same complete retained document, Manifest and
Runtime Policy completed all three XeLaTeX rounds at absolute staging lengths
of 110 and 220 characters. Lengths of 234 and 237 failed in MiKTeX's PDF backend
or its Windows process launch. These observations support compact workspace
allocation for this environment; 220 is not a general platform limit or a
guarantee for other paths and documents.

If an actual compile fails with a MiKTeX internal error or Windows process
error, inspect its retained adapter logs and compiler `engine-profile` logs
before changing content. Preserve the failed operation, reconcile it only
after process continuity is established, and use a new compact workspace for
the next public attempt. Reuse only inputs that remain current under their
existing provider checks. A diagnostic PDF does not authorize reuse of a
partial Final Compile or delivery.

## Reconcile an incomplete workspace

Use the public reconciliation command when a governed Final Compile workspace
contains `final-compile-operation.json` and the compiler is no longer live, yet
no completed `final-compile-report.json` was published:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B scripts\persisted_command.py start --task-name final-compile-reconcile --cwd "D:\Project\video2pdf\newskill-kimi" -- D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B scripts\video_workflow.py delivery-quality-final-compile-reconcile --workspace-root "<interrupted-workspace>"
```

Retain the returned `data.run_dir` and observe it with the persisted runner's
`show` or `reconcile` command. A terminal `status.json` and actual
`exit-code.txt` establish command completion. A fresh actual Final Compile
also requires its provider-published passing report. Acceptance v2, every-page
visual review, a fresh Delivery Guard and delivery lifecycle completion remain
required before PDF delivery.

The provider preserves the recorded `operation_id` inside the immutable
operation evidence and derives a separate archive identity from the exact
workspace directory name. The archive destination is deterministic within the
workspace parent:

```text
<workspace-parent>/待删除/final-compile-interrupted-by-workspace/<workspace-name>
```

Two interrupted sibling workspaces may therefore share one operation identity
and still retain distinct archives. Repeating the command with the original
workspace path locates the matching archive, revalidates its operation and
execution fingerprints and completed-evidence boundary, and returns
`final_compile_interruption_already_reconciled` without rewriting the archive.
The live-process observation made before the original archive move is not
repeated, because the immutable historical PID may later identify an unrelated
process after operating-system PID reuse.

Reconciliation fails closed when the workspace cannot be matched uniquely, the
operation or execution fingerprint is invalid, process continuity is unknown,
the compiler process is still live, completed Final Compile evidence exists, or
the workspace or archive escapes the repository boundary. Existing archives in
the earlier operation-only layout remain readable historical evidence and are
never moved, rewritten, or deleted by this command.

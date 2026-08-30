# Final Compile Reconciliation

Use the public reconciliation command when a governed Final Compile workspace
contains `final-compile-operation.json` and the compiler is no longer live, yet
no completed `final-compile-report.json` was published:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 -B scripts\video_workflow.py delivery-quality-final-compile-reconcile --workspace-root "<interrupted-workspace>"
```

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

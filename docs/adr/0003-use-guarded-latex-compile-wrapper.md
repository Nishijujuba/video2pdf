# Use guarded LaTeX compile wrapper

The video-to-PDF workflow will route both temporary and final LaTeX compilation through a guarded compile wrapper instead of allowing direct `xelatex`, `pdflatex`, `lualatex`, `latexmk`, or `tectonic` shell calls. This wrapper owns path validation, timeout behavior, idle log watchdog behavior, disposable build location, and machine-readable compile provenance, while a project `PreToolUse` guard blocks commands that bypass the wrapper or use unsafe output directories.

Final compile provenance also records wrapper producer identity, wrapper contract, wrapper mode, wrapper script fingerprint, and semantic invocation arguments so `delivery_guard.py` can distinguish a guarded wrapper report from a hand-written artifact-fingerprint-only report.

Direct engine calls are convenient during manual debugging, yet they make it easy to create literal shell-variable paths such as `$build`, write outside the video output directory's `待删除` area, or leave Codex waiting inside a long-running tool call. The accepted trade-off is to preserve temporary compilation through controlled `quick` mode while requiring final delivery to prove a fresh passing final compile report before `delivery_guard.py check` can pass.

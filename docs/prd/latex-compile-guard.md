# PRD: LaTeX Compile Guard

## Problem Statement

The video-to-PDF workflow can currently reach LaTeX compilation through ad hoc shell commands. A raw engine call can hang inside a tool invocation for a long time, write into a mistaken output directory such as a literal `$build`, produce no final PDF, or leave only a zero-byte log. Once the tool call is already running, a prompt-level instruction or Stop hook cannot recover control quickly.

The project already has a Final Delivery Guard that blocks delivery without fresh acceptance evidence. That guard intentionally stays out of long-running work such as LaTeX compilation and page rendering. The missing layer is therefore a compile-time guard that prevents unsafe LaTeX calls before they start, runs all allowed compilation through a bounded wrapper, and leaves machine-readable provenance for final delivery checks.

The user needs temporary compilation to remain possible for debugging layout and TeX errors. The project also needs final compilation to become auditable enough that a generated PDF cannot bypass the guarded wrapper and still be delivered as a fresh video-to-PDF output.

## Solution

Introduce a project-local LaTeX Compile Guard with three cooperating parts.

First, add a guarded LaTeX compile wrapper. The wrapper is the only supported entry point for video-to-PDF LaTeX compilation. It exposes semantic options such as compile mode, TeX file, run count, total timeout, and idle log timeout. It does not expose arbitrary `-output-directory` control to callers. The wrapper creates its own build directory inside the current video output directory's `待删除` area, invokes the configured LaTeX engine through a structured process call, watches for total timeout and idle output, writes logs and diagnostics, and exits with clear failure information.

The wrapper supports two modes:

- `quick`: temporary compilation for debugging. It runs with a shorter default timeout, usually one compile run, and leaves all outputs under the disposable build directory. Quick reports are diagnostic only and cannot satisfy final delivery.
- `final`: final compilation for delivery. It runs with the delivery run policy, copies the final PDF and useful logs to the durable video output area, and writes the latest final compile provenance report.

Second, add a project `PreToolUse` guard for shell commands. The guard uses Codex's `PreToolUse` hook event with a `Bash` matcher. It strongly blocks direct calls to LaTeX engines, dangerous output-directory forms, and commands that attempt to bypass the wrapper. It also performs a fast read-only anomaly scan for known compile hazards such as literal `$build` directories, zero-byte compile logs, or stale LaTeX processes. The scan must stay fast and must not compile, kill processes, move files, or perform repair.

Third, extend final delivery mechanics so `delivery_guard.py check` requires a fresh passing final compile report for newly generated video PDFs. This report proves that the final PDF came from the guarded wrapper. It does not replace Final Delivery Acceptance. `acceptance_report.json` remains the only machine-readable quality decision source, and `delivery_guard_report.json` remains the final mechanical delivery proof.

### Artifact Contract

Temporary compile reports live under the disposable build run:

```text
<video-output-dir>\待删除\latex-build\<run-id>\compile_report.json
```

The latest final compile report lives at:

```text
<video-output-dir>\review\latex\compile_report.json
```

The final compile report records compile provenance and freshness data, including:

- schema version
- mode
- status
- source skill or caller
- TeX path
- final PDF path
- engine path
- run count
- timeout settings
- staging/build directory
- stdout or log paths
- started and finished timestamps
- failure reason when applicable
- final PDF fingerprint
- main TeX fingerprint

The report is a compile provenance artifact. It is not an acceptance decision, score, or reviewer judgment.

### Final Delivery Guard Integration

For newly generated video PDFs, final delivery is allowed only when `delivery_guard.py check` can verify that:

- the video target is a new video PDF target rather than an explicitly legacy external PDF target;
- `review\latex\compile_report.json` exists;
- the compile report has `mode: "final"`;
- the compile report has `status: "passed"`;
- the report's TeX path resolves to the delivery target's main TeX;
- the report's PDF path resolves to the delivery target's final PDF;
- the report's recorded final PDF fingerprint matches the current final PDF;
- the report's recorded main TeX fingerprint matches the current main TeX;
- quick-mode reports never satisfy final delivery.

Historical or externally supplied PDFs may use an explicit legacy boundary. A legacy target may skip the final compile report only when the task is to inspect, organize, or deliver an existing PDF without recompilation. If a legacy repair workflow recompiles the PDF, the regenerated PDF must produce a final compile report.

## User Stories

1. As a video-to-PDF workflow owner, I want all LaTeX compilation to go through one guarded wrapper, so that every compile has the same path, timeout, and evidence rules.
2. As a video-to-PDF workflow owner, I want direct LaTeX engine shell calls blocked, so that agents cannot accidentally bypass the wrapper.
3. As a video-to-PDF workflow owner, I want `xelatex`, `pdflatex`, `lualatex`, `latexmk`, and `tectonic` direct calls blocked, so that equivalent compile bypasses are treated consistently.
4. As a video-to-PDF workflow owner, I want dangerous output directories such as `$build`, `${build}`, `%build%`, empty output directories, project root, and plain `build` blocked, so that shell-variable mistakes cannot create misleading compile locations.
5. As a video-to-PDF workflow owner, I want temporary compilation to remain available, so that layout debugging and TeX error diagnosis stay practical.
6. As a video-to-PDF workflow owner, I want temporary compilation to use `quick` mode, so that temporary outputs cannot be confused with final delivered PDFs.
7. As a video-to-PDF workflow owner, I want final compilation to use `final` mode, so that delivered PDFs have a durable provenance report.
8. As a video-to-PDF workflow owner, I want the wrapper to create build directories under the video output directory's `待删除` area, so that disposable compile artifacts remain inside the video task boundary.
9. As a video-to-PDF workflow owner, I want callers to pass semantic wrapper options rather than raw engine output-directory flags, so that path policy remains centralized.
10. As a video-to-PDF workflow owner, I want the wrapper to invoke LaTeX through structured process arguments, so that PowerShell variable interpolation cannot silently alter the command.
11. As a video-to-PDF workflow owner, I want the wrapper to enforce a total timeout, so that a compile cannot hold the tool call for an unbounded time.
12. As a video-to-PDF workflow owner, I want the wrapper to enforce an idle log timeout, so that a stalled compile fails with diagnostics instead of waiting indefinitely.
13. As a video-to-PDF workflow owner, I want the wrapper to preserve compile logs, so that TeX failures can be debugged after the process exits.
14. As a video-to-PDF workflow owner, I want a failed compile report to include the failure reason, so that the next agent can repair the source instead of guessing.
15. As a video-to-PDF workflow owner, I want a final compile report under `review\latex`, so that compile provenance is easy to audit alongside other review evidence.
16. As a video-to-PDF workflow owner, I want quick compile reports to remain under `待删除`, so that temporary diagnostics do not look like delivery evidence.
17. As a video-to-PDF workflow owner, I want `delivery_guard.py check` to require a fresh passing final compile report for new video PDFs, so that a bypassed compile cannot be delivered.
18. As a video-to-PDF workflow owner, I want `delivery_guard.py check` to verify the final PDF fingerprint from the compile report, so that stale reports cannot approve changed PDFs.
19. As a video-to-PDF workflow owner, I want `delivery_guard.py check` to verify the main TeX fingerprint from the compile report, so that changed source cannot reuse old compile evidence.
20. As a video-to-PDF workflow owner, I want legacy PDF targets to have an explicit source boundary, so that existing PDFs can be inspected without fabricating compile provenance.
21. As a video-to-PDF workflow owner, I want legacy repairs that recompile to produce final compile reports, so that regenerated PDFs follow the same provenance rules.
22. As a video-to-PDF workflow owner, I want the Acceptance Reviewer to ignore compile provenance, so that final quality judgment remains based on final artifacts and acceptance criteria.
23. As a video-to-PDF workflow owner, I want `acceptance_report.json` to remain the only quality decision source, so that compile success is never mistaken for acceptance success.
24. As a video-to-PDF workflow owner, I want `delivery_guard_report.json` to remain the mechanical delivery proof, so that final delivery has one mechanical pass/fail artifact.
25. As a video-to-PDF workflow owner, I want a `PreToolUse` guard to block risky compile commands before execution, so that obvious mistakes stop before a long-running tool call starts.
26. As a video-to-PDF workflow owner, I want the `PreToolUse` guard to use strong blocking, so that agents cannot proceed after a detected compile bypass.
27. As a video-to-PDF workflow owner, I want the `PreToolUse` guard to scan for known stale compile hazards quickly, so that a new command can warn about an abnormal workspace state.
28. As a video-to-PDF workflow owner, I want the anomaly scan to be read-only, so that hooks do not unexpectedly repair, kill processes, or move files.
29. As a video-to-PDF workflow owner, I want the existing Stop hook to remain focused on Final Delivery Guard, so that compile enforcement does not create recursive or long-running hook behavior.
30. As a future agent, I want the render skills to document the guarded compile entry point, so that PDF generation instructions do not still recommend raw `xelatex`.
31. As a future agent, I want Bilibili and YouTube render workflows to use the same compile guard contract, so that source platform does not affect compile safety.
32. As a future agent, I want tests at the wrapper CLI seam, so that timeouts, modes, output placement, and reports are verified from observable behavior.
33. As a future agent, I want tests at the hook decision seam, so that blocked and allowed commands are verified without launching real LaTeX.
34. As a future agent, I want tests at the delivery guard seam, so that compile provenance is enforced before final delivery.
35. As a final PDF reader, I want delivered PDFs to come from a controlled final compile, so that incomplete or stale compile outputs do not reach me as final artifacts.

## Implementation Decisions

- The accepted architecture is a dual enforcement path: a guarded compile wrapper handles actual compilation, while `PreToolUse` blocks risky shell entry points.
- The compile wrapper is the only supported LaTeX compilation entry point for video-to-PDF workflows.
- Direct LaTeX engine calls are strongly blocked by the project `PreToolUse` guard.
- The wrapper has two modes: `quick` for temporary diagnostic compilation and `final` for deliverable compilation.
- Quick mode writes diagnostic outputs and reports only inside disposable build storage.
- Final mode writes the latest compile provenance report under the video output directory's LaTeX review area.
- Callers pass semantic compile options. Raw arbitrary output-directory control is outside the wrapper interface.
- The wrapper decides and creates the build directory inside the video output directory's disposable storage.
- The wrapper enforces both total timeout and idle output timeout.
- The wrapper writes machine-readable compile reports for both successful and failed runs.
- Final compile reports include fingerprints for the final PDF and main TeX source.
- Delivery guard checks require a fresh passing final compile report for new video PDF targets.
- Quick compile reports cannot satisfy final delivery.
- Legacy PDF handling remains explicit. Existing external PDFs may skip compile provenance only when the workflow does not recompile them.
- The Acceptance Reviewer does not read compile reports as acceptance evidence.
- `acceptance_report.json` remains the only machine-readable quality decision source.
- `delivery_guard_report.json` remains the mechanical proof that final delivery conditions are satisfied.
- The existing Stop hook remains scoped to Final Delivery Guard.
- The compile anomaly scan is read-only and fast.
- The anomaly scan does not compile, kill processes, move files, or repair files.
- Bilibili and YouTube render instructions should stop recommending raw `xelatex` commands.
- The existing ASCII staging compiler is prior art for copying inputs into a controlled staging directory, but the new contract must also enforce timeouts, disposable output location, compile reports, and delivery guard integration.

## Testing Decisions

- The highest test seam for compilation behavior is the guarded wrapper command-line interface.
- Wrapper tests should verify external behavior through exit codes, output files, reports, and log files.
- Wrapper tests should use fake or tiny engine commands where possible, so unit tests do not depend on a full MiKTeX run.
- Wrapper tests should cover `quick` mode output placement, report placement, and non-delivery status.
- Wrapper tests should cover `final` mode output placement, report placement, PDF fingerprint recording, and TeX fingerprint recording.
- Wrapper tests should cover invalid TeX path, invalid mode, invalid timeout, missing engine, and output path policy failures.
- Wrapper tests should cover total timeout and idle timeout behavior through a controlled fake process.
- Hook tests should treat the `PreToolUse` script as a pure decision function over hook input JSON.
- Hook tests should verify strong blocking for direct LaTeX engines.
- Hook tests should verify strong blocking for dangerous output-directory values.
- Hook tests should verify allowing the guarded wrapper command.
- Hook tests should verify that non-LaTeX shell commands are allowed.
- Hook tests should verify that scan-state findings are reported without writes or process termination.
- Delivery guard tests should verify that missing, failed, malformed, stale, quick-mode, wrong-PDF, and wrong-TeX compile reports block new video PDF delivery.
- Delivery guard tests should verify that a fresh passing final compile report allows the existing acceptance freshness checks to proceed.
- Legacy target tests should verify that explicitly marked legacy external PDFs can be checked without a compile report only when no recompilation is claimed.
- Skill contract tests should verify that Bilibili and YouTube render instructions require the guarded wrapper and no longer direct agents to call raw `xelatex`.
- Configuration tests should validate `.codex` hook configuration syntax and ensure the existing Stop hook remains present.
- Tests should avoid real YouTube or Bilibili downloads.
- Tests should avoid real model calls.
- Full LaTeX smoke tests may be added as optional integration checks when MiKTeX is available.

## Out of Scope

- Replacing the Final Delivery Acceptance Gate.
- Letting compile success replace acceptance success.
- Expanding acceptance criteria categories.
- Moving Acceptance Reviewer responsibilities into the compile wrapper.
- Adding long-running compile or repair work to the Stop hook.
- Killing already-running LaTeX processes from a hook.
- Moving, deleting, or repairing files from the anomaly scan.
- Supporting arbitrary caller-provided LaTeX output directories.
- Creating a cross-project plugin version of the guard.
- Rewriting the whole Bilibili or YouTube render workflow.
- Running full video generation as part of implementation tests.
- Allowing ordinary agents to waive compile provenance for newly generated video PDFs.

## Further Notes

The key distinction is between prevention, execution control, and delivery proof. `PreToolUse` prevents obvious unsafe commands before they start. The guarded wrapper controls actual LaTeX execution and bounded failure. `delivery_guard.py check` proves that the final delivered PDF came from the controlled path.

The main limitation is that `PreToolUse` cannot interrupt a tool call that has already started. Long-running compile protection therefore belongs inside the wrapper through total timeout and idle watchdog behavior.

The second limitation is version drift in Codex hook behavior. Current local Codex schema and tests use the `PreToolUse` event name and `Bash` matcher for shell commands. Future Codex upgrades should recheck that schema before changing hook configuration.

# Persisted command runner operations

Qualifying non-interactive commands run through one repository entrypoint so their process identity, complete output, heartbeat, terminal state, and exit code remain observable after the initiating agent session disappears.

## Qualification

Persisted execution is mandatory when the expected runtime exceeds five minutes, the active tool requires later waits, the process may outlive the initiating session, or rerunning it is expensive or evidentiary. This includes qualifying tests, downloads, transcription, rendering, compilation, migration, recovery, and batch commands.

Interactive commands that require terminal input remain ineligible. A target must receive every required argument before launch.

## Command reference

All examples run from the repository root with the project Python runtime:

```powershell
$python = 'D:\Project\video2pdf\kimi\.venv\Scripts\python.exe'
& $python -X utf8 -B scripts\persisted_command.py start --task-name "<task-name>" --cwd "<working-directory>" -- <command> <arguments>
& $python -X utf8 -B scripts\persisted_command.py wait --run-dir "<run-dir>"
& $python -X utf8 -B scripts\persisted_command.py list
& $python -X utf8 -B scripts\persisted_command.py show --run-dir "<run-dir>"
& $python -X utf8 -B scripts\persisted_command.py reconcile --run-dir "<run-dir>"
```

`start` returns JSON containing `data.run_id` and `data.run_dir`. `wait` blocks until state, structured failure, or security eligibility changes and then returns one compact event. An already-terminal run returns its current terminal event immediately. `list` discovers all retained runs. `show` reads one complete record. `reconcile` checks persisted process identity and may correct a stale non-terminal status without restarting, terminating, attaching to, or taking over the target.

On Windows, persisted execution must not leave a visible PowerShell window open. Let `start` return immediately after it launches the detached supervisor, then observe the run later with non-blocking `show` or `reconcile` calls. Use `wait` only when the calling tool guarantees hidden-window execution. Launch one `wait` process and keep observing that same process through the tool layer; timed relaunches recreate model wakeups and JSON payloads. Set the calling tool's command timeout longer than the expected wait duration because a short command timeout terminates the observer.

PowerShell coordinators that capture the runner's native JSON output must establish UTF-8 in their own process before the first native invocation:

```powershell
$utf8NoBom = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
```

In PowerShell 7, `[Console]::OutputEncoding` governs decoding of captured native stdout, while `$OutputEncoding` governs text sent from PowerShell to native stdin. Setting both to the same UTF-8 encoding keeps the boundary consistent without changing the system locale. If `start` exits successfully and the consumer cannot parse its response, treat that as a consumer transport failure. Recover the retained run through `list`, `show`, and `reconcile`; do not invoke `start` again solely because response parsing failed.

## User-facing notification policy

The supervisor heartbeat proves that execution remains observable. It is durable operation evidence and does not by itself justify a user-facing progress message. After `start`, report the stable task name and `data.run_dir` once.

Use one of two observation modes:

- When the result blocks the requested delivery, keep the task active and inspect it silently with `show`, `reconcile`, or a hidden-window `wait`.
- When the result does not block the current delivery, return control with `data.run_dir`; a later session can recover it through `list`, `show`, or `reconcile`.

Emit a user-facing update only for a terminal state, a changed security classification or `acceptance_evidence_eligible` value, an explicit machine-readable milestone from the target, or an error, blocker, or user decision. Raw log growth, a newer `heartbeat_at`, and an unchanged `running` state remain silent observations. Do not emit filler commentary for an unchanged `running` state, a heartbeat refresh, log growth, the absence of a terminal event, or the absence of new errors or blockers; messages such as “still running,” “no terminal event yet,” or “no new errors” are invalid progress updates.

`wait` does not accept an observation timeout. It emits a compact event containing state, exit code, structured failure and security fields, latest-output time, log sizes, and log filenames relative to `run_dir`. It never embeds raw log lines. Use `show` for the complete persisted snapshot, then read `stdout.log`, `stderr.log`, or `command.log` only when diagnosis requires them. Report `interrupted` and `unknown` immediately because continuity can no longer be proven. If a higher-priority runtime requires a chat heartbeat, use the longest permitted interval and emit only its minimal required text.

At run creation, the runner also generates an independent 256-bit random
`run_nonce`. It is recorded in `command.json` and every `status.json`, then
injected into the target as `VIDEO2PDF_PERSISTED_RUN_NONCE` together with
`VIDEO2PDF_PERSISTED_RUN_ID`. The values let an eligible target bind its own
external artifacts back to this persisted launch. They are local lineage
controls, not signatures; a local writer who can modify all unsigned
artifacts can forge them.

On Windows, `supervisor_identity` and `target_identity` record a complete
process observation: PID, creation FILETIME, executable path and file identity,
plus parent PID and parent creation identity. Each observation carries a
SHA-256 over that complete relation. Terminal status also records file
identity, size, modification time, and creation/change time for the command,
supervisor launch record, three logs, and exit-code artifact. PID alone never
authorizes reconciliation or Promotion; unavailable rich identity remains
diagnostic only.

`reconcile` first distinguishes a proven missing target from an unavailable
observation. For a live target it re-observes and compares every persisted
identity field: PID, creation identity, canonical executable path, executable
file identity, parent PID and creation identity, and the complete-observation
SHA-256. A proven missing process becomes `interrupted`; an incomplete,
unavailable, or drifting rich identity becomes `unknown`. A match leaves the
running status unchanged.

A Promotion runner may be the persisted target process itself. When the
Windows environment executable acts as a launcher, the runner must instead be
the launcher's directly observed child. Discovery and worker-launcher
relations then originate from that runner identity.

Project-test Promotion runs add a stricter source gate. Before creating an
external run, `run_project_tests.py` requires a clean Git worktree except the
explicit `待删除/` evidence-output boundary. It records HEAD, the Git tree, Git
blob identities, exact runtime hashes, and a closed inventory covering all
tracked runtime-authority roots, including code, tests, prompts, schemas,
configuration, documentation contracts, and evidence authorities. Exact
runtime bytes are frozen under the external run. Independent discovery and
workers execute from that frozen root. A frozen-root-local `.git` indirection
targets a run-owned Git authority whose independent refs are pinned to the
manifest commit and tree. Link, config, shared-object alternates, hashes, and
opened-file identities are manifest-bound without exporting global Git
environment variables. Git-authority tests inspect the committed execution
tree while nested temporary repositories remain isolated. Workers revalidate
the committed and frozen bytes at startup, and the Promotion validator
independently recomputes the same relation through stable opened-file
snapshots.

The output-boundary exception accepts an unchanged copy of a tracked source
when the tracked original stays at its authoritative path with identical
bytes. The copy has no execution authority: it is absent from the Registry,
execution-source manifest, frozen inventory, discovery roots, and worker
import path. Tracked source modification, deletion, rename or move is still
rejected. For a Git porcelain `R` or `C` record, both recorded paths are
checked, and untracked source outside the explicit output boundary is
rejected.

`stdout.log` and `stderr.log` preserve the target's original byte streams. `command.log` preserves supervisor observation order as length-prefixed binary records. Each record has the ASCII header `[<stream> <byte-length>]\n`, immediately followed by exactly `<byte-length>` payload bytes. `<stream>` is `stdout` or `stderr`. Consumers must use the declared byte length rather than newline or prefix scanning to locate the next record.

At launch the runner records the execution-time implementation Git state in `command.json`:
`git_commit` is the full `git rev-parse HEAD` value resolved in the run's working
directory, or the sentinel `"<git-commit-unavailable>"` when the working
directory is not an anchored Git worktree. `worktree_clean` is `true` only when
`git status --porcelain` reports no changes at all (tracked or untracked,
excluding gitignored paths). These fields causally bind every persisted run to
the code that was checked out when it executed. Evidence collectors that
finalize manifests against an implementation commit treat a sentinel
`git_commit`, a mismatch against the final manifest commit, or a dirty
`worktree_clean` as failed qualification evidence.

The accepted exit-code set defaults to `{0}`. Repeating `--accepted-exit-code <code>` before `--` replaces that default with the declared set, so `0` must also be declared when it remains valid beside an intentional nonzero code. That declaration becomes immutable at launch. `succeeded` and `failed` require an actual child exit code; `launch_failed` has none. An absent matching process becomes `interrupted`, while uncertain identity becomes `unknown`.

## End-to-end cross-process example

The following controlled command runs long enough for the launcher to exit before the child. It writes no repository artifact whose presence could be confused with success.

In the initiating process:

```powershell
$python = 'D:\Project\video2pdf\kimi\.venv\Scripts\python.exe'
& $python -X utf8 -B scripts\persisted_command.py start --task-name "persisted-contract-demo" --cwd "$PWD" -- $python -X utf8 -c "import time; print('started', flush=True); time.sleep(15); print('finished', flush=True)"
```

The launcher returns immediately. Copy `data.run_dir` from its JSON response. Open a separate process, rediscover the run, resume observation, and inspect it:

```powershell
$python = 'D:\Project\video2pdf\kimi\.venv\Scripts\python.exe'
& $python -X utf8 -B scripts\persisted_command.py list
& $python -X utf8 -B scripts\persisted_command.py show --run-dir "<data.run_dir>"
& $python -X utf8 -B scripts\persisted_command.py reconcile --run-dir "<data.run_dir>"
& $python -X utf8 -B scripts\persisted_command.py wait --run-dir "<data.run_dir>"
```

A successful `wait` event reports `data.state` as `succeeded` and `data.exit_code` as `0`. The run directory contains `command.json`, `status.json`, `stdout.log`, `stderr.log`, `command.log`, and `exit-code.txt`. `stdout.log` contains both controlled messages; `exit-code.txt` contains the terminal `0`. These persisted files are the terminal evidence located from another process.

## Recovery and evidence use

After session loss, `list` is the discovery operation. The new session selects the immutable run directory and uses `show`, `reconcile`, or `wait`; it never infers success from output artifacts. A missing `exit-code.txt` means no actual exit code has been persisted.

Complete logs remain under `待删除/long-running/` until manual cleanup. They are never truncated, rotated, overwritten, or automatically deleted. A rerun receives another run ID and preserves prior history.

The command record omits environment values and redacts recognized sensitive arguments. Target output must already be safe to retain. If `status.security.acceptance_evidence_eligible` is false and its classification is `security_failure`, the logs remain local diagnostic material and cannot serve as acceptance evidence. Shared or committed evidence must omit secrets, raw cookies, tokens, authorization headers, and credential-bearing URLs.

## Authority boundary

Persisted execution records command operation evidence only. They do not activate Workflow Kernel 2.0 and do not replace Acceptance Reports, Delivery Guard reports, Exit Evidence manifests, Workflow Kernel Run Records, or any existing validation gate.

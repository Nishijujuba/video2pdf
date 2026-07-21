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
& $python -X utf8 -B scripts\persisted_command.py wait --run-dir "<run-dir>" --timeout-seconds 3600
& $python -X utf8 -B scripts\persisted_command.py list
& $python -X utf8 -B scripts\persisted_command.py show --run-dir "<run-dir>"
& $python -X utf8 -B scripts\persisted_command.py reconcile --run-dir "<run-dir>"
```

`start` returns JSON containing `data.run_id` and `data.run_dir`. `wait` observes until a terminal state or the requested observation timeout. `list` discovers all retained runs. `show` reads one record. `reconcile` checks persisted process identity and may correct a stale non-terminal status without restarting, terminating, attaching to, or taking over the target.

`stdout.log` and `stderr.log` preserve the target's original byte streams. `command.log` preserves supervisor observation order as length-prefixed binary records. Each record has the ASCII header `[<stream> <byte-length>]\n`, immediately followed by exactly `<byte-length>` payload bytes. `<stream>` is `stdout` or `stderr`. Consumers must use the declared byte length rather than newline or prefix scanning to locate the next record.

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
& $python -X utf8 -B scripts\persisted_command.py wait --run-dir "<data.run_dir>" --timeout-seconds 60
```

A successful terminal response reports `status.state` as `succeeded` and `status.exit_code` as `0`. The run directory contains `command.json`, `status.json`, `stdout.log`, `stderr.log`, `command.log`, and `exit-code.txt`. `stdout.log` contains both controlled messages; `exit-code.txt` contains the terminal `0`. These persisted files are the terminal evidence located from another process.

## Recovery and evidence use

After session loss, `list` is the discovery operation. The new session selects the immutable run directory and uses `show`, `reconcile`, or `wait`; it never infers success from output artifacts. A missing `exit-code.txt` means no actual exit code has been persisted.

Complete logs remain under `待删除/long-running/` until manual cleanup. They are never truncated, rotated, overwritten, or automatically deleted. A rerun receives another run ID and preserves prior history.

The command record omits environment values and redacts recognized sensitive arguments. Target output must already be safe to retain. If `status.security.acceptance_evidence_eligible` is false and its classification is `security_failure`, the logs remain local diagnostic material and cannot serve as acceptance evidence. Shared or committed evidence must omit secrets, raw cookies, tokens, authorization headers, and credential-bearing URLs.

## Authority boundary

Persisted execution records command operation evidence only. They do not activate Workflow Kernel 2.0 and do not replace Acceptance Reports, Delivery Guard reports, Exit Evidence manifests, Workflow Kernel Run Records, or any existing validation gate.

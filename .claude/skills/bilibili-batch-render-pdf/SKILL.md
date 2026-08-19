---
name: bilibili-batch-render-pdf
description: Use when the user provides a Bilibili multi-part video, playlist, collection, or several Bilibili URLs and wants one independent PDF per part through the Video Workflow Kernel. The batch skill plans items with `batch-plan`, creates one independent Video Workflow Run per selected item with `batch-run`, and supervises recovery and status with `batch-recover`/`batch-status`. Item success requires the guarded `delivered` delivery stage; PDF existence alone is never success.
---

# Bilibili Batch Render PDF (Kernel Supervisor)

Use this skill to orchestrate a Bilibili batch where each selected part is rendered as its own independent PDF by its own Video Workflow Run through the Kernel.

This skill is a Kernel-supervisor skill. It does not replace `bilibili-render-pdf`; every batch item still becomes a single-video Run that uses the single-video Kernel workflow and its skill semantics. The Batch Supervisor enumerates items, plans a Batch Record, creates one Run per selected item, submits only currently admitted work through Resource Admission, and rebuilds read-only Batch Item Projections from authoritative Run state.

## Active Authority Boundary

Batch is `active_batch` for all new batches under the current runtime authority and published Slice 14 Exit Evidence Manifest. The Legacy batch driver is retained for pre-existing batch directories only; PDF-existence success and global `--concurrency` are retired.

For every new batch, run `workflow-policy-check` and `batch-authority-check` first. When Batch reports current `active_batch` authority, create the new batch with ordinary `batch-plan`. If either check reports missing or stale authority, fail closed and repair authority before planning; never route a new batch through the Legacy driver.

### Authority recovery

Publishing Slice 14 and activating Batch authority are separate governed recovery steps. Use `batch-activate` only when the formal Batch authority is absent and repository owners explicitly authorize rebuilding activation evidence. Use `batch-reconcile` after an interrupted activation. A published manifest without a current `active_batch.json` authority leaves new planning closed.

Use `batch-authority-refresh` when an existing Batch authority is stale because its published Slice 14 evidence or prerequisite Global Gate/platform bindings have advanced. Refresh requires the current authority generation as an optimistic concurrency fence and publishes the next generation. Use `batch-reconcile` after an interrupted refresh. Existing Legacy batch directories remain on the Legacy driver throughout authority recovery; recovery changes authority for new batches and never migrates historical directories.

## Fit

Use this skill when:

- the source is a Bilibili 分P video, playlist, collection, or a batch of Bilibili URLs
- the expected result is one standalone `.pdf` per part, each delivered by its own guarded Video Workflow Run
- the batch must be resumable after a failure without duplicating Runs
- the user wants batch status tracked as read-only projections over authoritative Run state

For one Bilibili video without batch orchestration, use `bilibili-render-pdf` directly.

## Batch Flow (pinned)

Invoke Kernel batch mechanics only through the public Workflow CLI at `scripts/video_workflow.py`. The default governed flow for every new batch is `workflow-policy-check` -> `batch-authority-check` -> `batch-plan` -> `batch-run` -> `batch-recover`/`batch-status`. `batch-activate`, `batch-authority-refresh`, and `batch-reconcile` belong only to authority recovery:

1. `workflow-policy-check` — fail closed unless the Global Gate and both platform authorities remain current.
2. `batch-authority-check` — fail closed unless the active Batch authority, Exit Evidence, Global Gate, and platform bindings remain current.
3. `batch-plan` — deterministically enumerate the source items and write the planned Batch Record (no Runs are created).
4. `batch-run` — create one independent Video Workflow Run per selected item through the kernel's guarded initialization path and submit only currently admitted work through Resource Admission.
5. `batch-recover` — reconcile referenced runs and rebuild every item projection from authoritative Run state after interruption.
6. `batch-status` — report the read-only batch status and per-item projection summaries.
7. `batch-rebuild-projections` — rebuild every item projection from authoritative Run state without touching Run state.

Absent-authority recovery uses `batch-activate` to publish generation 1 from the published Slice 14 Exit Evidence Manifest. Stale-authority recovery uses `batch-authority-refresh` to publish the next generation from refreshed Slice 14 evidence. `batch-reconcile` converges an interrupted activation or refresh intent without creating an extra authority generation.

## Command Reference (pinned)

Use the Workflow CLI launcher with the skill virtual environment:

Verify the current Global Gate, platform, and Batch authorities before ordinary planning:

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py workflow-policy-check --control-store-root "<control-store-root>"
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-authority-check --control-store-root "<control-store-root>"
```

Absent Batch authority: after repository-owner authorization, publish generation 1 from the current Slice 14 evidence. If activation is interrupted, reconcile the prepared intent, then prove the authority current:

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-activate --control-store-root "<control-store-root>" --exit-evidence "<published-slice14-manifest>" --activated-at "<iso>"
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-reconcile --control-store-root "<control-store-root>"
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-authority-check --control-store-root "<control-store-root>"
```

Stale Batch authority: publish refreshed Slice 14 evidence, then read `<control-store-root>/active_batch.json`. The failing authority-check envelope does not supply the current generation. Require the file's `generation` to be an integer greater than or equal to 1, and pass that exact value as the optimistic concurrency fence. If refresh is interrupted, reconcile its prepared intent before checking currentness:

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-authority-refresh --control-store-root "<control-store-root>" --exit-evidence "<refreshed-slice14-manifest>" --expected-generation "<current-generation>" --refreshed-at "<iso>"
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-reconcile --control-store-root "<control-store-root>"
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-authority-check --control-store-root "<control-store-root>"
```

`batch-authority-check` is required before `batch-plan` and after any authority repair or control-root relocation. A stale `--expected-generation` fails closed; the operator must reread current authority and must not bypass the generation fence.

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-plan --control-store-root <control-store-root> --platform bilibili --source-url <url> --task-start <iso> --request-id <id>
```

Plan a batch (`--control-store-root --platform (--source-url | --url-set) --task-start --request-id [--selection] [--workspace-root]`). `--control-store-root` owns the Batch Record and supervisor state. `--workspace-root` is the output root for the independent Runs. When `--workspace-root` is omitted, the Workflow CLI uses the repository `workspace` directory. The planned Batch Record binds this output root, so every later command resolves it from the record and accepts only the control-store root:

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-plan `
  --control-store-root "<control-store-root>" `
  --platform bilibili `
  --source-url "https://www.bilibili.com/video/BV..." `
  --task-start "2026-08-16T09:30:00+08:00" `
  --request-id "<request-id>" `
  [--selection 1,2,3] `
  [--workspace-root "D:\Project\video2pdf\newskill-kimi\workspace"]
```

Create and submit the independent Runs (`--batch-id --control-store-root --session-id [--run-task-start] [--fault-point]`):

`--run-task-start` is optional. On the first `batch-run`, omission generates one timezone-qualified timestamp and binds it to `Batch Record.run_task_start`; every later omission reuses that exact bound value. Supplying a different value after binding fails closed.

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-run `
  --batch-id "<batch-id>" `
  --control-store-root "<control-store-root>" `
  --session-id "<session-id>" `
  [--run-task-start "<iso>"] `
  [--fault-point "<fault-point>"]
```

Recover and rebuild projections (`--batch-id --control-store-root`):

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-recover `
  --batch-id "<batch-id>" `
  --control-store-root "<control-store-root>"
```

Rebuild every item projection (`--batch-id --control-store-root`):

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-rebuild-projections --batch-id "<batch-id>" --control-store-root "<control-store-root>"
```

Report read-only batch status (`--batch-id --control-store-root`):

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-status --batch-id "<batch-id>" --control-store-root "<control-store-root>"
```

The `batch-plan`, `batch-rebuild-projections`, and `batch-status` commands are read-only with respect to Run state. `batch-run` creates Runs through the kernel's own guarded initialization path and `batch-recover` only calls the kernel's own reconcile functions.

## Success Definition (pinned)

An item is successful only when its Video Workflow Run has reached the `delivered` stage with a fresh passing Delivery Guard report. A PDF path, output-directory existence, process exit code, or cached batch status cannot establish success.

In authoritative state terms this means `run.json` records `delivery.stage == "delivered"` and `review/acceptance/delivery_guard_report.json` is present and passing for that Run. The Batch Item Projection sets `delivery_outcome.guarded_delivered == true` only when the projection provider verified both conditions; a bare PDF in the item directory, a `0` exit code, or a cached batch status is never success.

## Concurrency (pinned)

Concurrency flows through Resource Admission only. The global `--concurrency` flag is retired. The batch submits only currently admitted work; it never pre-creates child tasks outside admission and it has no independent parallelism control. Fairness is expressed through the batch `fairness_group_id` on each Resource Admission claim.

## Authentication and Breakers (pinned)

Platform authentication failures open the platform Resource Circuit Breaker (existing authority) and stop admission of later platform work. Already-started independent runs retain their own state. The old batch-local cookie-scanning breaker is retired; the batch never implements its own authentication breaker.

## Per-Video Authority (pinned)

The Batch Supervisor never writes a video's phase, checkpoint, acceptance result, or delivery stage. The Video Workflow Run Record remains authoritative for every per-video lifecycle decision. Batch records carry source selection, item order, run mappings, and rebuildable projections only.

## Recovery (pinned)

`batch-recover` reconciles referenced runs and rebuilds every item projection from authoritative Run state. Interrupted item creation never creates duplicate runs: each selected item derives a deterministic `run_id` through the kernel's bootstrap formula, and the kernel's initialization/reconcile path converges to one Run per item even when item creation was interrupted.

## Project Constants

- Output root: `D:\Project\video2pdf\newskill-kimi\workspace`
- Bilibili cookies: `C:\Users\juju\Downloads\www.bilibili.com_cookies.txt`
- Skill venv: `D:/Project/video2pdf/kimi/.venv/`
- Skill tools: `D:\Project\video2pdf\kimi\tools`
- XeLaTeX: `D:\kits\MiKTex\miktex\bin\x64\xelatex.exe`

Hard rules:

- Use the Bilibili cookie file first. Use the relevant platform cookie file through the existing platform authority; do not reimplement cookie handling inside the batch.
- If cookies are expired or rejected, the platform Resource Circuit Breaker pauses admission and the operator refreshes the cookie file before continuing.
- Prefer English subtitles during subtitle acquisition, then follow `bilibili-render-pdf` fallbacks when needed.
- Use English for collection, reasoning, planning, and intermediate organization.
- Use Chinese for the final PDF content.
- For English teaching or IELTS content, preserve useful original English and make the note bilingual where helpful.
- Each item output directory is created under `D:\Project\video2pdf\newskill-kimi\workspace` and named from the original video or part title plus the task start timestamp in local machine time: `normalized_title_yyyyMMdd_HHmmss`.
- Directory and final PDF basenames share the same whitelist: preserve Unicode letters and numbers, preserve only ASCII space and `_` as special characters, replace every other character with `_`, collapse repeated spaces and `_`, then trim leading or trailing spaces, `_`, and `.`.
- The final delivered PDF basename comes from the PDF article title when one exists, or the original video title when no separate article title exists.
- Every part output directory must contain `待删除`.
- Disposable intermediates belong under `待删除`.
- Never permanently delete files.

## Legacy Batch Driver Note

The legacy driver (`scripts/run_batch.py`) is retained under `legacy/` for pre-existing batch directories only. New batches must use the Kernel `batch-*` CLI. The legacy driver's PDF-existence success rule and global `--concurrency` authority carry no new-batch authority per ADR 0036 and issue #15.

Pre-existing legacy batch directories can still be managed with the legacy driver from its staged location:

```powershell
python .agents\skills\bilibili-batch-render-pdf\legacy\scripts\run_batch.py --help
```

The legacy `--mode plan`, `--mode manual`, and `--mode reconcile` flows remain documented inside the legacy driver for pre-existing batch directories only; they are not part of the Kernel batch flow.

## Verification

After creating or changing this skill, validate with the Workflow CLI and the batch status command:

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-status --batch-id "<batch-id>" --control-store-root "<control-store-root>"
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B scripts/video_workflow.py batch-recover --batch-id "<batch-id>" --control-store-root "<control-store-root>"
```

Verify the `.agents` and `.claude` mirrors are byte-identical:

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B -c "from pathlib import Path; a=Path('.agents/skills/bilibili-batch-render-pdf/SKILL.md').read_bytes(); b=Path('.claude/skills/bilibili-batch-render-pdf/SKILL.md').read_bytes(); assert a==b, 'mirror differs'; print('batch skill mirror identical')"
```

Pre-existing legacy batches can still be managed with the legacy driver in `legacy/`; validate the staged legacy driver still parses and its tests still pass:

```powershell
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B -c "import ast; from pathlib import Path; ast.parse(Path('.agents/skills/bilibili-batch-render-pdf/legacy/scripts/run_batch.py').read_text(encoding='utf-8')); print('legacy run_batch parses')"
D:/Project/video2pdf/kimi/.venv/Scripts/python.exe -X utf8 -B -m unittest discover -s .agents/skills/bilibili-batch-render-pdf/legacy/scripts -p "test_run_batch.py" -v
```

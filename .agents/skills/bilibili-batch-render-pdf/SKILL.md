---
name: bilibili-batch-render-pdf
description: Use when the user provides a Bilibili multi-part video, playlist, collection, or several Bilibili URLs and wants one independent PDF per part with resumable status tracking, Codex CLI batch execution, AGENTS.md compliance, and per-video subagent rendering.
---

# Bilibili Batch Render PDF

Use this skill to orchestrate a Bilibili batch where each selected part is rendered as its own independent PDF by a child `codex exec` task using `bilibili-render-pdf`.

This skill is a supervisor. It enumerates parts, creates a manifest, prepares per-part prompts, dispatches `codex exec`, and records status. It does not replace `bilibili-render-pdf`; every child task must still use that skill for the actual PDF build.

## Fit

Use this skill when:

- the source is a Bilibili 分P video, playlist, collection, or a batch of Bilibili URLs
- the expected result is one standalone `.pdf` per part
- the batch should be resumable after a failure
- the user wants `codex exec` to isolate each part
- the project `AGENTS.md` requires multiple subagents inside every single-video PDF workflow

For one Bilibili video without batch orchestration, use `bilibili-render-pdf` directly.

## Required Project Contract

Every child `codex exec` prompt must tell the child agent to read and obey:

- `AGENTS.md`
- `.agents/skills/bilibili-render-pdf/SKILL.md`

Every child task must process only one selected part. If Bilibili metadata exposes multiple parts again, the child must stay on the assigned `Pxx` and avoid broadening the scope.

Every child task must use multiple subagents for the PDF workflow:

- Data Preparation agent: download the assigned part's original video and usable subtitles, collect metadata and cover/source assets, preserve subtitle timestamps, and write the source-material handoff before outline work begins.
- Outline agent: table of contents, terminology, symbol table, chapter boundaries, writing contract, cross-section conventions.
- Writer agents: one or more writers, saving complete chapter drafts as `section_*.tex`.
- Figure agents: frame extraction, selection, cropping, explanatory diagrams, captions, timestamp footnotes.
- Consistency agent: duplicate definitions, terminology, transitions, cross-references, notation.
- Independent review agent: after the first PDF is produced, compare against original subtitles and continue revisions until the TeX content is complete enough.

Every child task must also run the mandatory Pyramid Gate from `.agents/skills/pyramid-principle-validate/SKILL.md` after `outline_contract.md`, after every `section_*.tex`, and after integrated `main.tex`. The child must leave reports under `review\pyramid\`, and the batch reconcile step will run `check_output_gate.py <output-dir> --enforce-gate` before accepting the part as succeeded.

Project constants:

- Output root: `D:\Project\video2pdf\newskill-kimi\workspace`
- Bilibili cookies: `C:\Users\juju\Downloads\www.bilibili.com_cookies.txt`
- Skill venv: `D:/Project/video2pdf/kimi/.venv/`
- Skill tools: `D:\Project\video2pdf\kimi\tools`
- XeLaTeX: `D:\kits\MiKTex\miktex\bin\x64\xelatex.exe`

Hard rules:

- Use the Bilibili cookie file first.
- If cookies are expired or rejected, stop the batch and ask for refreshed cookies.
- Prefer English subtitles during subtitle acquisition, then follow `bilibili-render-pdf` fallbacks when needed.
- Use English for collection, reasoning, planning, and intermediate organization.
- Use Chinese for the final PDF content.
- For English teaching or IELTS content, preserve useful original English and make the note bilingual where helpful.
- Output directories are created under `D:\Project\video2pdf\newskill-kimi\workspace` and named from the original video or part title plus the task start timestamp in local machine time: `normalized_title_yyyyMMdd_HHmmss`.
- Directory and final PDF basenames share the same whitelist: preserve Unicode letters and numbers, preserve only ASCII space and `_` as special characters, replace every other character with `_`, collapse repeated spaces and `_`, then trim leading or trailing spaces, `_`, and `.`.
- The final delivered PDF basename comes from the PDF article title when one exists, or the original video title when no separate article title exists.
- Every part output directory must contain `待删除`.
- Disposable intermediates belong under `待删除`.
- Never permanently delete files.

## Batch Driver

Use the bundled driver:

```powershell
python .agents\skills\bilibili-batch-render-pdf\scripts\run_batch.py --help
```

Plan a batch without launching child Codex tasks:

```powershell
python .agents\skills\bilibili-batch-render-pdf\scripts\run_batch.py `
  --url "https://www.bilibili.com/video/BV..." `
  --mode plan
```

Run or resume a planned batch:

```powershell
python .agents\skills\bilibili-batch-render-pdf\scripts\run_batch.py `
  --manifest "D:\Project\video2pdf\newskill-kimi\workspace\<batch>\batch-control\manifest.json" `
  --mode run
```

Use manual mode when the current Codex desktop/app-server environment has already shown a `codex_cli` app-server permission failure:

```powershell
python .agents\skills\bilibili-batch-render-pdf\scripts\run_batch.py `
  --manifest "D:\Project\video2pdf\newskill-kimi\workspace\<batch>\batch-control\manifest.json" `
  --mode manual `
  --part 5
```

After manually completing that part in the current session, reconcile the manifest and `status.json` from verified artifacts:

```powershell
python .agents\skills\bilibili-batch-render-pdf\scripts\run_batch.py `
  --manifest "D:\Project\video2pdf\newskill-kimi\workspace\<batch>\batch-control\manifest.json" `
  --mode reconcile `
  --part 5
```

Default execution is sequential. Keep `--concurrency 1` until cookies, subtitle acquisition, LaTeX compilation, and frame extraction have been proven stable for this source. Raise concurrency only after that.

Driver defaults are tuned for this Windows workspace:

- external Bilibili cookies are copied into `<out-root>\待删除\bilibili-batch-cookies` before `yt-dlp` uses them; pass `--no-localize-cookie-file` only when the cookie path is already writable and disposable
- bare `--codex codex` resolves to `codex.cmd` on Windows when available, avoiding `.ps1` launch permission failures
- child Codex tasks include `-c service_tier='fast'` by default; add more `--codex-config` values when needed, or pass `--no-default-codex-config` only after confirming the user's Codex config parses cleanly
- `--skip-git-repo-check` is enabled by default because each child task is already constrained to the workspace, selected part, manifest, and output schema
- a manifest with previous app-server permission failure history blocks `--mode run` by default; pass `--allow-known-codex-app-server-retry` only after the Codex CLI/app-server environment has actually been fixed

## Manifest

The manifest is the batch ledger. It records the source, output root, batch directory, each selected part, prompt path, log path, last message path, attempts, status, and errors.

Statuses:

- `planned`: discovered and ready
- `running`: child `codex exec` is active
- `succeeded`: child task exited successfully and PDF artifact verification passed
- `failed`: child task exited unsuccessfully
- `blocked`: child task cannot continue without external action, usually refreshed cookies
- `skipped`: part was filtered out

Resumption rule:

- By default, `succeeded` items are skipped.
- Failed, blocked, and planned items can be retried by running with the same manifest.
- Use `--force` only when a completed part should be rebuilt.

## Codex Exec Contract

The driver invokes child jobs with:

```text
codex -c service_tier='fast' exec --json --sandbox workspace-write --cd D:\Project\video2pdf\newskill-kimi --output-schema <part-result.schema.json> --output-last-message <file> --skip-git-repo-check -
```

The prompt is sent through stdin. JSONL output is written to the per-part log file. The final child response is written to `last-message.json`.

The child final response must match `references/part-result.schema.json`. The driver treats the final JSON as the structured result, then verifies artifact existence separately. A zero exit code alone does not prove success.

Child tasks should end with JSON containing:

- processed part index and title
- output directory
- final `.tex` path
- final `.pdf` path
- subtitle source used
- figure count
- independent review status
- unresolved issues, if any

## Failure Handling

Cookie failures are fatal for the batch. The driver scans failed child logs for login, expiration, rejected-cookie, 401, 403, and similar signals. When such a signal appears, it stops scheduling further work.

Ordinary per-part failures are recorded in the manifest. The batch may continue so later parts are not blocked by one malformed video, unless `--stop-on-failure` is passed.

Each part also gets a `status.json` containing `part_id`, `attempt`, `pid`, `exit_code`, `duration_ms`, `pdf_path`, `tex_path`, `artifact_checks`, `failure_class`, `retryable`, `next_action`, and the raw final response.

If `codex exec` is unavailable, the driver remains useful in `--mode plan` and `--mode manual`: it generates manifest entries and child prompts that can be launched in the current session.

Codex CLI infrastructure failures are recorded as `failure_class: codex_cli`. Common examples are `.ps1` permission denial, invalid `service_tier` config, untrusted workspace checks, stale `arg0` temp directory permissions, and `failed to initialize in-process app-server client`.

After an app-server permission failure, do not keep retrying the same child command. Run `--mode manual --part N`, use the generated `prompts\Pxx.md` in the current session with the required subagent roles, then run `--mode reconcile --part N`. Reconcile requires a `.tex`, a non-trivial `.pdf`, `review\consistency_review.md`, `review\independent_review.md`, and Pyramid Gate reports under `review\pyramid\`. The Pyramid Gate requirement is always enforced by `check_output_gate.py --enforce-gate`.

## Verification

After creating or changing this skill, validate:

```powershell
python -c "import ast, pathlib; ast.parse(pathlib.Path('.agents/skills/bilibili-batch-render-pdf/scripts/run_batch.py').read_text(encoding='utf-8'))"
python .agents\skills\bilibili-batch-render-pdf\scripts\run_batch.py --help
python .agents\skills\bilibili-batch-render-pdf\scripts\test_run_batch.py
python .agents\skills\pyramid-principle-validate\scripts\check_output_gate.py "<part-output-dir>" --enforce-gate
```

For live batches, first run `--mode plan`, inspect `manifest.json` and `prompts\Pxx.md`, then run `--mode run`.

# Project Agent Instructions

## Scope

These instructions apply whenever this project uses either of the following skills:

- `/bilibili-render-pdf`
- `/youtube-render-pdf`

The goal is to produce a complete, accurate, figure-rich Chinese PDF from a video source while keeping long-running work split across isolated subagents.

## Multi-Agent Orchestration

When using `/bilibili-render-pdf` or `/youtube-render-pdf`, the master agent must spawn multiple subagents to isolate context and reduce master-agent context pressure.

Required subagent roles:

- **Outline agent**: define the global table of contents, terminology, symbol table, chapter boundaries, writing contract, and cross-section conventions before chapter writing begins.
- **Writer agents**: use one or more writer agents depending on the number of chapters. Each writer agent must write complete chapter drafts directly and save them as `section_*.tex`.
- **Figure agents**: use one or more figure agents depending on the number of chapters. Figure agents are responsible for frame extraction, image selection, cropping, generating new explanatory diagrams or scripts, writing captions, and adding timestamp footnotes.
- **Consistency agent**: check for duplicate definitions, inconsistent terminology, broken transitions between chapters, missing cross-references, and unclear notation.
- **Independent review agent**: after the first PDF is delivered, spawn an independent review agent. This agent must compare the draft against the original subtitle files and check for missing important details or subtle information. The workflow must continue through interaction and revision until the review agent judges that the TeX content is complete enough.

## Required Tool Paths

The project has an existing `uv` environment management setup.

Use these local paths:

- Skill virtual environment: `D:/Project/video2pdf/kimi/.venv/`
- Skill tool directory: `D:\Project\video2pdf\kimi\tools`
- Working `xelatex` executable: `D:\kits\MiKTex\miktex\bin\x64\xelatex.exe`

## Cookies

Cookie files are already expected at:

- `C:\Users\juju\Downloads\www.bilibili.com_cookies.txt`
- `C:\Users\juju\Downloads\www.youtube.com_cookies.txt`

Use the relevant cookie file first. If the cookie is expired or rejected, stop and ask the user to provide a refreshed cookie file before continuing.

## Output Layout

All produced files for a video must be placed under:

```text
D:\Project\video2pdf\newskill-kimi\{video-name}_{task-start-timestamp}
```

The `{video-name}` directory must be derived from the original video title. Use the task start time in the local machine timezone for `{task-start-timestamp}`, formatted as `yyyyMMdd_HHmmss`.

Normalize the title before creating the directory:

- preserve Unicode letters and numbers
- preserve only these special characters: ASCII space and `_`
- replace every other character with `_`
- collapse repeated spaces, collapse repeated `_`, and trim leading or trailing spaces, `_`, and `.`
- if the normalized title is too long, shorten it while preserving the timestamp suffix

The final delivered PDF must use the same normalization rule. Its base filename must be derived from the PDF article title when a clear article title exists; otherwise use the original video title. The `.pdf` extension is appended after normalization.

Each video output directory must include a subfolder named:

```text
待删除
```

Place disposable intermediate files in `待删除`. Do not permanently delete intermediate files during the workflow.

## Existing Output Normalization

When reorganizing already generated video documentation directories, use:

```text
python scripts\normalize_video_workspace.py --root D:\Project\video2pdf\newskill-kimi
```

The script defaults to a dry run and writes `workspace\migration-plan.csv` and `workspace\migration-plan.json`. It only moves directories when `--apply` is passed.

For this historical migration, high-confidence directories move as whole directories into `workspace\<normalized-video-documentation-name>`. Low-confidence directories move as whole directories into `workspace\低置信目录\<original-directory-name>`. A directory is a valid video output only when internal final-delivery identity evidence exists, such as a delivered video name, article title, `main.pdf`, `notes.pdf`, or a direct `build\*.pdf` delivered PDF. Directory `CreationTime` is not authoritative for the migration date; infer the date from durable artifacts, with PDF time as a fallback.

## Language and Writing Requirements

Subtitle download priority:

- Prefer English subtitles when downloading subtitles.

Working language:

- Use English when collecting materials, reasoning, planning, and organizing intermediate results.
- Use Chinese for the final written PDF content.

The final PDF should be comprehensive, technically precise, and faithful to the original subtitle content.

## Agent skills

### Issue tracker

Issues are tracked as local markdown files under `docs/issues/<feature-slug>/`; `docs/` is the Obsidian vault root for PRDs, ADRs, and issue graph tracking. There is no external PR triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

The repo uses the default five-label triage vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a single-context domain-doc layout: root `CONTEXT.md` plus root `docs/adr/`. See `docs/agents/domain.md`.

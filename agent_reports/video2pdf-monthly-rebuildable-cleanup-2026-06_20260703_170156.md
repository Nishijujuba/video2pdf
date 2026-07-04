# video2pdf Monthly Rebuildable Cleanup Dry Run

Run time: 2026-07-03 17:01:56 +08:00
Automation ID: video2pdf-monthly-rebuildable-cleanup-monitor
Root: `D:\Project\video2pdf\newskill-kimi`
Target month: June 2026
Date boundary used: `[2026-06-01 00:00:00, 2026-07-01 00:00:00)`
Mode: rebuildable cleanup
Result: no files moved

## Scope Result

The dry-run scanned only one-level child directories under `D:\Project\video2pdf\newskill-kimi`.

- Candidate video directory count: 0
- Processed video directory count: 0
- Moved file count: 0
- Moved size: 0 bytes
- Kept `.tex` count: 0
- Root-level `.pdf` count: 0
- TeX-referenced dependency count: 0
- Risk item count: 0

No `Move-Item` operation was executed because no one-level child directory both had `LastWriteTime` in June 2026 and qualified as a video-to-PDF output directory.

## Excluded Directories

| Directory | LastWriteTime | Reason |
|---|---:|---|
| `.agents` | 2026-05-12 18:04:42 | Explicit project/tool/cache/common exclusion. |
| `.cache` | 2026-06-11 16:06:52 | Explicit project/tool/cache/common exclusion. |
| `.claude` | 2026-06-15 19:59:28 | Explicit project/tool/cache/common exclusion. |
| `.codex` | 2026-06-29 16:36:53 | Explicit project/tool/cache/common exclusion. |
| `.git` | 2026-07-03 17:00:37 | Explicit project/tool/cache/common exclusion. |
| `.venvs` | 2026-06-11 16:08:25 | Explicit project/tool/cache/common exclusion. |
| `docs` | 2026-07-01 10:24:57 | Explicit project/tool/cache/common exclusion. |
| `review` | 2026-07-03 09:10:54 | Project infrastructure/review output, not a one-video output directory. |
| `scripts` | 2026-07-02 16:40:01 | Project infrastructure/tool directory. |
| `work` | 2026-07-03 16:32:12 | Explicit project/tool/cache/common exclusion. |
| `workspace` | 2026-07-02 17:28:42 | Project workspace/category folder, not a one-video output directory for this root-level scan. |
| `待删除` | 2026-07-03 09:28:09 | Explicit project/tool/cache/common exclusion and cleanup holding area. |

Configured exclusions not present as one-level directories during this run: `.uv-cache-qwen3-asr`, `agent_reports` before report creation, `figure_scripts`, `figure_blocks`, `node_modules`, `tmp`, `build`, `dist`.

## Out-of-Scope One-Level Directories

These directories were not processed because their `LastWriteTime` did not fall within June 2026.

| Directory | LastWriteTime |
|---|---:|
| `1_面向高效长程推理的版面感知视觉记忆机制_Yaorui Shi_哔哩哔哩_20260702_234531` | 2026-07-03 08:52:56 |
| `1_长程对话中的交互式记忆评测_Jiayang Cheng_20260702_234408` | 2026-07-03 09:57:48 |
| `1_长程对话中的交互式记忆评测_Jiayang Cheng_哔哩哔哩_20260702_234408` | 2026-07-02 23:45:15 |
| `放弃思维链_大模型新架构Loop Transformer硬核解析 _ bycloud_20260702_233431` | 2026-07-02 23:36:40 |
| `放弃思维链_大模型新架构Loop Transformer硬核解析_20260702_233431` | 2026-07-03 09:19:56 |
| `全程_Tim_刘润_进化者访谈_跟着tim一起拆解_爆款内容是怎么_炼_成的_20260702_232511` | 2026-07-03 16:32:12 |

## Candidate Directory Details

No candidate directories were found, so there are no per-directory kept dependency counts, movable intermediate counts, movable sizes, or directory-specific risks to report.

## Risk Items

No risk items were found during this run:

- no root-level final PDF: 0
- subdirectory PDF with unclear final status: 0
- missing TeX-referenced image: 0
- unresolved TeX reference path: 0
- no recognizable main TeX: 0
- file in use: 0

## Post-Move Verification

No post-move verification was applicable because no moves were performed.

The intended rebuildable-mode safety boundary remains:

- preserve all `.tex` files;
- preserve root-level final PDFs or final-PDF candidates;
- preserve TeX-referenced images, charts, styles, bibliography files, and child TeX files as far as detectable;
- move only download, frame, transcript, log, cache, and LaTeX byproduct intermediates when a directory qualifies and has no risk items;
- report missing dependencies that were already absent before cleanup because cleanup cannot automatically repair them.

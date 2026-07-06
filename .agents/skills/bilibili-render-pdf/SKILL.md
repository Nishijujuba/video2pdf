---
name: bilibili-render-pdf
description: Generate a professional, detailed, figure-rich LaTeX course note and final PDF from a Bilibili lecture, tutorial, or technical talk. Use when the user provides a Bilibili URL (BV number) and wants structured Chinese teaching notes that combine the video's title, chapters, diagrams, formulas, code, subtitle explanations, the original video cover on the front page, a final synthesis chapter, key frames extracted from the highest usable video resolution, and a rendered PDF that passes compile and layout blank-space checks. Falls back to Whisper speech-to-text when no CC subtitles are available.
---

# Bilibili Render PDF

Use this skill to turn a Bilibili video into a complete, compileable `.tex` note and a rendered PDF.

This skill extends the `youtube-render-pdf` workflow with Bilibili-specific adaptations for cookie-first subtitle probing, login-gated high resolution, subtitle scarcity, multi-part (分P) videos, and platform-specific non-teaching content.

## Bilibili vs YouTube: Key Differences

| Aspect | Handling |
|--------|----------|
| **Subtitle scarcity** | Try cookie-assisted CC subtitle probing first → then inspect metadata → fall back to Whisper speech-to-text → visual-only mode |
| **Login-gated HD** | 1080P+ requires cookies; prompt the user to use `yt-dlp --cookies-from-browser chrome` |
| **Multi-part videos** | Detect 分P videos and ask the user which parts to process |
| **URL formats** | Support `bilibili.com/video/BVxxxxxxx` and `b23.tv` short links |
| **Danmaku** | Do not use danmaku as a teaching content source (too noisy); use only CC subtitles or Whisper output |

## Goal

Produce a professional Chinese lecture note from a Bilibili URL.

The output must:

- use the video's actual teaching content rather than subtitle transcription alone
- place the video's original cover image on the front page of the `.tex` and rendered PDF whenever available
- include all necessary high-value key frames as figures, without adding redundant screenshots
- end with a final synthesis section that includes the speaker's substantive closing discussion and your own distilled takeaways
- be structurally organized with `\section{...}` and `\subsection{...}`
- be a complete `.tex` document from `\documentclass` to `\end{document}`
- be compiled successfully to PDF as part of the final delivery

## Local Environment On This Machine

When running on this machine, prefer these exact binaries instead of relying on PATH lookup:

- Shared Python environment for helper scripts and generated figures: `D:\Project\video2pdf\kimi\.venv\Scripts\python.exe`
- Whisper CLI for subtitle fallback: `D:\Project\video2pdf\kimi\.venv\Scripts\whisper.exe`
- `yt-dlp`: `D:\Project\video2pdf\kimi\tools\yt-dlp.exe`
- `ffmpeg`: `D:\Project\video2pdf\kimi\tools\ffmpeg\bin\ffmpeg.exe`
- `ffprobe`: `D:\Project\video2pdf\kimi\tools\ffmpeg\bin\ffprobe.exe`
- ImageMagick `magick`: `D:\Project\video2pdf\kimi\tools\imagemagick\magick.exe`
- LaTeX engine path for the guarded wrapper `--engine` argument: `D:\kits\MiKTex\miktex\bin\x64\xelatex.exe`

Use the shared `kimi` uv environment as the default local runtime for Python-based helper work, and use the `whisper.exe` path above whenever the CC subtitle path is unavailable.

## Pedagogical Standard

The notes must read like a strong human teacher is guiding the reader through the material.

- organize each major section so the reader first understands the motivation, then the main idea, then the mechanism, then the example or evidence, and finally the takeaway
- be patient and explicit about logical transitions; make it clear why the speaker introduces a concept, what problem it solves, and how the next idea follows
- aim for deep-but-accessible explanations: keep the technical depth, but introduce formalism only after giving intuition in plain language
- when a section is dense, break it into smaller subsections that progressively build understanding rather than compressing everything into one long derivation
- do not dump subtitle content in chronological order; rewrite it into a teaching sequence with clear intent, contrast, and buildup

## Source Acquisition

### Cookie-First Subtitle Probe

1. Before inspecting metadata, first try cookie-assisted subtitle discovery with `yt-dlp --list-subs`.
   Use a user-provided Netscape cookie file such as `www.bilibili.com_cookies.txt` whenever available.
   This comes first in the workflow because Bilibili subtitle availability is often clearer with cookies than from an initial metadata-only pass.

2. Use a generic Bash command format for manual probing:

```bash
COOKIE_FILE="/path/to/www.bilibili.com_cookies.txt"
URL="https://www.bilibili.com/video/BVxxxxxxxxx/"

yt-dlp --cookies "$COOKIE_FILE" --no-playlist --list-subs "$URL"
```

3. Use a generic Python command format when the probe is being orchestrated programmatically:

```python
import subprocess

cookie_file = "/path/to/www.bilibili.com_cookies.txt"
url = "https://www.bilibili.com/video/BVxxxxxxxxx/"

subprocess.run(
    [
        "yt-dlp",
        "--cookies",
        cookie_file,
        "--no-playlist",
        "--list-subs",
        url,
    ],
    check=True,
)
```

4. If the cookie-assisted subtitle probe shows usable tracks, immediately prefer the same cookie file for subtitle download as well.
   Keep the raw timestamped subtitle file; do not flatten it too early.

5. If a cookie file is expected and the probe fails because the cookie is missing, expired, or authentication-gated, ask the user for a refreshed cookie before concluding that CC subtitles are unavailable.

### Metadata Inspection

1. Only after the cookie-first subtitle probe, inspect the video metadata.
   Prefer title, chapters, duration, thumbnail availability, and subtitle availability before writing.

2. Detect multi-part (分P) videos.
   List all parts and ask the user which parts to process before downloading.

### Subtitle Acquisition (Three-Level Fallback)

For English teaching, IELTS speaking, IELTS writing, TOEFL, pronunciation, grammar, vocabulary, or similar language-learning content, treat English wording as primary evidence:

- list available subtitles first and prefer `en`, `en-US`, `en-GB`, or other English tracks when they exist
- if English subtitles are unavailable or unusable, run local Whisper before relying on Chinese-only subtitles
- keep Chinese `zh`, `zh-Hans`, or `ai-zh` subtitles as auxiliary timing and meaning checks when useful
- preserve authentic phrases, collocations, sentence patterns, sample answers, examiner-style wording, and discourse markers in the final bilingual note

**Priority 1: CC subtitles (platform-embedded)**

Use manual subtitles over auto-generated subtitles when both are available.
Prefer `zh-Hans`, `zh-CN`, `zh`, or `ai-zh` subtitle tracks.
Preserve the subtitle timestamps; do not flatten subtitles into plain text too early if figures still need to be located.
On Bilibili, AI subtitles such as `ai-zh` may only appear when auto-generated subtitles are requested, so include `--write-auto-subs` alongside `--write-subs`.
When a cookie file is available, reuse it for subtitle download.

```bash
COOKIE_FILE="/path/to/www.bilibili.com_cookies.txt"
URL="https://www.bilibili.com/video/BVxxxxxxxxx/"

yt-dlp --cookies "$COOKIE_FILE" --no-playlist \
  --write-subs --write-auto-subs \
  --sub-langs "zh-Hans,zh-CN,zh,ai-zh" --convert-subs srt \
  --skip-download -o "%(title)s.%(ext)s" "$URL"
```

For English-learning videos, check and download English tracks first:

```bash
COOKIE_FILE="/path/to/www.bilibili.com_cookies.txt"
URL="https://www.bilibili.com/video/BVxxxxxxxxx/"

yt-dlp --cookies "$COOKIE_FILE" --no-playlist --list-subs "$URL"
yt-dlp --cookies "$COOKIE_FILE" --no-playlist \
  --write-subs --write-auto-subs \
  --sub-langs "en,en-US,en-GB,zh-Hans,zh-CN,zh,ai-zh" --convert-subs srt \
  --skip-download -o "%(title)s.%(ext)s" "$URL"
```

**Priority 2: Whisper speech-to-text (when no CC subtitles are available)**

Extract audio first, then transcribe with Whisper to produce a timestamped SRT file.

```
yt-dlp -x --audio-format wav -o "audio.%(ext)s" "<URL>"
whisper audio.wav --model medium --language zh --output_format srt --output_dir .
```

**Priority 3: Visual-only mode (when audio quality is too poor)**

Skip subtitles entirely and rely on dense frame sampling to extract teaching content from the video frames alone.

### Video and Cover Download

1. Acquire the video's original cover image before writing the `.tex`.
   Prefer the highest-resolution thumbnail exposed by the platform metadata.
   Save the selected cover locally and reference that local asset from the front page.

2. Prefer the best usable video source for figure extraction.
   Probe formats and choose the highest resolution that is actually downloadable in the current environment.
   Note that 1080P+ on Bilibili typically requires login cookies.

3. Keep all source artifacts local when practical.
   Typical working artifacts are metadata, the downloaded cover image, a timestamped subtitle file (CC or Whisper-generated), optional cleaned transcript text, a local video file, and extracted frames.

## Output Naming

Create the video output directory under `D:\Project\video2pdf\newskill-kimi\workspace` using the original Bilibili title plus the task start timestamp from the local machine timezone:

```text
D:\Project\video2pdf\newskill-kimi\workspace\{normalized-original-video-title}_{yyyyMMdd_HHmmss}
```

The `workspace` directory is the default parent for new Bilibili PDF outputs. Do not create new video output directories directly under `D:\Project\video2pdf\newskill-kimi` unless the user explicitly asks for a legacy/root-level location.

Normalize directory and final PDF names with the project whitelist: preserve Unicode letters and numbers, preserve only ASCII space and `_` as special characters, replace every other character with `_`, collapse repeated spaces and `_`, then trim leading or trailing spaces, `_`, and `.`. Shorten long titles while preserving the timestamp suffix for the output directory.

The final delivered PDF basename must come from the PDF article title when one exists, or the original video title when no separate article title exists. Apply the same normalization before appending `.pdf`.

### Windows and Sandbox Acquisition Safeguards

Use these safeguards when running inside this project workspace:

- copy cookie files from `C:\Users\juju\Downloads` into the output directory's `待删除` tree before passing them to `yt-dlp`; Bilibili cookie jars may be written back during use
- add `--no-cache-dir` to `yt-dlp` commands to avoid cache writes outside the workspace
- when a `.part` rename or merge rename fails, rerun the download with `--no-part`; if a complete `.temp.mp4` or format-specific media file already exists, validate it with `ffprobe` before reusing it
- when a downloaded merged temp file contains both video and audio streams, copy it to a stable workspace filename for downstream frame extraction
- keep the failed temp files in `待删除` for audit; permanent cleanup belongs to the user
- pass Chinese paths to Python tools through command-line arguments or environment variables; avoid embedding those paths inside stdin scripts because Windows console encodings may replace characters with `?`
- compile through the LaTeX Compile Guard with `scripts/compile_latex_ascii.py`; pass the engine path with `--engine` and keep direct engine shell calls out of the workflow

## Long Video Strategy

For longer videos, do not rely on a single monolithic pass.

- If the video is longer than 20 minutes, or the subtitle file contains more than 300 subtitle entries, split the work into smaller segments.
- Prefer chapter boundaries or 分P boundaries for splitting. If those are unavailable or too uneven, split by coherent time windows or subtitle ranges.
- When subagents are available, spawn multiple subagents in parallel for different segments so coverage stays high and detail is not lost.
- Give each subagent a concrete segment boundary and require it to return: the segment's teaching goal, the core claims, important formulas or code, required figures with time provenance, and any ambiguities that need integration-time resolution.
- Keep a small overlap between neighboring segments when the explanation crosses boundaries, then deduplicate during integration.
- The main agent must integrate the segment outputs into one unified outline and one coherent final narrative. The final PDF must read like a single lecture note, not a concatenation of chunk summaries.

## Pyramid Gate Workflow

Run the general Pyramid Gate during the single-video workflow. Batch orchestration remains out of scope here.

Canonical Pyramid Gate artifacts for each video output directory:

- `<video-name>\outline_contract.md`
- `<video-name>\section_*.tex`
- `<video-name>\main.tex`
- `<video-name>\review\pyramid\outline.pyramid.json`
- `<video-name>\review\pyramid\section_*.pyramid.json`
- `<video-name>\review\pyramid\main.pyramid.json`
- `<video-name>\review\pyramid\summary.md`

The JSON reports are the machine decision source. The output-level hook writes or refreshes `summary.md` as human review evidence after the JSON reports validate against the current source files.

### Outline Gate

After `<video-name>\outline_contract.md` exists and before any writer agent starts, run:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\evaluate_pyramid_text.py `
  "<video-name>\outline_contract.md" `
  "<video-name>\review\pyramid\outline.pyramid.json" `
  --artifact-type "outline_contract" `
  --context-label "outline" `
  --evaluation-context "Teaching-PDF outline checkpoint: validate that the Bilibili note outline states the teaching goal, chapter hierarchy, terminology contract, figure plan, and reader progression before writer agents start."

D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\validate_report.py `
  "<video-name>\review\pyramid\outline.pyramid.json" `
  --input-file "<video-name>\outline_contract.md" `
  --enforce-gate
```

If the outline report status is `needs_revision` or `blocked`, stop writer work. Revise `outline_contract.md`, rerun the evaluator, and continue only after validation exits successfully or explicit waiver evidence is recorded.

### Section Gate

After each `<video-name>\section_*.tex` exists and before integration, run the same checkpoint with the matching section stem. For example, for `section_01.tex`:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\evaluate_pyramid_text.py `
  "<video-name>\section_01.tex" `
  "<video-name>\review\pyramid\section_01.pyramid.json" `
  --artifact-type "tex_section" `
  --context-label "section_01" `
  --evaluation-context "Teaching-PDF section checkpoint: validate that this Bilibili chapter draft has a clear teaching claim, coherent subsection hierarchy, distinct same-level groups, source-grounded examples, and a reader-facing takeaway before integration."

D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\validate_report.py `
  "<video-name>\review\pyramid\section_01.pyramid.json" `
  --input-file "<video-name>\section_01.tex" `
  --enforce-gate
```

Repeat for every root-level `section_*.tex`. The `context_label` and report filename must use the same section stem, such as `section_02` and `section_02.pyramid.json`.

If any section report status is `needs_revision` or `blocked`, stop integration. Revise the failing section, rerun its evaluator, and continue only after validation exits successfully or explicit waiver evidence is recorded.

### Main Gate

After the integrated `<video-name>\main.tex` exists and before PDF compilation, run:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\evaluate_pyramid_text.py `
  "<video-name>\main.tex" `
  "<video-name>\review\pyramid\main.pyramid.json" `
  --artifact-type "tex_document" `
  --context-label "main" `
  --evaluation-context "Teaching-PDF main checkpoint: validate that the integrated Bilibili note has a conclusion-first document structure, coherent chapter sequence, complete teaching progression, aligned titles, and source-faithful synthesis before PDF compilation."

D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\validate_report.py `
  "<video-name>\review\pyramid\main.pyramid.json" `
  --input-file "<video-name>\main.tex" `
  --enforce-gate
```

If the main report status is `needs_revision` or `blocked`, stop PDF compilation. Revise `main.tex`, rerun the evaluator, and compile only after validation exits successfully or explicit waiver evidence is recorded.

### Waivers And Output-Level Check

A waiver is valid only when the workflow owner explicitly approves continuing despite a `needs_revision` or `blocked` report. The evaluator wrapper owns the waiver metadata:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\evaluate_pyramid_text.py `
  "<video-name>\main.tex" `
  "<video-name>\review\pyramid\main.pyramid.json" `
  --artifact-type "tex_document" `
  --context-label "main" `
  --evaluation-context "Teaching-PDF main checkpoint: validate that the integrated Bilibili note has a conclusion-first document structure, coherent chapter sequence, complete teaching progression, aligned titles, and source-faithful synthesis before PDF compilation." `
  --waiver-approved-by "<approver>" `
  --waiver-reason "<specific reason for continuing despite the report>"

D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\validate_report.py `
  "<video-name>\review\pyramid\main.pyramid.json" `
  --input-file "<video-name>\main.tex" `
  --enforce-gate `
  --allow-waiver
```

Run the output-level gate after the outline, every section, and main report exist, and before accepting the output as ready for PDF compilation or delivery:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\check_output_gate.py `
  "<video-name>" `
  --enforce-gate
```

If approved waivers are part of the evidence, the output-level check must opt into them:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\pyramid-principle-validate\scripts\check_output_gate.py `
  "<video-name>" `
  --enforce-gate `
  --allow-waivers
```

The output-level check validates source fingerprints, checkpoint identity metadata, required reports, and waiver metadata. It writes or refreshes `<video-name>\review\pyramid\summary.md`; the workflow must still treat the JSON reports as the authoritative gate decisions.

## Teaching Content Rules

Build the notes from all of the following when available:

- video title and chapter structure
- the video's original cover image and key metadata
- on-screen diagrams, formulas, tables, plots, and architecture slides
- subtitle explanations, examples, and verbal emphasis
- short high-signal original dialogue segments in interview, panel, podcast, or conversation videos, when the exact wording adds presence, humor, intuition, or unusually compact information
- code snippets shown or described in the talk

Skip content outside the actual lesson:

- greetings
- small talk
- routine back-and-forth that does not add information, tension, humor, intuition, or teaching value
- sponsorship
- channel logistics (一键三连, 关注投币, etc.)
- closing pleasantries

Keep the speaker's closing discussion when it carries actual teaching value, such as synthesis, limitations, future work, tradeoffs, advice, or open questions.

## Writing Rules

1. Write the notes in Chinese unless the user explicitly requests another language.

2. Organize the document with `\section{...}` and `\subsection{...}`.
   Reconstruct the teaching flow when needed; do not blindly mirror subtitle order.
   Each section should answer, in order when applicable: what problem is being solved, why simpler views are insufficient, what the core idea is, how it works, and what the reader should retain.

   Avoid overusing binary contrast sentence patterns that deny one framing and then pivot to another.
   Use it only when the video itself establishes a real contrast and that contrast materially clarifies the mechanism.

   Do not use vague or overly abstract phrasing.
   Ground claims in concrete mechanisms, examples, variables, steps, observed phenomena, timestamps, figures, or speaker-provided evidence whenever possible.

3. Start from `assets/notes-template.tex`.
   Fill in the metadata block, including the local cover image path, and replace the body content block with the generated notes.

4. The front page must include the video's original cover image when available.
   Place it on the first page rather than burying it later in the document.
   Keep it visually distinct from in-body teaching figures.

5. Use figures whenever they materially improve explanation.
   Include as many figures as are necessary for teaching clarity, even if that means many figures across the document.
   Do not optimize for a small figure count; optimize for explanatory coverage and readability.
   Good figures are key formulas, diagrams, tables, plots, visual comparisons, pipeline schedules, architecture views, and stage-by-stage visual progressions.

6. Do not place images inside custom message boxes.

7. When a mathematical formula appears:
   first explain in plain Chinese what the formula is trying to express and why it appears
   show it in display math using `$$...$$`
   then immediately follow with a flat list that explains every symbol

8. When code examples appear:
   explain the role of the code before the listing and summarize the expected behavior after it when useful
   wrap them in `lstlisting`
   include a descriptive `caption`

9. Highlight teaching signals deliberately and repeatedly when the content justifies it:
   use `importantbox` for core concepts the reader must walk away with, including formal definitions, central claims, key mechanism summaries, theorem-like statements, critical algorithm steps, and compact restatements of the main idea after a dense explanation
   use `knowledgebox` for background and side knowledge that improves understanding without being the main thread, including prerequisite reminders, historical lineage, engineering context, design tradeoffs, terminology comparisons, and intuition-building analogies
   use `warningbox` for common misunderstandings and failure points, including notation overload, hidden assumptions, misleading heuristics, easy-to-make implementation mistakes, causal confusions, off-by-one style reasoning errors, and places where the speaker contrasts a wrong intuition with the correct one
   use `dialoguebox` only for conversation-heavy videos when a brief original dialogue segment is high-information, funny, vivid, or especially intuitive, and preserving the speaker's wording gives the reader a stronger sense of being present in the discussion
   a `dialoguebox` may contain either one exchange or several tightly connected turns, such as a question, follow-up, pushback, clarification, and answer sequence
   keep `dialoguebox` snippets short: preserve speaker labels and a concrete timestamp or interval, lightly clean obvious ASR errors only when confident, and follow the box with prose that explains why the dialogue segment matters
   do not use `dialoguebox` for greetings, filler, long transcript dumps, or dialogue that would be clearer as ordinary summarized exposition
   there is no quota of one box per section; add multiple boxes in a section when the material contains multiple distinct teaching signals
   each box should carry a specific pedagogical payload rather than generic emphasis
   prefer placing a box immediately after the paragraph, derivation, or example that motivates it
   routine exposition should stay in normal prose; boxes are for high-signal takeaways, not decoration
   figures must stay outside `importantbox`, `knowledgebox`, `warningbox`, and `dialoguebox`

10. End every major section with `\subsection{本章小结}`.
    Add `\subsection{拓展阅读}` when there are one or two worthwhile external links.

11. End the document with a final top-level section such as `\section{总结与延伸}`.
    That final section must include:
    - the speaker's substantive closing discussion, excluding routine sign-off language
    - your own structured distillation of the core claims, mechanisms, and practical implications
    - your expanded synthesis, including conceptual compression, cross-links between sections, and any careful generalization that stays faithful to the video
    - concrete takeaways, open questions, or next steps when the material supports them

12. Do not emit `[cite]`-style placeholders anywhere in the LaTeX.

## Figure Handling

Select figures by necessity and teaching value, not by an arbitrary quota or a bias toward keeping the document visually sparse.

When locating candidate frames, bias strongly toward recall before precision.
It is better to inspect too many nearby candidates first than to miss the one frame where the slide, formula, table, or diagram is finally fully revealed and readable.

Frame understanding must come from direct visual inspection.

- Use the `view image` tool to inspect candidate frames and crops before deciding what they show, how they should be described, and whether they are complete enough to include.
- Do not use OCR tools such as `tesseract` as a substitute for visual understanding of a frame.
- Do not infer a frame's semantic content only from nearby subtitles, filenames, or timestamps without checking the image itself.
- Contact sheets, montages, and tiled strips are good for recall, but final keep-or-reject decisions and semantic naming must be based on actual image inspection with `view image`.

### Frame Selection Checklist

Before inserting any video frame, first inspect several nearby candidates from the same subtitle-aligned interval and apply this checklist. If any item fails, reject the frame and keep searching nearby rather than forcing an approximate match.

- Relevance: the frame must directly support the exact concept discussed in the surrounding paragraph or subsection, not just the same broad topic.
- Required content visible: every visual element referenced in the text must already be visible in the frame.
- Fully revealed state: when slides, whiteboards, animations, or dashboards build progressively, use the final fully populated readable state rather than an intermediate state.
- Best nearby candidate: compare multiple nearby frames and prefer the one that is both most complete and most readable.
- Readability: text, formulas, labels, and diagram structure must be legible enough to justify inclusion.

### Frame Naming

- Use neutral timestamp-based names for raw candidate frames. Do not assign semantic names before inspecting the actual frame content.
- Rename a frame semantically only after visually confirming what is fully visible in the image.
- The semantic filename must describe the frame's actual visible content, not a guess based on subtitles, nearby narration, or the intended paragraph topic.
- If the frame is partially revealed, transitional, or ambiguous, keep searching and do not lock in a semantic name yet.

- Use the timestamped subtitle file (CC or Whisper-generated SRT) as the primary locator for key-frame search.
- First identify the subtitle span that corresponds to the concept, example, formula, or visual explanation being discussed.
- Then search within that subtitle-aligned time interval, and slightly around its boundaries when needed, to find the best readable frame.
- Do not jump directly from one guessed timestamp to one extracted frame.
  First generate a dense candidate set across the relevant interval, then inspect and down-select.
- Prefer tools that help you inspect many nearby candidates at once, such as `magick montage`, contact sheets, tiled frame strips, or equivalent workflows.
  Use them to maximize recall and avoid missing the frame where the visual content is fully present.
- When the visual is a progressive PPT reveal, animation build, whiteboard accumulation, or dashboard state change, explicitly search for the final fully populated state.
  Do not stop at the first frame that seems approximately correct.
- If several nearby candidates differ only by progressive reveal state, keep checking until you find the frame with the most complete readable information.
- When in doubt between a sparse early frame and a denser later frame from the same explanation window, prefer the later frame if it is materially more complete and still readable.
- Include every figure that is necessary to explain the content well.
- It is acceptable, and often desirable, to include several figures within one section or subsection when the video builds an idea in stages.
- Omit repetitive or low-information frames.
- Extract frames near chapter boundaries and explanation peaks when chapters exist, but still validate them against subtitle timing.
- Search nearby timestamps when the first extracted frame catches an animation transition.
- Crop, enlarge, or isolate the relevant region when the full frame is too loose.
- When a slide reveals content progressively, capture the final readable state and add intermediate frames only when they teach a genuinely different step.
- For dense visual sections, it is acceptable to over-sample first and discard later.
  Do not optimize candidate count so early that key visual states are never inspected.
- Prefer a sequence of necessary figures over one overloaded figure with unreadable labels.
- Preserve readability of formulas and labels.

## Figure Time Provenance

Whenever the `.tex` or PDF references a specific video frame, or a crop derived from a video frame, record its source time interval on the same page as a bottom footnote.

- The footnote must show the concrete time interval, for example `00:12:31--00:12:46`.
- The interval should come from the subtitle-aligned segment used to locate the figure, not from a vague chapter-level estimate.
- If the figure is a crop, the footnote still refers to the original video time interval of the source frame or subtitle span.
- If several nearby frames in one figure all come from the same subtitle interval, one clear footnote is enough.
- Keep the figure and its time footnote anchored to the same page; prefer layouts such as `[H]`, a non-floating block, or another stable placement when ordinary floats would separate them.

## LaTeX Layout Guardrails

Treat layout as part of the deliverable, not a final cosmetic pass. Figure-heavy notes fail most often when large forced-position floats accumulate, then LaTeX moves the next text block to a later page and leaves a large blank region behind. Apply these rules before the first compile:

1. Do not place `\clearpage`, `\newpage`, `\pagebreak`, or `\vfill` inside generated chapter fragments or figure fragments. Only the main assembly may insert structural page breaks for the title page, table of contents, or a deliberately reviewed major boundary.

2. Use bounded image dimensions by default:
   `\includegraphics[width=0.76\linewidth,height=0.34\textheight,keepaspectratio]{...}`.
   For wide dense slides, allow up to `width=0.86\linewidth,height=0.42\textheight` only when the page contains one figure plus explanatory prose. For small crops, prefer `width=0.68\linewidth,height=0.30\textheight`.

3. Avoid three or more consecutive figure environments without prose between them. Interleave figures with explanatory text, or combine related frames into a compact subfigure layout when the comparison matters.

4. Prefer `figure` placement `[htbp]` for ordinary figures. Use `[H]` only when provenance footnotes or nearby explanation would become confusing if the float moved. Never combine repeated `[H]` figures with large image heights.

5. If a subsection needs many screenshots, keep each screenshot smaller and add one or two sentences of explanation between figure blocks. The goal is a readable learning rhythm: text establishes the question, the figure supplies evidence, the next text explains what to see.

6. After assembling `main.tex`, scan all included `.tex` fragments for forbidden layout commands and oversized graphics before compiling:
   `rg -n "\\clearpage|\\newpage|\\pagebreak|\\vfill|height=0\\.[5-9][0-9]*\\textheight|width=\\textwidth" . -g "*.tex"`.
   If matches appear in generated body or figure fragments, fix them before rendering unless they are intentional title-page or template code.

7. Run a guarded quick mode compile for temporary diagnostic compile checks, then run the bundled layout checker on the generated diagnostic PDF:
   `D:\Project\video2pdf\kimi\.venv\Scripts\python.exe <skill-dir>\scripts\check_pdf_layout.py "<diagnostic.pdf>" --max-bottom-blank 0.35`.
   If it flags pages, render those pages to images, inspect them, reduce figure sizes or remove forced placement, and compile again. Do not deliver a PDF while flagged pages remain unexplained.

## Visualization

For concepts that remain hard to explain with only screenshots and prose, add accurate visualizations.

Two acceptable routes:

- generate LaTeX-native visualizations with TikZ or PGFPlots
- generate figures ahead of time with scripts and include them as images

For script-generated illustrations, prefer Python tools such as `matplotlib` and `seaborn` when they are the clearest way to produce an accurate teaching figure.

When a visualization is generated externally rather than drawn natively in LaTeX:

- export the figure as `pdf` so it can be inserted into the `.tex` without rasterization loss
- prefer vector output for plots, charts, and schematic illustrations
- avoid `png` or `jpg` for script-generated teaching figures unless the content is inherently raster

When the source material contains relationships, results, or equations that would be clearer when redrawn than when shown as a screenshot, prefer rebuilding them with LaTeX-native tools or with `matplotlib` / `seaborn`.

Use visualizations for:

- process flows, pipelines, and architecture overviews
- curves and charts such as scaling laws, training curves, benchmark results, and ablation comparisons
- distributions, correlations, heatmaps, and other plots that explain data relationships
- complex functions, surfaces, contour plots, and geometric intuition figures
- tables or comparisons that become clearer when redrawn as charts
- summary diagrams that compress a section's core mechanism or takeaway into one figure

Do not add decorative graphics that do not teach anything.

## PDF Verification

Always compile through the LaTeX Compile Guard, then inspect the rendered PDF visually before delivery.

Use quick mode only as the temporary diagnostic compile path for TeX errors, layout investigation, and intermediate PDF inspection. Quick mode leaves its diagnostic `compile_report.json` under `待删除\latex-build\<run-id>\` and cannot satisfy final delivery.

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\bilibili-render-pdf\scripts\compile_latex_ascii.py `
  --mode quick `
  --tex "D:\Project\video2pdf\newskill-kimi\workspace\<video>\main.tex" `
  --engine "D:\kits\MiKTex\miktex\bin\x64\xelatex.exe"
```

Use final mode as the delivery compile path. Final mode writes the durable PDF and the latest final compile provenance report at `review\latex\compile_report.json`; `delivery_guard.py check` verifies that report before delivery.

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\bilibili-render-pdf\scripts\compile_latex_ascii.py `
  --mode final `
  --tex "D:\Project\video2pdf\newskill-kimi\workspace\<video>\main.tex" `
  --final-pdf "D:\Project\video2pdf\newskill-kimi\workspace\<video>\<normalized-title>.pdf" `
  --engine "D:\kits\MiKTex\miktex\bin\x64\xelatex.exe" `
  --source-skill "bilibili-render-pdf"
```

The wrapper copies TeX, section files, covers, and figure assets into a guarded build directory under the video output directory's `待删除\latex-build\` area, invokes the configured engine through structured arguments, enforces bounded runtime, writes logs, and preserves build evidence for audit. Raw direct engine calls are blocked workflow bypasses.

Use the bundled `scripts/check_pdf_layout.py` for a first pass when PyMuPDF is available:

```powershell
D:\Project\video2pdf\kimi\.venv\Scripts\python.exe .agents\skills\bilibili-render-pdf\scripts\check_pdf_layout.py "<final.pdf>" --max-bottom-blank 0.35
```

If `pdftoppm`, Poppler, or another renderer reports missing CJK maps such as `Adobe-GB1`, treat that renderer as unreliable for Chinese layout checking. Render with PyMuPDF instead, then inspect representative pages with `view_image`, especially pages containing tables, dense bilingual text, TikZ diagrams, video screenshots, and the final page.

When using Python to render a PDF whose path contains Chinese characters, pass the PDF path and output directory as command-line arguments or environment variables. This avoids stdin script encoding loss on Windows.

## Final Delivery Acceptance Gate

After PDF rendering and PDF verification, run the Final Delivery Acceptance Gate before delivery.

Required evidence paths:

- `docs/acceptance/acceptance_criteria.v1.json`
- `review/acceptance/allowed_artifacts_manifest.json`
- `review/acceptance/rendered_pages/`
- `review/acceptance/acceptance_report.json`
- optional `review/acceptance/acceptance_summary.md`

Use `.agents/skills/final-delivery-acceptance/scripts/render_pdf_pages.py` to render every final PDF page into `review/acceptance/rendered_pages/`. Create or refresh the allowed artifact manifest before launching the Acceptance Reviewer. The Acceptance Reviewer is read-only and uses only final delivered artifacts, the criteria file, the allowed manifest, and rendered page evidence.

`acceptance_report.json is the only machine-readable delivery decision source`. A missing, failed, malformed, stale, or forbidden-context report blocks final delivery.

If acceptance fails, use repair subagents to revise the affected TeX, figures, tables, or credibility caveat placement. Recompile or regenerate affected final artifacts, refresh rendered page evidence and stale upstream evidence, then run a fresh Acceptance Reviewer from the final-artifacts-only context.

Pyramid Gate and independent content review remain separate. Their passes never imply Final Delivery Acceptance pass.

### Guarded Target Lifecycle

The render workflow must create `.codex/delivery-targets/current.json` at `generating`, then create the video-level `review/acceptance/delivery_target.json` before final delivery. The lifecycle stages are `generating`, `ready_for_delivery`, `accepted`, `delivered`, `blocked`.

The video-level target records `attempt_limit: 3`, the final PDF, the main TeX file, `review/acceptance/allowed_artifacts_manifest.json`, `review/acceptance/acceptance_report.json`, and `review/acceptance/delivery_guard_report.json`. Newly generated video PDFs must also have final compile provenance at `review\latex\compile_report.json`. `acceptance_report.json is the only machine-readable delivery decision source`. `delivery_guard_report.json is a mechanical proof of freshness and contract validity`.

After rendering the final PDF, set the active target to `ready_for_delivery`, run the Acceptance Reviewer in a separate read-only role, and set the stage to `accepted` only after acceptance passes. If acceptance fails, run bounded repair with repair subagents, preserve attempt evidence under `review/acceptance/attempts/attempt_01/`, rerender or regenerate changed final artifacts, refresh rendered page evidence, and rerun a fresh Acceptance Reviewer. Continue through `attempt_02/` and `attempt_03/` only when needed. After the third failed attempt, write `review/acceptance/manual_repair_brief.md`, set the target to `blocked`, and stop delivery.

Before the final response, run `delivery_guard.py check`. Do not deliver this PDF until delivery_guard.py records a fresh pass. After successful delivery, clear `.codex/delivery-targets/current.json` so stale `delivered` state cannot affect later work.

The project Stop hook calls `delivery_guard.py hook-stop`, which may run `delivery_guard.py check` once for `ready_for_delivery` or `accepted`. The Stop hook must not launch the Acceptance Reviewer, repair subagents, page rendering, or LaTeX compilation. UserPromptSubmit remains out of scope.

Blocking text must include: Final Delivery Guard blocked delivery. Use a separate Acceptance Reviewer subagent and repair subagents. Do not deliver this PDF until delivery_guard.py records a fresh pass.

## Final Checklist

Before delivery, verify all of the following:

- `check_output_gate.py "<video-name>" --enforce-gate` has passed, or `--allow-waivers` has passed with explicit waiver evidence in the relevant JSON reports
- `<video-name>\review\pyramid\summary.md` is current, and the matching JSON reports remain the machine decision source
- `review\acceptance\allowed_artifacts_manifest.json` is current and lists the final delivered artifacts
- `review\acceptance\rendered_pages\page_0001.png` and subsequent page images cover every rendered PDF page
- `review\acceptance\acceptance_report.json` exists, validates against the current final artifact fingerprints, and reports `overall_status: "pass"`
- `review\latex\compile_report.json` exists, records `mode: "final"` and `status: "passed"`, and matches the current main TeX and final PDF
- `acceptance_report.json is the only machine-readable acceptance decision`; `acceptance_summary.md` is optional explanatory text
- missing, failed, malformed, stale, or forbidden-context report blocks final delivery
- no important teaching content has been dropped, and no concrete but critical detail has been lost during condensation, restructuring, or summarization
- the text and figures are aligned: each inserted frame supports the surrounding explanation, necessary crops have been applied, and the chosen frame shows the fullest relevant information rather than a transitional or incomplete state
- the document is visually rich enough for teaching: check whether more high-information key frames should be added, and whether additional LaTeX-native or Python-script-generated illustrations would improve clarity
- the LaTeX log has no hard errors and no unresolved layout warnings that affect the rendered document
- the generated PDF passes `scripts/check_pdf_layout.py` or every flagged page has been rendered and manually justified as an intentional section ending

## Delivery

Deliver all of the following:

- the Whisper-generated SRT subtitle file, if speech-to-text was used
- the downloaded cover image referenced on the front page
- any extracted or generated figure assets referenced by the document
- the final `.tex` file and the compiled `.pdf` file (must use a reasonable Chinese filename, e.g., `[中文视频标题]_notes.tex` and `[中文视频标题]_notes.pdf`)

## Asset

- `assets/notes-template.tex`: default LaTeX template to copy and fill
- `scripts/compile_latex_ascii.py`: compile a TeX file from ASCII staging and copy outputs back
- `scripts/check_pdf_layout.py`: post-compile PDF layout checker for blank-page and large-empty-region regressions

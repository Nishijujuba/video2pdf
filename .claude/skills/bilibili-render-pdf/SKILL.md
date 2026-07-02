---
name: bilibili-render-pdf
description: Generate a professional, detailed, figure-rich LaTeX course note and final PDF from a Bilibili lecture, tutorial, or technical talk. Use when the user provides a Bilibili URL (BV number) and wants structured Chinese teaching notes that combine the video's title, chapters, diagrams, formulas, code, subtitle explanations, the original video cover on the front page, and a final synthesis chapter, with key frames extracted from the highest usable video resolution and inserted as figures, and where the final deliverable must include a rendered PDF. Falls back to Whisper speech-to-text when no CC subtitles are available.
---

# Bilibili Render PDF

Use this skill to turn a Bilibili video into a complete, compileable `.tex` note and a rendered PDF.

This skill extends the `youtube-render-pdf` workflow with Bilibili-specific adaptations for subtitle scarcity, login-gated high resolution, multi-part (分P) videos, and platform-specific non-teaching content.

## Bilibili vs YouTube: Key Differences

| Aspect | Handling |
|--------|----------|
| **Subtitle scarcity** | Try CC subtitles first → fall back to Whisper speech-to-text → visual-only mode |
| **Login-gated HD** | 1080P+ requires cookies; prompt the user to use `yt-dlp --cookies "$COOKIE_FILE" ` |
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
- Whisper GPU transcriber for subtitle fallback (short audio <10min): `D:\Project\video2pdf\kimi\tools\whisper_gpu.bat`
  - It wraps `faster-whisper` (medium model + CUDA float16) via the shared `.venv` Python runtime.
  - Recommended invocation: `D:\Project\video2pdf\kimi\tools\whisper_gpu.bat audio.wav --format srt --language zh`
- **Whisper chunked transcriber for long audio (≥10min): `D:\Project\video2pdf\kimi\tools\whisper_chunked.bat`**
  - Automatically splits long audio into 5-minute chunks, transcribes each, and merges with correct timestamp offsets.
  - Uses `int8` compute type by default (more stable than `float16` for long audio).
  - Outputs real-time progress to `.whisper_log` in the working directory.
  - Automatically kills competing GPU processes before loading the model.
  - Recommended invocation: `D:\Project\video2pdf\kimi\tools\whisper_chunked.bat audio.wav --language zh -o audio.srt`
- `yt-dlp`: `D:\Project\video2pdf\kimi\tools\yt-dlp.exe`
- `ffmpeg`: `D:\Project\video2pdf\kimi\tools\ffmpeg\bin\ffmpeg.exe`
- `ffprobe`: `D:\Project\video2pdf\kimi\tools\ffmpeg\bin\ffprobe.exe`
- ImageMagick `magick`: `D:\Project\video2pdf\kimi\tools\imagemagick\magick.exe`
- `xelatex`: `D:\kits\MiKTex\miktex\bin\x64\xelatex.exe`
- `COOKIE_FILE`: `C:\Users\juju\Downloads\www.bilibili.com_cookies.txt`

Use the shared `kimi` uv environment as the default local runtime for Python-based helper work. When CC subtitles are unavailable, use the `whisper_gpu.bat` path above, which automatically invokes `faster-whisper` with the medium model on CUDA.

## Pedagogical Standard

The notes must read like a strong human teacher is guiding the reader through the material.

- organize each major section so the reader first understands the motivation, then the main idea, then the mechanism, then the example or evidence, and finally the takeaway
- be patient and explicit about logical transitions; make it clear why the speaker introduces a concept, what problem it solves, and how the next idea follows
- aim for deep-but-accessible explanations: keep the technical depth, but introduce formalism only after giving intuition in plain language
- when a section is dense, break it into smaller subsections that progressively build understanding rather than compressing everything into one long derivation
- do not dump subtitle content in chronological order; rewrite it into a teaching sequence with clear intent, contrast, and buildup

## Source Acquisition

### Metadata Inspection

1. Inspect the video metadata first.
   Prefer title, chapters, duration, thumbnail availability before writing.

   Do NOT use metadata to determine subtitle availability.
   Bilibili's metadata is unreliable about whether subtitles exist — especially
   for AI-generated (ai-zh) subtitles, which are commonly available but rarely
   listed in metadata. The only reliable way to check is to run the Priority 1
   subtitle download command (with cookies) and see if it produces files.

2. Detect multi-part (分P) videos.
   List all parts and ask the user which parts to process before downloading.



### Subtitle Acquisition (Three-Level Fallback)

The subtitle acquisition process is a strict sequential pipeline. You MUST
execute Priority 1 first and check its actual file output before deciding
whether to proceed to Priority 2. Never skip Priority 1 based on metadata,
assumptions, or partial information — Bilibili frequently provides AI-generated
Chinese subtitles that are not advertised in video metadata.

Decision rule: After running the Priority 1 command, list the working directory
for `.srt` or `.vtt` files. If any subtitle file was produced, use it and stop.
Only if zero subtitle files were produced, proceed to Priority 2.

**Priority 1: CC subtitles (platform-embedded) — always attempt this first**

This step is mandatory. Run the download command below regardless of what
metadata or any other source says about subtitle availability. Bilibili's
AI-generated subtitles (ai-zh track) are present on most videos but are
often invisible in metadata queries. The cookie file (`$COOKIE_FILE`) is
required — without it, Bilibili may not expose available subtitles.

After running the command, list the working directory for `.srt` or `.vtt`
files. If any subtitle file was produced, use it — prefer manual subtitles
over auto-generated when both exist, and prefer `zh-Hans` > `zh-CN` > `zh` >
`ai-zh` tracks.
Preserve the subtitle timestamps; do not flatten subtitles into plain text too early if figures still need to be located.

```
yt-dlp --cookies "$COOKIE_FILE" --write-subs  --write-auto-subs --sub-langs "zh-Hans,zh-CN,zh,ai-zh" --convert-subs srt \
  --skip-download -o "%(title)s.%(ext)s" "<URL>"
```



**Priority 2: Whisper speech-to-text (only if Priority 1 produced zero subtitle files)**

Before proceeding, verify: did the Priority 1 yt-dlp command produce any
`.srt` or `.vtt` files in the working directory? If yes, stop here and use
those files — do not run Whisper. Only if the answer is definitively no
(no subtitle files were created), proceed with audio extraction and Whisper
transcription below.

Extract audio first, then transcribe with the GPU Whisper wrapper to produce a timestamped SRT file.

**For short audio (<8 minutes):**
```
yt-dlp -x --audio-format wav -o "audio.%(ext)s" "<URL>"
D:\Project\video2pdf\kimi\tools\whisper_gpu.bat audio.wav --format srt --language zh
```

**For audio ≥8 minutes (strongly recommended):**
Use the chunked transcriber. It uses `int8` (more stable than `float16`), writes file logs, and saves per-chunk checkpoints so a crash does not lose progress.
```
yt-dlp -x --audio-format wav -o "audio.%(ext)s" "<URL>"
D:\Project\video2pdf\kimi\tools\whisper_chunked.bat audio.wav --language zh -o audio.srt
```

**Checkpoint / resume behavior:** `whisper_chunked.bat` automatically saves a `chunk_NNN.srt` checkpoint file after each chunk finishes. If the process is killed or hangs, re-run the exact same command and it will skip already-completed chunks. Checkpoints are cleaned up only after the final merged `audio.srt` is successfully written.

**Monitoring progress and detecting hangs:** Both scripts write real-time logs to `.whisper_log` in the working directory. Poll this file to check progress instead of relying on buffered stdout.
```
# In a separate terminal / polling loop:
tail -f .whisper_log
```
If `.whisper_log` shows no new chunk completion for **more than 15 minutes**, the faster-whisper generator has likely hung. Terminate the process and re-run the same command — checkpoint resume will pick up where it left off.

**Why chunking is necessary:** On this machine (RTX 3080 + Windows), loading the medium model with float16 on audio longer than ~10 minutes frequently hangs or silently crashes. The chunked script:
1. Splits audio into 5-minute segments via ffmpeg (merges the last segment if it is under 60 seconds)
2. Cleans up competing GPU processes before loading the model
3. Loads the model once with `int8` compute type (more stable than `float16`)
4. Transcribes each segment sequentially and saves a checkpoint after each one
5. Merges all SRT segments with correct timestamp offsets
6. Handles both standard `-->` and non-standard `-->` arrow formats (the latter occurs with some faster-whisper int8 builds)

**Priority 3: Visual-only mode (when audio quality is too poor)**

Skip subtitles entirely and rely on dense frame sampling to extract teaching content from the video frames alone.


### Video and Cover Download

1. **Validate cookie first.** Before downloading anything, run a quick `--list-formats` probe with cookies. If the command returns zero formats or only low-resolution options, the cookie has likely expired. Stop and ask the user to refresh the cookie file before proceeding.

2. Acquire the video's original cover image before writing the `.tex`.
   Prefer the highest-resolution thumbnail exposed by the platform metadata.
   Save the selected cover locally and reference that local asset from the front page.

3. Prefer the best usable video source for figure extraction.
   Probe formats and choose the highest resolution that is actually downloadable in the current environment.
   On Bilibili, use this strategy:
   - First try `30080+30216` (1080P + audio) — requires working cookies
   - If that fails, fall back to `30064+30216` (720P + audio)
   - If that also fails, fall back to `bestvideo[height<=720]+bestaudio/best`
   The specific format IDs above are the reliable Bilibili DASH codes on this host.

4. Keep all source artifacts local when practical.
   Typical working artifacts are metadata, the downloaded cover image, a timestamped subtitle file (CC or Whisper-generated), optional cleaned transcript text, a local video file, and extracted frames.

## Long Video Strategy

For longer videos, do not rely on a single monolithic pass. A single agent writing the full document from scratch accumulates unbounded context and produces increasingly incoherent output as it approaches its limits. The bigger risk is subtler: the main agent's judgment degrades when it is simultaneously tracking environment setup, outline decisions, frame extraction, and prose drafting all at once. Isolate concerns into roles.

### Role-based orchestration (preferred)

When subagents are available, split by **role** rather than by segment. Four roles cover a full lecture note:

1. **Outline agent** (1 instance, runs first). Reads all subtitles and the chapter list (or builds a chapter list from subtitle topics if Bilibili chapters are absent). Produces a complete global outline: section titles, subsection structure, a shared terminology table, a symbol legend, and the boundary timestamps for each chapter. This is the contract the other agents work against. The main agent must not start any other subagents until the outline agent returns.

2. **Writer agents** (one per chapter or one per 2–3 short chapters). Each receives the global outline, the subtitle slice for its chapters, and the terminology table. Produces a complete `.tex` fragment for its chapters — from the first `\section{}` to the `\subsection{本章小结}` — saved to disk as `section_N.tex`. Writers must not duplicate definitions or background that other sections own; the outline contract prevents this.

3. **Figure agents** (one per chapter, or merged with the writer when the chapter is short). Responsible only for locating, extracting, inspecting, cropping, and writing figure `\includegraphics{}` blocks with captions and footnotes. Figures must not be inserted by the writer until the figure agent confirms the local file path and a semantic description of what the frame actually shows. Running figure agents in parallel with writers is acceptable as long as figure paths are confirmed before the writer finalises its section.

4. **Consistency agent** (1 instance, runs after all writers complete). Reads all `section_*.tex` files and checks: duplicate term definitions, notation that shifts between sections, forward references to concepts introduced later than assumed, and chapter transitions that leave a logical gap. Returns a diff-style list of fixes. The main agent applies the fixes before assembly.

The main agent's role is orchestration: issue the outline subagent, distribute the contracts, collect the section files, apply the consistency fixes, assemble the final `.tex`, and compile.

### Segment-based fallback

If role-based orchestration is not available (no subagents or too many context limits), fall back to segment-based monolithic passes:

- Split at chapter boundaries, or at coherent time windows if chapters are missing.
- Keep a small overlap between neighboring segments when an explanation crosses the boundary.
- Deduplicate the overlap during integration.
- Integrate segment outputs into one unified narrative; the final document must read like a single lecture note, not concatenated summaries.

## Teaching Content Rules

Build the notes from all of the following when available:

- video title and chapter structure
- the video's original cover image and key metadata
- on-screen diagrams, formulas, tables, plots, and architecture slides
- subtitle explanations, examples, and verbal emphasis
- code snippets shown or described in the talk

Skip content that does not contribute to the actual lesson:

- greetings
- small talk
- sponsorship
- channel logistics (一键三连, 关注投币, etc.)
- closing pleasantries

Keep the speaker's closing discussion when it carries actual teaching value, such as synthesis, limitations, future work, tradeoffs, advice, or open questions.

## Writing Rules

1. Write the notes in Chinese unless the user explicitly requests another language.

2. Organize the document with `\section{...}` and `\subsection{...}`.
   Reconstruct the teaching flow when needed; do not blindly mirror subtitle order.
   Each section should answer, in order when applicable: what problem is being solved, why simpler views are insufficient, what the core idea is, how it works, and what the reader should retain.

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
   there is no quota of one box per section; add multiple boxes in a section when the material contains multiple distinct teaching signals
   each box should carry a specific pedagogical payload rather than generic emphasis
   prefer placing a box immediately after the paragraph, derivation, or example that motivates it
   routine exposition should stay in normal prose; boxes are for high-signal takeaways, not decoration
   figures must stay outside `importantbox`, `knowledgebox`, and `warningbox`

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

## Final Checklist

Before delivery, verify all of the following:

- no important teaching content has been dropped, and no concrete but critical detail has been lost during condensation, restructuring, or summarization
- the text and figures are aligned: each inserted frame supports the surrounding explanation, necessary crops have been applied, and the chosen frame shows the fullest relevant information rather than a transitional or incomplete state
- the document is visually rich enough for teaching: check whether more high-information key frames should be added, and whether additional LaTeX-native or Python-script-generated illustrations would improve clarity

## Delivery

Deliver all of the following:

- the final `.tex` file
- the downloaded cover image referenced on the front page
- any extracted or generated figure assets referenced by the document
- the compiled PDF
- the Whisper-generated SRT subtitle file, if speech-to-text was used

## Asset

- `assets/notes-template.tex`: default LaTeX template to copy and fill

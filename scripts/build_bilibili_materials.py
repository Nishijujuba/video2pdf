from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


TIME_RE = re.compile(
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})"
)


@dataclass
class Cue:
    index: int
    start: float
    end: float
    text: str


def parse_time(value: str) -> float:
    match = TIME_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Bad SRT timestamp: {value!r}")
    groups = {key: int(val) for key, val in match.groupdict().items()}
    return (
        groups["h"] * 3600
        + groups["m"] * 60
        + groups["s"]
        + groups["ms"] / 1000
    )


def fmt_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_clock(seconds: float) -> str:
    total = int(round(seconds))
    s = total % 60
    total //= 60
    m = total % 60
    h = total // 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_srt(path: Path) -> list[Cue]:
    blocks = re.split(r"\r?\n\r?\n+", path.read_text(encoding="utf-8").strip())
    cues: list[Cue] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            index = int(lines[0])
        except ValueError:
            index = len(cues) + 1
        start_raw, end_raw = [part.strip() for part in lines[1].split("-->", 1)]
        text = " ".join(lines[2:]).strip()
        cues.append(Cue(index=index, start=parse_time(start_raw), end=parse_time(end_raw), text=text))
    return cues


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: build_bilibili_materials.py <target-folder>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    source = root / "source"
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)

    metadata = json.loads((source / "metadata.info.json").read_text(encoding="utf-8"))
    cues = parse_srt(source / "subtitles.ai-zh.srt")
    chapters = metadata.get("chapters") or [
        {"title": "Full video", "start_time": 0, "end_time": metadata.get("duration", cues[-1].end)}
    ]

    transcript_lines = [
        "# Full Timestamped Transcript",
        "",
        f"- Source: `{(source / 'subtitles.ai-zh.srt').as_posix()}`",
        f"- Cue count: {len(cues)}",
        "",
    ]
    subtitle_index = []
    for cue in cues:
        transcript_lines.append(
            f"{cue.index}. `{fmt_clock(cue.start)}--{fmt_clock(cue.end)}` {cue.text}"
        )
        subtitle_index.append(
            {"index": cue.index, "start": cue.start, "end": cue.end, "text": cue.text}
        )
    write_markdown(work / "transcript_full.md", "\n".join(transcript_lines) + "\n")
    (work / "subtitle_index.json").write_text(
        json.dumps(subtitle_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = [
        "# Metadata Summary",
        "",
        f"- Title: {metadata.get('title', '')}",
        f"- Bilibili ID: {metadata.get('id', '')}",
        f"- Uploader: {metadata.get('uploader', '')}",
        f"- Upload date: {metadata.get('upload_date', '')}",
        f"- Duration: {fmt_clock(float(metadata.get('duration') or 0))}",
        f"- URL: {metadata.get('webpage_url', '')}",
        f"- Thumbnail: {metadata.get('thumbnail', '')}",
        f"- Local cover: `assets/cover.jpg`",
        f"- Local video: `source/video.mp4`",
        f"- Local subtitle: `source/subtitles.ai-zh.srt`",
        "",
        "## Chapters",
        "",
    ]

    for idx, chapter in enumerate(chapters, start=1):
        start = float(chapter.get("start_time", 0))
        end = float(chapter.get("end_time", metadata.get("duration", start)))
        title = chapter.get("title", f"Chapter {idx}")
        chapter_cues = [
            cue for cue in cues if cue.end >= start - 5 and cue.start <= end + 5
        ]
        file_name = f"chapter_{idx:02d}_transcript.md"
        summary.append(f"{idx}. `{fmt_clock(start)}--{fmt_clock(end)}` {title} -> `{file_name}`")
        lines = [
            f"# Chapter {idx}: {title}",
            "",
            f"- Time window: `{fmt_clock(start)}--{fmt_clock(end)}`",
            f"- Cue count with 5s overlap: {len(chapter_cues)}",
            "",
            "## Timestamped Subtitle Slice",
            "",
        ]
        for cue in chapter_cues:
            lines.append(f"- `{fmt_clock(cue.start)}--{fmt_clock(cue.end)}` {cue.text}")
        write_markdown(work / file_name, "\n".join(lines) + "\n")

    write_markdown(work / "metadata_summary.md", "\n".join(summary) + "\n")
    print(f"Wrote materials to {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

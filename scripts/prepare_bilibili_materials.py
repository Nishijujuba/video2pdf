from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path


SRT_BLOCK = re.compile(
    r"(?:^|\n)(\d+)\s*\n"
    r"(\d\d:\d\d:\d\d,\d{3})\s*-->\s*(\d\d:\d\d:\d\d,\d{3})\s*\n"
    r"(.*?)(?=\n\d+\s*\n\d\d:\d\d:\d\d,\d{3}\s*-->|\Z)",
    re.S,
)


def timestamp_to_seconds(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def clean_subtitle_text(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(without_tags).split())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--srt", default="source.ai-zh.srt")
    parser.add_argument("--metadata", default="source.info.json")
    args = parser.parse_args()

    root = args.root
    materials = root / "materials"
    materials.mkdir(exist_ok=True)

    srt_text = (root / args.srt).read_text(encoding="utf-8-sig")
    segments = []
    for index, start, end, text in SRT_BLOCK.findall(srt_text):
        clean = clean_subtitle_text(text)
        if not clean:
            continue
        segments.append(
            {
                "index": int(index),
                "start": start,
                "end": end,
                "start_seconds": timestamp_to_seconds(start),
                "end_seconds": timestamp_to_seconds(end),
                "text": clean,
            }
        )

    (materials / "transcript_segments.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    transcript_lines = ["# Timestamped transcript", "", f"- segments: {len(segments)}", ""]
    for segment in segments:
        transcript_lines.append(
            f"[{segment['start']}--{segment['end']}] {segment['text']}"
        )
    (materials / "transcript.md").write_text("\n".join(transcript_lines), encoding="utf-8")

    metadata = json.loads((root / args.metadata).read_text(encoding="utf-8"))
    summary = {
        key: metadata.get(key)
        for key in [
            "id",
            "title",
            "fulltitle",
            "duration",
            "duration_string",
            "upload_date",
            "uploader",
            "webpage_url",
            "description",
            "thumbnail",
        ]
    }
    summary["chapters"] = metadata.get("chapters") or []
    summary["local_cover"] = "source.jpg"
    summary["local_video"] = "source_video.mp4"
    summary["subtitle_file"] = args.srt
    (materials / "metadata_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "segments": len(segments),
                "first": segments[0] if segments else None,
                "last": segments[-1] if segments else None,
                "title": summary.get("title"),
                "duration": summary.get("duration"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

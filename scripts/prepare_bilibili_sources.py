from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(r"D:\Project\video2pdf\newskill-kimi\大模型胡说八道？用第一性原理拆解Attention+FFN")
OUT = ROOT / "sources"


TIME_RE = re.compile(
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})\s*-->\s*"
    r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2}),(?P<ms2>\d{3})"
)


def parse_time(groups: dict[str, str], suffix: str = "") -> float:
    return (
        int(groups[f"h{suffix}"]) * 3600
        + int(groups[f"m{suffix}"]) * 60
        + int(groups[f"s{suffix}"])
        + int(groups[f"ms{suffix}"]) / 1000
    )


def fmt_time(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{(whole % 3600) // 60:02d}:{whole % 60:02d}"


def parse_srt(path: Path, part: str) -> list[dict[str, str | float | int]]:
    text = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\n\s*\n", text.strip())
    rows: list[dict[str, str | float | int]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        index_line = lines[0]
        time_line = lines[1] if "-->" in lines[1] else ""
        if not time_line:
            continue
        match = TIME_RE.search(time_line)
        if not match:
            continue
        groups = match.groupdict()
        body = " ".join(lines[2:]).strip()
        if not body:
            continue
        try:
            index = int(index_line)
        except ValueError:
            index = len(rows) + 1
        start = parse_time(groups)
        end = parse_time(groups, "2")
        rows.append(
            {
                "part": part,
                "index": index,
                "start": start,
                "end": end,
                "start_time": fmt_time(start),
                "end_time": fmt_time(end),
                "text": body,
            }
        )
    return rows


def load_metadata() -> list[dict[str, object]]:
    metadata = []
    for path in sorted(ROOT.glob("*.info.json")):
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        metadata.append(
            {
                "file": path.name,
                "id": data.get("id"),
                "title": data.get("title"),
                "duration": data.get("duration"),
                "duration_string": data.get("duration_string"),
                "playlist_index": data.get("playlist_index"),
                "thumbnail": data.get("thumbnail"),
                "uploader": data.get("uploader"),
                "upload_date": data.get("upload_date"),
                "resolution": data.get("resolution"),
                "format_id": data.get("format_id"),
            }
        )
    return metadata


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    srt_files = sorted(ROOT.glob("*.srt"))
    all_rows: list[dict[str, str | float | int]] = []
    for path in srt_files:
        part = "p02" if "p02" in path.name or "02_" in path.name else "p01"
        rows = parse_srt(path, part)
        all_rows.extend(rows)
        with (OUT / f"subtitles_{part}.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["part", "index", "start_time", "end_time", "text"],
                delimiter="\t",
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "part": row["part"],
                        "index": row["index"],
                        "start_time": row["start_time"],
                        "end_time": row["end_time"],
                        "text": row["text"],
                    }
                )

    with (OUT / "combined_subtitles.txt").open("w", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(
                f"[{row['part']} {row['start_time']}--{row['end_time']}] {row['text']}\n"
            )

    metadata = load_metadata()
    (OUT / "metadata_summary.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_lines = [
        "# Source Summary",
        "",
        "## Metadata",
    ]
    for item in metadata:
        if not item.get("duration_string"):
            continue
        summary_lines.append(
            f"- {item.get('id')}: {item.get('duration_string')}, "
            f"{item.get('resolution')}, upload_date={item.get('upload_date')}, "
            f"format={item.get('format_id')}"
        )
    summary_lines.extend(
        [
            "",
            "## Subtitle Files",
            *[f"- {path.name}" for path in srt_files],
            "",
            "## Parsed Subtitle Rows",
        ]
    )
    for part in sorted({str(row["part"]) for row in all_rows}):
        count = sum(1 for row in all_rows if row["part"] == part)
        summary_lines.append(f"- {part}: {count} rows")

    (OUT / "source_summary.md").write_text(
        "\n".join(summary_lines) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

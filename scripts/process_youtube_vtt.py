import html
import json
import re
import sys
from pathlib import Path


def ts_to_sec(ts: str) -> float:
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
    else:
        h, m, s = "0", parts[0], parts[1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def sec_to_ts(sec: float) -> str:
    sec = max(0, float(sec))
    h = int(sec // 3600)
    sec -= h * 3600
    m = int(sec // 60)
    sec -= m * 60
    return f"{h:02d}:{m:02d}:{sec:05.2f}"


def clean_text(raw: str) -> str:
    raw = re.sub(r"<\d\d:\d\d:\d\d\.\d\d\d>", " ", raw)
    raw = re.sub(r"</?c[^>]*>", " ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*|[^\sA-Za-z0-9]", text)


def detok(tokens: list[str]) -> str:
    text = ""
    no_space_before = set(".,;:!?%)]}’”")
    no_space_after = set("([{“")
    for token in tokens:
        if not text:
            text = token
        elif token in no_space_before:
            text += token
        elif text[-1:] in no_space_after:
            text += token
        else:
            text += " " + token
    return text


def read_vtt(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "-->" in line:
            start, rest = line.split("-->", 1)
            end = rest.strip().split()[0]
            i += 1
            texts = []
            while i < len(lines) and lines[i].strip():
                texts.append(lines[i])
                i += 1
            text = clean_text("\n".join(texts))
            if text:
                entries.append(
                    {
                        "start": ts_to_sec(start.strip()),
                        "end": ts_to_sec(end.strip()),
                        "text": text,
                    }
                )
        i += 1
    return entries


def dedupe_rolling_captions(entries: list[dict]) -> tuple[list[dict], list[str]]:
    all_tokens: list[str] = []
    clean_entries = []
    for entry in entries:
        tokens = tokenize(entry["text"])
        max_overlap = min(len(tokens), len(all_tokens), 100)
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if all_tokens[-size:] == tokens[:size]:
                overlap = size
                break
        new_tokens = tokens[overlap:]
        if not new_tokens:
            continue
        all_tokens.extend(new_tokens)
        clean_entries.append(
            {
                "start": entry["start"],
                "end": entry["end"],
                "start_ts": sec_to_ts(entry["start"]),
                "end_ts": sec_to_ts(entry["end"]),
                "text": detok(new_tokens),
                "full_cue": entry["text"],
            }
        )
    return clean_entries, all_tokens


def make_windows(clean_entries: list[dict], window_seconds: float = 90.0) -> list[dict]:
    windows = []
    cur_start = None
    cur_end = None
    buffer = []

    for entry in clean_entries:
        if cur_start is None:
            cur_start = entry["start"]
            cur_end = cur_start + window_seconds
        if entry["start"] >= cur_end and buffer:
            windows.append(
                {
                    "start": cur_start,
                    "end": cur_end,
                    "start_ts": sec_to_ts(cur_start),
                    "end_ts": sec_to_ts(cur_end),
                    "text": detok(tokenize(" ".join(buffer))),
                }
            )
            while entry["start"] >= cur_end:
                cur_start = cur_end
                cur_end += window_seconds
            buffer = []
        buffer.append(entry["text"])

    if buffer and cur_start is not None:
        end = clean_entries[-1]["end"]
        windows.append(
            {
                "start": cur_start,
                "end": end,
                "start_ts": sec_to_ts(cur_start),
                "end_ts": sec_to_ts(end),
                "text": detok(tokenize(" ".join(buffer))),
            }
        )
    return windows


def main() -> int:
    if len(sys.argv) != 5:
        print(
            "usage: process_youtube_vtt.py input.vtt transcript.txt segments.json entries.json",
            file=sys.stderr,
        )
        return 2

    vtt_path = Path(sys.argv[1])
    transcript_path = Path(sys.argv[2])
    segments_path = Path(sys.argv[3])
    entries_path = Path(sys.argv[4])

    raw_entries = read_vtt(vtt_path)
    clean_entries, all_tokens = dedupe_rolling_captions(raw_entries)
    windows = make_windows(clean_entries)

    transcript_parts = []
    for idx, window in enumerate(windows, 1):
        transcript_parts.append(
            f"[{idx:02d}] {window['start_ts']} -- {window['end_ts']}\n{window['text']}\n"
        )
    transcript_path.write_text("\n".join(transcript_parts), encoding="utf-8")
    segments_path.write_text(json.dumps(windows, ensure_ascii=False, indent=2), encoding="utf-8")
    entries_path.write_text(
        json.dumps(clean_entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    word_count = len([token for token in all_tokens if re.search(r"[A-Za-z0-9]", token)])
    print(
        f"raw_cues={len(raw_entries)} clean_entries={len(clean_entries)} "
        f"windows={len(windows)} words={word_count}"
    )
    print(transcript_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

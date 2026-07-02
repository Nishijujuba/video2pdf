#!/usr/bin/env python3
"""Plan and optionally apply normalized workspace moves for video PDF artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


WORKSPACE_NAME = "workspace"
LOW_CONFIDENCE_NAME = "低置信目录"

EXCLUDED_DIRS = {
    WORKSPACE_NAME,
    "待删除",
    ".agents",
    ".codex",
    ".claude",
    ".git",
    ".cache",
    ".venvs",
    ".uv-cache-qwen3-asr",
    "docs",
    "scripts",
    "agent_reports",
    "figure_blocks",
    "figure_scripts",
    "work",
    "__pycache__",
}

GENERIC_PDF_STEMS = {
    "main",
    "main_fixed",
    "output",
    "article",
    "note",
    "document",
    "final",
}

DELIVERED_GENERIC_PDF_STEMS = {
    "main",
    "notes",
}

TEXT_IDENTITY_FILES = (
    "main.tex",
    "outline_contract.md",
    "source.info.json",
    "video.info.json",
    "metadata.json",
)

PLAN_FIELDS = (
    "source_name",
    "source_path",
    "target_name",
    "target_relative",
    "target_path",
    "confidence",
    "reason",
    "needs_review",
    "artifact_date",
    "date_source",
    "identity_title",
    "identity_source",
    "series_name",
    "episode_number",
    "pdf_count",
    "main_pdf",
)


def sanitize_windows_name(value: str, fallback: str = "video", limit: int = 120) -> str:
    text = normalize_spaces(value)
    if not text:
        text = fallback
    text = "".join(ch if (ch.isalnum() or ch in {" ", "_"}) else "_" for ch in text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"_+", "_", text).strip(" _.")
    if len(text) > limit:
        text = text[:limit].rstrip(" _.")
    return text or fallback


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def read_text_limited(path: Path, limit: int = 262_144) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def strip_latex_commands(value: str) -> str:
    text = value.replace(r"\&", "&").replace(r"\_", "_")
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", text)
    text = text.replace("{", "").replace("}", "")
    return normalize_spaces(text)


def clean_title(value: str) -> str:
    text = strip_latex_commands(value)
    text = re.sub(r"^Outline\s+Contract\s*[:：-]\s*", "", text, flags=re.I)
    text = re.sub(r"^(课程笔记|视频课程笔记|中文笔记|笔记|PDF|文章|标题)\s*[:：-]\s*", "", text, flags=re.I)
    text = re.sub(r"\s*(中文笔记|课程笔记|notes?)$", "", text, flags=re.I)
    if re.search(r"(在此填写|TODO|TBD)", text, flags=re.I):
        return ""
    return normalize_spaces(text)


def clean_directory_title(value: str) -> str:
    text = strip_latex_commands(value)
    if re.search(r"(在此填写|TODO|TBD)", text, flags=re.I):
        return ""
    return normalize_spaces(text)


def extract_braced_value(text: str, command: str) -> str | None:
    marker = f"\\{command}"
    start = text.find(marker)
    if start < 0:
        return None
    brace = text.find("{", start + len(marker))
    if brace < 0:
        return None
    depth = 0
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1 : index]
    return None


def title_from_main_tex(path: Path) -> tuple[str, str] | None:
    text = read_text_limited(path)
    if not text:
        return None
    for pattern in (
        r"\\newcommand\{\\notetitle\}\{(?P<title>.*?)\}",
        r"\\renewcommand\{\\notetitle\}\{(?P<title>.*?)\}",
        r"\\def\\notetitle\{(?P<title>.*?)\}",
    ):
        match = re.search(pattern, text, re.S)
        if match:
            title = clean_title(match.group("title"))
            if title:
                return title, path.name
    title_value = extract_braced_value(text, "title")
    if title_value:
        title = clean_title(title_value)
        if title:
            return title, path.name
    return None


def title_from_markdown(path: Path) -> tuple[str, str] | None:
    text = read_text_limited(path)
    if not text:
        return None
    for pattern in (
        r"(?im)^\s*(?:PDF\s*)?(?:Article|Video)?\s*Title\s*[:：]\s*(?P<title>.+)$",
        r"(?im)^\s*(?:文章|视频|文档|PDF)\s*(?:标题|名|名称)\s*[:：]\s*(?P<title>.+)$",
        r"(?im)^#\s+(?P<title>.+)$",
    ):
        match = re.search(pattern, text)
        if match:
            title = clean_title(match.group("title"))
            if title and title.lower() not in {"outline", "outline contract"}:
                return title, path.name
    return None


def title_from_json(path: Path) -> tuple[str, str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("article_title", "pdf_title", "note_title", "title", "fulltitle", "video_title"):
        value = data.get(key)
        if isinstance(value, str):
            title = clean_title(value)
            if title:
                return title, path.name
    return None


def pdf_candidates(video_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    try:
        candidates.extend(path for path in video_dir.glob("*.pdf") if path.is_file())
        build_dir = video_dir / "build"
        if build_dir.is_dir():
            candidates.extend(path for path in build_dir.glob("*.pdf") if path.is_file())
    except OSError:
        return []
    return sorted(set(candidates))


def has_main_pdf(video_dir: Path) -> bool:
    return any(pdf.name.lower() == "main.pdf" for pdf in pdf_candidates(video_dir))


def relative_source(video_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(video_dir).as_posix()
    except ValueError:
        return path.name


def title_from_pdf_name(video_dir: Path) -> tuple[str, str] | None:
    pdfs = pdf_candidates(video_dir)
    named = [path for path in pdfs if sanitize_windows_name(path.stem).lower() not in GENERIC_PDF_STEMS]
    if not named:
        return None
    named.sort(key=lambda path: (path.stat().st_size if path.exists() else 0, len(path.stem)), reverse=True)
    title = clean_title(named[0].stem)
    return (title, relative_source(video_dir, named[0])) if title else None


def identity_evidence(video_dir: Path) -> tuple[str | None, str | None, bool, int, bool]:
    main_tex = video_dir / "main.tex"
    if main_tex.exists():
        title = title_from_main_tex(main_tex)
        if title:
            return title[0], title[1], True, len(pdf_candidates(video_dir)), has_main_pdf(video_dir)

    pdf_title = title_from_pdf_name(video_dir)
    if pdf_title:
        return pdf_title[0], pdf_title[1], True, len(pdf_candidates(video_dir)), has_main_pdf(video_dir)

    for name in ("outline_contract.md",):
        path = video_dir / name
        if path.exists():
            title = title_from_markdown(path)
            if title:
                return title[0], title[1], True, len(pdf_candidates(video_dir)), has_main_pdf(video_dir)

    for name in ("source.info.json", "video.info.json", "metadata.json"):
        path = video_dir / name
        if path.exists():
            title = title_from_json(path)
            if title:
                return title[0], title[1], True, len(pdf_candidates(video_dir)), has_main_pdf(video_dir)

    pdfs = pdf_candidates(video_dir)
    main_pdf = video_dir / "main.pdf"
    main_pdfs = [pdf for pdf in pdfs if pdf.name.lower() == "main.pdf"]
    if main_pdfs:
        return clean_directory_title(video_dir.name), relative_source(video_dir, main_pdfs[0]), True, len(pdfs), True

    for pdf in pdfs:
        if sanitize_windows_name(pdf.stem).lower() in DELIVERED_GENERIC_PDF_STEMS:
            title = clean_directory_title(video_dir.name)
            if title:
                return title, relative_source(video_dir, pdf), True, len(pdfs), bool(main_pdfs)

    return None, None, False, len(pdfs), False


def durable_artifacts(video_dir: Path) -> list[Path]:
    artifacts: list[Path] = []
    for name in TEXT_IDENTITY_FILES:
        path = video_dir / name
        if path.is_file():
            artifacts.append(path)
    artifacts.extend(pdf_candidates(video_dir))
    pyramid_dir = video_dir / "review" / "pyramid"
    if pyramid_dir.is_dir():
        artifacts.extend(sorted(path for path in pyramid_dir.glob("*.json") if path.is_file()))
    return artifacts


def artifact_date(video_dir: Path) -> tuple[str | None, str | None]:
    artifacts = durable_artifacts(video_dir)
    if not artifacts:
        return None, None
    earliest = min(artifacts, key=lambda path: path.stat().st_mtime)
    try:
        relative = earliest.relative_to(video_dir).as_posix()
    except ValueError:
        relative = earliest.name
    return earliest.stat().st_mtime_ns, relative


def format_artifact_date(timestamp_ns: int | None) -> str:
    if timestamp_ns is None:
        return ""
    from datetime import datetime

    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000).strftime("%Y%m%d")


def episode_from_name(name: str) -> tuple[str, str] | None:
    patterns = (
        r"^(?P<series>.+?)[ _-]+[pP](?P<num>\d{1,3})(?:\b|[_ -]).*$",
        r"^(?P<series>.+?)[ _-]+(?:EP|Ep|ep)(?P<num>\d{1,3})(?:\b|[_ -]).*$",
        r"^(?P<series>.+?)[ _-]+Lecture\s*(?P<num>\d{1,3})(?:\b|[_ -]).*$",
        r"^(?P<series>.+?)(?P<num>\d{1,2})$",
    )
    for pattern in patterns:
        match = re.match(pattern, name)
        if match:
            series = sanitize_windows_name(match.group("series"), fallback="series")
            number = str(int(match.group("num"))).zfill(2)
            return series, number
    return None


def cluster_series_prefixes(candidates: list[Path]) -> set[str]:
    prefixes: list[str] = []
    for path in candidates:
        match = re.match(r"^(?P<prefix>[^_-]{3,}?)[_-].+", path.name)
        if match:
            prefix = sanitize_windows_name(match.group("prefix"), fallback="")
            if len(prefix) >= 3:
                prefixes.append(prefix)
    counts = Counter(prefixes)
    return {prefix for prefix, count in counts.items() if count >= 2}


def fallback_series_prefix(name: str, prefixes: set[str]) -> str | None:
    for prefix in sorted(prefixes, key=len, reverse=True):
        if name.startswith(prefix + "-") or name.startswith(prefix + "_"):
            return prefix
    return None


def target_for_high_confidence(
    source_name: str,
    title: str,
    date_text: str,
    series_prefixes: set[str],
) -> tuple[str, str, str]:
    episode = episode_from_name(source_name)
    if episode:
        series, number = episode
        return f"{series}_{number}_{date_text}", series, number

    prefix = fallback_series_prefix(source_name, series_prefixes)
    if prefix:
        title_part = sanitize_windows_name(title, fallback=source_name)
        return f"{prefix}_{title_part}_{date_text}", prefix, ""

    title_part = sanitize_windows_name(title, fallback=source_name)
    return f"{title_part}_{date_text}", "", ""


def low_confidence_row(root: Path, source_dir: Path, reason: str, pdf_count: int = 0, main_pdf: bool = False) -> dict[str, Any]:
    target = root / WORKSPACE_NAME / LOW_CONFIDENCE_NAME / source_dir.name
    return {
        "source_name": source_dir.name,
        "source_path": str(source_dir),
        "target_name": source_dir.name,
        "target_relative": f"{WORKSPACE_NAME}/{LOW_CONFIDENCE_NAME}/{source_dir.name}",
        "target_path": str(target),
        "confidence": "low",
        "reason": reason,
        "needs_review": True,
        "artifact_date": "",
        "date_source": "",
        "identity_title": "",
        "identity_source": "",
        "series_name": "",
        "episode_number": "",
        "pdf_count": pdf_count,
        "main_pdf": main_pdf,
    }


def candidate_dirs(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and path.name not in EXCLUDED_DIRS
    )


def build_plan(root: str | Path) -> list[dict[str, Any]]:
    root_path = Path(root).resolve()
    candidates = candidate_dirs(root_path)
    series_prefixes = cluster_series_prefixes(candidates)
    rows: list[dict[str, Any]] = []

    for source_dir in candidates:
        title, identity_source, valid, pdf_count, main_pdf = identity_evidence(source_dir)
        date_ns, date_source = artifact_date(source_dir)
        date_text = format_artifact_date(date_ns)
        if not valid or not title:
            rows.append(low_confidence_row(root_path, source_dir, "missing final-delivery identity", pdf_count, main_pdf))
            continue
        if not date_text:
            rows.append(low_confidence_row(root_path, source_dir, "missing durable artifact date", pdf_count, main_pdf))
            continue

        target_name, series_name, episode_number = target_for_high_confidence(
            source_dir.name,
            title,
            date_text,
            series_prefixes,
        )
        target_name = sanitize_windows_name(target_name, fallback=source_dir.name)
        target = root_path / WORKSPACE_NAME / target_name
        rows.append(
            {
                "source_name": source_dir.name,
                "source_path": str(source_dir),
                "target_name": target_name,
                "target_relative": f"{WORKSPACE_NAME}/{target_name}",
                "target_path": str(target),
                "confidence": "high",
                "reason": "final-delivery identity found",
                "needs_review": False,
                "artifact_date": date_text,
                "date_source": date_source or "",
                "identity_title": title,
                "identity_source": identity_source or "",
                "series_name": series_name,
                "episode_number": episode_number,
                "pdf_count": pdf_count,
                "main_pdf": main_pdf,
            }
        )

    mark_conflicts(root_path, rows)
    return rows


def mark_conflicts(root: Path, rows: list[dict[str, Any]]) -> None:
    high_targets = Counter(row["target_path"] for row in rows if row["confidence"] == "high")
    for row in rows:
        if row["confidence"] != "high":
            continue
        if high_targets[row["target_path"]] <= 1 and not Path(row["target_path"]).exists():
            continue
        source_name = row["source_name"]
        row.update(low_confidence_row(root, Path(row["source_path"]), "target path conflict", row["pdf_count"], row["main_pdf"]))
        row["source_name"] = source_name


def write_plan(root: str | Path, rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    root_path = Path(root).resolve()
    workspace = root_path / WORKSPACE_NAME
    workspace.mkdir(parents=True, exist_ok=True)
    json_path = workspace / "migration-plan.json"
    csv_path = workspace / "migration-plan.csv"
    try:
        write_plan_files(rows, csv_path, json_path)
        return csv_path, json_path
    except PermissionError:
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = workspace / f"migration-plan_{suffix}.json"
        csv_path = workspace / f"migration-plan_{suffix}.csv"
        write_plan_files(rows, csv_path, json_path)
        return csv_path, json_path


def write_plan_files(rows: list[dict[str, Any]], csv_path: Path, json_path: Path) -> None:
    # Write CSV first because it is the file most often left open in spreadsheet tools.
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PLAN_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in PLAN_FIELDS})
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def rename_directory(source: Path, target: Path) -> None:
    source.rename(target)


def apply_plan(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    for row in rows:
        source = Path(row["source_path"])
        target = Path(row["target_path"])
        if not source.exists():
            raise RuntimeError(f"source does not exist: {source}")
        if target.exists():
            raise RuntimeError(f"target already exists: {target}")

    errors: list[dict[str, str]] = []
    for row in rows:
        source = Path(row["source_path"])
        target = Path(row["target_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            rename_directory(source, target)
        except OSError as exc:
            errors.append(
                {
                    "source_path": str(source),
                    "target_path": str(target),
                    "error": str(exc),
                }
            )
    return errors


def write_apply_errors(root: str | Path, errors: list[dict[str, str]]) -> Path | None:
    if not errors:
        return None
    root_path = Path(root).resolve()
    workspace = root_path / WORKSPACE_NAME
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "migration-errors.json"
    path.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def summarize(rows: list[dict[str, Any]]) -> str:
    high = sum(1 for row in rows if row["confidence"] == "high")
    low = sum(1 for row in rows if row["confidence"] == "low")
    return f"planned {len(rows)} directories: {high} high confidence, {low} low confidence"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Project root to scan. Defaults to the current directory.")
    parser.add_argument("--apply", action="store_true", help="Move directories according to the generated plan.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    rows = build_plan(root)
    csv_path, json_path = write_plan(root, rows)
    print(summarize(rows))
    print(f"wrote {csv_path}")
    print(f"wrote {json_path}")
    if args.apply:
        errors = apply_plan(rows)
        error_path = write_apply_errors(root, errors)
        moved = len(rows) - len(errors)
        print(f"applied directory moves: {moved} moved, {len(errors)} errors")
        if error_path:
            print(f"wrote {error_path}")
    else:
        print("dry run only; pass --apply to move directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

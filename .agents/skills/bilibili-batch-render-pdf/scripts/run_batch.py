from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_OUTPUT_ROOT = Path(r"D:\Project\video2pdf\newskill-kimi")
DEFAULT_COOKIE_FILE = Path(r"C:\Users\juju\Downloads\www.bilibili.com_cookies.txt")
DEFAULT_VENV_PYTHON = Path(r"D:\Project\video2pdf\kimi\.venv\Scripts\python.exe")
DEFAULT_TOOLS_DIR = Path(r"D:\Project\video2pdf\kimi\tools")
DEFAULT_XELATEX = Path(r"D:\kits\MiKTex\miktex\bin\x64\xelatex.exe")
TRASH_DIR_NAME = "待删除"
DEFAULT_CODEX_CONFIG = ["service_tier='fast'"]
DEFAULT_PART_RESULT_SCHEMA = (
    Path(__file__).resolve().parents[1] / "references" / "part-result.schema.json"
)
DEFAULT_PYRAMID_OUTPUT_GATE = (
    Path(__file__).resolve().parents[2]
    / "pyramid-principle-validate"
    / "scripts"
    / "check_output_gate.py"
)
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

COOKIE_FAILURE_PATTERNS = [
    "cookie expired",
    "cookies expired",
    "cookie rejected",
    "cookies rejected",
    "login required",
    "please login",
    "not logged in",
    "403 forbidden",
    "401 unauthorized",
    "http error 403",
    "http error 401",
    "登录",
    "未登录",
    "过期",
    "失效",
]

CODEX_APP_SERVER_FAILURE_PATTERNS = [
    "failed to initialize in-process app-server client",
    "in-process app-server permission error",
    "app-server permission error",
    "app-server",
]

STATUS_PLANNED = "planned"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
STATUS_SKIPPED = "skipped"

_manifest_lock = threading.Lock()


def utc_now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def task_start_timestamp(now: _dt.datetime | None = None) -> str:
    current = now or _dt.datetime.now().astimezone()
    return current.strftime(TIMESTAMP_FORMAT)


def sanitize_windows_name(value: str, fallback: str = "bilibili-batch", limit: int = 80) -> str:
    text = (value or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"\s+", " ", text)
    text = "".join(ch if (ch.isalnum() or ch in {" ", "_"}) else "_" for ch in text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"_+", "_", text).strip(" _.")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
    if text.upper() in reserved:
        text = f"{text}_"
    if len(text) > limit:
        text = text[:limit].rstrip(" _.")
    return text or fallback


def timestamped_output_name(
    title: str,
    *,
    timestamp: str,
    fallback: str,
    limit: int = 96,
) -> str:
    suffix = f"_{timestamp}"
    title_limit = max(1, limit - len(suffix))
    safe_title = sanitize_windows_name(title, fallback=fallback, limit=title_limit)
    return f"{safe_title}{suffix}"


def part_url(source_url: str, index: int) -> str:
    if re.search(r"([?&])p=\d+", source_url):
        return re.sub(r"([?&])p=\d+", rf"\1p={index}", source_url, count=1)
    separator = "&" if "?" in source_url else "?"
    return f"{source_url}{separator}p={index}"


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        fail(f"manifest not found: {path}")
    except json.JSONDecodeError as exc:
        fail(f"manifest is invalid JSON: {path}: {exc}")


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temp_path.write_text(payload, encoding="utf-8")
    try:
        os.replace(temp_path, path)
    except PermissionError:
        # Some managed Windows directories reject atomic replace even when normal writes work.
        # Keep the temp file for audit instead of deleting it.
        path.write_text(payload, encoding="utf-8")


def summarize_manifest(manifest: dict[str, Any]) -> dict[str, int]:
    summary = {
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "blocked": 0,
        "running": 0,
        "planned": 0,
        "skipped": 0,
    }
    for item in manifest.get("items", []):
        summary["total"] += 1
        status = item.get("status")
        if status in summary:
            summary[status] += 1
    return summary


def write_item_status(item: dict[str, Any]) -> None:
    status_path = item.get("status_path")
    if status_path:
        write_json_atomic(Path(status_path), dict(item))


def check_inputs_for_enumeration(args: argparse.Namespace) -> None:
    if not args.url:
        return
    if not Path(args.venv_python).exists():
        fail(f"yt-dlp Python runtime is missing: {args.venv_python}")
    if not Path(args.cookie_file).exists():
        fail(f"Bilibili cookie file is missing: {args.cookie_file}")


def split_extra_args(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(shlex.split(value, posix=False))
    return result


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def prepare_cookie_file(args: argparse.Namespace) -> None:
    if not args.localize_cookie_file:
        return
    source = Path(args.cookie_file)
    if not source.exists():
        return

    output_root = Path(args.out_root).resolve()
    if path_is_within(source, output_root):
        args.cookie_file = source.resolve()
        return

    cookie_dir = output_root / TRASH_DIR_NAME / "bilibili-batch-cookies"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    destination = cookie_dir / source.name
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        fail(f"could not copy Bilibili cookie file into workspace cache: {exc}")
    args.cookie_file = destination.resolve()
    print(f"Using localized cookie file: {args.cookie_file}")


def resolve_codex_executable(value: str) -> str:
    if os.name == "nt" and value.lower() == "codex":
        cmd_path = shutil.which("codex.cmd")
        if cmd_path:
            return cmd_path
    resolved = shutil.which(value)
    return resolved or value


def run_yt_dlp_metadata(args: argparse.Namespace) -> dict[str, Any]:
    check_inputs_for_enumeration(args)
    cmd = [
        str(args.venv_python),
        "-m",
        "yt_dlp",
        "--no-cache-dir",
        "--dump-single-json",
        "--flat-playlist",
        "--cookies",
        str(args.cookie_file),
    ]
    if args.proxy:
        cmd.extend(["--proxy", args.proxy])
    cmd.extend(split_extra_args(args.yt_dlp_arg or []))
    cmd.append(args.url)

    print("Enumerating Bilibili parts with yt-dlp...")
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(args.out_root),
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if contains_cookie_failure(stderr):
            fail("Bilibili cookies appear expired or rejected. Refresh the cookie file before continuing.")
        fail(f"yt-dlp metadata enumeration failed with exit code {proc.returncode}:\n{stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        fail(f"yt-dlp returned invalid JSON: {exc}")


def build_manifest_from_url(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    metadata = run_yt_dlp_metadata(args)
    output_root = Path(args.out_root).resolve()
    started_at = task_start_timestamp()
    batch_title = args.batch_name or metadata.get("title") or "bilibili-batch"
    batch_name = timestamped_output_name(str(batch_title), timestamp=started_at, fallback="bilibili-batch")
    batch_dir = output_root / batch_name
    control_dir = batch_dir / "batch-control"
    entries = metadata.get("entries") or []

    if not entries:
        entries = [
            {
                "title": metadata.get("title") or batch_name,
                "webpage_url": args.url,
                "url": args.url,
                "id": metadata.get("id"),
            }
        ]

    items: list[dict[str, Any]] = []
    seen_dirs: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        title = str(entry.get("title") or f"P{index:02d}")
        safe_title = timestamped_output_name(title, timestamp=started_at, fallback=f"P{index:02d}", limit=80)
        part_dir_name = f"P{index:02d}_{safe_title}"
        if part_dir_name.lower() in seen_dirs:
            part_dir_name = f"{part_dir_name}_{index}"
        seen_dirs.add(part_dir_name.lower())

        raw_url = entry.get("webpage_url") or entry.get("url")
        if isinstance(raw_url, str) and raw_url.startswith(("http://", "https://")):
            item_url = raw_url
        else:
            item_url = part_url(args.url, index)

        output_dir = batch_dir / part_dir_name
        part_id_seed = str(entry.get("id") or metadata.get("id") or batch_name)
        part_id = f"{sanitize_windows_name(part_id_seed, fallback='BV', limit=32)}_p{index:03d}"
        items.append(
            {
                "part_id": part_id,
                "index": index,
                "page": index,
                "bvid": metadata.get("id"),
                "id": entry.get("id"),
                "title": title,
                "part_title": title,
                "url": item_url,
                "source_url": args.url,
                "output_dir": str(output_dir),
                "status": STATUS_PLANNED,
                "attempt": 0,
                "attempts": 0,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "started_at": None,
                "ended_at": None,
                "duration_ms": None,
                "pid": None,
                "exit_code": None,
                "failure_class": "none",
                "error_message": "",
                "retryable": False,
                "next_action": "",
                "pdf_path": "",
                "tex_path": "",
                "artifact_checks": {
                    "pdf_exists": False,
                    "pdf_size_bytes": 0,
                    "tex_exists": False,
                    "compile_log_exists": False,
                },
                "raw_final_response": {},
                "prompt_path": str(control_dir / "prompts" / f"P{index:02d}.md"),
                "log_path": str(control_dir / "logs" / f"P{index:02d}.jsonl"),
                "last_message_path": str(control_dir / "last-messages" / f"P{index:02d}.json"),
                "status_path": str(control_dir / "parts" / part_id / "status.json"),
                "output_schema_path": str(DEFAULT_PART_RESULT_SCHEMA),
            }
        )

    manifest = {
        "schema_version": 1,
        "kind": "bilibili-batch-render-pdf",
        "source_url": args.url,
        "batch_name": batch_name,
        "batch_title": batch_title,
        "task_start_timestamp": started_at,
        "output_root": str(output_root),
        "batch_dir": str(batch_dir),
        "control_dir": str(control_dir),
        "cookie_file": str(args.cookie_file),
        "venv_python": str(args.venv_python),
        "tools_dir": str(DEFAULT_TOOLS_DIR),
        "xelatex": str(DEFAULT_XELATEX),
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "driver_version": "bilibili-batch-render-pdf@1",
        "items": items,
        "summary": {},
    }
    manifest_path = control_dir / "manifest.json"
    return manifest_path, manifest


def ensure_manifest_paths(manifest: dict[str, Any], manifest_path: Path) -> None:
    control_dir = Path(manifest.get("control_dir") or manifest_path.parent)
    for item in manifest.get("items", []):
        index = int(item["index"])
        part_id = item.get("part_id") or f"part_p{index:03d}"
        output_dir = Path(item["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / TRASH_DIR_NAME).mkdir(parents=True, exist_ok=True)
        item.setdefault("page", index)
        item.setdefault("part_id", part_id)
        item.setdefault("part_title", item.get("title", ""))
        item.setdefault("attempt", item.get("attempts", 0))
        item.setdefault("started_at", item.get("last_started_at"))
        item.setdefault("ended_at", item.get("last_finished_at"))
        item.setdefault("duration_ms", None)
        item.setdefault("pid", None)
        item.setdefault("exit_code", None)
        item.setdefault("failure_class", "none")
        item.setdefault("error_message", item.get("last_error", ""))
        item.setdefault("retryable", False)
        item.setdefault("next_action", "")
        item.setdefault("pdf_path", "")
        item.setdefault("tex_path", "")
        if not isinstance(item.get("artifact_checks"), dict) or not item.get("artifact_checks"):
            item["artifact_checks"] = {
                "pdf_exists": False,
                "pdf_size_bytes": 0,
                "tex_exists": False,
                "compile_log_exists": False,
            }
        item.setdefault("raw_final_response", {})
        item.setdefault("prompt_path", str(control_dir / "prompts" / f"P{index:02d}.md"))
        item.setdefault("log_path", str(control_dir / "logs" / f"P{index:02d}.jsonl"))
        item.setdefault("last_message_path", str(control_dir / "last-messages" / f"P{index:02d}.json"))
        item.setdefault("status_path", str(control_dir / "parts" / part_id / "status.json"))
        item.setdefault("output_schema_path", str(DEFAULT_PART_RESULT_SCHEMA))
        Path(item["prompt_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(item["log_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(item["last_message_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(item["status_path"]).parent.mkdir(parents=True, exist_ok=True)


def make_child_prompt(item: dict[str, Any], manifest: dict[str, Any]) -> str:
    index = int(item["index"])
    return f"""Use the local `bilibili-render-pdf` skill for exactly one selected Bilibili part.

Critical instruction sources:
- Read and obey `AGENTS.md` in the current workspace.
- Read and obey `.agents/skills/bilibili-render-pdf/SKILL.md`.
- Project instructions override any weaker default inside the skill.

Selected part:
- Batch source URL: {manifest.get("source_url")}
- Part index: P{index:02d}
- Part title: {item.get("title")}
- Part URL: {item.get("url")}
- Output directory: {item.get("output_dir")}
- Bilibili cookie file: {manifest.get("cookie_file")}
- Skill virtual environment: {manifest.get("venv_python")}
- Skill tool directory: {manifest.get("tools_dir")}
- XeLaTeX executable: {manifest.get("xelatex")}

Scope:
- Process only P{index:02d}. If the Bilibili metadata exposes multiple parts again, stay on this assigned part.
- Produce one independent PDF for this part.
- Place all generated artifacts for this part under the output directory above.
- Name the final delivered PDF from the PDF article title when one exists, or the original part/video title when no separate article title exists.
- Apply the project name whitelist to the final PDF basename: preserve Unicode letters and numbers, preserve only ASCII space and `_` as special characters, replace every other character with `_`, collapse repeated spaces and `_`, then trim leading or trailing spaces, `_`, and `.`.
- Ensure the output directory contains a `待删除` subfolder.
- Put disposable intermediates under `待删除`.
- Never permanently delete files.

Required multi-subagent workflow:
- Spawn an outline agent before chapter writing begins. It must define the table of contents, terminology, symbol table, chapter boundaries, writing contract, and cross-section conventions.
- Spawn one or more writer agents according to chapter count. Writer agents must write complete drafts directly and save them as `section_*.tex`.
- Spawn one or more figure agents. Figure agents own frame extraction, image selection, cropping, generated diagrams or scripts, captions, and timestamp footnotes.
- Spawn a consistency agent to check duplicate definitions, terminology drift, weak transitions, missing cross-references, and unclear notation.
- After the first PDF is delivered, spawn an independent review agent. It must compare the TeX draft against the original subtitle files and require revisions until the content is complete enough.

Mandatory Pyramid Gate:
- Use `.agents/skills/pyramid-principle-validate/SKILL.md`.
- Run it after `outline_contract.md`, after every `section_*.tex`, and after integrated `main.tex`.
- Write reports under `review\\pyramid\\`: `outline.pyramid.json`, `section_*.pyramid.json`, `main.pyramid.json`, and `summary.md`.
- After writing each report, run `.agents\\skills\\pyramid-principle-validate\\scripts\\validate_report.py <report-json> --enforce-gate`.
- The batch supervisor will also run `.agents\\skills\\pyramid-principle-validate\\scripts\\check_output_gate.py <output-dir> --enforce-gate` during reconcile. Missing or failing reports prevent success.

Content rules:
- Use English while collecting materials, reasoning, planning, and organizing intermediate results.
- Prefer English subtitles first. If English subtitles are unavailable or unusable, follow the Bilibili skill fallback path.
- Write the final PDF content in Chinese.
- For English teaching, IELTS speaking, IELTS writing, or similar language-learning content, preserve useful original English and make the PDF bilingual where helpful.
- Preserve subtitle timestamps for figure lookup and review.
- Use the original cover image on the front page when available.
- Inspect frames and crops visually before using them.
- Every video-derived figure needs concrete subtitle-aligned timestamp provenance.
- Start from `.agents/skills/bilibili-render-pdf/assets/notes-template.tex`.
- Produce a complete compileable `.tex` file and a rendered `.pdf`.

Compilation notes:
- The required final `.tex` and `.pdf` must end up under the assigned output directory.
- Compile with `.agents\\skills\\bilibili-render-pdf\\scripts\\compile_latex_ascii.py` and the configured MiKTeX XeLaTeX executable. On this Windows host, running MiKTeX directly inside Chinese/non-ASCII output paths can fail during MiKTeX FNDB or temp-file maintenance before TeX is read.
- The helper creates an ASCII staging directory under `work\\latex_ascii_*`, copies TeX/section/cover/figure assets, runs XeLaTeX twice there, copies PDF and compile logs back under the assigned output directory, and preserves staging for audit.
- Do not point `MIKTEX_USERDATA`, `MIKTEX_USERCONFIG`, `MIKTEX_USERINSTALL`, `TEMP`, or `TMP` at the Chinese output directory for compilation. That can create locked MiKTeX maintenance files and block later moves.

Stop condition:
- If the cookie file is expired, rejected, or insufficient for the required source, stop this child task and say that refreshed Bilibili cookies are required.

Final response format:
Return only JSON that matches the provided output schema. Include: part index, part title, status, output directory, final `.tex` path, final `.pdf` path, subtitle source, figure count, independent review result, unresolved issues, failure_class, error_message, and a short summary.
"""


def write_prompts(manifest: dict[str, Any]) -> None:
    for item in manifest.get("items", []):
        prompt_path = Path(item["prompt_path"])
        prompt_path.write_text(make_child_prompt(item, manifest), encoding="utf-8")


def contains_cookie_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return any(pattern in lowered for pattern in COOKIE_FAILURE_PATTERNS)


def contains_codex_app_server_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return any(pattern in lowered for pattern in CODEX_APP_SERVER_FAILURE_PATTERNS)


def has_codex_app_server_history(manifest: dict[str, Any]) -> bool:
    for item in manifest.get("items", []):
        raw_final = item.get("raw_final_response")
        raw_final_text = ""
        if isinstance(raw_final, dict):
            raw_final_text = json.dumps(raw_final, ensure_ascii=False)
        fields = [
            str(item.get("failure_class") or ""),
            str(item.get("error_message") or ""),
            str(item.get("next_action") or ""),
            raw_final_text,
        ]
        if any(contains_codex_app_server_failure(field) for field in fields):
            return True
        if item.get("failure_class") == "codex_cli":
            try:
                if contains_codex_app_server_failure(load_log_text(item)):
                    return True
            except OSError:
                continue
    return False


def review_artifact_checks(output_dir: Path) -> dict[str, Any]:
    consistency_path = output_dir / "review" / "consistency_review.md"
    independent_path = output_dir / "review" / "independent_review.md"
    checks = {
        "consistency_review_exists": consistency_path.exists(),
        "consistency_review_path": str(consistency_path) if consistency_path.exists() else "",
        "independent_review_exists": independent_path.exists(),
        "independent_review_path": str(independent_path) if independent_path.exists() else "",
    }
    checks.update(pyramid_gate_artifact_checks(output_dir))
    return checks


def pyramid_gate_artifact_checks(output_dir: Path) -> dict[str, Any]:
    review_dir = output_dir / "review" / "pyramid"
    result: dict[str, Any] = {
        "pyramid_review_dir_exists": review_dir.exists(),
        "pyramid_gate_passed": False,
        "pyramid_gate_script": str(DEFAULT_PYRAMID_OUTPUT_GATE),
        "pyramid_gate_stdout": "",
        "pyramid_gate_stderr": "",
        "pyramid_gate_exit_code": None,
    }
    if not DEFAULT_PYRAMID_OUTPUT_GATE.exists():
        result["pyramid_gate_stderr"] = f"missing gate script: {DEFAULT_PYRAMID_OUTPUT_GATE}"
        return result
    completed = subprocess.run(
        [
            sys.executable,
            str(DEFAULT_PYRAMID_OUTPUT_GATE),
            str(output_dir),
            "--enforce-gate",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    result["pyramid_gate_stdout"] = completed.stdout
    result["pyramid_gate_stderr"] = completed.stderr
    result["pyramid_gate_exit_code"] = completed.returncode
    result["pyramid_gate_passed"] = completed.returncode == 0
    return result


def missing_reconcile_requirements(checks: dict[str, Any], *, min_pdf_bytes: int, require_reviews: bool) -> list[str]:
    missing: list[str] = []
    if not checks.get("pdf_exists"):
        missing.append("pdf")
    elif int(checks.get("pdf_size_bytes") or 0) < min_pdf_bytes:
        missing.append(f"pdf_size>={min_pdf_bytes}")
    if not checks.get("tex_exists"):
        missing.append("tex")
    if require_reviews:
        if not checks.get("consistency_review_exists"):
            missing.append("review/consistency_review.md")
        if not checks.get("independent_review_exists"):
            missing.append("review/independent_review.md")
    if not checks.get("pyramid_gate_passed"):
        missing.append("review/pyramid reports passing --enforce-gate")
    return missing


def reconcile_items(
    manifest_path: Path,
    manifest: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    min_pdf_bytes: int = 1024,
    require_reviews: bool = True,
) -> list[dict[str, Any]]:
    reconciled: list[dict[str, Any]] = []
    for item in items:
        final = load_final_response(item)
        checks = artifact_checks(item, final)
        checks.update(review_artifact_checks(Path(item["output_dir"])))
        missing = missing_reconcile_requirements(
            checks,
            min_pdf_bytes=min_pdf_bytes,
            require_reviews=require_reviews,
        )
        if missing:
            update_item(
                manifest_path,
                manifest,
                item,
                artifact_checks=checks,
                next_action="manual artifact reconciliation incomplete: missing " + ", ".join(missing),
            )
            print(f"[P{int(item['index']):02d}] not reconciled: missing {', '.join(missing)}")
            continue

        raw_final = final if isinstance(final, dict) and not final.get("_malformed") else {}
        raw_final.setdefault("status", STATUS_SUCCEEDED)
        raw_final.setdefault("summary", "Manual completion reconciled from verified artifacts.")
        raw_final["_manual_reconciled"] = True
        update_item(
            manifest_path,
            manifest,
            item,
            status=STATUS_SUCCEEDED,
            ended_at=item.get("ended_at") or utc_now(),
            exit_code=item.get("exit_code"),
            pdf_path=checks.get("pdf_path", ""),
            tex_path=checks.get("tex_path", ""),
            artifact_checks=checks,
            raw_final_response=raw_final,
            failure_class="none",
            error_message="",
            retryable=False,
            next_action="",
        )
        print(f"[P{int(item['index']):02d}] reconciled from existing artifacts")
        reconciled.append(item)
    return reconciled


def load_log_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("log_path", "last_message_path"):
        path = Path(item.get(key) or "")
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def load_final_response(item: dict[str, Any]) -> dict[str, Any]:
    path = Path(item.get("last_message_path") or "")
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"_malformed": True, "raw_text": text[:4000]}
    if isinstance(parsed, dict):
        return parsed
    return {"_malformed": True, "raw_value": parsed}


def newest_file(root: Path, pattern: str, *, include_trash: bool = False) -> Path | None:
    if not root.exists():
        return None
    candidates = []
    for path in root.rglob(pattern):
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            relative_parts = path.parts
        if include_trash or TRASH_DIR_NAME not in relative_parts:
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def path_from_final(final: dict[str, Any], key: str) -> str:
    value = final.get(key)
    return value if isinstance(value, str) else ""


def artifact_checks(item: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
    output_dir = Path(item["output_dir"])
    pdf_path = Path(path_from_final(final, "pdf_path")) if path_from_final(final, "pdf_path") else None
    tex_path = Path(path_from_final(final, "tex_path")) if path_from_final(final, "tex_path") else None

    if pdf_path is None or not pdf_path.exists():
        pdf_path = newest_file(output_dir, "*.pdf")
    if tex_path is None or not tex_path.exists():
        tex_path = newest_file(output_dir, "*.tex")

    log_path = newest_file(output_dir, "*.log", include_trash=True)
    pdf_size = pdf_path.stat().st_size if pdf_path and pdf_path.exists() else 0
    return {
        "pdf_exists": bool(pdf_path and pdf_path.exists()),
        "pdf_size_bytes": pdf_size,
        "tex_exists": bool(tex_path and tex_path.exists()),
        "compile_log_exists": bool(log_path and log_path.exists()),
        "pdf_path": str(pdf_path) if pdf_path else "",
        "tex_path": str(tex_path) if tex_path else "",
        "compile_log_path": str(log_path) if log_path else "",
    }


def classify_failure(return_code: int, log_text: str, final: dict[str, Any], checks: dict[str, Any]) -> str:
    lowered = (log_text or "").lower()
    if contains_cookie_failure(log_text):
        return "auth"
    if (
        contains_codex_app_server_failure(lowered)
        or "service_tier" in lowered
        or "not inside a trusted directory" in lowered
        or "failed to clean up stale arg0 temp dirs" in lowered
        or "could not update path" in lowered
    ):
        return "codex_cli"
    if final.get("_malformed"):
        return "schema"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "latex" in lowered or "xelatex" in lowered or "tex capacity" in lowered:
        return "latex"
    if "whisper" in lowered:
        return "whisper"
    if "yt-dlp" in lowered or "download" in lowered or "bilibili" in lowered:
        return "download"
    if return_code != 0:
        return "codex_cli"
    if not checks.get("pdf_exists"):
        return "verification"
    return "unknown"


def retryable_failure(failure_class: str) -> bool:
    return failure_class in {"network", "download", "whisper", "latex", "timeout", "unknown"}


def next_action_for_failure(failure_class: str, log_text: str = "") -> str:
    lowered = (log_text or "").lower()
    if failure_class == "auth":
        return "refresh Bilibili cookies and rerun the manifest"
    if failure_class == "codex_cli":
        if "not inside a trusted directory" in lowered:
            return "rerun with --skip-git-repo-check enabled or trust the workspace"
        if "service_tier" in lowered:
            return "rerun with the default service_tier=fast override or pass --codex-config service_tier='fast'"
        if contains_codex_app_server_failure(lowered):
            return "use the generated prompt in the current session with the required subagents, or rerun the driver outside the restricted app-server environment"
        return "use --codex to point at codex.cmd on Windows, or run the generated prompt manually in the current session"
    return "inspect logs and rerun this part when fixed"


def update_item(manifest_path: Path, manifest: dict[str, Any], item: dict[str, Any], **updates: Any) -> None:
    with _manifest_lock:
        item.update(updates)
        item["updated_at"] = utc_now()
        manifest["updated_at"] = utc_now()
        manifest["summary"] = summarize_manifest(manifest)
        write_item_status(item)
        write_json_atomic(manifest_path, manifest)


def build_codex_command(args: argparse.Namespace, item: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    cmd = [args.codex_executable]
    for config in args.codex_config:
        cmd.extend(["-c", config])
    cmd.extend(
        [
        "exec",
        "--json",
        "--sandbox",
        args.sandbox,
        "--cd",
        str(Path(manifest.get("output_root") or args.out_root)),
        "--output-schema",
        str(Path(item.get("output_schema_path") or DEFAULT_PART_RESULT_SCHEMA)),
        "--output-last-message",
        str(item["last_message_path"]),
        ]
    )
    if args.model:
        cmd.extend(["--model", args.model])
    if args.profile:
        cmd.extend(["--profile", args.profile])
    if args.skip_git_repo_check:
        cmd.append("--skip-git-repo-check")
    cmd.append("-")
    return cmd


def run_one_item(
    args: argparse.Namespace,
    manifest_path: Path,
    manifest: dict[str, Any],
    item: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    index = int(item["index"])
    prompt_path = Path(item["prompt_path"])
    log_path = Path(item["log_path"])
    last_message_path = Path(item["last_message_path"])
    prompt = prompt_path.read_text(encoding="utf-8")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    last_message_path.parent.mkdir(parents=True, exist_ok=True)

    started_perf = time.perf_counter()
    started_at = utc_now()
    next_attempt = int(item.get("attempt") or item.get("attempts") or 0) + 1
    update_item(
        manifest_path,
        manifest,
        item,
        status=STATUS_RUNNING,
        attempt=next_attempt,
        attempts=next_attempt,
        started_at=started_at,
        ended_at=None,
        duration_ms=None,
        pid=None,
        exit_code=None,
        failure_class="none",
        error_message="",
        retryable=False,
        next_action="",
    )

    cmd = build_codex_command(args, item, manifest)
    print(f"[P{index:02d}] launching codex exec")
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        log_file.write(json.dumps({"event": "command", "argv": cmd, "time": utc_now()}, ensure_ascii=False) + "\n")
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(Path(manifest.get("output_root") or args.out_root)),
            )
        except OSError as exc:
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            error = f"failed to launch codex exec: {exc}"
            log_file.write(
                json.dumps(
                    {"event": "launch_error", "error": error, "time": utc_now()},
                    ensure_ascii=False,
                )
                + "\n"
            )
            update_item(
                manifest_path,
                manifest,
                item,
                status=STATUS_FAILED,
                ended_at=utc_now(),
                duration_ms=duration_ms,
                exit_code=None,
                failure_class="codex_cli",
                error_message=error,
                retryable=False,
                next_action=next_action_for_failure("codex_cli", error),
            )
            print(f"[P{index:02d}] failed: {error}", file=sys.stderr)
            return item, False
        update_item(manifest_path, manifest, item, pid=proc.pid)
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
        for line in proc.stdout:
            log_file.write(line)
            log_file.flush()
            print(f"[P{index:02d}] {line}", end="")
        return_code = proc.wait()

    duration_ms = int((time.perf_counter() - started_perf) * 1000)
    final = load_final_response(item)
    checks = artifact_checks(item, final)
    child_status = str(final.get("status") or "").lower()
    child_blocked = child_status == STATUS_BLOCKED
    success = return_code == 0 and checks.get("pdf_exists") and not final.get("_malformed") and child_status not in {
        STATUS_FAILED,
        STATUS_BLOCKED,
    }

    if success:
        update_item(
            manifest_path,
            manifest,
            item,
            status=STATUS_SUCCEEDED,
            ended_at=utc_now(),
            duration_ms=duration_ms,
            exit_code=return_code,
            pdf_path=checks.get("pdf_path", ""),
            tex_path=checks.get("tex_path", ""),
            artifact_checks=checks,
            raw_final_response=final,
            failure_class="none",
            error_message="",
            retryable=False,
            next_action="",
        )
        print(f"[P{index:02d}] succeeded")
        return item, False

    log_text = load_log_text(item)
    cookie_failure = contains_cookie_failure(log_text) or child_status == STATUS_BLOCKED
    failure_class = classify_failure(return_code, log_text, final, checks)
    if child_blocked and failure_class == "unknown":
        failure_class = "auth"
    error = f"codex exec exited with code {return_code}"
    if final.get("_malformed"):
        error = f"{error}; final response did not match JSON schema"
    if return_code == 0 and not checks.get("pdf_exists"):
        error = f"{error}; PDF artifact was not verified"
    if cookie_failure:
        error = f"{error}; possible cookie failure"
    next_action = next_action_for_failure(failure_class, log_text)
    update_item(
        manifest_path,
        manifest,
        item,
        status=STATUS_BLOCKED if failure_class == "auth" else STATUS_FAILED,
        ended_at=utc_now(),
        duration_ms=duration_ms,
        exit_code=return_code,
        pdf_path=checks.get("pdf_path", ""),
        tex_path=checks.get("tex_path", ""),
        artifact_checks=checks,
        raw_final_response=final,
        failure_class=failure_class,
        error_message=error,
        retryable=retryable_failure(failure_class),
        next_action=next_action,
    )
    print(f"[P{index:02d}] failed: {error}", file=sys.stderr)
    return item, cookie_failure


def select_items(manifest: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    selected_parts = set(args.part or [])
    result: list[dict[str, Any]] = []
    for item in manifest.get("items", []):
        index = int(item["index"])
        if selected_parts and index not in selected_parts:
            continue
        if item.get("status") == STATUS_SUCCEEDED and not args.force:
            continue
        if item.get("status") == STATUS_SKIPPED and not args.force:
            continue
        result.append(item)
    return result


def run_items(args: argparse.Namespace, manifest_path: Path, manifest: dict[str, Any]) -> None:
    items = select_items(manifest, args)
    if not items:
        print("No runnable items selected.")
        return
    if has_codex_app_server_history(manifest) and not args.allow_known_codex_app_server_retry:
        fail(
            "manifest records a previous Codex app-server permission failure. "
            "Use --mode manual for the next prompt and --mode reconcile after manual completion, "
            "or pass --allow-known-codex-app-server-retry if the Codex CLI environment has been fixed."
        )
    args.codex_executable = resolve_codex_executable(args.codex)
    if shutil.which(args.codex_executable) is None and not Path(args.codex_executable).exists():
        fail(f"codex executable was not found: {args.codex_executable}")
    if args.concurrency > 1:
        print("WARNING: concurrency > 1 may allow already-started jobs to continue after a cookie failure.")

    stop_event = threading.Event()

    def worker(item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if stop_event.is_set():
            update_item(
                manifest_path,
                manifest,
                item,
                status=STATUS_SKIPPED,
                failure_class="unknown",
                error_message="stopped by earlier fatal error",
                next_action="rerun the manifest after resolving the earlier failure",
            )
            return item, False
        item_result, cookie_failure = run_one_item(args, manifest_path, manifest, item)
        if cookie_failure and not args.continue_on_cookie_error:
            stop_event.set()
        if item_result.get("failure_class") == "codex_cli" and not args.continue_on_codex_error:
            stop_event.set()
        if item_result.get("status") == STATUS_FAILED and args.stop_on_failure:
            stop_event.set()
        return item_result, cookie_failure

    if args.concurrency == 1:
        for item in items:
            worker(item)
            if stop_event.is_set():
                break
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(worker, item) for item in items]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def print_manual_instructions(manifest: dict[str, Any], args: argparse.Namespace) -> None:
    items = select_items(manifest, args)
    if not items:
        print("No manual items selected.")
        return
    for item in items:
        index = int(item["index"])
        print(f"[P{index:02d}] manual execution prompt:")
        print(f"  Prompt: {item['prompt_path']}")
        print(f"  Output: {item['output_dir']}")
        print("  After the PDF, TeX, consistency review, independent review, and Pyramid Gate reports exist, run:")
        print(
            "  python .agents\\skills\\bilibili-batch-render-pdf\\scripts\\run_batch.py "
            f"--manifest \"{manifest.get('control_dir')}\\manifest.json\" --mode reconcile --part {index}"
        )


def reconcile_selected_items(args: argparse.Namespace, manifest_path: Path, manifest: dict[str, Any]) -> None:
    items = select_items(manifest, args)
    if not items:
        print("No reconcile items selected.")
        return
    reconciled = reconcile_items(
        manifest_path,
        manifest,
        items,
        min_pdf_bytes=args.min_pdf_bytes,
        require_reviews=not args.no_require_reviews,
    )
    print(f"Reconciled items: {len(reconciled)}")


def load_or_create_manifest(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    if args.manifest:
        manifest_path = Path(args.manifest).resolve()
        return manifest_path, read_json(manifest_path)
    return build_manifest_from_url(args)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan, run, manually complete, reconcile, and resume Bilibili multi-part PDF batches.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Bilibili source URL to enumerate.")
    source.add_argument("--manifest", help="Existing manifest.json to resume.")
    parser.add_argument(
        "--mode",
        choices=["plan", "manual", "run", "reconcile"],
        default="plan",
        help="Generate prompts, show manual instructions, run codex exec, or reconcile manually completed artifacts.",
    )
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUTPUT_ROOT, help="Workspace/output root.")
    parser.add_argument("--batch-name", help="Override the batch folder name.")
    parser.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE, help="Bilibili Netscape cookie file.")
    parser.add_argument("--venv-python", type=Path, default=DEFAULT_VENV_PYTHON, help="Python executable with yt-dlp installed.")
    parser.add_argument("--proxy", help="Optional yt-dlp proxy, such as http://127.0.0.1:7897.")
    parser.add_argument("--yt-dlp-arg", action="append", default=[], help="Extra yt-dlp argument string. Repeat as needed.")
    parser.add_argument("--codex", default="codex", help="Codex CLI executable. On Windows, bare 'codex' resolves to codex.cmd when available.")
    parser.add_argument("--codex-config", action="append", default=[], help="Codex CLI config override passed as '-c <value>' before exec. Repeat as needed.")
    parser.add_argument("--no-default-codex-config", action="store_true", help="Do not add the default service_tier='fast' override.")
    parser.add_argument("--sandbox", default="workspace-write", choices=["read-only", "workspace-write", "danger-full-access"])
    parser.add_argument("--model", help="Optional Codex model override for child tasks.")
    parser.add_argument("--profile", help="Optional Codex config profile for child tasks.")
    parser.add_argument("--skip-git-repo-check", action=argparse.BooleanOptionalAction, default=True, help="Pass through to codex exec. Enabled by default for batch child jobs.")
    parser.add_argument("--localize-cookie-file", action=argparse.BooleanOptionalAction, default=True, help="Copy an external cookie file into the workspace 待删除 cache before yt-dlp uses it.")
    parser.add_argument("--part", type=int, action="append", help="Only plan or run one selected part index. Repeat as needed.")
    parser.add_argument("--force", action="store_true", help="Run items even if their status is succeeded or skipped.")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of child codex exec jobs to run at once.")
    parser.add_argument("--stop-on-failure", action="store_true", help="Stop scheduling after any failed part.")
    parser.add_argument("--continue-on-cookie-error", action="store_true", help="Continue scheduling after a suspected cookie failure.")
    parser.add_argument("--continue-on-codex-error", action="store_true", help="Continue scheduling after a Codex CLI infrastructure failure.")
    parser.add_argument(
        "--allow-known-codex-app-server-retry",
        action="store_true",
        help="Allow codex exec even when the manifest records a previous app-server permission failure.",
    )
    parser.add_argument(
        "--min-pdf-bytes",
        type=int,
        default=1024,
        help="Minimum PDF size accepted by --mode reconcile.",
    )
    parser.add_argument(
        "--no-require-reviews",
        action="store_true",
        help="Allow --mode reconcile without review/consistency_review.md and review/independent_review.md.",
    )
    args = parser.parse_args(argv)
    args.codex_config = ([] if args.no_default_codex_config else list(DEFAULT_CODEX_CONFIG)) + args.codex_config
    if args.concurrency < 1:
        fail("--concurrency must be at least 1")
    if args.sandbox == "danger-full-access":
        fail("danger-full-access is intentionally blocked by this batch driver")
    if args.min_pdf_bytes < 1:
        fail("--min-pdf-bytes must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    args.out_root = Path(args.out_root).resolve()
    args.out_root.mkdir(parents=True, exist_ok=True)
    prepare_cookie_file(args)

    manifest_path, manifest = load_or_create_manifest(args)
    ensure_manifest_paths(manifest, manifest_path)
    write_prompts(manifest)
    manifest["summary"] = summarize_manifest(manifest)
    for item in manifest.get("items", []):
        write_item_status(item)
    write_json_atomic(manifest_path, manifest)

    print(f"Manifest: {manifest_path}")
    print(f"Items: {len(manifest.get('items', []))}")
    if args.mode == "plan":
        print("Plan mode complete. Inspect manifest.json and prompts before running.")
        return 0
    if args.mode == "manual":
        print_manual_instructions(manifest, args)
        return 0
    if args.mode == "reconcile":
        reconcile_selected_items(args, manifest_path, manifest)
        print(f"Batch status updated: {manifest_path}")
        return 0

    run_items(args, manifest_path, manifest)
    print(f"Batch status updated: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

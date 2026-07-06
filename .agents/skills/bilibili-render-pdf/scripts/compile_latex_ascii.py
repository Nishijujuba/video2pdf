from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


TRASH_DIR_NAME = "待删除"
DEFAULT_XELATEX = Path(r"D:\kits\MiKTex\miktex\bin\x64\xelatex.exe")
DEFAULT_SOURCE_SKILL = "bilibili-render-pdf"
COMPILE_REPORT_PRODUCER = "compile_latex_ascii.py"
COMPILE_REPORT_PRODUCER_CONTRACT = "latex_compile_guard.v1"
TASKKILL_TIMEOUT_SECONDS = 5
TERMINATION_GRACE_SECONDS = 1.0
COPY_SUFFIXES = {
    ".tex",
    ".sty",
    ".cls",
    ".bib",
    ".bst",
    ".cfg",
    ".def",
    ".clo",
    ".jpg",
    ".jpeg",
    ".png",
    ".pdf",
    ".eps",
}
SKIP_DIR_NAMES = {
    TRASH_DIR_NAME,
    ".git",
    "__pycache__",
    "review",
    "batch-control",
}
SKIP_SUFFIXES = {
    ".mp4",
    ".m4a",
    ".mp3",
    ".wav",
    ".mkv",
    ".webm",
    ".srt",
    ".vtt",
    ".json",
    ".jsonl",
    ".log",
    ".aux",
    ".out",
    ".toc",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
}
COPY_BACK_SUFFIXES = {
    ".pdf",
    ".log",
    ".aux",
    ".out",
    ".toc",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def ascii_path_component(value: str, fallback: str = "document") -> str:
    component = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    component = re.sub(r"_+", "_", component)
    component = component.strip("._-")
    return component or fallback


def has_skip_suffix(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in SKIP_SUFFIXES)


def should_copy(path: Path, source_dir: Path) -> bool:
    if not path.is_file():
        return False
    relative = path.relative_to(source_dir)
    if any(part in SKIP_DIR_NAMES for part in relative.parts[:-1]):
        return False
    if has_skip_suffix(path):
        return False
    return path.suffix.lower() in COPY_SUFFIXES


def iter_copy_candidates(source_dir: Path) -> list[Path]:
    return sorted(path for path in source_dir.rglob("*") if should_copy(path, source_dir))


def copy_compile_inputs(
    source_dir: Path,
    staging_dir: Path,
    excluded_sources: set[Path] | None = None,
) -> list[Path]:
    excluded_resolved = {path.resolve() for path in excluded_sources or set()}
    copied: list[Path] = []
    for source in iter_copy_candidates(source_dir):
        if source.resolve() in excluded_resolved:
            continue
        destination = staging_dir / source.relative_to(source_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def copy_compile_outputs(staging_dir: Path, output_dir: Path, stem: str) -> list[Path]:
    copied: list[Path] = []
    for suffix in COPY_BACK_SUFFIXES:
        source = staging_dir / f"{stem}{suffix}"
        if source.exists():
            destination = output_dir / source.name
            shutil.copy2(source, destination)
            copied.append(destination)
    return copied


def default_staging_root(tex_path: Path) -> Path:
    for parent in [tex_path.parent, *tex_path.parents]:
        if parent.name == "newskill-kimi":
            return parent / "work"
    return Path.cwd() / "work"


def resolve_engine(value: str | None) -> str:
    if value:
        return value
    env_engine = os.environ.get("XELATEX")
    if env_engine:
        return env_engine
    if DEFAULT_XELATEX.exists():
        return str(DEFAULT_XELATEX)
    return "xelatex"


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def non_empty_text(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must be a non-empty string")
    return value


def iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def make_quick_build_dir(video_output_dir: Path) -> Path:
    run_id = f"{_dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
    return video_output_dir / TRASH_DIR_NAME / "latex-build" / run_id


def file_fingerprint(path: Path) -> dict[str, str | int]:
    data = path.read_bytes()
    return {
        "algorithm": "sha256",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def write_compile_report(
    report_path: Path,
    *,
    mode: str,
    status: str,
    source_tex: Path,
    engine: Path,
    run_count: int,
    total_timeout: float,
    idle_timeout: float,
    log_paths: list[Path],
    build_directory: Path,
    start_time: str,
    finish_time: str,
    source_skill: str | None = None,
    final_pdf: Path | None = None,
    argv: list[str] | None = None,
    failure_reason: str | None = None,
) -> None:
    wrapper_script = Path(__file__).resolve()
    report = {
        "schema_version": "latex_compile_report.v1",
        "mode": mode,
        "status": status,
        "producer": COMPILE_REPORT_PRODUCER,
        "producer_contract": COMPILE_REPORT_PRODUCER_CONTRACT,
        "producer_mode": mode,
        "wrapper_script": str(wrapper_script),
        "wrapper_script_fingerprint": file_fingerprint(wrapper_script),
        "argv": list(argv or []),
        "source_tex": str(source_tex.resolve()),
        "main_tex": str(source_tex.resolve()),
        "source_tex_fingerprint": file_fingerprint(source_tex) if source_tex.exists() else None,
        "engine": str(engine.resolve()),
        "run_count": run_count,
        "timeout_settings": {
            "total_seconds": float(total_timeout),
            "idle_seconds": float(idle_timeout),
        },
        "log_paths": [str(path.resolve()) for path in log_paths],
        "build_directory": str(build_directory.resolve()),
        "start_time": start_time,
        "finish_time": finish_time,
    }
    if source_skill is not None:
        report["source_skill"] = source_skill
    if final_pdf is not None:
        report["final_pdf"] = str(final_pdf.resolve())
        report["final_pdf_fingerprint"] = file_fingerprint(final_pdf) if final_pdf.exists() else None
    if failure_reason:
        report["failure_reason"] = failure_reason
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _report_fingerprint_matches(report: dict[str, object], path_key: str, fingerprint_key: str) -> bool:
    path_value = report.get(path_key)
    fingerprint_value = report.get(fingerprint_key)
    if not isinstance(path_value, str) or not isinstance(fingerprint_value, dict):
        return False
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return False
    return fingerprint_value == file_fingerprint(path)


def compile_report_fingerprints_are_current(report_path: Path) -> bool:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(report, dict):
        return False
    if not _report_fingerprint_matches(report, "source_tex", "source_tex_fingerprint"):
        return False
    if "wrapper_script" in report and not _report_fingerprint_matches(
        report,
        "wrapper_script",
        "wrapper_script_fingerprint",
    ):
        return False
    if "final_pdf" in report:
        return _report_fingerprint_matches(report, "final_pdf", "final_pdf_fingerprint")
    return True


def terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=TASKKILL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        return
    try:
        proc.kill()
    except OSError:
        pass


def run_engine_with_timeouts(
    cmd: list[str],
    *,
    cwd: Path,
    total_timeout: float,
    idle_timeout: float,
) -> tuple[int, str, str | None]:
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return 1, "", f"failed to start LaTeX engine: {exc.__class__.__name__}: {exc}"
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert proc.stdout is not None
        try:
            while True:
                chunk = proc.stdout.read(1)
                if chunk == "":
                    break
                output_queue.put(chunk)
        finally:
            proc.stdout.close()
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    output: list[str] = []
    started = time.monotonic()
    last_output = started
    reader_done = False
    failure_reason: str | None = None
    termination_deadline: float | None = None

    while True:
        try:
            item = output_queue.get(timeout=0.05)
        except queue.Empty:
            item = "__queue_empty__"

        if item is None:
            reader_done = True
        elif item != "__queue_empty__":
            output.append(item)
            last_output = time.monotonic()

        if reader_done and proc.poll() is not None:
            break

        if proc.poll() is None and failure_reason is None:
            now = time.monotonic()
            if now - started >= total_timeout:
                failure_reason = "total timeout exceeded"
                terminate_process_tree(proc)
                termination_deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
            elif now - last_output >= idle_timeout:
                failure_reason = "idle timeout exceeded"
                terminate_process_tree(proc)
                termination_deadline = time.monotonic() + TERMINATION_GRACE_SECONDS

        if failure_reason and proc.poll() is not None and reader_done:
            break
        if failure_reason and termination_deadline is not None and time.monotonic() >= termination_deadline:
            break

    reader.join(timeout=0.2)
    while not output_queue.empty():
        item = output_queue.get_nowait()
        if item:
            output.append(item)

    return proc.returncode if proc.returncode is not None else 1, "".join(output), failure_reason


def compile_quick(
    tex_path: Path,
    *,
    engine: Path,
    runs: int,
    total_timeout: float,
    idle_timeout: float,
    argv: list[str] | None = None,
) -> Path:
    source_tex = tex_path.resolve()
    if not source_tex.exists() or not source_tex.is_file():
        fail(f"TeX file not found: {source_tex}")
    engine_path = engine.resolve()
    if not engine_path.exists() or not engine_path.is_file():
        fail(f"LaTeX engine not found: {engine_path}")

    video_output_dir = source_tex.parent
    build_dir = make_quick_build_dir(video_output_dir)
    build_dir.mkdir(parents=True, exist_ok=False)
    output_pdf_paths = {video_output_dir / f"{source_tex.stem}.pdf"}
    copy_compile_inputs(video_output_dir, build_dir, excluded_sources=output_pdf_paths)
    staged_tex = build_dir / source_tex.name
    if not staged_tex.exists():
        fail(f"main TeX file was not copied into quick build: {staged_tex}")

    start_time = iso_now()
    started_monotonic = time.monotonic()
    log_paths: list[Path] = []
    report_path = build_dir / "compile_report.json"
    status = "passed"
    failure_reason: str | None = None

    for run_number in range(1, runs + 1):
        elapsed = time.monotonic() - started_monotonic
        remaining = total_timeout - elapsed
        log_path = build_dir / f"{source_tex.stem}.compile-run-{run_number}.stdout.log"
        log_paths.append(log_path)
        if remaining <= 0:
            status = "failed"
            failure_reason = "total timeout exceeded before run started"
            log_path.write_text("", encoding="utf-8")
            break

        cmd = [
            str(engine_path),
            "-interaction=nonstopmode",
            "-halt-on-error",
            source_tex.name,
        ]
        returncode, output, timeout_failure = run_engine_with_timeouts(
            cmd,
            cwd=build_dir,
            total_timeout=remaining,
            idle_timeout=idle_timeout,
        )
        log_path.write_text(output, encoding="utf-8")
        if timeout_failure:
            status = "failed"
            failure_reason = f"{timeout_failure} during run {run_number}"
            break
        if returncode != 0:
            status = "failed"
            failure_reason = f"LaTeX engine failed on run {run_number} with exit code {returncode}"
            break

    write_compile_report(
        report_path,
        mode="quick",
        status=status,
        source_tex=source_tex,
        engine=engine_path,
        run_count=runs,
        total_timeout=total_timeout,
        idle_timeout=idle_timeout,
        log_paths=log_paths,
        build_directory=build_dir,
        start_time=start_time,
        finish_time=iso_now(),
        argv=argv,
        failure_reason=failure_reason,
    )
    if status != "passed":
        fail(f"quick compile failed: {failure_reason}; report: {report_path}")
    return report_path


def compile_final(
    tex_path: Path,
    *,
    engine: Path,
    final_pdf: Path,
    runs: int,
    total_timeout: float,
    idle_timeout: float,
    source_skill: str = DEFAULT_SOURCE_SKILL,
    argv: list[str] | None = None,
) -> Path:
    source_tex = tex_path.resolve()
    if not source_tex.exists() or not source_tex.is_file():
        fail(f"TeX file not found: {source_tex}")
    engine_path = engine.resolve()
    if not engine_path.exists() or not engine_path.is_file():
        fail(f"LaTeX engine not found: {engine_path}")

    video_output_dir = source_tex.parent
    final_pdf_path = final_pdf.resolve()
    if not final_pdf_path.is_relative_to(video_output_dir.resolve()):
        fail(f"final PDF must stay inside video output directory: {final_pdf_path}")

    build_dir = make_quick_build_dir(video_output_dir)
    build_dir.mkdir(parents=True, exist_ok=False)
    copy_compile_inputs(video_output_dir, build_dir)
    staged_tex = build_dir / source_tex.name
    if not staged_tex.exists():
        fail(f"main TeX file was not copied into final build: {staged_tex}")

    start_time = iso_now()
    started_monotonic = time.monotonic()
    log_paths: list[Path] = []
    report_path = video_output_dir / "review" / "latex" / "compile_report.json"
    status = "passed"
    failure_reason: str | None = None

    for run_number in range(1, runs + 1):
        elapsed = time.monotonic() - started_monotonic
        remaining = total_timeout - elapsed
        log_path = build_dir / f"{source_tex.stem}.compile-run-{run_number}.stdout.log"
        log_paths.append(log_path)
        if remaining <= 0:
            status = "failed"
            failure_reason = "total timeout exceeded before run started"
            log_path.write_text("", encoding="utf-8")
            break

        cmd = [
            str(engine_path),
            "-interaction=nonstopmode",
            "-halt-on-error",
            source_tex.name,
        ]
        returncode, output, timeout_failure = run_engine_with_timeouts(
            cmd,
            cwd=build_dir,
            total_timeout=remaining,
            idle_timeout=idle_timeout,
        )
        log_path.write_text(output, encoding="utf-8")
        if timeout_failure:
            status = "failed"
            failure_reason = f"{timeout_failure} during run {run_number}"
            break
        if returncode != 0:
            status = "failed"
            failure_reason = f"LaTeX engine failed on run {run_number} with exit code {returncode}"
            break

    staged_pdf = build_dir / f"{source_tex.stem}.pdf"
    if status == "passed":
        if staged_pdf.exists():
            final_pdf_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged_pdf, final_pdf_path)
        else:
            status = "failed"
            failure_reason = f"current build did not produce {source_tex.stem}.pdf: {staged_pdf}"
        if status == "passed" and not final_pdf_path.exists():
            status = "failed"
            failure_reason = f"final PDF was not produced: {final_pdf_path}"

    write_compile_report(
        report_path,
        mode="final",
        status=status,
        source_tex=source_tex,
        engine=engine_path,
        run_count=runs,
        total_timeout=total_timeout,
        idle_timeout=idle_timeout,
        log_paths=log_paths,
        build_directory=build_dir,
        start_time=start_time,
        finish_time=iso_now(),
        source_skill=source_skill,
        final_pdf=final_pdf_path,
        argv=argv,
        failure_reason=failure_reason,
    )
    if status != "passed":
        fail(f"final compile failed: {failure_reason}; report: {report_path}")
    return report_path


def compile_latex(
    tex_path: Path,
    *,
    engine: str | None = None,
    staging_root: Path | None = None,
    runs: int = 2,
) -> tuple[Path, list[Path]]:
    tex_path = tex_path.resolve()
    if not tex_path.exists():
        fail(f"TeX file not found: {tex_path}")
    if runs < 1:
        fail("--runs must be at least 1")

    source_dir = tex_path.parent
    staging_root = (staging_root or default_staging_root(tex_path)).resolve()
    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    staging_stem = ascii_path_component(tex_path.stem)
    staging_dir = staging_root / f"latex_ascii_{staging_stem}_{timestamp}"
    if not is_ascii_path(staging_dir):
        fail(f"staging path must be ASCII-only: {staging_dir}")
    staging_dir.mkdir(parents=True, exist_ok=True)
    copied_inputs = copy_compile_inputs(source_dir, staging_dir)
    staged_tex = staging_dir / tex_path.name
    if not staged_tex.exists():
        fail(f"main TeX file was not copied into staging: {staged_tex}")

    command_engine = resolve_engine(engine)
    for run_number in range(1, runs + 1):
        cmd = [
            command_engine,
            "-interaction=nonstopmode",
            "-halt-on-error",
            tex_path.name,
        ]
        print(f"XeLaTeX run {run_number}/{runs}: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            cwd=str(staging_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log_path = staging_dir / f"{tex_path.stem}.compile-run-{run_number}.stdout.log"
        log_path.write_text(proc.stdout or "", encoding="utf-8")
        if proc.returncode != 0:
            print(proc.stdout[-4000:] if proc.stdout else "", file=sys.stderr)
            fail(f"XeLaTeX failed on run {run_number}; staging preserved at {staging_dir}")

    copied_outputs = copy_compile_outputs(staging_dir, source_dir, tex_path.stem)
    pdf_path = source_dir / f"{tex_path.stem}.pdf"
    if not pdf_path.exists():
        fail(f"compiled PDF was not copied back: {pdf_path}; staging preserved at {staging_dir}")
    print(f"Staging preserved: {staging_dir}")
    print(f"Copied inputs: {len(copied_inputs)}")
    print("Copied outputs:")
    for output in copied_outputs:
        print(f"  {output}")
    return staging_dir, copied_outputs


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile a LaTeX document through the legacy copier or guarded wrapper.",
    )
    parser.add_argument("legacy_tex", nargs="?", type=Path, help="Legacy positional source .tex file.")
    parser.add_argument("--tex", type=Path, help="Path to the source .tex file.")
    parser.add_argument("--mode", choices=["quick", "final"], help="Compile mode.")
    parser.add_argument("--engine", help="LaTeX engine executable path.")
    parser.add_argument("--final-pdf", type=Path, help="Durable PDF path for final mode.")
    parser.add_argument(
        "--source-skill",
        type=non_empty_text,
        default=DEFAULT_SOURCE_SKILL,
        help="Caller identity recorded in final compile reports.",
    )
    parser.add_argument("--staging-root", type=Path, help="Legacy ASCII staging root.")
    parser.add_argument("--runs", type=positive_int, help="Number of LaTeX runs.")
    parser.add_argument("--total-timeout", type=positive_float, default=120.0, help="Total timeout in seconds.")
    parser.add_argument("--idle-timeout", type=positive_float, default=30.0, help="Idle output timeout in seconds.")
    args = parser.parse_args(argv)

    if args.mode == "quick":
        if args.legacy_tex is not None:
            parser.error("quick mode requires --tex instead of positional tex")
        if args.tex is None:
            parser.error("quick mode requires --tex")
        if args.engine is None:
            parser.error("quick mode requires --engine")
        return args
    if args.mode == "final":
        if args.legacy_tex is not None:
            parser.error("final mode requires --tex instead of positional tex")
        if args.tex is None:
            parser.error("final mode requires --tex")
        if args.engine is None:
            parser.error("final mode requires --engine")
        if args.final_pdf is None:
            parser.error("final mode requires --final-pdf")
        return args

    if args.tex is not None:
        parser.error("--tex requires --mode quick or final")
    if args.final_pdf is not None:
        parser.error("--final-pdf requires --mode final")
    if args.legacy_tex is None:
        parser.error("positional tex is required unless --mode quick or final is used")
    return args


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    args = parse_args(effective_argv)
    if args.mode == "quick":
        compile_quick(
            args.tex,
            engine=Path(args.engine),
            runs=args.runs or 1,
            total_timeout=args.total_timeout,
            idle_timeout=args.idle_timeout,
            argv=effective_argv,
        )
    elif args.mode == "final":
        compile_final(
            args.tex,
            engine=Path(args.engine),
            final_pdf=args.final_pdf,
            runs=args.runs or 2,
            total_timeout=args.total_timeout,
            idle_timeout=args.idle_timeout,
            source_skill=args.source_skill,
            argv=effective_argv,
        )
    else:
        compile_latex(
            args.legacy_tex,
            engine=args.engine,
            staging_root=args.staging_root,
            runs=args.runs or 2,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import datetime as _dt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


TRASH_DIR_NAME = "待删除"
DEFAULT_XELATEX = Path(r"D:\kits\MiKTex\miktex\bin\x64\xelatex.exe")
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


def copy_compile_inputs(source_dir: Path, staging_dir: Path) -> list[Path]:
    copied: list[Path] = []
    for source in iter_copy_candidates(source_dir):
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
        description="Compile a XeLaTeX document from an ASCII staging directory and copy outputs back.",
    )
    parser.add_argument("tex", type=Path, help="Path to the final .tex file.")
    parser.add_argument("--engine", help="XeLaTeX executable. Defaults to XELATEX env, project MiKTeX, then xelatex.")
    parser.add_argument("--staging-root", type=Path, help="ASCII directory where staging folders should be created.")
    parser.add_argument("--runs", type=int, default=2, help="Number of XeLaTeX runs.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    compile_latex(
        args.tex,
        engine=args.engine,
        staging_root=args.staging_root,
        runs=args.runs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

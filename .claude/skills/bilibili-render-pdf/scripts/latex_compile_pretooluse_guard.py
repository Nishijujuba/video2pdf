#!/usr/bin/env python3
"""PreToolUse guard for unsafe LaTeX shell commands."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import sys
import time
from typing import Any


LATEX_ENGINES = {
    "xelatex",
    "xelatex.exe",
    "pdflatex",
    "lualatex",
    "latexmk",
    "tectonic",
}
DANGEROUS_OUTPUT_DIR_VALUES = {"$build", "${build}", "%build%", "build"}
GUARDED_WRAPPER_NAME = "compile_latex_ascii.py"
ALLOWED_WRAPPER_MODES = {"quick", "final"}
LATEX_PROCESS_INDICATOR_NAMES = {
    ".latex-process",
    "latex-process.pid",
    "latex.pid",
    "xelatex.pid",
    "pdflatex.pid",
    "lualatex.pid",
    "latexmk.pid",
    "tectonic.pid",
}
SCAN_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
COMMAND_FIELD_NAMES = ("command", "cmd", "shell_command", "bash_command")
SHELL_CONTROL_TOKENS = {";", "&&", "||", "|", "&"}
COMMAND_PREFIX_TOKENS = {"command", "env", "exec", "nohup", "time"}


def command_from_hook(hook_input: dict[str, Any]) -> str:
    tool_input = hook_input.get("tool_input")
    if isinstance(tool_input, dict):
        for field in COMMAND_FIELD_NAMES:
            command = tool_input.get(field)
            if isinstance(command, str):
                return command
    if isinstance(tool_input, str):
        return tool_input
    for field in COMMAND_FIELD_NAMES:
        command = hook_input.get(field)
        if isinstance(command, str):
            return command
    return ""


def command_tokens(command: str) -> list[str]:
    command = re.sub(r"(&&|\|\||[;|&])", r" \1 ", command)
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return re.findall(r"[^\s\"']+", command)


def token_basename(token: str) -> str:
    return normalized_arg_value(token).replace("\\", "/").rsplit("/", 1)[-1].lower()


def direct_latex_engine(command: str) -> str | None:
    expect_command = True
    for token in command_tokens(command):
        cleaned = normalized_arg_value(token)
        if cleaned in SHELL_CONTROL_TOKENS:
            expect_command = True
            continue
        name = token_basename(token)
        if expect_command and "=" in cleaned and not cleaned.startswith("-"):
            continue
        if expect_command and name in COMMAND_PREFIX_TOKENS:
            continue
        if expect_command and name in LATEX_ENGINES:
            return name
        expect_command = False
    return None


def normalized_arg_value(value: str) -> str:
    return value.strip().strip("\"'")


def is_project_root_output(value: str, project_root: Path) -> bool:
    cleaned = normalized_arg_value(value)
    if not cleaned:
        return False
    try:
        return Path(cleaned).resolve() == project_root.resolve()
    except OSError:
        return False


def dangerous_output_directory(command: str, project_root: Path) -> str | None:
    tokens = command_tokens(command)
    for index, token in enumerate(tokens):
        if token == "-output-directory":
            value = tokens[index + 1] if index + 1 < len(tokens) else ""
        elif token.startswith("-output-directory="):
            value = token.split("=", 1)[1]
        else:
            continue

        cleaned = normalized_arg_value(value)
        if not cleaned:
            return "empty output directory"
        if cleaned in DANGEROUS_OUTPUT_DIR_VALUES:
            return cleaned
        if is_project_root_output(cleaned, project_root):
            return cleaned
    return None


def guarded_wrapper_mode(command: str) -> str | None:
    tokens = command_tokens(command)
    if not has_guarded_wrapper(command):
        return None
    for index, token in enumerate(tokens):
        if token == "--mode" and index + 1 < len(tokens):
            mode = normalized_arg_value(tokens[index + 1]).lower()
        elif token.startswith("--mode="):
            mode = normalized_arg_value(token.split("=", 1)[1]).lower()
        else:
            continue
        if mode in ALLOWED_WRAPPER_MODES:
            return mode
    return None


def has_guarded_wrapper(command: str) -> bool:
    return any(token_basename(token) == GUARDED_WRAPPER_NAME for token in command_tokens(command))


def iter_scan_paths(root: Path, max_entries: int, deadline_seconds: float) -> list[Path]:
    deadline = time.monotonic() + deadline_seconds
    paths: list[Path] = []
    stack = [root]
    while stack and len(paths) < max_entries and time.monotonic() <= deadline:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if len(paths) >= max_entries or time.monotonic() > deadline:
                break
            paths.append(child)
            if child.is_dir() and child.name not in SCAN_SKIP_DIRS:
                stack.append(child)
    return paths


def is_compile_log(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() == ".log" and ("compile" in name or name in {"main.log", "notes.log"})


def scan_anomalies(scan_root: Path, max_entries: int = 500, deadline_seconds: float = 0.03) -> list[dict[str, str]]:
    root = scan_root.resolve()
    if not root.exists() or not root.is_dir():
        return []
    anomalies: list[dict[str, str]] = []
    for path in iter_scan_paths(root, max_entries=max_entries, deadline_seconds=deadline_seconds):
        if path.is_dir() and path.name == "$build":
            anomalies.append({"type": "literal_build_directory", "path": str(path.resolve())})
            continue
        if path.is_file() and is_compile_log(path):
            try:
                is_empty = path.stat().st_size == 0
            except OSError:
                is_empty = False
            if is_empty:
                anomalies.append({"type": "zero_byte_compile_log", "path": str(path.resolve())})
                continue
        if path.is_file() and path.name.lower() in LATEX_PROCESS_INDICATOR_NAMES:
            anomalies.append({"type": "stale_latex_process_indicator", "path": str(path.resolve())})
    return anomalies


def allow(
    reason: str = "Command allowed by LaTeX compile PreToolUse guard.",
    anomalies: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return hook_output("approve", "allow", reason, anomalies or [])


def block(reason: str, anomalies: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return hook_output("block", "deny", reason, anomalies or [])


def hook_output(
    decision: str,
    permission_decision: str,
    reason: str,
    anomalies: list[dict[str, str]],
) -> dict[str, Any]:
    additional_context = ""
    if anomalies:
        additional_context = "LaTeX compile anomaly scan: " + json.dumps(anomalies, ensure_ascii=False)
    return {
        "continue": True,
        "decision": decision,
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission_decision,
            "permissionDecisionReason": reason,
            "additionalContext": additional_context,
        },
    }


def decide_hook(
    hook_input: dict[str, Any],
    project_root: Path | None = None,
    scan_root: Path | None = None,
) -> dict[str, Any]:
    command = command_from_hook(hook_input)
    root = (project_root or Path.cwd()).resolve()
    anomalies = scan_anomalies(scan_root or root)
    unsafe_output = dangerous_output_directory(command, root)
    if unsafe_output:
        return block(
            "Blocked unsafe LaTeX output directory "
            f"'{unsafe_output}'. Use the guarded compile wrapper so build output stays under the video "
            "directory's disposable storage.",
            anomalies,
        )
    engine = direct_latex_engine(command)
    if engine:
        return block(
            "Blocked direct LaTeX engine call "
            f"'{engine}'. Use .agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py "
            "with --mode quick or --mode final.",
            anomalies,
        )
    mode = guarded_wrapper_mode(command)
    if mode:
        return allow(f"Guarded LaTeX compile wrapper allowed in {mode} mode.", anomalies)
    if has_guarded_wrapper(command):
        return block(
            "Blocked compile_latex_ascii.py without an allowed guarded wrapper mode. "
            "Use --mode quick for temporary diagnostics or --mode final for delivery-oriented compilation.",
            anomalies,
        )
    return allow(anomalies=anomalies)


def main() -> int:
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps(block(f"Invalid hook JSON: {exc}"), ensure_ascii=False))
        return 0
    print(json.dumps(decide_hook(hook_input), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

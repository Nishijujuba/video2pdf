#!/usr/bin/env python3
"""Run a constrained Codex-backed Pyramid Principle text evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence
from uuid import uuid4


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_report import DIMENSION_WEIGHTS, ValidationError, validate_report


DEFAULT_MAX_INPUT_CHARS = 160000
DEFAULT_CODEX_MODEL = "gpt-5.5"
PROMPT_VERSION = "pyramid-principle-text-v1"
STANDARD_NAME = "Pyramid Principle Text Standard"
BACKEND = "codex-exec"
SEMANTIC_KEYS = {"status", "score", "dimensions", "findings", "required_revisions"}
DIMENSION_KEYS = {
    "top_down_clarity",
    "support_hierarchy",
    "grouping_mece",
    "teaching_progression",
    "title_body_alignment",
}
STATUSES = {"pass", "needs_revision", "blocked"}
SEVERITIES = {"info", "minor", "major", "critical"}
TEX_INPUT_RE = re.compile(r"(?m)^(?P<indent>[ \t]*)\\input\{(?P<target>[^}]+)\}")


class EvaluationError(Exception):
    """Raised when the evaluator cannot produce a valid report."""


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[..., CompletedProcessLike]


def _semantic_schema_path() -> Path:
    return SKILL_DIR / "references" / "evaluator-output-schema.json"


def _scratch_dir(output_path: Path) -> Path:
    output_parent = output_path.parent.resolve(strict=False)
    if output_parent.name == "pyramid" and output_parent.parent.name == "review":
        artifact_root = output_parent.parent.parent
    else:
        artifact_root = output_parent
    path = artifact_root / "待删除" / "pyramid-evaluator"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def _read_input(input_path: Path) -> tuple[bytes, str]:
    try:
        raw = input_path.read_bytes()
    except OSError as exc:
        raise EvaluationError(f"cannot read input artifact: {input_path}") from exc
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvaluationError(f"input artifact must be UTF-8 text: {input_path}") from exc


def _resolve_tex_input(input_path: Path, target: str) -> Path:
    normalized = target.replace("/", os.sep).replace("\\", os.sep)
    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = input_path.parent / candidate
    if candidate.suffix == "":
        candidate = candidate.with_suffix(".tex")
    return candidate


def _expand_tex_inputs_once(artifact_text: str, input_path: Path) -> tuple[str, bool]:
    changed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        target = match.group("target").strip()
        include_path = _resolve_tex_input(input_path, target)
        if not include_path.exists() or include_path.resolve(strict=False) == input_path.resolve(strict=False):
            return match.group(0)
        try:
            included = include_path.read_text(encoding="utf-8")
        except OSError:
            return match.group(0)
        changed = True
        return (
            f"% --- expanded from \\input{{{target}}} ---\n"
            f"{included}\n"
            f"% --- end expanded \\input{{{target}}} ---"
        )

    return TEX_INPUT_RE.sub(replace, artifact_text), changed


def _semantic_review_text(artifact_text: str, input_path: Path, artifact_type: str) -> str:
    if artifact_type != "tex_document":
        return artifact_text
    expanded = artifact_text
    for _ in range(8):
        expanded, changed = _expand_tex_inputs_once(expanded, input_path)
        if not changed:
            break
    return expanded


def _normalize_semantic_score(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    dimensions = normalized["dimensions"]
    normalized["score"] = round(
        sum(float(dimensions[name]) * weight for name, weight in DIMENSION_WEIGHTS.items()),
        3,
    )
    return normalized


def build_prompt(
    *,
    artifact_text: str,
    artifact_type: str,
    context_label: str,
    evaluation_context: str,
    input_sha256: str,
    input_size_chars: int,
    max_input_size_chars: int,
    large_input_approval_state: str,
) -> str:
    return f"""You are a constrained semantic evaluator for the Pyramid Principle Text Standard.

Evaluate only the supplied artifact text. Return only JSON that matches the provided schema.
Never include target, artifact_type, context_label, waiver, audit, markdown fences, or prose outside JSON.

Standard:
- A strong text states its controlling claim early.
- Parent claims are supported by child sections.
- Same-level groups are meaningfully distinct.
- Teaching documents progress through motivation, core idea, mechanism, example, evidence, and takeaway when those elements apply.
- Titles and headings accurately promise the body content below them.

Scoring:
- top_down_clarity weight 0.25
- support_hierarchy weight 0.25
- grouping_mece weight 0.20
- teaching_progression weight 0.20
- title_body_alignment weight 0.10

Status mapping:
- pass: score >= 0.80
- needs_revision: 0.60 <= score < 0.80
- blocked: score < 0.60

Severe structural failures should force needs_revision or blocked even if the weighted score is higher.

Evaluation context:
{evaluation_context}

Wrapper metadata for orientation:
- artifact_type: {artifact_type}
- context_label: {context_label}
- prompt_version: {PROMPT_VERSION}
- input_sha256: {input_sha256}
- input_size_chars: {input_size_chars}
- max_input_size_chars: {max_input_size_chars}
- large_input_approval_state: {large_input_approval_state}

Artifact text:
<artifact>
{artifact_text}
</artifact>
"""


def _build_codex_command(
    *,
    codex_executable: str,
    codex_model: str | None,
    schema_path: Path,
    output_last_message_path: Path,
    work_dir: Path,
) -> list[str]:
    command = [
        codex_executable,
        "exec",
    ]
    if codex_model:
        command.extend(["-m", codex_model])
    command.extend(
        [
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_last_message_path),
        "--json",
        "--cd",
        str(work_dir),
        "-c",
        'approval_policy="never"',
        "-",
        ]
    )
    return command


def _resolve_codex_executable(value: str) -> str:
    if os.name == "nt" and value.lower() == "codex":
        cmd_path = shutil.which("codex.cmd")
        if cmd_path:
            return cmd_path
    resolved = shutil.which(value)
    return resolved or value


def _run_codex(command: list[str], prompt: str, runner: CommandRunner) -> None:
    try:
        completed = runner(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise EvaluationError(f"failed to start codex exec: {exc}") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or "no process output"
        raise EvaluationError(f"codex exec failed with exit code {completed.returncode}: {detail}")


def _load_semantic_output(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise EvaluationError(f"codex exec did not write semantic output: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvaluationError(f"codex exec produced non-JSON semantic output: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationError("codex exec semantic output must be a JSON object")
    return value


def _require_number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvaluationError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise EvaluationError(f"{label} must be finite")
    if number < 0 or number > 1:
        raise EvaluationError(f"{label} must be between 0 and 1")
    return number


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{label} must be a non-empty string")
    return value


def _validate_semantic_result(result: dict[str, Any]) -> None:
    missing = SEMANTIC_KEYS - set(result)
    extra = set(result) - SEMANTIC_KEYS
    if missing:
        raise EvaluationError("semantic output missing keys: " + ", ".join(sorted(missing)))
    if extra:
        raise EvaluationError("semantic output has unknown keys: " + ", ".join(sorted(extra)))

    status = _require_string(result["status"], "semantic output status")
    if status not in STATUSES:
        raise EvaluationError("semantic output status must be one of: blocked, needs_revision, pass")
    _require_number(result["score"], "semantic output score")

    dimensions = result["dimensions"]
    if not isinstance(dimensions, dict):
        raise EvaluationError("semantic output dimensions must be an object")
    missing_dimensions = DIMENSION_KEYS - set(dimensions)
    extra_dimensions = set(dimensions) - DIMENSION_KEYS
    if missing_dimensions:
        raise EvaluationError("semantic output dimensions missing keys: " + ", ".join(sorted(missing_dimensions)))
    if extra_dimensions:
        raise EvaluationError("semantic output dimensions has unknown keys: " + ", ".join(sorted(extra_dimensions)))
    for key in DIMENSION_KEYS:
        _require_number(dimensions[key], f"semantic output dimensions.{key}")

    findings = result["findings"]
    if not isinstance(findings, list):
        raise EvaluationError("semantic output findings must be an array")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise EvaluationError(f"semantic output findings[{index}] must be an object")
        expected_finding_keys = {"severity", "location", "issue", "recommendation"}
        missing_finding = expected_finding_keys - set(finding)
        extra_finding = set(finding) - expected_finding_keys
        if missing_finding:
            raise EvaluationError(
                f"semantic output findings[{index}] missing keys: " + ", ".join(sorted(missing_finding))
            )
        if extra_finding:
            raise EvaluationError(
                f"semantic output findings[{index}] has unknown keys: " + ", ".join(sorted(extra_finding))
            )
        severity = _require_string(finding["severity"], f"semantic output findings[{index}].severity")
        if severity not in SEVERITIES:
            raise EvaluationError(f"semantic output findings[{index}].severity is invalid")
        for key in ("location", "issue", "recommendation"):
            _require_string(finding[key], f"semantic output findings[{index}].{key}")

    required_revisions = result["required_revisions"]
    if not isinstance(required_revisions, list):
        raise EvaluationError("semantic output required_revisions must be an array")
    for index, item in enumerate(required_revisions):
        _require_string(item, f"semantic output required_revisions[{index}]")


def _build_waiver_metadata(
    *,
    waiver_approved_by: str | None,
    waiver_reason: str | None,
    waiver_approved_at: str | None,
    generated_at: str,
) -> dict[str, Any]:
    waiver_requested = any(value is not None for value in (waiver_approved_by, waiver_reason, waiver_approved_at))
    if not waiver_requested:
        return {
            "state": "none",
            "approved_by": None,
            "reason": None,
            "approved_at": None,
        }
    if waiver_approved_by is None or not waiver_approved_by.strip():
        raise EvaluationError("--waiver-approved-by is required when recording a waiver")
    if waiver_reason is None or not waiver_reason.strip():
        raise EvaluationError("--waiver-reason is required when recording a waiver")
    return {
        "state": "approved",
        "approved_by": waiver_approved_by,
        "reason": waiver_reason,
        "approved_at": waiver_approved_at or generated_at,
    }


def _build_report(
    *,
    semantic_result: dict[str, Any],
    input_path: Path,
    artifact_type: str,
    context_label: str,
    evaluation_context: str,
    input_sha256: str,
    input_size_chars: int,
    max_input_size_chars: int,
    large_input_approval_state: str,
    generated_at: str,
    waiver: dict[str, Any],
) -> dict[str, Any]:
    return {
        "target": str(input_path),
        "artifact_type": artifact_type,
        "context_label": context_label,
        "status": semantic_result["status"],
        "score": semantic_result["score"],
        "dimensions": semantic_result["dimensions"],
        "findings": semantic_result["findings"],
        "required_revisions": semantic_result["required_revisions"],
        "waiver": waiver,
        "audit": {
            "standard_name": STANDARD_NAME,
            "backend": BACKEND,
            "prompt_version": PROMPT_VERSION,
            "input_sha256": input_sha256,
            "input_size_chars": input_size_chars,
            "max_input_size_chars": max_input_size_chars,
            "large_input_approval_state": large_input_approval_state,
            "evaluation_context": evaluation_context,
            "generated_at": generated_at,
        },
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evaluate_file(
    *,
    input_path: Path,
    output_path: Path,
    artifact_type: str,
    context_label: str,
    evaluation_context: str,
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
    allow_large_input: bool = False,
    waiver_approved_by: str | None = None,
    waiver_reason: str | None = None,
    waiver_approved_at: str | None = None,
    codex_executable: str = "codex",
    codex_model: str | None = DEFAULT_CODEX_MODEL,
    runner: CommandRunner = subprocess.run,
    now: Callable[[], datetime] = _utc_now,
    work_dir: Path | None = None,
) -> list[str]:
    if max_input_chars < 1:
        raise EvaluationError("--max-input-chars must be at least 1")
    if not artifact_type.strip():
        raise EvaluationError("--artifact-type must be a non-empty string")
    if not context_label.strip():
        raise EvaluationError("--context-label must be a non-empty string")
    if not evaluation_context.strip():
        raise EvaluationError("--evaluation-context must be a non-empty string")

    raw, artifact_text = _read_input(input_path)
    input_size_chars = len(artifact_text)
    semantic_text = _semantic_review_text(artifact_text, input_path, artifact_type)
    semantic_size_chars = len(semantic_text)
    review_size_chars = max(input_size_chars, semantic_size_chars)
    if review_size_chars > max_input_chars and not allow_large_input:
        raise EvaluationError(
            f"input artifact has {review_size_chars} review characters, exceeding --max-input-chars {max_input_chars}; "
            "rerun with --allow-large-input after explicit approval"
        )
    large_input_approval_state = "approved" if review_size_chars > max_input_chars else "not_required"
    input_sha256 = hashlib.sha256(raw).hexdigest()
    generated_at = _format_timestamp(now())
    waiver = _build_waiver_metadata(
        waiver_approved_by=waiver_approved_by,
        waiver_reason=waiver_reason,
        waiver_approved_at=waiver_approved_at,
        generated_at=generated_at,
    )
    scratch_dir = _scratch_dir(output_path)
    scratch_stem = f"{output_path.stem}.{input_sha256[:12]}.{uuid4().hex}"
    semantic_output_path = scratch_dir / f"{scratch_stem}.semantic.json"
    candidate_report_path = scratch_dir / f"{scratch_stem}.candidate-report.json"
    schema_path = _semantic_schema_path()
    if not schema_path.exists():
        raise EvaluationError(f"missing semantic output schema: {schema_path}")

    prompt = build_prompt(
        artifact_text=semantic_text,
        artifact_type=artifact_type,
        context_label=context_label,
        evaluation_context=evaluation_context,
        input_sha256=input_sha256,
        input_size_chars=input_size_chars,
        max_input_size_chars=max_input_chars,
        large_input_approval_state=large_input_approval_state,
    )
    command = _build_codex_command(
        codex_executable=_resolve_codex_executable(codex_executable),
        codex_model=codex_model,
        schema_path=schema_path,
        output_last_message_path=semantic_output_path,
        work_dir=work_dir or Path.cwd(),
    )

    _run_codex(command, prompt, runner)
    semantic_result = _load_semantic_output(semantic_output_path)
    _validate_semantic_result(semantic_result)
    semantic_result = _normalize_semantic_score(semantic_result)
    report = _build_report(
        semantic_result=semantic_result,
        input_path=input_path,
        artifact_type=artifact_type,
        context_label=context_label,
        evaluation_context=evaluation_context,
        input_sha256=input_sha256,
        input_size_chars=input_size_chars,
        max_input_size_chars=max_input_chars,
        large_input_approval_state=large_input_approval_state,
        generated_at=generated_at,
        waiver=waiver,
    )
    _write_json(candidate_report_path, report)
    warnings = validate_report(candidate_report_path, enforce_gate=False, input_file=input_path)
    _write_json(output_path, report)
    return warnings


def main(argv: Sequence[str] | None = None, *, runner: CommandRunner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one text artifact with the Pyramid Principle Text Standard.")
    parser.add_argument("input_artifact", type=Path, help="UTF-8 text artifact to evaluate.")
    parser.add_argument("output_report", type=Path, help="JSON Gate Report path to write.")
    parser.add_argument("--artifact-type", required=True, help="General artifact type, such as tex_section or markdown.")
    parser.add_argument("--context-label", required=True, help="Caller-owned checkpoint label, such as outline or main.")
    parser.add_argument("--evaluation-context", required=True, help="Context that guides the semantic review.")
    parser.add_argument(
        "--max-input-chars",
        type=int,
        default=DEFAULT_MAX_INPUT_CHARS,
        help=f"Maximum input characters before explicit approval is required. Default: {DEFAULT_MAX_INPUT_CHARS}.",
    )
    parser.add_argument(
        "--allow-large-input",
        action="store_true",
        help="Record explicit approval for an input larger than --max-input-chars.",
    )
    parser.add_argument("--waiver-approved-by", help="Human or workflow owner who approved continuation under waiver.")
    parser.add_argument("--waiver-reason", help="Reason continuation is approved despite needs_revision or blocked status.")
    parser.add_argument(
        "--waiver-approved-at",
        help="ISO 8601 approval timestamp. Defaults to the report generation time when omitted.",
    )
    parser.add_argument("--codex-executable", default="codex", help=argparse.SUPPRESS)
    parser.add_argument("--codex-model", default=DEFAULT_CODEX_MODEL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        warnings = evaluate_file(
            input_path=args.input_artifact,
            output_path=args.output_report,
            artifact_type=args.artifact_type,
            context_label=args.context_label,
            evaluation_context=args.evaluation_context,
            max_input_chars=args.max_input_chars,
            allow_large_input=args.allow_large_input,
            waiver_approved_by=args.waiver_approved_by,
            waiver_reason=args.waiver_reason,
            waiver_approved_at=args.waiver_approved_at,
            codex_executable=args.codex_executable,
            codex_model=args.codex_model,
            runner=runner,
        )
    except (EvaluationError, ValidationError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"WROTE: {args.output_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

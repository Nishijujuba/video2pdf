#!/usr/bin/env python3
"""Validate Pyramid Principle gate reports.

The script intentionally avoids third-party dependencies so it can run in the
project's existing Python environments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


STATUSES = {"pass", "needs_revision", "blocked"}
STATUS_RANK = {"blocked": 0, "needs_revision": 1, "pass": 2}
STANDARD_NAME = "Pyramid Principle Text Standard"
BACKENDS = {"codex-exec"}
LARGE_INPUT_APPROVAL_STATES = {"not_required", "approved"}
WAIVER_STATES = {"none", "approved"}
REQUIRED_REPORT_KEYS = {
    "target",
    "artifact_type",
    "context_label",
    "status",
    "score",
    "dimensions",
    "findings",
    "required_revisions",
    "waiver",
    "audit",
}
REQUIRED_WAIVER_KEYS = {"state", "approved_by", "reason", "approved_at"}
REQUIRED_AUDIT_KEYS = {
    "standard_name",
    "backend",
    "prompt_version",
    "input_sha256",
    "input_size_chars",
    "max_input_size_chars",
    "large_input_approval_state",
    "evaluation_context",
    "generated_at",
}
DIMENSION_WEIGHTS = {
    "top_down_clarity": 0.25,
    "support_hierarchy": 0.25,
    "grouping_mece": 0.20,
    "teaching_progression": 0.20,
    "title_body_alignment": 0.10,
}
SEVERITIES = {"info", "minor", "major", "critical"}
EXIT_VALID = 0
EXIT_VALIDATION_FAILURE = 1
EXIT_GATE_BLOCKED = 2
EXIT_MALFORMED_WAIVER = 3


class ValidationError(Exception):
    pass


class GateBlockedError(ValidationError):
    pass


class WaiverValidationError(ValidationError):
    pass


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _validate_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing:
        raise ValidationError(f"{label} missing keys: {', '.join(sorted(missing))}")
    if extra:
        raise ValidationError(f"{label} has unknown keys: {', '.join(sorted(extra))}")


def _require_string(value: Any, label: str, *, allow_null: bool = False) -> str | None:
    if value is None and allow_null:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value


def _require_number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{label} must be finite")
    if number < 0 or number > 1:
        raise ValidationError(f"{label} must be between 0 and 1")
    return number


def _require_integer(value: Any, label: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{label} must be an integer")
    if value < minimum:
        raise ValidationError(f"{label} must be at least {minimum}")
    return value


def _require_datetime(value: Any, label: str, *, allow_null: bool = False) -> str | None:
    text = _require_string(value, label, allow_null=allow_null)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label} must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{label} must include a timezone")
    return text


def _require_sha256(value: Any, label: str) -> str:
    text = _require_string(value, label)
    assert text is not None
    if len(text) != 64 or text.lower() != text or any(char not in "0123456789abcdef" for char in text):
        raise ValidationError(f"{label} must be a lowercase SHA-256 hex digest")
    return text


def _validate_dimensions(report: dict[str, Any]) -> dict[str, float]:
    dimensions = _require_object(report.get("dimensions"), "dimensions")
    _validate_keys(dimensions, set(DIMENSION_WEIGHTS), "dimensions")
    return {
        name: _require_number(dimensions[name], f"dimensions.{name}")
        for name in DIMENSION_WEIGHTS
    }


def _expected_status(score: float) -> str:
    if score >= 0.80:
        return "pass"
    if score >= 0.60:
        return "needs_revision"
    return "blocked"


def _validate_findings(report: dict[str, Any]) -> None:
    findings = report["findings"]
    if not isinstance(findings, list):
        raise ValidationError("findings must be an array")
    for index, finding in enumerate(findings):
        finding = _require_object(finding, f"findings[{index}]")
        _validate_keys(finding, {"severity", "location", "issue", "recommendation"}, f"findings[{index}]")
        for key in ("severity", "location", "issue", "recommendation"):
            _require_string(finding[key], f"findings[{index}].{key}")
        if finding["severity"] not in SEVERITIES:
            raise ValidationError(f"findings[{index}].severity is invalid")


def _validate_required_revisions(report: dict[str, Any]) -> None:
    required_revisions = report["required_revisions"]
    if not isinstance(required_revisions, list):
        raise ValidationError("required_revisions must be an array")
    for index, item in enumerate(required_revisions):
        _require_string(item, f"required_revisions[{index}]")


def _validate_waiver(report: dict[str, Any]) -> None:
    try:
        waiver = _require_object(report["waiver"], "waiver")
        _validate_keys(waiver, REQUIRED_WAIVER_KEYS, "waiver")

        state = _require_string(waiver["state"], "waiver.state")
        if state not in WAIVER_STATES:
            raise ValidationError(f"waiver.state must be one of: {', '.join(sorted(WAIVER_STATES))}")

        if state == "none":
            for key in ("approved_by", "reason", "approved_at"):
                if waiver[key] is not None:
                    raise ValidationError(f"waiver.{key} must be null when waiver.state is 'none'")
            return

        approved_by = _require_string(waiver["approved_by"], "waiver.approved_by")
        reason = _require_string(waiver["reason"], "waiver.reason")
        approved_at = _require_datetime(waiver["approved_at"], "waiver.approved_at")
        if not approved_by or not reason or not approved_at:
            raise ValidationError("approved waivers require approved_by, reason, and approved_at")
        if report["status"] == "pass":
            raise ValidationError("approved waivers are only valid for needs_revision or blocked reports")
    except ValidationError as exc:
        raise WaiverValidationError(str(exc)) from exc


def _validate_audit(report: dict[str, Any]) -> list[str]:
    audit = _require_object(report["audit"], "audit")
    _validate_keys(audit, REQUIRED_AUDIT_KEYS, "audit")

    standard_name = _require_string(audit["standard_name"], "audit.standard_name")
    if standard_name != STANDARD_NAME:
        raise ValidationError(f"audit.standard_name must be {STANDARD_NAME!r}")

    backend = _require_string(audit["backend"], "audit.backend")
    if backend not in BACKENDS:
        raise ValidationError(f"audit.backend must be one of: {', '.join(sorted(BACKENDS))}")

    _require_string(audit["prompt_version"], "audit.prompt_version")
    _require_sha256(audit["input_sha256"], "audit.input_sha256")
    input_size = _require_integer(audit["input_size_chars"], "audit.input_size_chars", minimum=0)
    max_input_size = _require_integer(audit["max_input_size_chars"], "audit.max_input_size_chars", minimum=1)

    approval_state = _require_string(audit["large_input_approval_state"], "audit.large_input_approval_state")
    if approval_state not in LARGE_INPUT_APPROVAL_STATES:
        raise ValidationError(
            "audit.large_input_approval_state must be one of: "
            + ", ".join(sorted(LARGE_INPUT_APPROVAL_STATES))
        )
    if input_size > max_input_size and approval_state != "approved":
        raise ValidationError("large inputs require audit.large_input_approval_state 'approved'")

    _require_string(audit["evaluation_context"], "audit.evaluation_context")
    _require_datetime(audit["generated_at"], "audit.generated_at")

    warnings: list[str] = []
    if input_size <= max_input_size and approval_state == "approved":
        warnings.append("large_input_approval_state is approved even though input_size_chars is within max_input_size_chars")
    return warnings


def _validate_input_file(report: dict[str, Any], input_file: Path) -> None:
    try:
        raw = input_file.read_bytes()
    except OSError as exc:
        raise ValidationError(f"cannot read input file: {input_file}") from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"input file must be UTF-8 text: {input_file}") from exc

    audit = _require_object(report["audit"], "audit")
    actual_hash = hashlib.sha256(raw).hexdigest()
    if audit["input_sha256"] != actual_hash:
        raise ValidationError("audit.input_sha256 does not match --input-file")

    actual_size = len(text)
    if audit["input_size_chars"] != actual_size:
        raise ValidationError("audit.input_size_chars does not match --input-file")


def validate_report(
    path: Path,
    *,
    enforce_gate: bool,
    allow_waiver: bool = False,
    input_file: Path | None = None,
) -> list[str]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc

    report = _require_object(report, "report")
    _validate_keys(report, REQUIRED_REPORT_KEYS, "report")

    _require_string(report["target"], "target")
    _require_string(report["artifact_type"], "artifact_type")
    _require_string(report["context_label"], "context_label")

    status = _require_string(report["status"], "status")
    if status not in STATUSES:
        raise ValidationError(f"status must be one of: {', '.join(sorted(STATUSES))}")

    score = _require_number(report["score"], "score")
    dimensions = _validate_dimensions(report)
    calculated = sum(dimensions[name] * weight for name, weight in DIMENSION_WEIGHTS.items())
    warnings: list[str] = []
    if abs(calculated - score) > 0.015:
        raise ValidationError(f"score {score:.3f} differs from weighted dimensions {calculated:.3f}")

    _validate_findings(report)
    _validate_required_revisions(report)
    _validate_waiver(report)
    warnings.extend(_validate_audit(report))
    if input_file is not None:
        _validate_input_file(report, input_file)
    findings = report["findings"]
    required_revisions = report["required_revisions"]

    expected = _expected_status(score)
    if STATUS_RANK[status] > STATUS_RANK[expected]:
        raise ValidationError(f"status {status!r} is more permissive than score-derived status {expected!r}")
    if STATUS_RANK[status] < STATUS_RANK[expected]:
        warnings.append(f"status {status!r} does not match score-derived status {expected!r}")

    if status == "pass" and required_revisions:
        raise ValidationError("pass reports cannot contain required_revisions")
    if status in {"needs_revision", "blocked"} and not findings:
        raise ValidationError(f"{status} reports must include at least one finding")
    if status in {"needs_revision", "blocked"} and not required_revisions:
        raise ValidationError(f"{status} reports must include required_revisions")

    if enforce_gate and status in {"needs_revision", "blocked"} and not (
        allow_waiver and report["waiver"]["state"] == "approved"
    ):
        raise GateBlockedError(f"gate status {status!r} blocks the workflow")

    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Pyramid Principle gate report.")
    parser.add_argument("report", type=Path, help="Path to a pyramid gate JSON report.")
    parser.add_argument(
        "--enforce-gate",
        action="store_true",
        help="Return failure for needs_revision or blocked reports.",
    )
    parser.add_argument(
        "--allow-waiver",
        action="store_true",
        help="Allow an approved waiver in the report to satisfy --enforce-gate.",
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        help="Recompute the reviewed input fingerprint and fail if the report is stale.",
    )
    args = parser.parse_args()

    try:
        warnings = validate_report(
            args.report,
            enforce_gate=args.enforce_gate,
            allow_waiver=args.allow_waiver,
            input_file=args.input_file,
        )
    except WaiverValidationError as exc:
        print(f"WAIVER_INVALID: {exc}", file=sys.stderr)
        return EXIT_MALFORMED_WAIVER
    except GateBlockedError as exc:
        print(f"GATE_BLOCKED: {exc}", file=sys.stderr)
        return EXIT_GATE_BLOCKED
    except ValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_FAILURE

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"VALID: {args.report}")
    return EXIT_VALID


if __name__ == "__main__":
    raise SystemExit(main())

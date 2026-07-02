#!/usr/bin/env python3
"""Validate required Pyramid Gate reports for one video output directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from validate_report import (
    EXIT_GATE_BLOCKED,
    EXIT_MALFORMED_WAIVER,
    EXIT_VALIDATION_FAILURE,
    GateBlockedError,
    ValidationError,
    WaiverValidationError,
    validate_report,
)


CHECKPOINT_METADATA = {
    "outline.pyramid.json": ("outline_contract.md", "outline_contract", "outline"),
    "main.pyramid.json": ("main.tex", "tex_document", "main"),
}


def _read_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_cell(value: object) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|").strip()
    return text or "None"


def _format_required_revisions(report: dict[str, Any]) -> str:
    revisions = report["required_revisions"]
    if not revisions:
        return "None"
    return "; ".join(_format_cell(item) for item in revisions)


def _format_waiver_reason(report: dict[str, Any]) -> str:
    waiver = report["waiver"]
    if waiver["state"] != "approved":
        return "None"
    return _format_cell(waiver["reason"])


def write_summary(review_dir: Path, reports: list[Path]) -> Path:
    summary_path = review_dir / "summary.md"
    lines = [
        "# Pyramid Gate Summary",
        "",
        "| Checkpoint | Report | Status | Score | Required revisions | Waiver reason |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for report_path in reports:
        report = _read_report(report_path)
        lines.append(
            "| {checkpoint} | {report} | {status} | {score:.2f} | {revisions} | {waiver} |".format(
                checkpoint=_format_cell(report["context_label"]),
                report=report_path.name,
                status=_format_cell(report["status"]),
                score=float(report["score"]),
                revisions=_format_required_revisions(report),
                waiver=_format_waiver_reason(report),
            )
        )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def source_for_report(output_dir: Path, report: Path) -> Path:
    if report.name in CHECKPOINT_METADATA:
        return output_dir / CHECKPOINT_METADATA[report.name][0]
    if report.name.startswith("section_") and report.name.endswith(".pyramid.json"):
        return output_dir / f"{report.name.removesuffix('.pyramid.json')}.tex"
    raise ValidationError(f"unknown pyramid report name: {report}")


def _expected_report_metadata(report: Path) -> tuple[str, str]:
    if report.name in CHECKPOINT_METADATA:
        return CHECKPOINT_METADATA[report.name][1], CHECKPOINT_METADATA[report.name][2]
    if report.name.startswith("section_") and report.name.endswith(".pyramid.json"):
        section_label = report.name.removesuffix(".pyramid.json")
        return "tex_section", section_label
    raise ValidationError(f"unknown pyramid report name: {report}")


def _validate_checkpoint_metadata(report_path: Path, source_path: Path) -> None:
    report = _read_report(report_path)
    expected_artifact_type, expected_context_label = _expected_report_metadata(report_path)
    if report["artifact_type"] != expected_artifact_type:
        raise ValidationError(
            f"{report_path} artifact_type must be {expected_artifact_type!r} for this checkpoint"
        )
    if report["context_label"] != expected_context_label:
        raise ValidationError(
            f"{report_path} context_label must be {expected_context_label!r} for this checkpoint"
        )
    report_target = Path(report["target"])
    if report_target.resolve(strict=False) != source_path.resolve(strict=False):
        raise ValidationError(f"{report_path} target must resolve to {source_path}")


def _section_report_for_source(review_dir: Path, section_source: Path) -> Path:
    return review_dir / f"{section_source.stem}.pyramid.json"


def check_output_dir(
    output_dir: Path,
    *,
    enforce_gate: bool,
    allow_no_sections: bool,
    allow_waivers: bool = False,
) -> list[Path]:
    review_dir = output_dir / "review" / "pyramid"
    if not review_dir.exists():
        raise ValidationError(f"missing pyramid review directory: {review_dir}")

    required = [
        review_dir / "outline.pyramid.json",
        review_dir / "main.pyramid.json",
    ]
    section_sources = sorted(output_dir.glob("section_*.tex"))
    expected_section_reports = [_section_report_for_source(review_dir, source) for source in section_sources]
    existing_section_reports = sorted(review_dir.glob("section_*.pyramid.json"))
    section_reports = sorted(set(expected_section_reports) | set(existing_section_reports))
    if not allow_no_sections and not existing_section_reports:
        raise ValidationError(f"missing section pyramid reports under: {review_dir}")

    missing = [path for path in [required[0], *expected_section_reports, required[1]] if not path.exists()]
    if missing:
        raise ValidationError("missing required pyramid reports: " + ", ".join(str(path) for path in missing))

    reports = [required[0], *section_reports, required[1]]
    for report in reports:
        source_path = source_for_report(output_dir, report)
        validate_report(
            report,
            enforce_gate=enforce_gate,
            allow_waiver=allow_waivers,
            input_file=source_path,
        )
        _validate_checkpoint_metadata(report, source_path)
    write_summary(review_dir, reports)
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Pyramid Gate reports for a video output directory.")
    parser.add_argument("output_dir", type=Path, help="Video output directory containing review/pyramid reports.")
    parser.add_argument(
        "--enforce-gate",
        action="store_true",
        help="Fail when any report has needs_revision or blocked status.",
    )
    parser.add_argument(
        "--allow-no-sections",
        action="store_true",
        help="Allow outputs without section_*.pyramid.json reports.",
    )
    parser.add_argument(
        "--allow-waivers",
        action="store_true",
        help="Allow reports with approved waiver metadata to satisfy --enforce-gate.",
    )
    args = parser.parse_args()

    try:
        reports = check_output_dir(
            args.output_dir,
            enforce_gate=args.enforce_gate,
            allow_no_sections=args.allow_no_sections,
            allow_waivers=args.allow_waivers,
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

    print(f"VALID: {args.output_dir}")
    for report in reports:
        print(f"  {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate Final Delivery Acceptance reports and gate decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from validate_acceptance_criteria import ALLOWED_CATEGORIES, ValidationError as CriteriaValidationError
from validate_acceptance_criteria import validate_acceptance_criteria


REPO_ROOT = Path(__file__).resolve().parents[4]
REPORT_KEYS = {
    "schema_version",
    "criteria_version",
    "criteria_file",
    "overall_status",
    "decision_source",
    "review_context_used",
    "artifact_fingerprints",
    "criterion_results",
    "visual_scan_evidence",
    "failed_criteria",
    "revision_required",
}
REVIEW_CONTEXT_KEYS = {
    "allowed_artifacts_manifest",
    "final_artifacts_only",
    "generation_process_used",
    "artifacts_read",
}
FINGERPRINT_KEYS = {"path", "sha256", "size_bytes", "size_chars"}
CRITERION_RESULT_KEYS = {
    "criterion_id",
    "category",
    "status",
    "evidence",
    "scan_evidence",
    "revision_guidance",
}
EVIDENCE_KEYS = {"artifact_path", "location", "summary"}
REVISION_GUIDANCE_KEYS = {"required_change", "allowed_fix_types"}
VISUAL_SCAN_KEYS = {"pdf", "page_count", "rendered_pages_dir", "pages_checked"}
PAGE_CHECK_KEYS = {"page", "rendered_page_image", "status", "criteria_checked", "failures"}
PAGE_FAILURE_KEYS = {"criterion_id", "category", "visible_defect", "rendered_page_image", "pdf_page"}
FORMULA_SCAN_KEYS = {"scan_policy", "scanned_artifacts", "formulas_checked", "no_body_formula_found"}
FORMULA_CHECK_KEYS = {"location", "formula_excerpt", "source_type", "status", "information_gain_summary"}
MANIFEST_KEYS = {"criteria_file", "review_output_dir", "final_artifacts", "forbidden_artifacts"}
MANIFEST_ARTIFACT_KEYS = {"role", "path"}
FORBIDDEN_ARTIFACTS = [
    "generation_notes",
    "writer_drafts",
    "chat_history",
    "intermediate_files",
    "work/",
    "review/consistency/",
    "review/pyramid/",
]
TEXT_CATEGORIES = {"style", "logic_readability"}
FORMULA_CATEGORIES = {"formula_information_gain"}
FORMULA_SOURCE_TYPES = {"source_material", "inherent_quantitative", "interpretive_teaching_model"}
VISUAL_CATEGORIES = {
    "figure_visual_integrity",
    "table_layout_integrity",
    "credibility_disclosure_placement",
}
OVERALL_STATUSES = {"pass", "fail"}
RESULT_STATUSES = {"pass", "fail"}
EXIT_VALID = 0
EXIT_INVALID = 1
EXIT_GATE_BLOCKED = 2


class ValidationError(Exception):
    """Raised when an Acceptance Report is malformed or stale."""


class GateBlockedError(ValidationError):
    """Raised when a valid report blocks delivery."""


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


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{label} must be a boolean")
    return value


def _require_int(value: Any, label: str, *, minimum: int = 0, allow_null: bool = False) -> int | None:
    if value is None and allow_null:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{label} must be an integer")
    if value < minimum:
        raise ValidationError(f"{label} must be at least {minimum}")
    return value


def _require_string_array(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be an array")
    if not allow_empty and not value:
        raise ValidationError(f"{label} must not be empty")
    return [_require_string(item, f"{label}[{index}]") or "" for index, item in enumerate(value)]


def _normalize_relative_path(value: Any, label: str) -> str:
    text = _require_string(value, label)
    assert text is not None
    if re.match(r"^[A-Za-z]:", text):
        raise ValidationError(f"{label} must be a relative path")
    normalized = text.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValidationError(f"{label} must be a relative path")
    path = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValidationError(f"{label} must not contain empty, current, or parent path segments")
    return path.as_posix()


def _path_under(base: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _repo_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.name


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} invalid JSON: {exc}") from exc


def compute_artifact_fingerprint(path: Path, relative_path: str) -> dict[str, Any]:
    """Return the report fingerprint object for one final artifact."""

    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValidationError(f"final artifact not found: {relative_path}") from exc
    size_chars: int | None
    try:
        size_chars = len(raw.decode("utf-8"))
    except UnicodeDecodeError:
        size_chars = None
    return {
        "path": _normalize_relative_path(relative_path, "artifact path"),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
        "size_chars": size_chars,
    }


def create_allowed_artifacts_manifest(
    video_output_dir: Path,
    criteria_path: Path,
    artifacts: list[tuple[str, str]],
) -> Path:
    """Create review/acceptance/allowed_artifacts_manifest.json."""

    video_output_dir = video_output_dir.resolve()
    if not video_output_dir.exists():
        raise ValidationError(f"video output directory not found: {video_output_dir}")
    acceptance_dir = video_output_dir / "review" / "acceptance"
    acceptance_dir.mkdir(parents=True, exist_ok=True)

    final_artifacts: list[dict[str, str]] = []
    for index, (role, artifact_path) in enumerate(artifacts):
        role = _require_string(role, f"artifacts[{index}].role") or ""
        normalized = _normalize_relative_path(artifact_path, f"artifacts[{index}].path")
        resolved = (video_output_dir / normalized).resolve()
        if not _path_under(video_output_dir, resolved):
            raise ValidationError(f"artifact path escapes video output directory: {artifact_path}")
        if not resolved.exists():
            raise ValidationError(f"final artifact not found: {normalized}")
        final_artifacts.append({"role": role, "path": normalized})

    manifest = {
        "criteria_file": _repo_relative(criteria_path),
        "review_output_dir": "review/acceptance",
        "final_artifacts": final_artifacts,
        "forbidden_artifacts": FORBIDDEN_ARTIFACTS,
    }
    manifest_path = acceptance_dir / "allowed_artifacts_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = _require_object(_load_json(manifest_path, "allowed artifacts manifest"), "manifest")
    _validate_keys(manifest, MANIFEST_KEYS, "manifest")
    _normalize_relative_path(manifest["criteria_file"], "manifest.criteria_file")
    _normalize_relative_path(manifest["review_output_dir"], "manifest.review_output_dir")
    artifacts = manifest["final_artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ValidationError("manifest.final_artifacts must be a non-empty array")
    seen: set[str] = set()
    for index, artifact in enumerate(artifacts):
        artifact = _require_object(artifact, f"manifest.final_artifacts[{index}]")
        _validate_keys(artifact, MANIFEST_ARTIFACT_KEYS, f"manifest.final_artifacts[{index}]")
        _require_string(artifact["role"], f"manifest.final_artifacts[{index}].role")
        path = _normalize_relative_path(artifact["path"], f"manifest.final_artifacts[{index}].path")
        if path in seen:
            raise ValidationError(f"manifest.final_artifacts[{index}].path is duplicated: {path}")
        seen.add(path)
    _require_string_array(manifest["forbidden_artifacts"], "manifest.forbidden_artifacts")
    return manifest


def _criteria_items(criteria_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        validate_acceptance_criteria(criteria_path)
    except CriteriaValidationError as exc:
        raise ValidationError(f"criteria invalid: {exc}") from exc
    criteria = _require_object(_load_json(criteria_path, "criteria"), "criteria")
    by_id: dict[str, dict[str, Any]] = {}
    for item in criteria["criteria"]:
        item_obj = _require_object(item, "criteria item")
        criterion_id = _require_string(item_obj["id"], "criteria item id") or ""
        by_id[criterion_id] = item_obj
    return criteria, by_id


def _load_report(report_path: Path) -> dict[str, Any]:
    report = _require_object(_load_json(report_path, "acceptance report"), "report")
    _validate_keys(report, REPORT_KEYS, "report")
    return report


def _validate_review_context(report: dict[str, Any], manifest: dict[str, Any]) -> None:
    context = _require_object(report["review_context_used"], "review_context_used")
    _validate_keys(context, REVIEW_CONTEXT_KEYS, "review_context_used")
    expected_manifest = f"{manifest['review_output_dir']}/allowed_artifacts_manifest.json"
    actual_manifest = _normalize_relative_path(context["allowed_artifacts_manifest"], "review_context_used.allowed_artifacts_manifest")
    if actual_manifest != expected_manifest:
        raise ValidationError("review_context_used.allowed_artifacts_manifest does not match manifest path")
    if _require_bool(context["final_artifacts_only"], "review_context_used.final_artifacts_only") is not True:
        raise ValidationError("review_context_used.final_artifacts_only must be true")
    if _require_bool(context["generation_process_used"], "review_context_used.generation_process_used") is not False:
        raise ValidationError("generation_process_used must be false")

    allowed = {artifact["path"] for artifact in manifest["final_artifacts"]}
    allowed.add(manifest["criteria_file"])
    artifacts_read = _require_string_array(context["artifacts_read"], "review_context_used.artifacts_read")
    for index, item in enumerate(artifacts_read):
        path = _normalize_relative_path(item, f"review_context_used.artifacts_read[{index}]")
        if path not in allowed:
            raise ValidationError(f"review_context_used.artifacts_read[{index}] is outside allowed artifacts")


def _validate_fingerprints(report: dict[str, Any], manifest: dict[str, Any], video_output_dir: Path) -> None:
    fingerprints = report["artifact_fingerprints"]
    if not isinstance(fingerprints, list):
        raise ValidationError("artifact_fingerprints must be an array")
    expected_paths = [artifact["path"] for artifact in manifest["final_artifacts"]]
    by_path: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(fingerprints):
        item = _require_object(item, f"artifact_fingerprints[{index}]")
        _validate_keys(item, FINGERPRINT_KEYS, f"artifact_fingerprints[{index}]")
        path = _normalize_relative_path(item["path"], f"artifact_fingerprints[{index}].path")
        if path in by_path:
            raise ValidationError(f"artifact_fingerprints entry is duplicated: {path}")
        by_path[path] = item
        _require_string(item["sha256"], f"artifact_fingerprints[{index}].sha256")
        _require_int(item["size_bytes"], f"artifact_fingerprints[{index}].size_bytes", minimum=0)
        _require_int(item["size_chars"], f"artifact_fingerprints[{index}].size_chars", minimum=0, allow_null=True)

    if set(by_path) != set(expected_paths):
        missing = set(expected_paths) - set(by_path)
        extra = set(by_path) - set(expected_paths)
        if missing:
            raise ValidationError(f"artifact_fingerprints missing entries: {', '.join(sorted(missing))}")
        raise ValidationError(f"artifact_fingerprints has entries outside manifest: {', '.join(sorted(extra))}")

    for path in expected_paths:
        current = compute_artifact_fingerprint(video_output_dir / path, path)
        if by_path[path] != current:
            raise ValidationError(f"artifact_fingerprints entry is stale: {path}")


def _validate_evidence_list(evidence: Any, label: str, allowed_paths: set[str], *, require_non_empty: bool) -> None:
    if not isinstance(evidence, list):
        raise ValidationError(f"{label} must be an array")
    if require_non_empty and not evidence:
        raise ValidationError(f"{label} must not be empty")
    for index, item in enumerate(evidence):
        item = _require_object(item, f"{label}[{index}]")
        _validate_keys(item, EVIDENCE_KEYS, f"{label}[{index}]")
        artifact_path = _normalize_relative_path(item["artifact_path"], f"{label}[{index}].artifact_path")
        if artifact_path not in allowed_paths:
            raise ValidationError("evidence path is outside allowed final artifacts")
        _require_string(item["location"], f"{label}[{index}].location")
        _require_string(item["summary"], f"{label}[{index}].summary")


def _validate_revision_guidance(value: Any, label: str) -> None:
    guidance = _require_object(value, label)
    _validate_keys(guidance, REVISION_GUIDANCE_KEYS, label)
    _require_string(guidance["required_change"], f"{label}.required_change")
    _require_string_array(guidance["allowed_fix_types"], f"{label}.allowed_fix_types", allow_empty=False)


def _validate_formula_scan_evidence(value: Any, label: str, *, result_status: str) -> None:
    scan = _require_object(value, label)
    _validate_keys(scan, FORMULA_SCAN_KEYS, "formula scan evidence")
    scan_policy = _require_string(scan["scan_policy"], f"{label}.scan_policy")
    if scan_policy != "full_artifact_formula_scan":
        raise ValidationError(f"{label}.scan_policy must be 'full_artifact_formula_scan'")
    _require_string_array(scan["scanned_artifacts"], f"{label}.scanned_artifacts", allow_empty=False)
    no_body_formula_found = _require_bool(scan["no_body_formula_found"], f"{label}.no_body_formula_found")
    formulas = scan["formulas_checked"]
    if not isinstance(formulas, list):
        raise ValidationError(f"{label}.formulas_checked must be an array")
    if no_body_formula_found and formulas:
        raise ValidationError("no_body_formula_found must be false when formulas_checked is non-empty")
    if not no_body_formula_found and not formulas:
        raise ValidationError("formulas_checked must not be empty when no_body_formula_found is false")

    formula_statuses: list[str] = []
    for index, formula in enumerate(formulas):
        formula_obj = _require_object(formula, f"{label}.formulas_checked[{index}]")
        _validate_keys(formula_obj, FORMULA_CHECK_KEYS, f"{label}.formulas_checked[{index}]")
        _require_string(formula_obj["location"], f"{label}.formulas_checked[{index}].location")
        _require_string(formula_obj["formula_excerpt"], f"{label}.formulas_checked[{index}].formula_excerpt")
        source_type = _require_string(formula_obj["source_type"], f"{label}.formulas_checked[{index}].source_type")
        if source_type not in FORMULA_SOURCE_TYPES:
            raise ValidationError(f"{label}.formulas_checked[{index}].source_type is invalid")
        status = _require_string(formula_obj["status"], f"{label}.formulas_checked[{index}].status")
        if status not in RESULT_STATUSES:
            raise ValidationError(f"{label}.formulas_checked[{index}].status is invalid")
        formula_statuses.append(status)
        _require_string(
            formula_obj["information_gain_summary"],
            f"{label}.formulas_checked[{index}].information_gain_summary",
        )

    failed_formula_count = sum(1 for status in formula_statuses if status == "fail")
    if result_status == "pass" and failed_formula_count:
        raise ValidationError("passing formula criterion cannot include failed formula checks")
    if result_status == "fail" and failed_formula_count == 0:
        raise ValidationError("failed formula criterion requires at least one failed formula check")


def _validate_criterion_results(
    report: dict[str, Any],
    criteria_by_id: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
) -> set[str]:
    results = report["criterion_results"]
    if not isinstance(results, list):
        raise ValidationError("criterion_results must be an array")
    expected_ids = set(criteria_by_id)
    seen_ids: set[str] = set()
    failed_ids: set[str] = set()
    allowed_evidence_paths = {artifact["path"] for artifact in manifest["final_artifacts"]}
    allowed_evidence_paths.add(manifest["criteria_file"])

    for index, result in enumerate(results):
        result = _require_object(result, f"criterion_results[{index}]")
        _validate_keys(result, CRITERION_RESULT_KEYS, f"criterion_results[{index}]")
        criterion_id = _require_string(result["criterion_id"], f"criterion_results[{index}].criterion_id") or ""
        if criterion_id not in criteria_by_id:
            raise ValidationError(f"criterion_results[{index}].criterion_id is not configured: {criterion_id}")
        if criterion_id in seen_ids:
            raise ValidationError(f"criterion_results[{index}].criterion_id is duplicated: {criterion_id}")
        seen_ids.add(criterion_id)

        expected_category = criteria_by_id[criterion_id]["category"]
        category = _require_string(result["category"], f"criterion_results[{index}].category")
        if category != expected_category:
            raise ValidationError(f"criterion_results[{index}].category does not match criteria")
        status = _require_string(result["status"], f"criterion_results[{index}].status")
        if status not in RESULT_STATUSES:
            raise ValidationError(f"criterion_results[{index}].status is invalid")

        is_fail = status == "fail"
        _validate_evidence_list(
            result["evidence"],
            f"criterion_results[{index}].evidence",
            allowed_evidence_paths,
            require_non_empty=is_fail,
        )
        if is_fail:
            failed_ids.add(criterion_id)
            if result["revision_guidance"] is None:
                raise ValidationError("failed criterion requires revision_guidance")
            _validate_revision_guidance(result["revision_guidance"], f"criterion_results[{index}].revision_guidance")
        elif result["revision_guidance"] is not None:
            raise ValidationError("pass criterion revision_guidance must be null")
        if category in FORMULA_CATEGORIES:
            _validate_formula_scan_evidence(
                result["scan_evidence"],
                f"criterion_results[{index}].scan_evidence",
                result_status=status,
            )

    if seen_ids != expected_ids:
        missing = expected_ids - seen_ids
        extra = seen_ids - expected_ids
        if missing:
            raise ValidationError(f"criterion_results missing configured criteria: {', '.join(sorted(missing))}")
        raise ValidationError(f"criterion_results has extra criteria: {', '.join(sorted(extra))}")
    return failed_ids


def _pdf_page_count(pdf_path: Path) -> int:
    try:
        import fitz
    except ImportError as exc:
        raise ValidationError("PyMuPDF is required to validate rendered PDF page coverage") from exc
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ValidationError(f"cannot open PDF for page count: {pdf_path}") from exc
    try:
        return len(doc)
    finally:
        doc.close()


def _validate_page_failure(
    failure: Any,
    label: str,
    page: int,
    rendered_image: str,
    criteria_by_id: dict[str, dict[str, Any]],
    failed_criterion_ids: set[str],
) -> None:
    failure = _require_object(failure, label)
    _validate_keys(failure, PAGE_FAILURE_KEYS, label)
    criterion_id = _require_string(failure["criterion_id"], f"{label}.criterion_id") or ""
    if criterion_id not in criteria_by_id:
        raise ValidationError(f"{label}.criterion_id is not configured")
    category = _require_string(failure["category"], f"{label}.category")
    if category != criteria_by_id[criterion_id]["category"]:
        raise ValidationError(f"{label}.category does not match criterion")
    if category not in VISUAL_CATEGORIES:
        raise ValidationError(f"{label}.category must be a visual category")
    _require_string(failure["visible_defect"], f"{label}.visible_defect")
    if _normalize_relative_path(failure["rendered_page_image"], f"{label}.rendered_page_image") != rendered_image:
        raise ValidationError(f"{label}.rendered_page_image must match page evidence")
    if _require_int(failure["pdf_page"], f"{label}.pdf_page", minimum=1) != page:
        raise ValidationError(f"{label}.pdf_page must match page number")
    if criterion_id not in failed_criterion_ids:
        raise ValidationError(f"{label}.criterion_id must have a failed criterion result")


def _validate_visual_scan(
    report: dict[str, Any],
    criteria_by_id: dict[str, dict[str, Any]],
    failed_criterion_ids: set[str],
    manifest: dict[str, Any],
    video_output_dir: Path,
) -> None:
    visual_criterion_ids = {
        criterion_id for criterion_id, item in criteria_by_id.items() if item["category"] in VISUAL_CATEGORIES
    }
    visual_categories = {item["category"] for item in criteria_by_id.values() if item["category"] in VISUAL_CATEGORIES}
    failed_visual_criterion_ids = {
        criterion_id
        for criterion_id in failed_criterion_ids
        if criteria_by_id[criterion_id]["category"] in VISUAL_CATEGORIES
    }
    page_failure_criterion_ids: set[str] = set()
    visual = report["visual_scan_evidence"]
    if not visual_criterion_ids:
        if visual is not None:
            raise ValidationError("visual_scan_evidence must be null when no visual criteria are configured")
        return
    visual = _require_object(visual, "visual_scan_evidence")
    _validate_keys(visual, VISUAL_SCAN_KEYS, "visual_scan_evidence")

    pdf = _normalize_relative_path(visual["pdf"], "visual_scan_evidence.pdf")
    pdf_artifacts = {artifact["path"] for artifact in manifest["final_artifacts"] if artifact["role"] == "pdf"}
    if pdf not in pdf_artifacts:
        raise ValidationError("visual_scan_evidence.pdf must be a manifest PDF artifact")
    page_count = _require_int(visual["page_count"], "visual_scan_evidence.page_count", minimum=1)
    actual_count = _pdf_page_count(video_output_dir / pdf)
    if page_count != actual_count:
        raise ValidationError("visual_scan_evidence.page_count disagrees with rendered PDF")

    rendered_pages_dir = _normalize_relative_path(visual["rendered_pages_dir"], "visual_scan_evidence.rendered_pages_dir")
    expected_rendered_dir = f"{manifest['review_output_dir']}/rendered_pages"
    if rendered_pages_dir != expected_rendered_dir:
        raise ValidationError("visual_scan_evidence.rendered_pages_dir is invalid")

    pages_checked = visual["pages_checked"]
    if not isinstance(pages_checked, list):
        raise ValidationError("visual_scan_evidence.pages_checked must be an array")
    page_numbers: list[int] = []
    for index, page_entry in enumerate(pages_checked):
        page_entry = _require_object(page_entry, f"visual_scan_evidence.pages_checked[{index}]")
        _validate_keys(page_entry, PAGE_CHECK_KEYS, f"visual_scan_evidence.pages_checked[{index}]")
        page = _require_int(page_entry["page"], f"visual_scan_evidence.pages_checked[{index}].page", minimum=1)
        assert page is not None
        page_numbers.append(page)
        expected_image = f"{rendered_pages_dir}/page_{page:04d}.png"
        rendered_image = _normalize_relative_path(
            page_entry["rendered_page_image"],
            f"visual_scan_evidence.pages_checked[{index}].rendered_page_image",
        )
        if rendered_image != expected_image:
            raise ValidationError("visual_scan_evidence.pages_checked rendered image path is misnumbered")
        if not (video_output_dir / rendered_image).exists():
            raise ValidationError(f"rendered page image is missing: {rendered_image}")
        status = _require_string(page_entry["status"], f"visual_scan_evidence.pages_checked[{index}].status")
        if status not in RESULT_STATUSES:
            raise ValidationError("visual_scan_evidence.pages_checked status is invalid")
        checked = set(_require_string_array(page_entry["criteria_checked"], f"visual_scan_evidence.pages_checked[{index}].criteria_checked"))
        if checked != visual_categories:
            raise ValidationError("visual_scan_evidence.pages_checked criteria_checked must include every visual category")
        failures = page_entry["failures"]
        if not isinstance(failures, list):
            raise ValidationError("visual_scan_evidence.pages_checked failures must be an array")
        if status == "fail" and not failures:
            raise ValidationError("failed page entry requires failures")
        if status == "pass" and failures:
            raise ValidationError("passing page entry must not include failures")
        for failure_index, failure in enumerate(failures):
            _validate_page_failure(
                failure,
                f"visual_scan_evidence.pages_checked[{index}].failures[{failure_index}]",
                page,
                rendered_image,
                criteria_by_id,
                failed_criterion_ids,
            )
            failure_obj = _require_object(
                failure,
                f"visual_scan_evidence.pages_checked[{index}].failures[{failure_index}]",
            )
            page_failure_criterion_ids.add(str(failure_obj["criterion_id"]))

    if page_numbers != list(range(1, page_count + 1)):
        raise ValidationError("visual_scan_evidence.pages_checked must cover every page exactly once")
    if not failed_visual_criterion_ids.issubset(page_failure_criterion_ids):
        raise ValidationError("failed visual criteria require page failure evidence")


def _validate_decision_consistency(report: dict[str, Any], failed_criterion_ids: set[str]) -> None:
    status = _require_string(report["overall_status"], "overall_status")
    if status not in OVERALL_STATUSES:
        raise ValidationError("overall_status must be pass or fail")
    if report["decision_source"] != "acceptance_report_json":
        raise ValidationError("decision_source must be acceptance_report_json")
    failed_criteria = set(_require_string_array(report["failed_criteria"], "failed_criteria"))
    if failed_criteria != failed_criterion_ids:
        raise ValidationError("failed_criteria must match failed criterion results")
    revision_required = _require_bool(report["revision_required"], "revision_required")
    if status == "pass":
        if failed_criterion_ids:
            raise ValidationError("overall_status pass conflicts with failed criteria")
        if revision_required:
            raise ValidationError("revision_required must be false when overall_status is pass")
    else:
        if not failed_criterion_ids:
            raise ValidationError("overall_status fail requires failed criteria")
        if not revision_required:
            raise ValidationError("revision_required must be true when overall_status is fail")


def validate_acceptance_report(
    report_path: Path,
    *,
    criteria_path: Path,
    video_output_dir: Path,
    manifest_path: Path,
    enforce_decision: bool,
) -> list[str]:
    """Validate an Acceptance Report and optionally enforce delivery status."""

    video_output_dir = video_output_dir.resolve()
    report = _load_report(report_path)
    manifest = _load_manifest(manifest_path)
    criteria, criteria_by_id = _criteria_items(criteria_path)

    if report["schema_version"] != "1.0":
        raise ValidationError("schema_version must be '1.0'")
    if report["criteria_version"] != criteria["criteria_version"]:
        raise ValidationError("criteria_version does not match criteria file")
    expected_criteria_file = _repo_relative(criteria_path)
    if report["criteria_file"] != expected_criteria_file or manifest["criteria_file"] != expected_criteria_file:
        raise ValidationError("criteria_file does not match criteria path")

    _validate_review_context(report, manifest)
    _validate_fingerprints(report, manifest, video_output_dir)
    failed_criterion_ids = _validate_criterion_results(report, criteria_by_id, manifest)
    _validate_decision_consistency(report, failed_criterion_ids)
    _validate_visual_scan(report, criteria_by_id, failed_criterion_ids, manifest, video_output_dir)

    if enforce_decision and report["overall_status"] == "fail":
        raise GateBlockedError("acceptance report status 'fail' blocks delivery")
    return []


def validate_delivery_decision(
    video_output_dir: Path,
    criteria_path: Path,
    *,
    report_path: Path | None = None,
    manifest_path: Path | None = None,
) -> list[str]:
    """Validate the default report for one video output directory as a delivery gate."""

    video_output_dir = video_output_dir.resolve()
    report_path = report_path or video_output_dir / "review" / "acceptance" / "acceptance_report.json"
    manifest_path = manifest_path or video_output_dir / "review" / "acceptance" / "allowed_artifacts_manifest.json"
    if not report_path.exists():
        raise GateBlockedError("missing acceptance report blocks delivery")
    return validate_acceptance_report(
        report_path,
        criteria_path=criteria_path,
        video_output_dir=video_output_dir,
        manifest_path=manifest_path,
        enforce_decision=True,
    )


def _parse_artifact_arg(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("artifact must use role=path")
    role, path = value.split("=", 1)
    if not role.strip() or not path.strip():
        raise argparse.ArgumentTypeError("artifact must use role=path")
    return role.strip(), path.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Final Delivery Acceptance reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser("manifest", help="Create allowed_artifacts_manifest.json.")
    manifest_parser.add_argument("video_output_dir", type=Path)
    manifest_parser.add_argument("--criteria", type=Path, default=REPO_ROOT / "docs" / "acceptance" / "acceptance_criteria.v1.json")
    manifest_parser.add_argument(
        "--artifact",
        action="append",
        type=_parse_artifact_arg,
        default=[],
        help="Final artifact in role=relative/path form. Defaults to tex=main.tex and pdf=final.pdf.",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate an Acceptance Report.")
    validate_parser.add_argument("report", type=Path)
    validate_parser.add_argument("--criteria", type=Path, required=True)
    validate_parser.add_argument("--video-output-dir", type=Path, required=True)
    validate_parser.add_argument("--manifest", type=Path)
    validate_parser.add_argument("--enforce-decision", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "manifest":
            artifacts = args.artifact or [("tex", "main.tex"), ("pdf", "final.pdf")]
            manifest_path = create_allowed_artifacts_manifest(args.video_output_dir, args.criteria, artifacts)
            print(manifest_path)
            return EXIT_VALID

        manifest_path = args.manifest or args.video_output_dir / "review" / "acceptance" / "allowed_artifacts_manifest.json"
        warnings = validate_acceptance_report(
            args.report,
            criteria_path=args.criteria,
            video_output_dir=args.video_output_dir,
            manifest_path=manifest_path,
            enforce_decision=args.enforce_decision,
        )
    except GateBlockedError as exc:
        print(f"GATE_BLOCKED: {exc}", file=sys.stderr)
        return EXIT_GATE_BLOCKED
    except ValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return EXIT_INVALID

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"VALID: {args.report}")
    return EXIT_VALID


if __name__ == "__main__":
    raise SystemExit(main())

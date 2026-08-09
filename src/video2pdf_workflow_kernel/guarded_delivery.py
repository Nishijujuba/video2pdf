from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .delivery_quality import DeliveryQualityRegistry
from .errors import ContractError
from .utils import canonical_json_bytes, read_json


REQUIRED_GUARD_CONDITIONS = frozenset(
    {
        "target_resolved",
        "allowed_artifacts_manifest_loaded",
        "final_pdf_in_manifest",
        "final_compile_provenance_current",
        "acceptance_report_v2_authority_current",
        "rendered_page_evidence_current",
        "artifact_fingerprints_current",
    }
)


def _fingerprint_without(value: dict[str, Any], field: str) -> str:
    payload = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def validate_acceptance_report(
    *,
    project_root: Path,
    report_path: Path,
    run_id: str,
    coordination_revision: int | None = None,
) -> dict[str, Any]:
    report = read_json(report_path.resolve())
    DeliveryQualityRegistry(project_root.resolve()).validate(
        "acceptance-report-v2", report
    )
    run_binding = report.get("run_binding")
    if (
        report.get("overall_status") != "pass"
        or report.get("routing_state") != "ready_for_delivery"
        or report.get("input_track") != "kernel"
        or not isinstance(run_binding, dict)
        or run_binding.get("run_id") != run_id
        or (
            coordination_revision is not None
            and run_binding.get("coordination_revision") != coordination_revision
        )
        or report.get("report_sha256")
        != _fingerprint_without(report, "report_sha256")
    ):
        raise ContractError(
            "Acceptance Report v2 is not a fingerprint-current passing Kernel decision"
        )
    return report


def validate_delivery_guard_report(*, report_path: Path) -> dict[str, Any]:
    report = read_json(report_path.resolve())
    checked_conditions = report.get("checked_conditions")
    condition_statuses = (
        {
            item.get("condition"): item.get("status")
            for item in checked_conditions
            if isinstance(item, dict)
        }
        if isinstance(checked_conditions, list)
        else {}
    )
    fingerprints = report.get("artifact_fingerprints")
    fingerprints_valid = bool(fingerprints) and all(
        isinstance(item, dict)
        and set(item) == {"path", "sha256", "size_bytes", "size_chars"}
        and isinstance(item["path"], str)
        and isinstance(item["sha256"], str)
        and item["sha256"].startswith("sha256:")
        and len(item["sha256"]) == 71
        and isinstance(item["size_bytes"], int)
        and (item["size_chars"] is None or isinstance(item["size_chars"], int))
        for item in fingerprints
    )
    if (
        report.get("schema_version") != "1.0"
        or report.get("status") != "pass"
        or report.get("stage") != "accepted"
        or report.get("validated_by") != "delivery_guard.py"
        or report.get("acceptance_report_status") != "pass"
        or not fingerprints_valid
        or set(condition_statuses) != REQUIRED_GUARD_CONDITIONS
        or any(status != "pass" for status in condition_statuses.values())
    ):
        raise ContractError(
            "Delivery Guard Report is not a complete passing mechanical decision"
        )
    return report

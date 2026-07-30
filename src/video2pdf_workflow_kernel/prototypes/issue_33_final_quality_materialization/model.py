"""PROTOTYPE: pure state model for Issue 33.

Question: can a fingerprinted dependency graph preserve unaffected independent
judgments while final materialization fails closed on stale evidence, quality
failures, Contract Gaps, cross-phase findings, and repair-budget exhaustion?
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


MAX_REPAIR_ATTEMPTS = 3

CHECKS = {
    "source_faithfulness": {
        "phase": "precompile",
        "owner": "Source-Faithfulness Reviewer",
        "rules": ["source.correspondence"],
        "dependencies": ["figure_content", "reader_text", "policy"],
    },
    "writing_quality": {
        "phase": "precompile",
        "owner": "Writing Quality Reviewer",
        "rules": ["writing.proposition_integrity"],
        "dependencies": ["reader_text", "reader_metadata", "policy"],
    },
    "pyramid": {
        "phase": "precompile",
        "owner": "Pyramid Reviewer",
        "rules": ["pyramid.argument_structure"],
        "dependencies": ["reader_text", "reader_metadata", "policy"],
    },
    "rendered_text_reconciliation": {
        "phase": "postcompile_mechanical",
        "owner": "Rendered Text Reconciliation Provider",
        "rules": [],
        "dependencies": ["final_pdf", "precompile_text_seal", "policy"],
    },
    "visual_quality": {
        "phase": "postcompile",
        "owner": "Visual Quality Reviewer",
        "rules": ["visual.render_integrity"],
        "dependencies": [
            "figure_content",
            "layout",
            "reader_metadata",
            "final_pdf",
            "policy",
        ],
    },
}

MUTATIONS = {
    "reader_text": {
        "label": "reader-facing text repair",
        "changes": ["reader_text"],
    },
    "figure_content": {
        "label": "figure-content repair",
        "changes": ["figure_content"],
    },
    "layout": {
        "label": "layout-only repair",
        "changes": ["layout"],
    },
    "reader_metadata": {
        "label": "reader-facing metadata repair",
        "changes": ["reader_metadata"],
    },
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _snapshot(state: dict[str, Any], dependencies: list[str]) -> dict[str, Any]:
    return {name: state["generations"][name] for name in dependencies}


def _report(
    state: dict[str, Any],
    check_name: str,
    decision: str = "pass",
) -> dict[str, Any]:
    spec = CHECKS[check_name]
    report = {
        "check": check_name,
        "phase": spec["phase"],
        "owner": spec["owner"],
        "rules": list(spec["rules"]),
        "dependency_snapshot": _snapshot(state, spec["dependencies"]),
        "decision": decision,
        "contract_gaps": [],
        "status": "current",
    }
    report["report_sha256"] = fingerprint(report)
    return report


def _seal_precompile(state: dict[str, Any], successor: bool) -> None:
    precompile_reports = {
        name: state["reports"][name]["report_sha256"]
        for name, spec in CHECKS.items()
        if spec["phase"] == "precompile"
    }
    seal = {
        "schema_version": "precompile-text-seal.prototype.v1",
        "artifact_generations": {
            name: state["generations"][name]
            for name in (
                "reader_text",
                "reader_metadata",
                "figure_content",
                "layout",
                "policy",
            )
        },
        "precompile_reports": precompile_reports,
        "reader_text_set_sha256": fingerprint(
            {
                "reader_text": state["generations"]["reader_text"],
                "reader_metadata": state["generations"]["reader_metadata"],
            }
        ),
        "successor_of": (
            state["precompile_text_seal"]["seal_sha256"]
            if successor and state["precompile_text_seal"]
            else None
        ),
        "text_equivalence_proven": successor,
        "status": "current",
        "decision": "pass",
    }
    seal["seal_sha256"] = fingerprint(seal)
    state["precompile_text_seal"] = seal
    state["generations"]["precompile_text_seal"] += 1


def _compile(state: dict[str, Any]) -> None:
    state["generations"]["final_pdf"] += 1
    final_seal = {
        "schema_version": "final-artifact-seal.prototype.v1",
        "compile_inputs": {
            name: state["generations"][name]
            for name in (
                "reader_text",
                "reader_metadata",
                "figure_content",
                "layout",
                "policy",
            )
        },
        "precompile_text_seal_sha256": state["precompile_text_seal"]["seal_sha256"],
        "final_pdf_generation": state["generations"]["final_pdf"],
        "status": "current",
    }
    final_seal["seal_sha256"] = fingerprint(final_seal)
    state["final_artifact_seal"] = final_seal


def new_state() -> dict[str, Any]:
    state: dict[str, Any] = {
        "prototype_schema": "issue-33-final-quality-state.prototype.v1",
        "catalog": {
            "catalog_id": "delivery-quality",
            "catalog_version": "prototype-1",
            "rules": [
                "source.correspondence",
                "writing.proposition_integrity",
                "pyramid.argument_structure",
                "visual.render_integrity",
            ],
        },
        "generations": {
            "reader_text": 1,
            "reader_metadata": 1,
            "figure_content": 1,
            "layout": 1,
            "policy": 1,
            "precompile_text_seal": 0,
            "final_pdf": 0,
        },
        "reports": {},
        "precompile_text_seal": None,
        "final_artifact_seal": None,
        "cross_phase_findings": [],
        "contract_gaps": [],
        "delivery_quality_report": None,
        "delivery_guard_report": None,
        "stage": "generating",
        "repair_budget": {
            "attempt_limit": MAX_REPAIR_ATTEMPTS,
            "attempts": [],
            "active_attempt": None,
        },
        "routing_state": "quality_evaluation",
        "last_rerun": [],
        "last_event": "Initialized an empty generation.",
    }
    for check_name in ("source_faithfulness", "writing_quality", "pyramid"):
        state["reports"][check_name] = _report(state, check_name)
    _seal_precompile(state, successor=False)
    _compile(state)
    for check_name in ("rendered_text_reconciliation", "visual_quality"):
        state["reports"][check_name] = _report(state, check_name)
    materialize(state)
    run_delivery_guard(state)
    state["last_event"] = "Initialized a complete passing delivery generation."
    return state


def _mark_derived_stale(state: dict[str, Any]) -> None:
    if state["precompile_text_seal"]:
        state["precompile_text_seal"]["status"] = "stale"
    if state["final_artifact_seal"]:
        state["final_artifact_seal"]["status"] = "stale"
    for name in ("rendered_text_reconciliation", "visual_quality"):
        if name in state["reports"]:
            state["reports"][name]["status"] = "stale"
    if state["delivery_quality_report"]:
        state["delivery_quality_report"]["status"] = "stale"
    if state["delivery_guard_report"]:
        state["delivery_guard_report"]["status"] = "stale"
    state["stage"] = "generating"
    state["routing_state"] = "repair_required"


def _start_attempt(state: dict[str, Any], mutation: str) -> dict[str, Any] | None:
    budget = state["repair_budget"]
    if budget["active_attempt"] is not None:
        state["last_event"] = "Finish the active repair attempt before starting another."
        return None
    if len(budget["attempts"]) >= budget["attempt_limit"]:
        state["routing_state"] = "manual_repair_required"
        state["last_event"] = "Repair budget exhausted; manual repair is required."
        return None
    attempt = {
        "attempt": len(budget["attempts"]) + 1,
        "mutation": mutation,
        "status": "active",
        "changed_generations": {},
        "rerun_checks": [],
        "result": None,
    }
    budget["attempts"].append(attempt)
    budget["active_attempt"] = attempt["attempt"]
    return attempt


def apply_repair(state: dict[str, Any], mutation: str) -> None:
    attempt = _start_attempt(state, mutation)
    if attempt is None:
        return
    changed = MUTATIONS[mutation]["changes"]
    for dependency in changed:
        state["generations"][dependency] += 1
        attempt["changed_generations"][dependency] = state["generations"][dependency]
    for check_name, spec in CHECKS.items():
        if set(spec["dependencies"]) & set(changed):
            state["reports"][check_name]["status"] = "stale"
    _mark_derived_stale(state)
    state["cross_phase_findings"] = []
    state["contract_gaps"] = []
    state["last_rerun"] = []
    state["last_event"] = (
        f"Started repair attempt {attempt['attempt']}: "
        f"{MUTATIONS[mutation]['label']}."
    )


def run_affected_checks(state: dict[str, Any]) -> None:
    rerun: list[str] = []
    old_seal = copy.deepcopy(state["precompile_text_seal"])
    for check_name in ("source_faithfulness", "writing_quality", "pyramid"):
        if state["reports"][check_name]["status"] == "stale":
            state["reports"][check_name] = _report(state, check_name)
            rerun.append(check_name)
    if not old_seal or old_seal["status"] == "stale":
        current_text_set_sha256 = fingerprint(
            {
                "reader_text": state["generations"]["reader_text"],
                "reader_metadata": state["generations"]["reader_metadata"],
            }
        )
        text_unchanged = (
            old_seal is not None
            and old_seal["reader_text_set_sha256"] == current_text_set_sha256
        )
        _seal_precompile(state, successor=text_unchanged)
        rerun.append(
            "precompile_text_seal.successor"
            if text_unchanged
            else "precompile_text_seal.fresh_review"
        )
    _compile(state)
    rerun.append("final_compile")
    for check_name in ("rendered_text_reconciliation", "visual_quality"):
        state["reports"][check_name] = _report(state, check_name)
        rerun.append(check_name)
    state["delivery_quality_report"] = None
    state["delivery_guard_report"] = None
    state["last_rerun"] = rerun
    active = state["repair_budget"]["active_attempt"]
    if active is not None:
        state["repair_budget"]["attempts"][active - 1]["rerun_checks"] = list(rerun)
    state["routing_state"] = "quality_evaluation"
    state["last_event"] = f"Executed {len(rerun)} affected derived step(s)."


def inject_visual_failure(state: dict[str, Any]) -> None:
    report = _report(state, "visual_quality", decision="fail")
    report["findings"] = [
        {
            "rule_id": "visual.render_integrity",
            "violation_id": "clipped_caption",
            "page": 3,
        }
    ]
    report["report_sha256"] = fingerprint(report)
    state["reports"]["visual_quality"] = report
    state["delivery_quality_report"] = None
    state["delivery_guard_report"] = None
    state["routing_state"] = "quality_evaluation"
    state["last_event"] = "Injected a current Visual Quality failure."


def inject_cross_phase_finding(state: dict[str, Any]) -> None:
    finding = {
        "finding_id": f"cross-phase-{len(state['cross_phase_findings']) + 1}",
        "source_owner": "Visual Quality Reviewer",
        "target_rule_id": "writing.proposition_integrity",
        "violation_id": "rendered_context_exposes_ambiguous_reference",
        "effect": "add_failure_only",
        "status": "current",
        "final_pdf_generation": state["generations"]["final_pdf"],
    }
    state["cross_phase_findings"].append(finding)
    state["delivery_quality_report"] = None
    state["delivery_guard_report"] = None
    state["routing_state"] = "quality_evaluation"
    state["last_event"] = "Added a current cross-phase failure against Writing Quality."


def inject_contract_gap(state: dict[str, Any]) -> None:
    state["contract_gaps"].append(
        {
            "code": "UNKNOWN_POSTCOMPILE_EVIDENCE_IDENTITY",
            "identity": "visual.experimental_unregistered",
            "requires": "human_policy_disposition",
        }
    )
    state["delivery_quality_report"] = None
    state["delivery_guard_report"] = None
    state["routing_state"] = "human_disposition_required"
    state["last_event"] = "Injected a Contract Gap; automated repair is blocked."


def _is_current(state: dict[str, Any], check_name: str) -> bool:
    report = state["reports"].get(check_name)
    if not report or report["status"] != "current":
        return False
    expected = _snapshot(state, CHECKS[check_name]["dependencies"])
    return report["dependency_snapshot"] == expected


def _finish_attempt(state: dict[str, Any], result: str) -> None:
    active = state["repair_budget"]["active_attempt"]
    if active is None:
        return
    attempt = state["repair_budget"]["attempts"][active - 1]
    attempt["status"] = "completed"
    attempt["result"] = result
    state["repair_budget"]["active_attempt"] = None


def materialize(state: dict[str, Any]) -> None:
    catalog_rules = set(state["catalog"]["rules"])
    semantic_reports = [
        state["reports"][name]
        for name in (
            "source_faithfulness",
            "writing_quality",
            "pyramid",
            "visual_quality",
        )
    ]
    observed_rules = [
        rule_id for report in semantic_reports for rule_id in report["rules"]
    ]
    partition_checks = {
        "complete_catalog_coverage": set(observed_rules) == catalog_rules,
        "disjoint_rule_ownership": len(observed_rules) == len(set(observed_rules)),
        "all_reports_current": all(_is_current(state, name) for name in CHECKS),
        "precompile_seal_current": bool(
            state["precompile_text_seal"]
            and state["precompile_text_seal"]["status"] == "current"
        ),
        "final_artifact_seal_current": bool(
            state["final_artifact_seal"]
            and state["final_artifact_seal"]["status"] == "current"
            and state["final_artifact_seal"]["final_pdf_generation"]
            == state["generations"]["final_pdf"]
        ),
        "rendered_text_reconciliation_passes": (
            state["reports"]["rendered_text_reconciliation"]["decision"] == "pass"
        ),
    }
    normalized_results = []
    for report in semantic_reports:
        for rule_id in report["rules"]:
            normalized_results.append(
                {
                    "rule_id": rule_id,
                    "decision_phase": report["phase"],
                    "decision_owner": report["owner"],
                    "source_report_sha256": report["report_sha256"],
                    "decision": report["decision"],
                }
            )
    for finding in state["cross_phase_findings"]:
        for result in normalized_results:
            if result["rule_id"] == finding["target_rule_id"]:
                result["decision"] = "fail"
                result["cross_phase_finding"] = finding["finding_id"]
    gaps = list(state["contract_gaps"])
    if not all(partition_checks.values()):
        gaps.extend(
            {
                "code": "MATERIALIZATION_PRECONDITION_FAILED",
                "check": name,
            }
            for name, passed in partition_checks.items()
            if not passed
        )
    if gaps:
        overall = "blocked_contract_gap"
        routing = "human_disposition_required"
    elif any(result["decision"] != "pass" for result in normalized_results):
        overall = "fail"
        attempts_used = len(state["repair_budget"]["attempts"])
        routing = (
            "manual_repair_required"
            if attempts_used >= state["repair_budget"]["attempt_limit"]
            else "repair_required"
        )
    else:
        overall = "pass"
        routing = "ready_for_delivery"
    report = {
        "schema_version": "delivery-quality-report.prototype.v1",
        "provider_id": "delivery-quality-materializer.prototype.v1",
        "catalog_sha256": fingerprint(state["catalog"]),
        "precompile_text_seal_sha256": (
            state["precompile_text_seal"]["seal_sha256"]
            if state["precompile_text_seal"]
            else None
        ),
        "final_artifact_seal_sha256": (
            state["final_artifact_seal"]["seal_sha256"]
            if state["final_artifact_seal"]
            else None
        ),
        "final_pdf_generation": state["generations"]["final_pdf"],
        "partition_checks": partition_checks,
        "normalized_rule_results": normalized_results,
        "cross_phase_findings": copy.deepcopy(state["cross_phase_findings"]),
        "contract_gaps": gaps,
        "overall_decision": overall,
        "routing_state": routing,
        "repair_budget": {
            "attempt_limit": state["repair_budget"]["attempt_limit"],
            "attempts_used": len(state["repair_budget"]["attempts"]),
        },
        "semantic_reinterpretation_performed": False,
        "status": "current",
    }
    report["report_sha256"] = fingerprint(report)
    state["delivery_quality_report"] = report
    state["delivery_guard_report"] = None
    state["routing_state"] = routing
    state["stage"] = "accepted" if overall == "pass" else "blocked"
    _finish_attempt(state, overall)
    state["last_event"] = f"Materialized authoritative quality decision: {overall}."


def run_delivery_guard(state: dict[str, Any]) -> None:
    report = state["delivery_quality_report"]
    checks = {
        "quality_report_exists": report is not None,
        "quality_report_current": bool(report and report["status"] == "current"),
        "quality_decision_passes": bool(
            report and report["overall_decision"] == "pass"
        ),
        "quality_report_binds_current_pdf": bool(
            report
            and report["final_pdf_generation"] == state["generations"]["final_pdf"]
        ),
        "quality_report_binds_current_precompile_seal": bool(
            report
            and state["precompile_text_seal"]
            and report["precompile_text_seal_sha256"]
            == state["precompile_text_seal"]["seal_sha256"]
        ),
        "quality_report_binds_current_final_seal": bool(
            report
            and state["final_artifact_seal"]
            and report["final_artifact_seal_sha256"]
            == state["final_artifact_seal"]["seal_sha256"]
        ),
        "lifecycle_stage_accepted": state["stage"] == "accepted",
        "routing_ready_for_delivery": state["routing_state"]
        == "ready_for_delivery",
    }
    guard = {
        "schema_version": "delivery-guard-report.prototype.v1",
        "quality_report_sha256": report["report_sha256"] if report else None,
        "mechanical_checks": checks,
        "semantic_judgment_performed": False,
        "decision": "pass" if all(checks.values()) else "block",
        "status": "current",
    }
    guard["report_sha256"] = fingerprint(guard)
    state["delivery_guard_report"] = guard
    state["last_event"] = (
        "Delivery Guard passed mechanical freshness checks."
        if guard["decision"] == "pass"
        else "Delivery Guard blocked delivery mechanically."
    )


def status_view(state: dict[str, Any]) -> dict[str, Any]:
    reports = {
        name: {
            "status": report["status"],
            "decision": report["decision"],
            "owner": report["owner"],
        }
        for name, report in state["reports"].items()
    }
    quality = state["delivery_quality_report"]
    guard = state["delivery_guard_report"]
    return {
        "last_event": state["last_event"],
        "stage": state["stage"],
        "routing_state": state["routing_state"],
        "generations": state["generations"],
        "reports": reports,
        "precompile_text_seal": (
            {
                "status": state["precompile_text_seal"]["status"],
                "successor": bool(state["precompile_text_seal"]["successor_of"]),
            }
            if state["precompile_text_seal"]
            else None
        ),
        "final_artifact_seal": (
            {"status": state["final_artifact_seal"]["status"]}
            if state["final_artifact_seal"]
            else None
        ),
        "cross_phase_findings": len(state["cross_phase_findings"]),
        "contract_gap_codes": [gap["code"] for gap in state["contract_gaps"]],
        "quality_decision": quality["overall_decision"] if quality else None,
        "guard_decision": guard["decision"] if guard else None,
        "repair_budget": {
            "used": len(state["repair_budget"]["attempts"]),
            "limit": state["repair_budget"]["attempt_limit"],
            "active": state["repair_budget"]["active_attempt"],
        },
        "last_rerun": state["last_rerun"],
    }


def artifact_view(state: dict[str, Any], name: str) -> Any:
    if name == "state":
        return status_view(state)
    mapping = {
        "reports": "reports",
        "quality": "delivery_quality_report",
        "guard": "delivery_guard_report",
        "attempts": "repair_budget",
    }
    return state[mapping[name]]

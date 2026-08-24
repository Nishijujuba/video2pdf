"""PROTOTYPE: pure state model for Issue 35.

Question: can dual source/text identities, complete rule-item coverage, and an
immutable resealing transition make precompile semantic judgment reusable
without letting a stale Artifact Generation enter Final Compile?
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


TEXT_KINDS = ("title", "paragraph", "caption", "table_cell", "callout", "footnote")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _source_sha(name: str, generation: int, presentation_revision: int) -> str:
    return fingerprint(
        {
            "name": name,
            "generation": generation,
            "presentation_revision": presentation_revision,
        }
    )


def new_state() -> dict[str, Any]:
    catalog = {
        "schema_version": "delivery-quality-rule-catalog.prototype.v1",
        "catalog_id": "delivery-quality",
        "catalog_version": "prototype-1",
        "language_profiles": ["zh-CN-teaching-pdf"],
        "rules": [
            {
                "rule_id": "writing.proposition_integrity",
                "requirement": "Every proposition is complete, clear, and logically connected.",
                "violations": ["missing_relation", "ambiguous_reference"],
            },
            {
                "rule_id": "writing.no_meta_process",
                "requirement": "Reader-facing prose does not expose internal generation process.",
                "violations": ["generation_process_exposed"],
            },
        ],
    }
    rule_fingerprints = {
        rule["rule_id"]: fingerprint(rule) for rule in catalog["rules"]
    }
    projection = {
        "schema_version": "delivery-quality-role-projection.prototype.v1",
        "projection_id": "writing-quality.zh-CN-teaching-pdf",
        "projection_kind": "evaluation",
        "owner": "Writing Quality Reviewer",
        "language_profile": "zh-CN-teaching-pdf",
        "catalog_sha256": fingerprint(catalog),
        "projected_rule_fingerprints": rule_fingerprints,
    }
    sources = {
        "main_tex": {
            "generation": 7,
            "presentation_revision": 1,
            "sha256": _source_sha("main_tex", 7, 1),
        },
        "figure_01": {
            "generation": 2,
            "presentation_revision": 1,
            "sha256": _source_sha("figure_01", 2, 1),
        },
    }
    regions = [
        {
            "region_id": "main.title",
            "kind": "title",
            "source_id": "main_tex",
            "locator": "latex:document/title",
            "text": "从视频证据到可交付课程讲义",
            "representation": "structured_text",
        },
        {
            "region_id": "main.paragraph.001",
            "kind": "paragraph",
            "source_id": "main_tex",
            "locator": "latex:section-1/p-1",
            "text": "可靠交付要求每条读者可见文字都能追溯到当前源工件。",
            "representation": "structured_text",
        },
        {
            "region_id": "figure.01.caption",
            "kind": "caption",
            "source_id": "main_tex",
            "locator": "latex:figure-01/caption",
            "text": "图 1：从规则目录到最终交付决定的证据链。",
            "representation": "structured_text",
        },
        {
            "region_id": "main.table.01.cell.r2c2",
            "kind": "table_cell",
            "source_id": "main_tex",
            "locator": "latex:table-01/r2c2",
            "text": "完整覆盖",
            "representation": "structured_text",
        },
        {
            "region_id": "figure.01.callout",
            "kind": "callout",
            "source_id": "figure_01",
            "locator": "figure:01/callout-A",
            "text": "先封印文字，再进入最终编译",
            "representation": "authoritative_raster_text",
        },
        {
            "region_id": "main.footnote.001",
            "kind": "footnote",
            "source_id": "main_tex",
            "locator": "latex:section-1/footnote-1",
            "text": "这里的封印表示不可变证据绑定。",
            "representation": "structured_text",
        },
    ]
    state = {
        "prototype_schema": "issue-35-text-seal-state.prototype.v1",
        "catalog": catalog,
        "projection": projection,
        "sources": sources,
        "declared_regions": regions,
        "injected_violations": {},
        "inventory": None,
        "writing_quality_report": None,
        "current_seal": None,
        "stale_seal": None,
        "stale_inventory": None,
        "text_equivalence_report": None,
        "final_compile_admission": None,
        "last_event": "Initialized an unevaluated integrated draft.",
    }
    refresh_inventory(state)
    return state


def _inventory_material(state: dict[str, Any]) -> dict[str, Any]:
    items = []
    coverage = []
    gaps = []
    extraction_records: dict[str, dict[str, Any]] = {}
    for region in state["declared_regions"]:
        source = state["sources"][region["source_id"]]
        extraction_records[region["source_id"]] = {
            "source_id": region["source_id"],
            "artifact_generation": source["generation"],
            "artifact_sha256": source["sha256"],
            "extractor_id": (
                "latex-reader-text-extractor.prototype"
                if region["source_id"] == "main_tex"
                else "declared-raster-text-provider.prototype"
            ),
        }
        text = region.get("text")
        representation = region.get("representation")
        if region["kind"] not in TEXT_KINDS:
            gaps.append(
                {
                    "code": "UNKNOWN_READER_FACING_TEXT_KIND",
                    "region_id": region["region_id"],
                    "kind": region["kind"],
                }
            )
            coverage.append(
                {
                    "region_id": region["region_id"],
                    "item_id": None,
                    "status": "contract_gap",
                }
            )
            continue
        if not text or representation == "missing":
            gaps.append(
                {
                    "code": "UNPROVABLE_READER_FACING_TEXT",
                    "region_id": region["region_id"],
                    "source_id": region["source_id"],
                }
            )
            coverage.append(
                {
                    "region_id": region["region_id"],
                    "item_id": None,
                    "status": "contract_gap",
                }
            )
            continue
        item = {
            "item_id": region["region_id"],
            "kind": region["kind"],
            "source_binding": {
                "source_id": region["source_id"],
                "artifact_generation": source["generation"],
                "artifact_sha256": source["sha256"],
                "locator": region["locator"],
            },
            "representation": representation,
            "exact_utf8_text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        item["item_sha256"] = fingerprint(item)
        items.append(item)
        coverage.append(
            {
                "region_id": region["region_id"],
                "item_id": item["item_id"],
                "status": "covered",
            }
        )
    items.sort(key=lambda item: item["item_id"])
    coverage.sort(key=lambda entry: entry["region_id"])
    extraction = sorted(extraction_records.values(), key=lambda item: item["source_id"])
    text_set = [
        {
            "item_id": item["item_id"],
            "kind": item["kind"],
            "representation": item["representation"],
            "text_sha256": item["text_sha256"],
        }
        for item in items
    ]
    surface = [
        {
            "region_id": region["region_id"],
            "kind": region["kind"],
            "representation": region["representation"],
        }
        for region in sorted(state["declared_regions"], key=lambda item: item["region_id"])
    ]
    inventory = {
        "schema_version": "reader-facing-text-inventory.prototype.v1",
        "language_profile": state["projection"]["language_profile"],
        "source_extraction": extraction,
        "items": items,
        "coverage_ledger": coverage,
        "contract_gaps": gaps,
        "reader_text_set_sha256": fingerprint(text_set),
        "declared_surface_sha256": fingerprint(surface),
        "coverage_ledger_sha256": fingerprint(coverage),
    }
    inventory["inventory_sha256"] = fingerprint(inventory)
    return inventory


def refresh_inventory(state: dict[str, Any]) -> None:
    state["inventory"] = _inventory_material(state)


def _current_source_bindings(state: dict[str, Any]) -> dict[str, Any]:
    return {
        source_id: {
            "generation": source["generation"],
            "sha256": source["sha256"],
        }
        for source_id, source in sorted(state["sources"].items())
    }


def _report_is_current(state: dict[str, Any]) -> bool:
    report = state["writing_quality_report"]
    return bool(
        report
        and report["inventory_sha256"] == state["inventory"]["inventory_sha256"]
        and report["artifact_generations"] == _current_source_bindings(state)
        and report["catalog_sha256"] == fingerprint(state["catalog"])
        and report["projection_sha256"] == fingerprint(state["projection"])
    )


def _seal_is_current(state: dict[str, Any]) -> bool:
    seal = state["current_seal"]
    return bool(
        seal
        and seal["inventory_sha256"] == state["inventory"]["inventory_sha256"]
        and seal["artifact_generations"] == _current_source_bindings(state)
        and seal["catalog_sha256"] == fingerprint(state["catalog"])
        and seal["projection_sha256"] == fingerprint(state["projection"])
    )


def run_writing_quality_gate(state: dict[str, Any]) -> None:
    refresh_inventory(state)
    inventory = state["inventory"]
    if inventory["contract_gaps"]:
        state["writing_quality_report"] = {
            "schema_version": "writing-quality-report.prototype.v1",
            "overall_decision": "blocked",
            "blocking_reason": "contract_gap",
            "contract_gaps": copy.deepcopy(inventory["contract_gaps"]),
            "inventory_sha256": inventory["inventory_sha256"],
            "artifact_generations": _current_source_bindings(state),
            "catalog_sha256": fingerprint(state["catalog"]),
            "projection_sha256": fingerprint(state["projection"]),
        }
        state["writing_quality_report"]["report_sha256"] = fingerprint(
            state["writing_quality_report"]
        )
        state["current_seal"] = None
        state["last_event"] = (
            "Gate blocked: at least one reader-facing region lacks authoritative text."
        )
        return

    results = []
    for rule_id, rule_sha in sorted(
        state["projection"]["projected_rule_fingerprints"].items()
    ):
        item_evidence = []
        for item in inventory["items"]:
            violation = state["injected_violations"].get(
                f"{rule_id}:{item['item_id']}"
            )
            evidence = {
                "item_id": item["item_id"],
                "item_sha256": item["item_sha256"],
                "locator": item["source_binding"]["locator"],
                "decision": "fail" if violation else "pass",
            }
            if violation:
                evidence["violation_id"] = violation
            item_evidence.append(evidence)
        results.append(
            {
                "rule_id": rule_id,
                "rule_semantic_sha256": rule_sha,
                "decision": (
                    "fail"
                    if any(item["decision"] == "fail" for item in item_evidence)
                    else "pass"
                ),
                "coverage_mode": "every_inventory_item",
                "item_evidence": item_evidence,
            }
        )
    expected_pairs = len(state["projection"]["projected_rule_fingerprints"]) * len(
        inventory["items"]
    )
    observed_pairs = sum(len(result["item_evidence"]) for result in results)
    report = {
        "schema_version": "writing-quality-report.prototype.v1",
        "owner": "Writing Quality Reviewer",
        "decision_origin": "fresh_independent_review",
        "language_profile": state["projection"]["language_profile"],
        "catalog_sha256": fingerprint(state["catalog"]),
        "projection_sha256": fingerprint(state["projection"]),
        "inventory_sha256": inventory["inventory_sha256"],
        "reader_text_set_sha256": inventory["reader_text_set_sha256"],
        "coverage_ledger_sha256": inventory["coverage_ledger_sha256"],
        "artifact_generations": _current_source_bindings(state),
        "coverage_proof": {
            "expected_rule_item_pairs": expected_pairs,
            "observed_rule_item_pairs": observed_pairs,
            "complete": observed_pairs == expected_pairs,
        },
        "results": results,
        "contract_gaps": [],
        "overall_decision": (
            "pass"
            if observed_pairs == expected_pairs
            and all(result["decision"] == "pass" for result in results)
            else "fail"
        ),
    }
    report["report_sha256"] = fingerprint(report)
    state["writing_quality_report"] = report
    if report["overall_decision"] != "pass":
        state["current_seal"] = None
        state["last_event"] = (
            "Gate failed or rule-item coverage is incomplete; no seal was created."
        )
        return
    _create_seal(state, decision_origin="fresh_evaluation")
    state["stale_seal"] = None
    state["stale_inventory"] = None
    state["text_equivalence_report"] = None
    state["final_compile_admission"] = None
    state["last_event"] = (
        "Fresh review passed with complete rule-item coverage; current text sealed."
    )


def _create_seal(
    state: dict[str, Any],
    *,
    decision_origin: str,
    predecessor: dict[str, Any] | None = None,
    equivalence_report: dict[str, Any] | None = None,
) -> None:
    report = state["writing_quality_report"]
    seal = {
        "schema_version": "precompile-text-seal.prototype.v1",
        "decision_origin": decision_origin,
        "language_profile": state["projection"]["language_profile"],
        "catalog_sha256": fingerprint(state["catalog"]),
        "projection_sha256": fingerprint(state["projection"]),
        "inventory_sha256": state["inventory"]["inventory_sha256"],
        "reader_text_set_sha256": state["inventory"]["reader_text_set_sha256"],
        "declared_surface_sha256": state["inventory"]["declared_surface_sha256"],
        "coverage_ledger_sha256": state["inventory"]["coverage_ledger_sha256"],
        "writing_quality_report_sha256": report["report_sha256"],
        "artifact_generations": _current_source_bindings(state),
        "predecessor_seal_sha256": (
            predecessor["seal_sha256"] if predecessor else None
        ),
        "text_equivalence_report_sha256": (
            equivalence_report["report_sha256"] if equivalence_report else None
        ),
    }
    seal["seal_sha256"] = fingerprint(seal)
    state["current_seal"] = seal


def edit_reader_text(state: dict[str, Any]) -> None:
    paragraph = next(
        region
        for region in state["declared_regions"]
        if region["region_id"] == "main.paragraph.001"
    )
    paragraph["text"] += "（内容修订）"
    source = state["sources"]["main_tex"]
    source["generation"] += 1
    source["sha256"] = _source_sha(
        "main_tex", source["generation"], source["presentation_revision"]
    )
    _invalidate_after_mutation(state, keep_report_for_equivalence=False)
    state["last_event"] = (
        "Reader-facing text changed; prior judgment and seal are invalid."
    )


def apply_presentation_only_change(state: dict[str, Any]) -> None:
    source = state["sources"]["main_tex"]
    source["generation"] += 1
    source["presentation_revision"] += 1
    source["sha256"] = _source_sha(
        "main_tex", source["generation"], source["presentation_revision"]
    )
    _invalidate_after_mutation(state, keep_report_for_equivalence=True)
    state["last_event"] = (
        "Presentation changed; compile is blocked until text equivalence reseals "
        "the new Artifact Generation."
    )


def add_unprovable_raster_text(state: dict[str, Any]) -> None:
    if any(
        region["region_id"] == "figure.01.hidden-label"
        for region in state["declared_regions"]
    ):
        state["last_event"] = "The unprovable raster region is already present."
        return
    state["declared_regions"].append(
        {
            "region_id": "figure.01.hidden-label",
            "kind": "callout",
            "source_id": "figure_01",
            "locator": "figure:01/unrepresented-label",
            "text": None,
            "representation": "missing",
        }
    )
    source = state["sources"]["figure_01"]
    source["generation"] += 1
    source["sha256"] = _source_sha(
        "figure_01", source["generation"], source["presentation_revision"]
    )
    _invalidate_after_mutation(state, keep_report_for_equivalence=False)
    state["last_event"] = (
        "A visible raster label lacks authoritative text; the inventory has a "
        "blocking Contract Gap."
    )


def inject_semantic_failure(state: dict[str, Any]) -> None:
    paragraph = next(
        region
        for region in state["declared_regions"]
        if region["region_id"] == "main.paragraph.001"
    )
    paragraph["text"] = "这证明了它，因为它证明了它。"
    state["injected_violations"][
        "writing.proposition_integrity:main.paragraph.001"
    ] = "ambiguous_reference"
    source = state["sources"]["main_tex"]
    source["generation"] += 1
    source["sha256"] = _source_sha(
        "main_tex", source["generation"], source["presentation_revision"]
    )
    _invalidate_after_mutation(state, keep_report_for_equivalence=False)
    state["last_event"] = (
        "An ambiguous-reference violation was injected; run the gate to inspect "
        "a complete-coverage failure."
    )


def _invalidate_after_mutation(
    state: dict[str, Any], *, keep_report_for_equivalence: bool
) -> None:
    prior_seal = state["current_seal"]
    if prior_seal:
        state["stale_seal"] = prior_seal
        state["stale_inventory"] = copy.deepcopy(state["inventory"])
    state["current_seal"] = None
    if not keep_report_for_equivalence:
        state["writing_quality_report"] = None
        state["stale_seal"] = None
        state["stale_inventory"] = None
    state["text_equivalence_report"] = None
    state["final_compile_admission"] = None
    refresh_inventory(state)


def prove_text_equivalence_and_reseal(state: dict[str, Any]) -> None:
    stale = state["stale_seal"]
    stale_inventory = state["stale_inventory"]
    report = state["writing_quality_report"]
    refresh_inventory(state)
    if not stale or not stale_inventory or not report:
        state["last_event"] = (
            "Equivalence unavailable: no stale seal and reusable prior judgment."
        )
        return
    inventory = state["inventory"]
    prior_items = {
        item["item_id"]: item for item in stale_inventory["items"]
    }
    current_items = {
        item["item_id"]: item for item in inventory["items"]
    }
    item_mapping = [
        {
            "item_id": item_id,
            "prior_locator": prior_items[item_id]["source_binding"]["locator"],
            "current_locator": current_items[item_id]["source_binding"]["locator"],
            "prior_text_sha256": prior_items[item_id]["text_sha256"],
            "current_text_sha256": current_items[item_id]["text_sha256"],
            "equivalent": (
                prior_items[item_id]["kind"] == current_items[item_id]["kind"]
                and prior_items[item_id]["representation"]
                == current_items[item_id]["representation"]
                and prior_items[item_id]["text_sha256"]
                == current_items[item_id]["text_sha256"]
            ),
        }
        for item_id in sorted(set(prior_items) & set(current_items))
    ]
    checks = {
        "stable_item_identity_bijection": (
            set(prior_items) == set(current_items)
            and len(item_mapping) == len(prior_items)
            and all(item["equivalent"] for item in item_mapping)
        ),
        "reader_text_set_unchanged": (
            stale["reader_text_set_sha256"] == inventory["reader_text_set_sha256"]
        ),
        "declared_surface_unchanged": (
            stale["declared_surface_sha256"] == inventory["declared_surface_sha256"]
        ),
        "catalog_unchanged": stale["catalog_sha256"] == fingerprint(state["catalog"]),
        "projection_unchanged": (
            stale["projection_sha256"] == fingerprint(state["projection"])
        ),
        "language_profile_unchanged": (
            stale["language_profile"] == state["projection"]["language_profile"]
        ),
        "no_contract_gap": not inventory["contract_gaps"],
    }
    equivalence = {
        "schema_version": "text-equivalence-report.prototype.v1",
        "prior_seal_sha256": stale["seal_sha256"],
        "prior_inventory_sha256": stale["inventory_sha256"],
        "current_inventory_sha256": inventory["inventory_sha256"],
        "current_artifact_generations": _current_source_bindings(state),
        "item_mapping": item_mapping,
        "checks": checks,
        "overall_decision": "equivalent" if all(checks.values()) else "different",
    }
    equivalence["report_sha256"] = fingerprint(equivalence)
    state["text_equivalence_report"] = equivalence
    if equivalence["overall_decision"] != "equivalent":
        state["writing_quality_report"] = None
        state["last_event"] = (
            "Text equivalence failed; fresh semantic review is required."
        )
        return
    _create_seal(
        state,
        decision_origin="reused_after_text_equivalence",
        predecessor=stale,
        equivalence_report=equivalence,
    )
    state["last_event"] = (
        "Text equivalence passed; a successor seal binds the new Artifact Generation."
    )


def attempt_final_compile_admission(state: dict[str, Any]) -> None:
    refresh_inventory(state)
    if not _seal_is_current(state):
        state["final_compile_admission"] = {
            "decision": "blocked",
            "reason": "current_precompile_text_seal_required",
        }
        state["last_event"] = "Final Compile blocked: no current Precompile Text Seal."
        return
    closure = {
        "artifact_generations": _current_source_bindings(state),
        "precompile_text_seal_sha256": state["current_seal"]["seal_sha256"],
    }
    state["final_compile_admission"] = {
        "schema_version": "final-compile-admission.prototype.v1",
        "decision": "admitted",
        "compile_input_closure_sha256": fingerprint(closure),
        **closure,
        "boundary_note": "Rendered-PDF lineage is owned by downstream Issue 33.",
    }
    state["last_event"] = (
        "Final Compile admitted against the exact current seal and input closure."
    )


def status_view(state: dict[str, Any]) -> dict[str, Any]:
    refresh_inventory(state)
    return {
        "last_event": state["last_event"],
        "artifact_generations": _current_source_bindings(state),
        "declared_text_kinds": sorted(
            {region["kind"] for region in state["declared_regions"]}
        ),
        "declared_region_count": len(state["declared_regions"]),
        "inventoried_item_count": len(state["inventory"]["items"]),
        "contract_gaps": state["inventory"]["contract_gaps"],
        "writing_report": (
            {
                "decision": state["writing_quality_report"].get("overall_decision"),
                "current": _report_is_current(state),
                "sha256": state["writing_quality_report"].get("report_sha256"),
            }
            if state["writing_quality_report"]
            else None
        ),
        "text_seal": (
            {
                "decision_origin": state["current_seal"]["decision_origin"],
                "current": _seal_is_current(state),
                "sha256": state["current_seal"]["seal_sha256"],
                "predecessor": state["current_seal"]["predecessor_seal_sha256"],
            }
            if state["current_seal"]
            else None
        ),
        "stale_seal_sha256": (
            state["stale_seal"]["seal_sha256"] if state["stale_seal"] else None
        ),
        "text_equivalence": (
            state["text_equivalence_report"]["overall_decision"]
            if state["text_equivalence_report"]
            else None
        ),
        "final_compile_eligible": _seal_is_current(state),
        "final_compile_admission": state["final_compile_admission"],
    }


def artifact_view(state: dict[str, Any], name: str) -> Any:
    if name == "state":
        return status_view(state)
    mapping = {
        "inventory": "inventory",
        "report": "writing_quality_report",
        "seal": "current_seal",
        "equivalence": "text_equivalence_report",
        "compile": "final_compile_admission",
    }
    return state[mapping[name]]

"""PROTOTYPE: rendered-text reconciliation state model for Issue 40."""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from typing import Any


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sealed_item(item_id: str, kind: str, text: str) -> dict[str, Any]:
    item = {
        "item_id": item_id,
        "kind": kind,
        "exact_utf8_text": text,
        "text_sha256": _text_sha(text),
    }
    item["item_sha256"] = fingerprint(item)
    return item


def _rendered_object(
    object_id: str,
    page: int,
    kind: str,
    text: str,
    *,
    bbox: list[int] | None = None,
) -> dict[str, Any]:
    obj = {
        "object_id": object_id,
        "page": page,
        "object_kind": kind,
        "bbox": bbox or [50, 50, 500, 80],
        "exact_utf8_text": text,
        "text_sha256": _text_sha(text),
    }
    obj["object_sha256"] = fingerprint(obj)
    return obj


def new_state() -> dict[str, Any]:
    sealed_items = [
        _sealed_item("main.title", "title", "从视频证据到可交付课程讲义"),
        _sealed_item(
            "main.paragraph.001",
            "paragraph",
            "可靠交付要求每条读者可见文字都能追溯到当前源工件。",
        ),
        _sealed_item(
            "figure.01.caption",
            "caption",
            "图 1：从规则目录到最终交付决定的证据链。",
        ),
        _sealed_item(
            "figure.01.callout",
            "callout",
            "先封印文字，再进入最终编译",
        ),
    ]
    inventory = {
        "schema_version": "reader-facing-text-inventory.prototype.v1",
        "language_profile": "zh-CN-teaching-pdf",
        "items": sealed_items,
        "declared_surface_sha256": fingerprint(
            [(item["item_id"], item["kind"]) for item in sealed_items]
        ),
        "reader_text_set_sha256": fingerprint(
            [(item["item_id"], item["text_sha256"]) for item in sealed_items]
        ),
    }
    inventory["inventory_sha256"] = fingerprint(inventory)
    precompile_seal = {
        "schema_version": "precompile-text-seal.prototype.v1",
        "inventory_sha256": inventory["inventory_sha256"],
        "reader_text_set_sha256": inventory["reader_text_set_sha256"],
        "artifact_generations": {"main_tex": 8, "figure_01": 2},
        "decision": "pass",
    }
    precompile_seal["seal_sha256"] = fingerprint(precompile_seal)

    rendered_objects = [
        _rendered_object(
            "p1.title.01",
            1,
            "pdf_text_run",
            "从视频证据到可交付课程讲义",
        ),
        _rendered_object(
            "p1.paragraph.01a",
            1,
            "pdf_text_run",
            "可靠交付要求每条读者可见文字",
        ),
        _rendered_object(
            "p1.paragraph.01b",
            1,
            "pdf_text_run",
            "都能追溯到当前源工件。",
        ),
        _rendered_object(
            "p1.caption.01",
            1,
            "pdf_text_run",
            "图 1: 从规则目录到最终交付决定的证据链。",
        ),
        _rendered_object(
            "p1.callout.01",
            1,
            "declared_raster_text",
            "先封印文字，再进入最终编译",
        ),
        _rendered_object("p1.header.01", 1, "pdf_text_run", "课程讲义"),
        _rendered_object("p1.page-number.01", 1, "pdf_text_run", "1"),
    ]
    pdf_sha = fingerprint(
        [(obj["object_id"], obj["object_sha256"]) for obj in rendered_objects]
    )
    rendered_inventory = {
        "schema_version": "rendered-text-object-inventory.prototype.v1",
        "pdf_sha256": pdf_sha,
        "extractor_suite": [
            "pdf-content-stream.prototype.v1",
            "pdf-annotation.prototype.v1",
            "pdf-form-xobject.prototype.v1",
            "declared-raster-text.prototype.v1",
        ],
        "coverage": {
            "page_count": 1,
            "pages_scanned": [1],
            "content_streams_complete": True,
            "annotations_complete": True,
            "form_xobjects_complete": True,
            "declared_raster_text_complete": True,
        },
        "objects": rendered_objects,
    }
    rendered_inventory["inventory_sha256"] = fingerprint(rendered_inventory)
    final_artifact_seal = {
        "schema_version": "final-artifact-seal.prototype.v1",
        "pdf_sha256": pdf_sha,
        "precompile_text_seal_sha256": precompile_seal["seal_sha256"],
        "compile_input_generations": copy.deepcopy(
            precompile_seal["artifact_generations"]
        ),
    }
    final_artifact_seal["seal_sha256"] = fingerprint(final_artifact_seal)

    edges = [
        {
            "edge_id": "origin.title",
            "disposition": "sealed_origin",
            "sealed_item_id": "main.title",
            "rendered_object_ids": ["p1.title.01"],
            "recipe": "exact_utf8",
        },
        {
            "edge_id": "origin.paragraph.001",
            "disposition": "sealed_origin",
            "sealed_item_id": "main.paragraph.001",
            "rendered_object_ids": ["p1.paragraph.01a", "p1.paragraph.01b"],
            "recipe": "layout_whitespace",
        },
        {
            "edge_id": "origin.caption.01",
            "disposition": "sealed_origin",
            "sealed_item_id": "figure.01.caption",
            "rendered_object_ids": ["p1.caption.01"],
            "recipe": "unicode_presentation",
        },
        {
            "edge_id": "origin.callout.01",
            "disposition": "sealed_origin",
            "sealed_item_id": "figure.01.callout",
            "rendered_object_ids": ["p1.callout.01"],
            "recipe": "exact_utf8",
        },
        {
            "edge_id": "generated.running-header",
            "disposition": "generated",
            "rendered_object_ids": ["p1.header.01"],
            "recipe": "declared_generated",
            "generator_id": "running-header.prototype.v1",
            "generator_inputs": {"literal": "课程讲义"},
        },
        {
            "edge_id": "generated.page-number",
            "disposition": "generated",
            "rendered_object_ids": ["p1.page-number.01"],
            "recipe": "declared_generated",
            "generator_id": "page-number.prototype.v1",
            "generator_inputs": {"page": 1},
        },
    ]
    origin_manifest = {
        "schema_version": "text-origin-manifest.prototype.v1",
        "compiler_provider": "guarded-xelatex-origin-trace.prototype.v1",
        "precompile_text_seal_sha256": precompile_seal["seal_sha256"],
        "final_artifact_seal_sha256": final_artifact_seal["seal_sha256"],
        "rendered_text_inventory_sha256": rendered_inventory["inventory_sha256"],
        "edges": edges,
    }
    origin_manifest["manifest_sha256"] = fingerprint(origin_manifest)
    return {
        "prototype_schema": "issue-40-rendered-text-reconciliation-state.prototype.v1",
        "sealed_inventory": inventory,
        "precompile_text_seal": precompile_seal,
        "final_artifact_seal": final_artifact_seal,
        "rendered_inventory": rendered_inventory,
        "origin_manifest": origin_manifest,
        "reconciliation_report": None,
        "last_event": "Initialized a current PDF with complete origin evidence.",
    }


def _normalize(text: str, recipe: str) -> str:
    if recipe == "exact_utf8":
        return text
    if recipe == "layout_whitespace":
        return "".join(text.split())
    if recipe == "unicode_presentation":
        normalized = (
            unicodedata.normalize("NFKC", text)
            .replace("：", ":")
            .replace("“", '"')
            .replace("”", '"')
        )
        return "".join(normalized.split())
    raise ValueError(f"unsupported recipe: {recipe}")


def _generated_text(edge: dict[str, Any]) -> str:
    generator_id = edge.get("generator_id")
    inputs = edge.get("generator_inputs", {})
    if generator_id == "running-header.prototype.v1":
        return inputs["literal"]
    if generator_id == "page-number.prototype.v1":
        return str(inputs["page"])
    raise ValueError(f"unsupported generator: {generator_id}")


def _refresh_fingerprints(state: dict[str, Any]) -> None:
    objects = state["rendered_inventory"]["objects"]
    for obj in objects:
        obj["text_sha256"] = _text_sha(obj["exact_utf8_text"])
        material = {key: value for key, value in obj.items() if key != "object_sha256"}
        obj["object_sha256"] = fingerprint(material)
    pdf_sha = fingerprint(
        [(obj["object_id"], obj["object_sha256"]) for obj in objects]
    )
    state["rendered_inventory"]["pdf_sha256"] = pdf_sha
    inventory_material = {
        key: value
        for key, value in state["rendered_inventory"].items()
        if key != "inventory_sha256"
    }
    state["rendered_inventory"]["inventory_sha256"] = fingerprint(inventory_material)
    state["final_artifact_seal"]["pdf_sha256"] = pdf_sha
    final_material = {
        key: value
        for key, value in state["final_artifact_seal"].items()
        if key != "seal_sha256"
    }
    state["final_artifact_seal"]["seal_sha256"] = fingerprint(final_material)
    state["origin_manifest"]["final_artifact_seal_sha256"] = state[
        "final_artifact_seal"
    ]["seal_sha256"]
    state["origin_manifest"]["rendered_text_inventory_sha256"] = state[
        "rendered_inventory"
    ]["inventory_sha256"]
    manifest_material = {
        key: value
        for key, value in state["origin_manifest"].items()
        if key != "manifest_sha256"
    }
    state["origin_manifest"]["manifest_sha256"] = fingerprint(manifest_material)


def _object_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        obj["object_id"]: obj for obj in state["rendered_inventory"]["objects"]
    }


def reconcile(state: dict[str, Any]) -> None:
    _refresh_fingerprints(state)
    inventory = state["sealed_inventory"]
    seal = state["precompile_text_seal"]
    final_seal = state["final_artifact_seal"]
    rendered = state["rendered_inventory"]
    manifest = state["origin_manifest"]
    sealed_by_id = {item["item_id"]: item for item in inventory["items"]}
    objects_by_id = _object_map(state)
    contract_gaps: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    input_checks = {
        "precompile_seal_passes": seal["decision"] == "pass",
        "sealed_inventory_current": (
            seal["inventory_sha256"] == inventory["inventory_sha256"]
        ),
        "final_seal_binds_precompile_seal": (
            final_seal["precompile_text_seal_sha256"] == seal["seal_sha256"]
        ),
        "final_seal_binds_pdf": final_seal["pdf_sha256"] == rendered["pdf_sha256"],
        "manifest_binds_final_seal": (
            manifest["final_artifact_seal_sha256"] == final_seal["seal_sha256"]
        ),
        "manifest_binds_rendered_inventory": (
            manifest["rendered_text_inventory_sha256"]
            == rendered["inventory_sha256"]
        ),
    }
    for check, passed in input_checks.items():
        if not passed:
            contract_gaps.append({"code": "STALE_OR_MISMATCHED_INPUT", "check": check})

    coverage = rendered["coverage"]
    extraction_checks = {
        "all_pages_scanned": coverage["pages_scanned"]
        == list(range(1, coverage["page_count"] + 1)),
        "content_streams_complete": coverage["content_streams_complete"],
        "annotations_complete": coverage["annotations_complete"],
        "form_xobjects_complete": coverage["form_xobjects_complete"],
        "declared_raster_text_complete": coverage[
            "declared_raster_text_complete"
        ],
    }
    for check, passed in extraction_checks.items():
        if not passed:
            contract_gaps.append(
                {"code": "INCOMPLETE_EXTRACTION_COVERAGE", "check": check}
            )

    object_dispositions: dict[str, list[str]] = {
        object_id: [] for object_id in objects_by_id
    }
    sealed_dispositions: dict[str, list[str]] = {
        item_id: [] for item_id in sealed_by_id
    }
    edge_results = []
    for edge in manifest["edges"]:
        edge_id = edge["edge_id"]
        rendered_ids = edge.get("rendered_object_ids", [])
        missing_object_ids = [
            object_id for object_id in rendered_ids if object_id not in objects_by_id
        ]
        if missing_object_ids:
            contract_gaps.append(
                {
                    "code": "DANGLING_RENDERED_OBJECT_REFERENCE",
                    "edge_id": edge_id,
                    "object_ids": missing_object_ids,
                }
            )
            continue
        for object_id in rendered_ids:
            object_dispositions[object_id].append(edge_id)
        actual_text = "".join(
            objects_by_id[object_id]["exact_utf8_text"]
            for object_id in rendered_ids
        )
        disposition = edge["disposition"]
        if disposition == "sealed_origin":
            item_id = edge.get("sealed_item_id")
            if item_id not in sealed_by_id:
                contract_gaps.append(
                    {
                        "code": "DANGLING_SEALED_ITEM_REFERENCE",
                        "edge_id": edge_id,
                        "item_id": item_id,
                    }
                )
                continue
            sealed_dispositions[item_id].append(edge_id)
            recipe = edge.get("recipe")
            try:
                equivalent = _normalize(
                    sealed_by_id[item_id]["exact_utf8_text"], recipe
                ) == _normalize(actual_text, recipe)
            except ValueError:
                contract_gaps.append(
                    {
                        "code": "UNSUPPORTED_TRANSFORMATION_RECIPE",
                        "edge_id": edge_id,
                        "recipe": recipe,
                    }
                )
                continue
            result = {
                "edge_id": edge_id,
                "disposition": disposition,
                "sealed_item_id": item_id,
                "rendered_object_ids": rendered_ids,
                "recipe": recipe,
                "decision": "pass" if equivalent else "substitution",
            }
            edge_results.append(result)
            if not equivalent:
                findings.append(copy.deepcopy(result))
        elif disposition == "generated":
            try:
                expected_text = _generated_text(edge)
            except (KeyError, ValueError):
                contract_gaps.append(
                    {
                        "code": "UNSUPPORTED_GENERATOR_RECIPE",
                        "edge_id": edge_id,
                        "generator_id": edge.get("generator_id"),
                    }
                )
                continue
            equivalent = expected_text == actual_text
            result = {
                "edge_id": edge_id,
                "disposition": disposition,
                "rendered_object_ids": rendered_ids,
                "generator_id": edge["generator_id"],
                "decision": "pass" if equivalent else "generated_mismatch",
            }
            edge_results.append(result)
            if not equivalent:
                findings.append(copy.deepcopy(result))
        elif disposition == "unexpected_addition":
            result = {
                "edge_id": edge_id,
                "disposition": disposition,
                "rendered_object_ids": rendered_ids,
                "decision": "addition",
            }
            edge_results.append(result)
            findings.append(copy.deepcopy(result))
        else:
            contract_gaps.append(
                {
                    "code": "UNKNOWN_DISPOSITION",
                    "edge_id": edge_id,
                    "disposition": disposition,
                }
            )

    for item_id, edges in sealed_dispositions.items():
        if not edges:
            finding = {
                "decision": "omission",
                "sealed_item_id": item_id,
                "reason": "no rendered provenance edge",
            }
            findings.append(finding)
        elif len(edges) > 1:
            contract_gaps.append(
                {
                    "code": "AMBIGUOUS_SEALED_ITEM_DISPOSITION",
                    "sealed_item_id": item_id,
                    "edge_ids": edges,
                }
            )
    for object_id, edges in object_dispositions.items():
        if not edges:
            contract_gaps.append(
                {
                    "code": "UNMAPPED_RENDERED_TEXT",
                    "object_id": object_id,
                    "page": objects_by_id[object_id]["page"],
                }
            )
        elif len(edges) > 1:
            contract_gaps.append(
                {
                    "code": "OVERLAPPING_RENDERED_OBJECT_DISPOSITION",
                    "object_id": object_id,
                    "edge_ids": edges,
                }
            )

    report = {
        "schema_version": "rendered-text-reconciliation-report.prototype.v1",
        "provider_id": "rendered-text-reconciliation-provider.prototype.v1",
        "decision_policy": "fail_closed",
        "precompile_text_seal_sha256": seal["seal_sha256"],
        "final_artifact_seal_sha256": final_seal["seal_sha256"],
        "pdf_sha256": rendered["pdf_sha256"],
        "rendered_text_inventory_sha256": rendered["inventory_sha256"],
        "text_origin_manifest_sha256": manifest["manifest_sha256"],
        "input_checks": input_checks,
        "extraction_checks": extraction_checks,
        "coverage_proof": {
            "sealed_items_expected": len(sealed_by_id),
            "sealed_items_disposed": sum(
                1 for edges in sealed_dispositions.values() if len(edges) == 1
            ),
            "rendered_objects_expected": len(objects_by_id),
            "rendered_objects_disposed": sum(
                1 for edges in object_dispositions.values() if len(edges) == 1
            ),
        },
        "edge_results": edge_results,
        "findings": findings,
        "contract_gaps": contract_gaps,
        "overall_decision": (
            "pass" if not findings and not contract_gaps else "fail"
        ),
        "semantic_reinterpretation_performed": False,
    }
    report["report_sha256"] = fingerprint(report)
    state["reconciliation_report"] = report
    if report["overall_decision"] == "pass":
        state["last_event"] = (
            "Reconciliation passed with complete sealed-item and rendered-object coverage."
        )
    else:
        state["last_event"] = (
            f"Reconciliation failed: {len(findings)} finding(s), "
            f"{len(contract_gaps)} Contract Gap(s)."
        )


def omit_sealed_item_rendering(state: dict[str, Any]) -> None:
    state["origin_manifest"]["edges"] = [
        edge
        for edge in state["origin_manifest"]["edges"]
        if edge["edge_id"] != "origin.paragraph.001"
    ]
    paragraph_ids = {"p1.paragraph.01a", "p1.paragraph.01b"}
    state["rendered_inventory"]["objects"] = [
        obj
        for obj in state["rendered_inventory"]["objects"]
        if obj["object_id"] not in paragraph_ids
    ]
    state["reconciliation_report"] = None
    state["last_event"] = "Removed a sealed paragraph from the rendered PDF fixture."


def substitute_caption(state: dict[str, Any]) -> None:
    obj = _object_map(state)["p1.caption.01"]
    obj["exact_utf8_text"] = "图 1: 从规则目录到最终交付决定。"
    state["reconciliation_report"] = None
    state["last_event"] = "Changed a mapped caption while preserving its origin edge."


def add_classified_unexpected_text(state: dict[str, Any]) -> None:
    if "p1.stray.01" not in _object_map(state):
        state["rendered_inventory"]["objects"].append(
            _rendered_object("p1.stray.01", 1, "pdf_text_run", "内部草稿")
        )
        state["origin_manifest"]["edges"].append(
            {
                "edge_id": "unexpected.stray.01",
                "disposition": "unexpected_addition",
                "rendered_object_ids": ["p1.stray.01"],
                "recipe": "none",
            }
        )
    state["reconciliation_report"] = None
    state["last_event"] = "Added unexpected text with an explicit failure disposition."


def add_unmapped_text(state: dict[str, Any]) -> None:
    if "p1.unmapped.01" not in _object_map(state):
        state["rendered_inventory"]["objects"].append(
            _rendered_object("p1.unmapped.01", 1, "pdf_text_run", "来源未知")
        )
    state["reconciliation_report"] = None
    state["last_event"] = "Added a rendered object with no origin or failure disposition."


def corrupt_generated_text(state: dict[str, Any]) -> None:
    _object_map(state)["p1.page-number.01"]["exact_utf8_text"] = "2"
    state["reconciliation_report"] = None
    state["last_event"] = "Changed generated page text without changing its recipe inputs."


def break_extraction_coverage(state: dict[str, Any]) -> None:
    state["rendered_inventory"]["coverage"]["form_xobjects_complete"] = False
    state["reconciliation_report"] = None
    state["last_event"] = "Marked form-XObject extraction coverage incomplete."


def status_view(state: dict[str, Any]) -> dict[str, Any]:
    report = state["reconciliation_report"]
    return {
        "last_event": state["last_event"],
        "sealed_item_count": len(state["sealed_inventory"]["items"]),
        "rendered_object_count": len(state["rendered_inventory"]["objects"]),
        "origin_edge_count": len(state["origin_manifest"]["edges"]),
        "report": (
            {
                "overall_decision": report["overall_decision"],
                "finding_decisions": [
                    finding["decision"] for finding in report["findings"]
                ],
                "contract_gap_codes": [
                    gap["code"] for gap in report["contract_gaps"]
                ],
                "coverage_proof": report["coverage_proof"],
                "semantic_reinterpretation_performed": report[
                    "semantic_reinterpretation_performed"
                ],
                "sha256": report["report_sha256"],
            }
            if report
            else None
        ),
    }


def artifact_view(state: dict[str, Any], name: str) -> Any:
    if name == "state":
        return status_view(state)
    mapping = {
        "sealed": "sealed_inventory",
        "rendered": "rendered_inventory",
        "origins": "origin_manifest",
        "report": "reconciliation_report",
    }
    return state[mapping[name]]

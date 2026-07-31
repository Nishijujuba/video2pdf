from __future__ import annotations

import hashlib
from pathlib import Path
import unicodedata
from typing import Any

from .delivery_quality import DeliveryQualityRegistry
from .errors import (
    ContractError,
    RenderedTextReconciliationContractGap,
    RenderedTextReconciliationFailed,
)
from .final_compile import final_compile_provider_identity
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic


RECIPES = ("exact_utf8", "layout_whitespace", "unicode_presentation", "declared_generated")
OBJECT_KINDS = (
    "pdf_text_run",
    "text_annotation",
    "form_xobject_text",
    "declared_raster_text",
)
REGISTERED_GENERATORS = {
    "page-number-v1": {
        "generator_id": "page-number-v1",
        "generator_version": "1.0.0",
        "kind": "page_number",
    },
}


def _fingerprint_without(value: dict[str, Any], field: str) -> str:
    material = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(canonical_json_bytes(material)).hexdigest()


def _require_fingerprint(value: dict[str, Any], field: str, label: str) -> None:
    if value.get(field) != _fingerprint_without(value, field):
        raise ContractError(f"{label} {field} is stale or invalid")


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
    raise ValueError(recipe)


def guarded_compile_provider_identity(project_root: Path) -> dict[str, str]:
    return final_compile_provider_identity(project_root)


def registered_generator_identity(generator_id: str) -> dict[str, str]:
    contract = REGISTERED_GENERATORS[generator_id]
    return {
        **contract,
        "generator_sha256": hashlib.sha256(
            canonical_json_bytes(contract)
        ).hexdigest(),
    }


def _generated_text(generator: dict[str, Any]) -> str:
    kind = generator.get("kind")
    inputs = generator.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("missing generator inputs")
    if kind == "literal":
        value = inputs.get("literal")
        if not isinstance(value, str):
            raise ValueError("invalid literal generator")
        return value
    if kind == "page_number":
        page = inputs.get("page")
        if not isinstance(page, int) or page < 1:
            raise ValueError("invalid page generator")
        return str(page)
    raise ValueError("unsupported generator")


class RenderedTextReconciliationProvider:
    """Fail-closed target-only final evidence and text-origin reconciler."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.registry = DeliveryQualityRegistry(self.project_root)

    def reconcile(
        self,
        *,
        precompile_workspace_root: Path,
        compile_manifest_path: Path,
        compile_report_path: Path,
        final_artifact_seal_path: Path,
        final_pdf_path: Path,
        render_evidence_manifest_path: Path,
        rendered_text_inventory_path: Path,
        text_origin_manifest_path: Path,
        output_path: Path,
        reconciled_at: str,
    ) -> dict[str, Any]:
        self.registry.check()
        precompile_root = precompile_workspace_root.resolve()
        seal = read_json(precompile_root / "precompile-text-seal.json")
        _require_fingerprint(seal, "seal_sha256", "Precompile Text Seal")
        if seal.get("activation_status") != "target_only":
            raise ContractError("Precompile Text Seal would change runtime authority")
        binding_root = precompile_root / "seal-bindings" / seal["seal_sha256"]
        inventory = read_json(binding_root / "reader-facing-text-inventory.json")
        generations = read_json(binding_root / "artifact-generations.json")
        _require_fingerprint(inventory, "inventory_sha256", "sealed inventory")
        _require_fingerprint(generations, "generation_set_sha256", "sealed generations")
        if (
            seal.get("inventory_sha256") != inventory["inventory_sha256"]
            or seal.get("generation_set_sha256") != generations["generation_set_sha256"]
            or seal.get("reader_text_set_sha256") != inventory.get("reader_text_set_sha256")
        ):
            raise ContractError("Precompile Text Seal binding snapshot is stale")

        compile_manifest = read_json(compile_manifest_path.resolve())
        compile_report = read_json(compile_report_path.resolve())
        final_seal = read_json(final_artifact_seal_path.resolve())
        render_evidence = read_json(render_evidence_manifest_path.resolve())
        rendered = read_json(rendered_text_inventory_path.resolve())
        origins = read_json(text_origin_manifest_path.resolve())
        for schema_name, value in (
            ("final-compile-report", compile_report),
            ("final-artifact-seal", final_seal),
            ("render-evidence-manifest", render_evidence),
            ("rendered-text-object-inventory", rendered),
            ("text-origin-manifest", origins),
        ):
            self.registry.validate(schema_name, value)
        for value, field, label in (
            (compile_manifest, "manifest_sha256", "Compile Manifest"),
            (compile_report, "report_sha256", "Final Compile Report"),
            (final_seal, "seal_sha256", "Final Artifact Seal"),
            (render_evidence, "manifest_sha256", "Render Evidence Manifest"),
            (rendered, "inventory_sha256", "Rendered Text Object Inventory"),
            (origins, "manifest_sha256", "Text Origin Manifest"),
        ):
            _require_fingerprint(value, field, label)

        pdf_path = final_pdf_path.resolve()
        pdf_sha256 = sha256_file(pdf_path)
        compile_entry_list = compile_manifest.get("entries", [])
        compile_entries = {
            (entry.get("logical_id"), entry.get("generation"), entry.get("sha256"))
            for entry in compile_entry_list
        }
        sealed_entries = {
            (entry.get("logical_id"), entry.get("generation"), entry.get("sha256"))
            for entry in generations.get("artifacts", [])
        }
        compile_entry_identities = [
            entry.get("logical_id") for entry in compile_entry_list
        ]
        compile_closure_exact = (
            len(compile_entry_list) == len(sealed_entries)
            and len(compile_entry_identities) == len(set(compile_entry_identities))
            and compile_entries == sealed_entries
        )
        registered_compile_provider = guarded_compile_provider_identity(
            self.project_root
        )
        input_checks = {
            "compile_manifest_mode_final": compile_manifest.get("mode") == "final",
            "compile_input_closure_exact": compile_closure_exact,
            "compile_report_passed": compile_report.get("status") == "pass",
            "compile_dependency_closure_complete": compile_report.get("dependency_closure", {}).get("complete") is True,
            "compile_report_mode_final": compile_report.get("mode") == "final",
            "compile_report_target_only": compile_report.get("delivery_authority") is False,
            "final_seal_binds_precompile_seal": final_seal.get("precompile_text_seal_sha256") == seal["seal_sha256"],
            "final_seal_binds_generation_set": final_seal.get("generation_set_sha256") == generations["generation_set_sha256"],
            "final_seal_binds_compile_manifest": final_seal.get("compile_manifest_sha256") == compile_manifest.get("manifest_sha256"),
            "final_seal_binds_pdf": final_seal.get("final_pdf", {}).get("sha256") == pdf_sha256,
            "final_seal_binds_pdf_size": final_seal.get("final_pdf", {}).get("size") == pdf_path.stat().st_size,
            "compile_report_binds_precompile_seal": compile_report.get("precompile_text_seal_sha256") == seal["seal_sha256"],
            "compile_report_binds_final_seal": compile_report.get("final_artifact_seal_sha256") == final_seal["seal_sha256"],
            "compile_report_binds_manifest": compile_report.get("compile_manifest_sha256") == compile_manifest.get("manifest_sha256"),
            "compile_report_binds_pdf": compile_report.get("pdf", {}).get("sha256") == pdf_sha256,
            "compile_provider_registered": final_seal.get("compile_provider") == registered_compile_provider,
            "compile_report_binds_provider": compile_report.get("compiler_provider") == registered_compile_provider,
            "render_evidence_binds_pdf": render_evidence.get("final_pdf_sha256") == pdf_sha256,
            "rendered_inventory_binds_pdf": rendered.get("final_pdf_sha256") == pdf_sha256,
            "origin_manifest_binds_precompile_seal": origins.get("precompile_text_seal_sha256") == seal["seal_sha256"],
            "origin_manifest_binds_final_seal": origins.get("final_artifact_seal_sha256") == final_seal["seal_sha256"],
            "origin_manifest_binds_rendered_inventory": origins.get("rendered_text_inventory_sha256") == rendered["inventory_sha256"],
            "origin_manifest_binds_compiler_provider": origins.get("compiler_provider") == final_seal.get("compile_provider"),
            "compile_report_binds_render_evidence": compile_report.get("render_evidence_manifest_sha256") == render_evidence.get("manifest_sha256"),
            "compile_report_binds_rendered_inventory": compile_report.get("rendered_text_inventory_sha256") == rendered.get("inventory_sha256"),
            "compile_report_binds_origin_manifest": compile_report.get("text_origin_manifest_sha256") == origins.get("manifest_sha256"),
        }
        contract_gaps = [
            {"code": "STALE_OR_MISMATCHED_INPUT", "check": check}
            for check, passed in input_checks.items()
            if not passed
        ]

        coverage = rendered.get("coverage", {})
        page_count = coverage.get("page_count")
        expected_pages = list(range(1, page_count + 1)) if isinstance(page_count, int) and page_count > 0 else []
        render_pages = [item.get("page") for item in render_evidence.get("pages", [])]
        render_root = render_evidence_manifest_path.resolve().parent
        page_files_current = True
        for page in render_evidence.get("pages", []):
            value = page.get("path")
            if not isinstance(value, str) or not value or "\\" in value:
                page_files_current = False
                continue
            path = (render_root / value).resolve()
            try:
                path.relative_to(render_root)
            except ValueError:
                page_files_current = False
                continue
            if not path.is_file() or sha256_file(path) != page.get("sha256"):
                page_files_current = False
        extraction_checks = {
            "all_pages_scanned": coverage.get("pages_scanned") == expected_pages,
            "render_evidence_complete": render_evidence.get("page_count") == page_count and render_pages == expected_pages,
            "rendered_page_files_current": page_files_current,
            "content_streams_complete": coverage.get("content_streams_complete") is True,
            "annotations_complete": coverage.get("annotations_complete") is True,
            "form_xobjects_complete": coverage.get("form_xobjects_complete") is True,
            "declared_raster_text_complete": coverage.get("declared_raster_text_complete") is True,
        }
        contract_gaps.extend(
            {"code": "INCOMPLETE_EXTRACTION_COVERAGE", "check": check}
            for check, passed in extraction_checks.items()
            if not passed
        )

        sealed_by_id = {item.get("item_id"): item for item in inventory.get("items", [])}
        objects_by_id: dict[str, dict[str, Any]] = {}
        registered_extractors = {
            item.get("extractor_id") for item in rendered.get("extractor_suite", [])
        }
        for obj in rendered.get("objects", []):
            object_id = obj.get("object_id")
            if not object_id or object_id in objects_by_id:
                contract_gaps.append({"code": "DUPLICATE_RENDERED_OBJECT_ID", "object_id": object_id})
                continue
            objects_by_id[object_id] = obj
            if obj.get("object_kind") not in OBJECT_KINDS:
                contract_gaps.append({"code": "UNSUPPORTED_RENDERED_OBJECT_KIND", "object_id": object_id})
            if obj.get("extractor_id") not in registered_extractors:
                contract_gaps.append({"code": "UNREGISTERED_RENDERED_TEXT_EXTRACTOR", "object_id": object_id})
            if obj.get("page") not in expected_pages:
                contract_gaps.append({"code": "RENDERED_OBJECT_OUTSIDE_PAGE_COVERAGE", "object_id": object_id})
            if obj.get("text_sha256") != hashlib.sha256(str(obj.get("exact_utf8_text", "")).encode("utf-8")).hexdigest():
                contract_gaps.append({"code": "RENDERED_OBJECT_TEXT_DRIFT", "object_id": object_id})
            if obj.get("object_sha256") != _fingerprint_without(obj, "object_sha256"):
                contract_gaps.append({"code": "RENDERED_OBJECT_FINGERPRINT_DRIFT", "object_id": object_id})

        object_edges = {object_id: [] for object_id in objects_by_id}
        sealed_edges = {item_id: [] for item_id in sealed_by_id}
        edge_ids: set[str] = set()
        findings: list[dict[str, Any]] = []
        edge_results: list[dict[str, Any]] = []
        for edge in origins.get("edges", []):
            edge_id = edge.get("edge_id")
            if not edge_id or edge_id in edge_ids:
                contract_gaps.append({"code": "DUPLICATE_ORIGIN_EDGE_ID", "edge_id": edge_id})
                continue
            edge_ids.add(edge_id)
            rendered_ids = edge.get("rendered_object_ids", [])
            missing = [object_id for object_id in rendered_ids if object_id not in objects_by_id]
            if missing:
                contract_gaps.append({"code": "DANGLING_RENDERED_OBJECT_REFERENCE", "edge_id": edge_id, "object_ids": missing})
                continue
            for object_id in rendered_ids:
                object_edges[object_id].append(edge_id)
            actual = "".join(objects_by_id[object_id]["exact_utf8_text"] for object_id in rendered_ids)
            disposition = edge.get("disposition")
            if disposition == "sealed_origin":
                item_id = edge.get("sealed_item_id")
                item = sealed_by_id.get(item_id)
                if item is None:
                    contract_gaps.append({"code": "DANGLING_SEALED_ITEM_REFERENCE", "edge_id": edge_id, "item_id": item_id})
                    continue
                sealed_edges[item_id].append(edge_id)
                sealed_text = edge.get("sealed_text_utf8")
                if not isinstance(sealed_text, str) or hashlib.sha256(sealed_text.encode("utf-8")).hexdigest() != item.get("text_sha256"):
                    contract_gaps.append({"code": "SEALED_TEXT_REPRESENTATION_MISMATCH", "edge_id": edge_id})
                    continue
                recipe = edge.get("recipe")
                if recipe not in RECIPES or recipe == "declared_generated":
                    contract_gaps.append({"code": "UNSUPPORTED_TRANSFORMATION_RECIPE", "edge_id": edge_id, "recipe": recipe})
                    continue
                equivalent = _normalize(sealed_text, recipe) == _normalize(actual, recipe)
                result = {"edge_id": edge_id, "disposition": disposition, "sealed_item_id": item_id, "rendered_object_ids": rendered_ids, "recipe": recipe, "decision": "pass" if equivalent else "substitution"}
                edge_results.append(result)
                if not equivalent:
                    findings.append(result)
            elif disposition == "generated":
                if edge.get("recipe") != "declared_generated":
                    contract_gaps.append({"code": "UNSUPPORTED_TRANSFORMATION_RECIPE", "edge_id": edge_id, "recipe": edge.get("recipe")})
                    continue
                generator = edge.get("generator")
                generator_id = (
                    generator.get("generator_id")
                    if isinstance(generator, dict)
                    else None
                )
                expected_generator = (
                    registered_generator_identity(generator_id)
                    if generator_id in REGISTERED_GENERATORS
                    else None
                )
                if (
                    not isinstance(generator, dict)
                    or expected_generator is None
                    or {
                        key: generator.get(key) for key in expected_generator
                    }
                    != expected_generator
                ):
                    contract_gaps.append({"code": "UNSUPPORTED_GENERATOR_RECIPE", "edge_id": edge_id})
                    continue
                try:
                    expected = _generated_text(generator)
                except ValueError:
                    contract_gaps.append({"code": "UNSUPPORTED_GENERATOR_RECIPE", "edge_id": edge_id})
                    continue
                equivalent = expected == actual
                result = {"edge_id": edge_id, "disposition": disposition, "rendered_object_ids": rendered_ids, "generator_id": generator["generator_id"], "decision": "pass" if equivalent else "generated_mismatch"}
                edge_results.append(result)
                if not equivalent:
                    findings.append(result)
            elif disposition == "unexpected_addition":
                result = {"edge_id": edge_id, "disposition": disposition, "rendered_object_ids": rendered_ids, "decision": "addition"}
                edge_results.append(result)
                findings.append(result)
            else:
                contract_gaps.append({"code": "UNKNOWN_DISPOSITION", "edge_id": edge_id, "disposition": disposition})

        for item_id, edges in sealed_edges.items():
            if not edges:
                findings.append({"decision": "omission", "sealed_item_id": item_id, "reason": "no rendered provenance edge"})
            elif len(edges) > 1:
                contract_gaps.append({"code": "AMBIGUOUS_SEALED_ITEM_DISPOSITION", "sealed_item_id": item_id, "edge_ids": edges})
        for object_id, edges in object_edges.items():
            if not edges:
                contract_gaps.append({"code": "UNMAPPED_RENDERED_TEXT", "object_id": object_id, "page": objects_by_id[object_id].get("page")})
            elif len(edges) > 1:
                contract_gaps.append({"code": "OVERLAPPING_RENDERED_OBJECT_DISPOSITION", "object_id": object_id, "edge_ids": edges})
        raster_item_ids = {
            item_id for item_id, item in sealed_by_id.items()
            if item.get("representation") == "authoritative_raster_text"
        }
        raster_object_ids = {
            object_id for object_id, obj in objects_by_id.items()
            if obj.get("object_kind") == "declared_raster_text"
        }
        for item_id in sorted(raster_item_ids):
            edges = sealed_edges.get(item_id, [])
            mapped = {
                object_id
                for edge in origins.get("edges", [])
                if edge.get("edge_id") in edges
                for object_id in edge.get("rendered_object_ids", [])
            }
            if not mapped or not mapped <= raster_object_ids:
                contract_gaps.append({"code": "MISSING_RASTER_TEXT_REPRESENTATION", "sealed_item_id": item_id})

        overall = "blocked_contract_gap" if contract_gaps else ("fail" if findings else "pass")
        report = {
            "schema_name": "rendered-text-reconciliation-report",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "reconciled_at": reconciled_at,
            "provider": {"provider_id": "rendered-text-reconciliation-provider", "provider_version": "1.0.0"},
            "decision_policy": "fail_closed",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "final_artifact_seal_sha256": final_seal["seal_sha256"],
            "final_pdf_sha256": pdf_sha256,
            "render_evidence_manifest_sha256": render_evidence["manifest_sha256"],
            "rendered_text_inventory_sha256": rendered["inventory_sha256"],
            "text_origin_manifest_sha256": origins["manifest_sha256"],
            "recipe_registry": {"registry_id": "rendered-text-recipes-v1", "recipes": list(RECIPES)},
            "input_checks": input_checks,
            "extraction_checks": extraction_checks,
            "coverage_proof": {
                "sealed_items_expected": len(sealed_by_id),
                "sealed_items_disposed": sum(1 for edges in sealed_edges.values() if len(edges) == 1),
                "rendered_objects_expected": len(objects_by_id),
                "rendered_objects_disposed": sum(1 for edges in object_edges.values() if len(edges) == 1),
            },
            "edge_results": edge_results,
            "findings": findings,
            "contract_gaps": contract_gaps,
            "overall_decision": overall,
            "semantic_reinterpretation_performed": False,
        }
        report["report_sha256"] = _fingerprint_without(report, "report_sha256")
        self.registry.validate("rendered-text-reconciliation-report", report)
        output = output_path.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and output.read_bytes() != canonical_json_bytes(report):
            raise ContractError("Rendered Text Reconciliation Report is immutable")
        write_json_atomic(output, report)
        data = {"overall_decision": overall, "finding_count": len(findings), "contract_gap_count": len(contract_gaps), "report_sha256": report["report_sha256"], "report_path": str(output), "activation_status": "target_only"}
        if contract_gaps:
            raise RenderedTextReconciliationContractGap("rendered-text reconciliation has unresolved Contract Gaps", data={**data, "evidence_path": str(output)})
        if findings:
            raise RenderedTextReconciliationFailed("rendered-text reconciliation found final-text fidelity failures", data={**data, "evidence_path": str(output)})
        return data

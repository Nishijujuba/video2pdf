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
from .final_compile import (
    REGISTERED_GENERATORS,
    final_compile_provider_identity,
    registered_generator_identity,
)
from .utils import canonical_json_bytes, read_json, sha256_file, write_json_atomic


RECIPES = (
    "exact_utf8",
    "layout_whitespace",
    "unicode_presentation",
    "compiler_source_map",
    "declared_generated",
)
OBJECT_KINDS = (
    "pdf_text_run",
    "text_annotation",
    "form_xobject_text",
    "declared_raster_text",
)


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


def _generated_texts(generator: dict[str, Any]) -> tuple[str, ...]:
    kind = generator.get("kind")
    inputs = generator.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("missing generator inputs")
    if kind == "page_number":
        first_page_number = inputs.get("first_page_number")
        page_count = inputs.get("page_count")
        if (
            set(inputs) != {"first_page_number", "page_count"}
            or not isinstance(first_page_number, int)
            or isinstance(first_page_number, bool)
            or first_page_number < 1
            or not isinstance(page_count, int)
            or isinstance(page_count, bool)
            or page_count < 1
        ):
            raise ValueError("invalid page generator")
        return tuple(
            str(page) for page in range(first_page_number, first_page_number + page_count)
        )
    if kind == "latex_style_box_title":
        texts = inputs.get("texts")
        source_artifact = inputs.get("source_artifact")
        if (
            set(inputs) != {"texts", "source_artifact"}
            or not isinstance(texts, list)
            or not texts
            or any(not isinstance(value, str) or not value for value in texts)
            or not isinstance(source_artifact, dict)
            or set(source_artifact) != {"logical_id", "generation", "sha256"}
            or not isinstance(source_artifact.get("logical_id"), str)
            or not source_artifact["logical_id"]
            or not isinstance(source_artifact.get("generation"), int)
            or isinstance(source_artifact.get("generation"), bool)
            or source_artifact["generation"] < 1
            or not isinstance(source_artifact.get("sha256"), str)
            or len(source_artifact["sha256"]) != 64
        ):
            raise ValueError("invalid LaTeX style text generator")
        return tuple(texts)
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
        self.registry.validate("precompile-text-seal", seal)
        _require_fingerprint(seal, "seal_sha256", "Precompile Text Seal")
        if seal.get("activation_status") != "target_only":
            raise ContractError("Precompile Text Seal would change runtime authority")
        binding_root = precompile_root / "seal-bindings" / seal["seal_sha256"]
        inventory = read_json(binding_root / "reader-facing-text-inventory.json")
        generations = read_json(binding_root / "artifact-generations.json")
        self.registry.validate("reader-facing-text-inventory", inventory)
        self.registry.validate("precompile-artifact-generation-set", generations)
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
            ("final-compile-manifest", compile_manifest),
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

        runtime_policy_binding = compile_manifest.get("runtime_policy", {})
        runtime_policy_path = Path(
            str(runtime_policy_binding.get("path", ""))
        ).resolve()
        runtime_policy_current = (
            runtime_policy_path.is_file()
            and sha256_file(runtime_policy_path)
            == runtime_policy_binding.get("sha256")
        )
        runtime_policy = (
            read_json(runtime_policy_path) if runtime_policy_current else {}
        )

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
        expected_closure_inputs = [
            {
                "logical_id": entry.get("logical_id"),
                "generation": entry.get("generation"),
                "sha256": entry.get("sha256"),
            }
            for entry in compile_entry_list
        ]
        reported_closure = compile_report.get("dependency_closure", {})
        reported_closure_inputs = reported_closure.get("inputs", [])
        reported_runtime_inputs = reported_closure.get("runtime_inputs", [])
        reported_generated_inputs = reported_closure.get("generated_inputs", [])
        compile_closure_exact = (
            len(compile_entry_list) == len(sealed_entries)
            and len(compile_entry_identities) == len(set(compile_entry_identities))
            and compile_entries == sealed_entries
        )
        recorder_closure_exact = (
            len(reported_closure_inputs) == len(expected_closure_inputs)
            and sorted(reported_closure_inputs, key=lambda item: item.get("logical_id", ""))
            == sorted(expected_closure_inputs, key=lambda item: item.get("logical_id", ""))
        )
        runtime_closure_exact = (
            reported_runtime_inputs
            == compile_manifest.get("approved_runtime_inputs", [])
            and all(
                Path(item.get("path", "")).is_file()
                and sha256_file(Path(item["path"]).resolve()) == item.get("sha256")
                for item in reported_runtime_inputs
                if isinstance(item, dict)
            )
            and len(reported_runtime_inputs)
            == sum(isinstance(item, dict) for item in reported_runtime_inputs)
        )
        generated_inputs_current = (
            all(
                Path(item.get("path", "")).is_file()
                and sha256_file(Path(item["path"]).resolve()) == item.get("sha256")
                and item.get("classification") == "attempt_generated_auxiliary"
                for item in reported_generated_inputs
                if isinstance(item, dict)
            )
            and len(reported_generated_inputs)
            == sum(isinstance(item, dict) for item in reported_generated_inputs)
        )
        registered_compile_provider = final_compile_provider_identity(
            self.project_root
        )
        adapter_identity = compile_report.get("compile_adapter", {})
        adapter_path = Path(adapter_identity.get("adapter_path", "")).resolve()
        try:
            adapter_path.relative_to(self.project_root)
            compile_adapter_current = (
                adapter_path.is_file()
                and sha256_file(adapter_path) == adapter_identity.get("adapter_sha256")
            )
        except ValueError:
            compile_adapter_current = False
        recorder_path_value = reported_closure.get("recorder_path")
        compile_recorder_current = False
        if (
            isinstance(recorder_path_value, str)
            and recorder_path_value
            and "\\" not in recorder_path_value
        ):
            recorder_path = (compile_report_path.resolve().parent / recorder_path_value).resolve()
            try:
                recorder_path.relative_to(compile_report_path.resolve().parent)
                compile_recorder_current = (
                    recorder_path.is_file()
                    and sha256_file(recorder_path)
                    == reported_closure.get("recorder_sha256")
                )
            except ValueError:
                compile_recorder_current = False
        input_checks = {
            "compile_manifest_mode_final": compile_manifest.get("mode") == "final",
            "compile_input_closure_exact": compile_closure_exact,
            "compile_report_passed": compile_report.get("status") == "pass",
            "compile_dependency_closure_complete": reported_closure.get("complete") is True,
            "compile_recorder_closure_exact": recorder_closure_exact,
            "compile_runtime_closure_exact": runtime_closure_exact,
            "compile_generated_inputs_current": generated_inputs_current,
            "compile_recorder_current": compile_recorder_current,
            "compile_adapter_current": compile_adapter_current,
            "compile_runtime_policy_current": runtime_policy_current,
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
                if recipe == "compiler_source_map":
                    source_mapping = edge.get("source_mapping")
                    equivalent = (
                        isinstance(source_mapping, dict)
                        and source_mapping.get("method") == "compiler_synctex_v1"
                        and source_mapping.get("logical_id")
                        == item.get("source_artifact_logical_id")
                        and source_mapping.get("generation")
                        == item.get("source_generation")
                        and source_mapping.get("sha256")
                        == item.get("source_sha256")
                        and {
                            value.get("object_id")
                            for value in source_mapping.get("object_sources", [])
                            if isinstance(value, dict)
                        }
                        == set(rendered_ids)
                    )
                else:
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
                if generator.get("kind") == "latex_style_box_title":
                    item_id = edge.get("sealed_item_id")
                    item = sealed_by_id.get(item_id)
                    inputs = generator.get("inputs", {})
                    source_artifact = inputs.get("source_artifact")
                    generated_titles = inputs.get("texts")
                    declared_titles = (
                        [
                            value
                            for value in item.get("declared_text", "").splitlines()
                            if value
                        ]
                        if isinstance(item, dict)
                        else []
                    )
                    if (
                        item is None
                        or item.get("representation")
                        != "declared_generated_text"
                        or not declared_titles
                        or len(declared_titles) != len(set(declared_titles))
                        or not isinstance(generated_titles, list)
                        or any(
                            not isinstance(value, str) or not value
                            for value in generated_titles
                        )
                        or set(generated_titles) != set(declared_titles)
                        or source_artifact
                        != {
                            "logical_id": item.get(
                                "source_artifact_logical_id"
                            ),
                            "generation": item.get("source_generation"),
                            "sha256": item.get("source_sha256"),
                        }
                    ):
                        contract_gaps.append(
                            {
                                "code": "UNSUPPORTED_GENERATOR_SOURCE",
                                "edge_id": edge_id,
                            }
                        )
                        continue
                    sealed_edges[item_id].append(edge_id)
                source_mapping = generator.get("source_mapping")
                object_sources = (
                    source_mapping.get("object_sources")
                    if isinstance(source_mapping, dict)
                    else None
                )
                source_provider = (
                    source_mapping.get("provider")
                    if isinstance(source_mapping, dict)
                    else None
                )
                source_records_complete = (
                    isinstance(object_sources, list)
                    and bool(object_sources)
                    and all(
                        isinstance(value, dict)
                        and isinstance(value.get("object_id"), str)
                        and isinstance(value.get("source_path"), str)
                        and value["source_path"]
                        and isinstance(value.get("line"), int)
                        and not isinstance(value.get("line"), bool)
                        and value["line"] >= 1
                        and isinstance(value.get("column"), int)
                        and not isinstance(value.get("column"), bool)
                        and isinstance(value.get("query"), dict)
                        and isinstance(value["query"].get("page"), int)
                        and not isinstance(value["query"].get("page"), bool)
                        and isinstance(value["query"].get("x"), (int, float))
                        and isinstance(value["query"].get("y"), (int, float))
                        and value.get("object_id") in objects_by_id
                        and value["query"].get("page")
                        == objects_by_id[value["object_id"]].get("page")
                        and value["query"].get("x")
                        == (
                            objects_by_id[value["object_id"]]["bbox"][0]
                            + objects_by_id[value["object_id"]]["bbox"][2]
                        )
                        / 2
                        and value["query"].get("y")
                        == (
                            objects_by_id[value["object_id"]]["bbox"][1]
                            + objects_by_id[value["object_id"]]["bbox"][3]
                        )
                        / 2
                        and (
                            any(
                                Path(value["source_path"]).resolve().is_file()
                                and Path(value["source_path"])
                                .resolve()
                                .as_posix()
                                .endswith("/" + entry["staging_path"])
                                and sha256_file(
                                    Path(value["source_path"]).resolve()
                                )
                                == entry["sha256"]
                                for entry in compile_entry_list
                            )
                            or any(
                                isinstance(generated_input, dict)
                                and generated_input.get("classification")
                                == "attempt_generated_auxiliary"
                                and Path(value["source_path"]).resolve()
                                == Path(
                                    str(generated_input.get("path", ""))
                                ).resolve()
                                and Path(value["source_path"]).resolve().is_file()
                                and sha256_file(
                                    Path(value["source_path"]).resolve()
                                )
                                == generated_input.get("sha256")
                                for generated_input in reported_generated_inputs
                            )
                        )
                        for value in object_sources
                    )
                )
                provider_current = (
                    isinstance(source_provider, dict)
                    and isinstance(source_provider.get("provider_id"), str)
                    and bool(source_provider["provider_id"])
                    and isinstance(source_provider.get("provider_sha256"), str)
                    and len(source_provider["provider_sha256"]) == 64
                    and all(
                        character in "0123456789abcdef"
                        for character in source_provider["provider_sha256"]
                    )
                )
                if provider_current and runtime_policy.get("policy_id") == "miktex-xelatex-runtime":
                    tool = Path(str(source_provider.get("tool_path", ""))).resolve()
                    runtime_roots = [
                        Path(value).resolve()
                        for value in runtime_policy.get("allowed_runtime_roots", [])
                    ]
                    provider_current = (
                        source_provider.get("provider_id")
                        == "synctex-reverse-map-v1"
                        and tool.is_file()
                        and sha256_file(tool)
                        == source_provider.get("provider_sha256")
                        and any(
                            tool == root or root in tool.parents
                            for root in runtime_roots
                        )
                    )
                if (
                    not isinstance(source_mapping, dict)
                    or source_mapping.get("method") != "compiler_synctex_v1"
                    or not source_records_complete
                    or not provider_current
                    or [value.get("object_id") for value in object_sources]
                    != rendered_ids
                ):
                    contract_gaps.append(
                        {"code": "UNSUPPORTED_GENERATOR_SOURCE", "edge_id": edge_id}
                    )
                    continue
                try:
                    expected = _generated_texts(generator)
                except ValueError:
                    contract_gaps.append({"code": "UNSUPPORTED_GENERATOR_RECIPE", "edge_id": edge_id})
                    continue
                if len(expected) != len(rendered_ids):
                    contract_gaps.append({"code": "UNSUPPORTED_GENERATOR_RECIPE", "edge_id": edge_id})
                    continue
                actual_generated_texts = tuple(
                    objects_by_id[object_id]["exact_utf8_text"]
                    for object_id in rendered_ids
                )
                equivalent = expected == actual_generated_texts
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

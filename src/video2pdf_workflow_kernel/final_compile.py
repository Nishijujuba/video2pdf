from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any

import fitz

from .delivery_quality import DeliveryQualityRegistry
from .evidence import EvidenceSupportError, git_output, sha256_git_blob
from .errors import CompileDependencyGap, ContractError
from .guarded_compile import GuardedCompileProvider
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_file,
    write_json_atomic,
)


def _fingerprint_without(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes({key: item for key, item in value.items() if key != field})
    ).hexdigest()


def _require_fingerprint(value: dict[str, Any], field: str, label: str) -> None:
    if value.get(field) != _fingerprint_without(value, field):
        raise ContractError(f"{label} {field} is stale or invalid")


def final_compile_provider_identity(project_root: Path) -> dict[str, str]:
    return {
        "provider_id": "guarded-final-compile-provider",
        "provider_sha256": sha256_file(
            project_root.resolve() / "src/video2pdf_workflow_kernel/final_compile.py"
        ),
    }


REGISTERED_GENERATORS = {
    "latex-style-box-title-v1": {
        "generator_id": "latex-style-box-title-v1",
        "generator_version": "1.0.0",
        "kind": "latex_style_box_title",
    },
    "page-number-v1": {
        "generator_id": "page-number-v1",
        "generator_version": "1.0.0",
        "kind": "page_number",
    },
}
GENERATED_RECORDER_SUFFIXES = frozenset(
    {".aux", ".toc", ".out", ".log", ".xdv", ".bcf", ".run.xml"}
)


def registered_generator_identity(generator_id: str) -> dict[str, str]:
    contract = REGISTERED_GENERATORS[generator_id]
    return {
        **contract,
        "generator_sha256": hashlib.sha256(canonical_json_bytes(contract)).hexdigest(),
    }


def _validate_derived_text_origin_evidence(evidence: dict[str, Any]) -> None:
    page_count = evidence.get("page_count")
    extractors = evidence.get("extractor_suite")
    objects = evidence.get("rendered_objects")
    edges = evidence.get("edges")
    sealed_items = evidence.get("sealed_items")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        raise ContractError("derived Text Origin page_count is invalid")
    if not isinstance(extractors, list) or not extractors:
        raise ContractError("derived Text Origin extractor suite is incomplete")
    extractor_ids = [item.get("extractor_id") for item in extractors if isinstance(item, dict)]
    if (
        len(extractor_ids) != len(extractors)
        or len(extractor_ids) != len(set(extractor_ids))
        or any(not isinstance(value, str) or not value for value in extractor_ids)
        or any(
            not isinstance(item.get("extractor_sha256"), str)
            or len(item["extractor_sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in item["extractor_sha256"])
            for item in extractors
        )
    ):
        raise ContractError("derived Text Origin extractor suite is invalid")
    if not isinstance(objects, list) or not objects:
        raise ContractError("derived Text Origin rendered objects are incomplete")
    if not isinstance(sealed_items, list) or not sealed_items or any(
        not isinstance(item, dict) for item in sealed_items
    ):
        raise ContractError("derived Text Origin sealed items are incomplete")
    object_ids: list[str] = []
    for item in objects:
        if not isinstance(item, dict):
            raise ContractError("derived Text Origin rendered object is invalid")
        object_id = item.get("object_id")
        object_ids.append(object_id)
        if (
            not isinstance(object_id, str)
            or not object_id
            or item.get("page") not in range(1, page_count + 1)
            or item.get("extractor_id") not in extractor_ids
            or not isinstance(item.get("object_kind"), str)
            or not item.get("object_kind")
            or not isinstance(item.get("bbox"), list)
            or len(item.get("bbox")) != 4
            or any(not isinstance(value, (int, float)) for value in item["bbox"])
            or not isinstance(item.get("exact_utf8_text"), str)
            or not isinstance(item.get("evidence_locator"), str)
            or not item.get("evidence_locator")
        ):
            raise ContractError("derived Text Origin rendered object is invalid")
        if item["object_kind"] == "declared_raster_text":
            source_path = item.get("source_path")
            source_pure = (
                PurePosixPath(source_path)
                if isinstance(source_path, str) and source_path
                else None
            )
            if (
                not isinstance(item.get("source_artifact_logical_id"), str)
                or not item.get("source_artifact_logical_id")
                or not isinstance(item.get("source_generation"), int)
                or isinstance(item.get("source_generation"), bool)
                or item["source_generation"] < 1
                or not isinstance(item.get("source_sha256"), str)
                or len(item["source_sha256"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in item["source_sha256"]
                )
                or source_pure is None
                or source_pure.is_absolute()
                or "\\" in source_path
                or Path(source_path).drive
                or any(part in {"", ".", ".."} for part in source_pure.parts)
            ):
                raise ContractError(
                    "derived Text Origin declared raster source is invalid",
                    data={
                        "first_failing_gate": "derived_text_origin_raster_source",
                        "error_code": "contract_invalid",
                    },
                )
    if len(object_ids) != len(set(object_ids)):
        raise ContractError("derived Text Origin rendered object identities are ambiguous")
    objects_by_id = {
        item["object_id"]: item for item in objects
    }
    if not isinstance(edges, list) or not edges:
        raise ContractError("derived Text Origin edges are incomplete")
    edge_ids = [item.get("edge_id") for item in edges if isinstance(item, dict)]
    if len(edge_ids) != len(edges) or len(edge_ids) != len(set(edge_ids)) or any(
        not isinstance(value, str) or not value for value in edge_ids
    ):
        raise ContractError("derived Text Origin edges are invalid")
    mapped_objects: list[str] = []
    sealed_origins: list[str] = []
    for edge in edges:
        disposition = edge.get("disposition")
        rendered_ids = edge.get("rendered_object_ids")
        if (
            disposition not in {"sealed_origin", "generated", "unexpected_addition"}
            or not isinstance(rendered_ids, list)
            or not rendered_ids
            or any(value not in object_ids for value in rendered_ids)
        ):
            raise ContractError("derived Text Origin edge is incomplete")
        mapped_objects.extend(rendered_ids)
        if disposition == "sealed_origin":
            sealed_item_id = edge.get("sealed_item_id")
            if not isinstance(sealed_item_id, str) or not isinstance(
                edge.get("sealed_text_utf8"), str
            ):
                raise ContractError("derived Text Origin sealed origin is incomplete")
            if edge.get("recipe") not in {
                "exact_utf8",
                "layout_whitespace",
                "unicode_presentation",
                "compiler_source_map",
            }:
                raise ContractError("derived Text Origin sealed origin recipe is unsupported")
            if edge.get("recipe") == "compiler_source_map":
                source_mapping = edge.get("source_mapping")
                if (
                    not isinstance(source_mapping, dict)
                    or source_mapping.get("method") != "compiler_synctex_v1"
                    or not isinstance(source_mapping.get("logical_id"), str)
                    or not isinstance(source_mapping.get("generation"), int)
                    or not isinstance(source_mapping.get("sha256"), str)
                    or not isinstance(source_mapping.get("provider"), dict)
                    or not isinstance(source_mapping.get("object_sources"), list)
                    or {
                        value.get("object_id")
                        for value in source_mapping["object_sources"]
                        if isinstance(value, dict)
                    }
                    != set(rendered_ids)
                ):
                    raise ContractError(
                        "compiler-derived source mapping is incomplete"
                    )
            sealed_origins.append(sealed_item_id)
        elif disposition == "generated":
            generator = edge.get("generator")
            generator_id = (
                generator.get("generator_id") if isinstance(generator, dict) else None
            )
            expected_generator = (
                registered_generator_identity(generator_id)
                if generator_id in REGISTERED_GENERATORS
                else None
            )
            if (
                edge.get("recipe") != "declared_generated"
                or not isinstance(generator, dict)
                or expected_generator is None
                or {key: generator.get(key) for key in expected_generator}
                != expected_generator
                or not isinstance(generator.get("inputs"), dict)
            ):
                raise ContractError("derived Text Origin generated origin is incomplete")
            source_mapping = generator.get("source_mapping")
            object_sources = (
                source_mapping.get("object_sources")
                if isinstance(source_mapping, dict)
                else None
            )
            provider = (
                source_mapping.get("provider")
                if isinstance(source_mapping, dict)
                else None
            )
            if (
                not isinstance(source_mapping, dict)
                or source_mapping.get("method") != "compiler_synctex_v1"
                or not isinstance(provider, dict)
                or not isinstance(provider.get("provider_id"), str)
                or not provider["provider_id"]
                or not isinstance(provider.get("provider_sha256"), str)
                or len(provider["provider_sha256"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in provider["provider_sha256"]
                )
                or not isinstance(object_sources, list)
                or any(
                    not isinstance(value, dict)
                    or not isinstance(value.get("object_id"), str)
                    or not isinstance(value.get("source_path"), str)
                    or not value["source_path"]
                    or not isinstance(value.get("line"), int)
                    or isinstance(value.get("line"), bool)
                    or value["line"] < 1
                    or not isinstance(value.get("column"), int)
                    or isinstance(value.get("column"), bool)
                    or not isinstance(value.get("query"), dict)
                    or not isinstance(value["query"].get("page"), int)
                    or not isinstance(value["query"].get("x"), (int, float))
                    or not isinstance(value["query"].get("y"), (int, float))
                    or value.get("object_id") not in objects_by_id
                    or value["query"].get("page")
                    != objects_by_id[value["object_id"]].get("page")
                    or value["query"].get("x")
                    != (
                        objects_by_id[value["object_id"]]["bbox"][0]
                        + objects_by_id[value["object_id"]]["bbox"][2]
                    )
                    / 2
                    or value["query"].get("y")
                    != (
                        objects_by_id[value["object_id"]]["bbox"][1]
                        + objects_by_id[value["object_id"]]["bbox"][3]
                    )
                    / 2
                    for value in object_sources
                )
                or [value.get("object_id") for value in object_sources]
                != rendered_ids
            ):
                raise ContractError(
                    "derived Text Origin generated origin is incomplete"
                )
            if generator.get("kind") == "latex_style_box_title":
                sealed_item_id = edge.get("sealed_item_id")
                sealed_item = next(
                    (
                        item
                        for item in sealed_items
                        if item.get("item_id") == sealed_item_id
                    ),
                    None,
                )
                inputs = generator.get("inputs")
                declared_titles = (
                    [
                        value
                        for value in sealed_item.get(
                            "exact_utf8_text", ""
                        ).splitlines()
                        if value
                    ]
                    if isinstance(sealed_item, dict)
                    else []
                )
                generated_titles = (
                    inputs.get("texts") if isinstance(inputs, dict) else None
                )
                source_artifact = (
                    inputs.get("source_artifact")
                    if isinstance(inputs, dict)
                    else None
                )
                if (
                    not isinstance(sealed_item_id, str)
                    or not sealed_item_id
                    or sealed_item is None
                    or sealed_item.get("representation")
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
                        "logical_id": sealed_item.get(
                            "source_artifact_logical_id"
                        ),
                        "generation": sealed_item.get("source_generation"),
                        "sha256": sealed_item.get("source_sha256"),
                    }
                ):
                    raise ContractError(
                        "derived Text Origin generated origin is incomplete"
                    )
                sealed_origins.append(sealed_item_id)
        elif edge.get("recipe") != "exact_utf8":
            raise ContractError("derived Text Origin unexpected addition recipe is unsupported")
    if sorted(mapped_objects) != sorted(object_ids) or len(mapped_objects) != len(
        set(mapped_objects)
    ):
        raise ContractError("derived Text Origin lacks exactly one disposition per object")
    sealed_item_ids = [item.get("item_id") for item in sealed_items]
    if (
        any(not isinstance(value, str) or not value for value in sealed_item_ids)
        or len(sealed_item_ids) != len(set(sealed_item_ids))
        or sorted(sealed_origins) != sorted(sealed_item_ids)
    ):
        raise ContractError("derived Text Origin lacks exactly one origin per sealed item")


class GuardedFinalCompileProvider:
    """Invoke a fingerprint-bound compiler adapter after sealed-text admission."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.registry = DeliveryQualityRegistry(self.project_root)

    def _validate_adapter_authority(self, adapter_path: Path) -> dict[str, str]:
        registered = (self.project_root / "scripts/guarded_final_compile_adapter.py").resolve()
        adapter = adapter_path.resolve()
        if adapter != registered:
            raise ContractError("registered Final Compile adapter is required")
        require_contained_path(
            adapter,
            self.project_root,
            purpose="registered Final Compile adapter",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        try:
            head = git_output(self.project_root, "rev-parse", "HEAD")
            committed_sha256 = sha256_git_blob(
                self.project_root, head, "scripts/guarded_final_compile_adapter.py"
            )
        except EvidenceSupportError as exc:
            raise ContractError(
                "registered Final Compile adapter must be tracked in current Git HEAD"
            ) from exc
        current_sha256 = sha256_file(adapter)
        if current_sha256 != committed_sha256:
            raise ContractError(
                "registered Final Compile adapter differs from current Git HEAD"
            )
        return {
            "adapter_path": str(adapter),
            "adapter_sha256": current_sha256,
            "protocol_version": "guarded-final-compile-v2",
        }

    def reconcile_interrupted(self, *, workspace_root: Path) -> dict[str, Any]:
        root = require_contained_path(
            workspace_root,
            self.project_root,
            purpose="Final Compile workspace",
            error_type=ContractError,
            leaf_kind="directory",
        )
        operation_path = root / "final-compile-operation.json"
        if not operation_path.is_file():
            raise ContractError(
                "Final Compile reconciliation requires a recorded operation",
                data={"error_code": "final_compile_operation_missing"},
            )
        operation = read_json(operation_path)
        _require_fingerprint(operation, "operation_sha256", "Final Compile operation")
        if (root / "final-compile-report.json").is_file():
            raise ContractError(
                "completed Final Compile evidence cannot be reconciled as interrupted",
                data={"error_code": "final_compile_already_completed"},
            )
        execution_path = root / "final-compile-execution.json"
        if execution_path.is_file():
            execution = read_json(execution_path)
            _require_fingerprint(
                execution, "execution_sha256", "Final Compile execution"
            )
            state = execution.get("state")
            if state == "launch_pending":
                raise ContractError(
                    "Final Compile process continuity is unknown",
                    data={"error_code": "final_compile_process_state_unknown"},
                )
            if state == "running":
                process_id = execution.get("adapter_pid")
                if not isinstance(process_id, int) or isinstance(process_id, bool):
                    raise ContractError(
                        "Final Compile running process identity is invalid",
                        data={"error_code": "final_compile_process_state_unknown"},
                    )
                try:
                    os.kill(process_id, 0)
                except OSError:
                    pass
                else:
                    raise ContractError(
                        "Final Compile process is still running",
                        data={
                            "error_code": "final_compile_process_live",
                            "adapter_pid": process_id,
                        },
                    )
            elif state not in {"succeeded", "failed", "launch_failed"}:
                raise ContractError(
                    "Final Compile execution state is invalid",
                    data={"error_code": "final_compile_process_state_unknown"},
                )
        archive = (
            root.parent
            / "待删除"
            / "final-compile-interrupted"
            / operation["operation_id"]
        ).resolve()
        require_contained_path(
            archive.parent,
            self.project_root,
            purpose="Final Compile interrupted archive parent",
            error_type=ContractError,
            leaf_kind="directory",
            allow_missing=True,
        ).mkdir(parents=True, exist_ok=True)
        if archive.exists():
            raise ContractError(
                "Final Compile interrupted archive already exists",
                data={
                    "error_code": "final_compile_reconciliation_conflict",
                    "archive_path": str(archive),
                },
            )
        root.replace(archive)
        return {
            "classification": "final_compile_interrupted_archived",
            "operation_id": operation["operation_id"],
            "archive_path": str(archive),
            "workspace_root": str(root),
        }

    def _validate_completed_replay(
        self,
        *,
        root: Path,
        report: dict[str, Any],
        operation: dict[str, Any],
    ) -> None:
        _require_fingerprint(report, "report_sha256", "Final Compile Report")
        if (
            report.get("precompile_text_seal_sha256")
            != operation["precompile_text_seal_sha256"]
            or report.get("reader_facing_text_inventory_sha256")
            != operation["reader_facing_text_inventory_sha256"]
            or report.get("compile_manifest_sha256")
            != operation["compile_manifest_sha256"]
            or report.get("compiler_provider") != operation["compile_provider"]
            or report.get("compile_adapter") != operation["compile_adapter"]
        ):
            raise ContractError("completed Final Compile replay binding is stale")

        def bound_file(relative: str, label: str) -> Path:
            path = require_contained_path(
                root / relative,
                root,
                purpose=label,
                error_type=ContractError,
                leaf_kind="file",
            )
            return path

        pdf = report["pdf"]
        pdf_path = bound_file(pdf["path"], "Final Compile PDF")
        if sha256_file(pdf_path) != pdf["sha256"] or pdf_path.stat().st_size != pdf["size"]:
            raise ContractError("completed Final Compile PDF is stale")
        recorder_path = bound_file(
            report["dependency_closure"]["recorder_path"],
            "Final Compile recorder",
        )
        if sha256_file(recorder_path) != report["dependency_closure"]["recorder_sha256"]:
            raise ContractError("completed Final Compile recorder is stale")
        final_seal = read_json(bound_file("final-artifact-seal.json", "Final Artifact Seal"))
        rendered = read_json(
            bound_file(
                "adapter-output/rendered-text-object-inventory.json",
                "Rendered Text Object Inventory",
            )
        )
        origins = read_json(bound_file("text-origin-manifest.json", "Text Origin Manifest"))
        trace = read_json(
            bound_file("adapter-output/text-origin-trace.json", "compiler Text Origin trace")
        )
        render_evidence = read_json(
            bound_file("render-evidence-manifest.json", "Render Evidence Manifest")
        )
        for value, field, label in (
            (final_seal, "seal_sha256", "Final Artifact Seal"),
            (rendered, "inventory_sha256", "Rendered Text Object Inventory"),
            (origins, "manifest_sha256", "Text Origin Manifest"),
            (render_evidence, "manifest_sha256", "Render Evidence Manifest"),
        ):
            _require_fingerprint(value, field, label)
        if (
            final_seal["seal_sha256"] != report["final_artifact_seal_sha256"]
            or rendered["inventory_sha256"] != report["rendered_text_inventory_sha256"]
            or origins["manifest_sha256"] != report["text_origin_manifest_sha256"]
            or render_evidence["manifest_sha256"]
            != report["render_evidence_manifest_sha256"]
            or trace.get("edges") != origins.get("edges")
        ):
            raise ContractError("completed Final Compile evidence graph is stale")
        for page in render_evidence["pages"]:
            page_path = bound_file(page["path"], "rendered Final Compile page")
            if sha256_file(page_path) != page["sha256"]:
                raise ContractError("completed rendered page is stale")

    def _validate_workspace_authority(
        self,
        precompile_workspace_root: Path,
        workspace_root: Path,
        runtime_policy_path: Path,
        *,
        input_track: str = "kernel",
        video_root: Path | None = None,
        compile_manifest_path: Path | None = None,
        entries: list[dict[str, Any]] | None = None,
    ) -> tuple[Path, dict[str, Any] | None]:
        if input_track == "legacy":
            if video_root is None:
                raise ContractError(
                    "Legacy Final Compile requires an explicit video root",
                    data={
                        "first_failing_gate": "legacy_final_compile_video_root",
                        "error_code": "legacy_final_compile_video_root_required",
                    },
                )
            if (
                compile_manifest_path is None
                or entries is None
            ):
                raise ContractError(
                    "Legacy Final Compile authority inputs are incomplete",
                    data={
                        "first_failing_gate": "legacy_final_compile_authority_inputs",
                        "error_code": "legacy_final_compile_authority_inputs_incomplete",
                    },
                )
            control_store_root = (self.project_root / "workspace").resolve()
            if video_root.resolve() == control_store_root:
                raise ContractError(
                    "Legacy Final Compile video root cannot be the Global Gate control root",
                    data={
                        "first_failing_gate": "global_gate_authority",
                        "error_code": "legacy_global_gate_root_forbidden",
                    },
                )
            legacy_root = require_contained_path(
                video_root,
                control_store_root,
                purpose="Legacy Final Compile video root",
                error_type=ContractError,
                leaf_kind="directory",
            )
            if (legacy_root / "workflow/run.json").exists():
                raise ContractError(
                    "Legacy Final Compile forbids a synthetic Workflow Run",
                    data={
                        "first_failing_gate": "legacy_run_record_absence",
                        "error_code": "legacy_synthetic_run_record_forbidden",
                    },
                )

            def require_legacy_path(
                path: Path,
                *,
                purpose: str,
                leaf_kind: str,
                allow_missing: bool = False,
            ) -> Path:
                try:
                    return require_contained_path(
                        path,
                        legacy_root,
                        purpose=purpose,
                        error_type=ContractError,
                        leaf_kind=leaf_kind,
                        allow_missing=allow_missing,
                    )
                except ContractError as exc:
                    raise ContractError(
                        f"{purpose} escapes the Legacy video root",
                        data={
                            "first_failing_gate": "legacy_final_compile_workspace_boundary",
                            "error_code": "legacy_final_compile_path_out_of_bounds",
                        },
                    ) from exc

            require_legacy_path(
                precompile_workspace_root,
                purpose="Legacy Precompile workspace",
                leaf_kind="directory",
            )
            require_legacy_path(
                compile_manifest_path,
                purpose="Legacy Final Compile Manifest",
                leaf_kind="file",
            )
            require_legacy_path(
                runtime_policy_path,
                purpose="Legacy Final Compile Runtime Policy",
                leaf_kind="file",
            )
            for entry in entries:
                require_legacy_path(
                    Path(entry["source_path"]),
                    purpose="Legacy Final Compile source",
                    leaf_kind="file",
                )
            root = require_legacy_path(
                workspace_root,
                purpose="Legacy Final Compile workspace",
                leaf_kind="directory",
                allow_missing=True,
            )
            from .global_gate import GlobalGatePublisher

            policy = GlobalGatePublisher(
                project_root=self.project_root
            ).check_policy(control_store_root=self.project_root / "workspace")
            return root, policy["global_gate_authority"]

        if input_track != "kernel":
            raise ContractError(
                "Final Compile input track is unsupported",
                data={
                    "first_failing_gate": "final_compile_input_track",
                    "error_code": "final_compile_input_track_unsupported",
                },
            )
        if video_root is not None:
            raise ContractError(
                "Kernel Final Compile does not accept a Legacy video root",
                data={
                    "first_failing_gate": "final_compile_input_track",
                    "error_code": "kernel_legacy_authority_forbidden",
                },
            )
        precompile = precompile_workspace_root
        run_boundary = next(
            (candidate for candidate in (precompile, *precompile.parents)
             if (candidate / "workflow/run.json").is_file()),
            None,
        )
        if run_boundary is None:
            raise ContractError("Final Compile requires a real Workflow Run")
        require_contained_path(
            precompile,
            run_boundary,
            purpose="Precompile workspace",
            error_type=ContractError,
            leaf_kind="directory",
        )
        root = require_contained_path(
            workspace_root,
            run_boundary,
            purpose="Final Compile workspace",
            error_type=ContractError,
            leaf_kind="directory",
            allow_missing=True,
        )
        from .content_production import ContentProduction
        from .kernel import VideoWorkflowKernel

        kernel = VideoWorkflowKernel(run_boundary.parent)
        authority = ContentProduction(kernel).require_current_diagnostic_compile_authority(
            run_boundary
        )
        if (
            Path(authority["runtime_policy_path"]) != runtime_policy_path.resolve()
            or authority["runtime_policy_sha256"] != sha256_file(runtime_policy_path)
        ):
            raise ContractError(
                "Final Compile requires the current diagnostic Runtime Policy"
            )
        return root, None

    def compile(
        self,
        *,
        input_track: str,
        video_root: Path | None = None,
        precompile_workspace_root: Path,
        compile_manifest_path: Path,
        compiler_adapter_path: Path,
        workspace_root: Path,
        compiled_at: str,
        runtime_policy_path: Path,
    ) -> dict[str, Any]:
        self.registry.check()
        precompile_root = precompile_workspace_root.resolve()
        seal = read_json(precompile_root / "precompile-text-seal.json")
        self.registry.validate("precompile-text-seal", seal)
        _require_fingerprint(seal, "seal_sha256", "Precompile Text Seal")
        binding_root = precompile_root / "seal-bindings" / seal["seal_sha256"]
        inventory = read_json(binding_root / "reader-facing-text-inventory.json")
        generations = read_json(binding_root / "artifact-generations.json")
        self.registry.validate("reader-facing-text-inventory", inventory)
        self.registry.validate("precompile-artifact-generation-set", generations)
        _require_fingerprint(inventory, "inventory_sha256", "sealed inventory")
        _require_fingerprint(generations, "generation_set_sha256", "sealed generations")
        if (
            seal.get("activation_status") != "target_only"
            or seal.get("inventory_sha256") != inventory["inventory_sha256"]
            or seal.get("generation_set_sha256") != generations["generation_set_sha256"]
        ):
            raise ContractError("Final Compile requires a current target-only Precompile Text Seal")

        compile_manifest = read_json(compile_manifest_path.resolve())
        self.registry.validate("final-compile-manifest", compile_manifest)
        _require_fingerprint(compile_manifest, "manifest_sha256", "Final Compile Manifest")
        if compile_manifest.get("precompile_text_seal_sha256") != seal["seal_sha256"]:
            raise ContractError("Final Compile Manifest binds another Precompile Text Seal")
        entries = compile_manifest.get("entries")
        if not isinstance(entries, list):
            raise ContractError("Final Compile Manifest entries are missing")
        entry_ids = [entry.get("logical_id") for entry in entries]
        entry_bindings = [
            (entry.get("logical_id"), entry.get("generation"), entry.get("sha256"))
            for entry in entries
        ]
        sealed_bindings = [
            (entry.get("logical_id"), entry.get("generation"), entry.get("sha256"))
            for entry in generations.get("artifacts", [])
        ]
        if (
            len(entry_ids) != len(set(entry_ids))
            or sorted(entry_bindings) != sorted(sealed_bindings)
        ):
            raise CompileDependencyGap("Final Compile Manifest lacks exact sealed input closure")
        for entry in entries:
            source_path = entry.get("source_path")
            staging_path = entry.get("staging_path")
            if (
                not isinstance(source_path, str)
                or not source_path
                or not isinstance(staging_path, str)
                or not staging_path
            ):
                raise CompileDependencyGap("Final Compile Manifest lacks source or staging paths")
            source = Path(source_path).resolve()
            if not source.is_file() or sha256_file(source) != entry.get("sha256"):
                raise CompileDependencyGap("Final Compile Manifest source identity is stale")
        approved_runtime_inputs = compile_manifest.get("approved_runtime_inputs", [])
        if not isinstance(approved_runtime_inputs, list):
            raise CompileDependencyGap("Final Compile runtime input registry is invalid")
        approved_runtime_paths: dict[str, dict[str, Any]] = {}
        for item in approved_runtime_inputs:
            if not isinstance(item, dict) or item.get("classification") not in {
                "registered_runtime_dependency",
                "registered_system_font",
            }:
                raise CompileDependencyGap("Final Compile runtime input registry is invalid")
            runtime_path = Path(item.get("path", "")).resolve()
            identity = str(runtime_path).casefold()
            if (
                not runtime_path.is_file()
                or sha256_file(runtime_path) != item.get("sha256")
                or identity in approved_runtime_paths
            ):
                raise CompileDependencyGap("Final Compile runtime input identity is stale")
            approved_runtime_paths[identity] = item

        compile_entry_by_binding = {
            (
                entry.get("logical_id"),
                entry.get("generation"),
                entry.get("sha256"),
            ): entry
            for entry in entries
        }
        for raster in inventory["items"]:
            if raster.get("representation") != "authoritative_raster_text":
                continue
            binding = (
                raster["source_artifact_logical_id"],
                raster["source_generation"],
                raster["source_sha256"],
            )
            source_entry = compile_entry_by_binding.get(binding)
            if (
                source_entry is None
            ):
                raise CompileDependencyGap(
                    "Final Compile raster source binding is stale",
                    data={
                        "first_failing_gate": "final_compile_raster_source_binding",
                        "error_code": "compile_dependency_gap",
                    },
                )
        runtime_policy = runtime_policy_path.resolve()
        require_contained_path(
            runtime_policy,
            self.project_root,
            purpose="Final Compile Runtime Policy",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        policy = read_json(runtime_policy)
        GuardedCompileProvider(self.project_root)._validate_runtime_policy(policy)
        runtime_roots = [
            Path(value).resolve() for value in policy["allowed_runtime_roots"]
        ]
        adapter_env = {"PYTHONUTF8": "1"}
        for key, value in os.environ.items():
            normalized_key = key.upper()
            if normalized_key in {"SYSTEMROOT", "WINDIR"}:
                adapter_env[normalized_key] = value
                continue
            if not normalized_key.startswith("MIKTEX_"):
                continue
            path_values = value.split(os.pathsep)
            if not path_values or any(not item for item in path_values):
                continue
            resolved_paths = [Path(item).resolve() for item in path_values]
            if all(
                any(path == root or root in path.parents for root in runtime_roots)
                for path in resolved_paths
            ):
                adapter_env[normalized_key] = value
        policy_binding = compile_manifest.get("runtime_policy")
        if (
            not isinstance(policy_binding, dict)
            or Path(str(policy_binding.get("path", ""))).resolve() != runtime_policy
            or policy_binding.get("sha256") != sha256_file(runtime_policy)
        ):
            raise ContractError("Final Compile Manifest runtime policy binding is stale")
        adapter_identity = self._validate_adapter_authority(compiler_adapter_path)
        adapter = Path(adapter_identity["adapter_path"])
        root, legacy_gate = self._validate_workspace_authority(
            precompile_workspace_root,
            workspace_root,
            runtime_policy,
            input_track=input_track,
            video_root=video_root,
            compile_manifest_path=compile_manifest_path,
            entries=entries,
        )
        operation = {
            "schema_name": "final-compile-operation",
            "schema_version": "1.0.0",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "reader_facing_text_inventory_sha256": inventory["inventory_sha256"],
            "compile_manifest_sha256": compile_manifest["manifest_sha256"],
            "runtime_policy_sha256": sha256_file(runtime_policy),
            "compile_provider": final_compile_provider_identity(self.project_root),
            "compile_adapter": adapter_identity,
        }
        operation["operation_id"] = hashlib.sha256(
            canonical_json_bytes(operation)
        ).hexdigest()[:32]
        operation["operation_sha256"] = _fingerprint_without(
            operation, "operation_sha256"
        )
        if root.exists() and any(root.iterdir()):
            existing_operation_path = root / "final-compile-operation.json"
            if existing_operation_path.is_file():
                existing_operation = read_json(existing_operation_path)
                if existing_operation == operation and (
                    root / "final-compile-report.json"
                ).is_file():
                    existing_report = read_json(root / "final-compile-report.json")
                    self._validate_completed_replay(
                        root=root,
                        report=existing_report,
                        operation=operation,
                    )
                    return {
                        "workspace_root": str(root),
                        "operation_id": operation["operation_id"],
                        "report_path": str(root / "final-compile-report.json"),
                        "report_sha256": existing_report["report_sha256"],
                        "status": "pass",
                        "replayed": True,
                    }
            raise ContractError(
                "Final Compile publication is interrupted or conflicts with this operation",
                data={
                    "error_code": "final_compile_interrupted",
                    "operation_id": operation["operation_id"],
                    "workspace_root": str(root),
                    "reconcile_command": "delivery-quality-final-compile-reconcile",
                },
            )
        root.mkdir(parents=True, exist_ok=False)
        write_json_atomic(root / "final-compile-operation.json", operation)
        adapter_output = root / "adapter-output"
        adapter_output.mkdir()
        request = {
            "schema_name": "guarded-final-compile-request",
            "schema_version": "2.0.0",
            "activation_status": "target_only",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "compile_manifest_path": str(compile_manifest_path.resolve()),
            "compile_manifest_sha256": compile_manifest["manifest_sha256"],
            "reader_facing_text_inventory_path": str(
                binding_root / "reader-facing-text-inventory.json"
            ),
            "reader_facing_text_inventory_sha256": inventory["inventory_sha256"],
            "generation_set_sha256": generations["generation_set_sha256"],
            "compile_provider": final_compile_provider_identity(self.project_root),
            "compiled_at": compiled_at,
            "output_root": str(adapter_output),
        }
        request["runtime_policy_path"] = str(runtime_policy)
        request["runtime_policy_sha256"] = sha256_file(runtime_policy)
        request_path = root / "compile-request.json"
        execution_path = root / "final-compile-execution.json"
        execution = {
            "schema_name": "final-compile-execution",
            "schema_version": "1.0.0",
            "operation_id": operation["operation_id"],
            "state": "launch_pending",
            "adapter_pid": None,
            "exit_code": None,
        }
        execution["execution_sha256"] = _fingerprint_without(
            execution, "execution_sha256"
        )
        write_json_atomic(execution_path, execution)
        request["execution_state_path"] = str(execution_path)
        request["operation_id"] = operation["operation_id"]
        write_json_atomic(request_path, request)
        try:
            process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-B", str(adapter), str(request_path)],
                cwd=self.project_root,
                text=True,
                encoding="utf-8",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=adapter_env,
            )
        except OSError:
            execution["state"] = "launch_failed"
            execution["execution_sha256"] = _fingerprint_without(
                execution, "execution_sha256"
            )
            write_json_atomic(execution_path, execution)
            raise
        stdout, stderr = process.communicate()
        execution = read_json(execution_path)
        _require_fingerprint(execution, "execution_sha256", "Final Compile execution")
        execution_pid = execution.get("adapter_pid")
        running_identity_is_valid = (
            execution.get("state") == "running"
            and isinstance(execution_pid, int)
            and not isinstance(execution_pid, bool)
        )
        adapter_failed_before_claiming_execution = (
            execution.get("state") == "launch_pending"
            and execution_pid is None
        )
        if (
            execution.get("operation_id") != operation["operation_id"]
            or not (
                running_identity_is_valid
                or adapter_failed_before_claiming_execution
            )
        ):
            raise CompileDependencyGap("Final Compile execution identity is stale")
        execution["state"] = "succeeded" if process.returncode == 0 else "failed"
        execution["exit_code"] = process.returncode
        execution["execution_sha256"] = _fingerprint_without(
            execution, "execution_sha256"
        )
        write_json_atomic(execution_path, execution)
        if process.returncode != 0 or stderr:
            raise CompileDependencyGap(
                "guarded Final Compile adapter failed",
                data={"exit_code": process.returncode},
            )

        pdf_path = adapter_output / "final.pdf"
        provenance_path = adapter_output / "compile-provenance.json"
        rendered_path = adapter_output / "rendered-text-object-inventory.json"
        trace_path = adapter_output / "text-origin-trace.json"
        final_seal_path = adapter_output / "final-artifact-seal.json"
        for path in (pdf_path, provenance_path, rendered_path, trace_path, final_seal_path):
            if not path.is_file():
                raise CompileDependencyGap("Final Compile adapter omitted required evidence")
        provenance = read_json(provenance_path)
        closure = provenance.get("dependency_closure", {})
        recorder_sha256 = closure.get("recorder_sha256")
        recorder_relative_path = closure.get("recorder_path")
        provenance_inputs = closure.get("inputs")
        provenance_runtime_inputs = closure.get("runtime_inputs")
        expected_inputs = [
            {
                "logical_id": entry.get("logical_id"),
                "generation": entry.get("generation"),
                "sha256": entry.get("sha256"),
            }
            for entry in entries
        ]
        if (
            provenance.get("compile_manifest_sha256") != compile_manifest["manifest_sha256"]
            or provenance.get("invocation", {}).get("recorder") is not True
            or closure.get("complete") is not True
            or not isinstance(recorder_sha256, str)
            or len(recorder_sha256) != 64
            or not isinstance(recorder_relative_path, str)
            or not recorder_relative_path
            or "\\" in recorder_relative_path
            or not isinstance(provenance_inputs, list)
            or len(provenance_inputs) != len(expected_inputs)
            or len({item.get("logical_id") for item in provenance_inputs if isinstance(item, dict)})
            != len(provenance_inputs)
            or sorted(provenance_inputs, key=lambda item: item.get("logical_id", ""))
            != sorted(expected_inputs, key=lambda item: item.get("logical_id", ""))
            or provenance_runtime_inputs != approved_runtime_inputs
            or provenance.get("reader_facing_text_inventory_sha256")
            != inventory["inventory_sha256"]
        ):
            raise CompileDependencyGap("Final Compile provenance is incomplete or stale")
        recorder_path = (adapter_output / recorder_relative_path).resolve()
        try:
            recorder_path.relative_to(adapter_output)
        except ValueError as exc:
            raise CompileDependencyGap("Final Compile recorder path escapes adapter output") from exc
        if not recorder_path.is_file() or sha256_file(recorder_path) != recorder_sha256:
            raise CompileDependencyGap("Final Compile recorder identity is stale")
        recorder_cwd_value = provenance.get("recorder_cwd")
        if not isinstance(recorder_cwd_value, str) or not recorder_cwd_value:
            raise CompileDependencyGap("Final Compile recorder working directory is missing")
        recorder_cwd = Path(recorder_cwd_value).resolve()
        declared_entry_paths: dict[str, dict[str, Any]] = {}
        entrypoint_paths: list[str] = []
        for entry in entries:
            staged_path = Path(entry["staging_path"])
            if staged_path.is_absolute():
                raise CompileDependencyGap("Final Compile staging path must be relative")
            staged_path = (recorder_cwd / staged_path).resolve()
            try:
                staged_path.relative_to(recorder_cwd)
            except ValueError as exc:
                raise CompileDependencyGap("Final Compile staging path escapes recorder root") from exc
            identity = str(staged_path).casefold()
            if (
                not staged_path.is_file()
                or sha256_file(staged_path) != entry.get("sha256")
                or identity in approved_runtime_paths
                or identity in declared_entry_paths
            ):
                raise CompileDependencyGap("Final Compile staged input identity is stale")
            declared_entry_paths[identity] = entry
            if staged_path.name.casefold() == "main.tex":
                entrypoint_paths.append(identity)
        if len(entrypoint_paths) != 1:
            raise CompileDependencyGap("Final Compile entrypoint is missing or ambiguous")
        declared_recorder_paths = approved_runtime_paths | declared_entry_paths
        observed_recorder_paths: list[str] = []
        generated_recorder_inputs: list[dict[str, Any]] = []
        for line in recorder_path.read_text(
            encoding="utf-8", errors="strict"
        ).splitlines():
            if not line.startswith("INPUT "):
                continue
            observed = Path(line[6:])
            if not observed.is_absolute():
                observed = recorder_cwd / observed
            observed = observed.resolve()
            identity = str(observed).casefold()
            if identity in declared_recorder_paths:
                observed_recorder_paths.append(identity)
                continue
            try:
                observed.relative_to(recorder_cwd)
                inside_recorder_root = True
            except ValueError:
                inside_recorder_root = False
            suffix = "".join(observed.suffixes[-2:]).casefold()
            if suffix not in GENERATED_RECORDER_SUFFIXES:
                suffix = observed.suffix.casefold()
            if (
                not inside_recorder_root
                or suffix not in GENERATED_RECORDER_SUFFIXES
                or not observed.is_file()
            ):
                raise CompileDependencyGap("Final Compile recorder contains undeclared input")
            generated_recorder_inputs.append(
                {
                    "path": str(observed),
                    "sha256": sha256_file(observed),
                    "classification": "attempt_generated_auxiliary",
                }
            )
        observed_recorder_path_set = set(observed_recorder_paths)
        if (
            entrypoint_paths[0] not in observed_recorder_path_set
            or not set(approved_runtime_paths).issubset(observed_recorder_path_set)
        ):
            raise CompileDependencyGap("Final Compile recorder closure is not exact")
        pdf_sha256 = sha256_file(pdf_path)
        final_seal = read_json(final_seal_path)
        self.registry.validate("final-artifact-seal", final_seal)
        _require_fingerprint(final_seal, "seal_sha256", "Final Artifact Seal")
        provider_identity = final_compile_provider_identity(self.project_root)
        if (
            final_seal.get("precompile_text_seal_sha256") != seal["seal_sha256"]
            or final_seal.get("generation_set_sha256") != generations["generation_set_sha256"]
            or final_seal.get("compile_manifest_sha256") != compile_manifest["manifest_sha256"]
            or final_seal.get("compile_provider") != provider_identity
            or final_seal.get("final_pdf")
            != {"path": "adapter-output/final.pdf", "sha256": pdf_sha256, "size": pdf_path.stat().st_size}
            or provenance.get("final_artifact_seal_sha256") != final_seal["seal_sha256"]
        ):
            raise CompileDependencyGap("Final Artifact Seal is incomplete or stale")
        rendered = read_json(rendered_path)
        if rendered.get("final_pdf_sha256") != pdf_sha256:
            raise CompileDependencyGap("Rendered Text Object Inventory binds another PDF")
        _require_fingerprint(rendered, "inventory_sha256", "Rendered Text Object Inventory")
        self.registry.validate("rendered-text-object-inventory", rendered)
        coverage = rendered["coverage"]
        if any(
            coverage.get(field) is not True
            for field in (
                "content_streams_complete",
                "annotations_complete",
                "form_xobjects_complete",
                "declared_raster_text_complete",
            )
        ):
            raise CompileDependencyGap("Rendered Text Object Inventory coverage is incomplete")
        rendered_object_ids: list[str] = []
        for item in rendered["objects"]:
            rendered_object_ids.append(item["object_id"])
            if (
                item.get("text_sha256")
                != hashlib.sha256(item["exact_utf8_text"].encode("utf-8")).hexdigest()
                or item.get("object_sha256")
                != _fingerprint_without(item, "object_sha256")
            ):
                raise CompileDependencyGap("Rendered Text Object Inventory object drifted")
        if (
            len(rendered_object_ids) != len(set(rendered_object_ids))
        ):
            raise CompileDependencyGap("Rendered Text Object Inventory object contract drifted")
        trace = read_json(trace_path)
        if (
            trace.get("schema_version") != "2.0.0"
            or trace.get("reader_facing_text_inventory_sha256")
            != inventory["inventory_sha256"]
            or trace.get("final_artifact_seal_sha256") != final_seal["seal_sha256"]
        ):
            raise CompileDependencyGap("compiler Text Origin trace is stale")

        try:
            with fitz.open(pdf_path) as document:
                pdf_page_count = document.page_count
        except Exception as exc:
            raise CompileDependencyGap("Final Compile PDF is unreadable") from exc
        derived_contract = {
            "page_count": pdf_page_count,
            "extractor_suite": rendered.get("extractor_suite"),
            "rendered_objects": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"text_sha256", "object_sha256"}
                }
                for item in rendered["objects"]
            ],
            "edges": trace.get("edges"),
            "sealed_items": [
                {
                    "item_id": item["item_id"],
                    "exact_utf8_text": item["declared_text"],
                    "representation": item.get("representation"),
                    "source_artifact_logical_id": item.get(
                        "source_artifact_logical_id"
                    ),
                    "source_generation": item.get("source_generation"),
                    "source_sha256": item.get("source_sha256"),
                }
                for item in inventory["items"]
            ],
        }
        for edge in derived_contract["edges"]:
            expected_source = None
            if edge.get("recipe") == "compiler_source_map":
                source_mapping = edge.get("source_mapping")
                if not isinstance(source_mapping, dict):
                    raise CompileDependencyGap(
                        "compiler source map evidence is incomplete"
                    )
                source_entry = compile_entry_by_binding.get(
                    (
                        source_mapping.get("logical_id"),
                        source_mapping.get("generation"),
                        source_mapping.get("sha256"),
                    )
                )
                if source_entry is None:
                    raise CompileDependencyGap(
                        "compiler source map cites an undeclared compile input"
                    )
                expected_source = (
                    recorder_cwd / Path(source_entry["staging_path"])
                ).resolve()
            elif edge.get("disposition") == "generated":
                generator = edge.get("generator")
                source_mapping = (
                    generator.get("source_mapping")
                    if isinstance(generator, dict)
                    else None
                )
                if not isinstance(source_mapping, dict):
                    raise CompileDependencyGap(
                        "generated compiler source map evidence is incomplete"
                    )
            else:
                continue
            object_sources = source_mapping.get("object_sources")
            if not isinstance(object_sources, list) or not object_sources:
                raise CompileDependencyGap(
                    "compiler source map object evidence is incomplete"
                )
            staged_sources = {
                (recorder_cwd / Path(value["staging_path"])).resolve()
                for value in compile_manifest["entries"]
            }
            if any(
                not isinstance(value, dict)
                or Path(str(value.get("source_path", ""))).resolve()
                not in staged_sources
                or (
                    expected_source is not None
                    and Path(str(value.get("source_path", ""))).resolve()
                    != expected_source
                )
                or not isinstance(value.get("line"), int)
                or isinstance(value.get("line"), bool)
                or value["line"] < 1
                or not isinstance(value.get("column"), int)
                or isinstance(value.get("column"), bool)
                or not isinstance(value.get("query"), dict)
                or not isinstance(value["query"].get("page"), int)
                or isinstance(value["query"].get("page"), bool)
                or not isinstance(value["query"].get("x"), (int, float))
                or not isinstance(value["query"].get("y"), (int, float))
                for value in object_sources
            ):
                raise CompileDependencyGap(
                    "compiler source map input identity is stale"
                )
            provider = source_mapping.get("provider", {})
            if policy["policy_id"] == "miktex-xelatex-runtime":
                tool = Path(str(provider.get("tool_path", ""))).resolve()
                if (
                    provider.get("provider_id") != "synctex-reverse-map-v1"
                    or not tool.is_file()
                    or sha256_file(tool) != provider.get("provider_sha256")
                    or not any(
                        tool == runtime_root or runtime_root in tool.parents
                        for runtime_root in runtime_roots
                    )
                ):
                    raise CompileDependencyGap(
                        "compiler source map provider identity is stale"
                    )
        try:
            _validate_derived_text_origin_evidence(derived_contract)
        except ContractError as exc:
            raise CompileDependencyGap(
                "compiler-derived Text Origin evidence is incomplete"
            ) from exc

        pages_root = adapter_output / "rendered_pages"
        pages = []
        for path in sorted(pages_root.glob("page_*.png")):
            try:
                page_number = int(path.stem.removeprefix("page_"))
            except ValueError as exc:
                raise CompileDependencyGap("Final Compile page identity is invalid") from exc
            try:
                pixmap = fitz.Pixmap(path)
                if pixmap.width < 1 or pixmap.height < 1:
                    raise ValueError("empty page image")
            except Exception as exc:
                raise CompileDependencyGap("Final Compile rendered page is unreadable") from exc
            pages.append({
                "page": page_number,
                "path": f"adapter-output/rendered_pages/{path.name}",
                "sha256": sha256_file(path),
            })
        expected_pages = list(range(1, pdf_page_count + 1))
        if (
            [item["page"] for item in pages] != expected_pages
            or rendered.get("coverage", {}).get("page_count") != pdf_page_count
            or rendered.get("coverage", {}).get("pages_scanned") != expected_pages
        ):
            raise CompileDependencyGap("Final Compile page rendering is incomplete")
        render_evidence = {
            "schema_name": "render-evidence-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "final_pdf_sha256": pdf_sha256,
            "page_count": pdf_page_count,
            "pages": pages,
        }
        render_evidence["manifest_sha256"] = _fingerprint_without(
            render_evidence, "manifest_sha256"
        )
        self.registry.validate("render-evidence-manifest", render_evidence)
        render_evidence_path = root / "render-evidence-manifest.json"
        write_json_atomic(render_evidence_path, render_evidence)

        published_final_seal_path = root / "final-artifact-seal.json"
        write_json_atomic(published_final_seal_path, final_seal)

        origins = {
            "schema_name": "text-origin-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "compiler_provider": provider_identity,
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "final_artifact_seal_sha256": final_seal["seal_sha256"],
            "rendered_text_inventory_sha256": rendered["inventory_sha256"],
            "edges": trace.get("edges", []),
        }
        origins["manifest_sha256"] = _fingerprint_without(origins, "manifest_sha256")
        self.registry.validate("text-origin-manifest", origins)
        origins_path = root / "text-origin-manifest.json"
        write_json_atomic(origins_path, origins)

        report = {
            "schema_name": "final-compile-report",
            "schema_version": "1.0.0",
            "mode": "final",
            "status": "pass",
            "delivery_authority": False,
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "final_artifact_seal_sha256": final_seal["seal_sha256"],
            "compile_manifest_sha256": compile_manifest["manifest_sha256"],
            "dependency_closure": {
                "complete": True,
                "inputs": provenance_inputs,
                "runtime_inputs": approved_runtime_inputs,
                "generated_inputs": generated_recorder_inputs,
                "recorder_sha256": recorder_sha256,
                "recorder_path": f"adapter-output/{recorder_relative_path}",
            },
            "pdf": final_seal["final_pdf"],
            "compiler_provider": provider_identity,
            "compile_adapter": adapter_identity,
            "reader_facing_text_inventory_sha256": inventory["inventory_sha256"],
            "render_evidence_manifest_sha256": render_evidence["manifest_sha256"],
            "rendered_text_inventory_sha256": rendered["inventory_sha256"],
            "text_origin_manifest_sha256": origins["manifest_sha256"],
        }
        report["report_sha256"] = _fingerprint_without(report, "report_sha256")
        self.registry.validate("final-compile-report", report)
        if legacy_gate is not None:
            from .global_gate import GlobalGatePublisher

            legacy_root = video_root.resolve() if video_root is not None else None
            if legacy_root is None or (legacy_root / "workflow/run.json").exists():
                raise ContractError(
                    "Legacy Final Compile forbids a synthetic Workflow Run",
                    data={
                        "first_failing_gate": "legacy_run_record_absence",
                        "error_code": "legacy_synthetic_run_record_forbidden",
                    },
                )
            current_policy = GlobalGatePublisher(
                project_root=self.project_root
            ).check_policy(control_store_root=self.project_root / "workspace")
            current_gate = current_policy["global_gate_authority"]
            if current_gate != legacy_gate:
                raise ContractError(
                    "Global Gate changed during Legacy Final Compile",
                    data={
                        "first_failing_gate": "global_gate_authority",
                        "error_code": "global_gate_authority_changed",
                    },
                )
        report_path = root / "final-compile-report.json"
        write_json_atomic(report_path, report)
        write_json_atomic(root / "compiler-adapter-identity.json", adapter_identity)
        return {
            "workspace_root": str(root),
            "operation_id": operation["operation_id"],
            "final_pdf_path": str(pdf_path),
            "final_artifact_seal_path": str(published_final_seal_path),
            "final_compile_report_path": str(report_path),
            "render_evidence_manifest_path": str(render_evidence_path),
            "rendered_text_inventory_path": str(rendered_path),
            "text_origin_manifest_path": str(origins_path),
            "activation_status": "target_only",
        }

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
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


def _validate_text_origin_plan(plan: dict[str, Any]) -> None:
    page_count = plan.get("page_count")
    extractors = plan.get("extractor_suite")
    objects = plan.get("rendered_objects")
    edges = plan.get("edges")
    sealed_items = plan.get("sealed_items")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        raise ContractError("Text Origin Plan page_count is invalid")
    if not isinstance(extractors, list) or not extractors:
        raise ContractError("Text Origin Plan extractor suite is incomplete")
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
        raise ContractError("Text Origin Plan extractor suite is invalid")
    if not isinstance(objects, list) or not objects:
        raise ContractError("Text Origin Plan rendered objects are incomplete")
    if not isinstance(sealed_items, list) or not sealed_items or any(
        not isinstance(item, dict) for item in sealed_items
    ):
        raise ContractError("Text Origin Plan sealed items are incomplete")
    object_ids: list[str] = []
    for item in objects:
        if not isinstance(item, dict):
            raise ContractError("Text Origin Plan rendered object is invalid")
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
            raise ContractError("Text Origin Plan rendered object is invalid")
    if len(object_ids) != len(set(object_ids)):
        raise ContractError("Text Origin Plan rendered object identities are ambiguous")
    if not isinstance(edges, list) or not edges:
        raise ContractError("Text Origin Plan origin edges are incomplete")
    edge_ids = [item.get("edge_id") for item in edges if isinstance(item, dict)]
    if len(edge_ids) != len(edges) or len(edge_ids) != len(set(edge_ids)) or any(
        not isinstance(value, str) or not value for value in edge_ids
    ):
        raise ContractError("Text Origin Plan origin edges are invalid")
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
            raise ContractError("Text Origin Plan origin edge is incomplete")
        mapped_objects.extend(rendered_ids)
        if disposition == "sealed_origin":
            sealed_item_id = edge.get("sealed_item_id")
            if not isinstance(sealed_item_id, str) or not isinstance(
                edge.get("sealed_text_utf8"), str
            ):
                raise ContractError("Text Origin Plan sealed origin is incomplete")
            if edge.get("recipe") not in {
                "exact_utf8",
                "layout_whitespace",
                "unicode_presentation",
            }:
                raise ContractError("Text Origin Plan sealed origin recipe is unsupported")
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
                raise ContractError("Text Origin Plan generated origin is incomplete")
        elif edge.get("recipe") != "exact_utf8":
            raise ContractError("Text Origin Plan unexpected addition recipe is unsupported")
    if sorted(mapped_objects) != sorted(object_ids) or len(mapped_objects) != len(
        set(mapped_objects)
    ):
        raise ContractError("Text Origin Plan lacks exactly one disposition per object")
    sealed_item_ids = [item.get("item_id") for item in sealed_items]
    if (
        any(not isinstance(value, str) or not value for value in sealed_item_ids)
        or len(sealed_item_ids) != len(set(sealed_item_ids))
        or sorted(sealed_origins) != sorted(sealed_item_ids)
    ):
        raise ContractError("Text Origin Plan lacks exactly one origin per sealed item")


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
            "protocol_version": "guarded-final-compile-v1",
        }

    def _validate_workspace_authority(
        self,
        precompile_workspace_root: Path,
        workspace_root: Path,
        runtime_policy_path: Path,
    ) -> Path:
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
        return root

    def compile(
        self,
        *,
        precompile_workspace_root: Path,
        compile_manifest_path: Path,
        text_origin_plan_path: Path,
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

        plan_path = text_origin_plan_path.resolve()
        plan = read_json(plan_path)
        _require_fingerprint(plan, "plan_sha256", "Text Origin Plan")
        if (
            plan.get("schema_name") != "text-origin-plan"
            or plan.get("schema_version") != "1.0.0"
            or plan.get("precompile_text_seal_sha256") != seal["seal_sha256"]
        ):
            raise ContractError("Text Origin Plan is stale or unsupported")
        _validate_text_origin_plan(plan)
        item_by_id = {item["item_id"]: item for item in inventory["items"]}
        planned_ids: list[str] = []
        for item in plan.get("sealed_items", []):
            item_id = item.get("item_id")
            planned_ids.append(item_id)
            sealed_item = item_by_id.get(item_id)
            text = item.get("exact_utf8_text")
            if (
                sealed_item is None
                or not isinstance(text, str)
                or hashlib.sha256(text.encode("utf-8")).hexdigest()
                != sealed_item.get("text_sha256")
            ):
                raise ContractError("Text Origin Plan does not reproduce sealed text")
        if len(planned_ids) != len(set(planned_ids)) or set(planned_ids) != set(item_by_id):
            raise ContractError("Text Origin Plan lacks complete sealed-item coverage")

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
        root = self._validate_workspace_authority(
            precompile_workspace_root, workspace_root, runtime_policy
        )
        if root.exists() and any(root.iterdir()):
            raise ContractError("Final Compile workspace must be empty")
        root.mkdir(parents=True, exist_ok=True)
        adapter_output = root / "adapter-output"
        adapter_output.mkdir()
        request = {
            "schema_name": "guarded-final-compile-request",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "compile_manifest_path": str(compile_manifest_path.resolve()),
            "compile_manifest_sha256": compile_manifest["manifest_sha256"],
            "text_origin_plan_path": str(plan_path),
            "text_origin_plan_sha256": plan["plan_sha256"],
            "generation_set_sha256": generations["generation_set_sha256"],
            "compile_provider": final_compile_provider_identity(self.project_root),
            "compiled_at": compiled_at,
            "output_root": str(adapter_output),
        }
        request["runtime_policy_path"] = str(runtime_policy)
        request["runtime_policy_sha256"] = sha256_file(runtime_policy)
        request_path = root / "compile-request.json"
        write_json_atomic(request_path, request)
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(adapter), str(request_path)],
            cwd=self.project_root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            timeout=120,
            env=adapter_env,
        )
        if completed.returncode != 0 or completed.stderr:
            raise CompileDependencyGap(
                "guarded Final Compile adapter failed",
                data={"exit_code": completed.returncode},
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
            or provenance.get("text_origin_plan_sha256") != plan["plan_sha256"]
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
        expected_recorder_paths = dict(approved_runtime_paths)
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
                or identity in expected_recorder_paths
            ):
                raise CompileDependencyGap("Final Compile staged input identity is stale")
            expected_recorder_paths[identity] = entry
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
            if identity in expected_recorder_paths:
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
        if set(observed_recorder_paths) != set(expected_recorder_paths):
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
        if rendered.get("extractor_suite") != plan["extractor_suite"]:
            raise CompileDependencyGap("Rendered Text Object Inventory extractor suite drifted")
        planned_object_ids = [item["object_id"] for item in plan["rendered_objects"]]
        rendered_object_ids: list[str] = []
        rendered_object_projection: list[dict[str, Any]] = []
        for item in rendered["objects"]:
            rendered_object_ids.append(item["object_id"])
            rendered_object_projection.append(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"text_sha256", "object_sha256"}
                }
            )
            if (
                item.get("text_sha256")
                != hashlib.sha256(item["exact_utf8_text"].encode("utf-8")).hexdigest()
                or item.get("object_sha256")
                != _fingerprint_without(item, "object_sha256")
            ):
                raise CompileDependencyGap("Rendered Text Object Inventory object drifted")
        if (
            len(rendered_object_ids) != len(set(rendered_object_ids))
            or sorted(rendered_object_ids) != sorted(planned_object_ids)
            or rendered_object_projection != plan["rendered_objects"]
        ):
            raise CompileDependencyGap("Rendered Text Object Inventory object contract drifted")
        trace = read_json(trace_path)
        if (
            trace.get("text_origin_plan_sha256") != plan["plan_sha256"]
            or trace.get("final_artifact_seal_sha256") != final_seal["seal_sha256"]
            or trace.get("edges") != plan["edges"]
        ):
            raise CompileDependencyGap("compiler Text Origin trace is stale")

        try:
            with fitz.open(pdf_path) as document:
                pdf_page_count = document.page_count
        except Exception as exc:
            raise CompileDependencyGap("Final Compile PDF is unreadable") from exc
        if pdf_page_count != plan["page_count"]:
            raise CompileDependencyGap("Final Compile PDF page count contradicts Text Origin Plan")

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
            "text_origin_plan_sha256": plan["plan_sha256"],
            "render_evidence_manifest_sha256": render_evidence["manifest_sha256"],
            "rendered_text_inventory_sha256": rendered["inventory_sha256"],
            "text_origin_manifest_sha256": origins["manifest_sha256"],
        }
        report["report_sha256"] = _fingerprint_without(report, "report_sha256")
        self.registry.validate("final-compile-report", report)
        report_path = root / "final-compile-report.json"
        write_json_atomic(report_path, report)
        write_json_atomic(root / "compiler-adapter-identity.json", adapter_identity)
        return {
            "workspace_root": str(root),
            "final_pdf_path": str(pdf_path),
            "final_artifact_seal_path": str(published_final_seal_path),
            "final_compile_report_path": str(report_path),
            "render_evidence_manifest_path": str(render_evidence_path),
            "rendered_text_inventory_path": str(rendered_path),
            "text_origin_manifest_path": str(origins_path),
            "activation_status": "target_only",
        }

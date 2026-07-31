from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from .delivery_quality import DeliveryQualityRegistry
from .errors import CompileDependencyGap, ContractError
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


class GuardedFinalCompileProvider:
    """Invoke a fingerprint-bound compiler adapter after sealed-text admission."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.registry = DeliveryQualityRegistry(self.project_root)

    def compile(
        self,
        *,
        precompile_workspace_root: Path,
        compile_manifest_path: Path,
        text_origin_plan_path: Path,
        compiler_adapter_path: Path,
        workspace_root: Path,
        compiled_at: str,
    ) -> dict[str, Any]:
        self.registry.check()
        precompile_root = precompile_workspace_root.resolve()
        seal = read_json(precompile_root / "precompile-text-seal.json")
        _require_fingerprint(seal, "seal_sha256", "Precompile Text Seal")
        binding_root = precompile_root / "seal-bindings" / seal["seal_sha256"]
        inventory = read_json(binding_root / "reader-facing-text-inventory.json")
        generations = read_json(binding_root / "artifact-generations.json")
        _require_fingerprint(inventory, "inventory_sha256", "sealed inventory")
        _require_fingerprint(generations, "generation_set_sha256", "sealed generations")
        if (
            seal.get("activation_status") != "target_only"
            or seal.get("inventory_sha256") != inventory["inventory_sha256"]
            or seal.get("generation_set_sha256") != generations["generation_set_sha256"]
        ):
            raise ContractError("Final Compile requires a current target-only Precompile Text Seal")

        compile_manifest = read_json(compile_manifest_path.resolve())
        _require_fingerprint(compile_manifest, "manifest_sha256", "Final Compile Manifest")
        if compile_manifest.get("mode") != "final":
            raise ContractError("Final Compile Manifest mode is not final")
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

        plan_path = text_origin_plan_path.resolve()
        plan = read_json(plan_path)
        _require_fingerprint(plan, "plan_sha256", "Text Origin Plan")
        if (
            plan.get("schema_name") != "text-origin-plan"
            or plan.get("schema_version") != "1.0.0"
            or plan.get("precompile_text_seal_sha256") != seal["seal_sha256"]
        ):
            raise ContractError("Text Origin Plan is stale or unsupported")
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

        adapter = compiler_adapter_path.resolve()
        require_contained_path(
            adapter,
            self.project_root,
            purpose="Final Compile adapter",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        if adapter.suffix.casefold() != ".py":
            raise ContractError("Final Compile adapter is unavailable or unsupported")
        adapter_identity = {
            "adapter_path": str(adapter),
            "adapter_sha256": sha256_file(adapter),
            "protocol_version": "guarded-final-compile-v1",
        }
        root = workspace_root.resolve()
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
            "output_root": str(adapter_output),
        }
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
        for path in (pdf_path, provenance_path, rendered_path, trace_path):
            if not path.is_file():
                raise CompileDependencyGap("Final Compile adapter omitted required evidence")
        provenance = read_json(provenance_path)
        if (
            provenance.get("compile_manifest_sha256") != compile_manifest["manifest_sha256"]
            or provenance.get("dependency_closure", {}).get("complete") is not True
            or provenance.get("text_origin_plan_sha256") != plan["plan_sha256"]
        ):
            raise CompileDependencyGap("Final Compile provenance is incomplete or stale")
        pdf_sha256 = sha256_file(pdf_path)
        rendered = read_json(rendered_path)
        if rendered.get("final_pdf_sha256") != pdf_sha256:
            raise CompileDependencyGap("Rendered Text Object Inventory binds another PDF")
        _require_fingerprint(rendered, "inventory_sha256", "Rendered Text Object Inventory")
        trace = read_json(trace_path)
        if trace.get("text_origin_plan_sha256") != plan["plan_sha256"]:
            raise CompileDependencyGap("compiler Text Origin trace is stale")

        pages_root = adapter_output / "rendered_pages"
        pages = []
        for page_number, path in enumerate(sorted(pages_root.glob("page_*.png")), start=1):
            pages.append({
                "page": page_number,
                "path": f"adapter-output/rendered_pages/{path.name}",
                "sha256": sha256_file(path),
            })
        if not pages or rendered.get("coverage", {}).get("pages_scanned") != list(
            range(1, len(pages) + 1)
        ):
            raise CompileDependencyGap("Final Compile page rendering is incomplete")
        render_evidence = {
            "schema_name": "render-evidence-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "final_pdf_sha256": pdf_sha256,
            "page_count": len(pages),
            "pages": pages,
        }
        render_evidence["manifest_sha256"] = _fingerprint_without(
            render_evidence, "manifest_sha256"
        )
        self.registry.validate("render-evidence-manifest", render_evidence)
        render_evidence_path = root / "render-evidence-manifest.json"
        write_json_atomic(render_evidence_path, render_evidence)

        provider_identity = final_compile_provider_identity(self.project_root)
        final_seal = {
            "schema_name": "final-artifact-seal",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "sealed_at": compiled_at,
            "precompile_text_seal_sha256": seal["seal_sha256"],
            "generation_set_sha256": generations["generation_set_sha256"],
            "compile_manifest_sha256": compile_manifest["manifest_sha256"],
            "compile_provider": provider_identity,
            "final_pdf": {"path": "adapter-output/final.pdf", "sha256": pdf_sha256, "size": pdf_path.stat().st_size},
        }
        final_seal["seal_sha256"] = _fingerprint_without(final_seal, "seal_sha256")
        self.registry.validate("final-artifact-seal", final_seal)
        final_seal_path = root / "final-artifact-seal.json"
        write_json_atomic(final_seal_path, final_seal)

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
            "dependency_closure": {"complete": True},
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
            "final_artifact_seal_path": str(final_seal_path),
            "final_compile_report_path": str(report_path),
            "render_evidence_manifest_path": str(render_evidence_path),
            "rendered_text_inventory_path": str(rendered_path),
            "text_origin_manifest_path": str(origins_path),
            "activation_status": "target_only",
        }

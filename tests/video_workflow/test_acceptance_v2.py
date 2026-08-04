from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from unittest import mock

from tests.video_workflow._test_run import new_case_dir
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore
from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher
import video2pdf_workflow_kernel.acceptance_v2 as acceptance_v2_module
import video2pdf_workflow_kernel.utils as utils_module
from video2pdf_workflow_kernel.acceptance_v2 import AcceptanceV2Provider
from video2pdf_workflow_kernel.errors import AcceptanceV2Rejected
from video2pdf_workflow_kernel.utils import AtomicJsonReplaceError
from scripts import issue43_exit_evidence_contract as issue43_evidence
from tests.video_workflow._issue43_git_authority import build_current_global_gate_authority


CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"


def canonical_sha(value: object) -> str:
    return hashlib.sha256(
        (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def activate_test_global_gate(control_store_root: Path) -> Path:
    repository, evidence = build_current_global_gate_authority(control_store_root)
    GlobalGatePublisher(project_root=repository).activate(
        control_store_root=control_store_root, exit_evidence=evidence, activated_at="2026-08-02T00:00:00Z"
    )
    return evidence


def run_cli(*args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *args],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    return result, json.loads(result.stdout)


class AcceptanceV2CliTests(unittest.TestCase):
    """Public seams start from a schema-valid, fingerprint-linked evidence graph."""

    def ensure_run_authority(self, root: Path) -> tuple[dict, Path, Path]:
        run_path = root / "workflow" / "run.json"
        # Each scenario owns an isolated authority graph. Sharing root.parent
        # lets the first Global Gate publication fence every later fixture.
        control_root = root / "control-store"
        if not run_path.is_file():
            record = json.loads((PROJECT_ROOT / "tests/video_workflow/fixtures/contracts/run-record.v3.valid.json").read_text(encoding="utf-8"))
            record["run_id"] = hashlib.md5(str(root).encode()).hexdigest()
            record["output_path"] = str(root.resolve())
            record["initialization_intent_id"] = f"acceptance-fixture-{record['run_id']}"
            run_path = write_json(run_path, record)
            digest = file_sha(run_path)
            store = ControlStore.initialize(control_root, ContractRegistry(PROJECT_ROOT))
            store.prepare_initialization(
                run_id=record["run_id"], output_path=root,
                intent_id=record["initialization_intent_id"],
                staging_path=control_root / "待删除" / "acceptance-run-staging" / record["run_id"],
            )
            store.bind_publication_expectations(
                record["initialization_intent_id"],
                expected_run_record_sha256=digest,
                canonical_platform=record["canonical_platform"],
                canonical_item_id=record["canonical_item_id"],
                source_identity=record["source_identity"],
                source_manifest_sha256="f" * 64,
            )
            store.transition_intent(record["initialization_intent_id"], expected_state="PREPARED", new_state="PUBLISHED", run_record_sha256=digest)
            store.transition_intent(record["initialization_intent_id"], expected_state="PUBLISHED", new_state="RECORD_COMMITTED", run_record_sha256=digest)
            store.transition_intent(record["initialization_intent_id"], expected_state="RECORD_COMMITTED", new_state="COMMITTED", run_record_sha256=digest)
        record = json.loads(run_path.read_text(encoding="utf-8"))
        return record, run_path, control_root

    def refresh_final_authority(self, binding: dict) -> None:
        final_checkpoint = binding["run"]["final_checkpoint"]
        authority_path = Path(final_checkpoint["authority_path"])
        authority = {
            "schema_name": "acceptance-v2-final-quality-authority", "schema_version": "1.0.0",
            "activation_status": "target_only", "run_id": binding["run"]["run_id"],
            "run_record_sha256": binding["run"]["run_record_sha256"],
            "acceptance_revision": binding["run"]["acceptance_revision"],
            "checkpoint": {"name": "final_quality_ready", "status": "current"},
            "artifact_generations": [
                {"logical_id": "kernel_run_record", "path": binding["run"]["run_record_path"], "sha256": binding["run"]["run_record_sha256"]},
                *[{"logical_id": f"artifact:{item['logical_id']}", "path": item["path"], "sha256": item["sha256"]} for item in binding["artifacts"]],
                *[{"logical_id": f"quality:{logical_id}", "path": item["path"], "sha256": item["sha256"]} for logical_id, item in sorted(binding["quality_inputs"].items())],
                *[{"logical_id": f"rendered_page:{item['page']}", "path": item["path"], "sha256": item["sha256"]} for item in binding["rendered_pages"]],
            ],
        }
        authority["authority_sha256"] = canonical_sha(authority)
        write_json(authority_path, authority)
        final_checkpoint["authority_sha256"] = file_sha(authority_path)

    def build_binding(self, root: Path, generation: int, *, equivalent: bool = False, publish_authority: bool = True, include_delivery_glossary: bool = False) -> Path:
        run_record, run_record_path, control_store_root = self.ensure_run_authority(root)
        artifacts = root / "artifacts"
        final_pdf = artifacts / "final.pdf"
        main_tex = artifacts / "main.tex"
        page_1 = artifacts / "page_001.png"
        page_2 = artifacts / "page_002.png"
        values = (
            (final_pdf, f"pdf-generation-{generation}".encode() if equivalent else b"pdf"),
            (main_tex, f"tex-generation-{1 if equivalent else generation}".encode()),
            (page_1, b"p1"),
            (page_2, b"p2"),
        )
        for path, data in values:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest_artifacts = [
            {"role": "pdf", "path": final_pdf.relative_to(root).as_posix()},
            {"role": "tex", "path": main_tex.relative_to(root).as_posix()},
        ]
        if include_delivery_glossary:
            glossary = root / "review" / "acceptance" / "delivery_glossary.json"
            write_json(glossary, {"schema_version": "delivery_glossary.v1", "terms": []})
            manifest_artifacts.append(
                {"role": "delivery_glossary", "path": glossary.relative_to(root).as_posix()}
            )
        allowed_manifest = write_json(
            root / "review" / "acceptance" / "allowed_artifacts_manifest.json",
            {"criteria_file": "docs/acceptance/acceptance_criteria.v1.json", "review_output_dir": "review/acceptance", "final_artifacts": manifest_artifacts, "forbidden_artifacts": []},
        )

        generation_sha = hashlib.sha256(f"generation-{generation}".encode()).hexdigest()
        fixed = hashlib.sha256(b"fixed").hexdigest()
        catalog_sha = file_sha(PROJECT_ROOT / "delivery-quality/v1/rule-catalog.v1.json")
        projections_sha = file_sha(PROJECT_ROOT / "delivery-quality/v1/role-projections.v1.json")
        projections = json.loads((PROJECT_ROOT / "delivery-quality/v1/role-projections.v1.json").read_text(encoding="utf-8"))
        writing_rules = next(item["rules"] for item in projections["projections"] if item["projection_id"] == "writing-quality-evaluation")
        precompile_provider = {
            "provider_id": "precompile-quality-provider", "provider_version": "1.0.0",
            "provider_sha256": file_sha(PROJECT_ROOT / "src/video2pdf_workflow_kernel/precompile_quality.py"),
        }
        precompile_report = {
            "schema_name": "precompile-quality-report", "schema_version": "1.0.0",
            "report_id": hashlib.md5(f"report-{generation}".encode()).hexdigest(),
            "activation_status": "target_only", "materialized_at": "2026-08-02T00:00:00Z",
            "generation_set_sha256": generation_sha, "inventory_sha256": fixed,
            "reader_text_set_sha256": file_sha(main_tex), "language_profile_id": "zh-hans",
            "delivery_glossary": None, "catalog_sha256": catalog_sha,
            "role_projections_sha256": projections_sha, "semantic_dependencies_sha256": fixed,
            "provider": precompile_provider,
            "owner_reports": [
                {"owner": owner, "task_id": hashlib.md5(f"{owner}-{generation}".encode()).hexdigest(),
                 "skeleton_sha256": fixed, "patch_sha256": fixed, "commit_sha256": fixed,
                 "reviewer": {"reviewer_id": f"independent-{owner}", "runtime_sha256": fixed,
                              "independent_from_generation_producers": True},
                 "result_count": 1, "decision": "pass"}
                for owner in ("source-faithfulness-reviewer", "writing-quality-reviewer", "pyramid-reviewer")
            ],
            "normalized_rule_results": [{
                "rule_id": rule["rule_id"], "rule_semantic_sha256": rule["rule_semantic_sha256"],
                "primary_semantic_decision_owner": "writing-quality-reviewer",
                "source_patch_sha256": fixed, "decision": "pass",
                "evidence": [{"result_key": f"{rule['rule_id']}:item-1", "decision": "pass",
                              "evidence_locator": f"artifact:{rule['rule_id']}", "violation_id": None,
                              "repair_write_set": []}],
                "violations": [], "exceptions": [],
            } for rule in writing_rules],
            "failure_set": [], "repair_routing": {}, "contract_gaps": [],
            "semantic_attempt_budget_consumed": False, "overall_decision": "pass",
        }
        precompile_report["report_sha256"] = canonical_sha(precompile_report)
        prior_seal = None
        equivalence = None
        if equivalent:
            prior_binding = json.loads((root / f"input-binding-{generation - 1}.json").read_text(encoding="utf-8"))
            precompile_report = json.loads(Path(prior_binding["quality_inputs"]["precompile_quality_report"]["path"]).read_text(encoding="utf-8"))
            prior_seal = json.loads(Path(prior_binding["quality_inputs"]["precompile_text_seal"]["path"]).read_text(encoding="utf-8"))
            equivalence = {
                "schema_name": "text-equivalence-report", "schema_version": "1.0.0", "activation_status": "target_only",
                "proved_at": "2026-08-02T00:00:30Z", "mutation_class": "presentation_only",
                "prior_seal_sha256": prior_seal["seal_sha256"], "prior_inventory_sha256": prior_seal["inventory_sha256"],
                "successor_inventory_sha256": fixed, "prior_generation_set_sha256": prior_seal["generation_set_sha256"],
                "successor_generation_set_sha256": generation_sha,
                "item_mapping": [{"prior_item_id": "item.1", "successor_item_id": "item.1"}],
                "checks": {"reader_text_equal": True}, "contract_gaps": [], "overall_decision": "equivalent",
            }
            equivalence["report_sha256"] = canonical_sha(equivalence)
        precompile_seal = {
            "schema_name": "precompile-text-seal", "schema_version": "1.0.0",
            "seal_id": hashlib.md5(f"seal-{generation}".encode()).hexdigest(),
            "activation_status": "target_only", "sealed_at": "2026-08-02T00:01:00Z",
            "decision_origin": "reused_after_text_equivalence" if equivalent else "fresh_evaluation", "generation_set_sha256": generation_sha,
            "catalog_sha256": catalog_sha, "role_projections_sha256": projections_sha,
            "language_profile_id": "zh-hans", "delivery_glossary": None,
            "semantic_dependencies_sha256": fixed, "inventory_sha256": fixed,
            "reader_text_set_sha256": file_sha(main_tex),
            "precompile_quality_report_sha256": precompile_report["report_sha256"],
            "provider": precompile_provider, "predecessor_seal_sha256": prior_seal["seal_sha256"] if prior_seal else None,
            "text_equivalence_report_sha256": equivalence["report_sha256"] if equivalence else None,
        }
        precompile_seal["seal_sha256"] = canonical_sha(precompile_seal)
        compile_provider = {
            "provider_id": "guarded-final-compile-provider",
            "provider_sha256": file_sha(PROJECT_ROOT / "src/video2pdf_workflow_kernel/final_compile.py"),
        }
        compile_manifest = {
            "schema_name": "final-compile-manifest", "schema_version": "1.0.0",
            "activation_status": "target_only", "mode": "final",
            "precompile_text_seal_sha256": precompile_seal["seal_sha256"],
            "entries": [{"logical_id": "integrated_main_tex", "generation": generation,
                         "sha256": file_sha(main_tex), "source_path": str(main_tex), "staging_path": "main.tex"}],
            "approved_runtime_inputs": [],
        }
        compile_manifest["manifest_sha256"] = canonical_sha(compile_manifest)
        final_seal = {
            "schema_name": "final-artifact-seal", "schema_version": "1.0.0",
            "activation_status": "target_only", "sealed_at": "2026-08-02T00:02:00Z",
            "precompile_text_seal_sha256": precompile_seal["seal_sha256"],
            "generation_set_sha256": generation_sha, "compile_manifest_sha256": compile_manifest["manifest_sha256"],
            "compile_provider": compile_provider,
            "final_pdf": {"path": str(final_pdf), "sha256": file_sha(final_pdf), "size": final_pdf.stat().st_size},
        }
        final_seal["seal_sha256"] = canonical_sha(final_seal)
        render_manifest = {
            "schema_name": "render-evidence-manifest", "schema_version": "1.0.0",
            "activation_status": "target_only", "final_pdf_sha256": file_sha(final_pdf), "page_count": 2,
            "pages": [{"page": 1, "path": str(page_1), "sha256": file_sha(page_1)},
                      {"page": 2, "path": str(page_2), "sha256": file_sha(page_2)}],
        }
        render_manifest["manifest_sha256"] = canonical_sha(render_manifest)
        rendered_inventory = {
            "schema_name": "rendered-text-object-inventory", "schema_version": "1.0.0",
            "activation_status": "target_only", "final_pdf_sha256": file_sha(final_pdf),
            "extractor_suite": [{"extractor_id": "pdf-text-v1", "extractor_sha256": fixed}],
            "coverage": {"page_count": 2, "pages_scanned": [1, 2], "content_streams_complete": True,
                         "annotations_complete": True, "form_xobjects_complete": True,
                         "declared_raster_text_complete": True},
            "objects": [{"object_id": "p1.text.1", "page": 1, "object_kind": "pdf_text_run",
                         "bbox": [0, 0, 1, 1], "exact_utf8_text": "example", "text_sha256": fixed,
                         "extractor_id": "pdf-text-v1", "evidence_locator": "page:1/object:1", "object_sha256": fixed}],
        }
        rendered_inventory["inventory_sha256"] = canonical_sha(rendered_inventory)
        origin_manifest = {
            "schema_name": "text-origin-manifest", "schema_version": "1.0.0", "activation_status": "target_only",
            "compiler_provider": compile_provider, "precompile_text_seal_sha256": precompile_seal["seal_sha256"],
            "final_artifact_seal_sha256": final_seal["seal_sha256"],
            "rendered_text_inventory_sha256": rendered_inventory["inventory_sha256"],
            "edges": [{"edge_id": "origin.1", "disposition": "sealed_origin", "sealed_item_id": "item.1",
                       "sealed_text_utf8": "example", "rendered_object_ids": ["p1.text.1"], "recipe": "exact_utf8"}],
        }
        origin_manifest["manifest_sha256"] = canonical_sha(origin_manifest)
        reconciliation = {
            "schema_name": "rendered-text-reconciliation-report", "schema_version": "1.0.0",
            "activation_status": "target_only", "reconciled_at": "2026-08-02T00:03:00Z",
            "provider": {"provider_id": "rendered-text-reconciliation-provider", "provider_version": "1.0.0"},
            "decision_policy": "fail_closed", "precompile_text_seal_sha256": precompile_seal["seal_sha256"],
            "final_artifact_seal_sha256": final_seal["seal_sha256"], "final_pdf_sha256": file_sha(final_pdf),
            "render_evidence_manifest_sha256": render_manifest["manifest_sha256"],
            "rendered_text_inventory_sha256": rendered_inventory["inventory_sha256"],
            "text_origin_manifest_sha256": origin_manifest["manifest_sha256"],
            "recipe_registry": {"registry_id": "rendered-text-recipes-v1", "recipes": ["exact_utf8"]},
            "input_checks": {"current": True}, "extraction_checks": {"complete": True},
            "coverage_proof": {}, "edge_results": [], "findings": [], "contract_gaps": [],
            "overall_decision": "pass", "semantic_reinterpretation_performed": False,
        }
        reconciliation["report_sha256"] = canonical_sha(reconciliation)

        quality_dir = artifacts / f"quality-generation-{generation}"
        quality_values = {
            "precompile_quality_report": precompile_report,
            "precompile_text_seal": precompile_seal,
            "final_artifact_seal": final_seal,
            "rendered_text_reconciliation": reconciliation,
            "final_compile_manifest": compile_manifest,
            "render_evidence_manifest": render_manifest,
            "rendered_text_object_inventory": rendered_inventory,
            "text_origin_manifest": origin_manifest,
        }
        if equivalence:
            quality_values["text_equivalence_report"] = equivalence
        quality_inputs = {}
        for logical_id, value in quality_values.items():
            path = write_json(quality_dir / f"{logical_id}.json", value)
            quality_inputs[logical_id] = {"path": str(path), "sha256": file_sha(path)}
        activate_test_global_gate(control_store_root)
        global_gate_authority = GlobalGatePublisher().require_current(control_store_root=control_store_root)
        binding = {
            "schema_name": "acceptance-v2-input-binding", "schema_version": "1.0.0",
            "activation_status": "target_only", "input_track": "kernel",
            "binding_id": f"final-evidence-{generation}",
            "global_gate_authority": global_gate_authority,
            "run": {"run_id": run_record["run_id"],
                    "coordination_revision": run_record["coordination_revision"], "acceptance_revision": generation, "video_root": str(root),
                    "checkpoint": {"name": "source_ready", "status": "current", "evidence_sha256": run_record["checkpoints"]["source_ready"]["evidence_sha256"]},
                    "run_record_path": str(run_record_path), "run_record_sha256": file_sha(run_record_path), "control_store_root": str(control_store_root),
                    "producer_ids": sorted({item["producer"] for item in run_record["artifact_generations"].values()}), "repairer_ids": [],
                    "predecessor_generation_set_sha256": hashlib.sha256(f"generation-{generation - 1}".encode()).hexdigest() if generation > 1 else None,
                    "changed_generation_ids": (["final_pdf"] if equivalent else ["main_tex"]) if generation > 1 else []},
            "quality_inputs": quality_inputs,
            "artifacts": [
                {"logical_id": "final_pdf", "path": str(final_pdf), "sha256": file_sha(final_pdf)},
                {"logical_id": "main_tex", "path": str(main_tex), "sha256": file_sha(main_tex)},
            ],
            "rendered_pages": [
                {"page": 1, "path": str(page_1), "sha256": file_sha(page_1)},
                {"page": 2, "path": str(page_2), "sha256": file_sha(page_2)},
            ],
        }
        final_authority_path = root / "workflow" / f"final-quality-ready.{generation}.json"
        binding["run"]["final_checkpoint"] = {
            "name": "final_quality_ready", "status": "current", "authority_path": str(final_authority_path),
            "authority_sha256": "0" * 64,
        }
        self.refresh_final_authority(binding)
        binding["binding_sha256"] = canonical_sha(binding)
        binding_path = write_json(root / f"input-binding-{generation}.json", binding)
        if publish_authority:
            published, envelope = run_cli("acceptance-final-authority-publish", "--input-binding", str(binding_path))
            self.assertEqual(0, published.returncode, published.stdout + published.stderr)
            self.assertEqual(generation, envelope["data"]["acceptance_revision"])
        return binding_path

    def prepare(self) -> tuple[Path, Path]:
        root = new_case_dir(self.id(), label="acceptance-v2")
        workspace = root / "review" / "acceptance"
        binding_path = self.build_binding(root, 1)
        completed, _ = run_cli(
            "acceptance-prepare", "--workspace-root", str(workspace), "--input-binding", str(binding_path),
            "--attempt-number", "1", "--prepared-at", "2026-08-02T00:00:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        return workspace, root

    def patch(self, workspace: Path, *, decision: str = "pass", contract_gap: bool = False,
              omit_page: bool = False, read_set: list[dict] | None = None,
              fencing_token: str | None = None, cross_findings: list[dict] | None = None) -> Path:
        skeleton = json.loads((workspace / "acceptance_report.skeleton.json").read_text(encoding="utf-8"))
        task = skeleton["dimensions"]["visual_quality"]
        binding = json.loads((workspace / "input-binding.json").read_text(encoding="utf-8"))
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        task_envelope_path = next(
            (Path(current["execution_root"]) / "tasks").glob("*/task.json")
        )
        task_envelope = json.loads(task_envelope_path.read_text(encoding="utf-8"))
        violation_by_rule = {
            "figure_visual_integrity": "figure_rendering_defect",
            "table_layout_integrity": "table_rendering_defect",
            "credibility_disclosure_rendered_placement": "disclosure_placement_inadequate",
        }
        results = [{
            "criterion_id": criterion, "decision": decision,
            "evidence": [{"artifact_logical_id": "final_pdf", "location": f"page:1:{criterion}"}],
            "required_change": "repair current artifact" if decision == "fail" else None,
            "allowed_repair_types": ["layout_repair"] if decision == "fail" else [],
            "violation_id": violation_by_rule[criterion] if decision == "fail" else None,
        } for criterion in task["criterion_ids"]]
        bound_pages = binding["rendered_pages"]["pages"] if binding.get("input_track") == "legacy" else binding["rendered_pages"]
        page_by_number = {item["page"]: item for item in bound_pages}
        pages = [{"page": page, "path": page_by_number[page]["path"], "sha256": page_by_number[page]["sha256"],
                  "decision": "pass", "evidence": [{"artifact_logical_id": f"rendered_page:{page}", "location": f"page:{page}"}]}
                 for page in skeleton["required_visual_pages"]]
        if omit_page:
            pages.pop()
        patch = {
            "schema_name": "acceptance-v2-judgment-patch", "schema_version": "1.0.0",
            "dimension": "visual_quality", "task_id": task["task_id"], "attempt_id": task["attempt_id"],
            "claim_generation": task["claim_generation"], "fencing_token": fencing_token or task["fencing_token"],
            "skeleton_sha256": skeleton["skeleton_sha256"],
            "reviewer": {"reviewer_id": "independent-visual", "independent": True},
            "actual_read_set": read_set if read_set is not None else task_envelope["authorized_read_set"],
            "criterion_results": results, "visual_scan_evidence": {"pages_checked": pages},
            "cross_phase_findings": cross_findings or [],
            "contract_gaps": [{"gap_id": "gap-1", "observation": "unmapped evidence", "evidence_location": "final.pdf:1"}] if contract_gap else [],
        }
        patch["patch_sha256"] = canonical_sha(patch)
        return write_json(Path(task_envelope["required_output"]["path"]), patch)

    def test_prepare_materializes_exact_read_only_reviewer_task_envelope(self) -> None:
        workspace, root = self.prepare()
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution_root = Path(current["execution_root"])
        task_path = next((execution_root / "tasks").glob("*/task.json"))
        task = json.loads(task_path.read_text(encoding="utf-8"))
        binding = json.loads((workspace / "input-binding.json").read_text(encoding="utf-8"))
        pages = binding["rendered_pages"]
        final_pdf = next(
            item for item in binding["artifacts"] if item["logical_id"] == "final_pdf"
        )
        role_projections = json.loads(
            (PROJECT_ROOT / "delivery-quality/v1/role-projections.v1.json").read_text(
                encoding="utf-8"
            )
        )
        visual_projection = next(
            item
            for item in role_projections["projections"]
            if item["projection_id"] == "visual-quality-evaluation"
        )
        expected = [
            {"logical_id": "final_pdf", "path": str(Path(final_pdf["path"]).resolve()), "sha256": final_pdf["sha256"]},
            *[
                {"logical_id": f"rendered_page:{item['page']}", "path": str(Path(item["path"]).resolve()), "sha256": item["sha256"]}
                for item in pages
            ],
            {"logical_id": "delivery_quality_catalog", "path": str((PROJECT_ROOT / "delivery-quality/v1/rule-catalog.v1.json").resolve()), "sha256": file_sha(PROJECT_ROOT / "delivery-quality/v1/rule-catalog.v1.json")},
            {"logical_id": "delivery_quality_role_projections", "path": str((PROJECT_ROOT / "delivery-quality/v1/role-projections.v1.json").resolve()), "sha256": file_sha(PROJECT_ROOT / "delivery-quality/v1/role-projections.v1.json")},
            {"logical_id": "role_projection:visual-quality-evaluation", "path": str((PROJECT_ROOT / visual_projection["generated_prompt"]["path"]).resolve()), "sha256": visual_projection["generated_prompt"]["sha256"]},
            {"logical_id": "acceptance_review_skeleton", "path": str((execution_root / "acceptance_report.skeleton.json").resolve()), "sha256": file_sha(execution_root / "acceptance_report.skeleton.json")},
            {"logical_id": "acceptance_input_binding", "path": str((execution_root / "input-binding.json").resolve()), "sha256": file_sha(execution_root / "input-binding.json")},
            {"logical_id": "global_gate_authority", "path": str(Path(binding["global_gate_authority"]["path"]).resolve()), "sha256": binding["global_gate_authority"]["file_sha256"]},
            {"logical_id": "allowed_artifacts_manifest", "path": str((root / "review/acceptance/allowed_artifacts_manifest.json").resolve()), "sha256": file_sha(root / "review/acceptance/allowed_artifacts_manifest.json")},
        ]
        self.assertEqual("read_only", task["input_access"])
        self.assertEqual(expected, task["authorized_read_set"])
        staged_patch = Path(task["required_output"]["path"])
        self.assertEqual(staged_patch.parent.name, task["attempt_id"])
        self.assertEqual([{"logical_id": "judgment_patch", "path": str(staged_patch)}], task["declared_write_set"])
        self.assertEqual(1, task["expected_execution_revision"])
        self.assertEqual("coordinator-session", task["coordinator_session"])
        self.assertTrue(staged_patch.parent.is_dir())
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as control:
            claim = control.execute(
                "SELECT execution_id,task_id,attempt_id,expected_execution_revision,coordinator_session,declared_write_set_json,fencing_token,task_envelope_sha256 FROM reviewer_claims WHERE task_id=?",
                (task["task_id"],),
            ).fetchone()
        self.assertEqual(
            (
                task["task_authority"]["execution_id"], task["task_id"], task["attempt_id"],
                task["expected_execution_revision"], task["coordinator_session"],
                json.dumps(task["declared_write_set"], ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                task["fencing_token"], file_sha(task_path),
            ),
            claim,
        )

    def test_claim_key_rejects_each_single_stale_authority_component(self) -> None:
        scenarios = (
            ("task_id", "f" * 32),
            ("execution_id", "f" * 32),
            ("attempt_id", "f" * 32),
            ("expected_execution_revision", 2),
            ("coordinator_session", "stale-session"),
            ("declared_write_set_json", "[]\n"),
            ("fencing_token", "f" * 64),
            ("task_envelope_sha256", "f" * 64),
        )
        for column, stale_value in scenarios:
            with self.subTest(
                scenario_id=f"stale_claim_{column}",
                target_invariant="complete_reviewer_claim_key",
                mutation_seam="after_prepare_before_patch_commit",
                rematerialized_nodes=[],
                intentionally_stale_nodes=[column],
                expected_first_gate="execution_identity",
                expected_error_code="acceptance_dimension_authority_stale",
            ):
                workspace, _ = self.prepare()
                staged = self.patch(workspace)
                with sqlite3.connect(workspace / "acceptance-control.sqlite3") as control:
                    control.execute(
                        f"UPDATE reviewer_claims SET {column}=?",
                        (stale_value,),
                    )
                completed, envelope = run_cli(
                    "acceptance-patch-commit", "--workspace-root", str(workspace),
                    "--dimension", "visual_quality", "--patch", str(staged),
                    "--committed-at", "2026-08-02T00:10:00Z",
                )
                self.assertNotEqual(0, completed.returncode)
                self.assertEqual("execution_identity", envelope["data"]["first_failing_gate"])
                self.assertEqual("acceptance_dimension_authority_stale", envelope["data"]["error_code"])

    def test_patch_commit_rejects_patch_outside_provider_created_staging_path(self) -> None:
        workspace, root = self.prepare()
        staged = self.patch(workspace)
        external = write_json(root / "external.patch.json", json.loads(staged.read_text(encoding="utf-8")))
        completed, envelope = run_cli(
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(external),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("patch_write_boundary", envelope["data"]["first_failing_gate"])
        self.assertEqual("acceptance_patch_staging_path_invalid", envelope["data"]["error_code"])

    def test_manifest_listed_delivery_glossary_is_an_exact_authorized_read(self) -> None:
        root = new_case_dir(self.id(), label="acceptance-v2-glossary")
        workspace = root / "review" / "acceptance"
        binding_path = self.build_binding(root, 1, include_delivery_glossary=True)
        completed, _ = run_cli(
            "acceptance-prepare", "--workspace-root", str(workspace),
            "--input-binding", str(binding_path), "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:00:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        task = json.loads(next((Path(current["execution_root"]) / "tasks").glob("*/task.json")).read_text(encoding="utf-8"))
        glossary = root / "review" / "acceptance" / "delivery_glossary.json"
        self.assertIn(
            {"logical_id": "delivery_glossary", "path": str(glossary.resolve()), "sha256": file_sha(glossary)},
            task["authorized_read_set"],
        )

    def test_prepare_rejects_duplicate_rendered_page_physical_path(self) -> None:
        root = new_case_dir(self.id(), label="acceptance-v2-duplicate-page-path")
        binding_path = self.build_binding(root, 1, publish_authority=False)
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["rendered_pages"][1]["path"] = binding["rendered_pages"][0]["path"]
        binding["rendered_pages"][1]["sha256"] = binding["rendered_pages"][0]["sha256"]
        render_manifest_path = Path(binding["quality_inputs"]["render_evidence_manifest"]["path"])
        render_manifest = json.loads(render_manifest_path.read_text(encoding="utf-8"))
        render_manifest["pages"] = [dict(item) for item in binding["rendered_pages"]]
        render_manifest["manifest_sha256"] = canonical_sha(
            {key: value for key, value in render_manifest.items() if key != "manifest_sha256"}
        )
        write_json(render_manifest_path, render_manifest)
        binding["quality_inputs"]["render_evidence_manifest"]["sha256"] = file_sha(render_manifest_path)
        reconciliation_path = Path(binding["quality_inputs"]["rendered_text_reconciliation"]["path"])
        reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
        reconciliation["render_evidence_manifest_sha256"] = render_manifest["manifest_sha256"]
        reconciliation["report_sha256"] = canonical_sha(
            {key: value for key, value in reconciliation.items() if key != "report_sha256"}
        )
        write_json(reconciliation_path, reconciliation)
        binding["quality_inputs"]["rendered_text_reconciliation"]["sha256"] = file_sha(reconciliation_path)
        self.refresh_final_authority(binding)
        binding["binding_sha256"] = canonical_sha({key: value for key, value in binding.items() if key != "binding_sha256"})
        write_json(binding_path, binding)
        published, _ = run_cli("acceptance-final-authority-publish", "--input-binding", str(binding_path))
        self.assertEqual(0, published.returncode)
        completed, envelope = run_cli(
            "acceptance-prepare", "--workspace-root", str(root / "review/acceptance"),
            "--input-binding", str(binding_path), "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:00:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("allowed_read_set", envelope["data"]["first_failing_gate"])
        self.assertEqual("acceptance_read_set_duplicate_path", envelope["data"]["error_code"])

    @unittest.skipUnless(os.name == "nt", "Windows case aliases are platform-specific")
    def test_prepare_rejects_case_alias_for_same_rendered_page_physical_path(self) -> None:
        root = new_case_dir(self.id(), label="acceptance-v2-case-alias-page-path")
        binding_path = self.build_binding(root, 1, publish_authority=False)
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        aliased_path = binding["rendered_pages"][0]["path"].swapcase()
        if not Path(aliased_path).is_file():
            self.skipTest("test volume treats the case alias as a distinct path")
        binding["rendered_pages"][1]["path"] = aliased_path
        binding["rendered_pages"][1]["sha256"] = binding["rendered_pages"][0]["sha256"]
        self._publish_duplicate_rendered_page_binding(binding_path, binding)
        completed, envelope = run_cli(
            "acceptance-prepare", "--workspace-root", str(root / "review/acceptance"),
            "--input-binding", str(binding_path), "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:00:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("allowed_read_set", envelope["data"]["first_failing_gate"])
        self.assertEqual("acceptance_read_set_duplicate_path", envelope["data"]["error_code"])

    def test_prepare_rejects_hardlink_alias_for_same_rendered_page_physical_file(self) -> None:
        root = new_case_dir(self.id(), label="acceptance-v2-hardlink-alias-page-path")
        binding_path = self.build_binding(root, 1, publish_authority=False)
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        source = Path(binding["rendered_pages"][0]["path"])
        alias = source.with_name("page-hardlink-alias.png")
        try:
            alias.hardlink_to(source)
        except OSError as exc:
            self.skipTest(f"hard links are unavailable on the test filesystem: {exc}")
        binding["rendered_pages"][1]["path"] = str(alias)
        binding["rendered_pages"][1]["sha256"] = binding["rendered_pages"][0]["sha256"]
        self._publish_duplicate_rendered_page_binding(binding_path, binding)
        completed, envelope = run_cli(
            "acceptance-prepare", "--workspace-root", str(root / "review/acceptance"),
            "--input-binding", str(binding_path), "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:00:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("allowed_read_set", envelope["data"]["first_failing_gate"])
        self.assertEqual("acceptance_read_set_duplicate_path", envelope["data"]["error_code"])

    def test_patch_commit_rejects_missing_authorized_read_path_fail_closed(self) -> None:
        workspace, root = self.prepare()
        patch = self.patch(workspace)
        binding = json.loads((workspace / "input-binding.json").read_text(encoding="utf-8"))
        rendered_page = Path(binding["rendered_pages"][0]["path"])
        staged_for_deletion = root / "待删除" / rendered_page.name
        staged_for_deletion.parent.mkdir(parents=True, exist_ok=True)
        rendered_page.replace(staged_for_deletion)
        completed, envelope = run_cli(
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("allowed_read_set", envelope["data"]["first_failing_gate"])
        self.assertEqual("acceptance_read_set_path_invalid", envelope["data"]["error_code"])

    def _publish_duplicate_rendered_page_binding(self, binding_path: Path, binding: dict) -> None:
        render_manifest_path = Path(binding["quality_inputs"]["render_evidence_manifest"]["path"])
        render_manifest = json.loads(render_manifest_path.read_text(encoding="utf-8"))
        render_manifest["pages"] = [dict(item) for item in binding["rendered_pages"]]
        render_manifest["manifest_sha256"] = canonical_sha(
            {key: value for key, value in render_manifest.items() if key != "manifest_sha256"}
        )
        write_json(render_manifest_path, render_manifest)
        binding["quality_inputs"]["render_evidence_manifest"]["sha256"] = file_sha(render_manifest_path)
        reconciliation_path = Path(binding["quality_inputs"]["rendered_text_reconciliation"]["path"])
        reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
        reconciliation["render_evidence_manifest_sha256"] = render_manifest["manifest_sha256"]
        reconciliation["report_sha256"] = canonical_sha(
            {key: value for key, value in reconciliation.items() if key != "report_sha256"}
        )
        write_json(reconciliation_path, reconciliation)
        binding["quality_inputs"]["rendered_text_reconciliation"]["sha256"] = file_sha(reconciliation_path)
        self.refresh_final_authority(binding)
        binding["binding_sha256"] = canonical_sha(
            {key: value for key, value in binding.items() if key != "binding_sha256"}
        )
        write_json(binding_path, binding)
        published, envelope = run_cli(
            "acceptance-final-authority-publish", "--input-binding", str(binding_path)
        )
        self.assertEqual(0, published.returncode, published.stdout + published.stderr)

    def test_patch_read_set_rejects_each_single_contradiction_at_allowed_read_set_gate(self) -> None:
        scenarios = (
            "omitted_path",
            "stale_path",
            "extra_path",
            "stale_sha",
            "duplicate_logical_id",
        )
        for scenario_id in scenarios:
            with self.subTest(
                scenario_id=scenario_id,
                target_invariant="task_envelope_exact_path_sha_read_set",
                mutation_seam="after_prepare_before_patch_commit",
                rematerialized_nodes=["patch_sha256"],
                intentionally_stale_nodes=[scenario_id],
                expected_first_gate="allowed_read_set",
                expected_error_code="acceptance_read_set_incomplete",
            ):
                workspace, root = self.prepare()
                current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
                task = json.loads(next((Path(current["execution_root"]) / "tasks").glob("*/task.json")).read_text(encoding="utf-8"))
                reads = [dict(item) for item in task["authorized_read_set"]]
                if scenario_id == "omitted_path":
                    reads.pop()
                elif scenario_id == "stale_path":
                    reads[0]["path"] = str((root / "artifacts" / "stale-final.pdf").resolve())
                elif scenario_id == "extra_path":
                    reads.append({"logical_id": "forbidden_generation_notes", "path": str((root / "generation-notes.md").resolve()), "sha256": "f" * 64})
                elif scenario_id == "stale_sha":
                    reads[0]["sha256"] = "f" * 64
                else:
                    duplicate = dict(reads[0])
                    duplicate["path"] = str((root / "artifacts" / "duplicate.pdf").resolve())
                    reads.append(duplicate)
                patch = self.patch(workspace, read_set=reads)
                completed, envelope = run_cli(
                    "acceptance-patch-commit", "--workspace-root", str(workspace),
                    "--dimension", "visual_quality", "--patch", str(patch),
                    "--committed-at", "2026-08-02T00:10:00Z",
                )
                self.assertNotEqual(0, completed.returncode)
                self.assertEqual("allowed_read_set", envelope["data"]["first_failing_gate"])
                self.assertEqual("acceptance_read_set_incomplete", envelope["data"]["error_code"])

    def test_patch_read_set_order_has_no_authority_semantics(self) -> None:
        workspace, _ = self.prepare()
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        task = json.loads(
            next((Path(current["execution_root"]) / "tasks").glob("*/task.json")).read_text(
                encoding="utf-8"
            )
        )
        patch = self.patch(
            workspace,
            read_set=list(reversed(task["authorized_read_set"])),
        )
        completed, envelope = run_cli(
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        self.assertEqual(0, completed.returncode, envelope)

    def commit_visual(self, workspace: Path, **kwargs: object) -> None:
        patch = self.patch(workspace, **kwargs)
        completed, _ = run_cli(
            "acceptance-patch-commit", "--workspace-root", str(workspace), "--dimension", "visual_quality",
            "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z",
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def materialize(self, workspace: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
        return run_cli(
            "acceptance-materialize", "--workspace-root", str(workspace),
            "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
            "--materialized-at", "2026-08-02T00:20:00Z",
        )

    def reconcile_while_writer_waits_on_abort(
        self,
        workspace: Path,
        intent_path: Path,
        writer_arguments: tuple[str, ...],
    ) -> tuple[dict, subprocess.CompletedProcess[str], dict]:
        entered_abort_write = threading.Event()
        release_abort_write = threading.Event()
        original_write = acceptance_v2_module.write_json_atomic

        def blocked_abort_write(path: Path, value: object) -> str:
            if (
                path.resolve() == intent_path.resolve()
                and isinstance(value, dict)
                and value.get("state") == "ABORTED"
                and not entered_abort_write.is_set()
            ):
                entered_abort_write.set()
                if not release_abort_write.wait(timeout=10):
                    raise TimeoutError("reconcile abort-write barrier was not released")
            return original_write(path, value)

        acceptance_v2_module.write_json_atomic = blocked_abort_write
        initial_mtime = intent_path.stat().st_mtime_ns
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                reconcile_future = pool.submit(
                    AcceptanceV2Provider(PROJECT_ROOT).reconcile,
                    workspace_root=workspace,
                )
                self.assertTrue(entered_abort_write.wait(timeout=10))
                writer_future = pool.submit(run_cli, *writer_arguments)
                deadline = time.monotonic() + 10
                while intent_path.stat().st_mtime_ns == initial_mtime:
                    if time.monotonic() >= deadline:
                        self.fail("writer did not republish PREPARED intent before the barrier deadline")
                    time.sleep(0.01)
                release_abort_write.set()
                recovery = reconcile_future.result(timeout=30)
                writer_completed, writer_envelope = writer_future.result(timeout=30)
        finally:
            release_abort_write.set()
            acceptance_v2_module.write_json_atomic = original_write
        return recovery, writer_completed, writer_envelope

    def writer_wins_before_waiting_reconcile(
        self,
        workspace: Path,
        intent_path: Path,
        writer_arguments: tuple[str, ...],
    ) -> tuple[subprocess.CompletedProcess[str], dict, dict]:
        blocker = sqlite3.connect(
            workspace / "acceptance-control.sqlite3",
            timeout=30,
            isolation_level=None,
        )
        blocker.execute("BEGIN IMMEDIATE")
        initial_mtime = intent_path.stat().st_mtime_ns
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                writer_future = pool.submit(run_cli, *writer_arguments)
                deadline = time.monotonic() + 10
                while intent_path.stat().st_mtime_ns == initial_mtime:
                    if time.monotonic() >= deadline:
                        self.fail("writer did not reach its first-CAS barrier")
                    time.sleep(0.01)
                reconcile_future = pool.submit(
                    AcceptanceV2Provider(PROJECT_ROOT).reconcile,
                    workspace_root=workspace,
                )
                time.sleep(0.2)
                blocker.execute("COMMIT")
                writer_completed, writer_envelope = writer_future.result(timeout=30)
                recovery = reconcile_future.result(timeout=30)
        finally:
            try:
                blocker.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            blocker.close()
        return writer_completed, writer_envelope, recovery

    def reconcile_then_two_writers_compete_at_reauthorization(
        self,
        workspace: Path,
        intent_path: Path,
        first_writer: Callable[[], dict],
        second_writer_arguments: tuple[str, ...],
    ) -> tuple[dict, AcceptanceV2Rejected, subprocess.CompletedProcess[str], dict]:
        reconcile_abort_entered = threading.Event()
        release_reconcile_abort = threading.Event()
        first_writer_abort_entered = threading.Event()
        release_first_writer_abort = threading.Event()
        abort_write_lock = threading.Lock()
        abort_write_count = 0
        original_write = acceptance_v2_module.write_json_atomic

        def blocked_abort_write(path: Path, value: object) -> str:
            nonlocal abort_write_count
            if (
                path.resolve() == intent_path.resolve()
                and isinstance(value, dict)
                and value.get("state") == "ABORTED"
            ):
                with abort_write_lock:
                    abort_write_count += 1
                    current_abort_write = abort_write_count
                if current_abort_write == 1:
                    reconcile_abort_entered.set()
                    if not release_reconcile_abort.wait(timeout=10):
                        raise TimeoutError("reconcile abort-write barrier was not released")
                elif current_abort_write == 2:
                    first_writer_abort_entered.set()
                    if not release_first_writer_abort.wait(timeout=10):
                        raise TimeoutError("first-writer abort-write barrier was not released")
            return original_write(path, value)

        acceptance_v2_module.write_json_atomic = blocked_abort_write
        initial_mtime = intent_path.stat().st_mtime_ns
        intent_id = json.loads(intent_path.read_text(encoding="utf-8"))["intent_id"]
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                reconcile_future = pool.submit(
                    AcceptanceV2Provider(PROJECT_ROOT).reconcile,
                    workspace_root=workspace,
                )
                self.assertTrue(reconcile_abort_entered.wait(timeout=10))
                first_writer_future = pool.submit(first_writer)
                deadline = time.monotonic() + 10
                while intent_path.stat().st_mtime_ns == initial_mtime:
                    if time.monotonic() >= deadline:
                        self.fail("first writer did not reach its reauthorization barrier")
                    time.sleep(0.01)
                release_reconcile_abort.set()
                recovery = reconcile_future.result(timeout=30)
                self.assertTrue(first_writer_abort_entered.wait(timeout=10))

                before_second_writer = intent_path.stat().st_mtime_ns
                second_writer_future = pool.submit(run_cli, *second_writer_arguments)
                deadline = time.monotonic() + 10
                while (
                    intent_path.stat().st_mtime_ns == before_second_writer
                    or json.loads(intent_path.read_text(encoding="utf-8"))["state"] != "PREPARED"
                ):
                    if time.monotonic() >= deadline:
                        self.fail("second writer did not reach its first-CAS barrier")
                    time.sleep(0.01)
                control_deadline = time.monotonic() + 5
                while time.monotonic() < control_deadline and not second_writer_future.done():
                    with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
                        controlled = database.execute(
                            "SELECT state FROM publication_intents WHERE intent_id=?",
                            (intent_id,),
                        ).fetchone()
                    if controlled is not None:
                        break
                    time.sleep(0.01)
                release_first_writer_abort.set()
                try:
                    first_writer_future.result(timeout=30)
                except AcceptanceV2Rejected as error:
                    first_writer_error = error
                else:
                    self.fail("first writer unexpectedly passed stale reauthorization")
                second_writer_completed, second_writer_envelope = second_writer_future.result(timeout=30)
        finally:
            release_reconcile_abort.set()
            release_first_writer_abort.set()
            acceptance_v2_module.write_json_atomic = original_write
        return recovery, first_writer_error, second_writer_completed, second_writer_envelope

    def test_complete_current_evidence_materializes_all_catalog_rules_and_guard_eligibility(self) -> None:
        workspace, _ = self.prepare()
        skeleton = json.loads((workspace / "acceptance_report.skeleton.json").read_text(encoding="utf-8"))
        self.assertEqual({"visual_quality"}, set(skeleton["dimensions"]))
        self.commit_visual(workspace)
        completed, envelope = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        report = json.loads((workspace / "acceptance_report.json").read_text(encoding="utf-8"))
        self.assertEqual(9, len(report["criterion_results"]))
        self.assertEqual(3, len(report["precompile_owner_reports"]))
        guarded, guard = run_cli("acceptance-guard-eligibility", "--workspace-root", str(workspace))
        self.assertEqual(0, guarded.returncode, guarded.stderr)
        self.assertTrue(guard["data"]["eligible"])
        self.assertTrue(guard["data"]["delivery_authority"])

    def test_visual_patch_missing_page_fails_at_visual_page_coverage_gate(self) -> None:
        workspace, _ = self.prepare()
        patch = self.patch(workspace, omit_page=True)
        completed, envelope = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("visual_page_coverage", envelope["data"]["first_failing_gate"])

    def test_incomplete_read_set_and_stale_fencing_token_fail_closed(self) -> None:
        workspace, _ = self.prepare()
        binding = json.loads((workspace / "input-binding.json").read_text(encoding="utf-8"))
        final_pdf = next(item for item in binding["artifacts"] if item["logical_id"] == "final_pdf")
        first_page = binding["rendered_pages"][0]
        patch = self.patch(workspace, read_set=[
            final_pdf,
            {"logical_id": "rendered_page:1", "path": first_page["path"], "sha256": first_page["sha256"]},
        ])
        completed, envelope = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("allowed_read_set", envelope["data"]["first_failing_gate"])
        patch = self.patch(workspace, fencing_token="0" * 64)
        completed, envelope = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("patch_fencing", envelope["data"]["first_failing_gate"])

    def test_page_fingerprint_and_reviewer_identity_are_authority_bound(self) -> None:
        workspace, _ = self.prepare()
        patch_path = self.patch(workspace)
        patch = json.loads(patch_path.read_text(encoding="utf-8"))
        patch["visual_scan_evidence"]["pages_checked"][0]["sha256"] = "0" * 64
        patch["patch_sha256"] = canonical_sha({key: value for key, value in patch.items() if key != "patch_sha256"})
        write_json(patch_path, patch)
        completed, envelope = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch_path), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("visual_page_coverage", envelope["data"]["first_failing_gate"])

        patch_path = self.patch(workspace)
        patch = json.loads(patch_path.read_text(encoding="utf-8"))
        binding = json.loads((workspace / "input-binding.json").read_text(encoding="utf-8"))
        patch["reviewer"]["reviewer_id"] = binding["run"]["producer_ids"][0]
        patch["patch_sha256"] = canonical_sha({key: value for key, value in patch.items() if key != "patch_sha256"})
        write_json(patch_path, patch)
        completed, envelope = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch_path), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("reviewer_independence", envelope["data"]["first_failing_gate"])

    def test_successful_patch_and_terminal_materialization_retries_are_idempotent(self) -> None:
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        first, _ = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertEqual(0, first.returncode, first.stderr)
        retried, retry = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:11:00Z")
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertTrue(retry["data"]["idempotent"])
        first_report, _ = self.materialize(workspace)
        self.assertEqual(0, first_report.returncode, first_report.stderr)
        retried_report, retry = self.materialize(workspace)
        self.assertEqual(0, retried_report.returncode, retried_report.stderr)
        self.assertTrue(retry["data"]["idempotent"])
        self.assertEqual(1, len(list((workspace / "executions").glob("*/reports/*/acceptance_report.json"))))

    def test_idempotent_retries_reject_drifted_committed_bytes(self) -> None:
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        first, _ = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertEqual(0, first.returncode, first.stderr)
        execution = json.loads((workspace / "execution.json").read_text(encoding="utf-8"))
        committed_patch = Path(execution["committed_patches"]["visual_quality"]["path"])
        drifted = json.loads(committed_patch.read_text(encoding="utf-8"))
        drifted["reviewer"]["reviewer_id"] = "drifted-reviewer"
        write_json(committed_patch, drifted)
        retried, envelope = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:11:00Z")
        self.assertNotEqual(0, retried.returncode)
        self.assertEqual("patch_freshness", envelope["data"]["first_failing_gate"])

        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        completed, _ = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution = json.loads((workspace / "execution.json").read_text(encoding="utf-8"))
        immutable_report = Path(execution["report_publication"]["path"])
        report = json.loads(immutable_report.read_text(encoding="utf-8"))
        report["routing_state"] = "repair_required"
        write_json(immutable_report, report)
        retried, envelope = self.materialize(workspace)
        self.assertNotEqual(0, retried.returncode)
        self.assertEqual("report_freshness", envelope["data"]["first_failing_gate"])

    def test_materialization_rejects_post_commit_patch_authority_substitution(self) -> None:
        workspace, root = self.prepare()
        self.commit_visual(workspace)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution_paths = [workspace / "execution.json", Path(current["execution_root"]) / "execution.json"]
        execution = json.loads(execution_paths[0].read_text(encoding="utf-8"))
        substitute_patch_path = self.patch(workspace, decision="fail")
        substitute_patch = json.loads(substitute_patch_path.read_text(encoding="utf-8"))
        substitute = Path(current["execution_root"]) / "committed" / "visual_quality" / "substitute" / "judgment-patch.json"
        write_json(substitute, substitute_patch)
        record = execution["committed_patches"]["visual_quality"]
        record.update({
            "patch_sha256": substitute_patch["patch_sha256"], "file_sha256": file_sha(substitute),
            "path": str(substitute), "intent_id": hashlib.md5(b"substitute").hexdigest(),
        })
        execution["execution_sha256"] = canonical_sha({key: value for key, value in execution.items() if key != "execution_sha256"})
        for path in execution_paths:
            write_json(path, execution)
        completed, envelope = self.materialize(workspace)
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("patch_freshness", envelope["data"]["first_failing_gate"])
        guarded, guard = run_cli("acceptance-guard-eligibility", "--workspace-root", str(workspace))
        self.assertEqual(0, guarded.returncode, guarded.stderr)
        self.assertFalse(guard["data"]["mechanical_checks"]["visual_patch_authority_current"])

    def test_patch_exact_retry_rejects_tampered_committed_intent_authority(self) -> None:
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        first, _ = run_cli(
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        self.assertEqual(0, first.returncode, first.stderr)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("patch-*.json"))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        intent["state"] = "ABORTED"
        write_json(intent_path, intent)
        retried, envelope = run_cli(
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:11:00Z",
        )
        self.assertNotEqual(0, retried.returncode)
        self.assertEqual("patch_freshness", envelope["data"]["first_failing_gate"])

    def test_two_writers_are_fenced_at_patch_and_report_publication(self) -> None:
        workspace, _ = self.prepare()
        staged_path = self.patch(workspace)
        def commit(_: int) -> tuple[subprocess.CompletedProcess[str], dict]:
            return run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
                "--dimension", "visual_quality", "--patch", str(staged_path), "--committed-at", "2026-08-02T00:10:00Z")
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(commit, (1, 2)))
        successful_patches = [
            envelope["data"] for completed, envelope in outcomes
            if completed.returncode == 0
        ]
        self.assertIn(len(successful_patches), {1, 2})
        if len(successful_patches) == 2:
            self.assertEqual(
                [False, True],
                sorted(item["idempotent"] for item in successful_patches),
            )
        else:
            self.assertFalse(successful_patches[0]["idempotent"])
            loser = next(
                envelope for completed, envelope in outcomes
                if completed.returncode != 0
            )
            self.assertIn("first_failing_gate", loser["data"], loser)
            self.assertEqual("patch_fencing", loser["data"]["first_failing_gate"], loser)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution = json.loads((workspace / "execution.json").read_text(encoding="utf-8"))
        committed_path = Path(execution["committed_patches"]["visual_quality"]["path"])
        self.assertTrue(committed_path.is_relative_to(Path(current["execution_root"]) / "committed"))

        def materialize(at: str) -> tuple[subprocess.CompletedProcess[str], dict]:
            return run_cli("acceptance-materialize", "--workspace-root", str(workspace),
                "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0", "--materialized-at", at)
        with ThreadPoolExecutor(max_workers=2) as pool:
            reports = list(pool.map(materialize, ("2026-08-02T00:20:00Z", "2026-08-02T00:21:00Z")))
        successful = [envelope["data"] for completed, envelope in reports if completed.returncode == 0]
        self.assertIn(len(successful), {1, 2})
        immutable_reports = list((workspace / "executions").glob("*/reports/*/acceptance_report.json"))
        self.assertEqual(1, len(immutable_reports))
        committed = json.loads((workspace / "acceptance_report.json").read_text(encoding="utf-8"))
        self.assertTrue(all(item["report_path"] == str(workspace / "acceptance_report.json") for item in successful))
        self.assertTrue(all(item["report_sha256"] == committed["report_sha256"] for item in successful))
        if len(successful) == 2:
            self.assertEqual([False, True], sorted(item["idempotent"] for item in successful))
        else:
            self.assertFalse(successful[0]["idempotent"])
            loser = next(envelope for completed, envelope in reports if completed.returncode != 0)
            self.assertEqual("report_fencing", loser["data"]["first_failing_gate"])
        pending = [json.loads(path.read_text(encoding="utf-8")) for path in (Path(current["execution_root"]) / "intents").glob("*.json")]
        self.assertFalse(any(item["state"] == "PREPARED" for item in pending))

    def test_exact_competing_patch_publish_survives_windows_replace_denial(self) -> None:
        workspace, _ = self.prepare()
        staged_path = self.patch(workspace)
        original_write = acceptance_v2_module.write_json_atomic
        denial_injected = False

        def publish_then_deny(path: Path, value: object) -> str:
            nonlocal denial_injected
            if (
                not denial_injected
                and path.name == "judgment-patch.json"
                and "committed" in path.parts
            ):
                denial_injected = True
                original_write(path, value)
                denial = PermissionError(13, "simulated Windows destination sharing denial")
                denial.winerror = 5
                raise AtomicJsonReplaceError(
                    path=path,
                    temp_path=path.with_name(".simulated.kernel-new"),
                    original_error=denial,
                    platform="nt",
                )
            return original_write(path, value)

        acceptance_v2_module.write_json_atomic = publish_then_deny
        try:
            result = AcceptanceV2Provider(PROJECT_ROOT).commit_patch(
                workspace_root=workspace,
                dimension="visual_quality",
                patch_path=staged_path,
                committed_at="2026-08-02T00:10:00Z",
            )
        finally:
            acceptance_v2_module.write_json_atomic = original_write

        self.assertTrue(denial_injected)
        self.assertFalse(result["idempotent"])
        execution = json.loads((workspace / "execution.json").read_text(encoding="utf-8"))
        committed = execution["committed_patches"]["visual_quality"]
        self.assertEqual(result["patch_sha256"], committed["patch_sha256"])

    def test_competing_prepared_write_rejects_wrong_stage_platform_code_and_content(self) -> None:
        root = new_case_dir("acceptance-atomic-stage")
        path = write_json(root / "intent.json", {"intent_id": "same", "state": "PREPARED"})
        expected = {"intent_id": "same", "state": "PREPARED"}

        with mock.patch.object(
            acceptance_v2_module,
            "write_json_atomic",
            side_effect=PermissionError(13, "pre-replace write denied"),
        ):
            with self.assertRaises(PermissionError):
                acceptance_v2_module._write_competing_prepared_json(path, expected)

        def structured(*, platform: str, winerror: int) -> AtomicJsonReplaceError:
            original = PermissionError(13, "replace denied")
            original.winerror = winerror
            return AtomicJsonReplaceError(
                path=path,
                temp_path=path.with_name(".simulated.kernel-new"),
                original_error=original,
                platform=platform,
            )

        for error in (
            structured(platform="posix", winerror=5),
            structured(platform="nt", winerror=87),
        ):
            with self.subTest(platform=error.platform, winerror=error.original_error.winerror):
                with mock.patch.object(
                    acceptance_v2_module, "write_json_atomic", side_effect=error,
                ):
                    with self.assertRaises(AtomicJsonReplaceError):
                        acceptance_v2_module._write_competing_prepared_json(path, expected)

        write_json(path, {"intent_id": "different", "state": "PREPARED"})
        mismatch = structured(platform="nt", winerror=32)
        with mock.patch.object(
            acceptance_v2_module, "write_json_atomic", side_effect=mismatch,
        ):
            with self.assertRaises(AtomicJsonReplaceError):
                acceptance_v2_module._write_competing_prepared_json(path, expected)

        unreadable = structured(platform="nt", winerror=33)
        missing_path = root / "missing-intent.json"
        with mock.patch.object(
            acceptance_v2_module, "write_json_atomic", side_effect=unreadable,
        ):
            with self.assertRaises(AtomicJsonReplaceError):
                acceptance_v2_module._write_competing_prepared_json(
                    missing_path, expected,
                )

    def test_atomic_json_stage_classification_and_owned_temp_cleanup(self) -> None:
        root = new_case_dir("atomic-json-cleanup")
        target = root / "value.json"

        with mock.patch.object(
            Path,
            "open",
            side_effect=PermissionError(13, "temp open denied"),
        ):
            with self.assertRaisesRegex(PermissionError, "temp open denied"):
                utils_module.write_json_atomic(target, {"value": 0})

        handle = mock.MagicMock()
        handle.__enter__.return_value = handle
        handle.write.side_effect = PermissionError(13, "temp write denied")
        with mock.patch.object(Path, "open", return_value=handle):
            with self.assertRaisesRegex(PermissionError, "temp write denied"):
                utils_module.write_json_atomic(target, {"value": 0})

        with mock.patch.object(
            utils_module.os,
            "fsync",
            side_effect=PermissionError(13, "fsync denied"),
        ):
            with self.assertRaises(PermissionError):
                utils_module.write_json_atomic(target, {"value": 1})
        self.assertEqual([], list(root.glob("*.kernel-new")))

        replace_denial = OSError(
            13,
            "replace denied",
            "source.kernel-new",
            5,
            "target.json",
        )
        with mock.patch.object(
            utils_module.os,
            "replace",
            side_effect=replace_denial,
        ):
            try:
                utils_module.write_json_atomic(target, {"value": 2})
            except OSError as caught:
                raised = caught
            else:
                self.fail("replace-stage error did not preserve OSError compatibility")
        self.assertIsInstance(raised, AtomicJsonReplaceError)
        self.assertIs(replace_denial, raised.original_error)
        self.assertEqual(replace_denial.args, raised.args)
        self.assertEqual(replace_denial.errno, raised.errno)
        self.assertEqual(replace_denial.winerror, raised.winerror)
        self.assertEqual(replace_denial.filename, raised.filename)
        self.assertEqual(replace_denial.filename2, raised.filename2)
        self.assertEqual(str(replace_denial), str(raised))
        self.assertIs(replace_denial, raised.__cause__)
        self.assertEqual([], list(root.glob("*.kernel-new")))

        replace_cleanup_denial = OSError(
            13,
            "replace primary",
            "source.kernel-new",
            32,
            "target.json",
        )
        with mock.patch.object(
            utils_module.os,
            "replace",
            side_effect=replace_cleanup_denial,
        ), mock.patch.object(
            Path,
            "unlink",
            side_effect=PermissionError(13, "cleanup secondary"),
        ):
            with self.assertRaises(AtomicJsonReplaceError) as raised:
                utils_module.write_json_atomic(target, {"value": 2})
        self.assertIs(replace_cleanup_denial, raised.exception.original_error)
        self.assertIs(replace_cleanup_denial, raised.exception.__cause__)
        self.assertTrue(
            any("cleanup secondary" in note for note in raised.exception.__notes__)
        )

        cleanup_failure = PermissionError(13, "cleanup denied")
        with mock.patch.object(
            utils_module.os,
            "fsync",
            side_effect=PermissionError(13, "fsync primary"),
        ), mock.patch.object(Path, "unlink", side_effect=cleanup_failure):
            with self.assertRaises(PermissionError) as raised:
                utils_module.write_json_atomic(target, {"value": 3})
        self.assertIn("fsync primary", str(raised.exception))
        self.assertTrue(
            any("temporary cleanup failed" in note for note in raised.exception.__notes__)
        )

    def test_exact_competing_report_bundle_publish_survives_windows_replace_denial(self) -> None:
        targets = (
            "intent",
            "acceptance_report.json",
            "attempt-record.json",
            "repair-ledger.json",
        )
        for target in targets:
            with self.subTest(target=target):
                workspace, _ = self.prepare()
                self.commit_visual(workspace)
                original_write = acceptance_v2_module.write_json_atomic
                denial_injected = False

                def publish_then_deny(path: Path, value: object) -> str:
                    nonlocal denial_injected
                    is_report_intent = (
                        target == "intent"
                        and path.parent.name == "intents"
                        and path.name.startswith("report-")
                    )
                    is_staged_member = (
                        target != "intent"
                        and "staged-reports" in path.parts
                        and path.name == target
                    )
                    if not denial_injected and (is_report_intent or is_staged_member):
                        denial_injected = True
                        original_write(path, value)
                        denial = OSError(
                            13,
                            "simulated report bundle sharing denial",
                            ".simulated.kernel-new",
                            32,
                            str(path),
                        )
                        raise AtomicJsonReplaceError(
                            path=path,
                            temp_path=path.with_name(".simulated.kernel-new"),
                            original_error=denial,
                            platform="nt",
                        )
                    return original_write(path, value)

                acceptance_v2_module.write_json_atomic = publish_then_deny
                try:
                    result = AcceptanceV2Provider(PROJECT_ROOT).materialize(
                        workspace_root=workspace,
                        provider_id="acceptance-v2-provider",
                        provider_version="1.0.0",
                        materialized_at="2026-08-02T00:20:00Z",
                    )
                finally:
                    acceptance_v2_module.write_json_atomic = original_write

                self.assertTrue(denial_injected)
                self.assertFalse(result["idempotent"])
                self.assertEqual(
                    result["report_sha256"],
                    json.loads(
                        (workspace / "acceptance_report.json").read_text(
                            encoding="utf-8",
                        )
                    )["report_sha256"],
                )

    def test_competing_report_bundle_conflicting_content_fails_closed(self) -> None:
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        original_write = acceptance_v2_module.write_json_atomic
        conflict_injected = False

        def publish_conflict_then_deny(path: Path, value: object) -> str:
            nonlocal conflict_injected
            if (
                not conflict_injected
                and "staged-reports" in path.parts
                and path.name == "attempt-record.json"
            ):
                conflict_injected = True
                conflicting = {**value, "attempt_number": value["attempt_number"] + 1}
                original_write(path, conflicting)
                denial = OSError(
                    13,
                    "simulated conflicting report bundle denial",
                    ".simulated.kernel-new",
                    5,
                    str(path),
                )
                raise AtomicJsonReplaceError(
                    path=path,
                    temp_path=path.with_name(".simulated.kernel-new"),
                    original_error=denial,
                    platform="nt",
                )
            return original_write(path, value)

        acceptance_v2_module.write_json_atomic = publish_conflict_then_deny
        try:
            with self.assertRaises(AtomicJsonReplaceError):
                AcceptanceV2Provider(PROJECT_ROOT).materialize(
                    workspace_root=workspace,
                    provider_id="acceptance-v2-provider",
                    provider_version="1.0.0",
                    materialized_at="2026-08-02T00:20:00Z",
                )
        finally:
            acceptance_v2_module.write_json_atomic = original_write
        self.assertTrue(conflict_injected)

    def test_distinct_report_writer_is_fenced_while_exact_interrupted_publication_requires_recovery(self) -> None:
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        arguments = (
            "acceptance-materialize", "--workspace-root", str(workspace),
            "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
        )
        interrupted, fault = run_cli(
            *arguments, "--materialized-at", "2026-08-02T00:20:00Z",
            "--fault-point", "after_report_publish",
        )
        self.assertNotEqual(0, interrupted.returncode)
        self.assertEqual("after_report_publish", fault["data"]["fault_point"])

        competing, conflict = run_cli(
            *arguments, "--materialized-at", "2026-08-02T00:21:00Z",
        )
        self.assertNotEqual(0, competing.returncode)
        self.assertEqual("report_fencing", conflict["data"]["first_failing_gate"])

        retry, recovery = run_cli(
            *arguments, "--materialized-at", "2026-08-02T00:20:00Z",
        )
        self.assertNotEqual(0, retry.returncode)
        self.assertEqual("publication_recovery", recovery["data"]["first_failing_gate"])

    def test_run_record_control_authority_and_skeleton_drift_fail_closed(self) -> None:
        workspace, _ = self.prepare()
        binding = json.loads((workspace / "input-binding.json").read_text(encoding="utf-8"))
        run_path = Path(binding["run"]["run_record_path"])
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        run_record["coordination_revision"] += 1
        write_json(run_path, run_record)
        patch = self.patch(workspace)
        completed, envelope = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("run_lifecycle", envelope["data"]["first_failing_gate"])

        workspace, _ = self.prepare()
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        skeleton_paths = [workspace / "acceptance_report.skeleton.json", Path(current["execution_root"]) / "acceptance_report.skeleton.json"]
        skeleton = json.loads(skeleton_paths[0].read_text(encoding="utf-8"))
        skeleton["dimensions"]["visual_quality"]["allowed_read_set"] = ["final_pdf", "rendered_page:1"]
        skeleton["skeleton_sha256"] = canonical_sha({key: value for key, value in skeleton.items() if key != "skeleton_sha256"})
        for path in skeleton_paths:
            write_json(path, skeleton)
        execution_paths = [workspace / "execution.json", Path(current["execution_root"]) / "execution.json"]
        execution = json.loads(execution_paths[0].read_text(encoding="utf-8"))
        execution["skeleton_sha256"] = skeleton["skeleton_sha256"]
        execution["execution_sha256"] = canonical_sha({key: value for key, value in execution.items() if key != "execution_sha256"})
        for path in execution_paths:
            write_json(path, execution)
        task_path = next((Path(current["execution_root"]) / "tasks").glob("*/task.json"))
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["skeleton_sha256"] = skeleton["skeleton_sha256"]
        write_json(task_path, task)
        patch = self.patch(workspace)
        completed, envelope = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("execution_identity", envelope["data"]["first_failing_gate"])

    def test_final_quality_authority_rejects_coherent_stale_evidence_mix(self) -> None:
        root = new_case_dir(self.id(), label="acceptance-v2-final-authority")
        binding_path = self.build_binding(root, 1)
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        replacement = root / "artifacts" / "alternate-final.pdf"
        replacement.write_bytes(b"alternate-valid-generation")
        final_pdf = next(item for item in binding["artifacts"] if item["logical_id"] == "final_pdf")
        final_pdf.update({"path": str(replacement), "sha256": file_sha(replacement)})
        binding["binding_sha256"] = canonical_sha({key: value for key, value in binding.items() if key != "binding_sha256"})
        write_json(binding_path, binding)
        completed, envelope = run_cli(
            "acceptance-prepare", "--workspace-root", str(root / "review/acceptance"),
            "--input-binding", str(binding_path), "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:00:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("run_final_quality_authority", envelope["data"]["first_failing_gate"])

    def test_final_quality_authority_requires_control_store_cas_publication(self) -> None:
        root = new_case_dir(self.id(), label="acceptance-v2-final-authority-cas")
        binding_path = self.build_binding(root, 1, publish_authority=False)
        completed, envelope = run_cli(
            "acceptance-prepare", "--workspace-root", str(root / "review/acceptance"),
            "--input-binding", str(binding_path), "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:00:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("acceptance_final_authority_unpublished", envelope["data"]["error_code"])
        published, first = run_cli("acceptance-final-authority-publish", "--input-binding", str(binding_path))
        self.assertEqual(0, published.returncode, published.stderr)
        self.assertFalse(first["data"]["idempotent"])
        retried, second = run_cli("acceptance-final-authority-publish", "--input-binding", str(binding_path))
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertTrue(second["data"]["idempotent"])

        conflicting_path = self.build_binding(root, 2, publish_authority=False)
        binding = json.loads(conflicting_path.read_text(encoding="utf-8"))
        binding["run"]["acceptance_revision"] = 1
        binding["run"]["final_checkpoint"]["authority_path"] = str(root / "workflow" / "final-quality-ready.1.json")
        self.refresh_final_authority(binding)
        binding["binding_sha256"] = canonical_sha({key: value for key, value in binding.items() if key != "binding_sha256"})
        write_json(conflicting_path, binding)
        conflicted, conflict = run_cli("acceptance-final-authority-publish", "--input-binding", str(conflicting_path))
        self.assertNotEqual(0, conflicted.returncode)
        self.assertEqual("acceptance_final_authority_conflict", conflict["data"]["error_code"])

    def test_render_manifest_page_set_must_equal_visual_binding(self) -> None:
        root = new_case_dir(self.id(), label="acceptance-v2-render-binding")
        binding_path = self.build_binding(root, 1, publish_authority=False)
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        manifest_path = Path(binding["quality_inputs"]["render_evidence_manifest"]["path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["pages"][0]["path"] = manifest["pages"][1]["path"]
        manifest["pages"][0]["sha256"] = manifest["pages"][1]["sha256"]
        manifest["manifest_sha256"] = canonical_sha({key: value for key, value in manifest.items() if key != "manifest_sha256"})
        write_json(manifest_path, manifest)
        binding["quality_inputs"]["render_evidence_manifest"]["sha256"] = file_sha(manifest_path)
        reconciliation_path = Path(binding["quality_inputs"]["rendered_text_reconciliation"]["path"])
        reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
        reconciliation["render_evidence_manifest_sha256"] = manifest["manifest_sha256"]
        reconciliation["report_sha256"] = canonical_sha({key: value for key, value in reconciliation.items() if key != "report_sha256"})
        write_json(reconciliation_path, reconciliation)
        binding["quality_inputs"]["rendered_text_reconciliation"]["sha256"] = file_sha(reconciliation_path)
        self.refresh_final_authority(binding)
        binding["binding_sha256"] = canonical_sha({key: value for key, value in binding.items() if key != "binding_sha256"})
        write_json(binding_path, binding)
        completed, envelope = run_cli("acceptance-final-authority-publish", "--input-binding", str(binding_path))
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("quality_input_validity", envelope["data"]["first_failing_gate"])

    def test_guard_binds_immutable_attempt_and_ledger_history(self) -> None:
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        completed, _ = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        report_path = Path(current["execution_root"]) / "reports"
        attempt_path = next(report_path.glob("*/attempt-record.json"))
        original_attempt = attempt_path.read_bytes()
        attempt = json.loads(original_attempt.decode("utf-8"))
        attempt["routing_state"] = "repair_required"
        attempt["attempt_record_sha256"] = canonical_sha({key: value for key, value in attempt.items() if key != "attempt_record_sha256"})
        write_json(attempt_path, attempt)
        _, guard = run_cli("acceptance-guard-eligibility", "--workspace-root", str(workspace))
        self.assertFalse(guard["data"]["eligible"])
        attempt_path.write_bytes(original_attempt)
        ledger_path = next(report_path.glob("*/repair-ledger.json"))
        root_ledger_path = workspace / "repair-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["contract_gap_cycles"] = ["forged"]
        ledger["ledger_sha256"] = canonical_sha({key: value for key, value in ledger.items() if key != "ledger_sha256"})
        write_json(ledger_path, ledger)
        write_json(root_ledger_path, ledger)
        _, guard = run_cli("acceptance-guard-eligibility", "--workspace-root", str(workspace))
        self.assertFalse(guard["data"]["eligible"])

    def test_guard_rejects_tampered_prior_semantic_attempt_history(self) -> None:
        workspace, root = self.prepare()
        self.commit_visual(workspace, decision="fail")
        completed, _ = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        successor = self.build_binding(root, 2)
        repaired, _ = run_cli(
            "acceptance-repair-prepare", "--workspace-root", str(workspace),
            "--input-binding", str(successor), "--prepared-at", "2026-08-02T00:30:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertEqual(0, repaired.returncode, repaired.stderr)
        self.commit_visual(workspace)
        completed, _ = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        _, guard = run_cli("acceptance-guard-eligibility", "--workspace-root", str(workspace))
        self.assertTrue(guard["data"]["eligible"])
        ledger = json.loads((workspace / "repair-ledger.json").read_text(encoding="utf-8"))
        prior_attempt_path = Path(ledger["semantic_attempts"][0]["attempt_record_path"])
        prior_attempt = json.loads(prior_attempt_path.read_text(encoding="utf-8"))
        prior_attempt["routing_state"] = "manual_repair_required"
        prior_attempt["attempt_record_sha256"] = canonical_sha({
            key: value for key, value in prior_attempt.items() if key != "attempt_record_sha256"
        })
        write_json(prior_attempt_path, prior_attempt)
        _, guard = run_cli("acceptance-guard-eligibility", "--workspace-root", str(workspace))
        self.assertFalse(guard["data"]["eligible"])
        self.assertFalse(guard["data"]["mechanical_checks"]["historical_attempts_current"])

    def test_authorized_root_escape_fails_before_input_io(self) -> None:
        root = new_case_dir(self.id(), label="acceptance-v2-path-boundary")
        binding_path = self.build_binding(root, 1, publish_authority=False)
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        outside = PROJECT_ROOT / "CONTEXT-MAP.md"
        binding["artifacts"][1]["path"] = str(outside)
        binding["artifacts"][1]["sha256"] = file_sha(outside)
        self.refresh_final_authority(binding)
        binding["binding_sha256"] = canonical_sha({key: value for key, value in binding.items() if key != "binding_sha256"})
        write_json(binding_path, binding)
        completed, envelope = run_cli("acceptance-final-authority-publish", "--input-binding", str(binding_path))
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("input_path_boundary", envelope["data"]["first_failing_gate"])

    def test_workspace_rejects_a_second_nonterminal_execution(self) -> None:
        workspace, root = self.prepare()
        binding = root / "input-binding-1.json"
        completed, envelope = run_cli(
            "acceptance-prepare", "--workspace-root", str(workspace),
            "--input-binding", str(binding), "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:05:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("execution_uniqueness", envelope["data"]["first_failing_gate"])

    def test_prepare_retry_recovers_after_control_commit(self) -> None:
        root = new_case_dir(self.id(), label="acceptance-v2-prepare-recovery")
        workspace = root / "review" / "acceptance"
        binding = self.build_binding(root, 1)
        arguments = (
            "acceptance-prepare", "--workspace-root", str(workspace), "--input-binding", str(binding),
            "--attempt-number", "1", "--prepared-at", "2026-08-02T00:00:00Z",
            "--coordinator-session", "coordinator-session",
        )
        failed, _ = run_cli(*arguments, "--fault-point", "after_prepare_control_commit")
        self.assertNotEqual(0, failed.returncode)
        recovered, envelope = run_cli(*arguments)
        self.assertEqual(0, recovered.returncode, recovered.stderr)
        self.assertTrue(Path(envelope["data"]["skeleton_path"]).is_file())

    def test_cross_phase_finding_can_only_add_precompile_failure(self) -> None:
        workspace, _ = self.prepare()
        finding = {"finding_id": "cross-1", "rule_id": "argument_chain_integrity",
                   "violation_id": "reasoning_link_missing", "effect": "add_failure_only",
                   "evidence": [{"artifact_logical_id": "final_pdf", "location": "page:2"}]}
        self.commit_visual(workspace, cross_findings=[finding])
        completed, envelope = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("fail", envelope["data"]["overall_status"])
        report = json.loads((workspace / "acceptance_report.json").read_text(encoding="utf-8"))
        result = next(item for item in report["criterion_results"] if item["rule_id"] == "argument_chain_integrity")
        self.assertEqual("fail", result["decision"])

    def test_unknown_violation_routes_to_contract_gap_without_attempt(self) -> None:
        workspace, _ = self.prepare()
        patch_path = self.patch(workspace, decision="fail")
        patch = json.loads(patch_path.read_text(encoding="utf-8"))
        patch["criterion_results"][0]["violation_id"] = "unregistered_violation"
        patch["patch_sha256"] = canonical_sha({key: value for key, value in patch.items() if key != "patch_sha256"})
        write_json(patch_path, patch)
        completed, _ = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch_path), "--committed-at", "2026-08-02T00:10:00Z")
        self.assertEqual(0, completed.returncode, completed.stderr)
        completed, envelope = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("blocked_contract_gap", envelope["data"]["overall_status"])
        self.assertEqual(0, envelope["data"]["semantic_attempts_used"])

    def test_stale_artifact_rejects_materialization_at_input_freshness_gate(self) -> None:
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        binding = json.loads((workspace / "input-binding.json").read_text(encoding="utf-8"))
        final_pdf = next(item for item in binding["artifacts"] if item["logical_id"] == "final_pdf")
        Path(final_pdf["path"]).write_bytes(b"stale-pdf")
        completed, envelope = self.materialize(workspace)
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("input_freshness", envelope["data"]["first_failing_gate"])

    def test_repair_requires_fresh_artifact_generation_and_bounds_three_failures(self) -> None:
        workspace, root = self.prepare()
        for attempt in (1, 2, 3):
            self.commit_visual(workspace, decision="fail")
            completed, envelope = self.materialize(workspace)
            self.assertEqual(0, completed.returncode, completed.stderr)
            if attempt < 3:
                unchanged = root / f"input-binding-{attempt}.json"
                rejected, rejection = run_cli("acceptance-repair-prepare", "--workspace-root", str(workspace),
                    "--input-binding", str(unchanged), "--prepared-at", f"2026-08-02T00:{20 + attempt:02d}:00Z", "--coordinator-session", "coordinator-session")
                self.assertNotEqual(0, rejected.returncode)
                self.assertEqual("repair_generation", rejection["data"]["first_failing_gate"])
                fresh = self.build_binding(root, attempt + 1)
                unrelated = json.loads(fresh.read_text(encoding="utf-8"))
                unrelated["run"]["run_id"] = "f" * 32
                unrelated["binding_sha256"] = canonical_sha({key: value for key, value in unrelated.items() if key != "binding_sha256"})
                unrelated_path = write_json(root / f"unrelated-binding-{attempt + 1}.json", unrelated)
                rejected, rejection = run_cli("acceptance-repair-prepare", "--workspace-root", str(workspace),
                    "--input-binding", str(unrelated_path), "--prepared-at", f"2026-08-02T00:{25 + attempt:02d}:00Z", "--coordinator-session", "coordinator-session")
                self.assertNotEqual(0, rejected.returncode)
                self.assertEqual("run_lifecycle", rejection["data"]["first_failing_gate"])
                repaired, _ = run_cli("acceptance-repair-prepare", "--workspace-root", str(workspace),
                    "--input-binding", str(fresh), "--prepared-at", f"2026-08-02T00:{30 + attempt:02d}:00Z", "--coordinator-session", "coordinator-session")
                self.assertEqual(0, repaired.returncode, repaired.stderr)
            else:
                self.assertEqual("manual_repair_required", envelope["data"]["routing_state"])
                self.assertEqual(3, envelope["data"]["semantic_attempts_used"])
        self.assertEqual(3, len(list((workspace / "executions").glob("*/reports/*/acceptance_report.json"))))
        attempt_paths = sorted((workspace / "executions").glob("*/reports/*/attempt-record.json"))
        self.assertEqual(3, len(attempt_paths))
        attempt_records = [json.loads(path.read_text(encoding="utf-8")) for path in attempt_paths]
        by_attempt = {item["attempt_number"]: item for item in attempt_records}
        self.assertEqual([], by_attempt[1]["changed_generations"])
        self.assertEqual(["main_tex"], by_attempt[2]["changed_generations"])
        expected_reruns = ["source-faithfulness-reviewer", "writing-quality-reviewer", "pyramid-reviewer", "visual_quality"]
        self.assertEqual(expected_reruns, by_attempt[2]["invalidated_judgments"])
        self.assertEqual(expected_reruns, by_attempt[2]["required_reruns"])
        self.assertEqual(expected_reruns, by_attempt[2]["completed_reruns"])

    def test_text_equivalence_successor_retains_precompile_judgments(self) -> None:
        workspace, root = self.prepare()
        self.commit_visual(workspace, decision="fail")
        completed, _ = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        successor = self.build_binding(root, 2, equivalent=True)
        repaired, envelope = run_cli("acceptance-repair-prepare", "--workspace-root", str(workspace),
            "--input-binding", str(successor), "--prepared-at", "2026-08-02T00:30:00Z", "--coordinator-session", "coordinator-session")
        self.assertEqual(0, repaired.returncode, repaired.stderr + json.dumps(envelope))
        self.commit_visual(workspace)
        completed, _ = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        attempt = json.loads(next((Path(current["execution_root"]) / "reports").glob("*/attempt-record.json")).read_text(encoding="utf-8"))
        retained = ["source-faithfulness-reviewer", "writing-quality-reviewer", "pyramid-reviewer"]
        self.assertEqual(retained, attempt["retained_judgments"])
        self.assertEqual(["visual_quality"], attempt["invalidated_judgments"])
        self.assertEqual(["visual_quality"], attempt["required_reruns"])

    def test_repair_rejects_underdeclared_changed_generation_set(self) -> None:
        workspace, root = self.prepare()
        self.commit_visual(workspace, decision="fail")
        completed, _ = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        successor_path = self.build_binding(root, 2)
        successor = json.loads(successor_path.read_text(encoding="utf-8"))
        successor["run"]["changed_generation_ids"] = ["final_pdf"]
        successor["binding_sha256"] = canonical_sha({key: value for key, value in successor.items() if key != "binding_sha256"})
        write_json(successor_path, successor)
        repaired, envelope = run_cli(
            "acceptance-repair-prepare", "--workspace-root", str(workspace),
            "--input-binding", str(successor_path), "--prepared-at", "2026-08-02T00:30:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertNotEqual(0, repaired.returncode)
        self.assertEqual("repair_generation", envelope["data"]["first_failing_gate"])

    def test_contract_gap_does_not_consume_semantic_attempt(self) -> None:
        workspace, _ = self.prepare()
        self.commit_visual(workspace, contract_gap=True)
        completed, envelope = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("blocked_contract_gap", envelope["data"]["overall_status"])
        self.assertEqual(0, envelope["data"]["semantic_attempts_used"])

    def test_reconcile_rejects_changed_published_bytes_and_finishes_intact_publication(self) -> None:
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        failed, _ = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z",
            "--fault-point", "after_patch_control_commit")
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("patch-*.json"))
        canonical = Path(json.loads(intent_path.read_text(encoding="utf-8"))["canonical_path"])
        changed = json.loads(canonical.read_text(encoding="utf-8"))
        changed["reviewer"]["reviewer_id"] = "tampered"
        write_json(canonical, changed)
        rejected, envelope = run_cli("acceptance-reconcile", "--workspace-root", str(workspace))
        self.assertNotEqual(0, rejected.returncode)
        self.assertEqual("publication_recovery", envelope["data"]["first_failing_gate"])

        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        failed, _ = run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch), "--committed-at", "2026-08-02T00:10:00Z",
            "--fault-point", "after_patch_publish")
        self.assertNotEqual(0, failed.returncode)
        reconciled, data = run_cli("acceptance-reconcile", "--workspace-root", str(workspace))
        self.assertEqual(0, reconciled.returncode, reconciled.stderr)
        self.assertEqual(["visual_quality"], data["data"]["committed_dimensions"])

    def test_patch_after_control_commit_recovers_intact_and_exact_retry_is_idempotent(self) -> None:
        # scenario_id: patch_after_control_commit_intact_recovery
        # authority: valid Patch + active Reviewer Claim -> boundary: Control Store commit
        # mutation seam: injected after_patch_control_commit fault
        # rematerialized nodes: execution.json and publication intent; stale before recovery: both
        # observation: committed Patch authority remains byte-stable through reconcile and retry
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        arguments = (
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        failed, fault = run_cli(
            *arguments, "--fault-point", "after_patch_control_commit",
        )
        self.assertNotEqual(0, failed.returncode)
        self.assertEqual("injected_acceptance_v2_fault", fault["classification"])
        self.assertEqual("after_patch_control_commit", fault["data"]["fault_point"])

        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution_root = Path(current["execution_root"])
        intent_path = next((execution_root / "intents").glob("patch-*.json"))
        self.assertEqual("PREPARED", json.loads(intent_path.read_text(encoding="utf-8"))["state"])
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            control_before = database.execute(
                "SELECT state, artifact_sha256 FROM publication_intents"
            ).fetchall()
            authority_before = database.execute(
                "SELECT execution_revision, state FROM execution_authority WHERE singleton=1"
            ).fetchone()
        self.assertEqual([("COMMITTED", json.loads(patch.read_text(encoding="utf-8"))["patch_sha256"])], control_before)
        self.assertEqual((2, "reviewing"), authority_before)

        reconciled, recovery = run_cli(
            "acceptance-reconcile", "--workspace-root", str(workspace),
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stderr)
        self.assertEqual(["committed_patch:visual_quality"], recovery["data"]["actions"])
        self.assertEqual("COMMITTED", json.loads(intent_path.read_text(encoding="utf-8"))["state"])
        stable_paths = (
            workspace / "execution.json",
            execution_root / "execution.json",
            intent_path,
            Path(json.loads((workspace / "execution.json").read_text(encoding="utf-8"))[
                "committed_patches"
            ]["visual_quality"]["path"]),
        )
        stable_bytes = {path: path.read_bytes() for path in stable_paths}

        retried, retry = run_cli(*arguments)
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertTrue(retry["data"]["idempotent"])
        self.assertEqual(stable_bytes, {path: path.read_bytes() for path in stable_paths})
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertEqual(control_before, database.execute(
                "SELECT state, artifact_sha256 FROM publication_intents"
            ).fetchall())
            self.assertEqual(authority_before, database.execute(
                "SELECT execution_revision, state FROM execution_authority WHERE singleton=1"
            ).fetchone())

    def test_patch_first_control_commit_recovers_and_exact_retry_is_idempotent(self) -> None:
        # scenario_id: patch_first_control_commit_recovery
        # authority: complete file intent + canonical Patch, then SQLite PREPARED/COMMITTING CAS
        # boundary: first SQLite commit before final publication control commit
        # expected observation: reconcile commits once; exact command retry is byte-idempotent
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        arguments = (
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        failed, fault = run_cli(
            *arguments, "--fault-point", "after_patch_intent_control_commit",
        )
        self.assertNotEqual(0, failed.returncode)
        self.assertEqual("after_patch_intent_control_commit", fault["data"]["fault_point"])

        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution_root = Path(current["execution_root"])
        intent_path = next((execution_root / "intents").glob("patch-*.json"))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual("PREPARED", intent["state"])
        self.assertTrue(Path(intent["canonical_path"]).is_file())
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertEqual(
                ("PREPARED",),
                database.execute(
                    "SELECT state FROM publication_intents WHERE intent_id=?",
                    (intent["intent_id"],),
                ).fetchone(),
            )
            self.assertEqual(
                ("COMMITTING",),
                database.execute(
                    "SELECT state FROM reviewer_claims WHERE task_id=?",
                    (intent["task_id"],),
                ).fetchone(),
            )

        reconciled, recovery = run_cli(
            "acceptance-reconcile", "--workspace-root", str(workspace),
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stderr)
        self.assertEqual(["committed_patch:visual_quality"], recovery["data"]["actions"])
        stable_paths = (
            workspace / "execution.json",
            execution_root / "execution.json",
            intent_path,
            Path(intent["canonical_path"]),
        )
        stable = {path: path.read_bytes() for path in stable_paths}
        retried, retry = run_cli(*arguments)
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertTrue(retry["data"]["idempotent"])
        self.assertEqual(stable, {path: path.read_bytes() for path in stable_paths})

    def test_patch_reconcile_abort_is_serialized_against_first_cas(self) -> None:
        # scenario_id: patch_reconcile_vs_first_cas
        # target invariant: a control-less abort cannot overwrite a newly controlled intent
        # barrier: reconcile holds BEGIN IMMEDIATE while publishing ABORTED; writer waits on CAS
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        arguments = (
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        failed, fault = run_cli(
            *arguments, "--fault-point", "after_patch_file_prepare",
        )
        self.assertNotEqual(0, failed.returncode)
        self.assertEqual("after_patch_file_prepare", fault["data"]["fault_point"])
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("patch-*.json"))

        recovery, writer, writer_envelope = self.reconcile_while_writer_waits_on_abort(
            workspace, intent_path, arguments,
        )
        self.assertEqual(
            ["aborted_uncommitted:acceptance_patch_publication"],
            recovery["actions"],
        )
        self.assertNotEqual(0, writer.returncode)
        self.assertEqual("patch_fencing", writer_envelope["data"]["first_failing_gate"])
        self.assertEqual("ABORTED", json.loads(intent_path.read_text(encoding="utf-8"))["state"])
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertIsNone(database.execute(
                "SELECT state FROM publication_intents WHERE intent_id=?",
                (json.loads(intent_path.read_text(encoding="utf-8"))["intent_id"],),
            ).fetchone())
        retried, retry = run_cli(*arguments)
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertFalse(retry["data"]["idempotent"])

    def test_patch_writer_commit_is_not_overwritten_by_waiting_reconcile(self) -> None:
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        arguments = (
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        failed, _ = run_cli(
            *arguments, "--fault-point", "after_patch_file_prepare",
        )
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("patch-*.json"))
        writer, _, recovery = self.writer_wins_before_waiting_reconcile(
            workspace, intent_path, arguments,
        )
        self.assertEqual(0, writer.returncode, writer.stderr)
        self.assertIn(recovery["actions"], ([], ["committed_patch:visual_quality"]))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual("COMMITTED", intent["state"])
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertEqual(("COMMITTED",), database.execute(
                "SELECT state FROM publication_intents WHERE intent_id=?",
                (intent["intent_id"],),
            ).fetchone())

    def test_patch_failed_writer_cannot_abort_a_later_competing_writer(self) -> None:
        # scenario_id: patch_reconcile_stale_writer_competing_writer
        # order: reconcile aborts, W1 fails locked reauthorization, W2 waits on W1's lock
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        arguments = (
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        failed, _ = run_cli(*arguments, "--fault-point", "after_patch_file_prepare")
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("patch-*.json"))

        recovery, first_error, second_completed, second_envelope = (
            self.reconcile_then_two_writers_compete_at_reauthorization(
                workspace,
                intent_path,
                lambda: AcceptanceV2Provider(PROJECT_ROOT).commit_patch(
                    workspace_root=workspace,
                    dimension="visual_quality",
                    patch_path=patch,
                    committed_at="2026-08-02T00:10:00Z",
                ),
                arguments,
            )
        )
        self.assertEqual(
            ["aborted_uncommitted:acceptance_patch_publication"],
            recovery["actions"],
        )
        self.assertEqual("patch_fencing", first_error.data["first_failing_gate"])
        self.assertNotEqual(0, second_completed.returncode)
        self.assertEqual("patch_fencing", second_envelope["data"]["first_failing_gate"])
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual("ABORTED", intent["state"])
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertIsNone(database.execute(
                "SELECT state FROM publication_intents WHERE intent_id=?",
                (intent["intent_id"],),
            ).fetchone())
        retried, retry = run_cli(*arguments)
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertFalse(retry["data"]["idempotent"])
        committed_intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual("COMMITTED", committed_intent["state"])

    def test_reconcile_abort_file_io_failure_rolls_back_and_remains_retryable(self) -> None:
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        arguments = (
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
        )
        failed, _ = run_cli(
            *arguments, "--fault-point", "after_patch_file_prepare",
        )
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("patch-*.json"))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        original_write = acceptance_v2_module.write_json_atomic

        def fail_abort_write(path: Path, value: object) -> str:
            if (
                path.resolve() == intent_path.resolve()
                and isinstance(value, dict)
                and value.get("state") == "ABORTED"
            ):
                raise OSError("injected abort publication failure")
            return original_write(path, value)

        acceptance_v2_module.write_json_atomic = fail_abort_write
        try:
            with self.assertRaisesRegex(OSError, "injected abort publication failure"):
                AcceptanceV2Provider(PROJECT_ROOT).reconcile(workspace_root=workspace)
        finally:
            acceptance_v2_module.write_json_atomic = original_write
        self.assertEqual("PREPARED", json.loads(intent_path.read_text(encoding="utf-8"))["state"])
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertIsNone(database.execute(
                "SELECT state FROM publication_intents WHERE intent_id=?",
                (intent["intent_id"],),
            ).fetchone())
        recovery = AcceptanceV2Provider(PROJECT_ROOT).reconcile(workspace_root=workspace)
        self.assertEqual(
            ["aborted_uncommitted:acceptance_patch_publication"],
            recovery["actions"],
        )
        retried, _ = run_cli(*arguments)
        self.assertEqual(0, retried.returncode, retried.stderr)

    def test_patch_reconcile_recovers_every_post_control_publication_boundary(self) -> None:
        # scenario_id: patch_post_control_projection_recovery
        # authority input: committed Control Store intent at revision N+1
        # boundary: each execution projection or file-intent write after that commit
        # rematerialized nodes: both execution projections and the file intent
        # expected first gate: public reconciliation completes the committed publication
        fault_points = (
            "after_patch_control_commit",
            "after_patch_execution_projection_write",
            "after_patch_root_execution_projection_write",
            "after_patch_intent_commit_write",
        )
        for fault_point in fault_points:
            with self.subTest(fault_point=fault_point):
                workspace, _ = self.prepare()
                patch = self.patch(workspace)
                failed, fault = run_cli(
                    "acceptance-patch-commit", "--workspace-root", str(workspace),
                    "--dimension", "visual_quality", "--patch", str(patch),
                    "--committed-at", "2026-08-02T00:10:00Z",
                    "--fault-point", fault_point,
                )
                self.assertNotEqual(0, failed.returncode)
                self.assertEqual(fault_point, fault["data"]["fault_point"])

                current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
                execution_root = Path(current["execution_root"])
                intent_path = next((execution_root / "intents").glob("patch-*.json"))
                if fault_point == "after_patch_execution_projection_write":
                    self.assertEqual(
                        json.loads((workspace / "execution.json").read_text(encoding="utf-8"))["execution_revision"] + 1,
                        json.loads((execution_root / "execution.json").read_text(encoding="utf-8"))["execution_revision"],
                    )
                if fault_point == "after_patch_root_execution_projection_write":
                    self.assertEqual("PREPARED", json.loads(intent_path.read_text(encoding="utf-8"))["state"])
                    self.assertEqual(
                        json.loads(intent_path.read_text(encoding="utf-8"))["expected_execution_revision"] + 1,
                        json.loads((workspace / "execution.json").read_text(encoding="utf-8"))["execution_revision"],
                    )

                reconciled, recovery = run_cli(
                    "acceptance-reconcile", "--workspace-root", str(workspace),
                )
                self.assertEqual(0, reconciled.returncode, reconciled.stderr)
                self.assertEqual(
                    [] if fault_point == "after_patch_intent_commit_write" else ["committed_patch:visual_quality"],
                    recovery["data"]["actions"],
                )
                self.assertEqual("COMMITTED", json.loads(intent_path.read_text(encoding="utf-8"))["state"])
                self.assertEqual(
                    (workspace / "execution.json").read_bytes(),
                    (execution_root / "execution.json").read_bytes(),
                )
                stable = {
                    path: path.read_bytes()
                    for path in (workspace / "execution.json", execution_root / "execution.json", intent_path)
                }
                repeated, repeat = run_cli(
                    "acceptance-reconcile", "--workspace-root", str(workspace),
                )
                self.assertEqual(0, repeated.returncode, repeated.stderr)
                self.assertEqual([], repeat["data"]["actions"])
                self.assertEqual(stable, {path: path.read_bytes() for path in stable})

    def test_patch_reconcile_rejects_equal_tampered_successor_projections(self) -> None:
        # scenario_id: patch_equal_successor_projection_tamper
        # target invariant: N+1 execution must be the deterministic successor of SQLite-bound N
        # mutation seam: after both execution projections are written, before file-intent commit
        # intentionally stale node: SQLite prior_execution_sha256
        # expected first gate/code: publication_recovery/acceptance_execution_projection_stale
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        failed, _ = run_cli(
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z",
            "--fault-point", "after_patch_root_execution_projection_write",
        )
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution_paths = (
            workspace / "execution.json",
            Path(current["execution_root"]) / "execution.json",
        )
        tampered = json.loads(execution_paths[0].read_text(encoding="utf-8"))
        tampered["prepared_at"] = "2026-08-02T00:00:01Z"
        tampered["execution_sha256"] = canonical_sha({
            key: value for key, value in tampered.items() if key != "execution_sha256"
        })
        for path in execution_paths:
            write_json(path, tampered)
        reconciled, envelope = run_cli(
            "acceptance-reconcile", "--workspace-root", str(workspace),
        )
        self.assertNotEqual(0, reconciled.returncode)
        self.assertEqual("publication_recovery", envelope["data"]["first_failing_gate"])
        self.assertEqual("acceptance_execution_projection_stale", envelope["data"]["error_code"])

    def test_reconcile_rejects_mutable_intent_authority_and_path_substitution(self) -> None:
        workspace, _ = self.prepare()
        patch = self.patch(workspace)
        failed, _ = run_cli(
            "acceptance-patch-commit", "--workspace-root", str(workspace),
            "--dimension", "visual_quality", "--patch", str(patch),
            "--committed-at", "2026-08-02T00:10:00Z", "--fault-point", "after_patch_publish",
        )
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("patch-*.json"))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        substitute = Path(current["execution_root"]) / "committed" / "visual_quality" / "substitute" / "judgment-patch.json"
        write_json(substitute, json.loads(Path(intent["canonical_path"]).read_text(encoding="utf-8")))
        intent["canonical_path"] = str(substitute)
        write_json(intent_path, intent)
        reconciled, envelope = run_cli("acceptance-reconcile", "--workspace-root", str(workspace))
        self.assertNotEqual(0, reconciled.returncode)
        self.assertEqual("publication_recovery", envelope["data"]["first_failing_gate"])

        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        failed, _ = run_cli(
            "acceptance-materialize", "--workspace-root", str(workspace),
            "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
            "--materialized-at", "2026-08-02T00:20:00Z", "--fault-point", "after_report_publish",
        )
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("report-*.json"))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        original_root = Path(intent["staged_path"]).parent
        substitute_root = Path(current["execution_root"]) / "staged-reports" / "substitute"
        for filename in ("acceptance_report.json", "attempt-record.json", "repair-ledger.json"):
            write_json(substitute_root / filename, json.loads((original_root / filename).read_text(encoding="utf-8")))
        intent["staged_path"] = str(substitute_root / "acceptance_report.json")
        write_json(intent_path, intent)
        reconciled, envelope = run_cli("acceptance-reconcile", "--workspace-root", str(workspace))
        self.assertNotEqual(0, reconciled.returncode)
        self.assertEqual("publication_recovery", envelope["data"]["first_failing_gate"])

    def test_reconcile_finishes_an_intact_interrupted_report_publication(self) -> None:
        for fault_point in ("after_report_publish", "after_report_control_commit"):
            with self.subTest(fault_point=fault_point):
                workspace, _ = self.prepare()
                self.commit_visual(workspace)
                failed, _ = run_cli(
                    "acceptance-materialize", "--workspace-root", str(workspace),
                    "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
                    "--materialized-at", "2026-08-02T00:20:00Z", "--fault-point", fault_point,
                )
                self.assertNotEqual(0, failed.returncode)
                reconciled, data = run_cli("acceptance-reconcile", "--workspace-root", str(workspace))
                self.assertEqual(0, reconciled.returncode, reconciled.stderr)
                self.assertTrue(data["data"]["report_published"])

    def test_report_first_control_commit_recovers_and_exact_retry_is_idempotent(self) -> None:
        # scenario_id: report_first_control_commit_recovery
        # authority: complete file intent + staged bundle, then SQLite PREPARED CAS
        # boundary: first SQLite commit before final publication control commit
        # expected observation: reconcile commits once; exact command retry is byte-idempotent
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        arguments = (
            "acceptance-materialize", "--workspace-root", str(workspace),
            "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
            "--materialized-at", "2026-08-02T00:20:00Z",
        )
        failed, fault = run_cli(
            *arguments, "--fault-point", "after_report_intent_control_commit",
        )
        self.assertNotEqual(0, failed.returncode)
        self.assertEqual("after_report_intent_control_commit", fault["data"]["fault_point"])

        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution_root = Path(current["execution_root"])
        intent_path = next((execution_root / "intents").glob("report-*.json"))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual("PREPARED", intent["state"])
        staged_root = Path(intent["staged_path"]).parent
        self.assertTrue(all(
            (staged_root / filename).is_file()
            for filename in ("acceptance_report.json", "attempt-record.json", "repair-ledger.json")
        ))
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertEqual(
                ("PREPARED",),
                database.execute(
                    "SELECT state FROM publication_intents WHERE intent_id=?",
                    (intent["intent_id"],),
                ).fetchone(),
            )

        reconciled, recovery = run_cli(
            "acceptance-reconcile", "--workspace-root", str(workspace),
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stderr)
        self.assertEqual(["committed_report"], recovery["data"]["actions"])
        published_root = Path(intent["canonical_path"]).parent
        stable_paths = (
            workspace / "execution.json",
            execution_root / "execution.json",
            workspace / "acceptance_report.json",
            workspace / "repair-ledger.json",
            published_root / "acceptance_report.json",
            published_root / "attempt-record.json",
            published_root / "repair-ledger.json",
            intent_path,
        )
        stable = {path: path.read_bytes() for path in stable_paths}
        retried, retry = run_cli(*arguments)
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertTrue(retry["data"]["idempotent"])
        self.assertEqual(stable, {path: path.read_bytes() for path in stable_paths})

    def test_report_reconcile_abort_is_serialized_against_first_cas(self) -> None:
        # scenario_id: report_reconcile_vs_first_cas
        # target invariant: a control-less abort cannot overwrite a newly controlled intent
        # barrier: reconcile holds BEGIN IMMEDIATE while publishing ABORTED; writer waits on CAS
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        arguments = (
            "acceptance-materialize", "--workspace-root", str(workspace),
            "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
            "--materialized-at", "2026-08-02T00:20:00Z",
        )
        failed, fault = run_cli(
            *arguments, "--fault-point", "after_report_file_prepare",
        )
        self.assertNotEqual(0, failed.returncode)
        self.assertEqual("after_report_file_prepare", fault["data"]["fault_point"])
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("report-*.json"))

        recovery, writer, writer_envelope = self.reconcile_while_writer_waits_on_abort(
            workspace, intent_path, arguments,
        )
        self.assertEqual(
            ["aborted_uncommitted:acceptance_report_publication"],
            recovery["actions"],
        )
        self.assertNotEqual(0, writer.returncode)
        self.assertEqual("report_fencing", writer_envelope["data"]["first_failing_gate"])
        self.assertEqual("ABORTED", json.loads(intent_path.read_text(encoding="utf-8"))["state"])
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertIsNone(database.execute(
                "SELECT state FROM publication_intents WHERE intent_id=?",
                (json.loads(intent_path.read_text(encoding="utf-8"))["intent_id"],),
            ).fetchone())
        retried, retry = run_cli(*arguments)
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertFalse(retry["data"]["idempotent"])

    def test_report_writer_commit_is_not_overwritten_by_waiting_reconcile(self) -> None:
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        arguments = (
            "acceptance-materialize", "--workspace-root", str(workspace),
            "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
            "--materialized-at", "2026-08-02T00:20:00Z",
        )
        failed, _ = run_cli(
            *arguments, "--fault-point", "after_report_file_prepare",
        )
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("report-*.json"))
        writer, _, recovery = self.writer_wins_before_waiting_reconcile(
            workspace, intent_path, arguments,
        )
        self.assertEqual(0, writer.returncode, writer.stderr)
        self.assertIn(recovery["actions"], ([], ["committed_report"]))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual("COMMITTED", intent["state"])
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertEqual(("COMMITTED",), database.execute(
                "SELECT state FROM publication_intents WHERE intent_id=?",
                (intent["intent_id"],),
            ).fetchone())

    def test_report_failed_writer_cannot_abort_a_later_competing_writer(self) -> None:
        # scenario_id: report_reconcile_stale_writer_competing_writer
        # order: reconcile aborts, W1 fails locked reauthorization, W2 waits on W1's lock
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        arguments = (
            "acceptance-materialize", "--workspace-root", str(workspace),
            "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
            "--materialized-at", "2026-08-02T00:20:00Z",
        )
        failed, _ = run_cli(*arguments, "--fault-point", "after_report_file_prepare")
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        intent_path = next((Path(current["execution_root"]) / "intents").glob("report-*.json"))

        recovery, first_error, second_completed, second_envelope = (
            self.reconcile_then_two_writers_compete_at_reauthorization(
                workspace,
                intent_path,
                lambda: AcceptanceV2Provider(PROJECT_ROOT).materialize(
                    workspace_root=workspace,
                    provider_id="acceptance-v2-provider",
                    provider_version="1.0.0",
                    materialized_at="2026-08-02T00:20:00Z",
                ),
                arguments,
            )
        )
        self.assertEqual(
            ["aborted_uncommitted:acceptance_report_publication"],
            recovery["actions"],
        )
        self.assertEqual("report_fencing", first_error.data["first_failing_gate"])
        self.assertNotEqual(0, second_completed.returncode)
        self.assertEqual("report_fencing", second_envelope["data"]["first_failing_gate"])
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual("ABORTED", intent["state"])
        with sqlite3.connect(workspace / "acceptance-control.sqlite3") as database:
            self.assertIsNone(database.execute(
                "SELECT state FROM publication_intents WHERE intent_id=?",
                (intent["intent_id"],),
            ).fetchone())
        retried, retry = run_cli(*arguments)
        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertFalse(retry["data"]["idempotent"])
        committed_intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual("COMMITTED", committed_intent["state"])
        guarded, guard = run_cli(
            "acceptance-guard-eligibility", "--workspace-root", str(workspace),
        )
        self.assertEqual(0, guarded.returncode, guarded.stderr)
        self.assertTrue(guard["data"]["eligible"])

    def test_report_reconcile_recovers_every_post_control_publication_boundary(self) -> None:
        # scenario_id: report_post_control_projection_recovery
        # authority input: terminal Control Store authority and committed report intent
        # boundary: every immutable/root projection or file-intent write after that commit
        # rematerialized nodes: report bundle, execution projections, root projections, intent
        # expected first gate: public reconciliation completes the committed publication
        fault_points = (
            "after_report_control_commit",
            "after_report_canonical_write",
            "after_report_attempt_record_write",
            "after_report_repair_ledger_write",
            "after_report_execution_projection_write",
            "after_report_root_execution_projection_write",
            "after_report_root_report_projection_write",
            "after_report_root_ledger_projection_write",
            "after_report_intent_commit_write",
        )
        for fault_point in fault_points:
            with self.subTest(fault_point=fault_point):
                workspace, _ = self.prepare()
                self.commit_visual(workspace)
                failed, fault = run_cli(
                    "acceptance-materialize", "--workspace-root", str(workspace),
                    "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
                    "--materialized-at", "2026-08-02T00:20:00Z", "--fault-point", fault_point,
                )
                self.assertNotEqual(0, failed.returncode)
                self.assertEqual(fault_point, fault["data"]["fault_point"])

                current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
                execution_root = Path(current["execution_root"])
                intent_path = next((execution_root / "intents").glob("report-*.json"))
                if fault_point == "after_report_execution_projection_write":
                    self.assertEqual(
                        json.loads((workspace / "execution.json").read_text(encoding="utf-8"))["execution_revision"] + 1,
                        json.loads((execution_root / "execution.json").read_text(encoding="utf-8"))["execution_revision"],
                    )
                if fault_point == "after_report_root_execution_projection_write":
                    self.assertEqual("PREPARED", json.loads(intent_path.read_text(encoding="utf-8"))["state"])
                    self.assertEqual(
                        json.loads(intent_path.read_text(encoding="utf-8"))["expected_execution_revision"] + 1,
                        json.loads((workspace / "execution.json").read_text(encoding="utf-8"))["execution_revision"],
                    )

                reconciled, recovery = run_cli(
                    "acceptance-reconcile", "--workspace-root", str(workspace),
                )
                self.assertEqual(0, reconciled.returncode, reconciled.stderr)
                self.assertEqual(
                    [] if fault_point == "after_report_intent_commit_write" else ["committed_report"],
                    recovery["data"]["actions"],
                )
                self.assertTrue(recovery["data"]["report_published"])
                self.assertEqual("COMMITTED", json.loads(intent_path.read_text(encoding="utf-8"))["state"])
                self.assertEqual(
                    (workspace / "execution.json").read_bytes(),
                    (execution_root / "execution.json").read_bytes(),
                )
                stable_paths = (
                    workspace / "execution.json",
                    execution_root / "execution.json",
                    workspace / "acceptance_report.json",
                    workspace / "repair-ledger.json",
                    intent_path,
                )
                stable = {path: path.read_bytes() for path in stable_paths}
                repeated, repeat = run_cli(
                    "acceptance-reconcile", "--workspace-root", str(workspace),
                )
                self.assertEqual(0, repeated.returncode, repeated.stderr)
                self.assertEqual([], repeat["data"]["actions"])
                self.assertEqual(stable, {path: path.read_bytes() for path in stable_paths})

    def test_report_reconcile_rejects_equal_tampered_successor_projections(self) -> None:
        # scenario_id: report_equal_successor_projection_tamper
        # target invariant: N+1 execution must be the deterministic successor of SQLite-bound N
        # mutation seam: after both execution projections are written, before file-intent commit
        # intentionally stale node: SQLite prior_execution_sha256
        # expected first gate/code: publication_recovery/acceptance_execution_projection_stale
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        failed, _ = run_cli(
            "acceptance-materialize", "--workspace-root", str(workspace),
            "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
            "--materialized-at", "2026-08-02T00:20:00Z",
            "--fault-point", "after_report_root_execution_projection_write",
        )
        self.assertNotEqual(0, failed.returncode)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution_paths = (
            workspace / "execution.json",
            Path(current["execution_root"]) / "execution.json",
        )
        tampered = json.loads(execution_paths[0].read_text(encoding="utf-8"))
        tampered["prepared_at"] = "2026-08-02T00:00:01Z"
        tampered["execution_sha256"] = canonical_sha({
            key: value for key, value in tampered.items() if key != "execution_sha256"
        })
        for path in execution_paths:
            write_json(path, tampered)
        reconciled, envelope = run_cli(
            "acceptance-reconcile", "--workspace-root", str(workspace),
        )
        self.assertNotEqual(0, reconciled.returncode)
        self.assertEqual("publication_recovery", envelope["data"]["first_failing_gate"])
        self.assertEqual("acceptance_execution_projection_stale", envelope["data"]["error_code"])

    def test_report_bundle_recovery_and_terminal_retry_reject_companion_drift(self) -> None:
        scenarios = (
            ("after_report_publish", "attempt-record.json", "tamper"),
            ("after_report_publish", "repair-ledger.json", "tamper"),
            ("after_report_control_commit", "attempt-record.json", "missing"),
            ("after_report_control_commit", "repair-ledger.json", "missing"),
        )
        for fault_point, filename, action in scenarios:
            with self.subTest(fault_point=fault_point, filename=filename, action=action):
                workspace, _ = self.prepare()
                self.commit_visual(workspace)
                failed, _ = run_cli(
                    "acceptance-materialize", "--workspace-root", str(workspace),
                    "--provider-id", "acceptance-v2-provider", "--provider-version", "1.0.0",
                    "--materialized-at", "2026-08-02T00:20:00Z", "--fault-point", fault_point,
                )
                self.assertNotEqual(0, failed.returncode)
                current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
                intent = json.loads(next((Path(current["execution_root"]) / "intents").glob("report-*.json")).read_text(encoding="utf-8"))
                companion_path = Path(intent["staged_path"]).parent / filename
                if action == "missing":
                    quarantine = companion_path.parent / "待删除"
                    quarantine.mkdir(exist_ok=True)
                    companion_path.replace(quarantine / filename)
                else:
                    companion = json.loads(companion_path.read_text(encoding="utf-8"))
                    fingerprint_field = "attempt_record_sha256" if filename == "attempt-record.json" else "ledger_sha256"
                    companion["routing_state" if filename == "attempt-record.json" else "contract_gap_cycles"] = "drifted"
                    companion[fingerprint_field] = canonical_sha({key: value for key, value in companion.items() if key != fingerprint_field})
                    write_json(companion_path, companion)
                reconciled, envelope = run_cli("acceptance-reconcile", "--workspace-root", str(workspace))
                self.assertNotEqual(0, reconciled.returncode)
                self.assertEqual("publication_recovery", envelope["data"]["first_failing_gate"])

        for filename in ("attempt-record.json", "repair-ledger.json"):
            with self.subTest(terminal_retry=filename):
                workspace, _ = self.prepare()
                self.commit_visual(workspace)
                completed, _ = self.materialize(workspace)
                self.assertEqual(0, completed.returncode, completed.stderr)
                execution = json.loads((workspace / "execution.json").read_text(encoding="utf-8"))
                companion_path = Path(execution["report_publication"]["path"]).parent / filename
                companion = json.loads(companion_path.read_text(encoding="utf-8"))
                fingerprint_field = "attempt_record_sha256" if filename == "attempt-record.json" else "ledger_sha256"
                companion["routing_state" if filename == "attempt-record.json" else "contract_gap_cycles"] = "drifted"
                companion[fingerprint_field] = canonical_sha({key: value for key, value in companion.items() if key != fingerprint_field})
                write_json(companion_path, companion)
                retried, envelope = self.materialize(workspace)
                self.assertNotEqual(0, retried.returncode)
                self.assertEqual("report_freshness", envelope["data"]["first_failing_gate"])

    def test_guard_rejects_stale_report_bytes(self) -> None:
        workspace, _ = self.prepare()
        self.commit_visual(workspace)
        completed, _ = self.materialize(workspace)
        self.assertEqual(0, completed.returncode, completed.stderr)
        report = json.loads((workspace / "acceptance_report.json").read_text(encoding="utf-8"))
        report["materialized_at"] = "2020-01-01T00:00:00Z"
        report["report_sha256"] = canonical_sha({key: value for key, value in report.items() if key != "report_sha256"})
        write_json(workspace / "acceptance_report.json", report)
        execution = json.loads((workspace / "execution.json").read_text(encoding="utf-8"))
        immutable_report = Path(execution["report_publication"]["path"])
        write_json(immutable_report, report)
        execution["report_publication"]["report_sha256"] = report["report_sha256"]
        execution["execution_sha256"] = canonical_sha({key: value for key, value in execution.items() if key != "execution_sha256"})
        write_json(workspace / "execution.json", execution)
        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        write_json(Path(current["execution_root"]) / "execution.json", execution)
        guarded, envelope = run_cli("acceptance-guard-eligibility", "--workspace-root", str(workspace))
        self.assertEqual(0, guarded.returncode, guarded.stderr)
        self.assertFalse(envelope["data"]["eligible"])


if __name__ == "__main__":
    unittest.main()

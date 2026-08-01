from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests.video_workflow._test_run import new_case_dir
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore


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
        control_root = root.parent
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

    def build_binding(self, root: Path, generation: int, *, equivalent: bool = False, publish_authority: bool = True) -> Path:
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
        binding = {
            "schema_name": "acceptance-v2-input-binding", "schema_version": "1.0.0",
            "activation_status": "target_only", "input_track": "kernel",
            "binding_id": f"final-evidence-{generation}",
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
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        return workspace, root

    def patch(self, workspace: Path, *, decision: str = "pass", contract_gap: bool = False,
              omit_page: bool = False, read_set: list[dict] | None = None,
              fencing_token: str | None = None, cross_findings: list[dict] | None = None) -> Path:
        skeleton = json.loads((workspace / "acceptance_report.skeleton.json").read_text(encoding="utf-8"))
        task = skeleton["dimensions"]["visual_quality"]
        binding = json.loads((workspace / "input-binding.json").read_text(encoding="utf-8"))
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
        page_by_number = {item["page"]: item for item in binding["rendered_pages"]}
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
            "actual_read_set": read_set if read_set is not None else [
                {"logical_id": "final_pdf", "path": next(item["path"] for item in binding["artifacts"] if item["logical_id"] == "final_pdf"),
                 "sha256": next(item["sha256"] for item in binding["artifacts"] if item["logical_id"] == "final_pdf")},
                *[{"logical_id": f"rendered_page:{item['page']}", "path": item["path"], "sha256": item["sha256"]}
                  for item in binding["rendered_pages"]],
            ],
            "criterion_results": results, "visual_scan_evidence": {"pages_checked": pages},
            "cross_phase_findings": cross_findings or [],
            "contract_gaps": [{"gap_id": "gap-1", "observation": "unmapped evidence", "evidence_location": "final.pdf:1"}] if contract_gap else [],
        }
        patch["patch_sha256"] = canonical_sha(patch)
        return write_json(workspace.parent.parent / "visual-quality.patch.json", patch)

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
        self.assertFalse(guard["data"]["delivery_authority"])

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
        workspace, root = self.prepare()
        pass_patch = self.patch(workspace)
        pass_patch = write_json(root / "visual-pass.patch.json", json.loads(pass_patch.read_text(encoding="utf-8")))
        fail_patch = self.patch(workspace, decision="fail")
        fail_patch = write_json(root / "visual-fail.patch.json", json.loads(fail_patch.read_text(encoding="utf-8")))
        def commit(path: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
            return run_cli("acceptance-patch-commit", "--workspace-root", str(workspace),
                "--dimension", "visual_quality", "--patch", str(path), "--committed-at", "2026-08-02T00:10:00Z")
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(commit, (pass_patch, fail_patch)))
        self.assertEqual([0, 1], sorted(0 if item[0].returncode == 0 else 1 for item in outcomes))
        loser = next(envelope for completed, envelope in outcomes if completed.returncode != 0)
        self.assertEqual("patch_fencing", loser["data"]["first_failing_gate"])
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
        task["allowed_read_set"] = skeleton["dimensions"]["visual_quality"]["allowed_read_set"]
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
                    "--input-binding", str(unchanged), "--prepared-at", f"2026-08-02T00:{20 + attempt:02d}:00Z")
                self.assertNotEqual(0, rejected.returncode)
                self.assertEqual("repair_generation", rejection["data"]["first_failing_gate"])
                fresh = self.build_binding(root, attempt + 1)
                unrelated = json.loads(fresh.read_text(encoding="utf-8"))
                unrelated["run"]["run_id"] = "f" * 32
                unrelated["binding_sha256"] = canonical_sha({key: value for key, value in unrelated.items() if key != "binding_sha256"})
                unrelated_path = write_json(root / f"unrelated-binding-{attempt + 1}.json", unrelated)
                rejected, rejection = run_cli("acceptance-repair-prepare", "--workspace-root", str(workspace),
                    "--input-binding", str(unrelated_path), "--prepared-at", f"2026-08-02T00:{25 + attempt:02d}:00Z")
                self.assertNotEqual(0, rejected.returncode)
                self.assertEqual("run_lifecycle", rejection["data"]["first_failing_gate"])
                repaired, _ = run_cli("acceptance-repair-prepare", "--workspace-root", str(workspace),
                    "--input-binding", str(fresh), "--prepared-at", f"2026-08-02T00:{30 + attempt:02d}:00Z")
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
            "--input-binding", str(successor), "--prepared-at", "2026-08-02T00:30:00Z")
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

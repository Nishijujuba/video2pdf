from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import unittest

from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow import test_acceptance_v2 as acceptance_v2_tests
from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def _run(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    return completed, json.loads(completed.stdout)


class Issue43GlobalGateTests(unittest.TestCase):
    """Public Seam 3 and Seam 4 scenarios start from one coherent positive graph."""

    patch = acceptance_v2_tests.AcceptanceV2CliTests.patch
    commit_visual = acceptance_v2_tests.AcceptanceV2CliTests.commit_visual
    materialize = acceptance_v2_tests.AcceptanceV2CliTests.materialize

    def legacy_graph(self, root: Path | None = None, compile_wrapper: Path | None = None) -> tuple[Path, dict[str, Path]]:
        root = root or new_case_dir(self.id(), label="issue43-legacy")
        compile_wrapper = compile_wrapper or PROJECT_ROOT / ".agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py"
        kernel_fixture = acceptance_v2_tests.AcceptanceV2CliTests()
        kernel_binding_path = kernel_fixture.build_binding(root, 1)
        kernel_binding = json.loads(kernel_binding_path.read_text(encoding="utf-8"))
        final_pdf = Path(next(item["path"] for item in kernel_binding["artifacts"] if item["logical_id"] == "final_pdf"))
        main_tex = Path(next(item["path"] for item in kernel_binding["artifacts"] if item["logical_id"] == "main_tex"))
        pages_bound = kernel_binding["rendered_pages"]
        abandoned = root / "待删除" / "kernel-fixture-workflow"
        abandoned.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root / "workflow"), str(abandoned))
        criteria = PROJECT_ROOT / "docs/acceptance/acceptance_criteria.v1.json"
        dimension_map = _write(root / "review/acceptance/acceptance_dimension_map.json", {
            "schema_name": "acceptance-dimension-map", "schema_version": "1.0.0",
            "dimensions": ["text_quality", "visual_quality"],
        })
        manifest = _write(root / "review/acceptance/allowed_artifacts_manifest.json", {
            "schema_version": "1.0", "video_output_dir": str(root.resolve()),
            "criteria_file": "docs/acceptance/acceptance_criteria.v1.json",
            "final_artifacts": [
                {"role": "pdf", "path": final_pdf.relative_to(root).as_posix(), "sha256": _sha(final_pdf), "size_bytes": final_pdf.stat().st_size},
                {"role": "tex", "path": main_tex.relative_to(root).as_posix(), "sha256": _sha(main_tex), "size_bytes": main_tex.stat().st_size},
            ], "review_output_dir": "review/acceptance",
        })
        compile_report = _write(root / "review/latex/compile_report.json", {
            "schema_version": "latex_compile_report.v1", "mode": "final", "status": "passed",
            "producer": "compile_latex_ascii.py", "producer_contract": "latex_compile_guard.v1",
            "producer_mode": "final", "source_tex": str(main_tex.resolve()),
            "main_tex": str(main_tex.resolve()), "final_pdf": str(final_pdf.resolve()),
            "wrapper_script": str(compile_wrapper.resolve()),
            "wrapper_script_fingerprint": {"algorithm": "sha256", "sha256": _sha(compile_wrapper), "size_bytes": compile_wrapper.stat().st_size},
            "argv": ["--mode", "final"],
            "source_tex_fingerprint": {"algorithm": "sha256", "sha256": _sha(main_tex), "size_bytes": main_tex.stat().st_size},
            "final_pdf_fingerprint": {"algorithm": "sha256", "sha256": _sha(final_pdf), "size_bytes": final_pdf.stat().st_size},
        })
        pages = _write(root / "review/acceptance/rendered_pages_manifest.json", {
            "schema_name": "rendered-pages-manifest", "schema_version": "1.0.0",
            "final_pdf_sha256": _sha(final_pdf), "page_count": len(pages_bound),
            "pages": pages_bound,
        })
        quality_inputs = _write(root / "review/acceptance/legacy_quality_inputs.json", {
            "schema_name": "legacy-quality-inputs-manifest", "schema_version": "1.0.0",
            "quality_inputs": kernel_binding["quality_inputs"],
        })
        return root, {"pdf": final_pdf, "tex": main_tex, "criteria": criteria, "dimensions": dimension_map,
                      "manifest": manifest, "compile": compile_report, "pages": pages,
                      "quality_inputs": quality_inputs}

    def activate_gate(self, root: Path) -> Path:
        return acceptance_v2_tests.activate_test_global_gate(root)

    def adopt(self, root: Path, paths: dict[str, Path]) -> tuple[subprocess.CompletedProcess[str], dict]:
        if not (root / "active_global_gate.json").is_file():
            self.activate_gate(root)
        return _run(
            "legacy-acceptance-adopt", "--video-output-dir", str(root), "--final-pdf", str(paths["pdf"]),
            "--main-tex", str(paths["tex"]), "--allowed-artifacts-manifest", str(paths["manifest"]),
            "--compile-report", str(paths["compile"]), "--criteria", str(paths["criteria"]),
            "--dimension-map", str(paths["dimensions"]), "--rendered-pages-manifest", str(paths["pages"]),
            "--quality-inputs-manifest", str(paths["quality_inputs"]),
            "--control-store-root", str(root),
            "--adopted-at", "2026-08-02T00:00:00Z",
        )

    def test_legacy_adoption_materializes_a_fresh_run_record_free_input_set(self) -> None:
        root, paths = self.legacy_graph()
        completed, envelope = self.adopt(root, paths)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        value = json.loads(Path(envelope["data"]["input_set_path"]).read_text(encoding="utf-8"))
        self.assertEqual(value["activation_status"], "active_global_gate")
        self.assertEqual(value["input_track"], "legacy")
        self.assertNotIn("run", value)
        self.assertFalse((root / "workflow/run.json").exists())

    def test_legacy_adoption_rejects_stale_page_fingerprint_at_the_freshness_gate(self) -> None:
        # scenario_id: legacy_page_stale; single contradiction after page-manifest publication.
        root, paths = self.legacy_graph()
        manifest = json.loads(paths["pages"].read_text(encoding="utf-8"))
        manifest["pages"][0]["sha256"] = "0" * 64
        _write(paths["pages"], manifest)
        completed, envelope = self.adopt(root, paths)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(envelope["data"]["first_failing_gate"], "rendered_page_freshness")
        self.assertEqual(envelope["data"]["error_code"], "legacy_rendered_page_stale")

    def test_acceptance_prepare_uses_the_same_provider_for_legacy_binding(self) -> None:
        root, paths = self.legacy_graph()
        completed, adopted = self.adopt(root, paths)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        completed, prepared = _run(
            "acceptance-prepare", "--workspace-root", str(root / "review/acceptance"),
            "--input-binding", adopted["data"]["input_set_path"], "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:01:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        skeleton = json.loads(Path(prepared["data"]["skeleton_path"]).read_text(encoding="utf-8"))
        self.assertEqual(set(skeleton["dimensions"]), {"visual_quality"})
        self.assertEqual(skeleton["activation_status"], "active_global_gate")

    def test_run_record_free_legacy_completes_provider_chain_and_guard_eligibility(self) -> None:
        root, paths = self.legacy_graph()
        completed, adopted = self.adopt(root, paths)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        workspace = root / "review/acceptance"
        prepared, _ = _run(
            "acceptance-prepare", "--workspace-root", str(workspace),
            "--input-binding", adopted["data"]["input_set_path"], "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:01:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        self.commit_visual(workspace)
        materialized, _ = self.materialize(workspace)
        self.assertEqual(0, materialized.returncode, materialized.stdout + materialized.stderr)
        guarded, envelope = _run("acceptance-guard-eligibility", "--workspace-root", str(workspace))
        self.assertEqual(0, guarded.returncode, guarded.stdout + guarded.stderr)
        self.assertTrue(envelope["data"]["eligible"])
        report = json.loads((workspace / "acceptance_report.json").read_text(encoding="utf-8"))
        self.assertEqual("legacy", report["input_track"])
        self.assertNotIn("run_binding", report)
        self.assertFalse((root / "workflow/run.json").exists())

    def test_legacy_adoption_rejects_stale_precompile_owner_report_at_freshness_gate(self) -> None:
        # scenario_id: legacy_precompile_stale; single contradiction after quality manifest publication.
        root, paths = self.legacy_graph()
        manifest = json.loads(paths["quality_inputs"].read_text(encoding="utf-8"))
        report_path = Path(manifest["quality_inputs"]["precompile_quality_report"]["path"])
        report_path.write_text("{}\n", encoding="utf-8")
        completed, envelope = self.adopt(root, paths)
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("quality_input_freshness", envelope["data"]["first_failing_gate"])
        self.assertEqual("legacy_quality_input_stale", envelope["data"]["error_code"])

    def test_legacy_adoption_rejects_unsupported_quality_input_set_at_membership_gate(self) -> None:
        # scenario_id: legacy_quality_membership; single contradiction removes one required input.
        root, paths = self.legacy_graph()
        manifest = json.loads(paths["quality_inputs"].read_text(encoding="utf-8"))
        manifest["quality_inputs"].pop("text_origin_manifest")
        _write(paths["quality_inputs"], manifest)
        completed, envelope = self.adopt(root, paths)
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("quality_input_membership", envelope["data"]["first_failing_gate"])
        self.assertEqual("legacy_quality_input_incomplete", envelope["data"]["error_code"])

    def test_legacy_adoption_rejects_unsupported_precompile_report_identity_at_contract_gate(self) -> None:
        # scenario_id: legacy_precompile_identity; fingerprint rematerialized after one identity contradiction.
        root, paths = self.legacy_graph()
        manifest = json.loads(paths["quality_inputs"].read_text(encoding="utf-8"))
        report_path = Path(manifest["quality_inputs"]["precompile_quality_report"]["path"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["schema_name"] = "unsupported-precompile-quality-report"
        _write(report_path, report)
        manifest["quality_inputs"]["precompile_quality_report"]["sha256"] = _sha(report_path)
        _write(paths["quality_inputs"], manifest)
        completed, envelope = self.adopt(root, paths)
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("quality_input_contract", envelope["data"]["first_failing_gate"])
        self.assertEqual("legacy_quality_input_contract_invalid", envelope["data"]["error_code"])

    def test_legacy_guard_eligibility_rejects_stale_adopted_artifact_without_key_error(self) -> None:
        # scenario_id: legacy_post_materialize_artifact_stale; single contradiction after report publication.
        root, paths = self.legacy_graph()
        completed, adopted = self.adopt(root, paths)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        workspace = root / "review/acceptance"
        prepared, _ = _run(
            "acceptance-prepare", "--workspace-root", str(workspace),
            "--input-binding", adopted["data"]["input_set_path"], "--attempt-number", "1",
            "--prepared-at", "2026-08-02T00:01:00Z",
            "--coordinator-session", "coordinator-session",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        self.commit_visual(workspace)
        materialized, _ = self.materialize(workspace)
        self.assertEqual(0, materialized.returncode, materialized.stdout + materialized.stderr)
        paths["tex"].write_text("stale after Acceptance Report publication\n", encoding="utf-8")
        guarded, envelope = _run("acceptance-guard-eligibility", "--workspace-root", str(workspace))
        self.assertNotEqual(0, guarded.returncode)
        self.assertEqual("input_freshness", envelope["data"]["first_failing_gate"])
        self.assertEqual("acceptance_input_stale", envelope["data"]["error_code"])

    def test_global_gate_activation_is_cas_idempotent_and_preserves_kernel_authority(self) -> None:
        root = new_case_dir(self.id(), label="issue43-cutover")
        manifest = self.activate_gate(root)
        repository = manifest.parents[2]
        publisher = GlobalGatePublisher(project_root=repository)
        first = publisher.activate(control_store_root=root, exit_evidence=manifest, activated_at="2026-08-02T00:00:00Z")
        second = publisher.activate(control_store_root=root, exit_evidence=manifest, activated_at="2026-08-02T00:00:00Z")
        self.assertTrue(second["idempotent"])
        authority = json.loads(Path(first["authority_path"]).read_text(encoding="utf-8"))
        self.assertEqual(authority["active_global_gate"], "acceptance_report_v2")
        self.assertEqual(authority["platform_kernel_authority"], "unchanged")


if __name__ == "__main__":
    unittest.main()

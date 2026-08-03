from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from tests.video_workflow._test_run import new_case_dir


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

    def legacy_graph(self) -> tuple[Path, dict[str, Path]]:
        root = new_case_dir(self.id(), label="issue43-legacy")
        final_pdf = _write(root / "final.pdf", b"%PDF-legacy")
        main_tex = _write(root / "main.tex", b"legacy tex")
        page = _write(root / "review/acceptance/rendered_pages/page_0001.png", b"png")
        criteria = PROJECT_ROOT / "docs/acceptance/acceptance_criteria.v1.json"
        dimension_map = _write(root / "review/acceptance/acceptance_dimension_map.json", {
            "schema_name": "acceptance-dimension-map", "schema_version": "1.0.0",
            "dimensions": ["text_quality", "visual_quality"],
        })
        manifest = _write(root / "review/acceptance/allowed_artifacts_manifest.json", {
            "schema_version": "1.0", "video_output_dir": str(root.resolve()),
            "criteria_file": "docs/acceptance/acceptance_criteria.v1.json",
            "final_artifacts": [
                {"role": "pdf", "path": "final.pdf", "sha256": _sha(final_pdf), "size_bytes": final_pdf.stat().st_size},
                {"role": "tex", "path": "main.tex", "sha256": _sha(main_tex), "size_bytes": main_tex.stat().st_size},
            ], "review_output_dir": "review/acceptance",
        })
        compile_report = _write(root / "review/latex/compile_report.json", {
            "schema_version": "latex_compile_report.v1", "mode": "final", "status": "passed",
            "producer": "compile_latex_ascii.py", "producer_contract": "latex_compile_guard.v1",
            "producer_mode": "final", "source_tex": str(main_tex.resolve()),
            "main_tex": str(main_tex.resolve()), "final_pdf": str(final_pdf.resolve()),
            "source_tex_fingerprint": {"algorithm": "sha256", "sha256": _sha(main_tex), "size_bytes": main_tex.stat().st_size},
            "final_pdf_fingerprint": {"algorithm": "sha256", "sha256": _sha(final_pdf), "size_bytes": final_pdf.stat().st_size},
        })
        pages = _write(root / "review/acceptance/rendered_pages_manifest.json", {
            "schema_name": "rendered-pages-manifest", "schema_version": "1.0.0",
            "final_pdf_sha256": _sha(final_pdf), "page_count": 1,
            "pages": [{"page": 1, "path": str(page.resolve()), "sha256": _sha(page)}],
        })
        return root, {"pdf": final_pdf, "tex": main_tex, "criteria": criteria, "dimensions": dimension_map,
                      "manifest": manifest, "compile": compile_report, "pages": pages}

    def activate_gate(self, root: Path) -> Path:
        mirror_source = _write(root / "policy-source.txt", b"active-global-gate")
        mirror_target = _write(root / "policy-mirror.txt", b"active-global-gate")
        members = [
            "catalogs", "projections", "criteria_migration", "schemas", "providers", "validators",
            "hooks", "skills", "project_instructions", "mirrors", "tests", "activation_documentation",
        ]
        manifest = _write(root / "exit-evidence.json", {
            "schema_name": "global-gate-exit-evidence", "schema_version": "1.0.0",
            "cutover": "global_acceptance_v2", "overall_decision": "pass",
            "atomic_members": members,
            "atomic_member_status": {member: "active" for member in members},
            "mirror_checks": [{"source_path": str(mirror_source.resolve()), "mirror_path": str(mirror_target.resolve()),
                               "source_sha256": _sha(mirror_source), "mirror_sha256": _sha(mirror_target), "status": "equal"}],
            "policy_status": "active_global_gate",
            "results": {"kernel_v2_pass": True, "legacy_v2_pass": True, "v1_rejected": True,
                        "fallback_rejected": True, "translation_rejected": True, "dual_authority_rejected": True,
                        "contract_gap_rejected": True, "unsupported_identity_rejected": True,
                        "synthetic_legacy_run_rejected": True},
        })
        completed, _ = _run("global-gate-activate", "--control-store-root", str(root),
                            "--exit-evidence", str(manifest), "--activated-at", "2026-08-02T00:00:00Z")
        self.assertEqual(completed.returncode, 0, completed.stdout)
        return manifest

    def adopt(self, root: Path, paths: dict[str, Path]) -> tuple[subprocess.CompletedProcess[str], dict]:
        if not (root / "active_global_gate.json").is_file():
            self.activate_gate(root)
        return _run(
            "legacy-acceptance-adopt", "--video-output-dir", str(root), "--final-pdf", str(paths["pdf"]),
            "--main-tex", str(paths["tex"]), "--allowed-artifacts-manifest", str(paths["manifest"]),
            "--compile-report", str(paths["compile"]), "--criteria", str(paths["criteria"]),
            "--dimension-map", str(paths["dimensions"]), "--rendered-pages-manifest", str(paths["pages"]),
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
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        skeleton = json.loads(Path(prepared["data"]["skeleton_path"]).read_text(encoding="utf-8"))
        self.assertEqual(set(skeleton["dimensions"]), {"text_quality", "visual_quality"})
        self.assertEqual(skeleton["activation_status"], "active_global_gate")

    def test_global_gate_activation_is_cas_idempotent_and_preserves_kernel_authority(self) -> None:
        root = new_case_dir(self.id(), label="issue43-cutover")
        manifest = self.activate_gate(root)
        completed, first = _run("global-gate-activate", "--control-store-root", str(root),
                                "--exit-evidence", str(manifest), "--activated-at", "2026-08-02T00:00:00Z")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        completed, second = _run("global-gate-activate", "--control-store-root", str(root),
                                 "--exit-evidence", str(manifest), "--activated-at", "2026-08-02T00:00:00Z")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(second["data"]["idempotent"])
        authority = json.loads(Path(first["data"]["authority_path"]).read_text(encoding="utf-8"))
        self.assertEqual(authority["active_global_gate"], "acceptance_report_v2")
        self.assertEqual(authority["platform_kernel_authority"], "unchanged")


if __name__ == "__main__":
    unittest.main()

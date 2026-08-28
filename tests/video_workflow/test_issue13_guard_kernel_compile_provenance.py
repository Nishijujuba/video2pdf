from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tests.video_workflow._test_run import new_case_dir  # noqa: E402
from video2pdf_workflow_kernel.guarded_delivery import (  # noqa: E402
    _load_active_delivery_guard,
)
from video2pdf_workflow_kernel.utils import canonical_json_bytes  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))
    return path


class Issue13GuardKernelCompileProvenanceTests(unittest.TestCase):
    """Kernel compile provenance is proven only for the kernel report contract.

    The active Final Delivery Guard's compile-provenance gate must accept the
    Kernel ``final-compile-report/1.0.0`` contract for ``kernel_authority``
    targets instead of demanding the Legacy ``latex_compile_report.v1`` schema.
    """

    def _kernel_target(self) -> tuple[object, object, Path, Path, Path]:
        guard = _load_active_delivery_guard(PROJECT_ROOT)
        case_dir = new_case_dir(self.id(), label="issue13-guard-kernel-compile")
        video_dir = case_dir / "video"
        (video_dir / "待删除").mkdir(parents=True)
        main_tex = video_dir / "work" / "integration" / "main.tex"
        main_tex.parent.mkdir(parents=True, exist_ok=True)
        main_tex.write_text("\\documentclass{article}\n", encoding="utf-8")
        final_pdf = (
            video_dir
            / "review"
            / "final-compile-v8"
            / "workspace"
            / "adapter-output"
            / "final.pdf"
        )
        final_pdf.parent.mkdir(parents=True, exist_ok=True)
        final_pdf.write_bytes(b"%PDF-1.7\nkernel final pdf\n")
        report_path = (
            video_dir
            / "review"
            / "final-compile-v8"
            / "workspace"
            / "final-compile-report.json"
        )
        target = guard.DeliveryTarget(
            project_root=PROJECT_ROOT,
            current_target_path=case_dir / "current.json",
            current_target={"schema_name": "kernel-session-delivery-target"},
            video_target={"schema_name": "kernel-delivery-target"},
            video_output_dir=video_dir,
            target_file=video_dir / "review" / "acceptance" / "delivery_target.json",
            final_pdf=final_pdf,
            main_tex=main_tex,
            manifest_path=video_dir / "review" / "acceptance" / "allowed_artifacts_manifest.json",
            acceptance_report_path=video_dir / "review" / "acceptance" / "acceptance_report.json",
            guard_report_path=video_dir / "review" / "acceptance" / "delivery_guard_report.json",
            compile_report_path=report_path,
            global_gate_authority_path=PROJECT_ROOT / "workspace" / "active_global_gate.json",
            global_gate_authority_sha256="0" * 64,
            attempt_limit=3,
            stage="accepted",
            final_pdf_relative="review/final-compile-v8/workspace/adapter-output/final.pdf",
            main_tex_relative="work/integration/main.tex",
            manifest_relative="review/acceptance/allowed_artifacts_manifest.json",
            acceptance_report_relative="review/acceptance/acceptance_report.json",
            guard_report_relative="review/acceptance/delivery_guard_report.json",
            compile_report_relative="review/final-compile-v8/workspace/final-compile-report.json",
            target_file_relative="review/acceptance/delivery_target.json",
            compile_provenance_required=True,
            legacy_existing_pdf=False,
            recompiled=False,
            kernel_authority=True,
        )
        return guard, target, report_path, main_tex, final_pdf

    def _valid_kernel_report(self, main_tex: Path, final_pdf: Path) -> dict:
        report = {
            "schema_name": "final-compile-report",
            "schema_version": "1.0.0",
            "mode": "final",
            "status": "pass",
            "delivery_authority": False,
            "precompile_text_seal_sha256": "1" * 64,
            "final_artifact_seal_sha256": "2" * 64,
            "compile_manifest_sha256": "3" * 64,
            "dependency_closure": {
                "complete": True,
                "inputs": [
                    {
                        "logical_id": "integrated_main",
                        "generation": 1,
                        "sha256": _sha256(main_tex),
                    }
                ],
                "runtime_inputs": [],
                "generated_inputs": [],
                "recorder_sha256": "4" * 64,
                "recorder_path": "adapter-output/compile-recorder.fls",
            },
            "pdf": {
                "path": "adapter-output/final.pdf",
                "sha256": _sha256(final_pdf),
                "size": final_pdf.stat().st_size,
            },
            "compiler_provider": {
                "provider_id": "guarded-final-compile-provider",
                "provider_sha256": hashlib.sha256(
                    (
                        PROJECT_ROOT
                        / "src/video2pdf_workflow_kernel/final_compile.py"
                    ).read_bytes()
                ).hexdigest(),
            },
            "compile_adapter": {
                "adapter_path": str(
                    (PROJECT_ROOT / "scripts/guarded_final_compile_adapter.py").resolve()
                ),
                "adapter_sha256": "5" * 64,
                "protocol_version": "guarded-final-compile-v2",
            },
            "text_origin_plan_sha256": "6" * 64,
            "render_evidence_manifest_sha256": "7" * 64,
            "rendered_text_inventory_sha256": "8" * 64,
            "text_origin_manifest_sha256": "9" * 64,
        }
        report["report_sha256"] = hashlib.sha256(
            canonical_json_bytes(
                {key: value for key, value in report.items() if key != "report_sha256"}
            )
        ).hexdigest()
        return report

    def test_kernel_compile_report_passes_the_active_guard_provenance_gate(
        self,
    ) -> None:
        guard, target, report_path, main_tex, final_pdf = self._kernel_target()
        _write_json(report_path, self._valid_kernel_report(main_tex, final_pdf))
        # scenario_id: kernel_compile_provenance_current
        # target_invariant: kernel final-compile-report/1.0.0 satisfies the gate
        # mutation_seam: none (positive); rematerialized_nodes: none
        # expected_first_gate/code: none — must not raise
        guard._ensure_compile_provenance(target)

    def test_kernel_compile_provenance_rejects_legacy_schema_demand(self) -> None:
        guard, target, report_path, main_tex, final_pdf = self._kernel_target()
        report = self._valid_kernel_report(main_tex, final_pdf)
        report["schema_version"] = "1.0"
        _write_json(report_path, report)
        # scenario_id: kernel_report_wrong_schema_version
        # target_invariant: kernel report schema_version must be 1.0.0
        # mutation_seam: schema_version; rematerialized_nodes: none
        # intentionally_stale_nodes: schema_version only
        # expected_first_gate/code: guard / compile provenance
        with self.assertRaisesRegex(
            Exception, "final compile report schema_version"
        ):
            guard._ensure_compile_provenance(target)

    def _recompute_report_sha(self, report: dict) -> dict:
        """Rematerialize the derived fingerprint after a nested-field mutation."""
        report["report_sha256"] = hashlib.sha256(
            canonical_json_bytes(
                {
                    key: value
                    for key, value in report.items()
                    if key != "report_sha256"
                }
            )
        ).hexdigest()
        return report

    def test_kernel_compile_provenance_rejects_stale_pdf_binding(self) -> None:
        guard, target, report_path, main_tex, final_pdf = self._kernel_target()
        report = self._valid_kernel_report(main_tex, final_pdf)
        report["pdf"] = {
            "path": "adapter-output/final.pdf",
            "sha256": "f" * 64,
            "size": final_pdf.stat().st_size,
        }
        self._recompute_report_sha(report)
        _write_json(report_path, report)
        # scenario_id: kernel_report_stale_pdf_fingerprint
        # target_invariant: report pdf.sha256 must match the current final PDF
        # mutation_seam: pdf.sha256; rematerialized_nodes: report_sha256
        # intentionally_stale_nodes: pdf.sha256 only
        # expected_first_gate/code: guard / compile provenance
        with self.assertRaisesRegex(Exception, "final_pdf_fingerprint"):
            guard._ensure_compile_provenance(target)

    def test_kernel_compile_provenance_rejects_stale_tex_binding(self) -> None:
        guard, target, report_path, main_tex, final_pdf = self._kernel_target()
        report = self._valid_kernel_report(main_tex, final_pdf)
        report["dependency_closure"]["inputs"] = [
            {
                "logical_id": "integrated_main",
                "generation": 1,
                "sha256": "e" * 64,
            }
        ]
        self._recompute_report_sha(report)
        _write_json(report_path, report)
        # scenario_id: kernel_report_stale_tex_fingerprint
        # target_invariant: dependency input sha must match the current main.tex
        # mutation_seam: dependency_closure.inputs[0].sha256
        # rematerialized_nodes: report_sha256
        # intentionally_stale_nodes: tex sha only
        # expected_first_gate/code: guard / compile provenance
        with self.assertRaisesRegex(Exception, "source_tex"):
            guard._ensure_compile_provenance(target)

    def test_kernel_compile_provenance_rejects_stale_provider(self) -> None:
        guard, target, report_path, main_tex, final_pdf = self._kernel_target()
        report = self._valid_kernel_report(main_tex, final_pdf)
        report["compiler_provider"] = {
            "provider_id": "guarded-final-compile-provider",
            "provider_sha256": "d" * 64,
        }
        self._recompute_report_sha(report)
        _write_json(report_path, report)
        # scenario_id: kernel_report_stale_provider
        # target_invariant: provider sha256 must match the current final_compile.py
        # mutation_seam: compiler_provider.provider_sha256
        # rematerialized_nodes: report_sha256
        # intentionally_stale_nodes: provider only
        # expected_first_gate/code: guard / compile provenance
        with self.assertRaisesRegex(Exception, "compiler_provider"):
            guard._ensure_compile_provenance(target)


if __name__ == "__main__":
    unittest.main()

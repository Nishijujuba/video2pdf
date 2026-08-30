from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

import fitz

from tests.video_workflow import test_guarded_final_compile_adapter as final_compile_tests
from tests.video_workflow._test_run import new_case_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD_SCRIPTS = (
    PROJECT_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "scripts"
)
if str(GUARD_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(GUARD_SCRIPTS))

import delivery_guard as delivery_guard_module


class Issue94RenderedPageAuthorityTests(unittest.TestCase):
    def test_final_compile_materializes_staged_rendered_pages(self) -> None:
        fixture = final_compile_tests.GuardedFinalCompileProviderAuthorityTests(
            methodName="test_public_final_compile_allows_unread_governance_entries"
        )
        fixture.setUp()

        workspace = fixture._run_public_final_compile_fixture(via_cli=True)

        video_root = workspace.parents[1]
        manifest_path = workspace / "render-evidence-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_page = workspace / "adapter-output" / "rendered_pages" / "page_001.png"
        self.assertTrue(expected_page.is_file())
        self.assertEqual(
            [{"page": 1, "path": "adapter-output/rendered_pages/page_001.png", "sha256": manifest["pages"][0]["sha256"]}],
            manifest["pages"],
        )
        self.assertEqual(
            [],
            list(
                (
                    video_root
                    / "review"
                    / "acceptance"
                    / "rendered_pages"
                ).glob("page_*.png")
            ),
        )

    def test_kernel_multi_page_guard_passes_without_coordinator_page_copy(self) -> None:
        video_root = new_case_dir(self.id(), label="issue94-guard") / "video"
        acceptance_root = video_root / "review" / "acceptance"
        rendered_root = acceptance_root / "rendered_pages"
        rendered_root.mkdir(parents=True)
        final_pdf = video_root / "final.pdf"
        document = fitz.open()
        for page_number in (1, 2):
            page = document.new_page(width=300, height=300)
            page.insert_text((72, 72), f"Page {page_number}")
        document.save(final_pdf)
        document.close()
        rendered_pages = []
        for page_number in (1, 2):
            page_path = rendered_root / f"page_{page_number:04d}.png"
            page_path.write_bytes(f"page-{page_number}".encode())
            rendered_pages.append(
                {
                    "page": page_number,
                    "path": str(page_path),
                    "sha256": hashlib.sha256(page_path.read_bytes()).hexdigest(),
                }
            )
        (acceptance_root / "input-binding.json").write_text(
            json.dumps({"rendered_pages": rendered_pages}),
            encoding="utf-8",
        )
        target = delivery_guard_module.DeliveryTarget(
            project_root=video_root.parent,
            current_target_path=video_root / "current.json",
            current_target={},
            video_target={},
            video_output_dir=video_root,
            target_file=acceptance_root / "delivery_target.json",
            final_pdf=final_pdf,
            main_tex=video_root / "main.tex",
            manifest_path=acceptance_root / "allowed_artifacts_manifest.json",
            acceptance_report_path=acceptance_root / "acceptance_report.json",
            guard_report_path=acceptance_root / "delivery_guard_report.json",
            compile_report_path=video_root / "review" / "latex" / "compile_report.json",
            global_gate_authority_path=video_root.parent / "active_global_gate.json",
            global_gate_authority_sha256="0" * 64,
            attempt_limit=3,
            stage="accepted",
            final_pdf_relative="final.pdf",
            main_tex_relative="main.tex",
            manifest_relative="review/acceptance/allowed_artifacts_manifest.json",
            acceptance_report_relative="review/acceptance/acceptance_report.json",
            guard_report_relative="review/acceptance/delivery_guard_report.json",
            compile_report_relative="review/latex/compile_report.json",
            target_file_relative="review/acceptance/delivery_target.json",
            compile_provenance_required=True,
            legacy_existing_pdf=False,
            recompiled=True,
            kernel_authority=True,
        )

        delivery_guard_module._ensure_rendered_page_coverage(target)

        binding_path = acceptance_root / "input-binding.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["rendered_pages"][1] = dict(binding["rendered_pages"][0])
        binding["rendered_pages"][1]["page"] = 2
        binding_path.write_text(json.dumps(binding), encoding="utf-8")
        with self.assertRaises(delivery_guard_module.GuardError) as raised:
            delivery_guard_module._ensure_rendered_page_coverage(target)
        self.assertEqual("rendered_page_authority", raised.exception.first_failing_gate)
        self.assertEqual(
            "rendered_page_authority_mismatch", raised.exception.error_code
        )


if __name__ == "__main__":
    unittest.main()

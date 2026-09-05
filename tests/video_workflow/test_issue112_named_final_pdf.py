from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock
import unittest

from tests.video_workflow.test_guarded_final_compile_adapter import (
    ADAPTER,
    PROJECT_ROOT,
    GuardedFinalCompileProviderAuthorityTests,
)

from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.final_compile import GuardedFinalCompileProvider


NAMED_PDF = "协同先于智能_让芯片设计团队像一个身体一样行动.pdf"


class Issue112NamedFinalPdfTests(unittest.TestCase):
    def _completed_baseline(self) -> tuple[Path, dict[str, object]]:
        fixture = GuardedFinalCompileProviderAuthorityTests(
            methodName=(
                "test_public_final_compile_allows_unread_governance_"
                "and_registered_runtime_inputs"
            )
        )
        fixture.setUp()
        workspace = fixture._run_public_final_compile_fixture()
        request = json.loads(
            (workspace / "compile-request.json").read_text(encoding="utf-8")
        )
        return workspace, request

    def _compile(
        self,
        *,
        request: dict[str, object],
        workspace: Path,
        pdf_basename: str,
    ) -> dict[str, object]:
        inventory_path = Path(str(request["reader_facing_text_inventory_path"]))
        quality = inventory_path.parents[2]
        with mock.patch(
            "video2pdf_workflow_kernel.final_compile.sha256_git_blob",
            return_value=hashlib.sha256(ADAPTER.read_bytes()).hexdigest(),
        ):
            return GuardedFinalCompileProvider(PROJECT_ROOT).compile(
                input_track="kernel",
                precompile_workspace_root=quality,
                compile_manifest_path=Path(str(request["compile_manifest_path"])),
                compiler_adapter_path=ADAPTER,
                workspace_root=workspace,
                compiled_at="2026-09-06T12:00:00+08:00",
                runtime_policy_path=Path(str(request["runtime_policy_path"])),
                pdf_basename=pdf_basename,
            )

    def test_public_provider_and_adapter_preserve_named_pdf_identity_and_default(self) -> None:
        baseline, baseline_request = self._completed_baseline()
        baseline_report = json.loads(
            (baseline / "final-compile-report.json").read_text(encoding="utf-8")
        )
        self.assertEqual("adapter-output/final.pdf", baseline_report["pdf"]["path"])
        self.assertTrue((baseline / "adapter-output/final.pdf").is_file())

        named_workspace = baseline.parent / "issue112-named-final"
        result = self._compile(
            request=baseline_request,
            workspace=named_workspace,
            pdf_basename=NAMED_PDF,
        )
        operation = json.loads(
            (named_workspace / "final-compile-operation.json").read_text(encoding="utf-8")
        )
        named_request = json.loads(
            (named_workspace / "compile-request.json").read_text(encoding="utf-8")
        )
        seal = json.loads(
            (named_workspace / "final-artifact-seal.json").read_text(encoding="utf-8")
        )
        report = json.loads(
            (named_workspace / "final-compile-report.json").read_text(encoding="utf-8")
        )
        expected_relative = f"adapter-output/{NAMED_PDF}"
        expected_path = named_workspace / expected_relative
        self.assertEqual(NAMED_PDF, operation["pdf_basename"])
        self.assertEqual(NAMED_PDF, named_request["pdf_basename"])
        self.assertEqual(expected_relative, seal["final_pdf"]["path"])
        self.assertEqual(seal["final_pdf"], report["pdf"])
        self.assertEqual(str(expected_path), result["final_pdf_path"])
        self.assertTrue(expected_path.is_file())
        self.assertFalse((named_workspace / "adapter-output/final.pdf").exists())

        replay = self._compile(
            request=baseline_request,
            workspace=named_workspace,
            pdf_basename=NAMED_PDF,
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(result["operation_id"], replay["operation_id"])
        self.assertEqual(str(expected_path), replay["final_pdf_path"])

    def test_changed_pdf_basename_cannot_reuse_completed_operation(self) -> None:
        """Change only the public basename after a valid completed publication.

        The provider derives the new operation identity; the old publication is
        intentionally retained. No fixture node is rewritten or rematerialized.
        The existing-publication gate must reject with final_compile_interrupted.
        """
        baseline, request = self._completed_baseline()
        workspace = baseline.parent / "issue112-replay-identity"
        first = self._compile(
            request=request,
            workspace=workspace,
            pdf_basename=NAMED_PDF,
        )
        with self.assertRaisesRegex(ContractError, "conflicts with this operation") as raised:
            self._compile(
                request=request,
                workspace=workspace,
                pdf_basename="另一个标题.pdf",
            )
        self.assertNotEqual(first["operation_id"], raised.exception.data["operation_id"])
        self.assertEqual("final_compile_interrupted", raised.exception.data["error_code"])

    def test_invalid_pdf_basename_fails_before_adapter_execution(self) -> None:
        """Change only the basename input of an otherwise valid compile graph.

        The public input is the mutation seam. No nodes are stale or require
        rematerialization because validation must precede workspace publication.
        Expected gate/code: final_compile_pdf_basename /
        final_compile_pdf_basename_invalid.
        """
        baseline, request = self._completed_baseline()
        workspace = baseline.parent / "issue112-invalid-name"
        with mock.patch("video2pdf_workflow_kernel.final_compile.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ContractError, "normalized PDF basename") as raised:
                self._compile(
                    request=request,
                    workspace=workspace,
                    pdf_basename="../unsealed-copy.pdf",
                )
        self.assertEqual("final_compile_pdf_basename", raised.exception.data["first_failing_gate"])
        self.assertEqual("final_compile_pdf_basename_invalid", raised.exception.data["error_code"])
        popen.assert_not_called()
        self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()

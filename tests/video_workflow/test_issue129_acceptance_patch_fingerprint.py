from __future__ import annotations

"""Issue #129 provider-owned Patch fingerprint fixture migration.

Graph: Task Envelope and active Reviewer Claim (authority inputs) -> reviewer
staging file (semantic submission) -> normalized canonical staging bytes
(derived node) -> Patch publication intent and committed Patch (boundaries) ->
execution and Claim projections (observations).

Positive fixtures omit the provider-owned ``patch_sha256`` field and use the
public commit and reconcile commands. Negative fixtures start from the same
coherent graph, introduce one declared contradiction, and assert its first
stable validation gate. Historical fingerprinted Patch fixtures and canonical
committed-Patch validation remain unchanged.
"""

import json
from pathlib import Path
import unittest

from tests.video_workflow import test_acceptance_v2 as acceptance_tests
from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow.test_issue13_run_initialization import (
    _write_current_global_gate,
)
from video2pdf_workflow_kernel.acceptance_v2 import AcceptanceV2Provider
from video2pdf_workflow_kernel.global_gate import (
    REQUIRED_ACCEPTANCE_QUALITY_INPUTS,
)


class Issue129AcceptancePatchFingerprintTests(unittest.TestCase):
    def _prepare(
        self,
    ) -> tuple[acceptance_tests.AcceptanceV2CliTests, Path, Path]:
        case_root = new_case_dir(self.id(), label="issue129-legacy-acceptance")
        video_root = case_root / "video"
        workspace = video_root / "review" / "acceptance"
        control_root = case_root / "control-store"
        control_root.mkdir(parents=True)
        _write_current_global_gate(control_root)
        final_pdf = video_root / "artifacts" / "final.pdf"
        main_tex = video_root / "artifacts" / "main.tex"
        page = workspace / "rendered_pages" / "page_0001.png"
        final_pdf.parent.mkdir(parents=True)
        page.parent.mkdir(parents=True)
        final_pdf.write_bytes(b"issue129-pdf")
        main_tex.write_bytes(b"issue129-tex")
        page.write_bytes(b"issue129-page")
        quality_inputs: dict[str, dict[str, str]] = {}
        for logical_id in sorted(REQUIRED_ACCEPTANCE_QUALITY_INPUTS):
            path = video_root / "quality" / f"{logical_id}.json"
            acceptance_tests.write_json(path, {"fixture": logical_id})
            quality_inputs[logical_id] = {
                "path": str(path.resolve()),
                "sha256": acceptance_tests.file_sha(path),
            }
        quality_manifest_path = acceptance_tests.write_json(
            video_root / "review" / "acceptance" / "legacy-quality-inputs.json",
            {"quality_inputs": quality_inputs},
        )
        allowed_path = acceptance_tests.write_json(
            workspace / "allowed_artifacts_manifest.json",
            {"fixture": "issue129-allowed"},
        )
        compile_path = acceptance_tests.write_json(
            video_root / "review" / "latex" / "compile-report.json",
            {"fixture": "issue129-compile"},
        )
        dimension_path = acceptance_tests.write_json(
            workspace / "acceptance-dimension-map.json",
            {"fixture": "issue129-dimensions"},
        )
        criteria_path = (
            acceptance_tests.PROJECT_ROOT
            / "docs"
            / "acceptance"
            / "acceptance_criteria.v1.json"
        )
        binding = {
            "schema_name": "legacy-acceptance-input-set",
            "schema_version": "1.0.0",
            "activation_status": "active_global_gate",
            "input_track": "legacy",
            "input_set_id": "issue129-legacy-input",
            "video_output_dir": str(video_root.resolve()),
            "artifacts": [
                {
                    "logical_id": "final_pdf",
                    "path": str(final_pdf.resolve()),
                    "sha256": acceptance_tests.file_sha(final_pdf),
                },
                {
                    "logical_id": "main_tex",
                    "path": str(main_tex.resolve()),
                    "sha256": acceptance_tests.file_sha(main_tex),
                },
            ],
            "quality_inputs_manifest": {
                "path": str(quality_manifest_path.resolve()),
                "sha256": acceptance_tests.file_sha(quality_manifest_path),
            },
            "quality_inputs": quality_inputs,
            "allowed_artifacts_manifest": {
                "path": str(allowed_path.resolve()),
                "sha256": acceptance_tests.file_sha(allowed_path),
            },
            "compile_provenance": {
                "path": str(compile_path.resolve()),
                "sha256": acceptance_tests.file_sha(compile_path),
            },
            "acceptance_criteria": {
                "path": str(criteria_path.resolve()),
                "sha256": acceptance_tests.file_sha(criteria_path),
            },
            "acceptance_dimension_map": {
                "path": str(dimension_path.resolve()),
                "sha256": acceptance_tests.file_sha(dimension_path),
            },
            "rendered_pages": {
                "page_count": 1,
                "pages": [
                    {
                        "page": 1,
                        "path": str(page.resolve()),
                        "sha256": acceptance_tests.file_sha(page),
                    }
                ],
            },
            "provider": {"provider_id": "legacy-acceptance-adoption-provider"},
            "invocation": {"command": "issue129-fixture"},
            "adopted_at": "2026-09-06T11:00:00Z",
            "global_gate_authority": AcceptanceV2Provider(
                acceptance_tests.PROJECT_ROOT
            ).require_current_global_gate(control_store_root=control_root),
        }
        binding["input_set_sha256"] = acceptance_tests.canonical_sha(binding)
        binding_path = acceptance_tests.write_json(
            workspace / "legacy-acceptance-input-set.json",
            binding,
        )
        prepared, envelope = acceptance_tests.run_cli(
            "acceptance-prepare",
            "--workspace-root",
            str(workspace),
            "--input-binding",
            str(binding_path),
            "--attempt-number",
            "1",
            "--prepared-at",
            "2026-09-06T11:01:00Z",
            "--coordinator-session",
            "session-issue129",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        self.assertEqual("active_global_gate", envelope["data"]["activation_status"])
        fixture = acceptance_tests.AcceptanceV2CliTests(
            methodName="test_prepare_materializes_exact_read_only_reviewer_task_envelope"
        )
        fixture.id = self.id  # type: ignore[method-assign]
        return fixture, workspace, video_root

    @staticmethod
    def _remove_provider_fingerprint(patch_path: Path) -> tuple[dict, bytes]:
        patch = json.loads(patch_path.read_text(encoding="utf-8"))
        patch.pop("patch_sha256")
        acceptance_tests.write_json(patch_path, patch)
        return patch, patch_path.read_bytes()

    def test_hashless_reviewer_patch_commits_and_retries_idempotently(self) -> None:
        fixture, workspace, _ = self._prepare()
        patch_path = fixture.patch(workspace)
        reviewer_patch, _ = self._remove_provider_fingerprint(patch_path)
        expected_fingerprint = acceptance_tests.canonical_sha(reviewer_patch)
        arguments = (
            "acceptance-patch-commit",
            "--workspace-root",
            str(workspace),
            "--dimension",
            "visual_quality",
            "--patch",
            str(patch_path),
            "--committed-at",
            "2026-09-06T12:00:00Z",
        )

        completed, envelope = acceptance_tests.run_cli(*arguments)

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(expected_fingerprint, envelope["data"]["patch_sha256"])
        normalized = json.loads(patch_path.read_text(encoding="utf-8"))
        self.assertEqual(expected_fingerprint, normalized.pop("patch_sha256"))
        self.assertEqual(reviewer_patch, normalized)

        current = json.loads((workspace / "current.json").read_text(encoding="utf-8"))
        execution_root = Path(current["execution_root"])
        execution = json.loads((workspace / "execution.json").read_text(encoding="utf-8"))
        committed_record = execution["committed_patches"]["visual_quality"]
        committed_path = Path(committed_record["path"])
        self.assertEqual(patch_path.read_bytes(), committed_path.read_bytes())
        stable_paths = (
            workspace / "execution.json",
            execution_root / "execution.json",
            committed_path,
            next((execution_root / "intents").glob("patch-*.json")),
            patch_path,
        )
        stable_bytes = {path: path.read_bytes() for path in stable_paths}
        retried, retry = acceptance_tests.run_cli(*arguments)

        self.assertEqual(0, retried.returncode, retried.stdout + retried.stderr)
        self.assertTrue(retry["data"]["idempotent"])
        self.assertEqual(stable_bytes, {path: path.read_bytes() for path in stable_paths})

    def test_hashless_patch_recovery_keeps_normalized_semantic_identity(self) -> None:
        # scenario_id: hashless_patch_publication_recovery
        # authority input: valid reviewer-authored Patch without provider fingerprint
        # derived node: normalized canonical staging bytes
        # boundaries: file preparation and first Control Store intent commit
        # rematerialized nodes: intent, committed Patch, execution and Claim projections
        # intentionally_stale_nodes: boundary-dependent publication projections only
        # observation: reconcile/retry retains one provider-derived Patch identity
        for fault_point in (
            "after_patch_file_prepare",
            "after_patch_intent_control_commit",
        ):
            with self.subTest(fault_point=fault_point):
                fixture, workspace, _ = self._prepare()
                patch_path = fixture.patch(workspace)
                reviewer_patch, _ = self._remove_provider_fingerprint(patch_path)
                expected_fingerprint = acceptance_tests.canonical_sha(reviewer_patch)
                arguments = (
                    "acceptance-patch-commit",
                    "--workspace-root",
                    str(workspace),
                    "--dimension",
                    "visual_quality",
                    "--patch",
                    str(patch_path),
                    "--committed-at",
                    "2026-09-06T12:00:00Z",
                )

                failed, fault = acceptance_tests.run_cli(
                    *arguments,
                    "--fault-point",
                    fault_point,
                )

                self.assertNotEqual(0, failed.returncode)
                self.assertEqual("injected_acceptance_v2_fault", fault["classification"])
                self.assertEqual(fault_point, fault["data"]["fault_point"])
                normalized_bytes = patch_path.read_bytes()
                normalized = json.loads(normalized_bytes)
                self.assertEqual(expected_fingerprint, normalized.pop("patch_sha256"))
                self.assertEqual(reviewer_patch, normalized)

                reconciled, recovery = acceptance_tests.run_cli(
                    "acceptance-reconcile",
                    "--workspace-root",
                    str(workspace),
                )

                self.assertEqual(
                    0,
                    reconciled.returncode,
                    reconciled.stdout + reconciled.stderr,
                )
                expected_actions = (
                    ["aborted_uncommitted:acceptance_patch_publication"]
                    if fault_point == "after_patch_file_prepare"
                    else ["committed_patch:visual_quality"]
                )
                self.assertEqual(expected_actions, recovery["data"]["actions"])
                self.assertEqual(normalized_bytes, patch_path.read_bytes())

                retried, retry = acceptance_tests.run_cli(*arguments)

                self.assertEqual(0, retried.returncode, retried.stdout + retried.stderr)
                self.assertEqual(
                    fault_point == "after_patch_intent_control_commit",
                    retry["data"]["idempotent"],
                )
                self.assertEqual(expected_fingerprint, retry["data"]["patch_sha256"])
                self.assertEqual(normalized_bytes, patch_path.read_bytes())

    def test_invalid_patch_is_rejected_without_rewriting_staged_bytes(self) -> None:
        scenarios = (
            (
                "supplied_fingerprint_mismatch",
                {"patch_sha256": "0" * 64},
                False,
                "patch_identity",
                "acceptance_patch_fingerprint_invalid",
                None,
            ),
            (
                "array_wrapped_patch",
                {},
                False,
                "delivery_quality_schema_validation",
                "contract_invalid",
                20,
            ),
            (
                "hashless_visual_coverage_gap",
                {},
                False,
                "visual_page_coverage",
                "acceptance_visual_page_coverage",
                None,
            ),
            (
                "hashless_stale_claim",
                {"fencing_token": "f" * 64},
                False,
                "patch_fencing",
                "acceptance_patch_fencing_stale",
                None,
            ),
        )
        for (
            scenario,
            replacements,
            omit_page,
            expected_gate,
            expected_code,
            expected_returncode,
        ) in scenarios:
            with self.subTest(scenario=scenario):
                fixture, workspace, _ = self._prepare()
                patch_path = fixture.patch(workspace, omit_page=omit_page)
                patch = json.loads(patch_path.read_text(encoding="utf-8"))
                if scenario == "array_wrapped_patch":
                    patch = [patch]
                elif scenario == "hashless_visual_coverage_gap":
                    patch["visual_scan_evidence"]["pages_checked"].append(
                        dict(patch["visual_scan_evidence"]["pages_checked"][0])
                    )
                if scenario != "array_wrapped_patch":
                    patch.update(replacements)
                    if scenario != "supplied_fingerprint_mismatch":
                        patch.pop("patch_sha256")
                acceptance_tests.write_json(patch_path, patch)
                staged_before = patch_path.read_bytes()

                rejected, envelope = acceptance_tests.run_cli(
                    "acceptance-patch-commit",
                    "--workspace-root",
                    str(workspace),
                    "--dimension",
                    "visual_quality",
                    "--patch",
                    str(patch_path),
                    "--committed-at",
                    "2026-09-06T12:00:00Z",
                )

                self.assertNotEqual(0, rejected.returncode)
                if expected_returncode is not None:
                    self.assertEqual(expected_returncode, rejected.returncode)
                self.assertEqual(
                    {
                        "first_failing_gate": expected_gate,
                        "error_code": expected_code,
                    },
                    {
                        "first_failing_gate": envelope["data"]["first_failing_gate"],
                        "error_code": envelope["data"]["error_code"],
                    },
                )
                self.assertEqual(staged_before, patch_path.read_bytes())


if __name__ == "__main__":
    unittest.main()

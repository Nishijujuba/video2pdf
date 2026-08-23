from __future__ import annotations

import json
from pathlib import Path
import shutil
import threading
import unittest
import uuid


from tests.video_workflow import test_issue13_final_evidence_cli as issue13_fixture


PROJECT_ROOT = issue13_fixture.PROJECT_ROOT


class Issue41LegacyFinalCompileTests(unittest.TestCase):
    """Legacy Final Compile enters through the public Delivery Quality CLI."""

    def _run_legacy_compile(
        self,
        fixture: dict[str, Path],
        *,
        video_root: Path,
        workspace_root: Path,
    ) -> tuple[object, dict]:
        fixture_case = issue13_fixture.Issue13FinalEvidenceCliTests(
            "test_public_cli_prepares_kernel_final_evidence_for_acceptance_v2"
        )
        return fixture_case._cli(
            "delivery-quality-final-compile",
            "--input-track", "legacy",
            "--video-root", str(video_root),
            "--precompile-workspace-root", str(fixture["quality"]),
            "--compile-manifest", str(fixture["manifest"]),
            "--text-origin-plan", str(fixture["origin_plan"]),
            "--compiler-adapter", str(
                PROJECT_ROOT / "scripts" / "guarded_final_compile_adapter.py"
            ),
            "--runtime-policy", str(fixture["runtime_policy"]),
            "--workspace-root", str(workspace_root),
            "--compiled-at", "2026-08-23T15:00:00+08:00",
        )

    def _run_record_free_legacy_precompile(self) -> dict[str, Path]:
        fixture_case = issue13_fixture.Issue13FinalEvidenceCliTests(
            "test_public_cli_prepares_kernel_final_evidence_for_acceptance_v2"
        )
        kernel_run, _control_root = fixture_case._source_ready_v4_run()
        fixture_case._production_complete(kernel_run)
        fixture_case._current_quality_evidence(kernel_run)

        video_root = (
            PROJECT_ROOT
            / "workspace"
            / "待删除"
            / "kernel-test-runs"
            / f"issue41-legacy-final-compile-{uuid.uuid4().hex[:10]}"
        )
        video_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(kernel_run, video_root)

        quality = video_root / "review" / "quality"
        manifest_path = quality / "inputs" / "final-compile-manifest.json"
        origin_plan_path = quality / "inputs" / "text-origin-plan.json"
        runtime_policy = quality / "inputs" / "legacy-compile-runtime-policy.json"
        shutil.copy2(
            video_root / "workflow" / "compile-runtime-policy.json",
            runtime_policy,
        )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["entries"]:
            original = Path(entry["source_path"])
            entry["source_path"] = str(video_root / original.relative_to(kernel_run))
        manifest["runtime_policy"] = {
            "path": str(runtime_policy.resolve()),
            "sha256": issue13_fixture._sha256(runtime_policy),
        }
        manifest["manifest_sha256"] = issue13_fixture._canonical_sha(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
        issue13_fixture._write_json(manifest_path, manifest)

        retired_workflow = video_root / "待删除" / "kernel-fixture-workflow"
        retired_workflow.parent.mkdir(parents=True, exist_ok=True)
        (video_root / "workflow").rename(retired_workflow)
        self.assertFalse((video_root / "workflow" / "run.json").exists())
        return {
            "video_root": video_root,
            "quality": quality,
            "manifest": manifest_path,
            "origin_plan": origin_plan_path,
            "runtime_policy": runtime_policy,
        }

    def test_public_legacy_final_compile_and_single_contradictions(self) -> None:
        fixture = self._run_record_free_legacy_precompile()
        video_root = fixture["video_root"]

        with self.subTest("valid Run-record-free Legacy precompile graph"):
            completed, envelope = self._run_legacy_compile(
                fixture,
                video_root=video_root,
                workspace_root=video_root / "review" / "acceptance" / "legacy-final",
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual("ok", envelope["status"])
            self.assertEqual("guarded_final_compile_complete", envelope["classification"])
            self.assertFalse((video_root / "workflow" / "run.json").exists())
            self.assertTrue(Path(envelope["data"]["final_compile_report_path"]).is_file())
            self.assertTrue(Path(envelope["data"]["final_pdf_path"]).is_file())

        # scenario_id: legacy_final_compile_wrong_global_gate_root
        # target_invariant: --video-root names one Legacy video, never the active
        #   Global Gate control-store root that owns cross-track authority.
        # mutation_seam: change only --video-root to the canonical gate root.
        # rematerialized_nodes: []
        # intentionally_stale_nodes: []
        # expected_first_gate: global_gate_authority
        # expected_error_code: legacy_global_gate_root_forbidden
        # scenario_class: single_contradiction
        with self.subTest("wrong Global Gate root"):
            completed, envelope = self._run_legacy_compile(
                fixture,
                video_root=PROJECT_ROOT / "workspace",
                workspace_root=video_root / "review" / "acceptance" / "wrong-gate-root",
            )
            self.assertNotEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual("error", envelope["status"])
            self.assertEqual("global_gate_authority", envelope["data"]["first_failing_gate"])
            self.assertEqual(
                "legacy_global_gate_root_forbidden", envelope["data"]["error_code"]
            )

        # scenario_id: legacy_final_compile_canonical_run_record_forbidden
        # target_invariant: a Legacy root is Run-record-free and cannot acquire
        #   Final Compile authority from a synthetic canonical workflow/run.json.
        # mutation_seam: add only <video-root>/workflow/run.json.
        # rematerialized_nodes: []
        # intentionally_stale_nodes: []
        # expected_first_gate: legacy_run_record_absence
        # expected_error_code: legacy_synthetic_run_record_forbidden
        # scenario_class: single_contradiction
        with self.subTest("canonical synthetic Workflow Run"):
            synthetic_run = issue13_fixture._write_json(
                video_root / "workflow" / "run.json",
                {"schema_name": "workflow-run", "schema_version": "4.0.0"},
            )
            completed, envelope = self._run_legacy_compile(
                fixture,
                video_root=video_root,
                workspace_root=video_root / "review" / "acceptance" / "synthetic-run",
            )
            self.assertNotEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual("error", envelope["status"])
            self.assertEqual(
                "legacy_run_record_absence", envelope["data"]["first_failing_gate"]
            )
            self.assertEqual(
                "legacy_synthetic_run_record_forbidden",
                envelope["data"]["error_code"],
            )
            parked = video_root / "待删除" / "synthetic-workflow"
            synthetic_run.parent.rename(parked)

        # scenario_id: legacy_final_compile_adapter_creates_canonical_run_record
        # target_invariant: a Legacy root remains Run-record-free across adapter
        #   execution and before Final Compile report publication.
        # mutation_seam: create only <video-root>/workflow/run.json after the
        #   registered adapter process returns successfully.
        # rematerialized_nodes: adapter outputs
        # intentionally_stale_nodes: []
        # expected_first_gate: legacy_run_record_absence
        # expected_error_code: legacy_synthetic_run_record_forbidden
        # scenario_class: single_contradiction
        with self.subTest("adapter creates canonical synthetic Workflow Run"):
            workspace_root = (
                video_root
                / "review"
                / "acceptance"
                / "adapter-created-synthetic-run"
            )
            adapter_marker = workspace_root / "adapter-output" / "final.pdf"
            synthetic_run_created = threading.Event()
            stop_observer = threading.Event()

            def create_synthetic_run_during_adapter() -> None:
                while not stop_observer.wait(0.005):
                    if adapter_marker.is_file():
                        issue13_fixture._write_json(
                            video_root / "workflow" / "run.json",
                            {
                                "schema_name": "workflow-run",
                                "schema_version": "4.0.0",
                            },
                        )
                        synthetic_run_created.set()
                        return

            observer = threading.Thread(
                target=create_synthetic_run_during_adapter,
                name="issue41-create-synthetic-run-during-adapter",
            )
            observer.start()
            try:
                completed, envelope = self._run_legacy_compile(
                    fixture,
                    video_root=video_root,
                    workspace_root=workspace_root,
                )
            finally:
                stop_observer.set()
                observer.join(timeout=5)
            self.assertTrue(
                synthetic_run_created.is_set(),
                "adapter completion marker was not observed before CLI return",
            )
            self.assertNotEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual("error", envelope["status"])
            self.assertEqual(
                "legacy_run_record_absence", envelope["data"]["first_failing_gate"]
            )
            self.assertEqual(
                "legacy_synthetic_run_record_forbidden",
                envelope["data"]["error_code"],
            )
            parked = video_root / "待删除" / "adapter-created-synthetic-workflow"
            (video_root / "workflow").rename(parked)

        # scenario_id: legacy_final_compile_workspace_escape
        # target_invariant: every Final Compile output remains inside the named
        #   Legacy video root.
        # mutation_seam: change only --workspace-root to a sibling directory.
        # rematerialized_nodes: []
        # intentionally_stale_nodes: []
        # expected_first_gate: legacy_final_compile_workspace_boundary
        # expected_error_code: legacy_final_compile_path_out_of_bounds
        # scenario_class: single_contradiction
        with self.subTest("Final Compile workspace escape"):
            completed, envelope = self._run_legacy_compile(
                fixture,
                video_root=video_root,
                workspace_root=video_root.parent / f"escape-{uuid.uuid4().hex[:8]}",
            )
            self.assertNotEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual("error", envelope["status"])
            self.assertEqual(
                "legacy_final_compile_workspace_boundary",
                envelope["data"]["first_failing_gate"],
            )
            self.assertEqual(
                "legacy_final_compile_path_out_of_bounds",
                envelope["data"]["error_code"],
            )


if __name__ == "__main__":
    unittest.main()

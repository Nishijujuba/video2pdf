from __future__ import annotations

"""Issue #101 qualification against the retained YouTube Production Run.

The fixture is the original Run named by ``VIDEO2PDF_ISSUE101_RUN_DIR``.  The
test intentionally skips when that retained evidence is unavailable; it never
constructs replacement video, subtitle, Figure, Production, or Precompile data.
"""

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"
RUN_ENV = "VIDEO2PDF_ISSUE101_RUN_DIR"


@unittest.skipUnless(os.environ.get(RUN_ENV), f"{RUN_ENV} is required")
class Issue101RetainedRunQualificationTests(unittest.TestCase):
    def test_public_promotion_is_idempotent_and_derives_current_successor(self) -> None:
        run_dir = Path(os.environ[RUN_ENV]).resolve()
        bundle_paths = sorted(
            (run_dir / "待删除" / "production-repair-replay" / "bundles").glob(
                "*/bundle.json"
            )
        )
        self.assertEqual(1, len(bundle_paths))
        predecessor = run_dir / "review/precompile/workspaces/attempt_01_20260831"
        successor = (
            run_dir
            / "review/precompile/workspaces/attempt_05_issue102_generated_titles"
        )
        command = [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(CLI),
            "delivery-quality-precompile-repair-promote",
            "--run-dir",
            str(run_dir),
            "--repair-bundle",
            str(bundle_paths[0]),
            "--predecessor-workspace-root",
            str(predecessor),
            "--workspace-root",
            str(successor),
            "--inventory",
            str(
                run_dir
                / "review/precompile/workspaces/attempt_02_20260831"
                / "reader-facing-text-inventory.json"
            ),
            "--semantic-dependencies",
            str(
                run_dir
                / "review/precompile/workspaces/attempt_02_20260831"
                / "semantic-dependencies.json"
            ),
            "--repair-attempt-number",
            "1",
            "--prepared-at",
            "2026-08-31T16:15:00+08:00",
        ]
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("ok", result["status"])
        self.assertEqual(
            "precompile_repair_already_promoted", result["classification"]
        )
        self.assertEqual(33, result["data"]["promoted_task_count"])

        generations = json.loads(
            Path(result["data"]["successor_generation_set_path"]).read_text(
                encoding="utf-8"
            )
        )
        manifest = json.loads(
            (run_dir / "workflow/compile-manifest.json").read_text(encoding="utf-8")
        )
        actual = {
            item["logical_id"]: (item["generation"], item["sha256"])
            for item in generations["artifacts"]
        }
        expected = {
            item["logical_id"]: (item["generation"], item["sha256"])
            for item in manifest["entries"]
        }
        self.assertEqual(expected, actual)
        self.assertEqual(
            str(successor / "repair-attempt.json"),
            result["data"]["repair_attempt_path"],
        )
        inventory = json.loads(
            Path(result["data"]["successor_inventory_path"]).read_text(
                encoding="utf-8"
            )
        )
        generated_titles = next(
            item
            for item in inventory["items"]
            if item["item_id"] == "generated.local_style.box_titles"
        )
        self.assertEqual(
            "核心结论\n机制说明\n边界与限制\n演讲原声",
            generated_titles["declared_text"],
        )
        dependencies = json.loads(
            Path(
                result["data"]["successor_semantic_dependencies_path"]
            ).read_text(encoding="utf-8")
        )
        for dependency in dependencies["dependencies"]:
            for evidence in dependency["projection"]["evidence"]:
                evidence_path = run_dir / evidence["path"]
                self.assertTrue(evidence_path.is_file())
                self.assertEqual(
                    evidence["sha256"],
                    hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                )

        # scenario_id: issue101_active_kernel_detached_prepare
        # target_invariant: active Kernel Reviewer dispatch requires Production authority
        # mutation_seam: invoke the legacy caller-authored repair-prepare CLI directly
        # rematerialized_nodes: none; the current Production graph remains coherent
        # intentionally_stale_nodes: none
        # expected_first_gate: kernel_production_authority
        # expected_error_code: precompile_repair_production_authority_required
        # scenario_class: single_contradiction
        forbidden_workspace = (
            PROJECT_ROOT / "待删除/test-runs/forbidden_direct_issue101"
        )
        rejected = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(CLI),
                "delivery-quality-precompile-repair-prepare",
                "--predecessor-workspace-root",
                str(predecessor),
                "--workspace-root",
                str(forbidden_workspace),
                "--inventory",
                result["data"]["successor_inventory_path"],
                "--artifact-generations",
                result["data"]["successor_generation_set_path"],
                "--semantic-dependencies",
                result["data"]["successor_semantic_dependencies_path"],
                "--repair-attempt-number",
                "1",
                "--prepared-at",
                "2026-08-31T16:15:00+08:00",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(20, rejected.returncode, rejected.stdout + rejected.stderr)
        rejection = json.loads(rejected.stdout)
        self.assertEqual("kernel_production_authority", rejection["data"]["first_failing_gate"])
        self.assertEqual(
            "precompile_repair_production_authority_required",
            rejection["data"]["error_code"],
        )
        self.assertFalse(forbidden_workspace.exists())


if __name__ == "__main__":
    unittest.main()

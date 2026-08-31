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
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.precompile_repair_promotion import (
    PrecompileRepairPromotionProvider,
)


@unittest.skipUnless(os.environ.get(RUN_ENV), f"{RUN_ENV} is required")
class Issue101RetainedRunQualificationTests(unittest.TestCase):
    def test_public_promotion_is_idempotent_and_derives_current_successor(self) -> None:
        run_dir = Path(os.environ[RUN_ENV]).resolve()
        bundle_paths = sorted(
            (run_dir / "待删除" / "production-repair-replay" / "bundles").glob(
                "*/bundle*.json"
            )
        )
        bundle_records = [
            (path, json.loads(path.read_text(encoding="utf-8")))
            for path in bundle_paths
        ]
        incomplete_bundle = next(
            path
            for path, bundle in bundle_records
            if len(bundle["task_order"]) == 9
        )
        complete_bundle, complete_bundle_data = max(
            (
                (path, bundle)
                for path, bundle in bundle_records
                if bundle.get("notes", {}).get("scope")
                == "complete Production closure with fresh independent Pyramid evaluations"
            ),
            key=lambda item: sum(
                claim["claim_generation"]
                for claim in item[1]["initial_claims"].values()
            ),
        )
        predecessor = run_dir / "review/precompile/workspaces/attempt_01_20260831"

        # The retained nine-task bundle is the exact failed Pyramid-only replay
        # from this PDF Run.  Replaying it must be rejected before any further
        # authoritative state change because its order cannot restore the full
        # outline -> content -> Pyramid -> main Production closure.
        state_path = run_dir / "workflow/production-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        provider = PrecompileRepairPromotionProvider(PROJECT_ROOT)
        self.assertEqual(
            complete_bundle_data["task_order"],
            provider._required_replay_task_order(state),
        )
        provider._preflight_claim_plan(
            state=state,
            initial_claims=complete_bundle_data["initial_claims"],
            task_order=complete_bundle_data["task_order"],
        )
        incomplete_claims = dict(complete_bundle_data["initial_claims"])
        incomplete_claims.pop(complete_bundle_data["task_order"][-1])
        with self.assertRaises(ContractError) as missing_claim:
            provider._preflight_claim_plan(
                state=state,
                initial_claims=incomplete_claims,
                task_order=complete_bundle_data["task_order"],
            )
        self.assertEqual(
            "precompile_repair_claim_plan",
            missing_claim.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_claim_plan_incomplete",
            missing_claim.exception.data["error_code"],
        )
        state_before = hashlib.sha256(state_path.read_bytes()).hexdigest()
        incomplete = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(CLI),
                "delivery-quality-precompile-repair-promote",
                "--run-dir",
                str(run_dir),
                "--repair-bundle",
                str(incomplete_bundle),
                "--predecessor-workspace-root",
                str(predecessor),
                "--workspace-root",
                str(run_dir / "review/precompile/workspaces/attempt_07_used_environment_titles"),
                "--inventory",
                str(
                    run_dir
                    / "review/precompile/workspaces/attempt_05_issue102_generated_titles"
                    / "reader-facing-text-inventory.json"
                ),
                "--semantic-dependencies",
                str(
                    run_dir
                    / "review/precompile/workspaces/attempt_05_issue102_generated_titles"
                    / "semantic-dependencies.json"
                ),
                "--repair-attempt-number",
                "3",
                "--prepared-at",
                "2026-08-31T18:40:00+08:00",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(20, incomplete.returncode, incomplete.stdout + incomplete.stderr)
        incomplete_result = json.loads(incomplete.stdout)
        self.assertEqual(
            "precompile_repair_task_order_closure",
            incomplete_result["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_task_order_incomplete",
            incomplete_result["data"]["error_code"],
        )
        self.assertEqual(
            state_before,
            hashlib.sha256(state_path.read_bytes()).hexdigest(),
        )

        bundle_suffix = complete_bundle.stem.removeprefix("bundle-")
        successor = (
            run_dir
            / "review/precompile/workspaces"
            / f"attempt_04_used_reader_items_{bundle_suffix}"
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
            str(complete_bundle),
            "--predecessor-workspace-root",
            str(predecessor),
            "--workspace-root",
            str(successor),
            "--inventory",
            str(
                run_dir
                / "review/precompile/workspaces/attempt_07_used_environment_titles"
                / "reader-facing-text-inventory.json"
            ),
            "--semantic-dependencies",
            str(
                run_dir
                / "review/precompile/workspaces/attempt_07_used_environment_titles"
                / "semantic-dependencies.json"
            ),
            "--repair-attempt-number",
            "3",
            "--prepared-at",
            "2026-08-31T18:40:00+08:00",
        ]
        refreshed_state = json.loads(state_path.read_text(encoding="utf-8"))
        fault_scenarios = (
            (
                ("after_supersede", "outline"),
                ("after_attempt_materialized", "pyramid-outline"),
                ("after_promotion_prepared", "writer-section-01"),
                ("before_receipt_committed", "writer-section-02"),
                ("after_state_committed", "pyramid-main"),
            )
            if all(
                refreshed_state["claims"][logical_key]["claim_generation"]
                == complete_bundle_data["initial_claims"][logical_key]["claim_generation"]
                for logical_key in complete_bundle_data["task_order"]
            )
            else ()
        )
        for fault_point, logical_task_key in fault_scenarios:
            interrupted = subprocess.run(
                [
                    *command,
                    "--fault-point",
                    fault_point,
                    "--fault-logical-task-key",
                    logical_task_key,
                ],
                cwd=PROJECT_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                60,
                interrupted.returncode,
                interrupted.stdout + interrupted.stderr,
            )
            fault_result = json.loads(interrupted.stdout)
            self.assertEqual(
                "injected_production_fault", fault_result["classification"]
            )
            self.assertEqual(fault_point, fault_result["data"]["fault_point"])

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
        self.assertIn(
            result["classification"],
            {"precompile_repair_promoted", "precompile_repair_already_promoted"},
        )
        self.assertEqual(33, result["data"]["promoted_task_count"])

        successor_inventory = json.loads(
            (
                successor / "reader-facing-text-inventory.json"
            ).read_text(encoding="utf-8")
        )
        self.assertNotIn(
            "raster.source_cover",
            {item["item_id"] for item in successor_inventory["items"]},
        )
        self.assertNotIn(
            "raster.source_cover",
            {item["region_id"] for item in successor_inventory["declared_surface"]},
        )
        self.assertNotIn(
            "raster.source_cover",
            {item["region_id"] for item in successor_inventory["coverage_ledger"]},
        )

        recovered_state = json.loads(state_path.read_text(encoding="utf-8"))
        for logical_key in complete_bundle_data["task_order"]:
            initial = complete_bundle_data["initial_claims"][logical_key]
            current = recovered_state["claims"][logical_key]
            self.assertEqual(initial["claim_generation"] + 1, current["claim_generation"])
            self.assertEqual("committed", current["status"])
            receipt = recovered_state["receipts"][logical_key]
            self.assertEqual(current["claim_generation"], receipt["claim_generation"])
            self.assertTrue(
                (
                    run_dir
                    / "workflow/tasks"
                    / current["task_id"]
                    / "attempts"
                    / receipt["attempt_id"]
                    / "attempt.json"
                ).is_file()
            )

        repeated = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)
        repeated_result = json.loads(repeated.stdout)
        self.assertEqual(
            "precompile_repair_already_promoted",
            repeated_result["classification"],
        )

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
            "核心结论\n机制说明\n边界与限制",
            generated_titles["declared_text"],
        )
        self.assertNotIn(
            "raster.source_cover",
            {item["item_id"] for item in inventory["items"]},
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
                "3",
                "--prepared-at",
                "2026-08-31T18:40:00+08:00",
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

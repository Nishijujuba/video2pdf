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


def canonical_sha(value: object) -> str:
    data = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    return path


def run_cli(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    return completed, json.loads(completed.stdout)


def generation_set() -> dict:
    value = {
        "schema_name": "precompile-artifact-generation-set",
        "schema_version": "1.0.0",
        "generation_set_id": "integrated-draft-7",
        "artifacts": [
            {
                "logical_id": "integrated_main_tex",
                "generation": 7,
                "sha256": "1" * 64,
            },
            {
                "logical_id": "figure_01",
                "generation": 2,
                "sha256": "2" * 64,
            },
        ],
    }
    value["generation_set_sha256"] = canonical_sha(value)
    return value


def semantic_dependencies() -> dict:
    value = {
        "schema_name": "precompile-semantic-dependencies",
        "schema_version": "1.0.0",
        "dependencies": [
            {
                "owner": "source-faithfulness-reviewer",
                "projection_id": "source-faithfulness-evaluation",
                "projection_sha256": "3" * 64,
                "required_scope_ids": ["source-correspondence"],
                "provider_id": "source-faithfulness-provider",
                "provider_sha256": "4" * 64,
            },
            {
                "owner": "pyramid-reviewer",
                "projection_id": "pyramid-evaluation",
                "projection_sha256": "5" * 64,
                "required_scope_ids": ["integrated-document"],
                "provider_id": "pyramid-provider",
                "provider_sha256": "6" * 64,
            },
        ],
    }
    value["dependencies_sha256"] = canonical_sha(value)
    return value


def inventory(*, raster_representation: bool = True) -> dict:
    generation = generation_set()
    items = [
        {
            "item_id": "main.title",
            "kind": "title",
            "semantic_region": "title",
            "language_profile_id": "zh-hans",
            "source_artifact_logical_id": "integrated_main_tex",
            "source_generation": 7,
            "source_sha256": "1" * 64,
            "locator": "latex:document/title",
            "representation": "structured_text",
            "text_sha256": "7" * 64,
            "applicable_rule_ids": ["no_meta_writing_content"],
        },
        {
            "item_id": "figure.01.callout",
            "kind": "figure_text",
            "semantic_region": "caption",
            "language_profile_id": "zh-hans",
            "source_artifact_logical_id": "figure_01",
            "source_generation": 2,
            "source_sha256": "2" * 64,
            "locator": "figure:01/callout-A",
            "representation": (
                "authoritative_raster_text"
                if raster_representation
                else "missing_raster_text"
            ),
            "text_sha256": "8" * 64 if raster_representation else None,
            "applicable_rule_ids": [
                "no_meta_writing_content",
                "argument_chain_integrity",
            ],
        },
    ]
    for item in items:
        item["item_sha256"] = canonical_sha(item)
    coverage = [
        {
            "region_id": item["item_id"],
            "item_id": item["item_id"],
            "status": "covered",
        }
        for item in items
    ]
    value = {
        "schema_name": "reader-facing-text-inventory",
        "schema_version": "1.0.0",
        "inventory_id": "inventory-7",
        "language_profile_id": "zh-hans",
        "delivery_glossary": None,
        "generation_set_sha256": generation["generation_set_sha256"],
        "declared_surface": [
            {"region_id": item["item_id"], "kind": item["kind"]} for item in items
        ],
        "items": items,
        "coverage_ledger": coverage,
        "extractors": [
            {
                "extractor_id": "latex-reader-text-extractor",
                "extractor_sha256": "9" * 64,
            },
            {
                "extractor_id": "declared-raster-text-provider",
                "extractor_sha256": "a" * 64,
            },
        ],
    }
    value["reader_text_set_sha256"] = canonical_sha(
        [
            {
                "item_id": item["item_id"],
                "kind": item["kind"],
                "representation": item["representation"],
                "text_sha256": item["text_sha256"],
            }
            for item in items
        ]
    )
    value["inventory_sha256"] = canonical_sha(value)
    return value


class PrecompileQualityCliTests(unittest.TestCase):
    def prepare_case(
        self,
        *,
        raster_representation: bool = True,
        fault_point: str | None = None,
    ) -> tuple[Path, subprocess.CompletedProcess[str], dict]:
        root = new_case_dir(self.id(), label="precompile-prepare")
        workspace = root / "quality"
        generation_path = write_json(root / "generations.json", generation_set())
        dependency_path = write_json(
            root / "semantic-dependencies.json", semantic_dependencies()
        )
        inventory_path = write_json(
            root / "inventory.json",
            inventory(raster_representation=raster_representation),
        )
        arguments = [
            "delivery-quality-precompile-prepare",
            "--workspace-root",
            str(workspace),
            "--inventory",
            str(inventory_path),
            "--artifact-generations",
            str(generation_path),
            "--semantic-dependencies",
            str(dependency_path),
            "--prepared-at",
            "2026-07-30T14:00:00Z",
        ]
        if fault_point is not None:
            arguments.extend(["--fault-point", fault_point])
        completed, envelope = run_cli(*arguments)
        return workspace, completed, envelope

    def commit_patch(
        self,
        workspace: Path,
        owner: str,
        *,
        fail_result_key: str | None = None,
        contract_gap: bool = False,
        fault_point: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        skeleton_path = workspace / "skeletons" / f"{owner}.skeleton.json"
        skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
        results = []
        for required in skeleton["required_results"]:
            result = {
                "result_key": required["result_key"],
                "decision": (
                    "fail"
                    if required["result_key"] == fail_result_key
                    else "pass"
                ),
                "evidence_locator": f"artifact:{required['result_key']}",
                "repair_write_set": (
                    ["work/sections/section_01.tex"]
                    if required["result_key"] == fail_result_key
                    else []
                ),
            }
            if result["decision"] == "fail":
                result["violation_id"] = "fixture_violation"
            results.append(result)
        patch = {
            "schema_name": "precompile-judgment-patch",
            "schema_version": "1.0.0",
            "task_id": skeleton["task_id"],
            "owner": owner,
            "skeleton_sha256": skeleton["skeleton_sha256"],
            "generation_set_sha256": skeleton["generation_set_sha256"],
            "reviewer": {
                "reviewer_id": f"reviewer-{owner}",
                "runtime_sha256": "b" * 64,
                "independent_from_generation_producers": True,
            },
            "results": results,
            "contract_gaps": (
                [
                    {
                        "gap_id": "gap-1",
                        "observation": "The evidence cannot map to registered policy.",
                        "evidence_locator": "artifact:unknown",
                    }
                ]
                if contract_gap
                else []
            ),
        }
        patch["patch_sha256"] = canonical_sha(patch)
        patch_path = write_json(
            workspace.parent / f"{owner}.patch.json",
            patch,
        )
        arguments = [
            "delivery-quality-precompile-patch-commit",
            "--workspace-root",
            str(workspace),
            "--owner",
            owner,
            "--patch",
            str(patch_path),
            "--committed-at",
            "2026-07-30T14:10:00Z",
        ]
        if fault_point is not None:
            arguments.extend(["--fault-point", fault_point])
        return run_cli(*arguments)

    def create_passing_seal(self) -> Path:
        workspace, completed, _ = self.prepare_case()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for owner in (
            "source-faithfulness-reviewer",
            "writing-quality-reviewer",
            "pyramid-reviewer",
        ):
            committed, _ = self.commit_patch(workspace, owner)
            self.assertEqual(committed.returncode, 0, committed.stderr)
        materialized, _ = run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(workspace),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-07-30T14:20:00Z",
        )
        self.assertEqual(materialized.returncode, 0, materialized.stderr)
        sealed, _ = run_cli(
            "delivery-quality-seal",
            "--workspace-root",
            str(workspace),
            "--sealed-at",
            "2026-07-30T14:21:00Z",
        )
        self.assertEqual(sealed.returncode, 0, sealed.stderr)
        return workspace

    def successor_inputs(
        self,
        workspace: Path,
        *,
        changed_text: bool = False,
    ) -> tuple[Path, Path]:
        generations = json.loads(
            (workspace / "artifact-generations.json").read_text(encoding="utf-8")
        )
        inventory_value = json.loads(
            (workspace / "reader-facing-text-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        main = next(
            item
            for item in generations["artifacts"]
            if item["logical_id"] == "integrated_main_tex"
        )
        main["generation"] += 1
        main["sha256"] = "c" * 64
        generations["generation_set_id"] = "integrated-draft-8"
        generations["generation_set_sha256"] = canonical_sha(
            {
                key: value
                for key, value in generations.items()
                if key != "generation_set_sha256"
            }
        )
        inventory_value["inventory_id"] = "inventory-8"
        inventory_value["generation_set_sha256"] = generations[
            "generation_set_sha256"
        ]
        for item in inventory_value["items"]:
            if item["source_artifact_logical_id"] == "integrated_main_tex":
                item["source_generation"] = main["generation"]
                item["source_sha256"] = main["sha256"]
            if changed_text and item["item_id"] == "main.title":
                item["text_sha256"] = "d" * 64
            item["item_sha256"] = canonical_sha(
                {
                    key: value
                    for key, value in item.items()
                    if key != "item_sha256"
                }
            )
        inventory_value["reader_text_set_sha256"] = canonical_sha(
            [
                {
                    "item_id": item["item_id"],
                    "kind": item["kind"],
                    "representation": item["representation"],
                    "text_sha256": item["text_sha256"],
                }
                for item in inventory_value["items"]
            ]
        )
        inventory_value["inventory_sha256"] = canonical_sha(
            {
                key: value
                for key, value in inventory_value.items()
                if key != "inventory_sha256"
            }
        )
        return (
            write_json(workspace.parent / "successor-generations.json", generations),
            write_json(workspace.parent / "successor-inventory.json", inventory_value),
        )

    def test_prepare_creates_three_isolated_same_generation_fixed_skeletons(
        self,
    ) -> None:
        workspace, completed, envelope = self.prepare_case()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            envelope["classification"], "precompile_review_tasks_prepared"
        )
        self.assertEqual(envelope["data"]["owner_count"], 3)
        skeletons = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((workspace / "skeletons").glob("*.json"))
        ]
        self.assertEqual(
            {item["owner"] for item in skeletons},
            {
                "source-faithfulness-reviewer",
                "writing-quality-reviewer",
                "pyramid-reviewer",
            },
        )
        self.assertEqual(
            {item["generation_set_sha256"] for item in skeletons},
            {generation_set()["generation_set_sha256"]},
        )
        self.assertEqual(len({item["task_id"] for item in skeletons}), 3)
        self.assertTrue(all(item["peer_results_visible"] is False for item in skeletons))
        writing = next(
            item for item in skeletons if item["owner"] == "writing-quality-reviewer"
        )
        self.assertEqual(len(writing["required_results"]), 3)
        self.assertEqual(
            {
                (item["rule_id"], item["item_id"])
                for item in writing["required_results"]
            },
            {
                ("no_meta_writing_content", "main.title"),
                ("no_meta_writing_content", "figure.01.callout"),
                ("argument_chain_integrity", "figure.01.callout"),
            },
        )

    def test_prepare_rejects_unrepresented_raster_text_before_writing_skeletons(
        self,
    ) -> None:
        workspace, completed, envelope = self.prepare_case(
            raster_representation=False
        )

        self.assertEqual(completed.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")
        self.assertIn("raster", envelope["data"]["message"].lower())
        self.assertFalse((workspace / "skeletons").exists())

    def test_independent_complete_patches_materialize_pass_and_create_initial_seal(
        self,
    ) -> None:
        workspace, completed, _ = self.prepare_case()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        task_ids = set()
        for owner in (
            "source-faithfulness-reviewer",
            "writing-quality-reviewer",
            "pyramid-reviewer",
        ):
            committed, envelope = self.commit_patch(workspace, owner)
            self.assertEqual(committed.returncode, 0, committed.stderr)
            self.assertEqual(
                envelope["classification"], "precompile_judgment_patch_committed"
            )
            task_ids.add(envelope["data"]["task_id"])
        self.assertEqual(len(task_ids), 3)

        materialized, envelope = run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(workspace),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-07-30T14:20:00Z",
        )
        self.assertEqual(materialized.returncode, 0, materialized.stderr)
        self.assertEqual(
            envelope["classification"], "precompile_quality_report_passed"
        )
        report = json.loads(
            (workspace / "precompile-quality-report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["overall_decision"], "pass")
        self.assertEqual(
            {item["owner"] for item in report["owner_reports"]},
            {
                "source-faithfulness-reviewer",
                "writing-quality-reviewer",
                "pyramid-reviewer",
            },
        )
        sealed, seal_envelope = run_cli(
            "delivery-quality-seal",
            "--workspace-root",
            str(workspace),
            "--sealed-at",
            "2026-07-30T14:21:00Z",
        )
        self.assertEqual(sealed.returncode, 0, sealed.stderr)
        self.assertEqual(
            seal_envelope["classification"], "precompile_text_seal_created"
        )
        seal = json.loads(
            (workspace / "precompile-text-seal.json").read_text(encoding="utf-8")
        )
        self.assertEqual(seal["decision_origin"], "fresh_evaluation")
        self.assertEqual(
            seal["generation_set_sha256"], generation_set()["generation_set_sha256"]
        )
        self.assertEqual(seal["predecessor_seal_sha256"], None)

    def test_patch_commit_rejects_missing_fixed_result_coverage(
        self,
    ) -> None:
        workspace, completed, _ = self.prepare_case()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        owner = "writing-quality-reviewer"
        skeleton = json.loads(
            (
                workspace / "skeletons" / f"{owner}.skeleton.json"
            ).read_text(encoding="utf-8")
        )
        patch = {
            "schema_name": "precompile-judgment-patch",
            "schema_version": "1.0.0",
            "task_id": skeleton["task_id"],
            "owner": owner,
            "skeleton_sha256": skeleton["skeleton_sha256"],
            "generation_set_sha256": skeleton["generation_set_sha256"],
            "reviewer": {
                "reviewer_id": "incomplete-reviewer",
                "runtime_sha256": "b" * 64,
                "independent_from_generation_producers": True,
            },
            "results": [],
            "contract_gaps": [],
        }
        patch["patch_sha256"] = canonical_sha(patch)
        patch_path = write_json(workspace.parent / "incomplete.patch.json", patch)

        rejected, envelope = run_cli(
            "delivery-quality-precompile-patch-commit",
            "--workspace-root",
            str(workspace),
            "--owner",
            owner,
            "--patch",
            str(patch_path),
            "--committed-at",
            "2026-07-30T14:10:00Z",
        )
        self.assertEqual(rejected.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")
        self.assertFalse((workspace / "patches" / f"{owner}.patch.json").exists())

    def test_presentation_only_equivalence_creates_successor_seal_with_lineage(
        self,
    ) -> None:
        workspace = self.create_passing_seal()
        prior = json.loads(
            (workspace / "precompile-text-seal.json").read_text(encoding="utf-8")
        )
        generations_path, inventory_path = self.successor_inputs(workspace)

        proved, envelope = run_cli(
            "delivery-quality-text-equivalence",
            "--workspace-root",
            str(workspace),
            "--successor-inventory",
            str(inventory_path),
            "--successor-artifact-generations",
            str(generations_path),
            "--mutation-class",
            "presentation_only",
            "--proved-at",
            "2026-07-30T14:30:00Z",
        )
        self.assertEqual(proved.returncode, 0, proved.stderr)
        self.assertEqual(envelope["classification"], "text_equivalence_proved")
        resealed, seal_envelope = run_cli(
            "delivery-quality-seal",
            "--workspace-root",
            str(workspace),
            "--sealed-at",
            "2026-07-30T14:31:00Z",
        )
        self.assertEqual(resealed.returncode, 0, resealed.stderr)
        self.assertEqual(
            seal_envelope["classification"], "precompile_text_successor_seal_created"
        )
        successor = json.loads(
            (workspace / "precompile-text-seal.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            successor["decision_origin"], "reused_after_text_equivalence"
        )
        self.assertEqual(successor["predecessor_seal_sha256"], prior["seal_sha256"])
        self.assertNotEqual(
            successor["generation_set_sha256"], prior["generation_set_sha256"]
        )
        self.assertTrue(
            (workspace / "seals" / f"{prior['seal_sha256']}.json").is_file()
        )

    def test_changed_reader_text_rejects_equivalence_and_successor_seal(
        self,
    ) -> None:
        workspace = self.create_passing_seal()
        generations_path, inventory_path = self.successor_inputs(
            workspace,
            changed_text=True,
        )

        rejected, envelope = run_cli(
            "delivery-quality-text-equivalence",
            "--workspace-root",
            str(workspace),
            "--successor-inventory",
            str(inventory_path),
            "--successor-artifact-generations",
            str(generations_path),
            "--mutation-class",
            "presentation_only",
            "--proved-at",
            "2026-07-30T14:30:00Z",
        )
        self.assertEqual(rejected.returncode, 30)
        self.assertEqual(envelope["classification"], "text_equivalence_rejected")
        report = json.loads(
            (workspace / "text-equivalence-report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["overall_decision"], "different")
        self.assertFalse(report["checks"]["reader_text_set_unchanged"])

    def test_overlapping_semantic_failures_route_one_integration_repair_and_no_seal(
        self,
    ) -> None:
        workspace, completed, _ = self.prepare_case()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        failures = {
            "writing-quality-reviewer": "no_meta_writing_content:main.title",
            "pyramid-reviewer": "pyramid-reviewer:integrated-document",
        }
        for owner in (
            "source-faithfulness-reviewer",
            "writing-quality-reviewer",
            "pyramid-reviewer",
        ):
            committed, _ = self.commit_patch(
                workspace,
                owner,
                fail_result_key=failures.get(owner),
            )
            self.assertEqual(committed.returncode, 0, committed.stderr)

        materialized, envelope = run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(workspace),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-07-30T15:00:00Z",
        )
        self.assertEqual(materialized.returncode, 0, materialized.stderr)
        self.assertEqual(
            envelope["classification"], "precompile_quality_report_failed"
        )
        report = json.loads(
            (workspace / "precompile-quality-report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["overall_decision"], "fail")
        self.assertTrue(report["semantic_attempt_budget_consumed"])
        self.assertEqual(
            len(report["repair_routing"]["integration_repair_tasks"]), 1
        )
        self.assertEqual(
            len(
                report["repair_routing"]["integration_repair_tasks"][0][
                    "failure_keys"
                ]
            ),
            2,
        )
        blocked, blocked_envelope = run_cli(
            "delivery-quality-seal",
            "--workspace-root",
            str(workspace),
            "--sealed-at",
            "2026-07-30T15:01:00Z",
        )
        self.assertEqual(blocked.returncode, 20)
        self.assertEqual(blocked_envelope["classification"], "contract_invalid")
        self.assertFalse((workspace / "precompile-text-seal.json").exists())

    def test_contract_gap_blocks_materialization_without_consuming_attempt(
        self,
    ) -> None:
        workspace, completed, _ = self.prepare_case()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for owner in (
            "source-faithfulness-reviewer",
            "writing-quality-reviewer",
            "pyramid-reviewer",
        ):
            committed, _ = self.commit_patch(
                workspace,
                owner,
                contract_gap=owner == "source-faithfulness-reviewer",
            )
            self.assertEqual(committed.returncode, 0, committed.stderr)

        blocked, envelope = run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(workspace),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-07-30T15:10:00Z",
        )
        self.assertEqual(blocked.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")
        self.assertFalse(envelope["data"]["semantic_attempt_budget_consumed"])
        self.assertFalse((workspace / "precompile-quality-report.json").exists())

    def test_semantic_dependency_mutation_invalidates_prepared_reviewer(
        self,
    ) -> None:
        workspace, completed, _ = self.prepare_case()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        dependencies_path = workspace / "semantic-dependencies.json"
        dependencies = json.loads(dependencies_path.read_text(encoding="utf-8"))
        dependencies["dependencies"][0]["provider_sha256"] = "e" * 64
        dependencies["dependencies_sha256"] = canonical_sha(
            {
                key: value
                for key, value in dependencies.items()
                if key != "dependencies_sha256"
            }
        )
        write_json(dependencies_path, dependencies)

        rejected, envelope = self.commit_patch(
            workspace,
            "source-faithfulness-reviewer",
        )
        self.assertEqual(rejected.returncode, 20)
        self.assertEqual(envelope["classification"], "contract_invalid")
        self.assertIn("stale", envelope["data"]["message"].lower())

    def test_interrupted_prepare_patch_and_materialization_replay_idempotently(
        self,
    ) -> None:
        workspace, interrupted, envelope = self.prepare_case(
            fault_point="after_first_skeleton_write"
        )
        self.assertEqual(interrupted.returncode, 60)
        self.assertEqual(envelope["classification"], "injected_precompile_fault")
        recovered, _ = run_cli(
            "delivery-quality-precompile-prepare",
            "--workspace-root",
            str(workspace),
            "--inventory",
            str(workspace.parent / "inventory.json"),
            "--artifact-generations",
            str(workspace.parent / "generations.json"),
            "--semantic-dependencies",
            str(workspace.parent / "semantic-dependencies.json"),
            "--prepared-at",
            "2026-07-30T14:00:00Z",
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)

        interrupted_patch, patch_envelope = self.commit_patch(
            workspace,
            "source-faithfulness-reviewer",
            fault_point="after_patch_write",
        )
        self.assertEqual(interrupted_patch.returncode, 60)
        self.assertEqual(
            patch_envelope["classification"], "injected_precompile_fault"
        )
        replayed_patch, replayed_envelope = self.commit_patch(
            workspace,
            "source-faithfulness-reviewer",
        )
        self.assertEqual(replayed_patch.returncode, 0, replayed_patch.stderr)
        self.assertTrue(replayed_envelope["data"]["recovered_partial_commit"])
        for owner in ("writing-quality-reviewer", "pyramid-reviewer"):
            committed, _ = self.commit_patch(workspace, owner)
            self.assertEqual(committed.returncode, 0, committed.stderr)

        interrupted_report, report_envelope = run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(workspace),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-07-30T14:20:00Z",
            "--fault-point",
            "after_report_write",
        )
        self.assertEqual(interrupted_report.returncode, 60)
        self.assertEqual(
            report_envelope["classification"], "injected_precompile_fault"
        )
        replayed_report, replayed_report_envelope = run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(workspace),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-07-30T14:20:00Z",
        )
        self.assertEqual(replayed_report.returncode, 0, replayed_report.stderr)
        self.assertEqual(
            replayed_report_envelope["classification"],
            "precompile_quality_report_passed",
        )


if __name__ == "__main__":
    unittest.main()

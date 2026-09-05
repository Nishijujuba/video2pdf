from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from tests.video_workflow.test_issue106_partial_repair_resume import (
    Issue106PartialRepairResumeTests,
)
from tests.video_workflow.test_issue106_reader_text_continuation import (
    fingerprint,
    write_json,
)
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.precompile_quality import (
    PRECOMPILE_OWNERS,
    PrecompileQualityProvider,
)
from video2pdf_workflow_kernel.utils import canonical_json_bytes, read_json


class Issue113SemanticInputRepairTests(unittest.TestCase):
    def _semantic_only_case(self) -> tuple[object, dict[str, object]]:
        fixture = Issue106PartialRepairResumeTests()
        provider, arguments = fixture._genuine_promotion_case()
        run = Path(arguments["run_dir"])
        state = read_json(run / "workflow/production-state.json")
        bundle_path = Path(arguments["repair_bundle_path"])
        bundle = read_json(bundle_path)
        artifact_by_payload = {
            "payload/outline.json": "outline_contract",
            "payload/writers/section_01.tex": "writer_section_01",
            "payload/writers/section_01.result.json": "writer_result_section_01",
            "payload/figures/figure_01.png": "figure_asset_figure_01",
            "payload/figures/figure_01.manifest.json": "figure_manifest_figure_01",
            "payload/figures/figure_01.tex": "figure_contribution_figure_01",
            "payload/pyramid/pyramid-outline.json": "pyramid_outline_report",
            "payload/pyramid/pyramid-section-section-01.json": (
                "pyramid_section_01_report"
            ),
            "payload/pyramid/pyramid-main.json": "pyramid_main_report",
        }
        for entry in bundle["derived_payload"]:
            suffix = next(
                (
                    candidate
                    for candidate in artifact_by_payload
                    if entry["path"].endswith(candidate)
                ),
                None,
            )
            if suffix is None:
                continue
            source = run / state["artifacts"][artifact_by_payload[suffix]]["path"]
            target = run / entry["path"]
            target.write_bytes(source.read_bytes())
            entry["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        write_json(bundle_path, bundle)

        predecessor_inventory = read_json(Path(arguments["inventory_path"]))
        predecessor_inventory["delivery_glossary"] = {
            "glossary_id": "issue113-current-glossary-v2",
            "path": "review/acceptance/glossaries/issue113-current-glossary-v2.json",
            "sha256": "d" * 64,
        }
        predecessor_inventory["inventory_sha256"] = fingerprint(
            predecessor_inventory, "inventory_sha256"
        )
        candidate_inventory_path = write_json(
            run / "待删除/issue113/candidate-reader-facing-text-inventory.json",
            predecessor_inventory,
        )
        arguments["inventory_path"] = candidate_inventory_path
        dependencies = read_json(Path(arguments["semantic_dependencies_path"]))
        source_dependency = next(
            item
            for item in dependencies["dependencies"]
            if item["owner"] == "source-faithfulness-reviewer"
        )
        source_dependency["projection"]["terminology_projection"] = {
            "delivery_glossary_id": "issue113-current-glossary-v2",
            "required_term_ids": ["workspace", "checkpoint"],
        }
        source_dependency["projection_sha256"] = hashlib.sha256(
            canonical_json_bytes(source_dependency["projection"])
        ).hexdigest()
        dependencies["dependencies_sha256"] = fingerprint(
            dependencies, "dependencies_sha256"
        )
        candidate_dependencies_path = write_json(
            run / "待删除/issue113/candidate-semantic-dependencies.json",
            dependencies,
        )
        arguments["semantic_dependencies_path"] = candidate_dependencies_path
        return provider, arguments

    def test_public_repair_admits_changed_glossary_with_stable_reader_text_and_generations(
        self,
    ) -> None:
        provider, arguments = self._semantic_only_case()
        predecessor = Path(arguments["predecessor_workspace_root"])
        predecessor_generations = read_json(predecessor / "artifact-generations.json")
        predecessor_inventory = read_json(predecessor / "reader-facing-text-inventory.json")
        predecessor_skeletons = {
            owner: read_json(
                predecessor
                / "reviewers"
                / owner
                / "input/review-skeleton.json"
            )
            for owner in PRECOMPILE_OWNERS
        }

        result = provider.promote(**arguments)

        successor_generations = read_json(Path(result["successor_generation_set_path"]))
        successor_inventory = read_json(Path(result["successor_inventory_path"]))
        successor_dependencies = read_json(
            Path(result["successor_semantic_dependencies_path"])
        )
        self.assertEqual(
            [
                (item["logical_id"], item["generation"], item["sha256"])
                for item in predecessor_generations["artifacts"]
            ],
            [
                (item["logical_id"], item["generation"], item["sha256"])
                for item in successor_generations["artifacts"]
            ],
        )
        self.assertEqual(
            predecessor_inventory["reader_text_set_sha256"],
            successor_inventory["reader_text_set_sha256"],
        )
        self.assertNotEqual(
            predecessor_inventory["delivery_glossary"],
            successor_inventory["delivery_glossary"],
        )
        self.assertNotEqual(
            read_json(predecessor / "semantic-dependencies.json")[
                "dependencies_sha256"
            ],
            successor_dependencies["dependencies_sha256"],
        )
        self.assertEqual(
            ["delivery_glossary", "semantic_dependencies"],
            read_json(Path(result["repair_attempt_path"]))[
                "advanced_semantic_input_ids"
            ],
        )
        successor_workspace = Path(arguments["workspace_root"])
        for owner in PRECOMPILE_OWNERS:
            successor_skeleton = read_json(
                successor_workspace
                / "reviewers"
                / owner
                / "input/review-skeleton.json"
            )
            self.assertNotEqual(
                predecessor_skeletons[owner]["task_id"],
                successor_skeleton["task_id"],
            )
            self.assertEqual(
                successor_inventory["delivery_glossary"],
                successor_skeleton["delivery_glossary"],
            )
        old_patch = (
            predecessor
            / "reviewers/writing-quality-reviewer/output/judgment-patch.json"
        )
        with self.assertRaises(ContractError):
            PrecompileQualityProvider(provider.project_root).commit_patch(
                workspace_root=successor_workspace,
                owner="writing-quality-reviewer",
                patch_path=old_patch,
                committed_at="2026-09-06T00:04:00Z",
            )

    def test_public_repair_rejects_identical_governed_inputs_before_successor_publication(
        self,
    ) -> None:
        # scenario_id: issue113_identical_governed_inputs
        # target_invariant: a repair advances content or a governed semantic input
        # mutation_seam: candidate Glossary binding is restored to the predecessor value
        # rematerialized_nodes: candidate inventory fingerprint and immutable bundle bytes
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_input_advance
        # expected_error_code: precompile_repair_evaluation_inputs_unchanged
        # scenario_class: single_contradiction
        provider, arguments = self._semantic_only_case()
        predecessor = Path(arguments["predecessor_workspace_root"])
        candidate_path = Path(arguments["inventory_path"])
        candidate = read_json(candidate_path)
        candidate["delivery_glossary"] = read_json(
            predecessor / "reader-facing-text-inventory.json"
        )["delivery_glossary"]
        candidate["inventory_sha256"] = fingerprint(candidate, "inventory_sha256")
        write_json(candidate_path, candidate)
        arguments["semantic_dependencies_path"] = (
            predecessor / "semantic-dependencies.json"
        )
        successor_workspace = Path(arguments["workspace_root"])

        with self.assertRaises(ContractError) as raised:
            provider.promote(**arguments)

        self.assertEqual(
            "precompile_repair_input_advance",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_evaluation_inputs_unchanged",
            raised.exception.data["error_code"],
        )
        self.assertFalse(successor_workspace.exists())

    def test_public_repair_exact_replay_returns_existing_semantic_successor(
        self,
    ) -> None:
        provider, arguments = self._semantic_only_case()
        first = provider.promote(**arguments)
        attempt_path = Path(first["repair_attempt_path"])
        attempt_bytes = attempt_path.read_bytes()
        skeleton_bytes = {
            path: path.read_bytes()
            for path in Path(arguments["workspace_root"]).glob(
                "reviewers/*/input/review-skeleton.json"
            )
        }

        replayed = provider.promote(**arguments)

        self.assertEqual("precompile_repair_already_promoted", replayed["classification"])
        self.assertEqual(first["repair_attempt_path"], replayed["repair_attempt_path"])
        self.assertEqual(attempt_bytes, attempt_path.read_bytes())
        self.assertEqual(
            skeleton_bytes,
            {path: path.read_bytes() for path in skeleton_bytes},
        )


if __name__ == "__main__":
    unittest.main()

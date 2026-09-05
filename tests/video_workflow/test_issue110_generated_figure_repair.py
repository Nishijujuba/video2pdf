from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow.test_issue105_content_repair_handoff import (
    fingerprint,
    write_json,
)
from tests.video_workflow.test_issue107_source_backed_figures import (
    Issue107SourceBackedFigureTests,
    png_bytes,
)
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.delivery_quality import DeliveryQualityRegistry
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.precompile_repair_promotion import (
    PrecompileRepairPromotionProvider,
)
from video2pdf_workflow_kernel.utils import canonical_json_bytes, read_json


class Issue110GeneratedFigureRepairTests(unittest.TestCase):
    def _rematerialize_figure_authority(
        self,
        *,
        run: Path,
        compile_manifest: dict,
        predecessor_generations: dict,
        manifest: dict,
        candidate: dict,
    ) -> dict:
        manifest_path = write_json(
            run / "work/figures/figure_demo.manifest.json", manifest
        )
        manifest_bytes = manifest_path.read_bytes()
        state_path = run / "workflow/production-state.json"
        state = read_json(state_path)
        artifact = state["artifacts"]["figure_manifest_figure_demo"]
        artifact["sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        artifact["size"] = len(manifest_bytes)
        write_json(state_path, state)
        for item in candidate["dependencies"][0]["projection"]["evidence"]:
            if item["path"] == manifest_path.relative_to(run).as_posix():
                item["sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        generations = PrecompileRepairPromotionProvider._derive_successor_generations(
            run_id=read_json(run / "workflow/run.json")["run_id"],
            compile_manifest=compile_manifest,
            predecessor=predecessor_generations,
            production_state_sha256=hashlib.sha256(state_path.read_bytes()).hexdigest(),
        )
        DeliveryQualityRegistry(PROJECT_ROOT).validate(
            "precompile-artifact-generation-set", generations
        )
        self.assertEqual(
            fingerprint(generations, "generation_set_sha256"),
            generations["generation_set_sha256"],
        )
        return generations

    def _generated_fixture(
        self, *, regenerated: bool
    ) -> tuple[Path, Path, dict, dict, dict, dict]:
        shared = Issue107SourceBackedFigureTests(
            "test_source_backed_replacement_derives_current_transform_evidence"
        )
        run, output_root, compile_manifest, generations, candidate = shared._fixture()
        asset_path = run / "figures/figure_demo.png"
        manifest_path = run / "work/figures/figure_demo.manifest.json"
        contribution_path = run / "work/figures/figure_demo.tex"
        contribution = b"Generated diagram contribution for the corrected evidence."
        contribution_path.write_bytes(contribution)
        asset_sha256 = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        prior_asset_sha256 = (
            hashlib.sha256(png_bytes(360, 70, b"prior")).hexdigest()
            if regenerated
            else asset_sha256
        )
        prior_source = {
            "kind": "generated_diagram",
            "value": "supporting source interval 00:05:03--00:05:20",
        }
        current_source = {
            "kind": "generated_diagram",
            "value": "supporting source intervals 00:05:03--00:05:20 and 00:07:11--00:07:29",
        }
        manifest = read_json(manifest_path)
        manifest["source"] = current_source
        manifest["slot_contribution_sha256"] = hashlib.sha256(contribution).hexdigest()
        manifest["authoritative_reader_text"] = {
            "text": "Current generated diagram labels",
            "completeness": "reviewed_complete",
            "unresolved_spans": [],
            "asset_path": manifest["asset_path"],
            "asset_sha256": asset_sha256,
        }
        ContractRegistry(PROJECT_ROOT).validate("figure-manifest", manifest)

        prior_manifest = deepcopy(manifest)
        prior_manifest["asset_sha256"] = prior_asset_sha256
        prior_manifest["source"] = prior_source
        prior_manifest["authoritative_reader_text"]["asset_sha256"] = (
            prior_asset_sha256
        )
        provenance_path = (
            run / "review/precompile/original/evidence/visual-source-provenance.json"
        )
        provenance = read_json(provenance_path)
        prior_visual = provenance["visual_evidence"][0]
        prior_visual["figure_asset"]["sha256"] = prior_asset_sha256
        prior_visual["figure_manifest"]["sha256"] = hashlib.sha256(
            canonical_json_bytes(prior_manifest)
        ).hexdigest()
        prior_visual["source"] = prior_source
        provenance["manifest_sha256"] = fingerprint(provenance, "manifest_sha256")
        write_json(provenance_path, provenance)
        for item in candidate["dependencies"][0]["projection"]["evidence"]:
            if item["path"] == provenance_path.relative_to(run).as_posix():
                item["sha256"] = hashlib.sha256(provenance_path.read_bytes()).hexdigest()

        generations = self._rematerialize_figure_authority(
            run=run,
            compile_manifest=compile_manifest,
            predecessor_generations=generations,
            manifest=manifest,
            candidate=candidate,
        )
        inventory_item = {
            "applicable_rule_ids": ["argument_chain_integrity"],
            "declaration_basis": "current_figure_manifest",
            "declared_text": "Prior generated diagram labels",
            "item_id": "raster.figure_asset_figure_demo",
            "kind": "figure_text",
            "language_profile_id": "zh-hans",
            "locator": "raster:figures/figure_demo.png",
            "representation": "authoritative_raster_text",
            "semantic_region": "figure_asset_figure_demo",
            "source_artifact_logical_id": "figure_asset_figure_demo",
            "source_generation": 1,
            "source_sha256": prior_asset_sha256,
            "text_sha256": hashlib.sha256(
                b"Prior generated diagram labels"
            ).hexdigest(),
        }
        inventory_item["item_sha256"] = fingerprint(inventory_item, "item_sha256")
        inventory = {
            "schema_name": "reader-facing-text-inventory",
            "schema_version": "1.0.0",
            "language_profile_id": "zh-hans",
            "delivery_glossary": None,
            "items": [inventory_item],
            "declared_surface": [
                {"kind": "figure_text", "region_id": inventory_item["item_id"]}
            ],
            "coverage_ledger": [
                {
                    "item_id": inventory_item["item_id"],
                    "region_id": inventory_item["item_id"],
                    "status": "covered",
                }
            ],
            "extractors": [{"extractor_id": "issue110-fixture"}],
        }
        return (
            run,
            output_root,
            compile_manifest,
            generations,
            candidate,
            inventory,
        )

    def test_generated_diagram_source_interval_correction_needs_no_transform(self) -> None:
        run, output_root, compile_manifest, generations, candidate, _inventory = (
            self._generated_fixture(regenerated=False)
        )
        successor = PrecompileRepairPromotionProvider._derive_successor_dependencies(
            run_dir=run,
            candidate=candidate,
            compile_manifest=compile_manifest,
            generations=generations,
            output_root=output_root,
            prepared_at="2026-09-06T04:30:00Z",
        )
        projection = successor["dependencies"][0]["projection"]
        derived = read_json(run / projection["visual_source_provenance"])
        visual = derived["visual_evidence"][0]
        self.assertEqual(
            "supporting source intervals 00:05:03--00:05:20 and 00:07:11--00:07:29",
            visual["source"]["value"],
        )
        self.assertNotIn("transform_evidence", visual)

    def test_regenerated_generated_diagram_binds_current_figure_authority(self) -> None:
        run, output_root, compile_manifest, generations, candidate, inventory = (
            self._generated_fixture(regenerated=True)
        )
        successor = PrecompileRepairPromotionProvider._derive_successor_dependencies(
            run_dir=run,
            candidate=candidate,
            compile_manifest=compile_manifest,
            generations=generations,
            output_root=output_root,
            prepared_at="2026-09-06T04:31:00Z",
        )
        successor_inventory = (
            PrecompileRepairPromotionProvider._derive_successor_inventory(
                run_dir=run,
                compile_manifest=compile_manifest,
                generations=generations,
                candidate=inventory,
                operation_id="issue110-generated-regeneration",
            )
        )
        DeliveryQualityRegistry(PROJECT_ROOT).validate(
            "reader-facing-text-inventory", successor_inventory
        )
        manifest_path = run / "work/figures/figure_demo.manifest.json"
        manifest = read_json(manifest_path)
        contribution_path = run / manifest["slot_contribution_path"]
        projection = successor["dependencies"][0]["projection"]
        visual = read_json(run / projection["visual_source_provenance"])[
            "visual_evidence"
        ][0]
        self.assertEqual(manifest["asset_sha256"], visual["figure_asset"]["sha256"])
        self.assertEqual(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            visual["figure_manifest"]["sha256"],
        )
        self.assertEqual(manifest["source"], visual["source"])
        self.assertEqual(
            hashlib.sha256(contribution_path.read_bytes()).hexdigest(),
            manifest["slot_contribution_sha256"],
        )
        self.assertEqual(
            manifest["authoritative_reader_text"]["text"],
            successor_inventory["items"][0]["declared_text"],
        )

    def test_changed_native_source_timestamp_without_transform_fails_at_transform_gate(
        self,
    ) -> None:
        # scenario_id: issue110_changed_native_figure_missing_transform
        # target_invariant: changed source_timestamp Figure has transform evidence
        # mutation_seam: current Figure Manifest transform_evidence removal
        # rematerialized_nodes: Figure Manifest, Production state, candidate evidence,
        # provider-derived Artifact Generation Set
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_figure_transform_record
        # expected_error_code: precompile_repair_transform_required
        # scenario_class: single_contradiction
        shared = Issue107SourceBackedFigureTests(
            "test_source_backed_replacement_derives_current_transform_evidence"
        )
        run, output_root, compile_manifest, generations, candidate = shared._fixture()
        manifest = read_json(run / "work/figures/figure_demo.manifest.json")
        manifest["source"].pop("transform_evidence")
        generations = self._rematerialize_figure_authority(
            run=run,
            compile_manifest=compile_manifest,
            predecessor_generations=generations,
            manifest=manifest,
            candidate=candidate,
        )
        with self.assertRaises(ContractError) as raised:
            PrecompileRepairPromotionProvider._derive_successor_dependencies(
                run_dir=run,
                candidate=candidate,
                compile_manifest=compile_manifest,
                generations=generations,
                output_root=output_root,
                prepared_at="2026-09-06T04:32:00Z",
            )
        self.assertEqual(
            "precompile_repair_figure_transform_record",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_transform_required",
            raised.exception.data["error_code"],
        )


if __name__ == "__main__":
    unittest.main()

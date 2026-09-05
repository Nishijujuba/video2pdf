from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
import sys
import unittest

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow.test_issue105_content_repair_handoff import (
    Issue105ContentRepairHandoffTests,
    fingerprint,
    write_json,
)
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.delivery_quality import DeliveryQualityRegistry
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.precompile_repair_promotion import (
    PrecompileRepairPromotionProvider,
)
from video2pdf_workflow_kernel.utils import read_json


def png_bytes(width: int, height: int, marker: bytes) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), tuple(marker[:3])).save(buffer, format="PNG")
    return buffer.getvalue()


class Issue107SourceBackedFigureTests(unittest.TestCase):
    def _fixture(self, contradiction: str | None = None) -> tuple[Path, Path, dict, dict, dict]:
        legacy = Issue105ContentRepairHandoffTests(
            "test_successor_dependencies_publish_fresh_current_visual_provenance"
        )
        run, output_root, compile_manifest, generations, _inventory, _ = (
            legacy._visual_derivation_fixture()
        )
        frame_path = run / "待删除/source-transform/frame.png"
        panel_path = run / "待删除/source-transform/panel.png"
        asset_path = run / "figures/figure_demo.png"
        frame_path.parent.mkdir(parents=True)
        frame_path.write_bytes(png_bytes(1920, 1080, b"frame"))
        panel_path.write_bytes(png_bytes(360, 70, b"panel"))
        asset_path.write_bytes(png_bytes(360, 70, b"output"))
        asset_sha256 = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        provenance_path = (
            run / "review/precompile/original/evidence/visual-source-provenance.json"
        )
        provenance = read_json(provenance_path)
        prior_visual = provenance["visual_evidence"][0]
        prior_visual["figure_asset"]["sha256"] = "0" * 64
        prior_visual["source"] = {"kind": "source_timestamp", "value": "00:05:03"}
        provenance["manifest_sha256"] = fingerprint(provenance, "manifest_sha256")
        write_json(provenance_path, provenance)
        transform = {
            "schema_name": "source-frame-detail-transform",
            "schema_version": "1.0.0",
            "source_video": dict(provenance["source"]["video"]),
            "decoded_frame": {
                "path": frame_path.relative_to(run).as_posix(),
                "sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
                "width": 1920,
                "height": 1080,
                "actual_frame_timestamp": "00:05:18.017700",
            },
            "panels": [
                {
                    "path": panel_path.relative_to(run).as_posix(),
                    "sha256": hashlib.sha256(panel_path.read_bytes()).hexdigest(),
                    "crop": {"x": 600, "y": 285, "width": 360, "height": 70},
                    "order": 1,
                    "role": "command_run_state",
                }
            ],
            "composition": {
                "method": "native-pixel crop",
                "order": ["command_run_state"],
                "scaling": "none",
                "output": {
                    "path": asset_path.relative_to(run).as_posix(),
                    "sha256": asset_sha256,
                    "width": 360,
                    "height": 70,
                },
            },
        }
        if contradiction == "source_video":
            transform["source_video"]["sha256"] = "1" * 64
        elif contradiction == "output_asset":
            transform["composition"]["output"]["sha256"] = "2" * 64
        transform_path = write_json(
            run / "待删除/source-transform/transform.json", transform
        )
        manifest_path = run / "work/figures/figure_demo.manifest.json"
        manifest = read_json(manifest_path)
        manifest["asset_sha256"] = asset_sha256
        manifest["source"] = {
            "kind": "source_timestamp",
            "value": "00:05:18.017700",
            "transform_evidence": {
                "path": transform_path.relative_to(run).as_posix(),
                "sha256": hashlib.sha256(transform_path.read_bytes()).hexdigest(),
            },
        }
        write_json(manifest_path, manifest)
        state_path = run / "workflow/production-state.json"
        state = read_json(state_path)
        state["artifacts"]["figure_manifest_figure_demo"]["sha256"] = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        state["artifacts"]["figure_manifest_figure_demo"]["size"] = len(
            manifest_path.read_bytes()
        )
        write_json(state_path, state)
        compile_manifest["entries"][0]["sha256"] = asset_sha256
        for entry in compile_manifest["entries"]:
            entry["producer"] = (
                "task:figure-demo"
                if entry["logical_id"].startswith("figure_asset_")
                else "provider:section-integration"
            )
        generations = PrecompileRepairPromotionProvider._derive_successor_generations(
            run_id=read_json(run / "workflow/run.json")["run_id"],
            compile_manifest=compile_manifest,
            predecessor=generations,
            production_state_sha256=hashlib.sha256(state_path.read_bytes()).hexdigest(),
        )
        DeliveryQualityRegistry(PROJECT_ROOT).validate(
            "precompile-artifact-generation-set", generations
        )
        self.assertEqual(
            fingerprint(generations, "generation_set_sha256"),
            generations["generation_set_sha256"],
        )
        evidence = [*provenance["source"].values()]
        evidence.extend(
            [
                {
                    "path": provenance_path.relative_to(run).as_posix(),
                    "sha256": hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
                },
                {
                    "path": asset_path.relative_to(run).as_posix(),
                    "sha256": asset_sha256,
                },
                {
                    "path": manifest_path.relative_to(run).as_posix(),
                    "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                },
            ]
        )
        projection = {
            "evidence": evidence,
            "primary_source": provenance["source"]["subtitle"]["path"],
            "projection_id": "youtube-source-faithfulness-evaluation-with-visual-provenance",
            "source_manifest": provenance["source"]["manifest"]["path"],
            "visual_source_provenance": provenance_path.relative_to(run).as_posix(),
        }
        candidate = {
            "schema_name": "precompile-semantic-dependencies",
            "schema_version": "1.0.0",
            "dependencies": [
                {
                    "owner": "source-faithfulness-reviewer",
                    "projection_id": projection["projection_id"],
                    "projection": projection,
                }
            ],
        }
        return run, output_root, compile_manifest, generations, candidate

    def test_source_backed_replacement_derives_current_transform_evidence(self) -> None:
        run, output_root, compile_manifest, generations, candidate = self._fixture()
        manifest = read_json(run / "work/figures/figure_demo.manifest.json")
        ContractRegistry(PROJECT_ROOT).validate("figure-manifest", manifest)
        successor = PrecompileRepairPromotionProvider._derive_successor_dependencies(
            run_dir=run,
            candidate=candidate,
            compile_manifest=compile_manifest,
            generations=generations,
            output_root=output_root,
            prepared_at="2026-09-06T02:00:00Z",
        )
        projection = successor["dependencies"][0]["projection"]
        evidence_paths = [item["path"] for item in projection["evidence"]]
        self.assertEqual(len(evidence_paths), len(set(evidence_paths)))
        self.assertIn("待删除/source-transform/transform.json", evidence_paths)
        self.assertIn("待删除/source-transform/frame.png", evidence_paths)
        self.assertIn("待删除/source-transform/panel.png", evidence_paths)
        derived = read_json(run / projection["visual_source_provenance"])
        self.assertEqual(
            manifest["source"]["transform_evidence"],
            derived["visual_evidence"][0]["transform_evidence"]["record"],
        )

    def test_source_backed_replacement_rejects_wrong_source_video_at_first_gate(self) -> None:
        # scenario_id: issue107_wrong_source_video
        # target_invariant: transform source video equals verified visual provenance video
        # mutation_seam: transform source_video sha256
        # rematerialized_nodes: transform file, Figure Manifest binding, Production state,
        # compile manifest, provider-derived Artifact Generation Set
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_figure_transform_source
        # expected_error_code: precompile_repair_transform_source_video_mismatch
        # scenario_class: single_contradiction
        run, output_root, compile_manifest, generations, candidate = self._fixture(
            "source_video"
        )
        with self.assertRaises(ContractError) as raised:
            PrecompileRepairPromotionProvider._derive_successor_dependencies(
                run_dir=run,
                candidate=candidate,
                compile_manifest=compile_manifest,
                generations=generations,
                output_root=output_root,
                prepared_at="2026-09-06T02:00:00Z",
            )
        self.assertEqual(
            "precompile_repair_figure_transform_source",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_transform_source_video_mismatch",
            raised.exception.data["error_code"],
        )

    def test_source_backed_replacement_rejects_wrong_output_asset_at_first_gate(self) -> None:
        # scenario_id: issue107_wrong_output_asset
        # target_invariant: transform output equals current Figure asset
        # mutation_seam: transform composition.output sha256
        # rematerialized_nodes: transform file, Figure Manifest binding, Production state,
        # compile manifest, provider-derived Artifact Generation Set
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_figure_transform_output
        # expected_error_code: precompile_repair_transform_output_asset_mismatch
        # scenario_class: single_contradiction
        run, output_root, compile_manifest, generations, candidate = self._fixture(
            "output_asset"
        )
        with self.assertRaises(ContractError) as raised:
            PrecompileRepairPromotionProvider._derive_successor_dependencies(
                run_dir=run,
                candidate=candidate,
                compile_manifest=compile_manifest,
                generations=generations,
                output_root=output_root,
                prepared_at="2026-09-06T02:00:00Z",
            )
        self.assertEqual(
            "precompile_repair_figure_transform_output",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_transform_output_asset_mismatch",
            raised.exception.data["error_code"],
        )


if __name__ == "__main__":
    unittest.main()

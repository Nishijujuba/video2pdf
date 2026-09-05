from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_case_dir
from tests.video_workflow.test_precompile_quality import (
    generation_set as valid_generation_set,
    inventory as valid_inventory,
    semantic_dependencies as valid_semantic_dependencies,
)
from tests.video_workflow.test_single_section_production import (
    PROJECT_ROOT as PRODUCTION_PROJECT_ROOT,
    SYSTEM_FONT,
    SingleSectionProductionTests,
)
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.guarded_compile import runtime_policy_for_fixture
from video2pdf_workflow_kernel.precompile_repair_promotion import (
    PrecompileRepairPromotionProvider,
)
from video2pdf_workflow_kernel.precompile_quality import (
    PRECOMPILE_OWNERS,
    PRECOMPILE_PROVIDER_ID,
    PRECOMPILE_PROVIDER_VERSION,
    PrecompileQualityProvider,
)
from video2pdf_workflow_kernel.runtime_refresh import CompileRuntimeRefreshProvider
from video2pdf_workflow_kernel.utils import canonical_json_bytes, read_json


def write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))
    return path


def fingerprint(value: dict, field: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes({key: item for key, item in value.items() if key != field})
    ).hexdigest()


class Issue106ReaderTextContinuationTests(unittest.TestCase):
    def test_changed_writer_result_is_part_of_the_authorized_producer_write_set(self) -> None:
        root = new_case_dir(self.id(), label="issue106-writer-result-write-set")
        run = root / "run"
        task_id = "9" * 32
        writer_path = run / "work/writers/section_02.tex"
        result_path = run / "work/writers/section_02.result.json"
        writer_path.parent.mkdir(parents=True)
        writer_path.write_text("unchanged section", encoding="utf-8")
        write_json(result_path, {"new_figure_candidates": []})
        write_json(
            run / "workflow/tasks" / task_id / "envelope.json",
            {"role": "writer", "section_id": "section_02"},
        )
        payload_root = run / "retained/payload/writers"
        payload_root.mkdir(parents=True)
        (payload_root / "section_02.tex").write_bytes(writer_path.read_bytes())
        payload_result = write_json(
            payload_root / "section_02.result.json",
            {
                "new_figure_candidates": [
                    {"slot_id": "figure_candidate", "teaching_purpose": "explain flow"}
                ]
            },
        )
        bundle_path = write_json(
            run / "retained/bundle.json",
            {
                "derived_payload": [
                    {
                        "path": "retained/payload/writers/section_02.tex",
                        "sha256": hashlib.sha256(writer_path.read_bytes()).hexdigest(),
                    },
                    {
                        "path": "retained/payload/writers/section_02.result.json",
                        "sha256": hashlib.sha256(payload_result.read_bytes()).hexdigest(),
                    },
                ]
            },
        )
        state = {
            "claims": {"writer-section_02": {"task_id": task_id}},
            "artifacts": {
                "writer_section_02": {"path": "work/writers/section_02.tex"},
                "writer_result_section_02": {
                    "path": "work/writers/section_02.result.json"
                },
            },
        }

        changed = PrecompileRepairPromotionProvider(PROJECT_ROOT)._changed_producer_write_set(
            run_dir=run,
            bundle_path=bundle_path,
            bundle=read_json(bundle_path),
            state=state,
            task_order=["writer-section_02"],
        )

        self.assertEqual(["work/writers/section_02.result.json"], changed)

    def _inventory_fixture(
        self, *, completeness: str = "reviewed_complete", unresolved: list[str] | None = None
    ) -> tuple[Path, dict, dict, dict]:
        root = new_case_dir(self.id(), label="issue106-reader-text")
        run = root / "run"
        write_json(run / "workflow/run.json", {"run_id": "1" * 32})
        asset_path = run / "figures/figure_demo.png"
        asset_path.parent.mkdir(parents=True)
        asset_path.write_bytes(b"current raster bytes")
        asset_sha256 = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        full_text = "AST — Abstract Syntax Tree\nCVE — Common Vulnerabilities and Exposures"
        figure_manifest = {
            "schema_name": "figure-manifest",
            "schema_version": "2.0.0",
            "kernel_version": "2.0.0",
            "slot_id": "figure_demo",
            "section_id": "section_02",
            "asset_path": "figures/figure_demo.png",
            "asset_sha256": asset_sha256,
            "caption": "A short caption",
            "source": {"kind": "source_timestamp", "value": "00:05:03--00:05:20"},
            "slot_contribution_path": "work/figures/figure_demo.tex",
            "slot_contribution_sha256": "2" * 64,
            "authoritative_reader_text": {
                "text": full_text,
                "completeness": completeness,
                "unresolved_spans": unresolved or [],
                "asset_path": "figures/figure_demo.png",
                "asset_sha256": asset_sha256,
            },
        }
        manifest_path = write_json(
            run / "work/figures/figure_demo.manifest.json", figure_manifest
        )
        write_json(
            run / "workflow/production-state.json",
            {
                "artifacts": {
                    "figure_manifest_figure_demo": {
                        "generation": 3,
                        "path": "work/figures/figure_demo.manifest.json",
                        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                    }
                }
            },
        )
        section_path = run / "work/integration/section_02.tex"
        section_path.parent.mkdir(parents=True)
        section_path.write_text(
            r"\includegraphics{figures/figure_demo.png}\begin{insightbox}x\end{insightbox}",
            encoding="utf-8",
        )
        style_path = run / "latex/course.sty"
        style_path.parent.mkdir(parents=True)
        style_path.write_text(
            r"\newtcolorbox{insightbox}{title={Current generated title}}",
            encoding="utf-8",
        )
        entries = [
            {
                "logical_id": "figure_asset_figure_demo",
                "generation": 3,
                "sha256": asset_sha256,
                "source_path": "figures/figure_demo.png",
            },
            {
                "logical_id": "integrated_section_02",
                "generation": 4,
                "sha256": hashlib.sha256(section_path.read_bytes()).hexdigest(),
                "source_path": "work/integration/section_02.tex",
            },
            {
                "logical_id": "local_style",
                "generation": 2,
                "sha256": hashlib.sha256(style_path.read_bytes()).hexdigest(),
                "source_path": "latex/course.sty",
            },
        ]
        generations = {
            "artifacts": [
                {key: entry[key] for key in ("logical_id", "generation", "sha256")}
                for entry in entries
            ],
            "generation_set_sha256": "3" * 64,
        }
        raster = {
            "item_id": "raster.figure_asset_figure_demo",
            "kind": "figure_text",
            "representation": "authoritative_raster_text",
            "source_artifact_logical_id": "figure_asset_figure_demo",
            "source_generation": 2,
            "source_sha256": asset_sha256,
            "locator": "raster:figures/figure_demo.png",
            "declared_text": "old caption",
            "text_sha256": hashlib.sha256(b"old caption").hexdigest(),
        }
        raster["item_sha256"] = fingerprint(raster, "item_sha256")
        generated = {
            "item_id": "generated.local-style",
            "kind": "generated_text",
            "representation": "declared_generated_text",
            "source_artifact_logical_id": "local_style",
            "source_generation": 1,
            "source_sha256": style_path.read_bytes().hex()[:64].ljust(64, "0"),
            "locator": "latex-generated:predecessor/old.sty/newtcolorbox-title",
            "declared_text": "Old generated title",
            "text_sha256": hashlib.sha256(b"Old generated title").hexdigest(),
        }
        generated["item_sha256"] = fingerprint(generated, "item_sha256")
        items = [raster, generated]
        inventory = {
            "schema_name": "reader-facing-text-inventory",
            "schema_version": "1.0.0",
            "items": items,
            "declared_surface": [
                {"kind": item["kind"], "region_id": item["item_id"]}
                for item in items
            ],
            "coverage_ledger": [
                {
                    "item_id": item["item_id"],
                    "region_id": item["item_id"],
                    "status": "covered",
                }
                for item in items
            ],
        }
        return run, {"entries": entries}, generations, inventory

    def test_successor_inventory_uses_complete_current_raster_declaration(self) -> None:
        run, compile_manifest, generations, inventory = self._inventory_fixture()
        successor = PrecompileRepairPromotionProvider._derive_successor_inventory(
            run_dir=run,
            compile_manifest=compile_manifest,
            generations=generations,
            candidate=inventory,
            operation_id="issue106",
        )
        raster = next(
            item
            for item in successor["items"]
            if item["representation"] == "authoritative_raster_text"
        )
        self.assertEqual(
            "AST — Abstract Syntax Tree\nCVE — Common Vulnerabilities and Exposures",
            raster["declared_text"],
        )
        self.assertNotEqual("A short caption", raster["declared_text"])

    def test_successor_inventory_blocks_unresolved_raster_text(self) -> None:
        # scenario_id: issue106_unresolved_raster_text
        # target_invariant: every referenced raster has reviewed complete visible text
        # mutation_seam: Figure Manifest completeness declaration
        # rematerialized_nodes: Production State Figure Manifest byte binding
        # intentionally_stale_nodes: none
        # expected_first_gate: reader_text_raster_completeness
        # expected_error_code: precompile_repair_raster_text_unresolved
        # scenario_class: single_contradiction
        run, compile_manifest, generations, inventory = self._inventory_fixture(
            completeness="unresolved", unresolved=["lower-right code fragment"]
        )
        with self.assertRaises(ContractError) as raised:
            PrecompileRepairPromotionProvider._derive_successor_inventory(
                run_dir=run,
                compile_manifest=compile_manifest,
                generations=generations,
                candidate=inventory,
                operation_id="issue106",
            )
        self.assertEqual(
            "reader_text_raster_completeness",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_raster_text_unresolved",
            raised.exception.data["error_code"],
        )

    def test_successor_inventory_derives_generated_locator_from_current_artifact(self) -> None:
        run, compile_manifest, generations, inventory = self._inventory_fixture()
        successor = PrecompileRepairPromotionProvider._derive_successor_inventory(
            run_dir=run,
            compile_manifest=compile_manifest,
            generations=generations,
            candidate=inventory,
            operation_id="issue106",
        )
        generated = next(
            item
            for item in successor["items"]
            if item["representation"] == "declared_generated_text"
        )
        self.assertEqual(
            "latex-generated:latex/course.sty/newtcolorbox-title",
            generated["locator"],
        )
        self.assertEqual("Current generated title", generated["declared_text"])

    def _materialization_fixture(self) -> tuple[Path, PrecompileQualityProvider]:
        root = new_case_dir(self.id(), label="issue106-gap-materialization")
        workspace = root / "precompile"
        write_json(
            workspace / "artifact-generations.json",
            {
                "generation_set_sha256": "4" * 64,
                "producer_ids": ["producer-106"],
                "artifacts": [
                    {"logical_id": "integrated_section_02", "generation": 1, "sha256": "b" * 64}
                ],
            },
        )
        inventory = {
            "reader_text_set_sha256": "6" * 64,
            "language_profile_id": "zh-hans",
            "delivery_glossary": None,
        }
        inventory["inventory_sha256"] = fingerprint(
            inventory, "inventory_sha256"
        )
        write_json(workspace / "reader-facing-text-inventory.json", inventory)
        write_json(workspace / "semantic-dependencies.json", {"dependencies_sha256": "7" * 64})
        for index, owner in enumerate(PRECOMPILE_OWNERS):
            skeleton = {
                "task_id": f"{index + 1:032x}",
                "owner": owner,
                "generation_set_sha256": "4" * 64,
                "inventory_sha256": inventory["inventory_sha256"],
            }
            skeleton["skeleton_sha256"] = fingerprint(skeleton, "skeleton_sha256")
            skeleton_path = workspace / "reviewers" / owner / "input/review-skeleton.json"
            write_json(skeleton_path, skeleton)
            results = []
            gaps = []
            if owner == "writing-quality-reviewer":
                results = [
                    {
                        "result_key": "first_use:section_02",
                        "decision": "fail",
                        "violation_id": "TERM_FIRST_USE",
                        "evidence_locator": "section_02:AST",
                        "repair_write_set": ["work/writers/section_02.tex"],
                    }
                ]
                gaps = [
                    {
                        "gap_id": "raster-text-completeness",
                        "observation": "visible image text is incomplete",
                        "evidence_locator": "inventory:raster.figure_demo",
                    }
                ]
            patch_value = {
                "task_id": skeleton["task_id"],
                "owner": owner,
                "skeleton_sha256": skeleton["skeleton_sha256"],
                "generation_set_sha256": "4" * 64,
                "reviewer": {
                    "reviewer_id": f"reviewer-{index}",
                    "runtime_sha256": "8" * 64,
                    "independent_from_generation_producers": True,
                },
                "results": results,
                "contract_gaps": gaps,
            }
            patch_value["patch_sha256"] = fingerprint(patch_value, "patch_sha256")
            write_json(
                workspace / "reviewers" / owner / "output/judgment-patch.json",
                patch_value,
            )
            commit = {
                "state": "committed",
                "patch_sha256": patch_value["patch_sha256"],
                "generation_set_sha256": "4" * 64,
            }
            commit["commit_sha256"] = fingerprint(commit, "commit_sha256")
            write_json(
                workspace / "reviewers" / owner / "commit/patch-commit.json",
                commit,
            )
        provider = PrecompileQualityProvider(PROJECT_ROOT)
        provider.registry.check = lambda: None
        provider.registry.validate = lambda _name, _value: None
        provider._validate_generation_set = lambda _value: None
        provider._validate_dependencies = lambda _value: None
        provider._validate_inventory = lambda *_args: None
        provider._validate_skeleton_current = lambda *_args: None
        provider._validate_patch = lambda *_args: None
        provider._writing_projection = lambda: ({"rules": []}, "9" * 64, "a" * 64)
        return workspace, provider

    def test_materialized_contract_gap_retains_same_batch_failures_and_routing(self) -> None:
        workspace, provider = self._materialization_fixture()
        with self.assertRaises(ContractError) as raised:
            provider.materialize(
                workspace_root=workspace,
                provider_id=PRECOMPILE_PROVIDER_ID,
                provider_version=PRECOMPILE_PROVIDER_VERSION,
                materialized_at="2026-09-06T00:00:00Z",
            )
        brief = json.loads(
            Path(raised.exception.data["evidence_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["writing-quality-reviewer:first_use:section_02"],
            [f"{item['owner']}:{item['result_key']}" for item in brief["failure_set"]],
        )
        self.assertEqual(
            ["writing-quality-reviewer:first_use:section_02"],
            brief["repair_routing"]["parallel_repair_tasks"][0]["failure_keys"],
        )
        self.assertFalse(brief["semantic_attempt_budget_consumed"])

    def test_prepare_repair_accepts_dispositioned_legacy_gap_without_spending_budget(self) -> None:
        workspace, provider = self._materialization_fixture()
        with self.assertRaises(ContractError) as raised:
            provider.materialize(
                workspace_root=workspace,
                provider_id=PRECOMPILE_PROVIDER_ID,
                provider_version=PRECOMPILE_PROVIDER_VERSION,
                materialized_at="2026-09-06T00:00:00Z",
            )
        brief_path = Path(raised.exception.data["evidence_path"])
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
        brief.pop("failure_set")
        brief.pop("repair_routing")
        brief.pop("committed_patch_bindings")
        brief["brief_sha256"] = fingerprint(brief, "brief_sha256")
        write_json(brief_path, brief)
        repaired_generations = {
            "generation_set_sha256": "c" * 64,
            "producer_ids": ["producer-106"],
            "artifacts": [
                {"logical_id": "integrated_section_02", "generation": 2, "sha256": "d" * 64}
            ],
        }
        repaired_path = write_json(workspace.parent / "candidate-generations.json", repaired_generations)
        bundle_path = write_json(workspace.parent / "bundle.json", {"bundle": "issue106"})
        disposition = {
            "schema_name": "content-repair-human-disposition",
            "schema_version": "2.0.0",
            "decision": "provider_repair_authorized",
            "approved_at": "2026-09-06T00:00:00Z",
            "approval_reference": "https://github.com/Nishijujuba/video2pdf/issues/106",
            "predecessor_contract_gap_brief_path": str(brief_path.resolve()),
            "predecessor_contract_gap_brief_sha256": brief["brief_sha256"],
            "generation_set_sha256": "4" * 64,
            "authorized_contract_gap_ids": ["raster-text-completeness"],
            "authorized_failure_keys": [
                "writing-quality-reviewer:first_use:section_02"
            ],
            "allowed_write_set": ["work/writers/section_02.tex"],
            "repair_bundle_path": str(bundle_path.resolve()),
            "repair_bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        }
        disposition["disposition_sha256"] = fingerprint(
            disposition, "disposition_sha256"
        )
        disposition_path = write_json(workspace.parent / "disposition.json", disposition)
        successor = workspace.parent / "successor"
        successor.mkdir(parents=True)
        provider.prepare = lambda **_kwargs: {
            "workspace_root": str(successor),
            "owner_count": 3,
            "generation_set_sha256": "c" * 64,
            "inventory_sha256": "f" * 64,
            "skeleton_paths": [
                str(workspace / "reviewers" / owner / "input/review-skeleton.json")
                for owner in PRECOMPILE_OWNERS
            ],
            "activation_status": "target_only",
        }
        result = provider.prepare_repair(
            predecessor_workspace_root=workspace,
            workspace_root=successor,
            inventory_path=workspace / "reader-facing-text-inventory.json",
            artifact_generations_path=repaired_path,
            semantic_dependencies_path=workspace / "semantic-dependencies.json",
            repair_attempt_number=1,
            prepared_at="2026-09-06T00:00:00Z",
            repair_disposition_path=disposition_path,
            repair_bundle_path=bundle_path,
            repair_sequence=2,
        )
        attempt = json.loads(
            Path(result["repair_attempt_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual("contract_gap_brief", attempt["predecessor_failure_authority"]["kind"])
        self.assertEqual(2, attempt["repair_sequence"])
        self.assertFalse(attempt["semantic_attempt_budget_consumed"])
        self.assertEqual(disposition["disposition_sha256"], attempt["disposition"]["disposition_sha256"])

    def test_legacy_gap_reconstruction_rejects_same_generation_wrong_inventory(self) -> None:
        # scenario_id: issue106_same_generation_wrong_inventory
        # target_invariant: a retained Gap brief owns its exact review inventory
        # mutation_seam: one retained Skeleton inventory binding
        # rematerialized_nodes: Skeleton fingerprint only
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_gap_predecessor_identity
        # expected_error_code: precompile_repair_gap_reviewer_binding_mismatch
        # scenario_class: single_contradiction
        workspace, provider = self._materialization_fixture()
        with self.assertRaises(ContractError) as raised:
            provider.materialize(
                workspace_root=workspace,
                provider_id=PRECOMPILE_PROVIDER_ID,
                provider_version=PRECOMPILE_PROVIDER_VERSION,
                materialized_at="2026-09-06T00:00:00Z",
            )
        brief = read_json(Path(raised.exception.data["evidence_path"]))
        owner = PRECOMPILE_OWNERS[0]
        skeleton_path = (
            workspace / "reviewers" / owner / "input/review-skeleton.json"
        )
        skeleton = read_json(skeleton_path)
        skeleton["inventory_sha256"] = "0" * 64
        skeleton["skeleton_sha256"] = fingerprint(
            skeleton, "skeleton_sha256"
        )
        write_json(skeleton_path, skeleton)
        with self.assertRaises(ContractError) as mismatch:
            provider.retained_contract_gap_evidence(
                workspace_root=workspace, brief=brief
            )
        self.assertEqual(
            "precompile_repair_gap_predecessor_identity",
            mismatch.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_gap_reviewer_binding_mismatch",
            mismatch.exception.data["error_code"],
        )

    def test_semantic_failure_chain_blocks_a_fourth_budgeted_attempt(self) -> None:
        workspace, provider = self._materialization_fixture()
        inventory = read_json(workspace / "reader-facing-text-inventory.json")
        report = {
            "schema_name": "precompile-quality-report",
            "schema_version": "1.0.0",
            "overall_decision": "fail",
            "generation_set_sha256": "4" * 64,
            "inventory_sha256": inventory["inventory_sha256"],
            "semantic_dependencies_sha256": "7" * 64,
            "failure_set": [
                {
                    "owner": "writing-quality-reviewer",
                    "result_key": "reader_wording:section_02",
                    "repair_write_set": [
                        "work/writers/section_02.result.json",
                        "work/writers/section_02.tex",
                    ],
                }
            ],
            "repair_routing": {
                "parallel_repair_tasks": [
                    {
                        "failure_keys": [
                            "writing-quality-reviewer:reader_wording:section_02"
                        ]
                    }
                ],
                "integration_repair_tasks": [],
            },
            "contract_gaps": [],
            "semantic_attempt_budget_consumed": True,
        }
        report["report_sha256"] = fingerprint(report, "report_sha256")
        write_json(workspace / "precompile-quality-report.json", report)
        predecessor_attempt = {
            "schema_name": "precompile-repair-attempt",
            "schema_version": "1.0.0",
            "repair_attempt_number": 3,
            "semantic_attempt_number": 3,
            "predecessor_report_sha256": "1" * 64,
            "repaired_generation_set_sha256": "4" * 64,
            "repaired_inventory_sha256": inventory["inventory_sha256"],
        }
        predecessor_attempt["attempt_sha256"] = fingerprint(
            predecessor_attempt, "attempt_sha256"
        )
        write_json(workspace / "repair-attempt.json", predecessor_attempt)
        repaired = {
            "generation_set_sha256": "c" * 64,
            "producer_ids": ["producer-106"],
            "artifacts": [
                {
                    "logical_id": "integrated_section_02",
                    "generation": 2,
                    "sha256": "d" * 64,
                }
            ],
        }
        repaired_path = write_json(
            workspace.parent / "budget-candidate-generations.json", repaired
        )
        with self.assertRaises(ContractError) as raised:
            provider.prepare_repair(
                predecessor_workspace_root=workspace,
                workspace_root=workspace.parent / "budget-successor",
                inventory_path=workspace / "reader-facing-text-inventory.json",
                artifact_generations_path=repaired_path,
                semantic_dependencies_path=workspace / "semantic-dependencies.json",
                repair_attempt_number=3,
                prepared_at="2026-09-06T00:00:00Z",
            )
        self.assertEqual(
            "precompile_repair_semantic_budget",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_semantic_budget_exhausted",
            raised.exception.data["error_code"],
        )

    def _runtime_continuation_fixture(self) -> dict:
        root = new_case_dir(self.id(), label="issue106-runtime-continuation")
        run = root / "run"
        workflow = run / "workflow"
        old_workspace = run / "review/precompile/workspaces/gap-01"
        new_workspace = run / "review/precompile/workspaces/repair-02"
        policy_path = write_json(workflow / "compile-runtime-policy.json", {})
        write_json(workflow / "production-state.json", {"run_id": "1" * 32})
        write_json(workflow / "compile-manifest.json", {})
        generations = {
            "schema_name": "precompile-artifact-generation-set",
            "schema_version": "1.0.0",
            "generation_set_id": "issue106-gap",
            "producer_ids": ["writer-106"],
            "artifacts": [
                {
                    "logical_id": "integrated_section_02",
                    "generation": 2,
                    "sha256": "a" * 64,
                }
            ],
        }
        generations["generation_set_sha256"] = fingerprint(
            generations, "generation_set_sha256"
        )
        generation_path = write_json(
            run / "review/precompile/production-repair-promotions/gap/artifact-generations.json",
            generations,
        )
        brief = {
            "schema_name": "precompile-contract-gap-brief",
            "schema_version": "1.0.0",
            "generation_set_sha256": generations["generation_set_sha256"],
            "inventory_sha256": "b" * 64,
            "contract_gaps": [
                {
                    "owner": "writing-quality-reviewer",
                    "gap_id": "raster-reader-text-incomplete",
                    "observation": "one raster declaration is incomplete",
                    "evidence_locator": "inventory:raster.figure_demo",
                }
            ],
            "semantic_attempt_budget_consumed": False,
            "routing": "human_policy_disposition_required",
        }
        brief["brief_sha256"] = fingerprint(brief, "brief_sha256")
        brief_path = write_json(
            old_workspace / "precompile-contract-gap-brief.json", brief
        )
        old_promotion = {
            "workspace_root": str(old_workspace.resolve()),
            "generation_set_path": str(generation_path.resolve()),
            "generation_set_sha256": generations["generation_set_sha256"],
            "generation_set_file_sha256": hashlib.sha256(
                generation_path.read_bytes()
            ).hexdigest(),
            "inventory_path": str(
                (run / "review/precompile/production-repair-promotions/gap/reader-facing-text-inventory.json").resolve()
            ),
            "semantic_dependencies_path": str(
                (run / "review/precompile/production-repair-promotions/gap/semantic-dependencies.json").resolve()
            ),
        }
        predecessor_inventory = {
            "inventory": "gap",
            "inventory_sha256": "b" * 64,
        }
        predecessor_dependencies = {
            "dependencies": "gap",
            "dependencies_sha256": "e" * 64,
        }
        write_json(Path(old_promotion["inventory_path"]), predecessor_inventory)
        write_json(
            Path(old_promotion["semantic_dependencies_path"]),
            predecessor_dependencies,
        )
        (old_workspace / "artifact-generations.json").write_bytes(
            generation_path.read_bytes()
        )
        write_json(
            old_workspace / "reader-facing-text-inventory.json",
            predecessor_inventory,
        )
        write_json(
            old_workspace / "semantic-dependencies.json",
            predecessor_dependencies,
        )
        bundle_path = write_json(
            run / "待删除/issue106-repair-bundle.json",
            {
                "schema_name": "production-repair-replay-bundle",
                "schema_version": "1.0.0",
                "run_id": "1" * 32,
                "input_snapshot": [],
                "derived_payload": [],
                "initial_claims": {},
                "task_order": [],
            },
        )
        operation_id = "operation-106"
        write_set = [
            "work/figures/figure_demo.manifest.json",
            "work/writers/section_02.result.json",
            "work/writers/section_02.tex",
        ]
        handoff = {
            "schema_name": "runtime-refresh-content-repair-handoff",
            "schema_version": "1.0.0",
            "state": "promotion_ready",
            "runtime_refresh_operation_id": operation_id,
            "runtime_policy_sha256": hashlib.sha256(
                policy_path.read_bytes()
            ).hexdigest(),
            "repair_bundle_path": str((run / "retained-old-bundle.json").resolve()),
            "repair_bundle_sha256": "c" * 64,
            "promotion": old_promotion,
        }
        handoff["handoff_sha256"] = fingerprint(handoff, "handoff_sha256")
        journal = {
            "schema_name": "compile-runtime-refresh-journal",
            "schema_version": "1.0.0",
            "state": "precompile_refresh_required",
            "operation_id": operation_id,
            "content_repair_handoff": handoff,
        }
        journal["journal_sha256"] = fingerprint(journal, "journal_sha256")
        active_path = write_json(workflow / "runtime-refresh-active.json", journal)
        disposition = {
            "schema_name": "content-repair-human-disposition",
            "schema_version": "2.0.0",
            "decision": "provider_repair_authorized",
            "approved_at": "2026-09-06T00:00:00Z",
            "approval_reference": "https://github.com/Nishijujuba/video2pdf/issues/106",
            "predecessor_contract_gap_brief_path": str(brief_path.resolve()),
            "predecessor_contract_gap_brief_sha256": brief["brief_sha256"],
            "authorized_contract_gap_ids": ["raster-reader-text-incomplete"],
            "authorized_failure_keys": [
                "writing-quality-reviewer:first_use:section_02"
            ],
            "allowed_write_set": write_set,
            "repair_bundle_path": str(bundle_path.resolve()),
            "repair_bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
            "generation_set_sha256": generations["generation_set_sha256"],
            "runtime_refresh_operation_id": operation_id,
            "runtime_policy_sha256": handoff["runtime_policy_sha256"],
            "predecessor_sequence": 0,
        }
        disposition["disposition_sha256"] = fingerprint(
            disposition, "disposition_sha256"
        )
        disposition_path = write_json(
            run / "待删除/issue106-disposition.json", disposition
        )
        successor_generations = {
            **generations,
            "generation_set_id": "issue106-repair",
            "artifacts": [
                {
                    **generations["artifacts"][0],
                    "generation": 3,
                    "sha256": "d" * 64,
                }
            ],
        }
        successor_generations["generation_set_sha256"] = fingerprint(
            successor_generations, "generation_set_sha256"
        )
        successor_generation_path = write_json(
            run / "review/precompile/production-repair-promotions/repair/artifact-generations.json",
            successor_generations,
        )
        return {
            "run": run,
            "provider": CompileRuntimeRefreshProvider(PROJECT_ROOT),
            "active_path": active_path,
            "operation_id": operation_id,
            "bundle_path": bundle_path,
            "brief_path": brief_path,
            "disposition_path": disposition_path,
            "old_promotion": old_promotion,
            "new_workspace": new_workspace,
            "generation_path": generation_path,
            "successor_generation_path": successor_generation_path,
            "write_set": write_set,
        }

    def test_runtime_continuation_appends_one_successor_and_replays_exactly(self) -> None:
        case = self._runtime_continuation_fixture()
        failure_evidence = {
            "failure_set": [
                {
                    "owner": "writing-quality-reviewer",
                    "result_key": "first_use:section_02",
                }
            ],
            "repair_routing": {},
        }
        with patch.object(
            PrecompileQualityProvider,
            "retained_contract_gap_evidence",
            return_value=failure_evidence,
        ):
            authorization = case["provider"].preflight_content_repair_promotion_refresh(
                run_dir=case["run"],
                expected_operation_id=case["operation_id"],
                repair_bundle_path=case["bundle_path"],
                disposition_path=case["disposition_path"],
                predecessor_contract_gap_brief_path=case["brief_path"],
                successor_workspace_root=case["new_workspace"],
                actual_write_set=case["write_set"],
            )
        case["new_workspace"].mkdir(parents=True)
        inventory_path = write_json(case["run"] / "derived/inventory.json", {})
        dependencies_path = write_json(case["run"] / "derived/dependencies.json", {})
        first = case["provider"].bind_content_repair_promotion(
            run_dir=case["run"],
            expected_operation_id=case["operation_id"],
            workspace_root=case["new_workspace"],
            generation_set_path=case["successor_generation_path"],
            inventory_path=inventory_path,
            semantic_dependencies_path=dependencies_path,
            disposition_path=case["disposition_path"],
            predecessor_contract_gap_brief_path=case["brief_path"],
            repair_bundle_path=case["bundle_path"],
            actual_write_set=case["write_set"],
            preflight_authorization=authorization,
        )
        active_bytes = case["active_path"].read_bytes()
        repeated = case["provider"].bind_content_repair_promotion(
            run_dir=case["run"],
            expected_operation_id=case["operation_id"],
            workspace_root=case["new_workspace"],
            generation_set_path=case["successor_generation_path"],
            inventory_path=inventory_path,
            semantic_dependencies_path=dependencies_path,
            disposition_path=case["disposition_path"],
            predecessor_contract_gap_brief_path=case["brief_path"],
            repair_bundle_path=case["bundle_path"],
        )
        self.assertEqual(first, repeated)
        self.assertEqual(active_bytes, case["active_path"].read_bytes())
        self.assertEqual([case["old_promotion"]], first["retained_prior_promotions"])
        self.assertEqual(authorization, first["promotion_refresh"])
        self.assertEqual(0, authorization["predecessor_sequence"])

    def test_prepare_repair_rejects_disposition_for_semantic_failure_authority(self) -> None:
        # scenario_id: issue106_semantic_failure_with_disposition
        # target_invariant: semantic failure reports cannot consume human Gap disposition
        # mutation_seam: repair_disposition_path supplied with a valid semantic failure report
        # rematerialized_nodes: none
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_failure_authority
        # expected_error_code: precompile_repair_semantic_authority_invalid
        # scenario_class: single_contradiction
        workspace, provider = self._materialization_fixture()
        inventory = read_json(workspace / "reader-facing-text-inventory.json")
        report = {
            "schema_name": "precompile-quality-report",
            "schema_version": "1.0.0",
            "overall_decision": "fail",
            "generation_set_sha256": "4" * 64,
            "inventory_sha256": inventory["inventory_sha256"],
            "semantic_dependencies_sha256": "7" * 64,
            "failure_set": [
                {
                    "owner": "writing-quality-reviewer",
                    "result_key": "reader_wording:section_02",
                    "repair_write_set": [
                        "work/writers/section_02.result.json",
                        "work/writers/section_02.tex",
                    ],
                }
            ],
            "repair_routing": {
                "parallel_repair_tasks": [
                    {
                        "failure_keys": [
                            "writing-quality-reviewer:reader_wording:section_02"
                        ]
                    }
                ],
                "integration_repair_tasks": [],
            },
            "contract_gaps": [],
            "semantic_attempt_budget_consumed": True,
        }
        report["report_sha256"] = fingerprint(report, "report_sha256")
        write_json(workspace / "precompile-quality-report.json", report)
        repaired = {
            "generation_set_sha256": "c" * 64,
            "producer_ids": ["producer-106"],
            "artifacts": [
                {
                    "logical_id": "integrated_section_02",
                    "generation": 2,
                    "sha256": "d" * 64,
                }
            ],
        }
        repaired_path = write_json(workspace.parent / "semantic-candidate.json", repaired)
        bundle_path = write_json(workspace.parent / "semantic-bundle.json", {})
        disposition_path = write_json(
            workspace.parent / "semantic-disposition.json",
            {"disposition_sha256": "a" * 64},
        )
        with self.assertRaises(ContractError) as raised:
            provider.prepare_repair(
                predecessor_workspace_root=workspace,
                workspace_root=workspace.parent / "semantic-successor",
                inventory_path=workspace / "reader-facing-text-inventory.json",
                artifact_generations_path=repaired_path,
                semantic_dependencies_path=workspace / "semantic-dependencies.json",
                repair_attempt_number=1,
                prepared_at="2026-09-06T00:00:00Z",
                repair_disposition_path=disposition_path,
                repair_bundle_path=bundle_path,
            )
        self.assertEqual(
            "precompile_repair_failure_authority",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_semantic_authority_invalid",
            raised.exception.data["error_code"],
        )

    def test_runtime_continuation_rejects_competing_successor_before_writes(self) -> None:
        # scenario_id: issue106_runtime_competing_successor
        # target_invariant: one continuation authorization owns one successor workspace
        # mutation_seam: a second successor path after a valid promotion refresh binding
        # rematerialized_nodes: complete promotion refresh and journal fingerprints
        # intentionally_stale_nodes: none
        # expected_first_gate: content_repair_continuation_successor
        # expected_error_code: runtime_refresh_continuation_competing_successor
        # scenario_class: single_contradiction
        case = self._runtime_continuation_fixture()
        failure_evidence = {
            "failure_set": [
                {
                    "owner": "writing-quality-reviewer",
                    "result_key": "first_use:section_02",
                }
            ],
            "repair_routing": {},
        }
        with patch.object(
            PrecompileQualityProvider,
            "retained_contract_gap_evidence",
            return_value=failure_evidence,
        ):
            authorization = case["provider"].preflight_content_repair_promotion_refresh(
                run_dir=case["run"],
                expected_operation_id=case["operation_id"],
                repair_bundle_path=case["bundle_path"],
                disposition_path=case["disposition_path"],
                predecessor_contract_gap_brief_path=case["brief_path"],
                successor_workspace_root=case["new_workspace"],
                actual_write_set=case["write_set"],
            )
        case["new_workspace"].mkdir(parents=True)
        inventory_path = write_json(case["run"] / "derived/competing-inventory.json", {})
        dependencies_path = write_json(
            case["run"] / "derived/competing-dependencies.json", {}
        )
        case["provider"].bind_content_repair_promotion(
            run_dir=case["run"],
            expected_operation_id=case["operation_id"],
            workspace_root=case["new_workspace"],
            generation_set_path=case["successor_generation_path"],
            inventory_path=inventory_path,
            semantic_dependencies_path=dependencies_path,
            disposition_path=case["disposition_path"],
            predecessor_contract_gap_brief_path=case["brief_path"],
            repair_bundle_path=case["bundle_path"],
            actual_write_set=case["write_set"],
            preflight_authorization=authorization,
        )
        competing = case["run"] / "review/precompile/workspaces/competing"
        before = case["active_path"].read_bytes()
        with self.assertRaises(ContractError) as raised:
            case["provider"].preflight_content_repair_promotion_refresh(
                run_dir=case["run"],
                expected_operation_id=case["operation_id"],
                repair_bundle_path=case["bundle_path"],
                disposition_path=case["disposition_path"],
                predecessor_contract_gap_brief_path=case["brief_path"],
                successor_workspace_root=competing,
                actual_write_set=case["write_set"],
            )
        self.assertEqual(
            "content_repair_continuation_successor",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "runtime_refresh_continuation_competing_successor",
            raised.exception.data["error_code"],
        )
        self.assertFalse(competing.exists())
        self.assertEqual(before, case["active_path"].read_bytes())

    def test_legacy_issue_specific_disposition_cannot_admit_a_new_successor(self) -> None:
        case = self._runtime_continuation_fixture()
        disposition = read_json(case["disposition_path"])
        disposition["schema_version"] = "1.0.0"
        disposition["disposition_sha256"] = fingerprint(
            disposition, "disposition_sha256"
        )
        write_json(case["disposition_path"], disposition)
        with self.assertRaises(ContractError) as raised:
            case["provider"].preflight_content_repair_promotion_refresh(
                run_dir=case["run"],
                expected_operation_id=case["operation_id"],
                repair_bundle_path=case["bundle_path"],
                disposition_path=case["disposition_path"],
                predecessor_contract_gap_brief_path=case["brief_path"],
                successor_workspace_root=case["new_workspace"],
                actual_write_set=case["write_set"],
            )
        self.assertEqual(
            "content_repair_continuation_disposition",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "runtime_refresh_legacy_disposition_read_only",
            raised.exception.data["error_code"],
        )

    def test_runtime_continuation_rejects_a_sealed_predecessor(self) -> None:
        # scenario_id: issue106_sealed_predecessor
        # target_invariant: a sealed workspace cannot become a repair predecessor
        # mutation_seam: publish a valid Seal at the predecessor lifecycle boundary
        # rematerialized_nodes: Seal fingerprint
        # intentionally_stale_nodes: none
        # expected_first_gate: content_repair_continuation_predecessor
        # expected_error_code: runtime_refresh_continuation_predecessor_sealed
        # scenario_class: single_contradiction
        case = self._runtime_continuation_fixture()
        predecessor = Path(case["old_promotion"]["workspace_root"])
        seal = read_json(PROJECT_ROOT / "delivery-quality/v1/precompile-text-seal.example.v1.json")
        seal["generation_set_sha256"] = read_json(case["generation_path"])[
            "generation_set_sha256"
        ]
        seal["inventory_sha256"] = read_json(
            predecessor / "reader-facing-text-inventory.json"
        )["inventory_sha256"]
        seal["semantic_dependencies_sha256"] = read_json(
            predecessor / "semantic-dependencies.json"
        )["dependencies_sha256"]
        seal["seal_sha256"] = fingerprint(seal, "seal_sha256")
        case["provider"].quality.validate("precompile-text-seal", seal)
        write_json(predecessor / "precompile-text-seal.json", seal)
        with self.assertRaises(ContractError) as raised:
            case["provider"].preflight_content_repair_promotion_refresh(
                run_dir=case["run"],
                expected_operation_id=case["operation_id"],
                repair_bundle_path=case["bundle_path"],
                disposition_path=case["disposition_path"],
                predecessor_contract_gap_brief_path=case["brief_path"],
                successor_workspace_root=case["new_workspace"],
                actual_write_set=case["write_set"],
            )
        self.assertEqual(
            "content_repair_continuation_predecessor",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "runtime_refresh_continuation_predecessor_sealed",
            raised.exception.data["error_code"],
        )

    def test_public_promotion_exact_replay_returns_the_recorded_gap_successor(self) -> None:
        case = self._runtime_continuation_fixture()
        failure_evidence = {
            "failure_set": [
                {
                    "owner": "writing-quality-reviewer",
                    "result_key": "first_use:section_02",
                }
            ],
            "repair_routing": {},
        }
        with patch.object(
            PrecompileQualityProvider,
            "retained_contract_gap_evidence",
            return_value=failure_evidence,
        ):
            authorization = case["provider"].preflight_content_repair_promotion_refresh(
                run_dir=case["run"],
                expected_operation_id=case["operation_id"],
                repair_bundle_path=case["bundle_path"],
                disposition_path=case["disposition_path"],
                predecessor_contract_gap_brief_path=case["brief_path"],
                successor_workspace_root=case["new_workspace"],
                actual_write_set=case["write_set"],
            )
        case["new_workspace"].mkdir(parents=True)
        published_inventory = {
            "inventory": "repaired",
            "inventory_sha256": "f" * 64,
        }
        published_dependencies = {
            "dependencies": "repaired",
            "dependencies_sha256": "9" * 64,
        }
        inventory_path = write_json(
            case["run"] / "derived/inventory.json", published_inventory
        )
        dependencies_path = write_json(
            case["run"] / "derived/dependencies.json", published_dependencies
        )
        handoff = case["provider"].bind_content_repair_promotion(
            run_dir=case["run"],
            expected_operation_id=case["operation_id"],
            workspace_root=case["new_workspace"],
            generation_set_path=case["successor_generation_path"],
            inventory_path=inventory_path,
            semantic_dependencies_path=dependencies_path,
            disposition_path=case["disposition_path"],
            predecessor_contract_gap_brief_path=case["brief_path"],
            repair_bundle_path=case["bundle_path"],
            actual_write_set=case["write_set"],
            preflight_authorization=authorization,
        )
        for source, name in (
            (case["successor_generation_path"], "artifact-generations.json"),
            (inventory_path, "reader-facing-text-inventory.json"),
            (dependencies_path, "semantic-dependencies.json"),
        ):
            (case["new_workspace"] / name).write_bytes(source.read_bytes())
        predecessor_generations = read_json(case["generation_path"])
        successor_generations = read_json(case["successor_generation_path"])
        attempt = {
            "schema_name": "precompile-repair-attempt",
            "schema_version": "1.0.0",
            "repair_attempt_number": 1,
            "prepared_at": "2026-09-06T00:00:00Z",
            "predecessor_failure_authority": {
                "kind": "contract_gap_brief",
                "path": str(case["brief_path"].resolve()),
                "sha256": read_json(case["brief_path"])["brief_sha256"],
            },
            "predecessor_generation_set_sha256": predecessor_generations[
                "generation_set_sha256"
            ],
            "repaired_generation_set_sha256": successor_generations[
                "generation_set_sha256"
            ],
            "repaired_inventory_sha256": published_inventory["inventory_sha256"],
            "disposition": {
                "disposition_sha256": authorization["disposition_sha256"]
            },
        }
        attempt["attempt_sha256"] = fingerprint(attempt, "attempt_sha256")
        write_json(case["new_workspace"] / "repair-attempt.json", attempt)
        active_before = case["active_path"].read_bytes()
        workspace_before = {
            path: path.read_bytes()
            for path in case["new_workspace"].rglob("*")
            if path.is_file()
        }
        provider = PrecompileRepairPromotionProvider(PROJECT_ROOT)
        provider.contracts.validate = lambda *_args: None
        provider.delivery_quality.validate = lambda *_args: None
        with patch.object(
            provider, "_required_replay_task_order", return_value=[]
        ), patch.object(
            provider, "_preflight_claim_plan", return_value=None
        ), patch(
            "video2pdf_workflow_kernel.precompile_repair_promotion.ContentProduction.require_current_diagnostic_compile_authority",
            return_value={"classification": "diagnostic_compile_current"},
        ):
            result = provider.promote(
                run_dir=case["run"],
                repair_bundle_path=case["bundle_path"],
                predecessor_workspace_root=Path(
                    case["old_promotion"]["workspace_root"]
                ),
                workspace_root=case["new_workspace"],
                inventory_path=Path(
                    case["old_promotion"]["workspace_root"]
                )
                / "reader-facing-text-inventory.json",
                semantic_dependencies_path=Path(
                    case["old_promotion"]["workspace_root"]
                )
                / "semantic-dependencies.json",
                repair_attempt_number=1,
                prepared_at="2026-09-06T00:00:00Z",
                runtime_refresh_operation_id=case["operation_id"],
                runtime_predecessor_final_compile_manifest_path=(
                    case["run"] / "workflow/compile-manifest.json"
                ),
                runtime_content_repair_disposition_path=case["disposition_path"],
                runtime_predecessor_contract_gap_brief_path=case["brief_path"],
            )
        self.assertEqual("precompile_repair_already_promoted", result["classification"])
        self.assertEqual(handoff["promotion"], result["runtime_refresh_handoff"]["promotion"])
        self.assertEqual(active_before, case["active_path"].read_bytes())
        self.assertEqual(
            workspace_before,
            {
                path: path.read_bytes()
                for path in case["new_workspace"].rglob("*")
                if path.is_file()
            },
        )

    def _complete_single_section_production(self) -> tuple[object, Path]:
        lifecycle = SingleSectionProductionTests(
            "test_public_plan_and_advance_reach_guarded_diagnostic_compile"
        )
        lifecycle.setUp()
        kernel = lifecycle.kernel
        run = lifecycle.run_dir

        outline = kernel.production_plan(run)["runnable_tasks"][0]
        attempt = lifecycle._attempt(
            outline, {"outline.json": lifecycle._outline_payload()}
        )
        kernel.production_advance(run, outline["task_id"], attempt)
        outline_gate = kernel.production_plan(run)["runnable_tasks"][0]
        attempt = lifecycle._attempt(
            outline_gate,
            {"pyramid-report.json": lifecycle._pyramid_payload(outline_gate)},
        )
        kernel.production_advance(run, outline_gate["task_id"], attempt)

        tasks = kernel.production_plan(run)["runnable_tasks"]
        writer = next(task for task in tasks if task["role"] == "writer")
        figure = next(task for task in tasks if task["role"] == "figure")
        writer_result = canonical_json_bytes(
            {
                "schema_name": "writer-result",
                "schema_version": "1.0.0",
                "section_id": "section_01",
                "new_figure_candidates": [],
            }
        )
        attempt = lifecycle._attempt(
            writer,
            {
                "section_01.tex": (
                    b"\\section{Core claim}\nDeclared inputs establish closure.\n"
                    b"% FIGURE_SLOT:figure_01\n"
                ),
                "writer-result.json": writer_result,
            },
        )
        kernel.production_advance(run, writer["task_id"], attempt)
        contribution = (
            b"\\begin{figure}[H]\n\\centering\n"
            b"\\includegraphics[width=0.76\\linewidth,height=0.34\\textheight,keepaspectratio]{figures/figure_01}\n"
            b"\\caption{Declared and observed compile inputs.}\n"
            b"\\par\\small Source (source\\_timestamp): 00:00:01\n"
            b"\\end{figure}\n"
        )
        figure_manifest = canonical_json_bytes(
            {
                "schema_name": "figure-manifest",
                "schema_version": "1.0.0",
                "kernel_version": "2.0.0",
                "slot_id": "figure_01",
                "section_id": "section_01",
                "asset_path": "figures/figure_01.png",
                "asset_sha256": hashlib.sha256(b"fixture-png").hexdigest(),
                "caption": "Declared and observed compile inputs.",
                "source": {"kind": "source_timestamp", "value": "00:00:01"},
                "slot_contribution_path": "work/figures/figure_01.tex",
                "slot_contribution_sha256": hashlib.sha256(contribution).hexdigest(),
            }
        )
        attempt = lifecycle._attempt(
            figure,
            {
                "figure_01.png": b"fixture-png",
                "figure-manifest.json": figure_manifest,
                "figure_01.tex": contribution,
            },
        )
        kernel.production_advance(run, figure["task_id"], attempt)
        section_gate = kernel.production_plan(run)["runnable_tasks"][0]
        attempt = lifecycle._attempt(
            section_gate,
            {"pyramid-report.json": lifecycle._pyramid_payload(section_gate)},
        )
        kernel.production_advance(run, section_gate["task_id"], attempt)
        main_gate = kernel.production_plan(run)["runnable_tasks"][0]
        attempt = lifecycle._attempt(
            main_gate,
            {"pyramid-report.json": lifecycle._pyramid_payload(main_gate)},
        )
        policy = runtime_policy_for_fixture(
            run_dir=run,
            engine_executable=Path(sys.executable),
            engine_prefix_args=[
                str(
                    PRODUCTION_PROJECT_ROOT
                    / "tests/video_workflow/fixtures/guarded-compile/fake_xelatex.py"
                )
            ],
            system_fonts=[SYSTEM_FONT],
        )
        result = kernel.production_advance(
            run,
            main_gate["task_id"],
            attempt,
            compile_runtime_policy=policy,
        )
        self.assertEqual("diagnostic_compile_ready", result["classification"])
        return kernel, run

    def _non_runtime_bound_repair_fixture(self) -> dict:
        kernel, run = self._complete_single_section_production()
        provider = PrecompileRepairPromotionProvider(PROJECT_ROOT)
        state = read_json(run / "workflow/production-state.json")
        task_order = provider._required_replay_task_order(state)
        initial_claims = {
            logical_key: {
                "task_id": state["claims"][logical_key]["task_id"],
                "claim_generation": state["claims"][logical_key][
                    "claim_generation"
                ],
            }
            for logical_key in task_order
        }

        bundle_root = run / "待删除/non-runtime-repair-bundle"
        input_snapshot = []
        for logical_key in task_order:
            envelope_path = (
                run
                / "workflow/tasks"
                / state["claims"][logical_key]["task_id"]
                / "envelope.json"
            )
            input_snapshot.append(
                {
                    "path": envelope_path.relative_to(run).as_posix(),
                    "sha256": hashlib.sha256(envelope_path.read_bytes()).hexdigest(),
                }
            )

        payload_sources = {
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
        derived_payload = []
        for relative, logical_id in payload_sources.items():
            source = run / state["artifacts"][logical_id]["path"]
            target = bundle_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            derived_payload.append(
                {
                    "path": target.relative_to(run).as_posix(),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
        policy_source = run / "workflow/compile-runtime-policy.json"
        policy_target = bundle_root / "payload/compile-runtime-policy.json"
        policy_target.parent.mkdir(parents=True, exist_ok=True)
        policy_target.write_bytes(policy_source.read_bytes())
        derived_payload.append(
            {
                "path": policy_target.relative_to(run).as_posix(),
                "sha256": hashlib.sha256(policy_target.read_bytes()).hexdigest(),
            }
        )
        bundle_path = write_json(
            bundle_root / "bundle.json",
            {
                "schema_name": "production-repair-replay-bundle",
                "schema_version": "1.0.0",
                "run_id": state["run_id"],
                "input_snapshot": input_snapshot,
                "derived_payload": derived_payload,
                "initial_claims": initial_claims,
                "task_order": task_order,
            },
        )

        predecessor = run / "review/precompile/workspaces/failed-current"
        predecessor_generations = valid_generation_set()
        predecessor_inventory = valid_inventory()
        predecessor_dependencies = valid_semantic_dependencies()
        write_json(predecessor / "artifact-generations.json", predecessor_generations)
        write_json(
            predecessor / "reader-facing-text-inventory.json", predecessor_inventory
        )
        write_json(
            predecessor / "semantic-dependencies.json", predecessor_dependencies
        )
        failure = {
            "schema_name": "precompile-quality-report",
            "schema_version": "1.0.0",
            "overall_decision": "fail",
            "generation_set_sha256": predecessor_generations[
                "generation_set_sha256"
            ],
            "inventory_sha256": predecessor_inventory["inventory_sha256"],
            "semantic_dependencies_sha256": predecessor_dependencies[
                "dependencies_sha256"
            ],
            "failure_set": [
                {
                    "owner": "writing-quality-reviewer",
                    "result_key": "reader_wording:section_01",
                    "repair_write_set": ["work/writers/section_01.tex"],
                }
            ],
            "contract_gaps": [],
            "semantic_attempt_budget_consumed": True,
            "repair_routing": {
                "parallel_repair_tasks": [
                    {
                        "failure_keys": [
                            "writing-quality-reviewer:reader_wording:section_01"
                        ]
                    }
                ],
                "integration_repair_tasks": [],
            },
        }
        failure["report_sha256"] = fingerprint(failure, "report_sha256")
        failure_path = write_json(
            predecessor / "precompile-quality-report.json", failure
        )

        successor_generations = json.loads(json.dumps(predecessor_generations))
        successor_main = next(
            item
            for item in successor_generations["artifacts"]
            if item["logical_id"] == "integrated_main_tex"
        )
        successor_main["generation"] += 1
        successor_main["sha256"] = "c" * 64
        successor_generations["generation_set_id"] = "integrated-draft-8"
        successor_generations["generation_set_sha256"] = fingerprint(
            successor_generations, "generation_set_sha256"
        )
        successor_inventory = json.loads(json.dumps(predecessor_inventory))
        successor_inventory["inventory_id"] = "inventory-8"
        successor_inventory["generation_set_sha256"] = successor_generations[
            "generation_set_sha256"
        ]
        for item in successor_inventory["items"]:
            if item["source_artifact_logical_id"] == "integrated_main_tex":
                item["source_generation"] = successor_main["generation"]
                item["source_sha256"] = successor_main["sha256"]
            item["item_sha256"] = fingerprint(item, "item_sha256")
        successor_inventory["reader_text_set_sha256"] = hashlib.sha256(
            canonical_json_bytes(
                [
                    {
                        "item_id": item["item_id"],
                        "kind": item["kind"],
                        "representation": item["representation"],
                        "text_sha256": item["text_sha256"],
                    }
                    for item in successor_inventory["items"]
                ]
            )
        ).hexdigest()
        successor_inventory["inventory_sha256"] = fingerprint(
            successor_inventory, "inventory_sha256"
        )
        candidate_root = run / "待删除/non-runtime-repair-inputs"
        generation_path = write_json(
            candidate_root / "artifact-generations.json", successor_generations
        )
        inventory_path = write_json(
            candidate_root / "reader-facing-text-inventory.json", successor_inventory
        )
        dependencies_path = write_json(
            candidate_root / "semantic-dependencies.json", predecessor_dependencies
        )
        workspace = run / "review/precompile/workspaces/repaired-current"
        prepared = PrecompileQualityProvider(PROJECT_ROOT).prepare_repair(
            predecessor_workspace_root=predecessor,
            workspace_root=workspace,
            inventory_path=inventory_path,
            artifact_generations_path=generation_path,
            semantic_dependencies_path=dependencies_path,
            repair_attempt_number=1,
            prepared_at="2026-09-06T00:00:00Z",
            repair_bundle_path=bundle_path,
            repair_sequence=1,
            kernel_production_run_dir=run,
            promotion_input_bindings={
                "predecessor_workspace_root": str(predecessor.resolve()),
                "inventory": {
                    "path": str(inventory_path.resolve()),
                    "sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
                },
                "semantic_dependencies": {
                    "path": str(dependencies_path.resolve()),
                    "sha256": hashlib.sha256(
                        dependencies_path.read_bytes()
                    ).hexdigest(),
                },
            },
        )
        return {
            "kernel": kernel,
            "run": run,
            "provider": provider,
            "bundle_path": bundle_path,
            "failure_path": failure_path,
            "predecessor": predecessor,
            "workspace": workspace,
            "inventory_path": inventory_path,
            "dependencies_path": dependencies_path,
            "prepared": prepared,
            "task_order": task_order,
        }

    def test_non_runtime_exact_replay_reuses_the_bound_workspace_read_only(self) -> None:
        case = self._non_runtime_bound_repair_fixture()
        run_before = {
            path.relative_to(case["run"]): path.read_bytes()
            for path in case["run"].rglob("*")
            if path.is_file()
        }
        result = case["provider"].promote(
            run_dir=case["run"],
            repair_bundle_path=case["bundle_path"],
            predecessor_workspace_root=case["predecessor"],
            workspace_root=case["workspace"],
            inventory_path=case["inventory_path"],
            semantic_dependencies_path=case["dependencies_path"],
            repair_attempt_number=1,
            prepared_at="2026-09-06T00:00:00Z",
            repair_failure_authority_path=case["failure_path"],
        )
        self.assertEqual("precompile_repair_already_promoted", result["classification"])
        self.assertEqual(
            run_before,
            {
                path.relative_to(case["run"]): path.read_bytes()
                for path in case["run"].rglob("*")
                if path.is_file()
            },
        )

    def test_non_runtime_repair_attempt_rejects_a_competing_workspace_before_writes(self) -> None:
        # scenario_id: issue106_non_runtime_competing_workspace
        # target_invariant: one repair attempt owns exactly one successor workspace
        # mutation_seam: substitute a fresh successor path for the bound workspace
        # rematerialized_nodes: none
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_workspace_binding
        # expected_error_code: precompile_repair_competing_workspace
        # scenario_class: single_contradiction
        case = self._non_runtime_bound_repair_fixture()
        competing = case["run"] / "review/precompile/workspaces/competing-non-runtime"
        with self.assertRaises(ContractError) as raised:
            case["provider"].promote(
                run_dir=case["run"],
                repair_bundle_path=case["bundle_path"],
                predecessor_workspace_root=case["predecessor"],
                workspace_root=competing,
                inventory_path=case["inventory_path"],
                semantic_dependencies_path=case["dependencies_path"],
                repair_attempt_number=1,
                prepared_at="2026-09-06T00:00:00Z",
                repair_failure_authority_path=case["failure_path"],
            )
        self.assertEqual(
            "precompile_repair_workspace_binding",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "precompile_repair_competing_workspace",
            raised.exception.data["error_code"],
        )
        self.assertFalse(competing.exists())

    def test_non_runtime_replay_rejects_stale_current_production_authority(self) -> None:
        # scenario_id: issue106_replay_stale_production_state
        # target_invariant: replay requires the current complete Production graph
        # mutation_seam: current draft_compile_ready checkpoint
        # rematerialized_nodes: Production State bytes only
        # intentionally_stale_nodes: diagnostic authority binding
        # expected_first_gate: precompile_repair_replay_production
        # expected_error_code: precompile_repair_replay_production_authority_stale
        # scenario_class: single_contradiction
        #
        # scenario_id: issue106_replay_stale_diagnostic_report
        # target_invariant: replay requires current diagnostic Compile authority
        # mutation_seam: current diagnostic report bytes
        # rematerialized_nodes: none
        # intentionally_stale_nodes: Production artifact binding
        # expected_first_gate: precompile_repair_replay_production
        # expected_error_code: precompile_repair_replay_production_authority_stale
        # scenario_class: single_contradiction
        for scenario in ("production_state", "diagnostic_report"):
            with self.subTest(scenario=scenario):
                case = self._non_runtime_bound_repair_fixture()
                if scenario == "production_state":
                    state_path = case["run"] / "workflow/production-state.json"
                    state = read_json(state_path)
                    state["checkpoints"]["draft_compile_ready"] = "pending"
                    write_json(state_path, state)
                else:
                    report_path = (
                        case["run"] / "review/latex/diagnostic-compile-report.json"
                    )
                    report_path.write_bytes(report_path.read_bytes() + b"\n")
                with self.assertRaises(ContractError) as raised:
                    case["provider"].promote(
                        run_dir=case["run"],
                        repair_bundle_path=case["bundle_path"],
                        predecessor_workspace_root=case["predecessor"],
                        workspace_root=case["workspace"],
                        inventory_path=case["inventory_path"],
                        semantic_dependencies_path=case["dependencies_path"],
                        repair_attempt_number=1,
                        prepared_at="2026-09-06T00:00:00Z",
                        repair_failure_authority_path=case["failure_path"],
                    )
                self.assertEqual(
                    "precompile_repair_replay_production",
                    raised.exception.data["first_failing_gate"],
                )
                self.assertEqual(
                    "precompile_repair_replay_production_authority_stale",
                    raised.exception.data["error_code"],
                )

    def test_non_runtime_replay_rejects_changed_cli_input_identities(self) -> None:
        # scenario_id: issue106_replay_changed_predecessor_identity
        # target_invariant: replay uses the predecessor workspace bound at preparation
        # mutation_seam: predecessor_workspace_root CLI argument
        # rematerialized_nodes: byte-identical alternate predecessor workspace
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_replay_inputs
        # expected_error_code: precompile_repair_replay_input_identity_changed
        # scenario_class: single_contradiction
        #
        # scenario_id: issue106_replay_changed_inventory_identity
        # target_invariant: replay uses the inventory path bound at preparation
        # mutation_seam: inventory CLI argument
        # rematerialized_nodes: byte-identical alternate inventory file
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_replay_inputs
        # expected_error_code: precompile_repair_replay_input_identity_changed
        # scenario_class: single_contradiction
        #
        # scenario_id: issue106_replay_changed_dependencies_identity
        # target_invariant: replay uses the dependency path bound at preparation
        # mutation_seam: semantic_dependencies CLI argument
        # rematerialized_nodes: byte-identical alternate dependency file
        # intentionally_stale_nodes: none
        # expected_first_gate: precompile_repair_replay_inputs
        # expected_error_code: precompile_repair_replay_input_identity_changed
        # scenario_class: single_contradiction
        for scenario in ("predecessor", "inventory", "dependencies"):
            with self.subTest(scenario=scenario):
                case = self._non_runtime_bound_repair_fixture()
                arguments = {
                    "predecessor_workspace_root": case["predecessor"],
                    "inventory_path": case["inventory_path"],
                    "semantic_dependencies_path": case["dependencies_path"],
                }
                alternate_root = case["run"] / "待删除/replay-identity-alternate"
                if scenario == "predecessor":
                    alternate = alternate_root / "predecessor"
                    for source in case["predecessor"].rglob("*"):
                        if source.is_file():
                            target = alternate / source.relative_to(case["predecessor"])
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(source.read_bytes())
                    arguments["predecessor_workspace_root"] = alternate
                elif scenario == "inventory":
                    alternate = alternate_root / "reader-facing-text-inventory.json"
                    alternate.parent.mkdir(parents=True, exist_ok=True)
                    alternate.write_bytes(case["inventory_path"].read_bytes())
                    arguments["inventory_path"] = alternate
                else:
                    alternate = alternate_root / "semantic-dependencies.json"
                    alternate.parent.mkdir(parents=True, exist_ok=True)
                    alternate.write_bytes(case["dependencies_path"].read_bytes())
                    arguments["semantic_dependencies_path"] = alternate
                with self.assertRaises(ContractError) as raised:
                    case["provider"].promote(
                        run_dir=case["run"],
                        repair_bundle_path=case["bundle_path"],
                        workspace_root=case["workspace"],
                        repair_attempt_number=1,
                        prepared_at="2026-09-06T00:00:00Z",
                        repair_failure_authority_path=case["failure_path"],
                        **arguments,
                    )
                self.assertEqual(
                    "precompile_repair_replay_inputs",
                    raised.exception.data["first_failing_gate"],
                )
                self.assertEqual(
                    "precompile_repair_replay_input_identity_changed",
                    raised.exception.data["error_code"],
                )

    def test_public_promotion_admits_the_next_semantic_failure_continuation(self) -> None:
        case = self._runtime_continuation_fixture()
        old_workspace = Path(case["old_promotion"]["workspace_root"])
        brief_path = case["brief_path"]
        report = {
            "schema_name": "precompile-quality-report",
            "schema_version": "1.0.0",
            "overall_decision": "fail",
            "generation_set_sha256": read_json(case["generation_path"])[
                "generation_set_sha256"
            ],
            "inventory_sha256": read_json(
                old_workspace / "reader-facing-text-inventory.json"
            )["inventory_sha256"],
            "semantic_dependencies_sha256": read_json(
                old_workspace / "semantic-dependencies.json"
            )["dependencies_sha256"],
            "failure_set": [
                {
                    "owner": "writing-quality-reviewer",
                    "result_key": "reader_wording:section_02",
                    "repair_write_set": [
                        "work/writers/section_02.result.json",
                        "work/writers/section_02.tex",
                    ],
                }
            ],
            "repair_routing": {
                "parallel_repair_tasks": [
                    {
                        "failure_keys": [
                            "writing-quality-reviewer:reader_wording:section_02"
                        ]
                    }
                ],
                "integration_repair_tasks": [],
            },
            "contract_gaps": [],
            "semantic_attempt_budget_consumed": True,
        }
        report["report_sha256"] = fingerprint(report, "report_sha256")
        report_path = write_json(
            old_workspace / "precompile-quality-report.json", report
        )
        state_path = case["run"] / "workflow/production-state.json"
        state = read_json(state_path)
        task_id = "2" * 32
        state["claims"] = {
            "writer-section_02": {
                "task_id": task_id,
                "claim_generation": 1,
                "status": "committed",
            }
        }
        writer_path = case["run"] / "work/writers/section_02.tex"
        writer_result_path = case["run"] / "work/writers/section_02.result.json"
        writer_path.parent.mkdir(parents=True, exist_ok=True)
        writer_path.write_text("old section", encoding="utf-8")
        write_json(writer_result_path, {"new_figure_candidates": []})
        state["artifacts"] = {
            "writer_section_02": {
                "path": "work/writers/section_02.tex",
                "sha256": hashlib.sha256(writer_path.read_bytes()).hexdigest(),
                "generation": 1,
            },
            "writer_result_section_02": {
                "path": "work/writers/section_02.result.json",
                "sha256": hashlib.sha256(writer_result_path.read_bytes()).hexdigest(),
                "generation": 1,
            },
        }
        write_json(state_path, state)
        write_json(
            case["run"] / "workflow/tasks" / task_id / "envelope.json",
            {
                "role": "writer",
                "section_id": "section_02",
                "logical_task_key": "writer-section_02",
            },
        )
        payload = case["bundle_path"].parent / "payload/writers/section_02.tex"
        payload_result = (
            case["bundle_path"].parent / "payload/writers/section_02.result.json"
        )
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_text("new section", encoding="utf-8")
        write_json(
            payload_result,
            {"new_figure_candidates": [{"slot_id": "figure_candidate"}]},
        )
        bundle = read_json(case["bundle_path"])
        bundle["task_order"] = ["writer-section_02"]
        bundle["initial_claims"] = {
            "writer-section_02": {
                "task_id": task_id,
                "claim_generation": 1,
            }
        }
        bundle["derived_payload"] = [
            {
                "path": payload.relative_to(case["run"]).as_posix(),
                "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            },
            {
                "path": payload_result.relative_to(case["run"]).as_posix(),
                "sha256": hashlib.sha256(payload_result.read_bytes()).hexdigest(),
            },
        ]
        write_json(case["bundle_path"], bundle)
        journal = read_json(case["active_path"])
        journal["content_repair_handoff"]["repair_bundle_sha256"] = hashlib.sha256(
            case["bundle_path"].read_bytes()
        ).hexdigest()
        journal["content_repair_handoff"]["handoff_sha256"] = fingerprint(
            journal["content_repair_handoff"], "handoff_sha256"
        )
        journal["journal_sha256"] = fingerprint(journal, "journal_sha256")
        write_json(case["active_path"], journal)
        provider = PrecompileRepairPromotionProvider(PROJECT_ROOT)
        provider.contracts.validate = lambda *_args: None
        provider.delivery_quality.validate = lambda *_args: None
        with patch.object(
            provider,
            "_required_replay_task_order",
            return_value=["writer-section_02"],
        ), patch.object(
            provider, "_preflight_claim_plan", return_value=None
        ), patch.object(
            provider,
            "_resume_production_repair",
            side_effect=RuntimeError("production replay reached"),
        ):
            with self.assertRaisesRegex(RuntimeError, "production replay reached"):
                provider.promote(
                    run_dir=case["run"],
                    repair_bundle_path=case["bundle_path"],
                    predecessor_workspace_root=old_workspace,
                    workspace_root=case["new_workspace"],
                    inventory_path=old_workspace
                    / "reader-facing-text-inventory.json",
                    semantic_dependencies_path=old_workspace
                    / "semantic-dependencies.json",
                    repair_attempt_number=2,
                    prepared_at="2026-09-06T01:00:00Z",
                    runtime_refresh_operation_id=case["operation_id"],
                    runtime_predecessor_final_compile_manifest_path=(
                        case["run"] / "workflow/compile-manifest.json"
                    ),
                    repair_failure_authority_path=report_path,
                )
        self.assertFalse(case["new_workspace"].exists())

    def test_public_promotion_admits_dispositioned_gap_without_runtime_attachment(self) -> None:
        case = self._runtime_continuation_fixture()
        retained_runtime = case["run"] / "待删除/retained-runtime-refresh-active.json"
        retained_runtime.parent.mkdir(parents=True, exist_ok=True)
        case["active_path"].replace(retained_runtime)
        state_path = case["run"] / "workflow/production-state.json"
        state = read_json(state_path)
        task_id = "3" * 32
        state["claims"] = {
            "writer-section_02": {
                "task_id": task_id,
                "claim_generation": 1,
                "status": "committed",
            }
        }
        writer_path = case["run"] / "work/writers/section_02.tex"
        writer_result_path = case["run"] / "work/writers/section_02.result.json"
        writer_path.parent.mkdir(parents=True, exist_ok=True)
        writer_path.write_text("old section", encoding="utf-8")
        write_json(writer_result_path, {"new_figure_candidates": []})
        state["artifacts"] = {
            "writer_section_02": {
                "path": "work/writers/section_02.tex",
                "sha256": hashlib.sha256(writer_path.read_bytes()).hexdigest(),
                "generation": 1,
            },
            "writer_result_section_02": {
                "path": "work/writers/section_02.result.json",
                "sha256": hashlib.sha256(writer_result_path.read_bytes()).hexdigest(),
                "generation": 1,
            },
        }
        write_json(state_path, state)
        write_json(
            case["run"] / "workflow/tasks" / task_id / "envelope.json",
            {
                "role": "writer",
                "section_id": "section_02",
                "logical_task_key": "writer-section_02",
            },
        )
        payload = case["bundle_path"].parent / "payload/writers/section_02.tex"
        payload_result = (
            case["bundle_path"].parent / "payload/writers/section_02.result.json"
        )
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_text("new section", encoding="utf-8")
        write_json(
            payload_result,
            {"new_figure_candidates": [{"slot_id": "figure_candidate"}]},
        )
        bundle = read_json(case["bundle_path"])
        bundle["task_order"] = ["writer-section_02"]
        bundle["initial_claims"] = {
            "writer-section_02": {
                "task_id": task_id,
                "claim_generation": 1,
            }
        }
        bundle["derived_payload"] = [
            {
                "path": payload.relative_to(case["run"]).as_posix(),
                "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            },
            {
                "path": payload_result.relative_to(case["run"]).as_posix(),
                "sha256": hashlib.sha256(payload_result.read_bytes()).hexdigest(),
            },
        ]
        write_json(case["bundle_path"], bundle)
        disposition = read_json(case["disposition_path"])
        disposition["repair_bundle_sha256"] = hashlib.sha256(
            case["bundle_path"].read_bytes()
        ).hexdigest()
        disposition["allowed_write_set"] = [
            "work/writers/section_02.result.json",
            "work/writers/section_02.tex",
        ]
        disposition["disposition_sha256"] = fingerprint(
            disposition, "disposition_sha256"
        )
        write_json(case["disposition_path"], disposition)
        provider = PrecompileRepairPromotionProvider(PROJECT_ROOT)
        provider.contracts.validate = lambda *_args: None
        provider.delivery_quality.validate = lambda *_args: None
        retained_evidence = {
            "failure_set": [
                {
                    "owner": "writing-quality-reviewer",
                    "result_key": "first_use:section_02",
                }
            ],
            "repair_routing": {},
        }
        with patch.object(
            PrecompileQualityProvider,
            "retained_contract_gap_evidence",
            return_value=retained_evidence,
        ), patch.object(
            provider,
            "_required_replay_task_order",
            return_value=["writer-section_02"],
        ), patch.object(
            provider, "_preflight_claim_plan", return_value=None
        ), patch.object(
            provider,
            "_resume_production_repair",
            side_effect=RuntimeError("production replay reached"),
        ):
            with self.assertRaisesRegex(RuntimeError, "production replay reached"):
                provider.promote(
                    run_dir=case["run"],
                    repair_bundle_path=case["bundle_path"],
                    predecessor_workspace_root=Path(
                        case["old_promotion"]["workspace_root"]
                    ),
                    workspace_root=case["new_workspace"],
                    inventory_path=Path(case["old_promotion"]["inventory_path"]),
                    semantic_dependencies_path=Path(
                        case["old_promotion"]["semantic_dependencies_path"]
                    ),
                    repair_attempt_number=1,
                    prepared_at="2026-09-06T01:00:00Z",
                    repair_failure_authority_path=case["brief_path"],
                    runtime_content_repair_disposition_path=case[
                        "disposition_path"
                    ],
                )
        self.assertFalse(case["new_workspace"].exists())


if __name__ == "__main__":
    unittest.main()

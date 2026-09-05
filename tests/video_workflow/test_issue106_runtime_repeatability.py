from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow.test_issue106_reader_text_continuation import (
    Issue106ReaderTextContinuationTests,
    fingerprint,
    write_json,
)
from video2pdf_workflow_kernel.errors import ContractError
from video2pdf_workflow_kernel.precompile_quality import (
    PRECOMPILE_OWNERS,
    PRECOMPILE_PROVIDER_ID,
    PRECOMPILE_PROVIDER_VERSION,
    PrecompileQualityProvider,
)
from video2pdf_workflow_kernel.runtime_refresh import CompileRuntimeRefreshProvider
from video2pdf_workflow_kernel.utils import canonical_json_bytes, read_json


class Issue106RuntimeRepeatabilityTests(unittest.TestCase):
    def _materialize_failure(self, workspace: Path, *, output_root: Path) -> Path:
        quality = PrecompileQualityProvider(PROJECT_ROOT)
        failed = False
        for owner in PRECOMPILE_OWNERS:
            skeleton_path = (
                workspace / "reviewers" / owner / "input/review-skeleton.json"
            )
            skeleton = read_json(skeleton_path)
            results = []
            for required in skeleton["required_results"]:
                should_fail = (
                    owner == "writing-quality-reviewer"
                    and required.get("item_id") == "integrated_section_01.body"
                    and not failed
                )
                result = {
                    "result_key": required["result_key"],
                    "decision": "fail" if should_fail else "pass",
                    "evidence_locator": (
                        "artifact:work/integration/section_01.tex"
                        if should_fail
                        else f"artifact:{required['result_key']}"
                    ),
                    "repair_write_set": (
                        ["work/writers/section_01.tex"] if should_fail else []
                    ),
                }
                if should_fail:
                    result["violation_id"] = "reader_wording_requires_second_repair"
                    failed = True
                results.append(result)
            patch = {
                "schema_name": "precompile-judgment-patch",
                "schema_version": "1.0.0",
                "task_id": skeleton["task_id"],
                "owner": owner,
                "skeleton_sha256": skeleton["skeleton_sha256"],
                "generation_set_sha256": skeleton["generation_set_sha256"],
                "reviewer": {
                    "reviewer_id": f"repeatability-{skeleton['task_id'][:12]}",
                    "runtime_sha256": hashlib.sha256(
                        skeleton_path.read_bytes()
                    ).hexdigest(),
                    "independent_from_generation_producers": True,
                },
                "results": results,
                "contract_gaps": [],
            }
            patch["patch_sha256"] = fingerprint(patch, "patch_sha256")
            patch_path = write_json(output_root / f"{owner}.patch.json", patch)
            quality.commit_patch(
                workspace_root=workspace,
                owner=owner,
                patch_path=patch_path,
                committed_at="2026-09-06T01:10:00Z",
            )
        self.assertTrue(failed)
        materialized = quality.materialize(
            workspace_root=workspace,
            provider_id=PRECOMPILE_PROVIDER_ID,
            provider_version=PRECOMPILE_PROVIDER_VERSION,
            materialized_at="2026-09-06T01:11:00Z",
        )
        self.assertEqual("fail", materialized["overall_decision"])
        return Path(materialized["report_path"])

    def _next_bundle(
        self,
        *,
        run: Path,
        task_order: list[str],
        lifecycle: Issue106ReaderTextContinuationTests,
    ) -> Path:
        _kernel, candidate_run = lifecycle._complete_single_section_production(
            writer_text=b"A second changed reader wording closes the reviewed failure."
        )
        state = read_json(run / "workflow/production-state.json")
        candidate_state = read_json(candidate_run / "workflow/production-state.json")
        bundle_root = run / "待删除/runtime-repeatability-next-bundle"
        input_snapshot = []
        for logical_key in task_order:
            envelope = (
                run
                / "workflow/tasks"
                / state["claims"][logical_key]["task_id"]
                / "envelope.json"
            )
            target = bundle_root / "input/envelopes" / f"{logical_key}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(envelope.read_bytes())
            input_snapshot.append(
                {
                    "path": target.relative_to(run).as_posix(),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
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
            source = candidate_run / candidate_state["artifacts"][logical_id]["path"]
            payload_bytes = source.read_bytes()
            if logical_id.startswith("pyramid_"):
                value = json.loads(payload_bytes)
                logical_key = next(
                    key
                    for key in task_order
                    if key == "pyramid-outline"
                    and logical_id == "pyramid_outline_report"
                    or key == "pyramid-main"
                    and logical_id == "pyramid_main_report"
                    or key.startswith("pyramid-section-")
                    and logical_id == "pyramid_section_01_report"
                )
                envelope = read_json(
                    run
                    / "workflow/tasks"
                    / state["claims"][logical_key]["task_id"]
                    / "envelope.json"
                )
                value["evaluation_context"] = envelope["evaluation_context"]
                payload_bytes = canonical_json_bytes(value)
            target = bundle_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload_bytes)
            derived_payload.append(
                {
                    "path": target.relative_to(run).as_posix(),
                    "sha256": hashlib.sha256(payload_bytes).hexdigest(),
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
        initial_claims = {
            key: {
                "task_id": state["claims"][key]["task_id"],
                "claim_generation": state["claims"][key]["claim_generation"],
            }
            for key in task_order
        }
        return write_json(
            bundle_root / "bundle.json",
            {
                "schema_name": "production-repair-replay-bundle",
                "schema_version": "1.0.0",
                "run_id": state["run_id"],
                "input_snapshot": input_snapshot,
                "derived_payload": derived_payload,
                "restoration": [],
                "initial_claims": initial_claims,
                "task_order": task_order,
            },
        )

    def _genuine_repeatability_fixture(self, *, legacy_refresh: bool) -> dict:
        lifecycle = Issue106ReaderTextContinuationTests(
            "test_non_runtime_exact_replay_reuses_the_bound_workspace_read_only"
        )
        base = lifecycle._non_runtime_bound_repair_fixture()
        run = base["run"]
        runtime = CompileRuntimeRefreshProvider(PROJECT_ROOT)
        policy_sha256 = hashlib.sha256(
            (run / "workflow/compile-runtime-policy.json").read_bytes()
        ).hexdigest()
        predecessor = base["predecessor"]
        predecessor_generations = predecessor / "artifact-generations.json"
        predecessor_inventory = predecessor / "reader-facing-text-inventory.json"
        predecessor_dependencies = predecessor / "semantic-dependencies.json"
        generations = read_json(predecessor_generations)
        initial_promotion = {
            "workspace_root": str(predecessor.resolve()),
            "generation_set_path": str(predecessor_generations.resolve()),
            "generation_set_sha256": generations["generation_set_sha256"],
            "generation_set_file_sha256": hashlib.sha256(
                predecessor_generations.read_bytes()
            ).hexdigest(),
            "inventory_path": str(predecessor_inventory.resolve()),
            "semantic_dependencies_path": str(predecessor_dependencies.resolve()),
        }
        operation_id = "issue106-runtime-repeatability"
        handoff = {
            "schema_name": "runtime-refresh-content-repair-handoff",
            "schema_version": "1.0.0",
            "state": "promotion_ready",
            "runtime_refresh_operation_id": operation_id,
            "runtime_policy_sha256": policy_sha256,
            "repair_bundle_path": str(base["bundle_path"].resolve()),
            "repair_bundle_sha256": hashlib.sha256(
                base["bundle_path"].read_bytes()
            ).hexdigest(),
            "promotion": initial_promotion,
        }
        handoff["handoff_sha256"] = fingerprint(handoff, "handoff_sha256")
        journal = {
            "schema_name": "compile-runtime-refresh-journal",
            "schema_version": "1.0.0",
            "state": "precompile_refresh_required",
            "operation_id": operation_id,
            "canonical_runtime_policy_sha256": policy_sha256,
            "content_repair_handoff": handoff,
        }
        journal["journal_sha256"] = fingerprint(journal, "journal_sha256")
        active_path = write_json(
            run / "workflow/runtime-refresh-active.json", journal
        )
        prior_write_set = ["work/writers/section_01.tex"]
        prior_authorization = runtime.preflight_repair_continuation(
            run_dir=run,
            expected_operation_id=operation_id,
            repair_bundle_path=base["bundle_path"],
            failure_authority_path=base["failure_path"],
            successor_workspace_root=base["workspace"],
            actual_write_set=prior_write_set,
        )
        promoted = base["promoted"]
        first_handoff = runtime.bind_content_repair_promotion(
            run_dir=run,
            expected_operation_id=operation_id,
            workspace_root=base["workspace"],
            generation_set_path=Path(promoted["successor_generation_set_path"]),
            inventory_path=Path(promoted["successor_inventory_path"]),
            semantic_dependencies_path=Path(
                promoted["successor_semantic_dependencies_path"]
            ),
            failure_authority_path=base["failure_path"],
            repair_bundle_path=base["bundle_path"],
            actual_write_set=prior_write_set,
            preflight_authorization=prior_authorization,
        )
        prior_refresh = deepcopy(first_handoff["promotion_refresh"])
        if legacy_refresh:
            legacy = deepcopy(prior_refresh)
            legacy["predecessor_contract_gap_brief_path"] = legacy.pop(
                "failure_authority_path"
            )
            legacy["predecessor_contract_gap_brief_sha256"] = legacy.pop(
                "failure_authority_sha256"
            )
            legacy.pop("authorization_sha256", None)
            legacy["disposition_sha256"] = prior_refresh["authorization_sha256"]
            first_handoff["promotion_refresh"] = legacy
            first_handoff["handoff_sha256"] = fingerprint(
                first_handoff, "handoff_sha256"
            )
            journal = read_json(active_path)
            journal["content_repair_handoff"] = first_handoff
            journal["journal_sha256"] = fingerprint(journal, "journal_sha256")
            write_json(active_path, journal)
            prior_refresh = legacy
        failure_path = self._materialize_failure(
            base["workspace"], output_root=run / "待删除/repeatability-review"
        )
        next_bundle = self._next_bundle(
            run=run, task_order=base["task_order"], lifecycle=lifecycle
        )
        successor = run / "review/precompile/workspaces/repaired-next"
        result = base["provider"].promote(
            run_dir=run,
            repair_bundle_path=next_bundle,
            predecessor_workspace_root=base["workspace"],
            workspace_root=successor,
            inventory_path=base["workspace"] / "reader-facing-text-inventory.json",
            semantic_dependencies_path=(
                base["workspace"] / "semantic-dependencies.json"
            ),
            repair_attempt_number=2,
            prepared_at="2026-09-06T01:12:00Z",
            repair_failure_authority_path=failure_path,
            runtime_refresh_operation_id=operation_id,
            runtime_predecessor_final_compile_manifest_path=(
                run / "workflow/compile-manifest.json"
            ),
        )
        self.assertEqual("precompile_repair_promoted", result["classification"])
        current_manifest = read_json(run / "workflow/compile-manifest.json")
        successor_generations = read_json(Path(result["successor_generation_set_path"]))
        self.assertEqual(
            sorted(
                (entry["logical_id"], entry["generation"], entry["sha256"])
                for entry in current_manifest["entries"]
            ),
            sorted(
                (item["logical_id"], item["generation"], item["sha256"])
                for item in successor_generations["artifacts"]
            ),
        )
        return {
            **base,
            "runtime": runtime,
            "active_path": active_path,
            "operation_id": operation_id,
            "prior_refresh": prior_refresh,
            "current_promotion": first_handoff["promotion"],
            "failure_path": failure_path,
            "next_bundle": next_bundle,
            "successor": successor,
            "result": result,
        }

    def test_modern_retained_refresh_allows_the_current_failure_to_start_next_cycle(
        self,
    ) -> None:
        case = self._genuine_repeatability_fixture(legacy_refresh=False)
        active_bytes = case["active_path"].read_bytes()
        replay = case["runtime"].bind_content_repair_promotion(
            run_dir=case["run"],
            expected_operation_id=case["operation_id"],
            workspace_root=case["successor"],
            generation_set_path=Path(
                case["result"]["successor_generation_set_path"]
            ),
            inventory_path=Path(case["result"]["successor_inventory_path"]),
            semantic_dependencies_path=Path(
                case["result"]["successor_semantic_dependencies_path"]
            ),
            failure_authority_path=case["failure_path"],
            repair_bundle_path=case["next_bundle"],
        )
        self.assertEqual(
            read_json(case["active_path"])["content_repair_handoff"], replay
        )
        self.assertEqual(active_bytes, case["active_path"].read_bytes())
        handoff = replay
        self.assertEqual(case["current_promotion"], handoff["retained_prior_promotions"][-1])
        self.assertEqual(case["prior_refresh"], handoff["retained_promotion_refreshes"][-1])

        promoted_replay = case["provider"].promote(
            run_dir=case["run"],
            repair_bundle_path=case["next_bundle"],
            predecessor_workspace_root=case["workspace"],
            workspace_root=case["successor"],
            inventory_path=case["workspace"] / "reader-facing-text-inventory.json",
            semantic_dependencies_path=(
                case["workspace"] / "semantic-dependencies.json"
            ),
            repair_attempt_number=2,
            prepared_at="2026-09-06T01:12:00Z",
            repair_failure_authority_path=case["failure_path"],
            runtime_refresh_operation_id=case["operation_id"],
            runtime_predecessor_final_compile_manifest_path=(
                case["run"] / "workflow/compile-manifest.json"
            ),
        )
        self.assertEqual(
            "precompile_repair_already_promoted",
            promoted_replay["classification"],
        )
        self.assertEqual(active_bytes, case["active_path"].read_bytes())

        competing = case["run"] / "review/precompile/workspaces/competing-replay"
        with self.assertRaises(ContractError) as raised:
            case["runtime"].preflight_repair_continuation(
                run_dir=case["run"],
                expected_operation_id=case["operation_id"],
                repair_bundle_path=case["next_bundle"],
                failure_authority_path=case["failure_path"],
                successor_workspace_root=competing,
                actual_write_set=["work/writers/section_01.tex"],
            )
        self.assertEqual(
            "runtime_refresh_continuation_competing_successor",
            raised.exception.data["error_code"],
        )
        self.assertFalse(competing.exists())
        self.assertEqual(active_bytes, case["active_path"].read_bytes())

    def test_legacy_retained_refresh_allows_the_current_failure_to_start_next_cycle(
        self,
    ) -> None:
        case = self._genuine_repeatability_fixture(legacy_refresh=True)
        handoff = read_json(case["active_path"])["content_repair_handoff"]
        self.assertEqual(case["current_promotion"], handoff["retained_prior_promotions"][-1])
        self.assertEqual(case["prior_refresh"], handoff["retained_promotion_refreshes"][-1])
        self.assertEqual(case["successor"].resolve(), Path(handoff["promotion"]["workspace_root"]))


if __name__ == "__main__":
    unittest.main()

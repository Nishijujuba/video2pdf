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
from video2pdf_workflow_kernel.content_production import ContentProduction
from video2pdf_workflow_kernel.errors import ContractError, RuntimeRefreshFault
from video2pdf_workflow_kernel.precompile_repair_promotion import (
    PrecompileRepairPromotionProvider,
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


class _Contracts:
    @staticmethod
    def validate(_name: str, _value: dict) -> None:
        return None


class _Kernel:
    def __init__(self) -> None:
        self.contracts = _Contracts()


class Issue105ContentRepairHandoffTests(unittest.TestCase):
    def _pending_runtime_fixture(self, state: str) -> tuple[Path, Path, Path, dict]:
        root = new_case_dir(self.id(), label="issue105-runtime-handoff")
        run = root / "run"
        (run / "workflow").mkdir(parents=True)
        write_json(run / "workflow/run.json", {"run_id": "run-105"})
        write_json(run / "workflow/production-state.json", {})
        policy_path = write_json(run / "workflow/compile-runtime-policy.json", {"policy": "current"})
        policy_sha256 = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        bundle_root = run / "待删除/precompile-repair-promotion/bundle-105"
        bundled_policy_path = write_json(bundle_root / "payload/compile-runtime-policy.json", {"policy": "current"})
        bundle = {
            "schema_name": "production-repair-replay-bundle",
            "schema_version": "1.0.0",
            "derived_payload": [
                {
                    "path": str(bundled_policy_path.relative_to(run)).replace("\\", "/"),
                    "sha256": hashlib.sha256(bundled_policy_path.read_bytes()).hexdigest(),
                }
            ],
        }
        bundle_path = write_json(bundle_root / "bundle.json", bundle)
        report = write_json(run / "review/precompile/failed/precompile-quality-report.json", {"decision": "fail"})
        operation_id = "1" * 32
        journal = {
            "operation_id": operation_id,
            "state": state,
            "canonical_runtime_policy_sha256": policy_sha256,
            "successor_runtime_policy_sha256": policy_sha256,
            "predecessor_runtime_policy_sha256": "2" * 64,
            "precompile": {"prepare_inputs": {"report": str(report.resolve())}},
        }
        journal["journal_sha256"] = fingerprint(journal, "journal_sha256")
        write_json(run / "workflow/runtime-refresh-active.json", journal)
        write_json(run / "待删除/runtime-refresh" / operation_id / "journal.json", journal)
        predecessor_manifest_value = {
            "schema_name": "final-compile-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "mode": "final",
            "precompile_text_seal_sha256": "3" * 64,
            "entries": [
                {
                    "logical_id": "integrated_main",
                    "generation": 1,
                    "sha256": "4" * 64,
                    "source_path": "main.tex",
                    "staging_path": "main.tex",
                }
            ],
            "approved_runtime_inputs": [],
            "runtime_policy": {"path": "policy.json", "sha256": "2" * 64},
        }
        predecessor_manifest_value["manifest_sha256"] = fingerprint(
            predecessor_manifest_value, "manifest_sha256"
        )
        predecessor_manifest = write_json(
            run / "review/precompile/old/final-compile-manifest.json",
            predecessor_manifest_value,
        )
        return run, bundle_path, predecessor_manifest, journal

    def test_public_promotion_parser_exposes_exact_runtime_handoff_identity(self) -> None:
        from video2pdf_workflow_kernel.cli import _parser

        parser = _parser()
        parsed = parser.parse_args(
            [
                "delivery-quality-precompile-repair-promote",
                "--run-dir", "run",
                "--repair-bundle", "bundle.json",
                "--predecessor-workspace-root", "old",
                "--workspace-root", "new",
                "--inventory", "inventory.json",
                "--semantic-dependencies", "dependencies.json",
                "--repair-attempt-number", "1",
                "--prepared-at", "2026-09-05T00:00:00Z",
                "--runtime-refresh-operation-id", "operation-105",
                "--runtime-predecessor-final-compile-manifest", "manifest.json",
            ]
        )
        self.assertEqual("operation-105", parsed.runtime_refresh_operation_id)
        self.assertEqual(Path("manifest.json"), parsed.runtime_predecessor_final_compile_manifest)

    def test_handoff_rejects_wrong_runtime_state_before_repair_mutation(self) -> None:
        # scenario_id: issue105_wrong_runtime_state
        # target_invariant: handoff admission requires precompile_refresh_required
        # mutation_seam: active runtime state
        # rematerialized_nodes: journal fingerprint
        # intentionally_stale_nodes: none
        # expected_first_gate: content_repair_runtime_state
        # expected_error_code: runtime_refresh_handoff_state_invalid
        # scenario_class: single_contradiction
        run, bundle, manifest, journal = self._pending_runtime_fixture("diagnostic_published")
        with self.assertRaises(ContractError) as raised:
            CompileRuntimeRefreshProvider(PROJECT_ROOT).prepare_content_repair_handoff(
                run_dir=run,
                repair_bundle_path=bundle,
                predecessor_final_compile_manifest_path=manifest,
                expected_operation_id=journal["operation_id"],
            )
        self.assertEqual("content_repair_runtime_state", raised.exception.data["first_failing_gate"])
        self.assertEqual("runtime_refresh_handoff_state_invalid", raised.exception.data["error_code"])
        active = json.loads((run / "workflow/runtime-refresh-active.json").read_text(encoding="utf-8"))
        self.assertNotIn("content_repair_handoff", active)

    def test_handoff_rejects_bundle_policy_mismatch_before_repair_mutation(self) -> None:
        # scenario_id: issue105_bundle_policy_mismatch
        # target_invariant: repair bundle policy equals pending successor policy
        # mutation_seam: bundled policy bytes and its rematerialized bundle row
        # rematerialized_nodes: bundle policy row and bundle bytes
        # intentionally_stale_nodes: none
        # expected_first_gate: content_repair_bundle_policy
        # expected_error_code: runtime_refresh_handoff_bundle_policy_mismatch
        # scenario_class: single_contradiction
        run, bundle, manifest, journal = self._pending_runtime_fixture("precompile_refresh_required")
        bundled_policy = bundle.parent / "payload/compile-runtime-policy.json"
        write_json(bundled_policy, {"policy": "predecessor"})
        bundle_data = json.loads(bundle.read_text(encoding="utf-8"))
        bundle_data["derived_payload"][0]["sha256"] = hashlib.sha256(bundled_policy.read_bytes()).hexdigest()
        write_json(bundle, bundle_data)
        with self.assertRaises(ContractError) as raised:
            CompileRuntimeRefreshProvider(PROJECT_ROOT).prepare_content_repair_handoff(
                run_dir=run,
                repair_bundle_path=bundle,
                predecessor_final_compile_manifest_path=manifest,
                expected_operation_id=journal["operation_id"],
            )
        self.assertEqual("content_repair_bundle_policy", raised.exception.data["first_failing_gate"])
        self.assertEqual("runtime_refresh_handoff_bundle_policy_mismatch", raised.exception.data["error_code"])
        active = json.loads((run / "workflow/runtime-refresh-active.json").read_text(encoding="utf-8"))
        self.assertNotIn("content_repair_handoff", active)

    def test_handoff_persists_once_and_retains_the_runtime_operation_journal(self) -> None:
        run, bundle, manifest, journal = self._pending_runtime_fixture(
            "precompile_refresh_required"
        )
        operation_journal = run / "待删除/runtime-refresh" / journal["operation_id"] / "journal.json"
        retained = operation_journal.read_bytes()
        provider = CompileRuntimeRefreshProvider(PROJECT_ROOT)
        first = provider.prepare_content_repair_handoff(
            run_dir=run,
            repair_bundle_path=bundle,
            predecessor_final_compile_manifest_path=manifest,
            expected_operation_id=journal["operation_id"],
        )
        repeated = provider.prepare_content_repair_handoff(
            run_dir=run,
            repair_bundle_path=bundle,
            predecessor_final_compile_manifest_path=manifest,
            expected_operation_id=journal["operation_id"],
        )
        self.assertEqual(first, repeated)
        self.assertEqual("prepared", first["state"])
        self.assertEqual(retained, operation_journal.read_bytes())
        active = json.loads(
            (run / "workflow/runtime-refresh-active.json").read_text(encoding="utf-8")
        )
        self.assertEqual(first, active["content_repair_handoff"])

    def _supersession_fixture(self) -> tuple[Path, Path, dict, dict]:
        root = new_case_dir(self.id(), label="issue105-supersession")
        run = root / "run"
        workspace = run / "review/precompile/repair"
        (run / "workflow").mkdir(parents=True)
        workspace.mkdir(parents=True)
        write_json(run / "workflow/run.json", {"run_id": "run-105"})
        inventory_path = write_json(run / "runtime-inventory.json", {"files": []})
        policy = {
            "package_inventory": {
                "path": str(inventory_path.resolve()),
                "sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
            },
            "system_fonts": [],
        }
        policy["policy_sha256"] = fingerprint(policy, "policy_sha256")
        policy_path = write_json(run / "workflow/compile-runtime-policy.json", policy)
        report = {
            "runtime_policy_sha256": policy["policy_sha256"],
            "dependency_closure": {"inputs": []},
        }
        report_path = write_json(run / "review/latex/diagnostic-compile-report.json", report)
        artifacts = [
            {"logical_id": "integrated_main", "generation": 2, "sha256": "4" * 64},
            {"logical_id": "integrated_section_01", "generation": 3, "sha256": "5" * 64},
        ]
        generations = {
            "schema_name": "precompile-artifact-generation-set",
            "schema_version": "1.0.0",
            "generation_set_id": "issue105-successor",
            "producer_ids": ["writer-105"],
            "artifacts": artifacts,
        }
        generations["generation_set_sha256"] = fingerprint(
            generations, "generation_set_sha256"
        )
        generations_path = write_json(workspace / "artifact-generations.json", generations)
        compile_entries = [
            {
                **artifact,
                "source_path": f"work/{artifact['logical_id']}.tex",
                "staging_path": f"{artifact['logical_id']}.tex",
            }
            for artifact in artifacts
        ]
        write_json(run / "workflow/compile-manifest.json", {"entries": compile_entries})
        operation_id = "6" * 32
        (run / "待删除/runtime-refresh" / operation_id / "content-repair").mkdir(
            parents=True
        )
        predecessor_manifest = write_json(run / "predecessor-final-compile-manifest.json", {})
        bundle = write_json(run / "repair-bundle.json", {})
        handoff = {
            "schema_name": "runtime-refresh-content-repair-handoff",
            "schema_version": "1.0.0",
            "state": "promotion_ready",
            "runtime_refresh_operation_id": operation_id,
            "runtime_policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
            "repair_bundle_path": str(bundle.resolve()),
            "repair_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "predecessor_final_compile_manifest_path": str(predecessor_manifest.resolve()),
            "predecessor_final_compile_manifest_sha256": hashlib.sha256(predecessor_manifest.read_bytes()).hexdigest(),
            "promotion": {
                "workspace_root": str(workspace.resolve()),
                "generation_set_path": str(generations_path.resolve()),
                "generation_set_sha256": generations["generation_set_sha256"],
                "generation_set_file_sha256": hashlib.sha256(generations_path.read_bytes()).hexdigest(),
            },
        }
        handoff["handoff_sha256"] = fingerprint(handoff, "handoff_sha256")
        journal = {
            "operation_id": operation_id,
            "state": "precompile_refresh_required",
            "runtime_inventory_path": str(inventory_path.resolve()),
            "drifted_inputs": [],
            "canonical_runtime_policy_path": str(policy_path.resolve()),
            "content_repair_handoff": handoff,
        }
        journal["journal_sha256"] = fingerprint(journal, "journal_sha256")
        write_json(run / "workflow/runtime-refresh-active.json", journal)
        return run, workspace, journal, generations

    def test_supersession_rejects_drifted_generation_file_before_terminal_writes(self) -> None:
        # scenario_id: issue105_generation_file_drift
        # target_invariant: promoted generation bytes must equal the recorded file binding
        # mutation_seam: reserialize identical JSON after promotion and before supersession
        # rematerialized_nodes: generation-set serialization; parsed object is unchanged
        # intentionally_stale_nodes: promotion.generation_set_file_sha256
        # expected_first_gate: content_repair_generation_file_binding
        # expected_error_code: runtime_refresh_handoff_generation_file_drift
        # scenario_class: single_contradiction
        run, workspace, journal, generations = self._supersession_fixture()
        generation_path = workspace / "artifact-generations.json"
        generation_path.write_text(
            json.dumps(generations, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        active_path = run / "workflow/runtime-refresh-active.json"
        active_before = active_path.read_bytes()
        output_path = (
            run
            / "待删除/runtime-refresh"
            / journal["operation_id"]
            / "content-repair/final-compile-manifest.json"
        )
        precompile = {
            "classification": "precompile_seal_reused",
            "seal_sha256": "7" * 64,
            "artifact_generations": generations["artifacts"],
        }
        authority = {
            "runtime_policy_path": str(
                (run / "workflow/compile-runtime-policy.json").resolve()
            ),
            "runtime_policy_sha256": hashlib.sha256(
                (run / "workflow/compile-runtime-policy.json").read_bytes()
            ).hexdigest(),
        }
        provider = CompileRuntimeRefreshProvider(PROJECT_ROOT)
        with (
            patch(
                "video2pdf_workflow_kernel.runtime_refresh.PrecompileQualityProvider.assess_current_seal",
                return_value=precompile,
            ),
            patch(
                "video2pdf_workflow_kernel.content_production.ContentProduction.require_current_diagnostic_compile_authority",
                return_value=authority,
            ),
            patch.object(provider.quality, "validate", return_value=None),
        ):
            with self.assertRaises(ContractError) as raised:
                provider.supersede_for_content_repair(workspace_root=workspace)
        self.assertEqual(
            "content_repair_generation_file_binding",
            raised.exception.data["first_failing_gate"],
        )
        self.assertEqual(
            "runtime_refresh_handoff_generation_file_drift",
            raised.exception.data["error_code"],
        )
        self.assertEqual(active_before, active_path.read_bytes())
        self.assertFalse(output_path.exists())

    def test_seal_fault_replay_supersedes_once_and_replays_current_manifest(self) -> None:
        run, workspace, journal, generations = self._supersession_fixture()
        precompile = {
            "classification": "precompile_seal_reused",
            "seal_sha256": "7" * 64,
            "artifact_generations": generations["artifacts"],
        }
        authority = {
            "runtime_policy_path": str(
                (run / "workflow/compile-runtime-policy.json").resolve()
            ),
            "runtime_policy_sha256": hashlib.sha256(
                (run / "workflow/compile-runtime-policy.json").read_bytes()
            ).hexdigest(),
        }
        provider = CompileRuntimeRefreshProvider(PROJECT_ROOT)
        with (
            patch(
                "video2pdf_workflow_kernel.runtime_refresh.PrecompileQualityProvider.assess_current_seal",
                return_value=precompile,
            ),
            patch(
                "video2pdf_workflow_kernel.content_production.ContentProduction.require_current_diagnostic_compile_authority",
                return_value=authority,
            ),
            patch.object(provider.quality, "validate", return_value=None),
        ):
            with self.assertRaises(RuntimeRefreshFault):
                provider.supersede_for_content_repair(
                    workspace_root=workspace,
                    fault_point="after_seal_before_runtime_supersession",
                )
            pending = json.loads(
                (run / "workflow/runtime-refresh-active.json").read_text(encoding="utf-8")
            )
            self.assertEqual("precompile_refresh_required", pending["state"])
            completed = provider.supersede_for_content_repair(workspace_root=workspace)
            repeated = provider.supersede_for_content_repair(workspace_root=workspace)
        self.assertEqual(completed, repeated)
        active = json.loads(
            (run / "workflow/runtime-refresh-active.json").read_text(encoding="utf-8")
        )
        self.assertEqual("superseded_by_content_repair", active["state"])
        manifest = json.loads(
            Path(active["successor_final_compile_manifest_path"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {
                (item["logical_id"], item["generation"], item["sha256"])
                for item in generations["artifacts"]
            },
            {
                (item["logical_id"], item["generation"], item["sha256"])
                for item in manifest["entries"]
            },
        )

    def _pyramid_rebind_fixture(
        self,
        *,
        reviewed_generation: int,
        reviewed_sha256: str | None = None,
        reviewed_context: dict | None = None,
    ) -> tuple[
        PrecompileRepairPromotionProvider,
        Path,
        Path,
        dict,
        dict,
        bytes,
    ]:
        root = new_case_dir(self.id(), label="issue105-pyramid-rebind")
        run = root / "run"
        target_path = run / "work/integration/section_02.tex"
        target_path.parent.mkdir(parents=True)
        target_path.write_bytes(b"reviewed section bytes")
        actual_target = {
            "logical_id": "integrated_section_02",
            "path": "work/integration/section_02.tex",
            "generation": 4,
            "sha256": hashlib.sha256(target_path.read_bytes()).hexdigest(),
        }
        context = {
            "pyramid_standard": "pyramid-principle-v1",
            "checkpoint": "pyramid_section",
            "audience": "reader-facing Chinese teaching PDF",
        }
        reviewed_target = {**actual_target, "generation": reviewed_generation}
        if reviewed_sha256 is not None:
            reviewed_target["sha256"] = reviewed_sha256
        binding = {
            "schema_name": "pyramid-evaluation-binding",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "target": reviewed_target,
            "evaluation_context": reviewed_context or context,
            "status": "pass",
        }
        task_id = "8" * 32
        envelope = {
            "task_id": task_id,
            "logical_task_key": "pyramid-section-section-02",
            "role": "pyramid_section",
            "claim_generation": 2,
            "claim_token": "9" * 32,
            "required_outputs": ["pyramid-report.json"],
            "pyramid_target": actual_target,
            "evaluation_context": context,
        }
        envelope_path = run / "workflow/tasks" / task_id / "envelope.json"
        write_json(envelope_path, envelope)
        bundle_root = run / "待删除/precompile-repair-promotion/issue105-rebind"
        binding_path = (
            bundle_root
            / "payload/pyramid/pyramid-section-section-02.json"
        )
        binding_bytes = canonical_json_bytes(binding)
        binding_path.parent.mkdir(parents=True)
        binding_path.write_bytes(binding_bytes)
        bundle = {
            "derived_payload": [
                {
                    "path": binding_path.relative_to(run).as_posix(),
                    "sha256": hashlib.sha256(binding_bytes).hexdigest(),
                }
            ]
        }
        return (
            PrecompileRepairPromotionProvider(PROJECT_ROOT),
            run,
            bundle_root,
            bundle,
            envelope,
            binding_bytes,
        )

    def test_pyramid_binding_rebinds_only_newer_generation_for_identical_content(self) -> None:
        provider, run, bundle_root, bundle, envelope, binding_bytes = (
            self._pyramid_rebind_fixture(reviewed_generation=3)
        )
        attempt_id = provider._materialize_replacement_attempt(
            run_dir=run,
            bundle_root=bundle_root,
            bundle=bundle,
            envelope=envelope,
        )
        binding_path = (
            bundle_root
            / "payload/pyramid/pyramid-section-section-02.json"
        )
        self.assertEqual(binding_bytes, binding_path.read_bytes())
        attempt_report = read_json(
            run
            / "workflow/tasks"
            / envelope["task_id"]
            / "attempts"
            / attempt_id
            / "pyramid-report.json"
        )
        self.assertEqual(envelope["pyramid_target"], attempt_report["target"])

    def test_pyramid_generation_rebind_rejects_changed_target_sha(self) -> None:
        # scenario_id: issue105_pyramid_rebind_sha_changed
        # target_invariant: reviewed and current target content identity must match
        # mutation_seam: reviewed target SHA only
        # rematerialized_nodes: bundle SHA declaration
        # intentionally_stale_nodes: none
        # expected_first_gate: repair_bundle_payload
        # expected_error_code: precompile_repair_pyramid_evaluation_stale
        # scenario_class: single_contradiction
        provider, run, bundle_root, bundle, envelope, _binding_bytes = (
            self._pyramid_rebind_fixture(
                reviewed_generation=4,
                reviewed_sha256="a" * 64,
            )
        )
        with self.assertRaises(ContractError) as raised:
            provider._materialize_replacement_attempt(
                run_dir=run,
                bundle_root=bundle_root,
                bundle=bundle,
                envelope=envelope,
            )
        self.assertEqual("repair_bundle_payload", raised.exception.data["first_failing_gate"])
        self.assertEqual(
            "precompile_repair_pyramid_evaluation_stale",
            raised.exception.data["error_code"],
        )

    def test_pyramid_generation_rebind_rejects_changed_evaluation_context(self) -> None:
        # scenario_id: issue105_pyramid_rebind_context_changed
        # target_invariant: reviewed and current evaluation context must match
        # mutation_seam: reviewed evaluation context only
        # rematerialized_nodes: bundle SHA declaration
        # intentionally_stale_nodes: none
        # expected_first_gate: repair_bundle_payload
        # expected_error_code: precompile_repair_pyramid_evaluation_stale
        # scenario_class: single_contradiction
        provider, run, bundle_root, bundle, envelope, _binding_bytes = (
            self._pyramid_rebind_fixture(
                reviewed_generation=4,
                reviewed_context={
                    "pyramid_standard": "pyramid-principle-v1",
                    "checkpoint": "pyramid_section",
                    "audience": "another audience",
                },
            )
        )
        with self.assertRaises(ContractError) as raised:
            provider._materialize_replacement_attempt(
                run_dir=run,
                bundle_root=bundle_root,
                bundle=bundle,
                envelope=envelope,
            )
        self.assertEqual("repair_bundle_payload", raised.exception.data["first_failing_gate"])
        self.assertEqual(
            "precompile_repair_pyramid_evaluation_stale",
            raised.exception.data["error_code"],
        )

    def _integrated_main(self, first_section: str) -> str:
        root = new_case_dir(self.id(), label="issue105-frontmatter")
        run = root / "run"
        (run / "work/outline").mkdir(parents=True)
        (run / "work/integration").mkdir(parents=True)
        (run / "workflow").mkdir(parents=True)
        support = {
            "document_class": "course",
            "class_content": "class",
            "style_name": "style",
            "style_content": "style",
            "bibliography_name": "refs.bib",
            "bibliography_content": "",
        }
        write_json(
            run / "work/outline/outline.json",
            {"compile_support": support, "terminology": []},
        )
        (run / "work/integration/section_01.tex").write_text(first_section, encoding="utf-8")
        section_sha256 = hashlib.sha256(first_section.encode("utf-8")).hexdigest()
        state = {
            "run_id": "run-105",
            "sections": {"section_01": {"figure_slots": []}},
            "artifacts": {
                "integrated_section_01": {
                    "generation": 1,
                    "sha256": section_sha256,
                    "path": "work/integration/section_01.tex",
                }
            },
            "source_binding": {},
        }
        production = ContentProduction.__new__(ContentProduction)
        production.kernel = _Kernel()
        production._record_artifact = lambda state, logical_id, relative, path, producer: state["artifacts"].setdefault(
            logical_id,
            {"generation": 1, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "path": relative},
        )
        production._integrate_main(run, state)
        return (run / "work/integration/main.tex").read_text(encoding="utf-8")

    def test_main_integration_defers_to_first_section_frontmatter(self) -> None:
        main = self._integrated_main(
            "\\begin{titlepage}Title\\end{titlepage}\n\\tableofcontents\n\\section{Thesis}\n"
        )
        self.assertEqual(0, main.count("\\tableofcontents"))
        self.assertIn("\\input{section_01.tex}", main)

    def test_main_integration_supplies_toc_when_first_section_has_none(self) -> None:
        main = self._integrated_main("\\section{Thesis}\n")
        self.assertEqual(1, main.count("\\tableofcontents"))
        self.assertLess(main.index("\\tableofcontents"), main.index("\\input{section_01.tex}"))


if __name__ == "__main__":
    unittest.main()

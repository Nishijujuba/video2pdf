from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.video_workflow._issue135_real_runtime_fixture import (
    complete_real_single_section_production,
)
from tests.video_workflow.test_issue106_partial_repair_resume import (
    Issue106PartialRepairResumeTests,
)
from tests.video_workflow.test_issue106_reader_text_continuation import (
    Issue106ReaderTextContinuationTests,
    fingerprint,
    write_json,
)
from video2pdf_workflow_kernel.precompile_quality import PRECOMPILE_OWNERS
from video2pdf_workflow_kernel.precompile_repair_promotion import (
    PrecompileRepairPromotionProvider,
)
from video2pdf_workflow_kernel.runtime_refresh import CompileRuntimeRefreshProvider
from video2pdf_workflow_kernel.utils import canonical_json_bytes, read_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts/video_workflow.py"


class Issue135TerminalRuntimeSupersessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = CLI

    def _run_cli(
        self, *arguments: str
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        command = [sys.executable, "-X", "utf8", "-B", str(self.cli)]
        completed = subprocess.run(
            [*command, *arguments],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        return completed, json.loads(completed.stdout)

    def _activate_upgraded_precompile_provider(self, run: Path) -> str:
        upgrade_root = run / "待删除/issue135/private-provider-upgrade"
        private_source = upgrade_root / "src"
        private_package = private_source / "video2pdf_workflow_kernel"
        private_package.mkdir(parents=True)
        shared_package = PROJECT_ROOT / "src/video2pdf_workflow_kernel"
        private_init = (
            f"__path__.append({str(shared_package)!r})\n".encode("utf-8")
            + (shared_package / "__init__.py").read_bytes()
        )
        (private_package / "__init__.py").write_bytes(private_init)
        descriptor = private_package / "precompile_quality.py"
        shutil.copy2(shared_package / "precompile_quality.py", descriptor)
        with descriptor.open("ab") as stream:
            stream.write(b"\n# Issue 135 private PRE provider upgrade fixture.\n")
        entrypoint = upgrade_root / "scripts/video_workflow.py"
        entrypoint.parent.mkdir(parents=True)
        shutil.copy2(CLI, entrypoint)
        self.cli = entrypoint
        return hashlib.sha256(descriptor.read_bytes()).hexdigest()

    def _captured_repair(self) -> tuple[object, dict[str, object]]:
        # This calls fixture helpers only. No earlier test method is executed.
        fixture = Issue106PartialRepairResumeTests()
        with patch.object(
            Issue106ReaderTextContinuationTests,
            "_complete_single_section_production",
            new=lambda _fixture, **kwargs: complete_real_single_section_production(
                **kwargs
            ),
        ):
            return fixture._genuine_promotion_case()

    def _predecessor_manifest(
        self, arguments: dict[str, object], passing_workspace: Path
    ) -> Path:
        run = Path(arguments["run_dir"])
        policy_path = run / "workflow/compile-runtime-policy.json"
        compile_manifest = read_json(run / "workflow/compile-manifest.json")
        manifest = {
            "schema_name": "final-compile-manifest",
            "schema_version": "1.0.0",
            "activation_status": "target_only",
            "mode": "final",
            "precompile_text_seal_sha256": read_json(
                passing_workspace / "precompile-text-seal.json"
            )["seal_sha256"],
            "entries": [
                {
                    "logical_id": entry["logical_id"],
                    "generation": entry["generation"],
                    "sha256": entry["sha256"],
                    "source_path": str((run / entry["source_path"]).resolve()),
                    "staging_path": entry["staging_path"],
                }
                for entry in compile_manifest["entries"]
            ],
            "approved_runtime_inputs": (
                CompileRuntimeRefreshProvider._approved_runtime_inputs(
                    read_json(run / "review/latex/diagnostic-compile-report.json"),
                    read_json(policy_path),
                )
            ),
            "runtime_policy": {
                "path": str(policy_path.resolve()),
                "sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
            },
        }
        manifest["manifest_sha256"] = fingerprint(manifest, "manifest_sha256")
        return write_json(run / "待删除/issue135/predecessor-final-compile-manifest.json", manifest)

    def _refresh_to_pending(
        self, arguments: dict[str, object], predecessor_manifest: Path
    ) -> dict:
        run = Path(arguments["run_dir"])
        completed, result = self._run_cli(
            "compile-runtime-refresh",
            "--run-dir",
            str(run),
            "--precompile-workspace-root",
            str(arguments["predecessor_workspace_root"]),
            "--final-compile-manifest",
            str(predecessor_manifest),
            "--refreshed-at",
            "2026-09-07T00:00:00Z",
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "precompile_refresh_required", result["classification"]
        )
        bundle_path = Path(arguments["repair_bundle_path"])
        bundle = read_json(bundle_path)
        policy_target = bundle_path.parent / "payload/compile-runtime-policy.json"
        policy_target.write_bytes(
            (run / "workflow/compile-runtime-policy.json").read_bytes()
        )
        policy_entry = next(
            entry
            for entry in bundle["derived_payload"]
            if entry["path"].endswith("payload/compile-runtime-policy.json")
        )
        policy_entry["sha256"] = hashlib.sha256(policy_target.read_bytes()).hexdigest()
        write_json(bundle_path, bundle)
        return read_json(run / "workflow/runtime-refresh-active.json")

    def _passing_predecessor(self, arguments: dict[str, object]) -> Path:
        run = Path(arguments["run_dir"])
        failed = Path(arguments["predecessor_workspace_root"])
        workspace = run / "review/precompile/workspaces/issue135-passing-predecessor"
        prepared, _ = self._run_cli(
            "delivery-quality-precompile-prepare",
            "--workspace-root",
            str(workspace),
            "--inventory",
            str(failed / "reader-facing-text-inventory.json"),
            "--artifact-generations",
            str(failed / "artifact-generations.json"),
            "--semantic-dependencies",
            str(failed / "semantic-dependencies.json"),
            "--prepared-at",
            "2026-09-06T23:50:00Z",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        self._pass_and_seal(workspace, timestamp="2026-09-06T23:55:00Z")
        return workspace

    @staticmethod
    def _promotion_arguments(arguments: dict[str, object]) -> list[str]:
        values = [
            "delivery-quality-precompile-repair-promote",
            "--run-dir",
            str(arguments["run_dir"]),
            "--repair-bundle",
            str(arguments["repair_bundle_path"]),
            "--predecessor-workspace-root",
            str(arguments["predecessor_workspace_root"]),
            "--workspace-root",
            str(arguments["workspace_root"]),
            "--inventory",
            str(arguments["inventory_path"]),
            "--semantic-dependencies",
            str(arguments["semantic_dependencies_path"]),
            "--repair-attempt-number",
            str(arguments["repair_attempt_number"]),
            "--prepared-at",
            str(arguments["prepared_at"]),
        ]
        failure = arguments.get("repair_failure_authority_path")
        if failure is not None:
            values.extend(["--repair-failure-authority", str(failure)])
        return values

    def _pass_and_seal(self, workspace: Path, *, timestamp: str) -> dict:
        for index, owner in enumerate(PRECOMPILE_OWNERS):
            skeleton_path = workspace / "reviewers" / owner / "input/review-skeleton.json"
            skeleton = read_json(skeleton_path)
            patch = {
                "schema_name": "precompile-judgment-patch",
                "schema_version": "1.0.0",
                "task_id": skeleton["task_id"],
                "owner": owner,
                "skeleton_sha256": skeleton["skeleton_sha256"],
                "generation_set_sha256": skeleton["generation_set_sha256"],
                "reviewer": {
                    "reviewer_id": f"issue135-reviewer-{index}",
                    "runtime_sha256": hashlib.sha256(skeleton_path.read_bytes()).hexdigest(),
                    "independent_from_generation_producers": True,
                },
                "results": [
                    {
                        "result_key": item["result_key"],
                        "decision": "pass",
                        "evidence_locator": f"artifact:{item['result_key']}",
                        "repair_write_set": [],
                    }
                    for item in skeleton["required_results"]
                ],
                "contract_gaps": [],
            }
            patch["patch_sha256"] = fingerprint(patch, "patch_sha256")
            patch_path = write_json(
                workspace.parent / f"issue135-{workspace.name}-{owner}.patch.json",
                patch,
            )
            committed, payload = self._run_cli(
                "delivery-quality-precompile-patch-commit",
                "--workspace-root",
                str(workspace),
                "--owner",
                owner,
                "--patch",
                str(patch_path),
                "--committed-at",
                timestamp,
            )
            self.assertEqual(0, committed.returncode, committed.stdout + committed.stderr)
            self.assertEqual(
                "precompile_judgment_patch_committed", payload["classification"]
            )
        materialized, payload = self._run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(workspace),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            timestamp,
        )
        self.assertEqual(0, materialized.returncode, materialized.stdout + materialized.stderr)
        self.assertEqual("precompile_quality_report_passed", payload["classification"])
        sealed, payload = self._run_cli(
            "delivery-quality-seal",
            "--workspace-root",
            str(workspace),
            "--sealed-at",
            timestamp,
        )
        self.assertEqual(0, sealed.returncode, sealed.stdout + sealed.stderr)
        self.assertEqual("precompile_text_seal_created", payload["classification"])
        return payload

    def _terminal_runtime_case(self) -> tuple[dict[str, object], dict, bytes, bytes]:
        _provider, arguments = self._captured_repair()
        passing_workspace = self._passing_predecessor(arguments)
        predecessor_manifest = self._predecessor_manifest(arguments, passing_workspace)
        pending = self._refresh_to_pending(arguments, predecessor_manifest)
        command = self._promotion_arguments(arguments)
        command = [
            item
            for index, item in enumerate(command)
            if not (
                item == "--repair-failure-authority"
                or (index > 0 and command[index - 1] == "--repair-failure-authority")
            )
        ]
        command.extend(
            [
                "--runtime-refresh-operation-id",
                pending["operation_id"],
                "--runtime-predecessor-final-compile-manifest",
                str(predecessor_manifest),
            ]
        )
        promoted, payload = self._run_cli(*command)
        self.assertEqual(0, promoted.returncode, promoted.stdout + promoted.stderr)
        self.assertEqual("precompile_repair_promoted", payload["classification"])
        workspace = Path(arguments["workspace_root"])
        self._pass_and_seal(workspace, timestamp="2026-09-07T00:10:00Z")
        active_path = Path(arguments["run_dir"]) / "workflow/runtime-refresh-active.json"
        journal = read_json(active_path)
        self.assertEqual("superseded_by_content_repair", journal["state"])
        self.assertEqual("superseded", journal["content_repair_handoff"]["state"])
        return (
            arguments,
            journal,
            active_path.read_bytes(),
            canonical_json_bytes(journal["content_repair_handoff"]),
        )

    def _fresh_failed_precompile(self, arguments: dict[str, object], *, label: str) -> Path:
        run = Path(arguments["run_dir"])
        source_workspace = Path(arguments["workspace_root"])
        workspace = run / f"review/precompile/workspaces/{label}"
        prepared, payload = self._run_cli(
            "delivery-quality-precompile-prepare",
            "--workspace-root",
            str(workspace),
            "--inventory",
            str(source_workspace / "reader-facing-text-inventory.json"),
            "--artifact-generations",
            str(source_workspace / "artifact-generations.json"),
            "--semantic-dependencies",
            str(source_workspace / "semantic-dependencies.json"),
            "--prepared-at",
            "2026-09-07T00:20:00Z",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        self.assertEqual("precompile_review_tasks_prepared", payload["classification"])
        failed = False
        for index, owner in enumerate(PRECOMPILE_OWNERS):
            skeleton_path = workspace / "reviewers" / owner / "input/review-skeleton.json"
            skeleton = read_json(skeleton_path)
            results = []
            for required in skeleton["required_results"]:
                is_failure = owner == "writing-quality-reviewer" and not failed
                result = {
                    "result_key": required["result_key"],
                    "decision": "fail" if is_failure else "pass",
                    "evidence_locator": f"artifact:{required['result_key']}",
                    "repair_write_set": (
                        ["work/writers/section_01.tex"] if is_failure else []
                    ),
                }
                if is_failure:
                    result["violation_id"] = "issue135_later_wording_repair"
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
                    "reviewer_id": f"issue135-failure-reviewer-{index}",
                    "runtime_sha256": hashlib.sha256(skeleton_path.read_bytes()).hexdigest(),
                    "independent_from_generation_producers": True,
                },
                "results": results,
                "contract_gaps": [],
            }
            patch["patch_sha256"] = fingerprint(patch, "patch_sha256")
            patch_path = write_json(workspace.parent / f"{label}-{owner}.patch.json", patch)
            committed, _ = self._run_cli(
                "delivery-quality-precompile-patch-commit",
                "--workspace-root",
                str(workspace),
                "--owner",
                owner,
                "--patch",
                str(patch_path),
                "--committed-at",
                "2026-09-07T00:21:00Z",
            )
            self.assertEqual(0, committed.returncode, committed.stdout + committed.stderr)
        materialized, result = self._run_cli(
            "delivery-quality-precompile-materialize",
            "--workspace-root",
            str(workspace),
            "--provider-id",
            "precompile-quality-provider",
            "--provider-version",
            "1.0.0",
            "--materialized-at",
            "2026-09-07T00:22:00Z",
        )
        self.assertEqual(0, materialized.returncode, materialized.stdout + materialized.stderr)
        self.assertEqual("precompile_quality_report_failed", result["classification"])
        return workspace

    def _later_repair_arguments(
        self, arguments: dict[str, object], predecessor: Path
    ) -> dict[str, object]:
        run = Path(arguments["run_dir"])
        _candidate_kernel, candidate_run = complete_real_single_section_production(
            writer_text=b"A later governed repair establishes a fresh current closure."
        )
        state = read_json(run / "workflow/production-state.json")
        candidate_state = read_json(candidate_run / "workflow/production-state.json")
        provider = PrecompileRepairPromotionProvider(PROJECT_ROOT)
        task_order = provider._required_replay_task_order(state)
        bundle_root = run / "待删除/issue135/later-repair-bundle"
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
            "payload/pyramid/pyramid-section-section-01.json": "pyramid_section_01_report",
            "payload/pyramid/pyramid-main.json": "pyramid_main_report",
        }
        pyramid_tasks = {
            "pyramid_outline_report": "pyramid-outline",
            "pyramid_section_01_report": "pyramid-section-section-01",
            "pyramid_main_report": "pyramid-main",
        }
        derived_payload = []
        for relative, logical_id in payload_sources.items():
            source = candidate_run / candidate_state["artifacts"][logical_id]["path"]
            payload_bytes = source.read_bytes()
            if logical_id in pyramid_tasks:
                value = json.loads(payload_bytes)
                envelope = read_json(
                    run
                    / "workflow/tasks"
                    / state["claims"][pyramid_tasks[logical_id]]["task_id"]
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
        policy_target = bundle_root / "payload/compile-runtime-policy.json"
        policy_target.parent.mkdir(parents=True, exist_ok=True)
        policy_target.write_bytes(
            (run / "workflow/compile-runtime-policy.json").read_bytes()
        )
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
                "restoration": [],
                "initial_claims": {
                    logical_key: {
                        "task_id": state["claims"][logical_key]["task_id"],
                        "claim_generation": state["claims"][logical_key]["claim_generation"],
                    }
                    for logical_key in task_order
                },
                "task_order": task_order,
            },
        )
        return {
            "run_dir": run,
            "repair_bundle_path": bundle_path,
            "predecessor_workspace_root": predecessor,
            "workspace_root": run / "review/precompile/workspaces/issue135-later-repaired",
            "inventory_path": predecessor / "reader-facing-text-inventory.json",
            "semantic_dependencies_path": predecessor / "semantic-dependencies.json",
            "repair_attempt_number": 1,
            "prepared_at": "2026-09-07T00:30:00Z",
            "repair_failure_authority_path": predecessor / "precompile-quality-report.json",
        }

    def test_terminal_runtime_closure_allows_later_public_precompile_repair_and_exact_replay(
        self,
    ) -> None:
        arguments, journal, journal_bytes, handoff_bytes = self._terminal_runtime_case()
        terminal_workspace = Path(arguments["workspace_root"])
        old_report_path = terminal_workspace / "precompile-quality-report.json"
        old_seal_path = terminal_workspace / "precompile-text-seal.json"
        old_provider_sha256 = read_json(old_report_path)["provider"]["provider_sha256"]
        upgraded_provider_sha256 = self._activate_upgraded_precompile_provider(
            Path(arguments["run_dir"])
        )
        self.assertNotEqual(old_provider_sha256, upgraded_provider_sha256)
        protected_terminal = {
            path: path.read_bytes()
            for path in (
                old_report_path,
                old_seal_path,
                Path(arguments["run_dir"])
                / "workflow/runtime-refresh-active.json",
            )
        }
        stale, stale_payload = self._run_cli(
            "delivery-quality-seal",
            "--workspace-root",
            str(terminal_workspace),
            "--sealed-at",
            "2026-09-07T00:25:00Z",
        )
        self.assertEqual(20, stale.returncode, stale.stdout + stale.stderr)
        self.assertIn("stale", stale_payload["data"]["message"].lower())
        self.assertEqual(
            protected_terminal,
            {path: path.read_bytes() for path in protected_terminal},
        )
        predecessor = self._fresh_failed_precompile(
            arguments, label="issue135-later-failed"
        )
        later = self._later_repair_arguments(arguments, predecessor)
        command = self._promotion_arguments(later)

        promoted, result = self._run_cli(*command)

        self.assertEqual(0, promoted.returncode, promoted.stdout + promoted.stderr)
        self.assertEqual("precompile_repair_promoted", result["classification"])
        run = Path(arguments["run_dir"])
        current_diagnostic_path = run / "review/latex/diagnostic-compile-report.json"
        current_diagnostic = read_json(current_diagnostic_path)
        self.assertNotEqual(
            journal["content_repair_handoff"]["successor_diagnostic_report_sha256"],
            hashlib.sha256(current_diagnostic_path.read_bytes()).hexdigest(),
        )
        self.assertEqual("pass", current_diagnostic["status"])
        self.assertTrue(current_diagnostic["dependency_closure"]["complete"])
        self.assertEqual(
            read_json(run / "workflow/compile-runtime-policy.json")["policy_sha256"],
            current_diagnostic["runtime_policy_sha256"],
        )
        self._pass_and_seal(
            Path(later["workspace_root"]), timestamp="2026-09-07T00:40:00Z"
        )
        for authority_name in (
            "precompile-quality-report.json",
            "precompile-text-seal.json",
        ):
            authority = read_json(Path(later["workspace_root"]) / authority_name)
            self.assertEqual(
                upgraded_provider_sha256,
                authority["provider"]["provider_sha256"],
            )
        repeated, replay = self._run_cli(*command)
        self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)
        self.assertEqual("precompile_repair_already_promoted", replay["classification"])
        active_path = Path(arguments["run_dir"]) / "workflow/runtime-refresh-active.json"
        self.assertEqual(journal_bytes, active_path.read_bytes())
        self.assertEqual(
            handoff_bytes,
            canonical_json_bytes(read_json(active_path)["content_repair_handoff"]),
        )

    def test_pending_runtime_refresh_without_attachments_fails_before_publication(
        self,
    ) -> None:
        _provider, arguments = self._captured_repair()
        passing_workspace = self._passing_predecessor(arguments)
        predecessor_manifest = self._predecessor_manifest(arguments, passing_workspace)
        pending = self._refresh_to_pending(arguments, predecessor_manifest)
        run = Path(arguments["run_dir"])
        protected = {
            path: path.read_bytes()
            for path in (
                run / "workflow/runtime-refresh-active.json",
                run / "workflow/production-state.json",
                run / "workflow/compile-manifest.json",
            )
        }
        command = self._promotion_arguments(arguments)
        command = [
            item
            for index, item in enumerate(command)
            if not (
                item == "--repair-failure-authority"
                or (index > 0 and command[index - 1] == "--repair-failure-authority")
            )
        ]
        completed, result = self._run_cli(*command)
        self.assertEqual(20, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "content_repair_runtime_state", result["data"]["first_failing_gate"]
        )
        self.assertEqual(
            "runtime_refresh_handoff_identity_required", result["data"]["error_code"]
        )
        self.assertFalse(Path(arguments["workspace_root"]).exists())
        self.assertEqual(protected, {path: path.read_bytes() for path in protected})

        command.extend(
            [
                "--runtime-refresh-operation-id",
                pending["operation_id"],
                "--runtime-predecessor-final-compile-manifest",
                str(predecessor_manifest),
            ]
        )
        recovered, payload = self._run_cli(*command)
        self.assertEqual(0, recovered.returncode, recovered.stdout + recovered.stderr)
        self.assertEqual("precompile_repair_promoted", payload["classification"])

    def test_invalid_terminal_closure_binding_fails_before_publication_and_recovers(
        self,
    ) -> None:
        arguments, journal, journal_bytes, _handoff_bytes = self._terminal_runtime_case()
        predecessor = self._fresh_failed_precompile(
            arguments, label="issue135-invalid-terminal-failed"
        )
        later = self._later_repair_arguments(arguments, predecessor)
        run = Path(arguments["run_dir"])
        active_path = run / "workflow/runtime-refresh-active.json"
        invalid = json.loads(json.dumps(journal))
        invalid["content_repair_handoff"][
            "successor_final_compile_manifest_sha256"
        ] = "f" * 64
        invalid["content_repair_handoff"]["handoff_sha256"] = fingerprint(
            invalid["content_repair_handoff"], "handoff_sha256"
        )
        invalid["journal_sha256"] = fingerprint(invalid, "journal_sha256")
        write_json(active_path, invalid)
        protected = {
            path: path.read_bytes()
            for path in (
                run / "workflow/production-state.json",
                run / "workflow/compile-manifest.json",
            )
        }
        completed, result = self._run_cli(*self._promotion_arguments(later))
        self.assertEqual(20, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(
            "content_repair_terminal_manifest_binding",
            result["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "runtime_refresh_terminal_manifest_file_drift",
            result["data"]["error_code"],
        )
        self.assertFalse(Path(later["workspace_root"]).exists())
        self.assertEqual(protected, {path: path.read_bytes() for path in protected})

        active_path.write_bytes(journal_bytes)
        recovered, payload = self._run_cli(*self._promotion_arguments(later))
        self.assertEqual(0, recovered.returncode, recovered.stdout + recovered.stderr)
        self.assertEqual("precompile_repair_promoted", payload["classification"])


if __name__ == "__main__":
    unittest.main()

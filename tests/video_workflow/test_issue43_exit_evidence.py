from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock
import uuid

from jsonschema import Draft202012Validator

from scripts import issue43_exit_evidence_contract as contract
from scripts import collect_issue43_exit_evidence as collector
from scripts import validate_slice_exit_evidence as validator
from tests.video_workflow._issue43_git_authority import (
    AUTHORITY_ROOT,
    AUTHORITY_DESCRIPTOR_PATH,
    AUTHORITY_OVERLAY_PATHS,
    _freeze_authority_overlay,
    _shared_git_objects_directory,
    build_current_global_gate_authority,
    commit_later_implementation_change,
)
from tests.video_workflow._test_run import new_case_dir
from video2pdf_workflow_kernel.evidence import (
    fingerprint_implementation_changes,
    sha256_git_blob,
)
from video2pdf_workflow_kernel.global_gate import GlobalGatePublisher
from video2pdf_workflow_kernel.global_gate_exit_evidence import (
    EVIDENCE_PREFIX,
    SLICE_BASE_COMMIT,
    ExitEvidenceValidationError,
    validate_global_gate_exit_evidence,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "schemas/exit-evidence-manifest.v2.schema.json"
CLI = PROJECT_ROOT / "scripts/video_workflow.py"


class Issue43ExitEvidenceContractTests(unittest.TestCase):
    def manifest(self) -> dict:
        sha256 = "1" * 64
        run_id = "11111111-1111-4111-8111-111111111111"
        return {
            "$schema": "https://video2pdf.local/schemas/exit-evidence-manifest.v2.schema.json",
            "schema_version": 2,
            "kind": "video-workflow-exit-evidence",
            "fingerprint_algorithm": "sha256-raw-v1",
            "slice": {"number": contract.SLICE_NUMBER, "name": contract.SLICE_NAME},
            "slice_base_commit": contract.SLICE_BASE_COMMIT,
            "implementation_commit": "2" * 40,
            "evidence_paths": [
                "evidence/global-gate/exit-evidence-manifest.json",
                *[
                    f"evidence/global-gate/logs/{command_id}.log"
                    for command_id, _, _ in contract.COMMANDS
                ],
                *[
                    f"evidence/global-gate/persisted/{command_id}/{filename}"
                    for command_id, _, _ in contract.COMMANDS
                    for filename in ("command.json", "status.json", "exit-code.txt")
                ],
            ],
            "generated_at": "2026-08-02T00:00:00Z",
            "activation_scope": deepcopy(contract.ACTIVATION_SCOPE),
            "atomic_members": list(contract.ATOMIC_MEMBERS),
            "atomic_member_status": deepcopy(contract.ATOMIC_MEMBER_STATUS),
            "mirror_checks": [
                {
                    "source_path": str((PROJECT_ROOT / source).resolve()),
                    "mirror_path": str((PROJECT_ROOT / mirror).resolve()),
                    "source_sha256": hashlib.sha256(
                        (PROJECT_ROOT / source).read_bytes()
                    ).hexdigest(),
                    "mirror_sha256": hashlib.sha256(
                        (PROJECT_ROOT / mirror).read_bytes()
                    ).hexdigest(),
                    "status": "equal",
                }
                for source, mirror in contract.MIRROR_SPECS
            ],
            "policy_status": contract.POLICY_STATUS,
            "commands": [
                {
                    "test_id": command_id,
                    "command": list(command),
                    "expected_exit_code": expected_exit_code,
                    "actual_exit_code": expected_exit_code,
                    "log": {
                        "role": "command_log",
                        "path": f"evidence/global-gate/logs/{command_id}.log",
                        "sha256": sha256,
                    },
                    "persisted_run": {
                        "run_id": run_id,
                        "command_record": {
                            "role": "persisted_command_record",
                            "path": f"evidence/global-gate/persisted/{command_id}/command.json",
                            "sha256": sha256,
                        },
                        "terminal_status": {
                            "role": "persisted_terminal_status",
                            "path": f"evidence/global-gate/persisted/{command_id}/status.json",
                            "sha256": sha256,
                        },
                        "exit_code": {
                            "role": "persisted_exit_code",
                            "path": f"evidence/global-gate/persisted/{command_id}/exit-code.txt",
                            "sha256": sha256,
                        },
                    },
                    "conforms": True,
                }
                for command_id, command, expected_exit_code in contract.COMMANDS
            ],
            "expected_checkpoints": deepcopy(contract.EXPECTED_CHECKPOINTS),
            "fixtures": [
                {"role": role, "path": path, "sha256": sha256}
                for role, path in contract.FIXTURE_SPECS
            ],
            "results": deepcopy(contract.RESULTS),
            "result_bindings": deepcopy(contract.RESULT_BINDINGS),
            "artifact_fingerprints": [
                {
                    "role": "implementation_artifact",
                    "path": "scripts/issue43_exit_evidence_contract.py",
                    "sha256": sha256,
                }
            ],
            "unresolved_exceptions": [],
            "overall_decision": "pass",
        }

    def test_contract_registers_the_complete_global_gate_qualification_surface(self) -> None:
        required_results = {
            "legacy_run_record_free_v2_pass",
            "kernel_v2_pass",
            "v1_rejected",
            "stale_legacy_authority_rejected",
            "incomplete_mirrors_rejected",
            "delivery_guard_runtime_mirrors_bound",
            "incomplete_delivery_guard_runtime_mirror_rejected",
            "unsupported_identity_rejected",
            "contract_gap_rejected",
            "failed_atomic_member_rejected",
            "patch_publication_recovered",
            "report_publication_recovered",
            "activation_publication_recovered",
            "activation_writers_fenced",
            "patch_writers_fenced",
            "report_writers_fenced",
            "patch_retry_idempotent",
            "patch_after_control_commit_recovered_idempotent",
            "report_retry_idempotent",
            "activation_retry_idempotent",
            "activation_after_control_commit_recovered_idempotent",
            "control_store_unavailable_rejected",
            "control_store_corrupt_rejected",
            "control_store_locked_rejected",
            "control_store_incompatible_rejected",
            "fallback_rejected",
            "translation_rejected",
            "synthetic_legacy_run_rejected",
            "dual_authority_rejected",
            "activation_reconcile_stale_publication_rejected",
            "active_global_gate_only",
            "complete_acceptance_v2_module",
            "complete_issue43_active_guard_module",
            "complete_delivery_guard_module",
            "policy_authority_refresh_preserves_base_global_gate",
            "policy_authority_refresh_recovered_with_stable_base",
            "modern_legacy_adoption_pass",
            "modern_legacy_adoption_schema_rejected",
            "modern_legacy_guard_pass",
            "modern_legacy_guard_schema_rejected",
            "complete_policy_authority_refresh_module",
            "complete_issue41_legacy_final_compile_module",
            "complete_issue43_global_gate_module",
            "complete_guarded_final_compile_adapter_module",
            "complete_rendered_text_reconciliation_module",
            "complete_issue13_final_evidence_module",
        }
        actual_results = {
            result_id for values in contract.RESULTS.values() for result_id in values
        }
        self.assertTrue(required_results <= actual_results)
        self.assertEqual(
            actual_results,
            {binding["result_id"] for binding in contract.RESULT_BINDINGS},
        )
        self.assertEqual(
            set(contract.QUALIFICATION_TEST_TARGETS)
            | {item[3] for item in contract.COMPLETE_MODULE_RESULT_SPECS},
            {binding["test_target"] for binding in contract.RESULT_BINDINGS},
        )
        self.assertEqual(49, len(contract.RESULT_BINDINGS))
        self.assertGreaterEqual(
            len(contract.QUALIFICATION_TEST_TARGETS),
            12,
            "Issue #43 evidence cannot collapse the cutover matrix into a few broad tracers",
        )
        self.assertEqual("active_global_gate", contract.ACTIVATION_SCOPE["kind"])
        self.assertEqual("unchanged", contract.ACTIVATION_SCOPE["platform_kernel_authority"])
        self.assertEqual(
            contract.QUALIFICATION_CONTRACT_SHA256,
            hashlib.sha256((
                json.dumps(
                    contract.RESULT_BINDINGS,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n"
            ).encode("utf-8")).hexdigest(),
        )

    def test_refresh_contract_covers_modern_legacy_and_stable_base_authority(self) -> None:
        bindings = {item["result_id"]: item for item in contract.RESULT_BINDINGS}
        self.assertEqual(
            "tests.video_workflow.test_global_gate_policy_authority_refresh",
            bindings["complete_policy_authority_refresh_module"]["test_target"],
        )
        self.assertEqual(
            "tests.video_workflow.test_issue41_legacy_final_compile",
            bindings["complete_issue41_legacy_final_compile_module"]["test_target"],
        )
        for result_id in (
            "modern_legacy_adoption_pass",
            "modern_legacy_adoption_schema_rejected",
            "modern_legacy_guard_pass",
            "modern_legacy_guard_schema_rejected",
        ):
            self.assertIn(bindings[result_id]["test_target"], contract.QUALIFICATION_TEST_TARGETS)
        self.assertEqual(
            ("compile_provenance", "legacy_compile_provenance_invalid"),
            (
                bindings["modern_legacy_adoption_schema_rejected"]["expected_first_failing_gate"],
                bindings["modern_legacy_adoption_schema_rejected"]["expected_error_code"],
            ),
        )
        self.assertEqual(
            ("delivery_guard", "delivery_guard_failed"),
            (
                bindings["modern_legacy_guard_schema_rejected"]["expected_first_failing_gate"],
                bindings["modern_legacy_guard_schema_rejected"]["expected_error_code"],
            ),
        )
        self.assertEqual("active_global_gate", contract.ACTIVATION_SCOPE["kind"])
        self.assertEqual("unchanged", contract.ACTIVATION_SCOPE["platform_kernel_authority"])

    def test_each_complete_module_has_one_dedicated_command(self) -> None:
        commands = {command_id: command for command_id, command, _ in contract.COMMANDS}
        for _, _, command_id, test_target in contract.COMPLETE_MODULE_RESULT_SPECS:
            self.assertIn(command_id, commands)
            self.assertIn(test_target, commands[command_id])

    def _synthetic_terminal_collection(
        self, root: Path, *, run_id_prefix: str = "00000000-0000-4000-8000"
    ) -> tuple[dict, Path, Path]:
        run_root = root / "runs"
        evidence_dir = root / "evidence/global-gate"
        runs = []
        for index, (command_id, command, expected_exit_code) in enumerate(contract.COMMANDS, 1):
            run_id = f"{run_id_prefix}-{index:012d}"
            run_dir = run_root / command_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "command.json").write_text(json.dumps({
                "schema_name": "persisted-command",
                "schema_version": "1.0.0",
                "run_id": run_id,
                "cwd": str(root.resolve()),
                "argv": list(command),
                "accepted_exit_codes": [expected_exit_code],
            }) + "\n", encoding="utf-8")
            (run_dir / "status.json").write_text(json.dumps({
                "schema_name": "persisted-command-status",
                "schema_version": "1.0.0",
                "run_id": run_id,
                "state": "succeeded",
                "exit_code": expected_exit_code,
                "security": {
                    "acceptance_evidence_eligible": True,
                    "classification": "no_secret_detected",
                },
            }) + "\n", encoding="utf-8")
            (run_dir / "exit-code.txt").write_text(
                f"{expected_exit_code}\n", encoding="utf-8"
            )
            (run_dir / "stdout.log").write_text("synthetic pass\n", encoding="utf-8")
            (run_dir / "stderr.log").write_bytes(b"")
            runs.append({"command_id": command_id, "run_id": run_id, "run_dir": str(run_dir)})
        return ({
            "schema_name": "issue43-exit-evidence-collection",
            "schema_version": "1.0.0",
            "implementation_commit": "2" * 40,
            "runs": runs,
        }, evidence_dir, run_root)

    def test_complete_delivery_guard_uses_import_safe_unittest_discovery(self) -> None:
        command_id, command, expected_exit_code = next(
            item
            for item in contract.COMMANDS
            if item[0] == "issue43-complete-delivery-guard"
        )
        self.assertEqual(0, expected_exit_code)
        self.assertEqual(
            ["-m", "unittest", "discover", "-v", "-s"],
            list(command[4:9]),
        )
        self.assertEqual(
            ".agents/skills/final-delivery-acceptance/scripts",
            command[9],
        )
        self.assertEqual(["-p", "test_delivery_guard.py"], list(command[10:]))
        binding = next(
            item
            for item in contract.RESULT_BINDINGS
            if item["command_id"] == command_id
        )
        self.assertEqual("test_delivery_guard.py", binding["test_target"])
        self.assertIn(binding["test_target"], command)

    def test_finalizer_binds_synthetic_persisted_terminal_evidence(self) -> None:
        root = new_case_dir(self.id(), label="issue43-persisted-terminal")
        collection, evidence_dir, _ = self._synthetic_terminal_collection(root)
        with (
            mock.patch.object(collector, "PROJECT_ROOT", root),
            mock.patch.object(collector, "EVIDENCE_DIR", evidence_dir),
            mock.patch.object(collector, "LOG_DIR", evidence_dir / "logs"),
        ):
            (evidence_dir / "logs").mkdir(parents=True)
            commands = collector.finalize_commands(collection, "2" * 40)
        self.assertEqual(len(contract.COMMANDS), len(commands))
        for command in commands:
            self.assertTrue(command["conforms"])
            self.assertEqual(
                {"run_id", "command_record", "terminal_status", "exit_code"},
                set(command["persisted_run"]),
            )

    def test_preflight_rejects_nonterminal_parent_before_publication(self) -> None:
        root = new_case_dir(self.id(), label="issue43-finalize-preflight")
        collection, _, run_root = self._synthetic_terminal_collection(root)
        status_path = run_root / contract.COMMANDS[0][0] / "status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["state"] = "running"
        status["exit_code"] = None
        status_path.write_text(json.dumps(status) + "\n", encoding="utf-8")
        with (
            mock.patch.object(collector, "PROJECT_ROOT", root),
            self.assertRaisesRegex(RuntimeError, "no coherent terminal state"),
        ):
            collector.require_collection_terminal(collection)

    def test_collect_never_starts_a_second_run_while_the_first_is_active(self) -> None:
        root = new_case_dir(self.id(), label="issue43-sequential-collect")
        calls: list[str] = []

        def fake_start(command_id: str, _command: tuple[str, ...], _exit: int) -> dict[str, str]:
            calls.append(command_id)
            run_dir = root / "runs" / command_id
            run_dir.mkdir(parents=True)
            run_id = "00000000-0000-4000-8000-000000000001"
            (run_dir / "status.json").write_text(json.dumps({
                "schema_name": "persisted-command-status",
                "run_id": run_id,
                "state": "running",
                "exit_code": None,
            }) + "\n", encoding="utf-8")
            return {"command_id": command_id, "run_id": run_id, "run_dir": str(run_dir)}

        with (
            mock.patch.object(collector, "PROJECT_ROOT", root),
            mock.patch.object(collector, "REFRESH_ROOT", root / "refresh"),
            mock.patch.object(collector, "_start_command", side_effect=fake_start),
            mock.patch.object(
                collector,
                "_observe_run",
                return_value={"state": "running", "exit_code": None},
            ),
        ):
            first = collector.advance_collection("2" * 40)
            resumed = collector.advance_collection(
                "2" * 40, Path(first["collection_path"])
            )
        self.assertEqual([contract.COMMANDS[0][0]], calls)
        self.assertEqual("running", resumed["orchestration_state"])
        self.assertEqual(1, len(resumed["runs"]))

    def test_collect_resumes_after_interruption_without_relaunching_completed_run(self) -> None:
        root = new_case_dir(self.id(), label="issue43-sequential-resume")
        calls: list[str] = []

        def fake_start(command_id: str, _command: tuple[str, ...], _exit: int) -> dict[str, str]:
            calls.append(command_id)
            index = len(calls)
            run_dir = root / "runs" / command_id
            run_dir.mkdir(parents=True)
            run_id = f"00000000-0000-4000-8000-{index:012d}"
            (run_dir / "status.json").write_text(json.dumps({
                "schema_name": "persisted-command-status",
                "run_id": run_id,
                "state": "running",
                "exit_code": None,
            }) + "\n", encoding="utf-8")
            return {"command_id": command_id, "run_id": run_id, "run_dir": str(run_dir)}

        with (
            mock.patch.object(collector, "PROJECT_ROOT", root),
            mock.patch.object(collector, "REFRESH_ROOT", root / "refresh"),
            mock.patch.object(collector, "_start_command", side_effect=fake_start),
            mock.patch.object(
                collector,
                "_observe_run",
                return_value={
                    "state": "succeeded",
                    "exit_code": 0,
                    "security": {
                        "acceptance_evidence_eligible": True,
                        "classification": "no_secret_detected",
                    },
                },
            ),
        ):
            first = collector.advance_collection("2" * 40)
            first_run = first["runs"][0]
            first_status = Path(first_run["run_dir"]) / "status.json"
            first_status.write_text(json.dumps({
                "schema_name": "persisted-command-status",
                "run_id": first_run["run_id"],
                "state": "succeeded",
                "exit_code": 0,
                "security": {
                    "acceptance_evidence_eligible": True,
                    "classification": "no_secret_detected",
                },
            }) + "\n", encoding="utf-8")
            (Path(first_run["run_dir"]) / "exit-code.txt").write_text(
                "0\n", encoding="utf-8"
            )
            resumed = collector.advance_collection(
                "2" * 40, Path(first["collection_path"])
            )
        self.assertEqual(
            [contract.COMMANDS[0][0], contract.COMMANDS[1][0]], calls
        )
        self.assertEqual(first_run, resumed["runs"][0])
        self.assertEqual(2, len(resumed["runs"]))

    def test_shared_validator_rejects_running_status_at_persisted_terminal_gate(self) -> None:
        # scenario_id: issue43_persisted_status_not_terminal
        # authority: persisted status -> boundary: Exit Evidence finalization
        # mutation: terminal state only; rematerialized: status fingerprint; stale: none
        # expected_first_gate: persisted_command_terminal
        # expected_error_code: persisted_command_terminal_invalid
        root = new_case_dir(self.id(), label="issue43-persisted-running")
        collection, evidence_dir, _ = self._synthetic_terminal_collection(root)
        logs = evidence_dir / "logs"
        logs.mkdir(parents=True)
        with (
            mock.patch.object(collector, "PROJECT_ROOT", root),
            mock.patch.object(collector, "EVIDENCE_DIR", evidence_dir),
            mock.patch.object(collector, "LOG_DIR", logs),
        ):
            commands = collector.finalize_commands(collection, "2" * 40)
        target = commands[0]["persisted_run"]["terminal_status"]
        status_path = root / target["path"]
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["state"] = "running"
        status_path.write_text(json.dumps(status) + "\n", encoding="utf-8")
        target["sha256"] = hashlib.sha256(status_path.read_bytes()).hexdigest()
        manifest_path = evidence_dir / "exit-evidence-manifest.json"
        manifest_path.write_text("{}\n", encoding="utf-8")
        manifest = {
            "slice": {"number": 11},
            "implementation_commit": "2" * 40,
            "commands": commands,
            "fixtures": [],
            "evidence_paths": [
                manifest_path.relative_to(root).as_posix(),
                *[command["log"]["path"] for command in commands],
                *[
                    artifact["path"]
                    for command in commands
                    for key, artifact in command["persisted_run"].items()
                    if key != "run_id"
                ],
            ],
        }
        with (
            mock.patch.object(validator, "PROJECT_ROOT", root),
            self.assertRaises(validator.EvidenceError) as raised,
        ):
            validator.validate_bindings(manifest, manifest_path)
        self.assertEqual("persisted_command_terminal", raised.exception.first_failing_gate)
        self.assertEqual("persisted_command_terminal_invalid", raised.exception.error_code)

    def test_v2_schema_admits_global_gate_activation_and_historical_manifests(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        schema_validator = Draft202012Validator(schema)
        schema_validator.validate(self.manifest())
        for number in range(7, 11):
            path = PROJECT_ROOT / f"evidence/slice-{number:02d}/exit-evidence-manifest.json"
            schema_validator.validate(json.loads(path.read_text(encoding="utf-8")))

    def test_manifest_v2_is_the_direct_sha_bound_global_gate_activation_contract(self) -> None:
        root = new_case_dir(self.id(), label="issue43-manifest-v2-activation")
        repository, manifest_path = build_current_global_gate_authority(root)
        result = GlobalGatePublisher(project_root=repository).activate(
            control_store_root=root,
            exit_evidence=manifest_path,
            activated_at="2026-08-03T00:00:00Z",
        )
        authority = json.loads(
            Path(result["authority_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            authority["exit_evidence_sha256"],
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        )

    def test_removed_implementation_path_requires_tombstone_authority(self) -> None:
        root = new_case_dir(self.id(), label="issue43-missing-tombstone")
        repository, manifest_path = build_current_global_gate_authority(root)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["implementation_tombstones"])
        manifest.pop("implementation_tombstones")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(ExitEvidenceValidationError) as raised:
            validate_global_gate_exit_evidence(
                manifest_path, project_root=repository
            )
        self.assertEqual(
            "implementation_tombstones", raised.exception.first_failing_gate
        )
        self.assertEqual(
            "implementation_tombstones_stale", raised.exception.error_code
        )

    def test_authority_fixture_rebuilds_a_polluted_cache_before_qualification_reuse(self) -> None:
        # scenario_id: authority_cache_pollution_rebuild
        # authority input: source checkout and control-store identity
        # derived nodes: implementation boundary, evidence closure, publication
        # boundary: publication commit
        # target contradiction: cached HEAD no longer equals that publication
        # rematerialized nodes: complete authority graph in a new repository
        # expected first gate after repair: no failure; publication paths are exact
        root = new_case_dir(self.id(), label="issue43-authority-cache-order")
        repository, manifest_path = build_current_global_gate_authority(root)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        publication_paths = set(
            subprocess.check_output(
                ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
                cwd=repository,
                text=True,
                encoding="utf-8",
            ).splitlines()
        )
        self.assertEqual(set(manifest["evidence_paths"]), publication_paths)

        with ThreadPoolExecutor(max_workers=4) as executor:
            concurrent_results = list(
                executor.map(lambda _: build_current_global_gate_authority(root), range(8))
            )
        self.assertEqual({repository}, {item[0] for item in concurrent_results})

        commit_later_implementation_change(repository)
        rebuilt_repository, rebuilt_manifest = build_current_global_gate_authority(root)
        self.assertNotEqual(repository, rebuilt_repository)
        GlobalGatePublisher(project_root=rebuilt_repository).activate(
            control_store_root=root,
            exit_evidence=rebuilt_manifest,
            activated_at="2026-08-03T00:00:00Z",
        )

    def test_authority_implementation_boundary_is_never_evidence_only(self) -> None:
        # scenario_id: implementation_boundary_has_real_fixture_authority
        # authority input: source implementation commit and qualification contract
        # derived node: deterministic authority descriptor
        # boundary: evidence-free implementation commit
        # expected first gate: no failure; implementation has non-evidence authority
        root = new_case_dir(self.id(), label="issue43-authority-implementation")
        repository, manifest_path = build_current_global_gate_authority(root)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        implementation = manifest["implementation_commit"]
        implementation_paths = set(
            subprocess.check_output(
                [
                    "git", "diff-tree", "--no-commit-id", "--name-only", "-r",
                    implementation,
                ],
                cwd=repository,
                text=True,
                encoding="utf-8",
            ).splitlines()
        )
        self.assertIn(AUTHORITY_DESCRIPTOR_PATH, implementation_paths)
        self.assertTrue(
            implementation_paths - set(manifest["evidence_paths"]),
            "implementation commit must contain a non-evidence authority path",
        )
        descriptor = json.loads(
            (repository / AUTHORITY_DESCRIPTOR_PATH).read_text(encoding="utf-8")
        )
        source_parent = subprocess.check_output(
            ["git", "rev-parse", f"{implementation}^"],
            cwd=repository,
            text=True,
            encoding="utf-8",
        ).strip()
        self.assertEqual(contract.QUALIFICATION_CONTRACT_SHA256, descriptor["qualification_contract_sha256"])
        self.assertEqual(source_parent, descriptor["source_implementation_commit"])
        self.assertIn(
            AUTHORITY_DESCRIPTOR_PATH,
            {item["path"] for item in manifest["artifact_fingerprints"]},
        )
        GlobalGatePublisher(project_root=repository).activate(
            control_store_root=root,
            exit_evidence=manifest_path,
            activated_at="2026-08-03T00:00:00Z",
        )

    def test_authority_fixture_reads_frozen_tree_from_real_gitfile_worktree(self) -> None:
        # scenario_id: linked_worktree_shared_object_authority
        # authority input: a frozen source commit reached through a gitfile worktree
        # derived nodes: alternate object path, implementation, evidence publication
        # boundary: source HEAD before authority materialization
        # expected first gate: no failure; the frozen tree remains readable
        retained = (
            AUTHORITY_ROOT
            / "linked-worktree-regressions"
            / uuid.uuid4().hex
        )
        shared_clone = retained / "shared-clone"
        linked_worktree = retained / "linked-worktree"
        retained.mkdir(parents=True, exist_ok=False)
        subprocess.check_call(
            ["git", "init", str(shared_clone)]
        )
        source_objects = _shared_git_objects_directory(PROJECT_ROOT)
        source_alternates = shared_clone / ".git/objects/info/alternates"
        source_alternates.parent.mkdir(parents=True, exist_ok=True)
        source_alternates.write_bytes(
            str(source_objects).encode("utf-8") + b"\n"
        )
        source_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
        ).strip()
        subprocess.check_call(
            [
                "git", "-C", str(shared_clone), "update-ref",
                "refs/heads/fixture", source_head,
            ]
        )
        subprocess.check_call(
            [
                "git", "-C", str(shared_clone), "worktree", "add", "--detach",
                "--no-checkout", str(linked_worktree), source_head,
            ]
        )
        self.assertTrue((linked_worktree / ".git").is_file())
        resolved_objects = _shared_git_objects_directory(linked_worktree)
        self.assertEqual(
            (shared_clone / ".git/objects").resolve(),
            resolved_objects,
        )

        root = new_case_dir(self.id(), label="issue43-gitfile-authority")
        repository, manifest_path = build_current_global_gate_authority(
            root,
            source_git_repository=linked_worktree,
        )
        self.assertEqual(
            str(resolved_objects),
            (repository / ".git/objects/info/alternates")
            .read_text(encoding="utf-8")
            .strip(),
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        descriptor = json.loads(
            subprocess.check_output(
                [
                    "git", "show",
                    f"{manifest['implementation_commit']}:{AUTHORITY_DESCRIPTOR_PATH}",
                ],
                cwd=repository,
                text=True,
                encoding="utf-8",
            )
        )
        frozen_commit = descriptor["source_implementation_commit"]
        frozen_contract = subprocess.check_output(
            [
                "git", "show",
                f"{frozen_commit}:scripts/issue43_exit_evidence_contract.py",
            ],
            cwd=repository,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            subprocess.check_output(
                [
                    "git", "show",
                    f"{frozen_commit}:scripts/issue43_exit_evidence_contract.py",
                ],
                cwd=linked_worktree,
                text=True,
                encoding="utf-8",
            ),
            frozen_contract,
        )

        subprocess.check_call(
            ["git", "config", "user.name", "Issue43 Source Fixture"],
            cwd=linked_worktree,
        )
        subprocess.check_call(
            ["git", "config", "user.email", "issue43-source@example.invalid"],
            cwd=linked_worktree,
        )
        subprocess.check_call(
            ["git", "read-tree", "HEAD"],
            cwd=linked_worktree,
        )
        advanced_path = linked_worktree / "src/issue43_cache_freshness.py"
        advanced_path.parent.mkdir(parents=True, exist_ok=True)
        advanced_path.write_text("SOURCE_HEAD_ADVANCED = True\n", encoding="utf-8")
        subprocess.check_call(
            ["git", "add", "src/issue43_cache_freshness.py"],
            cwd=linked_worktree,
        )
        subprocess.check_call(
            ["git", "commit", "-m", "Advance fixture source implementation"],
            cwd=linked_worktree,
        )
        advanced_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=linked_worktree,
            text=True,
            encoding="utf-8",
        ).strip()

        rebuilt_repository, rebuilt_manifest_path = (
            build_current_global_gate_authority(
                root,
                source_git_repository=linked_worktree,
            )
        )
        self.assertNotEqual(repository, rebuilt_repository)
        rebuilt_manifest = json.loads(
            rebuilt_manifest_path.read_text(encoding="utf-8")
        )
        rebuilt_implementation = rebuilt_manifest["implementation_commit"]
        self.assertEqual(
            advanced_head,
            subprocess.check_output(
                ["git", "rev-parse", f"{rebuilt_implementation}^"],
                cwd=rebuilt_repository,
                text=True,
                encoding="utf-8",
            ).strip(),
        )
        rebuilt_descriptor = json.loads(
            subprocess.check_output(
                [
                    "git", "show",
                    f"{rebuilt_implementation}:{AUTHORITY_DESCRIPTOR_PATH}",
                ],
                cwd=rebuilt_repository,
                text=True,
                encoding="utf-8",
            )
        )
        self.assertEqual(
            advanced_head,
            rebuilt_descriptor["source_implementation_commit"],
        )
        self.assertIn(
            "src/issue43_cache_freshness.py",
            {item["path"] for item in rebuilt_manifest["artifact_fingerprints"]},
        )
        rebuilt_origin = json.loads(
            (AUTHORITY_ROOT / f"{rebuilt_repository.name}.origin.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(advanced_head, rebuilt_origin["source_head"])
        self.assertEqual(
            advanced_head,
            rebuilt_origin["source_implementation_commit"],
        )

    def test_authority_overlay_identity_and_bytes_refresh_the_cache(self) -> None:
        # scenario_id: authority_overlay_cache_freshness
        # authority inputs: frozen Git source plus complete WIP overlay bytes
        # boundaries: cache lookup, implementation commit, evidence publication
        # expected first gate: no failure; each overlay identity is materialized
        retained = AUTHORITY_ROOT / "overlay-regressions" / uuid.uuid4().hex
        first_overlay = retained / "overlay-one"
        second_overlay = retained / "overlay-two"
        for overlay in (first_overlay, second_overlay):
            for relative in AUTHORITY_OVERLAY_PATHS:
                target = overlay / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PROJECT_ROOT / relative, target)

        root = new_case_dir(self.id(), label="issue43-overlay-authority")
        first_repository, first_manifest_path = build_current_global_gate_authority(
            root,
            authority_overlay_root=first_overlay,
        )
        second_repository, second_manifest_path = build_current_global_gate_authority(
            root,
            authority_overlay_root=second_overlay,
        )
        self.assertNotEqual(first_repository, second_repository)
        first_origin = json.loads(
            (AUTHORITY_ROOT / f"{first_repository.name}.origin.json")
            .read_text(encoding="utf-8")
        )
        second_origin = json.loads(
            (AUTHORITY_ROOT / f"{second_repository.name}.origin.json")
            .read_text(encoding="utf-8")
        )
        self.assertNotEqual(
            first_origin["authority_overlay_root"],
            second_origin["authority_overlay_root"],
        )
        self.assertEqual(
            first_origin["authority_overlay_sha256"],
            second_origin["authority_overlay_sha256"],
        )

        target_relative = "src/video2pdf_workflow_kernel/global_gate.py"
        target_path = second_overlay / target_relative
        target_path.write_bytes(
            target_path.read_bytes()
            + b"\n# retained Issue 43 overlay cache freshness probe\n"
        )
        third_repository, third_manifest_path = build_current_global_gate_authority(
            root,
            authority_overlay_root=second_overlay,
        )
        self.assertNotEqual(second_repository, third_repository)
        second_manifest = json.loads(
            second_manifest_path.read_text(encoding="utf-8")
        )
        third_manifest = json.loads(
            third_manifest_path.read_text(encoding="utf-8")
        )
        second_fingerprints = {
            item["path"]: item["sha256"]
            for item in second_manifest["artifact_fingerprints"]
        }
        third_fingerprints = {
            item["path"]: item["sha256"]
            for item in third_manifest["artifact_fingerprints"]
        }
        expected_target_sha256 = hashlib.sha256(target_path.read_bytes()).hexdigest()
        self.assertNotEqual(
            second_fingerprints[target_relative],
            third_fingerprints[target_relative],
        )
        self.assertEqual(
            expected_target_sha256,
            third_fingerprints[target_relative],
        )
        third_implementation = third_manifest["implementation_commit"]
        self.assertEqual(
            target_path.read_text(encoding="utf-8"),
            subprocess.check_output(
                ["git", "show", f"{third_implementation}:{target_relative}"],
                cwd=third_repository,
                text=True,
                encoding="utf-8",
            ),
        )
        third_descriptor = json.loads(
            subprocess.check_output(
                [
                    "git", "show",
                    f"{third_implementation}:{AUTHORITY_DESCRIPTOR_PATH}",
                ],
                cwd=third_repository,
                text=True,
                encoding="utf-8",
            )
        )
        third_origin = json.loads(
            (AUTHORITY_ROOT / f"{third_repository.name}.origin.json")
            .read_text(encoding="utf-8")
        )
        self.assertNotEqual(
            second_origin["authority_overlay_sha256"],
            third_origin["authority_overlay_sha256"],
        )
        self.assertEqual(
            third_origin["authority_overlay_sha256"],
            third_descriptor["authority_overlay_sha256"],
        )

    def test_git_and_overlay_source_resolution_fail_closed(self) -> None:
        missing_repository = (
            new_case_dir(self.id(), label="issue43-missing-git-common-dir")
            / "missing"
        )
        with self.assertRaisesRegex(
            AssertionError,
            "cannot resolve shared Git directory",
        ):
            _shared_git_objects_directory(missing_repository)

        root = new_case_dir(self.id(), label="issue43-nondirectory-objects")
        common_dir = root / "common"
        common_dir.mkdir()
        (common_dir / "objects").write_text("not a directory\n", encoding="utf-8")
        completed = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--git-common-dir"],
            returncode=0,
            stdout=str(common_dir),
            stderr="",
        )
        with mock.patch("subprocess.run", return_value=completed):
            with self.assertRaisesRegex(
                AssertionError,
                "shared Git object path is not a directory",
            ):
                _shared_git_objects_directory(PROJECT_ROOT)

        incomplete_overlay = new_case_dir(
            self.id(),
            label="issue43-incomplete-authority-overlay",
        )
        with self.assertRaisesRegex(
            AssertionError,
            "authority overlay path is unavailable",
        ):
            _freeze_authority_overlay(incomplete_overlay)

    def test_authority_fixture_rebuilds_dirty_uncommitted_evidence(self) -> None:
        # scenario_id: dirty_evidence_worktree_rebuild
        # target contradiction: one governed evidence blob differs from HEAD
        # rematerialized nodes: complete authority graph in a new repository
        # expected first gate after repair: no failure
        self._assert_dirty_authority_rebuilt(
            "evidence/global-gate/logs/issue43-global-gate-tests.log"
        )

    def test_authority_fixture_rebuilds_dirty_uncommitted_implementation(self) -> None:
        # scenario_id: dirty_implementation_worktree_rebuild
        # target contradiction: one tracked implementation file differs from HEAD
        # rematerialized nodes: complete authority graph in a new repository
        # expected first gate after repair: no failure
        self._assert_dirty_authority_rebuilt(
            "src/video2pdf_workflow_kernel/global_gate.py"
        )

    def _assert_dirty_authority_rebuilt(self, relative_path: str) -> None:
        root = new_case_dir(
            f"{self.id()}-{relative_path}",
            label="issue43-authority-dirty-worktree",
        )
        repository, _ = build_current_global_gate_authority(root)
        dirty_path = repository / relative_path
        dirty_path.write_text(
            dirty_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

        rebuilt_repository, rebuilt_manifest = build_current_global_gate_authority(root)
        self.assertNotEqual(repository, rebuilt_repository)
        GlobalGatePublisher(project_root=rebuilt_repository).activate(
            control_store_root=root,
            exit_evidence=rebuilt_manifest,
            activated_at="2026-08-03T00:00:00Z",
        )

    def test_negative_result_bindings_require_public_failure_diagnostics(self) -> None:
        manifest = self.manifest()
        negative = next(
            binding
            for binding in manifest["result_bindings"]
            if binding["result_kind"] == "negative"
        )
        self.assertIn("expected_first_failing_gate", negative)
        self.assertIn("expected_error_code", negative)
        damaged = deepcopy(manifest)
        damaged_binding = next(
            binding
            for binding in damaged["result_bindings"]
            if binding["result_kind"] == "negative"
        )
        damaged_binding.pop("expected_error_code")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(damaged))
        self.assertTrue(errors, "a negative result without a stable public error code must be invalid")

    def test_schema_valid_result_tracer_substitution_cannot_activate(self) -> None:
        # scenario_id: qualification_tracer_substitution; the binding remains schema-valid
        # and command-bound, while its registered qualification identity is stale.
        root = new_case_dir(self.id(), label="issue43-tracer-substitution")
        manifest = self.manifest()
        manifest["result_bindings"][0]["test_target"] = manifest["result_bindings"][1]["test_target"]
        manifest_path = root / "exit-evidence-manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(CLI), "global-gate-activate",
             "--control-store-root", str(root), "--exit-evidence", str(manifest_path),
             "--activated-at", "2026-08-03T00:00:00Z"],
            cwd=PROJECT_ROOT, text=True, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        envelope = json.loads(completed.stdout)
        self.assertEqual(envelope["data"]["first_failing_gate"], "qualification_result_binding")
        self.assertEqual(envelope["data"]["error_code"], "global_gate_qualification_contract_stale")

    def test_negative_result_diagnostic_drift_has_a_stable_binding_authority_gate(self) -> None:
        # scenario_id: negative_tracer_diagnostic_drift
        # authority: registered public tracer -> boundary: qualification publication
        # mutation: expected error code only; rematerialized: none; stale: binding authority
        # expected_first_gate: qualification_result_binding
        # expected_error_code: result_binding_authority_stale
        manifest = self.manifest()
        negative = next(
            binding
            for binding in manifest["result_bindings"]
            if binding["result_kind"] == "negative"
        )
        negative["expected_error_code"] = "invented_self_reported_rejection"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("qualification_result_binding", raised.exception.first_failing_gate)
        self.assertEqual("result_binding_authority_stale", raised.exception.error_code)

    def test_historical_slice_7_through_10_evidence_bytes_match_the_cutover_base(self) -> None:
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--exit-code",
                contract.SLICE_BASE_COMMIT,
                "--",
                "evidence/slice-07",
                "evidence/slice-08",
                "evidence/slice-09",
                "evidence/slice-10",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_platform_kernel_activation_is_a_single_contradiction_with_stable_diagnostics(self) -> None:
        # scenario_id: platform_kernel_scope_change
        # authority: Issue #43 activation scope -> boundary: Exit Evidence publication
        # mutation: platform Kernel authority only; rematerialized: none; stale: none
        # expected_first_gate: activation_scope; expected_error_code: platform_kernel_authority_changed
        manifest = self.manifest()
        manifest["activation_scope"]["platform_kernel_authority"] = "active_kernel"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("activation_scope", raised.exception.first_failing_gate)
        self.assertEqual("platform_kernel_authority_changed", raised.exception.error_code)

    def test_inactive_atomic_member_blocks_before_mirror_and_policy_checks(self) -> None:
        # scenario_id: inactive_atomic_member
        # authority: atomic member registry -> boundary: cutover publication
        # mutation: one member status; rematerialized: none; stale: none
        # expected_first_gate: atomic_member_status; expected_error_code: atomic_member_inactive
        manifest = self.manifest()
        manifest["atomic_member_status"]["validators"] = "failed"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("atomic_member_status", raised.exception.first_failing_gate)
        self.assertEqual("atomic_member_inactive", raised.exception.error_code)

    def test_incomplete_mirror_checks_block_before_policy_status(self) -> None:
        # scenario_id: incomplete_mirror_checks
        # authority: registered mirror pairs -> boundary: cutover publication
        # mutation: omit one pair; rematerialized: none; stale: none
        # expected_first_gate: mirror_checks; expected_error_code: incomplete_mirror_checks
        manifest = self.manifest()
        manifest["mirror_checks"].pop()
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("mirror_checks", raised.exception.first_failing_gate)
        self.assertEqual("incomplete_mirror_checks", raised.exception.error_code)

    def test_missing_v1_rejection_is_a_single_coverage_contradiction(self) -> None:
        # scenario_id: missing_v1_rejection
        # authority: closed result set -> boundary: qualification publication
        # mutation: omit v1 result and binding; rematerialized: bindings; stale: none
        # expected_first_gate: qualification_result_coverage; expected_error_code: incomplete_results
        manifest = self.manifest()
        manifest["results"]["negative"].remove("v1_rejected")
        manifest["result_bindings"] = [
            item for item in manifest["result_bindings"] if item["result_id"] != "v1_rejected"
        ]
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("qualification_result_coverage", raised.exception.first_failing_gate)
        self.assertEqual("incomplete_results", raised.exception.error_code)

    def test_failed_atomic_member_precedes_activation_decision(self) -> None:
        # scenario_id: failed_atomic_member
        # authority: closed command vector -> boundary: activation publication
        # mutation: one command exit only; rematerialized: conforms and overall decision; stale: none
        # expected_first_gate: atomic_group; expected_error_code: atomic_member_failed
        manifest = self.manifest()
        manifest["commands"][0]["actual_exit_code"] = 1
        manifest["commands"][0]["conforms"] = False
        manifest["overall_decision"] = "fail"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("atomic_group", raised.exception.first_failing_gate)
        self.assertEqual("atomic_member_failed", raised.exception.error_code)

    def test_unsupported_result_identity_has_a_stable_first_gate(self) -> None:
        # scenario_id: unsupported_result_identity
        # authority: closed result registry -> boundary: qualification publication
        # mutation: add one result and binding identity; rematerialized: bindings; stale: none
        # expected_first_gate: qualification_result_coverage; expected_error_code: unsupported_result_identity
        manifest = self.manifest()
        manifest["results"]["negative"].append("acceptance_v3_rejected")
        extra_binding = deepcopy(manifest["result_bindings"][0])
        extra_binding["result_id"] = "acceptance_v3_rejected"
        extra_binding["result_kind"] = "negative"
        manifest["result_bindings"].append(extra_binding)
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("qualification_result_coverage", raised.exception.first_failing_gate)
        self.assertEqual("unsupported_result_identity", raised.exception.error_code)

    def test_contract_gap_blocks_before_activation_decision(self) -> None:
        # scenario_id: unresolved_contract_gap
        # authority: exception ledger -> boundary: activation publication
        # mutation: one blocking Contract Gap; rematerialized: overall decision; stale: none
        # expected_first_gate: contract_gap; expected_error_code: unresolved_contract_gap
        manifest = self.manifest()
        manifest["unresolved_exceptions"] = [
            {"blocking": True, "code": "contract_gap", "message": "unsupported identity"}
        ]
        manifest["overall_decision"] = "fail"
        with self.assertRaises(validator.EvidenceError) as raised:
            validator.validate_semantics(manifest)
        self.assertEqual("contract_gap", raised.exception.first_failing_gate)
        self.assertEqual("unresolved_contract_gap", raised.exception.error_code)

    def test_collector_materializes_the_registered_schema_valid_cutover_shape(self) -> None:
        root = new_case_dir(self.id(), label="issue43-exit-evidence-collector")
        evidence_dir = root / "evidence/global-gate"
        logs = evidence_dir / "logs"
        manifest_path = evidence_dir / "exit-evidence-manifest.json"
        sha256 = "1" * 64
        commands = deepcopy(self.manifest()["commands"])
        collection_path = root / "collection.json"
        collection_path.write_text(
            json.dumps({
                "schema_name": "issue43-exit-evidence-collection",
                "schema_version": "1.0.0",
                "implementation_commit": "2" * 40,
                "runs": [],
            }) + "\n",
            encoding="utf-8",
        )

        def fake_git(*arguments: str) -> str:
            if arguments[:2] == ("status", "--porcelain=v1"):
                return ""
            if arguments == ("rev-parse", "HEAD"):
                return "2" * 40
            return ""

        with (
            mock.patch.object(collector, "EVIDENCE_DIR", evidence_dir),
            mock.patch.object(collector, "LOG_DIR", logs),
            mock.patch.object(collector, "MANIFEST_PATH", manifest_path),
            mock.patch.object(collector, "git", side_effect=fake_git),
            mock.patch.object(collector, "sha256_git_blob", return_value=sha256),
            mock.patch.object(collector, "preserve_previous_evidence"),
            mock.patch.object(collector, "require_collection_terminal"),
            mock.patch.object(collector, "finalize_commands", return_value=commands),
            mock.patch.object(
                collector,
                "fingerprint_implementation_changes",
                return_value=[
                    {
                        "role": "implementation_artifact",
                        "path": "scripts/issue43_exit_evidence_contract.py",
                        "sha256": sha256,
                    }
                ],
            ),
            mock.patch.object(
                collector, "implementation_change_tombstones", return_value=[]
            ),
        ):
            self.assertEqual(collector.finalize(collection_path), 0)

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(json.loads(manifest_path.read_text(encoding="utf-8")))
        activated = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-B", str(CLI),
                "global-gate-activate", "--control-store-root", str(root),
                "--exit-evidence", str(manifest_path),
                "--activated-at", "2026-08-03T00:00:00Z",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(activated.returncode, 0)
        envelope = json.loads(activated.stdout)
        self.assertEqual(envelope["data"]["first_failing_gate"], "implementation_lineage")
        self.assertEqual(envelope["data"]["error_code"], "implementation_commit_invalid")

    def _repo_git(self, repository: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            self.fail(
                f"git {' '.join(arguments)} failed: {completed.stdout}{completed.stderr}"
            )
        return completed.stdout.strip()

    def _init_finalize_repository(
        self,
        label: str,
        *,
        autocrlf: str = "false",
        fixture_bytes: bytes = b"specimen line one\nspecimen line two\n",
    ) -> tuple[Path, Path]:
        case = new_case_dir(self.id(), label=label)
        repository = case / "repository"
        repository.mkdir()
        self._repo_git(repository, "init")
        self._repo_git(repository, "config", "user.name", "Issue43 Collector Test")
        self._repo_git(
            repository, "config", "user.email", "issue43-collector@example.invalid"
        )
        self._repo_git(repository, "config", "core.autocrlf", autocrlf)
        (repository / ".gitignore").write_text("runs/\n", encoding="utf-8")
        fixture = repository / "fixtures/specimen.txt"
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes(fixture_bytes)
        self._repo_git(repository, "add", "-A")
        self._repo_git(repository, "commit", "-m", "Materialize implementation authority")
        return case, repository

    def _run_finalize(
        self, case: Path, repository: Path, collection: dict, collection_name: str
    ) -> dict:
        collection["implementation_commit"] = self._repo_git(
            repository, "rev-parse", "HEAD"
        )
        collection_path = case / collection_name
        collection_path.write_text(
            json.dumps(collection) + "\n", encoding="utf-8"
        )
        evidence_dir = repository / "evidence/global-gate"
        original_git = collector.git

        def git_proxy(*arguments: str) -> str:
            if arguments[:2] == ("merge-base", "--is-ancestor"):
                return ""
            return original_git(*arguments)

        with (
            mock.patch.object(collector, "PROJECT_ROOT", repository),
            mock.patch.object(collector, "EVIDENCE_DIR", evidence_dir),
            mock.patch.object(collector, "LOG_DIR", evidence_dir / "logs"),
            mock.patch.object(
                collector,
                "MANIFEST_PATH",
                evidence_dir / "exit-evidence-manifest.json",
            ),
            mock.patch.object(collector, "REFRESH_ROOT", case / "refresh"),
            mock.patch.object(collector, "MIRROR_SPECS", ()),
            mock.patch.object(
                collector,
                "FIXTURE_SPECS",
                (("fixture_specimen", "fixtures/specimen.txt"),),
            ),
            mock.patch.object(collector, "git", side_effect=git_proxy),
            mock.patch.object(
                collector, "fingerprint_implementation_changes", return_value=[]
            ),
            mock.patch.object(
                collector, "implementation_change_tombstones", return_value=[]
            ),
        ):
            self.assertEqual(0, collector.finalize(collection_path))
        return json.loads(
            (evidence_dir / "exit-evidence-manifest.json").read_text(encoding="utf-8")
        )

    def _publish_and_diff(self, repository: Path) -> set[str]:
        self._repo_git(repository, "add", "-f", "evidence/global-gate")
        self._repo_git(
            repository, "commit", "-m", "Publish test Global Gate evidence"
        )
        return set(
            filter(
                None,
                self._repo_git(
                    repository,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ).splitlines(),
            )
        )

    def test_finalize_first_publication_lists_the_complete_evidence_set(self) -> None:
        # scenario_id: first_publication_complete_evidence_set
        # authority input: implementation HEAD without any prior evidence generation
        # derived node: manifest evidence_paths
        # boundary: publication commit; the validator's historical_evidence
        #   gate requires diff-tree paths to stay within evidence_paths while
        #   every declared path resolves as a regular publication-tree blob
        # mutation: none; regression guard for the full 21-path first publication
        # expected first gate after repair: no failure; publication paths are exact
        case, repository = self._init_finalize_repository("issue43-first-publication")
        collection, _, _ = self._synthetic_terminal_collection(repository)
        manifest = self._run_finalize(case, repository, collection, "collection.json")
        expected = {
            "evidence/global-gate/exit-evidence-manifest.json",
            *(
                f"evidence/global-gate/logs/{command_id}.log"
                for command_id, _, _ in contract.COMMANDS
            ),
            *(
                f"evidence/global-gate/persisted/{command_id}/{filename}"
                for command_id, _, _ in contract.COMMANDS
                for filename in ("command.json", "status.json", "exit-code.txt")
            ),
        }
        self.assertEqual(21, len(expected))
        actual = set(manifest["evidence_paths"])
        self.assertEqual(expected, actual)
        self.assertEqual(expected, self._publish_and_diff(repository))

    def _stage_second_generation(self, root: Path) -> tuple[Path, Path]:
        """Advance a dedicated authority fixture to a second evidence generation.

        Rotated run identities change every log and persisted artifact except
        the five immutable ``"0\\n"`` exit-code blobs, which the second
        publication tree inherits unchanged from the first generation. The
        staged publication is left uncommitted so tests can mutate it.
        """
        repository, _ = build_current_global_gate_authority(root)
        commit_later_implementation_change(repository)
        implementation = self._repo_git(repository, "rev-parse", "HEAD")
        manifest_path = (
            repository / "evidence/global-gate/exit-evidence-manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["implementation_commit"] = implementation
        for index, command in enumerate(manifest["commands"], 1):
            log_path = repository / command["log"]["path"]
            log_path.write_bytes(
                (
                    f"EVIDENCE_IMPLEMENTATION_COMMIT: {implementation}\n"
                    f"qualified command: {command['test_id']}\n"
                ).encode("utf-8")
            )
            command["log"]["sha256"] = hashlib.sha256(
                log_path.read_bytes()
            ).hexdigest()
            persisted = command["persisted_run"]
            run_id = f"22222222-2222-4222-8222-{index:012d}"
            persisted["run_id"] = run_id
            for key in ("command_record", "terminal_status"):
                artifact = persisted[key]
                artifact_path = repository / artifact["path"]
                record = json.loads(artifact_path.read_text(encoding="utf-8"))
                record["run_id"] = run_id
                artifact_path.write_bytes(
                    (
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                artifact["sha256"] = hashlib.sha256(
                    artifact_path.read_bytes()
                ).hexdigest()
        for fixture in manifest["fixtures"]:
            fixture["sha256"] = sha256_git_blob(
                repository, implementation, fixture["path"]
            )
        manifest["artifact_fingerprints"] = fingerprint_implementation_changes(
            repository,
            SLICE_BASE_COMMIT,
            implementation,
            excluded_prefixes=(EVIDENCE_PREFIX,),
        )
        manifest_path.write_bytes(
            (
                json.dumps(
                    manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                + "\n"
            ).encode("utf-8")
        )
        self._repo_git(repository, "add", "-f", "evidence/global-gate")
        return repository, manifest_path

    def _commit_publication(self, repository: Path) -> set[str]:
        self._repo_git(
            repository, "commit", "-m", "Republish test Global Gate evidence"
        )
        return set(
            filter(
                None,
                self._repo_git(
                    repository,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ).splitlines(),
            )
        )

    def test_validator_accepts_republication_inheriting_byte_identical_blobs(self) -> None:
        # scenario_id: republication_inherits_byte_identical_exit_codes
        # authority input: a first-generation committed evidence closure
        # derived nodes: second implementation commit, rotated run identities
        # boundary: second publication commit; the validator's
        #   historical_evidence gate requires diff-tree paths to stay within
        #   evidence_paths while every declared path resolves as a regular
        #   publication-tree blob bound to its manifest fingerprint
        # target contradiction: the five byte-identical exit-code blobs can
        #   never appear in a second publication diff
        # rematerialized nodes: none; the publication tree inherits them
        # expected first gate after repair: no failure
        root = new_case_dir(self.id(), label="issue43-republication")
        repository, manifest_path = self._stage_second_generation(root)
        publication_paths = self._commit_publication(repository)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        evidence_paths = set(manifest["evidence_paths"])
        exit_code_paths = {
            f"evidence/global-gate/persisted/{command_id}/exit-code.txt"
            for command_id, _, _ in contract.COMMANDS
        }
        self.assertEqual(5, len(exit_code_paths))
        self.assertTrue(exit_code_paths <= evidence_paths)
        self.assertTrue(exit_code_paths.isdisjoint(publication_paths))
        self.assertEqual(evidence_paths - exit_code_paths, publication_paths)
        validated = validate_global_gate_exit_evidence(
            manifest_path, project_root=repository
        )
        self.assertEqual(
            manifest["implementation_commit"],
            validated.value["implementation_commit"],
        )

    def test_validator_accepts_the_complete_first_publication(self) -> None:
        # scenario_id: first_publication_real_validator_pass
        # authority input: the complete first-generation evidence closure
        # boundary: publication commit == HEAD == direct implementation child
        # expected first gate after repair: no failure
        root = new_case_dir(
            self.id(), label="issue43-first-publication-validation"
        )
        repository, manifest_path = build_current_global_gate_authority(root)
        validated = validate_global_gate_exit_evidence(
            manifest_path, project_root=repository
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["implementation_commit"],
            validated.value["implementation_commit"],
        )

    def test_validator_rejects_smuggled_publication_paths(self) -> None:
        # scenario_id: smuggled_publication_paths
        # mutation: one evidence-prefix and one non-evidence path smuggled
        #   into the second publication commit
        # expected_first_gate: historical_evidence
        # expected_error_code: historical_evidence_paths_stale
        root = new_case_dir(self.id(), label="issue43-smuggled-publication")
        repository, manifest_path = self._stage_second_generation(root)
        smuggled = (
            "evidence/global-gate/smuggled-evidence.log",
            "src/smuggled_non_evidence.py",
        )
        (repository / smuggled[0]).write_text(
            "smuggled evidence\n", encoding="utf-8"
        )
        (repository / smuggled[1]).write_text(
            "SMUGGLED = True\n", encoding="utf-8"
        )
        self._repo_git(repository, "add", "-f", *smuggled)
        publication_paths = self._commit_publication(repository)
        self.assertTrue(set(smuggled) <= publication_paths)
        with self.assertRaises(ExitEvidenceValidationError) as raised:
            validate_global_gate_exit_evidence(
                manifest_path, project_root=repository
            )
        self.assertEqual(
            "historical_evidence", raised.exception.first_failing_gate
        )
        self.assertEqual(
            "historical_evidence_paths_stale", raised.exception.error_code
        )

    def test_validator_rejects_a_listed_path_never_published(self) -> None:
        # scenario_id: listed_evidence_path_unpublished
        # mutation: one declared exit-code artifact exists on disk with a
        #   matching fingerprint but was never added to any commit
        # expected_first_gate: historical_evidence
        # expected_error_code: historical_evidence_path_unpublished
        root = new_case_dir(self.id(), label="issue43-unpublished-evidence")
        repository, manifest_path = build_current_global_gate_authority(root)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        missing = (
            f"evidence/global-gate/persisted/"
            f"{contract.COMMANDS[0][0]}/exit-code.txt"
        )
        self._repo_git(
            repository, "reset", "--soft", manifest["implementation_commit"]
        )
        self._repo_git(repository, "rm", "--cached", "--", missing)
        publication_paths = self._commit_publication(repository)
        self.assertNotIn(missing, publication_paths)
        self.assertTrue((repository / missing).is_file())
        with self.assertRaises(ExitEvidenceValidationError) as raised:
            validate_global_gate_exit_evidence(
                manifest_path, project_root=repository
            )
        self.assertEqual(
            "historical_evidence", raised.exception.first_failing_gate
        )
        self.assertEqual(
            "historical_evidence_path_unpublished", raised.exception.error_code
        )

    def test_validator_rejects_a_listed_path_deleted_in_publication(self) -> None:
        # scenario_id: listed_evidence_path_deleted_in_publication
        # mutation: one declared log stays on disk with a matching
        #   fingerprint but the second publication deletes it from the tree
        # expected_first_gate: historical_evidence
        # expected_error_code: historical_evidence_paths_stale
        root = new_case_dir(self.id(), label="issue43-deleted-evidence")
        repository, manifest_path = self._stage_second_generation(root)
        deleted = f"evidence/global-gate/logs/{contract.COMMANDS[0][0]}.log"
        self._repo_git(repository, "rm", "--cached", "--", deleted)
        publication_paths = self._commit_publication(repository)
        self.assertIn(deleted, publication_paths)
        self.assertTrue((repository / deleted).is_file())
        with self.assertRaises(ExitEvidenceValidationError) as raised:
            validate_global_gate_exit_evidence(
                manifest_path, project_root=repository
            )
        self.assertEqual(
            "historical_evidence", raised.exception.first_failing_gate
        )
        self.assertEqual(
            "historical_evidence_paths_stale", raised.exception.error_code
        )

    def test_validator_rejects_blob_bytes_differing_from_declared_fingerprint(self) -> None:
        # scenario_id: publication_blob_bytes_differ_from_fingerprint
        # authority input: a second-generation staged publication
        # mutation: one persisted artifact is committed with an extra byte
        #   while the on-disk copy keeps the manifest-declared bytes, so the
        #   disk binding gates pass and only the committed blob differs
        # expected_first_gate: historical_evidence
        # expected_error_code: historical_evidence_paths_stale
        root = new_case_dir(self.id(), label="issue43-blob-byte-binding")
        repository, manifest_path = self._stage_second_generation(root)
        target = (
            f"evidence/global-gate/persisted/"
            f"{contract.COMMANDS[0][0]}/status.json"
        )
        declared_bytes = (repository / target).read_bytes()
        (repository / target).write_bytes(declared_bytes + b"\n")
        self._repo_git(repository, "add", "-f", "--", target)
        (repository / target).write_bytes(declared_bytes)
        publication_paths = self._commit_publication(repository)
        self.assertIn(target, publication_paths)
        with self.assertRaises(ExitEvidenceValidationError) as raised:
            validate_global_gate_exit_evidence(
                manifest_path, project_root=repository
            )
        self.assertEqual(
            "historical_evidence", raised.exception.first_failing_gate
        )
        self.assertEqual(
            "historical_evidence_paths_stale", raised.exception.error_code
        )

    def test_validator_rejects_a_symlink_at_a_declared_path(self) -> None:
        # scenario_id: symlink_at_declared_evidence_path
        # mutation: one declared persisted artifact enters the publication
        #   commit as a symlink (mode 120000) staged directly in the index;
        #   the on-disk copy keeps the manifest-declared regular bytes
        # expected_first_gate: historical_evidence
        # expected_error_code: historical_evidence_paths_stale
        root = new_case_dir(self.id(), label="issue43-symlink-evidence")
        repository, manifest_path = self._stage_second_generation(root)
        target = (
            f"evidence/global-gate/persisted/"
            f"{contract.COMMANDS[0][0]}/exit-code.txt"
        )
        blob_sha = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repository,
            input=b"0\n",
            capture_output=True,
            check=True,
        ).stdout.decode("ascii").strip()
        self._repo_git(
            repository,
            "update-index",
            "--add",
            "--cacheinfo",
            f"120000,{blob_sha},{target}",
        )
        publication_paths = self._commit_publication(repository)
        self.assertIn(target, publication_paths)
        with self.assertRaises(ExitEvidenceValidationError) as raised:
            validate_global_gate_exit_evidence(
                manifest_path, project_root=repository
            )
        self.assertEqual(
            "historical_evidence", raised.exception.first_failing_gate
        )
        self.assertEqual(
            "historical_evidence_paths_stale", raised.exception.error_code
        )

    def test_finalize_fingerprints_fixtures_from_git_blob_bytes(self) -> None:
        # scenario_id: fixture_fingerprint_blob_byte_authority
        # authority input: fixture git blob bytes at implementation_commit
        # derived node: manifest fixtures[].sha256
        # boundary: clean-tree CRLF disk drift under core.autocrlf=true; the
        #   validator fixture_fingerprint gate hashes blob bytes via
        #   evidence.sha256_git_blob
        # target contradiction: raw on-disk fixture bytes hashed instead of
        #   blob bytes (fixture_sha256_stale on CRLF hosts)
        # rematerialized nodes: fixture fingerprint from the git blob
        # expected first gate after repair: fixture_fingerprint passes
        case, repository = self._init_finalize_repository(
            "issue43-fixture-blob-fingerprint",
            autocrlf="true",
            # Committed from CRLF disk bytes under core.autocrlf=true: the blob
            # keeps LF bytes while the clean worktree file carries CRLF bytes,
            # exactly the drift state of the failing Windows host.
            fixture_bytes=b"specimen line one\r\nspecimen line two\r\n",
        )
        fixture = repository / "fixtures/specimen.txt"
        self.assertEqual(
            "",
            self._repo_git(
                repository, "status", "--porcelain=v1", "--untracked-files=all"
            ),
        )
        head = self._repo_git(repository, "rev-parse", "HEAD")
        blob_bytes = subprocess.run(
            ["git", "cat-file", "blob", f"{head}:fixtures/specimen.txt"],
            cwd=repository,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(b"specimen line one\nspecimen line two\n", blob_bytes)
        self.assertNotEqual(blob_bytes, fixture.read_bytes())

        collection, _, _ = self._synthetic_terminal_collection(repository)
        manifest = self._run_finalize(case, repository, collection, "collection.json")
        entry = next(
            item
            for item in manifest["fixtures"]
            if item["path"] == "fixtures/specimen.txt"
        )
        # Mirror of the validator gate: evidence.sha256_git_blob at
        # implementation_commit must equal the recorded fingerprint.
        self.assertEqual(
            sha256_git_blob(repository, head, "fixtures/specimen.txt"),
            entry["sha256"],
        )
        self.assertEqual(hashlib.sha256(blob_bytes).hexdigest(), entry["sha256"])
        self.assertNotEqual(
            hashlib.sha256(fixture.read_bytes()).hexdigest(), entry["sha256"]
        )


if __name__ == "__main__":
    unittest.main()

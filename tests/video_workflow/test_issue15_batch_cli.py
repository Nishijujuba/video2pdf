from __future__ import annotations

import json
from pathlib import Path
import hashlib
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow._test_run import new_workflow_workspace
from video2pdf_workflow_kernel import cli as kernel_cli
from video2pdf_workflow_kernel.batch_authority import BatchCutoverPublisher
from video2pdf_workflow_kernel.batch_projection import BatchProjectionProvider
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore
from video2pdf_workflow_kernel.cutover_retirement import project_maintenance_fence
from video2pdf_workflow_kernel.errors import KernelError


def _write_current_global_gate(control_root: Path) -> None:
    authority_path = control_root / "active_global_gate.json"
    authority_path.write_bytes(
        (PROJECT_ROOT / "workspace" / "active_global_gate.json").read_bytes()
    )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority_file_sha = hashlib.sha256(authority_path.read_bytes()).hexdigest()
    with sqlite3.connect(control_root / "global-gate-control.sqlite3") as connection:
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "CREATE TABLE gate_authority (singleton INTEGER PRIMARY KEY, generation INTEGER NOT NULL, evidence_sha256 TEXT NOT NULL, authority_sha256 TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE gate_intents (intent_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO gate_authority VALUES (1, ?, ?, ?)",
            (
                authority["generation"],
                authority["exit_evidence_sha256"],
                authority_file_sha,
            ),
        )


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    command = arguments[0] if arguments else "unknown"
    with patch.object(
        BatchProjectionProvider,
        "_enumerate_items",
        return_value=[
            {
                "item_index": 1,
                "part_id": "p1",
                "canonical_item_id": "BV1xx411c7mD:p1",
                "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
                "title": "Part One",
            },
            {
                "item_index": 2,
                "part_id": "p2",
                "canonical_item_id": "BV1xx411c7mD:p2",
                "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/?p=2",
                "title": "Part Two",
            },
        ],
    ):
        try:
            parsed = kernel_cli._parser().parse_args(list(arguments))
            envelope = kernel_cli._execute(parsed, PROJECT_ROOT)
            returncode = 0
        except KernelError as exc:
            envelope = kernel_cli._error(command, exc)
            returncode = exc.exit_code
    return subprocess.CompletedProcess(
        list(arguments),
        returncode,
        json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n",
        "",
    )


def _write_batch_project(case_root: Path) -> tuple[Path, Path, Path, Path]:
    project_root = case_root / "project"
    config_root = project_root / "config"
    output_root = project_root / "workspace"
    control_root = project_root / "control-store"
    config_root.mkdir(parents=True)
    profile_path = config_root / "workflow-release-profile.v1.json"
    profile_path.write_bytes(
        (PROJECT_ROOT / "config" / "workflow-release-profile.v1.json").read_bytes()
    )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    control_root.mkdir(parents=True)
    _write_current_global_gate(control_root)
    history = control_root / ".workflow-release-history"
    history.mkdir()
    (history / "cutover-authority-tombstone.json").write_text(
        json.dumps(
            {
                "state": "RETIRED",
                "release_id": profile["release_id"],
                "contract_compatibility": profile["contract_compatibility"],
                "profile_path": str(profile_path.resolve()),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (config_root / "workflow-admission-activation.v1.json").write_text(
        json.dumps(
            {
                "schema_name": "workflow-admission-activation",
                "schema_version": "1.0.0",
                "activation_status": "active_profile_admission",
                "release_id": profile["release_id"],
                "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                "generation": 1,
                "activated_at": "2026-08-27T00:00:00Z",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    project_config = config_root / "workflow-project.v1.json"
    project_config.write_text(
        json.dumps(
            {
                "schema_name": "workflow-project-config",
                "schema_version": "1.0.0",
                "workspace_root": "workspace",
                "control_store_root": "control-store",
                "release_profile": "config/workflow-release-profile.v1.json",
                "ordinary_run_platforms": ["bilibili", "youtube"],
                "existing_directory_policy": "explicit_legacy_maintenance_only",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return project_config, output_root, control_root, profile_path


class Issue15BatchCliTests(unittest.TestCase):
    def test_batch_commands_are_registered(self) -> None:
        choices = kernel_cli._parser()._subparsers._group_actions[0].choices
        for command in (
            "batch-plan",
            "batch-run",
            "batch-recover",
            "batch-rebuild-projections",
            "batch-status",
        ):
            with self.subTest(command=command):
                self.assertIn(command, choices)

    def test_batch_plan_returns_envelope(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-plan")
        project_config, output_root, control_root, _profile = _write_batch_project(
            workspace
        )
        arguments = (
            "batch-plan",
            "--project-config",
            str(project_config),
            "--platform",
            "bilibili",
            "--source-url",
            "https://www.bilibili.com/video/BV1xx411c7mD/",
            "--task-start",
            "2026-08-16T09:05:00+08:00",
            "--request-id",
            "cli-plan-request",
        )
        with patch.object(
            BatchCutoverPublisher,
            "require_current",
            side_effect=AssertionError("ordinary planning read retired Batch authority"),
        ):
            completed = _run_cli(*arguments)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        envelope = json.loads(completed.stdout)
        self.assertEqual(envelope["classification"], "batch_planned")
        self.assertEqual(envelope["command"], "batch-plan")
        self.assertEqual(
            envelope["data"]["admission_authority"], "workflow_release_profile"
        )
        self.assertEqual(envelope["data"]["release_id"], "workflow-2.0")
        self.assertIn("batch_id", envelope["data"])
        self.assertIn("batch_dir", envelope["data"])
        self.assertEqual(len(envelope["data"]["item_order"]), 2)
        record = ControlStore(
            control_root, ContractRegistry(PROJECT_ROOT)
        ).get_batch_record(envelope["data"]["batch_id"])
        self.assertIsNotNone(record)
        self.assertEqual(Path(record["output_root"]), output_root)
        self.assertTrue(Path(record["batch_dir"]).is_relative_to(output_root))
        self.assertEqual(
            record["batch_authority_binding"]["authority_kind"],
            "workflow_release_profile",
        )
        self.assertEqual(
            Path(record["batch_authority_binding"]["profile_path"]),
            _profile.resolve(),
        )
        self.assertEqual(
            record["batch_authority_binding"]["release_id"], "workflow-2.0"
        )
        self.assertEqual(record["run_mappings"], [])
        self.assertEqual(record["projections"], [])
        self.assertEqual(list(output_root.rglob("workflow/run.json")), [])

        replayed = _run_cli(*arguments)
        replay_envelope = json.loads(replayed.stdout)
        replay_record = ControlStore(
            control_root, ContractRegistry(PROJECT_ROOT)
        ).get_batch_record(envelope["data"]["batch_id"])
        self.assertEqual(replayed.returncode, 0, replayed.stdout)
        self.assertEqual(replay_envelope["data"]["created_or_replayed"], "REPLAY")
        self.assertEqual(replay_record["item_order"], record["item_order"])
        self.assertEqual(
            len(
                ControlStore(
                    control_root, ContractRegistry(PROJECT_ROOT)
                ).list_batch_records()
            ),
            1,
        )
        with patch.object(
            BatchCutoverPublisher,
            "require_current",
            side_effect=AssertionError("Profile-backed status read retired Batch authority"),
        ):
            status = _run_cli(
                "batch-status",
                "--control-store-root",
                str(control_root),
                "--batch-id",
                envelope["data"]["batch_id"],
            )
        self.assertEqual(status.returncode, 0, status.stdout)
        self.assertEqual(
            json.loads(status.stdout)["classification"], "batch_status_reported"
        )

    def test_batch_plan_maintenance_fence_fails_before_record(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-profile-closed")
        project_config, output_root, control_root, _profile = _write_batch_project(
            workspace
        )

        # scenario_id: coordinated_activation_fence
        # target_invariant: Batch planning cannot enter during release activation
        # mutation_seam: exclusive project maintenance fence
        # rematerialized_nodes: none
        # intentionally_stale_nodes: none
        # expected_first_gate: project_maintenance_fence
        # expected_error_code: project_maintenance_in_progress
        # scenario_class: single_contradiction
        with project_maintenance_fence(control_root):
            completed = _run_cli(
                "batch-plan",
                "--project-config",
                str(project_config),
                "--platform",
                "bilibili",
                "--source-url",
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                "--task-start",
                "2026-08-16T09:05:00+08:00",
                "--request-id",
                "activation-fenced-batch",
            )
        envelope = json.loads(completed.stdout)
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(
            envelope["data"]["first_failing_gate"], "project_maintenance_fence"
        )
        self.assertEqual(
            envelope["data"]["error_code"],
            "project_maintenance_in_progress",
        )
        self.assertFalse(output_root.exists())
        self.assertFalse((control_root / ".workflow-control/control.sqlite3").exists())
        self.assertEqual(list(workspace.rglob("batch-record.json")), [])

    def test_batch_run_delegates_start_binding_to_provider(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-run-start")
        output_root = workspace / "outputs"
        control_root = workspace / "control"
        output_root.mkdir()
        control_root.mkdir()
        contracts = ContractRegistry(PROJECT_ROOT)
        store = ControlStore.initialize(control_root, contracts)
        record = {
            "schema_name": "batch-record",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "batch_id": "a" * 32,
            "batch_identity": {
                "kind": "url_set",
                "canonical_platform": "bilibili",
                "batch_source_identity": "b" * 64,
                "source_url": "https://www.bilibili.com/video/BV1xx411c7mD/",
                "original_title": "Batch",
                "task_start": "2026-08-16T09:05:00+08:00",
                "request_id": "cli-run",
            },
            "output_root": str(output_root),
            "batch_dir": str(output_root / "batch" / "batch-control"),
            "control_dir": str(control_root / ".workflow-control" / "batches"),
            "batch_stage": "planned",
            "batch_authority_binding": None,
            "run_task_start": None,
            "item_order": [
                {
                    "item_index": 1,
                    "part_id": "p1",
                    "canonical_item_id": "BV1xx411c7mD:p1",
                    "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
                    "title": "Part One",
                    "selected": True,
                }
            ],
            "run_mappings": [],
            "projections": [],
            "created_at": "2026-08-16T09:05:00+08:00",
            "updated_at": "2026-08-16T09:05:00+08:00",
        }
        store.create_batch_record(record, record["batch_identity"])
        argv = [
            "batch-run",
            "--batch-id",
            record["batch_id"],
            "--control-store-root",
            str(control_root),
            "--session-id",
            "session-cli-run",
        ]
        with patch.object(
            BatchProjectionProvider,
            "run",
            return_value={"batch_id": record["batch_id"], "items": []},
        ) as run:
            kernel_cli._execute(kernel_cli._parser().parse_args(argv), PROJECT_ROOT)
        self.assertEqual(run.call_args.kwargs["workspace_root"], output_root)
        self.assertEqual(run.call_args.kwargs["control_store_root"], control_root)
        self.assertIsNotNone(run.call_args.kwargs["run_task_start"])
        self.assertIsNone(
            ControlStore(control_root, contracts)
            .get_batch_record(record["batch_id"])["run_task_start"]
        )

    def test_batch_run_accepts_claim_before_mapping_fault_point(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-run-profile")
        project_config, _output_root, control_root, _profile = _write_batch_project(
            workspace
        )
        planned = _run_cli(
            "batch-plan",
            "--project-config",
            str(project_config),
            "--platform",
            "bilibili",
            "--source-url",
            "https://www.bilibili.com/video/BV1xx411c7mD/",
            "--task-start",
            "2026-08-16T09:05:00+08:00",
            "--request-id",
            "cli-profile-run",
        )
        self.assertEqual(planned.returncode, 0, planned.stdout)
        batch_id = json.loads(planned.stdout)["data"]["batch_id"]
        with patch.object(
            BatchCutoverPublisher,
            "require_current",
            side_effect=AssertionError("Profile-backed run read retired Batch authority"),
        ):
            interrupted = _run_cli(
                "batch-run",
                "--batch-id",
                batch_id,
                "--control-store-root",
                str(control_root),
                "--session-id",
                "session-cli-fault",
                "--fault-point",
                "after_first_task_claim_before_mapping_commit",
            )
        self.assertNotEqual(interrupted.returncode, 0, interrupted.stdout)
        self.assertEqual(
            json.loads(interrupted.stdout)["classification"],
            "injected_initialization_fault",
        )

    def test_batch_plan_requires_task_start(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-plan-missing")
        project_config, _output_root, _control_root, _profile = _write_batch_project(
            workspace
        )
        completed = _run_cli(
            "batch-plan",
            "--project-config",
            str(project_config),
            "--platform",
            "bilibili",
            "--source-url",
            "https://www.bilibili.com/video/BV1xx411c7mD/",
            "--request-id",
            "cli-plan-request",
        )
        self.assertNotEqual(completed.returncode, 0)
        envelope = json.loads(completed.stdout)
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["classification"], "usage_error")

    def test_batch_status_unknown_batch_fails(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-status-unknown")
        completed = _run_cli(
            "batch-status",
            "--control-store-root",
            str(workspace),
            "--batch-id",
            "f" * 32,
        )
        self.assertNotEqual(completed.returncode, 0)
        envelope = json.loads(completed.stdout)
        self.assertEqual(envelope["status"], "error")
        self.assertIn("message", envelope["data"])

    def test_batch_recover_unknown_batch_fails(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-recover-unknown")
        completed = _run_cli(
            "batch-recover",
            "--batch-id",
            "f" * 32,
            "--control-store-root",
            str(workspace),
        )
        self.assertNotEqual(completed.returncode, 0)
        envelope = json.loads(completed.stdout)
        self.assertEqual(envelope["status"], "error")

    def test_recover_rebuild_and_status_derive_output_root_from_batch_record(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-read-record")
        output_root = workspace / "outputs"
        control_root = workspace / "control"
        output_root.mkdir()
        control_root.mkdir()
        contracts = ContractRegistry(PROJECT_ROOT)
        record = {
            "schema_name": "batch-record",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "batch_id": "b" * 32,
            "batch_identity": {
                "kind": "url_set",
                "canonical_platform": "bilibili",
                "batch_source_identity": "c" * 64,
                "source_url": "https://www.bilibili.com/video/BV1xx411c7mD/",
                "original_title": "Batch",
                "task_start": "2026-08-16T09:05:00+08:00",
                "request_id": "cli-record-roots",
            },
            "output_root": str(output_root),
            "batch_dir": str(output_root / "batch" / "batch-control"),
            "control_dir": str(control_root / ".workflow-control" / "batches"),
            "batch_stage": "planned",
            "batch_authority_binding": None,
            "run_task_start": None,
            "item_order": [
                {
                    "item_index": 1,
                    "part_id": "p1",
                    "canonical_item_id": "BV1xx411c7mD:p1",
                    "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD/?p=1",
                    "title": "Part One",
                    "selected": True,
                }
            ],
            "run_mappings": [],
            "projections": [],
            "created_at": "2026-08-16T09:05:00+08:00",
            "updated_at": "2026-08-16T09:05:00+08:00",
        }
        ControlStore.initialize(control_root, contracts).create_batch_record(
            record, record["batch_identity"]
        )
        cases = (
            ("batch-recover", "recover", {"batch_id": record["batch_id"]}),
            ("batch-rebuild-projections", "rebuild_projections", []),
            ("batch-status", "status", {"batch_id": record["batch_id"]}),
        )
        for command, method_name, result in cases:
            with self.subTest(command=command), patch.object(
                BatchProjectionProvider, method_name, return_value=result
            ) as method:
                args = kernel_cli._parser().parse_args(
                    [
                        command,
                        "--batch-id",
                        record["batch_id"],
                        "--control-store-root",
                        str(control_root),
                    ]
                )
                kernel_cli._execute(args, PROJECT_ROOT)
                self.assertEqual(method.call_args.kwargs["workspace_root"], output_root)
                self.assertEqual(
                    method.call_args.kwargs["control_store_root"], control_root
                )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
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
from video2pdf_workflow_kernel.batch_projection import BatchProjectionProvider
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.control_store import ControlStore
from video2pdf_workflow_kernel.errors import KernelError
from video2pdf_workflow_kernel.platform_kernel import BilibiliPlatformCutoverPublisher


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
        completed = _run_cli(
            "batch-plan",
            "--workspace-root",
            str(workspace),
            "--platform",
            "bilibili",
            "--source-url",
            "https://www.bilibili.com/video/BV1xx411c7mD/",
            "--task-start",
            "2026-08-16T09:05:00+08:00",
            "--request-id",
            "cli-plan-request",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        envelope = json.loads(completed.stdout)
        self.assertEqual(envelope["classification"], "batch_planned")
        self.assertEqual(envelope["command"], "batch-plan")
        self.assertIn("batch_id", envelope["data"])
        self.assertIn("batch_dir", envelope["data"])
        self.assertEqual(len(envelope["data"]["item_order"]), 2)

    def test_batch_plan_workspace_defaults_to_project_root(self) -> None:
        result = {
            "batch_id": "a" * 32,
            "batch_dir": str(PROJECT_ROOT / "batch"),
            "batch_record_path": str(PROJECT_ROOT / "batch" / "batch-record.json"),
            "item_order": [],
            "created_or_replayed": "CREATED",
        }
        with patch.object(BatchProjectionProvider, "plan", return_value=result) as plan:
            args = kernel_cli._parser().parse_args(
                [
                    "batch-plan",
                    "--platform",
                    "bilibili",
                    "--source-url",
                    "https://www.bilibili.com/video/BV1xx411c7mD/",
                    "--task-start",
                    "2026-08-16T09:05:00+08:00",
                    "--request-id",
                    "default-workspace",
                ]
            )
            kernel_cli._execute(args, PROJECT_ROOT)
        self.assertEqual(plan.call_args.kwargs["workspace_root"], PROJECT_ROOT)

    def test_batch_run_omitted_start_binds_once_and_reuses_it(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-run-start")
        contracts = ContractRegistry(PROJECT_ROOT)
        store = ControlStore.initialize(workspace, contracts)
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
            "output_root": str(workspace),
            "batch_dir": str(workspace / "batch" / "batch-control"),
            "control_dir": str(workspace / ".workflow-control" / "batches"),
            "batch_stage": "planned",
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
        authority_path = workspace / "authority.json"
        authority_path.write_text(
            json.dumps({"global_gate_binding": {"generation": 1}}),
            encoding="utf-8",
        )
        argv = [
            "batch-run",
            "--batch-id",
            record["batch_id"],
            "--control-store-root",
            str(workspace),
            "--session-id",
            "session-cli-run",
        ]
        with patch.object(
            BilibiliPlatformCutoverPublisher,
            "require_current",
            return_value={"authority_path": str(authority_path)},
        ), patch.object(
            BatchProjectionProvider,
            "run",
            return_value={"batch_id": record["batch_id"], "items": []},
        ) as run:
            kernel_cli._execute(kernel_cli._parser().parse_args(argv), PROJECT_ROOT)
            first_start = run.call_args.kwargs["run_task_start"]
            kernel_cli._execute(kernel_cli._parser().parse_args(argv), PROJECT_ROOT)
            second_start = run.call_args.kwargs["run_task_start"]
        self.assertEqual(first_start, second_start)
        self.assertEqual(
            ControlStore(workspace, contracts)
            .get_batch_record(record["batch_id"])["run_task_start"],
            first_start,
        )

    def test_batch_plan_requires_task_start(self) -> None:
        workspace = new_workflow_workspace(self.id(), label="cli-plan-missing")
        completed = _run_cli(
            "batch-plan",
            "--workspace-root",
            str(workspace),
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
            "--workspace-root",
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
            "--workspace-root",
            str(workspace),
            "--batch-id",
            "f" * 32,
            "--control-store-root",
            str(workspace),
        )
        self.assertNotEqual(completed.returncode, 0)
        envelope = json.loads(completed.stdout)
        self.assertEqual(envelope["status"], "error")


if __name__ == "__main__":
    unittest.main()

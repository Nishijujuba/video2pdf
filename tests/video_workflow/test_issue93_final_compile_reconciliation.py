from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts" / "video_workflow.py"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.video_workflow._test_run import new_case_dir


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_interrupted_workspace(root: Path, operation_id: str, marker: str) -> None:
    root.mkdir(parents=True)
    operation = {
        "schema_name": "final-compile-operation",
        "schema_version": "1.0.0",
        "operation_id": operation_id,
    }
    operation["operation_sha256"] = hashlib.sha256(
        _canonical_bytes(operation)
    ).hexdigest()
    (root / "final-compile-operation.json").write_bytes(_canonical_bytes(operation))
    execution = {
        "schema_name": "final-compile-execution",
        "schema_version": "1.0.0",
        "operation_id": operation_id,
        "state": "failed",
        "adapter_pid": None,
        "exit_code": 1,
    }
    execution["execution_sha256"] = hashlib.sha256(
        _canonical_bytes(execution)
    ).hexdigest()
    (root / "final-compile-execution.json").write_bytes(_canonical_bytes(execution))
    (root / "partial-evidence.txt").write_text(marker, encoding="utf-8")


def _run_reconcile(workspace_root: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(CLI),
            "delivery-quality-final-compile-reconcile",
            "--workspace-root",
            str(workspace_root),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    return completed, json.loads(completed.stdout)


class FinalCompileReconciliationArchiveTests(unittest.TestCase):
    # Fixture migration impact: _write_interrupted_workspace is the valid graph
    # builder; operation/execution records are derived nodes; reconciliation is
    # the archive boundary; the negative replay scenario mutates only execution
    # state after that boundary and asserts its stable first-gate error code.
    def test_public_reconcile_archives_shared_operation_workspaces_distinctly(self) -> None:
        case_root = new_case_dir(self.id())
        operation_id = f"issue93-shared-{uuid.uuid4().hex}"
        first_workspace = case_root / "workspace-a"
        second_workspace = case_root / "workspace-b"
        _write_interrupted_workspace(first_workspace, operation_id, "first")
        _write_interrupted_workspace(second_workspace, operation_id, "second")

        first_completed, first = _run_reconcile(first_workspace)
        second_completed, second = _run_reconcile(second_workspace)

        self.assertEqual(0, first_completed.returncode, first_completed.stderr)
        self.assertEqual(0, second_completed.returncode, second_completed.stderr)
        self.assertEqual(operation_id, first["data"]["operation_id"])
        self.assertEqual(operation_id, second["data"]["operation_id"])
        first_archive = Path(first["data"]["archive_path"])
        second_archive = Path(second["data"]["archive_path"])
        self.assertEqual(
            case_root
            / "待删除"
            / "final-compile-interrupted-by-workspace"
            / "workspace-a",
            first_archive,
        )
        self.assertEqual(
            case_root
            / "待删除"
            / "final-compile-interrupted-by-workspace"
            / "workspace-b",
            second_archive,
        )
        self.assertEqual("first", (first_archive / "partial-evidence.txt").read_text())
        self.assertEqual("second", (second_archive / "partial-evidence.txt").read_text())

    def test_public_reconcile_replay_returns_the_same_immutable_archive(self) -> None:
        case_root = new_case_dir(self.id())
        operation_id = f"issue93-replay-{uuid.uuid4().hex}"
        workspace = case_root / "workspace-a"
        _write_interrupted_workspace(workspace, operation_id, "retained")

        first_completed, first = _run_reconcile(workspace)
        archive = Path(first["data"]["archive_path"])
        operation_before = (archive / "final-compile-operation.json").read_bytes()
        second_completed, second = _run_reconcile(workspace)

        self.assertEqual(0, first_completed.returncode, first_completed.stderr)
        self.assertEqual(0, second_completed.returncode, second_completed.stderr)
        self.assertEqual("final_compile_interrupted_archived", first["data"]["classification"])
        self.assertEqual(
            "final_compile_interruption_already_reconciled",
            second["data"]["classification"],
        )
        self.assertEqual(str(archive), second["data"]["archive_path"])
        self.assertEqual(
            operation_before,
            (archive / "final-compile-operation.json").read_bytes(),
        )
        self.assertEqual("retained", (archive / "partial-evidence.txt").read_text())

    def test_replay_rejects_stale_execution_fingerprint_at_execution_gate(self) -> None:
        # scenario_id: issue93-replay-stale-execution
        # target_invariant: execution fingerprint matches archived execution bytes
        # mutation_seam: after successful archive publication
        # rematerialized_nodes: none
        # intentionally_stale_nodes: execution_sha256
        # expected_first_gate/error: final_compile_execution_fingerprint_invalid
        # scenario_class: single_contradiction
        case_root = new_case_dir(self.id())
        operation_id = f"issue93-fingerprint-{uuid.uuid4().hex}"
        workspace = case_root / "workspace-a"
        _write_interrupted_workspace(workspace, operation_id, "retained")
        first_completed, first = _run_reconcile(workspace)
        self.assertEqual(0, first_completed.returncode, first_completed.stderr)
        archive = Path(first["data"]["archive_path"])
        execution_path = archive / "final-compile-execution.json"
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
        execution["exit_code"] = 2
        execution_path.write_bytes(_canonical_bytes(execution))

        replay_completed, replay = _run_reconcile(workspace)

        self.assertEqual(20, replay_completed.returncode, replay_completed.stderr)
        self.assertEqual("contract_invalid", replay["classification"])
        self.assertEqual(
            "final_compile_execution_fingerprint_invalid",
            replay["data"]["error_code"],
        )

    def test_new_archive_does_not_modify_operation_only_legacy_archive(self) -> None:
        case_root = new_case_dir(self.id())
        operation_id = f"issue93-legacy-{uuid.uuid4().hex}"
        legacy_archive = (
            case_root
            / "待删除"
            / "final-compile-interrupted"
            / operation_id
        )
        _write_interrupted_workspace(legacy_archive, operation_id, "legacy")
        legacy_bytes = {
            path.relative_to(legacy_archive): path.read_bytes()
            for path in legacy_archive.rglob("*")
            if path.is_file()
        }
        workspace = case_root / "workspace-b"
        _write_interrupted_workspace(workspace, operation_id, "current")

        completed, result = _run_reconcile(workspace)

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(
            case_root
            / "待删除"
            / "final-compile-interrupted-by-workspace"
            / "workspace-b",
            Path(result["data"]["archive_path"]),
        )
        self.assertEqual(
            legacy_bytes,
            {
                path.relative_to(legacy_archive): path.read_bytes()
                for path in legacy_archive.rglob("*")
                if path.is_file()
            },
        )


if __name__ == "__main__":
    unittest.main()

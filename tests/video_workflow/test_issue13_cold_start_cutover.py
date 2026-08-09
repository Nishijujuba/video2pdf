from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]

from tests.video_workflow._test_run import child_environment, new_case_dir
from tests.video_workflow import test_issue13_platform_cutover as platform_cutover_test


def _run_public_cli(test_id: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(PROJECT_ROOT / "scripts" / "video_workflow.py"),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=child_environment(test_id),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class Issue13ColdStartCutoverTests(unittest.TestCase):
    def _cold_start_case(self) -> tuple[Path, Path, Path, str]:
        case_root = new_case_dir(self.id(), label="issue13-cold-start-cutover")
        control_store_root = case_root / "control"
        control_store_root.mkdir()
        platform_cutover_test.Issue13PlatformCutoverTests._write_stub_global_gate(
            control_store_root
        )

        workspace_root = case_root / "project" / "workspace"
        workspace_root.mkdir(parents=True)
        probe_path = case_root / "candidate-probe.json"
        probe_path.write_bytes(
            (
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "contracts"
                / "bootstrap-record.v2.valid.json"
            ).read_bytes()
        )
        implementation_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout.strip()
        return control_store_root, workspace_root, probe_path, implementation_commit

    def _prepare_candidate(
        self,
        *,
        control_store_root: Path,
        probe_path: Path,
        implementation_commit: str,
    ) -> dict:
        completed = _run_public_cli(
            self.id() + "-prepare",
            "platform-kernel-prepare",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control_store_root),
            "--implementation-commit",
            implementation_commit,
            "--candidate-probe",
            str(probe_path),
            "--candidate-session-id",
            "session-issue13-candidate",
            "--prepared-at",
            "2026-08-09T13:00:00Z",
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        envelope = json.loads(completed.stdout)
        self.assertEqual("platform_kernel_candidate_prepared", envelope["classification"])
        self.assertEqual(
            {
                "authority_status": "prepared_candidate",
                "candidate_run_id": "00000000000000000000000000000000",
                "candidate_session_id": "session-issue13-candidate",
                "platform_statuses": {
                    "bilibili": "active_legacy",
                    "youtube": "active_legacy",
                },
            },
            {
                key: envelope["data"][key]
                for key in (
                    "authority_status",
                    "candidate_run_id",
                    "candidate_session_id",
                    "platform_statuses",
                )
            },
        )
        self.assertFalse(
            (control_store_root / "platform-authorities" / "bilibili.json").exists(),
            "prepare must not publish active_kernel authority",
        )
        return envelope

    def test_cold_start_prepare_binds_one_candidate_without_activation(self) -> None:
        control, _workspace, probe, implementation_commit = self._cold_start_case()

        self._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )

    def test_prepared_candidate_can_initialize_v4_through_public_cli(self) -> None:
        control, workspace, probe, implementation_commit = self._cold_start_case()
        self._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )

        completed = _run_public_cli(
            self.id() + "-candidate-init",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(probe),
            "--session-id",
            "session-issue13-candidate",
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        envelope = json.loads(completed.stdout)
        run_record = json.loads(
            (Path(envelope["data"]["run_dir"]) / "workflow" / "run.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("cutover_candidate_initialized", envelope["classification"])
        self.assertEqual("4.0.0", run_record["schema_version"])
        self.assertEqual("generating", run_record["delivery"]["stage"])

    def test_prepared_state_keeps_ordinary_init_fail_closed_until_confirmed(self) -> None:
        control, workspace, probe, implementation_commit = self._cold_start_case()
        self._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )

        completed = _run_public_cli(
            self.id() + "-ordinary-init",
            "init-run",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(probe),
            "--session-id",
            "session-ordinary-run",
        )

        self.assertEqual(30, completed.returncode, completed.stdout + completed.stderr)
        envelope = json.loads(completed.stdout)
        self.assertEqual("error", envelope["status"])
        self.assertEqual("identity_or_path_conflict", envelope["classification"])
        self.assertEqual(
            "platform_kernel_authority",
            envelope["data"]["first_failing_gate"],
        )
        self.assertEqual(
            "bilibili_platform_authority_pending_confirmation",
            envelope["data"]["error_code"],
        )


if __name__ == "__main__":
    unittest.main()

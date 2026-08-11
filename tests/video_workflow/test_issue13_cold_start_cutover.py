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

    def test_initialized_candidate_acquires_recorded_bilibili_source_without_a_second_run(
        self,
    ) -> None:
        control, workspace, probe, implementation_commit = self._cold_start_case()
        probe_value = json.loads(probe.read_text(encoding="utf-8"))
        probe_value.update(
            {
                "canonical_item_id": "BV1TEST00001:p1",
                "source_identity": (
                    "51b5b6809799e799b780ea3dcbf50322d5ada3dae052fe50e0da65e98f328129"
                ),
                "original_title": "Bilibili Adapter Fixture",
                "source_request": {
                    "kind": "fresh_download",
                    "canonical_locator": (
                        "https://www.bilibili.com/video/BV1TEST00001/"
                    ),
                },
            }
        )
        probe.write_text(
            json.dumps(probe_value, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        self._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )
        initialized = _run_public_cli(
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
        self.assertEqual(
            0, initialized.returncode, initialized.stdout + initialized.stderr
        )
        initialized_envelope = json.loads(initialized.stdout)
        run_dir = Path(initialized_envelope["data"]["run_dir"])
        initialized_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        cookie_file = control.parent / "credentials" / "bilibili-cookies.txt"
        cookie_file.parent.mkdir(parents=True)
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\trecorded\n",
            encoding="utf-8",
        )

        faulted = _run_public_cli(
            self.id() + "-source-acquire-faulted",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "providers"
                / "bilibili"
                / "fresh-download"
            ),
            "--fault-point",
            "after_provider_terminal_proof_persisted",
        )
        self.assertNotEqual(0, faulted.returncode)
        self.assertEqual(
            "pending",
            json.loads(
                (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
            )["source_state"],
        )

        reconciled = _run_public_cli(
            self.id() + "-source-acquire-reconcile",
            "source-acquire-reconcile",
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(
            0, reconciled.returncode, reconciled.stdout + reconciled.stderr
        )
        self.assertEqual(
            1, json.loads(reconciled.stdout)["data"]["tasks_advanced"]
        )

        acquired = _run_public_cli(
            self.id() + "-source-acquire",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "providers"
                / "bilibili"
                / "fresh-download"
            ),
        )

        self.assertEqual(0, acquired.returncode, acquired.stdout + acquired.stderr)
        acquired_envelope = json.loads(acquired.stdout)
        current_record = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual("source_acquired", acquired_envelope["classification"])
        self.assertEqual(
            initialized_record["run_id"], acquired_envelope["data"]["run_id"]
        )
        self.assertEqual(initialized_record["run_id"], current_record["run_id"])
        self.assertEqual(
            initialized_record["source_identity"], current_record["source_identity"]
        )
        self.assertEqual("ready", current_record["source_state"])
        self.assertEqual(
            "current", current_record["checkpoints"]["source_ready"]["status"]
        )
        self.assertTrue((run_dir / "source" / "manifest.json").is_file())
        self.assertTrue(
            current_record["artifact_generations"]["source_acquisition_decision"][
                "producer"
            ].startswith("task:"),
            "source selection must pass through Task/Attempt promotion authority",
        )
        self.assertEqual(
            [run_dir.resolve()],
            [
                path.parent.parent.resolve()
                for path in workspace.rglob("workflow/run.json")
            ],
            "source acquisition must attach to the candidate instead of creating a second Run",
        )

        planned = _run_public_cli(
            self.id() + "-production-plan",
            "production-plan",
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(0, planned.returncode, planned.stdout + planned.stderr)
        self.assertEqual(
            "production_tasks_runnable",
            json.loads(planned.stdout)["classification"],
        )

        replayed = _run_public_cli(
            self.id() + "-source-acquire-replay",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "providers"
                / "bilibili"
                / "fresh-download"
            ),
        )
        self.assertEqual(0, replayed.returncode, replayed.stdout + replayed.stderr)
        self.assertEqual(
            "source_already_ready", json.loads(replayed.stdout)["classification"]
        )
        self.assertEqual(
            [run_dir.resolve()],
            [
                path.parent.parent.resolve()
                for path in workspace.rglob("workflow/run.json")
            ],
        )

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

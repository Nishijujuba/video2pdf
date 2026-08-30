from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]

from tests.video_workflow._test_run import child_environment, new_case_dir
from tests.video_workflow import test_issue13_platform_cutover as platform_cutover_test
from video2pdf_workflow_kernel.cli import _production_probe_from_path
from video2pdf_workflow_kernel.control_store import ControlStore
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
from video2pdf_workflow_kernel.platform_kernel import PlatformCutoverPublisher


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
    def setUp(self) -> None:
        self.skipTest(
            "Issue #90 archived cutover-candidate initialization commands"
        )

    def _cold_start_case(
        self, *, current_global_gate: bool = False
    ) -> tuple[Path, Path, Path, str]:
        case_root = new_case_dir(self.id(), label="issue13-cold-start-cutover")
        control_store_root = case_root / "control"
        control_store_root.mkdir()
        if current_global_gate:
            from tests.video_workflow import test_acceptance_v2 as acceptance_v2_test

            acceptance_v2_test.activate_test_global_gate(control_store_root)
        else:
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
        candidate_run_id = json.loads(probe_path.read_text(encoding="utf-8"))[
            "run_id"
        ]
        result = PlatformCutoverPublisher().prepare_candidate(
            platform="bilibili",
            control_store_root=control_store_root,
            implementation_commit=implementation_commit,
            candidate_probe=probe_path,
            candidate_session_id="session-issue13-candidate",
            prepared_at="2026-08-09T13:00:00Z",
        )
        self.assertEqual(
            {
                "authority_status": "prepared_candidate",
                "candidate_run_id": candidate_run_id,
                "candidate_session_id": "session-issue13-candidate",
                "platform_statuses": {
                    "bilibili": "active_legacy",
                    "youtube": "active_legacy",
                },
            },
            {
                key: result[key]
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
        return {"classification": "platform_kernel_candidate_prepared", "data": result}

    def _initialize_candidate(
        self,
        *,
        control_store_root: Path,
        workspace_root: Path,
        probe_path: Path,
        session_id: str = "session-issue13-candidate",
    ) -> Path:
        kernel = VideoWorkflowKernel(workspace_root)
        kernel.control_store = ControlStore.initialize(workspace_root, kernel.contracts)
        probe = _production_probe_from_path(probe_path, kernel.contracts)
        publisher = PlatformCutoverPublisher()
        candidate = publisher.begin_candidate_initialization(
            platform=probe.canonical_platform,
            control_store_root=control_store_root,
            candidate_probe=probe_path,
            candidate_session_id=session_id,
            workspace_root=workspace_root,
        )
        initialized = kernel.initialize_production_source(
            probe,
            session_id=session_id,
            global_gate_binding=candidate["global_gate_binding"],
        )
        publisher.record_candidate_initialized(
            platform=probe.canonical_platform,
            control_store_root=control_store_root,
            candidate_run_dir=initialized.run_dir,
        )
        return initialized.run_dir

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

    def test_cutover_candidate_coexists_with_existing_legacy_task_index(self) -> None:
        control, workspace, probe, implementation_commit = self._cold_start_case()
        self._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )
        project_root = workspace.parent
        legacy_index_path = (
            project_root / ".codex" / "delivery-targets" / "task-index.json"
        )
        legacy_index_path.parent.mkdir(parents=True)
        legacy_index = {
            "schema_version": "1.0",
            "tasks": [
                {
                    "video_output_dir": "workspace/existing-legacy-video",
                    "target_file": (
                        "workspace/existing-legacy-video/review/acceptance/"
                        "delivery_target.json"
                    ),
                    "owner_session_id": "legacy-session",
                    "owner_status": "active",
                    "last_session_id": "legacy-session",
                    "stage": "generating",
                    "updated_at": "2026-08-09T12:00:00+08:00",
                }
            ],
        }
        legacy_bytes = (
            json.dumps(legacy_index, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        legacy_index_path.write_bytes(legacy_bytes)
        legacy_sha = hashlib.sha256(legacy_bytes).hexdigest()

        faulted = _run_public_cli(
            self.id() + "-candidate-fault",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(probe),
            "--session-id",
            "session-issue13-candidate",
            "--fault-point",
            "after_candidate_begin",
        )
        self.assertEqual(60, faulted.returncode, faulted.stdout + faulted.stderr)
        candidate_reconciled = _run_public_cli(
            self.id() + "-candidate-reconcile",
            "platform-kernel-candidate-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--workspace-root",
            str(workspace),
            "--candidate-probe",
            str(probe),
            "--candidate-session-id",
            "session-issue13-candidate",
        )
        self.assertEqual(
            0,
            candidate_reconciled.returncode,
            candidate_reconciled.stdout + candidate_reconciled.stderr,
        )
        retried = _run_public_cli(
            self.id() + "-candidate-retry",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace),
            "--control-store-root",
            str(control),
            "--probe",
            str(probe),
            "--session-id",
            "session-issue13-candidate",
            "--fault-point",
            "after_output_dir_publish",
        )
        self.assertEqual(60, retried.returncode, retried.stdout + retried.stderr)
        initialized = _run_public_cli(
            self.id() + "-publication-reconcile",
            "platform-kernel-candidate-reconcile",
            "--platform",
            "bilibili",
            "--control-store-root",
            str(control),
            "--workspace-root",
            str(workspace),
            "--candidate-probe",
            str(probe),
            "--candidate-session-id",
            "session-issue13-candidate",
        )
        self.assertEqual(
            0, initialized.returncode, initialized.stdout + initialized.stderr
        )
        initialized_envelope = json.loads(initialized.stdout)
        run_dir = Path(initialized_envelope["data"]["run_dir"])
        run_record_path = run_dir / "workflow" / "run.json"
        run_record = json.loads(run_record_path.read_text(encoding="utf-8"))
        sibling_index_path = legacy_index_path.with_name("kernel-task-index.json")
        sibling_index = json.loads(sibling_index_path.read_text(encoding="utf-8"))
        session_path = (
            project_root
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / "session-issue13-candidate"
            / "current.json"
        )
        session_target = json.loads(session_path.read_text(encoding="utf-8"))
        video_target = json.loads(
            (run_dir / "review" / "acceptance" / "delivery_target.json").read_text(
                encoding="utf-8"
            )
        )
        task_binding = run_record["delivery"]["projections"]["task_index"]
        self.assertEqual(legacy_bytes, legacy_index_path.read_bytes())
        self.assertEqual(
            legacy_sha,
            hashlib.sha256(legacy_index_path.read_bytes()).hexdigest(),
        )
        self.assertEqual("1.0.0", sibling_index["schema_version"])
        self.assertEqual(
            [run_record["run_id"]],
            [entry["run_id"] for entry in sibling_index["entries"]],
        )
        self.assertEqual(str(sibling_index_path.resolve()), task_binding["path"])
        self.assertEqual(
            hashlib.sha256(sibling_index_path.read_bytes()).hexdigest(),
            task_binding["sha256"],
        )
        self.assertEqual(run_record["run_id"], session_target["run_id"])
        self.assertEqual(run_record["run_id"], video_target["run_id"])

        reconciled = _run_public_cli(
            self.id() + "-reconcile",
            "reconcile-run",
            "--workspace-root",
            str(workspace),
            "--run-id",
            run_record["run_id"],
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        self.assertEqual(legacy_bytes, legacy_index_path.read_bytes())
        self.assertEqual(
            hashlib.sha256(sibling_index_path.read_bytes()).hexdigest(),
            json.loads(run_record_path.read_text(encoding="utf-8"))["delivery"][
                "projections"
            ]["task_index"]["sha256"],
        )

    def test_cutover_candidate_rejects_invalid_kernel_task_index_sibling(self) -> None:
        control, workspace, probe, implementation_commit = self._cold_start_case()
        self._prepare_candidate(
            control_store_root=control,
            probe_path=probe,
            implementation_commit=implementation_commit,
        )
        delivery_root = workspace.parent / ".codex" / "delivery-targets"
        delivery_root.mkdir(parents=True)
        legacy_path = delivery_root / "task-index.json"
        sibling_path = delivery_root / "kernel-task-index.json"
        legacy_bytes = b'{"schema_version":"1.0","tasks":[]}\n'
        invalid_sibling_bytes = b'{"schema_version":"1.0.0","entries":[]}\n'
        legacy_path.write_bytes(legacy_bytes)
        sibling_path.write_bytes(invalid_sibling_bytes)

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

        self.assertNotEqual(0, completed.returncode)
        envelope = json.loads(completed.stdout)
        self.assertIn(
            envelope["classification"], {"contract_invalid", "unknown_contract_version"}
        )
        self.assertEqual(legacy_bytes, legacy_path.read_bytes())
        self.assertEqual(invalid_sibling_bytes, sibling_path.read_bytes())
        self.assertFalse(
            (
                workspace.parent
                / ".codex"
                / "delivery-targets"
                / "sessions"
                / "session-issue13-candidate"
                / "current.json"
            ).exists()
        )

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
        original_cookie_bytes = cookie_file.read_bytes()
        provider_recording = (
            PROJECT_ROOT
            / "tests"
            / "video_workflow"
            / "fixtures"
            / "providers"
            / "bilibili"
            / "fresh-download"
        )
        replacement_recording = control.parent / "provider-generation-2"
        shutil.copytree(provider_recording, replacement_recording)
        replacement_subtitle = (
            replacement_recording / "outputs" / "subtitle.manual.en.srt"
        )
        replacement_subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n"
            "generation two provider subtitle\n",
            encoding="utf-8",
            newline="\n",
        )
        replacement_record = json.loads(
            (replacement_recording / "recording.json").read_text(encoding="utf-8")
        )
        manual_command = next(
            item
            for item in replacement_record["commands"]
            if item["operation"] == "subtitle_manual"
        )
        manual_command["outputs"][0]["sha256"] = hashlib.sha256(
            replacement_subtitle.read_bytes()
        ).hexdigest()
        (replacement_recording / "recording.json").write_text(
            json.dumps(replacement_record, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )

        faulted = _run_public_cli(
            self.id() + "-source-acquire-faulted",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(provider_recording),
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
        first_working_copies = list(
            (run_dir / "待删除" / "source-acquire" / "credentials").glob(
                "**/cookies.txt"
            )
        )
        self.assertEqual(1, len(first_working_copies))
        first_scratch_inventories = list(
            (
                run_dir.parent.parent
                / "待删除"
                / "source-acquire"
                / initialized_record["run_id"]
                / "candidate-materialization"
            ).rglob("candidate-inventory.json")
        )
        self.assertEqual(1, len(first_scratch_inventories))
        first_scratch_inventory_path = first_scratch_inventories[0]
        first_scratch_inventory_bytes = first_scratch_inventory_path.read_bytes()
        self.assertFalse(
            (
                run_dir
                / "work"
                / "source-acquisition"
                / "candidate-inventory.json"
            ).exists(),
            "generation 1 must remain unpromoted after the injected fault",
        )
        first_working_copies[0].write_bytes(
            b"# Netscape HTTP Cookie File\nprovider-writeback-generation-1\n"
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
            str(replacement_recording),
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
        self.assertEqual(
            initialized_record["source_epoch"], current_record["source_epoch"]
        )
        self.assertEqual("ready", current_record["source_state"])
        self.assertEqual(original_cookie_bytes, cookie_file.read_bytes())
        attempts = sorted(
            (
                json.loads(path.read_text(encoding="utf-8"))
                for path in (run_dir / "workflow" / "tasks").glob(
                    "*/attempts/*/attempt.json"
                )
                if json.loads(
                    (path.parents[2] / "task.json").read_text(encoding="utf-8")
                )["task_stage"]
                == "provider_acquisition"
            ),
            key=lambda item: item["claim_generation"],
        )
        self.assertEqual([1, 2], [item["claim_generation"] for item in attempts])
        scratch_inventories = list(
            (
                run_dir.parent.parent
                / "待删除"
                / "source-acquire"
                / initialized_record["run_id"]
                / "candidate-materialization"
            ).rglob("candidate-inventory.json")
        )
        self.assertEqual(2, len(scratch_inventories))
        self.assertEqual(
            first_scratch_inventory_bytes,
            first_scratch_inventory_path.read_bytes(),
            "generation 2 must preserve generation 1 scratch evidence",
        )
        scratch_inventory_values = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in scratch_inventories
        ]
        self.assertEqual(
            2,
            len(
                {
                    inventory["provider"]["recording_sha256"]
                    for inventory in scratch_inventory_values
                }
            ),
            "each provider generation must retain its own semantic inventory",
        )
        working_copies = list(
            (run_dir / "待删除" / "source-acquire" / "credentials").glob(
                "**/cookies.txt"
            )
        )
        self.assertEqual(2, len(working_copies))
        self.assertEqual(
            {item["attempt_id"] for item in attempts},
            {path.parent.name.split(".g", 1)[0] for path in working_copies},
        )
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

        stale_replay = _run_public_cli(
            self.id() + "-source-acquire-stale-replay",
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
        self.assertEqual(20, stale_replay.returncode)
        self.assertEqual(
            "contract_invalid", json.loads(stale_replay.stdout)["classification"]
        )

        replayed = _run_public_cli(
            self.id() + "-source-acquire-current-replay",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(replacement_recording),
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

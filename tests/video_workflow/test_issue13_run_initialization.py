from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
from tests.video_workflow._test_run import (
    new_case_dir,
    write_committed_cutover_retirement,
)
from tests.video_workflow.test_issue13_platform_cutover import (
    _run_cli as _run_platform_cli,
)
from src.video2pdf_workflow_kernel.cli import main as workflow_main
from src.video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
from src.video2pdf_workflow_kernel.platform_kernel import (
    BilibiliPlatformCutoverPublisher,
)


def _run_cli(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = _run_platform_cli(*arguments)
    return completed, json.loads(completed.stdout)


class _LiveFixtureSubprocess:
    """Serve recorded probe outputs through the live command runner."""

    def __init__(self, recording: Path) -> None:
        self._recording = recording
        self._commands = json.loads(
            (recording / "recording.json").read_text(encoding="utf-8")
        )["commands"]
        self._cursor = 0

    def __call__(self, argv, **_kwargs) -> subprocess.CompletedProcess[bytes]:
        expected = self._commands[self._cursor]
        comparable = list(argv)
        comparable[0] = "python"
        if "--cookies" in comparable:
            comparable[comparable.index("--cookies") + 1] = (
                "<localized-cookie-file>"
            )
        if comparable != expected["argv"] or expected["outputs"]:
            raise AssertionError(
                f"live provider probe command {self._cursor} differs from fixture"
            )
        self._cursor += 1
        return subprocess.CompletedProcess(
            args=argv,
            returncode=expected["returncode"],
            stdout=(self._recording / expected["stdout"]["path"]).read_bytes(),
            stderr=(self._recording / expected["stderr"]["path"]).read_bytes(),
        )


def _run_start_cli_with_recording(
    recording: Path,
    *arguments: str,
    injected_fault_point: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    process_patch = patch(
        "src.video2pdf_workflow_kernel.adapters.base.subprocess.run",
        side_effect=_LiveFixtureSubprocess(recording),
    )
    original_initialize = VideoWorkflowKernel.initialize_production_source

    def initialize_with_fault(self, probe, **kwargs):
        kwargs["fault_point"] = injected_fault_point
        return original_initialize(self, probe, **kwargs)

    fault_patch = (
        patch.object(
            VideoWorkflowKernel,
            "initialize_production_source",
            initialize_with_fault,
        )
        if injected_fault_point is not None
        else None
    )
    with process_patch, redirect_stdout(stdout), redirect_stderr(stderr):
        if fault_patch is None:
            returncode = workflow_main(list(arguments))
        else:
            with fault_patch:
                returncode = workflow_main(list(arguments))
    completed = subprocess.CompletedProcess(
        args=list(arguments),
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )
    return completed, json.loads(completed.stdout)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


def _source_identity(item_id: str) -> str:
    value = {"canonical_item_id": item_id, "canonical_platform": "bilibili"}
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_current_global_gate(control_root: Path) -> None:
    authority_path = control_root / "active_global_gate.json"
    authority_path.write_bytes(
        (PROJECT_ROOT / "workspace" / "active_global_gate.json").read_bytes()
    )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
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
                _sha256(authority_path),
            ),
        )


def _write_start_run_project(case_root: Path) -> tuple[Path, Path, Path]:
    project_root = case_root / "project"
    config_root = project_root / "config"
    workspace_root = project_root / "workspace"
    config_root.mkdir(parents=True)
    profile_path = config_root / "workflow-release-profile.v1.json"
    profile_path.write_bytes(
        (PROJECT_ROOT / "config" / "workflow-release-profile.v1.json").read_bytes()
    )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    activation_path = config_root / "workflow-admission-activation.v1.json"
    activation_path.write_text(
        json.dumps(
            {
                "schema_name": "workflow-admission-activation",
                "schema_version": "1.0.0",
                "activation_status": "active_profile_admission",
                "release_id": "workflow-2.0",
                "profile_sha256": _sha256(profile_path),
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
                "control_store_root": "workspace",
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
    workspace_root.mkdir(parents=True)
    _write_current_global_gate(workspace_root)
    write_committed_cutover_retirement(
        workspace_root, profile=profile, profile_path=profile_path
    )
    credential = case_root / "private" / "cookies.txt"
    credential.parent.mkdir(parents=True)
    credential.write_text(
        "# Netscape HTTP Cookie File\n"
        ".example.invalid\tTRUE\t/\tTRUE\t2147483647\tSESSION\tsecret\n",
        encoding="utf-8",
    )
    return project_config, workspace_root, credential


class Issue13RunInitializationTests(unittest.TestCase):
    def test_start_run_rejects_unsafe_session_ids_before_bootstrap_or_run_publication(
        self,
    ) -> None:
        cases = (
            ("parent_segment", ".."),
            ("absolute_path", None),
            ("windows_drive_like", "C:session-escape"),
        )
        for label, declared_session_id in cases:
            with self.subTest(label=label):
                case_root = new_case_dir(
                    f"{self.id()}-{label}", label="issue13-unsafe-session-id"
                )
                project_config, workspace_root, _credential = (
                    _write_start_run_project(case_root)
                )
                session_id = declared_session_id or str(
                    (case_root / "absolute-session-escape").resolve()
                )

                completed, envelope = _run_cli(
                    "start-run",
                    "--project-config",
                    str(project_config),
                    "--platform",
                    "bilibili",
                    "--source-url",
                    "https://www.bilibili.com/video/BV1TEST00001/?p=1",
                    "--session-id",
                    session_id,
                )

                self.assertEqual(
                    {
                        "returncode": 2,
                        "status": "error",
                        "classification": "usage_error",
                        "session_targets": [],
                    },
                    {
                        "returncode": completed.returncode,
                        "status": envelope["status"],
                        "classification": envelope["classification"],
                        "session_targets": sorted(
                            str(path.relative_to(case_root))
                            for path in case_root.rglob("current.json")
                        ),
                    },
                    completed.stdout + completed.stderr,
                )
                self.assertEqual(list(workspace_root.rglob("workflow/run.json")), [])
                self.assertFalse(
                    (workspace_root / ".workflow-control/control.sqlite3").exists()
                )

    def test_profile_backed_start_run_atomically_publishes_v4_and_delivery_projections(
        self,
    ) -> None:
        case_root = new_case_dir(self.id(), label="issue13-run-initialization")
        project_root = case_root / "project"
        project_config, workspace_root, credential = _write_start_run_project(case_root)

        completed, envelope = _run_start_cli_with_recording(
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/providers/bilibili/fresh-download",
            "start-run",
            "--project-config",
            str(project_config),
            "--platform",
            "bilibili",
            "--source-url",
            "https://www.bilibili.com/video/BV1TEST00001/?p=1",
            "--session-id",
            "session-issue13",
            "--credential-ref",
            str(credential),
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("run_initialized", envelope["classification"])

        run_dir = Path(envelope["data"]["run_dir"])
        run_path = run_dir / "workflow" / "run.json"
        video_target_path = (
            run_dir / "review" / "acceptance" / "delivery_target.json"
        )
        session_target_path = (
            project_root
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / "session-issue13"
            / "current.json"
        )
        task_index_path = (
            project_root / ".codex" / "delivery-targets" / "task-index.json"
        )
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        bootstrap_record = json.loads(
            (run_dir / "待删除" / "bootstrap" / "probe.json").read_text(
                encoding="utf-8"
            )
        )
        video_target = json.loads(video_target_path.read_text(encoding="utf-8"))
        session_target = json.loads(session_target_path.read_text(encoding="utf-8"))
        task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        task_entry = next(
            item
            for item in task_index["entries"]
            if item["run_id"] == run_record["run_id"]
        )

        self.assertEqual(
            {
                "run": {
                    "schema": ("run-record", "4.0.0"),
                    "platform": "bilibili",
                    "revision": 1,
                    "stage": "generating",
                    "session_id": "session-issue13",
                    "video_target_sha256": _sha256(video_target_path),
                    "session_target_sha256": _sha256(session_target_path),
                    "task_index_sha256": _sha256(task_index_path),
                },
                "video_target": {
                    "schema": ("kernel-delivery-target", "1.0.0"),
                    "run_id": run_record["run_id"],
                    "stage": "generating",
                },
                "session_target": {
                    "schema": ("kernel-session-delivery-target", "1.0.0"),
                    "run_id": run_record["run_id"],
                    "stage": "generating",
                },
                "task_entry": {
                    "run_id": run_record["run_id"],
                    "stage": "generating",
                    "session_id": "session-issue13",
                    "video_target_sha256": _sha256(video_target_path),
                    "session_target_sha256": _sha256(session_target_path),
                },
            },
            {
                "run": {
                    "schema": (
                        run_record["schema_name"],
                        run_record["schema_version"],
                    ),
                    "platform": run_record["canonical_platform"],
                    "revision": run_record["coordination_revision"],
                    "stage": run_record["delivery"]["stage"],
                    "session_id": run_record["delivery"]["ownership"][
                        "session_id"
                    ],
                    "video_target_sha256": run_record["delivery"][
                        "projections"
                    ]["video_target"]["sha256"],
                    "session_target_sha256": run_record["delivery"][
                        "projections"
                    ]["session_target"]["sha256"],
                    "task_index_sha256": run_record["delivery"]["projections"][
                        "task_index"
                    ]["sha256"],
                },
                "video_target": {
                    "schema": (
                        video_target["schema_name"],
                        video_target["schema_version"],
                    ),
                    "run_id": video_target["run_id"],
                    "stage": video_target["stage"],
                },
                "session_target": {
                    "schema": (
                        session_target["schema_name"],
                        session_target["schema_version"],
                    ),
                    "run_id": session_target["run_id"],
                    "stage": session_target["stage"],
                },
                "task_entry": {
                    "run_id": task_entry["run_id"],
                    "stage": task_entry["stage"],
                    "session_id": task_entry["session_id"],
                    "video_target_sha256": task_entry["video_target"]["sha256"],
                    "session_target_sha256": task_entry["session_target"]["sha256"],
                },
            },
        )
        self.assertEqual(
            video_target["global_gate_authority"],
            {
                "path": str((workspace_root / "active_global_gate.json").resolve()),
                "generation": 1,
                "sha256": _sha256(workspace_root / "active_global_gate.json"),
            },
        )
        self.assertEqual(
            str(Path(sys.executable)),
            bootstrap_record["probe_execution"]["command_argv_redacted"][0],
        )

    @unittest.skip("Issue #90 archives production init-run")
    def test_interrupted_active_bilibili_init_rolls_forward_all_v4_projections(
        self,
    ) -> None:
        from tests.video_workflow.test_issue13_platform_cutover import (
            Issue13PlatformCutoverTests,
        )

        authority_fixture = Issue13PlatformCutoverTests(
            "test_bilibili_activation_publishes_single_platform_authority"
        )
        control_store_root, exit_evidence = (
            authority_fixture._write_valid_cutover_manifest()
        )
        with sqlite3.connect(
            control_store_root / "global-gate-control.sqlite3"
        ) as connection:
            connection.execute("PRAGMA user_version=1")
        with patch(
            "src.video2pdf_workflow_kernel.platform_kernel._require_formal_exit_evidence"
        ):
            BilibiliPlatformCutoverPublisher().activate(
                platform="bilibili",
                control_store_root=control_store_root,
                exit_evidence=exit_evidence,
                activated_at="2026-08-09T00:00:00Z",
            )

        case_root = new_case_dir(self.id(), label="issue13-init-recovery")
        project_root = case_root / "project"
        workspace_root = project_root / "workspace"
        run_id = "00000000000000000000000000000000"
        session_id = "session-issue13-recovery"
        probe_path = (
            workspace_root
            / "待删除"
            / "pipeline-bootstrap"
            / run_id
            / "probe.json"
        )
        probe_path.parent.mkdir(parents=True)
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

        interrupted, fault = _run_cli(
            "init-run",
            "--workspace-root",
            str(workspace_root),
            "--control-store-root",
            str(control_store_root),
            "--probe",
            str(probe_path),
            "--session-id",
            session_id,
            "--fault-point",
            "after_output_dir_publish",
        )
        self.assertEqual(60, interrupted.returncode, interrupted.stdout)
        self.assertEqual("injected_initialization_fault", fault["classification"])
        session_target_path = (
            project_root
            / ".codex"
            / "delivery-targets"
            / "sessions"
            / session_id
            / "current.json"
        )
        task_index_path = (
            project_root / ".codex" / "delivery-targets" / "task-index.json"
        )
        self.assertFalse(session_target_path.exists())
        self.assertFalse(task_index_path.exists())

        reconciled, recovery = _run_cli(
            "reconcile-run",
            "--workspace-root",
            str(workspace_root),
            "--run-id",
            run_id,
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        self.assertEqual(
            {
                "classification": "initialization_reconciled",
                "outcome": "new_state_complete",
                "run_id": run_id,
            },
            {
                "classification": recovery["classification"],
                "outcome": recovery["data"]["outcome"],
                "run_id": recovery["data"]["run_id"],
            },
        )
        run_dir = Path(recovery["data"]["run_dir"])
        run_path = run_dir / "workflow" / "run.json"
        video_target_path = (
            run_dir / "review" / "acceptance" / "delivery_target.json"
        )
        self.assertTrue(run_path.is_file())
        self.assertTrue(video_target_path.is_file())
        self.assertTrue(session_target_path.is_file())
        self.assertTrue(task_index_path.is_file())
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        video_target = json.loads(video_target_path.read_text(encoding="utf-8"))
        session_target = json.loads(session_target_path.read_text(encoding="utf-8"))
        task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        task_entry = next(
            item for item in task_index["entries"] if item["run_id"] == run_id
        )
        self.assertEqual("4.0.0", run_record["schema_version"])
        self.assertEqual("generating", run_record["delivery"]["stage"])
        self.assertEqual(session_id, run_record["delivery"]["ownership"]["session_id"])
        self.assertEqual(
            _sha256(video_target_path),
            run_record["delivery"]["projections"]["video_target"]["sha256"],
        )
        self.assertEqual(
            _sha256(session_target_path),
            run_record["delivery"]["projections"]["session_target"]["sha256"],
        )
        self.assertEqual(
            _sha256(task_index_path),
            run_record["delivery"]["projections"]["task_index"]["sha256"],
        )
        self.assertEqual(run_id, video_target["run_id"])
        self.assertEqual(run_id, session_target["run_id"])
        self.assertEqual(run_id, task_entry["run_id"])
        self.assertEqual(_sha256(video_target_path), session_target["video_target"]["sha256"])
        self.assertEqual(_sha256(video_target_path), task_entry["video_target"]["sha256"])
        self.assertEqual(_sha256(session_target_path), task_entry["session_target"]["sha256"])

    def test_start_run_retry_recovers_without_reprobing_or_reacquiring_delivery_lock(
        self,
    ) -> None:
        case_root = new_case_dir(self.id(), label="issue13-init-direct-retry")
        project_config, _workspace_root, credential = _write_start_run_project(
            case_root
        )
        session_id = "session-issue13-direct-retry"
        command = (
            "start-run",
            "--project-config",
            str(project_config),
            "--platform",
            "bilibili",
            "--source-url",
            "https://www.bilibili.com/video/BV1TEST00001/?p=1",
            "--session-id",
            session_id,
            "--credential-ref",
            str(credential),
        )
        recording = (
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/providers/bilibili/fresh-download"
        )

        interrupted, fault = _run_start_cli_with_recording(
            recording,
            *command,
            injected_fault_point="after_output_dir_publish",
        )
        self.assertEqual(60, interrupted.returncode, interrupted.stdout)
        self.assertEqual("injected_initialization_fault", fault["classification"])

        recovered, recovery = _run_start_cli_with_recording(recording, *command)
        self.assertEqual(0, recovered.returncode, recovered.stdout + recovered.stderr)
        self.assertEqual("run_initialized", recovery["classification"])
        run_id = recovery["data"]["run_id"]

        completed, envelope = _run_start_cli_with_recording(recording, *command)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("run_initialized", envelope["classification"])
        self.assertEqual(run_id, envelope["data"]["run_id"])

    @unittest.skip("Issue #90 archives production init-run")
    def test_concurrent_active_bilibili_init_preserves_both_runs_in_shared_task_index(
        self,
    ) -> None:
        from tests.video_workflow.test_issue13_platform_cutover import (
            Issue13PlatformCutoverTests,
        )

        authority_fixture = Issue13PlatformCutoverTests(
            "test_bilibili_activation_publishes_single_platform_authority"
        )
        control_store_root, exit_evidence = (
            authority_fixture._write_valid_cutover_manifest()
        )
        with sqlite3.connect(
            control_store_root / "global-gate-control.sqlite3"
        ) as connection:
            connection.execute("PRAGMA user_version=1")
        with patch(
            "src.video2pdf_workflow_kernel.platform_kernel._require_formal_exit_evidence"
        ):
            BilibiliPlatformCutoverPublisher().activate(
                platform="bilibili",
                control_store_root=control_store_root,
                exit_evidence=exit_evidence,
                activated_at="2026-08-09T00:00:00Z",
            )

        case_root = new_case_dir(self.id(), label="issue13-concurrent-init")
        project_root = case_root / "project"
        workspace_root = project_root / "workspace"
        fixture = json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "video_workflow"
                / "fixtures"
                / "contracts"
                / "bootstrap-record.v2.valid.json"
            ).read_text(encoding="utf-8")
        )
        cases = (
            {
                "run_id": "13131313131313131313131313131313",
                "session_id": "session-issue13-concurrent-a",
                "request_id": "issue-13-concurrent-a",
                "item_id": "BV1Issue13ConcurrentA",
                "source_identity": _source_identity("BV1Issue13ConcurrentA"),
                "title": "Issue 13 concurrent Bilibili A",
                "task_start": "2026-08-09T09:00:00+08:00",
            },
            {
                "run_id": "24242424242424242424242424242424",
                "session_id": "session-issue13-concurrent-b",
                "request_id": "issue-13-concurrent-b",
                "item_id": "BV1Issue13ConcurrentB",
                "source_identity": _source_identity("BV1Issue13ConcurrentB"),
                "title": "Issue 13 concurrent Bilibili B",
                "task_start": "2026-08-09T09:00:01+08:00",
            },
        )
        commands: list[tuple[str, ...]] = []
        for case in cases:
            probe = json.loads(json.dumps(fixture))
            probe.update(
                {
                    "run_id": case["run_id"],
                    "request_id": case["request_id"],
                    "task_start": case["task_start"],
                    "canonical_item_id": case["item_id"],
                    "source_identity": case["source_identity"],
                    "original_title": case["title"],
                }
            )
            probe["source_request"]["canonical_locator"] = (
                f"https://www.bilibili.com/video/{case['item_id']}"
            )
            probe_path = (
                workspace_root
                / "待删除"
                / "pipeline-bootstrap"
                / case["run_id"]
                / "probe.json"
            )
            probe_path.parent.mkdir(parents=True)
            probe_path.write_text(
                json.dumps(
                    probe,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            commands.append(
                (
                    "init-run",
                    "--workspace-root",
                    str(workspace_root),
                    "--control-store-root",
                    str(control_store_root),
                    "--probe",
                    str(probe_path),
                    "--session-id",
                    case["session_id"],
                )
            )

        barrier = threading.Barrier(2)

        def initialize(arguments: tuple[str, ...]) -> tuple[subprocess.CompletedProcess[str], dict]:
            barrier.wait()
            return _run_cli(*arguments)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_results = list(executor.map(initialize, commands))
        final_results = []
        for arguments, result in zip(commands, first_results, strict=True):
            final_results.append(result if result[0].returncode == 0 else _run_cli(*arguments))
        self.assertEqual(
            [0, 0],
            [completed.returncode for completed, _ in final_results],
            [completed.stdout + completed.stderr for completed, _ in final_results],
        )

        task_index_path = (
            project_root / ".codex" / "delivery-targets" / "task-index.json"
        )
        task_index = json.loads(task_index_path.read_text(encoding="utf-8"))
        expected_run_ids = sorted(case["run_id"] for case in cases)
        self.assertEqual(
            expected_run_ids,
            [entry["run_id"] for entry in task_index["entries"]],
        )
        self.assertEqual(2, len({entry["run_id"] for entry in task_index["entries"]}))

        index_by_run = {entry["run_id"]: entry for entry in task_index["entries"]}
        for case, (_, envelope) in zip(cases, final_results, strict=True):
            run_dir = Path(envelope["data"]["run_dir"])
            run_path = run_dir / "workflow" / "run.json"
            video_target_path = (
                run_dir / "review" / "acceptance" / "delivery_target.json"
            )
            session_target_path = (
                project_root
                / ".codex"
                / "delivery-targets"
                / "sessions"
                / case["session_id"]
                / "current.json"
            )
            run_record = json.loads(run_path.read_text(encoding="utf-8"))
            session_target = json.loads(
                session_target_path.read_text(encoding="utf-8")
            )
            entry = index_by_run[case["run_id"]]
            publication = json.loads(
                (
                    run_dir
                    / "待删除"
                    / "bootstrap"
                    / "initial-delivery-publication.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                {
                    "files": (True, True, True),
                    "run_index_sha": _canonical_sha256(
                        publication["task_index"]
                    ),
                    "run_video_sha": _sha256(video_target_path),
                    "run_session_sha": _sha256(session_target_path),
                    "session_video_sha": _sha256(video_target_path),
                    "entry_video_sha": _sha256(video_target_path),
                    "entry_session_sha": _sha256(session_target_path),
                },
                {
                    "files": (
                        run_path.is_file(),
                        video_target_path.is_file(),
                        session_target_path.is_file(),
                    ),
                    "run_index_sha": run_record["delivery"]["projections"][
                        "task_index"
                    ]["sha256"],
                    "run_video_sha": run_record["delivery"]["projections"][
                        "video_target"
                    ]["sha256"],
                    "run_session_sha": run_record["delivery"]["projections"][
                        "session_target"
                    ]["sha256"],
                    "session_video_sha": session_target["video_target"]["sha256"],
                    "entry_video_sha": entry["video_target"]["sha256"],
                    "entry_session_sha": entry["session_target"]["sha256"],
                },
            )
            authority_check, authority = _run_cli(
                "reconcile-authority",
                "--workspace-root",
                str(workspace_root),
                "--kind",
                "kernel_run",
                "--id",
                case["run_id"],
            )
            self.assertEqual(
                {
                    "returncode": 0,
                    "classification": "authority_reconciled",
                    "outcome": "current_state_verified",
                },
                {
                    "returncode": authority_check.returncode,
                    "classification": authority["classification"],
                    "outcome": authority.get("data", {}).get("outcome"),
                },
            )

if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
from contextlib import redirect_stdout


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECORDED_PROVIDER = (
    PROJECT_ROOT
    / "tests"
    / "video_workflow"
    / "fixtures"
    / "providers"
    / "bilibili"
    / "fresh-download"
)
SOURCE_URL = "https://www.bilibili.com/video/BV1TEST00001/?p=1"

from tests.video_workflow import test_issue13_platform_cutover as platform_cutover_test
from tests.video_workflow._test_run import child_environment, new_case_dir


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


def _recorded_provider_without_subtitles(case_root: Path) -> Path:
    """Rematerialize one valid replay where the probe exposes no subtitle tracks."""

    recording_root = case_root / "recorded-provider-without-subtitles"
    shutil.copytree(RECORDED_PROVIDER, recording_root)

    metadata_path = recording_root / "stdout" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["subtitles"] = {}
    metadata["automatic_captions"] = {}
    metadata_bytes = (
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    metadata_path.write_bytes(metadata_bytes)

    subtitle_list_path = recording_root / "stdout" / "subtitle-list.txt"
    subtitle_list_bytes = b"no subtitles\n"
    subtitle_list_path.write_bytes(subtitle_list_bytes)

    manifest_path = recording_root / "recording.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recording_id"] = "bilibili-fresh-download-without-subtitles-v1"
    manifest["commands"] = [
        command
        for command in manifest["commands"]
        if command["operation"] not in {"subtitle_manual", "subtitle_automatic"}
    ]
    for command in manifest["commands"]:
        if command["operation"] == "subtitle_list":
            command["stdout"]["sha256"] = hashlib.sha256(
                subtitle_list_bytes
            ).hexdigest()
        elif command["operation"] == "metadata_probe":
            command["stdout"]["sha256"] = hashlib.sha256(metadata_bytes).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return recording_root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Issue13WhisperSourceCliTests(unittest.TestCase):
    def test_attempt_cookie_replay_rejects_a_mutated_existing_working_copy(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.errors import KernelConflict
        from video2pdf_workflow_kernel.source_acquire import _localized_cookie

        case_root = new_case_dir(self.id(), label="attempt-cookie-replay")
        run_dir = case_root / "run"
        run_dir.mkdir()
        cookie = case_root / "user-cookie.txt"
        original = b"# Netscape HTTP Cookie File\nexternal-cookie\n"
        cookie.write_bytes(original)
        working = _localized_cookie(
            cookie,
            run_dir,
            attempt_id="a" * 24,
            claim_generation=1,
        )
        working.write_bytes(
            b"# Netscape HTTP Cookie File\nprovider-mutated-cookie\n"
        )

        with self.assertRaises(KernelConflict) as raised:
            _localized_cookie(
                cookie,
                run_dir,
                attempt_id="a" * 24,
                claim_generation=1,
            )

        self.assertEqual(cookie.read_bytes(), original)
        serialized = f"{raised.exception}\n{raised.exception.data}"
        self.assertNotIn("cookies.txt", serialized)
        self.assertNotIn(str(working), serialized)

    def test_source_credential_localization_rejects_a_linked_ancestor(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.source_acquire import _localized_cookie

        case_root = new_case_dir(self.id(), label="source-cookie-linked-ancestor")
        run_dir = case_root / "run"
        run_dir.mkdir()
        outside = case_root / "outside"
        outside.mkdir()
        try:
            os.symlink(outside, run_dir / "待删除", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        cookie = case_root / "cookie.txt"
        cookie.write_text("secret-cookie", encoding="utf-8")

        with self.assertRaisesRegex(ContractError, "link|reparse"):
            _localized_cookie(cookie, run_dir)

        self.assertFalse((outside / "source-acquire" / "credentials").exists())

    def test_bootstrap_credential_localization_rejects_a_linked_ancestor(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.production_bootstrap import _localize_cookie

        case_root = new_case_dir(self.id(), label="bootstrap-cookie-linked-ancestor")
        disposable = case_root / "待删除"
        disposable.mkdir()
        outside = case_root / "outside"
        outside.mkdir()
        try:
            os.symlink(outside, disposable / "bootstrap", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        cookie = case_root / "cookie.txt"
        cookie.write_text("secret-cookie", encoding="utf-8")

        with self.assertRaisesRegex(ContractError, "link|reparse"):
            _localize_cookie(
                cookie,
                disposable_root=disposable,
                credential_identity="credential-identity",
            )

        self.assertFalse((outside / "credentials").exists())

    def test_source_acquire_reports_cookie_rejection_as_recoverable_user_input(
        self,
    ) -> None:
        from video2pdf_workflow_kernel import cli
        from video2pdf_workflow_kernel.adapters import PlatformAdapterError

        blocker = {
            "kind": "user_input",
            "reason": "cookie_rejected",
            "canonical_platform": "bilibili",
            "resource_class": "bilibili_download",
            "breaker_state": "open",
            "evidence_sha256": "c" * 64,
        }
        rejected = PlatformAdapterError(
            "platform cookie was rejected",
            classification="source_authentication_required",
            exit_code=30,
            blocker_kind="user_input",
            data={
                "authentication_classification": "cookie_rejected",
                "source_blocker": blocker,
            },
        )
        with mock.patch.object(
            cli,
            "acquire_bilibili_source_for_run",
            side_effect=rejected,
        ):
            for provider_arguments in (
                (),
                ("--provider-recording", str(Path("recorded-provider"))),
            ):
                with self.subTest(provider_arguments=provider_arguments):
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        exit_code = cli.main(
                            [
                                "source-acquire",
                                "--run-dir",
                                str(Path("candidate-run")),
                                "--cookie-file",
                                str(Path("replacement-cookie.txt")),
                                *provider_arguments,
                            ]
                        )

                    envelope = json.loads(stdout.getvalue())
                    self.assertEqual(0, exit_code)
                    self.assertEqual("ok", envelope["status"])
                    self.assertEqual(
                        "user_input_required", envelope["classification"]
                    )
                    self.assertEqual(
                        blocker, envelope["data"]["source_blocker"]
                    )

    def test_source_acquire_without_recording_selects_live_runner_without_network(
        self,
    ) -> None:
        from video2pdf_workflow_kernel import source_acquire
        from video2pdf_workflow_kernel.adapters import YtDlpRuntime

        case_root = new_case_dir(self.id(), label="issue13-live-runner-selection")
        run_dir = case_root / "workspace" / "run-live-selection"
        (run_dir / "workflow").mkdir(parents=True)
        run_record = {
            "canonical_platform": "bilibili",
            "canonical_item_id": "BV1TEST00001:p1",
            "original_title": "Live Runner Selection",
            "task_start": "2026-08-11T10:00:00+08:00",
            "run_id": "run-live-selection",
            "source_identity": "a" * 64,
            "checkpoints": {"source_ready": {"status": "current"}},
        }
        run_path = run_dir / "workflow" / "run.json"
        run_path.write_text(json.dumps(run_record), encoding="utf-8")
        cookie = case_root / "cookie.txt"
        cookie.write_text("# Netscape HTTP Cookie File\nfixture\n", encoding="utf-8")
        runtime = YtDlpRuntime(
            python_executable=Path("python"),
            ffmpeg_dir=Path("ffmpeg-bin"),
            ffprobe_executable=Path("ffprobe"),
        )
        live_runner = object()
        manifest_path = run_dir / "source" / "manifest.json"

        with (
            mock.patch.object(
                source_acquire,
                "_runtime_tools",
                return_value=(runtime, {"yt-dlp": "live"}, "b" * 64),
            ) as runtime_tools,
            mock.patch.object(
                source_acquire,
                "SubprocessCommandRunner",
                return_value=live_runner,
            ) as runner_factory,
            mock.patch.object(
                source_acquire,
                "acquire_source_for_initialized_run",
                return_value=SimpleNamespace(
                    run_path=run_path,
                    manifest_path=manifest_path,
                ),
            ) as attach_source,
        ):
            result = source_acquire.acquire_bilibili_source_for_run(
                run_dir=run_dir,
                cookie_file=cookie,
                provider_recording=None,
            )

        runtime_tools.assert_called_once()
        runner_factory.assert_called_once_with()
        call = attach_source.call_args.kwargs
        self.assertIs(call["runner"], live_runner)
        self.assertEqual("live", call["provider_kind"])
        self.assertIsNone(call["recording_sha256"])
        self.assertIsNone(call["recording_evidence"])
        self.assertEqual("run-live-selection", result["run_id"])

    def test_public_source_acquire_promotes_supplied_whisper_srt_when_probe_has_no_subtitles(
        self,
    ) -> None:
        case_root = new_case_dir(self.id(), label="issue13-whisper-source-cli")
        project_root = case_root / "candidate-project"
        workspace_root = project_root / "workspace"
        workspace_root.mkdir(parents=True)
        control_store_root = case_root / "control"
        control_store_root.mkdir()
        platform_cutover_test.Issue13PlatformCutoverTests._write_stub_global_gate(
            control_store_root
        )

        recording_root = _recorded_provider_without_subtitles(case_root)
        cookie_file = case_root / "credentials" / "bilibili-cookies.txt"
        cookie_file.parent.mkdir()
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\trecorded\n",
            encoding="utf-8",
        )
        whisper_srt = case_root / "worker-output" / "transcription.srt"
        whisper_srt.parent.mkdir()
        whisper_srt.write_bytes(
            b"1\n00:00:00,000 --> 00:00:04,500\n"
            b"A generated transcript is now governed source evidence.\n\n"
            b"2\n00:00:04,500 --> 00:00:09,000\n"
            b"Every cue remains bound to the acquired video timeline.\n"
        )

        probed = _run_public_cli(
            self.id() + "-probe",
            "bootstrap-probe",
            "--workspace-root",
            str(workspace_root),
            "--platform",
            "bilibili",
            "--source-url",
            SOURCE_URL,
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(recording_root),
            "--task-start",
            "2026-08-11T10:00:00+08:00",
            "--request-id",
            "issue13-whisper-source-probe",
        )
        self.assertEqual(0, probed.returncode, probed.stdout + probed.stderr)
        probe = json.loads(probed.stdout)
        probe_path = Path(probe["data"]["probe_record"])

        implementation_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout.strip()
        prepared = _run_public_cli(
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
            "session-issue13-whisper-source",
            "--prepared-at",
            "2026-08-11T02:01:00Z",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        initialized = _run_public_cli(
            self.id() + "-init",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace_root),
            "--control-store-root",
            str(control_store_root),
            "--probe",
            str(probe_path),
            "--session-id",
            "session-issue13-whisper-source",
        )
        self.assertEqual(
            0, initialized.returncode, initialized.stdout + initialized.stderr
        )
        run_dir = Path(json.loads(initialized.stdout)["data"]["run_dir"])

        acquired = _run_public_cli(
            self.id() + "-acquire",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(recording_root),
            "--whisper-transcript",
            str(whisper_srt),
        )

        self.assertEqual(0, acquired.returncode, acquired.stdout + acquired.stderr)
        self.assertEqual(
            "source_acquired", json.loads(acquired.stdout)["classification"]
        )

        manifest_path = run_dir / "source" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        transcript = next(
            artifact
            for artifact in manifest["artifacts"]
            if artifact["origin"] == "whisper_transcription"
        )
        selected_id = manifest["selection"]["selected_subtitle_artifact_id"]

        self.assertEqual("validated", manifest["package_status"])
        self.assertEqual("pass", manifest["technical_validation"]["status"])
        self.assertEqual("used", manifest["selection"]["whisper_status"])
        self.assertEqual(selected_id, transcript["logical_id"])
        self.assertEqual("transcript", transcript["role"])
        self.assertEqual("transcript", transcript["subtitle_kind"])
        self.assertEqual("ready", run["source_state"])
        self.assertEqual("current", run["checkpoints"]["source_ready"]["status"])
        self.assertEqual(
            _sha256(manifest_path),
            run["artifact_generations"]["source_manifest"]["sha256"],
        )
        transcription_generation = run["artifact_generations"][
            "source_transcription"
        ]
        self.assertTrue(
            transcription_generation["producer"].startswith("task:"),
            "the supplied worker output must pass through Whisper Task promotion",
        )
        self.assertEqual(
            "work/source-acquisition/transcription.srt",
            transcription_generation["path"],
        )
        self.assertNotEqual(
            whisper_srt.resolve(),
            (run_dir / transcription_generation["path"]).resolve(),
            "the external SRT is worker output rather than canonical Run storage",
        )
        self.assertEqual(
            _sha256(run_dir / transcription_generation["path"]),
            transcription_generation["sha256"],
        )
        self.assertEqual(
            {
                "generation": transcription_generation["generation"],
                "sha256": transcription_generation["sha256"],
            },
            manifest["provenance"]["whisper_generation"],
        )

        external_worker_path = str(whisper_srt.resolve())
        for label, persisted_or_returned_value in (
            ("CLI envelope", acquired.stdout + acquired.stderr),
            ("Run Record", json.dumps(run, ensure_ascii=False, sort_keys=True)),
            (
                "Source Manifest",
                json.dumps(manifest, ensure_ascii=False, sort_keys=True),
            ),
        ):
            with self.subTest(path_privacy=label):
                self.assertNotIn(external_worker_path, persisted_or_returned_value)

        self.assertEqual(
            {"cover", "metadata", "transcript", "video"},
            {artifact["role"] for artifact in manifest["artifacts"]},
            "the replay must expose no platform subtitle candidate",
        )
        for artifact in manifest["artifacts"]:
            self.assertEqual(
                artifact["sha256"],
                _sha256(run_dir / artifact["path"]),
                f"{artifact['logical_id']} must be bound to the current Source Manifest",
            )

    def test_public_source_acquire_resumes_only_whisper_from_decision_ready(
        self,
    ) -> None:
        case_root = new_case_dir(self.id(), label="issue13-whisper-resume")
        project_root = case_root / "candidate-project"
        workspace_root = project_root / "workspace"
        workspace_root.mkdir(parents=True)
        control_store_root = case_root / "control"
        control_store_root.mkdir()
        platform_cutover_test.Issue13PlatformCutoverTests._write_stub_global_gate(
            control_store_root
        )
        recording_root = _recorded_provider_without_subtitles(case_root)
        cookie_file = case_root / "credentials" / "bilibili-cookies.txt"
        cookie_file.parent.mkdir()
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\trecorded\n",
            encoding="utf-8",
        )
        invalid_srt = case_root / "worker-output" / "invalid.srt"
        invalid_srt.parent.mkdir()
        invalid_srt.write_text("transcription failed", encoding="utf-8")
        valid_srt = case_root / "worker-output" / "generation-2.srt"
        valid_srt.write_bytes(
            b"1\n00:00:00,000 --> 00:00:04,500\n"
            b"The fresh Whisper generation completes the same source epoch.\n"
        )

        probed = _run_public_cli(
            self.id() + "-probe",
            "bootstrap-probe",
            "--workspace-root",
            str(workspace_root),
            "--platform",
            "bilibili",
            "--source-url",
            SOURCE_URL,
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(recording_root),
            "--task-start",
            "2026-08-11T10:00:00+08:00",
            "--request-id",
            "issue13-whisper-resume-probe",
        )
        self.assertEqual(0, probed.returncode, probed.stdout + probed.stderr)
        probe_path = Path(json.loads(probed.stdout)["data"]["probe_record"])
        implementation_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout.strip()
        prepared = _run_public_cli(
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
            "session-issue13-whisper-resume",
            "--prepared-at",
            "2026-08-11T02:01:00Z",
        )
        self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
        initialized = _run_public_cli(
            self.id() + "-init",
            "init-cutover-candidate",
            "--workspace-root",
            str(workspace_root),
            "--control-store-root",
            str(control_store_root),
            "--probe",
            str(probe_path),
            "--session-id",
            "session-issue13-whisper-resume",
        )
        self.assertEqual(0, initialized.returncode, initialized.stdout + initialized.stderr)
        run_dir = Path(json.loads(initialized.stdout)["data"]["run_dir"])
        initialized_run = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )

        faulted = _run_public_cli(
            self.id() + "-faulted",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(recording_root),
            "--whisper-transcript",
            str(invalid_srt),
        )
        self.assertNotEqual(0, faulted.returncode, faulted.stdout + faulted.stderr)
        decision_ready = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual("decision_ready", decision_ready["source_state"])
        self.assertNotIn("source_manifest", decision_ready["artifact_generations"])
        self.assertNotIn("source_ready", decision_ready["checkpoints"])

        task_records = {
            record["task_stage"]: record
            for record in (
                json.loads(path.read_text(encoding="utf-8"))
                for path in (run_dir / "workflow" / "tasks").glob("*/task.json")
            )
        }
        original_task_ids = {
            stage: record["task_id"] for stage, record in task_records.items()
        }
        original_attempt_counts = {
            stage: len(list((run_dir / "workflow" / "tasks" / record["task_id"] / "attempts").glob("*")))
            for stage, record in task_records.items()
        }
        self.assertEqual(
            {"provider_acquisition", "semantic_judgment", "whisper_transcription"},
            set(original_task_ids),
        )

        inventory_path = (
            run_dir / "work" / "source-acquisition" / "candidate-inventory.json"
        )
        inventory_bytes = inventory_path.read_bytes()
        inventory_path.write_bytes(inventory_bytes + b"\n")
        drifted_resume = _run_public_cli(
            self.id() + "-drifted-resume",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(recording_root),
            "--whisper-transcript",
            str(valid_srt),
        )
        self.assertEqual(40, drifted_resume.returncode)
        self.assertEqual(
            "artifact_drift", json.loads(drifted_resume.stdout)["classification"]
        )
        inventory_path.write_bytes(inventory_bytes)

        # scenario_id: decision_ready_skeleton_producer_split
        # target_invariant: inventory and skeleton share one committed provider Attempt
        # mutation_seam: promoted skeleton generation producer
        # rematerialized_nodes: Run bytes, latest promotion replacement, journal, row identity
        # intentionally_stale_nodes: none
        # expected_first_gate: decision-ready provider producer cohesion
        # expected_error_code: artifact_drift
        def rematerialize_skeleton_producer(producer: str) -> None:
            from video2pdf_workflow_kernel.utils import canonical_json_bytes

            run_path = run_dir / "workflow" / "run.json"
            replacement = json.loads(run_path.read_text(encoding="utf-8"))
            replacement["artifact_generations"][
                "source_acquisition_decision_skeleton"
            ]["producer"] = producer
            replacement_bytes = canonical_json_bytes(replacement)
            replacement_sha = hashlib.sha256(replacement_bytes).hexdigest()
            replacement_json = replacement_bytes.decode("utf-8")
            intent_id = replacement["last_mutation_intent_id"]
            control_store = (
                run_dir.parent / ".workflow-control" / "control.sqlite3"
            )
            with sqlite3.connect(control_store) as database:
                row = database.execute(
                    "SELECT i.task_id,i.attempt_id,i.outputs_json,"
                    "c.envelope_sha256,a.completion_sha256 "
                    "FROM task_promotion_intents i "
                    "JOIN task_claims c ON c.task_id=i.task_id "
                    "JOIN task_attempts a ON a.attempt_id=i.attempt_id "
                    "WHERE i.intent_id=? AND i.state='COMMITTED'",
                    (intent_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                task_id, attempt_id, outputs_json, envelope_sha, completion_sha = row
                outputs_sha = hashlib.sha256(outputs_json.encode("utf-8")).hexdigest()
                intent_identity = hashlib.sha256(
                    "\0".join(
                        (
                            "task_promotion_intent_row_v2",
                            intent_id,
                            replacement_sha,
                            outputs_sha,
                            envelope_sha,
                            completion_sha,
                        )
                    ).encode("utf-8")
                ).hexdigest()
                journal_path = (
                    run_dir
                    / "workflow"
                    / "tasks"
                    / task_id
                    / "attempts"
                    / attempt_id
                    / "p.json"
                )
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                journal["replacement_run_record_sha256"] = replacement_sha
                journal_bytes = canonical_json_bytes(journal)
                journal_path.write_bytes(journal_bytes)
                database.execute(
                    "UPDATE task_promotion_intents SET "
                    "replacement_run_record_json=?, replacement_run_record_sha256=?, "
                    "journal_sha256=?, intent_identity=? WHERE intent_id=?",
                    (
                        replacement_json,
                        replacement_sha,
                        hashlib.sha256(journal_bytes).hexdigest(),
                        intent_identity,
                        intent_id,
                    ),
                )
            run_path.write_bytes(replacement_bytes)

        inventory_producer = decision_ready["artifact_generations"][
            "source_candidate_inventory"
        ]["producer"]
        skeleton_producer = decision_ready["artifact_generations"][
            "source_acquisition_decision_skeleton"
        ]["producer"]
        semantic_producer = decision_ready["artifact_generations"][
            "source_acquisition_decision"
        ]["producer"]
        self.assertEqual(inventory_producer, skeleton_producer)
        rematerialize_skeleton_producer(semantic_producer)
        split_provider_resume = _run_public_cli(
            self.id() + "-split-provider-resume",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(recording_root),
            "--whisper-transcript",
            str(valid_srt),
        )
        self.assertEqual(40, split_provider_resume.returncode)
        split_error = json.loads(split_provider_resume.stdout)
        self.assertEqual("artifact_drift", split_error["classification"])
        self.assertIn(
            "provider Task producer cohesion",
            split_error["data"]["message"],
        )
        rematerialize_skeleton_producer(skeleton_producer)

        reconciled = _run_public_cli(
            self.id() + "-reconcile",
            "source-acquire-reconcile",
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stdout + reconciled.stderr)
        self.assertEqual(1, json.loads(reconciled.stdout)["data"]["tasks_advanced"])

        resumed = _run_public_cli(
            self.id() + "-resume",
            "source-acquire",
            "--run-dir",
            str(run_dir),
            "--cookie-file",
            str(cookie_file),
            "--provider-recording",
            str(recording_root),
            "--whisper-transcript",
            str(valid_srt),
        )
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        final_run = json.loads(
            (run_dir / "workflow" / "run.json").read_text(encoding="utf-8")
        )
        final_task_records = {
            record["task_stage"]: record
            for record in (
                json.loads(path.read_text(encoding="utf-8"))
                for path in (run_dir / "workflow" / "tasks").glob("*/task.json")
            )
        }
        self.assertEqual(
            original_task_ids,
            {stage: record["task_id"] for stage, record in final_task_records.items()},
        )
        self.assertEqual(initialized_run["run_id"], final_run["run_id"])
        self.assertEqual(initialized_run["source_identity"], final_run["source_identity"])
        self.assertEqual(initialized_run["source_epoch"], final_run["source_epoch"])
        self.assertEqual("ready", final_run["source_state"])
        self.assertEqual("current", final_run["checkpoints"]["source_ready"]["status"])
        self.assertEqual(
            original_attempt_counts["provider_acquisition"],
            len(list((run_dir / "workflow" / "tasks" / original_task_ids["provider_acquisition"] / "attempts").glob("*"))),
            "resume must not invoke or reclaim the provider Task",
        )
        self.assertEqual(
            original_attempt_counts["semantic_judgment"],
            len(list((run_dir / "workflow" / "tasks" / original_task_ids["semantic_judgment"] / "attempts").glob("*"))),
            "resume must not invoke or reclaim the semantic Task",
        )
        self.assertEqual(
            original_attempt_counts["whisper_transcription"] + 1,
            len(list((run_dir / "workflow" / "tasks" / original_task_ids["whisper_transcription"] / "attempts").glob("*"))),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from datetime import datetime
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts/persisted_command.py"


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


class PersistedCommandCliTests(unittest.TestCase):
    def test_reconcile_marks_mismatch_and_insufficient_identity_unknown(self) -> None:
        cases = (
            (
                "creation mismatch",
                "synthetic-process-creation-identity",
                "target_process_creation_mismatch",
            ),
            ("identity missing", None, "target_identity_incomplete"),
        )
        for label, persisted_creation_identity, expected_reason in cases:
            with self.subTest(label=label):
                child = (
                    "import time; "
                    "print('alive', flush=True); "
                    "time.sleep(3); "
                    "print('finished', flush=True)"
                )
                started = run_cli(
                    "start",
                    "--task-name",
                    f"reconcile {label} {uuid.uuid4().hex}",
                    "--",
                    sys.executable,
                    "-X",
                    "utf8",
                    "-c",
                    child,
                )
                self.assertEqual(
                    started.returncode,
                    0,
                    started.stderr or started.stdout,
                )
                run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

                ready_deadline = time.monotonic() + 10
                running_status = None
                while time.monotonic() < ready_deadline:
                    shown = run_cli("show", "--run-dir", str(run_dir))
                    self.assertEqual(
                        shown.returncode,
                        0,
                        shown.stderr or shown.stdout,
                    )
                    candidate = json.loads(shown.stdout)["data"]["status"]
                    if (candidate.get("target_identity") or {}).get(
                        "process_creation_identity"
                    ):
                        running_status = candidate
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(running_status)
                assert running_status is not None

                running_status["target_identity"][
                    "process_creation_identity"
                ] = persisted_creation_identity
                (run_dir / "status.json").write_text(
                    json.dumps(
                        running_status,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )

                reconciled = run_cli("reconcile", "--run-dir", str(run_dir))
                self.assertEqual(
                    reconciled.returncode,
                    0,
                    reconciled.stderr or reconciled.stdout,
                )
                reconciled_data = json.loads(reconciled.stdout)["data"]
                self.assertEqual(reconciled_data["status"]["state"], "unknown")
                self.assertEqual(
                    reconciled_data["reconciliation"]["reason"],
                    expected_reason,
                )
                self.assertEqual(
                    reconciled_data["reconciliation"]["observed_target_identity"][
                        "pid"
                    ],
                    running_status["target_identity"]["pid"],
                )

                completion_deadline = time.monotonic() + 10
                completed_status = None
                while time.monotonic() < completion_deadline:
                    shown = run_cli("show", "--run-dir", str(run_dir))
                    self.assertEqual(
                        shown.returncode,
                        0,
                        shown.stderr or shown.stdout,
                    )
                    candidate = json.loads(shown.stdout)["data"]["status"]
                    if candidate["state"] == "succeeded":
                        completed_status = candidate
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(completed_status)
                self.assertEqual(
                    (run_dir / "stdout.log").read_text(encoding="utf-8"),
                    "alive\nfinished\n",
                )

    def test_reconcile_marks_proven_missing_target_interrupted(self) -> None:
        stop_marker = (
            PROJECT_ROOT
            / "待删除/process-reconcile-markers"
            / f"{uuid.uuid4().hex}.stop"
        )
        stop_marker.parent.mkdir(parents=True, exist_ok=True)
        child = (
            "import pathlib,sys,time\n"
            "stop = pathlib.Path(sys.argv[1])\n"
            "print('waiting', flush=True)\n"
            "while not stop.exists():\n"
            "    time.sleep(0.05)\n"
        )
        started = run_cli(
            "start",
            "--task-name",
            f"reconcile interrupted {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
            str(stop_marker),
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        try:
            ready_deadline = time.monotonic() + 10
            running_status = None
            while time.monotonic() < ready_deadline:
                shown = run_cli("show", "--run-dir", str(run_dir))
                self.assertEqual(shown.returncode, 0, shown.stderr or shown.stdout)
                candidate = json.loads(shown.stdout)["data"]["status"]
                if (candidate.get("target_identity") or {}).get(
                    "process_creation_identity"
                ):
                    running_status = candidate
                    break
                time.sleep(0.05)
            self.assertIsNotNone(running_status)
            assert running_status is not None

            os.kill(running_status["supervisor_pid"], signal.SIGTERM)
            stop_marker.write_text("stop\n", encoding="utf-8")

            reconcile_deadline = time.monotonic() + 10
            reconciled_data = None
            while time.monotonic() < reconcile_deadline:
                reconciled = run_cli("reconcile", "--run-dir", str(run_dir))
                self.assertEqual(
                    reconciled.returncode,
                    0,
                    reconciled.stderr or reconciled.stdout,
                )
                candidate = json.loads(reconciled.stdout)["data"]
                if candidate["status"]["state"] == "interrupted":
                    reconciled_data = candidate
                    break
                self.assertEqual(candidate["status"]["state"], "running")
                time.sleep(0.05)
            self.assertIsNotNone(reconciled_data)
            assert reconciled_data is not None
            self.assertEqual(
                reconciled_data["reconciliation"]["reason"],
                "target_process_missing",
            )
            persisted = json.loads(
                (run_dir / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["state"], "interrupted")
            self.assertEqual(persisted["reconciliation"]["decision"], "interrupted")
        finally:
            stop_marker.write_text("stop\n", encoding="utf-8")

    def test_reconcile_preserves_matching_live_target_without_mutation(self) -> None:
        child = (
            "import time; "
            "print('born', flush=True); "
            "time.sleep(4); "
            "print('finished', flush=True)"
        )
        started = run_cli(
            "start",
            "--task-name",
            f"reconcile live {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        ready_deadline = time.monotonic() + 10
        running_status = None
        while time.monotonic() < ready_deadline:
            shown = run_cli("show", "--run-dir", str(run_dir))
            self.assertEqual(shown.returncode, 0, shown.stderr or shown.stdout)
            candidate = json.loads(shown.stdout)["data"]["status"]
            if (candidate.get("target_identity") or {}).get(
                "process_creation_identity"
            ):
                running_status = candidate
                break
            time.sleep(0.05)
        self.assertIsNotNone(running_status)
        assert running_status is not None
        status_before = (run_dir / "status.json").read_bytes()

        reconciled = run_cli("reconcile", "--run-dir", str(run_dir))
        self.assertEqual(
            reconciled.returncode,
            0,
            reconciled.stderr or reconciled.stdout,
        )
        reconciled_data = json.loads(reconciled.stdout)["data"]
        self.assertEqual(reconciled_data["status"]["state"], "running")
        self.assertEqual(
            reconciled_data["status"]["target_identity"],
            running_status["target_identity"],
        )
        self.assertEqual(reconciled_data["reconciliation"]["decision"], "running")
        self.assertEqual((run_dir / "status.json").read_bytes(), status_before)

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "10",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        self.assertEqual((run_dir / "stdout.log").read_text(encoding="utf-8"), "born\nfinished\n")

    def test_status_heartbeats_during_output_silence_with_identity_telemetry(self) -> None:
        child = (
            "import sys,time; "
            "print('heartbeat-out', flush=True); "
            "print('heartbeat-error', file=sys.stderr, flush=True); "
            "time.sleep(38)"
        )
        started = run_cli(
            "start",
            "--task-name",
            f"heartbeat identity {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        ready_deadline = time.monotonic() + 10
        initial_status = None
        while time.monotonic() < ready_deadline:
            shown = run_cli("show", "--run-dir", str(run_dir))
            self.assertEqual(shown.returncode, 0, shown.stderr or shown.stdout)
            candidate = json.loads(shown.stdout)["data"]["status"]
            target_identity = candidate.get("target_identity") or {}
            if candidate.get("heartbeat_at") and target_identity.get(
                "process_creation_identity"
            ):
                initial_status = candidate
                break
            time.sleep(0.05)
        self.assertIsNotNone(initial_status)
        assert initial_status is not None

        initial_heartbeat = datetime.fromisoformat(initial_status["heartbeat_at"])
        time.sleep(27)
        heartbeat_deadline = time.monotonic() + 4
        refreshed_status = None
        while time.monotonic() < heartbeat_deadline:
            shown = run_cli("show", "--run-dir", str(run_dir))
            self.assertEqual(shown.returncode, 0, shown.stderr or shown.stdout)
            candidate = json.loads(shown.stdout)["data"]["status"]
            heartbeat_at = candidate.get("heartbeat_at")
            if heartbeat_at and heartbeat_at != initial_status["heartbeat_at"]:
                refreshed_status = candidate
                break
            time.sleep(0.1)
        self.assertIsNotNone(refreshed_status)
        assert refreshed_status is not None

        refreshed_heartbeat = datetime.fromisoformat(refreshed_status["heartbeat_at"])
        self.assertLessEqual(
            (refreshed_heartbeat - initial_heartbeat).total_seconds(),
            30,
        )
        self.assertEqual(refreshed_status["state"], "running")
        self.assertGreaterEqual(refreshed_status["elapsed_seconds"], 25)
        self.assertIsNotNone(refreshed_status["latest_output_at"])
        self.assertEqual(
            refreshed_status["log_sizes"],
            {
                "stdout": (run_dir / "stdout.log").stat().st_size,
                "stderr": (run_dir / "stderr.log").stat().st_size,
                "merged": (run_dir / "command.log").stat().st_size,
            },
        )

        self.assertEqual(
            refreshed_status["supervisor_identity"]["pid"],
            refreshed_status["supervisor_pid"],
        )
        self.assertIsNotNone(
            refreshed_status["supervisor_identity"]["process_creation_identity"]
        )
        self.assertEqual(
            refreshed_status["target_identity"]["pid"],
            refreshed_status["child_pid"],
        )
        self.assertIsNotNone(
            refreshed_status["target_identity"]["process_creation_identity"]
        )

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "15",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        final_status = json.loads(waited.stdout)["data"]["status"]
        self.assertEqual(final_status["state"], "succeeded")
        self.assertGreaterEqual(final_status["elapsed_seconds"], 38)

    def test_start_returns_durable_identity_and_command_outlives_launcher(self) -> None:
        task_name = f"detached/success {uuid.uuid4().hex}"
        marker = PROJECT_ROOT / "待删除/long-running-test-markers" / f"{uuid.uuid4().hex}.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        child = (
            "import pathlib,sys,time; "
            "time.sleep(2); "
            "pathlib.Path(sys.argv[1]).write_text('finished', encoding='utf-8')"
        )

        started_at = time.monotonic()
        completed = run_cli(
            "start",
            "--task-name",
            task_name,
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
            str(marker),
        )
        elapsed = time.monotonic() - started_at

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertLess(elapsed, 1.2)
        self.assertFalse(marker.exists())
        payload = json.loads(completed.stdout)
        run_id = payload["data"]["run_id"]
        run_dir = Path(payload["data"]["run_dir"])
        self.assertEqual(len(run_id), 36)
        self.assertTrue(run_dir.is_relative_to(PROJECT_ROOT / "待删除/long-running"))
        self.assertTrue(run_dir.name.endswith(f"_{run_id[:8]}"))
        self.assertIn("detached_success", run_dir.name)

        command = json.loads((run_dir / "command.json").read_text(encoding="utf-8"))
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(command["schema_name"], "persisted-command")
        self.assertEqual(command["schema_version"], "1.0.0")
        self.assertEqual(command["run_id"], run_id)
        self.assertEqual(status["schema_name"], "persisted-command-status")
        self.assertEqual(status["schema_version"], "1.0.0")
        self.assertEqual(status["run_id"], run_id)
        for filename in ("stdout.log", "stderr.log", "command.log"):
            self.assertTrue((run_dir / filename).is_file(), filename)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.05)
        self.assertEqual(marker.read_text(encoding="utf-8"), "finished")

    def test_show_and_wait_read_successful_result_with_complete_streams(self) -> None:
        child = (
            "import sys,time; "
            "print('stdout-one', flush=True); time.sleep(0.1); "
            "print('stderr-one', file=sys.stderr, flush=True); time.sleep(0.1); "
            "print('stdout-two', flush=True); time.sleep(0.1); "
            "print('stderr-two', file=sys.stderr, flush=True)"
        )
        started = run_cli(
            "start",
            "--task-name",
            f"stream order {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        shown = run_cli("show", "--run-dir", str(run_dir))
        self.assertEqual(shown.returncode, 0, shown.stderr or shown.stdout)
        shown_data = json.loads(shown.stdout)["data"]
        self.assertEqual(shown_data["command"]["run_id"], shown_data["status"]["run_id"])
        self.assertIn(shown_data["status"]["state"], {"running", "succeeded"})

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "20",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        waited_data = json.loads(waited.stdout)["data"]
        self.assertEqual(waited_data["status"]["state"], "succeeded")
        self.assertEqual(waited_data["status"]["exit_code"], 0)
        self.assertEqual((run_dir / "exit-code.txt").read_text(encoding="utf-8"), "0\n")
        self.assertEqual(
            (run_dir / "stdout.log").read_text(encoding="utf-8"),
            "stdout-one\nstdout-two\n",
        )
        self.assertEqual(
            (run_dir / "stderr.log").read_text(encoding="utf-8"),
            "stderr-one\nstderr-two\n",
        )
        merged = (run_dir / "command.log").read_text(encoding="utf-8")
        expected_entries = [
            "[stdout] stdout-one",
            "[stderr] stderr-one",
            "[stdout] stdout-two",
            "[stderr] stderr-two",
        ]
        positions = [merged.index(entry) for entry in expected_entries]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()

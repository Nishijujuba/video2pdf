from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI = PROJECT_ROOT / "scripts/persisted_command.py"


def run_cli(
    *arguments: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(CLI), *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )


class PersistedCommandCliTests(unittest.TestCase):
    def test_log_persistence_failure_fails_closed_with_sanitized_information(self) -> None:
        secret = f"sensitive-log-error-{uuid.uuid4().hex}"
        hook_dir = (
            PROJECT_ROOT
            / "待删除/persisted-command-test-hooks"
            / uuid.uuid4().hex
        )
        hook_dir.mkdir(parents=True)
        (hook_dir / "sitecustomize.py").write_text(
            "import io\n"
            "import sys\n"
            "original_open = io.open\n"
            "def injected_open(file, *args, **kwargs):\n"
            "    mode = args[0] if args else kwargs.get('mode', 'r')\n"
            "    if '_supervise' in sys.argv and str(file).endswith('stdout.log') and 'ab' in mode:\n"
            f"        raise OSError({secret!r})\n"
            "    return original_open(file, *args, **kwargs)\n"
            "io.open = injected_open\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(hook_dir), env.get("PYTHONPATH")) if part
        )
        started = run_cli(
            "start",
            "--task-name",
            f"log persistence failure {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "print('child exited successfully', flush=True)",
            env=env,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "5",
        )

        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        status = json.loads(waited.stdout)["data"]["status"]
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["exit_code"], 0)
        self.assertEqual(
            status["failure"],
            {
                "kind": "log_persistence_failed",
                "message": "one or more command logs could not be persisted",
            },
        )
        self.assertNotIn(secret, json.dumps(status["failure"]))
        self.assertEqual((run_dir / "exit-code.txt").read_text(encoding="utf-8"), "0\n")

    def test_missing_executable_is_launch_failed_without_exit_code(self) -> None:
        secret = f"must-not-leak-{uuid.uuid4().hex}"
        started = run_cli(
            "start",
            "--task-name",
            f"launch failure {uuid.uuid4().hex}",
            "--",
            f"missing-executable-{secret}",
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "5",
        )

        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        data = json.loads(waited.stdout)["data"]
        self.assertEqual(data["status"]["state"], "launch_failed")
        self.assertIsNone(data["status"]["exit_code"])
        self.assertEqual(
            data["status"]["failure"],
            {
                "kind": "child_launch_failed",
                "message": "target process could not be launched",
            },
        )
        self.assertNotIn(secret, json.dumps(data["status"]["failure"]))
        self.assertIsNone(data["exit_code_path"])
        self.assertFalse((run_dir / "exit-code.txt").exists())

    def test_declared_nonzero_exit_is_succeeded_and_persisted(self) -> None:
        started = run_cli(
            "start",
            "--task-name",
            f"accepted nonzero {uuid.uuid4().hex}",
            "--accepted-exit-code",
            "7",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "raise SystemExit(7)",
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        reclassification = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--accepted-exit-code",
            "0",
        )
        self.assertEqual(reclassification.returncode, 2)
        command = json.loads((run_dir / "command.json").read_text(encoding="utf-8"))
        self.assertEqual(command["accepted_exit_codes"], [7])

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "20",
        )

        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        data = json.loads(waited.stdout)["data"]
        self.assertEqual(data["command"]["accepted_exit_codes"], [7])
        self.assertEqual(data["status"]["state"], "succeeded")
        self.assertEqual(data["status"]["exit_code"], 7)
        self.assertEqual((run_dir / "exit-code.txt").read_text(encoding="utf-8"), "7\n")

    def test_unexpected_nonzero_exit_is_failed_with_actual_code(self) -> None:
        started = run_cli(
            "start",
            "--task-name",
            f"unexpected nonzero {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "raise SystemExit(7)",
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "20",
        )

        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        status = json.loads(waited.stdout)["data"]["status"]
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["exit_code"], 7)
        self.assertEqual((run_dir / "exit-code.txt").read_text(encoding="utf-8"), "7\n")

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
        self.assertEqual(command["accepted_exit_codes"], [0])
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

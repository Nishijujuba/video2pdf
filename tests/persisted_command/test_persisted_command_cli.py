from __future__ import annotations

import json
from pathlib import Path
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
    def test_rerun_preserves_terminal_record_and_complete_logs(self) -> None:
        task_name = f"rerun retention {uuid.uuid4().hex}"
        child = "import sys; print('kept-out'); print('kept-error', file=sys.stderr)"

        def start_and_wait() -> dict[str, str]:
            started = run_cli(
                "start",
                "--task-name",
                task_name,
                "--",
                sys.executable,
                "-X",
                "utf8",
                "-c",
                child,
            )
            self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
            data = json.loads(started.stdout)["data"]
            waited = run_cli(
                "wait",
                "--run-dir",
                data["run_dir"],
                "--timeout-seconds",
                "10",
            )
            self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
            return data

        first = start_and_wait()
        first_dir = Path(first["run_dir"])
        retained_names = (
            "command.json",
            "status.json",
            "stdout.log",
            "stderr.log",
            "command.log",
            "exit-code.txt",
        )
        first_snapshot = {
            name: (first_dir / name).read_bytes() for name in retained_names
        }

        second = start_and_wait()
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertNotEqual(first["run_dir"], second["run_dir"])
        self.assertEqual(
            {name: (first_dir / name).read_bytes() for name in retained_names},
            first_snapshot,
        )

        shown = run_cli("show", "--run-dir", first["run_dir"])
        self.assertEqual(shown.returncode, 0, shown.stderr or shown.stdout)
        shown_data = json.loads(shown.stdout)["data"]
        self.assertEqual(shown_data["run_id"], first["run_id"])
        self.assertEqual(shown_data["task_name"], task_name)
        self.assertEqual(shown_data["state"], "succeeded")
        self.assertEqual(shown_data["exit_code"], 0)
        self.assertEqual(
            Path(shown_data["evidence_paths"]["stdout"]).read_text(encoding="utf-8"),
            "kept-out\n",
        )
        self.assertEqual(
            Path(shown_data["evidence_paths"]["stderr"]).read_text(encoding="utf-8"),
            "kept-error\n",
        )

    def test_list_discovers_concurrent_same_name_runs_with_complete_summaries(self) -> None:
        task_name = f"concurrent history {uuid.uuid4().hex}"
        child = "import time; time.sleep(0.2); print('complete', flush=True)"
        command = [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(CLI),
            "start",
            "--task-name",
            task_name,
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
        ]

        while time.time() % 1 > 0.5:
            time.sleep(0.01)
        launchers = [
            subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            for _ in range(2)
        ]
        started = [launcher.communicate(timeout=10) for launcher in launchers]
        self.assertEqual([launcher.returncode for launcher in launchers], [0, 0], started)
        start_data = [json.loads(stdout)["data"] for stdout, _ in started]
        self.assertEqual(len({item["run_id"] for item in start_data}), 2)
        self.assertEqual(len({item["run_dir"] for item in start_data}), 2)
        directory_names = [Path(item["run_dir"]).name for item in start_data]
        timestamps = [name.rsplit("_", 3)[-3:-1] for name in directory_names]
        self.assertEqual(timestamps[0], timestamps[1])

        for item in start_data:
            waited = run_cli(
                "wait",
                "--run-dir",
                item["run_dir"],
                "--timeout-seconds",
                "10",
            )
            self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)

        listed = run_cli("list")
        self.assertEqual(listed.returncode, 0, listed.stderr or listed.stdout)
        matching = {
            item["run_id"]: item
            for item in json.loads(listed.stdout)["data"]["runs"]
            if item["task_name"] == task_name
        }
        self.assertEqual(set(matching), {item["run_id"] for item in start_data})
        for item in matching.values():
            self.assertEqual(item["state"], "succeeded")
            self.assertEqual(item["exit_code"], 0)
            self.assertIsInstance(item["process_identity"]["supervisor_pid"], int)
            self.assertIsInstance(item["process_identity"]["child_pid"], int)
            self.assertTrue(Path(item["evidence_paths"]["command"]).is_file())
            self.assertTrue(Path(item["evidence_paths"]["status"]).is_file())
            self.assertTrue(Path(item["evidence_paths"]["stdout"]).is_file())
            self.assertTrue(Path(item["evidence_paths"]["stderr"]).is_file())
            self.assertTrue(Path(item["evidence_paths"]["merged"]).is_file())
            self.assertTrue(Path(item["evidence_paths"]["exit_code"]).is_file())

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

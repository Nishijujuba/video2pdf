from __future__ import annotations

import json
from datetime import datetime
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Callable
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


def run_with_supervisor_hook(
    *,
    task_name: str,
    sitecustomize_source: str,
) -> tuple[Path, dict[str, object]]:
    hook_dir = (
        PROJECT_ROOT
        / "待删除/persisted-command-test-hooks"
        / uuid.uuid4().hex
    )
    hook_dir.mkdir(parents=True)
    (hook_dir / "sitecustomize.py").write_text(
        sitecustomize_source,
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(hook_dir), env.get("PYTHONPATH")) if part
    )
    started = run_cli(
        "start",
        "--task-name",
        task_name,
        "--",
        sys.executable,
        "-X",
        "utf8",
        "-c",
        "print('child exited successfully', flush=True)",
        env=env,
    )
    if started.returncode != 0:
        raise AssertionError(started.stderr or started.stdout)
    run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])
    waited = run_cli(
        "wait",
        "--run-dir",
        str(run_dir),
        "--timeout-seconds",
        "20",
    )
    if waited.returncode != 0:
        raise AssertionError(waited.stderr or waited.stdout)
    return run_dir, json.loads(waited.stdout)["data"]["status"]


class PersistedCommandCliTests(unittest.TestCase):
    def test_log_close_failure_also_fails_closed(self) -> None:
        secret = f"sensitive-log-close-{uuid.uuid4().hex}"
        _run_dir, status = run_with_supervisor_hook(
            task_name=f"log close failure {uuid.uuid4().hex}",
            sitecustomize_source=(
            "import io\n"
            "import sys\n"
            "original_open = io.open\n"
            "class CloseFailure:\n"
            "    def __init__(self, wrapped): self.wrapped = wrapped\n"
            "    def __enter__(self): return self\n"
            "    def __exit__(self, *ignored): self.close()\n"
            "    def __getattr__(self, name): return getattr(self.wrapped, name)\n"
            "    def close(self):\n"
            "        self.wrapped.close()\n"
            f"        raise OSError({secret!r})\n"
            "def injected_open(file, *args, **kwargs):\n"
            "    opened = original_open(file, *args, **kwargs)\n"
            "    mode = args[0] if args else kwargs.get('mode', 'r')\n"
            "    if '_supervise' in sys.argv and str(file).endswith('stdout.log') and 'ab' in mode:\n"
            "        return CloseFailure(opened)\n"
            "    return opened\n"
            "io.open = injected_open\n"
            ),
        )
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["exit_code"], 0)
        self.assertEqual(status["failure"]["kind"], "log_persistence_failed")
        self.assertNotIn(secret, json.dumps(status["failure"]))

    def test_log_persistence_failure_fails_closed_with_sanitized_information(self) -> None:
        secret = f"sensitive-log-error-{uuid.uuid4().hex}"
        run_dir, status = run_with_supervisor_hook(
            task_name=f"log persistence failure {uuid.uuid4().hex}",
            sitecustomize_source=(
            "import io\n"
            "import sys\n"
            "original_open = io.open\n"
            "def injected_open(file, *args, **kwargs):\n"
            "    mode = args[0] if args else kwargs.get('mode', 'r')\n"
            "    if '_supervise' in sys.argv and str(file).endswith('stdout.log') and 'ab' in mode:\n"
            f"        raise OSError({secret!r})\n"
            "    return original_open(file, *args, **kwargs)\n"
            "io.open = injected_open\n"
            ),
        )
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

    def test_reconcile_during_natural_exit_preserves_terminal_result(self) -> None:
        child = "import time; time.sleep(1); print('complete', flush=True)"
        started = run_cli(
            "start",
            "--task-name",
            f"reconcile completion race {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        completion_deadline = time.monotonic() + 10
        terminal_data = None
        while time.monotonic() < completion_deadline:
            reconciled = run_cli("reconcile", "--run-dir", str(run_dir))
            self.assertEqual(
                reconciled.returncode,
                0,
                reconciled.stderr or reconciled.stdout,
            )
            candidate = json.loads(reconciled.stdout)["data"]
            if candidate["status"]["state"] == "succeeded":
                terminal_data = candidate
                break
            self.assertIn(
                candidate["status"]["state"],
                {"running", "interrupted", "unknown"},
            )
            time.sleep(0.02)
        self.assertIsNotNone(terminal_data)

        status_before = (run_dir / "status.json").read_bytes()
        repeated = run_cli("reconcile", "--run-dir", str(run_dir))
        self.assertEqual(repeated.returncode, 0, repeated.stderr or repeated.stdout)
        self.assertEqual(
            json.loads(repeated.stdout)["data"]["status"]["state"],
            "succeeded",
        )
        self.assertEqual((run_dir / "status.json").read_bytes(), status_before)

    def test_status_heartbeats_after_target_closes_output_pipes(self) -> None:
        if os.name == "nt":
            target_command = [
                os.environ["COMSPEC"],
                "/d",
                "/s",
                "/c",
                "ping -n 39 127.0.0.1 >NUL 2>&1",
            ]
        else:
            target_command = [
                "/bin/sh",
                "-c",
                "exec >/dev/null 2>&1; sleep 38",
            ]
        started = run_cli(
            "start",
            "--task-name",
            f"closed pipes heartbeat {uuid.uuid4().hex}",
            "--",
            *target_command,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        initial_status = self._wait_for_status(
            run_dir,
            lambda status: bool(status.get("heartbeat_at")),
        )

        time.sleep(27)
        refreshed_status = self._wait_for_status(
            run_dir,
            lambda status: status.get("heartbeat_at")
            != initial_status["heartbeat_at"],
            timeout_seconds=4,
            poll_seconds=0.1,
        )
        self.assertEqual(refreshed_status["state"], "running")

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "15",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)

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

                running_status = self._wait_for_status(
                    run_dir,
                    lambda status: bool(
                        (status.get("target_identity") or {}).get(
                            "process_creation_identity"
                        )
                    ),
                )

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

                self._wait_for_status(
                    run_dir,
                    lambda status: status["state"] == "succeeded",
                )
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
            running_status = self._wait_for_status(
                run_dir,
                lambda status: bool(
                    (status.get("target_identity") or {}).get(
                        "process_creation_identity"
                    )
                ),
            )

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

            status_before = (run_dir / "status.json").read_bytes()
            repeated = run_cli("reconcile", "--run-dir", str(run_dir))
            self.assertEqual(
                repeated.returncode,
                0,
                repeated.stderr or repeated.stdout,
            )
            self.assertEqual(
                json.loads(repeated.stdout)["data"]["status"]["state"],
                "interrupted",
            )
            self.assertEqual((run_dir / "status.json").read_bytes(), status_before)
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

        running_status = self._wait_for_status(
            run_dir,
            lambda status: bool(
                (status.get("target_identity") or {}).get(
                    "process_creation_identity"
                )
            ),
        )
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
        self.assertEqual(
            (run_dir / "stdout.log").read_text(encoding="utf-8"),
            "born\nfinished\n",
        )

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

        initial_status = self._wait_for_status(
            run_dir,
            lambda status: bool(
                status.get("heartbeat_at")
                and (status.get("target_identity") or {}).get(
                    "process_creation_identity"
                )
            ),
        )

        initial_heartbeat = datetime.fromisoformat(initial_status["heartbeat_at"])
        time.sleep(27)
        refreshed_status = self._wait_for_status(
            run_dir,
            lambda status: bool(
                status.get("heartbeat_at")
                and status["heartbeat_at"] != initial_status["heartbeat_at"]
            ),
            timeout_seconds=4,
            poll_seconds=0.1,
        )

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

    def _wait_for_status(
        self,
        run_dir: Path,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout_seconds: float = 10,
        poll_seconds: float = 0.05,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_status = None
        while time.monotonic() < deadline:
            shown = run_cli("show", "--run-dir", str(run_dir))
            self.assertEqual(shown.returncode, 0, shown.stderr or shown.stdout)
            last_status = json.loads(shown.stdout)["data"]["status"]
            if predicate(last_status):
                return last_status
            time.sleep(poll_seconds)
        self.fail(f"status predicate timed out; last status: {last_status}")

if __name__ == "__main__":
    unittest.main()

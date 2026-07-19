from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
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


def run_to_terminal(
    *start_arguments: str,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 20,
) -> tuple[
    subprocess.CompletedProcess[str],
    subprocess.CompletedProcess[str],
    Path,
    dict[str, Any],
]:
    started = run_cli(*start_arguments, env=env)
    if started.returncode != 0:
        raise AssertionError(started.stderr or started.stdout)
    run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])
    waited = run_cli(
        "wait",
        "--run-dir",
        str(run_dir),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    if waited.returncode != 0:
        raise AssertionError(waited.stderr or waited.stdout)
    return started, waited, run_dir, json.loads(waited.stdout)["data"]


def shareable_metadata(
    run_dir: Path,
    *responses: subprocess.CompletedProcess[str],
) -> str:
    return "\n".join(
        (
            *(response.stdout for response in responses),
            (run_dir / "command.json").read_text(encoding="utf-8"),
            (run_dir / "status.json").read_text(encoding="utf-8"),
        )
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
    _started, _waited, run_dir, data = run_to_terminal(
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
    return run_dir, data["status"]


class PersistedCommandCliTests(unittest.TestCase):
    def test_supervisor_handoff_failure_terminalizes_with_sanitized_evidence(
        self,
    ) -> None:
        secret = f"handoff-error-{uuid.uuid4().hex}"
        hook_dir = (
            PROJECT_ROOT
            / "待删除/persisted-command-test-hooks"
            / uuid.uuid4().hex
        )
        hook_dir.mkdir(parents=True)
        (hook_dir / "sitecustomize.py").write_text(
            "import json\n"
            "import sys\n"
            "if '_supervise' in sys.argv:\n"
            "    def injected_load(_stream):\n"
            f"        raise OSError({secret!r})\n"
            "    json.load = injected_load\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(hook_dir), env.get("PYTHONPATH")) if part
        )

        started, waited, run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"handoff failure {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "print('must not launch')",
            env=env,
            timeout_seconds=5,
        )
        self.assertEqual(data["status"]["state"], "launch_failed")
        self.assertEqual(
            data["status"]["failure"],
            {
                "kind": "supervisor_handoff_failed",
                "message": "target launch request could not be received",
            },
        )
        self.assertIsNone(data["status"]["exit_code"])
        self.assertIsNone(data["exit_code_path"])
        self.assertNotIn(secret, waited.stdout)

    def test_relative_httponly_cookie_is_resolved_from_target_cwd_and_detected(
        self,
    ) -> None:
        working_directory = (
            PROJECT_ROOT
            / "待删除/persisted-command-relative-cookie"
            / uuid.uuid4().hex
        )
        working_directory.mkdir(parents=True)
        cookie_value = f"httponly-cookie-{uuid.uuid4().hex}"
        (working_directory / "cookies.txt").write_text(
            "# Netscape HTTP Cookie File\n"
            f"#HttpOnly_.example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\t{cookie_value}\n",
            encoding="utf-8",
        )

        started, waited, run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"relative httponly cookie {uuid.uuid4().hex}",
            "--cwd",
            str(working_directory),
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "import pathlib; print(pathlib.Path('cookies.txt').read_text(encoding='utf-8'), flush=True)",
            "--cookies",
            "cookies.txt",
        )
        self.assertIn("<localized-cookie-file>", data["command"]["argv"])
        self.assertEqual(
            data["status"]["security"],
            {
                "acceptance_evidence_eligible": False,
                "classification": "security_failure",
            },
        )
        self.assertIn(
            cookie_value,
            (run_dir / "stdout.log").read_text(encoding="utf-8"),
        )
        shareable = shareable_metadata(run_dir, started, waited)
        self.assertNotIn(cookie_value, shareable)

    def test_task_name_and_cwd_do_not_repeat_known_or_environment_secrets(
        self,
    ) -> None:
        token = f"task-token-{uuid.uuid4().hex}"
        environment_secret = f"cwd-secret-{uuid.uuid4().hex}"
        working_directory = (
            PROJECT_ROOT
            / "待删除/persisted-command-test-cwd"
            / environment_secret
        )
        working_directory.mkdir(parents=True)
        env = os.environ.copy()
        env["ISSUE21_API_TOKEN"] = environment_secret

        started, waited, run_dir, _data = run_to_terminal(
            "start",
            "--task-name",
            f"task repeats {token}",
            "--cwd",
            str(working_directory),
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "import pathlib; print(pathlib.Path.cwd(), flush=True)",
            "--token",
            token,
            env=env,
        )
        shareable = shareable_metadata(run_dir, started, waited)
        self.assertNotIn(token, shareable)
        self.assertNotIn(environment_secret, shareable)
        self.assertIn(
            str(working_directory),
            (run_dir / "stdout.log").read_text(encoding="utf-8"),
        )

    def test_known_secrets_are_redacted_from_earlier_unlabelled_arguments(
        self,
    ) -> None:
        secret_root = (
            PROJECT_ROOT
            / "待删除/persisted-command-test-secrets"
            / uuid.uuid4().hex
        )
        secret_root.mkdir(parents=True)
        cookie_file = secret_root / "cookies.txt"
        cookie_value = f"repeated-cookie-{uuid.uuid4().hex}"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            f".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\t{cookie_value}\n",
            encoding="utf-8",
        )
        token = f"repeated-token-{uuid.uuid4().hex}"

        started, waited, run_dir, _data = run_to_terminal(
            "start",
            "--task-name",
            f"repeated secrets {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "raise SystemExit(0)",
            "--trace",
            token,
            cookie_value,
            cookie_file.as_posix(),
            "--token",
            token,
            "--cookies",
            str(cookie_file),
        )
        shareable = shareable_metadata(run_dir, started, waited)
        for secret in (str(cookie_file), cookie_file.as_posix(), cookie_value, token):
            self.assertNotIn(secret, shareable)

    def test_sensitive_header_arguments_are_redacted_and_detected(self) -> None:
        bearer_token = f"bearer-secret-{uuid.uuid4().hex}"
        cookie_header = f"cookie-header-secret-{uuid.uuid4().hex}"
        child = "import sys; print(sys.argv[2].split()[-1], flush=True)"

        started, waited, run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"sensitive headers {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
            "--header",
            f"Authorization: Bearer {bearer_token}",
            f"--add-header=Cookie: {cookie_header}",
        )
        self.assertEqual(
            data["command"]["argv"],
            [
                sys.executable,
                "-X",
                "utf8",
                "-c",
                child,
                "--header",
                "Authorization: <redacted>",
                "--add-header=Cookie: <redacted>",
            ],
        )
        self.assertEqual(
            data["status"]["security"],
            {
                "acceptance_evidence_eligible": False,
                "classification": "security_failure",
            },
        )
        shareable = shareable_metadata(run_dir, started, waited)
        self.assertNotIn(bearer_token, shareable)
        self.assertNotIn(cookie_header, shareable)
        self.assertEqual(
            (run_dir / "stdout.log").read_text(encoding="utf-8"),
            f"{bearer_token}\n",
        )

    def test_sanitized_logs_are_security_eligible_for_acceptance(self) -> None:
        _started, _waited, _run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"sanitized log {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "print('sanitized output', flush=True)",
        )
        status = data["status"]
        self.assertEqual(
            status["security"],
            {
                "acceptance_evidence_eligible": True,
                "classification": "no_secret_detected",
            },
        )

    def test_detected_log_secrets_are_complete_but_ineligible_for_acceptance(
        self,
    ) -> None:
        secret_root = (
            PROJECT_ROOT
            / "待删除/persisted-command-test-secrets"
            / uuid.uuid4().hex
        )
        secret_root.mkdir(parents=True)
        cookie_file = secret_root / "cookies.txt"
        cookie_value = f"cookie-output-{uuid.uuid4().hex}"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            f".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\t{cookie_value}\n",
            encoding="utf-8",
        )
        token = f"token-output-{uuid.uuid4().hex}"
        environment_secret = f"environment-output-{uuid.uuid4().hex}"
        env = os.environ.copy()
        env["ISSUE21_API_TOKEN"] = environment_secret
        child = (
            "import os,pathlib,sys; "
            "print(sys.argv[2], flush=True); "
            "print(os.environ['ISSUE21_API_TOKEN'], flush=True); "
            "print(pathlib.Path(sys.argv[4]).read_text(encoding='utf-8'), "
            "file=sys.stderr, flush=True)"
        )

        started, waited, run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"detected log secret {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
            "--token",
            token,
            "--cookies",
            str(cookie_file),
            env=env,
        )
        self.assertEqual(data["status"]["state"], "succeeded")
        self.assertEqual(
            data["status"]["security"],
            {
                "acceptance_evidence_eligible": False,
                "classification": "security_failure",
            },
        )
        self.assertEqual(
            (run_dir / "stdout.log").read_text(encoding="utf-8"),
            f"{token}\n{environment_secret}\n",
        )
        self.assertIn(
            cookie_value,
            (run_dir / "stderr.log").read_text(encoding="utf-8"),
        )
        shareable = shareable_metadata(run_dir, started, waited)
        for secret in (str(cookie_file), cookie_value, token, environment_secret):
            self.assertNotIn(secret, shareable)

    def test_shareable_metadata_redacts_sensitive_arguments_and_environment_values(
        self,
    ) -> None:
        secret_root = (
            PROJECT_ROOT
            / "待删除/persisted-command-test-secrets"
            / uuid.uuid4().hex
        )
        secret_root.mkdir(parents=True)
        cookie_file = secret_root / "cookies.txt"
        cookie_value = f"cookie-secret-{uuid.uuid4().hex}"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            f".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\t{cookie_value}\n",
            encoding="utf-8",
        )
        token = f"token-secret-{uuid.uuid4().hex}"
        api_key = f"api-key-secret-{uuid.uuid4().hex}"
        query_secret = f"query-secret-{uuid.uuid4().hex}"
        environment_secret = f"environment-secret-{uuid.uuid4().hex}"
        env = os.environ.copy()
        env["ISSUE21_API_TOKEN"] = environment_secret

        started, waited, run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"secret-safe metadata {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "raise SystemExit(0)",
            "--cookies",
            str(cookie_file),
            "--token",
            token,
            f"--api-key={api_key}",
            f"https://example.test/resource?token={query_secret}",
            env=env,
        )
        command = data["command"]
        shareable = shareable_metadata(run_dir, started, waited)
        for secret in (
            str(cookie_file),
            cookie_value,
            token,
            api_key,
            query_secret,
            environment_secret,
        ):
            self.assertNotIn(secret, shareable)
        self.assertIn("<localized-cookie-file>", command["argv"])
        self.assertIn("<redacted>", command["argv"])
        self.assertIn("--api-key=<redacted>", command["argv"])
        self.assertIn(
            "https://example.test/resource?token=<redacted>",
            command["argv"],
        )

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


if __name__ == "__main__":
    unittest.main()

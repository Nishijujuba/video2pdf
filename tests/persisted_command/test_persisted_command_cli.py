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


def supervisor_hook_environment(
    fixture_root: Path,
    sitecustomize_source: str,
) -> dict[str, str]:
    hook_dir = fixture_root / "hook"
    hook_dir.mkdir(parents=True)
    (hook_dir / "sitecustomize.py").write_text(
        sitecustomize_source,
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (str(hook_dir), environment.get("PYTHONPATH"))
        if part
    )
    return environment


def status_sharing_conflict_hook(
    attempts_path: Path,
    *,
    failures_before_success: int | None,
    fail_publication_error_write: bool = False,
    require_exit_code: bool = True,
) -> str:
    failure_condition = (
        "True"
        if failures_before_success is None
        else f"attempts <= {failures_before_success}"
    )
    publication_error_hook = (
        "    original_open = io.open\n"
        "    def fail_publication_error_open(file, *args, **kwargs):\n"
        "        mode = args[0] if args else kwargs.get('mode', 'r')\n"
        "        if str(file).endswith('status-publication-error.json') and 'x' in mode:\n"
        "            raise OSError('simulated publication error evidence failure')\n"
        "        return original_open(file, *args, **kwargs)\n"
        "    io.open = fail_publication_error_open\n"
        if fail_publication_error_write
        else ""
    )
    exit_code_condition = " and exit_code_path.exists()" if require_exit_code else ""
    return (
        "import io\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n"
        "if '_supervise' in sys.argv:\n"
        "    original_replace = os.replace\n"
        f"    attempts_path = Path({str(attempts_path)!r})\n"
        "    attempts = 0\n"
        "    def sharing_conflict(source, destination):\n"
        "        global attempts\n"
        "        destination_path = Path(destination)\n"
        "        exit_code_path = destination_path.parent / 'exit-code.txt'\n"
        f"        if destination_path.name == 'status.json'{exit_code_condition}:\n"
        "            attempts += 1\n"
        "            attempts_path.write_text(str(attempts), encoding='utf-8')\n"
        f"            if {failure_condition}:\n"
        "                error = PermissionError(13, 'simulated sharing conflict', str(destination))\n"
        "                error.winerror = 32\n"
        "                raise error\n"
        "        return original_replace(source, destination)\n"
        "    os.replace = sharing_conflict\n"
        f"{publication_error_hook}"
    )


def run_with_supervisor_hook(
    *,
    task_name: str,
    sitecustomize_source: str,
) -> tuple[Path, dict[str, object]]:
    fixture_root = (
        PROJECT_ROOT
        / "待删除/persisted-command-test-hooks"
        / uuid.uuid4().hex
    )
    env = supervisor_hook_environment(
        fixture_root,
        sitecustomize_source,
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
    def test_short_cookie_value_is_detected_in_persisted_output(self) -> None:
        secret_root = (
            PROJECT_ROOT
            / "待删除/persisted-command-test-secrets"
            / uuid.uuid4().hex
        )
        secret_root.mkdir(parents=True)
        cookie_file = secret_root / "cookies.txt"
        cookie_value = "q7z"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            f".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\t{cookie_value}\n",
            encoding="utf-8",
        )
        child = (
            "import pathlib,sys; "
            "line=pathlib.Path(sys.argv[2]).read_text(encoding='utf-8').splitlines()[1]; "
            "print(line.split('\\t')[-1], flush=True)"
        )

        started, waited, run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"short cookie {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
            "--cookies",
            str(cookie_file),
        )
        self.assertEqual(
            data["status"]["security"],
            {
                "acceptance_evidence_eligible": False,
                "classification": "security_failure",
            },
        )
        self.assertEqual(
            (run_dir / "stdout.log").read_text(encoding="utf-8"),
            f"{cookie_value}\n",
        )
        self.assertNotIn(
            cookie_value,
            shareable_metadata(run_dir, started, waited),
        )

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
        self.assertTrue(
            {
                "schema_name",
                "schema_version",
                "run_id",
                "state",
                "updated_at",
                "started_at",
                "elapsed_seconds",
                "heartbeat_at",
                "latest_output_at",
                "log_sizes",
                "supervisor_pid",
                "child_pid",
                "supervisor_identity",
                "target_identity",
                "exit_code",
                "failure",
                "finished_at",
            }.issubset(data["status"]),
        )
        self.assertEqual(
            data["status"]["target_identity"],
            {"pid": None, "process_creation_identity": None},
        )
        self.assertIsInstance(
            data["status"]["supervisor_identity"]["process_creation_identity"],
            str,
        )
        self.assertFalse((run_dir / "exit-code.txt").exists())

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

    def test_credential_and_proxy_urls_are_redacted_without_changing_target_argv(
        self,
    ) -> None:
        ordinary_password = f"ordinary-password-{uuid.uuid4().hex}"
        proxy_password = f"proxy-password-{uuid.uuid4().hex}"
        inline_proxy_password = f"inline-proxy-password-{uuid.uuid4().hex}"
        ordinary_url = (
            f"https://service-user:{ordinary_password}@example.test/resource"
        )
        proxy_url = f"http://proxy-user:{proxy_password}@proxy.test:8080"
        inline_proxy_url = (
            f"https://inline-user:{inline_proxy_password}@proxy.test:8443"
        )
        working_directory = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-credential-url-cwd"
            / f"service-user_{ordinary_password}"
        )
        working_directory.mkdir(parents=True)
        child = "import json,sys; print(json.dumps(sys.argv[1:]), flush=True)"
        target_arguments = [
            ordinary_url,
            "--proxy",
            proxy_url,
            f"--https-proxy={inline_proxy_url}",
        ]

        started, waited, run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"credential URL proxy-user {proxy_password}",
            "--cwd",
            str(working_directory),
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
            *target_arguments,
        )

        self.assertEqual(
            data["command"]["argv"][-4:],
            [
                "https://<redacted>@example.test/resource",
                "--proxy",
                "http://<redacted>@proxy.test:8080",
                "--https-proxy=https://<redacted>@proxy.test:8443",
            ],
        )
        self.assertEqual(
            json.loads((run_dir / "stdout.log").read_text(encoding="utf-8")),
            target_arguments,
        )
        self.assertEqual(
            data["status"]["security"],
            {
                "acceptance_evidence_eligible": False,
                "classification": "security_failure",
            },
        )
        shareable = shareable_metadata(run_dir, started, waited)
        for credential in (
            "service-user",
            ordinary_password,
            "proxy-user",
            proxy_password,
            "inline-user",
            inline_proxy_password,
        ):
            self.assertNotIn(credential, shareable)

    def test_credential_query_parameter_vocabulary_is_redacted_end_to_end(
        self,
    ) -> None:
        parameter_names = (
            "api_key",
            "apikey",
            "api-key",
            "access_token",
            "access-token",
            "client_secret",
            "client-secret",
            "password",
            "passwd",
            "auth",
            "authorization",
            "cookie",
            "cookies",
            "session",
            "session_id",
            "session-token",
            "signature",
            "sig",
            "csrf",
            "po_token",
            "po-token",
            "token",
            "visitor_data",
            "visitor-data",
        )
        secrets = {
            name: f"{name}-secret-{uuid.uuid4().hex}"
            for name in parameter_names
        }
        credential_url = "https://example.test/resource?" + "&".join(
            f"{name}={secrets[name]}" for name in parameter_names
        )
        expected_url = "https://example.test/resource?" + "&".join(
            f"{name}=<redacted>" for name in parameter_names
        )
        working_directory = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-query-secret-cwd"
            / secrets["client_secret"]
        )
        working_directory.mkdir(parents=True)

        started, waited, run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"query credentials {secrets['access_token']}",
            "--cwd",
            str(working_directory),
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "import sys; print(sys.argv[1], flush=True)",
            credential_url,
        )

        self.assertEqual(data["command"]["argv"][-1], expected_url)
        self.assertEqual(
            (run_dir / "stdout.log").read_text(encoding="utf-8"),
            f"{credential_url}\n",
        )
        self.assertEqual(
            data["status"]["security"],
            {
                "acceptance_evidence_eligible": False,
                "classification": "security_failure",
            },
        )
        shareable = shareable_metadata(run_dir, started, waited)
        for secret in secrets.values():
            self.assertNotIn(secret, shareable)

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

    def test_sensitive_header_families_are_redacted_for_separate_and_inline_values(
        self,
    ) -> None:
        secrets = {
            "proxy_authorization": f"proxy-auth-{uuid.uuid4().hex}",
            "api_key": f"header-api-key-{uuid.uuid4().hex}",
            "auth_token": f"header-auth-token-{uuid.uuid4().hex}",
            "authentication_token": f"authentication-token-{uuid.uuid4().hex}",
            "cookie": f"cookie-value-{uuid.uuid4().hex}",
            "set_cookie": f"set-cookie-value-{uuid.uuid4().hex}",
        }
        target_arguments = [
            "--header",
            f"Proxy-Authorization: Basic {secrets['proxy_authorization']}",
            f"--add-header=X-API-Key: {secrets['api_key']}",
            "--header",
            f"X-Auth-Token: {secrets['auth_token']}",
            f"--header=Authentication-Token: {secrets['authentication_token']}",
            "--header",
            f"Cookie: session={secrets['cookie']}",
            f"--add-header=Set-Cookie: session={secrets['set_cookie']}; Path=/; HttpOnly",
        ]
        child = "import json,sys; print(json.dumps(sys.argv[1:]), flush=True)"

        started, waited, run_dir, data = run_to_terminal(
            "start",
            "--task-name",
            f"header credentials {secrets['api_key']}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
            *target_arguments,
        )

        self.assertEqual(
            data["command"]["argv"][-len(target_arguments):],
            [
                "--header",
                "Proxy-Authorization: <redacted>",
                "--add-header=X-API-Key: <redacted>",
                "--header",
                "X-Auth-Token: <redacted>",
                "--header=Authentication-Token: <redacted>",
                "--header",
                "Cookie: <redacted>",
                "--add-header=Set-Cookie: <redacted>",
            ],
        )
        self.assertEqual(
            json.loads((run_dir / "stdout.log").read_text(encoding="utf-8")),
            target_arguments,
        )
        self.assertEqual(
            data["status"]["security"],
            {
                "acceptance_evidence_eligible": False,
                "classification": "security_failure",
            },
        )
        shareable = shareable_metadata(run_dir, started, waited)
        for secret in secrets.values():
            self.assertNotIn(secret, shareable)

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
        self.assertNotIn("environment", command)
        self.assertNotIn("env", command)
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
        launch_error_detail = f"must-not-leak-{uuid.uuid4().hex}"
        recognized_secret = f"launch-client-secret-{uuid.uuid4().hex}"
        started = run_cli(
            "start",
            "--task-name",
            f"launch failure {recognized_secret}",
            "--",
            f"missing-executable-{launch_error_detail}",
            "--client-secret",
            recognized_secret,
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
        self.assertNotIn(
            launch_error_detail,
            json.dumps(data["status"]["failure"]),
        )
        self.assertNotIn(
            recognized_secret,
            shareable_metadata(run_dir, started, waited),
        )
        self.assertIsNone(data["exit_code_path"])
        self.assertFalse((run_dir / "exit-code.txt").exists())

    def test_invalid_working_directory_is_launch_failed_without_exit_code(
        self,
    ) -> None:
        missing_working_directory = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-invalid-cwd"
            / uuid.uuid4().hex
            / "missing"
        )
        started = run_cli(
            "start",
            "--task-name",
            f"invalid launch condition {uuid.uuid4().hex}",
            "--cwd",
            str(missing_working_directory),
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "print('must not launch')",
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "60",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        data = json.loads(waited.stdout)["data"]
        self.assertEqual(data["status"]["state"], "launch_failed")
        self.assertEqual(
            data["status"]["failure"]["kind"],
            "child_launch_failed",
        )
        self.assertIsNone(data["status"]["exit_code"])
        self.assertEqual(
            data["status"]["target_identity"],
            {"pid": None, "process_creation_identity": None},
        )
        self.assertFalse((run_dir / "exit-code.txt").exists())

    def test_concurrent_reconcile_preserves_pending_launch_failure_truth(
        self,
    ) -> None:
        fixture_root = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-launch-failure-reconciliation"
            / uuid.uuid4().hex
        )
        launch_attempted = fixture_root / "launch-attempted"
        release_launch = fixture_root / "release-launch"
        env = supervisor_hook_environment(
            fixture_root,
            (
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                "import time\n"
                "if '_supervise' in sys.argv:\n"
                f"    launch_attempted = Path({str(launch_attempted)!r})\n"
                f"    release_launch = Path({str(release_launch)!r})\n"
                "    def fail_target_launch(*args, **kwargs):\n"
                "        launch_attempted.write_text('attempted\\n', encoding='utf-8')\n"
                "        while not release_launch.exists():\n"
                "            time.sleep(0.01)\n"
                "        raise OSError('simulated target launch failure')\n"
                "    subprocess.Popen = fail_target_launch\n"
            ),
        )
        started = run_cli(
            "start",
            "--task-name",
            f"launch failure reconciliation {uuid.uuid4().hex}",
            "--",
            f"missing-executable-{uuid.uuid4().hex}",
            env=env,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])
        launch_deadline = time.monotonic() + 60
        while time.monotonic() < launch_deadline and not launch_attempted.is_file():
            time.sleep(0.02)
        self.assertTrue(launch_attempted.is_file())

        reconcile_command = [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(CLI),
            "reconcile",
            "--run-dir",
            str(run_dir),
        ]
        observers = [
            subprocess.Popen(
                reconcile_command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            for _ in range(4)
        ]
        try:
            observer_results = [
                observer.communicate(timeout=60) for observer in observers
            ]
            for observer, (stdout, stderr) in zip(observers, observer_results):
                self.assertEqual(observer.returncode, 0, stderr or stdout)
                data = json.loads(stdout)["data"]
                self.assertEqual(data["status"]["state"], "running")
                self.assertEqual(
                    data["reconciliation"]["reason"],
                    "launch_outcome_pending",
                )
            persisted_pending = json.loads(
                (run_dir / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted_pending["state"], "running")
            self.assertNotIn("reconciliation", persisted_pending)
        finally:
            release_launch.write_text("release\n", encoding="utf-8")
            for observer in observers:
                if observer.poll() is None:
                    observer.communicate(timeout=60)

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "60",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        data = json.loads(waited.stdout)["data"]
        status = data["status"]
        self.assertEqual(status["state"], "launch_failed")
        self.assertIsNone(status["exit_code"])
        self.assertFalse((run_dir / "exit-code.txt").exists())
        self.assertEqual(status["schema_name"], "persisted-command-status")
        self.assertEqual(status["schema_version"], "1.0.0")
        self.assertIsInstance(status["started_at"], str)
        self.assertIsInstance(status["updated_at"], str)
        self.assertIsInstance(status["finished_at"], str)
        self.assertGreaterEqual(status["elapsed_seconds"], 0)
        self.assertEqual(status["heartbeat_at"], status["updated_at"])
        self.assertIsNone(status["latest_output_at"])
        self.assertEqual(
            status["log_sizes"],
            {"stdout": 0, "stderr": 0, "merged": 0},
        )
        self.assertIsInstance(status["supervisor_identity"]["pid"], int)
        self.assertIsInstance(
            status["supervisor_identity"]["process_creation_identity"],
            str,
        )
        self.assertEqual(
            status["target_identity"],
            {"pid": None, "process_creation_identity": None},
        )
        self.assertEqual(
            status["failure"],
            {
                "kind": "child_launch_failed",
                "message": "target process could not be launched",
            },
        )

        terminal_status_before = (run_dir / "status.json").read_bytes()
        terminal_observers = [
            subprocess.Popen(
                reconcile_command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            for _ in range(4)
        ]
        for observer in terminal_observers:
            stdout, stderr = observer.communicate(timeout=60)
            self.assertEqual(observer.returncode, 0, stderr or stdout)
            reconciled = json.loads(stdout)["data"]
            self.assertEqual(reconciled["status"]["state"], "launch_failed")
            self.assertEqual(
                reconciled["reconciliation"]["reason"],
                "status_already_terminal",
            )
        self.assertEqual(
            (run_dir / "status.json").read_bytes(),
            terminal_status_before,
        )

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

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
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
        started = [launcher.communicate(timeout=60) for launcher in launchers]
        self.assertEqual([launcher.returncode for launcher in launchers], [0, 0], started)
        start_data = [json.loads(stdout)["data"] for stdout, _ in started]
        self.assertEqual(len({item["run_id"] for item in start_data}), 2)
        self.assertEqual(len({item["run_dir"] for item in start_data}), 2)

        for item in start_data:
            waited = run_cli(
                "wait",
                "--run-dir",
                item["run_dir"],
                "--timeout-seconds",
                "60",
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
        release_marker = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-test-markers"
            / f"{uuid.uuid4().hex}.txt"
        )
        release_marker.parent.mkdir(parents=True, exist_ok=True)
        child = (
            "from pathlib import Path\n"
            "import os,sys,time\n"
            "marker=Path(sys.argv[1])\n"
            "os.close(sys.stdout.fileno())\n"
            "os.close(sys.stderr.fileno())\n"
            "while not marker.exists():\n"
            "    time.sleep(0.02)\n"
            "os._exit(0)\n"
        )
        started = run_cli(
            "start",
            "--task-name",
            f"closed pipes heartbeat {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
            str(release_marker),
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        try:
            initial_status = self._wait_for_status(
                run_dir,
                lambda status: bool(status.get("heartbeat_at")),
                timeout_seconds=60,
            )

            time.sleep(27)
            refreshed_status = self._wait_for_status(
                run_dir,
                lambda status: status.get("heartbeat_at")
                != initial_status["heartbeat_at"],
                timeout_seconds=15,
                poll_seconds=0.1,
            )
            self.assertEqual(refreshed_status["state"], "running")
        finally:
            release_marker.write_text("release\n", encoding="utf-8")

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "30",
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
                release_marker = (
                    PROJECT_ROOT
                    / "\u5f85\u5220\u9664"
                    / "persisted-command-test-markers"
                    / f"{uuid.uuid4().hex}.txt"
                )
                release_marker.parent.mkdir(parents=True, exist_ok=True)
                child = (
                    "from pathlib import Path\n"
                    "import sys,time\n"
                    "marker=Path(sys.argv[1])\n"
                    "print('alive', flush=True)\n"
                    "while not marker.exists():\n"
                    "    time.sleep(0.02)\n"
                    "print('finished', flush=True)\n"
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
                    str(release_marker),
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

                try:
                    reconciled = run_cli(
                        "reconcile",
                        "--run-dir",
                        str(run_dir),
                    )
                    self.assertEqual(
                        reconciled.returncode,
                        0,
                        reconciled.stderr or reconciled.stdout,
                    )
                    reconciled_data = json.loads(reconciled.stdout)["data"]
                    self.assertEqual(
                        reconciled_data["status"]["state"],
                        "unknown",
                    )
                    self.assertEqual(
                        reconciled_data["reconciliation"]["reason"],
                        expected_reason,
                    )
                    self.assertEqual(
                        reconciled_data["reconciliation"][
                            "observed_target_identity"
                        ]["pid"],
                        running_status["target_identity"]["pid"],
                    )
                finally:
                    release_marker.write_text("release\n", encoding="utf-8")

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
                timeout_seconds=60,
            )

            os.kill(running_status["supervisor_pid"], signal.SIGTERM)
            stop_marker.write_text("stop\n", encoding="utf-8")

            reconcile_deadline = time.monotonic() + 60
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
        fixture_root = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-reconcile-live"
            / uuid.uuid4().hex
        )
        release_target = fixture_root / "release-target"
        child = (
            "from pathlib import Path\n"
            "import sys,time\n"
            "marker=Path(sys.argv[1])\n"
            "print('born', flush=True)\n"
            "while not marker.exists():\n"
            "    time.sleep(0.02)\n"
            "print('finished', flush=True)\n"
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
            str(release_target),
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
                timeout_seconds=60,
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
            self.assertEqual(
                reconciled_data["reconciliation"]["decision"], "running"
            )
            self.assertEqual((run_dir / "status.json").read_bytes(), status_before)
        finally:
            release_target.parent.mkdir(parents=True, exist_ok=True)
            release_target.write_text("release\n", encoding="utf-8")

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "30",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        self.assertEqual(
            (run_dir / "stdout.log").read_text(encoding="utf-8"),
            "born\nfinished\n",
        )

    def test_status_heartbeats_during_output_silence_with_identity_telemetry(self) -> None:
        release_marker = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-test-markers"
            / f"{uuid.uuid4().hex}.txt"
        )
        release_marker.parent.mkdir(parents=True, exist_ok=True)
        child = (
            "from pathlib import Path\n"
            "import sys,time\n"
            "marker=Path(sys.argv[1])\n"
            "print('heartbeat-out', flush=True)\n"
            "print('heartbeat-error', file=sys.stderr, flush=True)\n"
            "while not marker.exists():\n"
            "    time.sleep(0.02)\n"
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
            str(release_marker),
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        try:
            initial_status = self._wait_for_status(
                run_dir,
                lambda status: bool(
                    status.get("heartbeat_at")
                    and (status.get("target_identity") or {}).get(
                        "process_creation_identity"
                    )
                ),
                timeout_seconds=60,
            )

            initial_heartbeat = datetime.fromisoformat(initial_status["heartbeat_at"])
            time.sleep(27)
            refreshed_status = self._wait_for_status(
                run_dir,
                lambda status: bool(
                    status.get("heartbeat_at")
                    and status["heartbeat_at"] != initial_status["heartbeat_at"]
                ),
                timeout_seconds=15,
                poll_seconds=0.1,
            )

            refreshed_heartbeat = datetime.fromisoformat(
                refreshed_status["heartbeat_at"]
            )
            self.assertGreater(refreshed_heartbeat, initial_heartbeat)
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
                refreshed_status["supervisor_identity"][
                    "process_creation_identity"
                ]
            )
            self.assertEqual(
                refreshed_status["target_identity"]["pid"],
                refreshed_status["child_pid"],
            )
            self.assertIsNotNone(
                refreshed_status["target_identity"]["process_creation_identity"]
            )
        finally:
            release_marker.write_text("release\n", encoding="utf-8")

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "30",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        final_status = json.loads(waited.stdout)["data"]["status"]
        self.assertEqual(final_status["state"], "succeeded")
        self.assertGreaterEqual(final_status["elapsed_seconds"], 25)

    @unittest.skipUnless(
        os.name == "nt",
        "Windows sharing semantics are required for this regression",
    )
    def test_concurrent_observers_cannot_block_heartbeat_or_terminal_status(self) -> None:
        fixture_root = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-concurrency"
            / uuid.uuid4().hex
        )
        release_target = fixture_root / "release-target"
        target_completed = fixture_root / "target-completed"
        env = supervisor_hook_environment(
            fixture_root,
            (
                "import io\n"
                "import os\n"
                "from pathlib import Path\n"
                "import queue\n"
                "import sys\n"
                "import time\n"
                "if '_supervise' in sys.argv:\n"
                "    real_monotonic = time.monotonic\n"
                "    real_queue_get = queue.Queue.get\n"
                "    origin = real_monotonic()\n"
                "    scale = 50.0\n"
                "    time.monotonic = lambda: origin + (real_monotonic() - origin) * scale\n"
                "    def scaled_queue_get(self, block=True, timeout=None):\n"
                "        if timeout is not None:\n"
                "            timeout = timeout / scale\n"
                "        return real_queue_get(self, block=block, timeout=timeout)\n"
                "    queue.Queue.get = scaled_queue_get\n"
                "target_status = os.environ.get('PERSISTED_TEST_STATUS_PATH')\n"
                "release_path = os.environ.get('PERSISTED_TEST_RELEASE_OBSERVERS')\n"
                "opened_path = os.environ.get('PERSISTED_TEST_STATUS_OPENED')\n"
                "started_dir = os.environ.get('PERSISTED_TEST_OBSERVER_STARTED_DIR')\n"
                "list_run_dir = os.environ.get('PERSISTED_TEST_LIST_RUN_DIR')\n"
                "if list_run_dir and 'list' in sys.argv:\n"
                "    original_iterdir = Path.iterdir\n"
                "    def target_only_iterdir(path):\n"
                "        if path.name == 'long-running':\n"
                "            return iter((Path(list_run_dir),))\n"
                "        return original_iterdir(path)\n"
                "    Path.iterdir = target_only_iterdir\n"
                "if target_status and any(op in sys.argv for op in ('show', 'wait', 'list')):\n"
                "    Path(started_dir, str(os.getpid())).write_text('started', encoding='utf-8')\n"
                "    original_open = io.open\n"
                "    class HeldStatus:\n"
                "        def __init__(self, wrapped): self.wrapped = wrapped\n"
                "        def __enter__(self): return self\n"
                "        def __exit__(self, *details):\n"
                "            Path(opened_path).write_text('opened', encoding='utf-8')\n"
                "            while not Path(release_path).exists():\n"
                "                time.sleep(0.01)\n"
                "            return self.wrapped.__exit__(*details)\n"
                "        def __getattr__(self, name): return getattr(self.wrapped, name)\n"
                "    def held_open(file, *args, **kwargs):\n"
                "        opened = original_open(file, *args, **kwargs)\n"
                "        mode = args[0] if args else kwargs.get('mode', 'r')\n"
                "        if str(Path(file).resolve()) == target_status and 'r' in mode:\n"
                "            return HeldStatus(opened)\n"
                "        return opened\n"
                "    io.open = held_open\n"
            ),
        )
        child = (
            "from pathlib import Path\n"
            "import sys,time\n"
            "marker=Path(sys.argv[1])\n"
            "completed=Path(sys.argv[2])\n"
            "print('waiting for release', flush=True)\n"
            "while not marker.exists():\n"
            "    time.sleep(0.02)\n"
            "completed.write_text('completed\\n', encoding='utf-8')\n"
        )
        started = run_cli(
            "start",
            "--task-name",
            f"concurrent status observation {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            child,
            str(release_target),
            str(target_completed),
            env=env,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])
        initial_status = self._wait_for_status(
            run_dir,
            lambda status: bool(status.get("child_pid")),
        )

        commands = (
            ("show", "--run-dir", str(run_dir)),
            ("wait", "--run-dir", str(run_dir), "--timeout-seconds", "2"),
            ("list",),
        )
        all_observers: list[subprocess.Popen[str]] = []
        observer_releases: list[Path] = []

        def start_observer_wave(label: str) -> tuple[list[subprocess.Popen[str]], Path]:
            started_dir = fixture_root / f"{label}-started"
            opened_path = fixture_root / f"{label}-status-opened"
            release_path = fixture_root / f"{label}-release-observers"
            started_dir.mkdir(parents=True)
            observer_env = env.copy()
            observer_env["PERSISTED_TEST_STATUS_PATH"] = str(
                (run_dir / "status.json").resolve()
            )
            observer_env["PERSISTED_TEST_RELEASE_OBSERVERS"] = str(
                release_path
            )
            observer_env["PERSISTED_TEST_STATUS_OPENED"] = str(opened_path)
            observer_env["PERSISTED_TEST_OBSERVER_STARTED_DIR"] = str(
                started_dir
            )
            observer_env["PERSISTED_TEST_LIST_RUN_DIR"] = str(run_dir)
            observers = [
                subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-B", str(CLI), *command],
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=observer_env,
                )
                for command in commands
            ]
            all_observers.extend(observers)
            observer_releases.append(release_path)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if (
                    len(tuple(started_dir.iterdir())) == len(observers)
                    and opened_path.exists()
                ):
                    break
                time.sleep(0.02)
            self.assertEqual(len(tuple(started_dir.iterdir())), 3)
            self.assertTrue(opened_path.exists())
            return observers, release_path

        def release_observer_wave(
            observers: list[subprocess.Popen[str]],
            release_path: Path,
        ) -> None:
            release_path.write_text("release\n", encoding="utf-8")
            observer_results = [
                observer.communicate(timeout=120) for observer in observers
            ]
            for observer, (stdout, stderr) in zip(observers, observer_results):
                self.assertIn(observer.returncode, {0, 124}, stderr or stdout)

        try:
            heartbeat_observers, heartbeat_release = start_observer_wave(
                "heartbeat"
            )
            time.sleep(1.0)
            heartbeat_release.write_text("release\n", encoding="utf-8")
            heartbeat_status = self._wait_for_status(
                run_dir,
                lambda status: (
                    status["heartbeat_at"] != initial_status["heartbeat_at"]
                ),
            )
            self.assertEqual(heartbeat_status["state"], "running")
            release_observer_wave(heartbeat_observers, heartbeat_release)

            terminal_observers, terminal_release = start_observer_wave(
                "terminal"
            )
            release_target.write_text("release\n", encoding="utf-8")
            self.assertTrue(release_target.is_file())
            target_deadline = time.monotonic() + 120
            while (
                time.monotonic() < target_deadline
                and not target_completed.is_file()
            ):
                time.sleep(0.02)
            self.assertTrue(target_completed.is_file())
            exit_code_deadline = time.monotonic() + 240
            while (
                time.monotonic() < exit_code_deadline
                and not (run_dir / "exit-code.txt").is_file()
            ):
                time.sleep(0.02)
            self.assertTrue((run_dir / "exit-code.txt").is_file())
            release_observer_wave(terminal_observers, terminal_release)
        finally:
            release_target.write_text("release\n", encoding="utf-8")
            for release_path in observer_releases:
                release_path.write_text("release\n", encoding="utf-8")
            for observer in all_observers:
                if observer.poll() is None:
                    observer.communicate(timeout=120)

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "30",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        final_status = json.loads(waited.stdout)["data"]["status"]
        self.assertEqual(final_status["state"], "succeeded")
        self.assertEqual(final_status["exit_code"], 0)
        self.assertNotEqual(
            final_status["heartbeat_at"],
            initial_status["heartbeat_at"],
        )

    @unittest.skipUnless(
        os.name == "nt",
        "Windows sharing semantics are required for this regression",
    )
    def test_transient_terminal_status_sharing_conflicts_are_retried(self) -> None:
        fixture_root = PROJECT_ROOT / "\u5f85\u5220\u9664" / "persisted-command-sharing-retry"
        fixture_root = fixture_root / uuid.uuid4().hex
        attempts_path = fixture_root / "attempts.txt"
        env = supervisor_hook_environment(
            fixture_root,
            status_sharing_conflict_hook(
                attempts_path,
                failures_before_success=3,
            ),
        )
        started = run_cli(
            "start",
            "--task-name",
            f"transient status sharing {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "print('complete', flush=True)",
            env=env,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "60",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        status = json.loads(waited.stdout)["data"]["status"]
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["exit_code"], 0)
        self.assertEqual(attempts_path.read_text(encoding="utf-8"), "4")

    @unittest.skipUnless(
        os.name == "nt",
        "Windows sharing semantics are required for this regression",
    )
    def test_unrecoverable_terminal_status_publication_is_unknown(self) -> None:
        fixture_root = PROJECT_ROOT / "\u5f85\u5220\u9664" / "persisted-command-sharing-failure"
        fixture_root = fixture_root / uuid.uuid4().hex
        attempts_path = fixture_root / "attempts.txt"
        env = supervisor_hook_environment(
            fixture_root,
            status_sharing_conflict_hook(
                attempts_path,
                failures_before_success=None,
            ),
        )
        started = run_cli(
            "start",
            "--task-name",
            f"unrecoverable status sharing {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "print('complete', flush=True)",
            env=env,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "60",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        data = json.loads(waited.stdout)["data"]
        self.assertEqual(data["status"]["state"], "unknown")
        self.assertEqual(data["status"]["exit_code"], 0)
        self.assertEqual(
            data["status"]["failure"],
            {
                "kind": "status_publication_failed",
                "message": "terminal status could not be atomically published",
            },
        )
        publication_error = Path(
            data["evidence_paths"]["status_publication_error"]
        )
        self.assertTrue(publication_error.is_file())
        publication_record = json.loads(
            publication_error.read_text(encoding="utf-8")
        )
        self.assertEqual(
            publication_record["schema_name"],
            "persisted-command-status-publication-error",
        )
        self.assertEqual(publication_record["schema_version"], "1.0.0")
        self.assertEqual(publication_record["state"], "unknown")
        self.assertEqual(publication_record["exit_code"], 0)
        self.assertEqual(attempts_path.read_text(encoding="utf-8"), "6")
        self.assertNotEqual(data["status"]["state"], "succeeded")

        shown = run_cli("show", "--run-dir", str(run_dir))
        self.assertEqual(shown.returncode, 0, shown.stderr or shown.stdout)
        self.assertEqual(
            json.loads(shown.stdout)["data"]["status"]["state"],
            "unknown",
        )
        listed = run_cli("list")
        self.assertEqual(listed.returncode, 0, listed.stderr or listed.stdout)
        listed_run = next(
            run
            for run in json.loads(listed.stdout)["data"]["runs"]
            if run["run_id"] == data["run_id"]
        )
        self.assertEqual(listed_run["status"]["state"], "unknown")

    @unittest.skipUnless(
        os.name == "nt",
        "Windows sharing semantics are required for this regression",
    )
    def test_missing_terminal_status_and_error_record_is_unknown(self) -> None:
        fixture_root = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-missing-terminal-records"
            / uuid.uuid4().hex
        )
        attempts_path = fixture_root / "attempts.txt"
        env = supervisor_hook_environment(
            fixture_root,
            status_sharing_conflict_hook(
                attempts_path,
                failures_before_success=None,
                fail_publication_error_write=True,
            ),
        )
        started = run_cli(
            "start",
            "--task-name",
            f"missing terminal records {uuid.uuid4().hex}",
            "--",
            sys.executable,
            "-X",
            "utf8",
            "-c",
            "print('complete', flush=True)",
            env=env,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "10",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        data = json.loads(waited.stdout)["data"]
        self.assertEqual(data["status"]["state"], "unknown")
        self.assertEqual(data["status"]["exit_code"], 0)
        self.assertEqual(
            data["status"]["failure"]["kind"],
            "status_publication_failed",
        )
        self.assertEqual(
            data["status"]["status_publication"]["reason"],
            "terminal_status_missing_after_supervisor_exit",
        )
        self.assertIsNone(data["evidence_paths"]["status_publication_error"])
        self.assertTrue((run_dir / "exit-code.txt").is_file())
        self.assertNotEqual(data["status"]["state"], "succeeded")

    @unittest.skipUnless(
        os.name == "nt",
        "Windows sharing semantics are required for this regression",
    )
    def test_missing_launch_failure_status_and_error_record_is_unknown(self) -> None:
        fixture_root = (
            PROJECT_ROOT
            / "\u5f85\u5220\u9664"
            / "persisted-command-missing-launch-failure-records"
            / uuid.uuid4().hex
        )
        attempts_path = fixture_root / "attempts.txt"
        env = supervisor_hook_environment(
            fixture_root,
            status_sharing_conflict_hook(
                attempts_path,
                failures_before_success=None,
                fail_publication_error_write=True,
                require_exit_code=False,
            ),
        )
        started = run_cli(
            "start",
            "--task-name",
            f"missing launch failure records {uuid.uuid4().hex}",
            "--",
            f"missing-executable-{uuid.uuid4().hex}",
            env=env,
        )
        self.assertEqual(started.returncode, 0, started.stderr or started.stdout)
        run_dir = Path(json.loads(started.stdout)["data"]["run_dir"])

        waited = run_cli(
            "wait",
            "--run-dir",
            str(run_dir),
            "--timeout-seconds",
            "60",
        )
        self.assertEqual(waited.returncode, 0, waited.stderr or waited.stdout)
        data = json.loads(waited.stdout)["data"]
        self.assertEqual(data["status"]["state"], "unknown")
        self.assertIsNone(data["status"]["exit_code"])
        self.assertEqual(
            data["status"]["failure"]["kind"],
            "status_publication_failed",
        )
        self.assertEqual(
            data["status"]["status_publication"]["reason"],
            "terminal_status_missing_after_supervisor_exit",
        )
        self.assertIsNone(data["evidence_paths"]["status_publication_error"])
        self.assertTrue(
            Path(data["evidence_paths"]["supervisor_identity"]).is_file()
        )
        self.assertFalse((run_dir / "exit-code.txt").exists())
        self.assertNotEqual(data["status"]["state"], "succeeded")

    def _wait_for_status(
        self,
        run_dir: Path,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout_seconds: float = 60,
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

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

TEST_RUNS = (
    PROJECT_ROOT / "待删除" / "kernel-test-runs" / "provider-secret-snapshot"
)


from video2pdf_workflow_kernel.adapters import (
    CommandSpec,
    PlatformAdapterError,
    SecretArgument,
    SubprocessCommandRunner,
)
from video2pdf_workflow_kernel.adapters import base
from video2pdf_workflow_kernel.adapters.yt_dlp import _require_success


def new_test_root() -> Path:
    root = TEST_RUNS / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


class ProviderSecretSnapshotTests(unittest.TestCase):
    def test_runner_redacts_pre_execution_cookie_after_provider_rewrites_jar(
        self,
    ) -> None:
        root = new_test_root()
        cookie = root / "credentials" / "cookies.txt"
        cookie.parent.mkdir(parents=True, exist_ok=False)
        old_secret = "pre-execution-cookie-secret"
        new_secret = "refreshed-cookie-secret"
        cookie.write_text(
            "# Netscape HTTP Cookie File\n"
            f".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\t{old_secret}\n",
            encoding="utf-8",
        )
        command = CommandSpec(
            operation="cookie_refresh_failure",
            argv=(
                "provider",
                "--cookies",
                SecretArgument(str(cookie)),
                "--cookie-copy",
                str(cookie),
                "--trace",
                old_secret,
            ),
            cwd=root,
            allowed_output_root=root,
            timeout_seconds=30,
        )

        def rewrite_cookie_jar(
            *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(args[0], command.execution_argv())
            cookie.write_text(
                "# Netscape HTTP Cookie File\n"
                f".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\t{new_secret}\n",
                encoding="utf-8",
            )
            leaked = f"cookie rejected {old_secret} at {cookie}".encode("utf-8")
            return subprocess.CompletedProcess(args[0], 1, leaked, leaked)

        with mock.patch.object(base.subprocess, "run", side_effect=rewrite_cookie_jar):
            result = SubprocessCommandRunner().run(command)

        serialized_result = "\n".join(
            (
                result.stdout.decode("utf-8"),
                result.stderr.decode("utf-8"),
                json.dumps(result.evidence.argv, ensure_ascii=False),
                repr(result),
            )
        )
        self.assertNotIn(old_secret, serialized_result)
        self.assertNotIn(str(cookie), serialized_result)
        self.assertIn("<redacted>", serialized_result)
        self.assertIn("<localized-cookie-file>", result.evidence.argv)

        with self.assertRaises(PlatformAdapterError) as raised:
            _require_success(
                result,
                adapter_id="youtube",
                operation=command.operation,
            )

        error = raised.exception
        serialized_error = "\n".join(
            (
                str(error),
                repr(error),
                json.dumps(error.data, ensure_ascii=False),
            )
        )
        self.assertEqual(error.classification, "source_authentication_required")
        self.assertEqual(error.data["authentication_classification"], "cookie_rejected")
        self.assertNotIn(old_secret, serialized_error)
        self.assertNotIn(str(cookie), serialized_error)
        self.assertIn("<redacted>", error.data["sanitized_stderr_tail"])


if __name__ == "__main__":
    unittest.main()

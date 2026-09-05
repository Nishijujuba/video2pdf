from __future__ import annotations

from contextlib import redirect_stderr
import importlib.util
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from tests.video_workflow.test_guarded_final_compile_adapter import (
    ADAPTER,
    GuardedFinalCompileProviderAuthorityTests,
    canonical_bytes,
    fingerprint,
)
from video2pdf_workflow_kernel.errors import CompileDependencyGap


class _FailedAdapterProcess:
    def __init__(self, command: list[str], *, stdout: str, stderr: str, exit_code: int) -> None:
        self.request_path = Path(command[-1])
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = exit_code

    def communicate(self) -> tuple[str, str]:
        request = json.loads(self.request_path.read_text(encoding="utf-8"))
        execution_path = Path(request["execution_state_path"])
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
        execution["state"] = "running"
        execution["adapter_pid"] = 115
        execution["execution_sha256"] = fingerprint(
            execution, "execution_sha256"
        )
        execution_path.write_bytes(canonical_bytes(execution))
        return self.stdout, self.stderr


class Issue115AdapterFailureDiagnosticsTests(unittest.TestCase):
    """Exercise the public provider at its registered adapter process boundary."""

    def _invoke_failed_adapter(
        self, *, stdout: str, stderr: str, exit_code: int
    ) -> tuple[CompileDependencyGap, _FailedAdapterProcess]:
        fixture = GuardedFinalCompileProviderAuthorityTests(
            methodName="test_provider_requires_registered_adapter_path_and_current_sha"
        )
        fixture.setUp()
        launched: list[_FailedAdapterProcess] = []
        original_popen = subprocess.Popen

        def launch(command: list[str], **kwargs: object) -> subprocess.Popen[str] | _FailedAdapterProcess:
            if Path(command[-2]).resolve() != ADAPTER.resolve():
                return original_popen(command, **kwargs)
            process = _FailedAdapterProcess(
                command, stdout=stdout, stderr=stderr, exit_code=exit_code
            )
            launched.append(process)
            return process

        with (
            mock.patch(
                "video2pdf_workflow_kernel.final_compile.subprocess.Popen",
                side_effect=launch,
            ),
            self.assertRaises(CompileDependencyGap) as raised,
        ):
            fixture._run_public_final_compile_fixture()
        self.assertEqual(1, len(launched))
        return raised.exception, launched[0]

    def test_controlled_adapter_failure_retains_complete_unicode_streams(self) -> None:
        # scenario_id: issue115-controlled-adapter-failure
        # target_invariant: failed adapter streams remain recoverable diagnostics
        # mutation_seam: registered adapter process result
        # rematerialized_nodes: execution terminal state and diagnostic stream files
        # intentionally_stale_nodes: none
        # expected_first_gate: final_compile_adapter_execution
        # expected_error_code: final_compile_adapter_failed
        # scenario_class: single_contradiction
        stdout = "compile pass 1\nPDF 已生成\nstdout final line"
        stderr = (
            "controlled AdapterError\n"
            "sealed text origin coverage is incomplete: 目录、正文\n"
        )
        error, process = self._invoke_failed_adapter(
            stdout=stdout, stderr=stderr, exit_code=17
        )

        self.assertEqual(
            "final_compile_adapter_execution",
            error.data["first_failing_gate"],
        )
        self.assertEqual("final_compile_adapter_failed", error.data["error_code"])
        self.assertEqual(17, error.data["exit_code"])
        self.assertEqual(
            stdout,
            Path(error.data["stdout_path"]).read_text(encoding="utf-8"),
        )
        self.assertEqual(
            stderr,
            Path(error.data["stderr_path"]).read_text(encoding="utf-8"),
        )
        self.assertEqual(process.request_path.parent, Path(error.data["stdout_path"]).parent)
        self.assertEqual(process.request_path.parent, Path(error.data["stderr_path"]).parent)
        self.assertFalse((process.request_path.parent / "final-compile-report.json").exists())

    def test_unexpected_adapter_exception_retains_local_traceback(self) -> None:
        # scenario_id: issue115-unexpected-adapter-exception
        # target_invariant: unexpected exception traceback survives adapter failure
        # mutation_seam: adapter main invocation
        # rematerialized_nodes: stderr diagnostic and execution terminal state
        # intentionally_stale_nodes: none
        # expected_first_gate: final_compile_adapter_execution
        # expected_error_code: final_compile_adapter_failed
        # scenario_class: single_contradiction
        spec = importlib.util.spec_from_file_location(
            "issue115_guarded_final_compile_adapter", ADAPTER
        )
        if spec is None or spec.loader is None:
            self.fail("guarded Final Compile adapter could not be loaded")
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        captured_stderr = StringIO()
        with (
            mock.patch.object(sys, "argv", [str(ADAPTER), "fixture-request.json"]),
            mock.patch.object(
                adapter,
                "run",
                side_effect=RuntimeError("unexpected adapter crash 崩溃"),
            ),
            redirect_stderr(captured_stderr),
        ):
            self.assertEqual(1, adapter.main())
        traceback_text = captured_stderr.getvalue()
        self.assertIn("Traceback (most recent call last):", traceback_text)
        self.assertIn("RuntimeError: unexpected adapter crash 崩溃", traceback_text)

        error, _ = self._invoke_failed_adapter(
            stdout="render complete\n页面：1\n",
            stderr=traceback_text,
            exit_code=1,
        )
        self.assertEqual(
            "final_compile_adapter_execution",
            error.data["first_failing_gate"],
        )
        self.assertEqual("final_compile_adapter_failed", error.data["error_code"])
        self.assertEqual(1, error.data["exit_code"])
        retained = Path(error.data["stderr_path"]).read_text(encoding="utf-8")
        self.assertEqual(traceback_text, retained)
        self.assertIn("RuntimeError: unexpected adapter crash 崩溃", retained)


if __name__ == "__main__":
    unittest.main()

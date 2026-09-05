from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class CompileRuntimeRefreshParserTests(unittest.TestCase):
    def test_public_runtime_refresh_command_is_available(self) -> None:
        from video2pdf_workflow_kernel.cli import _parser

        commands = _parser()._subparsers._group_actions[0].choices
        self.assertIn("compile-runtime-refresh", commands)


RUN_ENV = "VIDEO2PDF_ISSUE104_RUN_DIR"
PRECOMPILE_ENV = "VIDEO2PDF_ISSUE104_PRECOMPILE_WORKSPACE"
MANIFEST_ENV = "VIDEO2PDF_ISSUE104_FINAL_COMPILE_MANIFEST"
REFRESHED_AT_ENV = "VIDEO2PDF_ISSUE104_REFRESHED_AT"
CLI = PROJECT_ROOT / "scripts/video_workflow.py"


@unittest.skipUnless(
    all(
        os.environ.get(name)
        for name in (RUN_ENV, PRECOMPILE_ENV, MANIFEST_ENV, REFRESHED_AT_ENV)
    ),
    "retained Issue #104 qualification environment is required",
)
class CompileRuntimeRefreshLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_dir = Path(os.environ[RUN_ENV]).resolve()
        self.workspace = Path(os.environ[PRECOMPILE_ENV]).resolve()
        self.final_manifest = Path(os.environ[MANIFEST_ENV]).resolve()
        self.refreshed_at = os.environ[REFRESHED_AT_ENV]

    def _refresh(
        self,
        *extra: str,
        refreshed_at: str | None = None,
        final_manifest: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(CLI),
                "compile-runtime-refresh",
                "--run-dir",
                str(self.run_dir),
                "--precompile-workspace-root",
                str(self.workspace),
                "--final-compile-manifest",
                str(final_manifest or self.final_manifest),
                "--refreshed-at",
                refreshed_at or self.refreshed_at,
                *extra,
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        return completed, json.loads(completed.stdout)

    def test_public_refresh_recovers_interruptions_and_preserves_authority(self) -> None:
        protected_paths = [
            self.final_manifest,
            self.workspace / "precompile-quality-report.json",
            self.workspace / "precompile-text-seal.json",
            self.run_dir / "workflow/compile-runtime-policy.json",
            self.run_dir / "workflow/compile-manifest.json",
            self.run_dir / "review/latex/diagnostic-compile-report.json",
            self.run_dir / "待删除/diagnostic-compile/main.pdf",
            self.run_dir / "workflow/production-state.json",
        ]
        predecessor = {path: path.read_bytes() for path in protected_paths}
        production_state = json.loads(predecessor[protected_paths[-1]])
        content_bindings = {
            key: value
            for key, value in production_state["artifacts"].items()
            if key
            not in {"compile_manifest", "diagnostic_compile_report", "diagnostic_pdf"}
        }
        rejected_manifest = (
            self.run_dir / "待删除/issue104-unauthorized-final-manifest.json"
        )
        invalid = json.loads(self.final_manifest.read_text(encoding="utf-8"))
        invalid["runtime_policy"]["sha256"] = "0" * 64
        invalid["manifest_sha256"] = hashlib.sha256(
            (
                json.dumps(
                    {
                        key: value
                        for key, value in invalid.items()
                        if key != "manifest_sha256"
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        rejected_manifest.parent.mkdir(parents=True, exist_ok=True)
        rejected_manifest.write_text(
            json.dumps(
                invalid,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        active_path = self.run_dir / "workflow/runtime-refresh-active.json"
        active = (
            json.loads(active_path.read_text(encoding="utf-8"))
            if active_path.is_file()
            else None
        )
        if active is None:
            operation_parent = self.run_dir / "待删除/runtime-refresh"
            operation_roots = (
                {path.resolve() for path in operation_parent.iterdir() if path.is_dir()}
                if operation_parent.is_dir()
                else set()
            )
            unauthorized, unauthorized_result = self._refresh(
                final_manifest=rejected_manifest
            )
            self.assertEqual(20, unauthorized.returncode)
            self.assertEqual("contract_invalid", unauthorized_result["classification"])
            self.assertFalse(active_path.exists())
            self.assertEqual(
                operation_roots,
                (
                    {
                        path.resolve()
                        for path in operation_parent.iterdir()
                        if path.is_dir()
                    }
                    if operation_parent.is_dir()
                    else set()
                ),
            )
            for path, expected in predecessor.items():
                self.assertEqual(expected, path.read_bytes())
            early, early_result = self._refresh(
                "--fault-point", "before_production_state_publish"
            )
            self.assertEqual(60, early.returncode, early.stdout + early.stderr)
            self.assertEqual(
                "injected_runtime_refresh_fault", early_result["classification"]
            )
            for path, expected in predecessor.items():
                self.assertEqual(expected, path.read_bytes())
            active = json.loads(active_path.read_text(encoding="utf-8"))
        self.assertEqual(self.refreshed_at, active["refreshed_at"])

        if active["state"] != "committed":
            changed_time, changed_time_result = self._refresh(
                refreshed_at=self.refreshed_at + "-changed"
            )
            self.assertEqual(20, changed_time.returncode)
            self.assertEqual("contract_invalid", changed_time_result["classification"])

        if active["state"] == "prepared":
            late, late_result = self._refresh(
                "--fault-point", "after_diagnostic_publish"
            )
            self.assertEqual(60, late.returncode, late.stdout + late.stderr)
            self.assertEqual(
                "injected_runtime_refresh_fault", late_result["classification"]
            )
            active = json.loads(active_path.read_text(encoding="utf-8"))

        if active["state"] != "committed":
            unauthorized, unauthorized_result = self._refresh(
                final_manifest=rejected_manifest
            )
            self.assertEqual(20, unauthorized.returncode)
            self.assertEqual("contract_invalid", unauthorized_result["classification"])

        completed, envelope = self._refresh()

        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertEqual("compile_runtime_refresh_complete", envelope["classification"])
        result = envelope["data"]
        self.assertGreaterEqual(len(result["drifted_inputs"]), 1)
        for path in protected_paths[:3]:
            self.assertEqual(predecessor[path], path.read_bytes())
        current_state = json.loads(protected_paths[-1].read_text(encoding="utf-8"))
        current_content_bindings = {
            key: value
            for key, value in current_state["artifacts"].items()
            if key
            not in {"compile_manifest", "diagnostic_compile_report", "diagnostic_pdf"}
        }
        self.assertEqual(content_bindings, current_content_bindings)
        journal = json.loads(
            (self.run_dir / "workflow/runtime-refresh-active.json").read_text(
                encoding="utf-8"
            )
        )
        archived_policy = Path(
            journal["predecessor_evidence"]["runtime_policy"]["archive_path"]
        )
        self.assertEqual(
            journal["predecessor_evidence"]["runtime_policy"]["sha256"],
            hashlib.sha256(archived_policy.read_bytes()).hexdigest(),
        )
        successor_manifest = Path(result["successor_final_compile_manifest_path"])
        self.assertTrue(successor_manifest.is_file())
        replayed, rejection = self._refresh(final_manifest=rejected_manifest)
        self.assertEqual(20, replayed.returncode)
        self.assertEqual("contract_invalid", rejection["classification"])
        repeated, repeated_result = self._refresh()
        self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)
        self.assertEqual(
            "compile_runtime_refresh_complete", repeated_result["classification"]
        )


if __name__ == "__main__":
    unittest.main()

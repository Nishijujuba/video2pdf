from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[4]


def load_guard():
    script_path = Path(__file__).with_name("latex_compile_pretooluse_guard.py")
    spec = importlib.util.spec_from_file_location("latex_compile_pretooluse_guard", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LatexCompilePreToolUseGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_guard()

    def test_direct_xelatex_command_is_blocked(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "xelatex main.tex"},
            }
        )

        self.assertEqual("block", result["decision"])
        self.assertIn("direct LaTeX engine call", result["reason"])
        self.assertIn("xelatex", result["reason"])

    def test_equivalent_direct_latex_engines_are_blocked(self) -> None:
        for engine in ("xelatex.exe", "pdflatex", "lualatex", "latexmk", "tectonic"):
            with self.subTest(engine=engine):
                result = self.module.decide_hook(
                    {
                        "tool_name": "Bash",
                        "tool_input": {"command": f"{engine} main.tex"},
                    }
                )

                self.assertEqual("block", result["decision"])
                self.assertIn(engine, result["reason"])

    def test_quoted_bare_engine_invocations_are_blocked(self) -> None:
        for command in ('& "xelatex.exe" main.tex', "& 'xelatex.exe' main.tex"):
            with self.subTest(command=command):
                result = self.module.decide_hook(
                    {
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                    }
                )

                self.assertEqual("block", result["decision"])
                self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
                self.assertIn("direct LaTeX engine call", result["reason"])
                self.assertIn("xelatex.exe", result["reason"])

    def test_command_like_tool_input_fallback_is_inspected(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"cmd": "xelatex main.tex"},
            }
        )

        self.assertEqual("block", result["decision"])
        self.assertIn("xelatex", result["reason"])

    def test_literal_build_shell_variable_output_directory_is_blocked(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python compile.py -output-directory $build main.tex"},
            }
        )

        self.assertEqual("block", result["decision"])
        self.assertIn("unsafe LaTeX output directory", result["reason"])
        self.assertIn("$build", result["reason"])

    def test_dangerous_output_directory_forms_are_blocked(self) -> None:
        project_root = Path.cwd()
        cases = [
            ("python compile.py -output-directory=$build main.tex", "$build"),
            ("python compile.py -output-directory ${build} main.tex", "${build}"),
            ("python compile.py -output-directory %build% main.tex", "%build%"),
            ("python compile.py -output-directory build main.tex", "build"),
            ("python compile.py -output-directory= main.tex", "empty output directory"),
            (f'python compile.py -output-directory "{project_root}" main.tex', str(project_root)),
        ]
        for command, expected_reason in cases:
            with self.subTest(command=command):
                result = self.module.decide_hook(
                    {
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                    },
                    project_root=project_root,
                )

                self.assertEqual("block", result["decision"])
                self.assertIn(expected_reason, result["reason"])

    def test_guarded_quick_wrapper_command_is_allowed_with_engine_argument(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        r"D:\Project\video2pdf\kimi\.venv\Scripts\python.exe -X utf8 "
                        r".agents\skills\bilibili-render-pdf\scripts\compile_latex_ascii.py "
                        r"--tex main.tex --mode quick --engine D:\kits\MiKTex\miktex\bin\x64\xelatex.exe"
                    )
                },
            }
        )

        self.assertEqual("approve", result["decision"])
        self.assertEqual("allow", result["hookSpecificOutput"]["permissionDecision"])

    def test_guarded_final_wrapper_mode_is_allowed(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "python .agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py "
                        "--tex main.tex --mode=final"
                    )
                },
            }
        )

        self.assertEqual("approve", result["decision"])
        self.assertEqual("allow", result["hookSpecificOutput"]["permissionDecision"])

    def test_guarded_wrapper_help_is_allowed_without_compile_mode(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "python .agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py --help"
                    )
                },
            }
        )

        self.assertEqual("approve", result["decision"])
        self.assertEqual("allow", result["hookSpecificOutput"]["permissionDecision"])
        self.assertIn("help", result["reason"].lower())

    def test_source_reading_command_that_names_wrapper_is_allowed(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "Get-Content -Raw "
                        ".agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py"
                    )
                },
            }
        )

        self.assertEqual("approve", result["decision"])
        self.assertEqual("allow", result["hookSpecificOutput"]["permissionDecision"])

    def test_python_module_source_check_that_names_wrapper_is_allowed(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "python -m py_compile "
                        ".agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py"
                    )
                },
            }
        )

        self.assertEqual("approve", result["decision"])
        self.assertEqual("allow", result["hookSpecificOutput"]["permissionDecision"])

    def test_guarded_wrapper_followed_by_direct_engine_is_blocked(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "python .agents/skills/bilibili-render-pdf/scripts/compile_latex_ascii.py "
                        "--tex main.tex --mode quick && xelatex main.tex"
                    )
                },
            }
        )

        self.assertEqual("block", result["decision"])
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertIn("direct LaTeX engine call", result["reason"])
        self.assertIn("xelatex", result["reason"])

    def test_wrapper_command_without_allowed_mode_is_blocked(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        r"python .agents\skills\bilibili-render-pdf\scripts\compile_latex_ascii.py "
                        "main.tex"
                    )
                },
            }
        )

        self.assertEqual("block", result["decision"])
        self.assertIn("guarded wrapper mode", result["reason"])

    def test_non_latex_shell_command_is_allowed(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git status --short"},
            }
        )

        self.assertEqual("approve", result["decision"])

    def test_latex_engine_name_as_non_command_argument_is_allowed(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo xelatex"},
            }
        )

        self.assertEqual("approve", result["decision"])

    def test_empty_output_directory_is_blocked(self) -> None:
        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": 'python compile.py -output-directory "" main.tex'},
            }
        )

        self.assertEqual("block", result["decision"])
        self.assertIn("empty output directory", result["reason"])

    def test_read_only_anomaly_scan_reports_known_compile_hazards(self) -> None:
        scan_root = self._make_scan_root("anomalies")
        literal_build = scan_root / "$build"
        literal_build.mkdir(parents=True)
        zero_log = scan_root / "main.compile-run-1.stdout.log"
        zero_log.write_text("", encoding="utf-8")
        stale_indicator = scan_root / "xelatex.pid"
        stale_indicator.write_text("12345", encoding="utf-8")

        result = self.module.decide_hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo safe"},
            },
            scan_root=scan_root,
        )

        self.assertEqual("approve", result["decision"])
        additional_context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("LaTeX compile anomaly scan:", additional_context)
        anomalies = json.loads(additional_context.split(": ", 1)[1])
        anomaly_types = {anomaly["type"] for anomaly in anomalies}
        self.assertEqual(
            {
                "literal_build_directory",
                "zero_byte_compile_log",
                "stale_latex_process_indicator",
            },
            anomaly_types,
        )
        self.assertTrue(literal_build.exists())
        self.assertTrue(zero_log.exists())
        self.assertTrue(stale_indicator.exists())

    def test_hook_config_keeps_stop_guard_and_adds_pretooluse_bash_guard(self) -> None:
        hooks = json.loads((REPO_ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))

        self.assertIn("Stop", hooks["hooks"])
        stop_hooks = hooks["hooks"]["Stop"][0]["hooks"]
        stop_command = stop_hooks[0].get("commandWindows") or stop_hooks[0]["command"]
        self.assertIn("delivery_guard.py", stop_command)
        self.assertIn("hook-stop", stop_command)

        self.assertIn("PreToolUse", hooks["hooks"])
        pretooluse = hooks["hooks"]["PreToolUse"]
        bash_entries = [entry for entry in pretooluse if entry.get("matcher") == "Bash"]
        self.assertEqual(1, len(bash_entries))
        guard_hooks = bash_entries[0]["hooks"]
        guard_command = guard_hooks[0].get("commandWindows") or guard_hooks[0]["command"]
        self.assertIn("latex_compile_pretooluse_guard.py", guard_command)
        self.assertLessEqual(guard_hooks[0]["timeout"], 5)

    def _make_scan_root(self, label: str) -> Path:
        scan_root = Path.cwd() / "待删除" / "skill-tests" / f"latex-pretooluse-{label}-{time.time_ns()}"
        scan_root.mkdir(parents=True, exist_ok=False)
        return scan_root


if __name__ == "__main__":
    unittest.main()

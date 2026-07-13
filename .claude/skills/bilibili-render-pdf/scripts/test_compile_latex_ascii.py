from __future__ import annotations

import importlib.util
import contextlib
import datetime as _dt
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import time
import unittest
from unittest import mock


def load_compile_helper():
    script_path = Path(__file__).with_name("compile_latex_ascii.py")
    spec = importlib.util.spec_from_file_location("compile_latex_ascii", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompileLatexAsciiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_compile_helper()

    def test_copy_candidates_keep_compile_assets_and_skip_bulk_intermediates(self) -> None:
        trash_root = Path.cwd() / "待删除" / "skill-tests"
        trash_root.mkdir(parents=True, exist_ok=True)
        source_dir = trash_root / f"compile-latex-{time.time_ns()}" / "P05-P05"
        (source_dir / "figures").mkdir(parents=True)
        (source_dir / "待删除").mkdir()
        (source_dir / "review").mkdir()

        keep_paths = [
            source_dir / "main.tex",
            source_dir / "section_01.tex",
            source_dir / "source.jpg",
            source_dir / "figures" / "frame.pdf",
        ]
        skip_paths = [
            source_dir / "source.temp.mp4",
            source_dir / "source.ai-zh.srt",
            source_dir / "review" / "independent_review.md",
            source_dir / "待删除" / "scratch.tex",
        ]
        for path in keep_paths + skip_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")

        candidates = {
            path.relative_to(source_dir).as_posix()
            for path in self.module.iter_copy_candidates(source_dir)
        }

        self.assertEqual(
            {"main.tex", "section_01.tex", "source.jpg", "figures/frame.pdf"},
            candidates,
        )

    def test_ascii_path_component_handles_non_ascii_stem(self) -> None:
        self.assertEqual("document", self.module.ascii_path_component("先天后天"))
        self.assertEqual("P05_notes", self.module.ascii_path_component("P05 notes"))
        self.assertEqual("P05_notes", self.module.ascii_path_component("P05_笔记 notes"))

    def test_long_windows_process_cwd_uses_short_alias(self) -> None:
        long_cwd = Path("C:/") / ("long-component-" * 20)
        short_cwd = str(Path.cwd())

        with (
            mock.patch.object(self.module.os, "name", "nt"),
            mock.patch.object(self.module, "windows_short_path", return_value=short_cwd) as get_short,
        ):
            process_cwd = self.module.process_cwd_for_subprocess(long_cwd)

        self.assertEqual(short_cwd, process_cwd)
        get_short.assert_called_once_with(long_cwd)

    def test_short_windows_process_cwd_keeps_original_path(self) -> None:
        short_cwd = Path(r"C:\\work\\latex-build")

        with (
            mock.patch.object(self.module.os, "name", "nt"),
            mock.patch.object(self.module, "windows_short_path") as get_short,
        ):
            process_cwd = self.module.process_cwd_for_subprocess(short_cwd)

        self.assertEqual(str(short_cwd), process_cwd)
        get_short.assert_not_called()

    def test_legacy_positional_cli_routes_to_copy_back_compile_path(self) -> None:
        video_dir = self._make_video_dir("legacy-cli")
        tex_path = video_dir / "main.tex"
        staging_root = video_dir / "ascii-staging"
        tex_path.write_text("\\documentclass{article}\n", encoding="utf-8")

        with mock.patch.object(self.module, "compile_latex", return_value=(staging_root, [])) as compile_latex:
            exit_code = self.module.main(
                [
                    str(tex_path),
                    "--engine",
                    "fake-xelatex",
                    "--staging-root",
                    str(staging_root),
                    "--runs",
                    "2",
                ]
            )

        self.assertEqual(0, exit_code)
        compile_latex.assert_called_once_with(
            tex_path,
            engine="fake-xelatex",
            staging_root=staging_root,
            runs=2,
        )
        self.assertFalse((video_dir / "待删除" / "latex-build").exists())

    def test_legacy_positional_cli_keeps_two_run_default(self) -> None:
        video_dir = self._make_video_dir("legacy-default-runs")
        tex_path = video_dir / "main.tex"
        tex_path.write_text("\\documentclass{article}\n", encoding="utf-8")

        with mock.patch.object(self.module, "compile_latex", return_value=(video_dir / "staging", [])) as compile_latex:
            exit_code = self.module.main([str(tex_path)])

        self.assertEqual(0, exit_code)
        compile_latex.assert_called_once_with(
            tex_path,
            engine=None,
            staging_root=None,
            runs=2,
        )

    def test_quick_mode_writes_report_under_disposable_video_output_build(self) -> None:
        video_dir = self._make_video_dir("quick-report")
        tex_path = video_dir / "main.tex"
        tex_path.write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n",
            encoding="utf-8",
        )
        engine_path = self._write_fake_engine(video_dir, "passing-engine")

        exit_code = self.module.main(
            [
                "--tex",
                str(tex_path),
                "--mode",
                "quick",
                "--engine",
                str(engine_path),
                "--total-timeout",
                "5",
                "--idle-timeout",
                "5",
            ]
        )

        self.assertEqual(0, exit_code)
        build_root = video_dir / "待删除" / "latex-build"
        reports = sorted(build_root.glob("*/compile_report.json"))
        self.assertEqual(1, len(reports))
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual("quick", report["mode"])
        self.assertEqual("passed", report["status"])
        self.assertEqual(str(tex_path.resolve()), report["source_tex"])
        self.assertEqual(str(engine_path.resolve()), report["engine"])
        self.assertEqual(1, report["run_count"])
        self.assertEqual({"total_seconds": 5.0, "idle_seconds": 5.0}, report["timeout_settings"])
        self.assertEqual(str(reports[0].parent.resolve()), report["build_directory"])
        expected_log_paths = [str((reports[0].parent / "main.compile-run-1.stdout.log").resolve())]
        self.assertEqual(expected_log_paths, report["log_paths"])
        for log_path in report["log_paths"]:
            log_file = Path(log_path)
            self.assertTrue(log_file.exists())
            self.assertTrue(log_file.is_relative_to(reports[0].parent.resolve()))
        start_time = _dt.datetime.fromisoformat(report["start_time"])
        finish_time = _dt.datetime.fromisoformat(report["finish_time"])
        self.assertLessEqual(start_time, finish_time)
        self.assertFalse((video_dir / "review" / "latex").exists())

    @unittest.skipUnless(os.name == "nt", "Windows process cwd regression")
    def test_quick_mode_compiles_from_long_physical_build_directory(self) -> None:
        video_dir = self._make_video_dir("quick-long-cwd")
        while len(str(video_dir / "待删除" / "latex-build" / "123456_123456")) < 280:
            video_dir = video_dir / "long-component"
        video_dir.mkdir(parents=True, exist_ok=True)
        tex_path = video_dir / "main.tex"
        tex_path.write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n",
            encoding="utf-8",
        )
        engine_dir = self._make_video_dir("quick-long-cwd-engine")
        engine_path = self._write_fake_engine(engine_dir, "passing-engine")

        exit_code = self.module.main(
            [
                "--tex",
                str(tex_path),
                "--mode",
                "quick",
                "--engine",
                str(engine_path),
                "--total-timeout",
                "5",
                "--idle-timeout",
                "5",
            ]
        )

        self.assertEqual(0, exit_code)
        reports = sorted((video_dir / "待删除" / "latex-build").glob("*/compile_report.json"))
        self.assertEqual(1, len(reports))
        self.assertGreaterEqual(len(str(reports[0].parent)), 260)
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual("passed", report["status"])
        self.assertEqual(str(reports[0].parent.resolve()), report["build_directory"])
        self.assertTrue(Path(report["log_paths"][0]).exists())

    def test_final_mode_writes_durable_report_and_final_pdf_provenance(self) -> None:
        video_dir = self._make_video_dir("final-report")
        tex_path = video_dir / "main.tex"
        tex_source = "\\documentclass{article}\n\\begin{document}\nFinal\n\\end{document}\n"
        tex_path.write_text(tex_source, encoding="utf-8")
        engine_path = self._write_fake_engine(video_dir, "passing-final-engine")
        final_pdf = video_dir / "Final Course.pdf"

        exit_code = self.module.main(
            [
                "--tex",
                str(tex_path),
                "--mode",
                "final",
                "--engine",
                str(engine_path),
                "--final-pdf",
                str(final_pdf),
                "--runs",
                "1",
                "--total-timeout",
                "5",
                "--idle-timeout",
                "5",
            ]
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(b"%PDF-1.4 fake\n", final_pdf.read_bytes())
        report_path = video_dir / "review" / "latex" / "compile_report.json"
        self.assertTrue(report_path.exists())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual("final", report["mode"])
        self.assertEqual("passed", report["status"])
        self.assertEqual("bilibili-render-pdf", report["source_skill"])
        wrapper_script = Path(self.module.__file__).resolve()
        wrapper_bytes = wrapper_script.read_bytes()
        self.assertEqual("compile_latex_ascii.py", report["producer"])
        self.assertEqual("latex_compile_guard.v1", report["producer_contract"])
        self.assertEqual("final", report["producer_mode"])
        self.assertEqual(str(wrapper_script), report["wrapper_script"])
        self.assertEqual(
            {
                "algorithm": "sha256",
                "sha256": hashlib.sha256(wrapper_bytes).hexdigest(),
                "size_bytes": len(wrapper_bytes),
            },
            report["wrapper_script_fingerprint"],
        )
        self.assertIn("--mode", report["argv"])
        self.assertIn("final", report["argv"])
        self.assertEqual(str(tex_path.resolve()), report["source_tex"])
        self.assertEqual(str(final_pdf.resolve()), report["final_pdf"])
        self.assertEqual(str(engine_path.resolve()), report["engine"])
        self.assertEqual(1, report["run_count"])
        self.assertEqual({"total_seconds": 5.0, "idle_seconds": 5.0}, report["timeout_settings"])
        build_dir = Path(report["build_directory"])
        self.assertTrue(build_dir.is_relative_to((video_dir / "待删除" / "latex-build").resolve()))
        self.assertEqual(str((build_dir / "main.compile-run-1.stdout.log").resolve()), report["log_paths"][0])
        self.assertTrue(Path(report["log_paths"][0]).exists())
        tex_bytes = tex_path.read_bytes()
        self.assertEqual(
            {
                "algorithm": "sha256",
                "sha256": hashlib.sha256(tex_bytes).hexdigest(),
                "size_bytes": len(tex_bytes),
            },
            report["source_tex_fingerprint"],
        )
        self.assertEqual(
            {
                "algorithm": "sha256",
                "sha256": hashlib.sha256(b"%PDF-1.4 fake\n").hexdigest(),
                "size_bytes": len(b"%PDF-1.4 fake\n"),
            },
            report["final_pdf_fingerprint"],
        )
        self.assertFalse((video_dir / "待删除" / "latex-build" / "compile_report.json").exists())

    def test_final_mode_writes_failed_report_when_engine_exits_unsuccessfully(self) -> None:
        video_dir = self._make_video_dir("final-failure-report")
        tex_path = video_dir / "main.tex"
        tex_path.write_text(
            "\\documentclass{article}\n\\begin{document}\nBroken\n\\end{document}\n",
            encoding="utf-8",
        )
        engine_path = self._write_failing_engine(video_dir, "failing-final-engine", exit_code=7)
        final_pdf = video_dir / "Broken Course.pdf"

        with self.assertRaises(SystemExit) as raised:
            self.module.main(
                [
                    "--tex",
                    str(tex_path),
                    "--mode",
                    "final",
                    "--engine",
                    str(engine_path),
                    "--final-pdf",
                    str(final_pdf),
                    "--runs",
                    "1",
                    "--total-timeout",
                    "5",
                    "--idle-timeout",
                    "5",
                ]
            )

        self.assertEqual(1, raised.exception.code)
        self.assertFalse(final_pdf.exists())
        report_path = video_dir / "review" / "latex" / "compile_report.json"
        self.assertTrue(report_path.exists())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual("final", report["mode"])
        self.assertEqual("failed", report["status"])
        self.assertEqual("bilibili-render-pdf", report["source_skill"])
        self.assertEqual(str(tex_path.resolve()), report["source_tex"])
        self.assertEqual(str(final_pdf.resolve()), report["final_pdf"])
        self.assertIsNone(report["final_pdf_fingerprint"])
        self.assertIn("exit code 7", report["failure_reason"])
        build_dir = Path(report["build_directory"])
        self.assertTrue(build_dir.is_relative_to((video_dir / "待删除" / "latex-build").resolve()))
        self.assertEqual([str((build_dir / "main.compile-run-1.stdout.log").resolve())], report["log_paths"])
        self.assertTrue(Path(report["log_paths"][0]).exists())

    def test_final_mode_fails_when_successful_engine_emits_no_pdf_even_if_final_pdf_exists(self) -> None:
        video_dir = self._make_video_dir("final-stale-preexisting-pdf")
        tex_path = video_dir / "main.tex"
        tex_path.write_text(
            "\\documentclass{article}\n\\begin{document}\nStale\n\\end{document}\n",
            encoding="utf-8",
        )
        engine_path = self._write_success_no_pdf_engine(video_dir, "successful-no-pdf-engine")
        final_pdf = video_dir / "Stale Course.pdf"
        stale_pdf_bytes = b"%PDF-1.4 stale preexisting\n"
        final_pdf.write_bytes(stale_pdf_bytes)

        with self.assertRaises(SystemExit) as raised:
            self.module.main(
                [
                    "--tex",
                    str(tex_path),
                    "--mode",
                    "final",
                    "--engine",
                    str(engine_path),
                    "--final-pdf",
                    str(final_pdf),
                    "--runs",
                    "1",
                    "--total-timeout",
                    "5",
                    "--idle-timeout",
                    "5",
                ]
            )

        self.assertEqual(1, raised.exception.code)
        self.assertEqual(stale_pdf_bytes, final_pdf.read_bytes())
        report_path = video_dir / "review" / "latex" / "compile_report.json"
        self.assertTrue(report_path.exists())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual("final", report["mode"])
        self.assertEqual("failed", report["status"])
        self.assertEqual("bilibili-render-pdf", report["source_skill"])
        self.assertEqual(str(tex_path.resolve()), report["source_tex"])
        self.assertEqual(str(final_pdf.resolve()), report["final_pdf"])
        self.assertIn("current build did not produce main.pdf", report["failure_reason"])
        build_dir = Path(report["build_directory"])
        self.assertFalse((build_dir / "main.pdf").exists())
        self.assertEqual([str((build_dir / "main.compile-run-1.stdout.log").resolve())], report["log_paths"])
        self.assertTrue(Path(report["log_paths"][0]).exists())

    def test_final_mode_records_explicit_source_skill(self) -> None:
        video_dir = self._make_video_dir("final-explicit-source-skill")
        tex_path = video_dir / "main.tex"
        tex_path.write_text(
            "\\documentclass{article}\n\\begin{document}\nExplicit source\n\\end{document}\n",
            encoding="utf-8",
        )
        engine_path = self._write_fake_engine(video_dir, "explicit-source-skill-engine")
        final_pdf = video_dir / "Explicit Source.pdf"

        exit_code = self.module.main(
            [
                "--tex",
                str(tex_path),
                "--mode",
                "final",
                "--engine",
                str(engine_path),
                "--final-pdf",
                str(final_pdf),
                "--source-skill",
                "youtube-render-pdf",
                "--runs",
                "1",
                "--total-timeout",
                "5",
                "--idle-timeout",
                "5",
            ]
        )

        self.assertEqual(0, exit_code)
        report_path = video_dir / "review" / "latex" / "compile_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual("final", report["mode"])
        self.assertEqual("passed", report["status"])
        self.assertEqual("youtube-render-pdf", report["source_skill"])

    def test_compile_report_fingerprint_helper_detects_changed_tex_or_pdf(self) -> None:
        def write_fresh_final_report(label: str) -> tuple[Path, Path, Path]:
            video_dir = self._make_video_dir(label)
            tex_path = video_dir / "main.tex"
            tex_path.write_text(
                "\\documentclass{article}\n\\begin{document}\nFresh\n\\end{document}\n",
                encoding="utf-8",
            )
            engine_path = self._write_fake_engine(video_dir, f"{label}-engine")
            final_pdf = video_dir / f"{label}.pdf"
            self.module.main(
                [
                    "--tex",
                    str(tex_path),
                    "--mode",
                    "final",
                    "--engine",
                    str(engine_path),
                    "--final-pdf",
                    str(final_pdf),
                    "--runs",
                    "1",
                    "--total-timeout",
                    "5",
                    "--idle-timeout",
                    "5",
                ]
            )
            return video_dir / "review" / "latex" / "compile_report.json", tex_path, final_pdf

        tex_report, tex_path, _ = write_fresh_final_report("tex-stale")
        self.assertTrue(self.module.compile_report_fingerprints_are_current(tex_report))
        tex_path.write_text(
            "\\documentclass{article}\n\\begin{document}\nChanged source\n\\end{document}\n",
            encoding="utf-8",
        )
        self.assertFalse(self.module.compile_report_fingerprints_are_current(tex_report))

        pdf_report, _, final_pdf = write_fresh_final_report("pdf-stale")
        self.assertTrue(self.module.compile_report_fingerprints_are_current(pdf_report))
        final_pdf.write_bytes(b"%PDF-1.4 changed\n")
        self.assertFalse(self.module.compile_report_fingerprints_are_current(pdf_report))

    def test_quick_mode_fails_when_engine_is_idle_past_timeout(self) -> None:
        video_dir = self._make_video_dir("idle-timeout")
        tex_path = video_dir / "main.tex"
        tex_path.write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n",
            encoding="utf-8",
        )
        engine_path = self._write_sleeping_engine(video_dir, "idle-engine", seconds=1.0)

        with self.assertRaises(SystemExit) as raised:
            self.module.main(
                [
                    "--tex",
                    str(tex_path),
                    "--mode",
                    "quick",
                    "--engine",
                    str(engine_path),
                    "--runs",
                    "1",
                    "--total-timeout",
                    "5",
                    "--idle-timeout",
                    "0.2",
                ]
            )

        self.assertEqual(1, raised.exception.code)
        reports = sorted((video_dir / "待删除" / "latex-build").glob("*/compile_report.json"))
        self.assertEqual(1, len(reports))
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual("quick", report["mode"])
        self.assertEqual("failed", report["status"])
        self.assertEqual(str(tex_path.resolve()), report["source_tex"])
        self.assertEqual(str(engine_path.resolve()), report["engine"])
        self.assertEqual(1, report["run_count"])
        self.assertEqual({"total_seconds": 5.0, "idle_seconds": 0.2}, report["timeout_settings"])
        self.assertEqual(str(reports[0].parent.resolve()), report["build_directory"])
        self.assertEqual([str((reports[0].parent / "main.compile-run-1.stdout.log").resolve())], report["log_paths"])
        self.assertTrue(Path(report["log_paths"][0]).exists())
        _dt.datetime.fromisoformat(report["start_time"])
        _dt.datetime.fromisoformat(report["finish_time"])
        self.assertIn("idle timeout", report["failure_reason"])
        self.assertFalse((video_dir / "review" / "latex").exists())

    def test_quick_mode_fails_when_total_timeout_expires(self) -> None:
        video_dir = self._make_video_dir("total-timeout")
        tex_path = video_dir / "main.tex"
        tex_path.write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n",
            encoding="utf-8",
        )
        engine_path = self._write_chatty_sleeping_engine(video_dir, "total-engine")

        with self.assertRaises(SystemExit) as raised:
            self.module.main(
                [
                    "--tex",
                    str(tex_path),
                    "--mode",
                    "quick",
                    "--engine",
                    str(engine_path),
                    "--runs",
                    "1",
                    "--total-timeout",
                    "0.2",
                    "--idle-timeout",
                    "5",
                ]
            )

        self.assertEqual(1, raised.exception.code)
        reports = sorted((video_dir / "待删除" / "latex-build").glob("*/compile_report.json"))
        self.assertEqual(1, len(reports))
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual("failed", report["status"])
        self.assertIn("total timeout", report["failure_reason"])

    def test_quick_mode_writes_failure_report_when_engine_cannot_start(self) -> None:
        video_dir = self._make_video_dir("engine-startup-failure")
        tex_path = video_dir / "main.tex"
        tex_path.write_text(
            "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n",
            encoding="utf-8",
        )
        engine_path = video_dir / "not-an-executable.txt"
        engine_path.write_text("plain text", encoding="utf-8")

        with self.assertRaises(SystemExit) as raised:
            self.module.main(
                [
                    "--tex",
                    str(tex_path),
                    "--mode",
                    "quick",
                    "--engine",
                    str(engine_path),
                    "--runs",
                    "1",
                    "--total-timeout",
                    "5",
                    "--idle-timeout",
                    "5",
                ]
            )

        self.assertEqual(1, raised.exception.code)
        reports = sorted((video_dir / "待删除" / "latex-build").glob("*/compile_report.json"))
        self.assertEqual(1, len(reports))
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertEqual("failed", report["status"])
        self.assertEqual(str(engine_path.resolve()), report["engine"])
        self.assertIn("failed to start LaTeX engine", report["failure_reason"])
        self.assertEqual([str((reports[0].parent / "main.compile-run-1.stdout.log").resolve())], report["log_paths"])
        self.assertTrue(Path(report["log_paths"][0]).exists())

    def test_windows_timeout_termination_bounds_taskkill_and_falls_back_to_process_kill(self) -> None:
        proc = mock.Mock()
        proc.pid = 12345
        proc.poll.return_value = None

        with (
            mock.patch.object(self.module.os, "name", "nt"),
            mock.patch.object(
                self.module.subprocess,
                "run",
                side_effect=self.module.subprocess.TimeoutExpired(["taskkill"], timeout=5),
            ) as taskkill,
        ):
            self.module.terminate_process_tree(proc)

        taskkill.assert_called_once()
        self.assertEqual(5, taskkill.call_args.kwargs["timeout"])
        proc.kill.assert_called_once_with()

    def test_quick_mode_rejects_missing_tex_path_with_clear_diagnostic(self) -> None:
        video_dir = self._make_video_dir("missing-tex")
        tex_path = video_dir / "missing.tex"
        engine_path = self._write_fake_engine(video_dir, "passing-engine")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            self.module.main(
                [
                    "--tex",
                    str(tex_path),
                    "--mode",
                    "quick",
                    "--engine",
                    str(engine_path),
                ]
            )

        self.assertEqual(1, raised.exception.code)
        self.assertIn("TeX file not found", stderr.getvalue())
        self.assertFalse((video_dir / "待删除" / "latex-build").exists())

    def test_quick_mode_rejects_missing_engine_path_before_creating_build_directory(self) -> None:
        video_dir = self._make_video_dir("missing-engine")
        tex_path = video_dir / "main.tex"
        tex_path.write_text("\\documentclass{article}\n", encoding="utf-8")
        missing_engine = video_dir / "missing-engine.exe"

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            self.module.main(
                [
                    "--tex",
                    str(tex_path),
                    "--mode",
                    "quick",
                    "--engine",
                    str(missing_engine),
                ]
            )

        self.assertEqual(1, raised.exception.code)
        self.assertIn(f"LaTeX engine not found: {missing_engine.resolve()}", stderr.getvalue())
        self.assertFalse((video_dir / "待删除" / "latex-build").exists())

    def test_cli_rejects_invalid_mode_before_creating_build_directory(self) -> None:
        video_dir = self._make_video_dir("invalid-mode")
        tex_path = video_dir / "main.tex"
        tex_path.write_text("\\documentclass{article}\n", encoding="utf-8")
        engine_path = self._write_fake_engine(video_dir, "passing-engine")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            self.module.main(
                [
                    "--tex",
                    str(tex_path),
                    "--mode",
                    "unsafe",
                    "--engine",
                    str(engine_path),
                ]
            )

        self.assertEqual(2, raised.exception.code)
        self.assertIn("invalid choice", stderr.getvalue())
        self.assertFalse((video_dir / "待删除" / "latex-build").exists())

    def test_cli_rejects_invalid_timeout_values_before_creating_build_directory(self) -> None:
        cases = [
            ("zero-total", "--total-timeout", "0"),
            ("negative-total", "--total-timeout", "-0.1"),
            ("zero-idle", "--idle-timeout", "0"),
            ("negative-idle", "--idle-timeout", "-0.1"),
        ]

        for label, option, value in cases:
            with self.subTest(option=option, value=value):
                video_dir = self._make_video_dir(label)
                tex_path = video_dir / "main.tex"
                tex_path.write_text("\\documentclass{article}\n", encoding="utf-8")
                engine_path = self._write_fake_engine(video_dir, "passing-engine")

                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                    self.module.main(
                        [
                            "--tex",
                            str(tex_path),
                            "--mode",
                            "quick",
                            "--engine",
                            str(engine_path),
                            option,
                            value,
                        ]
                    )

                self.assertEqual(2, raised.exception.code)
                self.assertIn(f"argument {option}: must be a positive number", stderr.getvalue())
                self.assertFalse((video_dir / "待删除" / "latex-build").exists())

    def test_main_with_explicit_empty_argv_does_not_consume_outer_process_args(self) -> None:
        video_dir = self._make_video_dir("empty-argv")
        tex_path = video_dir / "main.tex"
        tex_path.write_text("\\documentclass{article}\n", encoding="utf-8")

        stderr = io.StringIO()
        with (
            mock.patch.object(self.module.sys, "argv", ["outer", str(tex_path), "--runs", "1"]),
            mock.patch.object(self.module, "compile_latex") as compile_latex,
            mock.patch.object(self.module, "compile_quick") as compile_quick,
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            self.module.main([])

        self.assertEqual(2, raised.exception.code)
        self.assertIn("positional tex is required unless --mode quick or final is used", stderr.getvalue())
        compile_latex.assert_not_called()
        compile_quick.assert_not_called()

    def _make_video_dir(self, label: str) -> Path:
        root = Path.cwd() / "待删除" / "skill-tests" / f"{label}-{time.time_ns()}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _write_fake_engine(self, video_dir: Path, name: str) -> Path:
        script_path = video_dir / f"{name}.py"
        script_path.write_text(
            "\n".join(
                [
                    "from pathlib import Path",
                    "import sys",
                    "tex_name = sys.argv[-1]",
                    "stem = Path(tex_name).stem",
                    "print('fake engine compiled ' + tex_name, flush=True)",
                    "Path(stem + '.log').write_text('fake log', encoding='utf-8')",
                    "Path(stem + '.pdf').write_bytes(b'%PDF-1.4 fake\\n')",
                    "raise SystemExit(0)",
                ]
            ),
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = video_dir / f"{name}.cmd"
            launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n', encoding="utf-8")
            return launcher
        script_path.write_text("#!/usr/bin/env python3\n" + script_path.read_text(encoding="utf-8"), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
        return script_path

    def _write_sleeping_engine(self, video_dir: Path, name: str, *, seconds: float) -> Path:
        script_path = video_dir / f"{name}.py"
        script_path.write_text(
            "\n".join(
                [
                    "from pathlib import Path",
                    "import sys",
                    "import time",
                    f"time.sleep({seconds!r})",
                    "tex_name = sys.argv[-1]",
                    "stem = Path(tex_name).stem",
                    "Path(stem + '.pdf').write_bytes(b'%PDF-1.4 fake\\n')",
                    "raise SystemExit(0)",
                ]
            ),
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = video_dir / f"{name}.cmd"
            launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n', encoding="utf-8")
            return launcher
        script_path.write_text("#!/usr/bin/env python3\n" + script_path.read_text(encoding="utf-8"), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
        return script_path

    def _write_chatty_sleeping_engine(self, video_dir: Path, name: str) -> Path:
        script_path = video_dir / f"{name}.py"
        script_path.write_text(
            "\n".join(
                [
                    "import time",
                    "for index in range(20):",
                    "    print('tick', index, flush=True)",
                    "    time.sleep(0.05)",
                    "raise SystemExit(0)",
                ]
            ),
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = video_dir / f"{name}.cmd"
            launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n', encoding="utf-8")
            return launcher
        script_path.write_text("#!/usr/bin/env python3\n" + script_path.read_text(encoding="utf-8"), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
        return script_path

    def _write_failing_engine(self, video_dir: Path, name: str, *, exit_code: int) -> Path:
        script_path = video_dir / f"{name}.py"
        script_path.write_text(
            "\n".join(
                [
                    "import sys",
                    "print('fake engine failed', flush=True)",
                    f"raise SystemExit({exit_code})",
                ]
            ),
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = video_dir / f"{name}.cmd"
            launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n', encoding="utf-8")
            return launcher
        script_path.write_text("#!/usr/bin/env python3\n" + script_path.read_text(encoding="utf-8"), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
        return script_path

    def _write_success_no_pdf_engine(self, video_dir: Path, name: str) -> Path:
        script_path = video_dir / f"{name}.py"
        script_path.write_text(
            "\n".join(
                [
                    "import sys",
                    "print('fake engine succeeded without pdf', flush=True)",
                    "raise SystemExit(0)",
                ]
            ),
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = video_dir / f"{name}.cmd"
            launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n', encoding="utf-8")
            return launcher
        script_path.write_text("#!/usr/bin/env python3\n" + script_path.read_text(encoding="utf-8"), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
        return script_path


if __name__ == "__main__":
    unittest.main()

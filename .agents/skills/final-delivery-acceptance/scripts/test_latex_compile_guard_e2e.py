#!/usr/bin/env python3
"""End-to-end fixture tests for the LaTeX Compile Guard contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import unittest
import uuid

import fitz

from validate_acceptance_report import compute_artifact_fingerprint, create_allowed_artifacts_manifest


REPO_ROOT = Path(__file__).resolve().parents[4]
CRITERIA_PATH = REPO_ROOT / "docs" / "acceptance" / "acceptance_criteria.v1.json"
WRAPPER_SCRIPT = REPO_ROOT / ".agents" / "skills" / "bilibili-render-pdf" / "scripts" / "compile_latex_ascii.py"
PRETOOLUSE_SCRIPT = REPO_ROOT / ".agents" / "skills" / "bilibili-render-pdf" / "scripts" / "latex_compile_pretooluse_guard.py"
DELIVERY_GUARD_SCRIPT = REPO_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "scripts" / "delivery_guard.py"
DEPENDENCY_VIEW_SCRIPT = REPO_ROOT / "scripts" / "generate_issue_dependency_views.py"
SKILL_CONTRACT_TEST = (
    REPO_ROOT / ".agents" / "skills" / "final-delivery-acceptance" / "scripts" / "test_skill_contracts.py"
)


def load_criteria() -> dict[str, object]:
    return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))


class LatexCompileGuardE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.case_dir = REPO_ROOT / "待删除" / "latex-compile-guard-e2e" / f"{self._testMethodName}-{uuid.uuid4().hex}"
        self.case_dir.mkdir(parents=True, exist_ok=False)
        self.counter = 0

    def make_video_dir(self, label: str) -> Path:
        self.counter += 1
        video_dir = self.case_dir / f"{label}-{self.counter}" / "video"
        (video_dir / "待删除").mkdir(parents=True, exist_ok=True)
        (video_dir / "review" / "acceptance" / "rendered_pages").mkdir(parents=True, exist_ok=True)
        (video_dir / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nGuard fixture\n\\end{document}\n",
            encoding="utf-8",
        )
        return video_dir

    def write_real_pdf(self, path: Path, text: str = "Guard fixture") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text((72, 72), text)
        doc.save(path)
        doc.close()

    def write_real_pdf_engine(self, video_dir: Path) -> Path:
        script_path = video_dir / "real_pdf_engine.py"
        script_path.write_text(
            "\n".join(
                [
                    "from pathlib import Path",
                    "import fitz",
                    "import sys",
                    "tex_name = sys.argv[-1]",
                    "stem = Path(tex_name).stem",
                    "doc = fitz.open()",
                    "page = doc.new_page(width=300, height=300)",
                    "page.insert_text((72, 72), 'compiled ' + tex_name)",
                    "doc.save(stem + '.pdf')",
                    "doc.close()",
                    "Path(stem + '.log').write_text('real pdf fixture log', encoding='utf-8')",
                    "print('real pdf fixture compiled ' + tex_name, flush=True)",
                    "raise SystemExit(0)",
                ]
            ),
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = video_dir / "real_pdf_engine.cmd"
            launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n', encoding="utf-8")
            return launcher
        script_path.write_text("#!/usr/bin/env python3\n" + script_path.read_text(encoding="utf-8"), encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
        return script_path

    def run_wrapper(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(WRAPPER_SCRIPT), *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def compile_final_fixture(self, video_dir: Path) -> Path:
        engine = self.write_real_pdf_engine(video_dir)
        final_pdf = video_dir / "Final Guard Fixture.pdf"
        completed = self.run_wrapper(
            "--tex",
            str(video_dir / "main.tex"),
            "--mode",
            "final",
            "--engine",
            str(engine),
            "--final-pdf",
            str(final_pdf),
            "--source-skill",
            "bilibili-render-pdf",
            "--runs",
            "1",
            "--total-timeout",
            "10",
            "--idle-timeout",
            "10",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with fitz.open(final_pdf) as doc:
            self.assertEqual(1, len(doc))
        return final_pdf

    def compile_quick_fixture(self, video_dir: Path) -> Path:
        engine = self.write_real_pdf_engine(video_dir)
        completed = self.run_wrapper(
            "--tex",
            str(video_dir / "main.tex"),
            "--mode",
            "quick",
            "--engine",
            str(engine),
            "--runs",
            "1",
            "--total-timeout",
            "10",
            "--idle-timeout",
            "10",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        reports = sorted((video_dir / "待删除" / "latex-build").glob("*/compile_report.json"))
        self.assertEqual(1, len(reports))
        return reports[0]

    def prepare_delivery_fixture(self, video_dir: Path, final_pdf: Path, compile_report: Path) -> Path:
        rendered_page = video_dir / "review" / "acceptance" / "rendered_pages" / "page_0001.png"
        rendered_page.write_bytes(b"png evidence")
        create_allowed_artifacts_manifest(
            video_dir,
            CRITERIA_PATH,
            [("tex", "main.tex"), ("pdf", final_pdf.relative_to(video_dir).as_posix())],
        )
        self.write_acceptance_report(video_dir, final_pdf)
        self.write_delivery_target(video_dir, final_pdf, compile_report)
        current_target = self.case_dir / ".codex" / "delivery-targets" / "current.json"
        current_target.parent.mkdir(parents=True, exist_ok=True)
        current_target.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "stage": "accepted",
                    "video_output_dir": video_dir.relative_to(REPO_ROOT).as_posix(),
                    "target_file": (video_dir / "review" / "acceptance" / "delivery_target.json")
                    .relative_to(REPO_ROOT)
                    .as_posix(),
                    "source_skill": "e2e-fixture",
                    "updated_at": "2026-07-06T00:00:00+08:00",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return current_target

    def write_acceptance_report(self, video_dir: Path, final_pdf: Path) -> None:
        criteria = load_criteria()
        criteria_items = criteria["criteria"]
        assert isinstance(criteria_items, list)
        pdf_relative = final_pdf.relative_to(video_dir).as_posix()
        report = {
            "schema_version": "1.0",
            "criteria_version": criteria["criteria_version"],
            "criteria_file": "docs/acceptance/acceptance_criteria.v1.json",
            "overall_status": "pass",
            "decision_source": "acceptance_report_json",
            "review_context_used": {
                "allowed_artifacts_manifest": "review/acceptance/allowed_artifacts_manifest.json",
                "final_artifacts_only": True,
                "generation_process_used": False,
                "artifacts_read": ["main.tex", pdf_relative, "docs/acceptance/acceptance_criteria.v1.json"],
            },
            "artifact_fingerprints": [
                compute_artifact_fingerprint(video_dir / "main.tex", "main.tex"),
                compute_artifact_fingerprint(final_pdf, pdf_relative),
            ],
            "criterion_results": [
                {
                    "criterion_id": item["id"],
                    "category": item["category"],
                    "status": "pass",
                    "evidence": [
                        {
                            "artifact_path": "main.tex" if item["category"] == "style" else pdf_relative,
                            "location": "full artifact",
                            "summary": "No blocking defect detected.",
                        }
                    ],
                    "scan_evidence": {
                        "scan_policy": item["scan_policy"],
                        "scanned_artifacts": ["main.tex" if item["category"] == "style" else pdf_relative],
                    },
                    "revision_guidance": None,
                }
                for item in criteria_items
            ],
            "visual_scan_evidence": {
                "pdf": pdf_relative,
                "page_count": 1,
                "rendered_pages_dir": "review/acceptance/rendered_pages",
                "pages_checked": [
                    {
                        "page": 1,
                        "rendered_page_image": "review/acceptance/rendered_pages/page_0001.png",
                        "status": "pass",
                        "criteria_checked": [
                            "figure_visual_integrity",
                            "table_layout_integrity",
                            "credibility_disclosure_placement",
                        ],
                        "failures": [],
                    }
                ],
            },
            "failed_criteria": [],
            "revision_required": False,
        }
        report_path = video_dir / "review" / "acceptance" / "acceptance_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_delivery_target(self, video_dir: Path, final_pdf: Path, compile_report: Path) -> None:
        target = {
            "schema_version": "1.0",
            "stage": "accepted",
            "video_output_dir": ".",
            "final_pdf": final_pdf.relative_to(video_dir).as_posix(),
            "main_tex": "main.tex",
            "allowed_artifacts_manifest": "review/acceptance/allowed_artifacts_manifest.json",
            "acceptance_report": "review/acceptance/acceptance_report.json",
            "delivery_guard_report": "review/acceptance/delivery_guard_report.json",
            "compile_report": compile_report.relative_to(video_dir).as_posix(),
            "attempt_limit": 3,
        }
        target_path = video_dir / "review" / "acceptance" / "delivery_target.json"
        target_path.write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_delivery_guard(self, current_target: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(DELIVERY_GUARD_SCRIPT),
                "check",
                "--project-root",
                str(REPO_ROOT),
                "--current-target",
                str(current_target),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_final_wrapper_compile_report_passes_delivery_guard(self) -> None:
        video_dir = self.make_video_dir("final-pass")
        final_pdf = self.compile_final_fixture(video_dir)
        compile_report = video_dir / "review" / "latex" / "compile_report.json"
        current_target = self.prepare_delivery_fixture(video_dir, final_pdf, compile_report)

        completed = self.run_delivery_guard(current_target)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        guard_report = json.loads(
            (video_dir / "review" / "acceptance" / "delivery_guard_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual("pass", guard_report["status"])
        self.assertIn("review/latex/compile_report.json", {item["path"] for item in guard_report["artifact_fingerprints"]})

    def test_final_pdf_fingerprint_change_blocks_delivery_guard(self) -> None:
        video_dir = self.make_video_dir("stale-pdf")
        final_pdf = self.compile_final_fixture(video_dir)
        current_target = self.prepare_delivery_fixture(
            video_dir,
            final_pdf,
            video_dir / "review" / "latex" / "compile_report.json",
        )
        with final_pdf.open("ab") as handle:
            handle.write(b"\nchanged after compile")

        completed = self.run_delivery_guard(current_target)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report final_pdf_fingerprint is stale", completed.stderr)

    def test_main_tex_fingerprint_change_blocks_delivery_guard(self) -> None:
        video_dir = self.make_video_dir("stale-tex")
        final_pdf = self.compile_final_fixture(video_dir)
        current_target = self.prepare_delivery_fixture(
            video_dir,
            final_pdf,
            video_dir / "review" / "latex" / "compile_report.json",
        )
        (video_dir / "main.tex").write_text("Changed after final compile.\n", encoding="utf-8")

        completed = self.run_delivery_guard(current_target)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report source_tex_fingerprint is stale", completed.stderr)

    def test_quick_mode_report_cannot_satisfy_delivery_guard(self) -> None:
        video_dir = self.make_video_dir("quick-blocked")
        quick_report = self.compile_quick_fixture(video_dir)
        final_pdf = video_dir / "Quick Mode Cannot Deliver.pdf"
        self.write_real_pdf(final_pdf)
        current_target = self.prepare_delivery_fixture(video_dir, final_pdf, quick_report)

        completed = self.run_delivery_guard(current_target)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("final compile report mode must be 'final'", completed.stderr)
        self.assertIn("quick", completed.stderr)

    def test_pretooluse_hooks_and_issue_dependency_view_agree_on_guard_contract(self) -> None:
        direct = self.run_pretooluse({"tool_name": "Bash", "tool_input": {"command": "xelatex main.tex"}})
        self.assertEqual(0, direct.returncode, direct.stderr)
        self.assertEqual("block", json.loads(direct.stdout)["decision"])

        guarded = self.run_pretooluse(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        f'"{sys.executable}" -X utf8 "{WRAPPER_SCRIPT}" '
                        f'--tex main.tex --mode final --engine "{REPO_ROOT / "fake-xelatex.cmd"}" '
                        "--final-pdf final.pdf"
                    )
                },
            }
        )
        self.assertEqual(0, guarded.returncode, guarded.stderr)
        self.assertEqual("approve", json.loads(guarded.stdout)["decision"])

        hooks = json.loads((REPO_ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertIn("Stop", hooks["hooks"])
        self.assertIn("PreToolUse", hooks["hooks"])
        self.assertIn("delivery_guard.py", json.dumps(hooks["hooks"]["Stop"], ensure_ascii=False))
        self.assertIn("latex_compile_pretooluse_guard.py", json.dumps(hooks["hooks"]["PreToolUse"], ensure_ascii=False))

        skill_contracts = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(SKILL_CONTRACT_TEST)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, skill_contracts.returncode, skill_contracts.stdout + skill_contracts.stderr)

        dependency_check = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(DEPENDENCY_VIEW_SCRIPT),
                "--check",
                "--feature",
                "latex-compile-guard",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, dependency_check.returncode, dependency_check.stdout + dependency_check.stderr)

    def run_pretooluse(self, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(PRETOOLUSE_SCRIPT)],
            cwd=REPO_ROOT,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()

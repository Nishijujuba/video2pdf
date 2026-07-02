from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import time
import unittest


def load_run_batch():
    script_path = Path(__file__).with_name("run_batch.py")
    spec = importlib.util.spec_from_file_location("run_batch", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunBatchFailureRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_batch = load_run_batch()

    def test_sanitize_name_uses_project_whitelist(self) -> None:
        cleaned = self.run_batch.sanitize_windows_name('A/B: C*D? E_F｜G', limit=80)

        self.assertEqual("A_B_ C_D_ E_F_G", cleaned)

    def test_timestamped_output_name_uses_task_start_suffix(self) -> None:
        name = self.run_batch.timestamped_output_name(
            "A/B: C",
            timestamp="20260702_104530",
            fallback="video",
            limit=40,
        )

        self.assertEqual("A_B_ C_20260702_104530", name)

    def write_pyramid_report(
        self,
        review_dir: Path,
        name: str,
        artifact_path: Path,
        artifact_type: str,
        context_label: str,
    ) -> None:
        raw = artifact_path.read_bytes()
        text = raw.decode("utf-8")
        report = {
            "target": str(artifact_path),
            "artifact_type": artifact_type,
            "context_label": context_label,
            "status": "pass",
            "score": 0.88,
            "dimensions": {
                "top_down_clarity": 0.9,
                "support_hierarchy": 0.88,
                "grouping_mece": 0.86,
                "teaching_progression": 0.88,
                "title_body_alignment": 0.9,
            },
            "findings": [],
            "required_revisions": [],
            "waiver": {
                "state": "none",
                "approved_by": None,
                "reason": None,
                "approved_at": None,
            },
            "audit": {
                "standard_name": "Pyramid Principle Text Standard",
                "backend": "codex-exec",
                "prompt_version": "pyramid-principle-text-v1",
                "input_sha256": hashlib.sha256(raw).hexdigest(),
                "input_size_chars": len(text),
                "max_input_size_chars": 160000,
                "large_input_approval_state": "not_required",
                "evaluation_context": f"Teaching-PDF {context_label} checkpoint.",
                "generated_at": "2026-07-02T10:45:30Z",
            },
        }
        (review_dir / name).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    def write_pyramid_gate_reports(self, output_dir: Path) -> None:
        pyramid_dir = output_dir / "review" / "pyramid"
        pyramid_dir.mkdir(parents=True, exist_ok=True)
        outline = output_dir / "outline_contract.md"
        section = output_dir / "section_01.tex"
        main = output_dir / "main.tex"
        outline.write_text("# Teaching claim\n\nThe outline starts with a controlling claim.\n", encoding="utf-8")
        section.write_text("\\section{Core idea}\nThe section starts with a parent claim.\n", encoding="utf-8")
        main.write_text(
            "\\documentclass{article}\\begin{document}The main document integrates the claim.\\end{document}",
            encoding="utf-8",
        )
        self.write_pyramid_report(pyramid_dir, "outline.pyramid.json", outline, "outline_contract", "outline")
        self.write_pyramid_report(pyramid_dir, "section_01.pyramid.json", section, "tex_section", "section_01")
        self.write_pyramid_report(pyramid_dir, "main.pyramid.json", main, "tex_document", "main")
        (pyramid_dir / "summary.md").write_text("# Pyramid Gate\n\nAll checkpoints passed.\n", encoding="utf-8")

    def test_detects_known_app_server_history(self) -> None:
        manifest = {
            "items": [
                {
                    "status": "succeeded",
                    "raw_final_response": {
                        "unresolved_issues": [
                            "codex exec failed with an in-process app-server permission error",
                        ],
                    },
                }
            ]
        }

        self.assertTrue(self.run_batch.has_codex_app_server_history(manifest))

    def test_reconcile_marks_manual_completion_succeeded(self) -> None:
        trash_root = Path.cwd() / "待删除" / "skill-tests"
        trash_root.mkdir(parents=True, exist_ok=True)
        root = trash_root / f"run-batch-{time.time_ns()}"
        output_dir = root / "P05-P05"
        review_dir = output_dir / "review"
        review_dir.mkdir(parents=True)
        tex_path = output_dir / "final.tex"
        pdf_path = output_dir / "final.pdf"
        tex_path.write_text("\\documentclass{article}\\begin{document}ok\\end{document}", encoding="utf-8")
        pdf_path.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
        (review_dir / "consistency_review.md").write_text("ok", encoding="utf-8")
        (review_dir / "independent_review.md").write_text("complete enough", encoding="utf-8")
        self.write_pyramid_gate_reports(output_dir)
        tex_path.write_text("\\documentclass{article}\\begin{document}ok\\end{document}", encoding="utf-8")

        item = {
            "part_id": "BV_test_p005",
            "index": 5,
            "title": "P05",
            "output_dir": str(output_dir),
            "status": "failed",
            "attempt": 1,
            "attempts": 1,
            "status_path": str(root / "parts" / "BV_test_p005" / "status.json"),
            "prompt_path": str(root / "prompts" / "P05.md"),
            "log_path": str(root / "logs" / "P05.jsonl"),
            "last_message_path": str(root / "last-messages" / "P05.json"),
            "output_schema_path": "schema.json",
            "artifact_checks": {},
            "raw_final_response": {},
        }
        manifest = {"items": [item], "summary": {}}
        manifest_path = root / "manifest.json"

        reconciled = self.run_batch.reconcile_items(manifest_path, manifest, [item])

        self.assertEqual([5], [entry["index"] for entry in reconciled])
        self.assertEqual("succeeded", item["status"])
        self.assertEqual("none", item["failure_class"])
        self.assertEqual(str(pdf_path), item["pdf_path"])
        self.assertEqual(str(tex_path), item["tex_path"])
        self.assertTrue(Path(item["status_path"]).exists())
        saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(1, saved_manifest["summary"]["succeeded"])

    def test_reconcile_requires_pyramid_gate_reports(self) -> None:
        trash_root = Path.cwd() / "待删除" / "skill-tests"
        trash_root.mkdir(parents=True, exist_ok=True)
        root = trash_root / f"run-batch-missing-pyramid-{time.time_ns()}"
        output_dir = root / "P06-P06"
        review_dir = output_dir / "review"
        review_dir.mkdir(parents=True)
        tex_path = output_dir / "final.tex"
        pdf_path = output_dir / "final.pdf"
        tex_path.write_text("\\documentclass{article}\\begin{document}ok\\end{document}", encoding="utf-8")
        pdf_path.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
        (review_dir / "consistency_review.md").write_text("ok", encoding="utf-8")
        (review_dir / "independent_review.md").write_text("complete enough", encoding="utf-8")

        item = {
            "part_id": "BV_test_p006",
            "index": 6,
            "title": "P06",
            "output_dir": str(output_dir),
            "status": "failed",
            "attempt": 1,
            "attempts": 1,
            "status_path": str(root / "parts" / "BV_test_p006" / "status.json"),
            "prompt_path": str(root / "prompts" / "P06.md"),
            "log_path": str(root / "logs" / "P06.jsonl"),
            "last_message_path": str(root / "last-messages" / "P06.json"),
            "output_schema_path": "schema.json",
            "artifact_checks": {},
            "raw_final_response": {},
        }
        manifest = {"items": [item], "summary": {}}
        manifest_path = root / "manifest.json"

        reconciled = self.run_batch.reconcile_items(manifest_path, manifest, [item])

        self.assertEqual([], reconciled)
        self.assertNotEqual("succeeded", item["status"])
        self.assertIn("review/pyramid reports passing --enforce-gate", item["next_action"])


if __name__ == "__main__":
    unittest.main()

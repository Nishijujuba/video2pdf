import importlib.util
import os
import unittest
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def load_script():
    script_path = Path(__file__).with_name("normalize_video_workspace.py")
    spec = importlib.util.spec_from_file_location("normalize_video_workspace", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def set_mtime(path: Path, value: str) -> None:
    timestamp = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()
    os.utime(path, (timestamp, timestamp))


class NormalizeVideoWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / "待删除" / "normalize-video-workspace-tests" / uuid4().hex
        self.root.mkdir(parents=True)
        self.script = load_script()

    def tearDown(self) -> None:
        pass

    def write_text(self, relative: str, content: str, mtime: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        set_mtime(path, mtime)
        return path

    def write_pdf(self, relative: str, mtime: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7\n%%EOF\n")
        set_mtime(path, mtime)
        return path

    def test_single_video_uses_main_tex_title_and_earliest_durable_date(self) -> None:
        self.write_text(
            "Raw Video/main.tex",
            r"\newcommand{\notetitle}{高质量 Agent 技能设计}",
            "2026-07-02 10:00:00",
        )
        self.write_text("Raw Video/outline_contract.md", "# Outline", "2026-07-01 09:00:00")
        self.write_pdf("Raw Video/main.pdf", "2026-07-03 11:00:00")

        rows = self.script.build_plan(self.root)

        self.assertEqual(1, len(rows))
        self.assertEqual("high", rows[0]["confidence"])
        self.assertEqual("高质量 Agent 技能设计_20260701", rows[0]["target_name"])
        self.assertEqual("outline_contract.md", rows[0]["date_source"])

    def test_series_episode_name_uses_two_digit_episode(self) -> None:
        self.write_text(
            "CS336_p02_PyTorch_einops/main.tex",
            r"\newcommand{\notetitle}{PyTorch 与 einops}",
            "2026-06-08 10:00:00",
        )
        self.write_pdf("CS336_p02_PyTorch_einops/main.pdf", "2026-06-08 11:00:00")

        rows = self.script.build_plan(self.root)

        self.assertEqual("high", rows[0]["confidence"])
        self.assertEqual("CS336_02_20260608", rows[0]["target_name"])

    def test_series_without_reliable_episode_uses_best_content_title(self) -> None:
        self.write_text(
            "最优传输-Minkowski问题/main.tex",
            r"\newcommand{\notetitle}{Minkowski 问题}",
            "2026-06-15 08:00:00",
        )
        self.write_pdf("最优传输-Minkowski问题/main.pdf", "2026-06-15 09:00:00")
        self.write_text(
            "最优传输-几何变分算法/main.tex",
            r"\newcommand{\notetitle}{几何变分算法}",
            "2026-06-15 08:30:00",
        )
        self.write_pdf("最优传输-几何变分算法/main.pdf", "2026-06-15 09:30:00")

        rows = {row["source_name"]: row for row in self.script.build_plan(self.root)}

        self.assertEqual("最优传输_Minkowski 问题_20260615", rows["最优传输-Minkowski问题"]["target_name"])
        self.assertEqual("最优传输_几何变分算法_20260615", rows["最优传输-几何变分算法"]["target_name"])
        self.assertEqual("high", rows["最优传输-Minkowski问题"]["confidence"])

    def test_missing_final_delivery_identity_goes_to_low_confidence_review(self) -> None:
        self.write_text("Scratch/main.log", "temporary compile output", "2026-07-01 09:00:00")
        (self.root / "Scratch" / "frames").mkdir(parents=True)

        rows = self.script.build_plan(self.root)

        self.assertEqual(1, len(rows))
        self.assertEqual("low", rows[0]["confidence"])
        self.assertEqual("workspace/低置信目录/Scratch", rows[0]["target_relative"])
        self.assertIn("missing final-delivery identity", rows[0]["reason"])

    def test_outline_contract_prefix_is_removed_from_markdown_title(self) -> None:
        self.write_text(
            "OutlineOnly/outline_contract.md",
            "# Outline Contract: Black Hat USA 2025 Timesketch AI Agent\n",
            "2026-06-12 09:00:00",
        )
        self.write_pdf("OutlineOnly/main.pdf", "2026-06-12 10:00:00")

        rows = self.script.build_plan(self.root)

        self.assertEqual("Black Hat USA 2025 Timesketch AI Agent_20260612", rows[0]["target_name"])

    def test_generic_fixed_pdf_name_is_not_final_delivery_identity(self) -> None:
        self.write_pdf("GenericPdf/main_fixed.pdf", "2026-07-01 10:00:00")

        rows = self.script.build_plan(self.root)

        self.assertEqual("low", rows[0]["confidence"])
        self.assertIn("missing final-delivery identity", rows[0]["reason"])

    def test_notes_pdf_is_final_delivery_identity(self) -> None:
        self.write_pdf("Delivered Notes/notes.pdf", "2026-07-01 10:00:00")

        rows = self.script.build_plan(self.root)

        self.assertEqual("high", rows[0]["confidence"])
        self.assertEqual("Delivered Notes_20260701", rows[0]["target_name"])
        self.assertEqual("notes.pdf", rows[0]["identity_source"])

    def test_build_main_pdf_is_final_delivery_identity(self) -> None:
        self.write_pdf("BuildOnly/build/main.pdf", "2026-05-19 12:11:28")

        rows = self.script.build_plan(self.root)

        self.assertEqual("high", rows[0]["confidence"])
        self.assertEqual("BuildOnly_20260519", rows[0]["target_name"])
        self.assertEqual("build/main.pdf", rows[0]["identity_source"])
        self.assertEqual("build/main.pdf", rows[0]["date_source"])

    def test_build_main_pdf_is_counted_when_main_tex_provides_title(self) -> None:
        self.write_text(
            "BuildPdfWithTex/main.tex",
            r"\newcommand{\notetitle}{构建目录里的最终 PDF}",
            "2026-05-19 12:10:58",
        )
        self.write_pdf("BuildPdfWithTex/build/main.pdf", "2026-05-19 12:11:28")

        rows = self.script.build_plan(self.root)

        self.assertEqual("high", rows[0]["confidence"])
        self.assertEqual("main.tex", rows[0]["identity_source"])
        self.assertEqual(1, rows[0]["pdf_count"])
        self.assertTrue(rows[0]["main_pdf"])

    def test_conflicting_target_names_go_to_low_confidence_review(self) -> None:
        self.write_text("First/main.tex", r"\newcommand{\notetitle}{重复标题}", "2026-07-01 09:00:00")
        self.write_pdf("First/main.pdf", "2026-07-01 10:00:00")
        self.write_text("Second/main.tex", r"\newcommand{\notetitle}{重复标题}", "2026-07-01 09:30:00")
        self.write_pdf("Second/main.pdf", "2026-07-01 10:30:00")

        rows = {row["source_name"]: row for row in self.script.build_plan(self.root)}

        self.assertEqual("low", rows["First"]["confidence"])
        self.assertEqual("low", rows["Second"]["confidence"])
        self.assertEqual("workspace/低置信目录/First", rows["First"]["target_relative"])
        self.assertEqual("target path conflict", rows["First"]["reason"])

    def test_apply_plan_uses_directory_rename_without_shutil_move_fallback(self) -> None:
        source = self.root / "Source"
        source.mkdir()
        (source / "main.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
        target = self.root / "workspace" / "Target"
        self.assertFalse(hasattr(self.script, "shutil"))
        self.script.apply_plan(
            [
                {
                    "source_path": str(source),
                    "target_path": str(target),
                }
            ]
        )

        self.assertFalse(source.exists())
        self.assertTrue((target / "main.pdf").exists())

    def test_apply_plan_records_rename_errors_and_continues(self) -> None:
        locked = self.root / "Locked"
        locked.mkdir()
        (locked / "main.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
        movable = self.root / "Movable"
        movable.mkdir()
        (movable / "main.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
        original_rename = self.script.rename_directory

        def fake_rename(source, target):
            if Path(source).name == "Locked":
                raise PermissionError("locked source")
            return original_rename(source, target)

        self.script.rename_directory = fake_rename
        try:
            errors = self.script.apply_plan(
                [
                    {"source_path": str(locked), "target_path": str(self.root / "workspace" / "LockedTarget")},
                    {"source_path": str(movable), "target_path": str(self.root / "workspace" / "MovableTarget")},
                ]
            )
        finally:
            self.script.rename_directory = original_rename

        self.assertEqual(1, len(errors))
        self.assertEqual(str(locked), errors[0]["source_path"])
        self.assertTrue(locked.exists())
        self.assertFalse(movable.exists())
        self.assertTrue((self.root / "workspace" / "MovableTarget" / "main.pdf").exists())


if __name__ == "__main__":
    unittest.main()

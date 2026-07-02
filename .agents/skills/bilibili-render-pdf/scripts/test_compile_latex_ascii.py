from __future__ import annotations

import importlib.util
from pathlib import Path
import time
import unittest


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


if __name__ == "__main__":
    unittest.main()

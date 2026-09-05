from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


from video2pdf_workflow_kernel.content_production import ContentProduction


class Issue111SourceNoteEscapingTests(unittest.TestCase):
    def test_figure_contribution_renders_ampersand_in_source_note_as_literal_text(self) -> None:
        manifest = {
            "slot_id": "figure_01",
            "caption": "Evidence overview",
            "source": {"kind": "generated_diagram", "value": "Q&A synthesis"},
        }

        contribution = ContentProduction._figure_contribution(manifest)

        self.assertIn(b"Source (generated\\_diagram): Q\\&A synthesis", contribution)
        self.assertNotIn(b"Source (generated\\_diagram): Q&A synthesis", contribution)

    def test_figure_contribution_escapes_reserved_source_text_without_changing_authored_caption_or_manifest(self) -> None:
        caption = r"\textbf{Q\&A}: $x_1$ is 50\%"
        manifest = {
            "slot_id": "figure_02",
            "caption": caption,
            "source": {
                "kind": "source_timestamp",
                "value": r"\root {draft}_50% #1 costs $5 & rises^2 ~",
            },
        }
        original_manifest = deepcopy(manifest)

        contribution = ContentProduction._figure_contribution(manifest)

        self.assertIn(f"\\caption{{{caption}}}\n".encode("utf-8"), contribution)
        self.assertIn(
            rb"Source (source\_timestamp): \textbackslash{}root \{draft\}\_50\% \#1 costs \$5 \& rises\textasciicircum{}2 \textasciitilde{}",
            contribution,
        )
        self.assertEqual(original_manifest, manifest)


if __name__ == "__main__":
    unittest.main()

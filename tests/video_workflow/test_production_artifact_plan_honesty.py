from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402


class ProductionArtifactPlanHonestyTests(unittest.TestCase):
    def test_run_plan_excludes_circular_transaction_journal_authority(self) -> None:
        plan = VideoWorkflowKernel._production_artifact_plan("0" * 32)
        logical_ids = {artifact["logical_id"] for artifact in plan["artifacts"]}

        self.assertNotIn("source_publication_journal", logical_ids)
        self.assertIn("source_manifest", logical_ids)


if __name__ == "__main__":
    unittest.main()

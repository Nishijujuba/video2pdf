from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow import test_source_publication_integration  # noqa: E402
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.source_acquisition import (  # noqa: E402
    SourceReopenFault,
)
from video2pdf_workflow_kernel.utils import read_json, sha256_file  # noqa: E402


class SourceReopenIntegrationTests(unittest.TestCase):
    def test_reconcile_commits_reopened_v3_run_to_file_backed_authority(
        self,
    ) -> None:
        kernel, run_dir, _ = (
            test_source_publication_integration.build_decision_ready_authority()
        )
        self.assertEqual(kernel.control_store.check().schema_version, 9)
        kernel.finalize_production_source(
            run_dir,
            published_at="2026-07-18T12:00:00+08:00",
        )
        ready = read_json(run_dir / "workflow/run.json")
        self.assertEqual(ready["schema_version"], "3.0.0")
        self.assertEqual(ready["source_state"], "ready")
        self.assertIsNotNone(ready["source_version"])

        with self.assertRaisesRegex(
            SourceReopenFault,
            "after_reopen_run_record_commit",
        ):
            kernel.source_reopen(
                run_dir,
                reason="correct production source evidence",
                fault_point="after_reopen_run_record_commit",
            )

        pending = kernel.control_store.prepared_run_state_mutation(ready["run_id"])
        self.assertIsNotNone(pending)
        restarted = VideoWorkflowKernel(kernel.workspace_root)
        reconciled = restarted.reconcile_run(run_dir)
        reopened = read_json(run_dir / "workflow/run.json")

        self.assertEqual(reconciled.outcome, "current_state_verified")
        self.assertEqual(reopened["source_epoch"], ready["source_epoch"] + 1)
        self.assertEqual(reopened["source_state"], "stale")
        self.assertIsNone(reopened["source_version"])
        self.assertEqual(reopened["phase"], "source_acquisition")
        self.assertEqual(
            reopened["checkpoints"]["run_initialized"]["status"],
            "current",
        )
        for checkpoint in (
            "source_candidates_ready",
            "source_acquisition_decision_ready",
            "source_ready",
        ):
            self.assertEqual(
                reopened["checkpoints"][checkpoint]["status"],
                "stale",
            )
        self.assertIsNone(
            restarted.control_store.prepared_run_state_mutation(ready["run_id"])
        )
        self.assertEqual(
            restarted.control_store.current_run_record_sha(ready["run_id"]),
            sha256_file(run_dir / "workflow/run.json"),
        )


if __name__ == "__main__":
    unittest.main()

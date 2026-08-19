from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow.test_source_publication_integration import (  # noqa: E402
    build_decision_ready_authority,
)
from video2pdf_workflow_kernel.errors import ArtifactDrift  # noqa: E402
from video2pdf_workflow_kernel.utils import read_json, sha256_file  # noqa: E402


class ProductionSourceDriftRecoveryTests(unittest.TestCase):
    def test_reconcile_commits_a_contract_valid_stale_v3_run(self) -> None:
        kernel, run_dir, _ = build_decision_ready_authority()
        kernel.finalize_production_source(
            run_dir,
            published_at="2026-07-18T12:00:00+08:00",
        )
        manifest = read_json(run_dir / "source/manifest.json")
        drifted = run_dir / manifest["artifacts"][0]["path"]
        drifted.write_bytes(drifted.read_bytes() + b"drift")

        with self.assertRaises(ArtifactDrift):
            kernel.reconcile_run(run_dir)

        stale = read_json(run_dir / "workflow/run.json")
        kernel.contracts.validate_run_record(stale)
        self.assertEqual(stale["source_state"], "stale")
        self.assertIsNone(stale["source_version"])
        self.assertEqual(stale["phase"], "source_acquisition")
        self.assertEqual(stale["checkpoints"]["run_initialized"]["status"], "current")
        self.assertTrue(
            all(
                checkpoint["status"] == "stale"
                for name, checkpoint in stale["checkpoints"].items()
                if name != "run_initialized"
            )
        )
        self.assertEqual(
            kernel.control_store.current_run_record_sha(stale["run_id"]),
            sha256_file(run_dir / "workflow/run.json"),
        )


if __name__ == "__main__":
    unittest.main()

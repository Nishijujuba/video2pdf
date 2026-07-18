from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.video_workflow.test_source_publication_integration import (  # noqa: E402
    build_decision_ready_authority,
)
from video2pdf_workflow_kernel import source_package  # noqa: E402
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.source_publication import (  # noqa: E402
    SourcePublicationFault,
)
from video2pdf_workflow_kernel.utils import read_json  # noqa: E402


class SourcePublicationPreIntentTests(unittest.TestCase):
    def test_candidate_write_is_fenced_and_changed_timestamp_replays(self) -> None:
        kernel, run_dir, prior = build_decision_ready_authority()
        original_write = source_package._write_materialization
        observations: list[tuple[str, bool]] = []

        def observe_preintent(pending):
            active = kernel.control_store.active_source_publication(prior["run_id"])
            destination = (
                run_dir
                / "work/source-acquisition/publications"
                / ("missing" if active is None else str(active["intent_id"]))
                / "candidate/source"
            )
            observations.append(
                (
                    "missing" if active is None else str(active["state"]),
                    destination.exists(),
                )
            )
            return original_write(pending)

        with patch(
            "video2pdf_workflow_kernel.source_package._write_materialization",
            side_effect=observe_preintent,
        ):
            with self.assertRaisesRegex(
                SourcePublicationFault,
                "after_source_publication_intent_prepared",
            ):
                kernel.finalize_production_source(
                    run_dir,
                    published_at="2026-07-18T12:00:00+08:00",
                    fault_point="after_source_publication_intent_prepared",
                )

        self.assertEqual(observations, [("PREPARED", False)])
        active = kernel.control_store.active_source_publication(prior["run_id"])
        self.assertIsNotNone(active)
        self.assertEqual(active["state"], "PREPARED")
        self.assertIsNone(active["journal_sha256"])
        candidate_manifest = (
            run_dir
            / "work/source-acquisition/publications"
            / str(active["intent_id"])
            / "candidate/source/manifest.json"
        )
        self.assertTrue(candidate_manifest.is_file())
        self.assertEqual(
            read_json(candidate_manifest)["published_at"],
            "2026-07-18T12:00:00+08:00",
        )

        restarted = VideoWorkflowKernel(kernel.workspace_root)
        result = restarted.finalize_production_source(
            run_dir,
            published_at="2026-07-18T13:30:00+08:00",
        )
        current = read_json(run_dir / "workflow/run.json")
        manifest = read_json(run_dir / "source/manifest.json")
        committed = restarted.control_store.source_publication_by_id(result.intent_id)

        self.assertEqual(current["source_state"], "ready")
        self.assertEqual(current["phase"], "source_ready")
        self.assertEqual(manifest["published_at"], "2026-07-18T12:00:00+08:00")
        self.assertEqual(committed["state"], "COMMITTED")
        self.assertEqual(result.manifest_sha256, committed["source_manifest_sha256"])
        replacement = json.loads(str(committed["replacement_run_record_json"]))
        self.assertEqual(
            replacement["artifact_generations"]["source_manifest"]["committed_at"],
            "2026-07-18T12:00:00+08:00",
        )

    def test_partial_candidate_write_resumes_from_frozen_intent(self) -> None:
        kernel, run_dir, prior = build_decision_ready_authority()
        original_write = source_package._write_materialization
        written_paths: list[Path] = []

        def interrupt_after_one_file(pending):
            first_path = sorted(pending, key=lambda path: path.as_posix())[0]
            original_write({first_path: pending[first_path]})
            written_paths.append(first_path)
            raise OSError("simulated candidate writer crash")

        with patch(
            "video2pdf_workflow_kernel.source_package._write_materialization",
            side_effect=interrupt_after_one_file,
        ):
            with self.assertRaisesRegex(OSError, "candidate writer crash"):
                kernel.finalize_production_source(
                    run_dir,
                    published_at="2026-07-18T12:00:00+08:00",
                )

        self.assertEqual(len(written_paths), 1)
        self.assertTrue(written_paths[0].is_file())
        active = kernel.control_store.active_source_publication(prior["run_id"])
        self.assertIsNotNone(active)
        self.assertEqual(active["state"], "PREPARED")
        self.assertIsNone(active["journal_sha256"])

        restarted = VideoWorkflowKernel(kernel.workspace_root)
        result = restarted.finalize_production_source(
            run_dir,
            published_at="2026-07-19T09:00:00+08:00",
        )
        manifest = read_json(result.manifest_path)

        self.assertEqual(
            read_json(run_dir / "workflow/run.json")["source_state"],
            "ready",
        )
        self.assertEqual(manifest["published_at"], "2026-07-18T12:00:00+08:00")
        self.assertEqual(
            restarted.control_store.source_publication_by_id(result.intent_id)["state"],
            "COMMITTED",
        )


if __name__ == "__main__":
    unittest.main()

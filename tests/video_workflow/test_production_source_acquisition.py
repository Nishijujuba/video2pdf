from __future__ import annotations

import unittest
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class ProductionSourceAcquisitionTests(unittest.TestCase):
    def test_source_identity_and_content_version_are_distinct_authorities(self) -> None:
        from video2pdf_workflow_kernel.source_acquisition import (
            SourceArtifactBinding,
            derive_source_identity,
            derive_source_version,
        )

        identity = derive_source_identity("youtube", "jNQXAC9IVRw")
        self.assertEqual(
            identity,
            "e98052f0a5a613c7a93be1a0e2e96e3144ae069fbbeecef812607a0aa8439a0e",
        )

        bindings = (
            SourceArtifactBinding(
                logical_id="video",
                role="video",
                media_type="video/mp4",
                sha256="1" * 64,
                size_bytes=19,
                language=None,
                subtitle_kind=None,
                technical_probe={
                    "status": "pass",
                    "duration_seconds": 19.0,
                    "stream_types": ["video", "audio"],
                    "codec_names": ["h264", "aac"],
                },
            ),
            SourceArtifactBinding(
                logical_id="subtitle.en.manual",
                role="subtitle",
                media_type="application/x-subrip",
                sha256="2" * 64,
                size_bytes=29,
                language="en",
                subtitle_kind="manual",
                technical_probe={
                    "status": "pass",
                    "duration_seconds": 19.0,
                    "stream_types": ["subtitle"],
                    "codec_names": ["srt"],
                },
            ),
        )
        version = derive_source_version(identity, bindings)
        self.assertEqual(
            version,
            "430be2e44f2cedeba9140afe55677e9b1010bd6e754b453287a0dff54bbea752",
        )

        # Run-local provenance is intentionally absent from the API.  Sorting
        # makes fresh acquisition and verified import converge on the same
        # content version when they publish identical original evidence.
        self.assertEqual(version, derive_source_version(identity, tuple(reversed(bindings))))

    def test_english_subtitle_policy_and_explicit_whisper_fallback_are_bounded(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_acquisition import (
            SubtitleCandidate,
            build_allowed_source_judgment,
            validate_source_judgment,
        )

        candidates = (
            SubtitleCandidate("3" * 64, "zh-Hans", "manual", True),
            SubtitleCandidate("4" * 64, "en", "automatic", True),
            SubtitleCandidate("5" * 64, "en-US", "manual", True),
        )
        allowed = build_allowed_source_judgment(
            candidates,
            english_primary=True,
            whisper_allowed=True,
            whisper_audio_candidate_id="6" * 64,
        )
        self.assertEqual(
            allowed["subtitle_candidate_ids"],
            ["5" * 64, "4" * 64],
        )
        self.assertEqual(
            allowed["whisper_choices"],
            ["not_required", "use_whisper", "unavailable"],
        )
        self.assertEqual(allowed["whisper_audio_candidate_id"], "6" * 64)

        validate_source_judgment(
            allowed,
            {
                "selected_subtitle_candidate_id": None,
                "subtitle_selection_rationale": "The available English tracks are unusable.",
                "whisper_fallback": {
                    "choice": "use_whisper",
                    "rationale": "English is primary evidence for this request.",
                },
                "known_gaps": [],
            },
        )
        with self.assertRaisesRegex(Exception, "not allowed"):
            validate_source_judgment(
                allowed,
                {
                    "selected_subtitle_candidate_id": "3" * 64,
                    "subtitle_selection_rationale": "Use Chinese instead.",
                    "whisper_fallback": {
                        "choice": "not_required",
                        "rationale": "A subtitle exists.",
                    },
                    "known_gaps": [],
                },
            )

    def test_downstream_reader_rejects_noncurrent_validated_source_package(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.errors import ArtifactDrift
        from tests.video_workflow.test_source_publication_integration import (
            build_decision_ready_authority,
        )

        kernel, run_dir, _ = build_decision_ready_authority()
        kernel.finalize_production_source(
            run_dir,
            published_at="2026-07-18T12:00:00+08:00",
        )
        current = kernel.require_current_validated_source_package(run_dir)
        self.assertEqual(current["source_state"], "ready")
        kernel.source_reopen(
            run_dir,
            reason="exercise downstream stale rejection",
        )
        with self.assertRaisesRegex(ArtifactDrift, "not current"):
            kernel.require_current_validated_source_package(run_dir)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from video2pdf_workflow_kernel.errors import (  # noqa: E402
    ArtifactDrift,
    ContractError,
    TaskFault,
)
from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel  # noqa: E402
from video2pdf_workflow_kernel.source_candidates import (  # noqa: E402
    SourceCandidatePolicy,
)
from video2pdf_workflow_kernel.utils import (  # noqa: E402
    read_json,
    sha256_file,
    write_json_atomic,
)


TASK_START = "2026-07-18T04:01:00Z"
TEST_ROOT = PROJECT_ROOT / "待删除" / "pcp"


def trusted_provider_verifier(**identity: object) -> str:
    return f"provider-proof://candidate-promotion/{identity['terminal_result_id']}"


def create_directory_link(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise unittest.SkipTest("directory symlinks are unavailable")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise unittest.SkipTest("directory junctions are unavailable")


class ProviderCandidatePromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        from video2pdf_workflow_kernel.adapters import (
            PlatformProbeRequest,
            RecordedCommandRunner,
            YouTubePlatformAdapter,
            YtDlpRuntime,
        )

        root = TEST_ROOT / uuid.uuid4().hex[:6]
        workspace = root / "workspace"
        staging = root / "provider-staging"
        cookie = root / "credentials/cookies.txt"
        staging.mkdir(parents=True)
        cookie.parent.mkdir(parents=True)
        cookie.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSID\trecorded\n",
            encoding="utf-8",
        )
        adapter = YouTubePlatformAdapter(
            YtDlpRuntime(
                python_executable=Path("python"),
                ffmpeg_dir=Path("ffmpeg-bin"),
                ffprobe_executable=Path("ffprobe"),
            )
        )
        runner = RecordedCommandRunner(
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/providers/youtube/fresh-download"
        )
        self.kernel = VideoWorkflowKernel(
            workspace,
            resource_provider_verifiers={
                "candidate-promotion-test": trusted_provider_verifier,
            },
        )
        probe = self.kernel.bootstrap_production_source(
            adapter=adapter,
            request=PlatformProbeRequest(
                source_url="https://www.youtube.com/watch?v=yt-test-001",
                localized_cookie_file=cookie,
                staging_root=staging,
            ),
            runner=runner,
            task_start="2026-07-18T12:00:00+08:00",
            request_id=f"candidate-promotion-{uuid.uuid4().hex[:8]}",
            provider_kind="recorded_fixture",
        )
        self.run_dir = self.kernel.initialize_production_source(probe).run_dir
        self.prepared = self.kernel.prepare_production_source_task(
            self.run_dir,
            task_stage="provider_acquisition",
            logical_task_key="source-provider-epoch-1",
            prepared_at=TASK_START,
        )

    def _claim(self, worker: str):
        return self.kernel.claim_task(
            self.run_dir,
            self.prepared.task_id,
            coordinator_session_id=f"coordinator-{worker}",
            worker_id=worker,
        )

    def _release(self, claim) -> None:
        launch_tokens: list[str] = []
        self.kernel.launch_admitted_task(
            claim.attempt_id,
            claim.claim_generation,
            ("youtube_download",),
            lambda token: launch_tokens.append(token) or "started",
        )
        self.kernel.release_resource_lease(
            claim.attempt_id,
            claim.claim_generation,
            launch_tokens[0],
            terminal_evidence={
                "evidence_class": "provider_terminal_result",
                "provider": "candidate-promotion-test",
                "terminal_result_id": f"terminal-{claim.attempt_id}",
                "declared_outcome": "succeeded",
                "observed_at": TASK_START,
            },
        )

    def _stage(self, claim, marker: str) -> dict[str, bytes]:
        record = read_json(self.run_dir / "workflow/run.json")
        epoch_root = (
            f"work/source-acquisition/candidates/e{record['source_epoch']}"
        )
        payloads = {
            "metadata/platform.json": (
                json.dumps(
                    {"title": "Recorded Source", "marker": marker},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            ),
            "cover/cover.jpg": b"recorded-jpeg-" + marker.encode("utf-8"),
            "media/video.mp4": b"recorded-video-" + marker.encode("utf-8"),
        }
        attempt_candidate_root = claim.attempt_dir / "o/candidates"
        for relative, payload in payloads.items():
            path = attempt_candidate_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        def candidate(
            relative: str,
            *,
            role: str,
            media_type: str,
            stream_types: list[str],
            codec_names: list[str],
        ) -> dict:
            payload = payloads[relative]
            digest = hashlib.sha256(payload).hexdigest()
            staged_path = f"{epoch_root}/{relative}"
            return {
                "candidate_id": hashlib.sha256(
                    f"candidate:{staged_path}:{digest}".encode("utf-8")
                ).hexdigest(),
                "role": role,
                "staged_path": staged_path,
                "media_type": media_type,
                "sha256": digest,
                "size_bytes": len(payload),
                "origin": "platform_download",
                "language": None,
                "subtitle_kind": None,
                "technical_probe": {
                    "status": "pass",
                    "duration_seconds": 30 if role == "video" else None,
                    "stream_types": stream_types,
                    "codec_names": codec_names,
                },
            }

        candidates = [
            candidate(
                "metadata/platform.json",
                role="metadata",
                media_type="application/json",
                stream_types=["metadata"],
                codec_names=["json"],
            ),
            candidate(
                "cover/cover.jpg",
                role="cover",
                media_type="image/jpeg",
                stream_types=["image"],
                codec_names=["jpeg"],
            ),
            candidate(
                "media/video.mp4",
                role="video",
                media_type="video/mp4",
                stream_types=["video", "audio"],
                codec_names=["h264", "aac"],
            ),
        ]
        policy = SourceCandidatePolicy(
            content_classification="language_learning",
            subtitle_language_priority=("en", "zh-Hans"),
            whisper_allowed=True,
        ).binding()
        acquisition_id = hashlib.sha256(
            f"acquisition:{record['run_id']}:{record['source_epoch']}:{marker}".encode(
                "utf-8"
            )
        ).hexdigest()[:32]
        inventory = {
            "schema_name": "source-candidate-inventory",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": record["run_id"],
            "acquisition_id": acquisition_id,
            "source_epoch": record["source_epoch"],
            "mode": "fresh_download",
            "adapter": {"id": "youtube", "contract_version": "1.0.0"},
            "canonical_platform": "youtube",
            "canonical_item_id": record["canonical_item_id"],
            "source_identity_scheme": "canonical-platform-item-v1",
            "source_identity": record["source_identity"],
            "provider": {
                "kind": "recorded_fixture",
                "recording_sha256": hashlib.sha256(b"recording").hexdigest(),
                "tool_versions": [
                    {"name": "recorded-youtube", "version": "1.0.0"},
                    {"name": "ffprobe", "version": "7.1"},
                ],
            },
            "authentication_classification": "cookie_accepted",
            "policy_binding": policy,
            "source_metadata": {
                "original_title": record["original_title"],
                "duration_seconds": 30,
            },
            "commands": [
                {
                    "command_id": "download",
                    "purpose": "download",
                    "command_argv_redacted": [
                        "recorded-youtube",
                        "--cookies",
                        "<localized-cookie-file>",
                        "--download",
                        "https://www.youtube.com/watch?v=yt-test-001",
                    ],
                    "exit_classification": "success",
                    "sanitized_log_sha256": hashlib.sha256(b"log").hexdigest(),
                }
            ],
            "candidates": candidates,
            "import_binding": None,
            "status": "candidates_ready",
        }
        output_root = claim.attempt_dir / "o"
        inventory_sha = write_json_atomic(
            output_root / "candidate-inventory.json", inventory
        )
        semantic_task_id = self.kernel.derive_production_source_task_id(
            self.run_dir,
            task_stage="semantic_judgment",
            logical_task_key="source-semantic-epoch-1",
        )
        video = candidates[-1]
        skeleton = {
            "schema_name": "source-acquisition-decision-skeleton",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "run_id": record["run_id"],
            "source_epoch": record["source_epoch"],
            "acquisition_id": acquisition_id,
            "task_id": semantic_task_id,
            "source_identity": record["source_identity"],
            "candidate_inventory": {
                "path": "work/source-acquisition/candidate-inventory.json",
                "generation": 1,
                "sha256": inventory_sha,
            },
            "policy_binding": {
                "policy_id": "source-acquisition-policy",
                "version": "1.0.0",
                "sha256": policy["sha256"],
            },
            "allowed_judgment": {
                "subtitle_candidate_ids": [],
                "whisper_choices": ["not_required", "use_whisper", "unavailable"],
                "whisper_audio_candidate_id": video["candidate_id"],
                "known_gap_codes": [
                    "metadata_incomplete",
                    "missing_audio",
                    "missing_cover",
                    "missing_subtitles",
                    "other",
                    "partial_subtitles",
                    "subtitle_quality",
                ],
            },
            "required_judgment_fields": [
                "selected_subtitle_candidate_id",
                "subtitle_selection_rationale",
                "whisper_fallback.choice",
                "whisper_fallback.rationale",
                "known_gaps",
            ],
            "target_checkpoint": "source_acquisition_decision_ready",
            "status": "prepared",
        }
        write_json_atomic(output_root / "decision.skeleton.json", skeleton)
        return payloads

    def _complete(self, claim) -> None:
        self.kernel.complete_task(
            self.run_dir,
            task_id=self.prepared.task_id,
            attempt_id=claim.attempt_id,
            claim_generation=claim.claim_generation,
        )

    @staticmethod
    def _quarantine(path: Path, label: str) -> Path:
        quarantine = (
            PROJECT_ROOT
            / "待删除/pcp-quarantine"
            / f"{label}-{uuid.uuid4().hex}"
            / path.name
        )
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        path.replace(quarantine)
        return quarantine

    def test_provider_envelope_declares_its_epoch_candidate_root(self) -> None:
        envelope = read_json(self.prepared.envelope_path)

        self.assertEqual(envelope["source_epoch"], 1)
        self.assertIn(
            "work/source-acquisition/candidates/e1",
            envelope["write_set"],
        )

    def test_completion_rejects_a_missing_inventory_candidate(self) -> None:
        claim = self._claim("missing-candidate")
        self._stage(claim, "missing")
        self._release(claim)
        missing = claim.attempt_dir / "o/candidates/cover/cover.jpg"
        quarantined = self._quarantine(missing, "missing-candidate")

        with self.assertRaisesRegex(ContractError, "exact declared outputs"):
            self._complete(claim)
        self.assertTrue(quarantined.is_file())

    def test_completion_rejects_an_extra_attempt_candidate(self) -> None:
        claim = self._claim("extra-candidate")
        self._stage(claim, "extra")
        extra = claim.attempt_dir / "o/candidates/subtitles/undeclared.srt"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nUndeclared.\n",
            encoding="utf-8",
        )
        self._release(claim)

        with self.assertRaisesRegex(ContractError, "exact declared outputs"):
            self._complete(claim)

    def test_completion_rejects_candidate_hash_drift(self) -> None:
        claim = self._claim("candidate-hash-drift")
        self._stage(claim, "original")
        drifted = claim.attempt_dir / "o/candidates/media/video.mp4"
        drifted.write_bytes(b"different-provider-bytes")
        self._release(claim)

        with self.assertRaisesRegex(ArtifactDrift, "fingerprint differs"):
            self._complete(claim)

    def test_completion_rejects_a_linked_candidate_directory(self) -> None:
        claim = self._claim("linked-candidate")
        self._stage(claim, "linked")
        linked = claim.attempt_dir / "o/candidates/cover"
        target = self._quarantine(linked, "linked-candidate")
        create_directory_link(linked, target)
        self._release(claim)

        with self.assertRaisesRegex(ContractError, "link or reparse point"):
            self._complete(claim)

    def test_completion_rejects_a_hardlinked_candidate_file(self) -> None:
        claim = self._claim("hardlinked-candidate")
        self._stage(claim, "hardlinked")
        candidate = claim.attempt_dir / "o/candidates/media/video.mp4"
        mirror = (
            PROJECT_ROOT
            / "待删除/provider-candidate-hardlinks"
            / f"{uuid.uuid4().hex}.mp4"
        )
        mirror.parent.mkdir(parents=True, exist_ok=True)
        os.link(candidate, mirror)
        self._release(claim)

        with self.assertRaisesRegex(
            (ArtifactDrift, ContractError),
            "independent regular file|hardlink",
        ):
            self._complete(claim)

    def test_completion_rejects_a_preexisting_epoch_root_without_promotion_intent(
        self,
    ) -> None:
        claim = self._claim("preexisting-canonical")
        self._stage(claim, "attempt-only")
        self._release(claim)
        canonical_root = self.run_dir / "work/source-acquisition/candidates/e1"
        canonical_root.mkdir(parents=True)

        with self.assertRaisesRegex(
            ArtifactDrift, "without matching Promotion authority"
        ):
            self._complete(claim)

    def test_reclaimed_provider_attempt_promotes_only_the_replacement_candidate_set(
        self,
    ) -> None:
        first = self._claim("provider-a")
        first_payloads = self._stage(first, "V1")
        self._release(first)

        replacement = self.kernel.reclaim_task(
            self.run_dir,
            task_id=self.prepared.task_id,
            expected_attempt_id=first.attempt_id,
            expected_claim_generation=first.claim_generation,
            coordinator_session_id="coordinator-provider-b",
            worker_id="provider-b",
            reason="provider process stopped before Completion Gate",
        )
        replacement_payloads = self._stage(replacement, "V2")
        self._release(replacement)
        canonical_root = self.run_dir / "work/source-acquisition/candidates/e1"

        self.assertFalse(canonical_root.exists())
        self._complete(replacement)
        self.assertFalse(canonical_root.exists())
        with self.assertRaises(TaskFault):
            self.kernel.promote_task(
                self.run_dir,
                task_id=self.prepared.task_id,
                attempt_id=replacement.attempt_id,
                claim_generation=replacement.claim_generation,
                fault_point="after_output_published",
            )

        run_id = read_json(self.run_dir / "workflow/run.json")["run_id"]
        self.kernel.reconcile_authority("kernel_run", run_id)

        for relative, payload in replacement_payloads.items():
            self.assertEqual((canonical_root / relative).read_bytes(), payload)
            self.assertEqual(
                (first.attempt_dir / "o/candidates" / relative).read_bytes(),
                first_payloads[relative],
            )
        run = read_json(self.run_dir / "workflow/run.json")
        self.assertEqual(run["source_state"], "candidates_ready")
        self.assertEqual(
            {
                logical_id
                for logical_id in run["artifact_generations"]
                if logical_id.startswith("source_candidate_")
            },
            {"source_candidate_inventory"},
        )
        self.assertEqual(
            sha256_file(self.run_dir / "work/source-acquisition/candidate-inventory.json"),
            run["artifact_generations"]["source_candidate_inventory"]["sha256"],
        )


if __name__ == "__main__":
    unittest.main()

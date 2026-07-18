from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class _AdmissionRecordingKernel:
    def __init__(self) -> None:
        self.launches: list[tuple[str, int, tuple[str, ...]]] = []
        self.breakers: list[tuple[str, str, str, str | None]] = []

    def launch_admitted_task(
        self,
        attempt_id: str,
        claim_generation: int,
        required_resources: tuple[str, ...],
        launcher,
        *,
        fault_point: str | None = None,
    ):
        self.launches.append((attempt_id, claim_generation, required_resources))
        return launcher("admitted-launch-token")

    def set_resource_circuit_breaker(
        self,
        resource_class: str,
        *,
        state: str,
        reason: str,
        platform: str | None = None,
    ) -> dict:
        self.breakers.append((resource_class, state, reason, platform))
        return {"state": state}


class ProductionSourceAcquisitionHardeningTests(unittest.TestCase):
    @staticmethod
    def _ready_source_run(label: str) -> Path:
        from tests.video_workflow import test_source_publication_integration

        kernel, run_dir, _ = (
            test_source_publication_integration.build_decision_ready_authority()
        )
        kernel.finalize_production_source(
            run_dir,
            published_at="2026-07-18T12:00:00+08:00",
        )
        return run_dir

    def test_fresh_download_and_whisper_launch_only_through_resource_admission(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_acquisition import (
            AdmittedSourceProviderLauncher,
        )

        kernel = _AdmissionRecordingKernel()
        admitted = AdmittedSourceProviderLauncher(kernel)
        calls: list[tuple[str, str]] = []

        adapter_result = admitted.launch_adapter(
            attempt_id="a" * 24,
            claim_generation=1,
            resource_class="youtube_download",
            provider=lambda token: calls.append(("adapter", token)) or "downloaded",
        )
        whisper_result = admitted.launch_whisper(
            attempt_id="b" * 24,
            claim_generation=2,
            provider=lambda token: calls.append(("whisper", token)) or "transcribed",
        )

        self.assertEqual(adapter_result, "downloaded")
        self.assertEqual(whisper_result, "transcribed")
        self.assertEqual(
            kernel.launches,
            [
                ("a" * 24, 1, ("youtube_download",)),
                ("b" * 24, 2, ("whisper",)),
            ],
        )
        self.assertEqual(
            calls,
            [
                ("adapter", "admitted-launch-token"),
                ("whisper", "admitted-launch-token"),
            ],
        )

    def test_cookie_rejection_is_user_input_and_opens_only_platform_breaker(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.adapters import PlatformAdapterError
        from video2pdf_workflow_kernel.source_acquisition import record_source_blocker

        kernel = _AdmissionRecordingKernel()
        error = PlatformAdapterError(
            "platform cookie was rejected",
            classification="source_authentication_required",
            exit_code=30,
            blocker_kind="user_input",
            data={"authentication_classification": "cookie_rejected"},
        )

        blocker = record_source_blocker(kernel, "bilibili", error)

        self.assertEqual(blocker["kind"], "user_input")
        self.assertEqual(blocker["reason"], "cookie_rejected")
        self.assertEqual(blocker["breaker_state"], "open")
        self.assertRegex(blocker["evidence_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            kernel.breakers,
            [
                (
                    "bilibili_download",
                    "open",
                    "cookie_rejected",
                    "bilibili",
                )
            ],
        )
        self.assertNotIn("cookie", json.dumps(blocker["evidence_sha256"]))

    def test_missing_cookie_blocks_without_opening_a_breaker(self) -> None:
        from video2pdf_workflow_kernel.adapters import PlatformAdapterError
        from video2pdf_workflow_kernel.source_acquisition import record_source_blocker

        kernel = _AdmissionRecordingKernel()
        error = PlatformAdapterError(
            "localized cookie file is absent",
            classification="source_authentication_required",
            exit_code=30,
            blocker_kind="user_input",
            data={"authentication_classification": "cookie_missing"},
        )

        blocker = record_source_blocker(kernel, "youtube", error)

        self.assertEqual(blocker["breaker_state"], "not_open")
        self.assertEqual(kernel.breakers, [])

    def test_cookie_rejection_persists_run_blocker_and_scoped_breaker(self) -> None:
        from video2pdf_workflow_kernel.adapters import (
            PlatformAdapterError,
            PlatformProbeRequest,
            RecordedCommandRunner,
            YouTubePlatformAdapter,
            YtDlpRuntime,
        )
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_acquisition import (
            persist_source_blocker,
        )
        from video2pdf_workflow_kernel.utils import read_json, sha256_file

        root = PROJECT_ROOT / "待删除" / "k" / uuid.uuid4().hex[:8]
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
        kernel = VideoWorkflowKernel(workspace)
        probe = kernel.bootstrap_production_source(
            adapter=adapter,
            request=PlatformProbeRequest(
                source_url="https://www.youtube.com/watch?v=yt-test-001",
                localized_cookie_file=cookie,
                staging_root=staging,
            ),
            runner=RecordedCommandRunner(
                PROJECT_ROOT
                / "tests/video_workflow/fixtures/providers/youtube/fresh-download"
            ),
            task_start="2026-07-18T12:00:00+08:00",
            request_id="production-cookie-blocker",
            provider_kind="recorded_fixture",
        )
        initialized = kernel.initialize_production_source(probe)
        error = PlatformAdapterError(
            "platform cookie was rejected",
            classification="source_authentication_required",
            exit_code=30,
            blocker_kind="user_input",
            data={"authentication_classification": "cookie_rejected"},
        )

        blocker = persist_source_blocker(
            kernel,
            initialized.run_dir,
            "youtube",
            error,
        )

        run_path = initialized.run_dir / "workflow/run.json"
        run = read_json(run_path)
        self.assertEqual(run["source_state"], "blocked_user_input")
        self.assertEqual(run["source_blocker"], blocker)
        self.assertIsNone(run["source_version"])
        self.assertEqual(run["phase"], "source_acquisition")
        self.assertEqual(run["coordination_revision"], 2)
        self.assertRegex(run["last_mutation_intent_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            kernel.control_store.current_run_record_sha(run["run_id"]),
            sha256_file(run_path),
        )
        restarted = VideoWorkflowKernel(workspace)
        self.assertEqual(
            {
                (item["resource_class"], item["scope_kind"], item["state"])
                for item in restarted.resource_circuit_breaker_status()
            },
            {("youtube_download", "platform", "open")},
        )

    def test_source_reopen_transitively_stales_dependents_and_recovers_fault_boundaries(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.source_acquisition import (
            SOURCE_REOPEN_FAULT_POINTS,
            SourceReopenSaga,
        )
        from video2pdf_workflow_kernel.utils import read_json, sha256_file

        for fault_point in sorted(SOURCE_REOPEN_FAULT_POINTS):
            with self.subTest(fault_point=fault_point):
                run_dir = self._ready_source_run(f"source-reopen-{fault_point}")
                saga = SourceReopenSaga(run_dir)
                with self.assertRaisesRegex(Exception, fault_point):
                    saga.reopen(
                        reason="source correction",
                        validated_record=read_json(run_dir / "workflow/run.json"),
                        fault_point=fault_point,
                    )

                result = SourceReopenSaga(run_dir).reconcile()
                reopened = read_json(run_dir / "workflow/run.json")
                journal = read_json(result.journal_path)

                self.assertEqual(journal["state"], "COMMITTED")
                self.assertEqual(reopened["source_epoch"], 2)
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
                        reopened["checkpoints"][checkpoint]["status"], "stale"
                    )
                preservation = next(
                    item
                    for item in journal["preservations"]
                    if item["logical_id"] == "source_package"
                )
                preserved = run_dir.joinpath(
                    *Path(preservation["preservation_path"]).parts
                )
                self.assertTrue((preserved / "manifest.json").is_file())
                self.assertTrue((preserved / "media/video.mp4").is_file())
                self.assertTrue((run_dir / "source").is_dir())
                self.assertFalse((run_dir / "source/manifest.json").exists())

    def test_production_initialization_commits_pending_run_without_a_manifest(
        self,
    ) -> None:
        from video2pdf_workflow_kernel.adapters import (
            PlatformProbeRequest,
            RecordedCommandRunner,
            YouTubePlatformAdapter,
            YtDlpRuntime,
        )
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.utils import read_json, sha256_file

        root = (
            PROJECT_ROOT
            / "待删除"
            / "k"
            / uuid.uuid4().hex[:8]
        )
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
        kernel = VideoWorkflowKernel(workspace)

        probe = kernel.bootstrap_production_source(
            adapter=adapter,
            request=PlatformProbeRequest(
                source_url="https://www.youtube.com/watch?v=yt-test-001",
                localized_cookie_file=cookie,
                staging_root=staging,
            ),
            runner=runner,
            task_start="2026-07-18T12:00:00+08:00",
            request_id="production-init",
            provider_kind="recorded_fixture",
        )
        initialized = kernel.initialize_production_source(probe)

        run = read_json(initialized.run_dir / "workflow/run.json")
        self.assertEqual(initialized.classification, "source_acquisition_pending")
        self.assertEqual(run["schema_version"], "3.0.0")
        self.assertEqual(run["canonical_platform"], "youtube")
        self.assertEqual(run["source_state"], "pending")
        self.assertIsNone(run["source_version"])
        self.assertEqual(run["phase"], "source_acquisition")
        self.assertEqual(
            run["checkpoints"]["run_initialized"]["status"], "current"
        )
        self.assertNotIn("source_manifest", run["artifact_generations"])
        self.assertFalse((initialized.run_dir / "source/manifest.json").exists())
        self.assertEqual(
            kernel.control_store.current_run_record_sha(run["run_id"]),
            sha256_file(initialized.run_dir / "workflow/run.json"),
        )

    def test_production_prompt_selects_versioned_role_and_platform_overlay(self) -> None:
        from video2pdf_workflow_kernel.prompts import (
            generate_source_acquisition_prompt,
        )

        prompt, provenance = generate_source_acquisition_prompt(
            PROJECT_ROOT,
            role_version="2.0.0",
            platform="youtube",
            platform_version="1.0.0",
        )

        self.assertEqual(
            provenance["role_template"]["identity"], "source-acquisition"
        )
        self.assertEqual(provenance["role_template"]["version"], "2.0.0")
        self.assertEqual(provenance["platform_overlay"]["identity"], "youtube")
        self.assertIn(b"Whisper", prompt)
        self.assertIn(b"English", prompt)


if __name__ == "__main__":
    unittest.main()

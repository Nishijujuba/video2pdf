from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

FIXTURES = PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "providers"
TEST_RUNS = PROJECT_ROOT / "待删除" / "kernel-test-runs" / "platform-adapters"


from video2pdf_workflow_kernel.adapters import (
    BilibiliPlatformAdapter,
    PlatformAcquireRequest,
    PlatformAdapter,
    PlatformAdapterError,
    PlatformProbeRequest,
    RecordedCommandRunner,
    SubprocessCommandRunner,
    YtDlpRuntime,
    YouTubePlatformAdapter,
)
from video2pdf_workflow_kernel.adapters.base import (
    CommandEvidence,
    CommandResult,
    CommandSpec,
    SecretArgument,
)


class StopAfterCommandCapture(RuntimeError):
    pass


class MetadataProbeRunner:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.metadata = metadata
        self.commands: list[CommandSpec] = []

    def run(self, command: CommandSpec) -> CommandResult:
        self.commands.append(command)
        evidence = CommandEvidence(
            operation=command.operation,
            argv=command.evidence_argv(),
            argv_sha256="0" * 64,
            returncode=0,
            stdout_sha256="0" * 64,
            stderr_sha256="0" * 64,
        )
        if command.operation == "subtitle_list":
            stdout = b""
        elif command.operation == "metadata_probe":
            stdout = json.dumps(self.metadata).encode("utf-8")
        else:
            raise AssertionError(f"unexpected command: {command.operation}")
        return CommandResult(
            returncode=0,
            stdout=stdout,
            stderr=b"",
            evidence=evidence,
        )


class BilibiliP2ProbeThenCaptureRunner:
    def __init__(self) -> None:
        self.commands: list[CommandSpec] = []

    def run(self, command: CommandSpec) -> CommandResult:
        self.commands.append(command)
        evidence = CommandEvidence(
            operation=command.operation,
            argv=command.evidence_argv(),
            argv_sha256="0" * 64,
            returncode=0,
            stdout_sha256="0" * 64,
            stderr_sha256="0" * 64,
        )
        if command.operation == "subtitle_list":
            stdout = b""
        elif command.operation == "metadata_probe":
            stdout = json.dumps(
                {
                    "id": "BV1TEST00001_p2",
                    "bvid": "BV1TEST00001",
                    "page": 2,
                    "title": "Bilibili part two fixture",
                    "duration": 12.5,
                    "webpage_url": "https://www.bilibili.com/video/BV1TEST00001/",
                    "subtitles": {},
                    "automatic_captions": {},
                    "formats": [],
                }
            ).encode("utf-8")
        else:
            raise StopAfterCommandCapture(command.operation)
        return CommandResult(
            returncode=0,
            stdout=stdout,
            stderr=b"",
            evidence=evidence,
        )


def new_test_root(label: str) -> Path:
    root = TEST_RUNS / f"{label}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def localized_cookie(root: Path, value: str = "adapter-cookie-secret") -> Path:
    path = root / "credentials" / "cookies.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\t{value}\n",
        encoding="utf-8",
    )
    return path


def runtime() -> YtDlpRuntime:
    return YtDlpRuntime(
        python_executable=Path("python"),
        ffmpeg_dir=Path("ffmpeg-bin"),
        ffprobe_executable=Path("ffprobe"),
    )


class PlatformAdapterTests(unittest.TestCase):
    def _copy_recording(self, platform: str, label: str) -> Path:
        target = new_test_root(label) / "recording"
        shutil.copytree(FIXTURES / platform / "fresh-download", target)
        return target

    def test_recorded_provider_manifest_is_closed_and_versioned(self) -> None:
        for mutation in ("missing", "extra"):
            with self.subTest(mutation=mutation):
                recording = self._copy_recording(
                    "bilibili", f"recording-contract-{mutation}"
                )
                manifest_path = recording / "recording.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if mutation == "missing":
                    manifest.pop("adapter")
                else:
                    manifest["unregistered_field"] = True
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
                )

                with self.assertRaisesRegex(
                    PlatformAdapterError, "manifest is contract-invalid"
                ):
                    RecordedCommandRunner(recording)

    def test_recorded_provider_rejects_platform_adapter_contract_drift(self) -> None:
        recording = self._copy_recording("bilibili", "recording-wrong-platform")
        manifest_path = recording / "recording.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["canonical_platform"] = "youtube"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )

        with self.assertRaisesRegex(
            PlatformAdapterError, "manifest is contract-invalid"
        ):
            RecordedCommandRunner(recording)

    def test_recorded_provider_rejects_declared_stdio_hash_drift(self) -> None:
        recording = self._copy_recording("youtube", "recording-stdio-drift")
        manifest_path = recording / "recording.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["commands"][0]["stdout"]["sha256"] = "0" * 64
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )

        with self.assertRaisesRegex(PlatformAdapterError, "stdout fixture drifted"):
            RecordedCommandRunner(recording)

    def test_recorded_provider_binding_is_immutable_and_manifest_bound(self) -> None:
        recording = FIXTURES / "youtube" / "fresh-download"
        runner = RecordedCommandRunner(recording)

        self.assertEqual(runner.recording_evidence.canonical_platform, "youtube")
        self.assertEqual(
            runner.recording_evidence.manifest_sha256,
            hashlib.sha256((recording / "recording.json").read_bytes()).hexdigest(),
        )
        with self.assertRaises(FrozenInstanceError):
            runner.recording_evidence.canonical_platform = "bilibili"
        with self.assertRaises(AttributeError):
            runner.recording_evidence = runner.recording_evidence

    def test_adapter_rejects_a_valid_recording_for_the_wrong_platform(self) -> None:
        root = new_test_root("recording-wrong-adapter")
        staging = root / "attempt"
        staging.mkdir(parents=True)
        runner = RecordedCommandRunner(FIXTURES / "youtube" / "fresh-download")

        with self.assertRaisesRegex(
            PlatformAdapterError, "recorded provider platform differs"
        ):
            BilibiliPlatformAdapter(runtime()).probe(
                PlatformProbeRequest(
                    source_url="https://www.bilibili.com/video/BV1TEST00001/?p=1",
                    localized_cookie_file=localized_cookie(root),
                    staging_root=staging,
                    explicit_item_selector="p1",
                ),
                runner=runner,
            )

        self.assertEqual(runner.evidence, [])

    def _assert_probe_metadata_rejected(
        self,
        *,
        platform: str,
        source_url: str,
        metadata: dict[str, object],
        explicit_item_selector: str | None = None,
    ) -> None:
        root = new_test_root(f"{platform}-invalid-metadata")
        staging = root / "attempt"
        staging.mkdir(parents=True)
        adapter = (
            BilibiliPlatformAdapter(runtime())
            if platform == "bilibili"
            else YouTubePlatformAdapter(runtime())
        )
        runner = MetadataProbeRunner(metadata)

        with self.assertRaises(PlatformAdapterError) as raised:
            adapter.probe(
                PlatformProbeRequest(
                    source_url=source_url,
                    localized_cookie_file=localized_cookie(root),
                    staging_root=staging,
                    explicit_item_selector=explicit_item_selector,
                ),
                runner=runner,
            )

        self.assertEqual(
            raised.exception.classification,
            "source_provider_output_invalid",
        )
        self.assertEqual(
            [command.operation for command in runner.commands],
            ["subtitle_list", "metadata_probe"],
        )

    def test_bilibili_probe_rejects_a_noncanonical_bv_identity(self) -> None:
        self._assert_probe_metadata_rejected(
            platform="bilibili",
            source_url="https://www.bilibili.com/video/BV1TEST00001/?p=1",
            explicit_item_selector="p1",
            metadata={
                "id": "BV123_p1",
                "bvid": "BV123",
                "page": 1,
                "title": "Malformed BV fixture",
                "duration": 12.5,
                "webpage_url": "https://www.bilibili.com/video/BV123/?p=1",
            },
        )

    def test_bilibili_probe_rejects_wrong_platform_metadata(self) -> None:
        self._assert_probe_metadata_rejected(
            platform="bilibili",
            source_url="https://www.bilibili.com/video/BV1TEST00001/?p=1",
            explicit_item_selector="p1",
            metadata={
                "id": "BV1TEST00001_p1",
                "bvid": "BV1TEST00001",
                "page": 1,
                "title": "Wrong platform fixture",
                "duration": 12.5,
                "webpage_url": "https://www.youtube.com/watch?v=yt-test-001",
            },
        )

    def test_bilibili_probe_rejects_metadata_for_a_different_part(self) -> None:
        self._assert_probe_metadata_rejected(
            platform="bilibili",
            source_url="https://www.bilibili.com/video/BV1TEST00001/?p=2",
            explicit_item_selector="p2",
            metadata={
                "id": "BV1TEST00001_p1",
                "bvid": "BV1TEST00001",
                "page": 1,
                "title": "Wrong part fixture",
                "duration": 12.5,
                "webpage_url": "https://www.bilibili.com/video/BV1TEST00001/?p=1",
            },
        )

    def test_youtube_probe_rejects_a_noncanonical_item_identity(self) -> None:
        self._assert_probe_metadata_rejected(
            platform="youtube",
            source_url="https://www.youtube.com/watch?v=yt-test-001",
            metadata={
                "id": "too-short",
                "title": "Malformed YouTube fixture",
                "duration": 12.5,
                "webpage_url": "https://www.youtube.com/watch?v=too-short",
            },
        )

    def test_youtube_probe_rejects_wrong_platform_metadata(self) -> None:
        self._assert_probe_metadata_rejected(
            platform="youtube",
            source_url="https://www.youtube.com/watch?v=yt-test-001",
            metadata={
                "id": "yt-test-001",
                "title": "Wrong platform fixture",
                "duration": 12.5,
                "webpage_url": "https://www.bilibili.com/video/BV1TEST00001/",
            },
        )

    def test_bilibili_p2_probe_selection_drives_the_acquisition_command(self) -> None:
        root = new_test_root("bilibili-p2-binding")
        staging = root / "attempt"
        staging.mkdir(parents=True)
        cookie = localized_cookie(root)
        runner = BilibiliP2ProbeThenCaptureRunner()
        adapter = BilibiliPlatformAdapter(runtime())

        probe = adapter.probe(
            PlatformProbeRequest(
                source_url="https://www.bilibili.com/video/BV1TEST00001/",
                localized_cookie_file=cookie,
                staging_root=staging,
                explicit_item_selector="p2",
            ),
            runner=runner,
        )

        self.assertEqual(probe.canonical_item_id, "BV1TEST00001:p2")
        self.assertEqual(
            probe.canonical_url,
            "https://www.bilibili.com/video/BV1TEST00001/?p=2",
        )
        with self.assertRaises(StopAfterCommandCapture):
            adapter.acquire(
                PlatformAcquireRequest(
                    source_url="https://www.bilibili.com/video/BV1TEST00001/",
                    localized_cookie_file=cookie,
                    staging_root=staging,
                    probe=probe,
                    eligible_track_ids=(),
                ),
                runner=runner,
            )
        acquisition_command = runner.commands[-1]
        self.assertEqual(acquisition_command.operation, "thumbnail_download")
        self.assertEqual(acquisition_command.execution_argv()[-1], probe.canonical_url)
        for command in runner.commands:
            with self.subTest(operation=command.operation):
                self.assertEqual(
                    command.execution_argv()[-1],
                    "https://www.bilibili.com/video/BV1TEST00001/?p=2",
                )

    def _recorded_probe(self, platform: str):
        root = new_test_root(f"{platform}-probe")
        staging = root / "attempt"
        staging.mkdir(parents=True)
        cookie = localized_cookie(root)
        runner = RecordedCommandRunner(FIXTURES / platform / "fresh-download")
        adapter = (
            BilibiliPlatformAdapter(runtime())
            if platform == "bilibili"
            else YouTubePlatformAdapter(runtime())
        )
        source_url = (
            "https://www.bilibili.com/video/BV1TEST00001/?p=1"
            if platform == "bilibili"
            else "https://www.youtube.com/watch?v=yt-test-001"
        )
        probe = adapter.probe(
            PlatformProbeRequest(
                source_url=source_url,
                localized_cookie_file=cookie,
                staging_root=staging,
                explicit_item_selector="p1" if platform == "bilibili" else None,
            ),
            runner=runner,
        )
        return staging, cookie, runner, adapter, probe

    def test_bilibili_acquire_rejects_a_different_item_before_provider_launch(self) -> None:
        staging, cookie, runner, adapter, probe = self._recorded_probe("bilibili")
        evidence_count = len(runner.evidence)

        with self.assertRaises(PlatformAdapterError) as raised:
            adapter.acquire(
                PlatformAcquireRequest(
                    source_url="https://www.bilibili.com/video/BV1OTHER0001/?p=1",
                    localized_cookie_file=cookie,
                    staging_root=staging,
                    probe=probe,
                    eligible_track_ids=tuple(
                        track.track_id for track in probe.subtitle_tracks
                    ),
                ),
                runner=runner,
            )

        self.assertEqual(raised.exception.classification, "contract_invalid")
        self.assertEqual(len(runner.evidence), evidence_count)

    def test_bilibili_acquire_rejects_a_conflicting_part_before_provider_launch(self) -> None:
        staging, cookie, runner, adapter, probe = self._recorded_probe("bilibili")
        evidence_count = len(runner.evidence)

        with self.assertRaises(PlatformAdapterError) as raised:
            adapter.acquire(
                PlatformAcquireRequest(
                    source_url="https://www.bilibili.com/video/BV1TEST00001/?p=2",
                    localized_cookie_file=cookie,
                    staging_root=staging,
                    probe=probe,
                    eligible_track_ids=tuple(
                        track.track_id for track in probe.subtitle_tracks
                    ),
                ),
                runner=runner,
            )

        self.assertEqual(raised.exception.classification, "contract_invalid")
        self.assertEqual(len(runner.evidence), evidence_count)

    def test_youtube_acquire_rejects_a_different_item_before_provider_launch(self) -> None:
        staging, cookie, runner, adapter, probe = self._recorded_probe("youtube")
        evidence_count = len(runner.evidence)

        with self.assertRaises(PlatformAdapterError) as raised:
            adapter.acquire(
                PlatformAcquireRequest(
                    source_url="https://youtu.be/yt-test-002",
                    localized_cookie_file=cookie,
                    staging_root=staging,
                    probe=probe,
                    eligible_track_ids=tuple(
                        track.track_id for track in probe.subtitle_tracks
                    ),
                ),
                runner=runner,
            )

        self.assertEqual(raised.exception.classification, "contract_invalid")
        self.assertEqual(len(runner.evidence), evidence_count)

    def test_bilibili_acquire_accepts_an_equivalent_url_and_uses_probe_canonical_url(self) -> None:
        staging, cookie, runner, adapter, probe = self._recorded_probe("bilibili")

        adapter.acquire(
            PlatformAcquireRequest(
                source_url=(
                    "https://bilibili.com/video/BV1TEST00001"
                    "?spm_id_from=request-only-tracking"
                ),
                localized_cookie_file=cookie,
                staging_root=staging,
                probe=probe,
                eligible_track_ids=tuple(
                    track.track_id for track in probe.subtitle_tracks
                ),
            ),
            runner=runner,
        )

        runner.assert_consumed()
        for command in runner.evidence[2:-1]:
            with self.subTest(operation=command.operation):
                self.assertEqual(command.argv[-1], probe.canonical_url)

    def test_youtube_acquire_accepts_an_equivalent_url_and_uses_probe_canonical_url(self) -> None:
        staging, cookie, runner, adapter, probe = self._recorded_probe("youtube")

        adapter.acquire(
            PlatformAcquireRequest(
                source_url="https://youtu.be/yt-test-001?t=9",
                localized_cookie_file=cookie,
                staging_root=staging,
                probe=probe,
                eligible_track_ids=tuple(
                    track.track_id for track in probe.subtitle_tracks
                ),
            ),
            runner=runner,
        )

        runner.assert_consumed()
        for command in runner.evidence[2:-1]:
            with self.subTest(operation=command.operation):
                self.assertEqual(command.argv[-1], probe.canonical_url)

    def test_youtube_translated_subtitle_filename_binds_requested_track(self) -> None:
        from video2pdf_workflow_kernel.adapters.yt_dlp import (
            _subtitle_output_candidates,
        )

        root = new_test_root("youtube-translated-subtitle")
        (root / "candidate.en-US.en.srt").write_text("english", encoding="utf-8")
        (root / "candidate.en-US.en-orig.srt").write_text(
            "english original", encoding="utf-8"
        )
        (root / "candidate.en-US.zh-Hans.srt").write_text(
            "chinese", encoding="utf-8"
        )

        self.assertEqual(
            _subtitle_output_candidates(root, "en"),
            (root / "candidate.en-US.en.srt",),
        )
        self.assertEqual(
            _subtitle_output_candidates(root, "en-orig"),
            (root / "candidate.en-US.en-orig.srt",),
        )
        self.assertEqual(
            _subtitle_output_candidates(root, "zh-Hans"),
            (root / "candidate.en-US.zh-Hans.srt",),
        )

    def _run_recorded_acquisition(self, platform: str):
        root = new_test_root(platform)
        staging = root / "attempt"
        staging.mkdir(parents=True)
        cookie = localized_cookie(root)
        runner = RecordedCommandRunner(FIXTURES / platform / "fresh-download")
        adapter = (
            BilibiliPlatformAdapter(runtime())
            if platform == "bilibili"
            else YouTubePlatformAdapter(runtime())
        )
        source_url = (
            "https://www.bilibili.com/video/BV1TEST00001/?p=1"
            if platform == "bilibili"
            else "https://www.youtube.com/watch?v=yt-test-001"
        )
        probe = adapter.probe(
            PlatformProbeRequest(
                source_url=source_url,
                localized_cookie_file=cookie,
                staging_root=staging,
                explicit_item_selector="p1" if platform == "bilibili" else None,
            ),
            runner=runner,
        )
        acquisition = adapter.acquire(
            PlatformAcquireRequest(
                source_url=source_url,
                localized_cookie_file=cookie,
                staging_root=staging,
                probe=probe,
                eligible_track_ids=tuple(track.track_id for track in probe.subtitle_tracks),
            ),
            runner=runner,
        )
        runner.assert_consumed()
        return root, runner, probe, acquisition

    def test_production_adapters_share_one_runtime_checkable_interface(self) -> None:
        self.assertIsInstance(BilibiliPlatformAdapter(runtime()), PlatformAdapter)
        self.assertIsInstance(YouTubePlatformAdapter(runtime()), PlatformAdapter)

    def test_bilibili_recording_is_cookie_first_and_materializes_canonical_outputs(self) -> None:
        root, runner, probe, acquisition = self._run_recorded_acquisition("bilibili")

        self.assertEqual(probe.canonical_platform, "bilibili")
        self.assertEqual(probe.canonical_item_id, "BV1TEST00001:p1")
        self.assertEqual(
            probe.canonical_url,
            "https://www.bilibili.com/video/BV1TEST00001/?p=1",
        )
        self.assertEqual(probe.authentication_classification, "cookie_accepted")
        self.assertEqual(runner.evidence[0].operation, "subtitle_list")
        self.assertIn("--list-subs", runner.evidence[0].argv)
        self.assertIn("<localized-cookie-file>", runner.evidence[0].argv)
        self.assertNotIn(str(root / "credentials" / "cookies.txt"), runner.evidence[0].argv)

        metadata = json.loads(probe.normalized_metadata_path.read_text(encoding="utf-8"))
        serialized = json.dumps(metadata, ensure_ascii=False)
        self.assertNotIn("adapter-cookie-secret", serialized)
        self.assertNotIn("http_headers", metadata)
        self.assertNotIn("url", metadata["formats"][0])

        paths = {item.path.name for item in acquisition.subtitle_candidates}
        self.assertEqual(paths, {"subtitle.en.manual.srt", "subtitle.ai-zh.automatic.srt"})
        self.assertEqual(acquisition.cover.path.as_posix().split("/")[-2:], ["cover", "cover.jpg"])
        self.assertEqual(acquisition.video.path.name, "video.mp4")
        self.assertEqual(acquisition.media_probe.path.name, "media-probe.json")

    def test_youtube_recording_puts_node_on_every_ytdlp_command(self) -> None:
        _, runner, probe, acquisition = self._run_recorded_acquisition("youtube")

        self.assertEqual(probe.canonical_platform, "youtube")
        self.assertEqual(probe.canonical_item_id, "yt-test-001")
        self.assertEqual(
            probe.canonical_url,
            "https://www.youtube.com/watch?v=yt-test-001",
        )
        yt_dlp_commands = [
            item for item in runner.evidence if "yt_dlp" in item.argv
        ]
        self.assertGreaterEqual(len(yt_dlp_commands), 5)
        for command in yt_dlp_commands:
            with self.subTest(operation=command.operation):
                index = command.argv.index("--js-runtimes")
                self.assertEqual(command.argv[index + 1], "node")
        self.assertEqual(acquisition.video.path.name, "video.mp4")

    def test_cookie_rejection_is_a_closed_user_input_classification_without_secret_leak(self) -> None:
        root = new_test_root("cookie-rejected")
        staging = root / "attempt"
        staging.mkdir(parents=True)
        cookie = localized_cookie(root, "do-not-leak-this-cookie")
        adapter = BilibiliPlatformAdapter(runtime())
        runner = RecordedCommandRunner(FIXTURES / "bilibili" / "cookie-rejected")

        with self.assertRaises(PlatformAdapterError) as raised:
            adapter.probe(
                PlatformProbeRequest(
                    source_url="https://www.bilibili.com/video/BV1TEST00001/",
                    localized_cookie_file=cookie,
                    staging_root=staging,
                ),
                runner=runner,
            )

        error = raised.exception
        self.assertEqual(error.classification, "source_authentication_required")
        self.assertEqual(error.exit_code, 30)
        self.assertEqual(error.blocker_kind, "user_input")
        self.assertEqual(error.data["authentication_classification"], "cookie_rejected")
        serialized = f"{error}\n{json.dumps(error.data, ensure_ascii=False)}"
        self.assertNotIn("do-not-leak-this-cookie", serialized)
        self.assertNotIn(str(cookie), serialized)

    def test_recorded_runner_rejects_an_unconsumed_or_out_of_order_recording(self) -> None:
        runner = RecordedCommandRunner(FIXTURES / "youtube" / "fresh-download")
        with self.assertRaisesRegex(PlatformAdapterError, "unconsumed"):
            runner.assert_consumed()

        root = new_test_root("wrong-order")
        spec = CommandSpec(
            operation="metadata_probe",
            argv=("python", "-m", "yt_dlp"),
            cwd=root,
            allowed_output_root=root,
            timeout_seconds=30,
        )
        with self.assertRaisesRegex(PlatformAdapterError, "out of order"):
            runner.run(spec)

    def test_subprocess_runner_redacts_secret_arguments_cookie_lines_and_values(self) -> None:
        root = new_test_root("redaction")
        cookie = localized_cookie(root, "super-secret-session-value")
        script = (
            "import sys;"
            "print(sys.argv[1]);"
            "print('Cookie: SESSDATA=super-secret-session-value');"
            "print('https://example.test/?token=super-secret-session-value')"
        )
        spec = CommandSpec(
            operation="redaction_probe",
            argv=(
                sys.executable,
                "-X",
                "utf8",
                "-c",
                script,
                SecretArgument(str(cookie)),
            ),
            cwd=root,
            allowed_output_root=root,
            timeout_seconds=30,
        )

        result = SubprocessCommandRunner().run(spec)

        self.assertEqual(result.returncode, 0)
        combined = (result.stdout + result.stderr).decode("utf-8")
        self.assertNotIn(str(cookie), combined)
        self.assertNotIn("super-secret-session-value", combined)
        self.assertNotIn(
            "super-secret-session-value", "\n".join(result.evidence.argv)
        )
        self.assertIn("<localized-cookie-file>", result.evidence.argv)
        self.assertIn("<redacted>", combined)


if __name__ == "__main__":
    unittest.main()

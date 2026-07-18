from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

FIXTURES = PROJECT_ROOT / "tests" / "video_workflow" / "fixtures" / "providers"
TEST_RUNS = PROJECT_ROOT / "待删除" / "kernel-test-runs" / "source-candidates"


from video2pdf_workflow_kernel.adapters import (
    BilibiliPlatformAdapter,
    PlatformAcquireRequest,
    PlatformProbeRequest,
    RecordedCommandRunner,
    YtDlpRuntime,
)
from video2pdf_workflow_kernel.adapters.base import CommandEvidence
from video2pdf_workflow_kernel.contracts import ContractRegistry
from video2pdf_workflow_kernel.errors import ArtifactDrift, ContractError
from video2pdf_workflow_kernel.source_acquisition import derive_source_identity
from video2pdf_workflow_kernel.source_candidates import (
    SourceCandidatePolicy,
    SourceProviderBinding,
    ToolVersion,
    materialize_source_candidates,
)
from video2pdf_workflow_kernel.utils import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)


RUN_ID = "1" * 32
ACQUISITION_ID = "2" * 32
TASK_ID = "3" * 32
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


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


def new_test_root(label: str) -> Path:
    root = TEST_RUNS / f"{label}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def localized_cookie(root: Path) -> Path:
    path = root / "credentials" / "cookies.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".example.test\tTRUE\t/\tTRUE\t2147483647\tSESSDATA\t"
        "source-candidate-cookie-secret\n",
        encoding="utf-8",
    )
    return path


def runtime() -> YtDlpRuntime:
    return YtDlpRuntime(
        python_executable=Path("python"),
        ffmpeg_dir=Path("ffmpeg-bin"),
        ffprobe_executable=Path("ffprobe"),
    )


def recorded_bilibili_acquisition(root: Path):
    staging = root / "attempt"
    staging.mkdir(parents=True)
    cookie = localized_cookie(root)
    runner = RecordedCommandRunner(FIXTURES / "bilibili" / "fresh-download")
    adapter = BilibiliPlatformAdapter(runtime())
    source_url = "https://www.bilibili.com/video/BV1TEST00001/?p=1"
    probe = adapter.probe(
        PlatformProbeRequest(
            source_url=source_url,
            localized_cookie_file=cookie,
            staging_root=staging,
            explicit_item_selector="p1",
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
    return cookie, probe, acquisition


def recorded_provider(
    recording_root: Path = FIXTURES / "bilibili" / "fresh-download",
    *,
    claimed_sha256: str | None = None,
) -> SourceProviderBinding:
    recording = RecordedCommandRunner(recording_root).recording_evidence
    return SourceProviderBinding(
        kind="recorded_fixture",
        recording_sha256=claimed_sha256 or recording.manifest_sha256,
        tool_versions=(
            ToolVersion(name="yt-dlp", version="2026.07.18"),
            ToolVersion(name="ffprobe", version="7.1"),
        ),
        recording_platform=recording.canonical_platform,
        recording_adapter_id=recording.adapter_id,
        recording_adapter_contract_version=recording.adapter_contract_version,
        recording_evidence=recording,
    )


def language_learning_policy() -> SourceCandidatePolicy:
    return SourceCandidatePolicy(
        content_classification="language_learning",
        subtitle_language_priority=("en", "zh-Hans"),
        whisper_allowed=True,
    )


def valid_import_binding(source_identity: str) -> dict:
    evidence = {"status": "pass", "evidence_sha256": HASH_C}
    return {
        "prior_run_id": "4" * 32,
        "prior_source_manifest_sha256": HASH_A,
        "prior_source_identity": source_identity,
        "prior_source_version": HASH_B,
        "validation": {
            "canonical_identity": dict(evidence),
            "schema_compatibility": dict(evidence),
            "artifact_fingerprints": dict(evidence),
            "subtitle_policy": dict(evidence),
            "technical_properties": dict(evidence),
            "source_quality": dict(evidence),
            "original_only": dict(evidence),
        },
    }


def verified_import_command() -> CommandEvidence:
    argv = (
        "verified-import-validator",
        "--validate-package",
        "<prior-source-package>",
    )
    return CommandEvidence(
        operation="verified_import_validation",
        argv=argv,
        argv_sha256=hashlib.sha256("\0".join(argv).encode()).hexdigest(),
        returncode=0,
        stdout_sha256=HASH_A,
        stderr_sha256=HASH_B,
    )


class SourceCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contracts = ContractRegistry(PROJECT_ROOT)

    def test_recorded_provider_rejects_a_claimed_manifest_hash_mismatch(self) -> None:
        with self.assertRaisesRegex(
            ContractError, "declared recording SHA-256 differs"
        ):
            recorded_provider(claimed_sha256=HASH_A)

    def test_materializer_rejects_fixture_x_claim_with_fixture_y_replay(self) -> None:
        root = new_test_root("recording-cross-fixture")
        _, probe, acquisition = recorded_bilibili_acquisition(root)
        alternate = root / "alternate-recording"
        shutil.copytree(FIXTURES / "bilibili" / "fresh-download", alternate)
        manifest_path = alternate / "recording.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["recording_id"] = "bilibili-alternate-recording-v1"
        manifest_path.write_bytes(canonical_json_bytes(manifest))

        with self.assertRaisesRegex(
            ContractError, "Recorded command evidence differs from provider binding"
        ):
            materialize_source_candidates(
                root / "run",
                run_id=RUN_ID,
                source_epoch=1,
                acquisition_id=ACQUISITION_ID,
                mode="fresh_download",
                probe=probe,
                acquisition=acquisition,
                provider=recorded_provider(alternate),
                policy=language_learning_policy(),
                task_id=TASK_ID,
                contracts=self.contracts,
            )

    def test_materializer_rejects_an_unconsumed_recorded_sequence(self) -> None:
        root = new_test_root("recording-unconsumed-sequence")
        _, probe, acquisition = recorded_bilibili_acquisition(root)
        incomplete = replace(
            acquisition,
            command_evidence=acquisition.command_evidence[:-1],
        )

        with self.assertRaisesRegex(
            ContractError, "Recorded command sequence is incomplete"
        ):
            materialize_source_candidates(
                root / "run",
                run_id=RUN_ID,
                source_epoch=1,
                acquisition_id=ACQUISITION_ID,
                mode="fresh_download",
                probe=probe,
                acquisition=incomplete,
                provider=recorded_provider(),
                policy=language_learning_policy(),
                task_id=TASK_ID,
                contracts=self.contracts,
            )

    def test_acquisition_probe_identity_binding_remains_an_independent_gate(self) -> None:
        root = new_test_root("probe-identity-binding")
        _, probe, acquisition = recorded_bilibili_acquisition(root)
        different_probe = replace(
            probe,
            canonical_item_id="BV1OTHER0001:p1",
            canonical_url="https://www.bilibili.com/video/BV1OTHER0001/?p=1",
        )
        run_dir = root / "run"

        with self.assertRaisesRegex(
            ContractError,
            "does not bind the supplied probe",
        ):
            materialize_source_candidates(
                run_dir,
                run_id=RUN_ID,
                source_epoch=1,
                acquisition_id=ACQUISITION_ID,
                mode="fresh_download",
                probe=different_probe,
                acquisition=acquisition,
                provider=recorded_provider(),
                policy=language_learning_policy(),
                task_id=TASK_ID,
                contracts=self.contracts,
            )

        self.assertFalse(
            (
                run_dir
                / "work"
                / "source-acquisition"
                / "candidate-inventory.json"
            ).exists()
        )

    def test_fresh_inventory_and_task_bound_skeleton_are_deterministic(self) -> None:
        root = new_test_root("fresh")
        cookie, probe, acquisition = recorded_bilibili_acquisition(root)
        run_dir = root / "run"

        first = materialize_source_candidates(
            run_dir,
            run_id=RUN_ID,
            source_epoch=1,
            acquisition_id=ACQUISITION_ID,
            mode="fresh_download",
            probe=probe,
            acquisition=acquisition,
            provider=recorded_provider(),
            policy=language_learning_policy(),
            task_id=TASK_ID,
            contracts=self.contracts,
        )

        self.assertEqual(
            first.inventory_path,
            run_dir / "work" / "source-acquisition" / "candidate-inventory.json",
        )
        self.assertEqual(
            first.skeleton_path,
            run_dir / "work" / "source-acquisition" / "decision.skeleton.json",
        )
        self.assertEqual(first.inventory_sha256, sha256_file(first.inventory_path))
        self.assertEqual(first.skeleton_sha256, sha256_file(first.skeleton_path))
        self.contracts.validate("source-candidate-inventory", first.inventory)
        self.contracts.validate(
            "source-acquisition-decision-skeleton", first.skeleton
        )

        candidate_ids = {candidate["candidate_id"] for candidate in first.inventory["candidates"]}
        candidate_paths = {
            candidate["staged_path"] for candidate in first.inventory["candidates"]
        }
        self.assertEqual(len(candidate_ids), len(first.inventory["candidates"]))
        self.assertEqual(len(candidate_paths), len(first.inventory["candidates"]))
        self.assertTrue({"metadata", "cover", "video", "subtitle"}.issubset(
            {candidate["role"] for candidate in first.inventory["candidates"]}
        ))
        for candidate in first.inventory["candidates"]:
            relative = PurePosixPath(candidate["staged_path"])
            staged = run_dir.joinpath(*relative.parts)
            self.assertTrue(staged.is_file())
            self.assertFalse(staged.is_symlink())
            self.assertEqual(candidate["sha256"], sha256_file(staged))
            self.assertEqual(candidate["size_bytes"], staged.stat().st_size)
            mechanical_identity = {
                key: candidate[key]
                for key in (
                    "role",
                    "staged_path",
                    "media_type",
                    "sha256",
                    "language",
                    "subtitle_kind",
                    "technical_probe",
                )
            }
            self.assertEqual(
                candidate["candidate_id"],
                sha256_bytes(canonical_json_bytes(mechanical_identity)),
            )

        command_text = json.dumps(first.inventory["commands"], ensure_ascii=False)
        self.assertIn("<localized-cookie-file>", command_text)
        self.assertNotIn(str(cookie), command_text)
        self.assertNotIn("source-candidate-cookie-secret", command_text)

        subtitles = {
            candidate["candidate_id"]: candidate
            for candidate in first.inventory["candidates"]
            if candidate["role"] == "subtitle"
        }
        allowed_ids = first.skeleton["allowed_judgment"]["subtitle_candidate_ids"]
        self.assertEqual(len(allowed_ids), 1)
        self.assertEqual(subtitles[allowed_ids[0]]["language"], "en")
        self.assertEqual(subtitles[allowed_ids[0]]["subtitle_kind"], "manual")
        video = next(
            candidate
            for candidate in first.inventory["candidates"]
            if candidate["role"] == "video"
        )
        self.assertIn("audio", video["technical_probe"]["stream_types"])
        self.assertEqual(
            first.skeleton["allowed_judgment"]["whisper_audio_candidate_id"],
            video["candidate_id"],
        )
        self.assertEqual(first.skeleton["task_id"], TASK_ID)

        second = materialize_source_candidates(
            run_dir,
            run_id=RUN_ID,
            source_epoch=1,
            acquisition_id=ACQUISITION_ID,
            mode="fresh_download",
            probe=probe,
            acquisition=acquisition,
            provider=recorded_provider(),
            policy=language_learning_policy(),
            task_id=TASK_ID,
            contracts=self.contracts,
        )
        self.assertEqual(second.inventory_sha256, first.inventory_sha256)
        self.assertEqual(second.skeleton_sha256, first.skeleton_sha256)
        self.assertEqual(second.inventory, first.inventory)
        self.assertEqual(second.skeleton, first.skeleton)

    def test_verified_import_materializes_inventory_without_decision_skeleton(self) -> None:
        root = new_test_root("verified-import")
        _, probe, acquisition = recorded_bilibili_acquisition(root)
        import_probe = replace(probe, command_evidence=(verified_import_command(),))
        acquisition = replace(
            acquisition,
            probe=import_probe,
            command_evidence=(),
        )
        run_dir = root / "run"
        source_identity = derive_source_identity(
            import_probe.canonical_platform, import_probe.canonical_item_id
        )
        provider = SourceProviderBinding(
            kind="verified_import",
            recording_sha256=None,
            tool_versions=(ToolVersion(name="import-validator", version="1.0.0"),),
        )
        policy = SourceCandidatePolicy(
            content_classification="general",
            subtitle_language_priority=("en", "zh-Hans"),
            whisper_allowed=False,
        )

        result = materialize_source_candidates(
            run_dir,
            run_id=RUN_ID,
            source_epoch=2,
            acquisition_id=ACQUISITION_ID,
            mode="verified_import",
            probe=import_probe,
            acquisition=acquisition,
            provider=provider,
            policy=policy,
            task_id=None,
            import_binding=valid_import_binding(source_identity),
            contracts=self.contracts,
        )

        self.assertIsNone(result.skeleton_path)
        self.assertIsNone(result.skeleton_sha256)
        self.assertIsNone(result.skeleton)
        self.assertFalse(
            (run_dir / "work" / "source-acquisition" / "decision.skeleton.json").exists()
        )
        self.assertEqual(result.inventory["provider"]["kind"], "verified_import")
        self.assertEqual(result.inventory["authentication_classification"], "not_applicable")
        self.assertTrue(all(
            candidate["origin"] == "verified_import"
            for candidate in result.inventory["candidates"]
        ))
        self.assertTrue(all(
            command["purpose"] == "verified_import_validation"
            for command in result.inventory["commands"]
        ))
        self.contracts.validate("source-candidate-inventory", result.inventory)

    def test_verified_import_rejects_fresh_download_command_evidence(self) -> None:
        root = new_test_root("verified-import-fresh-evidence")
        _, probe, acquisition = recorded_bilibili_acquisition(root)
        source_identity = derive_source_identity(
            probe.canonical_platform, probe.canonical_item_id
        )

        with self.assertRaises(ContractError):
            materialize_source_candidates(
                root / "run",
                run_id=RUN_ID,
                source_epoch=2,
                acquisition_id=ACQUISITION_ID,
                mode="verified_import",
                probe=probe,
                acquisition=acquisition,
                provider=SourceProviderBinding(
                    kind="verified_import",
                    recording_sha256=None,
                    tool_versions=(
                        ToolVersion(name="import-validator", version="1.0.0"),
                    ),
                ),
                policy=SourceCandidatePolicy(
                    content_classification="general",
                    subtitle_language_priority=("en",),
                    whisper_allowed=False,
                ),
                task_id=None,
                import_binding=valid_import_binding(source_identity),
                contracts=self.contracts,
            )

        self.assertFalse(
            (root / "run" / "work" / "source-acquisition" / "candidate-inventory.json").exists()
        )

    def test_candidate_source_escape_fails_before_publication(self) -> None:
        root = new_test_root("escape")
        _, probe, acquisition = recorded_bilibili_acquisition(root)
        outside = root / "outside-cover.jpg"
        outside.write_bytes(b"outside-cover")
        escaped_cover = replace(
            acquisition.cover,
            path=outside,
            sha256=sha256_file(outside),
            size_bytes=outside.stat().st_size,
        )
        escaped = replace(acquisition, cover=escaped_cover)
        run_dir = root / "run"

        with self.assertRaises(ContractError):
            materialize_source_candidates(
                run_dir,
                run_id=RUN_ID,
                source_epoch=1,
                acquisition_id=ACQUISITION_ID,
                mode="fresh_download",
                probe=probe,
                acquisition=escaped,
                provider=recorded_provider(),
                policy=language_learning_policy(),
                task_id=TASK_ID,
                contracts=self.contracts,
            )

        self.assertFalse(
            (run_dir / "work" / "source-acquisition" / "candidate-inventory.json").exists()
        )
        self.assertFalse(
            (run_dir / "work" / "source-acquisition" / "decision.skeleton.json").exists()
        )

    def test_candidate_target_parent_junction_fails_before_any_publication(self) -> None:
        root = new_test_root("target-parent-junction")
        _, probe, acquisition = recorded_bilibili_acquisition(root)
        run_dir = root / "run"
        candidate_parent = run_dir / "work/source-acquisition/candidates"
        candidate_parent.parent.mkdir(parents=True)
        outside = TEST_RUNS / "outside" / uuid.uuid4().hex
        create_directory_link(candidate_parent, outside)

        with self.assertRaises((ContractError, ArtifactDrift)):
            materialize_source_candidates(
                run_dir,
                run_id=RUN_ID,
                source_epoch=1,
                acquisition_id=ACQUISITION_ID,
                mode="fresh_download",
                probe=probe,
                acquisition=acquisition,
                provider=recorded_provider(),
                policy=language_learning_policy(),
                task_id=TASK_ID,
                contracts=self.contracts,
            )

        self.assertEqual(tuple(outside.iterdir()), ())
        self.assertFalse(
            (run_dir / "work/source-acquisition/candidate-inventory.json").exists()
        )

    def test_language_learning_policy_rejects_non_english_first_priority(self) -> None:
        with self.assertRaises(ContractError):
            SourceCandidatePolicy(
                content_classification="language_learning",
                subtitle_language_priority=("zh-Hans", "en"),
                whisper_allowed=True,
            )

    def test_unredacted_cookie_command_fails_before_publication(self) -> None:
        root = new_test_root("secret-evidence")
        cookie, probe, acquisition = recorded_bilibili_acquisition(root)
        unsafe_argv = ("python", "-m", "yt_dlp", "--cookies", str(cookie))
        unsafe_evidence = replace(
            probe.command_evidence[0],
            argv=unsafe_argv,
            argv_sha256=hashlib.sha256("\0".join(unsafe_argv).encode()).hexdigest(),
        )
        unsafe_probe = replace(
            probe,
            command_evidence=(unsafe_evidence, *probe.command_evidence[1:]),
        )
        unsafe_acquisition = replace(acquisition, probe=unsafe_probe)
        run_dir = root / "run"

        with self.assertRaises(ContractError):
            materialize_source_candidates(
                run_dir,
                run_id=RUN_ID,
                source_epoch=1,
                acquisition_id=ACQUISITION_ID,
                mode="fresh_download",
                probe=unsafe_probe,
                acquisition=unsafe_acquisition,
                provider=recorded_provider(),
                policy=language_learning_policy(),
                task_id=TASK_ID,
                contracts=self.contracts,
            )

        self.assertFalse(
            (run_dir / "work" / "source-acquisition" / "candidate-inventory.json").exists()
        )

    def test_uppercase_cookie_flag_cannot_bypass_command_redaction(self) -> None:
        root = new_test_root("uppercase-secret-evidence")
        cookie, probe, acquisition = recorded_bilibili_acquisition(root)
        unsafe_argv = ("python", "-m", "yt_dlp", "--COOKIES", str(cookie))
        unsafe_evidence = replace(
            probe.command_evidence[0],
            argv=unsafe_argv,
            argv_sha256=hashlib.sha256("\0".join(unsafe_argv).encode()).hexdigest(),
        )
        unsafe_probe = replace(
            probe,
            command_evidence=(unsafe_evidence, *probe.command_evidence[1:]),
        )
        unsafe_acquisition = replace(acquisition, probe=unsafe_probe)
        run_dir = root / "run"

        with self.assertRaises(ContractError):
            materialize_source_candidates(
                run_dir,
                run_id=RUN_ID,
                source_epoch=1,
                acquisition_id=ACQUISITION_ID,
                mode="fresh_download",
                probe=unsafe_probe,
                acquisition=unsafe_acquisition,
                provider=recorded_provider(),
                policy=language_learning_policy(),
                task_id=TASK_ID,
                contracts=self.contracts,
            )

        self.assertFalse(
            (run_dir / "work" / "source-acquisition" / "candidate-inventory.json").exists()
        )

    def test_malformed_subtitle_cannot_receive_passing_technical_evidence(self) -> None:
        root = new_test_root("invalid-subtitle")
        _, probe, acquisition = recorded_bilibili_acquisition(root)
        subtitle = acquisition.subtitle_candidates[0]
        subtitle.path.write_text(
            "1\n00:00:03,000 --> 00:00:01,000\nreversed\n",
            encoding="utf-8",
        )
        invalid_subtitle = replace(
            subtitle,
            sha256=sha256_file(subtitle.path),
            size_bytes=subtitle.path.stat().st_size,
        )
        invalid_acquisition = replace(
            acquisition,
            subtitle_candidates=(
                invalid_subtitle,
                *acquisition.subtitle_candidates[1:],
            ),
        )
        run_dir = root / "run"

        with self.assertRaises(ContractError):
            materialize_source_candidates(
                run_dir,
                run_id=RUN_ID,
                source_epoch=1,
                acquisition_id=ACQUISITION_ID,
                mode="fresh_download",
                probe=probe,
                acquisition=invalid_acquisition,
                provider=recorded_provider(),
                policy=language_learning_policy(),
                task_id=TASK_ID,
                contracts=self.contracts,
            )

        self.assertFalse(
            (run_dir / "work" / "source-acquisition" / "candidate-inventory.json").exists()
        )


if __name__ == "__main__":
    unittest.main()

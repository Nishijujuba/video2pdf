from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

TEST_ROOT = PROJECT_ROOT / "待删除" / "kernel-test-runs" / "source-live-smoke"
HASH_A = "a" * 64
HASH_B = "b" * 64
RUN_ID = "1" * 32


def new_test_root(label: str) -> Path:
    root = TEST_ROOT / f"{label}-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def write_json(path: Path, value: dict) -> None:
    from video2pdf_workflow_kernel.utils import canonical_json_bytes

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


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


def recorded_youtube_smoke_inputs(label: str):
    from video2pdf_workflow_kernel.adapters import (
        RecordedCommandRunner,
        YtDlpRuntime,
    )
    from video2pdf_workflow_kernel.source_live_smoke import (
        CredentialBinding,
        SourceLiveSmokeCase,
    )

    root = PROJECT_ROOT / "\u5f85\u5220\u9664" / label[:4] / uuid.uuid4().hex[:6]
    root.mkdir(parents=True, exist_ok=False)
    work_root = root / "workspace"
    cookie = root / "cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        ".example.test\tTRUE\t/\tTRUE\t2147483647\tSID\trecorded\n",
        encoding="utf-8",
    )
    recording = (
        PROJECT_ROOT
        / "tests/video_workflow/fixtures/providers/youtube/fresh-download"
    )
    recorded = RecordedCommandRunner(recording)
    runtime = YtDlpRuntime(
        python_executable=Path("python"),
        ffmpeg_dir=Path("ffmpeg-bin"),
        ffprobe_executable=Path("ffprobe"),
    )
    case = SourceLiveSmokeCase(
        platform="youtube",
        source_url="https://www.youtube.com/watch?v=yt-test-001",
        original_title="YouTube Adapter Fixture",
        explicit_item_selector=None,
        content_classification="language_learning",
        subtitle_language_priority=("en", "zh-Hans"),
        whisper_allowed=True,
        max_video_height=1080,
    )
    return (
        work_root,
        recorded,
        runtime,
        case,
        CredentialBinding("youtube", cookie),
    )


def case_value(platform: str = "bilibili") -> dict:
    return {
        "schema_name": "source-live-smoke-case",
        "schema_version": "1.0.0",
        "platform": platform,
        "source_url": (
            "https://www.bilibili.com/video/BV1TEST00001/"
            if platform == "bilibili"
            else "https://www.youtube.com/watch?v=yt-test-001"
        ),
        "original_title": f"{platform.title()} live smoke fixture",
        "explicit_item_selector": "p1" if platform == "bilibili" else None,
        "content_classification": "language_learning",
        "subtitle_language_priority": ["en", "zh-Hans"],
        "whisper_allowed": True,
        "max_video_height": 144,
    }


def current_run(root: Path, platform: str = "bilibili") -> tuple[Path, Path]:
    from video2pdf_workflow_kernel.source_acquisition import derive_source_identity
    from video2pdf_workflow_kernel.utils import sha256_file

    run_dir = root / "run"
    manifest = {
        "schema_name": "source-manifest",
        "schema_version": "2.0.0",
        "kernel_version": "2.0.0",
        "run_id": RUN_ID,
        "canonical_platform": platform,
        "canonical_item_id": "BV1TEST00001:p1" if platform == "bilibili" else "yt-test-001",
        "source_identity": derive_source_identity(
            platform, "BV1TEST00001:p1" if platform == "bilibili" else "yt-test-001"
        ),
        "source_version": HASH_B,
        "package_status": "validated",
    }
    manifest_path = run_dir / "source" / "manifest.json"
    write_json(manifest_path, manifest)
    manifest_sha = sha256_file(manifest_path)
    run_record = {
        "schema_name": "run-record",
        "schema_version": "3.0.0",
        "run_id": RUN_ID,
        "canonical_platform": platform,
        "source_identity": manifest["source_identity"],
        "source_version": HASH_B,
        "source_state": "ready",
        "phase": "source_ready",
        "artifact_generations": {
            "source_manifest": {
                "path": "source/manifest.json",
                "generation": 1,
                "sha256": manifest_sha,
            }
        },
        "checkpoints": {
            "source_ready": {
                "status": "current",
                "evidence_sha256": manifest_sha,
                "artifact_bindings": [
                    {
                        "logical_id": "source_manifest",
                        "generation": 1,
                        "sha256": manifest_sha,
                    }
                ],
            }
        },
    }
    run_path = run_dir / "workflow" / "run.json"
    write_json(run_path, run_record)
    return run_path, manifest_path


class RecordingKernel:
    def __init__(self) -> None:
        self.launches: list[tuple[str, int, tuple[str, ...]]] = []

    def launch_admitted_task(
        self,
        attempt_id: str,
        claim_generation: int,
        required_resources: tuple[str, ...],
        provider,
        *,
        fault_point: str | None = None,
    ):
        self.launches.append(
            (attempt_id, claim_generation, required_resources)
        )
        return provider("launch-token")


class SourceLiveSmokeTests(unittest.TestCase):
    def test_smoke_case_rejects_an_ancestor_link_outside_its_boundary(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.source_live_smoke import load_smoke_case

        root = new_test_root("case-link")
        boundary = root / "project"
        outside = root / "outside"
        boundary.mkdir()
        write_json(outside / "case.json", case_value())
        create_directory_link(boundary / "linked", outside)

        with self.assertRaisesRegex(ContractError, "link|outside|boundary"):
            load_smoke_case(
                boundary / "linked/case.json",
                project_root=boundary,
            )

    def test_case_parser_is_closed_and_platform_bound(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.source_live_smoke import load_smoke_case

        root = new_test_root("case")
        path = root / "case.json"
        write_json(path, case_value())

        parsed = load_smoke_case(path, project_root=PROJECT_ROOT)

        self.assertEqual(parsed.platform, "bilibili")
        self.assertEqual(parsed.original_title, "Bilibili live smoke fixture")
        self.assertEqual(parsed.max_video_height, 144)
        unexpected = case_value()
        unexpected["cookie_file"] = "C:/private/cookies.txt"
        write_json(path, unexpected)
        with self.assertRaisesRegex(ContractError, "closed field set"):
            load_smoke_case(path, project_root=PROJECT_ROOT)

    def test_deterministic_locator_bootstrap_never_calls_an_adapter_or_runner(self) -> None:
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.models import DeterministicLocatorRequest
        from video2pdf_workflow_kernel.utils import read_json

        root = new_test_root("locator")
        calls: list[str] = []

        class NoCallAdapter:
            adapter_id = "youtube-yt-dlp.v1"
            canonical_platform = "youtube"
            download_resource_class = "youtube_download"

            def probe(self, request, *, runner):
                calls.append("adapter.probe")
                raise AssertionError("deterministic locator called the live Adapter")

            def acquire(self, request, *, runner):
                calls.append("adapter.acquire")
                raise AssertionError("deterministic locator acquired media")

        class NoCallRunner:
            def run(self, command):
                calls.append("runner.run")
                raise AssertionError("deterministic locator launched a command")

        kernel = VideoWorkflowKernel(root / "workspace")
        result = kernel.bootstrap_production_source(
            adapter=NoCallAdapter(),
            request=DeterministicLocatorRequest(
                source_url="https://www.youtube.com/watch?v=yt-test-001",
                original_title="YouTube live smoke fixture",
            ),
            runner=NoCallRunner(),
            task_start="2026-07-18T12:00:00+08:00",
            request_id="deterministic-locator",
            provider_kind="deterministic_locator",
        )

        self.assertEqual(calls, [])
        record = read_json(result.record_path)
        self.assertEqual(record["canonical_item_id"], "yt-test-001")
        self.assertEqual(record["availability"]["status"], "pending")
        self.assertEqual(
            record["probe_execution"]["provider_kind"],
            "deterministic_locator",
        )
        self.assertEqual(record["probe_execution"]["command_argv_redacted"], [])
        self.assertIsNone(record["probe_execution"]["resource_admission"])
        tampered = json.loads(json.dumps(record))
        tampered["probe_execution"]["normalized_result_sha256"] = HASH_B
        with self.assertRaisesRegex(Exception, "execution evidence"):
            kernel.contracts.validate("bootstrap-record", tampered)

    def test_deterministic_locator_rejects_ambiguous_platform_urls(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.models import DeterministicLocatorRequest

        class Adapter:
            adapter_id = "locator-test"
            download_resource_class = "youtube_download"

            def __init__(self, platform: str) -> None:
                self.canonical_platform = platform
                self.download_resource_class = f"{platform}_download"

            def probe(self, request, *, runner):
                raise AssertionError("invalid locator reached the Adapter")

            def acquire(self, request, *, runner):
                raise AssertionError("invalid locator reached acquisition")

        cases = (
            (
                "youtube",
                DeterministicLocatorRequest(
                    source_url="https://youtu.be/yt-test-001",
                    original_title="Ambiguous short URL",
                ),
            ),
            (
                "youtube",
                DeterministicLocatorRequest(
                    source_url=(
                        "https://www.youtube.com/watch?v=yt-test-001&list=PL1"
                    ),
                    original_title="Ambiguous playlist URL",
                ),
            ),
            (
                "bilibili",
                DeterministicLocatorRequest(
                    source_url="https://www.bilibili.com/video/BV1gCLq6YEW4/",
                    original_title="Missing explicit part",
                ),
            ),
        )
        for index, (platform, request) in enumerate(cases):
            with self.subTest(platform=platform, source_url=request.source_url):
                kernel = VideoWorkflowKernel(
                    new_test_root(f"ambiguous-{index}") / "workspace"
                )
                with self.assertRaises(ContractError):
                    kernel.bootstrap_production_source(
                        adapter=Adapter(platform),
                        request=request,
                        runner=object(),
                        task_start="2026-07-18T12:00:00+08:00",
                        request_id=f"ambiguous-{index}",
                        provider_kind="deterministic_locator",
                    )

    def test_bilibili_deterministic_locator_binds_the_explicit_part(self) -> None:
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.models import DeterministicLocatorRequest
        from video2pdf_workflow_kernel.utils import read_json

        class NoCallBilibiliAdapter:
            adapter_id = "bilibili-yt-dlp.v1"
            canonical_platform = "bilibili"
            download_resource_class = "bilibili_download"

            def probe(self, request, *, runner):
                raise AssertionError("deterministic locator called the live Adapter")

            def acquire(self, request, *, runner):
                raise AssertionError("deterministic locator acquired media")

        kernel = VideoWorkflowKernel(new_test_root("bilibili-locator") / "workspace")
        result = kernel.bootstrap_production_source(
            adapter=NoCallBilibiliAdapter(),
            request=DeterministicLocatorRequest(
                source_url="https://www.bilibili.com/video/BV1gCLq6YEW4/",
                original_title="Bilibili live smoke fixture",
                explicit_item_selector="p1",
            ),
            runner=object(),
            task_start="2026-07-18T12:00:00+08:00",
            request_id="bilibili-deterministic-locator",
            provider_kind="deterministic_locator",
        )

        record = read_json(result.record_path)
        self.assertEqual(record["canonical_item_id"], "BV1gCLq6YEW4:p1")
        self.assertEqual(
            record["source_request"]["canonical_locator"],
            "https://www.bilibili.com/video/BV1gCLq6YEW4/",
        )

    def test_deterministic_locator_initialization_replays_the_pending_run(self) -> None:
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.models import DeterministicLocatorRequest
        from video2pdf_workflow_kernel.utils import read_json

        class NoCallAdapter:
            adapter_id = "youtube-yt-dlp.v1"
            canonical_platform = "youtube"
            download_resource_class = "youtube_download"

            def probe(self, request, *, runner):
                raise AssertionError("deterministic locator called the live Adapter")

            def acquire(self, request, *, runner):
                raise AssertionError("deterministic locator acquired media")

        kernel = VideoWorkflowKernel(
            PROJECT_ROOT / "待删除" / "r" / uuid.uuid4().hex[:6] / "w"
        )
        bootstrap = kernel.bootstrap_production_source(
            adapter=NoCallAdapter(),
            request=DeterministicLocatorRequest(
                source_url="https://www.youtube.com/watch?v=yt-test-001",
                original_title="YouTube locator replay",
            ),
            runner=object(),
            task_start="2026-07-18T12:00:00+08:00",
            request_id="deterministic-locator-replay",
            provider_kind="deterministic_locator",
        )

        first = kernel.initialize_production_source(bootstrap)
        with mock.patch.object(
            kernel,
            "_verify_current_source",
            side_effect=AssertionError(
                "pending initialization replay used the ready-only verifier"
            ),
        ):
            replay = kernel.initialize_production_source(bootstrap)

        self.assertEqual(replay.run_dir, first.run_dir)
        run = read_json(replay.run_dir / "workflow/run.json")
        self.assertEqual(run["source_state"], "pending")
        self.assertEqual(run["phase"], "source_acquisition")
        self.assertEqual(
            run["checkpoints"]["run_initialized"]["status"], "current"
        )

    def test_recorded_live_provider_only_starts_inside_resource_admission(self) -> None:
        from video2pdf_workflow_kernel import adapters
        from video2pdf_workflow_kernel.adapters import (
            RecordedCommandRunner,
            YtDlpRuntime,
        )
        from video2pdf_workflow_kernel import source_live_smoke
        from video2pdf_workflow_kernel.source_live_smoke import (
            CredentialBinding,
            SourceLiveSmokeCase,
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        root = PROJECT_ROOT / "待删除" / "s" / uuid.uuid4().hex[:6]
        work_root = root / "w"
        cookie = root / "c.txt"
        cookie.parent.mkdir(parents=True, exist_ok=False)
        cookie.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSID\trecorded\n",
            encoding="utf-8",
        )
        recording = (
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/providers/youtube/fresh-download"
        )
        recorded = RecordedCommandRunner(recording)
        admission = {"active": False, "launches": 0}

        class GuardedRunner:
            def run(_runner, command):
                self.assertTrue(
                    admission["active"],
                    f"provider command started before admission: {command.operation}",
                )
                return recorded.run(command)

        real_launch = source_live_smoke.launch_admitted_platform_acquisition

        def guarded_launch(*, kernel, platform, attempt_id, claim_generation, acquire):
            def admitted(launch_token):
                admission["active"] = True
                admission["launches"] += 1
                try:
                    return acquire(launch_token)
                finally:
                    admission["active"] = False

            return real_launch(
                kernel=kernel,
                platform=platform,
                attempt_id=attempt_id,
                claim_generation=claim_generation,
                acquire=admitted,
            )

        runtime = YtDlpRuntime(
            python_executable=Path("python"),
            ffmpeg_dir=Path("ffmpeg-bin"),
            ffprobe_executable=Path("ffprobe"),
        )
        case = SourceLiveSmokeCase(
            platform="youtube",
            source_url="https://www.youtube.com/watch?v=yt-test-001",
            original_title="YouTube Adapter Fixture",
            explicit_item_selector=None,
            content_classification="language_learning",
            subtitle_language_priority=("en", "zh-Hans"),
            whisper_allowed=True,
            max_video_height=1080,
        )
        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {
                        "yt-dlp": "recorded",
                        "ffprobe": "recorded",
                        "node": "recorded",
                    },
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=GuardedRunner(),
            ),
            mock.patch.object(
                source_live_smoke,
                "launch_admitted_platform_acquisition",
                side_effect=guarded_launch,
            ),
        ):
            result = _execute_kernel_source_live_smoke(
                case,
                CredentialBinding("youtube", cookie),
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        recorded.assert_consumed()
        self.assertEqual(admission, {"active": False, "launches": 1})
        self.assertEqual(len(recorded.evidence), 7)
        run = read_json(result.run_path)
        self.assertEqual(run["source_state"], "ready")
        self.assertEqual(run["checkpoints"]["source_ready"]["status"], "current")

    def test_cookie_rejection_from_admitted_provider_persists_run_blocker(
        self,
    ) -> None:
        from video2pdf_workflow_kernel import adapters, source_live_smoke
        from video2pdf_workflow_kernel.adapters import (
            PlatformAdapterError,
            YtDlpRuntime,
        )
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_live_smoke import (
            CredentialBinding,
            SourceLiveSmokeCase,
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        root = PROJECT_ROOT / "待删除" / "cookie-blocker" / uuid.uuid4().hex[:6]
        work_root = root / "workspace"
        cookie = root / "cookie.txt"
        cookie.parent.mkdir(parents=True, exist_ok=False)
        cookie.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSID\trejected\n",
            encoding="utf-8",
        )

        class RejectingRunner:
            def run(self, command):
                raise PlatformAdapterError(
                    "platform cookie was rejected",
                    classification="source_authentication_required",
                    exit_code=30,
                    blocker_kind="user_input",
                    data={"authentication_classification": "cookie_rejected"},
                )

        runtime = YtDlpRuntime(
            python_executable=Path("python"),
            ffmpeg_dir=Path("ffmpeg-bin"),
            ffprobe_executable=Path("ffprobe"),
        )
        case = SourceLiveSmokeCase(
            platform="youtube",
            source_url="https://www.youtube.com/watch?v=yt-test-001",
            original_title="YouTube cookie rejection fixture",
            explicit_item_selector=None,
            content_classification="language_learning",
            subtitle_language_priority=("en",),
            whisper_allowed=True,
            max_video_height=1080,
        )
        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=RejectingRunner(),
            ),
            self.assertRaises(PlatformAdapterError) as raised,
        ):
            _execute_kernel_source_live_smoke(
                case,
                CredentialBinding("youtube", cookie),
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        run_paths = tuple(work_root.glob("*/workflow/run.json"))
        self.assertEqual(len(run_paths), 1)
        run = read_json(run_paths[0])
        self.assertEqual(run["source_state"], "blocked_user_input")
        self.assertEqual(run["source_blocker"], raised.exception.data["source_blocker"])
        self.assertEqual(run["source_blocker"]["reason"], "cookie_rejected")
        self.assertEqual(
            {
                (item["resource_class"], item["scope_kind"], item["state"])
                for item in VideoWorkflowKernel(work_root).resource_circuit_breaker_status()
            },
            {("youtube_download", "platform", "open")},
        )

    def test_provider_lease_is_released_when_candidate_materialization_fails(
        self,
    ) -> None:
        from video2pdf_workflow_kernel import (
            adapters,
            source_candidates,
            source_live_smoke,
        )
        from video2pdf_workflow_kernel.adapters import (
            RecordedCommandRunner,
            YtDlpRuntime,
        )
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_live_smoke import (
            CredentialBinding,
            SourceLiveSmokeCase,
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        root = PROJECT_ROOT / "待删除" / "pmf" / uuid.uuid4().hex[:6]
        root.mkdir(parents=True, exist_ok=False)
        work_root = root / "workspace"
        cookie = root / "cookie.txt"
        cookie.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSID\trecorded\n",
            encoding="utf-8",
        )
        recording = (
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/providers/youtube/fresh-download"
        )
        recorded = RecordedCommandRunner(recording)
        runtime = YtDlpRuntime(
            python_executable=Path("python"),
            ffmpeg_dir=Path("ffmpeg-bin"),
            ffprobe_executable=Path("ffprobe"),
        )
        case = SourceLiveSmokeCase(
            platform="youtube",
            source_url="https://www.youtube.com/watch?v=yt-test-001",
            original_title="YouTube Adapter Fixture",
            explicit_item_selector=None,
            content_classification="language_learning",
            subtitle_language_priority=("en", "zh-Hans"),
            whisper_allowed=True,
            max_video_height=1080,
        )
        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=recorded,
            ),
            mock.patch.object(
                source_candidates,
                "materialize_source_candidates",
                side_effect=RuntimeError(
                    "forced deterministic materialization failure"
                ),
            ),
            self.assertRaisesRegex(
                RuntimeError, "forced deterministic materialization failure"
            ),
        ):
            _execute_kernel_source_live_smoke(
                case,
                CredentialBinding("youtube", cookie),
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        recorded.assert_consumed()
        self.assertEqual(len(recorded.evidence), 7)
        proofs = tuple(work_root.rglob("terminal-proofs/*.json"))
        self.assertEqual(len(proofs), 1)
        proof = read_json(proofs[0])
        self.assertEqual(proof["stage"], "provider")
        self.assertEqual(proof["declared_outcome"], "failed")

        kernel = VideoWorkflowKernel(work_root)
        capacity = kernel.resource_capacity_status()
        self.assertEqual(capacity["resources"]["youtube_download"]["usage"], 0)
        run_path = next(work_root.rglob("workflow/run.json"))
        run = read_json(run_path)
        task_id = kernel.derive_production_source_task_id(
            run_path.parent.parent,
            task_stage="provider_acquisition",
            logical_task_key=(
                f"source-acquisition-provider-epoch-{run['source_epoch']}"
            ),
        )
        lease = kernel.resource_status(task_id, proof["attempt_id"])
        self.assertEqual(lease.lease_state, "released")

    def test_semantic_lease_is_released_when_judgment_callback_fails(self) -> None:
        from video2pdf_workflow_kernel import adapters, source_live_smoke
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_live_smoke import (
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        work_root, recorded, runtime, case, credential = (
            recorded_youtube_smoke_inputs("semantic-callback-failure")
        )
        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=recorded,
            ),
            mock.patch.object(
                source_live_smoke,
                "build_deterministic_smoke_judgment_patch",
                side_effect=RuntimeError("forced semantic callback failure"),
            ),
            self.assertRaisesRegex(
                RuntimeError, "forced semantic callback failure"
            ),
        ):
            _execute_kernel_source_live_smoke(
                case,
                credential,
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        recorded.assert_consumed()
        proofs = [read_json(path) for path in work_root.rglob("terminal-proofs/*.json")]
        semantic_proof = next(item for item in proofs if item["stage"] == "semantic")
        self.assertEqual(semantic_proof["declared_outcome"], "failed")
        self.assertEqual(semantic_proof["artifacts"], {})

        kernel = VideoWorkflowKernel(work_root)
        capacity = kernel.resource_capacity_status()
        self.assertEqual(capacity["resources"]["codex_semantic"]["usage"], 0)
        run_path = next(work_root.rglob("workflow/run.json"))
        run = read_json(run_path)
        task_id = kernel.derive_production_source_task_id(
            run_path.parent.parent,
            task_stage="semantic_judgment",
            logical_task_key=(
                f"source-acquisition-semantic-epoch-{run['source_epoch']}"
            ),
        )
        lease = kernel.resource_status(task_id, semantic_proof["attempt_id"])
        self.assertEqual(lease.lease_state, "released")

    def test_semantic_lease_is_released_when_judgment_output_is_missing(self) -> None:
        from video2pdf_workflow_kernel import adapters, source_live_smoke
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_live_smoke import (
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        work_root, recorded, runtime, case, credential = (
            recorded_youtube_smoke_inputs("semantic-output-failure")
        )
        real_write = source_live_smoke._write_task_json_output

        def omit_semantic_output(prepared, claimed, logical_id, value):
            if logical_id == "source_acquisition_decision":
                path, _ = source_live_smoke._task_output_path(
                    prepared, claimed, logical_id
                )
                return path
            return real_write(prepared, claimed, logical_id, value)

        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=recorded,
            ),
            mock.patch.object(
                source_live_smoke,
                "_write_task_json_output",
                side_effect=omit_semantic_output,
            ),
            self.assertRaises(FileNotFoundError),
        ):
            _execute_kernel_source_live_smoke(
                case,
                credential,
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        recorded.assert_consumed()
        proofs = [read_json(path) for path in work_root.rglob("terminal-proofs/*.json")]
        semantic_proof = next(item for item in proofs if item["stage"] == "semantic")
        self.assertEqual(semantic_proof["declared_outcome"], "failed")

        kernel = VideoWorkflowKernel(work_root)
        capacity = kernel.resource_capacity_status()
        self.assertEqual(capacity["resources"]["codex_semantic"]["usage"], 0)
        run_path = next(work_root.rglob("workflow/run.json"))
        run = read_json(run_path)
        task_id = kernel.derive_production_source_task_id(
            run_path.parent.parent,
            task_stage="semantic_judgment",
            logical_task_key=(
                f"source-acquisition-semantic-epoch-{run['source_epoch']}"
            ),
        )
        lease = kernel.resource_status(task_id, semantic_proof["attempt_id"])
        self.assertEqual(lease.lease_state, "released")

    def test_whisper_lease_is_released_when_provider_fails(self) -> None:
        from video2pdf_workflow_kernel import adapters, source_live_smoke
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_live_smoke import (
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        work_root, recorded, runtime, case, credential = (
            recorded_youtube_smoke_inputs("whisper-provider-failure")
        )
        real_builder = source_live_smoke.build_deterministic_smoke_judgment_patch

        def choose_whisper(**kwargs):
            skeleton = json.loads(json.dumps(kwargs["skeleton"]))
            skeleton["allowed_judgment"]["subtitle_candidate_ids"] = []
            return real_builder(**{**kwargs, "skeleton": skeleton})

        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=recorded,
            ),
            mock.patch.object(
                source_live_smoke,
                "build_deterministic_smoke_judgment_patch",
                side_effect=choose_whisper,
            ),
            mock.patch.object(
                source_live_smoke,
                "_transcribe_whisper",
                side_effect=RuntimeError("forced Whisper provider failure"),
            ),
            self.assertRaisesRegex(
                RuntimeError, "forced Whisper provider failure"
            ),
        ):
            _execute_kernel_source_live_smoke(
                case,
                credential,
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        recorded.assert_consumed()
        proofs = [read_json(path) for path in work_root.rglob("terminal-proofs/*.json")]
        whisper_proof = next(item for item in proofs if item["stage"] == "whisper")
        self.assertEqual(whisper_proof["declared_outcome"], "failed")
        self.assertEqual(whisper_proof["artifacts"], {})

        kernel = VideoWorkflowKernel(work_root)
        capacity = kernel.resource_capacity_status()
        self.assertEqual(capacity["resources"]["whisper"]["usage"], 0)
        run_path = next(work_root.rglob("workflow/run.json"))
        run = read_json(run_path)
        task_id = kernel.derive_production_source_task_id(
            run_path.parent.parent,
            task_stage="whisper_transcription",
            logical_task_key=(
                f"source-whisper-transcription-epoch-{run['source_epoch']}"
            ),
        )
        lease = kernel.resource_status(task_id, whisper_proof["attempt_id"])
        self.assertEqual(lease.lease_state, "released")

    def test_whisper_lease_is_released_when_transcript_output_is_missing(
        self,
    ) -> None:
        from video2pdf_workflow_kernel import adapters, source_live_smoke
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_live_smoke import (
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        work_root, recorded, runtime, case, credential = (
            recorded_youtube_smoke_inputs("whisper-output-missing")
        )
        real_builder = source_live_smoke.build_deterministic_smoke_judgment_patch

        def choose_whisper(**kwargs):
            skeleton = json.loads(json.dumps(kwargs["skeleton"]))
            skeleton["allowed_judgment"]["subtitle_candidate_ids"] = []
            return real_builder(**{**kwargs, "skeleton": skeleton})

        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=recorded,
            ),
            mock.patch.object(
                source_live_smoke,
                "build_deterministic_smoke_judgment_patch",
                side_effect=choose_whisper,
            ),
            mock.patch.object(
                source_live_smoke,
                "_transcribe_whisper",
                return_value="en",
            ),
            self.assertRaises(FileNotFoundError),
        ):
            _execute_kernel_source_live_smoke(
                case,
                credential,
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        recorded.assert_consumed()
        proofs = [read_json(path) for path in work_root.rglob("terminal-proofs/*.json")]
        whisper_proof = next(item for item in proofs if item["stage"] == "whisper")
        self.assertEqual(whisper_proof["declared_outcome"], "failed")
        self.assertEqual(whisper_proof["artifacts"], {})

        kernel = VideoWorkflowKernel(work_root)
        self.assertEqual(
            kernel.resource_capacity_status()["resources"]["whisper"]["usage"],
            0,
        )
        run_path = next(work_root.rglob("workflow/run.json"))
        run = read_json(run_path)
        task_id = kernel.derive_production_source_task_id(
            run_path.parent.parent,
            task_stage="whisper_transcription",
            logical_task_key=(
                f"source-whisper-transcription-epoch-{run['source_epoch']}"
            ),
        )
        lease = kernel.resource_status(task_id, whisper_proof["attempt_id"])
        self.assertEqual(lease.lease_state, "released")

    def test_whisper_lease_is_released_when_transcript_output_drifts(
        self,
    ) -> None:
        from video2pdf_workflow_kernel import adapters, source_live_smoke
        from video2pdf_workflow_kernel.errors import ArtifactDrift
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_live_smoke import (
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json, sha256_file

        work_root, recorded, runtime, case, credential = (
            recorded_youtube_smoke_inputs("whisper-output-drift")
        )
        real_builder = source_live_smoke.build_deterministic_smoke_judgment_patch
        transcript_output: Path | None = None

        def choose_whisper(**kwargs):
            skeleton = json.loads(json.dumps(kwargs["skeleton"]))
            skeleton["allowed_judgment"]["subtitle_candidate_ids"] = []
            return real_builder(**{**kwargs, "skeleton": skeleton})

        def write_then_drift(_audio_path: Path, output_path: Path) -> str:
            nonlocal transcript_output
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(
                b"1\n00:00:00,000 --> 00:00:01,000\nrecorded transcript\n"
            )
            transcript_output = output_path
            return "en"

        def reject_drifted_output(path: Path) -> str:
            if transcript_output is not None and path == transcript_output:
                raise ArtifactDrift("forced Whisper transcript output drift")
            return sha256_file(path)

        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=recorded,
            ),
            mock.patch.object(
                source_live_smoke,
                "build_deterministic_smoke_judgment_patch",
                side_effect=choose_whisper,
            ),
            mock.patch.object(
                source_live_smoke,
                "_transcribe_whisper",
                side_effect=write_then_drift,
            ),
            mock.patch.object(
                source_live_smoke,
                "sha256_file",
                side_effect=reject_drifted_output,
            ),
            self.assertRaisesRegex(
                ArtifactDrift, "forced Whisper transcript output drift"
            ),
        ):
            _execute_kernel_source_live_smoke(
                case,
                credential,
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        recorded.assert_consumed()
        proofs = [read_json(path) for path in work_root.rglob("terminal-proofs/*.json")]
        whisper_proof = next(item for item in proofs if item["stage"] == "whisper")
        self.assertEqual(whisper_proof["declared_outcome"], "failed")
        self.assertEqual(whisper_proof["artifacts"], {})

        kernel = VideoWorkflowKernel(work_root)
        self.assertEqual(
            kernel.resource_capacity_status()["resources"]["whisper"]["usage"],
            0,
        )
        run_path = next(work_root.rglob("workflow/run.json"))
        run = read_json(run_path)
        task_id = kernel.derive_production_source_task_id(
            run_path.parent.parent,
            task_stage="whisper_transcription",
            logical_task_key=(
                f"source-whisper-transcription-epoch-{run['source_epoch']}"
            ),
        )
        lease = kernel.resource_status(task_id, whisper_proof["attempt_id"])
        self.assertEqual(lease.lease_state, "released")

    def test_whisper_lease_is_released_when_launch_token_is_ambiguous(
        self,
    ) -> None:
        from video2pdf_workflow_kernel import adapters, source_live_smoke
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_acquisition import (
            AdmittedSourceProviderLauncher,
        )
        from video2pdf_workflow_kernel.source_live_smoke import (
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        work_root, recorded, runtime, case, credential = (
            recorded_youtube_smoke_inputs("whisper-token-ambiguity")
        )
        real_builder = source_live_smoke.build_deterministic_smoke_judgment_patch
        real_launch = AdmittedSourceProviderLauncher.launch_whisper

        def choose_whisper(**kwargs):
            skeleton = json.loads(json.dumps(kwargs["skeleton"]))
            skeleton["allowed_judgment"]["subtitle_candidate_ids"] = []
            return real_builder(**{**kwargs, "skeleton": skeleton})

        def write_transcript(_audio_path: Path, output_path: Path) -> str:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(
                b"1\n00:00:00,000 --> 00:00:01,000\nrecorded transcript\n"
            )
            return "en"

        def launch_twice(
            launcher,
            *,
            attempt_id,
            claim_generation,
            provider,
            fault_point=None,
        ):
            result = real_launch(
                launcher,
                attempt_id=attempt_id,
                claim_generation=claim_generation,
                provider=provider,
                fault_point=fault_point,
            )
            provider("forced-ambiguous-launch-token")
            return result

        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=recorded,
            ),
            mock.patch.object(
                source_live_smoke,
                "build_deterministic_smoke_judgment_patch",
                side_effect=choose_whisper,
            ),
            mock.patch.object(
                source_live_smoke,
                "_transcribe_whisper",
                side_effect=write_transcript,
            ),
            mock.patch.object(
                AdmittedSourceProviderLauncher,
                "launch_whisper",
                new=launch_twice,
            ),
            self.assertRaisesRegex(
                ContractError, "Whisper launch token is ambiguous"
            ),
        ):
            _execute_kernel_source_live_smoke(
                case,
                credential,
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        recorded.assert_consumed()
        proofs = [read_json(path) for path in work_root.rglob("terminal-proofs/*.json")]
        whisper_proof = next(item for item in proofs if item["stage"] == "whisper")
        self.assertEqual(whisper_proof["declared_outcome"], "failed")
        self.assertEqual(whisper_proof["artifacts"], {})

        kernel = VideoWorkflowKernel(work_root)
        self.assertEqual(
            kernel.resource_capacity_status()["resources"]["whisper"]["usage"],
            0,
        )
        run_path = next(work_root.rglob("workflow/run.json"))
        run = read_json(run_path)
        task_id = kernel.derive_production_source_task_id(
            run_path.parent.parent,
            task_stage="whisper_transcription",
            logical_task_key=(
                f"source-whisper-transcription-epoch-{run['source_epoch']}"
            ),
        )
        lease = kernel.resource_status(task_id, whisper_proof["attempt_id"])
        self.assertEqual(lease.lease_state, "released")

    def test_whisper_success_releases_lease_and_completes_task(self) -> None:
        from video2pdf_workflow_kernel import adapters, source_live_smoke
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_live_smoke import (
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        work_root, recorded, runtime, case, credential = (
            recorded_youtube_smoke_inputs("whisper-success")
        )
        real_builder = source_live_smoke.build_deterministic_smoke_judgment_patch

        def choose_whisper(**kwargs):
            skeleton = json.loads(json.dumps(kwargs["skeleton"]))
            skeleton["allowed_judgment"]["subtitle_candidate_ids"] = []
            return real_builder(**{**kwargs, "skeleton": skeleton})

        def write_transcript(_audio_path: Path, output_path: Path) -> str:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(
                b"1\n00:00:00,000 --> 00:00:01,000\nrecorded transcript\n"
            )
            return "en"

        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=recorded,
            ),
            mock.patch.object(
                source_live_smoke,
                "build_deterministic_smoke_judgment_patch",
                side_effect=choose_whisper,
            ),
            mock.patch.object(
                source_live_smoke,
                "_transcribe_whisper",
                side_effect=write_transcript,
            ),
        ):
            result = _execute_kernel_source_live_smoke(
                case,
                credential,
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        recorded.assert_consumed()
        run = read_json(result.run_path)
        self.assertEqual(run["source_state"], "ready")
        self.assertEqual(run["checkpoints"]["source_ready"]["status"], "current")
        proofs = [read_json(path) for path in work_root.rglob("terminal-proofs/*.json")]
        whisper_proof = next(item for item in proofs if item["stage"] == "whisper")
        self.assertEqual(whisper_proof["declared_outcome"], "succeeded")
        self.assertEqual(
            set(whisper_proof["artifacts"]), {"source_transcription"}
        )

        kernel = VideoWorkflowKernel(work_root)
        self.assertEqual(
            kernel.resource_capacity_status()["resources"]["whisper"]["usage"],
            0,
        )
        task_id = kernel.derive_production_source_task_id(
            result.run_path.parent.parent,
            task_stage="whisper_transcription",
            logical_task_key=(
                f"source-whisper-transcription-epoch-{run['source_epoch']}"
            ),
        )
        self.assertEqual(kernel.task_claim_status(task_id)["state"], "terminal")

    def test_recorded_probe_rejects_a_stale_locator_title_before_acquisition(
        self,
    ) -> None:
        from video2pdf_workflow_kernel import adapters, source_live_smoke
        from video2pdf_workflow_kernel.adapters import (
            RecordedCommandRunner,
            YtDlpRuntime,
        )
        from video2pdf_workflow_kernel.errors import ArtifactDrift
        from video2pdf_workflow_kernel.kernel import VideoWorkflowKernel
        from video2pdf_workflow_kernel.source_live_smoke import (
            CredentialBinding,
            SourceLiveSmokeCase,
            _execute_kernel_source_live_smoke,
        )
        from video2pdf_workflow_kernel.utils import read_json

        root = PROJECT_ROOT / "待删除" / "stale-title" / uuid.uuid4().hex[:6]
        work_root = root / "workspace"
        cookie = root / "cookie.txt"
        cookie.parent.mkdir(parents=True, exist_ok=False)
        cookie.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.test\tTRUE\t/\tTRUE\t2147483647\tSID\trecorded\n",
            encoding="utf-8",
        )
        recording = (
            PROJECT_ROOT
            / "tests/video_workflow/fixtures/providers/youtube/fresh-download"
        )
        recorded = RecordedCommandRunner(recording)
        runtime = YtDlpRuntime(
            python_executable=Path("python"),
            ffmpeg_dir=Path("ffmpeg-bin"),
            ffprobe_executable=Path("ffprobe"),
        )
        case = SourceLiveSmokeCase(
            platform="youtube",
            source_url="https://www.youtube.com/watch?v=yt-test-001",
            original_title="Stale YouTube Fixture Title",
            explicit_item_selector=None,
            content_classification="language_learning",
            subtitle_language_priority=("en", "zh-Hans"),
            whisper_allowed=True,
            max_video_height=1080,
        )
        with (
            mock.patch.object(
                source_live_smoke,
                "_runtime_tools",
                return_value=(
                    runtime,
                    {"yt-dlp": "recorded", "ffprobe": "recorded"},
                    HASH_A,
                ),
            ),
            mock.patch.object(
                adapters,
                "SubprocessCommandRunner",
                return_value=recorded,
            ),
            self.assertRaisesRegex(ArtifactDrift, "original title"),
        ):
            _execute_kernel_source_live_smoke(
                case,
                CredentialBinding("youtube", cookie),
                work_root,
                PROJECT_ROOT,
                "2026-07-18T12:00:00+08:00",
            )

        self.assertEqual(
            [item.operation for item in recorded.evidence],
            ["subtitle_list", "metadata_probe"],
        )
        capacity = VideoWorkflowKernel(work_root).resource_capacity_status()
        self.assertEqual(capacity["resources"]["youtube_download"]["usage"], 0)
        proofs = tuple(
            (work_root / "待删除" / "source-live-smoke" / "terminal-proofs").glob(
                "*.json"
            )
        )
        self.assertEqual(len(proofs), 1)
        self.assertEqual(read_json(proofs[0])["declared_outcome"], "failed")

    def test_provider_acquisition_uses_kernel_resource_admission_boundary(self) -> None:
        from video2pdf_workflow_kernel.source_live_smoke import (
            launch_admitted_platform_acquisition,
        )

        kernel = RecordingKernel()
        sentinel = object()

        result = launch_admitted_platform_acquisition(
            kernel=kernel,
            platform="youtube",
            attempt_id="2" * 24,
            claim_generation=3,
            acquire=lambda launch_token: (launch_token, sentinel),
        )

        self.assertEqual(result, ("launch-token", sentinel))
        self.assertEqual(
            kernel.launches,
            [("2" * 24, 3, ("youtube_download",))],
        )

    def test_deterministic_smoke_worker_stays_inside_skeleton_choices(self) -> None:
        from video2pdf_workflow_kernel.source_live_smoke import (
            build_deterministic_smoke_judgment_patch,
        )

        skeleton = {
            "task_id": "3" * 32,
            "allowed_judgment": {
                "subtitle_candidate_ids": [HASH_A],
                "whisper_choices": ["not_required", "use_whisper", "unavailable"],
                "whisper_audio_candidate_id": HASH_B,
            },
        }
        patch = build_deterministic_smoke_judgment_patch(
            skeleton=skeleton,
            task_id="3" * 32,
            attempt_id="4" * 24,
            task_envelope_sha256=HASH_A,
            skeleton_sha256=HASH_B,
        )

        self.assertEqual(
            set(patch["judgment"]),
            {
                "selected_subtitle_candidate_id",
                "subtitle_selection_rationale",
                "whisper_fallback",
                "known_gaps",
            },
        )
        self.assertEqual(
            patch["judgment"]["selected_subtitle_candidate_id"], HASH_A
        )
        self.assertEqual(
            patch["judgment"]["whisper_fallback"]["choice"], "not_required"
        )

        without_subtitles = {
            **skeleton,
            "allowed_judgment": {
                **skeleton["allowed_judgment"],
                "subtitle_candidate_ids": [],
            },
        }
        patch = build_deterministic_smoke_judgment_patch(
            skeleton=without_subtitles,
            task_id="3" * 32,
            attempt_id="4" * 24,
            task_envelope_sha256=HASH_A,
            skeleton_sha256=HASH_B,
        )
        self.assertEqual(
            patch["judgment"]["whisper_fallback"]["choice"], "use_whisper"
        )

    def test_whisper_writer_numbers_only_nonempty_cues(self) -> None:
        from types import SimpleNamespace

        from video2pdf_workflow_kernel.source_live_smoke import _transcribe_whisper

        root = new_test_root("whisper-cues")
        output = root / "transcription.srt"

        class Model:
            def transcribe(self, audio_path, *, fp16):
                return {
                    "language": "en",
                    "segments": [
                        {"start": 0.0, "end": 1.0, "text": " First "},
                        {"start": 1.0, "end": 2.0, "text": "  "},
                        {"start": 2.0, "end": 3.0, "text": "Second"},
                    ],
                }

        fake_whisper = SimpleNamespace(load_model=lambda name: Model())
        with mock.patch.dict(sys.modules, {"whisper": fake_whisper}):
            language = _transcribe_whisper(root / "audio.m4a", output)

        self.assertEqual(language, "en")
        self.assertEqual(
            output.read_text(encoding="utf-8"),
            "1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\nSecond\n",
        )

    def test_report_is_closed_secret_free_and_bound_to_current_manifest(self) -> None:
        from video2pdf_workflow_kernel.source_live_smoke import (
            SourceLiveSmokeExecution,
            build_smoke_report,
        )

        root = new_test_root("report")
        run_path, manifest_path = current_run(root)
        execution = SourceLiveSmokeExecution(
            run_path=run_path,
            manifest_path=manifest_path,
            command_argv_redacted=(
                "D:/Project/video2pdf/kimi/.venv/Scripts/python.exe",
                "-m",
                "yt_dlp",
                "--ffmpeg-location",
                "D:/Project/video2pdf/kimi/tools/ffmpeg/bin",
                "--cookies",
                "<localized-cookie-file>",
                "https://www.bilibili.com/video/BV1TEST00001/",
            ),
            authentication_classification="cookie_accepted",
            tool_versions={"yt-dlp": "2026.05.05", "ffprobe": "7.1"},
            runtime_policy_sha256=HASH_A,
        )

        report = build_smoke_report(
            execution,
            expected_platform="bilibili",
            project_root=PROJECT_ROOT,
            recorded_at="2026-07-18T12:00:00+08:00",
        )

        self.assertEqual(
            set(report),
            {
                "platform",
                "adapter_id",
                "adapter_contract_version",
                "provider_kind",
                "run_id",
                "command_argv_redacted",
                "authentication_classification",
                "tool_versions",
                "target_checkpoint",
                "source_manifest",
                "runtime_policy_sha256",
                "recorded_at",
            },
        )
        self.assertEqual(report["command_argv_redacted"].count("<COOKIE_FILE>"), 1)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("cookies.txt", serialized.lower())
        self.assertNotIn("D:/Project", serialized)
        self.assertEqual(
            report["target_checkpoint"]["evidence_sha256"],
            report["source_manifest"]["sha256"],
        )
        self.assertEqual(
            report["source_manifest"]["source_identity"],
            json.loads(manifest_path.read_text(encoding="utf-8"))["source_identity"],
        )
        self.assertEqual(report["source_manifest"]["canonical_platform"], "bilibili")
        self.assertEqual(
            report["source_manifest"]["canonical_item_id"],
            "BV1TEST00001:p1",
        )

    def test_report_rejects_a_stale_source_ready_binding(self) -> None:
        from video2pdf_workflow_kernel.errors import ArtifactDrift
        from video2pdf_workflow_kernel.source_live_smoke import (
            SourceLiveSmokeExecution,
            build_smoke_report,
        )

        root = new_test_root("stale")
        run_path, manifest_path = current_run(root, "youtube")
        run_record = json.loads(run_path.read_text(encoding="utf-8"))
        run_record["checkpoints"]["source_ready"]["status"] = "stale"
        write_json(run_path, run_record)
        execution = SourceLiveSmokeExecution(
            run_path=run_path,
            manifest_path=manifest_path,
            command_argv_redacted=(
                "python",
                "-m",
                "yt_dlp",
                "--cookies",
                "<localized-cookie-file>",
            ),
            authentication_classification="cookie_accepted",
            tool_versions={"yt-dlp": "2026.05.05"},
            runtime_policy_sha256=HASH_A,
        )

        with self.assertRaisesRegex(ArtifactDrift, "source_ready"):
            build_smoke_report(
                execution,
                expected_platform="youtube",
                project_root=PROJECT_ROOT,
                recorded_at=datetime.now(timezone.utc).isoformat(),
            )

    def test_runner_resolves_the_closed_profile_only_inside_the_process(self) -> None:
        from video2pdf_workflow_kernel.errors import ContractError
        from video2pdf_workflow_kernel.source_live_smoke import (
            SourceLiveSmokeExecution,
            run_source_live_smoke,
        )

        root = new_test_root("profile")
        case_path = root / "case.json"
        write_json(case_path, case_value("youtube"))
        run_path, manifest_path = current_run(root, "youtube")
        observed_cookie_paths: list[Path] = []

        def execute(case, credential, work_root, project_root, recorded_at):
            observed_cookie_paths.append(credential.localized_cookie_file)
            self.assertEqual(case.platform, "youtube")
            self.assertEqual(work_root, (root / "work").resolve())
            self.assertEqual(project_root, PROJECT_ROOT.resolve())
            self.assertEqual(recorded_at, "2026-07-18T12:00:00+08:00")
            return SourceLiveSmokeExecution(
                run_path=run_path,
                manifest_path=manifest_path,
                command_argv_redacted=(
                    "python",
                    "-m",
                    "yt_dlp",
                    "--cookies",
                    "<localized-cookie-file>",
                ),
                authentication_classification="cookie_accepted",
                tool_versions={"yt-dlp": "2026.05.05"},
                runtime_policy_sha256=HASH_A,
            )

        report = run_source_live_smoke(
            spec_path=case_path,
            credential_profile="youtube-project-cookie",
            work_root=root / "work",
            project_root=PROJECT_ROOT,
            executor=execute,
            clock=lambda: datetime.fromisoformat("2026-07-18T12:00:00+08:00"),
        )

        self.assertEqual(len(observed_cookie_paths), 1)
        cookie_path = observed_cookie_paths[0]
        self.assertEqual(cookie_path.name, "www.youtube.com_cookies.txt")
        self.assertNotIn(str(cookie_path), json.dumps(report, ensure_ascii=False))
        with self.assertRaisesRegex(ContractError, "another platform"):
            run_source_live_smoke(
                spec_path=case_path,
                credential_profile="bilibili-project-cookie",
                work_root=root / "work",
                project_root=PROJECT_ROOT,
                executor=execute,
            )

    def test_cli_emits_raw_closed_report_and_masks_unexpected_failure(self) -> None:
        from video2pdf_workflow_kernel import cli

        root = new_test_root("cli")
        case_path = root / "case.json"
        write_json(case_path, case_value())
        report = {
            "platform": "bilibili",
            "adapter_id": "bilibili",
            "adapter_contract_version": "1.0.0",
            "provider_kind": "live",
            "run_id": RUN_ID,
            "command_argv_redacted": [
                "python",
                "-m",
                "yt_dlp",
                "--cookies",
                "<COOKIE_FILE>",
            ],
            "authentication_classification": "cookie_accepted",
            "tool_versions": {"yt-dlp": "2026.05.05"},
            "target_checkpoint": {
                "name": "source_ready",
                "status": "current",
                "evidence_sha256": HASH_A,
            },
            "source_manifest": {
                "path": "workspace/待删除/source/manifest.json",
                "sha256": HASH_A,
                "canonical_platform": "bilibili",
                "canonical_item_id": "BV1TEST00001:p1",
                "source_identity": HASH_B,
                "source_version": HASH_A,
            },
            "runtime_policy_sha256": HASH_B,
            "recorded_at": "2026-07-18T12:00:00+08:00",
        }
        stdout = StringIO()
        stderr = StringIO()
        with mock.patch.object(cli, "run_source_live_smoke", return_value=report):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "source-live-smoke",
                        "--spec",
                        str(case_path),
                        "--credential-profile",
                        "bilibili-project-cookie",
                        "--work-root",
                        str(root / "work"),
                    ]
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertEqual(stderr.getvalue(), "")

        leaked = "C:/Users/example/private-cookies.txt"
        stdout = StringIO()
        stderr = StringIO()
        with mock.patch.object(
            cli, "run_source_live_smoke", side_effect=RuntimeError(leaked)
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "source-live-smoke",
                        "--spec",
                        str(case_path),
                        "--credential-profile",
                        "bilibili-project-cookie",
                        "--work-root",
                        str(root / "work"),
                    ]
                )
        self.assertEqual(exit_code, 70)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(leaked, stderr.getvalue())
        self.assertIn("source live smoke failed", stderr.getvalue().lower())


if __name__ == "__main__":
    unittest.main()

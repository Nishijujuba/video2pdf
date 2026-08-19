from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from .adapters import (
    BilibiliPlatformAdapter,
    RecordedCommandRunner,
    SubprocessCommandRunner,
    YouTubePlatformAdapter,
    YtDlpRuntime,
)
from .errors import ContractError, KernelConflict
from .kernel import VideoWorkflowKernel
from .source_live_smoke import (
    CredentialBinding,
    SourceLiveSmokeCase,
    _TerminalProofRegistry,
    _runtime_tools,
    acquire_source_for_initialized_run,
)
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_file,
)


def _platform_adapter(platform: str, runtime: YtDlpRuntime):
    if platform == "youtube":
        return YouTubePlatformAdapter(runtime)
    return BilibiliPlatformAdapter(runtime)


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _credential_root(run_dir: Path) -> Path:
    root = run_dir / "待删除" / "source-acquire" / "credentials"
    require_contained_path(
        root,
        run_dir,
        purpose="source acquisition credential root",
        error_type=ContractError,
        allow_missing=True,
    )
    root.mkdir(parents=True, exist_ok=True)
    return require_contained_path(
        root,
        run_dir,
        purpose="source acquisition credential root",
        error_type=ContractError,
        leaf_kind="directory",
    )


def _localized_cookie(
    source: Path,
    run_dir: Path,
    *,
    attempt_id: str | None = None,
    claim_generation: int | None = None,
) -> Path:
    if (attempt_id is None) != (claim_generation is None):
        raise ContractError(
            "source acquisition credential attempt binding is incomplete"
        )
    if attempt_id is not None and (
        re.fullmatch(r"[0-9a-f]{24}", attempt_id) is None
        or isinstance(claim_generation, bool)
        or not isinstance(claim_generation, int)
        or claim_generation < 1
    ):
        raise ContractError("source acquisition credential attempt binding is invalid")
    try:
        if not source.is_file() or source.is_symlink() or _is_reparse_point(source):
            raise ContractError("source acquisition cookie is not a regular file")
        payload = source.read_bytes()
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError("source acquisition cookie is unreadable") from exc
    if not payload:
        raise ContractError("source acquisition cookie is empty")
    identity = hashlib.sha256(payload).hexdigest()[:24]
    root = _credential_root(run_dir) / identity
    if attempt_id is not None:
        root = root / "attempts" / f"{attempt_id}.g{claim_generation}"
    require_contained_path(
        root,
        run_dir,
        purpose="source acquisition credential directory",
        error_type=ContractError,
        allow_missing=True,
    )
    root.mkdir(parents=True, exist_ok=True)
    require_contained_path(
        root,
        run_dir,
        purpose="source acquisition credential directory",
        error_type=ContractError,
        leaf_kind="directory",
    )
    target = root / "cookies.txt"
    require_contained_path(
        target,
        run_dir,
        purpose="source acquisition localized cookie",
        error_type=ContractError,
        allow_missing=True,
    )
    if target.exists():
        if (
            not target.is_file()
            or target.is_symlink()
            or _is_reparse_point(target)
            or target.read_bytes() != payload
        ):
            raise KernelConflict("localized source credential changed on replay")
        return require_contained_path(
            target,
            run_dir,
            purpose="source acquisition localized cookie",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
    temporary = root / f".cookies.{uuid.uuid4().hex}.kernel-new"
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        raise ContractError("source acquisition cookie localization failed") from exc
    return require_contained_path(
        target,
        run_dir,
        purpose="source acquisition localized cookie",
        error_type=ContractError,
        leaf_kind="file",
        require_single_link=True,
    )


def acquire_bilibili_source_for_run(
    *,
    run_dir: Path,
    cookie_file: Path,
    provider_recording: Path | None,
    whisper_transcript: Path | None = None,
    fault_point: str | None = None,
) -> dict[str, object]:
    """Run the platform provider against the identity of one existing Run."""

    run_dir = run_dir.resolve()
    project_root = Path(__file__).resolve().parents[2]
    run = read_json(run_dir / "workflow" / "run.json")
    canonical_platform = str(run.get("canonical_platform"))
    if canonical_platform not in {"bilibili", "youtube"}:
        raise ContractError("source-acquire currently supports Bilibili or YouTube Runs only")
    if (
        run.get("source_state") == "ready"
        and run.get("checkpoints", {}).get("source_ready", {}).get("status")
        == "current"
    ):
        manifest_path = run_dir / "source" / "manifest.json"
        inventory_path = run_dir / "work/source-acquisition/candidate-inventory.json"
        manifest_generation = run.get("artifact_generations", {}).get(
            "source_manifest", {}
        )
        inventory_generation = run.get("artifact_generations", {}).get(
            "source_candidate_inventory", {}
        )
        if (
            not manifest_path.is_file()
            or manifest_generation.get("sha256") != sha256_file(manifest_path)
            or not inventory_path.is_file()
            or inventory_generation.get("sha256") != sha256_file(inventory_path)
        ):
            raise ContractError("source-acquire current source authority drifted")
        inventory = read_json(inventory_path)
        expected_kind = "live"
        expected_recording_sha256 = None
        if provider_recording is not None:
            replay = RecordedCommandRunner(provider_recording.resolve())
            expected_kind = "recorded_fixture"
            expected_recording_sha256 = replay.recording_evidence.manifest_sha256
        provider = inventory.get("provider", {})
        if (
            provider.get("kind") != expected_kind
            or provider.get("recording_sha256") != expected_recording_sha256
            or inventory.get("source_identity") != run.get("source_identity")
        ):
            raise ContractError("source-acquire replay differs from current source authority")
        return {
            "run_id": run["run_id"],
            "run_dir": str(run_dir),
            "source_identity": run["source_identity"],
            "source_manifest": str(manifest_path),
            "checkpoint": "source_ready",
            "checkpoint_status": "current",
            "idempotent": True,
        }
    item_id = str(run["canonical_item_id"])
    base_item_id, separator, part = item_id.partition(":")
    explicit_selector = part if separator else None
    if canonical_platform == "youtube":
        source_url = f"https://www.youtube.com/watch?v={base_item_id}"
    else:
        source_url = f"https://www.bilibili.com/video/{base_item_id}/"
    case = SourceLiveSmokeCase(
        platform=canonical_platform,
        source_url=source_url,
        original_title=str(run["original_title"]),
        explicit_item_selector=explicit_selector,
        content_classification="general",
        subtitle_language_priority=("en", "zh-Hans", "zh", "ai-zh"),
        whisper_allowed=True,
        max_video_height=1080,
    )
    if provider_recording is not None:
        runner = RecordedCommandRunner(provider_recording.resolve())
        evidence = runner.recording_evidence
        runtime = YtDlpRuntime(
            python_executable=Path("python"),
            ffmpeg_dir=Path("ffmpeg-bin"),
            ffprobe_executable=Path("ffprobe"),
        )
        adapter = _platform_adapter(canonical_platform, runtime)
        runner.assert_adapter_binding(
            canonical_platform=canonical_platform,
            adapter_id=adapter.adapter_id,
            adapter_contract_version=adapter.adapter_contract_version,
        )
        versions = {"recorded-provider": evidence.recording_id}
        runtime_policy_sha256 = hashlib.sha256(
            canonical_json_bytes(
                {
                    "provider": "recorded_fixture",
                    "recording_sha256": evidence.manifest_sha256,
                }
            )
        ).hexdigest()
        provider_kind = "recorded_fixture"
        recording_sha256 = evidence.manifest_sha256
    else:
        runtime, versions, runtime_policy_sha256 = _runtime_tools(
            case,
            project_root,
            policy_schema_name="source-acquire-runtime-policy",
        )
        adapter = _platform_adapter(canonical_platform, runtime)
        runner = SubprocessCommandRunner()
        evidence = None
        provider_kind = "live"
        recording_sha256 = None
    disposable_root = (
        run_dir.parent.parent
        / "待删除"
        / "source-acquire"
        / str(run["run_id"])
    )
    _credential_root(run_dir)
    proofs = _TerminalProofRegistry(
        disposable_root / "terminal-proofs",
        project_root,
        provider_id="source-acquire",
    )
    kernel = VideoWorkflowKernel(
        run_dir.parent,
        resource_provider_verifiers={"source-acquire": proofs.verify},
    )
    recorded_at = str(run["task_start"])
    execution = acquire_source_for_initialized_run(
        kernel=kernel,
        run_dir=run_dir,
        case=case,
        credential=CredentialBinding(
            platform=canonical_platform,
            localized_cookie_file=cookie_file.resolve(),
        ),
        credential_materializer=lambda binding, claim: CredentialBinding(
            platform=binding.platform,
            localized_cookie_file=_localized_cookie(
                binding.localized_cookie_file,
                run_dir,
                attempt_id=claim.attempt_id,
                claim_generation=claim.claim_generation,
            ),
        ),
        adapter=adapter,
        runner=runner,
        proofs=proofs,
        recorded_at=recorded_at,
        versions=versions,
        runtime_policy_sha256=runtime_policy_sha256,
        provider_kind=provider_kind,
        recording_sha256=recording_sha256,
        recording_evidence=evidence,
        disposable_root=disposable_root,
        whisper_transcript=(
            None if whisper_transcript is None else whisper_transcript.resolve()
        ),
        fault_point=fault_point,
    )
    current = read_json(execution.run_path)
    return {
        "run_id": current["run_id"],
        "run_dir": str(run_dir),
        "source_identity": current["source_identity"],
        "source_manifest": str(execution.manifest_path),
        "checkpoint": "source_ready",
        "checkpoint_status": current["checkpoints"]["source_ready"]["status"],
        "idempotent": False,
    }


def reconcile_bilibili_source_acquire(*, run_dir: Path) -> dict[str, object]:
    """Resume lease release and Task publication from durable terminal proofs."""

    run_dir = run_dir.resolve()
    project_root = Path(__file__).resolve().parents[2]
    run = read_json(run_dir / "workflow" / "run.json")
    proof_root = (
        run_dir.parent.parent
        / "待删除"
        / "source-acquire"
        / str(run["run_id"])
        / "terminal-proofs"
    )
    proofs = _TerminalProofRegistry(
        proof_root,
        project_root,
        provider_id="source-acquire",
    )
    kernel = VideoWorkflowKernel(
        run_dir.parent,
        resource_provider_verifiers={"source-acquire": proofs.verify},
    )
    released = 0
    advanced = 0
    for proof in proofs.records():
        status = kernel.resource_status(proof["task_id"], proof["attempt_id"])
        if status.lease_state != "released":
            kernel.release_resource_lease(
                proof["attempt_id"],
                proof["claim_generation"],
                proof["launch_token"],
                terminal_evidence={
                    "evidence_class": "provider_terminal_result",
                    "provider": "source-acquire",
                    "terminal_result_id": proof["terminal_result_id"],
                    "declared_outcome": proof["declared_outcome"],
                    "observed_at": proof["observed_at"],
                },
            )
            released += 1
        claim = kernel.task_claim_status(proof["task_id"])
        if (
            claim is not None
            and claim["state"] == "active"
            and claim["attempt_id"] == proof["attempt_id"]
            and claim["claim_generation"] == proof["claim_generation"]
        ):
            kernel.reclaim_task(
                run_dir,
                task_id=proof["task_id"],
                expected_attempt_id=proof["attempt_id"],
                expected_claim_generation=proof["claim_generation"],
                coordinator_session_id="source-acquire-bilibili",
                worker_id=f"source-acquire-bilibili-{proof['stage']}",
                reason="durable terminal proof requires a fresh provider generation",
            )
            advanced += 1
    return {
        "run_id": run["run_id"],
        "run_dir": str(run_dir),
        "proofs_loaded": len(proofs.records()),
        "leases_released": released,
        "tasks_advanced": advanced,
    }

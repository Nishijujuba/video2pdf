from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from importlib import metadata as importlib_metadata
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any, Callable, Literal, Mapping, TypeVar
from urllib.parse import urlsplit

from .errors import ArtifactDrift, ContractError
from .source_acquisition import AdmittedSourceProviderLauncher
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_bytes,
    sha256_file,
    write_json_atomic,
)


_SHA256_LENGTH = 64
_CASE_FIELDS = {
    "schema_name",
    "schema_version",
    "platform",
    "source_url",
    "original_title",
    "explicit_item_selector",
    "content_classification",
    "subtitle_language_priority",
    "whisper_allowed",
    "max_video_height",
}
_PLATFORM_HOSTS = {
    "bilibili": {"bilibili.com", "www.bilibili.com"},
    "youtube": {"youtube.com", "www.youtube.com"},
}
_CREDENTIAL_PROFILES = {
    "bilibili-project-cookie": (
        "bilibili",
        Path.home() / "Downloads" / "www.bilibili.com_cookies.txt",
    ),
    "youtube-project-cookie": (
        "youtube",
        Path.home() / "Downloads" / "www.youtube.com_cookies.txt",
    ),
}
_T = TypeVar("_T")


@dataclass(frozen=True)
class SourceLiveSmokeCase:
    platform: Literal["bilibili", "youtube"]
    source_url: str
    original_title: str
    explicit_item_selector: str | None
    content_classification: Literal["general", "language_learning"]
    subtitle_language_priority: tuple[str, ...]
    whisper_allowed: bool
    max_video_height: int


@dataclass(frozen=True, repr=False)
class CredentialBinding:
    platform: Literal["bilibili", "youtube"]
    localized_cookie_file: Path


@dataclass(frozen=True)
class SourceLiveSmokeExecution:
    run_path: Path
    manifest_path: Path
    command_argv_redacted: tuple[str, ...]
    authentication_classification: str
    tool_versions: Mapping[str, str]
    runtime_policy_sha256: str


class _TerminalProofRegistry:
    def __init__(self, proof_root: Path, project_root: Path) -> None:
        self.proof_root = proof_root
        self.project_root = project_root
        self._proofs: dict[str, tuple[str, str, str]] = {}

    def record(
        self,
        *,
        terminal_result_id: str,
        attempt_id: str,
        stage: str,
        declared_outcome: Literal["succeeded", "failed"],
        artifacts: Mapping[str, str],
        observed_at: str,
    ) -> None:
        value = {
            "schema_name": "source-live-smoke-terminal-proof",
            "schema_version": "1.0.0",
            "terminal_result_id": terminal_result_id,
            "attempt_id": attempt_id,
            "stage": stage,
            "declared_outcome": declared_outcome,
            "artifacts": dict(sorted(artifacts.items())),
            "observed_at": observed_at,
        }
        path = self.proof_root / f"{terminal_result_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        expected = canonical_json_bytes(value)
        if path.exists():
            if path.is_symlink() or _is_reparse_point(path) or path.read_bytes() != expected:
                raise ArtifactDrift("source live smoke terminal proof drifted")
        else:
            write_json_atomic(path, value)
        proof_sha = sha256_file(path)
        reference = (
            f"{_project_relative(path, self.project_root, label='terminal proof')}"
            f"#sha256={proof_sha}"
        )
        self._proofs[terminal_result_id] = (
            attempt_id,
            declared_outcome,
            reference,
        )

    def verify(self, **binding: Any) -> str:
        terminal_result_id = binding.get("terminal_result_id")
        try:
            attempt_id, declared_outcome, reference = self._proofs[
                str(terminal_result_id)
            ]
        except KeyError as exc:
            raise ContractError("source live smoke terminal proof is unknown") from exc
        if (
            binding.get("attempt_id") != attempt_id
            or binding.get("declared_outcome") != declared_outcome
        ):
            raise ContractError("source live smoke terminal proof has a stale attempt")
        return reference


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    marker = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & marker)


def _contained_regular_file(path: Path, boundary: Path, *, label: str) -> Path:
    candidate = path if path.is_absolute() else boundary / path
    return require_contained_path(
        candidate,
        boundary,
        purpose=label,
        error_type=ContractError,
        leaf_kind="file",
        require_single_link=True,
    )


def _closed_case(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _CASE_FIELDS:
        raise ContractError("Source live smoke case differs from its closed field set")
    if (
        value["schema_name"] != "source-live-smoke-case"
        or value["schema_version"] != "1.0.0"
    ):
        raise ContractError("Source live smoke case contract version is unsupported")
    return value


def load_smoke_case(path: Path, *, project_root: Path) -> SourceLiveSmokeCase:
    case_path = _contained_regular_file(path, project_root, label="smoke case")
    try:
        value = _closed_case(json.loads(case_path.read_text(encoding="utf-8")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("Source live smoke case is not canonical JSON") from exc
    platform = value["platform"]
    if platform not in _PLATFORM_HOSTS:
        raise ContractError("Source live smoke platform is unsupported")
    source_url = value["source_url"]
    if not isinstance(source_url, str) or not source_url.strip():
        raise ContractError("Source live smoke URL is invalid")
    parsed = urlsplit(source_url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in _PLATFORM_HOSTS[platform]:
        raise ContractError("Source live smoke URL disagrees with its platform")
    original_title = value["original_title"]
    if (
        not isinstance(original_title, str)
        or not original_title
        or original_title.strip() != original_title
        or len(original_title) > 2000
    ):
        raise ContractError("Source live smoke original title is invalid")
    selector = value["explicit_item_selector"]
    if selector is not None and (
        not isinstance(selector, str)
        or not selector.startswith("p")
        or not selector[1:].isdigit()
        or int(selector[1:]) < 1
    ):
        raise ContractError("Source live smoke item selector is invalid")
    if platform == "youtube" and selector is not None:
        raise ContractError("YouTube live smoke does not accept an item selector")
    classification = value["content_classification"]
    if classification not in {"general", "language_learning"}:
        raise ContractError("Source live smoke content classification is unsupported")
    priorities = value["subtitle_language_priority"]
    if (
        not isinstance(priorities, list)
        or not priorities
        or any(
            not isinstance(item, str)
            or not item
            or item.strip() != item
            for item in priorities
        )
        or len(priorities) != len(set(priorities))
    ):
        raise ContractError("Source live smoke subtitle priority is invalid")
    if classification == "language_learning" and priorities[0] != "en":
        raise ContractError("Language-learning smoke must prioritize English")
    whisper_allowed = value["whisper_allowed"]
    if not isinstance(whisper_allowed, bool):
        raise ContractError("Source live smoke Whisper policy is invalid")
    height = value["max_video_height"]
    if isinstance(height, bool) or not isinstance(height, int) or not 144 <= height <= 4320:
        raise ContractError("Source live smoke video height is invalid")
    return SourceLiveSmokeCase(
        platform=platform,
        source_url=source_url,
        original_title=original_title,
        explicit_item_selector=selector,
        content_classification=classification,
        subtitle_language_priority=tuple(priorities),
        whisper_allowed=whisper_allowed,
        max_video_height=height,
    )


def resolve_credential_profile(
    profile: str, *, expected_platform: str
) -> CredentialBinding:
    try:
        platform, cookie_file = _CREDENTIAL_PROFILES[profile]
    except KeyError as exc:
        raise ContractError("Source live smoke credential profile is unknown") from exc
    if platform != expected_platform:
        raise ContractError("Source live smoke credential profile targets another platform")
    return CredentialBinding(platform=platform, localized_cookie_file=cookie_file)


def launch_admitted_platform_acquisition(
    *,
    kernel: Any,
    platform: str,
    attempt_id: str,
    claim_generation: int,
    acquire: Callable[[str], _T],
) -> _T:
    if platform not in _PLATFORM_HOSTS:
        raise ContractError("Source live smoke platform is unsupported")
    return AdmittedSourceProviderLauncher(kernel).launch_adapter(
        attempt_id=attempt_id,
        claim_generation=claim_generation,
        resource_class=f"{platform}_download",
        provider=acquire,
    )


def build_deterministic_smoke_judgment_patch(
    *,
    skeleton: Mapping[str, Any],
    task_id: str,
    attempt_id: str,
    task_envelope_sha256: str,
    skeleton_sha256: str,
) -> dict[str, Any]:
    if skeleton.get("task_id") != task_id:
        raise ContractError("smoke semantic Task differs from its Decision Skeleton")
    allowed = skeleton.get("allowed_judgment")
    if not isinstance(allowed, Mapping):
        raise ContractError("smoke Decision Skeleton lacks bounded judgment choices")
    subtitle_ids = allowed.get("subtitle_candidate_ids")
    whisper_choices = allowed.get("whisper_choices")
    if (
        not isinstance(subtitle_ids, list)
        or any(not isinstance(item, str) for item in subtitle_ids)
        or not isinstance(whisper_choices, list)
    ):
        raise ContractError("smoke Decision Skeleton choices are invalid")
    selected = subtitle_ids[0] if subtitle_ids else None
    known_gaps: list[dict[str, Any]] = []
    if selected is not None:
        choice = "not_required"
        selection_rationale = (
            "The first policy-ranked usable subtitle candidate was selected."
        )
        fallback_rationale = "A policy-ranked usable subtitle candidate exists."
    elif (
        "use_whisper" in whisper_choices
        and allowed.get("whisper_audio_candidate_id") is not None
    ):
        choice = "use_whisper"
        selection_rationale = "No usable subtitle candidate is available."
        fallback_rationale = "The declared audio candidate requires Whisper fallback."
    elif "unavailable" in whisper_choices:
        choice = "unavailable"
        selection_rationale = "No usable subtitle candidate is available."
        fallback_rationale = "The bounded smoke policy exposes no usable fallback."
        known_gaps = [
            {
                "code": "missing_subtitles",
                "description": "No usable subtitle or transcript is available.",
                "affected_ranges": [],
            }
        ]
    else:
        raise ContractError("smoke Decision Skeleton has no complete judgment path")
    patch = {
        "schema_name": "source-acquisition-judgment-patch",
        "schema_version": "2.0.0",
        "kernel_version": "2.0.0",
        "task_id": task_id,
        "attempt_id": attempt_id,
        "task_envelope_sha256": _require_sha256(
            task_envelope_sha256, label="Task Envelope"
        ),
        "skeleton_sha256": _require_sha256(
            skeleton_sha256, label="Decision Skeleton"
        ),
        "judgment": {
            "selected_subtitle_candidate_id": selected,
            "subtitle_selection_rationale": selection_rationale,
            "whisper_fallback": {
                "choice": choice,
                "rationale": fallback_rationale,
            },
            "known_gaps": known_gaps,
        },
    }
    return patch


def _looks_absolute_path(value: str) -> bool:
    return (
        len(value) >= 3
        and value[1] == ":"
        and value[2] in {"/", "\\"}
    ) or value.startswith("/")


def _safe_command_argv(argv: tuple[str, ...]) -> list[str]:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ContractError("Source live smoke command evidence is invalid")
    safe = list(argv)
    cookie_indexes = [index for index, item in enumerate(safe) if item == "--cookies"]
    if len(cookie_indexes) != 1 or cookie_indexes[0] + 1 >= len(safe):
        raise ContractError("Source live smoke command lacks one cookie argument")
    cookie_value_index = cookie_indexes[0] + 1
    if safe[cookie_value_index] not in {
        "<localized-cookie-file>",
        "<COOKIE_FILE>",
    }:
        raise ContractError("Source live smoke command contains an unredacted credential")
    safe[cookie_value_index] = "<COOKIE_FILE>"
    for index, item in enumerate(tuple(safe)):
        if index == cookie_value_index or not _looks_absolute_path(item):
            continue
        if index == 0:
            safe[index] = "<PYTHON>"
        elif index > 0 and safe[index - 1] == "--ffmpeg-location":
            safe[index] = "<FFMPEG_DIR>"
        else:
            raise ContractError("Source live smoke command contains an unsafe local path")
    serialized = "\n".join(safe).casefold()
    if "cookie:" in serialized or "cookies.txt" in serialized:
        raise ContractError("Source live smoke command contains authentication material")
    return safe


def _require_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractError(f"{label} is not a SHA-256 fingerprint")
    return value


def _project_relative(path: Path, project_root: Path, *, label: str) -> str:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ContractError(f"{label} escapes the project root") from exc
    return relative.as_posix()


def _work_root_target(path: Path, project_root: Path) -> Path:
    project_root = project_root.resolve()
    target = path if path.is_absolute() else project_root / path
    target = Path(os.path.abspath(target))
    try:
        target.relative_to(project_root)
    except ValueError as exc:
        raise ContractError("Source live smoke work root escapes the project root") from exc
    current = target
    while current != project_root:
        if current.exists() and (current.is_symlink() or _is_reparse_point(current)):
            raise ContractError("Source live smoke work root crosses a link or reparse point")
        current = current.parent
    return target


def build_smoke_report(
    execution: SourceLiveSmokeExecution,
    *,
    expected_platform: str,
    project_root: Path,
    recorded_at: str,
) -> dict[str, Any]:
    if expected_platform not in _PLATFORM_HOSTS:
        raise ContractError("Source live smoke platform is unsupported")
    try:
        parsed_time = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("Source live smoke timestamp is invalid") from exc
    if parsed_time.tzinfo is None:
        raise ContractError("Source live smoke timestamp must include a timezone")
    run_path = _contained_regular_file(
        execution.run_path, project_root, label="smoke Run Record"
    )
    manifest_path = _contained_regular_file(
        execution.manifest_path, project_root, label="smoke Source Manifest"
    )
    run = read_json(run_path)
    manifest = read_json(manifest_path)
    manifest_sha = sha256_file(manifest_path)
    if (
        run.get("schema_name") != "run-record"
        or run.get("schema_version") != "3.0.0"
        or run.get("canonical_platform") != expected_platform
        or run.get("source_state") != "ready"
        or run.get("phase") != "source_ready"
    ):
        raise ArtifactDrift("source_ready Run authority is not current")
    checkpoint = run.get("checkpoints", {}).get("source_ready")
    generation = run.get("artifact_generations", {}).get("source_manifest")
    expected_manifest_path = run_path.parent.parent / "source" / "manifest.json"
    if (
        manifest_path.resolve() != expected_manifest_path.resolve()
        or not isinstance(checkpoint, dict)
        or checkpoint.get("status") != "current"
        or checkpoint.get("evidence_sha256") != manifest_sha
        or not isinstance(generation, dict)
        or generation.get("path") != "source/manifest.json"
        or generation.get("sha256") != manifest_sha
        or not any(
            binding.get("logical_id") == "source_manifest"
            and binding.get("generation") == generation.get("generation")
            and binding.get("sha256") == manifest_sha
            for binding in checkpoint.get("artifact_bindings", [])
            if isinstance(binding, dict)
        )
    ):
        raise ArtifactDrift("source_ready checkpoint has a stale Source Manifest binding")
    if (
        manifest.get("schema_name") != "source-manifest"
        or manifest.get("schema_version") != "2.0.0"
        or manifest.get("package_status") != "validated"
        or manifest.get("run_id") != run.get("run_id")
        or manifest.get("canonical_platform") != expected_platform
        or manifest.get("source_identity") != run.get("source_identity")
        or manifest.get("source_version") != run.get("source_version")
    ):
        raise ArtifactDrift("source_ready Source Manifest identity is stale")
    source_identity = _require_sha256(
        manifest.get("source_identity"), label="Source Identity"
    )
    source_version = _require_sha256(
        manifest.get("source_version"), label="Source Version"
    )
    canonical_item_id = manifest.get("canonical_item_id")
    if not isinstance(canonical_item_id, str) or not canonical_item_id:
        raise ArtifactDrift("source_ready Source Manifest item identity is invalid")
    if execution.authentication_classification != "cookie_accepted":
        raise ContractError("Source live smoke lacks accepted authentication evidence")
    tool_versions = dict(execution.tool_versions)
    if not tool_versions or any(
        not isinstance(name, str)
        or not name
        or not isinstance(version, str)
        or not version
        for name, version in tool_versions.items()
    ):
        raise ContractError("Source live smoke tool versions are invalid")
    runtime_policy_sha = _require_sha256(
        execution.runtime_policy_sha256, label="runtime policy"
    )
    return {
        "platform": expected_platform,
        "adapter_id": expected_platform,
        "adapter_contract_version": "1.0.0",
        "provider_kind": "live",
        "run_id": run["run_id"],
        "command_argv_redacted": _safe_command_argv(
            execution.command_argv_redacted
        ),
        "authentication_classification": execution.authentication_classification,
        "tool_versions": dict(sorted(tool_versions.items())),
        "target_checkpoint": {
            "name": "source_ready",
            "status": "current",
            "evidence_sha256": manifest_sha,
        },
        "source_manifest": {
            "path": _project_relative(
                manifest_path, project_root, label="smoke Source Manifest"
            ),
            "sha256": manifest_sha,
            "canonical_platform": expected_platform,
            "canonical_item_id": canonical_item_id,
            "source_identity": source_identity,
            "source_version": source_version,
        },
        "runtime_policy_sha256": runtime_policy_sha,
        "recorded_at": recorded_at,
    }


def _runtime_tools(
    case: SourceLiveSmokeCase, project_root: Path
) -> tuple[Any, dict[str, str], str]:
    from .adapters import YtDlpRuntime

    ffmpeg_dir = project_root.parent / "kimi" / "tools" / "ffmpeg" / "bin"
    ffprobe = ffmpeg_dir / "ffprobe.exe"
    if not ffprobe.is_file():
        raise ContractError("source live smoke ffprobe runtime is unavailable")
    try:
        yt_dlp_version = importlib_metadata.version("yt-dlp")
    except importlib_metadata.PackageNotFoundError as exc:
        raise ContractError("source live smoke yt-dlp runtime is unavailable") from exc
    try:
        completed = subprocess.run(
            (str(ffprobe), "-version"),
            cwd=project_root,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError("source live smoke ffprobe version check failed") from exc
    if completed.returncode != 0:
        raise ContractError("source live smoke ffprobe version check failed")
    first_line = completed.stdout.decode("utf-8", errors="replace").splitlines()
    if not first_line:
        raise ContractError("source live smoke ffprobe version is unavailable")
    ffprobe_parts = first_line[0].split()
    ffprobe_version = ffprobe_parts[2] if len(ffprobe_parts) > 2 else ffprobe_parts[-1]
    versions = {"ffprobe": ffprobe_version, "yt-dlp": yt_dlp_version}
    if case.platform == "youtube":
        try:
            node = subprocess.run(
                ("node", "--version"),
                cwd=project_root,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContractError("source live smoke Node runtime is unavailable") from exc
        node_version = node.stdout.decode("utf-8", errors="replace").strip()
        if node.returncode != 0 or not node_version:
            raise ContractError("source live smoke Node runtime is unavailable")
        versions["node"] = node_version.removeprefix("v")
    policy = {
        "schema_name": "source-live-smoke-runtime-policy",
        "schema_version": "1.0.0",
        "platform": case.platform,
        "adapter_contract_version": "1.0.0",
        "content_classification": case.content_classification,
        "subtitle_language_priority": list(case.subtitle_language_priority),
        "whisper_allowed": case.whisper_allowed,
        "max_video_height": case.max_video_height,
        "tool_versions": dict(sorted(versions.items())),
    }
    runtime_policy_sha = sha256_bytes(canonical_json_bytes(policy))
    return (
        YtDlpRuntime(
            python_executable=Path(sys.executable),
            ffmpeg_dir=ffmpeg_dir,
            ffprobe_executable=ffprobe,
        ),
        versions,
        runtime_policy_sha,
    )


def _adapter_for(case: SourceLiveSmokeCase, runtime: Any) -> Any:
    from .adapters import BilibiliPlatformAdapter, YouTubePlatformAdapter

    if case.platform == "bilibili":
        return BilibiliPlatformAdapter(runtime)
    return YouTubePlatformAdapter(runtime)


def _eligible_subtitle_tracks(
    case: SourceLiveSmokeCase, probe: Any
) -> tuple[str, ...]:
    priorities = {
        language.casefold(): index
        for index, language in enumerate(case.subtitle_language_priority)
    }
    selected = [
        track
        for track in probe.subtitle_tracks
        if track.normalized_language.casefold() in priorities
        or track.normalized_language.casefold().split("-", 1)[0] in priorities
    ]
    selected.sort(
        key=lambda track: (
            priorities.get(
                track.normalized_language.casefold(),
                priorities.get(
                    track.normalized_language.casefold().split("-", 1)[0],
                    len(priorities),
                ),
            ),
            0 if track.origin == "manual" else 1,
            track.track_id,
        )
    )
    return tuple(track.track_id for track in selected[:4])


def _copy_candidate_staging(
    scratch_run: Path, attempt_dir: Path, inventory: Mapping[str, Any]
) -> None:
    planned: list[tuple[Path, Path, str]] = []
    attempt_root = attempt_dir.resolve()
    scratch_root = scratch_run.resolve()
    canonical_root = PurePosixPath(
        "work/source-acquisition/candidates"
    ) / f"e{inventory['source_epoch']}"
    for candidate in inventory["candidates"]:
        canonical = PurePosixPath(str(candidate["staged_path"]))
        try:
            relative = canonical.relative_to(canonical_root)
        except ValueError as exc:
            raise ContractError(
                "source live smoke candidate differs from its epoch root"
            ) from exc
        source = scratch_root.joinpath(*canonical.parts)
        target = attempt_root / "o/candidates" / Path(*relative.parts)
        if (
            not source.is_file()
            or source.is_symlink()
            or _is_reparse_point(source)
            or sha256_file(source) != candidate["sha256"]
        ):
            raise ArtifactDrift("source live smoke candidate staging drifted")
        try:
            target.resolve(strict=False).relative_to(attempt_root)
        except ValueError as exc:
            raise ContractError(
                "source live smoke candidate target escapes its Attempt"
            ) from exc
        if target.exists() and (
            target.is_symlink()
            or _is_reparse_point(target)
            or not target.is_file()
            or sha256_file(target) != candidate["sha256"]
        ):
            raise ArtifactDrift("source live smoke candidate target drifted")
        planned.append((source, target, candidate["sha256"]))
    for source, target, expected_sha in planned:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        if sha256_file(target) != expected_sha:
            raise ArtifactDrift("source live smoke candidate publication drifted")


def _task_output_path(
    prepared: Any, claimed: Any, logical_id: str
) -> tuple[Path, Mapping[str, Any]]:
    envelope = read_json(prepared.envelope_path)
    output = next(
        (
            item
            for item in envelope["required_outputs"]
            if item["logical_id"] == logical_id
        ),
        None,
    )
    if output is None:
        raise ContractError("source live smoke Task lacks its declared output")
    path = claimed.attempt_dir / Path(*output["attempt_relative_path"].split("/"))
    return path, output


def _write_task_json_output(
    prepared: Any, claimed: Any, logical_id: str, value: Mapping[str, Any]
) -> Path:
    path, _ = _task_output_path(prepared, claimed, logical_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, dict(value))
    return path


def _terminal_result_id(stage: str, attempt_id: str) -> str:
    suffix = hashlib.sha256(f"{stage}\0{attempt_id}".encode("utf-8")).hexdigest()[:16]
    return f"source-live-smoke-{stage}-{suffix}"


def _release_stage(
    *,
    kernel: Any,
    proofs: _TerminalProofRegistry,
    claimed: Any,
    launch_token: str,
    stage: str,
    artifacts: Mapping[str, str],
    observed_at: str,
    declared_outcome: Literal["succeeded", "failed"] = "succeeded",
) -> None:
    terminal_result_id = _terminal_result_id(stage, claimed.attempt_id)
    proofs.record(
        terminal_result_id=terminal_result_id,
        attempt_id=claimed.attempt_id,
        stage=stage,
        declared_outcome=declared_outcome,
        artifacts=artifacts,
        observed_at=observed_at,
    )
    kernel.release_resource_lease(
        claimed.attempt_id,
        claimed.claim_generation,
        launch_token,
        terminal_evidence={
            "evidence_class": "provider_terminal_result",
            "provider": "source-live-smoke",
            "terminal_result_id": terminal_result_id,
            "declared_outcome": declared_outcome,
            "observed_at": observed_at,
        },
    )


def _complete_and_promote(kernel: Any, run_dir: Path, prepared: Any, claimed: Any) -> None:
    kernel.complete_task(
        run_dir,
        task_id=prepared.task_id,
        attempt_id=claimed.attempt_id,
        claim_generation=claimed.claim_generation,
    )
    kernel.promote_task(
        run_dir,
        task_id=prepared.task_id,
        attempt_id=claimed.attempt_id,
        claim_generation=claimed.claim_generation,
    )


def _transcribe_whisper(audio_path: Path, output_path: Path) -> str:
    try:
        import whisper
    except (ImportError, ModuleNotFoundError) as exc:
        raise ContractError("source live smoke Whisper runtime is unavailable") from exc
    try:
        model = whisper.load_model("base")
        result = model.transcribe(str(audio_path), fp16=False)
    except Exception as exc:
        raise ContractError("source live smoke Whisper provider failed") from exc
    segments = result.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ContractError("source live smoke Whisper returned no transcript")

    def stamp(seconds: float) -> str:
        milliseconds = max(0, round(float(seconds) * 1000))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    lines: list[str] = []
    cue_index = 0
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        cue_index += 1
        lines.extend(
            (
                str(cue_index),
                f"{stamp(segment['start'])} --> {stamp(segment['end'])}",
                text,
                "",
            )
        )
    if not lines:
        raise ContractError("source live smoke Whisper returned an empty transcript")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    language = str(result.get("language") or "und").strip()
    return language or "und"


def _execute_kernel_source_live_smoke(
    case: SourceLiveSmokeCase,
    credential: CredentialBinding,
    work_root: Path,
    project_root: Path,
    recorded_at: str,
) -> SourceLiveSmokeExecution:
    from .adapters import (
        PlatformAcquireRequest,
        PlatformAdapterError,
        PlatformProbeRequest,
        SubprocessCommandRunner,
    )
    from .kernel import VideoWorkflowKernel
    from .models import DeterministicLocatorRequest
    from .source_candidates import (
        SourceCandidatePolicy,
        SourceProviderBinding,
        ToolVersion,
        materialize_source_candidates,
    )
    from .source_package import GeneratedWhisperTranscript
    from .source_acquisition import persist_source_blocker

    work_root.mkdir(parents=True, exist_ok=True)
    trash_root = work_root / "待删除" / "source-live-smoke"
    trash_root.mkdir(parents=True, exist_ok=True)
    runtime, versions, runtime_policy_sha = _runtime_tools(case, project_root)
    adapter = _adapter_for(case, runtime)
    runner = SubprocessCommandRunner()
    proofs = _TerminalProofRegistry(trash_root / "terminal-proofs", project_root)
    kernel = VideoWorkflowKernel(
        work_root,
        resource_provider_verifiers={"source-live-smoke": proofs.verify},
    )
    request_hash = sha256_bytes(
        canonical_json_bytes(
            {
                "platform": case.platform,
                "source_url": case.source_url,
                "recorded_at": recorded_at,
            }
        )
    )[:24]
    bootstrap_request = DeterministicLocatorRequest(
        source_url=case.source_url,
        original_title=case.original_title,
        explicit_item_selector=case.explicit_item_selector,
    )
    bootstrap = kernel.bootstrap_production_source(
        adapter=adapter,
        request=bootstrap_request,
        runner=runner,
        task_start=recorded_at,
        request_id=f"source-live-smoke-{case.platform}-{request_hash}",
        provider_kind="deterministic_locator",
    )
    initialized = kernel.initialize_production_source(bootstrap)
    run_dir = initialized.run_dir
    run = read_json(run_dir / "workflow" / "run.json")
    source_epoch = int(run["source_epoch"])
    provider_key = f"source-acquisition-provider-epoch-{source_epoch}"
    semantic_key = f"source-acquisition-semantic-epoch-{source_epoch}"
    semantic_task_id = kernel.derive_production_source_task_id(
        run_dir,
        task_stage="semantic_judgment",
        logical_task_key=semantic_key,
    )
    prepared_provider = kernel.prepare_production_source_task(
        run_dir,
        task_stage="provider_acquisition",
        logical_task_key=provider_key,
        prepared_at=recorded_at,
    )
    claimed_provider = kernel.claim_task(
        run_dir,
        prepared_provider.task_id,
        coordinator_session_id=f"source-live-smoke-{case.platform}",
        worker_id=f"source-live-smoke-{case.platform}-provider",
    )
    launch_tokens: list[str] = []
    provider_failures: list[Exception] = []

    def acquire(launch_token: str) -> tuple[Any, Any]:
        launch_tokens.append(launch_token)
        staging = trash_root / "provider" / claimed_provider.attempt_id
        try:
            admitted_probe = adapter.probe(
                PlatformProbeRequest(
                    source_url=case.source_url,
                    localized_cookie_file=credential.localized_cookie_file,
                    staging_root=staging,
                    explicit_item_selector=case.explicit_item_selector,
                ),
                runner=runner,
            )
            if (
                admitted_probe.canonical_platform != bootstrap.canonical_platform
                or admitted_probe.canonical_item_id != bootstrap.canonical_item_id
                or admitted_probe.original_title != bootstrap.original_title
            ):
                raise ArtifactDrift(
                    "admitted Source Probe changed deterministic Bootstrap identity "
                    "or original title"
                )
            acquisition = adapter.acquire(
                PlatformAcquireRequest(
                    source_url=case.source_url,
                    localized_cookie_file=credential.localized_cookie_file,
                    staging_root=staging,
                    probe=admitted_probe,
                    eligible_track_ids=_eligible_subtitle_tracks(
                        case, admitted_probe
                    ),
                    max_video_height=case.max_video_height,
                ),
                runner=runner,
            )
            return admitted_probe, acquisition
        except Exception as error:
            provider_failures.append(error)
            return None, None

    probe, acquisition = launch_admitted_platform_acquisition(
        kernel=kernel,
        platform=case.platform,
        attempt_id=claimed_provider.attempt_id,
        claim_generation=claimed_provider.claim_generation,
        acquire=acquire,
    )
    if len(launch_tokens) != 1:
        raise ContractError("source live smoke provider launch token is ambiguous")
    if provider_failures:
        error = provider_failures[0]
        _release_stage(
            kernel=kernel,
            proofs=proofs,
            claimed=claimed_provider,
            launch_token=launch_tokens[0],
            stage="provider",
            artifacts={},
            observed_at=recorded_at,
            declared_outcome="failed",
        )
        if (
            isinstance(error, PlatformAdapterError)
            and error.blocker_kind == "user_input"
        ):
            error.data["source_blocker"] = persist_source_blocker(
                kernel, run_dir, case.platform, error
            )
        raise error
    try:
        policy = SourceCandidatePolicy(
            content_classification=case.content_classification,
            subtitle_language_priority=case.subtitle_language_priority,
            whisper_allowed=case.whisper_allowed,
        )
        provider = SourceProviderBinding(
            kind="live",
            recording_sha256=None,
            tool_versions=tuple(
                ToolVersion(name=name, version=version)
                for name, version in sorted(versions.items())
            ),
        )
        scratch_run = (
            trash_root / "candidate-materialization" / prepared_provider.task_id
        )
        candidates = materialize_source_candidates(
            scratch_run,
            run_id=bootstrap.run_id,
            source_epoch=source_epoch,
            acquisition_id=sha256_bytes(
                f"source-acquisition\0{bootstrap.run_id}\0{source_epoch}".encode(
                    "utf-8"
                )
            )[:32],
            mode="fresh_download",
            probe=probe,
            acquisition=acquisition,
            provider=provider,
            policy=policy,
            task_id=semantic_task_id,
            contracts=kernel.contracts,
        )
        _copy_candidate_staging(
            scratch_run, claimed_provider.attempt_dir, candidates.inventory
        )
        inventory_output = _write_task_json_output(
            prepared_provider,
            claimed_provider,
            "source_candidate_inventory",
            candidates.inventory,
        )
        if candidates.skeleton is None:
            raise ContractError("fresh source live smoke lacks a Decision Skeleton")
        skeleton_output = _write_task_json_output(
            prepared_provider,
            claimed_provider,
            "source_acquisition_decision_skeleton",
            candidates.skeleton,
        )
    except Exception:
        _release_stage(
            kernel=kernel,
            proofs=proofs,
            claimed=claimed_provider,
            launch_token=launch_tokens[0],
            stage="provider",
            artifacts={},
            observed_at=recorded_at,
            declared_outcome="failed",
        )
        raise
    _release_stage(
        kernel=kernel,
        proofs=proofs,
        claimed=claimed_provider,
        launch_token=launch_tokens[0],
        stage="provider",
        artifacts={
            "source_candidate_inventory": sha256_file(inventory_output),
            "source_acquisition_decision_skeleton": sha256_file(skeleton_output),
        },
        observed_at=recorded_at,
    )
    _complete_and_promote(
        kernel, run_dir, prepared_provider, claimed_provider
    )

    prepared_semantic = kernel.prepare_production_source_task(
        run_dir,
        task_stage="semantic_judgment",
        logical_task_key=semantic_key,
        prepared_at=recorded_at,
    )
    if prepared_semantic.task_id != semantic_task_id:
        raise ArtifactDrift("source live smoke semantic Task identity changed")
    claimed_semantic = kernel.claim_task(
        run_dir,
        prepared_semantic.task_id,
        coordinator_session_id=f"source-live-smoke-{case.platform}",
        worker_id=f"source-live-smoke-{case.platform}-semantic",
    )
    semantic_tokens: list[str] = []
    semantic_failures: list[Exception] = []

    def judge(launch_token: str) -> dict[str, Any]:
        semantic_tokens.append(launch_token)
        try:
            skeleton_path = run_dir / "work/source-acquisition/decision.skeleton.json"
            patch = build_deterministic_smoke_judgment_patch(
                skeleton=read_json(skeleton_path),
                task_id=prepared_semantic.task_id,
                attempt_id=claimed_semantic.attempt_id,
                task_envelope_sha256=sha256_file(prepared_semantic.envelope_path),
                skeleton_sha256=sha256_file(skeleton_path),
            )
            _write_task_json_output(
                prepared_semantic,
                claimed_semantic,
                "source_acquisition_decision",
                patch,
            )
            return patch
        except Exception as error:
            semantic_failures.append(error)
            return {}

    patch = kernel.launch_admitted_task(
        claimed_semantic.attempt_id,
        claimed_semantic.claim_generation,
        ("codex_semantic",),
        judge,
    )
    if len(semantic_tokens) != 1:
        raise ContractError("source live smoke semantic launch token is ambiguous")
    if semantic_failures:
        _release_stage(
            kernel=kernel,
            proofs=proofs,
            claimed=claimed_semantic,
            launch_token=semantic_tokens[0],
            stage="semantic",
            artifacts={},
            observed_at=recorded_at,
            declared_outcome="failed",
        )
        raise semantic_failures[0]
    try:
        patch_path, _ = _task_output_path(
            prepared_semantic, claimed_semantic, "source_acquisition_decision"
        )
        patch_sha256 = sha256_file(patch_path)
    except Exception:
        _release_stage(
            kernel=kernel,
            proofs=proofs,
            claimed=claimed_semantic,
            launch_token=semantic_tokens[0],
            stage="semantic",
            artifacts={},
            observed_at=recorded_at,
            declared_outcome="failed",
        )
        raise
    _release_stage(
        kernel=kernel,
        proofs=proofs,
        claimed=claimed_semantic,
        launch_token=semantic_tokens[0],
        stage="semantic",
        artifacts={"source_acquisition_decision": patch_sha256},
        observed_at=recorded_at,
    )
    _complete_and_promote(
        kernel, run_dir, prepared_semantic, claimed_semantic
    )

    whisper_transcript = None
    if patch["judgment"]["whisper_fallback"]["choice"] == "use_whisper":
        inventory = read_json(
            run_dir / "work/source-acquisition/candidate-inventory.json"
        )
        audio_id = candidates.skeleton["allowed_judgment"][
            "whisper_audio_candidate_id"
        ]
        audio = next(
            item for item in inventory["candidates"] if item["candidate_id"] == audio_id
        )
        whisper_key = f"source-whisper-transcription-epoch-{source_epoch}"
        prepared_whisper = kernel.prepare_production_source_task(
            run_dir,
            task_stage="whisper_transcription",
            logical_task_key=whisper_key,
            prepared_at=recorded_at,
            whisper_audio_candidate={
                "candidate_id": audio["candidate_id"],
                "staged_path": audio["staged_path"],
                "sha256": audio["sha256"],
            },
        )
        claimed_whisper = kernel.claim_task(
            run_dir,
            prepared_whisper.task_id,
            coordinator_session_id=f"source-live-smoke-{case.platform}",
            worker_id=f"source-live-smoke-{case.platform}-whisper",
        )
        whisper_tokens: list[str] = []
        whisper_failures: list[Exception] = []

        def transcribe(launch_token: str) -> str:
            whisper_tokens.append(launch_token)
            try:
                output, _ = _task_output_path(
                    prepared_whisper, claimed_whisper, "source_transcription"
                )
                return _transcribe_whisper(
                    run_dir / Path(*audio["staged_path"].split("/")), output
                )
            except Exception as error:
                whisper_failures.append(error)
                return "und"

        language = AdmittedSourceProviderLauncher(kernel).launch_whisper(
            attempt_id=claimed_whisper.attempt_id,
            claim_generation=claimed_whisper.claim_generation,
            provider=transcribe,
        )
        launch_token = whisper_tokens[0] if whisper_tokens else None
        try:
            if len(whisper_tokens) != 1:
                raise ContractError(
                    "source live smoke Whisper launch token is ambiguous"
                )
            if whisper_failures:
                raise whisper_failures[0]
            whisper_output, _ = _task_output_path(
                prepared_whisper, claimed_whisper, "source_transcription"
            )
            whisper_output_sha256 = sha256_file(whisper_output)
        except Exception:
            if launch_token is not None:
                _release_stage(
                    kernel=kernel,
                    proofs=proofs,
                    claimed=claimed_whisper,
                    launch_token=launch_token,
                    stage="whisper",
                    artifacts={},
                    observed_at=recorded_at,
                    declared_outcome="failed",
                )
            raise
        _release_stage(
            kernel=kernel,
            proofs=proofs,
            claimed=claimed_whisper,
            launch_token=launch_token,
            stage="whisper",
            artifacts={"source_transcription": whisper_output_sha256},
            observed_at=recorded_at,
        )
        _complete_and_promote(
            kernel, run_dir, prepared_whisper, claimed_whisper
        )
        run = read_json(run_dir / "workflow/run.json")
        transcript_generation = run["artifact_generations"]["source_transcription"]
        transcript_path = run_dir / transcript_generation["path"]
        whisper_transcript = GeneratedWhisperTranscript(
            staged_path=transcript_generation["path"],
            sha256=sha256_file(transcript_path),
            size_bytes=transcript_path.stat().st_size,
            media_type="application/x-subrip",
            language=language,
            technical_probe={
                "status": "pass",
                "duration_seconds": inventory["source_metadata"]["duration_seconds"],
                "stream_types": ["transcript"],
                "codec_names": ["subrip"],
            },
            generation=transcript_generation["generation"],
            evidence_sha256=transcript_generation["sha256"],
        )
    finalized = kernel.finalize_production_source(
        run_dir,
        published_at=recorded_at,
        whisper_transcript=whisper_transcript,
    )
    media_command = next(
        (
            evidence
            for evidence in acquisition.command_evidence
            if evidence.operation == "media_download"
        ),
        None,
    )
    if media_command is None:
        raise ContractError("source live smoke lacks media download command evidence")
    manifest_path = Path(finalized.manifest_path)
    return SourceLiveSmokeExecution(
        run_path=run_dir / "workflow/run.json",
        manifest_path=manifest_path,
        command_argv_redacted=tuple(media_command.argv),
        authentication_classification=probe.authentication_classification,
        tool_versions=versions,
        runtime_policy_sha256=runtime_policy_sha,
    )


def run_source_live_smoke(
    *,
    spec_path: Path,
    credential_profile: str,
    work_root: Path,
    project_root: Path,
    executor: Callable[
        [
            SourceLiveSmokeCase,
            CredentialBinding,
            Path,
            Path,
            str,
        ],
        SourceLiveSmokeExecution,
    ]
    | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    case = load_smoke_case(spec_path, project_root=project_root)
    credential = resolve_credential_profile(
        credential_profile, expected_platform=case.platform
    )
    target = _work_root_target(work_root, project_root)
    now = (clock or (lambda: datetime.now().astimezone()))()
    if now.tzinfo is None:
        raise ContractError("Source live smoke clock must include a timezone")
    recorded_at = now.isoformat()
    execution = (executor or _execute_kernel_source_live_smoke)(
        case,
        credential,
        target,
        project_root.resolve(),
        recorded_at,
    )
    return build_smoke_report(
        execution,
        expected_platform=case.platform,
        project_root=project_root,
        recorded_at=recorded_at,
    )


__all__ = [
    "CredentialBinding",
    "SourceLiveSmokeCase",
    "SourceLiveSmokeExecution",
    "build_smoke_report",
    "build_deterministic_smoke_judgment_patch",
    "launch_admitted_platform_acquisition",
    "load_smoke_case",
    "resolve_credential_profile",
    "run_source_live_smoke",
]

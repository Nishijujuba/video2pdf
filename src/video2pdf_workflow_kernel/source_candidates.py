from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any, Literal, Mapping, Protocol, Sequence

from .adapters.base import (
    CommandEvidence,
    PlatformAcquisition,
    PlatformProbe,
    RecordedProviderEvidence,
    StagedArtifact,
    command_evidence_sequence_sha256,
)
from .errors import ArtifactDrift, ContractError
from .source_acquisition import (
    SOURCE_IDENTITY_SCHEME,
    SubtitleCandidate,
    build_allowed_source_judgment,
    derive_source_identity,
)
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_bytes,
    sha256_file,
    write_json_atomic,
)


KERNEL_VERSION = "2.0.0"
INVENTORY_PATH = PurePosixPath("work/source-acquisition/candidate-inventory.json")
DECISION_SKELETON_PATH = PurePosixPath(
    "work/source-acquisition/decision.skeleton.json"
)
CANDIDATE_ROOT = PurePosixPath("work/source-acquisition/candidates")
_ID32 = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_OPERATION = re.compile(r"[^a-z0-9-]+")
_AUTH_HEADER = re.compile(r"(?i)(?:authorization|cookie|set-cookie)\s*:")
_QUERY_SECRET = re.compile(
    r"(?i)[?&](?:auth|authorization|cookie|csrf|po_token|session|signature|token|visitor_data)="
)
_SRT_TIMESTAMP = re.compile(
    r"^(?P<start>\d+:\d{2}:\d{2}[,.]\d{3})\s+-->\s+"
    r"(?P<end>\d+:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?$"
)
_PURPOSE_BY_OPERATION = {
    "subtitle_list": "subtitle_inventory",
    "metadata_probe": "metadata_probe",
    "format_list": "format_inventory",
    "format_inventory": "format_inventory",
    "subtitle_manual": "download",
    "subtitle_automatic": "download",
    "thumbnail_download": "download",
    "media_download": "download",
    "media_probe": "technical_probe",
}
_VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


class ContractValidator(Protocol):
    def validate(self, schema_name: str, instance: Any) -> None: ...


@dataclass(frozen=True)
class ToolVersion:
    name: str
    version: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name
            or self.name.strip() != self.name
            or len(self.name) > 128
        ):
            raise ContractError("Source provider tool name is invalid")
        if (
            not isinstance(self.version, str)
            or not self.version
            or self.version.strip() != self.version
            or len(self.version) > 256
        ):
            raise ContractError("Source provider tool version is invalid")


@dataclass(frozen=True)
class SourceProviderBinding:
    kind: Literal["live", "recorded_fixture", "verified_import"]
    recording_sha256: str | None
    tool_versions: tuple[ToolVersion, ...]
    recording_platform: Literal["bilibili", "youtube"] | None = None
    recording_adapter_id: str | None = None
    recording_adapter_contract_version: str | None = None
    recording_evidence: RecordedProviderEvidence | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"live", "recorded_fixture", "verified_import"}:
            raise ContractError("Source provider kind is unsupported")
        if not self.tool_versions:
            raise ContractError("Source provider requires at least one tool version")
        if any(not isinstance(item, ToolVersion) for item in self.tool_versions):
            raise ContractError("Source provider tool versions are invalid")
        names = [item.name for item in self.tool_versions]
        if len(names) != len(set(names)):
            raise ContractError("Source provider repeats a tool identity")
        if self.kind == "recorded_fixture":
            if self.recording_sha256 is None or _SHA256.fullmatch(
                self.recording_sha256
            ) is None:
                raise ContractError("Recorded provider requires its recording SHA-256")
            if self.recording_platform not in {"bilibili", "youtube"}:
                raise ContractError("Recorded provider requires its canonical platform")
            if (
                not isinstance(self.recording_adapter_id, str)
                or not self.recording_adapter_id
                or self.recording_adapter_contract_version != "1.0.0"
            ):
                raise ContractError("Recorded provider requires its Adapter contract")
            if not isinstance(self.recording_evidence, RecordedProviderEvidence):
                raise ContractError("Recorded provider requires actual runner evidence")
            evidence = self.recording_evidence
            if self.recording_sha256 != evidence.manifest_sha256:
                raise ContractError(
                    "Recorded provider declared recording SHA-256 differs from runner evidence"
                )
            if self.recording_platform != evidence.canonical_platform:
                raise ContractError(
                    "Recorded provider declared platform differs from runner evidence"
                )
            if (
                self.recording_adapter_id != evidence.adapter_id
                or self.recording_adapter_contract_version
                != evidence.adapter_contract_version
            ):
                raise ContractError(
                    "Recorded provider declared Adapter contract differs from runner evidence"
                )
        elif any(
            value is not None
            for value in (
                self.recording_sha256,
                self.recording_platform,
                self.recording_adapter_id,
                self.recording_adapter_contract_version,
                self.recording_evidence,
            )
        ):
            raise ContractError("Non-recorded provider cannot claim recording evidence")


@dataclass(frozen=True)
class SourceCandidatePolicy:
    content_classification: Literal["general", "language_learning"]
    subtitle_language_priority: tuple[str, ...]
    whisper_allowed: bool
    policy_id: str = "source-acquisition-policy"
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.policy_id != "source-acquisition-policy" or self.version != "1.0.0":
            raise ContractError("Source Candidate policy identity is unsupported")
        if self.content_classification not in {"general", "language_learning"}:
            raise ContractError("Source Candidate content classification is unsupported")
        if not isinstance(self.whisper_allowed, bool):
            raise ContractError("Source Candidate Whisper policy must be boolean")
        priorities = self.subtitle_language_priority
        if (
            not priorities
            or any(
                not isinstance(value, str)
                or not value
                or value.strip() != value
                or len(value) > 64
                for value in priorities
            )
            or len(priorities) != len(set(priorities))
        ):
            raise ContractError("Subtitle language priority is invalid")
        if self.content_classification == "language_learning" and priorities[0] != "en":
            raise ContractError("Language-learning Source policy must prioritize English")

    def binding(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "policy_id": self.policy_id,
            "version": self.version,
            "content_classification": self.content_classification,
            "subtitle_language_priority": list(self.subtitle_language_priority),
            "whisper_allowed": self.whisper_allowed,
        }
        return {"sha256": sha256_bytes(canonical_json_bytes(value)), **value}


@dataclass(frozen=True)
class SourceCandidateMaterialization:
    inventory_path: Path
    inventory_sha256: str
    inventory: dict[str, Any]
    skeleton_path: Path | None
    skeleton_sha256: str | None
    skeleton: dict[str, Any] | None


@dataclass(frozen=True)
class _CandidateSource:
    role: str
    relative_path: PurePosixPath
    source_path: Path
    media_type: str
    sha256: str
    size_bytes: int
    language: str | None
    subtitle_kind: str | None
    technical_probe: dict[str, Any]
    canonical_bytes: bytes | None = None


def _validate_declared_artifact(
    artifact: StagedArtifact, root: Path, *, purpose: str
) -> tuple[Path, str, int]:
    if not isinstance(artifact, StagedArtifact):
        raise ContractError(f"{purpose} artifact binding is invalid")
    path = require_contained_path(
        artifact.path,
        root,
        purpose=purpose,
        error_type=ContractError,
        leaf_kind="file",
        require_single_link=True,
    )
    actual_size = path.stat().st_size
    actual_sha256 = sha256_file(path)
    if actual_size <= 0:
        raise ContractError(f"{purpose} source is empty")
    if artifact.size_bytes != actual_size or artifact.sha256 != actual_sha256:
        raise ArtifactDrift(f"{purpose} source differs from Adapter evidence")
    return path, actual_sha256, actual_size


def _canonical_json_source(path: Path, root: Path, *, purpose: str) -> tuple[Path, bytes, Any]:
    resolved = require_contained_path(
        path,
        root,
        purpose=purpose,
        error_type=ContractError,
        leaf_kind="file",
        require_single_link=True,
    )
    try:
        value = read_json(resolved)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{purpose} source is not valid JSON") from exc
    data = canonical_json_bytes(value)
    if not data:
        raise ContractError(f"{purpose} source is empty")
    return resolved, data, value


def _positive_number(value: Any, *, purpose: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{purpose} duration is invalid") from exc
    if not math.isfinite(number) or number <= 0:
        raise ContractError(f"{purpose} duration is invalid")
    return number


def _media_technical_probe(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("Media technical probe root is invalid")
    format_value = value.get("format")
    streams_value = value.get("streams")
    if not isinstance(format_value, Mapping) or not isinstance(streams_value, list):
        raise ContractError("Media technical probe structure is invalid")
    duration = _positive_number(format_value.get("duration"), purpose="media probe")
    stream_types: list[str] = []
    codec_names: list[str] = []
    for stream in streams_value:
        if not isinstance(stream, Mapping):
            raise ContractError("Media technical probe stream is invalid")
        stream_type = stream.get("codec_type")
        codec_name = stream.get("codec_name")
        if stream_type not in {"video", "audio"}:
            continue
        if not isinstance(codec_name, str) or not codec_name.strip():
            raise ContractError("Media technical probe codec is invalid")
        if stream_type not in stream_types:
            stream_types.append(stream_type)
        if codec_name not in codec_names:
            codec_names.append(codec_name)
    if "video" not in stream_types:
        raise ContractError("Media technical probe lacks a video stream")
    return {
        "status": "pass",
        "duration_seconds": duration,
        "stream_types": stream_types,
        "codec_names": codec_names,
    }


def _srt_seconds(value: str) -> float:
    clock, milliseconds = re.split(r"[,.]", value, maxsplit=1)
    hours, minutes, seconds = (int(part) for part in clock.split(":"))
    if minutes >= 60 or seconds >= 60:
        raise ContractError("Subtitle timestamp clock is invalid")
    return hours * 3600 + minutes * 60 + seconds + int(milliseconds) / 1000


def _subtitle_technical_probe(path: Path, video_duration: float) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise ContractError("Subtitle Candidate is not valid UTF-8 text") from exc
    if "\x00" in text:
        raise ContractError("Subtitle Candidate contains binary data")
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip()) if text.strip() else []
    if not blocks:
        raise ContractError("Subtitle Candidate contains no cues")
    previous_start = -1.0
    last_end = 0.0
    tolerance = max(2.0, video_duration * 0.02)
    for block in blocks:
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if len(lines) < 2:
            raise ContractError("Subtitle Candidate cue is incomplete")
        match = _SRT_TIMESTAMP.fullmatch(lines[0].strip())
        if match is None or not any(line.strip() for line in lines[1:]):
            raise ContractError("Subtitle Candidate cue structure is invalid")
        start = _srt_seconds(match.group("start"))
        end = _srt_seconds(match.group("end"))
        if start < previous_start or end <= start:
            raise ContractError("Subtitle Candidate timeline is invalid")
        if end > video_duration + tolerance:
            raise ContractError("Subtitle Candidate timeline exceeds the video duration")
        previous_start = start
        last_end = max(last_end, end)
    return {
        "status": "pass",
        "duration_seconds": last_end,
        "stream_types": ["subtitle"],
        "codec_names": ["subrip"],
    }


def _staged_path(
    relative: PurePosixPath, *, source_epoch: int | None = None
) -> str:
    root = (
        CANDIDATE_ROOT
        if source_epoch is None
        else CANDIDATE_ROOT / f"e{source_epoch}"
    )
    return (root / relative).as_posix()


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    identity = {
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
    return sha256_bytes(canonical_json_bytes(identity))


def _safe_command_argv(evidence: CommandEvidence) -> list[str]:
    if not evidence.argv or any(
        not isinstance(value, str) or not value for value in evidence.argv
    ):
        raise ContractError("Source command evidence argv is invalid")
    expected_argv_sha = hashlib.sha256(
        "\0".join(evidence.argv).encode("utf-8")
    ).hexdigest()
    if evidence.argv_sha256 != expected_argv_sha:
        raise ArtifactDrift("Source command argv evidence fingerprint differs")
    if any(
        _SHA256.fullmatch(value) is None
        for value in (evidence.stdout_sha256, evidence.stderr_sha256)
    ):
        raise ContractError("Source command log fingerprint is invalid")
    if evidence.returncode != 0:
        raise ContractError("Failed Source command cannot enter a ready inventory")

    sanitized = list(evidence.argv)
    for index, value in enumerate(sanitized):
        lowered = value.casefold()
        if "\r" in value or "\n" in value or _AUTH_HEADER.search(value):
            raise ContractError("Source command evidence contains authentication material")
        if _QUERY_SECRET.search(value) and "<redacted>" not in lowered:
            raise ContractError("Source command evidence contains a sensitive query value")
        if lowered.startswith("--cookies="):
            supplied = value.split("=", 1)[1]
            if supplied not in {"<localized-cookie-file>", "<COOKIE_FILE>"}:
                raise ContractError("Source command cookie argument is not redacted")
            sanitized[index] = "--cookies=<localized-cookie-file>"
        if lowered == "--cookies":
            if index + 1 >= len(sanitized) or sanitized[index + 1] not in {
                "<localized-cookie-file>",
                "<COOKIE_FILE>",
            }:
                raise ContractError("Source command cookie argument is not redacted")
            sanitized[index + 1] = "<localized-cookie-file>"
        if lowered.startswith("--cookies-from-browser"):
            raise ContractError("Browser cookie extraction is outside the Adapter contract")
    return sanitized


def _command_id(operation: str, counts: dict[str, int]) -> str:
    base = _SAFE_OPERATION.sub("-", operation.casefold().replace("_", "-")).strip("-")
    if not base:
        base = "command"
    base = base[:60].rstrip("-")
    counts[base] = counts.get(base, 0) + 1
    suffix = "" if counts[base] == 1 else f"-{counts[base]}"
    return f"{base[:64 - len(suffix)].rstrip('-')}{suffix}"


def _commands(
    evidence_values: Sequence[CommandEvidence], *, mode: str
) -> list[dict[str, Any]]:
    if not evidence_values:
        raise ContractError("Source Candidate Inventory requires command evidence")
    counts: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for evidence in evidence_values:
        if not isinstance(evidence, CommandEvidence) or not evidence.operation:
            raise ContractError("Source command evidence is invalid")
        if mode == "verified_import" and evidence.operation != "verified_import_validation":
            raise ContractError(
                "Verified Import requires dedicated package-validation command evidence"
            )
        argv = _safe_command_argv(evidence)
        purpose = (
            "verified_import_validation"
            if mode == "verified_import"
            else _PURPOSE_BY_OPERATION.get(evidence.operation)
        )
        if purpose is None:
            raise ContractError("Source command operation has no closed purpose mapping")
        result.append(
            {
                "command_id": _command_id(evidence.operation, counts),
                "purpose": purpose,
                "command_argv_redacted": argv,
                "exit_classification": "success",
                "sanitized_log_sha256": sha256_bytes(
                    canonical_json_bytes(
                        {
                            "stdout_sha256": evidence.stdout_sha256,
                            "stderr_sha256": evidence.stderr_sha256,
                        }
                    )
                ),
            }
        )
    return result


def _target_path(run_dir: Path, relative: PurePosixPath) -> Path:
    target = Path(os.path.abspath(run_dir.joinpath(*relative.parts)))
    return require_contained_path(
        target,
        run_dir,
        purpose="Source Candidate target",
        error_type=ContractError,
        allow_missing=True,
    )


def _preflight_target(
    target: Path,
    *,
    boundary: Path,
    expected_sha256: str,
    expected_size: int,
    purpose: str,
) -> None:
    require_contained_path(
        target,
        boundary,
        purpose=purpose,
        error_type=ArtifactDrift,
        leaf_kind="file",
        allow_missing=True,
        require_single_link=True,
    )
    if not target.exists():
        return
    if target.stat().st_size != expected_size or sha256_file(target) != expected_sha256:
        raise ArtifactDrift(f"{purpose} target contains different content")


def _preflight_json_target(
    target: Path, value: Mapping[str, Any], *, boundary: Path, purpose: str
) -> str:
    data = canonical_json_bytes(value)
    digest = sha256_bytes(data)
    _preflight_target(
        target,
        boundary=boundary,
        expected_sha256=digest,
        expected_size=len(data),
        purpose=purpose,
    )
    require_contained_path(
        target.with_name(f".{target.name}.kernel-new"),
        boundary,
        purpose=f"{purpose} atomic staging",
        error_type=ArtifactDrift,
        leaf_kind="file",
        allow_missing=True,
        require_single_link=True,
    )
    return digest


def _write_candidate(source: _CandidateSource, target: Path) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.canonical_bytes is not None:
        target.write_bytes(source.canonical_bytes)
    else:
        shutil.copy2(source.source_path, target)
    require_contained_path(
        target,
        target.parent,
        purpose="Source Candidate publication",
        error_type=ArtifactDrift,
        leaf_kind="file",
        require_single_link=True,
    )
    if target.stat().st_size != source.size_bytes or sha256_file(target) != source.sha256:
        raise ArtifactDrift("Source Candidate publication fingerprint differs")


def _validate_invocation(
    *,
    run_id: str,
    source_epoch: int,
    acquisition_id: str,
    mode: str,
    task_id: str | None,
    inventory_generation: int,
    provider: SourceProviderBinding,
    import_binding: Mapping[str, Any] | None,
) -> None:
    if _ID32.fullmatch(run_id) is None or _ID32.fullmatch(acquisition_id) is None:
        raise ContractError("Source Candidate run or acquisition identity is invalid")
    if (
        isinstance(source_epoch, bool)
        or not isinstance(source_epoch, int)
        or source_epoch < 1
        or isinstance(inventory_generation, bool)
        or not isinstance(inventory_generation, int)
        or inventory_generation < 1
    ):
        raise ContractError("Source Candidate epoch or generation is invalid")
    if mode == "fresh_download":
        if task_id is None or _ID32.fullmatch(task_id) is None:
            raise ContractError("Fresh Source Candidate preparation requires a semantic task")
        if provider.kind == "verified_import" or import_binding is not None:
            raise ContractError("Fresh Source Candidate preparation contains import authority")
    elif mode == "verified_import":
        if task_id is not None:
            raise ContractError("Verified Import cannot create a semantic decision skeleton")
        if provider.kind != "verified_import" or import_binding is None:
            raise ContractError("Verified Import requires its validation binding")
    else:
        raise ContractError("Source Acquisition Mode is unsupported")


def _validate_recorded_runner_evidence(
    *,
    provider: SourceProviderBinding,
    probe: PlatformProbe,
    acquisition: PlatformAcquisition,
) -> None:
    if provider.kind != "recorded_fixture":
        return
    binding = provider.recording_evidence
    if binding is None:
        raise ContractError("Recorded provider runner evidence is unavailable")
    if (
        probe.canonical_platform != provider.recording_platform
        or probe.adapter_id != provider.recording_adapter_id
    ):
        raise ContractError(
            "Recorded provider platform or Adapter differs from Probe evidence"
        )
    command_evidence = (*probe.command_evidence, *acquisition.command_evidence)
    if any(item.recorded_provider != binding for item in command_evidence):
        raise ContractError(
            "Recorded command evidence differs from provider binding"
        )
    if len(command_evidence) != binding.command_count:
        raise ContractError("Recorded command sequence is incomplete")
    if (
        command_evidence_sequence_sha256(list(command_evidence))
        != binding.command_sequence_sha256
    ):
        raise ContractError("Recorded command sequence differs from its manifest")


def _candidate_sources(
    probe: PlatformProbe, acquisition: PlatformAcquisition
) -> list[_CandidateSource]:
    if acquisition.probe != probe:
        raise ContractError("Platform Acquisition does not bind the supplied probe")
    metadata_path = probe.normalized_metadata_path
    if len(metadata_path.parents) < 2:
        raise ContractError("Platform metadata path has no canonical staging root")
    canonical_root = metadata_path.parents[1]
    metadata_source, metadata_bytes, metadata_value = _canonical_json_source(
        metadata_path, canonical_root, purpose="platform metadata"
    )
    if not isinstance(metadata_value, Mapping):
        raise ContractError("Platform metadata root is invalid")
    expected_metadata = {
        "adapter_id": probe.adapter_id,
        "canonical_platform": probe.canonical_platform,
        "canonical_item_id": probe.canonical_item_id,
        "canonical_url": probe.canonical_url,
        "original_title": probe.original_title,
    }
    if any(metadata_value.get(key) != value for key, value in expected_metadata.items()):
        raise ContractError("Platform metadata differs from Probe authority")
    if _positive_number(
        metadata_value.get("duration_seconds"), purpose="platform metadata"
    ) != _positive_number(probe.duration_seconds, purpose="Source metadata"):
        raise ContractError("Platform metadata duration differs from Probe authority")

    media_probe_source, _, _ = _validate_declared_artifact(
        acquisition.media_probe, canonical_root, purpose="media probe"
    )
    try:
        media_probe_value = read_json(media_probe_source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("Media technical probe is not valid JSON") from exc
    media_probe_bytes = canonical_json_bytes(media_probe_value)
    media_technical = _media_technical_probe(media_probe_value)

    cover_source, cover_sha256, cover_size = _validate_declared_artifact(
        acquisition.cover, canonical_root, purpose="cover"
    )
    video_source, video_sha256, video_size = _validate_declared_artifact(
        acquisition.video, canonical_root, purpose="video"
    )
    video_extension = video_source.suffix.casefold()
    expected_media_type = _VIDEO_MEDIA_TYPES.get(video_extension)
    if expected_media_type is None or acquisition.video.media_type != expected_media_type:
        raise ContractError("Video Candidate extension and media type are unsupported")
    if acquisition.cover.media_type != "image/jpeg":
        raise ContractError("Cover Candidate media type is unsupported")

    duration = _positive_number(probe.duration_seconds, purpose="Source metadata")
    sources = [
        _CandidateSource(
            role="metadata",
            relative_path=PurePosixPath("metadata/platform.json"),
            source_path=metadata_source,
            media_type="application/json",
            sha256=sha256_bytes(metadata_bytes),
            size_bytes=len(metadata_bytes),
            language=None,
            subtitle_kind=None,
            technical_probe={
                "status": "pass",
                "duration_seconds": None,
                "stream_types": ["metadata"],
                "codec_names": ["json"],
            },
            canonical_bytes=metadata_bytes,
        ),
        _CandidateSource(
            role="metadata",
            relative_path=PurePosixPath("metadata/media-probe.json"),
            source_path=media_probe_source,
            media_type="application/json",
            sha256=sha256_bytes(media_probe_bytes),
            size_bytes=len(media_probe_bytes),
            language=None,
            subtitle_kind=None,
            technical_probe={
                "status": "pass",
                "duration_seconds": None,
                "stream_types": ["metadata"],
                "codec_names": ["json"],
            },
            canonical_bytes=media_probe_bytes,
        ),
        _CandidateSource(
            role="cover",
            relative_path=PurePosixPath("cover/cover.jpg"),
            source_path=cover_source,
            media_type=acquisition.cover.media_type,
            sha256=cover_sha256,
            size_bytes=cover_size,
            language=None,
            subtitle_kind=None,
            technical_probe={
                "status": "pass",
                "duration_seconds": None,
                "stream_types": ["image"],
                "codec_names": ["jpeg"],
            },
        ),
        _CandidateSource(
            role="video",
            relative_path=PurePosixPath(f"media/video{video_extension}"),
            source_path=video_source,
            media_type=acquisition.video.media_type,
            sha256=video_sha256,
            size_bytes=video_size,
            language=None,
            subtitle_kind=None,
            technical_probe=media_technical,
        ),
    ]

    tracks = {track.track_id: track for track in probe.subtitle_tracks}
    if len(tracks) != len(probe.subtitle_tracks):
        raise ContractError("Platform Probe repeats a subtitle track identity")
    subtitle_values: list[
        tuple[str, str, str, StagedArtifact, Path, str, int]
    ] = []
    seen_artifacts: set[str] = set()
    for artifact in acquisition.subtitle_candidates:
        if artifact.logical_id in seen_artifacts or artifact.logical_id not in tracks:
            raise ContractError("Subtitle Candidate does not bind one unique Probe track")
        seen_artifacts.add(artifact.logical_id)
        track = tracks[artifact.logical_id]
        source, source_sha256, source_size = _validate_declared_artifact(
            artifact, canonical_root, purpose="subtitle"
        )
        if (
            artifact.media_type != "application/x-subrip"
            or source.suffix.casefold() != ".srt"
        ):
            raise ContractError("Subtitle Candidate format is unsupported")
        subtitle_values.append(
            (
                track.origin,
                track.normalized_language,
                track.track_id,
                artifact,
                source,
                source_sha256,
                source_size,
            )
        )
    subtitle_values.sort(
        key=lambda item: (0 if item[0] == "manual" else 1, item[1], item[2])
    )
    for (
        origin,
        language,
        _,
        artifact,
        source,
        source_sha256,
        source_size,
    ) in subtitle_values:
        relative = PurePosixPath(f"subtitles/subtitle.{language}.{origin}.srt")
        sources.append(
            _CandidateSource(
                role="subtitle",
                relative_path=relative,
                source_path=source,
                media_type=artifact.media_type,
                sha256=source_sha256,
                size_bytes=source_size,
                language=language,
                subtitle_kind=origin,
                technical_probe=_subtitle_technical_probe(source, duration),
            )
        )

    paths = [_staged_path(source.relative_path) for source in sources]
    if len(paths) != len(set(paths)):
        raise ContractError("Source Candidate canonical paths collide")
    return sources


def materialize_source_candidates(
    run_dir: Path,
    *,
    run_id: str,
    source_epoch: int,
    acquisition_id: str,
    mode: Literal["fresh_download", "verified_import"],
    probe: PlatformProbe,
    acquisition: PlatformAcquisition,
    provider: SourceProviderBinding,
    policy: SourceCandidatePolicy,
    task_id: str | None,
    contracts: ContractValidator,
    inventory_generation: int = 1,
    import_binding: Mapping[str, Any] | None = None,
) -> SourceCandidateMaterialization:
    """Materialize the deterministic mechanical boundary before semantic judgment."""

    if not isinstance(run_dir, Path) or not isinstance(provider, SourceProviderBinding):
        raise ContractError("Source Candidate invocation boundary is invalid")
    if not isinstance(policy, SourceCandidatePolicy) or not hasattr(contracts, "validate"):
        raise ContractError("Source Candidate policy or contract authority is invalid")
    _validate_invocation(
        run_id=run_id,
        source_epoch=source_epoch,
        acquisition_id=acquisition_id,
        mode=mode,
        task_id=task_id,
        inventory_generation=inventory_generation,
        provider=provider,
        import_binding=import_binding,
    )
    if not isinstance(probe, PlatformProbe) or not isinstance(
        acquisition, PlatformAcquisition
    ):
        raise ContractError("Source Candidate Adapter evidence is invalid")
    if probe.canonical_platform not in {"bilibili", "youtube"}:
        raise ContractError("Source Candidate platform is unsupported")
    if (
        not probe.original_title
        or probe.original_title.strip() != probe.original_title
        or not probe.canonical_item_id
        or probe.canonical_item_id.strip() != probe.canonical_item_id
    ):
        raise ContractError("Source Candidate metadata identity is invalid")

    _validate_recorded_runner_evidence(
        provider=provider,
        probe=probe,
        acquisition=acquisition,
    )
    sources = _candidate_sources(probe, acquisition)
    evidence_values = (*probe.command_evidence, *acquisition.command_evidence)
    command_values = _commands(evidence_values, mode=mode)
    source_identity = derive_source_identity(
        probe.canonical_platform, probe.canonical_item_id
    )
    policy_binding = policy.binding()
    origin = "verified_import" if mode == "verified_import" else "platform_download"
    candidates: list[dict[str, Any]] = []
    for source in sources:
        candidate: dict[str, Any] = {
            "role": source.role,
            "staged_path": _staged_path(
                source.relative_path, source_epoch=source_epoch
            ),
            "media_type": source.media_type,
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
            "origin": origin,
            "language": source.language,
            "subtitle_kind": source.subtitle_kind,
            "technical_probe": source.technical_probe,
        }
        candidate["candidate_id"] = _candidate_id(candidate)
        candidates.append({"candidate_id": candidate.pop("candidate_id"), **candidate})
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ContractError("Source Candidate mechanical identities collide")

    safe_import_binding: Any = None
    if import_binding is not None:
        try:
            safe_import_binding = json.loads(
                json.dumps(import_binding, ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError) as exc:
            raise ContractError("Verified Import binding is not canonical JSON") from exc
    inventory: dict[str, Any] = {
        "schema_name": "source-candidate-inventory",
        "schema_version": "1.0.0",
        "kernel_version": KERNEL_VERSION,
        "run_id": run_id,
        "acquisition_id": acquisition_id,
        "source_epoch": source_epoch,
        "mode": mode,
        "adapter": {"id": probe.canonical_platform, "contract_version": "1.0.0"},
        "canonical_platform": probe.canonical_platform,
        "canonical_item_id": probe.canonical_item_id,
        "source_identity_scheme": SOURCE_IDENTITY_SCHEME,
        "source_identity": source_identity,
        "provider": {
            "kind": provider.kind,
            "recording_sha256": provider.recording_sha256,
            "tool_versions": [
                {"name": item.name, "version": item.version}
                for item in sorted(provider.tool_versions, key=lambda item: item.name)
            ],
        },
        "authentication_classification": (
            "not_applicable"
            if mode == "verified_import"
            else probe.authentication_classification
        ),
        "policy_binding": policy_binding,
        "source_metadata": {
            "original_title": probe.original_title,
            "duration_seconds": _positive_number(
                probe.duration_seconds, purpose="Source metadata"
            ),
        },
        "commands": command_values,
        "candidates": candidates,
        "import_binding": safe_import_binding,
        "status": "candidates_ready",
    }
    contracts.validate("source-candidate-inventory", inventory)

    run_dir = Path(os.path.abspath(run_dir))
    inventory_path = _target_path(run_dir, INVENTORY_PATH)
    inventory_sha256 = _preflight_json_target(
        inventory_path,
        inventory,
        boundary=run_dir,
        purpose="Source Candidate Inventory",
    )
    skeleton: dict[str, Any] | None = None
    skeleton_path: Path | None = None
    skeleton_sha256: str | None = None
    if mode == "fresh_download":
        video_candidate = next(
            candidate for candidate in candidates if candidate["role"] == "video"
        )
        has_audio = "audio" in video_candidate["technical_probe"]["stream_types"]
        whisper_audio_candidate_id = (
            video_candidate["candidate_id"]
            if policy.whisper_allowed and has_audio
            else None
        )
        allowed = build_allowed_source_judgment(
            [
                SubtitleCandidate(
                    candidate_id=candidate["candidate_id"],
                    language=candidate["language"],
                    subtitle_kind=candidate["subtitle_kind"],
                    technically_usable=candidate["technical_probe"]["status"] == "pass",
                )
                for candidate in candidates
                if candidate["role"] == "subtitle"
            ],
            english_primary=policy.content_classification == "language_learning",
            whisper_allowed=policy.whisper_allowed,
            whisper_audio_candidate_id=whisper_audio_candidate_id,
        )
        skeleton = {
            "schema_name": "source-acquisition-decision-skeleton",
            "schema_version": "1.0.0",
            "kernel_version": KERNEL_VERSION,
            "run_id": run_id,
            "source_epoch": source_epoch,
            "acquisition_id": acquisition_id,
            "task_id": task_id,
            "source_identity": source_identity,
            "candidate_inventory": {
                "path": INVENTORY_PATH.as_posix(),
                "generation": inventory_generation,
                "sha256": inventory_sha256,
            },
            "policy_binding": {
                "policy_id": policy.policy_id,
                "version": policy.version,
                "sha256": policy_binding["sha256"],
            },
            "allowed_judgment": allowed,
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
        contracts.validate("source-acquisition-decision-skeleton", skeleton)
        skeleton_path = _target_path(run_dir, DECISION_SKELETON_PATH)
        skeleton_sha256 = _preflight_json_target(
            skeleton_path,
            skeleton,
            boundary=run_dir,
            purpose="Source Acquisition Decision Skeleton",
        )
    else:
        unexpected_skeleton = _target_path(run_dir, DECISION_SKELETON_PATH)
        if unexpected_skeleton.exists() or unexpected_skeleton.is_symlink():
            raise ArtifactDrift("Verified Import run contains a decision skeleton")

    candidate_targets: list[tuple[_CandidateSource, Path]] = []
    for source in sources:
        target = _target_path(
            run_dir,
            CANDIDATE_ROOT / f"e{source_epoch}" / source.relative_path,
        )
        _preflight_target(
            target,
            boundary=run_dir,
            expected_sha256=source.sha256,
            expected_size=source.size_bytes,
            purpose="Source Candidate",
        )
        candidate_targets.append((source, target))

    for source, target in candidate_targets:
        _write_candidate(source, target)
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    written_inventory_sha = write_json_atomic(inventory_path, inventory)
    if written_inventory_sha != inventory_sha256:
        raise ArtifactDrift("Source Candidate Inventory write fingerprint differs")
    if skeleton is not None and skeleton_path is not None and skeleton_sha256 is not None:
        written_skeleton_sha = write_json_atomic(skeleton_path, skeleton)
        if written_skeleton_sha != skeleton_sha256:
            raise ArtifactDrift("Decision Skeleton write fingerprint differs")

    return SourceCandidateMaterialization(
        inventory_path=inventory_path,
        inventory_sha256=inventory_sha256,
        inventory=inventory,
        skeleton_path=skeleton_path,
        skeleton_sha256=skeleton_sha256,
        skeleton=skeleton,
    )


def materialize_verified_import_candidates(
    run_dir: Path,
    *,
    run_id: str,
    source_epoch: int,
    acquisition_id: str,
    prior_run_dir: Path,
    prior_manifest: Mapping[str, Any],
    provider: SourceProviderBinding,
    policy: SourceCandidatePolicy,
    import_binding: Mapping[str, Any],
    validation_command: CommandEvidence,
    contracts: ContractValidator,
    inventory_generation: int = 1,
) -> SourceCandidateMaterialization:
    """Copy a validated prior package into the verified-import candidate boundary."""

    _validate_invocation(
        run_id=run_id,
        source_epoch=source_epoch,
        acquisition_id=acquisition_id,
        mode="verified_import",
        task_id=None,
        inventory_generation=inventory_generation,
        provider=provider,
        import_binding=import_binding,
    )
    if not isinstance(policy, SourceCandidatePolicy) or not hasattr(
        contracts, "validate"
    ):
        raise ContractError("Verified Import policy or contract authority is invalid")
    if not isinstance(prior_manifest, Mapping):
        raise ContractError("Verified Import prior Manifest is invalid")
    contracts.validate("source-manifest", dict(prior_manifest))
    if prior_manifest.get("schema_version") != "2.0.0":
        raise ContractError("Verified Import requires Source Manifest v2")
    if import_binding.get("prior_run_id") != prior_manifest.get("run_id"):
        raise ContractError("Verified Import prior Run binding differs from Manifest")
    source_identity = derive_source_identity(
        str(prior_manifest["canonical_platform"]),
        str(prior_manifest["canonical_item_id"]),
    )
    if (
        prior_manifest["source_identity"] != source_identity
        or import_binding.get("prior_source_identity") != source_identity
        or import_binding.get("prior_source_version")
        != prior_manifest["source_version"]
    ):
        raise ArtifactDrift("Verified Import identity or version binding is stale")

    prior_run_dir = Path(os.path.abspath(prior_run_dir))
    sources: list[_CandidateSource] = []
    for artifact in prior_manifest["artifacts"]:
        artifact_path = PurePosixPath(artifact["path"])
        try:
            relative = artifact_path.relative_to("source")
        except ValueError as exc:
            raise ContractError(
                "Verified Import artifact is outside the prior Source Package"
            ) from exc
        source_path = require_contained_path(
            prior_run_dir.joinpath(*artifact_path.parts),
            prior_run_dir,
            purpose="Verified Import artifact",
            error_type=ContractError,
            leaf_kind="file",
            require_single_link=True,
        )
        actual_sha256 = sha256_file(source_path)
        actual_size = source_path.stat().st_size
        if (
            actual_size <= 0
            or actual_size != artifact["size_bytes"]
            or actual_sha256 != artifact["sha256"]
        ):
            raise ArtifactDrift("Verified Import artifact fingerprint drifted")
        sources.append(
            _CandidateSource(
                role=artifact["role"],
                relative_path=relative,
                source_path=source_path,
                media_type=artifact["media_type"],
                sha256=actual_sha256,
                size_bytes=actual_size,
                language=artifact["language"],
                subtitle_kind=artifact["subtitle_kind"],
                technical_probe=dict(artifact["technical_probe"]),
            )
        )
    staged_paths = [_staged_path(source.relative_path) for source in sources]
    if len(staged_paths) != len(set(staged_paths)):
        raise ContractError("Verified Import canonical Candidate paths collide")

    candidates: list[dict[str, Any]] = []
    for source in sources:
        candidate: dict[str, Any] = {
            "role": source.role,
            "staged_path": _staged_path(source.relative_path),
            "media_type": source.media_type,
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
            "origin": "verified_import",
            "language": source.language,
            "subtitle_kind": source.subtitle_kind,
            "technical_probe": source.technical_probe,
        }
        candidate_id = _candidate_id(candidate)
        candidates.append({"candidate_id": candidate_id, **candidate})
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ContractError("Verified Import Candidate identities collide")

    try:
        safe_import_binding = json.loads(
            json.dumps(import_binding, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise ContractError("Verified Import binding is not canonical JSON") from exc
    policy_binding = policy.binding()
    inventory = {
        "schema_name": "source-candidate-inventory",
        "schema_version": "1.0.0",
        "kernel_version": KERNEL_VERSION,
        "run_id": run_id,
        "acquisition_id": acquisition_id,
        "source_epoch": source_epoch,
        "mode": "verified_import",
        "adapter": {
            "id": prior_manifest["canonical_platform"],
            "contract_version": "1.0.0",
        },
        "canonical_platform": prior_manifest["canonical_platform"],
        "canonical_item_id": prior_manifest["canonical_item_id"],
        "source_identity_scheme": SOURCE_IDENTITY_SCHEME,
        "source_identity": source_identity,
        "provider": {
            "kind": provider.kind,
            "recording_sha256": provider.recording_sha256,
            "tool_versions": [
                {"name": item.name, "version": item.version}
                for item in sorted(provider.tool_versions, key=lambda item: item.name)
            ],
        },
        "authentication_classification": "not_applicable",
        "policy_binding": policy_binding,
        "source_metadata": {
            "original_title": prior_manifest["original_title"],
            "duration_seconds": _positive_number(
                prior_manifest["technical_validation"]["duration_seconds"],
                purpose="Verified Import Source metadata",
            ),
        },
        "commands": _commands((validation_command,), mode="verified_import"),
        "candidates": candidates,
        "import_binding": safe_import_binding,
        "status": "candidates_ready",
    }
    contracts.validate("source-candidate-inventory", inventory)

    run_dir = Path(os.path.abspath(run_dir))
    unexpected_skeleton = _target_path(run_dir, DECISION_SKELETON_PATH)
    if unexpected_skeleton.exists() or unexpected_skeleton.is_symlink():
        raise ArtifactDrift("Verified Import run contains a decision skeleton")
    inventory_path = _target_path(run_dir, INVENTORY_PATH)
    inventory_sha256 = _preflight_json_target(
        inventory_path,
        inventory,
        boundary=run_dir,
        purpose="Verified Import Candidate Inventory",
    )
    candidate_targets: list[tuple[_CandidateSource, Path]] = []
    for source in sources:
        target = _target_path(run_dir, CANDIDATE_ROOT / source.relative_path)
        _preflight_target(
            target,
            boundary=run_dir,
            expected_sha256=source.sha256,
            expected_size=source.size_bytes,
            purpose="Verified Import Candidate",
        )
        candidate_targets.append((source, target))
    for source, target in candidate_targets:
        _write_candidate(source, target)
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    if write_json_atomic(inventory_path, inventory) != inventory_sha256:
        raise ArtifactDrift("Verified Import Candidate Inventory write drifted")
    return SourceCandidateMaterialization(
        inventory_path=inventory_path,
        inventory_sha256=inventory_sha256,
        inventory=inventory,
        skeleton_path=None,
        skeleton_sha256=None,
        skeleton=None,
    )

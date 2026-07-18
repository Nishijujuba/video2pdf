from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Callable, Mapping, Protocol

from .contracts import ContractRegistry
from .errors import ContractError
from .source_acquisition import derive_source_identity, validate_source_judgment
from .utils import (
    canonical_json_bytes,
    require_contained_path,
    sha256_bytes,
)


INVENTORY_PATH = "work/source-acquisition/candidate-inventory.json"
DECISION_SKELETON_PATH = "work/source-acquisition/decision.skeleton.json"
JUDGMENT_PATCH_PATH = "workflow/source-acquisition-judgment-patch.json"
SOURCE_VERSION_CANONICALIZATION = "video2pdf-canonical-json-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_EXTENSIONS = {
    ("metadata", "application/json"): ".json",
    ("cover", "image/jpeg"): ".jpg",
    ("cover", "image/png"): ".png",
    ("cover", "image/webp"): ".webp",
    ("video", "video/mp4"): ".mp4",
    ("video", "video/x-matroska"): ".mkv",
    ("video", "video/webm"): ".webm",
    ("audio", "audio/mp4"): ".m4a",
    ("audio", "audio/mpeg"): ".mp3",
    ("audio", "audio/wav"): ".wav",
    ("audio", "audio/flac"): ".flac",
    ("audio", "audio/ogg"): ".ogg",
    ("subtitle", "application/x-subrip"): ".srt",
    ("subtitle", "text/vtt"): ".vtt",
    ("transcript", "application/x-subrip"): ".srt",
    ("transcript", "text/vtt"): ".vtt",
    ("transcript", "text/plain"): ".txt",
}


class ContractValidator(Protocol):
    def validate(self, schema_name: str, instance: Any) -> None: ...


@dataclass(frozen=True)
class GeneratedWhisperTranscript:
    staged_path: str
    media_type: str
    sha256: str
    size_bytes: int
    language: str
    technical_probe: Mapping[str, Any]
    generation: int
    evidence_sha256: str


@dataclass(frozen=True)
class MaterializedSourcePackage:
    manifest: dict[str, Any]
    manifest_bytes: bytes
    manifest_sha256: str
    manifest_path: Path
    artifact_paths: tuple[Path, ...]


@dataclass(frozen=True)
class _ArtifactInput:
    candidate_id: str | None
    role: str
    staged_path: str
    media_type: str
    sha256: str
    size_bytes: int
    origin: str
    language: str | None
    subtitle_kind: str | None
    technical_probe: Mapping[str, Any]
    raw: bytes


def _canonical_relative(value: str, *, label: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ContractError(f"{label} is not a canonical run-relative path")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or pure.as_posix() != value
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(part.endswith((" ", ".")) or ":" in part for part in pure.parts)
    ):
        raise ContractError(f"{label} is not a canonical run-relative path")
    return pure


def _run_path(run_dir: Path, value: str, *, label: str) -> Path:
    pure = _canonical_relative(value, label=label)
    root = run_dir.resolve()
    candidate = root.joinpath(*pure.parts)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ContractError(f"{label} escapes the run root") from exc
    return candidate


def _read_regular_file(run_dir: Path, relative: str, *, label: str) -> bytes:
    path = _run_path(run_dir, relative, label=label)
    require_contained_path(
        path,
        run_dir,
        purpose=label,
        error_type=ContractError,
        leaf_kind="file",
        require_single_link=True,
    )
    return path.read_bytes()


def _read_json_control(
    run_dir: Path, relative: str, *, label: str
) -> tuple[dict[str, Any], bytes, str]:
    raw = _read_regular_file(run_dir, relative, label=label)
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} root must be an object")
    return value, raw, sha256_bytes(raw)


def _safe_language(value: str) -> tuple[str, str]:
    display = value.strip().replace("_", "-").lower()
    display = re.sub(r"[^a-z0-9-]+", "-", display)
    display = re.sub(r"-+", "-", display).strip("-")
    if not display:
        raise ContractError("source artifact language cannot be canonicalized")
    return display.replace("-", "_"), display


def _extension(role: str, media_type: str) -> str:
    try:
        return _MEDIA_EXTENSIONS[(role, media_type)]
    except KeyError as exc:
        raise ContractError(
            f"source artifact media type is unsupported for {role}: {media_type}"
        ) from exc


def _artifact_identity(item: _ArtifactInput) -> tuple[str, str]:
    extension = _extension(item.role, item.media_type)
    if item.role == "metadata":
        name = PurePosixPath(item.staged_path).name
        if name == "platform.json":
            return "metadata", "source/metadata/platform.json"
        if name == "media-probe.json":
            return "media_probe", "source/metadata/media-probe.json"
        raise ContractError("metadata candidate lacks a canonical mechanical identity")
    if item.role == "cover":
        return "cover", f"source/cover/cover{extension}"
    if item.role == "video":
        return "video", f"source/media/video{extension}"
    if item.role == "audio":
        return "audio", f"source/media/audio{extension}"
    if item.role in {"subtitle", "transcript"}:
        if not item.language or not item.subtitle_kind:
            raise ContractError("subtitle artifact lacks language or track kind")
        language_id, language_path = _safe_language(item.language)
        kind = item.subtitle_kind
        logical_id = f"{item.role}_{language_id}_{kind}"
        filename = f"{item.role}.{language_path}.{kind}{extension}"
        return logical_id, f"source/subtitles/{filename}"
    raise ContractError(f"unsupported source artifact role: {item.role}")


def _candidate_input(run_dir: Path, candidate: Mapping[str, Any]) -> _ArtifactInput:
    staged_path = candidate.get("staged_path")
    if not isinstance(staged_path, str):
        raise ContractError("candidate staged path is missing")
    if not staged_path.startswith("work/source-acquisition/"):
        raise ContractError("candidate staged path is outside Source Acquisition staging")
    raw = _read_regular_file(run_dir, staged_path, label="candidate staged path")
    expected_sha = candidate.get("sha256")
    expected_size = candidate.get("size_bytes")
    if sha256_bytes(raw) != expected_sha or len(raw) != expected_size:
        raise ContractError("candidate fingerprint drift detected before materialization")
    probe = candidate.get("technical_probe")
    if not isinstance(probe, Mapping):
        raise ContractError("candidate technical probe is missing")
    return _ArtifactInput(
        candidate_id=str(candidate.get("candidate_id")),
        role=str(candidate.get("role")),
        staged_path=staged_path,
        media_type=str(candidate.get("media_type")),
        sha256=str(expected_sha),
        size_bytes=int(expected_size),
        origin=str(candidate.get("origin")),
        language=candidate.get("language"),
        subtitle_kind=candidate.get("subtitle_kind"),
        technical_probe=deepcopy(dict(probe)),
        raw=raw,
    )


def _whisper_input(
    run_dir: Path, value: GeneratedWhisperTranscript
) -> _ArtifactInput:
    if value.generation < 1 or _SHA256.fullmatch(value.evidence_sha256) is None:
        raise ContractError("Whisper generation evidence is invalid")
    if not value.staged_path.startswith("work/source-acquisition/"):
        raise ContractError("Whisper transcript staged path is outside Source Acquisition")
    raw = _read_regular_file(
        run_dir, value.staged_path, label="Whisper transcript staged path"
    )
    if sha256_bytes(raw) != value.sha256 or len(raw) != value.size_bytes:
        raise ContractError("Whisper transcript fingerprint drift detected")
    return _ArtifactInput(
        candidate_id=None,
        role="transcript",
        staged_path=value.staged_path,
        media_type=value.media_type,
        sha256=value.sha256,
        size_bytes=value.size_bytes,
        origin="whisper_transcription",
        language=value.language,
        subtitle_kind="transcript",
        technical_probe=deepcopy(dict(value.technical_probe)),
        raw=raw,
    )


def _policy_manifest_binding(inventory: Mapping[str, Any]) -> dict[str, Any]:
    policy = inventory["policy_binding"]
    return {
        "policy_id": policy["policy_id"],
        "version": policy["version"],
        "sha256": policy["sha256"],
    }


def _validate_fresh_controls(
    inventory: Mapping[str, Any],
    inventory_sha: str,
    skeleton: Mapping[str, Any],
    skeleton_sha: str,
    patch: Mapping[str, Any],
) -> None:
    expected_skeleton = {
        "run_id": inventory["run_id"],
        "source_epoch": inventory["source_epoch"],
        "acquisition_id": inventory["acquisition_id"],
        "source_identity": inventory["source_identity"],
    }
    if any(skeleton.get(key) != value for key, value in expected_skeleton.items()):
        raise ContractError("Decision Skeleton differs from Candidate Inventory authority")
    inventory_binding = skeleton.get("candidate_inventory")
    if (
        not isinstance(inventory_binding, Mapping)
        or inventory_binding.get("path") != INVENTORY_PATH
        or inventory_binding.get("sha256") != inventory_sha
    ):
        raise ContractError("Candidate Inventory fingerprint binding is stale")
    if skeleton.get("policy_binding") != _policy_manifest_binding(inventory):
        raise ContractError("Decision Skeleton policy binding is stale")
    if patch.get("task_id") != skeleton.get("task_id"):
        raise ContractError("Judgment Patch Task identity differs from its Skeleton")
    if patch.get("skeleton_sha256") != skeleton_sha:
        raise ContractError("Judgment Patch Skeleton fingerprint is stale")
    judgment = patch.get("judgment")
    if not isinstance(judgment, Mapping):
        raise ContractError("Source Acquisition Judgment is missing")
    allowed = skeleton.get("allowed_judgment")
    if not isinstance(allowed, Mapping):
        raise ContractError("Decision Skeleton allowed judgment is missing")
    candidates = {
        item["candidate_id"]: item for item in inventory["candidates"]
    }
    subtitle_ids = set(allowed.get("subtitle_candidate_ids", []))
    if any(
        candidate_id not in candidates
        or candidates[candidate_id]["role"] not in {"subtitle", "transcript"}
        for candidate_id in subtitle_ids
    ):
        raise ContractError("Decision Skeleton exposes a non-candidate subtitle")
    audio_id = allowed.get("whisper_audio_candidate_id")
    if audio_id is not None:
        audio = candidates.get(audio_id)
        if audio is None or "audio" not in audio["technical_probe"]["stream_types"]:
            raise ContractError("Decision Skeleton Whisper input is not an audio candidate")
    validate_source_judgment(allowed, judgment)


def _selected_import_candidate(inventory: Mapping[str, Any]) -> str | None:
    priorities = {
        language.strip().replace("_", "-").lower(): index
        for index, language in enumerate(
            inventory["policy_binding"]["subtitle_language_priority"]
        )
    }
    candidates = [
        item
        for item in inventory["candidates"]
        if item["role"] in {"subtitle", "transcript"}
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            priorities.get(
                str(item["language"]).strip().replace("_", "-").lower(),
                len(priorities),
            ),
            {"manual": 0, "automatic": 1, "transcript": 2}.get(
                item["subtitle_kind"], 3
            ),
            item["candidate_id"],
        )
    )
    return str(candidates[0]["candidate_id"])


def _version_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(artifact[key])
        for key in (
            "logical_id",
            "role",
            "media_type",
            "sha256",
            "size_bytes",
            "language",
            "subtitle_kind",
            "technical_probe",
        )
    }


def _preflight_materialization(
    run_dir: Path,
    destination_source_root: Path,
    pending: Mapping[Path, bytes],
) -> None:
    staging = {
        path.with_name(f".{path.name}.kernel-new")
        for path in pending
    }
    require_contained_path(
        destination_source_root,
        run_dir,
        purpose="destination Source root",
        error_type=ContractError,
        leaf_kind="directory",
        allow_missing=True,
    )
    for path in (*pending, *staging):
        require_contained_path(
            path,
            run_dir,
            purpose="destination Source artifact",
            error_type=ContractError,
            leaf_kind="file",
            allow_missing=True,
            require_single_link=True,
        )
    if destination_source_root.exists():
        entries = tuple(destination_source_root.rglob("*"))
        for path in entries:
            require_contained_path(
                path,
                run_dir,
                purpose="destination Source tree entry",
                error_type=ContractError,
                allow_missing=False,
            )
        expected = {*pending, *staging}
        actual = {
            path
            for path in entries
            if path.is_file()
        }
        if actual - expected:
            raise ContractError("destination Source root contains undeclared files")
    for path, raw in pending.items():
        if path.exists():
            if path.read_bytes() != raw:
                raise ContractError("destination Source artifact contains fingerprint drift")


def _write_materialization(pending: Mapping[Path, bytes]) -> None:
    for path, raw in pending.items():
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.kernel-new")
        with staging.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)


def materialize_source_package(
    run_dir: Path,
    *,
    inventory_path: str = INVENTORY_PATH,
    destination_source_root: str,
    published_at: str,
    decision_skeleton_path: str | None = None,
    judgment_patch_path: str | None = None,
    whisper_transcript: GeneratedWhisperTranscript | None = None,
    contracts: ContractValidator | None = None,
    prepare_publication: Callable[[MaterializedSourcePackage], None] | None = None,
) -> MaterializedSourcePackage:
    """Validate controls and materialize one deterministic Source Package tree."""

    run_dir = Path(run_dir).resolve()
    if inventory_path != INVENTORY_PATH:
        raise ContractError("Candidate Inventory path differs from its fixed authority")
    destination_pure = _canonical_relative(
        destination_source_root, label="destination Source root"
    )
    if destination_pure.name != "source":
        raise ContractError("destination Source root must end in source")
    destination_root = _run_path(
        run_dir, destination_source_root, label="destination Source root"
    )
    try:
        timestamp = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("published_at is not an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise ContractError("published_at must include a timezone")

    selected_contracts: ContractValidator = contracts or ContractRegistry(
        Path(__file__).resolve().parents[2]
    )
    inventory, _, inventory_sha = _read_json_control(
        run_dir, inventory_path, label="Candidate Inventory"
    )
    selected_contracts.validate("source-candidate-inventory", inventory)
    expected_identity = derive_source_identity(
        inventory["canonical_platform"], inventory["canonical_item_id"]
    )
    if (
        inventory["source_identity_scheme"] != "canonical-platform-item-v1"
        or inventory["source_identity"] != expected_identity
    ):
        raise ContractError("Candidate Inventory Source Identity is stale")

    mode = inventory["mode"]
    skeleton: dict[str, Any] | None = None
    patch: dict[str, Any] | None = None
    skeleton_sha: str | None = None
    patch_sha: str | None = None
    whisper_generation: dict[str, Any] | None = None
    selected_candidate_id: str | None
    known_gaps: list[dict[str, Any]]
    whisper_status: str
    if mode == "fresh_download":
        if decision_skeleton_path is None or judgment_patch_path is None:
            raise ContractError("fresh Source materialization requires Skeleton and Judgment")
        if decision_skeleton_path != DECISION_SKELETON_PATH:
            raise ContractError("Decision Skeleton path differs from its fixed authority")
        if judgment_patch_path != JUDGMENT_PATCH_PATH:
            raise ContractError("Judgment Patch path differs from its fixed authority")
        skeleton, _, skeleton_sha = _read_json_control(
            run_dir, decision_skeleton_path, label="Decision Skeleton"
        )
        patch, _, patch_sha = _read_json_control(
            run_dir, judgment_patch_path, label="Judgment Patch"
        )
        selected_contracts.validate("source-acquisition-decision-skeleton", skeleton)
        selected_contracts.validate("source-acquisition-judgment-patch", patch)
        _validate_fresh_controls(
            inventory, inventory_sha, skeleton, skeleton_sha, patch
        )
        judgment = patch["judgment"]
        selected_candidate_id = judgment["selected_subtitle_candidate_id"]
        choice = judgment["whisper_fallback"]["choice"]
        whisper_status = {
            "not_required": "not_required",
            "use_whisper": "used",
            "unavailable": "unavailable",
        }[choice]
        known_gaps = deepcopy(judgment["known_gaps"])
        if choice == "use_whisper":
            if whisper_transcript is None:
                raise ContractError("Whisper judgment lacks generated transcript evidence")
            whisper_generation = {
                "generation": whisper_transcript.generation,
                "sha256": whisper_transcript.evidence_sha256,
            }
        elif whisper_transcript is not None:
            raise ContractError("unused Whisper transcript cannot enter Source Package")
    elif mode == "verified_import":
        if (
            decision_skeleton_path is not None
            or judgment_patch_path is not None
            or whisper_transcript is not None
        ):
            raise ContractError("Verified Import must skip semantic Agent controls")
        selected_candidate_id = _selected_import_candidate(inventory)
        whisper_status = "not_required" if selected_candidate_id else "unavailable"
        known_gaps = []
    else:
        raise ContractError(f"unsupported Source Acquisition Mode: {mode}")

    inputs = [_candidate_input(run_dir, item) for item in inventory["candidates"]]
    if whisper_transcript is not None:
        inputs.append(_whisper_input(run_dir, whisper_transcript))
    artifacts: list[dict[str, Any]] = []
    raw_by_logical_id: dict[str, bytes] = {}
    logical_by_candidate: dict[str, str] = {}
    paths: set[str] = set()
    for item in inputs:
        logical_id, path = _artifact_identity(item)
        if logical_id in raw_by_logical_id or path in paths:
            raise ContractError("canonical Source artifact identities are duplicated")
        raw_by_logical_id[logical_id] = item.raw
        paths.add(path)
        if item.candidate_id is not None:
            logical_by_candidate[item.candidate_id] = logical_id
        artifacts.append(
            {
                "logical_id": logical_id,
                "role": item.role,
                "path": path,
                "media_type": item.media_type,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
                "origin": item.origin,
                "language": item.language,
                "subtitle_kind": item.subtitle_kind,
                "technical_probe": deepcopy(dict(item.technical_probe)),
            }
        )
    artifacts.sort(key=lambda item: item["logical_id"])
    if whisper_status == "used":
        selected_artifact_id = next(
            item["logical_id"]
            for item in artifacts
            if item["origin"] == "whisper_transcription"
        )
    elif selected_candidate_id is None:
        selected_artifact_id = None
    else:
        try:
            selected_artifact_id = logical_by_candidate[selected_candidate_id]
        except KeyError as exc:
            raise ContractError("selected source candidate was not materialized") from exc

    version_artifacts = [_version_artifact(item) for item in artifacts]
    source_version_basis = {
        "canonicalization": SOURCE_VERSION_CANONICALIZATION,
        "source_identity": expected_identity,
        "artifacts": version_artifacts,
    }
    source_version = sha256_bytes(canonical_json_bytes(source_version_basis))
    video_stream_count = sum(
        "video" in item["technical_probe"]["stream_types"] for item in artifacts
    )
    audio_stream_count = sum(
        "audio" in item["technical_probe"]["stream_types"] for item in artifacts
    )
    if video_stream_count < 1 or audio_stream_count < 1:
        raise ContractError("Source technical probes lack video or audio streams")
    subtitle_languages = sorted(
        {
            item["language"]
            for item in artifacts
            if item["role"] in {"subtitle", "transcript"}
            and item["language"] is not None
        },
        key=str.casefold,
    )

    if mode == "fresh_download":
        assert skeleton_sha is not None and patch_sha is not None and patch is not None
        auth = inventory["authentication_classification"]
        if auth not in {"anonymous", "cookie_accepted"}:
            raise ContractError("fresh Source Manifest has no successful authentication class")
        provenance = {
            "kind": "fresh_download",
            "candidate_inventory_sha256": inventory_sha,
            "decision_skeleton_sha256": skeleton_sha,
            "judgment_patch": {
                "task_id": patch["task_id"],
                "attempt_id": patch["attempt_id"],
                "sha256": patch_sha,
            },
            "whisper_generation": whisper_generation,
            "authentication_classification": auth,
        }
    else:
        import_binding = inventory.get("import_binding")
        if not isinstance(import_binding, Mapping):
            raise ContractError("Verified Import lacks its validation binding")
        if (
            import_binding["prior_source_identity"] != expected_identity
            or import_binding["prior_source_version"] != source_version
        ):
            raise ContractError("Verified Import changed Source Identity or Version")
        provenance = {
            "kind": "verified_import",
            "candidate_inventory_sha256": inventory_sha,
            "prior_run_id": import_binding["prior_run_id"],
            "prior_source_manifest_sha256": import_binding[
                "prior_source_manifest_sha256"
            ],
            "prior_source_identity": import_binding["prior_source_identity"],
            "prior_source_version": import_binding["prior_source_version"],
            "import_validation_sha256": sha256_bytes(
                canonical_json_bytes(import_binding["validation"])
            ),
            "authentication_classification": "not_applicable",
        }

    manifest = {
        "schema_name": "source-manifest",
        "schema_version": "2.0.0",
        "kernel_version": "2.0.0",
        "run_id": inventory["run_id"],
        "source_epoch": inventory["source_epoch"],
        "acquisition_id": inventory["acquisition_id"],
        "mode": mode,
        "adapter": deepcopy(inventory["adapter"]),
        "canonical_platform": inventory["canonical_platform"],
        "canonical_item_id": inventory["canonical_item_id"],
        "source_identity_scheme": "canonical-platform-item-v1",
        "source_identity": expected_identity,
        "source_version_scheme": "source-content-v1",
        "source_version": source_version,
        "original_title": inventory["source_metadata"]["original_title"],
        "policy_binding": _policy_manifest_binding(inventory),
        "provenance": provenance,
        "selection": {
            "selected_subtitle_artifact_id": selected_artifact_id,
            "whisper_status": whisper_status,
        },
        "technical_validation": {
            "duration_seconds": inventory["source_metadata"]["duration_seconds"],
            "video_stream_count": video_stream_count,
            "audio_stream_count": audio_stream_count,
            "subtitle_languages": subtitle_languages,
            "status": "pass",
        },
        "source_version_basis": source_version_basis,
        "artifacts": artifacts,
        "known_gaps": known_gaps,
        "published_at": published_at,
        "package_status": "validated",
    }
    selected_contracts.validate("source-manifest", manifest)
    manifest_bytes = canonical_json_bytes(manifest)
    pending: dict[Path, bytes] = {}
    artifact_paths: list[Path] = []
    for artifact in artifacts:
        relative = PurePosixPath(artifact["path"]).relative_to("source")
        target = destination_root / Path(*relative.parts)
        pending[target] = raw_by_logical_id[artifact["logical_id"]]
        artifact_paths.append(target)
    manifest_path = destination_root / "manifest.json"
    pending[manifest_path] = manifest_bytes
    package = MaterializedSourcePackage(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_sha256=sha256_bytes(manifest_bytes),
        manifest_path=manifest_path,
        artifact_paths=tuple(artifact_paths),
    )
    _preflight_materialization(run_dir, destination_root, pending)
    if prepare_publication is not None:
        prepare_publication(package)
    _write_materialization(pending)
    return package


__all__ = [
    "GeneratedWhisperTranscript",
    "MaterializedSourcePackage",
    "materialize_source_package",
]

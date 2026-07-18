from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Callable, Mapping, Sequence, TypeVar

from .contracts import ContractRegistry
from .errors import ArtifactDrift, ContractError, KernelConflict, KernelError
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_file,
    write_json_atomic,
)


SOURCE_IDENTITY_SCHEME = "canonical-platform-item-v1"
SOURCE_VERSION_SCHEME = "source-content-v1"
SOURCE_VERSION_CANONICALIZATION = "video2pdf-canonical-json-v1"
PRODUCTION_PLATFORMS = frozenset({"bilibili", "youtube"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_RESULT = TypeVar("_PROVIDER_RESULT")
SOURCE_REOPEN_FAULT_POINTS = frozenset(
    {
        "after_reopen_prepared",
        "after_reopen_source_preserved",
        "after_reopen_run_record_commit",
    }
)
SOURCE_USER_INPUT_RESOLUTION_FAULT_POINTS = frozenset(
    {
        "after_resolution_evidence_written",
        "after_resolution_mutation_prepared",
        "after_resolution_run_record_written",
    }
)


@dataclass(frozen=True)
class SubtitleCandidate:
    candidate_id: str
    language: str
    subtitle_kind: str
    technically_usable: bool

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.language:
            raise ContractError("subtitle candidate identity and language are required")
        if _SHA256.fullmatch(self.candidate_id) is None:
            raise ContractError("subtitle candidate identity must be SHA-256")
        if self.subtitle_kind not in {"manual", "automatic"}:
            raise ContractError("subtitle candidate kind is unsupported")


@dataclass(frozen=True)
class SourceArtifactBinding:
    """Path-independent original-source evidence used for content versioning."""

    logical_id: str
    role: str
    media_type: str
    sha256: str
    size_bytes: int
    language: str | None
    subtitle_kind: str | None
    technical_probe: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.logical_id or not self.role or not self.media_type:
            raise ContractError("Source Artifact Binding identities must be non-empty")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ContractError("Source Artifact Binding SHA-256 is invalid")
        if self.size_bytes <= 0:
            raise ContractError("Source Artifact Binding size must be positive")
        if self.subtitle_kind not in {None, "manual", "automatic", "whisper"}:
            raise ContractError("Source Artifact Binding subtitle kind is unsupported")
        if self.role == "subtitle" and not self.language:
            raise ContractError("subtitle Source Artifact Binding requires a language")
        if not isinstance(self.technical_probe, Mapping):
            raise ContractError("Source Artifact Binding technical evidence must be an object")

    def version_value(self) -> dict[str, Any]:
        return {
            "logical_id": self.logical_id,
            "role": self.role,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "language": self.language,
            "subtitle_kind": self.subtitle_kind,
            "technical_probe": dict(self.technical_probe),
        }


@dataclass(frozen=True)
class SourceReopenResult:
    intent_id: str
    journal_path: Path


class SourceReopenFault(KernelError):
    classification = "injected_source_reopen_fault"
    exit_code = 60


class SourceUserInputResolutionFault(KernelError):
    classification = "injected_source_user_input_resolution_fault"
    exit_code = 61


def transitively_stale_checkpoints(
    dependencies: Mapping[str, Sequence[str]], seed: str
) -> set[str]:
    """Return the seed and every checkpoint that directly or indirectly consumes it."""

    if seed not in dependencies:
        raise ContractError(f"Source Reopen seed checkpoint is absent: {seed}")
    stale = {seed}
    while True:
        expanded = {
            checkpoint
            for checkpoint, prerequisites in dependencies.items()
            if any(prerequisite in stale for prerequisite in prerequisites)
        }
        updated = stale | expanded
        if updated == stale:
            return stale
        stale = updated


class SourceReopenControlAuthority:
    """Compatibility boundary over the persisted Slice 2 Run mutation authority."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def intent_id(self, record: Mapping[str, Any], prior_sha256: str) -> str:
        return self._store.derive_run_state_mutation_id(
            run_id=record["run_id"],
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=prior_sha256,
        )

    def prepare(
        self,
        replacement: dict[str, Any],
        *,
        prior_sha256: str,
    ) -> None:
        intent = self._store.prepare_run_state_mutation(
            run_id=replacement["run_id"],
            expected_run_revision=replacement["coordination_revision"] - 1,
            old_run_record_sha256=prior_sha256,
            replacement_run_record=replacement,
        )
        if intent["replacement_run_record_sha256"] != hashlib.sha256(
            canonical_json_bytes(replacement)
        ).hexdigest():
            raise KernelConflict("Source Reopen mutation authority changed replacement")

    def commit(self, intent_id: str) -> None:
        self._store.commit_run_state_mutation(intent_id)

    def current_run_record_sha(self, run_id: str) -> str | None:
        return self._store.current_run_record_sha(run_id)

    def committed_replacement_sha(self, intent_id: str) -> str | None:
        mutation = self._store.run_state_mutation_by_id(intent_id)
        if mutation is None or mutation["state"] != "COMMITTED":
            return None
        return str(mutation["replacement_run_record_sha256"])


class SourceReopenSaga:
    """Preserve a finalized Source Package and durably invalidate its consumers."""

    def __init__(
        self,
        run_dir: Path,
        *,
        contracts: ContractRegistry | None = None,
        authority: Any | None = None,
    ) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.run_path = self.run_dir / "workflow" / "run.json"
        self.journal_root = self.run_dir / "待删除" / "source-reopens"
        self.contracts = contracts or ContractRegistry(
            Path(__file__).resolve().parents[2]
        )
        self.authority = authority

    @staticmethod
    def _inject(selected: str | None, current: str) -> None:
        if selected == current:
            raise SourceReopenFault(current)

    def _validate_journal_path(
        self,
        journal_path: Path,
        journal: Mapping[str, Any],
        *,
        allow_missing: bool,
    ) -> Path:
        expected = self.run_dir.joinpath(
            *PurePosixPath(str(journal["journal_path"])).parts
        )
        if Path(os.path.abspath(journal_path)) != expected:
            raise ArtifactDrift("Source Reopen journal path binding drifted")
        return require_contained_path(
            expected,
            self.run_dir,
            purpose="Source Reopen journal",
            error_type=ArtifactDrift,
            leaf_kind="file",
            allow_missing=allow_missing,
            require_single_link=True,
        )

    def _read_journal(self, journal_path: Path) -> dict[str, Any]:
        require_contained_path(
            journal_path,
            self.run_dir,
            purpose="Source Reopen journal",
            error_type=ArtifactDrift,
            leaf_kind="file",
            require_single_link=True,
        )
        journal = read_json(journal_path)
        self.contracts.validate("source-reopen-journal", journal)
        self._validate_journal_path(journal_path, journal, allow_missing=False)
        if journal_path.read_bytes() != canonical_json_bytes(journal):
            raise ArtifactDrift("Source Reopen journal is not canonical JSON")
        return journal

    def _write_journal(
        self,
        journal_path: Path,
        journal: dict[str, Any],
    ) -> dict[str, Any]:
        self.contracts.validate("source-reopen-journal", journal)
        self._validate_journal_path(journal_path, journal, allow_missing=True)
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(journal_path, journal)
        persisted = self._read_journal(journal_path)
        if persisted != journal:
            raise ArtifactDrift("Source Reopen journal changed during durable write")
        return persisted

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        try:
            result = path.lstat()
        except OSError:
            return False
        return path.is_symlink() or bool(
            getattr(result, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )

    def _preservation_paths(
        self,
        preservation: Mapping[str, Any],
    ) -> tuple[Path, Path]:
        logical_id = str(preservation["logical_id"])
        leaf_kind = (
            "file" if preservation["kind"] == "control_file" else "directory"
        )
        current_path = self.run_dir.joinpath(
            *PurePosixPath(str(preservation["current_path"])).parts
        )
        preservation_path = self.run_dir.joinpath(
            *PurePosixPath(str(preservation["preservation_path"])).parts
        )
        for path, label in (
            (current_path, "current"),
            (preservation_path, "preservation"),
        ):
            require_contained_path(
                path,
                self.run_dir,
                purpose=f"Source Reopen {label} {logical_id}",
                error_type=ArtifactDrift,
                leaf_kind=leaf_kind,
                allow_missing=True,
                require_single_link=leaf_kind == "file",
            )
            if os.path.lexists(path) and self._is_link_or_reparse(path):
                raise ArtifactDrift(
                    f"Source Reopen {label} {logical_id} is linked or reparse-backed"
                )
        return current_path, preservation_path

    def _closed_tree_inventory(
        self,
        root: Path,
        *,
        purpose: str,
    ) -> tuple[set[str], set[str]]:
        if self._is_link_or_reparse(root) or not root.is_dir():
            raise ArtifactDrift(f"{purpose} root is absent or linked")
        files: set[str] = set()
        directories: set[str] = set()
        pending = [(root, "")]
        while pending:
            directory, prefix = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    relative = f"{prefix}/{entry.name}".lstrip("/")
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or (
                        getattr(info, "st_file_attributes", 0)
                        & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    ):
                        raise ArtifactDrift(
                            f"{purpose} contains a linked or reparse-backed entry"
                        )
                    if entry.is_dir(follow_symlinks=False):
                        directories.add(relative)
                        pending.append((Path(entry.path), relative))
                    elif entry.is_file(follow_symlinks=False):
                        files.add(relative)
                    else:
                        raise ArtifactDrift(
                            f"{purpose} contains an unsupported entry"
                        )
        return files, directories

    def _verify_source_tree(
        self,
        root: Path,
        *,
        expected_manifest_sha256: str,
    ) -> None:
        manifest_path = root / "manifest.json"
        if (
            self._is_link_or_reparse(manifest_path)
            or not manifest_path.is_file()
            or sha256_file(manifest_path) != expected_manifest_sha256
        ):
            raise ArtifactDrift("Source Reopen prior Source Manifest drifted")
        manifest = read_json(manifest_path)
        if manifest.get("schema_name") != "source-manifest":
            self._closed_tree_inventory(root, purpose="Source Reopen prior Source tree")
            return
        self.contracts.validate("source-manifest", manifest)
        expected_files = {"manifest.json"}
        for artifact in manifest["artifacts"]:
            relative = PurePosixPath(artifact["path"]).relative_to("source")
            expected_files.add(relative.as_posix())
            path = root.joinpath(*relative.parts)
            if (
                self._is_link_or_reparse(path)
                or not path.is_file()
                or path.stat().st_size != artifact["size_bytes"]
                or sha256_file(path) != artifact["sha256"]
            ):
                raise ArtifactDrift(
                    "Source Reopen prior Source artifact fingerprint drifted"
                )
        observed_files, _ = self._closed_tree_inventory(
            root,
            purpose="Source Reopen prior Source tree",
        )
        if observed_files != expected_files:
            raise ArtifactDrift(
                "Source Reopen prior Source tree is outside its closed Manifest"
            )

    def _verify_candidate_tree(
        self,
        root: Path,
        *,
        inventory_path: Path,
        expected_inventory_sha256: str,
    ) -> None:
        if (
            self._is_link_or_reparse(inventory_path)
            or not inventory_path.is_file()
            or sha256_file(inventory_path) != expected_inventory_sha256
        ):
            raise ArtifactDrift(
                "Source Reopen Candidate Inventory evidence drifted"
            )
        inventory = read_json(inventory_path)
        self.contracts.validate("source-candidate-inventory", inventory)
        candidate_prefix = PurePosixPath("work/source-acquisition/candidates")
        expected_files: dict[str, Mapping[str, Any]] = {}
        expected_directories: set[str] = set()
        for candidate in inventory["candidates"]:
            try:
                relative = PurePosixPath(candidate["staged_path"]).relative_to(
                    candidate_prefix
                )
            except ValueError as exc:
                raise ArtifactDrift(
                    "Source Reopen Candidate Inventory path escaped staging"
                ) from exc
            relative_value = relative.as_posix()
            if relative_value in expected_files:
                raise ArtifactDrift(
                    "Source Reopen Candidate Inventory repeats a staged path"
                )
            expected_files[relative_value] = candidate
            parent = relative.parent
            while str(parent) != ".":
                expected_directories.add(parent.as_posix())
                parent = parent.parent
        observed_files, observed_directories = self._closed_tree_inventory(
            root,
            purpose="Source Reopen Candidate tree",
        )
        if (
            observed_files != set(expected_files)
            or observed_directories != expected_directories
        ):
            raise ArtifactDrift(
                "Source Reopen Candidate tree differs from its closed Inventory"
            )
        for relative, candidate in expected_files.items():
            path = root.joinpath(*PurePosixPath(relative).parts)
            if (
                path.stat().st_size != candidate["size_bytes"]
                or sha256_file(path) != candidate["sha256"]
            ):
                raise ArtifactDrift(
                    "Source Reopen Candidate fingerprint differs from Inventory"
                )

    def _verify_preservation_batch(
        self,
        journal: Mapping[str, Any],
        locations: Mapping[str, Path],
    ) -> None:
        by_id = {
            str(item["logical_id"]): item for item in journal["preservations"]
        }
        for logical_id, preservation in by_id.items():
            location = locations[logical_id]
            expected_sha = preservation["expected_sha256"]
            if logical_id == "source_package":
                self._verify_source_tree(
                    location,
                    expected_manifest_sha256=str(expected_sha),
                )
            elif preservation["kind"] == "control_file":
                if (
                    self._is_link_or_reparse(location)
                    or not location.is_file()
                    or sha256_file(location) != expected_sha
                ):
                    raise ArtifactDrift(
                        f"Source Reopen control evidence drifted: {logical_id}"
                    )
        candidate = by_id.get("source_candidates")
        if candidate is not None:
            inventory_path = locations.get("source_candidate_inventory")
            if inventory_path is None:
                raise ArtifactDrift(
                    "Source Reopen Candidate tree lacks Inventory preservation"
                )
            self._verify_candidate_tree(
                locations["source_candidates"],
                inventory_path=inventory_path,
                expected_inventory_sha256=str(candidate["expected_sha256"]),
            )

    def _preflight_preservations(
        self,
        journal: Mapping[str, Any],
    ) -> tuple[dict[str, tuple[Path, Path]], dict[str, Path]]:
        paths: dict[str, tuple[Path, Path]] = {}
        locations: dict[str, Path] = {}
        for preservation in journal["preservations"]:
            logical_id = str(preservation["logical_id"])
            current, preserved = self._preservation_paths(preservation)
            current_exists = os.path.lexists(current)
            preserved_exists = os.path.lexists(preserved)
            if current_exists and preserved_exists:
                raise ArtifactDrift(
                    f"Source Reopen retained two copies of {logical_id}"
                )
            if not current_exists and not preserved_exists:
                raise ArtifactDrift(
                    f"Source Reopen lost both copies of {logical_id}"
                )
            paths[logical_id] = (current, preserved)
            locations[logical_id] = current if current_exists else preserved
        self._verify_preservation_batch(journal, locations)
        return paths, locations

    def _verify_preserved_batch(self, journal: Mapping[str, Any]) -> None:
        locations: dict[str, Path] = {}
        for preservation in journal["preservations"]:
            logical_id = str(preservation["logical_id"])
            _, preserved = self._preservation_paths(preservation)
            if not os.path.lexists(preserved):
                raise ArtifactDrift(
                    f"Source Reopen preservation disappeared: {logical_id}"
                )
            locations[logical_id] = preserved
        self._verify_preservation_batch(journal, locations)

    def _preservations(
        self,
        record: Mapping[str, Any],
        intent_id: str,
        source_manifest_sha256: str,
    ) -> list[dict[str, Any]]:
        generations = record["artifact_generations"]
        specs = [
            (
                "source_package",
                "source_tree",
                "source",
                "source/manifest.json",
                "source_manifest",
            ),
        ]
        if "source_candidate_inventory" in generations:
            specs.extend(
                [
                    (
                        "source_candidates",
                        "candidate_tree",
                        "work/source-acquisition/candidates",
                        "work/source-acquisition/candidate-inventory.json",
                        "source_candidate_inventory",
                    ),
                    (
                        "source_candidate_inventory",
                        "control_file",
                        "work/source-acquisition/candidate-inventory.json",
                        "work/source-acquisition/candidate-inventory.json",
                        "source_candidate_inventory",
                    ),
                ]
            )
        for logical_id, current_path, generation_id in (
            (
                "source_acquisition_decision_skeleton",
                "work/source-acquisition/decision.skeleton.json",
                "source_acquisition_decision_skeleton",
            ),
            (
                "source_transcription",
                "work/source-acquisition/transcription.srt",
                "source_transcription",
            ),
            (
                "source_acquisition_decision",
                "workflow/source-acquisition-judgment-patch.json",
                "source_acquisition_decision",
            ),
        ):
            if generation_id in generations:
                specs.append(
                    (
                        logical_id,
                        "control_file",
                        current_path,
                        current_path,
                        generation_id,
                    )
                )
        result = []
        for logical_id, kind, current_path, evidence_path, generation_id in specs:
            expected_sha = (
                source_manifest_sha256
                if generation_id == "source_manifest"
                else generations[generation_id]["sha256"]
            )
            result.append(
                {
                    "logical_id": logical_id,
                    "kind": kind,
                    "current_path": current_path,
                    "preservation_path": (
                        f"待删除/source-reopens/{intent_id}/previous/{current_path}"
                    ),
                    "evidence_path": evidence_path,
                    "expected_sha256": expected_sha,
                }
            )
        return result

    def _replacement(self, record: dict[str, Any], intent_id: str) -> dict[str, Any]:
        replacement = json.loads(json.dumps(record))
        stale = transitively_stale_checkpoints(
            replacement["checkpoint_dependencies"], "source_candidates_ready"
        )
        for checkpoint in stale:
            if checkpoint in replacement["checkpoints"]:
                replacement["checkpoints"][checkpoint]["status"] = "stale"
        replacement["source_epoch"] = int(record["source_epoch"]) + 1
        replacement["source_state"] = "stale"
        replacement["source_version"] = None
        replacement["source_blocker"] = None
        replacement["phase"] = "source_acquisition"
        replacement["coordination_revision"] = int(record["coordination_revision"]) + 1
        replacement["last_mutation_intent_id"] = intent_id
        return replacement

    def reopen(
        self,
        *,
        reason: str,
        validated_record: Mapping[str, Any],
        fault_point: str | None = None,
    ) -> SourceReopenResult:
        if fault_point is not None and fault_point not in SOURCE_REOPEN_FAULT_POINTS:
            raise ContractError(f"unknown Source Reopen fault point: {fault_point}")
        if not reason.strip():
            raise ContractError("Source Reopen requires a reason")
        if not isinstance(validated_record, Mapping):
            raise ContractError("Source Reopen requires a validated Source record")
        record = json.loads(json.dumps(validated_record))
        if (
            hashlib.sha256(canonical_json_bytes(record)).hexdigest()
            != sha256_file(self.run_path)
            or record.get("source_state") != "ready"
            or _SHA256.fullmatch(str(record.get("source_version", ""))) is None
            or record.get("checkpoints", {}).get("source_ready", {}).get("status")
            != "current"
        ):
            raise ArtifactDrift("Source Reopen record is not the validated current authority")
        source_root = self.run_dir / "source"
        manifest_path = source_root / "manifest.json"
        expected_manifest = record["artifact_generations"]["source_manifest"]["sha256"]
        if (
            source_root.is_symlink()
            or not manifest_path.is_file()
            or manifest_path.is_symlink()
            or sha256_file(manifest_path) != expected_manifest
        ):
            raise ArtifactDrift("current Source Package cannot be preserved for reopen")
        prior_run_sha = sha256_file(self.run_path)
        if self.authority is None:
            intent_id = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "operation": "source-reopen-v1",
                        "run_id": record["run_id"],
                        "coordination_revision": record["coordination_revision"],
                        "source_manifest_sha256": expected_manifest,
                        "reason": reason,
                    }
                )
            ).hexdigest()
        else:
            intent_id = self.authority.intent_id(record, prior_run_sha)
        replacement = self._replacement(record, intent_id)
        replacement_sha = hashlib.sha256(canonical_json_bytes(replacement)).hexdigest()
        journal_path = self.journal_root / intent_id / "reopen.json"
        journal = {
            "schema_name": "source-reopen-journal",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "intent_id": intent_id,
            "run_id": record["run_id"],
            "reason": reason,
            "prior_source_epoch": record["source_epoch"],
            "expected_run_revision": record["coordination_revision"],
            "prior_run_record_sha256": prior_run_sha,
            "replacement_run_record_sha256": replacement_sha,
            "replacement_run_record": replacement,
            "source_manifest_sha256": expected_manifest,
            "coordination_record_path": "workflow/run.json",
            "journal_path": (
                f"待删除/source-reopens/{intent_id}/reopen.json"
            ),
            "preservations": self._preservations(
                record,
                intent_id,
                expected_manifest,
            ),
            "state": "PREPARED",
        }
        self.contracts.validate("source-reopen-journal", journal)
        if journal_path.exists():
            if self._read_journal(journal_path) != journal:
                raise KernelConflict("Source Reopen intent identity was reused")
        else:
            self._write_journal(journal_path, journal)
        if self.authority is not None:
            self.authority.prepare(replacement, prior_sha256=prior_run_sha)
        self._inject(fault_point, "after_reopen_prepared")
        result = self._advance(journal_path, fault_point=fault_point)
        return result

    def reconcile(self) -> SourceReopenResult:
        journals = sorted(self.journal_root.glob("*/reopen.json"))
        if not journals:
            raise KernelConflict("Source Reopen has no durable journal")
        validated = [(path, self._read_journal(path)) for path in journals]
        active = [path for path, journal in validated if journal["state"] != "COMMITTED"]
        if len(active) > 1:
            raise KernelConflict("multiple Source Reopen intents require recovery")
        selected = active[0] if active else journals[-1]
        return self._advance(selected, fault_point=None)

    def _advance(
        self, journal_path: Path, *, fault_point: str | None
    ) -> SourceReopenResult:
        journal = self._read_journal(journal_path)
        replacement = journal["replacement_run_record"]
        state = journal["state"]
        if self.authority is not None and state != "COMMITTED":
            self.authority.prepare(
                replacement,
                prior_sha256=journal["prior_run_record_sha256"],
            )
        if state == "PREPARED":
            paths, _ = self._preflight_preservations(journal)
            for current, preserved in paths.values():
                if os.path.lexists(preserved):
                    continue
                preserved.parent.mkdir(parents=True, exist_ok=True)
                os.replace(current, preserved)
            post_paths, post_locations = self._preflight_preservations(journal)
            if any(
                post_locations[logical_id] != preserved
                for logical_id, (_, preserved) in post_paths.items()
            ):
                raise ArtifactDrift(
                    "Source Reopen preservation batch is only partially moved"
                )
            journal["state"] = "SOURCE_PRESERVED"
            journal = self._write_journal(journal_path, journal)
            state = "SOURCE_PRESERVED"
            self._inject(fault_point, "after_reopen_source_preserved")
        if state == "SOURCE_PRESERVED":
            self._verify_preserved_batch(journal)
            source_root = self.run_dir / "source"
            candidate_root = self.run_dir / "work/source-acquisition/candidates"
            for root in (source_root, candidate_root):
                require_contained_path(
                    root,
                    self.run_dir,
                    purpose="Source Reopen fresh acquisition root",
                    error_type=ArtifactDrift,
                    leaf_kind="directory",
                    allow_missing=True,
                )
                if os.path.lexists(root):
                    if self._is_link_or_reparse(root) or not root.is_dir():
                        raise ArtifactDrift(
                            "Source Reopen fresh acquisition root is linked or invalid"
                        )
                    if any(os.scandir(root)):
                        raise ArtifactDrift(
                            "Source Reopen fresh acquisition root is not empty"
                        )
                else:
                    root.mkdir(parents=True, exist_ok=False)
            actual_run_sha = sha256_file(self.run_path)
            if actual_run_sha == journal["prior_run_record_sha256"]:
                written = write_json_atomic(self.run_path, replacement)
                if written != journal["replacement_run_record_sha256"]:
                    raise KernelConflict("Source Reopen replacement fingerprint changed")
            elif actual_run_sha != journal["replacement_run_record_sha256"]:
                raise ArtifactDrift("Source Reopen Run Record has an unknown generation")
            journal["state"] = "RECORD_COMMITTED"
            journal = self._write_journal(journal_path, journal)
            state = "RECORD_COMMITTED"
            self._inject(fault_point, "after_reopen_run_record_commit")
        if state == "RECORD_COMMITTED":
            self._verify_preserved_batch(journal)
            if sha256_file(self.run_path) != journal["replacement_run_record_sha256"]:
                raise ArtifactDrift("Source Reopen lost its coordination commit marker")
            if self.authority is not None:
                self.authority.commit(journal["intent_id"])
            journal["state"] = "COMMITTED"
            journal = self._write_journal(journal_path, journal)
            state = "COMMITTED"
        elif state != "COMMITTED":
            raise ArtifactDrift("Source Reopen journal state is unsupported")
        if state == "COMMITTED":
            self._verify_preserved_batch(journal)
        if self.authority is not None:
            committed_sha = self.authority.committed_replacement_sha(
                journal["intent_id"]
            )
            if committed_sha != journal["replacement_run_record_sha256"]:
                raise ArtifactDrift(
                    "Source Reopen Control Store commit marker is stale"
                )
        self.contracts.validate("source-reopen-journal", journal)
        return SourceReopenResult(journal["intent_id"], journal_path)


class AdmittedSourceProviderLauncher:
    """The only production boundary allowed to start download or Whisper work."""

    def __init__(self, kernel: Any) -> None:
        self._kernel = kernel

    def launch_adapter(
        self,
        *,
        attempt_id: str,
        claim_generation: int,
        resource_class: str,
        provider: Callable[[str], _PROVIDER_RESULT],
        fault_point: str | None = None,
    ) -> _PROVIDER_RESULT:
        if resource_class not in {"bilibili_download", "youtube_download"}:
            raise ContractError("production Source Adapter resource class is invalid")
        return self._kernel.launch_admitted_task(
            attempt_id,
            claim_generation,
            (resource_class,),
            provider,
            fault_point=fault_point,
        )

    def launch_whisper(
        self,
        *,
        attempt_id: str,
        claim_generation: int,
        provider: Callable[[str], _PROVIDER_RESULT],
        fault_point: str | None = None,
    ) -> _PROVIDER_RESULT:
        return self._kernel.launch_admitted_task(
            attempt_id,
            claim_generation,
            ("whisper",),
            provider,
            fault_point=fault_point,
        )


def record_source_blocker(
    kernel: Any, canonical_platform: str, error: Exception
) -> dict[str, Any]:
    """Classify a safe user-input blocker and scope authentication breakers."""

    if canonical_platform not in PRODUCTION_PLATFORMS:
        raise ContractError("Source blocker platform is unsupported")
    data = getattr(error, "data", {})
    reason = data.get("authentication_classification")
    allowed = {
        "cookie_missing",
        "cookie_unreadable",
        "cookie_rejected",
        "cookie_expired",
    }
    if getattr(error, "blocker_kind", None) != "user_input" or reason not in allowed:
        raise ContractError("platform failure is not a supported Source user-input blocker")
    resource_class = f"{canonical_platform}_download"
    breaker_state = "open" if reason in {"cookie_rejected", "cookie_expired"} else "not_open"
    evidence = {
        "classification": getattr(error, "classification", "kernel_error"),
        "kind": "user_input",
        "reason": reason,
        "canonical_platform": canonical_platform,
        "resource_class": resource_class,
        "breaker_state": breaker_state,
    }
    if breaker_state == "open":
        kernel.set_resource_circuit_breaker(
            resource_class,
            state="open",
            reason=reason,
            platform=canonical_platform,
        )
    return {
        "kind": "user_input",
        "reason": reason,
        "canonical_platform": canonical_platform,
        "resource_class": resource_class,
        "breaker_state": breaker_state,
        "evidence_sha256": hashlib.sha256(canonical_json_bytes(evidence)).hexdigest(),
    }


def persist_source_blocker(
    kernel: Any,
    run_dir: Path,
    canonical_platform: str,
    error: Exception,
) -> dict[str, Any]:
    """Commit a production user-input blocker to Run authority and its breaker."""

    run_path = Path(run_dir).resolve() / "workflow" / "run.json"
    record = read_json(run_path)
    kernel.contracts.validate_run_record(record)
    if record.get("schema_version") != "3.0.0":
        raise ContractError("Source user-input blockers require Run Record v3")
    if record.get("canonical_platform") != canonical_platform:
        raise ContractError("Source blocker platform differs from Run identity")
    store = getattr(kernel, "control_store", None)
    if store is None:
        raise KernelConflict("Source blocker has no durable Control Store authority")
    prior_sha = sha256_file(run_path)
    if store.current_run_record_sha(record["run_id"]) != prior_sha:
        raise ArtifactDrift(
            "Run Record differs from its committed authority predecessor",
            data={"drifted_paths": ["workflow/run.json"]},
        )
    if record["source_state"] not in {"pending", "stale", "blocked_user_input"}:
        raise KernelConflict("Source user-input blocker cannot replace acquired evidence")
    blocker = record_source_blocker(kernel, canonical_platform, error)
    if record["source_state"] == "blocked_user_input":
        if record["source_blocker"] != blocker:
            raise KernelConflict("Source Run already records a different user-input blocker")
        return blocker

    replacement = json.loads(json.dumps(record))
    replacement["source_state"] = "blocked_user_input"
    replacement["source_version"] = None
    replacement["source_blocker"] = blocker
    replacement["phase"] = "source_acquisition"
    for checkpoint_name, checkpoint in replacement["checkpoints"].items():
        if checkpoint_name != "run_initialized":
            checkpoint["status"] = "stale"
    replacement["coordination_revision"] = int(record["coordination_revision"]) + 1
    mutation_id = store.derive_run_state_mutation_id(
        run_id=record["run_id"],
        expected_run_revision=record["coordination_revision"],
        old_run_record_sha256=prior_sha,
    )
    replacement["last_mutation_intent_id"] = mutation_id
    kernel.contracts.validate_run_record(replacement)
    mutation = store.prepare_run_state_mutation(
        run_id=record["run_id"],
        expected_run_revision=record["coordination_revision"],
        old_run_record_sha256=prior_sha,
        replacement_run_record=replacement,
    )
    if sha256_file(run_path) != mutation["old_run_record_sha256"]:
        raise KernelConflict("Run Record changed after Source blocker preparation")
    replacement_sha = write_json_atomic(run_path, replacement)
    if replacement_sha != mutation["replacement_run_record_sha256"]:
        raise KernelConflict("Source blocker replacement fingerprint changed")
    store.commit_run_state_mutation(mutation_id)
    if store.current_run_record_sha(record["run_id"]) != replacement_sha:
        raise ArtifactDrift("Source blocker Control Store commit marker is stale")
    return blocker


def resolve_source_user_input(
    kernel: Any,
    run_dir: Path,
    *,
    authentication_classification: str,
    credential_evidence: Mapping[str, Any],
    credential_evidence_sha256: str,
    fault_point: str | None = None,
) -> dict[str, Any]:
    """Advance one blocked Run into a fresh Source acquisition epoch."""

    if (
        fault_point is not None
        and fault_point not in SOURCE_USER_INPUT_RESOLUTION_FAULT_POINTS
    ):
        raise ContractError(
            f"unknown Source user-input resolution fault point: {fault_point}"
        )

    if authentication_classification != "cookie_accepted":
        raise ContractError(
            "Source user-input resolution requires cookie_accepted evidence"
        )
    if _SHA256.fullmatch(credential_evidence_sha256) is None:
        raise ContractError(
            "Source user-input resolution evidence must be a SHA-256 fingerprint"
        )
    run_dir = Path(run_dir).resolve()
    run_path = require_contained_path(
        run_dir / "workflow" / "run.json",
        run_dir,
        purpose="Source user-input resolution Run Record",
        error_type=ArtifactDrift,
        leaf_kind="file",
        require_single_link=True,
    )
    record = read_json(run_path)
    kernel.contracts.validate_run_record(record)
    if record.get("schema_version") != "3.0.0":
        raise ContractError("Source user-input resolution requires Run Record v3")
    if record.get("source_state") != "blocked_user_input":
        raise KernelConflict("Source Run is not blocked on user input")
    blocker = record.get("source_blocker")
    if not isinstance(blocker, Mapping):
        raise ArtifactDrift("blocked Source Run lacks its blocker authority")
    platform = str(record["canonical_platform"])
    resource_class = f"{platform}_download"
    if (
        blocker.get("canonical_platform") != platform
        or blocker.get("resource_class") != resource_class
    ):
        raise ArtifactDrift("Source blocker differs from Run platform authority")
    expected_breaker_key = f"platform:{platform}:{resource_class}"
    breakers = [
        item
        for item in kernel.resource_circuit_breaker_status()
        if item.get("breaker_key") == expected_breaker_key
    ]
    if len(breakers) != 1 or breakers[0].get("state") != "closed":
        raise KernelConflict("Source platform breaker is still open or not closed")
    closed_breaker = breakers[0]

    if not isinstance(credential_evidence, Mapping):
        raise ContractError("Source credential resolution evidence must be an object")
    try:
        evidence_bytes = canonical_json_bytes(dict(credential_evidence))
        evidence = json.loads(evidence_bytes.decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ContractError(
            "Source credential resolution evidence is not canonical JSON"
        ) from exc
    kernel.contracts.validate("source-credential-resolution-evidence", evidence)
    computed_evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
    if computed_evidence_sha256 != credential_evidence_sha256:
        raise ContractError(
            "Source credential resolution evidence fingerprint differs from its "
            "canonical content"
        )
    expected_evidence_bindings = {
        "run_id": record["run_id"],
        "source_epoch": record["source_epoch"],
        "canonical_platform": platform,
        "resource_class": resource_class,
        "breaker_key": expected_breaker_key,
        "breaker_updated_seq": closed_breaker["updated_seq"],
        "blocker_evidence_sha256": blocker["evidence_sha256"],
        "authentication_classification": authentication_classification,
    }
    if any(
        evidence.get(field) != expected
        for field, expected in expected_evidence_bindings.items()
    ):
        raise ContractError(
            "Source credential resolution evidence does not bind the current "
            "blocked Run and closed platform breaker"
        )

    store = getattr(kernel, "control_store", None)
    if store is None:
        raise KernelConflict("Source user-input resolution has no durable authority")
    prior_sha = sha256_file(run_path)
    if store.current_run_record_sha(record["run_id"]) != prior_sha:
        raise ArtifactDrift(
            "Run Record differs from its committed authority predecessor",
            data={"drifted_paths": ["workflow/run.json"]},
        )
    replacement = json.loads(json.dumps(record))
    replacement["source_epoch"] = int(record["source_epoch"]) + 1
    replacement["source_state"] = "pending"
    replacement["source_version"] = None
    replacement["source_blocker"] = None
    replacement["phase"] = "source_acquisition"
    for checkpoint_name, checkpoint in replacement["checkpoints"].items():
        if checkpoint_name != "run_initialized":
            checkpoint["status"] = "stale"
    replacement["coordination_revision"] = int(record["coordination_revision"]) + 1
    mutation_id = store.derive_run_state_mutation_id(
        run_id=record["run_id"],
        expected_run_revision=record["coordination_revision"],
        old_run_record_sha256=prior_sha,
    )
    replacement["last_mutation_intent_id"] = mutation_id
    evidence_directory = require_contained_path(
        run_dir / "待删除" / "source-blocker-resolutions" / mutation_id,
        run_dir,
        purpose="Source credential resolution evidence directory",
        error_type=ArtifactDrift,
        allow_missing=True,
    )
    evidence_directory.mkdir(parents=True, exist_ok=True)
    require_contained_path(
        evidence_directory,
        run_dir,
        purpose="Source credential resolution evidence directory",
        error_type=ArtifactDrift,
        leaf_kind="directory",
    )
    evidence_path = require_contained_path(
        evidence_directory / "credential-evidence.json",
        run_dir,
        purpose="Source credential resolution evidence",
        error_type=ArtifactDrift,
        allow_missing=True,
    )
    if evidence_path.exists():
        require_contained_path(
            evidence_path,
            run_dir,
            purpose="Source credential resolution evidence",
            error_type=ArtifactDrift,
            leaf_kind="file",
            require_single_link=True,
        )
        try:
            existing_evidence = read_json(evidence_path)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ArtifactDrift(
                "Source credential resolution evidence is unreadable"
            ) from exc
        if (
            existing_evidence != evidence
            or sha256_file(evidence_path) != credential_evidence_sha256
        ):
            raise ArtifactDrift(
                "Source credential resolution evidence differs from its intent"
            )
    else:
        written_evidence_sha256 = write_json_atomic(evidence_path, evidence)
        if written_evidence_sha256 != credential_evidence_sha256:
            raise ArtifactDrift(
                "Source credential resolution evidence changed during persistence"
            )
        require_contained_path(
            evidence_path,
            run_dir,
            purpose="Source credential resolution evidence",
            error_type=ArtifactDrift,
            leaf_kind="file",
            require_single_link=True,
        )
    evidence_relative = evidence_path.relative_to(run_dir).as_posix()
    previous_evidence = replacement["artifact_generations"].get(
        "source_credential_resolution_evidence"
    )
    replacement["artifact_generations"][
        "source_credential_resolution_evidence"
    ] = {
        "path": evidence_relative,
        "generation": (
            1
            if previous_evidence is None
            else int(previous_evidence["generation"]) + 1
        ),
        "sha256": credential_evidence_sha256,
        "producer": "kernel:source-blocker-resolve",
        "committed_at": evidence["verified_at"],
        "source_epoch": replacement["source_epoch"],
    }
    kernel.contracts.validate_run_record(replacement)
    if fault_point == "after_resolution_evidence_written":
        raise SourceUserInputResolutionFault(fault_point)
    mutation = store.prepare_run_state_mutation(
        run_id=record["run_id"],
        expected_run_revision=record["coordination_revision"],
        old_run_record_sha256=prior_sha,
        replacement_run_record=replacement,
    )
    if fault_point == "after_resolution_mutation_prepared":
        raise SourceUserInputResolutionFault(fault_point)
    if sha256_file(run_path) != mutation["old_run_record_sha256"]:
        raise KernelConflict("Run Record changed after user-input resolution preparation")
    replacement_sha = write_json_atomic(run_path, replacement)
    if replacement_sha != mutation["replacement_run_record_sha256"]:
        raise KernelConflict("Source user-input resolution fingerprint changed")
    if fault_point == "after_resolution_run_record_written":
        raise SourceUserInputResolutionFault(fault_point)
    store.commit_run_state_mutation(mutation_id)
    if store.current_run_record_sha(record["run_id"]) != replacement_sha:
        raise ArtifactDrift(
            "Source user-input resolution Control Store commit marker is stale"
        )
    return {
        "classification": "source_user_input_resolved",
        "run_id": record["run_id"],
        "canonical_platform": platform,
        "authentication_classification": authentication_classification,
        "credential_evidence_sha256": credential_evidence_sha256,
        "credential_evidence_path": evidence_relative,
        "source_epoch": replacement["source_epoch"],
        "coordination_revision": replacement["coordination_revision"],
    }


def derive_source_identity(canonical_platform: str, canonical_item_id: str) -> str:
    """Return the stable platform/item authority shared by fresh and import modes."""

    if canonical_platform not in PRODUCTION_PLATFORMS:
        raise ContractError(f"unsupported production source platform: {canonical_platform}")
    if not canonical_item_id or canonical_item_id.strip() != canonical_item_id:
        raise ContractError("canonical source item identity is invalid")
    value = {
        "canonical_item_id": canonical_item_id,
        "canonical_platform": canonical_platform,
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def derive_source_version(
    source_identity: str, artifacts: Sequence[SourceArtifactBinding]
) -> str:
    """Fingerprint published original evidence without run-local provenance."""

    if _SHA256.fullmatch(source_identity) is None:
        raise ContractError("source identity SHA-256 is invalid")
    if not artifacts:
        raise ContractError("source version requires published original evidence")
    logical_ids = [artifact.logical_id for artifact in artifacts]
    if len(logical_ids) != len(set(logical_ids)):
        raise ContractError("source version artifact identities must be unique")
    ordered = sorted(
        (artifact.version_value() for artifact in artifacts),
        key=lambda item: item["logical_id"],
    )
    value = {
        "canonicalization": SOURCE_VERSION_CANONICALIZATION,
        "source_identity": source_identity,
        "artifacts": ordered,
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _normalized_language(value: str) -> str:
    return value.strip().replace("_", "-").lower()


def build_allowed_source_judgment(
    candidates: Sequence[SubtitleCandidate],
    *,
    english_primary: bool,
    whisper_allowed: bool,
    whisper_audio_candidate_id: str | None,
) -> dict[str, Any]:
    """Materialize the closed choices exposed to the semantic source role."""

    usable = [candidate for candidate in candidates if candidate.technically_usable]
    if english_primary:
        usable = [
            candidate
            for candidate in usable
            if _normalized_language(candidate.language).split("-", 1)[0] == "en"
        ]
    usable.sort(
        key=lambda candidate: (
            0 if candidate.subtitle_kind == "manual" else 1,
            _normalized_language(candidate.language),
            candidate.candidate_id,
        )
    )
    if whisper_allowed:
        if not whisper_audio_candidate_id:
            raise ContractError("Whisper policy requires a declared audio candidate")
        whisper_choices = ["not_required", "use_whisper", "unavailable"]
    else:
        if whisper_audio_candidate_id is not None:
            raise ContractError("disabled Whisper policy cannot expose an audio candidate")
        whisper_choices = ["not_required", "unavailable"]
    return {
        "subtitle_candidate_ids": [candidate.candidate_id for candidate in usable],
        "whisper_choices": whisper_choices,
        "whisper_audio_candidate_id": whisper_audio_candidate_id,
        "known_gap_codes": [
            "metadata_incomplete",
            "missing_audio",
            "missing_cover",
            "missing_subtitles",
            "other",
            "partial_subtitles",
            "subtitle_quality",
        ],
    }


def validate_source_judgment(
    allowed: Mapping[str, Any], judgment: Mapping[str, Any]
) -> None:
    """Validate semantic choices without accepting any mechanical fields."""

    expected_fields = {
        "selected_subtitle_candidate_id",
        "subtitle_selection_rationale",
        "whisper_fallback",
        "known_gaps",
    }
    if set(judgment) != expected_fields:
        raise ContractError("Source Acquisition Judgment contains mechanical or missing fields")
    selection = judgment["selected_subtitle_candidate_id"]
    if selection is not None and selection not in allowed["subtitle_candidate_ids"]:
        raise ContractError("selected subtitle candidate is not allowed by the decision skeleton")
    rationale = judgment["subtitle_selection_rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise ContractError("subtitle selection rationale is required")
    fallback = judgment["whisper_fallback"]
    if not isinstance(fallback, Mapping) or set(fallback) != {"choice", "rationale"}:
        raise ContractError("Whisper fallback judgment is invalid")
    choice = fallback["choice"]
    if choice not in allowed["whisper_choices"]:
        raise ContractError("Whisper fallback choice is not allowed by the decision skeleton")
    if not isinstance(fallback["rationale"], str) or not fallback["rationale"].strip():
        raise ContractError("Whisper fallback rationale is required")
    gaps = judgment["known_gaps"]
    if not isinstance(gaps, list):
        raise ContractError("known source gaps must be an array")
    for gap in gaps:
        if not isinstance(gap, Mapping) or set(gap) != {
            "code",
            "description",
            "affected_ranges",
        }:
            raise ContractError("known source gap is invalid")
        if gap["code"] not in allowed["known_gap_codes"]:
            raise ContractError("known source gap code is not allowed by the decision skeleton")
    if choice == "not_required" and selection is None:
        raise ContractError("not_required Whisper fallback requires a subtitle selection")
    if choice == "use_whisper":
        if selection is not None or not allowed["whisper_audio_candidate_id"]:
            raise ContractError("use_whisper requires the declared audio candidate and no subtitle")
    if choice == "unavailable" and not gaps:
        raise ContractError("unavailable source fallback requires an explicit known gap")

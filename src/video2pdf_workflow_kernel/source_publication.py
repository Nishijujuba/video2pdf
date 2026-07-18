from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .contracts import ContractRegistry
from .errors import ArtifactDrift, ContractError, KernelConflict, KernelError
from .source_package import MaterializedSourcePackage
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_bytes,
    sha256_file,
    write_json_atomic,
)


SOURCE_PUBLICATION_FAULT_POINTS = frozenset(
    {
        "after_source_publication_intent_prepared",
        "after_source_publication_journal_written",
        "after_source_publication_journal_bound",
        "after_prior_source_preserved",
        "after_source_tree_published",
        "after_source_files_state_commit",
        "after_source_run_record_commit_marker",
        "after_source_record_state_commit",
        "before_source_publication_intent_commit",
        "after_source_publication_intent_commit",
    }
)
JOURNAL_PATH = PurePosixPath(
    "work/source-acquisition/source-publication-journal.json"
)


class SourcePublicationFault(KernelError):
    classification = "injected_source_publication_fault"
    exit_code = 61


@dataclass(frozen=True)
class SourcePublicationResult:
    intent_id: str
    run_dir: Path
    manifest_path: Path
    manifest_sha256: str
    source_identity: str
    source_version: str


class SourcePublicationControlAuthority:
    """Narrow adapter over the durable Source Publication authority."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def intent_id(self, record: Mapping[str, Any], prior_sha256: str) -> str:
        return self._store.derive_source_publication_intent_id(
            run_id=record["run_id"],
            source_epoch=record["source_epoch"],
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=prior_sha256,
        )

    def prepare(
        self,
        record: dict[str, Any],
        replacement: dict[str, Any],
        manifest: dict[str, Any],
        prior_sha256: str,
        manifest_sha256: str,
    ) -> Any:
        return self._store.prepare_source_publication(
            run_id=record["run_id"],
            source_epoch=record["source_epoch"],
            expected_run_revision=record["coordination_revision"],
            old_run_record_sha256=prior_sha256,
            source_manifest_sha256=manifest_sha256,
            source_identity=manifest["source_identity"],
            source_version=manifest["source_version"],
            replacement_run_record=replacement,
        )

    def bind_journal(self, intent_id: str, journal_sha256: str) -> None:
        self._store.bind_source_publication_journal(intent_id, journal_sha256)

    def transition(
        self, intent_id: str, expected_state: str, new_state: str
    ) -> None:
        self._store.transition_source_publication(
            intent_id,
            expected_state=expected_state,
            new_state=new_state,
        )

    def commit(self, intent_id: str) -> None:
        self._store.commit_source_publication(intent_id)

    def current_run_record_sha(self, run_id: str) -> str | None:
        return self._store.current_run_record_sha(run_id)

    def active(self, run_id: str) -> Any | None:
        return self._store.active_source_publication(run_id)


class SourcePublicationSaga:
    """Publish one validated candidate Source tree with recoverable fencing."""

    def __init__(
        self,
        run_dir: Path,
        *,
        contracts: ContractRegistry | None = None,
        authority: Any,
    ) -> None:
        self.run_dir = Path(os.path.abspath(run_dir))
        require_contained_path(
            self.run_dir,
            self.run_dir,
            purpose="Source publication Run root",
            error_type=ArtifactDrift,
            leaf_kind="directory",
        )
        self.run_path = self.run_dir / "workflow/run.json"
        self.journal_path = self.run_dir.joinpath(*JOURNAL_PATH.parts)
        self.contracts = contracts or ContractRegistry(
            Path(__file__).resolve().parents[2]
        )
        self.authority = authority

    @staticmethod
    def _inject(selected: str | None, current: str) -> None:
        if selected == current:
            raise SourcePublicationFault(current)

    def _record(self) -> tuple[dict[str, Any], str]:
        require_contained_path(
            self.run_path,
            self.run_dir,
            purpose="Source publication Run Record",
            error_type=ArtifactDrift,
            leaf_kind="file",
            require_single_link=True,
        )
        record = read_json(self.run_path)
        self.contracts.validate_run_record(record)
        if record.get("schema_version") != "3.0.0":
            raise ContractError("Source publication requires Run Record v3")
        if Path(record["output_path"]).resolve() != self.run_dir:
            raise KernelConflict("Source publication Run path binding differs")
        return record, sha256_file(self.run_path)

    def publication_intent_id(self) -> str:
        record, prior_sha = self._record()
        return self.authority.intent_id(record, prior_sha)

    def materialization_published_at(self, requested: str) -> str:
        """Return the timestamp frozen by a pre-materialization Intent replay."""

        record, prior_sha = self._record()
        active = self.authority.active(record["run_id"])
        if active is None:
            return requested
        intent_id = self.authority.intent_id(record, prior_sha)
        if (
            active["intent_id"] != intent_id
            or active["state"] != "PREPARED"
            or active["predecessor_committed_sha256"] != prior_sha
        ):
            raise ArtifactDrift(
                "active Source publication cannot resume candidate materialization"
            )
        try:
            replacement = json.loads(str(active["replacement_run_record_json"]))
            self.contracts.validate_run_record(replacement)
            generation = replacement["artifact_generations"]["source_manifest"]
            checkpoint = replacement["checkpoints"]["source_ready"]
            published_at = generation["committed_at"]
        except (ContractError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ArtifactDrift(
                "Source publication lacks its frozen materialization timestamp"
            ) from exc
        if (
            not isinstance(published_at, str)
            or checkpoint.get("completed_at") != published_at
            or generation.get("sha256") != active["source_manifest_sha256"]
        ):
            raise ArtifactDrift(
                "Source publication frozen materialization evidence drifted"
            )
        return published_at

    def candidate_source_root(self, intent_id: str) -> Path:
        return (
            self.run_dir
            / "work/source-acquisition/publications"
            / intent_id
            / "candidate/source"
        )

    def _candidate_relative_root(self, intent_id: str) -> str:
        return f"work/source-acquisition/publications/{intent_id}/candidate/source"

    def _load_candidate_package(self, intent_id: str) -> MaterializedSourcePackage:
        root = self.candidate_source_root(intent_id)
        manifest_path = root / "manifest.json"
        require_contained_path(
            manifest_path,
            self.run_dir,
            purpose="Source publication candidate Manifest",
            error_type=ArtifactDrift,
            leaf_kind="file",
            require_single_link=True,
        )
        manifest = read_json(manifest_path)
        self.contracts.validate("source-manifest", manifest)
        manifest_bytes = canonical_json_bytes(manifest)
        if manifest_path.read_bytes() != manifest_bytes:
            raise ArtifactDrift("Source publication candidate Manifest is not canonical")
        artifact_paths = tuple(
            root.joinpath(
                *PurePosixPath(artifact["path"]).relative_to("source").parts
            )
            for artifact in manifest["artifacts"]
        )
        return MaterializedSourcePackage(
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_sha256=sha256_bytes(manifest_bytes),
            manifest_path=manifest_path,
            artifact_paths=artifact_paths,
        )

    def _verify_package(
        self,
        record: dict[str, Any],
        package: MaterializedSourcePackage,
        intent_id: str,
        *,
        require_materialized: bool,
    ) -> None:
        expected_root = self.candidate_source_root(intent_id)
        expected_manifest = expected_root / "manifest.json"
        if Path(os.path.abspath(package.manifest_path)) != expected_manifest:
            raise ContractError("Source Package candidate root differs from Intent identity")
        require_contained_path(
            package.manifest_path,
            self.run_dir,
            purpose="Source Package candidate Manifest",
            error_type=ArtifactDrift,
            leaf_kind="file",
            allow_missing=not require_materialized,
            require_single_link=True,
        )
        manifest = package.manifest
        self.contracts.validate("source-manifest", manifest)
        canonical = canonical_json_bytes(manifest)
        if (
            package.manifest_bytes != canonical
            or package.manifest_sha256 != sha256_bytes(canonical)
            or (
                require_materialized
                and package.manifest_path.read_bytes() != canonical
            )
        ):
            raise ArtifactDrift("Source Package Manifest fingerprint drifted")
        expected_identity = {
            "run_id": record["run_id"],
            "source_epoch": record["source_epoch"],
            "canonical_platform": record["canonical_platform"],
            "canonical_item_id": record["canonical_item_id"],
            "source_identity": record["source_identity"],
            "mode": record["source_acquisition_mode"],
        }
        if any(manifest.get(key) != value for key, value in expected_identity.items()):
            raise ContractError("Source Package identity differs from current Run")
        generations = record["artifact_generations"]
        provenance = manifest["provenance"]
        if manifest["mode"] == "fresh_download":
            required = {
                "candidate_inventory_sha256": "source_candidate_inventory",
                "decision_skeleton_sha256": "source_acquisition_decision_skeleton",
            }
            if any(
                generations.get(logical_id, {}).get("sha256") != provenance[field]
                for field, logical_id in required.items()
            ):
                raise ArtifactDrift("Source Package mechanical controls are stale")
            decision = generations.get("source_acquisition_decision")
            if (
                decision is None
                or provenance["judgment_patch"]["sha256"] != decision["sha256"]
                or record["checkpoints"].get(
                    "source_acquisition_decision_ready", {}
                ).get("status")
                != "current"
            ):
                raise ArtifactDrift("Source Package semantic Decision is stale")
        else:
            inventory_path = (
                self.run_dir
                / "work/source-acquisition/candidate-inventory.json"
            )
            try:
                inventory = read_json(inventory_path)
                self.contracts.validate("source-candidate-inventory", inventory)
                inventory_sha256 = sha256_file(inventory_path)
            except (ContractError, OSError, ValueError) as exc:
                raise ArtifactDrift(
                    "Verified Import Candidate Inventory is invalid"
                ) from exc
            if (
                inventory_sha256 != provenance["candidate_inventory_sha256"]
                or inventory["run_id"] != record["run_id"]
                or inventory["source_epoch"] != record["source_epoch"]
                or inventory["source_identity"] != record["source_identity"]
                or inventory["mode"] != "verified_import"
            ):
                raise ArtifactDrift(
                    "Verified Import Candidate Inventory authority is stale"
                )
            candidate_checkpoint = record["checkpoints"].get(
                "source_candidates_ready"
            )
            candidate_generation = generations.get("source_candidate_inventory")
            if candidate_checkpoint is None or candidate_checkpoint.get("status") != "current":
                if record["source_state"] != "pending" or candidate_generation is not None:
                    raise ArtifactDrift("Verified Import candidate checkpoint is stale")
            elif (
                candidate_generation is None
                or candidate_generation["sha256"] != inventory_sha256
            ):
                raise ArtifactDrift("Verified Import candidate generation is stale")
        expected_paths = {Path(os.path.abspath(package.manifest_path))}
        manifest_artifacts = {
            artifact["logical_id"]: artifact for artifact in manifest["artifacts"]
        }
        if len(package.artifact_paths) != len(manifest_artifacts):
            raise ArtifactDrift("Source Package artifact path set is incomplete")
        for path in package.artifact_paths:
            try:
                relative = Path(os.path.abspath(path)).relative_to(
                    expected_root
                ).as_posix()
            except ValueError as exc:
                raise ContractError("Source Package artifact escapes candidate root") from exc
            match = next(
                (
                    artifact
                    for artifact in manifest["artifacts"]
                    if PurePosixPath(artifact["path"]).relative_to("source").as_posix()
                    == relative
                ),
                None,
            )
            checked_path = require_contained_path(
                path,
                self.run_dir,
                purpose="Source Package candidate artifact",
                error_type=ArtifactDrift,
                leaf_kind="file",
                allow_missing=not require_materialized,
                require_single_link=True,
            )
            if match is None or (
                require_materialized
                and (
                    sha256_file(checked_path) != match["sha256"]
                    or path.stat().st_size != match["size_bytes"]
                )
            ):
                raise ArtifactDrift("Source Package artifact fingerprint drifted")
            expected_paths.add(Path(os.path.abspath(path)))
        if require_materialized:
            entries = tuple(expected_root.rglob("*"))
            for path in entries:
                require_contained_path(
                    path,
                    self.run_dir,
                    purpose="Source Package candidate tree entry",
                    error_type=ArtifactDrift,
                )
            actual_paths = {
                path for path in entries if path.is_file()
            }
            if actual_paths != expected_paths:
                raise ArtifactDrift("Source Package candidate inventory is not closed")

    def _prior_manifest_sha(self, record: dict[str, Any]) -> str | None:
        generation = record["artifact_generations"].get("source_manifest")
        if generation is not None:
            return str(generation["sha256"])
        journals = sorted(
            (self.run_dir / "待删除/source-reopens").glob("*/reopen.json")
        )
        for path in reversed(journals):
            value = read_json(path)
            replacement = value.get("replacement_run_record", {})
            if replacement.get("source_epoch") == record["source_epoch"]:
                return value.get("source_manifest_sha256")
        return None

    def _replacement(
        self,
        record: dict[str, Any],
        package: MaterializedSourcePackage,
        intent_id: str,
    ) -> dict[str, Any]:
        replacement = deepcopy(record)
        manifest = package.manifest
        previous = replacement["artifact_generations"].get("source_manifest")
        generation = 1 if previous is None else int(previous["generation"]) + 1
        replacement["source_acquisition_mode"] = manifest["mode"]
        replacement["source_version"] = manifest["source_version"]
        replacement["source_state"] = "ready"
        replacement["source_blocker"] = None
        replacement["phase"] = "source_ready"
        replacement["coordination_revision"] = int(record["coordination_revision"]) + 1
        replacement["last_mutation_intent_id"] = intent_id
        if manifest["mode"] == "verified_import" and (
            "source_candidate_inventory" not in replacement["artifact_generations"]
        ):
            inventory_sha256 = manifest["provenance"][
                "candidate_inventory_sha256"
            ]
            replacement["artifact_generations"]["source_candidate_inventory"] = {
                "path": "work/source-acquisition/candidate-inventory.json",
                "generation": 1,
                "sha256": inventory_sha256,
                "producer": "kernel:verified-import",
                "committed_at": manifest["published_at"],
                "source_epoch": record["source_epoch"],
            }
            initialized = replacement["checkpoints"].get("run_initialized")
            if initialized is None or initialized["status"] != "current":
                raise ArtifactDrift(
                    "Verified Import initialization checkpoint is stale"
                )
            replacement["checkpoints"]["source_candidates_ready"] = {
                "status": "current",
                "artifact_bindings": [
                    {
                        "logical_id": "source_candidate_inventory",
                        "generation": 1,
                        "sha256": inventory_sha256,
                    }
                ],
                "prerequisite_bindings": [
                    {
                        "checkpoint": "run_initialized",
                        "evidence_sha256": initialized["evidence_sha256"],
                    }
                ],
                "evidence_sha256": sha256_bytes(
                    canonical_json_bytes(
                        [
                            {
                                "logical_id": "source_candidate_inventory",
                                "sha256": inventory_sha256,
                            }
                        ]
                    )
                ),
                "completed_at": manifest["published_at"],
            }
        replacement["artifact_generations"]["source_manifest"] = {
            "path": "source/manifest.json",
            "generation": generation,
            "sha256": package.manifest_sha256,
            "producer": "kernel:source-finalize",
            "committed_at": manifest["published_at"],
            "source_epoch": record["source_epoch"],
        }
        prerequisite = (
            "source_acquisition_decision_ready"
            if manifest["mode"] == "fresh_download"
            else "source_candidates_ready"
        )
        binding_ids = ["source_candidate_inventory"]
        if manifest["mode"] == "fresh_download":
            binding_ids.append("source_acquisition_decision")
        if (
            manifest["selection"]["whisper_status"] == "used"
            and "source_transcription" in replacement["artifact_generations"]
        ):
            binding_ids.append("source_transcription")
        binding_ids.append("source_manifest")
        replacement["checkpoints"]["source_ready"] = {
            "status": "current",
            "artifact_bindings": [
                {
                    "logical_id": logical_id,
                    "generation": replacement["artifact_generations"][logical_id][
                        "generation"
                    ],
                    "sha256": replacement["artifact_generations"][logical_id][
                        "sha256"
                    ],
                }
                for logical_id in binding_ids
            ],
            "prerequisite_bindings": [
                {
                    "checkpoint": prerequisite,
                    "evidence_sha256": replacement["checkpoints"][prerequisite][
                        "evidence_sha256"
                    ],
                }
            ],
            "evidence_sha256": package.manifest_sha256,
            "completed_at": manifest["published_at"],
        }
        self.contracts.validate_run_record(replacement)
        return replacement

    def _journal(
        self,
        record: dict[str, Any],
        replacement: dict[str, Any],
        package: MaterializedSourcePackage,
        intent_id: str,
        prior_run_sha: str,
    ) -> dict[str, Any]:
        candidate_root = self._candidate_relative_root(intent_id)
        preservation_root = f"待删除/source-publications/{intent_id}/previous/source"
        artifact_by_path = {
            artifact["path"]: artifact for artifact in package.manifest["artifacts"]
        }
        output_specs = [
            (
                "source_manifest",
                f"{candidate_root}/manifest.json",
                "source/manifest.json",
                package.manifest_sha256,
            ),
            *[
                (
                    artifact["logical_id"],
                    f"{candidate_root}/{PurePosixPath(artifact['path']).relative_to('source').as_posix()}",
                    artifact["path"],
                    artifact["sha256"],
                )
                for artifact in artifact_by_path.values()
            ],
        ]
        outputs = []
        for logical_id, candidate_path, canonical_path, output_sha in output_specs:
            canonical = self.run_dir.joinpath(*PurePosixPath(canonical_path).parts)
            require_contained_path(
                canonical,
                self.run_dir,
                purpose="Source publication canonical output",
                error_type=ArtifactDrift,
                leaf_kind="file",
                allow_missing=True,
                require_single_link=True,
            )
            prior_sha = sha256_file(canonical) if canonical.is_file() else None
            relative = PurePosixPath(canonical_path).relative_to("source").as_posix()
            outputs.append(
                {
                    "logical_id": logical_id,
                    "candidate_path": candidate_path,
                    "canonical_path": canonical_path,
                    "sha256": output_sha,
                    "prior_sha256": prior_sha,
                    "preservation_path": f"{preservation_root}/{relative}",
                }
            )
        outputs.sort(key=lambda item: item["logical_id"])
        prior_manifest = self._prior_manifest_sha(record)
        publication_kind = (
            "initial_publish"
            if record["source_epoch"] == 1 and prior_manifest is None
            else "reopen_publish"
        )
        journal = {
            "schema_name": "source-publication-journal",
            "schema_version": "1.0.0",
            "kernel_version": "2.0.0",
            "intent_id": intent_id,
            "run_id": record["run_id"],
            "source_epoch": record["source_epoch"],
            "mode": package.manifest["mode"],
            "publication_kind": publication_kind,
            "expected_run_revision": record["coordination_revision"],
            "prior_run_record_sha256": prior_run_sha,
            "replacement_run_record_sha256": sha256_bytes(
                canonical_json_bytes(replacement)
            ),
            "source_identity": package.manifest["source_identity"],
            "source_version": package.manifest["source_version"],
            "candidate_source_root": candidate_root,
            "canonical_source_root": "source",
            "preservation_root": preservation_root,
            "prior_source_manifest_sha256": prior_manifest,
            "replacement_source_manifest_sha256": package.manifest_sha256,
            "coordination_record_path": "workflow/run.json",
            "outputs": outputs,
            "state": "PREPARED",
        }
        self.contracts.validate("source-publication-journal", journal)
        return journal

    def _preflight_publication_paths(self, journal: Mapping[str, Any]) -> None:
        """Validate every publication read, write, and move path as one batch."""

        journal_preservation = (
            self.run_dir
            / "待删除/source-publications"
            / str(journal["intent_id"])
            / "previous/source-publication-journal.json"
        )
        paths = (
            (self.run_path, "Run Record", "file", False, True),
            (
                self.run_path.with_name(f".{self.run_path.name}.kernel-new"),
                "Run Record atomic staging",
                "file",
                True,
                True,
            ),
            (self.journal_path, "publication journal", "file", True, True),
            (
                self.journal_path.with_name(
                    f".{self.journal_path.name}.kernel-new"
                ),
                "publication journal atomic staging",
                "file",
                True,
                True,
            ),
            (
                journal_preservation,
                "publication journal preservation",
                "file",
                True,
                True,
            ),
        )
        for path, label, leaf_kind, allow_missing, single_link in paths:
            require_contained_path(
                path,
                self.run_dir,
                purpose=f"Source {label}",
                error_type=ArtifactDrift,
                leaf_kind=leaf_kind,
                allow_missing=allow_missing,
                require_single_link=single_link,
            )
        write_root = (
            self.run_dir
            / "待删除/source-publications"
            / str(journal["intent_id"])
            / "writes"
        )
        require_contained_path(
            write_root,
            self.run_dir,
            purpose="Source publication write staging root",
            error_type=ArtifactDrift,
            leaf_kind="directory",
            allow_missing=True,
        )
        for output in journal["outputs"]:
            candidate = self.run_dir.joinpath(
                *PurePosixPath(output["candidate_path"]).parts
            )
            canonical = self.run_dir.joinpath(
                *PurePosixPath(output["canonical_path"]).parts
            )
            preservation = self.run_dir.joinpath(
                *PurePosixPath(output["preservation_path"]).parts
            )
            relative = PurePosixPath(output["canonical_path"]).relative_to("source")
            staged = write_root.joinpath(*relative.parts)
            for path, label, allow_missing in (
                (candidate, "candidate output", False),
                (canonical, "canonical output", True),
                (preservation, "preservation output", True),
                (staged, "write staging output", True),
            ):
                require_contained_path(
                    path,
                    self.run_dir,
                    purpose=f"Source publication {label}",
                    error_type=ArtifactDrift,
                    leaf_kind="file",
                    allow_missing=allow_missing,
                    require_single_link=True,
                )

    def _write_journal(self, journal: dict[str, Any]) -> str:
        self._preflight_publication_paths(journal)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        if self.journal_path.exists():
            existing = read_json(self.journal_path)
            if existing == journal:
                return sha256_file(self.journal_path)
            if existing.get("intent_id") == journal["intent_id"]:
                raise ArtifactDrift("Source publication journal drifted")
            preserved = (
                self.run_dir
                / "待删除/source-publications"
                / journal["intent_id"]
                / "previous/source-publication-journal.json"
            )
            preserved.parent.mkdir(parents=True, exist_ok=True)
            if preserved.exists():
                if read_json(preserved) != existing:
                    raise ArtifactDrift("prior Source publication journal preservation drifted")
            else:
                os.replace(self.journal_path, preserved)
        return write_json_atomic(self.journal_path, journal)

    def _prepare(
        self,
        package: MaterializedSourcePackage,
        *,
        require_materialized: bool,
    ) -> tuple[dict[str, Any], str, str, dict[str, Any], Any]:
        record, prior_sha = self._record()
        if record["source_state"] not in {
            "pending",
            "candidates_ready",
            "decision_ready",
            "stale",
        }:
            raise KernelConflict(
                "Source publication requires completed acquisition controls"
            )
        if (
            record["source_state"] == "pending"
            and record["source_acquisition_mode"] != "verified_import"
        ):
            raise KernelConflict(
                "pending Source publication is limited to Verified Import"
            )
        intent_id = self.authority.intent_id(record, prior_sha)
        self._verify_package(
            record,
            package,
            intent_id,
            require_materialized=require_materialized,
        )
        replacement = self._replacement(record, package, intent_id)
        durable = self.authority.prepare(
            record,
            replacement,
            package.manifest,
            prior_sha,
            package.manifest_sha256,
        )
        if (
            durable["intent_id"] != intent_id
            or durable["replacement_run_record_sha256"]
            != sha256_bytes(canonical_json_bytes(replacement))
        ):
            raise KernelConflict("Source publication durable authority changed identity")
        return record, prior_sha, intent_id, replacement, durable

    def prepare(self, package: MaterializedSourcePackage) -> Any:
        """Fence one fully planned Source Package before candidate files are written."""

        *_, durable = self._prepare(package, require_materialized=False)
        return durable

    def publish(
        self,
        package: MaterializedSourcePackage,
        *,
        fault_point: str | None = None,
    ) -> SourcePublicationResult:
        if fault_point is not None and fault_point not in SOURCE_PUBLICATION_FAULT_POINTS:
            raise ContractError(f"unknown Source publication fault point: {fault_point}")
        record, prior_sha, intent_id, replacement, durable = self._prepare(
            package,
            require_materialized=True,
        )
        journal = self._journal(
            record, replacement, package, intent_id, prior_sha
        )
        self._preflight_publication_paths(journal)
        self._inject(fault_point, "after_source_publication_intent_prepared")
        journal_sha = self._write_journal(journal)
        self._inject(fault_point, "after_source_publication_journal_written")
        self.authority.bind_journal(intent_id, journal_sha)
        self._inject(fault_point, "after_source_publication_journal_bound")
        return self._advance(
            durable,
            journal,
            replacement,
            package,
            fault_point=fault_point,
        )

    def reconcile(self) -> SourcePublicationResult:
        record, actual_run_sha = self._record()
        active = self.authority.active(record["run_id"])
        if active is None:
            require_contained_path(
                self.journal_path,
                self.run_dir,
                purpose="Source publication journal",
                error_type=ArtifactDrift,
                leaf_kind="file",
                allow_missing=True,
                require_single_link=True,
            )
            if not self.journal_path.is_file():
                raise KernelConflict("Source publication has no durable recovery evidence")
            journal = read_json(self.journal_path)
            self._preflight_publication_paths(journal)
            if (
                actual_run_sha != journal["replacement_run_record_sha256"]
                or self.authority.current_run_record_sha(record["run_id"])
                != actual_run_sha
            ):
                raise ArtifactDrift("committed Source publication evidence is inconsistent")
            package = self._load_candidate_package(journal["intent_id"])
            self._verify_committed(record, package, journal)
            return self._result(journal, package)
        intent_id = str(active["intent_id"])
        package = self._load_candidate_package(intent_id)
        try:
            replacement = json.loads(str(active["replacement_run_record_json"]))
        except json.JSONDecodeError as exc:
            raise ArtifactDrift("Source publication replacement authority is invalid") from exc
        require_contained_path(
            self.journal_path,
            self.run_dir,
            purpose="Source publication journal",
            error_type=ArtifactDrift,
            leaf_kind="file",
            allow_missing=True,
            require_single_link=True,
        )
        if self.journal_path.is_file():
            journal = read_json(self.journal_path)
            if journal.get("intent_id") != intent_id:
                raise ArtifactDrift("active Source publication journal identity differs")
        else:
            if actual_run_sha != active["predecessor_committed_sha256"]:
                raise ArtifactDrift("Source publication lost its prepared journal")
            self._verify_package(
                record,
                package,
                intent_id,
                require_materialized=True,
            )
            journal = self._journal(
                record,
                replacement,
                package,
                intent_id,
                str(active["predecessor_committed_sha256"]),
            )
            journal_sha = self._write_journal(journal)
            self.authority.bind_journal(intent_id, journal_sha)
        self._preflight_publication_paths(journal)
        return self._advance(
            active,
            journal,
            replacement,
            package,
            fault_point=None,
        )

    def _preserve_outputs(self, journal: dict[str, Any]) -> None:
        self._preflight_publication_paths(journal)
        expected_canonical = {
            output["canonical_path"] for output in journal["outputs"]
        }
        source_root = self.run_dir / "source"
        actual_files = {
            path.relative_to(self.run_dir).as_posix()
            for path in source_root.rglob("*")
            if path.is_file()
        }
        if actual_files - expected_canonical:
            raise ArtifactDrift("canonical Source tree contains undeclared prior files")
        for output in journal["outputs"]:
            canonical = self.run_dir.joinpath(
                *PurePosixPath(output["canonical_path"]).parts
            )
            preservation = self.run_dir.joinpath(
                *PurePosixPath(output["preservation_path"]).parts
            )
            prior_sha = output["prior_sha256"]
            if canonical.is_file():
                actual = sha256_file(canonical)
                if actual == output["sha256"]:
                    continue
                if prior_sha is None or actual != prior_sha:
                    raise ArtifactDrift("canonical Source output has an unknown generation")
                preservation.parent.mkdir(parents=True, exist_ok=True)
                if preservation.exists():
                    if not preservation.is_file() or sha256_file(preservation) != prior_sha:
                        raise ArtifactDrift("prior Source output preservation drifted")
                else:
                    os.replace(canonical, preservation)
            elif prior_sha is not None:
                if not preservation.is_file() or sha256_file(preservation) != prior_sha:
                    raise ArtifactDrift("prior Source output disappeared before publication")

    def _publish_outputs(self, journal: dict[str, Any]) -> None:
        self._preflight_publication_paths(journal)
        write_root = (
            self.run_dir
            / "待删除/source-publications"
            / journal["intent_id"]
            / "writes"
        )
        for output in journal["outputs"]:
            candidate = self.run_dir.joinpath(
                *PurePosixPath(output["candidate_path"]).parts
            )
            canonical = self.run_dir.joinpath(
                *PurePosixPath(output["canonical_path"]).parts
            )
            if not candidate.is_file() or sha256_file(candidate) != output["sha256"]:
                raise ArtifactDrift("Source publication candidate output drifted")
            if canonical.is_file():
                if sha256_file(canonical) != output["sha256"]:
                    raise ArtifactDrift("Source publication canonical output drifted")
                continue
            relative = PurePosixPath(output["canonical_path"]).relative_to("source")
            staged = write_root.joinpath(*relative.parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            raw = candidate.read_bytes()
            if staged.exists():
                if not staged.is_file() or sha256_file(staged) != output["sha256"]:
                    raise ArtifactDrift("Source publication write staging drifted")
            else:
                staged.write_bytes(raw)
            canonical.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, canonical)

    def _verify_published_outputs(self, journal: dict[str, Any]) -> None:
        self._preflight_publication_paths(journal)
        for output in journal["outputs"]:
            canonical = self.run_dir.joinpath(
                *PurePosixPath(output["canonical_path"]).parts
            )
            if not canonical.is_file() or sha256_file(canonical) != output["sha256"]:
                raise ArtifactDrift("published Source output fingerprint drifted")

    def _advance(
        self,
        durable: Mapping[str, Any],
        journal: dict[str, Any],
        replacement: dict[str, Any],
        package: MaterializedSourcePackage,
        *,
        fault_point: str | None,
    ) -> SourcePublicationResult:
        self.contracts.validate("source-publication-journal", journal)
        replacement_sha = sha256_bytes(canonical_json_bytes(replacement))
        if (
            durable["intent_id"] != journal["intent_id"]
            or replacement_sha != journal["replacement_run_record_sha256"]
            or durable["replacement_run_record_sha256"] != replacement_sha
        ):
            raise ArtifactDrift("Source publication durable evidence drifted")
        state = str(durable["state"])
        if state == "PREPARED":
            self._preserve_outputs(journal)
            self._inject(fault_point, "after_prior_source_preserved")
            self._publish_outputs(journal)
            self._inject(fault_point, "after_source_tree_published")
            self.authority.transition(
                journal["intent_id"], "PREPARED", "FILES_PUBLISHED"
            )
            state = "FILES_PUBLISHED"
            self._inject(fault_point, "after_source_files_state_commit")
        if state == "FILES_PUBLISHED":
            self._verify_published_outputs(journal)
            actual_run_sha = sha256_file(self.run_path)
            if actual_run_sha == journal["prior_run_record_sha256"]:
                written = write_json_atomic(self.run_path, replacement)
                if written != replacement_sha:
                    raise KernelConflict("Source publication replacement changed")
            elif actual_run_sha != replacement_sha:
                raise ArtifactDrift("Source publication Run Record has an unknown generation")
            self._inject(fault_point, "after_source_run_record_commit_marker")
            self.authority.transition(
                journal["intent_id"], "FILES_PUBLISHED", "RECORD_COMMITTED"
            )
            state = "RECORD_COMMITTED"
            self._inject(fault_point, "after_source_record_state_commit")
        if state == "RECORD_COMMITTED":
            if sha256_file(self.run_path) != replacement_sha:
                raise ArtifactDrift("Source publication lost its Run commit marker")
            self._verify_published_outputs(journal)
            self._inject(fault_point, "before_source_publication_intent_commit")
            self.authority.commit(journal["intent_id"])
            state = "COMMITTED"
            self._inject(fault_point, "after_source_publication_intent_commit")
        if state != "COMMITTED":
            raise ArtifactDrift("Source publication state is unsupported")
        current = read_json(self.run_path)
        self._verify_committed(current, package, journal)
        if (
            self.authority.current_run_record_sha(journal["run_id"])
            != replacement_sha
        ):
            raise ArtifactDrift("Source publication durable Run marker is stale")
        return self._result(journal, package)

    def _verify_committed(
        self,
        record: dict[str, Any],
        package: MaterializedSourcePackage,
        journal: dict[str, Any],
    ) -> None:
        self.contracts.validate_run_record(record)
        if (
            record["source_state"] != "ready"
            or record["source_version"] != package.manifest["source_version"]
            or record["source_identity"] != package.manifest["source_identity"]
            or record["checkpoints"]["source_ready"]["status"] != "current"
            or record["artifact_generations"]["source_manifest"]["sha256"]
            != journal["replacement_source_manifest_sha256"]
        ):
            raise ArtifactDrift("committed Source publication Run authority drifted")
        self._verify_published_outputs(journal)

    def _result(
        self,
        journal: dict[str, Any],
        package: MaterializedSourcePackage,
    ) -> SourcePublicationResult:
        return SourcePublicationResult(
            intent_id=journal["intent_id"],
            run_dir=self.run_dir,
            manifest_path=self.run_dir / "source/manifest.json",
            manifest_sha256=journal["replacement_source_manifest_sha256"],
            source_identity=package.manifest["source_identity"],
            source_version=package.manifest["source_version"],
        )


__all__ = [
    "SOURCE_PUBLICATION_FAULT_POINTS",
    "SourcePublicationControlAuthority",
    "SourcePublicationFault",
    "SourcePublicationResult",
    "SourcePublicationSaga",
]

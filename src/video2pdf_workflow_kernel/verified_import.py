from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters.base import CommandEvidence
from .errors import ArtifactDrift, ContractError
from .source_acquisition import derive_source_identity
from .source_candidates import SourceCandidatePolicy
from .utils import (
    canonical_json_bytes,
    read_json,
    require_contained_path,
    sha256_bytes,
    sha256_file,
)


_VERSION_FIELDS = (
    "logical_id",
    "role",
    "media_type",
    "sha256",
    "size_bytes",
    "language",
    "subtitle_kind",
    "technical_probe",
)


@dataclass(frozen=True)
class VerifiedSourcePackage:
    prior_run_dir: Path
    prior_run_id: str
    manifest: dict[str, Any]
    manifest_sha256: str
    policy: SourceCandidatePolicy
    import_binding: dict[str, Any]
    validation_command: CommandEvidence


def _contained_regular_file(root: Path, relative: str, *, label: str) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or PurePosixPath(relative).is_absolute()
        or PurePosixPath(relative).as_posix() != relative
        or any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts)
    ):
        raise ContractError(f"{label} path is not canonical")
    return require_contained_path(
        root.joinpath(*PurePosixPath(relative).parts),
        root,
        purpose=label,
        error_type=ArtifactDrift,
        leaf_kind="file",
        require_single_link=True,
    )


def _validation_evidence(value: Any) -> dict[str, str]:
    return {
        "status": "pass",
        "evidence_sha256": sha256_bytes(canonical_json_bytes(value)),
    }


def inspect_current_source_package(
    prior_run_dir: Path,
    *,
    contracts: Any,
    validation_command_argv: tuple[str, ...],
) -> VerifiedSourcePackage:
    """Validate one current v2 Source Package for deterministic reuse."""

    lexical = Path(os.path.abspath(prior_run_dir))
    prior_run_dir = require_contained_path(
        lexical,
        lexical,
        purpose="Verified Import prior Run",
        error_type=ArtifactDrift,
        leaf_kind="directory",
    )
    record_path = _contained_regular_file(
        prior_run_dir, "workflow/run.json", label="prior Run Record"
    )
    record = read_json(record_path)
    contracts.validate_run_record(record)
    if record.get("schema_version") != "3.0.0":
        raise ContractError("Verified Import requires a production Run Record v3")
    source_ready = record["checkpoints"].get("source_ready")
    if (
        record["source_state"] != "ready"
        or record["phase"] != "source_ready"
        or not isinstance(source_ready, dict)
        or source_ready.get("status") != "current"
    ):
        raise ArtifactDrift("Verified Import requires a current prior Source Package")

    generations = record["artifact_generations"]
    manifest_generation = generations.get("source_manifest")
    inventory_generation = generations.get("source_candidate_inventory")
    if not isinstance(manifest_generation, dict) or not isinstance(
        inventory_generation, dict
    ):
        raise ArtifactDrift("current prior Source Package lacks import generations")
    if manifest_generation["path"] != "source/manifest.json":
        raise ArtifactDrift("prior Source Manifest path differs from fixed authority")
    if inventory_generation["path"] != (
        "work/source-acquisition/candidate-inventory.json"
    ):
        raise ArtifactDrift("prior Candidate Inventory path differs from fixed authority")

    bindings = {
        item["logical_id"]: item for item in source_ready["artifact_bindings"]
    }
    for logical_id, generation in (
        ("source_manifest", manifest_generation),
        ("source_candidate_inventory", inventory_generation),
    ):
        binding = bindings.get(logical_id)
        if (
            binding is None
            or binding["generation"] != generation["generation"]
            or binding["sha256"] != generation["sha256"]
        ):
            raise ArtifactDrift(
                f"prior source_ready does not bind current {logical_id}"
            )

    manifest_path = _contained_regular_file(
        prior_run_dir, "source/manifest.json", label="prior Source Manifest"
    )
    manifest_sha256 = sha256_file(manifest_path)
    if manifest_sha256 != manifest_generation["sha256"]:
        raise ArtifactDrift("prior Source Manifest fingerprint is stale")
    manifest = read_json(manifest_path)
    contracts.validate("source-manifest", manifest)
    expected_identity = derive_source_identity(
        manifest["canonical_platform"], manifest["canonical_item_id"]
    )
    identity_fields = {
        "run_id": record["run_id"],
        "canonical_platform": record["canonical_platform"],
        "canonical_item_id": record["canonical_item_id"],
        "source_identity": expected_identity,
        "source_version": record["source_version"],
    }
    if any(manifest.get(key) != value for key, value in identity_fields.items()):
        raise ArtifactDrift("prior Source Manifest identity or version is stale")
    if record["source_identity"] != expected_identity:
        raise ArtifactDrift("prior Run Source Identity is stale")
    expected_version = sha256_bytes(
        canonical_json_bytes(manifest["source_version_basis"])
    )
    if manifest["source_version"] != expected_version:
        raise ArtifactDrift("prior Source Version fingerprint is stale")

    version_artifacts = [
        {field: artifact[field] for field in _VERSION_FIELDS}
        for artifact in manifest["artifacts"]
    ]
    if manifest["source_version_basis"] != {
        "canonicalization": "video2pdf-canonical-json-v1",
        "source_identity": expected_identity,
        "artifacts": version_artifacts,
    }:
        raise ArtifactDrift("prior Source Version basis differs from its artifacts")

    expected_source_files = {manifest_path}
    fingerprint_evidence: list[dict[str, Any]] = []
    for artifact in manifest["artifacts"]:
        path = _contained_regular_file(
            prior_run_dir, artifact["path"], label="prior Source artifact"
        )
        if (
            path.stat().st_size != artifact["size_bytes"]
            or sha256_file(path) != artifact["sha256"]
        ):
            raise ArtifactDrift("prior Source artifact fingerprint is stale")
        expected_source_files.add(path)
        fingerprint_evidence.append(
            {
                "logical_id": artifact["logical_id"],
                "path": artifact["path"],
                "sha256": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
            }
        )
    source_entries = tuple((prior_run_dir / "source").rglob("*"))
    for path in source_entries:
        require_contained_path(
            path,
            prior_run_dir,
            purpose="prior Source Package tree entry",
            error_type=ArtifactDrift,
        )
    actual_source_files = {
        path for path in source_entries if path.is_file()
    }
    if actual_source_files != expected_source_files:
        raise ArtifactDrift("prior Source Package contains undeclared files")

    inventory_path = _contained_regular_file(
        prior_run_dir,
        "work/source-acquisition/candidate-inventory.json",
        label="prior Candidate Inventory",
    )
    inventory_sha256 = sha256_file(inventory_path)
    if inventory_sha256 != inventory_generation["sha256"]:
        raise ArtifactDrift("prior Candidate Inventory fingerprint is stale")
    inventory = read_json(inventory_path)
    contracts.validate("source-candidate-inventory", inventory)
    provenance_inventory_sha = manifest["provenance"][
        "candidate_inventory_sha256"
    ]
    if provenance_inventory_sha != inventory_sha256:
        raise ArtifactDrift("prior Manifest Candidate Inventory binding is stale")
    inventory_identity = {
        "run_id": record["run_id"],
        "source_epoch": record["source_epoch"],
        "canonical_platform": record["canonical_platform"],
        "canonical_item_id": record["canonical_item_id"],
        "source_identity": expected_identity,
    }
    if any(inventory.get(key) != value for key, value in inventory_identity.items()):
        raise ArtifactDrift("prior Candidate Inventory identity is stale")

    policy_value = inventory["policy_binding"]
    policy = SourceCandidatePolicy(
        content_classification=policy_value["content_classification"],
        subtitle_language_priority=tuple(
            policy_value["subtitle_language_priority"]
        ),
        whisper_allowed=policy_value["whisper_allowed"],
        policy_id=policy_value["policy_id"],
        version=policy_value["version"],
    )
    if manifest["policy_binding"] != {
        key: policy_value[key] for key in ("policy_id", "version", "sha256")
    }:
        raise ArtifactDrift("prior Source policy binding is stale")

    roles = {artifact["role"] for artifact in manifest["artifacts"]}
    original_only = {
        "allowed_roles": sorted(
            {"metadata", "cover", "video", "audio", "subtitle", "transcript"}
        ),
        "observed_roles": sorted(roles),
        "paths": sorted(artifact["path"] for artifact in manifest["artifacts"]),
    }
    validations = {
        "canonical_identity": _validation_evidence(
            {
                "canonical_platform": manifest["canonical_platform"],
                "canonical_item_id": manifest["canonical_item_id"],
                "source_identity": expected_identity,
            }
        ),
        "schema_compatibility": _validation_evidence(
            {
                "schema_name": manifest["schema_name"],
                "schema_version": manifest["schema_version"],
                "kernel_version": manifest["kernel_version"],
            }
        ),
        "artifact_fingerprints": _validation_evidence(fingerprint_evidence),
        "subtitle_policy": _validation_evidence(
            {
                "prior_policy_binding": policy_value,
                "receiving_policy_binding": policy.binding(),
                "subtitle_languages": manifest["technical_validation"][
                    "subtitle_languages"
                ],
            }
        ),
        "technical_properties": _validation_evidence(
            manifest["technical_validation"]
        ),
        "source_quality": _validation_evidence(
            {
                "package_status": manifest["package_status"],
                "selection": manifest["selection"],
                "known_gaps": manifest["known_gaps"],
            }
        ),
        "original_only": _validation_evidence(original_only),
    }
    import_binding = {
        "prior_run_id": record["run_id"],
        "prior_source_manifest_sha256": manifest_sha256,
        "prior_source_identity": expected_identity,
        "prior_source_version": expected_version,
        "validation": validations,
    }
    argv = validation_command_argv
    if not argv or any(not isinstance(value, str) or not value for value in argv):
        raise ContractError("Verified Import validation command is invalid")
    validation_sha256 = sha256_bytes(canonical_json_bytes(validations))
    validation_command = CommandEvidence(
        operation="verified_import_validation",
        argv=argv,
        argv_sha256=hashlib.sha256("\0".join(argv).encode("utf-8")).hexdigest(),
        returncode=0,
        stdout_sha256=validation_sha256,
        stderr_sha256=sha256_bytes(b""),
    )
    return VerifiedSourcePackage(
        prior_run_dir=prior_run_dir,
        prior_run_id=record["run_id"],
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        policy=policy,
        import_binding=import_binding,
        validation_command=validation_command,
    )


__all__ = ["VerifiedSourcePackage", "inspect_current_source_package"]

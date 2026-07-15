from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tomllib
from typing import Any, Iterator

from .artifact_plan import ARTIFACT_PLAN_BINDINGS

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import SchemaError, ValidationError
    from referencing import Registry, Resource
except ImportError as exc:  # pragma: no cover - exercised by startup environments
    raise RuntimeError(
        "Workflow Kernel requires the locked jsonschema runtime; install "
        "requirements/pylock.video-workflow-runtime.toml"
    ) from exc

from .errors import ContractError, UnknownContractVersion, UnresolvedSchemaReference
from .utils import read_json
from .utils import sha256_file


JSONSCHEMA_VERSION = "4.26.0"
DRAFT = "https://json-schema.org/draft/2020-12/schema"
REGISTRY_RELATIVE_PATH = Path("schemas/video-workflow/registry.v1.json")
RUNTIME_INPUT = Path("requirements/video-workflow-runtime.in")
RUNTIME_LOCK = Path("requirements/pylock.video-workflow-runtime.toml")


@dataclass(frozen=True)
class ContractEntry:
    schema_name: str
    schema_version: str
    schema_id: str
    schema_path: Path
    kind: str
    positive_example: Path | None
    negative_example: Path | None
    invariants: tuple[str, ...]


WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
KNOWN_INVARIANTS = frozenset(
    {
        "artifact-plan-paths-v1",
        "control-store-identity-path-v1",
        "fixture-package-paths-v1",
        "run-record-freshness-v1",
        "scaffold-contract-directories-v1",
        "source-manifest-paths-and-fingerprints-v1",
    }
)


def _validate_project_relative_path(value: str, *, prefix: str | None = None) -> None:
    if not isinstance(value, str) or not value or "\\" in value or re.match(r"^[A-Za-z]:", value):
        raise ContractError(f"path is not a canonical project-relative path: {value!r}")
    pure = PurePosixPath(value)
    parts = pure.parts
    if (
        pure.is_absolute()
        or not parts
        or pure.as_posix() != value
        or any(part in {".", ".."} for part in parts)
    ):
        raise ContractError(f"path is not a canonical project-relative path: {value!r}")
    for part in parts:
        if part.endswith((" ", ".")) or ":" in part:
            raise ContractError(f"path contains an unsupported Windows component: {value!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_DEVICE_NAMES:
            raise ContractError(f"path contains a reserved Windows device name: {value!r}")
    if prefix is not None and (not parts or parts[0] != prefix):
        raise ContractError(f"path must stay under {prefix}/: {value!r}")


def _validate_canonical_absolute_path(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ContractError(f"path is not canonical absolute: {value!r}")
    if re.match(r"^[A-Za-z]:", value) or value.startswith("\\\\"):
        pure: PureWindowsPath | PurePosixPath = PureWindowsPath(value)
    else:
        pure = PurePosixPath(value)
    if not pure.is_absolute() or str(pure) != value:
        raise ContractError(f"path is not canonical absolute: {value!r}")
    for part in pure.parts[1:]:
        if part in {".", ".."} or part.endswith((" ", ".")):
            raise ContractError(f"absolute path contains a noncanonical component: {value!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_DEVICE_NAMES:
            raise ContractError(f"absolute path contains a reserved component: {value!r}")


def _validate_artifact_plan(instance: dict[str, Any]) -> None:
    expected = {
        binding.logical_id: (
            binding.path,
            binding.schema_name,
            binding.generator,
            binding.earliest_checkpoint,
        )
        for binding in ARTIFACT_PLAN_BINDINGS
    }
    logical_ids: set[str] = set()
    paths: set[str] = set()
    for artifact in instance["artifacts"]:
        logical_id = artifact["logical_id"]
        path = artifact["path"]
        _validate_project_relative_path(path)
        if logical_id in logical_ids or path in paths:
            raise ContractError("Artifact Plan logical identities and paths must be unique")
        actual = (
            path,
            artifact["schema_name"],
            artifact["generator"],
            artifact["earliest_checkpoint"],
        )
        if expected.get(logical_id) != actual:
            raise ContractError(f"Artifact Plan binding is invalid for {logical_id!r}")
        logical_ids.add(logical_id)
        paths.add(path)
    if logical_ids != set(expected):
        raise ContractError("Artifact Plan does not contain the exact Slice 1 artifact set")


def _validate_source_manifest(instance: dict[str, Any]) -> None:
    logical_ids: set[str] = set()
    paths: set[str] = set()
    for artifact in instance["artifacts"]:
        logical_id = artifact["logical_id"]
        path = artifact["path"]
        _validate_project_relative_path(path, prefix="source")
        if logical_id in logical_ids or path in paths:
            raise ContractError("Source Manifest artifact identities and paths must be unique")
        logical_ids.add(logical_id)
        paths.add(path)


def _validate_fixture_package(instance: dict[str, Any]) -> None:
    logical_ids: set[str] = set()
    paths: set[str] = set()
    allowed_roots = {"metadata", "subtitles", "media", "cover"}
    for artifact in instance["artifacts"]:
        logical_id = artifact["logical_id"]
        path = artifact["path"]
        _validate_project_relative_path(path)
        if PurePosixPath(path).parts[0] not in allowed_roots:
            raise ContractError(f"fixture artifact path has an unapproved root: {path!r}")
        if logical_id in logical_ids or path in paths:
            raise ContractError("fixture artifact identities and paths must be unique")
        logical_ids.add(logical_id)
        paths.add(path)


def _validate_run_record(instance: dict[str, Any]) -> None:
    generation = instance["artifact_generations"]["source_manifest"]
    checkpoint = instance["checkpoints"]["source_ready"]
    _validate_project_relative_path(generation["path"], prefix="source")
    _validate_project_relative_path(instance["artifact_plan"])
    _validate_canonical_absolute_path(instance["output_path"])
    if checkpoint["artifact_generations"]["source_manifest"] != generation["generation"]:
        raise ContractError("source_ready generation does not bind to Source Manifest generation")
    if checkpoint["evidence_sha256"] != generation["sha256"]:
        raise ContractError("source_ready evidence fingerprint does not bind to Source Manifest")


def _validate_scaffold_contract(instance: dict[str, Any]) -> None:
    for value in instance["managed_directories"]:
        _validate_project_relative_path(value)
    for value in instance["reserved_descendant_paths"]:
        _validate_project_relative_path(value)


INVARIANT_VALIDATORS = {
    "artifact-plan-paths-v1": _validate_artifact_plan,
    "control-store-identity-path-v1": lambda value: _validate_canonical_absolute_path(
        value["workspace_path"]
    ),
    "fixture-package-paths-v1": _validate_fixture_package,
    "run-record-freshness-v1": _validate_run_record,
    "scaffold-contract-directories-v1": _validate_scaffold_contract,
    "source-manifest-paths-and-fingerprints-v1": _validate_source_manifest,
}


def _walk_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            yield reference
        for child in value.values():
            yield from _walk_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_refs(child)


class ContractRegistry:
    """Closed JSON Schema registry; structural field authority stays in Schema."""

    def __init__(self, project_root: Path, registry_path: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.registry_path = (registry_path or self.project_root / REGISTRY_RELATIVE_PATH).resolve()
        self._canonical = read_json(self.project_root / REGISTRY_RELATIVE_PATH)
        self._manifest = read_json(self.registry_path)
        self.entries = self._load_entries()
        self.schemas: dict[str, dict[str, Any]] = {}
        self._registry: Registry | None = None

    def _load_entries(self) -> tuple[ContractEntry, ...]:
        if not isinstance(self._manifest, dict):
            raise ContractError("Kernel Schema Registry root must be an object")
        if self._manifest.get("schema_name") != "kernel-schema-registry":
            raise ContractError("Kernel Schema Registry identity is invalid")
        if self._manifest.get("schema_version") != "1.0.0":
            raise UnknownContractVersion("unknown Kernel Schema Registry version")
        contracts = self._manifest.get("contracts")
        if not isinstance(contracts, list) or not contracts:
            raise ContractError("Kernel Schema Registry contracts must be a non-empty array")
        known_versions = {
            item["schema_name"]: item["schema_version"]
            for item in self._canonical["contracts"]
        }
        entries: list[ContractEntry] = []
        seen_names: set[str] = set()
        seen_ids: set[str] = set()
        for raw in contracts:
            if not isinstance(raw, dict):
                raise ContractError("registry contract entry must be an object")
            name = raw.get("schema_name")
            version = raw.get("schema_version")
            if name not in known_versions or version != known_versions[name]:
                raise UnknownContractVersion(
                    f"unknown registered contract version: {name!r} {version!r}"
                )
            schema_id = raw.get("schema_id")
            if name in seen_names or schema_id in seen_ids:
                raise ContractError("registry schema names and identities must be unique")
            seen_names.add(name)
            seen_ids.add(schema_id)
            raw_path = Path(str(raw.get("schema_path", "")))
            schema_path = raw_path if raw_path.is_absolute() else self.project_root / raw_path
            positive = raw.get("positive_example")
            negative = raw.get("negative_example")
            invariants = raw.get("invariants", [])
            if (
                not isinstance(invariants, list)
                or any(not isinstance(value, str) for value in invariants)
                or len(set(invariants)) != len(invariants)
            ):
                raise ContractError(f"registry invariants are invalid for {name!r}")
            unknown_invariants = sorted(set(invariants) - KNOWN_INVARIANTS)
            if unknown_invariants:
                raise ContractError(
                    f"registry declares unknown contract invariants: {unknown_invariants}"
                )
            entries.append(
                ContractEntry(
                    schema_name=name,
                    schema_version=version,
                    schema_id=schema_id,
                    schema_path=schema_path.resolve(),
                    kind=str(raw.get("kind")),
                    positive_example=(self.project_root / positive).resolve() if positive else None,
                    negative_example=(self.project_root / negative).resolve() if negative else None,
                    invariants=tuple(invariants),
                )
            )
        required_names = set(known_versions)
        if seen_names != required_names:
            raise ContractError(
                "registry must contain the exact canonical contract name/version set: "
                f"missing={sorted(required_names - seen_names)}, "
                f"extra={sorted(seen_names - required_names)}"
            )
        return tuple(entries)

    def check(self) -> dict[str, Any]:
        runtime = self._prepare_registry()
        installed = runtime["jsonschema_version"]

        positive_count = 0
        negative_count = 0
        for entry in self.entries:
            if entry.kind != "contract":
                continue
            if entry.positive_example is None or entry.negative_example is None:
                raise ContractError(f"contract examples missing for {entry.schema_name}")
            self.validate(entry.schema_name, read_json(entry.positive_example))
            positive_count += 1
            try:
                self.validate(entry.schema_name, read_json(entry.negative_example))
            except ContractError:
                negative_count += 1
            else:
                raise ContractError(
                    f"negative contract example unexpectedly passed: {entry.negative_example}"
                )
        return {
            "jsonschema_version": installed,
            "json_schema_draft": DRAFT,
            "contract_count": positive_count,
            "supporting_schema_count": sum(e.kind == "supporting_schema" for e in self.entries),
            "positive_examples_validated": positive_count,
            "negative_examples_rejected": negative_count,
            "registry_path": str(self.registry_path),
            "registry_complete": True,
            "registered_schema_names": sorted(entry.schema_name for entry in self.entries),
            "runtime_lock": runtime,
        }

    def _check_locked_runtime(self) -> dict[str, Any]:
        input_path = self.project_root / RUNTIME_INPUT
        lock_path = self.project_root / RUNTIME_LOCK
        direct_lines = {
            line.strip()
            for line in input_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if direct_lines != {f"jsonschema=={JSONSCHEMA_VERSION}"}:
            raise ContractError("runtime input must contain only the exact jsonschema pin")
        with lock_path.open("rb") as handle:
            lock = tomllib.load(handle)
        if lock.get("lock-version") != "1.0" or lock.get("created-by") != "uv":
            raise ContractError("runtime lock is not the expected uv PEP 751 lock")
        packages = lock.get("packages")
        if not isinstance(packages, list) or not packages:
            raise ContractError("runtime lock contains no packages")
        locked: dict[str, str] = {}
        for package in packages:
            name = package.get("name")
            version = package.get("version")
            if not isinstance(name, str) or not isinstance(version, str) or name in locked:
                raise ContractError("runtime lock package identities must be unique strings")
            artifacts = []
            if isinstance(package.get("sdist"), dict):
                artifacts.append(package["sdist"])
            if isinstance(package.get("wheels"), list):
                artifacts.extend(package["wheels"])
            if not artifacts or any(
                not isinstance(artifact.get("hashes"), dict)
                or not isinstance(artifact["hashes"].get("sha256"), str)
                or len(artifact["hashes"]["sha256"]) != 64
                for artifact in artifacts
            ):
                raise ContractError(f"runtime lock package lacks full SHA-256 hashes: {name}")
            locked[name] = version
        if locked.get("jsonschema") != JSONSCHEMA_VERSION:
            raise ContractError("runtime lock jsonschema version differs from the direct pin")
        installed: dict[str, str] = {}
        for name, expected in locked.items():
            try:
                actual = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError as exc:
                raise ContractError(f"locked runtime package is unavailable: {name}") from exc
            if actual != expected:
                raise ContractError(
                    f"locked runtime package mismatch: {name}: expected {expected}, got {actual}"
                )
            installed[name] = actual
        return {
            "jsonschema_version": installed["jsonschema"],
            "locked_packages": installed,
            "lock_path": str(lock_path),
            "lock_sha256": sha256_file(lock_path),
        }

    def validate(self, schema_name: str, instance: Any) -> None:
        if self._registry is None:
            self._prepare_registry()
        entry = next((item for item in self.entries if item.schema_name == schema_name), None)
        if entry is None:
            raise UnknownContractVersion(f"unregistered contract: {schema_name}")
        if isinstance(instance, dict):
            actual_version = instance.get("schema_version")
            if actual_version != entry.schema_version:
                raise UnknownContractVersion(
                    f"unknown {schema_name} schema_version: {actual_version!r}"
                )
        validator = Draft202012Validator(
            self.schemas[schema_name],
            registry=self._registry,
            format_checker=FormatChecker(),
        )
        try:
            validator.validate(instance)
        except ValidationError as exc:
            path = "/".join(str(part) for part in exc.absolute_path) or "$"
            raise ContractError(f"{schema_name} instance invalid at {path}: {exc.message}") from exc
        if isinstance(instance, dict):
            for invariant in entry.invariants:
                INVARIANT_VALIDATORS[invariant](instance)

    def _prepare_registry(self) -> dict[str, Any]:
        """Prepare every registry entry through the same closed, locked path."""
        if self._registry is not None:
            return self._check_locked_runtime()
        runtime = self._check_locked_runtime()
        registered_ids = {entry.schema_id for entry in self.entries}
        resources: list[tuple[str, Resource]] = []
        for entry in self.entries:
            try:
                schema = read_json(entry.schema_path)
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                raise ContractError(f"cannot load schema: {entry.schema_path}: {exc}") from exc
            if not isinstance(schema, dict):
                raise ContractError(f"schema root must be an object: {entry.schema_path}")
            if schema.get("$schema") != DRAFT:
                raise ContractError(f"schema draft mismatch: {entry.schema_path}")
            if schema.get("$id") != entry.schema_id:
                raise ContractError(f"schema identity mismatch: {entry.schema_path}")
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise ContractError(
                    f"invalid Draft 2020-12 schema {entry.schema_id}: {exc.message}"
                ) from exc
            for reference in _walk_refs(schema):
                target = reference.split("#", 1)[0]
                if target and target not in registered_ids:
                    raise UnresolvedSchemaReference(
                        f"unregistered schema reference {reference!r} in {entry.schema_id}"
                    )
            self.schemas[entry.schema_name] = schema
            resources.append((entry.schema_id, Resource.from_contents(schema)))
        self._registry = Registry().with_resources(resources)
        if self.registry_path == (self.project_root / REGISTRY_RELATIVE_PATH).resolve():
            registered_paths = {entry.schema_path for entry in self.entries}
            disk_paths = {
                path.resolve()
                for path in (self.project_root / "schemas/video-workflow/v1").glob(
                    "*.schema.json"
                )
            }
            if registered_paths != disk_paths:
                missing = sorted(str(path) for path in disk_paths - registered_paths)
                extra = sorted(str(path) for path in registered_paths - disk_paths)
                raise ContractError(
                    f"registry completeness mismatch: missing={missing}, extra={extra}"
                )
        return runtime

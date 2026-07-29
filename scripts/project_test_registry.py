"""Strict, versioned registry for authoritative project test suites."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


SCHEMA_NAME = "video2pdf.project-test-suites"
SCHEMA_VERSION = 1
PROJECT_FIELDS = frozenset({"project_key", "repository"})
ROOT_FIELDS = frozenset({"path", "pattern"})
SUITE_FIELDS = frozenset({"suite_id", "suite_key", "roots"})
MIRROR_FIELDS = frozenset({"authority_path", "mirror_path"})
TOP_LEVEL_FIELDS = frozenset(
    {"schema_name", "schema_version", "project", "suites", "mirrors"}
)
SAFE_IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class RegistryError(ValueError):
    """The suite registry or its repository coverage is invalid."""


@dataclass(frozen=True)
class SuiteRoot:
    path: str
    pattern: str


@dataclass(frozen=True)
class Suite:
    suite_id: str
    suite_key: str
    roots: tuple[SuiteRoot, ...]


@dataclass(frozen=True)
class Mirror:
    authority_path: str
    mirror_path: str


@dataclass(frozen=True)
class Registry:
    repo_root: Path
    registry_path: Path
    fingerprint: str
    project_key: str
    repository: str
    suites: tuple[Suite, ...]
    mirrors: tuple[Mirror, ...]

    def select_suites(
        self, suite_ids: Sequence[str] | None
    ) -> tuple[Suite, ...]:
        by_id = {suite.suite_id: suite for suite in self.suites}
        if suite_ids is None:
            return tuple(sorted(self.suites, key=lambda suite: suite.suite_id))
        if not suite_ids:
            raise RegistryError("at least one suite must be selected")
        if len(set(suite_ids)) != len(suite_ids):
            raise RegistryError("duplicate suite selection")
        unknown = sorted(set(suite_ids) - by_id.keys())
        if unknown:
            raise RegistryError(f"unknown suite: {', '.join(unknown)}")
        return tuple(by_id[suite_id] for suite_id in sorted(suite_ids))

    def registered_test_files(
        self, suite_ids: Sequence[str] | None = None
    ) -> tuple[str, ...]:
        paths: set[str] = set()
        for suite in self.select_suites(suite_ids):
            for root in suite.roots:
                root_path = self.repo_root / PurePosixPath(root.path)
                for candidate in root_path.glob(root.pattern):
                    if candidate.is_file():
                        paths.add(candidate.relative_to(self.repo_root).as_posix())
        return tuple(sorted(paths))


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise RegistryError(f"{label} field names must be strings")
    return value


def _require_fields(
    value: dict[str, Any], expected: frozenset[str], label: str
) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise RegistryError(f"{label} has unknown field: {', '.join(unknown)}")
    if missing:
        raise RegistryError(f"{label} is missing field: {', '.join(missing)}")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{label} must be a non-empty string")
    return value


def _require_identifier(value: Any, label: str) -> str:
    text = _require_string(value, label)
    if SAFE_IDENTIFIER.fullmatch(text) is None:
        raise RegistryError(
            f"{label} must be a lowercase filesystem-safe identifier"
        )
    return text


def _relative_directory(repo_root: Path, value: Any, label: str) -> str:
    text = _require_string(value, label).replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or text.startswith("/") or ".." in path.parts:
        raise RegistryError(f"{label} must be a repository-relative path")
    normalized = path.as_posix()
    if normalized in ("", "."):
        raise RegistryError(f"{label} must identify a repository directory")
    absolute = repo_root.joinpath(*path.parts)
    if not absolute.exists():
        raise RegistryError(f"{label} does not exist: {normalized}")
    if not absolute.is_dir():
        raise RegistryError(f"{label} must be a directory: {normalized}")
    try:
        absolute.resolve(strict=True).relative_to(repo_root.resolve(strict=True))
    except ValueError as error:
        raise RegistryError(
            f"{label} resolves outside the repository: {normalized}"
        ) from error
    return normalized


def _parse_root(repo_root: Path, value: Any, label: str) -> SuiteRoot:
    item = _require_object(value, label)
    _require_fields(item, ROOT_FIELDS, label)
    path = _relative_directory(repo_root, item["path"], f"{label}.path")
    pattern = _require_string(item["pattern"], f"{label}.pattern")
    pattern_path = PurePosixPath(pattern.replace("\\", "/"))
    if pattern_path.is_absolute() or ".." in pattern_path.parts:
        raise RegistryError(f"{label}.pattern must stay inside its root")
    if not pattern.endswith(".py"):
        raise RegistryError(f"{label}.pattern must select Python files")
    return SuiteRoot(path=path, pattern=pattern)


def _paths_overlap(first: str, second: str) -> bool:
    first_parts = tuple(part.casefold() for part in PurePosixPath(first).parts)
    second_parts = tuple(part.casefold() for part in PurePosixPath(second).parts)
    shorter = min(len(first_parts), len(second_parts))
    return first_parts[:shorter] == second_parts[:shorter]


def _authoritative_test_candidates(repo_root: Path) -> set[str]:
    candidates: set[str] = set()
    for relative_root, recursive in (
        ("tests", True),
        ("scripts", False),
        (".agents/skills", True),
    ):
        root = repo_root / relative_root
        if not root.is_dir():
            continue
        iterator: Iterable[Path]
        iterator = root.rglob("test*.py") if recursive else root.glob("test*.py")
        for path in iterator:
            relative = path.relative_to(repo_root)
            if (
                path.is_file()
                and "__pycache__" not in relative.parts
                and "待删除" not in relative.parts
            ):
                candidates.add(relative.as_posix())
    return candidates


def load_registry(repo_root: Path, registry_path: Path) -> Registry:
    """Load and fully validate one immutable registry snapshot."""

    repo_root = repo_root.resolve(strict=True)
    if not registry_path.is_absolute():
        registry_path = repo_root / registry_path
    registry_path = registry_path.resolve(strict=True)
    try:
        registry_path.relative_to(repo_root)
    except ValueError as error:
        raise RegistryError("registry path must be inside the repository") from error
    raw = registry_path.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RegistryError(f"registry is not valid UTF-8 JSON: {error}") from error
    top = _require_object(document, "registry")
    _require_fields(top, TOP_LEVEL_FIELDS, "registry")
    if top["schema_name"] != SCHEMA_NAME:
        raise RegistryError(f"unsupported schema_name: {top['schema_name']!r}")
    if type(top["schema_version"]) is not int or top["schema_version"] != 1:
        raise RegistryError(
            f"unsupported schema_version: {top['schema_version']!r}"
        )

    project = _require_object(top["project"], "project")
    _require_fields(project, PROJECT_FIELDS, "project")
    project_key = _require_identifier(
        project["project_key"], "project.project_key"
    )
    repository = _require_string(project["repository"], "project.repository")

    if not isinstance(top["suites"], list) or not top["suites"]:
        raise RegistryError("suites must be a non-empty array")
    suites: list[Suite] = []
    suite_ids: set[str] = set()
    suite_keys: set[str] = set()
    authority_roots: list[str] = []
    for index, raw_suite in enumerate(top["suites"]):
        label = f"suites[{index}]"
        item = _require_object(raw_suite, label)
        _require_fields(item, SUITE_FIELDS, label)
        suite_id = _require_identifier(item["suite_id"], f"{label}.suite_id")
        suite_key = _require_identifier(
            item["suite_key"], f"{label}.suite_key"
        )
        if suite_id in suite_ids:
            raise RegistryError(f"duplicate suite_id: {suite_id}")
        if suite_key in suite_keys:
            raise RegistryError(f"duplicate suite_key: {suite_key}")
        if not isinstance(item["roots"], list) or not item["roots"]:
            raise RegistryError(f"{label}.roots must be a non-empty array")
        roots = tuple(
            _parse_root(repo_root, root, f"{label}.roots[{root_index}]")
            for root_index, root in enumerate(item["roots"])
        )
        for root in roots:
            for existing in authority_roots:
                if root.path == existing:
                    raise RegistryError(
                        f"duplicate authority root: {root.path}"
                    )
                if _paths_overlap(root.path, existing):
                    raise RegistryError(
                        "overlapping authority roots: "
                        f"{existing} and {root.path}"
                    )
            if not any(
                candidate.is_file()
                for candidate in (repo_root / root.path).glob(root.pattern)
            ):
                raise RegistryError(
                    "authority root matches no test files: "
                    f"{root.path} ({root.pattern})"
                )
            authority_roots.append(root.path)
        suite_ids.add(suite_id)
        suite_keys.add(suite_key)
        suites.append(Suite(suite_id, suite_key, roots))

    if not isinstance(top["mirrors"], list):
        raise RegistryError("mirrors must be an array")
    mirrors: list[Mirror] = []
    seen_mirror_paths: set[str] = set()
    for index, raw_mirror in enumerate(top["mirrors"]):
        label = f"mirrors[{index}]"
        item = _require_object(raw_mirror, label)
        _require_fields(item, MIRROR_FIELDS, label)
        authority_path = _relative_directory(
            repo_root, item["authority_path"], f"{label}.authority_path"
        )
        mirror_path = _relative_directory(
            repo_root, item["mirror_path"], f"{label}.mirror_path"
        )
        if authority_path not in authority_roots:
            raise RegistryError(
                "mirror authority must identify an authority root: "
                f"{authority_path}"
            )
        if any(_paths_overlap(mirror_path, root) for root in authority_roots):
            raise RegistryError(
                f"mirror path overlaps an authority root: {mirror_path}"
            )
        if mirror_path in seen_mirror_paths:
            raise RegistryError(f"duplicate mirror path: {mirror_path}")
        seen_mirror_paths.add(mirror_path)
        mirrors.append(Mirror(authority_path, mirror_path))

    registry = Registry(
        repo_root=repo_root,
        registry_path=registry_path,
        fingerprint=hashlib.sha256(raw).hexdigest(),
        project_key=project_key,
        repository=repository,
        suites=tuple(suites),
        mirrors=tuple(mirrors),
    )
    unregistered = sorted(
        _authoritative_test_candidates(repo_root)
        - set(registry.registered_test_files())
    )
    if unregistered:
        raise RegistryError(
            "unregistered authoritative test file: " + ", ".join(unregistered)
        )
    return registry

"""Dynamic unittest inventory and immutable discovery manifest support."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, Iterator, Sequence

from scripts.project_test_registry import Registry


DISCOVERY_SCHEMA_NAME = "video2pdf.project-test-discovery"
DISCOVERY_SCHEMA_VERSION = 1


class DiscoveryError(RuntimeError):
    """Dynamic discovery could not prove a complete, unique test set."""


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _flatten_suite(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten_suite(item)
        else:
            if not isinstance(item, unittest.TestCase):
                raise DiscoveryError(
                    f"unittest loader returned unsupported item: {item!r}"
                )
            yield item


def _discover_file(source_path: Path) -> tuple[str, ...]:
    loader = unittest.TestLoader()
    module_name = source_path.stem
    previous = sys.modules.pop(module_name, None)
    sys.path.insert(0, str(source_path.parent))
    try:
        suite = loader.discover(
            start_dir=str(source_path.parent),
            pattern=source_path.name,
            top_level_dir=str(source_path.parent),
        )
        tests = tuple(_flatten_suite(suite))
    finally:
        sys.path.pop(0)
        sys.modules.pop(module_name, None)
        if previous is not None:
            sys.modules[module_name] = previous
    if loader.errors:
        raise DiscoveryError(
            "unittest loader error for "
            f"{source_path}: {' | '.join(loader.errors)}"
        )
    failed = [
        test.id()
        for test in tests
        if test.__class__.__name__ == "_FailedTest"
        or test.__class__.__module__ == "unittest.loader"
    ]
    if failed:
        raise DiscoveryError(
            f"unittest _FailedTest for {source_path}: {', '.join(failed)}"
        )
    ids = [test.id() for test in tests]
    if any(not isinstance(test_id, str) or not test_id for test_id in ids):
        raise DiscoveryError(f"empty TestCase.id() from {source_path}")
    return tuple(sorted(ids))


def _current_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    commit = completed.stdout.strip()
    if completed.returncode != 0 or not commit:
        raise DiscoveryError(
            "cannot resolve repository commit: " + completed.stderr.strip()
        )
    return commit


def discover_tests(
    registry: Registry,
    selected_suite_ids: Sequence[str] | None = None,
    *,
    commit: str | None = None,
) -> dict[str, Any]:
    """Discover the selected closed set and return a stable manifest value."""

    suites = registry.select_suites(selected_suite_ids)
    modules: list[dict[str, Any]] = []
    module_keys: dict[str, tuple[str, str]] = {}
    all_test_ids: list[str] = []
    for suite in suites:
        for root in sorted(suite.roots, key=lambda item: (item.path, item.pattern)):
            absolute_root = registry.repo_root / root.path
            sources = sorted(
                (
                    candidate
                    for candidate in absolute_root.glob(root.pattern)
                    if candidate.is_file()
                ),
                key=lambda path: path.relative_to(registry.repo_root).as_posix(),
            )
            for source in sources:
                source_relative = source.relative_to(
                    registry.repo_root
                ).as_posix()
                module_identity = (suite.suite_id, source_relative)
                module_key = hashlib.sha256(
                    f"{module_identity[0]}\0{module_identity[1]}".encode("utf-8")
                ).hexdigest()[:12]
                existing = module_keys.get(module_key)
                if existing is not None and existing != module_identity:
                    raise DiscoveryError(
                        "module_key collision: "
                        f"{existing!r} and {module_identity!r}"
                    )
                module_keys[module_key] = module_identity
                test_ids = _discover_file(source)
                modules.append(
                    {
                        "suite_id": suite.suite_id,
                        "root_path": root.path,
                        "source_path": source_relative,
                        "module_key": module_key,
                        "test_count": len(test_ids),
                        "test_ids": list(test_ids),
                    }
                )
                all_test_ids.extend(test_ids)

    duplicates = sorted(
        test_id
        for test_id in set(all_test_ids)
        if all_test_ids.count(test_id) > 1
    )
    if duplicates:
        raise DiscoveryError(
            "duplicate TestCase.id(): " + ", ".join(duplicates)
        )
    ordered_ids = sorted(all_test_ids)
    suite_ids = [suite.suite_id for suite in suites]
    return {
        "schema_name": DISCOVERY_SCHEMA_NAME,
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "project": {
            "project_key": registry.project_key,
            "repository": registry.repository,
        },
        "commit": commit or _current_commit(registry.repo_root),
        "registry_path": registry.registry_path.relative_to(
            registry.repo_root
        ).as_posix(),
        "registry_sha256": registry.fingerprint,
        "discovery_arguments": {"suite_ids": suite_ids},
        "suite_ids": suite_ids,
        "suites": [
            {
                "suite_id": suite.suite_id,
                "suite_key": suite.suite_key,
                "roots": [
                    {"path": root.path, "pattern": root.pattern}
                    for root in sorted(
                        suite.roots, key=lambda item: (item.path, item.pattern)
                    )
                ],
            }
            for suite in suites
        ],
        "modules": sorted(
            modules, key=lambda module: (module["suite_id"], module["source_path"])
        ),
        "duplicate_test_ids": [],
        "total_count": len(ordered_ids),
        "test_id_set_sha256": hashlib.sha256(
            _canonical_json_bytes(ordered_ids)
        ).hexdigest(),
    }


def write_discovery_manifest(
    destination: Path, manifest: dict[str, Any]
) -> None:
    """Write once; an existing path is immutable even when content matches."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json_bytes(manifest).decode("utf-8"))
    except FileExistsError as error:
        raise DiscoveryError(
            f"discovery manifest already exists: {destination}"
        ) from error


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--suite", action="append")
    arguments = parser.parse_args(argv)
    repo_root = arguments.repo_root.resolve(strict=True)
    from scripts.project_test_registry import load_registry

    registry = load_registry(repo_root, arguments.registry)
    manifest = discover_tests(registry, arguments.suite)
    write_discovery_manifest(arguments.destination, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

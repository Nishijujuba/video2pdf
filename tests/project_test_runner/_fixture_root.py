"""Suite-aware generated-fixture boundary for project tests.

Runner-managed execution derives its destination exclusively from the frozen
worker environment and validates the complete owned-run identity. Direct
execution uses a suite-partitioned repository-local compatibility root.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from types import MappingProxyType
from typing import Mapping
import uuid

from scripts.project_test_external_root import (
    ExternalRootError,
    assert_safe_write_path,
    validate_owned_run_directory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_DIR_ENV = "VIDEO2PDF_PROJECT_TEST_RUN_DIR"
SUITE_ID_ENV = "VIDEO2PDF_PROJECT_TEST_SUITE_ID"
MODULE_KEY_ENV = "VIDEO2PDF_PROJECT_TEST_MODULE_KEY"
_MODULE_KEY = re.compile(r"^[0-9a-f]{12}$")
_SUITE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT = {
    "project_key": "video2pdf",
    "repository": "Nishijujuba/video2pdf",
}
_REPARSE_ATTRIBUTE = 0x400
FROZEN_RUN_ENV: Mapping[str, str | None] = MappingProxyType(
    {
        RUN_DIR_ENV: os.environ.get(RUN_DIR_ENV),
        SUITE_ID_ENV: os.environ.get(SUITE_ID_ENV),
        MODULE_KEY_ENV: os.environ.get(MODULE_KEY_ENV),
    }
)


class FixtureRootError(RuntimeError):
    """The runner-provided generated-fixture boundary is invalid."""


def _is_reparse(path: Path) -> bool:
    stat_result = os.lstat(path)
    return path.is_symlink() or bool(
        getattr(stat_result, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE
    )


def _assert_ordinary_ancestors(path: Path) -> None:
    current = path
    while True:
        if current.exists() and _is_reparse(current):
            raise FixtureRootError(f"reparse point is forbidden: {current}")
        if current.parent == current:
            return
        current = current.parent


def committed_fixture_root() -> Path:
    """Return the read-only root containing committed runner fixtures."""

    return Path(__file__).resolve().parent / "fixtures"


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _read_manifest(run_dir: Path, name: str) -> tuple[dict[str, object], bytes]:
    try:
        path = assert_safe_write_path(run_dir, run_dir / name)
        if not path.is_file():
            raise FixtureRootError(
                f"runner identity requires an ordinary {name}"
            )
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except FixtureRootError:
        raise
    except (
        ExternalRootError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        raise FixtureRootError(
            f"runner identity has invalid {name}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise FixtureRootError(f"runner identity {name} must be an object")
    return value, raw


def _current_commit(project_root: Path) -> str:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise FixtureRootError(
            "runner identity cannot resolve the git executable"
        )
    try:
        completed = subprocess.run(
            [git_executable, "rev-parse", "HEAD"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise FixtureRootError(
            "runner identity cannot inspect the active repository commit"
        ) from error
    commit = completed.stdout.strip()
    if completed.returncode != 0 or _COMMIT.fullmatch(commit) is None:
        raise FixtureRootError(
            "runner identity cannot resolve the active repository commit"
        )
    return commit


def _current_registry_sha256(project_root: Path) -> str:
    try:
        registry = project_root / "config" / "test-suites.v1.json"
        return hashlib.sha256(registry.read_bytes()).hexdigest()
    except OSError as error:
        raise FixtureRootError(
            "runner identity cannot fingerprint the active suite registry"
        ) from error


def _validate_manifest_identity(
    run_dir: Path,
    suite_id: str,
    module_key: str,
    project_root: Path,
) -> None:
    test_run, _ = _read_manifest(run_dir, "test-run.json")
    discovery, discovery_raw = _read_manifest(run_dir, "discovery.json")
    if (
        test_run.get("schema_name") != "video2pdf.project-test-run"
        or test_run.get("schema_version") != 1
    ):
        raise FixtureRootError("runner identity test-run.json schema is invalid")
    discovery_sha256 = test_run.get("discovery_sha256")
    if (
        not isinstance(discovery_sha256, str)
        or _SHA256.fullmatch(discovery_sha256) is None
        or hashlib.sha256(discovery_raw).hexdigest() != discovery_sha256
    ):
        raise FixtureRootError(
            "runner discovery.json fingerprint does not match test-run.json"
        )
    if (
        discovery.get("schema_name") != "video2pdf.project-test-discovery"
        or discovery.get("schema_version") != 1
    ):
        raise FixtureRootError("runner identity discovery.json schema is invalid")

    manifest_run_dir = test_run.get("run_dir")
    manifest_run_path = (
        Path(manifest_run_dir) if isinstance(manifest_run_dir, str) else None
    )
    if (
        manifest_run_path is None
        or not manifest_run_path.is_absolute()
        or ".." in manifest_run_path.parts
        or not _same_path(manifest_run_path, run_dir)
    ):
        raise FixtureRootError(
            "runner test-run.json run directory identity does not match"
        )

    commit = test_run.get("commit")
    registry_sha256 = test_run.get("registry_sha256")
    selected_suite_ids = test_run.get("suite_ids")
    if (
        test_run.get("project") != _PROJECT
        or discovery.get("project") != _PROJECT
        or not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or discovery.get("commit") != commit
        or commit != _current_commit(project_root)
        or not isinstance(registry_sha256, str)
        or _SHA256.fullmatch(registry_sha256) is None
        or discovery.get("registry_sha256") != registry_sha256
        or registry_sha256 != _current_registry_sha256(project_root)
    ):
        raise FixtureRootError(
            "runner project, repository, commit, or registry identity "
            "does not match"
        )
    if (
        not isinstance(selected_suite_ids, list)
        or not selected_suite_ids
        or any(
            not isinstance(selected, str)
            or _SUITE_ID.fullmatch(selected) is None
            for selected in selected_suite_ids
        )
        or len(set(selected_suite_ids)) != len(selected_suite_ids)
        or discovery.get("suite_ids") != selected_suite_ids
        or discovery.get("discovery_arguments")
        != {"suite_ids": selected_suite_ids}
        or suite_id not in selected_suite_ids
    ):
        raise FixtureRootError(
            "runner worker suite identity is outside the selected suite set"
        )

    run_suite_key = run_dir.parent.name
    if run_suite_key == "all":
        if len(selected_suite_ids) < 2:
            raise FixtureRootError(
                "runner all-suite path requires multiple selected suites"
            )
    elif run_suite_key != suite_id or selected_suite_ids != [suite_id]:
        raise FixtureRootError(
            "runner single-suite path must match the selected suite identity"
        )

    modules = discovery.get("modules")
    if not isinstance(modules, list):
        raise FixtureRootError("runner discovery module inventory is invalid")
    matches = [
        module
        for module in modules
        if isinstance(module, dict) and module.get("module_key") == module_key
    ]
    if len(matches) != 1 or matches[0].get("suite_id") != suite_id:
        raise FixtureRootError(
            "runner discovery does not bind the active module and suite identity"
        )
    test_ids = matches[0].get("test_ids")
    if (
        not isinstance(test_ids, list)
        or not test_ids
        or any(not isinstance(test_id, str) or not test_id for test_id in test_ids)
        or len(set(test_ids)) != len(test_ids)
    ):
        raise FixtureRootError(
            "runner discovery active module test IDs are invalid"
        )


def fixture_root_from_environment(
    environment: Mapping[str, str | None],
    project_root: Path = PROJECT_ROOT,
    *,
    expected_suite: str | None = None,
) -> Path:
    """Resolve generated fixtures from one immutable environment snapshot.

    ``expected_suite`` verifies a caller's suite contract. It never supplies or
    changes a runner-managed output path.
    """

    if (
        expected_suite is not None
        and _SUITE_ID.fullmatch(expected_suite) is None
    ):
        raise FixtureRootError(f"invalid expected suite: {expected_suite}")
    values = {
        RUN_DIR_ENV: environment.get(RUN_DIR_ENV),
        SUITE_ID_ENV: environment.get(SUITE_ID_ENV),
        MODULE_KEY_ENV: environment.get(MODULE_KEY_ENV),
    }
    if not any(values.values()):
        fallback_suite = expected_suite or "project-test-runner"
        return (
            project_root
            / "待删除"
            / "kernel-test-runs"
            / fallback_suite
        )
    if not all(values.values()):
        raise FixtureRootError("runner test identity environment is incomplete")

    run_text = values[RUN_DIR_ENV]
    suite_id = values[SUITE_ID_ENV]
    module_key = values[MODULE_KEY_ENV]
    assert (
        run_text is not None
        and suite_id is not None
        and module_key is not None
    )
    if expected_suite is not None and suite_id != expected_suite:
        raise FixtureRootError(
            f"runner suite identity {suite_id!r} does not match expected "
            f"suite {expected_suite!r}"
        )
    if _MODULE_KEY.fullmatch(module_key) is None:
        raise FixtureRootError(f"invalid module key: {module_key}")
    try:
        run_dir = validate_owned_run_directory(run_text)
    except ExternalRootError as error:
        raise FixtureRootError(
            f"runner test directory ownership is invalid: {error}"
        ) from error
    _assert_ordinary_ancestors(run_dir)
    _validate_manifest_identity(
        run_dir,
        suite_id,
        module_key,
        project_root.resolve(strict=True),
    )

    try:
        generated = assert_safe_write_path(
            run_dir,
            run_dir / "generated" / module_key,
        )
    except ExternalRootError as error:
        raise FixtureRootError(
            "generated fixture root escapes or violates the active run"
        ) from error
    return generated


def generated_fixture_root(
    *, expected_suite: str | None = None
) -> Path:
    """Return and create this module's generated-fixture root."""

    root = fixture_root_from_environment(
        FROZEN_RUN_ENV,
        expected_suite=expected_suite,
    )
    root.mkdir(parents=True, exist_ok=True)
    _assert_ordinary_ancestors(root)
    return root


def new_fixture_dir(
    label: str,
    *,
    expected_suite: str | None = None,
) -> Path:
    """Create one unique fixture directory without changing the active root."""

    if not label or re.fullmatch(r"[a-z0-9-]+", label) is None:
        raise FixtureRootError(
            "fixture label must contain lowercase letters, digits, or hyphens"
        )
    path = generated_fixture_root(
        expected_suite=expected_suite
    ) / f"{label}-{uuid.uuid4().hex}"
    path.mkdir(exist_ok=False)
    return path

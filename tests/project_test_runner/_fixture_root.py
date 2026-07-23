"""Generated-fixture boundary for project-test-runner tests."""

from __future__ import annotations

import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_DIR_ENV = "VIDEO2PDF_PROJECT_TEST_RUN_DIR"
SUITE_ID_ENV = "VIDEO2PDF_PROJECT_TEST_SUITE_ID"
MODULE_KEY_ENV = "VIDEO2PDF_PROJECT_TEST_MODULE_KEY"
_MODULE_KEY = re.compile(r"^[0-9a-f]{12}$")
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


def fixture_root_from_environment(
    environment: Mapping[str, str | None],
    project_root: Path = PROJECT_ROOT,
) -> Path:
    """Resolve generated fixtures from one immutable environment snapshot."""

    values = {
        RUN_DIR_ENV: environment.get(RUN_DIR_ENV),
        SUITE_ID_ENV: environment.get(SUITE_ID_ENV),
        MODULE_KEY_ENV: environment.get(MODULE_KEY_ENV),
    }
    if not any(values.values()):
        return (
            project_root
            / "待删除"
            / "kernel-test-runs"
            / "project-test-runner"
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
    run_dir = Path(run_text)
    if not run_dir.is_absolute() or not run_dir.is_dir():
        raise FixtureRootError(
            "runner test directory must be an existing absolute directory"
        )
    if suite_id != "project-test-runner":
        raise FixtureRootError(f"unexpected suite identity: {suite_id}")
    if _MODULE_KEY.fullmatch(module_key) is None:
        raise FixtureRootError(f"invalid module key: {module_key}")
    _assert_ordinary_ancestors(run_dir)

    generated = run_dir / "generated" / module_key
    try:
        generated.absolute().relative_to(run_dir.absolute())
    except ValueError as error:
        raise FixtureRootError(
            f"generated fixture root escapes active run: {generated}"
        ) from error
    return generated


def generated_fixture_root() -> Path:
    """Return and create this module's generated-fixture root."""

    root = fixture_root_from_environment(FROZEN_RUN_ENV)
    root.mkdir(parents=True, exist_ok=True)
    _assert_ordinary_ancestors(root)
    return root


def new_fixture_dir(label: str) -> Path:
    """Create one unique generated-fixture directory."""

    if not label or re.fullmatch(r"[a-z0-9-]+", label) is None:
        raise FixtureRootError(
            "fixture label must contain lowercase letters, digits, or hyphens"
        )
    path = generated_fixture_root() / f"{label}-{uuid.uuid4().hex}"
    path.mkdir(exist_ok=False)
    return path

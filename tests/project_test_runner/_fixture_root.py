"""Suite-aware generated-fixture boundary for project tests.

Runner-managed execution derives its destination exclusively from the frozen
worker environment and validates the complete owned-run identity. Direct
execution uses a suite-partitioned repository-local compatibility root.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Mapping
import uuid

from scripts.project_test_external_root import (
    ExternalRootError,
    assert_safe_write_path,
)
from scripts.project_test_run_identity import (
    MODULE_KEY_ENV,
    RUN_DIR_ENV,
    SUITE_ID_ENV,
    ProjectTestRunIdentityError,
    freeze_worker_environment,
    resolve_active_worker_identity,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FROZEN_RUN_ENV = freeze_worker_environment()
FixtureRootError = ProjectTestRunIdentityError


def committed_fixture_root() -> Path:
    """Return the read-only root containing committed runner fixtures."""

    return Path(__file__).resolve().parent / "fixtures"


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

    identity = resolve_active_worker_identity(
        environment,
        project_root=project_root,
        expected_suite=expected_suite,
    )
    if identity is None:
        fallback_suite = expected_suite or "project-test-runner"
        return (
            project_root
            / "待删除"
            / "kernel-test-runs"
            / fallback_suite
        )
    try:
        return assert_safe_write_path(
            identity.run_dir,
            identity.run_dir / "generated" / identity.module_key,
        )
    except ExternalRootError as error:
        raise FixtureRootError(
            "generated fixture root escapes or violates the active run"
        ) from error


def generated_fixture_root(
    *, expected_suite: str | None = None
) -> Path:
    """Return and create this module's generated-fixture root."""

    root = fixture_root_from_environment(
        FROZEN_RUN_ENV,
        expected_suite=expected_suite,
    )
    root.mkdir(parents=True, exist_ok=True)
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

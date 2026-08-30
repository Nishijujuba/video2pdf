"""Generated-data boundary shared by Video Workflow tests.

The project runner supplies an immutable external run directory and a short
module identity. Direct ``unittest`` execution intentionally retains the
historical repository-local compatibility root.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid

from scripts.project_test_external_root import (
    ExternalRootError,
    assert_safe_write_path,
)
from scripts.project_test_run_identity import (
    MODULE_KEY_ENV,
    RUN_DIR_ENV,
    SUITE_ID_ENV,
    ActiveWorkerIdentity,
    ProjectTestRunIdentityError,
    freeze_worker_environment,
    resolve_active_worker_identity,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
FROZEN_RUN_ENV = freeze_worker_environment()
_recorded_paths: set[tuple[str, str]] = set()


TestRunBoundaryError = ProjectTestRunIdentityError


def _active_identity() -> ActiveWorkerIdentity | None:
    return resolve_active_worker_identity(
        FROZEN_RUN_ENV,
        project_root=_PROJECT_ROOT,
        expected_suite="video-workflow",
    )


def _contained(run_dir: Path, candidate: Path) -> Path:
    try:
        return assert_safe_write_path(run_dir, candidate)
    except ExternalRootError as error:
        raise TestRunBoundaryError(
            f"generated path escapes active run: {candidate}"
        ) from error


def _record(
    run_dir: Path,
    module_root: Path,
    kind: str,
    path: Path,
    **fields: str,
) -> None:
    key = (kind, str(path))
    if key in _recorded_paths:
        return
    manifest = _contained(run_dir, module_root / "generated-paths.jsonl")
    record = {
        "schema_name": "video2pdf.generated-test-path",
        "schema_version": 1,
        "kind": kind,
        "path": str(path),
        **fields,
    }
    with manifest.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        )
    _recorded_paths.add(key)


def module_test_root(project_root: Path | None = None) -> Path:
    """Return this module's generated-data root."""

    identity = _active_identity()
    if identity is None:
        if project_root is None:
            project_root = Path(__file__).resolve().parents[2]
        return project_root / "待删除" / "kernel-test-runs"
    run_dir = identity.run_dir
    module_key = identity.module_key
    generated = _contained(run_dir, run_dir / "generated")
    generated.mkdir(exist_ok=True)
    module_root = _contained(run_dir, generated / module_key)
    module_root.mkdir(exist_ok=True)
    _record(
        run_dir,
        module_root,
        "module_root",
        module_root,
        suite_id="video-workflow",
        module_key=module_key,
    )
    return module_root


def new_case_dir(test_id: str, *, label: str = "") -> Path:
    """Create a compact, unique directory for one test case."""

    if not test_id:
        raise TestRunBoundaryError("test_id must be non-empty")
    root = module_test_root()
    case_key = hashlib.sha256(test_id.encode("utf-8")).hexdigest()[:10]
    case_dir = root / f"c-{case_key}-{uuid.uuid4().hex[:8]}"
    identity = _active_identity()
    if identity is not None:
        case_dir = _contained(identity.run_dir, case_dir)
    case_dir.mkdir(exist_ok=False)
    if identity is not None:
        _record(
            identity.run_dir,
            root,
            "case_dir",
            case_dir,
            test_id=test_id,
            label=label,
        )
    return case_dir


def new_workflow_workspace(test_id: str, *, label: str) -> Path:
    """Create a compact workspace while retaining semantic manifest identity."""

    case_dir = new_case_dir(test_id, label=label)
    workspace = case_dir / "w"
    identity = _active_identity()
    if identity is not None:
        workspace = _contained(identity.run_dir, workspace)
    workspace.mkdir(exist_ok=False)
    return workspace


def child_environment(test_id: str) -> dict[str, str]:
    """Inherit the current environment and pin child temporary data."""

    environment = os.environ.copy()
    temp_dir = new_case_dir(test_id, label="temp")
    for name in ("TEMP", "TMP", "TMPDIR"):
        environment[name] = str(temp_dir)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def write_committed_cutover_retirement(
    control_root: Path, *, profile: dict[str, object], profile_path: Path
) -> None:
    """Materialize the smallest coherent committed-retirement fixture."""

    retired_at = "2026-08-27T00:00:00Z"
    migration_id = "fixture-profile-activation"
    history = control_root / ".workflow-release-history"
    bundle = history / "cutover-authority-retirement" / migration_id
    (bundle / "original").mkdir(parents=True)
    record = {
        "schema_name": "cutover-authority-retirement-record",
        "schema_version": "1.0.0",
        "migration_id": migration_id,
        "state": "RETIRED",
        "profile": {
            "path": str(profile_path.resolve()),
            "release_id": profile["release_id"],
            "contract_compatibility": profile["contract_compatibility"],
        },
        "inventory": [],
        "dispositions": [],
        "historical_evidence": [],
        "live_control_store": {
            "path": str(control_root / "global-gate-control.sqlite3"),
            "status": "healthy",
            "schema_version": 1,
        },
        "prepared_at": retired_at,
        "retired_at": retired_at,
    }
    (bundle / "retirement-record.json").write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tombstone = {
        "schema_name": "cutover-authority-tombstone",
        "schema_version": "1.0.0",
        "migration_id": migration_id,
        "state": "RETIRED",
        "release_id": profile["release_id"],
        "contract_compatibility": profile["contract_compatibility"],
        "profile_path": str(profile_path.resolve()),
        "audit_bundle_path": str(bundle),
        "capabilities_retired": ["batch", "bilibili", "youtube"],
        "disposition_counts": {},
        "historical_limitation_count": 0,
        "retired_at": retired_at,
    }
    (history / "cutover-authority-tombstone.json").write_text(
        json.dumps(tombstone, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

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
import re
import uuid

from scripts.project_test_external_root import (
    ExternalRootError,
    assert_safe_write_path,
    ensure_project_root,
)


RUN_DIR_ENV = "VIDEO2PDF_PROJECT_TEST_RUN_DIR"
SUITE_ID_ENV = "VIDEO2PDF_PROJECT_TEST_SUITE_ID"
MODULE_KEY_ENV = "VIDEO2PDF_PROJECT_TEST_MODULE_KEY"
_MODULE_KEY = re.compile(r"^[0-9a-f]{12}$")
_RUN_DIRECTORY = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{8}$")
_PROJECT_REMOTE = "https://github.com/Nishijujuba/video2pdf.git"
_REPARSE_ATTRIBUTE = 0x400
_recorded_paths: set[tuple[str, str]] = set()


class TestRunBoundaryError(RuntimeError):
    """The runner-provided generated-data boundary is unsafe or incomplete."""


def _is_reparse(path: Path) -> bool:
    stat_result = os.lstat(path)
    return path.is_symlink() or bool(
        getattr(stat_result, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE
    )


def _assert_ordinary_ancestors(path: Path) -> None:
    current = path
    while True:
        if current.exists() and _is_reparse(current):
            raise TestRunBoundaryError(f"reparse point is forbidden: {current}")
        if current.parent == current:
            return
        current = current.parent


def _active_identity() -> tuple[Path, str] | None:
    values = {
        RUN_DIR_ENV: os.environ.get(RUN_DIR_ENV),
        SUITE_ID_ENV: os.environ.get(SUITE_ID_ENV),
        MODULE_KEY_ENV: os.environ.get(MODULE_KEY_ENV),
    }
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise TestRunBoundaryError(
            "runner test identity environment is incomplete"
        )
    run_text = values[RUN_DIR_ENV]
    suite_id = values[SUITE_ID_ENV]
    module_key = values[MODULE_KEY_ENV]
    assert run_text is not None and suite_id is not None and module_key is not None
    run_dir = Path(run_text)
    if not run_dir.is_absolute() or not run_dir.is_dir():
        raise TestRunBoundaryError(
            "runner test directory must be an existing absolute directory"
        )
    if suite_id != "video-workflow":
        raise TestRunBoundaryError(f"unexpected suite identity: {suite_id}")
    if _MODULE_KEY.fullmatch(module_key) is None:
        raise TestRunBoundaryError(f"invalid module key: {module_key}")
    suite_root = run_dir.parent
    project_root = suite_root.parent
    external_root = project_root.parent
    if project_root.name != "video2pdf" or not (
        project_root / "project.json"
    ).is_file():
        raise TestRunBoundaryError(
            "runner test directory lacks the owned video2pdf project marker"
        )
    if suite_root.name != suite_id:
        raise TestRunBoundaryError(
            "runner test directory suite key does not match suite identity"
        )
    if _RUN_DIRECTORY.fullmatch(run_dir.name) is None:
        raise TestRunBoundaryError(
            "runner test directory must use timestamp_short-run-id identity"
        )
    try:
        owned_project_root = ensure_project_root(
            external_root,
            _PROJECT_REMOTE,
        )
        if os.path.normcase(str(owned_project_root)) != os.path.normcase(
            str(project_root)
        ):
            raise TestRunBoundaryError(
                "runner test directory is outside the owned video2pdf project"
            )
        safe_run_dir = assert_safe_write_path(owned_project_root, run_dir)
    except ExternalRootError as error:
        raise TestRunBoundaryError(
            f"runner test directory ownership is invalid: {error}"
        ) from error
    if not suite_root.is_dir() or not safe_run_dir.is_dir():
        raise TestRunBoundaryError(
            "runner suite and run paths must be existing ordinary directories"
        )
    _assert_ordinary_ancestors(safe_run_dir)
    return safe_run_dir, module_key


def _contained(run_dir: Path, candidate: Path) -> Path:
    absolute = candidate.absolute()
    try:
        absolute.relative_to(run_dir.absolute())
    except ValueError as error:
        raise TestRunBoundaryError(
            f"generated path escapes active run: {candidate}"
        ) from error
    _assert_ordinary_ancestors(absolute.parent)
    return absolute


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
    run_dir, module_key = identity
    generated = _contained(run_dir, run_dir / "generated")
    generated.mkdir(exist_ok=True)
    module_root = _contained(run_dir, generated / module_key)
    module_root.mkdir(exist_ok=True)
    _assert_ordinary_ancestors(module_root)
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
        run_dir, _ = identity
        case_dir = _contained(run_dir, case_dir)
    case_dir.mkdir(exist_ok=False)
    if identity is not None:
        _record(
            identity[0],
            root,
            "case_dir",
            case_dir,
            test_id=test_id,
            label=label,
        )
    return case_dir


def child_environment(test_id: str) -> dict[str, str]:
    """Inherit the current environment and pin child temporary data."""

    environment = os.environ.copy()
    temp_dir = new_case_dir(test_id, label="temp")
    for name in ("TEMP", "TMP", "TMPDIR"):
        environment[name] = str(temp_dir)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment

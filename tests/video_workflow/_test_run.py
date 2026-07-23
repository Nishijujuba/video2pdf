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
import shutil
import subprocess
import uuid

from scripts.project_test_external_root import (
    ExternalRootError,
    assert_safe_write_path,
    validate_owned_run_directory,
)


RUN_DIR_ENV = "VIDEO2PDF_PROJECT_TEST_RUN_DIR"
SUITE_ID_ENV = "VIDEO2PDF_PROJECT_TEST_SUITE_ID"
MODULE_KEY_ENV = "VIDEO2PDF_PROJECT_TEST_MODULE_KEY"
_MODULE_KEY = re.compile(r"^[0-9a-f]{12}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT = {
    "project_key": "video2pdf",
    "repository": "Nishijujuba/video2pdf",
}
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY_PATH = _PROJECT_ROOT / "config" / "test-suites.v1.json"
_GIT_EXECUTABLE = shutil.which("git")
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


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _read_manifest(run_dir: Path, name: str) -> tuple[dict[str, object], bytes]:
    path = run_dir / name
    try:
        safe_path = assert_safe_write_path(run_dir, path)
        if not safe_path.is_file():
            raise TestRunBoundaryError(
                f"runner identity requires an ordinary {name}"
            )
        raw = safe_path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except TestRunBoundaryError:
        raise
    except (ExternalRootError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TestRunBoundaryError(
            f"runner identity has invalid {name}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise TestRunBoundaryError(f"runner identity {name} must be an object")
    return value, raw


def _current_commit() -> str:
    if _GIT_EXECUTABLE is None:
        raise TestRunBoundaryError(
            "runner identity cannot resolve the git executable"
        )
    try:
        completed = subprocess.run(
            [_GIT_EXECUTABLE, "rev-parse", "HEAD"],
            cwd=_PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise TestRunBoundaryError(
            "runner identity cannot inspect the active repository commit"
        ) from error
    commit = completed.stdout.strip()
    if completed.returncode != 0 or _COMMIT.fullmatch(commit) is None:
        raise TestRunBoundaryError(
            "runner identity cannot resolve the active repository commit"
        )
    return commit


def _current_registry_sha256() -> str:
    try:
        return hashlib.sha256(_REGISTRY_PATH.read_bytes()).hexdigest()
    except OSError as error:
        raise TestRunBoundaryError(
            "runner identity cannot fingerprint the active suite registry"
        ) from error


def _validate_manifest_identity(
    run_dir: Path,
    suite_id: str,
    module_key: str,
) -> None:
    test_run, _ = _read_manifest(run_dir, "test-run.json")
    discovery, discovery_raw = _read_manifest(run_dir, "discovery.json")

    if (
        test_run.get("schema_name") != "video2pdf.project-test-run"
        or test_run.get("schema_version") != 1
    ):
        raise TestRunBoundaryError("runner identity test-run.json schema is invalid")
    discovery_sha256 = test_run.get("discovery_sha256")
    if (
        not isinstance(discovery_sha256, str)
        or _SHA256.fullmatch(discovery_sha256) is None
        or hashlib.sha256(discovery_raw).hexdigest() != discovery_sha256
    ):
        raise TestRunBoundaryError(
            "runner discovery.json fingerprint does not match test-run.json"
        )
    if (
        discovery.get("schema_name") != "video2pdf.project-test-discovery"
        or discovery.get("schema_version") != 1
    ):
        raise TestRunBoundaryError("runner identity discovery.json schema is invalid")

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
        raise TestRunBoundaryError(
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
        or commit != _current_commit()
        or not isinstance(registry_sha256, str)
        or _SHA256.fullmatch(registry_sha256) is None
        or discovery.get("registry_sha256") != registry_sha256
        or _current_registry_sha256() != registry_sha256
    ):
        raise TestRunBoundaryError(
            "runner project, repository, commit, or registry identity does not match"
        )
    if (
        not isinstance(selected_suite_ids, list)
        or not selected_suite_ids
        or any(
            not isinstance(selected, str) or not selected
            for selected in selected_suite_ids
        )
        or len(set(selected_suite_ids)) != len(selected_suite_ids)
        or discovery.get("suite_ids") != selected_suite_ids
        or discovery.get("discovery_arguments")
        != {"suite_ids": selected_suite_ids}
        or suite_id not in selected_suite_ids
    ):
        raise TestRunBoundaryError(
            "runner worker suite identity is outside the selected suite set"
        )

    run_suite_key = run_dir.parent.name
    if run_suite_key == "all":
        if len(selected_suite_ids) < 2:
            raise TestRunBoundaryError(
                "runner all-suite path requires multiple selected suites"
            )
    elif run_suite_key != suite_id or selected_suite_ids != [suite_id]:
        raise TestRunBoundaryError(
            "runner single-suite path must match the selected suite identity"
        )

    modules = discovery.get("modules")
    if not isinstance(modules, list):
        raise TestRunBoundaryError("runner discovery module inventory is invalid")
    matches = [
        module
        for module in modules
        if isinstance(module, dict) and module.get("module_key") == module_key
    ]
    if len(matches) != 1 or matches[0].get("suite_id") != suite_id:
        raise TestRunBoundaryError(
            "runner discovery does not bind the active module and suite identity"
        )
    test_ids = matches[0].get("test_ids")
    if (
        not isinstance(test_ids, list)
        or not test_ids
        or any(not isinstance(test_id, str) or not test_id for test_id in test_ids)
        or len(set(test_ids)) != len(test_ids)
    ):
        raise TestRunBoundaryError(
            "runner discovery active module test IDs are invalid"
        )


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
    if suite_id != "video-workflow":
        raise TestRunBoundaryError(f"unexpected suite identity: {suite_id}")
    if _MODULE_KEY.fullmatch(module_key) is None:
        raise TestRunBoundaryError(f"invalid module key: {module_key}")
    try:
        safe_run_dir = validate_owned_run_directory(run_text)
    except ExternalRootError as error:
        raise TestRunBoundaryError(
            f"runner test directory ownership is invalid: {error}"
        ) from error
    _assert_ordinary_ancestors(safe_run_dir)
    _validate_manifest_identity(safe_run_dir, suite_id, module_key)
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

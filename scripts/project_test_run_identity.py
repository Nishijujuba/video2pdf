"""Shared active-worker identity contract for project tests.

Worker modules freeze the three runner environment values once, at module
import, and pass that immutable snapshot to :func:`resolve_active_worker_identity`.
This keeps later process-local environment mutation from changing ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from types import MappingProxyType
from typing import Mapping, Sequence

from scripts.project_test_external_root import (
    ExternalRootError,
    assert_safe_write_path,
    create_unique_run_directory,
    ensure_project_root,
    validate_owned_run_directory,
)
from scripts.project_test_results import (
    ResultIntegrityError,
    canonical_json_bytes,
    write_json_exclusive,
)


RUN_DIR_ENV = "VIDEO2PDF_PROJECT_TEST_RUN_DIR"
SUITE_ID_ENV = "VIDEO2PDF_PROJECT_TEST_SUITE_ID"
MODULE_KEY_ENV = "VIDEO2PDF_PROJECT_TEST_MODULE_KEY"
WORKER_ENV_NAMES = (RUN_DIR_ENV, SUITE_ID_ENV, MODULE_KEY_ENV)

PROJECT_IDENTITY = {
    "project_key": "video2pdf",
    "repository": "Nishijujuba/video2pdf",
}
REGISTRY_RELATIVE_PATH = Path("config/test-suites.v1.json")

_SUITE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_MODULE_KEY = re.compile(r"[0-9a-f]{12}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_EXECUTABLE = shutil.which("git")


class ProjectTestRunIdentityError(RuntimeError):
    """The runner-provided worker identity is absent, unsafe, or inconsistent."""


@dataclass(frozen=True)
class ActiveWorkerIdentity:
    """Validated identity and closed test set assigned to one worker."""

    run_dir: Path
    suite_id: str
    module_key: str
    test_ids: tuple[str, ...]
    selected_suite_ids: tuple[str, ...]


def freeze_worker_environment(
    environment: Mapping[str, str] | None = None,
) -> Mapping[str, str | None]:
    """Capture runner identity values once for the importing worker module."""

    source = os.environ if environment is None else environment
    return MappingProxyType({name: source.get(name) for name in WORKER_ENV_NAMES})


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _read_canonical_manifest(
    run_dir: Path,
    name: str,
) -> tuple[dict[str, object], bytes]:
    try:
        path = assert_safe_write_path(run_dir, run_dir / name)
        if not path.is_file():
            raise ProjectTestRunIdentityError(
                f"runner identity requires an ordinary {name}"
            )
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except ProjectTestRunIdentityError:
        raise
    except (
        ExternalRootError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        raise ProjectTestRunIdentityError(
            f"runner identity has invalid {name}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise ProjectTestRunIdentityError(
            f"runner identity {name} must be an object"
        )
    if raw != canonical_json_bytes(value):
        raise ProjectTestRunIdentityError(
            f"runner identity {name} is not a canonical exclusive artifact"
        )
    return value, raw


def _live_commit(project_root: Path) -> str:
    if _GIT_EXECUTABLE is None:
        raise ProjectTestRunIdentityError(
            "runner identity cannot resolve the git executable"
        )
    try:
        completed = subprocess.run(
            [_GIT_EXECUTABLE, "rev-parse", "HEAD"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise ProjectTestRunIdentityError(
            "runner identity cannot inspect the active repository commit"
        ) from error
    commit = completed.stdout.strip()
    if completed.returncode != 0 or _COMMIT.fullmatch(commit) is None:
        raise ProjectTestRunIdentityError(
            "runner identity cannot resolve the active repository commit"
        )
    return commit


def _live_registry_sha256(project_root: Path) -> str:
    try:
        return hashlib.sha256(
            (project_root / REGISTRY_RELATIVE_PATH).read_bytes()
        ).hexdigest()
    except OSError as error:
        raise ProjectTestRunIdentityError(
            "runner identity cannot fingerprint the active suite registry"
        ) from error


def _validate_selected_suites(
    run_dir: Path,
    suite_id: str,
    test_run: Mapping[str, object],
    discovery: Mapping[str, object],
) -> tuple[str, ...]:
    selected = test_run.get("suite_ids")
    if (
        not isinstance(selected, list)
        or not selected
        or any(
            not isinstance(value, str) or _SUITE_ID.fullmatch(value) is None
            for value in selected
        )
        or len(set(selected)) != len(selected)
        or discovery.get("suite_ids") != selected
        or discovery.get("discovery_arguments") != {"suite_ids": selected}
        or suite_id not in selected
    ):
        raise ProjectTestRunIdentityError(
            "runner worker suite identity is outside the selected suite set"
        )
    suites = discovery.get("suites")
    if not isinstance(suites, list):
        raise ProjectTestRunIdentityError(
            "runner discovery suite identity inventory is invalid"
        )
    suite_keys: dict[str, str] = {}
    for suite in suites:
        if not isinstance(suite, dict):
            raise ProjectTestRunIdentityError(
                "runner discovery suite identity inventory is invalid"
            )
        discovered_id = suite.get("suite_id")
        discovered_key = suite.get("suite_key")
        if (
            not isinstance(discovered_id, str)
            or discovered_id not in selected
            or discovered_id in suite_keys
            or not isinstance(discovered_key, str)
            or _SUITE_ID.fullmatch(discovered_key) is None
        ):
            raise ProjectTestRunIdentityError(
                "runner discovery suite ID or suite key is invalid"
            )
        suite_keys[discovered_id] = discovered_key
    if set(suite_keys) != set(selected) or len(set(suite_keys.values())) != len(
        suite_keys
    ):
        raise ProjectTestRunIdentityError(
            "runner discovery selected suite identities are incomplete"
        )
    run_suite_key = run_dir.parent.name
    if run_suite_key == "all":
        if len(selected) < 2:
            raise ProjectTestRunIdentityError(
                "runner all-suite path requires multiple selected suites"
            )
    elif run_suite_key != suite_keys[suite_id] or selected != [suite_id]:
        raise ProjectTestRunIdentityError(
            "runner single-suite path must match the selected suite identity"
        )
    return tuple(selected)


def _active_module_test_ids(
    discovery: Mapping[str, object],
    suite_id: str,
    module_key: str,
) -> tuple[str, ...]:
    modules = discovery.get("modules")
    if not isinstance(modules, list):
        raise ProjectTestRunIdentityError(
            "runner discovery module inventory is invalid"
        )
    matches = [
        module
        for module in modules
        if isinstance(module, dict) and module.get("module_key") == module_key
    ]
    if len(matches) != 1 or matches[0].get("suite_id") != suite_id:
        raise ProjectTestRunIdentityError(
            "runner discovery does not bind the active module and suite identity"
        )
    test_ids = matches[0].get("test_ids")
    if (
        not isinstance(test_ids, list)
        or not test_ids
        or any(
            not isinstance(test_id, str) or not test_id for test_id in test_ids
        )
        or len(set(test_ids)) != len(test_ids)
    ):
        raise ProjectTestRunIdentityError(
            "runner discovery active module test IDs are invalid"
        )
    return tuple(test_ids)


def _validate_discovery_closed_set(
    discovery: Mapping[str, object],
    selected_suite_ids: Sequence[str],
) -> None:
    modules = discovery.get("modules")
    if not isinstance(modules, list):
        raise ProjectTestRunIdentityError(
            "runner discovery module inventory is invalid"
        )
    module_keys: list[str] = []
    all_test_ids: list[str] = []
    for module in modules:
        if not isinstance(module, dict):
            raise ProjectTestRunIdentityError(
                "runner discovery module inventory is invalid"
            )
        module_key = module.get("module_key")
        suite_id = module.get("suite_id")
        test_ids = module.get("test_ids")
        test_count = module.get("test_count")
        if (
            not isinstance(module_key, str)
            or _MODULE_KEY.fullmatch(module_key) is None
            or not isinstance(suite_id, str)
            or _SUITE_ID.fullmatch(suite_id) is None
            or suite_id not in selected_suite_ids
            or not isinstance(test_ids, list)
            or any(
                not isinstance(test_id, str) or not test_id
                for test_id in test_ids
            )
            or type(test_count) is not int
            or test_count != len(test_ids)
        ):
            raise ProjectTestRunIdentityError(
                "runner discovery module keys, suite IDs, or test IDs are invalid"
            )
        module_keys.append(module_key)
        all_test_ids.extend(test_ids)
    if (
        len(set(module_keys)) != len(module_keys)
        or len(set(all_test_ids)) != len(all_test_ids)
        or discovery.get("duplicate_test_ids") != []
        or discovery.get("total_count") != len(all_test_ids)
        or discovery.get("test_id_set_sha256")
        != hashlib.sha256(
            canonical_json_bytes(sorted(all_test_ids))
        ).hexdigest()
    ):
        raise ProjectTestRunIdentityError(
            "runner discovery module and test-ID closed set is invalid"
        )


def resolve_active_worker_identity(
    environment: Mapping[str, str | None],
    *,
    project_root: Path,
    expected_suite: str | None = None,
) -> ActiveWorkerIdentity | None:
    """Validate one frozen worker snapshot, returning ``None`` for direct runs."""

    if expected_suite is not None and _SUITE_ID.fullmatch(expected_suite) is None:
        raise ProjectTestRunIdentityError(
            f"invalid expected suite: {expected_suite}"
        )
    values = {name: environment.get(name) for name in WORKER_ENV_NAMES}
    if all(value is None for value in values.values()):
        return None
    if any(value is None or value == "" for value in values.values()):
        raise ProjectTestRunIdentityError(
            "runner test identity environment is incomplete"
        )
    run_text = values[RUN_DIR_ENV]
    suite_id = values[SUITE_ID_ENV]
    module_key = values[MODULE_KEY_ENV]
    assert run_text is not None and suite_id is not None and module_key is not None
    if _SUITE_ID.fullmatch(suite_id) is None:
        raise ProjectTestRunIdentityError(
            f"invalid runner suite identity: {suite_id}"
        )
    if expected_suite is not None and suite_id != expected_suite:
        raise ProjectTestRunIdentityError(
            f"runner suite identity {suite_id!r} does not match expected "
            f"suite {expected_suite!r}"
        )
    if _MODULE_KEY.fullmatch(module_key) is None:
        raise ProjectTestRunIdentityError(f"invalid module key: {module_key}")

    try:
        run_dir = validate_owned_run_directory(run_text)
    except ExternalRootError as error:
        raise ProjectTestRunIdentityError(
            f"runner test directory ownership is invalid: {error}"
        ) from error
    test_run, _ = _read_canonical_manifest(run_dir, "test-run.json")
    discovery, discovery_raw = _read_canonical_manifest(
        run_dir, "discovery.json"
    )
    if (
        test_run.get("schema_name") != "video2pdf.project-test-run"
        or test_run.get("schema_version") != 1
    ):
        raise ProjectTestRunIdentityError(
            "runner identity test-run.json schema is invalid"
        )
    if (
        discovery.get("schema_name") != "video2pdf.project-test-discovery"
        or discovery.get("schema_version") != 1
    ):
        raise ProjectTestRunIdentityError(
            "runner identity discovery.json schema is invalid"
        )
    discovery_sha256 = test_run.get("discovery_sha256")
    if (
        not isinstance(discovery_sha256, str)
        or _SHA256.fullmatch(discovery_sha256) is None
        or hashlib.sha256(discovery_raw).hexdigest() != discovery_sha256
    ):
        raise ProjectTestRunIdentityError(
            "runner discovery.json fingerprint does not match test-run.json"
        )

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
        raise ProjectTestRunIdentityError(
            "runner test-run.json run directory identity does not match"
        )

    absolute_project_root = project_root.resolve(strict=True)
    commit = test_run.get("commit")
    registry_sha256 = test_run.get("registry_sha256")
    if (
        test_run.get("project") != PROJECT_IDENTITY
        or discovery.get("project") != PROJECT_IDENTITY
        or not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or discovery.get("commit") != commit
        or commit != _live_commit(absolute_project_root)
        or not isinstance(registry_sha256, str)
        or _SHA256.fullmatch(registry_sha256) is None
        or discovery.get("registry_sha256") != registry_sha256
        or registry_sha256 != _live_registry_sha256(absolute_project_root)
    ):
        raise ProjectTestRunIdentityError(
            "runner project, repository, commit, or registry identity "
            "does not match"
        )

    selected_suite_ids = _validate_selected_suites(
        run_dir, suite_id, test_run, discovery
    )
    _validate_discovery_closed_set(discovery, selected_suite_ids)
    test_ids = _active_module_test_ids(discovery, suite_id, module_key)
    return ActiveWorkerIdentity(
        run_dir=run_dir,
        suite_id=suite_id,
        module_key=module_key,
        test_ids=test_ids,
        selected_suite_ids=selected_suite_ids,
    )


def create_synthetic_project_test_run(
    *,
    external_root: Path,
    project_root: Path,
    suite_id: str,
    module_key: str,
    test_ids: Sequence[str],
    selected_suite_ids: Sequence[str] | None = None,
    run_suite_key: str | None = None,
) -> Path:
    """Create one valid synthetic run baseline through production write APIs."""

    selected = list(selected_suite_ids or (suite_id,))
    if (
        _SUITE_ID.fullmatch(suite_id) is None
        or _MODULE_KEY.fullmatch(module_key) is None
        or not selected
        or any(
            not isinstance(value, str) or _SUITE_ID.fullmatch(value) is None
            for value in selected
        )
        or len(set(selected)) != len(selected)
        or suite_id not in selected
        or not test_ids
        or any(not isinstance(test_id, str) or not test_id for test_id in test_ids)
        or len(set(test_ids)) != len(test_ids)
    ):
        raise ProjectTestRunIdentityError("synthetic worker identity is invalid")
    suite_key = run_suite_key or ("all" if len(selected) > 1 else suite_id)
    if (suite_key == "all") != (len(selected) > 1):
        raise ProjectTestRunIdentityError(
            "synthetic run suite key does not match selected suites"
        )
    owned_project_root = ensure_project_root(
        external_root,
        "https://github.com/Nishijujuba/video2pdf.git",
    )
    run_dir = create_unique_run_directory(
        owned_project_root,
        suite_key,
        registered_suite_keys={suite_key},
    )
    absolute_project_root = project_root.resolve(strict=True)
    commit = _live_commit(absolute_project_root)
    registry_sha256 = _live_registry_sha256(absolute_project_root)
    ordered_ids = list(test_ids)
    discovery = {
        "schema_name": "video2pdf.project-test-discovery",
        "schema_version": 1,
        "project": PROJECT_IDENTITY,
        "commit": commit,
        "registry_path": REGISTRY_RELATIVE_PATH.as_posix(),
        "registry_sha256": registry_sha256,
        "discovery_arguments": {"suite_ids": selected},
        "suite_ids": selected,
        "suites": [
            {
                "suite_id": value,
                "suite_key": (
                    suite_key
                    if len(selected) == 1 and value == suite_id
                    else value
                ),
                "roots": [],
            }
            for value in selected
        ],
        "modules": [
            {
                "suite_id": suite_id,
                "root_path": "synthetic",
                "source_path": "synthetic.py",
                "module_key": module_key,
                "test_count": len(ordered_ids),
                "test_ids": ordered_ids,
            }
        ],
        "duplicate_test_ids": [],
        "total_count": len(ordered_ids),
        "test_id_set_sha256": hashlib.sha256(
            canonical_json_bytes(sorted(ordered_ids))
        ).hexdigest(),
    }
    try:
        discovery_sha256 = write_json_exclusive(
            run_dir / "discovery.json", discovery
        )
        write_json_exclusive(
            run_dir / "test-run.json",
            {
                "schema_name": "video2pdf.project-test-run",
                "schema_version": 1,
                "command": "run",
                "project": PROJECT_IDENTITY,
                "commit": commit,
                "registry_sha256": registry_sha256,
                "discovery_sha256": discovery_sha256,
                "suite_ids": selected,
                "run_dir": str(run_dir),
                "requested_jobs": 1,
                "timings_from": None,
                "runner_pid": os.getpid(),
                "discovery_process": {"pid": os.getpid(), "exit_code": 0},
            },
        )
    except ResultIntegrityError as error:
        raise ProjectTestRunIdentityError(
            f"cannot create synthetic run artifacts: {error}"
        ) from error
    return run_dir
